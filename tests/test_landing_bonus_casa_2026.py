"""Landing pubblica "Bonus Casa 2026" montata su `/bonus-casa-2026/`.

Si prova, SENZA database e SENZA login: la pagina, il logo, la locandina e il
PDF rispondono dall'applicazione vera con i byte dei file in
`landing/bonus-casa-2026/`; i percorsi relativi scritti nell'HTML esistono.
"""
from __future__ import annotations

import hashlib
import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "landing" / "bonus-casa-2026"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    main = importlib.import_module("main")
    with TestClient(main.app) as c:
        yield c


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_la_pagina_e_servita_senza_login(client):
    r = client.get("/bonus-casa-2026/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.content == (LANDING / "index.html").read_bytes()
    assert "Scarica il PDF gratuito" in r.text
    assert 'href="https://www.stima360.it"' in r.text


def test_il_percorso_senza_barra_finale_porta_alla_pagina(client):
    r = client.get("/bonus-casa-2026", follow_redirects=True)
    assert r.status_code == 200
    assert r.content == (LANDING / "index.html").read_bytes()


@pytest.mark.parametrize("nome, tipo", [
    ("logo.jpg", "image/jpeg"),
    ("locandina.webp", "image/webp"),
    ("bonus-casa-2026.pdf", "application/pdf"),
])
def test_gli_asset_rispondono_con_i_byte_originali(client, nome, tipo):
    r = client.get(f"/bonus-casa-2026/assets/{nome}")
    assert r.status_code == 200
    assert r.headers["content-type"] == tipo
    assert hashlib.sha256(r.content).hexdigest() == _sha(LANDING / "assets" / nome)


def test_ogni_percorso_relativo_dell_html_esiste_ed_e_servito(client):
    html = (LANDING / "index.html").read_text(encoding="utf-8")
    relativi = set(re.findall(r'(?:src|href)="(assets/[^"]+)"', html))
    assert relativi == {"assets/logo.jpg", "assets/locandina.webp", "assets/bonus-casa-2026.pdf"}
    for rel in relativi:
        assert client.get(f"/bonus-casa-2026/{rel}").status_code == 200


def test_il_pdf_e_la_guida_bonus_casa_2026():
    assert (LANDING / "assets" / "bonus-casa-2026.pdf").read_bytes().startswith(b"%PDF")
    assert _sha(LANDING / "assets" / "bonus-casa-2026.pdf") == \
        "d62fee48248766a6c4c07b0c08a644a9e4c96f147ae5960b154a962dfe354e18"


def test_un_file_inesistente_sotto_la_landing_e_404(client):
    assert client.get("/bonus-casa-2026/assets/non-esiste.pdf").status_code == 404
