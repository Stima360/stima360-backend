"""BLOCCO-6B / FIRMA OTP - come si firma un foglio visita: la SIMULAZIONE locale o la FIRMA OTP STIMA360.

`ESIGN_PROVIDER` sceglie il modo (variabile d'ambiente, mai dal browser):

    assente | simulated   l'adattatore SIMULATO: locale, nessuna rete, nessuna firma. Il suo «documento ricevuto»
                          e' una ricevuta che dichiara «SIMULAZIONE — NESSUNA FIRMA VALIDA».
    stima360_otp          la FIRMA ELETTRONICA SEMPLICE (FES) proprietaria: il cliente riceve un collegamento personale
                          via email, legge il PDF, dichiara di voler firmare e conferma con un codice OTP ricevuto
                          per email; l'agente firma dal CRM, autenticato, con la stessa dichiarazione. Tutto il
                          procedimento vive in `esign.otp_service`; qui solo la scelta del modo.
    altro                 nessun servizio: il CRM risponde «non configurato». Nessun valore attiva un fornitore
                          esterno: non ne esiste alcuno nel codice.

`SignatureProvider` resta il contratto minimo che il servizio usa per l'adattatore simulato (creazione, annullamento,
documento ricevuto). La firma OTP non e' un «fornitore»: e' il CRM stesso, e il suo nome nel database
(`provider = 'stima360_otp'`) serve a distinguere le richieste nate da questo procedimento.
"""
from __future__ import annotations

import io
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

PROVIDER_ENV = "ESIGN_PROVIDER"
SIMULATED = "simulated"
#: La firma OTP proprietaria: il valore di `ESIGN_PROVIDER` che la attiva e il nome scritto sulle richieste.
OTP_PROVIDER = "stima360_otp"
MODES = (SIMULATED, OTP_PROVIDER)


@dataclass(frozen=True)
class ProviderRequest:
    reference: str


@dataclass(frozen=True)
class ProviderDocument:
    content: bytes
    media_type: str


class ProviderError(Exception):
    """Un passo del procedimento non e' riuscito. Mai segreti nel messaggio."""

    def __init__(self, code: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class SignatureProvider(Protocol):
    name: str
    is_simulation: bool
    environment: str                    # 'simulation' | 'sandbox' | 'production'

    def create_request(self, *, document: bytes, document_sha256: str, signer_name: str, signer_contact: str,
                       expires_at: datetime, idempotency_key: str) -> ProviderRequest: ...

    def cancel_request(self, reference: str) -> None: ...

    def fetch_document(self, reference: str, *, document_sha256: str, signer_name: str) -> ProviderDocument: ...


class SimulatedProvider:
    """Locale e riconoscibile: riferimenti `SIM-...`, nessuna rete, nessuna firma."""

    name = SIMULATED
    is_simulation = True
    environment = "simulation"

    def create_request(self, *, document, document_sha256, signer_name, signer_contact, expires_at,
                       idempotency_key) -> ProviderRequest:
        return ProviderRequest(reference=f"SIM-{uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key).hex[:16]}")

    def cancel_request(self, reference: str) -> None:
        return None

    def fetch_document(self, reference: str, *, document_sha256: str, signer_name: str) -> ProviderDocument:
        return ProviderDocument(content=simulation_receipt(reference, document_sha256, signer_name),
                                media_type="application/pdf")


def simulation_receipt(reference: str, document_sha256: str, signer_name: str) -> bytes:
    """Una pagina che dice cosa e': esito SIMULATO, nessuna firma valida."""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    from property_valuations.pdf_render import _register_fonts, _styles, clean_text

    _register_fonts()
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, title="SIMULAZIONE - documento non firmato")
    story = [Paragraph("SIMULAZIONE — NESSUNA FIRMA VALIDA", styles["title"]), Spacer(1, 8)]
    for line in (
        "Questo file è prodotto dall'adattatore di firma SIMULATO di STIMA360, solo per prova.",
        "Non contiene alcuna firma elettronica, semplice, avanzata o qualificata, e non attesta "
        "che il documento sia stato sottoscritto da qualcuno.",
        f"Riferimento di simulazione: {reference}",
        f"Firmatario indicato (non verificato): {signer_name}",
        f"Impronta SHA-256 del foglio visita a cui si riferisce: {document_sha256}",
    ):
        story += [Paragraph(clean_text(line), styles["body"]), Spacer(1, 6)]
    doc.build(story)
    return buffer.getvalue()


class ProviderNotConfigured(Exception):
    pass


def mode() -> str:
    """Il modo configurato: `simulated` (default) o `stima360_otp`. Altro -> `ProviderNotConfigured`."""
    value = (os.getenv(PROVIDER_ENV) or SIMULATED).strip()
    if value in MODES:
        return value
    raise ProviderNotConfigured(value)


def otp_enabled() -> bool:
    try:
        return mode() == OTP_PROVIDER
    except ProviderNotConfigured:
        return False


def current() -> SignatureProvider:
    """L'adattatore simulato, quando il modo e' la simulazione. Per la firma OTP non c'e' un adattatore:
    il servizio passa da `esign.otp_service`."""
    if mode() == SIMULATED:
        return SimulatedProvider()
    raise ProviderNotConfigured(OTP_PROVIDER)


def by_name(name: str) -> SignatureProvider:
    """L'adattatore che ha creato una richiesta simulata (per annullarla), anche se `ESIGN_PROVIDER` e' cambiato."""
    if name == SIMULATED:
        return SimulatedProvider()
    raise ProviderNotConfigured(name)
