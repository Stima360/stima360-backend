"""Login, session resolution and logout.

This layer owns the transaction and is the only place a raw session token or a
plaintext password exists. Neither is ever logged, and neither is ever passed
to the repository: the repository receives a SHA-256 token hash and an encoded
PBKDF2 password hash.

There is deliberately no logging in this module at all. An authentication
failure is the one event most likely to be logged with its cause attached, and
the cause is exactly what must not be recorded or disclosed.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from core.normalization import normalize_email

from . import repository, security
from .context import OperatorContext
from .database import operator_cursor
from .enums import AGENCY_STATUS_ACTIVE, SESSION_IDLE_MINUTES, SESSION_MAX_HOURS
from .exceptions import AuthenticationFailed


# A real PBKDF2 hash of an unguessable value, computed once when this module is
# imported. When an email is unknown the password is verified against this
# instead of skipping the check, so a request for a non-existent account costs
# the same 600k iterations as a request for a real one and login timing does
# not disclose which addresses are registered.
#
# Computed once per process, never per request: regenerating it on each failed
# login would reintroduce the timing difference it exists to remove, and would
# hand an attacker a cheap way to make the server do unbounded work.
_DUMMY_PASSWORD_HASH = security.hash_password(secrets.token_urlsafe(32))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _scope_is_usable(row: dict) -> bool:
    """True when the account, its membership and its agency all permit access.

    A platform admin needs no membership: platform administration sits outside
    the agency hierarchy. Everyone else needs an active membership in an active
    agency.
    """
    if row.get("status") != "active" and row.get("user_status") != "active":
        return False
    if row.get("is_platform_admin"):
        return True
    return (
        row.get("membership_status") == "active"
        and row.get("agency_status") == "active"
    )


def login(email: str, password: str) -> str:
    """Authenticate and return the raw session token.

    The raw token is returned to the caller (the router, which puts it in a
    Set-Cookie header) and nowhere else. Only its hash is persisted.

    Raises AuthenticationFailed, identically, for every failure.
    """
    normalized = normalize_email(email)

    with operator_cursor(commit=True) as (_, cur):
        operator = (
            repository.find_operator_by_email(cur, normalized) if normalized else None
        )

        # Always verify, even with no account: the hashing cost must not depend
        # on whether the address exists.
        stored = operator["password_hash"] if operator else _DUMMY_PASSWORD_HASH
        password_ok = security.verify_password(password, stored)

        if operator is None or not password_ok or not _scope_is_usable(operator):
            raise AuthenticationFailed()

        raw_token = security.generate_session_token()
        repository.create_session(
            cur,
            operator["id"],
            security.hash_session_token(raw_token),
            _utcnow() + timedelta(hours=SESSION_MAX_HOURS),
        )
        repository.mark_login(cur, operator["id"])
        return raw_token


def logout(raw_token: str | None) -> None:
    """Revoke a session. Silent whether or not the token existed.

    Returns None in every case: a caller cannot learn from logout whether a
    token was real.
    """
    if not raw_token:
        return

    with operator_cursor(commit=True) as (_, cur):
        repository.revoke_session(cur, security.hash_session_token(raw_token))


def session_from_token(raw_token: str | None) -> dict | None:
    """Resolve a cookie value into the caller's scope plus session display data.

    Returns None - never a partial result - for an absent, unknown, revoked,
    expired or idle-timed-out session, for a disabled account, and for an
    agency operator whose membership or agency is no longer active.

    The idle window is advanced only after the session has fully validated, so
    a rejected request cannot keep a dead session alive.

    The returned mapping carries the scope under "context" plus the values /me
    needs that are not part of the scope itself: the agency's display name, the
    session's expiry and - since P28 - the acting and home agencies. They are
    returned separately, rather than added to OperatorContext, because a scope
    is an authorisation decision and must not accumulate presentation fields. A
    plain dict keeps this module free of any dependency on the HTTP layer that
    consumes it.

    P28 - DOVE SI DECIDE L'AGENZIA EFFETTIVA.

    Un platform admin puo' avere DICHIARATO di stare operando dentro
    un'agenzia. Se quella dichiarazione e' ancora legittima, l'agenzia effettiva
    e' quella: `context.agency_id` la porta, e da li' in poi tutto il prodotto -
    centocinquanta route, un solo predicate - la usa senza sapere come ci sia
    finita. Se non lo e' piu', viene CANCELLATA qui, dentro la transazione che
    questa funzione gia' possiede.

    L'attore non cambia mai: `user_id` resta la persona vera, ed e' quello che
    finisce negli audit.
    """
    if not raw_token:
        return None

    with operator_cursor(commit=True) as (_, cur):
        row = repository.resolve_session(
            cur, security.hash_session_token(raw_token), SESSION_IDLE_MINUTES
        )
        if row is None:
            return None

        # L'ORDINE FRA QUESTE DUE RIGHE E' UNA CORREZIONE, NON UNO STILE.
        #
        # Prima erano invertite: una sessione non piu' utilizzabile usciva
        # subito, e l'acting restava scritto in riga. Sembra innocuo - quella
        # sessione non risolve piu' - ma non lo e': togliere
        # `is_platform_admin` a chi non ha membership rende la sessione
        # inutilizzabile PRIMA che qualcuno guardi l'acting, quindi il
        # contesto non veniva cancellato; e il giorno in cui quel flag
        # tornasse, la sessione tornerebbe valida CON DENTRO l'agenzia di
        # prima - senza un nuovo ingresso e senza una riga di audit che lo
        # racconti.
        #
        # Adesso l'acting viene giudicato per primo, e la sua legittimita'
        # include l'utilizzabilita' della sessione: un contesto di
        # impersonazione non sopravvive a cio' che lo reggeva.
        usabile = _scope_is_usable(row)
        acting = _acting_or_cleared(cur, row, usabile=usabile)
        if not usabile:
            return None

        repository.touch_session(cur, row["session_id"])
        return {
            "context": OperatorContext(
                user_id=row["user_id"],
                # L'agenzia EFFETTIVA: quella visitata se c'e', altrimenti la
                # propria. Non c'e' un terzo caso, e non c'e' nessun punto in
                # cui le due siano vere insieme.
                agency_id=(
                    acting["agency_id"] if acting else row["agency_id"]
                ),
                # IL RUOLO DI CASA NON SEGUE CHI VIAGGIA.
                #
                # Dentro un'agenzia visitata non si ha un ruolo: non c'e' una
                # membership da cui prenderlo. Lasciare quello di casa sarebbe
                # un difetto vero e non una sbavatura - `scoped_predicate`
                # restringe le letture di un `agent` ai record assegnati a lui,
                # e un platform admin che a casa sua e' agente vedrebbe
                # dell'agenzia ospite esattamente nulla, senza che niente lo
                # segnali. L'autorita' qui viene da `is_platform_admin`, che la
                # matrice dei permessi legge per primo.
                role=None if acting else row["role"],
                is_platform_admin=bool(row["is_platform_admin"]),
                session_id=row["session_id"],
                auth_channel="operator_session",
            ),
            # Il nome dell'agenzia in cui si sta operando: e' quello che la
            # Shell scrive nella barra, e deve essere l'ospite quando si e'
            # ospiti, altrimenti la barra direbbe il posto sbagliato.
            "agency_name": (
                acting["agency_name"] if acting else row.get("agency_name")
            ),
            "expires_at": row["expires_at"],
            "acting_agency_id": acting["agency_id"] if acting else None,
            "acting_agency_name": acting["agency_name"] if acting else None,
            "acting_entered_at": acting["entered_at"] if acting else None,
            # L'appartenenza vera, sempre, anche mentre si e' altrove: e' come
            # la UI puo' dire "sei Giorgio di Casa, dentro Ospite" invece di
            # far sparire meta' della frase.
            "home_agency_id": row.get("agency_id"),
            "home_agency_name": row.get("agency_name"),
        }


def _acting_or_cleared(cur, row: dict, *, usabile: bool) -> dict | None:
    """L'agenzia visitata se la dichiarazione regge ancora, altrimenti None.

    E QUANDO NON REGGE, LA CANCELLA.

    E' la differenza fra "ignorato" e "revocato", ed e' una differenza che si
    vede il giorno dopo: un acting ignorato resta scritto nella riga, e
    riattivare l'agenzia rimetterebbe dentro qualcuno che non ha chiesto di
    rientrare. Cancellarlo significa che per tornarci serve un nuovo ingresso,
    con la sua riga di audit.

    Le tre condizioni sono rilette a ogni richiesta perche' stanno nella stessa
    query che rilegge membership e stato agenzia:

    * la SESSIONE deve essere ancora utilizzabile (`usabile`). Un contesto di
      impersonazione non sopravvive a cio' che lo reggeva: se l'account e'
      disabilitato, o se togliere `is_platform_admin` a chi non ha membership
      rende la sessione inservibile, l'acting sparisce insieme. Senza questa
      condizione resterebbe scritto in riga e tornerebbe in vita il giorno in
      cui la sessione tornasse valida - senza un nuovo ingresso e senza una
      riga di audit che lo racconti.
    * chi impersona deve ESSERE ANCORA platform admin. Tolto il flag, l'acting
      non e' piu' di nessuno.
    * l'agenzia visitata deve essere ANCORA `active`. Sospenderla la spegne per
      i suoi stessi operatori: lasciarla aperta a un visitatore sarebbe il
      contrario di cio' che sospenderla significa.

    Fail-closed: qualunque dubbio produce None, cioe' il ritorno alla
    membership - che per un platform admin che non ne ha e' 403 sul tenant.
    """
    if row.get("acting_agency_id") is None:
        return None

    if not usabile:
        repository.clear_acting_context(cur, row["session_id"])
        return None

    if not row.get("is_platform_admin"):
        repository.clear_acting_context(cur, row["session_id"])
        return None

    if row.get("acting_agency_status") != AGENCY_STATUS_ACTIVE:
        repository.clear_acting_context(cur, row["session_id"])
        return None

    return {
        "agency_id": int(row["acting_agency_id"]),
        "agency_name": row.get("acting_agency_name"),
        "entered_at": row.get("acting_entered_at"),
    }


def context_from_token(raw_token: str | None) -> OperatorContext | None:
    """Resolve a cookie value into the caller's effective scope, or None."""
    resolved = session_from_token(raw_token)
    return resolved["context"] if resolved else None
