"""ADM D15 - collaudo di attivazione su PostgreSQL usa-e-getta, con D15 ATTIVA.

Lo schema e' quello completo del runner (catena `completo`), che contiene gia'
la migration 105 (`migrations/105_...`). La fixture qui sotto lo verifica.
Nessun database condiviso viene toccato da qui.

  01  P26-3: le identita' di certificazione si DISMETTONO (sessioni cancellate,
      membership revocate, account disabilitati), senza un solo DELETE su
      account o membership; login negato; residuo operativo zero, residuo
      dismesso contato; la riga storica resta e non si cancella
  02  P26-6: identita' e agenzia dedicata; le fixture dentro l'agenzia
      dedicata si rimuovono, l'agenzia si ARCHIVIA; preflight, verifica e
      CLEAN-CHK superati sullo schema vero; residui segnalati
  03  un residuo OPERATIVO ferma ancora le certificazioni
  04  ADM-3 BIS con D15: agenzia ed operatore eliminati dalla Rete, le email
      della valutazione e il follow-up partono dal centro con firma STIMA360,
      nessun dato cambia proprietario, nessuna email reale (spia)
"""
from __future__ import annotations

import io
from contextlib import contextmanager

import pytest

from tests.test_adm1_super_admin_postgres import PASSWORD, adm1  # noqa: F401
from tests.test_c1_lettura_territoriale_postgres import rete  # noqa: F401
from tests.test_censimento_3_backend_postgres import (  # noqa: F401
    DSN, _q, completo, d15_attiva, mondo,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


@pytest.fixture(scope="module", autouse=True)
def _d15_nel_database_di_prova(completo):
    """D15 c'e' gia': l'ha applicata il runner con il resto della catena."""
    assert d15_attiva(completo), "la catena del runner non ha applicato la 105"
    yield


def _database(script, m):
    """Il `Database` dello script, con i cursori sul database di prova."""
    from psycopg2.extras import RealDictCursor

    @contextmanager
    def factory(*, commit=False):
        conn = m["psycopg2"].connect(m["dsn"], cursor_factory=RealDictCursor)
        try:
            cur = conn.cursor()
            yield conn, cur
            if commit:
                conn.commit()
        finally:
            conn.close()

    return script.Database(factory)


def _sequenza_agenzie(m):
    """Le agenzie della fixture nascono con id espliciti: la sequenza va
    riallineata perche' `create_dedicated_agency` (INSERT senza id) funzioni
    come sul TEST."""
    _q(m, "SELECT setval(pg_get_serial_sequence('agencies', 'id'), (SELECT max(id) FROM agencies))")


def _report(script):
    return script.Report(stream=io.StringIO())


def _fallimenti(script, report):
    return [(i, t) for k, i, t in report.rows if k == script.FAIL]


def _login(m, email, password):
    from fastapi.testclient import TestClient
    c = TestClient(m["app"], base_url="https://testserver")
    return c.post("/api/operator-auth/login", json={"email": email, "password": password}).status_code


def _rifiutata(m, sql, params):
    from psycopg2 import errors
    with pytest.raises(errors.RestrictViolation):
        _q(m, sql, params)


def _sessione(m, user_id):
    from operator_auth.security import generate_session_token, hash_session_token
    _q(m, "INSERT INTO operator_sessions (operator_user_id, token_hash, expires_at) "
          "VALUES (%s, %s, NOW() + INTERVAL '10 minutes')",
       (user_id, hash_session_token(generate_session_token())))


def test_01_p26_3_dismette_senza_cancellare(adm1):
    from scripts import p26_3_live_cert as p26_3
    m = adm1
    report = _report(p26_3)
    operatori = p26_3.TemporaryOperators(_database(p26_3, m), report)
    creati = [operatori.create({"id": 1}), operatori.create({"id": 2})]
    ids = tuple(o["id"] for o in creati)
    _sessione(m, ids[0])
    assert _login(m, creati[1]["email"], creati[1]["password"]) == 204
    assert operatori.leftovers() == 2

    operatori.cleanup()

    assert not _fallimenti(p26_3, report), report.rows
    assert _q(m, "SELECT status FROM operator_users WHERE id = ANY(%s) ORDER BY id",
              (list(ids),)) == [["disabled"], ["disabled"]]
    assert {r[0] for r in _q(m, "SELECT status FROM agency_memberships "
                                "WHERE operator_user_id = ANY(%s)", (list(ids),))} == {"revoked"}
    assert _q(m, "SELECT count(*) FROM operator_sessions WHERE operator_user_id = ANY(%s)",
              (list(ids),))[0][0] == 0
    assert _login(m, creati[1]["email"], creati[1]["password"]) == 401
    assert operatori.leftovers() == 0 and operatori.retired() >= 2
    # la riga storica resta, e non si cancella
    _rifiutata(m, "DELETE FROM operator_users WHERE id = %s", (ids[0],))
    _rifiutata(m, "DELETE FROM agency_memberships WHERE operator_user_id = %s", (ids[0],))


def test_02_p26_6_archivia_l_agenzia_dedicata_e_dismette_le_identita(adm1):
    from scripts import p26_6_live_cert as p26_6
    m = adm1
    _sequenza_agenzie(m)
    report = _report(p26_6)
    cert = p26_6.Certification(_database(p26_6, m), report)
    operatore = cert.create_operator({"id": 1})
    dedicata = cert.create_dedicated_agency("X")
    assert dedicata, report.rows
    agenzia = dedicata["id"]
    # una fixture del run dentro l'agenzia dedicata
    contatto = _q(m, "INSERT INTO contacts (agency_id, display_name) VALUES (%s, %s) RETURNING id",
                  (agenzia, f"{cert.marker('X')} contatto"))[0][0]
    _sessione(m, operatore["id"])
    assert cert.leftovers() >= 1

    cert.snapshot_before_cleanup()
    cert.preflight_dependencies()
    cert.cleanup_dedicated_agencies()
    cert.verify_no_residue()
    cert.cleanup_database()

    assert not _fallimenti(p26_6, report), report.rows
    esiti = {i: t for _k, i, t in report.rows}
    assert "archiviate" in esiti["CLEAN-DEDICATA"], esiti["CLEAN-DEDICATA"]
    assert _q(m, "SELECT status FROM agencies WHERE id = %s", (agenzia,))[0][0] == "archived"
    assert _q(m, "SELECT count(*) FROM contacts WHERE id = %s", (contatto,))[0][0] == 0
    ids = list(cert.created_user_ids)
    assert {r[0] for r in _q(m, "SELECT status FROM operator_users WHERE id = ANY(%s)", (ids,))} \
        == {"disabled"}
    assert {r[0] for r in _q(m, "SELECT status FROM agency_memberships "
                                "WHERE operator_user_id = ANY(%s)", (ids,))} == {"revoked"}
    assert _q(m, "SELECT count(*) FROM operator_sessions WHERE operator_user_id = ANY(%s)",
              (ids,))[0][0] == 0
    assert _login(m, dedicata["email"], dedicata["password"]) == 401
    # nessun residuo operativo; i dismessi si contano
    assert cert.leftovers() == 0
    identita, agenzie = cert.retired()
    assert identita >= 2 and agenzie >= 1
    _rifiutata(m, "DELETE FROM agencies WHERE id = %s", (agenzia,))


def test_03_un_residuo_operativo_ferma_ancora_le_certificazioni(adm1):
    from scripts import p26_3_live_cert as p26_3
    from scripts import p26_6_live_cert as p26_6
    m = adm1
    _sequenza_agenzie(m)
    in_corso = p26_3.TemporaryOperators(_database(p26_3, m), _report(p26_3))
    in_corso.create({"id": 1})
    try:
        assert in_corso.leftovers() == 1
        # un'agenzia dedicata non archiviata e' un run P26-6 in corso
        cert = p26_6.Certification(_database(p26_6, m), _report(p26_6))
        prima = cert.leftovers()
        cert.create_dedicated_agency("Y")
        assert cert.leftovers() == prima + 2   # la sua identita' e la sua agenzia
        cert.snapshot_before_cleanup()
        cert.cleanup_dedicated_agencies()
        cert.cleanup_database()
        assert cert.leftovers() == prima
    finally:
        in_corso.cleanup()
    assert in_corso.leftovers() == 0


# ---------------------------------------------------------------------------
# ADM-3 BIS con D15
# ---------------------------------------------------------------------------

@pytest.fixture
def comunicazioni(adm1, monkeypatch):
    """I moduli di comunicazione sul database di prova; nessuna email reale."""
    import sys

    import psycopg2
    from psycopg2.extras import DictCursor

    from communication import unsubscribe
    from communication.providers import email_smtp

    m = adm1

    def connessione():
        return psycopg2.connect(m["dsn"], cursor_factory=DictCursor)

    for caricato in list(sys.modules.values()):
        if getattr(caricato, "__name__", "").startswith("tests."):
            continue
        if getattr(caricato, "get_connection", None) is not None:
            try:
                monkeypatch.setattr(caricato, "get_connection", connessione)
            except AttributeError:  # pragma: no cover
                pass
    from communication import database as comm_db
    from consent import database as consent_db
    from core import database as core_db
    from operator_auth import database as operator_db
    for aiutante in (comm_db.communication_cursor, consent_db.consent_cursor,
                     core_db.core_cursor, operator_db.operator_cursor):
        globali = getattr(aiutante, "__wrapped__", aiutante).__globals__
        if "get_connection" in globali:
            monkeypatch.setitem(globali, "get_connection", connessione)
    monkeypatch.setenv(unsubscribe.SECRET_ENV, "una-chiave-di-prova-lunga-almeno-trentadue-caratteri")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://test.stima360.it")
    inviate: list = []
    monkeypatch.setattr(email_smtp, "invia_mail", lambda *a, **k: (inviate.append(a), True)[1])
    yield {**m, "inviate": inviate}
    # La sequenza di prova si ritira con il servizio vero: nessun DELETE.
    from communication import journey_service
    from tests.test_p29_3c_orchestrator_postgres import Ctx
    ctx = Ctx(m["quattro"], user_id=m["persone"]["quattro"], role="agency_owner")
    for (jid,) in _q(m, "SELECT id FROM communication_journeys WHERE agency_id = %s "
                        "AND status = 'active'", (m["quattro"],)):
        journey_service.retire_journey(ctx, jid)


def test_04_adm3_bis_continua_con_d15_dopo_l_eliminazione_dalla_rete(comunicazioni):
    from communication import central_continuity, journey_service
    from communication.providers import email_smtp
    from tests.test_p29_3c_orchestrator_postgres import Ctx, passo

    m = comunicazioni
    q, titolare = m["quattro"], m["persone"]["quattro"]
    ctx = Ctx(q, user_id=titolare, role="agency_owner")
    j = journey_service.provision_journey(
        ctx, journey_key="stima_lead", version=1, trigger_type="stima_pdf_sent",
        name="Lead da stima", steps=[passo(1, delay=0)], send_timezone="Europe/Rome")
    journey_service.activate_journey(ctx, j["id"])

    contatto = _q(m, "INSERT INTO contacts (agency_id, first_name, display_name, email, email_normalized) "
                     "VALUES (%s, 'Lia', 'Lia D15', 'lia.d15@x.it', 'lia.d15@x.it') RETURNING id", (q,))[0][0]
    _q(m, "INSERT INTO consent_events (agency_id, contact_id, purpose, decision, decided_at, source, "
          "actor_type) VALUES (%s, %s, 'marketing', 'granted', NOW(), 'crm', 'subject')", (q, contatto))
    _q(m, "UPDATE contacts SET marketing_consent = TRUE, marketing_consent_at = NOW(), "
          "marketing_consent_source = 'crm' WHERE id = %s", (contatto,))
    lead = _q(m, "INSERT INTO leads (agency_id, contact_id) VALUES (%s, %s) RETURNING id",
              (q, contatto))[0][0]
    stima = _q(m, "INSERT INTO stime (agency_id, comune) VALUES (%s, 'Alba') RETURNING id", (q,))[0][0]
    _q(m, """INSERT INTO communication_messages
                 (agency_id, contact_id, lead_id, stima_id, channel, direction, communication_type,
                  mode, reason_code, subject_snapshot, rendered_body, destination_snapshot, status,
                  sent_at, actor_type, idempotency_key)
             VALUES (%s,%s,%s,%s,'email','outbound','service','automatic','stima_pdf','Stima',
                     'corpo','lia.d15@x.it','sent',NOW(),'system',%s)""",
       (q, contatto, lead, stima, f"stima_email_cliente:{stima}"))
    in_coda = _q(m, """INSERT INTO communication_messages
                 (agency_id, contact_id, lead_id, stima_id, channel, direction, communication_type,
                  mode, reason_code, subject_snapshot, rendered_body, destination_snapshot, status,
                  scheduled_at, actor_type, idempotency_key, metadata)
             VALUES (%s,%s,%s,%s,'email','outbound','service','automatic','stima_pdf','Oggetto',
                     'corpo','lia.d15@x.it','queued',NOW() - INTERVAL '1 minute','system',%s,
                     '{"pdf_url": "https://test.stima360.it/pdf/1.pdf"}'::jsonb) RETURNING id""",
                 (q, contatto, lead, stima, f"adm-d15:stima_pdf:{stima}"))[0][0]

    # «Elimina dalla Rete»: agenzia (sospensione + archiviazione) e titolare.
    sa = m["accedi"]("super")
    for verso in ("suspended", "archived"):
        assert sa.patch(f"/api/platform/agencies/{q}", json={"status": verso}).status_code == 200
    assert sa.patch(f"/api/platform/operators/{titolare}", json={"status": "disabled"}).status_code == 200
    try:
        esito = central_continuity.run_for_suspended_agencies(provider=email_smtp)

        assert esito["failed"] == [], esito
        assert q in [r["agency_id"] for r in esito["runs"]], esito
        oggetti = [o for (_d, o, _c) in m["inviate"]]
        assert len(m["inviate"]) == 2, m["inviate"]
        assert "[prova] STIMA360" in oggetti, oggetti
        assert _q(m, "SELECT status FROM communication_messages WHERE id = %s", (in_coda,))[0][0] == "sent"
        # nessun dato cambia proprietario, nulla viene cancellato
        assert _q(m, "SELECT agency_id FROM contacts WHERE id = %s", (contatto,))[0][0] == q
        assert _q(m, "SELECT agency_id FROM leads WHERE id = %s", (lead,))[0][0] == q
        # un secondo giro non duplica
        central_continuity.run_for_suspended_agencies(provider=email_smtp)
        assert len(m["inviate"]) == 2
    finally:
        assert sa.patch(f"/api/platform/operators/{titolare}", json={"status": "active"}).status_code == 200
        assert sa.patch(f"/api/platform/agencies/{q}", json={"status": "active"}).status_code == 200
