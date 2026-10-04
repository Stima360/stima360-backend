from typing import Any

from pydantic import BaseModel


class Contact360Response(BaseModel):
    contact: dict[str, Any]
    roles: list[dict[str, Any]]
    leads: list[dict[str, Any]]
    properties: list[dict[str, Any]]
    buy_requests: list[dict[str, Any]]
    matches: list[dict[str, Any]]
    visits: list[dict[str, Any]]
    activities: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    # LMC-8: additivo. Le nove sezioni storiche restano quelle che erano.
    owner_home: dict[str, Any]


# ---------------------------------------------------------------------------
# VENDITORI-1 - «questo proprietario vende questo specifico immobile»
# ---------------------------------------------------------------------------
from pydantic import ConfigDict, Field  # noqa: E402


class SellerActivate(BaseModel):
    """Attiva (o riattiva) Vende per un proprietario su un immobile.
    `lead_id`: la scelta esplicita fra i candidati quando il riuso non e'
    univoco; `new_lead`: crea comunque un lead nuovo. Mai entrambi.
    `extra=forbid`: agenzia, agente e stato li decide il server."""
    model_config = ConfigDict(extra="forbid")
    property_id: int = Field(..., ge=1)
    contact_id: int = Field(..., ge=1)
    lead_id: int | None = Field(None, ge=1)
    new_lead: bool = False


class SellerDeactivate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    property_id: int = Field(..., ge=1)
    contact_id: int = Field(..., ge=1)
    outcome: str = Field(..., pattern="^(paused|not_selling|mistake)$")
    note: str | None = Field(None, max_length=500)
