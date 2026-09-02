"""Raw SQL / CORE-orchestration repository for the P18 Follow-up module.

No rule logic lives here (see followup/service.py for that) - this module's
only job is: gate on idempotency, create the CORE task, record the
outcome. It uses its own local transactions (followup_cursor), never the
one used for the stima INSERT, the CORE bridge, or seller_intelligence -
see the module docstring in followup/database.py for why.

Idempotency has two independent layers, deliberately mirroring the pattern
FLOW already uses in production (flow/repository.py::execute_live):

1. followup_actions.idempotency_key UNIQUE (hard DB-level guarantee): the
   INSERT below uses ON CONFLICT (idempotency_key) DO NOTHING RETURNING *,
   so two concurrent/duplicated calls for the same key can never both
   proceed to create a task.
2. A best-effort tasks.metadata->>'idempotency_key' lookup before
   inserting the CORE task, for compatibility with the same convention
   FLOW already writes into CORE task metadata - not the authoritative
   gate (layer 1 is), just a second line of defense consistent with the
   rest of the codebase.

Deliberately THREE separate small transactions, not one:
  1. INSERT the 'pending' followup_actions row and COMMIT it immediately.
  2. Attempt the CORE task creation and mark 'completed', in its own
     transaction.
  3. Only if step 2 fails: a best-effort separate transaction that marks
     the same row 'failed' with the error, then re-raises the original
     exception.
This is what makes "registra failed se possibile" actually possible: if
everything were one transaction, a failure in step 2 would roll back step
1 too, and no failure record would ever survive to be inspected later.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from psycopg2.extras import Json

from core import repository as core_repository

from .database import followup_cursor
from .exceptions import ConflictError


def _row(row):
    return dict(row) if row else None


def _find_task_by_idempotency_key(cur, idempotency_key: str) -> int | None:
    """Best-effort duplicate check against CORE tasks.metadata, mirroring
    the exact lookup flow/repository.py already performs before creating a
    CORE task. Not the authoritative gate - see module docstring."""
    cur.execute(
        "SELECT id FROM tasks WHERE metadata->>'idempotency_key' = %s ORDER BY id LIMIT 1",
        (idempotency_key,),
    )
    row = cur.fetchone()
    return row["id"] if row else None


def _insert_pending_action(
    *,
    rule_code: str,
    trigger_type: str,
    idempotency_key: str,
    contact_id: int | None,
    lead_id: int | None,
    stima_id: int | None,
) -> dict[str, Any]:
    """Step 1: insert (or find) the followup_actions row for this key.

    Returns the row dict plus a "created" bool: True if this call inserted
    a brand new 'pending' row, False if a row for this idempotency_key was
    already there (any status).
    """
    with followup_cursor(commit=True) as (_, cur):
        cur.execute(
            """
            INSERT INTO followup_actions (
                rule_code, trigger_type, contact_id, lead_id, stima_id,
                idempotency_key, status
            ) VALUES (
                %(rule_code)s, %(trigger_type)s, %(contact_id)s, %(lead_id)s,
                %(stima_id)s, %(idempotency_key)s, 'pending'
            )
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING *
            """,
            {
                "rule_code": rule_code,
                "trigger_type": trigger_type,
                "contact_id": contact_id,
                "lead_id": lead_id,
                "stima_id": stima_id,
                "idempotency_key": idempotency_key,
            },
        )
        row = _row(cur.fetchone())
        if row is not None:
            return {**row, "_created": True}

        cur.execute(
            "SELECT * FROM followup_actions WHERE idempotency_key = %s",
            (idempotency_key,),
        )
        existing = _row(cur.fetchone())
        if existing is None:
            raise RuntimeError(
                f"followup_actions insert conflicted on idempotency_key="
                f"{idempotency_key!r} but no existing row was found"
            )
        return {**existing, "_created": False}


def _mark_failed_best_effort(action_id: int, error: Exception) -> None:
    """Step 3: best-effort only. If this itself fails, it must not mask
    the original error - the caller re-raises regardless."""
    try:
        with followup_cursor(commit=True) as (_, cur):
            cur.execute(
                """
                UPDATE followup_actions
                SET status = 'failed', error_message = %s
                WHERE id = %s AND status = 'pending'
                """,
                (str(error)[:2000], action_id),
            )
    except Exception:
        pass


def execute_followup_action(
    *,
    rule_code: str,
    trigger_type: str,
    idempotency_key: str,
    contact_id: int | None,
    lead_id: int | None,
    stima_id: int | None,
    task_title: str,
    task_description: str | None,
    task_type: str,
    priority: str,
    due_at,
    created_by: str | None,
) -> dict[str, Any]:
    """Create one CORE task for a follow-up rule, idempotently.

    Returns {"task_id": int, "followup_action_id": int,
    "status": "completed" | "already_completed"}.

    Raises on any real failure (ConflictError for an ambiguous prior
    pending/failed attempt, or whatever
    core_repository.create_task_with_cursor raises, e.g. NotFoundError for
    a bad reference) - this function never swallows anything itself. That
    is safe_run_followup()'s job, one layer up in service.py.
    """
    action = _insert_pending_action(
        rule_code=rule_code,
        trigger_type=trigger_type,
        idempotency_key=idempotency_key,
        contact_id=contact_id,
        lead_id=lead_id,
        stima_id=stima_id,
    )

    if not action["_created"]:
        if action["status"] == "completed":
            return {
                "task_id": action["task_id"],
                "followup_action_id": action["id"],
                "status": "already_completed",
            }
        # 'pending' or 'failed': deliberately not auto-retried in P18-B -
        # see followup/exceptions.py::ConflictError.
        raise ConflictError(
            f"followup_actions {idempotency_key!r} already exists with "
            f"status={action['status']!r} (id={action['id']}) - not "
            "retrying automatically"
        )

    task_metadata = {
        "source": "followup",
        "rule_code": rule_code,
        "idempotency_key": idempotency_key,
        "trigger_type": trigger_type,
    }

    try:
        with followup_cursor(commit=True) as (_, cur):
            existing_task_id = _find_task_by_idempotency_key(cur, idempotency_key)
            if existing_task_id is not None:
                task = {"id": existing_task_id}
            else:
                task = core_repository.create_task_with_cursor(
                    cur,
                    {
                        "contact_id": contact_id,
                        "lead_id": lead_id,
                        "stima_id": stima_id,
                        "title": task_title,
                        "description": task_description,
                        "task_type": task_type,
                        "priority": priority,
                        "status": "open",
                        "due_at": due_at,
                        "completed_at": None,
                        "assigned_to": None,
                        "created_by": created_by or "FOLLOWUP",
                        "metadata": task_metadata,
                    },
                )
            cur.execute(
                """
                UPDATE followup_actions
                SET status = 'completed', task_id = %s, error_message = NULL
                WHERE id = %s
                """,
                (task["id"], action["id"]),
            )
    except Exception as exc:
        _mark_failed_best_effort(action["id"], exc)
        raise

    return {
        "task_id": task["id"],
        "followup_action_id": action["id"],
        "status": "completed",
    }


def list_temporal_escalation_candidates(*, limit: int, rule_code: str) -> list[dict[str, Any]]:
    with followup_cursor() as (_, cur):
        cur.execute(
            """
            SELECT
                t.id,
                t.contact_id,
                t.lead_id,
                t.stima_id,
                t.priority,
                t.metadata
            FROM tasks t
            LEFT JOIN leads l ON l.id = t.lead_id
            WHERE t.status = 'open'
              AND t.priority IN ('low', 'normal')
              AND t.task_type = 'automated_followup'
              AND t.title = 'Contattare proprietario'
              AND t.due_at IS NOT NULL
              AND t.due_at <= NOW() - INTERVAL '24 hours'
              AND COALESCE(t.metadata->>'source', '') = 'followup'
              AND COALESCE(t.metadata->>'rule_code', '') = 'FOLLOWUP_STIMA_RICHIESTA'
              AND (
                    t.lead_id IS NULL
                    OR (
                        l.status NOT IN ('closed', 'paused')
                        AND l.stage = 'new'
                    )
              )
              AND NOT EXISTS (
                    SELECT 1
                    FROM followup_actions fa
                    WHERE fa.idempotency_key = (
                        'followup:time:' || %s || ':task:' || t.id::text || ':v1'
                    )
                    AND fa.status = 'completed'
              )
            ORDER BY t.due_at ASC, t.id ASC
            LIMIT %s
            """,
            (rule_code, limit),
        )
        return [dict(row) for row in cur.fetchall()]


def execute_temporal_escalation(
    *,
    rule_code: str,
    trigger_type: str,
    task_id: int,
    contact_id: int | None,
    lead_id: int | None,
    stima_id: int | None,
    idempotency_key: str,
    created_by: str,
) -> dict[str, Any]:
    action = _insert_pending_action(
        rule_code=rule_code,
        trigger_type=trigger_type,
        idempotency_key=idempotency_key,
        contact_id=contact_id,
        lead_id=lead_id,
        stima_id=stima_id,
    )
    if not action["_created"]:
        if action["status"] == "completed":
            return {
                "task_id": action["task_id"],
                "followup_action_id": action["id"],
                "status": "already_completed",
            }
        raise ConflictError(
            f"followup_actions {idempotency_key!r} already exists with "
            f"status={action['status']!r} (id={action['id']}) - not "
            "retrying automatically"
        )

    try:
        with followup_cursor(commit=True) as (_, cur):
            cur.execute("SELECT id, priority, metadata FROM tasks WHERE id = %s FOR UPDATE", (task_id,))
            task = _row(cur.fetchone())
            if task is None:
                raise ConflictError(f"task {task_id} not found")

            previous_priority = task["priority"]
            if previous_priority not in {"low", "normal"}:
                raise ConflictError(f"task {task_id} is not eligible for escalation")

            temporal_metadata = {
                rule_code: {
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                    "applied_by": created_by,
                    "reason": "task_overdue_24h_after_due_at",
                    "previous_priority": previous_priority,
                    "new_priority": "high",
                    "idempotency_key": idempotency_key,
                }
            }
            cur.execute(
                """
                UPDATE tasks t
                SET priority = 'high',
                    metadata = jsonb_set(
                        COALESCE(t.metadata, '{}'::jsonb),
                        '{temporal_escalations}',
                        (
                            CASE
                                WHEN jsonb_typeof(COALESCE(t.metadata, '{}'::jsonb)->'temporal_escalations') = 'object'
                                THEN COALESCE(t.metadata, '{}'::jsonb)->'temporal_escalations'
                                ELSE '{}'::jsonb
                            END
                        ) || %s::jsonb,
                        true
                    ),
                    updated_at = NOW()
                WHERE t.id = %s
                  AND t.status = 'open'
                  AND t.priority IN ('low', 'normal')
                  AND t.task_type = 'automated_followup'
                  AND t.title = 'Contattare proprietario'
                  AND t.due_at IS NOT NULL
                  AND t.due_at <= NOW() - INTERVAL '24 hours'
                  AND COALESCE(t.metadata->>'source', '') = 'followup'
                  AND COALESCE(t.metadata->>'rule_code', '') = 'FOLLOWUP_STIMA_RICHIESTA'
                  AND (
                        t.lead_id IS NULL
                        OR (
                            EXISTS (
                                SELECT 1
                                FROM leads l
                                WHERE l.id = t.lead_id
                                  AND l.status NOT IN ('closed', 'paused')
                                  AND l.stage = 'new'
                            )
                        )
                  )
                RETURNING t.id
                """,
                (Json(temporal_metadata), task_id),
            )
            updated = cur.fetchone()
            if not updated:
                raise ConflictError(f"task {task_id} no longer eligible for escalation")

            cur.execute(
                """
                UPDATE followup_actions
                SET status = 'completed', task_id = %s, error_message = NULL
                WHERE id = %s
                """,
                (task_id, action["id"]),
            )
    except Exception as exc:
        _mark_failed_best_effort(action["id"], exc)
        raise

    return {
        "task_id": task_id,
        "followup_action_id": action["id"],
        "status": "completed",
    }
