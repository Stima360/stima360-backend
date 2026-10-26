"""Operatori, membership, ruoli e titolare. P27-3.

Le regole di dominio del Network vivono qui. L'ordine fra scrittura, audit e
commit vive in `platform_admin/transaction.py` ed e' condiviso con P27-2.

QUATTRO DECISIONI CHE QUESTO MODULO PRENDE, E PERCHE'

1. UNA PERSONA, UNA RIGA. `operator_users.email_normalized` e' UNIQUE su tutta
   la tabella, non per agenzia: il login riceve un'email e nient'altro, quindi
   due righe con la stessa email renderebbero ambigua la risoluzione. Creare un
   operatore con un'email gia' presente NON crea una seconda persona: riusa
   quella che c'e'.

2. NESSUNO SPOSTAMENTO IMPLICITO. Se quella persona ha gia' una membership
   ATTIVA in un'altra agenzia, la richiesta si ferma con 409. Non si revoca la
   precedente e non si sposta nessuno: `uq_agency_memberships_single_active`
   dice che una membership attiva alla volta, e trasferire qualcuno fra due
   affiliati e' una decisione commerciale che deve essere chiesta, non un
   effetto collaterale di una creazione.

3. NESSUN DUPLICATO, NESSUNA RIATTIVAZIONE DI CONTRABBANDO. Se esiste gia' una
   riga fra quell'operatore e QUESTA agenzia - attiva, sospesa o revocata - la
   creazione risponde 409 e indica la PATCH della membership. Riattivare un
   rapporto revocato assegnandogli per giunta un ruolo nuovo, dentro una
   chiamata che si chiama "crea", e' il genere di scorciatoia che si scopre
   dopo. UNIQUE (agency_id, operator_user_id) non viene mai aggirato.

4. IL TITOLARE SI TRASFERISCE, NON SI ASSEGNA. `uq_agency_memberships_single_owner`
   ammette un solo titolare attivo per agenzia, e una PATCH che mettesse
   `role='agency_owner'` dovrebbe degradare qualcun altro in silenzio per
   riuscirci. Quella PATCH quindi rifiuta il ruolo di titolare e rimanda
   all'operazione dedicata, che fa le due cose insieme e le audita come una.

CIO' CHE QUESTO MODULO NON FA

Non cancella. `revoked` e `disabled` sono stati, non DELETE. Non tocca
`operator_auth`: la regola per cui un operatore `disabled` smette di poter
lavorare e' gia' li' - `resolve_session` rilegge lo stato dell'utente a ogni
richiesta - e riscriverla qui significherebbe averne due che possono divergere.
Non normalizza email e non calcola hash: usa le funzioni che gia' esistono.
"""
from __future__ import annotations

from typing import Any

from psycopg2 import errors

from core.normalization import normalize_email
from operator_auth import repository as session_repository
from operator_auth.context import OperatorContext
from operator_auth.security import hash_password

from . import agencies_repository, operators_repository, super_admin
from .database import platform_operation_cursor
from .enums import (
    ACTION_MEMBERSHIP_CREATE,
    ACTION_MEMBERSHIP_UPDATE,
    ACTION_OPERATOR_CREATE,
    ACTION_OPERATOR_PLATFORM_ADMIN_UPDATE,
    ACTION_OPERATOR_DELETE,
    ACTION_OPERATOR_RESTORE,
    ACTION_OPERATOR_UPDATE,
    ACTION_OWNER_TRANSFER,
    ACTIVE_MEMBERSHIP_ELSEWHERE_MESSAGE,
    AGENCY_DELETED_NOT_USABLE_MESSAGE,
    AGENCY_NOT_FOUND_MESSAGE,
    AGENCY_STATUS_ARCHIVED,
    AUDIT_PLATFORM_ADMIN_CHANGE_KEY,
    EMAIL_TAKEN_MESSAGE,
    LAST_SUPER_ADMIN_MESSAGE,
    MEMBERSHIP_ACTIVE,
    MEMBERSHIP_EXISTS_MESSAGE,
    MEMBERSHIP_NOT_FOUND_MESSAGE,
    OPERATOR_ACTIVE,
    OPERATOR_DELETE_NEEDS_DISABLED_MESSAGE,
    OPERATOR_DELETE_PROTECTED_MESSAGE,
    OPERATOR_DELETE_UNAVAILABLE_MESSAGE,
    OPERATOR_DELETED_MESSAGE,
    OPERATOR_DISABLED,
    OPERATOR_NOT_DELETED_MESSAGE,
    OPERATOR_NOT_FOUND_MESSAGE,
    PROTECTED_SUPER_ADMIN_EMAIL,
    OWNER_ALREADY_EXISTS_MESSAGE,
    OWNER_MUST_BE_ACTIVE_MEMBER_MESSAGE,
    OWNER_ROLE_NEEDS_TRANSFER_MESSAGE,
    PASSWORD_ON_EXISTING_IDENTITY_MESSAGE,
    PLATFORM_ADMIN_DESIGNATED_MESSAGE,
    PLATFORM_ADMIN_LAST_MESSAGE,
    PLATFORM_ADMIN_NEEDS_ACTIVE_MESSAGE,
    PLATFORM_ADMIN_NEEDS_LOGIN_MESSAGE,
    PLATFORM_ADMIN_UNCHANGED_MESSAGE,
    PASSWORD_REQUIRED_MESSAGE,
    ROLE_AGENCY_ADMIN,
    ROLE_AGENCY_OWNER,
    TARGET_TYPE_MEMBERSHIP,
    TARGET_TYPE_OPERATOR,
)
from .exceptions import (
    AgencyNotFound,
    MembershipNotFound,
    OperatorDeleteUnavailable,
    OperatorNotFound,
    PasswordRequired,
    PlatformConflict,
)
from .transaction import audit_then_commit, status_change

# I vincoli di `operator_users` e `agency_memberships` che rappresentano un
# conflitto di DOMINIO, e il messaggio che ciascuno merita.
#
# La mappa esiste per non catturare genericamente ogni violazione di integrita'
# trasformandola in 409. Un vincolo che non e' qui dentro non e' un conflitto
# previsto: e' un difetto, e deve salire come tale invece di travestirsi da
# "richiesta in conflitto" e mandare qualcuno a cercare un problema di dati.
CONFLICT_CONSTRAINTS = {
    "operator_users_email_unq": EMAIL_TAKEN_MESSAGE,
    "agency_memberships_unq": MEMBERSHIP_EXISTS_MESSAGE,
    "uq_agency_memberships_single_active": ACTIVE_MEMBERSHIP_ELSEWHERE_MESSAGE,
    "uq_agency_memberships_single_owner": OWNER_ALREADY_EXISTS_MESSAGE,
}


def _as_conflict(exc: errors.UniqueViolation) -> PlatformConflict:
    """Traduce una violazione ATTESA in un conflitto di dominio, o rilancia.

    Il nome del vincolo si legge da `exc.diag.constraint_name`, che psycopg2
    riporta dal messaggio strutturato di PostgreSQL. Se non c'e' - driver
    diverso, errore rimaneggiato - la violazione NON viene indovinata: risale.
    Un 409 inventato su un vincolo sconosciuto direbbe al chiamante che deve
    cambiare la richiesta, quando magari deve cambiarla chi ha scritto il
    codice.

    Il messaggio restituito viene sempre dalle costanti: quello di PostgreSQL
    contiene il nome del vincolo, quello della tabella e il valore in
    conflitto.
    """
    constraint = getattr(getattr(exc, "diag", None), "constraint_name", None)
    message = CONFLICT_CONSTRAINTS.get(constraint)
    if message is None:
        raise exc
    return PlatformConflict(message)


def _require_agency(cur, agency_id: int) -> dict[str, Any]:
    """L'agenzia deve esistere. Il suo STATO non conta.

    Decisione esplicita di P27-3: la superficie Platform amministra anche gli
    operatori di un'agenzia sospesa o archiviata. E' il momento in cui serve di
    piu' - si sospende un affiliato proprio quando qualcosa non va, e dover
    prima riattivarlo per sistemarne l'organico sarebbe un giro assurdo che
    nel frattempo riapre il tenant.

    Questo non allenta nulla sul lato tenant: `operator_auth` continua a
    richiedere `agency_status='active'` perche' una sessione sia utilizzabile,
    e P27-3 non tocca quella regola.
    """
    agency = agencies_repository.get_agency(cur, agency_id)
    if agency is None:
        raise AgencyNotFound(AGENCY_NOT_FOUND_MESSAGE)
    return agency


# ---------------------------------------------------------------------------
# Letture
# ---------------------------------------------------------------------------

def list_agency_operators(agency_id: int) -> list[dict[str, Any]]:
    with platform_operation_cursor() as (_, cur):
        _require_agency(cur, agency_id)
        return operators_repository.list_agency_operators(cur, agency_id)


def list_operators(*, without_active_membership: bool, limit: int, offset: int) -> list[dict[str, Any]]:
    with platform_operation_cursor() as (_, cur):
        return operators_repository.list_operators(
            cur, without_active_membership=without_active_membership, limit=limit, offset=offset
        )


def get_operator(operator_user_id: int) -> dict[str, Any]:
    """Identita' piu' TUTTE le sue membership, in ogni agenzia e in ogni stato.

    Non filtrate per agenzia: questa route non e' sotto `/agencies/{id}`, e la
    domanda che risponde e' "chi e' questa persona nella rete" - alla quale
    un elenco potato non risponde.
    """
    with platform_operation_cursor() as (_, cur):
        operator = operators_repository.get_operator(cur, operator_user_id)
        if operator is None:
            raise OperatorNotFound(OPERATOR_NOT_FOUND_MESSAGE)
        memberships = operators_repository.list_memberships_of_operator(
            cur, operator_user_id
        )
    return {"operator": operator, "memberships": memberships}


# ---------------------------------------------------------------------------
# Creazione / assegnazione
# ---------------------------------------------------------------------------

def create_agency_operator(
    actor: OperatorContext,
    agency_id: int,
    *,
    email: str,
    password: str | None,
    first_name: str | None,
    last_name: str | None,
    operator_status: str,
    role: str,
    created_fields: list[str],
) -> dict[str, Any]:
    """Crea o riusa l'operatore, e lo lega all'agenzia. Una transazione sola.

    I DUE CASI, E DUE AZIONI DI AUDIT DIVERSE

    * email nuova     -> nasce un `operator_users` E la membership.
                         Azione  `platform.operator.create`
                         Target  operator / operator_user_id
                         La password SERVE: senza, 422.
    * email gia' nota -> la persona e' quella, nasce SOLO la membership.
                         Azione  `platform.membership.create`
                         Target  agency_membership / membership_id
                         La password non deve essere inviata: se c'e', 409.

    L'azione dice cosa e' realmente accaduto. Una sola azione
    `platform.operator.create` con un metadato a correggerne il senso
    significherebbe che meta' delle righe del registro affermano una cosa falsa
    nel campo che si legge per primo - e in una tabella append-only non c'e' un
    secondo momento in cui rimediarlo.

    Resta UNA riga per richiesta in entrambi i casi: e' una operazione di
    business sola, e due righe direbbero due fatti veri nascondendo che sono
    successi insieme o per niente.

    LA CREDENZIALE DI UNA PERSONA ESISTENTE NON SI TOCCA MAI DA QUI.

    Sul percorso "email gia' nota" non c'e' nessuna scrittura su
    `operator_users`: ne' della password ne' di altro. Una password inviata
    comunque viene rifiutata esplicitamente, e non ignorata - ignorarla
    lascerebbe credere di aver impostato una credenziale che nessuno ha
    toccato, applicarla renderebbe questa route un reimposta-password
    implicito.

    La password non compare in questa funzione oltre la riga che la trasforma
    in hash: non viene loggata, non entra nell'audit, non torna nella risposta.
    """
    normalized = normalize_email(email)
    if not normalized:
        # Lo schema rifiuta gia' un'email vuota; qui si copre il caso in cui la
        # normalizzazione la riduca a nulla (soli spazi), che lo schema non
        # puo' sapere senza duplicare la normalizzazione.
        raise PlatformConflict(EMAIL_TAKEN_MESSAGE)

    with platform_operation_cursor() as (conn, cur):
        super_admin.lock_super_admin(cur, actor, audit_action=ACTION_OPERATOR_CREATE)
        # ELIMINA AGENZIA: l'organico di un'agenzia eliminata si amministra
        # (revoche, disattivazioni), ma non vi entra nessuno di nuovo.
        if _require_agency(cur, agency_id)["status"] == AGENCY_STATUS_ARCHIVED:
            raise PlatformConflict(AGENCY_DELETED_NOT_USABLE_MESSAGE)

        existing = operators_repository.find_operator_by_normalized_email(
            cur, normalized
        )

        # Il contratto della credenziale, deciso qui perche' solo qui si sa
        # chi e' quella email. I due rifiuti sono deterministici e non
        # dipendono dall'ordine in cui il client ha messo i campi.
        if existing is not None and existing.get("deleted_at") is not None:
            # ELIMINA OPERATORE: un eliminato non entra in un'agenzia.
            raise PlatformConflict(OPERATOR_DELETED_MESSAGE)
        if existing is not None and password is not None:
            raise PlatformConflict(PASSWORD_ON_EXISTING_IDENTITY_MESSAGE)
        if existing is None and password is None:
            raise PasswordRequired(PASSWORD_REQUIRED_MESSAGE)

        if existing is None:
            try:
                operator = operators_repository.create_operator(
                    cur,
                    email=email.strip(),
                    email_normalized=normalized,
                    password_hash=hash_password(password),
                    first_name=first_name,
                    last_name=last_name,
                    status=operator_status,
                )
            except errors.UniqueViolation as exc:
                raise _as_conflict(exc) from exc
        else:
            operator = existing

        _guard_new_membership(cur, agency_id, operator["id"])

        try:
            membership = operators_repository.create_membership(
                cur,
                agency_id=agency_id,
                operator_user_id=operator["id"],
                role=role,
                status=MEMBERSHIP_ACTIVE,
            )
        except errors.UniqueViolation as exc:
            raise _as_conflict(exc) from exc

        # L'azione E IL TARGET descrivono entrambi cio' che e' stato creato.
        #
        # Non basta che l'azione sia giusta: una riga che dicesse
        # `membership.create` puntando all'operatore sarebbe incoerente con se
        # stessa, perche' l'operatore su quel percorso esisteva gia' e nessuno
        # lo ha creato. Il target e' quindi l'oggetto nuovo - la persona nel
        # primo caso, la membership nel secondo - e in entrambi i casi
        # `target_agency_id` dice dove.
        if existing is None:
            action = ACTION_OPERATOR_CREATE
            target_type = TARGET_TYPE_OPERATOR
            target_id = operator["id"]
        else:
            action = ACTION_MEMBERSHIP_CREATE
            target_type = TARGET_TYPE_MEMBERSHIP
            target_id = membership["id"]

        audit_then_commit(
            conn,
            actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            target_agency_id=agency_id,
            # Solo nomi di campo. Il VALORE del ruolo era qui e non ci deve
            # stare: `agent` o `agency_owner` sono contenuto della richiesta, e
            # una tabella append-only non e' il posto in cui farlo entrare per
            # comodita' di lettura. Che il ruolo sia stato indicato si vede
            # comunque, perche' il suo nome compare in `created_fields`.
            metadata={"created_fields": sorted(created_fields)},
        )
        return {"operator": operator, "membership": membership}


def _guard_new_membership(cur, agency_id: int, operator_user_id: int) -> None:
    """I due conflitti che una membership nuova puo' incontrare.

    Controlli applicativi, che esistono per il MESSAGGIO: i vincoli di
    database dicono la stessa cosa, ma la dicono nominando un indice. Restano
    entrambi - questi per il caso normale, quelli per la corsa.
    """
    if operators_repository.get_membership(cur, agency_id, operator_user_id):
        # Attiva, sospesa o revocata: la coppia e' occupata, e la si modifica
        # dalla PATCH invece di scriverne una seconda.
        raise PlatformConflict(MEMBERSHIP_EXISTS_MESSAGE)

    if operators_repository.active_membership_elsewhere(
        cur, operator_user_id, excluding_agency_id=agency_id
    ):
        raise PlatformConflict(ACTIVE_MEMBERSHIP_ELSEWHERE_MESSAGE)


# ---------------------------------------------------------------------------
# Aggiornamento identita'
# ---------------------------------------------------------------------------

def update_operator(
    actor: OperatorContext, operator_user_id: int, fields: dict[str, Any]
) -> dict[str, Any]:
    """Aggiorna nome, cognome e stato dell'operatore.

    Non l'email e non `is_platform_admin`: non sono campi di questo schema e
    non sono colonne aggiornabili del repository. Vedi
    `operators_repository.OPERATOR_UPDATABLE_COLUMNS`.

    Portare un operatore a `disabled` non richiede niente di piu' di questa
    UPDATE: le sue sessioni smettono di funzionare alla richiesta successiva
    perche' `operator_auth.repository.resolve_session` rilegge `u.status` ogni
    volta e `_scope_is_usable` rifiuta tutto cio' che non e' `active`. Non c'e'
    una seconda invalidazione da scrivere, e scriverla significherebbe averne
    due che possono divergere.

    ADM-1 ha aggiunto la seconda, perche' la prima da sola non bastava: il
    ricontrollo ferma la sessione finche' l'account e' disabilitato, ma alla
    riattivazione la stessa sessione tornava valida. Ogni cambio di stato
    revoca ora le sessioni dell'operatore, nella stessa transazione: dopo una
    riattivazione si rifa' l'accesso. E l'account del Super Admin generale
    designato non lascia `active` da questa superficie, nemmeno per mano sua.
    """
    with platform_operation_cursor() as (conn, cur):
        super_admin.lock_super_admin(cur, actor, audit_action=ACTION_OPERATOR_UPDATE)
        current = operators_repository.lock_operator(cur, operator_user_id)
        if current is None:
            raise OperatorNotFound(OPERATOR_NOT_FOUND_MESSAGE)

        status_changes = "status" in fields and fields["status"] != current["status"]
        if (status_changes and fields["status"] != OPERATOR_ACTIVE
                and super_admin.is_designated(operator_user_id)):
            raise PlatformConflict(LAST_SUPER_ADMIN_MESSAGE)
        # ELIMINA OPERATORE: un eliminato non si riattiva senza ripristino.
        if current.get("deleted_at") is not None and fields.get("status") == OPERATOR_ACTIVE:
            raise PlatformConflict(OPERATOR_DELETED_MESSAGE)

        operator = operators_repository.update_operator(cur, operator_user_id, fields)
        if operator is None:
            raise OperatorNotFound(OPERATOR_NOT_FOUND_MESSAGE)

        if status_changes:
            session_repository.revoke_operator_sessions(cur, operator_user_id)

        audit_then_commit(
            conn,
            actor,
            action=ACTION_OPERATOR_UPDATE,
            target_type=TARGET_TYPE_OPERATOR,
            target_id=operator_user_id,
            target_agency_id=None,
            metadata={"changed_fields": sorted(fields),
                      **status_change(current["status"], operator["status"])},
        )
        return operator


# ---------------------------------------------------------------------------
# ELIMINA OPERATORE - eliminazione logica (migration 113)
# ---------------------------------------------------------------------------

def _is_protected_account(operator: dict[str, Any]) -> bool:
    """Il Super Admin designato e l'account `info@stima360.it` non si eliminano."""
    return (super_admin.is_designated(operator["id"])
            or normalize_email(operator.get("email") or "") == PROTECTED_SUPER_ADMIN_EMAIL)


def delete_operator(actor: OperatorContext, operator_user_id: int) -> dict[str, Any]:
    """Elimina LOGICAMENTE un operatore gia' escluso dalla Rete (`disabled`).

    Scrive solo `operator_users.deleted_at`/`deleted_by_user_id` e revoca le
    sessioni residue, nella stessa transazione e con la sua riga di registro.
    Membership, contatti, immobili, incarichi, appuntamenti e storico non
    cambiano: l'eliminato esce dagli elenchi operativi, non accede (e' e resta
    `disabled`) e non e' assegnabile (le assegnazioni richiedono un account
    attivo). Ripetere il comando non cambia nulla.
    """
    with platform_operation_cursor() as (conn, cur):
        super_admin.lock_super_admin(cur, actor, audit_action=ACTION_OPERATOR_DELETE)
        if not operators_repository.elimination_mark_supported(cur):
            raise OperatorDeleteUnavailable(OPERATOR_DELETE_UNAVAILABLE_MESSAGE)
        current = operators_repository.lock_operator(cur, operator_user_id)
        if current is None:
            raise OperatorNotFound(OPERATOR_NOT_FOUND_MESSAGE)
        if _is_protected_account(current):
            raise PlatformConflict(OPERATOR_DELETE_PROTECTED_MESSAGE)
        if current["status"] != OPERATOR_DISABLED:
            raise PlatformConflict(OPERATOR_DELETE_NEEDS_DISABLED_MESSAGE)
        if current.get("deleted_at") is not None:
            return current

        operator = operators_repository.set_elimination_mark(
            cur, operator_user_id, deleted_by=actor.user_id)
        session_repository.revoke_operator_sessions(cur, operator_user_id)
        audit_then_commit(
            conn,
            actor,
            action=ACTION_OPERATOR_DELETE,
            target_type=TARGET_TYPE_OPERATOR,
            target_id=operator_user_id,
            target_agency_id=None,
            metadata={"deleted": True},
        )
        return operator


def restore_deleted_operator(actor: OperatorContext, operator_user_id: int) -> dict[str, Any]:
    """Annulla l'eliminazione: l'account torna fra gli esclusi (`disabled`).
    Per farlo accedere di nuovo si usa il «Ripristina operatore» esistente."""
    with platform_operation_cursor() as (conn, cur):
        super_admin.lock_super_admin(cur, actor, audit_action=ACTION_OPERATOR_RESTORE)
        if not operators_repository.elimination_mark_supported(cur):
            raise OperatorDeleteUnavailable(OPERATOR_DELETE_UNAVAILABLE_MESSAGE)
        current = operators_repository.lock_operator(cur, operator_user_id)
        if current is None:
            raise OperatorNotFound(OPERATOR_NOT_FOUND_MESSAGE)
        if current.get("deleted_at") is None:
            raise PlatformConflict(OPERATOR_NOT_DELETED_MESSAGE)

        operator = operators_repository.set_elimination_mark(cur, operator_user_id, deleted_by=None)
        audit_then_commit(
            conn,
            actor,
            action=ACTION_OPERATOR_RESTORE,
            target_type=TARGET_TYPE_OPERATOR,
            target_id=operator_user_id,
            target_agency_id=None,
            metadata={"deleted": False},
        )
        return operator


# ---------------------------------------------------------------------------
# Aggiornamento membership
# ---------------------------------------------------------------------------

def set_platform_admin(
    actor: OperatorContext, operator_user_id: int, value: bool
) -> dict[str, Any]:
    """ADM-6. Concede o toglie `is_platform_admin`. Solo il Super Admin designato.

    E' il passo 2 (promozione) e il passo 5 (revoca) della sostituzione del
    Super Admin: la designazione resta in `STIMA360_SUPER_ADMIN_USER_ID`, e
    cambiarla e' un atto di configurazione, non di questa superficie.

    Guardie, tutte nella stessa transazione e dopo `lock_super_admin`, su cui
    ogni modifica della Rete si serializza:

    * concedere: l'account e' `active` e ha gia' fatto almeno un login riuscito;
    * togliere: mai al designato, e mai all'ultimo amministratore attivo;
    * nessun no-op: un flag gia' com'e' richiesto e' 409, non un 200 vuoto.

    Togliere il flag revoca tutte le sessioni dell'operatore: un amministratore
    retrocesso non resta dentro con la sessione di prima. La membership non si
    tocca: chi era membro di un'agenzia lo resta.
    """
    with platform_operation_cursor() as (conn, cur):
        super_admin.lock_super_admin(
            cur, actor, audit_action=ACTION_OPERATOR_PLATFORM_ADMIN_UPDATE)
        current = operators_repository.lock_operator(cur, operator_user_id)
        if current is None:
            raise OperatorNotFound(OPERATOR_NOT_FOUND_MESSAGE)

        prima = bool(current["is_platform_admin"])
        if prima == bool(value):
            raise PlatformConflict(PLATFORM_ADMIN_UNCHANGED_MESSAGE)

        if value:
            if current["status"] != OPERATOR_ACTIVE:
                raise PlatformConflict(PLATFORM_ADMIN_NEEDS_ACTIVE_MESSAGE)
            if current.get("last_login_at") is None:
                raise PlatformConflict(PLATFORM_ADMIN_NEEDS_LOGIN_MESSAGE)
        else:
            if super_admin.is_designated(operator_user_id):
                raise PlatformConflict(PLATFORM_ADMIN_DESIGNATED_MESSAGE)
            if operators_repository.count_active_platform_admins(
                    cur, excluding=operator_user_id) == 0:
                raise PlatformConflict(PLATFORM_ADMIN_LAST_MESSAGE)

        operator = operators_repository.set_platform_admin(cur, operator_user_id, value)
        if operator is None:
            raise OperatorNotFound(OPERATOR_NOT_FOUND_MESSAGE)

        if not value:
            session_repository.revoke_operator_sessions(cur, operator_user_id)

        audit_then_commit(
            conn,
            actor,
            action=ACTION_OPERATOR_PLATFORM_ADMIN_UPDATE,
            target_type=TARGET_TYPE_OPERATOR,
            target_id=operator_user_id,
            target_agency_id=None,
            metadata={"changed_fields": ["is_platform_admin"],
                      AUDIT_PLATFORM_ADMIN_CHANGE_KEY: {"from": prima, "to": bool(value)}},
        )
        return operator


def update_membership(
    actor: OperatorContext,
    agency_id: int,
    operator_user_id: int,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Cambia ruolo e/o stato della membership fra quell'operatore e l'agenzia.

    IL RUOLO DI TITOLARE NON PASSA DI QUI.

    Assegnarlo richiederebbe di degradare il titolare in carica, e farlo dentro
    una PATCH che parla di una persona sola significherebbe cambiare lo stato
    di un'altra senza nominarla. Risponde 409 e indica l'operazione dedicata.

    LA RIATTIVAZIONE E' IL CASO DELICATO.

    Riportare `active` una membership sospesa o revocata e' l'unica strada per
    rimettere qualcuno al lavoro, ma `uq_agency_memberships_single_active`
    ammette una sola membership attiva per persona: se nel frattempo e' entrata
    in un'altra agenzia, la riattivazione e' 409 e non una revoca silenziosa
    dell'altra.

    ADM-1: un cambio di stato della membership revoca le sessioni
    dell'operatore nella stessa transazione, salvo per un platform admin, la
    cui sessione non dipende mai da una membership.
    """
    with platform_operation_cursor() as (conn, cur):
        super_admin.lock_super_admin(cur, actor, audit_action=ACTION_MEMBERSHIP_UPDATE)
        _require_agency(cur, agency_id)
        person = operators_repository.lock_operator(cur, operator_user_id)

        membership = operators_repository.get_membership(
            cur, agency_id, operator_user_id
        )
        if membership is None:
            raise MembershipNotFound(MEMBERSHIP_NOT_FOUND_MESSAGE)

        if fields.get("role") == ROLE_AGENCY_OWNER:
            raise PlatformConflict(OWNER_ROLE_NEEDS_TRANSFER_MESSAGE)

        _guard_reactivation(cur, membership, fields, agency_id, operator_user_id)

        try:
            updated = operators_repository.update_membership(
                cur, membership["id"], fields
            )
        except errors.UniqueViolation as exc:
            raise _as_conflict(exc) from exc

        if updated is None:
            raise MembershipNotFound(MEMBERSHIP_NOT_FOUND_MESSAGE)

        if (updated["status"] != membership["status"] and person is not None
                and not person["is_platform_admin"]):
            session_repository.revoke_operator_sessions(cur, operator_user_id)

        audit_then_commit(
            conn,
            actor,
            action=ACTION_MEMBERSHIP_UPDATE,
            target_type=TARGET_TYPE_OPERATOR,
            target_id=operator_user_id,
            target_agency_id=agency_id,
            metadata={"changed_fields": sorted(fields),
                      **status_change(membership["status"], updated["status"])},
        )
        return updated


def _guard_reactivation(
    cur, membership, fields, agency_id: int, operator_user_id: int
) -> None:
    """I due conflitti di una riattivazione, quando e' davvero una riattivazione.

    Il controllo scatta solo se la riga NON era gia' attiva: una PATCH che
    tocca il ruolo di una membership attiva non sta riattivando niente, e
    farle attraversare questi controlli le farebbe trovare se stessa.
    """
    if fields.get("status") != MEMBERSHIP_ACTIVE:
        return
    if membership["status"] == MEMBERSHIP_ACTIVE:
        return

    if operators_repository.active_membership_elsewhere(
        cur, operator_user_id, excluding_agency_id=agency_id
    ):
        raise PlatformConflict(ACTIVE_MEMBERSHIP_ELSEWHERE_MESSAGE)

    # Riattivare una riga il cui ruolo e' gia' `agency_owner` rimetterebbe un
    # secondo titolare attivo. Il vincolo lo impedisce; qui si spiega perche'.
    becoming_owner = fields.get("role", membership["role"]) == ROLE_AGENCY_OWNER
    if becoming_owner and operators_repository.active_owner_of(cur, agency_id):
        raise PlatformConflict(OWNER_ALREADY_EXISTS_MESSAGE)


# ---------------------------------------------------------------------------
# Trasferimento titolare
# ---------------------------------------------------------------------------

def transfer_owner(
    actor: OperatorContext, agency_id: int, new_owner_user_id: int
) -> dict[str, Any]:
    """Rende B il titolare dell'agenzia, degradando A ad `agency_admin`.

    UNA TRANSAZIONE, E UN ORDINE CHE NON E' NEGOZIABILE.

    `uq_agency_memberships_single_owner` e' un indice unico parziale, verificato
    a ogni istruzione. Promuovere B prima di degradare A lo violerebbe subito;
    degradando per primo, la sequenza attraversa uno stato con ZERO titolari,
    che l'indice ammette. Quello stato non viene mai committato: le due
    istruzioni stanno nella stessa transazione, e chi legge da fuori vede
    l'agenzia passare da A a B senza passi intermedi.

    COSA SERVE A B, E COSA NON SUCCEDE SE NON CE L'HA

    B deve gia' avere una membership ATTIVA in QUESTA agenzia. Se non ce l'ha,
    l'operazione si ferma: inventargliene una qui significherebbe far entrare
    qualcuno in un'agenzia da una route che parla di titolarita', saltando i
    controlli che la creazione fa - a partire da "ha gia' una membership attiva
    altrove?".

    A NON VIENE REVOCATO. Un titolare sostituito resta una persona che lavora
    li', e toglierle l'accesso sarebbe una seconda decisione che nessuno ha
    preso. Diventa `agency_admin`.

    Se B e' gia' il titolare, non succede niente: nessuna scrittura, nessuna
    riga di audit. Registrare un trasferimento da B a B sarebbe una riga falsa
    in un registro che non si puo' correggere.
    """
    with platform_operation_cursor() as (conn, cur):
        super_admin.lock_super_admin(cur, actor, audit_action=ACTION_OWNER_TRANSFER)
        _require_agency(cur, agency_id)

        if operators_repository.get_operator(cur, new_owner_user_id) is None:
            raise OperatorNotFound(OPERATOR_NOT_FOUND_MESSAGE)

        incoming = operators_repository.get_membership(
            cur, agency_id, new_owner_user_id
        )
        if incoming is None:
            raise MembershipNotFound(MEMBERSHIP_NOT_FOUND_MESSAGE)
        if incoming["status"] != MEMBERSHIP_ACTIVE:
            raise PlatformConflict(OWNER_MUST_BE_ACTIVE_MEMBER_MESSAGE)

        current = operators_repository.active_owner_of(cur, agency_id)
        if current is not None and current["operator_user_id"] == new_owner_user_id:
            return {"membership": incoming, "demoted": None}

        demoted = operators_repository.demote_active_owner(
            cur, agency_id, to_role=ROLE_AGENCY_ADMIN
        )

        try:
            promoted = operators_repository.update_membership(
                cur, incoming["id"], {"role": ROLE_AGENCY_OWNER}
            )
        except errors.UniqueViolation as exc:
            raise _as_conflict(exc) from exc

        audit_then_commit(
            conn,
            actor,
            action=ACTION_OWNER_TRANSFER,
            target_type=TARGET_TYPE_OPERATOR,
            target_id=new_owner_user_id,
            target_agency_id=agency_id,
            metadata={"demoted_previous_owner": demoted is not None},
        )
        return {"membership": promoted, "demoted": demoted}
