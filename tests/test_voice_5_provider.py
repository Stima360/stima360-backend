"""STIMA Voice Fase 5 - il fornitore AI, senza rete e senza database.

La rete e' una sessione finta: nessuna chiamata reale a un modello. Cosa
prova:
  * fail-closed: senza configurazione (o senza HTTPS) non c'e' fornitore;
    l'interruttore globale e' spento se non vale esattamente "1";
  * la richiesta: solo regole fisse + testo dettato (nessun dato del CRM),
    schema chiuso, temperatura 0, timeout esplicito, chiave solo
    nell'intestazione;
  * ogni deviazione (rete, HTTP, JSON, schema, campi in piu', id, intenti
    inventati) scarta la risposta intera: nessuna azione;
  * nei log non finiscono testo, risposta o chiave.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import pytest
import requests

NOW = datetime.fromisoformat("2026-10-11T09:00:00+02:00")
CONF = {"STIMA_VOICE_AI_URL": "https://ai.example.test/v1/chat/completions",
        "STIMA_VOICE_AI_MODEL": "modello-di-prova", "STIMA_VOICE_AI_KEY": "chiave-segreta-123"}
TESTO = "Domani alle 15 appuntamento con Fabio"
BUONO = {"commands": [{"intent": "create_appointment", "quote": TESTO,
                       "when": {"date_text": "domani", "time_text": "alle 15"},
                       "contact": {"first_name": "Fabio"}}], "clarifications": []}


class Risposta:
    def __init__(self, status=200, corpo=None, grezzo=None):
        self.status_code = status
        self._corpo = corpo
        self.content = grezzo if grezzo is not None else json.dumps(corpo).encode()

    def json(self):
        if self._corpo is None:
            raise ValueError("non JSON")
        return self._corpo


class Sessione:
    def __init__(self, risposta=None, errore=None):
        self.risposta, self.errore, self.chiamate = risposta, errore, []

    def post(self, url, **kw):
        self.chiamate.append((url, kw))
        if self.errore:
            raise self.errore
        return self.risposta


def _completamento(contenuto):
    return Risposta(corpo={"choices": [{"message": {"content": contenuto}}]})


def _fornitore(sessione):
    from voice.provider import HttpVoiceProvider, ProviderConfig
    return HttpVoiceProvider(ProviderConfig.from_env(CONF), session=sessione)


# ---------------------------------------------------------------------------

def test_01_not_configured_means_no_provider():
    from voice.provider import ProviderConfig, ProviderNotConfigured
    for manca in CONF:
        with pytest.raises(ProviderNotConfigured):
            ProviderConfig.from_env({k: v for k, v in CONF.items() if k != manca})
    with pytest.raises(ProviderNotConfigured):
        ProviderConfig.from_env({**CONF, "STIMA_VOICE_AI_URL": "http://ai.example.test/v1"})
    assert ProviderConfig.from_env({**CONF, "STIMA_VOICE_AI_TIMEOUT": "7"}).timeout == (5.0, 7.0)


def test_02_global_switch_is_off_unless_exactly_one():
    from voice.service import globally_enabled
    assert globally_enabled({}) is False
    for valore in ("0", "true", "yes", "on", " ", "11"):
        assert globally_enabled({"STIMA_VOICE_ENABLED": valore}) is False
    assert globally_enabled({"STIMA_VOICE_ENABLED": "1"}) is True


def test_03_request_carries_only_rules_and_the_dictated_text():
    from voice.provider import SYSTEM_PROMPT, interpret
    sessione = Sessione(_completamento(json.dumps(BUONO)))
    esito = interpret(_fornitore(sessione), TESTO, NOW)
    assert esito.commands[0].intent == "create_appointment"
    url, kw = sessione.chiamate[0]
    assert url == CONF["STIMA_VOICE_AI_URL"] and kw["timeout"] == (5.0, 20.0)
    corpo = kw["json"]
    assert corpo["model"] == "modello-di-prova" and corpo["temperature"] == 0
    assert corpo["messages"] == [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": TESTO}]
    assert kw["headers"]["Authorization"] == "Bearer chiave-segreta-123"
    # Fase 7: User-Agent esplicito e senza dati (Cloudflare 1010 senza)
    assert kw["headers"]["User-Agent"] == "STIMA360-Voice/1.0"
    assert set(kw["headers"]) == {"Authorization", "Content-Type", "User-Agent"}
    assert "chiave-segreta-123" not in json.dumps(corpo)
    schema = corpo["response_format"]["json_schema"]["schema"]

    def chiuso(nodo):
        if isinstance(nodo, dict):
            if nodo.get("type") == "object" or "properties" in nodo:
                assert nodo.get("additionalProperties") is False, nodo
            for v in nodo.values():
                chiuso(v)
        elif isinstance(nodo, list):
            for v in nodo:
                chiuso(v)
    chiuso(schema)
    nomi = json.dumps(schema)
    for vietato in ('"contact_id"', '"property_id"', '"agency_id"', '"assigned_user_id"', '"sql"'):
        assert vietato not in nomi


@pytest.mark.parametrize("caso", ["timeout", "rete", "http500", "http429", "non_json", "contenuto_non_json",
                                  "senza_choices", "troppo_grande"])
def test_04_transport_and_format_errors_are_never_actions(caso):
    from voice.provider import ProviderMalformed, ProviderUnavailable, interpret
    sessioni = {
        "timeout": (Sessione(errore=requests.Timeout("lento")), ProviderUnavailable),
        "rete": (Sessione(errore=requests.ConnectionError("giu'")), ProviderUnavailable),
        "http500": (Sessione(Risposta(500, {"error": "x"})), ProviderUnavailable),
        "http429": (Sessione(Risposta(429, {"error": "x"})), ProviderUnavailable),
        "non_json": (Sessione(Risposta(200, None, b"<html>")), ProviderMalformed),
        "contenuto_non_json": (Sessione(_completamento("non e' json")), ProviderMalformed),
        "senza_choices": (Sessione(Risposta(200, {"id": "x"})), ProviderMalformed),
        "troppo_grande": (Sessione(Risposta(200, {"choices": []}, b"x" * 300_000)), ProviderMalformed),
    }
    sessione, errore = sessioni[caso]
    with pytest.raises(errore):
        interpret(_fornitore(sessione), TESTO, NOW)


@pytest.mark.parametrize("uscita", [
    {"commands": [{"intent": "delete_everything", "quote": "x"}]},                       # intento inventato
    {"commands": [{**BUONO["commands"][0], "contact_id": 12}]},                          # un id dal modello
    {"commands": [{**BUONO["commands"][0], "assigned_user_id": 3}]},                     # un agente dal modello
    {"commands": [{"intent": "add_note", "quote": "x", "text": "DROP TABLE contacts;",
                   "contact": {"first_name": "Fabio"}, "sql": "DELETE FROM contacts"}]},  # SQL come azione
    {"commands": BUONO["commands"], "extra": True},                                      # campo in piu'
    {"commands": [{"intent": "create_building", "quote": "x", "units_declared": -3}]},   # fuori dominio
    ["non", "un", "oggetto"],
])
def test_05_anything_outside_the_closed_schema_is_discarded(uscita):
    from voice.provider import ProviderMalformed, interpret
    with pytest.raises(ProviderMalformed):
        interpret(_fornitore(Sessione(_completamento(json.dumps(uscita)))), TESTO, NOW)


def test_06_logs_never_contain_text_response_or_key(caplog):
    from voice.provider import ProviderMalformed, ProviderUnavailable, interpret
    caplog.set_level(logging.DEBUG, logger="stima360.voice.provider")
    segreto = "Mario Rossi 333 1111111 via Roma 10"
    with pytest.raises(ProviderMalformed):
        interpret(_fornitore(Sessione(_completamento(json.dumps({"commands": [{"intent": "x", "quote": segreto}]})))),
                  segreto, NOW)
    with pytest.raises(ProviderUnavailable):
        interpret(_fornitore(Sessione(Risposta(500, {"error": segreto}))), segreto, NOW)
    tutto = " ".join(r.getMessage() for r in caplog.records)
    assert caplog.records and segreto not in tutto and "chiave-segreta-123" not in tutto


def test_07_empty_or_too_long_text_never_reaches_the_model():
    from voice.provider import ProviderMalformed, interpret
    from voice.schemas import MAX_TEXT
    sessione = Sessione(_completamento(json.dumps(BUONO)))
    for testo in ("", "   ", "x" * (MAX_TEXT + 1)):
        with pytest.raises(ProviderMalformed):
            interpret(_fornitore(sessione), testo, NOW)
    assert sessione.chiamate == []


def test_08_audio_is_not_available_in_this_phase():
    from voice.fake_provider import ProviderUnavailable
    with pytest.raises(ProviderUnavailable):
        _fornitore(Sessione()).transcribe(b"\x00", mime_type="audio/webm")
