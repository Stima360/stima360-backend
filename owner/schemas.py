from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class M(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AccountCreate(M):
    contact_id: int
    preferred_language: str = "it"


class AccessCreate(M):
    owner_account_id: int
    property_id: int
    access_role: Literal["owner", "co_owner", "delegate", "legal_representative"] = "owner"
    is_primary: bool = False
    valid_until: datetime | None = None


class TokenCreate(M):
    token_type: Literal["invitation", "login"] = "login"
    expires_minutes: int = Field(30, ge=5, le=1440)
    created_by: str | None = None


class TokenConsume(M):
    token: str = Field(min_length=32, max_length=512)


class PublicationCreate(M):
    property_id: int
    publication_type: Literal[
        "general_update",
        "marketing_update",
        "visit_update",
        "feedback_summary",
        "strategy_update",
        "milestone",
    ]
    title: str = Field(min_length=1, max_length=200)
    summary: str | None = Field(None, max_length=1000)
    body: str = Field(min_length=1, max_length=20000)
    acknowledgement_required: bool = False


class PublicationUpdate(M):
    publication_type: Literal[
        "general_update",
        "marketing_update",
        "visit_update",
        "feedback_summary",
        "strategy_update",
        "milestone",
    ] | None = None
    title: str | None = Field(None, min_length=1, max_length=200)
    summary: str | None = Field(None, max_length=1000)
    body: str | None = Field(None, min_length=1, max_length=20000)
    acknowledgement_required: bool | None = None


class FeedbackCreate(M):
    feedback_type: Literal[
        "contact_request",
        "correction_request",
        "general_message",
        "strategy_feedback",
        "price_review",
        "availability_update",
        "document_question",
    ]
    subject: str = Field(min_length=1, max_length=150)
    message: str = Field(min_length=1, max_length=5000)
    availability_from: datetime | None = None
    availability_to: datetime | None = None

    @model_validator(mode="after")
    def validate_availability(self):
        if (
            self.availability_from is not None
            and self.availability_to is not None
            and self.availability_to <= self.availability_from
        ):
            raise ValueError("availability_to deve essere successivo ad availability_from")
        if self.feedback_type == "availability_update" and not (
            self.availability_from or self.availability_to
        ):
            raise ValueError("availability_update richiede almeno un estremo temporale")
        return self


class FeedbackPublic(M):
    feedback_type: Literal[
        "contact_request",
        "correction_request",
        "general_message",
        "strategy_feedback",
        "price_review",
        "availability_update",
        "document_question",
    ]
    subject: str
    message: str
    status: Literal["new", "in_review", "handled", "closed"]
    submitted_at: datetime
    availability_from: datetime | None = None
    availability_to: datetime | None = None
    handled_at: datetime | None = None
    public_response: str | None = None


class FeedbackListResponse(M):
    items: list[FeedbackPublic]


class FeedbackStatus(M):
    status: Literal["new", "in_review", "handled", "closed"]
    handled_by: str | None = Field(None, max_length=200)
    public_response: str | None = Field(None, max_length=5000)


SharedDocumentType = Literal[
    "mandate",
    "floor_plan",
    "ape",
    "cadastral_extract",
    "photo_report",
    "activity_report",
    "information",
]
SharedDocumentStatus = Literal["draft", "published", "revoked", "archived"]


class SharedDocumentCreate(M):
    property_document_id: int
    owner_account_id: int | None = None
    public_title: str = Field(min_length=1, max_length=200)
    public_document_type: SharedDocumentType
    expires_at: datetime | None = None
    acknowledgement_required: bool = False
    created_by: str | None = Field(None, max_length=200)


class SharedDocumentUpdate(M):
    public_title: str | None = Field(None, min_length=1, max_length=200)
    public_document_type: SharedDocumentType | None = None
    expires_at: datetime | None = None
    acknowledgement_required: bool | None = None


class SharedDocumentSupersede(M):
    property_document_id: int | None = None
    public_title: str = Field(min_length=1, max_length=200)
    public_document_type: SharedDocumentType
    expires_at: datetime | None = None
    acknowledgement_required: bool = False
    created_by: str | None = Field(None, max_length=200)


class RevokeRequest(M):
    actor: str | None = Field(None, max_length=200)
    reason: str | None = Field(None, max_length=500)


VisitFeedbackCategory = Literal[
    "price", "state", "layout", "location", "accessories", "general"
]
VisitFeedbackSentiment = Literal["positive", "neutral", "negative", "mixed"]
VisitFeedbackStatus = Literal["draft", "published", "archived"]


_PRIVACY_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "html_or_script",
        "HTML, script e attributi eseguibili non sono ammessi",
        re.compile(r"<[^>]+>|javascript\s*:|on(?:error|load|click)\s*=", re.IGNORECASE),
    ),
    (
        "email",
        "Indirizzi email non ammessi",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    ),
    (
        "phone",
        "Numeri di telefono non ammessi",
        re.compile(r"(?<!\w)(?:\+?\d[\d\s()./-]{7,}\d)(?!\w)"),
    ),
    (
        "url",
        "URL e domini non ammessi",
        re.compile(r"\b(?:https?://|www\.)\S+|\b[a-z0-9-]+\.(?:it|com|net|org|eu)\b", re.IGNORECASE),
    ),
    (
        "social_handle",
        "Username social non ammessi",
        re.compile(r"(?<!\w)@[a-z0-9_.-]{2,}\b", re.IGNORECASE),
    ),
    (
        "tax_or_identity_code",
        "Codici identificativi personali non ammessi",
        re.compile(r"\b[A-Z]{6}[0-9]{2}[A-EHLMPRST][0-9]{2}[A-Z][0-9]{3}[A-Z]\b", re.IGNORECASE),
    ),
    (
        "precise_datetime",
        "Data o orario preciso della visita non ammesso",
        re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b|\b(?:[01]?\d|2[0-3]):[0-5]\d\b"),
    ),
    (
        "financial_amount",
        "Importi, budget o dati finanziari non ammessi",
        re.compile(
            r"(?:€|\beur\b|\beuro\b)|\b\d{1,3}(?:[.\s]\d{3})+(?:,\d{1,2})?\b|"
            r"\b(?:budget|mutuo|reddito|liquidit[àa]|finanziament[oi]|caparra|isee|provenienza\s+dei\s+fondi)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "match_or_scoring",
        "Score, ranking o dettagli MATCH non ammessi",
        re.compile(r"\b(?:match|score|ranking|override|algoritm[oi]|punteggio|graduatoria)\b|\b\d{1,3}\s*/\s*100\b", re.IGNORECASE),
    ),
    (
        "personal_reference",
        "Riferimenti identificativi a visitatori o acquirenti non ammessi",
        re.compile(
            r"\b(?:sig\.?|signor[ea]?|nome|cognome|cliente|acquirente|visitatore|coppia|famiglia|figli[oa]?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "sensitive_data",
        "Dati particolari o discriminatori non ammessi",
        re.compile(
            r"\b(?:salute|malatti[ae]|disabilit[àa]|disabile|religion[ei]|nazionalit[àa]|etni[ac]|"
            r"orientamento|omosessual[ei]|gay|politic[ao]|condann[ae]|giudiziari[oa]|gravidanza)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "direct_quote",
        "Citazioni testuali del visitatore non ammesse",
        re.compile(r"[\"“”«»]"),
    ),
)


def visit_feedback_privacy_issues(value: str) -> list[dict[str, str]]:
    """Return controlled reason codes without echoing the submitted text."""
    text = (value or "").strip()
    issues: list[dict[str, str]] = []
    if not text:
        return [{"code": "empty", "message": "La sintesi pubblica è obbligatoria"}]
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        issues.append({"code": "control_characters", "message": "Caratteri di controllo non ammessi"})
    for code, message, pattern in _PRIVACY_RULES:
        if pattern.search(text):
            issues.append({"code": code, "message": message})
    return issues


def validate_visit_feedback_summary(value: str) -> str:
    text = (value or "").strip()
    issues = visit_feedback_privacy_issues(text)
    if issues:
        codes = ",".join(issue["code"] for issue in issues)
        raise ValueError(f"Sintesi pubblica non conforme: {codes}")
    return text


class PrivacyValidationRequest(M):
    public_summary: str = Field(min_length=1, max_length=5000)


class VisitFeedbackCreate(M):
    property_visit_id: int
    owner_account_id: int | None = None
    category: VisitFeedbackCategory
    public_summary: str = Field(min_length=1, max_length=5000)
    sentiment: VisitFeedbackSentiment | None = None
    created_by: str | None = Field(None, max_length=200)

    _validate_summary = field_validator("public_summary")(validate_visit_feedback_summary)


class VisitFeedbackUpdate(M):
    category: VisitFeedbackCategory | None = None
    public_summary: str | None = Field(None, min_length=1, max_length=5000)
    sentiment: VisitFeedbackSentiment | None = None

    @field_validator("public_summary")
    @classmethod
    def validate_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_visit_feedback_summary(value)


class VisitFeedbackSupersede(M):
    category: VisitFeedbackCategory
    public_summary: str = Field(min_length=1, max_length=5000)
    sentiment: VisitFeedbackSentiment | None = None
    created_by: str | None = Field(None, max_length=200)

    _validate_summary = field_validator("public_summary")(validate_visit_feedback_summary)

# OWNER 0.2 P5 - in-app notifications ---------------------------------------
NotificationType = Literal[
    "publication_published",
    "visit_feedback_published",
    "shared_document_published",
    "request_handled",
]
NotificationTargetType = Literal[
    "owner_publication",
    "owner_visit_feedback",
    "owner_shared_document",
    "owner_feedback",
]


class OwnerNotificationDTO(M):
    id: int
    type: NotificationType
    title: str
    body: str
    created_at: datetime
    read_at: datetime | None = None
    target_type: NotificationTargetType
    target_id: int


class NotificationPreferencesDTO(M):
    in_app_enabled: bool
    publication_enabled: bool
    visit_feedback_enabled: bool
    document_enabled: bool
    request_update_enabled: bool


class NotificationPreferencesUpdate(NotificationPreferencesDTO):
    pass
