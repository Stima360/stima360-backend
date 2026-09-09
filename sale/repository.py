from __future__ import annotations

from decimal import Decimal

from psycopg2 import errors

from buy.repository import history
from core.database import core_cursor
from core.exceptions import ConflictError, NotFoundError


def _row(value):
    return dict(value) if value else None


def _proposal_for_sale(cur, proposal_id: int, *, lock: bool = False):
    suffix = " FOR UPDATE OF pp" if lock else ""
    cur.execute(
        f"""SELECT pp.id,pp.status,pp.amount,pp.match_id,
            m.property_id,m.buy_request_id
            FROM property_proposals pp
            JOIN matches m ON m.id=pp.match_id
            WHERE pp.id=%s{suffix}""",
        (proposal_id,),
    )
    result = _row(cur.fetchone())
    if not result:
        raise NotFoundError(f"proposal {proposal_id} not found")
    return result


def _sale(cur, sale_id: int, *, lock: bool = False):
    suffix = " FOR UPDATE OF ps" if lock else ""
    cur.execute(
        f"""SELECT ps.*,
            p.title AS property_title,p.code AS property_code,
            b.title AS buy_title,b.contact_id,
            pr.amount AS proposal_amount,pr.status AS proposal_status
            FROM property_sales ps
            JOIN properties p ON p.id=ps.property_id
            JOIN buy_requests b ON b.id=ps.buy_request_id
            JOIN property_proposals pr ON pr.id=ps.proposal_id
            WHERE ps.id=%s{suffix}""",
        (sale_id,),
    )
    result = _row(cur.fetchone())
    if not result:
        raise NotFoundError(f"sale {sale_id} not found")
    return result


def _sellers(cur, sale_id: int):
    cur.execute(
        "SELECT * FROM property_sale_sellers WHERE sale_id=%s ORDER BY id",
        (sale_id,),
    )
    return [dict(item) for item in cur.fetchall()]


def _with_sellers(cur, sale: dict):
    sale = dict(sale)
    sale["sellers"] = _sellers(cur, sale["id"])
    return sale


def _same_create_payload(existing: dict, data: dict) -> bool:
    return (
        int(existing["proposal_id"]) == int(data["proposal_id"])
        and Decimal(existing["sale_price"]) == Decimal(data["effective_sale_price"])
        and existing.get("notes") == data.get("notes")
    )


def create_sale(data: dict, created_by: str):
    data = dict(data)
    data["idempotency_key"] = str(data["idempotency_key"])
    with core_cursor(commit=True) as (_, cur):
        proposal = _proposal_for_sale(cur, data["proposal_id"], lock=True)
        if proposal["status"] != "accepted":
            raise ConflictError("proposal must be accepted before a sale can be created")

        effective_sale_price = data.get("sale_price")
        if effective_sale_price is None:
            effective_sale_price = proposal["amount"]

        cur.execute(
            "SELECT * FROM property_sales WHERE idempotency_key=%s FOR UPDATE",
            (data["idempotency_key"],),
        )
        existing = _row(cur.fetchone())
        if existing:
            if not _same_create_payload(
                existing,
                {
                    "proposal_id": data["proposal_id"],
                    "effective_sale_price": effective_sale_price,
                    "notes": data.get("notes"),
                },
            ):
                raise ConflictError("idempotency key already used with a different payload")
            return _with_sellers(cur, _sale(cur, existing["id"]))

        cur.execute(
            "SELECT id FROM property_sales WHERE proposal_id=%s AND status IN ('pending','completed') FOR UPDATE",
            (data["proposal_id"],),
        )
        if cur.fetchone():
            raise ConflictError("an active sale already exists for this proposal")

        cur.execute(
            "SELECT id FROM property_sales WHERE property_id=%s AND status IN ('pending','completed') FOR UPDATE",
            (proposal["property_id"],),
        )
        if cur.fetchone():
            raise ConflictError("an active sale already exists for this property")

        cur.execute(
            """SELECT contact_id,role,ownership_share FROM property_contacts
            WHERE property_id=%s AND role IN ('owner','seller')
            ORDER BY contact_id,role""",
            (proposal["property_id"],),
        )
        eligible_sellers = [dict(item) for item in cur.fetchall()]
        if not eligible_sellers:
            raise ConflictError("no eligible seller/owner registered for this property")

        try:
            cur.execute(
                """INSERT INTO property_sales(
                    property_id,buy_request_id,proposal_id,sale_price,notes,
                    idempotency_key,created_by
                ) VALUES(%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (idempotency_key) DO NOTHING RETURNING *""",
                (
                    proposal["property_id"],
                    proposal["buy_request_id"],
                    data["proposal_id"],
                    effective_sale_price,
                    data.get("notes"),
                    data["idempotency_key"],
                    created_by,
                ),
            )
            created = _row(cur.fetchone())
        except errors.UniqueViolation as exc:
            constraint = getattr(exc.diag, "constraint_name", "") or ""
            if "proposal_active" in constraint:
                raise ConflictError("an active sale already exists for this proposal") from exc
            if "property_active" in constraint:
                raise ConflictError("an active sale already exists for this property") from exc
            raise ConflictError("sale already exists") from exc

        if not created:
            cur.execute(
                "SELECT * FROM property_sales WHERE idempotency_key=%s FOR UPDATE",
                (data["idempotency_key"],),
            )
            existing = _row(cur.fetchone())
            if not existing or not _same_create_payload(
                existing,
                {
                    "proposal_id": data["proposal_id"],
                    "effective_sale_price": effective_sale_price,
                    "notes": data.get("notes"),
                },
            ):
                raise ConflictError("idempotency key already used with a different payload")
            return _with_sellers(cur, _sale(cur, existing["id"]))

        for seller in eligible_sellers:
            cur.execute(
                """INSERT INTO property_sale_sellers(sale_id,contact_id,role,ownership_share)
                VALUES(%s,%s,%s,%s)""",
                (created["id"], seller["contact_id"], seller["role"], seller["ownership_share"]),
            )

        return _with_sellers(cur, _sale(cur, created["id"]))


def get_sale(sale_id: int):
    with core_cursor() as (_, cur):
        return _with_sellers(cur, _sale(cur, sale_id))


def list_sales(
    *,
    status: str | None = None,
    property_id: int | None = None,
    buy_request_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
):
    filters = ["TRUE"]
    params = []
    for expression, value in (
        ("ps.status=%s", status),
        ("ps.property_id=%s", property_id),
        ("ps.buy_request_id=%s", buy_request_id),
    ):
        if value is not None:
            filters.append(expression)
            params.append(value)
    params.extend((limit, offset))
    with core_cursor() as (_, cur):
        cur.execute(
            f"""SELECT ps.*,
                p.title AS property_title,p.code AS property_code,
                b.title AS buy_title,b.contact_id,
                pr.amount AS proposal_amount,pr.status AS proposal_status
                FROM property_sales ps
                JOIN properties p ON p.id=ps.property_id
                JOIN buy_requests b ON b.id=ps.buy_request_id
                JOIN property_proposals pr ON pr.id=ps.proposal_id
                WHERE {' AND '.join(filters)}
                ORDER BY ps.created_at DESC,ps.id DESC LIMIT %s OFFSET %s""",
            params,
        )
        return [dict(item) for item in cur.fetchall()]


def update_sale(sale_id: int, data: dict):
    data = dict(data)
    with core_cursor(commit=True) as (_, cur):
        current = _sale(cur, sale_id, lock=True)
        if current["status"] != "pending":
            raise ConflictError("only pending sales can be updated")
        if not data:
            return _with_sellers(cur, current)
        changed = {field: value for field, value in data.items() if current.get(field) != value}
        if not changed:
            return _with_sellers(cur, current)
        assignments = [f"{field}=%s" for field in changed]
        assignments.append("updated_at=NOW()")
        cur.execute(
            f"UPDATE property_sales SET {','.join(assignments)} WHERE id=%s RETURNING *",
            [*changed.values(), sale_id],
        )
        return _with_sellers(cur, _sale(cur, sale_id))


def complete_sale(sale_id: int, actor: str):
    with core_cursor(commit=True) as (_, cur):
        cur.execute("SELECT * FROM property_sales WHERE id=%s FOR UPDATE", (sale_id,))
        sale = _row(cur.fetchone())
        if not sale:
            raise NotFoundError(f"sale {sale_id} not found")
        if sale["status"] == "completed":
            return _with_sellers(cur, _sale(cur, sale_id))
        if sale["status"] == "cancelled":
            raise ConflictError("sale is cancelled")

        cur.execute(
            "SELECT id,commercial_status FROM properties WHERE id=%s FOR UPDATE",
            (sale["property_id"],),
        )
        property_row = _row(cur.fetchone())
        if not property_row:
            raise NotFoundError(f"property {sale['property_id']} not found")

        cur.execute(
            "SELECT id,status FROM buy_requests WHERE id=%s FOR UPDATE",
            (sale["buy_request_id"],),
        )
        buy_request_row = _row(cur.fetchone())
        if not buy_request_row:
            raise NotFoundError(f"buy request {sale['buy_request_id']} not found")

        cur.execute(
            "SELECT id,status,match_id FROM property_proposals WHERE id=%s FOR UPDATE",
            (sale["proposal_id"],),
        )
        proposal_row = _row(cur.fetchone())
        if not proposal_row:
            raise NotFoundError(f"proposal {sale['proposal_id']} not found")
        if proposal_row["status"] != "accepted":
            raise ConflictError("proposal is no longer accepted")

        cur.execute(
            "SELECT id,property_id,buy_request_id FROM matches WHERE id=%s",
            (proposal_row["match_id"],),
        )
        match_row = _row(cur.fetchone())
        if (
            not match_row
            or match_row["property_id"] != sale["property_id"]
            or match_row["buy_request_id"] != sale["buy_request_id"]
        ):
            raise ConflictError("relation chain mismatch between sale, proposal and match")

        cur.execute(
            """UPDATE property_sales
            SET status='completed',completed_at=NOW(),completed_by=%s,updated_at=NOW()
            WHERE id=%s RETURNING *""",
            (actor, sale_id),
        )
        updated = _row(cur.fetchone())

        if property_row["commercial_status"] != "sold":
            cur.execute(
                "UPDATE properties SET commercial_status='sold',updated_at=NOW() WHERE id=%s",
                (sale["property_id"],),
            )
            cur.execute(
                """INSERT INTO property_status_history(
                    property_id,field_name,old_value,new_value,note,changed_by
                ) VALUES(%s,'commercial_status',%s,'sold',%s,%s)""",
                (
                    sale["property_id"],
                    property_row["commercial_status"],
                    f"Vendita conclusa (sale #{sale_id})",
                    actor,
                ),
            )

        cur.execute(
            "UPDATE buy_requests SET status='satisfied',updated_at=NOW() WHERE id=%s",
            (sale["buy_request_id"],),
        )

        history(
            cur,
            sale["buy_request_id"],
            "sale_completed",
            f"Vendita conclusa (sale #{sale_id})",
            new_value={
                "sale_id": sale_id,
                "property_id": sale["property_id"],
                "proposal_id": sale["proposal_id"],
                "sale_price": updated["sale_price"],
            },
            property_id=sale["property_id"],
            created_by=actor,
        )

        return _with_sellers(cur, _sale(cur, sale_id))


def cancel_sale(sale_id: int, actor: str):
    with core_cursor(commit=True) as (_, cur):
        cur.execute("SELECT * FROM property_sales WHERE id=%s FOR UPDATE", (sale_id,))
        sale = _row(cur.fetchone())
        if not sale:
            raise NotFoundError(f"sale {sale_id} not found")
        if sale["status"] == "cancelled":
            return _with_sellers(cur, _sale(cur, sale_id))
        if sale["status"] == "completed":
            raise ConflictError("sale is completed")

        cur.execute(
            """UPDATE property_sales
            SET status='cancelled',cancelled_at=NOW(),cancelled_by=%s,updated_at=NOW()
            WHERE id=%s""",
            (actor, sale_id),
        )
        return _with_sellers(cur, _sale(cur, sale_id))


# ---------------------------------------------------------------------------
# P26-5 scoped HTTP runtime surface.
#
# A sale names three rows - a property, a buy request and a proposal - and they
# can disagree in two distinct ways:
#
#   CROSS-AGENCY            one of them belongs to another tenant
#   SAME-AGENCY WRONG PAIR  all of them are this tenant's, but the property and
#                           buy request are not the pair of the proposal's match
#
# The second is not a tenancy breach and no agency predicate catches it, yet
# completing such a sale would mark the wrong property sold and satisfy the
# wrong buy request. Both are refused: every root is re-read in scope, and the
# chain equality is asserted before anything is written.
#
# The legacy functions above are untouched: tests/test_sale_01_p10.py calls them
# directly. The actor still comes from require_admin, server-side.
# ---------------------------------------------------------------------------

_SALE_JOIN = """
            JOIN properties p ON p.id=ps.property_id
            JOIN buy_requests b ON b.id=ps.buy_request_id
            JOIN property_proposals pr ON pr.id=ps.proposal_id
"""
_SALE_SCOPE = "b.agency_id=%s AND p.agency_id=%s"


def _agency(ctx) -> int:
    return ctx.require_agency()


def _scoped_proposal_for_sale(cur, proposal_id: int, agency_id: int, *, lock: bool = False):
    suffix = " FOR UPDATE OF pp" if lock else ""
    cur.execute(
        f"""SELECT pp.id,pp.status,pp.amount,pp.match_id,
            m.property_id,m.buy_request_id
            FROM property_proposals pp
            JOIN matches m ON m.id=pp.match_id
            JOIN buy_requests b ON b.id=m.buy_request_id
            JOIN properties p ON p.id=m.property_id
            WHERE pp.id=%s AND b.agency_id=%s AND p.agency_id=%s{suffix}""",
        (proposal_id, agency_id, agency_id),
    )
    result = _row(cur.fetchone())
    if not result:
        raise NotFoundError(f"proposal {proposal_id} not found")
    return result


def _scoped_sale(cur, sale_id: int, agency_id: int, *, lock: bool = False):
    suffix = " FOR UPDATE OF ps" if lock else ""
    cur.execute(
        f"""SELECT ps.*,
            p.title AS property_title,p.code AS property_code,
            b.title AS buy_title,b.contact_id,
            pr.amount AS proposal_amount,pr.status AS proposal_status
            FROM property_sales ps {_SALE_JOIN}
            WHERE ps.id=%s AND {_SALE_SCOPE}{suffix}""",
        (sale_id, agency_id, agency_id),
    )
    result = _row(cur.fetchone())
    if not result:
        raise NotFoundError(f"sale {sale_id} not found")
    return result


def create_sale_scoped(ctx, data: dict, created_by: str):
    agency_id = _agency(ctx)
    data = dict(data)
    data["idempotency_key"] = str(data["idempotency_key"])
    with core_cursor(commit=True) as (_, cur):
        proposal = _scoped_proposal_for_sale(cur, data["proposal_id"], agency_id, lock=True)
        if proposal["status"] != "accepted":
            raise ConflictError("proposal must be accepted before a sale can be created")

        effective_sale_price = data.get("sale_price")
        if effective_sale_price is None:
            effective_sale_price = proposal["amount"]

        # Scoped: a replay of another tenant's globally unique key finds
        # nothing here and is refused by the unique index below, so the record
        # is never returned across the boundary.
        cur.execute(
            f"""SELECT ps.* FROM property_sales ps {_SALE_JOIN}
                WHERE ps.idempotency_key=%s AND {_SALE_SCOPE} FOR UPDATE OF ps""",
            (data["idempotency_key"], agency_id, agency_id),
        )
        existing = _row(cur.fetchone())
        if existing:
            if not _same_create_payload(
                existing,
                {
                    "proposal_id": data["proposal_id"],
                    "effective_sale_price": effective_sale_price,
                    "notes": data.get("notes"),
                },
            ):
                raise ConflictError("idempotency key already used with a different payload")
            return _with_sellers(cur, _scoped_sale(cur, existing["id"], agency_id))

        cur.execute(
            "SELECT id FROM property_sales WHERE proposal_id=%s AND status IN ('pending','completed') FOR UPDATE",
            (data["proposal_id"],),
        )
        if cur.fetchone():
            raise ConflictError("an active sale already exists for this proposal")

        cur.execute(
            "SELECT id FROM property_sales WHERE property_id=%s AND status IN ('pending','completed') FOR UPDATE",
            (proposal["property_id"],),
        )
        if cur.fetchone():
            raise ConflictError("an active sale already exists for this property")

        # The seller snapshot may only name contacts of this agency. It stays a
        # snapshot - historical by design, not re-derived later - but it cannot
        # be seeded from another tenant's contact rows.
        cur.execute(
            """SELECT pc.contact_id,pc.role,pc.ownership_share
            FROM property_contacts pc
            JOIN properties pr ON pr.id=pc.property_id
            JOIN contacts ct ON ct.id=pc.contact_id
            WHERE pc.property_id=%s AND pc.role IN ('owner','seller')
              AND pr.agency_id=%s AND ct.agency_id=%s
            ORDER BY pc.contact_id,pc.role""",
            (proposal["property_id"], agency_id, agency_id),
        )
        eligible_sellers = [dict(item) for item in cur.fetchall()]
        if not eligible_sellers:
            raise ConflictError("no eligible seller/owner registered for this property")

        try:
            cur.execute(
                """INSERT INTO property_sales(
                    property_id,buy_request_id,proposal_id,sale_price,notes,
                    idempotency_key,created_by
                ) VALUES(%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (idempotency_key) DO NOTHING RETURNING *""",
                (
                    proposal["property_id"],
                    proposal["buy_request_id"],
                    data["proposal_id"],
                    effective_sale_price,
                    data.get("notes"),
                    data["idempotency_key"],
                    created_by,
                ),
            )
            created = _row(cur.fetchone())
        except errors.UniqueViolation as exc:
            constraint = getattr(exc.diag, "constraint_name", "") or ""
            if "proposal_active" in constraint:
                raise ConflictError("an active sale already exists for this proposal") from exc
            if "property_active" in constraint:
                raise ConflictError("an active sale already exists for this property") from exc
            raise ConflictError("sale already exists") from exc

        if not created:
            cur.execute(
                f"""SELECT ps.* FROM property_sales ps {_SALE_JOIN}
                    WHERE ps.idempotency_key=%s AND {_SALE_SCOPE} FOR UPDATE OF ps""",
                (data["idempotency_key"], agency_id, agency_id),
            )
            existing = _row(cur.fetchone())
            if not existing or not _same_create_payload(
                existing,
                {
                    "proposal_id": data["proposal_id"],
                    "effective_sale_price": effective_sale_price,
                    "notes": data.get("notes"),
                },
            ):
                raise ConflictError("idempotency key already used with a different payload")
            return _with_sellers(cur, _scoped_sale(cur, existing["id"], agency_id))

        for seller in eligible_sellers:
            cur.execute(
                """INSERT INTO property_sale_sellers(sale_id,contact_id,role,ownership_share)
                VALUES(%s,%s,%s,%s)""",
                (created["id"], seller["contact_id"], seller["role"], seller["ownership_share"]),
            )

        return _with_sellers(cur, _scoped_sale(cur, created["id"], agency_id))


def get_sale_scoped(ctx, sale_id: int):
    agency_id = _agency(ctx)
    with core_cursor() as (_, cur):
        return _with_sellers(cur, _scoped_sale(cur, sale_id, agency_id))


def list_sales_scoped(
    ctx,
    *,
    status: str | None = None,
    property_id: int | None = None,
    buy_request_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
):
    agency_id = _agency(ctx)
    filters = [_SALE_SCOPE]
    params = [agency_id, agency_id]
    for expression, value in (
        ("ps.status=%s", status),
        ("ps.property_id=%s", property_id),
        ("ps.buy_request_id=%s", buy_request_id),
    ):
        if value is not None:
            filters.append(expression)
            params.append(value)
    params.extend((limit, offset))
    with core_cursor() as (_, cur):
        cur.execute(
            f"""SELECT ps.*,
                p.title AS property_title,p.code AS property_code,
                b.title AS buy_title,b.contact_id,
                pr.amount AS proposal_amount,pr.status AS proposal_status
                FROM property_sales ps {_SALE_JOIN}
                WHERE {' AND '.join(filters)}
                ORDER BY ps.created_at DESC,ps.id DESC LIMIT %s OFFSET %s""",
            params,
        )
        return [dict(item) for item in cur.fetchall()]


def update_sale_scoped(ctx, sale_id: int, data: dict):
    agency_id = _agency(ctx)
    data = dict(data)
    with core_cursor(commit=True) as (_, cur):
        current = _scoped_sale(cur, sale_id, agency_id, lock=True)
        if current["status"] != "pending":
            raise ConflictError("only pending sales can be updated")
        if not data:
            return _with_sellers(cur, current)
        changed = {field: value for field, value in data.items() if current.get(field) != value}
        if not changed:
            return _with_sellers(cur, current)
        assignments = [f"{field}=%s" for field in changed]
        assignments.append("updated_at=NOW()")
        cur.execute(
            f"UPDATE property_sales SET {','.join(assignments)} WHERE id=%s RETURNING *",
            [*changed.values(), sale_id],
        )
        return _with_sellers(cur, _scoped_sale(cur, sale_id, agency_id))


def complete_sale_scoped(ctx, sale_id: int, actor: str):
    """The most dangerous path: it writes the sale, the property, the property
    history and the buy request. Every root is resolved in scope first, and the
    chain equality is checked, before a single write."""
    agency_id = _agency(ctx)
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            f"""SELECT ps.* FROM property_sales ps {_SALE_JOIN}
                WHERE ps.id=%s AND {_SALE_SCOPE} FOR UPDATE OF ps""",
            (sale_id, agency_id, agency_id),
        )
        sale = _row(cur.fetchone())
        if not sale:
            raise NotFoundError(f"sale {sale_id} not found")
        if sale["status"] == "completed":
            return _with_sellers(cur, _scoped_sale(cur, sale_id, agency_id))
        if sale["status"] == "cancelled":
            raise ConflictError("sale is cancelled")

        cur.execute(
            "SELECT id,commercial_status FROM properties WHERE id=%s AND agency_id=%s FOR UPDATE",
            (sale["property_id"], agency_id),
        )
        property_row = _row(cur.fetchone())
        if not property_row:
            raise NotFoundError(f"property {sale['property_id']} not found")

        cur.execute(
            "SELECT id,status FROM buy_requests WHERE id=%s AND agency_id=%s FOR UPDATE",
            (sale["buy_request_id"], agency_id),
        )
        buy_request_row = _row(cur.fetchone())
        if not buy_request_row:
            raise NotFoundError(f"buy request {sale['buy_request_id']} not found")

        cur.execute(
            """SELECT pp.id,pp.status,pp.match_id FROM property_proposals pp
            JOIN matches m ON m.id=pp.match_id
            JOIN buy_requests b ON b.id=m.buy_request_id
            JOIN properties p ON p.id=m.property_id
            WHERE pp.id=%s AND b.agency_id=%s AND p.agency_id=%s FOR UPDATE OF pp""",
            (sale["proposal_id"], agency_id, agency_id),
        )
        proposal_row = _row(cur.fetchone())
        if not proposal_row:
            raise NotFoundError(f"proposal {sale['proposal_id']} not found")
        if proposal_row["status"] != "accepted":
            raise ConflictError("proposal is no longer accepted")

        cur.execute(
            """SELECT m.id,m.property_id,m.buy_request_id FROM matches m
            JOIN buy_requests b ON b.id=m.buy_request_id
            JOIN properties p ON p.id=m.property_id
            WHERE m.id=%s AND b.agency_id=%s AND p.agency_id=%s""",
            (proposal_row["match_id"], agency_id, agency_id),
        )
        match_row = _row(cur.fetchone())
        # Same-agency wrong pair: every row above may legitimately belong to
        # this tenant and the sale still describe a chain that does not exist.
        if (
            not match_row
            or match_row["property_id"] != sale["property_id"]
            or match_row["buy_request_id"] != sale["buy_request_id"]
        ):
            raise ConflictError("relation chain mismatch between sale, proposal and match")

        cur.execute(
            """UPDATE property_sales
            SET status='completed',completed_at=NOW(),completed_by=%s,updated_at=NOW()
            WHERE id=%s RETURNING *""",
            (actor, sale_id),
        )
        updated = _row(cur.fetchone())

        if property_row["commercial_status"] != "sold":
            cur.execute(
                "UPDATE properties SET commercial_status='sold',updated_at=NOW() WHERE id=%s",
                (sale["property_id"],),
            )
            cur.execute(
                """INSERT INTO property_status_history(
                    property_id,field_name,old_value,new_value,note,changed_by
                ) VALUES(%s,'commercial_status',%s,'sold',%s,%s)""",
                (
                    sale["property_id"],
                    property_row["commercial_status"],
                    f"Vendita conclusa (sale #{sale_id})",
                    actor,
                ),
            )

        cur.execute(
            "UPDATE buy_requests SET status='satisfied',updated_at=NOW() WHERE id=%s",
            (sale["buy_request_id"],),
        )

        history(
            cur,
            sale["buy_request_id"],
            "sale_completed",
            f"Vendita conclusa (sale #{sale_id})",
            new_value={
                "sale_id": sale_id,
                "property_id": sale["property_id"],
                "proposal_id": sale["proposal_id"],
                "sale_price": updated["sale_price"],
            },
            property_id=sale["property_id"],
            created_by=actor,
            agency_id=agency_id,
        )

        return _with_sellers(cur, _scoped_sale(cur, sale_id, agency_id))


def cancel_sale_scoped(ctx, sale_id: int, actor: str):
    agency_id = _agency(ctx)
    with core_cursor(commit=True) as (_, cur):
        sale = _scoped_sale(cur, sale_id, agency_id, lock=True)
        if sale["status"] == "cancelled":
            return _with_sellers(cur, sale)
        if sale["status"] == "completed":
            raise ConflictError("sale is completed")

        cur.execute(
            """UPDATE property_sales
            SET status='cancelled',cancelled_at=NOW(),cancelled_by=%s,updated_at=NOW()
            WHERE id=%s""",
            (actor, sale_id),
        )
        return _with_sellers(cur, _scoped_sale(cur, sale_id, agency_id))
