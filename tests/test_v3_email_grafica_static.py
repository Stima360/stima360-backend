"""V3 - LA GRAFICA DELLE 16 EMAIL, senza database.

  01  per ognuna delle 16: logo ufficiale (rotta pubblica, alt), colore della
      tappa a rotazione (pulsante, linea, riquadri), titolo = oggetto, OGNI
      riga del testo approvato presente nell'html (escape), link di
      disiscrizione e sito nel piede, CTA secondo la regola della tappa.
  02  l'adapter manda html con il marchio E il testo semplice identico al
      corpo in coda; un messaggio di testo che NON e' del V3 resta com'era.
  03  il logo e' il file ufficiale (impronta) e la rotta pubblica lo serve.
  04  i confini: niente rete, niente ambiente, niente dominio nel modulo.
"""
from __future__ import annotations

import hashlib
import html as _html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://crm-test.example.invalid"
LOGO_SHA256 = "b867fb06d3289cfabb7d3c9d2a8e857a2d2f611b68f6a277db75204c1e01bb58"


def _messaggio(n: int, nome: str = "Anna", agenzia: str = "Agenzia Prova") -> dict:
    from communication import templates
    t = templates.get(f"followup_v3_m{n}", 3)
    contesto = {"contact_first_name": nome, "agency_name": agenzia,
                "unsubscribe_url": f"{BASE}/api/public/communication/unsubscribe?t=tok{n}",
                "guide_url": f"{BASE}/api/public/communication/guida-vendere-casa-2026.pdf"}
    return {"template_key": f"followup_v3_m{n}", "subject_snapshot": t.subject(contesto),
            "rendered_body": t.body(contesto), "destination_snapshot": "anna@example.it",
            "metadata": {"body_format": "text", "reply_to": "info@stima360.it",
                         "unsubscribe_url": contesto["unsubscribe_url"]}}


def test_01_le_sedici_email_hanno_marchio_colore_testo_e_cta():
    from communication.providers import email_brand_v3 as b
    attesi = ["verde", "blu", "arancione", "viola", "bordeaux", "giallo"] * 3
    for n in range(1, 17):
        m = _messaggio(n)
        assert b.si_applica(m)
        h = b.html(m)
        nome, col, tinta, testo_pulsante = b.colore(n)
        assert nome == attesi[n - 1], (n, nome)
        assert f'data-colore="{nome}"' in h and h.count(col) >= 3, (n, nome)
        if nome == "giallo":
            assert "color:#1A1A1A;text-decoration:none" in h, "sul giallo il testo del pulsante e' scuro"
        assert f'src="{BASE}/api/public/communication/logo-stima360.jpg"' in h and f'alt="{b.LOGO_ALT}"' in h
        assert f"<h1" in h and _html.escape(m["subject_snapshot"]) in h
        corpo = m["rendered_body"].split("\nSe non vuoi piu'")[0]
        for riga in (r.strip() for r in corpo.splitlines()):
            if riga and riga != "su Stima360":
                riga = "\u2022 " + riga[2:] if riga.startswith("* ") else riga   # pallino al posto dell'asterisco
                assert _html.escape(riga) in h or re.sub(r"https?://\S+", "", _html.escape(riga)).strip() in h, (n, riga)
        assert f'href="{BASE}/api/public/communication/unsubscribe?t=tok{n}"' in h
        assert 'href="https://www.stima360.it"' in h and "Agenzia Prova su Stima360" in h
        etichetta, dove = b.CTA[n]
        assert _html.escape(etichetta) in h
        if dove == "guida":
            assert f'href="{BASE}/api/public/communication/guida-vendere-casa-2026.pdf"' in h
        elif dove == "risposta":
            assert 'href="mailto:info@stima360.it"' in h
        assert "<table" in h and 'max-width:600px' in h and "@media only screen" in h


def test_02_l_adapter_manda_html_con_marchio_e_testo_identico(monkeypatch):
    from communication.providers import email_smtp
    chiamate = []
    monkeypatch.setattr(email_smtp, "invia_mail",
                        lambda dest, ogg, corpo_html, allegato=None, **kw: chiamate.append((dest, ogg, corpo_html, kw)) or True)
    m = _messaggio(3)
    esito = email_smtp.send(m)
    assert esito.outcome == "accepted"
    dest, ogg, corpo_html, kw = chiamate[0]
    assert "logo-stima360.jpg" in corpo_html and 'data-colore="arancione"' in corpo_html
    assert kw["corpo_testo"] == m["rendered_body"], "il testo semplice e' il corpo in coda, identico"
    assert kw["reply_to"] == "info@stima360.it" and "List-Unsubscribe" in kw["intestazioni"]
    # un testo di servizio (non V3) passa come prima, senza marchio
    servizio = {"template_key": "consent_confirmation", "subject_snapshot": "Conferma",
                "rendered_body": "Ciao,\n\nconferma qui: https://x.test/c/1\n", "destination_snapshot": "a@b.it",
                "metadata": {"body_format": "text"}}
    email_smtp.send(servizio)
    assert "logo-stima360" not in chiamate[1][2] and "<p>Ciao," in chiamate[1][2]


def test_03_il_logo_e_quello_ufficiale_e_la_rotta_lo_serve():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from communication import marchio, public_router
    assert marchio.LOGO_FILE.is_file()
    assert hashlib.sha256(marchio.LOGO_FILE.read_bytes()).hexdigest() == LOGO_SHA256
    app = FastAPI()
    app.include_router(public_router.router)
    r = TestClient(app).get(marchio.LOGO_PERCORSO)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert hashlib.sha256(r.content).hexdigest() == LOGO_SHA256


def test_04_i_confini_del_modulo():
    codice = (ROOT / "communication" / "providers" / "email_brand_v3.py").read_text(encoding="utf-8")
    for vietato in ("requests", "smtplib", "httpx", "urllib", "socket", "os.getenv", "cur.execute",
                    "SELECT", "agency_id", "contact_id", "invia_mail", "SMTP_"):
        assert vietato not in codice, vietato
