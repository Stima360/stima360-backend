from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import (BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr,
                      field_validator, model_validator)


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


class LoginLinkRequest(M):
    """LMC-1B: l'indirizzo, e nient'altro.

    Nessun `agency_id`, nessun `owner_account_id`: chi chiede il link non
    sceglie il tenant, lo si ricava dal contatto che porta l'indirizzo. Il
    limite di 320 caratteri e' quello di `contacts.email` e ferma un corpo
    enorme prima che diventi una query.

    La validazione e' volutamente LARGA - una stringa non vuota - perche' un
    422 su un indirizzo malformato e un 204 su uno sconosciuto sarebbero due
    risposte diverse, cioe' un canale di enumerazione: l'indirizzo che non
    esiste e quello che non e' nemmeno un indirizzo devono finire nello stesso
    silenzio, e ci arrivano perche' la normalizzazione non trova nessuno.
    """

    email: str = Field(min_length=1, max_length=320)


class HomeSummary(M):
    """LMC-2: una casa nella lista. Solo campi descrittivi e di stato."""

    stima_id: int
    comune: str | None = None
    microzona: str | None = None
    via: str | None = None
    civico: str | None = None
    tipologia: str | None = None
    mq: int | None = None
    created_at: datetime | None = None
    has_watch: bool
    initial_value: float | None = None
    last_update_at: datetime | None = None
    data_status: Literal["ready", "partial", "building_history"]


class HomeListResponse(M):
    items: list[HomeSummary]


class HomeEventCreate(M):
    """LMC-7: l'unica cosa che il client puo' dire sul proprio comportamento.

    Un `Literal`, non una stringa: l'insieme chiuso e' applicato dallo
    schema, quindi un'azione inventata riceve un 422 senza arrivare a
    toccare il dominio. Non c'e' `event_type`, non c'e' `contact_id`, non
    c'e' `lead_id`, non c'e' `agency_id`: sono tutte cose che il server
    deriva, e che un portale non deve poter dichiarare - `seller_timeline_events`
    e' la memoria su cui il CRM decide chi richiamare.
    """

    action: Literal["value_history_viewed", "buyer_demand_viewed"]


class HomeProfileUpdate(BaseModel):
    """LMC-10: cio' che il proprietario puo' correggere della propria casa.

    `extra="forbid"`: una chiave fuori da questo elenco riceve un 422 senza
    arrivare al dominio. E' la stessa whitelist di `home_profile` e delle
    colonne della migration 068, e un test la riconfronta con quelle - tre
    elenchi che devono coincidere e che nessuno confronta prima o poi non
    coincidono.

    COSA NON C'E', E NON PER DIMENTICANZA. `comune` e `microzona` scelgono la
    base EUR/mq; `via` e `civico` sono identita'; `tipologia`, la posizione e
    la vista mare sono classificazione. Per quelli la strada e' la richiesta
    di verifica gratuita di LMC-9. E naturalmente non c'e' nessun
    `agency_id`, `contact_id`, `owner_account_id` o `lead_id`: il server li
    deriva, sempre.

    I tipi sono STRETTI: `StrictInt` rifiuta `true` e `"95"`, che pydantic in
    modalita' permissiva convertirebbe in `1` e `95`. Un browser che manda
    una casella di spunta dove va un numero deve sentirselo dire, non vedere
    la propria casa diventare di un metro quadro. La validazione vera resta
    pero' in `owner.home_update.normalize_patch` (limiti, insiemi chiusi,
    token delle pertinenze): questo schema e' il primo cancello, non l'unico.
    """

    model_config = ConfigDict(extra="forbid")

    #: La versione del profilo su cui il form e' stato aperto. `0` significa
    #: "nessuna correzione ancora", ed e' il valore della prima modifica.
    expected_version: StrictInt = Field(ge=0)

    mq: StrictInt | None = None
    piano: StrictStr | None = None
    locali: StrictInt | None = None
    bagni: StrictInt | None = None
    ascensore: StrictBool | None = None
    anno: StrictInt | None = None
    stato: StrictStr | None = None
    pertinenze: list[StrictStr] | None = None
    mqgiardino: StrictInt | None = None
    mqgarage: StrictInt | None = None
    mqcantina: StrictInt | None = None
    mqpostoauto: StrictInt | None = None
    mqtaverna: StrictInt | None = None
    mqsoffitta: StrictInt | None = None
    mqterrazzo: StrictInt | None = None
    numbalconi: StrictInt | None = None
    altrodescrizione: StrictStr | None = None

    def patch(self) -> dict:
        """Solo i campi che il client ha davvero mandato.

        `exclude_unset` e' la differenza fra "non l'ho toccato" e "l'ho messo
        a null": il primo non entra nella patch, il secondo si' e viene
        rifiutato piu' avanti, perche' in LMC-10 cancellare un valore non e'
        un gesto che esiste.
        """
        dati = self.model_dump(exclude_unset=True)
        dati.pop("expected_version", None)
        return dati


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


# LMC-12 - le notifiche PRE-INCARICO ("Novita' sulla tua casa"). Stream
# separato da P5: un DTO suo, chiuso, e `stima_id` al posto di
# `target_type`/`target_id` perche' il bersaglio e' sempre la casa. Niente
# `evidence`, niente impronta dell'algoritmo, niente id di osservazione.
HomeNotificationType = Literal[
    "home_value_changed",
    "home_demand_changed",
    "home_method_changed",
]


class OwnerHomeNotificationDTO(M):
    id: int
    type: HomeNotificationType
    stima_id: int
    title: str
    body: str
    created_at: datetime
    read_at: datetime | None = None


class HomeNotificationListResponse(M):
    items: list[OwnerHomeNotificationDTO]
    limit: int
    offset: int
    has_more: bool


# LMC-13 - le metriche di acquisizione, operator-facing. DTO CHIUSO e senza
# PII: solo interi, tassi, i due confini della finestra e le ragioni di cio'
# che non e' misurabile. Nessun identificativo (stima, account, contatto,
# lead, immobile), nessun dato personale, nessun punteggio.
class HomeMetricsRates(M):
    view_rate: float | None = None
    return_rate: float | None = None
    strong_interest_rate: float | None = None
    consultation_rate: float | None = None
    inspection_rate: float | None = None
    mandate_rate: float | None = None


class HomeMetricsResponse(M):
    period_days: int
    cohort_from: str
    cohort_to: str
    unit: Literal["stima"]

    cohort_homes: int
    activated_owners: int
    active_homes_now: int

    viewed_homes: int
    returning_homes: int

    value_interest_homes: int
    demand_interest_homes: int
    updated_homes: int
    strong_interest_homes: int

    consultation_homes: int

    # Sempre null: nessuna fonte autorevole. Il tipo lo dichiara.
    inspection_homes: None = None
    mandate_homes: None = None

    rates: HomeMetricsRates
    not_measurable: dict[str, str]
