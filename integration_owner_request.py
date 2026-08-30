"""Neutral OWNER integration bridge for cross-module event persistence.

Keeps OWNER free from direct FLOW imports while preserving the caller-owned
database transaction/cursor.
"""

from flow.repository import add_event_with_cursor as _add_event_with_cursor
from flow.service import process_saved_event as _process_saved_event


def record_owner_request_event_with_cursor(cur, data):
    """Persist the OWNER-originated integration event using the caller cursor."""
    return _add_event_with_cursor(cur, data)


def process_saved_owner_request_event(event_id):
    """Dispatch a committed OWNER FLOW event without surfacing automation failures to OWNER."""
    try:
        return _process_saved_event(event_id)
    except Exception:
        return None
