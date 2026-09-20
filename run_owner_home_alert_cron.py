"""LMC-12 - il giro delle notifiche PRE-INCARICO. IN-PROCESS, nessun HTTP.

COSA FA, IN UNA RIGA

Per ogni agenzia attiva, per ogni casa con un accesso proprietario attivo,
confronta gli snapshot e le rilevazioni gia' scritti e, SOLO se c'e' un
cambiamento che supera le soglie di LMC-12, scrive una notifica in-app in
`owner_home_notifications`. Niente altro esce da qui: nessuna email, nessun
WhatsApp, nessun SMS, nessun enqueue P29, nessun task, nessun follow-up.

PERCHE' IN-PROCESS, E PERCHE' DOPO LMC-11

Stesso modello di `run_property_watch_valuation_cron.py`: le funzioni sono
per-agenzia e certificate, non c'e' rete di mezzo. Va schedulato DOPO il
giro del valore (LMC-11): questo giro confronta cio' che trova, e se gira
prima trova il candidato di ieri. Non e' un errore, e' un giorno di ritardo -
ma e' inutile.

LE DUE MODALITA' SONO ENTRAMBE ESPLICITE

    python run_owner_home_alert_cron.py --agency-id 1
    python run_owner_home_alert_cron.py --all-agencies

Senza argomenti non parte.

A PAGINE, FINO IN FONDO

`OWNER_HOME_ALERT_PAGE_SIZE` e' la DIMENSIONE DI PAGINA, non un tetto: ogni
agenzia viene percorsa per intero, N grant per volta con un cursore sull'id
(keyset, non OFFSET).

IL LOCK

Un solo giro per volta, con un advisory lock di sessione dal choke point gia'
certificato (`property_watch.database.advisory_job_lock`) ma con uno SCOPE
SUO, `owner:home_alerts`: LMC-11 e LMC-12 non si escludono a vicenda e
nessuno dei due tocca il lock dell'altro. Lock occupato = `another_run_active`
e codice 0.

I TRE CODICI DI USCITA

    0   il giro e' andato e nessuna casa e' fallita (incluso "nessun grant"
        e `another_run_active`).
    1   guasto TECNICO: configurazione, database irraggiungibile, nessuna
        agenzia attiva. Il giro non e' avvenuto.
    2   guasto APPLICATIVO: almeno una casa e' fallita. `suppressed` e
        `skipped` NON sono qui: sono esiti leciti.
"""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    all_agencies: bool
    agency_id: int | None
    page_size: int


PAGE_SIZE_DEFAULT = 500
PAGE_SIZE_MASSIMA = 5000


def _integer(name: str, default: int, minimum: int = 1,
             maximum: int = PAGE_SIZE_MASSIMA) -> int:
    grezzo = os.getenv(name, str(default))
    try:
        valore = int(grezzo)
    except (TypeError, ValueError):
        raise ConfigurationError(name) from None
    if valore < minimum or valore > maximum:
        raise ConfigurationError(name)
    return valore


def load_config(argv=None) -> Config:
    parser = argparse.ArgumentParser(
        prog="run_owner_home_alert_cron",
        description="Notifiche in-app PRE-INCARICO 'La Mia Casa', in-process, per agenzia.")
    gruppo = parser.add_mutually_exclusive_group(required=True)
    gruppo.add_argument("--agency-id", type=int, help="elabora SOLO questa agenzia, se attiva")
    gruppo.add_argument("--all-agencies", action="store_true",
                        help="elabora ogni agenzia attiva, una alla volta")
    try:
        argomenti = parser.parse_args(argv)
    except SystemExit:
        raise ConfigurationError("mode") from None
    if argomenti.agency_id is not None and argomenti.agency_id < 1:
        raise ConfigurationError("--agency-id")
    return Config(all_agencies=bool(argomenti.all_agencies), agency_id=argomenti.agency_id,
                  page_size=_integer("OWNER_HOME_ALERT_PAGE_SIZE", PAGE_SIZE_DEFAULT))


#: I campi che il log puo' portare. ELENCO CHIUSO: identificativi interni e
#: conteggi, mai un indirizzo, un'email, un telefono, un titolo o un corpo.
CAMPI_LOG = ("agencies", "processed", "created", "reused", "suppressed", "skipped", "failed")


def _log(phase: str, status: str, duration_ms: int, reason: str | None = None,
         counts: dict | None = None) -> None:
    campi = [f"phase={phase}", f"status={status}"]
    for chiave in CAMPI_LOG:
        if counts and chiave in counts:
            campi.append(f"{chiave}={counts[chiave]}")
    campi.append(f"duration_ms={duration_ms}")
    if reason:
        campi.append(f"reason={reason}")
    print(" ".join(campi), flush=True)


def _active_agency_ids():
    from property_watch import repository as pw_repository

    return pw_repository.list_active_agency_ids()


def run_cycle(config: Config) -> dict:
    from owner import home_alert_service

    if config.all_agencies:
        return home_alert_service.run_for_all_agencies(page_size=config.page_size)
    esito = home_alert_service.run_for_agency(config.agency_id, page_size=config.page_size)
    return {"agencies": 1,
            **{chiave: esito[chiave] for chiave in
               ("processed", *home_alert_service.CYCLE_STATUSES)},
            "runs": [esito]}


def main(argv=None) -> int:
    try:
        config = load_config(argv)
    except ConfigurationError:
        _log("config", "failed", 0, "configuration")
        return 1

    from owner.home_alert_service import HOME_ALERT_LOCK_SCOPE
    from property_watch.database import advisory_job_lock

    try:
        attive = _active_agency_ids()
    except Exception as exc:  # noqa: BLE001 - guasto tecnico
        _log("agencies", "failed", 0, type(exc).__name__)
        return 1

    if config.all_agencies:
        if not attive:
            _log("agencies", "failed", 0, "nessuna_agenzia_attiva")
            return 1
    elif config.agency_id not in attive:
        _log("agencies", "failed", 0, "agenzia_non_attiva")
        return 1

    avvio = time.monotonic()
    try:
        with advisory_job_lock(HOME_ALERT_LOCK_SCOPE) as ottenuto:
            if not ottenuto:
                _log("runner", "another_run_active", 0)
                return 0
            _log("runner", "started", 0)
            riepilogo = run_cycle(config)
    except Exception as exc:  # noqa: BLE001 - guasto tecnico del giro
        _log("runner", "failed", int((time.monotonic() - avvio) * 1000), type(exc).__name__)
        return 1

    durata = int((time.monotonic() - avvio) * 1000)
    fallito = int(riepilogo.get("failed", 0)) > 0
    _log("runner", "failed" if fallito else "completed", durata, counts=riepilogo)
    return 2 if fallito else 0


if __name__ == "__main__":
    raise SystemExit(main())
