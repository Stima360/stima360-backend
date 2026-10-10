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

from contextlib import contextmanager
from typing import Any

from appointments import repository as agenda_repository
from core import building_trash, contact_trash, property_trash
from core.database import core_cursor
from core.normalization import normalize_email, normalize_phone
from core.scope import scoped_source
from property import census

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
