"""ROADMAP VISITE (fase D) - l'AGGIORNAMENTO AL PROPRIETARIO su una visita, dalla scheda
dell'appuntamento: bozza -> anteprima -> APPROVAZIONE dell'agente -> pubblicazione.

NESSUN SISTEMA PARALLELO. Il contenuto e' un `owner_visit_feedback_publications` (OWNER 0.2):
bozza, pubblicazione, versione successiva e ritiro sono le funzioni di `owner.repository`, con la
loro validazione della privacy, il loro audit e la loro notifica nel portale. Questo modulo ci
aggiunge solo quello che mancava:

  * l'accesso dalla visita e per l'AGENTE della visita (la superficie «Proprietari» richiede il
    ruolo di titolare): il titolare e gli amministratori per ogni visita, l'agente per le proprie;
  * il vincolo che la visita sia stata EFFETTUATA (esito registrato in Agenda): niente riscontro
    su una visita prenotata, annullata o con il cliente assente;
  * la proposta di testo, scritta SOLO dall'interesse registrato e mai dalle note interne;
  * l'approvazione ESPLICITA: niente si pubblica da solo, niente si pubblica in un passo solo.

COSA NON ESCE MAI. Le note interne e il feedback interno (`property_visits.feedback`) non vengono
mai copiati nel testo del proprietario; i nomi, i recapiti e i dati personali dell'acquirente
sono bloccati dalla stessa validazione della privacy dell'area proprietari (nominativi, recapiti,
importi, dati sensibili, citazioni). Quello che il proprietario legge e' il testo che l'agente ha
scritto e approvato, e nient'altro. Il portale mostra le notifiche in-app: nessun messaggio al
proprietario o all'acquirente parte da qui.
"""
from __future__ import annotations

from typing import Any

from buyer_visits import visit_outcome
from core.database import core_cursor
from core.exceptions import ConflictError, NotFoundError, ValidationError
from operator_auth.permissions import may_assign_records
from owner import repository as owner_repository
from owner.schemas import visit_feedback_privacy_issues
from visit_sheets import repository as visit_repository

from . import repository
from .service import Forbidden, NotFound, ReportError

CATEGORIES = tuple(owner_repository.CATEGORY_LABELS)
SENTIMENTS = tuple(owner_repository.SENTIMENT_LABELS)
SUMMARY_MAX = 5000

NOT_PREPARABLE_MESSAGE = ("L'aggiornamento al proprietario si prepara dopo che la visita è stata registrata come "
                          "effettuata: con la visita prenotata, annullata o con il cliente assente non c'è nulla "
                          "da riferire.")
NOTICE = ("Il testo che approvi è l'unico che il proprietario potrà leggere. Le note interne, i nomi e i recapiti "
          "dell'acquirente non vengono mai pubblicati.")

#: Il testo proposto, dall'interesse registrato (frasi che passano la validazione della privacy:
#: nessun nome, nessuna parola che indichi una persona).
SUGGESTIONS = {
    "high": ("La visita è stata effettuata e ha suscitato un forte interesse per l'immobile.", "positive"),
    "medium": ("La visita è stata effettuata e ha suscitato interesse per l'immobile.", "positive"),
    "undecided": ("La visita è stata effettuata; per ora l'interesse è ancora da definire.", "neutral"),
    "none": ("La visita è stata effettuata; l'immobile non ha incontrato le esigenze di questa visita.", "negative"),
    None: ("La visita è stata effettuata.", "neutral"),
}


class Conflict(ReportError):
    status = 409


class Invalid(ReportError):
    status = 422


def _author(ctx) -> str:
    return f"operator:{int(ctx.user_id)}"


def _require_session(ctx) -> int:
    agency_id = ctx.require_agency()
    if ctx.user_id is None:
        raise Forbidden("SESSION_REQUIRED", "Serve una sessione operatore.")
    return agency_id


def _visit(cur, ctx, agency_id: int, appointment_id: int) -> tuple[dict, dict | None]:
    """L'appuntamento (stessa visibilita' dell'Agenda) e la sua visita collegata."""
    row = visit_repository.visit(cur, agency_id, appointment_id)
    visible = row is not None and (may_assign_records(ctx.role, ctx.is_platform_admin)
                                   or row["assigned_user_id"] == ctx.user_id)
    if not visible or row["appointment_type"] != "buyer_visit" or row["property_id"] is None:
        raise NotFound("VISIT_NOT_FOUND", "Visita non trovata.")
    return row, visit_outcome.summary(cur, agency_id, appointment_id, row["status"])


def _wrap(call, *args):
    try:
        return call(*args)
    except ConflictError as exc:
        raise Conflict("CONFLICT", str(exc)) from exc
    except NotFoundError as exc:
        raise NotFound("NOT_FOUND", "Riscontro non trovato.") from exc
    except ValidationError as exc:
        raise Invalid("INVALID", str(exc)) from exc


def _suggestion(visit: dict | None) -> dict:
    testo, sentiment = SUGGESTIONS.get(visit["interest"] if visit else None, SUGGESTIONS[None])
    return {"category": "general", "sentiment": sentiment, "public_summary": testo}


def _view(row: dict) -> dict:
    sentiment = row.get("sentiment")
    return {"id": row["id"], "status": row["status"], "category": row["category"],
            "category_label": owner_repository.CATEGORY_LABELS[row["category"]],
            "public_summary": row["public_summary"], "sentiment": sentiment,
            "sentiment_label": owner_repository.SENTIMENT_LABELS.get(sentiment) if sentiment else None,
            "version_number": row["version_number"],
            "published_at": row["published_at"].isoformat() if row.get("published_at") else None,
            "created_by": row.get("created_by"),
            "supersedes_id": row.get("supersedes_feedback_publication_id")}


def _current(rows: list[dict]) -> dict | None:
    """Quello che l'agente sta gestendo: la bozza piu' recente, altrimenti il riscontro pubblicato
    corrente. Per tutti i proprietari dell'immobile (non quelli indirizzati a uno solo)."""
    family = [r for r in rows if r.get("owner_account_id") is None]
    drafts = [r for r in family if r["status"] == "draft"]
    if drafts:
        return max(drafts, key=lambda r: r["id"])
    published = [r for r in family if r["status"] == "published"
                 and r.get("superseded_by_feedback_publication_id") is None]
    return max(published, key=lambda r: r["id"]) if published else None


def get_state(ctx, appointment_id: int) -> dict:
    agency_id = _require_session(ctx)
    with core_cursor() as (_, cur):
        row, visit = _visit(cur, ctx, agency_id, appointment_id)
        owners = repository.owners_with_access(cur, agency_id, row["property_id"]) if visit else 0
    preparable = row["status"] == "completed" and visit is not None
    rows = owner_repository.list_visit_feedback_publications(agency_id, property_visit_id=visit["id"], limit=50) \
        if visit else []
    current = _current(rows)
    out = {"appointment_id": appointment_id, "property_id": row["property_id"],
           "visit_status": row["status"], "can_prepare": preparable,
           "reason": None if preparable else NOT_PREPARABLE_MESSAGE, "notice": NOTICE,
           "owners_with_access": owners, "categories": [{"code": c, "label": owner_repository.CATEGORY_LABELS[c]}
                                                        for c in CATEGORIES],
           "sentiments": [{"code": s, "label": owner_repository.SENTIMENT_LABELS[s]} for s in SENTIMENTS],
           "current": _view(current) if current else None,
           "suggestion": _suggestion(visit) if preparable and not current else None}
    if current:
        out["can_edit"] = preparable
        out["can_approve"] = preparable and current["status"] == "draft"
        out["can_withdraw"] = current["status"] == "published"
    return out


def check(ctx, appointment_id: int, public_summary: str) -> dict:
    """La validazione della privacy, senza scrivere: stessi controlli della pubblicazione."""
    agency_id = _require_session(ctx)
    with core_cursor() as (_, cur):
        _visit(cur, ctx, agency_id, appointment_id)
    issues = visit_feedback_privacy_issues(public_summary or "")
    return {"valid": not issues, "issues": issues}


def _clean(category: str, sentiment: str | None, public_summary: str) -> tuple[str, str | None, str]:
    if category not in CATEGORIES:
        raise Invalid("INVALID_CATEGORY", "Categoria non valida.")
    if sentiment is not None and sentiment not in SENTIMENTS:
        raise Invalid("INVALID_SENTIMENT", "Esito non valido.")
    testo = (public_summary or "").strip()
    if len(testo) > SUMMARY_MAX:
        raise Invalid("SUMMARY_TOO_LONG", f"Il testo può avere al massimo {SUMMARY_MAX} caratteri.")
    issues = visit_feedback_privacy_issues(testo)
    if issues:
        error = Invalid("PRIVACY_CHECK_FAILED", "Il testo non può essere pubblicato: " + "; ".join(
            i["message"] for i in issues))
        error.issues = issues
        raise error
    return category, sentiment, testo


def save_draft(ctx, appointment_id: int, *, category: str, sentiment: str | None, public_summary: str) -> dict:
    """Salva il testo come BOZZA (mai visibile al proprietario). Una bozza esistente si corregge;
    un riscontro gia' pubblicato si corregge con una nuova versione, che resta bozza fino
    all'approvazione; altrimenti nasce la prima bozza."""
    agency_id = _require_session(ctx)
    category, sentiment, testo = _clean(category, sentiment, public_summary)
    with core_cursor() as (_, cur):
        row, visit = _visit(cur, ctx, agency_id, appointment_id)
    if row["status"] != "completed" or visit is None:
        raise Conflict("VISIT_NOT_COMPLETED", NOT_PREPARABLE_MESSAGE)
    rows = owner_repository.list_visit_feedback_publications(agency_id, property_visit_id=visit["id"], limit=50)
    current = _current(rows)
    if current is None:
        _wrap(owner_repository.create_visit_feedback_publication, agency_id, {
            "property_visit_id": visit["id"], "owner_account_id": None, "category": category,
            "public_summary": testo, "sentiment": sentiment, "created_by": _author(ctx)})
    elif current["status"] == "draft":
        _wrap(owner_repository.update_visit_feedback_publication, agency_id, current["id"], {
            "category": category, "public_summary": testo, "sentiment": sentiment})
    else:
        _wrap(owner_repository.supersede_visit_feedback, agency_id, current["id"], {
            "category": category, "public_summary": testo, "sentiment": sentiment, "created_by": _author(ctx)})
    return get_state(ctx, appointment_id)


def _own_publication(ctx, appointment_id: int, publication_id: int) -> tuple[dict, dict]:
    agency_id = _require_session(ctx)
    with core_cursor() as (_, cur):
        row, visit = _visit(cur, ctx, agency_id, appointment_id)
    if visit is None:
        raise NotFound("VISIT_NOT_FOUND", "Visita non trovata.")
    publication = _wrap(owner_repository.get_visit_feedback_publication, agency_id, publication_id)
    if publication["property_visit_id"] != visit["id"]:
        raise NotFound("NOT_FOUND", "Riscontro non trovato.")        # mai il riscontro di un'altra visita
    return row, publication


def approve(ctx, appointment_id: int, publication_id: int, *, confirm: bool) -> dict:
    """L'approvazione dell'agente: pubblica la bozza SOLO con la conferma esplicita."""
    if confirm is not True:
        raise Invalid("APPROVAL_REQUIRED", "Per pubblicare al proprietario conferma di aver controllato il testo.")
    row, publication = _own_publication(ctx, appointment_id, publication_id)
    if row["status"] != "completed":
        raise Conflict("VISIT_NOT_COMPLETED", NOT_PREPARABLE_MESSAGE)
    agency_id = ctx.require_agency()
    _wrap(owner_repository.publish_visit_feedback, agency_id, publication["id"])
    return get_state(ctx, appointment_id)


def withdraw(ctx, appointment_id: int, publication_id: int) -> dict:
    """Ritira dal portale un riscontro pubblicato (resta nello storico, archiviato)."""
    _row, publication = _own_publication(ctx, appointment_id, publication_id)
    _wrap(owner_repository.archive_visit_feedback, ctx.require_agency(), publication["id"])
    return get_state(ctx, appointment_id)
