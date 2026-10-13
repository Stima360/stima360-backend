"""STIMA360 TERRITORIO T5-bis - ricerca indirizzo (geocodifica) per la mappa.

Una sola operazione: `search(ctx, q)` -> fino a 5 risultati da Nominatim
(OpenStreetMap), SOLO su richiesta esplicita dell'operatore (il pulsante
«Cerca» della pagina di posizionamento). I risultati servono a centrare la
mappa e a mettere il punto PROVVISORIO: niente viene salvato qui, la posizione
si verifica solo con la conferma T4 (property/building_position.py).

Policy Nominatim (operations.osmfoundation.org/policies/nominatim):

  * al massimo 1 richiesta al secondo PER L'INTERA APPLICAZIONE. Qui lo
    garantisce il database: `pg_advisory_xact_lock` su una chiave fissa
    serializza le chiamate di TUTTI i processi e di TUTTE le istanze che usano
    lo stesso database del CRM (stesso meccanismo gia' usato in
    appointments/, core/ e crm/). Chi tiene il lock chiama il provider e poi
    aspetta che sia passato almeno MIN_INTERVAL dall'acquisizione prima di
    rilasciarlo; chi aspetta oltre LOCK_TIMEOUT riceve 503 GEOCODE_BUSY
    invece di accodarsi all'infinito;
  * User-Agent applicativo con un recapito;
  * nessun autocompletamento (la ricerca parte solo dal pulsante);
  * cache delle richieste identiche (per processo, con scadenza), cosi' una
    ricerca ripetuta non torna al provider;
  * attribuzione «© OpenStreetMap contributors» mostrata dall'interfaccia.

Al provider va SOLO il testo scritto dall'operatore: nessun dato del CRM.
Configurazione (variabili d'ambiente, nessun codice da cambiare):
  STIMA360_GEOCODE_ENABLED      '1' (default) | '0' = ricerca spenta (503 GEOCODE_DISABLED)
  STIMA360_GEOCODE_URL          endpoint di ricerca (default Nominatim pubblico)
  STIMA360_GEOCODE_CONTACT      recapito nello User-Agent (default info@stima360.it)
  STIMA360_GEOCODE_TIMEOUT      secondi per la risposta del provider (default 6)
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import OrderedDict

import requests

from core.database import core_cursor
from core.exceptions import ValidationError

from . import census

DEFAULT_URL = "https://nominatim.openstreetmap.org/search"
DEFAULT_CONTACT = "info@stima360.it"
APP_NAME = "STIMA360-CRM/1.0"
LOCK_KEY = "stima360.territorio.geocode"
MIN_INTERVAL = 1.0          # secondi fra due chiamate al provider (policy Nominatim)
LOCK_TIMEOUT_MS = 10000     # oltre, 503 GEOCODE_BUSY
MAX_RESULTS = 5
MAX_QUERY = 200
CACHE_TTL = 24 * 3600       # secondi
CACHE_SIZE = 500


class GeocodeDisabled(census.CensusConflict):
    def __init__(self):
        super().__init__("La ricerca indirizzo non e' attiva su questo ambiente", "GEOCODE_DISABLED")


class GeocodeBusy(census.CensusConflict):
    def __init__(self):
        super().__init__("Troppe ricerche in corso: riprova fra qualche secondo", "GEOCODE_BUSY")


class GeocodeUnavailable(census.CensusConflict):
    def __init__(self):
        super().__init__("Il servizio di ricerca indirizzi non risponde: riprova piu' tardi", "GEOCODE_UNAVAILABLE")


def enabled(env=None) -> bool:
    return (env if env is not None else os.environ).get("STIMA360_GEOCODE_ENABLED", "1").strip() != "0"


def provider_url(env=None) -> str:
    return (env if env is not None else os.environ).get("STIMA360_GEOCODE_URL", "").strip() or DEFAULT_URL


def user_agent(env=None) -> str:
    contatto = (env if env is not None else os.environ).get("STIMA360_GEOCODE_CONTACT", "").strip() or DEFAULT_CONTACT
    return f"{APP_NAME} (ricerca indirizzi per la mappa; {contatto})"


def timeout_s(env=None) -> float:
    try:
        return max(1.0, min(30.0, float((env if env is not None else os.environ).get("STIMA360_GEOCODE_TIMEOUT", "6"))))
    except ValueError:
        return 6.0


def normalize_query(q) -> str:
    """Il testo dell'operatore, ripulito: spazi compressi, lunghezza limitata."""
    testo = re.sub(r"\s+", " ", str(q or "")).strip()
    if len(testo) < 3:
        raise ValidationError("Scrivi almeno via e comune")
    return testo[:MAX_QUERY]


_NUMERO = re.compile(r"\b\d+[a-zA-Z]?\b")


def wanted_house_number(q: str) -> str | None:
    """Il civico chiesto dall'operatore (il primo numero), se c'e'."""
    m = _NUMERO.search(q)
    return m.group(0).lower() if m else None


def shape_results(raw, q: str) -> list[dict]:
    """I SOLI campi che servono alla mappa, con il livello di precisione:
    `house` = civico trovato, `street` = solo la via (il punto NON e' l'edificio),
    `area` = comune/zona."""
    civico = wanted_house_number(q)
    esito = []
    for r in raw if isinstance(raw, list) else []:
        try:
            lat, lng = float(r.get("lat")), float(r.get("lon"))
        except (TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue
        indirizzo = r.get("address") if isinstance(r.get("address"), dict) else {}
        numero = str(indirizzo.get("house_number") or "").strip().lower()
        via = indirizzo.get("road") or indirizzo.get("pedestrian") or indirizzo.get("square")
        if numero:
            precisione = "house"
        elif via:
            precisione = "street"
        else:
            precisione = "area"
        esito.append({
            "label": str(r.get("display_name") or "")[:300],
            "latitude": f"{lat:.7f}",
            "longitude": f"{lng:.7f}",
            "precision": precisione,
            "house_number": numero or None,
            "house_number_matches": bool(civico) and numero == civico,
            "road": via,
            "town": indirizzo.get("city") or indirizzo.get("town") or indirizzo.get("village") or indirizzo.get("municipality"),
            "postcode": indirizzo.get("postcode"),
        })
        if len(esito) >= MAX_RESULTS:
            break
    return esito


# --- cache per processo ---------------------------------------------------------

_cache_lock = threading.Lock()
_cache: "OrderedDict[str, tuple[float, list[dict]]]" = OrderedDict()


def _cache_get(chiave: str):
    with _cache_lock:
        voce = _cache.get(chiave)
        if voce is None:
            return None
        scadenza, valore = voce
        if scadenza < time.monotonic():
            del _cache[chiave]
            return None
        _cache.move_to_end(chiave)
        return valore


def _cache_put(chiave: str, valore: list[dict]) -> None:
    with _cache_lock:
        _cache[chiave] = (time.monotonic() + CACHE_TTL, valore)
        _cache.move_to_end(chiave)
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# --- la chiamata al provider ------------------------------------------------------

def call_provider(q: str, *, http=None, env=None) -> list:
    """UNA richiesta a Nominatim: formato json, solo Italia, al massimo 5
    risultati, indirizzo scomposto. Mai cookie, mai dati del CRM."""
    risposta = (http or requests).get(provider_url(env), params={
        "q": q, "format": "jsonv2", "countrycodes": "it", "limit": MAX_RESULTS,
        "addressdetails": 1, "accept-language": "it",
    }, headers={"User-Agent": user_agent(env), "Accept": "application/json"}, timeout=timeout_s(env))
    if risposta.status_code != 200:
        raise GeocodeUnavailable()
    try:
        return risposta.json()
    except ValueError as exc:
        raise GeocodeUnavailable() from exc


def search(ctx, q, *, http=None, env=None, sleep=time.sleep) -> dict:
    ctx.require_agency()                       # solo dentro un'agenzia, come la mappa
    if not enabled(env):
        raise GeocodeDisabled()
    testo = normalize_query(q)
    chiave = testo.casefold()
    in_cache = _cache_get(chiave)
    if in_cache is not None:
        return {"query": testo, "results": in_cache, "cached": True, "attribution": "© OpenStreetMap contributors"}

    # IL LIMITE GLOBALE: una transazione, un lock condiviso da tutta l'applicazione.
    with core_cursor() as (_, cur):
        cur.execute("SET LOCAL lock_timeout = %s", (f"{LOCK_TIMEOUT_MS}ms",))
        try:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (LOCK_KEY,))
        except Exception as exc:               # lock_timeout: psycopg2 QueryCanceled / LockNotAvailable
            raise GeocodeBusy() from exc
        inizio = time.monotonic()
        grezzi, errore = None, None
        try:
            grezzi = call_provider(testo, http=http, env=env)
        except GeocodeUnavailable as exc:
            errore = exc
        except Exception as exc:               # timeout, rete, DNS
            errore = exc
        # chi tiene il lock aspetta il resto del secondo PRIMA di rilasciarlo: cosi'
        # due chiamate consecutive, da qualunque processo, distano almeno MIN_INTERVAL
        resto = MIN_INTERVAL - (time.monotonic() - inizio)
        if resto > 0:
            sleep(resto)
    if grezzi is None:
        raise GeocodeUnavailable() from errore
    risultati = shape_results(grezzi, testo)
    _cache_put(chiave, risultati)
    return {"query": testo, "results": risultati, "cached": False, "attribution": "© OpenStreetMap contributors"}
