"""FIRMA OTP - le parti senza database: segreti e derivazioni, catena delle prove, dichiarazione, email, PDF
(attestazione e rapporto), e le guardie statiche di pagina pubblica, riquadro CRM e assenza di Documenso."""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from esign import attest_pdf, consent, emails, evidence, otp_security, providers, report_pdf

ROOT = Path(__file__).resolve().parents[1]
PEPPER = "pepe-di-prova-molto-lungo-0123456789-abcdef"
KEY = "chiave-prove-di-prova-molto-lunga-0123456789"


@pytest.fixture
def configurato(monkeypatch):
    monkeypatch.setenv(otp_security.PEPPER_ENV, PEPPER)
    monkeypatch.setenv(otp_security.EVIDENCE_KEY_ENV, KEY)
    for name in otp_security.DEFAULTS:
        monkeypatch.delenv(name, raising=False)


# ---- Documenso non esiste piu' ----------------------------------------------------------------------------------------

def test_documenso_e_sparito_dal_codice_e_dalle_dipendenze():
    assert not (ROOT / "esign" / "documenso.py").exists()
    for path in (ROOT / "esign").glob("*.py"):
        assert "documenso" not in path.read_text(encoding="utf-8").lower(), path.name
    for path in (ROOT / "static" / "os_shell" / "assets" / "components").glob("*.js"):
        assert "documenso" not in path.read_text(encoding="utf-8").lower(), path.name
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "documenso" not in req and "pypdf" in req
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "esign_internal_router" not in main and "webhooks" not in main.split("esign_public_router")[0][-400:]
    assert providers.OTP_PROVIDER == "stima360_otp" and providers.MODES == ("simulated", "stima360_otp")


# ---- segreti ----------------------------------------------------------------------------------------------------------

def test_token_di_accesso_e_lungo_casuale_e_solo_la_sua_impronta_va_nel_database():
    a, b = otp_security.new_access_token(), otp_security.new_access_token()
    assert a != b and len(a) >= 40 and otp_security.looks_like_access_token(a)
    assert otp_security.hash_access_token(a) == hashlib.sha256(a.encode()).hexdigest()
    assert not otp_security.looks_like_access_token("corto") and not otp_security.looks_like_access_token("x" * 20 + "/..")


def test_otp_a_sei_cifre_uniforme_e_derivazione_con_pepe_e_sale(configurato):
    codici = {otp_security.new_otp() for _ in range(200)}
    assert all(re.fullmatch(r"\d{6}", c) for c in codici) and len(codici) > 150
    salt = otp_security.new_salt()
    stored = otp_security.hash_otp("123456", challenge_salt=salt)
    assert stored.startswith("pbkdf2_sha256$") and "123456" not in stored
    assert otp_security.verify_otp("123456", challenge_salt=salt, stored=stored)
    assert not otp_security.verify_otp("123457", challenge_salt=salt, stored=stored)
    assert not otp_security.verify_otp("123456", challenge_salt=otp_security.new_salt(), stored=stored)   # sale diverso
    assert not otp_security.verify_otp("12345", challenge_salt=salt, stored=stored)                     # forma sbagliata
    assert otp_security.hash_otp("123456", challenge_salt=salt) != stored                               # sale interno casuale


def test_senza_pepe_o_chiave_il_procedimento_non_parte(monkeypatch, configurato):
    monkeypatch.setenv(otp_security.PEPPER_ENV, "corto")
    with pytest.raises(otp_security.NotConfigured):
        otp_security.require_configuration()
    monkeypatch.setenv(otp_security.PEPPER_ENV, PEPPER)
    monkeypatch.delenv(otp_security.EVIDENCE_KEY_ENV)
    with pytest.raises(otp_security.NotConfigured):
        otp_security.require_configuration()
    monkeypatch.setenv(otp_security.EVIDENCE_KEY_ENV, KEY)
    monkeypatch.setenv("ESIGN_OTP_TTL_MINUTES", "zero")
    with pytest.raises(otp_security.NotConfigured):
        otp_security.require_configuration()


def test_con_un_pepe_diverso_lo_stesso_codice_non_verifica(monkeypatch, configurato):
    salt = otp_security.new_salt()
    stored = otp_security.hash_otp("654321", challenge_salt=salt)
    monkeypatch.setenv(otp_security.PEPPER_ENV, PEPPER + "-altro")
    assert not otp_security.verify_otp("654321", challenge_salt=salt, stored=stored)


# SENTINELLA AGGIORNATA (correzione urgente FIRMA OTP): la whitelist `ESIGN_ALLOWED_RECIPIENTS` e' stata rimossa per
# decisione del titolare. L'agente invia il foglio e il codice a qualsiasi email valida del cliente; restano la
# validazione dell'indirizzo, i limiti di frequenza, i tentativi, l'autenticazione e l'isolamento per agenzia.
def test_nessuna_whitelist_dei_destinatari_della_firma():
    assert not hasattr(otp_security, "allowed_recipients") and not hasattr(otp_security, "recipient_allowed")
    for path in list((ROOT / "esign").glob("*.py")) + [ROOT / "static" / "os_shell" / "assets" / "components" / "visit-sheets.js"]:
        text = path.read_text(encoding="utf-8")
        assert "ESIGN_ALLOWED_RECIPIENTS" not in text and "RECIPIENT_NOT_ALLOWED" not in text, path.name
        assert "recipients_restricted" not in text and "destinatari autorizzati" not in text, path.name
    # la validazione dell'indirizzo resta: un'email valida qualsiasi si', un telefono o un testo no
    assert otp_security.is_valid_email("chiunque.mai.visto@dominio-nuovo.it")
    assert not otp_security.is_valid_email("3331234567") and not otp_security.is_valid_email("") \
        and not otp_security.is_valid_email(" a@b.it")


def test_ip_e_user_agent_minimizzati(configurato):
    h = otp_security.ip_hash("203.0.113.9")
    assert h and len(h) == 64 and "203" not in h and h != hashlib.sha256(b"203.0.113.9").hexdigest()
    assert otp_security.user_agent_short("  Mozilla/5.0  " + "x" * 500).startswith("Mozilla/5.0 x") and len(otp_security.user_agent_short("x" * 500)) == 200
    assert otp_security.ip_hash(None) is None


# ---- catena delle prove -----------------------------------------------------------------------------------------------

def test_la_catena_delle_prove_si_rompe_se_una_voce_cambia(configurato):
    t0 = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)
    rows, prev = [], None
    for seq, kind in enumerate(("request_created", "link_issued", "otp_verified"), start=1):
        text = evidence.canonical(request_id=7, seq=seq, event_type=kind, occurred_at=t0 + timedelta(seconds=seq),
                                  actor_kind="system", actor_ref=None, signer_id=None, details={"n": seq})
        h = evidence.entry_hash(prev, text)
        rows.append({"request_id": 7, "seq": seq, "event_type": kind, "occurred_at": t0 + timedelta(seconds=seq), "actor_kind": "system",
                     "actor_ref": None, "signer_id": None, "details": {"n": seq}, "prev_hash": prev, "entry_hash": h, "seal": evidence.seal(h)})
        prev = h

    class Cur:
        def __init__(self, rows): self.rows = rows
        def execute(self, *a): pass
        def fetchall(self): return self.rows

    assert evidence.verify(Cur(rows), 7)["ok"] is True
    manomessa = [dict(r) for r in rows]
    manomessa[1]["details"] = {"n": 99}
    out = evidence.verify(Cur(manomessa), 7)
    assert out["ok"] is False and any("voce 2" in p for p in out["problems"])
    tolta = [rows[0], rows[2]]
    assert evidence.verify(Cur(tolta), 7)["ok"] is False
    # ricostruire la catena senza la chiave non basta: il sigillo non torna
    rifatta = [dict(r) for r in rows]
    rifatta[2]["details"] = {"n": 3, "extra": True}
    text = evidence.canonical(request_id=7, seq=3, event_type="otp_verified", occurred_at=rifatta[2]["occurred_at"], actor_kind="system",
                              actor_ref=None, signer_id=None, details=rifatta[2]["details"])
    rifatta[2]["entry_hash"] = evidence.entry_hash(rifatta[1]["entry_hash"], text)
    rifatta[2]["seal"] = hmac.new(b"chiave-sbagliata", rifatta[2]["entry_hash"].encode(), hashlib.sha256).hexdigest()
    out = evidence.verify(Cur(rifatta), 7)
    assert out["ok"] is False and any("sigillo" in p for p in out["problems"])


# ---- dichiarazione ed email -------------------------------------------------------------------------------------------

def test_la_dichiarazione_e_legata_alla_versione_del_pdf():
    # SENTINELLA AGGIORNATA (FIRMA SEMPLIFICATA): la dichiarazione proposta oggi e' la v2, senza impronta visibile;
    # il documento e' identificato da titolo, immobile e data e il legame crittografico e' nelle prove
    # (`document_sha256` accanto a `consent_sha256`, vedi test_firma_otp_postgres). La v1 resta identica.
    assert consent.VERSION == "dichiarazione_firma_otp_v2"
    a = consent.text(title="Foglio visita n. 1 (versione 1)", sha256="a" * 64, address="Via Roma 1, Teramo",
                     visit_text="02/11/2026")
    b = consent.text(title="Foglio visita n. 1 (versione 2)", sha256="b" * 64, address="Via Roma 1, Teramo",
                     visit_text="02/11/2026")
    assert "a" * 64 not in a and "SHA" not in a and consent.digest(a) != consent.digest(b)
    assert "Via Roma 1, Teramo" in a and "02/11/2026" in a and "questa versione del documento" in a
    assert "firma elettronica semplice" in a and "avanzata" not in a and "qualificata" not in a
    v1 = consent.text(title="Foglio visita n. 1 (versione 1)", sha256="a" * 64, version=consent.VERSION_V1)
    assert "a" * 64 in v1 and v1 == consent.TEMPLATE_V1.format(title="Foglio visita n. 1 (versione 1)", sha256="a" * 64)


def test_le_email_hanno_marchio_oggetti_richiesti_e_nessun_dato_in_piu(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://test.invalid/")
    url = emails.sign_url("tok_en-123")
    assert url == "https://test.invalid/firma/tok_en-123"
    oggetto, corpo = emails.link_email(signer_name="Mario <b>", agency_name="Agenzia & Co", title="Foglio visita n. 3 (versione 1)",
                                       url=url, expires_at=datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc))
    assert oggetto == "STIMA360 — Foglio visita da firmare"
    assert "Visualizza e firma il documento" in corpo and "codice OTP" in corpo and "logo-stima360.jpg" in corpo
    assert "Mario &lt;b&gt;" in corpo and "Agenzia &amp; Co" in corpo and url in corpo and "10/11/2026" in corpo
    oggetto2, corpo2 = emails.otp_email(code="123456", minutes=10, title="Foglio visita n. 3 (versione 1)")
    assert oggetto2 == "STIMA360 — Il tuo codice di conferma" and "123456" in corpo2 and "10 minuti" in corpo2
    assert "non te lo chiederanno mai" in corpo2


# ---- PDF ---------------------------------------------------------------------------------------------------------------

def _foglio() -> bytes:
    from tests.test_blocco_6a_visit_sheets import SNAP
    from visit_sheets.pdf_render import render_visit_sheet_pdf
    return render_visit_sheet_pdf(SNAP, {"header_name": "Agenzia Prova"}, generated_at_text="04/11/2026")


def _testo(pdf: bytes) -> str:
    if shutil.which("pdftotext"):
        return " ".join(subprocess.run(["pdftotext", "-layout", "-", "-"], input=pdf, capture_output=True, check=True).stdout.decode().split())
    from esign.pdf_text import extract_text
    return extract_text(pdf)


def test_il_documento_sottoscritto_conserva_le_pagine_originali_e_aggiunge_l_attestazione():
    from pypdf import PdfReader
    original = _foglio()
    when = datetime(2026, 11, 4, 9, 30, tzinfo=timezone.utc)
    info = {"procedure": "S360-PROVA", "title": "Foglio visita n. 1 (versione 1)", "original_sha256": hashlib.sha256(original).hexdigest(),
            "signers": [{"role": "visitor", "role_label": "Cliente", "name": "Cliente Prova", "status": "signed", "signed_at": when,
                         "procedure": "S360-PROVA", "method": "email+otp"},
                        {"role": "agent", "role_label": "Agente", "name": "Agente Prova", "status": "signed", "signed_at": when,
                         "procedure": "S360-PROVA", "method": "sessione"}]}
    out = attest_pdf.build(original, identity={"header_name": "Agenzia Prova"}, info=info)
    assert out.startswith(b"%PDF") and len(PdfReader(io.BytesIO(out)).pages) == len(PdfReader(io.BytesIO(original)).pages) + 1
    testo = _testo(out)
    for atteso in ("Sottoscrizione elettronica mediante conferma OTP", "S360-PROVA", "Cliente Prova", "Agente Prova",
                   "Firma elettronica semplice", "non è una firma digitale", info["original_sha256"][:20]):
        assert "".join(atteso.split()) in "".join(testo.split()), atteso
    assert "".join(_testo(original).split()) in "".join(testo.split()) or "Firma del visitatore" in testo


def test_il_rapporto_delle_prove_e_leggibile_e_senza_segreti():
    when = datetime(2026, 11, 4, 9, 30, tzinfo=timezone.utc)
    events = [{"seq": 1, "event_type": "request_created", "occurred_at": when, "actor_kind": "operator", "details": {"procedure": "S360-X"},
               "entry_hash": "a" * 64},
              {"seq": 2, "event_type": "otp_verified", "occurred_at": when, "actor_kind": "signer", "details": {"attempts": 1}, "entry_hash": "b" * 64}]
    data = {"procedure": "S360-X", "request_id": 5, "generated_at": when, "property": "IMM-1 · Via Prova 1", "visit": "Appuntamento 9",
            "sheet": "Foglio visita n. 9 (versione 1)", "outcome": "Firmato", "original_sha256": "c" * 64, "signed_sha256": "d" * 64,
            "signers": [{"role_label": "Cliente", "name": "Cliente", "email": "c@esempio.invalid", "method": "email+otp", "status_label": "Ha firmato",
                         "signed_at": when}],
            "consent": {"version": consent.VERSION, "text": consent.text(title="t", sha256="c" * 64), "sha256": "e" * 64},
            "events": events, "chain_head": "b" * 64}
    pdf = report_pdf.build(identity={"header_name": "Agenzia Prova"}, data=data)
    testo = "".join(_testo(pdf).split())
    for atteso in ("Rapporto delle prove", "S360-X", "FIRMA ELETTRONICA SEMPLICE", "Codice OTP verificato", "Testa della catena",
                   "non è una marca temporale qualificata", "c@esempio.invalid"):
        assert "".join(atteso.split()) in testo, atteso
    assert "123456" not in testo and "pbkdf2" not in testo


# ---- guardie statiche -------------------------------------------------------------------------------------------------

def test_la_pagina_pubblica_non_usa_html_con_dati_ne_risorse_esterne():
    js = (ROOT / "static" / "esign_sign" / "assets" / "sign-page.js").read_text(encoding="utf-8")
    html = (ROOT / "static" / "esign_sign" / "index.html").read_text(encoding="utf-8")
    for vietato in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "document.write"):
        assert vietato not in js, vietato
    assert "credentials: 'omit'" in js and "cache: 'no-store'" in js
    assert "https://" not in html and "http://" not in html                              # nessuna risorsa esterna
    assert 'name="robots" content="noindex' in html and "/firma/assets/sign-page.js" in html
    # SENTINELLA AGGIORNATA (FIRMA SEMPLIFICATA): niente casella separata; il consenso e' l'azione esplicita
    # «Accetto il documento e richiedo il codice» con la dichiarazione in vista, confermata da «Conferma firma» con il
    # codice; il server lo registra (versione, impronta della dichiarazione e del documento). Nessuna casella
    # preselezionata, nessuna impronta, procedimento o identificativo tecnico in pagina.
    assert "checked = true" not in js and "type = 'checkbox'" not in js
    assert "Accetto il documento e richiedo il codice" in js and "Conferma firma" in js and "Non voglio firmare" in js
    assert js.count("consent_accepted: true") == 1 and "call('POST', '/confirm'" in js
    for tecnico in ("sha256", "state.procedure", "Procedimento", "Impronta"):
        assert tecnico not in js, tecnico
    assert 'src="/os/assets/brand/stima360-logo.png"' in html and "Firma il foglio visita" in html
    for vietato in ("avanzata", "qualificata", "firma digitale"):
        assert vietato not in js.lower() or "non" in js.lower(), vietato


def test_il_riquadro_crm_rispetta_le_regole():
    # SENTINELLA AGGIORNATA (FIRMA SEMPLIFICATA): il riquadro «Firma del foglio visita». Un pulsante principale per
    # stato, le azioni secondarie nel menu «⋯» (solo quelle possibili), lo stato dal server (`r.ui`, presentation.py),
    # aggiornamento automatico invece di «Verifica stato». Il vecchio riquadro resta solo per la simulazione.
    js = (ROOT / "static" / "os_shell" / "assets" / "components" / "visit-sheets.js").read_text(encoding="utf-8")
    nuovo = js[js.index("export async function mountSignaturePanel("):]
    assert "innerHTML" not in js and "Firma del foglio visita" in nuovo and "'Invia per firma'" in nuovo
    for azione in ("data-sign-send", "data-sign-preview", "data-sign-menu", "data-signature-resend", "data-signature-copy",
                   "data-signature-cancel", "data-sign-view", "data-signature-document", "data-signature-audit",
                   "data-signature-verify", "data-signature-events", "data-signature-agent", "data-agent-sign", "data-sign-agent-now"):
        assert azione in nuovo, azione
    assert "r.ui" in nuovo and "`${ESIGN}/send`" in nuovo and "setTimeout(" in nuovo           # stati dal server, da soli
    assert "data-signature-refresh" not in nuovo and "Verifica stato" not in nuovo
    assert "config.consent_version" in nuovo and "dichiarazione_firma_otp_v1" not in nuovo
    assert "signing-links" not in js and "documenso" not in js.lower()


@pytest.mark.skipif(shutil.which("node") is None, reason="node non disponibile")
@pytest.mark.parametrize("path", ["static/esign_sign/assets/sign-page.js", "static/os_shell/assets/components/visit-sheets.js"])
def test_sintassi_javascript(path, tmp_path):
    copia = tmp_path / "m.mjs"
    copia.write_text((ROOT / path).read_text(encoding="utf-8"), encoding="utf-8")
    assert subprocess.run(["node", "--check", str(copia)], capture_output=True, text=True, timeout=60).returncode == 0


def test_migrazione_129_valida_additiva_e_con_down():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    m = next(x for x in runner.discover_migrations() if x.version == "129_signature_otp")
    assert m.down_available and runner.validate_migration(m) == []
    up = (ROOT / "migrations" / "129_signature_otp.sql").read_text(encoding="utf-8")
    assert "DROP TABLE" not in up and "DELETE FROM" not in up and "signature_evidence" in up and "append-only" in up
    assert "'signature_link', 'signature_otp'" in up
