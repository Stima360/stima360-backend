"""Fase B - il consenso marketing RACCOLTO DALL'OPERATORE, con la prova.

Fino a qui il registro (062) conosceva due sole porte: la stima pubblica (il
proprietario spunta la casella) e il link di disiscrizione. Un operatore che
raccoglie un modulo firmato non aveva un posto in cui dirlo, e un contatto
creato a mano restava `never_given` per sempre.

Questa e' quella porta, con le sue regole:

  * NESSUNA DICHIARAZIONE SENZA PROVA. Un grant porta SEMPRE un tipo di prova
    fra quelli ammessi (modulo cartaceo, modulo digitale, altra modalita'
    documentata) e un riferimento che identifichi dove la prova si trova:
    un numero di protocollo, una posizione d'archivio, un identificativo del
    documento. Il riferimento non puo' essere l'email o il telefono del
    contatto - "ce l'ho in rubrica" non e' una prova - ne' una parola sola.
  * UN'INFORMATIVA VERA. Il grant cita la versione dell'informativa marketing
    (061) presentata: deve esistere, essere marketing, ed essere valida alla
    data di raccolta. Senza informativa valida non si registra niente.
  * LA DATA E' QUELLA DELLA RACCOLTA, non quella del click: la dichiara
    l'operatore, nel passato o adesso, mai nel futuro. Un grant dopo una revoca
    e' una manifestazione NUOVA: deve essere successivo alla revoca, e lo si
    rifiuta se e' retrodatato prima di essa.
  * CHI. L'operatore della sessione, della stessa agenzia del contatto, con
    un'identita' (`user_id`): il canale Basic condiviso non ha una persona
    dietro e non puo' registrare consensi. L'attore finisce nell'evento
    (`actor_ref`), con data e ora della registrazione (`created_at`).
  * APPEND-ONLY, come tutto il registro: niente si sovrascrive. La revoca o il
    rifiuto espressi al personale si registrano come evento `revoked`, con
    l'AMBITO: questa agenzia (il caso normale) oppure tutto STIMA360, che in
    piu' scrive la lista di piattaforma. L'ambito lo dichiara l'operatore
    e non viene mai ampliato da solo.

Il consenso resta per (agenzia, contatto): la stessa persona in un'altra
agenzia e' un altro contatto, con il suo consenso. La dedup fra agenzie e'
affare della sequenza, non del registro.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from core.exceptions import PermissionDenied
from operator_auth.enums import AGENCY_ROLES

from . import repository
from . import service as consent_service
from .database import consent_cursor
from .enums import (
    ACTOR_OPERATOR, DECISION_GRANTED, DECISION_REVOKED, PURPOSE_MARKETING, SOURCE_CRM,
)
from .exceptions import ConflictError, NotFoundError, ValidationError
from .guard import can_send_marketing

#: Le modalita' di raccolta ammesse per un GRANT. Chiuse: una telefonata o
#: una email in rubrica non sono fra queste, di proposito.
EVIDENCE_PAPER_FORM = "paper_form"
EVIDENCE_DIGITAL_FORM = "digital_form"
EVIDENCE_OTHER_DOCUMENTED = "other_documented"
GRANT_EVIDENCE_TYPES = (EVIDENCE_PAPER_FORM, EVIDENCE_DIGITAL_FORM, EVIDENCE_OTHER_DOCUMENTED)
#: La prova di una REVOCA espressa al personale, se c'e' (facoltativa).
REVOCATION_EVIDENCE_TYPES = GRANT_EVIDENCE_TYPES + ("written_request",)

#: Gli ambiti di una revoca registrata dall'operatore.
SCOPE_AGENCY, SCOPE_PLATFORM = "agency", "platform"
REVOCATION_SCOPES = (SCOPE_AGENCY, SCOPE_PLATFORM)

EVIDENCE_REF_MIN, EVIDENCE_REF_MAX = 6, 100
NOTE_MIN_REVOCATION, NOTE_MAX = 10, 2000
#: Quanto puo' essere nel futuro una data di raccolta (tolleranza d'orologio).
FUTURE_TOLERANCE = timedelta(minutes=5)
#: Quanto indietro: oltre, e' quasi certamente un errore di battitura.
MAX_AGE = timedelta(days=365 * 10)

_RIFERIMENTO = re.compile(r"[A-Za-z0-9]")


def _operatore(ctx) -> str:
    """L'identita' di chi registra: una persona, della sua agenzia."""
    if getattr(ctx, "user_id", None) is None:
        raise PermissionDenied("recording a consent requires an identified operator session")
    if getattr(ctx, "agency_id", None) is None:
        raise PermissionDenied("recording a consent requires an agency context")
    ruolo = getattr(ctx, "role", None)
    if ruolo not in AGENCY_ROLES and not getattr(ctx, "is_platform_admin", False):
        raise PermissionDenied("recording a consent requires an active agency operator")
    return f"operator_user:{ctx.user_id}"


def _istante(valore, campo: str) -> datetime:
    if not isinstance(valore, datetime) or valore.tzinfo is None:
        raise ValidationError(f"{campo} must be a timezone-aware datetime")
    adesso = datetime.now(timezone.utc)
    if valore > adesso + FUTURE_TOLERANCE:
        raise ValidationError(f"{campo} cannot be in the future")
    if valore < adesso - MAX_AGE:
        raise ValidationError(f"{campo} is too far in the past to be plausible")
    return valore


def _riferimento_prova(evidence_ref, contact: dict[str, Any]) -> str:
    ref = (evidence_ref or "").strip() if isinstance(evidence_ref, str) else ""
    if len(ref) < EVIDENCE_REF_MIN or len(ref) > EVIDENCE_REF_MAX:
        raise ValidationError(
            f"evidence_ref must identify a retrievable proof ({EVIDENCE_REF_MIN}-{EVIDENCE_REF_MAX} characters)")
    if not _RIFERIMENTO.search(ref) or len(ref.split()) < 1:
        raise ValidationError("evidence_ref must contain an identifier, not punctuation alone")
    basso = ref.lower()
    for campo in ("email", "email_normalized", "phone", "phone_normalized", "secondary_phone"):
        valore = (contact.get(campo) or "").strip().lower()
        if valore and (basso == valore or valore in basso and len(valore) >= EVIDENCE_REF_MIN):
            raise ValidationError(
                "evidence_ref cannot be the contact's own email or phone: an address on file is not a proof")
    return ref


def _nota(note, *, minimo: int = 0) -> str | None:
    testo = (note or "").strip() if isinstance(note, str) else ""
    if len(testo) > NOTE_MAX:
        raise ValidationError(f"note is longer than {NOTE_MAX} characters")
    if minimo and len(testo) < minimo:
        raise ValidationError(f"note must describe how the decision was expressed ({minimo}+ characters)")
    return testo or None


def _contatto_registrabile(cur, ctx, contact_id: int) -> dict[str, Any]:
    contact = repository.select_contact(cur, ctx, contact_id)
    if contact.get("deleted_at") is not None:
        raise ConflictError("the contact is in the trash: restore it before recording a consent")
    if contact.get("status") == "archived":
        raise ConflictError("the contact is archived: no consent can be recorded")
    return contact


def _informativa_valida(cur, notice_id, decided_at: datetime) -> dict[str, Any]:
    if not isinstance(notice_id, int) or isinstance(notice_id, bool) or notice_id <= 0:
        raise ValidationError("notice_id is required: a consent cites the privacy notice presented")
    cur.execute(
        """SELECT id, purpose, version, context, valid_from, valid_to, content_available
             FROM consent_notices WHERE id = %s""", [notice_id])
    riga = cur.fetchone()
    riga = dict(riga) if riga is not None else None
    if riga is None or riga["purpose"] != PURPOSE_MARKETING:
        raise ValidationError("notice_id does not name a marketing privacy notice")
    if not riga["content_available"]:
        raise ValidationError("the privacy notice text is not available: it cannot be cited")
    if riga["valid_from"] > decided_at or (riga["valid_to"] is not None and riga["valid_to"] < decided_at):
        raise ValidationError(
            f"privacy notice {riga['version']!r} was not valid on the collection date")
    return riga


def _impronta(*parti: str) -> str:
    return hashlib.sha256("|".join(parti).encode("utf-8")).hexdigest()[:16]


def list_notices(*, at: datetime | None = None) -> list[dict[str, Any]]:
    """Le informative marketing valide a `at` (adesso, se non dato)."""
    quando = at or datetime.now(timezone.utc)
    with consent_cursor() as (_, cur):
        cur.execute(
            """SELECT id, purpose, version, context, valid_from, valid_to, content_hash, content
                 FROM consent_notices
                WHERE purpose = %s AND content_available
                  AND valid_from <= %s AND (valid_to IS NULL OR valid_to >= %s)
                ORDER BY valid_from DESC, id DESC""",
            [PURPOSE_MARKETING, quando, quando])
        return [dict(r) for r in cur.fetchall()]


def register_grant(ctx, contact_id: int, *, decided_at: datetime, evidence_type: str,
                   evidence_ref: str, notice_id: int, note: str | None = None) -> dict[str, Any]:
    """Un consenso marketing raccolto dall'operatore, con la prova. Append-only."""
    attore = _operatore(ctx)
    quando = _istante(decided_at, "decided_at")
    if evidence_type not in GRANT_EVIDENCE_TYPES:
        raise ValidationError(f"evidence_type must be one of {list(GRANT_EVIDENCE_TYPES)}")
    nota = _nota(note)
    with consent_cursor(commit=True) as (_, cur):
        contact = _contatto_registrabile(cur, ctx, contact_id)
        ref = _riferimento_prova(evidence_ref, contact)
        informativa = _informativa_valida(cur, notice_id, quando)
        ultimo = repository.latest_event(cur, ctx, contact_id, PURPOSE_MARKETING)
        if ultimo is not None and ultimo["decision"] == DECISION_REVOKED and quando <= ultimo["decided_at"]:
            raise ConflictError(
                "a consent after a revocation must be a new manifestation, dated after the revocation")
        optout = repository.latest_platform_optout(cur, contact.get("email_normalized"))
        if optout is not None and quando <= optout["opted_out_at"]:
            raise ConflictError(
                "this address opted out of all STIMA360 marketing after that date: "
                "a new consent must be dated after the opt-out")
        esito = consent_service.record_grant(
            ctx, contact_id=contact_id, purpose=PURPOSE_MARKETING, source=SOURCE_CRM,
            actor_type=ACTOR_OPERATOR, actor_ref=attore, notice_id=informativa["id"],
            decided_at=quando, evidence_type=evidence_type, evidence_ref=ref, note=nota,
            idempotency_key=f"crm:grant:{ctx.require_agency()}:{contact_id}:"
                            f"{int(quando.timestamp())}:{_impronta(evidence_type, ref)}",
            cur=cur)
        return {"recorded": True, "created": esito["created"], "event": esito["event"],
                "notice": {"id": informativa["id"], "version": informativa["version"]}}


def register_refusal(ctx, contact_id: int, *, decided_at: datetime, scope: str,
                     note: str, evidence_type: str | None = None,
                     evidence_ref: str | None = None) -> dict[str, Any]:
    """Un rifiuto o una revoca espressi al personale. Blocca subito gli invii.

    `scope=agency` scrive la revoca di questa agenzia (il caso normale);
    `scope=platform` in piu' registra l'indirizzo nella lista di piattaforma,
    SOLO perche' l'operatore lo ha dichiarato. Nessun evento di consenso di
    altre agenzie viene toccato.
    """
    attore = _operatore(ctx)
    quando = _istante(decided_at, "decided_at")
    if scope not in REVOCATION_SCOPES:
        raise ValidationError(f"scope must be one of {list(REVOCATION_SCOPES)}")
    nota = _nota(note, minimo=NOTE_MIN_REVOCATION)
    if (evidence_type is None) != (evidence_ref is None):
        raise ValidationError("evidence_type and evidence_ref go together or not at all")
    if evidence_type is not None and evidence_type not in REVOCATION_EVIDENCE_TYPES:
        raise ValidationError(f"evidence_type must be one of {list(REVOCATION_EVIDENCE_TYPES)}")
    with consent_cursor(commit=True) as (_, cur):
        contact = _contatto_registrabile(cur, ctx, contact_id)
        ref = _riferimento_prova(evidence_ref, contact) if evidence_ref is not None else None
        chiave = (f"crm:revoke:{ctx.require_agency()}:{contact_id}:{int(quando.timestamp())}:"
                  f"{_impronta(scope, nota or '')}")
        esito = consent_service.record_revocation(
            ctx, contact_id=contact_id, purpose=PURPOSE_MARKETING, source=SOURCE_CRM,
            actor_type=ACTOR_OPERATOR, actor_ref=attore, decided_at=quando,
            evidence_type=evidence_type, evidence_ref=ref, note=nota,
            idempotency_key=chiave, cur=cur)
        optout = None
        if scope == SCOPE_PLATFORM:
            optout = consent_service.register_platform_optout(
                ctx, contact_id=contact_id, source=SOURCE_CRM, actor_type=ACTOR_OPERATOR,
                actor_ref=attore, consent_event_id=esito["event"]["id"], note=nota,
                opted_out_at=quando, idempotency_key=f"optout:{chiave}", cur=cur)
        return {"recorded": True, "created": esito["created"], "event": esito["event"],
                "scope": scope, "platform_optout": (optout or {}).get("optout")}


def marketing_state(ctx, contact_id: int) -> dict[str, Any]:
    """Lo stato del consenso marketing, come lo vede la guardia, per la scheda."""
    decisione = can_send_marketing(ctx, contact_id)
    with consent_cursor() as (_, cur):
        contact = repository.select_contact(cur, ctx, contact_id)
        ultimo = repository.latest_event(cur, ctx, contact_id, PURPOSE_MARKETING)
        optout = repository.latest_platform_optout(cur, contact.get("email_normalized"))
    return {
        "allowed": decisione.allowed, "reason": decisione.reason, "state": decisione.state,
        "legacy": decisione.legacy, "source": decisione.source,
        "decided_at": decisione.decided_at,
        "last_event": None if ultimo is None else {
            "id": ultimo["id"], "decision": ultimo["decision"], "decided_at": ultimo["decided_at"],
            "source": ultimo["source"], "actor_type": ultimo.get("actor_type"),
            "evidence_type": ultimo.get("evidence_type"), "notice_id": ultimo.get("notice_id"),
            "recorded_at": ultimo.get("created_at")},
        "platform_optout_at": None if optout is None else optout["opted_out_at"],
        "can_record": getattr(ctx, "user_id", None) is not None
                      and contact.get("deleted_at") is None and contact.get("status") != "archived",
    }


__all__ = ["register_grant", "register_refusal", "marketing_state", "list_notices",
           "GRANT_EVIDENCE_TYPES", "REVOCATION_EVIDENCE_TYPES", "REVOCATION_SCOPES",
           "NotFoundError"]
