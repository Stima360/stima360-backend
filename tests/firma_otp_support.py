"""FIRMA OTP - attrezzi dei test PostgreSQL: configurazione, trasporto email FINTO che cattura i messaggi, estrazione
di collegamento e codice dalle email (come farebbe il cliente), foglio di una visita svolta."""
from __future__ import annotations

import re
import uuid

from communication.providers.base import OUTCOME_ACCEPTED, OUTCOME_REJECTED, ProviderCapabilities, ProviderResult
from esign import otp_security

PEPPER = "pepe-di-prova-molto-lungo-0123456789-abcdef"
KEY = "chiave-prove-di-prova-molto-lunga-0123456789"
CLIENTE = "cliente.otp@esempio.invalid"
API = "/api/signature-requests"
PUB = "/api/public/esign/sign"


class Posta:
    """Il trasporto finto: cattura ogni messaggio che il dispatcher gli consegna. `rifiuta` simula un SMTP guasto."""
    NAME = "posta-di-prova"
    CAPABILITIES = ProviderCapabilities(returns_message_id=False, distinguishes_failure_class=True, reports_delivery=False)

    def __init__(self):
        self.messaggi: list[dict] = []
        self.rifiuta = False

    def send(self, message):
        self.messaggi.append(dict(message))
        if self.rifiuta:
            return ProviderResult(outcome=OUTCOME_REJECTED, error_code="provider_unavailable", error_detail="smtp finto spento")
        return ProviderResult(outcome=OUTCOME_ACCEPTED)

    def per(self, reason_code: str, destinatario: str | None = None) -> list[dict]:
        return [m for m in self.messaggi if m["reason_code"] == reason_code and (destinatario is None or m["destination_snapshot"] == destinatario)]


def configura(monkeypatch, posta: Posta, *, destinatari: str = CLIENTE):
    monkeypatch.setenv("ESIGN_PROVIDER", "stima360_otp")
    monkeypatch.setenv(otp_security.PEPPER_ENV, PEPPER)
    monkeypatch.setenv(otp_security.EVIDENCE_KEY_ENV, KEY)
    monkeypatch.setenv(otp_security.RECIPIENTS_ENV, destinatari)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://test.invalid")
    monkeypatch.delenv("ESIGN_DISPATCH_IMMEDIATE", raising=False)
    for name in otp_security.DEFAULTS:
        monkeypatch.delenv(name, raising=False)
    from esign import emails
    monkeypatch.setattr(emails, "_provider", lambda: posta)


def link_da(messaggio: dict) -> tuple[str, str]:
    """(URL, token) dall'email del collegamento."""
    m = re.search(r'href="(https://test\.invalid/firma/([A-Za-z0-9_-]+))"', messaggio["rendered_body"])
    assert m, "nessun collegamento nell'email"
    return m.group(1), m.group(2)


def codice_da(messaggio: dict) -> str:
    m = re.search(r">(\d{6})<", messaggio["rendered_body"])
    assert m, "nessun codice nell'email"
    return m.group(1)


def crea(client, sheet_id: int, *, email: str = CLIENTE, nome: str = "Cliente OTP", key=None, **extra):
    return client.post(API, json={"visit_sheet_id": sheet_id, "signer_name": nome, "signer_contact": email,
                                  "expires_in_days": 7, "idempotency_key": key or str(uuid.uuid4()), **extra})


def foglio(b, *, agent="agent_a"):
    """Un foglio di una visita svolta (agente: `agent`)."""
    from tests.test_blocco_5a_visit_booking_postgres import _property
    from tests.test_blocco_6a_visit_sheets_postgres import _visit
    aid = _visit(b, _property(b), agent=agent)
    from tests.test_blocco_2c_integrated_postgres import _q
    _q(b, "SET session_replication_role = replica; UPDATE appointments SET status = 'completed', "
          "completed_at = start_at + interval '50 minutes' WHERE id = %s", (aid,))
    r = b["login"]("owner_a").post(f"/api/visit-sheets/appointments/{aid}")
    assert r.status_code == 200, r.text
    return r.json()["sheet"]
