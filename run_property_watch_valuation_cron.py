"""LMC-11 - il giro periodico del valore. IN-PROCESS, nessun accesso HTTP.

COSA FA, IN UNA RIGA

Per ogni agenzia attiva, per ogni watch attivo, ricalcola il valore con il
motore ufficiale e registra uno snapshot reale. Da li' il proprietario vede
`current_value` aggiornato e lo storico crescere.

PERCHE' IN-PROCESS E NON VIA API

Stessa ragione di `run_flow_p2b_cron.py`, che e' il modello: P26-5 ha reso le
rotte session-only, quindi un runner HTTP avrebbe bisogno di un'identita'
tecnica da custodire e ruotare. Le funzioni applicative sono gia' per-agenzia
e gia' certificate: non c'e' niente da autenticare perche' non c'e' nessuna
rete di mezzo.

LE DUE MODALITA' SONO ENTRAMBE ESPLICITE

    python run_property_watch_valuation_cron.py --agency-id 1
    python run_property_watch_valuation_cron.py --all-agencies

Senza argomenti non parte. Un default implicito qui sceglierebbe quali tenant
vengono rivalutati, ed e' una decisione che deve stare nel comando.

A PAGINE, FINO IN FONDO

`PROPERTY_WATCH_VALUATION_LIMIT` e' la DIMENSIONE DI PAGINA e non un tetto:
ogni agenzia viene percorsa per intero, leggendo N watch per volta con un
cursore sull'id (keyset, non OFFSET). Con 700 watch e pagine da 500 il giro
ne rivaluta 700, in due pagine.

IL LOCK, E PERCHE' DI SESSIONE

Un solo giro per volta. Il lock e' `pg_try_advisory_lock` tenuto da una
connessione dedicata per tutta la durata (vedi
`property_watch.database.advisory_job_lock`): i lock transazionali usati
altrove nel repository cadrebbero alla prima commit, cioe' dopo il primo
watch. Se il lock non si ottiene, il giro esce con `another_run_active` e
codice 0 - non e' un guasto, e' l'altra esecuzione che sta lavorando, e un
exit non-zero farebbe suonare un allarme ogni volta che due schedulazioni si
sovrappongono.

COSA QUESTO RUNNER NON FA

Non ritenta, non dorme, non cicla: un giro, un esito, un codice di uscita.
Non costruisce payload e non tocca l'idempotenza - passa da
`refresh_valuation_snapshots_for_*`, che passa da LMC-3. Non manda niente a
nessuno: nessun evento Seller Intelligence, nessun task, nessuna email,
nessun WhatsApp, nessun enqueue P29, nessuna notifica al proprietario. LMC-11
produce dati di valutazione e nient'altro.

I TRE CODICI DI USCITA

    0   il giro e' andato e nessun watch e' fallito. Include il caso
        `another_run_active` e il caso "nessun watch da rivalutare": una lista
        vuota non e' un guasto.
    1   guasto TECNICO: configurazione non valida, database irraggiungibile,
        nessuna agenzia attiva. Il giro non e' avvenuto.
    2   guasto APPLICATIVO: il giro e' andato ma almeno un watch e' fallito.
        `skipped` NON e' qui: e' uno stato lecito (vedi
        `VALUATION_CYCLE_STATUSES`), e trasformarlo in allarme renderebbe
        rosso un cron che funziona.

La differenza fra 1 e 2 e' operativa: l'1 si guarda nella piattaforma, il 2
nei log del giro.
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
    """Cosa elaborare e con quali limiti. Nessuna credenziale, nessun URL."""

    all_agencies: bool
    agency_id: int | None
    page_size: int


#: LA DIMENSIONE DI PAGINA, non un tetto giornaliero.
#:
#: Il giro percorre ogni agenzia FINO IN FONDO, a pagine di questa
#: dimensione: con 700 watch attivi e pagine da 500, il giro fa due pagine e
#: li rivaluta tutti e 700. Questo numero dice quante righe si leggono per
#: volta, e niente altro.
#:
#: Prima non era cosi', ed era un difetto: la pagina era l'unica, quindi i
#: watch oltre il cinquecentesimo non venivano rivalutati mai. Il nome della
#: variabile d'ambiente resta `PROPERTY_WATCH_VALUATION_LIMIT` per non
#: cambiare una configurazione gia' scritta altrove, ma il significato e'
#: questo.
PAGE_SIZE_DEFAULT = 500
PAGE_SIZE_MASSIMA = 5000

#: Alias di compatibilita': il nome vecchio restava leggibile in giro.
LIMIT_DEFAULT = PAGE_SIZE_DEFAULT
LIMIT_MASSIMO = PAGE_SIZE_MASSIMA


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
        prog="run_property_watch_valuation_cron",
        description="Ricalcolo periodico del valore delle case PRE-INCARICO, "
                    "in-process, per agenzia.")
    gruppo = parser.add_mutually_exclusive_group(required=True)
    gruppo.add_argument("--agency-id", type=int,
                        help="elabora SOLO questa agenzia, se attiva")
    gruppo.add_argument("--all-agencies", action="store_true",
                        help="elabora ogni agenzia attiva, una alla volta")
    try:
        argomenti = parser.parse_args(argv)
    except SystemExit:
        raise ConfigurationError("mode") from None
    if argomenti.agency_id is not None and argomenti.agency_id < 1:
        raise ConfigurationError("--agency-id")
    return Config(
        all_agencies=bool(argomenti.all_agencies),
        agency_id=argomenti.agency_id,
        # Il nome resta quello, il significato e' "dimensione di pagina".
        page_size=_integer("PROPERTY_WATCH_VALUATION_LIMIT", PAGE_SIZE_DEFAULT),
    )


#: I campi che il log puo' portare. ELENCO CHIUSO, ed e' il punto: un log di
#: cron finisce in posti che non controlliamo. Qui ci sono identificativi
#: interni e conteggi, e non c'e' modo di far passare un indirizzo, una email,
#: un telefono, un token o un payload - nemmeno per sbaglio, perche' una
#: chiave non prevista non viene stampata.
CAMPI_LOG = ("agencies", "processed", "created", "reused", "skipped", "failed")


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
    """Le agenzie attive, dal wrapper gia' approvato. Nessuna query nuova."""
    from property_watch import repository as pw_repository

    return pw_repository.list_active_agency_ids()


def run_cycle(config: Config) -> dict:
    """Il giro vero, gia' dentro il lock. Ritorna il riepilogo."""
    from property_watch import service as pw_service

    if config.all_agencies:
        return pw_service.refresh_valuation_snapshots_for_all_agencies(
            page_size=config.page_size)
    esito = pw_service.refresh_valuation_snapshots_for_agency(
        config.agency_id, page_size=config.page_size)
    return {"agencies": 1,
            **{chiave: esito[chiave] for chiave in
               ("processed", "created", "reused", "skipped", "failed")},
            "runs": [esito]}


def main(argv=None) -> int:
    try:
        config = load_config(argv)
    except ConfigurationError:
        _log("config", "failed", 0, "configuration")
        return 1

    from property_watch.database import (VALUATION_CRON_LOCK_SCOPE,
                                         advisory_job_lock)

    try:
        attive = _active_agency_ids()
    except Exception as exc:  # noqa: BLE001 - guasto tecnico, non applicativo
        _log("agencies", "failed", 0, type(exc).__name__)
        return 1

    if config.all_agencies:
        if not attive:
            _log("agencies", "failed", 0, "nessuna_agenzia_attiva")
            return 1
    elif config.agency_id not in attive:
        # Attiva PRIMA di elaborarla: un'agenzia sospesa non e' un'agenzia con
        # zero watch, e trattarla come tale la farebbe risultare elaborata con
        # successo.
        _log("agencies", "failed", 0, "agenzia_non_attiva")
        return 1

    avvio = time.monotonic()
    try:
        with advisory_job_lock(VALUATION_CRON_LOCK_SCOPE) as ottenuto:
            if not ottenuto:
                _log("runner", "another_run_active", 0)
                return 0
            _log("runner", "started", 0)
            riepilogo = run_cycle(config)
    except Exception as exc:  # noqa: BLE001 - guasto tecnico del giro
        _log("runner", "failed", int((time.monotonic() - avvio) * 1000),
             type(exc).__name__)
        return 1

    durata = int((time.monotonic() - avvio) * 1000)
    fallito = int(riepilogo.get("failed", 0)) > 0
    _log("runner", "failed" if fallito else "completed", durata, counts=riepilogo)
    return 2 if fallito else 0


if __name__ == "__main__":
    raise SystemExit(main())
