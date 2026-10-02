"""CRM-OPS-4 - lo storico interazioni dell'immobile.

UNA fonte: `activities` (il registro canonico del CRM), con `property_id`
(migration 082). La scheda Immobile e la scheda Incarico chiamano gli stessi
endpoint e leggono le stesse righe: nessuna copia, nessuna tabella per vista.
Un'interazione con un referente compare anche nel tab Attivita' del contatto
e nella vista Attivita', perche' e' la stessa riga.

Cosa NON fa:
  * non scrive audit (`acquisition_events`, `appointment_events`, storici
    dell'immobile): una nota non e' un cambio di stato;
  * non crea appuntamenti: l'Agenda resta la sola fonte degli appuntamenti;
  * non accetta data/ora dal client: `occurred_at` e' il NOW() del database,
    l'autore e' l'operatore della sessione;
  * non modifica e non cancella: il registro e' append-only da questa API.
"""
from __future__ import annotations

from core import repository as core_repository
from core.database import core_cursor
from core.exceptions import NotFoundError, ValidationError
from core.scope import scoped_predicate

#: I tipi che un operatore registra a mano: un sottoinsieme ESATTO di
#: `core.enums.ACTIVITY_TYPES`, gli stessi del dialog "Nuova attivita'"
#: (valuation/status_change/system li scrivono processi automatici).
INTERACTION_TYPES = ("call", "meeting", "note", "email", "whatsapp")
INTERACTION_LABELS_IT = {
    "call": "Telefonata", "meeting": "Incontro", "note": "Nota",
    "email": "Email", "whatsapp": "WhatsApp / Messaggio",
}
#: Da dove e' stata registrata: scheda Immobile o scheda Incarico. Solo
#: contesto, in `activities.metadata`: la riga e' una sola.
CONTEXTS = ("property", "mandate")
MAX_NOTE = 5000
REFERENTE_NON_COLLEGATO = "Il referente non e' collegato a questo immobile"
SENZA_INCARICO = "L'immobile non ha un incarico"
MAX_LIST = 200

NOME_OPERATORE = ("COALESCE(NULLIF(BTRIM(CONCAT_WS(' ', {a}.first_name, {a}.last_name)), ''),"
                  " split_part({a}.email, '@', 1))")
NOME_CONTATTO = ("COALESCE({a}.display_name,"
                 " NULLIF(BTRIM(CONCAT_WS(' ', {a}.first_name, {a}.last_name)), ''),"
                 " {a}.company_name)")


def options() -> dict:
    return {"types": [{"value": t, "label": INTERACTION_LABELS_IT[t]} for t in INTERACTION_TYPES],
            "contexts": list(CONTEXTS)}


def immobile_nello_scope(cur, agency_id: int, property_id: int) -> dict:
    """L'immobile nell'agenzia dello scope; altrove = inesistente (404)."""
    cur.execute("SELECT id, acquisition_id, mandate_type, mandate_start FROM properties "
                "WHERE id = %s AND agency_id = %s", (property_id, agency_id))
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"property {property_id} not found")
    return dict(row)


def _voce(row) -> dict:
    voce = dict(row)
    metadata = voce.pop("metadata", None) or {}
    voce["interaction_type"] = voce.pop("activity_type")
    voce["type_label"] = INTERACTION_LABELS_IT.get(voce["interaction_type"],
                                                   voce["interaction_type"])
    voce["note"] = voce.pop("description")
    voce["context"] = metadata.get("context")
    return voce


def list_with_cursor(ctx, cur, agency_id: int, property_id: int, *, limit: int = 50,
                     offset: int = 0, activity_id: int | None = None) -> list[dict]:
    """Le interazioni di un immobile GIA' verificato nello scope, dalla piu'
    recente. Lo scope delle righe e' quello di `activities` (core.scope)."""
    predicato, params = scoped_predicate(ctx, "activities", "a")
    cur.execute(
        f"""
        SELECT a.id, a.activity_type, a.description, a.occurred_at, a.created_at,
               a.created_by_user_id, a.contact_id, a.metadata,
               {NOME_CONTATTO.format(a='c')} AS contact_name,
               {NOME_OPERATORE.format(a='u')} AS author_name
          FROM activities a
          LEFT JOIN contacts c ON c.id = a.contact_id AND c.agency_id = a.agency_id
          LEFT JOIN operator_users u ON u.id = a.created_by_user_id
         WHERE {predicato} AND a.agency_id = %s AND a.property_id = %s
               {"AND a.id = %s" if activity_id is not None else ""}
         ORDER BY a.occurred_at DESC, a.id DESC
         LIMIT %s OFFSET %s
        """,
        params + [agency_id, property_id]
        + ([activity_id] if activity_id is not None else []) + [limit, offset])
    return [_voce(r) for r in cur.fetchall()]


def list_interactions(ctx, property_id: int, *, limit: int = 50, offset: int = 0) -> dict:
    agency_id = ctx.require_agency()
    if not 1 <= int(limit) <= MAX_LIST or int(offset) < 0:
        raise ValidationError("Paginazione non valida")
    with core_cursor() as (_, cur):
        immobile_nello_scope(cur, agency_id, property_id)
        return {"items": list_with_cursor(ctx, cur, agency_id, property_id,
                                          limit=limit, offset=offset),
                **options()}


def create_interaction(ctx, property_id: int, body) -> dict:
    """Una riga di `activities` sull'immobile, nella transazione della
    richiesta. Data/ora e autore li mette il server."""
    agency_id = ctx.require_agency()
    if ctx.user_id is None:
        raise ValidationError("Le interazioni richiedono una sessione operatore")
    nota = (body.note or "").strip()
    if not nota:
        raise ValidationError("Scrivi la nota dell'interazione")
    if body.interaction_type not in INTERACTION_TYPES:
        raise ValidationError("Tipo di interazione non valido")
    with core_cursor(commit=True) as (_, cur):
        # L'ORDINE DEI CONTROLLI e' voluto: immobile (tenant) -> referente
        # (tenant) -> contesto. Le sonde ostili P26-6 mandano `context:
        # mandate` su immobili che non sono incarichi: se un controllo di
        # tenant regredisse, la richiesta si fermerebbe comunque sul contesto
        # (400) senza scrivere, e la sonda lo vedrebbe dal messaggio.
        immobile = immobile_nello_scope(cur, agency_id, property_id)
        if body.contact_id is not None:
            # Il referente e' un contatto DELL'immobile (property_contacts):
            # un contatto di un'altra agenzia non puo' esserlo per costruzione.
            cur.execute("SELECT 1 FROM property_contacts WHERE property_id = %s AND contact_id = %s",
                        (property_id, body.contact_id))
            if cur.fetchone() is None:
                raise ValidationError(REFERENTE_NON_COLLEGATO)
        if body.context == "mandate" and immobile["acquisition_id"] is None:
            raise ValidationError(SENZA_INCARICO)
        try:
            # La stessa scrittura di POST /api/core/activities: il referente
            # passa dallo scope dei contatti (un agente nomina solo chi vede).
            riga = core_repository.create_activity_with_cursor(cur, {
                "contact_id": body.contact_id, "lead_id": None, "stima_id": None,
                "property_id": property_id, "activity_type": body.interaction_type,
                "direction": None, "channel": None, "subject": None, "description": nota,
                "outcome": None, "occurred_at": None, "created_by": None,
                "metadata": {"context": body.context},
            }, ctx=ctx)
        except NotFoundError as exc:
            raise NotFoundError("Referente non disponibile per il tuo profilo: "
                                "registra l'interazione senza referente") from exc
        return list_with_cursor(ctx, cur, agency_id, property_id, limit=1,
                                activity_id=riga["id"])[0]
