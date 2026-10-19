"""ADM-5B - il Super Admin generale designato, letto in un posto solo.

`platform_admin` dipende da `operator_auth`, mai il contrario. La risoluzione
della sessione (`operator_auth.service`) deve pero' sapere chi e' il designato
per tenere aperta la consultazione di un'agenzia non attiva: la lettura della
configurazione sta qui, e `platform_admin.super_admin` la riusa invece di
ripeterla.
"""
from __future__ import annotations

import os
import re

SUPER_ADMIN_ENV = "STIMA360_SUPER_ADMIN_USER_ID"
_ID_VALIDO = re.compile(r"[1-9][0-9]{0,17}")


def designated_super_admin_id() -> int | None:
    """L'id dell'account designato, letto dalla configurazione del servizio
    a OGNI chiamata. Assente, vuoto o non valido (non un intero positivo): None,
    cioe' nessun Super Admin generale e tutte le operazioni riservate negate."""
    grezzo = (os.getenv(SUPER_ADMIN_ENV) or "").strip()
    return int(grezzo) if _ID_VALIDO.fullmatch(grezzo) else None


def is_designated(user_id) -> bool:
    """L'account e' quello designato dalla configurazione? Solo l'identita':
    il ruolo nel database lo verifica `platform_admin.super_admin`."""
    designato = designated_super_admin_id()
    try:
        return designato is not None and int(user_id) == designato
    except (TypeError, ValueError):
        return False
