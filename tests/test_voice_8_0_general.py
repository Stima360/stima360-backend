"""STIMA VOICE V8-0 - disponibilita' generale: le regole senza database.

  01  nessuna lista pilota: assente o vuota = nessun filtro; un refuso = nessuno
  02  un appuntamento si conferma SEMPRE, anche in modalita' `auto`
  03  la modalita' con cui nasce un'agenzia e' `review`
  04  il freno per operatore: al settimo comando in un minuto, 429 VOICE_RATE_LIMITED
  05  le dettature del dialog Agenda contano nel limite giornaliero dell'agenzia
  06  il router traduce il freno in 429 e l'interfaccia lo spiega
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from voice import policy, service, settings
from voice.dates import ROMA
from voice.executor import VoiceRefused
from voice.planner import build_plan
from voice.policy import Facts, RefFacts, Settings, decide
from voice.schemas import parse_plan_output

NOW = datetime(2026, 10, 10, 16, 0, tzinfo=ROMA)


@pytest.mark.parametrize("valore, atteso", [
    (None, None), ("", None), ("   ", None), ("12", frozenset({12})), ("12;", frozenset()), ("0", frozenset()),
])
def test_01_nessuna_lista_pilota(valore, atteso):
    env = {} if valore is None else {"STIMA_VOICE_PILOT_USER_IDS": valore}
    assert service.pilot_user_ids(env) == atteso


def _piano(comando):
    return build_plan(parse_plan_output({"commands": [comando]}), comando["quote"], NOW)


def test_02_un_appuntamento_si_conferma_sempre():
    appuntamento = _piano({"intent": "create_appointment", "quote": "Domani alle 15 telefonata con Mario Rossi",
                           "when": {"date_text": "domani", "time_text": "alle 15"},
                           "contact": {"first_name": "Mario", "last_name": "Rossi", "phone": "333 1111111"}})
    fatti = Facts(refs={(1, "contact"): RefFacts(1, True)})
    for modo in ("auto", "assisted", "review"):
        d = decide(appuntamento, fatti, Settings(mode=modo, auto_intents=frozenset({"create_appointment"}))).of(1)
        assert d.decision == "ask" and d.reasons == (policy.MODE_REVIEW,), modo
    # le altre operazioni seguono la modalita' come prima
    nota = _piano({"intent": "add_note", "quote": "Nota per Mario Rossi: richiamare", "text": "richiamare",
                   "contact": {"first_name": "Mario", "last_name": "Rossi", "phone": "333 1111111"}})
    assert decide(nota, fatti, Settings(mode="auto")).of(1).decision == "auto"
    # SENTINELLA AGGIORNATA DA STIMA VOICE V9: anche ogni scrittura su incarichi e acquisizioni
    assert policy.ALWAYS_CONFIRM_INTENTS == frozenset({"create_appointment", "mandate_prepare", "mandate_price",
                                                       "acquisition_start"})


def test_03_un_agenzia_nasce_in_revisione():
    from platform_admin import agencies_repository
    assert agencies_repository.VOICE_PROVISION_MODE == "review"
    assert settings.DISABLED.enabled is False            # senza riga: spento (fail-closed)


def _ctx(user=7, agency=1):
    return SimpleNamespace(user_id=user, require_agency=lambda: agency)


def test_04_freno_per_operatore():
    assert service.OPERATOR_PER_MINUTE == 6
    t = NOW
    for i in range(6):
        service._freno_operatore(_ctx(), t + timedelta(seconds=i))
    with pytest.raises(VoiceRefused) as e:
        service._freno_operatore(_ctx(), t + timedelta(seconds=10))
    assert e.value.code == service.RATE_LIMITED
    service._freno_operatore(_ctx(user=8), t + timedelta(seconds=10))     # un collega no
    service._freno_operatore(_ctx(), t + timedelta(seconds=61))          # dopo un minuto si', di nuovo


def test_05_le_dettature_contano_nel_limite_dell_agenzia():
    imp = SimpleNamespace(max_commands_per_day=3)
    for _ in range(3):
        service._conta_interpretazione(_ctx(), imp, NOW)
    with pytest.raises(VoiceRefused) as e:
        service._conta_interpretazione(_ctx(user=9), imp, NOW)            # e' dell'agenzia, non dell'operatore
    assert e.value.code == service.DAILY_LIMIT
    service._conta_interpretazione(_ctx(agency=35), imp, NOW)             # un'altra agenzia no
    service._conta_interpretazione(_ctx(), imp, NOW + timedelta(days=1))  # il giorno dopo si'


def test_06_router_e_interfaccia():
    from pathlib import Path
    from voice import router
    assert router._STATI[service.RATE_LIMITED] == 429
    modello = (Path(__file__).resolve().parents[1] / "static/os_shell/assets/voice/voice-model.js").read_text(encoding="utf-8")
    assert "VOICE_RATE_LIMITED: 'Troppe richieste vocali in un minuto" in modello
