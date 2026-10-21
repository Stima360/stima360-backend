"""INFORMATIVE PRIVACY (Super Admin › Rete) - prove STATICHE, senza database.

La scheda «Informative privacy» non aggiunge API ne' migration: usa le quattro
rotte di `platform_admin/router.py` nate con la Fase B. Qui si prova che:

  * le quattro rotte sono registrate nell'app VERA (`main.app`), in OpenAPI,
    e ognuna dipende da `require_super_admin_context`;
  * la scheda e' cablata in `rete.js` (voce fra le TABS, import, ramo di
    `mostraTab`) e vive dietro la stessa porta `mostraSoloSuperAdmin`;
  * la vista manda al server SOLO i campi della richiesta (`purpose`,
    `version`, `context`, `content`, `valid_from`, `valid_to`): mai
    `content_hash`, `created_by`, `content_available`;
  * niente e' precompilato: il campo del testo nasce vuoto e la vista non
    pubblica nulla da sola (nessuna POST fuori dal `submit` o dalla conferma);
  * il testo integrale passa da `textContent`, mai da `innerHTML`;
  * la chiusura passa dal dialogo di conferma (`confirmAction`);
  * il Registro etichetta le due operazioni di audit e il loro oggetto;
  * i tre file JavaScript sono sintatticamente validi (`node --check`).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VISTE = ROOT / "static" / "os_shell" / "assets" / "views"
VISTA = VISTE / "rete-informative.js"
RETE = VISTE / "rete.js"
REGISTRO = VISTE / "rete-registro.js"

ROTTE = {
    ("GET", "/api/platform/consent-notices"),
    ("POST", "/api/platform/consent-notices"),
    ("GET", "/api/platform/consent-notices/{notice_id}"),
    ("POST", "/api/platform/consent-notices/{notice_id}/close"),
}


def _senza_commenti(testo: str) -> str:
    testo = re.sub(r"/\*[\s\S]*?\*/", "", testo)
    return re.sub(r"(?m)^\s*//[^\n]*", "", testo)


# ---------------------------------------------------------------------------
# A  le rotte, nell'app vera
# ---------------------------------------------------------------------------

def _rotte_effettive(radici=None):
    """Le rotte dell'app VERA. FastAPI >= 0.140 include i router in modo
    pigro (`_IncludedRouter`, anche annidati): le rotte si leggono dai
    candidati effettivi, che portano gia' le dipendenze del router e quelle
    di `include_router`."""
    import main
    for r in (main.app.routes if radici is None else radici):
        if hasattr(r, "effective_candidates"):
            yield from _rotte_effettive(r.effective_candidates())
        elif getattr(r, "methods", None) and getattr(r, "path", None):
            yield r


def test_a1_le_quattro_rotte_sono_registrate_in_main_app():
    rotte = {(metodo, r.path) for r in _rotte_effettive() for metodo in r.methods if metodo != "HEAD"}
    assert ROTTE <= rotte, sorted(ROTTE - rotte)


def test_a2_le_quattro_rotte_sono_in_openapi():
    import main
    percorsi = main.app.openapi()["paths"]
    for metodo, percorso in ROTTE:
        assert percorso in percorsi, percorso
        assert metodo.lower() in percorsi[percorso], (metodo, percorso)


def test_a3_ogni_rotta_dipende_dal_super_admin_designato():
    from platform_admin.dependencies import require_platform_admin, require_super_admin_context
    trovate = 0
    for r in _rotte_effettive():
        if r.path in {p for _, p in ROTTE}:
            trovate += 1
            nomi = {d.call for d in r.dependant.dependencies}
            assert require_super_admin_context in nomi, r.path
            # la protezione di tutto il router (main.py) resta davanti
            assert require_platform_admin in nomi, r.path
    assert trovate == 4


# ---------------------------------------------------------------------------
# B  la scheda nella Rete
# ---------------------------------------------------------------------------

def test_b1_la_scheda_e_cablata_in_rete_js():
    testo = RETE.read_text(encoding="utf-8")
    assert "{ key: 'informative', label: 'Informative privacy' }" in testo
    assert "import { renderInformative } from './rete-informative.js'" in testo
    assert "if (key === 'informative') return renderInformative(contentEl, hostEl);" in testo
    # la porta della Rete precede qualunque scheda: la vista non la ridefinisce
    assert "if (mostraSoloSuperAdmin(container, platformMe)) return;" in testo
    assert testo.index("mostraSoloSuperAdmin(container, platformMe)") < testo.index("mostraTab('agenzie')")


def test_b2_la_vista_parla_solo_con_le_rotte_delle_informative():
    testo = _senza_commenti(VISTA.read_text(encoding="utf-8"))
    chiamate = re.findall(r"api(Get|Post|Put|Patch|Delete)\(([^,)]+)", testo)
    assert chiamate, "nessuna chiamata API"
    for metodo, url in chiamate:
        assert metodo in {"Get", "Post"}, (metodo, url)
        assert "/consent-notices" in url, url
    assert "fetch(" not in testo


def test_b3_la_vista_manda_solo_i_campi_della_richiesta():
    testo = VISTA.read_text(encoding="utf-8")
    corpo = re.search(r"const corpo = \{([\s\S]*?)\};", testo)
    assert corpo, "il corpo della POST non e' un oggetto letterale"
    chiavi = set(re.findall(r"^\s*(\w+):", corpo.group(1), flags=re.M))
    # SENTINELLA AGGIORNATA DA C1/C2: i canali che il testo copre.
    assert chiavi == {"purpose", "version", "context", "content", "valid_from", "valid_to", "channels"}, chiavi
    for vietato in ("content_hash", "created_by", "content_available", "actor", "user_id"):
        assert f"{vietato}:" not in corpo.group(1), vietato


def test_b4_niente_precompilato_e_nessuna_pubblicazione_automatica():
    testo = VISTA.read_text(encoding="utf-8")
    textarea = re.search(r"<textarea[^>]*name=\"content\"[^>]*>(.*?)</textarea>", testo, flags=re.S)
    assert textarea and textarea.group(1) == "", "il testo dell'informativa deve nascere vuoto"
    assert 'value="${' not in re.search(r"<input[^>]*name=\"version\"[^>]*>", testo).group(0)
    assert 'value="${' not in re.search(r"<input[^>]*name=\"context\"[^>]*>", testo).group(0)
    # le due scritture stanno dentro il submit e dentro la conferma, e nient'altro
    assert testo.count("apiPost(") == 2
    assert "form.addEventListener('submit'" in testo
    assert "await confirmAction(host," in testo
    assert testo.index("await confirmAction(host,") < testo.index("/close`")
    assert "'Chiudi la versione'" in testo and "tono: 'danger'" in testo


def test_b5_il_testo_integrale_passa_da_textcontent():
    testo = VISTA.read_text(encoding="utf-8")
    assert "querySelector('[data-informativa-testo]').textContent = n.content" in testo
    assert "innerHTML = n.content" not in testo
    assert "${n.content}" not in testo and "${escapeHtml(n.content)}" not in testo
    # gli errori comprensibili del server arrivano dal `message` dell'Error di api-client
    assert "error.message" in testo and "error.detail" not in testo
    assert "errore.textContent = spiega(error" in testo


def test_b6_il_registro_etichetta_le_operazioni_sulle_informative():
    testo = REGISTRO.read_text(encoding="utf-8")
    assert "'platform.consent_notice.create': 'Informativa pubblicata'" in testo
    assert "'platform.consent_notice.close': 'Informativa chiusa'" in testo
    assert "consent_notice: 'Informativa'" in testo
    from platform_admin.enums import ACTION_CONSENT_NOTICE_CLOSE, ACTION_CONSENT_NOTICE_CREATE
    assert ACTION_CONSENT_NOTICE_CREATE == "platform.consent_notice.create"
    assert ACTION_CONSENT_NOTICE_CLOSE == "platform.consent_notice.close"


@pytest.mark.parametrize("file", [VISTA, RETE, REGISTRO])
def test_b7_node_check(file):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node non installato")
    esito = subprocess.run([node, "--check", str(file)], capture_output=True, text=True, timeout=30)
    assert esito.returncode == 0, esito.stderr
