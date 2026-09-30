"""A32-1 - l'email del promemoria: oggetto + corpo HTML italiano. PURA.

Riceve SOLO dati gia' letti e sanitizzati, e la sua firma e' la lista chiusa
di cio' che puo' comparire nel messaggio:

  customer_name     facoltativo: il nome con cui salutare
  appointment_type  uno dei tipi ammessi (policy.ALLOWED_TYPES)
  start_at          istante con fuso; mostrato in Europe/Rome
  agency_name       la firma: chi si assume la comunicazione
  property_address  facoltativo, SOLO per `buyer_visit`, SOLO strutturato
                    (via, civico, comune) - mai un testo libero

MAI nel messaggio (e non esiste un parametro per farcelo arrivare):
`location_text`, note, `outcome_note`, telefono, stato del lead, fonte,
id interni, token, l'email del cliente, il nome dell'agente.

SICUREZZA: ogni valore dinamico passa da `html.escape(..., quote=True)` prima
di entrare nell'HTML. L'oggetto non contiene valori forniti dall'utente: solo
etichette di questo modulo, data e ora (niente header injection possibile).

VERSIONE: `TEMPLATE_KEY`/`TEMPLATE_VERSION` finiscono sul ledger. Un testo
pubblicato non si corregge: si aggiunge la versione successiva.
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Mapping

from . import policy

TEMPLATE_KEY = "appointment_reminder_24h"
TEMPLATE_VERSION = 1

#: Etichette per il CLIENTE (non quelle interne dell'Agenda).
CUSTOMER_LABELS = {
    "buyer_visit": "visita all'immobile",
    "inspection": "sopralluogo",
    "seller_meeting": "appuntamento",
    "valuation_presentation": "presentazione della valutazione",
    "mandate_signing": "firma dell'incarico",
}

_GIORNI = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
_MESI = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
         "agosto", "settembre", "ottobre", "novembre", "dicembre")

#: Le sole chiavi di un indirizzo strutturato (colonne di `properties`).
ADDRESS_FIELDS = ("address", "civic_number", "city")


def _e(valore: str) -> str:
    return html.escape(valore, quote=True)


def _testo(valore: Any) -> str:
    if valore is None:
        return ""
    if not isinstance(valore, str):
        raise TypeError(f"expected text, got {type(valore).__name__}")
    return " ".join(valore.split())      # niente a capo o spazi multipli


def format_date(start_at: datetime) -> str:
    locale = policy._con_fuso(start_at, "start_at").astimezone(policy.ZONA)
    return f"{_GIORNI[locale.weekday()]} {locale.day} {_MESI[locale.month - 1]} {locale.year}"


def format_time(start_at: datetime) -> str:
    locale = policy._con_fuso(start_at, "start_at").astimezone(policy.ZONA)
    return f"{locale.hour:02d}:{locale.minute:02d}"


def format_address(property_address: Mapping[str, Any]) -> str:
    """"Via Roma 12, Giulianova". Solo le chiavi di `ADDRESS_FIELDS`."""
    if not isinstance(property_address, Mapping):
        raise TypeError("property_address must be a structured mapping "
                        "(address, civic_number, city), never free text")
    sconosciute = set(property_address) - set(ADDRESS_FIELDS)
    if sconosciute:
        raise ValueError(f"property_address has unknown fields {sorted(sconosciute)}")
    via = " ".join(p for p in (_testo(property_address.get("address")),
                               _testo(property_address.get("civic_number"))) if p)
    comune = _testo(property_address.get("city"))
    return ", ".join(p for p in (via, comune) if p)


def render_subject(*, appointment_type: str, start_at: datetime) -> str:
    etichetta = _etichetta(appointment_type)
    return f"Promemoria: {etichetta} {format_date(start_at)} alle {format_time(start_at)}"


def _etichetta(appointment_type: str) -> str:
    try:
        return CUSTOMER_LABELS[appointment_type]
    except (KeyError, TypeError):
        raise ValueError(f"appointment_type {appointment_type!r} has no reminder template") from None


def render_email(*, appointment_type: str, start_at: datetime, agency_name: str,
                 customer_name: str | None = None,
                 property_address: Mapping[str, Any] | None = None) -> tuple[str, str]:
    """`(subject, html_body)`."""
    etichetta = _etichetta(appointment_type)
    agenzia = _testo(agency_name)
    if not agenzia:
        raise ValueError("agency_name is required: the agency signs the reminder")
    nome = _testo(customer_name)
    indirizzo = ""
    if appointment_type == "buyer_visit" and property_address is not None:
        indirizzo = format_address(property_address)

    saluto = f"Buongiorno {_e(nome)}," if nome else "Buongiorno,"
    righe = [
        ("Appuntamento", etichetta),
        ("Data", format_date(start_at)),
        ("Ora", format_time(start_at)),
    ]
    if indirizzo:
        righe.append(("Indirizzo", indirizzo))
    righe.append(("Agenzia", agenzia))
    tabella = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#555;">{_e(k)}</td>'
        f'<td style="padding:4px 0;"><strong>{_e(v)}</strong></td></tr>'
        for k, v in righe)

    corpo = (
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#222;line-height:1.5;'
        'max-width:560px;">'
        '<h2 style="font-size:18px;margin:0 0 16px 0;">Promemoria appuntamento</h2>'
        f'<p style="margin:0 0 14px 0;">{saluto}</p>'
        f'<p style="margin:0 0 14px 0;">ti ricordiamo il tuo prossimo appuntamento.</p>'
        f'<table style="border-collapse:collapse;margin:0 0 18px 0;">{tabella}</table>'
        '<p style="margin:0 0 14px 0;">Se hai bisogno di spostarlo o annullarlo, '
        "contatta l'agenzia.</p>"
        f'<p style="margin:0;">{_e(agenzia)}</p>'
        '</div>'
    )
    return render_subject(appointment_type=appointment_type, start_at=start_at), corpo
