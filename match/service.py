from . import repository

def dump(model, exclude_unset=False): return model.dict(exclude_unset=exclude_unset)
def calculate_pair(payload): return repository.calculate_pair(payload.buy_request_id, payload.property_id, created_by=payload.created_by)
def calculate_for_buy(request_id, payload): return repository.calculate_for_buy(request_id, payload.created_by)
def calculate_for_property(property_id, payload): return repository.calculate_for_property(property_id, payload.created_by)
def list_matches(*args): return repository.list_matches(*args)
def get_match(match_id): return repository.get_match(match_id)
def update_match(match_id, payload):
    payload.validate_values(); return repository.update_match(match_id, dump(payload, True))
def set_override(match_id, payload): return repository.set_override(match_id, payload.manual_score, payload.manual_reason)
def clear_override(match_id): return repository.clear_override(match_id)
def add_exclusion(payload): return repository.add_exclusion(dump(payload))
def list_exclusions(): return repository.list_exclusions()
def delete_exclusion(exclusion_id): return repository.delete_exclusion(exclusion_id)
def dashboard(): return repository.dashboard()
