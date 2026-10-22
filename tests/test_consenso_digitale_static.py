"""C3 - raccolta digitale del consenso: controlli statici (senza database).

Token imprevedibili e solo in impronta, intestazioni di sicurezza della pagina
pubblica, testo dell'informativa sempre escapato, nessuna autorizzazione via
GET, casella email del sito mai preselezionata, `segno` fissato, passo del
funnel nell'ordine giusto. Il comportamento sul database e' in
`test_consenso_digitale_postgres.py`.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _testo(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_i_token_sono_casuali_lunghi_e_conservati_solo_come_impronta():
    from consent import capture
    token = {capture.generate_token() for _ in range(200)}
    assert len(token) == 200
    assert all(len(t) >= 43 and re.fullmatch(r"[A-Za-z0-9_-]+", t) for t in token)
    assert all(capture.token_well_formed(t) for t in token)
    for cattivo in (None, "", "corto", "a" * 43 + "!", "../" + "a" * 43, 123):
        assert not capture.token_well_formed(cattivo)
    t = next(iter(token))
    assert re.fullmatch(r"[0-9a-f]{64}", capture.hash_token(t))
    sql = _testo("migrations/110_consent_capture.sql")
    assert "token_hash              CHAR(64)" in sql or re.search(r"token_hash\s+CHAR\(64\)", sql)
    assert not re.search(r"^\s+token\s+(TEXT|VARCHAR)", sql, re.M), "nessun token in chiaro nel database"


def test_le_scadenze_approvate_30_giorni_link_7_giorni_conferma():
    from datetime import timedelta
    from consent import capture
    assert capture.LINK_TTL == timedelta(days=30)
    assert capture.CONFIRM_TTL == timedelta(days=7)


def test_l_ip_si_conserva_solo_come_hmac_e_mai_senza_pepe(monkeypatch):
    from consent import capture
    monkeypatch.delenv(capture.IP_PEPPER_ENV, raising=False)
    assert capture.hash_ip("203.0.113.9") is None
    monkeypatch.setenv(capture.IP_PEPPER_ENV, "x" * 32)
    impronta = capture.hash_ip("203.0.113.9")
    assert re.fullmatch(r"[0-9a-f]{64}", impronta) and "203" not in impronta


def test_la_pagina_pubblica_ha_le_intestazioni_di_sicurezza():
    from communication import consent_page
    h = consent_page.SECURITY_HEADERS
    assert h["Cache-Control"] == "no-store"
    assert h["Referrer-Policy"] == "no-referrer"
    assert h["X-Frame-Options"] == "DENY"
    assert h["X-Content-Type-Options"] == "nosniff"
    assert "noindex" in h["X-Robots-Tag"]
    csp = h["Content-Security-Policy"]
    for parte in ("default-src 'none'", "frame-ancestors 'none'", "form-action 'self'"):
        assert parte in csp
    assert "script-src" not in csp, "la pagina non esegue JavaScript"


def test_l_informativa_e_sempre_escapata():
    from communication import consent_page
    html = consent_page.notice_page({"version": "<v>", "channels": ["email"],
                                     "content": "<script>alert(1)</script>"})
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<v>" not in html


def test_nessuna_autorizzazione_via_get():
    """Le funzioni GET della pagina pubblica non chiamano mai submit/confirm."""
    albero = ast.parse(_testo("communication/public_router.py"))
    for nodo in albero.body:
        if not isinstance(nodo, ast.FunctionDef):
            continue
        metodi = {d.func.attr for d in nodo.decorator_list
                  if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)}
        percorsi = [d.args[0].value for d in nodo.decorator_list
                    if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant)]
        if "get" not in metodi or not any(str(p).startswith("/consent") for p in percorsi):
            continue
        chiamate = {n.func.attr for n in ast.walk(nodo)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert not chiamate & {"submit", "confirm_email", "record_grant", "record_revocation"}, nodo.name


def test_le_rotte_operatore_del_link_sono_solo_post():
    sorgente = _testo("communication/router.py")
    assert '@router.post("/contacts/{contact_id}/consent/link", status_code=201)' in sorgente
    assert '@router.post("/contacts/{contact_id}/consent/link/disable")' in sorgente
    assert '@router.get("/contacts/{contact_id}/consent/link' not in sorgente


def test_segno_e_fissato_nei_requisiti():
    righe = [r.strip() for r in _testo("requirements.txt").splitlines()]
    assert any(re.fullmatch(r"segno==\d+\.\d+\.\d+", r) for r in righe)


def test_il_passo_del_funnel_sta_tra_immobile_ed_evento():
    sorgente = _testo("main.py")
    i_prop = sorgente.index('receipt.step("property"')
    i_cons = sorgente.index('receipt.step("consent_email"')
    i_evt = sorgente.index('receipt.step("event_requested"')
    assert i_prop < i_cons < i_evt


def test_la_casella_email_del_sito_e_facoltativa_e_mai_preselezionata():
    html = _testo("site-preview/public/dati_personali.html")
    casella = re.search(r'<input[^>]*id="consensoEmail"[^>]*>', html).group(0)
    assert "checked" not in casella and "required" not in casella
    assert 'id="consensoEmailBox" hidden' in html
    assert "consenso_marketing" not in html, "la spunta combinata email+WhatsApp non si invia piu'"
    assert not re.search(r'id="marketing"', html)


def test_la_scheda_contatto_non_usa_innerhtml_per_link_e_qr():
    js = _testo("static/os_shell/assets/components/communications.js")
    inizio = js.index("function renderRaccolta")
    corpo = js[inizio:js.index("\nfunction ", inizio + 10)]
    assert "Genera link consenso" in corpo
    assert "data-consent-link-generate" in corpo
    # Il gestore che scrive URL e QR del server usa solo DOM e textContent.
    gestore = js[js.index("// C3: genera / copia / disattiva"):]
    gestore = gestore[:gestore.index("\n  });\n") ]
    assert "innerHTML" not in gestore and "insertAdjacentHTML" not in gestore
    assert ".textContent = scadenza" in gestore
    assert "qr.startsWith('data:image/svg+xml')" in gestore


# ---------------------------------------------------------------------------
# C3.1 - la casella del sito pubblicato nell'import
# ---------------------------------------------------------------------------

def test_c31_l_interruttore_del_sito_e_spento_salvo_on_esplicito(monkeypatch):
    from communication import consent_page
    assert consent_page.SITE_DOUBLE_OPT_IN_ENV == "SITE_CONSENT_DOUBLE_OPT_IN"
    monkeypatch.delenv(consent_page.SITE_DOUBLE_OPT_IN_ENV, raising=False)
    assert not consent_page.site_double_opt_in_enabled()
    for valore in ("", "off", "0", "false", "yes", "ON?", "1"):
        monkeypatch.setenv(consent_page.SITE_DOUBLE_OPT_IN_ENV, valore)
        assert not consent_page.site_double_opt_in_enabled(), valore
    monkeypatch.setenv(consent_page.SITE_DOUBLE_OPT_IN_ENV, " On ")
    assert consent_page.site_double_opt_in_enabled()
    monkeypatch.delenv(consent_page.SITE_DOUBLE_OPT_IN_ENV, raising=False)
    esito = consent_page.request_from_site_checkbox(None, stima_id=1, contact_id=1, consenso_marketing=True,
                                                    consenso_marketing_at=None)
    assert esito == {"status": "disabled"}


def test_c31_il_passo_consenso_segue_il_bridge_e_non_legge_id_dal_client():
    from site_import import service
    assert service.STIMA_STEPS == ("bridge", "consent", "property", "event", "followup", "pdf")
    corpo = _testo("communication/consent_page.py")
    funzione = corpo[corpo.index("def request_from_site_checkbox"):]
    funzione = funzione[:funzione.index("\n\n\n")]
    assert "notice_id=None" in funzione and "decided_at=consenso_marketing_at" in funzione
    assert "informativa_marketing_id" not in funzione and "whatsapp" not in funzione.split('"""')[2].lower()


def test_c31_l_informativa_si_attribuisce_solo_se_gia_registrata():
    sorgente = _testo("consent/capture.py")
    funzione = sorgente[sorgente.index("def notice_at"):sorgente.index("def _consuma")]
    assert "created_at <= %s" in funzione and "valid_from <= %s" in funzione
