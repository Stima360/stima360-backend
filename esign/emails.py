"""FIRMA OTP - le due email del procedimento, sul sistema SMTP del CRM (registro comunicazioni + dispatcher).

    collegamento   «STIMA360 — Foglio visita da firmare»: chi, che cosa, il pulsante «Visualizza e firma il documento»,
                   l'avviso che servira' un codice OTP, la scadenza. Nessun dato dell'immobile oltre al riferimento.
    codice         «STIMA360 — Il tuo codice di conferma»: il codice a 6 cifre, la durata, «se non sei stato tu ignora».

Entrambe sono email di SERVIZIO (`communication_type = service`): accodate nella transazione del chiamante con la
chiave di idempotenza della sfida, poi SPEDITE SUBITO con un giro del dispatcher per l'agenzia (`dispatch_now`),
perche' un codice che scade in dieci minuti non puo' aspettare il cron. Il dispatcher e' lo stesso di sempre (claim,
divieto di contatto, trasporto, registro): qui non si apre nessun SMTP. L'esito tecnico si legge dal dominio
comunicazioni; «accettata dal server di posta» non e' mai scritta come «consegnata».
"""
from __future__ import annotations

import html
import logging
import os
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from communication import service as communication_service
from communication.enums import (CHANNEL_EMAIL, MODE_AUTOMATIC, REASON_SIGNATURE_LINK, REASON_SIGNATURE_OTP,
                                 TYPE_SERVICE)
from communication.marchio import LOGO_ALT, LOGO_PERCORSO
from operator_auth.context import SystemAgencyContext

logger = logging.getLogger(__name__)
ROMA = ZoneInfo("Europe/Rome")
ORIGIN = "public_esign"
PAGE_PREFIX = "/firma"
TEMPLATE_LINK = ("signature_link", 1)
TEMPLATE_OTP = ("signature_otp", 1)
SUBJECT_LINK = "STIMA360 — Foglio visita da firmare"
SUBJECT_OTP = "STIMA360 — Il tuo codice di conferma"
IMMEDIATE_ENV = "ESIGN_DISPATCH_IMMEDIATE"


def public_base_url() -> str:
    return (os.getenv("PUBLIC_BASE_URL") or "https://stima360-backend.onrender.com").rstrip("/")


def sign_url(token: str) -> str:
    return f"{public_base_url()}{PAGE_PREFIX}/{quote(token, safe='')}"


def _logo() -> str:
    return html.escape(public_base_url() + LOGO_PERCORSO, quote=True)


def _frame(title: str, inner: str) -> str:
    return (f'<div style="font-family:Arial,Helvetica,sans-serif;color:#19211c;line-height:1.6;max-width:640px;'
            f'margin:0 auto;padding:8px;">'
            f'<p style="margin:0 0 16px 0;"><img src="{_logo()}" alt="{html.escape(LOGO_ALT)}" width="160" '
            f'style="display:block;max-width:160px;height:auto;"></p>'
            f'<h2 style="margin:0 0 14px 0;color:#173c2b;font-size:20px;">{html.escape(title)}</h2>{inner}'
            f'<p style="margin:22px 0 0 0;font-size:12px;color:#59645e;">STIMA360 — Immobiliare di nuova generazione. '
            f'Questo messaggio riguarda un procedimento di firma elettronica semplice avviato dall\'agenzia indicata.</p></div>')


def _quando(dt: datetime) -> str:
    return dt.astimezone(ROMA).strftime("%d/%m/%Y alle %H:%M")


def link_email(*, signer_name: str, agency_name: str, title: str, url: str, expires_at: datetime) -> tuple[str, str]:
    sicuro = html.escape(url, quote=True)
    inner = (
        f'<p style="margin:0 0 14px 0;">Gentile {html.escape(signer_name)},</p>'
        f'<p style="margin:0 0 14px 0;">l\'agenzia <b>{html.escape(agency_name)}</b> ti chiede di sottoscrivere il documento '
        f'<b>{html.escape(title)}</b> relativo alla visita dell\'immobile.</p>'
        f'<p style="margin:0 0 18px 0;"><a href="{sicuro}" style="display:inline-block;padding:14px 24px;background:#173c2b;'
        f'color:#ffffff;text-decoration:none;border-radius:8px;font-weight:bold;">Visualizza e firma il documento</a></p>'
        f'<p style="margin:0 0 14px 0;font-size:14px;">Potrai leggere e scaricare il documento. Per confermare la firma ti '
        f'chiederemo un <b>codice OTP</b> che riceverai a questo stesso indirizzo email.</p>'
        f'<p style="margin:0 0 14px 0;font-size:13px;color:#59645e;">Il collegamento è personale, non va inoltrato e scade il '
        f'{html.escape(_quando(expires_at))}. Se il pulsante non funziona copia questo indirizzo nel browser:<br>'
        f'<span style="word-break:break-all;">{sicuro}</span></p>'
        f'<p style="margin:0;font-size:13px;color:#59645e;">Se non riconosci questa richiesta ignora il messaggio: senza il codice '
        f'nessuna firma viene apposta.</p>')
    return SUBJECT_LINK, _frame("Foglio visita da firmare", inner)


def otp_email(*, code: str, minutes: int, title: str) -> tuple[str, str]:
    inner = (
        f'<p style="margin:0 0 14px 0;">Il tuo codice per confermare la firma del documento <b>{html.escape(title)}</b>:</p>'
        f'<p style="margin:0 0 18px 0;font-size:32px;letter-spacing:8px;font-weight:bold;color:#173c2b;">{html.escape(code)}</p>'
        f'<p style="margin:0 0 14px 0;font-size:14px;">Il codice vale <b>{int(minutes)} minuti</b> e per una sola conferma. '
        f'Inseriscilo nella pagina di firma che hai aperto.</p>'
        f'<p style="margin:0;font-size:13px;color:#59645e;">Se non hai richiesto tu questo codice ignora il messaggio e non '
        f'comunicarlo a nessuno: STIMA360 e l\'agenzia non te lo chiederanno mai per telefono.</p>')
    return SUBJECT_OTP, _frame("Il tuo codice di conferma", inner)


def enqueue(cur, *, agency_id: int, contact_id: int | None, property_id: int | None, destination: str, subject: str,
            body_html: str, reason_code: str, idempotency_key: str, metadata: dict) -> int:
    """Nella transazione del chiamante: la riga del registro comunicazioni. Restituisce l'id del messaggio."""
    template = TEMPLATE_LINK if reason_code == REASON_SIGNATURE_LINK else TEMPLATE_OTP
    esito = communication_service.enqueue(
        SystemAgencyContext(agency_id=agency_id, origin=ORIGIN), contact_id=contact_id, channel=CHANNEL_EMAIL,
        communication_type=TYPE_SERVICE, mode=MODE_AUTOMATIC, reason_code=reason_code, rendered_body=body_html,
        destination_snapshot=destination, idempotency_key=idempotency_key, subject_snapshot=subject,
        template_key=template[0], template_version=template[1], property_id=property_id, metadata=metadata, cur=cur)
    return int(esito["message"]["id"])


def _provider():
    from communication import dispatcher
    return dispatcher.adapter_per(CHANNEL_EMAIL)


def dispatch_now(agency_id: int) -> dict | None:
    """Un giro del dispatcher per l'agenzia, subito dopo il commit. Spento con `ESIGN_DISPATCH_IMMEDIATE=0`
    (allora spedisce il cron). Un errore qui non annulla niente: il messaggio resta in coda per il cron."""
    if (os.getenv(IMMEDIATE_ENV) or "1").strip() in ("0", "false", "no"):
        return None
    from communication import dispatcher
    ctx = SystemAgencyContext(agency_id=agency_id, origin=ORIGIN)
    provider = _provider()
    try:
        return dispatcher.dispatch_batch(ctx, channel=CHANNEL_EMAIL, provider=provider, limit=10)
    except Exception:  # noqa: BLE001 - il messaggio e' in coda: lo spedira' il cron
        logger.exception("esign_dispatch_now_failed agency_id=%s", agency_id)
        return None


def message_status(cur, agency_id: int, message_id: int | None) -> dict | None:
    """L'esito TECNICO dell'email, letto dal dominio comunicazioni (stesso cursore): stato del messaggio e ultimo codice
    d'errore, mai il corpo. Nessuna query diretta sul registro: il dominio e' l'unica via."""
    if message_id is None:
        return None
    ctx = SystemAgencyContext(agency_id=agency_id, origin=ORIGIN)
    try:
        message = communication_service.get_message(ctx, message_id, cur=cur)
    except Exception:  # noqa: BLE001 - un messaggio fuori scope o sparito: nessuno stato
        return None
    attempts = communication_service.list_attempts(ctx, message_id, cur=cur)
    last = attempts[-1] if attempts else None
    updated = message.get("updated_at")
    return {"message_id": message_id, "status": message["status"], "error_code": (last or {}).get("error_code"),
            "updated_at": updated.isoformat() if hasattr(updated, "isoformat") else updated}
