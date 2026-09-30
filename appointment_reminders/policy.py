"""A32-1 - la policy dei promemoria: funzioni PURE.

Nessun database, nessun orologio implicito (`now` e' sempre un argomento),
nessuno stato. Ogni istante in ingresso deve avere un fuso: un orario senza
fuso e' ambiguo e viene rifiutato, non indovinato.

REGOLE CONGELATE (gate A32-0 / A32-0B / A32-1)

  Tipi         buyer_visit, inspection, seller_meeting,
               valuation_presentation, mandate_signing
  Fonti        crm_manual, booking_link, lmc15_facade
               (escluse: legacy_stime_dettagliate, stima_inspections_backfill,
               system, a30_test - e qualunque altra: la lista e' chiusa)
  Stati        scheduled, confirmed
  Contatto     deve esistere, non `archived`, email valida e non vuota
  Fuso         Europe/Rome

  Target       la STESSA ORA CIVILE del GIORNO DI CALENDARIO precedente
               (non `start_at - 24h`: attraverso un cambio d'ora le due
               cose differiscono di un'ora).
  Finestra     08:00 <= invio < 20:00 locale, tutti i giorni (domenica
               inclusa). Target prima delle 08:00 -> 08:00 dello stesso
               giorno; target dalle 20:00 in poi -> 19:00 dello stesso giorno.
  Soglie       nessun promemoria se l'invio lascerebbe meno di 3h
               all'inizio; nessun promemoria se l'occorrenza e' stata creata
               meno di 12h prima dell'inizio. Sono due regole distinte.
  Tardivo      se il target e' gia' passato, si invia al primo istante
               ammesso dalla finestra a partire da `now`, solo se le due
               soglie reggono; se il primo istante ammesso lascia < 3h,
               nessun promemoria.

ORE CHE NON ESISTONO O CHE ESISTONO DUE VOLTE

Il target e' un'ora civile. Il giorno in cui passa l'ora legale (ultima
domenica di marzo, 02:00 -> 03:00) le 02:xx non esistono: `ZoneInfo` con
`fold=0` le legge con l'offset di prima del salto, cioe' il primo istante
reale dopo il salto; in ogni caso la finestra le porta alle 08:00. Il giorno
in cui torna l'ora solare (ultima domenica di ottobre) le 02:xx esistono due
volte: vale la prima (`fold=0`). Sono ore notturne: la finestra le sposta
comunque alle 08:00.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

TIMEZONE = "Europe/Rome"
ZONA = ZoneInfo(TIMEZONE)

#: L'unico offset dell'MVP. Vive nel `template_key` e nei metadata, mai nel
#: `reason_code` (che resta `appointment_reminder` per ogni offset).
OFFSET_24H = "24h"
OFFSETS = (OFFSET_24H,)

ALLOWED_TYPES = frozenset({
    "buyer_visit", "inspection", "seller_meeting", "valuation_presentation",
    "mandate_signing",
})
ALLOWED_SOURCES = frozenset({"crm_manual", "booking_link", "lmc15_facade"})
#: Documentazione esplicita di cio' che il gate ha escluso. La regola e' la
#: allowlist qui sopra: qualunque fonte non elencata li' e' esclusa.
EXCLUDED_SOURCES = frozenset({
    "legacy_stime_dettagliate", "stima_inspections_backfill", "system", "a30_test",
})
ALLOWED_STATUSES = frozenset({"scheduled", "confirmed"})
ARCHIVED_CONTACT_STATUS = "archived"

WINDOW_START = time(8, 0)
WINDOW_END = time(20, 0)          # esclusa
LATE_EVENING_SEND = time(19, 0)   # dove va un target dalle 20:00 in poi

MIN_LEAD = timedelta(hours=3)
MIN_BOOKING_NOTICE = timedelta(hours=12)

#: Il limite della colonna del destinatario nel ledger delle comunicazioni (064).
MAX_EMAIL_LENGTH = 320

#: Prefisso dell'identita' di occorrenza. `v1` versiona il FORMATO della
#: chiave, non il template.
OCCURRENCE_PREFIX = "appointment_reminder:v1"

# I motivi di non idoneita', stabili (finiranno nei conteggi del planner).
REASON_TYPE = "type_not_allowed"
REASON_SOURCE = "source_not_allowed"
REASON_STATUS = "status_not_allowed"
REASON_NO_CONTACT = "contact_missing"
REASON_CONTACT_ARCHIVED = "contact_archived"
REASON_NO_EMAIL = "email_missing_or_invalid"
REASON_BOOKING_TOO_LATE = "booked_less_than_12h_before"
REASON_LEAD_TOO_SHORT = "less_than_3h_left"

# Volutamente conservativa: una parte locale e un dominio con almeno un punto,
# niente spazi, niente caratteri che in un header o in HTML cambierebbero
# significato. Un'email che non passa non riceve un promemoria (nessuna
# correzione, nessuna inferenza).
_EMAIL = re.compile(r"^[^@\s<>\"'(),;:\[\]\\]{1,64}@[^@\s<>\"'(),;:\[\]\\]+\.[^@\s<>\"'(),;:\[\]\\.]{2,}$")


def _con_fuso(valore: Any, nome: str) -> datetime:
    if not isinstance(valore, datetime):
        raise TypeError(f"{nome} must be a datetime, got {type(valore).__name__}")
    if valore.tzinfo is None or valore.utcoffset() is None:
        raise ValueError(f"{nome} must be timezone-aware: a naive time is ambiguous")
    return valore


def is_valid_email(value: Any) -> bool:
    """Vero solo per un indirizzo plausibile, gia' senza spazi ai bordi."""
    if not isinstance(value, str):
        return False
    pulito = value.strip()
    if not pulito or pulito != value or len(pulito) > MAX_EMAIL_LENGTH:
        return False
    return _EMAIL.match(pulito) is not None


# ---------------------------------------------------------------------------
# IDONEITA' STATICA: tipo, fonte, stato, contatto, email
# ---------------------------------------------------------------------------

def ineligibility_reason(appointment: Mapping[str, Any],
                         contact: Mapping[str, Any] | None) -> str | None:
    """None se l'appuntamento e il suo contatto possono ricevere un
    promemoria; altrimenti il motivo (uno solo, il primo che fallisce)."""
    if appointment.get("appointment_type") not in ALLOWED_TYPES:
        return REASON_TYPE
    if appointment.get("source") not in ALLOWED_SOURCES:
        return REASON_SOURCE
    if appointment.get("status") not in ALLOWED_STATUSES:
        return REASON_STATUS
    if contact is None:
        return REASON_NO_CONTACT
    if contact.get("status") == ARCHIVED_CONTACT_STATUS:
        return REASON_CONTACT_ARCHIVED
    if not is_valid_email(contact.get("email")):
        return REASON_NO_EMAIL
    return None


# ---------------------------------------------------------------------------
# TEMPO: target nominale, finestra, target effettivo, decisione
# ---------------------------------------------------------------------------

def nominal_reminder_at(start_at: datetime) -> datetime:
    """La stessa ora civile del giorno di calendario precedente (UTC)."""
    locale = _con_fuso(start_at, "start_at").astimezone(ZONA)
    giorno_prima = locale.date() - timedelta(days=1)
    orario = locale.time().replace(tzinfo=None, fold=0)
    return datetime.combine(giorno_prima, orario, tzinfo=ZONA).astimezone(timezone.utc)


def in_window(instant: datetime) -> bool:
    ora = _con_fuso(instant, "instant").astimezone(ZONA).time()
    return WINDOW_START <= ora < WINDOW_END


def _in_finestra_stesso_giorno(instant: datetime) -> datetime:
    locale = instant.astimezone(ZONA)
    ora = locale.time()
    if ora < WINDOW_START:
        return datetime.combine(locale.date(), WINDOW_START, tzinfo=ZONA).astimezone(timezone.utc)
    if ora >= WINDOW_END:
        return datetime.combine(locale.date(), LATE_EVENING_SEND,
                                tzinfo=ZONA).astimezone(timezone.utc)
    return instant.astimezone(timezone.utc)


def effective_reminder_at(start_at: datetime) -> datetime:
    """Il target nominale portato dentro la finestra, nello STESSO giorno."""
    return _in_finestra_stesso_giorno(nominal_reminder_at(start_at))


def next_window_instant(instant: datetime) -> datetime:
    """Il primo istante >= `instant` ammesso dalla finestra (UTC)."""
    locale = _con_fuso(instant, "instant").astimezone(ZONA)
    ora = locale.time()
    if ora < WINDOW_START:
        return datetime.combine(locale.date(), WINDOW_START, tzinfo=ZONA).astimezone(timezone.utc)
    if ora >= WINDOW_END:
        domani = locale.date() + timedelta(days=1)
        return datetime.combine(domani, WINDOW_START, tzinfo=ZONA).astimezone(timezone.utc)
    return instant.astimezone(timezone.utc)


@dataclass(frozen=True)
class SendDecision:
    """`send_at` e' il primo istante in cui il promemoria PUO' partire;
    `due` dice se, a `now`, e' gia' ora di mandarlo. Se `reason` non e' None
    il promemoria non partira' mai per questa occorrenza."""
    send_at: datetime | None
    due: bool
    reason: str | None


def send_decision(*, now: datetime, start_at: datetime, created_at: datetime) -> SendDecision:
    """Soglie 12h/3h, finestra, promemoria tardivo. Solo tempo: l'idoneita'
    statica e' `ineligibility_reason`."""
    now = _con_fuso(now, "now")
    start_at = _con_fuso(start_at, "start_at")
    created_at = _con_fuso(created_at, "created_at")
    if start_at - created_at < MIN_BOOKING_NOTICE:
        return SendDecision(None, False, REASON_BOOKING_TOO_LATE)
    candidato = effective_reminder_at(start_at)
    if candidato < now:
        # Target passato (prenotazione tardiva, o un giro saltato): il primo
        # istante ammesso da adesso in poi.
        candidato = next_window_instant(now)
    if start_at - candidato < MIN_LEAD:
        return SendDecision(None, False, REASON_LEAD_TOO_SHORT)
    return SendDecision(candidato, candidato <= now, None)


def is_reminder_eligible(*, now: datetime, appointment: Mapping[str, Any],
                         contact: Mapping[str, Any] | None) -> SendDecision:
    """La decisione completa: idoneita' statica, poi tempo. `appointment`
    porta `appointment_type`, `source`, `status`, `start_at`, `created_at`;
    `contact` porta `status` ed `email` (o e' None)."""
    motivo = ineligibility_reason(appointment, contact)
    if motivo is not None:
        return SendDecision(None, False, motivo)
    return send_decision(now=now, start_at=appointment["start_at"],
                         created_at=appointment["created_at"])


# ---------------------------------------------------------------------------
# IDENTITA' DELL'OCCORRENZA
# ---------------------------------------------------------------------------

def occurrence_key(appointment_id: int, start_at: datetime,
                   offset: str = OFFSET_24H) -> str:
    """`appointment_reminder:v1:<appointment_id>:<offset>:<start_epoch>`.

    Deterministica e compatta. Identifica l'OCCORRENZA (quale appuntamento,
    quale offset, quale orario), non il destinatario: l'email NON ne fa parte,
    cosi' che un promemoria gia' inviato non si ripeta se l'indirizzo cambia.
    Un reschedule A30 crea un appuntamento nuovo (id nuovo) e quindi
    un'occorrenza nuova. Le revisioni (r1..r5) sono del planner, non di qui.
    `start_epoch` sono i secondi UNIX interi dell'istante di inizio.
    """
    if isinstance(appointment_id, bool) or not isinstance(appointment_id, int) \
            or appointment_id < 1:
        raise ValueError("appointment_id must be a positive integer")
    if offset not in OFFSETS:
        raise ValueError(f"unknown reminder offset {offset!r}")
    start_at = _con_fuso(start_at, "start_at")
    epoca = int(start_at.timestamp())
    return f"{OCCURRENCE_PREFIX}:{appointment_id}:{offset}:{epoca}"
