"""P29-2.6E blocker 2 - IL CRON NON PUO' DIRE "VERDE" SE L'EMAIL NON E' PARTITA.

IL GUASTO CHE QUESTO FILE ESISTE PER IMPEDIRE

Una password SMTP scaduta non solleva niente: `database.invia_mail` stampa e
ritorna `False`, l'adapter lo traduce in `unknown`, il dispatcher lo registra
come `indeterminate`, la rotta risponde 200 e il ledger racconta tutto. Il
giro, visto da fuori, e' andato benissimo. Se il runner esce 0, Render resta
verde per settimane mentre non parte una email - e il ledger lo sa, ma non lo
guarda nessuno.

I TRE CODICI, E LA RAGIONE DI CIASCUNO

    0   HTTP riuscito E failed == indeterminate == lost == 0.
    1   guasto TECNICO: configurazione, login, rete, timeout, protocollo.
    2   guasto APPLICATIVO: il giro e' andato, ma qualcosa non e' partito.

La differenza fra 1 e 2 e' operativa: l'1 si guarda nella piattaforma, il 2 nel
ledger. Un solo codice per entrambi obbligherebbe a leggere i log per sapere
quale dei due mestieri serve.

`suppressed` NON E' UN GUASTO, ED E' LA PARTE PIU' FACILE DA SBAGLIARE

E' l'esito legittimo del gate di consenso: un contatto che ha revocato il
consenso marketing produce `suppressed` a ogni giro, per sempre. Contarlo fra i
guasti renderebbe rosso un cron che sta funzionando esattamente come deve, e un
allarme che suona sempre e' un allarme che nessuno guarda piu'.

Nessun PostgreSQL qui: si misura il CONTRATTO del runner. La catena vera, con
l'app e il database, e' in `test_p29_2_6e_dispatch_ops_postgres.py`.
"""

from __future__ import annotations

import importlib

import pytest

requests = pytest.importorskip("requests")

runner = importlib.import_module("run_communication_dispatch_cron")


@pytest.fixture
def ambiente(monkeypatch):
    """Le tre env obbligatorie, e nient'altro: i default devono bastare."""
    monkeypatch.setenv("COMMUNICATION_DISPATCH_BASE_URL", "https://esempio.it")
    monkeypatch.setenv("COMMUNICATION_DISPATCH_EMAIL", "cron@example.it")
    monkeypatch.setenv("COMMUNICATION_DISPATCH_PASSWORD", "x")
    for avanzo in ("COMMUNICATION_DISPATCH_CHANNEL", "COMMUNICATION_DISPATCH_LIMIT",
                   "COMMUNICATION_CONNECT_TIMEOUT_SECONDS",
                   "COMMUNICATION_READ_TIMEOUT_SECONDS"):
        monkeypatch.delenv(avanzo, raising=False)


ZERO = {"claimed": 0, "sent": 0, "suppressed": 0, "failed": 0,
        "indeterminate": 0, "lost": 0}


def conteggi(**cambiamenti) -> dict:
    return {**ZERO, **cambiamenti}


# ---------------------------------------------------------------------------
# Il predicato, da solo
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dati, guasto", [
    (conteggi(claimed=1, sent=1), False),
    (conteggi(), False),
    (conteggi(claimed=3, suppressed=3), False),
    (conteggi(claimed=1, failed=1), True),
    (conteggi(claimed=1, indeterminate=1), True),
    (conteggi(claimed=1, lost=1), True),
    (conteggi(claimed=3, sent=2, failed=1), True),
    (conteggi(claimed=2, sent=1, suppressed=1), False),
])
def test_exit_1_il_predicato_di_guasto_applicativo(dati, guasto):
    assert runner._application_failure(dati) is guasto


def test_exit_2_suppressed_non_e_un_guasto_e_non_deve_diventarlo():
    """Fissato per NOME: se qualcuno aggiungesse `suppressed` all'elenco, un
    cron marketing che funziona diventerebbe rosso per sempre."""
    assert "suppressed" not in runner.CONTEGGI_DI_GUASTO
    assert "sent" not in runner.CONTEGGI_DI_GUASTO
    assert "claimed" not in runner.CONTEGGI_DI_GUASTO
    assert set(runner.CONTEGGI_DI_GUASTO) == {"failed", "indeterminate", "lost"}


# ---------------------------------------------------------------------------
# I codici di uscita veri, da `main()`
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dati, atteso", [
    (conteggi(claimed=1, sent=1), 0),
    (conteggi(), 0),
    (conteggi(claimed=2, suppressed=2), 0),
    (conteggi(claimed=1, failed=1), 2),
    (conteggi(claimed=1, indeterminate=1), 2),
    (conteggi(claimed=1, lost=1), 2),
])
def test_exit_3_main_traduce_i_conteggi_nel_codice(ambiente, monkeypatch, dati, atteso):
    monkeypatch.setattr(runner, "run_once", lambda config, **kw: dati)
    assert runner.main() == atteso


def test_exit_4_una_configurazione_incompleta_e_un_guasto_tecnico(monkeypatch):
    monkeypatch.delenv("COMMUNICATION_DISPATCH_BASE_URL", raising=False)
    monkeypatch.setenv("COMMUNICATION_DISPATCH_EMAIL", "cron@example.it")
    monkeypatch.setenv("COMMUNICATION_DISPATCH_PASSWORD", "x")
    assert runner.main() == 1


# ---------------------------------------------------------------------------
# I guasti tecnici, con una sessione finta
# ---------------------------------------------------------------------------

class Risposta:
    def __init__(self, stato=200, corpo=None):
        self.status_code = stato
        self._corpo = corpo if corpo is not None else conteggi()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._corpo


class SessioneFinta:
    """Una sessione che risponde secondo un copione, e annota cosa le chiedono."""

    def __init__(self, copione):
        self.copione = copione
        self.chiamate = []

    def post(self, url, **kwargs):
        self.chiamate.append(url)
        for pezzo, esito in self.copione.items():
            if pezzo in url:
                if isinstance(esito, Exception):
                    raise esito
                return esito
        return Risposta()


def config_finta():
    return runner.Config(base_url="https://esempio.it", email="cron@example.it",
                         password="x", channel="email", limit=10,
                         timeout=(5.0, 60.0))


def _tecnico(sessione):
    """Fa girare il runner VERO contro la sessione finta e pretende un guasto
    tecnico. Restituisce la sessione, con annotato cio' che le e' stato chiesto."""
    with pytest.raises(runner.TechnicalError):
        runner.run_once(config_finta(), sessione=sessione)
    return sessione


def test_exit_5_un_login_rifiutato_e_un_guasto_tecnico(ambiente, monkeypatch):
    sessione = _tecnico(SessioneFinta({"login": Risposta(401)}))

    assert not any("dispatch" in u for u in sessione.chiamate), (
        "ha provato a dispacciare dopo un login rifiutato")
    assert any("logout" in u for u in sessione.chiamate), "logout non tentato"

    # E il codice di uscita e' 1, non 2: il dispatch non e' mai avvenuto.
    def esplode(config, **kw):
        raise runner.TechnicalError("login")

    monkeypatch.setattr(runner, "run_once", esplode)
    assert runner.main() == 1


def test_exit_6_la_rete_che_cade_e_un_guasto_tecnico(ambiente, monkeypatch):
    sessione = _tecnico(SessioneFinta({"dispatch": requests.ConnectionError("giu'")}))
    assert any("logout" in u for u in sessione.chiamate), "logout non tentato"

    def esplode(config, **kw):
        raise runner.TechnicalError("http_or_network")

    monkeypatch.setattr(runner, "run_once", esplode)
    assert runner.main() == 1


def test_exit_7_un_timeout_e_un_guasto_tecnico(ambiente, monkeypatch):
    sessione = _tecnico(SessioneFinta({"dispatch": requests.Timeout("scaduto")}))
    assert any("logout" in u for u in sessione.chiamate)

    def esplode(config, **kw):
        raise runner.TechnicalError("timeout")

    monkeypatch.setattr(runner, "run_once", esplode)
    assert runner.main() == 1


def test_exit_8_un_corpo_che_non_e_il_contratto_e_un_guasto_tecnico():
    """Un 200 con conteggi mancanti non e' un giro riuscito: e' un contratto
    cambiato, e va visto subito invece di essere letto come zero."""
    sessione = SessioneFinta({"dispatch": Risposta(200, {"sent": 1})})
    with pytest.raises(runner.TechnicalError) as exc:
        runner.run_once(config_finta(), sessione=sessione)
    assert "invalid_json" in str(exc.value)


def test_exit_9_il_logout_e_sempre_tentato():
    """Anche dopo un giro perfettamente riuscito, e anche dopo un guasto.

    Una sessione abbandonata a ogni giro e' una riga viva in piu' ogni volta.
    """
    riuscito = SessioneFinta({})
    runner.run_once(config_finta(), sessione=riuscito)
    assert any("logout" in u for u in riuscito.chiamate)

    fallito = SessioneFinta({"dispatch": requests.ConnectionError("giu'")})
    with pytest.raises(runner.TechnicalError):
        runner.run_once(config_finta(), sessione=fallito)
    assert any("logout" in u for u in fallito.chiamate)


def test_exit_10_il_log_non_nomina_credenziali_ne_destinatari(capsys):
    """Un log di cron finisce in posti che non controlliamo."""
    sessione = SessioneFinta({"dispatch": Risposta(200, conteggi(claimed=1, sent=1))})
    runner.run_once(config_finta(), sessione=sessione)
    stampato = capsys.readouterr().out

    assert "status=completed" in stampato and "sent=1" in stampato
    for segreto in ("cron@example.it", "mario@example.it", "Cookie", "Set-Cookie"):
        assert segreto not in stampato, f"{segreto!r} finito nel log del cron"

    # La riga e' fatta di campi `nome=valore`: si controllano i NOMI, cosi' la
    # verifica non dipende da quanto sia distinguibile la password di prova.
    campi = dict(pezzo.split("=", 1) for pezzo in stampato.split() if "=" in pezzo)
    assert not {"email", "password", "user", "destination", "to", "cookie"} & set(campi)
    # SENTINELLA AGGIORNATA DA P29-3E (collisione dichiarata). Un giro
    # stampa adesso DUE righe - il tick e il dispatch - e quella del tick
    # porta `phase=journey_tick` con i conteggi del motore. La garanzia che
    # questo test da' non cambia: l'elenco dei nomi ammessi resta CHIUSO, e
    # nessun nome nuovo e' un indirizzo, una credenziale o un cookie.
    assert set(campi) <= ({"status", "phase", "channel", "duration_ms", "reason"}
                          | set(runner.CONTEGGI)
                          | set(runner.CONTEGGI_TICK)), sorted(campi)
