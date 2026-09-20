"""Database helpers dedicated to the additive Property Watch module."""

from __future__ import annotations

from contextlib import contextmanager

from psycopg2.extras import RealDictCursor

from database import get_connection


@contextmanager
def property_watch_cursor(*, commit: bool = False):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield conn, cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# LMC-11 - IL LOCK DEL GIRO, E PERCHE' NON E' QUELLO CHE USA IL RESTO DEL REPO
#
# Ogni advisory lock gia' presente (core.repository.bridge_public_stima,
# flow.repository, database_revival, owner.repository) e'
# `pg_advisory_xact_lock`: si prende all'inizio di UNA transazione e il
# database lo rilascia da solo al COMMIT o al ROLLBACK. E' la forma giusta
# quando cio' che va serializzato sta dentro una transazione sola.
#
# Il giro del cron non sta in una transazione sola: apre e chiude una
# connessione per ogni lettura e per ogni scrittura, per agenzia e per watch.
# Un lock transazionale cadrebbe alla prima commit, cioe' dopo il primo watch,
# e da quel momento una seconda esecuzione entrerebbe indisturbata - un lock
# che esiste ma non protegge niente e' peggio di nessun lock, perche' qualcuno
# lo legge e si tranquillizza.
#
# Serve quindi un lock di SESSIONE, tenuto da una connessione dedicata che
# resta aperta per tutta la durata del giro. Due conseguenze, entrambe volute:
#
#   - `pg_try_advisory_lock` e non `pg_advisory_lock`: se un altro giro e' in
#     corso si esce subito con un esito dichiarato, non si sta in coda. Un
#     cron che aspetta il precedente e' un cron che accumula processi.
#   - il rilascio e' nel `finally`, e comunque la chiusura della connessione lo
#     rilascerebbe: due reti, perche' un lock di sessione dimenticato
#     resterebbe finche' il processo non muore.
#
# La chiave si deriva come ovunque nel repository - `hashtextextended(<scope>,0)`
# calcolato da PostgreSQL - e non con `hash()` di Python, che e' randomizzato
# per processo da PYTHONHASHSEED: due worker calcolerebbero chiavi diverse per
# lo stesso scope e il lock non serializzerebbe niente.
# ---------------------------------------------------------------------------

#: Lo scope del lock. Uno per l'intero giro, non per agenzia: il cron e' unico
#: e gira su tutti i tenant, quindi cio' che va impedito e' la seconda
#: esecuzione del cron, non la concorrenza fra agenzie.
VALUATION_CRON_LOCK_SCOPE = "property_watch:valuation_cron"


@contextmanager
def advisory_job_lock(scope: str):
    """Tiene un advisory lock di sessione per tutta la durata del blocco.

    Restituisce `True` se il lock e' stato ottenuto, `False` se un altro giro
    lo tiene gia'. Nel secondo caso il chiamante deve uscire: non e' un
    errore, e' l'altra esecuzione che sta lavorando.
    """
    conn = get_connection()
    ottenuto = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (scope,))
            ottenuto = bool(cur.fetchone()[0])
        # La connessione del lock non scrive niente: la si chiude in lettura
        # per non lasciare una transazione aperta per tutta la durata del giro.
        conn.rollback()
        yield ottenuto
    finally:
        if ottenuto:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (scope,))
                conn.rollback()
            except Exception:  # noqa: BLE001 - la chiusura sotto rilascia comunque
                pass
        conn.close()
