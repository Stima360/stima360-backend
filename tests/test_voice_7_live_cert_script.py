"""STIMA Voice Fase 7 - lo script di certificazione live, senza database e senza rete.

Prova le parti che devono valere prima di qualunque scrittura: il bersaglio
(un solo nome di database), l'elenco delle rotte allineato al router vero, le
conferme che non scelgono mai un id fuori dai candidati, i testi dei tre
scenari, e che nel sorgente non ci sia una DELETE ne' una stampa di segreti.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "voice_7_live_cert.py"


def _modulo():
    spec = importlib.util.spec_from_file_location("voice_7_live_cert", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.mark.parametrize("nome", [None, "", "stima360_db", "stima360", "stima360_db_test2", "STIMA360_DB_TEST",
                                  "stima360_crm", " stima360_db_prod "])
def test_l1_only_the_test_database_is_accepted(nome):
    m = _modulo()
    with pytest.raises(m.GuardFailure):
        m.assert_certification_database(nome)
    assert m.assert_certification_database("stima360_db_test") == "stima360_db_test"


def test_l2_route_list_matches_the_real_router():
    m = _modulo()
    from voice import router as voice_router
    vere = sorted((meth, r.path.replace("{command_id}", "1").replace("{ordinal}", "1"))
                  for r in voice_router.router.routes for meth in r.methods)
    assert sorted(m.ROTTE) == vere


def test_l3_confirmations_never_pick_an_id_outside_the_candidates():
    m = _modulo()
    vista = {"steps": [
        {"ordinal": 1, "state": "pending", "needs": "needs_confirmation", "candidates": {}},
        {"ordinal": 2, "state": "pending", "needs": "needs_confirmation",
         "candidates": {"contact": [{"id": 10, "label": "x"}, {"id": 11, "label": "y"}]}},
        {"ordinal": 3, "state": "pending", "needs": "needs_answer", "candidates": {}},
        {"ordinal": 4, "state": "succeeded", "needs": None, "candidates": {}},
    ]}
    assert m.conferme(vista, {"contact": 11}) == [{"ordinal": 1, "choices": None},
                                                  {"ordinal": 2, "choices": {"contact": 11}}]
    for sbagliato in (None, {}, {"contact": 99}):
        with pytest.raises(m.GuardFailure):
            m.conferme(vista, sbagliato)


def test_l4_the_three_scenarios_are_the_mandate_texts():
    m = _modulo()
    assert m.SCENARIO_A == "Fabio vuole vendere un trilocale in via Duca d'Aosta"
    assert m.SCENARIO_B == "Censisci una palazzina con 12 appartamenti"
    assert m.SCENARIO_C == "Domani alle 15 appuntamento con Fabio"


def test_l5_no_delete_no_secret_printing_and_sessions_are_revoked():
    testo = SCRIPT.read_text(encoding="utf-8")
    codice = "\n".join(r for r in testo.splitlines() if not r.lstrip().startswith("#"))
    assert not re.search(r"\bDELETE\s+FROM\b", codice, re.I)
    assert not re.search(r"\b(TRUNCATE|DROP)\b", codice)
    assert "UPDATE operator_sessions SET revoked_at = NOW()" in codice
    for segreto in ("DB_PASSWORD", "STIMA_VOICE_AI_KEY", "print(token", "print(grezzo"):
        assert segreto not in codice
    # i dati sintetici non hanno recapiti: nessuna comunicazione possibile
    assert '"email"' not in codice and '"phone"' not in codice
