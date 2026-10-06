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
* Ritentativo dello stesso invio (il sito non ha una chiave: un doppio invio
  e' una SECONDA stima con un SECONDO lead): stessa agenzia, stesso contatto,
  stessa impronta dei valori dichiarati, entro 24 ore -> la stima si collega
  alla scheda gia' nata (`origin = 'retry'`), nessuna scheda nuova. Un lock
  transazionale sul contatto serializza i due invii.
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
  del sito; senza la migration 087 non scrive nulla.
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
RETRY_WINDOW_HOURS = 24
_MANCA = object()
_ACC = "accessory:"

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


def _installata(cur) -> bool:
    cur.execute("SELECT to_regclass('public.property_site_sources') IS NOT NULL AS pronta")
    return bool(cur.fetchone()["pronta"])


def _j(valore):
    if isinstance(valore, Decimal):
        return str(valore)
    if isinstance(valore, dict):
        return {k: _j(v) for k, v in valore.items()}
    return valore


def _canon(valore):
    """Confronto robusto fra il valore del database, quello del sito e quello
    salvato in JSON (Decimal / stringa / intero)."""
    if valore is None:
        return None
    if isinstance(valore, bool):
        return ("b", valore)
    if isinstance(valore, (int, float, Decimal)):
        return ("n", Decimal(str(valore)).normalize())
    testo = str(valore).strip()
    if testo == "":
        return None
    try:
        numero = Decimal(testo)
        if numero.is_finite():
            return ("n", numero.normalize())
    except InvalidOperation:
        pass
    return ("s", testo)


def _uguale(a, b) -> bool:
    return _canon(a) == _canon(b)


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

def _scrivi_campi(cur, prop: dict, valori: dict) -> dict:
    if not valori:
        return prop
    valori = dict(valori)
    if any(f in valori for f in TITLE_SOURCE_FIELDS) and prop.get("title") == generated_title(prop):
        valori["title"] = generated_title({**prop, **valori})
    cur.execute(f"UPDATE properties SET {', '.join(f'{k} = %s' for k in valori)}, updated_at = NOW() "
                "WHERE id = %s RETURNING *", [*valori.values(), prop["id"]])
    return repository.row(cur.fetchone())


def _inserisci_accessorio(cur, property_id, kind, voce) -> dict:
    cur.execute("INSERT INTO property_accessories (property_id, kind, cadastral_status, surface_sqm, quantity, source) "
                "VALUES (%s, %s, %s, %s, %s, 'stima360') RETURNING *",
                (property_id, kind, cat.ACCESSORY_KINDS[kind]["status"], voce.get("surface_sqm"), voce.get("quantity")))
    return repository.row(cur.fetchone())


def _applica(cur, prop: dict, campi: dict, accessori: dict, *, precedente: dict, ignorati: set,
             pertinenze_dichiarate: bool, origine: str) -> tuple[dict, dict, list]:
    """(scheda aggiornata, istantanea scritta, conflitti nuovi)."""
    scritti: dict = {}
    da_scrivere: dict = {}
    conflitti: list = []

    def conflitto(campo, valore_sito, attuale):
        if (campo, repr(_canon_voce(valore_sito))) in ignorati:
            return
        conflitti.append({"id": uuid.uuid4().hex[:12], "field": campo, "site_value": _j(valore_sito),
                          "current_value": _j(attuale), "status": "open", "origin": origine, "detected_at": _now()})

    for campo, nuovo in campi.items():
        if nuovo is None:
            continue
        attuale = prop.get(campo)
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
    for kind, voce in accessori.items():
        chiave = _ACC + kind
        nuovo = {"surface_sqm": voce.get("surface_sqm"), "quantity": voce.get("quantity")}
        prima = precedente.get(chiave, _MANCA)
        stesse = [a for a in esistenti if a["kind"] == kind]
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
            if kind in accessori:
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

def _doppioni(cur, agency_id, property_id, contact_id, campi: dict) -> list[dict]:
    citta, via, civico = campi.get("city"), campi.get("address"), campi.get("civic_number")
    per_indirizzo = bool(citta and via and civico)
    cur.execute(f"""
        SELECT p.id, p.code, p.title, p.record_kind,
               EXISTS (SELECT 1 FROM property_contacts pc WHERE pc.property_id = p.id AND pc.contact_id = %s) AS stesso_contatto,
               (%s AND lower(btrim(p.city)) = lower(btrim(%s))
                   AND lower(regexp_replace(btrim(p.address), '\\s+', ' ', 'g')) = lower(regexp_replace(btrim(%s), '\\s+', ' ', 'g'))
                   AND upper(btrim(p.civic_number)) = upper(btrim(%s))) AS stesso_indirizzo
          FROM properties p
         WHERE p.agency_id = %s AND p.id <> %s AND p.archived_at IS NULL AND {_property_trash.live('p')}
         ORDER BY p.id""",
                (contact_id, per_indirizzo, citta or "", via or "", civico or "", agency_id, property_id))
    trovati = []
    for r in cur.fetchall():
        motivi = [m for m, ok in (("same_contact", r["stesso_contatto"]), ("same_address", r["stesso_indirizzo"])) if ok]
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
# 1. LA STIMA SALVATA (/api/salva_stima, dopo il bridge)
# ---------------------------------------------------------------------------

def _dichiarato(esito: cat.Esito) -> dict:
    return {**esito.declared,
            "accessories": {k: _j({kk: vv for kk, vv in v.items()}) for k, v in esito.accessories.items()},
            "pertinenze_declared": esito.pertinenze_declared}


def sync_public_stima(system_ctx, *, stima_id: int, raw: dict | None, bridge_result: dict | None) -> dict:
    ponte = bridge_result or {}
    contact_id, lead_id = ponte.get("contact_id"), ponte.get("lead_id")
    if ponte.get("status") not in ("linked", "already_linked") or contact_id is None or lead_id is None:
        return {"status": "skipped", "reason": "no_contact_lead"}
    with core_cursor(commit=True) as (_, cur):
        if not _installata(cur):
            return {"status": "skipped", "reason": "not_installed"}
        _census._assicura_083(cur)
        cur.execute("SELECT id, agency_id, comune FROM stime WHERE id = %s", (stima_id,))
        stima = repository.row(cur.fetchone())
        if stima is None:
            return {"status": "skipped", "reason": "no_stima"}
        agency_id = stima["agency_id"]
        if getattr(system_ctx, "agency_id", agency_id) != agency_id:
            return {"status": "skipped", "reason": "agency_mismatch"}
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"site_sync:contact:{agency_id}:{contact_id}",))
        cur.execute("SELECT * FROM property_site_sources WHERE stima_id = %s AND status = 'active'", (stima_id,))
        gia = repository.row(cur.fetchone())
        if gia is not None:
            return {"status": "replica", "property_id": gia["property_id"], "source_id": gia["id"]}

        esito = cat.map_site_payload(raw, comune=stima.get("comune"))
        impronta = cat.fingerprint(esito)

        # --- ritentativo dello stesso invio -------------------------------------------
        cur.execute(f"""
            SELECT s.* FROM property_site_sources s JOIN properties p ON p.id = s.property_id
             WHERE s.agency_id = %s AND s.contact_id = %s AND s.fingerprint = %s AND s.status = 'active'
               AND s.created_at > NOW() - make_interval(hours => %s) AND {_property_trash.live('p')}
             ORDER BY s.id DESC LIMIT 1""", (agency_id, contact_id, impronta, RETRY_WINDOW_HOURS))
        precedente = repository.row(cur.fetchone())
        if precedente is not None:
            pid = precedente["property_id"]
            _collega_lead(cur, pid, lead_id, "related")
            _collega_contatto(cur, pid, contact_id)
            cur.execute("""INSERT INTO property_site_sources (agency_id, property_id, stima_id, contact_id, lead_id, origin,
                               fingerprint, declared, unmapped) VALUES (%s,%s,%s,%s,%s,'retry',%s,%s,%s) RETURNING id""",
                        (agency_id, pid, stima_id, contact_id, lead_id, impronta, Json(_dichiarato(esito)),
                         Json(esito.unmapped)))
            sid = cur.fetchone()["id"]
            _attivita(cur, system_ctx, pid, f"Stima Stima360 n. {stima_id}: stesso invio della stima "
                      f"n. {precedente['stima_id']}, collegata a questa scheda", stima_id=stima_id, origin="retry")
            return {"status": "retry", "property_id": pid, "source_id": sid}

        # --- scheda nuova (censimento) ---------------------------------------------
        dati = {k: v for k, v in esito.fields.items()}
        if "property_type" not in dati:
            dati["property_type"] = "other"
            if not any(u["site_field"] == "tipologia" for u in esito.unmapped):
                esito.ignoto("tipologia", None, "Tipologia non dichiarata dal sito")
        dati.update({"source": cat.SOURCE, "metadata": {"origin": cat.SOURCE, "stima_id": stima_id}})
        _census._prepara_unita(cur, agency_id, dati)
        chiave = client_request_id_for(stima_id)
        cur.execute("SELECT id FROM properties p WHERE agency_id = %s AND client_request_id = %s AND "
                    f"{_property_trash.live('p')}", (agency_id, str(chiave)))
        esistente = cur.fetchone()
        if esistente is not None:
            pid = esistente["id"]
            scritti = {}
        else:
            riga = _census._inserisci_unita(None, cur, agency_id, dati, chiave=chiave, impronta=impronta, conferma=True)
            pid = riga["id"]
            scritti = {k: _j(v) for k, v in esito.fields.items()}
            for kind, voce in esito.accessories.items():
                _inserisci_accessorio(cur, pid, kind, voce)
                scritti[_ACC + kind] = _j({"surface_sqm": voce.get("surface_sqm"), "quantity": voce.get("quantity")})
        _collega_lead(cur, pid, lead_id, "origin")
        _collega_contatto(cur, pid, contact_id)
        doppioni = _doppioni(cur, agency_id, pid, contact_id, esito.fields)
        cur.execute("""INSERT INTO property_site_sources (agency_id, property_id, stima_id, contact_id, lead_id, origin,
                           fingerprint, site_values, declared, unmapped, duplicates)
                       VALUES (%s,%s,%s,%s,%s,'auto',%s,%s,%s,%s,%s) RETURNING id""",
                    (agency_id, pid, stima_id, contact_id, lead_id, impronta, Json(scritti),
                     Json(_dichiarato(esito)), Json(esito.unmapped), Json(doppioni)))
        sid = cur.fetchone()["id"]
        _attivita(cur, system_ctx, pid, f"Scheda di censimento creata dalla stima Stima360 n. {stima_id}",
                  stima_id=stima_id, origin="auto", unmapped=len(esito.unmapped), duplicates=len(doppioni))
        return {"status": "created", "property_id": pid, "source_id": sid, "duplicates": doppioni,
                "unmapped": esito.unmapped}


def safe_sync_public_stima(system_ctx, *, stima_id: int, raw: dict | None, bridge_result: dict | None) -> dict | None:
    """Mai un'eccezione verso il sito: un errore qui e' un log, non una stima persa."""
    try:
        esito = sync_public_stima(system_ctx, stima_id=stima_id, raw=raw, bridge_result=bridge_result)
        log.info("site_sync stima_id=%s status=%s property_id=%s", stima_id, esito.get("status"),
                 esito.get("property_id"))
        return esito
    except Exception as exc:  # noqa: BLE001 - fail-open dichiarato
        log.error("site_sync stima_id=%s status=error error_type=%s", stima_id, type(exc).__name__)
        return None


# ---------------------------------------------------------------------------
# 2. LA STIMA DETTAGLIATA (/api/salva_stima_dettagliata, dopo l'INSERT)
# ---------------------------------------------------------------------------

def sync_detail(*, stima_id: int | None, detail_id: int | None, raw: dict | None) -> dict:
    if stima_id is None:
        return {"status": "skipped", "reason": "orphan_detail"}
    with core_cursor(commit=True) as (_, cur):
        if not _installata(cur):
            return {"status": "skipped", "reason": "not_installed"}
        cur.execute("SELECT * FROM property_site_sources WHERE stima_id = %s AND status = 'active' FOR UPDATE",
                    (stima_id,))
        fonte = repository.row(cur.fetchone())
        if fonte is None:
            return {"status": "skipped", "reason": "no_source"}
        if detail_id is not None and detail_id in (fonte["detail_ids"] or []):
            return {"status": "replica", "property_id": fonte["property_id"], "source_id": fonte["id"]}
        try:
            prop = _immobile(cur, fonte["agency_id"], fonte["property_id"], lock=True)
        except NotFoundError:
            return {"status": "skipped", "reason": "property_in_trash"}
        from network_routing.service import system_context_for_persisted_public_stima
        ctx = system_context_for_persisted_public_stima(cur, stima_id=stima_id)
        esito = cat.map_site_payload(raw, detailed=True)
        prop, scritti, conflitti = _applica(cur, prop, esito.fields, esito.accessories,
                                            precedente=_istantanea(cur, prop["id"]),
                                            ignorati=_ignorati(cur, prop["id"]),
                                            pertinenze_dichiarate=esito.pertinenze_declared,
                                            origine=f"detail:{detail_id}")
        dichiarato = {**(fonte["declared"] or {}), **_dichiarato(esito),
                      "accessories": {**((fonte["declared"] or {}).get("accessories") or {}),
                                      **_dichiarato(esito)["accessories"]}}
        unmapped = (fonte["unmapped"] or []) + [{**u, "origin": f"detail:{detail_id}"} for u in esito.unmapped]
        cur.execute("""UPDATE property_site_sources SET site_values = site_values || %s, declared = %s, unmapped = %s,
                           conflicts = %s, detail_ids = detail_ids || %s, updated_at = NOW() WHERE id = %s""",
                    (Json(_j(scritti)), Json(dichiarato), Json(unmapped),
                     Json(_unisci_conflitti(fonte["conflicts"], conflitti, set(scritti))),
                     Json([detail_id] if detail_id is not None else []), fonte["id"]))
        _chiudi_altrove(cur, prop["id"], fonte["id"], set(scritti) | {c["field"] for c in conflitti})
        _attivita(cur, ctx, prop["id"],
                  f"Stima dettagliata Stima360 (stima n. {stima_id}): {len(scritti)} dati allineati, "
                  f"{len(conflitti)} da verificare", stima_id=stima_id, detail_id=detail_id,
                  conflicts=len(conflitti))
        return {"status": "updated", "property_id": prop["id"], "source_id": fonte["id"],
                "written": sorted(scritti), "conflicts": conflitti}


def safe_sync_detail(*, stima_id: int | None, detail_id: int | None, raw: dict | None) -> dict | None:
    try:
        esito = sync_detail(stima_id=stima_id, detail_id=detail_id, raw=raw)
        log.info("site_sync_detail stima_id=%s detail_id=%s status=%s", stima_id, detail_id, esito.get("status"))
        return esito
    except Exception as exc:  # noqa: BLE001 - fail-open dichiarato
        log.error("site_sync_detail stima_id=%s status=error error_type=%s", stima_id, type(exc).__name__)
        return None


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
        if not _installata(cur):
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
    if not _installata(cur):
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
        if not _installata(cur):
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
