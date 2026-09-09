from . import repository

def dump(model,exclude_unset=False): return model.dict(exclude_unset=exclude_unset)
def create_request(p): return repository.create_request(dump(p))
def list_requests(*args): return repository.list_requests(*args)
def get_request(i): return repository.get_request(i)
def update_request(i,p): return repository.update_request(i,dump(p,True))
def archive_request(i): return repository.archive_request(i)
def add_location(i,p): return repository.add_child('buy_request_locations',i,dump(p))
def delete_location(i): return repository.delete_child('buy_request_locations',i,'location')
def add_typology(i,p): return repository.add_child('buy_request_typologies',i,dump(p))
def delete_typology(i): return repository.delete_child('buy_request_typologies',i,'typology')
def add_feature(i,p): return repository.add_child('buy_request_features',i,dump(p))
def delete_feature(i): return repository.delete_child('buy_request_features',i,'feature')
def normalized(i): return repository.normalized(i)
def dashboard(): return repository.dashboard()
def workflow(i): return repository.workflow(i)
def list_matches(i): return repository.list_matches(i)
def add_interaction(i,p): return repository.add_interaction(i,dump(p))
def update_interaction(i,p): return repository.update_interaction(i,dump(p,True))
def delete_interaction(i): return repository.delete_interaction(i)
def match_decision(i,match_id,p):
    data=dump(p)
    action=data.pop('action')
    if action == 'visit_scheduled':
        return repository.schedule_match_visit(i,match_id,data)
    data.pop('scheduled_at',None)
    data['match_id']=match_id
    data['interaction_type']=action
    return repository.add_interaction(i,data)
def create_task(i,p): return repository.create_task(i,dump(p))
def list_tasks(i): return repository.list_tasks(i)
def unlink_task(i): return repository.unlink_task(i)
def add_note(i,p): return repository.add_note(i,p.description,p.created_by)


# P26-2D scoped HTTP runtime surface

def create_request_scoped(ctx, p):
    return repository.create_request_scoped(ctx, dump(p))


def list_requests_scoped(ctx, *args):
    return repository.list_requests_scoped(ctx, *args)


def get_request_scoped(ctx, i):
    return repository.get_request_scoped(ctx, i)


def update_request_scoped(ctx, i, p):
    return repository.update_request_scoped(ctx, i, dump(p, True))


def archive_request_scoped(ctx, i):
    return repository.archive_request_scoped(ctx, i)


def add_location_scoped(ctx, i, p):
    return repository.add_child_scoped(ctx, "buy_request_locations", i, dump(p))


def delete_location_scoped(ctx, i):
    return repository.delete_child_scoped(ctx, "buy_request_locations", i, "location")


def add_typology_scoped(ctx, i, p):
    return repository.add_child_scoped(ctx, "buy_request_typologies", i, dump(p))


def delete_typology_scoped(ctx, i):
    return repository.delete_child_scoped(ctx, "buy_request_typologies", i, "typology")


def add_feature_scoped(ctx, i, p):
    return repository.add_child_scoped(ctx, "buy_request_features", i, dump(p))


def delete_feature_scoped(ctx, i):
    return repository.delete_child_scoped(ctx, "buy_request_features", i, "feature")


def normalized_scoped(ctx, i):
    return repository.normalized_scoped(ctx, i)


def dashboard_scoped(ctx):
    return repository.dashboard_scoped(ctx)


def workflow_scoped(ctx, i):
    return repository.workflow_scoped(ctx, i)


def list_matches_scoped(ctx, i):
    return repository.list_matches_scoped(ctx, i)


def add_interaction_scoped(ctx, i, p):
    return repository.add_interaction_scoped(ctx, i, dump(p))


def update_interaction_scoped(ctx, i, p):
    return repository.update_interaction_scoped(ctx, i, dump(p, True))


def delete_interaction_scoped(ctx, i):
    return repository.delete_interaction_scoped(ctx, i)


def match_decision_scoped(ctx, i, match_id, p):
    data = dump(p)
    action = data.pop("action")
    if action == "visit_scheduled":
        return repository.schedule_match_visit_scoped(ctx, i, match_id, data)
    data.pop("scheduled_at", None)
    data["match_id"] = match_id
    data["interaction_type"] = action
    return repository.add_interaction_scoped(ctx, i, data)


def create_task_scoped(ctx, i, p):
    return repository.create_task_scoped(ctx, i, dump(p))


def list_tasks_scoped(ctx, i):
    return repository.list_tasks_scoped(ctx, i)


def unlink_task_scoped(ctx, i):
    return repository.unlink_task_scoped(ctx, i)


def add_note_scoped(ctx, i, p):
    return repository.add_note_scoped(ctx, i, p.description, p.created_by)
