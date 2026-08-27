from buy.service import list_requests as list_buy_requests
from core.service import get_contact, list_activities, list_leads, list_tasks
from match.service import list_matches
from property.service import list_properties, list_visits_by_contact


def get_contact_360(contact_id: int) -> dict:
    contact_data = dict(get_contact(contact_id))
    roles = list(contact_data.pop("roles", []))

    leads = list_leads(500, 0, contact_id, None, None, None)
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
    activities = list_activities(500, 0, contact_id, None, None)
    tasks = list_tasks(500, 0, contact_id, None, None, None)

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
