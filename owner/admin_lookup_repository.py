"""Read-only OWNER Admin lookup queries for P8.1.

This module intentionally exposes small projections from CORE/PROPERTY data.
It performs no mutation and does not duplicate source records in OWNER.
"""
from __future__ import annotations

from core.database import core_cursor
from core.exceptions import NotFoundError


OWNER_ELIGIBLE_PROPERTY_CONTACT_ROLES = frozenset({"owner"})
_NOT_FOUND = "Risorsa non trovata"


def lookup_contacts(search: str | None = None, limit: int = 50) -> list[dict]:
    """Return the minimum CORE contact projection required by OWNER Admin."""
    needle = (search or "").strip()
    with core_cursor() as (_, cur):
        if needle:
            pattern = f"%{needle}%"
            cur.execute(
                """SELECT id,display_name,email
                   FROM contacts
                   WHERE display_name ILIKE %s OR email ILIKE %s
                   ORDER BY display_name NULLS LAST,id
                   LIMIT %s""",
                (pattern, pattern, limit),
            )
        else:
            cur.execute(
                """SELECT id,display_name,email
                   FROM contacts
                   ORDER BY display_name NULLS LAST,id
                   LIMIT %s""",
                (limit,),
            )
        return [dict(item) for item in cur.fetchall()]


def lookup_account_properties(owner_account_id: int) -> list[dict]:
    """Return only properties for which the account contact has role='owner'."""
    with core_cursor() as (_, cur):
        cur.execute(
            "SELECT contact_id FROM owner_accounts WHERE id=%s",
            (owner_account_id,),
        )
        account = cur.fetchone()
        if not account:
            raise NotFoundError(_NOT_FOUND)

        cur.execute(
            """SELECT DISTINCT p.id,p.code,p.title,p.address,p.city
               FROM property_contacts pc
               JOIN properties p ON p.id=pc.property_id
               WHERE pc.contact_id=%s AND pc.role=%s
               ORDER BY p.title NULLS LAST,p.id""",
            (account["contact_id"], "owner"),
        )
        return [dict(item) for item in cur.fetchall()]


def _ensure_owner_eligible_property(cur, owner_account_id: int, property_id: int) -> None:
    cur.execute(
        """SELECT 1
           FROM owner_accounts oa
           JOIN property_contacts pc ON pc.contact_id=oa.contact_id
           WHERE oa.id=%s AND pc.property_id=%s AND pc.role=%s
           LIMIT 1""",
        (owner_account_id, property_id, "owner"),
    )
    if not cur.fetchone():
        raise NotFoundError(_NOT_FOUND)


def lookup_property_documents(owner_account_id: int, property_id: int) -> list[dict]:
    """Return a storage-safe projection only for an owner-eligible property."""
    with core_cursor() as (_, cur):
        _ensure_owner_eligible_property(cur, owner_account_id, property_id)
        cur.execute(
            """SELECT id,title,document_type,status,expires_at
               FROM property_documents
               WHERE property_id=%s
               ORDER BY created_at DESC,id DESC""",
            (property_id,),
        )
        return [dict(item) for item in cur.fetchall()]


def lookup_property_visits(owner_account_id: int, property_id: int) -> list[dict]:
    """Return a visitor-safe projection only for an owner-eligible property."""
    with core_cursor() as (_, cur):
        _ensure_owner_eligible_property(cur, owner_account_id, property_id)
        cur.execute(
            """SELECT id,scheduled_at,status
               FROM property_visits
               WHERE property_id=%s
               ORDER BY scheduled_at DESC,id DESC""",
            (property_id,),
        )
        return [dict(item) for item in cur.fetchall()]
