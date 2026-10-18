"""ADM-3 BIS - continuita' centrale dei follow-up email delle agenzie SOSPESE.

PostgreSQL usa-e-getta (harness P29-3C, importato) con la 079 in coda, cosi'
il ledger ammette anche i promemoria degli appuntamenti. Nessuna email parte:
l'adapter e' quello vero (`email_smtp`) e `invia_mail` e' una spia.

  01  agenzia ATTIVA: il giro centrale non la tocca, i suoi passi nascono
      senza segno e con la sua firma, il dispatch normale resta quello
  02  agenzia SOSPESA: un solo giro centrale iscrive, compone e spedisce M1
      e spedisce l'email della stima ancora in coda
  03  mittente e firma STIMA360 (segno nei metadata, firma nel testo)
  04  nessun promemoria, nessun messaggio dell'operatore per l'agenzia sospesa
  05  consenso revocato dopo la composizione: soppresso, nessun invio
  06  giri ripetuti: nessun duplicato
  07  nessuna fuga fra agenzie: l'agenzia attiva accanto resta intatta
  08  i messaggi accodati PRIMA della sospensione restano `queued`, intatti
  09  alla riattivazione nessun invio massivo: il giro centrale smette, il
      dispatch normale riprende a lotti
  10  nessun accesso: sessioni e login dell'agenzia sospesa non cambiano
  11  il cron: `run_central_continuity` lavora le sospese anche con
      `--agency-id 1`, e un guasto non diventa un successo
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_p29_3c_orchestrator_postgres import (  # noqa: F401
    CATENA, DSN, SCHEMA_MINIMO, _dsn_per, _migrazione, iscrizioni, journey_attiva,
    modulo, mondo, passo,
)

pytestmark = pytest.mark.skipif(not os.getenv("P29_TEST_DSN"),
                                reason="P29_TEST_DSN non impostata")

VERSIONE_079 = "079_a32_1_appointment_reminders"
PASSI = [passo(1, delay=0)]


@pytest.fixture(scope="module")
def db():
    """Lo stesso database di P29-3C, piu' la 079 (CHECK del motivo)."""
    psycopg2 = pytest.importorskip("psycopg2")
    from psycopg2.extras import DictCursor

    nome = f"adm3bis_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')
    dsn = _dsn_per(nome)
    conn = psycopg2.connect(dsn, cursor_factory=DictCursor)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_MINIMO)
            for versione in (*CATENA, VERSIONE_079):
                cur.execute(_migrazione(versione))
        yield {"conn": conn, "dsn": dsn}
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


@pytest.fixture
def spia(monkeypatch):
    """Le email che SAREBBERO partite: (destinatario, oggetto, corpo)."""
    from communication.providers import email_smtp

    inviate: list = []
    monkeypatch.setattr(email_smtp, "invia_mail",
                        lambda *a, **k: (inviate.append(a), True)[1])
    return inviate


def _stato(mondo, agenzia, stato):
    mondo["sql"]("UPDATE agencies SET status = %s WHERE id = %s", (stato, agenzia))


def _centrale():
    from communication import central_continuity
    from communication.providers import email_smtp
    return central_continuity.run_for_suspended_agencies(provider=email_smtp)


def _normale(mondo, agenzia):
    """Il dispatch di sempre di un'agenzia attiva (quello del cron di dispatch)."""
    from communication import dispatcher
    from communication.providers import email_smtp
    return dispatcher.dispatch_batch(mondo["ctx"](agenzia=agenzia), channel="email",
                                     provider=email_smtp)


def _riga(mondo, message_id):
    return mondo["righe"]("communication_messages", "id = %s", (message_id,))[0]


def _passi(mondo, agenzia):
    return mondo["righe"]("communication_messages",
                          "agency_id = %s AND enrollment_id IS NOT NULL", (agenzia,))


def _accoda(mondo, c, *, motivo, tipo="service", metadata=None):
    """Un messaggio in coda, dovuto adesso, accanto al contatto `c`."""
    import json
    return mondo["sql"](
        """INSERT INTO communication_messages
               (agency_id, contact_id, lead_id, stima_id, channel, direction,
                communication_type, mode, reason_code, subject_snapshot, rendered_body,
                destination_snapshot, status, scheduled_at, actor_type, idempotency_key, metadata)
           VALUES (%s,%s,%s,%s,'email','outbound',%s,'automatic',%s,'Oggetto','corpo',
                   %s,'queued',NOW() - INTERVAL '1 minute','system',%s,%s::jsonb) RETURNING id""",
        (c["agency"], c["contact"], c["lead"], c["stima"], tipo, motivo,
         _email(mondo, c), f"adm3bis:{motivo}:{uuid.uuid4().hex}",
         json.dumps(metadata if metadata is not None else {})))[0][0]


def _email(mondo, c):
    return mondo["sql"]("SELECT email FROM contacts WHERE id = %s", (c["contact"],))[0][0]


def _stima_in_coda(mondo, c):
    return _accoda(mondo, c, motivo="stima_pdf",
                   metadata={"pdf_url": f"https://test.stima360.it/pdf/{c['stima']}.pdf"})


# ===========================================================================

def test_01_agenzia_attiva_invariata(mondo, modulo, spia):
    a = mondo["a"]
    journey_attiva(modulo, mondo["ctx"](), passi=PASSI)
    c = mondo["contatto"]()
    stima = _stima_in_coda(mondo, c)

    esito = _centrale()
    assert esito == {"agencies": 0, "runs": [], "failed": []}
    assert spia == [] and iscrizioni(mondo) == [] and _riga(mondo, stima)["status"] == "queued"

    modulo["tick"].tick(mondo["ctx"]())
    m1 = _passi(mondo, a)[0]
    assert m1["metadata"] in (None, {}), "un passo di un'agenzia attiva porta il segno centrale"
    assert m1["subject_snapshot"] == "[prova] A"

    conteggi = _normale(mondo, a)
    assert conteggi["sent"] == 2 and len(spia) == 2
    assert {_riga(mondo, m1["id"])["status"], _riga(mondo, stima)["status"]} == {"sent"}


def test_02_03_sospesa_continua_dal_centro_con_firma_stima360(mondo, modulo, spia):
    a = mondo["a"]
    journey_attiva(modulo, mondo["ctx"](), passi=PASSI)
    _stato(mondo, a, "suspended")
    c = mondo["contatto"]()
    stima = _stima_in_coda(mondo, c)

    esito = _centrale()

    assert esito["agencies"] == 1 and esito["failed"] == []
    assert esito["runs"][0]["agency_id"] == a
    assert len(iscrizioni(mondo)) == 1 and iscrizioni(mondo)[0]["agency_id"] == a
    m1 = _passi(mondo, a)
    assert len(m1) == 1 and m1[0]["status"] == "sent", "M1 non e' partita nello stesso giro"
    assert _riga(mondo, stima)["status"] == "sent"
    assert len(spia) == 2
    # 03 - firma e segno
    assert m1[0]["metadata"] == {"signature": "stima360_central"}
    assert m1[0]["subject_snapshot"] == "[prova] STIMA360"
    oggetti = {o for (_dest, o, _corpo) in spia}
    assert "[prova] STIMA360" in oggetti and "[prova] A" not in oggetti
    # il lead resta dell'agenzia: nessun dato cambia proprietario
    assert mondo["sql"]("SELECT agency_id FROM contacts WHERE id = %s", (c["contact"],))[0][0] == a
    assert mondo["sql"]("SELECT agency_id FROM leads WHERE id = %s", (c["lead"],))[0][0] == a


def test_03b_firma_centrale_nella_chiusura_dei_template_reali(mondo, modulo):
    """La chiusura dei template della sequenza `stima_lead` firma STIMA360."""
    from communication import journey_tick, templates
    anagrafica = {"agency_status": "suspended", "agency_name": "Agenzia Rossi",
                  "contact_first_name": "Lia"}
    assert journey_tick._firma_centrale(anagrafica)
    chiusura = templates._chiusura({"agency_name": journey_tick.CENTRAL_SIGNATURE_NAME,
                                    templates.UNSUBSCRIBE_FIELD: "https://x/u"})
    assert chiusura.startswith("\nSTIMA360\nsu Stima360\n") and "Rossi" not in chiusura
    assert not journey_tick._firma_centrale({**anagrafica, "agency_status": "active"})


def test_04_nessun_promemoria_ne_messaggio_dell_operatore(mondo, modulo, spia):
    a = mondo["a"]
    c = mondo["contatto"]()
    promemoria = _accoda(mondo, c, motivo="appointment_reminder")
    manuale = _accoda(mondo, c, motivo="operator_manual")
    portale = _accoda(mondo, c, motivo="owner_login_link")
    # Il segno centrale lo scrive solo il motore delle sequenze: qui nessuno
    # di questi messaggi lo porta, e nessuno parte.
    _stato(mondo, a, "suspended")

    _centrale()

    assert spia == []
    for mid in (promemoria, manuale, portale):
        assert _riga(mondo, mid)["status"] == "queued"


def test_05_consenso_revocato_soppresso(mondo, modulo, spia):
    a = mondo["a"]
    journey_attiva(modulo, mondo["ctx"](), passi=PASSI)
    _stato(mondo, a, "suspended")
    c = mondo["contatto"]()
    modulo["tick"].tick(mondo["ctx"]())  # compone M1 con il segno, non spedisce
    m1 = _passi(mondo, a)[0]
    assert m1["status"] == "queued" and m1["metadata"] == {"signature": "stima360_central"}
    mondo["sql"]("INSERT INTO consent_events (agency_id, contact_id, purpose, decision, decided_at, "
                 "source, actor_type) VALUES (%s,%s,'marketing','revoked',NOW(),'crm','subject')",
                 (a, c["contact"]))
    mondo["sql"]("UPDATE contacts SET marketing_consent = FALSE WHERE id = %s", (c["contact"],))

    # e un messaggio col segno, fuori sequenza, che arriva fino al dispatcher
    fuori = _accoda(mondo, c, motivo="m2", tipo="marketing",
                    metadata={"signature": "stima360_central"})

    _centrale()

    assert spia == []
    # il motore ferma l'iscrizione per primo e annulla M1; cio' che arriva al
    # dispatcher passa dal cancello del consenso di sempre
    assert _riga(mondo, m1["id"])["status"] == "cancelled"
    assert _riga(mondo, fuori)["status"] == "suppressed"


def test_05b_senza_consenso_nessuna_sequenza(mondo, modulo, spia):
    a = mondo["a"]
    journey_attiva(modulo, mondo["ctx"](), passi=PASSI)
    _stato(mondo, a, "suspended")
    mondo["contatto"](consenso=False)

    _centrale()

    assert spia == [] and _passi(mondo, a) == []


def test_06_giri_ripetuti_nessun_duplicato(mondo, modulo, spia):
    a = mondo["a"]
    journey_attiva(modulo, mondo["ctx"](), passi=PASSI)
    _stato(mondo, a, "suspended")
    c = mondo["contatto"]()
    _stima_in_coda(mondo, c)

    for _ in range(4):
        _centrale()

    assert len(spia) == 2
    assert len(_passi(mondo, a)) == 1 and len(iscrizioni(mondo)) == 1


def test_07_nessuna_fuga_fra_agenzie(mondo, modulo, spia):
    a, b = mondo["a"], mondo["b"]
    journey_attiva(modulo, mondo["ctx"](), passi=PASSI)
    journey_attiva(modulo, mondo["ctx"]("admin_b", agenzia=b), passi=PASSI)
    cb = mondo["contatto"](b)
    modulo["tick"].tick(mondo["ctx"]("admin_b", agenzia=b))  # M1 di B, in coda
    stima_b = _stima_in_coda(mondo, cb)
    # perfino un messaggio di B col segno centrale non e' affare del giro
    # centrale finche' B e' attiva: si lavorano solo le agenzie sospese.
    segnato_b = _accoda(mondo, cb, motivo="m2", tipo="marketing",
                        metadata={"signature": "stima360_central"})
    _stato(mondo, a, "suspended")
    ca = mondo["contatto"](a)

    esito = _centrale()

    assert [r["agency_id"] for r in esito["runs"]] == [a]
    assert len(spia) == 1 and spia[0][0] == _email(mondo, ca)
    for mid in (stima_b, segnato_b, _passi(mondo, b)[0]["id"]):
        assert _riga(mondo, mid)["status"] == "queued"
    assert [r["agency_id"] for r in iscrizioni(mondo) if r["contact_id"] == ca["contact"]] == [a]


def test_08_i_messaggi_accodati_prima_della_sospensione_restano_in_coda(mondo, modulo, spia):
    a = mondo["a"]
    journey_attiva(modulo, mondo["ctx"](), passi=PASSI)
    mondo["contatto"]()
    modulo["tick"].tick(mondo["ctx"]())  # composto con la firma dell'agenzia
    prima = _passi(mondo, a)[0]
    assert prima["status"] == "queued" and prima["subject_snapshot"] == "[prova] A"
    _stato(mondo, a, "suspended")

    for _ in range(3):
        _centrale()

    assert spia == []
    dopo = _riga(mondo, prima["id"])
    assert dopo == prima, "un messaggio accodato prima della sospensione e' stato toccato"
    assert mondo["sql"]("SELECT count(*) FROM communication_attempts WHERE message_id = %s",
                        (prima["id"],))[0][0] == 0


def test_09_alla_riattivazione_nessun_invio_massivo(mondo, modulo, spia):
    from communication import service as comm_service
    a = mondo["a"]
    journey_attiva(modulo, mondo["ctx"](), passi=PASSI)
    for _ in range(comm_service.DEFAULT_CLAIM_BATCH + 2):
        mondo["contatto"]()
    modulo["tick"].tick(mondo["ctx"]())
    _stato(mondo, a, "suspended")
    _centrale()
    assert spia == []

    _stato(mondo, a, "active")
    assert _centrale()["agencies"] == 0 and spia == []
    primo = _normale(mondo, a)
    assert primo["sent"] == comm_service.DEFAULT_CLAIM_BATCH
    assert len([m for m in _passi(mondo, a) if m["status"] == "queued"]) == 2


def test_10_nessun_accesso_per_l_agenzia_sospesa(mondo, modulo, spia):
    a = mondo["a"]
    journey_attiva(modulo, mondo["ctx"](), passi=PASSI)
    _stato(mondo, a, "suspended")
    mondo["contatto"]()
    utente = mondo["admin"]["id"]
    mondo["sql"]("INSERT INTO operator_sessions (operator_user_id, token_hash, expires_at, revoked_at) "
                 "VALUES (%s, %s, NOW() + INTERVAL '1 day', NOW())", (utente, "f" * 64))
    sessioni = mondo["righe"]("operator_sessions")
    utenti = mondo["righe"]("operator_users")

    _centrale()

    assert len(spia) == 1
    assert mondo["righe"]("operator_sessions") == sessioni
    assert mondo["righe"]("operator_users") == utenti


def test_11_il_cron_lavora_le_sospese_qualunque_sia_agency_id(mondo, modulo, spia, monkeypatch, capsys):
    import importlib
    from communication import central_continuity
    from communication.providers import email_smtp
    cron = importlib.import_module("run_flow_p2b_cron")
    a = mondo["a"]
    journey_attiva(modulo, mondo["ctx"](), passi=PASSI)
    _stato(mondo, a, "suspended")
    mondo["contatto"]()
    # il flow delle agenzie attive non e' oggetto di questo test
    monkeypatch.setattr(cron, "_active_agency_ids", lambda: [1])
    monkeypatch.setattr(cron, "run_cycle", lambda agency_id, **_k: (0, {}, {}))
    monkeypatch.setattr(central_continuity.dispatcher, "adapter_per", lambda canale: email_smtp)

    assert cron.main(["--agency-id", "1"]) == 0
    assert len(spia) == 1 and _passi(mondo, a)[0]["status"] == "sent"
    assert "phase=central_continuity status=completed" in capsys.readouterr().out

    def rotto(**_k):
        raise RuntimeError("giu'")
    monkeypatch.setattr(central_continuity, "run_for_suspended_agencies", rotto)
    assert cron.main(["--agency-id", "1"]) == 1
