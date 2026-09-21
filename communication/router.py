"""La rotta di dispatch. DICHIARATA in P29-2.4, MONTATA in P29-2.6E.

PERCHE' NON ERA IN main.py, E PERCHE' ADESSO C'E'

In P29-2.4 il provider era finto: la rotta non avrebbe mandato niente a
nessuno, e una rotta viva che non fa nulla e' una rotta che qualcuno un giorno
chiama credendo che faccia qualcosa. Con l'adapter email reale (P29-2.5E) e un
cron che la chiama (P29-2.6E), la ragione per tenerla fuori e' venuta meno.

LO SCOPE VIENE DALLA SESSIONE, IL PERMESSO DALLA MATRICE

`require_dispatch_context` (P29-2.6E) e' `legacy_basic_agency_context` piu' una
riga: l'agenzia esce dalla sessione autenticata e da nient'altro (P26-5:
nessun fallback, nessun Basic), e il chiamante deve essere fra quelli che
vedono TUTTI i record dell'agenzia - perche' e' su tutti che il giro agisce.
Un `agent` prende 403. Vedi `communication/dependencies.py` per il perche'.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from core.exceptions import PermissionDenied
from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import contact_view
from . import dispatcher
from . import service as dispatcher_service
from . import journey_catalog
from . import journey_service
from . import journey_tick
from .dependencies import require_dispatch_context
from .exceptions import ConflictError, NotFoundError, ValidationError
from .schemas import DispatchRequest, JourneyTickRequest, ManualMessageRequest

router = APIRouter(prefix="/api/communication", tags=["communication"])


@router.post("/dispatch")
def dispatch(
    payload: DispatchRequest,
    ctx: OperatorContext = Depends(require_dispatch_context),
):
    """Un giro di dispatch per l'agenzia della sessione, su UN canale.

    403 a chi non vede tutti i record dell'agenzia - un `agent` non li vede, e
    un giro di dispatch li tocca tutti.

    403 anche se il contesto non e' vincolato a un'agenzia: un platform admin
    senza membership non ha un'agenzia per cui dispacciare, e indovinarne una
    sarebbe esattamente il dispatcher cross-tenant che il design vieta.
    """
    try:
        return dispatcher.dispatch_batch(
            ctx,
            channel=payload.channel,
            # Il trasporto si risolve DAL CANALE, esplicitamente: il default di
            # `dispatch_batch` e' il provider finto, e una rotta montata che ci
            # cadesse sopra direbbe `sent` senza aver mandato niente.
            provider=dispatcher.adapter_per(payload.channel),
            limit=payload.limit,
        )
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValidationError as exc:
        # Un canale senza trasporto reale: `whatsapp` finche' P29-2.5W e'
        # deferita e R3 e' OPEN. 501 e non 422 perche' la richiesta e' ben
        # formata - e' il server a non avere ancora quel pezzo.
        raise HTTPException(status_code=501, detail=str(exc)) from exc


# ===========================================================================
# P29-3C - LE ROTTE DELLE JOURNEY
#
# Tre atti, e tre soglie diverse, prese dalla matrice di P26-1 invece che
# inventate qui:
#
#   tick           agisce su TUTTE le iscrizioni dell'agenzia, quindi chiede
#                  la stessa riga del dispatch - "vede tutti i record" - e un
#                  `agent` prende 403. E' la stessa dipendenza, riusata: due
#                  soglie separate per la stessa capacita' diventerebbero
#                  prima o poi due soglie diverse.
#   send-current   agisce su UNA iscrizione, quindi basta una sessione: il
#                  restringimento dell'agente ai propri contatti lo applica
#                  `contact_in_scope` dentro il service, che e' dove quella
#                  regola vive gia' (core.scope).
#   skip-current   idem.
#
# In tutte e tre l'agenzia viene da `ctx.require_agency()`: dalla membership
# o dall'acting di P28, mai dal corpo. Un platform admin senza agenzia
# vincolata prende 403 da `PlatformAdminAgencyRequired`, come altrove.
#
# 503 E NON 500 QUANDO LA 071 NON C'E'. Il codice puo' arrivare in TEST prima
# della migration: in quella finestra queste rotte - e SOLO queste - devono
# dire "questa funzione non e' ancora migrata". Le rotte P29 esistenti non
# passano di qui e non cambiano comportamento.
# ===========================================================================

#: Il corpo della risposta quando lo schema non c'e' ancora. Un codice
#: MACCHINA, perche' un client non deve leggere una frase per decidere.
FEATURE_NOT_MIGRATED = "feature_not_migrated"


def _tradotto(azione):
    """Le eccezioni di dominio, tradotte una volta sola."""
    try:
        return azione()
    except journey_tick.FeatureNotMigrated as exc:
        raise HTTPException(status_code=503,
                            detail={"code": FEATURE_NOT_MIGRATED, "message": str(exc)}) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Risorsa non trovata") from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/journeys/tick")
def journeys_tick(
    payload: JourneyTickRequest | None = None,
    ctx: OperatorContext = Depends(require_dispatch_context),
):
    """Un giro del motore per l'agenzia della sessione.

    NON la chiama nessun cron: il collegamento con il dispatch si fa dopo che
    la 071 sara' applicata su TEST. Finche' nessuna journey e' `active`, un
    giro non fa nulla e lo dice con dei conteggi a zero.
    """
    limite = payload.limit if payload else journey_tick.LIMITE_PREDEFINITO
    return _tradotto(lambda: journey_tick.tick(ctx, limit=limite))


@router.post("/journeys/enrollments/{enrollment_id}/send-current")
def journeys_send_current(
    enrollment_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    """L'operatore manda adesso il passo assistito in attesa. Doppio click: una riga sola."""
    esito = _tradotto(lambda: journey_tick.send_current(ctx, enrollment_id))
    if esito["stopped"] is not None:
        # L'iscrizione si e' fermata mentre il click arrivava: lo stop e' GIA'
        # committato (il service torna invece di sollevare, perche' una
        # eccezione lo annullerebbe), e all'operatore si dice perche' la mail
        # non e' partita.
        raise HTTPException(status_code=409, detail={
            "code": "enrollment_stopped", "stop_reason": esito["stopped"],
            "message": f"enrollment {enrollment_id} was stopped ({esito['stopped']}): "
                       "the step was not sent"})
    return {"created": esito["created"], "message_id": esito["message"]["id"],
            "status": esito["message"]["status"], "enrollment": esito["enrollment"]}


@router.post("/journeys/enrollments/{enrollment_id}/skip-current")
def journeys_skip_current(
    enrollment_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    """L'operatore salta il passo assistito in attesa. Il tempo del passo dopo riparte da adesso."""
    esito = _tradotto(lambda: journey_tick.skip_current(ctx, enrollment_id))
    return {"skipped_step_no": esito["skipped_step_no"], "completed": esito["completed"],
            "enrollment": esito["enrollment"]}


# ===========================================================================
# P29-3D - LA SUPERFICIE DEL CONTACT 360
#
# DUE SOGLIE, E NESSUNA TERZA.
#
#   Cio' che riguarda UN CONTATTO - leggerne lo storico, annullare un suo
#   messaggio, mettere in pausa la sua automazione - chiede una sessione, e
#   il restringimento dell'agente ai propri contatti lo applica `core.scope`
#   dentro il dominio, dove quella regola vive gia'. Un agente sui contatti
#   che gli sono assegnati lavora; su quelli di un collega riceve 404, che e'
#   la stessa risposta che riceverebbe per un contatto inesistente.
#
#   Cio' che riguarda L'INTERA AGENZIA - creare la sequenza, accenderla,
#   ritirarla - chiede la riga di matrice del dispatch ("vede tutti i
#   record"), perche' accendere una journey decide cosa ricevera' OGNI
#   contatto dell'agenzia. Un agente prende 403.
#
# NESSUN `agency_id` NEL CORPO, in nessuna di queste rotte: viene dalla
# sessione o dall'acting, come ovunque in P29.
# ===========================================================================

@router.get("/contacts/{contact_id}/messages")
def contact_messages(
    contact_id: int,
    limit: int = 50,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    """Lo storico delle comunicazioni di un contatto, gia' tradotto.

    Funziona anche senza la 071: il ledger esiste dalla 064, e una scheda
    contatto non deve rompersi perche' una migration non e' ancora passata.
    """
    return _tradotto(lambda: contact_view.messages(ctx, contact_id, limit=limit))


@router.get("/contacts/{contact_id}/journey")
def contact_journey(
    contact_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    """La card dell'automazione. `available: false` se la 071 non c'e'."""
    return _tradotto(lambda: contact_view.journey(ctx, contact_id))


@router.post("/contacts/{contact_id}/messages")
def contact_send_manual(
    contact_id: int,
    payload: ManualMessageRequest,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    """Un messaggio scritto da una persona. Il destinatario lo decide il
    contatto, non il client; il consenso non si aggira."""
    esito = _tradotto(lambda: contact_view.send_manual(
        ctx, contact_id, subject=payload.subject, body=payload.body,
        communication_type=payload.communication_type))
    return {"created": esito["created"], "message_id": esito["message"]["id"],
            "status": esito["message"]["status"]}


@router.post("/contacts/{contact_id}/automation/pause")
def contact_automation_pause(
    contact_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    """Ferma le automazioni di QUESTO contatto. I messaggi manuali restano."""
    return _tradotto(lambda: journey_service.pause_automations(ctx, contact_id))


@router.post("/contacts/{contact_id}/automation/resume")
def contact_automation_resume(
    contact_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    return _tradotto(lambda: journey_service.resume_automations(ctx, contact_id))


@router.post("/messages/{message_id}/cancel")
def message_cancel(
    message_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    """Ritira dalla coda un messaggio non ancora partito."""
    esito = _tradotto(lambda: dispatcher_service.cancel(ctx, message_id))
    return {"message_id": esito["id"], "status": esito["status"]}


@router.post("/messages/{message_id}/send-now")
def message_send_now(
    message_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    """Anticipa a subito un messaggio in coda. NON spedisce: sposta la data,
    e il dispatcher resta l'unico che parla con un provider."""
    esito = _tradotto(lambda: dispatcher_service.send_now(ctx, message_id))
    return {"message_id": esito["id"], "status": esito["status"],
            "scheduled_at": esito["scheduled_at"]}


@router.post("/journeys/enrollments/{enrollment_id}/pause")
def enrollment_pause(
    enrollment_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    return _tradotto(lambda: journey_service.pause_enrollment(ctx, enrollment_id))


@router.post("/journeys/enrollments/{enrollment_id}/resume")
def enrollment_resume(
    enrollment_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    return _tradotto(lambda: journey_service.resume_enrollment(ctx, enrollment_id))


@router.post("/journeys/enrollments/{enrollment_id}/stop")
def enrollment_stop(
    enrollment_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    """Interrompe l'iscrizione per decisione di una persona: `operator`, con
    il suo nome nel registro."""
    return _tradotto(lambda: journey_service.stop_enrollment(ctx, enrollment_id))


@router.post("/journeys/stima-lead/provision")
def provision_stima_lead(
    ctx: OperatorContext = Depends(require_dispatch_context),
):
    """Crea la sequenza della stima v1 in BOZZA. Idempotente, e non accende
    niente: l'attivazione e' un secondo gesto, esplicito."""
    esito = _tradotto(lambda: journey_catalog.ensure_stima_lead_v1(ctx))
    return {"created": esito["created"], "journey": esito["journey"]}


@router.post("/journeys/{journey_id}/activate")
def journey_activate(
    journey_id: int,
    ctx: OperatorContext = Depends(require_dispatch_context),
):
    """Accende una journey. Da questo istante in avanti: le stime spedite
    PRIMA non producono iscrizioni (P29-3A.1 SS C)."""
    return _tradotto(lambda: journey_service.activate_journey(ctx, journey_id))


@router.post("/journeys/{journey_id}/retire")
def journey_retire(
    journey_id: int,
    ctx: OperatorContext = Depends(require_dispatch_context),
):
    """Spegne una journey: non nascono piu' iscrizioni. Quelle gia' aperte
    proseguono sulla versione a cui sono nate, salvo una condizione di stop."""
    return _tradotto(lambda: journey_service.retire_journey(ctx, journey_id))
