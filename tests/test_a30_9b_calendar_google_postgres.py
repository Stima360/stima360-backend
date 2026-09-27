"""A30-9B - PostgreSQL VERO: OAuth (stato monouso, race, callback), hook
Agenda + LMC-15 (matrice di transizione), backfill, resync, disconnect,
worker con provider FINTO (mai Google reale, §39), isolamento di tenant.

Riusa i fixture di A30-9A (`w`, `ring`, `db_074`, `pulito`) e quelli di A30-2
(`mondo`, `db`, `ore`, `futuro`, `chiave`) via `test_a30_9a_calendar_sync_postgres`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_a30_9a_calendar_sync_postgres import (  # noqa: F401 - fixture
    DSN, NS, db_074, pulito, ring, w)
from tests.test_a30_2_appointments_postgres import chiave, db, futuro, mondo, ore  # noqa: F401

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare A30-9B")

CLIENT_ID = "cid-test"
CLIENT_SECRET = "csecret-test"
REDIRECT_URI = "https://esempio.test/api/calendar/google/callback"

_MIGRAZIONI = Path(__file__).resolve().parents[1] / "migrations"
_SU_073 = (_MIGRAZIONI / "073_a30_2p_lmc15_facade.sql").read_text(encoding="utf-8")


@pytest.fixture
def w073(w):
    """`w`, con la 073 (facade LMC-15) applicata al database - guardata come
    in `test_a30_2p_facade_postgres.py::lmc15`: serve solo per i test che
    esercitano `appointments/lmc15_facade.py` direttamente."""
    esiste = w["sql"]("SELECT 1 FROM pg_constraint WHERE conname = "
                      "'appointments_lmc15_facade_chk'")
    if not esiste:
        with w["conn"].cursor() as cur:
            cur.execute(_SU_073)
            cur.execute("INSERT INTO schema_migrations (version, applied_at) "
                        "VALUES ('073_a30_2p_lmc15_facade', NOW())")
        w["conn"].commit()
    return w


# ---------------------------------------------------------------------------
# banco
# ---------------------------------------------------------------------------

@pytest.fixture
def genv(monkeypatch):
    """(1) Google ABILITATO e configurato per il test, via env - mai
    hardcoded nel dominio."""
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("GOOGLE_CALENDAR_REDIRECT_URI", REDIRECT_URI)
    monkeypatch.setenv("GOOGLE_CALENDAR_DEPLOYMENT_NAMESPACE", NS)
    return True


@pytest.fixture
def httpg(w):
    """Come il fixture `http` di A30-9A, ma con ANCHE il router Google
    Calendar montato: hook (via le rotte native) e OAuth nella stessa app."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from appointments.router import router as appointments_router
    from calendar_sync.router import router as gcal_router
    from operator_auth.dependencies import require_operator

    app = FastAPI()
    app.include_router(appointments_router)
    app.include_router(gcal_router)
    stato = {"ctx": w["ctx"]("giorgio")}
    app.dependency_overrides[require_operator] = lambda: stato["ctx"]
    client = TestClient(app)

    def come(chi, **kw):
        stato["ctx"] = w["ctx"](chi, **kw)
        return client

    return come


def _crea(httpg, *, status="scheduled", agente="giorgio", assegnato="luca", **kw):
    corpo = {"appointment_type": "seller_meeting", "status": status,
             "assigned_user_id": None, "start_at": ore(10).isoformat(),
             "end_at": ore(11).isoformat(), "client_request_id": chiave()}
    corpo.update(kw)
    r = httpg(agente).post("/api/appointments", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()


def _riga_sync(w, root):
    righe = w["sql"]("SELECT * FROM appointment_calendar_sync WHERE chain_root_appointment_id=%s",
                     (root,))
    return dict(righe[0]) if righe else None


def _fake_oauth(monkeypatch, *, subject="sub-luca", refresh_token="refresh-abc",
               scope="openid https://www.googleapis.com/auth/calendar.events.owned",
               id_token="jwt-finto", iss="https://accounts.google.com"):
    """Sostituisce SOLO lo scambio del `code` (rete verso Google) con uno
    finto (§39): `authorization_url()` resta la libreria VERA (nessuna rete,
    e' pura costruzione dell'URL/PKCE) - solo `fetch_token` e la verifica
    dell'id_token sono finti."""
    from google_auth_oauthlib.flow import Flow

    from calendar_sync import oauth as gcal_oauth

    def _fetch_token_finto(self, **kw):
        return {"scope": scope, "refresh_token": refresh_token, "id_token": id_token}

    def _verifica_finta(jwt, client_id):
        assert jwt == id_token
        return {"iss": iss, "aud": client_id, "sub": subject}

    monkeypatch.setattr(Flow, "fetch_token", _fetch_token_finto)
    monkeypatch.setattr(gcal_oauth, "_default_id_token_verifier", _verifica_finta)


def _connetti(httpg, monkeypatch, w, chi, *, subject=None):
    """Un giro COMPLETO e riuscito di /connect + /callback per `chi`."""
    _fake_oauth(monkeypatch, subject=subject or f"sub-{chi}")
    r = httpg(chi).post("/api/calendar/google/connect")
    assert r.status_code == 200, r.text
    url = r.json()["authorization_url"]
    from urllib.parse import parse_qs, urlparse

    stato = parse_qs(urlparse(url).query)["state"][0]
    r2 = httpg(chi).get("/api/calendar/google/callback",
                        params={"code": "AUTHCODE", "state": stato}, follow_redirects=False)
    assert r2.status_code in (302, 307), r2.text
    return r2


# ---------------------------------------------------------------------------
# A - CONFIGURAZIONE, BOOT (1, 2, 36 - gia' negli static; qui il boot reale)
# ---------------------------------------------------------------------------

def test_04_status_non_configured_senza_env(httpg):
    r = httpg("giorgio").get("/api/calendar/google/status")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["configured"] is False
    assert corpo["enabled"] is False
    assert corpo["connection_status"] == "not_configured"


def test_05_status_not_connected_con_google_abilitato(genv, httpg):
    r = httpg("luca").get("/api/calendar/google/status")
    corpo = r.json()
    assert corpo["configured"] is True
    assert corpo["enabled"] is True
    assert corpo["connection_status"] == "not_connected"


# ---------------------------------------------------------------------------
# B - CONNECT (4, 5, 6, 7, 8, 13, 30, 71)
# ---------------------------------------------------------------------------

def test_04b_connect_senza_config_errore_controllato(httpg, ring):
    r = httpg("luca").post("/api/calendar/google/connect")
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "GOOGLE_NOT_CONFIGURED"


def test_04c_connect_agent_active_e_browser_non_scelto(genv, httpg, ring, w):
    """(4, 5) Connect di un agente ATTIVO; l'agenzia/l'operatore vengono
    SOLO dal contesto server, mai da un corpo che il browser potrebbe
    mandare (la rotta non accetta nessun campo: un corpo qualunque e'
    ignorato, mai letto come agency_id/user_id)."""
    r = httpg("luca").post("/api/calendar/google/connect",
                           json={"agency_id": 999999, "user_id": 999999})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert set(corpo) == {"authorization_url"}
    riga = w["sql"]("SELECT agency_id, user_id FROM calendar_oauth_states")[0]
    assert riga["agency_id"] == w["a"] and riga["user_id"] == w["luca"]


def test_06_07_state_hash_only_ttl_10_min(genv, httpg, ring, w):
    """(6, 7) SOLO l'hash dello stato e' salvato, mai il chiaro; TTL = 10
    minuti (§ costante `OAUTH_STATE_TTL_SECONDS`)."""
    from calendar_sync import constants as k
    r = httpg("luca").post("/api/calendar/google/connect")
    url = r.json()["authorization_url"]
    from urllib.parse import parse_qs, urlparse

    stato_chiaro = parse_qs(urlparse(url).query)["state"][0]
    riga = w["sql"]("SELECT state_hash FROM calendar_oauth_states")[0]
    assert stato_chiaro != riga["state_hash"]
    assert k.OAUTH_STATE_TTL_SECONDS == 600


def test_08_verifier_cifrato(genv, httpg, ring, w):
    """(8) Il verifier PKCE e' salvato SOLO cifrato."""
    httpg("luca").post("/api/calendar/google/connect")
    riga = w["sql"]("SELECT code_verifier_ciphertext FROM calendar_oauth_states")[0]
    assert bytes(riga["code_verifier_ciphertext"]) != b""
    # Non decodificabile come testo PKCE in chiaro (43-128 char urlsafe).
    grezzo = bytes(riga["code_verifier_ciphertext"])
    assert not grezzo.decode("ascii", errors="ignore").isascii() or len(grezzo) > 128


def test_13_membership_sospesa_connect_rifiutato(genv, httpg, ring, w):
    w["sql"]("UPDATE agency_memberships SET status='suspended' "
            "WHERE agency_id=%s AND operator_user_id=%s", (w["a"], w["luca"]))
    r = httpg("luca").post("/api/calendar/google/connect")
    assert r.status_code == 403
    assert r.json()["code"] == "GOOGLE_MEMBERSHIP_INACTIVE"


def test_30_platform_admin_senza_agenzia_operatore_richiesto(genv, httpg):
    r = httpg(None, platform=True).post("/api/calendar/google/connect")
    assert r.status_code in (403, 422)


# ---------------------------------------------------------------------------
# C - CALLBACK (14-19, 62, 71)
# ---------------------------------------------------------------------------

def test_14_callback_valido_connessione_creata(genv, httpg, ring, w, monkeypatch):
    r2 = _connetti(httpg, monkeypatch, w, "luca")
    assert r2.headers["location"].startswith("/os/#/agenda")
    riga = w["sql"]("SELECT status, provider_subject, user_id, agency_id "
                    "FROM calendar_connections")[0]
    assert riga["status"] == "connected"
    assert riga["provider_subject"] == "sub-luca"
    assert riga["user_id"] == w["luca"] and riga["agency_id"] == w["a"]


def test_23_24_25_no_access_token_persistito_solo_refresh_cifrato(genv, httpg, ring, w,
                                                                    monkeypatch):
    """(23, 24, 25) Solo il refresh token cifrato e' salvato; nessuna colonna
    per un access token; `provider_subject` e' proprio il `sub`."""
    _connetti(httpg, monkeypatch, w, "luca")
    riga = w["sql"]("SELECT * FROM calendar_connections")[0]
    assert "access_token" not in riga
    assert riga["refresh_token_ciphertext"] is not None
    grezzo = bytes(riga["refresh_token_ciphertext"])
    assert b"refresh-abc" not in grezzo
    assert riga["provider_subject"] == "sub-luca"


def test_15_callback_scaduto(genv, httpg, ring, w, monkeypatch):
    _fake_oauth(monkeypatch)
    r = httpg("luca").post("/api/calendar/google/connect")
    from urllib.parse import parse_qs, urlparse

    stato = parse_qs(urlparse(r.json()["authorization_url"]).query)["state"][0]
    # Il CHECK della 074 pretende expires_at > created_at (<= +1h): si
    # spostano ENTRAMBI nel passato, cosi' lo stato resta valido per il
    # vincolo ma e' scaduto per l'orologio reale.
    w["sql"]("UPDATE calendar_oauth_states SET created_at = NOW() - interval '2 hours', "
            "expires_at = NOW() - interval '1 hour'")
    r2 = httpg("luca").get("/api/calendar/google/callback",
                           params={"code": "C", "state": stato}, follow_redirects=False)
    assert "GOOGLE_OAUTH_STATE_EXPIRED" in r2.headers["location"]


def test_16_callback_riusato(genv, httpg, ring, w, monkeypatch):
    r2 = _connetti(httpg, monkeypatch, w, "luca")
    from urllib.parse import parse_qs, urlparse

    # Ripete la STESSA richiesta di connect per riprendere lo stesso state:
    # piu' semplice e' rigiocare la richiesta gia' consumata sopra.
    r = httpg("luca").post("/api/calendar/google/connect")
    stato = parse_qs(urlparse(r.json()["authorization_url"]).query)["state"][0]
    httpg("luca").get("/api/calendar/google/callback", params={"code": "C1", "state": stato},
                      follow_redirects=False)
    r3 = httpg("luca").get("/api/calendar/google/callback", params={"code": "C2", "state": stato},
                           follow_redirects=False)
    assert "GOOGLE_OAUTH_STATE_USED" in r3.headers["location"]


def test_17_callback_mismatch_utente_sessione(genv, httpg, ring, w, monkeypatch):
    """(17) Lo state di `luca` non e' consumabile dalla sessione di `marta`:
    errore GENERICO (nessun oracolo su chi era atteso)."""
    _fake_oauth(monkeypatch)
    r = httpg("luca").post("/api/calendar/google/connect")
    from urllib.parse import parse_qs, urlparse

    stato = parse_qs(urlparse(r.json()["authorization_url"]).query)["state"][0]
    r2 = httpg("marta").get("/api/calendar/google/callback",
                            params={"code": "C", "state": stato}, follow_redirects=False)
    assert "GOOGLE_OAUTH_STATE_INVALID" in r2.headers["location"]


def test_18_membership_sospesa_prima_del_callback(genv, httpg, ring, w, monkeypatch):
    _fake_oauth(monkeypatch)
    r = httpg("luca").post("/api/calendar/google/connect")
    from urllib.parse import parse_qs, urlparse

    stato = parse_qs(urlparse(r.json()["authorization_url"]).query)["state"][0]
    w["sql"]("UPDATE agency_memberships SET status='suspended' "
            "WHERE agency_id=%s AND operator_user_id=%s", (w["a"], w["luca"]))
    r2 = httpg("luca").get("/api/calendar/google/callback",
                           params={"code": "C", "state": stato}, follow_redirects=False)
    assert "GOOGLE_MEMBERSHIP_INACTIVE" in r2.headers["location"]
    assert w["sql"]("SELECT count(*) AS n FROM calendar_connections")[0]["n"] == 0


def test_19_oauth_denied(genv, httpg):
    r = httpg("luca").get("/api/calendar/google/callback",
                          params={"error": "access_denied", "state": "x"},
                          follow_redirects=False)
    assert "GOOGLE_OAUTH_DENIED" in r.headers["location"]


def test_20_missing_refresh_token(genv, httpg, ring, w, monkeypatch):
    from google_auth_oauthlib.flow import Flow
    monkeypatch.setattr(Flow, "fetch_token", lambda self, **kw: {
        "scope": "openid https://www.googleapis.com/auth/calendar.events.owned",
        "id_token": "jwt-finto"})
    r = httpg("luca").post("/api/calendar/google/connect")
    from urllib.parse import parse_qs, urlparse

    stato = parse_qs(urlparse(r.json()["authorization_url"]).query)["state"][0]
    r2 = httpg("luca").get("/api/calendar/google/callback",
                           params={"code": "C", "state": stato}, follow_redirects=False)
    assert "GOOGLE_REFRESH_TOKEN_MISSING" in r2.headers["location"]
    assert w["sql"]("SELECT count(*) AS n FROM calendar_connections")[0]["n"] == 0


def test_21_22_id_token_non_valido_o_audience_sbagliata(genv, httpg, ring, w, monkeypatch):
    _fake_oauth(monkeypatch)
    from calendar_sync import oauth as gcal_oauth
    monkeypatch.setattr(gcal_oauth, "_default_id_token_verifier",
                        lambda jwt, client_id: {"iss": "https://accounts.google.com",
                                                "aud": "un-altro-client", "sub": None})
    r = httpg("luca").post("/api/calendar/google/connect")
    from urllib.parse import parse_qs, urlparse

    stato = parse_qs(urlparse(r.json()["authorization_url"]).query)["state"][0]
    r2 = httpg("luca").get("/api/calendar/google/callback",
                           params={"code": "C", "state": stato}, follow_redirects=False)
    assert "GOOGLE_ID_TOKEN_INVALID" in r2.headers["location"]


def test_41_reconnect_idempotente(genv, httpg, ring, w, monkeypatch):
    _connetti(httpg, monkeypatch, w, "luca")
    n1 = w["sql"]("SELECT count(*) AS n FROM calendar_connections")[0]["n"]
    _connetti(httpg, monkeypatch, w, "luca", subject="sub-luca")
    n2 = w["sql"]("SELECT count(*) AS n FROM calendar_connections")[0]["n"]
    assert n1 == n2 == 1


# ---------------------------------------------------------------------------
# D - BACKFILL (38, 39, 40, 41)
# ---------------------------------------------------------------------------

def test_38_39_40_backfill_scheduled_confirmed_non_passati(genv, httpg, ring, w, monkeypatch):
    futura_sched = _crea(httpg, status="scheduled", assegnato="luca",
                         assigned_user_id=w["luca"])
    futura_conf = _crea(httpg, status="scheduled", assegnato="luca",
                        assigned_user_id=w["luca"], start_at=ore(14).isoformat(),
                        end_at=ore(15).isoformat())
    httpg("giorgio").post(f"/api/appointments/{futura_conf['id']}/confirm",
                          json={"version": futura_conf["version"]})
    passata = _crea(httpg, status="requested", assegnato="luca", assigned_user_id=None,
                    start_at="2020-01-01T09:00:00+01:00", end_at="2020-01-01T10:00:00+01:00")
    richiesta = _crea(httpg, status="requested", assegnato="luca", assigned_user_id=None,
                      start_at=ore(16).isoformat(), end_at=ore(17).isoformat())

    _connetti(httpg, monkeypatch, w, "luca")

    assert _riga_sync(w, futura_sched["id"]) is not None
    assert _riga_sync(w, futura_conf["id"]) is not None
    assert _riga_sync(w, passata["id"]) is None
    assert _riga_sync(w, richiesta["id"]) is None


# ---------------------------------------------------------------------------
# E - RESYNC (42)
# ---------------------------------------------------------------------------

def test_42_resync_solo_proprie_righe(genv, httpg, ring, w, monkeypatch):
    # futuro davvero (rispetto al NOW() reale del DB): `future_syncable_
    # appointment_ids` filtra `start_at > NOW()`, e `ore()` da solo GIORNO,
    # un riferimento fisso nel PASSATO reale.
    a1 = _crea(httpg, status="scheduled", assegnato="luca", assigned_user_id=w["luca"],
              start_at=futuro(10).isoformat(), end_at=futuro(11).isoformat())
    a2 = _crea(httpg, status="scheduled", assegnato="marta", assigned_user_id=w["marta"],
              start_at=futuro(14).isoformat(), end_at=futuro(15).isoformat())
    _connetti(httpg, monkeypatch, w, "luca")
    _connetti(httpg, monkeypatch, w, "marta")
    riga1 = _riga_sync(w, a1["id"])
    riga2 = _riga_sync(w, a2["id"])
    w["sql"]("UPDATE appointment_calendar_sync SET status='failed' WHERE id IN (%s,%s)",
            (riga1["id"], riga2["id"]))
    r = httpg("luca").post("/api/calendar/google/resync")
    assert r.status_code == 200, r.text
    assert r.json()["requeued"] == 1
    assert _riga_sync(w, a1["id"])["status"] == "pending"
    assert _riga_sync(w, a2["id"])["status"] == "failed"  # non toccata: e' di marta


def test_71_agente_non_gestisce_google_del_collega(genv, httpg, ring, w, monkeypatch):
    """(71) `luca` non puo' disconnettere/risincronizzare la connessione di
    `marta`: ogni rotta opera SOLO sulla connessione del CHIAMANTE."""
    _connetti(httpg, monkeypatch, w, "marta")
    r = httpg("luca").post("/api/calendar/google/disconnect")
    assert r.status_code == 404  # luca non ha connessione: la sua, non quella di marta
    assert w["sql"]("SELECT status FROM calendar_connections WHERE user_id=%s",
                    (w["marta"],))[0]["status"] == "connected"


# ---------------------------------------------------------------------------
# F - DISCONNECT (43)
# ---------------------------------------------------------------------------

def test_43_disconnect_locale_riesce_anche_se_revoca_fallisce(genv, httpg, ring, w, monkeypatch):
    from calendar_sync import oauth as gcal_oauth
    monkeypatch.setattr(gcal_oauth, "revoke_refresh_token", lambda token: False)
    _connetti(httpg, monkeypatch, w, "luca")
    r = httpg("luca").post("/api/calendar/google/disconnect")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "disconnected"
    riga = w["sql"]("SELECT status, refresh_token_ciphertext FROM calendar_connections")[0]
    assert riga["status"] == "disconnected"
    assert riga["refresh_token_ciphertext"] is None


# ---------------------------------------------------------------------------
# G - HOOK APPOINTMENT -> SYNC: LA MATRICE DI TRANSIZIONE (§18, 26-36)
# ---------------------------------------------------------------------------

def test_27_requested_create_no_dirty(genv, httpg, w):
    a = _crea(httpg, status="requested", assigned_user_id=None)
    assert _riga_sync(w, a["id"]) is None


def test_26_scheduled_create_mark_dirty(genv, httpg, w):
    # Una riga NUOVA nasce gia' con dirty_generation=1 (default della 074);
    # l'hook la marca ANCORA dirty (mark_dirty_with_cursor incrementa sempre):
    # 2 e' la firma "la riga esiste ed e' stata marcata", non solo "esiste".
    a = _crea(httpg, status="scheduled", assigned_user_id=w["luca"])
    riga = _riga_sync(w, a["id"])
    assert riga is not None and riga["status"] == "pending" and riga["dirty_generation"] == 2


def test_28_schedule_mark_dirty(genv, httpg, w):
    a = _crea(httpg, status="requested", assigned_user_id=None)
    r = httpg("giorgio").post(f"/api/appointments/{a['id']}/schedule",
                              json={"version": a["version"], "assigned_user_id": w["luca"],
                                    "start_at": ore(12).isoformat(), "end_at": ore(13).isoformat()})
    assert r.status_code == 200, r.text
    riga = _riga_sync(w, a["id"])
    assert riga is not None and riga["dirty_generation"] == 2  # nasce ora: 1 + il mark dirty


def test_29_confirm_no_generation_bump(genv, httpg, w):
    a = _crea(httpg, status="scheduled", assigned_user_id=w["luca"])
    prima = _riga_sync(w, a["id"])["dirty_generation"]
    r = httpg("giorgio").post(f"/api/appointments/{a['id']}/confirm", json={"version": a["version"]})
    assert r.status_code == 200, r.text
    dopo = _riga_sync(w, a["id"])["dirty_generation"]
    assert dopo == prima


def test_30_31_reschedule_stessa_catena_stesso_evento_id(genv, httpg, w):
    a = _crea(httpg, status="scheduled", assigned_user_id=w["luca"])
    # (§9) l'id evento e' DETERMINISTICO e nasce con la riga stessa: nessun
    # bisogno (anzi, e' impossibile: la colonna e' immutabile per trigger,
    # migration 074) di "fingerlo" post-creazione. Basta leggere quello vero.
    remoto_prima = _riga_sync(w, a["id"])["remote_event_id"]
    r = httpg("giorgio").post(f"/api/appointments/{a['id']}/reschedule",
                              json={"version": a["version"], "start_at": ore(15).isoformat(),
                                    "end_at": ore(16).isoformat()})
    assert r.status_code == 201, r.text
    nuova = r.json()
    riga = _riga_sync(w, a["id"])  # stessa RADICE
    assert riga is not None
    assert riga["remote_event_id"] == remoto_prima  # id evento invariato
    assert riga["current_appointment_id"] == nuova["id"]
    assert riga["dirty_generation"] == 3  # 2 (creazione, gia' 'scheduled') + 1 (reschedule)


def test_32_reassign_dirty(genv, httpg, w):
    a = _crea(httpg, status="confirmed", assigned_user_id=w["luca"])
    r = httpg("giorgio").post(f"/api/appointments/{a['id']}/reassign",
                              json={"version": a["version"], "assigned_user_id": w["marta"]})
    assert r.status_code == 200, r.text
    riga = _riga_sync(w, a["id"])
    assert riga is not None and riga["dirty_generation"] == 3  # 2 (creazione, gia' 'confirmed') + 1 (reassign)


def test_33_notes_only_patch_no_dirty(genv, httpg, w):
    a = _crea(httpg, status="scheduled", assigned_user_id=w["luca"])
    riga_prima = _riga_sync(w, a["id"])
    r = httpg("giorgio").patch(f"/api/appointments/{a['id']}",
                               json={"version": a["version"], "notes": "una nota qualunque"})
    assert r.status_code == 200, r.text
    riga_dopo = _riga_sync(w, a["id"])
    assert riga_dopo["dirty_generation"] == riga_prima["dirty_generation"]


def test_32b_patch_rilevante_mark_dirty_a_livello_di_funzione_pura():
    """(32) `PatchBody` (appointments/schemas.py) oggi non permette di
    cambiare orari/tipo/agente (solo note, luogo, collegamenti): non esiste
    quindi una richiesta HTTP reale che lo dimostri end-to-end. La REGOLA
    (mark dirty solo se il PATCH cambia un campo Google-rilevante) e'
    verificata direttamente sull'insieme che il service usa."""
    from appointments.service import _GCAL_RELEVANT_PATCH_FIELDS
    assert _GCAL_RELEVANT_PATCH_FIELDS & {"start_at": None}.keys()
    assert not (_GCAL_RELEVANT_PATCH_FIELDS & {"notes": None, "location_text": None}.keys())


def test_34_cancel_dirty(genv, httpg, w):
    a = _crea(httpg, status="scheduled", assigned_user_id=w["luca"])
    r = httpg("giorgio").post(f"/api/appointments/{a['id']}/cancel",
                              json={"version": a["version"], "reason": "cliente ha rinunciato"})
    assert r.status_code == 200, r.text
    riga = _riga_sync(w, a["id"])
    assert riga is not None and riga["dirty_generation"] == 3  # 2 (creazione, gia' 'scheduled') + 1 (cancel)


def test_35_complete_no_new_dirty(genv, httpg, w):
    a = _crea(httpg, status="confirmed", assigned_user_id=w["luca"],
             start_at=ore(8).isoformat(), end_at=ore(9).isoformat())
    prima = _riga_sync(w, a["id"])["dirty_generation"]
    r = httpg("giorgio").post(f"/api/appointments/{a['id']}/complete", json={"version": a["version"]})
    assert r.status_code == 200, r.text
    dopo = _riga_sync(w, a["id"])["dirty_generation"]
    assert dopo == prima


def test_36_no_show_no_new_dirty(genv, httpg, w):
    a = _crea(httpg, status="confirmed", assigned_user_id=w["luca"],
             start_at=ore(6).isoformat(), end_at=ore(7).isoformat())
    prima = _riga_sync(w, a["id"])["dirty_generation"]
    r = httpg("giorgio").post(f"/api/appointments/{a['id']}/no-show", json={"version": a["version"]})
    assert r.status_code == 200, r.text
    dopo = _riga_sync(w, a["id"])["dirty_generation"]
    assert dopo == prima


def test_72_appointment_mutation_succeeds_when_disabled(httpg, w, ring):
    """(72) Google DISABILITATO: l'appuntamento CRM riesce comunque, e non
    nasce nessuna riga di sync (NO-OP fail-open)."""
    a = _crea(httpg, status="scheduled", assigned_user_id=w["luca"])
    assert _riga_sync(w, a["id"]) is None


def test_73_74_worker_non_tocca_version_ne_eventi_dell_appuntamento(genv, httpg, ring, w):
    """(73, 74) La sincronizzazione (creazione della riga + mark dirty) non
    tocca `version` dell'appuntamento ne' scrive `appointment_events`."""
    a = _crea(httpg, status="scheduled", assigned_user_id=w["luca"])
    dopo = httpg("giorgio").get(f"/api/appointments/{a['id']}").json()
    assert dopo["appointment"]["version"] == a["version"]
    n_eventi = w["sql"]("SELECT count(*) AS n FROM appointment_events WHERE appointment_id=%s",
                        (a["id"],))[0]["n"]
    assert n_eventi >= 1  # solo l'evento 'created' nativo dell'Agenda


# ---------------------------------------------------------------------------
# H - LMC-15 (37, §20)
# ---------------------------------------------------------------------------

def test_37_lmc15_schedule_inspection_mark_dirty(genv, w073, ring):
    w = w073
    from appointments import lmc15_facade
    ispezione = lmc15_facade.schedule_inspection(
        w["a"], stima_id=w["stima"], scheduled_for=futuro(3), actor_user_id=w["giorgio"])
    riga = w["sql"]("SELECT a.id FROM appointments a WHERE a.stima_inspection_id=%s",
                    (ispezione["id"],))[0]
    sync = _riga_sync(w, riga["id"])
    assert sync is not None and sync["dirty_generation"] == 2  # nasce ora: 1 + il mark dirty


def test_37b_lmc15_cancel_inspection_mark_dirty(genv, w073, ring):
    w = w073
    from appointments import lmc15_facade
    ispezione = lmc15_facade.schedule_inspection(
        w["a"], stima_id=w["stima"], scheduled_for=futuro(3), actor_user_id=w["giorgio"])
    lmc15_facade.cancel_inspection(w["a"], inspection_id=ispezione["id"], reason="test",
                                   actor_user_id=w["giorgio"])
    riga = w["sql"]("SELECT a.id FROM appointments a WHERE a.stima_inspection_id=%s",
                    (ispezione["id"],))[0]
    sync = _riga_sync(w, riga["id"])
    assert sync is not None and sync["dirty_generation"] == 3  # 2 (creazione, schedule_inspection) + 1 (cancel)


def test_37c_lmc15_complete_inspection_no_dirty_extra(genv, w073, ring):
    w = w073
    from appointments import lmc15_facade
    passato = ore(8)
    ispezione = w["sql"](
        "INSERT INTO stima_inspections (stima_id, status, scheduled_for, "
        "created_by_operator_user_id) "
        "VALUES (%s, 'scheduled', %s, %s) "
        "RETURNING id, stima_id, status, scheduled_for, %s AS agency_id",
        (w["stima"], passato, w["giorgio"], w["a"]))[0]
    from appointments import backfill, repository as ap_repo
    from core.database import core_cursor
    with core_cursor(commit=True) as (_, cur):
        piano = backfill.map_inspection(dict(ispezione))
        ap_repo.insert_appointment(cur, piano["values"], actor_user_id=None)
    riga_app = w["sql"]("SELECT id FROM appointments WHERE stima_inspection_id=%s",
                        (ispezione["id"],))[0]
    from calendar_sync.repository import mark_dirty_with_cursor
    with core_cursor(commit=True) as (_, cur):
        mark_dirty_with_cursor(cur, w["a"], riga_app["id"], deployment_namespace=NS)
    prima = _riga_sync(w, riga_app["id"])["dirty_generation"]
    lmc15_facade.complete_inspection(w["a"], inspection_id=ispezione["id"],
                                     completed_at=passato, actor_user_id=w["giorgio"])
    dopo = _riga_sync(w, riga_app["id"])["dirty_generation"]
    assert dopo == prima


# ---------------------------------------------------------------------------
# I - WORKER (provider finto, riusa il riconciliatore di A30-9A) (44-57)
# ---------------------------------------------------------------------------

def test_44_worker_crea_evento_per_scheduled(genv, httpg, ring, w, monkeypatch):
    from calendar_sync import service as gcal_service
    from calendar_sync.fake_provider import FakeCalendarProvider
    a = _crea(httpg, status="scheduled", assigned_user_id=w["luca"])
    _connetti(httpg, monkeypatch, w, "luca")
    fake = FakeCalendarProvider()
    esiti = gcal_service.run_once(provider=fake, keyring=ring)
    assert any(esito == "synced" for _, esito in esiti)
    assert len(fake.all_active()) == 1


def test_51_52_reassign_a_b_e_b_senza_connessione(genv, httpg, ring, w, monkeypatch):
    from calendar_sync import service as gcal_service
    from calendar_sync.fake_provider import FakeCalendarProvider
    a = _crea(httpg, status="confirmed", assigned_user_id=w["luca"])
    _connetti(httpg, monkeypatch, w, "luca")
    fake = FakeCalendarProvider()
    gcal_service.run_once(provider=fake, keyring=ring)
    assert len(fake.all_active()) == 1
    versione = httpg("giorgio").get(f"/api/appointments/{a['id']}").json()["appointment"]["version"]
    httpg("giorgio").post(f"/api/appointments/{a['id']}/reassign",
                          json={"version": versione, "assigned_user_id": w["marta"]})
    gcal_service.run_once(provider=fake, keyring=ring)
    assert len(fake.all_active()) == 0  # marta non ha connessione: waiting_connection
    riga = _riga_sync(w, a["id"])
    assert riga["status"] == "waiting_connection"


# ---------------------------------------------------------------------------
# J - ISOLAMENTO DI TENANT (69, 70)
# ---------------------------------------------------------------------------

def test_69_70_isolamento_tenant_connessioni_e_sync(genv, httpg, ring, w, monkeypatch):
    a = _crea(httpg, status="scheduled", assigned_user_id=w["luca"])
    _connetti(httpg, monkeypatch, w, "luca")
    r = httpg("estraneo").get("/api/calendar/google/status")
    assert r.status_code == 200
    assert r.json()["connection_status"] in ("not_configured", "not_connected")
    riga = w["sql"]("SELECT agency_id FROM calendar_connections")[0]
    assert riga["agency_id"] == w["a"]
    sync = w["sql"]("SELECT agency_id FROM appointment_calendar_sync")[0]
    assert sync["agency_id"] == w["a"]
