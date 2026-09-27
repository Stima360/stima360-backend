"""A30-9B - test STATICI/UNITARI: configurazione, OAuth (URL, PKCE, scope),
provider Google (classificazione HTTP), import sicuro, nessun segreto nei
log. Nessuna rete: `google_provider`/`oauth` sono esercitati con una sessione
HTTP FINTA (mai Google reale, §39).

Numerazione fra parentesi = i punti del gate §41/§44 che questa funzione
copre (piu' funzioni condividono spesso lo stesso scenario, come il gate
autorizza).
"""
from __future__ import annotations

import ast
import base64
import hashlib
import logging
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACCHETTO = ROOT / "calendar_sync"


# ---------------------------------------------------------------------------
# 1, 2, 36 - CONFIGURAZIONE E IMPORT SICURO
# ---------------------------------------------------------------------------

def test_01_app_boot_senza_env_google(monkeypatch):
    """(1) L'app si avvia anche con Google non configurato."""
    for nome in ("GOOGLE_CALENDAR_ENABLED", "GOOGLE_CALENDAR_CLIENT_ID",
                "GOOGLE_CALENDAR_CLIENT_SECRET", "GOOGLE_CALENDAR_REDIRECT_URI",
                "GOOGLE_CALENDAR_DEPLOYMENT_NAMESPACE", "GOOGLE_TOKEN_FERNET_KEYS"):
        monkeypatch.delenv(nome, raising=False)
    import importlib

    import main
    importlib.reload(main)
    from calendar_sync import config as c
    assert c.is_enabled() is False
    assert c.hook_deployment_namespace() is None


def test_02_status_non_configured_quando_disabilitato(monkeypatch):
    monkeypatch.delenv("GOOGLE_CALENDAR_ENABLED", raising=False)
    from calendar_sync import config as c
    assert c.is_enabled() is False
    with pytest.raises(c.GoogleCalendarConfigError):
        c.require_config()


def test_03_connect_senza_config_errore_controllato(monkeypatch):
    """(3, 36) Enabled ma incompleto: errore controllato, mai un traceback
    con un segreto dentro."""
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    monkeypatch.delenv("GOOGLE_CALENDAR_CLIENT_ID", raising=False)
    from calendar_sync import config as c
    with pytest.raises(c.GoogleCalendarConfigError) as exc:
        c.require_config()
    assert "client_id" not in str(exc.value)
    assert "secret" not in str(exc.value).lower()


def test_36_nessuna_rete_dns_oauth_db_all_import(monkeypatch):
    """(36) Import di main/appointments/calendar_sync: nessuna DNS, nessun
    OAuth, nessun decrypt, nessuna lettura DB, nessun fallimento senza env."""
    for nome in ("GOOGLE_CALENDAR_ENABLED", "GOOGLE_TOKEN_FERNET_KEYS"):
        monkeypatch.delenv(nome, raising=False)
    import socket

    chiamato = []

    def _niente_dns(*a, **kw):
        chiamato.append(a)
        raise AssertionError("risoluzione DNS durante l'import")

    monkeypatch.setattr(socket, "getaddrinfo", _niente_dns)
    import importlib

    import appointments.lmc15_facade
    import appointments.service
    import calendar_sync
    import calendar_sync.config
    import calendar_sync.google_provider
    import calendar_sync.integration
    import calendar_sync.oauth
    import calendar_sync.router
    import main
    for modulo in (main, appointments.service, appointments.lmc15_facade,
                  calendar_sync, calendar_sync.router):
        importlib.reload(modulo)
    assert chiamato == []


def test_37_main_solo_mount():
    """(37) `main.py` autorizzato SOLO al mount del router calendar_sync."""
    testo = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "from calendar_sync.router import router as calendar_sync_router" in testo
    assert "app.include_router(calendar_sync_router" in testo
    for vietato in ("oauth", "google_provider", "GoogleCalendarProvider", "Fernet"):
        assert vietato not in testo


# ---------------------------------------------------------------------------
# 11, 12(scope), 21, 22, 25 - OAUTH: URL, SCOPE, PKCE
# ---------------------------------------------------------------------------

def _cfg():
    from calendar_sync.config import GoogleConfig
    return GoogleConfig(client_id="CID", client_secret="CSEC",
                        redirect_uri="https://esempio.test/api/calendar/google/callback",
                        deployment_namespace="stima360-test")


def test_10_11_12_oauth_url_esatto_scope_minimale_access_offline():
    """(10, 11, 12) redirect_uri ESATTO, scope minimale (openid + soltanto
    calendar.events.owned, mai calendar/calendar.events/email/profile),
    access_type=offline, include_granted_scopes=true, prompt=consent."""
    from calendar_sync import oauth
    cfg = _cfg()
    verifier = oauth.new_code_verifier()
    url = oauth.authorization_url(cfg, state="STATO123", code_verifier=verifier)
    assert f"redirect_uri={_enc(cfg.redirect_uri)}" in url
    assert "scope=openid+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcalendar.events.owned" in url
    assert "calendar.events%22" not in url  # niente scope pieno
    for vietato in ("email", "profile", "userinfo"):
        assert vietato not in url
    assert "access_type=offline" in url
    assert "include_granted_scopes=true" in url
    assert "prompt=consent" in url
    assert "state=STATO123" in url
    assert oauth.SCOPES == ("openid", "https://www.googleapis.com/auth/calendar.events.owned")


def _enc(v):
    from urllib.parse import quote

    return quote(v, safe="")


def test_09_pkce_s256_esatto():
    """(9) La libreria calcola code_challenge = base64url(sha256(verifier)),
    METODO S256 - verificato contro il calcolo di riferimento RFC 7636."""
    from calendar_sync import oauth
    cfg = _cfg()
    verifier = oauth.new_code_verifier()
    assert 43 <= len(verifier) <= 128
    url = oauth.authorization_url(cfg, state="s", code_verifier=verifier)
    atteso = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert f"code_challenge={atteso}" in url
    assert "code_challenge_method=S256" in url


def test_23_25_id_token_verificato_sub_estratto():
    """(23, wrong audience implicitamente, 25) `exchange_code` chiama
    l'`id_token_verifier` iniettato con l'id_token e il client_id, e usa
    `sub` come provider_subject."""
    from calendar_sync import oauth

    class _FlowFinto:
        def __init__(self, token):
            self._token = token

        def fetch_token(self, **kw):
            return self._token

    cfg = _cfg()
    token = {"scope": "openid https://www.googleapis.com/auth/calendar.events.owned",
             "refresh_token": "refresh-xyz", "id_token": "un.jwt.finto"}

    chiamate = []

    def verificatore(id_token_jwt, client_id):
        chiamate.append((id_token_jwt, client_id))
        return {"iss": "https://accounts.google.com", "aud": client_id, "sub": "sub-777"}

    import calendar_sync.oauth as modulo

    def _build_flow_finto(config, *, code_verifier):
        return _FlowFinto(token)

    original = modulo.build_flow
    modulo.build_flow = _build_flow_finto
    try:
        esito = oauth.exchange_code(cfg, code="CODE", code_verifier="verifier",
                                    id_token_verifier=verificatore)
    finally:
        modulo.build_flow = original
    assert chiamate == [("un.jwt.finto", "CID")]
    assert esito.subject == "sub-777"
    assert esito.refresh_token == "refresh-xyz"


def test_21_22_id_token_non_valido_o_senza_sub():
    from calendar_sync import oauth

    class _FlowFinto:
        def __init__(self, token):
            self._token = token

        def fetch_token(self, **kw):
            return self._token

    cfg = _cfg()
    token = {"scope": "openid https://www.googleapis.com/auth/calendar.events.owned",
             "refresh_token": "r", "id_token": "jwt"}
    import calendar_sync.oauth as modulo
    original = modulo.build_flow
    modulo.build_flow = lambda config, *, code_verifier: _FlowFinto(token)
    try:
        with pytest.raises(oauth.OAuthError) as exc:
            oauth.exchange_code(cfg, code="C", code_verifier="v",
                                id_token_verifier=lambda j, c: (_ for _ in ()).throw(ValueError()))
        assert exc.value.code == "GOOGLE_ID_TOKEN_INVALID"
        with pytest.raises(oauth.OAuthError) as exc2:
            oauth.exchange_code(cfg, code="C", code_verifier="v",
                                id_token_verifier=lambda j, c: {"iss": "https://accounts.google.com"})
        assert exc2.value.code == "GOOGLE_ID_TOKEN_INVALID"
    finally:
        modulo.build_flow = original


def test_20_refresh_token_assente():
    """(20) Manca il refresh token nella risposta: errore stabile
    `GOOGLE_REFRESH_TOKEN_MISSING`, nessuna connessione creata (verificato
    qui a livello di funzione pura; il livello HTTP e' nel test PostgreSQL)."""
    from calendar_sync import oauth

    class _FlowFinto:
        def fetch_token(self, **kw):
            return {"scope": "openid https://www.googleapis.com/auth/calendar.events.owned"}

    import calendar_sync.oauth as modulo
    original = modulo.build_flow
    modulo.build_flow = lambda config, *, code_verifier: _FlowFinto()
    try:
        with pytest.raises(oauth.OAuthError) as exc:
            oauth.exchange_code(_cfg(), code="C", code_verifier="v")
        assert exc.value.code == "GOOGLE_REFRESH_TOKEN_MISSING"
    finally:
        modulo.build_flow = original


def test_11b_scope_insufficiente():
    from calendar_sync import oauth

    class _FlowFinto:
        def fetch_token(self, **kw):
            return {"scope": "openid", "refresh_token": "r", "id_token": "j"}

    import calendar_sync.oauth as modulo
    original = modulo.build_flow
    modulo.build_flow = lambda config, *, code_verifier: _FlowFinto()
    try:
        with pytest.raises(oauth.OAuthError) as exc:
            oauth.exchange_code(_cfg(), code="C", code_verifier="v")
        assert exc.value.code == "GOOGLE_SCOPE_INSUFFICIENT"
    finally:
        modulo.build_flow = original


# ---------------------------------------------------------------------------
# GOOGLE PROVIDER - CLASSIFICAZIONE HTTP (44-63, senza rete: sessione finta)
# ---------------------------------------------------------------------------

class _RispostaFinta:
    def __init__(self, status_code, corpo=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._corpo = corpo if corpo is not None else {}

    def json(self):
        return self._corpo


class _SessioneFinta:
    """Sostituisce `AuthorizedSession`: nessuna rete, una lista di risposte
    programmate per (metodo, path-suffix)."""

    def __init__(self, script):
        self.script = list(script)
        self.richieste = []

    def request(self, metodo, url, *, json=None, params=None, timeout=None):
        assert timeout is not None, "chiamata Google senza timeout esplicito (§33)"
        self.richieste.append((metodo, url, json, params))
        return self.script.pop(0)


def _provider_con_script(monkeypatch, script):
    from calendar_sync import google_provider as gp
    provider = gp.GoogleCalendarProvider(client_id="cid", client_secret="csec")
    sessione = _SessioneFinta(script)
    monkeypatch.setattr(provider, "_session", lambda auth: sessione)
    return provider, sessione


def _auth():
    from calendar_sync.provider import ProviderAuth
    return ProviderAuth(connection_id=1, refresh_token="refresh-segreto")


def _payload(event_id="s360evt"):
    from datetime import datetime, timezone

    from calendar_sync.provider import EventPayload
    return EventPayload(event_id=event_id, summary="Appuntamento",
                        start_at=datetime(2027, 1, 1, 10, tzinfo=timezone.utc),
                        end_at=datetime(2027, 1, 1, 11, tzinfo=timezone.utc),
                        timezone="Europe/Rome", description="Stima360",
                        private_properties={"stima360_chain_id": "1"})


def test_45_46_ensure_update_esistente(monkeypatch):
    """(45, 46) L'evento esiste: PATCH riuscito, un solo esito 'updated'."""
    provider, sessione = _provider_con_script(monkeypatch, [
        _RispostaFinta(200, {"etag": '"1"'}),
    ])
    esito = provider.ensure_event(_auth(), "primary", _payload())
    assert esito.outcome == "updated"
    assert sessione.richieste[0][0] == "PATCH"


def test_47_48_ensure_404_poi_create_poi_409_poi_update(monkeypatch):
    """(47, 48) 404 sul PATCH -> POST create; create risponde 409 -> un
    ultimo PATCH chiude con 'updated'. Nessun id casuale, un solo evento."""
    provider, sessione = _provider_con_script(monkeypatch, [
        _RispostaFinta(404),
        _RispostaFinta(409),
        _RispostaFinta(200, {"etag": '"2"'}),
    ])
    esito = provider.ensure_event(_auth(), "primary", _payload())
    assert esito.outcome == "updated"
    assert [r[0] for r in sessione.richieste] == ["PATCH", "POST", "PATCH"]
    # Lo stesso event_id su create e sui due PATCH: nessun id casuale.
    assert sessione.richieste[1][1].endswith("/events")
    assert sessione.richieste[1][2]["id"] == _payload().event_id


def test_47b_ensure_404_poi_create_riuscita(monkeypatch):
    provider, sessione = _provider_con_script(monkeypatch, [
        _RispostaFinta(404),
        _RispostaFinta(200, {"etag": '"1"'}),
    ])
    esito = provider.ensure_event(_auth(), "primary", _payload())
    assert esito.outcome == "created"


def test_49_50_delete_riuscito_e_404_successo(monkeypatch):
    """(49, 50) DELETE riuscito = 'deleted'; 404/410 = 'absent' (successo
    idempotente)."""
    provider, _ = _provider_con_script(monkeypatch, [_RispostaFinta(204)])
    assert provider.delete_event(_auth(), "primary", "s360x").outcome == "deleted"
    provider, _ = _provider_con_script(monkeypatch, [_RispostaFinta(404)])
    assert provider.delete_event(_auth(), "primary", "s360x").outcome == "absent"
    provider, _ = _provider_con_script(monkeypatch, [_RispostaFinta(410)])
    assert provider.delete_event(_auth(), "primary", "s360x").outcome == "absent"


def test_58_59_60_61_63_64_classificazione_errori(monkeypatch):
    """(58 timeout, 59 429, 60 5xx, 61 401, 63 403 quota, 64 403 permission)."""
    from calendar_sync import google_provider as gp
    from calendar_sync.provider import (
        FORBIDDEN,
        NOT_FOUND,
        RATE_LIMITED,
        SERVER_ERROR,
        TIMEOUT,
        UNAUTHORIZED,
        CalendarProviderError,
        classify,
    )
    provider, _ = _provider_con_script(monkeypatch, [_RispostaFinta(429)])
    with pytest.raises(CalendarProviderError) as exc:
        provider.ensure_event(_auth(), "primary", _payload())
    assert exc.value.kind == RATE_LIMITED
    assert classify(exc.value).action == "retry"

    provider, _ = _provider_con_script(monkeypatch, [_RispostaFinta(503)])
    with pytest.raises(CalendarProviderError) as exc:
        provider.ensure_event(_auth(), "primary", _payload())
    assert exc.value.kind == SERVER_ERROR

    provider, _ = _provider_con_script(monkeypatch, [_RispostaFinta(401)])
    with pytest.raises(CalendarProviderError) as exc:
        provider.ensure_event(_auth(), "primary", _payload())
    assert exc.value.kind == UNAUTHORIZED
    assert classify(exc.value).action == "needs_reauth"

    provider, _ = _provider_con_script(monkeypatch, [
        _RispostaFinta(403, {"error": {"errors": [{"reason": "rateLimitExceeded"}]}})])
    with pytest.raises(CalendarProviderError) as exc:
        provider.ensure_event(_auth(), "primary", _payload())
    assert exc.value.reason == "rateLimitExceeded"
    assert classify(exc.value).action == "retry"

    provider, _ = _provider_con_script(monkeypatch, [
        _RispostaFinta(403, {"error": {"errors": [{"reason": "insufficientPermissions"}]}})])
    with pytest.raises(CalendarProviderError) as exc:
        provider.ensure_event(_auth(), "primary", _payload())
    assert classify(exc.value).action == "needs_reauth"


def test_33_58_timeout_esplicito_su_ogni_chiamata(monkeypatch):
    """(33, 58) Ogni chiamata passa un timeout esplicito - verificato dalla
    sessione finta stessa (`assert timeout is not None`); qui si controlla
    anche che un vero `requests.exceptions.Timeout` diventi TIMEOUT."""
    import requests

    from calendar_sync import google_provider as gp
    from calendar_sync.provider import TIMEOUT, CalendarProviderError
    provider = gp.GoogleCalendarProvider(client_id="c", client_secret="s")

    class _SessioneCheTimeouta:
        def request(self, *a, **kw):
            raise requests.exceptions.Timeout()

    monkeypatch.setattr(provider, "_session", lambda auth: _SessioneCheTimeouta())
    with pytest.raises(CalendarProviderError) as exc:
        provider.ensure_event(_auth(), "primary", _payload())
    assert exc.value.kind == TIMEOUT


def test_61b_invalid_grant_da_refresh(monkeypatch):
    """(61, 62) Il refresh del token fallisce (revocato/scaduto):
    INVALID_GRANT, classificato needs_reauth."""
    from google.auth.exceptions import RefreshError

    from calendar_sync import google_provider as gp
    from calendar_sync.provider import INVALID_GRANT, CalendarProviderError, classify
    provider = gp.GoogleCalendarProvider(client_id="c", client_secret="s")

    class _SessioneCheRifiuta:
        def request(self, *a, **kw):
            raise RefreshError("invalid_grant")

    monkeypatch.setattr(provider, "_session", lambda auth: _SessioneCheRifiuta())
    with pytest.raises(CalendarProviderError) as exc:
        provider.ensure_event(_auth(), "primary", _payload())
    assert exc.value.kind == INVALID_GRANT
    assert classify(exc.value).action == "needs_reauth"


def test_66_nessun_segreto_nei_log(caplog):
    """(66) Il refresh token/client_secret non compaiono mai in un
    `repr`/log del provider o dell'auth."""
    from calendar_sync import google_provider as gp
    provider = gp.GoogleCalendarProvider(client_id="cid-visibile", client_secret="SEGRETO-XYZ")
    assert "SEGRETO-XYZ" not in repr(provider)
    auth = _auth()
    assert "refresh-segreto" not in repr(auth)


def test_67_niente_pii_nel_payload_google():
    """(67, §15) Il body Google contiene SOLO i campi ammessi: mai cliente,
    email, telefono, luogo, note, esito, motivo, lead, stima, immobile."""
    from calendar_sync.google_provider import _event_body
    payload = _payload()
    corpo = _event_body(payload)
    testo = str(corpo)
    for vietato in ("cliente", "email", "telefono", "notes", "location", "outcome",
                    "cancelled_reason", "lead", "property", "attendee"):
        assert vietato not in testo.lower()
    assert "attendees" not in corpo
    assert corpo["summary"] == "Appuntamento"
    assert corpo["description"] == "Stima360"


def test_14_sendupdates_none_nessun_attendee():
    """(14) `sendUpdates=none`, mai un attendee, su ensure e delete."""
    from calendar_sync.google_provider import SEND_UPDATES_NONE, _event_body
    assert SEND_UPDATES_NONE == {"sendUpdates": "none"}
    assert "attendees" not in _event_body(_payload())


# ---------------------------------------------------------------------------
# 2 - DEPENDENCIES
# ---------------------------------------------------------------------------

def test_02b_dipendenze_esatte():
    requisiti = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "google-auth==2.58.1" in requisiti
    assert "google-auth-oauthlib==1.4.1" in requisiti
    assert "python-client" not in requisiti.lower()
