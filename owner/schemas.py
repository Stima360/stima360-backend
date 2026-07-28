from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        "general_update", "marketing_update", "visit_update",
        "feedback_summary", "strategy_update", "milestone"
    ]
    title: str = Field(min_length=1, max_length=200)
    summary: str | None = Field(None, max_length=1000)
    body: str = Field(min_length=1, max_length=20000)
    acknowledgement_required: bool = False


class PublicationUpdate(M):
    publication_type: Literal[
        "general_update", "marketing_update", "visit_update",
        "feedback_summary", "strategy_update", "milestone"
    ] | None = None
    title: str | None = Field(None, min_length=1, max_length=200)
    summary: str | None = Field(None, max_length=1000)
    body: str | None = Field(None, min_length=1, max_length=20000)
    acknowledgement_required: bool | None = None


class FeedbackCreate(M):
    feedback_type: Literal[
        "contact_request", "correction_request", "general_message",
        "strategy_feedback", "price_review", "availability_update",
        "document_question"
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


class FeedbackStatus(M):
    status: Literal["new", "in_review", "handled", "closed"]
    handled_by: str | None = Field(None, max_length=200)
    public_response: str | None = Field(None, max_length=5000)


class SharedDocumentCreate(M):
    property_document_id: int
    owner_account_id: int | None = None
    public_title: str = Field(min_length=1, max_length=200)
    public_document_type: str = Field(min_length=1, max_length=50)
    expires_at: datetime | None = None
    acknowledgement_required: bool = False
    created_by: str | None = Field(None, max_length=200)


class SharedDocumentUpdate(M):
    public_title: str | None = Field(None, min_length=1, max_length=200)
    public_document_type: str | None = Field(None, min_length=1, max_length=50)
    expires_at: datetime | None = None
    acknowledgement_required: bool | None = None


class SharedDocumentSupersede(M):
    public_title: str = Field(min_length=1, max_length=200)
    public_document_type: str = Field(min_length=1, max_length=50)
    expires_at: datetime | None = None
    acknowledgement_required: bool = False
    created_by: str | None = Field(None, max_length=200)


class RevokeRequest(M):
    actor: str | None = Field(None, max_length=200)


class VisitFeedbackCreate(M):
    property_visit_id: int
    owner_account_id: int | None = None
    category: Literal["price", "state", "layout", "location", "accessories", "general"]
    public_summary: str = Field(min_length=1, max_length=5000)
    sentiment: Literal["positive", "neutral", "negative", "mixed"] | None = None
    created_by: str | None = Field(None, max_length=200)


class VisitFeedbackUpdate(M):
    category: Literal["price", "state", "layout", "location", "accessories", "general"] | None = None
    public_summary: str | None = Field(None, min_length=1, max_length=5000)
    sentiment: Literal["positive", "neutral", "negative", "mixed"] | None = None


class VisitFeedbackSupersede(M):
    category: Literal["price", "state", "layout", "location", "accessories", "general"]
    public_summary: str = Field(min_length=1, max_length=5000)
    sentiment: Literal["positive", "neutral", "negative", "mixed"] | None = None
    created_by: str | None = Field(None, max_length=200)
