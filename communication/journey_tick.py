"""P29-3C - il motore delle journey: un giro, quattro fasi, nessuna rete.

COSA FA UN GIRO, E PERCHE' IN QUEST'ORDINE

    A. STOP      i fatti commerciali autorevoli fermano le iscrizioni
    B. ADVANCE   un passo SPEDITO fa nascere il prossimo
    C. ENROLL    una mail di stima SPEDITA fa nascere una iscrizione
    D. DUE       un passo dovuto diventa una riga in coda

L'ordine non e' estetico. STOP viene per primo perche' e' l'unica fase che
TOGLIE: farla dopo significherebbe accodare un messaggio a un contatto che ha
gia' firmato un incarico e poi cancellarlo un istante dopo - e in mezzo il
dispatcher potrebbe averlo spedito. ADVANCE precede DUE perche' il passo
appena maturato deve poter partire nello STESSO giro. ENROLL sta fra i due
perche' una iscrizione nuova ha il diritto di veder partire M1 subito, se M1
e' gia' dovuto.

NON C'E' RETE, E NON C'E' UN PROVIDER

Questo modulo non importa nessun adapter e non chiama nessun invio: produce
righe `queued` nel ledger e si ferma li'. Chi spedisce e' il dispatcher, che
interroga il consenso immediatamente prima - e continua a essere l'unico
posto in cui un messaggio parte. Un motore che spedisse da solo sarebbe un
cron che manda email, cioe' la cosa che il gate di P29-3A ha vietato.

NESSUN LOCK ATTRAVERSA UN'ATTESA. Ogni fase e' una transazione breve; dentro
ciascuna, le righe si prendono con `FOR UPDATE SKIP LOCKED`, cosi' due tick
simultanei si dividono il lavoro invece di aspettarsi. Cio' che rende
IMPOSSIBILE il doppione non e' pero' il lock: sono i vincoli della 071 (la
chiave di idempotenza dell'iscrizione, `(journey_id, trigger_message_id)`,
l'unicita' del passo vivo) e quella del ledger. Il lock serve a non sprecare
lavoro; il database a non sbagliarlo.

UN ERRORE SU UNA ISCRIZIONE NON FERMA LE ALTRE

Ogni iscrizione viene lavorata dentro un SAVEPOINT. Un template che non
esiste, un contatto senza email, una finestra malformata: la riga fallisce,
viene contata e registrata nel log, e il giro prosegue. La cosa che NON si fa
mai e' inventare un messaggio al posto di quello che non si e' potuto
costruire.

IL TICK NON E' UN CRON, ANCORA. Nessun `run_*.py` lo chiama: esiste la rotta,
e il collegamento con il dispatch si fara' dopo che la 071 sara' applicata
su TEST. Finche' nessuna journey e' `active`, un giro non fa assolutamente
nulla - e' il motore acceso in folle.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from consent.guard import can_send_marketing_bulk

from . import journey_repository as repo
from . import send_window
from . import service as communication_service
from . import templates
from . import unsubscribe
from .exceptions import ConflictError, NotFoundError, ValidationError
from .journey_enums import (
    CONSENT_STOP_BY_GUARD_REASON, ENR_ACTIVE, KIND_AWAIT_OPERATOR, KIND_ENQUEUE,
    STOP_ACQUISITION_LINKED, STOP_CONSENT_NOT_GRANTED, STOP_CONSULTATION_REQUESTED,
    STOP_CONTACT_INACTIVE, STOP_INSPECTION, STOP_LEAD_CLOSED, STOP_MANDATE_SIGNED,
    STOP_PRIORITY, TRIGGER_STIMA_PDF_SENT, choose_stop_reason,
)
from .journey_service import cursore, operatore
from .repository import contact_in_scope

logger = logging.getLogger(__name__)

#: Quante righe al massimo una fase tratta in un giro. Un tetto e non una
#: pagina: il giro successivo riprende da dove questo ha smesso, e un tick
#: che prova a svuotare una coda infinita e' un tick che non finisce mai.
LIMITE_PREDEFINITO = 500

#: I tipi di evento della timeline che testimoniano una ragione di stop. La
#: ragione la decide il FATTO (la riga di `stima_acquisitions`, di
#: `stima_inspections`, di `leads`, di `contacts`); l'evento serve solo a
#: dire QUALE riga di storia lo racconta, e finisce in `stop_event_id`.
#: `consultation_requested` e' l'eccezione dichiarata: li' l'evento E' il
#: fatto, perche' la richiesta di consulenza del proprietario non ha un'altra
#: tabella in cui vivere (LMC-9).
EVENTI_DI_STOP: dict[str, tuple[str, ...]] = {
    "mandate_signed": ("mandate_signed",),
    "acquisition_linked": ("acquisition_linked",),
    "inspection": ("inspection_completed", "inspection_scheduled"),
    "consultation_requested": ("owner_consultation_requested",),
}

#: L'evento di timeline dei passi saltati a mano (P29-3C §8).
EVENT_STEP_SKIPPED = "journey_step_skipped"

#: La base degli URL pubblici. Letta a ogni chiamata e non all'import: un
#: test che la cambia deve poterla cambiare davvero.
#:
#: SENZA DEFAULT, DI PROPOSITO. Un link di disiscrizione che punta all'host
#: sbagliato e' peggio di un messaggio non partito: chi clicca crede di
#: essersi disiscritto e non lo e'. Se la variabile non c'e', il passo
#: fallisce, viene contato e registrato, e le altre iscrizioni proseguono.
BASE_ENV = "PUBLIC_BASE_URL"


def _base_pubblica() -> str:
    base = (os.getenv(BASE_ENV) or "").strip()
    if not base:
        raise ValidationError(
            f"{BASE_ENV} is not configured: a marketing message cannot be built "
            "without an unsubscribe link that points somewhere real")
    return base.rstrip("/")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _conteggi() -> dict[str, int]:
    return {
        "stopped": 0, "advanced": 0, "completed": 0,
        "enrolled_active": 0, "enrolled_stopped": 0, "enrolled_skipped": 0,
        "queued": 0, "queued_idempotent": 0, "awaiting_operator": 0, "errors": 0,
    }


class _Savepoint:
    """Un punto di ripresa per UNA iscrizione.

    Senza, il primo errore - un template mancante, un contatto senza email -
    porterebbe in `InFailedSqlTransaction` l'intera transazione della fase, e
    il giro morirebbe sulla prima riga storta invece che su nessuna.

    SI USA SEMPRE COSI', E NON AL CONTRARIO:

        try:
            with _Savepoint(cur, nome):
                ...
        except Exception:
            ...

    L'errore deve ATTRAVERSARE il `with`, perche' e' l'uscita dal blocco a
    fare `ROLLBACK TO SAVEPOINT`. Catturarlo dentro lascerebbe la transazione
    abortita e il `RELEASE SAVEPOINT` fallirebbe a sua volta - che e'
    esattamente il modo in cui questo motore si e' rotto la prima volta che
    due giri concorrenti hanno litigato su una chiave unica.
    """

    def __init__(self, cur, nome: str):
        self.cur, self.nome = cur, nome

    def __enter__(self):
        self.cur.execute(f"SAVEPOINT {self.nome}")
        return self

    def __exit__(self, tipo, valore, _tb):
        if tipo is None:
            self.cur.execute(f"RELEASE SAVEPOINT {self.nome}")
            return False
        self.cur.execute(f"ROLLBACK TO SAVEPOINT {self.nome}")
        return False


# ---------------------------------------------------------------------------
# LO SCHEMA
# ---------------------------------------------------------------------------

class FeatureNotMigrated(ConflictError):
    """Le journey esistono nel codice, non ancora nel database.

    Un errore DICHIARATO e non un incidente: il codice arriva in TEST con il
    deploy, la 071 con un gesto separato, e fra i due momenti le rotte delle
    journey devono dire "non ancora" invece di rompersi. Il router la traduce
    in 503 - un 500 con `UndefinedTable` direbbe la stessa cosa a chi legge i
    log, e niente a chi chiama.
    """


MESSAGGIO_NON_MIGRATO = (
    "journey automation requires migration 071, which is not applied on this database"
)


def _pretendi_schema(cur) -> None:
    if not repo.schema_ready(cur):
        raise FeatureNotMigrated(MESSAGGIO_NON_MIGRATO)


# ---------------------------------------------------------------------------
# IL FENCE, e la decisione presa con le righe in mano
# ---------------------------------------------------------------------------

#: I nomi delle ragioni che la query fresca sa calcolare, passati come
#: parametri perche' il SQL non contenga letterali di dominio.
NOMI_DI_STOP = {
    "mandate_signed": STOP_MANDATE_SIGNED,
    "acquisition_linked": STOP_ACQUISITION_LINKED,
    "inspection": STOP_INSPECTION,
    "consultation_requested": STOP_CONSULTATION_REQUESTED,
    "lead_closed": STOP_LEAD_CLOSED,
    "contact_inactive": STOP_CONTACT_INACTIVE,
}


def _fence_e_stop(cur, ctx, *, contact_id: int, lead_id: int | None, stima_id: int | None,
                  stima_snapshot: int | None):
    """Prende il fence e risponde: c'e' uno stop ADESSO? `(ragione, evento)`.

    L'ordine conta ed e' il contratto della fase:

        1. fence   contatto, lead, stima - in quest'ordine, fino al commit
        2. lettura i fatti in UNA statement, il consenso dalla sua unica
                   implementazione, entrambi con le righe gia' bloccate
        3. ritorno chi chiama scrive NELLA STESSA transazione, e solo il
                   commit rilascia i lock

    Fra il punto 2 e la scrittura non c'e' nessuna finestra: uno stop che
    volesse atterrare deve prima prendere una riga che teniamo noi. E uno
    stop che aveva gia' COMMITTATO prima del punto 1 lo vediamo qui, perche'
    la lettura e' successiva al lock.

    IL CONSENSO NON E' NELLA QUERY, e non e' una dimenticanza: la sua
    decisione vive in `consent.guard` e riscriverla in SQL sarebbe la seconda
    implementazione che il mandato vieta. Si interroga dopo il fence - il
    contatto e' bloccato, e il percorso di scrittura del consenso comincia
    proprio con `lock_contact`, quindi nessuna revoca puo' infilarsi fra
    questa lettura e il nostro commit.
    """
    repo.fence(cur, ctx, contact_id=contact_id, lead_id=lead_id, stima_id=stima_id)
    ragione, evento = repo.fresh_stop(
        cur, ctx, contact_id=contact_id, lead_id=lead_id, stima_id=stima_id,
        stima_snapshot=stima_snapshot, priorita=STOP_PRIORITY,
        tipi_evento=EVENTI_DI_STOP, nomi=NOMI_DI_STOP)
    ragioni = {ragione} if ragione else set()

    decisione = can_send_marketing_bulk(ctx, [contact_id]).get(contact_id)
    if decisione is not None and not decisione.allowed:
        ragioni.add(CONSENT_STOP_BY_GUARD_REASON.get(decisione.reason, STOP_CONSENT_NOT_GRANTED))

    vincente = choose_stop_reason(ragioni)
    if vincente is None:
        return None, None
    # L'evento vale solo se racconta LA ragione che ha vinto: una ragione di
    # consenso non ha un evento di timeline, e attribuirle quello di un altro
    # fatto sarebbe una spiegazione falsa.
    return vincente, (evento if vincente == ragione else None)


# ---------------------------------------------------------------------------
# A - STOP
# ---------------------------------------------------------------------------

def _ragioni_di_stop(cur, ctx, righe: list[dict[str, Any]]):
    """`(ragioni per iscrizione, eventi per stima)`, con una query per famiglia.

    Il risultato NON dipende dall'ordine: ogni famiglia e' un insieme, la
    scelta finale e' `choose_stop_reason`, che legge una priorita' fissa.
    """
    snapshot = sorted({r["stima_id_snapshot"] for r in righe if r["stima_id_snapshot"]})
    vive = sorted({r["stima_id"] for r in righe if r["stima_id"]})
    contatti = sorted({r["contact_id"] for r in righe})
    lead = sorted({r["lead_id"] for r in righe if r["lead_id"]})

    acquisizioni = repo.facts_acquisitions(cur, snapshot)
    sopralluoghi = repo.facts_inspections(cur, snapshot)
    chiusi = repo.facts_leads_closed(cur, ctx, lead)
    inattivi = repo.facts_contacts_not_active(cur, ctx, contatti)
    tutti_i_tipi = tuple(t for tipi in EVENTI_DI_STOP.values() for t in tipi)
    eventi = repo.stop_events(cur, ctx, vive, tutti_i_tipi)
    consenso = can_send_marketing_bulk(ctx, contatti)

    ragioni: dict[int, set[str]] = {}
    for r in righe:
        trovate = set(acquisizioni.get(r["stima_id_snapshot"], ()))
        if r["stima_id_snapshot"] in sopralluoghi:
            trovate.add("inspection")
        if r["stima_id"] and (r["stima_id"], "owner_consultation_requested") in eventi:
            trovate.add("consultation_requested")
        if r["lead_id"] in chiusi:
            trovate.add("lead_closed")
        if r["contact_id"] in inattivi:
            trovate.add("contact_inactive")
        decisione = consenso.get(r["contact_id"])
        if decisione is not None and not decisione.allowed:
            trovate.add(CONSENT_STOP_BY_GUARD_REASON.get(decisione.reason, "consent_not_granted"))
        ragioni[r["id"]] = trovate
    return ragioni, eventi


def _evento_per(ragione: str, stima_id: int | None, eventi) -> int | None:
    """L'evento che racconta QUESTA ragione per QUESTA stima, se c'e'."""
    if stima_id is None:
        return None
    for tipo in EVENTI_DI_STOP.get(ragione, ()):
        identificativo = eventi.get((stima_id, tipo))
        if identificativo is not None:
            return identificativo
    return None


def _fase_stop(ctx, cur, adesso, limite: int, conteggi: dict[str, int]) -> None:
    righe = repo.lock_open_enrollments(cur, ctx, limit=limite)
    if not righe:
        return
    ragioni, eventi = _ragioni_di_stop(cur, ctx, righe)
    for r in righe:
        vincente = choose_stop_reason(ragioni[r["id"]])
        if vincente is None:
            continue
        try:
            with _Savepoint(cur, f"stop_{r['id']}"):
                repo.stop_enrollment(
                    cur, ctx, r["id"], reason=vincente, actor_user_id=None,
                    stop_event_id=_evento_per(vincente, r["stima_id"], eventi))
                repo.cancel_queued_journey_messages(
                    cur, ctx, r["id"], reason=f"stopped:{vincente}", actor_user_id=None)
                conteggi["stopped"] += 1
        except Exception:
            conteggi["errors"] += 1
            logger.exception("journey_tick_stop_failed enrollment_id=%s reason=%s",
                             r["id"], vincente)


# ---------------------------------------------------------------------------
# B - ADVANCE
# ---------------------------------------------------------------------------

def _passi_per_journey(cur, ctx, journey_ids) -> dict[int, list[dict[str, Any]]]:
    return {j: [s for s in repo.list_steps(cur, ctx, j) if s["active"]]
            for j in sorted(set(journey_ids))}


def _quando(base: datetime, passo: dict[str, Any], fuso: str) -> datetime:
    """`base + ritardo`, spostato in avanti fino alla prima finestra utile."""
    return send_window.next_allowed(
        base + timedelta(seconds=passo["delay_seconds"]), passo["send_window"], fuso)


def _base_temporale(passo: dict[str, Any], *, trigger_sent_at: datetime,
                    precedente_sent_at: datetime) -> datetime:
    """Da dove si misura il ritardo del passo: SEMPRE da un `sent_at`.

    `delay_from='trigger'` misura dalla mail della stima, `previous_step_sent`
    dall'invio del passo precedente. Nessuno dei due e' `created_at`
    dell'iscrizione: quello e' il momento in cui il MOTORE ha visto il fatto,
    non il momento in cui il fatto e' accaduto, e usarlo farebbe scivolare
    tutta la sequenza ogni volta che un tick gira in ritardo.
    """
    return trigger_sent_at if passo["delay_from"] == "trigger" else precedente_sent_at


def _fase_advance(ctx, cur, adesso, limite: int, conteggi: dict[str, int]) -> None:
    righe = repo.lock_advanceable_enrollments(cur, ctx, limit=limite)
    if not righe:
        return
    spediti = repo.sent_steps(cur, ctx, [r["id"] for r in righe])
    passi = _passi_per_journey(cur, ctx, [r["journey_id"] for r in righe])
    journeys = {j: repo.select_journey(cur, ctx, j) for j in passi}

    for r in righe:
        sent_at = spediti.get((r["id"], r["next_step_no"], r["run_no"]))
        if sent_at is None:
            continue  # il passo corrente non e' partito: non c'e' niente da avanzare
        try:
            with _Savepoint(cur, f"adv_{r['id']}"):
                successivi = [s for s in passi[r["journey_id"]] if s["step_no"] > r["next_step_no"]]
                if not successivi:
                    # L'ultimo passo e' partito: la journey ha detto tutto
                    # quello che aveva da dire. `ConflictError` qui significa
                    # che un altro atto l'ha gia' chiusa fra la lettura e
                    # adesso - non e' un errore, e' una corsa persa.
                    try:
                        repo.complete_enrollment(cur, ctx, r["id"])
                        conteggi["completed"] += 1
                    except ConflictError:
                        pass
                    continue
                passo = successivi[0]
                journey = journeys[r["journey_id"]]
                base = _base_temporale(passo, trigger_sent_at=r["trigger_sent_at"],
                                       precedente_sent_at=sent_at)
                avanzata = repo.advance_enrollment(
                    cur, ctx, r["id"], da_step=r["next_step_no"], next_step_no=passo["step_no"],
                    next_action_at=_quando(base, passo, journey["send_timezone"]),
                    next_action_kind=(KIND_AWAIT_OPERATOR if passo["default_mode"] == "assisted"
                                      else KIND_ENQUEUE))
                if avanzata is not None:
                    conteggi["advanced"] += 1
        except Exception:
            conteggi["errors"] += 1
            logger.exception("journey_tick_advance_failed enrollment_id=%s", r["id"])


# ---------------------------------------------------------------------------
# C - NUOVE ISCRIZIONI
# ---------------------------------------------------------------------------

def _righe_finte_per_candidati(candidati: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """I candidati, nella forma che lo stop engine sa gia' leggere.

    `_ragioni_di_stop` lavora su righe di iscrizione; un candidato e' una
    iscrizione che non e' ancora nata, e porta gli stessi quattro
    riferimenti. Adattare la forma costa cinque righe e permette di NON
    avere un secondo motore: le fonti, le query in blocco, la mappa degli
    eventi e la priorita' sono le stesse, letteralmente le stesse funzioni.

    `stima_id_snapshot` e' lo `stima_id` del trigger perche' e' esattamente
    cio' che il database scrivera' nello snapshot un istante dopo (la 071
    assegna `stima_id_snapshot := stima_id` all'INSERT).
    """
    return [{"id": c["trigger_message_id"], "contact_id": c["contact_id"],
             "lead_id": c["lead_id"], "stima_id": c["stima_id"],
             "stima_id_snapshot": c["stima_id"]}
            for c in candidati]


def _fase_enroll(ctx, cur, adesso, limite: int, conteggi: dict[str, int]) -> None:
    """Una iscrizione per ogni mail di stima spedita DOPO l'attivazione.

    I FATTI DECIDONO COME NASCE, NON SE NASCE. Con il permesso e senza fatti
    contrari nasce `active` con il primo passo dovuto; altrimenti nasce gia'
    `stopped` con la ragione (opzione B di P29-3A.1) e NESSUN messaggio. Una
    riga che spiega perche' non e' partito niente vale piu' di un silenzio.

    PERCHE' I FATTI DI STOP SI VALUTANO QUI (P29-3C, race chiusa).
    La fase STOP gira PRIMA di questa, quindi non vede le iscrizioni che
    questa crea: un incarico firmato prima che la mail di stima partisse
    avrebbe prodotto una iscrizione `active`, un M1 accodato nella fase DUE
    dello STESSO giro, e lo stop solo al giro successivo - con in mezzo un
    dispatch che poteva spedirlo. La correzione non e' una seconda fase di
    stop dopo questa (che rimanderebbe il problema a chi nasce dopo di
    lei): e' che la NASCITA guardi gli stessi fatti, con le stesse query in
    blocco e la stessa priorita'.

    UNA SOLA RISOLUZIONE. Consenso e fatti commerciali finiscono nello
    stesso insieme di ragioni e passano per `choose_stop_reason`, che legge
    `STOP_PRIORITY`. Non esistono due decisioni da mettere d'accordo: ne
    esiste una, e l'ordine ufficiale la risolve.
    """
    for journey in repo.active_journeys(cur, ctx, trigger_type=TRIGGER_STIMA_PDF_SENT):
        passi = [s for s in repo.list_steps(cur, ctx, journey["id"]) if s["active"]]
        if not passi:
            continue
        candidati = repo.candidate_triggers(
            cur, ctx, journey_id=journey["id"], activated_at=journey["activated_at"],
            limit=limite)
        if not candidati:
            continue
        primo = passi[0]
        for candidato in candidati:
            try:
                with _Savepoint(cur, f"enr_{candidato['trigger_message_id']}"):
                    # IL FENCE, e solo qui: un candidato e' una riga che sta
                    # per essere scritta, quindi la sua decisione si prende
                    # con contatto, lead e stima bloccati. Non e' una N+1
                    # sull'universo delle iscrizioni - e' una statement per
                    # ogni riga che nasce, che e' il minimo per non nascere
                    # sbagliata.
                    ragione, evento = _fence_e_stop(
                        cur, ctx, contact_id=candidato["contact_id"],
                        lead_id=candidato["lead_id"], stima_id=candidato["stima_id"],
                        stima_snapshot=candidato["stima_id"])
                    if ragione is None:
                        stato = ENR_ACTIVE
                        quando = _quando(candidato["sent_at"], primo, journey["send_timezone"])
                        kind = (KIND_AWAIT_OPERATOR if primo["default_mode"] == "assisted"
                                else KIND_ENQUEUE)
                        passo_no = primo["step_no"]
                    else:
                        stato = "stopped"
                        passo_no = quando = kind = None

                    _, creata = repo.insert_enrollment(
                        cur, ctx, journey_id=journey["id"], contact_id=candidato["contact_id"],
                        lead_id=candidato["lead_id"], stima_id=candidato["stima_id"],
                        trigger_message_id=candidato["trigger_message_id"],
                        status=stato, next_step_no=passo_no, next_action_at=quando,
                        next_action_kind=kind, stop_reason=ragione, stop_event_id=evento,
                        actor_type="system", actor_user_id=None,
                        idempotency_key=(f"journey:{journey['journey_key']}"
                                         f":v{journey['version']}:stima:{candidato['stima_id']}"))
                    if not creata:
                        conteggi["enrolled_skipped"] += 1
                    elif stato == ENR_ACTIVE:
                        conteggi["enrolled_active"] += 1
                    else:
                        conteggi["enrolled_stopped"] += 1
            except Exception:
                # Una corsa persa con un altro tick arriva qui come violazione
                # di unicita': l'iscrizione esiste gia', ed e' esattamente cio'
                # che si voleva. Contata come saltata, non come errore.
                conteggi["enrolled_skipped"] += 1
                logger.info("journey_tick_enroll_skipped trigger_message_id=%s journey_id=%s",
                            candidato["trigger_message_id"], journey["id"], exc_info=True)


# ---------------------------------------------------------------------------
# D - AZIONI DOVUTE
# ---------------------------------------------------------------------------

def _contesto_di_rendering(ctx, anagrafica: dict[str, Any], *, contact_id: int) -> dict[str, str]:
    """I campi che un template puo' chiedere. Nessun altro.

    `unsubscribe_url` e' obbligatorio per il marketing (P29-3B.0) e viene
    costruito qui: senza la chiave di firma la costruzione FALLISCE, e il
    messaggio non nasce. Una mail di marketing senza link di disiscrizione
    non e' un messaggio degradato, e' un messaggio che non si manda.
    """
    nome = (anagrafica.get("first_name") or anagrafica.get("display_name") or "").strip()
    return {
        "contact_first_name": nome,
        "agency_name": (anagrafica.get("agency_name") or "").strip(),
        "unsubscribe_url": unsubscribe.unsubscribe_url(
            _base_pubblica(), ctx.require_agency(), contact_id),
    }


def _accoda_passo(ctx, cur, riga: dict[str, Any], passo: dict[str, Any], *, mode: str,
                  scheduled_at: datetime, anagrafica: dict[str, Any]) -> dict[str, Any]:
    """Il messaggio del passo, nel ledger. Nessun provider, nessuna rete."""
    contesto = _contesto_di_rendering(ctx, anagrafica, contact_id=riga["contact_id"])
    soggetto, corpo = templates.render(passo["template_key"], passo["template_version"], contesto)
    destinazione = (anagrafica.get("email") or "").strip()
    if not destinazione:
        raise ValidationError(
            f"contact {riga['contact_id']} has no email: the step cannot be queued")
    return communication_service.enqueue(
        ctx, contact_id=riga["contact_id"], channel=passo["channel"],
        communication_type=passo["communication_type"], mode=mode,
        reason_code=passo["reason_code"], rendered_body=corpo, subject_snapshot=soggetto,
        destination_snapshot=destinazione, template_key=passo["template_key"],
        template_version=passo["template_version"], stima_id=riga["stima_id"],
        lead_id=riga["lead_id"], scheduled_at=scheduled_at,
        idempotency_key=(f"journey:{riga['id']}:step:{passo['step_no']}:run:{riga['run_no']}"),
        enrollment_id=riga["id"], step_no=passo["step_no"], run_no=riga["run_no"], cur=cur)


def _fase_due(ctx, cur, adesso, limite: int, conteggi: dict[str, int]) -> None:
    righe = repo.lock_due_enrollments(cur, ctx, kind=KIND_ENQUEUE, now=adesso, limit=limite)
    attesa = repo.lock_due_enrollments(cur, ctx, kind=KIND_AWAIT_OPERATOR, now=adesso,
                                       limit=limite)
    conteggi["awaiting_operator"] += len(attesa)
    if not righe:
        return
    passi = _passi_per_journey(cur, ctx, [r["journey_id"] for r in righe])
    anagrafiche = repo.rendering_context(cur, ctx, [r["contact_id"] for r in righe])

    for r in righe:
        try:
            with _Savepoint(cur, f"due_{r['id']}"):
                # IL FENCE, prima di accodare: `lock_due_enrollments` ha gia'
                # bloccato l'iscrizione, ma non il contatto, il lead e la
                # stima - ed e' li' che gli stop atterrano. Senza questa
                # rilettura, un incarico firmato fra la fase STOP e questa
                # riga produrrebbe un messaggio che il dispatcher puo'
                # spedire prima che il giro successivo lo cancelli.
                ragione, evento = _fence_e_stop(
                    cur, ctx, contact_id=r["contact_id"], lead_id=r["lead_id"],
                    stima_id=r["stima_id"], stima_snapshot=r["stima_id_snapshot"])
                if ragione is not None:
                    repo.stop_enrollment(cur, ctx, r["id"], reason=ragione,
                                         actor_user_id=None, stop_event_id=evento)
                    repo.cancel_queued_journey_messages(
                        cur, ctx, r["id"], reason=f"stopped:{ragione}", actor_user_id=None)
                    conteggi["stopped"] += 1
                    continue
                passo = next(s for s in passi[r["journey_id"]] if s["step_no"] == r["next_step_no"])
                esito = _accoda_passo(
                    ctx, cur, r, passo, mode="automatic",
                    # La policy del ledger, applicata qui invece che subita:
                    # un passo dovuto parte adesso, uno futuro porta la sua ora.
                    scheduled_at=r["next_action_at"] if r["next_action_at"] > adesso else adesso,
                    anagrafica=anagrafiche.get(r["contact_id"], {}))
                conteggi["queued" if esito["created"] else "queued_idempotent"] += 1
        except Exception:
            conteggi["errors"] += 1
            logger.exception(
                "journey_tick_enqueue_failed enrollment_id=%s step_no=%s run_no=%s",
                r["id"], r["next_step_no"], r["run_no"])
        # NON si avanza dopo un enqueue: il passo successivo nasce quando
        # QUESTO diventa `sent`, e chi lo manda e' il dispatcher.


# ---------------------------------------------------------------------------
# IL GIRO
# ---------------------------------------------------------------------------

FASI = (("stop", _fase_stop), ("advance", _fase_advance),
        ("enroll", _fase_enroll), ("due", _fase_due))


def tick(ctx, *, now: datetime | None = None, limit: int = LIMITE_PREDEFINITO,
         cur=None) -> dict[str, int]:
    """Un giro per l'agenzia effettiva del chiamante. Restituisce i conteggi.

    Con `cur` gira nella transazione di chi chiama - e' come lo guidano i
    test, che devono poter tenere aperte due transazioni insieme. Senza,
    ogni fase apre e chiude la propria: quattro transazioni brevi, e nessun
    lock che sopravviva alla fase che lo ha preso.
    """
    adesso = now or utcnow()
    conteggi = _conteggi()
    ctx.require_agency()
    for nome, fase in FASI:
        with cursore(cur) as (_, c):
            _pretendi_schema(c)
            fase(ctx, c, adesso, limit, conteggi)
    logger.info("journey_tick agency_id=%s %s", ctx.require_agency(), conteggi)
    return conteggi


# ---------------------------------------------------------------------------
# I DUE ATTI DELL'OPERATORE su un passo ASSISTITO
#
# Un passo `assisted` NON crea un messaggio quando scade: crea un'attesa. E'
# la decisione di P29-3A.1 (§ modo dei passi) e il motivo per cui esiste
# questa coppia di funzioni - qualcuno guarda il testo e decide se mandarlo o
# saltarlo. L'attore viene SEMPRE dalla sessione; nessuna delle due accetta
# un `agency_id`, e nessuna chiama un provider.
# ---------------------------------------------------------------------------

def _passo_corrente(cur, ctx, riga: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    passi = [s for s in repo.list_steps(cur, ctx, riga["journey_id"]) if s["active"]]
    passo = next((s for s in passi if s["step_no"] == riga["next_step_no"]), None)
    if passo is None:
        raise ConflictError(
            f"enrollment {riga['id']} points at step {riga['next_step_no']}, "
            "which is not an active step of its journey")
    return passo, passi


def send_current(ctx, enrollment_id: int, *, now: datetime | None = None,
                 cur=None) -> dict[str, Any]:
    """Manda ADESSO il passo assistito che sta aspettando.

    Idempotente per costruzione, e non per fortuna: il messaggio del
    tentativo `(iscrizione, passo, run)` ha una chiave di idempotenza
    deterministica e un indice unico che la protegge. Il secondo di due click
    ritrova lo stesso messaggio e risponde `created=False` invece di
    accodarne un altro o di sollevare - perche' dal punto di vista di chi ha
    cliccato due volte, la mail e' partita, ed e' vero.

    Il testo si compone al momento del click e non alla scadenza: quello che
    l'operatore ha fatto partire e' cio' che il registro conserva.
    """
    utente = operatore(ctx)
    adesso = now or utcnow()
    with cursore(cur) as (_, c):
        _pretendi_schema(c)
        riga = repo.select_enrollment(c, ctx, enrollment_id, for_update=True)
        contact_in_scope(c, ctx, riga["contact_id"])
        if riga["status"] != ENR_ACTIVE:
            raise ConflictError(f"enrollment {enrollment_id} is {riga['status']}, not active")
        passo, _ = _passo_corrente(c, ctx, riga)
        if passo["default_mode"] != "assisted":
            raise ConflictError(
                f"step {passo['step_no']} of enrollment {enrollment_id} is automatic: "
                "the engine sends it, not an operator")

        esistente = repo.select_journey_message(
            c, ctx, enrollment_id, step_no=passo["step_no"], run_no=riga["run_no"])
        if esistente is not None:
            return {"message": esistente, "created": False, "stopped": None,
                    "enrollment": repo.select_enrollment(c, ctx, enrollment_id)}

        if riga["next_action_kind"] != KIND_AWAIT_OPERATOR:
            raise ConflictError(
                f"enrollment {enrollment_id} is not waiting for an operator")
        if riga["next_action_at"] > adesso:
            raise ConflictError(
                f"step {passo['step_no']} of enrollment {enrollment_id} is not due yet "
                f"({riga['next_action_at'].isoformat()})")

        # IL FENCE, prima di far partire il passo assistito. Un operatore che
        # clicca mentre un collega registra l'incarico non deve poter mandare
        # la mail: se lo stop ha committato prima del lock, l'iscrizione si
        # ferma qui e il click viene rifiutato con la ragione. Se il lock e'
        # nostro, la mail parte e lo stop aspetta il commit.
        ragione, evento = _fence_e_stop(
            c, ctx, contact_id=riga["contact_id"], lead_id=riga["lead_id"],
            stima_id=riga["stima_id"], stima_snapshot=riga["stima_id_snapshot"])
        if ragione is not None:
            fermata = repo.stop_enrollment(c, ctx, enrollment_id, reason=ragione,
                                           actor_user_id=None, stop_event_id=evento)
            repo.cancel_queued_journey_messages(
                c, ctx, enrollment_id, reason=f"stopped:{ragione}", actor_user_id=None)
            # SI TORNA, NON SI SOLLEVA. Un'eccezione qui uscirebbe dal
            # context della transazione, che fa `rollback()`: lo stop appena
            # deciso sparirebbe e l'iscrizione resterebbe viva fino al giro
            # successivo - cioe' il contrario di cio' che questo controllo
            # esiste per ottenere. La transazione committa con lo stop
            # dentro, e il RIFIUTO all'operatore lo formula la rotta, che
            # parla dopo il commit.
            logger.info("journey_step_refused_by_stop enrollment_id=%s reason=%s user_id=%s",
                        enrollment_id, ragione, utente)
            return {"message": None, "created": False, "enrollment": fermata,
                    "stopped": ragione}

        anagrafiche = repo.rendering_context(c, ctx, [riga["contact_id"]])
        esito = _accoda_passo(ctx, c, riga, passo, mode="assisted", scheduled_at=adesso,
                              anagrafica=anagrafiche.get(riga["contact_id"], {}))
        repo.hand_to_enqueue(c, ctx, enrollment_id, step_no=passo["step_no"])
        logger.info("journey_step_sent_by_operator enrollment_id=%s step_no=%s user_id=%s",
                    enrollment_id, passo["step_no"], utente)
        return {"message": esito["message"], "created": esito["created"],
                "enrollment": repo.select_enrollment(c, ctx, enrollment_id),
                "stopped": None}


def skip_current(ctx, enrollment_id: int, *, now: datetime | None = None,
                 cur=None) -> dict[str, Any]:
    """Salta il passo assistito in attesa e va al successivo.

    La base temporale del passo dopo e' ADESSO, dichiaratamente: il passo
    saltato non ha un `sent_at` da cui misurare, e usare quello del passo
    ancora precedente farebbe arrivare il successivo gia' in ritardo - o
    peggio, subito. Lo salto e' un atto, e il tempo riparte da li'.
    """
    utente = operatore(ctx)
    adesso = now or utcnow()
    with cursore(cur) as (_, c):
        _pretendi_schema(c)
        riga = repo.select_enrollment(c, ctx, enrollment_id, for_update=True)
        contact_in_scope(c, ctx, riga["contact_id"])
        if riga["status"] != ENR_ACTIVE:
            raise ConflictError(f"enrollment {enrollment_id} is {riga['status']}, not active")
        passo, passi = _passo_corrente(c, ctx, riga)
        if passo["default_mode"] != "assisted" or riga["next_action_kind"] != KIND_AWAIT_OPERATOR:
            raise ConflictError(
                f"step {passo['step_no']} of enrollment {enrollment_id} is not waiting for "
                "an operator: there is nothing to skip")

        journey = repo.select_journey(c, ctx, riga["journey_id"])
        repo.timeline_event(
            c, ctx, event_type=EVENT_STEP_SKIPPED, contact_id=riga["contact_id"],
            stima_id=riga["stima_id"],
            payload={"journey_key": journey["journey_key"], "journey_version": journey["version"],
                     "step_key": passo["step_key"], "enrollment_id": enrollment_id,
                     "run_no": riga["run_no"]},
            idempotency_key=(f"p29_3:v1:step_skipped:enr:{enrollment_id}"
                             f":step:{passo['step_no']}:run:{riga['run_no']}"),
            created_by=str(utente), occurred_at=adesso)

        successivi = [s for s in passi if s["step_no"] > passo["step_no"]]
        if not successivi:
            return {"enrollment": repo.complete_enrollment(c, ctx, enrollment_id),
                    "skipped_step_no": passo["step_no"], "completed": True}
        prossimo = successivi[0]
        avanzata = repo.advance_enrollment(
            c, ctx, enrollment_id, da_step=passo["step_no"], next_step_no=prossimo["step_no"],
            next_action_at=_quando(adesso, prossimo, journey["send_timezone"]),
            next_action_kind=(KIND_AWAIT_OPERATOR if prossimo["default_mode"] == "assisted"
                              else KIND_ENQUEUE))
        return {"enrollment": avanzata or repo.select_enrollment(c, ctx, enrollment_id),
                "skipped_step_no": passo["step_no"], "completed": False}
