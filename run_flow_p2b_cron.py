"""Runner FLOW P2B. Esecuzione IN-PROCESS, nessun accesso HTTP.

PERCHE' NON PASSA PIU' DALL'API

Questo runner autenticava con HTTP Basic (ADMIN_USER/ADMIN_PASS) su
`/api/flow/events/recover` e `/api/flow/scan`. P26-5 ha reso quelle route
session-only: l'header Basic non viene piu' letto da nulla, il cron riceveva
401 e usciva 1. Il log diceva `reason=http_or_network` perche'
`requests.HTTPError` e' una sottoclasse di `requests.RequestException` e il
runner aveva un ramo solo - quindi la causa vera restava invisibile.

Le strade erano tre: riaprire Basic (disfa P26-5), dare al cron un'identita'
tecnica con sessione (una credenziale nuova da custodire e ruotare), oppure
non uscire affatto dal processo. E' stata scelta la terza: le funzioni
applicative sono gia' per-agenzia e gia' certificate, quindi non serve ne' un
canale ne' un'identita'. Non c'e' niente da autenticare perche' non c'e'
nessuna rete di mezzo.

LE DUE MODALITA' SONO ENTRAMBE ESPLICITE

    python run_flow_p2b_cron.py --agency-id 1
    python run_flow_p2b_cron.py --all-agencies

Senza argomenti il runner NON parte. Il runner HTTP risolveva implicitamente
l'agenzia Default dal contesto di compatibilita', e con una seconda agenzia
attiva avrebbe continuato a servire solo la prima senza dirlo. Un default
implicito qui sceglie quali tenant vengono elaborati: e' una decisione che
deve stare nel comando, non in una dipendenza.

PER OGNI AGENZIA: RECOVERY, POI SCAN

Nell'ordine, e non e' indifferente: la recovery sblocca gli eventi rimasti in
`received`, e sono proprio quelli che lo scan successivo deve poter valutare.
Invertirli farebbe lavorare lo scan su uno stato ancora incagliato.

UN'AGENZIA NON FERMA LE ALTRE

In `--all-agencies` un errore su A viene registrato e il ciclo prosegue con B:
il contrario significherebbe che un solo tenant rotto sospende la piattaforma.
L'esito complessivo resta pero' non-zero, perche' "abbiamo continuato" non e'
"e' andato bene".
"""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass


class ConfigurationError(ValueError):
    pass


class TechnicalError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    """Cosa elaborare e con quali limiti. Nessuna credenziale, nessun URL."""
    all_agencies: bool
    agency_id: int | None
    recovery_limit: int
    scan_limit: int


def _integer(name, default, minimum=1, maximum=500):
    raw=os.getenv(name,str(default))
    try: value=int(raw)
    except (TypeError,ValueError): raise ConfigurationError(name) from None
    if value<minimum or value>maximum: raise ConfigurationError(name)
    return value


def load_config(argv=None):
    """La configurazione del processo cron, indipendente dal web server.

    Legge solo variabili d'ambiente proprie (i limiti) e gli argomenti della
    riga di comando. Non importa `main`, non apre socket, e non ha bisogno che
    l'applicazione web sia in esecuzione: l'unico presupposto condiviso sono le
    variabili di connessione al database, che i wrapper approvati usano gia'.
    """
    parser=argparse.ArgumentParser(
        prog='run_flow_p2b_cron',
        description='Ciclo FLOW (recovery + scan) in-process, per agenzia.')
    gruppo=parser.add_mutually_exclusive_group(required=True)
    gruppo.add_argument('--agency-id',type=int,
                        help="elabora SOLO questa agenzia, se attiva")
    gruppo.add_argument('--all-agencies',action='store_true',
                        help='elabora ogni agenzia attiva, una alla volta')
    try:
        arguments=parser.parse_args(argv)
    except SystemExit:
        # argparse esce da solo; qui serve l'errore tipizzato del modulo, cosi'
        # che `main` logghi `phase=config` come per ogni altra configurazione
        # invalida invece di morire senza una riga di log.
        raise ConfigurationError('mode') from None
    if arguments.agency_id is not None and arguments.agency_id < 1:
        raise ConfigurationError('--agency-id')
    return Config(
        all_agencies=bool(arguments.all_agencies),
        agency_id=arguments.agency_id,
        recovery_limit=_integer('FLOW_RECOVERY_LIMIT',100),
        scan_limit=_integer('FLOW_SCAN_LIMIT',100),
    )


def _log(phase,status,duration_ms,reason=None,counts=None):
    fields=[f"phase={phase}",f"status={status}"]
    for key in ('requested_limit','processed','ignored','failed','busy','successes','failures','skips'):
        if counts and key in counts: fields.append(f"{key}={counts[key]}")
    fields.append(f"duration_ms={duration_ms}")
    if reason: fields.append(f"reason={reason}")
    print(' '.join(fields),flush=True)


def _application_failure(data):
    return data.get('status') not in ('completed','success') or bool(data.get('failed',0)) or bool(data.get('failures',0)) or bool(data.get('busy',0))


# ---------------------------------------------------------------------------
# IL CICLO, E DOVE VIVE LO SCOPE
#
# `recover_received_events_for_agency` e `scan_for_agency` portano l'agency_id
# fin dentro la query: il filtro sta nella SELECT che decide QUALI righe sono
# eleggibili, non in un `if` a valle. I gemelli senza agenzia
# (`recover_received_events`, `scan` senza `agency_id`) esistono ancora per la
# compatibilita' del router, e questo runner non li chiama mai - lo fissa un
# test, perche' una chiamata al gemello sbagliato sarebbe una passata
# cross-tenant che nessun errore segnalerebbe.
# ---------------------------------------------------------------------------

def _active_agency_ids():
    """Le agenzie attive, dal wrapper gia' approvato. Nessuna query nuova."""
    from flow import repository as flow_repository

    return flow_repository.list_active_agency_ids()


def run_cycle(agency_id, *, recovery_limit, scan_limit):
    """Un ciclo completo per UNA agenzia: recovery, poi scan.

    Ritorna `(esito, recovery, scan)` dove esito e' 0 se tutto e' andato bene,
    2 se c'e' un problema applicativo, 1 se una delle due fasi ha sollevato.
    Le eccezioni non escono da qui: in modalita' multipla devono fermare questa
    agenzia e non il ciclo.
    """
    from flow import service as flow_service
    from flow.schemas import ScanRequest

    esito = 0
    recovery = scan = None

    started = time.monotonic()
    try:
        recovery = flow_service.recover_received_events_for_agency(
            agency_id, recovery_limit)
        _log(f'recovery.agency_{agency_id}', recovery['status'],
             int((time.monotonic() - started) * 1000), counts=recovery)
        if _application_failure(recovery):
            esito = 2
    except Exception as exc:
        _log(f'recovery.agency_{agency_id}', 'failed',
             int((time.monotonic() - started) * 1000), type(exc).__name__)
        return 1, None, None

    # Lo scan parte solo dopo la recovery: sblocca gli eventi che lo scan deve
    # poter valutare.
    started = time.monotonic()
    try:
        scan = flow_service.scan_for_agency(
            agency_id, ScanRequest(simulation=False, limit=scan_limit))
        _log(f'scan.agency_{agency_id}', scan['status'],
             int((time.monotonic() - started) * 1000), counts=scan)
    except Exception as exc:
        _log(f'scan.agency_{agency_id}', 'failed',
             int((time.monotonic() - started) * 1000), type(exc).__name__)
        return 1, recovery, None

    if _application_failure(scan):
        esito = 2
    # Saturazione: il limite e' stato raggiunto, quindi potrebbe esserci altro
    # da fare che questo ciclo non ha visto. Era gia' cosi' nel runner HTTP.
    if scan.get('processed') == scan.get('requested_limit'):
        _log(f'runner.agency_{agency_id}', 'partial_failure', 0, 'possible_saturation')
        esito = max(esito, 2)
    return esito, recovery, scan


def main(argv=None):
    try:
        config = load_config(argv)
    except ConfigurationError:
        _log('config', 'failed', 0, 'configuration')
        return 1

    try:
        attive = _active_agency_ids()
    except Exception as exc:
        _log('agencies', 'failed', 0, type(exc).__name__)
        return 1

    if config.all_agencies:
        bersagli = list(attive)
        if not bersagli:
            _log('agencies', 'failed', 0, 'nessuna_agenzia_attiva')
            return 1
    else:
        # Attiva PRIMA di elaborarla: un'agenzia sospesa non e' un'agenzia con
        # zero righe da lavorare, e trattarla come tale la farebbe risultare
        # elaborata con successo.
        if config.agency_id not in attive:
            _log('agencies', 'failed', 0, 'agenzia_non_attiva')
            return 1
        bersagli = [config.agency_id]

    _log('runner', 'started', 0, counts={'agencies': len(bersagli)})

    peggiore = 0
    for agency_id in bersagli:
        esito, _recovery, _scan = run_cycle(
            agency_id,
            recovery_limit=config.recovery_limit,
            scan_limit=config.scan_limit,
        )
        # Un errore su A non impedisce B: si prosegue e si tiene il peggiore.
        # 1 (tecnico) prevale su 2 (applicativo), che prevale su 0.
        peggiore = 1 if 1 in (peggiore, esito) else max(peggiore, esito)

    _log('runner', 'completed' if peggiore == 0 else 'failed', 0,
         counts={'agencies': len(bersagli)})
    return peggiore


if __name__=='__main__':
    raise SystemExit(main())
