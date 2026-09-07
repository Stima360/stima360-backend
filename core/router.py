"""FastAPI router for STIMA360 CORE CRM.

Every handler that reaches a scoped CORE table takes its agency scope from
`Depends(require_operator)` and forwards it, unchanged, as the first argument
to the service call. The router builds no context of its own and reads no
agency, role or user from the request: an `agency_id` arriving in a query
string, a path or a body is exactly what the approved rule forbids trusting,
so there is nowhere for one to enter.

Staging note: `main.py` still mounts this router behind the legacy Basic
dependency, so between Task 11 and Task 15 a request passes both that outer
guard and `require_operator`. Task 15 owns replacing the outer dependency and
adding the Basic branch to `require_operator`; P26-1 is not deployed between
tasks.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service
from .exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDenied,
    ValidationError,
)
from .schemas import (
    ActivityCreate,
    AssignmentUpdate,
    ContactCreate,
    ContactRoleCreate,
    ContactUpdate,
    LeadCreate,
    LeadStimaLinkCreate,
    LeadUpdate,
    TaskCreate,
    TaskUpdate,
)

router = APIRouter(prefix="/api/core", tags=["core"])


# D-6: one constant body for every 404, so a record that exists in another
# agency is byte-identical to one that never existed. The exception's own
# message names the id and the entity - useful in a log, disclosing in a
# response - so it is deliberately not forwarded. Matches the wording
# owner/router_portal.py and owner/router_admin_lookups.py already use.
NOT_FOUND_MESSAGE = "Risorsa non trovata"

# Spec 11.2: a platform admin holding no membership cannot create through the
# generic agency endpoints. Nothing is hidden, so this is 403, not 404.
AGENCY_CONTEXT_REQUIRED_MESSAGE = "Questa operazione richiede un contesto di agenzia."


def _translate(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=NOT_FOUND_MESSAGE) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionDenied as exc:
        # D-6: 403 for a role refusal, never 404. Nothing is being hidden -
        # the caller legitimately knows the endpoint exists.
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(
            status_code=403, detail=AGENCY_CONTEXT_REQUIRED_MESSAGE
        ) from exc


@router.post("/contacts", status_code=201)
def create_contact(payload: ContactCreate, ctx: OperatorContext = Depends(require_operator)):
    return _translate(service.create_contact, ctx, payload)


@router.get("/contacts")
def list_contacts(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    status: str | None = None,
    ctx: OperatorContext = Depends(require_operator),
):
    return {"items": _translate(service.list_contacts, ctx, limit, offset, search, status)}


@router.get("/contacts/{contact_id}")
def get_contact(contact_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _translate(service.get_contact, ctx, contact_id)


@router.patch("/contacts/{contact_id}")
def update_contact(contact_id: int, payload: ContactUpdate, ctx: OperatorContext = Depends(require_operator)):
    return _translate(service.update_contact, ctx, contact_id, payload)


@router.post("/contacts/{contact_id}/roles", status_code=201)
def add_contact_role(contact_id: int, payload: ContactRoleCreate, ctx: OperatorContext = Depends(require_operator)):
    return _translate(service.add_contact_role, ctx, contact_id, payload)


@router.delete("/contacts/{contact_id}/roles/{role}", status_code=204)
def delete_contact_role(contact_id: int, role: str, ctx: OperatorContext = Depends(require_operator)):
    _translate(service.delete_contact_role, ctx, contact_id, role)
    return Response(status_code=204)


@router.patch("/contacts/{contact_id}/assignment")
def set_contact_assignment(
    contact_id: int,
    payload: AssignmentUpdate,
    ctx: OperatorContext = Depends(require_operator),
):
    """Assign or clear a contact's agent.

    A separate endpoint from PATCH /contacts/{id} on purpose: the generic
    update schema does not declare `assigned_agent_id`, so a reassignment
    cannot ride along on an ordinary field update (design spec section 10).
    """
    return _translate(service.set_contact_assignment, ctx, contact_id, payload)


@router.post("/leads", status_code=201)
def create_lead(payload: LeadCreate, ctx: OperatorContext = Depends(require_operator)):
    return _translate(service.create_lead, ctx, payload)


@router.get("/leads")
def list_leads(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    contact_id: int | None = None,
    pipeline: str | None = None,
    stage: str | None = None,
    status: str | None = None,
    ctx: OperatorContext = Depends(require_operator),
):
    return {
        "items": _translate(
            service.list_leads, ctx, limit, offset, contact_id, pipeline, stage, status
        )
    }


@router.get("/leads/{lead_id}")
def get_lead(lead_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _translate(service.get_lead, ctx, lead_id)


@router.patch("/leads/{lead_id}")
def update_lead(lead_id: int, payload: LeadUpdate, ctx: OperatorContext = Depends(require_operator)):
    return _translate(service.update_lead, ctx, lead_id, payload)


@router.patch("/leads/{lead_id}/assignment")
def set_lead_assignment(
    lead_id: int,
    payload: AssignmentUpdate,
    ctx: OperatorContext = Depends(require_operator),
):
    """Assign or clear a lead's agent. See set_contact_assignment above."""
    return _translate(service.set_lead_assignment, ctx, lead_id, payload)


@router.post("/leads/{lead_id}/stime/{stima_id}", status_code=201)
def link_stima(lead_id: int, stima_id: int, payload: LeadStimaLinkCreate, ctx: OperatorContext = Depends(require_operator)):
    return _translate(service.link_stima, ctx, lead_id, stima_id, payload)


@router.delete("/leads/{lead_id}/stime/{stima_id}", status_code=204)
def unlink_stima(lead_id: int, stima_id: int, ctx: OperatorContext = Depends(require_operator)):
    _translate(service.unlink_stima, ctx, lead_id, stima_id)
    return Response(status_code=204)


@router.post("/activities", status_code=201)
def create_activity(payload: ActivityCreate, ctx: OperatorContext = Depends(require_operator)):
    return _translate(service.create_activity, ctx, payload)


@router.delete("/activities/{activity_id}", status_code=204)
def delete_activity(activity_id: int, ctx: OperatorContext = Depends(require_operator)):
    _translate(service.delete_activity, ctx, activity_id)
    return Response(status_code=204)


@router.get("/activities")
def list_activities(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    contact_id: int | None = None,
    lead_id: int | None = None,
    stima_id: int | None = None,
    ctx: OperatorContext = Depends(require_operator),
):
    return {
        "items": _translate(
            service.list_activities, ctx, limit, offset, contact_id, lead_id, stima_id
        )
    }


@router.post("/tasks", status_code=201)
def create_task(payload: TaskCreate, ctx: OperatorContext = Depends(require_operator)):
    return _translate(service.create_task, ctx, payload)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, ctx: OperatorContext = Depends(require_operator)):
    _translate(service.delete_task, ctx, task_id)
    return Response(status_code=204)


@router.get("/tasks")
def list_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    contact_id: int | None = None,
    lead_id: int | None = None,
    stima_id: int | None = None,
    status: str | None = None,
    ctx: OperatorContext = Depends(require_operator),
):
    return {
        "items": _translate(
            service.list_tasks, ctx, limit, offset, contact_id, lead_id, stima_id, status
        )
    }


@router.patch("/tasks/{task_id}")
def update_task(task_id: int, payload: TaskUpdate, ctx: OperatorContext = Depends(require_operator)):
    return _translate(service.update_task, ctx, task_id, payload)
