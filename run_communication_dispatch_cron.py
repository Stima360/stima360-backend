"""P29-2.6E cron runner per il dispatch delle comunicazioni. Scheduling remains external.

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
    2   guasto APPLICATIVO: il giro e' andato, ma almeno un messaggio non e'
        partito. Vedi `CONTEGGI_DI_GUASTO`.

La differenza fra 1 e 2 e' operativa e non estetica: l'1 si guarda nella
piattaforma (credenziali, rete, servizio giu'), il 2 nel ledger.
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
    )


#: I conteggi che `dispatch_batch` restituisce. Elencati per NOME: un giro che
#: ne restituisse uno diverso e' un contratto cambiato, e va visto subito.
CONTEGGI = ("claimed", "sent", "suppressed", "failed", "indeterminate", "lost")


def _log(status: str, duration_ms: int, reason: str | None = None,
         counts: dict | None = None, channel: str | None = None) -> None:
    """Una riga sola, a campi. Nessuna credenziale, nessun cookie, nessun
    indirizzo: un log di cron finisce in posti che non controlliamo."""
    fields = [f"status={status}"]
    if channel:
        fields.append(f"channel={channel}")
    for key in CONTEGGI:
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


def _application_failure(data: dict) -> bool:
    """True quando almeno un messaggio NON e' partito e il giro non puo' dirsi
    riuscito. Vedi `CONTEGGI_DI_GUASTO`.

    `claimed == 0` non e' un guasto: una coda vuota e' il caso normale di un
    cron che gira ogni ora.
    """
    return any(int(data.get(chiave, 0) or 0) > 0 for chiave in CONTEGGI_DI_GUASTO)


def run_once(config: Config, sessione: requests.Session | None = None) -> dict:
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
