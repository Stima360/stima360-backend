"""Il SUPER ADMIN GENERALE designato: UNA implementazione dei controlli.

Estratto da `property/building_deletion.py` (ELIMINAZIONE-EDIFICIO-2, opzione
B) per CESTINO-GENERALE-1, perche' le stesse regole valgano per ogni
operazione riservata (eliminazioni degli edifici, Cestino di piattaforma,
cancellazione definitiva) senza una seconda copia.

Servono TUTTE, nel backend:
  1. l'ammissione alla superficie Platform (`require_platform_admin` sul mount
     e sulla rotta: `is_platform_admin` della sessione);
  2. l'identita' stabile dell'account (`operator_users.id` della sessione)
     uguale a quella designata nella configurazione del servizio
     (`STIMA360_SUPER_ADMIN_USER_ID`): `require_super_admin`;
  3. il ruolo ancora vero nel database al momento dell'operazione
     (`is_platform_admin = TRUE` e `status = 'active'`): `require_super_admin_db`.

Mai l'email: non concede nulla. Senza configurazione (o con un valore non
valido) tutto e' NEGATO (403 SUPER_ADMIN_NOT_CONFIGURED). Il rifiuto di un
amministratore di piattaforma e' registrato in `platform_audit_log` come
`denied` con l'azione del chiamante, best effort: il 403 resta comunque.
"""
from __future__ import annotations

import logging
import os
import re

from core.exceptions import PermissionDenied

SUPER_ADMIN_ENV = "STIMA360_SUPER_ADMIN_USER_ID"
SUPER_ADMIN_REQUIRED = "SUPER_ADMIN_REQUIRED"
SUPER_ADMIN_NOT_CONFIGURED = "SUPER_ADMIN_NOT_CONFIGURED"
SUPER_ADMIN_REQUIRED_MESSAGE = "Operazione riservata al Super Admin generale designato della piattaforma"
SUPER_ADMIN_NOT_CONFIGURED_MESSAGE = ("Operazione non disponibile: il Super Admin generale non è configurato su questo "
                                      "servizio. Nessuna modifica è stata fatta")
_ID_VALIDO = re.compile(r"[1-9][0-9]{0,17}")

logger = logging.getLogger(__name__)


class SuperAdminDenied(PermissionDenied):
    """403 con `code` (SUPER_ADMIN_REQUIRED / SUPER_ADMIN_NOT_CONFIGURED)."""

    def __init__(self, message, code, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


def designated_super_admin_id() -> int | None:
    """L'id dell'account designato, letto dalla configurazione del servizio
    a OGNI chiamata. Assente, vuoto o non valido (non un intero positivo): None,
    cioe' nessun Super Admin generale e tutte le operazioni riservate negate."""
    grezzo = (os.getenv(SUPER_ADMIN_ENV) or "").strip()
    return int(grezzo) if _ID_VALIDO.fullmatch(grezzo) else None


def deny(ctx, code: str, reason: str, *, audit_action: str, error_class=SuperAdminDenied):
    """403. Il rifiuto di un amministratore di piattaforma (ammesso alla
    superficie, ma non il Super Admin generale) e' un evento di sicurezza:
    riga `denied` in `platform_audit_log`, best effort."""
    if bool(getattr(ctx, "is_platform_admin", False)) and getattr(ctx, "user_id", None) is not None:
        try:
            from platform_admin import audit
            from platform_admin.enums import RESULT_DENIED
            audit.record(action=audit_action, actor=ctx, result=RESULT_DENIED, metadata={"reason": reason})
        except Exception:  # noqa: BLE001 - il rifiuto non dipende dal registro
            logger.error("platform_audit_log non scrivibile: rifiuto %s (%s) non registrato", code, reason)
    messaggio = SUPER_ADMIN_NOT_CONFIGURED_MESSAGE if code == SUPER_ADMIN_NOT_CONFIGURED else SUPER_ADMIN_REQUIRED_MESSAGE
    raise error_class(messaggio, code)


def require_super_admin(ctx, *, audit_action: str, error_class=SuperAdminDenied) -> None:
    """Prima di qualunque lettura: il flag `is_platform_admin` della sessione
    E l'identita' stabile dell'account uguale a quella designata."""
    utente = getattr(ctx, "user_id", None)
    if not bool(getattr(ctx, "is_platform_admin", False)) or utente is None:
        deny(ctx, SUPER_ADMIN_REQUIRED, "not_platform_admin", audit_action=audit_action, error_class=error_class)
    designato = designated_super_admin_id()
    if designato is None:
        logger.error("%s assente o non valida: operazioni del Super Admin generale negate", SUPER_ADMIN_ENV)
        deny(ctx, SUPER_ADMIN_NOT_CONFIGURED, "not_configured", audit_action=audit_action, error_class=error_class)
    try:
        stesso = int(utente) == designato
    except (TypeError, ValueError):
        stesso = False
    if not stesso:
        deny(ctx, SUPER_ADMIN_REQUIRED, "not_designated", audit_action=audit_action, error_class=error_class)


def require_super_admin_db(cur, ctx, *, audit_action: str, error_class=SuperAdminDenied,
                           for_update: bool = False) -> None:
    """Il ruolo EFFETTIVO, adesso, nel database: l'account designato e'
    ancora amministratore di piattaforma e attivo. `for_update` tiene la riga
    bloccata fino alla fine della transazione del chiamante (ADM-1)."""
    blocco = " FOR UPDATE" if for_update else ""
    cur.execute("SELECT is_platform_admin, status FROM operator_users WHERE id = %s" + blocco, (ctx.user_id,))
    riga = cur.fetchone()
    if riga is None or riga["is_platform_admin"] is not True or riga["status"] != "active":
        deny(ctx, SUPER_ADMIN_REQUIRED, "role_revoked", audit_action=audit_action, error_class=error_class)


# ---------------------------------------------------------------------------
# ADM-1 - la Rete e' del SOLO Super Admin generale designato.
# ---------------------------------------------------------------------------

def is_designated(user_id) -> bool:
    """L'account e' quello designato dalla configurazione? Solo l'identita':
    il ruolo nel database lo verifica `require_super_admin_db`."""
    designato = designated_super_admin_id()
    try:
        return designato is not None and int(user_id) == designato
    except (TypeError, ValueError):
        return False


def lock_super_admin(cur, ctx, *, audit_action: str, error_class=SuperAdminDenied) -> None:
    """Dentro la transazione di una modifica della Rete, PRIMA di ogni altra
    lettura: identita' designata e ruolo riletto, con la riga dell'account
    BLOCCATA fino al commit. Tutte le modifiche della Rete passano di qui per
    prime: si serializzano sulla stessa riga, sempre nello stesso ordine
    (attore -> agenzia -> operatore -> sessioni), e nessuna puo' committare se
    nel frattempo il ruolo e' stato tolto."""
    require_super_admin(ctx, audit_action=audit_action, error_class=error_class)
    require_super_admin_db(cur, ctx, audit_action=audit_action, error_class=error_class, for_update=True)
