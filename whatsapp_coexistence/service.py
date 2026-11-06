"""WHATSAPP COEXISTENCE - collegare il numero dell'app WhatsApp Business alla
Cloud API con la procedura ufficiale di Meta (Embedded Signup), senza togliere
l'app dal telefono.

Chi: SOLO il titolare dell'agenzia (`agency_owner`) avvia un collegamento;
titolare e Super Admin che opera nell'agenzia ne vedono lo stato.

Il percorso, nell'ordine:
  1. il browser apre la procedura di Meta (SDK di Facebook, App ID e ID della
     configurazione pubblici, `GET /config`). Il titolare sceglie il proprio
     account e inquadra il QR sul telefono, nell'app WhatsApp Business;
  2. Meta restituisce al browser un codice monouso e gli identificativi del
     WABA e del numero; il browser li manda qui (`POST /onboardings`);
  3. il server scambia il codice con il segreto dell'app (mai nel browser) e
     usa il token ottenuto SOLO per questa richiesta: verifica che il numero
     sia davvero nel WABA, iscrive l'app ai webhook del WABA, chiede a Meta di
     sincronizzare rubrica e storico. Il token non si salva da nessuna parte;
  4. «Confermato da Meta» solo quando Meta stessa risponde che il numero e' nel
     WABA e attivo sulla Cloud API (`POST /onboardings/{id}/verify`, anche
     automatico al passo 3). Le prove di ricezione (messaggi, eco dal telefono,
     storico, rubrica) si contano dal database e si mostrano a parte: cio' che
     il CRM ha chiesto, cio' che Meta conferma e cio' che e' arrivato restano
     tre cose distinte.

Le chiamate successive (verifica, nuova richiesta di sincronizzazione) usano il
token di sistema della casella (`WHATSAPP_TOKEN__<phone_number_id>` o
`WHATSAPP_TOKEN`), come gli invii. Nessuna rotta invia messaggi a clienti.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

from core.database import core_cursor
from inbound_messages import routing
from message_replies import transport

from . import repository
from .enums import (
    APP_ID_ENV,
    APP_SECRET_ENV,
    CALLBACK_URL_ENV,
    CODE_EXCHANGE_FAILED_MESSAGE,
    CONFIG_ID_ENV,
    FORBIDDEN_MESSAGE,
    NOT_CONFIGURED_MESSAGE,
    NOT_FOUND_MESSAGE,
    NOT_INSTALLED_MESSAGE,
    NOT_ROUTED_MESSAGE,
    OWNER_ROLE,
    PHONE_AMBIGUOUS_MESSAGE,
    PHONE_NOT_IN_WABA_MESSAGE,
    STATUS_CONFIRMED,
    STATUS_FAILED,
    STATUS_LABELS,
    STATUS_REQUESTED,
    SYNC_CONTACTS,
    SYNC_HISTORY,
    SYNC_KINDS,
    TOKEN_MISSING_MESSAGE,
    VERIFY_TOKEN_ENV,
)
from .graph import Graph

log = logging.getLogger(__name__)


class CoexistenceError(Exception):
    status = 400

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


class Forbidden(CoexistenceError):
    status = 403


class NotFound(CoexistenceError):
    status = 404


class NotInstalled(CoexistenceError):
    status = 503


class NotConfigured(CoexistenceError):
    status = 503


class Rejected(CoexistenceError):
    status = 422


def make_graph(version: str) -> Graph:
    """Sostituibile nei test con un client finto."""
    return Graph(version)


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _is_owner(ctx) -> bool:
    return getattr(ctx, "role", None) == OWNER_ROLE


def _may_view(ctx) -> bool:
    return _is_owner(ctx) or bool(getattr(ctx, "is_platform_admin", False))


def _require_view(ctx):
    if not _may_view(ctx):
        raise Forbidden("COEXISTENCE_FORBIDDEN", FORBIDDEN_MESSAGE)


def _require_owner(ctx):
    if not _is_owner(ctx):
        raise Forbidden("COEXISTENCE_FORBIDDEN", FORBIDDEN_MESSAGE)


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _missing_config() -> list[str]:
    missing = [name for name in (APP_ID_ENV, CONFIG_ID_ENV, APP_SECRET_ENV) if not _env(name)]
    if transport.graph_version() is None:
        missing.append("WHATSAPP_GRAPH_VERSION")
    return missing


def config(ctx) -> dict:
    """Che cosa serve al browser per aprire la procedura di Meta. Solo valori
    pubblici; i nomi delle variabili mancanti, mai i loro valori."""
    _require_view(ctx)
    ctx.require_agency()
    with core_cursor() as (_, cur):
        installed = repository.installed(cur)
    missing = _missing_config()
    return {"installed": installed, "enabled": installed and not missing, "missing": missing,
            "app_id": _env(APP_ID_ENV) or None, "config_id": _env(CONFIG_ID_ENV) or None,
            "graph_version": transport.graph_version(), "can_connect": _is_owner(ctx),
            "callback_override": bool(_env(CALLBACK_URL_ENV) and _env(VERIFY_TOKEN_ENV))}


def _routed(agency_id: int, phone_number_id: str) -> bool:
    return routing.configured_routes().get(phone_number_id) == {agency_id}


def _view(cur, row: dict) -> dict:
    agency_id = row["agency_id"]
    number = row["phone_number_id"]
    ev = repository.evidence(cur, agency_id, number)
    history = ev.pop("history_last_event")
    details = row.get("meta_details") or {}
    return {
        "id": row["id"], "waba_id": row["waba_id"], "phone_number_id": number,
        "display_phone_number": row.get("display_phone_number"), "verified_name": row.get("verified_name"),
        "status": row["status"], "status_label": STATUS_LABELS.get(row["status"], row["status"]),
        "connected": row["status"] == STATUS_CONFIRMED,
        "steps": row.get("steps") or {}, "error_code": row.get("error_code"),
        "meta": {"platform_type": details.get("platform_type"), "is_on_biz_app": details.get("is_on_biz_app"),
                 "number_status": details.get("status"), "checked_at": details.get("checked_at")},
        "meta_confirmed_at": _iso(row.get("meta_confirmed_at")),
        "contacts_sync_requested_at": _iso(row.get("contacts_sync_requested_at")),
        "history_sync_requested_at": _iso(row.get("history_sync_requested_at")),
        "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
        "routed": _routed(agency_id, number),
        "token_present": transport.token_for(number) is not None,
        "evidence": {
            "live_messages": int(ev["live_messages"] or 0), "app_echoes": int(ev["app_echoes"] or 0),
            "history_messages": int(ev["history_messages"] or 0), "conversations": int(ev["conversations"] or 0),
            "last_live_at": _iso(ev["last_live_at"]),
            "app_contacts_present": int(ev["app_contacts"]["present"] or 0),
            "app_contacts_removed": int(ev["app_contacts"]["removed"] or 0),
            "app_contacts_synced_at": _iso(ev["app_contacts"]["last_synced_at"]),
            "history": ({"phase": history["details"].get("phase"), "progress": history["details"].get("progress"),
                         "errors": history["details"].get("errors"), "at": _iso(history["received_at"])}
                        if history else None),
        },
    }


def list_onboardings(ctx) -> dict:
    _require_view(ctx)
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        if not repository.installed(cur):
            return {"installed": False, "items": []}
        items = [_view(cur, r) for r in repository.list_for_agency(cur, agency_id)]
    return {"installed": True, "items": items, "can_connect": _is_owner(ctx)}


def _confirm(graph: Graph, token: str, waba_id: str, phone_number_id: str) -> tuple[bool, dict, str | None]:
    """(confermato, dettagli letti da Meta, codice d'errore). Confermato SOLO se
    Meta dice che il numero e' nel WABA e attivo sulla Cloud API."""
    ok, body, err = graph.waba_phone_numbers(token, waba_id)
    if not ok:
        return False, {}, err
    numbers = [n for n in (body or {}).get("data") or [] if isinstance(n, dict)]
    mine = next((n for n in numbers if str(n.get("id")) == phone_number_id), None)
    if mine is None:
        return False, {}, "phone_not_in_waba"
    ok, pn, err = graph.phone_number(token, phone_number_id)
    if not ok:
        return False, {}, err
    details = {"display_phone_number": re.sub(r"\D", "", str(pn.get("display_phone_number") or "")) or None,
               "verified_name": pn.get("verified_name"), "platform_type": pn.get("platform_type"),
               "status": pn.get("status"), "is_on_biz_app": pn.get("is_on_biz_app", mine.get("is_on_biz_app")),
               "code_verification_status": mine.get("code_verification_status"),
               "checked_at": datetime.now(timezone.utc).isoformat()}
    confirmed = str(details["platform_type"] or "").upper() == "CLOUD_API"
    return confirmed, details, None if confirmed else "not_on_cloud_api"


def start_onboarding(ctx, *, code: str, waba_id: str, phone_number_id: str | None,
                     business_id: str | None = None) -> dict:
    """Passo 3 del percorso (vedi il modulo). Il token della procedura vive solo
    dentro questa funzione."""
    _require_owner(ctx)
    agency_id = ctx.require_agency()
    missing = _missing_config()
    if missing:
        raise NotConfigured("COEXISTENCE_NOT_CONFIGURED", NOT_CONFIGURED_MESSAGE, missing=missing)
    with core_cursor() as (_, cur):
        if not repository.installed(cur):
            raise NotInstalled("COEXISTENCE_NOT_INSTALLED", NOT_INSTALLED_MESSAGE)
    graph = make_graph(transport.graph_version())
    steps: dict = {}

    ok, body, err = graph.exchange_code(_env(APP_ID_ENV), _env(APP_SECRET_ENV), code)
    token = (body or {}).get("access_token") if ok else None
    steps["code_exchange"] = {"ok": bool(token), "error": None if token else (err or "no_token")}
    if not token:
        raise Rejected("CODE_EXCHANGE_FAILED", CODE_EXCHANGE_FAILED_MESSAGE, steps=steps)

    ok, body, err = graph.waba_phone_numbers(token, waba_id)
    numbers = [n for n in (body or {}).get("data") or [] if isinstance(n, dict)] if ok else []
    steps["waba_numbers"] = {"ok": ok, "error": err, "count": len(numbers)}
    if not ok:
        raise Rejected("WABA_NOT_READABLE", PHONE_NOT_IN_WABA_MESSAGE, steps=steps)
    if not phone_number_id:
        if len(numbers) != 1:
            raise Rejected("PHONE_AMBIGUOUS", PHONE_AMBIGUOUS_MESSAGE, steps=steps,
                           numbers=[{"id": str(n.get("id")), "display_phone_number": n.get("display_phone_number")}
                                    for n in numbers])
        phone_number_id = str(numbers[0].get("id"))
    if not re.fullmatch(r"[0-9]{5,20}", phone_number_id or "") \
            or not any(str(n.get("id")) == phone_number_id for n in numbers):
        raise Rejected("PHONE_NOT_IN_WABA", PHONE_NOT_IN_WABA_MESSAGE, steps=steps)

    with core_cursor(commit=True) as (_, cur):
        row = repository.insert(cur, agency_id=agency_id, user_id=ctx.user_id, waba_id=waba_id,
                                phone_number_id=phone_number_id, business_id=business_id)

    callback = _env(CALLBACK_URL_ENV) or None
    shared_waba = len(numbers) > 1
    if callback and shared_waba:
        # L'indirizzo dei webhook si cambia per TUTTO il WABA: con altri numeri
        # (per esempio il numero API attuale) li sposterebbe insieme. Mai.
        steps["callback_override"] = {"ok": False, "error": "shared_waba_override_skipped"}
        callback = None
    ok, _, err = graph.subscribe_app(token, waba_id, callback_url=callback,
                                     verify_token=_env(VERIFY_TOKEN_ENV) or None)
    steps["subscribe_webhooks"] = {"ok": ok, "error": err, "callback_override": bool(callback)}
    now = datetime.now(timezone.utc)
    synced = {}
    # Rubrica e storico si chiedono SOLO se il numero e' gia' instradato a questa
    # agenzia: arrivati prima, resterebbero senza agenzia e invisibili per sempre.
    # Altrimenti il titolare li chiede dal pannello dopo aver configurato il numero.
    routed_now = _routed(agency_id, phone_number_id)
    for kind, sync_type in (("contacts", SYNC_CONTACTS), ("history", SYNC_HISTORY)):
        if not routed_now:
            steps[f"sync_{kind}"] = {"ok": False, "error": "not_routed_yet"}
            continue
        ok, _, err = graph.request_sync(token, phone_number_id, sync_type)
        steps[f"sync_{kind}"] = {"ok": ok, "error": err}
        if ok:
            synced[f"{kind}_sync_requested_at"] = now
    confirmed, details, err = _confirm(graph, token, waba_id, phone_number_id)
    steps["meta_check"] = {"ok": confirmed, "error": err}
    del token
    failed = not steps["subscribe_webhooks"]["ok"]
    status = STATUS_CONFIRMED if confirmed and not failed else (STATUS_FAILED if failed else STATUS_REQUESTED)
    with core_cursor(commit=True) as (_, cur):
        row = repository.update(
            cur, row["id"], agency_id, status=status, steps=steps, meta_details=details,
            error_code=None if status != STATUS_FAILED else steps["subscribe_webhooks"]["error"],
            meta_confirmed_at=now if status == STATUS_CONFIRMED else None,
            display_phone_number=details.get("display_phone_number"), verified_name=details.get("verified_name"),
            **synced)
        out = _view(cur, row)
    log.info("whatsapp_coexistence_onboarding agency=%s id=%s status=%s", agency_id, row["id"], status)
    _protect_inbox(ctx, agency_id, phone_number_id, details.get("display_phone_number"))
    return out


def _protect_inbox(ctx, agency_id: int, phone_number_id: str, display: str | None) -> None:
    """Il numero personale nasce RISERVATO e SENZA risposte automatiche, se la
    casella non ha ancora impostazioni e il numero e' gia' instradato a questa
    agenzia. Impostazioni gia' scelte dal titolare non si toccano."""
    from whatsapp_inboxes import repository as inbox_repository
    from whatsapp_inboxes import service as inboxes

    try:
        with core_cursor() as (_, cur):
            if not inbox_repository.installed(cur) or inbox_repository.by_numbers(cur, [phone_number_id]):
                return
        if not _routed(agency_id, phone_number_id):
            return
        inboxes.save_inbox(ctx, phone_number_id, label="Numero personale", kind="coexistence",
                           automations_enabled=False, access_mode="restricted", status=None,
                           display_phone_number=display if display and re.fullmatch(r"[0-9]{6,15}", display) else None)
    except inboxes.InboxError as exc:
        log.info("whatsapp_coexistence_inbox_defaults_skipped code=%s", exc.code)


def verify(ctx, onboarding_id: int) -> dict:
    """Richiede di nuovo a Meta lo stato del numero, con il token di sistema della casella."""
    _require_view(ctx)
    agency_id = ctx.require_agency()
    version = transport.graph_version()
    with core_cursor() as (_, cur):
        if not repository.installed(cur):
            raise NotInstalled("COEXISTENCE_NOT_INSTALLED", NOT_INSTALLED_MESSAGE)
        row = repository.get(cur, onboarding_id, agency_id)
    if row is None:
        raise NotFound("COEXISTENCE_NOT_FOUND", NOT_FOUND_MESSAGE)
    token = transport.token_for(row["phone_number_id"])
    if not token or version is None:
        raise NotConfigured("TOKEN_MISSING", TOKEN_MISSING_MESSAGE)
    confirmed, details, err = _confirm(make_graph(version), token, row["waba_id"], row["phone_number_id"])
    steps = dict(row.get("steps") or {})
    steps["meta_check"] = {"ok": confirmed, "error": err}
    now = datetime.now(timezone.utc)
    with core_cursor(commit=True) as (_, cur):
        current = repository.get(cur, onboarding_id, agency_id, lock=True)
        fields = {"steps": steps}
        if details:
            fields.update(meta_details=details, display_phone_number=details.get("display_phone_number"),
                          verified_name=details.get("verified_name"))
        if confirmed:
            fields.update(status=STATUS_CONFIRMED, error_code=None,
                          meta_confirmed_at=current.get("meta_confirmed_at") or now)
        elif current["status"] == STATUS_CONFIRMED:
            # Meta non conferma piu': lo stato torna «in attesa», con il motivo.
            fields.update(status=STATUS_REQUESTED, meta_confirmed_at=None, error_code=err)
        else:
            fields.update(error_code=err)
        row = repository.update(cur, onboarding_id, agency_id, **fields)
        return _view(cur, row)


def request_sync(ctx, onboarding_id: int, kind: str) -> dict:
    """Chiede di nuovo a Meta la rubrica o lo storico (Meta li concede solo per
    un periodo limitato dopo il collegamento: l'esito di Meta si registra)."""
    _require_owner(ctx)
    agency_id = ctx.require_agency()
    sync_type = SYNC_KINDS.get(kind)
    if sync_type is None:
        raise CoexistenceError("SYNC_KIND_INVALID", "Sincronizzazione non riconosciuta.")
    version = transport.graph_version()
    with core_cursor() as (_, cur):
        if not repository.installed(cur):
            raise NotInstalled("COEXISTENCE_NOT_INSTALLED", NOT_INSTALLED_MESSAGE)
        row = repository.get(cur, onboarding_id, agency_id)
    if row is None:
        raise NotFound("COEXISTENCE_NOT_FOUND", NOT_FOUND_MESSAGE)
    if not _routed(agency_id, row["phone_number_id"]):
        raise CoexistenceError("NOT_ROUTED", NOT_ROUTED_MESSAGE)
    token = transport.token_for(row["phone_number_id"])
    if not token or version is None:
        raise NotConfigured("TOKEN_MISSING", TOKEN_MISSING_MESSAGE)
    ok, _, err = make_graph(version).request_sync(token, row["phone_number_id"], sync_type)
    steps = dict(row.get("steps") or {})
    steps[f"sync_{kind}"] = {"ok": ok, "error": err, "again": True}
    fields = {"steps": steps}
    if ok:
        fields[f"{kind}_sync_requested_at"] = datetime.now(timezone.utc)
    with core_cursor(commit=True) as (_, cur):
        row = repository.update(cur, onboarding_id, agency_id, **fields)
        return _view(cur, row)
