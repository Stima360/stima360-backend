"""DELETE-ARCH Fase 0 — cancellazione fisica delle stime, limitata.

Contratto REV 2, §18.2 (C3): nessun archivio stime in questo progetto; l'hard
delete legacy (`POST /api/admin/stime/delete`, `/stime_dettagliate/delete`)
resta ma diventa

  * riservato al titolare dell'agenzia (`agency_owner`; NON il platform
    admin in acting);
  * limitato alla propria agenzia;
  * eleggibile per ogni stima solo se NESSUNA riga di storico CRM la
    referenzia (la cascata DB su `activities`/`tasks`/`lead_stime`/owner_*
    resta fino a M1 ma non puo' scattare: la si intercetta prima);
  * tutto-o-niente: se una sola stima selezionata non e' eleggibile, nessuna
    viene cancellata e la risposta e' `409 NOT_PURGEABLE` con i blocchi per
    stima;
  * una transazione con rollback esplicito.

La lista dei riferimenti e' stata verificata sul catalogo (`pg_constraint`,
`confrelid = 'stime'`): 15 FK reali, di cui `stime_dettagliate` e' contenuto
proprio della stima (viene cancellato con lei, nella stessa transazione e
nella stessa agenzia). A questi si aggiunge `appointments.stima_id`, che e'
un riferimento morbido senza FK (A30-1, test_03): un appuntamento e' un
evento protetto e blocca comunque. A runtime il catalogo viene riletto e
ogni FK verso `stime` non presente in questa lista viene contata lo stesso:
una migration futura che aggiunga un riferimento non apre un buco.

Nessuna colonna nuova, nessuna migration, nessun `stime.archived_at`.
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("stima360.lifecycle")

CODE = "NOT_PURGEABLE"
FORBIDDEN_MESSAGE = "Solo il titolare dell'agenzia può cancellare le stime"
NOT_PURGEABLE_MESSAGE = "Una o più stime non sono eliminabili: nessuna stima è stata cancellata"

#: (tabella, colonna, etichetta) — i riferimenti che rendono una stima NON
#: eleggibile. Verificati sul catalogo del DB locale (schema completo, 083).
STIME_REFERENCES = (
    ("activities", "stima_id", "attività"),
    ("tasks", "stima_id", "task"),
    ("lead_stime", "stima_id", "collegamenti lead-stima"),
    ("owner_stima_access", "stima_id", "accessi proprietario"),
    ("owner_home_overrides", "stima_id", "override home proprietario"),
    ("owner_home_notifications", "stima_id", "notifiche home proprietario"),
    ("communication_messages", "stima_id", "messaggi"),
    ("communication_enrollments", "stima_id", "iscrizioni comunicazione"),
    ("followup_actions", "stima_id", "azioni di follow-up"),
    ("next_best_actions", "stima_id", "next best action"),
    ("property_watches", "stima_id", "osservazioni immobile"),
    ("seller_timeline_events", "stima_id", "eventi timeline venditore"),
    ("stima_acquisitions", "stima_id", "ponte acquisizione"),
    ("stima_inspections", "stima_id", "sopralluoghi"),
    ("appointments", "stima_id", "appuntamenti"),  # riferimento morbido, senza FK
)

#: Contenuto proprio della stima: va via con lei, non la blocca.
OWN_CONTENT = frozenset({"stime_dettagliate", "stima_pdf_artifacts", "public_submission_receipts"})

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class StimePurgeForbidden(Exception):
    """Ruolo insufficiente: 403."""


class StimeNotPurgeable(Exception):
    """Almeno una stima non e' eleggibile: 409 NOT_PURGEABLE, nessuna cancellazione."""

    code = CODE

    def __init__(self, blockers):
        super().__init__(NOT_PURGEABLE_MESSAGE)
        self.blockers = blockers


def may_purge(ctx) -> bool:
    """SOLO il titolare dell'agenzia (`agency_owner`).

    DELETE-ARCH Fase 0, review: un platform admin in acting NON cancella
    fisicamente le stime. L'acting consente di operare nel tenant, non
    conferisce il privilegio distruttivo massimo sui dati legacy; se un
    giorno servira', sara' una capability separata, esplicita e auditata.
    (In acting il contesto ha `role=None`, vedi
    `operator_auth.service._effective_agency`; il controllo su
    `is_platform_admin` chiude anche un contesto costruito diversamente.)"""
    return getattr(ctx, "role", None) == "agency_owner" and not getattr(ctx, "is_platform_admin", False)


def require_purge_role(ctx):
    if not may_purge(ctx):
        raise StimePurgeForbidden(FORBIDDEN_MESSAGE)


def _catalog_references(cur):
    """Le FK reali verso `stime` lette dal catalogo, come (tabella, colonna).
    Fail-safe: se la lettura non e' possibile (double di test, catalogo
    assente) si resta sulla lista statica."""
    try:
        cur.execute(
            "SELECT c.conrelid::regclass::text, a.attname "
            "  FROM pg_constraint c "
            "  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
            " WHERE c.contype = 'f' AND c.confrelid = 'stime'::regclass")
        righe = cur.fetchall() or []
    except Exception:  # pragma: no cover - solo senza catalogo
        return []
    trovate = []
    for riga in righe:
        try:
            tabella, colonna = str(riga[0]), str(riga[1])
        except (TypeError, IndexError, KeyError):
            continue
        if _IDENT.match(tabella) and _IDENT.match(colonna):
            trovate.append((tabella, colonna))
    return trovate


def references(cur):
    """Lista statica + FK del catalogo non ancora elencate (esclusi i
    contenuti propri). L'ordine e' quello della lista statica, poi le nuove."""
    elenco = list(STIME_REFERENCES)
    note = {(t, c) for t, c, _ in elenco}
    for tabella, colonna in _catalog_references(cur):
        if tabella in OWN_CONTENT or (tabella, colonna) in note:
            continue
        elenco.append((tabella, colonna, tabella))
        note.add((tabella, colonna))
    return elenco


def blockers_for(cur, agency_id, ids):
    """Per ogni id richiesto: i blocchi. `{}` quando tutte sono eleggibili.

    Una stima inesistente o di un'altra agenzia e' un blocco (`not_found`):
    con la regola tutto-o-niente non puo' essere ignorata in silenzio, come
    faceva la rotta precedente."""
    ids = [int(i) for i in ids]
    cur.execute("SELECT id FROM stime WHERE id = ANY(%s) AND agency_id = %s", (ids, agency_id))
    presenti = {int(r[0]) for r in (cur.fetchall() or [])}
    blocchi = {}
    for sid in ids:
        if sid not in presenti:
            blocchi.setdefault(sid, []).append(
                {"table": "stime", "code": "not_found", "count": 0,
                 "label": "stima inesistente o di un'altra agenzia"})
    eleggibili = [sid for sid in ids if sid in presenti]
    if not eleggibili:
        return blocchi
    for tabella, colonna, etichetta in references(cur):
        cur.execute(
            f"SELECT {colonna}, COUNT(*) FROM {tabella} WHERE {colonna} = ANY(%s) GROUP BY {colonna}",
            (eleggibili,))
        for riga in cur.fetchall() or []:
            try:
                sid, n = int(riga[0]), int(riga[1])
            except (TypeError, ValueError, IndexError):
                continue
            if sid in presenti and n > 0:
                blocchi.setdefault(sid, []).append(
                    {"table": tabella, "code": "referenced", "count": n, "label": etichetta})
    return blocchi


try:  # pragma: no cover - psycopg2 e' sempre presente in esecuzione
    from psycopg2 import errors as _pg_errors
    _LOCK_ERRORS = (_pg_errors.LockNotAvailable, _pg_errors.DeadlockDetected)
except Exception:  # pragma: no cover
    _LOCK_ERRORS = ()

#: Quanto la cancellazione aspetta i lock prima di rinunciare con un 409.
LOCK_TIMEOUT = "5s"


def lock_for_purge(cur, agency_id, ids):
    """DELETE-ARCH Fase 0, review 2: chiude la finestra fra il controllo dei
    riferimenti e la DELETE. Due lock, nella stessa transazione, PRIMA del
    controllo e tenuti fino al commit:

    1. `stime ... FOR UPDATE` sulle righe richieste (ordinate per id). Ogni
       INSERT/UPDATE che scrive una FK verso `stime` prende `FOR KEY SHARE`
       sulla riga referenziata, che confligge con `FOR UPDATE`: una scrittura
       in volo viene attesa (e poi vista dal conteggio, che in READ COMMITTED
       legge dopo il lock), una nuova resta in attesa fino al commit e poi
       fallisce sulla FK perche' la riga non c'e' piu'. Copre le 15 FK del
       catalogo, CASCADE e SET NULL compresi.
    2. `LOCK TABLE appointments IN SHARE MODE`: `appointments.stima_id` NON ha
       FK (A30-1), quindi il lock di riga non lo protegge. SHARE confligge con
       ROW EXCLUSIVE (INSERT/UPDATE/DELETE): nessun appuntamento si scrive fra
       controllo e DELETE, quelli in volo vengono attesi e contati. Due purge
       concorrenti prendono SHARE insieme (compatibile) e si serializzano sulle
       righe `stime`. Lock tenuto per una transazione breve, solo su azione
       del titolare.

    `lock_timeout` limita l'attesa: un lock non ottenuto e' un 409, mai un 500
    e mai una cancellazione parziale. Nessuna migration."""
    cur.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    cur.execute("SELECT id FROM stime WHERE id = ANY(%s) AND agency_id = %s ORDER BY id FOR UPDATE",
                (list(ids), agency_id))
    cur.fetchall()
    cur.execute("SELECT to_regclass('public.appointments') IS NOT NULL")
    riga = cur.fetchone()
    if riga and riga[0]:
        cur.execute("LOCK TABLE appointments IN SHARE MODE")


def purge_stime(conn, ctx, agency_id, ids):
    """Cancella le stime eleggibili (con le loro `stime_dettagliate`), oppure
    nessuna. Ritorna il numero di stime cancellate. La connessione viene
    sempre chiusa dal chiamante; qui si fa commit o rollback."""
    require_purge_role(ctx)
    ids = sorted({int(i) for i in ids})
    cur = conn.cursor()
    try:
        try:
            lock_for_purge(cur, agency_id, ids)
        except _LOCK_ERRORS:
            conn.rollback()
            _audit("stime_purge_refused", ctx, agency_id=agency_id, ids=ids, reason="lock_busy")
            raise StimeNotPurgeable([{"stima_id": sid, "blockers": [
                {"table": "stime", "code": "busy", "count": 0,
                 "label": "la stima è in uso da un'altra operazione: riprova"}]} for sid in ids])
        blocchi = blockers_for(cur, agency_id, ids)
        if blocchi:
            conn.rollback()
            _audit("stime_purge_refused", ctx, agency_id=agency_id, ids=ids,
                   blockers={str(k): v for k, v in blocchi.items()})
            raise StimeNotPurgeable([{"stima_id": sid, "blockers": blocchi[sid]} for sid in ids if sid in blocchi])
        cur.execute("DELETE FROM stime_dettagliate WHERE stima_id = ANY(%s) AND agency_id = %s", (ids, agency_id))
        dettagli = cur.rowcount
        cur.execute("DELETE FROM stime WHERE id = ANY(%s) AND agency_id = %s", (ids, agency_id))
        cancellate = cur.rowcount
        if cancellate != len(ids):
            # Fra il controllo e la cancellazione qualcosa e' cambiato: niente
            # a meta'.
            conn.rollback()
            raise StimeNotPurgeable([{"stima_id": sid, "blockers": [
                {"table": "stime", "code": "changed", "count": 0,
                 "label": "la stima è cambiata durante la cancellazione"}]} for sid in ids])
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass
    _audit("stime_purged", ctx, agency_id=agency_id, ids=ids, deleted=cancellate, details_deleted=dettagli)
    return cancellate


def purge_stime_dettagliate(conn, ctx, agency_id, ids):
    """Solo le righe di dettaglio, per id proprio, della propria agenzia.
    Stesso ruolo e stessa regola tutto-o-niente: un id inesistente o di
    un'altra agenzia blocca l'intera richiesta."""
    require_purge_role(ctx)
    ids = sorted({int(i) for i in ids})
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM stime_dettagliate WHERE id = ANY(%s) AND agency_id = %s", (ids, agency_id))
        presenti = {int(r[0]) for r in (cur.fetchall() or [])}
        mancanti = [sid for sid in ids if sid not in presenti]
        if mancanti:
            conn.rollback()
            _audit("stime_dettagliate_purge_refused", ctx, agency_id=agency_id, ids=ids, missing=mancanti)
            raise StimeNotPurgeable([{"stima_dettagliata_id": sid, "blockers": [
                {"table": "stime_dettagliate", "code": "not_found", "count": 0,
                 "label": "riga inesistente o di un'altra agenzia"}]} for sid in mancanti])
        cur.execute("DELETE FROM stime_dettagliate WHERE id = ANY(%s) AND agency_id = %s", (ids, agency_id))
        cancellate = cur.rowcount
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass
    _audit("stime_dettagliate_purged", ctx, agency_id=agency_id, ids=ids, deleted=cancellate)
    return cancellate


def _audit(azione, ctx, **campi):
    """Audit minimo Fase 0: log applicativo strutturato. Non persistente
    (il registro `record_lifecycle_events` arriva in Fase 1/M1)."""
    voce = {"action": azione, "user_id": getattr(ctx, "user_id", None),
            "role": getattr(ctx, "role", None), "session_id": getattr(ctx, "session_id", None)}
    voce.update(campi)
    try:
        log.info("lifecycle %s", json.dumps(voce, default=str, sort_keys=True))
    except Exception:  # pragma: no cover
        log.info("lifecycle %r", voce)
