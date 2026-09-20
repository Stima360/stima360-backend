"""LMC-7 - "Interesse La Mia Casa": dagli eventi a una frase leggibile.

UN LIVELLO CHE SI PUO' DISCUTERE.

Non c'e' un punteggio. Un 87/100 sembra preciso e non lo e': nessuno sa dire
perche' non sia 84, e un operatore che lo vede o ci crede troppo o smette di
guardarlo. Qui il livello e' una funzione a gradini di due fatti contabili -
in quanti GIORNI distinti il proprietario e' tornato, e se ha aperto una delle
due sezioni che richiedono un gesto esplicito - e ogni gradino ha una frase
che lo spiega. Se il livello non si potesse giustificare con una riga di
testo, la soglia sarebbe sbagliata.

IL GIORNO E' L'UNITA'.

Dieci aperture in un pomeriggio sono un pomeriggio. Contare gli eventi invece
dei giorni premierebbe chi ricarica la pagina, che e' esattamente il
comportamento meno informativo che esista.

ALTA INTENZIONE.

Aprire la scheda e' curiosita'. Chiedere di vedere l'andamento del valore o
la domanda di acquirenti e' un gesto in piu', deliberato, e per questo pesa:
sono le due azioni che in LMC-6 richiedono un click esplicito.

Questo modulo LEGGE e basta. Gli eventi li scrive `owner/tracking.py`, li
conserva Seller Intelligence, e qui non esiste nessuna tabella nuova.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from seller_intelligence import service as seller_intelligence

from .tracking import CONSULTATION_REQUESTED, EVENT_SOURCE, HOME_UPDATED, HOME_VIEWED

#: Le finestre, in giorni.
WINDOW_DAYS = 30
RECENT_WINDOW_DAYS = 7

#: Le soglie. Dichiarate qui, una volta, perche' un numero sparso in un `if`
#: e' una regola che nessuno puo' rivedere.
ACTIVE_DAYS_FOR_HIGH_7D = 2
ACTIVE_DAYS_FOR_MEDIUM_30D = 2

LEVELS = ("none", "low", "medium", "high")

VALUE_HISTORY_VIEWED = "owner_value_history_viewed"
BUYER_DEMAND_VIEWED = "owner_buyer_demand_viewed"

#: I gesti espliciti: le due sezioni che si aprono solo con un click, e -
#: da LMC-10 - la correzione dei dati della casa.
#:
#: Aggiornare la propria casa e' il piu' deliberato dei tre: chi lo fa ha
#: aperto un form, cambiato un dato e salvato. Resta pero' un segnale come
#: gli altri e non un livello: da solo porta a MEDIO, e diventa ALTO solo
#: insieme ai ritorni recenti, esattamente come consultare l'andamento del
#: valore. L'unica cosa che vale HIGH da sola e' la richiesta di LMC-9, che
#: non e' un'osservazione ma una persona che ha chiesto di essere chiamata.
HIGH_INTENTION_EVENTS = frozenset({VALUE_HISTORY_VIEWED, BUYER_DEMAND_VIEWED,
                                   HOME_UPDATED})

#: L'insieme chiuso di cio' che conta come attivita' del proprietario. Un
#: `stima_richiesta` e' del funnel, una `nota_agente` e' dell'operatore:
#: nessuno dei due dice che il proprietario e' tornato a guardare.
TRACKED_EVENTS = frozenset({HOME_VIEWED, CONSULTATION_REQUESTED,
                            *HIGH_INTENTION_EVENTS})

#: La frase che spiega il livello quando c'e' una richiesta. Viene per
#: prima perche' e' la cosa piu' importante che si possa dire di quella
#: persona: non ha guardato, ha chiesto.
CONSULTATION_REASON = "Ha richiesto una verifica gratuita"

#: LMC-10. Viene subito dopo la richiesta perche' e' la seconda cosa piu'
#: utile da sapere prima di telefonare: i dati su cui l'agenzia ragiona sono
#: cambiati, e li ha cambiati il proprietario.
HOME_UPDATED_REASON = "Ha aggiornato i dati della casa"

#: Quante righe si leggono. Un evento significativo al giorno per tre tipi su
#: trenta giorni fa novanta nel caso peggiore: 200 e' largo e limitato.
READ_LIMIT = 200


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


def _rilevanti(events, adesso: datetime) -> list[tuple[str, datetime]]:
    """Gli eventi del portale proprietario dentro la finestra, datati.

    Il filtro e' per tipo E per sorgente: un evento che si chiamasse allo
    stesso modo ma arrivasse da un'altra superficie non e' attivita' dentro
    La Mia Casa.
    """
    confine = adesso - timedelta(days=WINDOW_DAYS)
    dentro = []
    for evento in events or []:
        if not isinstance(evento, dict):
            continue
        tipo = evento.get("event_type")
        if tipo not in TRACKED_EVENTS:
            continue
        if evento.get("event_source") != EVENT_SOURCE:
            continue
        quando = _as_datetime(evento.get("occurred_at"))
        if quando is None or quando < confine or quando > adesso:
            continue
        dentro.append((tipo, quando))
    return dentro


def _giorni(coppie, adesso: datetime, finestra: int) -> set:
    confine = adesso - timedelta(days=finestra)
    return {quando.astimezone(timezone.utc).date()
            for _tipo, quando in coppie if quando >= confine}


def _reasons(*, attivi_7, attivi_30, aperture_30, storico, domanda,
             consultazione, aggiornata, ultima: datetime | None) -> list[str]:
    """Le frasi. In terza persona: le legge un operatore, non il proprietario."""
    if not attivi_30:
        return ["Nessuna attività nel portale negli ultimi 30 giorni"]

    ragioni = []
    if consultazione:
        ragioni.append(CONSULTATION_REASON)
    if aggiornata:
        ragioni.append(HOME_UPDATED_REASON)
    if attivi_7 >= ACTIVE_DAYS_FOR_HIGH_7D:
        ragioni.append(f"È tornato {attivi_7} giorni negli ultimi "
                       f"{RECENT_WINDOW_DAYS} giorni")
    elif attivi_30 >= ACTIVE_DAYS_FOR_MEDIUM_30D:
        ragioni.append(f"È tornato {attivi_30} giorni negli ultimi "
                       f"{WINDOW_DAYS} giorni")
    elif aperture_30:
        ragioni.append("Ha aperto la scheda della casa una volta negli ultimi "
                       f"{WINDOW_DAYS} giorni")

    if storico:
        ragioni.append("Ha consultato l’andamento del valore")
    if domanda:
        ragioni.append("Ha consultato la domanda di immobili simili")
    if ultima is not None:
        ragioni.append("Ultima attività il "
                       f"{ultima.astimezone(timezone.utc).strftime('%d/%m/%Y')}")
    return ragioni


def _level(*, attivi_7: int, attivi_30: int, alta_intenzione: bool,
           consultation_requested: bool) -> str:
    """I gradini, nell'ordine in cui si leggono.

    `high` chiede DUE cose insieme: che sia tornato piu' di una volta nella
    settimana e che abbia aperto almeno una sezione ad alta intenzione. Una
    sola delle due non basta - chi ricarica spesso non e' un venditore
    pronto, e chi apre l'andamento una volta sola sta ancora guardandosi
    intorno - e chiederle entrambe e' cio' che rende il livello difendibile
    davanti a chi deve decidere se telefonare.
    """
    if consultation_requested:
        # LMC-9: una richiesta esplicita batte qualunque conteggio. Oggi non
        # puo' ancora accadere, e il ramo esiste per dire dove andra'.
        return "high"
    if attivi_7 >= ACTIVE_DAYS_FOR_HIGH_7D and alta_intenzione:
        return "high"
    if attivi_30 >= ACTIVE_DAYS_FOR_MEDIUM_30D or alta_intenzione:
        return "medium"
    if attivi_30 >= 1:
        return "low"
    return "none"


def build_interest(events, *, now: datetime | None = None) -> dict[str, Any]:
    """La proiezione, da una lista di eventi gia' letta. Pura.

    Prende gli eventi invece di andarli a cercare: cosi' la regola si prova
    senza un database, e chi legge decide da quale scope arrivano le righe.
    """
    adesso = now or datetime.now(timezone.utc)
    coppie = _rilevanti(events, adesso)

    attivi_30 = _giorni(coppie, adesso, WINDOW_DAYS)
    attivi_7 = _giorni(coppie, adesso, RECENT_WINDOW_DAYS)
    aperture = {quando.astimezone(timezone.utc).date()
                for tipo, quando in coppie if tipo == HOME_VIEWED}
    storico = any(tipo == VALUE_HISTORY_VIEWED for tipo, _ in coppie)
    domanda = any(tipo == BUYER_DEMAND_VIEWED for tipo, _ in coppie)
    ultima = max((quando for _tipo, quando in coppie), default=None)

    # LMC-9: la richiesta, DENTRO LA FINESTRA. `coppie` contiene solo gli
    # eventi degli ultimi 30 giorni, quindi una richiesta di quaranta giorni
    # fa non accende piu' questo campo. L'evento resta nel database e la
    # timeline lo mostra: quello che scade e' l'affermazione "sta chiedendo
    # ADESSO", che e' l'unica che questo read-model fa. Uno stato eterno in
    # una proiezione dichiarata a 30 giorni sarebbe una seconda verita'.
    consultation_requested = any(tipo == CONSULTATION_REQUESTED for tipo, _ in coppie)

    # LMC-10: vero quando esiste un `owner_home_updated` DENTRO LA FINESTRA,
    # cioe' con la stessa scadenza degli altri campi di questa proiezione.
    # L'evento resta nel database e la timeline continua a mostrarlo: quello
    # che scade e' l'affermazione "ha aggiornato di recente", che e' l'unica
    # che questo read-model fa.
    #
    # La fonte e' Seller Intelligence e non `owner_home_overrides.updated_at`.
    # Sarebbe stato piu' robusto - un dato non si perde mentre un evento
    # fail-open si' - ma avrebbe messo due sorgenti diverse dietro lo stesso
    # riquadro: la timeline dell'operatore mostrerebbe l'evento e il radar
    # un'altra data, e quando le due divergessero nessuno saprebbe quale
    # credere. Una sorgente sola, dichiarata.
    home_updated = any(tipo == HOME_UPDATED for tipo, _ in coppie)

    return {"interest": {
        "level": _level(attivi_7=len(attivi_7), attivi_30=len(attivi_30),
                        alta_intenzione=storico or domanda or home_updated,
                        consultation_requested=consultation_requested),
        "active_days_7d": len(attivi_7),
        "active_days_30d": len(attivi_30),
        "last_activity_at": ultima.isoformat() if ultima is not None else None,
        "home_viewed_days_30d": len(aperture),
        "value_history_viewed": storico,
        "buyer_demand_viewed": domanda,
        "home_updated": home_updated,
        "consultation_requested": consultation_requested,
        "reasons": _reasons(attivi_7=len(attivi_7), attivi_30=len(attivi_30),
                            aperture_30=len(aperture), storico=storico,
                            domanda=domanda, consultazione=consultation_requested,
                            aggiornata=home_updated, ultima=ultima),
    }}


def interest_for_stima_scoped(ctx, stima_id: int, *,
                              now: datetime | None = None) -> dict[str, Any]:
    """La proiezione per una stima, letta nello scope di un operatore.

    Il lettore e' il CRM (LMC-8), quindi il contesto e' quello di un
    operatore e la tenancy e' quella che Seller Intelligence gia' applica:
    `list_timeline_scoped` filtra per `agency_id` prima di qualunque altra
    condizione, percio' la timeline di un'altra agenzia non e' raggiungibile
    nemmeno indovinando un id.
    """
    eventi = seller_intelligence.list_timeline_scoped(
        ctx, stima_id=stima_id, limit=READ_LIMIT)
    return build_interest(eventi, now=now)
