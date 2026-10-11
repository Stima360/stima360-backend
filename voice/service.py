"""STIMA Voice Fase 5 - il servizio delle API: collega fornitore AI, planner
(Fasi 1-2), registro (Fase 3) ed esecutore (Fase 4). Nessuna logica del CRM:
ogni scrittura passa dall'esecutore, che usa i servizi esistenti.

Abilitazione fail-closed, su tre livelli (tutti necessari):
  1. `STIMA_VOICE_ENABLED=1` nell'ambiente del server (assente = spento);
  2. `voice_agency_settings.enabled` per l'agenzia (default FALSE; tabella
     assente, cioe' migrazione 098 non applicata = spento);
  3. il fornitore AI configurato (`voice.provider`), solo per interpretare.
In piu' il router (`voice.router`) NON e' montato in `main.py`: montarlo e'
una decisione separata.

Chi parla, l'agenzia, il ruolo e i permessi vengono SOLO dalla sessione
(`OperatorContext` del router): mai dal testo, dal modello o dal corpo della
richiesta. Le scelte fra candidati che arrivano dal client sono valide solo
se compaiono fra i candidati che il CRM mostra a quell'operatore (executor).

Limite d'uso: `voice_agency_settings.max_commands_per_day` comandi NUOVI per
agenzia nel giorno di calendario Europe/Rome (una ripetizione della stessa
chiave non conta). Limite morbido: due richieste concorrenti al confine possono
superarlo di poco. `max_audio_seconds_month` non si applica: in Fase 5 non c'e'
audio.

Limiti noti, dichiarati (non nascosti):
  * un comando chiuso solo con la riconciliazione resta `partial` come stato
    complessivo anche con tutti i passi conclusi (la 098 non ammette
    `partial -> completed` senza esecuzione): la vista lo dice con
    `all_steps_concluded` e una nota;
  * il comune fuori dal catalogo del CRM ora si rileva nel piano
    (`municipality_not_supported`, domanda non confermabile) con la stessa
    regola del censimento; il censimento resta comunque l'ultima difesa.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg2

from . import dates, executor, ledger, planner, reconcile, undo
from . import provider as voice_provider
from . import repository as voice_repository
from .executor import VoiceRefused

log = logging.getLogger("stima360.voice")
ROMA = ZoneInfo("Europe/Rome")

GLOBALLY_DISABLED = "VOICE_GLOBALLY_DISABLED"
DAILY_LIMIT = "VOICE_DAILY_LIMIT"
TEXT_INVALID = "VOICE_TEXT_INVALID"

#: I messaggi delle domande e dei blocchi, per codice (stabili, in italiano).
MESSAGES = {
    "contact_identity_weak": "Serve il cognome, il telefono o l'email del contatto.",
    "record_kind_unknown": "Scheda commerciale o di censimento?",
    "record_kind_unverified": "Non ho capito se e' una scheda commerciale o di censimento.",
    "record_kind_conflict": "Il vocale parla sia di vendita sia di censimento: che scheda serve?",
    "building_location_missing": "Dove si trova la palazzina? Serve l'indirizzo o un nome.",
    "task_without_contact": "A quale contatto si riferisce il promemoria?",
    "agent_named": "L'appuntamento e' per un altro agente: serve il permesso di assegnare.",
    dates.UNRECOGNIZED_DATE: "Non ho capito il giorno.",
    dates.UNRECOGNIZED_TIME: "Non ho capito l'orario.",
    dates.AMBIGUOUS_HOUR: "Mattina o pomeriggio?",
    dates.AMBIGUOUS_WEEK: "Quale giorno, esattamente?",
    dates.NONEXISTENT_LOCAL: "Quell'orario non esiste nella notte del cambio d'ora.",
    dates.DOUBLE_LOCAL: "Quell'orario cade due volte nella notte del cambio d'ora.",
    dates.PAST: "Il momento indicato e' gia' passato.",
    "unsupported_intent": "Questa richiesta non e' fra i comandi vocali.",
    "units_invented": "Le unita' non dettate non si creano: si aggiungono con il censimento.",
    "invalid_payload": "Dati non validi per il CRM.",
    "too_many_commands": "Troppi comandi in un solo vocale.",
    "reference_not_found": "Non trovo il record indicato fra quelli che puoi vedere.",
    "reference_ambiguous": "Ci sono piu' record possibili: scegli quello giusto.",
    "reference_fuzzy": "Ho trovato un record simile: confermi che e' questo?",
    "note_target_not_exact": "Confermi il record su cui scrivere la nota?",
    "contact_confirmation_required": "Trovato per nome: confermi che e' questo contatto?",
    "possible_duplicate_contact": "Esiste gia' un contatto con questi dati: crearne un altro?",
    "possible_duplicate_hidden": "Un collega segue gia' un contatto con questi recapiti: crearne un altro?",
    "similar_building_found": "Esiste gia' una palazzina simile: salvarne un'altra?",
    "similar_unit_found": "Esiste gia' un'unita' simile: salvarne un'altra?",
    "cadastral_duplicate": "Unita' gia' censita con gli stessi dati catastali: non si crea.",
    "agenda_conflict": "In quell'orario hai gia' un impegno: scegli un altro orario.",
    "agent_cannot_assign": "Non puoi fissare appuntamenti per altri agenti.",
    "depends_on_question": "Dipende da una domanda di un passo precedente.",
    "depends_on_blocked": "Dipende da un passo bloccato.",
    "mode_review": "Modalita' revisione: ogni passo va confermato.",
    "mode_assisted": "Modalita' assistita: questo passo va confermato.",
    executor.OWNER_LINK_CONFIRMATION: "Confermi il collegamento del proprietario? Non si annulla con la voce.",
    executor.COMPOSITE: "Comando in piu' passi: conferma prima di qualsiasi scrittura.",
    executor.PLAN_HAS_BLOCKED: "Una parte del comando e' bloccata: confermi di eseguire il resto?",
    executor.PROPERTY_NOT_MANAGEABLE: "L'immobile non e' gestito da te.",
    executor.MUNICIPALITY_NOT_SUPPORTED: "Il comune indicato non e' fra quelli gestiti dal CRM: indica un comune valido.",
    executor.REFERENCE_CHANGED: "I dati sono cambiati dopo la conferma: ripeti il comando.",
}
PARTIAL_NOTICE = ("Tutti i passi sono conclusi, ma lo stato complessivo resta «parziale» perche' almeno "
                  "un passo e' stato chiuso con una riconciliazione (limite noto).")


# ---------------------------------------------------------------------------
# Abilitazione
# ---------------------------------------------------------------------------

def globally_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return env.get("STIMA_VOICE_ENABLED", "").strip() == "1"


def guard(ctx):
    """Tutti i livelli d'abilitazione e il cancello dell'esecutore (D-E)."""
    if not globally_enabled():
        raise VoiceRefused(GLOBALLY_DISABLED, "STIMA Voice non e' attivo su questo server")
    try:
        return executor.gate(ctx)
    except psycopg2.Error as exc:
        if getattr(exc, "pgcode", None) == "42P01":          # 098 non applicata: spento
            raise VoiceRefused(executor.DISABLED, "STIMA Voice non e' installato su questo database") from None
        raise


# ---------------------------------------------------------------------------
# Ricezione del testo -> piano (-> esecuzione automatica se tutto e' `auto`)
# ---------------------------------------------------------------------------

def _gia_ricevuto(ctx, chiave) -> bool:
    with voice_repository.read_only() as cur:
        cur.execute("SELECT 1 FROM voice_commands WHERE agency_id = %s AND actor_user_id = %s AND client_command_id = %s",
                    (ctx.require_agency(), ctx.user_id, str(chiave)))
        return cur.fetchone() is not None


def _limite_giornaliero(ctx, impostazioni, adesso: datetime) -> None:
    giorno = adesso.astimezone(ROMA).date()
    inizio = datetime.combine(giorno, time(0, 0), tzinfo=ROMA)
    fine = datetime.combine(giorno + timedelta(days=1), time(0, 0), tzinfo=ROMA)
    with voice_repository.read_only() as cur:
        cur.execute("SELECT count(*) AS n FROM voice_commands WHERE agency_id = %s AND recorded_at >= %s "
                    "AND recorded_at < %s", (ctx.require_agency(), inizio, fine))
        usati = int(cur.fetchone()["n"])
    if usati >= impostazioni.max_commands_per_day:
        raise VoiceRefused(DAILY_LIMIT, "Limite giornaliero dei comandi vocali raggiunto")


def submit(ctx, client_command_id, text: str, *, provider, now: datetime | None = None) -> dict[str, Any]:
    """Testo dettato -> comando nel registro -> piano. Idempotente sulla
    chiave del client: la stessa chiave con lo stesso testo restituisce lo
    stesso comando senza richiamare il modello; con un testo diverso e' un
    conflitto. Un errore del fornitore lascia il comando senza piano e
    senza effetti: si ripete con la stessa chiave."""
    impostazioni = guard(ctx)
    testo = (text or "").strip()
    if not testo or len(testo) > voice_provider.MAX_TEXT:
        raise VoiceRefused(TEXT_INVALID, "testo vuoto o troppo lungo")
    adesso = now or datetime.now(ROMA)
    if not _gia_ricevuto(ctx, client_command_id):
        _limite_giornaliero(ctx, impostazioni, adesso)
    riga, _ = ledger.open_command(ctx, client_command_id, input_kind="text",
                                  request_sha256=ledger.request_fingerprint("text", testo), recorded_at=adesso)
    if riga["plan_sha256"] is None:
        with ledger.executing(ctx, riga["id"]):            # una sola interpretazione per comando
            comando = ledger.get_command(ctx, riga["id"])
            if comando["plan_sha256"] is None:
                output = voice_provider.interpret(provider, testo, comando["recorded_at"])
                piano = planner.build_plan(output, testo, comando["recorded_at"])
                executor.prepare(ctx, riga["id"], piano)
                registrato = ledger.get_command(ctx, riga["id"])
                if registrato["steps"] and all(p["decision"] == "auto" for p in registrato["steps"]):
                    automatico = True
                else:
                    automatico = False
            else:
                automatico = False
        if automatico:
            executor.execute(ctx, riga["id"])
    return view(ctx, riga["id"])


# ---------------------------------------------------------------------------
# Vista
# ---------------------------------------------------------------------------

def _motivi(codici) -> list[dict]:
    return [{"code": c, "message": MESSAGES.get(c, c), "confirmable": c in executor.CONFIRMABLE} for c in codici]


_RISULTATO = ("entity", "id", "record_kind", "created", "preexisting", "origin", "property_id", "contact_id",
              "lead_id", "reconciled")


def view(ctx, command_id: int) -> dict[str, Any]:
    guard(ctx)
    comando = ledger.get_command(ctx, command_id)
    base = {"command_id": comando["id"], "status": comando["status"], "mode": comando["mode"],
            "recorded_at": comando["recorded_at"].isoformat() if comando["recorded_at"] else None}
    if comando["plan"] is None:
        return {**base, "steps": [], "clarifications": [], "all_steps_concluded": False, "notice": None}
    viste = executor.situation(ctx, command_id)
    annullabili = {u["ordinal"]: u for u in undo.assess(ctx, command_id)}
    piano = {s["ordinal"]: s for s in comando["plan"]["steps"]}
    passi = []
    for p in comando["steps"]:
        n, v = p["ordinal"], viste[p["ordinal"]]
        attesa = None
        if p["state"] == "pending":
            attesa = executor._attesa(v, executor._conferme(comando["frozen"], n))
            if attesa is None and v.decision == "auto" and p["decision"] == "ask" \
                    and executor._conferme(comando["frozen"], n) is None:
                attesa = "needs_confirmation"
        scelte = {k: x for k, x in v.labels.items() if len(x) > 1 or v.decision == "ask"}
        passi.append({
            "ordinal": n, "intent": p["intent"], "quote": piano[n]["quote"], "state": p["state"],
            "decision": v.decision if p["state"] in ("pending", "failed") else p["decision"],
            "needs": attesa, "reasons": _motivi(v.reasons if p["state"] in ("pending", "failed") else p["reasons"]),
            "candidates": scelte, "duplicates": v.duplicate_labels, "hidden_duplicate": v.hidden,
            "start_at": piano[n].get("start_at"), "end_at": piano[n].get("end_at"),
            "result": {k: x for k, x in (p["result"] or {}).items() if k in _RISULTATO},
            "error": p["error_type"], "undo": annullabili[n]["status"],
        })
    conclusi = all(p["state"] in ("succeeded", "skipped") for p in comando["steps"])
    return {**base, "steps": passi, "clarifications": list(comando["plan"].get("clarifications") or []),
            "all_steps_concluded": conclusi,
            "notice": PARTIAL_NOTICE if conclusi and comando["status"] == "partial" else None}


# ---------------------------------------------------------------------------
# Azioni (ognuna ricontrolla l'abilitazione e restituisce la vista)
# ---------------------------------------------------------------------------

def confirm(ctx, command_id: int, *, steps: list[dict] | None = None, all_steps: bool = False) -> dict[str, Any]:
    guard(ctx)
    if all_steps:
        scelte = {int(s["ordinal"]): s.get("choices") or {} for s in (steps or [])}
        executor.confirm_all(ctx, command_id, choices=scelte)
    else:
        for s in steps or []:
            executor.confirm(ctx, command_id, int(s["ordinal"]), choices=s.get("choices") or None)
    return view(ctx, command_id)


def execute(ctx, command_id: int) -> dict[str, Any]:
    guard(ctx)
    executor.execute(ctx, command_id)
    return view(ctx, command_id)


def retry(ctx, command_id: int, ordinal: int) -> dict[str, Any]:
    guard(ctx)
    executor.retry(ctx, command_id, ordinal)
    return view(ctx, command_id)


def cancel(ctx, command_id: int) -> dict[str, Any]:
    guard(ctx)
    ledger.cancel(ctx, command_id)
    return view(ctx, command_id)


def undo_status(ctx, command_id: int) -> list[dict]:
    guard(ctx)
    return undo.assess(ctx, command_id)


def undo_step(ctx, command_id: int, ordinal: int) -> dict:
    guard(ctx)
    return undo.undo_step(ctx, command_id, ordinal)


def undo_command(ctx, command_id: int) -> list[dict]:
    guard(ctx)
    return undo.undo_command(ctx, command_id)


def reconcile_inspect(ctx, command_id: int, ordinal: int) -> dict:
    guard(ctx)
    return reconcile.inspect(ctx, command_id, ordinal)


def reconcile_decide(ctx, command_id: int, ordinal: int, *, outcome: str, record_id: int | None) -> dict:
    guard(ctx)
    reconcile.decide(ctx, command_id, ordinal, outcome=outcome, record_id=record_id)
    return view(ctx, command_id)
