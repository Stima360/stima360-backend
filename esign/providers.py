"""BLOCCO-6B - l'interfaccia verso un servizio di firma e l'adattatore SIMULATO.

`SignatureProvider` e' il contratto che un futuro fornitore reale dovra'
rispettare (nessun fornitore e' scelto ne' integrato). L'unico adattatore
presente e' `SimulatedProvider`, locale: non contatta nessuno, non produce
firme, e il documento che «riceve» e' una ricevuta di simulazione che dice
in chiaro di NON essere un documento firmato.

Scelta del fornitore: variabile `ESIGN_PROVIDER`. Assente o `simulated` ->
l'adattatore simulato; ogni altro valore -> nessun fornitore (il servizio
risponde «non configurato»). Non esiste un valore che attivi una firma reale:
`REAL_PROVIDERS` e' vuoto finche' un fornitore non e' scelto e il suo
adattatore scritto sulla sua documentazione ufficiale.

BLOCCO-5: IL CONTRATTO DI UN FORNITORE REALE (indipendente dal fornitore)

    environment       'sandbox' o 'production' (dichiarato dall'adattatore,
                      registrato sulla richiesta; firma valida solo in
                      'production')
    create_request    IDEMPOTENTE sulla chiave: richiamata con la stessa chiave
                      restituisce la stessa richiesta remota (nessun orfano se
                      il nostro INSERT fallisce dopo la chiamata)
    fetch_status      lo stato ATTUALE letto dal fornitore: un evento webhook
                      non basta mai da solo, lo stato si rilegge
    fetch_document    il documento firmato; `fetch_audit_trail` il rapporto
                      di completamento
    verify_event      autenticita' dell'evento ricevuto (firma del corpo
                      grezzo; vedi `verify_hmac_sha256`) e suo id univoco;
                      solleva `InvalidEventSignature`
"""
from __future__ import annotations

import hashlib
import hmac
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


#: Gli stati che un fornitore puo' riportare, tradotti nei nostri.
PROVIDER_STATUSES = ("pending", "signed", "declined", "expired", "cancelled")


@dataclass(frozen=True)
class ProviderStatus:
    status: str                         # uno di PROVIDER_STATUSES
    signed_at: datetime | None = None
    signature_level: str | None = None  # 'ses' | 'aes' | 'qes', come dichiarato dal fornitore


@dataclass(frozen=True)
class ProviderEvent:
    event_id: str
    event_type: str
    reference: str | None


class InvalidEventSignature(Exception):
    """L'evento non e' autentico (firma assente, sbagliata, corpo alterato)."""


class SignatureProvider(Protocol):
    name: str
    is_simulation: bool
    environment: str                    # 'simulation' | 'sandbox' | 'production'

    def create_request(self, *, document: bytes, document_sha256: str, signer_name: str, signer_contact: str,
                       expires_at: datetime, idempotency_key: str) -> ProviderRequest: ...

    def cancel_request(self, reference: str) -> None: ...

    def fetch_status(self, reference: str) -> ProviderStatus: ...

    def fetch_document(self, reference: str, *, document_sha256: str, signer_name: str) -> ProviderDocument: ...

    def fetch_audit_trail(self, reference: str) -> ProviderDocument | None: ...

    def verify_event(self, headers: dict, raw_body: bytes) -> ProviderEvent: ...


def verify_hmac_sha256(secret: str, raw_body: bytes, received: str | None, *, prefix: str = "sha256=") -> None:
    """Verifica una firma HMAC-SHA256 del corpo GREZZO (mai del JSON
    ricostruito), confronto a tempo costante. Solleva `InvalidEventSignature`.
    Il formato (header, prefisso) e' quello documentato dal fornitore scelto."""
    if not secret or not received or not received.startswith(prefix):
        raise InvalidEventSignature("missing")
    expected = prefix + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received.strip()):
        raise InvalidEventSignature("mismatch")


class SimulatedProvider:
    """Locale e riconoscibile: riferimenti `SIM-...`, nessuna rete, nessuna firma."""

    name = SIMULATED
    is_simulation = True
    environment = "simulation"

    def fetch_status(self, reference: str) -> ProviderStatus:
        return ProviderStatus("pending")

    def fetch_audit_trail(self, reference: str) -> ProviderDocument | None:
        return None

    def verify_event(self, headers: dict, raw_body: bytes) -> ProviderEvent:
        # la simulazione non riceve eventi: nessun webhook la tocca
        raise InvalidEventSignature("simulation")

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


#: Gli adattatori REALI disponibili, per nome. Vuoto: nessun fornitore e'
#: scelto (vedi roadmap/BLOCCO-5_FIRMA_FORNITORI.md). Un adattatore si
#: aggiunge qui solo scritto sulla documentazione ufficiale del fornitore.
REAL_PROVIDERS: dict[str, type] = {}


def current() -> SignatureProvider:
    value = (os.getenv(PROVIDER_ENV) or SIMULATED).strip()
    if value == SIMULATED:
        return SimulatedProvider()
    if value in REAL_PROVIDERS:
        return REAL_PROVIDERS[value]()
    raise ProviderNotConfigured(value)


def by_name(name: str) -> SignatureProvider:
    """L'adattatore che ha creato una richiesta (per annullarla o leggerne lo
    stato), anche se nel frattempo `ESIGN_PROVIDER` e' cambiato."""
    if name == SIMULATED:
        return SimulatedProvider()
    if name in REAL_PROVIDERS:
        return REAL_PROVIDERS[name]()
    raise ProviderNotConfigured(name)
