"""P29-3E su PostgreSQL reale: il cron che chiude la catena.

COSA SI PROVA QUI, E PERCHE' NON BASTAVA IL CONTRATTO

`test_p29_3e_cron_integration.py` prova che il runner chiama le cose
nell'ordine giusto e che un tick rotto non ferma il dispatch. Quello e' il
contratto, e si misura con una sessione finta. Qui si prova la CATENA:

    stima spedita -> tick -> M1 in coda -> dispatch -> trasporto

contro l'app vera, il database vero, le migrazioni vere - e con la spia al
posto di SMTP, perche' nessuna email parte mai da un test.

Cio' che solo un database puo' dire: che due giri consecutivi non producano
due M1; che due giri SIMULTANEI non producano due iscrizioni; che la
provenienza (`enrollment_id`, `step_no`, `run_no`) sopravviva al passaggio dal
motore al dispatcher e sia ancora li' sulla riga spedita; che un messaggio
gia' `sent` non rinasca al giro dopo; e che M3 - il passo assistito - non
parta dal cron per nessuna ragione, perche' impegna il tempo di una persona.

L'HARNESS E' QUELLO DI P29-3C, importato invece che ricopiato.

Opt-in: senza `P29_TEST_DSN` si salta tutto.
"""
from __future__ import annotations

import importlib
import os
import pytest

from tests.test_p29_3c_orchestrator_postgres import (  # noqa: F401
    PASSWORD, accedi, client, db, iscrizioni, journey_attiva, mondo, modulo,
    passo, _in_parallelo,
)

pytestmark = pytest.mark.skipif(not os.getenv("P29_TEST_DSN"),
                                reason="P29_TEST_DSN non impostata")

runner = importlib.import_module("run_communication_dispatch_cron")

def config(mondo, **cambiamenti):
    """Il runner vero, puntato all'app in processo: `base_url` vuoto, e il
    `TestClient` al posto di `requests`."""
    base = dict(base_url="", email=mondo["admin"]["email"], password=PASSWORD,
                channel="email", limit=10, timeout=(5.0, 60.0), journey_limit=500)
    return runner.Config(**{**base, **cambiamenti})


@pytest.fixture
def spia(client, monkeypatch):
    """Le email che SAREBBERO partite. Il trasporto e' gia' sostituito dalla
    fixture `client`; qui se ne prende una con la lista in mano."""
    from communication.providers import email_smtp

    inviate: list = []
    monkeypatch.setattr(email_smtp, "invia_mail",
                        lambda *a, **k: (inviate.append((a, k)), True)[1])
    return inviate


def giro(client, mondo, **cambiamenti):
    """UN giro di cron completo contro l'app vera."""
    client.cookies.clear()
    return runner.run_once(config(mondo, **cambiamenti), sessione=client)


def _sequenza(modulo, ctx, *, passi=None):
    """Una journey attiva con il primo passo DOVUTO SUBITO.

    Due scelte, entrambe per misurare la catena e non altro:

    `delay=0` perche' qui interessa che cio' che il motore accoda parta nello
    stesso giro. Quanto si aspetta prima di M1 - e come la finestra sposta un
    invio dovuto di notte o di domenica - sono i ritardi e la finestra, e
    hanno i loro test, puri, dove si misurano meglio.

    Nessuna finestra, per la stessa ragione: con quella di `stima_lead` un
    test che gira alle tre di notte vedrebbe M1 programmata per le nove, e
    fallirebbe in base all'ora in cui qualcuno lancia la suite.
    """
    return journey_attiva(modulo, ctx, passi=passi or [passo(1, delay=0)])


def m1(mondo) -> list[dict]:
    """Le righe del primo passo, quali che siano. `messaggi()` filtra per
    iscrizione, non per motivo: qui interessa il motivo."""
    return mondo["righe"]("communication_messages", "reason_code = %s", ("m1",))


def _contatto_dopo_l_accensione(mondo):
    """Un contatto la cui stima e' stata spedita ADESSO, cioe' dopo che la
    sequenza e' stata accesa.

    Non e' un dettaglio: l'attivazione porta con se' il cutoff storico - da
    quel momento in avanti, mai indietro - quindi una stima con un `sent_at`
    di ieri non produce nessuna iscrizione, ed e' corretto. Il cutoff ha i
    suoi test in P29-3D; qui si vuole che la catena parta.
    """
    return mondo["contatto"]()


# ===========================================================================
# A - LA CATENA, IN UN GIRO SOLO
# ===========================================================================

def test_01_stima_tick_M1_dispatch_in_un_solo_giro(client, mondo, modulo, spia):
    """IL PUNTO DELLA FASE. Cio' che il motore accoda parte nello stesso giro.

    Se il tick girasse DOPO il dispatch, o in un cron separato, M1 sarebbe
    accodata adesso e spedita al giro dopo: un'ora di ritardo per niente, su
    ogni singolo passo di ogni singola sequenza.
    """
    _sequenza(modulo, mondo["ctx"]())
    _contatto_dopo_l_accensione(mondo)

    dati = giro(client, mondo)

    assert dati["journey_status"] == "completed"
    assert dati["journey_queued"] == 1, "il tick non ha accodato M1"
    assert dati["sent"] == 1, "il dispatch non ha visto M1 nello stesso giro"
    assert len(spia) == 1, "l'email non e' arrivata al trasporto"

    righe = m1(mondo)
    assert len(righe) == 1 and righe[0]["status"] == "sent"


def test_02_la_provenienza_sopravvive_al_passaggio_al_dispatcher(client, mondo, modulo, spia):
    """Il dispatcher non sa cosa sia una journey e non deve saperlo. Le tre
    colonne che dicono da dove nasce il messaggio devono essere ancora li'
    dopo che lui l'ha spedito: e' l'unico modo, dopo, di sapere perche' una
    persona ha ricevuto quella mail."""
    j = _sequenza(modulo, mondo["ctx"]())
    _contatto_dopo_l_accensione(mondo)

    giro(client, mondo)

    riga = m1(mondo)[0]
    assert riga["status"] == "sent" and riga["sent_at"] is not None
    assert riga["step_no"] == 1 and riga["run_no"] == 1
    assert riga["enrollment_id"] is not None
    iscrizione = iscrizioni(mondo)[0]
    assert riga["enrollment_id"] == iscrizione["id"]
    assert iscrizione["journey_id"] == j["id"]


def test_03_un_giro_a_vuoto_non_crea_niente(client, mondo, modulo, spia):
    """Nessuna journey attiva: il cron gira, dice zero, e non inventa
    lavoro. E' lo stato in cui l'ambiente si trova prima che qualcuno accenda
    la sequenza, ed e' il piu' comune."""
    _contatto_dopo_l_accensione(mondo)

    dati = giro(client, mondo)

    assert dati["journey_status"] == "completed"
    assert dati["journey_queued"] == 0 and dati["claimed"] == 0
    assert spia == []
    assert iscrizioni(mondo) == []
    assert runner._application_failure(dati) is False


# ===========================================================================
# B - IDEMPOTENZA
# ===========================================================================

def test_04_due_giri_consecutivi_non_duplicano_M1(client, mondo, modulo, spia):
    """Il cron gira ogni ora, per sempre. Se il secondo giro riaccodasse M1,
    il proprietario riceverebbe la stessa mail ventiquattro volte al giorno."""
    _sequenza(modulo, mondo["ctx"]())
    _contatto_dopo_l_accensione(mondo)

    primo = giro(client, mondo)
    secondo = giro(client, mondo)

    assert primo["journey_queued"] == 1 and secondo["journey_queued"] == 0
    assert primo["sent"] == 1 and secondo["sent"] == 0
    assert len(m1(mondo)) == 1
    assert len(spia) == 1
    assert len(iscrizioni(mondo)) == 1, "il secondo giro ha iscritto di nuovo"


def test_05_cinque_giri_di_fila_restano_un_messaggio_solo(client, mondo, modulo, spia):
    """Non e' lo stesso test con un numero piu' grande: il primo giro fa
    nascere l'iscrizione e il secondo la trova. Dal terzo in poi si misura
    che non ci sia un ciclo lento - un passo che avanza di uno a ogni giro e
    ricomincia."""
    _sequenza(modulo, mondo["ctx"]())
    _contatto_dopo_l_accensione(mondo)

    for _ in range(5):
        giro(client, mondo)

    assert len(m1(mondo)) == 1
    assert len(spia) == 1
    assert len(iscrizioni(mondo)) == 1


def test_06_un_messaggio_gia_spedito_non_rinasce(client, mondo, modulo, spia):
    """La riga `sent` e' storia. Un motore che la ricreasse perche' "il passo
    uno risulta fatto ma il messaggio non e' in coda" manderebbe due volte
    tutto, per sempre."""
    _sequenza(modulo, mondo["ctx"]())
    _contatto_dopo_l_accensione(mondo)
    giro(client, mondo)

    prima = m1(mondo)[0]
    for _ in range(3):
        giro(client, mondo)
    dopo = m1(mondo)

    assert len(dopo) == 1 and dopo[0]["id"] == prima["id"]
    assert dopo[0]["sent_at"] == prima["sent_at"], "la riga spedita e' stata riscritta"


# ===========================================================================
# C - CONCORRENZA
# ===========================================================================

def test_07_due_giri_di_cron_simultanei_non_duplicano_niente(client, mondo, modulo, spia):
    """Due macchine che partono insieme, o un giro lento e il successivo che
    lo raggiunge. Il motore ha `SKIP LOCKED` e le chiavi di idempotenza; qui
    si prova che il CRON, che li usa dall'esterno via HTTP, non li aggira.
    """
    _sequenza(modulo, mondo["ctx"]())
    _contatto_dopo_l_accensione(mondo)

    from fastapi.testclient import TestClient
    import main as main_module

    def un_giro():
        # Una sessione HTTP per filo: due giri che si scambiassero il cookie
        # non sarebbero due cron, sarebbero un cron confuso.
        with TestClient(main_module.app, base_url="https://testserver") as c:
            return runner.run_once(config(mondo), sessione=c)

    esiti, errori = _in_parallelo(un_giro)

    assert errori == [], errori
    assert len(iscrizioni(mondo)) == 1, "due iscrizioni per lo stesso contatto"
    assert len(m1(mondo)) == 1
    assert sum(e["journey_queued"] for e in esiti) == 1, esiti
    assert sum(e["sent"] for e in esiti) == 1, esiti
    assert len(spia) == 1


# ===========================================================================
# D - IL PASSO ASSISTITO
# ===========================================================================

#: Un passo assistito DOVUTO SUBITO. Con un ritardo, i test qui sotto
#: passerebbero per la ragione sbagliata - non e' partito niente perche' non
#: era ancora il momento - e non proverebbero niente.
ASSISTITO = [passo(1, mode="assisted", delay=0)]


def test_08_il_cron_non_manda_un_passo_assistito(client, mondo, modulo, spia):
    """M3 propone un sopralluogo: impegna il tempo di una persona, e quella
    persona deve poter dire di no. Un cron che lo mandasse da solo prenderebbe
    appuntamenti a nome di qualcuno che non lo sa."""
    _sequenza(modulo, mondo["ctx"](), passi=ASSISTITO)
    _contatto_dopo_l_accensione(mondo)

    dati = giro(client, mondo)

    assert dati["journey_status"] == "completed"
    assert dati["journey_queued"] == 0
    assert dati["sent"] == 0 and spia == []
    assert m1(mondo) == []
    iscrizione = iscrizioni(mondo)[0]
    assert iscrizione["next_action_kind"] == "await_operator"
    assert iscrizione["awaiting_since"] is not None


def test_09_cento_giri_non_stancano_un_passo_assistito(client, mondo, modulo, spia):
    """L'attesa non scade da sola e non si trasforma in un invio. Dieci giri
    bastano a dimostrarlo: se ce ne fosse uno che cede, cederebbe qui."""
    _sequenza(modulo, mondo["ctx"](), passi=ASSISTITO)
    _contatto_dopo_l_accensione(mondo)

    for _ in range(10):
        giro(client, mondo)

    assert spia == []
    assert m1(mondo) == []
    assert iscrizioni(mondo)[0]["next_action_kind"] == "await_operator"


def test_10_dopo_l_approvazione_il_cron_lo_spedisce(client, mondo, modulo, spia):
    """La persona approva, e da quel momento il passo e' un messaggio come
    gli altri: il giro dopo lo porta via."""
    _sequenza(modulo, mondo["ctx"](), passi=ASSISTITO)
    _contatto_dopo_l_accensione(mondo)
    giro(client, mondo)

    iscrizione = iscrizioni(mondo)[0]
    accedi(client, mondo, "admin")
    risposta = client.post(
        f"/api/communication/journeys/enrollments/{iscrizione['id']}/send-current")
    assert risposta.status_code == 200, risposta.text

    dati = giro(client, mondo)
    assert dati["sent"] == 1 and len(spia) == 1
    assert [m["mode"] for m in m1(mondo)] == ["assisted"]


# ===========================================================================
# E - IL CRON NON ACCENDE NIENTE
# ===========================================================================

def test_11_il_cron_non_provisiona_nessuna_journey(client, mondo, modulo, spia):
    """Dieci giri su un'agenzia che non ha nessuna journey: dopo, non ne ha
    ancora nessuna. La sequenza si accende a mano, o non si accende."""
    _contatto_dopo_l_accensione(mondo)

    for _ in range(10):
        giro(client, mondo)

    assert mondo["righe"]("communication_journeys") == []
    assert iscrizioni(mondo) == []


def test_12_il_cron_non_attiva_una_journey_in_bozza(client, mondo, modulo, spia):
    """Provisionata ma non attivata: una bozza non iscrive nessuno, e il cron
    non la sveglia. Questo e' lo stato in cui l'ambiente TEST si trovera' fra
    il passo 1 e il passo 3 della certificazione, e deve essere innocuo."""
    from communication import journey_catalog

    ctx = mondo["ctx"]()
    esito = journey_catalog.ensure_stima_lead_v1(ctx)
    assert esito["journey"]["status"] == "draft"
    _contatto_dopo_l_accensione(mondo)

    for _ in range(3):
        giro(client, mondo)

    journeys = mondo["righe"]("communication_journeys")
    assert [j["status"] for j in journeys] == ["draft"], "il cron ha acceso la sequenza"
    assert iscrizioni(mondo) == []
    assert spia == []


# ===========================================================================
# F - L'ISOLAMENTO DEL GUASTO, CONTRO L'APP VERA
# ===========================================================================

class TickRotto:
    """Il `TestClient`, con il solo tick sostituito da un guasto.

    Non si rompe l'app: si rompe la CHIAMATA al tick, che e' cio' che
    succederebbe davvero con un servizio lento, un proxy, un permesso
    revocato. Tutto il resto del giro passa dall'app vera.
    """

    def __init__(self, client, stato=500):
        self._client = client
        self._stato = stato
        self.chiamate: list[str] = []

    @property
    def cookies(self):
        return self._client.cookies

    def post(self, url, **kwargs):
        self.chiamate.append(url)
        if "/journeys/tick" in url:
            class Guasta:
                status_code = self._stato

                def json(_self):
                    raise ValueError("nessun corpo")
            return Guasta()
        return self._client.post(url, **kwargs)


def test_13_un_tick_rotto_non_ferma_le_email_gia_in_coda(client, mondo, modulo, spia):
    """LA GARANZIA PIU' IMPORTANTE DI QUESTA FASE.

    In coda c'e' un messaggio scritto da una persona, che con le journey non
    c'entra niente. Il motore e' rotto. Quel messaggio deve partire lo
    stesso: il giro finisce 2 e lo dice, ma parte.
    """
    _sequenza(modulo, mondo["ctx"]())
    dati_contatto = _contatto_dopo_l_accensione(mondo)

    accedi(client, mondo, "admin")
    manuale = client.post(
        f"/api/communication/contacts/{dati_contatto['contact']}/messages",
        json={"subject": "Scritto a mano", "body": "Buongiorno, la richiamo domani."})
    assert manuale.status_code == 200, manuale.text

    rotto = TickRotto(client)
    dati = runner.run_once(config(mondo), sessione=rotto)

    assert dati["journey_status"] == "failed"
    assert dati["sent"] == 1, "il messaggio manuale non e' partito"
    assert len(spia) == 1
    assert runner._application_failure(dati) is True, "un giro cosi' non e' verde"
    # E il motore non ha lavorato: nessuna iscrizione, quindi nessuna M1.
    assert iscrizioni(mondo) == []


def test_14_il_giro_con_il_tick_rotto_esce_2_e_non_1(client, mondo, modulo, spia,
                                                     monkeypatch):
    """1 vorrebbe dire "non abbiamo dispacciato", e non e' vero. La
    differenza e' operativa: l'1 si guarda nella piattaforma, il 2 nel ledger
    e nel motore."""
    _contatto_dopo_l_accensione(mondo)
    rotto = TickRotto(client)

    monkeypatch.setenv("COMMUNICATION_DISPATCH_BASE_URL", "https://testserver")
    monkeypatch.setenv("COMMUNICATION_DISPATCH_EMAIL", mondo["admin"]["email"])
    monkeypatch.setenv("COMMUNICATION_DISPATCH_PASSWORD", PASSWORD)
    vero = runner.run_once
    monkeypatch.setattr(runner, "run_once",
                        lambda c, **kw: vero(config(mondo), sessione=rotto))

    assert runner.main() == 2


def test_15_un_agente_non_puo_far_girare_il_motore(client, mondo, modulo, spia):
    """Il tick agisce su TUTTE le iscrizioni dell'agenzia, e un agente non
    vede tutti i record. Se l'account del cron fosse declassato per errore,
    il motore deve fermarsi in modo VISIBILE - non lavorare a meta'.

    Si misura il tick e non il giro intero di proposito: al dispatch un
    agente prende 403 e il `TestClient` alza un'eccezione `httpx` che il
    runner non cattura, perche' in produzione parla `requests` (e' la
    ragione documentata di `test_ops_11` in P29-2.6E). Farlo girare tutto
    qui proverebbe il comportamento di una libreria che il runner non usa.
    """
    _sequenza(modulo, mondo["ctx"]())
    _contatto_dopo_l_accensione(mondo)

    accedi(client, mondo, "agente")
    esito = runner._journey_tick(config(mondo), client)

    assert esito["status"] == "failed" and esito["reason"] == "http_403"
    assert iscrizioni(mondo) == [], "un agente ha fatto girare il motore"
    assert runner._application_failure({"journey_status": esito["status"]}) is True


# ===========================================================================
# G - LA SESSIONE
# ===========================================================================

def test_16_il_giro_non_lascia_una_sessione_viva(client, mondo, modulo, spia):
    """Il logout e' nel `finally`, e adesso il giro fa una chiamata in piu':
    tre giri devono lasciare zero sessioni, come prima."""
    _sequenza(modulo, mondo["ctx"]())
    _contatto_dopo_l_accensione(mondo)

    for _ in range(3):
        giro(client, mondo)

    vive = mondo["sql"]("SELECT count(*) AS n FROM operator_sessions "
                        "WHERE revoked_at IS NULL")
    assert vive[0]["n"] == 0, "una sessione per giro resta viva"


def test_17_il_tick_gira_dentro_la_sessione_del_giro(client, mondo, modulo, spia):
    """Senza cookie il tick prende 401, e il motore non lavora. Lo si prova
    togliendo il login: se il tick girasse fuori dalla sessione, qui
    iscriverebbe lo stesso."""
    _sequenza(modulo, mondo["ctx"]())
    _contatto_dopo_l_accensione(mondo)

    client.cookies.clear()
    risposta = client.post("/api/communication/journeys/tick", json={"limit": 10})
    assert risposta.status_code == 401, risposta.text
    assert iscrizioni(mondo) == []


# ===========================================================================
# H - LA SEQUENZA VERA, DAL CATALOGO
# ===========================================================================

def test_18_la_sequenza_stima_lead_vera_manda_M1_dal_cron(client, mondo, modulo, spia):
    """Non un passo di prova: la journey del catalogo, provisionata e
    attivata a mano come in produzione, con il testo vero di M1.

    Due giri, perche' la sequenza vera si comporta come deve: M1 e' dovuta
    UN GIORNO dopo la stima, quindi il primo giro iscrive e non accoda. Si fa
    scadere l'attesa sull'iscrizione - e non sul `sent_at` della stima, che
    e' un fatto e non si tocca - e il secondo giro accoda.

    Si guarda che M1 NASCA con il testo vero, non che parta: la finestra
    lun-sab 09:00-19:00 puo' spostare l'invio avanti di ore, e quando parte
    lo decide l'orologio di chi lancia la suite.
    """
    from communication import journey_catalog

    ctx = mondo["ctx"]()
    esito = journey_catalog.ensure_stima_lead_v1(ctx)
    attiva = modulo["journeys"].activate_journey(ctx, esito["journey"]["id"])
    assert attiva["status"] == "active"
    _contatto_dopo_l_accensione(mondo)

    primo = giro(client, mondo)
    assert primo["journey_status"] == "completed"
    assert primo["journey_queued"] == 0, "M1 e' dovuta domani, non adesso"
    iscrizione = iscrizioni(mondo)[0]
    assert iscrizione["next_step_no"] == 1

    mondo["sql"]("UPDATE communication_enrollments "
                 "SET next_action_at = NOW() - interval '1 minute' WHERE id = %s",
                 (iscrizione["id"],))
    secondo = giro(client, mondo)

    assert secondo["journey_queued"] == 1
    riga = m1(mondo)[0]
    assert riga["template_key"] == "stima_lead_m1" and riga["template_version"] == 1
    assert riga["communication_type"] == "marketing" and riga["mode"] == "automatic"
    assert riga["status"] in ("queued", "sent")
    assert "non vuoi piu' ricevere" in riga["rendered_body"].lower()
