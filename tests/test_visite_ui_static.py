"""ROADMAP VISITE - guardie statiche della UI (esito, foglio visita, aggiornamento al proprietario).

Non servono ne' database ne' browser: leggono i sorgenti dei componenti condivisi e
impongono le regole che il resto del CRM si aspetta (testo dei dati solo con
`textContent`, nessun percorso API nei file dell'Agenda, approvazione esplicita).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[1]
ASSETS = RADICE / "static" / "os_shell" / "assets"
COMPONENTI = [
    ASSETS / "components" / "agenda" / "agenda-visit.js",
    ASSETS / "components" / "visit-owner-update.js",
    ASSETS / "components" / "visit-sheets.js",
]
FILE_AGENDA = [
    ASSETS / "components" / "agenda" / "agenda-visit.js",
    ASSETS / "components" / "agenda" / "agenda-drawer.js",
    ASSETS / "components" / "agenda" / "agenda-dialogs.js",
    ASSETS / "agenda" / "agenda-model.js",
]


def _testo(percorso: Path) -> str:
    return percorso.read_text(encoding="utf-8")


@pytest.mark.parametrize("percorso", COMPONENTI, ids=lambda p: p.name)
def test_i_componenti_visita_non_usano_innerhtml_ne_eval(percorso):
    codice = re.sub(r"//[^\n]*", "", _testo(percorso))
    assert "innerHTML" not in codice and "outerHTML" not in codice
    assert "insertAdjacentHTML" not in codice and "document.write" not in codice
    assert "eval(" not in codice


def test_i_file_agenda_non_chiamano_percorsi_api_diversi_dal_proprio():
    # I percorsi delle altre API stanno nei componenti condivisi, non nei file dell'Agenda.
    for percorso in FILE_AGENDA:
        codice = _testo(percorso)
        for vietato in ("/api/visit-sheets", "/api/owner-reports", "/api/owner-portal"):
            assert vietato not in codice, (percorso.name, vietato)


def test_il_percorso_dell_esito_e_l_unica_rotta_nuova_dell_agenda():
    api = _testo(ASSETS / "agenda" / "agenda-api.js")
    assert "putVisitOutcome" in api and "/visit-outcome" in api


def test_l_aggiornamento_al_proprietario_richiede_la_conferma_esplicita():
    codice = _testo(ASSETS / "components" / "visit-owner-update.js")
    # Il pulsante nasce disattivato e si attiva solo con la casella di controllo.
    assert "approva.disabled = true" in codice
    assert "approva.disabled = !casella.checked" in codice
    assert "if (!casella.checked) return;" in codice
    assert "confirm: true" in codice


def test_la_visita_non_si_chiude_da_sola_e_lo_dice():
    codice = _testo(ASSETS / "components" / "agenda" / "agenda-visit.js")
    assert "La visita non si chiude da sola" in codice
    assert "can_record_feedback" in codice and "can_record_interest" in codice


def test_il_feedback_e_dichiarato_nota_interna():
    codice = _testo(ASSETS / "components" / "agenda" / "agenda-visit.js")
    assert "Nota interna: non viene mai pubblicata al proprietario senza la tua approvazione." in codice


def test_il_foglio_non_simula_una_firma_valida():
    codice = _testo(ASSETS / "components" / "visit-sheets.js")
    assert "Da firmare" in codice
    assert not re.search(r"['\"`]Firmato['\"`]", codice)


@pytest.mark.skipif(shutil.which("node") is None, reason="node non disponibile")
@pytest.mark.parametrize("percorso", COMPONENTI + FILE_AGENDA[1:], ids=lambda p: p.name)
def test_la_sintassi_dei_moduli_e_valida(percorso):
    copia = percorso.with_suffix(".check.mjs")
    try:
        copia.write_text(_testo(percorso), encoding="utf-8")
        esito = subprocess.run(["node", "--check", str(copia)], capture_output=True, text=True, timeout=60)
    finally:
        copia.unlink(missing_ok=True)
    assert esito.returncode == 0, esito.stderr[:800]
