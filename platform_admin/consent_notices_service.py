"""Fase B - le informative marketing, riservate al Super Admin.

`consent_notices` (061) esisteva senza nessuno che la scrivesse. Qui nasce
una versione, con testo, finalita', contesto e validita'; qui si chiude la
sua validita'. Nessuna informativa fittizia: il testo lo scrive una persona,
l'hash lo calcola il server, l'autore e' l'attore della sessione, e ogni atto
passa dal registro di audit della Rete (`audit_then_commit`).

Il testo di una versione pubblicata NON si modifica: chi deve cambiarlo
chiude questa versione e ne crea un'altra. E' cio' che permette a un evento
di consenso di citare `notice_id` e restare vero per sempre.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from operator_auth.context import OperatorContext

from . import super_admin
from .audit import actor_label
from .database import platform_audit_cursor, platform_operation_cursor
from .enums import (
    ACTION_CONSENT_NOTICE_CLOSE, ACTION_CONSENT_NOTICE_CREATE, CONSENT_NOTICE_PURPOSES,
    TARGET_TYPE_CONSENT_NOTICE,
)
from .exceptions import ConsentNoticeConflict, ConsentNoticeNotFound
from .transaction import audit_then_commit

COLONNE = ("id", "purpose", "version", "context", "content", "content_hash", "content_available",
           "valid_from", "valid_to", "created_at", "created_by")
# 109: i canali che il testo copre. Una riga letta senza la colonna (schema
# senza 109) copre '{}' e non autorizza nessun canale.
CANALI_INFORMATIVA = ("email", "sms", "whatsapp", "phone")


def _riga(row) -> dict[str, Any]:
    riga = {k: row[k] for k in COLONNE}
    chiavi = row.keys() if hasattr(row, "keys") else ()
    riga["channels"] = list(row["channels"] or []) if "channels" in chiavi else []
    return riga


def list_notices(*, purpose: str | None = None) -> list[dict[str, Any]]:
    with platform_audit_cursor() as (_, cur):
        if purpose is None:
            cur.execute("SELECT * FROM consent_notices ORDER BY purpose, valid_from DESC, id DESC")
        else:
            cur.execute("SELECT * FROM consent_notices WHERE purpose = %s ORDER BY valid_from DESC, id DESC",
                        [purpose])
        return [_riga(r) for r in cur.fetchall()]


def get_notice(notice_id: int) -> dict[str, Any]:
    with platform_audit_cursor() as (_, cur):
        cur.execute("SELECT * FROM consent_notices WHERE id = %s", [notice_id])
        row = cur.fetchone()
    if row is None:
        raise ConsentNoticeNotFound(f"informativa {notice_id} non trovata")
    return _riga(row)


def create_notice(actor: OperatorContext, *, purpose: str, version: str, context: str,
                  content: str, valid_from: datetime, valid_to: datetime | None,
                  channels=None) -> dict[str, Any]:
    """Una versione nuova. Solleva `ConsentNoticeConflict` se (finalita',
    versione) esiste gia'.

    109: `channels` e' l'elenco dei canali che il TESTO descrive. Per il
    marketing ne serve almeno uno, dichiarato dal Super Admin e mai dedotto;
    un consenso su un canale potra' citare solo una versione che lo copre.
    `privacy_terms` non ha canali."""
    if purpose not in CONSENT_NOTICE_PURPOSES:
        raise ValueError(f"purpose fuori da {CONSENT_NOTICE_PURPOSES}")
    canali = [c for c in CANALI_INFORMATIVA if c in set(channels or ())]
    fuori = [c for c in (channels or ()) if c not in CANALI_INFORMATIVA]
    if fuori:
        raise ValueError(f"canali sconosciuti {fuori}: ammessi {list(CANALI_INFORMATIVA)}")
    if purpose == "marketing" and not canali:
        raise ValueError("una informativa marketing deve dichiarare almeno un canale che il testo copre")
    if purpose != "marketing" and canali:
        raise ValueError("solo una informativa marketing dichiara canali")
    if valid_to is not None and valid_to < valid_from:
        raise ValueError("valid_to precede valid_from")
    impronta = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with platform_operation_cursor() as (conn, cur):
        super_admin.lock_super_admin(cur, actor, audit_action=ACTION_CONSENT_NOTICE_CREATE)
        cur.execute("SELECT 1 FROM consent_notices WHERE purpose = %s AND version = %s",
                    [purpose, version])
        if cur.fetchone() is not None:
            raise ConsentNoticeConflict(
                f"esiste gia' una informativa {purpose!r} con versione {version!r}")
        if valid_to is None:
            # 061: UNA sola versione corrente (senza `valid_to`) per finalita'.
            # La precedente si chiude prima, esplicitamente: non qui, di nascosto.
            cur.execute("SELECT version FROM consent_notices WHERE purpose = %s AND valid_to IS NULL", [purpose])
            corrente = cur.fetchone()
            if corrente is not None:
                raise ConsentNoticeConflict(
                    f"la versione {corrente['version']!r} e' ancora corrente per {purpose!r}: "
                    "chiuderla prima di pubblicarne un'altra")
        cur.execute(
            """INSERT INTO consent_notices
                   (purpose, version, content, content_hash, content_available, context,
                    valid_from, valid_to, created_by, channels)
               VALUES (%s, %s, %s, %s, TRUE, %s, %s, %s, %s, %s) RETURNING *""",
            [purpose, version, content, impronta, context, valid_from, valid_to, actor_label(actor), canali])
        row = _riga(cur.fetchone())
        audit_then_commit(
            conn, actor, action=ACTION_CONSENT_NOTICE_CREATE,
            target_type=TARGET_TYPE_CONSENT_NOTICE, target_id=row["id"], target_agency_id=None,
            metadata={"purpose": purpose, "version": version, "content_hash": impronta,
                      "channels": canali, "valid_from": valid_from.isoformat(),
                      "valid_to": valid_to.isoformat() if valid_to else None})
        return row


def close_notice(actor: OperatorContext, notice_id: int, *, valid_to: datetime | None = None) -> dict[str, Any]:
    """Chiude la validita' di una versione (da adesso, o dalla data data).
    Il testo non cambia. Solleva se non esiste o e' gia' chiusa."""
    quando = valid_to or datetime.now(timezone.utc)
    with platform_operation_cursor() as (conn, cur):
        super_admin.lock_super_admin(cur, actor, audit_action=ACTION_CONSENT_NOTICE_CLOSE)
        cur.execute("SELECT * FROM consent_notices WHERE id = %s FOR UPDATE", [notice_id])
        row = cur.fetchone()
        if row is None:
            raise ConsentNoticeNotFound(f"informativa {notice_id} non trovata")
        if row["valid_to"] is not None:
            raise ConsentNoticeConflict(f"informativa {notice_id} gia' chiusa")
        if quando < row["valid_from"]:
            raise ValueError("valid_to precede valid_from")
        cur.execute("UPDATE consent_notices SET valid_to = %s WHERE id = %s RETURNING *",
                    [quando, notice_id])
        aggiornata = _riga(cur.fetchone())
        audit_then_commit(
            conn, actor, action=ACTION_CONSENT_NOTICE_CLOSE,
            target_type=TARGET_TYPE_CONSENT_NOTICE, target_id=notice_id, target_agency_id=None,
            metadata={"valid_to": quando.isoformat()})
        return aggiornata
