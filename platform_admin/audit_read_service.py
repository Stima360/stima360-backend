"""ADM-5A - consultazione del registro delle operazioni amministrative.

Sola lettura: la transazione e' `READ ONLY`, quindi una scrittura qui sarebbe
rifiutata dal database. Nessuna riga di audit operativa: l'ammissione alla
superficie ha gia' registrato chi ha letto.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any

from . import audit_read_repository as repo
from .database import platform_operation_cursor


@contextmanager
def _read_only():
    with platform_operation_cursor() as (_, cur):
        cur.execute("SET TRANSACTION READ ONLY")
        yield cur


def list_audit(
    *,
    action: str | None = None,
    result: str | None = None,
    target_agency_id: int | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    with _read_only() as cur:
        return repo.list_entries(
            cur, action=action, result=result, target_agency_id=target_agency_id,
            target_type=target_type, target_id=target_id, since=since, until=until,
            limit=limit, offset=offset,
        )
