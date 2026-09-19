"""LMC-1B su PostgreSQL reale: CHECK, token, tenancy, ledger, migration.

Cio' che un doppio non puo' dire: che il database ACCETTI davvero
`reason_code = 'owner_login_link'` solo dopo la 067, che il rate limit conti
righe vere, che un grant revocato o scaduto renda l'account non eleggibile,
che l'owner legacy (solo `owner_property_access`) continui a poter entrare,
che il token duri 30 minuti, che il raw non sia nel database, che si consumi
una volta sola e produca la sessione del portale esistente.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Database usa-e-getta, creato e
cancellato qui; non si passa da `database.get_connection()`, che e' il choke
point dell'applicazione.

Schema: le tabelle storiche che le migration non creano, poi 009 (OWNER),
064 (ledger P29), 065 (lifecycle parent), 066 (grant pre-incarico) e 067.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare la 067")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
VERSION = "067_lmc1b_owner_login_reason"

SCHEMA_MINIMO = """
CREATE TABLE agencies (
    id BIGSERIAL PRIMARY KEY, slug VARCHAR(80) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'active');
CREATE TABLE contacts (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    assigned_agent_id BIGINT,
    display_name VARCHAR(200), email VARCHAR(320), email_normalized VARCHAR(320),
    status VARCHAR(20) NOT NULL DEFAULT 'active', archived_at TIMESTAMPTZ,
    marketing_consent BOOLEAN, marketing_consent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT contacts_agency_scope_unq UNIQUE (agency_id, id));
CREATE TABLE leads (id BIGSERIAL PRIMARY KEY, agency_id BIGINT);
CREATE TABLE stime (
    id SERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    comune VARCHAR(100));
CREATE TABLE properties (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    title VARCHAR(200), address VARCHAR(300), city VARCHAR(120));
CREATE TABLE activities (id BIGSERIAL PRIMARY KEY);
CREATE TABLE schema_migrations (version VARCHAR(200) PRIMARY KEY);
"""

CATENA = ("009_owner_01", "064_p29_communication_foundation",
          "065_p29_service_lifecycle_parent", "066_lmc1_owner_stima_access", VERSION)


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


def _sql(version: str) -> str:
    return (MIGRAZIONI / f"{version}.sql").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"lmc1b_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')

    dsn = _dsn_per(nome)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_MINIMO)
            for versione in CATENA:
                cur.execute(_sql(versione))
                cur.execute("INSERT INTO schema_migrations (version) VALUES (%s) "
                            "ON CONFLICT DO NOTHING", (versione,))
        conn.autocommit = False
        yield {"conn": conn, "dsn": dsn}
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


@pytest.fixture
def modulo(db, monkeypatch):
    """Il dominio owner, con il choke point puntato sul database di prova."""
    import psycopg2

    from core import database as core_database
    from owner import login_service, repository

    monkeypatch.setattr(core_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    return {"login_service": login_service, "repository": repository}


@pytest.fixture
def mondo(db):
    """Due agenzie. In A: contatto+account+stima+grant. In B: un secondo
    contatto con la STESSA email, account, stima e grant. Piu' un owner legacy
    (solo `owner_property_access`) in A."""
    conn = db["conn"]
    conn.rollback()  # un test precedente puo' aver lasciato la transazione abortita
    with conn.cursor() as cur:
        # L'ordine non e' cosmetico. `communication_messages` rifiuta la DELETE
        # diretta (guardia della 064/065): le sue righe se ne vanno solo quando
        # se ne va un genitore di lifecycle, cioe' la stima o il contatto. E
        # `owner_accounts.contact_id` e' RESTRICT, quindi gli account vanno via
        # prima dei contatti.
        for tabella in ("owner_access_tokens", "owner_sessions", "owner_audit_log",
                        "owner_stima_access", "owner_property_access", "owner_accounts",
                        "stime", "contacts", "properties", "agencies"):
            cur.execute(f"DELETE FROM {tabella}")
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno') RETURNING id")
        a = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (slug) VALUES ('b-due') RETURNING id")
        b = cur.fetchone()[0]

        def contatto(agency, email, nome):
            cur.execute("INSERT INTO contacts (agency_id, display_name, email, email_normalized) "
                        "VALUES (%s,%s,%s,%s) RETURNING id", (agency, nome, email, email.lower()))
            return cur.fetchone()[0]

        def account(contact_id, status="invited"):
            cur.execute("INSERT INTO owner_accounts (contact_id, status) VALUES (%s,%s) RETURNING id",
                        (contact_id, status))
            return cur.fetchone()[0]

        k_a = contatto(a, "Mario@Example.it", "Mario Rossi")
        acc_a = account(k_a)
        cur.execute("INSERT INTO stime (agency_id, comune) VALUES (%s,'Alba Adriatica') RETURNING id", (a,))
        st_a = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_stima_access (owner_account_id, stima_id, granted_by) "
                    "VALUES (%s,%s,'LMC_PROVISIONING') RETURNING id", (acc_a, st_a))
        grant_a = cur.fetchone()[0]

        k_b = contatto(b, "mario@example.it", "Mario Rossi")
        acc_b = account(k_b)
        cur.execute("INSERT INTO stime (agency_id, comune) VALUES (%s,'Tortoreto') RETURNING id", (b,))
        st_b = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_stima_access (owner_account_id, stima_id, granted_by) "
                    "VALUES (%s,%s,'LMC_PROVISIONING') RETURNING id", (acc_b, st_b))
        grant_b = cur.fetchone()[0]

        # L'owner legacy: nessuna stima, solo un immobile.
        k_legacy = contatto(a, "legacy@example.it", "Anna Legacy")
        acc_legacy = account(k_legacy, status="active")
        cur.execute("INSERT INTO properties (agency_id, title) VALUES (%s,'Villa') RETURNING id", (a,))
        prop = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_property_access (owner_account_id, property_id) "
                    "VALUES (%s,%s) RETURNING id", (acc_legacy, prop))
        grant_legacy = cur.fetchone()[0]

        # Un contatto senza alcun grant.
        k_orfano = contatto(a, "orfano@example.it", "Nessun Grant")
        acc_orfano = account(k_orfano)
    conn.commit()
    return {"conn": conn, "a": a, "b": b, "k_a": k_a, "acc_a": acc_a, "st_a": st_a,
            "grant_a": grant_a, "k_b": k_b, "acc_b": acc_b, "st_b": st_b, "grant_b": grant_b,
            "k_legacy": k_legacy, "acc_legacy": acc_legacy, "prop": prop,
            "grant_legacy": grant_legacy, "acc_orfano": acc_orfano}


def righe(mondo, sql, params=()):
    with mondo["conn"].cursor() as cur:
        cur.execute(sql, params)
        valori = cur.fetchall()
    mondo["conn"].rollback()
    return valori


def conta(mondo, sql, params=()):
    return righe(mondo, sql, params)[0][0]


def prova(mondo, sql, params=()):
    try:
        with mondo["conn"].cursor() as cur:
            cur.execute(sql, params)
        mondo["conn"].commit()
        return None
    except Exception as exc:
        mondo["conn"].rollback()
        return exc


def messaggi(mondo):
    return righe(mondo, "SELECT agency_id, contact_id, channel, communication_type, mode, "
                        "reason_code, template_key, template_version, destination_snapshot, "
                        "subject_snapshot, rendered_body, idempotency_key, status, actor_type, "
                        "actor_user_id, stima_id FROM communication_messages ORDER BY id")


def token(mondo):
    return righe(mondo, "SELECT id, owner_account_id, token_hash, token_type, expires_at, "
                        "used_at, revoked_at, created_by FROM owner_access_tokens ORDER BY id")


# ---------------------------------------------------------------------------
# 1-3: lookup ed eleggibilita'
# ---------------------------------------------------------------------------

def test_1_email_sconosciuta_non_produce_niente(mondo, modulo):
    esito = modulo["login_service"].request_login_link("nessuno@example.it")
    assert esito["requested"] == 0
    assert token(mondo) == [] and messaggi(mondo) == []


def test_2_owner_con_grant_stima_riceve_token_e_messaggio(mondo, modulo):
    esito = modulo["login_service"].request_login_link("  MARIO@example.IT ")
    assert esito["sent"] == 2, "due agenzie, due invii"

    t = token(mondo)
    assert len(t) == 2
    assert {r[1] for r in t} == {mondo["acc_a"], mondo["acc_b"]}
    assert {r[3] for r in t} == {"login"}

    m = messaggi(mondo)
    assert len(m) == 2
    for riga in m:
        (agency_id, contact_id, channel, ctype, mode, reason, tkey, tver,
         dest, subject, body, idem, status, actor, actor_user, stima_id) = riga
        assert channel == "email" and ctype == "service" and mode == "automatic"
        assert reason == "owner_login_link"
        assert tkey == "owner_login_link" and tver == 1
        assert status == "queued" and actor == "system" and actor_user is None
        assert dest.lower() == "mario@example.it"
        assert subject and body
        assert stima_id is None, "il messaggio di login non e' legato a una stima"
    assert {r[0] for r in m} == {mondo["a"], mondo["b"]}
    assert {r[1] for r in m} == {mondo["k_a"], mondo["k_b"]}
    assert len({r[11] for r in m}) == 2, "due chiavi di idempotenza distinte"


def test_3_owner_legacy_solo_property_access_continua_a_funzionare(mondo, modulo):
    esito = modulo["login_service"].request_login_link("legacy@example.it")
    assert esito["sent"] == 1
    t = token(mondo)
    assert len(t) == 1 and t[0][1] == mondo["acc_legacy"]
    m = messaggi(mondo)
    assert len(m) == 1 and m[0][1] == mondo["k_legacy"] and m[0][0] == mondo["a"]


def test_3b_un_contatto_senza_alcun_grant_non_e_eleggibile(mondo, modulo):
    esito = modulo["login_service"].request_login_link("orfano@example.it")
    assert esito["requested"] == 0
    assert token(mondo) == [] and messaggi(mondo) == []


# ---------------------------------------------------------------------------
# 4-7: account disabilitato, grant revocato, scaduto, tenant incoerente
# ---------------------------------------------------------------------------

def test_4_account_disabled_non_riceve_niente(mondo, modulo):
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE owner_accounts SET status='disabled', disabled_at=NOW() WHERE id IN %s",
                    ((mondo["acc_a"], mondo["acc_b"]),))
    mondo["conn"].commit()
    esito = modulo["login_service"].request_login_link("mario@example.it")
    assert esito["requested"] == 0
    assert token(mondo) == [] and messaggi(mondo) == []


def test_5_grant_revocato_non_rende_eleggibili(mondo, modulo):
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE owner_stima_access SET access_status='revoked', revoked_at=NOW() "
                    "WHERE id IN %s", ((mondo["grant_a"], mondo["grant_b"]),))
    mondo["conn"].commit()
    esito = modulo["login_service"].request_login_link("mario@example.it")
    assert esito["requested"] == 0
    assert token(mondo) == []


def test_6_grant_scaduto_non_rende_eleggibili(mondo, modulo):
    with mondo["conn"].cursor() as cur:
        # Entrambe le date indietro: la 066 impone `valid_until >= valid_from`,
        # quindi un grant scaduto e' un grant nato prima, non uno con la fine
        # prima dell'inizio.
        cur.execute("UPDATE owner_stima_access SET valid_from = NOW() - INTERVAL '2 hours', "
                    "valid_until = NOW() - INTERVAL '1 hour' WHERE id IN %s",
                    ((mondo["grant_a"], mondo["grant_b"]),))
    mondo["conn"].commit()
    esito = modulo["login_service"].request_login_link("mario@example.it")
    assert esito["requested"] == 0
    assert token(mondo) == []


def test_7_un_grant_cross_tenant_non_e_nemmeno_scrivibile(mondo, modulo):
    """Il trigger della 066 rifiuta; e il lookup non potrebbe comunque vederlo,
    perche' il predicato confronta le due radici."""
    esito = prova(mondo, "INSERT INTO owner_stima_access (owner_account_id, stima_id) VALUES (%s,%s)",
                  (mondo["acc_a"], mondo["st_b"]))
    assert esito is not None and "LMC-1A owner/stima tenancy" in str(esito)

    # Lo stesso per il grant legacy: due radici che non concordano restano invisibili.
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO properties (agency_id, title) VALUES (%s,'Altrove') RETURNING id",
                    (mondo["b"],))
        prop_b = cur.fetchone()[0]
        cur.execute("DELETE FROM owner_property_access WHERE id = %s", (mondo["grant_legacy"],))
        cur.execute("INSERT INTO owner_property_access (owner_account_id, property_id) VALUES (%s,%s)",
                    (mondo["acc_legacy"], prop_b))
    mondo["conn"].commit()
    esito = modulo["login_service"].request_login_link("legacy@example.it")
    assert esito["requested"] == 0, "un grant fra due agenzie non rende eleggibili"
    assert token(mondo) == []


def test_7b_il_lookup_non_mescola_le_due_agenzie(mondo, modulo):
    modulo["login_service"].request_login_link("mario@example.it")
    coppie = righe(mondo, """SELECT m.agency_id, c.agency_id, t.owner_account_id, oa.contact_id
                               FROM communication_messages m
                               JOIN contacts c ON c.id = m.contact_id
                               JOIN owner_accounts oa ON oa.contact_id = c.id
                               JOIN owner_access_tokens t ON t.owner_account_id = oa.id
                              ORDER BY m.id""")
    for agenzia_messaggio, agenzia_contatto, _account, _contatto in coppie:
        assert agenzia_messaggio == agenzia_contatto


# ---------------------------------------------------------------------------
# 8-9: multi-agenzia e rate limit
# ---------------------------------------------------------------------------

def test_8_due_agenzie_due_ledger_separati(mondo, modulo):
    modulo["login_service"].request_login_link("mario@example.it")
    per_agenzia = dict(righe(mondo, "SELECT agency_id, count(*) FROM communication_messages "
                                    "GROUP BY agency_id ORDER BY agency_id"))
    assert per_agenzia == {mondo["a"]: 1, mondo["b"]: 1}
    # Ogni messaggio porta il link del PROPRIO token e di nessun altro.
    corpi = dict(righe(mondo, "SELECT agency_id, rendered_body FROM communication_messages"))
    assert corpi[mondo["a"]] != corpi[mondo["b"]]


def test_9_i_primi_tre_passano_e_il_quarto_no(mondo, modulo):
    for giro in range(3):
        esito = modulo["login_service"].request_login_link("legacy@example.it")
        assert esito["sent"] == 1, giro
    quarta = modulo["login_service"].request_login_link("legacy@example.it")
    assert quarta["sent"] == 0 and quarta["rate_limited"] == 1

    assert conta(mondo, "SELECT count(*) FROM owner_access_tokens WHERE owner_account_id = %s",
                 (mondo["acc_legacy"],)) == 3
    assert conta(mondo, "SELECT count(*) FROM communication_messages") == 3


def test_9b_un_token_gia_consumato_continua_a_contare(mondo, modulo):
    """Il tetto conta le RICHIESTE fatte, non i link ancora aperti: aprire il
    link non libera un posto, altrimenti il limite sarebbe una porta
    girevole."""
    for _ in range(3):
        modulo["login_service"].request_login_link("legacy@example.it")
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE owner_access_tokens SET used_at = NOW() WHERE owner_account_id = %s",
                    (mondo["acc_legacy"],))
    mondo["conn"].commit()

    esito = modulo["login_service"].request_login_link("legacy@example.it")
    assert esito["sent"] == 0 and esito["rate_limited"] == 1
    assert conta(mondo, "SELECT count(*) FROM owner_access_tokens WHERE owner_account_id = %s",
                 (mondo["acc_legacy"],)) == 3


def test_9c_un_token_revocato_continua_a_contare(mondo, modulo):
    """Stessa ragione: la revoca e' una decisione sull'accesso, non un credito
    restituito a chi ha gia' chiesto."""
    for _ in range(3):
        modulo["login_service"].request_login_link("legacy@example.it")
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE owner_access_tokens SET revoked_at = NOW() WHERE owner_account_id = %s",
                    (mondo["acc_legacy"],))
    mondo["conn"].commit()

    esito = modulo["login_service"].request_login_link("legacy@example.it")
    assert esito["sent"] == 0 and esito["rate_limited"] == 1
    assert conta(mondo, "SELECT count(*) FROM owner_access_tokens WHERE owner_account_id = %s",
                 (mondo["acc_legacy"],)) == 3


def test_9d_un_token_piu_vecchio_della_finestra_non_conta(mondo, modulo):
    """Solo il tempo libera un posto: quindici minuti, e nient'altro."""
    for _ in range(3):
        modulo["login_service"].request_login_link("legacy@example.it")
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE owner_access_tokens SET created_at = NOW() - INTERVAL '20 minutes', "
                    "expires_at = NOW() - INTERVAL '5 minutes' WHERE owner_account_id = %s",
                    (mondo["acc_legacy"],))
    mondo["conn"].commit()

    esito = modulo["login_service"].request_login_link("legacy@example.it")
    assert esito["sent"] == 1
    assert conta(mondo, "SELECT count(*) FROM owner_access_tokens WHERE owner_account_id = %s",
                 (mondo["acc_legacy"],)) == 4


def test_9e_il_limite_di_un_account_non_tocca_l_altro(mondo, modulo):
    """Il tetto e' per owner_account. Mario e' contatto di due agenzie: se il
    suo account in A ha gia' esaurito i posti, quello in B riceve comunque il
    proprio link."""
    with mondo["conn"].cursor() as cur:
        for n in range(3):
            cur.execute(
                "INSERT INTO owner_access_tokens (owner_account_id, token_hash, token_type, "
                "expires_at, created_by) VALUES (%s, %s, 'login', NOW() + INTERVAL '30 minutes', "
                "'LMC_LOGIN')", (mondo["acc_a"], f"{n:064d}"))
    mondo["conn"].commit()

    esito = modulo["login_service"].request_login_link("mario@example.it")
    assert esito["requested"] == 2
    assert esito["sent"] == 1 and esito["rate_limited"] == 1

    nuovi = righe(mondo, "SELECT owner_account_id FROM owner_access_tokens "
                         "WHERE created_by = 'LMC_LOGIN' AND token_hash NOT LIKE '0%%' "
                         "ORDER BY id")
    assert [r[0] for r in nuovi] == [mondo["acc_b"]]
    messaggi_per_agenzia = dict(righe(mondo, "SELECT agency_id, count(*) FROM communication_messages "
                                             "GROUP BY agency_id"))
    assert messaggi_per_agenzia == {mondo["b"]: 1}


# ---------------------------------------------------------------------------
# 10-13: il token e la sessione
# ---------------------------------------------------------------------------

def test_10_il_token_dura_trenta_minuti(mondo, modulo):
    modulo["login_service"].request_login_link("legacy@example.it")
    creato, scade = righe(mondo, "SELECT created_at, expires_at FROM owner_access_tokens")[0]
    durata = scade - creato
    assert timedelta(minutes=29) < durata < timedelta(minutes=31), durata


def test_11_il_raw_token_non_e_nel_database(mondo, modulo):
    modulo["login_service"].request_login_link("legacy@example.it")
    hash_salvato = token(mondo)[0][2]
    corpo = righe(mondo, "SELECT rendered_body FROM communication_messages")[0][0]
    import re as _re
    raw = _re.search(r"token=([A-Za-z0-9_\-%]+)", corpo).group(1)
    assert len(raw) >= 32
    assert raw != hash_salvato
    assert raw not in str(token(mondo))
    from owner.security import hash_secret
    from urllib.parse import unquote
    assert hash_secret(unquote(raw)) == hash_salvato, "in tabella c'e' lo sha256, non il segreto"


def test_12_il_token_si_consuma_una_volta_sola_e_apre_la_sessione(mondo, modulo):
    import re as _re
    from urllib.parse import unquote

    modulo["login_service"].request_login_link("legacy@example.it")
    corpo = righe(mondo, "SELECT rendered_body FROM communication_messages")[0][0]
    raw = unquote(_re.search(r"token=([A-Za-z0-9_\-%]+)", corpo).group(1))

    sessione, cookie = modulo["repository"].consume_token(raw)
    assert sessione["owner_account_id"] == mondo["acc_legacy"]
    assert cookie and cookie != raw
    assert conta(mondo, "SELECT count(*) FROM owner_sessions") == 1
    assert righe(mondo, "SELECT used_at FROM owner_access_tokens")[0][0] is not None

    from core.exceptions import NotFoundError
    with pytest.raises(NotFoundError):
        modulo["repository"].consume_token(raw)
    assert conta(mondo, "SELECT count(*) FROM owner_sessions") == 1


def test_13_la_sessione_e_quella_del_portale_esistente(mondo, modulo):
    import re as _re
    from urllib.parse import unquote

    modulo["login_service"].request_login_link("legacy@example.it")
    corpo = righe(mondo, "SELECT rendered_body FROM communication_messages")[0][0]
    raw = unquote(_re.search(r"token=([A-Za-z0-9_\-%]+)", corpo).group(1))
    _sessione, cookie = modulo["repository"].consume_token(raw)

    letta = modulo["repository"].get_session(cookie)
    assert letta["owner_account_id"] == mondo["acc_legacy"]
    assert righe(mondo, "SELECT status FROM owner_accounts WHERE id = %s",
                 (mondo["acc_legacy"],))[0][0] == "active"


def test_13b_un_token_scaduto_non_apre_niente(mondo, modulo):
    import re as _re
    from urllib.parse import unquote
    from core.exceptions import NotFoundError

    modulo["login_service"].request_login_link("legacy@example.it")
    corpo = righe(mondo, "SELECT rendered_body FROM communication_messages")[0][0]
    raw = unquote(_re.search(r"token=([A-Za-z0-9_\-%]+)", corpo).group(1))
    with mondo["conn"].cursor() as cur:
        # Anche `created_at` indietro: la 009 impone `expires_at > created_at`,
        # quindi un token scaduto e' un token nato prima, non uno che scade
        # prima di nascere.
        cur.execute("UPDATE owner_access_tokens SET created_at = NOW() - INTERVAL '40 minutes', "
                    "expires_at = NOW() - INTERVAL '1 minute'")
    mondo["conn"].commit()
    with pytest.raises(NotFoundError):
        modulo["repository"].consume_token(raw)
    assert conta(mondo, "SELECT count(*) FROM owner_sessions") == 0


# ---------------------------------------------------------------------------
# 15: il fallimento dell'enqueue non lascia token inutilizzabili
# ---------------------------------------------------------------------------

def test_15_un_enqueue_fallito_annulla_anche_il_token(mondo, modulo, monkeypatch):
    def esplode(*_a, **_k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(modulo["login_service"].communication_service, "enqueue", esplode)
    esito = modulo["login_service"].request_login_link("legacy@example.it")

    assert esito["failed"] == 1 and esito["sent"] == 0
    assert token(mondo) == [], "nessun token orfano: stessa transazione del messaggio"
    assert messaggi(mondo) == []
    assert conta(mondo, "SELECT count(*) FROM owner_audit_log") == 0


def test_15b_dopo_un_fallimento_la_richiesta_successiva_riparte(mondo, modulo, monkeypatch):
    originale = modulo["login_service"].communication_service.enqueue
    monkeypatch.setattr(modulo["login_service"].communication_service, "enqueue",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("ledger down")))
    modulo["login_service"].request_login_link("legacy@example.it")
    monkeypatch.setattr(modulo["login_service"].communication_service, "enqueue", originale)
    esito = modulo["login_service"].request_login_link("legacy@example.it")
    assert esito["sent"] == 1
    assert len(token(mondo)) == 1 and len(messaggi(mondo)) == 1


# ---------------------------------------------------------------------------
# 16: il CHECK del database
# ---------------------------------------------------------------------------

def test_16_il_database_ammette_owner_login_link_e_rifiuta_gli_inventati(mondo):
    base = ("INSERT INTO communication_messages (agency_id, contact_id, channel, direction, "
            "communication_type, mode, reason_code, rendered_body, destination_snapshot, "
            "subject_snapshot, idempotency_key, actor_type) "
            "VALUES (%s,%s,'email','outbound','service','automatic',%s,'corpo','a@b.it','ogg',%s,'system')")
    assert prova(mondo, base, (mondo["a"], mondo["k_a"], "owner_login_link", "k-ok")) is None
    rifiutato = prova(mondo, base, (mondo["a"], mondo["k_a"], "owner_magic_link", "k-no"))
    assert rifiutato is not None and "reason_code_chk" in str(rifiutato)
    # I motivi storici restano ammessi.
    assert prova(mondo, base, (mondo["a"], mondo["k_a"], "stima_pdf", "k-pdf")) is None


# ---------------------------------------------------------------------------
# 17: migration up / down / up
# ---------------------------------------------------------------------------

def test_17_la_down_toglie_il_valore_e_la_up_lo_rimette(db, mondo):
    psycopg2 = pytest.importorskip("psycopg2")
    db["conn"].rollback()
    conn = psycopg2.connect(db["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SET lock_timeout = '5s'")
            cur.execute((MIGRAZIONI / f"{VERSION}_down.sql").read_text(encoding="utf-8"))
            cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conname = 'communication_messages_reason_code_chk'")
            assert "owner_login_link" not in cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM schema_migrations WHERE version = %s", (VERSION,))
            assert cur.fetchone()[0] == 0

            cur.execute(_sql(VERSION))
            cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (VERSION,))
            cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conname = 'communication_messages_reason_code_chk'")
            definizione = cur.fetchone()[0]
            assert "owner_login_link" in definizione and "stima_pdf" in definizione
            cur.execute(_sql(VERSION))  # idempotente
    finally:
        conn.close()


def test_17b_la_down_rifiuta_se_esistono_messaggi_di_login(db, mondo, modulo):
    psycopg2 = pytest.importorskip("psycopg2")
    modulo["login_service"].request_login_link("legacy@example.it")
    assert conta(mondo, "SELECT count(*) FROM communication_messages "
                        "WHERE reason_code = 'owner_login_link'") == 1

    db["conn"].rollback()
    conn = psycopg2.connect(db["dsn"])
    conn.autocommit = True
    try:
        # La down si bracketta da sola: quando la sonda solleva, il suo BEGIN e'
        # ancora aperto su QUESTA connessione e va chiuso, altrimenti ogni
        # comando successivo trova la transazione abortita.
        with conn.cursor() as cur:
            cur.execute("SET lock_timeout = '5s'")
            with pytest.raises(Exception) as errore:
                cur.execute((MIGRAZIONI / f"{VERSION}_down.sql").read_text(encoding="utf-8"))
            assert "owner_login_link" in str(errore.value)
            # `conn.rollback()` non basta: con autocommit psycopg2 crede che non
            # ci sia nessuna transazione, mentre il BEGIN lo ha aperto lo script.
            cur.execute("ROLLBACK")
        # Nulla e' cambiato: il vincolo e il ledger sono intatti.
        with conn.cursor() as cur:
            cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conname = 'communication_messages_reason_code_chk'")
            assert "owner_login_link" in cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM communication_messages")
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 20-23: nessuna regressione sui percorsi vicini
# ---------------------------------------------------------------------------

def test_20_stima_pdf_continua_ad_accodarsi_come_prima(db, mondo, modulo):
    """Il percorso P29 del funnel pubblico, invariato: stesso contesto
    `public_stima`, stesso reason code, sullo stesso ledger che ora ammette
    anche il login."""
    from communication import service as communication_service
    from operator_auth.context import SystemAgencyContext

    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO stime (agency_id, comune) VALUES (%s,'Martinsicuro') RETURNING id",
                    (mondo["a"],))
        stima = cur.fetchone()[0]
    mondo["conn"].commit()

    import psycopg2
    from psycopg2.extras import RealDictCursor

    ctx = SystemAgencyContext(agency_id=mondo["a"], origin="public_stima")
    conn = psycopg2.connect(db["dsn"])
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        esito = communication_service.enqueue(
            ctx, contact_id=mondo["k_a"], channel="email", communication_type="service",
            mode="automatic", reason_code="stima_pdf", rendered_body="<p>pdf</p>",
            destination_snapshot="mario@example.it", subject_snapshot="La tua stima",
            idempotency_key=f"stima_email_cliente:{stima}", stima_id=stima,
            metadata={"pdf_url": "https://esempio.test/x.pdf"}, cur=cur)
        conn.commit()
    finally:
        conn.close()
    assert esito["created"] is True
    assert conta(mondo, "SELECT count(*) FROM communication_messages "
                        "WHERE reason_code = 'stima_pdf'") == 1


def test_23_il_provisioning_lmc1a_continua_a_funzionare(mondo, modulo):
    from owner import provisioning
    from operator_auth.context import SystemAgencyContext

    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO contacts (agency_id, display_name, email, email_normalized) "
                    "VALUES (%s,'Nuovo','nuovo@example.it','nuovo@example.it') RETURNING id",
                    (mondo["a"],))
        contatto = cur.fetchone()[0]
        cur.execute("INSERT INTO stime (agency_id, comune) VALUES (%s,'Giulianova') RETURNING id",
                    (mondo["a"],))
        stima = cur.fetchone()[0]
    mondo["conn"].commit()

    ctx = SystemAgencyContext(agency_id=mondo["a"], origin="public_stima")
    esito = provisioning.provision_for_public_stima(
        ctx, stima_id=stima, bridge_result={"status": "linked", "contact_id": contatto})
    assert esito["status"] == "provisioned"

    # E il nuovo owner puo' subito chiedere il proprio link.
    login = modulo["login_service"].request_login_link("nuovo@example.it")
    assert login["sent"] == 1
