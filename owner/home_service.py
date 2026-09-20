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

from datetime import datetime, timedelta, timezone
from typing import Any

import home_profile
from core.exceptions import NotFoundError
from property_watch.exceptions import WatchNotFoundError  # noqa: F401  (documenta la fonte)

from . import demand
from . import repository as owner_repository
from . import tracking

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
#: Uno solo, e per costruzione. Le altre osservazioni che PROPERTY WATCH
#: scrive - `microzone_price_changed`, `internal_supply_snapshot`,
#: `buyer_pressure_snapshot` - sono monitoraggio del MERCATO attorno alla casa:
#: dicono che il prezzo medio della microzona si e' mosso, quante case
#: concorrenti ci sono, quanta pressione c'e' dal lato acquirenti. Nessuna di
#: esse afferma che QUESTA casa oggi vale una cifra diversa, e trattarle come
#: storia del valore significherebbe mostrare un andamento che nessuno ha
#: calcolato.
#:
#: `valuation_snapshot` (LMC-3) lo afferma: e' un ricalcolo esplicito con il
#: motore ufficiale, datato, con l'impronta dell'input e dell'algoritmo.
VALUATION_HISTORY_OBSERVATIONS: frozenset[str] = frozenset({"valuation_snapshot"})

#: I periodi su cui si espone una variazione, in giorni.
CHANGE_WINDOWS = (("change_30d", 30), ("change_90d", 90), ("change_365d", 365))

#: I campi di uno snapshot che il proprietario vede. `reason` e `input_digest`
#: restano interni: dicono perche' il sistema ha ricalcolato e su quali dati,
#: che e' diagnostica, non informazione sulla casa.
SNAPSHOT_PUBLIC_FIELDS = ("computed_at", "price_exact", "eur_mq_finale",
                          "algorithm_fingerprint")

#: Gli stati dei dati esposti al frontend.
STATUS_READY = "ready"
STATUS_PARTIAL = "partial"
STATUS_BUILDING_HISTORY = "building_history"

#: Le fonti ammesse per il valore iniziale, in ordine di preferenza.
SOURCE_BASELINE = "property_watch_baseline"
SOURCE_EVENT = "seller_timeline_event"

#: Lo stato del valore corrente: `available` quando esiste almeno uno
#: snapshot, altrimenti `history_not_available`. La baseline NON e' un valore
#: corrente e non fa mai diventare questo stato `available`.
CURRENT_VALUE_AVAILABLE = "available"
CURRENT_VALUE_MISSING = "history_not_available"


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


def _dichiarato_vuoto(campo: str, overridden) -> bool:
    """Il proprietario ha risposto "nessuna", ed e' una risposta.

    LMC-10. Un campo puo' essere conosciuto in due modi: perche' porta un
    valore (`_has_value`), oppure perche' il proprietario ha dichiarato
    esplicitamente che non ce n'e'. Il secondo modo esiste solo dove la
    stringa vuota e' un'affermazione sulla casa invece dell'assenza di un
    dato, cioe' per `pertinenze` e per nient'altro: l'insieme arriva da
    `home_profile.EMPTY_MEANS_OVERRIDE`, non da un elenco ricopiato qui.

    E si guarda `overridden`, non il valore: e' la differenza fra
    "`owner_home_overrides.pertinenze` vale `''`" e "vale NULL". Nel primo
    caso il proprietario ha risposto, nel secondo non ha detto niente e vale
    la regola di LMC-2 sul dato originale. Leggere la stringa vuota senza
    sapere da dove viene confonderebbe i due casi, ed e' esattamente
    l'errore che questa funzione esiste per non fare.

    Una stringa vuota in un altro campo non acquista nessun significato:
    `altrodescrizione` salvato vuoto resta un campo non compilato.
    """
    return campo in home_profile.EMPTY_MEANS_OVERRIDE and campo in (overridden or ())


def build_profile(stima: dict[str, Any], *, version: int = 0,
                  overridden: tuple[str, ...] = (),
                  updated_at: Any = None) -> dict[str, Any]:
    """Quali campi dichiarati ci sono, quali mancano, e la percentuale.

    `conosciuti / considerati * 100`, arrotondata. Nessun peso: un campo vale
    un campo. L'ordine e' quello di `PROFILE_FIELDS`, quindi due chiamate sugli
    stessi dati danno lo stesso identico dizionario.

    LMC-10: un campo conta come conosciuto anche quando il proprietario ha
    dichiarato esplicitamente che non c'e' - oggi solo le pertinenze. Vedi
    `_dichiarato_vuoto`.
    """
    conosciuti = [campo for campo in PROFILE_FIELDS
                  if _has_value(stima.get(campo))
                  or _dichiarato_vuoto(campo, overridden)]
    mancanti = [campo for campo in PROFILE_FIELDS if campo not in conosciuti]
    return {
        "considered_fields": list(PROFILE_FIELDS),
        "known_fields": conosciuti,
        "missing_fields": mancanti,
        "completion_percent": round(len(conosciuti) / len(PROFILE_FIELDS) * 100),
        # LMC-10. `editable_fields` e' la whitelist, e viene di la' invece di
        # essere ricopiata: il form del proprietario non deve poter mostrare
        # una casella che il server rifiuterebbe.
        "editable_fields": list(home_profile.OVERRIDABLE_FIELDS),
        "overridden_fields": list(overridden),
        "version": version,
        "updated_at": updated_at,
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


def _snapshots(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gli snapshot del valore, in ordine di calcolo. Solo quelli veri.

    Un'osservazione senza payload - tutte quelle che non sono snapshot, per
    come il repository le legge - non puo' entrare qui nemmeno per errore.
    """
    punti = [o for o in observations
             if o.get("observation_type") in VALUATION_HISTORY_OBSERVATIONS
             and isinstance(o.get("payload"), dict)
             and _price(o["payload"]) is not None]
    return sorted(punti, key=lambda o: o["payload"].get("computed_at") or o.get("observed_at") or "")


def _public_snapshot(osservazione: dict[str, Any]) -> dict[str, Any]:
    """Un punto dello storico, campo per campo."""
    payload = osservazione["payload"]
    return {campo: payload.get(campo) for campo in SNAPSHOT_PUBLIC_FIELDS}


def _as_datetime(valore) -> datetime | None:
    if isinstance(valore, datetime):
        return valore if valore.tzinfo else valore.replace(tzinfo=timezone.utc)
    if isinstance(valore, str):
        try:
            momento = datetime.fromisoformat(valore.replace("Z", "+00:00"))
        except ValueError:
            return None
        return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)
    return None


def _change(punti: list[dict[str, Any]], giorni: int):
    """La variazione su una finestra, oppure `None` se la storia non basta.

    L'ANCORA E' L'ULTIMO SNAPSHOT REALE, NON L'OROLOGIO. Il confine si misura
    da `ultimo.computed_at`, non da adesso: "negli ultimi 30 giorni" significa
    "nei 30 giorni che precedono il valore che sto mostrando". Se l'ultimo
    ricalcolo e' di 40 giorni fa, una finestra ancorata a oggi confronterebbe
    quel valore con se' stesso - il confine `oggi - 30 giorni` cadrebbe DOPO
    di esso, quindi nessun punto precedente resterebbe eleggibile, oppure
    peggio lo si prenderebbe come termine di paragone di se' stesso. Ancorando
    all'ultimo snapshot, `change_30d` resta sempre "quanto e' cambiato nei 30
    giorni prima di questa misura", che e' l'unica frase vera che i dati
    sostengono.

    LA REGOLA DI SELEZIONE, per intero. Si prende l'ultimo snapshot e, fra
    quelli calcolati PRIMA del confine `ultimo.computed_at - giorni`, il PIU'
    RECENTE: e' quello che meglio rappresenta "com'era allora" senza andare
    piu' indietro del necessario. L'ultimo non puo' mai essere anche il punto
    di partenza (si guarda solo `punti[:-1]`). Se nessuno snapshot e'
    abbastanza vecchio, la risposta e' `None` - non si usa la baseline al suo
    posto, non si ripiega su osservazioni di mercato, e non si sposta il
    confine per far tornare un numero.

    METODOLOGIA. Se i due punti sono stati calcolati con impronte diverse,
    la differenza mescola mercato e metodo: `methodology_changed` lo dice, e
    `change_percent` diventa `None` perche' quei due numeri non si sottraggono.
    I due valori restano visibili - sono entrambi veri - ma la percentuale
    che li lega non lo sarebbe.
    """
    if len(punti) < 2:
        return None
    ultimo = punti[-1]
    ancora = (_as_datetime(ultimo["payload"].get("computed_at")) or
              _as_datetime(ultimo.get("observed_at")))
    if ancora is None:
        return None
    confine = ancora - timedelta(days=giorni)
    # Un punto con data illeggibile ricade sull'ancora, che e' posteriore al
    # confine: resta fuori. Nessuna data inventata lo fa entrare.
    precedenti = [p for p in punti[:-1]
                  if (_as_datetime(p["payload"].get("computed_at")) or
                      _as_datetime(p.get("observed_at")) or ancora) <= confine]
    if not precedenti:
        return None
    storico = precedenti[-1]

    da = _price(storico["payload"])
    a = _price(ultimo["payload"])
    metodologia_cambiata = (storico["payload"].get("algorithm_fingerprint")
                            != ultimo["payload"].get("algorithm_fingerprint"))
    percentuale = None
    if not metodologia_cambiata and da:
        percentuale = round((a - da) / da * 100, 2)
    return {
        "from_value": da,
        "to_value": a,
        "from_computed_at": storico["payload"].get("computed_at"),
        "to_computed_at": ultimo["payload"].get("computed_at"),
        "change_percent": percentuale,
        "methodology_changed": metodologia_cambiata,
    }


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
                      completed_payload, buyer_pressure=None,
                      overrides=None) -> dict[str, Any]:
    """Il dettaglio di una casa, composto dai domini esistenti.

    `watch` e' la riga del watch oppure `None`; di essa esce SOLO presenza e
    stato. `observations` serve a contare e a datare, non a mostrare.

    `buyer_pressure` (LMC-4) e' l'ultima rilevazione della domanda, gia'
    sfoltita dal repository, oppure `None`. Arriva dal chiamante e non viene
    letta qui: chi costruisce la vista non apre connessioni.

    Nessun orologio: tutto cio' che questa vista dice sul valore e' datato dai
    dati stessi. Due chiamate a distanza di mesi sulle stesse osservazioni
    danno lo stesso identico dizionario.
    """
    # LMC-10: cio' che si mostra e' il PROFILO EFFETTIVO, cioe' l'originale
    # con sopra le correzioni del proprietario. La somma la fa `home_profile`,
    # e la fa anche per Property Watch e per il CRM: una regola sola, in un
    # posto solo. `stima` resta la riga originale e non viene modificata.
    profilo = home_profile.build_effective_home_profile(stima, overrides)
    casa = profilo["home"]
    valore, fonte = initial_value(baseline_payload=baseline_payload,
                                  completed_payload=completed_payload)
    storia = _history(observations)
    ha_watch = watch is not None
    punti = _snapshots(observations)
    ultimo = punti[-1]["payload"] if punti else None
    corrente = _price(ultimo) if ultimo is not None else None
    variazioni = {nome: _change(punti, giorni) for nome, giorni in CHANGE_WINDOWS}
    domanda = demand.build(buyer_pressure)
    return {
        "stima_id": casa.get("id"),
        "property": {
            "comune": casa.get("comune"),
            "microzona": casa.get("microzona"),
            "via": casa.get("via"),
            "civico": casa.get("civico"),
            "tipologia": casa.get("tipologia"),
            "mq": casa.get("mq"),
            "piano": casa.get("piano"),
            "locali": casa.get("locali"),
            "bagni": casa.get("bagni"),
            "pertinenze": casa.get("pertinenze"),
            "ascensore": casa.get("ascensore"),
            "anno": casa.get("anno"),
            "stato": casa.get("stato"),
            "vistamareyn": casa.get("vistamareyn"),
            "distanzamare": casa.get("distanzamare"),
            "altrodescrizione": casa.get("altrodescrizione"),
        },
        "created_at": casa.get("data"),
        # LMC-10: la versione del profilo, che il client rimanda indietro come
        # `expected_version`. Zero significa "nessuna correzione ancora", ed
        # e' il valore con cui si fa la PRIMA modifica: cosi' il client non
        # deve distinguere "non c'e' riga" da "c'e' e vale qualcosa".
        "profile_version": profilo["version"],
        "valuation": {
            # Il valore ORIGINARIO: storico, immutabile, mai ricalcolato.
            "initial_value": valore,
            "initial_value_source": fonte,
            # Il valore di oggi: l'ultimo snapshot reale, se esiste.
            "current_value": corrente,
            "current_value_status": (CURRENT_VALUE_AVAILABLE if corrente is not None
                                     else CURRENT_VALUE_MISSING),
            # Quando e' stato calcolato quel valore. Serve a non far credere
            # che sia di oggi: e' la stessa data su cui sono ancorate le
            # finestre 30/90/365. `None` finche' nessuno snapshot esiste.
            "current_value_computed_at": (ultimo.get("computed_at")
                                          if ultimo is not None else None),
            **variazioni,
        },
        "valuation_history": [_public_snapshot(p) for p in punti],
        # LMC-4: la domanda acquirenti, aggregata. Vedi `owner/demand.py` per
        # cosa resta dentro Property Watch e perche'.
        "buyer_demand": domanda,
        "watch": {"has_watch": ha_watch,
                  "status": watch.get("status") if ha_watch else None},
        "history": storia,
        "profile": build_profile(casa, version=profilo["version"],
                                 overridden=profilo["overridden_fields"],
                                 updated_at=profilo["updated_at"]),
        "capabilities": {
            # Vera solo con una storia reale del VALORE, cioe' con almeno una
            # rivalutazione persistita. In LMC-2 non ne esistono: le
            # osservazioni di mercato di PROPERTY WATCH non lo sono, e
            # `valuation_snapshot` arriva con LMC-3. Quindi false, sempre.
            "valuation_history": storia["history_available"],
            # Vera solo con una rilevazione Buyer Pressure leggibile: senza
            # misura, o con una misura che non si lascia interpretare, il
            # blocco esiste ma dice "non disponibile", e la capability lo
            # segue invece di promettere una sezione vuota.
            "buyer_demand": domanda["status"] != demand.STATUS_UNAVAILABLE,
            # Non esiste nessuna fonte di comparabili per il proprietario.
            "comparables": False,
            # LMC-10: il proprietario puo' correggere i dati della sua casa.
            # Vera per chiunque arrivi fin qui, perche' arrivarci significa
            # gia' avere un grant valido su questa stima: non c'e' una
            # seconda condizione da controllare, e inventarne una (per
            # esempio "solo se c'e' un watch") vorrebbe dire nascondere il
            # form proprio alle case con meno dati.
            #
            # Il CRM (LMC-8) compone la stessa vista con `stima={}`: li' non
            # c'e' nessun proprietario e nessun form, e la capability non
            # viene letta.
            "profile_update": True,
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
    # LMC-10: gli override di TUTTE le case in una query sola. Il riepilogo
    # mostra `mq`, che e' un campo correggibile: senza questa lettura la
    # lista direbbe 95 e il dettaglio 110 della stessa casa. Una query per
    # riga sarebbe stata l'altra soluzione, e con dieci case sono dieci
    # viaggi in piu' per un dato che quasi nessuna casa ha.
    override = owner_repository.home_overrides_for_account(owner_account_id)
    homes = []
    for originale in stime:
        stima = home_profile.effective_home(originale, override.get(originale["id"]))
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


def compose_home(owner_account_id: int, stima_id: int) -> dict[str, Any]:
    """La vista di una casa, senza audit e senza radar. `NotFoundError` altrimenti.

    E' il corpo di `get_home`, estratto perche' LMC-10 ha un secondo lettore:
    la risposta dell'aggiornamento, che restituisce la casa gia' aggiornata
    per non costringere il frontend a una seconda GET (e a una finestra in
    cui mostra dati vecchi). Quella risposta NON deve pero' contare come una
    visita - chi ha appena salvato non e' "tornato a guardare" - ne' lasciare
    una riga in `owner_audit_log` per un'azione che non e' un accesso.

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

    # LMC-4: la domanda acquirenti, solo se questa casa ha un watch. Lettura
    # separata e volutamente stretta: `get_current_watch_state_scoped`
    # risponderebbe alla stessa domanda, ma caricherebbe ogni osservazione
    # con il suo payload - metriche interne comprese - dentro il processo del
    # portale. Meglio non leggere il dato privato che leggerlo e scartarlo.
    pressione = owner_repository.home_buyer_pressure(agency_id, stima_id) if watch else None

    return build_home_detail(stima=stima, watch={"status": watch["status"]} if watch else None,
                             observations=watch["observations"] if watch else [],
                             baseline_payload=baseline, completed_payload=completed,
                             buyer_pressure=pressione,
                             overrides=owner_repository.home_override(agency_id, stima_id))


def get_home(owner_account_id: int, stima_id: int) -> dict[str, Any]:
    """Una casa sola, come la vede il proprietario che la apre.

    Composizione, poi la traccia locale, poi il radar - in quest'ordine, e
    con il radar per ultimo perche' non deve poter togliere niente a chi sta
    guardando la propria casa.
    """
    vista = compose_home(owner_account_id, stima_id)
    _audit_view(owner_account_id, stima_id)
    # LMC-7: l'apertura della scheda e' un fatto che il server osserva, non
    # una dichiarazione del client. Dopo l'audit e dopo che la vista e' gia'
    # costruita: se il tracciamento fallisce, il proprietario ha comunque la
    # sua casa davanti. `track_home_viewed` non solleva mai.
    #
    # Qui, non nella lista: aprire `/homes` significa vedere un elenco di
    # indirizzi, non guardare una casa, e contarlo come visita farebbe
    # risultare "tornato" chi ha solo fatto login.
    tracking.track_home_viewed(owner_account_id, stima_id)
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
