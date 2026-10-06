"""VENDITORI-1 - «questo proprietario vende questo specifico immobile».

MODELLO (nessuna tabella nuova)

    Opportunita' Venditore = lead `pipeline='sell'` del contatto
                             + `property_leads(relation_type='seller')` verso
                               QUELL'immobile.

Tutto il resto esiste gia' e si riusa:

  * proprietario  -> `property_contacts` (owner|seller), mai toccato: Vende non
                     sostituisce il ruolo;
  * stato         -> `leads.stage` / `leads.status` (open, paused, closed) e
                     `lost_reason`: si chiude o si sospende, non si cancella;
  * agente        -> `leads.assigned_agent_id`, con lo stesso predicato di
                     scope di tutto il CRM (`core.scope`): l'agente vede i
                     suoi lead, owner/admin l'agenzia. Nessun criterio nuovo;
  * storico       -> `activities` (con `property_id`, `contact_id`,
                     `lead_id`): la stessa riga nello storico immobile, nel
                     contatto e nel lead; le transizioni di Vende sono righe
                     `status_change` scritte da `create_activity_with_cursor`;
  * prossima azione -> `tasks` del lead (e, in sola lettura, il campo
                     `leads.next_action_at` gia' mostrato nella scheda lead);
  * acquisizione  -> il modulo Acquisizioni, con `property_id`,
                     `owner_contact_id`, `lead_id`: Vende non ne crea.

REGOLE

  * Attivare e' IDEMPOTENTE per (agenzia, immobile, contatto): un lock
    transazionale `pg_advisory_xact_lock` serializza le richieste uguali, e la
    prima cosa che si cerca e' l'opportunita' gia' esistente. Nessun vincolo
    DB puo' esprimerlo (la coppia vive in due tabelle), il lock e' lo stesso
    schema del bridge della stima pubblica.
  * Lead SELL gia' esistente: si riusa SOLO se la corrispondenza e' univoca
    (vedi `_scegli_lead`); se non lo e' non si collega nulla e si restituisce
    l'elenco dei candidati: la scelta (o «lead nuovo») e' dell'operatore.
  * Censimento: una scheda `census` non entra nel flusso (409
    PROPERTY_IN_CENSUS); si passa dal «Prendi in carico» esistente.

DELETE-ARCH FASE 1B - «INSERITO PER ERRORE»

  * L'attivazione scrive nello storico (`activities.metadata`) da DOVE viene
    il lead: `origin` = `created` (lead nuovo di Vende), `reused` (lead SELL
    aperto gia' esistente), `reopened` (lead sospeso/chiuso riaperto), con lo
    stato REALE di prima (`previous`: stage, status, lost_reason, closed_at e
    la relazione con l'immobile) e la `lead_source`. Nessuna colonna nuova.
  * «Inserito per errore» annulla QUELLA attivazione, e non e' piu' un testo:
      - created            -> lead closed/lost, `lost_reason='created_by_mistake'`;
                              la relazione seller RESTA (prova dell'errore);
      - reused / reopened  -> il lead torna allo stato di prima e la
                              relazione torna com'era: tolta se non c'era,
                              riportata al tipo di prima (origin/related) se
                              c'era, intatta se era gia' seller.
    Attivita', task, stime e storico non si toccano mai.
  * Un'acquisizione APERTA sullo stesso immobile e lo stesso lead blocca
    (409 ACQUISITION_OPEN): si segna prima quella come creata per errore.
  * I `created_by_mistake` escono da tutte le viste normali della worklist;
    si rivedono solo col filtro «Inseriti per errore».
"""
from __future__ import annotations

from typing import Any

from acquisitions.enums import TERMINAL_STATUSES as ACQUISITION_TERMINAL
from core import repository as core_repository
from core.database import core_cursor
from core.property_trash import PropertyInTrash
from core.exceptions import ConflictError, NotFoundError, ValidationError
from core.scope import scoped_predicate
from seller_intent import batch as intent_batch

OWNER_ROLES = ("owner", "seller")
INTERACTION_TYPES = ("call", "meeting", "note", "email", "whatsapp")
LEAD_SOURCE = "property_seller"
#: DELETE-ARCH Fase 1B: il codice canonico (stesso valore delle acquisizioni).
MISTAKE_REASON = "created_by_mistake"
MISTAKE_LABEL_IT = "Inserito per errore"
ORIGINS = ("created", "reused", "reopened")

#: Le viste della worklist: poche, ciascuna un predicato SQL esplicito.
VIEWS = {
    "all": ("Tutti", None),
    "new": ("Nuovi", "l.stage = 'new'"),
    "contacted": ("Contattati", "l.stage = 'contacted'"),
    "qualified": ("Qualificati", "l.stage = 'qualified'"),
    "ready": ("Pronti per acquisizione", "l.stage = 'appointment'"),
    "overdue": ("Da richiamare", "COALESCE(nt.due_at, l.next_action_at) < NOW()"),
    "no_action": ("Senza prossima azione", "nt.id IS NULL AND l.next_action_at IS NULL"),
}
STATUS_FILTERS = {
    "active": ("Attivi", "l.status = 'open'"),
    "paused": ("Sospesi", "l.status = 'paused'"),
    "closed": ("Chiusi", "l.status = 'closed'"),
    "all": ("Tutti gli stati", None),
    # DELETE-ARCH Fase 1B: l'unico filtro che mostra gli errori.
    "mistakes": ("Inseriti per errore", f"l.lost_reason = '{MISTAKE_REASON}'"),
}
#: Fuori da ogni vista normale (active/paused/closed/all).
NOT_MISTAKE_SQL = f"l.lost_reason IS DISTINCT FROM '{MISTAKE_REASON}'"
STAGE_LABELS_IT = {
    "new": "Nuovo", "contacted": "Contattato", "qualified": "Qualificato",
    "appointment": "Appuntamento", "proposal": "Proposta", "won": "Acquisito", "lost": "Perso",
}
OUTCOMES = {
    "paused": "Vendita sospesa",
    "not_selling": "Non vende più",
    "mistake": "Inserito per errore",
}

NOME_CONTATTO = ("COALESCE(NULLIF(BTRIM({a}.display_name), ''),"
                 " NULLIF(BTRIM(CONCAT_WS(' ', {a}.first_name, {a}.last_name)), ''),"
                 " {a}.company_name)")
NOME_OPERATORE = ("COALESCE(NULLIF(BTRIM(CONCAT_WS(' ', {a}.first_name, {a}.last_name)), ''),"
                  " split_part({a}.email, '@', 1))")


class SellerError(ConflictError):
    """409 con un `code` leggibile dalla UI (e dati in `extra`)."""

    def __init__(self, message: str, code: str, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


# ---------------------------------------------------------------------------
# Lettura comune
# ---------------------------------------------------------------------------

def _operatore(ctx) -> int:
    if getattr(ctx, "user_id", None) is None:
        raise ValidationError("I venditori richiedono una sessione operatore")
    return int(ctx.user_id)


def _blocca_coppia(cur, agency_id: int, property_id: int, contact_id: int) -> None:
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (f"crm:seller:{agency_id}:{property_id}:{contact_id}",))


def _immobile(cur, agency_id: int, property_id: int) -> dict:
    cur.execute("SELECT id, code, title, archived_at, commercial_status, "
                "COALESCE(to_jsonb(p) ->> 'record_kind', 'crm') AS record_kind, "
                "(to_jsonb(p) ->> 'deleted_at') AS deleted_at "
                "FROM properties p WHERE p.id = %s AND p.agency_id = %s", (property_id, agency_id))
    riga = cur.fetchone()
    if riga is None:
        raise NotFoundError(f"property {property_id} not found")
    riga = dict(riga)
    # DELETE-ARCH Fase 2B2: «Vende» non si attiva ne' si chiude su un immobile
    # nel Cestino (congelato): 409 PROPERTY_IN_TRASH.
    if riga.pop("deleted_at") is not None:
        raise PropertyInTrash()
    return riga


def _contatto(cur, agency_id: int, contact_id: int) -> dict:
    cur.execute(f"SELECT c.id, {NOME_CONTATTO.format(a='c')} AS name FROM contacts c "
                "WHERE c.id = %s AND c.agency_id = %s", (contact_id, agency_id))
    riga = cur.fetchone()
    if riga is None:
        raise NotFoundError(f"contact {contact_id} not found")
    return dict(riga)


def _visibile(cur, ctx, table: str, entity_id: int) -> bool:
    alias = {"leads": "l", "contacts": "c"}[table]
    predicato, params = scoped_predicate(ctx, table, alias)
    cur.execute(f"SELECT 1 FROM {table} {alias} WHERE {alias}.id = %s AND {predicato}", [entity_id] + params)
    return cur.fetchone() is not None


def _opportunita(cur, agency_id: int, property_id: int, contact_id: int):
    """L'opportunita' gia' esistente per la coppia: prima una aperta, poi una
    sospesa, poi la piu' recente chiusa (lo stesso lead si riapre)."""
    cur.execute(
        """
        SELECT l.* FROM leads l
          JOIN property_leads pl ON pl.lead_id = l.id
         WHERE l.agency_id = %s AND l.contact_id = %s AND l.pipeline = 'sell'
           AND pl.property_id = %s AND pl.relation_type = 'seller'
         ORDER BY (l.status = 'open') DESC, (l.status = 'paused') DESC, l.id DESC
         LIMIT 1
         FOR UPDATE OF l
        """, (agency_id, contact_id, property_id))
    riga = cur.fetchone()
    return dict(riga) if riga else None


def _storico(cur, ctx, *, property_id: int, contact_id: int, lead_id: int, testo: str, evento: str,
             extra: dict | None = None) -> None:
    # REV 2 (R3): se il contatto non e' piu' collegato all'immobile (nessuna
    # riga property_contacts) il trigger 082 rifiuterebbe una riga con
    # `property_id`. La chiusura/sospensione resta possibile: l'evento va nello
    # storico del lead e del contatto, senza immobile. Il collegamento NON si
    # ricrea.
    cur.execute("SELECT 1 FROM property_contacts WHERE property_id = %s AND contact_id = %s",
                (property_id, contact_id))
    collegato = cur.fetchone() is not None
    core_repository.create_activity_with_cursor(cur, {
        "contact_id": contact_id, "lead_id": lead_id, "stima_id": None,
        "property_id": property_id if collegato else None,
        "activity_type": "status_change", "direction": None, "channel": None, "subject": None,
        "description": testo, "outcome": None, "occurred_at": None, "created_by": None,
        "metadata": {"context": "seller", "event": evento, "property_id": property_id, **(extra or {})},
    }, ctx=ctx)


def _iso(valore):
    return valore.isoformat() if hasattr(valore, "isoformat") else valore


def _prima(lead: dict | None, relazione: str | None) -> dict:
    """Lo stato REALE del lead prima dell'attivazione (Fase 1B). Per un lead
    nuovo non esisteva nulla: tutti None."""
    lead = lead or {}
    return {"stage": lead.get("stage"), "status": lead.get("status"), "lost_reason": lead.get("lost_reason"),
            "closed_at": _iso(lead.get("closed_at")), "relation_type": relazione}


def _relazione(cur, property_id: int, lead_id: int) -> str | None:
    cur.execute("SELECT relation_type FROM property_leads WHERE property_id = %s AND lead_id = %s",
                (property_id, lead_id))
    riga = cur.fetchone()
    return None if riga is None else riga["relation_type"]


def _esito(lead: dict, property_id: int, **flag) -> dict:
    return {"lead_id": lead["id"], "property_id": property_id, "contact_id": lead["contact_id"],
            "stage": lead["stage"], "status": lead["status"], "lost_reason": lead.get("lost_reason"),
            "assigned_agent_id": lead.get("assigned_agent_id"),
            "created": False, "reused": False, "reopened": False, **flag}


# ---------------------------------------------------------------------------
# Attivazione: «Vende»
# ---------------------------------------------------------------------------

def _candidati(cur, agency_id: int, property_id: int, contact_id: int) -> list[dict]:
    """I lead SELL non chiusi del contatto che possono diventare l'opportunita'
    di QUESTO immobile: senza collegamenti ad ALTRI immobili. Un lead gia'
    collegato a questo immobile (per esempio come `origin`) e' marcato `here`.
    Si guarda nell'intera agenzia, non nello scope dell'agente: e' proprio cio'
    che l'agente non vede che altrimenti diventerebbe un doppione."""
    cur.execute(
        """
        SELECT l.id, l.stage, l.status, l.source, l.created_at, l.assigned_agent_id,
               EXISTS (SELECT 1 FROM property_leads pl WHERE pl.lead_id = l.id AND pl.property_id = %s) AS here
          FROM leads l
         WHERE l.agency_id = %s AND l.contact_id = %s AND l.pipeline = 'sell' AND l.status <> 'closed'
           AND NOT EXISTS (SELECT 1 FROM property_leads pl WHERE pl.lead_id = l.id AND pl.property_id <> %s)
         ORDER BY l.id
        """, (property_id, agency_id, contact_id, property_id))
    return [dict(r) for r in cur.fetchall()]


def _scegli_lead(cur, ctx, agency_id: int, property_id: int, contact_id: int) -> dict | None:
    """La regola deterministica di riuso.

      1. un solo candidato gia' collegato a questo immobile -> quello;
      2. altrimenti un solo candidato libero (nessun immobile) -> quello;
      3. nessun candidato -> None (si crea un lead nuovo);
      4. piu' candidati -> SELLER_LEAD_AMBIGUOUS con l'elenco: niente
         associazioni indovinate.
    Un candidato che chi chiama non vede (un agente e il lead non assegnato
    della stima pubblica) ferma tutto: SELLER_LEAD_NOT_VISIBLE, mai un doppione.
    I lead chiusi non sono candidati (appartengono al passato); quelli
    collegati ad altri immobili sono altre opportunita'.
    """
    candidati = _candidati(cur, agency_id, property_id, contact_id)
    if not candidati:
        return None
    if any(not _visibile(cur, ctx, "leads", c["id"]) for c in candidati):
        raise SellerError("Esiste gia' un lead venditore di questo contatto non assegnato a te: "
                          "chiedi a un amministratore di assegnartelo", "SELLER_LEAD_NOT_VISIBLE")
    qui = [c for c in candidati if c["here"]]
    scelta = qui if qui else candidati
    if len(scelta) == 1:
        return scelta[0]
    raise SellerError("Il contatto ha piu' lead venditore senza immobile: scegli quale collegare",
                      "SELLER_LEAD_AMBIGUOUS",
                      candidates=[{"id": c["id"], "stage": c["stage"], "status": c["status"], "source": c["source"],
                                   "created_at": c["created_at"].isoformat() if c["created_at"] else None}
                                  for c in scelta])


def _lead_esplicito(cur, ctx, agency_id: int, property_id: int, contact_id: int, lead_id: int) -> dict:
    cur.execute("SELECT id, status FROM leads WHERE id = %s AND agency_id = %s AND contact_id = %s AND pipeline = 'sell'",
                (lead_id, agency_id, contact_id))
    riga = cur.fetchone()
    if riga is None or not _visibile(cur, ctx, "leads", lead_id):
        raise NotFoundError(f"lead {lead_id} not found")
    cur.execute("SELECT 1 FROM property_leads WHERE lead_id = %s AND property_id <> %s", (lead_id, property_id))
    if cur.fetchone() is not None:
        raise SellerError("Il lead scelto riguarda un altro immobile", "SELLER_LEAD_OTHER_PROPERTY")
    return dict(riga)


def _riapri(cur, lead_id: int) -> dict:
    cur.execute("UPDATE leads SET status = 'open', stage = CASE WHEN stage = 'lost' THEN 'new' ELSE stage END, "
                "lost_reason = NULL, closed_at = NULL, updated_at = NOW() WHERE id = %s RETURNING *", (lead_id,))
    return dict(cur.fetchone())


def activate(ctx, body) -> tuple[dict, bool]:
    """Restituisce `(esito, creato)`; `creato` decide 201 o 200."""
    agency_id = ctx.require_agency()
    _operatore(ctx)
    with core_cursor(commit=True) as (_, cur):
        immobile = _immobile(cur, agency_id, body.property_id)
        contatto = _contatto(cur, agency_id, body.contact_id)
        _blocca_coppia(cur, agency_id, body.property_id, body.contact_id)
        cur.execute("SELECT 1 FROM property_contacts WHERE property_id = %s AND contact_id = %s AND role = ANY(%s)",
                    (body.property_id, body.contact_id, list(OWNER_ROLES)))
        if cur.fetchone() is None:
            raise SellerError("Il contatto non e' collegato a questo immobile come proprietario",
                              "SELLER_NOT_OWNER")
        if immobile["record_kind"] == "census":
            raise SellerError("Immobile in censimento: per lavorare la vendita prendilo prima in carico",
                              "PROPERTY_IN_CENSUS")
        if immobile["archived_at"] is not None or immobile["commercial_status"] in ("sold", "archived", "withdrawn"):
            raise SellerError("L'immobile e' venduto, ritirato o archiviato", "SELLER_PROPERTY_CLOSED")
        if not _visibile(cur, ctx, "contacts", body.contact_id):
            raise SellerError("Il proprietario e' assegnato a un altro agente: chiedi a un amministratore",
                              "SELLER_CONTACT_NOT_ASSIGNED")

        esistente = _opportunita(cur, agency_id, body.property_id, body.contact_id)
        if esistente is not None:
            if not _visibile(cur, ctx, "leads", esistente["id"]):
                raise SellerError("Questo venditore e' seguito da un altro agente", "SELLER_LEAD_NOT_VISIBLE")
            if body.lead_id is not None and body.lead_id != esistente["id"]:
                raise SellerError("Per questo proprietario e questo immobile esiste gia' un'opportunita' venditore",
                                  "SELLER_ALREADY_ACTIVE", lead_id=esistente["id"])
            if esistente["status"] == "open":
                return _esito(esistente, body.property_id, reused=True), False
            prima = _prima(esistente, "seller")
            riaperto = _riapri(cur, esistente["id"])
            # Fase 1B: evento `activated` con origin `reopened` (prima: evento
            # `reopened` senza stato precedente).
            _storico(cur, ctx, property_id=body.property_id, contact_id=body.contact_id, lead_id=riaperto["id"],
                     testo=f"Vende: riattivato — {contatto['name']} vende di nuovo questo immobile", evento="activated",
                     extra={"origin": "reopened", "previous": prima, "lead_source": esistente.get("source")})
            return _esito(riaperto, body.property_id, reused=True, reopened=True), False

        creato = False
        if body.lead_id is not None:
            scelto = _lead_esplicito(cur, ctx, agency_id, body.property_id, body.contact_id, body.lead_id)
        elif body.new_lead:
            scelto = None
        else:
            scelto = _scegli_lead(cur, ctx, agency_id, body.property_id, body.contact_id)
        prima = None
        relazione_prima = None
        if scelto is not None:
            cur.execute("SELECT * FROM leads WHERE id = %s", (scelto["id"],))
            prima = dict(cur.fetchone())
            relazione_prima = _relazione(cur, body.property_id, scelto["id"])
        if scelto is None:
            lead = core_repository.create_lead_with_cursor(ctx, cur, {
                "contact_id": body.contact_id, "source": LEAD_SOURCE, "pipeline": "sell", "stage": "new",
                "priority": "normal", "status": "open", "assigned_to": None, "estimated_value": None,
                "next_action_at": None, "lost_reason": None, "notes": None,
            })
            creato = True
        elif scelto["status"] != "open":
            lead = _riapri(cur, scelto["id"])
        else:
            lead = dict(prima)
        origine = "created" if creato else ("reused" if prima["status"] == "open" else "reopened")
        # il collegamento: se il lead era gia' legato a questo immobile con
        # un'altra relazione (origin/related) diventa il venditore - stessa riga
        cur.execute("INSERT INTO property_leads (property_id, lead_id, relation_type) VALUES (%s, %s, 'seller') "
                    "ON CONFLICT (property_id, lead_id) DO UPDATE SET relation_type = 'seller'",
                    (body.property_id, lead["id"]))
        _storico(cur, ctx, property_id=body.property_id, contact_id=body.contact_id, lead_id=lead["id"],
                 testo=f"Vende: attivato — {contatto['name']} vende questo immobile", evento="activated",
                 extra={"origin": origine, "previous": _prima(prima, relazione_prima),
                        "lead_source": lead.get("source")})
        return _esito(lead, body.property_id, created=creato, reused=not creato), creato


# ---------------------------------------------------------------------------
# Disattivazione: sospesa / non vende piu' / inserito per errore
# ---------------------------------------------------------------------------

def deactivate(ctx, body) -> dict:
    agency_id = ctx.require_agency()
    _operatore(ctx)
    with core_cursor(commit=True) as (_, cur):
        _immobile(cur, agency_id, body.property_id)
        contatto = _contatto(cur, agency_id, body.contact_id)
        _blocca_coppia(cur, agency_id, body.property_id, body.contact_id)
        lead = _opportunita(cur, agency_id, body.property_id, body.contact_id)
        if body.outcome == "mistake":
            return _per_errore(cur, ctx, agency_id, body, contatto, lead)
        if lead is None or not _visibile(cur, ctx, "leads", lead["id"]):
            raise NotFoundError("Nessuna opportunita' venditore per questo proprietario e questo immobile")
        etichetta = OUTCOMES[body.outcome]
        nota = (body.note or "").strip()
        if body.outcome == "paused":
            if lead["status"] == "paused":
                return _esito(lead, body.property_id)
            cur.execute("UPDATE leads SET status = 'paused', closed_at = NULL, updated_at = NOW() "
                        "WHERE id = %s RETURNING *", (lead["id"],))
        else:                                   # not_selling ("mistake" e' in _per_errore)
            motivo = f"{etichetta}: {nota}" if nota else etichetta
            if lead["status"] == "closed" and (lead.get("lost_reason") or "").startswith(etichetta):
                return _esito(lead, body.property_id)
            cur.execute("UPDATE leads SET status = 'closed', stage = 'lost', lost_reason = %s, "
                        "closed_at = NOW(), updated_at = NOW() WHERE id = %s RETURNING *", (motivo, lead["id"]))
        aggiornato = dict(cur.fetchone())
        _storico(cur, ctx, property_id=body.property_id, contact_id=body.contact_id, lead_id=lead["id"],
                 testo=f"Vende: {etichetta.lower()} — {contatto['name']}" + (f" ({nota})" if nota else ""),
                 evento=body.outcome)
        return _esito(aggiornato, body.property_id)


# ---------------------------------------------------------------------------
# DELETE-ARCH Fase 1B: «Inserito per errore»
# ---------------------------------------------------------------------------

_EVENTI_SQL = """
SELECT id, metadata FROM activities
 WHERE agency_id = %s AND activity_type = 'status_change' AND metadata->>'context' = 'seller'
   AND {chi} AND (metadata->>'property_id')::bigint = %s AND metadata->>'event' = ANY(%s)
 ORDER BY id DESC LIMIT 1
"""


def _ultimo_evento(cur, agency_id: int, property_id: int, eventi, *, lead_id=None, contact_id=None):
    chi, valore = ("lead_id = %s", lead_id) if lead_id is not None else ("contact_id = %s", contact_id)
    cur.execute(_EVENTI_SQL.format(chi=chi), (agency_id, valore, property_id, list(eventi)))
    riga = cur.fetchone()
    return None if riga is None else dict(riga)


def _per_errore(cur, ctx, agency_id: int, body, contatto: dict, lead: dict | None) -> dict:
    """Annulla l'ULTIMA attivazione Vende della coppia (immobile, contatto).

    Deterministico e idempotente: se l'ultimo evento Vende di questo lead su
    questo immobile e' gia' `mistake`, non si fa nulla (stessa risposta, nessuna
    attivita' in piu'); se la relazione e' gia' stata tolta, l'ultimo evento
    del contatto sull'immobile e' `mistake` e si risponde lo stesso."""
    property_id = body.property_id
    if lead is None:
        fatto = _ultimo_evento(cur, agency_id, property_id, ("activated", "reopened", "mistake"),
                               contact_id=body.contact_id)
        if fatto is not None and fatto["metadata"].get("event") == "mistake" and fatto["metadata"].get("unlinked"):
            lead_id = int(fatto["metadata"]["lead_id"])
            if _visibile(cur, ctx, "leads", lead_id):
                cur.execute("SELECT * FROM leads WHERE id = %s", (lead_id,))
                return _esito(dict(cur.fetchone()), property_id, already=True, unlinked=True)
        raise NotFoundError("Nessuna opportunita' venditore per questo proprietario e questo immobile")
    if not _visibile(cur, ctx, "leads", lead["id"]):
        raise NotFoundError("Nessuna opportunita' venditore per questo proprietario e questo immobile")

    ultimo = _ultimo_evento(cur, agency_id, property_id, ("activated", "reopened", "mistake", "paused", "not_selling"),
                            lead_id=lead["id"])
    if ultimo is not None and ultimo["metadata"].get("event") == "mistake":
        return _esito(lead, property_id, already=True, unlinked=False)

    # La creazione di un'acquisizione blocca la riga dell'immobile FOR UPDATE
    # (acquisitions/repository.py): FOR SHARE qui serializza le due cose, e
    # il controllo sotto vede l'acquisizione appena committata.
    cur.execute("SELECT 1 FROM properties WHERE id = %s AND agency_id = %s FOR SHARE", (property_id, agency_id))
    cur.execute("SELECT id FROM acquisitions WHERE agency_id = %s AND property_id = %s AND lead_id = %s "
                "AND status <> ALL(%s) ORDER BY id LIMIT 1",
                (agency_id, property_id, lead["id"], list(ACQUISITION_TERMINAL)))
    aperta = cur.fetchone()
    if aperta is not None:
        raise SellerError("Segna prima l'acquisizione come creata per errore.", "ACQUISITION_OPEN",
                          acquisition_id=aperta["id"])

    attivazione = _ultimo_evento(cur, agency_id, property_id, ("activated", "reopened"), lead_id=lead["id"])
    meta = (attivazione or {}).get("metadata") or {}
    origine = meta.get("origin")
    if origine not in ORIGINS:
        # Attivazione precedente alla Fase 1B (nessuno stato salvato): solo un
        # lead nato da Vende si puo' chiudere come errore senza rischiare di
        # chiudere un lead vero; per gli altri lo stato di prima non e' noto.
        if lead.get("source") != LEAD_SOURCE:
            raise SellerError("Questa attivazione e' precedente alla gestione degli errori: non so a che stato "
                              "riportare il lead. Usa «Non vende più».", "SELLER_MISTAKE_ORIGIN_UNKNOWN")
        origine = "created"

    nota = (body.note or "").strip()
    unlinked = False
    relazione = "kept"
    prima = meta.get("previous") or {}
    if origine == "created":
        cur.execute("UPDATE leads SET status = 'closed', stage = 'lost', lost_reason = %s, "
                    "closed_at = COALESCE(closed_at, NOW()), updated_at = NOW() WHERE id = %s RETURNING *",
                    (MISTAKE_REASON, lead["id"]))
        azione = "closed"
    else:
        stato = prima.get("status") or "open"
        chiuso = prima.get("closed_at") if stato == "closed" else None
        cur.execute("UPDATE leads SET stage = %s, status = %s, lost_reason = %s, "
                    "closed_at = CASE WHEN %s = 'closed' THEN COALESCE(%s::timestamptz, NOW()) ELSE NULL END, "
                    "updated_at = NOW() WHERE id = %s RETURNING *",
                    (prima.get("stage") or lead["stage"], stato, prima.get("lost_reason"), stato, chiuso, lead["id"]))
        azione = "restored"
        tipo_prima = prima.get("relation_type")
        if tipo_prima is None:
            relazione, unlinked = "unlinked", True
        elif tipo_prima != "seller":
            relazione = f"restored:{tipo_prima}"
    aggiornato = dict(cur.fetchone())
    # lo storico PRIMA di toccare la relazione: la riga resta sull'immobile
    _storico(cur, ctx, property_id=property_id, contact_id=body.contact_id, lead_id=lead["id"],
             testo=f"Vende: inserito per errore — {contatto['name']}" + (f" ({nota})" if nota else ""),
             evento="mistake",
             extra={"lead_id": lead["id"], "origin": origine, "action": azione, "relation": relazione,
                    "unlinked": unlinked, "activation_activity_id": (attivazione or {}).get("id"),
                    "restored": None if azione == "closed" else {
                        "stage": aggiornato["stage"], "status": aggiornato["status"],
                        "lost_reason": aggiornato["lost_reason"]},
                    "note": nota or None})
    if unlinked:
        cur.execute("DELETE FROM property_leads WHERE property_id = %s AND lead_id = %s AND relation_type = 'seller'",
                    (property_id, lead["id"]))
    elif relazione.startswith("restored:"):
        cur.execute("UPDATE property_leads SET relation_type = %s WHERE property_id = %s AND lead_id = %s "
                    "AND relation_type = 'seller'", (prima["relation_type"], property_id, lead["id"]))
    return _esito(aggiornato, property_id, already=False, unlinked=unlinked, origin=origine, action=azione)


# ---------------------------------------------------------------------------
# Worklist
# ---------------------------------------------------------------------------

def list_sellers(ctx, *, view: str = "all", status: str = "active", agent_id: int | None = None,
                 city: str | None = None, search: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    """La worklist: UNA statement per la pagina (laterali per ultima
    interazione, prossima azione, acquisizione) + UNA per il punteggio seller
    intent della pagina. Mai una query per riga."""
    agency_id = ctx.require_agency()
    if view not in VIEWS or status not in STATUS_FILTERS:
        raise ValidationError("Filtro non valido")
    predicato, params = scoped_predicate(ctx, "leads", "l")
    # DELETE-ARCH Fase 0: un immobile archiviato esce dalla worklist.
    filtri = [predicato, "pl.relation_type = 'seller'", "l.pipeline = 'sell'", "p.archived_at IS NULL"]
    # DELETE-ARCH Fase 1B: gli inseriti per errore solo nel loro filtro.
    if status != "mistakes":
        filtri.append(NOT_MISTAKE_SQL)
    for clausola in (VIEWS[view][1], STATUS_FILTERS[status][1]):
        if clausola:
            filtri.append(clausola)
    extra: list[Any] = []
    if agent_id is not None:
        if agent_id == 0:
            filtri.append("l.assigned_agent_id IS NULL")
        else:
            filtri.append("l.assigned_agent_id = %s")
            extra.append(agent_id)
    if city:
        filtri.append("p.city ILIKE %s")
        extra.append(f"%{city.strip()}%")
    if search:
        filtri.append(f"({NOME_CONTATTO.format(a='c')} ILIKE %s OR c.phone ILIKE %s OR c.email ILIKE %s "
                      "OR p.code ILIKE %s OR p.address ILIKE %s OR p.title ILIKE %s)")
        extra += [f"%{search.strip()}%"] * 6
    terminali = list(ACQUISITION_TERMINAL)
    sees_all = bool(getattr(ctx, "sees_all_agency_records", False))
    with core_cursor() as (_, cur):
        cur.execute(
            f"""
            SELECT l.id AS lead_id, l.stage, l.status, l.lost_reason, l.assigned_agent_id, l.next_action_at,
                   l.created_at, l.updated_at,
                   c.id AS contact_id, {NOME_CONTATTO.format(a='c')} AS contact_name, c.phone, c.email,
                   p.id AS property_id, p.code, p.title, p.address, p.civic_number, p.city,
                   COALESCE(to_jsonb(p) ->> 'record_kind', 'crm') AS record_kind,
                   EXISTS (SELECT 1 FROM property_contacts pc WHERE pc.property_id = p.id
                            AND pc.contact_id = c.id AND pc.role = ANY(%s)) AS still_owner,
                   {NOME_OPERATORE.format(a='u')} AS agent_name,
                   la.id AS la_id, la.activity_type AS la_type, la.description AS la_note,
                   la.occurred_at AS la_at,
                   nt.id AS nt_id, nt.title AS nt_title, nt.due_at AS nt_due,
                   aq.id AS aq_id, aq.status AS aq_status, aq.assigned_agent_id AS aq_agent
              FROM property_leads pl
              JOIN leads l ON l.id = pl.lead_id
              JOIN properties p ON p.id = pl.property_id AND p.agency_id = l.agency_id
                              AND (to_jsonb(p)->>'deleted_at') IS NULL   -- DELETE-ARCH 2B2
              JOIN contacts c ON c.id = l.contact_id AND c.agency_id = l.agency_id
              LEFT JOIN operator_users u ON u.id = l.assigned_agent_id
              LEFT JOIN LATERAL (
                   SELECT a.id, a.activity_type, a.description, a.occurred_at FROM activities a
                    WHERE a.agency_id = l.agency_id AND a.activity_type = ANY(%s)
                      AND (a.lead_id = l.id OR (a.property_id = p.id AND a.contact_id = c.id))
                    ORDER BY a.occurred_at DESC, a.id DESC LIMIT 1) la ON TRUE
              LEFT JOIN LATERAL (
                   SELECT t.id, t.title, t.due_at FROM tasks t
                    WHERE t.agency_id = l.agency_id AND t.lead_id = l.id AND t.status IN ('open', 'in_progress')
                    ORDER BY t.due_at ASC NULLS LAST, t.id LIMIT 1) nt ON TRUE
              LEFT JOIN LATERAL (
                   -- REV 2 (R1): «in corso» = NON terminale, su tutta la selezione:
                   -- una vecchia acquisizione persa/acquisita sul lead non conta,
                   -- e un'acquisizione aperta dell'immobile (anche di un
                   -- comproprietario) vince su qualunque storico.
                   SELECT q.id, q.status, q.assigned_agent_id FROM acquisitions q
                    WHERE q.agency_id = l.agency_id
                      AND q.status <> ALL(%s)
                      AND (q.lead_id = l.id OR q.property_id = p.id)
                    ORDER BY (q.lead_id = l.id) DESC, q.id DESC LIMIT 1) aq ON TRUE
             WHERE {' AND '.join(filtri)}
             ORDER BY COALESCE(nt.due_at, l.next_action_at) ASC NULLS LAST, l.updated_at DESC, l.id DESC
             LIMIT %s OFFSET %s
            """,
            [list(OWNER_ROLES), list(INTERACTION_TYPES), terminali] + params + extra + [int(limit) + 1, int(offset)])
        righe = [dict(r) for r in cur.fetchall()]
        altri = len(righe) > int(limit)
        righe = righe[: int(limit)]
        punteggi = intent_batch.get_seller_intent_scores_scoped_many(cur, ctx, [r["lead_id"] for r in righe])
    return {"items": [_voce(r, punteggi.get(r["lead_id"]), ctx, sees_all) for r in righe],
            "has_more": altri, "limit": int(limit), "offset": int(offset),
            "views": [{"value": k, "label": v[0]} for k, v in VIEWS.items()],
            "statuses": [{"value": k, "label": v[0]} for k, v in STATUS_FILTERS.items()],
            "stages": [{"value": k, "label": v} for k, v in STAGE_LABELS_IT.items()],
            "outcomes": [{"value": k, "label": v} for k, v in OUTCOMES.items()]}


def _voce(r: dict, punteggio: dict | None, ctx, sees_all: bool) -> dict:
    from datetime import datetime, timezone
    scadenza = r["nt_due"] or r["next_action_at"]
    adesso = datetime.now(timezone.utc)
    return {
        "lead_id": r["lead_id"], "stage": r["stage"], "stage_label": STAGE_LABELS_IT.get(r["stage"], r["stage"]),
        "status": r["status"], "lost_reason": r["lost_reason"],
        "lost_reason_label": MISTAKE_LABEL_IT if r["lost_reason"] == MISTAKE_REASON else r["lost_reason"],
        "assigned_agent_id": r["assigned_agent_id"], "agent_name": r["agent_name"],
        "contact": {"id": r["contact_id"], "name": r["contact_name"], "phone": r["phone"], "email": r["email"]},
        "property": {"id": r["property_id"], "code": r["code"], "title": r["title"], "address": r["address"],
                     "civic_number": r["civic_number"], "city": r["city"], "record_kind": r["record_kind"]},
        "still_owner": r["still_owner"],
        "last_interaction": None if r["la_id"] is None else {
            "id": r["la_id"], "interaction_type": r["la_type"], "note": (r["la_note"] or "")[:280],
            "occurred_at": r["la_at"].isoformat() if r["la_at"] else None},
        "next_action": None if scadenza is None else {
            "task_id": r["nt_id"], "title": r["nt_title"] or "Prossima azione",
            "due_at": scadenza.isoformat(), "overdue": scadenza < adesso,
            "source": "task" if r["nt_id"] is not None else "lead"},
        "intent": None if punteggio is None else {
            "score": punteggio["score"], "band": punteggio["band"], "state": punteggio["state"]},
        "acquisition": None if r["aq_id"] is None else {
            "id": r["aq_id"], "status": r["aq_status"],
            "visible": sees_all or r["aq_agent"] == getattr(ctx, "user_id", None)},
    }
