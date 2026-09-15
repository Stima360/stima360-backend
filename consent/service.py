"""P29-1.2 - the single write path, at the domain level.

Tre funzioni di scrittura, e nessuna quarta:

    record_grant          una concessione esplicita
    record_revocation     una revoca esplicita
    record_optional_grant una concessione SE c'e' qualcosa da concedere

IL CASO D VIVE IN QUESTA FORMA, NON IN UN `IF`

Il committente ha deciso, e la decisione e' vincolante: una casella marketing
non spuntata su una nuova richiesta di stima NON revoca un consenso
precedentemente valido. La revoca deriva esclusivamente da un evento esplicito
e auditabile.

Il modo per garantirlo non e' controllare un booleano in un punto e sperare
che nessuno lo controlli male altrove. E' non avere l'API che sbaglierebbe:
qui NON esiste un `set_marketing(contact_id, bool)`. Un chiamante che ha in
mano un booleano - il bridge pubblico, da P29-1.4 - chiama
`record_optional_grant`, che con `granted=False` non scrive niente e lo dice.

Per revocare bisogna chiamare `record_revocation`, cioe' bisogna VOLERLO, e
bisogna dichiarare chi lo vuole: `system` non e' ammesso, e il database lo
rifiuterebbe comunque.

COSA QUESTO MODULO NON FA, ANCORA

* non e' chiamato dal bridge pubblico   -> P29-1.4
* non espone `can_send_marketing`       -> P29-1.5
* non cancella messaggi programmati     -> P29-4, quando le code esisteranno
* non scrive in seller_timeline_events  -> in valutazione, P29-1.4

L'ultimo punto e' una scelta di questa fase, non una dimenticanza: scrivere in
un dominio altrui prima che il committente lo abbia approvato sarebbe
esattamente il tipo di sconfinamento che P29-0 ha censito come rischio.
"""

from __future__ import annotations

from typing import Any

from . import repository
from .enums import (
    ACTOR_OPERATOR,
    ACTOR_TYPES,
    DECISION_GRANTED,
    DECISION_REVOKED,
    PROJECTION_COLUMNS,
    PURPOSES,
    REVOKING_ACTOR_TYPES,
    STATUS_GRANTED,
    STATUS_NEVER_GIVEN,
    STATUS_REVOKED,
    SOURCE_LEGACY,
    WRITABLE_SOURCES,
)
from .exceptions import ValidationError


def _validated(
    *,
    purpose: str,
    decision: str,
    source: str,
    actor_type: str,
    actor_ref: str | None,
    evidence_type: str | None,
    evidence_ref: str | None,
) -> None:
    """Rifiuta gli argomenti fuori dagli insiemi chiusi, PRIMA di scrivere.

    Il database ha gli stessi vincoli e li farebbe rispettare comunque. Questo
    livello esiste perche' un errore del chiamante diventi un ValidationError
    leggibile invece di una IntegrityError del driver, e perche' la regola sia
    dichiarata dove la si legge.
    """
    if purpose not in PURPOSES:
        raise ValidationError(f"purpose must be one of {sorted(PURPOSES)}")
    if actor_type not in ACTOR_TYPES:
        raise ValidationError(f"actor_type must be one of {sorted(ACTOR_TYPES)}")
    if not source or not source.strip():
        raise ValidationError("source is required: a consent with no origin is not auditable")
    if source == SOURCE_LEGACY:
        raise ValidationError(
            "'legacy' is not a writable source: it describes consent recorded "
            "before this register existed and must never be created now"
        )
    if source not in WRITABLE_SOURCES:
        raise ValidationError(f"source must be one of {sorted(WRITABLE_SOURCES)}")
    if decision == DECISION_REVOKED and actor_type not in REVOKING_ACTOR_TYPES:
        raise ValidationError(
            "a revocation must come from the subject or from an operator; "
            "a system-authored revocation is consent lost for a reason nobody can explain"
        )
    if actor_type == ACTOR_OPERATOR and not (actor_ref or "").strip():
        raise ValidationError("an operator event must carry actor_ref")
    if (evidence_type is None) != (evidence_ref is None):
        raise ValidationError("evidence_type and evidence_ref go together or not at all")


def _record(
    ctx,
    *,
    contact_id: int,
    purpose: str,
    decision: str,
    source: str,
    actor_type: str,
    actor_ref: str | None = None,
    notice_id: int | None = None,
    decided_at=None,
    evidence_type: str | None = None,
    evidence_ref: str | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    _validated(
        purpose=purpose,
        decision=decision,
        source=source,
        actor_type=actor_type,
        actor_ref=actor_ref,
        evidence_type=evidence_type,
        evidence_ref=evidence_ref,
    )
    outcome = repository.record_decision(
        ctx,
        {
            "contact_id": contact_id,
            "purpose": purpose,
            "decision": decision,
            "decided_at": decided_at or repository.utcnow(),
            "source": source,
            "notice_id": notice_id,
            "actor_type": actor_type,
            "actor_ref": actor_ref,
            "evidence_type": evidence_type,
            "evidence_ref": evidence_ref,
            "note": note,
            "idempotency_key": idempotency_key,
        },
    )
    return {
        "recorded": True,
        "created": outcome["created"],
        "event": outcome["event"],
        "state": state_from_projection(outcome["contact"], purpose),
    }


def record_grant(ctx, **kwargs) -> dict[str, Any]:
    """Registra una concessione. Vince sempre su qualunque stato precedente.

    Casi A, C e F del design: primo consenso, consenso dopo un precedente non
    concesso, consenso dopo una revoca. Sono lo stesso atto - una persona ha
    detto si' - e producono lo stesso evento.
    """
    return _record(ctx, decision=DECISION_GRANTED, **kwargs)


def record_revocation(ctx, **kwargs) -> dict[str, Any]:
    """Registra una revoca. Deve essere voluta da qualcuno.

    Caso E del design. Immediatamente efficace sulla proiezione, che e' cio'
    che P24 e (da P29-1.5) `can_send_marketing` leggono.

    Non cancella niente: l'evento di concessione resta dov'e', e
    `marketing_consent_at` conserva la data della concessione revocata.
    """
    return _record(ctx, decision=DECISION_REVOKED, **kwargs)


def record_optional_grant(ctx, *, granted: bool, **kwargs) -> dict[str, Any]:
    """Una concessione se c'e', e NIENTE se non c'e'.

    Questa e' la funzione che il bridge pubblico chiamera' in P29-1.4, ed e' il
    caso D reso impossibile da sbagliare:

        granted=True   -> un evento `granted`, come record_grant
        granted=False  -> NESSUN evento, NESSUNA scrittura, NESSUNA revoca

    Il ritorno lo dichiara (`recorded: False`, `reason`) invece di mentire con
    un successo vuoto: un chiamante che volesse distinguere i due esiti puo'
    farlo, e un log che registrasse solo "ok" direbbe una cosa falsa.
    """
    if granted:
        return record_grant(ctx, **kwargs)
    return {
        "recorded": False,
        "created": False,
        "event": None,
        "reason": "no_grant_to_record",
        "state": None,
    }


def state_from_projection(contact: dict[str, Any], purpose: str) -> dict[str, Any]:
    """Lo stato corrente, derivato dalla proiezione di un contatto.

    LA DERIVAZIONE, PER INTERO E SENZA ECCEZIONI

        flag IS TRUE                      -> granted
        revoked_at IS NOT NULL            -> revoked
        altrimenti                        -> never_given

    `never_given` e `revoked` NON sono lo stesso stato. Entrambi impediscono un
    invio marketing; solo il secondo dice che qualcuno aveva detto si'.

    IL LEGACY, SENZA INVENTARE NIENTE

    Un contatto concesso ma senza provenienza registrata e' un consenso
    raccolto prima che questo registro esistesse - 12 contatti nel censimento
    P29-1.0. Viene esposto come `granted` con `source='legacy'` e nessuna
    notice: significa esattamente "acconsentito prima che tracciassimo
    l'origine", che e' un'informazione vera. Non gli viene attribuita una
    versione che nessuno ha accettato.
    """
    if purpose not in PURPOSES:
        raise ValidationError(f"purpose must be one of {sorted(PURPOSES)}")
    columns = PROJECTION_COLUMNS[purpose]

    flag = contact.get(columns["flag"])
    granted_at = contact.get(columns["granted_at"])
    revoked_at = contact.get(columns["revoked_at"])
    source = contact.get(columns["source"])
    notice_id = contact.get(columns["notice_id"])

    if flag is True:
        status = STATUS_GRANTED
    elif revoked_at is not None:
        status = STATUS_REVOKED
    else:
        status = STATUS_NEVER_GIVEN

    is_legacy = status == STATUS_GRANTED and source is None

    return {
        "purpose": purpose,
        "status": status,
        "granted_at": granted_at,
        "revoked_at": revoked_at,
        "source": SOURCE_LEGACY if is_legacy else source,
        "notice_id": notice_id,
        "legacy": is_legacy,
    }


def current_state(ctx, contact_id: int, purpose: str) -> dict[str, Any]:
    """Lo stato corrente di un contatto per uno scopo. Sola lettura."""
    contact = repository.read_projection(ctx, contact_id)
    return state_from_projection(contact, purpose)
