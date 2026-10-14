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
  STIMA360_GEOCODE_REVERSE_URL  endpoint della ricerca INVERSA (default: lo stesso host di
                                STIMA360_GEOCODE_URL con `/reverse` al posto di `/search`)

STIMA360 ZONA-5 - la ricerca INVERSA (`reverse(ctx, lat, lon)`): dal punto
scelto sulla mappa (doppio clic, pressione prolungata, «Inserisci qui» del GPS)
a via, civico, Comune, CAP e provincia, SOLO dopo quel gesto esplicito (mai
durante trascinamento, zoom o spostamento). STESSO lock globale, stessa cache,
stesso User-Agent e stessa attribuzione della ricerca diretta: ricerca e
ricerca inversa si contendono UN solo turno al secondo per tutta l'applicazione.
Al provider vanno SOLO le due coordinate del punto scelto (nessun dato del
CRM); nulla viene registrato nei log applicativi e nulla nel database: il
punto resta nell'interfaccia finche' l'operatore non salva l'entita'. Un
civico assente resta vuoto: nulla viene inventato.
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


def reverse_url(env=None) -> str:
    """ZONA-5: l'endpoint inverso; se non e' configurato, `/reverse` accanto a `/search`."""
    esplicito = (env if env is not None else os.environ).get("STIMA360_GEOCODE_REVERSE_URL", "").strip()
    if esplicito:
        return esplicito
    base = provider_url(env)
    return re.sub(r"/search/?$", "/reverse", base) if re.search(r"/search/?$", base) else base.rstrip("/") + "/reverse"


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


# --- ZONA-5: la forma del risultato inverso ----------------------------------------

def shape_reverse(raw, lat, lng) -> dict:
    """Dal risultato di Nominatim i SOLI campi che servono al pannello «Indirizzo
    individuato»: via, civico (vuoto se assente: mai inventato), Comune, CAP,
    provincia, indirizzo leggibile. `found` False se il provider non ha un
    indirizzo per quel punto (risposta vuota o con `error`)."""
    vuoto = {"found": False, "road": None, "house_number": None, "town": None, "postcode": None, "province": None,
             "label": None, "precision": None, "latitude": f"{lat:.7f}", "longitude": f"{lng:.7f}"}
    if not isinstance(raw, dict) or raw.get("error") or not raw.get("address"):
        return vuoto
    indirizzo = raw.get("address") if isinstance(raw.get("address"), dict) else {}
    numero = str(indirizzo.get("house_number") or "").strip() or None
    via = indirizzo.get("road") or indirizzo.get("pedestrian") or indirizzo.get("square") or indirizzo.get("footway")
    comune = indirizzo.get("city") or indirizzo.get("town") or indirizzo.get("village") or indirizzo.get("municipality") or indirizzo.get("hamlet")
    provincia = indirizzo.get("county") or indirizzo.get("province") or indirizzo.get("state_district")
    return {
        "found": True,
        "road": via,
        "house_number": numero,
        "town": comune,
        "postcode": indirizzo.get("postcode"),
        "province": provincia,
        "label": str(raw.get("display_name") or "")[:300] or None,
        "precision": "house" if numero else ("street" if via else "area"),
        "latitude": f"{lat:.7f}",
        "longitude": f"{lng:.7f}",
    }


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


def _con_il_turno(chiamata, *, sleep=time.sleep):
    """IL LIMITE GLOBALE (ricerca diretta E inversa): una transazione, UN lock
    condiviso da tutta l'applicazione. Chi tiene il lock chiama il provider e
    poi aspetta il resto del secondo PRIMA di rilasciarlo: cosi' due chiamate
    consecutive, di qualunque tipo e da qualunque processo, distano almeno
    MIN_INTERVAL. Restituisce (grezzi, errore)."""
    with core_cursor() as (_, cur):
        cur.execute("SET LOCAL lock_timeout = %s", (f"{LOCK_TIMEOUT_MS}ms",))
        try:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (LOCK_KEY,))
        except Exception as exc:               # lock_timeout: psycopg2 QueryCanceled / LockNotAvailable
            raise GeocodeBusy() from exc
        inizio = time.monotonic()
        grezzi, errore = None, None
        try:
            grezzi = chiamata()
        except GeocodeUnavailable as exc:
            errore = exc
        except Exception as exc:               # timeout, rete, DNS
            errore = exc
        resto = MIN_INTERVAL - (time.monotonic() - inizio)
        if resto > 0:
            sleep(resto)
    return grezzi, errore


def search(ctx, q, *, http=None, env=None, sleep=time.sleep) -> dict:
    ctx.require_agency()                       # solo dentro un'agenzia, come la mappa
    if not enabled(env):
        raise GeocodeDisabled()
    testo = normalize_query(q)
    chiave = testo.casefold()
    in_cache = _cache_get(chiave)
    if in_cache is not None:
        return {"query": testo, "results": in_cache, "cached": True, "attribution": "© OpenStreetMap contributors"}
    grezzi, errore = _con_il_turno(lambda: call_provider(testo, http=http, env=env), sleep=sleep)
    if grezzi is None:
        raise GeocodeUnavailable() from errore
    risultati = shape_results(grezzi, testo)
    _cache_put(chiave, risultati)
    return {"query": testo, "results": risultati, "cached": False, "attribution": "© OpenStreetMap contributors"}


# --- ZONA-5: la ricerca inversa -------------------------------------------------------

def call_reverse(lat, lng, *, http=None, env=None) -> dict:
    """UNA richiesta inversa a Nominatim: formato json, dettaglio al civico (zoom 18),
    indirizzo scomposto. SOLO le due coordinate: mai cookie, mai dati del CRM."""
    risposta = (http or requests).get(reverse_url(env), params={
        "lat": f"{lat:.7f}", "lon": f"{lng:.7f}", "format": "jsonv2", "zoom": 18, "addressdetails": 1, "accept-language": "it",
    }, headers={"User-Agent": user_agent(env), "Accept": "application/json"}, timeout=timeout_s(env))
    if risposta.status_code != 200:
        raise GeocodeUnavailable()
    try:
        return risposta.json()
    except ValueError as exc:
        raise GeocodeUnavailable() from exc


def reverse(ctx, lat, lon, *, http=None, env=None, sleep=time.sleep) -> dict:
    ctx.require_agency()
    if not enabled(env):
        raise GeocodeDisabled()
    from .building_position import validate_coordinates   # le stesse regole di T4 (intervallo, mai 0,0)
    la, lo = validate_coordinates(lat, lon)
    la_f, lo_f = float(la), float(lo)
    chiave = f"reverse:{la_f:.7f},{lo_f:.7f}"
    in_cache = _cache_get(chiave)
    if in_cache is not None:
        return {**in_cache[0], "cached": True, "attribution": "© OpenStreetMap contributors"}
    grezzi, errore = _con_il_turno(lambda: call_reverse(la_f, lo_f, http=http, env=env), sleep=sleep)
    if grezzi is None:
        raise GeocodeUnavailable() from errore
    esito = shape_reverse(grezzi, la_f, lo_f)
    _cache_put(chiave, [esito])
    return {**esito, "cached": False, "attribution": "© OpenStreetMap contributors"}
