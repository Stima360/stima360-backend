"""Read-only OWNER Admin lookup queries for P8.1.

This module intentionally exposes small projections from CORE/PROPERTY data.
It performs no mutation and does not duplicate source records in OWNER.

P26-6C: every query here is bound to one agency. `agency_id` is the first
parameter of every public function and has no default, so a caller that forgets
the tenant raises a TypeError rather than reading the platform. The value comes
from `legacy_basic_agency_context`, resolved server-side; nothing in a request
body, query string, path or header reaches it.

Both roots are checked wherever both exist. An owner account reaches an agency
through its contact and a property carries its own, so `account of A` +
`property of B` must resolve to nothing - and it must do so in the eligibility
guard, before any source row is read.
"""
from __future__ import annotations

from core.database import core_cursor
from core.exceptions import NotFoundError


OWNER_ELIGIBLE_PROPERTY_CONTACT_ROLES = frozenset({"owner"})
_NOT_FOUND = "Risorsa non trovata"


def lookup_contacts(agency_id: int, search: str | None = None, limit: int = 50) -> list[dict]:
    """Return the minimum CORE contact projection required by OWNER Admin."""
    needle = (search or "").strip()
    with core_cursor() as (_, cur):
        if needle:
            pattern = f"%{needle}%"
            # The parentheses are load-bearing. Without them the clause reads
            # `(agency_id=%s AND display_name ILIKE %s) OR email ILIKE %s`, and
            # a contact of another agency whose email matched would come back -
            # with the caller's own agency still the only parameter, so nothing
            # about the values would look wrong.
            cur.execute(
                """SELECT id,display_name,email
                   FROM contacts
                   WHERE agency_id=%s AND (display_name ILIKE %s OR email ILIKE %s)
                   ORDER BY display_name NULLS LAST,id
                   LIMIT %s""",
                (agency_id, pattern, pattern, limit),
            )
        else:
            cur.execute(
                """SELECT id,display_name,email
                   FROM contacts
                   WHERE agency_id=%s
                   ORDER BY display_name NULLS LAST,id
                   LIMIT %s""",
                (agency_id, limit),
            )
        return [dict(item) for item in cur.fetchall()]


def lookup_account_properties(agency_id: int, owner_account_id: int) -> list[dict]:
    """Return only properties for which the account contact has role='owner'."""
    with core_cursor() as (_, cur):
        cur.execute(
            """SELECT oa.contact_id
               FROM owner_accounts oa
               JOIN contacts ct ON ct.id=oa.contact_id
               WHERE oa.id=%s AND ct.agency_id=%s""",
            (owner_account_id, agency_id),
        )
        account = cur.fetchone()
        if not account:
            raise NotFoundError(_NOT_FOUND)

        cur.execute(
            """SELECT DISTINCT p.id,p.code,p.title,p.address,p.city
               FROM property_contacts pc
               JOIN properties p ON p.id=pc.property_id
               WHERE pc.contact_id=%s AND pc.role=%s AND p.agency_id=%s
               ORDER BY p.title NULLS LAST,p.id""",
            (account["contact_id"], "owner", agency_id),
        )
        return [dict(item) for item in cur.fetchall()]


def _ensure_owner_eligible_property(
    cur, agency_id: int, owner_account_id: int, property_id: int
) -> None:
    """Both roots must reach the caller's agency, and the role must be owner.

    One statement, so there is no window in which half the condition holds. It
    runs before any source row is read: a refusal must not be distinguishable
    from an empty result by how much work the server did.
    """
    cur.execute(
        """SELECT 1
           FROM owner_accounts oa
           JOIN contacts ct ON ct.id=oa.contact_id
           JOIN property_contacts pc ON pc.contact_id=oa.contact_id
           JOIN properties p ON p.id=pc.property_id
           WHERE oa.id=%s AND pc.property_id=%s AND pc.role=%s
             AND ct.agency_id=%s AND p.agency_id=%s
           LIMIT 1""",
        (owner_account_id, property_id, "owner", agency_id, agency_id),
    )
    if not cur.fetchone():
        raise NotFoundError(_NOT_FOUND)


def lookup_property_documents(
    agency_id: int, owner_account_id: int, property_id: int
) -> list[dict]:
    """Return a storage-safe projection only for an owner-eligible property."""
    with core_cursor() as (_, cur):
        _ensure_owner_eligible_property(cur, agency_id, owner_account_id, property_id)
        # The guard above has already pinned the property to this agency. The
        # tenant is named here too so that every statement stands on its own -
        # a later edit that moves or weakens the guard cannot silently widen
        # this one.
        cur.execute(
            """SELECT id,title,document_type,status,expires_at
               FROM property_documents
               WHERE property_id=%s
                 AND property_id IN (SELECT id FROM properties WHERE agency_id=%s)
               ORDER BY created_at DESC,id DESC""",
            (property_id, agency_id),
        )
        return [dict(item) for item in cur.fetchall()]


def lookup_property_visits(
    agency_id: int, owner_account_id: int, property_id: int
) -> list[dict]:
    """Return a visitor-safe projection only for an owner-eligible property."""
    with core_cursor() as (_, cur):
        _ensure_owner_eligible_property(cur, agency_id, owner_account_id, property_id)
        cur.execute(
            """SELECT id,scheduled_at,status
               FROM property_visits
               WHERE property_id=%s
                 AND property_id IN (SELECT id FROM properties WHERE agency_id=%s)
               ORDER BY scheduled_at DESC,id DESC""",
            (property_id, agency_id),
        )
        return [dict(item) for item in cur.fetchall()]
