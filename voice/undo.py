"""STIMA Voice Fase 4 - l'annullamento protetto (D3, D-A, D-B).

Si annulla SOLO cio' che il comando ha creato, SOLO attraverso i percorsi
reversibili che il CRM ha gia', con i permessi di adesso:
  * contatto, unita' commerciale, edificio -> Cestino «creato per errore»;
  * unita' di censimento -> «Annulla» del censimento (`undo_create`, archivia);
  * appuntamento -> annullato con `kind = mistake`;
  * nota (anche d'immobile) e task -> «Segna come creato per errore»;
  * «Vende» creato dal comando -> «Inserito per errore».
Mai una DELETE fisica, mai lo storico cancellato.

Non si offre (stato `not_undoable`):
  * il collegamento proprietario (D-A: l'unico percorso inverso e' una
    DELETE fisica);
  * «Vende» riutilizzato o riaperto: il lead esisteva prima del comando;
  * un record preesistente o attribuito con una riconciliazione umana.

Regole:
  * finestra di 15 minuti dalla CREAZIONE del record, con l'orologio del
    database (istanti assoluti: mezzanotte e ora legale non contano);
  * la finestra non scavalca i blocchi del CRM: il servizio decide;
  * un passo con dipendenti ancora vivi e annullabili non si annulla
    (VOICE_DEPENDENTS_ACTIVE); l'annullamento del comando va in ordine inverso;
  * ogni tentativo si registra in `voice_step_undos` (099, append-only); un
    solo `undone` per passo, anche con richieste concorrenti;
  * l'annullamento e' esclusivo con l'esecuzione (stesso lock del comando).
Non garantito: un'operazione reversibile puo' diventare bloccata da attivita'
successive (un task completato, un incarico, un appuntamento avvenuto).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from psycopg2 import errors as pg_errors
from psycopg2.extras import Json

from appointments import service as agenda
from appointments.schemas import CancelBody
from core import service as core_service
from core.database import core_cursor
from core.schemas import ContactTrash, MistakeMark
from crm import sellers
from crm.schemas import SellerDeactivate
from property import building_lifecycle, census
from property import service as property_service
from property.schemas import PropertyTrash

from . import executor, ledger, operations
from . import repository as voice_repository

WINDOW = timedelta(minutes=15)
REASON = "created_by_mistake"

ENTITY = {"create_contact": "contact", "create_unit": "property", "create_building": "building",
          "create_appointment": "appointment", "add_note": "activity", "add_task": "task",
          "activate_seller": "seller_lead"}
_TABELLA = {"contact": "contacts", "property": "properties", "building": "buildings", "appointment": "appointments",
            "activity": "activities", "task": "tasks", "seller_lead": "leads"}

OWNER_LINK_NOT_UNDOABLE = "VOICE_OWNER_LINK_NOT_UNDOABLE"
PREEXISTING = "VOICE_PREEXISTING_RECORD"
SELLER_NOT_CREATED = "VOICE_SELLER_NOT_CREATED_BY_COMMAND"
RECONCILED = "VOICE_RECONCILED_RECORD"
DEPENDENTS_ACTIVE = "VOICE_DEPENDENTS_ACTIVE"
WINDOW_EXPIRED = "VOICE_UNDO_WINDOW_EXPIRED"
NOT_APPLICABLE = "VOICE_STEP_NOT_EXECUTED"


def _server_now(cur):
    """L'orologio del database (lato server), non quello del client."""
    cur.execute("SELECT clock_timestamp() AS adesso")
    return cur.fetchone()["adesso"]


def _nota(command_id: int, ordinal: int) -> str:
    return f"Annullato con STIMA Voice (comando {command_id}, passo {ordinal})"


def _entita(passo: dict) -> tuple[str | None, int | None]:
    risultato = passo.get("result") or {}
    tipo = ENTITY.get(passo["intent"])
    valore = risultato.get("lead_id") if tipo == "seller_lead" else risultato.get("id")
    return tipo, (int(valore) if valore is not None else None)


def _gia_annullato(cur, tipo: str, entity_id: int) -> bool:
    """Lo stato del record dice gia' «annullato» (crash fra il servizio e la
    registrazione, o un secondo tentativo)."""
    if tipo in ("contact", "building"):
        cur.execute(f"SELECT deleted_at IS NOT NULL AS fatto FROM {_TABELLA[tipo]} WHERE id = %s", (entity_id,))
    elif tipo == "property":
        cur.execute("SELECT (deleted_at IS NOT NULL OR (record_kind = 'census' AND commercial_status = 'archived')) "
                    "AS fatto FROM properties WHERE id = %s", (entity_id,))
    elif tipo == "appointment":
        cur.execute("SELECT (status = 'cancelled' AND cancelled_kind = 'mistake') AS fatto FROM appointments "
                    "WHERE id = %s", (entity_id,))
    elif tipo in ("activity", "task"):
        cur.execute(f"SELECT COALESCE((metadata->>'mistake')::boolean, FALSE) AS fatto FROM {_TABELLA[tipo]} "
                    "WHERE id = %s", (entity_id,))
    elif tipo == "seller_lead":
        cur.execute("SELECT (lost_reason = %s) AS fatto FROM leads WHERE id = %s", (REASON, entity_id))
    else:
        return False
    riga = cur.fetchone()
    return bool(riga and riga["fatto"])


def _creato_alle(cur, tipo: str, entity_id: int):
    cur.execute(f"SELECT created_at FROM {_TABELLA[tipo]} WHERE id = %s", (entity_id,))
    riga = cur.fetchone()
    return None if riga is None else riga["created_at"]


def _registrato_annullato(cur, command_id: int, ordinal: int) -> bool:
    cur.execute("SELECT 1 FROM voice_step_undos WHERE command_id = %s AND ordinal = %s AND outcome = 'undone'",
                (command_id, ordinal))
    return cur.fetchone() is not None


def _valuta(cur, comando: dict, passo: dict, adesso, gia: dict[int, str]) -> tuple[str, str | None]:
    """Lo stato di annullamento di un passo, in sola lettura. `gia`: lo
    stato dei passi successivi (valutati prima, ordine inverso)."""
    if passo["state"] != "succeeded":
        return "not_applicable", NOT_APPLICABLE
    risultato = passo.get("result") or {}
    if passo["intent"] == "link_owner":
        return "not_undoable", OWNER_LINK_NOT_UNDOABLE
    if risultato.get("reconciled"):
        return "not_undoable", RECONCILED
    if passo["intent"] == "activate_seller" and risultato.get("origin") != "created":
        return "not_undoable", SELLER_NOT_CREATED
    if risultato.get("created") is not True:
        return "not_undoable", PREEXISTING
    tipo, entity_id = _entita(passo)
    if tipo is None or entity_id is None:
        return "not_undoable", PREEXISTING
    if _registrato_annullato(cur, comando["id"], passo["ordinal"]) or _gia_annullato(cur, tipo, entity_id):
        return "undone", None
    creato = _creato_alle(cur, tipo, entity_id)
    if creato is None or adesso - creato > WINDOW:
        return "expired", WINDOW_EXPIRED
    piano = {s["ordinal"]: s for s in comando["plan"]["steps"]}
    for n, stato in gia.items():
        if passo["ordinal"] in piano[n]["depends_on"] and stato not in ("undone", "not_undoable", "not_applicable"):
            return "blocked", DEPENDENTS_ACTIVE
    return "undoable", None


def _valuta_tutti(cur, comando: dict) -> dict[int, tuple[str, str | None]]:
    adesso = _server_now(cur)
    esiti: dict[int, tuple[str, str | None]] = {}
    for passo in sorted(comando["steps"], key=lambda p: -p["ordinal"]):
        esiti[passo["ordinal"]] = _valuta(cur, comando, passo, adesso, {n: e[0] for n, e in esiti.items()})
    return esiti


def assess(ctx, command_id: int) -> list[dict[str, Any]]:
    """Per ogni passo: `undoable`, `undone`, `blocked`, `expired`,
    `not_undoable` o `not_applicable`. Solo i primi meritano un «Annulla»."""
    executor.gate(ctx)
    comando = ledger.get_command(ctx, command_id)
    with voice_repository.read_only() as cur:
        stati = _valuta_tutti(cur, comando)
    return [{"ordinal": p["ordinal"], "intent": p["intent"], "status": stati[p["ordinal"]][0],
             "reason": stati[p["ordinal"]][1]} for p in comando["steps"]]


# ---------------------------------------------------------------------------

def _servizio(ctx, comando: dict, passo: dict, tipo: str, entity_id: int) -> None:
    """Il percorso reversibile del CRM per quel record. Le eccezioni del CRM
    (blocchi, permessi) risalgono: decide il servizio."""
    nota = _nota(comando["id"], passo["ordinal"])
    if tipo == "contact":
        core_service.trash_contact(ctx, entity_id, ContactTrash(reason_code=REASON, note=nota))
    elif tipo == "property":
        if (passo.get("result") or {}).get("record_kind") == "census":
            census.undo_create(ctx, entity_id)
        else:
            property_service.trash_property(ctx, entity_id, PropertyTrash(reason_code=REASON, note=nota))
    elif tipo == "building":
        building_lifecycle.trash_building(ctx, entity_id, REASON, nota)
    elif tipo == "appointment":
        with voice_repository.read_only() as cur:
            cur.execute("SELECT version FROM appointments WHERE id = %s AND agency_id = %s",
                        (entity_id, ctx.require_agency()))
            riga = cur.fetchone()
        versione = int(riga["version"]) if riga else 1
        agenda.cancel_appointment(ctx, entity_id, CancelBody(version=versione, kind="mistake", reason=nota[:300]))
    elif tipo == "activity":
        core_service.mark_activity_mistake(ctx, entity_id, MistakeMark(note=nota))
    elif tipo == "task":
        core_service.mark_task_mistake(ctx, entity_id, MistakeMark(note=nota))
    elif tipo == "seller_lead":
        r = passo["result"]
        sellers.deactivate(ctx, SellerDeactivate(property_id=int(r["property_id"]), contact_id=int(r["contact_id"]),
                                                 outcome="mistake", note=nota))
    else:  # pragma: no cover - ENTITY copre ogni intento annullabile
        raise operations.StepRefused(operations.UNSUPPORTED)


def _registra(ctx, comando: dict, passo: dict, tipo: str, entity_id: int, esito: str, codice: str | None,
              dettaglio: dict | None = None) -> str:
    try:
        with core_cursor(commit=True) as (_, cur):
            cur.execute("INSERT INTO voice_step_undos (agency_id, command_id, ordinal, actor_user_id, entity_type, "
                        "entity_id, outcome, code, detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (comando["agency_id"], comando["id"], passo["ordinal"], comando["actor_user_id"], tipo,
                         entity_id, esito, (codice or None) and codice[:60], Json(dettaglio or {})))
    except pg_errors.UniqueViolation:
        return "already_undone"
    return esito


def _annulla_uno(ctx, comando: dict, passo: dict, stato: str, codice: str | None) -> dict[str, Any]:
    ordinal = passo["ordinal"]
    base = {"ordinal": ordinal, "intent": passo["intent"]}
    if stato in ("not_undoable", "not_applicable"):
        return {**base, "outcome": stato, "code": codice}
    tipo, entity_id = _entita(passo)
    if stato == "undone":
        with voice_repository.read_only() as cur:
            registrato = _registrato_annullato(cur, comando["id"], ordinal)
        if not registrato:   # annullato nel CRM ma non registrato (crash): si registra ora
            _registra(ctx, comando, passo, tipo, entity_id, "undone", None, {"recovered": True})
        return {**base, "outcome": "already_undone", "code": None}
    if stato in ("expired", "blocked"):
        _registra(ctx, comando, passo, tipo, entity_id, stato, codice)
        return {**base, "outcome": stato, "code": codice}
    try:
        _servizio(ctx, comando, passo, tipo, entity_id)
    except Exception as exc:  # noqa: BLE001 - blocchi e permessi del CRM, registrati
        with voice_repository.read_only() as cur:
            fatto = _gia_annullato(cur, tipo, entity_id)
        if fatto:
            return {**base, "outcome": _registra(ctx, comando, passo, tipo, entity_id, "undone", None,
                                                 {"already": operations.error_code(exc)}), "code": None}
        codice = operations.error_code(exc)
        _registra(ctx, comando, passo, tipo, entity_id, "blocked", codice,
                  {"blockers": getattr(exc, "extra", {}).get("blockers")} if getattr(exc, "extra", None) else None)
        return {**base, "outcome": "blocked", "code": codice}
    return {**base, "outcome": _registra(ctx, comando, passo, tipo, entity_id, "undone", None), "code": None}


def undo_step(ctx, command_id: int, ordinal: int) -> dict[str, Any]:
    executor.gate(ctx)
    with ledger.executing(ctx, command_id):
        comando = ledger.get_command(ctx, command_id)
        passo = next((p for p in comando["steps"] if p["ordinal"] == ordinal), None)
        if passo is None:
            raise ledger.CommandNotFound("passo non trovato")
        with voice_repository.read_only() as cur:
            stato, codice = _valuta_tutti(cur, comando)[ordinal]
        return _annulla_uno(ctx, comando, passo, stato, codice)


def undo_command(ctx, command_id: int) -> list[dict[str, Any]]:
    """Tutto il comando, in ordine inverso. Ogni passo si rivaluta dopo i
    successivi: un dipendente annullato libera il passo da cui dipende."""
    executor.gate(ctx)
    esiti = []
    with ledger.executing(ctx, command_id):
        comando = ledger.get_command(ctx, command_id)
        for passo in sorted(comando["steps"], key=lambda p: -p["ordinal"]):
            with voice_repository.read_only() as cur:
                stato, codice = _valuta_tutti(cur, comando)[passo["ordinal"]]
            esiti.append(_annulla_uno(ctx, comando, passo, stato, codice))
    return esiti
