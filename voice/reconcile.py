"""STIMA Voice Fase 4 - la riconciliazione dei passi `indeterminate` (D-G).

Un passo e' `indeterminate` quando il servizio del CRM non ha una chiave di
idempotenza e l'esecuzione si e' interrotta fra la chiamata e il checkpoint
(contatto, collegamento proprietario, nota su immobile). Non si ripete mai da
solo: ripeterlo potrebbe creare un doppione.

`inspect` analizza in SOLA LETTURA e risponde con uno di tre esiti:
  * `present` - l'effetto c'e' di sicuro (oggi solo il collegamento
    proprietario: la coppia immobile-contatto e' unica nel CRM);
  * `absent` - l'effetto sicuramente non c'e'. Per i passi senza chiave non
    si puo' dimostrare: l'assenza di una corrispondenza NON prova che la
    scrittura non sia avvenuta (puo' essere stata corretta o spostata nel
    frattempo). Oggi nessun caso arriva qui;
  * `not_provable` - non dimostrabile: si mostrano i candidati (creati da chi
    parla, dopo l'inizio del passo, con gli stessi dati), ma campi simili non
    bastano ad attribuire un record al comando.

`decide` e' la decisione UMANA dell'operatore del comando:
  * `present` con il record scelto fra i candidati (o il collegamento
    esistente): il passo diventa `succeeded`, marcato `reconciled: human`;
  * `absent`: il passo diventa `skipped` e non si ripete; se serve, si detta
    di nuovo. La 098 non consente `indeterminate -> pending`, ed e' giusto:
    nessuna ripetizione di un passo non idempotente.
Le scritture toccano SOLO `voice_command_steps` e `voice_command_events`,
con le transizioni gia' ammesse dalla 098.
"""
from __future__ import annotations

from typing import Any

from psycopg2.extras import Json

from core.database import core_cursor
from core.normalization import normalize_email, normalize_phone

from . import executor, ledger
from . import repository as voice_repository

NOT_INDETERMINATE = "VOICE_NOT_INDETERMINATE"
NOT_A_CANDIDATE = "VOICE_RECORD_NOT_A_CANDIDATE"
OUTCOME_INVALID = "VOICE_OUTCOME_INVALID"
NOT_PRESENT = "VOICE_EFFECT_NOT_PRESENT"


class ReconcileRefused(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _passo(ctx, command_id: int, ordinal: int) -> tuple[dict, dict, dict]:
    comando = ledger.get_command(ctx, command_id)
    riga = next((p for p in comando["steps"] if p["ordinal"] == ordinal), None)
    if riga is None:
        raise ledger.CommandNotFound("passo non trovato")
    if riga["state"] != "indeterminate":
        raise ReconcileRefused(NOT_INDETERMINATE, f"passo {ordinal} in stato {riga['state']}")
    piano = next(s for s in comando["plan"]["steps"] if s["ordinal"] == ordinal)
    return comando, riga, piano


def inspect(ctx, command_id: int, ordinal: int) -> dict[str, Any]:
    executor.gate(ctx)
    comando, riga, piano = _passo(ctx, command_id, ordinal)
    agency_id = ctx.require_agency()
    dal = riga["started_at"]
    ids = {k: int(v) for k, v in (comando["frozen"].get(f"exec:{ordinal}:refs") or {}).items()}
    p = piano["payload"]
    candidati: list[dict] = []
    esito, base = "not_provable", "no_idempotency_key"
    with voice_repository.read_only() as cur:
        if piano["intent"] == "create_contact":
            condizioni, params = [], []
            if normalize_phone(p.get("phone")):
                condizioni.append("phone_normalized = %s")
                params.append(normalize_phone(p.get("phone")))
            if normalize_email(p.get("email")):
                condizioni.append("email_normalized = %s")
                params.append(normalize_email(p.get("email")))
            if not condizioni:
                condizioni.append("lower(coalesce(first_name, '')) = lower(%s) AND lower(coalesce(last_name, '')) = lower(%s)")
                params += [p.get("first_name") or "", p.get("last_name") or ""]
            cur.execute("SELECT id, display_name FROM contacts WHERE agency_id = %s AND created_by_user_id = %s "
                        f"AND created_at >= %s AND ({' OR '.join(condizioni)}) ORDER BY id",
                        [agency_id, ctx.user_id, dal] + params)
            candidati = [{"id": int(r["id"]), "label": r["display_name"] or f"Contatto {r['id']}"} for r in cur.fetchall()]
        elif piano["intent"] == "link_owner" and {"property", "contact"} <= set(ids):
            cur.execute("SELECT 1 FROM property_contacts pc JOIN properties pr ON pr.id = pc.property_id "
                        "WHERE pc.property_id = %s AND pc.contact_id = %s AND pc.role IN ('owner', 'seller') "
                        "AND pr.agency_id = %s", (ids["property"], ids["contact"], agency_id))
            if cur.fetchone() is not None:
                esito, base = "present", "owner_link_exists"
        elif piano["intent"] == "add_note" and "property" in ids:
            cur.execute("SELECT id FROM activities WHERE agency_id = %s AND property_id = %s AND created_by_user_id = %s "
                        "AND created_at >= %s AND description = %s ORDER BY id",
                        (agency_id, ids["property"], ctx.user_id, dal, p.get("note")))
            candidati = [{"id": int(r["id"]), "label": f"Nota {r['id']}"} for r in cur.fetchall()]
    return {"ordinal": ordinal, "intent": piano["intent"], "outcome": esito, "basis": base,
            "candidates": candidati, "since": dal.isoformat() if dal else None}


def decide(ctx, command_id: int, ordinal: int, *, outcome: str, record_id: int | None = None) -> dict:
    """La decisione umana. Rianalizza (sola lettura) e poi chiude il passo."""
    if outcome not in ("present", "absent"):
        raise ReconcileRefused(OUTCOME_INVALID, "esito: present oppure absent")
    analisi = inspect(ctx, command_id, ordinal)
    agency_id, actor = ctx.require_agency(), int(ctx.user_id)
    if outcome == "present":
        if analisi["intent"] == "link_owner":
            if analisi["outcome"] != "present":
                raise ReconcileRefused(NOT_PRESENT, "il collegamento non risulta presente")
            risultato = {"entity": "property_contact", "created": None, "reconciled": "human"}
        else:
            if record_id is None or int(record_id) not in {c["id"] for c in analisi["candidates"]}:
                raise ReconcileRefused(NOT_A_CANDIDATE, "il record indicato non e' fra i candidati")
            entita = "contact" if analisi["intent"] == "create_contact" else "activity"
            risultato = {"entity": entita, "id": int(record_id), "created": None, "reconciled": "human"}
        stato, evento = "succeeded", "step_succeeded"
    else:
        risultato, stato, evento = None, "skipped", "step_skipped"
    with core_cursor(commit=True) as (_, cur):
        ledger._command(cur, agency_id, actor, command_id, lock=True)
        if stato == "succeeded":
            cur.execute("UPDATE voice_command_steps SET state = 'succeeded', result = %s, finished_at = NOW() "
                        "WHERE command_id = %s AND ordinal = %s AND state = 'indeterminate' RETURNING *",
                        (Json(risultato), command_id, ordinal))
        else:
            cur.execute("UPDATE voice_command_steps SET state = 'skipped', error_type = 'reconciled_absent', "
                        "finished_at = NOW() WHERE command_id = %s AND ordinal = %s AND state = 'indeterminate' "
                        "RETURNING *", (command_id, ordinal))
        riga = cur.fetchone()
        if riga is None:
            raise ReconcileRefused(NOT_INDETERMINATE, "il passo non e' piu' incerto")
        ledger._event(cur, agency_id, command_id, evento, actor, ordinal,
                      {"reconciled": "human", "analysis": analisi["outcome"], "basis": analisi["basis"],
                       "decision": outcome, "record_id": record_id})
        return dict(riga)
