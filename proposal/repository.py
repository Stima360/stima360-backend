from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from psycopg2 import errors

from buy.repository import history
from core.database import core_cursor
from core.exceptions import ConflictError, NotFoundError, ValidationError

from .enums import PROPOSAL_TRANSITIONS, TERMINAL_PROPOSAL_STATUSES


def _row(value):
    return dict(value) if value else None


def _relation(cur, match_id: int, *, lock: bool = False):
    suffix = " FOR UPDATE OF m,b,p" if lock else ""
    cur.execute(
        f"""SELECT m.id AS match_id,m.archived_at AS match_archived_at,
            b.id AS buy_request_id,b.title AS buy_title,b.contact_id,b.lead_id,
            b.archived_at AS buy_archived_at,c.display_name AS contact_name,
            p.id AS property_id,p.title AS property_title,p.code AS property_code,
            p.archived_at AS property_archived_at
            FROM matches m
            JOIN buy_requests b ON b.id=m.buy_request_id
            JOIN contacts c ON c.id=b.contact_id
            JOIN properties p ON p.id=m.property_id
            WHERE m.id=%s{suffix}""",
        (match_id,),
    )
    relation = _row(cur.fetchone())
    if not relation:
        raise NotFoundError(f"match {match_id} not found")
    if relation.get("match_archived_at") is not None:
        raise ValidationError("match is archived")
    if relation.get("buy_archived_at") is not None:
        raise ValidationError("buy request is archived")
    if relation.get("property_archived_at") is not None:
        raise ValidationError("property is archived")
    return relation


def _proposal(cur, proposal_id: int, *, lock: bool = False):
    suffix = " FOR UPDATE OF pp" if lock else ""
    cur.execute(
        f"""SELECT pp.*,NOW() AS database_now,
            m.buy_request_id,m.property_id,
            b.title AS buy_title,b.contact_id,b.lead_id,
            c.display_name AS contact_name,
            p.title AS property_title,p.code AS property_code
            FROM property_proposals pp
            JOIN matches m ON m.id=pp.match_id
            JOIN buy_requests b ON b.id=m.buy_request_id
            JOIN contacts c ON c.id=b.contact_id
            JOIN properties p ON p.id=m.property_id
            WHERE pp.id=%s{suffix}""",
        (proposal_id,),
    )
    result = _row(cur.fetchone())
    if not result:
        raise NotFoundError(f"proposal {proposal_id} not found")
    return result


def _same_create_payload(existing: dict, data: dict) -> bool:
    return (
        int(existing["match_id"]) == int(data["match_id"])
        and Decimal(existing["amount"]) == Decimal(data["amount"])
        and _same_instant(existing["expires_at"], data["expires_at"])
        and existing.get("notes") == data.get("notes")
    )


def _same_instant(left: datetime, right: datetime) -> bool:
    if left.utcoffset() is None or right.utcoffset() is None:
        return False
    return left.astimezone(timezone.utc) == right.astimezone(timezone.utc)


def _history_value(proposal: dict) -> dict:
    return {
        "proposal_id": proposal["id"],
        "status": proposal["status"],
        "amount": proposal["amount"],
        "expires_at": proposal["expires_at"],
    }


def create_proposal(data: dict, created_by: str):
    data = dict(data)
    data["idempotency_key"] = str(data["idempotency_key"])
    with core_cursor(commit=True) as (_, cur):
        relation = _relation(cur, data["match_id"], lock=True)
        cur.execute(
            "SELECT * FROM property_proposals WHERE idempotency_key=%s FOR UPDATE",
            (data["idempotency_key"],),
        )
        existing = _row(cur.fetchone())
        if existing:
            if not _same_create_payload(existing, data):
                raise ConflictError("idempotency key already used with a different payload")
            return _proposal(cur, existing["id"])

        cur.execute(
            """SELECT id FROM property_proposals
            WHERE match_id=%s AND status IN ('draft','submitted') FOR UPDATE""",
            (data["match_id"],),
        )
        if cur.fetchone():
            raise ConflictError("open proposal already exists for match")

        try:
            cur.execute(
                """INSERT INTO property_proposals(
                    match_id,amount,expires_at,notes,idempotency_key,created_by
                ) VALUES(%s,%s,%s,%s,%s,%s)
                ON CONFLICT (idempotency_key) DO NOTHING RETURNING *""",
                (
                    data["match_id"],
                    data["amount"],
                    data["expires_at"],
                    data.get("notes"),
                    data["idempotency_key"],
                    created_by,
                ),
            )
            created = _row(cur.fetchone())
        except errors.UniqueViolation as exc:
            raise ConflictError("open proposal already exists for match") from exc

        if not created:
            cur.execute(
                "SELECT * FROM property_proposals WHERE idempotency_key=%s FOR UPDATE",
                (data["idempotency_key"],),
            )
            existing = _row(cur.fetchone())
            if not existing or not _same_create_payload(existing, data):
                raise ConflictError("idempotency key already used with a different payload")
            return _proposal(cur, existing["id"])

        history(
            cur,
            relation["buy_request_id"],
            "proposal_created",
            "Proposta creata",
            new_value=_history_value(created),
            match_id=relation["match_id"],
            property_id=relation["property_id"],
            created_by=created_by,
        )
        return _proposal(cur, created["id"])


def get_proposal(proposal_id: int):
    with core_cursor() as (_, cur):
        return _proposal(cur, proposal_id)


def list_proposals(
    *,
    match_id: int | None = None,
    buy_request_id: int | None = None,
    property_id: int | None = None,
    contact_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
):
    filters = ["TRUE"]
    params = []
    for expression, value in (
        ("pp.match_id=%s", match_id),
        ("m.buy_request_id=%s", buy_request_id),
        ("m.property_id=%s", property_id),
        ("b.contact_id=%s", contact_id),
    ):
        if value is not None:
            filters.append(expression)
            params.append(value)
    params.extend((limit, offset))
    with core_cursor() as (_, cur):
        cur.execute(
            f"""SELECT pp.*,NOW() AS database_now,
                m.buy_request_id,m.property_id,
                b.title AS buy_title,b.contact_id,b.lead_id,
                c.display_name AS contact_name,
                p.title AS property_title,p.code AS property_code
                FROM property_proposals pp
                JOIN matches m ON m.id=pp.match_id
                JOIN buy_requests b ON b.id=m.buy_request_id
                JOIN contacts c ON c.id=b.contact_id
                JOIN properties p ON p.id=m.property_id
                WHERE {' AND '.join(filters)}
                ORDER BY pp.created_at DESC,pp.id DESC LIMIT %s OFFSET %s""",
            params,
        )
        return [dict(item) for item in cur.fetchall()]


def update_proposal(proposal_id: int, data: dict, created_by: str):
    data = dict(data)
    with core_cursor(commit=True) as (_, cur):
        snapshot = _proposal(cur, proposal_id)
        _relation(cur, snapshot["match_id"], lock=True)
        current = _proposal(cur, proposal_id, lock=True)
        if current["status"] != "draft":
            raise ConflictError("only draft proposals can be updated")
        if not data:
            return current
        changed = {field: value for field, value in data.items() if current.get(field) != value}
        if not changed:
            return current
        assignments = [f"{field}=%s" for field in changed]
        assignments.append("updated_at=NOW()")
        cur.execute(
            f"UPDATE property_proposals SET {','.join(assignments)} WHERE id=%s RETURNING *",
            [*changed.values(), proposal_id],
        )
        updated = _row(cur.fetchone())
        history(
            cur,
            current["buy_request_id"],
            "proposal_updated",
            "Proposta aggiornata",
            old_value={field: current.get(field) for field in changed},
            new_value={field: updated.get(field) for field in changed},
            match_id=current["match_id"],
            property_id=current["property_id"],
            created_by=created_by,
        )
        return _proposal(cur, proposal_id)


def transition_proposal(proposal_id: int, target_status: str, created_by: str):
    with core_cursor(commit=True) as (_, cur):
        snapshot = _proposal(cur, proposal_id)
        _relation(cur, snapshot["match_id"], lock=True)
        current = _proposal(cur, proposal_id, lock=True)
        status = current["status"]
        if status in TERMINAL_PROPOSAL_STATUSES:
            raise ConflictError("proposal is terminal")
        if target_status not in PROPOSAL_TRANSITIONS[status]:
            raise ConflictError(f"transition {status} -> {target_status} is not allowed")

        expired = current["database_now"] >= current["expires_at"]
        if target_status == "expired" and not expired:
            raise ValidationError("proposal is not expired")
        if target_status == "accepted":
            if expired:
                raise ValidationError("proposal is expired")
            cur.execute("SELECT id FROM properties WHERE id=%s FOR UPDATE", (current["property_id"],))
            if not cur.fetchone():
                raise NotFoundError(f"property {current['property_id']} not found")
            cur.execute(
                """SELECT pp.id FROM property_proposals pp
                JOIN matches m ON m.id=pp.match_id
                WHERE m.property_id=%s AND pp.status='accepted' AND pp.id<>%s
                LIMIT 1""",
                (current["property_id"], proposal_id),
            )
            if cur.fetchone():
                raise ConflictError("accepted proposal already exists for property")

        cur.execute(
            "UPDATE property_proposals SET status=%s,updated_at=NOW() WHERE id=%s RETURNING *",
            (target_status, proposal_id),
        )
        updated = _row(cur.fetchone())
        history(
            cur,
            current["buy_request_id"],
            "proposal_status_changed",
            f"Proposta: {status} -> {target_status}",
            old_value={"status": status},
            new_value={"status": target_status},
            match_id=current["match_id"],
            property_id=current["property_id"],
            created_by=created_by,
        )
        return _proposal(cur, updated["id"])
