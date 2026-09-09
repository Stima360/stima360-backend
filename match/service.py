from . import repository


def dump(model, exclude_unset=False):
    return model.model_dump(exclude_unset=exclude_unset)


def calculate_pair(payload):
    return repository.calculate_pair(payload.buy_request_id, payload.property_id, created_by=payload.created_by)


def calculate_for_buy(request_id, payload):
    return repository.calculate_for_buy(request_id, payload.created_by)


def calculate_for_property(property_id, payload):
    return repository.calculate_for_property(property_id, payload.created_by)


def get_readiness(buy_request_id=None, property_id=None):
    return repository.get_readiness(buy_request_id, property_id)


def list_matches(*args, **kwargs):
    return repository.list_matches(*args, **kwargs)


def get_match(match_id):
    return repository.get_match(match_id)


def update_match(match_id, payload):
    payload.validate_values()
    return repository.update_match(match_id, dump(payload, True))


def set_override(match_id, payload):
    return repository.set_override(match_id, payload.manual_score, payload.manual_reason)


def clear_override(match_id):
    return repository.clear_override(match_id)


def add_exclusion(payload):
    return repository.add_exclusion(dump(payload))


def list_exclusions():
    return repository.list_exclusions()


def delete_exclusion(exclusion_id):
    return repository.delete_exclusion(exclusion_id)


def detect_stale(payload):
    return repository.detect_stale(payload.match_id, payload.buy_request_id, payload.property_id)


def refresh_match(match_id, payload):
    return repository.refresh_match(match_id, payload.created_by, "manual", payload.trigger_reason)


def refresh_for_buy(request_id, payload):
    return repository.refresh_for_buy(request_id, payload.created_by, payload.trigger_reason)


def refresh_for_property(property_id, payload):
    return repository.refresh_for_property(property_id, payload.created_by, payload.trigger_reason)


def refresh_stale(limit, payload):
    return repository.refresh_stale(limit, payload.created_by, payload.trigger_reason)


def refresh_history(match_id):
    return repository.refresh_history(match_id)


def timeline(match_id):
    return repository.timeline(match_id)


def add_feedback(match_id, payload):
    return repository.add_feedback(match_id, dump(payload))


def list_feedback(match_id):
    return repository.list_feedback(match_id)


def delete_feedback(feedback_id):
    return repository.delete_feedback(feedback_id)


def dashboard():
    return repository.dashboard()


def dashboard_stale(limit):
    return repository.dashboard_stale(limit)


def dashboard_errors(limit):
    return repository.dashboard_errors(limit)


def dashboard_review(limit):
    return repository.dashboard_review(limit)


# ---------------------------------------------------------------------------
# P26-2E scoped surface.
#
# The 26 HTTP handlers call these; the legacy functions above keep their
# signatures for crm/service.py and the historical tests. This layer decides
# what is asked for, never which agency it belongs to: ctx is threaded straight
# through, unread and unmodified.
# ---------------------------------------------------------------------------

def calculate_pair_scoped(ctx, payload):
    return repository.calculate_pair_scoped(
        ctx, payload.buy_request_id, payload.property_id, created_by=payload.created_by
    )


def calculate_for_buy_scoped(ctx, request_id, payload):
    return repository.calculate_for_buy_scoped(ctx, request_id, payload.created_by)


def calculate_for_property_scoped(ctx, property_id, payload):
    return repository.calculate_for_property_scoped(ctx, property_id, payload.created_by)


def get_readiness_scoped(ctx, buy_request_id=None, property_id=None):
    return repository.get_readiness_scoped(ctx, buy_request_id, property_id)


def list_matches_scoped(ctx, *args, **kwargs):
    return repository.list_matches_scoped(ctx, *args, **kwargs)


def get_match_scoped(ctx, match_id):
    return repository.get_match_scoped(ctx, match_id)


def update_match_scoped(ctx, match_id, payload):
    payload.validate_values()
    return repository.update_match_scoped(ctx, match_id, dump(payload, True))


def set_override_scoped(ctx, match_id, payload):
    return repository.set_override_scoped(ctx, match_id, payload.manual_score, payload.manual_reason)


def clear_override_scoped(ctx, match_id):
    return repository.clear_override_scoped(ctx, match_id)


def add_exclusion_scoped(ctx, payload):
    return repository.add_exclusion_scoped(ctx, dump(payload))


def list_exclusions_scoped(ctx):
    return repository.list_exclusions_scoped(ctx)


def delete_exclusion_scoped(ctx, exclusion_id):
    return repository.delete_exclusion_scoped(ctx, exclusion_id)


def detect_stale_scoped(ctx, payload):
    return repository.detect_stale_scoped(
        ctx, payload.match_id, payload.buy_request_id, payload.property_id
    )


def refresh_match_scoped(ctx, match_id, payload):
    return repository.refresh_match_scoped(ctx, match_id, payload.created_by, "manual", payload.trigger_reason)


def refresh_for_buy_scoped(ctx, request_id, payload):
    return repository.refresh_for_buy_scoped(ctx, request_id, payload.created_by, payload.trigger_reason)


def refresh_for_property_scoped(ctx, property_id, payload):
    return repository.refresh_for_property_scoped(ctx, property_id, payload.created_by, payload.trigger_reason)


def refresh_stale_scoped(ctx, limit, payload):
    return repository.refresh_stale_scoped(ctx, limit, payload.created_by, payload.trigger_reason)


def refresh_history_scoped(ctx, match_id):
    return repository.refresh_history_scoped(ctx, match_id)


def timeline_scoped(ctx, match_id):
    return repository.timeline_scoped(ctx, match_id)


def add_feedback_scoped(ctx, match_id, payload):
    return repository.add_feedback_scoped(ctx, match_id, dump(payload))


def list_feedback_scoped(ctx, match_id):
    return repository.list_feedback_scoped(ctx, match_id)


def delete_feedback_scoped(ctx, feedback_id):
    return repository.delete_feedback_scoped(ctx, feedback_id)


def dashboard_scoped(ctx):
    return repository.dashboard_scoped(ctx)


def dashboard_stale_scoped(ctx, limit):
    return repository.dashboard_stale_scoped(ctx, limit)


def dashboard_errors_scoped(ctx, limit):
    return repository.dashboard_errors_scoped(ctx, limit)


def dashboard_review_scoped(ctx, limit):
    return repository.dashboard_review_scoped(ctx, limit)
