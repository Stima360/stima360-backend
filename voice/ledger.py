"""STIMA Voice Fase 3 - il registro dei comandi vocali (migration 098).

Lo stesso contratto della saga `public_submissions.Receipt` - valori
congelati una volta sola, passi con checkpoint persistenti, ripresa che
salta cio' che e' gia' riuscito, stato `partial` - su tabelle proprie:
riusare `Receipt` vorrebbe dire scrivere nel registro del sito pubblico.

Regole:
  * ogni funzione con un `ctx` lavora SOLO sui comandi della propria agenzia
    E del proprio attore: un altro agente riceve `CommandNotFound`, come per
    un id inesistente;
  * stessa chiave (`client_command_id`) con lo stesso contenuto = lo stesso
    comando; con contenuto diverso = `IdempotencyConflict`. Lo stesso vale
    per il piano: un secondo piano diverso sullo stesso comando e' rifiutato;
  * un passo si esegue solo se: il piano e' registrato, la decisione e'
    `auto` (oppure `ask` con conferma esplicita), i passi da cui dipende sono
    riusciti, e il passo e' `pending`. `blocked` non si esegue mai;
  * l'esecuzione di un comando e' esclusiva: un lock consultivo di sessione
    per comando (`executing`); un secondo esecutore riceve `CommandBusy`;
  * dopo un crash un passo rimasto `running`:
      - `replayable` (il servizio CRM ha una chiave di idempotenza) torna
        `pending` e si ripete con la STESSA chiave congelata;
      - `at_most_once` (nessuna chiave nel CRM) diventa `indeterminate` e non
        si ripete mai da solo;
  * i trascritti scadono e si cancellano (`purge_expired_transcripts`),
    senza che nulla qui lo pianifichi: l'attivazione e' fuori dalla Fase 3.

Le scritture qui toccano SOLO le tabelle `voice_*`. Nessun servizio del CRM
e' chiamato da questo modulo: l'operazione di un passo la passa il chiamante
(Fase 4).
"""
from __future__ import annotations

import hashlib
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Callable

from psycopg2.extras import Json

from core import database as core_database
from core.database import core_cursor

from .planner import Plan
from .policy import PlanDecision

#: La classe di ogni intento: dipende SOLO da cosa offre il servizio del CRM
#: che lo eseguira'. Con una chiave di idempotenza nativa = ripetibile.
STEP_CLASSES = {
    "create_unit": "replayable",          # census.create_unit: client_request_id
    "create_building": "replayable",      # census.create_building: client_request_id
    "create_appointment": "replayable",   # appointments: client_request_id
    "activate_seller": "replayable",      # crm.sellers.activate: riuso sulla coppia
    "create_contact": "at_most_once",     # core: nessuna chiave
    "add_note": "at_most_once",
    "add_task": "at_most_once",
    "link_owner": "at_most_once",
    "unsupported": "at_most_once",        # mai eseguito: sempre bloccato
}

TERMINAL_COMMAND = ("completed", "failed", "cancelled")
RUNNABLE_COMMAND = ("planned", "awaiting_answers", "executing", "partial")
UNCERTAIN_ERRORS: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError)
try:  # gli errori di connessione al database: commit forse avvenuto, forse no
    import psycopg2
    UNCERTAIN_ERRORS += (psycopg2.OperationalError, psycopg2.InterfaceError)
except ImportError:  # pragma: no cover
    pass


class LedgerError(Exception):
    code = "VOICE_LEDGER_ERROR"


class IdempotencyConflict(LedgerError):
    code = "VOICE_IDEMPOTENCY_KEY_REUSED"


class CommandNotFound(LedgerError):
    code = "VOICE_COMMAND_NOT_FOUND"


class CommandBusy(LedgerError):
    code = "VOICE_COMMAND_BUSY"


class InvalidTransition(LedgerError):
    code = "VOICE_INVALID_TRANSITION"


class StepNotAllowed(LedgerError):
    code = "VOICE_STEP_NOT_ALLOWED"


class ActorRequired(LedgerError):
    code = "VOICE_ACTOR_REQUIRED"


# ---------------------------------------------------------------------------
# Identita'
# ---------------------------------------------------------------------------

def request_fingerprint(input_kind: str, content: bytes | str) -> str:
    """L'impronta della richiesta: tipo di ingresso + contenuto (byte
    dell'audio o testo). Stessa chiave con impronta diversa = conflitto."""
    if input_kind not in ("audio", "text"):
        raise ValueError("input_kind must be audio or text")
    dati = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    return hashlib.sha256(input_kind.encode() + b"\x00" + dati).hexdigest()


def _scope(ctx) -> tuple[int, int]:
    agency_id = ctx.require_agency()
    user_id = getattr(ctx, "user_id", None)
    if user_id is None:
        raise ActorRequired("un comando vocale ha sempre un operatore")
    return agency_id, int(user_id)


def _uuid(value) -> str:
    return str(uuid.UUID(str(value)))


def _json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _event(cur, agency_id, command_id, event_type, actor_user_id, ordinal=None, detail=None) -> None:
    cur.execute("INSERT INTO voice_command_events (agency_id, command_id, ordinal, event_type, actor_user_id, detail) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (agency_id, command_id, ordinal, event_type, actor_user_id, Json(_json(detail or {}))))


def _command(cur, agency_id, actor, command_id, *, lock=False) -> dict:
    cur.execute("SELECT * FROM voice_commands WHERE id = %s AND agency_id = %s AND actor_user_id = %s"
                + (" FOR UPDATE" if lock else ""), (command_id, agency_id, actor))
    riga = cur.fetchone()
    if riga is None:
        raise CommandNotFound("comando vocale non trovato")
    return dict(riga)


def _steps(cur, command_id) -> list[dict]:
    cur.execute("SELECT * FROM voice_command_steps WHERE command_id = %s ORDER BY ordinal", (command_id,))
    return [dict(r) for r in cur.fetchall()]


def _set_status(cur, comando: dict, nuovo: str, actor) -> None:
    if comando["status"] == nuovo:
        return
    cur.execute("UPDATE voice_commands SET status = %s, completed_at = CASE WHEN %s THEN NOW() END WHERE id = %s",
                (nuovo, nuovo in TERMINAL_COMMAND, comando["id"]))
    _event(cur, comando["agency_id"], comando["id"], "status_changed", actor,
           detail={"from": comando["status"], "to": nuovo})
    comando["status"] = nuovo


# ---------------------------------------------------------------------------
# Apertura e piano
# ---------------------------------------------------------------------------

def open_command(ctx, client_command_id, *, input_kind: str, request_sha256: str,
                 recorded_at: datetime) -> tuple[dict, bool]:
    """Il comando per questa chiave: creato ora (`True`) o gia' esistente con
    la stessa impronta (`False`). Impronta diversa = `IdempotencyConflict`.
    Due richieste concorrenti con la stessa chiave si mettono in fila sul
    lock consultivo: una crea, l'altra ritrova."""
    agency_id, actor = _scope(ctx)
    chiave = _uuid(client_command_id)
    if recorded_at.tzinfo is None:
        raise ValueError("recorded_at must be timezone-aware")
    with core_cursor(commit=True) as (_, cur):
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"voice:command:{agency_id}:{actor}:{chiave}",))
        cur.execute("SELECT * FROM voice_commands WHERE agency_id = %s AND actor_user_id = %s AND client_command_id = %s",
                    (agency_id, actor, chiave))
        esistente = cur.fetchone()
        if esistente is not None:
            esistente = dict(esistente)
            if esistente["request_sha256"] != request_sha256 or esistente["input_kind"] != input_kind:
                raise IdempotencyConflict("questa richiesta risulta gia' inviata con un contenuto diverso")
            _event(cur, agency_id, esistente["id"], "replayed", actor)
            return esistente, False
        cur.execute("INSERT INTO voice_commands (agency_id, actor_user_id, client_command_id, request_sha256, input_kind, "
                    "recorded_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
                    (agency_id, actor, chiave, request_sha256, input_kind, recorded_at))
        riga = dict(cur.fetchone())
        _event(cur, agency_id, riga["id"], "received", actor, detail={"input_kind": input_kind})
        return riga, True


def serialize_plan(plan: Plan) -> dict:
    """Il piano come JSON: cio' che verra' eseguito, senza il trascritto."""
    return {
        "recorded_at": plan.recorded_at.isoformat(),
        "fingerprint": plan.fingerprint,
        "clarifications": list(plan.clarifications),
        "steps": [{
            "ordinal": s.ordinal, "intent": s.intent, "quote": s.quote, "payload": _json(s.payload),
            "refs": {k: {"kind": r.kind, "step": r.step, "description": _json(r.description)} for k, r in s.refs.items()},
            "depends_on": list(s.depends_on), "issues": [i.code for i in s.issues],
            "start_at": s.start_at.isoformat() if s.start_at else None,
            "end_at": s.end_at.isoformat() if s.end_at else None,
        } for s in plan.steps],
    }


def record_plan(ctx, command_id: int, plan: Plan, decision: PlanDecision, *, mode: str,
                retention_days: int) -> dict:
    """Registra trascritto, piano, decisioni e passi. Idempotente sullo
    stesso piano (stessa impronta); un piano diverso e' un conflitto. Le
    chiavi dei passi ripetibili nascono QUI, una volta sola, e si congelano."""
    agency_id, actor = _scope(ctx)
    if len(decision.steps) != len(plan.steps):
        raise ValueError("decision does not match plan")
    if not 1 <= int(retention_days) <= 365:
        raise ValueError("retention_days out of range")
    with core_cursor(commit=True) as (_, cur):
        comando = _command(cur, agency_id, actor, command_id, lock=True)
        if comando["plan_sha256"] is not None:
            if comando["plan_sha256"] != plan.fingerprint:
                raise IdempotencyConflict("questo comando ha gia' un piano diverso")
            return {**comando, "steps": _steps(cur, command_id)}
        if comando["status"] != "received":
            raise InvalidTransition(f"stato {comando['status']}: il piano non si registra")
        frozen = dict(comando["frozen"])
        for s in plan.steps:
            if STEP_CLASSES[s.intent] == "replayable":
                frozen.setdefault(f"step:{s.ordinal}:client_request_id", str(uuid.uuid4()))
        stato = "awaiting_answers" if decision.asks else "planned"
        cur.execute("UPDATE voice_commands SET transcript = %s, transcript_expires_at = NOW() + make_interval(days => %s), "
                    "plan = %s, plan_sha256 = %s, mode = %s, frozen = %s, status = %s WHERE id = %s",
                    (plan.transcript, int(retention_days), Json(serialize_plan(plan)), plan.fingerprint, mode,
                     Json(frozen), stato, command_id))
        for s, d in zip(plan.steps, decision.steps):
            classe = STEP_CLASSES[s.intent]
            cur.execute("INSERT INTO voice_command_steps (command_id, ordinal, agency_id, intent, decision, reasons, "
                        "step_class, client_request_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (command_id, s.ordinal, agency_id, s.intent, d.decision, list(d.reasons), classe,
                         frozen.get(f"step:{s.ordinal}:client_request_id")))
            if d.decision == "blocked":
                cur.execute("UPDATE voice_command_steps SET state = 'skipped', error_type = 'blocked', finished_at = NOW() "
                            "WHERE command_id = %s AND ordinal = %s", (command_id, s.ordinal))
                _event(cur, agency_id, command_id, "step_skipped", actor, s.ordinal, {"reasons": list(d.reasons)})
        _event(cur, agency_id, command_id, "planned", actor,
               detail={"steps": len(plan.steps), "auto": len(decision.auto), "ask": len(decision.asks),
                       "blocked": len(decision.blocked), "mode": mode})
        return {**_command(cur, agency_id, actor, command_id), "steps": _steps(cur, command_id)}


def get_command(ctx, command_id: int) -> dict:
    agency_id, actor = _scope(ctx)
    with core_cursor() as (conn, cur):
        comando = _command(cur, agency_id, actor, command_id)
        comando["steps"] = _steps(cur, command_id)
        conn.rollback()
        return comando


def freeze(ctx, command_id: int, name: str, factory: Callable[[], Any]) -> Any:
    """Come `Receipt.frozen`: il valore si calcola UNA volta e poi si rilegge,
    anche dopo un crash. Il database rifiuta la riscrittura."""
    agency_id, actor = _scope(ctx)
    with core_cursor(commit=True) as (_, cur):
        comando = _command(cur, agency_id, actor, command_id, lock=True)
        if name in comando["frozen"]:
            return comando["frozen"][name]
        valore = _json(factory())
        cur.execute("UPDATE voice_commands SET frozen = frozen || %s WHERE id = %s", (Json({name: valore}), command_id))
        return valore


# ---------------------------------------------------------------------------
# Esecuzione
# ---------------------------------------------------------------------------

@contextmanager
def executing(ctx, command_id: int):
    """Esecuzione esclusiva di un comando: lock consultivo di SESSIONE su una
    connessione dedicata, rilasciato a fine blocco o alla morte della
    connessione (crash). Chi non lo ottiene riceve `CommandBusy`. Appena
    ottenuto, i passi rimasti `running` da un esecutore morto si riprendono."""
    agency_id, actor = _scope(ctx)
    get_command(ctx, command_id)  # esiste ed e' di chi chiede, prima di qualunque lock
    conn = core_database.get_connection()  # letta al momento: i test la sostituiscono
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (f"voice:execute:{command_id}",))
            if not cur.fetchone()[0]:
                raise CommandBusy("questo comando e' gia' in esecuzione")
        try:
            recover(ctx, command_id)
            yield
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (f"voice:execute:{command_id}",))
    finally:
        conn.close()


def recover(ctx, command_id: int) -> list[int]:
    """I passi `running` lasciati da un esecutore morto. Va chiamata SOLO da
    chi tiene il lock di esecuzione (`executing` lo fa)."""
    agency_id, actor = _scope(ctx)
    ripresi = []
    with core_cursor(commit=True) as (_, cur):
        _command(cur, agency_id, actor, command_id, lock=True)
        cur.execute("SELECT ordinal, step_class FROM voice_command_steps WHERE command_id = %s AND state = 'running' "
                    "ORDER BY ordinal FOR UPDATE", (command_id,))
        for riga in cur.fetchall():
            if riga["step_class"] == "replayable":
                cur.execute("UPDATE voice_command_steps SET state = 'pending' WHERE command_id = %s AND ordinal = %s",
                            (command_id, riga["ordinal"]))
                _event(cur, agency_id, command_id, "step_recovered", actor, riga["ordinal"])
            else:
                cur.execute("UPDATE voice_command_steps SET state = 'indeterminate', error_type = 'interrupted', "
                            "finished_at = NOW() WHERE command_id = %s AND ordinal = %s", (command_id, riga["ordinal"]))
                _event(cur, agency_id, command_id, "step_indeterminate", actor, riga["ordinal"],
                       {"error_type": "interrupted"})
            ripresi.append(riga["ordinal"])
    return ripresi


def begin_step(ctx, command_id: int, ordinal: int, *, confirmed: bool = False) -> dict:
    agency_id, actor = _scope(ctx)
    with core_cursor(commit=True) as (_, cur):
        comando = _command(cur, agency_id, actor, command_id, lock=True)
        if comando["status"] not in RUNNABLE_COMMAND or comando["plan_sha256"] is None:
            raise InvalidTransition(f"stato {comando['status']}: nessun passo eseguibile")
        passi = {p["ordinal"]: p for p in _steps(cur, command_id)}
        passo = passi.get(ordinal)
        if passo is None:
            raise CommandNotFound("passo non trovato")
        if passo["decision"] == "blocked":
            raise StepNotAllowed("passo bloccato: non si esegue")
        if passo["decision"] == "ask" and not confirmed:
            raise StepNotAllowed("passo in attesa di conferma")
        dipendenze = next(s for s in comando["plan"]["steps"] if s["ordinal"] == ordinal)["depends_on"]
        mancanti = [d for d in dipendenze if passi[d]["state"] != "succeeded"]
        if mancanti:
            raise StepNotAllowed(f"dipende da passi non riusciti: {mancanti}")
        cur.execute("UPDATE voice_command_steps SET state = 'running', attempts = attempts + 1, started_at = NOW(), "
                    "error_type = NULL WHERE command_id = %s AND ordinal = %s AND state = 'pending' RETURNING *",
                    (command_id, ordinal))
        riga = cur.fetchone()
        if riga is None:
            raise InvalidTransition(f"passo {ordinal} in stato {passo['state']}: non si avvia")
        _set_status(cur, comando, "executing", actor)
        _event(cur, agency_id, command_id, "step_running", actor, ordinal, {"attempt": riga["attempts"]})
        return dict(riga)


def finish_step(ctx, command_id: int, ordinal: int, result: dict) -> dict:
    agency_id, actor = _scope(ctx)
    with core_cursor(commit=True) as (_, cur):
        _command(cur, agency_id, actor, command_id, lock=True)
        cur.execute("UPDATE voice_command_steps SET state = 'succeeded', result = %s, finished_at = NOW() "
                    "WHERE command_id = %s AND ordinal = %s AND state = 'running' RETURNING *",
                    (Json(_json(result or {})), command_id, ordinal))
        riga = cur.fetchone()
        if riga is None:
            raise InvalidTransition(f"passo {ordinal} non in esecuzione")
        _event(cur, agency_id, command_id, "step_succeeded", actor, ordinal)
        return dict(riga)


def fail_step(ctx, command_id: int, ordinal: int, *, error_type: str, uncertain: bool = False) -> dict:
    """Un errore SICURO (il servizio ha annullato la sua transazione) rende il
    passo `failed`, ripetibile. Un errore INCERTO (connessione caduta: il
    commit forse c'e' stato) su un passo `at_most_once` lo rende
    `indeterminate`: non si ripete senza una verifica."""
    agency_id, actor = _scope(ctx)
    with core_cursor(commit=True) as (_, cur):
        _command(cur, agency_id, actor, command_id, lock=True)
        cur.execute("SELECT step_class FROM voice_command_steps WHERE command_id = %s AND ordinal = %s",
                    (command_id, ordinal))
        trovato = cur.fetchone()
        if trovato is None:
            raise CommandNotFound("passo non trovato")
        stato = "indeterminate" if uncertain and trovato["step_class"] == "at_most_once" else "failed"
        cur.execute("UPDATE voice_command_steps SET state = %s, error_type = %s, finished_at = NOW() "
                    "WHERE command_id = %s AND ordinal = %s AND state = 'running' RETURNING *",
                    (stato, error_type[:60], command_id, ordinal))
        riga = cur.fetchone()
        if riga is None:
            raise InvalidTransition(f"passo {ordinal} non in esecuzione")
        _event(cur, agency_id, command_id, "step_failed" if stato == "failed" else "step_indeterminate", actor,
               ordinal, {"error_type": error_type[:60]})
        return dict(riga)


def run_step(ctx, command_id: int, ordinal: int, operation: Callable[[dict], dict], *,
             confirmed: bool = False) -> dict:
    """begin -> operazione (FUORI da ogni transazione del registro) -> esito.
    Un crash fra l'operazione e l'esito lascia il passo `running`: lo
    riprende `recover` al prossimo `executing`."""
    passo = begin_step(ctx, command_id, ordinal, confirmed=confirmed)
    try:
        risultato = operation(passo)
    except UNCERTAIN_ERRORS as exc:
        fail_step(ctx, command_id, ordinal, error_type=type(exc).__name__, uncertain=True)
        raise
    except Exception as exc:
        fail_step(ctx, command_id, ordinal, error_type=type(exc).__name__)
        raise
    return finish_step(ctx, command_id, ordinal, risultato)


def retry_step(ctx, command_id: int, ordinal: int) -> dict:
    """Un passo `failed` torna `pending` (stessa chiave congelata)."""
    agency_id, actor = _scope(ctx)
    with core_cursor(commit=True) as (_, cur):
        _command(cur, agency_id, actor, command_id, lock=True)
        cur.execute("UPDATE voice_command_steps SET state = 'pending' WHERE command_id = %s AND ordinal = %s "
                    "AND state = 'failed' RETURNING *", (command_id, ordinal))
        riga = cur.fetchone()
        if riga is None:
            raise InvalidTransition(f"passo {ordinal} non fallito")
        _event(cur, agency_id, command_id, "step_recovered", actor, ordinal, {"retry": True})
        return dict(riga)


def finalize(ctx, command_id: int) -> str:
    """`completed` se ogni passo e' riuscito o saltato, altrimenti `partial`."""
    agency_id, actor = _scope(ctx)
    with core_cursor(commit=True) as (_, cur):
        comando = _command(cur, agency_id, actor, command_id, lock=True)
        if comando["status"] != "executing":
            return comando["status"]
        stati = {p["state"] for p in _steps(cur, command_id)}
        _set_status(cur, comando, "completed" if stati <= {"succeeded", "skipped"} else "partial", actor)
        return comando["status"]


def cancel(ctx, command_id: int) -> str:
    agency_id, actor = _scope(ctx)
    with core_cursor(commit=True) as (_, cur):
        comando = _command(cur, agency_id, actor, command_id, lock=True)
        if comando["status"] == "cancelled":
            return "cancelled"
        if comando["status"] not in ("received", "planned", "awaiting_answers", "partial"):
            raise InvalidTransition(f"stato {comando['status']}: non si annulla")
        cur.execute("UPDATE voice_command_steps SET state = 'skipped', error_type = 'cancelled', finished_at = NOW() "
                    "WHERE command_id = %s AND state IN ('pending', 'failed') RETURNING ordinal", (command_id,))
        for r in cur.fetchall():
            _event(cur, agency_id, command_id, "step_skipped", actor, r["ordinal"], {"reason": "cancelled"})
        _set_status(cur, comando, "cancelled", actor)
        return "cancelled"


# ---------------------------------------------------------------------------
# Trascritti: scadenza e cancellazione
# ---------------------------------------------------------------------------

def purge_expired_transcripts(*, now: datetime | None = None, limit: int = 500) -> int:
    """Cancella i trascritti scaduti (tutte le agenzie). Restano comando,
    piano e storico; il trascritto non torna (guardia della 098). Nessun
    cron la chiama in Fase 3: e' pronta, non attiva."""
    istante = now or datetime.now(timezone.utc)
    if istante.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    with core_cursor(commit=True) as (_, cur):
        cur.execute("UPDATE voice_commands SET transcript = NULL, transcript_purged_at = %s WHERE id IN ("
                    " SELECT id FROM voice_commands WHERE transcript IS NOT NULL AND transcript_expires_at <= %s"
                    " ORDER BY transcript_expires_at LIMIT %s FOR UPDATE SKIP LOCKED) RETURNING id, agency_id",
                    (istante, istante, int(limit)))
        cancellati = [dict(r) for r in cur.fetchall()]
        for r in cancellati:
            _event(cur, r["agency_id"], r["id"], "transcript_purged", None)
        return len(cancellati)
