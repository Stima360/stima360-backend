"""CATALOGO-CANONICO-1 (FASE D) - DAL SITO ALLA SCHEDA IMMOBILE.

Decisione funzionale (Giorgio, 6 ottobre 2026):

  * l'immobile nasce AUTOMATICAMENTE dalla prima stima salvata con contatto e
    lead (`/api/salva_stima` dopo il bridge CORE), non dal calcolo anonimo
    (`/api/stima_base` non scrive nulla qui);
  * nasce come CENSIMENTO (`record_kind = 'census'`, stato `draft`), con
    provenienza Stima360 (`source = 'stima360'`), senza incarico, senza agente
    e senza alcuna attivazione commerciale;
  * il completamento della stima dettagliata aggiorna lo STESSO immobile;
  * un ritentativo dello stesso invio non crea un doppione;
  * un dato corretto a mano dall'agente non si sovrascrive mai;
  * stime diverse forse riferite allo stesso immobile: si SEGNALA il possibile
    doppione e si consente il collegamento esplicito. Nessuna unione automatica
    per indirizzo.

COME
----
* Valori: SOLO quelli che il sito ha inviato davvero (il payload grezzo),
  tradotti con il catalogo canonico (`property/site_catalog.py`). I default
  che `salva_stima` mette nella riga `stime` (piano 1, 3 locali, ascensore,
  anno 2000, via "Zona"...) non diventano mai dati dell'immobile.
* Idempotenza della stessa stima: la scheda nasce con una `client_request_id`
  deterministica (uuid5 della stima) e la provenienza ha un indice UNIQUE
  parziale su `stima_id` attivo; un secondo passaggio restituisce la riga.
* Ricezione PRIMA del trasferimento (migration 088, `site_submissions`): ogni
  invio si conserva subito, con i soli valori dell'immobile COME il form li ha
  inviati (nessun dato di contatto). Il trasferimento nella scheda segna la
  riga `synced` nella stessa transazione; se fallisce la riga resta `failed`
  (tipo di errore, tentativi) e `scripts/site_sync_recover.py` la riprende,
  in modo idempotente. Nessuna ricostruzione da `stime` (default e interi).
* Ritentativo dello stesso invio: SOLO con l'identita' stabile della
  richiesta (`client_request_id`, UUID scelto dal sito e riusato nei
  ritentativi). Stessa agenzia e stessa identita' -> la stessa scheda, senza
  limite di tempo; un lock transazionale serializza le richieste concorrenti.
  Senza identita' (client attuale) due invii uguali dello stesso contatto NON
  sono la prova di un ritentativo: nasce una scheda nuova, segnalata come
  possibile doppione («stesso contatto e stessi dati»), mai unita.
* Stima dettagliata precompilata (`/api/prefill` legge `stime`, con i default
  e i metri quadri interi): un valore rimandato UGUALE al precompilato non e'
  una dichiarazione; uno diverso e' una correzione esplicita del cliente; i
  campi elencati in `campi_dichiarati` (se il sito li manda) valgono sempre.
* Regola di scrittura per campo (dettagliata e collegamento esplicito):
    - valore uguale -> niente da fare;
    - campo vuoto mai scritto dal sito -> si scrive;
    - campo con l'ultimo valore scritto dal sito (l'agente non l'ha toccato)
      -> si aggiorna;
    - altrimenti l'agente lo ha corretto -> CONFLITTO, mostrato nella scheda
      con «Applica» / «Ignora». Un valore vuoto non si scrive mai.
  "L'ultimo valore scritto dal sito" e' l'istantanea `site_values` delle
  provenienze della scheda.
* Pertinenze: righe di `property_accessories` (`source = 'stima360'`), stato
  catastale "Da chiarire" (il sito non dice se c'e' un subalterno; nessuna
  unita', nessun edificio, nessuna relazione inventati). Balconi: compresi.
* Fail-open: `safe_*` non lascia uscire eccezioni e non cambia mai la risposta
  del sito; senza la 087 e la 088 non riceve e non scrive nulla (la
  provenienza gia' scritta si legge e si gestisce con la sola 087).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from psycopg2.extras import Json

from core import property_trash as _property_trash
from core import repository as core_repository
from core.database import core_cursor
from core.exceptions import ConflictError, NotFoundError, ValidationError

from . import census as _census
from . import lifecycle as _lifecycle
from . import repository
from . import site_catalog as cat
from .catalog import TITLE_SOURCE_FIELDS, generated_title

log = logging.getLogger("stima360.site_sync")

#: Spazio dei nomi della chiave di idempotenza della scheda nata da una stima.
NAMESPACE = uuid.UUID("6f0b6c1e-6a52-5d0e-9c3f-1f4a0d3e5a87")
_MANCA = object()
_ACC = "accessory:"
#: `properties.metadata` -> campi «Da verificare» nonostante un valore tecnico.
UNVERIFIED_KEY = "site_unverified"

NOT_INSTALLED = "La provenienza dal sito non e' installata su questo database (migration 087 non applicata)."
SELLER_LINK_ACTIVE = ("La stima e' gia' un'opportunita' Venditore su questo immobile: "
                      "chiudila o sospendila prima di collegarla a un altro")


class SiteSyncError(ConflictError):
    def __init__(self, message, code, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


def client_request_id_for(stima_id: int) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"stima360:stima:{int(stima_id)}")


# ---------------------------------------------------------------------------
# utilita'
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _provenienza(cur) -> bool:
    """La 087 (provenienza): basta per leggere e gestire le provenienze gia'
    scritte (lista, Applica/Ignora, doppioni, collegamento)."""
    cur.execute("SELECT to_regclass('public.property_site_sources') IS NOT NULL AS pronta")
    return bool(cur.fetchone()["pronta"])


def _installata(cur) -> bool:
    """087 (provenienza) e 088 (ricezione degli invii): per RICEVERE e
    trasferire un invio servono entrambe."""
    cur.execute("SELECT to_regclass('public.property_site_sources') IS NOT NULL"
                "   AND to_regclass('public.site_submissions') IS NOT NULL AS pronta")
    return bool(cur.fetchone()["pronta"])


def _j(valore):
    if isinstance(valore, Decimal):
        return str(valore)
    if isinstance(valore, dict):
        return {k: _j(v) for k, v in valore.items()}
    return valore


_canon = cat.canon          # un solo confronto, quello del catalogo
_uguale = cat.same


def _acc_vuoto(voce) -> bool:
    return all(voce.get(k) is None for k in ("surface_sqm", "quantity"))


def _acc_compatibile(riga: dict, nuovo: dict) -> bool:
    """La riga dice gia' quello che il sito dice (un valore non indicato dal
    sito non contraddice nulla)."""
    return all(nuovo.get(k) is None or _uguale(riga.get(k), nuovo.get(k)) for k in ("surface_sqm", "quantity"))


def _acc_identico(riga: dict, istantanea: dict) -> bool:
    return all(_uguale(riga.get(k), (istantanea or {}).get(k)) for k in ("surface_sqm", "quantity"))


def _attivita(cur, ctx, property_id, testo, **metadata) -> None:
    if ctx is None:
        return
    core_repository.create_activity_with_cursor(cur, {
        "contact_id": None, "lead_id": None, "stima_id": None, "property_id": property_id,
        "activity_type": "system", "direction": None, "channel": None, "subject": None,
        "description": testo, "outcome": None, "occurred_at": None, "created_by": None,
        "metadata": {"context": "site_sync", **metadata},
    }, ctx=ctx)


def _immobile(cur, agency_id, property_id, *, lock=False):
    """La scheda nell'agenzia, fuori dal Cestino (404 altrimenti)."""
    if lock:
        cur.execute("SELECT * FROM properties WHERE id = %s AND agency_id = %s FOR UPDATE", (property_id, agency_id))
    else:
        cur.execute(f"SELECT * FROM properties p WHERE p.id = %s AND p.agency_id = %s AND {_property_trash.live('p')}",
                    (property_id, agency_id))
    riga = repository.row(cur.fetchone())
    if riga is None or riga.get("deleted_at") is not None:
        raise NotFoundError(f"property {property_id} not found")
    return riga


def _istantanea(cur, property_id) -> dict:
    """L'ultimo valore scritto dal sito per campo, su questa scheda."""
    cur.execute("SELECT site_values FROM property_site_sources WHERE property_id = %s ORDER BY updated_at, id",
                (property_id,))
    valori: dict = {}
    for riga in cur.fetchall():
        valori.update(riga["site_values"] or {})
    return valori


def _ignorati(cur, property_id) -> set:
    cur.execute("SELECT conflicts FROM property_site_sources WHERE property_id = %s", (property_id,))
    fuori = set()
    for riga in cur.fetchall():
        for c in riga["conflicts"] or []:
            if c.get("status") == "ignored":
                fuori.add((c["field"], repr(_canon_voce(c.get("site_value")))))
    return fuori


def _canon_voce(valore):
    if isinstance(valore, dict):
        return tuple(sorted((k, _canon(v)) for k, v in valore.items()))
    return _canon(valore)


# ---------------------------------------------------------------------------
# scrittura con la regola per campo
# ---------------------------------------------------------------------------

def _da_verificare(prop: dict) -> dict:
    """I campi che la scheda mostra «Da verificare» (`metadata.site_unverified`)."""
    return dict(((prop.get("metadata") or {}).get(UNVERIFIED_KEY) or {}))


def _scrivi_campi(cur, prop: dict, valori: dict) -> dict:
    if not valori:
        return prop
    valori = dict(valori)
    sospesi = _da_verificare(prop)
    if any(f in sospesi for f in valori):
        # un valore dichiarato chiude il «Da verificare» di quel campo
        metadata = dict(prop.get("metadata") or {})
        restanti = {k: v for k, v in sospesi.items() if k not in valori}
        if restanti:
            metadata[UNVERIFIED_KEY] = restanti
        else:
            metadata.pop(UNVERIFIED_KEY, None)
        valori["metadata"] = metadata
    if any(f in valori for f in TITLE_SOURCE_FIELDS) and prop.get("title") == generated_title(prop):
        valori["title"] = generated_title({**prop, **valori})
    if "metadata" in valori:
        valori["metadata"] = Json(valori["metadata"])
    cur.execute(f"UPDATE properties SET {', '.join(f'{k} = %s' for k in valori)}, updated_at = NOW() "
                "WHERE id = %s RETURNING *", [*valori.values(), prop["id"]])
    return repository.row(cur.fetchone())


def _pertinenze_autonome(cur, property_id) -> dict:
    """PERTINENZE-1: i tipi che su questa scheda sono gia' UNITA' AUTONOME
    collegate (un accessorio trasformato con «Chiarisci › E' separata», o una
    pertinenza del tipo indicato): {tipo: codice}. Il sito che ridichiara
    quel tipo non ricrea l'accessorio (doppio conteggio). Valida anche senza
    la 089: il tipo si legge anche dalla provenienza dell'accessorio."""
    cur.execute(f"""SELECT p.code, coalesce(to_jsonb(p)->>'pertinenza_kind', p.metadata->'from_accessory'->>'kind') AS kind
                      FROM properties p WHERE p.parent_property_id = %s AND {_property_trash.live('p')}
                       AND p.archived_at IS NULL ORDER BY p.id""", (property_id,))
    return {r["kind"]: r["code"] for r in cur.fetchall() if r["kind"]}


def _inserisci_accessorio(cur, property_id, kind, voce) -> dict:
    cur.execute("INSERT INTO property_accessories (property_id, kind, cadastral_status, surface_sqm, quantity, source) "
                "VALUES (%s, %s, %s, %s, %s, 'stima360') RETURNING *",
                (property_id, kind, cat.ACCESSORY_KINDS[kind]["status"], voce.get("surface_sqm"), voce.get("quantity")))
    return repository.row(cur.fetchone())


def _applica(cur, prop: dict, campi: dict, accessori: dict, *, precedente: dict, ignorati: set,
             pertinenze_dichiarate: bool, origine: str, ancora_presenti: set | None = None) -> tuple[dict, dict, list]:
    """(scheda aggiornata, istantanea scritta, conflitti nuovi).

    `ancora_presenti`: pertinenze ancora nell'elenco del sito ma tolte da
    `accessori` perche' rimaste come precompilate (non sono state tolte)."""
    scritti: dict = {}
    da_scrivere: dict = {}
    conflitti: list = []

    def conflitto(campo, valore_sito, attuale):
        if (campo, repr(_canon_voce(valore_sito))) in ignorati:
            return
        conflitti.append({"id": uuid.uuid4().hex[:12], "field": campo, "site_value": _j(valore_sito),
                          "current_value": _j(attuale), "status": "open", "origin": origine, "detected_at": _now()})

    sospesi = _da_verificare(prop)
    for campo, nuovo in campi.items():
        if nuovo is None:
            continue
        # un valore tecnico (la tipologia NOT NULL) «Da verificare» vale vuoto
        attuale = None if campo in sospesi else prop.get(campo)
        prima = precedente.get(campo, _MANCA)
        if _uguale(attuale, nuovo):
            scritti[campo] = nuovo
        elif attuale is None and prima is _MANCA:
            da_scrivere[campo] = nuovo
            scritti[campo] = nuovo
        elif prima is not _MANCA and attuale is not None and _uguale(attuale, prima):
            da_scrivere[campo] = nuovo
            scritti[campo] = nuovo
        elif prima is not _MANCA and attuale is None and _uguale(nuovo, prima):
            continue                       # l'agente lo ha svuotato: decisione sua
        else:
            conflitto(campo, nuovo, attuale)
    prop = _scrivi_campi(cur, prop, da_scrivere)

    cur.execute("SELECT * FROM property_accessories WHERE property_id = %s ORDER BY id FOR UPDATE", (prop["id"],))
    esistenti = [dict(r) for r in cur.fetchall()]
    autonome = _pertinenze_autonome(cur, prop["id"]) if accessori else {}
    for kind, voce in accessori.items():
        chiave = _ACC + kind
        nuovo = {"surface_sqm": voce.get("surface_sqm"), "quantity": voce.get("quantity")}
        prima = precedente.get(chiave, _MANCA)
        stesse = [a for a in esistenti if a["kind"] == kind]
        if not stesse and kind in autonome:
            # gia' unita' autonoma collegata: nessun accessorio doppio, nessun conflitto
            scritti[chiave] = prima if prima is not _MANCA else _j(nuovo)
            continue
        if not stesse:
            if prima is _MANCA:
                _inserisci_accessorio(cur, prop["id"], kind, nuovo)
                scritti[chiave] = _j(nuovo)
            else:
                conflitto(chiave, {"present": True, **nuovo}, None)     # tolta dall'agente
            continue
        compatibile = next((a for a in stesse if _acc_compatibile(a, nuovo)), None)
        if compatibile is not None:
            # niente da scrivere: la riga dice gia' quello che dice il sito
            scritti[chiave] = _j({k: compatibile.get(k) for k in nuovo})
            continue
        if len(stesse) == 1 and prima is not _MANCA and _acc_identico(stesse[0], prima):
            aggiorna = {k: v for k, v in nuovo.items() if v is not None}
            cur.execute(f"UPDATE property_accessories SET {', '.join(f'{k} = %s' for k in aggiorna)}, "
                        "updated_at = NOW() WHERE id = %s", [*aggiorna.values(), stesse[0]["id"]])
            scritti[chiave] = _j({k: (nuovo[k] if nuovo[k] is not None else stesse[0].get(k)) for k in nuovo})
            continue
        if _acc_vuoto(nuovo):
            continue
        conflitto(chiave, {"present": True, **nuovo},
                  {"present": True, "surface_sqm": stesse[0].get("surface_sqm"), "quantity": stesse[0].get("quantity")})

    if pertinenze_dichiarate:
        for chiave in [k for k in precedente if k.startswith(_ACC)]:
            kind = chiave[len(_ACC):]
            if kind in accessori or kind in (ancora_presenti or ()):
                continue
            dal_sito = [a for a in esistenti if a["kind"] == kind and a.get("source") == "stima360"]
            if dal_sito:
                conflitto(chiave, {"present": False},
                          {"present": True, "surface_sqm": dal_sito[0].get("surface_sqm"),
                           "quantity": dal_sito[0].get("quantity")})
    return prop, scritti, conflitti


def _unisci_conflitti(vecchi: list, nuovi: list, risolti: set) -> list:
    """I conflitti aperti dello stesso campo sono sostituiti dai nuovi; quelli
    il cui campo ora coincide col sito si chiudono (`superseded`)."""
    campi_nuovi = {c["field"] for c in nuovi}
    tenuti = []
    for c in vecchi or []:
        if c.get("status") == "open" and (c["field"] in campi_nuovi or c["field"] in risolti):
            tenuti.append({**c, "status": "superseded", "resolved_at": _now()})
        else:
            tenuti.append(c)
    return tenuti + nuovi


def _chiudi_altrove(cur, property_id, sorgente_id, campi: set) -> None:
    """Un campo appena allineato o in conflitto su una provenienza chiude i
    conflitti aperti dello stesso campo sulle altre provenienze della scheda."""
    if not campi:
        return
    cur.execute("SELECT id, conflicts FROM property_site_sources WHERE property_id = %s AND id <> %s FOR UPDATE",
                (property_id, sorgente_id))
    for riga in [dict(r) for r in cur.fetchall()]:
        cambiati = False
        lista = []
        for c in riga["conflicts"] or []:
            if c.get("status") == "open" and c["field"] in campi:
                c = {**c, "status": "superseded", "resolved_at": _now()}
                cambiati = True
            lista.append(c)
        if cambiati:
            cur.execute("UPDATE property_site_sources SET conflicts = %s, updated_at = NOW() WHERE id = %s",
                        (Json(lista), riga["id"]))


# ---------------------------------------------------------------------------
# possibili doppioni (solo segnalati)
# ---------------------------------------------------------------------------

def _doppioni(cur, agency_id, property_id, contact_id, campi: dict, impronta: str | None = None) -> list[dict]:
    """Possibili doppioni, solo SEGNALATI: stesso contatto e stessi dati
    dichiarati (forse un ritentativo, ma senza identita' della richiesta non
    e' una prova), stesso contatto, stesso indirizzo."""
    citta, via, civico = campi.get("city"), campi.get("address"), campi.get("civic_number")
    per_indirizzo = bool(citta and via and civico)
    cur.execute(f"""
        SELECT p.id, p.code, p.title, p.record_kind,
               EXISTS (SELECT 1 FROM property_site_sources s WHERE s.property_id = p.id AND s.status = 'active'
                          AND s.contact_id = %s AND s.fingerprint = %s) AS stessi_dati,
               EXISTS (SELECT 1 FROM property_contacts pc WHERE pc.property_id = p.id AND pc.contact_id = %s) AS stesso_contatto,
               (%s AND lower(btrim(p.city)) = lower(btrim(%s))
                   AND lower(regexp_replace(btrim(p.address), '\\s+', ' ', 'g')) = lower(regexp_replace(btrim(%s), '\\s+', ' ', 'g'))
                   AND upper(btrim(p.civic_number)) = upper(btrim(%s))) AS stesso_indirizzo
          FROM properties p
         WHERE p.agency_id = %s AND p.id <> %s AND p.archived_at IS NULL AND {_property_trash.live('p')}
         ORDER BY p.id""",
                (contact_id, impronta or "", contact_id, per_indirizzo, citta or "", via or "", civico or "",
                 agency_id, property_id))
    trovati = []
    for r in cur.fetchall():
        motivi = [m for m, ok in (("same_submission_data", r["stessi_dati"]), ("same_contact", r["stesso_contatto"]),
                                  ("same_address", r["stesso_indirizzo"])) if ok]
        if motivi:
            trovati.append({"property_id": r["id"], "code": r["code"], "reasons": motivi, "dismissed": False})
    return trovati[:10]


# ---------------------------------------------------------------------------
# collegamenti con il lead e il contatto
# ---------------------------------------------------------------------------

def _collega_lead(cur, property_id, lead_id, relazione) -> None:
    if lead_id is None:
        return
    cur.execute("INSERT INTO property_leads (property_id, lead_id, relation_type) VALUES (%s, %s, %s) "
                "ON CONFLICT (property_id, lead_id) DO NOTHING", (property_id, lead_id, relazione))


def _collega_contatto(cur, property_id, contact_id) -> None:
    """Il contatto della stima come `contact` (ruolo neutro): la stima non
    prova la proprieta', il ruolo di proprietario lo decide l'agente."""
    if contact_id is None:
        return
    cur.execute("SELECT 1 FROM property_contacts WHERE property_id = %s AND contact_id = %s LIMIT 1",
                (property_id, contact_id))
    if cur.fetchone() is None:
        cur.execute("INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'contact') "
                    "ON CONFLICT DO NOTHING", (property_id, contact_id))


# ---------------------------------------------------------------------------
# 0. RICEZIONE: l'invio si conserva prima di qualunque trasferimento
# ---------------------------------------------------------------------------

def _dichiarato(esito: cat.Esito) -> dict:
    return {**esito.declared,
            "accessories": {k: _j({kk: vv for kk, vv in v.items()}) for k, v in esito.accessories.items()},
            "pertinenze_declared": esito.pertinenze_declared}


def _ricevi(cur, *, kind, agency_id, stima_id, detail_id, raw, contact_id=None, lead_id=None, prefill=None):
    """La riga di `site_submissions` (una per invio). Idempotente sulla stima
    rapida e sulla riga del dettaglio. Restituisce la riga."""
    valori, altre = cat.declared_payload(raw)
    if altre:
        valori["_altre_chiavi"] = altre            # solo i NOMI: dice cosa il CRM non legge ancora
    chiave = cat.request_id(raw) if kind == "quick" else None
    campi = cat.declared_fields(raw) if kind == "detail" else None
    vincolo = "(stima_id) WHERE kind = 'quick'" if kind == "quick" else "(detail_id) WHERE kind = 'detail'"
    cur.execute(f"""INSERT INTO site_submissions (agency_id, kind, stima_id, detail_id, client_request_id, declared,
                        declared_fields, prefill, contact_id, lead_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT {vincolo} DO NOTHING RETURNING *""",
                (agency_id, kind, stima_id, detail_id, str(chiave) if chiave else None, Json(valori),
                 Json(campi) if campi is not None else None, Json(prefill) if prefill is not None else None,
                 contact_id, lead_id))
    riga = repository.row(cur.fetchone())
    if riga is None:
        colonna, valore = ("stima_id", stima_id) if kind == "quick" else ("detail_id", detail_id)
        cur.execute(f"SELECT * FROM site_submissions WHERE kind = %s AND {colonna} = %s", (kind, valore))
        riga = repository.row(cur.fetchone())
    return riga


def _prefill_di(cur, stima_id) -> dict | None:
    """I valori che `/api/prefill` ha servito per questa stima: le stesse
    colonne di `stime` (default e interi compresi), senza i dati di contatto."""
    colonne = ", ".join(f"s.{c}" for _k, c in cat.PREFILL_KEYS)
    cur.execute(f"SELECT {colonne} FROM stime s WHERE s.id = %s", (stima_id,))
    riga = cur.fetchone()
    if riga is None:
        return None
    return {k: _j(riga[c]) if not isinstance(riga[c], (int, float, str, bool, type(None))) else riga[c]
            for k, c in cat.PREFILL_KEYS}


def _esito_invio(cur, sub_id, status, *, reason=None, property_id=None, error=None, attempt=False):
    cur.execute("""UPDATE site_submissions SET status = %s, reason = %s, property_id = COALESCE(%s, property_id),
                       last_error = %s, attempts = attempts + %s, updated_at = NOW(),
                       synced_at = CASE WHEN %s = 'synced' THEN NOW() ELSE synced_at END
                   WHERE id = %s""",
                (status, reason, property_id, error, 1 if attempt else 0, status, sub_id))


def _segna_fallito(sub_id, exc) -> None:
    """In una transazione propria: il trasferimento e' andato in rollback."""
    try:
        with core_cursor(commit=True) as (_, cur):
            _esito_invio(cur, sub_id, "failed", reason="error", error=type(exc).__name__[:120], attempt=True)
    except Exception:  # noqa: BLE001 - resta il log
        log.error("site_sync_mark_failed submission_id=%s", sub_id)


# ---------------------------------------------------------------------------
# 1. LA STIMA SALVATA (/api/salva_stima, dopo il bridge)
# ---------------------------------------------------------------------------

def record_public_stima(*, stima_id: int, raw: dict | None, bridge_result: dict | None) -> dict:
    """Conserva l'invio della stima rapida (transazione propria)."""
    ponte = bridge_result or {}
    with core_cursor(commit=True) as (_, cur):
        if not _installata(cur):
            return {"status": "not_installed"}
        cur.execute("SELECT id, agency_id FROM stime WHERE id = %s", (stima_id,))
        stima = repository.row(cur.fetchone())
        if stima is None:
            return {"status": "no_stima"}
        riga = _ricevi(cur, kind="quick", agency_id=stima["agency_id"], stima_id=stima_id, detail_id=None, raw=raw,
                       contact_id=ponte.get("contact_id"), lead_id=ponte.get("lead_id"))
        return {"status": "recorded", "submission_id": riga["id"]}


def _contatto_e_lead(cur, sub: dict) -> tuple:
    """Dal bridge (salvati con l'invio) o, nel recupero, da `lead_stime`
    (il bridge e' idempotente: se e' passato dopo, il collegamento c'e')."""
    if sub.get("contact_id") and sub.get("lead_id"):
        return sub["contact_id"], sub["lead_id"]
    cur.execute("SELECT l.id AS lead_id, l.contact_id FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id "
                "WHERE ls.stima_id = %s AND l.agency_id = %s ORDER BY ls.id LIMIT 1", (sub["stima_id"], sub["agency_id"]))
    r = cur.fetchone()
    return (r["contact_id"], r["lead_id"]) if r else (None, None)


def sync_quick(system_ctx, submission_id: int) -> dict:
    """Trasferisce l'invio nella scheda. Lo stato dell'invio cambia NELLA STESSA
    transazione della scheda: o entrambi, o nessuno."""
    with core_cursor(commit=True) as (_, cur):
        _census._assicura_083(cur)
        cur.execute("SELECT * FROM site_submissions WHERE id = %s AND kind = 'quick' FOR UPDATE", (submission_id,))
        sub = repository.row(cur.fetchone())
        if sub is None:
            return {"status": "skipped", "reason": "no_submission"}
        if sub["status"] == "synced":
            return {"status": "replica", "property_id": sub["property_id"]}
        stima_id, agency_id = sub["stima_id"], sub["agency_id"]
        if system_ctx is not None and getattr(system_ctx, "agency_id", agency_id) != agency_id:
            _esito_invio(cur, sub["id"], "skipped", reason="agency_mismatch")
            return {"status": "skipped", "reason": "agency_mismatch"}
        contact_id, lead_id = _contatto_e_lead(cur, sub)
        if contact_id is None or lead_id is None:
            # la decisione: niente scheda senza contatto e lead (calcolo anonimo o bridge non riuscito)
            _esito_invio(cur, sub["id"], "skipped", reason="no_contact_lead")
            return {"status": "skipped", "reason": "no_contact_lead"}
        chiave = sub.get("client_request_id")
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"site_sync:request:{agency_id}:{chiave}" if chiave else f"site_sync:contact:{agency_id}:{contact_id}",))
        cur.execute("SELECT * FROM property_site_sources WHERE stima_id = %s AND status = 'active'", (stima_id,))
        gia = repository.row(cur.fetchone())
        if gia is not None:
            _esito_invio(cur, sub["id"], "synced", property_id=gia["property_id"])
            return {"status": "replica", "property_id": gia["property_id"], "source_id": gia["id"]}
        cur.execute("SELECT comune FROM stime WHERE id = %s", (stima_id,))
        comune = (cur.fetchone() or {}).get("comune")
        esito = cat.map_site_payload(sub["declared"], comune=comune)
        impronta = cat.fingerprint(esito)

        # --- stesso invio ripetuto: SOLO con l'identita' della richiesta ----------------
        if chiave:
            cur.execute(f"""SELECT s.property_id, s.stima_id FROM site_submissions s JOIN properties p ON p.id = s.property_id
                             WHERE s.agency_id = %s AND s.kind = 'quick' AND s.client_request_id = %s
                               AND s.status = 'synced' AND s.id <> %s AND {_property_trash.live('p')}
                             ORDER BY s.id LIMIT 1""", (agency_id, str(chiave), sub["id"]))
            prima = repository.row(cur.fetchone())
            if prima is not None:
                pid = prima["property_id"]
                prop = _immobile(cur, agency_id, pid, lock=True)
                prop, scritti, conflitti = _applica(cur, prop, esito.fields, esito.accessories,
                                                    precedente=_istantanea(cur, pid), ignorati=_ignorati(cur, pid),
                                                    pertinenze_dichiarate=False, origine=f"stima:{stima_id}")
                _collega_lead(cur, pid, lead_id, "related")
                _collega_contatto(cur, pid, contact_id)
                cur.execute("""INSERT INTO property_site_sources (agency_id, property_id, stima_id, contact_id, lead_id, origin,
                                   fingerprint, site_values, declared, unmapped, conflicts)
                               VALUES (%s,%s,%s,%s,%s,'retry',%s,%s,%s,%s,%s) RETURNING id""",
                            (agency_id, pid, stima_id, contact_id, lead_id, impronta, Json(_j(scritti)),
                             Json(_dichiarato(esito)), Json(esito.unmapped), Json(conflitti)))
                sid = cur.fetchone()["id"]
                _chiudi_altrove(cur, pid, sid, set(scritti) | {c["field"] for c in conflitti})
                _attivita(cur, system_ctx, pid, f"Stima Stima360 n. {stima_id}: stessa richiesta della stima "
                          f"n. {prima['stima_id']} (ritentativo), collegata a questa scheda", stima_id=stima_id, origin="retry")
                _esito_invio(cur, sub["id"], "synced", reason="same_request", property_id=pid)
                return {"status": "retry", "property_id": pid, "source_id": sid}

        # --- scheda nuova (censimento) ---------------------------------------------
        dati = {k: v for k, v in esito.fields.items()}
        metadata = {"origin": cat.SOURCE, "stima_id": stima_id}
        if "property_type" not in dati:
            # NOT NULL nel database: `other` e' solo il valore TECNICO; la scheda
            # mostra «Da verificare» con il valore del sito (mai «Altro» scelto)
            dati["property_type"] = "other"
            metadata[UNVERIFIED_KEY] = {"property_type": esito.unverified.get("property_type")
                                        or {"raw": None, "reason": "Tipologia non dichiarata dal sito"}}
            if "property_type" not in esito.unverified:
                esito.ignoto("tipologia", None, "Tipologia non dichiarata dal sito")
        dati.update({"source": cat.SOURCE, "metadata": metadata})
        _census._prepara_unita(cur, agency_id, dati)
        chiave_scheda = client_request_id_for(stima_id)
        cur.execute("SELECT id FROM properties p WHERE agency_id = %s AND client_request_id = %s AND "
                    f"{_property_trash.live('p')}", (agency_id, str(chiave_scheda)))
        esistente = cur.fetchone()
        if esistente is not None:
            pid = esistente["id"]
            scritti = {}
        else:
            riga = _census._inserisci_unita(None, cur, agency_id, dati, chiave=chiave_scheda, impronta=impronta,
                                            conferma=True)
            pid = riga["id"]
            scritti = {k: _j(v) for k, v in esito.fields.items()}
            for kind, voce in esito.accessories.items():
                _inserisci_accessorio(cur, pid, kind, voce)
                scritti[_ACC + kind] = _j({"surface_sqm": voce.get("surface_sqm"), "quantity": voce.get("quantity")})
        _collega_lead(cur, pid, lead_id, "origin")
        _collega_contatto(cur, pid, contact_id)
        doppioni = _doppioni(cur, agency_id, pid, contact_id, esito.fields, impronta)
        cur.execute("""INSERT INTO property_site_sources (agency_id, property_id, stima_id, contact_id, lead_id, origin,
                           fingerprint, site_values, declared, unmapped, duplicates)
                       VALUES (%s,%s,%s,%s,%s,'auto',%s,%s,%s,%s,%s) RETURNING id""",
                    (agency_id, pid, stima_id, contact_id, lead_id, impronta, Json(scritti),
                     Json(_dichiarato(esito)), Json(esito.unmapped), Json(doppioni)))
        sid = cur.fetchone()["id"]
        _attivita(cur, system_ctx, pid, f"Scheda di censimento creata dalla stima Stima360 n. {stima_id}",
                  stima_id=stima_id, origin="auto", unmapped=len(esito.unmapped), duplicates=len(doppioni))
        _esito_invio(cur, sub["id"], "synced", property_id=pid)
        return {"status": "created", "property_id": pid, "source_id": sid, "duplicates": doppioni,
                "unmapped": esito.unmapped}


def sync_public_stima(system_ctx, *, stima_id: int, raw: dict | None, bridge_result: dict | None) -> dict:
    """Conserva l'invio, poi lo trasferisce. Un errore del trasferimento lascia
    l'invio `failed` (recuperabile), non lo perde."""
    ricevuto = record_public_stima(stima_id=stima_id, raw=raw, bridge_result=bridge_result)
    if ricevuto["status"] != "recorded":
        return {"status": "skipped", "reason": ricevuto["status"]}
    try:
        return sync_quick(system_ctx, ricevuto["submission_id"])
    except Exception as exc:
        _segna_fallito(ricevuto["submission_id"], exc)
        raise


def safe_sync_public_stima(system_ctx, *, stima_id: int, raw: dict | None, bridge_result: dict | None) -> dict | None:
    """Mai un'eccezione verso il sito: un errore qui e' un invio `failed`
    (recuperabile) e una riga di log, mai una stima persa."""
    try:
        esito = sync_public_stima(system_ctx, stima_id=stima_id, raw=raw, bridge_result=bridge_result)
        log.info("site_sync stima_id=%s status=%s reason=%s property_id=%s", stima_id, esito.get("status"),
                 esito.get("reason"), esito.get("property_id"))
        return esito
    except Exception as exc:  # noqa: BLE001 - fail-open dichiarato
        log.error("site_sync stima_id=%s status=error error_type=%s", stima_id, type(exc).__name__)
        return None


# ---------------------------------------------------------------------------
# 2. LA STIMA DETTAGLIATA (/api/salva_stima_dettagliata, dopo l'INSERT)
# ---------------------------------------------------------------------------

def record_detail(*, stima_id: int | None, detail_id: int | None, raw: dict | None) -> dict:
    if detail_id is None:
        return {"status": "no_detail"}
    with core_cursor(commit=True) as (_, cur):
        if not _installata(cur):
            return {"status": "not_installed"}
        cur.execute("SELECT id, agency_id, stima_id FROM stime_dettagliate WHERE id = %s", (detail_id,))
        dettaglio = repository.row(cur.fetchone())
        if dettaglio is None:
            return {"status": "no_detail"}
        stima_id = dettaglio["stima_id"] if dettaglio["stima_id"] is not None else stima_id
        prefill = _prefill_di(cur, stima_id) if stima_id is not None else None
        riga = _ricevi(cur, kind="detail", agency_id=dettaglio["agency_id"], stima_id=dettaglio["stima_id"],
                       detail_id=detail_id, raw=raw, prefill=prefill)
        return {"status": "recorded", "submission_id": riga["id"]}


def sync_detail_submission(submission_id: int) -> dict:
    with core_cursor(commit=True) as (_, cur):
        cur.execute("SELECT * FROM site_submissions WHERE id = %s AND kind = 'detail' FOR UPDATE", (submission_id,))
        sub = repository.row(cur.fetchone())
        if sub is None:
            return {"status": "skipped", "reason": "no_submission"}
        if sub["status"] == "synced":
            return {"status": "replica", "property_id": sub["property_id"]}
        stima_id, detail_id = sub["stima_id"], sub["detail_id"]
        if stima_id is None:
            _esito_invio(cur, sub["id"], "skipped", reason="orphan_detail")
            return {"status": "skipped", "reason": "orphan_detail"}
        cur.execute("SELECT * FROM property_site_sources WHERE stima_id = %s AND status = 'active' FOR UPDATE", (stima_id,))
        fonte = repository.row(cur.fetchone())
        if fonte is None:
            cur.execute("SELECT status FROM site_submissions WHERE kind = 'quick' AND stima_id = %s", (stima_id,))
            rapida = cur.fetchone()
            if rapida is not None and rapida["status"] in ("pending", "failed"):
                # la stima rapida non e' ancora nella scheda: si aspetta il suo recupero
                _esito_invio(cur, sub["id"], "pending", reason="waiting_quick")
                return {"status": "pending", "reason": "waiting_quick"}
            _esito_invio(cur, sub["id"], "skipped", reason="no_source")
            return {"status": "skipped", "reason": "no_source"}
        try:
            prop = _immobile(cur, fonte["agency_id"], fonte["property_id"], lock=True)
        except NotFoundError:
            _esito_invio(cur, sub["id"], "skipped", reason="property_in_trash")
            return {"status": "skipped", "reason": "property_in_trash"}
        from network_routing.service import system_context_for_persisted_public_stima
        ctx = system_context_for_persisted_public_stima(cur, stima_id=stima_id)
        esito = cat.map_site_payload(sub["declared"], detailed=True)
        precompilato = cat.map_site_payload(sub["prefill"], detailed=True) if sub.get("prefill") is not None else None
        invariati = cat.separate_prefilled(esito, precompilato, sub.get("declared_fields"))
        prop, scritti, conflitti = _applica(cur, prop, esito.fields, esito.accessories,
                                            precedente=_istantanea(cur, prop["id"]),
                                            ignorati=_ignorati(cur, prop["id"]),
                                            pertinenze_dichiarate=esito.pertinenze_declared,
                                            origine=f"detail:{detail_id}",
                                            ancora_presenti={t[len(_ACC):] for t in invariati if t.startswith(_ACC)})
        base = fonte["declared"] or {}
        dichiarato = {**base, **_dichiarato(esito),
                      "accessories": {**(base.get("accessories") or {}), **_dichiarato(esito)["accessories"]},
                      "prefilled_unchanged": sorted(set(base.get("prefilled_unchanged") or []) | set(invariati))}
        unmapped = (fonte["unmapped"] or []) + [{**u, "origin": f"detail:{detail_id}"} for u in esito.unmapped]
        cur.execute("""UPDATE property_site_sources SET site_values = site_values || %s, declared = %s, unmapped = %s,
                           conflicts = %s, detail_ids = detail_ids || %s, updated_at = NOW() WHERE id = %s""",
                    (Json(_j(scritti)), Json(dichiarato), Json(unmapped),
                     Json(_unisci_conflitti(fonte["conflicts"], conflitti, set(scritti))),
                     Json([detail_id] if detail_id is not None else []), fonte["id"]))
        _chiudi_altrove(cur, prop["id"], fonte["id"], set(scritti) | {c["field"] for c in conflitti})
        _attivita(cur, ctx, prop["id"],
                  f"Stima dettagliata Stima360 (stima n. {stima_id}): {len(scritti)} dati allineati, "
                  f"{len(conflitti)} da verificare, {len(invariati)} lasciati come precompilati",
                  stima_id=stima_id, detail_id=detail_id, conflicts=len(conflitti), prefilled_unchanged=len(invariati))
        _esito_invio(cur, sub["id"], "synced", property_id=prop["id"])
        return {"status": "updated", "property_id": prop["id"], "source_id": fonte["id"],
                "written": sorted(scritti), "conflicts": conflitti, "prefilled_unchanged": invariati}


def sync_detail(*, stima_id: int | None, detail_id: int | None, raw: dict | None) -> dict:
    ricevuto = record_detail(stima_id=stima_id, detail_id=detail_id, raw=raw)
    if ricevuto["status"] != "recorded":
        return {"status": "skipped", "reason": ricevuto["status"]}
    try:
        return sync_detail_submission(ricevuto["submission_id"])
    except Exception as exc:
        _segna_fallito(ricevuto["submission_id"], exc)
        raise


def safe_sync_detail(*, stima_id: int | None, detail_id: int | None, raw: dict | None) -> dict | None:
    try:
        esito = sync_detail(stima_id=stima_id, detail_id=detail_id, raw=raw)
        log.info("site_sync_detail stima_id=%s detail_id=%s status=%s reason=%s", stima_id, detail_id,
                 esito.get("status"), esito.get("reason"))
        return esito
    except Exception as exc:  # noqa: BLE001 - fail-open dichiarato
        log.error("site_sync_detail stima_id=%s status=error error_type=%s", stima_id, type(exc).__name__)
        return None


# ---------------------------------------------------------------------------
# 2bis. RECUPERO degli invii non trasferiti (osservabile, idempotente)
# ---------------------------------------------------------------------------
#
# Riprende SOLO le righe di `site_submissions` (pending, failed, le rapide
# saltate per mancanza di lead se nel frattempo il lead c'e', e le dettagliate
# saltate perche' la loro rapida non era nella scheda), con i valori
# conservati al momento dell'invio. NON e' un backfill dello storico: le stime
# precedenti alla 088 non hanno l'invio originale e non si ricostruiscono da
# `stime` (default e interi): restano fuori, contate come tali.

RECOVERABLE = ("pending", "failed")


def census() -> dict:
    """Quanti invii per tipo, stato e motivo, e quante stime non hanno un
    invio conservato (precedenti alla 088: non recuperabili con precisione)."""
    with core_cursor() as (_, cur):
        if not _installata(cur):
            return {"installed": False}
        cur.execute("SELECT kind, status, coalesce(reason, '') AS reason, count(*) AS n FROM site_submissions "
                    "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3")
        righe = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT count(*) AS n FROM stime s WHERE NOT EXISTS (SELECT 1 FROM site_submissions x "
                    "WHERE x.kind = 'quick' AND x.stima_id = s.id)")
        senza = cur.fetchone()["n"]
        return {"installed": True, "submissions": righe, "stime_without_submission": senza}


def _candidati(cur, *, limit, agency_id, min_age_seconds) -> list[dict]:
    filtro = " AND agency_id = %s" if agency_id is not None else ""
    cur.execute(f"""SELECT id, kind, stima_id, detail_id, status, reason, attempts FROM site_submissions
                     WHERE (status IN ('pending', 'failed')
                            OR (kind = 'quick' AND status = 'skipped' AND reason = 'no_contact_lead'
                                AND EXISTS (SELECT 1 FROM lead_stime ls WHERE ls.stima_id = site_submissions.stima_id))
                            OR (kind = 'detail' AND status = 'skipped' AND reason = 'no_source'
                                AND EXISTS (SELECT 1 FROM site_submissions q WHERE q.kind = 'quick'
                                               AND q.stima_id = site_submissions.stima_id
                                               AND (q.status IN ('pending', 'failed')
                                                    OR (q.status = 'synced' AND q.synced_at > site_submissions.updated_at)
                                                    OR (q.status = 'skipped' AND q.reason = 'no_contact_lead'
                                                        AND EXISTS (SELECT 1 FROM lead_stime ls
                                                                     WHERE ls.stima_id = q.stima_id))))))
                       AND created_at < NOW() - make_interval(secs => %s){filtro}
                     ORDER BY (kind = 'detail'), id LIMIT %s""",
                [min_age_seconds, *([agency_id] if agency_id is not None else []), limit])
    return [dict(r) for r in cur.fetchall()]


def recover(*, apply: bool = False, limit: int = 200, agency_id: int | None = None, min_age_seconds: int = 120) -> dict:
    """Prima le rapide, poi le dettagliate. Senza `apply` solo l'elenco.
    Ogni riga in una transazione sua; ripetere non duplica nulla."""
    with core_cursor() as (_, cur):
        if not _installata(cur):
            return {"installed": False, "items": []}
        candidati = _candidati(cur, limit=limit, agency_id=agency_id, min_age_seconds=min_age_seconds)
    voci = []
    for c in candidati:
        voce = {**c, "outcome": None}
        if apply:
            try:
                if c["kind"] == "quick":
                    with core_cursor(commit=True) as (_, cur):
                        cur.execute("UPDATE site_submissions SET status = 'pending', reason = NULL WHERE id = %s "
                                    "AND status = 'skipped'", (c["id"],))
                        from network_routing.service import system_context_for_persisted_public_stima
                        ctx = system_context_for_persisted_public_stima(cur, stima_id=c["stima_id"])
                    esito = sync_quick(ctx, c["id"])
                else:
                    with core_cursor(commit=True) as (_, cur):
                        cur.execute("UPDATE site_submissions SET status = 'pending', reason = NULL WHERE id = %s "
                                    "AND status = 'skipped'", (c["id"],))
                    esito = sync_detail_submission(c["id"])
                voce["outcome"] = esito.get("status")
                voce["reason"] = esito.get("reason")
                voce["property_id"] = esito.get("property_id")
            except Exception as exc:  # noqa: BLE001 - registrato sulla riga, si continua
                _segna_fallito(c["id"], exc)
                voce["outcome"] = "failed"
                voce["error"] = type(exc).__name__
        voci.append(voce)
    riepilogo: dict = {}
    for v in voci:
        riepilogo[v["outcome"] or "to_process"] = riepilogo.get(v["outcome"] or "to_process", 0) + 1
    return {"installed": True, "applied": apply, "items": voci, "summary": riepilogo}


# ---------------------------------------------------------------------------
# 3. LA SCHEDA: provenienza, conflitti, doppioni, collegamento esplicito
# ---------------------------------------------------------------------------

def _fonte(cur, agency_id, property_id, source_id, *, lock=False) -> dict:
    cur.execute(f"SELECT * FROM property_site_sources WHERE id = %s AND agency_id = %s AND property_id = %s"
                f"{' FOR UPDATE' if lock else ''}", (source_id, agency_id, property_id))
    riga = repository.row(cur.fetchone())
    if riga is None:
        raise NotFoundError(f"site source {source_id} not found")
    return riga


def _codici(cur, agency_id, ids) -> dict:
    ids = [i for i in ids if i is not None]
    if not ids:
        return {}
    cur.execute(f"SELECT p.id, p.code, p.title, p.record_kind FROM properties p WHERE p.agency_id = %s "
                f"AND p.id = ANY(%s) AND {_property_trash.live('p')}", (agency_id, list(ids)))
    return {r["id"]: dict(r) for r in cur.fetchall()}


def list_sources(ctx, property_id: int) -> dict:
    from . import service as _service
    prop = _service.get_property(ctx, property_id)          # visibilita' e Cestino come la scheda
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        if not _provenienza(cur):
            return {"installed": False, "items": [], "can_manage": False}
        cur.execute("SELECT s.*, st.data AS stima_at FROM property_site_sources s JOIN stime st ON st.id = s.stima_id "
                    "WHERE s.property_id = %s AND s.agency_id = %s ORDER BY s.id", (property_id, agency_id))
        righe = [dict(r) for r in cur.fetchall()]
        altri = _codici(cur, agency_id, [d["property_id"] for r in righe for d in (r["duplicates"] or [])]
                        + [r["relinked_to_property_id"] for r in righe])
        voci = []
        for r in righe:
            duplicati = [{**d, **({"code": altri[d["property_id"]]["code"], "title": altri[d["property_id"]]["title"]}
                                  if d["property_id"] in altri else {})}
                         for d in (r["duplicates"] or []) if d["property_id"] in altri]
            voci.append({
                "id": r["id"], "stima_id": r["stima_id"], "stima_at": r["stima_at"], "origin": r["origin"],
                "status": r["status"], "contact_id": r["contact_id"], "lead_id": r["lead_id"],
                "declared": r["declared"], "unmapped": r["unmapped"], "conflicts": r["conflicts"],
                "duplicates": duplicati, "detail_ids": r["detail_ids"],
                "relinked_to": altri.get(r["relinked_to_property_id"]) if r["relinked_to_property_id"] else None,
                "created_at": r["created_at"], "updated_at": r["updated_at"],
            })
    return {"installed": True, "items": voci, "can_manage": _lifecycle.may_manage(ctx, prop),
            "labels": cat.FIELD_LABELS}


def _gestibile(ctx, cur, property_id) -> dict:
    agency_id = ctx.require_agency()
    if not _provenienza(cur):
        raise SiteSyncError(NOT_INSTALLED, "SITE_SYNC_NOT_INSTALLED")
    prop = _immobile(cur, agency_id, property_id, lock=True)
    _lifecycle.require_manage(ctx, prop)
    return prop


def resolve_conflict(ctx, property_id: int, source_id: int, body) -> dict:
    """«Applica» scrive il valore del sito (e lo registra come ultimo valore
    del sito); «Ignora» lo lascia com'e' e non lo ripropone."""
    azione, conflict_id = body.action, body.conflict_id
    with core_cursor(commit=True) as (_, cur):
        prop = _gestibile(ctx, cur, property_id)
        fonte = _fonte(cur, prop["agency_id"], property_id, source_id, lock=True)
        lista = list(fonte["conflicts"] or [])
        indice = next((i for i, c in enumerate(lista) if c.get("id") == conflict_id), None)
        if indice is None:
            raise NotFoundError(f"conflict {conflict_id} not found")
        c = lista[indice]
        if c.get("status") != "open":
            raise SiteSyncError("Questa differenza e' gia' stata gestita", "CONFLICT_ALREADY_RESOLVED")
        campo, valore = c["field"], c["site_value"]
        istantanea = {}
        if azione == "apply":
            if campo.startswith(_ACC):
                kind = campo[len(_ACC):]
                cur.execute("SELECT * FROM property_accessories WHERE property_id = %s AND kind = %s ORDER BY id FOR UPDATE",
                            (property_id, kind))
                righe = [dict(r) for r in cur.fetchall()]
                if valore.get("present") is False:
                    for r in [r for r in righe if r.get("source") == "stima360"]:
                        cur.execute("DELETE FROM property_accessories WHERE id = %s", (r["id"],))
                    istantanea = {}
                else:
                    nuovo = {"surface_sqm": valore.get("surface_sqm"), "quantity": valore.get("quantity")}
                    autonoma = _pertinenze_autonome(cur, property_id).get(kind) if not righe else None
                    if autonoma is not None:
                        raise SiteSyncError(f"Questa pertinenza e' gia' un'unita' autonoma collegata ({autonoma}): "
                                            "non si crea un accessorio doppio", "PERTINENZA_IS_UNIT", code_unit=autonoma)
                    if righe:
                        aggiorna = {k: v for k, v in nuovo.items() if v is not None}
                        if aggiorna:
                            cur.execute(f"UPDATE property_accessories SET {', '.join(f'{k} = %s' for k in aggiorna)}, "
                                        "updated_at = NOW() WHERE id = %s", [*aggiorna.values(), righe[0]["id"]])
                    else:
                        _inserisci_accessorio(cur, property_id, kind, nuovo)
                    istantanea = {campo: nuovo}
            else:
                if campo not in cat.FIELD_LABELS:
                    raise ValidationError("Campo non gestito")
                _scrivi_campi(cur, prop, {campo: valore})
                istantanea = {campo: valore}
        lista[indice] = {**c, "status": "applied" if azione == "apply" else "ignored", "resolved_at": _now(),
                         "resolved_by_user_id": getattr(ctx, "user_id", None)}
        cur.execute("UPDATE property_site_sources SET conflicts = %s, site_values = site_values || %s, "
                    "updated_at = NOW() WHERE id = %s", (Json(lista), Json(_j(istantanea)), source_id))
        etichetta = cat.FIELD_LABELS.get(campo) or cat_label(campo)
        _attivita(cur, ctx, property_id, f"Dato dal sito «{etichetta}»: {'applicato' if azione == 'apply' else 'ignorato'}",
                  field=campo, action=azione, stima_id=fonte["stima_id"])
        return {"conflict": lista[indice]}


def cat_label(campo: str) -> str:
    if campo.startswith(_ACC):
        from .catalog import ACCESSORY_KIND_LABELS
        return f"Pertinenza: {ACCESSORY_KIND_LABELS.get(campo[len(_ACC):], campo)}"
    return campo


def dismiss_duplicate(ctx, property_id: int, source_id: int, other_id: int) -> dict:
    with core_cursor(commit=True) as (_, cur):
        prop = _gestibile(ctx, cur, property_id)
        fonte = _fonte(cur, prop["agency_id"], property_id, source_id, lock=True)
        lista = list(fonte["duplicates"] or [])
        if not any(d["property_id"] == other_id for d in lista):
            raise NotFoundError("duplicate not found")
        lista = [{**d, "dismissed": True} if d["property_id"] == other_id else d for d in lista]
        cur.execute("UPDATE property_site_sources SET duplicates = %s, updated_at = NOW() WHERE id = %s",
                    (Json(lista), source_id))
        return {"duplicates": lista}


def relink(ctx, property_id: int, source_id: int, body) -> dict:
    """«Collega questa stima a IMM-x»: la stima smette di descrivere questa
    scheda e descrive l'altra. Sull'altra si scrivono solo i campi vuoti; ogni
    differenza diventa un conflitto da decidere. La scheda di partenza NON si
    cancella: resta com'e', e decide l'operatore (Cestino, se non serve)."""
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        if not _provenienza(cur):
            raise SiteSyncError(NOT_INSTALLED, "SITE_SYNC_NOT_INSTALLED")
        destinazione_id = body.target_property_id
        if destinazione_id is None and body.target_code:
            cur.execute(f"SELECT p.id FROM properties p WHERE p.agency_id = %s AND upper(btrim(p.code)) = upper(btrim(%s)) "
                        f"AND {_property_trash.live('p')}", (agency_id, body.target_code))
            trovata = cur.fetchone()
            if trovata is None:
                raise NotFoundError(f"Nessun immobile con codice {body.target_code}")
            destinazione_id = trovata["id"]
        if destinazione_id is None:
            raise ValidationError("Indica l'immobile da collegare")
        if destinazione_id == property_id:
            raise ValidationError("La stima descrive gia' questo immobile")
        primo, secondo = sorted((property_id, destinazione_id))
        bloccati = {primo: _immobile(cur, agency_id, primo, lock=True), secondo: _immobile(cur, agency_id, secondo, lock=True)}
        origine, destinazione = bloccati[property_id], bloccati[destinazione_id]
        _lifecycle.require_manage(ctx, origine)
        _lifecycle.require_manage(ctx, destinazione)
        fonte = _fonte(cur, agency_id, property_id, source_id, lock=True)
        if fonte["status"] != "active":
            raise SiteSyncError("Questa stima e' gia' collegata a un altro immobile", "SOURCE_ALREADY_RELINKED")
        relazione = None
        if fonte["lead_id"] is not None:
            cur.execute("SELECT relation_type FROM property_leads WHERE property_id = %s AND lead_id = %s",
                        (property_id, fonte["lead_id"]))
            r = cur.fetchone()
            relazione = r["relation_type"] if r else None
            if relazione == "seller":
                raise SiteSyncError(SELLER_LINK_ACTIVE, "SELLER_LINK_ACTIVE")
        cur.execute("UPDATE property_site_sources SET status = 'relinked', relinked_to_property_id = %s, "
                    "linked_by_user_id = %s, updated_at = NOW() WHERE id = %s",
                    (destinazione_id, getattr(ctx, "user_id", None), source_id))
        dichiarato = fonte["declared"] or {}
        campi = {k: v.get("value") for k, v in dichiarato.items()
                 if isinstance(v, dict) and "value" in v and k in cat.FIELD_LABELS and v.get("value") is not None}
        accessori = {k: {"surface_sqm": v.get("surface_sqm"), "quantity": v.get("quantity")}
                     for k, v in (dichiarato.get("accessories") or {}).items()}
        destinazione, scritti, conflitti = _applica(
            cur, destinazione, campi, accessori, precedente=_istantanea(cur, destinazione_id),
            ignorati=_ignorati(cur, destinazione_id), pertinenze_dichiarate=False, origine=f"link:{source_id}")
        cur.execute("""INSERT INTO property_site_sources (agency_id, property_id, stima_id, contact_id, lead_id, origin,
                           fingerprint, site_values, declared, unmapped, conflicts, detail_ids, linked_by_user_id)
                       VALUES (%s,%s,%s,%s,%s,'manual_link',%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (agency_id, destinazione_id, fonte["stima_id"], fonte["contact_id"], fonte["lead_id"],
                     fonte["fingerprint"], Json(_j(scritti)), Json(dichiarato), Json(fonte["unmapped"] or []),
                     Json(conflitti), Json(fonte["detail_ids"] or []), getattr(ctx, "user_id", None)))
        nuova = cur.fetchone()["id"]
        # il lead della stima segue la stima (mai un'opportunita' Venditore: vedi sopra)
        if relazione is not None:
            cur.execute("DELETE FROM property_leads WHERE property_id = %s AND lead_id = %s", (property_id, fonte["lead_id"]))
            _collega_lead(cur, destinazione_id, fonte["lead_id"], relazione)
        # il contatto: tolto dalla scheda di partenza solo se ce lo aveva messo il sito
        # (ruolo neutro) e nessun'altra stima attiva di quella scheda lo porta
        if fonte["contact_id"] is not None:
            cur.execute("SELECT 1 FROM property_site_sources WHERE property_id = %s AND status = 'active' "
                        "AND contact_id = %s LIMIT 1", (property_id, fonte["contact_id"]))
            if cur.fetchone() is None:
                cur.execute("DELETE FROM property_contacts WHERE property_id = %s AND contact_id = %s AND role = 'contact'",
                            (property_id, fonte["contact_id"]))
            _collega_contatto(cur, destinazione_id, fonte["contact_id"])
        _attivita(cur, ctx, property_id, f"Stima Stima360 n. {fonte['stima_id']} collegata a "
                  f"{destinazione.get('code') or '#' + str(destinazione_id)}", stima_id=fonte["stima_id"],
                  relinked_to=destinazione_id)
        _attivita(cur, ctx, destinazione_id, f"Stima Stima360 n. {fonte['stima_id']} collegata da "
                  f"{origine.get('code') or '#' + str(property_id)}: {len(scritti)} dati completati, "
                  f"{len(conflitti)} da verificare", stima_id=fonte["stima_id"], relinked_from=property_id)
        return {"source_id": nuova, "property_id": destinazione_id, "written": sorted(scritti), "conflicts": conflitti}
