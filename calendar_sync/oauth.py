"""A30-9B - il flusso OAuth 2.0 web-server con PKCE (S256) verso Google.

Verificato (§6) su `google-auth-oauthlib` reale, INSTALLATA in questo
ambiente (vedi il report del gate per la versione esatta):
`google_auth_oauthlib.flow.Flow` accetta un `code_verifier` esplicito (noi lo
generiamo, non lo lascia generare alla libreria: dobbiamo salvarlo cifrato
PRIMA del redirect) e calcola da solo `code_challenge`/`code_challenge_method`
in `authorization_url()` (S256, RFC 7636); `fetch_token()` invia lo stesso
`code_verifier` nello scambio del `code`. Nessun protocollo PKCE artigianale:
e' la libreria a farlo.

Questo modulo non fa MAI rete per la generazione dello state/verifier (solo
`secrets`, puro); la rete (autorizzazione, token, verifica id_token) avviene
solo dentro `exchange_code`, con un timeout esplicito passato dal chiamante
tramite `requests.Session`. I test (§39) non toccano MAI Google reale:
passano un `id_token_verifier` finto.
"""
from __future__ import annotations

from dataclasses import dataclass

from google_auth_oauthlib.flow import Flow

from .config import GoogleConfig
from .service import hash_oauth_state, new_oauth_state  # noqa: F401 - ri-esportati

#: §3: scope minimo. MAI email/profile/userinfo per A30-9B.
SCOPES = ("openid", "https://www.googleapis.com/auth/calendar.events.owned")


class OAuthError(Exception):
    """Errore controllato del flusso OAuth. Mai un segreto nel messaggio."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


def _client_config(config: GoogleConfig) -> dict:
    return {
        "web": {
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [config.redirect_uri],
        }
    }


def build_flow(config: GoogleConfig, *, code_verifier: str) -> Flow:
    """Un `Flow` per QUESTA sola richiesta: verifier nostro, mai generato
    dalla libreria (dobbiamo poterlo cifrare e salvare prima del redirect)."""
    return Flow.from_client_config(
        _client_config(config), scopes=list(SCOPES), redirect_uri=config.redirect_uri,
        code_verifier=code_verifier, autogenerate_code_verifier=False,
    )


def new_code_verifier() -> str:
    """RFC 7636 §4.1: 43-128 caratteri di [A-Za-z0-9-._~]. `secrets.token_urlsafe`
    produce '-' e '_' e nessun padding: valido cosi' com'e'."""
    import secrets

    return secrets.token_urlsafe(64)  # ~86 caratteri


def authorization_url(config: GoogleConfig, *, state: str, code_verifier: str) -> str:
    """L'URL di autorizzazione ESATTO (§5): `response_type=code` (implicito
    nella libreria per un flusso "web"), `access_type=offline`,
    `include_granted_scopes=true`, `prompt=consent`, `redirect_uri` esattamente
    quello configurato, scope minimale, PKCE S256."""
    flow = build_flow(config, code_verifier=code_verifier)
    url, _ = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent", state=state,
    )
    return url


@dataclass(frozen=True)
class ExchangedToken:
    refresh_token: str
    granted_scopes: tuple
    subject: str  # `sub` dall'id_token verificato


def _default_id_token_verifier(id_token_jwt: str, client_id: str) -> dict:
    """La verifica VERA (rete verso Google per le chiavi pubbliche): audience
    e issuer verificati dalla libreria stessa."""
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token as google_id_token

    return google_id_token.verify_oauth2_token(id_token_jwt, Request(), client_id)


def exchange_code(config: GoogleConfig, *, code: str, code_verifier: str,
                  id_token_verifier=None) -> ExchangedToken:
    """§8: scambio server-side del `code`. Verifica scope, refresh token
    presente, id_token (audience = nostro client_id, issuer Google), estrae
    `sub`. `id_token_verifier` e' iniettabile: i test (§39) passano una
    verifica finta, mai Google reale. Risolto DENTRO la funzione (mai come
    default legato alla definizione): un monkeypatch del chiamante deve
    poter sostituire la verifica reale in ogni test, non solo alla prima
    importazione del modulo."""
    if id_token_verifier is None:
        id_token_verifier = _default_id_token_verifier
    flow = build_flow(config, code_verifier=code_verifier)
    try:
        token = flow.fetch_token(code=code)
    except Exception as errore:  # noqa: BLE001 - qualunque errore della libreria e' opaco
        raise OAuthError("GOOGLE_TOKEN_EXCHANGE_FAILED") from errore

    concessi = set((token.get("scope") or "").split())
    mancanti = set(SCOPES) - concessi
    if mancanti:
        raise OAuthError("GOOGLE_SCOPE_INSUFFICIENT")

    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise OAuthError("GOOGLE_REFRESH_TOKEN_MISSING")

    id_token_jwt = token.get("id_token")
    if not id_token_jwt:
        raise OAuthError("GOOGLE_ID_TOKEN_MISSING")
    try:
        rivendicazioni = id_token_verifier(id_token_jwt, config.client_id)
    except Exception as errore:  # noqa: BLE001 - mai il dettaglio della libreria
        raise OAuthError("GOOGLE_ID_TOKEN_INVALID") from errore
    if rivendicazioni.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise OAuthError("GOOGLE_ID_TOKEN_INVALID")
    subject = rivendicazioni.get("sub")
    if not subject or not isinstance(subject, str):
        raise OAuthError("GOOGLE_ID_TOKEN_INVALID")

    return ExchangedToken(refresh_token=refresh_token, granted_scopes=tuple(sorted(concessi)),
                          subject=subject)


REVOKE_URI = "https://oauth2.googleapis.com/revoke"
_REVOKE_TIMEOUT = (5, 10)


def revoke_refresh_token(refresh_token: str) -> bool:
    """§25: revoca BEST EFFORT. Vero se Google ha risposto 200; qualunque
    altro esito (rete, timeout, 4xx/5xx) torna False senza mai propagare
    un'eccezione: il disconnect locale deve riuscire comunque."""
    import requests

    try:
        risposta = requests.post(REVOKE_URI, params={"token": refresh_token},
                                 timeout=_REVOKE_TIMEOUT)
        return risposta.status_code == 200
    except requests.exceptions.RequestException:
        return False
