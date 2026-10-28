"""BLOCCO-1B - il contratto dei dati agenzia per i report.

Un solo modello valida sia cio' che si scrive sia cio' che si rilegge da
`agencies.settings`: due modelli avrebbero significato due idee di cosa sia un
profilo valido (la stessa regola di P27-4 per la configurazione).
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import MAX_LENGTHS

# Caratteri di controllo (a capo compresi): nessun campo e' multi-riga.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Un indirizzo email plausibile, senza pretese di RFC 5322: una parte locale,
# una @, un dominio con almeno un punto, nessuno spazio.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", re.IGNORECASE)


def _clean(value, field: str):
    """Spazi tolti ai bordi; vuoto -> assente. Mai un dato inventato."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("deve essere un testo")
    value = value.strip()
    if not value:
        return None
    if _CONTROL.search(value):
        raise ValueError("non puo' contenere a capo o caratteri di controllo")
    if len(value) > MAX_LENGTHS[field]:
        raise ValueError(f"al massimo {MAX_LENGTHS[field]} caratteri")
    return value


class AgencyProfile(BaseModel):
    """I dati dell'agenzia per l'intestazione dei report.

    Solo `display_name` e' richiesto in questa fase. Gli altri campi sono
    facoltativi: se mancano restano assenti, e nulla li ricava altrove.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(..., description="Nome da mostrare nei report")
    legal_name: str | None = None
    vat_number: str | None = None
    tax_code: str | None = None
    legal_address: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None

    @field_validator("display_name", mode="before")
    @classmethod
    def _display_name(cls, value):
        value = _clean(value, "display_name")
        if value is None:
            raise ValueError("il nome da mostrare nei report e' obbligatorio")
        return value

    @field_validator("legal_name", "vat_number", "tax_code", "legal_address", "phone", mode="before")
    @classmethod
    def _optional_text(cls, value, info):
        return _clean(value, info.field_name)

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, value):
        value = _clean(value, "email")
        if value is not None and not _EMAIL.match(value):
            raise ValueError("indirizzo email non valido")
        return value

    @field_validator("website", mode="before")
    @classmethod
    def _website(cls, value):
        value = _clean(value, "website")
        if value is None:
            return None
        parts = urlsplit(value)
        if (parts.scheme.lower() not in ("http", "https") or not parts.hostname
                or not _HOST.match(parts.hostname) or parts.username or parts.password):
            raise ValueError("indirizzo web non valido: usare http:// o https://")
        return value


class LogoMetadata(BaseModel):
    content_type: str
    width: int
    height: int
    size_bytes: int
    sha256: str
    uploaded_at: str | None = None
    url: str


class AgencyProfileResponse(BaseModel):
    """Il profilo come lo legge l'interfaccia.

    `configured` dice se i dati sono mai stati salvati. `agency_name` e' il
    nome dell'anagrafica (`agencies.name`), mostrato dall'anteprima quando il
    nome per i report non e' ancora stato scelto: e' un dato reale, non un
    valore inventato, e non viene salvato al posto del profilo.
    """

    agency_id: int
    agency_name: str
    configured: bool
    profile: dict
    updated_at: str | None = None
    can_edit: bool
    logo: LogoMetadata | None = None
    #: False finche' la migrazione 115 non e' applicata: i dati si salvano, il
    #: logo no (le sue rotte rispondono 503).
    logo_available: bool = True
