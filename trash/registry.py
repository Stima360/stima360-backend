"""CESTINO-GENERALE-1 - il registro delle categorie del Cestino unico.

Ogni categoria dichiara, in un posto solo:
  * la tabella e l'alias, e la sorgente normalizzata per l'elenco unificato
    (etichetta, riga descrittiva, stato, testo di ricerca, dati in piu');
  * la regola di VISIBILITA' per l'agenzia di chi chiede: la stessa del suo
    elenco esistente (non una nuova), riscritta qui come predicato SQL e
    verificata con una prova di parita';
  * il collegamento alla scheda (o None se la scheda non si apre dal Cestino).

Le categorie che arriveranno nelle fasi successive (attivita', task, lead,
appuntamenti, acquisizioni, vendite, proposte, stime) si aggiungono qui con
il loro adattatore, senza toccare la pagina ne' l'API.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.scope import scoped_predicate
from operator_auth import permissions

_NOME_CONTATTO = ("COALESCE(NULLIF(BTRIM({a}.display_name), ''),"
                  " NULLIF(BTRIM(CONCAT_WS(' ', {a}.first_name, {a}.last_name)), ''),"
                  " NULLIF(BTRIM({a}.company_name), ''))")


def _gestisce_agenzia(ctx) -> bool:
    return permissions.sees_all_agency_records(getattr(ctx, "role", None), getattr(ctx, "is_platform_admin", False))


@dataclass(frozen=True)
class Categoria:
    kind: str                     # = record_lifecycle_events.entity_type
    table: str
    alias: str
    label_it: str                 # nome della categoria (filtri, contatori)
    select: str                   # le colonne normalizzate (alias della tabella)
    extra_where: str              # condizioni proprie (oltre a deleted_at IS NOT NULL)
    tenant_visibility: Callable   # (ctx) -> (predicato, params)
    href: str | None              # modello del collegamento alla scheda ("{id}")


# --- regole di visibilita' (le stesse degli elenchi esistenti) ----------------

def _vis_property(ctx):
    """property/lifecycle.list_trash: owner/admin/platform admin in acting
    tutto; agent cio' che ha spostato lui; ogni altro ruolo nulla."""
    agenzia = ctx.require_agency()
    if _gestisce_agenzia(ctx):
        return "p.agency_id = %s", [agenzia]
    if getattr(ctx, "role", None) == "agent":
        return "p.agency_id = %s AND p.deleted_by_user_id = %s", [agenzia, getattr(ctx, "user_id", None)]
    return "FALSE", []


def _vis_contact(ctx):
    """core/contact_lifecycle.list_trash: lo scope CORE (un agent solo i
    contatti assegnati a lui) e, se non gestisce l'agenzia, solo cio' che ha
    spostato lui."""
    ctx.require_agency()
    predicato, params = scoped_predicate(ctx, "contacts", "c")
    if not _gestisce_agenzia(ctx):
        predicato += " AND c.deleted_by_user_id = %s"
        params = [*params, getattr(ctx, "user_id", None)]
    return predicato, params


def _vis_building(ctx):
    """property/building_deletion.list_trash_groups: owner/admin tutto;
    agent gli edifici spostati da lui o per cui ha chiesto l'eliminazione;
    ogni altro ruolo nulla."""
    agenzia = ctx.require_agency()
    if _gestisce_agenzia(ctx):
        return "b.agency_id = %s", [agenzia]
    if getattr(ctx, "role", None) == "agent":
        io = getattr(ctx, "user_id", None)
        return ("b.agency_id = %s AND (b.deleted_by_user_id = %s OR EXISTS (SELECT 1 FROM building_deletion_requests q "
                "WHERE q.building_id = b.id AND q.requested_by_user_id = %s))"), [agenzia, io, io]
    return "FALSE", []


def _vis_buy_request(ctx):
    """buy/lifecycle.list_trash: owner/admin tutto; ogni altro ruolo cio' che
    ha spostato lui."""
    agenzia = ctx.require_agency()
    if _gestisce_agenzia(ctx):
        return "r.agency_id = %s", [agenzia]
    return "r.agency_id = %s AND r.deleted_by_user_id = %s", [agenzia, getattr(ctx, "user_id", None)]


# --- le categorie ---------------------------------------------------------------

PROPERTY = Categoria(
    kind="property", table="properties", alias="p", label_it="Immobili",
    select="""'property'::text AS kind, p.id, p.agency_id,
              COALESCE(NULLIF(BTRIM(p.code), ''), '#' || p.id) AS label,
              CONCAT_WS(' · ', NULLIF(BTRIM(p.title), ''),
                        NULLIF(BTRIM(CONCAT_WS(' ', p.address, p.civic_number)), ''), NULLIF(BTRIM(p.city), '')) AS line,
              p.commercial_status AS status, p.deleted_at, p.deleted_reason, p.deleted_by_user_id,
              jsonb_build_object('archived', p.archived_at IS NOT NULL,
                                 'record_kind', to_jsonb(p) ->> 'record_kind',
                                 'building_id', p.building_id) AS extra,
              lower(CONCAT_WS(' ', p.code, p.title, p.address, p.civic_number, p.city, p.province, p.id::text)) AS search""",
    # le unita' di un edificio nel Cestino stanno nella sua card (categoria Edifici)
    extra_where=("NOT EXISTS (SELECT 1 FROM buildings bx WHERE bx.id = p.building_id "
                 "AND (to_jsonb(bx) ->> 'deleted_at') IS NOT NULL)"),
    tenant_visibility=_vis_property, href=None,
)

CONTACT = Categoria(
    kind="contact", table="contacts", alias="c", label_it="Contatti",
    select=f"""'contact'::text AS kind, c.id, c.agency_id,
              COALESCE({_NOME_CONTATTO.format(a='c')}, 'Contatto #' || c.id) AS label,
              CONCAT_WS(' · ', NULLIF(BTRIM(c.email), ''), NULLIF(BTRIM(c.phone), '')) AS line,
              c.status, c.deleted_at, c.deleted_reason, c.deleted_by_user_id,
              jsonb_build_object('contact_type', c.contact_type, 'archived', c.archived_at IS NOT NULL) AS extra,
              lower(CONCAT_WS(' ', c.display_name, c.first_name, c.last_name, c.company_name, c.email, c.phone,
                              c.id::text)) AS search""",
    extra_where="TRUE", tenant_visibility=_vis_contact, href="#/contatti/{id}",
)

BUILDING = Categoria(
    kind="building", table="buildings", alias="b", label_it="Edifici",
    select="""'building'::text AS kind, b.id, b.agency_id,
              COALESCE(NULLIF(BTRIM(b.name), ''),
                       NULLIF(BTRIM(CONCAT_WS(' ', b.address, b.civic_number)), ''), 'Edificio #' || b.id) AS label,
              CONCAT_WS(' · ', NULLIF(BTRIM(CONCAT_WS(' ', b.address, b.civic_number)), ''), NULLIF(BTRIM(b.city), ''))
                AS line,
              b.census_status AS status, b.deleted_at, b.deleted_reason, b.deleted_by_user_id,
              jsonb_build_object('units_total', (SELECT count(*) FROM properties ux WHERE ux.building_id = b.id),
                                 'building_type', b.building_type) AS extra,
              lower(CONCAT_WS(' ', b.name, b.address, b.civic_number, b.city, b.province, b.id::text)) AS search""",
    extra_where="TRUE", tenant_visibility=_vis_building, href="#/edifici/{id}",
)

BUY_REQUEST = Categoria(
    kind="buy_request", table="buy_requests", alias="r", label_it="Richieste acquirente",
    select=f"""'buy_request'::text AS kind, r.id, r.agency_id,
              COALESCE(NULLIF(BTRIM(r.title), ''), 'Richiesta #' || r.id) AS label,
              COALESCE({_NOME_CONTATTO.format(a='rc')}, '') AS line,
              r.status, r.deleted_at, r.deleted_reason, r.deleted_by_user_id,
              jsonb_build_object('contact_id', r.contact_id,
                                 'contact_in_trash', (to_jsonb(rc) ->> 'deleted_at') IS NOT NULL,
                                 'budget_max', r.budget_max) AS extra,
              lower(CONCAT_WS(' ', r.title, rc.display_name, rc.first_name, rc.last_name, rc.company_name,
                              rc.email, rc.phone, r.id::text)) AS search""",
    extra_where="TRUE", tenant_visibility=_vis_buy_request, href="#/acquirenti/{id}",
)

CATEGORIE: dict[str, Categoria] = {c.kind: c for c in (PROPERTY, CONTACT, BUILDING, BUY_REQUEST)}

#: le FROM delle categorie (la richiesta acquirente porta con se' il suo contatto)
FROM = {
    "property": "properties p",
    "contact": "contacts c",
    "building": "buildings b",
    "buy_request": "buy_requests r JOIN contacts rc ON rc.id = r.contact_id",
}


def href(kind: str, entity_id: int) -> str | None:
    modello = CATEGORIE[kind].href
    return modello.format(id=int(entity_id)) if modello else None
