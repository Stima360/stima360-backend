"""FastAPI router for STIMA360 CORE CRM."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response

from . import service
from .exceptions import ConflictError, NotFoundError, ValidationError
from .schemas import (
    ActivityCreate,
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


def _translate(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/contacts", status_code=201)
def create_contact(payload: ContactCreate):
    return _translate(service.create_contact, payload)


@router.get("/contacts")
def list_contacts(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    status: str | None = None,
):
    return {"items": _translate(service.list_contacts, limit, offset, search, status)}


@router.get("/contacts/{contact_id}")
def get_contact(contact_id: int):
    return _translate(service.get_contact, contact_id)


@router.patch("/contacts/{contact_id}")
def update_contact(contact_id: int, payload: ContactUpdate):
    return _translate(service.update_contact, contact_id, payload)


@router.post("/contacts/{contact_id}/roles", status_code=201)
def add_contact_role(contact_id: int, payload: ContactRoleCreate):
    return _translate(service.add_contact_role, contact_id, payload)


@router.delete("/contacts/{contact_id}/roles/{role}", status_code=204)
def delete_contact_role(contact_id: int, role: str):
    _translate(service.delete_contact_role, contact_id, role)
    return Response(status_code=204)


@router.post("/leads", status_code=201)
def create_lead(payload: LeadCreate):
    return _translate(service.create_lead, payload)


@router.get("/leads")
def list_leads(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    contact_id: int | None = None,
    pipeline: str | None = None,
    stage: str | None = None,
    status: str | None = None,
):
    return {
        "items": _translate(
            service.list_leads, limit, offset, contact_id, pipeline, stage, status
        )
    }


@router.get("/leads/{lead_id}")
def get_lead(lead_id: int):
    return _translate(service.get_lead, lead_id)


@router.patch("/leads/{lead_id}")
def update_lead(lead_id: int, payload: LeadUpdate):
    return _translate(service.update_lead, lead_id, payload)


@router.post("/leads/{lead_id}/stime/{stima_id}", status_code=201)
def link_stima(lead_id: int, stima_id: int, payload: LeadStimaLinkCreate):
    return _translate(service.link_stima, lead_id, stima_id, payload)


@router.delete("/leads/{lead_id}/stime/{stima_id}", status_code=204)
def unlink_stima(lead_id: int, stima_id: int):
    _translate(service.unlink_stima, lead_id, stima_id)
    return Response(status_code=204)


@router.post("/activities", status_code=201)
def create_activity(payload: ActivityCreate):
    return _translate(service.create_activity, payload)


@router.get("/activities")
def list_activities(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    contact_id: int | None = None,
    lead_id: int | None = None,
    stima_id: int | None = None,
):
    return {
        "items": _translate(
            service.list_activities, limit, offset, contact_id, lead_id, stima_id
        )
    }


@router.post("/tasks", status_code=201)
def create_task(payload: TaskCreate):
    return _translate(service.create_task, payload)


@router.get("/tasks")
def list_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    contact_id: int | None = None,
    lead_id: int | None = None,
    stima_id: int | None = None,
    status: str | None = None,
):
    return {
        "items": _translate(
            service.list_tasks, limit, offset, contact_id, lead_id, stima_id, status
        )
    }


@router.patch("/tasks/{task_id}")
def update_task(task_id: int, payload: TaskUpdate):
    return _translate(service.update_task, task_id, payload)
