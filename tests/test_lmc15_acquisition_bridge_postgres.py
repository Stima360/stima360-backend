"""LMC-15 su PostgreSQL reale: il ponte, provato dove vive.

SETTE COSE CHE UN DOPPIO NON PUO' PROVARE.

LO SNAPSHOT. Che `stima_id_snapshot` lo scriva il DATABASE e non il chiamante,
che sia immutabile, e che sopravviva alla cancellazione della stima. E' un
trigger: fuori da PostgreSQL non esiste.

LA CARDINALITA'. Che una property abbia UNA sola origine attiva, e che dopo
una revoca esplicita possa averne un'altra. E' un indice unico parziale.

LA TENANCY DERIVATA. Che una stima di un'agenzia e una property di un'altra
non si possano legare, che un operatore estraneo non possa firmare, e che un
platform admin in acting - che una membership NON ce l'ha - possa comunque
lavorare. Sono tre rami dello stesso trigger.

LE MATRICI DI STATO. Che una revoca a meta' e un mandato senza autore siano
IRRAPPRESENTABILI, non solo scoraggiati. Sono CHECK.

IL HARD DELETE. Che cancellare una stima non fallisca, non porti via il
registro e non renda irrevocabile il link rimasto orfano. E' `ON DELETE SET
NULL` piu' un trigger che deve saperlo.

L'ATOMICITA'. Che il fatto e la sua proiezione sulla timeline vivano o
muoiano insieme.

LE METRICHE. Che i due numeratori nuovi contino le case giuste, dentro i
confini giusti, e solo dopo l'accensione della misura.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Database usa-e-getta.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare LMC-15")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
VERSIONE = "070_lmc15_acquisition_bridge"

GIORNO = timedelta(days=1)
#: "Adesso" del test. Le coorti si misurano all'indietro da qui.
ORA = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)

SCHEMA_MINIMO = """
CREATE TABLE agencies (
    id BIGSERIAL PRIMARY KEY, slug VARCHAR(80) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'active');
CREATE TABLE operator_users (
    id BIGSERIAL PRIMARY KEY, email VARCHAR(320) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    is_platform_admin BOOLEAN NOT NULL DEFAULT FALSE);
CREATE TABLE agency_memberships (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    operator_user_id BIGINT NOT NULL REFERENCES operator_users(id) ON DELETE RESTRICT,
    role VARCHAR(20) NOT NULL DEFAULT 'agent',
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    UNIQUE (agency_id, operator_user_id));
CREATE TABLE contacts (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    display_name VARCHAR(200), email VARCHAR(320), email_normalized VARCHAR(320),
    status VARCHAR(20) NOT NULL DEFAULT 'active');
CREATE TABLE stime (
    id SERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    comune VARCHAR(100), microzona VARCHAR(100), via VARCHAR(100), civico VARCHAR(20),
    tipologia VARCHAR(50), mq INTEGER,
    nome VARCHAR(50), cognome VARCHAR(50), email VARCHAR(100), telefono VARCHAR(30),
    data TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE leads (
    id BIGSERIAL PRIMARY KEY,
    contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE RESTRICT,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    pipeline VARCHAR(20) NOT NULL DEFAULT 'general',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE properties (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    title VARCHAR(200),
    commercial_status VARCHAR(50) NOT NULL DEFAULT 'draft');
CREATE TABLE activities (id BIGSERIAL PRIMARY KEY);
CREATE TABLE seller_timeline_events (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    contact_id BIGINT REFERENCES contacts(id) ON DELETE SET NULL,
    lead_id BIGINT REFERENCES leads(id) ON DELETE SET NULL,
    stima_id INTEGER REFERENCES stime(id) ON DELETE SET NULL,
    property_id BIGINT REFERENCES properties(id) ON DELETE SET NULL,
    event_type VARCHAR(50) NOT NULL, event_source VARCHAR(30),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key VARCHAR(255), created_by VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE UNIQUE INDEX idx_ste_idem ON seller_timeline_events (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE TABLE schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rolled_back_at TIMESTAMPTZ);
"""

#: Le migration reali che il ponte presuppone: il grant owner (066, che porta
#: `owner_accounts` e `owner_stima_access`) e la 070 stessa. Applicate dai
#: file veri, non riscritte qui: una copia diverge.
CATENA = ("009_owner_01", "066_lmc1_owner_stima_access", VERSIONE)


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"lmc15_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')

    dsn = _dsn_per(nome)
    # `DictCursor` e non `RealDictCursor`: le righe si leggono sia per indice
    # (`fetchone()[0]`) sia per nome, e i test usano entrambe le forme.
    from psycopg2.extras import DictCursor
    conn = psycopg2.connect(dsn, cursor_factory=DictCursor)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_MINIMO)
            for versione in CATENA:
                cur.execute((MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8"))
        conn.autocommit = False
        yield {"conn": conn, "dsn": dsn}
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


@pytest.fixture
def modulo(db, monkeypatch):
    import psycopg2

    from acquisition import repository as acq_repository
    from acquisition import service as acq_service
    from core import database as core_database
    from owner import home_metrics
    from owner import repository as owner_repository

    monkeypatch.setattr(core_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    return {"acq": acq_repository, "service": acq_service,
            "owner": owner_repository, "metrics": home_metrics}


@pytest.fixture
def mondo(db):
    """Due agenzie, i loro operatori, e le funzioni per popolarle."""
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        for tabella in ("stima_acquisitions", "stima_inspections",
                        "seller_timeline_events", "owner_stima_access",
                        "owner_accounts", "leads", "stime", "properties",
                        "contacts", "agency_memberships", "operator_users",
                        "agencies"):
            cur.execute(f"DELETE FROM {tabella}")
        cur.execute("DELETE FROM schema_migrations")
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno') RETURNING id")
        a = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (slug) VALUES ('b-due') RETURNING id")
        b = cur.fetchone()[0]

        def operatore(email, agenzia=None, platform=False):
            cur.execute("INSERT INTO operator_users (email, is_platform_admin) "
                        "VALUES (%s,%s) RETURNING id", (email, platform))
            i = cur.fetchone()[0]
            if agenzia is not None:
                cur.execute("INSERT INTO agency_memberships (agency_id, operator_user_id) "
                            "VALUES (%s,%s)", (agenzia, i))
            return i

        op_a = operatore("op-a@example.it", a)
        op_b = operatore("op-b@example.it", b)
        op_sospeso = operatore("sospeso@example.it", a)
        cur.execute("UPDATE agency_memberships SET status='revoked' "
                    "WHERE operator_user_id=%s", (op_sospeso,))
        admin = operatore("admin@example.it", None, platform=True)
    conn.commit()

    stato = {"conn": conn, "a": a, "b": b, "op_a": op_a, "op_b": op_b,
             "op_sospeso": op_sospeso, "admin": admin, "seq": 0}

    def stima(agency, via="Via Trieste"):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO stime (agency_id, comune, via, mq) "
                        "VALUES (%s,'Alba Adriatica',%s,95) RETURNING id", (agency, via))
            i = cur.fetchone()[0]
        conn.commit()
        return i

    def immobile(agency, titolo=None):
        stato["seq"] += 1
        with conn.cursor() as cur:
            cur.execute("INSERT INTO properties (agency_id, title) VALUES (%s,%s) RETURNING id",
                        (agency, titolo or f"Immobile {stato['seq']}"))
            i = cur.fetchone()[0]
        conn.commit()
        return i

    def account(agency, etichetta):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO contacts (agency_id, display_name, email, email_normalized) "
                        "VALUES (%s,%s,%s,%s) RETURNING id",
                        (agency, etichetta, f"{etichetta}@example.it",
                         f"{etichetta}@example.it"))
            contatto = cur.fetchone()[0]
            cur.execute("INSERT INTO owner_accounts (contact_id,status) "
                        "VALUES (%s,'active') RETURNING id", (contatto,))
            acc = cur.fetchone()[0]
        conn.commit()
        return {"account": acc, "contact": contatto}

    def grant(acc, stima_id, *, quando=None):
        quando = quando or ORA - 10 * GIORNO
        with conn.cursor() as cur:
            cur.execute("INSERT INTO owner_stima_access "
                        "(owner_account_id,stima_id,access_role,access_status,created_at,"
                        " valid_from,granted_by) "
                        "VALUES (%s,%s,'owner','active',%s,%s,'LMC_PROVISIONING') RETURNING id",
                        (acc["account"], stima_id, quando, quando - GIORNO))
            i = cur.fetchone()[0]
        conn.commit()
        return i

    def acceso(quando=None):
        """Registra la 070 nel ledger: la misura comincia da qui."""
        with conn.cursor() as cur:
            cur.execute("INSERT INTO schema_migrations (version, applied_at) "
                        "VALUES (%s,%s) ON CONFLICT (version) DO UPDATE "
                        "SET applied_at = EXCLUDED.applied_at, rolled_back_at = NULL",
                        (VERSIONE, quando or ORA - 100 * GIORNO))
        conn.commit()

    def righe(tabella, dove="TRUE", parametri=()):
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {tabella} WHERE {dove} ORDER BY id", parametri)
            return [dict(r) for r in cur.fetchall()]

    def sql(testo, parametri=None):
        """SQL grezzo. `None` e non `()` quando non ci sono parametri: con una
        sequenza vuota psycopg2 interpola comunque, e il `%` dentro una
        `RAISE EXCEPTION` di una migration diventerebbe un segnaposto."""
        with conn.cursor() as cur:
            cur.execute(testo, parametri)
            return cur.fetchall() if cur.description else None

    stato.update(stima=stima, immobile=immobile, account=account, grant=grant,
                 acceso=acceso, righe=righe, sql=sql)
    return stato


class Ctx:
    """Il contesto operatore, nella sola forma che il ponte usa."""

    def __init__(self, agency, user_id):
        self.user_id = user_id
        self._agency = agency

    def require_agency(self):
        return self._agency


# ---------------------------------------------------------------------------
# G - LO SNAPSHOT: LO SCRIVE IL DATABASE
# ---------------------------------------------------------------------------

def test_42_lo_snapshot_lo_assegna_il_database_e_ignora_il_client(mondo):
    """Il trigger non VALIDA il valore arrivato: lo SOVRASCRIVE senza
    leggerlo. Un client che lo dichiarasse non otterrebbe un errore da
    aggirare, ma semplicemente nessun effetto."""
    st = mondo["stima"](mondo["a"])
    pr = mondo["immobile"](mondo["a"])
    mondo["sql"](
        """INSERT INTO stima_acquisitions
                  (stima_id, stima_id_snapshot, property_id, linked_by_operator_user_id)
           VALUES (%s, 999999, %s, %s)""", (st, pr, mondo["op_a"]))
    riga = mondo["righe"]("stima_acquisitions")[0]
    assert riga["stima_id_snapshot"] == st
    assert riga["stima_id_snapshot"] != 999999


def test_43_senza_stima_non_si_crea_ne_un_link_ne_un_sopralluogo(mondo):
    import psycopg2
    pr = mondo["immobile"](mondo["a"])
    for sql, par in (
        ("""INSERT INTO stima_acquisitions (property_id, linked_by_operator_user_id)
            VALUES (%s,%s)""", (pr, mondo["op_a"])),
        ("""INSERT INTO stima_inspections (scheduled_for, created_by_operator_user_id)
            VALUES (NOW(), %s)""", (mondo["op_a"],)),
    ):
        with pytest.raises(psycopg2.Error):
            mondo["sql"](sql, par)
        mondo["conn"].rollback()


def test_44_lo_snapshot_e_immutabile_e_la_stima_non_si_riassegna(mondo, modulo):
    import psycopg2
    st = mondo["stima"](mondo["a"])
    altra = mondo["stima"](mondo["a"], via="Via Roma")
    pr = mondo["immobile"](mondo["a"])
    link = modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=st, property_id=pr, actor_user_id=mondo["op_a"])
    for sql, par in (
        ("UPDATE stima_acquisitions SET stima_id_snapshot = 7 WHERE id = %s", (link["id"],)),
        ("UPDATE stima_acquisitions SET stima_id = %s WHERE id = %s", (altra, link["id"])),
    ):
        with pytest.raises(psycopg2.Error):
            mondo["sql"](sql, par)
        mondo["conn"].rollback()


# ---------------------------------------------------------------------------
# H - LA CARDINALITA'
# ---------------------------------------------------------------------------

def test_45_una_stima_puo_generare_piu_immobili(mondo, modulo):
    """Un frazionamento: una villa stimata una volta diventa due unita'. Non
    si e' scelto UNIQUE su `stima_id` proprio per questo."""
    st = mondo["stima"](mondo["a"])
    uno = modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=st, property_id=mondo["immobile"](mondo["a"]),
        actor_user_id=mondo["op_a"])
    due = modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=st, property_id=mondo["immobile"](mondo["a"]),
        actor_user_id=mondo["op_a"])
    assert uno["id"] != due["id"]
    assert uno["stima_id"] == due["stima_id"] == st


def test_46_una_property_ha_UNA_sola_origine_attiva(mondo, modulo):
    """E la rifiuta l'INDICE UNICO PARZIALE, non un controllo applicativo: la
    seconda stima e' VALIDA e della STESSA agenzia, quindi nessun altro
    predicato potrebbe fermarla."""
    import psycopg2
    prima = mondo["stima"](mondo["a"])
    seconda = mondo["stima"](mondo["a"], via="Via Roma")
    pr = mondo["immobile"](mondo["a"])
    modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=prima, property_id=pr, actor_user_id=mondo["op_a"])
    with pytest.raises(psycopg2.errors.UniqueViolation) as info:
        mondo["sql"](
            """INSERT INTO stima_acquisitions
                      (stima_id, property_id, linked_by_operator_user_id)
               VALUES (%s,%s,%s)""", (seconda, pr, mondo["op_a"]))
    assert "idx_stima_acq_active_property" in str(info.value)
    mondo["conn"].rollback()


def test_47_dopo_una_revoca_la_property_si_ricollega(mondo, modulo):
    """L'indice e' PARZIALE: i link revocati restano nel registro e non
    occupano piu' il posto."""
    prima = mondo["stima"](mondo["a"])
    seconda = mondo["stima"](mondo["a"], via="Via Roma")
    pr = mondo["immobile"](mondo["a"])
    vecchio = modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=prima, property_id=pr, actor_user_id=mondo["op_a"])
    modulo["acq"].revoke_acquisition_link(
        mondo["a"], acquisition_id=vecchio["id"], reason="stima sbagliata",
        actor_user_id=mondo["op_a"])
    nuovo = modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=seconda, property_id=pr, actor_user_id=mondo["op_a"])
    assert nuovo["link_status"] == "active"
    assert len(mondo["righe"]("stima_acquisitions")) == 2, "il revocato resta nel registro"


# ---------------------------------------------------------------------------
# I - LA TENANCY, DERIVATA E IMPOSTA DAL DATABASE
# ---------------------------------------------------------------------------

def test_48_una_stima_e_una_property_di_agenzie_diverse_non_si_legano(mondo):
    import psycopg2
    st = mondo["stima"](mondo["a"])
    pr = mondo["immobile"](mondo["b"])
    with pytest.raises(psycopg2.Error) as info:
        mondo["sql"](
            """INSERT INTO stima_acquisitions
                      (stima_id, property_id, linked_by_operator_user_id)
               VALUES (%s,%s,%s)""", (st, pr, mondo["op_a"]))
    assert "tenancy" in str(info.value).lower()
    mondo["conn"].rollback()


def test_49_un_operatore_di_un_altra_agenzia_non_puo_agire(mondo):
    import psycopg2
    st = mondo["stima"](mondo["a"])
    pr = mondo["immobile"](mondo["a"])
    with pytest.raises(psycopg2.Error) as info:
        mondo["sql"](
            """INSERT INTO stima_acquisitions
                      (stima_id, property_id, linked_by_operator_user_id)
               VALUES (%s,%s,%s)""", (st, pr, mondo["op_b"]))
    assert "no active membership" in str(info.value)
    mondo["conn"].rollback()


def test_50_una_membership_revocata_non_basta(mondo):
    import psycopg2
    st = mondo["stima"](mondo["a"])
    pr = mondo["immobile"](mondo["a"])
    with pytest.raises(psycopg2.Error):
        mondo["sql"](
            """INSERT INTO stima_acquisitions
                      (stima_id, property_id, linked_by_operator_user_id)
               VALUES (%s,%s,%s)""", (st, pr, mondo["op_sospeso"]))
    mondo["conn"].rollback()


def test_51_il_platform_admin_in_acting_lavora_senza_membership(mondo, modulo):
    """LA REGOLA DEL PONTE PIU' DELICATA. Un platform admin in acting NON ha
    una `agency_memberships`: e' il modello di P26/060 e non un'eccezione da
    concedere. Un trigger che pretendesse la membership renderebbe
    impossibile una operazione legittima, ed e' esattamente cio' che lo
    SCHEMA GATE vietava."""
    st = mondo["stima"](mondo["a"])
    pr = mondo["immobile"](mondo["a"])
    link = modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=st, property_id=pr, actor_user_id=mondo["admin"])
    assert link["link_status"] == "active"
    assert mondo["righe"]("agency_memberships",
                          "operator_user_id = %s", (mondo["admin"],)) == []


def test_52_un_operatore_inesistente_e_rifiutato(mondo):
    import psycopg2
    st = mondo["stima"](mondo["a"])
    pr = mondo["immobile"](mondo["a"])
    with pytest.raises(psycopg2.Error) as info:
        mondo["sql"](
            """INSERT INTO stima_acquisitions
                      (stima_id, property_id, linked_by_operator_user_id)
               VALUES (%s,%s,%s)""", (st, pr, 987654))
    assert "does not exist" in str(info.value)
    mondo["conn"].rollback()


def test_53_il_repository_non_vede_le_stime_ne_le_property_altrui(mondo, modulo):
    from core.exceptions import NotFoundError
    st_a, st_b = mondo["stima"](mondo["a"]), mondo["stima"](mondo["b"])
    pr_a, pr_b = mondo["immobile"](mondo["a"]), mondo["immobile"](mondo["b"])
    # stima altrui
    with pytest.raises(NotFoundError):
        modulo["acq"].create_acquisition_link(
            mondo["a"], stima_id=st_b, property_id=pr_a, actor_user_id=mondo["op_a"])
    # property altrui
    with pytest.raises(NotFoundError):
        modulo["acq"].create_acquisition_link(
            mondo["a"], stima_id=st_a, property_id=pr_b, actor_user_id=mondo["op_a"])
    assert mondo["righe"]("stima_acquisitions") == []


def test_54_un_link_dell_altra_agenzia_e_404_non_403(mondo, modulo):
    """Dire "esiste ma non e' tua" confermerebbe che esiste."""
    from core.exceptions import NotFoundError
    st = mondo["stima"](mondo["b"])
    pr = mondo["immobile"](mondo["b"])
    link = modulo["acq"].create_acquisition_link(
        mondo["b"], stima_id=st, property_id=pr, actor_user_id=mondo["op_b"])
    for chiamata in (
        lambda: modulo["acq"].record_mandate(
            mondo["a"], acquisition_id=link["id"], signed_at=ORA,
            reference=None, actor_user_id=mondo["op_a"]),
        lambda: modulo["acq"].revoke_acquisition_link(
            mondo["a"], acquisition_id=link["id"], reason="x",
            actor_user_id=mondo["op_a"]),
    ):
        with pytest.raises(NotFoundError):
            chiamata()


# ---------------------------------------------------------------------------
# J - LA MATRICE DELLA REVOCA
# ---------------------------------------------------------------------------

def _link_nudo(mondo, modulo):
    return modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=mondo["stima"](mondo["a"]),
        property_id=mondo["immobile"](mondo["a"]), actor_user_id=mondo["op_a"])


@pytest.mark.parametrize("assegnazioni", [
    "revoked_at = NOW()",
    "revoked_reason = 'x'",
    "revoked_by_operator_user_id = %(op)s",
    "link_status = 'revoked'",
    "link_status = 'revoked', revoked_at = NOW()",
    "link_status = 'revoked', revoked_at = NOW(), revoked_reason = 'x'",
    "link_status = 'revoked', revoked_at = NOW(), revoked_reason = '   ', "
    "revoked_by_operator_user_id = %(op)s",
])
def test_55_ogni_revoca_PARZIALE_e_irrappresentabile(mondo, modulo, assegnazioni):
    """Non "scoraggiata": impossibile. Sette stati intermedi, sette rifiuti."""
    import psycopg2
    link = _link_nudo(mondo, modulo)
    with pytest.raises(psycopg2.errors.CheckViolation):
        mondo["sql"](f"UPDATE stima_acquisitions SET {assegnazioni} WHERE id = %(id)s",
                     {"op": mondo["op_a"], "id": link["id"]})
    mondo["conn"].rollback()


def test_56_la_revoca_completa_passa_e_non_tocca_il_mandato(mondo, modulo):
    link = _link_nudo(mondo, modulo)
    modulo["acq"].record_mandate(mondo["a"], acquisition_id=link["id"],
                                 signed_at=ORA - GIORNO, reference="REP/42",
                                 actor_user_id=mondo["op_a"])
    revocato = modulo["acq"].revoke_acquisition_link(
        mondo["a"], acquisition_id=link["id"], reason="mandato rescisso",
        actor_user_id=mondo["op_a"])
    assert revocato["link_status"] == "revoked"
    assert revocato["revoked_reason"] == "mandato rescisso"
    # IL LINK e IL MANDATO SONO FATTI DIVERSI: revocare il primo non cancella
    # il secondo, che e' successo davvero e resta nel registro.
    assert revocato["mandate_signed_at"] is not None
    assert revocato["mandate_reference"] == "REP/42"


def test_57_revocare_due_volte_e_un_conflitto_non_un_secondo_effetto(mondo, modulo):
    from core.exceptions import ConflictError
    link = _link_nudo(mondo, modulo)
    modulo["acq"].revoke_acquisition_link(
        mondo["a"], acquisition_id=link["id"], reason="prima",
        actor_user_id=mondo["op_a"])
    with pytest.raises(ConflictError):
        modulo["acq"].revoke_acquisition_link(
            mondo["a"], acquisition_id=link["id"], reason="seconda",
            actor_user_id=mondo["op_a"])
    riga = mondo["righe"]("stima_acquisitions")[0]
    assert riga["revoked_reason"] == "prima", "la prima revoca non viene riscritta"


def test_58_revocare_un_link_inesistente_e_404(mondo, modulo):
    from core.exceptions import NotFoundError
    with pytest.raises(NotFoundError):
        modulo["acq"].revoke_acquisition_link(
            mondo["a"], acquisition_id=987654, reason="x", actor_user_id=mondo["op_a"])


# ---------------------------------------------------------------------------
# K - IL MANDATO
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("assegnazioni", [
    "mandate_signed_at = NOW()",
    "mandate_recorded_at = NOW()",
    "mandate_recorded_by_operator_user_id = %(op)s",
    "mandate_signed_at = NOW(), mandate_recorded_at = NOW()",
    "mandate_reference = 'REP/1'",
])
def test_59_un_mandato_a_meta_e_irrappresentabile(mondo, modulo, assegnazioni):
    """Firma, registrazione e autore sono una TRIPLA: `num_nonnulls IN (0,3)`.
    E un riferimento di repertorio senza firma non e' un mandato."""
    import psycopg2
    link = _link_nudo(mondo, modulo)
    with pytest.raises(psycopg2.errors.CheckViolation):
        mondo["sql"](f"UPDATE stima_acquisitions SET {assegnazioni} WHERE id = %(id)s",
                     {"op": mondo["op_a"], "id": link["id"]})
    mondo["conn"].rollback()


def test_60_non_si_registra_un_mandato_PRIMA_che_sia_firmato(mondo, modulo):
    import psycopg2
    link = _link_nudo(mondo, modulo)
    with pytest.raises(psycopg2.errors.CheckViolation):
        mondo["sql"](
            """UPDATE stima_acquisitions
                  SET mandate_signed_at = NOW(),
                      mandate_recorded_at = NOW() - interval '1 day',
                      mandate_recorded_by_operator_user_id = %s
                WHERE id = %s""", (mondo["op_a"], link["id"]))
    mondo["conn"].rollback()


def test_61_la_data_di_registrazione_la_mette_il_server(mondo, modulo):
    """`mandate_signed_at` e' dichiarata - e' la data vera della firma -
    ma `mandate_recorded_at` e' `NOW()`: lasciarla al client vorrebbe dire
    poter raccontare di aver registrato ieri cio' che si registra oggi."""
    link = _link_nudo(mondo, modulo)
    firma = datetime.now(timezone.utc) - 3 * GIORNO
    esito = modulo["acq"].record_mandate(
        mondo["a"], acquisition_id=link["id"], signed_at=firma,
        reference=None, actor_user_id=mondo["op_a"])
    assert abs((esito["mandate_signed_at"] - firma).total_seconds()) < 1
    assert esito["mandate_recorded_at"] > firma
    from acquisition import schemas
    assert "mandate_recorded_at" not in schemas.MandateRecord.model_fields


def test_62_un_secondo_mandato_e_un_conflitto_dichiarato(mondo, modulo):
    from core.exceptions import ConflictError
    link = _link_nudo(mondo, modulo)
    modulo["acq"].record_mandate(mondo["a"], acquisition_id=link["id"],
                                 signed_at=ORA - GIORNO, reference=None,
                                 actor_user_id=mondo["op_a"])
    with pytest.raises(ConflictError) as info:
        modulo["acq"].record_mandate(mondo["a"], acquisition_id=link["id"],
                                     signed_at=ORA, reference=None,
                                     actor_user_id=mondo["op_a"])
    assert "gia'" in str(info.value)


def test_63_su_un_link_revocato_non_si_registra_un_mandato(mondo, modulo):
    from core.exceptions import ConflictError
    link = _link_nudo(mondo, modulo)
    modulo["acq"].revoke_acquisition_link(
        mondo["a"], acquisition_id=link["id"], reason="x", actor_user_id=mondo["op_a"])
    with pytest.raises(ConflictError) as info:
        modulo["acq"].record_mandate(mondo["a"], acquisition_id=link["id"],
                                     signed_at=ORA, reference=None,
                                     actor_user_id=mondo["op_a"])
    assert "revocato" in str(info.value).lower()


# ---------------------------------------------------------------------------
# L - IL SOPRALLUOGO
# ---------------------------------------------------------------------------

def test_64_il_ciclo_normale_fissa_e_conclude(mondo, modulo):
    st = mondo["stima"](mondo["a"])
    appuntamento = datetime.now(timezone.utc) + GIORNO
    fissato = modulo["acq"].create_inspection(
        mondo["a"], stima_id=st, scheduled_for=appuntamento,
        actor_user_id=mondo["op_a"])
    assert fissato["status"] == "scheduled"
    assert fissato["completed_at"] is None
    avvenuto = datetime.now(timezone.utc) - timedelta(hours=1)
    concluso = modulo["acq"].complete_inspection(
        mondo["a"], inspection_id=fissato["id"], completed_at=avvenuto,
        actor_user_id=mondo["op_a"])
    assert concluso["status"] == "completed"
    assert abs((concluso["completed_at"] - avvenuto).total_seconds()) < 1


def test_65_annullare_richiede_un_appuntamento_da_annullare(mondo, modulo):
    """`cancelled` esige `scheduled_for NOT NULL`: se un appuntamento non c'e'
    mai stato non c'e' niente da disdire."""
    import psycopg2
    st = mondo["stima"](mondo["a"])
    registrato = modulo["acq"].create_completed_inspection(
        mondo["a"], stima_id=st, completed_at=datetime.now(timezone.utc) - GIORNO,
        actor_user_id=mondo["op_a"])
    assert registrato["scheduled_for"] is None
    with pytest.raises(psycopg2.errors.CheckViolation):
        mondo["sql"](
            """UPDATE stima_inspections
                  SET status='cancelled', cancelled_at=NOW(), cancelled_recorded_at=NOW(),
                      cancelled_by_operator_user_id=%s, cancelled_reason='x',
                      completed_at=NULL, completed_recorded_at=NULL,
                      completed_by_operator_user_id=NULL
                WHERE id=%s""", (mondo["op_a"], registrato["id"]))
    mondo["conn"].rollback()


def test_66_un_sopralluogo_avvenuto_e_mai_fissato_si_registra(mondo, modulo):
    """L'unico stato in cui `scheduled_for` puo' restare NULL. Pretendere una
    data di appuntamento mai esistita costringerebbe a inventarla."""
    st = mondo["stima"](mondo["a"])
    avvenuto = datetime.now(timezone.utc) - 5 * GIORNO
    riga = modulo["acq"].create_completed_inspection(
        mondo["a"], stima_id=st, completed_at=avvenuto, actor_user_id=mondo["op_a"])
    assert riga["status"] == "completed" and riga["scheduled_for"] is None
    assert abs((riga["completed_at"] - avvenuto).total_seconds()) < 1


def test_67_annullare_un_appuntamento_fissato_funziona_anche_senza_ragione(mondo, modulo):
    st = mondo["stima"](mondo["a"])
    fissato = modulo["acq"].create_inspection(
        mondo["a"], stima_id=st, scheduled_for=datetime.now(timezone.utc) + GIORNO,
        actor_user_id=mondo["op_a"])
    annullato = modulo["acq"].cancel_inspection(
        mondo["a"], inspection_id=fissato["id"], reason=None,
        actor_user_id=mondo["op_a"])
    assert annullato["status"] == "cancelled"
    assert annullato["cancelled_at"] is not None
    assert annullato["cancelled_reason"] is None


@pytest.mark.parametrize("gesto", ["complete", "cancel"])
def test_68_i_due_stati_finali_sono_TERMINALI(mondo, modulo, gesto):
    """Concluso e annullato non si riaprono e non si scambiano: la
    transizione parte solo da `scheduled`."""
    from core.exceptions import ConflictError
    st = mondo["stima"](mondo["a"])
    fissato = modulo["acq"].create_inspection(
        mondo["a"], stima_id=st, scheduled_for=datetime.now(timezone.utc) + GIORNO,
        actor_user_id=mondo["op_a"])
    if gesto == "complete":
        modulo["acq"].complete_inspection(
            mondo["a"], inspection_id=fissato["id"],
            completed_at=datetime.now(timezone.utc), actor_user_id=mondo["op_a"])
    else:
        modulo["acq"].cancel_inspection(
            mondo["a"], inspection_id=fissato["id"], reason="disdetto",
            actor_user_id=mondo["op_a"])
    for chiamata in (
        lambda: modulo["acq"].complete_inspection(
            mondo["a"], inspection_id=fissato["id"],
            completed_at=datetime.now(timezone.utc), actor_user_id=mondo["op_a"]),
        lambda: modulo["acq"].cancel_inspection(
            mondo["a"], inspection_id=fissato["id"], reason="ancora",
            actor_user_id=mondo["op_a"]),
    ):
        with pytest.raises(ConflictError):
            chiamata()


def test_69_un_sopralluogo_dell_altra_agenzia_e_404(mondo, modulo):
    from core.exceptions import NotFoundError
    st = mondo["stima"](mondo["b"])
    altrui = modulo["acq"].create_inspection(
        mondo["b"], stima_id=st, scheduled_for=datetime.now(timezone.utc) + GIORNO,
        actor_user_id=mondo["op_b"])
    with pytest.raises(NotFoundError):
        modulo["acq"].complete_inspection(
            mondo["a"], inspection_id=altrui["id"],
            completed_at=datetime.now(timezone.utc), actor_user_id=mondo["op_a"])
    with pytest.raises(NotFoundError):
        modulo["acq"].create_inspection(
            mondo["a"], stima_id=st,
            scheduled_for=datetime.now(timezone.utc) + GIORNO,
            actor_user_id=mondo["op_a"])


# ---------------------------------------------------------------------------
# M - LA PROIEZIONE SULLA TIMELINE
# ---------------------------------------------------------------------------

def test_70_ogni_gesto_lascia_UNA_riga_di_timeline_scopata(mondo, modulo):
    st = mondo["stima"](mondo["a"])
    pr = mondo["immobile"](mondo["a"])
    link = modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=st, property_id=pr, actor_user_id=mondo["op_a"])
    modulo["acq"].record_mandate(mondo["a"], acquisition_id=link["id"],
                                 signed_at=datetime.now(timezone.utc) - GIORNO,
                                 reference=None, actor_user_id=mondo["op_a"])
    fissato = modulo["acq"].create_inspection(
        mondo["a"], stima_id=st, scheduled_for=datetime.now(timezone.utc) + GIORNO,
        actor_user_id=mondo["op_a"])
    modulo["acq"].complete_inspection(
        mondo["a"], inspection_id=fissato["id"],
        completed_at=datetime.now(timezone.utc), actor_user_id=mondo["op_a"])
    modulo["acq"].revoke_acquisition_link(
        mondo["a"], acquisition_id=link["id"], reason="fine",
        actor_user_id=mondo["op_a"])

    eventi = mondo["righe"]("seller_timeline_events")
    assert [e["event_type"] for e in eventi] == [
        "acquisition_linked", "mandate_signed", "inspection_scheduled",
        "inspection_completed", "acquisition_revoked"]
    for e in eventi:
        assert e["agency_id"] == mondo["a"]
        assert e["event_source"] == "crm_acquisition"
        assert e["idempotency_key"].startswith("lmc15:v1:")
        assert e["occurred_at"] is not None
        assert e["created_by"] == str(mondo["op_a"])
        # La proiezione non porta il proprietario: e' un fatto dell'agenzia.
        assert e["contact_id"] is None and e["lead_id"] is None


def test_71_l_evento_porta_l_istante_del_FATTO_non_quello_della_scrittura(mondo, modulo):
    """Un sopralluogo avvenuto a marzo e scritto oggi sta sulla timeline a
    marzo: e' la sola data che racconti la storia del venditore."""
    st = mondo["stima"](mondo["a"])
    avvenuto = datetime.now(timezone.utc) - 30 * GIORNO
    modulo["acq"].create_completed_inspection(
        mondo["a"], stima_id=st, completed_at=avvenuto, actor_user_id=mondo["op_a"])
    evento = mondo["righe"]("seller_timeline_events")[0]
    assert abs((evento["occurred_at"] - avvenuto).total_seconds()) < 1
    # e la tabella del ponte conserva ANCHE quando lo si e' scritto.
    riga = mondo["righe"]("stima_inspections")[0]
    assert riga["completed_recorded_at"] > riga["completed_at"]


def test_72_il_fatto_e_la_sua_proiezione_vivono_o_muoiono_insieme(mondo, modulo,
                                                                 monkeypatch):
    """`seller_timeline_events` e' una PROIEZIONE: se la sua scrittura
    fallisce non deve restare un fatto che nessuna timeline racconta."""
    from core.exceptions import ValidationError
    st = mondo["stima"](mondo["a"])
    pr = mondo["immobile"](mondo["a"])

    def esplode(cur, dati, agency_id):
        raise ValidationError("proiezione fallita")

    from seller_intelligence import repository as si_repository
    monkeypatch.setattr(si_repository, "_insert_event_with_agency", esplode)
    with pytest.raises(ValidationError):
        modulo["acq"].create_acquisition_link(
            mondo["a"], stima_id=st, property_id=pr, actor_user_id=mondo["op_a"])
    assert mondo["righe"]("stima_acquisitions") == [], "il fatto non deve restare"
    assert mondo["righe"]("seller_timeline_events") == []


def test_73_l_evento_della_revoca_porta_lo_snapshot_e_non_inventa_la_stima(mondo, modulo):
    st = mondo["stima"](mondo["a"])
    pr = mondo["immobile"](mondo["a"])
    link = modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=st, property_id=pr, actor_user_id=mondo["op_a"])
    mondo["sql"]("DELETE FROM stime WHERE id = %s", (st,))
    mondo["conn"].commit()
    modulo["acq"].revoke_acquisition_link(
        mondo["a"], acquisition_id=link["id"], reason="stima cancellata",
        actor_user_id=mondo["op_a"])
    revoca = [e for e in mondo["righe"]("seller_timeline_events")
              if e["event_type"] == "acquisition_revoked"][0]
    assert revoca["stima_id"] is None
    assert revoca["payload"]["stima_id_snapshot"] == st
    assert revoca["property_id"] == pr


# ---------------------------------------------------------------------------
# N - IL HARD DELETE DELLA STIMA
# ---------------------------------------------------------------------------

def test_74_cancellare_una_stima_NON_fallisce_e_non_porta_via_il_registro(mondo, modulo):
    """LA REGRESSIONE PIU' PERICOLOSA DI LMC-15, e la ragione per cui la
    guardia dei sopralluoghi verifica un attore solo quando lo si SCRIVE.

    `DELETE /api/admin/stime/delete` esiste da prima di LMC-15 ed e' gia'
    certificato. `ON DELETE SET NULL` esegue un UPDATE sulle righe figlie, e
    una guardia che in quel momento rivalidasse attori immutati non
    troverebbe piu' nessuna agenzia contro cui farlo: la cancellazione
    fallirebbe, e il ponte avrebbe rotto una funzione che non e' sua.
    """
    st = mondo["stima"](mondo["a"])
    pr = mondo["immobile"](mondo["a"])
    link = modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=st, property_id=pr, actor_user_id=mondo["op_a"])
    sopralluogo = modulo["acq"].create_inspection(
        mondo["a"], stima_id=st, scheduled_for=datetime.now(timezone.utc) + GIORNO,
        actor_user_id=mondo["op_a"])

    mondo["sql"]("DELETE FROM stime WHERE id = %s", (st,))
    mondo["conn"].commit()

    l = mondo["righe"]("stima_acquisitions")[0]
    i = mondo["righe"]("stima_inspections")[0]
    assert l["stima_id"] is None and l["stima_id_snapshot"] == st
    assert i["stima_id"] is None and i["stima_id_snapshot"] == st
    assert l["link_status"] == "active", "nessun auto-revoke durante il DELETE"
    assert i["status"] == "scheduled"
    assert mondo["righe"]("properties", "id = %s", (pr,)), "la property resta"
    assert l["id"] == link["id"] and i["id"] == sopralluogo["id"]


def test_75_il_link_rimasto_orfano_resta_revocabile(mondo, modulo):
    """Proprio nel momento in cui serve. La tenancy della revoca passa dalla
    PROPERTY, che c'e' ancora, e non dalla stima, che non c'e' piu'."""
    st = mondo["stima"](mondo["a"])
    pr = mondo["immobile"](mondo["a"])
    link = modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=st, property_id=pr, actor_user_id=mondo["op_a"])
    mondo["sql"]("DELETE FROM stime WHERE id = %s", (st,))
    mondo["conn"].commit()
    revocato = modulo["acq"].revoke_acquisition_link(
        mondo["a"], acquisition_id=link["id"], reason="stima cancellata",
        actor_user_id=mondo["op_a"])
    assert revocato["link_status"] == "revoked"
    # ...e nemmeno da orfano puo' revocarlo un operatore di un'altra agenzia.
    altro = modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=mondo["stima"](mondo["a"]),
        property_id=mondo["immobile"](mondo["a"]), actor_user_id=mondo["op_a"])
    from core.exceptions import NotFoundError
    with pytest.raises(NotFoundError):
        modulo["acq"].revoke_acquisition_link(
            mondo["b"], acquisition_id=altro["id"], reason="x",
            actor_user_id=mondo["op_b"])


def test_76_su_una_riga_orfana_non_si_scrive_un_attore_nuovo(mondo, modulo):
    """L'altra meta' della regola: senza stima non c'e' agenzia, e senza
    agenzia un attore non si puo' verificare. Quindi non si scrive."""
    import psycopg2
    st = mondo["stima"](mondo["a"])
    sopralluogo = modulo["acq"].create_inspection(
        mondo["a"], stima_id=st, scheduled_for=datetime.now(timezone.utc) + GIORNO,
        actor_user_id=mondo["op_a"])
    mondo["sql"]("DELETE FROM stime WHERE id = %s", (st,))
    mondo["conn"].commit()
    with pytest.raises(psycopg2.Error) as info:
        mondo["sql"](
            """UPDATE stima_inspections
                  SET status='completed', completed_at=NOW(), completed_recorded_at=NOW(),
                      completed_by_operator_user_id=%s
                WHERE id=%s""", (mondo["op_a"], sopralluogo["id"]))
    assert "without an agency" in str(info.value)
    mondo["conn"].rollback()


def test_77_una_property_collegata_non_si_puo_cancellare(mondo, modulo):
    """`ON DELETE RESTRICT`: `properties` non viene mai hard-deleted dal repo
    (l'endpoint archivia), e se qualcuno ci provasse il registro la
    tratterrebbe invece di perdere il legame."""
    import psycopg2
    st = mondo["stima"](mondo["a"])
    pr = mondo["immobile"](mondo["a"])
    modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=st, property_id=pr, actor_user_id=mondo["op_a"])
    with pytest.raises(psycopg2.Error):
        mondo["sql"]("DELETE FROM properties WHERE id = %s", (pr,))
    mondo["conn"].rollback()


def test_78_un_operatore_che_ha_firmato_non_si_puo_cancellare(mondo, modulo):
    """Un registro di audit che perde l'autore non e' piu' un registro."""
    import psycopg2
    st = mondo["stima"](mondo["a"])
    pr = mondo["immobile"](mondo["a"])
    modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=st, property_id=pr, actor_user_id=mondo["op_a"])
    with pytest.raises(psycopg2.Error):
        mondo["sql"]("DELETE FROM operator_users WHERE id = %s", (mondo["op_a"],))
    mondo["conn"].rollback()


# ---------------------------------------------------------------------------
# O - LE METRICHE LMC-13, DA CAPO A FONDO
# ---------------------------------------------------------------------------

def _metriche(modulo, mondo, agency=None, *, days=30, now=ORA):
    """Il DTO come lo comporrebbe la rotta."""
    da, a = modulo["metrics"].window(days, now=now)
    conteggi, inizio = modulo["owner"].home_metrics_counts(
        agency if agency is not None else mondo["a"], cohort_from=da, cohort_to=a)
    return modulo["metrics"].build(conteggi, days=days, cohort_from=da,
                                   cohort_to=a, measurement_started_at=inizio)


def _casa(mondo, agency=None, etichetta="mario"):
    """Una stima con un proprietario e il suo grant: una casa in coorte."""
    agency = agency if agency is not None else mondo["a"]
    st = mondo["stima"](agency)
    acc = mondo["account"](agency, f"{etichetta}-{st}")
    mondo["grant"](acc, st)
    return st


def test_79_senza_riga_nel_registro_i_due_gradini_restano_assenti(mondo, modulo):
    """Le tabelle ESISTONO - la 070 e' applicata su questo database - ma il
    registro non dice da quando. Senza quella data la misura non e'
    cominciata, e il dato e' assente, non zero."""
    _casa(mondo)
    d = _metriche(modulo, mondo)
    assert d["cohort_homes"] == 1
    assert d["inspection_homes"] is None and d["mandate_homes"] is None
    assert d["measurement_started_at"] is None
    assert (d["not_measurable"]["inspection_homes"]
            == modulo["metrics"].REASON_NOT_APPLIED)


def test_80_la_data_viene_dal_registro_e_finisce_nel_dto(mondo, modulo):
    acceso = ORA - 100 * GIORNO
    mondo["acceso"](acceso)
    _casa(mondo)
    d = _metriche(modulo, mondo)
    assert d["measurement_started_at"] == modulo["metrics"]._iso(acceso)
    assert d["inspection_homes"] == 0, "misurato: zero e' un numero"
    assert d["not_measurable"] == {}


def test_81_una_migration_annullata_spegne_di_nuovo_la_misura(mondo, modulo):
    mondo["acceso"](ORA - 100 * GIORNO)
    _casa(mondo)
    assert _metriche(modulo, mondo)["inspection_homes"] == 0
    mondo["sql"]("UPDATE schema_migrations SET rolled_back_at = NOW() WHERE version = %s",
                 (VERSIONE,))
    mondo["conn"].commit()
    d = _metriche(modulo, mondo)
    assert d["inspection_homes"] is None and d["measurement_started_at"] is None


def test_82_una_coorte_anteriore_all_accensione_non_si_misura(mondo, modulo):
    """NO BACKFILL. Quelle case non avevano un ponte su cui passare: dire
    "zero incarichi" sarebbe una bugia con l'aria di una misura."""
    mondo["acceso"](ORA - 5 * GIORNO)      # acceso DOPO l'inizio della coorte
    _casa(mondo)
    d = _metriche(modulo, mondo, days=30)  # cohort_from = ORA - 30 giorni
    assert d["cohort_homes"] == 1, "le altre metriche continuano a misurare"
    assert d["viewed_homes"] == 0
    assert d["inspection_homes"] is None and d["mandate_homes"] is None
    assert (d["not_measurable"]["mandate_homes"]
            == modulo["metrics"].REASON_BEFORE_START)
    assert d["measurement_started_at"] is not None, "la data si dichiara comunque"


def test_83_solo_il_sopralluogo_AVVENUTO_conta(mondo, modulo):
    """Fissato non e' avvenuto, e annullato nemmeno. Contare gli appuntamenti
    misurerebbe le intenzioni dell'agenzia, non il percorso della casa."""
    mondo["acceso"](ORA - 100 * GIORNO)
    fatta = _casa(mondo, etichetta="fatta")
    fissata = _casa(mondo, etichetta="fissata")
    disdetta = _casa(mondo, etichetta="disdetta")
    modulo["acq"].create_completed_inspection(
        mondo["a"], stima_id=fatta, completed_at=ORA - 5 * GIORNO,
        actor_user_id=mondo["op_a"])
    modulo["acq"].create_inspection(
        mondo["a"], stima_id=fissata, scheduled_for=datetime.now(timezone.utc) + GIORNO,
        actor_user_id=mondo["op_a"])
    annullabile = modulo["acq"].create_inspection(
        mondo["a"], stima_id=disdetta, scheduled_for=datetime.now(timezone.utc) + GIORNO,
        actor_user_id=mondo["op_a"])
    modulo["acq"].cancel_inspection(
        mondo["a"], inspection_id=annullabile["id"], reason="disdetto",
        actor_user_id=mondo["op_a"])
    d = _metriche(modulo, mondo)
    assert d["cohort_homes"] == 3
    assert d["inspection_homes"] == 1
    assert d["rates"]["inspection_rate"] == round(1 / 3, 4)


def test_84_solo_il_mandato_FIRMATO_su_un_link_ATTIVO_conta(mondo, modulo):
    mondo["acceso"](ORA - 100 * GIORNO)
    firmata = _casa(mondo, etichetta="firmata")
    nuda = _casa(mondo, etichetta="nuda")
    revocata = _casa(mondo, etichetta="revocata")

    uno = modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=firmata, property_id=mondo["immobile"](mondo["a"]),
        actor_user_id=mondo["op_a"])
    modulo["acq"].record_mandate(mondo["a"], acquisition_id=uno["id"],
                                 signed_at=ORA - 4 * GIORNO, reference=None,
                                 actor_user_id=mondo["op_a"])
    # un link senza firma
    modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=nuda, property_id=mondo["immobile"](mondo["a"]),
        actor_user_id=mondo["op_a"])
    # un link firmato ma poi revocato
    tre = modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=revocata, property_id=mondo["immobile"](mondo["a"]),
        actor_user_id=mondo["op_a"])
    modulo["acq"].record_mandate(mondo["a"], acquisition_id=tre["id"],
                                 signed_at=ORA - 4 * GIORNO, reference=None,
                                 actor_user_id=mondo["op_a"])
    modulo["acq"].revoke_acquisition_link(
        mondo["a"], acquisition_id=tre["id"], reason="rescisso",
        actor_user_id=mondo["op_a"])

    d = _metriche(modulo, mondo)
    assert d["cohort_homes"] == 3
    assert d["mandate_homes"] == 1


def test_85_i_confini_temporali_valgono_anche_per_i_due_gradini_nuovi(mondo, modulo):
    """`>= cohort_at` perche' un fatto anteriore all'ingresso della casa nel
    portale non appartiene a quella coorte, `< cohort_to` come per tutto il
    resto del DTO."""
    mondo["acceso"](ORA - 100 * GIORNO)
    prima = _casa(mondo, etichetta="prima")      # cohort_at = ORA - 10 giorni
    dentro = _casa(mondo, etichetta="dentro")
    modulo["acq"].create_completed_inspection(
        mondo["a"], stima_id=prima, completed_at=ORA - 20 * GIORNO,
        actor_user_id=mondo["op_a"])
    modulo["acq"].create_completed_inspection(
        mondo["a"], stima_id=dentro, completed_at=ORA - 3 * GIORNO,
        actor_user_id=mondo["op_a"])
    assert _metriche(modulo, mondo)["inspection_homes"] == 1
    # e con la finestra chiusa PRIMA di quel sopralluogo, nessuno.
    assert _metriche(modulo, mondo, now=ORA - 4 * GIORNO)["inspection_homes"] == 0


def test_86_una_casa_con_due_sopralluoghi_conta_UNA_volta(mondo, modulo):
    """L'unita' e' la CASA anche qui: `COUNT(DISTINCT stima_id)`."""
    mondo["acceso"](ORA - 100 * GIORNO)
    st = _casa(mondo)
    for giorni in (5, 3):
        modulo["acq"].create_completed_inspection(
            mondo["a"], stima_id=st, completed_at=ORA - giorni * GIORNO,
            actor_user_id=mondo["op_a"])
    d = _metriche(modulo, mondo)
    assert d["inspection_homes"] == 1
    assert d["rates"]["inspection_rate"] == 1.0


def test_87_una_stima_che_genera_due_immobili_conta_UNA_volta(mondo, modulo):
    """Un frazionamento e' una acquisizione sola, non due."""
    mondo["acceso"](ORA - 100 * GIORNO)
    st = _casa(mondo)
    for _ in range(2):
        link = modulo["acq"].create_acquisition_link(
            mondo["a"], stima_id=st, property_id=mondo["immobile"](mondo["a"]),
            actor_user_id=mondo["op_a"])
        modulo["acq"].record_mandate(mondo["a"], acquisition_id=link["id"],
                                     signed_at=ORA - 4 * GIORNO, reference=None,
                                     actor_user_id=mondo["op_a"])
    assert _metriche(modulo, mondo)["mandate_homes"] == 1


def test_88_i_due_gradini_nuovi_sono_isolati_fra_tenant(mondo, modulo):
    mondo["acceso"](ORA - 100 * GIORNO)
    mia = _casa(mondo, mondo["a"], "mia")
    sua = _casa(mondo, mondo["b"], "sua")
    modulo["acq"].create_completed_inspection(
        mondo["b"], stima_id=sua, completed_at=ORA - 3 * GIORNO,
        actor_user_id=mondo["op_b"])
    link = modulo["acq"].create_acquisition_link(
        mondo["b"], stima_id=sua, property_id=mondo["immobile"](mondo["b"]),
        actor_user_id=mondo["op_b"])
    modulo["acq"].record_mandate(mondo["b"], acquisition_id=link["id"],
                                 signed_at=ORA - 3 * GIORNO, reference=None,
                                 actor_user_id=mondo["op_b"])
    assert mia  # la casa di A esiste ma non ha nessun fatto del ponte
    a = _metriche(modulo, mondo, mondo["a"])
    b = _metriche(modulo, mondo, mondo["b"])
    assert a["cohort_homes"] == 1 and b["cohort_homes"] == 1
    assert a["inspection_homes"] == 0 and a["mandate_homes"] == 0
    assert b["inspection_homes"] == 1 and b["mandate_homes"] == 1


def test_89_il_dto_resta_una_whitelist_chiusa_anche_col_ponte_acceso(mondo, modulo):
    """Le due colonne nuove entrano nel DTO, e nient'altro con loro."""
    mondo["acceso"](ORA - 100 * GIORNO)
    st = _casa(mondo)
    modulo["acq"].create_completed_inspection(
        mondo["a"], stima_id=st, completed_at=ORA - 3 * GIORNO,
        actor_user_id=mondo["op_a"])
    d = _metriche(modulo, mondo)
    atteso = {"period_days", "cohort_from", "cohort_to", "unit",
              "measurement_started_at",
              "cohort_homes", "active_homes_now", "activated_owners",
              "viewed_homes", "returning_homes", "value_interest_homes",
              "demand_interest_homes", "updated_homes", "strong_interest_homes",
              "consultation_homes", "inspection_homes", "mandate_homes",
              "rates", "not_measurable"}
    assert set(d) == atteso, sorted(set(d) ^ atteso)
    for chiave in d:
        for vietata in ("property_id", "acquisition_id", "inspection_id",
                        "operator", "email", "reference", "snapshot"):
            assert vietata not in chiave.lower(), (chiave, vietata)
    from owner.schemas import HomeMetricsResponse
    HomeMetricsResponse.model_validate(d)


def test_90_il_denominatore_resta_cohort_homes_e_unit_resta_stima(mondo, modulo):
    mondo["acceso"](ORA - 100 * GIORNO)
    case = [_casa(mondo, etichetta=f"c{i}") for i in range(4)]
    modulo["acq"].create_completed_inspection(
        mondo["a"], stima_id=case[0], completed_at=ORA - 3 * GIORNO,
        actor_user_id=mondo["op_a"])
    d = _metriche(modulo, mondo)
    assert d["unit"] == "stima"
    assert d["cohort_homes"] == 4
    assert d["inspection_homes"] == 1
    assert d["rates"]["inspection_rate"] == 0.25


# ---------------------------------------------------------------------------
# P - LA DISCESA
# ---------------------------------------------------------------------------

def test_91_la_down_rifiuta_di_distruggere_il_registro(mondo, modulo):
    """Queste righe sono l'UNICO posto in cui il sistema sa che una casa
    PRE-incarico e' diventata un incarico: nessun'altra tabella lo dice, e
    l'audit LMC-15A lo ha dimostrato. Perderle non e' reversibile.

    E non lascia niente a meta': il rifiuto arriva prima di ogni DROP, quindi
    dopo il tentativo lo schema e' quello di prima e il ponte funziona
    ancora."""
    import psycopg2
    st = mondo["stima"](mondo["a"])
    modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=st, property_id=mondo["immobile"](mondo["a"]),
        actor_user_id=mondo["op_a"])
    giu = (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")
    with pytest.raises(psycopg2.Error) as info:
        mondo["sql"](giu)
    mondo["conn"].rollback()
    assert "would be destroyed" in str(info.value)
    # lo schema e' intatto: la riga c'e' ancora e se ne puo' scrivere un'altra.
    assert len(mondo["righe"]("stima_acquisitions")) == 1
    modulo["acq"].create_inspection(
        mondo["a"], stima_id=st, scheduled_for=datetime.now(timezone.utc) + GIORNO,
        actor_user_id=mondo["op_a"])


def test_92_la_down_pulita_scende_e_la_up_risale(mondo):
    """Su tabelle vuote la discesa funziona davvero - trigger, funzioni,
    tabelle e riga di registro - e la risalita ricostruisce tutto: una
    migration che scende e non risale e' un vicolo cieco."""
    conn = mondo["conn"]
    mondo["acceso"]()
    giu = (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")
    su = (MIGRAZIONI / f"{VERSIONE}.sql").read_text(encoding="utf-8")

    mondo["sql"](giu)
    conn.commit()
    presenti = mondo["sql"](
        "SELECT to_regclass('public.stima_acquisitions') AS a,"
        "       to_regclass('public.stima_inspections')  AS i,"
        "       to_regclass('public.stime')              AS s")[0]
    assert presenti["a"] is None and presenti["i"] is None
    assert presenti["s"] is not None, "la discesa non tocca cio' che non e' suo"
    assert mondo["sql"]("SELECT count(*) AS n FROM schema_migrations "
                        "WHERE version = %s", (VERSIONE,))[0]["n"] == 0
    funzioni = mondo["sql"](
        "SELECT count(*) AS n FROM pg_proc WHERE proname IN "
        "('stima_acquisitions_guard','stima_inspections_guard',"
        " 'lmc15_assert_operator_may_act')")[0]["n"]
    assert funzioni == 0, "le funzioni restano indietro"

    mondo["sql"](su)
    conn.commit()
    risalite = mondo["sql"](
        "SELECT to_regclass('public.stima_acquisitions') AS a,"
        "       to_regclass('public.stima_inspections')  AS i")[0]
    assert risalite["a"] is not None and risalite["i"] is not None
    # e i trigger sono tornati: il ponte e' di nuovo sorvegliato.
    trigger = mondo["sql"](
        "SELECT count(*) AS n FROM pg_trigger WHERE tgname IN "
        "('trg_stima_acquisitions_guard','trg_stima_inspections_guard')")[0]["n"]
    assert trigger == 2


# ---------------------------------------------------------------------------
# Q - LE PROVE CHE IL GATE PRE-MIGRATION HA TROVATO MANCANTI
#
# Tredici punti dei settanta obbligatori non avevano una prova propria: per
# undici di essi il vincolo esisteva ed era corretto, ma "il DDL lo dice" non
# e' una prova. Ognuno ha qui il suo test, isolato, con il numero del punto.
# ---------------------------------------------------------------------------

def test_93_p04_la_down_rifiuta_anche_con_il_SOLO_sopralluogo(mondo, modulo):
    """Punto 4. L'altra meta' del test 91: nessun link, un sopralluogo solo.

    E nessun DROP a meta': dopo il rifiuto le due tabelle, le tre funzioni e
    i due trigger ci sono ancora, la riga si rilegge, e il ponte accetta
    un'altra scrittura."""
    import psycopg2
    st = mondo["stima"](mondo["a"])
    fissato = modulo["acq"].create_inspection(
        mondo["a"], stima_id=st, scheduled_for=datetime.now(timezone.utc) + GIORNO,
        actor_user_id=mondo["op_a"])
    assert mondo["righe"]("stima_acquisitions") == [], "nessun link: e' il punto"

    giu = (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")
    with pytest.raises(psycopg2.Error) as info:
        mondo["sql"](giu)
    mondo["conn"].rollback()
    assert "0 acquisition link(s) and 1 inspection(s)" in str(info.value)

    # NESSUN DROP e' avvenuto.
    presenti = mondo["sql"](
        "SELECT to_regclass('public.stima_acquisitions') AS a,"
        "       to_regclass('public.stima_inspections')  AS i")[0]
    assert presenti["a"] is not None and presenti["i"] is not None
    assert mondo["sql"](
        "SELECT count(*) AS n FROM pg_proc WHERE proname IN "
        "('stima_acquisitions_guard','stima_inspections_guard',"
        " 'lmc15_assert_operator_may_act')")[0]["n"] == 3
    assert mondo["sql"](
        "SELECT count(*) AS n FROM pg_trigger WHERE tgname IN "
        "('trg_stima_acquisitions_guard','trg_stima_inspections_guard')")[0]["n"] == 2
    # La riga e' ancora leggibile, e lo schema ancora utilizzabile.
    riga = mondo["righe"]("stima_inspections")[0]
    assert riga["id"] == fissato["id"] and riga["status"] == "scheduled"
    modulo["acq"].create_completed_inspection(
        mondo["a"], stima_id=st, completed_at=datetime.now(timezone.utc) - GIORNO,
        actor_user_id=mondo["op_a"])
    assert len(mondo["righe"]("stima_inspections")) == 2


def test_94_p06_lo_snapshot_del_sopralluogo_lo_assegna_il_database(mondo):
    """Punto 6. Come il test 42, sull'altra tabella: un valore falso arrivato
    dal chiamante non e' rifiutato, e' SOSTITUITO con la stima vera."""
    st = mondo["stima"](mondo["a"])
    mondo["sql"](
        """INSERT INTO stima_inspections
                  (stima_id, stima_id_snapshot, scheduled_for, created_by_operator_user_id)
           VALUES (%s, 999999, NOW() + interval '1 day', %s)""", (st, mondo["op_a"]))
    riga = mondo["righe"]("stima_inspections")[0]
    assert riga["stima_id_snapshot"] == st
    assert riga["stima_id_snapshot"] != 999999


def test_95_p08_lo_snapshot_del_sopralluogo_e_immutabile(mondo, modulo):
    """Punto 8, sui sopralluoghi. Un UPDATE manuale dello snapshot fallisce,
    e la stima non si riassegna nemmeno qui."""
    import psycopg2
    st = mondo["stima"](mondo["a"])
    altra = mondo["stima"](mondo["a"], via="Via Roma")
    fissato = modulo["acq"].create_inspection(
        mondo["a"], stima_id=st, scheduled_for=datetime.now(timezone.utc) + GIORNO,
        actor_user_id=mondo["op_a"])
    with pytest.raises(psycopg2.Error) as info:
        mondo["sql"]("UPDATE stima_inspections SET stima_id_snapshot = 7 WHERE id = %s",
                     (fissato["id"],))
    mondo["conn"].rollback()
    assert "immutable" in str(info.value)
    with pytest.raises(psycopg2.Error):
        mondo["sql"]("UPDATE stima_inspections SET stima_id = %s WHERE id = %s",
                     (altra, fissato["id"]))
    mondo["conn"].rollback()
    assert mondo["righe"]("stima_inspections")[0]["stima_id_snapshot"] == st


def test_96_p23_revocato_senza_data_e_irrappresentabile(mondo, modulo):
    """Punto 23, ISOLATO: `revoked`, attore e ragione presenti, `revoked_at`
    NULL. L'unica cosa che manca e' la data."""
    import psycopg2
    link = _link_nudo(mondo, modulo)
    with pytest.raises(psycopg2.errors.CheckViolation) as info:
        mondo["sql"](
            """UPDATE stima_acquisitions
                  SET link_status = 'revoked',
                      revoked_by_operator_user_id = %s,
                      revoked_reason = 'senza data'
                WHERE id = %s""", (mondo["op_a"], link["id"]))
    mondo["conn"].rollback()
    assert "stima_acq_revoked_chk" in str(info.value)


def test_97_p25_revocato_senza_ragione_e_irrappresentabile(mondo, modulo):
    """Punto 25, ISOLATO: `revoked`, data e attore presenti, `revoked_reason`
    NULL. L'unica cosa che manca e' la ragione."""
    import psycopg2
    link = _link_nudo(mondo, modulo)
    with pytest.raises(psycopg2.errors.CheckViolation) as info:
        mondo["sql"](
            """UPDATE stima_acquisitions
                  SET link_status = 'revoked',
                      revoked_at = NOW(),
                      revoked_by_operator_user_id = %s
                WHERE id = %s""", (mondo["op_a"], link["id"]))
    mondo["conn"].rollback()
    assert "stima_acq_revoked_chk" in str(info.value)


def test_98_p31_fissato_senza_data_di_appuntamento_e_irrappresentabile(mondo):
    """Punto 31. `scheduled` esige `scheduled_for`: un appuntamento senza
    data non e' un appuntamento."""
    import psycopg2
    st = mondo["stima"](mondo["a"])
    with pytest.raises(psycopg2.errors.CheckViolation) as info:
        mondo["sql"](
            """INSERT INTO stima_inspections (stima_id, status, created_by_operator_user_id)
               VALUES (%s, 'scheduled', %s)""", (st, mondo["op_a"]))
    mondo["conn"].rollback()
    assert "stima_insp_scheduled_chk" in str(info.value)


def test_99_p34_concluso_senza_data_di_registrazione_e_irrappresentabile(mondo):
    """Punto 34. Tutti gli altri campi di `completed` validi, manca solo
    `completed_recorded_at`."""
    import psycopg2
    st = mondo["stima"](mondo["a"])
    with pytest.raises(psycopg2.errors.CheckViolation) as info:
        mondo["sql"](
            """INSERT INTO stima_inspections
                  (stima_id, status, completed_at, completed_by_operator_user_id,
                   created_by_operator_user_id)
               VALUES (%s, 'completed', NOW() - interval '1 day', %s, %s)""",
            (st, mondo["op_a"], mondo["op_a"]))
    mondo["conn"].rollback()
    assert "stima_insp_completed_chk" in str(info.value)


def test_100_p35_non_si_registra_un_sopralluogo_PRIMA_che_sia_avvenuto(mondo):
    """Punto 35. `completed_recorded_at < completed_at` e' un sopralluogo
    scritto prima di essere successo."""
    import psycopg2
    st = mondo["stima"](mondo["a"])
    with pytest.raises(psycopg2.errors.CheckViolation) as info:
        mondo["sql"](
            """INSERT INTO stima_inspections
                  (stima_id, status, completed_at, completed_recorded_at,
                   completed_by_operator_user_id, created_by_operator_user_id)
               VALUES (%s, 'completed', NOW(), NOW() - interval '1 day', %s, %s)""",
            (st, mondo["op_a"], mondo["op_a"]))
    mondo["conn"].rollback()
    assert "stima_insp_completed_order_chk" in str(info.value)


def test_101_p37_annullato_senza_data_di_registrazione_e_irrappresentabile(mondo):
    """Punto 37. `scheduled_for`, `cancelled_at` e `cancelled_by` presenti,
    manca solo `cancelled_recorded_at`."""
    import psycopg2
    st = mondo["stima"](mondo["a"])
    with pytest.raises(psycopg2.errors.CheckViolation) as info:
        mondo["sql"](
            """INSERT INTO stima_inspections
                  (stima_id, status, scheduled_for, cancelled_at,
                   cancelled_by_operator_user_id, created_by_operator_user_id)
               VALUES (%s, 'cancelled', NOW() + interval '1 day', NOW(), %s, %s)""",
            (st, mondo["op_a"], mondo["op_a"]))
    mondo["conn"].rollback()
    assert "stima_insp_cancelled_chk" in str(info.value)


def test_102_p43_l_unicita_della_property_resiste_alla_cancellazione_della_stima(
        mondo, modulo):
    """Punto 43. La stima sparisce, il link resta attivo e continua a occupare
    l'unica origine attiva della property: una SECONDA stima, valida e della
    stessa agenzia, non si collega finche' il primo link non e' revocato."""
    import psycopg2
    prima = mondo["stima"](mondo["a"])
    seconda = mondo["stima"](mondo["a"], via="Via Roma")
    pr = mondo["immobile"](mondo["a"])
    modulo["acq"].create_acquisition_link(
        mondo["a"], stima_id=prima, property_id=pr, actor_user_id=mondo["op_a"])
    mondo["sql"]("DELETE FROM stime WHERE id = %s", (prima,))
    mondo["conn"].commit()
    orfano = mondo["righe"]("stima_acquisitions")[0]
    assert orfano["stima_id"] is None and orfano["link_status"] == "active"

    with pytest.raises(psycopg2.errors.UniqueViolation) as info:
        mondo["sql"](
            """INSERT INTO stima_acquisitions
                      (stima_id, property_id, linked_by_operator_user_id)
               VALUES (%s, %s, %s)""", (seconda, pr, mondo["op_a"]))
    mondo["conn"].rollback()
    assert "idx_stima_acq_active_property" in str(info.value)
    # E via repository lo stesso rifiuto, con la stessa causa.
    with pytest.raises(psycopg2.errors.UniqueViolation):
        modulo["acq"].create_acquisition_link(
            mondo["a"], stima_id=seconda, property_id=pr, actor_user_id=mondo["op_a"])


def test_103_p47_i_tassi_del_ponte_sono_null_prima_dell_accensione(mondo, modulo):
    """Punto 47, ESPLICITO sui tassi e in entrambi i casi: migration non
    applicata, e coorte che comincia prima dell'accensione."""
    _casa(mondo)
    # a) nessuna riga nel registro
    d = _metriche(modulo, mondo)
    assert d["measurement_started_at"] is None
    assert d["rates"]["inspection_rate"] is None
    assert d["rates"]["mandate_rate"] is None
    # b) accesa DOPO l'inizio della coorte
    mondo["acceso"](ORA - 5 * GIORNO)
    d = _metriche(modulo, mondo, days=30)
    assert d["measurement_started_at"] is not None
    assert d["inspection_homes"] is None and d["mandate_homes"] is None
    assert d["rates"]["inspection_rate"] is None
    assert d["rates"]["mandate_rate"] is None
    # e gli altri tassi non ne risentono: la coorte c'e', e' misurata.
    assert d["rates"]["view_rate"] == 0.0


# ---------------------------------------------------------------------------
# R - IL PLATFORM ADMIN, DALLA RICHIESTA HTTP AL DATABASE (punti 14 e 15)
#
# Qui NIENTE e' sostituito lungo la catena di autenticazione: la sessione
# nasce da `POST /api/operator-auth/login` con una password vera, l'acting da
# `POST /api/platform/agencies/{id}/enter`, e `session_from_token` - quella
# di produzione - rilegge la riga di `operator_sessions` a ogni richiesta e
# applica `_effective_agency`. L'unica cosa che cambia e' DOVE sta il
# database: un PostgreSQL usa-e-getta al posto di quello configurato. Nessun
# `dependency_overrides`, nessun `OperatorContext` costruito a mano.
# ---------------------------------------------------------------------------

#: Le migration REALI dell'autenticazione: 027 porta `agencies`,
#: `operator_users`, `operator_sessions` e `agency_memberships`; 057 il
#: registro che l'ingresso in acting scrive; 060 le due colonne dell'acting.
CATENA_AUTH = ("027_p26_agency_identity", "057_p27_platform_audit_log",
               "060_p28_acting_agency_context")

#: Il resto dello schema minimo, SENZA le tre tabelle che la 027 crea da se'.
SCHEMA_HTTP = "\n".join(
    blocco + ";" for blocco in SCHEMA_MINIMO.split(";")
    if blocco.strip() and not any(
        f"CREATE TABLE {t} (" in blocco
        for t in ("agencies", "operator_users", "agency_memberships")))

ADMIN_EMAIL = "superadmin@example.it"
ADMIN_PASSWORD = "una-password-di-prova-lunga"


@pytest.fixture(scope="module")
def db_http():
    psycopg2 = pytest.importorskip("psycopg2")
    from psycopg2.extras import DictCursor

    nome = f"lmc15_http_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')
    dsn = _dsn_per(nome)
    conn = psycopg2.connect(dsn, cursor_factory=DictCursor)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for versione in CATENA_AUTH:
                cur.execute((MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8"))
            cur.execute(SCHEMA_HTTP)
            for versione in CATENA:
                cur.execute((MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8"))
        yield {"conn": conn, "dsn": dsn}
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


@pytest.fixture
def app_http(db_http, monkeypatch):
    """L'app VERA, con ogni connessione dirottata sul database usa-e-getta.

    `database.get_connection` e' importato PER NOME in quattro moduli, quindi
    va sostituito in ciascuno: sostituire solo l'origine lascerebbe la copia
    gia' legata in `operator_auth.database` a puntare al database vero.
    """
    import psycopg2
    from psycopg2.extras import DictCursor
    from fastapi.testclient import TestClient

    import database as radice
    from core import database as core_database
    from operator_auth import database as auth_database
    from platform_admin import database as platform_database

    def connessione():
        return psycopg2.connect(db_http["dsn"], cursor_factory=DictCursor)

    for modulo_ in (radice, core_database, auth_database, platform_database):
        monkeypatch.setattr(modulo_, "get_connection", connessione)

    from main import app
    # `https`, non per estetica: il cookie di sessione e' `Secure` senza
    # condizioni (TEST e PROD servono solo HTTPS), e su un base_url `http` il
    # client non lo rimanderebbe mai. Il cookie e' quello vero, emesso dalla
    # rotta di login; e' lo schema dell'URL a dover essere quello vero.
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def piattaforma(db_http):
    """Due agenzie, un platform admin VERO senza nessuna membership, una
    stima e un immobile per agenzia. Tutto pulito prima di ogni test."""
    from core.normalization import normalize_email
    from operator_auth import security

    conn = db_http["conn"]
    with conn.cursor() as cur:
        for tabella in ("stima_acquisitions", "stima_inspections",
                        "seller_timeline_events", "owner_stima_access",
                        "owner_accounts", "leads", "stime", "properties",
                        "contacts", "operator_sessions",
                        "agency_memberships", "operator_users", "agencies"):
            cur.execute(f"DELETE FROM {tabella}")
        # `platform_audit_log` NON si svuota: e' append-only per trigger (057),
        # e va bene cosi' - le righe degli ingressi in acting dei test
        # precedenti restano, come resterebbero in produzione.
        cur.execute("INSERT INTO agencies (name, slug) VALUES ('Agenzia A','agenzia-a') "
                    "RETURNING id")
        a = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (name, slug) VALUES ('Agenzia B','agenzia-b') "
                    "RETURNING id")
        b = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO operator_users
                      (email, email_normalized, password_hash, is_platform_admin)
               VALUES (%s, %s, %s, TRUE) RETURNING id""",
            (ADMIN_EMAIL, normalize_email(ADMIN_EMAIL),
             security.hash_password(ADMIN_PASSWORD)))
        admin = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM agency_memberships WHERE operator_user_id = %s",
                    (admin,))
        assert cur.fetchone()[0] == 0, "il platform admin NON ha membership: e' il punto"

        def stima(agency):
            cur.execute("INSERT INTO stime (agency_id, comune, via, mq) "
                        "VALUES (%s,'Alba Adriatica','Via Trieste',95) RETURNING id",
                        (agency,))
            return cur.fetchone()[0]

        def immobile(agency):
            cur.execute("INSERT INTO properties (agency_id, title) VALUES (%s,'Casa') "
                        "RETURNING id", (agency,))
            return cur.fetchone()[0]

        dati = {"a": a, "b": b, "admin": admin,
                "stima_a": stima(a), "stima_b": stima(b),
                "immobile_a": immobile(a), "immobile_b": immobile(b)}

    def conta(tabella):
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {tabella}")
            return cur.fetchone()[0]

    def righe(tabella):
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {tabella} ORDER BY id")
            return [dict(r) for r in cur.fetchall()]

    dati.update(conta=conta, righe=righe)
    return dati


def _login(client):
    """La sessione VERA: password verificata, riga scritta, cookie emesso."""
    from operator_auth.enums import COOKIE_NAME
    risposta = client.post("/api/operator-auth/login",
                           json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert risposta.status_code == 204, risposta.text
    assert COOKIE_NAME in client.cookies, "nessun cookie di sessione emesso"


def test_104_p14_il_platform_admin_in_acting_scrive_il_ponte_da_HTTP_a_PostgreSQL(
        app_http, piattaforma):
    """Punto 14. HTTP -> router -> service -> repository -> PostgreSQL, con
    la sessione e l'acting risolti dal codice di produzione.

    L'agenzia effettiva viene dall'acting, non da una membership (che non
    esiste) e non dal client (che non puo' dirla). Lo si vede in tre modi:
    la scrittura sulla stima e l'immobile di A riesce; la riga porta il
    platform admin come attore; e nello STESSO acting l'immobile di B resta
    invisibile, quindi lo scope e' A e non "tutto".
    """
    client = app_http
    _login(client)

    # Prima dell'acting: nessuna agenzia effettiva, e /me lo dice.
    me = client.get("/api/operator-auth/me")
    assert me.status_code == 200, me.text
    assert me.json().get("agency_id") is None
    assert me.json().get("is_platform_admin") is True

    # L'ingresso dichiarato, dalla rotta vera: scrive `acting_agency_id`
    # sulla riga di sessione e una riga nel registro di piattaforma.
    entrata = client.post(f"/api/platform/agencies/{piattaforma['a']}/enter")
    assert entrata.status_code == 200, entrata.text
    assert entrata.json()["acting_agency_id"] == piattaforma["a"]
    sessione = piattaforma["righe"]("operator_sessions")[0]
    assert sessione["acting_agency_id"] == piattaforma["a"]
    assert sessione["acting_entered_at"] is not None
    me = client.get("/api/operator-auth/me").json()
    assert me["agency_id"] == piattaforma["a"], "l'agenzia effettiva e' quella visitata"

    prima_eventi = piattaforma["conta"]("seller_timeline_events")

    # LA SCRITTURA, dalla rotta vera.
    risposta = client.post(
        f"/api/acquisition/stime/{piattaforma['stima_a']}/links",
        json={"property_id": piattaforma["immobile_a"]})
    assert risposta.status_code == 201, risposta.text
    corpo = risposta.json()
    assert corpo["link_status"] == "active"
    assert corpo["stima_id"] == piattaforma["stima_a"]
    assert corpo["property_id"] == piattaforma["immobile_a"]
    assert "stima_id_snapshot" not in corpo
    assert not [k for k in corpo if k.endswith("operator_user_id")]

    # La riga e' davvero persistita, e l'attore e' il platform admin.
    righe = piattaforma["righe"]("stima_acquisitions")
    assert len(righe) == 1
    riga = righe[0]
    assert riga["id"] == corpo["id"]
    assert riga["linked_by_operator_user_id"] == piattaforma["admin"]
    assert riga["stima_id_snapshot"] == piattaforma["stima_a"]
    assert piattaforma["conta"]("agency_memberships") == 0, "e ancora nessuna membership"

    # La proiezione: una riga, dell'agenzia visitata, firmata dall'admin.
    assert piattaforma["conta"]("seller_timeline_events") == prima_eventi + 1
    evento = piattaforma["righe"]("seller_timeline_events")[-1]
    assert evento["agency_id"] == piattaforma["a"]
    assert evento["event_type"] == "acquisition_linked"
    assert evento["created_by"] == str(piattaforma["admin"])

    # Lo scope e' A: l'immobile di B, nello stesso acting, non esiste.
    altrui = client.post(
        f"/api/acquisition/stime/{piattaforma['stima_a']}/links",
        json={"property_id": piattaforma["immobile_b"]})
    assert altrui.status_code == 404, altrui.text
    assert piattaforma["conta"]("stima_acquisitions") == 1

    # Anche un sopralluogo, per completezza del percorso.
    fissato = client.post(
        f"/api/acquisition/stime/{piattaforma['stima_a']}/inspections",
        json={"scheduled_for": "2030-01-01T10:00:00Z"})
    assert fissato.status_code == 201, fissato.text
    assert (piattaforma["righe"]("stima_inspections")[0]["created_by_operator_user_id"]
            == piattaforma["admin"])


def test_105_p15_il_platform_admin_senza_acting_riceve_403_e_non_scrive_niente(
        app_http, piattaforma):
    """Punto 15. Stessa identita', nessun ingresso dichiarato: 403 su ogni
    scrittura del ponte, e il repository non viene raggiunto - lo provano i
    conteggi, identici prima e dopo, su entrambe le tabelle del ponte e
    sulla timeline."""
    client = app_http
    _login(client)
    me = client.get("/api/operator-auth/me").json()
    assert me["is_platform_admin"] is True and me.get("agency_id") is None
    assert piattaforma["righe"]("operator_sessions")[0]["acting_agency_id"] is None

    prima = {t: piattaforma["conta"](t) for t in
             ("stima_acquisitions", "stima_inspections", "seller_timeline_events")}

    tentativi = (
        (f"/api/acquisition/stime/{piattaforma['stima_a']}/links",
         {"property_id": piattaforma["immobile_a"]}),
        (f"/api/acquisition/stime/{piattaforma['stima_a']}/inspections",
         {"scheduled_for": "2030-01-01T10:00:00Z"}),
        (f"/api/acquisition/stime/{piattaforma['stima_a']}/inspections/completed",
         {"completed_at": "2026-01-01T10:00:00Z"}),
        ("/api/acquisition/links/1/mandate", {"mandate_signed_at": "2026-01-01T10:00:00Z"}),
        ("/api/acquisition/links/1/revoke", {"revoked_reason": "x"}),
        ("/api/acquisition/inspections/1/complete", {"completed_at": "2026-01-01T10:00:00Z"}),
        ("/api/acquisition/inspections/1/cancel", {}),
    )
    for percorso, corpo in tentativi:
        risposta = client.post(percorso, json=corpo)
        assert risposta.status_code == 403, (percorso, risposta.status_code, risposta.text)

    dopo = {t: piattaforma["conta"](t) for t in prima}
    assert dopo == prima, (prima, dopo)
    assert dopo["stima_acquisitions"] == 0
    assert dopo["seller_timeline_events"] == 0

    # E non e' un 403 "generico" della piattaforma: appena entra, la stessa
    # sessione scrive. Il rifiuto era per l'assenza di acting, non altro.
    assert client.post(f"/api/platform/agencies/{piattaforma['a']}/enter").status_code == 200
    riuscita = client.post(
        f"/api/acquisition/stime/{piattaforma['stima_a']}/links",
        json={"property_id": piattaforma["immobile_a"]})
    assert riuscita.status_code == 201, riuscita.text
    assert piattaforma["conta"]("stima_acquisitions") == 1
