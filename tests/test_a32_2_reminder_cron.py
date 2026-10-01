"""A32-2 - il giro dei promemoria dentro il cron del dispatch (C1-C9).

    login -> journeys/tick -> reminders/tick -> dispatch -> logout

Senza database e senza rete: una sessione finta risponde secondo un copione e
annota l'ordine. La regola, come per le journey (P29-3E): QUALUNQUE COSA FACCIA
IL GIRO DEI PROMEMORIA, IL DISPATCH PARTE - e il giro finisce 2 se i promemoria
non hanno fatto il loro lavoro.
"""
from __future__ import annotations

import importlib

import pytest

requests = pytest.importorskip("requests")

runner = importlib.import_module("run_communication_dispatch_cron")

LOGIN = "/api/operator-auth/login"
LOGOUT = "/api/operator-auth/logout"
JOURNEY = "/api/communication/journeys/tick"
REMINDER = "/api/communication/reminders/tick"
DISPATCH = "/api/communication/dispatch"

ZERO_DISPATCH = {"claimed": 0, "sent": 0, "suppressed": 0, "failed": 0,
                 "indeterminate": 0, "lost": 0}
ZERO_JOURNEY = {"stopped": 0, "advanced": 0, "completed": 0, "enrolled_active": 0,
                "enrolled_stopped": 0, "enrolled_skipped": 0, "queued": 0,
                "queued_idempotent": 0, "awaiting_operator": 0, "errors": 0}
ZERO_REMINDER = {"scanned": 0, "due": 0, "queued": 0, "queued_idempotent": 0,
                 "not_due": 0, "ineligible": 0, "errors": 0,
                 "skipped_by_reason": {"contact_archived": 0}}


class Risposta:
    def __init__(self, stato=200, corpo=None):
        self.status_code = stato
        self._corpo = corpo

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        if self._corpo is None:
            raise ValueError("nessun corpo")
        return self._corpo


class Sessione:
    def __init__(self, copione=None):
        self.copione = copione or {}
        self.chiamate: list[tuple[str, dict]] = []

    def post(self, url, **kwargs):
        self.chiamate.append((url, kwargs))
        for pezzo, esito in self.copione.items():
            if pezzo in url:
                if isinstance(esito, Exception):
                    raise esito
                return esito() if callable(esito) else esito
        if JOURNEY in url:
            return Risposta(200, dict(ZERO_JOURNEY))
        if REMINDER in url:
            return Risposta(200, dict(ZERO_REMINDER))
        if DISPATCH in url:
            return Risposta(200, dict(ZERO_DISPATCH))
        return Risposta(204, None)

    def close(self):
        self.chiusa = True

    def fasi(self):
        nomi = {LOGIN: "login", LOGOUT: "logout", JOURNEY: "journeys",
                REMINDER: "reminders", DISPATCH: "dispatch"}
        return [next(n for p, n in nomi.items() if p in url) for url, _ in self.chiamate]


def config(**cambiamenti):
    base = dict(base_url="https://esempio.it", email="cron@example.it",
                password="segreto-da-non-stampare", channel="email", limit=10,
                timeout=(5.0, 60.0), journey_limit=500)
    return runner.Config(**{**base, **cambiamenti})


def esegui(monkeypatch, sessione, **cambiamenti):
    """`main()` vero, con la config e la sessione finte: il codice di uscita."""
    monkeypatch.setattr(runner, "load_config", lambda: config(**cambiamenti))
    monkeypatch.setattr(runner.requests, "Session", lambda: sessione)
    return runner.main()


# ---------------------------------------------------------------------------

def test_C1_ordine_esatto():
    s = Sessione()
    runner.run_once(config(), sessione=s)
    assert s.fasi() == ["login", "journeys", "reminders", "dispatch", "logout"]


def test_C1b_il_corpo_del_giro_promemoria_e_vuoto():
    s = Sessione()
    runner.run_once(config(), sessione=s)
    [(_, kw)] = [c for c in s.chiamate if REMINDER in c[0]]
    assert kw["json"] == {} and "auth" not in kw


def test_C2_cio_che_il_giro_accoda_parte_nello_stesso_giro(monkeypatch):
    stato = {"in_coda": 0}

    def promemoria():
        stato["in_coda"] += 2
        return Risposta(200, {**ZERO_REMINDER, "scanned": 3, "due": 2, "queued": 2})

    def dispatch():
        n, stato["in_coda"] = stato["in_coda"], 0
        return Risposta(200, {**ZERO_DISPATCH, "claimed": n, "sent": n})

    s = Sessione({REMINDER: promemoria, DISPATCH: dispatch})
    dati = runner.run_once(config(), sessione=s)
    assert dati["claimed"] == 2 and dati["sent"] == 2
    assert dati["reminder_status"] == "completed" and dati["reminder_queued"] == 2
    assert esegui(monkeypatch, Sessione({REMINDER: promemoria, DISPATCH: dispatch})) == 0


@pytest.mark.parametrize("guasto", [
    Risposta(500, {"detail": "boom"}),
    Risposta(403, {"detail": "Forbidden"}),
    Risposta(200, None),
    Risposta(200, {"scanned": 1}),
    Risposta(200, {**ZERO_REMINDER, "errors": -1}),
    requests.Timeout("lento"),
    requests.ConnectionError("giu'"),
    RuntimeError("imprevisto"),
])
def test_C3_giro_promemoria_fallito_dispatch_parte_uscita_2(monkeypatch, guasto):
    s = Sessione({REMINDER: guasto})
    assert esegui(monkeypatch, s) == 2
    assert s.fasi() == ["login", "journeys", "reminders", "dispatch", "logout"]
    dati = runner.run_once(config(), sessione=Sessione({REMINDER: guasto}))
    assert dati["reminder_status"] == "failed"


def test_C4_errori_per_candidato_dispatch_parte_uscita_2(monkeypatch):
    s = Sessione({REMINDER: Risposta(200, {**ZERO_REMINDER, "scanned": 2, "errors": 1})})
    assert esegui(monkeypatch, s) == 2
    assert "dispatch" in s.fasi()


def test_C5_feature_not_migrated_dispatch_parte_nessun_exit_1(monkeypatch, capsys):
    non_migrata = Risposta(503, {"detail": {"code": "feature_not_migrated", "message": "079"}})
    s = Sessione({REMINDER: non_migrata})
    assert esegui(monkeypatch, s) == 0
    assert s.fasi() == ["login", "journeys", "reminders", "dispatch", "logout"]
    dati = runner.run_once(config(), sessione=Sessione({REMINDER: non_migrata}))
    assert dati["reminder_status"] == "not_migrated"
    assert "phase=reminder_tick" in capsys.readouterr().out


def test_C5b_un_503_qualunque_e_un_guasto(monkeypatch):
    s = Sessione({REMINDER: Risposta(503, {"detail": {"code": "altro"}})})
    assert esegui(monkeypatch, s) == 2


def test_C6_canale_diverso_da_email_nessun_giro_promemoria(monkeypatch):
    s = Sessione()
    dati = runner.run_once(config(channel="whatsapp"), sessione=s)
    assert s.fasi() == ["login", "journeys", "dispatch", "logout"]
    assert dati["reminder_status"] == "skipped" and "reminder_errors" not in dati
    s2 = Sessione()
    assert esegui(monkeypatch, s2, channel="whatsapp") == 0
    assert all(REMINDER not in url for url, _ in s2.chiamate)


def test_C7_logout_sempre(monkeypatch):
    for copione in ({REMINDER: RuntimeError("x")}, {DISPATCH: requests.Timeout("t")},
                    {DISPATCH: Risposta(500, None)}, {REMINDER: Risposta(500, None),
                                                      DISPATCH: Risposta(500, None)}):
        s = Sessione(copione)
        esegui(monkeypatch, s)
        assert s.fasi()[-1] == "logout"


def test_C7b_dispatch_rotto_resta_exit_1(monkeypatch):
    s = Sessione({DISPATCH: requests.ConnectionError("giu'")})
    assert esegui(monkeypatch, s) == 1


def test_C8_log_solo_conteggi_niente_segreti_niente_destinatari(monkeypatch, capsys):
    s = Sessione({REMINDER: Risposta(200, {**ZERO_REMINDER, "scanned": 1, "due": 1,
                                           "queued": 1,
                                           "skipped_by_reason": {"contact_archived": 0},
                                           "email": "mario@example.it"}),
                  LOGIN: Risposta(204, None)})
    esegui(monkeypatch, s)
    fuori = capsys.readouterr().out
    for vietato in ("segreto-da-non-stampare", "cron@example.it", "mario@example.it",
                    "cookie", "Cookie", "@"):
        assert vietato not in fuori
    righe = [r for r in fuori.splitlines() if "phase=reminder_tick" in r]
    assert len(righe) == 1
    campi = dict(p.split("=", 1) for p in righe[0].split())
    assert set(campi) == {"status", "phase", "scanned", "due", "queued", "queued_idempotent",
                          "not_due", "ineligible", "errors", "duration_ms"}


def test_C9_il_giro_delle_journey_e_invariato(monkeypatch):
    visti = []

    class Osservata(Sessione):
        def post(self, url, **kwargs):
            if JOURNEY in url:
                visti.append(kwargs.get("json"))
            return super().post(url, **kwargs)

    runner.run_once(config(journey_limit=42), sessione=Osservata())
    assert visti == [{"limit": 42}]
    # un guasto delle journey resta exit 2 e non tocca i promemoria ne' il dispatch
    s = Sessione({JOURNEY: Risposta(500, None)})
    assert esegui(monkeypatch, s) == 2
    assert s.fasi() == ["login", "journeys", "reminders", "dispatch", "logout"]
    # e la journey not_migrated resta un non-guasto
    s = Sessione({JOURNEY: Risposta(503, {"detail": {"code": "feature_not_migrated"}})})
    assert esegui(monkeypatch, s) == 0


def test_C9b_i_guasti_del_dispatch_restano_exit_2(monkeypatch):
    for chiave in ("failed", "indeterminate", "lost"):
        s = Sessione({DISPATCH: Risposta(200, {**ZERO_DISPATCH, chiave: 1})})
        assert esegui(monkeypatch, s) == 2


def test_C9c_i_predicati():
    assert not runner._reminder_failure({})
    assert not runner._reminder_failure({"reminder_status": "skipped"})
    assert not runner._reminder_failure({"reminder_status": "not_migrated"})
    assert not runner._reminder_failure({"reminder_status": "completed", "reminder_errors": 0})
    assert runner._reminder_failure({"reminder_status": "failed"})
    assert runner._reminder_failure({"reminder_status": "completed", "reminder_errors": 3})
    assert runner._application_failure({**ZERO_DISPATCH, "reminder_status": "failed"})
