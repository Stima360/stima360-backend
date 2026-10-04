from core.exceptions import PermissionDenied, ValidationError
from operator_auth import permissions
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
    data = dump(p, True)
    # DELETE-ARCH Fase 0 - transizione compatibile: la scheda Acquirente
    # archivia con `PATCH status='archived'` (P25-5 non chiama la DELETE), e
    # quella PATCH ora E' l'azione Archivia: stesso ruolo (owner/admin),
    # stessi blocchi (409 ARCHIVE_BLOCKED) e `archived_at` scritto nella
    # stessa transazione, mai uno stato `archived` senza data. `archived_at`
    # non si imposta a mano. La riattivazione (da `archived` a un altro
    # stato) e' riservata a owner/admin e azzera `archived_at` nella stessa
    # scrittura.
    if "archived_at" in data:
        raise ValidationError("archived_at non si imposta a mano: archivia con lo stato 'archived' o riattiva cambiando stato")
    if "status" in data:
        corrente = repository.get_request_scoped(ctx, i)
        if data["status"] == "archived" and corrente.get("status") != "archived":
            _require_archive_role(ctx)
            archiviata = repository.archive_request_scoped(ctx, i)
            resto = {k: v for k, v in data.items() if k != "status"}
            return repository.update_request_scoped(ctx, i, resto) if resto else archiviata
        if corrente.get("status") == "archived" and data["status"] != "archived":
            _require_archive_role(ctx)
            data["archived_at"] = None
    return repository.update_request_scoped(ctx, i, data)


# DELETE-ARCH Fase 0 (contratto REV 2, D10): sulle richieste acquirente
# non esiste ancora un modello di assegnazione (solo `assigned_to` testuale),
# quindi archivia/riattiva restano a owner, admin e platform admin in acting;
# un `agent` riceve 403. Archiviare e' rifiutato con una proposta in corso o
# una vendita pendente (`409 ARCHIVE_BLOCKED` con i blocchi).
AGENT_CANNOT_ARCHIVE_REQUEST = "Un agente non archivia né riattiva una richiesta acquirente: chiedi a un amministratore."
ARCHIVE_BLOCKED_MESSAGE = "La richiesta ha processi aperti: chiudili prima di archiviare"


def _require_archive_role(ctx):
    if not permissions.sees_all_agency_records(getattr(ctx, "role", None), getattr(ctx, "is_platform_admin", False)):
        raise PermissionDenied(AGENT_CANNOT_ARCHIVE_REQUEST)


def archive_request_scoped(ctx, i):
    _require_archive_role(ctx)
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
