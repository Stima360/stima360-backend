"""DOCUMENSO - l'adattatore (API v2, buste) provato sul filo contro un Documenso FINTO. Nessuna rete esterna, nessun
database, nessuna credenziale vera. Prova che il NOSTRO codice parla come il contratto dell'SDK ufficiale dice: non
prova nulla sul comportamento del servizio vero (per quello serve la prima firma reale di prova)."""
from __future__ import annotations

import hashlib
import json

import pytest

from esign import documenso, providers
from esign.documenso import DocumensoProvider, field_geometry
from esign.providers import InvalidEventSignature, ProviderError, ProviderNotConfigured, SignerSpec
from tests.documenso_fake import TOKEN, WEBHOOK_SECRET, FakeDocumenso, webhook_body

PDF = b"%PDF-1.4\n% foglio visita sintetico\n%%EOF"
SHA = hashlib.sha256(PDF).hexdigest()
LAYOUT = {"page_width": 595.2756, "page_height": 841.8898, "boxes": {
    "visitor": {"page": 1, "x": 60.7, "y": 246.7, "width": 204.0, "height": 40.4},
    "agent": {"page": 1, "x": 330.6, "y": 246.7, "width": 204.0, "height": 40.4}}}
SIGNERS = (SignerSpec("visitor", "Cliente di Prova", "cliente.prova@esempio.invalid", 1),
           SignerSpec("agent", "Agente di Prova", "agente.prova@esempio.invalid", 1))
KEY = "6f1d3c5e-0a8e-4f43-9a3a-0b8f9c1d2e3f"


@pytest.fixture
def fake():
    f = FakeDocumenso()
    yield f
    f.close()


def provider(fake, **env):
    base = {"DOCUMENSO_API_URL": fake.url, "DOCUMENSO_API_TOKEN": TOKEN, "DOCUMENSO_WEBHOOK_SECRET": WEBHOOK_SECRET}
    return DocumensoProvider(env={**base, **env})


def create(p, key=KEY):
    return p.create_request(document=PDF, document_sha256=SHA, signer_name="Cliente di Prova",
                            signer_contact="cliente.prova@esempio.invalid", expires_at=None, idempotency_key=key,
                            signers=SIGNERS, layout=LAYOUT, title="Foglio visita n. 1 (versione 1)")


# ---- configurazione --------------------------------------------------------------------------------------------------

def test_senza_token_il_fornitore_non_e_configurato():
    with pytest.raises(ProviderNotConfigured):
        DocumensoProvider(env={})


@pytest.mark.parametrize("url", ["http://app.documenso.example/api/v2", "ftp://x/api", "https://u:p@host/api/v2", "nonunurl", ""])
def test_l_url_deve_essere_https_o_locale(url):
    if url == "":
        assert DocumensoProvider(env={"DOCUMENSO_API_TOKEN": TOKEN}).api_url == documenso.DEFAULT_API_URL
        return
    with pytest.raises(ProviderNotConfigured):
        DocumensoProvider(env={"DOCUMENSO_API_TOKEN": TOKEN, "DOCUMENSO_API_URL": url})


def test_url_configurabile_cloud_o_auto_ospitato():
    cloud = DocumensoProvider(env={"DOCUMENSO_API_TOKEN": TOKEN})
    own = DocumensoProvider(env={"DOCUMENSO_API_TOKEN": TOKEN, "DOCUMENSO_API_URL": "https://firma.agenzia.example/api/v2/"})
    assert cloud.api_url == "https://app.documenso.com/api/v2" and own.api_url == "https://firma.agenzia.example/api/v2"


def test_ambiente_di_default_e_prova_e_solo_due_valori():
    assert DocumensoProvider(env={"DOCUMENSO_API_TOKEN": TOKEN}).environment == "sandbox"
    assert DocumensoProvider(env={"DOCUMENSO_API_TOKEN": TOKEN, "DOCUMENSO_ENVIRONMENT": "production"}).environment == "production"
    with pytest.raises(ProviderNotConfigured):
        DocumensoProvider(env={"DOCUMENSO_API_TOKEN": TOKEN, "DOCUMENSO_ENVIRONMENT": "live"})


def test_repr_e_errori_non_mostrano_il_token(fake):
    p = provider(fake)
    assert TOKEN not in repr(p) and WEBHOOK_SECRET not in repr(p)
    fake.token = "un-altro-token"                                   # il fornitore risponde 401
    with pytest.raises(ProviderError) as exc:
        p.fetch_status("envelope_x")
    assert TOKEN not in str(exc.value) and "401" in str(exc.value)


# ---- creazione: nessun invio, due firmatari, campi, idempotenza ------------------------------------------------------

def test_creazione_senza_invii_con_due_firmatari_e_campi(fake):
    p = provider(fake)
    out = create(p)
    assert out.reference.startswith("envelope_")
    created = fake.only("POST", "/envelope/create")[0]["payload"]
    dist = fake.only("POST", "/envelope/distribute")[0]["payload"]
    # NESSUN INVIO: distribuzione NONE e tutte le email del fornitore spente, sia alla creazione sia alla distribuzione
    for meta in (created["meta"], dist["meta"]):
        assert meta["distributionMethod"] == "NONE"
        assert meta["emailSettings"] and all(v is False for v in meta["emailSettings"].values())
    assert created["externalId"] == KEY and created["type"] == "DOCUMENT" and created["meta"]["language"] == "it"
    assert created["meta"]["timezone"] == "Europe/Rome" and created["meta"]["signingOrder"] == "PARALLEL"
    # due firmatari distinti, ciascuno col suo campo firma
    emails = [r["email"] for r in created["recipients"]]
    assert emails == ["cliente.prova@esempio.invalid", "agente.prova@esempio.invalid"]
    assert all(len(r["fields"]) == 1 and r["fields"][0]["type"] == "SIGNATURE" and r["role"] == "SIGNER"
               for r in created["recipients"])
    f0, f1 = (r["fields"][0] for r in created["recipients"])
    assert f0["page"] == f1["page"] == 1 and f0["positionX"] < f1["positionX"] and f0["positionY"] == f1["positionY"]
    assert 0 < f0["positionX"] < 100 and 0 < f0["positionY"] < 100 and f0["width"] > 0 and f0["height"] > 0
    # collegamenti personali distinti, uno per ruolo, e identificativi remoti
    assert [s.role for s in out.signers] == ["visitor", "agent"]
    assert out.signers[0].signing_url != out.signers[1].signing_url and all(s.signing_url and s.recipient_id for s in out.signers)
    # il PDF caricato e' quello del foglio, byte per byte
    assert fake.envelopes[out.reference]["pdf"] == PDF
    # autenticazione: il token, senza prefisso, in Authorization; mai nell'URL
    assert all(r["auth"] == TOKEN for r in fake.requests)


def test_la_posizione_dei_campi_e_in_percentuale_con_origine_in_alto():
    g = field_geometry({"page": 2, "x": 100.0, "y": 200.0, "width": 200.0, "height": 50.0, "page_width": 600.0, "page_height": 800.0})
    assert g == {"page": 2, "positionX": 16.6667, "positionY": 68.75, "width": 33.3333, "height": 6.25}


def test_stessa_chiave_stessa_busta_nessun_orfano(fake):
    p = provider(fake)
    first = create(p)
    again = create(p)                                               # il nostro INSERT e' fallito: riprovo con la stessa chiave
    assert again.reference == first.reference and len(fake.envelopes) == 1
    assert len(fake.only("POST", "/envelope/create")) == 1
    assert [s.signing_url for s in again.signers] == [s.signing_url for s in first.signers] or all(s.signing_url for s in again.signers)
    other = create(p, key="7a2e4d6f-1b9f-4a54-8b4b-1c9a0d2e3f40")
    assert other.reference != first.reference and len(fake.envelopes) == 2


def test_se_la_distribuzione_fallisce_la_bozza_non_resta_orfana(fake):
    p = provider(fake)
    fake.fail["POST /envelope/distribute"] = 500
    with pytest.raises(ProviderError):
        create(p)
    assert [e["deletedAt"] is not None for e in fake.envelopes.values()] == [True]
    assert create(p).reference.startswith("envelope_")             # riprova con la stessa chiave: nessun doppione attivo


def test_firmatari_con_lo_stesso_indirizzo_o_nessuno_sono_rifiutati(fake):
    p = provider(fake)
    same = (SIGNERS[0], SignerSpec("agent", "Agente", SIGNERS[0].email.upper(), 1))
    with pytest.raises(ProviderError):
        p.create_request(document=PDF, document_sha256=SHA, signer_name="x", signer_contact="y", expires_at=None,
                         idempotency_key=KEY, signers=same, layout=LAYOUT)
    with pytest.raises(ProviderError):
        p.create_request(document=PDF, document_sha256=SHA, signer_name="x", signer_contact="y", expires_at=None,
                         idempotency_key=KEY, signers=(), layout=LAYOUT)
    assert not fake.envelopes


def test_errore_del_fornitore_non_espone_il_corpo_e_rete_assente_e_un_errore_pulito(fake):
    p = provider(fake)
    fake.fail["POST /envelope/create"] = 500
    with pytest.raises(ProviderError) as exc:
        create(p)
    assert exc.value.code == "PROVIDER_REJECTED" and "errore finto" not in str(exc.value)
    fake.close()
    with pytest.raises(ProviderError) as exc2:
        provider(fake).fetch_status("envelope_x")
    assert exc2.value.code == "PROVIDER_UNREACHABLE"


# ---- stato, firme, documento, prove -----------------------------------------------------------------------------------

def test_stati_e_firmatari(fake):
    p = provider(fake)
    ref = create(p).reference
    s = p.fetch_status(ref)
    assert s.status == "pending" and [x.status for x in s.signers] == ["pending", "pending"]
    fake.sign(ref, "cliente.prova@esempio.invalid")
    s = p.fetch_status(ref)
    assert s.status == "pending" and sorted(x.status for x in s.signers) == ["pending", "signed"]
    fake.sign(ref, "agente.prova@esempio.invalid")
    s = p.fetch_status(ref)
    assert s.status == "signed" and s.signature_level == "ses" and s.signed_at is not None   # SEMPLICE, mai avanzata
    ref2 = create(p, key="7a2e4d6f-1b9f-4a54-8b4b-1c9a0d2e3f40").reference
    fake.reject(ref2, "cliente.prova@esempio.invalid")
    assert p.fetch_status(ref2).status == "declined"
    p.cancel_request(ref)
    assert p.fetch_status(ref).status == "cancelled"


def test_documento_firmato_verificato_sull_originale_e_audit_trail(fake):
    p = provider(fake)
    ref = create(p).reference
    fake.sign(ref, "cliente.prova@esempio.invalid")
    fake.sign(ref, "agente.prova@esempio.invalid")
    doc = p.fetch_document(ref, document_sha256=SHA, signer_name="x")
    assert doc.content.startswith(b"%PDF") and doc.content != PDF and doc.media_type == "application/pdf"
    assert any("version" in r["query"] and r["query"]["version"] == ["original"] for r in fake.only("GET", "/envelope/item/"))
    trail = p.fetch_audit_trail(ref)
    body = json.loads(trail.content)
    assert trail.media_type == "application/json" and body["envelope_id"] == ref and len(body["events"]) >= 3
    fake.tamper_original = True                                     # il fornitore ha firmato un altro documento
    with pytest.raises(ProviderError) as exc:
        p.fetch_document(ref, document_sha256=SHA, signer_name="x")
    assert exc.value.code == "ORIGINAL_MISMATCH"


def test_download_che_risponde_con_un_url_non_https_e_rifiutato(fake):
    p = provider(fake)
    ref = create(p).reference
    fake.download_as_json = True
    with pytest.raises(ProviderError) as exc:
        p.fetch_document(ref, document_sha256=SHA, signer_name="x")      # l'URL del finto e' http://: rifiutato senza rete
    assert exc.value.code == "PROVIDER_BAD_RESPONSE"


def test_annullare_una_busta_gia_sparita_non_e_un_errore(fake):
    p = provider(fake)
    p.cancel_request("envelope_inesistente")                           # 404 -> gia' sparita, l'effetto c'e'


# ---- webhook: segreto condiviso, non HMAC ----------------------------------------------------------------------------

def event(fake, ref, name="DOCUMENT_COMPLETED", **kw):
    return webhook_body(name, fake.envelopes[ref], **kw)


def test_webhook_segreto_giusto(fake):
    p = provider(fake)
    ref = create(p).reference
    body = event(fake, ref)
    ev = p.verify_event({"X-Documenso-Secret": WEBHOOK_SECRET}, body)
    assert ev.event_type == "DOCUMENT_COMPLETED" and ev.external_id == KEY and ev.reference is None
    assert ev.event_id == hashlib.sha256(body).hexdigest()
    same = p.verify_event({"x-documenso-secret": WEBHOOK_SECRET}, body)           # intestazione case-insensitive
    assert same.event_id == ev.event_id                                            # stesso corpo = stesso evento
    assert p.verify_event({"X-Documenso-Secret": WEBHOOK_SECRET}, event(fake, ref, "DOCUMENT_SIGNED")).event_id != ev.event_id


@pytest.mark.parametrize("headers", [{}, {"X-Documenso-Secret": ""}, {"X-Documenso-Secret": "sbagliato"},
                                      {"X-Documenso-Secret": WEBHOOK_SECRET + "x"}, {"X-Documenso-Secret": WEBHOOK_SECRET[:-1]},
                                      {"X-Other": WEBHOOK_SECRET}])
def test_webhook_segreto_assente_o_sbagliato(fake, headers):
    p = provider(fake)
    with pytest.raises(InvalidEventSignature):
        p.verify_event(headers, event(fake, create(p).reference))


def test_senza_segreto_configurato_ogni_evento_e_rifiutato(fake):
    p = provider(fake, DOCUMENSO_WEBHOOK_SECRET="")
    with pytest.raises(InvalidEventSignature):
        p.verify_event({"X-Documenso-Secret": ""}, b"{}")
    short = provider(fake, DOCUMENSO_WEBHOOK_SECRET="corto")
    with pytest.raises(InvalidEventSignature):
        short.verify_event({"X-Documenso-Secret": "corto"}, b"{}")


def test_il_confronto_del_segreto_e_a_tempo_costante(fake, monkeypatch):
    calls = []
    real = documenso.hmac.compare_digest
    monkeypatch.setattr(documenso.hmac, "compare_digest", lambda a, b: calls.append((type(a), type(b))) or real(a, b))
    p = provider(fake)
    p.verify_event({"X-Documenso-Secret": WEBHOOK_SECRET}, event(fake, create(p).reference))
    assert calls == [(bytes, bytes)]


@pytest.mark.parametrize("raw", [b"non json", b"[]", b'{"payload": {}}', b'{"event": 5}', b"\xff\xfe"])
def test_webhook_corpo_malformato(fake, raw):
    with pytest.raises(InvalidEventSignature):
        provider(fake).verify_event({"X-Documenso-Secret": WEBHOOK_SECRET}, raw)


def test_il_riferimento_si_legge_solo_se_ha_la_forma_di_una_busta(fake):
    p = provider(fake)
    ok = json.dumps({"event": "DOCUMENT_SIGNED", "payload": {"envelopeId": "envelope_abc123", "externalId": KEY}}).encode()
    bad = json.dumps({"event": "DOCUMENT_SIGNED", "payload": {"envelopeId": "../../etc/passwd", "id": 5}}).encode()
    assert p.verify_event({"X-Documenso-Secret": WEBHOOK_SECRET}, ok).reference == "envelope_abc123"
    assert p.verify_event({"X-Documenso-Secret": WEBHOOK_SECRET}, bad).reference is None


@pytest.mark.parametrize("url,ok", [("https://files.documenso.example/x.pdf", True), ("http://files.example/x", False),
                                    ("https://127.0.0.1/x", False), ("https://10.0.0.5/x", False), ("https://169.254.169.254/x", False),
                                    ("https://localhost/x", False), ("https://u:p@host.example/x", False), ("https://[::1]/x", False)])
def test_l_url_di_download_non_porta_mai_verso_la_rete_locale(url, ok):
    assert documenso._public_https(url) is ok
