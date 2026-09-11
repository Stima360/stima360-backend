"""Le dipendenze FastAPI che trasformano una credenziale in uno scope server-side.

Qualunque sia il canale, lo scope si decide qui, sul server. Niente legge
un'agenzia, un ruolo o un utente da un parametro, da un header o da un corpo:
un `agency_id` che arrivasse dallo stato del client e' inaffidabile per
costruzione, non per disciplina.

P26-5: DA DUE CANALI A UNO.

Fino a P26-4 questo modulo ne conosceva due - il cookie di sessione e la
credenziale condivisa ADMIN_USER/ADMIN_PASS - e la parte difficile era la loro
precedenza. P26-3 la scrisse in un posto solo; la sua revisione dovette
aggiungere una regola perche' un cookie RIFIUTATO non ricadesse sul Basic della
stessa richiesta; e OWNER Admin ebbe bisogno di uno scope apposta per non
finire ammessa da una credenziale e scopata dall'altra.

Adesso la scelta non esiste: tutti i client sono migrati - la OS Shell in
P26-3, i cinque frontend amministrativi in P26-5 - quindi un segreto condiviso
non autentica piu' alcuna route di tenant. Ed e' questo il punto, non
l'eleganza: finche' una password sola apriva le porte di un'agenzia, la
piattaforma non poteva ospitarne una seconda.

Le dipendenze, e la divisione fra loro:

* `optional_session` - l'unico punto in cui un cookie diventa uno scope.
  Restituisce una sessione viva o None, non solleva mai, e passa da uno schema
  `APIKeyCookie`: quello schema e' un `SecurityBase`, quindi ogni route che
  dichiara questa dipendenza porta con se' il proprio `security` nell'OpenAPI.
* `current_session` - sessione o 401. Per gli endpoint che servono la sessione
  in se' e non uno scope, come `/me`.
* `require_operator` - lo scope che ogni endpoint CORE dichiara.
* `require_authenticated_operator` - ammissione al mount, senza scope e senza
  database. E' la forma dell'allargamento di D-1: prima di P26-3 la sessione
  raggiungeva solo `/api/operator-auth` e `/api/core`, adesso anche gli undici
  router che la OS Shell chiama, e il confine resta una lista esplicita di
  mount in `main.py`.
* `legacy_basic_agency_context` - lo scope di ogni router fuori da CORE. Il
  nome e' storico: cambiarlo nello stesso diff che ne cambiava il
  comportamento avrebbe sepolto una decisione dentro un'edit meccanica su
  centocinquanta route.
* `require_owner_admin_context` - OWNER Admin: sessione piu' ruolo minimo
  `agency_owner`. Ammissione e scope decisi insieme, cosi' non possono
  divergere.

`_default_agency_context` e `_verify_legacy_credentials` restano per l'unica
superficie non-tenant ancora sul canale precedente (`/api/admin/check`), e
`admin_security.require_admin` resta la sola definizione di cosa sia quella
credenziale.

GATE-MA1 e' ancora APERTO, e adesso per una ragione sola. Il residuo
strutturale - un segreto condiviso capace di autenticare una route di tenant -
non c'e' piu': quella meta' del gate l'ha chiusa P26-5. Quel che manca e'
l'esito della matrice ostile live A/B su TEST, che e' l'unica prova che
l'isolamento regga sui dati veri e non solo nel codice. Nessuna seconda
agenzia reale e' autorizzata finche' quella prova non esiste.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyCookie, HTTPBasicCredentials

from admin_security import require_admin
from core.scope import resolve_default_agency_id

from . import service
from .context import OperatorContext
from .database import operator_cursor
from .enums import COOKIE_NAME

# The role the compatibility credential presents as. The legacy credential is
# the agency's own administrative login, so it maps to the agency's owner - and
# never to platform_admin, which would make it cross-agency (design spec D-2).
LEGACY_BASIC_ROLE = "agency_owner"

# One message for every rejection: absent cookie, unknown, revoked, expired,
# idle, disabled account, suspended membership, suspended agency. A caller
# learns only that they are not authenticated.
#
# The wording and the challenge header below are byte-identical to what
# admin_security.require_admin returned before P26-1. /api/core kept that
# contract through the transition, and the OS Shell relies on the challenge to
# prompt for the legacy credential - a 401 without it simply fails silently in
# a browser.
NOT_AUTHENTICATED_MESSAGE = "Non autorizzato"

# P26-5: la soglia di ruolo di OWNER Admin. Vedi `require_owner_admin_context`
# per le tre fonti concordi da cui e' ricavata.
OWNER_ADMIN_MIN_ROLE = "agency_owner"
OWNER_ADMIN_FORBIDDEN_MESSAGE = "Operazione riservata al titolare dell'agenzia"
BASIC_CHALLENGE = {"WWW-Authenticate": 'Basic realm="STIMA360 Admin"'}

# P26-5: il cookie di sessione dichiarato come SCHEMA DI SICUREZZA, non letto
# di nascosto dalla richiesta.
#
# Fino a P26-4 lo `security` dell'OpenAPI arrivava gratis dallo schema
# `HTTPBasic`: era un `SecurityBase`, quindi ogni route che dichiarava quella
# dipendenza si portava dietro la propria dichiarazione. Tolto il Basic, senza
# nulla al suo posto CENTOTTO route sarebbero diventate "senza sicurezza" agli
# occhi di chiunque legga il documento - client generati, gateway, revisori -
# pur essendo protette esattamente come prima.
#
# `APIKeyCookie` e' anch'esso un `SecurityBase`, e descrive la verita': questa
# API si autentica con un cookie. `auto_error=False` perche' il rifiuto lo
# formulano le dipendenze, ciascuna col proprio codice.
_cookie_scheme = APIKeyCookie(name=COOKIE_NAME, auto_error=False)


@dataclass(frozen=True)
class AuthenticatedSession:
    """A resolved session: the caller's scope plus what /me may display.

    `agency_name` and `expires_at` are held here rather than on OperatorContext
    because a scope is an authorisation decision. Adding presentation fields to
    it would mean every scoped repository call carried data it must never use,
    and would invite exactly the kind of drift the frozen field list prevents.
    """

    context: OperatorContext
    agency_name: str | None
    expires_at: datetime


def optional_session(
    raw_token: str | None = Depends(_cookie_scheme),
) -> AuthenticatedSession | None:
    """Resolve the session cookie if there is a live one, else None.

    The single point where a cookie becomes a scope, and the only place that
    reads the cookie at all. Both `current_session` and `require_operator`
    derive from it rather than resolving independently, so FastAPI's
    per-request dependency cache means one database resolution per request no
    matter how many dependencies a handler declares.

    Returns None - never a partial result - for an absent, unknown, revoked,
    expired or idle-timed-out session. Turning that None into a 401 is the
    caller's decision: `current_session` e `require_operator` rifiutano, mentre
    `/me` e i router che vogliono distinguere lo usano cosi' com'e'.

    P26-5: il valore arriva da `_cookie_scheme` invece che da `request.cookies`.
    E' lo stesso cookie e lo stesso valore, ma passando da uno schema di
    sicurezza la dipendenza si porta dietro la dichiarazione `security`
    nell'OpenAPI - che prima veniva dallo schema Basic e sarebbe sparita con
    esso.
    """
    if not raw_token:
        return None

    resolved = service.session_from_token(raw_token)
    if resolved is None:
        return None

    return AuthenticatedSession(
        context=resolved["context"],
        agency_name=resolved["agency_name"],
        expires_at=resolved["expires_at"],
    )


def current_session(
    session: AuthenticatedSession | None = Depends(optional_session),
) -> AuthenticatedSession:
    """The caller's session, or 401. Session cookie only - no legacy channel.

    Used by endpoints that need the session itself rather than a scope, such
    as /me. Basic is deliberately not accepted here: the legacy credential is
    a shared secret with no session behind it, so there is nothing to report.
    """
    if session is None:
        raise HTTPException(status_code=401, detail=NOT_AUTHENTICATED_MESSAGE)
    return session


def require_operator(
    session: AuthenticatedSession | None = Depends(optional_session),
) -> OperatorContext:
    """The authenticated caller's scope, from either approved channel.

    Precedence, and the reasoning for it:

    1. **A valid operator session cookie.** A real person with a real agency
       and a real role. It is tried first so that an operator who also happens
       to send Basic is never silently downgraded to the shared credential's
       Default-Agency scope.
    2. **Valid legacy `ADMIN_USER`/`ADMIN_PASS`.** The OS Shell has not yet
       migrated to the cookie session (design spec section 18), so this channel
       must keep working on `/api/core`. It resolves to an agency-*bound*
       context: Default Agency by slug, `agency_owner`, never platform admin,
       with `user_id` and `session_id` left None because the credential is a
       shared secret rather than a person (D-2).
    3. **Otherwise 401**, with the same message for every cause.

    Fail-closed at each step. An invalid or revoked cookie does not lock out a
    caller who also sent valid Basic - it simply is not a session - but it can
    never itself become authority. Nothing here reads an agency, a role or a
    user from the request.

    It depends on `optional_session` rather than `current_session` so that an
    absent or dead cookie falls through to the Basic branch instead of raising
    401 inside a nested dependency - and, because that dependency is shared and
    cached, a handler declaring both still costs one session resolution.

    The Basic credential arrives through FastAPI's own `HTTPBasic` scheme
    rather than by parsing the header here. That is what keeps the OpenAPI
    document honest: the scheme is a `SecurityBase`, so every route declaring
    this dependency continues to advertise `security`, exactly as it did when
    `require_admin` guarded it. A hand-rolled header parse authenticates just
    as well and silently drops that declaration - which is how CORE briefly
    came to look unauthenticated to anything reading the schema.
    """
    context = _scope_from_session(session)
    if context is None:
        raise HTTPException(status_code=401, detail=NOT_AUTHENTICATED_MESSAGE)
    return context


def require_authenticated_operator(
    session: AuthenticatedSession | None = Depends(optional_session),
) -> None:
    """Mount-level admission for the routers the OS Shell calls. No scope.

    P26-3 needed every one of those routers to accept the session cookie, and
    the obvious way - mounting them on `require_operator` - would have made
    each request resolve the Default Agency twice on the legacy channel: once
    for the mount, once for the route's own `legacy_basic_agency_context`.
    FastAPI caches per callable, and those are two different callables.

    So this one answers the only question a mount has to answer - may this
    caller in at all - and answers it without touching the database: a session
    is resolved once, by the cached `optional_session`, and the legacy branch is
    a string comparison. The scope still comes from the route's own dependency,
    which is where it belongs and where tests already override it.

    Returns None on purpose. A mount-level dependency whose value nothing reads
    should not look like it produces one.
    """
    if session is not None:
        return None

    # P26-5: il rifiuto non passa piu' da `require_admin`.
    #
    # Fino a P26-4 delegava a `require_admin`, per tenere il 401 byte-identico a
    # quello di P26-1 e conservarne il 503 quando il server non aveva credenziali
    # amministrative configurate. Adesso questa superficie non conosce piu'
    # quella credenziale, e delegarle il rifiuto significherebbe che l'assenza
    # di ADMIN_PASS puo' ancora cambiare la risposta di una route che con
    # ADMIN_PASS non c'entra nulla. Il 503 resta dove resta il canale.
    raise HTTPException(status_code=401, detail=NOT_AUTHENTICATED_MESSAGE)


def _scope_from_session(
    session: AuthenticatedSession | None,
) -> OperatorContext | None:
    """Lo scope del chiamante, o None. UNA sola sorgente: la sessione.

    P26-5 HA TOLTO IL SECONDO RAMO.

    Fino a P26-4 questa funzione sceglieva fra due canali - il cookie di
    sessione e la credenziale condivisa ADMIN_USER/ADMIN_PASS - e la parte
    difficile era la precedenza: quale vince, cosa succede con un cookie
    rifiutato, come si evita che una meta' della richiesta sia decisa da una
    credenziale e l'altra meta' dall'altra.

    Quella difficolta' non esiste piu', perche' non esiste piu' la scelta.
    Tutti i client sono migrati - la OS Shell in P26-3, i cinque frontend
    amministrativi in P26-5 - quindi un segreto condiviso non autentica piu'
    alcuna route di tenant. Ed e' questo il punto, non l'eleganza: finche' una
    password sola apriva le porte di un'agenzia, la piattaforma non poteva
    ospitarne una seconda.

    Non solleva mai per "non autenticato": ritorna None e lascia al chiamante
    la formulazione del rifiuto.
    """
    return session.context if session is not None else None


def _default_agency_context() -> OperatorContext:
    """The legacy Basic scope: Default Agency, agency owner, never platform admin.

    Every field is decided here, on the server:

    * `agency_id` is resolved from the Default Agency slug. Not a parameter,
      not a header, not a numeric constant.
    * `role` is the agency owner, never platform admin, so the scope is
      agency-bound and can never reach the cross-agency branch.
    * `user_id` and `session_id` are None: the credential is a shared secret,
      not a person, and recording an invented operator would be a lie.
    """
    with operator_cursor() as (_, cur):
        agency_id = resolve_default_agency_id(cur)

    return OperatorContext(
        user_id=None,
        agency_id=agency_id,
        role=LEGACY_BASIC_ROLE,
        is_platform_admin=False,
        session_id=None,
        auth_channel="legacy_basic",
    )


def require_owner_admin_context(
    session: AuthenticatedSession | None = Depends(optional_session),
) -> OperatorContext:
    """Lo scope di OWNER Admin: sessione, con ruolo almeno `agency_owner`.

    PERCHE' UN RUOLO, E PERCHE' PROPRIO QUESTO

    OWNER Admin non e' una superficie operativa qualunque: ci si gestiscono i
    conti dei proprietari, si emettono i token con cui quei proprietari entrano
    nel portale, e si leggono i documenti che hanno caricato. Ammetterla a
    qualunque sessione autenticata sarebbe un'escalation, ed e' il motivo per
    cui P26-3 la lascio' fuori dall'allargamento di D-1:
    `require_authenticated_operator` verifica che il chiamante sia autenticato,
    non cosa gli e' permesso fare.

    La soglia non e' inventata. Viene, in quest'ordine:

    1. `operator_auth/permissions.py`, la matrice approvata: "Manage agency
       users" e' YES per platform_admin e agency_owner, LIMITED per
       agency_admin e **NO per agent**.
    2. Il comportamento storico: questa superficie era autenticata dalla
       credenziale condivisa, che `LEGACY_BASIC_ROLE` mappa esattamente su
       `agency_owner`. Chi ci entrava prima entrava con quel ruolo, quindi
       chiedere quel ruolo non toglie accesso a nessuno e non lo allarga.
    3. Il minimo privilegio, che fra due letture compatibili sceglie la
       stretta: `agency_admin` ha "LIMITED" sui membri, non pieno, e i conti
       proprietario non sono membri dell'agenzia.

    Le tre fonti concordano, quindi non c'e' ambiguita' da rimettere a una
    decisione di prodotto: platform admin oppure `agency_owner`, e chiunque
    altro riceve 403 - non 401, perche' e' autenticato benissimo: non e'
    autorizzato.

    Sostituisce `basic_only_agency_context`, che esisteva per evitare uno stato
    ibrido cookie+Basic. Quello stato adesso e' impossibile per costruzione.
    """
    context = _scope_from_session(session)
    if context is None:
        raise HTTPException(status_code=401, detail=NOT_AUTHENTICATED_MESSAGE)

    if context.is_platform_admin or context.role == OWNER_ADMIN_MIN_ROLE:
        return context

    raise HTTPException(status_code=403, detail=OWNER_ADMIN_FORBIDDEN_MESSAGE)


def _verify_legacy_credentials(credentials: HTTPBasicCredentials) -> str | None:
    """Return the verified legacy username, or None.

    The comparison is delegated to `admin_security.require_admin`, which P26-1
    leaves untouched and which remains the single definition of what the legacy
    credential is. This only turns "raise 401" into "return None", so a failed
    Basic attempt falls through to one 401 rather than producing a second,
    differently-worded one.

    A 503 is deliberately re-raised rather than swallowed. It means the server
    has no admin credentials configured at all, which is an operational fault -
    reporting it as "not authenticated" would send an operator hunting for the
    wrong problem, and it is the answer this path gave before P26-1.
    """
    try:
        return require_admin(credentials)
    except HTTPException as exc:
        if exc.status_code == 503:
            raise
        return None


def legacy_basic_agency_context(
    session: AuthenticatedSession | None = Depends(optional_session),
) -> OperatorContext:
    """Lo scope di ogni router fuori da CORE. P26-5: SOLO la sessione.

    IL NOME E' STORICO, E RESTA DI PROPOSITO.

    Questa dipendenza e' dichiarata da circa centocinquanta route. P26-3 ne ha
    cambiato il comportamento tenendone il nome, perche' rinominarla nello
    stesso diff che ne alterava la sicurezza avrebbe sepolto una decisione
    dentro una modifica meccanica. P26-5 fa la stessa scelta per la stessa
    ragione: qui sparisce il canale Basic, che e' il cambiamento da leggere, e
    il rinomino resta un commit cosmetico separato.

    * P26-1 - P26-2: risolveva la Default Agency dalla credenziale condivisa.
    * P26-3 - P26-4: la sessione se c'era, altrimenti quella credenziale.
    * P26-5: **la sessione, e nient'altro.** Nessun fallback, nessuna
      precedenza da spiegare, nessuna combinazione cookie+Basic possibile.

    L'agenzia e' quella dell'operatore che ha fatto login, non piu' la Default
    Agency di un segreto condiviso - ed e' questo che rende una seconda agenzia
    reale una possibilita' tecnica invece che un rischio.
    """
    context = _scope_from_session(session)
    if context is None:
        raise HTTPException(status_code=401, detail=NOT_AUTHENTICATED_MESSAGE)
    return context


def audit_actor(
    context: OperatorContext = Depends(legacy_basic_agency_context),
) -> str:
    """Chi ha fatto l'operazione, per l'audit trail. P26-5.

    Prima era `actor: str = Depends(require_admin)`: lo username Basic, cioe'
    la stringa "giorgio" per chiunque conoscesse la password condivisa. Un
    audit trail che registra sempre lo stesso nome non e' un audit trail, e
    quando le agenzie diventano due diventa attivamente fuorviante - due
    persone di due aziende diverse comparirebbero come lo stesso attore.

    Adesso e' l'operatore della sessione, nella stessa forma che la OS Shell
    gia' usa in P26-3 per `activated_by`: `operator:<user_id>`. L'id e non
    l'email di proposito, perche' `/me` esclude deliberatamente l'email e un
    audit trail non e' il posto dove reintrodurre un dato personale.
    """
    return f"operator:{context.user_id}"
