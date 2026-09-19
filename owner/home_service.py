"""LMC-2 - "La Mia Casa": il read-model PRE-INCARICO del portale proprietario.

    GET /api/owner/portal/homes            la lista delle case che l'owner puo' vedere
    GET /api/owner/portal/homes/{stima_id} una casa sola, composta dai domini esistenti

NESSUNA TABELLA NUOVA, NESSUNA SCRITTURA DI DOMINIO. Questo modulo legge
`stime` attraverso il grant `owner_stima_access` (LMC-1A), la baseline e il
conteggio delle osservazioni da PROPERTY WATCH, e - solo come ripiego reale -
l'evento `stima_completata` di Seller Intelligence. Non ricalcola niente:
`valuation.py` non compare qui, e non comparira' in LMC-2 per nessuna ragione.

IL VALORE INIZIALE E IL VALORE CORRENTE SONO DUE COSE DIVERSE

`initial_value` e' il valore ORIGINARIO, storico e immutabile: quello che il
motore calcolo' al momento della stima e che PROPERTY WATCH incise nella
propria baseline. Si legge, non si ricostruisce.

`current_value` in LMC-2 e' `null`, con `current_value_status =
"history_not_available"`. Non esiste ancora nessun valore corrente persistito
nel sistema - `valuation_snapshot` e' LMC-3 - e spacciare la baseline per un
valore di oggi significherebbe dire al proprietario che la sua casa vale
adesso quanto valeva allora, che e' un'affermazione che nessuno ha fatto.

COSA NON ESCE DI QUI

Niente dati di chi compra (nomi, contatti, budget, richieste, abbinamenti),
niente metriche interne (pressione degli acquirenti, inventario dei
concorrenti, punteggi), niente dati personali del venditore, niente campi
gestionali del CRM. La regola non e' una lista di esclusioni ma il contrario:
ogni vista qui costruisce un dizionario NUOVO, campo per campo, a partire da
colonne dichiarate. Cio' che non e' stato scritto esplicitamente non esce.
"""

from __future__ import annotations

from typing import Any

from core.exceptions import NotFoundError
from property_watch.exceptions import WatchNotFoundError  # noqa: F401  (documenta la fonte)

from . import repository as owner_repository

#: I campi di `stime` su cui si misura la completezza del profilo. Insieme
#: DICHIARATO e chiuso: la percentuale e' `len(known) / len(PROFILE_FIELDS)`,
#: senza pesi e senza punteggi, quindi e' spiegabile dicendo quali campi ci
#: sono e quali no. Tutti descrivono l'immobile; nessuno riguarda la persona
#: (nome, email, telefono) ne' la gestione interna (`lead_status`,
#: `note_internal`, `prezzo_mq_base`).
#:
#: Nota onesta: il modulo pubblico scrive un valore di ripiego per alcuni di
#: questi campi (`piano`, `locali`, `bagni`, `ascensore`, `anno`, `stato`),
#: quindi risultano "conosciuti" anche quando il proprietario non li ha
#: dichiarati. La percentuale misura cio' che il database SA, non cio' che la
#: persona ha digitato: e' verificabile, ed e' l'unica lettura che non richiede
#: di indovinare.
PROFILE_FIELDS = (
    "comune", "microzona", "via", "civico", "tipologia", "mq", "piano",
    "locali", "bagni", "pertinenze", "ascensore", "anno", "stato",
    "vistamareyn", "distanzamare", "altrodescrizione",
)

#: La baseline: il punto di partenza, non un aggiornamento.
BASELINE_OBSERVATION = "watch_started"

#: I tipi di osservazione che costituiscono STORIA DEL VALORE dell'immobile.
#:
#: Vuoto, e resta vuoto per tutta LMC-2. Le osservazioni che PROPERTY WATCH
#: scrive oggi - `microzone_price_changed`, `internal_supply_snapshot`,
#: `buyer_pressure_snapshot` - sono monitoraggio del MERCATO attorno alla casa:
#: dicono che il prezzo medio della microzona si e' mosso, quante case
#: concorrenti ci sono, quanta pressione c'e' dal lato acquirenti. Nessuna di
#: esse afferma che QUESTA casa oggi vale una cifra diversa da quella della
#: stima. Trattarle come storia del valore significherebbe mostrare al
#: proprietario un andamento che nessuno ha calcolato.
#:
#: L'unica cosa che sara' storia del valore e' il `valuation_snapshot` di
#: LMC-3: un ricalcolo esplicito, con il motore ufficiale, datato e persistito.
#: Finche' non esiste, `valuation_history` e' false e `history_status` e'
#: `building` - qualunque sia il numero di osservazioni.
VALUATION_HISTORY_OBSERVATIONS: frozenset[str] = frozenset()

#: Gli stati dei dati esposti al frontend.
STATUS_READY = "ready"
STATUS_PARTIAL = "partial"
STATUS_BUILDING_HISTORY = "building_history"

#: Le fonti ammesse per il valore iniziale, in ordine di preferenza.
SOURCE_BASELINE = "property_watch_baseline"
SOURCE_EVENT = "seller_timeline_event"

#: Lo stato del valore corrente finche' LMC-3 non esiste.
CURRENT_VALUE_STATUS = "history_not_available"


class OwnerAgencyScope:
    """Uno scope minimo per leggere PROPERTY WATCH con l'agenzia del grant.

    Deliberatamente non un `OperatorContext`: dietro c'e' un proprietario, non
    un operatore, e costruirne uno dichiarerebbe un principale autenticato che
    non esiste. Espone solo `require_agency`, che e' tutto cio' che le funzioni
    scopate di PROPERTY WATCH chiedono a uno scope.

    L'agenzia non arriva mai dal client: la ricava `home_agency_for_account`
    dalla catena account -> contatto -> agenzia.
    """

    __slots__ = ("_agency_id",)

    def __init__(self, agency_id: int) -> None:
        self._agency_id = agency_id

    def require_agency(self) -> int:
        return self._agency_id


def _has_value(valore: Any) -> bool:
    """Un campo e' conosciuto se c'e' e non e' una stringa vuota."""
    if valore is None:
        return False
    if isinstance(valore, str):
        return valore.strip() != ""
    return True


def _price(payload) -> int | float | None:
    """`price_exact` da un payload, se c'e' ed e' un numero."""
    if not isinstance(payload, dict):
        return None
    valore = payload.get("price_exact")
    if isinstance(valore, bool) or not isinstance(valore, (int, float)):
        return None
    return valore


def initial_value(*, baseline_payload, completed_payload) -> tuple[Any, str | None]:
    """Il valore ORIGINARIO e da dove viene, oppure `(None, None)`.

    Due sole fonti, in quest'ordine: la baseline `watch_started` di PROPERTY
    WATCH, che e' la persistenza ufficiale del valore al momento della stima;
    e, se il watch non c'e' o non ha baseline, l'evento `stima_completata` di
    Seller Intelligence, che porta lo stesso numero scritto dallo stesso
    funnel. Nessuna terza via, e in particolare nessun ricalcolo.
    """
    valore = _price(baseline_payload)
    if valore is not None:
        return valore, SOURCE_BASELINE
    valore = _price(completed_payload)
    if valore is not None:
        return valore, SOURCE_EVENT
    return None, None


def build_profile(stima: dict[str, Any]) -> dict[str, Any]:
    """Quali campi dichiarati ci sono, quali mancano, e la percentuale.

    `conosciuti / considerati * 100`, arrotondata. Nessun peso: un campo vale
    un campo. L'ordine e' quello di `PROFILE_FIELDS`, quindi due chiamate sugli
    stessi dati danno lo stesso identico dizionario.
    """
    conosciuti = [campo for campo in PROFILE_FIELDS if _has_value(stima.get(campo))]
    mancanti = [campo for campo in PROFILE_FIELDS if campo not in conosciuti]
    return {
        "considered_fields": list(PROFILE_FIELDS),
        "known_fields": conosciuti,
        "missing_fields": mancanti,
        "completion_percent": round(len(conosciuti) / len(PROFILE_FIELDS) * 100),
    }


def _history(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """L'andamento del VALORE, e - separatamente - quanto monitoraggio c'e'.

    DUE COSE DIVERSE, E IL PUNTO DI QUESTA FUNZIONE E' NON CONFONDERLE.

    `observation_count`, `first_observed_at` e `last_observed_at` raccontano
    PROPERTY WATCH: quante osservazioni reali esistono su questa casa e in che
    arco di tempo. Sono dati veri e restano esposti.

    `history_available` e `history_status` riguardano invece l'andamento del
    VALORE, ed e' una domanda a cui oggi si risponde sempre "non ancora":
    nessuna delle osservazioni che il sistema scrive e' una rivalutazione
    dell'immobile (vedi VALUATION_HISTORY_OBSERVATIONS). Dieci osservazioni di
    mercato non fanno un punto in piu' su una curva di valore che nessuno ha
    calcolato.

    Solo conteggi e due date, mai il contenuto: i payload portano metriche
    interne, e in LMC-2 non sono pubblicabili.
    """
    momenti = [o.get("observed_at") for o in observations if o.get("observed_at") is not None]
    rivalutazioni = [o for o in observations
                     if o.get("observation_type") in VALUATION_HISTORY_OBSERVATIONS]
    disponibile = bool(rivalutazioni)
    return {
        "history_available": disponibile,
        "history_status": "available" if disponibile else "building",
        "observation_count": len(observations),
        "first_observed_at": min(momenti) if momenti else None,
        "last_observed_at": max(momenti) if momenti else None,
    }


def _data_status(valore, ha_watch: bool) -> str:
    """`ready` con valore e watch, `building_history` con il solo valore,
    `partial` quando il valore non c'e'."""
    if valore is None:
        return STATUS_PARTIAL
    return STATUS_READY if ha_watch else STATUS_BUILDING_HISTORY


def build_home_summary(*, stima, has_watch, initial_value, last_observed_at,
                       observation_count) -> dict[str, Any]:
    """Una voce della lista. Campo per campo, mai la riga cosi' com'e'."""
    del observation_count  # la lista dice SE c'e' storia, il dettaglio quanta
    return {
        "stima_id": stima.get("id"),
        "comune": stima.get("comune"),
        "microzona": stima.get("microzona"),
        "via": stima.get("via"),
        "civico": stima.get("civico"),
        "tipologia": stima.get("tipologia"),
        "mq": stima.get("mq"),
        "created_at": stima.get("data"),
        "has_watch": bool(has_watch),
        "initial_value": initial_value,
        "last_update_at": last_observed_at,
        "data_status": _data_status(initial_value, bool(has_watch)),
    }


def build_home_detail(*, stima, watch, observations, baseline_payload,
                      completed_payload) -> dict[str, Any]:
    """Il dettaglio di una casa, composto dai domini esistenti.

    `watch` e' la riga del watch oppure `None`; di essa esce SOLO presenza e
    stato. `observations` serve a contare e a datare, non a mostrare.
    """
    valore, fonte = initial_value(baseline_payload=baseline_payload,
                                  completed_payload=completed_payload)
    storia = _history(observations)
    ha_watch = watch is not None
    return {
        "stima_id": stima.get("id"),
        "property": {
            "comune": stima.get("comune"),
            "microzona": stima.get("microzona"),
            "via": stima.get("via"),
            "civico": stima.get("civico"),
            "tipologia": stima.get("tipologia"),
            "mq": stima.get("mq"),
            "piano": stima.get("piano"),
            "locali": stima.get("locali"),
            "bagni": stima.get("bagni"),
            "pertinenze": stima.get("pertinenze"),
            "ascensore": stima.get("ascensore"),
            "anno": stima.get("anno"),
            "stato": stima.get("stato"),
            "vistamareyn": stima.get("vistamareyn"),
            "distanzamare": stima.get("distanzamare"),
            "altrodescrizione": stima.get("altrodescrizione"),
        },
        "created_at": stima.get("data"),
        "valuation": {
            "initial_value": valore,
            "initial_value_source": fonte,
            "current_value": None,
            "current_value_status": CURRENT_VALUE_STATUS,
        },
        "watch": {"has_watch": ha_watch,
                  "status": watch.get("status") if ha_watch else None},
        "history": storia,
        "profile": build_profile(stima),
        "capabilities": {
            # Vera solo con una storia reale del VALORE, cioe' con almeno una
            # rivalutazione persistita. In LMC-2 non ne esistono: le
            # osservazioni di mercato di PROPERTY WATCH non lo sono, e
            # `valuation_snapshot` arriva con LMC-3. Quindi false, sempre.
            "valuation_history": storia["history_available"],
            # LMC-4: la domanda degli acquirenti non e' ancora pubblicata.
            "buyer_demand": False,
            # Non esiste nessuna fonte di comparabili per il proprietario.
            "comparables": False,
            # LMC-2 e' sola lettura.
            "profile_update": False,
        },
        "data_status": _data_status(valore, ha_watch),
    }


def _watch_bundle(agency_id: int, stima_id: int):
    """Presenza, storia e baseline del watch, oppure `None`. Solo lettura."""
    return owner_repository.home_watch_summary(agency_id, stima_id)


def list_homes(owner_account_id: int) -> list[dict[str, Any]]:
    """Le case dell'owner autenticato. Lista vuota se non ne ha nessuna.

    Nessun audit: aprire la propria lista non e' un accesso a un documento, e
    una riga per ogni caricamento del portale renderebbe `owner_audit_log`
    illeggibile proprio quando serve.
    """
    stime = owner_repository.list_home_grants(owner_account_id)
    if not stime:
        return []
    homes = []
    for stima in stime:
        agency_id = stima["agency_id"]
        watch = _watch_bundle(agency_id, stima["id"])
        baseline = watch["baseline_payload"] if watch else None
        completed = (None if _price(baseline) is not None
                     else owner_repository.home_completed_valuation(agency_id, stima["id"]))
        valore, _fonte = initial_value(baseline_payload=baseline, completed_payload=completed)
        momenti = [o["observed_at"] for o in (watch["observations"] if watch else [])
                   if o.get("observed_at") is not None]
        homes.append(build_home_summary(
            stima=stima,
            has_watch=watch is not None,
            initial_value=valore,
            last_observed_at=max(momenti) if momenti else None,
            observation_count=len(momenti),
        ))
    return homes


def get_home(owner_account_id: int, stima_id: int) -> dict[str, Any]:
    """Una casa sola. `NotFoundError` - il 404 neutro di OWNER - in ogni altro caso.

    L'autorizzazione e' la prima cosa che accade: `get_home_grant` solleva
    prima che qualunque altro dominio venga interrogato, quindi un id altrui
    non produce nemmeno una lettura di PROPERTY WATCH da cui dedurre qualcosa
    (per esempio dal tempo di risposta).
    """
    stima = owner_repository.get_home_grant(owner_account_id, stima_id)
    agency_id = stima["agency_id"]

    watch = _watch_bundle(agency_id, stima_id)
    baseline = watch["baseline_payload"] if watch else None
    completed = (None if _price(baseline) is not None
                 else owner_repository.home_completed_valuation(agency_id, stima_id))

    vista = build_home_detail(stima=stima, watch={"status": watch["status"]} if watch else None,
                              observations=watch["observations"] if watch else [],
                              baseline_payload=baseline, completed_payload=completed)
    _audit_view(owner_account_id, stima_id)
    return vista


def _audit_view(owner_account_id: int, stima_id: int) -> None:
    """La traccia locale dell'apertura, come per ogni altro documento OWNER.

    Non e' un evento Seller Intelligence: quelli sono LMC-7, e sono un'altra
    decisione. Qui resta il registro interno che il dominio gia' tiene, con la
    stessa forma delle altre azioni (`owner_audit_log`), e solo per il
    DETTAGLIO: la lista non lascia traccia.
    """
    try:
        owner_repository.audit("home_viewed", owner_account_id,
                               etype="stima", eid=stima_id)
    except Exception:  # noqa: BLE001 - una traccia mancata non nega l'accesso
        pass


def build_dashboard(owner_account_id: int) -> dict[str, Any]:
    """Il cruscotto: gli immobili di sempre, piu' le case pre-incarico.

    ADDITIVO. `properties` e `property_count` restano quelli che erano, con
    la stessa funzione dietro: un owner POST-INCARICO che non ha nessuna stima
    vede esattamente cio' che vedeva prima, con due campi in piu' che valgono
    lista vuota e zero. Le due superfici convivono perche' lo stesso
    proprietario puo' avere una casa in stima e un immobile in incarico.
    """
    properties = owner_repository.portal_properties(owner_account_id)
    homes = list_homes(owner_account_id)
    return {"properties": properties, "property_count": len(properties),
            "homes": homes, "home_count": len(homes)}
