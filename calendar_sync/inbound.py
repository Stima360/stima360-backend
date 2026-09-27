"""A30-10B - il worker INBOUND (Google -> Agenda).

Legge SOLO gli eventi Google GIA' mappati da Stima360 (mai un elenco, mai un
evento arbitrario del calendario dell'operatore: A30-10 non e' un import).
Quando la lettura mostra una modifica AMMESSA (orario, cancellazione), la
applica passando ESCLUSIVAMENTE dal dominio autorevole
(`appointments.service.reschedule_appointment` / `cancel_appointment`), con
l'OperatorContext costruito dalla sola factory approvata
(`operator_auth.calendar_inbound.calendar_inbound_context`, decisione D2).
Nessuna scrittura diretta su `appointments` qui, nessun secondo path.

DUE CODE INDIPENDENTI (074 outbound, 075 inbound, decisione D6): nessuna
colonna condivisa, nessuna transazione che le mescoli. `dirty_generation` /
`synced_generation` sono l'UNICO punto di contatto, e solo in lettura (D:
"LOCAL DIRTY WINS").

TRANSAZIONI: nessuna chiamata Google dentro una transazione PostgreSQL,
stesso principio del riconciliatore outbound (A30-9A §D4). Ogni fase e' una
transazione BREVE propria; il domain path (`reschedule_appointment` /
`cancel_appointment`) apre e chiude LA SUA, mai annidata dentro una nostra.

LOCAL DIRTY WINS (decisione del gate): se `dirty_generation != synced_generation`
PRIMA della GET, la mapping non viene nemmeno letta - l'outbound vince. Dopo
la GET e prima di qualunque mutazione CRM, la generazione si rilegge: se e'
cambiata, l'inbound abortisce senza scrivere. La barriera DEFINITIVA resta
comunque il controllo di `version` dentro il dominio (`VersionConflict`), che
qui non viene mai forzato.

D1 (ACTOR): l'attore e sempre `calendar_connections.user_id`. Prima di ogni
mutazione si verifica che sia ancora l'assegnatario ATTUALE della riga viva:
se non coincide, l'inbound NON si applica (il CRM vince, l'outbound
riallinea).
"""
from __future__ import annotations

import logging

from core.database import core_cursor

from appointments import errors as appointment_errors
from appointments.schemas import CancelBody, RescheduleBody
from appointments.service import cancel_appointment, reschedule_appointment
from operator_auth.calendar_inbound import (
    CalendarInboundContextUnavailable,
    calendar_inbound_context,
)

from . import constants as k
from . import crypto, repository
from .provider import (
    NEEDS_REAUTH,
    RETRY,
    CalendarProviderError,
    ProviderAuth,
    backoff_seconds,
    classify,
)

log = logging.getLogger("calendar_sync.inbound")

#: D3 - il motivo ESATTO richiesto dal gate, mai un altro testo.
REASON_GOOGLE_CANCELLED = "Annullato da Google Calendar"
#: D4 - il codice stabile per un reschedule inbound rifiutato per conflitto.
ERROR_INBOUND_CONFLICT = "inbound_conflict"
#: Stati della riga viva su cui l'inbound ha titolo (D-eligibility): mai
#: `requested`, `completed`, `no_show`, `cancelled`, `rescheduled` - nessuno
#: stato terminale/non ancora fissato torna indietro per una modifica Google.
_ELIGIBLE_STATUSES = ("scheduled", "confirmed")


def run_once(*, provider, limit: int = 25, lease_seconds: int = k.DEFAULT_LEASE_SECONDS,
             agency_id: int | None = None, keyring=None, cursor=core_cursor) -> list:
    """UN giro: un lotto di claim inbound (transazione breve, poi commit),
    ogni riga lavorata per conto proprio - un errore inatteso su una riga non
    ferma le altre (stesso principio di `calendar_sync.service.run_once`)."""
    with cursor(commit=True) as (_, cur):
        prese = repository.claim_batch_inbound(cur, limit=limit, lease_seconds=lease_seconds,
                                                agency_id=agency_id)
    esiti = []
    for presa in prese:
        try:
            esiti.append((presa.sync_id, _process(presa, provider=provider, keyring=keyring,
                                                   cursor=cursor)))
        except Exception:  # noqa: BLE001 - nessun dettaglio (potrebbe contenere dati)
            log.exception("calendar_sync.inbound: errore interno sulla riga %s", presa.sync_id)
            try:
                with cursor(commit=True) as (_, cur):
                    repository.release_inbound_claim(cur, presa, error_code="internal_error")
            except Exception:  # noqa: BLE001
                log.exception("calendar_sync.inbound: impossibile liberare il claim della riga %s",
                              presa.sync_id)
            esiti.append((presa.sync_id, "internal_error"))
    return esiti


def _process(claim, *, provider, keyring, cursor) -> str:
    # FASE 1 (tx breve): stato attuale, guardia local-dirty, segreto della
    # connessione. Nessuna rete qui.
    with cursor(commit=True) as (_, cur):
        riga = repository.load_claimed_inbound(cur, claim)
        if riga is None:
            return "lost_claim"
        if riga["dirty_generation"] != riga["synced_generation"]:
            # LOCAL DIRTY WINS: l'outbound ha del lavoro non ancora
            # sincronizzato su questa catena. Non si legge nemmeno Google.
            repository.release_inbound_claim(cur, claim)
            return "deferred_local_dirty"
        connessione = repository.get_connection(cur, claim.agency_id, riga["remote_connection_id"])
        if not (connessione and connessione.get("usable")):
            repository.release_inbound_claim(cur, claim)
            return "connection_unusable"
        appuntamento = repository.get_appointment(cur, claim.agency_id,
                                                   riga["current_appointment_id"])
        if not (appuntamento and appuntamento["status"] in _ELIGIBLE_STATUSES):
            repository.release_inbound_claim(cur, claim)
            return "not_eligible"
        segreto = repository.connection_secret(cur, claim.agency_id, connessione["id"])
        generazione_vista = riga["dirty_generation"]
        tentativi_attuali = int(riga["inbound_attempt_count"])

    if segreto is None:
        with cursor(commit=True) as (_, cur):
            repository.release_inbound_claim(cur, claim, error_code="connection_changed")
        return "connection_changed"

    try:
        if keyring is None:
            keyring = crypto.require_keyring()
        token = keyring.decrypt(segreto)
    except crypto.CalendarCryptoNotConfigured:
        with cursor(commit=True) as (_, cur):
            repository.release_inbound_claim(cur, claim, error_code="crypto_not_configured")
        return "crypto_not_configured"
    except crypto.CalendarCryptoError:
        with cursor(commit=True) as (_, cur):
            repository.mark_connection_needs_reauth(cur, claim.agency_id, connessione["id"],
                                                     error_code="token_unreadable")
            repository.release_inbound_claim(cur, claim, error_code="token_unreadable")
        return "needs_reauth"
    auth = ProviderAuth(connection_id=connessione["id"], refresh_token=token)
    del segreto, token

    # FASE 2: la CHIAMATA GOOGLE, fuori da qualunque transazione.
    try:
        remoto = provider.get_event(auth, riga["remote_calendar_id"], riga["remote_event_id"])
    except CalendarProviderError as errore:
        return _handle_provider_error(cursor, claim, connessione, errore,
                                      tentativi_attuali=tentativi_attuali)
    finally:
        del auth

    # FASE 3 (tx breve): rileggere la generazione PRIMA di decidere. Nessuna
    # rete qui: solo la decisione e, se serve, l'annotazione della coda.
    with cursor(commit=True) as (_, cur):
        fresca = repository.load_claimed_inbound(cur, claim)
        if fresca is None:
            return "lost_claim"
        if (fresca["dirty_generation"] != generazione_vista
                or fresca["dirty_generation"] != fresca["synced_generation"]):
            # F - CONCORRENZA: il CRM e' cambiato mentre leggevamo Google.
            # ABORT: nessuna scrittura. L'outbound (gia' innescato da quella
            # stessa scrittura) riallinea Google al CRM.
            repository.release_inbound_claim(cur, claim)
            return "aborted_race"
        appuntamento = repository.get_appointment(cur, claim.agency_id,
                                                   fresca["current_appointment_id"])
        if not (appuntamento and appuntamento["status"] in _ELIGIBLE_STATUSES):
            repository.release_inbound_claim(cur, claim)
            return "not_eligible"

        piano, extra = _decidi(remoto, fresca, appuntamento)
        if piano in ("synced", "content_drift_resync"):
            if piano == "content_drift_resync":
                repository.request_resync(cur, claim.agency_id, fresca["id"])
            repository.finalize_inbound_success(cur, claim)
            return piano

        # CANCEL o RESCHEDULE: uno snapshot per il domain path, che apre le
        # sue proprie transazioni - questa si chiude qui, prima di chiamarlo.
        sync_id = fresca["id"]
        appointment_id = appuntamento["id"]
        versione = appuntamento["version"]
        assegnatario = appuntamento["assigned_user_id"]
        titolare_connessione = connessione["user_id"]

    if assegnatario != titolare_connessione:
        # D1 (REVIEW FIX GATE, FIX 2): l'assegnatario attuale non e' (piu')
        # il titolare della connessione che ha generato questa lettura: NON
        # si applica nulla di quanto letto da Google - nessun reschedule,
        # nessun cancel, nessuna decifratura ulteriore del segreto. Il CRM
        # vince. Ma la mapping resta com'era (magari ancora sull'evento
        # dell'agente precedente): senza una richiesta esplicita l'outbound
        # non avrebbe alcun motivo di riguardarla finche' non arriva
        # un'altra mutazione CRM. Si richiede quindi qui, con la STESSA
        # primitiva autorevole del ramo D4 (`request_resync`), la
        # riconciliazione OUTBOUND della stessa catena: il giro outbound
        # successivo (stesso cron, dopo questo) sposta/ripristina l'evento
        # remoto sul calendario dell'agente ATTUALE, senza che l'inbound
        # abbia toccato `appointments` o `appointment_events`.
        with cursor(commit=True) as (_, cur):
            repository.request_resync(cur, claim.agency_id, sync_id)
            repository.release_inbound_claim(cur, claim, error_code="assignee_mismatch")
        return "assignee_mismatch"

    try:
        with cursor(commit=False) as (_, cur):
            ctx = calendar_inbound_context(cur, agency_id=claim.agency_id,
                                           user_id=titolare_connessione)
    except CalendarInboundContextUnavailable:
        with cursor(commit=True) as (_, cur):
            repository.release_inbound_claim(cur, claim, error_code="operator_context_unavailable")
        return "operator_context_unavailable"

    if piano == "cancel":
        return _apply_cancel(cursor, claim, sync_id, ctx, appointment_id, versione)
    return _apply_reschedule(cursor, claim, sync_id, ctx, appointment_id, versione, extra)


def _decidi(remoto, fresca, appuntamento):
    """Il piano, puro: cosa fare con la lettura remota. `extra` porta
    (start_at, end_at) per un reschedule, altrimenti None."""
    if remoto is None or remoto.status == "cancelled":
        return "cancel", None
    if remoto.etag == fresca["etag"] and remoto.updated_at == fresca["remote_updated_at"]:
        return "synced", None
    if not _proprieta_coerenti(remoto.private_properties, fresca, appuntamento):
        # Manomesse/incoerenti: NON importare nulla, nemmeno un orario
        # altrimenti legittimo. Solo un resync outbound.
        return "content_drift_resync", None
    if (remoto.start_at, remoto.end_at) != (appuntamento["start_at"], appuntamento["end_at"]):
        return "reschedule", (remoto.start_at, remoto.end_at)
    # etag/updated diversi ma orari e proprieta' coerenti: SOLO contenuto non
    # ammesso (summary/description) e' cambiato su Google.
    return "content_drift_resync", None


def _proprieta_coerenti(private_properties, fresca, appuntamento) -> bool:
    """Le proprieta' private tecniche, QUANDO presenti, devono combaciare con
    la mapping corrente. Assenti (mai scritte, o un evento piu' vecchio del
    campo) non bloccano nulla: solo un valore presente e DIVERSO conta come
    incoerenza."""
    if not private_properties:
        return True
    origine = private_properties.get("stima360_origin")
    if origine is not None and origine != k.ORIGIN_MARKER:
        return False
    catena = private_properties.get("stima360_chain_id")
    if catena is not None and str(catena) != str(fresca["chain_root_appointment_id"]):
        return False
    riga_id = private_properties.get("stima360_appointment_id")
    if riga_id is not None and str(riga_id) != str(appuntamento["id"]):
        return False
    return True


def _apply_reschedule(cursor, claim, sync_id, ctx, appointment_id, versione, orari):
    start_at, end_at = orari
    # D2/gate: il reschedule Google non cambia mai l'agente. `None` conserva
    # quello attuale (il service lo interpreta cosi', A30-2P/A30-8).
    payload = RescheduleBody(start_at=start_at, end_at=end_at, assigned_user_id=None,
                             version=versione)
    try:
        reschedule_appointment(ctx, appointment_id, payload)
    except appointment_errors.AppointmentConflict:
        # D4: CRM WINS. Nessun bypass, nessun retry della mutazione. Un
        # resync outbound, nello stesso giro cron, riporta Google all'orario
        # CRM.
        with cursor(commit=True) as (_, cur):
            repository.request_resync(cur, claim.agency_id, sync_id)
            repository.release_inbound_claim(cur, claim, error_code=ERROR_INBOUND_CONFLICT)
        return "inbound_conflict"
    except (appointment_errors.VersionConflict, appointment_errors.InvalidTransition):
        # Local wins: qualcosa e' cambiato/e' terminale nel frattempo. Non si
        # forza nulla; l'outbound (se la generazione e' salita) riallinea.
        with cursor(commit=True) as (_, cur):
            repository.release_inbound_claim(cur, claim)
        return "local_wins"

    with cursor(commit=True) as (_, cur):
        repository.finalize_inbound_success(cur, claim)
    return "rescheduled"


def _apply_cancel(cursor, claim, sync_id, ctx, appointment_id, versione):
    payload = CancelBody(version=versione, reason=REASON_GOOGLE_CANCELLED, follow_up=None)
    try:
        cancel_appointment(ctx, appointment_id, payload)
    except (appointment_errors.VersionConflict, appointment_errors.InvalidTransition):
        # La riga e' diventata terminale (o e' cambiata) nel frattempo:
        # local wins, non si forza il cancel.
        with cursor(commit=True) as (_, cur):
            repository.release_inbound_claim(cur, claim)
        return "local_wins"

    with cursor(commit=True) as (_, cur):
        repository.finalize_inbound_success(cur, claim)
    return "cancelled"


def _handle_provider_error(cursor, claim, connessione, errore, *, tentativi_attuali):
    classificazione = classify(errore, operation="get")
    with cursor(commit=True) as (_, cur):
        if classificazione.action == NEEDS_REAUTH:
            repository.mark_connection_needs_reauth(cur, claim.agency_id, connessione["id"],
                                                     error_code=classificazione.code)
            repository.release_inbound_claim(cur, claim, error_code=classificazione.code)
            return "needs_reauth"
        # RETRY (429/5xx/timeout/rete) o FAILED (una nostra richiesta
        # malformata, in pratica irraggiungibile per una GET): in entrambi i
        # casi si ritenta con lo stesso backoff deterministico outbound (mai
        # uno stato terminale: l'eleggibilita' resta legata solo alla riga
        # viva, non a un tetto di tentativi di LETTURA).
        ritardo = backoff_seconds(tentativi_attuali + 1)
        repository.mark_retry_inbound(cur, claim, error_code=classificazione.code,
                                      delay_seconds=ritardo)
        return "retrying" if classificazione.action == RETRY else "retrying_failed_get"
