"""STIMA Voice - la politica: per ogni passo del piano, `auto`, `ask` o
`blocked`. Deterministica, pura, senza eccezioni nascoste.

Ingressi:
  * il `Plan` (voice.planner), con le `issues` gia' accertate;
  * i `Facts`: cio' che il risolutore (Fase 2) ha trovato nel CRM con i
    permessi dell'agente - candidati per ogni riferimento, possibili doppioni,
    conflitti d'agenda, edifici simili. In Fase 1 li forniscono i test;
  * le `Settings` dell'agenzia: la modalita' (`review`, `assisted`, `auto`) e
    gli intenti ammessi in automatico. La modalita' `review` esiste SOLO per
    la misura del collaudo: il comportamento finale e' `auto`.

Ordine di valutazione, dal piu' severo: blocchi, poi domande, poi
l'automatico; infine la modalita' puo' solo spostare da `auto` a `ask`, mai
il contrario. I passi che dipendono da un passo in domanda aspettano la
risposta; quelli che dipendono da un passo bloccato sono bloccati.

Perche' una regola e' una domanda e non un'esecuzione: ogni `reason` e' un
codice stabile e un test lo fissa. Nessuna regola qui legge il database.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from . import dates
from .planner import (
    AGENT_NAMED, BUILDING_LOCATION_MISSING, CONTACT_IDENTITY_WEAK, INVALID_PAYLOAD,
    RECORD_KIND_CONFLICT, RECORD_KIND_UNKNOWN, RECORD_KIND_UNVERIFIED, TASK_WITHOUT_CONTACT,
    TOO_MANY_COMMANDS, UNITS_INVENTED, UNSUPPORTED, Plan, Step,
)

Decision = Literal["auto", "ask", "blocked"]
Mode = Literal["review", "assisted", "auto"]

#: Issue del pianificatore che bloccano: non c'e' domanda che le sciolga.
BLOCKING_ISSUES = frozenset({UNSUPPORTED, UNITS_INVENTED, INVALID_PAYLOAD, TOO_MANY_COMMANDS})
#: Issue del pianificatore che chiedono.
ASKING_ISSUES = frozenset({
    CONTACT_IDENTITY_WEAK, RECORD_KIND_UNKNOWN, RECORD_KIND_UNVERIFIED, RECORD_KIND_CONFLICT,
    BUILDING_LOCATION_MISSING, TASK_WITHOUT_CONTACT, AGENT_NAMED,
    dates.UNRECOGNIZED_DATE, dates.UNRECOGNIZED_TIME, dates.AMBIGUOUS_HOUR, dates.AMBIGUOUS_WEEK,
    dates.NONEXISTENT_LOCAL, dates.DOUBLE_LOCAL, dates.PAST,
})

# Motivi aggiunti dalla politica (stabili).
REF_NOT_FOUND = "reference_not_found"
REF_AMBIGUOUS = "reference_ambiguous"
REF_FUZZY = "reference_fuzzy"                   # trovato uno, ma non per recapito o nome completo
DUPLICATE_VISIBLE = "possible_duplicate_contact"
DUPLICATE_HIDDEN = "possible_duplicate_hidden"  # D1: esiste, ma non lo puoi vedere
SIMILAR_BUILDING = "similar_building_found"
CADASTRAL_DUPLICATE = "cadastral_duplicate"
AGENDA_CONFLICT = "agenda_conflict"
AGENT_CANNOT_ASSIGN = "agent_cannot_assign"
DEPENDS_ON_QUESTION = "depends_on_question"
DEPENDS_ON_BLOCKED = "depends_on_blocked"
MODE_REVIEW = "mode_review"
MODE_ASSISTED = "mode_assisted"
NOTE_TARGET_NOT_EXACT = "note_target_not_exact"

#: Gli intenti che in modalita' `assisted` partono per primi (ordine di
#: attivazione progressiva della roadmap, Fase 7c).
DEFAULT_ASSISTED_INTENTS = frozenset({"add_note", "add_task"})


@dataclass(frozen=True)
class RefFacts:
    """Cosa il risolutore ha trovato per un riferimento. `exact` = trovato
    per telefono/email/codice o per nome e cognome completi."""
    candidates: int
    exact: bool = False


@dataclass(frozen=True)
class Facts:
    refs: dict[tuple[int, str], RefFacts] = field(default_factory=dict)
    #: create_contact: quanti contatti VISIBILI condividono telefono o email.
    duplicate_visible: dict[int, int] = field(default_factory=dict)
    #: create_contact: esiste un contatto con lo stesso recapito NON visibile
    #: (altro agente). Solo vero/falso: nessun dato di chi non si puo' vedere.
    duplicate_hidden: frozenset[int] = frozenset()
    similar_building: frozenset[int] = frozenset()
    cadastral_duplicate: frozenset[int] = frozenset()
    agenda_conflict: frozenset[int] = frozenset()
    actor_may_assign: bool = False


@dataclass(frozen=True)
class Settings:
    mode: Mode = "auto"
    auto_intents: frozenset[str] = DEFAULT_ASSISTED_INTENTS
    #: D1: il controllo riservato dei doppioni fra agenti.
    hidden_duplicate_check: bool = True


@dataclass(frozen=True)
class StepDecision:
    ordinal: int
    intent: str
    decision: Decision
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PlanDecision:
    steps: tuple[StepDecision, ...]

    @property
    def auto(self) -> tuple[int, ...]:
        return tuple(s.ordinal for s in self.steps if s.decision == "auto")

    @property
    def asks(self) -> tuple[int, ...]:
        return tuple(s.ordinal for s in self.steps if s.decision == "ask")

    @property
    def blocked(self) -> tuple[int, ...]:
        return tuple(s.ordinal for s in self.steps if s.decision == "blocked")

    @property
    def needs_answers(self) -> bool:
        return bool(self.asks)

    def of(self, ordinal: int) -> StepDecision:
        return self.steps[ordinal - 1]


def _ref_reasons(step: Step, facts: Facts) -> list[str]:
    motivi: list[str] = []
    for nome, ref in sorted(step.refs.items()):
        if ref.step is not None:
            continue  # creato in questo stesso vocale: univoco per costruzione
        if ref.kind == "agent" and not facts.actor_may_assign:
            continue  # bloccato prima: AGENT_CANNOT_ASSIGN
        trovato = facts.refs.get((step.ordinal, nome))
        if trovato is None or trovato.candidates == 0:
            motivi.append(REF_NOT_FOUND)
        elif trovato.candidates > 1:
            motivi.append(REF_AMBIGUOUS)
        elif not trovato.exact:
            motivi.append(REF_FUZZY if step.intent != "add_note" else NOTE_TARGET_NOT_EXACT)
    return motivi


def decide_step(step: Step, facts: Facts, settings: Settings) -> StepDecision:
    blocchi = sorted({i.code for i in step.issues if i.code in BLOCKING_ISSUES})
    if step.intent == "create_appointment" and "agent" in step.refs and not facts.actor_may_assign:
        blocchi.append(AGENT_CANNOT_ASSIGN)
    if blocchi:
        return StepDecision(step.ordinal, step.intent, "blocked", tuple(blocchi))

    domande = sorted({i.code for i in step.issues if i.code in ASKING_ISSUES})
    if step.intent == "create_appointment" and "agent" in step.refs and facts.actor_may_assign:
        domande = [d for d in domande if d != AGENT_NAMED]  # puo' assegnare: resta solo da risolvere il nome
    domande += _ref_reasons(step, facts)
    if step.intent == "create_contact":
        if facts.duplicate_visible.get(step.ordinal, 0) > 0:
            domande.append(DUPLICATE_VISIBLE)
        if settings.hidden_duplicate_check and step.ordinal in facts.duplicate_hidden:
            domande.append(DUPLICATE_HIDDEN)
    if step.intent == "create_building" and step.ordinal in facts.similar_building:
        domande.append(SIMILAR_BUILDING)
    if step.intent == "create_unit" and step.ordinal in facts.cadastral_duplicate:
        domande.append(CADASTRAL_DUPLICATE)
    if step.intent == "create_appointment" and step.ordinal in facts.agenda_conflict:
        domande.append(AGENDA_CONFLICT)
    if domande:
        return StepDecision(step.ordinal, step.intent, "ask", tuple(dict.fromkeys(domande)))

    if settings.mode == "review":
        return StepDecision(step.ordinal, step.intent, "ask", (MODE_REVIEW,))
    if settings.mode == "assisted" and step.intent not in settings.auto_intents:
        return StepDecision(step.ordinal, step.intent, "ask", (MODE_ASSISTED,))
    return StepDecision(step.ordinal, step.intent, "auto", ())


def decide(plan: Plan, facts: Facts, settings: Settings = Settings()) -> PlanDecision:
    decisioni: dict[int, StepDecision] = {}
    for step in plan.steps:
        propria = decide_step(step, facts, settings)
        motivi = list(propria.reasons)
        esito: Decision = propria.decision
        for dipendenza in step.depends_on:
            padre = decisioni[dipendenza]
            if padre.decision == "blocked":
                esito, motivi = "blocked", [DEPENDS_ON_BLOCKED]
                break
            if padre.decision == "ask" and esito != "blocked":
                esito = "ask"
                motivi.append(DEPENDS_ON_QUESTION)
        decisioni[step.ordinal] = StepDecision(step.ordinal, step.intent, esito, tuple(dict.fromkeys(motivi)))
    return PlanDecision(tuple(decisioni[s.ordinal] for s in plan.steps))
