"""V3 - ORDINE DEI LOCK fra la chiusura di un invio e il tick, su PostgreSQL.

Due giri di cron sovrapposti potevano andare in DEADLOCK: la chiusura di un
`sent` aggiornava la riga del messaggio e solo DOPO, inserendo l'evento di
Seller Intelligence, chiedeva il lock di chiave esterna sul contatto; il tick
blocca prima il contatto (fence) e poi tocca il messaggio. PostgreSQL annullava
la chiusura: email partita davvero, registrata come esito incerto.

L'ordine del codice e' "prima il contatto": la chiusura prende per primo lo
stesso lock (FOR KEY SHARE) che la chiave esterna chiederebbe comunque.

Deterministico, senza attese a tempo: B tiene il contatto; appena PostgreSQL
mostra la chiusura in attesa di un lock, B prova la riga del messaggio con
NOWAIT. Se la ottiene, la chiusura si e' fermata PRIMA del messaggio.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo  # noqa: F401
from tests.test_delete_arch_0_postgres import mondo, operatori  # noqa: F401
from tests.test_fase_b_followup_postgres import (  # noqa: F401
    GIORNO, _ctx, _iscrizioni, _messaggi, fb)
from tests.test_v3_followup_postgres import T0, _attiva, _proprietario, _tick, v3  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def test_01_la_chiusura_di_un_invio_blocca_il_contatto_prima_del_messaggio(v3):
    import psycopg2
    from communication import dispatcher
    from communication.providers import null

    m = v3
    _attiva(m)
    cid = _proprietario(m, "ordine.lock@example.it")
    _tick(m, T0)
    _tick(m, T0 + 4 * GIORNO)
    e = _iscrizioni(m, contact_id=cid)[0]
    (prima,) = [x for x in _messaggi(m, e["id"]) if x["status"] == "queued"]

    contatto_bloccato = threading.Event()
    esito = {}

    def giro_concorrente():
        b = psycopg2.connect(m["dsn"])
        monitor = psycopg2.connect(m["dsn"])
        monitor.autocommit = True
        try:
            with b.cursor() as cur:
                cur.execute("SELECT id FROM contacts WHERE id = %s FOR UPDATE", (cid,))
                contatto_bloccato.set()
                with monitor.cursor() as mon:
                    for _ in range(4000):   # attende lo STATO "in attesa", non un tempo
                        mon.execute("SELECT count(*) FROM pg_locks WHERE NOT granted")
                        if mon.fetchone()[0] > 0:
                            break
                    else:
                        esito["messaggio"] = "nessuna_attesa"
                        return
                try:
                    cur.execute("SELECT id FROM communication_messages WHERE id = %s FOR UPDATE NOWAIT",
                                (prima["id"],))
                    esito["messaggio"] = "libero"
                except psycopg2.errors.LockNotAvailable:
                    esito["messaggio"] = "gia_bloccato_dalla_chiusura"
        finally:
            b.rollback()
            b.close()
            monitor.close()

    filo = threading.Thread(target=giro_concorrente)

    def send(message):
        filo.start()
        assert contatto_bloccato.wait(30)
        return null.send(message)

    provider = SimpleNamespace(NAME=null.NAME, CAPABILITIES=null.CAPABILITIES, send=send)
    conteggi = dispatcher.dispatch_batch(_ctx(m, "owner_a"), channel="email", provider=provider, limit=50)
    filo.join(60)

    assert esito.get("messaggio") == "libero", esito
    assert conteggi["sent"] == 1 and conteggi["lost"] == 0, conteggi
    assert _q(m, "SELECT status FROM communication_messages WHERE id = %s", (prima["id"],))[0][0] == "sent"
