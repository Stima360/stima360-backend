"""Il runner del cron con l'IDENTITA' TECNICA, senza rete e senza database.

  01  configurazione: con il segreto bastano URL e agenzia (nessuna password);
      senza agenzia e' un guasto di configurazione; senza segreto valgono le
      credenziali di prima.
  02  un giro: UNA chiamata alla rotta interna, col segreto nell'intestazione
      dedicata, nessun login; tre righe di log; exit 0.
  03  il segreto non compare mai nel log.
  04  i rifiuti hanno un nome (401 -> token_rejected) ed exit 1; un tick
      fallito sul server rende il giro 2, come prima.
"""
from __future__ import annotations

import pytest

import run_communication_dispatch_cron as runner

SEGRETO = "segreto-di-prova-" + "x" * 32
CONTEGGI_INVIO = {"claimed": 1, "sent": 1, "suppressed": 0, "failed": 0, "indeterminate": 0, "lost": 0}
CONTEGGI_TICK = {k: 0 for k in runner.CONTEGGI_TICK}
CONTEGGI_PROMEMORIA = {k: 0 for k in runner.CONTEGGI_PROMEMORIA}


class Risposta:
    def __init__(self, stato, corpo=None):
        self.status_code = stato
        self._corpo = corpo

    def json(self):
        if self._corpo is None:
            raise ValueError("no json")
        return self._corpo


class Sessione:
    def __init__(self, risposta):
        self.chiamate = []
        self.risposta = risposta

    def post(self, url, **kw):
        self.chiamate.append((url, kw))
        return self.risposta

    def close(self):
        pass


def _corpo(journey="completed"):
    return {"journey": {"status": journey, "counts": dict(CONTEGGI_TICK) if journey == "completed" else None},
            "reminder": {"status": "completed", "counts": dict(CONTEGGI_PROMEMORIA)},
            "dispatch": dict(CONTEGGI_INVIO)}


@pytest.fixture
def ambiente(monkeypatch):
    for nome in ("COMMUNICATION_DISPATCH_EMAIL", "COMMUNICATION_DISPATCH_PASSWORD"):
        monkeypatch.delenv(nome, raising=False)
    monkeypatch.setenv("COMMUNICATION_DISPATCH_BASE_URL", "https://crm-test.example.invalid")
    monkeypatch.setenv("COMMUNICATION_CRON_TOKEN", SEGRETO)
    monkeypatch.setenv("COMMUNICATION_CRON_AGENCY_ID", "1")
    return monkeypatch


def test_01_configurazione(ambiente):
    config = runner.load_config()
    assert config.token == SEGRETO and config.agency_id == 1 and not config.email and not config.password
    ambiente.setenv("COMMUNICATION_CRON_AGENCY_ID", "uno")
    with pytest.raises(runner.ConfigurationError, match="COMMUNICATION_CRON_AGENCY_ID"):
        runner.load_config()
    ambiente.delenv("COMMUNICATION_CRON_TOKEN")
    with pytest.raises(runner.ConfigurationError, match="credentials"):
        runner.load_config()


def test_02_una_chiamata_sola_nessun_login(ambiente, capsys):
    sessione = Sessione(Risposta(200, _corpo()))
    dati = runner.run_once(runner.load_config(), sessione=sessione)
    assert [u for u, _ in sessione.chiamate] == ["https://crm-test.example.invalid" + runner.ROTTA_GIRO_INTERNO]
    _, kw = sessione.chiamate[0]
    assert kw["headers"] == {runner.CRON_TOKEN_HEADER: SEGRETO}
    assert kw["json"] == {"agency_id": 1, "channel": "email", "limit": 10, "journey_limit": 500}
    assert dati["sent"] == 1 and dati["journey_status"] == runner.TICK_COMPLETATO
    righe = capsys.readouterr().out.strip().splitlines()
    assert [r.split()[1] for r in righe[:2]] == ["phase=journey_tick", "phase=reminder_tick"]
    assert righe[2].startswith("status=completed channel=email claimed=1 sent=1")
    assert not runner._application_failure(dati)


def test_03_il_segreto_non_finisce_nel_log(ambiente, capsys, monkeypatch):
    for risposta in (Risposta(200, _corpo()), Risposta(401), Risposta(500)):
        monkeypatch.setattr(runner.requests, "Session", lambda r=risposta: Sessione(r))
        runner.main()
    assert SEGRETO not in capsys.readouterr().out


@pytest.mark.parametrize("stato, motivo", [(401, "token_rejected"), (403, "agency_not_allowed"),
                                           (409, "agency_not_active"),
                                           (503, "token_not_configured_on_server"), (500, "http_500")])
def test_04_i_rifiuti_hanno_un_nome_ed_exit_1(ambiente, capsys, monkeypatch, stato, motivo):
    monkeypatch.setattr(runner.requests, "Session", lambda: Sessione(Risposta(stato)))
    assert runner.main() == 1
    assert f"reason={motivo}" in capsys.readouterr().out


def test_05_un_tick_fallito_sul_server_rende_il_giro_2(ambiente, monkeypatch):
    monkeypatch.setattr(runner.requests, "Session", lambda: Sessione(Risposta(200, _corpo("failed"))))
    assert runner.main() == 2
    monkeypatch.setattr(runner.requests, "Session", lambda: Sessione(Risposta(200, _corpo())))
    assert runner.main() == 0
