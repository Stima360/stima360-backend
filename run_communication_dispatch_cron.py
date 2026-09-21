"""P29-2.6E cron runner per il dispatch delle comunicazioni. Scheduling remains external.

P29-3E: LO STESSO CRON FA ANCHE GIRARE IL MOTORE DELLE JOURNEY

Un secondo runner sarebbe stato piu' facile da scrivere e peggio da gestire:
due cron da configurare per ogni agenzia, due credenziali, due allarmi, e
soprattutto nessuna garanzia sull'ORDINE. Cio' che il tick mette in coda deve
poter partire nello stesso giro, altrimenti M1 aspetta il giro dopo senza
ragione. Quindi: TICK PRIMA, DISPATCH POI, nello stesso login.

Il runner resta cio' che era: un CLIENT HTTP. Non importa il dominio, non
apre connessioni, non conosce una journey - chiama una rotta che esiste gia'
(P29-3C) e legge dei conteggi. E non provisiona e non attiva NIENTE: la
sequenza `stima_lead` si accende a mano, una volta, da un amministratore.

PERCHE' NON E' UNA COPIA DI `run_followup_p18d_cron.py`

Quel runner e' il modello che il design indica (§8.3), e la sua FORMA e' quella
giusta: nessuno scheduler interno, un endpoint autenticato, un cron esterno che
lo chiama. Ma il suo canale di AUTENTICAZIONE non esiste piu': manda
`auth=(ADMIN_USER, ADMIN_PASS)`, e dopo P26-5 `legacy_basic_agency_context`
risolve lo scope dalla sessione e da nient'altro - una richiesta con il solo
Basic prende 401, come fissa
`tests/test_final_release_security_blockers.py`.

Questo runner apre quindi una SESSIONE:

    POST /api/operator-auth/login     -> 204 + Set-Cookie
    POST /api/communication/dispatch  -> con quel cookie
    POST /api/operator-auth/logout    -> sempre, anche dopo un errore

Login a ogni giro invece di tenere un cookie vivo: le sessioni hanno un idle di
quattro ore e un tetto massimo, e un cron che dorme piu' a lungo dell'idle si
troverebbe un 401 intermittente - il genere di guasto che si manifesta di notte
e non si riproduce di giorno. Un login costa una richiesta.

Il logout e' nel `finally` perche' una sessione abbandonata a ogni giro e' una
riga viva in piu' ogni volta: in un mese di esecuzioni orarie sono centinaia di
sessioni che nessuno chiude.

UN CRON PER AGENZIA, E UN CRON PER CANALE

L'agenzia viene dalla sessione - mai dal payload, che un `agency_id` lo rifiuta
con 422 - e il canale dal corpo. Con N agenzie servono N cron, come gia' oggi
per P18-D2; e finche' l'unico adapter reale e' quello email, il canale e'
`email`. Il giorno in cui P29-2.5W sara' sbloccata (R3), sara' un secondo cron
con `COMMUNICATION_DISPATCH_CHANNEL=whatsapp`, non una riga in piu' qui.

L'ACCOUNT E' UN `agency_admin`, E NON PIU' IN BASSO

`COMMUNICATION_DISPATCH_EMAIL` deve appartenere a un operatore con ruolo
`agency_admin`: il MINIMO che la rotta autorizza.

Una prima stesura diceva `agent`, e funzionava - il dispatcher non gira sul
contesto di chi lo invoca ma su un `SystemAgencyContext` costruito dalla sua
sola agenzia (D1), quindi il restringimento per `assigned_agent_id` non lo
toccava. Funzionava, ed era proprio il problema: se la rotta ammette `agent`,
quella capacita' ce l'ha OGNI agente umano dell'agenzia, e un account dedicato
non cambia niente. La rotta adesso chiede la riga "See all agency records"
della matrice P26-1 - YES per owner e admin, NO per agent - perche' un giro di
dispatch agisce su tutti i record dell'agenzia. Vedi
`communication/dependencies.py`.

`agency_admin` e non `agency_owner`: fra i due ammessi si sceglie il minore.

COSA QUESTO RUNNER NON FA

Non ritenta, non dorme, non cicla. Un giro, un esito, un codice di uscita. La
ripetizione e' del cron; i tentativi di invio sono `attempt_count` sulla riga,
e li governa il dominio.

I TRE CODICI DI USCITA

    0   la chiamata HTTP e' riuscita E nessun messaggio e' rimasto indietro:
        failed == 0, indeterminate == 0, lost == 0. Una coda vuota
        (`claimed == 0`) e' exit 0: non c'era niente da mandare.
    1   guasto TECNICO del giro: configurazione, login, rete, timeout,
        protocollo. Il dispatch non e' avvenuto, o non si sa se sia avvenuto.
    2   guasto APPLICATIVO: il giro e' andato, ma qualcosa non e' passato -
        almeno un messaggio non e' partito (`CONTEGGI_DI_GUASTO`), oppure il
        tick delle journey non e' riuscito. Il dispatch e' avvenuto lo stesso.

La differenza fra 1 e 2 e' operativa e non estetica: l'1 si guarda nella
piattaforma (credenziali, rete, servizio giu'), il 2 nel ledger e nel motore.

IL TICK NON PUO' TOGLIERE AL CRON LA CAPACITA' DI SPEDIRE

Un guasto del motore delle journey e' un guasto del motore delle journey. Se
diventasse un `return 1` prima del dispatch, un bug nel tick bloccherebbe
anche i messaggi che sono GIA' in coda - compresi quelli scritti a mano da
una persona, che con le journey non c'entrano niente. Quindi: qualunque cosa
faccia il tick, il dispatch parte; se il tick non e' riuscito, il giro finisce
2 e lo dice nel log, ma le email in coda sono partite.

L'unica eccezione e' il login: senza sessione non si fa nemmeno il tick, e il
giro e' 1 prima di cominciare.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests


class ConfigurationError(ValueError):
    pass


class TechnicalError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    base_url: str
    email: str
    password: str
    channel: str
    limit: int
    timeout: tuple[float, float]
    #: Quante iscrizioni al massimo puo' toccare UN giro del motore. Non e'
    #: `limit`: quello conta messaggi da spedire, questo conta iscrizioni da
    #: far avanzare, e i due numeri non hanno ragione di coincidere. Il
    #: default e' quello della rotta; l'intervallo lo pretende lo schema
    #: `JourneyTickRequest`, e qui si rifiuta prima di partire invece di
    #: scoprirlo con un 422 a meta' giro.
    journey_limit: int = 500


#: I canali che il dominio conosce. Tenuti qui come letterali e non importati da
#: `communication.enums`: questo runner e' un CLIENT HTTP e non deve importare
#: il dominio - girerebbe altrove, con un database che non e' il suo.
CANALI = ("email", "whatsapp")


def _integer(name: str, default: int, minimum: int = 1, maximum: int = 50) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ConfigurationError(name) from None
    if value < minimum or value > maximum:
        raise ConfigurationError(name)
    return value


def _seconds(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ConfigurationError(name) from None
    if value <= 0:
        raise ConfigurationError(name)
    return value


def load_config() -> Config:
    base_url = (os.getenv("COMMUNICATION_DISPATCH_BASE_URL") or "").strip().rstrip("/")
    email = os.getenv("COMMUNICATION_DISPATCH_EMAIL") or ""
    password = os.getenv("COMMUNICATION_DISPATCH_PASSWORD") or ""
    channel = (os.getenv("COMMUNICATION_DISPATCH_CHANNEL") or "email").strip()

    parsed = urlparse(base_url)
    if not base_url or parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigurationError("COMMUNICATION_DISPATCH_BASE_URL")
    if not email or not password:
        raise ConfigurationError("COMMUNICATION_DISPATCH credentials")
    if channel not in CANALI:
        raise ConfigurationError("COMMUNICATION_DISPATCH_CHANNEL")

    return Config(
        base_url,
        email,
        password,
        channel,
        _integer("COMMUNICATION_DISPATCH_LIMIT", 10),
        (
            _seconds("COMMUNICATION_CONNECT_TIMEOUT_SECONDS", 5),
            _seconds("COMMUNICATION_READ_TIMEOUT_SECONDS", 120),
        ),
        _integer("COMMUNICATION_JOURNEY_TICK_LIMIT", 500, minimum=1, maximum=2000),
    )


#: I conteggi che `dispatch_batch` restituisce. Elencati per NOME: un giro che
#: ne restituisse uno diverso e' un contratto cambiato, e va visto subito.
CONTEGGI = ("claimed", "sent", "suppressed", "failed", "indeterminate", "lost")


#: I conteggi che `journeys/tick` restituisce (P29-3C). Stessa regola dei
#: precedenti: elencati per NOME, perche' un giro che ne restituisse uno
#: diverso e' un contratto cambiato e va visto subito.
CONTEGGI_TICK = ("stopped", "advanced", "completed",
                 "enrolled_active", "enrolled_stopped", "enrolled_skipped",
                 "queued", "queued_idempotent", "awaiting_operator", "errors")


def _log(status: str, duration_ms: int, reason: str | None = None,
         counts: dict | None = None, channel: str | None = None,
         phase: str | None = None, nomi: tuple[str, ...] = CONTEGGI) -> None:
    """Una riga sola, a campi. Nessuna credenziale, nessun cookie, nessun
    indirizzo: un log di cron finisce in posti che non controlliamo.

    P29-3E: `phase` distingue le DUE righe che un giro stampa adesso - una
    per il tick, una per il dispatch - e `nomi` dice quali conteggi ci si
    aspetta in questa fase. Senza `phase` la riga e' quella del dispatch, e
    resta identica a com'era: i cruscotti che la leggono non cambiano.
    """
    fields = [f"status={status}"]
    if phase:
        fields.append(f"phase={phase}")
    if channel:
        fields.append(f"channel={channel}")
    for key in nomi:
        if counts and key in counts:
            fields.append(f"{key}={counts[key]}")
    fields.append(f"duration_ms={duration_ms}")
    if reason:
        fields.append(f"reason={reason}")
    print(" ".join(fields), flush=True)


#: I conteggi che rendono un giro un INSUCCESSO APPLICATIVO (exit 2).
#:
#: `failed`        - l'invio e' stato rifiutato dal trasporto. Il ledger lo
#:                   racconta, ma il cron NON deve dire "verde": una casella
#:                   SMTP scaduta produce failed a ogni giro, e un exit 0
#:                   lascerebbe Render verde mentre non parte una email.
#: `indeterminate` - l'esito non si sa (C22: `error_code='outcome_unknown'`).
#:                   "Non lo so" non e' "e' andata bene".
#: `lost`          - la finalizzazione ha mancato il compare-and-set: il
#:                   messaggio esiste, l'esito no.
#:
#: `suppressed` NON e' qui, ed e' una scelta: e' l'esito LEGITTIMO del gate di
#: consenso - un contatto che ha revocato il consenso marketing produce
#: `suppressed` per sempre, e trasformarlo in un allarme renderebbe rosso un
#: cron che sta funzionando esattamente come deve.
CONTEGGI_DI_GUASTO = ("failed", "indeterminate", "lost")


#: Il codice macchina che la rotta usa quando la 071 non c'e' (P29-3C). Il
#: runner lo riconosce invece di leggere una frase: una frase cambia.
NON_MIGRATA = "feature_not_migrated"

#: Gli esiti possibili del tick dentro un giro. `not_migrated` e' uno stato
#: ATTESO finche' la 071 non e' applicata ovunque, e non e' un guasto; dopo,
#: non dovrebbe comparire mai piu', ed e' la riga da cercare nei log.
TICK_COMPLETATO = "completed"
TICK_NON_MIGRATA = "not_migrated"
TICK_FALLITO = "failed"


def _journey_failure(data: dict) -> bool:
    """True quando il motore delle journey non ha fatto il suo giro.

    Due casi, e nessuno dei due impedisce al dispatch di partire:

    `journey_status == failed`  il tick non e' arrivato in fondo - rete,
                                timeout, 4xx, 5xx, contratto cambiato.
    `journey_errors > 0`        il tick e' arrivato in fondo, ma almeno una
                                iscrizione e' morta nel suo savepoint. Come
                                `failed` per il dispatch: il ledger lo sa, e
                                un exit 0 farebbe si' che non lo guardi
                                nessuno.

    Assente vuol dire "nessun tick in questo giro" e non e' un guasto: un
    dizionario di soli conteggi di dispatch resta valido.
    """
    if data.get("journey_status") == TICK_FALLITO:
        return True
    return int(data.get("journey_errors", 0) or 0) > 0


def _application_failure(data: dict) -> bool:
    """True quando almeno un messaggio NON e' partito e il giro non puo' dirsi
    riuscito. Vedi `CONTEGGI_DI_GUASTO`.

    `claimed == 0` non e' un guasto: una coda vuota e' il caso normale di un
    cron che gira ogni ora.

    P29-3E: un tick che non e' riuscito conta come guasto applicativo. Il
    dispatch e' avvenuto lo stesso - questo predicato si legge DOPO - ma il
    giro non puo' dirsi verde se il motore non ha girato.
    """
    if any(int(data.get(chiave, 0) or 0) > 0 for chiave in CONTEGGI_DI_GUASTO):
        return True
    return _journey_failure(data)


def _codice_di_errore(risposta) -> str | None:
    """Il codice macchina dentro un corpo di errore, se c'e'.

    La rotta risponde `{"detail": {"code": ..., "message": ...}}`. Un corpo
    diverso - una pagina HTML di un proxy, un 503 del load balancer - non ha
    un `detail` e non ha un codice: si restituisce None, e il chiamante lo
    tratta come un guasto qualunque, che e' cio' che e'.
    """
    try:
        corpo = risposta.json()
    except Exception:  # noqa: BLE001 - un corpo illeggibile non e' un codice
        return None
    dettaglio = corpo.get("detail") if isinstance(corpo, dict) else None
    if isinstance(dettaglio, dict):
        codice = dettaglio.get("code")
        return codice if isinstance(codice, str) else None
    return None


def _journey_tick(config: Config, sessione) -> dict:
    """UN giro del motore. Non solleva mai, e non ritenta mai.

    NON SOLLEVA perche' il dispatch deve partire comunque: l'esito torna come
    dato, e chi legge decide. Nemmeno un guasto che qui non e' previsto -
    un trasporto che alza un'eccezione di un'altra libreria, un corpo di una
    forma che nessuno si aspettava - deve poter togliere al giro la capacita'
    di spedire cio' che e' gia' in coda. Finisce in `unexpected`, che nel log
    si vede e nel codice di uscita pesa come qualunque altro guasto del tick.

    NON RITENTA perche' un tick e' una SCRITTURA - iscrizioni create, passi
    accodati - e riprovarlo dentro lo stesso giro significherebbe farlo
    girare due volte su una risposta persa. Il motore e' idempotente e
    sopravviverebbe, ma la ripetizione e' del cron: fra un'ora c'e' il giro
    dopo.

    Restituisce `{"status": ..., "counts": dict | None, "reason": str | None}`.
    """
    iniziato = time.monotonic()

    def esito(stato, reason=None, counts=None):
        _log("completed" if stato == TICK_COMPLETATO else
             "skipped" if stato == TICK_NON_MIGRATA else "failed",
             int((time.monotonic() - iniziato) * 1000), reason,
             counts=counts, phase="journey_tick", nomi=CONTEGGI_TICK)
        return {"status": stato, "counts": counts, "reason": reason}

    try:
        try:
            risposta = sessione.post(
                f"{config.base_url}/api/communication/journeys/tick",
                json={"limit": config.journey_limit},
                timeout=config.timeout,
            )
        except requests.Timeout:
            return esito(TICK_FALLITO, "timeout")
        except requests.RequestException:
            return esito(TICK_FALLITO, "http_or_network")

        # Lo stato si LEGGE invece di farsi sollevare da `raise_for_status`:
        # quel metodo alza l'eccezione della libreria che ha fatto la
        # richiesta, e questa funzione ha promesso di non sollevare niente.
        stato = getattr(risposta, "status_code", None)
        if not isinstance(stato, int):
            return esito(TICK_FALLITO, "invalid_response")

        if stato == 503 and _codice_di_errore(risposta) == NON_MIGRATA:
            # La 071 non c'e' ancora su questo ambiente. E' lo stato previsto
            # dal deploy-prima-della-migration, non un guasto: si passa
            # oltre, e il log lo dice con una parola che si puo' cercare.
            return esito(TICK_NON_MIGRATA, NON_MIGRATA)
        if stato >= 400:
            return esito(TICK_FALLITO, f"http_{stato}")

        try:
            dati = risposta.json()
        except ValueError:
            return esito(TICK_FALLITO, "invalid_json")
        if not isinstance(dati, dict) or any(
                type(dati.get(chiave)) is not int or dati[chiave] < 0
                for chiave in CONTEGGI_TICK):
            return esito(TICK_FALLITO, "invalid_json")

        return esito(TICK_COMPLETATO, counts=dati)
    except Exception:  # noqa: BLE001 - vedi la docstring: l'isolamento e' il punto
        return esito(TICK_FALLITO, "unexpected")


def run_once(config: Config, sessione: requests.Session | None = None) -> dict:
    """UN giro intero: login, tick, dispatch, logout.

    L'ordine non e' un dettaglio. Il tick sta PRIMA perche' cio' che mette in
    coda deve poter partire in questo stesso giro; sta DOPO il login perche'
    la rotta vuole una sessione; e il logout sta nel `finally` perche' vale
    anche quando il resto e' andato male.

    Restituisce i conteggi del dispatch - il contratto di prima, invariato -
    con accanto `journey_status` e, se il tick e' arrivato in fondo,
    `journey_errors` e `journey_queued`.
    """
    started = time.monotonic()
    propria = sessione is None
    sessione = sessione or requests.Session()

    try:
        try:
            accesso = sessione.post(
                f"{config.base_url}/api/operator-auth/login",
                json={"email": config.email, "password": config.password},
                timeout=config.timeout,
            )
            accesso.raise_for_status()
        except requests.RequestException:
            _log("failed", int((time.monotonic() - started) * 1000), "login",
                 channel=config.channel)
            raise TechnicalError("login") from None

        # IL TICK PRIMA DEL DISPATCH, nello stesso login: cio' che il motore
        # mette in coda adesso puo' partire in questo stesso giro. Non
        # solleva: qualunque cosa risponda, il dispatch va fatto.
        journey = _journey_tick(config, sessione)

        try:
            risposta = sessione.post(
                f"{config.base_url}/api/communication/dispatch",
                json={"channel": config.channel, "limit": config.limit},
                timeout=config.timeout,
            )
            risposta.raise_for_status()
            dati = risposta.json()
            if not isinstance(dati, dict):
                raise TechnicalError("invalid_json")
            for chiave in CONTEGGI:
                if type(dati.get(chiave)) is not int or dati[chiave] < 0:
                    raise TechnicalError("invalid_json")
        except requests.Timeout:
            _log("failed", int((time.monotonic() - started) * 1000), "timeout",
                 channel=config.channel)
            raise TechnicalError("timeout") from None
        except requests.RequestException:
            _log("failed", int((time.monotonic() - started) * 1000), "http_or_network",
                 channel=config.channel)
            raise TechnicalError("http_or_network") from None
        except (ValueError, TechnicalError):
            _log("failed", int((time.monotonic() - started) * 1000), "invalid_json",
                 channel=config.channel)
            raise TechnicalError("invalid_json") from None

        _log("completed", int((time.monotonic() - started) * 1000), counts=dati,
             channel=config.channel)
        # L'esito del tick viaggia accanto ai conteggi del dispatch, piatto e
        # con un prefisso suo: `main` ne ha bisogno per il codice di uscita, e
        # chi legge la risposta di un giro non deve andarselo a cercare in un
        # dizionario annidato.
        dati["journey_status"] = journey["status"]
        if journey["counts"] is not None:
            dati["journey_errors"] = journey["counts"]["errors"]
            dati["journey_queued"] = journey["counts"]["queued"]
        return dati
    finally:
        # Sempre, anche dopo un errore: una sessione abbandonata a ogni giro
        # e' una riga viva in piu' ogni volta.
        try:
            sessione.post(f"{config.base_url}/api/operator-auth/logout",
                          timeout=config.timeout)
        except requests.RequestException:
            pass
        if propria:
            sessione.close()


def main() -> int:
    try:
        config = load_config()
    except ConfigurationError:
        _log("failed", 0, "configuration")
        return 1

    try:
        risultato = run_once(config)
    except TechnicalError:
        return 1
    if _application_failure(risultato):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
