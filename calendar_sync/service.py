"""A30-9A - il cuore della sincronizzazione: id evento, payload, piano, riconciliatore.

LATEST-STATE (D9). Il worker non esegue comandi vecchi: prende una riga di
sync (claim con generazione N), legge dal DB lo stato ATTUALE della catena
(riga viva, agente, connessioni, remoto attuale), decide cosa deve essere vero
sul calendario remoto e lo rende vero. Alla fine dichiara `synced` solo se la
generazione e' ancora N: se nel frattempo qualcuno ha segnato la catena, la
riga torna `pending` e il giro successivo riconcilia lo stato piu' recente.

TRANSAZIONI (D4). Nessuna chiamata remota avviene dentro una transazione: si
legge e si annota in una transazione breve, si chiama il provider, si
registra l'esito di OGNI fase riuscita in un'altra transazione breve con
compare-and-set sul claim. Un crash fra la chiamata e la registrazione lascia
la riga `syncing` fino al lease: il retry ripete la stessa fase con lo STESSO
id evento deterministico, e l'ensure idempotente del provider converge senza
duplicati.

POLITICA PER STATO DELLA RIGA VIVA (D6, D7, D8)
    scheduled, confirmed  evento presente sul calendario dell'AGENTE ATTUALE,
                          se ha una connessione valida; se l'evento vive su
                          un'altra connessione (riassegnazione) prima lo si
                          toglie da li'; senza connessione: waiting_connection;
    cancelled             evento assente (DELETE; 404/410 = gia' assente);
    completed, no_show,
    requested             nessuna azione remota: un evento esistente resta
                          nello storico, uno mai creato non si crea.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from core.database import core_cursor

from . import constants as k
from . import crypto, repository
from .provider import (
    FAILED,
    NEEDS_REAUTH,
    RETRY,
    SUCCESS,
    CalendarProviderError,
    EventPayload,
    ProviderAuth,
    backoff_seconds,
    classify,
    exhausted,
)

log = logging.getLogger("calendar_sync")

ROMA = ZoneInfo("Europe/Rome")

# ---------------------------------------------------------------------------
# ID EVENTO DETERMINISTICO (funzione pura)
# ---------------------------------------------------------------------------
#
# Contratto Google Calendar (Events resource, campo `id` fornito dal client):
# caratteri ammessi = quelli della codifica base32hex, cioe' lettere minuscole
# a-v e cifre 0-9 (RFC 2938 §3.1.2); lunghezza fra 5 e 1024 caratteri; unico
# per calendario. Qui: "s360" + base32hex minuscolo, senza padding, dello
# SHA-256 di (namespace dell'algoritmo, NAMESPACE DI DEPLOYMENT, agenzia,
# radice della catena) = 4 + 52 = 56 caratteri, tutti in [0-9a-v].
#
# NAMESPACE DI DEPLOYMENT (A30-9A final review, punto 1): TEST e PROD possono
# avere gli stessi id di agenzia e di appuntamento. Se lo stesso calendario
# Google fosse collegato per errore a entrambi, non devono mai condividere un
# id evento: il namespace del deployment entra nel digest (mai in chiaro
# nell'id). Non e' un segreto; lo sceglie e lo passa ESPLICITAMENTE il
# chiamante (A30-9B lo prendera' dalla configurazione runtime): il dominio
# non conosce nomi d'ambiente, servizi o database.
#
# Stabile: dipende SOLO da deployment, agenzia e radice, non dalla riga viva,
# dall'agente o dal calendario, quindi non cambia con retry, spostamenti e
# riassegnazioni (l'unicita' Google e' per calendario). Non contiene dati
# personali e non e' invertibile (SHA-256); i suoi ingressi numerici sono
# piccoli, quindi e' un identificatore pseudonimo, non un segreto (le
# proprieta' private dell'evento portano comunque gli id CRM, per scelta del
# gate).

#: Un namespace di deployment: minuscole, cifre, '.', '_', '-'; 3-64 caratteri.
_DEPLOYMENT_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


def deterministic_event_id(deployment_namespace: str, agency_id: int,
                           chain_root_appointment_id: int) -> str:
    if not isinstance(deployment_namespace, str) or not _DEPLOYMENT_NAMESPACE.match(
            deployment_namespace):
        raise ValueError("deployment_namespace non valido: [a-z0-9._-], 3-64 caratteri")
    if (isinstance(agency_id, bool) or not isinstance(agency_id, int)
            or isinstance(chain_root_appointment_id, bool)
            or not isinstance(chain_root_appointment_id, int)):
        raise TypeError("agency_id e chain_root_appointment_id devono essere interi")
    # Serializzazione non ambigua (JSON di una lista): nessuna collisione per
    # concatenazione fra campi.
    materiale = json.dumps([k.EVENT_ID_NAMESPACE, deployment_namespace, agency_id,
                            chain_root_appointment_id], separators=(",", ":")).encode("ascii")
    impronta = hashlib.sha256(materiale).digest()
    corpo = base64.b32hexencode(impronta).decode("ascii").rstrip("=").lower()
    return k.EVENT_ID_PREFIX + corpo


# ---------------------------------------------------------------------------
# PAYLOAD (funzioni pure) - nessun dato personale
# ---------------------------------------------------------------------------

#: Etichette neutre dell'evento: il TIPO, mai il cliente.
_ETICHETTE = {
    "inspection": "Sopralluogo",
    "seller_meeting": "Appuntamento",
    "buyer_visit": "Visita",
    "valuation_presentation": "Appuntamento",
    "mandate_signing": "Appuntamento",
    "call": "Telefonata",
    "video_call": "Videochiamata",
}
DEFAULT_SUMMARY = "Appuntamento"
DESCRIPTION = "Stima360"


def build_payload(appointment: dict, *, agency_id: int, chain_root_appointment_id: int,
                  event_id: str) -> EventPayload:
    """L'evento remoto desiderato per la riga viva `appointment`.

    SOLO: etichetta del tipo, inizio, fine, fuso, descrizione fissa, id
    tecnici nelle proprieta' private. MAI: cliente, telefono, email, luogo,
    note, nota di esito, motivo d'annullamento, lead, stima, immobile, agente.
    """
    if appointment["agency_id"] != agency_id:
        raise ValueError("appuntamento di un'altra agenzia")
    inizio = appointment["start_at"].astimezone(ROMA)
    fine = appointment["end_at"].astimezone(ROMA)
    return EventPayload(
        event_id=event_id,
        summary=_ETICHETTE.get(appointment["appointment_type"], DEFAULT_SUMMARY),
        start_at=inizio,
        end_at=fine,
        timezone="Europe/Rome",
        description=DESCRIPTION,
        private_properties={
            "stima360_chain_id": str(chain_root_appointment_id),
            "stima360_appointment_id": str(appointment["id"]),
            "stima360_origin": k.ORIGIN_MARKER,
        },
    )


def payload_hash(payload: EventPayload) -> str:
    """Hash CANONICO: istanti normalizzati in UTC, chiavi ordinate. Due payload
    con gli stessi istanti espressi in fusi diversi hanno lo stesso hash."""
    def istante(v):
        return v.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    canonico = {
        "event_id": payload.event_id,
        "summary": payload.summary,
        "start_at": istante(payload.start_at),
        "end_at": istante(payload.end_at),
        "timezone": payload.timezone,
        "description": payload.description,
        "private_properties": {str(a): str(b) for a, b in payload.private_properties.items()},
    }
    testo = json.dumps(canonico, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(testo.encode("ascii")).hexdigest()


# ---------------------------------------------------------------------------
# INTENTI OAUTH (solo primitive: il flusso e' A30-9B)
# ---------------------------------------------------------------------------

def new_oauth_state() -> tuple:
    """(state in chiaro da mettere nell'URL, hash da salvare). Il chiaro non
    si salva mai."""
    grezzo = secrets.token_urlsafe(32)
    return grezzo, hash_oauth_state(grezzo)


def hash_oauth_state(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# PIANO (funzione pura)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Plan:
    delete_on: dict | None = None        # connessione da cui togliere l'evento
    abandon_remote: bool = False         # il remoto attuale non e' raggiungibile
    ensure_on: dict | None = None        # connessione su cui l'evento deve esistere
    final_status: str = "synced"
    final_code: str | None = None


def _usabile(conn) -> bool:
    """Utilizzabile ADESSO: flag calcolato dal repository a ogni lettura
    (stato `connected`, token presente, membership attiva)."""
    return bool(conn and conn.get("usable"))


def _in_attesa_di_riautorizzazione(conn) -> bool:
    """La connessione chiede una nuova autorizzazione all'operatore, che e'
    ancora un membro attivo. Con la membership sospesa non c'e' nessuno a cui
    chiederla: la connessione e' semplicemente non disponibile."""
    return bool(conn and conn["status"] == "needs_reauth" and conn.get("membership_active"))


def plan_reconciliation(*, appointment: dict, sync_row: dict, desired_connection: dict | None,
                        remote_connection: dict | None, desired_hash: str | None) -> Plan:
    """Il piano, puro. Una connessione si USA (decifratura, provider) solo se
    `usable`; una connessione non utilizzabile non si tocca nemmeno per
    cancellare eventi (A30-9A final review, punto 2)."""
    stato = appointment["status"]
    remoto = remote_connection if sync_row["remote_connection_id"] is not None else None

    if stato in k.APPOINTMENT_REMOTE_PRESENT:
        valida = desired_connection if _usabile(desired_connection) else None
        delete_on, abbandona = None, False
        if remoto is not None and (valida is None or remoto["id"] != valida["id"]):
            if _usabile(remoto):
                delete_on = remoto
            elif desired_connection is not None and remoto["id"] == desired_connection["id"]:
                pass    # la STESSA connessione, temporaneamente non disponibile: il
                        # mapping resta, per riprendere senza duplicati
            else:
                abbandona = True
        if valida is not None:
            gia_allineato = (remoto is not None and remoto["id"] == valida["id"]
                             and sync_row["synced_payload_hash"] == desired_hash)
            return Plan(delete_on=delete_on, abandon_remote=abbandona,
                        ensure_on=None if gia_allineato else valida,
                        final_status="synced",
                        final_code="previous_calendar_unreachable" if abbandona else None)
        if _in_attesa_di_riautorizzazione(desired_connection):
            return Plan(delete_on=delete_on, abandon_remote=abbandona,
                        final_status="needs_reauth", final_code="connection_needs_reauth")
        return Plan(delete_on=delete_on, abandon_remote=abbandona,
                    final_status="waiting_connection", final_code="no_connection")

    if stato in k.APPOINTMENT_REMOTE_ABSENT:
        if remoto is None:
            return Plan(final_status="synced")
        if _usabile(remoto):
            return Plan(delete_on=remoto, final_status="synced")
        if remoto["status"] == "disconnected":
            return Plan(final_status="detached", final_code="disconnected")
        if _in_attesa_di_riautorizzazione(remoto):
            return Plan(final_status="needs_reauth", final_code="connection_needs_reauth")
        return Plan(final_status="waiting_connection", final_code="no_connection")

    if stato in k.APPOINTMENT_REMOTE_UNTOUCHED:
        return Plan(final_status="synced")

    # `rescheduled` come riga viva: la catena e' rotta (uno spostamento ha
    # sempre un successore). Non si indovina.
    return Plan(final_status="failed", final_code="chain_broken")


# ---------------------------------------------------------------------------
# RICONCILIATORE
# ---------------------------------------------------------------------------

class _Fermo(Exception):
    """Interruzione controllata di un giro: l'esito e' gia' stato registrato."""

    def __init__(self, esito):
        super().__init__(esito)
        self.esito = esito


def _auth(cursor, claim, connessione, keyring_ref):
    """Le credenziali di una connessione, decifrate solo in memoria."""
    with cursor(commit=False) as (_, cur):
        segreto = repository.connection_secret(cur, claim.agency_id, connessione["id"])
    if segreto is None:
        raise _Transitorio("connection_changed")
    if keyring_ref[0] is None:
        keyring_ref[0] = crypto.require_keyring()
    token = keyring_ref[0].decrypt(segreto)
    return ProviderAuth(connection_id=connessione["id"], refresh_token=token)


class _Transitorio(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _registra(cursor, funzione, *args, **kwargs):
    with cursor(commit=True) as (_, cur):
        riga = funzione(cur, *args, **kwargs)
    if riga is None:
        raise _Fermo("lost_claim")
    return riga


def _errore_remoto(cursor, claim, riga, errore, connessione, operazione):
    c = classify(errore, operation=operazione)
    log.info("calendar_sync: sync=%s op=%s errore=%s azione=%s",
             claim.sync_id, operazione, c.code, c.action)
    if c.action == SUCCESS:
        return c
    if c.action == RETRY:
        tentativo = int(riga["attempt_count"]) + 1
        _registra(cursor, repository.mark_retry, claim, error_code=c.code,
                  delay_seconds=backoff_seconds(tentativo), exhausted=exhausted(tentativo))
        raise _Fermo("retrying" if not exhausted(tentativo) else "failed")
    if c.action == NEEDS_REAUTH:
        _registra(cursor, repository.mark_needs_reauth, claim,
                  connection_id=connessione["id"] if connessione else None, error_code=c.code)
        raise _Fermo("needs_reauth")
    assert c.action == FAILED
    _registra(cursor, repository.mark_failed, claim, error_code=c.code)
    raise _Fermo("failed")


def reconcile(claim, *, provider, keyring=None, cursor=core_cursor) -> str:
    """Riconcilia UNA riga presa con `claim_batch`. Restituisce l'esito:
    synced | waiting_connection | needs_reauth | detached | failed | retrying |
    pending (generazione cambiata) | lost_claim."""
    keyring_ref = [keyring]
    try:
        return _reconcile(claim, provider=provider, keyring_ref=keyring_ref, cursor=cursor)
    except _Fermo as fermo:
        return fermo.esito


def _reconcile(claim, *, provider, keyring_ref, cursor):
    agency = claim.agency_id
    # 1. LO STATO ATTUALE, in una transazione breve.
    with cursor(commit=True) as (_, cur):
        riga = repository.load_claimed(cur, claim)
        if riga is None:
            raise _Fermo("lost_claim")
        radice = riga["chain_root_appointment_id"]
        viva = repository.chain_tip(cur, agency, radice)
        appuntamento = repository.get_appointment(cur, agency, viva)
        agente = appuntamento["assigned_user_id"]
        desiderata = repository.connection_for_user(cur, agency, agente) if agente else None
        remota = (repository.get_connection(cur, agency, riga["remote_connection_id"])
                  if riga["remote_connection_id"] is not None else None)
        payload = None
        if appuntamento["status"] in k.APPOINTMENT_REMOTE_PRESENT:
            payload = build_payload(appuntamento, agency_id=agency,
                                    chain_root_appointment_id=radice,
                                    event_id=riga["remote_event_id"])
        hash_desiderato = payload_hash(payload) if payload is not None else None
        riga = repository.record_desired(cur, claim, current_appointment_id=viva,
                                         desired_payload_hash=hash_desiderato)
        if riga is None:
            raise _Fermo("lost_claim")

    piano = plan_reconciliation(appointment=appuntamento, sync_row=riga,
                                desired_connection=desiderata, remote_connection=remota,
                                desired_hash=hash_desiderato)

    # 2. FASE DELETE (riassegnazione o annullamento): prima si toglie.
    if piano.delete_on is not None:
        try:
            auth = _auth(cursor, claim, piano.delete_on, keyring_ref)
            try:
                provider.delete_event(auth, riga["remote_calendar_id"], riga["remote_event_id"])
            except CalendarProviderError as errore:
                # ritorna solo per SUCCESS (404/410 = gia' assente); ogni
                # altro esito e' registrato e interrompe il giro
                _errore_remoto(cursor, claim, riga, errore, piano.delete_on, "delete")
        except _Transitorio as t:
            return _transitorio(cursor, claim, riga, t.code)
        except crypto.CalendarCryptoNotConfigured:
            return _transitorio(cursor, claim, riga, "crypto_not_configured")
        except crypto.CalendarCryptoError:
            _registra(cursor, repository.mark_needs_reauth, claim,
                      connection_id=piano.delete_on["id"], error_code="token_unreadable")
            return "needs_reauth"
        riga = _registra(cursor, repository.record_remote_absent, claim)
    elif piano.abandon_remote:
        # Il calendario precedente non e' raggiungibile (connessione non piu'
        # valida): l'evento resta li', il mapping lo dimentica (D6 "se
        # possibile") e l'esito lo annota.
        riga = _registra(cursor, repository.record_remote_absent, claim)

    # 3. FASE ENSURE: l'evento esiste sul calendario desiderato.
    if piano.ensure_on is not None:
        try:
            auth = _auth(cursor, claim, piano.ensure_on, keyring_ref)
            try:
                esito = provider.ensure_event(auth, piano.ensure_on["calendar_id"], payload)
            except CalendarProviderError as errore:
                _errore_remoto(cursor, claim, riga, errore, piano.ensure_on, "ensure")
                return _transitorio(cursor, claim, riga, "ensure_inconclusive")
        except _Transitorio as t:
            return _transitorio(cursor, claim, riga, t.code)
        except crypto.CalendarCryptoNotConfigured:
            return _transitorio(cursor, claim, riga, "crypto_not_configured")
        except crypto.CalendarCryptoError:
            _registra(cursor, repository.mark_needs_reauth, claim,
                      connection_id=piano.ensure_on["id"], error_code="token_unreadable")
            return "needs_reauth"
        riga = _registra(cursor, repository.record_remote_present, claim,
                         connection_id=piano.ensure_on["id"],
                         calendar_id=piano.ensure_on["calendar_id"],
                         payload_hash=hash_desiderato, etag=esito.etag,
                         remote_updated_at=esito.remote_updated_at)

    # 4. CHIUSURA, con il controllo della generazione (D9).
    finale = _registra(cursor, repository.finalize, claim, final_status=piano.final_status,
                       error_code=piano.final_code)
    return finale["status"]


def _transitorio(cursor, claim, riga, code):
    tentativo = int(riga["attempt_count"]) + 1
    _registra(cursor, repository.mark_retry, claim, error_code=code,
              delay_seconds=backoff_seconds(tentativo), exhausted=exhausted(tentativo))
    return "failed" if exhausted(tentativo) else "retrying"


# ---------------------------------------------------------------------------
# UN GIRO DEL WORKER (il runner cron e' A30-9B)
# ---------------------------------------------------------------------------

def run_once(*, provider, limit: int = 10, lease_seconds: int = k.DEFAULT_LEASE_SECONDS,
             agency_id: int | None = None, keyring=None, cursor=core_cursor) -> list:
    """Prende un lotto (transazione breve, poi commit) e riconcilia riga per
    riga. Un errore inatteso su una riga non ferma le altre: la riga torna in
    retry con un codice generico, senza dettagli."""
    with cursor(commit=True) as (_, cur):
        prese = repository.claim_batch(cur, limit=limit, lease_seconds=lease_seconds,
                                       agency_id=agency_id)
    esiti = []
    for presa in prese:
        try:
            esiti.append((presa.sync_id, reconcile(presa, provider=provider, keyring=keyring,
                                                   cursor=cursor)))
        except Exception:  # noqa: BLE001 - nessun dettaglio (potrebbe contenere dati)
            log.exception("calendar_sync: errore interno sulla riga %s", presa.sync_id)
            try:
                with cursor(commit=True) as (_, cur):
                    riga = repository.load_claimed(cur, presa)
                    if riga is not None:
                        tentativo = int(riga["attempt_count"]) + 1
                        repository.mark_retry(cur, presa, error_code="internal_error",
                                              delay_seconds=backoff_seconds(tentativo),
                                              exhausted=exhausted(tentativo))
            except Exception:  # noqa: BLE001
                log.exception("calendar_sync: impossibile registrare l'errore della riga %s",
                              presa.sync_id)
            esiti.append((presa.sync_id, "internal_error"))
    return esiti
