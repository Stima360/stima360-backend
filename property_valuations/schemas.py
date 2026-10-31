"""BLOCCO-2A - il contenuto di una valutazione dell'agente.

Un solo modello per bozza e approvazione: in bozza ogni campo puo' mancare,
ma cio' che c'e' deve essere valido (importi positivi, minimo <= massimo, date,
URL). La completezza richiesta per approvare la decide `approval_issues`.

Importi: `Decimal` con due decimali, mai float, mai zero (un importo assente e'
None: "non valutato"). Nessun importo viene ricavato da un altro: il canone
non e' una percentuale del prezzo, e il server non calcola nulla.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    COMPARABLE_KINDS,
    COMPARABLE_PRICE_TYPES,
    COMPARABLE_RENT_PERIODS,
    LONG_TEXT,
    MAX_COMPARABLES,
    MEDIUM_TEXT,
    RENT_EXPENSES,
    RENT_FURNISHING,
    RENT_SEGMENTS,
    SHORT_TEXT,
    SURFACE_KINDS,
    SURFACE_SOURCES,
)

#: Importo in euro: positivo, finito, al massimo 12 cifre intere e 2 decimali.
Money = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)]
#: Superficie in metri quadrati: positiva, finita.
Area = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2, allow_inf_nan=False)]

# I testi lunghi ammettono gli a capo; nessun altro carattere di controllo.
_CONTROL_LONG = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CONTROL_SHORT = re.compile(r"[\x00-\x1f\x7f]")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", re.IGNORECASE)


def _text(value, limit: int, *, multiline: bool):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("deve essere un testo")
    value = value.strip()
    if not value:
        return None
    if (_CONTROL_LONG if multiline else _CONTROL_SHORT).search(value):
        raise ValueError("contiene caratteri non ammessi")
    if len(value) > limit:
        raise ValueError(f"al massimo {limit} caratteri")
    return value


def _choice(value, choices: dict, label: str):
    if value is None or value == "":
        return None
    if value not in choices:
        raise ValueError(f"{label} non valido")
    return value


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Surface(_Model):
    value: Area | None = None
    kind: str | None = None
    source: str | None = None
    note: str | None = None

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v):
        return _choice(v, SURFACE_KINDS, "tipo di superficie")

    @field_validator("source", mode="before")
    @classmethod
    def _source(cls, v):
        return _choice(v, SURFACE_SOURCES, "provenienza del dato")

    @field_validator("note", mode="before")
    @classmethod
    def _note(cls, v):
        return _text(v, SHORT_TEXT, multiline=False)

    def is_empty(self) -> bool:
        return all(getattr(self, f) is None for f in type(self).model_fields)


def _check_range(model, label: str):
    if model.min is not None and model.max is not None and model.min > model.max:
        raise ValueError(f"{label}: il minimo non puo' superare il massimo")
    return model


class SaleSection(_Model):
    min: Money | None = None
    max: Money | None = None
    recommended: Money | None = None
    outside_range_reason: str | None = None

    @field_validator("outside_range_reason", mode="before")
    @classmethod
    def _reason(cls, v):
        return _text(v, MEDIUM_TEXT, multiline=True)

    @model_validator(mode="after")
    def _range(self):
        return _check_range(self, "vendita")

    def is_empty(self) -> bool:
        return all(getattr(self, f) is None for f in type(self).model_fields)


class RentSection(_Model):
    """Canone MENSILE di locazione. Nessuna tariffa giornaliera turistica."""

    segment: str | None = None
    furnishing: str | None = None
    expenses: str | None = None
    min: Money | None = None
    max: Money | None = None
    recommended: Money | None = None
    outside_range_reason: str | None = None
    notes: str | None = None

    @field_validator("segment", mode="before")
    @classmethod
    def _segment(cls, v):
        return _choice(v, RENT_SEGMENTS, "segmento di locazione")

    @field_validator("furnishing", mode="before")
    @classmethod
    def _furnishing(cls, v):
        return _choice(v, RENT_FURNISHING, "arredo")

    @field_validator("expenses", mode="before")
    @classmethod
    def _expenses(cls, v):
        return _choice(v, RENT_EXPENSES, "spese")

    @field_validator("outside_range_reason", "notes", mode="before")
    @classmethod
    def _long(cls, v):
        return _text(v, MEDIUM_TEXT, multiline=True)

    @model_validator(mode="after")
    def _range(self):
        return _check_range(self, "locazione")

    def is_empty(self) -> bool:
        return all(getattr(self, f) is None for f in type(self).model_fields)


class Comparable(_Model):
    """Un comparabile inserito dall'operatore. Nessun dato e' recuperato dagli
    URL: l'indirizzo e' solo un riferimento."""

    kind: str
    price_type: str
    amount: Money
    rent_period: str | None = None
    surface: Area | None = None
    surface_kind: str | None = None
    zone: str | None = None
    features: str | None = None
    source: str
    data_date: date | None = None
    url: str | None = None
    differences: str | None = None

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v):
        value = _choice(v, COMPARABLE_KINDS, "tipo di comparabile")
        if value is None:
            raise ValueError("indicare vendita o locazione")
        return value

    @field_validator("price_type", mode="before")
    @classmethod
    def _price_type(cls, v):
        value = _choice(v, COMPARABLE_PRICE_TYPES, "tipo di prezzo")
        if value is None:
            raise ValueError("indicare prezzo richiesto o concluso")
        return value

    @field_validator("rent_period", mode="before")
    @classmethod
    def _period(cls, v):
        return _choice(v, COMPARABLE_RENT_PERIODS, "periodo del canone")

    @field_validator("surface_kind", mode="before")
    @classmethod
    def _surface_kind(cls, v):
        return _choice(v, SURFACE_KINDS, "tipo di superficie")

    @field_validator("zone", mode="before")
    @classmethod
    def _zone(cls, v):
        return _text(v, SHORT_TEXT, multiline=False)

    @field_validator("features", "differences", mode="before")
    @classmethod
    def _long(cls, v):
        return _text(v, MEDIUM_TEXT, multiline=True)

    @field_validator("source", mode="before")
    @classmethod
    def _source(cls, v):
        value = _text(v, SHORT_TEXT, multiline=False)
        if value is None:
            raise ValueError("indicare la fonte del comparabile")
        return value

    @field_validator("data_date")
    @classmethod
    def _date(cls, v):
        if v is not None and v > date.today():
            raise ValueError("la data del dato non puo' essere futura")
        return v

    @field_validator("url", mode="before")
    @classmethod
    def _url(cls, v):
        value = _text(v, SHORT_TEXT, multiline=False)
        if value is None:
            return None
        parts = urlsplit(value)
        if (parts.scheme.lower() not in ("http", "https") or not parts.hostname
                or not _HOST.match(parts.hostname) or parts.username or parts.password):
            raise ValueError("indirizzo web non valido: usare http:// o https://")
        return value

    @model_validator(mode="after")
    def _period_matches_kind(self):
        if self.kind == "locazione" and self.rent_period is None:
            raise ValueError("per un comparabile di locazione indicare se il canone e' mensile o annuale")
        if self.kind == "vendita" and self.rent_period is not None:
            raise ValueError("il periodo del canone vale solo per i comparabili di locazione")
        return self


class ValuationContent(_Model):
    """Cio' che l'operatore inserisce. In bozza tutto puo' mancare."""

    characteristics: str | None = None
    surface: Surface = Field(default_factory=Surface)
    sale: SaleSection | None = None
    rent: RentSection | None = None
    rationale: str | None = None
    sources: str | None = None
    comparables: list[Comparable] = Field(default_factory=list, max_length=MAX_COMPARABLES)
    comparables_note: str | None = None

    @field_validator("characteristics", "rationale", mode="before")
    @classmethod
    def _long(cls, v):
        return _text(v, LONG_TEXT, multiline=True)

    @field_validator("sources", "comparables_note", mode="before")
    @classmethod
    def _medium(cls, v):
        return _text(v, MEDIUM_TEXT, multiline=True)

    @model_validator(mode="after")
    def _empty_sections_are_absent(self):
        """Una sezione senza alcun dato e' "non valutata": None, mai zero."""
        if self.sale is not None and self.sale.is_empty():
            self.sale = None
        if self.rent is not None and self.rent.is_empty():
            self.rent = None
        return self


class CreateRequest(_Model):
    property_id: int = Field(gt=0)
    content: ValuationContent = Field(default_factory=ValuationContent)


class UpdateRequest(_Model):
    row_version: int = Field(ge=1)
    content: ValuationContent


class ApproveRequest(_Model):
    row_version: int = Field(ge=1)


# ---------------------------------------------------------------------------
# completezza per l'approvazione
# ---------------------------------------------------------------------------

def _section_issues(section, label: str, required: tuple[str, ...]) -> tuple[bool, list[str]]:
    """(completa, problemi). Una sezione assente non e' un problema."""
    if section is None:
        return False, []
    missing = [name for name in required if getattr(section, name) is None]
    issues = []
    if missing:
        issues.append(f"La sezione {label} e' incompleta: completala o svuotala.")
        return False, issues
    if not (section.min <= section.recommended <= section.max) and section.outside_range_reason is None:
        issues.append(f"{label.capitalize()}: il valore consigliato e' fuori dall'intervallo, "
                      "serve una motivazione specifica.")
    return not issues, issues


def approval_issues(content: ValuationContent) -> list[str]:
    """I punti che impediscono l'approvazione, leggibili dall'operatore. Vuoto
    = approvabile. Nessun valore mancante viene sostituito o calcolato."""
    issues: list[str] = []
    sale_ok, sale_issues = _section_issues(content.sale, "vendita", ("min", "max", "recommended"))
    rent_ok, rent_issues = _section_issues(
        content.rent, "locazione", ("segment", "furnishing", "expenses", "min", "max", "recommended"))
    issues += sale_issues + rent_issues
    if not (sale_ok or rent_ok) and not (sale_issues or rent_issues):
        # nessuna sezione compilata: entrambe "non valutate"
        issues.append("Compila almeno una sezione completa: vendita o locazione.")
    if content.characteristics is None:
        issues.append("Indica le caratteristiche rilevanti dell'immobile.")
    surface = content.surface
    if surface.value is None or surface.kind is None or surface.source is None:
        issues.append("Indica la superficie utilizzata, il suo tipo e la provenienza del dato.")
    if content.rationale is None:
        issues.append("Scrivi la motivazione della valutazione.")
    if content.sources is None:
        issues.append("Dichiara le fonti o la base informativa della valutazione.")
    if not content.comparables and content.comparables_note is None:
        issues.append("Nessun comparabile: dichiara esplicitamente perche' non ce ne sono.")
    return issues


def amounts(content: ValuationContent) -> dict[str, Any]:
    """Le colonne riassuntive, prese tali e quali dal contenuto (None = non valutato)."""
    sale, rent = content.sale, content.rent
    return {
        "sale_min": sale.min if sale else None,
        "sale_max": sale.max if sale else None,
        "sale_recommended": sale.recommended if sale else None,
        "rent_min": rent.min if rent else None,
        "rent_max": rent.max if rent else None,
        "rent_recommended": rent.recommended if rent else None,
    }
