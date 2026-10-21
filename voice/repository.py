"""STIMA Voice - Fase 2: le sole letture che servono al risolutore.

Regole del modulo:
  * una sola connessione per risoluzione, in una transazione dichiarata
    `READ ONLY` al database: una scrittura accidentale fallisce in
    PostgreSQL, non dipende dalla disciplina del codice;
  * i CONTATTI si leggono SOLO con il predicato del CRM
    (`core.scope.scoped_source`): un agente vede i contatti assegnati a lui,
    titolare e amministratore tutta l'agenzia. Nessuna seconda copia della
    regola;
  * l'UNICA lettura dei contatti fuori da quel predicato e'
    `hidden_contact_exists` (decisione D1): restituisce un booleano e nulla
    piu' - nessun id, nome, recapito o conteggio;
  * IMMOBILI ed EDIFICI sono visibili a tutta l'agenzia (regola del CRM:
    `property/repository.list_properties`, `census.list_buildings`): si
    filtrano per agenzia, fuori dal Cestino e non archiviati;
  * gli avvisi di doppione su edifici e unita' sono quelli del censimento
    (`property.census._simili_edificio`, `_simili_unita`,
    `_duplicato_catastale`), chiamati con il nostro cursore e mai riscritti;
  * gli agenti assegnabili sono quelli dell'Agenda
    (`appointments.repository.agents`).
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from difflib import SequenceMatcher
from typing import Any

from appointments import repository as agenda_repository
from appointments.enums import MANDATE_REQUIRED_TYPES, SALE_MANDATE_STATES
from core.property_mandate import mandate_origin, mandate_state, signed_link_mandate
from core import building_trash, contact_trash, property_trash
from core.database import core_cursor
from core.normalization import normalize_email, normalize_phone
from core.scope import scoped_source
from property import census

from .dates import fold

MAX_CANDIDATES = 5


@contextmanager
def read_only():
    """Un cursore in una transazione READ ONLY, chiusa sempre senza commit."""
    with core_cursor() as (conn, cur):
        cur.execute("SET TRANSACTION READ ONLY")
        try:
            yield cur
        finally:
            conn.rollback()


# ---------------------------------------------------------------------------
# CONTATTI (con il predicato del CRM)
# ---------------------------------------------------------------------------

_CONTACT_COLUMNS = "c.id, c.display_name, c.first_name, c.last_name, c.company_name, c.status"


def _visible_contacts(ctx, cur, condition: str, params: list, *, include_archived: bool) -> list[dict]:
    source, scope_params = scoped_source(ctx, "contacts", "c")
    archiviati = "" if include_archived else " AND c.status <> 'archived'"
    cur.execute(
        f"SELECT {_CONTACT_COLUMNS} FROM {source} AND {contact_trash.live('c')}{archiviati} "
        f"AND ({condition}) ORDER BY c.id LIMIT %s",
        scope_params + params + [MAX_CANDIDATES + 1],
    )
    return [dict(r) for r in cur.fetchall()]


def contacts_by_identity(ctx, cur, *, phone: str | None = None, email: str | None = None,
                         include_archived: bool = False) -> list[dict]:
    """Contatti visibili con lo stesso telefono o la stessa email normalizzati
    (stessa normalizzazione del CRM)."""
    condizioni, params = [], []
    telefono, posta = normalize_phone(phone), normalize_email(email)
    if telefono:
        condizioni.append("c.phone_normalized = %s")
        params.append(telefono)
    if posta:
        condizioni.append("c.email_normalized = %s")
        params.append(posta)
    if not condizioni:
        return []
    return _visible_contacts(ctx, cur, " OR ".join(condizioni), params, include_archived=include_archived)


def contacts_by_name(ctx, cur, *, first_name: str | None = None, last_name: str | None = None,
                     company_name: str | None = None, include_archived: bool = False) -> list[dict]:
    """Contatti visibili con nome e/o cognome UGUALI (senza distinzione di
    maiuscole e spazi), o con la stessa ragione sociale."""
    condizioni, params = [], []
    if first_name:
        condizioni.append("lower(btrim(c.first_name)) = lower(btrim(%s))")
        params.append(first_name)
    if last_name:
        condizioni.append("lower(btrim(c.last_name)) = lower(btrim(%s))")
        params.append(last_name)
    if company_name:
        condizioni = ["lower(btrim(c.company_name)) = lower(btrim(%s))"]
        params = [company_name]
    if not condizioni:
        return []
    return _visible_contacts(ctx, cur, " AND ".join(condizioni), params, include_archived=include_archived)


def hidden_contact_exists(ctx, cur, *, phone: str | None = None, email: str | None = None) -> bool:
    """D1: esiste NELLA STESSA AGENZIA un contatto vivo con lo stesso
    recapito che il chiamante NON vede? Solo vero/falso.

    Per chi vede tutta l'agenzia la risposta e' sempre falso: non c'e' nulla
    di nascosto. La query non seleziona colonne dei contatti: `EXISTS`."""
    if ctx.sees_all_agency_records:
        return False
    telefono, posta = normalize_phone(phone), normalize_email(email)
    condizioni, params = [], []
    if telefono:
        condizioni.append("h.phone_normalized = %s")
        params.append(telefono)
    if posta:
        condizioni.append("h.email_normalized = %s")
        params.append(posta)
    if not condizioni:
        return False
    source, scope_params = scoped_source(ctx, "contacts", "v")
    cur.execute(
        f"SELECT EXISTS (SELECT 1 FROM contacts h WHERE h.agency_id = %s AND {contact_trash.live('h')} "
        f"AND ({' OR '.join(condizioni)}) "
        f"AND NOT EXISTS (SELECT 1 FROM {source} AND v.id = h.id)) AS hidden",
        [ctx.require_agency()] + params + scope_params,
    )
    return bool(cur.fetchone()["hidden"])


# ---------------------------------------------------------------------------
# IMMOBILI E EDIFICI (visibili a tutta l'agenzia, come nel CRM)
# ---------------------------------------------------------------------------

_PROPERTY_COLUMNS = ("p.id, p.code, p.title, p.property_type, p.record_kind, p.address, p.civic_number, "
                     "p.city, p.floor, p.internal_number, p.building_id")


def _live_property(alias: str = "p") -> str:
    return (f"{alias}.archived_at IS NULL AND {alias}.commercial_status <> 'archived' "
            f"AND {property_trash.live(alias)}")


def properties_by_code(ctx, cur, code: str) -> list[dict]:
    cur.execute(f"SELECT {_PROPERTY_COLUMNS} FROM properties p WHERE p.agency_id = %s AND {_live_property()} "
                "AND lower(btrim(p.code)) = lower(btrim(%s)) ORDER BY p.id LIMIT %s",
                (ctx.require_agency(), code, MAX_CANDIDATES + 1))
    return [dict(r) for r in cur.fetchall()]


def properties_by_address(ctx, cur, *, address: str, civic_number: str | None = None, city: str | None = None,
                          property_type: str | None = None, exact: bool = True) -> list[dict]:
    """Immobili vivi dell'agenzia all'indirizzo. `exact`: via uguale; senza,
    la via detta e' contenuta in quella registrata («Roma» in «Via Roma»)."""
    condizioni = [f"p.agency_id = %s", _live_property()]
    params: list[Any] = [ctx.require_agency()]
    if exact:
        condizioni.append("lower(btrim(p.address)) = lower(btrim(%s))")
        params.append(address)
    else:
        condizioni.append("p.address ILIKE %s")
        params.append(f"%{address.strip()}%")
    if civic_number:
        condizioni.append("lower(btrim(coalesce(p.civic_number, ''))) = lower(btrim(%s))")
        params.append(civic_number)
    if city:
        condizioni.append("lower(btrim(coalesce(p.city, ''))) = lower(btrim(%s))")
        params.append(city)
    if property_type:
        condizioni.append("p.property_type = %s")
        params.append(property_type)
    cur.execute(f"SELECT {_PROPERTY_COLUMNS} FROM properties p WHERE {' AND '.join(condizioni)} "
                "ORDER BY p.id LIMIT %s", params + [MAX_CANDIDATES + 1])
    return [dict(r) for r in cur.fetchall()]


_BUILDING_COLUMNS = "b.id, b.name, b.building_type, b.address, b.civic_number, b.city, b.units_declared"


def buildings_by(ctx, cur, *, name: str | None = None, address: str | None = None,
                 civic_number: str | None = None, city: str | None = None, exact: bool = True) -> list[dict]:
    condizioni = ["b.agency_id = %s", "b.archived_at IS NULL", building_trash.live("b")]
    params: list[Any] = [ctx.require_agency()]
    if name:
        condizioni.append("lower(btrim(coalesce(b.name, ''))) = lower(btrim(%s))")
        params.append(name)
    if address:
        if exact:
            condizioni.append("lower(btrim(coalesce(b.address, ''))) = lower(btrim(%s))")
            params.append(address)
        else:
            condizioni.append("b.address ILIKE %s")
            params.append(f"%{address.strip()}%")
    if civic_number:
        condizioni.append("lower(btrim(coalesce(b.civic_number, ''))) = lower(btrim(%s))")
        params.append(civic_number)
    if city:
        condizioni.append("lower(btrim(coalesce(b.city, ''))) = lower(btrim(%s))")
        params.append(city)
    if len(condizioni) == 3:
        return []
    cur.execute(f"SELECT {_BUILDING_COLUMNS} FROM buildings b WHERE {' AND '.join(condizioni)} "
                "ORDER BY b.id LIMIT %s", params + [MAX_CANDIDATES + 1])
    return [dict(r) for r in cur.fetchall()]


# --- avvisi del censimento, riusati -----------------------------------------

def similar_buildings(ctx, cur, data: dict) -> list[dict]:
    """`census._simili_edificio`: stessa chiave catastale o stesso indirizzo."""
    return census._simili_edificio(cur, ctx.require_agency(), data)


def similar_units(ctx, cur, data: dict) -> list[dict]:
    """`census._simili_unita`: stessa posizione nella palazzina."""
    return census._simili_unita(cur, ctx.require_agency(), data)


def cadastral_duplicate(ctx, cur, data: dict) -> dict | None:
    """`census._duplicato_catastale`: identita' catastale completa gia' censita."""
    return census._duplicato_catastale(cur, ctx.require_agency(), data)


# ---------------------------------------------------------------------------
# AGENTI (quelli dell'Agenda)
# ---------------------------------------------------------------------------

def assignable_agents(ctx, cur) -> list[dict]:
    return agenda_repository.agents(cur, ctx.require_agency())


def operator_name(ctx, cur, user_id: int | None) -> str | None:
    """V8-4: il nome di un operatore dell'agenzia (chi parla, o l'agente
    scelto), dallo stesso elenco dell'Agenda; None se non e' dell'agenzia."""
    if user_id is None:
        return None
    for a in agenda_repository.agents(cur, ctx.require_agency()):
        if int(a["id"]) == int(user_id):
            return a.get("name")
    return None


# ---------------------------------------------------------------------------
# V8-2: l'incarico di vendita, letto come lo legge l'Agenda
# ---------------------------------------------------------------------------

def requires_mandate(appointment_type: str | None) -> bool:
    """Il tipo di appuntamento esige un incarico (visita immobile, appuntamento
    di vendita): la lista e' quella dell'Agenda, non una copia."""
    return appointment_type in MANDATE_REQUIRED_TYPES


def usable_mandate(ctx, cur, property_id: int) -> bool:
    """L'immobile ha un incarico REALE, in corso o scaduto, fuori dal Cestino?
    Le STESSE funzioni di `appointments.service._controlla_vendita`: nessuna
    regola nuova, la voce legge quel che l'Agenda imporra' comunque."""
    immobile = agenda_repository.sale_mandate_property(cur, ctx.require_agency(), property_id)
    if immobile is None or immobile["deleted_at"] is not None:
        return False
    immobile["signed_mandate"] = signed_link_mandate(cur, property_id)
    return mandate_origin(immobile) is not None and mandate_state(immobile) in SALE_MANDATE_STATES


# ---------------------------------------------------------------------------
# V8-3: nominativi con piccole imprecisioni di pronuncia, e il ruolo
# ---------------------------------------------------------------------------

#: Soglie fisse della somiglianza (0..1, `difflib.SequenceMatcher`): un
#: cognome «Rosi» per «Rossi» = 0.89; «Bianco» per «Bianchi» = 0.77;
#: «Verde» per «Verdi» = 0.80; «Galli» per «Gallo» = 0.80. Sotto 0.75 non e'
#: la stessa persona. Un test le fissa.
NAME_SIMILARITY = 0.75
#: Al massimo quanti contatti visibili si confrontano in Python (il prefiltro
#: SQL e' l'iniziale di una delle parole dette, su nome, cognome o nome
#: completo; dai piu' recenti). Collaudo 3: 200 in ordine di id poteva
#: escludere i contatti nuovi di un'agenzia grande.
SIMILARITY_SCAN = 2000
#: Collaudo iPhone del 21/10: un nome detto a meta' («Ruvo» per «Ruvolo»,
#: «Cens» per «Censori»): almeno PREFIX_MIN_LETTERS lettere che aprono il nome
#: registrato valgono PREFIX_SIMILARITY. Resta un CANDIDATO da confermare.
PREFIX_MIN_LETTERS = 3
PREFIX_SIMILARITY = 0.85


def name_similarity(said: str | None, stored: str | None) -> float:
    """Somiglianza fra due nomi, senza accenti ne' maiuscole; 0 se manca uno dei due."""
    a, b = fold(said or ""), fold(stored or "")
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    simile = SequenceMatcher(None, a, b).ratio()
    if len(a) >= PREFIX_MIN_LETTERS and b.startswith(a):
        simile = max(simile, PREFIX_SIMILARITY)
    return simile


# ---------------------------------------------------------------------------
# Collaudo 3 (21/10): cognomi pronunciati o trascritti male, in generale
# ---------------------------------------------------------------------------
#
# Perche' «Francesco Ruvo» / «Francesco Rivo» non trovava Francesco Ruvolo:
#   * si confrontavano SOLO `first_name` e `last_name`, mentre nel CRM un
#     contatto persona puo' avere solo il nome completo (`display_name`): e'
#     il nome che mostrano Contatti e Agenda (`COALESCE(display_name, ...)`);
#   * «Rivo» contro «Ruvolo» vale 0.60 (sotto 0.75): una vocale sbagliata in
#     un cognome detto a meta' non bastava.
# La regola, generale (nessun alias): le parole dette e quelle registrate si
# confrontano a coppie, in qualunque ordine (nome e cognome invertiti), su
# ogni forma del nome del contatto; una parola e' simile se lo e' per
# `name_similarity` oppure se ha le stesse consonanti, o le prime («Rivo» ->
# r-v, «Ruvolo» -> r-v-l): le vocali sono la parte che il riconoscimento
# sbaglia di piu'. Il risultato e' sempre un CANDIDATO da confermare, letto
# con il predicato del CRM (chi non si vede non compare).

_PAROLE = re.compile(r"[a-z]+")
_VOCALI = frozenset("aeiouy")
#: Stesse consonanti (o le prime): «Rivo» per «Ruvolo», «Rosi» per «Rossi».
SKELETON_SIMILARITY = 0.78


def name_tokens(*parti: str | None) -> list[str]:
    """Le parole di un nome, senza accenti ne' maiuscole; via le iniziali puntate."""
    return [t for p in parti for t in _PAROLE.findall(fold(p or "")) if len(t) >= 2]


def _scheletro(parola: str) -> str:
    """L'iniziale e poi le sole consonanti, senza doppie: «ruvolo» -> «rvl»."""
    uscita = parola[:1]
    for ch in parola[1:]:
        if ch not in _VOCALI and ch != uscita[-1]:
            uscita += ch
    return uscita


def token_similarity(said: str, stored: str) -> float:
    """Somiglianza fra una parola detta e una registrata (gia' ripiegate)."""
    simile = name_similarity(said, stored)
    if len(said) >= PREFIX_MIN_LETTERS:
        a, b = _scheletro(said), _scheletro(stored)
        if len(a) >= 2 and b.startswith(a):
            simile = max(simile, SKELETON_SIMILARITY)
    return simile


def _abbina(dette: list[str], registrate: list[str]) -> tuple[float, float] | None:
    """Ogni parola detta con una registrata diversa (la coppia piu' simile per
    prima). (copertura, media) o None. Copertura 1 = ogni parola detta
    trovata; meno di 1 solo se il registrato ha meno parole (es. solo il
    cognome) e sono state trovate tutte."""
    coppie = sorted(((token_similarity(a, b), i, j) for i, a in enumerate(dette) for j, b in enumerate(registrate)),
                    reverse=True)
    prese_a, prese_b, punti = set(), set(), []
    for simile, i, j in coppie:
        if simile < NAME_SIMILARITY or i in prese_a or j in prese_b:
            continue
        prese_a.add(i)
        prese_b.add(j)
        punti.append(simile)
    if not punti or len(punti) < min(len(dette), len(registrate)):
        return None
    return len(punti) / len(dette), sum(punti) / len(punti)


def person_match(dette: list[str], *forme: list[str]) -> tuple[float, float] | None:
    """La migliore fra le forme del nome registrato (nome + cognome, nome completo...)."""
    risultati = [r for f in forme if f for r in [_abbina(dette, f)] if r is not None]
    return max(risultati) if risultati else None


def _solo_i_completi(punteggi: list) -> list:
    """Se qualcuno corrisponde a TUTTE le parole dette, quelli parziali (es. un
    contatto con il solo nome «Francesco» per «Francesco Ruvo») non si propongono."""
    if any(p[0][0] >= 1.0 for p in punteggi):
        return [p for p in punteggi if p[0][0] >= 1.0]
    return punteggi


def _prefiltro_iniziali(alias: str, dette: list[str]) -> tuple[str, list]:
    """SQL: il contatto ha almeno una parola (nome, cognome o nome completo)
    che comincia con l'iniziale di una parola detta."""
    iniziali = sorted({t[0] for t in dette})
    return (f"(lower(left(btrim({alias}.first_name), 1)) = ANY(%s) "
            f"OR lower(left(btrim({alias}.last_name), 1)) = ANY(%s) "
            f"OR lower({alias}.display_name) ~ %s)",
            [iniziali, iniziali, f"(^|[^a-z])[{''.join(iniziali)}]"])


def _forme_contatto(r: dict, prefisso: str = "") -> list[list[str]]:
    return [name_tokens(r.get(f"{prefisso}first_name"), r.get(f"{prefisso}last_name")),
            name_tokens(r.get(f"{prefisso}display_name"))]


def contacts_by_similar_name(ctx, cur, *, first_name: str | None = None, last_name: str | None = None,
                             include_archived: bool = False) -> list[dict]:
    """Contatti visibili il cui nome SOMIGLIA a quello detto (vedi sopra), i
    piu' somiglianti per primi. Mai esatti: chi li usa chiede sempre la conferma."""
    dette = name_tokens(first_name, last_name)
    if not dette:
        return []
    condizione, params = _prefiltro_iniziali("c", dette)
    source, scope_params = scoped_source(ctx, "contacts", "c")
    archiviati = "" if include_archived else " AND c.status <> 'archived'"
    cur.execute(
        f"SELECT {_CONTACT_COLUMNS} FROM {source} AND {contact_trash.live('c')}{archiviati} "
        f"AND {condizione} ORDER BY c.id DESC LIMIT %s",
        scope_params + params + [SIMILARITY_SCAN],
    )
    punteggi = []
    for r in cur.fetchall():
        r = dict(r)
        punto = person_match(dette, *_forme_contatto(r))
        if punto is not None:
            punteggi.append((punto, -int(r["id"]), r))
    punteggi = _solo_i_completi(punteggi)
    punteggi.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [r for _, _, r in punteggi[:MAX_CANDIDATES + 1]]


#: Il ruolo detto -> i ruoli del CRM che lo soddisfano (contact_roles vivi,
#: oppure il collegamento proprietario/venditore a un immobile, oppure una
#: richiesta d'acquisto aperta). Si PREFERISCE, non si esclude.
_ROLE_FAMILIES = {
    "owner": ("owner", "seller"), "seller": ("owner", "seller"),
    "buyer": ("buyer", "prospect"),
}


def contacts_with_role(ctx, cur, contact_ids: list[int], role: str | None) -> set[int]:
    """Fra i contatti dati, quelli che hanno il ruolo detto (famiglia di
    ruoli sopra). Senza ruolo o senza contatti: insieme vuoto."""
    if not role or not contact_ids:
        return set()
    famiglia = _ROLE_FAMILIES.get(role, (role,))
    cur.execute(
        "SELECT c.id FROM contacts c WHERE c.id = ANY(%s) AND ("
        "EXISTS (SELECT 1 FROM contact_roles r WHERE r.contact_id = c.id AND r.role = ANY(%s) "
        "        AND (r.valid_to IS NULL OR r.valid_to >= NOW()))"
        " OR EXISTS (SELECT 1 FROM property_contacts pc WHERE pc.contact_id = c.id AND pc.role = ANY(%s))"
        + (" OR EXISTS (SELECT 1 FROM buy_requests b WHERE b.contact_id = c.id AND b.status IN ('draft', 'active', 'paused'))"
           if role == "buyer" else "")
        + ")",
        (list(contact_ids), list(famiglia), list(famiglia)))
    return {int(r["id"]) for r in cur.fetchall()}


# ---------------------------------------------------------------------------
# V8-0: gli immobili di un PROPRIETARIO («la casa di Francesco Ruvolo»)
# ---------------------------------------------------------------------------

_OWNER_ROLES = ("owner", "seller")


def properties_by_owner(ctx, cur, *, first_name: str | None = None, last_name: str | None = None,
                        company_name: str | None = None) -> list[dict]:
    """Immobili vivi dell'agenzia il cui proprietario (o venditore) ha il nome
    detto: uguale, o - per piccole imprecisioni - simile (stessa iniziale del
    cognome, NAME_SIMILARITY). Come la ricerca degli incarichi dell'Agenda, il
    proprietario si cerca nell'agenzia; l'immobile resta da confermare."""
    dette, ditta = name_tokens(first_name, last_name), fold(company_name or "")
    if not (dette or ditta):
        return []
    if ditta:
        filtro, params = "lower(btrim(c.company_name)) = %s", [ditta]
    else:
        filtro, params = _prefiltro_iniziali("c", dette)
    cur.execute(
        f"SELECT {_PROPERTY_COLUMNS}, c.first_name AS owner_first_name, c.last_name AS owner_last_name, "
        "c.company_name AS owner_company_name, c.display_name AS owner_display_name "
        "FROM properties p JOIN property_contacts pc ON pc.property_id = p.id AND pc.role = ANY(%s) "
        "JOIN contacts c ON c.id = pc.contact_id "
        f"WHERE p.agency_id = %s AND {_live_property()} AND c.agency_id = p.agency_id AND {filtro} "
        "ORDER BY p.id DESC LIMIT %s",
        [list(_OWNER_ROLES), ctx.require_agency(), *params, SIMILARITY_SCAN])
    migliori: dict[int, tuple] = {}
    for r in cur.fetchall():
        r = dict(r)
        punto = (1.0, 1.0) if ditta else person_match(dette, *_forme_contatto(r, "owner_"))
        if punto is None:
            continue
        r["owner_name"] = r.get("owner_display_name") or " ".join(
            x for x in (r.get("owner_first_name"), r.get("owner_last_name")) if x) or r.get("owner_company_name")
        if r["id"] not in migliori or punto > migliori[r["id"]][0]:
            migliori[r["id"]] = (punto, r)
    ordinati = sorted(_solo_i_completi(list(migliori.values())), key=lambda x: (x[0], x[1]["id"]), reverse=True)
    return [r for _, r in ordinati[:MAX_CANDIDATES + 1]]
