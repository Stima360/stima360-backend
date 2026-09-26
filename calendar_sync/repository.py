"""A30-9A - accesso al database della sincronizzazione calendario.

Ogni funzione riceve il CURSORE: la transazione la apre e la chiude il
chiamante (il futuro hook dell'Agenda la usera' dentro la transazione della
mutazione; il riconciliatore in transazioni brevi, mai durante una chiamata
remota). Nessuna connessione nasce qui (P26 H11).

TENANT: ogni lettura o scrittura "di dominio" riceve `agency_id` esplicito e
lo mette nel WHERE. L'unica eccezione dichiarata e' `claim_batch`, la presa di
lavoro del worker di sistema, che puo' essere limitata a un'agenzia ma di
norma lavora su tutte: restituisce l'agenzia di ogni riga, e tutto cio' che
segue la usa.

Nessuna funzione tocca `appointments` in scrittura: la sincronizzazione non
cambia mai la `version` di un appuntamento ne' scrive `appointment_events`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from . import constants as k

SYNC_COLUMNS = (
    "id", "agency_id", "provider", "chain_root_appointment_id", "current_appointment_id",
    "remote_connection_id", "remote_calendar_id", "remote_event_id", "status",
    "dirty_generation", "synced_generation", "desired_payload_hash", "synced_payload_hash",
    "attempt_count", "next_attempt_at", "claim_token", "claimed_at", "last_error_code",
    "last_error_detail", "etag", "remote_updated_at", "last_synced_at", "created_at",
    "updated_at",
)
#: Le colonne di una connessione che escono dal repository: MAI il ciphertext,
#: salvo che nella funzione dedicata del riconciliatore.
CONNECTION_COLUMNS = (
    "id", "agency_id", "user_id", "provider", "provider_subject", "calendar_id",
    "token_key_id", "granted_scopes", "status", "last_error_code", "connected_at",
    "disconnected_at", "created_at", "updated_at",
)
_SYNC = ", ".join(SYNC_COLUMNS)
_CONN = ", ".join(CONNECTION_COLUMNS)

#: QUANDO UNA CONNESSIONE E' UTILIZZABILE, verificato AL MOMENTO DELL'USO (non
#: solo quando la connessione nasce): stato `connected`, token presente, e la
#: membership (agenzia, operatore) ATTUALMENTE attiva nel modello autorevole
#: `agency_memberships`. Una membership sospesa o revocata dopo il collegamento
#: rende la connessione inutilizzabile senza toccarne la riga; riattivata, la
#: stessa riga torna utilizzabile. `c` e' l'alias di `calendar_connections`.
_MEMBERSHIP_ATTIVA = (
    "EXISTS (SELECT 1 FROM agency_memberships m WHERE m.agency_id = c.agency_id "
    "AND m.operator_user_id = c.user_id AND m.status = 'active')")
_USABLE = ("(c.status = 'connected' AND c.refresh_token_ciphertext IS NOT NULL "
           f"AND {_MEMBERSHIP_ATTIVA})")
#: Le colonne di una connessione con i due flag calcolati a ogni lettura.
_CONN_C = (", ".join(f"c.{x}" for x in CONNECTION_COLUMNS)
           + f", {_MEMBERSHIP_ATTIVA} AS membership_active, {_USABLE} AS usable")

_MAX_ERROR_DETAIL = 300


class ChainNotFound(LookupError):
    """L'appuntamento non esiste in questa agenzia (o la catena e' rotta)."""


def _riga(r):
    return None if r is None else dict(r)


def sanitize_detail(testo) -> str | None:
    """Un dettaglio d'errore sicuro da salvare: una riga, stampabile, corto.
    I chiamanti ci passano solo testi propri (mai corpi di risposta remoti)."""
    if testo is None:
        return None
    pulito = "".join(c if c.isprintable() else " " for c in str(testo)).strip()
    return pulito[:_MAX_ERROR_DETAIL] or None


# ---------------------------------------------------------------------------
# CATENA (sola lettura su appointments)
# ---------------------------------------------------------------------------

def chain_root(cur, agency_id: int, appointment_id: int) -> int:
    """La radice della catena di `appointment_id` seguendo `rescheduled_from_id`
    all'indietro, SOLO dentro l'agenzia. Radice di un appuntamento mai
    spostato = se stesso."""
    cur.execute(
        """
        WITH RECURSIVE su AS (
            SELECT id, rescheduled_from_id, 0 AS profondita
              FROM appointments WHERE id = %(id)s AND agency_id = %(agency)s
            UNION ALL
            SELECT a.id, a.rescheduled_from_id, su.profondita + 1
              FROM appointments a JOIN su ON a.id = su.rescheduled_from_id
             WHERE a.agency_id = %(agency)s AND su.profondita < 1000
        )
        SELECT id FROM su WHERE rescheduled_from_id IS NULL
        """,
        {"id": appointment_id, "agency": agency_id},
    )
    r = cur.fetchone()
    if r is None:
        raise ChainNotFound(f"appuntamento {appointment_id} non trovato in questa agenzia")
    return int(r["id"])


def chain_tip(cur, agency_id: int, root_id: int) -> int:
    """La riga VIVA della catena: dalla radice in avanti lungo gli spostamenti
    (`rescheduled_from_id` e' UNIQUE: al massimo un successore)."""
    cur.execute(
        """
        WITH RECURSIVE giu AS (
            SELECT id, 0 AS profondita
              FROM appointments WHERE id = %(id)s AND agency_id = %(agency)s
            UNION ALL
            SELECT a.id, giu.profondita + 1
              FROM appointments a JOIN giu ON a.rescheduled_from_id = giu.id
             WHERE a.agency_id = %(agency)s AND giu.profondita < 1000
        )
        SELECT id FROM giu ORDER BY profondita DESC LIMIT 1
        """,
        {"id": root_id, "agency": agency_id},
    )
    r = cur.fetchone()
    if r is None:
        raise ChainNotFound(f"catena {root_id} non trovata in questa agenzia")
    return int(r["id"])


def get_appointment(cur, agency_id: int, appointment_id: int):
    """La riga di `appointments` (sola lettura) che il riconciliatore usa."""
    cur.execute(
        "SELECT id, agency_id, appointment_type, status, start_at, end_at, timezone, "
        "assigned_user_id, rescheduled_from_id, version "
        "FROM appointments WHERE id = %s AND agency_id = %s",
        (appointment_id, agency_id),
    )
    return _riga(cur.fetchone())


# ---------------------------------------------------------------------------
# RIGHE DI SYNC
# ---------------------------------------------------------------------------

def get_sync_row(cur, agency_id: int, root_id: int, *, provider=k.PROVIDER_GOOGLE):
    cur.execute(
        f"SELECT {_SYNC} FROM appointment_calendar_sync "
        "WHERE agency_id = %s AND provider = %s AND chain_root_appointment_id = %s",
        (agency_id, provider, root_id),
    )
    return _riga(cur.fetchone())


def get_sync_row_by_id(cur, agency_id: int, sync_id: int):
    cur.execute(
        f"SELECT {_SYNC} FROM appointment_calendar_sync WHERE id = %s AND agency_id = %s",
        (sync_id, agency_id),
    )
    return _riga(cur.fetchone())


def ensure_sync_row(cur, agency_id: int, appointment_id: int, *, deployment_namespace: str,
                    provider=k.PROVIDER_GOOGLE):
    """La riga della catena di `appointment_id`, creata se manca, BLOCCATA
    (FOR UPDATE) fino al commit del chiamante. L'id evento deterministico
    nasce qui, prima di qualunque chiamata remota, dal NAMESPACE DI
    DEPLOYMENT che il chiamante passa esplicitamente (A30-9B lo prendera'
    dalla configurazione runtime): due deployment diversi non condividono mai
    un id evento. Se la riga puntava a una riga viva non piu' attuale, la
    riallinea alla punta della catena."""
    from .service import deterministic_event_id

    deterministic_event_id(deployment_namespace, agency_id, 0)   # namespace valido, o errore
    root = chain_root(cur, agency_id, appointment_id)
    tip = chain_tip(cur, agency_id, root)
    cur.execute(
        "INSERT INTO appointment_calendar_sync "
        "(agency_id, provider, chain_root_appointment_id, current_appointment_id, "
        " remote_event_id) VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (agency_id, provider, chain_root_appointment_id) DO NOTHING",
        (agency_id, provider, root, tip,
         deterministic_event_id(deployment_namespace, agency_id, root)),
    )
    cur.execute(
        f"SELECT {_SYNC} FROM appointment_calendar_sync "
        "WHERE agency_id = %s AND provider = %s AND chain_root_appointment_id = %s "
        "FOR UPDATE",
        (agency_id, provider, root),
    )
    riga = dict(cur.fetchone())
    if riga["current_appointment_id"] != tip:
        cur.execute(
            f"UPDATE appointment_calendar_sync SET current_appointment_id = %s "
            f"WHERE id = %s RETURNING {_SYNC}",
            (tip, riga["id"]),
        )
        riga = dict(cur.fetchone())
    return riga


def mark_dirty_with_cursor(cur, agency_id: int, appointment_id: int, *,
                           deployment_namespace: str, provider=k.PROVIDER_GOOGLE):
    """Segna la catena come DA RICONCILIARE, nella transazione del chiamante.

    `dirty_generation += 1` sempre. Lo stato:
      * `syncing`  -> resta (il worker in corso vedra' la generazione cambiata
                      e non dichiarera' `synced`);
      * `detached` -> resta (la connessione e' stata scollegata: D14);
      * ogni altro -> `pending`, subito, con i tentativi azzerati.
    Due mark_dirty concorrenti si serializzano sul lock della riga: nessun
    incremento perso."""
    riga = ensure_sync_row(cur, agency_id, appointment_id,
                           deployment_namespace=deployment_namespace, provider=provider)
    cur.execute(
        f"""
        UPDATE appointment_calendar_sync SET
            dirty_generation = dirty_generation + 1,
            status = CASE WHEN status IN ('syncing', 'detached') THEN status ELSE 'pending' END,
            attempt_count = CASE WHEN status IN ('syncing', 'detached') THEN attempt_count ELSE 0 END,
            next_attempt_at = CASE WHEN status IN ('syncing', 'detached') THEN next_attempt_at ELSE NOW() END,
            last_error_code = CASE WHEN status IN ('syncing', 'detached') THEN last_error_code ELSE NULL END,
            last_error_detail = CASE WHEN status IN ('syncing', 'detached') THEN last_error_detail ELSE NULL END
         WHERE id = %s AND agency_id = %s
        RETURNING {_SYNC}
        """,
        (riga["id"], agency_id),
    )
    return dict(cur.fetchone())


def move_current_appointment_on_reschedule(cur, agency_id: int, old_id: int, new_id: int, *,
                                           provider=k.PROVIDER_GOOGLE):
    """D5: lo spostamento tiene lo STESSO evento remoto. La riga della catena
    passa dalla riga vecchia alla nuova; radice e id evento non cambiano.
    Richiede che `new_id` sia il successore diretto di `old_id` nella stessa
    agenzia. None se la catena non ha ancora una riga di sync."""
    cur.execute(
        "SELECT rescheduled_from_id FROM appointments WHERE id = %s AND agency_id = %s",
        (new_id, agency_id),
    )
    r = cur.fetchone()
    if r is None or r["rescheduled_from_id"] != old_id:
        raise ValueError(f"{new_id} non e' lo spostamento di {old_id} in questa agenzia")
    cur.execute(
        f"UPDATE appointment_calendar_sync SET current_appointment_id = %s "
        f"WHERE agency_id = %s AND provider = %s AND current_appointment_id = %s "
        f"RETURNING {_SYNC}",
        (new_id, agency_id, provider, old_id),
    )
    return _riga(cur.fetchone())


def request_resync(cur, agency_id: int, sync_id: int):
    """D12 (primitiva; la rotta arriva con A30-9B): riporta la riga `pending`,
    azzera i tentativi e dimentica l'hash sincronizzato, cosi' il prossimo giro
    ri-verifica l'evento remoto (e lo ricrea se sparito). Non chiama il
    remoto. Una riga `syncing` o `detached` resta com'e' (la generazione sale
    comunque)."""
    cur.execute(
        f"""
        UPDATE appointment_calendar_sync SET
            dirty_generation = dirty_generation + 1,
            synced_payload_hash = NULL,
            status = CASE WHEN status IN ('syncing', 'detached') THEN status ELSE 'pending' END,
            attempt_count = CASE WHEN status IN ('syncing', 'detached') THEN attempt_count ELSE 0 END,
            next_attempt_at = CASE WHEN status IN ('syncing', 'detached') THEN next_attempt_at ELSE NOW() END,
            last_error_code = CASE WHEN status IN ('syncing', 'detached') THEN last_error_code ELSE NULL END,
            last_error_detail = CASE WHEN status IN ('syncing', 'detached') THEN last_error_detail ELSE NULL END
         WHERE id = %s AND agency_id = %s
        RETURNING {_SYNC}
        """,
        (sync_id, agency_id),
    )
    return _riga(cur.fetchone())


# ---------------------------------------------------------------------------
# CODA: claim, lease, compare-and-set
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Claim:
    sync_id: int
    agency_id: int
    claim_token: str
    generation: int


def claim_batch(cur, *, limit: int = 10, lease_seconds: int = k.DEFAULT_LEASE_SECONDS,
                agency_id: int | None = None) -> list:
    """Prende fino a `limit` righe da lavorare: `pending`/`retrying` scadute, o
    `syncing` con lease scaduto (worker morto). `FOR UPDATE SKIP LOCKED`: due
    worker concorrenti non prendono mai la stessa riga. Ogni presa riceve un
    `claim_token` casuale (uuid4, 122 bit da os.urandom) e la generazione
    corrente, che il worker dovra' ripresentare."""
    limit = max(1, min(int(limit), 100))
    filtro, par = "", {"lease": int(lease_seconds), "limit": limit}
    if agency_id is not None:
        filtro, par["agency"] = " AND agency_id = %(agency)s", agency_id
    cur.execute(
        f"""
        SELECT id FROM appointment_calendar_sync
         WHERE ((status IN ('pending', 'retrying') AND next_attempt_at <= NOW())
             OR (status = 'syncing'
                 AND claimed_at < NOW() - make_interval(secs => %(lease)s))){filtro}
         ORDER BY next_attempt_at, id
         LIMIT %(limit)s
         FOR UPDATE SKIP LOCKED
        """,
        par,
    )
    ids = [r["id"] for r in cur.fetchall()]
    prese = []
    for sync_id in ids:
        token = str(uuid.uuid4())
        cur.execute(
            "UPDATE appointment_calendar_sync SET status = 'syncing', claim_token = %s, "
            "claimed_at = NOW() WHERE id = %s "
            "RETURNING id, agency_id, claim_token, dirty_generation",
            (token, sync_id),
        )
        r = cur.fetchone()
        prese.append(Claim(r["id"], r["agency_id"], str(r["claim_token"]),
                           int(r["dirty_generation"])))
    return prese


def load_claimed(cur, claim: Claim):
    """La riga, SOLO se il claim e' ancora nostro (altrimenti None: un altro
    worker l'ha ripresa dopo un lease scaduto)."""
    cur.execute(
        f"SELECT {_SYNC} FROM appointment_calendar_sync "
        "WHERE id = %s AND agency_id = %s AND status = 'syncing' AND claim_token = %s",
        (claim.sync_id, claim.agency_id, claim.claim_token),
    )
    return _riga(cur.fetchone())


def _cas(cur, claim: Claim, assegnazioni: str, valori: dict):
    """UPDATE compare-and-set sul claim: nessun effetto se il claim non e'
    piu' nostro. Restituisce la riga aggiornata o None."""
    par = dict(valori)
    par.update({"id": claim.sync_id, "agency": claim.agency_id, "token": claim.claim_token})
    cur.execute(
        f"UPDATE appointment_calendar_sync SET {assegnazioni} "
        "WHERE id = %(id)s AND agency_id = %(agency)s AND status = 'syncing' "
        f"AND claim_token = %(token)s RETURNING {_SYNC}",
        par,
    )
    return _riga(cur.fetchone())


def record_desired(cur, claim: Claim, *, current_appointment_id: int,
                   desired_payload_hash: str | None):
    """Il riconciliatore annota cosa vuole (hash del payload) e, se la riga
    viva era rimasta indietro, la riallinea. Nessun effetto remoto."""
    return _cas(cur, claim,
                "current_appointment_id = %(current)s, desired_payload_hash = %(hash)s",
                {"current": current_appointment_id, "hash": desired_payload_hash})


def record_remote_present(cur, claim: Claim, *, connection_id: int, calendar_id: str,
                          payload_hash: str, etag=None, remote_updated_at=None):
    """Dopo un ensure riuscito: l'evento ora vive su QUESTA connessione."""
    return _cas(cur, claim,
                "remote_connection_id = %(conn)s, remote_calendar_id = %(cal)s, "
                "synced_payload_hash = %(hash)s, etag = %(etag)s, "
                "remote_updated_at = %(upd)s",
                {"conn": connection_id, "cal": calendar_id, "hash": payload_hash,
                 "etag": etag, "upd": remote_updated_at})


def record_remote_absent(cur, claim: Claim):
    """Dopo un delete riuscito (o un evento gia' assente): nessun remoto."""
    return _cas(cur, claim,
                "remote_connection_id = NULL, remote_calendar_id = NULL, "
                "synced_payload_hash = NULL, etag = NULL, remote_updated_at = NULL", {})


def finalize(cur, claim: Claim, *, final_status: str, error_code: str | None = None,
             error_detail: str | None = None):
    """Chiude il lavoro sul claim (CAS). D9, OBBLIGATORIO: lo stato finale vale
    solo se `dirty_generation` e' ancora quella presa; se nel frattempo e'
    cresciuta la riga torna `pending` (subito), cosi' il prossimo giro
    riconcilia lo stato piu' recente. `synced_generation` registra comunque
    la generazione lavorata quando l'esito e' `synced`."""
    if final_status not in ("synced", "waiting_connection", "needs_reauth", "detached", "failed"):
        raise ValueError(f"stato finale non ammesso: {final_status}")
    return _cas(
        cur, claim,
        """
        synced_generation = CASE WHEN %(final)s = 'synced' THEN %(gen)s ELSE synced_generation END,
        status = CASE WHEN dirty_generation = %(gen)s THEN %(final)s ELSE 'pending' END,
        claim_token = NULL, claimed_at = NULL,
        attempt_count = CASE WHEN %(final)s = 'synced' THEN 0 ELSE attempt_count END,
        next_attempt_at = NOW(),
        last_error_code = %(code)s, last_error_detail = %(detail)s,
        last_synced_at = CASE WHEN %(final)s = 'synced' THEN NOW() ELSE last_synced_at END
        """,
        {"final": final_status, "gen": claim.generation, "code": error_code,
         "detail": sanitize_detail(error_detail)},
    )


def mark_retry(cur, claim: Claim, *, error_code: str, delay_seconds: int,
               exhausted: bool, error_detail: str | None = None):
    """Un fallimento transitorio: `attempt_count += 1`, `retrying` con
    `next_attempt_at = NOW() + delay`, oppure `failed` a tentativi esauriti."""
    return _cas(
        cur, claim,
        """
        attempt_count = attempt_count + 1,
        status = CASE WHEN %(fine)s THEN 'failed' ELSE 'retrying' END,
        claim_token = NULL, claimed_at = NULL,
        next_attempt_at = NOW() + make_interval(secs => %(delay)s),
        last_error_code = %(code)s, last_error_detail = %(detail)s
        """,
        {"fine": bool(exhausted), "delay": int(delay_seconds), "code": error_code,
         "detail": sanitize_detail(error_detail)},
    )


def mark_failed(cur, claim: Claim, *, error_code: str, error_detail: str | None = None):
    return _cas(
        cur, claim,
        "status = 'failed', claim_token = NULL, claimed_at = NULL, next_attempt_at = NOW(), "
        "last_error_code = %(code)s, last_error_detail = %(detail)s",
        {"code": error_code, "detail": sanitize_detail(error_detail)},
    )


def mark_needs_reauth(cur, claim: Claim, *, connection_id: int | None, error_code: str):
    """La riga aspetta una nuova autorizzazione; se il problema e' di UNA
    connessione, anche la connessione passa `needs_reauth` (stessa agenzia)."""
    riga = _cas(
        cur, claim,
        "status = 'needs_reauth', claim_token = NULL, claimed_at = NULL, "
        "next_attempt_at = NOW(), last_error_code = %(code)s, last_error_detail = NULL",
        {"code": error_code},
    )
    if riga is not None and connection_id is not None:
        mark_connection_needs_reauth(cur, claim.agency_id, connection_id, error_code=error_code)
    return riga


def mark_waiting_connection(cur, claim: Claim):
    return finalize(cur, claim, final_status="waiting_connection",
                    error_code="no_connection")


# ---------------------------------------------------------------------------
# CONNESSIONI
# ---------------------------------------------------------------------------

def upsert_connection(cur, *, agency_id: int, user_id: int, provider_subject: str,
                      refresh_token, granted_scopes=(), calendar_id: str = k.DEFAULT_CALENDAR_ID,
                      provider: str = k.PROVIDER_GOOGLE):
    """La connessione dell'operatore `user_id` nell'agenzia `agency_id`
    (UNIQUE per agenzia + operatore + provider): creata o ricollegata.
    `refresh_token` e' un `crypto.Ciphertext`, mai testo in chiaro. Che la
    membership sia ATTIVA lo verifica il trigger della 074. Per A30-9A e'
    usata dai test; il callback OAuth (A30-9B) sara' il suo chiamante."""
    from .crypto import Ciphertext

    if not isinstance(refresh_token, Ciphertext):
        raise TypeError("refresh_token deve essere un Ciphertext")
    cur.execute(
        f"""
        INSERT INTO calendar_connections
            (agency_id, user_id, provider, provider_subject, calendar_id,
             refresh_token_ciphertext, token_key_id, granted_scopes, status,
             connected_at, disconnected_at, last_error_code)
        VALUES (%(agency)s, %(user)s, %(provider)s, %(subject)s, %(cal)s,
                %(ct)s, %(kid)s, %(scopes)s, 'connected', NOW(), NULL, NULL)
        ON CONFLICT (agency_id, user_id, provider) DO UPDATE SET
            provider_subject = EXCLUDED.provider_subject,
            calendar_id = EXCLUDED.calendar_id,
            refresh_token_ciphertext = EXCLUDED.refresh_token_ciphertext,
            token_key_id = EXCLUDED.token_key_id,
            granted_scopes = EXCLUDED.granted_scopes,
            status = 'connected', connected_at = NOW(), disconnected_at = NULL,
            last_error_code = NULL
        RETURNING {_CONN}
        """,
        {"agency": agency_id, "user": user_id, "provider": provider,
         "subject": provider_subject, "cal": calendar_id,
         "ct": refresh_token.value, "kid": refresh_token.key_id,
         "scopes": list(granted_scopes)},
    )
    return dict(cur.fetchone())


def get_connection(cur, agency_id: int, connection_id: int):
    """La connessione (stessa agenzia) con `membership_active` e `usable`
    calcolati ORA."""
    cur.execute(f"SELECT {_CONN_C} FROM calendar_connections c "
                "WHERE c.id = %s AND c.agency_id = %s",
                (connection_id, agency_id))
    return _riga(cur.fetchone())


def connection_for_user(cur, agency_id: int, user_id: int, *, provider=k.PROVIDER_GOOGLE):
    """La connessione dell'operatore in QUESTA agenzia (qualunque stato), con
    `membership_active` e `usable` calcolati ORA."""
    cur.execute(
        f"SELECT {_CONN_C} FROM calendar_connections c "
        "WHERE c.agency_id = %s AND c.user_id = %s AND c.provider = %s",
        (agency_id, user_id, provider),
    )
    return _riga(cur.fetchone())


def usable_connection_for_user(cur, agency_id: int, user_id: int, *,
                               provider=k.PROVIDER_GOOGLE):
    """La connessione dell'operatore SOLO se utilizzabile adesso (vedi
    `_USABLE`); altrimenti None."""
    cur.execute(
        f"SELECT {_CONN_C} FROM calendar_connections c "
        f"WHERE c.agency_id = %s AND c.user_id = %s AND c.provider = %s AND {_USABLE}",
        (agency_id, user_id, provider),
    )
    return _riga(cur.fetchone())


def connection_secret(cur, agency_id: int, connection_id: int):
    """Il ciphertext del refresh token, SOLO per il riconciliatore, SOLO nella
    stessa agenzia e SOLO se la connessione e' utilizzabile IN QUESTO ISTANTE
    (stato, token e membership attiva ricontrollati subito prima della
    decifratura). None altrimenti: il token non esce."""
    cur.execute(
        "SELECT c.refresh_token_ciphertext, c.token_key_id FROM calendar_connections c "
        f"WHERE c.id = %s AND c.agency_id = %s AND {_USABLE}",
        (connection_id, agency_id),
    )
    r = cur.fetchone()
    if r is None or r["refresh_token_ciphertext"] is None:
        return None
    return bytes(r["refresh_token_ciphertext"]), r["token_key_id"]


def mark_connection_needs_reauth(cur, agency_id: int, connection_id: int, *, error_code: str):
    cur.execute(
        "UPDATE calendar_connections SET status = 'needs_reauth', last_error_code = %s "
        "WHERE id = %s AND agency_id = %s AND status = 'connected'",
        (error_code, connection_id, agency_id),
    )


def mark_connection_disconnected(cur, agency_id: int, connection_id: int):
    """D14 (primitiva; la rotta arriva con A30-9B): il token locale e'
    rimosso, la connessione disabilitata, le righe che hanno l'evento su quel
    calendario diventano `detached` (i mapping restano per audit e reconnect).
    Gli eventi remoti restano dove sono. La revoca remota e' del chiamante."""
    cur.execute(
        "UPDATE calendar_connections SET status = 'disconnected', "
        "refresh_token_ciphertext = NULL, token_key_id = NULL, disconnected_at = NOW() "
        "WHERE id = %s AND agency_id = %s AND status <> 'disconnected'",
        (connection_id, agency_id),
    )
    cur.execute(
        "UPDATE appointment_calendar_sync SET status = 'detached', last_error_code = 'disconnected' "
        "WHERE agency_id = %s AND remote_connection_id = %s AND status <> 'syncing'",
        (agency_id, connection_id),
    )


# ---------------------------------------------------------------------------
# INTENTI OAUTH (monouso)
# ---------------------------------------------------------------------------

def create_oauth_state(cur, *, agency_id: int, user_id: int, state_hash: str,
                       code_verifier, ttl_seconds: int = k.OAUTH_STATE_TTL_SECONDS,
                       provider: str = k.PROVIDER_GOOGLE):
    """Salva SOLO l'hash dello state e il verifier PKCE cifrato."""
    from .crypto import Ciphertext

    if not isinstance(code_verifier, Ciphertext):
        raise TypeError("code_verifier deve essere un Ciphertext")
    cur.execute(
        "INSERT INTO calendar_oauth_states (agency_id, user_id, provider, state_hash, "
        " code_verifier_ciphertext, token_key_id, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, NOW() + make_interval(secs => %s)) "
        "RETURNING id, agency_id, user_id, provider, state_hash, token_key_id, "
        "expires_at, used_at, created_at",
        (agency_id, user_id, provider, state_hash, code_verifier.value,
         code_verifier.key_id, int(ttl_seconds)),
    )
    return dict(cur.fetchone())


def consume_oauth_state(cur, *, agency_id: int, user_id: int, state_hash: str,
                        provider: str = k.PROVIDER_GOOGLE):
    """Consuma l'intento: solo se esiste, e' di QUESTA agenzia e di QUESTO
    operatore, non e' scaduto e non e' gia' stato usato. Monouso anche fra
    richieste concorrenti (FOR UPDATE). None in ogni altro caso, senza dire
    quale (nessun oracolo). Restituisce il verifier cifrato."""
    cur.execute(
        "SELECT id, code_verifier_ciphertext, token_key_id FROM calendar_oauth_states "
        "WHERE state_hash = %s AND agency_id = %s AND user_id = %s AND provider = %s "
        "AND used_at IS NULL AND expires_at > NOW() FOR UPDATE",
        (state_hash, agency_id, user_id, provider),
    )
    r = cur.fetchone()
    if r is None:
        return None
    cur.execute("UPDATE calendar_oauth_states SET used_at = NOW() WHERE id = %s AND used_at IS NULL",
                (r["id"],))
    if cur.rowcount != 1:
        return None
    return {"id": r["id"], "code_verifier_ciphertext": bytes(r["code_verifier_ciphertext"]),
            "token_key_id": r["token_key_id"]}
