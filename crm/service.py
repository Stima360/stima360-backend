"""The CRM 360 assembly.

CRM owns no table. It reads eight subsystems and returns one document, so its
tenant isolation is entirely a question of what it passes down - there is no
`agency_id` column here that a migration could add, and no query of its own
that a predicate could bound.

P26-6C closes the four reads that were still unscoped. The imports below are
deliberately explicit about which surface each one uses: PROPERTY keeps
compatibility overloads that accept a context *or* silently do without it, so
a call site that reads as safe is not evidence that it is. Where a scoped twin
exists under its own name - BUY and MATCH - that name is imported rather than
the legacy one, and it is not aliased back to the legacy spelling.
"""

from buy.service import list_requests_scoped
# LMC-8: l'unica cucitura verso il portale proprietario, e passa da un
# adapter dedicato in sola lettura. Vedi `test_next3_crm_360::test_16`, che
# ammette esattamente questo modulo e continua a vietare tutti gli altri.
from owner.crm_radar import contact_homes_block as owner_home_block
from core.service import get_contact, list_activities, list_leads, list_tasks
from match.service import list_matches_scoped
from property.service import list_properties, list_visits_by_contact


def get_contact_360(ctx, contact_id: int) -> dict:
    """Assemble one contact's full picture, inside one agency.

    `ctx` is the caller's AgencyScope, forwarded unchanged - by identity, not
    by reconstruction - to all eight reads. CRM synthesizes no agency of its
    own: an agency arrives here only because the caller already had one.

    The agency is required up front rather than at the first read that happens
    to need it. When this was written the reason was sharp: an unbound platform
    admin would be served by CORE, whose `scoped_predicate` answered such a
    context with a deliberate platform-wide `TRUE`, and refused a few
    statements later by PROPERTY, whose `require_agency()` did not - so the
    request would have read across every tenant before failing.

    P27-1, decision D1, removed that branch: CORE now refuses an unbound
    context exactly as PROPERTY always did, so the divergence this guard was
    written to absorb no longer exists. The guard STAYS - it is one comparison,
    it keeps the refusal on this route at the first statement rather than the
    second, and an aggregator that reads six subsystems should state the scope
    it needs rather than discover it. What it no longer does is compensate for
    a disagreement between two subsystems.

    MATCHES ARE A PAIR, NOT A LIST

    The loop enumerates matches by `buy_request_id`. Scoping the match read
    alone would not be enough while the request ids came from an unscoped buy
    read: the scoped match query would faithfully return the matches of a
    foreign request, having been handed its id. Both sides moved together.
    """
    ctx.require_agency()

    contact_data = dict(get_contact(ctx, contact_id))
    roles = list(contact_data.pop("roles", []))

    leads = list_leads(ctx, 500, 0, contact_id, None, None, None)
    # The 11 positional filters are the legacy signature and are unchanged; the
    # context is prepended, which is the overload that turns on `p.agency_id`.
    properties = list_properties(
        ctx,
        500,
        0,
        None,
        None,
        None,
        None,
        contact_id,
        None,
        None,
        False,
        False,
    )
    buy_requests = list_requests_scoped(
        ctx, 500, 0, None, None, None, None, contact_id, None, None
    )

    matches = []
    for request in buy_requests:
        matches.extend(
            list_matches_scoped(
                ctx,
                limit=500,
                offset=0,
                buy_request_id=request["id"],
                property_id=None,
                match_class=None,
                commercial_status=None,
                compatible_only=False,
                freshness_status=None,
                review_required=None,
            )
        )

    visits = list_visits_by_contact(ctx, contact_id)
    activities = list_activities(ctx, 500, 0, contact_id, None, None)
    tasks = list_tasks(ctx, 500, 0, contact_id, None, None, None)

    return {
        "contact": contact_data,
        "roles": roles,
        "leads": leads,
        "properties": properties,
        "buy_requests": buy_requests,
        "matches": matches,
        "visits": visits,
        "activities": activities,
        "tasks": tasks,
        # LMC-8, additivo: le case pre-incarico di questo contatto, ognuna
        # con il radar di LMC-7. Il livello NON si calcola qui.
        "owner_home": owner_home_block(ctx, contact_id),
    }
