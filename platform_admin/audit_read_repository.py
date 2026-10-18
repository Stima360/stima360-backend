"""ADM-5A - la lettura di `platform_audit_log`. SOLO SELECT.

`repository.py` resta il solo punto che scrive il registro e non lo legge: la
lettura sta qui, separata, e il service la apre in una transazione che il
database rende di sola lettura.

Le righe si restituiscono come sono state scritte: id ed etichetta originali di
chi ha agito, nessun ricalcolo dall'anagrafica degli operatori.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .enums import ACTION_ADMISSION

AUDIT_COLUMNS = (
    "id", "created_at", "actor_user_id", "actor_label", "action", "result",
    "target_type", "target_id", "target_agency_id", "metadata",
)


def list_entries(
    cur,
    *,
    action: str | None,
    result: str | None,
    target_agency_id: int | None,
    target_type: str | None,
    target_id: str | None,
    since: datetime | None,
    until: datetime | None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Le righe dal piu' recente. Senza un filtro `action` esplicito le
    ammissioni (`platform.admission`, una per richiesta) restano fuori."""
    where, params = [], []
    if action is None:
        where.append("action <> %s")
        params.append(ACTION_ADMISSION)
    else:
        where.append("action = %s")
        params.append(action)
    for column, value in (("result", result), ("target_agency_id", target_agency_id),
                          ("target_type", target_type), ("target_id", target_id)):
        if value is not None:
            where.append(f"{column} = %s")
            params.append(value)
    if since is not None:
        where.append("created_at >= %s")
        params.append(since)
    if until is not None:
        where.append("created_at < %s")
        params.append(until)
    cur.execute(
        f"""SELECT {", ".join(AUDIT_COLUMNS)}
              FROM platform_audit_log
             WHERE {" AND ".join(where)}
             ORDER BY created_at DESC, id DESC
             LIMIT %s OFFSET %s""",
        (*params, limit, offset),
    )
    return [dict(row) for row in cur.fetchall()]
