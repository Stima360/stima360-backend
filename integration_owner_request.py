"""Neutral OWNER integration bridge for cross-module event persistence.

Keeps OWNER free from direct FLOW imports while preserving the caller-owned
database transaction/cursor.
"""

from flow.repository import add_event_with_cursor as _add_event_with_cursor
from flow.service import process_saved_event as _process_saved_event


def record_owner_request_event_with_cursor(cur, data, *, agency_id):
    """Persist the OWNER-originated integration event using the caller cursor.

    P26-6C: a FLOW event carries a tenant. OWNER resolves it - an owner account
    is its contact's, and the contact carries the agency - and hands it over,
    rather than letting FLOW guess from a polymorphic entity reference it has
    no foreign key for.

    Keyword-only and without a default, so a caller that forgets is a TypeError
    here and not a NULL that 054 rejects one layer down. This is the whole of
    the OWNER adaptation P26-6C needed: no OWNER route, table or ownership
    decision changes in this slice.
    """
    return _add_event_with_cursor(cur, data, agency_id=agency_id)


def process_saved_owner_request_event(event_id):
    """Dispatch a committed OWNER FLOW event without surfacing automation failures to OWNER."""
    try:
        return _process_saved_event(event_id)
    except Exception:
        return None
