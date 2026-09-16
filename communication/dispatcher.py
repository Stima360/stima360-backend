"""P29-2.4 - il dispatcher: claim, gate del consenso, provider, esito.

L'UNICO FILE DEL DOMINIO CHE PUO' IMPORTARE UN PROVIDER

E' la prima delle tre sentinelle del design (§12.2), ed e' strutturale: un
service che potesse importare un provider potrebbe mandare un messaggio senza
passare da qui, cioe' senza passare dal gate del consenso.

IL GATE STA FRA IL CLAIM E IL PROVIDER, E NON ALTROVE

Non a `enqueue`. Fra l'accodamento e l'invio possono passare giorni - M3 e' a
distanza dalla stima - e in quei giorni la persona puo' revocare da un link di
disiscrizione. Una decisione presa all'accodamento sarebbe una decisione su
ieri. Qui invece la revoca blocca il messaggio SUCCESSIVO, sempre, senza che
nessuno debba ricordarsi di svuotare la coda.

E' `communication_type` a decidere se il gate si applica: non il canale, non il
`reason_code`, non il template. Un M4 confermato a mano da un operatore resta
marketing e resta soggetto alla revoca; la mail di servizio con il PDF non lo e'
mai, perche' esegue una richiesta dell'interessato.

LA TRANSAZIONE DEL CLAIM SI CHIUDE PRIMA DELLA RETE

Il claim committa, e SOLO DOPO si chiama il provider. Non e' un dettaglio di
prestazioni: tenere aperta una transazione durante una chiamata di rete
significa tenere un lock per tutta la durata di quella chiamata, e con un
provider lento un batch da dieci diventa una coda seriale con dieci lock vivi.

IN P29-2.4 IL PROVIDER E' FINTO

Non c'e' rete, non c'e' SMTP, non c'e' Meta. Il percorso completo esiste ed e'
provato; il trasporto reale arriva in P29-2.5 e si innestera' qui senza che
questo file cambi forma.
"""

from __future__ import annotations

import logging
from typing import Any

from consent.exceptions import NotFoundError as ConsentNotFoundError
from consent.guard import can_send_marketing
from operator_auth.context import SystemAgencyContext

from . import service
from .enums import (ERROR_OUTCOME_UNKNOWN, ERROR_PROVIDER_REJECTED, ERROR_UNKNOWN,
                    TYPE_MARKETING)
from .providers import null as provider_finto
from .providers.base import OUTCOME_ACCEPTED, OUTCOME_REJECTED

logger = logging.getLogger(__name__)

#: L'origin che entitola il dispatcher a uno scope di sistema. Decisione D1 del
#: design, approvata con il vincolo di agency scope.
DISPATCH_ORIGIN = "communication_dispatch"

#: La ragione con cui si sopprime un messaggio il cui contatto non esiste piu'
#: nello scope. Appartiene all'insieme chiuso delle ragioni della guardia.
REASON_CONTATTO_ASSENTE = "deny_never_given"


def contesto_di_sistema(ctx_operatore) -> SystemAgencyContext:
    """Lo scope su cui gira il dispatcher, derivato dalla sessione autenticata.

    L'agenzia viene da `require_agency()` dell'operatore che ha invocato il
    dispatcher - cioe' dal login - e non dal payload della richiesta. Un
    `agency_id` nel corpo non sarebbe ignorato: lo schema lo rifiuta con 422
    prima di arrivare qui.

    Perche' `SystemAgencyContext` e non l'`OperatorContext` ricevuto: un
    operatore con ruolo `agent` restringe le letture per `assigned_agent_id`, e
    un dispatcher che girasse su quel contesto salterebbe silenziosamente i
    messaggi dei contatti non assegnati a lui. Non un errore, un buco.
    `SystemAgencyContext` esprime "questa agenzia" e nient'altro:
    `is_platform_admin` e' un attributo di classe, non un campo, quindi non e'
    costruibile piu' largo.
    """
    return SystemAgencyContext(
        agency_id=ctx_operatore.require_agency(), origin=DISPATCH_ORIGIN
    )


def _consenso_nega(ctx, message: dict[str, Any]) -> str | None:
    """La ragione per cui questo messaggio non deve partire, o None.

    Solo il marketing passa da qui. Il servizio esegue una richiesta
    dell'interessato e non ha un consenso da interrogare: interrogarlo lo stesso
    significherebbe poter bloccare la mail con il PDF della stima a chi quella
    stima l'ha appena chiesta.

    Un contatto sparito dallo scope fra l'accodamento e adesso fa sollevare
    `NotFoundError` alla guardia. Non e' un errore del dispatcher: e' un
    messaggio che non ha piu' un destinatario interrogabile, e fail closed
    significa sopprimerlo invece di mandarlo.
    """
    if message["communication_type"] != TYPE_MARKETING:
        return None
    try:
        decisione = can_send_marketing(ctx, message["contact_id"])
    except ConsentNotFoundError:
        return REASON_CONTATTO_ASSENTE
    return None if decisione.allowed else decisione.reason


def _esito_del_provider(risultato, capabilities) -> tuple[str, dict[str, Any]]:
    """Traduce cio' che il trasporto ha osservato in una finalizzazione.

    `unknown` -> `indeterminate`, sempre. Ma anche `rejected` diventa
    `indeterminate` quando il trasporto NON sa distinguere gli esiti: un
    `False` di `database.invia_mail` non e' un rifiuto certo, e' l'unica cosa
    che quella funzione sa dire. Mapparlo su `failed` sarebbe una bugia che
    autorizza un retry.

    La regola vive qui e non nell'adapter: e' il dispatcher a decidere cosa
    scrivere nel ledger, e lo decide da cio' che la capability DICHIARA.
    """
    if risultato.outcome == OUTCOME_ACCEPTED:
        return "sent", {"provider_message_id": risultato.provider_message_id}
    if risultato.outcome == OUTCOME_REJECTED and capabilities.distinguishes_failure_class:
        return "failed", {
            "error_code": risultato.error_code or ERROR_PROVIDER_REJECTED,
            "error_detail": risultato.error_detail,
        }
    return "indeterminate", {
        "error_code": risultato.error_code or ERROR_UNKNOWN,
        "error_detail": risultato.error_detail,
    }


def _esito_di_una_eccezione(exc: BaseException) -> tuple[str, dict[str, Any]]:
    """Un adapter che ha sollevato non ha DIMOSTRATO di non aver mandato.

    C22, ramo B. E' la rete di sicurezza del batch, non la politica normale:
    quella e' il ramo A - l'adapter normalizza da se' gli errori operativi
    prevedibili in un `ProviderResult`. Qui si arriva solo quando quel contratto
    viene violato, o quando succede qualcosa che nessuno aveva previsto.

    PERCHE' `indeterminate` E NON `failed`

    La sequenza "richiesta partita -> messaggio inviato -> connessione caduta
    prima della risposta" produce esattamente un'eccezione, e in quella
    sequenza il messaggio E' PARTITO. Scrivere `failed` significherebbe
    dichiarare una certezza che non abbiamo, e `failed` e' lo stato che §7.3
    autorizza a rientrare in coda: una bugia qui diventa un doppio invio dopo.
    `indeterminate` non rientra mai in coda automaticamente - e' il punto di C1.

    `outcome_unknown` ESISTE GIA'

    E' uno degli otto codici gia' ammessi dal CHECK della 064, ed e' lo stesso
    che la recovery degli stale scrive: "il tentativo si e' chiuso senza che
    sapessimo com'e' andata" descrive tutti e due i casi. Nessuna migration,
    nessun codice nuovo.

    IN `error_detail` VA LA CLASSE, NON IL MESSAGGIO

    Il testo di un'eccezione di rete contiene spesso il destinatario - un
    indirizzo email, un numero di telefono - e il ledger non e' il posto dove
    duplicarlo. La classe qualificata identifica il guasto e resta stabile; il
    traceback completo va nel log, dove serve a chi indaga e non in una riga che
    resta per anni.
    """
    return "indeterminate", {
        "error_code": ERROR_OUTCOME_UNKNOWN,
        "error_detail": f"{type(exc).__module__}.{type(exc).__qualname__}",
    }


def dispatch_batch(ctx_operatore, *, limit: int = service.DEFAULT_CLAIM_BATCH,
                   provider=provider_finto) -> dict[str, Any]:
    """Un giro di dispatch. Restituisce il conteggio di cio' che e' successo.

    L'ordine e' quello del design, e ogni passo ha il suo confine:

        1. il claim committa                 (transazione breve, nessuna rete)
        2. per ogni messaggio reclamato:
             a. il gate del consenso         (solo marketing)
             b. il provider                  (fuori da ogni transazione)
             c. la finalizzazione            (compare-and-set con il token)

    Una soppressione non chiama il provider e non lo chiamera' mai: e'
    terminale. Una finalizzazione che trova l'ownership persa non solleva -
    registra il risultato tardivo e il batch continua con gli altri messaggi,
    che e' il comportamento che il design chiede esplicitamente.
    """
    ctx = contesto_di_sistema(ctx_operatore)
    reclamati = service.claim_due(ctx, provider=provider.NAME, limit=limit)

    conteggi = {"claimed": len(reclamati), "sent": 0, "suppressed": 0,
                "failed": 0, "indeterminate": 0, "lost": 0}

    for preso in reclamati:
        message = preso["message"]
        token = message["claim_token"]

        ragione = _consenso_nega(ctx, message)
        if ragione is not None:
            esito = service.finalize_suppressed(ctx, message["id"], token, reason=ragione)
            conteggi["suppressed" if esito is not None else "lost"] += 1
            continue

        # Fuori da ogni transazione: il claim ha gia' committato.
        #
        # Il try avvolge LA SOLA CHIAMATA AL PROVIDER, non la finalizzazione:
        # un'eccezione del nostro codice di scrittura non deve essere scambiata
        # per un guasto del trasporto e sepolta come `outcome_unknown`. E' una
        # barriera di isolamento del batch, non un silenziatore.
        #
        # `Exception` e non `BaseException`: un KeyboardInterrupt o un
        # SystemExit devono continuare a fermare il processo.
        try:
            risultato = provider.send(message)
        except Exception as exc:  # noqa: BLE001 - vedi _esito_di_una_eccezione
            logger.exception(
                "provider raised during dispatch: message_id=%s agency_id=%s "
                "provider=%s", message["id"], ctx.agency_id, provider.NAME,
            )
            stato, campi = _esito_di_una_eccezione(exc)
        else:
            stato, campi = _esito_del_provider(risultato, provider.CAPABILITIES)

        finalizza = {
            "sent": service.finalize_sent,
            "failed": service.finalize_failed,
            "indeterminate": service.finalize_indeterminate,
        }[stato]
        esito = finalizza(ctx, message["id"], token, **campi)
        conteggi[stato if esito is not None else "lost"] += 1

    return conteggi
