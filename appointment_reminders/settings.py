"""ROADMAP PUNTO 2 - le impostazioni d'ambiente delle notifiche degli
appuntamenti. E' l'UNICO modulo del package che legge l'ambiente: la policy
resta pura e riceve i valori come argomenti.

TRE INTERRUTTORI INDIPENDENTI (nessuno ne implica un altro):

  1. promemoria 24h gia' esistenti   si accendono dal cron (agenzie consentite e
                                     token del cron); nessuna variabile qui li
                                     accende o li spegne. Restano quelli di prima.
  2. promemoria a 2 ore              `APPOINTMENT_REMINDER_OFFSETS` contiene `2h`
                                     (es. `24h,2h`); assente = solo `24h`.
  3. conferme al cliente             `APPOINTMENT_CONFIRMATIONS=1`. Governa SOLO
                                     la conferma (e il «nuovo orario») accodata
                                     dall'Agenda; non tocca ne' il 24h ne' il 2h.

DESTINATARI - `APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS`, elenco ESPLICITO di
email (separate da virgola, punto e virgola o spazio), mai dedotto dagli indirizzi
dei clienti:

  * conferme e 2h: FAIL-CLOSED. Variabile assente o vuota = nessun destinatario;
    `*` = tutti (apertura generale, da scegliere a mano).
  * 24h di prima: invariato. Assente = nessun limite; se presente, limita anche
    lui (e `*` = nessun limite).
"""
from __future__ import annotations

import os

from . import policy

CONFIRMATIONS_ENV = "APPOINTMENT_CONFIRMATIONS"


def offsets() -> tuple[str, ...]:
    return policy.enabled_offsets(os.getenv(policy.OFFSETS_ENV))


def allowed_recipients() -> frozenset[str] | None:
    """Per il promemoria 24h di prima: assente = nessun limite."""
    return policy.allowed_recipients(os.getenv(policy.RECIPIENTS_ENV))


def allowed_recipients_new() -> frozenset[str] | None:
    """Per le conferme e il 2h: assente = nessun destinatario (fail-closed)."""
    return policy.allowed_recipients_strict(os.getenv(policy.RECIPIENTS_ENV))


def allowed_recipients_for(offset: str | None) -> frozenset[str] | None:
    """`offset` e' `24h`/`2h` per un promemoria, `None` per la conferma."""
    return allowed_recipients() if offset == policy.OFFSET_24H else allowed_recipients_new()


def confirmations_enabled() -> bool:
    return (os.getenv(CONFIRMATIONS_ENV) or "").strip() == "1"
