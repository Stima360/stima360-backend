from buy.service import list_requests as list_buy_requests
from core.service import get_contact, list_activities, list_leads, list_tasks
from match.service import list_matches
from property.service import list_properties, list_visits_by_contact


def get_contact_360(ctx, contact_id: int) -> dict:
    """Assemble one contact's full picture.

    `ctx` is the caller's AgencyScope, forwarded unchanged to the four CORE
    reads. CRM synthesizes no agency of its own: an agency arrives here only
    because the caller already had one.

    The non-CORE reads below - properties, buy requests, matches and visits -
    are deliberately left as they are. Those modules are out of scope for
    P26-1 (design spec section 11) and remain reachable cross-agency through
    the legacy Basic channel; giving them a `ctx` here would imply an isolation
    this phase does not deliver.
    """
    contact_data = dict(get_contact(ctx, contact_id))
    roles = list(contact_data.pop("roles", []))

    leads = list_leads(ctx, 500, 0, contact_id, None, None, None)
    properties = list_properties(
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
    buy_requests = list_buy_requests(500, 0, None, None, None, None, contact_id, None, None)

    matches = []
    for request in buy_requests:
        matches.extend(
            list_matches(
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

    visits = list_visits_by_contact(contact_id)
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
    }
