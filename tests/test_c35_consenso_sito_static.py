"""C3.5 - la modifica minima della pagina del sito, letta come testo.

  01  la copia di partenza e' l'originale ricevuto (impronta di
      html-provenance.json) e la privacy ricevuta e' conservata identica.
  02  le sole differenze sono la lettura della prova e il corpo della
      richiesta: nessuna casella nuova, il marketing resta facoltativo, il
      testo delle caselle non cambia, valutazione/PDF/redirect intatti.
  03  la patch allegata e' esattamente la differenza fra le due copie.
  04  backend: la prova si conserva solo se coincide con una versione
      ufficiale accettata conservata sul server, mai inventata.
  05  le versioni ufficiali: file verificati con l'impronta, la privacy
      precedente conservata identica come storica (non accettata), la nuova
      allineata alla casella (marketing solo email e WhatsApp).
  06  la patch della privacy cambia solo la riga dei canali del marketing.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "site-preview" / "baseline" / "dati_personali.html"
C35 = ROOT / "site-preview" / "c35-consenso"


def _righe(p):
    return p.read_text(encoding="utf-8").splitlines()


def test_01_copie_di_partenza():
    prov = json.loads((ROOT / "site-preview" / "html-provenance.json").read_text(encoding="utf-8"))
    pagina = next(p for p in prov["pages"] if p["url"].endswith("/dati_personali.html"))
    assert hashlib.sha256(BASE.read_bytes()).hexdigest() == pagina["sha256"]
    # la privacy RICEVUTA (privacy.zip) e' conservata identica come versione storica
    assert hashlib.sha256((ROOT / "site_official" / "versions" / "privacy-d615e05d.html").read_bytes()).hexdigest() == \
        "d615e05d94bac3816b089c077b6b7249c8d43f5957b48011cff0f06cb8b75d15"


def test_02_solo_la_prova_nessuna_casella_nuova():
    vecchio, nuovo = _righe(BASE), _righe(C35 / "dati_personali.html")
    tolte = [r for r in difflib.ndiff(vecchio, nuovo) if r.startswith("- ")]
    aggiunte = [r[2:] for r in difflib.ndiff(vecchio, nuovo) if r.startswith("+ ")]
    assert tolte == ["-         body: JSON.stringify(datiFinali)"]
    corpo = "\n".join(aggiunte)
    assert "body: JSON.stringify({ ...datiFinali, ...provaMarketing })" in corpo
    for vietato in ("<input", "<label", "required", "innerHTML", "localStorage", "location"):
        assert vietato not in corpo, vietato
    html = (C35 / "dati_personali.html").read_text(encoding="utf-8")
    caselle = re.findall(r"<input[^>]*type=\"checkbox\"[^>]*>", html)
    assert caselle == re.findall(r"<input[^>]*type=\"checkbox\"[^>]*>", BASE.read_text(encoding="utf-8"))
    assert caselle == ['<input type="checkbox" id="privacy" name="privacy" required />',
                       '<input type="checkbox" id="marketing" name="marketing" />']
    assert "consenso_marketing: document.getElementById(\"marketing\").checked," in html


def test_03_la_patch_e_la_differenza():
    diff = subprocess.run(["diff", "-u", "baseline/dati_personali.html", "c35-consenso/dati_personali.html"],
                          cwd=ROOT / "site-preview", capture_output=True, text=True).stdout
    salvata = (C35 / "dati_personali.patch").read_text(encoding="utf-8")
    togli_date = lambda t: re.sub(r"^(---|\+\+\+) (\S+).*$", r"\1 \2", t, flags=re.M)
    assert togli_date(diff) == togli_date(salvata)


def test_04_backend_conserva_solo_versioni_ufficiali():
    import main
    from site_official import official_versions
    casella = official_versions()["marketing_checkbox"][0]["text"]
    nuova = hashlib.sha256((C35 / "privacy.html").read_bytes()).hexdigest()
    storica = "d615e05d94bac3816b089c077b6b7249c8d43f5957b48011cff0f06cb8b75d15"
    spazi = casella.replace(" ", "  \n ")
    assert main._prova_casella_marketing({"consenso_marketing_testo": spazi,
                                          "informativa_privacy_sha256": nuova.upper()}) == (casella, nuova)
    for raw in ({}, {"consenso_marketing_testo": "", "informativa_privacy_sha256": ""},
                {"consenso_marketing_testo": "x" * 1001, "informativa_privacy_sha256": "g" * 64},
                {"consenso_marketing_testo": ["lista"], "informativa_privacy_sha256": 123},
                {"consenso_marketing_testo": casella + " e SMS", "informativa_privacy_sha256": storica},
                {"consenso_marketing_testo": "Acconsento a tutto.", "informativa_privacy_sha256": "a" * 64}):
        assert main._prova_casella_marketing(raw) == (None, None), raw


def test_05_versioni_ufficiali_verificate():
    import site_official
    site_official.official_versions.cache_clear()
    v = site_official.official_versions()
    assert [(c["version"], c["accepted"], c["channels"]) for c in v["marketing_checkbox"]] == [
        ("casella-marketing-1", True, ["email", "whatsapp"])]
    assert [(p["version"], p["accepted"]) for p in v["privacy"]] == [
        ("privacy-d615e05d", False), ("privacy-675f0724", True)]
    ufficiale = ROOT / "site_official" / "versions"
    assert hashlib.sha256((ufficiale / "privacy-d615e05d.html").read_bytes()).hexdigest().startswith("d615e05d")
    assert (ufficiale / "privacy-675f0724.html").read_bytes() == (C35 / "privacy.html").read_bytes()
    # il testo della casella della pagina da pubblicare e' quello ufficiale
    m = re.search(r'<label for="marketing">(.*?)</label>', (C35 / "dati_personali.html").read_text(encoding="utf-8"),
                  re.S)
    from consent.site_evidence import normalize_text
    assert normalize_text(re.sub(r"<[^>]+>", " ", m.group(1))) == normalize_text(v["marketing_checkbox"][0]["text"])
    nuova = (C35 / "privacy.html").read_text(encoding="utf-8")
    assert "promozionali sui servizi Stima360 tramite email e WhatsApp" in nuova
    assert "telefono e WhatsApp" not in nuova


def test_05b_una_versione_alterata_sparisce(tmp_path, monkeypatch):
    import json
    import shutil
    import site_official
    for f in site_official.DIRECTORY.iterdir():
        shutil.copy(f, tmp_path / f.name)
    (tmp_path / "privacy-675f0724.html").write_bytes(b"alterata")
    dati = json.loads((tmp_path / "versions.json").read_text(encoding="utf-8"))
    dati["marketing_checkbox"][0]["text"] += " (modificato)"
    (tmp_path / "versions.json").write_text(json.dumps(dati), encoding="utf-8")
    monkeypatch.setattr(site_official, "DIRECTORY", tmp_path)
    monkeypatch.setattr(site_official, "VERSIONS", tmp_path / "versions.json")
    site_official.official_versions.cache_clear()
    try:
        v = site_official.official_versions()
        assert v["marketing_checkbox"] == [] and [p["version"] for p in v["privacy"]] == ["privacy-d615e05d"]
        assert site_official.accepted_privacy_hashes() == [] and site_official.accepted_texts() == []
    finally:
        site_official.official_versions.cache_clear()


def test_06_la_patch_della_privacy_tocca_solo_i_canali():
    salvata = (C35 / "privacy.patch").read_text(encoding="utf-8")
    tolte = [r for r in salvata.splitlines() if r.startswith("-") and not r.startswith("---")]
    aggiunte = [r for r in salvata.splitlines() if r.startswith("+") and not r.startswith("+++")]
    assert tolte == ["-e promozionali sui servizi Stima360 tramite email, telefono e WhatsApp"]
    assert aggiunte == ["+e promozionali sui servizi Stima360 tramite email e WhatsApp"]
    diff = subprocess.run(["diff", "-u", "--label", "privacy.html.originale", "--label", "privacy.html",
                           str(ROOT / "site_official" / "versions" / "privacy-d615e05d.html"), str(C35 / "privacy.html")],
                          capture_output=True, text=True).stdout
    assert diff == salvata


def test_07_la_patch_di_main_porta_gli_stessi_file_ufficiali():
    """Il backend del sito (`main`) e il CRM usano le STESSE versioni ufficiali:
    i file di `site_official/` nella patch per `main` sono identici a quelli qui."""
    patch = (C35 / "rilascio" / "main-salva_stima.patch").read_text(encoding="utf-8")
    blocchi = re.split(r"^diff --git ", patch, flags=re.M)
    nuovi = {}
    for b in blocchi:
        m = re.match(r"a/(\S+) b/\S+\nnew file mode", b)
        if m and m.group(1).startswith("site_official/"):
            corpo = b.split("@@", 2)[2].split("\n", 1)[1]
            righe = [r[1:] for r in corpo.split("\n") if r.startswith("+")]
            fine = "" if "\\ No newline at end of file" in corpo else "\n"
            nuovi[m.group(1)] = "\n".join(righe) + fine
    attesi = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "site_official").rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts)
    assert sorted(nuovi) == attesi
    for nome, testo in nuovi.items():
        assert (ROOT / nome).read_text(encoding="utf-8") == testo, nome
    # main.py: SOLO righe aggiunte (7), la chiamata al modulo separato dopo il commit della stima
    blocco_main = next(b for b in blocchi if b.startswith("a/main.py "))
    cambiate = [r for r in blocco_main.splitlines()[4:] if r.startswith(("+", "-"))]
    assert all(r.startswith("+") for r in cambiate) and len(cambiate) == 7, cambiate
    assert "+            site_consent_proof.record_after_commit(conn, new_id, raw)" in cambiate
    for vietato in ("genera_pdf_stima(", "invia_mail(", "invia_whatsapp(", "compute_from_payload("):
        assert not [r for r in patch.splitlines() if r.startswith(("+", "-")) and vietato in r
                    and not r.startswith(("+++", "---")) and "test_c35" not in r and "lambda" not in r], vietato
