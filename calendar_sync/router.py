"""A30-9B - la superficie HTTP di Google Calendar: `/api/calendar/google`.

MONTATO in `main.py` (solo il mount: nessuna logica OAuth/Google in
`main.py`, §37). Tutte le rotte, TRANNE il callback, passano da
`require_operator` come in `appointments/router.py`: nessuna accetta
`agency_id` o `user_id` dal chiamante (§5: "il browser NON puo' inviare
agency_id/user_id"), sempre da `ctx.require_agency()` / `ctx.user_id`.

Il CALLBACK e' GET perche' Google ci arriva con un redirect del browser
(§4): richiede comunque una sessione operatore valida (§7, §30) - il legame
con lo `state` e' quello che impedisce a chiunque altro di usarlo.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse

from core.database import core_cursor
from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import config as gcal_config
from . import crypto, oauth, repository
from . import service as gcal_service

router = APIRouter(prefix="/api/calendar/google", tags=["calendar_sync"])

#: §9: dove il browser torna dopo il callback. Mai il `code` nella pagina
#: finale. La query string sta PRIMA del `#`, non dopo: il router della OS
#: Shell (hash routing) legge il nome della rotta da tutto cio' che segue
#: `#/`, senza spezzarlo su un eventuale `?` - un `#/agenda?x=y` non
#: corrisponderebbe alla rotta `agenda` e la pagina mostrerebbe "non
#: trovata". Con la query PRIMA del `#` il router non la vede nemmeno, ed e'
#: la pagina stessa (`agenda-page.js`) a leggerla da `window.location.search`.
_AGENDA_URL = "/os/#/agenda"
_AGENDA_BASE = "/os/"
#: Il ritorno all'Agenda e' un 302 Found: un redirect top-level del browser
#: (GET -> GET) dopo il giro OAuth, lo stesso stato che il certificatore live
#: (`scripts/p26_6_live_cert.py`, CALENDAR_SYNC) e il suo doppio attendono.
#: Esplicito, perche' `RedirectResponse` senza `status_code` risponderebbe
#: 307 (il default di Starlette), e nessun contratto di A30-9B lo ha scelto.
_REDIRECT_STATUS = 302


class _Richiesta(Exception):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


def _risposta(status, code, detail, **extra):
    corpo = dict(extra)
    corpo.update({"detail": detail, "code": code})
    return JSONResponse(status_code=status, content=jsonable_encoder(corpo))


def _x(funzione, *args, status=200, **kwargs):
    try:
        esito = funzione(*args, **kwargs)
    except _Richiesta as exc:
        return _risposta(exc.status, exc.code, exc.detail)
    except PlatformAdminAgencyRequired:
        return _risposta(403, "PLATFORM_ADMIN_AGENCY_REQUIRED", "Scegli un'agenzia")
    return JSONResponse(status_code=status, content=jsonable_encoder(esito))


def _operatore_richiesto(ctx: OperatorContext) -> int:
    if ctx.user_id is None:
        raise _Richiesta(403, "GOOGLE_OPERATOR_REQUIRED",
                         "Serve un operatore autenticato per collegare Google Calendar")
    return ctx.user_id


# ---------------------------------------------------------------------------
# STATUS (§10)
# ---------------------------------------------------------------------------

def _status(ctx: OperatorContext):
    configured = False
    try:
        gcal_config.require_config()
        configured = True
    except gcal_config.GoogleCalendarConfigError:
        configured = False
    enabled = gcal_config.is_enabled()
    base = {"configured": configured, "enabled": enabled, "connection_status": "not_configured",
            "connected_at": None, "needs_reauth": False, "last_error_code": None,
            "pending_sync_count": 0, "failed_sync_count": 0}
    if not enabled or ctx.user_id is None or ctx.agency_id is None:
        return base
    agency_id = ctx.require_agency()
    with core_cursor(commit=False) as (_, cur):
        conn = repository.connection_for_user(cur, agency_id, ctx.user_id)
        conteggi = ({"pending_sync_count": 0, "failed_sync_count": 0} if conn is None else
                   repository.connection_sync_counts(cur, agency_id, conn["id"]))
    if conn is None:
        base["connection_status"] = "not_connected"
        return base
    base.update(conteggi)
    base["connection_status"] = conn["status"]
    base["connected_at"] = conn["connected_at"]
    base["needs_reauth"] = conn["status"] == "needs_reauth"
    base["last_error_code"] = conn["last_error_code"]
    return base


@router.get("/status")
def status(ctx: OperatorContext = Depends(require_operator)):
    return _x(_status, ctx)


# ---------------------------------------------------------------------------
# CONNECT (§5)
# ---------------------------------------------------------------------------

def _connect(ctx: OperatorContext):
    agency_id = ctx.require_agency()
    user_id = _operatore_richiesto(ctx)
    try:
        cfg = gcal_config.require_config()
    except gcal_config.GoogleCalendarConfigError:
        raise _Richiesta(409, "GOOGLE_NOT_CONFIGURED",
                         "Google Calendar non e' configurato per questo ambiente")
    try:
        keyring = crypto.require_keyring()
    except crypto.CalendarCryptoNotConfigured:
        raise _Richiesta(409, "GOOGLE_NOT_CONFIGURED",
                         "Google Calendar non e' configurato per questo ambiente")

    raw_state, state_hash = gcal_service.new_oauth_state()
    code_verifier = oauth.new_code_verifier()
    with core_cursor(commit=True) as (_, cur):
        if not repository.membership_active(cur, agency_id, user_id):
            raise _Richiesta(403, "GOOGLE_MEMBERSHIP_INACTIVE",
                             "Solo un membro attivo dell'agenzia puo' collegare Google Calendar")
        repository.create_oauth_state(cur, agency_id=agency_id, user_id=user_id,
                                      state_hash=state_hash,
                                      code_verifier=keyring.encrypt(code_verifier))
    url = oauth.authorization_url(cfg, state=raw_state, code_verifier=code_verifier)
    return {"authorization_url": url}


@router.post("/connect")
def connect(ctx: OperatorContext = Depends(require_operator)):
    return _x(_connect, ctx, status=200)


# ---------------------------------------------------------------------------
# CALLBACK (§7, §8, §9) - GET: redirect del browser da Google
# ---------------------------------------------------------------------------

def _redirect_errore(code: str) -> RedirectResponse:
    # Nessun dettaglio nella query string di ritorno: solo un codice stabile,
    # utile alla UI, mai un agency_id/user_id/stato interno.
    from urllib.parse import urlencode

    return RedirectResponse(f"{_AGENDA_BASE}?{urlencode({'google_calendar_error': code})}#/agenda",
                            status_code=_REDIRECT_STATUS)


def _callback(request: Request, ctx: OperatorContext, *, id_token_verifier=None):
    try:
        agency_id = ctx.require_agency()
    except PlatformAdminAgencyRequired:
        return _redirect_errore("GOOGLE_OPERATOR_REQUIRED")
    if ctx.user_id is None:
        return _redirect_errore("GOOGLE_OPERATOR_REQUIRED")
    user_id = ctx.user_id

    errore_google = request.query_params.get("error")
    if errore_google:
        return _redirect_errore("GOOGLE_OAUTH_DENIED")
    code = request.query_params.get("code")
    raw_state = request.query_params.get("state")
    if not code or not raw_state:
        return _redirect_errore("GOOGLE_OAUTH_DENIED")

    try:
        cfg = gcal_config.require_config()
    except gcal_config.GoogleCalendarConfigError:
        return _redirect_errore("GOOGLE_NOT_CONFIGURED")
    try:
        keyring = crypto.require_keyring()
    except crypto.CalendarCryptoNotConfigured:
        return _redirect_errore("GOOGLE_NOT_CONFIGURED")

    state_hash = gcal_service.hash_oauth_state(raw_state)
    with core_cursor(commit=True) as (_, cur):
        consumato = repository.consume_oauth_state(cur, agency_id=agency_id, user_id=user_id,
                                                    state_hash=state_hash)
        if consumato is None:
            codice = repository.classify_oauth_state_failure(
                cur, agency_id=agency_id, user_id=user_id, state_hash=state_hash)
            errore_state = codice
        else:
            errore_state = None
        if consumato is not None and not repository.membership_active(cur, agency_id, user_id):
            errore_state = "GOOGLE_MEMBERSHIP_INACTIVE"
            consumato = None
        if consumato is not None:
            try:
                code_verifier = keyring.decrypt(
                    (consumato["code_verifier_ciphertext"], consumato["token_key_id"]))
            except crypto.CalendarCryptoError:
                errore_state = "GOOGLE_OAUTH_STATE_INVALID"
                consumato = None
    if consumato is None:
        return _redirect_errore(errore_state or "GOOGLE_OAUTH_STATE_INVALID")

    kwargs = {} if id_token_verifier is None else {"id_token_verifier": id_token_verifier}
    try:
        scambiato = oauth.exchange_code(cfg, code=code, code_verifier=code_verifier, **kwargs)
    except oauth.OAuthError as exc:
        return _redirect_errore(exc.code)

    # §9: tutto (upsert connessione + backfill) in UNA transazione; NESSUNA
    # chiamata Calendar qui - solo l'exchange OAuth, gia' fatto sopra.
    with core_cursor(commit=True) as (_, cur):
        if not repository.membership_active(cur, agency_id, user_id):
            return _redirect_errore("GOOGLE_MEMBERSHIP_INACTIVE")
        keyring = crypto.require_keyring()
        repository.upsert_connection(
            cur, agency_id=agency_id, user_id=user_id, provider_subject=scambiato.subject,
            refresh_token=keyring.encrypt(scambiato.refresh_token),
            granted_scopes=scambiato.granted_scopes)
        for appointment_id in repository.future_syncable_appointment_ids(cur, agency_id, user_id):
            repository.mark_dirty_with_cursor(cur, agency_id, appointment_id,
                                              deployment_namespace=cfg.deployment_namespace)
    return RedirectResponse(_AGENDA_URL, status_code=_REDIRECT_STATUS)


@router.get("/callback")
def callback(request: Request, ctx: OperatorContext = Depends(require_operator)):
    return _callback(request, ctx)


# ---------------------------------------------------------------------------
# DISCONNECT (§25)
# ---------------------------------------------------------------------------

def _disconnect(ctx: OperatorContext):
    agency_id = ctx.require_agency()
    user_id = _operatore_richiesto(ctx)
    with core_cursor(commit=False) as (_, cur):
        conn = repository.connection_for_user(cur, agency_id, user_id)
    if conn is None:
        raise _Richiesta(404, "GOOGLE_NOT_CONNECTED", "Nessuna connessione Google da scollegare")
    # 1. token utilizzabile se possibile; 2. revoca BEST EFFORT (mai blocca).
    if conn["status"] == "connected":
        try:
            with core_cursor(commit=False) as (_, cur):
                segreto = repository.connection_secret(cur, agency_id, conn["id"])
            if segreto is not None:
                token = crypto.require_keyring().decrypt(segreto)
                oauth.revoke_refresh_token(token)
        except Exception:  # noqa: BLE001 - best effort: il disconnect locale non dipende da questo
            pass
    # 3. transazione locale: SEMPRE eseguita, anche se la revoca e' fallita.
    with core_cursor(commit=True) as (_, cur):
        repository.mark_connection_disconnected(cur, agency_id, conn["id"])
    return {"status": "disconnected"}


@router.post("/disconnect")
def disconnect(ctx: OperatorContext = Depends(require_operator)):
    return _x(_disconnect, ctx)


# ---------------------------------------------------------------------------
# RESYNC (§26)
# ---------------------------------------------------------------------------

def _resync(ctx: OperatorContext):
    agency_id = ctx.require_agency()
    user_id = _operatore_richiesto(ctx)
    namespace = gcal_config.hook_deployment_namespace()
    if namespace is None:
        raise _Richiesta(409, "GOOGLE_NOT_CONFIGURED",
                         "Google Calendar non e' configurato per questo ambiente")
    with core_cursor(commit=True) as (_, cur):
        if not repository.membership_active(cur, agency_id, user_id):
            raise _Richiesta(403, "GOOGLE_MEMBERSHIP_INACTIVE",
                             "Solo un membro attivo dell'agenzia puo' risincronizzare")
        ids = repository.future_syncable_appointment_ids(cur, agency_id, user_id)
        for appointment_id in ids:
            repository.mark_dirty_with_cursor(cur, agency_id, appointment_id,
                                              deployment_namespace=namespace)
    return {"status": "ok", "requeued": len(ids)}


@router.post("/resync")
def resync(ctx: OperatorContext = Depends(require_operator)):
    return _x(_resync, ctx)
