"""P29-3B.0 / 3B.2A su PostgreSQL reale: la fondazione delle journey.

COSA SI PROVA QUI E NON ALTROVE

LA MIGRATION 071, su e giu' - e che la giu' riporti la guardia del ledger alla
versione POST-065, non a quella della 064: la politica di purge del genitore
di lifecycle deve sopravvivere a un rollback di questa fase.

LE MATRICI DI STATO, scritte nel database: uno stato terminale con un campo di
avanzamento ancora scritto, una pausa senza chi l'ha decisa, uno `stopped`
senza ragione sono irrappresentabili. Si prova scrivendo davvero righe a meta'.

LO SNAPSHOT E IL TRIGGER li assegna il database. LA TENANCY e' derivata e
verificata. L'ANTI-SPAM e' un indice, non un controllo applicativo.

IL LEGAME COL LEDGER: mai due messaggi vivi dello stesso passo, un
`cancelled` lascia il posto al run successivo, un `sent` lo occupa per sempre.

L'UNSUBSCRIBE: firma valida -> revoca; ripetuto -> un evento solo; alterato ->
niente; di un'altra agenzia -> niente; il consenso di SERVIZIO intatto.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Database usa-e-getta.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
VERSIONE = "071_p29_3_journey_automation"
SECRET = "una-chiave-di-prova-lunga-almeno-trentadue-caratteri"

SCHEMA_MINIMO = """
CREATE TABLE agencies (id BIGSERIAL PRIMARY KEY, name VARCHAR(200) NOT NULL DEFAULT 'A',
  slug VARCHAR(80) NOT NULL UNIQUE, status VARCHAR(20) NOT NULL DEFAULT 'active');
CREATE TABLE operator_users (id BIGSERIAL PRIMARY KEY, email VARCHAR(320) NOT NULL UNIQUE,
  status VARCHAR(20) NOT NULL DEFAULT 'active', is_platform_admin BOOLEAN NOT NULL DEFAULT FALSE);
CREATE TABLE agency_memberships (id BIGSERIAL PRIMARY KEY,
  agency_id BIGINT NOT NULL REFERENCES agencies(id),
  operator_user_id BIGINT NOT NULL REFERENCES operator_users(id),
  role VARCHAR(20) NOT NULL DEFAULT 'agent', status VARCHAR(20) NOT NULL DEFAULT 'active',
  UNIQUE (agency_id, operator_user_id));
CREATE TABLE contacts (id BIGSERIAL PRIMARY KEY,
  agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
  assigned_agent_id BIGINT, display_name VARCHAR(200), email VARCHAR(320), email_normalized VARCHAR(320),
  status VARCHAR(20) NOT NULL DEFAULT 'active', archived_at TIMESTAMPTZ,
  marketing_consent BOOLEAN, marketing_consent_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT contacts_agency_scope_unq UNIQUE (agency_id, id));
CREATE TABLE leads (id BIGSERIAL PRIMARY KEY, agency_id BIGINT NOT NULL, contact_id BIGINT,
  stage VARCHAR(20) NOT NULL DEFAULT 'new', status VARCHAR(20) NOT NULL DEFAULT 'open');
CREATE TABLE stime (id SERIAL PRIMARY KEY,
  agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
  comune VARCHAR(100), via VARCHAR(100), nome VARCHAR(50), cognome VARCHAR(50),
  email VARCHAR(100), telefono VARCHAR(30));
CREATE TABLE properties (id BIGSERIAL PRIMARY KEY, agency_id BIGINT NOT NULL);
CREATE TABLE seller_timeline_events (id BIGSERIAL PRIMARY KEY,
  agency_id BIGINT NOT NULL REFERENCES agencies(id),
  contact_id BIGINT REFERENCES contacts(id) ON DELETE SET NULL, lead_id BIGINT,
  stima_id INTEGER REFERENCES stime(id) ON DELETE SET NULL, property_id BIGINT,
  event_type VARCHAR(50) NOT NULL, event_source VARCHAR(30),
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  idempotency_key VARCHAR(255), created_by VARCHAR(200),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE UNIQUE INDEX idx_ste_idem ON seller_timeline_events (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE TABLE schema_migrations (version TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), rolled_back_at TIMESTAMPTZ);
"""

#: Le migration REALI che la 071 presuppone.
CATENA = ("061_p29_consent_notices", "062_p29_consent_events",
          "063_p29_contacts_consent_projection", "064_p29_communication_foundation",
          "065_p29_service_lifecycle_parent", "067_lmc1b_owner_login_reason", VERSIONE)


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


def _migrazione(nome: str) -> str:
    return (MIGRAZIONI / f"{nome}.sql").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")
    from psycopg2.extras import DictCursor

    nome = f"p29_3_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
            for versione in CATENA:
                cur.execute(_migrazione(versione))
        yield {"conn": conn, "dsn": dsn}
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


@pytest.fixture
def modulo(db, monkeypatch):
    import psycopg2
    from psycopg2.extras import DictCursor

    import database as radice
    from communication import database as comm_db
    from communication import journey_service, journey_repository, unsubscribe
    from communication import service as comm_service
    from consent import database as consent_db
    from core import database as core_db

    def connessione():
        return psycopg2.connect(db["dsn"], cursor_factory=DictCursor)
    for m in (radice, comm_db, consent_db, core_db):
        monkeypatch.setattr(m, "get_connection", connessione)
    monkeypatch.setenv(unsubscribe.SECRET_ENV, SECRET)
    return {"journeys": journey_service, "repo": journey_repository,
            "unsubscribe": unsubscribe, "comm": comm_service}


ORA = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
GIORNO = timedelta(days=1)


@pytest.fixture
def mondo(db):
    """Due agenzie, operatori, un contatto con stima e mail `stima_pdf` spedita."""
    conn = db["conn"]
    with conn.cursor() as cur:
        # Ordine: figlie prima. `consent_events` e il ledger sono append-only
        # e spariscono SOLO con il loro genitore (`contacts`), per cascata.
        for t in ("communication_enrollments", "communication_automation_controls",
                  "communication_journey_steps", "communication_journeys",
                  "seller_timeline_events", "leads", "contacts", "stime",
                  "agency_memberships", "operator_users", "agencies"):
            cur.execute(f"DELETE FROM {t}")
        cur.execute("INSERT INTO agencies (name, slug) VALUES ('A','a-uno') RETURNING id"); a = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (name, slug) VALUES ('B','b-due') RETURNING id"); b = cur.fetchone()[0]

        def operatore(email, agenzia=None, platform=False):
            cur.execute("INSERT INTO operator_users (email, is_platform_admin) VALUES (%s,%s) RETURNING id",
                        (email, platform))
            i = cur.fetchone()[0]
            if agenzia is not None:
                cur.execute("INSERT INTO agency_memberships (agency_id, operator_user_id) VALUES (%s,%s)",
                            (agenzia, i))
            return i
        op_a, op_b = operatore("a@x.it", a), operatore("b@x.it", b)
        admin = operatore("admin@x.it", None, platform=True)
    stato = {"conn": conn, "a": a, "b": b, "op_a": op_a, "op_b": op_b, "admin": admin, "n": 0}

    def contatto(agency, *, consenso=True, inviata=None):
        """`inviata`: quando la mail della stima e' partita. Default: adesso,
        cioe' DOPO l'attivazione di una journey creata prima nel test."""
        stato["n"] += 1
        with conn.cursor() as cur:
            cur.execute("INSERT INTO contacts (agency_id, display_name, email, email_normalized) "
                        "VALUES (%s,%s,%s,%s) RETURNING id",
                        (agency, f"c{stato['n']}", f"c{stato['n']}@x.it", f"c{stato['n']}@x.it"))
            ct = cur.fetchone()[0]
            if consenso:
                cur.execute("INSERT INTO consent_events (agency_id, contact_id, purpose, decision, decided_at, "
                            "source, actor_type) VALUES (%s,%s,'marketing','granted',NOW(),'crm','subject')",
                            (agency, ct))
                cur.execute("UPDATE contacts SET marketing_consent = TRUE, marketing_consent_at = NOW(), "
                            "marketing_consent_source = 'crm' WHERE id = %s", (ct,))
            cur.execute("INSERT INTO stime (agency_id, comune) VALUES (%s,'Alba') RETURNING id", (agency,))
            st = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO communication_messages
                       (agency_id, contact_id, stima_id, channel, direction, communication_type, mode,
                        reason_code, subject_snapshot, rendered_body, destination_snapshot, status,
                        sent_at, actor_type, idempotency_key)
                   VALUES (%s,%s,%s,'email','outbound','service','automatic','stima_pdf','Stima',
                           'corpo','c@x.it','sent',%s,'system',%s) RETURNING id""",
                (agency, ct, st, inviata or datetime.now(timezone.utc),
                 f"stima_email_cliente:{st}"))
            msg = cur.fetchone()[0]
        return {"contact": ct, "stima": st, "trigger": msg}

    def righe(tabella, dove="TRUE", p=()):
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {tabella} WHERE {dove} ORDER BY id", p)
            return [dict(r) for r in cur.fetchall()]

    def sql(testo, p=None):
        """Un errore lascia la connessione pulita: una `down` che rifiuta ha
        aperto il suo BEGIN, e senza rollback tutto cio' che segue direbbe
        `InFailedSqlTransaction` invece della verita'."""
        try:
            with conn.cursor() as cur:
                cur.execute(testo, p)
                return cur.fetchall() if cur.description else None
        except Exception:
            # `conn.rollback()` non basta: con autocommit psycopg2 non sa che
            # il BEGIN della migration ha aperto una transazione sul server.
            with conn.cursor() as cur:
                cur.execute("ROLLBACK")
            raise

    stato.update(contatto=contatto, righe=righe, sql=sql)
    return stato


class Ctx:
    def __init__(self, agency, user_id=None, role=None, is_platform_admin=False):
        self.user_id, self._agency, self.role = user_id, agency, role
        self.is_platform_admin = is_platform_admin

    def require_agency(self):
        if self._agency is None:
            from operator_auth.exceptions import PlatformAdminAgencyRequired
            raise PlatformAdminAgencyRequired("nessuna agenzia")
        return self._agency


PASSI = [
    {"step_no": 1, "step_key": "M1", "reason_code": "m1", "channel": "email",
     "communication_type": "marketing", "default_mode": "automatic", "delay_from": "trigger",
     "delay_seconds": 86400, "template_key": "registry_probe", "template_version": 1,
     "stop_on": ["mandate_signed"]},
    {"step_no": 2, "step_key": "M2", "reason_code": "m2", "channel": "email",
     "communication_type": "marketing", "default_mode": "assisted",
     "delay_from": "previous_step_sent", "delay_seconds": 3600,
     "template_key": "registry_probe", "template_version": 1, "stop_on": []},
]


def _journey_attiva(modulo, ctx, key="stima_lead", passi=PASSI):
    j = modulo["journeys"].provision_journey(
        ctx, journey_key=key, version=1, trigger_type="stima_pdf_sent", name="Lead da stima", steps=passi)
    return modulo["journeys"].activate_journey(ctx, j["id"])


# ---------------------------------------------------------------------------
# A - LA MIGRATION: su, giu', e la guardia POST-065
# ---------------------------------------------------------------------------

def _guardia(mondo) -> str:
    return mondo["sql"]("SELECT prosrc FROM pg_proc WHERE proname = 'communication_messages_guard'")[0][0]


def test_01_la_071_e_applicata_e_la_guardia_protegge_la_provenienza(mondo):
    src = _guardia(mondo)
    for col in ("enrollment_id", "step_no", "run_no"):
        assert f"NEW.{col}" in src, col
    assert "OLD.contact_id IS NULL" in src, "la semantica 065 e' dentro la 071"
    assert mondo["sql"]("SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name='communication_messages'")[0][0] == 39


def test_02_la_down_ripristina_ESATTAMENTE_la_guardia_post_065(mondo):
    """RED per costruzione: il testo della funzione dopo la down deve essere
    identico, carattere per carattere, a quello che la 065 definisce - non a
    quello della 064, che non conosceva il genitore di lifecycle."""
    sql065 = _migrazione("065_p29_service_lifecycle_parent")
    corpo065 = re.search(r"AS \$fn\$(.*?)\$fn\$;", sql065[sql065.index("communication_messages_guard"):], re.S).group(1)
    sql064 = _migrazione("064_p29_communication_foundation")
    corpo064 = re.search(r"AS \$fn\$(.*?)\$fn\$;", sql064[sql064.index("communication_messages_guard"):], re.S).group(1)
    assert corpo064 != corpo065, "il test avrebbe senso solo se 064 e 065 differissero"

    mondo["sql"](_migrazione(f"{VERSIONE}_down"))
    try:
        dopo = _guardia(mondo)
        assert dopo == corpo065, "la guardia dopo la down NON e' quella della 065"
        assert dopo != corpo064
        assert "enrollment_id" not in dopo
        assert mondo["sql"]("SELECT to_regclass('public.communication_enrollments')")[0][0] is None
        assert mondo["sql"]("SELECT count(*) FROM information_schema.columns "
                            "WHERE table_name='communication_messages'")[0][0] == 36
        # e la semantica 065 e' VIVA, non solo testuale: un messaggio senza
        # contatto (lifecycle parent = stima) si puo' ancora scrivere...
        mondo["sql"]("INSERT INTO agencies (name, slug) VALUES ('Z','z-zeta')")
        a = mondo["sql"]("SELECT id FROM agencies WHERE slug='z-zeta'")[0][0]
        mondo["sql"]("INSERT INTO stime (agency_id, comune) VALUES (%s,'X')", (a,))
        st = mondo["sql"]("SELECT max(id) FROM stime")[0][0]
        mondo["sql"](
            """INSERT INTO communication_messages (agency_id, contact_id, stima_id, channel, direction,
               communication_type, mode, reason_code, subject_snapshot, rendered_body,
               destination_snapshot, actor_type, idempotency_key)
               VALUES (%s, NULL, %s, 'email','outbound','service','automatic','stima_pdf','s','b','d@x.it',
                       'system', %s)""", (a, st, f"post-down:{st}"))
        # ...e il suo consenso e il CHECK di reason_code sono intatti.
        assert mondo["sql"]("SELECT count(*) FROM pg_constraint WHERE conname = "
                            "'communication_messages_reason_code_chk'")[0][0] == 1
        assert mondo["sql"]("SELECT to_regclass('public.consent_events')")[0][0] is not None
    finally:
        mondo["sql"](_migrazione(VERSIONE))
    assert "NEW.enrollment_id" in _guardia(mondo), "la up risale"


def test_03_la_down_rifiuta_con_dati_e_non_lascia_niente_a_meta(mondo, modulo):
    import psycopg2
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    j = _journey_attiva(modulo, ctx)
    c = mondo["contatto"](mondo["a"])
    modulo["journeys"].enroll_from_trigger(ctx, journey_id=j["id"], contact_id=c["contact"],
                                          trigger_message_id=c["trigger"])
    with pytest.raises(psycopg2.Error) as info:
        mondo["sql"](_migrazione(f"{VERSIONE}_down"))
    assert "1 enrollment(s)" in str(info.value)
    assert mondo["sql"]("SELECT to_regclass('public.communication_enrollments')")[0][0] is not None
    assert "NEW.enrollment_id" in _guardia(mondo)


def test_04_la_down_rifiuta_con_il_solo_controllo_o_il_solo_messaggio_di_journey(mondo, modulo):
    import psycopg2
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    c = mondo["contatto"](mondo["a"])
    modulo["journeys"].pause_automations(ctx, c["contact"], reason="prova")
    with pytest.raises(psycopg2.Error) as info:
        mondo["sql"](_migrazione(f"{VERSIONE}_down"))
    assert "1 automation control(s)" in str(info.value)


# ---------------------------------------------------------------------------
# B - LA JOURNEY: attore, attivazione, timezone
# ---------------------------------------------------------------------------

def test_05_una_journey_provisionata_dal_sistema_non_inventa_un_operatore(mondo, modulo):
    from operator_auth.context import SystemAgencyContext
    ctx = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
    j = modulo["journeys"].provision_journey(
        ctx, journey_key="stima_lead", version=1, trigger_type="stima_pdf_sent",
        name="Lead da stima", steps=PASSI)
    riga = mondo["righe"]("communication_journeys")[0]
    assert riga["created_by_type"] == "system" and riga["created_by_operator_user_id"] is None
    assert riga["status"] == "draft" and riga["activated_at"] is None
    assert riga["send_timezone"] == "Europe/Rome"
    attiva = modulo["journeys"].activate_journey(ctx, j["id"])
    riga = mondo["righe"]("communication_journeys")[0]
    assert attiva["status"] == "active" and riga["activated_by_type"] == "system"
    assert riga["activated_by_operator_user_id"] is None and riga["activated_at"] is not None


def test_06_una_journey_attivata_da_un_operatore_lo_firma(mondo, modulo):
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    _journey_attiva(modulo, ctx)
    riga = mondo["righe"]("communication_journeys")[0]
    assert riga["created_by_type"] == "operator" and riga["created_by_operator_user_id"] == mondo["op_a"]
    assert riga["activated_by_type"] == "operator" and riga["activated_by_operator_user_id"] == mondo["op_a"]


def test_07_la_matrice_dell_attivazione_e_nel_database(mondo):
    import psycopg2
    for assegnazioni in (
        "status='active'",                                   # senza attore ne' data
        "status='active', activated_at=NOW()",               # senza attore
        "status='active', activated_at=NOW(), activated_by_type='operator'",  # operatore senza id
        "status='retired', retired_at=NOW()",                # mai attivata
        "created_by_type='operator'",                        # operatore senza id
    ):
        mondo["sql"]("INSERT INTO communication_journeys (agency_id, journey_key, version, trigger_type, "
                     "name, created_by_type) VALUES (%s,'kk',1,'stima_pdf_sent','n','system')", (mondo["a"],))
        with pytest.raises(psycopg2.errors.CheckViolation):
            mondo["sql"](f"UPDATE communication_journeys SET {assegnazioni} WHERE journey_key='kk'")
        mondo["sql"]("DELETE FROM communication_journeys WHERE journey_key='kk'")


def test_08_una_sola_versione_attiva_per_chiave_e_la_nuova_ritira_la_vecchia(mondo, modulo):
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    v1 = _journey_attiva(modulo, ctx)
    v2 = modulo["journeys"].provision_journey(
        ctx, journey_key="stima_lead", version=2, trigger_type="stima_pdf_sent", name="v2", steps=PASSI)
    modulo["journeys"].activate_journey(ctx, v2["id"])
    stati = {r["version"]: r["status"] for r in mondo["righe"]("communication_journeys")}
    assert stati == {1: "retired", 2: "active"}
    assert mondo["righe"]("communication_journeys", "id=%s", (v1["id"],))[0]["retired_at"] is not None


def test_09_il_timezone_e_salvato_e_validato(mondo, modulo):
    from communication.exceptions import ValidationError
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    j = modulo["journeys"].provision_journey(
        ctx, journey_key="k_tz", version=1, trigger_type="stima_pdf_sent", name="n", steps=PASSI,
        send_timezone="Europe/Madrid")
    assert mondo["righe"]("communication_journeys", "id=%s", (j["id"],))[0]["send_timezone"] == "Europe/Madrid"
    with pytest.raises(ValidationError):
        modulo["journeys"].provision_journey(
            ctx, journey_key="k_tz2", version=1, trigger_type="stima_pdf_sent", name="n", steps=PASSI,
            send_timezone="Marte/Olympus")


def test_10_un_passo_con_un_template_non_registrato_non_si_crea(mondo, modulo):
    from communication.exceptions import ValidationError
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    passi = [dict(PASSI[0], template_version=99)]
    with pytest.raises(ValidationError):
        modulo["journeys"].provision_journey(
            ctx, journey_key="kk", version=1, trigger_type="stima_pdf_sent", name="n", steps=passi)
    assert mondo["righe"]("communication_journeys") == []


# ---------------------------------------------------------------------------
# C - L'ISCRIZIONE: trigger, snapshot, cutoff, consenso, unicita'
# ---------------------------------------------------------------------------

def test_11_l_iscrizione_nasce_active_con_il_primo_passo_dovuto(mondo, modulo):
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    j = _journey_attiva(modulo, ctx)
    c = mondo["contatto"](mondo["a"])
    esito = modulo["journeys"].enroll_from_trigger(
        ctx, journey_id=j["id"], contact_id=c["contact"], trigger_message_id=c["trigger"])
    e = esito["enrollment"]
    assert esito["created"] and e["status"] == "active"
    assert e["next_step_no"] == 1 and e["next_action_kind"] == "enqueue" and e["awaiting_since"] is None
    riga = mondo["righe"]("communication_enrollments")[0]
    inviata = mondo["righe"]("communication_messages", "id=%s", (c["trigger"],))[0]["sent_at"]
    assert riga["trigger_sent_at"] == inviata, "copiato dal ledger, dal database"
    assert riga["next_action_at"] == inviata + timedelta(seconds=86400), "base = sent_at, non created_at"
    assert riga["stima_id_snapshot"] == c["stima"]
    assert riga["enrolled_by_type"] == "operator" and riga["enrolled_by_operator_user_id"] == mondo["op_a"]
    eventi = mondo["righe"]("seller_timeline_events")
    assert [x["event_type"] for x in eventi] == ["journey_enrolled"]
    assert eventi[0]["agency_id"] == mondo["a"] and eventi[0]["contact_id"] == c["contact"]


def test_12_lo_snapshot_e_il_trigger_sent_at_li_assegna_il_database(mondo, modulo):
    """Un chiamante che li dichiarasse non ottiene un errore: ottiene i valori veri."""
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    j = _journey_attiva(modulo, ctx)
    c = mondo["contatto"](mondo["a"])
    mondo["sql"](
        """INSERT INTO communication_enrollments
               (agency_id, journey_id, contact_id, stima_id, stima_id_snapshot, trigger_message_id,
                trigger_sent_at, status, next_step_no, next_action_at, next_action_kind,
                enrolled_by_type, idempotency_key)
           VALUES (%s,%s,%s,%s, 999999, %s, '1999-01-01', 'active', 1, NOW(), 'enqueue', 'system', 'k')""",
        (mondo["a"], j["id"], c["contact"], c["stima"], c["trigger"]))
    riga = mondo["righe"]("communication_enrollments")[0]
    assert riga["stima_id_snapshot"] == c["stima"]
    assert riga["trigger_sent_at"].year != 1999


def test_13_snapshot_immutabile_stima_non_riassegnabile_set_null_conserva(mondo, modulo):
    import psycopg2
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    j = _journey_attiva(modulo, ctx)
    c = mondo["contatto"](mondo["a"])
    altra = mondo["sql"]("INSERT INTO stime (agency_id, comune) VALUES (%s,'Y') RETURNING id", (mondo["a"],))[0][0]
    e = modulo["journeys"].enroll_from_trigger(ctx, journey_id=j["id"], contact_id=c["contact"],
                                              trigger_message_id=c["trigger"])["enrollment"]
    with pytest.raises(psycopg2.Error):
        mondo["sql"]("UPDATE communication_enrollments SET stima_id_snapshot = 7 WHERE id=%s", (e["id"],))
    with pytest.raises(psycopg2.Error):
        mondo["sql"]("UPDATE communication_enrollments SET stima_id = %s WHERE id=%s", (altra, e["id"]))
    mondo["sql"]("DELETE FROM stime WHERE id = %s", (c["stima"],))
    riga = mondo["righe"]("communication_enrollments")[0]
    assert riga["stima_id"] is None and riga["stima_id_snapshot"] == c["stima"] and riga["status"] == "active"


def test_14_senza_stima_non_si_iscrive(mondo, modulo):
    import psycopg2
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    j = _journey_attiva(modulo, ctx)
    c = mondo["contatto"](mondo["a"])
    with pytest.raises(psycopg2.Error) as info:
        mondo["sql"](
            """INSERT INTO communication_enrollments (agency_id, journey_id, contact_id, trigger_message_id,
               status, next_step_no, next_action_at, next_action_kind, enrolled_by_type, idempotency_key)
               VALUES (%s,%s,%s,%s,'active',1,NOW(),'enqueue','system','k')""",
            (mondo["a"], j["id"], c["contact"], c["trigger"]))
    assert "stima_id is required" in str(info.value)


def test_15_nessuna_iscrizione_storica_anteriore_all_attivazione(mondo, modulo):
    from communication.exceptions import ConflictError
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    vecchio = mondo["contatto"](mondo["a"], inviata=datetime.now(timezone.utc) - 10 * GIORNO)
    j = _journey_attiva(modulo, ctx)
    with pytest.raises(ConflictError) as info:
        modulo["journeys"].enroll_from_trigger(ctx, journey_id=j["id"], contact_id=vecchio["contact"],
                                              trigger_message_id=vecchio["trigger"])
    assert "predates" in str(info.value)
    assert mondo["righe"]("communication_enrollments") == []


def test_16_senza_consenso_l_iscrizione_nasce_stopped_e_M1_non_esiste(mondo, modulo):
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    j = _journey_attiva(modulo, ctx)
    c = mondo["contatto"](mondo["a"], consenso=False)
    e = modulo["journeys"].enroll_from_trigger(ctx, journey_id=j["id"], contact_id=c["contact"],
                                              trigger_message_id=c["trigger"])["enrollment"]
    assert e["status"] == "stopped" and e["stop_reason"] == "consent_not_granted"
    assert e["next_step_no"] is None and e["next_action_at"] is None
    assert mondo["righe"]("communication_messages", "enrollment_id IS NOT NULL") == []
    assert [x["event_type"] for x in mondo["righe"]("seller_timeline_events")] == ["journey_stopped"]


def test_17_le_tre_ragioni_del_consenso_sono_distinte(mondo, modulo):
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    j = _journey_attiva(modulo, ctx)
    # revocato
    c = mondo["contatto"](mondo["a"])
    mondo["sql"]("INSERT INTO consent_events (agency_id, contact_id, purpose, decision, decided_at, source, actor_type) "
                 "VALUES (%s,%s,'marketing','revoked',NOW(),'crm','subject')", (mondo["a"], c["contact"]))
    mondo["sql"]("UPDATE contacts SET marketing_consent = FALSE, marketing_revoked_at = NOW() WHERE id=%s", (c["contact"],))
    e = modulo["journeys"].enroll_from_trigger(ctx, journey_id=j["id"], contact_id=c["contact"],
                                              trigger_message_id=c["trigger"])["enrollment"]
    assert e["stop_reason"] == "consent_revoked"
    # mai dato
    c2 = mondo["contatto"](mondo["a"], consenso=False)
    e2 = modulo["journeys"].enroll_from_trigger(ctx, journey_id=j["id"], contact_id=c2["contact"],
                                               trigger_message_id=c2["trigger"])["enrollment"]
    assert e2["stop_reason"] == "consent_not_granted"
    from communication.journey_enums import CONSENT_STOP_BY_GUARD_REASON
    assert CONSENT_STOP_BY_GUARD_REASON["deny_inconsistent_state"] == "consent_inconsistent"


def test_18_lo_stesso_trigger_non_duplica_la_stessa_journey(mondo, modulo):
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    j = _journey_attiva(modulo, ctx)
    c = mondo["contatto"](mondo["a"])
    uno = modulo["journeys"].enroll_from_trigger(ctx, journey_id=j["id"], contact_id=c["contact"],
                                                trigger_message_id=c["trigger"])
    # la seconda chiamata trova l'iscrizione aperta e rifiuta; con la stessa
    # chiave a livello di database, l'UNIQUE (journey, trigger) la ferma comunque
    from communication.exceptions import ConflictError
    with pytest.raises(ConflictError):
        modulo["journeys"].enroll_from_trigger(ctx, journey_id=j["id"], contact_id=c["contact"],
                                              trigger_message_id=c["trigger"])
    import psycopg2
    with pytest.raises(psycopg2.errors.UniqueViolation) as info:
        mondo["sql"](
            """INSERT INTO communication_enrollments (agency_id, journey_id, contact_id, stima_id, stima_id_snapshot,
               trigger_message_id, trigger_sent_at, status, stop_reason, stopped_at, enrolled_by_type, idempotency_key)
               VALUES (%s,%s,%s,%s,0,%s,NOW(),'stopped','lead_closed',NOW(),'system','k2')""",
            (mondo["a"], j["id"], c["contact"], c["stima"], c["trigger"]))
    assert "comm_enroll_trigger_unq" in str(info.value) or "comm_enroll_stima_unq" in str(info.value)
    assert len(mondo["righe"]("communication_enrollments")) == 1 and uno["created"]


def test_19_lo_stesso_trigger_e_riusabile_da_una_journey_DIVERSA(mondo, modulo):
    """`UNIQUE (journey_id, trigger_message_id)`, non `UNIQUE (trigger_message_id)`."""
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    j1 = _journey_attiva(modulo, ctx, key="stima_lead")
    j2 = _journey_attiva(modulo, ctx, key="altra_journey")
    c = mondo["contatto"](mondo["a"])
    e1 = modulo["journeys"].enroll_from_trigger(ctx, journey_id=j1["id"], contact_id=c["contact"],
                                               trigger_message_id=c["trigger"])["enrollment"]
    modulo["journeys"].stop_enrollment(ctx, e1["id"])   # chiude la prima: il contatto e' libero
    e2 = modulo["journeys"].enroll_from_trigger(ctx, journey_id=j2["id"], contact_id=c["contact"],
                                               trigger_message_id=c["trigger"])["enrollment"]
    assert e2["journey_id"] == j2["id"] and e2["trigger_message_id"] == c["trigger"]
    assert len(mondo["righe"]("communication_enrollments")) == 2


def test_20_UNA_sola_iscrizione_aperta_per_contatto_anche_su_journey_diverse(mondo, modulo):
    import psycopg2
    from communication.exceptions import ConflictError
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    j1 = _journey_attiva(modulo, ctx, key="stima_lead")
    j2 = _journey_attiva(modulo, ctx, key="altra_journey")
    c = mondo["contatto"](mondo["a"])
    modulo["journeys"].enroll_from_trigger(ctx, journey_id=j1["id"], contact_id=c["contact"],
                                          trigger_message_id=c["trigger"])
    with pytest.raises(ConflictError):
        modulo["journeys"].enroll_from_trigger(ctx, journey_id=j2["id"], contact_id=c["contact"],
                                              trigger_message_id=c["trigger"])
    # e l'INDICE lo impone anche a chi salta il service
    with pytest.raises(psycopg2.errors.UniqueViolation) as info:
        mondo["sql"](
            """INSERT INTO communication_enrollments (agency_id, journey_id, contact_id, stima_id, stima_id_snapshot,
               trigger_message_id, trigger_sent_at, status, next_step_no, next_action_at, next_action_kind,
               paused_at, paused_source, paused_by_operator_user_id, enrolled_by_type, idempotency_key)
               VALUES (%s,%s,%s,%s,0,%s,NOW(),'paused',1,NOW(),'enqueue',NOW(),'enrollment',%s,'system','k3')""",
            (mondo["a"], j2["id"], c["contact"], c["stima"], c["trigger"], mondo["op_a"]))
    assert "uq_comm_enroll_open_per_contact" in str(info.value)


# ---------------------------------------------------------------------------
# D - TENANCY E ATTORE
# ---------------------------------------------------------------------------

def test_21_una_stima_o_un_trigger_di_un_altra_agenzia_non_entrano(mondo, modulo):
    import psycopg2
    from communication.exceptions import NotFoundError
    ctx_a = Ctx(mondo["a"], user_id=mondo["op_a"])
    j = _journey_attiva(modulo, ctx_a)
    cb = mondo["contatto"](mondo["b"])
    with pytest.raises(NotFoundError):
        modulo["journeys"].enroll_from_trigger(ctx_a, journey_id=j["id"], contact_id=cb["contact"],
                                              trigger_message_id=cb["trigger"])
    ca = mondo["contatto"](mondo["a"])
    with pytest.raises(psycopg2.Error) as info:
        mondo["sql"](
            """INSERT INTO communication_enrollments (agency_id, journey_id, contact_id, stima_id, stima_id_snapshot,
               trigger_message_id, trigger_sent_at, status, next_step_no, next_action_at, next_action_kind,
               enrolled_by_type, idempotency_key)
               VALUES (%s,%s,%s,%s,0,%s,NOW(),'active',1,NOW(),'enqueue','system','k4')""",
            (mondo["a"], j["id"], ca["contact"], cb["stima"], ca["trigger"]))
    assert "tenancy" in str(info.value)
    assert mondo["righe"]("communication_enrollments") == []


def test_22_un_operatore_di_un_altra_agenzia_non_puo_firmare(mondo, modulo):
    import psycopg2
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    j = _journey_attiva(modulo, ctx)
    c = mondo["contatto"](mondo["a"])
    e = modulo["journeys"].enroll_from_trigger(ctx, journey_id=j["id"], contact_id=c["contact"],
                                              trigger_message_id=c["trigger"])["enrollment"]
    with pytest.raises(psycopg2.Error) as info:
        mondo["sql"]("UPDATE communication_enrollments SET status='paused', paused_at=NOW(), "
                     "paused_source='enrollment', paused_by_operator_user_id=%s WHERE id=%s",
                     (mondo["op_b"], e["id"]))
    assert "no active membership" in str(info.value)


def test_23_il_platform_admin_in_acting_agisce_senza_membership(mondo, modulo):
    ctx = Ctx(mondo["a"], user_id=mondo["admin"], is_platform_admin=True)
    j = _journey_attiva(modulo, ctx)
    c = mondo["contatto"](mondo["a"])
    e = modulo["journeys"].enroll_from_trigger(ctx, journey_id=j["id"], contact_id=c["contact"],
                                              trigger_message_id=c["trigger"])["enrollment"]
    modulo["journeys"].pause_enrollment(ctx, e["id"])
    riga = mondo["righe"]("communication_enrollments")[0]
    assert riga["paused_by_operator_user_id"] == mondo["admin"]
    assert mondo["sql"]("SELECT count(*) FROM agency_memberships WHERE operator_user_id=%s",
                        (mondo["admin"],))[0][0] == 0


def test_24_il_platform_admin_senza_acting_e_rifiutato_prima_di_ogni_scrittura(mondo, modulo):
    from operator_auth.exceptions import PlatformAdminAgencyRequired
    ctx = Ctx(None, user_id=mondo["admin"], is_platform_admin=True)
    with pytest.raises(PlatformAdminAgencyRequired):
        modulo["journeys"].provision_journey(ctx, journey_key="kk", version=1, trigger_type="stima_pdf_sent",
                                             name="n", steps=PASSI)
    with pytest.raises(PlatformAdminAgencyRequired):
        modulo["journeys"].pause_automations(ctx, 1)
    assert mondo["righe"]("communication_journeys") == []
    assert mondo["righe"]("communication_automation_controls") == []


def test_25_un_contesto_senza_persona_non_puo_fare_atti_da_operatore(mondo, modulo):
    from core.exceptions import PermissionDenied
    from operator_auth.context import SystemAgencyContext
    ctx = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
    c = mondo["contatto"](mondo["a"])
    with pytest.raises(PermissionDenied):
        modulo["journeys"].pause_automations(ctx, c["contact"])
    with pytest.raises(PermissionDenied):
        modulo["journeys"].stop_enrollment(ctx, 1, reason="operator")
    assert mondo["righe"]("communication_automation_controls") == []


# ---------------------------------------------------------------------------
# E - LA MATRICE DELL'ENROLLMENT, riga per riga
# ---------------------------------------------------------------------------

def _iscritto(mondo, modulo, ctx=None):
    ctx = ctx or Ctx(mondo["a"], user_id=mondo["op_a"])
    j = _journey_attiva(modulo, ctx)
    c = mondo["contatto"](mondo["a"])
    e = modulo["journeys"].enroll_from_trigger(ctx, journey_id=j["id"], contact_id=c["contact"],
                                              trigger_message_id=c["trigger"])["enrollment"]
    return ctx, j, c, e


@pytest.mark.parametrize("assegnazioni", [
    # ACTIVE con residui di altri stati
    "paused_at = NOW()", "stopped_at = NOW()", "completed_at = NOW()", "stop_reason = 'operator'",
    # ACTIVE senza avanzamento
    "next_step_no = NULL", "next_action_at = NULL", "next_action_kind = NULL",
    # awaiting incoerente
    "awaiting_since = NOW()",                                  # kind='enqueue' ma awaiting
    "next_action_kind = 'await_operator'",                     # await senza awaiting_since
    # PAUSED a meta'
    "status = 'paused'",
    "status = 'paused', paused_at = NOW()",
    "status = 'paused', paused_at = NOW(), paused_source = 'enrollment'",
    "status = 'paused', paused_at = NOW(), paused_source = 'contact_control', paused_by_operator_user_id = %(op)s, next_step_no = NULL",
    # COMPLETED a meta'
    "status = 'completed'",
    "status = 'completed', completed_at = NOW()",              # next_* ancora scritti
    "status = 'completed', completed_at = NOW(), next_step_no = NULL, next_action_at = NULL, next_action_kind = NULL, paused_at = NOW()",
    # STOPPED a meta'
    "status = 'stopped', stopped_at = NOW(), stop_reason = 'lead_closed'",   # next_* ancora scritti
    "status = 'stopped', stop_reason = 'lead_closed', next_step_no = NULL, next_action_at = NULL, next_action_kind = NULL",  # senza data
    "status = 'stopped', stopped_at = NOW(), next_step_no = NULL, next_action_at = NULL, next_action_kind = NULL",           # senza ragione
    "status = 'stopped', stopped_at = NOW(), stop_reason = 'operator', next_step_no = NULL, next_action_at = NULL, next_action_kind = NULL",  # operator senza persona
    "status = 'stopped', stopped_at = NOW(), stop_reason = 'lead_closed', stopped_by_operator_user_id = %(op)s, next_step_no = NULL, next_action_at = NULL, next_action_kind = NULL",  # sistema con persona inventata
    "status = 'stopped', stopped_at = NOW(), stop_reason = 'lead_closed', completed_at = NOW(), next_step_no = NULL, next_action_at = NULL, next_action_kind = NULL",
])
def test_26_ogni_stato_a_meta_e_irrappresentabile(mondo, modulo, assegnazioni):
    import psycopg2
    _, _, _, e = _iscritto(mondo, modulo)
    with pytest.raises(psycopg2.errors.CheckViolation):
        mondo["sql"](f"UPDATE communication_enrollments SET {assegnazioni} WHERE id = %(id)s",
                     {"id": e["id"], "op": mondo["op_a"]})


def test_27_le_transizioni_complete_passano_e_azzerano_cio_che_devono(mondo, modulo):
    ctx, j, c, e = _iscritto(mondo, modulo)
    p = modulo["journeys"].pause_enrollment(ctx, e["id"])
    assert p["status"] == "paused" and p["paused_source"] == "enrollment" and p["next_step_no"] == 1
    r = modulo["journeys"].resume_enrollment(ctx, e["id"])
    assert r["status"] == "active" and r["paused_at"] is None and r["run_no"] == 2
    s = modulo["journeys"].stop_enrollment(ctx, e["id"])
    riga = mondo["righe"]("communication_enrollments")[0]
    assert s["status"] == "stopped" and riga["stop_reason"] == "operator"
    assert riga["stopped_by_operator_user_id"] == mondo["op_a"]
    assert riga["next_step_no"] is None and riga["next_action_at"] is None and riga["next_action_kind"] is None


def test_28_lo_stop_di_sistema_non_inventa_un_operatore(mondo, modulo):
    from operator_auth.context import SystemAgencyContext
    ctx, j, c, e = _iscritto(mondo, modulo)
    sistema = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
    s = modulo["journeys"].stop_enrollment(sistema, e["id"], reason="lead_closed")
    riga = mondo["righe"]("communication_enrollments")[0]
    assert s["status"] == "stopped" and riga["stopped_by_operator_user_id"] is None


def test_29_gli_stati_terminali_non_si_riaprono(mondo, modulo):
    from communication.exceptions import ConflictError
    ctx, j, c, e = _iscritto(mondo, modulo)
    modulo["journeys"].stop_enrollment(ctx, e["id"])
    for chiamata in (lambda: modulo["journeys"].pause_enrollment(ctx, e["id"]),
                     lambda: modulo["journeys"].resume_enrollment(ctx, e["id"]),
                     lambda: modulo["journeys"].stop_enrollment(ctx, e["id"])):
        with pytest.raises(ConflictError):
            chiamata()


def test_30_un_passo_assisted_nasce_in_attesa_dell_agente_e_senza_messaggio(mondo, modulo):
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    passi = [dict(PASSI[0], default_mode="assisted")]
    j = _journey_attiva(modulo, ctx, passi=passi)
    c = mondo["contatto"](mondo["a"])
    e = modulo["journeys"].enroll_from_trigger(ctx, journey_id=j["id"], contact_id=c["contact"],
                                              trigger_message_id=c["trigger"])["enrollment"]
    assert e["next_action_kind"] == "await_operator" and e["awaiting_since"] is not None
    assert mondo["righe"]("communication_messages", "enrollment_id IS NOT NULL") == []
    prossimo = modulo["journeys"].inspect_next_step(ctx, e["id"])
    assert prossimo["step"]["step_key"] == "M1" and prossimo["kind"] == "await_operator"


# ---------------------------------------------------------------------------
# F - PAUSE / RESUME VS UNIQUE DEL LEDGER
# ---------------------------------------------------------------------------

def _accoda_passo(modulo, ctx, e, step_no, run_no, **extra):
    return modulo["comm"].enqueue(
        ctx, contact_id=e["contact_id"], channel="email", communication_type="marketing",
        mode="automatic", reason_code="m1", rendered_body="corpo", subject_snapshot="s",
        destination_snapshot="c@x.it",
        idempotency_key=f"journey:{e['id']}:step:{step_no}:run:{run_no}",
        stima_id=e["stima_id"], enrollment_id=e["id"], step_no=step_no, run_no=run_no, **extra)


def test_31_duplicate_queued_dello_stesso_passo_e_idempotente(mondo, modulo):
    from operator_auth.context import SystemAgencyContext
    ctx, j, c, e = _iscritto(mondo, modulo)
    sistema = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
    uno = _accoda_passo(modulo, sistema, e, 1, 1)
    due = _accoda_passo(modulo, sistema, e, 1, 1)
    assert uno["created"] and not due["created"] and uno["message"]["id"] == due["message"]["id"]
    assert len(mondo["righe"]("communication_messages", "enrollment_id = %s", (e["id"],))) == 1
    # e un run diverso con lo stesso passo VIVO e' rifiutato dall'indice
    import psycopg2
    with pytest.raises(psycopg2.errors.UniqueViolation) as info:
        _accoda_passo(modulo, sistema, e, 1, 2)
    assert "uq_communication_messages_step_alive" in str(info.value)


def test_32_cancel_del_run1_consente_il_run2_dello_stesso_passo(mondo, modulo):
    from operator_auth.context import SystemAgencyContext
    ctx, j, c, e = _iscritto(mondo, modulo)
    sistema = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
    m1 = _accoda_passo(modulo, sistema, e, 1, 1)["message"]
    modulo["journeys"].pause_enrollment(ctx, e["id"])          # -> cancelled, auditabile
    riga = mondo["righe"]("communication_messages", "id=%s", (m1["id"],))[0]
    assert riga["status"] == "cancelled" and riga["metadata"]["cancel_reason"] == "paused"
    assert riga["metadata"]["cancelled_by"] == str(mondo["op_a"])
    modulo["journeys"].resume_enrollment(ctx, e["id"])
    m2 = _accoda_passo(modulo, sistema, e, 1, 2)["message"]
    assert m2["id"] != m1["id"] and m2["status"] == "queued"
    vivi = mondo["righe"]("communication_messages", "enrollment_id=%s AND status <> 'cancelled'", (e["id"],))
    assert len(vivi) == 1


def test_33_un_passo_SENT_non_si_riaccoda_mai_con_nessun_run(mondo, modulo):
    import psycopg2
    from operator_auth.context import SystemAgencyContext
    ctx, j, c, e = _iscritto(mondo, modulo)
    sistema = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
    m1 = _accoda_passo(modulo, sistema, e, 1, 1)["message"]
    mondo["sql"]("UPDATE communication_messages SET status='sent', sent_at=NOW() WHERE id=%s", (m1["id"],))
    with pytest.raises(psycopg2.errors.UniqueViolation) as info:
        _accoda_passo(modulo, sistema, e, 1, 2)
    assert "uq_communication_messages_step_alive" in str(info.value)


def test_34_la_provenienza_e_immutabile_e_va_a_terne(mondo, modulo):
    import psycopg2
    from communication.exceptions import ValidationError
    from operator_auth.context import SystemAgencyContext
    ctx, j, c, e = _iscritto(mondo, modulo)
    sistema = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
    m1 = _accoda_passo(modulo, sistema, e, 1, 1)["message"]
    for col in ("enrollment_id = NULL", "step_no = 2", "run_no = 9"):
        with pytest.raises(psycopg2.Error) as info:
            mondo["sql"](f"UPDATE communication_messages SET {col} WHERE id=%s", (m1["id"],))
        assert "immutable" in str(info.value)
    with pytest.raises(ValidationError):
        modulo["comm"].enqueue(
            sistema, contact_id=e["contact_id"], channel="email", communication_type="marketing",
            mode="automatic", reason_code="m2", rendered_body="b", subject_snapshot="s",
            destination_snapshot="c@x.it", idempotency_key="mezza", enrollment_id=e["id"])
    # una terna a meta' su un INSERT e' rifiutata dal CHECK
    with pytest.raises(psycopg2.errors.CheckViolation):
        mondo["sql"](
            """INSERT INTO communication_messages (agency_id, contact_id, stima_id, channel, direction,
               communication_type, mode, reason_code, subject_snapshot, rendered_body, destination_snapshot,
               actor_type, idempotency_key, enrollment_id, step_no)
               VALUES (%s,%s,%s,'email','outbound','marketing','automatic','m1','s','b','d@x.it','system',
                       'mezza-sql',%s,1)""", (mondo["a"], c["contact"], c["stima"], e["id"]))


# ---------------------------------------------------------------------------
# G - AUTOMATION CONTROL, contatto
# ---------------------------------------------------------------------------

def test_35_la_pausa_del_contatto_ferma_le_iscrizioni_e_cancella_i_queued_ma_non_i_manuali(mondo, modulo):
    from operator_auth.context import SystemAgencyContext
    ctx, j, c, e = _iscritto(mondo, modulo)
    sistema = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
    auto = _accoda_passo(modulo, sistema, e, 1, 1)["message"]
    manuale = modulo["comm"].enqueue(
        ctx, contact_id=c["contact"], channel="email", communication_type="marketing", mode="manual",
        reason_code="operator_manual", rendered_body="ciao", subject_snapshot="s",
        destination_snapshot="c@x.it", idempotency_key="manuale-1")["message"]
    controllo = modulo["journeys"].pause_automations(ctx, c["contact"], reason="cliente chiede tregua")
    assert controllo["paused"] and controllo["pause_reason"] == "cliente chiede tregua"
    riga = mondo["righe"]("communication_automation_controls")[0]
    assert riga["paused_by_operator_user_id"] == mondo["op_a"] and riga["resumed_at"] is None
    enr = mondo["righe"]("communication_enrollments")[0]
    assert enr["status"] == "paused" and enr["paused_source"] == "contact_control"
    stati = {r["id"]: r["status"] for r in mondo["righe"]("communication_messages", "contact_id=%s", (c["contact"],))}
    assert stati[auto["id"]] == "cancelled" and stati[manuale["id"]] == "queued"
    assert mondo["righe"]("communication_messages", "id=%s", (auto["id"],))[0]["metadata"]["cancel_reason"] == "contact_paused"
    eventi = [x["event_type"] for x in mondo["righe"]("seller_timeline_events")]
    assert "automation_paused" in eventi


def test_36_con_le_automazioni_in_pausa_nessuna_nuova_iscrizione(mondo, modulo):
    from communication.exceptions import ConflictError
    ctx = Ctx(mondo["a"], user_id=mondo["op_a"])
    j = _journey_attiva(modulo, ctx)
    c = mondo["contatto"](mondo["a"])
    modulo["journeys"].pause_automations(ctx, c["contact"])
    with pytest.raises(ConflictError) as info:
        modulo["journeys"].enroll_from_trigger(ctx, journey_id=j["id"], contact_id=c["contact"],
                                              trigger_message_id=c["trigger"])
    assert "paused" in str(info.value)
    assert mondo["righe"]("communication_enrollments") == []


def test_37_la_ripresa_riabilita_riprende_solo_le_pause_da_controllo_e_non_inventa(mondo, modulo):
    ctx, j, c, e = _iscritto(mondo, modulo)
    modulo["journeys"].pause_automations(ctx, c["contact"])
    controllo = modulo["journeys"].resume_automations(ctx, c["contact"])
    assert not controllo["paused"] and controllo["resumed_at"] is not None
    riga = mondo["righe"]("communication_automation_controls")[0]
    assert riga["paused_at"] is None and riga["paused_by_operator_user_id"] is None and riga["pause_reason"] is None
    assert riga["resumed_by_operator_user_id"] == mondo["op_a"]
    enr = mondo["righe"]("communication_enrollments")[0]
    assert enr["status"] == "active" and enr["run_no"] == 2
    eventi = [x["event_type"] for x in mondo["righe"]("seller_timeline_events")]
    assert eventi.count("automation_paused") == 1 and eventi.count("automation_resumed") == 1
    # una pausa messa A MANO sulla iscrizione non viene ripresa dal contatto
    modulo["journeys"].pause_enrollment(ctx, e["id"])
    modulo["journeys"].pause_automations(ctx, c["contact"])
    modulo["journeys"].resume_automations(ctx, c["contact"])
    enr = mondo["righe"]("communication_enrollments")[0]
    assert enr["status"] == "paused" and enr["paused_source"] == "enrollment"


def test_38_una_nuova_pausa_azzera_resumed_e_la_matrice_del_controllo_e_nel_database(mondo, modulo):
    import psycopg2
    ctx, j, c, e = _iscritto(mondo, modulo)
    modulo["journeys"].pause_automations(ctx, c["contact"])
    modulo["journeys"].resume_automations(ctx, c["contact"])
    modulo["journeys"].pause_automations(ctx, c["contact"], reason="di nuovo")
    riga = mondo["righe"]("communication_automation_controls")[0]
    assert riga["paused"] and riga["resumed_at"] is None and riga["resumed_by_operator_user_id"] is None
    for assegnazioni in (
        "paused_at = NULL",                                    # paused senza data
        "paused_by_operator_user_id = NULL",                   # paused senza persona
        "resumed_at = NOW()",                                  # paused con resumed
        "pause_reason = '  '",                                 # ragione vuota
        "paused = FALSE",                                      # false con residui paused_*
        "paused = FALSE, paused_at = NULL, paused_by_operator_user_id = NULL, pause_reason = NULL, resumed_at = NOW()",  # resumed a meta'
    ):
        with pytest.raises(psycopg2.errors.CheckViolation):
            mondo["sql"](f"UPDATE communication_automation_controls SET {assegnazioni} WHERE id=%s", (riga["id"],))
    with pytest.raises(psycopg2.errors.UniqueViolation):
        mondo["sql"]("INSERT INTO communication_automation_controls (agency_id, contact_id) VALUES (%s,%s)",
                     (mondo["a"], c["contact"]))


def test_39_una_pausa_troppo_vecchia_non_riprende_expired_on_resume(mondo, modulo):
    ctx, j, c, e = _iscritto(mondo, modulo)
    modulo["journeys"].pause_enrollment(ctx, e["id"])
    r = modulo["journeys"].resume_enrollment(ctx, e["id"], now=datetime.now(timezone.utc) + 40 * GIORNO)
    assert r["status"] == "stopped" and r["stop_reason"] == "expired_on_resume"
    assert mondo["righe"]("communication_enrollments")[0]["stopped_by_operator_user_id"] is None


# ---------------------------------------------------------------------------
# H - UNSUBSCRIBE
# ---------------------------------------------------------------------------

def _stato_marketing(mondo, contact_id):
    r = mondo["righe"]("contacts", "id=%s", (contact_id,))[0]
    return r["marketing_consent"], r["marketing_revoked_at"], r.get("marketing_consent_source")


def test_40_token_valido_revoca_il_marketing_e_non_il_servizio(mondo, modulo):
    c = mondo["contatto"](mondo["a"])
    mondo["sql"]("INSERT INTO consent_events (agency_id, contact_id, purpose, decision, decided_at, source, actor_type) "
                 "VALUES (%s,%s,'privacy_terms','granted',NOW(),'crm','subject')", (mondo["a"], c["contact"]))
    mondo["sql"]("UPDATE contacts SET privacy_terms_accepted = TRUE, privacy_terms_accepted_at = NOW() WHERE id=%s", (c["contact"],))
    token = modulo["unsubscribe"].issue(mondo["a"], c["contact"])
    assert modulo["unsubscribe"].process(token) is True
    consenso, revocato, fonte = _stato_marketing(mondo, c["contact"])
    assert consenso is False and revocato is not None and fonte == "unsubscribe_link"
    evento = mondo["righe"]("consent_events", "contact_id=%s AND decision='revoked'", (c["contact"],))
    assert len(evento) == 1
    assert evento[0]["source"] == "unsubscribe_link" and evento[0]["actor_type"] == "subject"
    assert evento[0]["purpose"] == "marketing"
    # il servizio e' intatto
    r = mondo["righe"]("contacts", "id=%s", (c["contact"],))[0]
    assert r["privacy_terms_accepted"] is True
    assert mondo["righe"]("consent_events", "contact_id=%s AND purpose='privacy_terms'", (c["contact"],))[0]["decision"] == "granted"


def test_41_il_retry_e_idempotente_un_evento_solo(mondo, modulo):
    c = mondo["contatto"](mondo["a"])
    token = modulo["unsubscribe"].issue(mondo["a"], c["contact"], issued_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    for _ in range(3):
        assert modulo["unsubscribe"].process(token) is True
    assert len(mondo["righe"]("consent_events", "contact_id=%s AND decision='revoked'", (c["contact"],))) == 1


def test_42_un_token_alterato_non_revoca_niente_e_non_legge_niente(mondo, modulo, monkeypatch):
    c = mondo["contatto"](mondo["a"])
    token = modulo["unsubscribe"].issue(mondo["a"], c["contact"])
    payload, firma = token.split(".")
    import base64
    grezzo = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
    v, ag, ct, q = grezzo.split(":")
    altro = base64.urlsafe_b64encode(f"{v}:{ag}:{int(ct)+1}:{q}".encode()).decode().rstrip("=")
    letture = []
    from consent import service as consent_service
    monkeypatch.setattr(consent_service, "record_revocation",
                        lambda *a, **k: letture.append(1) or {"recorded": True})
    for manomesso in (f"{altro}.{firma}", f"{payload}.{firma[:-2]}xx", payload, "", None, "a.b", "x" * 401):
        assert modulo["unsubscribe"].process(manomesso) is False, manomesso
    assert letture == [], "nessuna scrittura tentata"
    assert _stato_marketing(mondo, c["contact"])[0] is True


def test_43_un_token_di_un_altra_agenzia_non_tocca_il_contatto(mondo, modulo):
    """La firma e' valida, ma il contatto non e' in QUELLA agenzia: per lo
    scope del consenso non esiste, e non si scrive nulla."""
    ca = mondo["contatto"](mondo["a"])
    token = modulo["unsubscribe"].issue(mondo["b"], ca["contact"])   # agenzia B, contatto di A
    assert modulo["unsubscribe"].process(token) is True              # firma valida...
    assert _stato_marketing(mondo, ca["contact"])[0] is True         # ...ma niente revocato
    assert mondo["righe"]("consent_events", "decision='revoked'") == []


def test_44_senza_chiave_di_firma_niente_link_e_niente_revoca(mondo, modulo, monkeypatch):
    from communication.unsubscribe import UnsubscribeNotConfigured
    c = mondo["contatto"](mondo["a"])
    token = modulo["unsubscribe"].issue(mondo["a"], c["contact"])
    monkeypatch.delenv(modulo["unsubscribe"].SECRET_ENV)
    with pytest.raises(UnsubscribeNotConfigured):
        modulo["unsubscribe"].issue(mondo["a"], c["contact"])
    assert modulo["unsubscribe"].process(token) is False
    assert _stato_marketing(mondo, c["contact"])[0] is True


def test_45_la_rotta_pubblica_e_neutra_e_senza_login(mondo, modulo, db, monkeypatch):
    """HTTP vero: GET mostra il pulsante e non scrive; POST scrive; ogni
    risposta e' identica, per token valido, ripetuto o alterato; niente
    cookie, niente sessione."""
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app, base_url="https://testserver")
    c = mondo["contatto"](mondo["a"])
    token = modulo["unsubscribe"].issue(mondo["a"], c["contact"])

    pagina = client.get(f"/api/public/communication/unsubscribe?t={token}")
    assert pagina.status_code == 200 and "Conferma disiscrizione" in pagina.text
    assert _stato_marketing(mondo, c["contact"])[0] is True, "la GET non disiscrive"

    ok = client.post("/api/public/communication/unsubscribe", data={"t": token})
    import html as _html
    assert ok.status_code == 200 and modulo["unsubscribe"].NEUTRAL_MESSAGE in _html.unescape(ok.text)
    assert _stato_marketing(mondo, c["contact"])[0] is False
    di_nuovo = client.post("/api/public/communication/unsubscribe", data={"t": token})
    alterato = client.post("/api/public/communication/unsubscribe", data={"t": token[:-3] + "abc"})
    vuoto = client.post("/api/public/communication/unsubscribe", data={})
    assert ok.text == di_nuovo.text == alterato.text == vuoto.text
    assert ok.headers.get("cache-control") == "no-store"
    assert "set-cookie" not in ok.headers
    assert len(mondo["righe"]("consent_events", "contact_id=%s AND decision='revoked'", (c["contact"],))) == 1


# ---------------------------------------------------------------------------
# I - PURGE: il genitore sparisce, la provenienza lo segue, la guardia non
#     fa fallire la cascata; un UPDATE diretto resta rifiutato
# ---------------------------------------------------------------------------

def test_46_il_purge_del_contatto_porta_via_iscrizione_e_messaggi_senza_far_fallire_la_guardia(mondo, modulo):
    from operator_auth.context import SystemAgencyContext
    ctx, j, c, e = _iscritto(mondo, modulo)
    sistema = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
    _accoda_passo(modulo, sistema, e, 1, 1)
    modulo["journeys"].pause_automations(ctx, c["contact"])
    mondo["sql"]("DELETE FROM contacts WHERE id = %s", (c["contact"],))
    assert mondo["righe"]("communication_enrollments") == []
    assert mondo["righe"]("communication_automation_controls") == []
    assert mondo["righe"]("communication_messages", "contact_id = %s", (c["contact"],)) == []


def test_47_un_update_diretto_di_enrollment_id_a_null_resta_rifiutato(mondo, modulo):
    import psycopg2
    from operator_auth.context import SystemAgencyContext
    ctx, j, c, e = _iscritto(mondo, modulo)
    sistema = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
    m = _accoda_passo(modulo, sistema, e, 1, 1)["message"]
    with pytest.raises(psycopg2.Error) as info:
        mondo["sql"]("UPDATE communication_messages SET enrollment_id = NULL, step_no = NULL, run_no = NULL "
                     "WHERE id = %s", (m["id"],))
    assert "immutable" in str(info.value)


def test_48_un_messaggio_senza_provenienza_si_accoda_anche_su_un_ledger_SENZA_la_071(mondo, modulo):
    """Il codice arriva in TEST prima che la 071 venga applicata (il push
    fa partire il deploy; la migration e' un gesto separato). In quella
    finestra ogni messaggio che il ledger accettava prima deve continuare a
    entrare: la INSERT nomina le tre colonne di provenienza SOLO quando il
    messaggio le porta. Un messaggio di journey, invece, pretende lo schema."""
    import psycopg2
    from operator_auth.context import SystemAgencyContext
    c = mondo["contatto"](mondo["a"])
    sistema = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
    mondo["sql"](_migrazione(f"{VERSIONE}_down"))
    try:
        assert mondo["sql"]("SELECT to_regclass('public.communication_enrollments')")[0][0] is None
        esito = modulo["comm"].enqueue(
            sistema, contact_id=c["contact"], channel="email", communication_type="service",
            mode="automatic", reason_code="operator_manual", rendered_body="corpo",
            subject_snapshot="s", destination_snapshot="c@x.it",
            idempotency_key=f"pre-071:{c['contact']}", stima_id=c["stima"])
        assert esito["created"] and esito["message"]["status"] == "queued"
        assert "enrollment_id" not in esito["message"]
        with pytest.raises(psycopg2.errors.UndefinedColumn):
            modulo["comm"].enqueue(
                sistema, contact_id=c["contact"], channel="email", communication_type="marketing",
                mode="automatic", reason_code="m1", rendered_body="corpo", subject_snapshot="s",
                destination_snapshot="c@x.it", idempotency_key=f"pre-071-journey:{c['contact']}",
                stima_id=c["stima"], enrollment_id=1, step_no=1, run_no=1)
    finally:
        mondo["sql"]("ROLLBACK")
        mondo["sql"](_migrazione(VERSIONE))
    assert mondo["sql"]("SELECT to_regclass('public.communication_enrollments')")[0][0] is not None
