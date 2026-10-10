"""STIMA Voice - Fase 2: il risolutore dei riferimenti, in sola lettura.

Dal `Plan` della Fase 1 ai fatti del CRM, con i permessi di chi parla:
  * per ogni riferimento («Fabio», «il trilocale di via Roma», «la palazzina
    di via Dante»): i candidati VISIBILI, quanti sono, se la corrispondenza e'
    esatta, e l'id quando e' uno solo;
  * per ogni creazione: i possibili doppioni (contatti visibili, contatti di
    un collega senza dati - D1, edifici e unita' simili del censimento);
  * se chi parla puo' assegnare ad altri agenti.

Ne esce un `Resolution` con i `Facts` per `voice.policy.decide` e i candidati
per le domande. Nessuna scrittura (repository.read_only), nessuna regola nuova
su chi vede cosa: contatti con il predicato del CRM, immobili ed edifici per
agenzia come nel CRM.

Regole di corrispondenza (deterministiche, provate dai test):
  * CONTATTO - "esatto" significa SOLO trovato per telefono o email
    (normalizzati come il CRM): un identificativo affidabile. Nome e cognome,
    anche completi, o la ragione sociale producono candidati NON esatti: con
    un solo candidato la politica chiede la conferma del contatto
    (`contact_confirmation_required`), con piu' candidati chiede di
    scegliere. Mai indovinare. Esclusi archiviati e Cestino, come le liste.
  * IMMOBILE - codice uguale = esatto; altrimenti via uguale (con civico =
    esatto, senza = non esatto), poi la via contenuta (non esatto); comune e
    tipo, se detti, restringono.
  * EDIFICIO - nome uguale, o via uguale (con civico = esatto), poi contenuta.
  * AGENTE - nome completo uguale = esatto; solo il nome = non esatto. Si
    cerca SOLO se chi parla puo' assegnare: a chi non puo', la politica blocca
    prima, e i nomi dei colleghi non servono.

Doppioni prima di una creazione:
  * contatto: stesso telefono/email fra i VISIBILI (anche archiviati, come
    `contact_lifecycle.possible_duplicates`), oppure - senza recapiti - nome
    e cognome uguali fra i visibili; D1: stesso telefono/email NON visibile
    = solo un segnale vero/falso, mai per nome (troppo debole, e direbbe di
    piu' di quanto serve);
  * edificio: `census._simili_edificio` (stesso indirizzo o catasto);
  * unita' in una palazzina gia' registrata: `census._simili_unita`;
  * unita' senza palazzina: stesso indirizzo con civico fra gli immobili vivi
    dell'agenzia (un avviso, mai un blocco).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import repository
from .planner import Plan, Ref, Step
from .policy import Facts, RefFacts


@dataclass(frozen=True)
class Candidate:
    """Cio' che una domanda puo' mostrare: solo dati che chi parla vede gia'."""
    id: int
    label: str
    kind: str


@dataclass(frozen=True)
class RefMatch:
    candidates: tuple[Candidate, ...]
    exact: bool

    @property
    def resolved_id(self) -> int | None:
        return self.candidates[0].id if len(self.candidates) == 1 else None

    @property
    def too_many(self) -> bool:
        return len(self.candidates) > repository.MAX_CANDIDATES


@dataclass
class Resolution:
    facts: Facts
    matches: dict[tuple[int, str], RefMatch] = field(default_factory=dict)
    #: create_contact / create_building / create_unit: i doppioni visibili.
    duplicates: dict[int, tuple[Candidate, ...]] = field(default_factory=dict)

    def resolved(self, ordinal: int, ref: str) -> int | None:
        m = self.matches.get((ordinal, ref))
        return m.resolved_id if m else None


# ---------------------------------------------------------------------------

def _contact_label(r: dict) -> str:
    nome = r.get("display_name") or " ".join(x for x in (r.get("first_name"), r.get("last_name")) if x) \
        or r.get("company_name") or f"Contatto {r['id']}"
    return nome


def _property_label(r: dict) -> str:
    luogo = ", ".join(x for x in (" ".join(y for y in (r.get("address"), r.get("civic_number")) if y), r.get("city")) if x)
    tipo = "censimento" if r.get("record_kind") == "census" else "commerciale"
    return f"{r.get('code') or r['id']} · {luogo or r.get('title') or 'senza indirizzo'} ({tipo})"


def _building_label(r: dict) -> str:
    luogo = ", ".join(x for x in (" ".join(y for y in (r.get("address"), r.get("civic_number")) if y), r.get("city")) if x)
    return r.get("name") or luogo or f"Edificio {r['id']}"


def _candidates(rows: list[dict], kind: str) -> tuple[Candidate, ...]:
    label = {"contact": _contact_label, "property": _property_label, "building": _building_label,
             "agent": lambda r: r.get("name") or f"Agente {r['id']}"}[kind]
    return tuple(Candidate(int(r["id"]), label(r), kind) for r in rows)


# --- singoli riferimenti ----------------------------------------------------

def _match_contact(ctx, cur, d: dict[str, Any]) -> RefMatch:
    if d.get("phone") or d.get("email"):
        trovati = repository.contacts_by_identity(ctx, cur, phone=d.get("phone"), email=d.get("email"))
        if trovati:
            return RefMatch(_candidates(trovati, "contact"), exact=True)
    # Recapito detto ma non trovato fra i visibili: si cerca per nome, ma
    # resta comunque una corrispondenza da confermare.
    if d.get("company_name"):
        trovati = repository.contacts_by_name(ctx, cur, company_name=d["company_name"])
        return RefMatch(_candidates(trovati, "contact"), exact=False)
    if d.get("first_name") or d.get("last_name"):
        trovati = repository.contacts_by_name(ctx, cur, first_name=d.get("first_name"), last_name=d.get("last_name"))
        return RefMatch(_candidates(trovati, "contact"), exact=False)
    return RefMatch((), exact=False)


def _match_property(ctx, cur, d: dict[str, Any]) -> RefMatch:
    if d.get("code"):
        trovati = repository.properties_by_code(ctx, cur, d["code"])
        if trovati:
            return RefMatch(_candidates(trovati, "property"), exact=True)
    if d.get("address"):
        filtri = {"civic_number": d.get("civic_number"), "city": d.get("city"), "property_type": d.get("property_type")}
        trovati = repository.properties_by_address(ctx, cur, address=d["address"], exact=True, **filtri)
        if trovati:
            return RefMatch(_candidates(trovati, "property"), exact=bool(d.get("civic_number")))
        trovati = repository.properties_by_address(ctx, cur, address=d["address"], exact=False, **filtri)
        return RefMatch(_candidates(trovati, "property"), exact=False)
    return RefMatch((), exact=False)


def _match_building(ctx, cur, d: dict[str, Any]) -> RefMatch:
    if d.get("name"):
        trovati = repository.buildings_by(ctx, cur, name=d["name"], city=d.get("city"))
        if trovati:
            return RefMatch(_candidates(trovati, "building"), exact=True)
    if d.get("address"):
        trovati = repository.buildings_by(ctx, cur, address=d["address"], civic_number=d.get("civic_number"),
                                          city=d.get("city"), exact=True)
        if trovati:
            return RefMatch(_candidates(trovati, "building"), exact=bool(d.get("civic_number")))
        trovati = repository.buildings_by(ctx, cur, address=d["address"], civic_number=d.get("civic_number"),
                                          city=d.get("city"), exact=False)
        return RefMatch(_candidates(trovati, "building"), exact=False)
    return RefMatch((), exact=False)


def _match_agent(ctx, cur, d: dict[str, Any], cache: dict) -> RefMatch:
    if "agents" not in cache:
        cache["agents"] = repository.assignable_agents(ctx, cur)
    cercato = " ".join((d.get("name") or "").lower().split())
    if not cercato:
        return RefMatch((), exact=False)
    completi = [a for a in cache["agents"] if " ".join((a.get("name") or "").lower().split()) == cercato]
    if completi:
        return RefMatch(_candidates(completi, "agent"), exact=True)
    per_nome = [a for a in cache["agents"] if (a.get("name") or "").lower().split()[:1] == cercato.split()[:1]]
    return RefMatch(_candidates(per_nome, "agent"), exact=False)


def _match(ctx, cur, ref: Ref, cache: dict) -> RefMatch:
    if ref.kind == "contact":
        return _match_contact(ctx, cur, ref.description)
    if ref.kind == "property":
        return _match_property(ctx, cur, ref.description)
    if ref.kind == "building":
        return _match_building(ctx, cur, ref.description)
    if ref.kind == "agent":
        return _match_agent(ctx, cur, ref.description, cache)
    raise ValueError(f"unknown reference kind {ref.kind!r}")


# --- doppioni prima di una creazione ----------------------------------------

def _contact_duplicates(ctx, cur, step: Step) -> tuple[tuple[Candidate, ...], bool]:
    p = step.payload
    visibili: list[dict] = []
    if p.get("phone") or p.get("email"):
        visibili = repository.contacts_by_identity(ctx, cur, phone=p.get("phone"), email=p.get("email"),
                                                   include_archived=True)
    elif p.get("company_name"):
        visibili = repository.contacts_by_name(ctx, cur, company_name=p["company_name"], include_archived=True)
    elif p.get("first_name") and p.get("last_name"):
        visibili = repository.contacts_by_name(ctx, cur, first_name=p["first_name"], last_name=p["last_name"],
                                               include_archived=True)
    nascosto = repository.hidden_contact_exists(ctx, cur, phone=p.get("phone"), email=p.get("email"))
    return _candidates(visibili, "contact"), nascosto


def _unit_duplicates(ctx, cur, step: Step, building_id: int | None) -> tuple[Candidate, ...]:
    p = step.payload
    if building_id is not None:
        simili = repository.similar_units(ctx, cur, {**p, "building_id": building_id})
        return tuple(Candidate(int(r["id"]), r.get("code") or r.get("title") or f"Immobile {r['id']}", "property") for r in simili)
    if p.get("address") and p.get("civic_number"):
        trovati = repository.properties_by_address(ctx, cur, address=p["address"], civic_number=p["civic_number"],
                                                  city=p.get("city"), exact=True)
        # Stesso indirizzo e civico: in una palazzina e' normale avere piu'
        # unita'; e' un doppione solo se coincidono anche piano e interno detti.
        for chiave in ("floor", "internal_number"):
            if p.get(chiave):
                trovati = [r for r in trovati if (r.get(chiave) or "").strip().lower() == str(p[chiave]).strip().lower()]
        return _candidates(trovati, "property")
    return ()


# ---------------------------------------------------------------------------

def resolve(plan: Plan, ctx) -> Resolution:
    """Risolve tutti i riferimenti e cerca i doppioni del piano, in UNA
    transazione di sola lettura. `ctx` e' il contesto reale dell'operatore
    (Fase 5: da `require_operator`); senza agenzia si rifiuta prima di
    leggere."""
    ctx.require_agency()
    refs: dict[tuple[int, str], RefFacts] = {}
    matches: dict[tuple[int, str], RefMatch] = {}
    duplicates: dict[int, tuple[Candidate, ...]] = {}
    duplicate_visible: dict[int, int] = {}
    duplicate_hidden: set[int] = set()
    similar_building: set[int] = set()
    similar_unit: set[int] = set()
    cache: dict = {}
    puo_assegnare = bool(ctx.may_assign_records)

    with repository.read_only() as cur:
        for step in plan.steps:
            for nome, ref in sorted(step.refs.items()):
                if ref.step is not None:
                    continue  # creato in questo vocale
                if ref.kind == "agent" and not puo_assegnare:
                    continue  # la politica blocca: nessuna lettura dei colleghi
                m = _match(ctx, cur, ref, cache)
                matches[(step.ordinal, nome)] = m
                refs[(step.ordinal, nome)] = RefFacts(len(m.candidates), exact=m.exact)

            if step.intent == "create_contact":
                visibili, nascosto = _contact_duplicates(ctx, cur, step)
                if visibili:
                    duplicates[step.ordinal] = visibili
                    duplicate_visible[step.ordinal] = len(visibili)
                if nascosto:
                    duplicate_hidden.add(step.ordinal)

            elif step.intent == "create_building":
                simili = repository.similar_buildings(ctx, cur, step.payload)
                if simili:
                    similar_building.add(step.ordinal)
                    duplicates[step.ordinal] = tuple(
                        Candidate(int(r["id"]), _building_label(r), "building") for r in simili)

            elif step.intent == "create_unit":
                edificio = step.refs.get("building")
                building_id = None
                if edificio is not None and edificio.step is None:
                    building_id = matches[(step.ordinal, "building")].resolved_id
                if edificio is None or edificio.step is None:
                    simili = _unit_duplicates(ctx, cur, step, building_id)
                    if simili:
                        similar_unit.add(step.ordinal)
                        duplicates[step.ordinal] = simili

    facts = Facts(refs=refs, duplicate_visible=duplicate_visible, duplicate_hidden=frozenset(duplicate_hidden),
                  similar_building=frozenset(similar_building), similar_unit=frozenset(similar_unit),
                  actor_may_assign=puo_assegnare)
    return Resolution(facts=facts, matches=matches, duplicates=duplicates)
