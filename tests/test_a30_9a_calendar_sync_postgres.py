"""A30-9A - le fondamenta della sincronizzazione calendario su PostgreSQL VERO.

Opt-in: senza `P29_TEST_DSN` si salta tutto. MAI PROD: il database e' quello
usa-e-getta di A30-2 (`test_a30_2_appointments_postgres.db`, 072 applicata),
su cui questo modulo applica la migration 074 VERA. Nessuna connessione nuova
nasce qui (P26 H11): tutto passa da `core.database.get_connection()`, che il
fixture `mondo` punta al database di prova.

Gli appuntamenti nascono, si spostano, si riassegnano e si chiudono dalle rotte
vere dell'Agenda (montate su un'app di prova); la sincronizzazione e'
esercitata DIRETTAMENTE (repository + riconciliatore + provider finto): in
A30-9A non c'e' ancora alcun hook nell'Agenda.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import timedelta

import pytest

from tests.test_a30_2_appointments_postgres import (  # noqa: F401 - fixture
    DSN, MIGRAZIONI, chiave, db, futuro, mondo, ore)

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare A30-9A")

UP = MIGRAZIONI / "074_a30_9a_calendar_sync.sql"
DOWN = MIGRAZIONI / "074_a30_9a_calendar_sync_down.sql"
TABELLE = ("calendar_connections", "calendar_oauth_states", "appointment_calendar_sync")
#: Il namespace di deployment che il futuro chiamante (A30-9B) passera' dalla
#: configurazione runtime; qui lo passa il test, esplicitamente.
NS = "stima360-test"


# ---------------------------------------------------------------------------
# banco
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db_074(db):
    with db["conn"].cursor() as cur:
        cur.execute(UP.read_text(encoding="utf-8"))
    return db


@pytest.fixture
def pulito(db_074):
    """Prima di `mondo`, che svuota operatori e membership: le connessioni li
    referenziano (RESTRICT)."""
    with db_074["conn"].cursor() as cur:
        cur.execute("DELETE FROM appointment_calendar_sync")
        cur.execute("DELETE FROM calendar_oauth_states")
        cur.execute("DELETE FROM calendar_connections")
    return db_074


@pytest.fixture
def w(pulito, mondo):
    return mondo


@pytest.fixture
def ring(monkeypatch):
    from cryptography.fernet import Fernet

    from calendar_sync import crypto
    raw = f"k2:{Fernet.generate_key().decode()},k1:{Fernet.generate_key().decode()}"
    monkeypatch.setenv(crypto.ENV_KEYS, raw)
    return crypto.parse_keyring(raw)


@pytest.fixture
def http(w):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from appointments.router import router
    from operator_auth.dependencies import require_operator

    app = FastAPI()
    app.include_router(router)
    stato = {"ctx": w["ctx"]("giorgio")}
    app.dependency_overrides[require_operator] = lambda: stato["ctx"]
    client = TestClient(app)

    def come(chi):
        stato["ctx"] = w["ctx"](chi)
        return client

    return come


def _crea(http, **kw):
    corpo = {"appointment_type": "inspection", "status": "scheduled",
             "start_at": ore(10).isoformat(), "end_at": ore(11).isoformat(),
             "client_request_id": chiave()}
    corpo.update(kw)
    r = http("giorgio").post("/api/appointments", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()


def _azione(http, a, azione, **corpo):
    percorso = {"no_show": "no-show"}.get(azione, azione)
    corpo.setdefault("version", a["version"])
    r = http("giorgio").post(f"/api/appointments/{a['id']}/{percorso}", json=corpo)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _sposta(http, a, ora):
    return _azione(http, a, "reschedule", start_at=ore(ora).isoformat(),
                   end_at=ore(ora + 1).isoformat())


def _cur():
    from core.database import core_cursor
    return core_cursor


def _tx(funzione, *args, **kw):
    from core.database import core_cursor
    with core_cursor(commit=True) as (_, cur):
        return funzione(cur, *args, **kw)


def _collega(w, ring, chi, agenzia="a"):
    from calendar_sync import repository
    return _tx(lambda cur: repository.upsert_connection(
        cur, agency_id=w[agenzia], user_id=w[chi], provider_subject=f"sub-{chi}",
        refresh_token=ring.encrypt(f"refresh-segreto-{chi}"),
        granted_scopes=["https://www.googleapis.com/auth/calendar.events"]))


def _dirty(w, appointment_id, agenzia="a"):
    from calendar_sync import repository
    return _tx(repository.mark_dirty_with_cursor, w[agenzia], appointment_id,
               deployment_namespace=NS)


def _riga(w, root):
    return dict(w["sql"]("SELECT * FROM appointment_calendar_sync WHERE chain_root_appointment_id=%s",
                         (root,))[0])


def _giro(fake, ring, **kw):
    from calendar_sync import service
    return service.run_once(provider=fake, keyring=ring, **kw)


def _impronta_agenda(w):
    """Tutto cio' che la sincronizzazione NON deve toccare."""
    return w["sql"]("SELECT (SELECT md5(string_agg(x::text, '|' ORDER BY x.id)) "
                    "FROM appointments x), (SELECT count(*) FROM appointment_events), "
                    "(SELECT md5(string_agg(e::text, '|' ORDER BY e.id)) FROM appointment_events e)")[0][:]


# ---------------------------------------------------------------------------
# A - MIGRATION (1, 2)
# ---------------------------------------------------------------------------

def test_01_02_migration_up_down_up(w, ring, http):
    presenti = {r[0] for r in w["sql"](
        "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    assert set(TABELLE) <= presenti
    colonne_072 = w["sql"]("SELECT column_name FROM information_schema.columns "
                           "WHERE table_name='appointments' AND column_name LIKE 'google_%%' "
                           "ORDER BY 1")
    # la down rifiuta se ci sono dati, e non cambia nulla
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    import psycopg2
    with pytest.raises(psycopg2.Error, match="would be destroyed"):
        with w["conn"].cursor() as cur:
            cur.execute(DOWN.read_text(encoding="utf-8"))
    with w["conn"].cursor() as cur:           # la down apre BEGIN: si chiude qui
        cur.execute("ROLLBACK")
    assert w["sql"]("SELECT count(*) FROM appointment_calendar_sync")[0][0] == 1
    w["sql"]("DELETE FROM appointment_calendar_sync")
    with w["conn"].cursor() as cur:
        cur.execute(DOWN.read_text(encoding="utf-8"))
    dopo = {r[0] for r in w["sql"]("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    assert not (set(TABELLE) & dopo)
    assert w["sql"]("SELECT proname FROM pg_proc WHERE proname IN "
                    "('calendar_connections_guard','appointment_calendar_sync_guard')") == []
    # le colonne google_* della 072 restano, identiche
    assert w["sql"]("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='appointments' AND column_name LIKE 'google_%%' "
                    "ORDER BY 1") == colonne_072
    with w["conn"].cursor() as cur:
        cur.execute(UP.read_text(encoding="utf-8"))
    assert set(TABELLE) <= {r[0] for r in w["sql"](
        "SELECT tablename FROM pg_tables WHERE schemaname='public'")}


# ---------------------------------------------------------------------------
# B - CONNESSIONI E TOKEN (3, 6, 43)
# ---------------------------------------------------------------------------

def test_03_una_connessione_per_agenzia_operatore_provider(w, ring):
    import psycopg2
    c1 = _collega(w, ring, "luca")
    c2 = _collega(w, ring, "luca")                 # ricollegamento: stessa riga
    assert c1["id"] == c2["id"]
    assert w["sql"]("SELECT count(*) FROM calendar_connections")[0][0] == 1
    with pytest.raises(psycopg2.errors.UniqueViolation):
        w["sql"]("INSERT INTO calendar_connections (agency_id, user_id, status) "
                 "VALUES (%s, %s, 'needs_reauth')", (w["a"], w["luca"]))
    w["conn"].rollback()


def test_06_solo_ciphertext_mai_il_token_in_chiaro(w, ring):
    from calendar_sync import repository
    c = _collega(w, ring, "luca")
    assert "refresh_token_ciphertext" not in c                    # mai fuori dal repository
    riga = w["sql"]("SELECT refresh_token_ciphertext, token_key_id, provider_subject, "
                    "calendar_id, granted_scopes FROM calendar_connections")[0]
    assert b"refresh-segreto-luca" not in bytes(riga[0]) and riga[1] == "k2"
    assert riga[3] == "primary" and riga[4] == ["https://www.googleapis.com/auth/calendar.events"]
    segreto = _tx(repository.connection_secret, w["a"], c["id"])
    assert ring.decrypt(segreto) == "refresh-segreto-luca"
    assert _tx(repository.connection_secret, w["b"], c["id"]) is None      # altra agenzia
    with pytest.raises(TypeError):
        _tx(lambda cur: repository.upsert_connection(
            cur, agency_id=w["a"], user_id=w["luca"], provider_subject="s",
            refresh_token="in-chiaro"))
    # CHECK: connected senza token, disconnected con token
    import psycopg2
    for sql in ("UPDATE calendar_connections SET refresh_token_ciphertext=NULL, token_key_id=NULL",
                "UPDATE calendar_connections SET status='disconnected', disconnected_at=NOW()"):
        with pytest.raises(psycopg2.errors.CheckViolation):
            w["sql"](sql)
        w["conn"].rollback()
    _tx(repository.mark_connection_disconnected, w["a"], c["id"])
    assert w["sql"]("SELECT status, refresh_token_ciphertext, token_key_id "
                    "FROM calendar_connections")[0][:] == ["disconnected", None, None]


def test_43_connessione_solo_per_una_membership_attiva_della_stessa_agenzia(w, ring):
    import psycopg2
    from calendar_sync import repository
    # un operatore dell'agenzia b in a: nessuna membership (il trigger della
    # 074 lo ferma per primo; la FK composita resta la garanzia strutturale)
    with pytest.raises(psycopg2.Error, match="no active membership|foreign key"):
        _tx(lambda cur: repository.upsert_connection(
            cur, agency_id=w["a"], user_id=w["estraneo"], provider_subject="s",
            refresh_token=ring.encrypt("t")))
    # un operatore di a nell'agenzia b
    with pytest.raises(psycopg2.Error, match="no active membership|foreign key"):
        _tx(lambda cur: repository.upsert_connection(
            cur, agency_id=w["b"], user_id=w["luca"], provider_subject="s",
            refresh_token=ring.encrypt("t")))
    # la FK composita da sola (uno stato che il trigger non controlla)
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        w["sql"]("INSERT INTO calendar_connections (agency_id, user_id, status) "
                 "VALUES (%s, %s, 'needs_reauth')", (w["a"], w["estraneo"]))
    w["conn"].rollback()
    # membership sospesa: il trigger della 074 rifiuta `connected`
    w["sql"]("UPDATE agency_memberships SET status='suspended' WHERE operator_user_id=%s",
             (w["marta"],))
    with pytest.raises(psycopg2.Error, match="no active membership"):
        _collega(w, ring, "marta")
    # identita' immutabile
    c = _collega(w, ring, "luca")
    with pytest.raises(psycopg2.Error, match="immutable"):
        w["sql"]("UPDATE calendar_connections SET user_id=%s WHERE id=%s", (w["giorgio"], c["id"]))
    w["conn"].rollback()


# ---------------------------------------------------------------------------
# C - STATE OAUTH (4, 5)
# ---------------------------------------------------------------------------

def test_04_05_state_oauth_hash_ttl_monouso_legato(w, ring):
    import psycopg2
    from calendar_sync import repository, service
    grezzo, h = service.new_oauth_state()
    assert h == service.hash_oauth_state(grezzo) and grezzo not in h
    riga = _tx(lambda cur: repository.create_oauth_state(
        cur, agency_id=w["a"], user_id=w["luca"], state_hash=h,
        code_verifier=ring.encrypt("verifier-pkce")))
    assert riga["expires_at"] > riga["created_at"]
    salvato = w["sql"]("SELECT * FROM calendar_oauth_states")[0]
    assert grezzo not in str(salvato[:]) and b"verifier-pkce" not in bytes(salvato["code_verifier_ciphertext"])
    with pytest.raises(psycopg2.errors.UniqueViolation):             # 4: hash unico
        _tx(lambda cur: repository.create_oauth_state(
            cur, agency_id=w["a"], user_id=w["marta"], state_hash=h,
            code_verifier=ring.encrypt("x")))
    with pytest.raises(psycopg2.errors.CheckViolation):              # 5: TTL massimo 1 ora
        _tx(lambda cur: repository.create_oauth_state(
            cur, agency_id=w["a"], user_id=w["luca"], state_hash="0" * 64,
            code_verifier=ring.encrypt("x"), ttl_seconds=7200))
    # legato: altro operatore o altra agenzia -> niente, e lo state resta valido
    assert _tx(lambda cur: repository.consume_oauth_state(
        cur, agency_id=w["a"], user_id=w["marta"], state_hash=h)) is None
    assert _tx(lambda cur: repository.consume_oauth_state(
        cur, agency_id=w["b"], user_id=w["luca"], state_hash=h)) is None
    usato = _tx(lambda cur: repository.consume_oauth_state(
        cur, agency_id=w["a"], user_id=w["luca"], state_hash=h))
    assert ring.decrypt((usato["code_verifier_ciphertext"], usato["token_key_id"])) == "verifier-pkce"
    assert _tx(lambda cur: repository.consume_oauth_state(
        cur, agency_id=w["a"], user_id=w["luca"], state_hash=h)) is None       # monouso
    # scaduto
    _, h2 = service.new_oauth_state()
    _tx(lambda cur: repository.create_oauth_state(
        cur, agency_id=w["a"], user_id=w["luca"], state_hash=h2,
        code_verifier=ring.encrypt("v"), ttl_seconds=1))
    # nel passato, TTL ancora coerente (le SET leggono i valori vecchi)
    w["sql"]("UPDATE calendar_oauth_states SET created_at = created_at - INTERVAL '1 hour', "
             "expires_at = created_at - INTERVAL '59 minutes' WHERE state_hash=%s", (h2,))
    assert _tx(lambda cur: repository.consume_oauth_state(
        cur, agency_id=w["a"], user_id=w["luca"], state_hash=h2)) is None


# ---------------------------------------------------------------------------
# D - CATENA (11-15)
# ---------------------------------------------------------------------------

def test_11_15_catena_a_b_c_una_sola_riga(w, http):
    from calendar_sync import repository, service
    a = _crea(http, assigned_user_id=w["luca"])
    riga = _tx(repository.ensure_sync_row, w["a"], a["id"], deployment_namespace=NS)
    assert (riga["chain_root_appointment_id"], riga["current_appointment_id"]) == (a["id"], a["id"])
    assert riga["remote_event_id"] == service.deterministic_event_id(NS, w["a"], a["id"])
    b = _sposta(http, a, 13)
    assert _tx(repository.move_current_appointment_on_reschedule, w["a"], a["id"], b["id"])[
        "current_appointment_id"] == b["id"]
    c = _sposta(http, b, 15)
    for x in (a, b, c):
        assert _tx(repository.chain_root, w["a"], x["id"]) == a["id"]
        assert _tx(repository.chain_tip, w["a"], a["id"]) == c["id"]
    # senza move esplicito: ensure/mark_dirty dalla riga C riallinea alla punta
    for x in (a, b, c):
        riga = _tx(repository.mark_dirty_with_cursor, w["a"], x["id"], deployment_namespace=NS)
        assert (riga["chain_root_appointment_id"], riga["current_appointment_id"]) == (a["id"], c["id"])
    assert w["sql"]("SELECT count(*) FROM appointment_calendar_sync")[0][0] == 1
    assert riga["remote_event_id"] == service.deterministic_event_id(NS, w["a"], a["id"])


def test_11b_la_catena_e_protetta_dal_database(w, http):
    import psycopg2
    from calendar_sync import repository, service
    a = _crea(http, assigned_user_id=w["luca"])
    b = _sposta(http, a, 13)
    altro = _crea(http, assigned_user_id=w["marta"], start_at=ore(8).isoformat(),
                  end_at=ore(9).isoformat())
    evento = service.deterministic_event_id(NS, w["a"], b["id"])
    # la radice deve essere una radice
    with pytest.raises(psycopg2.Error, match="not the root"):
        w["sql"]("INSERT INTO appointment_calendar_sync (agency_id, chain_root_appointment_id, "
                 "current_appointment_id, remote_event_id) VALUES (%s,%s,%s,%s)",
                 (w["a"], b["id"], b["id"], evento))
    w["conn"].rollback()
    riga = _tx(repository.ensure_sync_row, w["a"], a["id"], deployment_namespace=NS)
    # la riga viva deve discendere dalla radice
    with pytest.raises(psycopg2.Error, match="does not descend"):
        w["sql"]("UPDATE appointment_calendar_sync SET current_appointment_id=%s WHERE id=%s",
                 (altro["id"], riga["id"]))
    w["conn"].rollback()
    # radice e id evento immutabili
    with pytest.raises(psycopg2.Error, match="immutable"):
        w["sql"]("UPDATE appointment_calendar_sync SET remote_event_id='s360aaaaa' WHERE id=%s",
                 (riga["id"],))
    w["conn"].rollback()
    with pytest.raises(ValueError):
        _tx(repository.move_current_appointment_on_reschedule, w["a"], altro["id"], b["id"])


# ---------------------------------------------------------------------------
# E - TENANT (42)
# ---------------------------------------------------------------------------

def test_42_isolamento_tenant(w, http, ring):
    import psycopg2
    from calendar_sync import repository, service
    a = _crea(http, assigned_user_id=w["luca"])
    riga = _dirty(w, a["id"])
    c = _collega(w, ring, "luca")
    # agenzia b: non vede la riga, non la catena, non la connessione
    assert _tx(repository.get_sync_row, w["b"], a["id"]) is None
    assert _tx(repository.get_sync_row_by_id, w["b"], riga["id"]) is None
    assert _tx(repository.get_connection, w["b"], c["id"]) is None
    assert _tx(repository.connection_for_user, w["b"], w["luca"]) is None
    with pytest.raises(repository.ChainNotFound):
        _tx(repository.mark_dirty_with_cursor, w["b"], a["id"], deployment_namespace=NS)
    # appuntamento di un'altra agenzia come radice/viva: FK composita
    libero = _crea(http, assigned_user_id=w["marta"])
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        w["sql"]("INSERT INTO appointment_calendar_sync (agency_id, chain_root_appointment_id, "
                 "current_appointment_id, remote_event_id) VALUES (%s,%s,%s,%s)",
                 (w["b"], libero["id"], libero["id"],
                  service.deterministic_event_id(NS, w["b"], libero["id"])))
    w["conn"].rollback()
    # connessione di un'altra agenzia come remoto: FK composita
    ext = _tx(lambda cur: repository.upsert_connection(
        cur, agency_id=w["b"], user_id=w["estraneo"], provider_subject="s",
        refresh_token=ring.encrypt("t")))
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        w["sql"]("UPDATE appointment_calendar_sync SET remote_connection_id=%s, "
                 "remote_calendar_id='primary' WHERE id=%s", (ext["id"], riga["id"]))
    w["conn"].rollback()
    # il worker limitato a un'agenzia non prende righe altrui
    assert _tx(repository.claim_batch, agency_id=w["b"]) == []


# ---------------------------------------------------------------------------
# F - GENERAZIONI, CLAIM, LEASE, CAS (16-21)
# ---------------------------------------------------------------------------

def test_16_mark_dirty_incrementa_la_generazione_e_riapre(w, http):
    a = _crea(http, assigned_user_id=w["luca"])
    g1 = _dirty(w, a["id"])["dirty_generation"]
    r = _dirty(w, a["id"])
    assert r["dirty_generation"] == g1 + 1 and r["status"] == "pending"
    w["sql"]("UPDATE appointment_calendar_sync SET status='failed', attempt_count=8, "
             "last_error_code='x'")
    r = _dirty(w, a["id"])
    assert (r["status"], r["attempt_count"], r["last_error_code"]) == ("pending", 0, None)


def test_17_mark_dirty_concorrente_nessun_incremento_perso(w, http):
    from calendar_sync import repository
    from core import database as core_database
    a = _crea(http, assigned_user_id=w["luca"])
    base = _dirty(w, a["id"])["dirty_generation"]
    prima = core_database.get_connection()
    try:
        with prima.cursor(cursor_factory=__import__("psycopg2.extras").extras.RealDictCursor) as cur:
            repository.mark_dirty_with_cursor(cur, w["a"], a["id"], deployment_namespace=NS)        # riga bloccata
            fatto = []
            filo = threading.Thread(target=lambda: fatto.append(_dirty(w, a["id"])))
            filo.start()
            filo.join(timeout=1)
            assert filo.is_alive()                                          # in attesa del lock
        prima.commit()
        filo.join(timeout=20)
    finally:
        prima.close()
    assert fatto[0]["dirty_generation"] == base + 2


def test_18_19_claim_skip_locked_un_solo_worker(w, http):
    from psycopg2.extras import RealDictCursor

    from calendar_sync import repository
    from core import database as core_database
    ids = []
    for ora in (8, 13, 15, 17):
        a = _crea(http, assigned_user_id=w["luca"], start_at=ore(ora).isoformat(),
                  end_at=ore(ora + 1).isoformat())
        ids.append(_dirty(w, a["id"])["id"])
    uno = core_database.get_connection()
    try:
        cur1 = uno.cursor(cursor_factory=RealDictCursor)
        primo = repository.claim_batch(cur1, limit=2)                      # tx aperta
        secondo = _tx(repository.claim_batch, limit=10)                    # altro worker
        uno.commit()
    finally:
        uno.close()
    presi_1 = {p.sync_id for p in primo}
    presi_2 = {p.sync_id for p in secondo}
    assert len(presi_1) == 2 and len(presi_2) == 2 and not (presi_1 & presi_2)
    assert presi_1 | presi_2 == set(ids)
    tokens = {p.claim_token for p in primo + secondo}
    assert len(tokens) == 4 and all(re.fullmatch(r"[0-9a-f-]{36}", t) for t in tokens)
    assert _tx(repository.claim_batch, limit=10) == []                     # tutto preso


def test_20_21_lease_scaduto_e_compare_and_set(w, http):
    from calendar_sync import repository
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    (vecchio,) = _tx(repository.claim_batch)
    assert _tx(repository.claim_batch, lease_seconds=300) == []            # lease valido
    w["sql"]("UPDATE appointment_calendar_sync SET claimed_at = NOW() - INTERVAL '10 minutes'")
    (nuovo,) = _tx(repository.claim_batch, lease_seconds=300)              # orfano ripreso
    assert nuovo.sync_id == vecchio.sync_id and nuovo.claim_token != vecchio.claim_token
    # il vecchio worker non puo' piu' scrivere nulla
    assert _tx(repository.finalize, vecchio, final_status="synced") is None
    assert _tx(repository.mark_retry, vecchio, error_code="x", delay_seconds=60,
               exhausted=False) is None
    assert _tx(repository.load_claimed, vecchio) is None
    assert _tx(repository.finalize, nuovo, final_status="synced")["status"] == "synced"


def test_20b_generazione_cambiata_durante_il_lavoro_non_e_synced(w, http):
    from calendar_sync import repository
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    (presa,) = _tx(repository.claim_batch)
    r = _dirty(w, a["id"])                                   # cambia mentre il worker lavora
    assert r["status"] == "syncing" and r["dirty_generation"] == presa.generation + 1
    fine = _tx(repository.finalize, presa, final_status="synced")
    assert fine["status"] == "pending" and fine["synced_generation"] == presa.generation
    (di_nuovo,) = _tx(repository.claim_batch)
    assert di_nuovo.generation == presa.generation + 1
    assert _tx(repository.finalize, di_nuovo, final_status="synced")["status"] == "synced"


# ---------------------------------------------------------------------------
# G - RICONCILIATORE CON IL PROVIDER FINTO (22-41)
# ---------------------------------------------------------------------------

def _fake():
    from calendar_sync.fake_provider import FakeCalendarProvider
    return FakeCalendarProvider()


def test_29_30_fissato_e_confermato(w, http, ring):
    from calendar_sync import service
    c = _collega(w, ring, "luca")
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    fake = _fake()
    assert _giro(fake, ring) == [(_riga(w, a["id"])["id"], "synced")]
    evento = service.deterministic_event_id(NS, w["a"], a["id"])
    assert list(fake.active_events(c["id"])) == [evento]
    riga = _riga(w, a["id"])
    assert (riga["remote_connection_id"], riga["remote_calendar_id"], riga["status"]) == (
        c["id"], "primary", "synced")
    assert riga["synced_payload_hash"] == riga["desired_payload_hash"]
    assert fake.calls == [("ensure", c["id"], "primary", evento)]
    # confirm: payload invariato -> nessuna chiamata remota
    _azione(http, a, "confirm")
    _dirty(w, a["id"])
    assert _giro(fake, ring)[0][1] == "synced" and len(fake.calls) == 1


def test_31_richiesta_nessun_remoto(w, http, ring):
    _collega(w, ring, "luca")
    r = _crea(http, status="requested", assigned_user_id=None)
    _dirty(w, r["id"])
    fake = _fake()
    assert _giro(fake, ring)[0][1] == "synced" and fake.calls == []


def test_32_35_annullato_cancella_e_404_e_successo(w, http, ring):
    c = _collega(w, ring, "luca")
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    fake = _fake()
    _giro(fake, ring)
    a = _azione(http, a, "cancel", reason="annullato")
    _dirty(w, a["id"])
    assert _giro(fake, ring)[0][1] == "synced"
    assert fake.active_events(c["id"]) == {}
    riga = _riga(w, a["id"])
    assert riga["remote_connection_id"] is None and riga["remote_calendar_id"] is None
    # un secondo annullato il cui evento e' gia' sparito sul remoto: 404 = successo
    b = _crea(http, assigned_user_id=w["luca"], start_at=ore(13).isoformat(),
              end_at=ore(14).isoformat())
    _dirty(w, b["id"])
    _giro(fake, ring)
    fake.vanish(c["id"], "primary", _riga(w, b["id"])["remote_event_id"])
    _azione(http, b, "cancel", reason="x")
    _dirty(w, b["id"])
    assert _giro(fake, ring)[0][1] == "synced"
    assert _riga(w, b["id"])["remote_connection_id"] is None


@pytest.mark.parametrize("chiusura", ["complete", "no_show"])
def test_33_34_completato_e_assente_non_toccano_e_non_creano(w, http, ring, chiusura):
    c = _collega(w, ring, "luca")
    fake = _fake()
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    _giro(fake, ring)
    chiamate = len(fake.calls)
    _azione(http, a, chiusura)
    _dirty(w, a["id"])
    assert _giro(fake, ring)[0][1] == "synced"
    assert len(fake.calls) == chiamate and len(fake.active_events(c["id"])) == 1   # resta
    # mai creato prima della chiusura: non si crea retroattivamente
    b = _crea(http, assigned_user_id=w["luca"], start_at=ore(13).isoformat(),
              end_at=ore(14).isoformat())
    _azione(http, b, chiusura)
    _dirty(w, b["id"])
    assert _giro(fake, ring)[0][1] == "synced"
    assert len(fake.calls) == chiamate and len(fake.active_events(c["id"])) == 1


def test_36_evento_sparito_sul_remoto_si_ricrea_con_lo_stesso_id(w, http, ring):
    from calendar_sync import repository
    c = _collega(w, ring, "luca")
    fake = _fake()
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    _giro(fake, ring)
    evento = _riga(w, a["id"])["remote_event_id"]
    fake.vanish(c["id"], "primary", evento)                     # cancellato a mano
    _tx(repository.request_resync, w["a"], _riga(w, a["id"])["id"])
    assert _giro(fake, ring)[0][1] == "synced"
    assert list(fake.active_events(c["id"])) == [evento]


def test_22_23_24_retry_su_429_5xx_timeout(w, http, ring):
    _collega(w, ring, "luca")
    fake = _fake()
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    for n, tipo in enumerate(("rate_limited", "server_error", "timeout"), start=1):
        fake.fail_next("ensure", tipo)
        assert _giro(fake, ring)[0][1] == "retrying"
        riga = _riga(w, a["id"])
        attesa = w["sql"]("SELECT EXTRACT(EPOCH FROM next_attempt_at - NOW())::int "
                          "FROM appointment_calendar_sync")[0][0]
        assert (riga["status"], riga["attempt_count"], riga["last_error_code"]) == (
            "retrying", n, tipo)
        assert abs(attesa - 60 * 3 ** (n - 1)) <= 5
        assert _giro(fake, ring) == []                           # non ancora scaduta
        w["sql"]("UPDATE appointment_calendar_sync SET next_attempt_at = NOW()")
    assert _giro(fake, ring)[0][1] == "synced"
    assert _riga(w, a["id"])["attempt_count"] == 0


def test_25_401_needs_reauth_riga_e_connessione(w, http, ring):
    c = _collega(w, ring, "luca")
    fake = _fake()
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    fake.fail_next("ensure", "unauthorized", http_status=401)
    assert _giro(fake, ring)[0][1] == "needs_reauth"
    assert _riga(w, a["id"])["status"] == "needs_reauth"
    assert w["sql"]("SELECT status, last_error_code FROM calendar_connections WHERE id=%s",
                    (c["id"],))[0][:] == ["needs_reauth", "unauthorized"]
    assert _giro(fake, ring) == []                                # non si martella


def test_26_tentativi_esauriti_failed(w, http, ring):
    _collega(w, ring, "luca")
    fake = _fake()
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    w["sql"]("UPDATE appointment_calendar_sync SET attempt_count = 7")
    fake.fail_next("ensure", "server_error", http_status=503)
    assert _giro(fake, ring)[0][1] == "failed"
    assert (_riga(w, a["id"])["status"], _riga(w, a["id"])["attempt_count"]) == ("failed", 8)
    assert _giro(fake, ring) == []


def test_37_38_crash_dopo_il_successo_remoto_nessun_duplicato(w, http, ring):
    from calendar_sync.fake_provider import SimulatedCrash
    c = _collega(w, ring, "luca")
    fake = _fake()
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    fake.crash_after_success_next("ensure")
    with pytest.raises(SimulatedCrash):
        _giro(fake, ring)
    riga = _riga(w, a["id"])
    assert riga["status"] == "syncing" and riga["remote_connection_id"] is None   # DB ignaro
    assert len(fake.active_events(c["id"])) == 1                                  # remoto fatto
    assert _giro(fake, ring, lease_seconds=300) == []                             # lease valido
    w["sql"]("UPDATE appointment_calendar_sync SET claimed_at = NOW() - INTERVAL '10 minutes'")
    assert _giro(fake, ring, lease_seconds=300)[0][1] == "synced"
    assert list(fake.active_events(c["id"])) == [riga["remote_event_id"]]         # UNO solo
    assert [x[0] for x in fake.calls] == ["ensure", "ensure"]                     # stesso id
    assert {x[3] for x in fake.calls} == {riga["remote_event_id"]}
    assert _riga(w, a["id"])["remote_connection_id"] == c["id"]


def test_39_riassegnazione_a_b_stesso_evento_logico(w, http, ring):
    ca, cb = _collega(w, ring, "luca"), _collega(w, ring, "marta")
    fake = _fake()
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    _giro(fake, ring)
    evento = _riga(w, a["id"])["remote_event_id"]
    _azione(http, a, "reassign", assigned_user_id=w["marta"])
    riga = _dirty(w, a["id"])
    assert riga["remote_connection_id"] == ca["id"]           # mark_dirty non sposta il remoto
    assert _giro(fake, ring)[0][1] == "synced"
    assert fake.active_events(ca["id"]) == {} and list(fake.active_events(cb["id"])) == [evento]
    assert [x[:2] for x in fake.calls[1:]] == [("delete", ca["id"]), ("ensure", cb["id"])]
    assert _riga(w, a["id"])["remote_connection_id"] == cb["id"]


def test_40_crash_dopo_il_delete_su_a_converge(w, http, ring):
    from calendar_sync.fake_provider import SimulatedCrash
    ca, cb = _collega(w, ring, "luca"), _collega(w, ring, "marta")
    fake = _fake()
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    _giro(fake, ring)
    _azione(http, a, "reassign", assigned_user_id=w["marta"])
    _dirty(w, a["id"])
    fake.crash_after_success_next("delete")
    with pytest.raises(SimulatedCrash):
        _giro(fake, ring)
    assert _riga(w, a["id"])["remote_connection_id"] == ca["id"]    # il DB crede ancora ad A
    w["sql"]("UPDATE appointment_calendar_sync SET claimed_at = NOW() - INTERVAL '10 minutes'")
    assert _giro(fake, ring)[0][1] == "synced"
    assert fake.active_events(ca["id"]) == {} and len(fake.active_events(cb["id"])) == 1
    assert len(fake.all_active()) == 1


def test_41_b_senza_connessione_waiting_connection_e_nessuna_ricreazione(w, http, ring):
    ca = _collega(w, ring, "luca")
    fake = _fake()
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    _giro(fake, ring)
    _azione(http, a, "reassign", assigned_user_id=w["marta"])
    _dirty(w, a["id"])
    assert _giro(fake, ring)[0][1] == "waiting_connection"
    assert fake.all_active() == {} and fake.active_events(ca["id"]) == {}
    riga = _riga(w, a["id"])
    assert (riga["remote_connection_id"], riga["last_error_code"]) == (None, "no_connection")


def test_12b_spostamento_aggiorna_lo_stesso_evento(w, http, ring):
    from calendar_sync import repository
    c = _collega(w, ring, "luca")
    fake = _fake()
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    _giro(fake, ring)
    evento = _riga(w, a["id"])["remote_event_id"]
    b = _sposta(http, a, 15)
    _tx(repository.move_current_appointment_on_reschedule, w["a"], a["id"], b["id"])
    _dirty(w, b["id"])
    assert _giro(fake, ring)[0][1] == "synced"
    (ev,) = fake.active_events(c["id"]).values()
    assert list(fake.active_events(c["id"])) == [evento]
    assert ev.payload.private_properties["stima360_appointment_id"] == str(b["id"])
    assert ev.payload.start_at.hour == 15


def test_44_45_la_sync_non_tocca_version_ne_appointment_events(w, http, ring):
    from calendar_sync import repository, service
    c = _collega(w, ring, "luca")
    a = _crea(http, assigned_user_id=w["luca"])
    b = _sposta(http, a, 15)
    prima = _impronta_agenda(w)
    fake = _fake()
    riga = _tx(repository.ensure_sync_row, w["a"], a["id"], deployment_namespace=NS)
    _dirty(w, b["id"])
    _giro(fake, ring)
    _tx(repository.request_resync, w["a"], riga["id"])
    fake.fail_next("ensure", "server_error")
    _giro(fake, ring)
    w["sql"]("UPDATE appointment_calendar_sync SET next_attempt_at = NOW()")
    _giro(fake, ring)
    _tx(lambda cur: repository.create_oauth_state(
        cur, agency_id=w["a"], user_id=w["luca"], state_hash=service.new_oauth_state()[1],
        code_verifier=ring.encrypt("v")))
    _tx(repository.mark_connection_disconnected, w["a"], c["id"])
    assert _impronta_agenda(w) == prima


def test_47_nessun_segreto_nei_log_ne_negli_errori(w, http, ring, caplog):
    c = _collega(w, ring, "luca")
    fake = _fake()
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    caplog.set_level(logging.DEBUG)
    for tipo in ("rate_limited", "unauthorized"):
        fake.fail_next("ensure", tipo)
        _giro(fake, ring)
        w["sql"]("UPDATE appointment_calendar_sync SET status='pending', next_attempt_at=NOW()")
        w["sql"]("UPDATE calendar_connections SET status='connected'")
    cipher = bytes(w["sql"]("SELECT refresh_token_ciphertext FROM calendar_connections")[0][0])
    testo = caplog.text + str(w["sql"]("SELECT last_error_code, last_error_detail "
                                       "FROM appointment_calendar_sync")[0][:])
    assert "refresh-segreto-luca" not in testo and cipher.decode() not in testo
    assert c["id"]


def test_47b_chiave_assente_errore_controllato_e_chiave_sbagliata_needs_reauth(w, http, ring,
                                                                               monkeypatch):
    from cryptography.fernet import Fernet

    from calendar_sync import crypto, service
    _collega(w, ring, "luca")
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    monkeypatch.delenv(crypto.ENV_KEYS)
    esiti = service.run_once(provider=_fake())                     # keyring dall'ambiente
    assert esiti[0][1] == "retrying"
    assert _riga(w, a["id"])["last_error_code"] == "crypto_not_configured"
    w["sql"]("UPDATE appointment_calendar_sync SET next_attempt_at = NOW()")
    altra = crypto.parse_keyring(f"k2:{Fernet.generate_key().decode()}")
    assert service.run_once(provider=_fake(), keyring=altra)[0][1] == "needs_reauth"
    assert _riga(w, a["id"])["last_error_code"] == "token_unreadable"


# ---------------------------------------------------------------------------
# H - MEMBERSHIP VERIFICATA AL MOMENTO DELL'USO (final review, punto 2)
# ---------------------------------------------------------------------------

class _ContaDecifrature:
    """Il portachiavi vero, con un contatore: prova che il token non viene
    nemmeno decifrato quando la connessione non e' utilizzabile."""

    def __init__(self, ring):
        self._ring, self.decrypt_calls = ring, 0

    def decrypt(self, ciphertext):
        self.decrypt_calls += 1
        return self._ring.decrypt(ciphertext)


def _membership(w, chi, stato):
    """La membership, DIRETTAMENTE nel modello autorevole; `calendar_connections`
    non si tocca."""
    w["sql"]("UPDATE agency_memberships SET status = %s WHERE operator_user_id = %s",
             (stato, w[chi]))


def test_60_membership_sospesa_dopo_il_connect_nessun_uso_del_token(w, http, ring):
    from calendar_sync import repository
    c = _collega(w, ring, "luca")                              # 1-2: membership attiva, connected
    assert _tx(repository.usable_connection_for_user, w["a"], w["luca"])["id"] == c["id"]  # 3
    assert _tx(repository.usable_connection_for_user, w["b"], w["luca"]) is None      # altro tenant
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    riga_conn = w["sql"]("SELECT * FROM calendar_connections WHERE id=%s", (c["id"],))[0][:]

    _membership(w, "luca", "suspended")                         # 4-5: SOLO la membership
    assert w["sql"]("SELECT * FROM calendar_connections WHERE id=%s", (c["id"],))[0][:] == riga_conn
    letta = _tx(repository.get_connection, w["a"], c["id"])
    assert (letta["status"], letta["membership_active"], letta["usable"]) == (
        "connected", False, False)
    assert _tx(repository.usable_connection_for_user, w["a"], w["luca"]) is None
    assert _tx(repository.connection_secret, w["a"], c["id"]) is None       # il token non esce

    prima = _impronta_agenda(w)
    fake, spia = _fake(), _ContaDecifrature(ring)
    esito = _giro(fake, spia)                                   # 6: riconciliazione
    assert esito[0][1] == "waiting_connection"                  # 7
    assert fake.calls == [] and spia.decrypt_calls == 0          # zero provider, zero decrypt
    riga = _riga(w, a["id"])
    assert riga["status"] == "waiting_connection" and riga["status"] != "synced"
    assert riga["last_synced_at"] is None and riga["remote_connection_id"] is None
    assert _impronta_agenda(w) == prima                          # appointment + eventi invariati

    _membership(w, "luca", "active")                            # 8: riattivata
    assert _tx(repository.usable_connection_for_user, w["a"], w["luca"])["id"] == c["id"]
    assert w["sql"]("SELECT count(*) FROM calendar_connections")[0][0] == 1  # stessa riga
    _dirty(w, a["id"])                                          # (A30-9B: resync/hook)
    assert _giro(fake, spia)[0][1] == "synced"                  # 9: procede
    assert spia.decrypt_calls == 1 and [x[0] for x in fake.calls] == ["ensure"]
    assert _riga(w, a["id"])["remote_connection_id"] == c["id"]


def test_61_membership_sospesa_non_si_cancella_nemmeno_l_evento_dell_utente(w, http, ring):
    """Il token dell'utente sospeso non si usa neppure per il DELETE: l'evento
    resta su Google (policy di disconnect gia' approvata) finche' la
    connessione non torna utilizzabile."""
    c = _collega(w, ring, "luca")
    fake = _fake()
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    _giro(fake, ring)
    evento = _riga(w, a["id"])["remote_event_id"]
    a = _azione(http, a, "cancel", reason="annullato")
    _dirty(w, a["id"])
    _membership(w, "luca", "revoked")
    spia = _ContaDecifrature(ring)
    chiamate = len(fake.calls)
    assert _giro(fake, spia)[0][1] == "waiting_connection"
    assert len(fake.calls) == chiamate and spia.decrypt_calls == 0
    assert list(fake.active_events(c["id"])) == [evento]           # resta dov'e'
    assert _riga(w, a["id"])["remote_connection_id"] == c["id"]      # il mapping resta
    _membership(w, "luca", "active")
    _dirty(w, a["id"])
    assert _giro(fake, spia)[0][1] == "synced"
    assert fake.active_events(c["id"]) == {} and fake.calls[-1][0] == "delete"


def test_62_riassegnazione_da_utente_sospeso_non_tocca_il_suo_calendario(w, http, ring):
    ca, cb = _collega(w, ring, "luca"), _collega(w, ring, "marta")
    fake = _fake()
    a = _crea(http, assigned_user_id=w["luca"])
    _dirty(w, a["id"])
    _giro(fake, ring)
    _azione(http, a, "reassign", assigned_user_id=w["marta"])
    _dirty(w, a["id"])
    _membership(w, "luca", "suspended")
    assert _giro(fake, ring)[0][1] == "synced"
    assert [x[:2] for x in fake.calls[1:]] == [("ensure", cb["id"])]   # nessun delete su A
    assert len(fake.active_events(ca["id"])) == 1                      # resta su A
    riga = _riga(w, a["id"])
    assert (riga["remote_connection_id"], riga["last_error_code"]) == (
        cb["id"], "previous_calendar_unreachable")
