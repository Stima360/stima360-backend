"""DOCUMENSO - guardie statiche dell'interfaccia di firma (`visit-sheets.js`): niente HTML con dati, i collegamenti di
firma solo http/https e solo su richiesta, il livello e' sempre «semplice», nessun testo che prometta invii."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "static" / "os_shell" / "assets" / "components" / "visit-sheets.js"


def code():
    return re.sub(r"//[^\n]*", "", JS.read_text(encoding="utf-8"))


def test_nessun_html_con_dati():
    c = code()
    assert "innerHTML" not in c and "outerHTML" not in c and "insertAdjacentHTML" not in c and "eval(" not in c


def test_i_collegamenti_di_firma_sono_validati_e_si_aprono_senza_riferimento():
    c = code()
    assert "safeHref" in c and "u.protocol === 'https:' || u.protocol === 'http:'" in c
    assert "rel = 'noopener noreferrer'" in c and "target = '_blank'" in c
    assert "javascript:" not in c


def test_i_collegamenti_si_caricano_solo_su_richiesta():
    c = code()
    assert c.count("signing-links") >= 2
    # il caricamento sta dentro showLinks, chiamato dal pulsante: non dentro load()
    load = c[c.index("async function load()"):c.index("await load();\n}")]
    assert "apiGet(`${ESIGN}/${r.id}/signing-links`)" not in load and "showLinks(r, linksHolder)" in load


def test_livello_semplice_e_nessuna_promessa_di_invio():
    c = code()
    assert "Firma elettronica SEMPLICE" in c and "nessun messaggio viene inviato" in c
    for vietato in ("avanzata", "qualificata", "FEA", "FEQ", "Invia al cliente", "Invia per email"):
        assert vietato not in c, vietato


@pytest.mark.skipif(shutil.which("node") is None, reason="node non disponibile")
def test_sintassi_del_modulo(tmp_path):
    copia = tmp_path / "visit-sheets.mjs"
    copia.write_text(JS.read_text(encoding="utf-8"), encoding="utf-8")
    assert subprocess.run(["node", "--check", str(copia)], capture_output=True, text=True, timeout=60).returncode == 0
