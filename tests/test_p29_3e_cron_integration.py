"""P29-3E senza database: il CONTRATTO del giro di cron con il tick dentro.

IL GUASTO CHE QUESTO FILE ESISTE PER IMPEDIRE

Il motore delle journey e' codice nuovo; il dispatch delle email e' codice che
funziona da settimane e che manda anche i messaggi scritti a mano da una
persona. Collegare il primo davanti al secondo, nello stesso giro, crea una
possibilita' che prima non c'era: che un bug nel motore tolga all'agenzia la
capacita' di spedire cio' che e' GIA' in coda. Un `raise` nel tick, un 500
della rotta, un timeout - e la posta si ferma per una ragione che con la posta
non c'entra niente.

Quindi la regola, e quasi tutti i test qui sotto la misurano da un lato o
dall'altro: QUALUNQUE COSA FACCIA IL TICK, IL DISPATCH PARTE. Non in silenzio -
il giro finisce 2 e lo dice nel log - ma parte.

L'ALTRO GUASTO: UN CRON CHE ACCENDE UNA SEQUENZA COMMERCIALE

Il collegamento al motore e l'accensione della sequenza sono due cose diverse,
e solo la prima appartiene a un cron. `provision` e `activate` restano gesti
che fa una persona, una volta, guardando cosa sta per succedere.

Niente PostgreSQL qui: si misura il contratto del runner con una sessione
finta. La catena vera - stima, tick, M1 in coda, dispatch, email - e' in
`test_p29_3e_cron_integration_postgres.py`.
"""
from __future__ import annotations

import hashlib
import importlib
import re
import subprocess
from pathlib import Path

import pytest

requests = pytest.importorskip("requests")

runner = importlib.import_module("run_communication_dispatch_cron")

ROOT = Path(__file__).resolve().parents[1]

LOGIN = "/api/operator-auth/login"
LOGOUT = "/api/operator-auth/logout"
TICK = "/api/communication/journeys/tick"
DISPATCH = "/api/communication/dispatch"


# ---------------------------------------------------------------------------
# Gli attrezzi
# ---------------------------------------------------------------------------

ZERO_DISPATCH = {"claimed": 0, "sent": 0, "suppressed": 0, "failed": 0,
                 "indeterminate": 0, "lost": 0}

ZERO_TICK = {"stopped": 0, "advanced": 0, "completed": 0, "enrolled_active": 0,
             "enrolled_stopped": 0, "enrolled_skipped": 0, "queued": 0,
             "queued_idempotent": 0, "awaiting_operator": 0, "errors": 0}


def conteggi(**cambiamenti) -> dict:
    return {**ZERO_DISPATCH, **cambiamenti}


def conteggi_tick(**cambiamenti) -> dict:
    return {**ZERO_TICK, **cambiamenti}


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
    """Risponde secondo un copione e ANNOTA l'ordine delle chiamate.

    Il default per ogni pezzo e' la risposta buona: un test che voglia
    provare un solo guasto ne descrive uno solo, e tutto il resto funziona -
    cosi' cio' che fallisce nel test e' cio' che il test dice di provare.
    """

    def __init__(self, copione=None):
        self.copione = copione or {}
        self.chiamate: list[str] = []

    def post(self, url, **kwargs):
        self.chiamate.append(url)
        for pezzo, esito in self.copione.items():
            if pezzo in url:
                if isinstance(esito, Exception):
                    raise esito
                if callable(esito):
                    return esito()
                return esito
        if TICK in url:
            return Risposta(200, conteggi_tick())
        if DISPATCH in url:
            return Risposta(200, conteggi())
        return Risposta(204, None)

    def fasi(self) -> list[str]:
        """Le chiamate ridotte al loro nome, nell'ordine in cui sono state
        fatte. E' l'ordine che questa fase deve garantire."""
        nomi = []
        for url in self.chiamate:
            for pezzo, nome in ((LOGIN, "login"), (LOGOUT, "logout"),
                                (TICK, "tick"), (DISPATCH, "dispatch")):
                if pezzo in url:
                    nomi.append(nome)
                    break
            else:  # pragma: no cover - una rotta che nessuno ha previsto
                nomi.append(url)
        return nomi


def config(**cambiamenti):
    base = dict(base_url="https://esempio.it", email="cron@example.it",
                password="x", channel="email", limit=10, timeout=(5.0, 60.0),
                journey_limit=500)
    return runner.Config(**{**base, **cambiamenti})


def righe_log(capsys) -> list[dict]:
    """Le righe stampate, ciascuna come dizionario `nome -> valore`."""
    fuori = []
    for riga in capsys.readouterr().out.splitlines():
        if not riga.strip():
            continue
        fuori.append(dict(p.split("=", 1) for p in riga.split() if "=" in p))
    return fuori


# ===========================================================================
# A - L'ORDINE
# ===========================================================================

def test_01_l_ordine_e_login_tick_dispatch_logout():
    """Il tick PRIMA del dispatch, e il logout dopo tutto.

    L'ordine e' la ragione per cui questa fase esiste: cio' che il motore
    mette in coda deve poter partire in questo stesso giro. Invertirli
    funzionerebbe lo stesso - e M1 aspetterebbe un'ora in piu' per niente.
    """
    sessione = Sessione()
    runner.run_once(config(), sessione=sessione)
    assert sessione.fasi() == ["login", "tick", "dispatch", "logout"]


def test_02_il_tick_e_una_chiamata_sola_e_porta_il_suo_limite():
    """Nessun secondo giro, nessun ritentativo: un tick e' una scrittura, e
    la ripetizione e' del cron."""
    visti = []

    class Osservata(Sessione):
        def post(self, url, **kwargs):
            if TICK in url:
                visti.append(kwargs.get("json"))
            return super().post(url, **kwargs)

    runner.run_once(config(journey_limit=42), sessione=Osservata())
    assert visti == [{"limit": 42}]


def test_03_il_tick_non_porta_l_agenzia_e_non_porta_un_bersaglio():
    """Lo scope viene dalla sessione, come ovunque in P29. Un corpo che
    nominasse un'agenzia o un contatto sarebbe un giro pilotato da fuori."""
    corpi = []

    class Osservata(Sessione):
        def post(self, url, **kwargs):
            if TICK in url:
                corpi.append(kwargs.get("json") or {})
            return super().post(url, **kwargs)

    runner.run_once(config(), sessione=Osservata())
    assert corpi == [{"limit": 500}]


# ===========================================================================
# B/C - IL GIRO NORMALE
# ===========================================================================

def test_04_cio_che_il_tick_accoda_arriva_nella_risposta_del_giro():
    """Il giro dice quante righe il motore ha messo in coda: senza, chi
    guarda un log non sa distinguere "non c'era niente da fare" da "il
    motore non ha fatto niente"."""
    sessione = Sessione({TICK: Risposta(200, conteggi_tick(queued=3, advanced=3)),
                         DISPATCH: Risposta(200, conteggi(claimed=3, sent=3))})
    dati = runner.run_once(config(), sessione=sessione)

    assert dati["sent"] == 3
    assert dati["journey_status"] == "completed"
    assert dati["journey_queued"] == 3
    assert dati["journey_errors"] == 0
    assert runner._application_failure(dati) is False


def test_05_un_tick_senza_lavoro_non_cambia_niente():
    """Il caso normale di un cron che gira ogni ora: zero ovunque, exit 0."""
    sessione = Sessione()
    dati = runner.run_once(config(), sessione=sessione)

    assert sessione.fasi() == ["login", "tick", "dispatch", "logout"]
    assert dati["journey_status"] == "completed" and dati["journey_queued"] == 0
    assert runner._application_failure(dati) is False


def test_06_i_conteggi_del_dispatch_restano_quelli_di_prima():
    """Il contratto che i cruscotti leggono non cambia: le chiavi nuove si
    AGGIUNGONO, e nessuna delle vecchie sparisce o cambia nome."""
    dati = runner.run_once(config(), sessione=Sessione())
    for chiave in runner.CONTEGGI:
        assert chiave in dati, chiave
    assert set(dati) - set(runner.CONTEGGI) == {
        "journey_status", "journey_queued", "journey_errors"}


# ===========================================================================
# D - LA 071 CHE NON C'E'
# ===========================================================================

NON_MIGRATA = Risposta(503, {"detail": {"code": "feature_not_migrated",
                                        "message": "la 071 non e' applicata"}})


def test_07_senza_la_071_il_dispatch_continua():
    """Il codice puo' arrivare in un ambiente prima della migration. In
    quella finestra il cron deve continuare a spedire: i messaggi in coda
    esistono dalla 064 e non hanno niente a che fare con le journey."""
    sessione = Sessione({TICK: NON_MIGRATA,
                         DISPATCH: Risposta(200, conteggi(claimed=1, sent=1))})
    dati = runner.run_once(config(), sessione=sessione)

    assert sessione.fasi() == ["login", "tick", "dispatch", "logout"]
    assert dati["sent"] == 1
    assert dati["journey_status"] == "not_migrated"


def test_08_senza_la_071_il_giro_e_verde_e_il_log_lo_dice_con_una_parola(capsys):
    """Non e' un guasto: e' uno stato previsto. Ma deve essere CERCABILE -
    applicata la migration, questa riga non deve comparire mai piu'.
    """
    dati = runner.run_once(config(), sessione=Sessione({TICK: NON_MIGRATA}))
    assert runner._application_failure(dati) is False

    prima, seconda = righe_log(capsys)
    assert prima["status"] == "skipped" and prima["phase"] == "journey_tick"
    assert prima["reason"] == "feature_not_migrated"
    assert seconda["status"] == "completed" and "phase" not in seconda


def test_09_un_503_che_NON_e_la_migration_e_un_guasto():
    """Un load balancer che risponde 503 con una pagina HTML non sta dicendo
    "questa funzione non e' migrata": sta dicendo che il servizio e' giu', e
    trattarlo come uno stato previsto nasconderebbe un guasto vero."""
    for corpo in ({"detail": "Service Unavailable"}, {"detail": {"code": "altro"}},
                  None, {}):
        sessione = Sessione({TICK: Risposta(503, corpo)})
        dati = runner.run_once(config(), sessione=sessione)
        assert dati["journey_status"] == "failed", corpo
        assert runner._application_failure(dati) is True, corpo
        assert "dispatch" in sessione.fasi(), corpo


# ===========================================================================
# E - IL TICK CHE SI ROMPE
# ===========================================================================

@pytest.mark.parametrize("guasto, motivo", [
    (Risposta(500, None), "http_500"),
    (Risposta(502, None), "http_502"),
    (Risposta(403, None), "http_403"),
    (requests.Timeout("scaduto"), "timeout"),
    (requests.ConnectionError("giu'"), "http_or_network"),
    (Risposta(200, {"queued": 1}), "invalid_json"),
    (Risposta(200, None), "invalid_json"),
    (Risposta(200, [1, 2, 3]), "invalid_json"),
])
def test_10_qualunque_guasto_del_tick_lascia_partire_il_dispatch(guasto, motivo, capsys):
    sessione = Sessione({TICK: guasto,
                         DISPATCH: Risposta(200, conteggi(claimed=2, sent=2))})
    dati = runner.run_once(config(), sessione=sessione)

    assert sessione.fasi() == ["login", "tick", "dispatch", "logout"]
    assert dati["sent"] == 2, "il dispatch non e' partito"
    assert dati["journey_status"] == "failed"
    assert "journey_queued" not in dati, "conteggi inventati da una risposta che non c'e'"

    riga = righe_log(capsys)[0]
    assert riga["status"] == "failed" and riga["phase"] == "journey_tick"
    assert riga["reason"] == motivo


def test_11_un_guasto_imprevisto_del_trasporto_non_ferma_il_giro():
    """Una libreria che alza un'eccezione che `requests` non conosce - e'
    successo davvero, con `httpx` sotto il TestClient - non deve poter
    togliere al giro la capacita' di spedire."""
    class Esotica(Exception):
        pass

    sessione = Sessione({TICK: Esotica("da un'altra libreria")})
    dati = runner.run_once(config(), sessione=sessione)

    assert sessione.fasi() == ["login", "tick", "dispatch", "logout"]
    assert dati["journey_status"] == "failed"


def test_12_un_tick_fallito_non_viene_ritentato_nello_stesso_giro():
    """Un tick e' una SCRITTURA: iscrizioni create, passi accodati. Riprovarlo
    su una risposta persa vorrebbe dire farlo girare due volte. Il motore e'
    idempotente e sopravviverebbe, ma la ripetizione e' del cron."""
    sessione = Sessione({TICK: requests.Timeout("scaduto")})
    runner.run_once(config(), sessione=sessione)
    assert sessione.fasi().count("tick") == 1


def test_13_un_tick_fallito_rende_il_giro_2_e_non_1(monkeypatch):
    """1 vuol dire "non abbiamo dispacciato"; qui abbiamo dispacciato. La
    differenza e' operativa: l'1 si guarda nella piattaforma, il 2 nel
    ledger e nel motore."""
    monkeypatch.setenv("COMMUNICATION_DISPATCH_BASE_URL", "https://esempio.it")
    monkeypatch.setenv("COMMUNICATION_DISPATCH_EMAIL", "cron@example.it")
    monkeypatch.setenv("COMMUNICATION_DISPATCH_PASSWORD", "x")
    sessione = Sessione({TICK: Risposta(500, None)})
    vero = runner.run_once  # prima del monkeypatch, o `main` chiamerebbe se' stesso
    monkeypatch.setattr(runner, "run_once", lambda c, **kw: vero(c, sessione=sessione))
    assert runner.main() == 2


def test_14_un_tick_con_iscrizioni_morte_non_e_un_giro_verde():
    """`errors` conta le iscrizioni che sono morte nel loro savepoint: un
    contatto senza email, un template che non c'e'. Il ledger lo sa, e un
    exit 0 farebbe si' che non lo guardi nessuno - lo stesso ragionamento di
    `failed` per il dispatch."""
    dati = runner.run_once(
        config(), sessione=Sessione({TICK: Risposta(200, conteggi_tick(errors=1,
                                                                      advanced=9))}))
    assert dati["journey_status"] == "completed" and dati["journey_errors"] == 1
    assert runner._application_failure(dati) is True


# ===========================================================================
# F/G - IL LOGIN, IL DISPATCH, IL LOGOUT
# ===========================================================================

def test_15_senza_login_non_si_fa_nemmeno_il_tick():
    """Un tick senza sessione prenderebbe 401, e sarebbe una chiamata inutile
    a un motore che scrive. Il giro finisce prima."""
    sessione = Sessione({LOGIN: Risposta(401, None)})
    with pytest.raises(runner.TechnicalError) as exc:
        runner.run_once(config(), sessione=sessione)

    assert "login" in str(exc.value)
    assert sessione.fasi() == ["login", "logout"]


@pytest.mark.parametrize("guasto", [
    requests.ConnectionError("giu'"), requests.Timeout("scaduto"),
    Risposta(500, None), Risposta(200, {"sent": 1}),
])
def test_16_un_dispatch_che_fallisce_resta_un_guasto_tecnico(guasto):
    """Il tick riuscito non addolcisce un dispatch fallito: il giro e' 1,
    come prima di questa fase."""
    sessione = Sessione({DISPATCH: guasto})
    with pytest.raises(runner.TechnicalError):
        runner.run_once(config(), sessione=sessione)
    assert sessione.fasi() == ["login", "tick", "dispatch", "logout"]


def test_17_il_logout_e_sempre_nel_finally():
    """Anche quando il tick esplode, anche quando il dispatch esplode, anche
    quando va tutto bene. Una sessione abbandonata a ogni giro e' una riga
    viva in piu' ogni volta."""
    riuscito = Sessione()
    runner.run_once(config(), sessione=riuscito)
    assert riuscito.fasi()[-1] == "logout"

    tick_rotto = Sessione({TICK: requests.ConnectionError("giu'")})
    runner.run_once(config(), sessione=tick_rotto)
    assert tick_rotto.fasi()[-1] == "logout"

    dispatch_rotto = Sessione({DISPATCH: requests.ConnectionError("giu'")})
    with pytest.raises(runner.TechnicalError):
        runner.run_once(config(), sessione=dispatch_rotto)
    assert dispatch_rotto.fasi()[-1] == "logout"

    logout_rotto = Sessione({LOGOUT: requests.ConnectionError("giu'")})
    runner.run_once(config(), sessione=logout_rotto)  # non solleva


# ===========================================================================
# H - LA CONFIGURAZIONE
# ===========================================================================

@pytest.fixture
def ambiente(monkeypatch):
    monkeypatch.setenv("COMMUNICATION_DISPATCH_BASE_URL", "https://esempio.it")
    monkeypatch.setenv("COMMUNICATION_DISPATCH_EMAIL", "cron@example.it")
    monkeypatch.setenv("COMMUNICATION_DISPATCH_PASSWORD", "x")
    for avanzo in ("COMMUNICATION_DISPATCH_CHANNEL", "COMMUNICATION_DISPATCH_LIMIT",
                   "COMMUNICATION_JOURNEY_TICK_LIMIT",
                   "COMMUNICATION_CONNECT_TIMEOUT_SECONDS",
                   "COMMUNICATION_READ_TIMEOUT_SECONDS"):
        monkeypatch.delenv(avanzo, raising=False)


def test_18_il_limite_del_tick_ha_un_default_e_non_va_configurato(ambiente):
    assert runner.load_config().journey_limit == 500


@pytest.mark.parametrize("valore", ["0", "2001", "-1", "cinquecento", ""])
def test_19_un_limite_del_tick_fuori_scala_e_un_guasto_di_configurazione(
        ambiente, monkeypatch, valore):
    """Lo schema della rotta accetta 1..2000. Scoprirlo con un 422 a meta'
    giro sarebbe un guasto travestito da guasto del motore."""
    monkeypatch.setenv("COMMUNICATION_JOURNEY_TICK_LIMIT", valore)
    with pytest.raises(runner.ConfigurationError):
        runner.load_config()
    assert runner.main() == 1


def test_20_il_log_non_nomina_credenziali_ne_destinatari(capsys):
    """Due righe adesso, e la regola vale per tutte e due: un log di cron
    finisce in posti che non controlliamo."""
    runner.run_once(config(), sessione=Sessione(
        {TICK: Risposta(200, conteggi_tick(queued=1)),
         DISPATCH: Risposta(200, conteggi(claimed=1, sent=1))}))
    righe = righe_log(capsys)

    assert len(righe) == 2
    for riga in righe:
        assert not {"email", "password", "user", "destination", "to", "cookie",
                    "contact_id", "message_id"} & set(riga)
        assert set(riga) <= ({"status", "phase", "channel", "duration_ms", "reason"}
                             | set(runner.CONTEGGI) | set(runner.CONTEGGI_TICK)), riga
    assert "cron@example.it" not in capsys.readouterr().out


# ===========================================================================
# I - IL CRON NON ACCENDE NIENTE
# ===========================================================================

def _codice_runner() -> str:
    testo = (ROOT / "run_communication_dispatch_cron.py").read_text(encoding="utf-8")
    testo = re.sub(r'"{3}[\s\S]*?"{3}', "", testo)
    return re.sub(r"#[^\n]*", "", testo)


def test_21_il_cron_non_provisiona_e_non_attiva_nessuna_journey():
    """La sequenza commerciale si accende a mano, una volta, da chi risponde
    di cio' che l'agenzia manda. Un cron che la accendesse da solo
    comincerebbe a scrivere ai proprietari senza che nessuno abbia deciso."""
    codice = _codice_runner()
    for vietato in ("stima_lead", "ensure_stima_lead", "provision", "activate",
                    "retire", "journeys/enrollments"):
        assert vietato not in codice, vietato


def test_22_il_cron_chiama_TRE_rotte_e_nessun_altra():
    """Le rotte che il runner tocca si contano dal codice: login, tick,
    dispatch, logout. Una quinta sarebbe una capacita' che nessuno ha
    chiesto a un processo che gira da solo ogni ora."""
    codice = _codice_runner()
    rotte = set(re.findall(r'/api/[a-z0-9\-/{}]+', codice))
    assert rotte == {"/api/operator-auth/login", "/api/operator-auth/logout",
                     "/api/communication/journeys/tick",
                     "/api/communication/dispatch"}, sorted(rotte)


def test_23_il_cron_resta_un_client_HTTP_e_non_importa_il_dominio():
    """Gira altrove, con un database che non e' il suo. Un import del dominio
    lo trasformerebbe in un secondo punto di accesso ai dati."""
    codice = _codice_runner()
    aghi = ("from communication", "import communication", "psycopg2",
            "get_" + "connection", "smtp" + "lib", "invia_" + "mail",
            "providers", "from core", "from consent", "sqlalchemy")
    for ago in aghi:
        assert ago not in codice, ago


def test_24_nessun_secondo_cron_e_nessun_altro_percorso_automatico():
    """Due processi che chiamano lo stesso motore sono due giri concorrenti
    che nessuno ha progettato. Il motore li reggerebbe - `SKIP LOCKED` e
    chiavi di idempotenza - ma nessuno li ha voluti."""
    from tests.p29_3e_diff import RUNNER_TOCCATO

    assert not list(ROOT.glob("run_journey*.py"))
    cron = sorted(p.name for p in ROOT.glob("run_*cron*.py"))
    assert cron == ["run_communication_dispatch_cron.py", "run_flow_p2b_cron.py",
                    "run_followup_p18d_cron.py", "run_owner_home_alert_cron.py",
                    "run_property_watch_valuation_cron.py"], cron

    chiamanti = subprocess.run(
        ["git", "--no-optional-locks", "grep", "-l", "journeys/tick", "--",
         "run_*.py", "scripts/*.py", "static/*", "*.sh"],
        cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert set(chiamanti) <= {RUNNER_TOCCATO}, sorted(chiamanti)


def test_25_nessuno_chiama_il_provisioning_da_un_percorso_automatico():
    """`ensure_stima_lead_v1` lo nominano il catalogo che lo definisce, la
    rotta che lo espone e i test. Nessun runner, nessuno script, nessun
    avvio dell'applicazione."""
    chiamanti = set(subprocess.run(
        ["git", "--no-optional-locks", "grep", "-l", "ensure_stima_lead_v1"],
        cwd=ROOT, capture_output=True, text=True).stdout.split())
    estranei = {c for c in chiamanti
                if not (c.startswith("tests/") or c in (
                    "communication/journey_catalog.py", "communication/router.py"))}
    assert estranei == set(), sorted(estranei)

    principale = (ROOT / "main.py").read_text(encoding="utf-8")
    for vietato in ("ensure_stima_lead", "activate_journey", "journeys/tick"):
        assert vietato not in principale, vietato


def test_26_la_migration_071_non_contiene_nessun_seed():
    migrazione = (ROOT / "migrations" / "071_p29_3_journey_automation.sql").read_text("utf-8")
    for vietato in ("INSERT INTO communication_journeys",
                    "INSERT INTO communication_journey_steps", "stima_lead"):
        assert vietato not in migrazione, vietato


# ===========================================================================
# J - NESSUNA RETE VERA, E IL PERIMETRO DELLA FASE
# ===========================================================================

def test_27_questo_modulo_non_apre_nessuna_connessione_vera():
    """Il runner parla `requests`, ma qui nessuna richiesta esce: la sessione
    e' una finta, e il modulo non importa ne' un trasporto ne' un provider."""
    import sys
    modulo = sys.modules[__name__]
    sorgente = Path(modulo.__file__).read_text(encoding="utf-8")
    # Gli aghi si compongono a runtime: scritti per esteso, questo file
    # risulterebbe esso stesso l'infrazione che sta cercando.
    aghi = ("smtp" + "lib", "http." + "client", "url" + "open",
            "requests" + ".post(", "requests" + ".get(")
    for ago in aghi:
        assert ago not in sorgente, ago
    assert "import " + "socket" not in sorgente


def test_28_i_file_toccati_sono_quelli_dichiarati_e_P29_2_0_resta_fuori():
    from tests.p29_3e_diff import FILE_MODIFICATI, FILE_NUOVI
    # P29-3G si dichiara allo stesso modo: l'unione cresce di una fase.
    from tests.p29_3g_diff import FILE_MODIFICATI as MOD_3G, FILE_NUOVI as NUOVI_3G
    # FLOW GLOBAL SECURITY si dichiara allo stesso modo: l'unione cresce
    # di una fase, il verso del controllo no. Non e' una fase di P29 - e'
    # il catalogo globale di FLOW - ma questa sentinella guarda il working
    # tree intero, quindi la collisione c'e' e va nominata.
    from tests.flow_global_security_diff import (
        FILE_MODIFICATI as MOD_FGS, FILE_NUOVI as NUOVI_FGS)
    # A30-1 si dichiara allo stesso modo: l'unione cresce di una fase, il
    # verso del controllo no. Non e' una fase di P29 - e' l'Agenda CRM - ma
    # questa sentinella guarda il working tree intero.
    # SENTINELLA AGGIORNATA DA A30-2: l'Agenda si dichiara in due file
    # (A30-1 + A30-2), letti come un'unica fase.
    from tests.a30_1_diff import FILE_MODIFICATI as MOD_A30_1, FILE_NUOVI as NUOVI_A30_1
    from tests.a30_2_diff import FILE_MODIFICATI as MOD_A30_2, FILE_NUOVI as NUOVI_A30_2
    # SENTINELLA AGGIORNATA DA A30-2P: terza dichiarazione dell'Agenda.
    from tests.a30_2p_diff import FILE_MODIFICATI as MOD_A30_2P, FILE_NUOVI as NUOVI_A30_2P
    # SENTINELLA AGGIORNATA DAL MOUNT A30: quarta dichiarazione dell'Agenda.
    from tests.a30_mount_diff import FILE_MODIFICATI as MOD_A30_M, FILE_NUOVI as NUOVI_A30_M
    # A30-4: la UI Agenda (OS Shell), sesta dichiarazione.
    from tests.a30_4_diff import FILE_MODIFICATI as MOD_A30_4, FILE_NUOVI as NUOVI_A30_4
    # SENTINELLA AGGIORNATA DA A30-5: settima dichiarazione dell'Agenda.
    from tests.a30_5_diff import FILE_MODIFICATI as MOD_A30_5, FILE_NUOVI as NUOVI_A30_5
    NUOVI_A30 = NUOVI_A30_1 | NUOVI_A30_2 | NUOVI_A30_2P | NUOVI_A30_M | NUOVI_A30_4 | NUOVI_A30_5
    MOD_A30 = MOD_A30_1 | MOD_A30_2 | MOD_A30_2P | MOD_A30_M | MOD_A30_4 | MOD_A30_5

    def _git(*argomenti):
        return subprocess.run(["git", "--no-optional-locks", *argomenti],
                              cwd=ROOT, capture_output=True, text=True).stdout

    righe = _git("status", "--porcelain").splitlines()
    nuovi = {r[3:].strip() for r in righe if r[:2].strip() in ("??", "A")}
    modificati = {r[3:].strip() for r in righe if r[:2].strip() not in ("??", "A")}
    tracciati = set(_git("ls-files").split())

    tutti_nuovi = FILE_NUOVI | NUOVI_3G | NUOVI_FGS | NUOVI_A30
    tutti_modificati = FILE_MODIFICATI | MOD_3G | MOD_FGS | MOD_A30 | tutti_nuovi
    assert nuovi - tutti_nuovi == {"P29_2_0_COMMUNICATION_DESIGN.md"}, \
        sorted(nuovi - tutti_nuovi)
    assert modificati <= tutti_modificati, \
        sorted(modificati - tutti_modificati)
    # NOTA DI P29-3G: dopo il commit di P29-3E i suoi file non sono piu' nel
    # working tree, e la garanzia si sposta su cio' che resta vero per
    # sempre: ogni file dichiarato esiste ed e' nell'indice.
    for nome in FILE_NUOVI:
        assert (ROOT / nome).exists(), nome
        assert nome in tracciati, nome
    for nome in FILE_MODIFICATI:
        assert nome in tracciati, nome
    assert "P29_2_0_COMMUNICATION_DESIGN.md" not in tracciati


def test_29_il_documento_di_design_non_e_stato_toccato():
    percorso = ROOT / "P29_2_0_COMMUNICATION_DESIGN.md"
    assert hashlib.md5(percorso.read_bytes()).hexdigest() == \
        "37b3066fc5cf41b905964e1f31fe93f4"


def test_30_il_piano_di_certificazione_esiste_e_non_e_uno_script():
    """La procedura per TEST e' scritta perche' la esegua una PERSONA, un
    passo per volta, guardando cosa succede. Uno script la renderebbe una
    cosa che si lancia e si dimentica - e il punto della certificazione e'
    proprio che qualcuno guardi."""
    piano = (ROOT / "docs" / "P29_3E_TEST_CERTIFICATION.md")
    assert piano.exists()
    testo = piano.read_text(encoding="utf-8")
    for passo in ("provision", "activate", "stima", "tick", "M1", "dispatch",
                  "Pause", "M3", "Contact 360", "isiscrizione", "cron",
                  "stop", "provenienza"):
        assert passo in testo, passo
    assert not list((ROOT / "scripts").glob("*3e*"))
    assert "PROD" in testo
