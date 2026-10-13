"""CESTINO-GENERALE-3 (CG-3) - la CANCELLAZIONE DEFINITIVA controllata di
immobili, contatti e richieste acquirente gia' nel Cestino.

UN motore, tre adattatori. Il flusso e' lo stesso per ogni categoria ed e'
quello di ELIMINAZIONE-EDIFICIO-1/2, di cui riusa le primitive
(`property/building_deletion.py`: catalogo delle FK, regole degli immobili,
riferimenti di STIMA Voice, copia dello storico, eventi, audit):

    purge_check  (sola lettura)  ammissibile? cosa si cancella, cosa resta,
                                 cosa blocca; impronta del riepilogo;
    purge        (scrittura)     Super Admin, testo di conferma esatto, riga
                                 bloccata (FOR UPDATE), blocchi RICALCOLATI,
                                 impronta identica a quella mostrata, evento
                                 `purge` nel registro con la copia di cio' che
                                 sparisce, verifica della copia, DELETE della
                                 sola riga (le cascate ammesse sono SOLO i
                                 contenuti propri elencati qui), verifica che
                                 nessuna riga punti piu' all'elemento, audit di
                                 piattaforma POI commit (audit_then_commit).
                                 Qualunque errore: rollback di tutto.

Super Admin generale designato SOLO (`platform_admin/super_admin.py`: id della
configurazione, `is_platform_admin`, account attivo riletto nel database).
STIMA Voice non chiama questo modulo.

Regole (ogni riga collegata che NON e' un contenuto proprio BLOCCA, qualunque
sia la sua regola ON DELETE - CASCADE, SET NULL o RESTRICT: nessuna scheda
altrui perde dati o collegamenti per una cancellazione):

  immobile         le STESSE regole delle unita' di un edificio
                   (`building_deletion.purge_blockers`): contenuti propri i
                   collegamenti ai proprietari (copiati), gli accessori, lo
                   storico stati di creazione; blocca tutto il resto (foto,
                   documenti, prezzi, attivita', visite, abbinamenti, incarico,
                   vendite, acquisizioni, portale...). Un'unita' di un edificio
                   nel Cestino si cancella SOLO con il suo edificio.
  contatto         contenuti propri: ruoli, pausa delle automazioni (scritta dal
                   Cestino stesso), esclusioni dei suggerimenti Venditore.
                   Bloccano consensi e messaggi (registri da conservare: serve
                   la procedura di anonimizzazione), lead, attivita', task,
                   proprieta', richieste, appuntamenti, portale, iscrizioni...
                   Nel registro NESSUN dato personale: solo ruoli e conteggi.
  richiesta        contenuti propri: criteri (zone, tipologie, caratteristiche),
                   calcoli ed esclusioni di abbinamento, abbinamenti SENZA
                   seguito, lo storico di creazione e modifica (copiato).
                   Bloccano interazioni, proposte, vendite, task, note, esiti.
"""
from __future__ import annotations

import hashlib
import json

from core.database import core_cursor
from core.exceptions import NotFoundError, ValidationError
from platform_admin import super_admin as _sa
from property import building_deletion as bd
from property.lifecycle import LifecycleConflict

AUDIT_PURGE = "platform.trash.purge"
AUDIT_DENIED = "platform.trash.denied"
PURGE_CONFIRM_TEXT = bd.PURGE_CONFIRM_TEXT

NOT_DELETED = "NOT_DELETED"
NOT_PURGEABLE = "NOT_PURGEABLE"
PURGE_STALE = "PURGE_STALE"
CONFIRM_MISMATCH = "CONFIRM_MISMATCH"
INVALID_TYPE = "INVALID_TYPE"
BUILDING_IN_TRASH = "BUILDING_IN_TRASH"
LEGAL_RECORDS = "LEGAL_RECORDS"

NOT_DELETED_MESSAGE = "L'elemento non è nel Cestino: si cancella definitivamente solo ciò che è già nel Cestino"
NOT_PURGEABLE_MESSAGE = ("Non si può eliminare definitivamente: ci sono dati collegati da conservare o da risolvere "
                         "prima. Nessun dato è stato cancellato")
PURGE_STALE_MESSAGE = ("Qualcosa è cambiato dopo il riepilogo: controlla di nuovo prima di confermare. "
                       "Nessun dato è stato cancellato")
CONFIRM_MISMATCH_MESSAGE = f"Per confermare scrivi esattamente «{PURGE_CONFIRM_TEXT}»"
INVALID_TYPE_MESSAGE = "Categoria non gestita dalla cancellazione definitiva"
BUILDING_IN_TRASH_LABEL = ("È un'unità di un edificio nel Cestino: si elimina definitivamente insieme al suo "
                           "edificio (Cestino › Richieste edifici)")
LEGAL_RECORDS_LABEL = ("Consensi e messaggi da conservare: la cancellazione definitiva resta bloccata finché non sarà "
                       "approvata la procedura di anonimizzazione")


# «come risolvere», per codice (il resto: la regola generale qui sotto)
_STORIA = ("È storia da conservare: l'elemento resta nel Cestino (non compare negli elenchi). "
           "Nessuna azione richiesta.")
COME_RISOLVERE = {
    NOT_DELETED: "Sposta prima l'elemento nel Cestino dalla sua scheda.",
    BUILDING_IN_TRASH: "Apri Cestino › Richieste edifici ed elimina definitivamente l'edificio con le sue unità.",
    LEGAL_RECORDS: ("Nessuna azione possibile per ora: serve la procedura di anonimizzazione, non ancora approvata. "
                    "Il contatto resta nel Cestino."),
    bd.VOICE_IN_PROGRESS: "Attendi la fine del comando di STIMA Voice e ripeti il controllo.",
    "MANDATE": _STORIA, "FK_property_sales": _STORIA, "FK_property_proposals": _STORIA,
    "FK_acquisitions": _STORIA, "FK_property_price_history": _STORIA, "FK_consent_events": _STORIA,
    "FK_property_photos": ("Se foto e documenti non vanno conservati: ripristina la scheda, rimuovili dalla scheda, "
                           "rimettila nel Cestino e ripeti il controllo."),
    "FK_property_documents": ("Se foto e documenti non vanno conservati: ripristina la scheda, rimuovili dalla "
                              "scheda, rimettila nel Cestino e ripeti il controllo."),
}
COME_RISOLVERE_ALTRO = ("Sono dati di altre schede o storia da conservare: aprile da «Collegamenti» per valutarle. "
                        "Finché ci sono, l'elemento resta nel Cestino.")


def _con_aiuto(blocchi: list[dict]) -> list[dict]:
    return [{**b, "help": b.get("help") or COME_RISOLVERE.get(b["code"], COME_RISOLVERE_ALTRO)} for b in blocchi]


class PurgeInvalid(ValidationError):
    def __init__(self, message, code, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


# ---------------------------------------------------------------------------
# primitive comuni
# ---------------------------------------------------------------------------

def _impronta(dati: dict) -> str:
    return hashlib.sha256(json.dumps(dati, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _blocca(blocchi: dict, codice: str, etichetta: str, n: int, *, href: str | None = None) -> None:
    if not n:
        return
    voce = blocchi.setdefault(codice, {"code": codice, "label": etichetta, "count": 0, "items": []})
    voce["count"] += int(n)
    if href:
        voce["href"] = href


def _etichetta(nome: str) -> str:
    if nome.startswith("communication_"):
        # il registro delle comunicazioni si legge qui solo per CONTARE (dal catalogo), mai per scriverlo
        return "Messaggi e comunicazioni (registro da conservare)"
    if nome == "consent_events":
        return "Consensi (registro legale da conservare)"
    return ETICHETTE.get(nome) or bd.ETICHETTE.get(nome) or f"Dati collegati ({nome})"


ETICHETTE = {
    "leads": "Lead (opportunità)",
    "activities": "Attività o interazioni",
    "tasks": "Task",
    "appointments": "Appuntamenti",
    "acquisitions": "Acquisizioni",
    "buy_requests": "Richieste acquirente del contatto",
    "property_contacts": "Proprietario o venditore di immobili",
    "property_sale_sellers": "Venditore in vendite registrate",
    "owner_accounts": "Account del portale proprietario",
    "property_visits": "Visite",
    "property_site_sources": "Richieste dal sito",
    "site_submissions": "Invii dal sito",
    "seller_timeline_events": "Storico Venditore",
    "followup_actions": "Azioni di follow-up",
    "next_best_actions": "Suggerimenti «Oggi»",
    "buy_request_interactions": "Interazioni con l'acquirente (proposte, visite, esiti)",
    "buy_request_task_links": "Task collegati alla richiesta",
    "buy_request_history": "Storico della richiesta (note, esiti, task)",
    "matches": "Abbinamenti con seguito (riscontri, proposte, interazioni)",
    "invisible_sale_candidates": "Candidati «vendita invisibile»",
    "property_sales": "Vendite registrate",
}

_PROPRI_ETICHETTE = {
    "contact_roles": "Ruoli del contatto",
    "communication_automation_controls": "Pausa delle automazioni (scritta dal Cestino)",
    "seller_revival_suppressions": "Esclusioni dai suggerimenti Venditore",
    "seller_timeline_events": "Traccia di pausa/ripresa delle automazioni (copiata nel registro)",
    "buy_request_locations": "Criteri: zone",
    "buy_request_typologies": "Criteri: tipologie",
    "buy_request_features": "Criteri: caratteristiche",
    "match_runs": "Calcoli di abbinamento",
    "match_exclusions": "Immobili esclusi dagli abbinamenti",
    "matches": "Abbinamenti calcolati senza seguito",
    "buy_request_history": "Storico di creazione e modifica (copiato nel registro)",
}

#: riferimenti senza FK (entity_type, entity_id) che BLOCCANO, come per gli edifici
_RIFERIMENTI_LIBERI = bd._RIFERIMENTI_LIBERI


def _scansione(cur, tabella: str, entity_id: int, propri: dict, *, kind: str) -> tuple[list[dict], list[dict]]:
    """Ogni FK verso `tabella.id` (dal catalogo, a ogni chiamata): le righe di
    una tabella in `propri` sono contenuti propri, salvo quelle che soddisfano
    il predicato associato (che BLOCCANO); ogni altra riga BLOCCA."""
    blocchi: dict[str, dict] = {}
    contati: dict[str, int] = {}
    for tab, col in bd._fk_verso(cur, tabella):
        nome = tab.split(".")[-1]
        filtro = f"{col} = %s"
        if nome in propri:
            cur.execute(f"SELECT count(*) AS n FROM {tab} WHERE {filtro}", (entity_id,))
            n = int(cur.fetchone()["n"])
            if propri[nome] is None:
                if n:
                    contati[nome] = contati.get(nome, 0) + n
                continue
            cur.execute(f"SELECT count(*) AS n FROM {tab} WHERE {filtro} AND ({propri[nome]})", (entity_id,))
            bloccanti = int(cur.fetchone()["n"])
            if n - bloccanti:
                contati[nome] = contati.get(nome, 0) + n - bloccanti
            _blocca(blocchi, f"FK_{nome}", _etichetta(nome), bloccanti)
            continue
        cur.execute(f"SELECT count(*) AS n FROM {tab} WHERE {filtro}", (entity_id,))
        _blocca(blocchi, f"FK_{nome}", _etichetta(nome), int(cur.fetchone()["n"]))
    for tab in _RIFERIMENTI_LIBERI:
        if not {"entity_type", "entity_id"} <= bd._colonne(cur, tab):
            continue
        cur.execute(f"SELECT count(*) AS n FROM {tab} WHERE entity_type = %s AND entity_id::text = %s",
                    (kind, str(entity_id)))
        _blocca(blocchi, f"REF_{tab}", bd.ETICHETTE.get(tab, f"Riferimenti in {tab}"), int(cur.fetchone()["n"]))
    return list(blocchi.values()), [{"table": t, "label": _PROPRI_ETICHETTE.get(t, t), "count": n}
                                    for t, n in contati.items()]


def riferimenti_voice(cur, kind: str, entity_id: int) -> dict:
    """I passi di STIMA Voice che hanno creato o usato l'elemento (per il
    risultato o per i riferimenti congelati) e i suoi «Annulla». Nessuna FK,
    registro append-only: si leggono, mai si scrivono. Stessa forma di
    `building_deletion.riferimenti_voice`."""
    esito = {"steps": [], "undos": []}
    e = str(int(entity_id))
    if {"intent", "result", "state"} <= bd._colonne(cur, "voice_command_steps") \
            and "frozen" in bd._colonne(cur, "voice_commands"):
        cur.execute("""
            SELECT s.command_id, s.ordinal, s.intent, s.state
              FROM voice_command_steps s JOIN voice_commands c ON c.id = s.command_id
             WHERE (s.result ->> 'entity' = %(k)s AND s.result ->> 'id' = %(e)s)
                OR s.result ->> (%(k)s || '_id') = %(e)s
                OR (c.frozen -> ('exec:' || s.ordinal || ':refs')) ->> %(k)s = %(e)s
             ORDER BY s.command_id, s.ordinal""", {"k": kind, "e": e})
        for r in cur.fetchall():
            esito["steps"].append({"command_id": int(r["command_id"]), "ordinal": int(r["ordinal"]),
                                   "intent": r["intent"], "state": r["state"], "property_ids": [],
                                   "building": False, "in_progress": r["state"] in bd.VOICE_STATI_IN_CORSO})
    if {"entity_type", "entity_id", "outcome"} <= bd._colonne(cur, "voice_step_undos"):
        cur.execute("SELECT id, command_id, ordinal, entity_type, entity_id, outcome, occurred_at FROM voice_step_undos "
                    "WHERE entity_type = %s AND entity_id = %s ORDER BY id", (kind, int(entity_id)))
        esito["undos"] = [{"id": int(r["id"]), "command_id": int(r["command_id"]), "ordinal": int(r["ordinal"]),
                           "entity_type": r["entity_type"], "entity_id": int(r["entity_id"]), "outcome": r["outcome"],
                           "occurred_at": r["occurred_at"]} for r in cur.fetchall()]
    return esito


def _voce_bloccante(voce: dict) -> list[dict]:
    in_corso = [p for p in voce["steps"] if p["in_progress"]]
    if not in_corso:
        return []
    return [{"code": bd.VOICE_IN_PROGRESS, "label": bd.VOICE_IN_PROGRESS_MESSAGE, "count": len(in_corso), "items": []}]


def _voce_conservata(voce: dict) -> list[dict]:
    voci = []
    conclusi = [p for p in voce["steps"] if not p["in_progress"]]
    if conclusi:
        voci.append({"code": "VOICE_COMMANDS", "label": ("Comandi di STIMA Voice che hanno creato o usato questi dati: "
                                                         "restano nello storico dei comandi vocali"),
                     "count": len(conclusi), "items": []})
    if voce["undos"]:
        voci.append({"code": "VOICE_UNDOS", "label": "Tentativi di «Annulla» di STIMA Voice: restano nello storico",
                     "count": len(voce["undos"]), "items": []})
    return voci


def _eventi_cestino(cur, kind: str, entity_id: int) -> int:
    cur.execute("SELECT count(*) AS n FROM record_lifecycle_events WHERE entity_type = %s AND entity_id = %s",
                (kind, entity_id))
    return int(cur.fetchone()["n"])


# ---------------------------------------------------------------------------
# adattatori
# ---------------------------------------------------------------------------

class _Immobile:
    kind, table = "property", "properties"

    def carica(self, cur, entity_id, lock):
        cur.execute(f"SELECT * FROM properties WHERE id = %s{' FOR UPDATE' if lock else ''}", (entity_id,))
        r = cur.fetchone()
        return dict(r) if r else None

    def etichetta(self, r):
        return bd._unit_label(r)

    def analizza(self, cur, r):
        blocchi = []
        if r.get("building_id") is not None:
            cur.execute("SELECT deleted_at FROM buildings WHERE id = %s", (r["building_id"],))
            b = cur.fetchone()
            if b and b["deleted_at"] is not None:
                blocchi.append({"code": BUILDING_IN_TRASH, "label": BUILDING_IN_TRASH_LABEL, "count": 1, "items": [],
                                "href": "#/cestino/richieste-edifici"})
        # le STESSE regole delle unita' di un edificio (nessun edificio: id 0 non esiste)
        altri, propri = bd.purge_blockers(cur, 0, [{**r, "in_trash": True}])
        storico, legami = bd._storico_stati(cur, [r["id"]]), bd._legami(cur, [r["id"]])
        voce = riferimenti_voice(cur, "property", r["id"])
        conservati = bd._conservati({"steps": [p for p in voce["steps"] if not p["in_progress"]],
                                     "undos": voce["undos"]}, storico, legami, {r["id"]: self.etichetta(r)})
        copia = {"status_history": storico, "owner_links": legami}
        prima = {k: r.get(k) for k in ("code", "title", "property_type", "floor", "internal_number", "record_kind",
                                       "building_id", "deleted_at", "deleted_reason")}
        return blocchi + altri, propri, conservati, voce, copia, prima

    def copia_attuale(self, cur, r):
        return {"status_history": bd._storico_stati(cur, [r["id"]]), "owner_links": bd._legami(cur, [r["id"]])}


class _Contatto:
    kind, table = "contact", "contacts"
    PROPRI = {
        "contact_roles": None, "communication_automation_controls": None, "seller_revival_suppressions": None,
        # la traccia di pausa/ripresa delle automazioni (la scrive anche lo spostamento nel Cestino) e'
        # del contatto e viene copiata nell'evento; ogni altro evento dello storico venditore BLOCCA
        "seller_timeline_events": ("event_type NOT IN ('automation_paused', 'automation_resumed') "
                                   "OR lead_id IS NOT NULL OR property_id IS NOT NULL OR stima_id IS NOT NULL"),
    }

    def carica(self, cur, entity_id, lock):
        cur.execute(f"SELECT * FROM contacts WHERE id = %s{' FOR UPDATE' if lock else ''}", (entity_id,))
        r = cur.fetchone()
        return dict(r) if r else None

    def etichetta(self, r):
        persona = " ".join(x for x in (r.get("first_name"), r.get("last_name")) if x)
        return r.get("display_name") or persona or r.get("company_name") or f"Contatto #{r['id']}"

    def analizza(self, cur, r):
        blocchi, propri = _scansione(cur, "contacts", r["id"], self.PROPRI, kind="contact")
        legali = [b for b in blocchi if b["code"] in ("FK_consent_events",) or b["code"].startswith("FK_communication_")]
        if legali:
            blocchi.insert(0, {"code": LEGAL_RECORDS, "label": LEGAL_RECORDS_LABEL,
                               "count": sum(b["count"] for b in legali), "items": []})
        voce = riferimenti_voice(cur, "contact", r["id"])
        copia = self.copia_attuale(cur, r)
        conservati = _voce_conservata(voce)
        # nel registro NESSUN dato personale: solo cio' che serve a ricostruire l'operazione
        prima = {"agency_id": r.get("agency_id"), "status": r.get("status"), "deleted_at": r.get("deleted_at"),
                 "deleted_reason": r.get("deleted_reason"), "created_at": r.get("created_at")}
        return blocchi, propri, conservati, voce, copia, prima

    def copia_attuale(self, cur, r):
        cur.execute("SELECT role FROM contact_roles WHERE contact_id = %s ORDER BY role", (r["id"],))
        ruoli = [x["role"] for x in cur.fetchall()]
        cur.execute("SELECT event_type, occurred_at FROM seller_timeline_events WHERE contact_id = %s "
                    "AND event_type IN ('automation_paused', 'automation_resumed') ORDER BY id", (r["id"],))
        traccia = [{"event_type": x["event_type"], "occurred_at": x["occurred_at"]} for x in cur.fetchall()]
        return {"roles": ruoli, "automation_trail": traccia}


_STORICO_PROPRIO = "('request_created', 'request_updated')"


class _Richiesta:
    kind, table = "buy_request", "buy_requests"
    PROPRI = {
        "buy_request_locations": None, "buy_request_typologies": None, "buy_request_features": None,
        "match_runs": None, "match_exclusions": None,
        # lo storico di creazione e modifica e' della richiesta; ogni altro evento e' storia e BLOCCA
        "buy_request_history": f"event_type NOT IN {_STORICO_PROPRIO}",
        # un abbinamento calcolato e' un suggerimento; con un seguito (riscontro, proposta,
        # interazione, evento) e' storia e BLOCCA
        "matches": ("EXISTS (SELECT 1 FROM match_feedback f WHERE f.match_id = matches.id) "
                    "OR EXISTS (SELECT 1 FROM property_proposals pp WHERE pp.match_id = matches.id) "
                    "OR EXISTS (SELECT 1 FROM buy_request_interactions i WHERE i.match_id = matches.id) "
                    f"OR EXISTS (SELECT 1 FROM buy_request_history h WHERE h.match_id = matches.id "
                    f"AND h.event_type NOT IN {_STORICO_PROPRIO})"),
    }

    def carica(self, cur, entity_id, lock):
        cur.execute(f"SELECT * FROM buy_requests WHERE id = %s{' FOR UPDATE' if lock else ''}", (entity_id,))
        r = cur.fetchone()
        return dict(r) if r else None

    def etichetta(self, r):
        return r.get("title") or f"Richiesta #{r['id']}"

    def analizza(self, cur, r):
        blocchi, propri = _scansione(cur, "buy_requests", r["id"], self.PROPRI, kind="buy_request")
        voce = riferimenti_voice(cur, "buy_request", r["id"])
        copia = self.copia_attuale(cur, r)
        conservati = _voce_conservata(voce)
        if copia["history"]:
            conservati.append({"code": "REQUEST_HISTORY", "count": len(copia["history"]), "items": [],
                               "label": "Storico di creazione e modifica: copiato nel registro prima della cancellazione"})
        prima = {"agency_id": r.get("agency_id"), "contact_id": r.get("contact_id"), "status": r.get("status"),
                 "deleted_at": r.get("deleted_at"), "deleted_reason": r.get("deleted_reason"),
                 "created_at": r.get("created_at")}
        return blocchi, propri, conservati, voce, copia, prima

    def copia_attuale(self, cur, r):
        cur.execute(f"SELECT id, event_type, created_at FROM buy_request_history WHERE buy_request_id = %s "
                    f"AND event_type IN {_STORICO_PROPRIO} ORDER BY id", (r["id"],))
        return {"history": [dict(x) for x in cur.fetchall()]}


ADATTATORI = {"property": _Immobile(), "contact": _Contatto(), "buy_request": _Richiesta()}


def _adattatore(kind: str):
    a = ADATTATORI.get(kind)
    if a is None:
        raise PurgeInvalid(INVALID_TYPE_MESSAGE, INVALID_TYPE, allowed=list(ADATTATORI))
    return a


def _riepilogo(a, cur, r) -> dict:
    nel_cestino = r.get("deleted_at") is not None
    if not nel_cestino:
        blocchi = _con_aiuto([{"code": NOT_DELETED, "label": NOT_DELETED_MESSAGE, "count": 1, "items": []}])
        return {"blocchi": blocchi, "propri": [], "conservati": [], "voce": {"steps": [], "undos": []},
                "copia": {}, "prima": {}, "impronta": _impronta({"k": a.kind, "id": r["id"], "deleted": None})}
    blocchi, propri, conservati, voce, copia, prima = a.analizza(cur, r)
    blocchi = blocchi + [b for b in _voce_bloccante(voce) if b["code"] not in {x["code"] for x in blocchi}]
    impronta = _impronta({"k": a.kind, "id": r["id"], "deleted_at": r.get("deleted_at"),
                          "blockers": sorted((b["code"], b["count"]) for b in blocchi),
                          "will_delete": sorted((p["table"], p["count"]) for p in propri),
                          "preserved": sorted((c["code"], c["count"]) for c in conservati), "copy": copia})
    return {"blocchi": _con_aiuto(blocchi), "propri": propri, "conservati": conservati, "voce": voce, "copia": copia,
            "prima": prima, "impronta": impronta}


# ---------------------------------------------------------------------------
# API del motore
# ---------------------------------------------------------------------------

def purge_check(ctx, kind: str, entity_id: int) -> dict:
    """Sola lettura: ammissibile? Cosa si cancella, cosa resta, cosa blocca."""
    _sa.require_super_admin(ctx, audit_action=AUDIT_DENIED)
    a = _adattatore(kind)
    with core_cursor() as (_, cur):
        cur.execute("SET TRANSACTION READ ONLY")
        _sa.require_super_admin_db(cur, ctx, audit_action=AUDIT_DENIED)
        r = a.carica(cur, entity_id, False)
        if r is None:
            raise NotFoundError(f"{kind} {entity_id} not found")
        s = _riepilogo(a, cur, r)
    return {"kind": kind, "id": int(entity_id), "agency_id": r.get("agency_id"), "label": a.etichetta(r),
            "in_trash": r.get("deleted_at") is not None, "eligible": not s["blocchi"], "blockers": s["blocchi"],
            "will_delete": s["propri"], "preserved": s["conservati"], "fingerprint": s["impronta"],
            "confirm_text": PURGE_CONFIRM_TEXT}


def _verifica_copia(cur, evento_id: int, a, r, copia: dict) -> None:
    cur.execute("SELECT entity_type, entity_id, action, before_state FROM record_lifecycle_events WHERE id = %s",
                (evento_id,))
    e = cur.fetchone()
    if not e or e["action"] != "purge" or e["entity_type"] != a.kind or int(e["entity_id"]) != int(r["id"]):
        bd._fallisci("evento_purge")
    registrata = (e["before_state"] or {}).get("copied") or {}
    if registrata != bd._json(a.copia_attuale(cur, r)) or registrata != bd._json(copia):
        bd._fallisci("copia_storico")


def _cancella_propri(cur, a, entity_id: int, attesi: list[dict]) -> None:
    """Cancella ESPLICITAMENTE i contenuti propri dichiarati dall'adattatore
    (anche dove la FK e' SET NULL: niente righe orfane). A blocchi zero ogni
    riga che punta all'elemento in quelle tabelle e' propria; i conteggi
    devono coincidere con il riepilogo confermato, altrimenti nulla cambia.
    Senza `PROPRI` (immobili) valgono le regole del motore edifici."""
    propri = getattr(a, "PROPRI", None)
    if not propri:
        return
    fatti: dict[str, int] = {}
    for tab, col in bd._fk_verso(cur, a.table):
        nome = tab.split(".")[-1]
        if nome not in propri:
            continue
        cur.execute(f"DELETE FROM {tab} WHERE {col} = %s", (entity_id,))
        if cur.rowcount:
            fatti[nome] = fatti.get(nome, 0) + cur.rowcount
    if fatti != {p["table"]: p["count"] for p in attesi}:
        bd._fallisci("contenuti_propri")


def _verifica_dopo(cur, a, entity_id: int, evento_id: int, voce_prima: dict) -> None:
    cur.execute(f"SELECT count(*) AS n FROM {a.table} WHERE id = %s", (entity_id,))
    if cur.fetchone()["n"]:
        bd._fallisci("elemento_presente")
    for tab, col in bd._fk_verso(cur, a.table):
        cur.execute(f"SELECT count(*) AS n FROM {tab} WHERE {col} = %s", (entity_id,))
        if cur.fetchone()["n"]:
            bd._fallisci(f"riferimenti_{tab.split('.')[-1]}")
    cur.execute("SELECT count(*) AS n FROM record_lifecycle_events WHERE id = %s AND action = 'purge'", (evento_id,))
    if cur.fetchone()["n"] != 1:
        bd._fallisci("evento_purge")
    if bd._chiavi_voce(riferimenti_voice(cur, a.kind, entity_id)) != bd._chiavi_voce(voce_prima):
        bd._fallisci("riferimenti_voice_dopo")


def purge(ctx, kind: str, entity_id: int, confirm_text: str, expected_fingerprint: str) -> dict:
    """La cancellazione definitiva, atomica. Ogni rifiuto o errore: nulla cambia."""
    _sa.require_super_admin(ctx, audit_action=AUDIT_DENIED)
    a = _adattatore(kind)
    if (confirm_text or "").strip() != PURGE_CONFIRM_TEXT:
        raise PurgeInvalid(CONFIRM_MISMATCH_MESSAGE, CONFIRM_MISMATCH)
    with core_cursor() as (conn, cur):
        _sa.require_super_admin_db(cur, ctx, audit_action=AUDIT_DENIED)
        r = a.carica(cur, entity_id, True)                         # la riga, bloccata fino al commit
        if r is None:
            raise NotFoundError(f"{kind} {entity_id} not found")
        if r.get("deleted_at") is None:
            raise LifecycleConflict(NOT_DELETED_MESSAGE, NOT_DELETED)
        s = _riepilogo(a, cur, r)                                  # i blocchi RICALCOLATI ora
        if s["blocchi"]:
            raise LifecycleConflict(NOT_PURGEABLE_MESSAGE, NOT_PURGEABLE, blockers=s["blocchi"])
        if s["impronta"] != expected_fingerprint:
            raise LifecycleConflict(PURGE_STALE_MESSAGE, PURGE_STALE, fingerprint=s["impronta"])
        agency_id = r["agency_id"]
        voce = s["voce"]
        evento = bd._evento(
            cur, ctx, agency_id, a.kind, int(r["id"]), "purge",
            before={**s["prima"], "copied": s["copia"]},
            metadata={"confirmed": True, "own_content": s["propri"],
                      "preserved_references": {"voice_steps": [bd._voce_meta(p) for p in voce["steps"]],
                                               "voice_undos": voce["undos"]},
                      "trash_events": _eventi_cestino(cur, a.kind, int(r["id"]))})
        _verifica_copia(cur, evento, a, r, s["copia"])             # la copia c'e' ed e' identica: poi il DELETE
        _cancella_propri(cur, a, int(r["id"]), s["propri"])        # i soli contenuti propri, esplicitamente
        cur.execute(f"DELETE FROM {a.table} WHERE id = %s AND agency_id = %s AND deleted_at IS NOT NULL",
                    (int(r["id"]), agency_id))
        if cur.rowcount != 1:
            raise LifecycleConflict(NOT_PURGEABLE_MESSAGE, NOT_PURGEABLE, blockers=[])
        _verifica_dopo(cur, a, int(r["id"]), evento, voce)         # nessun riferimento inconsistente
        from platform_admin.transaction import audit_then_commit
        audit_then_commit(conn, ctx, action=AUDIT_PURGE, target_type=a.kind, target_id=int(r["id"]),
                          target_agency_id=agency_id,
                          metadata={"own_content": {p["table"]: p["count"] for p in s["propri"]},
                                    "preserved": {c["code"]: c["count"] for c in s["conservati"]},
                                    "lifecycle_event_id": evento})
    return {"kind": kind, "id": int(entity_id), "purged": True, "deleted_own_content": s["propri"],
            "preserved": s["conservati"]}
