"""ROADMAP PUNTO 2 - le impostazioni d'ambiente delle notifiche degli
appuntamenti. E' l'UNICO modulo del package che legge l'ambiente: la policy
resta pura e riceve i valori come argomenti.

  APPOINTMENT_REMINDER_OFFSETS                 es. `24h,2h`; assente = solo `24h`
  APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS  indirizzi email ammessi durante il
                                               collaudo; assente = nessun limite;
                                               presente ma vuota = nessuno
  APPOINTMENT_CONFIRMATIONS                    `1` accende le conferme al cliente
"""
from __future__ import annotations

import os

from . import policy

CONFIRMATIONS_ENV = "APPOINTMENT_CONFIRMATIONS"


def offsets() -> tuple[str, ...]:
    return policy.enabled_offsets(os.getenv(policy.OFFSETS_ENV))


def allowed_recipients() -> frozenset[str] | None:
    return policy.allowed_recipients(os.getenv(policy.RECIPIENTS_ENV))


def confirmations_enabled() -> bool:
    return (os.getenv(CONFIRMATIONS_ENV) or "").strip() == "1"
