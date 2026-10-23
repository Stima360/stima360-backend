"""STIMA Voice Fase 4 - l'esecutore deterministico.

Il modello AI interpreta; qui il codice decide e scrive, sempre attraverso i
servizi del CRM (`voice.operations`) e sempre con la sessione reale di chi
parla. Il registro e' quello della Fase 3 (`voice.ledger`).

Flusso:
  1. `prepare` - normalizza il piano (D-F), risolve i riferimenti con i
     permessi reali, aggiunge i fatti d'Agenda, decide (politica della Fase 1
     piu' le guardie della Fase 4) e registra il piano con le chiavi congelate.
  2. `confirm` / `confirm_all` - la conferma di un passo vale per la
     SITUAZIONE confermata (motivi, candidati, doppioni: un'impronta). Una
     domanda che chiede dati non si conferma; un passo bloccato nemmeno.
  3. `execute` - riesamina la situazione con i dati e i permessi di ADESSO,
     poi esegue i passi in ordine. Un passo parte solo se: e' `auto` oggi e
     lo era (o e' stato confermato), oppure la sua conferma ha la stessa
     impronta di oggi. Si ferma al primo passo che non riesce: il resto
     aspetta (stato `partial` del registro). Nessun retry cieco: un passo
     fallito riparte solo con `retry`, un passo incerto solo dopo la
     riconciliazione (`voice.reconcile`).

Guardie della Fase 4 (solo piu' severe della politica, mai meno):
  * collegamento proprietario: sempre una domanda (D-A), mai annullabile;
  * comando composto (un passo usa il record di un altro): domanda prima di
    qualunque scrittura, perche' un fallimento a meta' lascerebbe record
    parziali; anche quando un altro passo del vocale e' bloccato;
  * D-D: collegamento proprietario e «Vende» bloccati su un immobile che chi
    parla non gestisce;
  * doppione catastale: bloccato (il censimento lo rifiuta comunque);
  * D-E: il platform admin non usa STIMA Voice, neanche in acting.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Callable

from . import ledger, mandate_rules, operations, policy, resolver
from . import settings as voice_settings
from .planner import Issue, Plan, Ref, Step, fingerprint

# Rifiuti (stabili).
DISABLED = "VOICE_DISABLED"
PLATFORM_ADMIN_BLOCKED = "VOICE_PLATFORM_ADMIN_BLOCKED"
ACTOR_REQUIRED = "VOICE_ACTOR_REQUIRED"
PLAN_MISSING = "VOICE_PLAN_MISSING"
CONFIRMATION_NOT_ALLOWED = "VOICE_CONFIRMATION_NOT_ALLOWED"
CHOICE_REQUIRED = "VOICE_CHOICE_REQUIRED"
CHOICE_INVALID = "VOICE_CHOICE_INVALID"
STEP_NOT_CONFIRMABLE_NOW = "VOICE_STEP_NOT_PENDING"
TOO_MANY_STEPS = "VOICE_TOO_MANY_STEPS"
REFERENCE_CHANGED = "VOICE_REFERENCE_CHANGED"

# Motivi aggiunti dalla Fase 4 (stabili).
OWNER_LINK_CONFIRMATION = "owner_link_confirmation_required"
COMPOSITE = "composite_command_confirmation"
PLAN_HAS_BLOCKED = "plan_has_blocked_steps"
PROPERTY_NOT_MANAGEABLE = "voice_property_not_manageable"
#: Fase 5: comune fuori dal catalogo del CRM. Serve un comune diverso (un
#: dato), non un «si'»: non e' confermabile.
MUNICIPALITY_NOT_SUPPORTED = "municipality_not_supported"

#: I motivi che una conferma puo' sciogliere. Tutti gli altri chiedono dati
#: (o una scelta diversa): servono parole nuove, non un «si'».
CONFIRMABLE = frozenset({
    policy.CONTACT_CONFIRM, policy.REF_FUZZY, policy.NOTE_TARGET_NOT_EXACT, policy.REF_AMBIGUOUS,
    policy.DUPLICATE_VISIBLE, policy.DUPLICATE_HIDDEN, policy.SIMILAR_BUILDING, policy.SIMILAR_UNIT,
    policy.MODE_REVIEW, policy.MODE_ASSISTED, policy.DEPENDS_ON_QUESTION,
    OWNER_LINK_CONFIRMATION, COMPOSITE, PLAN_HAS_BLOCKED,
    # V9: l'operazione sull'incarico detta per esteso («genera un incarico
    # effettivo», «aggiorna il prezzo»...): e' proprio cio' che si conferma
    *mandate_rules.MODES,
})
MAX_STEPS = 12
DONE = ("succeeded", "skipped")


class VoiceRefused(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


# ---------------------------------------------------------------------------
# Cancello
# ---------------------------------------------------------------------------

def gate(ctx) -> voice_settings.VoiceSettings:
    """D-E e accensione: platform admin mai, operatore sempre, agenzia
    accesa. Nessun contesto di sistema, nessuna origine privilegiata."""
    if getattr(ctx, "is_platform_admin", False):
        raise VoiceRefused(PLATFORM_ADMIN_BLOCKED, "STIMA Voice non e' disponibile per il platform admin")
    if getattr(ctx, "user_id", None) is None:
        raise VoiceRefused(ACTOR_REQUIRED, "serve la sessione di un operatore")
    ctx.require_agency()
    impostazioni = voice_settings.load(ctx)
    if not impostazioni.enabled:
        raise VoiceRefused(DISABLED, "STIMA Voice non e' attivo per questa agenzia")
    return impostazioni


# ---------------------------------------------------------------------------
# D-F: un solo collegamento proprietario, esplicito
# ---------------------------------------------------------------------------

def _stesso_contatto(a: Ref, b: Ref) -> bool:
    if a.step is not None or b.step is not None:
        return a.step == b.step
    return a.description == b.description


def normalize_plan(plan: Plan) -> Plan:
    """Il proprietario detto dentro `create_unit` diventa UN passo
    `link_owner` esplicito, subito dopo l'unita' - salvo che il vocale ne
    contenga gia' uno per la stessa coppia: allora resta solo quello.
    «Vende» sulla stessa coppia dipende dal collegamento. Le altre parti del
    piano non cambiano."""
    vecchi = [copy.deepcopy(s) for s in plan.steps]
    espliciti = [(s.refs["property"].step, s.refs["contact"]) for s in vecchi
                 if s.intent == "link_owner" and "property" in s.refs and "contact" in s.refs]
    nuovi: list[tuple[Step, int | None]] = []        # (passo, ordinale vecchio)
    for s in vecchi:
        proprietario = s.refs.pop("owner", None) if s.intent == "create_unit" else None
        nuovi.append((s, s.ordinal))
        if proprietario is None:
            continue
        if any(prop == s.ordinal and _stesso_contatto(contatto, proprietario) for prop, contatto in espliciti):
            continue
        sintetico = Step(ordinal=0, intent="link_owner", quote=s.quote,
                         refs={"contact": proprietario, "property": Ref("property", s.ordinal, {})})
        nuovi.append((sintetico, None))
    if len(nuovi) > MAX_STEPS:
        raise VoiceRefused(TOO_MANY_STEPS, "troppi passi in un solo vocale")
    mappa = {vecchio: n for n, (_, vecchio) in enumerate(nuovi, start=1) if vecchio is not None}
    passi: list[Step] = []
    for n, (s, _) in enumerate(nuovi, start=1):
        s.ordinal = n
        s.refs = {k: Ref(r.kind, mappa[r.step] if r.step is not None else None, r.description)
                  for k, r in s.refs.items()}
        s.depends_on = tuple(sorted({r.step for r in s.refs.values() if r.step is not None}))
        passi.append(s)
    for s in passi:
        if s.intent != "activate_seller":
            continue
        for l in passi:
            if (l.intent == "link_owner" and l.ordinal < s.ordinal
                    and l.refs["property"].step == s.refs["property"].step
                    and (l.refs["property"].step is not None
                         or l.refs["property"].description == s.refs["property"].description)
                    and _stesso_contatto(l.refs["contact"], s.refs["contact"])):
                s.depends_on = tuple(sorted(set(s.depends_on) | {l.ordinal}))
    return Plan(plan.transcript, plan.recorded_at, passi, plan.clarifications, fingerprint(passi))


def _plan_from_json(comando: dict) -> Plan:
    dati = comando["plan"]
    passi = []
    for s in dati["steps"]:
        passi.append(Step(
            ordinal=s["ordinal"], intent=s["intent"], quote=s["quote"], payload=dict(s["payload"]),
            refs={k: Ref(r["kind"], r["step"], dict(r["description"])) for k, r in s["refs"].items()},
            depends_on=tuple(s["depends_on"]), issues=tuple(Issue(c, "") for c in s["issues"]),
            start_at=datetime.fromisoformat(s["start_at"]) if s.get("start_at") else None,
            end_at=datetime.fromisoformat(s["end_at"]) if s.get("end_at") else None))
    return Plan(comando.get("transcript") or "", datetime.fromisoformat(dati["recorded_at"]), passi,
                tuple(dati.get("clarifications") or ()), dati["fingerprint"])


# ---------------------------------------------------------------------------
# Situazione: risoluzione + fatti d'Agenda + guardie
# ---------------------------------------------------------------------------

@dataclass
class View:
    ordinal: int
    intent: str
    decision: str
    reasons: tuple[str, ...]
    candidates: dict[str, list[int]] = field(default_factory=dict)
    resolved: dict[str, int] = field(default_factory=dict)
    duplicates: list[int] = field(default_factory=list)
    hidden: bool = False
    digest: str = ""
    #: Fase 5: le etichette dei candidati e dei doppioni, per la domanda.
    #: Solo dati che chi parla vede gia' (resolver). Fuori dall'impronta.
    labels: dict[str, list[dict]] = field(default_factory=dict)
    duplicate_labels: list[dict] = field(default_factory=list)
    #: Collaudo 3: il proprietario detto, trovato fra i contatti ma senza
    #: immobili (resolver.owner_hints). Fuori dall'impronta.
    owner_labels: list[dict] = field(default_factory=list)
    #: V9: la situazione reale e la valutazione di un passo sugli incarichi
    #: (resolver.mandates), per la scheda. Fuori dall'impronta: l'operazione
    #: confermata e' gia' fra i motivi.
    mandate: dict | None = None

    @property
    def confirmable(self) -> bool:
        return self.decision == "ask" and set(self.reasons) <= CONFIRMABLE


def strengthen(step: Step, decision: str, reasons, *, manageable: bool = True, composite: bool = False,
               cadastral: bool = False, municipality_ok: bool = True) -> tuple[str, tuple[str, ...]]:
    """Le guardie della Fase 4 sopra la decisione della politica. Solo piu'
    severe: un `blocked` resta tale, un `ask` non diventa `auto`."""
    motivi = list(reasons)
    if not municipality_ok and decision != "blocked" and not cadastral and manageable:
        motivi.append(MUNICIPALITY_NOT_SUPPORTED)
        decision = "ask"
    blocchi = []
    if cadastral:
        blocchi.append(policy.CADASTRAL_DUPLICATE)
    if not manageable:
        blocchi.append(PROPERTY_NOT_MANAGEABLE)
    if blocchi:
        gia = motivi if decision == "blocked" else []
        return "blocked", tuple(dict.fromkeys(gia + blocchi))
    if decision == "blocked":
        return decision, tuple(motivi)
    extra = []
    if step.intent == "link_owner":
        extra.append(OWNER_LINK_CONFIRMATION)
    if composite:
        extra.append(COMPOSITE)
    if extra:
        motivi = [m for m in motivi if m not in (policy.MODE_REVIEW, policy.MODE_ASSISTED)] + extra \
            if decision == "auto" else motivi + extra
        return "ask", tuple(dict.fromkeys(motivi))
    motivi = [m for m in motivi if m != policy.CADASTRAL_DUPLICATE]
    return decision, tuple(motivi)


def guard_reasons_for_facts(*, cadastral: bool = False) -> tuple[str, tuple[str, ...]]:
    """La guardia catastale da sola (pura): un doppione catastale e' sempre
    bloccato, anche se la politica lo considerava una domanda."""
    passo = Step(ordinal=1, intent="create_unit", quote="-")
    return strengthen(passo, "ask", (policy.CADASTRAL_DUPLICATE,) if cadastral else (), cadastral=cadastral)


def _composto(step: Step, plan: Plan) -> bool:
    return bool(step.depends_on) or any(step.ordinal in s.depends_on for s in plan.steps)


def _gestibile(ctx, step: Step, plan: Plan, resolution, stati: dict[int, dict]) -> bool:
    """D-D, in previsione: si puo' gestire l'immobile di un collegamento o
    di un «Vende»? Owner/admin si'. Un agente: un immobile gia' esistente
    solo se gli e' assegnato; uno creato in questo vocale solo se e'
    commerciale (il censimento non si assegna)."""
    if step.intent not in ("link_owner", "activate_seller") or "property" not in step.refs:
        return True
    rif = step.refs["property"]
    if rif.step is not None:
        fatto = stati.get(rif.step) or {}
        if fatto.get("state") == "succeeded" and (fatto.get("result") or {}).get("id"):
            return operations.manageable(ctx, int(fatto["result"]["id"]))
        if ctx.sees_all_agency_records:
            return True
        return plan.step(rif.step).payload.get("record_kind") != "census"
    pid = resolution.resolved(step.ordinal, "property")
    return True if pid is None else operations.manageable(ctx, pid)


def _digest(plan_sha: str, v: View) -> str:
    dati = {"plan": plan_sha, "ordinal": v.ordinal, "decision": v.decision, "reasons": sorted(v.reasons),
            "candidates": {k: sorted(x) for k, x in sorted(v.candidates.items())},
            "duplicates": sorted(v.duplicates), "hidden": v.hidden}
    if v.mandate:
        # V9: si conferma un'operazione CON i suoi valori (prezzo proposto, versione
        # dell'acquisizione): se cambiano dopo la conferma, serve una conferma nuova.
        valutazione, situazione = v.mandate["evaluation"], v.mandate["situation"]
        dati["mandate"] = {"mode": valutazione.mode, "values": {k: str(x) for k, x in valutazione.values.items()},
                           "acquisition": [(situazione.get("acquisition") or {}).get(c) for c in ("id", "version")]}
    return hashlib.sha256(json.dumps(dati, sort_keys=True).encode()).hexdigest()


def _evaluate(ctx, plan: Plan, impostazioni, *, stati: dict[int, dict], frozen: dict,
              record: bool = False) -> dict[int, View]:
    """La situazione di OGNI passo con i dati e i permessi di adesso."""
    resolution = resolver.resolve(plan, ctx)
    facts = resolution.facts
    conflitti, simili_b, simili_u = set(facts.agenda_conflict), set(facts.similar_building), set(facts.similar_unit)
    for s in plan.steps:
        if (stati.get(s.ordinal) or {}).get("state") in DONE:
            continue
        chiave = frozen.get(f"step:{s.ordinal}:client_request_id")
        proprio = operations.record_by_key(ctx, s.intent, chiave)
        if proprio is not None:          # la stessa richiesta gia' scritta: replica, non doppione
            simili_b.discard(s.ordinal)
            simili_u.discard(s.ordinal)
        if s.intent in ("create_appointment", "acquisition_start") and s.start_at and s.end_at:
            agente = resolution.resolved(s.ordinal, "agent") if "agent" in s.refs else ctx.user_id
            if agente is not None and operations.agenda_busy(ctx, agente, s.start_at, s.end_at,
                                                             exclude_id=(proprio or {}).get("id")):
                conflitti.add(s.ordinal)
    facts = replace(facts, agenda_conflict=frozenset(conflitti), similar_building=frozenset(simili_b),
                    similar_unit=frozenset(simili_u))

    viste: dict[int, View] = {}
    for s in plan.steps:
        base = policy.decide_step(s, facts, impostazioni.policy)
        decisione, motivi = strengthen(
            s, base.decision, base.reasons, manageable=_gestibile(ctx, s, plan, resolution, stati),
            composite=_composto(s, plan), cadastral=s.ordinal in facts.cadastral_duplicate,
            municipality_ok=(s.intent not in ("create_unit", "create_building")
                             or operations.municipality_supported(s.payload)))
        viste[s.ordinal] = View(s.ordinal, s.intent, decisione, motivi)
    # dipendenze: un passo che dipende da uno bloccato e' bloccato; nel piano
    # registrato (come la politica) uno che dipende da una domanda aspetta
    for s in plan.steps:
        v = viste[s.ordinal]
        for d in s.depends_on:
            padre = viste[d]
            if padre.decision == "blocked" and v.decision != "blocked":
                v.decision, v.reasons = "blocked", (policy.DEPENDS_ON_BLOCKED,)
                break
            if record and padre.decision == "ask" and v.decision == "auto":
                v.decision = "ask"
            if record and padre.decision == "ask":
                v.reasons = tuple(dict.fromkeys(v.reasons + (policy.DEPENDS_ON_QUESTION,)))
    aperti = [v for n, v in viste.items() if (stati.get(n) or {}).get("state") not in DONE]
    if any(v.decision == "blocked" for v in aperti):
        for v in aperti:
            if v.decision != "blocked":
                v.decision = "ask"
                v.reasons = tuple(dict.fromkeys(
                    [m for m in v.reasons if m not in (policy.MODE_REVIEW, policy.MODE_ASSISTED)] + [PLAN_HAS_BLOCKED]))
    for s in plan.steps:
        v = viste[s.ordinal]
        for nome, ref in s.refs.items():
            if ref.step is not None:
                continue
            m = resolution.matches.get((s.ordinal, nome))
            if m is not None:
                v.candidates[nome] = [c.id for c in m.candidates]
                v.labels[nome] = [{"id": c.id, "label": c.label} for c in m.candidates]
                if m.resolved_id is not None:
                    v.resolved[nome] = m.resolved_id
        v.duplicates = [c.id for c in resolution.duplicates.get(s.ordinal, ())] if s.intent == "create_contact" else []
        v.duplicate_labels = [{"id": c.id, "label": c.label} for c in resolution.duplicates.get(s.ordinal, ())]
        v.hidden = s.ordinal in facts.duplicate_hidden
        v.owner_labels = [{"id": c.id, "label": c.label} for c in resolution.owner_hints.get(s.ordinal, ())]
        v.mandate = resolution.mandates.get(s.ordinal)
        v.digest = _digest(plan.fingerprint, v)
    return viste


# ---------------------------------------------------------------------------
# Preparazione e conferma
# ---------------------------------------------------------------------------

def prepare(ctx, command_id: int, plan: Plan) -> dict:
    impostazioni = gate(ctx)
    normale = normalize_plan(plan)
    viste = _evaluate(ctx, normale, impostazioni, stati={}, frozen={}, record=True)
    decisione = policy.PlanDecision(tuple(policy.StepDecision(v.ordinal, v.intent, v.decision, v.reasons)
                                          for v in sorted(viste.values(), key=lambda x: x.ordinal)))
    return ledger.record_plan(ctx, command_id, normale, decisione, mode=impostazioni.policy.mode,
                              retention_days=impostazioni.transcript_retention_days)


def _contesto(ctx, command_id: int):
    impostazioni = gate(ctx)
    comando = ledger.get_command(ctx, command_id)
    if comando["plan"] is None:
        raise VoiceRefused(PLAN_MISSING, "il comando non ha ancora un piano")
    stati = {p["ordinal"]: p for p in comando["steps"]}
    return impostazioni, comando, _plan_from_json(comando), stati


def _conferme(frozen: dict, ordinal: int) -> dict | None:
    prefisso = f"confirm:{ordinal}:"
    numeri = sorted(int(k[len(prefisso):]) for k in frozen if k.startswith(prefisso))
    return frozen[f"{prefisso}{numeri[-1]}"] if numeri else None


def situation(ctx, command_id: int) -> dict[int, View]:
    impostazioni, comando, piano, stati = _contesto(ctx, command_id)
    return _evaluate(ctx, piano, impostazioni, stati=stati, frozen=comando["frozen"])


def _conferma(ctx, command_id: int, comando: dict, viste: dict[int, View], stati: dict, ordinal: int,
              choices: dict[str, int] | None) -> dict:
    v = viste.get(ordinal)
    if v is None:
        raise ledger.CommandNotFound("passo non trovato")
    if stati[ordinal]["state"] not in ("pending", "failed"):
        raise VoiceRefused(STEP_NOT_CONFIRMABLE_NOW, f"passo {ordinal} in stato {stati[ordinal]['state']}")
    if v.decision == "auto":
        return {"ordinal": ordinal, "confirmed": False, "reason": "auto"}
    if not v.confirmable:
        raise VoiceRefused(CONFIRMATION_NOT_ALLOWED, "questo passo non si sblocca con una conferma: "
                           + ", ".join(m for m in v.reasons if m not in CONFIRMABLE))
    scelte: dict[str, int] = {}
    for nome, candidati in v.candidates.items():
        scelto = (choices or {}).get(nome)
        if scelto is None:
            if len(candidati) != 1:
                raise VoiceRefused(CHOICE_REQUIRED, f"scegli fra i candidati per {nome}")
            scelto = candidati[0]
        if int(scelto) not in candidati:
            raise VoiceRefused(CHOICE_INVALID, f"{nome}: la scelta non e' fra i candidati")
        scelte[nome] = int(scelto)
    prefisso = f"confirm:{ordinal}:"
    n = 1 + max([int(k[len(prefisso):]) for k in comando["frozen"] if k.startswith(prefisso)] or [0])
    valore = {"digest": v.digest, "choices": scelte, "reasons": list(v.reasons),
              "duplicates": v.duplicates, "hidden": v.hidden}
    ledger.freeze(ctx, command_id, f"{prefisso}{n}", lambda: valore)
    return {"ordinal": ordinal, "confirmed": True, "digest": v.digest, "choices": scelte}


def confirm(ctx, command_id: int, ordinal: int, *, choices: dict[str, int] | None = None) -> dict:
    impostazioni, comando, piano, stati = _contesto(ctx, command_id)
    viste = _evaluate(ctx, piano, impostazioni, stati=stati, frozen=comando["frozen"])
    return _conferma(ctx, command_id, comando, viste, stati, ordinal, choices)


def confirm_all(ctx, command_id: int, *, choices: dict[int, dict[str, int]] | None = None) -> list[dict]:
    """Conferma ogni passo in domanda (i bloccati restano bloccati). Si ferma
    al primo passo che una conferma non puo' sbloccare."""
    impostazioni, comando, piano, stati = _contesto(ctx, command_id)
    viste = _evaluate(ctx, piano, impostazioni, stati=stati, frozen=comando["frozen"])
    esiti = []
    for n in sorted(viste):
        if viste[n].decision != "ask" or stati[n]["state"] not in ("pending", "failed"):
            continue
        esiti.append(_conferma(ctx, command_id, comando, viste, stati, n, (choices or {}).get(n)))
        comando = ledger.get_command(ctx, command_id)
    return esiti


# ---------------------------------------------------------------------------
# Esecuzione
# ---------------------------------------------------------------------------

def _ids(step: Step, stati: dict, v: View, conferma: dict | None) -> dict[str, int] | None:
    ids: dict[str, int] = {}
    for nome, ref in step.refs.items():
        if ref.step is not None:
            fatto = stati.get(ref.step) or {}
            valore = (fatto.get("result") or {}).get("id")
            if fatto.get("state") != "succeeded" or valore is None:
                return None
            ids[nome] = int(valore)
        elif conferma is not None and nome in (conferma.get("choices") or {}):
            ids[nome] = int(conferma["choices"][nome])
        elif nome in v.resolved:
            ids[nome] = int(v.resolved[nome])
        else:
            return None
    return ids


def _run(ctx, command_id: int, ordinal: int, operazione: Callable[[], dict], confirmed: bool) -> dict:
    """Come `ledger.run_step`, con il CODICE dell'errore del CRM nel passo."""
    ledger.begin_step(ctx, command_id, ordinal, confirmed=confirmed)
    try:
        risultato = operazione()
    except ledger.UNCERTAIN_ERRORS as exc:
        ledger.fail_step(ctx, command_id, ordinal, error_type=operations.error_code(exc), uncertain=True)
        raise
    except Exception as exc:
        ledger.fail_step(ctx, command_id, ordinal, error_type=operations.error_code(exc))
        raise
    return ledger.finish_step(ctx, command_id, ordinal, risultato)


def _attesa(v: View, conferma: dict | None) -> str | None:
    """Cosa manca a un passo `pending` per partire, oppure None."""
    if v.decision == "blocked":
        return "blocked"
    if v.decision == "ask":
        if not v.confirmable:
            return "needs_answer"
        if conferma is None or conferma.get("digest") != v.digest:
            return "needs_confirmation"
    return None


def execute(ctx, command_id: int, *, clock: Callable[[], datetime] | None = None) -> dict:
    impostazioni = gate(ctx)
    comando = ledger.get_command(ctx, command_id)
    if comando["plan"] is None:
        raise VoiceRefused(PLAN_MISSING, "il comando non ha ancora un piano")
    esiti: dict[int, dict[str, Any]] = {}
    if comando["status"] in ledger.TERMINAL_COMMAND:
        return _report(ctx, command_id, esiti)
    with ledger.executing(ctx, command_id):
        comando = ledger.get_command(ctx, command_id)
        piano = _plan_from_json(comando)
        stati = {p["ordinal"]: p for p in comando["steps"]}
        viste = _evaluate(ctx, piano, impostazioni, stati=stati, frozen=comando["frozen"])
        adesso = clock() if clock else operations.db_now()
        fermo = False
        for s in piano.steps:
            n, riga, v = s.ordinal, stati[s.ordinal], viste[s.ordinal]
            if riga["state"] in DONE:
                esiti[n] = {"outcome": "done" if riga["state"] == "succeeded" else "skipped"}
                continue
            if riga["state"] in ("failed", "indeterminate", "running"):
                esiti[n] = {"outcome": riga["state"]}
                fermo = True
                continue
            conferma = _conferme(comando["frozen"], n)
            manca = _attesa(v, conferma)
            if manca is None and v.decision == "auto" and riga["decision"] == "ask" and conferma is None:
                manca = "needs_confirmation"          # deciso «chiedi»: serve comunque un si'
            if fermo or any((stati.get(d) or {}).get("state") != "succeeded" for d in s.depends_on):
                esiti[n] = {"outcome": manca or "waiting_dependency", "reasons": v.reasons}
                continue
            if manca is not None:
                esiti[n] = {"outcome": manca, "reasons": v.reasons}
                fermo = True
                continue
            ids = _ids(s, stati, v, conferma)
            if ids is None:
                esiti[n] = {"outcome": "needs_answer", "reasons": v.reasons}
                fermo = True
                continue
            congelati = ledger.freeze(ctx, command_id, f"exec:{n}:refs", lambda: ids)
            if {k: int(x) for k, x in congelati.items()} != ids:
                esiti[n] = {"outcome": "blocked", "reasons": (REFERENCE_CHANGED,)}
                fermo = True
                continue
            prima = {}
            if s.intent == "activate_seller":
                prima = ledger.freeze(ctx, command_id, f"exec:{n}:seller_before",
                                      lambda: operations.seller_state(ctx, ids["property"], ids["contact"]))
            inp = operations.StepInput(
                command_id=command_id, ordinal=n, intent=s.intent, payload=s.payload, ids=ids,
                key=str(riga["client_request_id"]) if riga["client_request_id"] else None, now=adesso,
                confirm_similar=bool(conferma) and bool(set(conferma.get("reasons") or ())
                                                       & {policy.SIMILAR_BUILDING, policy.SIMILAR_UNIT}),
                accepted_duplicates=frozenset((conferma or {}).get("duplicates") or ()),
                accepted_hidden=bool((conferma or {}).get("hidden")), seller_before=prima,
                confirmed_reasons=tuple((conferma or {}).get("reasons") or ()))
            confermato = riga["decision"] == "ask"
            try:
                if operations.is_atomic(s.intent, s.refs):
                    ledger.run_step_atomic(ctx, command_id, n,
                                           lambda cur, _passo, inp=inp: operations.run_with_cursor(ctx, cur, inp),
                                           confirmed=confermato, describe=operations.error_code)
                else:
                    _run(ctx, command_id, n, lambda inp=inp: operations.run(ctx, inp), confermato)
                esiti[n] = {"outcome": "executed"}
            except ledger.LedgerError as exc:
                esiti[n] = {"outcome": "blocked", "reasons": (exc.code,)}
                fermo = True
            except Exception:  # noqa: BLE001 - registrato nel passo (failed / indeterminate)
                esiti[n] = {"outcome": None}
                fermo = True
            stati = {p["ordinal"]: p for p in ledger.get_command(ctx, command_id)["steps"]}
        ledger.finalize(ctx, command_id)
    return _report(ctx, command_id, esiti)


def retry(ctx, command_id: int, ordinal: int) -> dict:
    """Decisione esplicita dell'operatore: un passo `failed` torna `pending`
    (stessa chiave congelata). Mai automatico."""
    gate(ctx)
    return ledger.retry_step(ctx, command_id, ordinal)


def _report(ctx, command_id: int, esiti: dict[int, dict]) -> dict:
    comando = ledger.get_command(ctx, command_id)
    passi = []
    for p in comando["steps"]:
        e = esiti.get(p["ordinal"], {})
        stato = p["state"]
        esito = e.get("outcome") or {"succeeded": "done", "skipped": "skipped", "failed": "failed",
                                     "indeterminate": "indeterminate"}.get(stato, "pending")
        passi.append({"ordinal": p["ordinal"], "intent": p["intent"], "state": stato, "decision": p["decision"],
                      "outcome": esito, "reasons": tuple(e.get("reasons") or p["reasons"] or ()),
                      "result": dict(p["result"] or {}), "error": p["error_type"]})
    return {"command_id": command_id, "status": comando["status"], "steps": passi}
