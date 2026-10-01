"""A32-2 - planner, ledger, revalida e dispatcher sul PostgreSQL VERO.

Banco: il database usa-e-getta della catena Agenda certificata (072-078, le
fixture di A30-2/A30-13B/A31-2), piu' il ledger P29 dai file VERI (064, 065,
067), il vincolo reale `contacts_agency_scope_unq` (030) e la 079 registrata
in `schema_migrations`. Il provider e' finto e conta le chiamate: nessuna email
parte. Nessun DB TEST remoto.

L'orologio del planner e della revalida e' iniettato (un giovedi' del 2031);
il ledger usa quello del database per `scheduled_at <= NOW()`, e un messaggio
accodato "adesso" e' comunque gia' dovuto per il claim.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests import test_a30_2_appointments_postgres as a30_2
from tests.test_a30_2_appointments_postgres import db, mondo  # noqa: F401
from tests.test_a30_13b_create_permissions_postgres import agenda_completa  # noqa: F401
from tests.test_a31_2_buyer_visits_postgres import schema_a31  # noqa: F401

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare A32-2")

MIGRAZIONI = a30_2.MIGRAZIONI
LEDGER = ("064_p29_communication_foundation", "065_p29_service_lifecycle_parent",
          "067_lmc1b_owner_login_reason")
VERSIONE_079 = "079_a32_1_appointment_reminders"

ROMA = ZoneInfo("Europe/Rome")


def roma(anno, mese, giorno, ora, minuto=0):
    return datetime(anno, mese, giorno, ora, minuto, tzinfo=ROMA)


ADESSO = roma(2031, 6, 12, 10, 0)
DOMANI_10 = roma(2031, 6, 13, 10, 0)


def _testo(versione: str) -> str:
    return (MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8")


def _unq_030() -> str:
    testo = _testo("030_p26_core_agency_enforce")
    return re.search(r"DO \$do\$\nBEGIN\n    IF NOT EXISTS \(\n        SELECT 1 FROM pg_constraint "
                     r"WHERE conname = 'contacts_agency_scope_unq'.*?\$do\$;", testo, re.S).group(0)


@pytest.fixture(scope="module")
def schema_a32(schema_a31):  # noqa: F811
    conn = schema_a31["conn"]
    with conn.cursor() as cur:
        cur.execute(_unq_030())
        for versione in LEDGER:
            cur.execute(_testo(versione))
        cur.execute(_testo(VERSIONE_079))
        cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (VERSIONE_079,))
    conn.commit()
    return schema_a31


class Provider:
    """Il trasporto finto: conta, non manda."""
    NAME = "a32_2_finto"

    def __init__(self):
        from communication.providers.base import ProviderCapabilities
        self.CAPABILITIES = ProviderCapabilities(returns_message_id=False,
                                                 distinguishes_failure_class=False,
                                                 reports_delivery=False)
        self.inviati = []

    def send(self, message):
        from communication.providers.base import OUTCOME_ACCEPTED, ProviderResult
        self.inviati.append(message["id"])
        return ProviderResult(outcome=OUTCOME_ACCEPTED)


@pytest.fixture
def x(mondo, schema_a32, monkeypatch):  # noqa: F811
    from appointment_reminders import revalidation
    from communication import database as communication_database
    from core import database as core_database

    # la stessa connessione usa-e-getta che `mondo` ha dato a CORE, senza
    # aprire un nuovo sito di connessione in questo file
    monkeypatch.setattr(communication_database, "get_connection", core_database.get_connection)
    # il ledger del test precedente se n'e' andato con la purge dei contatti di
    # `mondo` (FK composita ON DELETE CASCADE): l'unica DELETE che la guardia
    # della 064 ammette.
    sql = mondo["sql"]
    assert sql("SELECT count(*) FROM communication_messages")[0][0] == 0
    sql("UPDATE agencies SET name = CASE WHEN id = %s THEN 'Agenzia Mare' "
        "ELSE 'Agenzia Monti' END", (mondo["a"],))
    sql("UPDATE contacts SET first_name = 'Mario', email = 'mario@example.it' WHERE id = %s",
        (mondo["mario"],))
    sql("UPDATE contacts SET email = 'altro@example.it' WHERE id = %s", (mondo["contatto_b"],))
    sql("UPDATE properties SET address = 'Via Roma', civic_number = '12', city = 'Giulianova' "
        "WHERE id = %s", (mondo["casa"],))
    stato = {"now": ADESSO}
    monkeypatch.setattr(revalidation, "_adesso", lambda: stato["now"])
    mondo["orologio"] = stato
    yield mondo
    mondo["conn"].rollback()


def nuovo(x, *, tipo="seller_meeting", inizio=DOMANI_10, creato=None, stato="scheduled",
          fonte="crm_manual", contatto="mario", agente="luca", agenzia="a", immobile=None,
          luogo=None):
    extra = {"confirmed": ", confirmed_at", "cancelled": ", cancelled_at"}.get(stato, "")
    valori = ", NOW()" if extra else ""
    return x["sql"](
        "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, "
        "start_at, end_at, source, contact_id, property_id, location_text, created_at"
        f"{extra}) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s{valori}) RETURNING id",
        (x[agenzia], x[agente], tipo, stato, inizio, inizio + timedelta(minutes=30), fonte,
         None if contatto is None else x[contatto], None if immobile is None else x[immobile],
         luogo, creato or inizio - timedelta(days=5)))[0][0]


def forza(x, testo, par=None):
    """Una manomissione del ledger che la guardia della 064 rifiuterebbe: la
    si fa a guardia spenta, per simulare un messaggio corrotto."""
    x["sql"]("ALTER TABLE communication_messages DISABLE TRIGGER trg_communication_messages_guard")
    try:
        x["sql"](testo, par)
    finally:
        x["sql"]("ALTER TABLE communication_messages "
                 "ENABLE ALWAYS TRIGGER trg_communication_messages_guard")


def owner(x, agenzia="a"):
    from operator_auth.context import OperatorContext
    return OperatorContext(user_id=x["giorgio"], agency_id=x[agenzia], role="agency_owner",
                           is_platform_admin=False, session_id=None, auth_channel="session")


def giro(x, now=ADESSO, agenzia="a"):
    from appointment_reminders import planner
    return planner.tick(owner(x, agenzia), now=now)


def dispatch(x, now=ADESSO, agenzia="a"):
    from communication import dispatcher
    x["orologio"]["now"] = now
    p = Provider()
    conteggi = dispatcher.dispatch_batch(owner(x, agenzia), channel="email", provider=p)
    return p, conteggi


def messaggi(x):
    return x["sql"]("SELECT id, agency_id, contact_id, channel, communication_type, mode, "
                    "reason_code, template_key, template_version, destination_snapshot, "
                    "idempotency_key, metadata, status, suppressed_reason, actor_type, "
                    "actor_user_id, subject_snapshot, rendered_body, attempt_count "
                    "FROM communication_messages ORDER BY id")


# ---------------------------------------------------------------------------
# PLANNER + LEDGER
# ---------------------------------------------------------------------------

def test_P1_un_promemoria_in_coda_con_i_campi_esatti(x):
    from appointment_reminders import policy
    app = nuovo(x)
    c = giro(x)
    assert (c["scanned"], c["due"], c["queued"]) == (1, 1, 1)
    [m] = messaggi(x)
    chiave = policy.occurrence_key(app, DOMANI_10)
    assert (m["agency_id"], m["contact_id"], m["channel"], m["communication_type"], m["mode"],
            m["reason_code"], m["template_key"], m["template_version"],
            m["destination_snapshot"], m["idempotency_key"], m["status"], m["actor_type"],
            m["actor_user_id"]) == (
        x["a"], x["mario"], "email", "service", "automatic", "appointment_reminder",
        "appointment_reminder_24h", 1, "mario@example.it", chiave, "queued", "system", None)
    assert m["metadata"] == {"kind": "appointment_reminder", "appointment_id": app,
                             "offset": "24h", "occurrence_key": chiave,
                             "start_epoch": int(DOMANI_10.timestamp())}
    assert "Buongiorno Mario" in m["rendered_body"] and "Agenzia Mare" in m["rendered_body"]


def test_P2_due_giri_una_riga_sola(x):
    nuovo(x)
    assert giro(x)["queued"] == 1
    c = giro(x)
    assert c["queued"] == 0 and c["queued_idempotent"] == 1
    assert len(messaggi(x)) == 1


def test_P3_P4_buyer_visit_indirizzo_strutturato_mai_location_text(x):
    nuovo(x, tipo="buyer_visit", immobile="casa",
          luogo="Citofono Rossi, cell 333 7654321, codice 4455")
    giro(x)
    [m] = messaggi(x)
    assert "Via Roma 12, Giulianova" in m["rendered_body"]
    for pezzo in ("Citofono", "7654321", "4455"):
        assert pezzo not in m["rendered_body"] and pezzo not in str(m["metadata"])


def test_P5_altro_tipo_nessun_indirizzo(x):
    nuovo(x, tipo="valuation_presentation", immobile="casa")
    giro(x)
    [m] = messaggi(x)
    assert "Via Roma" not in m["rendered_body"]


def test_P6_P8_filtri_sql_e_policy_nessuna_riga(x):
    nuovo(x, fonte="system", inizio=roma(2031, 6, 13, 9, 0))
    nuovo(x, tipo="call", inizio=roma(2031, 6, 13, 9, 0), agente="marta")
    nuovo(x, stato="cancelled", inizio=roma(2031, 6, 13, 9, 0), agente="giorgio")
    c = giro(x)
    # i filtri grossolani del repository non le leggono nemmeno
    assert c["scanned"] == 0 and messaggi(x) == []


def test_P9_P11_contatto_mancante_archiviato_email(x):
    nuovo(x, contatto=None, inizio=roma(2031, 6, 13, 9, 0))           # SQL: non letto
    nuovo(x, contatto="bruno", inizio=roma(2031, 6, 13, 9, 0), agente="marta")  # senza email
    c = giro(x)
    assert c["scanned"] == 1 and c["skipped_by_reason"]["email_missing_or_invalid"] == 1
    x["sql"]("UPDATE contacts SET email = 'bruno@example.it', status = 'archived' "
             "WHERE id = %s", (x["bruno"],))
    c = giro(x)
    assert c["skipped_by_reason"]["contact_archived"] == 1 and messaggi(x) == []


def test_P12_P13_soglie(x):
    nuovo(x, inizio=roma(2031, 6, 12, 20, 0), creato=roma(2031, 6, 12, 8, 30))   # 11h30
    nuovo(x, inizio=roma(2031, 6, 12, 12, 30), agente="marta")                   # 2h30
    c = giro(x)
    assert c["skipped_by_reason"]["booked_less_than_12h_before"] == 1
    assert c["skipped_by_reason"]["less_than_3h_left"] == 1 and messaggi(x) == []


def test_P14_P16_non_ancora_dovuto_e_orizzonte(x):
    nuovo(x, inizio=roma(2031, 6, 13, 10, 30))                       # target fra 30'
    nuovo(x, inizio=roma(2031, 6, 13, 21, 30), agente="marta")       # 19:00 di oggi
    nuovo(x, inizio=roma(2031, 6, 14, 22, 30), agente="giorgio")     # oltre 36h: non letto
    c = giro(x)
    assert c["scanned"] == 2 and c["not_due"] == 2 and messaggi(x) == []
    # alle 19:00: quello delle 10:30 e' in ritardo ma valido, l'altro e' al target
    assert giro(x, now=roma(2031, 6, 12, 19, 0))["queued"] == 2
    # il terzo entra nell'orizzonte e parte al suo target (13/6 19:00)
    c = giro(x, now=roma(2031, 6, 13, 18, 59))
    # (quello delle 21:30 di oggi e' ancora futuro ma ormai a meno di 3h)
    assert c["scanned"] == 2 and c["not_due"] == 1
    assert c["skipped_by_reason"]["less_than_3h_left"] == 1
    assert giro(x, now=roma(2031, 6, 13, 19, 0))["queued"] == 1


def test_P19_consenso_marketing_negato_il_servizio_parte(x):
    # la riga del contatto nega il marketing; il gate non si interroga per il
    # servizio (le tabelle del consenso non esistono nemmeno in questo banco)
    x["sql"]("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS marketing_consent BOOLEAN")
    x["sql"]("UPDATE contacts SET marketing_consent = FALSE WHERE id = %s", (x["mario"],))
    nuovo(x)
    giro(x)
    p, c = dispatch(x, now=roma(2031, 6, 12, 10, 5))
    assert c["sent"] == 1 and len(p.inviati) == 1


def test_P20_isolamento_fra_agenzie(x):
    a = nuovo(x)
    b = nuovo(x, agenzia="b", contatto="contatto_b", agente="estraneo")
    c = giro(x, agenzia="a")
    assert c["scanned"] == 1
    [m] = messaggi(x)
    assert m["agency_id"] == x["a"] and m["metadata"]["appointment_id"] == a
    # il dispatch di A non tocca B, e quello di B manda solo B
    giro(x, agenzia="b")
    p, _ = dispatch(x, now=roma(2031, 6, 12, 10, 5), agenzia="a")
    righe = {r["agency_id"]: r for r in messaggi(x)}
    assert righe[x["a"]]["status"] == "sent" and righe[x["b"]]["status"] == "queued"
    assert righe[x["b"]]["metadata"]["appointment_id"] == b
    p, _ = dispatch(x, now=roma(2031, 6, 12, 10, 5), agenzia="b")
    assert p.inviati == [righe[x["b"]]["id"]]


def test_P21_R7_email_cambiata_soppresso_e_mai_ricreato(x):
    nuovo(x)
    giro(x)
    x["sql"]("UPDATE contacts SET email = 'nuova@example.it' WHERE id = %s", (x["mario"],))
    p, c = dispatch(x, now=roma(2031, 6, 12, 10, 5))
    assert p.inviati == [] and c["suppressed"] == 1
    [m] = messaggi(x)
    assert (m["status"], m["suppressed_reason"]) == ("suppressed", "destination_changed")
    c = giro(x, now=roma(2031, 6, 12, 11, 0))
    assert c["queued"] == 0 and c["queued_idempotent"] == 1 and len(messaggi(x)) == 1
    p, c = dispatch(x, now=roma(2031, 6, 12, 11, 5))
    assert p.inviati == [] and c["claimed"] == 0


def test_P22_reschedule_il_successore_ha_una_chiave_nuova_sotto_la_policy(x):
    from appointments import repository as app_repo  # noqa: F401 - solo per chiarezza
    vecchio = nuovo(x)
    giro(x)
    # A30: il vecchio diventa `rescheduled`, il successore e' una riga nuova
    x["sql"]("UPDATE appointments SET status = 'rescheduled', rescheduled_at = NOW() "
             "WHERE id = %s", (vecchio,))
    succ = x["sql"](
        "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, "
        "start_at, end_at, source, contact_id, rescheduled_from_id, created_at) VALUES "
        "(%s,%s,'seller_meeting','scheduled',%s,%s,'crm_manual',%s,%s,%s) RETURNING id",
        (x["a"], x["luca"], roma(2031, 6, 14, 11, 0), roma(2031, 6, 14, 11, 30), x["mario"],
         vecchio, ADESSO))[0][0]
    p, c = dispatch(x, now=roma(2031, 6, 12, 10, 5))
    assert p.inviati == [] and messaggi(x)[0]["suppressed_reason"] == "status_not_allowed"
    c = giro(x)                    # il successore e' oltre l'orizzonte: non letto
    assert c["queued"] == 0 and c["scanned"] == 0 and len(messaggi(x)) == 1
    assert giro(x, now=roma(2031, 6, 13, 10, 59))["not_due"] == 1
    c = giro(x, now=roma(2031, 6, 13, 11, 0))
    assert c["queued"] == 1
    righe = messaggi(x)
    assert len(righe) == 2 and righe[1]["metadata"]["appointment_id"] == succ
    assert righe[0]["idempotency_key"] != righe[1]["idempotency_key"]


# ---------------------------------------------------------------------------
# REVALIDA FINALE + DISPATCH
# ---------------------------------------------------------------------------

def test_R1_valido_una_chiamata_sent_tentativo_coerente(x):
    nuovo(x)
    giro(x)
    p, c = dispatch(x, now=roma(2031, 6, 12, 10, 5))
    assert c == {"claimed": 1, "sent": 1, "suppressed": 0, "failed": 0,
                 "indeterminate": 0, "lost": 0}
    [m] = messaggi(x)
    assert p.inviati == [m["id"]] and m["status"] == "sent" and m["attempt_count"] == 1
    [t] = x["sql"]("SELECT attempt_no, outcome, provider FROM communication_attempts "
                   "WHERE message_id = %s", (m["id"],))
    assert tuple(t) == (1, "accepted", "a32_2_finto")
    # il giro dopo: nessun secondo invio
    giro(x, now=roma(2031, 6, 12, 11, 0))
    p, c = dispatch(x, now=roma(2031, 6, 12, 11, 5))
    assert p.inviati == [] and c["claimed"] == 0 and len(messaggi(x)) == 1


@pytest.mark.parametrize("dopo,colonne", [
    ("cancelled", "cancelled_at = NOW(), cancelled_reason = 'x'"),       # R2
    ("completed", "completed_at = NOW()"),                               # R3
    ("no_show", "no_show_at = NOW()"),                                   # R4
    ("rescheduled", "rescheduled_at = NOW()"),                           # R5
])
def test_R2_R5_chiuso_dopo_l_accodamento_provider_zero_soppresso(x, dopo, colonne):
    app = nuovo(x)
    giro(x)
    x["sql"](f"UPDATE appointments SET status = %s, {colonne} WHERE id = %s", (dopo, app))
    p, c = dispatch(x, now=roma(2031, 6, 12, 10, 5))
    assert p.inviati == [] and c["suppressed"] == 1
    [m] = messaggi(x)
    assert (m["status"], m["suppressed_reason"]) == ("suppressed", "status_not_allowed")
    [t] = x["sql"]("SELECT outcome, failure_class FROM communication_attempts "
                   "WHERE message_id = %s", (m["id"],))
    assert tuple(t) == ("rejected", "definite")


def test_R6_contatto_archiviato_dopo(x):
    nuovo(x)
    giro(x)
    x["sql"]("UPDATE contacts SET status = 'archived' WHERE id = %s", (x["mario"],))
    p, _ = dispatch(x, now=roma(2031, 6, 12, 10, 5))
    assert p.inviati == [] and messaggi(x)[0]["suppressed_reason"] == "contact_archived"


def test_R8_contatto_cambiato(x):
    app = nuovo(x)
    giro(x)
    x["sql"]("UPDATE contacts SET email = 'bruno@example.it' WHERE id = %s", (x["bruno"],))
    x["sql"]("UPDATE appointments SET contact_id = %s WHERE id = %s", (x["bruno"], app))
    p, _ = dispatch(x, now=roma(2031, 6, 12, 10, 5))
    assert p.inviati == [] and messaggi(x)[0]["suppressed_reason"] == "contact_changed"


def test_R9_inizio_spostato(x):
    app = nuovo(x)
    giro(x)
    x["sql"]("UPDATE appointments SET start_at = start_at + INTERVAL '1 hour', "
             "end_at = end_at + INTERVAL '1 hour' WHERE id = %s", (app,))
    p, _ = dispatch(x, now=roma(2031, 6, 12, 10, 5))
    assert p.inviati == [] and messaggi(x)[0]["suppressed_reason"] == "occurrence_changed"


def test_R10_R11_meno_di_3h_e_fuori_finestra_al_dispatch(x):
    nuovo(x, inizio=roma(2031, 6, 13, 22, 30))
    assert giro(x, now=roma(2031, 6, 12, 19, 0))["queued"] == 1        # al target, 19:00
    p, _ = dispatch(x, now=roma(2031, 6, 12, 20, 0))                    # fuori finestra
    assert p.inviati == [] and messaggi(x)[0]["suppressed_reason"] == "reminder_not_due_now"


def test_R10_meno_di_3h_al_dispatch(x):
    nuovo(x, inizio=roma(2031, 6, 12, 14, 0), creato=roma(2031, 6, 1, 9, 0))
    assert giro(x, now=roma(2031, 6, 12, 10, 0))["queued"] == 1        # 4h prima
    p, _ = dispatch(x, now=roma(2031, 6, 12, 11, 1))                    # 2h59 prima
    assert p.inviati == [] and messaggi(x)[0]["suppressed_reason"] == "less_than_3h_left"


def test_R12_metadata_corrotti_fail_closed(x):
    nuovo(x)
    giro(x)
    forza(x, "UPDATE communication_messages SET metadata = '{}'::jsonb")
    p, _ = dispatch(x, now=roma(2031, 6, 12, 10, 5))
    assert p.inviati == [] and messaggi(x)[0]["suppressed_reason"] == "reminder_metadata_invalid"


def test_R13_id_di_un_altra_agenzia_nei_metadata(x):
    import json

    from appointment_reminders import planner, policy
    nuovo(x)
    b = nuovo(x, agenzia="b", contatto="contatto_b", agente="estraneo")
    giro(x)
    forza(x, "UPDATE communication_messages SET metadata = %s::jsonb, idempotency_key = %s",
             (json.dumps(planner.metadata_for(b, DOMANI_10)), policy.occurrence_key(b, DOMANI_10)))
    p, _ = dispatch(x, now=roma(2031, 6, 12, 10, 5))
    [m] = messaggi(x)
    assert p.inviati == [] and m["suppressed_reason"] == "appointment_missing"
    assert "altro@example.it" not in str(dict(m))


def test_R14_un_servizio_non_promemoria_parte_come_prima(x):
    from communication import service
    from operator_auth.context import SystemAgencyContext
    ctx = SystemAgencyContext(agency_id=x["a"], origin="communication_dispatch")
    service.enqueue(ctx, contact_id=x["mario"], channel="email", communication_type="service",
                    mode="automatic", reason_code="owner_login_link", rendered_body="<p>link</p>",
                    destination_snapshot="cambiata@example.it", idempotency_key="link-1",
                    subject_snapshot="Il tuo accesso")
    p, c = dispatch(x, now=roma(2031, 6, 12, 23, 0))       # fuori finestra: irrilevante
    assert c["sent"] == 1 and len(p.inviati) == 1


# ---------------------------------------------------------------------------
# LA ROTTA
# ---------------------------------------------------------------------------

@pytest.fixture
def http(x):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from appointment_reminders import planner
    from appointment_reminders.router import router
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import legacy_basic_agency_context

    app = FastAPI()
    app.include_router(router)
    stato = {}
    app.dependency_overrides[legacy_basic_agency_context] = lambda: stato["ctx"]
    client = TestClient(app)

    def come(ruolo, *, agenzia="a", platform=False):
        stato["ctx"] = OperatorContext(
            user_id=x["giorgio"], agency_id=None if agenzia is None else x[agenzia],
            role=ruolo, is_platform_admin=platform, session_id=None, auth_channel="session")
        return client

    x["planner"] = planner
    return come


def test_rotta_owner_admin_200_agent_403_platform_non_vincolato_403(http, x, monkeypatch):
    monkeypatch.setattr(x["planner"], "_adesso", lambda: ADESSO)
    nuovo(x)
    r = http("agency_owner").post("/api/communication/reminders/tick")
    assert r.status_code == 200 and r.json()["queued"] == 1
    r = http("agency_admin").post("/api/communication/reminders/tick", json={})
    assert r.status_code == 200 and r.json()["queued_idempotent"] == 1
    assert http("agent").post("/api/communication/reminders/tick").status_code == 403
    r = http(None, agenzia=None, platform=True).post("/api/communication/reminders/tick")
    assert r.status_code == 403
    assert len(messaggi(x)) == 1


@pytest.mark.parametrize("corpo", [{"agency_id": 1}, {"now": "2031-01-01T00:00:00Z"},
                                   {"email": "a@b.it"}, {"recipient": "x"},
                                   {"appointment_id": 5}, {"limit": 0}])
def test_rotta_il_corpo_non_decide_niente_422(http, x, corpo):
    r = http("agency_owner").post("/api/communication/reminders/tick", json=corpo)
    assert r.status_code == 422 and messaggi(x) == []


def test_rotta_079_non_applicata_503_feature_not_migrated(http, x, monkeypatch):
    monkeypatch.setattr(x["planner"], "_adesso", lambda: ADESSO)
    nuovo(x)
    x["sql"]("UPDATE schema_migrations SET rolled_back_at = NOW() WHERE version = %s",
             (VERSIONE_079,))
    try:
        r = http("agency_owner").post("/api/communication/reminders/tick")
        assert r.status_code == 503 and r.json()["detail"]["code"] == "feature_not_migrated"
        assert messaggi(x) == []
    finally:
        x["sql"]("UPDATE schema_migrations SET rolled_back_at = NULL WHERE version = %s",
                 (VERSIONE_079,))
    assert http("agency_owner").post("/api/communication/reminders/tick").status_code == 200


def test_readiness_riga_del_registro_assente_o_rolled_back(x):
    from psycopg2.extras import RealDictCursor

    from appointment_reminders import repository
    conn = x["conn"]
    conn.rollback()
    autocommit, conn.autocommit = conn.autocommit, True
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            assert repository.schema_ready(cur) is True
            cur.execute("BEGIN")
            cur.execute("DELETE FROM schema_migrations WHERE version = %s", (VERSIONE_079,))
            assert repository.schema_ready(cur) is False
            cur.execute("ROLLBACK")
            cur.execute("BEGIN")
            cur.execute("UPDATE schema_migrations SET rolled_back_at = NOW() WHERE version = %s",
                        (VERSIONE_079,))
            assert repository.schema_ready(cur) is False
            cur.execute("ROLLBACK")
            assert repository.schema_ready(cur) is True
    finally:
        conn.autocommit = autocommit
