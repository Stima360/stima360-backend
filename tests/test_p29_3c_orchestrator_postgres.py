"""P29-3C su PostgreSQL reale: il motore delle journey, e la catena via HTTP.

COSA SI PROVA QUI E NON ALTROVE

IL GIRO, sui dati veri: una mail di stima spedita produce una iscrizione,
l'iscrizione produce un passo in coda quando e' dovuto, il passo SPEDITO
produce il successivo, l'ultimo chiude. Nessuno di questi passaggi e'
osservabile con un doppio: dipendono da indici parziali, da CHECK e da cosa
vede una transazione mentre un'altra e' aperta.

GLI STOP AUTOREVOLI, presi dalle tabelle che li contengono davvero - la 070
per incarichi e sopralluoghi, `leads`, `contacts`, la timeline del
proprietario, il dominio del consenso - e con la PRIORITA' che decide quando
piu' fatti sono veri insieme.

LA CONCORRENZA: due giri simultanei, due click simultanei, uno stop che
corre contro un enqueue. Con due connessioni VERE e transazioni sovrapposte,
che e' l'unico momento in cui `FOR UPDATE SKIP LOCKED` e i vincoli di
unicita' fanno qualcosa.

LA CATENA OPERATIVA: login reale, rotta vera, cookie vero. La matrice di
autorizzazione del tick si misura cambiando SOLO il ruolo di chi chiama.

IL DEPLOY PRIMA DELLA MIGRATION: con la 071 assente le rotte journey
rispondono 503 con un codice macchina, e tutto il resto di P29 continua a
funzionare.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Database usa-e-getta, creato e
distrutto da qui. Nessuna email parte: `invia_mail` e' sempre una spia.
"""
from __future__ import annotations

import os
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
PASSWORD = "orchestrator-password-di-prova"

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

SCHEMA_MINIMO = """
CREATE TABLE agencies (id BIGSERIAL PRIMARY KEY, name VARCHAR(200) NOT NULL DEFAULT 'A',
  slug VARCHAR(80) NOT NULL UNIQUE, status VARCHAR(20) NOT NULL DEFAULT 'active');
CREATE TABLE operator_users (id BIGSERIAL PRIMARY KEY, email VARCHAR(320) NOT NULL,
  email_normalized VARCHAR(320) NOT NULL UNIQUE, password_hash TEXT NOT NULL,
  first_name VARCHAR(100), last_name VARCHAR(100),
  status VARCHAR(20) NOT NULL DEFAULT 'active', is_platform_admin BOOLEAN NOT NULL DEFAULT FALSE,
  last_login_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE agency_memberships (id BIGSERIAL PRIMARY KEY,
  agency_id BIGINT NOT NULL REFERENCES agencies(id),
  operator_user_id BIGINT NOT NULL REFERENCES operator_users(id) ON DELETE CASCADE,
  role VARCHAR(20) NOT NULL DEFAULT 'agent', status VARCHAR(20) NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (agency_id, operator_user_id));
CREATE TABLE operator_sessions (id BIGSERIAL PRIMARY KEY,
  operator_user_id BIGINT NOT NULL REFERENCES operator_users(id) ON DELETE CASCADE,
  token_hash CHAR(64) NOT NULL UNIQUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ, acting_agency_id BIGINT REFERENCES agencies(id),
  acting_entered_at TIMESTAMPTZ);
CREATE TABLE contacts (id BIGSERIAL PRIMARY KEY,
  agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
  assigned_agent_id BIGINT, first_name VARCHAR(100), display_name VARCHAR(200),
  email VARCHAR(320), email_normalized VARCHAR(320),
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
CREATE TABLE properties (id BIGSERIAL PRIMARY KEY,
  agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
  commercial_status VARCHAR(30) NOT NULL DEFAULT 'active');
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

#: Le migration REALI. La 070 c'e' perche' gli stop autorevoli vivono nelle
#: sue due tabelle: provarli su tabelle finte proverebbe la finzione.
CATENA = ("061_p29_consent_notices", "062_p29_consent_events",
          "063_p29_contacts_consent_projection", "064_p29_communication_foundation",
          "065_p29_service_lifecycle_parent", "067_lmc1b_owner_login_reason",
          "070_lmc15_acquisition_bridge", VERSIONE)

TABELLE_DA_PULIRE = (
    "communication_enrollments", "communication_automation_controls",
    "communication_journey_steps", "communication_journeys",
    "stima_inspections", "stima_acquisitions",
    "seller_timeline_events", "leads", "contacts", "stime", "properties",
    "operator_sessions", "agency_memberships", "operator_users", "agencies",
)


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

    nome = f"p29_3c_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
    """I moduli veri, con le connessioni dirottate sul database usa-e-getta.

    Si dirottano TUTTI i punti che aprono una connessione - la radice e i
    quattro alias di dominio - perche' basta che uno resti scoperto perche'
    un test parli con un database diverso da quello che sta preparando.
    """
    import psycopg2
    from psycopg2.extras import DictCursor

    import database as radice
    from communication import database as comm_db
    from communication import journey_service, journey_repository, journey_tick, unsubscribe
    from communication import service as comm_service
    from consent import database as consent_db
    from core import database as core_db
    from operator_auth import database as operator_db
    from seller_intelligence import database as si_db

    def connessione():
        return psycopg2.connect(db["dsn"], cursor_factory=DictCursor)

    # SI DIROTTA OGNI MODULO CHE HA IMPORTATO `get_connection`, non solo
    # quelli che questo test usa di persona.
    #
    # `from database import get_connection` copia il RIFERIMENTO nel modulo
    # che importa: dirottare solo la radice lascerebbe intatte tutte le
    # copie. In una suite intera i moduli caricati sono molti di piu' che in
    # un giro isolato - il primo tentativo dirottava cinque moduli e i test
    # passavano da soli e fallivano nel lotto, con una connessione al socket
    # di default arrivata da una copia non dirottata. L'elenco si costruisce
    # guardando i moduli DAVVERO caricati, cosi' non puo' restare indietro.
    import sys as _sys
    from acquisition import repository as acquisizione_repo
    from seller_intelligence import repository as si_repo

    for modulo_caricato in list(_sys.modules.values()):
        if getattr(modulo_caricato, "__name__", "").startswith("tests."):
            continue
        if getattr(modulo_caricato, "get_connection", None) is not None:
            try:
                monkeypatch.setattr(modulo_caricato, "get_connection", connessione)
            except AttributeError:  # pragma: no cover - moduli senza __dict__
                pass

    # E SI SEGUONO LE FUNZIONI, non solo i nomi dei moduli.
    #
    # Nella suite intera esistono DUE oggetti-modulo `core.database`: alcuni
    # test di P26 caricano file per percorso con `spec_from_file_location` e
    # rimpiazzano la voce in `sys.modules`. Chi era stato importato prima -
    # `acquisition.repository`, per esempio - continua a usare il PRIMO, che
    # non e' piu' raggiungibile per nome. Dirottare solo i moduli in
    # `sys.modules` lasciava quel percorso collegato al database di
    # default: i test passavano da soli e fallivano nel lotto.
    #
    # Qui si parte dalle funzioni che il codice sotto prova chiamera'
    # davvero e si scrive nel loro spazio globale, qualunque modulo sia.
    for aiutante in (comm_db.communication_cursor, consent_db.consent_cursor,
                     core_db.core_cursor, si_db.si_cursor,
                     acquisizione_repo.core_cursor, si_repo.si_cursor,
                     operator_db.operator_cursor):
        globali = getattr(aiutante, "__wrapped__", aiutante).__globals__
        if "get_connection" in globali:
            monkeypatch.setitem(globali, "get_connection", connessione)
    monkeypatch.setenv(unsubscribe.SECRET_ENV, SECRET)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://test.stima360.it")
    return {"journeys": journey_service, "repo": journey_repository, "tick": journey_tick,
            "unsubscribe": unsubscribe, "comm": comm_service, "connessione": connessione}


class Ctx:
    """Un contesto di sessione, come lo vede il dominio."""

    def __init__(self, agency, user_id=None, role="agency_admin", is_platform_admin=False):
        self.user_id, self._agency, self.role = user_id, agency, role
        self.is_platform_admin = is_platform_admin

    @property
    def sees_all_agency_records(self):
        return self.is_platform_admin or self.role in ("agency_owner", "agency_admin")

    def require_agency(self):
        if self._agency is None:
            from operator_auth.exceptions import PlatformAdminAgencyRequired
            raise PlatformAdminAgencyRequired("nessuna agenzia")
        return self._agency


@pytest.fixture
def mondo(db):
    """Due agenzie, i quattro ruoli, e le officine per costruire i fatti."""
    from operator_auth.security import hash_password

    conn = db["conn"]
    suffisso = uuid.uuid4().hex[:8]
    with conn.cursor() as cur:
        for t in TABELLE_DA_PULIRE:
            cur.execute(f"DELETE FROM {t}")
        cur.execute("INSERT INTO agencies (name, slug) VALUES ('A','a-uno') RETURNING id")
        a = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (name, slug) VALUES ('B','b-due') RETURNING id")
        b = cur.fetchone()[0]
        hash_pw = hash_password(PASSWORD)

        def operatore(ruolo, agenzia=None, platform=False):
            email = f"{ruolo}-{suffisso}@example.it"
            cur.execute(
                "INSERT INTO operator_users (email, email_normalized, password_hash, "
                "is_platform_admin) VALUES (%s,%s,%s,%s) RETURNING id",
                (email, email, hash_pw, platform))
            i = cur.fetchone()[0]
            if agenzia is not None:
                cur.execute("INSERT INTO agency_memberships (agency_id, operator_user_id, role) "
                            "VALUES (%s,%s,%s)", (agenzia, i, ruolo))
            return {"id": i, "email": email, "role": ruolo}

        admin = operatore("agency_admin", a)
        owner = operatore("agency_owner", a)
        agente = operatore("agent", a)
        admin_b = operatore("agency_admin-b", b)
        piattaforma = operatore("platform", None, platform=True)

    stato = {"conn": conn, "a": a, "b": b, "admin": admin, "owner": owner, "agente": agente,
             "admin_b": admin_b, "piattaforma": piattaforma, "n": 0}

    def contatto(agency=None, *, consenso=True, inviata=None, assegnato=None, stato_contatto="active"):
        """Un contatto con la sua stima e la mail `stima_pdf` GIA' SPEDITA.

        `inviata` e' il `sent_at` del trigger: e' da li' che il motore misura
        tutto, ed e' il parametro con cui i test fanno accadere il futuro
        senza aspettarlo.
        """
        agency = stato["a"] if agency is None else agency
        stato["n"] += 1
        n = stato["n"]
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO contacts (agency_id, first_name, display_name, email, "
                "email_normalized, assigned_agent_id, status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (agency, f"Nome{n}", f"c{n}", f"c{n}@x.it", f"c{n}@x.it", assegnato, stato_contatto))
            ct = cur.fetchone()[0]
            if consenso:
                cur.execute(
                    "INSERT INTO consent_events (agency_id, contact_id, purpose, decision, "
                    "decided_at, source, actor_type) "
                    "VALUES (%s,%s,'marketing','granted',NOW(),'crm','subject')", (agency, ct))
                cur.execute("UPDATE contacts SET marketing_consent = TRUE, "
                            "marketing_consent_at = NOW(), marketing_consent_source = 'crm' "
                            "WHERE id = %s", (ct,))
            cur.execute("INSERT INTO leads (agency_id, contact_id) VALUES (%s,%s) RETURNING id",
                        (agency, ct))
            lead = cur.fetchone()[0]
            cur.execute("INSERT INTO stime (agency_id, comune) VALUES (%s,'Alba') RETURNING id",
                        (agency,))
            st = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO communication_messages
                       (agency_id, contact_id, lead_id, stima_id, channel, direction,
                        communication_type, mode, reason_code, subject_snapshot, rendered_body,
                        destination_snapshot, status, sent_at, actor_type, idempotency_key)
                   VALUES (%s,%s,%s,%s,'email','outbound','service','automatic','stima_pdf',
                           'Stima','corpo','c@x.it','sent',%s,'system',%s) RETURNING id""",
                (agency, ct, lead, st, inviata or datetime.now(timezone.utc),
                 f"stima_email_cliente:{st}"))
            msg = cur.fetchone()[0]
        return {"contact": ct, "stima": st, "trigger": msg, "lead": lead, "agency": agency}

    def righe(tabella, dove="TRUE", p=()):
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {tabella} WHERE {dove} ORDER BY id", p)
            return [dict(r) for r in cur.fetchall()]

    def sql(testo, p=None):
        try:
            with conn.cursor() as cur:
                cur.execute(testo, p)
                return cur.fetchall() if cur.description else None
        except Exception:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK")
            raise

    stato.update(contatto=contatto, righe=righe, sql=sql,
                 ctx=lambda ruolo="admin", agenzia=None: Ctx(
                     stato["a"] if agenzia is None else agenzia,
                     user_id=stato[ruolo]["id"], role=stato[ruolo]["role"]))
    return stato


GIORNO = timedelta(days=1)
ORA_SEC = 3600


def passo(n, *, mode="automatic", delay=GIORNO.total_seconds(), delay_from="trigger",
          finestra=None, key=None):
    return {"step_no": n, "step_key": key or f"M{n}", "reason_code": f"m{n}", "channel": "email",
            "communication_type": "marketing", "default_mode": mode, "delay_from": delay_from,
            "delay_seconds": int(delay), "send_window": finestra,
            "template_key": "registry_probe", "template_version": 1, "stop_on": []}


PASSI_DUE = [passo(1), passo(2, mode="assisted", delay=ORA_SEC, delay_from="previous_step_sent")]
PASSI_UNO = [passo(1, delay=0)]


def journey_attiva(modulo, ctx, *, key="stima_lead", passi=None, tz="Europe/Rome"):
    j = modulo["journeys"].provision_journey(
        ctx, journey_key=key, version=1, trigger_type="stima_pdf_sent", name="Lead da stima",
        steps=list(passi if passi is not None else PASSI_DUE), send_timezone=tz)
    return modulo["journeys"].activate_journey(ctx, j["id"])


def segna_spedito(mondo, message_id, quando=None):
    """Il messaggio diventa `sent`, come lo farebbe la finalizzazione.

    Si tocca solo cio' che il dispatcher tocca: lo stato e l'istante. Le
    colonne di identita' e di provenienza restano immutabili - la guardia del
    ledger rifiuterebbe, ed e' giusto che rifiuti anche a un test.
    """
    mondo["sql"]("UPDATE communication_messages SET status = 'sent', sent_at = %s WHERE id = %s",
                 (quando or datetime.now(timezone.utc), message_id))


def messaggi(mondo, enrollment_id=None):
    dove = "enrollment_id IS NOT NULL" if enrollment_id is None else "enrollment_id = %s"
    return mondo["righe"]("communication_messages", dove,
                          () if enrollment_id is None else (enrollment_id,))


def iscrizioni(mondo):
    return mondo["righe"]("communication_enrollments")


# ===========================================================================
# C - NUOVE ISCRIZIONI
# ===========================================================================

def test_01_una_mail_di_stima_spedita_produce_una_iscrizione_attiva(mondo, modulo):
    ctx = mondo["ctx"]()
    j = journey_attiva(modulo, ctx)
    c = mondo["contatto"]()
    conteggi = modulo["tick"].tick(ctx)

    assert conteggi["enrolled_active"] == 1, conteggi
    (e,) = iscrizioni(mondo)
    assert e["status"] == "active" and e["journey_id"] == j["id"]
    assert e["contact_id"] == c["contact"] and e["trigger_message_id"] == c["trigger"]
    # Il primo passo e' dovuto un giorno dopo il TRIGGER, non dopo il tick.
    assert e["next_step_no"] == 1 and e["next_action_kind"] == "enqueue"
    assert e["next_action_at"] - e["trigger_sent_at"] == GIORNO
    # Lo snapshot lo assegna il database (disciplina LMC-15).
    assert e["stima_id_snapshot"] == c["stima"]
    assert messaggi(mondo) == []


def test_02_una_stima_spedita_PRIMA_dell_attivazione_non_iscrive_nessuno(mondo, modulo):
    """Il cutover di P29-3A.1 §C: il motore non recupera la storia."""
    ctx = mondo["ctx"]()
    mondo["contatto"](inviata=datetime.now(timezone.utc) - 30 * GIORNO)
    journey_attiva(modulo, ctx)
    conteggi = modulo["tick"].tick(ctx)
    assert conteggi["enrolled_active"] == 0 and iscrizioni(mondo) == []


def test_03_senza_consenso_l_iscrizione_nasce_stopped_e_nessun_messaggio(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx)
    mondo["contatto"](consenso=False)
    conteggi = modulo["tick"].tick(ctx)

    assert conteggi["enrolled_stopped"] == 1 and conteggi["enrolled_active"] == 0
    (e,) = iscrizioni(mondo)
    assert e["status"] == "stopped" and e["stop_reason"] == "consent_not_granted"
    assert e["next_step_no"] is None and e["next_action_at"] is None
    assert messaggi(mondo) == []


def test_04_le_tre_ragioni_del_consenso_sono_distinte(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx)
    mai = mondo["contatto"](consenso=False)
    revocato = mondo["contatto"]()
    incoerente = mondo["contatto"]()
    mondo["sql"](
        "INSERT INTO consent_events (agency_id, contact_id, purpose, decision, decided_at, "
        "source, actor_type) VALUES (%s,%s,'marketing','revoked',NOW(),'crm','subject')",
        (mondo["a"], revocato["contact"]))
    mondo["sql"]("UPDATE contacts SET marketing_consent = FALSE, marketing_revoked_at = NOW() "
                 "WHERE id = %s", (revocato["contact"],))
    # Concesso per evento, ma la proiezione dice il contrario: incoerente.
    mondo["sql"]("UPDATE contacts SET marketing_consent = FALSE WHERE id = %s",
                 (incoerente["contact"],))

    modulo["tick"].tick(ctx)
    per_contatto = {e["contact_id"]: e for e in iscrizioni(mondo)}
    assert per_contatto[mai["contact"]]["stop_reason"] == "consent_not_granted"
    assert per_contatto[revocato["contact"]]["stop_reason"] == "consent_revoked"
    assert per_contatto[incoerente["contact"]]["stop_reason"] == "consent_inconsistent"
    assert all(e["status"] == "stopped" for e in per_contatto.values())
    assert messaggi(mondo) == []


def test_05_con_le_automazioni_del_contatto_in_pausa_non_si_iscrive(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx)
    c = mondo["contatto"]()
    modulo["journeys"].pause_automations(ctx, c["contact"])
    assert modulo["tick"].tick(ctx)["enrolled_active"] == 0
    assert iscrizioni(mondo) == []


def test_06_una_sola_iscrizione_aperta_per_contatto_anche_con_due_journey(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, key="prima")
    c = mondo["contatto"]()
    modulo["tick"].tick(ctx)
    # Una seconda journey, e una seconda stima dello STESSO contatto.
    journey_attiva(modulo, ctx, key="seconda")
    mondo["sql"]("INSERT INTO stime (agency_id, comune) VALUES (%s,'Asti')", (mondo["a"],))
    st2 = mondo["sql"]("SELECT max(id) FROM stime")[0][0]
    mondo["sql"](
        """INSERT INTO communication_messages (agency_id, contact_id, stima_id, channel, direction,
             communication_type, mode, reason_code, subject_snapshot, rendered_body,
             destination_snapshot, status, sent_at, actor_type, idempotency_key)
           VALUES (%s,%s,%s,'email','outbound','service','automatic','stima_pdf','S','c','c@x.it',
                   'sent',NOW(),'system',%s)""",
        (mondo["a"], c["contact"], st2, f"stima_email_cliente:{st2}"))

    modulo["tick"].tick(ctx)
    aperte = [e for e in iscrizioni(mondo) if e["status"] in ("active", "paused")]
    assert len(aperte) == 1, aperte


def test_07_un_secondo_giro_non_iscrive_di_nuovo_lo_stesso_trigger(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx)
    mondo["contatto"]()
    primo = modulo["tick"].tick(ctx)
    secondo = modulo["tick"].tick(ctx)
    assert primo["enrolled_active"] == 1 and secondo["enrolled_active"] == 0
    assert len(iscrizioni(mondo)) == 1


# ===========================================================================
# D - AZIONI DOVUTE
# ===========================================================================

def test_08_un_passo_automatico_DOVUTO_diventa_una_riga_in_coda(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    c = mondo["contatto"]()
    conteggi = modulo["tick"].tick(ctx)

    assert conteggi["queued"] == 1, conteggi
    (m,) = messaggi(mondo)
    (e,) = iscrizioni(mondo)
    assert m["status"] == "queued" and m["mode"] == "automatic" and m["reason_code"] == "m1"
    assert (m["enrollment_id"], m["step_no"], m["run_no"]) == (e["id"], 1, 1)
    assert m["destination_snapshot"] == f"c{mondo['n']}@x.it"
    assert m["template_key"] == "registry_probe" and m["template_version"] == 1
    assert "Disiscrizione: https://test.stima360.it/api/public/communication/unsubscribe?t=" \
        in m["rendered_body"]
    # Accodare NON avanza: il passo dopo nasce quando questo e' SPEDITO.
    assert e["next_step_no"] == 1 and e["status"] == "active"


def test_09_un_passo_NON_ancora_dovuto_non_accoda_niente(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=[passo(1, delay=30 * GIORNO.total_seconds())])
    mondo["contatto"]()
    conteggi = modulo["tick"].tick(ctx)
    assert conteggi["enrolled_active"] == 1 and conteggi["queued"] == 0
    assert messaggi(mondo) == []


def test_10_due_giri_non_accodano_due_volte_lo_stesso_passo(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    mondo["contatto"]()
    modulo["tick"].tick(ctx)
    secondo = modulo["tick"].tick(ctx)
    assert secondo["queued"] == 0 and secondo["queued_idempotent"] == 1
    assert len(messaggi(mondo)) == 1


def test_11_un_template_mancante_ferma_QUELLA_iscrizione_e_non_le_altre(mondo, modulo,
                                                                        monkeypatch):
    """Il registro e' la sola autorita': niente ripiego, niente messaggio inventato."""
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    mondo["contatto"]()
    mondo["contatto"]()
    from communication import templates
    vero = templates.get
    chiamate = {"n": 0}

    def guasto(key, version):
        chiamate["n"] += 1
        if chiamate["n"] == 1:
            from communication.exceptions import ValidationError
            raise ValidationError("template non registrato")
        return vero(key, version)

    monkeypatch.setattr(templates, "get", guasto)
    conteggi = modulo["tick"].tick(ctx)
    assert conteggi["errors"] == 1 and conteggi["queued"] == 1, conteggi
    assert len(messaggi(mondo)) == 1
    # Nessun messaggio inventato: quello che manca, manca.
    assert len(iscrizioni(mondo)) == 2


def test_11b_un_errore_SQL_su_una_iscrizione_non_avvelena_il_resto_del_giro(mondo, modulo):
    """Il caso che distingue un savepoint vero da un savepoint finto.

    Un errore Python - un template mancante - non aborte la transazione: la
    si potrebbe riprendere anche senza savepoint. Un errore del DATABASE si':
    da li' in poi ogni comando risponde `InFailedSqlTransaction` finche'
    qualcuno non torna a un punto di ripresa. Qui se ne provoca uno vero -
    un messaggio che occupa gia' la terna `(iscrizione, passo, run)` con
    un'altra chiave di idempotenza, quindi l'indice unico scatta invece
    dell'ON CONFLICT - e si pretende che la seconda iscrizione parta lo
    stesso.
    """
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=[passo(1, delay=30 * GIORNO.total_seconds())])
    primo = mondo["contatto"]()
    mondo["contatto"]()
    modulo["tick"].tick(ctx)
    assert len(iscrizioni(mondo)) == 2
    bloccata = [e for e in iscrizioni(mondo) if e["contact_id"] == primo["contact"]][0]
    mondo["sql"](
        """INSERT INTO communication_messages
               (agency_id, contact_id, stima_id, channel, direction, communication_type, mode,
                reason_code, subject_snapshot, rendered_body, destination_snapshot, status,
                actor_type, idempotency_key, enrollment_id, step_no, run_no)
           VALUES (%s,%s,%s,'email','outbound','marketing','automatic','m1','S','c','c@x.it',
                   'queued','system',%s,%s,1,1)""",
        (mondo["a"], primo["contact"], primo["stima"], "un-altra-chiave", bloccata["id"]))
    mondo["sql"]("UPDATE communication_enrollments SET next_action_at = NOW() - INTERVAL "
                 "'1 minute'")

    conteggi = modulo["tick"].tick(ctx)
    assert conteggi["errors"] == 1 and conteggi["queued"] == 1, conteggi
    # La seconda iscrizione ha il suo messaggio; la prima ha solo quello che
    # c'era gia'. Nessuna riga inventata, nessun giro interrotto.
    assert len(messaggi(mondo)) == 2


# ===========================================================================
# B - AVANZAMENTO
# ===========================================================================

def test_12_un_passo_SPEDITO_fa_nascere_il_successivo_dal_suo_sent_at(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=[passo(1, delay=0),
                                       passo(2, delay=ORA_SEC, delay_from="previous_step_sent")])
    mondo["contatto"]()
    modulo["tick"].tick(ctx)
    (m,) = messaggi(mondo)
    spedito_alle = datetime.now(timezone.utc) - timedelta(minutes=10)
    segna_spedito(mondo, m["id"], spedito_alle)

    conteggi = modulo["tick"].tick(ctx)
    assert conteggi["advanced"] == 1, conteggi
    (e,) = iscrizioni(mondo)
    assert e["next_step_no"] == 2 and e["next_action_kind"] == "enqueue"
    # La base e' il `sent_at` del passo precedente, non l'ora del tick.
    assert abs((e["next_action_at"] - spedito_alle).total_seconds() - ORA_SEC) < 2


def test_13_l_ultimo_passo_spedito_completa_l_iscrizione(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    mondo["contatto"]()
    modulo["tick"].tick(ctx)
    segna_spedito(mondo, messaggi(mondo)[0]["id"])

    conteggi = modulo["tick"].tick(ctx)
    assert conteggi["completed"] == 1, conteggi
    (e,) = iscrizioni(mondo)
    assert e["status"] == "completed" and e["completed_at"] is not None
    assert e["next_step_no"] is None and e["next_action_at"] is None


def test_14_un_passo_ancora_in_coda_non_fa_avanzare_niente(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    mondo["contatto"]()
    modulo["tick"].tick(ctx)
    conteggi = modulo["tick"].tick(ctx)
    assert conteggi["advanced"] == 0 and conteggi["completed"] == 0
    assert iscrizioni(mondo)[0]["next_step_no"] == 1


def test_15_il_ritardo_dal_TRIGGER_si_misura_dal_trigger_anche_al_secondo_passo(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=[passo(1, delay=0),
                                       passo(2, delay=2 * GIORNO.total_seconds(),
                                             delay_from="trigger")])
    mondo["contatto"]()
    modulo["tick"].tick(ctx)
    segna_spedito(mondo, messaggi(mondo)[0]["id"])
    modulo["tick"].tick(ctx)
    (e,) = iscrizioni(mondo)
    assert e["next_action_at"] - e["trigger_sent_at"] == 2 * GIORNO


# ===========================================================================
# ASSISTED: il passo che aspetta una persona
# ===========================================================================

PASSI_ASSISTITI = [passo(1, mode="assisted", delay=0), passo(2, delay=ORA_SEC,
                                                             delay_from="previous_step_sent")]


def _iscritto_assistito(mondo, modulo, ctx, *, passi=None):
    journey_attiva(modulo, ctx, passi=passi if passi is not None else PASSI_ASSISTITI)
    c = mondo["contatto"]()
    modulo["tick"].tick(ctx)
    (e,) = iscrizioni(mondo)
    return c, e


def test_16_un_passo_assistito_dovuto_NON_crea_un_messaggio_ma_un_attesa(mondo, modulo):
    ctx = mondo["ctx"]()
    _, e = _iscritto_assistito(mondo, modulo, ctx)
    assert e["next_action_kind"] == "await_operator" and e["awaiting_since"] is not None
    assert messaggi(mondo) == []
    # E il giro successivo continua a contarlo come in attesa, senza accodare.
    conteggi = modulo["tick"].tick(ctx)
    assert conteggi["awaiting_operator"] == 1 and conteggi["queued"] == 0
    assert messaggi(mondo) == []


def test_17_send_current_crea_il_messaggio_assistito_e_lo_firma_la_sessione(mondo, modulo):
    ctx = mondo["ctx"]()
    _, e = _iscritto_assistito(mondo, modulo, ctx)
    esito = modulo["tick"].send_current(ctx, e["id"])

    assert esito["created"] is True
    (m,) = messaggi(mondo)
    assert m["mode"] == "assisted" and m["status"] == "queued"
    assert (m["enrollment_id"], m["step_no"], m["run_no"]) == (e["id"], 1, 1)
    assert m["actor_type"] == "operator" and m["actor_user_id"] == mondo["admin"]["id"]
    # L'iscrizione non aspetta piu' nessuno: ora aspetta il ledger.
    dopo = iscrizioni(mondo)[0]
    assert dopo["next_action_kind"] == "enqueue" and dopo["awaiting_since"] is None


def test_18_due_click_producono_UN_messaggio_solo(mondo, modulo):
    ctx = mondo["ctx"]()
    _, e = _iscritto_assistito(mondo, modulo, ctx)
    primo = modulo["tick"].send_current(ctx, e["id"])
    secondo = modulo["tick"].send_current(ctx, e["id"])
    assert primo["created"] is True and secondo["created"] is False
    assert primo["message"]["id"] == secondo["message"]["id"]
    assert len(messaggi(mondo)) == 1


def test_19_send_current_rifiuta_un_passo_automatico(mondo, modulo):
    from communication.exceptions import ConflictError
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=[passo(1, delay=30 * GIORNO.total_seconds())])
    mondo["contatto"]()
    modulo["tick"].tick(ctx)
    (e,) = iscrizioni(mondo)
    with pytest.raises(ConflictError) as info:
        modulo["tick"].send_current(ctx, e["id"])
    assert "automatic" in str(info.value)
    assert messaggi(mondo) == []


def test_20_send_current_rifiuta_un_passo_non_ancora_dovuto(mondo, modulo):
    from communication.exceptions import ConflictError
    ctx = mondo["ctx"]()
    _, e = _iscritto_assistito(
        mondo, modulo, ctx,
        passi=[passo(1, mode="assisted", delay=7 * GIORNO.total_seconds())])
    with pytest.raises(ConflictError) as info:
        modulo["tick"].send_current(ctx, e["id"])
    assert "not due yet" in str(info.value)


def test_21_skip_salta_il_passo_e_riparte_da_ADESSO(mondo, modulo):
    ctx = mondo["ctx"]()
    _, e = _iscritto_assistito(mondo, modulo, ctx)
    prima = datetime.now(timezone.utc)
    esito = modulo["tick"].skip_current(ctx, e["id"])

    assert esito["skipped_step_no"] == 1 and esito["completed"] is False
    dopo = iscrizioni(mondo)[0]
    assert dopo["next_step_no"] == 2 and dopo["next_action_kind"] == "enqueue"
    # La base e' lo SKIP, non il trigger: un'ora da adesso, non da ieri.
    assert abs((dopo["next_action_at"] - prima).total_seconds() - ORA_SEC) < 5
    assert messaggi(mondo) == []
    (evento,) = [r for r in mondo["righe"]("seller_timeline_events")
                 if r["event_type"] == "journey_step_skipped"]
    assert evento["payload"]["step_key"] == "M1"
    assert evento["created_by"] == str(mondo["admin"]["id"])


def test_22_skip_dell_ultimo_passo_completa(mondo, modulo):
    ctx = mondo["ctx"]()
    _, e = _iscritto_assistito(mondo, modulo, ctx,
                               passi=[passo(1, mode="assisted", delay=0)])
    esito = modulo["tick"].skip_current(ctx, e["id"])
    assert esito["completed"] is True
    assert iscrizioni(mondo)[0]["status"] == "completed"


def test_23_skip_e_send_current_pretendono_una_persona(mondo, modulo):
    """Un contesto di sistema non fa atti da operatore, nemmeno questi."""
    # La classe si prende da DOVE VIENE SOLLEVATA e non da `core.exceptions`:
    # in una suite intera un altro modulo puo' aver ricaricato quel modulo, e
    # due classi con lo stesso nome non sono la stessa classe. `pytest.raises`
    # confronta identita', non nomi.
    from communication.journey_service import PermissionDenied
    from operator_auth.context import SystemAgencyContext
    ctx = mondo["ctx"]()
    _, e = _iscritto_assistito(mondo, modulo, ctx)
    sistema = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
    for azione in (modulo["tick"].send_current, modulo["tick"].skip_current):
        with pytest.raises(PermissionDenied):
            azione(sistema, e["id"])
    assert messaggi(mondo) == []


# ===========================================================================
# A - GLI STOP AUTOREVOLI
# ===========================================================================

def acquisizione(mondo, c, *, mandato=False, revocata=False):
    """Un incarico nel registro della 070, con il suo immobile."""
    mondo["sql"]("INSERT INTO properties (agency_id) VALUES (%s)", (c["agency"],))
    prop = mondo["sql"]("SELECT max(id) FROM properties")[0][0]
    mondo["sql"](
        """INSERT INTO stima_acquisitions
               (stima_id, stima_id_snapshot, property_id, link_status, linked_by_operator_user_id,
                mandate_signed_at, mandate_recorded_at, mandate_recorded_by_operator_user_id,
                revoked_at, revoked_by_operator_user_id, revoked_reason)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (c["stima"], c["stima"], prop, "revoked" if revocata else "active",
         mondo["admin"]["id"],
         datetime.now(timezone.utc) if mandato else None,
         datetime.now(timezone.utc) if mandato else None,
         mondo["admin"]["id"] if mandato else None,
         datetime.now(timezone.utc) if revocata else None,
         mondo["admin"]["id"] if revocata else None,
         "cambio idea" if revocata else None))


def sopralluogo(mondo, c, *, stato="scheduled"):
    mondo["sql"](
        """INSERT INTO stima_inspections (stima_id, stima_id_snapshot, status, scheduled_for,
               completed_at, completed_recorded_at, completed_by_operator_user_id,
               cancelled_at, cancelled_recorded_at, cancelled_by_operator_user_id,
               cancelled_reason, created_by_operator_user_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (c["stima"], c["stima"], stato,
         datetime.now(timezone.utc) if stato != "completed" else None,
         datetime.now(timezone.utc) if stato == "completed" else None,
         datetime.now(timezone.utc) if stato == "completed" else None,
         mondo["admin"]["id"] if stato == "completed" else None,
         datetime.now(timezone.utc) if stato == "cancelled" else None,
         datetime.now(timezone.utc) if stato == "cancelled" else None,
         mondo["admin"]["id"] if stato == "cancelled" else None,
         "rimandato" if stato == "cancelled" else None,
         mondo["admin"]["id"]))


def evento(mondo, c, tipo, *, chiave=None):
    mondo["sql"](
        "INSERT INTO seller_timeline_events (agency_id, contact_id, stima_id, event_type, "
        "event_source, idempotency_key) VALUES (%s,%s,%s,%s,'crm_acquisition',%s)",
        (c["agency"], c["contact"], c["stima"], tipo, chiave or f"{tipo}:{c['stima']}"))


def _iscritto(mondo, modulo, ctx, *, passi=None):
    journey_attiva(modulo, ctx, passi=passi if passi is not None else
                   [passo(1, delay=30 * GIORNO.total_seconds())])
    c = mondo["contatto"]()
    modulo["tick"].tick(ctx)
    (e,) = iscrizioni(mondo)
    return c, e


@pytest.mark.parametrize("fatto,atteso", [
    ("mandato", "mandate_signed"),
    ("acquisizione", "acquisition_linked"),
    ("sopralluogo", "inspection"),
    ("sopralluogo_fatto", "inspection"),
    ("consulenza", "consultation_requested"),
    ("lead_chiuso", "lead_closed"),
    ("contatto_inattivo", "contact_inactive"),
    ("contatto_archiviato", "contact_inactive"),
    ("consenso_revocato", "consent_revoked"),
])
def test_24_ogni_fatto_autorevole_ferma_l_iscrizione(mondo, modulo, fatto, atteso):
    ctx = mondo["ctx"]()
    c, e = _iscritto(mondo, modulo, ctx)
    if fatto == "mandato":
        acquisizione(mondo, c, mandato=True)
    elif fatto == "acquisizione":
        acquisizione(mondo, c)
    elif fatto == "sopralluogo":
        sopralluogo(mondo, c)
    elif fatto == "sopralluogo_fatto":
        sopralluogo(mondo, c, stato="completed")
    elif fatto == "consulenza":
        evento(mondo, c, "owner_consultation_requested")
    elif fatto == "lead_chiuso":
        mondo["sql"]("UPDATE leads SET status = 'closed' WHERE id = %s", (c["lead"],))
    elif fatto == "contatto_inattivo":
        mondo["sql"]("UPDATE contacts SET status = 'inactive' WHERE id = %s", (c["contact"],))
    elif fatto == "contatto_archiviato":
        mondo["sql"]("UPDATE contacts SET status = 'archived' WHERE id = %s", (c["contact"],))
    else:
        mondo["sql"]("INSERT INTO consent_events (agency_id, contact_id, purpose, decision, "
                     "decided_at, source, actor_type) VALUES (%s,%s,'marketing','revoked',NOW(),"
                     "'crm','subject')", (mondo["a"], c["contact"]))
        mondo["sql"]("UPDATE contacts SET marketing_consent = FALSE, marketing_revoked_at = NOW() "
                     "WHERE id = %s", (c["contact"],))

    conteggi = modulo["tick"].tick(ctx)
    assert conteggi["stopped"] == 1, conteggi
    dopo = iscrizioni(mondo)[0]
    assert dopo["status"] == "stopped" and dopo["stop_reason"] == atteso
    assert dopo["next_step_no"] is None and dopo["stopped_by_operator_user_id"] is None


def test_25_un_incarico_REVOCATO_e_un_sopralluogo_ANNULLATO_non_fermano_niente(mondo, modulo):
    """Il fatto che ferma e' quello IN CORSO: cio' che e' stato annullato e'
    esattamente il caso in cui la journey deve continuare a parlare."""
    ctx = mondo["ctx"]()
    c, _ = _iscritto(mondo, modulo, ctx)
    acquisizione(mondo, c, mandato=True, revocata=True)
    sopralluogo(mondo, c, stato="cancelled")
    assert modulo["tick"].tick(ctx)["stopped"] == 0
    assert iscrizioni(mondo)[0]["status"] == "active"


def test_26_quando_piu_fatti_sono_veri_vince_la_priorita(mondo, modulo):
    """L'ordine delle query non deve contare: conta `STOP_PRIORITY`."""
    ctx = mondo["ctx"]()
    c, _ = _iscritto(mondo, modulo, ctx)
    acquisizione(mondo, c, mandato=True)       # 1 mandate_signed
    sopralluogo(mondo, c)                      # 3 inspection
    evento(mondo, c, "owner_consultation_requested")  # 4
    mondo["sql"]("UPDATE leads SET status = 'closed' WHERE id = %s", (c["lead"],))  # 5
    mondo["sql"]("UPDATE contacts SET status = 'inactive' WHERE id = %s", (c["contact"],))  # 6

    modulo["tick"].tick(ctx)
    assert iscrizioni(mondo)[0]["stop_reason"] == "mandate_signed"


def test_27_lo_stop_porta_l_evento_autorevole_quando_esiste(mondo, modulo):
    ctx = mondo["ctx"]()
    c, _ = _iscritto(mondo, modulo, ctx)
    acquisizione(mondo, c, mandato=True)
    evento(mondo, c, "mandate_signed")
    modulo["tick"].tick(ctx)
    dopo = iscrizioni(mondo)[0]
    (ev,) = [r for r in mondo["righe"]("seller_timeline_events")
             if r["event_type"] == "mandate_signed"]
    assert dopo["stop_event_id"] == ev["id"]


def test_28_senza_evento_compatibile_lo_stop_non_ne_inventa_uno(mondo, modulo):
    ctx = mondo["ctx"]()
    c, _ = _iscritto(mondo, modulo, ctx)
    sopralluogo(mondo, c)
    modulo["tick"].tick(ctx)
    dopo = iscrizioni(mondo)[0]
    assert dopo["stop_reason"] == "inspection" and dopo["stop_event_id"] is None


def test_29_lo_stop_cancella_il_messaggio_ancora_in_coda(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    c = mondo["contatto"]()
    modulo["tick"].tick(ctx)
    (m,) = messaggi(mondo)
    assert m["status"] == "queued"

    acquisizione(mondo, c, mandato=True)
    modulo["tick"].tick(ctx)
    (dopo,) = messaggi(mondo)
    assert dopo["status"] == "cancelled"
    assert dopo["metadata"]["cancel_reason"] == "stopped:mandate_signed"
    assert iscrizioni(mondo)[0]["status"] == "stopped"


def test_30_un_messaggio_gia_SPEDITO_non_torna_indietro_per_uno_stop(mondo, modulo):
    """Lo stop ferma il futuro, non riscrive il passato: il ledger e' immutabile."""
    ctx = mondo["ctx"]()
    # Il trigger deve essere POSTERIORE all'attivazione (il cutoff di §C),
    # quindi il primo passo si fa dovuto con un ritardo nullo invece che con
    # una mail vecchia: la storia non si iscrive, e questo test non prova il
    # cutoff ma l'immutabilita' del ledger.
    journey_attiva(modulo, ctx, passi=[passo(1, delay=0), passo(2, delay=ORA_SEC,
                                                                delay_from="previous_step_sent")])
    c = mondo["contatto"]()
    modulo["tick"].tick(ctx)
    (m,) = messaggi(mondo)
    segna_spedito(mondo, m["id"])
    acquisizione(mondo, c, mandato=True)
    modulo["tick"].tick(ctx)
    assert messaggi(mondo)[0]["status"] == "sent"
    assert iscrizioni(mondo)[0]["status"] == "stopped"


# ===========================================================================
# LA TIMELINE DELL'INVIO
# ===========================================================================

def _finalizza_come_il_dispatcher(mondo, modulo, message_id):
    """Il `sent` con il suo seguito, nella STESSA transazione.

    Si chiama la funzione vera - `integrations.dopo_invio` - sul cursore che
    scrive lo stato, che e' esattamente cio' che fa il dispatcher nel ramo
    `sent`. Nessun doppio: il ramo e' quello di produzione.
    """
    from communication import integrations
    conn = modulo["connessione"]()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE communication_messages SET status = 'sent', sent_at = NOW() "
                        "WHERE id = %s RETURNING *", (message_id,))
            riga = dict(cur.fetchone())
            integrations.dopo_invio(cur, riga)
        conn.commit()
    finally:
        conn.close()
    return riga


def test_31_un_passo_spedito_scrive_la_timeline_con_payload_minimo(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    c = mondo["contatto"]()
    modulo["tick"].tick(ctx)
    (m,) = messaggi(mondo)
    _finalizza_come_il_dispatcher(mondo, modulo, m["id"])

    eventi = [r for r in mondo["righe"]("seller_timeline_events")
              if r["event_type"] == "journey_message_sent"]
    assert len(eventi) == 1, eventi
    (ev,) = eventi
    assert set(ev["payload"]) == {"journey_key", "journey_version", "step_key", "message_id"}
    assert ev["payload"] == {"journey_key": "stima_lead", "journey_version": 1,
                             "step_key": "M1", "message_id": m["id"]}
    assert ev["stima_id"] == c["stima"] and ev["contact_id"] == c["contact"]
    assert ev["idempotency_key"] == f"journey_message_sent:{m['id']}"


def test_32_la_timeline_non_contiene_PII_ne_il_testo_del_messaggio(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    c = mondo["contatto"]()
    modulo["tick"].tick(ctx)
    (m,) = messaggi(mondo)
    _finalizza_come_il_dispatcher(mondo, modulo, m["id"])

    (ev,) = [r for r in mondo["righe"]("seller_timeline_events")
             if r["event_type"] == "journey_message_sent"]
    testo = repr(ev).lower()
    for vietato in (m["destination_snapshot"].lower(), "disiscrizione", "unsubscribe",
                    m["rendered_body"][:20].lower(), m["subject_snapshot"].lower()):
        assert vietato not in testo, vietato


def test_33_finalizzare_due_volte_lascia_UN_evento_solo(mondo, modulo):
    """Idempotenza deterministica: la chiave e' il messaggio, non l'istante."""
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    mondo["contatto"]()
    modulo["tick"].tick(ctx)
    (m,) = messaggi(mondo)
    _finalizza_come_il_dispatcher(mondo, modulo, m["id"])
    _finalizza_come_il_dispatcher(mondo, modulo, m["id"])
    assert len([r for r in mondo["righe"]("seller_timeline_events")
                if r["event_type"] == "journey_message_sent"]) == 1


def test_34_una_mail_di_stima_NON_scrive_l_evento_di_journey(mondo, modulo):
    """I due hook non si incontrano mai: il trigger non e' un passo."""
    from communication import integrations
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    c = mondo["contatto"]()
    riga = mondo["righe"]("communication_messages", "id = %s", (c["trigger"],))[0]
    riga["metadata"] = {"pdf_url": "https://example.it/x.pdf"}
    conn = modulo["connessione"]()
    try:
        with conn.cursor() as cur:
            integrations.dopo_invio(cur, riga)
        conn.commit()
    finally:
        conn.close()
    tipi = {r["event_type"] for r in mondo["righe"]("seller_timeline_events")}
    assert "journey_message_sent" not in tipi and "email_stima_inviata" in tipi


# ===========================================================================
# LA FINESTRA DI INVIO, applicata dal motore
# ===========================================================================

def test_35_la_finestra_sposta_il_passo_dentro_l_orario_della_journey(mondo, modulo):
    from zoneinfo import ZoneInfo
    ctx = mondo["ctx"]()
    finestra = {"days": [1, 2, 3, 4, 5], "from": "09:00", "to": "19:00"}
    journey_attiva(modulo, ctx, passi=[passo(1, delay=0, finestra=finestra)],
                   tz="Europe/Rome")
    mondo["contatto"]()
    modulo["tick"].tick(ctx)

    (e,) = iscrizioni(mondo)
    locale = e["next_action_at"].astimezone(ZoneInfo("Europe/Rome"))
    assert locale.isoweekday() in (1, 2, 3, 4, 5), locale
    assert 9 <= locale.hour < 19, locale
    assert e["next_action_at"] >= e["trigger_sent_at"]


# ===========================================================================
# TENANCY E SCOPE
# ===========================================================================

def test_36_un_giro_di_un_agenzia_non_tocca_l_altra(mondo, modulo):
    ctx_a = mondo["ctx"]()
    ctx_b = Ctx(mondo["b"], user_id=mondo["admin_b"]["id"], role="agency_admin")
    journey_attiva(modulo, ctx_a, passi=PASSI_UNO)
    mondo["contatto"](mondo["a"])
    mondo["contatto"](mondo["b"])

    conteggi_b = modulo["tick"].tick(ctx_b)
    assert conteggi_b == {k: 0 for k in conteggi_b}, conteggi_b
    assert iscrizioni(mondo) == []

    modulo["tick"].tick(ctx_a)
    (e,) = iscrizioni(mondo)
    assert e["agency_id"] == mondo["a"]


def test_37_un_operatore_dell_altra_agenzia_non_vede_l_iscrizione(mondo, modulo):
    from communication.exceptions import NotFoundError
    ctx_a = mondo["ctx"]()
    _, e = _iscritto_assistito(mondo, modulo, ctx_a)
    ctx_b = Ctx(mondo["b"], user_id=mondo["admin_b"]["id"], role="agency_admin")
    for azione in (modulo["tick"].send_current, modulo["tick"].skip_current):
        with pytest.raises(NotFoundError):
            azione(ctx_b, e["id"])
    assert messaggi(mondo) == []


def test_38_un_agente_agisce_solo_sui_PROPRI_contatti(mondo, modulo):
    from communication.exceptions import NotFoundError
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=[passo(1, mode="assisted", delay=0)])
    altrui = mondo["contatto"]()
    mio = mondo["contatto"](assegnato=mondo["agente"]["id"])
    modulo["tick"].tick(ctx)
    per_contatto = {e["contact_id"]: e for e in iscrizioni(mondo)}

    agente = Ctx(mondo["a"], user_id=mondo["agente"]["id"], role="agent")
    with pytest.raises(NotFoundError):
        modulo["tick"].send_current(agente, per_contatto[altrui["contact"]]["id"])
    esito = modulo["tick"].send_current(agente, per_contatto[mio["contact"]]["id"])
    assert esito["created"] is True
    assert len(messaggi(mondo)) == 1


def test_39_il_platform_admin_in_acting_agisce_il_platform_admin_slegato_no(mondo, modulo):
    from operator_auth.exceptions import PlatformAdminAgencyRequired
    ctx = mondo["ctx"]()
    _, e = _iscritto_assistito(mondo, modulo, ctx)

    acting = Ctx(mondo["a"], user_id=mondo["piattaforma"]["id"], role=None,
                 is_platform_admin=True)
    slegato = Ctx(None, user_id=mondo["piattaforma"]["id"], role=None, is_platform_admin=True)
    with pytest.raises(PlatformAdminAgencyRequired):
        modulo["tick"].send_current(slegato, e["id"])
    assert messaggi(mondo) == []
    assert modulo["tick"].send_current(acting, e["id"])["created"] is True
    with pytest.raises(PlatformAdminAgencyRequired):
        modulo["tick"].tick(slegato)


# ===========================================================================
# CONCORRENZA
#
# Con thread e connessioni VERE: e' l'unico modo di far accadere davvero due
# giri insieme. Cio' che si prova non e' che il lock funzioni - quello lo
# garantisce PostgreSQL - ma che il motore, quando perde una corsa, NON
# produca un doppione e non si pianti.
# ===========================================================================

def _in_parallelo(azione, n=2, timeout=40):
    import threading

    barriera = threading.Barrier(n, timeout=timeout)
    esiti, errori = [], []

    def corpo():
        try:
            barriera.wait()
            esiti.append(azione())
        except Exception as exc:  # pragma: no cover - solo se il motore si rompe
            errori.append(exc)

    fili = [threading.Thread(target=corpo, daemon=True) for _ in range(n)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout)
    assert not [f for f in fili if f.is_alive()], "un giro concorrente non e' tornato"
    return esiti, errori


def test_40_due_giri_simultanei_non_iscrivono_due_volte(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=[passo(1, delay=30 * GIORNO.total_seconds())])
    mondo["contatto"]()

    esiti, errori = _in_parallelo(lambda: modulo["tick"].tick(ctx))
    assert errori == [], errori
    assert len(iscrizioni(mondo)) == 1
    assert sum(e["enrolled_active"] for e in esiti) == 1, esiti


def test_41_due_giri_simultanei_non_accodano_due_volte(mondo, modulo):
    ctx = mondo["ctx"]()
    # L'iscrizione nasce con il passo NON ancora dovuto (nessun messaggio), e
    # poi lo si fa scadere: e' l'unico modo di avere due giri che trovano lo
    # stesso passo dovuto insieme. Cancellare il messaggio del primo giro non
    # sarebbe possibile - il ledger rifiuta la DELETE, ed e' giusto cosi'.
    journey_attiva(modulo, ctx, passi=[passo(1, delay=30 * GIORNO.total_seconds())])
    mondo["contatto"]()
    modulo["tick"].tick(ctx)
    assert messaggi(mondo) == []
    mondo["sql"]("UPDATE communication_enrollments SET next_action_at = NOW() - INTERVAL "
                 "'1 minute'")

    esiti, errori = _in_parallelo(lambda: modulo["tick"].tick(ctx))
    assert errori == [], errori
    assert len(messaggi(mondo)) == 1
    assert sum(e["queued"] for e in esiti) == 1, esiti


def test_42_due_click_simultanei_su_send_current_producono_un_messaggio(mondo, modulo):
    ctx = mondo["ctx"]()
    _, e = _iscritto_assistito(mondo, modulo, ctx)

    esiti, errori = _in_parallelo(lambda: modulo["tick"].send_current(ctx, e["id"]))
    assert errori == [], errori
    assert len(messaggi(mondo)) == 1
    assert sorted(x["created"] for x in esiti) == [False, True], esiti


def test_43_stop_e_enqueue_in_corsa_non_lasciano_un_messaggio_vivo(mondo, modulo):
    """I due ordini possibili, entrambi deterministici.

    `FOR UPDATE SKIP LOCKED` fa si' che due giri non lavorino mai la stessa
    iscrizione NELLO STESSO istante: uno dei due la salta. Quindi la domanda
    vera non e' "cosa succede se accadono insieme" - non accade - ma "cosa
    resta dopo, in ciascuno dei due ordini". La risposta deve essere la
    stessa: se lo stop e' avvenuto, non c'e' un messaggio vivo.
    """
    ctx = mondo["ctx"]()

    # ORDINE 1: prima accoda, poi ferma.
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    c = mondo["contatto"]()
    modulo["tick"].tick(ctx)
    assert messaggi(mondo)[0]["status"] == "queued"
    acquisizione(mondo, c, mandato=True)
    modulo["tick"].tick(ctx)
    vivi = [m for m in messaggi(mondo) if m["status"] not in ("cancelled", "sent")]
    assert vivi == [], vivi
    assert iscrizioni(mondo)[0]["status"] == "stopped"

    # ORDINE 2: il fatto esiste GIA' quando l'iscrizione nasce.
    #
    # Era il limite dell'ordine A-B-C-D, ed e' chiuso da P29-3C: la fase
    # STOP gira prima di ENROLL e non puo' vedere cio' che ENROLL crea,
    # quindi e' la NASCITA a guardare gli stessi fatti. L'iscrizione nasce
    # gia' ferma, nello stesso giro, e nessun messaggio esiste - nemmeno
    # cancellato, perche' nemmeno accodato.
    c2 = mondo["contatto"]()
    acquisizione(mondo, c2, mandato=True)
    modulo["tick"].tick(ctx)
    iscrizione2 = [e for e in iscrizioni(mondo) if e["contact_id"] == c2["contact"]][0]
    assert iscrizione2["status"] == "stopped"
    assert iscrizione2["stop_reason"] == "mandate_signed"
    assert messaggi(mondo, iscrizione2["id"]) == []


def test_44_uno_stop_concorrente_a_un_enqueue_gia_committato_lo_cancella(mondo, modulo):
    """Le due transazioni si toccano davvero: l'enqueue tiene il lock mentre
    lo stop prova a prendere la stessa riga."""
    from psycopg2.extras import DictCursor
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    c = mondo["contatto"]()
    modulo["tick"].tick(ctx)          # iscritta e accodata
    acquisizione(mondo, c, mandato=True)

    conn_a = modulo["connessione"]()
    conn_b = modulo["connessione"]()
    try:
        with conn_a.cursor(cursor_factory=DictCursor) as ca:
            # A blocca l'iscrizione (fase DUE) e NON committa.
            from communication import journey_repository as repo
            repo.lock_due_enrollments(ca, ctx, kind="enqueue",
                                      now=datetime.now(timezone.utc), limit=10)
            with conn_b.cursor(cursor_factory=DictCursor) as cb:
                # B la SALTA: e' bloccata. Nessuno stop, nessun errore.
                conteggi = modulo["tick"]._conteggi()
                modulo["tick"]._fase_stop(ctx, cb, datetime.now(timezone.utc), 10, conteggi)
                assert conteggi["stopped"] == 0
            conn_b.commit()
        conn_a.commit()
    finally:
        conn_a.close()
        conn_b.close()

    # Il giro successivo trova la riga libera e la ferma, cancellando la coda.
    modulo["tick"].tick(ctx)
    assert iscrizioni(mondo)[0]["status"] == "stopped"
    assert [m["status"] for m in messaggi(mondo)] == ["cancelled"]


# ===========================================================================
# DEPLOY PRIMA DELLA MIGRATION
# ===========================================================================

def test_45_senza_la_071_il_resto_di_P29_continua_a_funzionare(mondo, modulo):
    from operator_auth.context import SystemAgencyContext
    ctx = mondo["ctx"]()
    c = mondo["contatto"]()
    mondo["sql"](_migrazione(f"{VERSIONE}_down"))
    try:
        assert mondo["sql"]("SELECT to_regclass('public.communication_enrollments')")[0][0] is None
        sistema = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
        esito = modulo["comm"].enqueue(
            sistema, contact_id=c["contact"], channel="email", communication_type="service",
            mode="automatic", reason_code="operator_manual", rendered_body="corpo",
            subject_snapshot="s", destination_snapshot="c@x.it",
            idempotency_key="senza-071", stima_id=c["stima"])
        assert esito["created"] and esito["message"]["status"] == "queued"
    finally:
        mondo["sql"]("ROLLBACK")
        mondo["sql"](_migrazione(VERSIONE))


def test_46_senza_la_071_il_motore_dice_non_migrato_e_non_UndefinedTable(mondo, modulo):
    ctx = mondo["ctx"]()
    mondo["sql"](_migrazione(f"{VERSIONE}_down"))
    try:
        for azione in (lambda: modulo["tick"].tick(ctx),
                       lambda: modulo["tick"].send_current(ctx, 1),
                       lambda: modulo["tick"].skip_current(ctx, 1)):
            with pytest.raises(modulo["tick"].FeatureNotMigrated) as info:
                azione()
            assert "071" in str(info.value)
    finally:
        mondo["sql"]("ROLLBACK")
        mondo["sql"](_migrazione(VERSIONE))


# ===========================================================================
# LA CATENA VIA HTTP: app vera, login vero, cookie vero
#
# Niente `dependency_overrides`, niente contesto costruito a mano: la matrice
# di autorizzazione del tick si misura cambiando SOLO il ruolo di chi si
# autentica, come in P29-2.6E OPS.
# ===========================================================================

@pytest.fixture
def client(mondo, modulo, monkeypatch):
    """L'app VERA, con i router montati come in produzione."""
    import tests.conftest  # noqa: F401  (installa lo shim quando serve)
    import main as main_module
    from communication.providers import email_smtp

    # NESSUNA EMAIL PARTE DA UN TEST, mai, nemmeno per sbaglio: il trasporto
    # reale e' sostituito da una spia prima che l'app esista. Il motore non
    # spedisce - accoda - ma la rotta di dispatch e' montata nella stessa app
    # e un giorno un test potrebbe farla girare con righe in coda.
    inviate: list[tuple] = []
    monkeypatch.setattr(email_smtp, "invia_mail",
                        lambda *a, **k: (inviate.append((a, k)), True)[1])

    # COLLISIONE SEGNALATA, NON MODIFICATA (come in P29-2.6E OPS):
    # `tests/test_p26_6c_backend_gate_closure.py` lascia installato un
    # `dependency_overrides[legacy_basic_agency_context]` che deciderebbe
    # l'agenzia al posto della sessione. Qui lo si mette da parte per la
    # durata del test e lo si rimette identico: fuori di qui la suite resta
    # quella di prima.
    override_altrui = dict(main_module.app.dependency_overrides)
    main_module.app.dependency_overrides.clear()
    try:
        # HTTPS: il cookie di sessione e' `Secure` senza condizioni, e su
        # `http://testserver` non tornerebbe indietro.
        with TestClient(main_module.app, base_url="https://testserver") as c:
            yield c
    finally:
        main_module.app.dependency_overrides.clear()
        main_module.app.dependency_overrides.update(override_altrui)


def accedi(client, mondo, chi):
    client.cookies.clear()
    risposta = client.post("/api/operator-auth/login",
                           json={"email": mondo[chi]["email"], "password": PASSWORD})
    assert risposta.status_code in (200, 204), risposta.text
    return risposta


def entra_in_acting(mondo, chi, agenzia=None):
    """Il platform admin entra in una agenzia: e' uno STATO nel database
    (P28), non un parametro di chiamata, e si prepara come tale."""
    mondo["sql"](
        "UPDATE operator_sessions SET acting_agency_id = %s, acting_entered_at = NOW() "
        "WHERE operator_user_id = %s AND revoked_at IS NULL",
        (agenzia if agenzia is not None else mondo["a"], mondo[chi]["id"]))


def test_47_le_tre_rotte_sono_montate_nell_app_vera(mondo):
    import tests.conftest  # noqa: F401
    import main as main_module
    percorsi = sorted(p for p in main_module.app.openapi()["paths"] if "journeys" in p)
    # SENTINELLA AGGIORNATA DA P29-3D: il Contact 360 aggiunge le azioni
    # dell'operatore su una iscrizione (pausa, ripresa, stop) e la gestione
    # della sequenza (provision, activate, retire). Le tre di P29-3C restano
    # dove erano: l'elenco cresce, non cambia.
    assert percorsi == [
        "/api/communication/journeys/enrollments/{enrollment_id}/pause",
        "/api/communication/journeys/enrollments/{enrollment_id}/resume",
        "/api/communication/journeys/enrollments/{enrollment_id}/send-current",
        "/api/communication/journeys/enrollments/{enrollment_id}/skip-current",
        "/api/communication/journeys/enrollments/{enrollment_id}/stop",
        "/api/communication/journeys/stima-lead/provision",
        "/api/communication/journeys/tick",
        "/api/communication/journeys/{journey_id}/activate",
        "/api/communication/journeys/{journey_id}/retire",
    ], percorsi


def test_48_senza_sessione_il_tick_rifiuta(client, mondo):
    risposta = client.post("/api/communication/journeys/tick", json={})
    assert risposta.status_code == 401, risposta.text


def test_49_un_agente_non_puo_far_girare_il_motore(client, mondo, modulo):
    """Un giro tocca TUTTE le iscrizioni dell'agenzia: chi non vede tutti i
    record non lo fa partire. Stessa riga di matrice del dispatch."""
    accedi(client, mondo, "agente")
    risposta = client.post("/api/communication/journeys/tick", json={})
    assert risposta.status_code == 403, risposta.text
    assert iscrizioni(mondo) == []


@pytest.mark.parametrize("chi", ["admin", "owner"])
def test_50_admin_e_owner_fanno_girare_il_motore(client, mondo, modulo, chi):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    mondo["contatto"]()

    accedi(client, mondo, chi)
    risposta = client.post("/api/communication/journeys/tick", json={})
    assert risposta.status_code == 200, risposta.text
    corpo = risposta.json()
    assert corpo["enrolled_active"] == 1 and corpo["queued"] == 1, corpo
    assert len(iscrizioni(mondo)) == 1 and len(messaggi(mondo)) == 1


def test_51_il_platform_admin_slegato_prende_403_e_in_acting_gira(client, mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    mondo["contatto"]()

    accedi(client, mondo, "piattaforma")
    slegato = client.post("/api/communication/journeys/tick", json={})
    assert slegato.status_code == 403, slegato.text
    assert iscrizioni(mondo) == []

    entra_in_acting(mondo, "piattaforma")
    in_acting = client.post("/api/communication/journeys/tick", json={})
    assert in_acting.status_code == 200, in_acting.text
    assert in_acting.json()["enrolled_active"] == 1
    assert iscrizioni(mondo)[0]["agency_id"] == mondo["a"]


def test_52_il_corpo_non_accetta_un_agency_id(client, mondo):
    accedi(client, mondo, "admin")
    risposta = client.post("/api/communication/journeys/tick",
                           json={"agency_id": 999, "limit": 10})
    assert risposta.status_code == 422, risposta.text


def test_53_send_current_via_http_e_lo_scope_dell_agente(client, mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=[passo(1, mode="assisted", delay=0)])
    altrui = mondo["contatto"]()
    mio = mondo["contatto"](assegnato=mondo["agente"]["id"])
    modulo["tick"].tick(ctx)
    per_contatto = {e["contact_id"]: e["id"] for e in iscrizioni(mondo)}

    accedi(client, mondo, "agente")
    negato = client.post(
        f"/api/communication/journeys/enrollments/{per_contatto[altrui['contact']]}/send-current")
    assert negato.status_code == 404, negato.text
    concesso = client.post(
        f"/api/communication/journeys/enrollments/{per_contatto[mio['contact']]}/send-current")
    assert concesso.status_code == 200, concesso.text
    assert concesso.json()["created"] is True
    # E il secondo click, via HTTP, non crea una seconda riga.
    ancora = client.post(
        f"/api/communication/journeys/enrollments/{per_contatto[mio['contact']]}/send-current")
    assert ancora.status_code == 200 and ancora.json()["created"] is False
    assert len(messaggi(mondo)) == 1


def test_54_skip_current_via_http(client, mondo, modulo):
    ctx = mondo["ctx"]()
    _, e = _iscritto_assistito(mondo, modulo, ctx)
    accedi(client, mondo, "admin")
    risposta = client.post(
        f"/api/communication/journeys/enrollments/{e['id']}/skip-current")
    assert risposta.status_code == 200, risposta.text
    assert risposta.json()["skipped_step_no"] == 1
    assert messaggi(mondo) == []


def test_55_senza_la_071_le_rotte_journey_dicono_503_e_le_altre_no(client, mondo, modulo):
    """Il deploy puo' precedere la migration: in quella finestra queste tre
    rotte dicono "non ancora" con un codice macchina, e nessuna delle altre
    cambia comportamento."""
    accedi(client, mondo, "admin")
    mondo["sql"](_migrazione(f"{VERSIONE}_down"))
    try:
        for percorso in ("/api/communication/journeys/tick",
                         "/api/communication/journeys/enrollments/1/send-current",
                         "/api/communication/journeys/enrollments/1/skip-current"):
            risposta = client.post(percorso, json={} if percorso.endswith("tick") else None)
            assert risposta.status_code == 503, (percorso, risposta.status_code, risposta.text)
            assert risposta.json()["detail"]["code"] == "feature_not_migrated"
        # La rotta di dispatch, che non conosce le journey, risponde come sempre.
        dispatch = client.post("/api/communication/dispatch", json={"channel": "email"})
        assert dispatch.status_code == 200, dispatch.text
    finally:
        mondo["sql"]("ROLLBACK")
        mondo["sql"](_migrazione(VERSIONE))


def test_56_la_lettura_in_blocco_del_consenso_da_le_STESSE_risposte(mondo, modulo):
    """La sentinella strutturale dice che la decisione e' una sola; questa
    lo misura sui dati, stato per stato."""
    from consent.guard import can_send_marketing, can_send_marketing_bulk
    ctx = mondo["ctx"]()
    concesso = mondo["contatto"]()
    mai = mondo["contatto"](consenso=False)
    revocato = mondo["contatto"]()
    incoerente = mondo["contatto"]()
    mondo["sql"]("INSERT INTO consent_events (agency_id, contact_id, purpose, decision, "
                 "decided_at, source, actor_type) VALUES (%s,%s,'marketing','revoked',NOW(),"
                 "'crm','subject')", (mondo["a"], revocato["contact"]))
    mondo["sql"]("UPDATE contacts SET marketing_consent = FALSE, marketing_revoked_at = NOW() "
                 "WHERE id = %s", (revocato["contact"],))
    mondo["sql"]("UPDATE contacts SET marketing_consent = FALSE WHERE id = %s",
                 (incoerente["contact"],))

    identificativi = [c["contact"] for c in (concesso, mai, revocato, incoerente)]
    in_blocco = can_send_marketing_bulk(ctx, identificativi)
    assert set(in_blocco) == set(identificativi)
    for cid in identificativi:
        una = can_send_marketing(ctx, cid)
        molte = in_blocco[cid]
        assert (una.allowed, una.reason, una.state) == (molte.allowed, molte.reason, molte.state)
    assert {d.reason for d in in_blocco.values()} == {
        "allow_explicit_grant", "deny_never_given", "deny_revoked", "deny_inconsistent_state"}


def test_57_la_lettura_in_blocco_non_esce_dallo_scope(mondo, modulo):
    """Un contatto di un'altra agenzia semplicemente non compare: la versione
    singola solleverebbe, e qui l'assenza e' la risposta."""
    from consent.guard import can_send_marketing_bulk
    ctx = mondo["ctx"]()
    mio = mondo["contatto"](mondo["a"])
    altrui = mondo["contatto"](mondo["b"])
    esito = can_send_marketing_bulk(ctx, [mio["contact"], altrui["contact"]])
    assert set(esito) == {mio["contact"]}


def test_58_un_agente_in_blocco_vede_solo_i_propri_contatti(mondo, modulo):
    from consent.guard import can_send_marketing_bulk
    mio = mondo["contatto"](assegnato=mondo["agente"]["id"])
    altrui = mondo["contatto"]()
    agente = Ctx(mondo["a"], user_id=mondo["agente"]["id"], role="agent")
    esito = can_send_marketing_bulk(agente, [mio["contact"], altrui["contact"]])
    assert set(esito) == {mio["contact"]}


# ===========================================================================
# REGRESSIONE: STOP GIA' PRESENTE PRIMA DELLA NASCITA DELL'ISCRIZIONE
# ===========================================================================

def _stop_pre_enrollment(mondo, c, fatto):
    """Fatti reali nelle stesse tabelle usate dai test dello STOP ordinario."""
    if fatto == "mandate_signed":
        acquisizione(mondo, c, mandato=True)
    elif fatto == "acquisition_linked":
        acquisizione(mondo, c)
    elif fatto == "inspection":
        sopralluogo(mondo, c)
    elif fatto == "consultation_requested":
        evento(mondo, c, "owner_consultation_requested")
    elif fatto == "lead_closed":
        mondo["sql"]("UPDATE leads SET status = 'closed' WHERE id = %s", (c["lead"],))
    elif fatto == "contact_inactive":
        mondo["sql"]("UPDATE contacts SET status = 'inactive' WHERE id = %s", (c["contact"],))
    elif fatto == "consent_revoked":
        mondo["sql"](
            "INSERT INTO consent_events (agency_id, contact_id, purpose, decision, "
            "decided_at, source, actor_type) VALUES (%s,%s,'marketing','revoked',NOW(),"
            "'crm','subject')", (c["agency"], c["contact"]))
        mondo["sql"]("UPDATE contacts SET marketing_consent = FALSE, marketing_revoked_at = NOW() "
                     "WHERE id = %s", (c["contact"],))
    elif fatto == "consent_inconsistent":
        mondo["sql"]("UPDATE contacts SET marketing_consent = FALSE WHERE id = %s",
                     (c["contact"],))
    else:
        raise AssertionError(f"fatto di prova sconosciuto: {fatto}")


def _scrittore_reale(mondo, modulo, c, fatto):
    """Il fatto di stop scritto da CHI LO SCRIVE IN PRODUZIONE.

    I test di stop ordinari inseriscono le righe a mano, e va bene: li'
    conta cosa c'e' nel database. Qui conta CHI lo mette, perche' la meta'
    del contratto che si sta provando e' che quegli scrittori prendano il
    fence. Con una INSERT grezza il blocco arriverebbe comunque - la chiave
    esterna verso `stime` prende un lock da sola - e il test passerebbe
    anche con i domini non coordinati: e' esattamente l'errore che una
    neutralizzazione ha fatto emergere.

    La consulenza passa da `seller_intelligence.record_event_scoped` con
    `lock_stima=True`, che e' la riga che il portale proprietario chiama
    (LMC-9 aggiunge solo il controllo del grant, che qui non e' in prova).
    """
    from acquisition import repository as acquisizione_repo
    from consent import service as consenso_service
    from seller_intelligence import service as si_service

    agenzia = c["agency"]
    ctx = Ctx(agenzia, user_id=mondo["admin"]["id"], role="agency_admin")

    if fatto in ("mandate_signed", "acquisition_linked"):
        with mondo["conn"].cursor() as cur:
            cur.execute("INSERT INTO properties (agency_id) VALUES (%s) RETURNING id", (agenzia,))
            immobile = cur.fetchone()[0]
        link = acquisizione_repo.create_acquisition_link(
            agenzia, stima_id=c["stima"], property_id=immobile,
            actor_user_id=mondo["admin"]["id"])
        if fatto == "mandate_signed":
            acquisizione_repo.record_mandate(
                agenzia, acquisition_id=link["id"],
                signed_at=datetime.now(timezone.utc), reference="RIF-1",
                actor_user_id=mondo["admin"]["id"])
    elif fatto == "inspection":
        acquisizione_repo.create_inspection(
            agenzia, stima_id=c["stima"], scheduled_for=datetime.now(timezone.utc),
            actor_user_id=mondo["admin"]["id"])
    elif fatto == "consultation_requested":
        si_service.record_event_scoped(
            ctx, stima_id=c["stima"], contact_id=c["contact"],
            event_type="owner_consultation_requested", event_source="owner_portal",
            payload={"action": "consultation_requested"},
            idempotency_key=f"owner_portal:consultation:{c['stima']}",
            lock_stima=True)
    elif fatto == "consent_revoked":
        consenso_service.record_revocation(
            ctx, contact_id=c["contact"], purpose="marketing", source="crm",
            actor_type="operator", actor_ref=str(mondo["admin"]["id"]),
            idempotency_key=f"revoca:{c['contact']}")
    elif fatto in ("lead_closed", "contact_inactive"):
        # Il CRM li scrive con una UPDATE sulla riga: il lock di riga e'
        # gia' quello del contratto, e non c'e' niente da aggiungere.
        conn = modulo["connessione"]()
        try:
            with conn:
                with conn.cursor() as cur:
                    if fatto == "lead_closed":
                        cur.execute("UPDATE leads SET status = 'closed' WHERE id = %s",
                                    (c["lead"],))
                    else:
                        cur.execute("UPDATE contacts SET status = 'inactive' WHERE id = %s",
                                    (c["contact"],))
        finally:
            conn.close()
    else:
        raise AssertionError(f"scrittore reale non previsto per {fatto}")


def _assert_nata_stopped(mondo, atteso):
    (e,) = iscrizioni(mondo)
    assert e["status"] == "stopped", e
    assert e["stop_reason"] == atteso
    assert e["stopped_at"] is not None
    assert all(e[k] is None for k in
               ("next_step_no", "next_action_at", "next_action_kind", "awaiting_since"))
    assert messaggi(mondo) == []  # Nessuna riga journey, neppure gia' cancellata.
    return e


@pytest.mark.parametrize("fatto", [
    "mandate_signed", "acquisition_linked", "inspection", "consultation_requested",
    "lead_closed", "contact_inactive",
])
def test_59_stop_pre_enrollment_ogni_fatto_blocca_M1_nello_stesso_tick(mondo, modulo, fatto):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    c = mondo["contatto"]()
    _stop_pre_enrollment(mondo, c, fatto)
    assert iscrizioni(mondo) == []

    esito = modulo["tick"].tick(ctx)

    _assert_nata_stopped(mondo, fatto)
    assert esito["enrolled_stopped"] == 1 and esito["enrolled_active"] == 0
    assert esito["stopped"] == 0  # Non e' una seconda fase STOP a salvarci.
    assert esito["queued"] == 0 and esito["errors"] == 0


def test_60_stop_pre_enrollment_tick_completo_non_rende_M1_inviabile(mondo, modulo):
    """Sentinella principale: senza il controllo in ENROLL, M1 e' gia' queued."""
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    c = mondo["contatto"]()
    acquisizione(mondo, c, mandato=True)
    evento(mondo, c, "mandate_signed")
    (autorevole,) = mondo["righe"]("seller_timeline_events", "event_type = 'mandate_signed'")
    assert iscrizioni(mondo) == []
    assert mondo["righe"]("communication_messages", "id = %s", (c["trigger"],))[0]["status"] == "sent"

    esito = modulo["tick"].tick(ctx)

    # Prima i messaggi: neutralizzare ENROLL deve fallire proprio sull'invio possibile.
    assert messaggi(mondo) == [], "M1 non deve esistere dopo STOP -> ADVANCE -> ENROLL -> DUE"
    e = _assert_nata_stopped(mondo, "mandate_signed")
    assert e["stop_event_id"] == autorevole["id"]
    assert esito["enrolled_stopped"] == 1 and esito["queued"] == 0
    assert esito["stopped"] == 0 and esito["errors"] == 0
    assert mondo["righe"]("seller_timeline_events", "event_type = 'journey_stopped'")
    modulo["tick"].tick(ctx)
    assert _assert_nata_stopped(mondo, "mandate_signed")["id"] == e["id"]


@pytest.mark.parametrize("inverti", [False, True])
@pytest.mark.parametrize("fatti,atteso", [
    (("mandate_signed", "consultation_requested", "consent_revoked"), "mandate_signed"),
    (("acquisition_linked", "inspection", "consent_inconsistent"), "acquisition_linked"),
    (("inspection", "consultation_requested", "consent_revoked"), "inspection"),
    (("consultation_requested", "lead_closed", "consent_revoked"), "consultation_requested"),
    (("lead_closed", "contact_inactive", "consent_revoked"), "lead_closed"),
    (("contact_inactive", "consent_inconsistent"), "contact_inactive"),
])
def test_61_stop_pre_enrollment_priorita_indipendente_dall_ordine(
        mondo, modulo, monkeypatch, fatti, atteso, inverti):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    c = mondo["contatto"]()
    for fatto in reversed(fatti) if inverti else fatti:
        _stop_pre_enrollment(mondo, c, fatto)
    eventi_veri = modulo["repo"].stop_events

    def eventi_riordinati(*a, **kw):
        righe = list(eventi_veri(*a, **kw).items())
        return dict(reversed(righe) if inverti else righe)

    monkeypatch.setattr(modulo["repo"], "stop_events", eventi_riordinati)
    modulo["tick"].tick(ctx)
    _assert_nata_stopped(mondo, atteso)


def test_62_stop_pre_enrollment_allow_e_fatti_annullati_lasciano_M1_attivo(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    c = mondo["contatto"]()
    acquisizione(mondo, c, mandato=True, revocata=True)
    sopralluogo(mondo, c, stato="cancelled")
    esito = modulo["tick"].tick(ctx)
    assert esito["enrolled_active"] == 1 and esito["queued"] == 1
    assert iscrizioni(mondo)[0]["status"] == "active"
    (m,) = messaggi(mondo)
    assert m["status"] == "queued" and m["reason_code"] == "m1"


@pytest.mark.parametrize("ragione", [
    "consent_not_granted", "consent_revoked", "consent_inconsistent",
])
def test_63_stop_pre_enrollment_consenso_conserva_le_tre_ragioni(mondo, modulo, ragione):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    c = mondo["contatto"](consenso=ragione != "consent_not_granted")
    if ragione != "consent_not_granted":
        _stop_pre_enrollment(mondo, c, ragione)
    esito = modulo["tick"].tick(ctx)
    e = _assert_nata_stopped(mondo, ragione)
    assert e["stop_event_id"] is None
    assert esito["enrolled_stopped"] == 1 and esito["errors"] == 0


def test_64_stop_pre_enrollment_evento_non_compatibile_non_viene_attribuito(mondo, modulo):
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    c = mondo["contatto"]()
    acquisizione(mondo, c, mandato=True)
    evento(mondo, c, "owner_consultation_requested")
    modulo["tick"].tick(ctx)
    assert _assert_nata_stopped(mondo, "mandate_signed")["stop_event_id"] is None


def test_65_stop_pre_enrollment_due_tick_concorrenti_una_stopped_zero_M1(
        mondo, modulo, monkeypatch):
    import threading

    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    c = mondo["contatto"]()
    acquisizione(mondo, c, mandato=True)
    candidati_veri = modulo["repo"].candidate_triggers
    barriera = threading.Barrier(2, timeout=20)
    letture = []

    def candidati_concorrenti(*a, **kw):
        righe = candidati_veri(*a, **kw)
        letture.append([r["trigger_message_id"] for r in righe])
        barriera.wait()  # Entrambi hanno letto PRIMA di qualunque INSERT.
        return righe

    monkeypatch.setattr(modulo["repo"], "candidate_triggers", candidati_concorrenti)
    esiti, errori = _in_parallelo(lambda: modulo["tick"].tick(ctx))
    assert errori == [], errori
    assert letture == [[c["trigger"]], [c["trigger"]]]
    _assert_nata_stopped(mondo, "mandate_signed")
    assert sum(e["enrolled_stopped"] for e in esiti) == 1
    assert all(e["queued"] == e["errors"] == 0 for e in esiti)


def test_66_le_query_degli_stop_non_crescono_con_l_universo_ne_a_sei_per_riga(
        mondo, modulo, monkeypatch):
    """Il conto vero delle statement, su due giri di dimensione diversa.

    Il mandato distingue due cose che qui si misurano separatamente:

      * la fase STOP guarda TUTTE le iscrizioni aperte, e lo fa in blocco:
        il numero di query per famiglia non deve cambiare quando l'universo
        passa da una iscrizione a dodici;
      * il fence e la rilettura fresca avvengono SOLO per una riga che sta
        per essere scritta, e per ciascuna sono UNA statement di fatti piu'
        UNA del consenso - non le sei del vecchio motore, e non sei per
        candidato.

    Si contano le query reali, compresa la connessione separata che il
    dominio del consenso apre per conto suo.
    """
    from psycopg2.extras import RealDictCursor
    from communication import database as comm_db
    from consent import database as consent_db

    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    misura = {"sql": []}

    class CursoreContatore(RealDictCursor):
        def execute(self, query, vars=None):
            misura["sql"].append(" ".join(str(query).lower().split()))
            return super().execute(query, vars)

    monkeypatch.setattr(comm_db, "RealDictCursor", CursoreContatore)
    monkeypatch.setattr(consent_db, "RealDictCursor", CursoreContatore)

    def conta(sql):
        fresche = [q for q in sql if "with prio(reason, rank)" in q]
        consenso = [q for q in sql if "consent_events" in q and "insert" not in q]
        # La lettura del consenso nomina anch'essa `contacts ... = ANY(...)`:
        # va tolta dal conto del blocco, o si conterebbe due volte.
        blocco = [q for q in sql if "= any(" in q and q not in consenso and any(
            tabella in q for tabella in ("stima_acquisitions", "stima_inspections",
                                         "from leads", "from contacts", "seller_timeline_events"))]
        return {"fresche": len(fresche), "consenso": len(consenso), "blocco": len(blocco)}

    misure = []
    for quanti in (1, 12):
        for _ in range(quanti):
            acquisizione(mondo, mondo["contatto"](), mandato=True)
        misura["sql"].clear()
        esito = modulo["tick"].tick(ctx)
        assert esito["enrolled_stopped"] == quanti and esito["errors"] == 0, esito
        misure.append(conta(misura["sql"]))

    uno, dodici = misure
    # UNA statement fresca per riga scritta, e una sola lettura di consenso
    # per riga: due, non sei, e non sei per candidato.
    assert uno["fresche"] == 1 and dodici["fresche"] == 12, misure
    assert uno["consenso"] == 1 and dodici["consenso"] == 12, misure
    # La fase STOP resta in blocco: l'universo passa da 0 a 1 iscrizione
    # aperta e le sue query per famiglia non si moltiplicano.
    assert dodici["blocco"] == uno["blocco"], misure
    assert dodici["blocco"] <= 8, misure

    assert len(iscrizioni(mondo)) == 13
    assert all(e["status"] == "stopped" for e in iscrizioni(mondo))
    assert messaggi(mondo) == []


class _Sequenza:
    """Una barriera in DUE tempi attorno al FENCE del motore.

    `prima` sospende l'azione del motore un istante PRIMA che prenda il
    fence; `dopo` la sospende un istante DOPO averlo preso e prima che
    committi. Sono i due soli momenti che il contratto distingue, e sono i
    due casi che questi test devono provare separatamente.
    """

    def __init__(self, modulo, monkeypatch, *, quando: str):
        import threading
        self.eventi = threading.Event(), threading.Event()   # (arrivato, riprendi)
        self.quando = quando
        self.originale = modulo["repo"].fence
        self.chiamate = 0
        monkeypatch.setattr(modulo["repo"], "fence", self._fence)

    def _fence(self, *a, **kw):
        arrivato, riprendi = self.eventi
        self.chiamate += 1
        primo = self.chiamate == 1
        if primo and self.quando == "prima":
            arrivato.set()
            assert riprendi.wait(25), "lo scrittore dello stop non ha finito"
        risultato = self.originale(*a, **kw)
        if primo and self.quando == "dopo":
            arrivato.set()
            assert riprendi.wait(25), "il test non ha rilasciato il motore"
        return risultato


def _in_parallelo_con_stop(mondo, modulo, monkeypatch, *, quando, azione, contatto, fatto,
                           attesa_stop=None):
    """Esegue l'azione del motore e lo scrittore dello stop, davvero insieme.

    Restituisce `(esito_azione, bloccato)`, dove `bloccato` dice se lo
    scrittore dello stop ha dovuto ASPETTARE il commit del motore - che e'
    l'osservabile del caso B, e non un dettaglio di tempistica: senza fence
    passerebbe subito.
    """
    import threading

    sequenza = _Sequenza(modulo, monkeypatch, quando=quando)
    arrivato, riprendi = sequenza.eventi
    esiti, errori = [], []

    def motore():
        try:
            esiti.append(azione())
        except Exception as exc:
            import traceback
            exc.tracciato = traceback.format_exc()
            errori.append(exc)

    filo = threading.Thread(target=motore, daemon=True)
    filo.start()
    # Se il motore muore prima del fence, la ragione e' la SUA eccezione:
    # aspettare venticinque secondi e poi dire "non e' arrivato" nasconde
    # l'unica informazione utile.
    for _ in range(250):
        if arrivato.wait(0.1) or errori:
            break
    assert errori == [], [getattr(e, "tracciato", repr(e)) for e in errori]
    assert arrivato.is_set(), "il motore non ha raggiunto il fence"

    scritto = threading.Event()
    guasti = []

    def scrittore():
        conn = modulo["connessione"]()
        try:
            with conn:
                with conn.cursor() as cur:
                    pass
            _scrittore_reale(mondo, modulo, contatto, fatto)
            scritto.set()
        except Exception as exc:  # pragma: no cover
            guasti.append(exc)
            scritto.set()
        finally:
            conn.close()

    filo_b = threading.Thread(target=scrittore, daemon=True)
    filo_b.start()
    # Nel caso "dopo" il motore tiene il fence: lo scrittore NON deve
    # riuscire a committare finche' non lo rilasciamo. Un'attesa breve che
    # scade e' proprio la prova che il lock morde.
    bloccato = not scritto.wait(3 if quando == "dopo" else 25)
    riprendi.set()
    filo.join(30)
    filo_b.join(30)
    assert not filo.is_alive() and not filo_b.is_alive(), "un filo e' rimasto appeso"
    assert guasti == [], guasti
    if attesa_stop is None:
        assert errori == [], errori
    return (esiti[0] if esiti else errori[0]), bloccato


# --- CASO A: lo stop ha committato PRIMA del fence -> lo stop vince ---------

@pytest.mark.parametrize("fatto", [
    "mandate_signed", "acquisition_linked", "inspection", "consultation_requested",
    "lead_closed", "contact_inactive", "consent_revoked",
])
def test_67_R1_caso_A_lo_stop_committato_prima_del_fence_vince_sulla_nascita(
        mondo, modulo, monkeypatch, fatto):
    """Nuova iscrizione. Il fatto atterra mentre il motore sta per recintare:
    la lettura fresca e' DOPO il lock, quindi lo vede."""
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=PASSI_UNO)
    c = mondo["contatto"]()
    esito, _ = _in_parallelo_con_stop(
        mondo, modulo, monkeypatch, quando="prima",
        azione=lambda: modulo["tick"].tick(ctx), contatto=c, fatto=fatto)
    _assert_nata_stopped(mondo, fatto)
    assert esito["enrolled_stopped"] == 1 and esito["enrolled_active"] == 0
    assert esito["queued"] == 0 and esito["errors"] == 0


@pytest.mark.parametrize("fatto", ["mandate_signed", "contact_inactive", "consent_revoked"])
def test_68_R2_caso_A_lo_stop_committato_prima_del_fence_vince_sull_enqueue(
        mondo, modulo, monkeypatch, fatto):
    """Passo automatico dovuto. Nessun messaggio deve nascere."""
    ctx = mondo["ctx"]()
    c, e = _iscritto(mondo, modulo, ctx)
    mondo["sql"]("UPDATE communication_enrollments SET next_action_at = NOW() WHERE id = %s",
                 (e["id"],))
    esito, _ = _in_parallelo_con_stop(
        mondo, modulo, monkeypatch, quando="prima",
        azione=lambda: modulo["tick"].tick(ctx), contatto=c, fatto=fatto)
    assert _assert_nata_stopped(mondo, fatto)["id"] == e["id"]
    assert esito["stopped"] == 1 and esito["queued"] == 0 and esito["errors"] == 0


def test_69_R3_caso_A_lo_stop_committato_prima_del_fence_vince_sull_assistito(
        client, mondo, modulo, monkeypatch):
    """Invio assistito via HTTP: il click arriva dopo lo stop e viene rifiutato."""
    ctx = mondo["ctx"]()
    c, e = _iscritto(mondo, modulo, ctx, passi=[passo(1, mode="assisted", delay=0)])
    assert e["next_action_kind"] == "await_operator"

    esito, _ = _in_parallelo_con_stop(
        mondo, modulo, monkeypatch, quando="prima",
        azione=lambda: modulo["tick"].send_current(ctx, e["id"]),
        contatto=c, fatto="inspection")
    # Il service TORNA l'esito invece di sollevare: sollevare annullerebbe
    # con il rollback proprio lo stop appena deciso.
    assert esito["stopped"] == "inspection" and esito["created"] is False
    assert esito["message"] is None
    assert _assert_nata_stopped(mondo, "inspection")["id"] == e["id"]


# --- CASO B: il fence e' del motore -> lo stop ASPETTA ---------------------

def test_70_R1_caso_B_il_fence_del_motore_fa_aspettare_lo_scrittore(
        mondo, modulo, monkeypatch):
    """Il motore ha recintato per primo: l'iscrizione nasce `active`, e
    l'incarico si registra solo DOPO il commit del motore.

    `bloccato` non e' una misura di lentezza: senza il fence sullo scrittore
    quella scrittura passerebbe subito, e il motore avrebbe deciso su un
    mondo gia' cambiato."""
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, passi=[passo(1, delay=30 * GIORNO.total_seconds())])
    c = mondo["contatto"]()
    esito, bloccato = _in_parallelo_con_stop(
        mondo, modulo, monkeypatch, quando="dopo",
        azione=lambda: modulo["tick"].tick(ctx), contatto=c, fatto="mandate_signed")
    assert bloccato, "lo scrittore dello stop NON ha aspettato il fence"
    assert esito["enrolled_active"] == 1 and esito["enrolled_stopped"] == 0
    (e,) = iscrizioni(mondo)
    assert e["status"] == "active"
    # E il fatto c'e' davvero: ha solo aspettato il suo turno.
    assert mondo["righe"]("stima_acquisitions")[0]["mandate_signed_at"] is not None
    # Il giro successivo lo vede e ferma l'iscrizione: nessun messaggio nato
    # nel frattempo, perche' il passo non era dovuto.
    modulo["tick"].tick(ctx)
    assert iscrizioni(mondo)[0]["status"] == "stopped"
    assert messaggi(mondo) == []


def test_71_R2_caso_B_il_fence_del_motore_protegge_l_enqueue(mondo, modulo, monkeypatch):
    """Il passo parte, e lo stop entra dopo: il messaggio esiste - la
    decisione era vera al suo istante - e il giro successivo lo cancella."""
    ctx = mondo["ctx"]()
    c, e = _iscritto(mondo, modulo, ctx)
    mondo["sql"]("UPDATE communication_enrollments SET next_action_at = NOW() WHERE id = %s",
                 (e["id"],))
    esito, bloccato = _in_parallelo_con_stop(
        mondo, modulo, monkeypatch, quando="dopo",
        azione=lambda: modulo["tick"].tick(ctx), contatto=c, fatto="inspection")
    assert bloccato, "lo scrittore dello stop NON ha aspettato il fence"
    assert esito["queued"] == 1 and esito["stopped"] == 0
    assert [m["status"] for m in messaggi(mondo)] == ["queued"]

    modulo["tick"].tick(ctx)
    assert iscrizioni(mondo)[0]["status"] == "stopped"
    assert [m["status"] for m in messaggi(mondo)] == ["cancelled"]


def test_72_R3_caso_B_il_fence_del_motore_protegge_l_invio_assistito(
        mondo, modulo, monkeypatch):
    ctx = mondo["ctx"]()
    c, e = _iscritto(mondo, modulo, ctx, passi=[passo(1, mode="assisted", delay=0)])
    esito, bloccato = _in_parallelo_con_stop(
        mondo, modulo, monkeypatch, quando="dopo",
        azione=lambda: modulo["tick"].send_current(ctx, e["id"]),
        contatto=c, fatto="mandate_signed")
    assert bloccato, "lo scrittore dello stop NON ha aspettato il fence"
    assert esito["created"] is True
    assert [m["status"] for m in messaggi(mondo)] == ["queued"]
    assert mondo["righe"]("stima_acquisitions")[0]["mandate_signed_at"] is not None


def test_73_gli_scrittori_degli_stop_coordinano_sullo_stesso_oggetto(mondo, modulo):
    """La meta' del contratto che sta FUORI dal motore, verificata sul vero
    lock manager: mentre il motore tiene il fence, ciascuno scrittore di
    stop aspetta. `pg_blocking_pids` lo dice senza cronometri."""
    import threading
    from psycopg2.extras import DictCursor

    ctx = mondo["ctx"]()
    c = mondo["contatto"]()
    conn_fence = modulo["connessione"]()
    try:
        with conn_fence.cursor(cursor_factory=DictCursor) as cur:
            modulo["repo"].fence(cur, ctx, contact_id=c["contact"], lead_id=c["lead"],
                                 stima_id=c["stima"])

            for fatto in ("mandate_signed", "inspection", "consultation_requested",
                          "lead_closed", "contact_inactive", "consent_revoked"):
                partito, finito = threading.Event(), threading.Event()

                def scrittore(fatto=fatto):
                    partito.set()
                    _scrittore_reale(mondo, modulo, c, fatto)
                    finito.set()

                filo = threading.Thread(target=scrittore, daemon=True)
                filo.start()
                assert partito.wait(10)
                assert not finito.wait(2), f"{fatto}: ha scritto senza rispettare il fence"
                conn_fence.rollback()
                assert finito.wait(15), f"{fatto}: non e' ripartito dopo il rilascio"
                filo.join(10)
                # Si riprende il fence per il fatto successivo, e SOLO con
                # `fence`: un lock preso a mano qui mascherebbe un fence
                # che non prende piu' quella riga.
                modulo["repo"].fence(cur, ctx, contact_id=c["contact"], lead_id=c["lead"],
                                     stima_id=c["stima"])
    finally:
        conn_fence.rollback()
        conn_fence.close()


def test_74_anche_gli_scrittori_di_SOLA_UPDATE_rispettano_il_fence(mondo, modulo):
    """IL CASO IN CUI IL FENCE E' L'UNICA PROTEZIONE.

    Una INSERT che referenzia `stime` prende da sola un lock sulla riga
    padre (`FOR KEY SHARE`, per la chiave esterna): link e sopralluoghi
    NUOVI si coordinerebbero con il motore anche senza che nessuno abbia
    scritto una riga di codice. Le UPDATE su righe gia' esistenti no - la
    chiave esterna non viene rivalidata - e sono esattamente queste:

        registrare il MANDATO su un link che c'e' gia'
        CHIUDERE un sopralluogo gia' fissato
        REVOCARE un link attivo

    Senza il `FOR UPDATE` esplicito aggiunto da P29-3C passerebbero mentre
    il motore sta decidendo. Questo test e' il solo posto in cui quella
    riga di codice e' osservabile, ed e' il motivo per cui esiste.
    """
    import threading
    from psycopg2.extras import DictCursor
    from acquisition import repository as acquisizione_repo

    ctx = mondo["ctx"]()
    c = mondo["contatto"]()
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO properties (agency_id) VALUES (%s) RETURNING id", (mondo["a"],))
        immobile = cur.fetchone()[0]
    link = acquisizione_repo.create_acquisition_link(
        mondo["a"], stima_id=c["stima"], property_id=immobile,
        actor_user_id=mondo["admin"]["id"])
    sopralluogo_riga = acquisizione_repo.create_inspection(
        mondo["a"], stima_id=c["stima"], scheduled_for=datetime.now(timezone.utc),
        actor_user_id=mondo["admin"]["id"])

    atti = {
        "record_mandate": lambda: acquisizione_repo.record_mandate(
            mondo["a"], acquisition_id=link["id"], signed_at=datetime.now(timezone.utc),
            reference="RIF-2", actor_user_id=mondo["admin"]["id"]),
        "complete_inspection": lambda: acquisizione_repo.complete_inspection(
            mondo["a"], inspection_id=sopralluogo_riga["id"],
            completed_at=datetime.now(timezone.utc), actor_user_id=mondo["admin"]["id"]),
        "revoke_acquisition_link": lambda: acquisizione_repo.revoke_acquisition_link(
            mondo["a"], acquisition_id=link["id"], reason="cambio idea",
            actor_user_id=mondo["admin"]["id"]),
    }

    conn_fence = modulo["connessione"]()
    try:
        with conn_fence.cursor(cursor_factory=DictCursor) as cur:
            for nome, atto in atti.items():
                modulo["repo"].fence(cur, ctx, contact_id=c["contact"], lead_id=c["lead"],
                                     stima_id=c["stima"])
                partito, finito = threading.Event(), threading.Event()
                guasti = []

                def scrittore(atto=atto):
                    partito.set()
                    try:
                        atto()
                    except Exception as exc:  # pragma: no cover
                        guasti.append(exc)
                    finito.set()

                filo = threading.Thread(target=scrittore, daemon=True)
                filo.start()
                assert partito.wait(10)
                assert not finito.wait(2), f"{nome}: ha scritto senza rispettare il fence"
                conn_fence.rollback()
                assert finito.wait(15), f"{nome}: non e' ripartito dopo il rilascio"
                filo.join(10)
                assert guasti == [], (nome, guasti)
    finally:
        conn_fence.rollback()
        conn_fence.close()




def test_75_ogni_scrittore_di_stop_prende_la_stima_PRIMA_di_scrivere(mondo, modulo,
                                                                     monkeypatch):
    """Il fence degli scrittori, misurato per quello che e'.

    ONESTA' SU COSA PROVA E COSA NO. Oggi ognuno di questi scrittori scrive
    anche una riga di `seller_timeline_events` che referenzia `stime`, e la
    chiave esterna prende da sola un lock sulla riga padre: il
    coordinamento con il motore ci sarebbe anche senza il `FOR UPDATE`
    esplicito, e infatti una neutralizzazione che lo toglie NON fa fallire
    i test di blocco. Il `FOR UPDATE` resta per tre ragioni, e questa
    sentinella protegge la terza:

      1. prende il lock PRIMA di scrivere, invece che a meta' del lavoro;
      2. dichiara l'intenzione invece di dipendere da un effetto collaterale
         della chiave esterna;
      3. sopravvive al giorno in cui uno di questi scrittori smettera' di
         proiettare un evento - e quel giorno, senza questa riga, il
         coordinamento sparirebbe in silenzio.

    Si misura la SEQUENZA delle statement: la prima cosa che ciascuno fa
    deve essere il lock sulla stima.
    """
    from psycopg2.extras import RealDictCursor
    from acquisition import repository as acquisizione_repo
    from core import database as core_db

    c = mondo["contatto"]()
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO properties (agency_id) VALUES (%s) RETURNING id", (mondo["a"],))
        immobile = cur.fetchone()[0]

    registro = {"sql": []}

    class CursoreTracciante(RealDictCursor):
        def execute(self, query, vars=None):
            registro["sql"].append(" ".join(str(query).lower().split()))
            return super().execute(query, vars)

    # Si scrive nello spazio globale del `core_cursor` CHE ACQUISITION USA:
    # nella suite intera non e' detto che sia quello di `core.database` in
    # `sys.modules` (vedi la nota nella fixture `modulo`).
    monkeypatch.setattr(core_db, "RealDictCursor", CursoreTracciante)
    globali = getattr(acquisizione_repo.core_cursor, "__wrapped__",
                      acquisizione_repo.core_cursor).__globals__
    monkeypatch.setitem(globali, "RealDictCursor", CursoreTracciante)

    def prima_statement(azione):
        registro["sql"].clear()
        esito = azione()
        assert registro["sql"], "nessuna query osservata"
        prima = registro["sql"][0]
        assert "from stime" in prima and "for update" in prima, prima
        return esito

    link = prima_statement(lambda: acquisizione_repo.create_acquisition_link(
        mondo["a"], stima_id=c["stima"], property_id=immobile,
        actor_user_id=mondo["admin"]["id"]))
    ispezione = prima_statement(lambda: acquisizione_repo.create_inspection(
        mondo["a"], stima_id=c["stima"], scheduled_for=datetime.now(timezone.utc),
        actor_user_id=mondo["admin"]["id"]))
    # Per gli atti su una riga esistente il lock arriva dopo la SOLA lettura
    # che risolve quale stima sia: e' la prima scrittura a dover trovarlo
    # gia' preso.
    for azione in (
        lambda: acquisizione_repo.record_mandate(
            mondo["a"], acquisition_id=link["id"], signed_at=datetime.now(timezone.utc),
            reference="RIF-3", actor_user_id=mondo["admin"]["id"]),
        lambda: acquisizione_repo.complete_inspection(
            mondo["a"], inspection_id=ispezione["id"],
            completed_at=datetime.now(timezone.utc), actor_user_id=mondo["admin"]["id"]),
        lambda: acquisizione_repo.revoke_acquisition_link(
            mondo["a"], acquisition_id=link["id"], reason="cambio idea",
            actor_user_id=mondo["admin"]["id"]),
    ):
        registro["sql"].clear()
        azione()
        lock = [i for i, q in enumerate(registro["sql"])
                if "from stime" in q and "for update" in q]
        scritture = [i for i, q in enumerate(registro["sql"])
                     if q.startswith("update") or q.startswith("insert")]
        assert lock, registro["sql"]
        assert lock[0] < min(scritture), registro["sql"]
