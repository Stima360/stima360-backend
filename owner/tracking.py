"""LMC-7 - il radar: da un'azione del proprietario a un evento di dominio.

COSA REGISTRA, E PERCHE' COSI' POCO.

Tre soli fatti, e nessuno di essi e' una misura: che il proprietario abbia
aperto la scheda della sua casa, che abbia chiesto di vedere l'andamento del
valore, che abbia chiesto di vedere la domanda. Il "quanto" non esiste - non
c'e' un tempo di permanenza, non c'e' un conteggio di click, non c'e' un
punteggio - perche' l'unica cosa che un operatore puo' davvero fare con
questo dato e' richiamare una persona, e per quello basta sapere se e' tornata
e cosa e' andata a guardare.

UN EVENTO SIGNIFICATIVO AL GIORNO.

Aprire la stessa casa dieci volte in un pomeriggio non e' dieci volte piu'
interessante: e' la stessa giornata. La chiave di idempotenza porta il giorno
UTC, quindi la seconda apertura non scrive una riga nuova e - cosa altrettanto
importante - non tocca quella gia' scritta: l'ora registrata resta la PRIMA
della giornata, e il registro resta append-only com'e' sempre stato.

Il giorno e' UTC per decisione, non per distrazione: e' l'unico confine che
non cambia con l'ora legale e che due processi diversi calcolano allo stesso
modo. Alle 01:30 italiane di lunedi' l'evento appartiene ancora a domenica.

CHI DECIDE COSA.

Il client dice al massimo una `action` scelta in un insieme chiuso, e la
stima. Tutto il resto - tipo di evento, sorgente, agenzia, contatto, lead -
lo deriva il server. Un portale che potesse dichiarare `event_type` potrebbe
scrivere nella timeline di vendita qualunque cosa, e quella timeline e' la
memoria su cui il CRM decide chi richiamare.

FAIL-OPEN, MA NON FAIL-SLOPPY.

Registrare non deve poter rompere la pagina: se la scrittura fallisce, il
proprietario vede la sua casa come se niente fosse. Il percorso resta pero'
scopato: il fail-open riguarda l'ESITO della scrittura, mai il controllo di
chi sta scrivendo su cosa. Una stima non autorizzata non e' un errore da
ingoiare, e infatti non arriva mai fin qui.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from seller_intelligence import service as seller_intelligence

from . import repository as owner_repository

logger = logging.getLogger(__name__)

#: La sorgente, fissa. `seller_timeline_events.event_source` e' VARCHAR(30).
EVENT_SOURCE = "owner_portal"

#: L'apertura della scheda. Non e' un'azione dichiarabile dal client: nasce
#: dalla GET del dettaglio, cioe' da un fatto che il server osserva da se'.
HOME_VIEWED = "owner_home_viewed"

#: L'INSIEME CHIUSO. Da `action` del client a `event_type` di dominio, e in
#: una direzione sola: quello che non e' scritto qui non e' registrabile.
#:
#: I comparabili non ci sono perche' non esistono: LMC-5 ha chiuso la fonte
#: dati, la sezione non e' in pagina e un evento su qualcosa che nessuno puo'
#: guardare sarebbe un dato inventato.
ACTIONS = {
    "value_history_viewed": "owner_value_history_viewed",
    "buyer_demand_viewed": "owner_buyer_demand_viewed",
}

#: LA RICHIESTA, che non e' un'osservazione.
#:
#: LMC-7 registra cosa il proprietario ha guardato; questo registra cosa ha
#: CHIESTO. Non sta in `ACTIONS` perche' non e' dichiarabile dal client
#: insieme alle altre: ha una rotta sua, che risponde se la richiesta e'
#: stata accettata invece di rispondere sempre 204. Il tipo di evento e'
#: comunque deciso qui, server-side, come tutti gli altri.
CONSULTATION_REQUESTED = "owner_consultation_requested"

#: LMC-10 - L'AGGIORNAMENTO DEI DATI DELLA CASA.
#:
#: Non sta in `ACTIONS` per la stessa ragione della richiesta: non e'
#: dichiarabile dal client. Nasce da un fatto che il server ha appena
#: compiuto - una riga scritta in `owner_home_overrides` - e un portale che
#: potesse dichiararlo potrebbe far comparire nel radar un aggiornamento che
#: non e' mai avvenuto.
HOME_UPDATED = "owner_home_updated"

EVENT_TYPES = frozenset({HOME_VIEWED, CONSULTATION_REQUESTED, HOME_UPDATED,
                         *ACTIONS.values()})

#: Quale capability del read-model autorizza quale azione. Il server la
#: ricontrolla per conto suo: un client che dichiara di aver guardato
#: l'andamento di una casa che non ha uno storico non registra niente.
ACTION_CAPABILITY = {
    "value_history_viewed": "valuation_history",
    "buyer_demand_viewed": "buyer_demand",
}


def event_payload(event_type: str) -> dict[str, str]:
    """Il payload: una chiave, e quella chiave non e' un dato personale.

    `stima_id`, `contact_id` e `lead_id` sono gia' COLONNE dell'evento, e
    ripeterli qui dentro significherebbe soltanto avere due posti dove
    guardare. Resta l'azione, che le colonne non dicono.
    """
    return {"action": event_type.replace("owner_", "", 1)}


def _utc_day(when: datetime) -> str:
    return when.astimezone(timezone.utc).date().isoformat()


def idempotency_key(event_type: str, *, owner_account_id: int, stima_id: int,
                    when: datetime) -> str:
    """`owner_portal:{tipo}:owner:{id}:stima:{id}:day:{YYYY-MM-DD}` in UTC.

    Nessun dato personale: due identificativi interni e una data. La chiave
    finisce in una colonna che un operatore puo' leggere, quindi non deve
    poter raccontare niente di chi c'e' dietro.
    """
    return (f"{EVENT_SOURCE}:{event_type}:owner:{owner_account_id}"
            f":stima:{stima_id}:day:{_utc_day(when)}")


class _OwnerScope:
    """L'agenzia derivata dal grant, nella forma che il dominio si aspetta.

    Non un `OperatorContext`: dietro c'e' un proprietario, non un operatore.
    Espone solo `require_agency`, che e' tutto cio' che
    `record_event_scoped` chiede a uno scope. L'agenzia non arriva mai dal
    client: la ricava `home_tracking_context` dalla catena
    account -> contatto -> agenzia, con la stima nello stesso tenant.
    """

    __slots__ = ("_agency_id",)

    def __init__(self, agency_id: int) -> None:
        self._agency_id = agency_id

    def require_agency(self) -> int:
        return self._agency_id


def _record(contesto: dict[str, Any], *, event_type: str, owner_account_id: int,
            stima_id: int, when: datetime | None = None) -> bool:
    """Scrive l'evento. Torna True se ha scritto o riletto.

    Non gestisce le eccezioni: lo fa `_fail_open`, che avvolge l'intero
    percorso. Averle raccolte qui avrebbe lasciato scoperta la lettura del
    contesto, che e' la parte che tocca il database per prima.

    La scopatura sta a monte (`contesto` esiste solo se il grant e' valido) e
    dentro `record_event_scoped`, che verifica che OGNI riferimento
    appartenga a quell'agenzia prima di inserire.
    """
    quando = when or datetime.now(timezone.utc)
    seller_intelligence.record_event_scoped(
        _OwnerScope(contesto["agency_id"]),
        event_type=event_type,
        event_source=EVENT_SOURCE,
        stima_id=stima_id,
        contact_id=contesto.get("contact_id"),
        lead_id=contesto.get("lead_id"),
        occurred_at=quando,
        payload=event_payload(event_type),
        idempotency_key=idempotency_key(event_type,
                                        owner_account_id=owner_account_id,
                                        stima_id=stima_id, when=quando),
    )
    return True


def _fail_open(event_type: str, stima_id: int, azione):
    """IL CONFINE. Nessuna eccezione esce di qui, qualunque cosa succeda.

    Avvolge TUTTO il percorso - la lettura del contesto, il controllo della
    capability e la scrittura - e non solo la scrittura. La lettura del
    contesto e' la prima cosa che tocca il database, quindi e' anche la
    prima che puo' fallire: lasciandola fuori, un guasto li' sarebbe
    arrivato fino alla pagina del proprietario, che e' esattamente cio' che
    questo confine esiste per impedire. (Lo dimostra un test che fa
    sollevare Seller Intelligence e verifica che `get_home` risponda lo
    stesso.)

    Il log non porta identificativi personali: qui si diagnostica un guasto,
    non si ricostruisce chi stava guardando cosa.
    """
    try:
        return azione()
    except Exception as exc:  # noqa: BLE001 - il tracciamento non nega la pagina
        logger.error("owner_tracking_failed event_type=%s stima_id=%s error_type=%s",
                     event_type, stima_id, type(exc).__name__)
        return False


def track_home_viewed(owner_account_id: int, stima_id: int, *,
                      when: datetime | None = None) -> bool:
    """L'apertura della scheda. Chiamata dal read-model, mai dal client.

    Se il grant non c'e' piu' fra la lettura e questa chiamata, non si
    registra niente e non si solleva: il proprietario ha comunque appena
    visto la pagina.
    """
    def _esegui():
        contesto = owner_repository.home_tracking_context(owner_account_id, stima_id)
        if contesto is None:
            return False
        return _record(contesto, event_type=HOME_VIEWED,
                       owner_account_id=owner_account_id, stima_id=stima_id, when=when)

    return _fail_open(HOME_VIEWED, stima_id, _esegui)


def track_home_updated(owner_account_id: int, stima_id: int, *,
                       when: datetime | None = None) -> bool:
    """LMC-10: il proprietario ha corretto i dati della sua casa.

    Chiamata SOLO dopo una scrittura riuscita, dal servizio di aggiornamento,
    e mai dalla rotta: l'evento afferma che qualcosa e' cambiato in tabella,
    e affermarlo prima di averlo verificato significherebbe metterlo nella
    timeline di vendita anche quando la scrittura e' fallita o non ha
    cambiato niente.

    FAIL-OPEN, E QUI LA SCELTA VA DETTA. In LMC-9 il fail-open sarebbe stato
    una bugia, perche' l'evento ERA la richiesta: perderlo voleva dire
    perderla. Qui no: l'aggiornamento della casa e' la riga di
    `owner_home_overrides`, gia' scritta e gia' al sicuro quando si arriva
    fin qui. Cio' che si rischia di perdere e' il SEGNALE per l'operatore,
    non il dato del proprietario - e rifiutare un aggiornamento salvato
    perche' Seller Intelligence e' giu' sarebbe il danno maggiore.

    Un evento al giorno UTC per proprietario e casa, come tutti gli altri:
    tre correzioni in un pomeriggio sono un pomeriggio.
    """
    def _esegui():
        contesto = owner_repository.home_tracking_context(owner_account_id, stima_id)
        if contesto is None:
            return False
        return _record(contesto, event_type=HOME_UPDATED,
                       owner_account_id=owner_account_id, stima_id=stima_id, when=when)

    return _fail_open(HOME_UPDATED, stima_id, _esegui)


def track_action(owner_account_id: int, stima_id: int, action: str, *,
                 when: datetime | None = None) -> bool:
    """Un'azione esplicita del proprietario.

    Tre cancelli, in quest'ordine: l'azione deve stare nell'insieme chiuso,
    il grant deve essere valido (e da li' arriva il tenant), e la capability
    deve essere vera DAVVERO - controllata sui dati, non sulla parola del
    client. Un "ho guardato l'andamento" su una casa senza storico non
    registra niente: sarebbe un segnale che descrive qualcosa che non e'
    successo.
    """
    event_type = ACTIONS.get(action)
    if event_type is None:
        return False

    def _esegui():
        contesto = owner_repository.home_tracking_context(owner_account_id, stima_id)
        if contesto is None:
            return False
        if not _capability_reale(contesto["agency_id"], stima_id, action):
            return False
        return _record(contesto, event_type=event_type,
                       owner_account_id=owner_account_id, stima_id=stima_id, when=when)

    return _fail_open(event_type, stima_id, _esegui)


def _capability_reale(agency_id: int, stima_id: int, action: str) -> bool:
    """La stessa verita' che il read-model mostra, riletta dai dati.

    Si riusano le funzioni di LMC-2/3/4 invece di ricalcolarle: se un giorno
    la regola di "storico disponibile" cambiasse, cambierebbe in un posto
    solo e questo cancello la seguirebbe.
    """
    from . import demand, home_service

    try:
        if action == "value_history_viewed":
            watch = owner_repository.home_watch_summary(agency_id, stima_id)
            if watch is None:
                return False
            osservazioni = watch.get("observations") or []
            return any(o.get("observation_type") in home_service.VALUATION_HISTORY_OBSERVATIONS
                       for o in osservazioni)
        if action == "buyer_demand_viewed":
            rilevazione = owner_repository.home_buyer_pressure(agency_id, stima_id)
            return demand.build(rilevazione)["status"] != demand.STATUS_UNAVAILABLE
    except Exception as exc:  # noqa: BLE001 - un guasto non diventa un segnale
        # Ridondante rispetto a `_fail_open`, e voluto: qui l'esito e' "non
        # registrare", non "fallire", e distinguere i due casi nel log dice
        # se il problema e' la capability o la scrittura.
        logger.error("owner_tracking_capability_failed action=%s stima_id=%s "
                     "error_type=%s", action, stima_id, type(exc).__name__)
    return False


# ---------------------------------------------------------------------------
# LMC-9 - LA RICHIESTA. Tutto il contrario del fail-open qui sopra.
#
# Gli eventi di LMC-7 sono osservazioni: se la scrittura fallisce non e'
# successo niente di importante, il proprietario ha visto la pagina comunque.
# Questa e' una persona che ha detto "chiamatemi". Se la perdiamo e le
# rispondiamo "fatto", aspettera' una telefonata che nessuno fara': un
# fallimento silenzioso qui non e' una metrica mancata, e' una bugia.
#
# Percio' questa funzione NON ingoia niente. Chi la chiama - la rotta -
# traduce l'assenza di accesso in un 404 neutro e un guasto in un 503, e le
# due cose restano distinte perche' una si riprova e l'altra no.
# ---------------------------------------------------------------------------

class ConsultationNotAllowed(Exception):
    """Nessun accesso valido a questa stima per questo proprietario.

    Una sola eccezione per tutti i casi - stima inesistente, di un altro
    proprietario, di un'altra agenzia, grant revocato o scaduto - perche'
    distinguerli darebbe a chi prova gli id un modo per sapere quali stime
    esistono.
    """


def track_consultation_request(owner_account_id: int, stima_id: int, *,
                               when: datetime | None = None) -> dict[str, Any]:
    """Registra la richiesta, oppure solleva. Mai una via di mezzo.

    `ConsultationNotAllowed` quando il grant non c'e'; qualunque altra
    eccezione - database giu', Seller Intelligence rotta - risale al
    chiamante cosi' com'e'. Non si passa da `safe_record_event` proprio per
    questo: quel wrapper esiste per il funnel pubblico, dove ingoiare e'
    corretto, e qui sarebbe il modo piu' rapido di perdere una richiesta
    senza che nessuno se ne accorga.

    Idempotente come il resto: stesso proprietario, stessa casa, stesso
    giorno UTC danno una riga sola. Chi tocca due volte ha chiesto una cosa
    sola, e il giorno dopo puo' chiedere di nuovo - quella e' una richiesta
    nuova, non un doppione.
    """
    contesto = owner_repository.home_tracking_context(owner_account_id, stima_id)
    if contesto is None:
        raise ConsultationNotAllowed("nessun accesso valido a questa stima")

    quando = when or datetime.now(timezone.utc)
    seller_intelligence.record_event_scoped(
        _OwnerScope(contesto["agency_id"]),
        event_type=CONSULTATION_REQUESTED,
        event_source=EVENT_SOURCE,
        stima_id=stima_id,
        contact_id=contesto.get("contact_id"),
        lead_id=contesto.get("lead_id"),
        occurred_at=quando,
        payload=event_payload(CONSULTATION_REQUESTED),
        idempotency_key=idempotency_key(CONSULTATION_REQUESTED,
                                        owner_account_id=owner_account_id,
                                        stima_id=stima_id, when=quando),
    )
    # Un esito solo, e di proposito. `record_event_scoped` rilegge la riga
    # esistente quando la chiave e' gia' stata usata, quindi arrivati qui la
    # richiesta ESISTE - se fosse nuova o gia' registrata e' una distinzione
    # che interessa a noi, non a chi ha chiesto di essere richiamato, e
    # dedurla confrontando timestamp sarebbe indovinare.
    return {"status": "recorded"}
