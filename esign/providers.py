"""BLOCCO-6B - l'interfaccia verso un servizio di firma e l'adattatore SIMULATO.

`SignatureProvider` e' il contratto che un futuro fornitore reale dovra'
rispettare (nessun fornitore e' scelto ne' integrato). L'unico adattatore
presente e' `SimulatedProvider`, locale: non contatta nessuno, non produce
firme, e il documento che «riceve» e' una ricevuta di simulazione che dice
in chiaro di NON essere un documento firmato.

Scelta del fornitore: variabile `ESIGN_PROVIDER`. Assente o `simulated` ->
l'adattatore simulato; ogni altro valore -> nessun fornitore (il servizio
risponde «non configurato»). Non esiste un valore che attivi una firma reale.
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


@dataclass(frozen=True)
class ProviderRequest:
    reference: str


@dataclass(frozen=True)
class ProviderDocument:
    content: bytes
    media_type: str


class SignatureProvider(Protocol):
    name: str
    is_simulation: bool

    def create_request(self, *, document: bytes, document_sha256: str, signer_name: str, signer_contact: str,
                       expires_at: datetime, idempotency_key: str) -> ProviderRequest: ...

    def cancel_request(self, reference: str) -> None: ...

    def fetch_document(self, reference: str, *, document_sha256: str, signer_name: str) -> ProviderDocument: ...


class SimulatedProvider:
    """Locale e riconoscibile: riferimenti `SIM-...`, nessuna rete, nessuna firma."""

    name = SIMULATED
    is_simulation = True

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


def current() -> SignatureProvider:
    value = (os.getenv(PROVIDER_ENV) or SIMULATED).strip()
    if value == SIMULATED:
        return SimulatedProvider()
    raise ProviderNotConfigured(value)
