"""A30-2 - la disponibilita': funzioni PURE su intervalli, niente database.

NESSUN ORARIO LAVORATIVO (D7 rev. 2). La finestra 08-20 della UI e' solo la
posizione iniziale della vista: qui gli slot si calcolano SOLO dentro la
finestra `[window_start, window_end)` che il chiamante passa. Le regole vere
(orari per agente, ferie, festivi) arrivano con A30-11.

Stessa regola del vincolo EXCLUDE della 072: intervalli semiaperti `[)`,
buffer compresi. `busy` e' l'elenco degli intervalli GIA' bloccati
(`blocked_range` delle righe che bloccano, buffer inclusi).

IL CAMBIO D'ORA. Gli slot si allineano al passo nell'ora LOCALE di Roma e
poi avanzano di minuti ASSOLUTI: il passo e' un divisore dell'ora, e i
cambi d'ora spostano l'offset di un'ora intera, quindi l'allineamento
locale (:00, :15, :30, :45) resta vero anche nei giorni da 23 e da 25 ore.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROMA = ZoneInfo("Europe/Rome")

ALLOWED_STEPS = (15, 30, 60)
MAX_WINDOW = timedelta(days=7)
ALTERNATIVES_HORIZON = timedelta(days=7)
ALTERNATIVES_COUNT = 3
MIN_DURATION = 5
MAX_DURATION = 24 * 60


def _allinea(istante: datetime, step: int) -> datetime:
    """Il primo istante >= `istante` che nell'ora di Roma cade sul passo."""
    locale = istante.astimezone(ROMA)
    base = locale.replace(second=0, microsecond=0)
    if base < locale:
        base += timedelta(minutes=1)
    resto = base.minute % step
    if resto:
        base += timedelta(minutes=step - resto)
    # In UTC da qui in avanti: in Python `aware + timedelta` somma l'ora
    # d'orologio, non il tempo assoluto, e attraverso un cambio d'ora
    # sbaglierebbe di un'ora.
    return base.astimezone(timezone.utc)


def overlaps(a_start, a_end, b_start, b_end) -> bool:
    """Intervalli semiaperti: [10,11) e [11,12) NON si toccano."""
    return a_start < b_end and b_start < a_end


def is_free(start: datetime, end: datetime, busy, *, buffer_before: int = 0,
            buffer_after: int = 0) -> bool:
    s = start - timedelta(minutes=buffer_before)
    e = end + timedelta(minutes=buffer_after)
    return not any(overlaps(s, e, b_start, b_end) for b_start, b_end in busy)


def slots(window_start: datetime, window_end: datetime, *, duration: int, step: int,
          busy, buffer_before: int = 0, buffer_after: int = 0) -> list[dict]:
    """Tutti gli slot della finestra, liberi o occupati."""
    if step not in ALLOWED_STEPS:
        raise ValueError(f"passo non ammesso: {step}")
    if not (MIN_DURATION <= duration <= MAX_DURATION):
        raise ValueError(f"durata non ammessa: {duration}")
    if window_end <= window_start or window_end - window_start > MAX_WINDOW:
        raise ValueError("finestra non valida (massimo 7 giorni)")
    durata = timedelta(minutes=duration)
    passo = timedelta(minutes=step)
    esito = []
    s = _allinea(window_start, step)
    while s + durata <= window_end:
        e = s + durata
        esito.append({"start_at": s, "end_at": e,
                      "available": is_free(s, e, busy, buffer_before=buffer_before,
                                           buffer_after=buffer_after)})
        s = s + passo
    return esito


def alternatives(requested_start: datetime, *, duration: int, busy, step: int = 15,
                 buffer_before: int = 0, buffer_after: int = 0,
                 count: int = ALTERNATIVES_COUNT) -> list[dict]:
    """I primi `count` orari liberi DOPO quello richiesto, entro 7 giorni.

    Senza orari lavorativi (D7): e' il calendario reale dell'agente a dire
    cosa e' libero, non una fascia inventata.
    """
    durata = timedelta(minutes=duration)
    fine = requested_start + ALTERNATIVES_HORIZON
    s = _allinea(requested_start + timedelta(minutes=step), step)
    esito = []
    while s < fine and len(esito) < count:
        e = s + durata
        if is_free(s, e, busy, buffer_before=buffer_before, buffer_after=buffer_after):
            esito.append({"start_at": s, "end_at": e})
        s = s + timedelta(minutes=step)
    return esito
