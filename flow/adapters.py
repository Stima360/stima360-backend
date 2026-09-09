from __future__ import annotations
from datetime import datetime, timezone
from core.database import core_cursor
from core.exceptions import NotFoundError


def _one(cur, sql, params, label):
    cur.execute(sql, params)
    row = cur.fetchone()
    if not row: raise NotFoundError(label)
    return dict(row)


def load_entity(entity_type: str, entity_id: int) -> dict:
    with core_cursor() as (_, cur):
        if entity_type == "lead":
            x = _one(cur, "SELECT * FROM leads WHERE id=%s", (entity_id,), f"lead {entity_id} not found")
            cur.execute("SELECT COUNT(*) AS n FROM activities WHERE lead_id=%s", (entity_id,)); x["activity_count"] = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM tasks WHERE lead_id=%s AND status IN ('open','in_progress')", (entity_id,)); x["open_task_count"] = cur.fetchone()["n"]
            x["entity_type"]="lead"; x["entity_id"]=entity_id; return x
        if entity_type == "property":
            x = _one(cur, "SELECT * FROM properties WHERE id=%s AND archived_at IS NULL", (entity_id,), f"property {entity_id} not found")
            cur.execute("SELECT COUNT(*) AS n FROM property_documents WHERE property_id=%s AND (status IN ('missing','requested','expired','rejected') OR (expires_at IS NOT NULL AND expires_at<CURRENT_DATE))", (entity_id,)); x["document_issue_count"] = cur.fetchone()["n"]
            cur.execute("SELECT contact_id FROM property_contacts WHERE property_id=%s ORDER BY is_primary DESC,id LIMIT 1", (entity_id,)); r=cur.fetchone(); x["contact_id"] = r["contact_id"] if r else None
            cur.execute("SELECT lead_id FROM property_leads WHERE property_id=%s ORDER BY id LIMIT 1", (entity_id,)); r=cur.fetchone(); x["lead_id"] = r["lead_id"] if r else None
            x["entity_type"]="property"; x["entity_id"]=entity_id; return x
        if entity_type == "buy_request":
            x = _one(cur, "SELECT * FROM buy_requests WHERE id=%s AND archived_at IS NULL", (entity_id,), f"buy request {entity_id} not found")
            x["entity_type"]="buy_request"; x["entity_id"]=entity_id; return x
        if entity_type == "match":
            x = _one(cur, """SELECT m.*,b.contact_id,b.lead_id,b.title AS buy_title,p.title AS property_title
                FROM matches m JOIN buy_requests b ON b.id=m.buy_request_id JOIN properties p ON p.id=m.property_id
                WHERE m.id=%s AND m.archived_at IS NULL""", (entity_id,), f"match {entity_id} not found")
            cur.execute("SELECT COUNT(*) AS n FROM buy_request_interactions WHERE match_id=%s AND interaction_type='proposed'", (entity_id,)); x["proposed_count"] = cur.fetchone()["n"]
            x["entity_type"]="match"; x["entity_id"]=entity_id; return x
        if entity_type == "property_visit":
            x = _one(cur, """SELECT v.*,p.title AS property_title FROM property_visits v JOIN properties p ON p.id=v.property_id WHERE v.id=%s""", (entity_id,), f"visit {entity_id} not found")
            cur.execute("SELECT COUNT(*) AS n FROM buy_request_interactions WHERE property_visit_id=%s AND interaction_type IN ('visited','interested','discarded','offer_candidate')", (entity_id,)); x["feedback_count"] = cur.fetchone()["n"]
            x["entity_type"]="property_visit"; x["entity_id"]=entity_id; return x
        if entity_type == "owner_feedback":
            x = _one(cur, """SELECT f.id,f.feedback_type,f.property_id,f.linked_activity_id,oa.contact_id
                FROM owner_feedback f JOIN owner_accounts oa ON oa.id=f.owner_account_id
                WHERE f.id=%s""", (entity_id,), f"owner feedback {entity_id} not found")
            return {
                "entity_type": "owner_feedback",
                "entity_id": entity_id,
                "owner_request_type": x["feedback_type"],
                "property_id": x["property_id"],
                "contact_id": x["contact_id"],
                "linked_activity_id": x["linked_activity_id"],
            }
    raise ValueError(f"unsupported entity_type {entity_type}")


# ---------------------------------------------------------------------------
# P26-6C: one definition per rule, used by BOTH the global scan and the
# per-agency one.
#
# Before this the two lived side by side and the scoped pair covered only the
# two rules NBA consumed. Extending that shape to every rule would have meant
# writing each business filter twice, and two copies of a rule are two rules
# that will eventually disagree - the WHERE clause here IS the automation's
# behaviour, so a drift between them is a drift in what the system does, not a
# stylistic one.
#
# Each entry supplies:
#
#   entity        what the ids name
#   source        FROM/JOIN, unscoped and scoped. They differ only where
#                 tenancy needs a join the business filter does not: a match
#                 has no agency column (its tenancy is its pair, P26-4) and a
#                 visit inherits its property's.
#   select        the projection, unscoped and scoped (aliases differ with the
#                 source)
#   where         the business filter, written once. `where_scoped` exists only
#                 where the scoped source introduces an alias, and is the same
#                 predicate with that alias applied - never a different filter.
#   tenant        the predicate that bounds it, applied only when an agency is
#                 given
#   order         the frozen ordering
#   params        the business parameters, in the order the filter names them
#
# Rules with no scan at all - the OWNER family, which is event-driven - are
# listed explicitly with `None` rather than left to fall off the end of a
# function. An unknown rule code and a rule that legitimately has no population
# to sweep are different answers, and the scoped entry point has to tell them
# apart.
#
# Every filter is applied in SQL. Nothing here selects a population and narrows
# it in Python.
# ---------------------------------------------------------------------------

_SCANS = {
    "FLOW-R001": {
        "entity": "lead",
        "source": ("leads", "leads"),
        "select": ("id", "id"),
        "where": "status='open'",
        "tenant": "agency_id=%s",
        "order": "created_at",
        "params": lambda p: (),
    },
    "FLOW-R002": {
        "entity": "property",
        "source": ("properties", "properties"),
        "select": ("id", "id"),
        "where": (
            "archived_at IS NULL "
            "AND commercial_status NOT IN ('sold','withdrawn','archived') "
            "AND mandate_end IS NOT NULL "
            "AND mandate_end<=CURRENT_DATE+(%s||' days')::interval"
        ),
        "tenant": "agency_id=%s",
        "order": "mandate_end",
        "params": lambda p: (p["days_before_expiry"],),
    },
    "FLOW-R003": {
        "entity": "property",
        "source": (
            "properties p JOIN property_documents d ON d.property_id=p.id",
            "properties p JOIN property_documents d ON d.property_id=p.id",
        ),
        "select": ("DISTINCT p.id", "DISTINCT p.id"),
        "where": (
            "p.archived_at IS NULL "
            "AND (d.status IN ('missing','requested','expired','rejected') "
            "OR (d.expires_at IS NOT NULL AND d.expires_at<CURRENT_DATE))"
        ),
        "tenant": "p.agency_id=%s",
        "order": "p.id",
        "params": lambda p: (),
    },
    "FLOW-R004": {
        "entity": "buy_request",
        "source": ("buy_requests", "buy_requests"),
        "select": ("id", "id"),
        "where": (
            "status='active' AND archived_at IS NULL "
            "AND next_action_at IS NOT NULL "
            "AND next_action_at<=NOW()-(%s||' hours')::interval"
        ),
        "tenant": "agency_id=%s",
        "order": "next_action_at",
        "params": lambda p: (p["overdue_hours"],),
    },
    # A match carries no agency column. Its tenancy is its pair, and P26-4
    # guarantees both roots share one agency - but both sides are named here
    # rather than only the buy request, because that guarantee is enforced by a
    # write-time trigger and a read should not depend on an invariant it can
    # assert for itself.
    "FLOW-R005": {
        "entity": "match",
        "source": (
            "matches",
            "matches m JOIN buy_requests b ON b.id=m.buy_request_id "
            "JOIN properties p ON p.id=m.property_id",
        ),
        "select": ("id", "m.id"),
        "where": (
            "archived_at IS NULL AND freshness_status='fresh' "
            "AND score_total>=%s AND commercial_status IN ('new','to_review')"
        ),
        "where_scoped": (
            "m.archived_at IS NULL AND m.freshness_status='fresh' "
            "AND m.score_total>=%s AND m.commercial_status IN ('new','to_review')"
        ),
        "tenant": "b.agency_id=%s AND p.agency_id=%s",
        "order": "score_total DESC",
        "order_scoped": "m.score_total DESC",
        "params": lambda p: (p["minimum_score"],),
    },
    "FLOW-R006": {
        "entity": "match",
        "source": (
            "matches",
            "matches m JOIN buy_requests b ON b.id=m.buy_request_id "
            "JOIN properties p ON p.id=m.property_id",
        ),
        "select": ("id", "m.id"),
        "where": "archived_at IS NULL AND review_required=TRUE",
        "where_scoped": "m.archived_at IS NULL AND m.review_required=TRUE",
        "tenant": "b.agency_id=%s AND p.agency_id=%s",
        "order": "updated_at",
        "order_scoped": "m.updated_at",
        "params": lambda p: (),
    },
    # A visit has no agency column either; it inherits its property's, and
    # `property_id` is NOT NULL, so the join can never drop a row the unscoped
    # scan would have returned for this agency.
    "FLOW-R007": {
        "entity": "property_visit",
        "source": (
            "property_visits",
            "property_visits v JOIN properties p ON p.id=v.property_id",
        ),
        "select": ("id", "v.id"),
        "where": (
            "status='completed' AND updated_at<=NOW()-(%s||' hours')::interval"
        ),
        "where_scoped": (
            "v.status='completed' AND v.updated_at<=NOW()-(%s||' hours')::interval"
        ),
        "tenant": "p.agency_id=%s",
        "order": "updated_at",
        "order_scoped": "v.updated_at",
        "params": lambda p: (p["feedback_wait_hours"],),
    },
    # The OWNER family is event-driven: it fires on `owner.request_submitted`
    # and has no population to sweep. Listed so that "no candidates" is an
    # answer this table gives rather than an absence the code falls through.
    "FLOW-R008": None,
    "FLOW-R009": None,
    "FLOW-R010": None,
    "FLOW-R011": None,
    "FLOW-R012": None,
}


def _scan_sql(spec: dict, *, agency_id: int | None, parameters: dict, limit: int):
    """Build one scan statement, with or without the tenant predicate."""
    scoped = agency_id is not None
    source = spec["source"][1 if scoped else 0]
    select = spec["select"][1 if scoped else 0]
    where = spec["where_scoped"] if scoped and "where_scoped" in spec else spec["where"]
    order = spec["order_scoped"] if scoped and "order_scoped" in spec else spec["order"]

    business = spec["params"](parameters)
    if scoped:
        # The tenant predicate leads, so its placeholders bind before the
        # business ones and the parameter order cannot drift as filters change.
        clause = f"{spec['tenant']} AND {where}"
        params = (agency_id,) * spec["tenant"].count("%s") + business + (limit,)
    else:
        clause = where
        params = business + (limit,)

    return f"SELECT {select} FROM {source} WHERE {clause} ORDER BY {order} LIMIT %s", params


def scan_candidates(rule_code: str, parameters: dict, limit: int) -> list[tuple[str,int]]:
    """The unscoped scan. Behaviour unchanged; now built from _SCANS.

    Retained because the historical FLOW tests call it directly and because it
    is what `scan_candidates_for_agency` is defined against - but since P26-6C
    it is reachable from no HTTP route, which is the point of the slice.
    """
    spec = _SCANS.get(rule_code)
    if spec is None:
        return []
    sql, params = _scan_sql(spec, agency_id=None, parameters=parameters, limit=limit)
    with core_cursor() as (_, cur):
        cur.execute(sql, params)
        return [(spec["entity"], r["id"]) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# The agency-scoped surface.
#
# P26-6B added wrappers here for the two rules Next Best Action consumes,
# deliberately leaving FLOW itself unmigrated. P26-6C finishes the job: FLOW's
# own routes now run per agency, and a router that could serve only two of
# twelve rules would not be a migration.
#
# Nothing below re-states a business filter. The scan builds from the same
# `_SCANS` entry the global scan uses, with the tenant predicate composed onto
# it, so the two can never drift apart.
# ---------------------------------------------------------------------------

# Derived from _SCANS rather than typed out, so a rule added to the registry
# without a scan definition is refused rather than silently unscoped.
AGENCY_SCOPED_RULES = tuple(sorted(_SCANS))

# The six entity types FLOW's rules name. Each reaches a tenant by the route
# its own module already certified - CORE, PROPERTY, BUY, MATCH, and for
# owner_feedback the OWNER chain (account -> contact -> agency).
SCOPED_ENTITY_TYPES = (
    "lead", "property", "buy_request", "match", "property_visit", "owner_feedback",
)


class UnscopedRuleError(NotImplementedError):
    """A FLOW rule with no agency-scoped implementation was asked for one."""


def scan_candidates_for_agency(
    agency_id: int, rule_code: str, parameters: dict, limit: int
) -> list[tuple[str, int]]:
    """Candidates for one rule, inside one agency.

    An unknown rule code is refused rather than answered with an empty list.
    The unscoped `scan_candidates` returns `[]` for anything it does not
    recognise, and inheriting that here would turn "I cannot scope this" into
    "there is nothing to do" - the quieter and more dangerous of the two.
    """
    if rule_code not in _SCANS:
        raise UnscopedRuleError(
            f"FLOW rule {rule_code!r} has no agency-scoped scan; "
            f"scoped rules are {', '.join(AGENCY_SCOPED_RULES)}"
        )
    spec = _SCANS[rule_code]
    if spec is None:
        # Event-driven: no population to sweep. Distinct from the refusal above.
        return []
    sql, params = _scan_sql(spec, agency_id=agency_id, parameters=parameters, limit=limit)
    with core_cursor() as (_, cur):
        cur.execute(sql, params)
        return [(spec["entity"], r["id"]) for r in cur.fetchall()]


def load_entity_for_agency(agency_id: int, entity_type: str, entity_id: int) -> dict:
    """Every entity type the scoped rules produce, resolved in one agency.

    A foreign id raises the same NotFoundError an absent one would: the caller
    learns the entity is not available to it, not that it exists elsewhere. An
    id guessed from another agency is therefore indistinguishable from one that
    was never there.

    The entity type is refused before the cursor opens, matching
    `scan_candidates_for_agency`: a refusal that has already started a
    transaction is a refusal that touched the database first, and this module
    is the one place where an unscoped read would have no predicate to catch it.

    Child reads are keyed on the parent id and carry no predicate of their own.
    That is not an omission - the parent has already resolved inside the
    agency, so the ids they are given are this agency's, the same reasoning
    P26-6B applied to the buy-request child tables. A redundant filter there
    would suggest they were a separate risk.
    """
    if entity_type not in SCOPED_ENTITY_TYPES:
        raise UnscopedRuleError(
            f"entity type {entity_type!r} has no agency-scoped loader"
        )
    with core_cursor() as (_, cur):
        if entity_type == "lead":
            x = _one(
                cur,
                "SELECT * FROM leads WHERE id=%s AND agency_id=%s",
                (entity_id, agency_id),
                f"lead {entity_id} not found",
            )
            cur.execute("SELECT COUNT(*) AS n FROM activities WHERE lead_id=%s", (entity_id,))
            x["activity_count"] = cur.fetchone()["n"]
            cur.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE lead_id=%s "
                "AND status IN ('open','in_progress')",
                (entity_id,),
            )
            x["open_task_count"] = cur.fetchone()["n"]
            x["entity_type"] = "lead"; x["entity_id"] = entity_id
            return x

        if entity_type == "property":
            x = _one(
                cur,
                "SELECT * FROM properties WHERE id=%s AND archived_at IS NULL AND agency_id=%s",
                (entity_id, agency_id),
                f"property {entity_id} not found",
            )
            cur.execute(
                "SELECT COUNT(*) AS n FROM property_documents WHERE property_id=%s "
                "AND (status IN ('missing','requested','expired','rejected') "
                "OR (expires_at IS NOT NULL AND expires_at<CURRENT_DATE))",
                (entity_id,),
            )
            x["document_issue_count"] = cur.fetchone()["n"]
            cur.execute(
                "SELECT contact_id FROM property_contacts WHERE property_id=%s "
                "ORDER BY is_primary DESC,id LIMIT 1",
                (entity_id,),
            )
            r = cur.fetchone(); x["contact_id"] = r["contact_id"] if r else None
            cur.execute(
                "SELECT lead_id FROM property_leads WHERE property_id=%s ORDER BY id LIMIT 1",
                (entity_id,),
            )
            r = cur.fetchone(); x["lead_id"] = r["lead_id"] if r else None
            x["entity_type"] = "property"; x["entity_id"] = entity_id
            return x

        if entity_type == "buy_request":
            x = _one(
                cur,
                "SELECT * FROM buy_requests WHERE id=%s AND agency_id=%s AND archived_at IS NULL",
                (entity_id, agency_id),
                f"buy request {entity_id} not found",
            )
            x["entity_type"] = "buy_request"; x["entity_id"] = entity_id
            return x

        if entity_type == "match":
            x = _one(
                cur,
                """SELECT m.*,b.contact_id,b.lead_id,b.title AS buy_title,p.title AS property_title
                    FROM matches m
                    JOIN buy_requests b ON b.id=m.buy_request_id
                    JOIN properties p ON p.id=m.property_id
                    WHERE m.id=%s AND b.agency_id=%s AND p.agency_id=%s
                      AND m.archived_at IS NULL""",
                (entity_id, agency_id, agency_id),
                f"match {entity_id} not found",
            )
            cur.execute(
                "SELECT COUNT(*) AS n FROM buy_request_interactions WHERE match_id=%s "
                "AND interaction_type='proposed'",
                (entity_id,),
            )
            x["proposed_count"] = cur.fetchone()["n"]
            x["entity_type"] = "match"; x["entity_id"] = entity_id
            return x

        if entity_type == "property_visit":
            x = _one(
                cur,
                """SELECT v.*,p.title AS property_title
                    FROM property_visits v JOIN properties p ON p.id=v.property_id
                    WHERE v.id=%s AND p.agency_id=%s""",
                (entity_id, agency_id),
                f"visit {entity_id} not found",
            )
            cur.execute(
                "SELECT COUNT(*) AS n FROM buy_request_interactions "
                "WHERE property_visit_id=%s AND interaction_type IN "
                "('visited','interested','discarded','offer_candidate')",
                (entity_id,),
            )
            x["feedback_count"] = cur.fetchone()["n"]
            x["entity_type"] = "property_visit"; x["entity_id"] = entity_id
            return x

        # OWNER's tenancy is its contact's: owner_account -> contact -> agency,
        # and `owner_accounts.contact_id` is NOT NULL UNIQUE ON DELETE RESTRICT
        # so the chain cannot be broken. The property is checked on the same
        # row rather than trusted - owner_feedback names two parents that a bad
        # write could have disagreed about. This reads OWNER's shape; it does
        # not migrate OWNER.
        x = _one(
            cur,
            """SELECT f.id,f.feedback_type,f.property_id,f.linked_activity_id,oa.contact_id
                FROM owner_feedback f
                JOIN owner_accounts oa ON oa.id=f.owner_account_id
                JOIN contacts c ON c.id=oa.contact_id
                JOIN properties p ON p.id=f.property_id
                WHERE f.id=%s AND c.agency_id=%s AND p.agency_id=%s""",
            (entity_id, agency_id, agency_id),
            f"owner feedback {entity_id} not found",
        )
        return {
            "entity_type": "owner_feedback",
            "entity_id": entity_id,
            "owner_request_type": x["feedback_type"],
            "property_id": x["property_id"],
            "contact_id": x["contact_id"],
            "linked_activity_id": x["linked_activity_id"],
        }
