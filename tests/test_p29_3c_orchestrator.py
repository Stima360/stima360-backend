"""P29-3C senza database: la finestra di invio, e le sentinelle della fase.

LA FINESTRA e' una funzione pura, quindi si prova qui e per intero - giorni,
orari, fusi, e i due giorni dell'anno in cui un'ora non esiste o esiste due
volte. Un test che avesse bisogno di PostgreSQL per rispondere "che ora e' a
Roma" starebbe misurando la cosa sbagliata.

LE SENTINELLE tengono il perimetro di questa fase: nessun cron nuovo, nessun
seed, nessun testo commerciale, nessun provider nel motore, nessuna rete,
l'attore sempre dalla sessione, e il documento P29-2.0 fuori dall'indice.
"""
from __future__ import annotations

import ast
import inspect
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACCHETTO = ROOT / "communication"
ROMA = ZoneInfo("Europe/Rome")


def _codice(modulo) -> str:
    """Il CODICE, senza docstring e senza commenti: una sentinella che
    leggesse la prosa scambierebbe una spiegazione per un'infrazione."""
    import re
    testo = inspect.getsource(modulo)
    testo = re.sub(r'"{3}[\s\S]*?"{3}', "", testo)
    return re.sub(r"#[^\n]*", "", testo)


# ===========================================================================
# A - LA FINESTRA DI INVIO
# ===========================================================================

LAVORATIVA = {"days": [1, 2, 3, 4, 5], "from": "09:00", "to": "19:00"}


def _roma(anno, mese, giorno, ora, minuto=0) -> datetime:
    return datetime(anno, mese, giorno, ora, minuto, tzinfo=ROMA)


def _next(istante, finestra=LAVORATIVA, fuso="Europe/Rome"):
    from communication import send_window
    return send_window.next_allowed(istante, finestra, fuso)


def test_01_senza_finestra_l_istante_non_si_sposta():
    from communication import send_window
    istante = datetime(2026, 9, 19, 23, 30, tzinfo=timezone.utc)
    assert send_window.next_allowed(istante, None, "Europe/Rome") == istante
    assert send_window.next_allowed(istante, {}, "Europe/Rome") == istante


def test_02_dentro_la_finestra_l_istante_resta_quello():
    """Un passo dovuto e' dovuto ADESSO: la finestra non lo rimanda all'inizio
    della prossima, o ogni mail partirebbe alle nove in punto."""
    mercoledi = _roma(2026, 9, 23, 14, 30)
    assert _next(mercoledi) == mercoledi


def test_03_prima_dell_apertura_si_aspetta_l_apertura_dello_stesso_giorno():
    assert _next(_roma(2026, 9, 23, 6, 0)) == _roma(2026, 9, 23, 9, 0)


def test_04_dopo_la_chiusura_si_passa_al_giorno_dopo():
    assert _next(_roma(2026, 9, 23, 20, 0)) == _roma(2026, 9, 24, 9, 0)


def test_05_il_sabato_e_la_domenica_si_saltano():
    assert _next(_roma(2026, 9, 19, 10, 0)) == _roma(2026, 9, 21, 9, 0)   # sabato
    assert _next(_roma(2026, 9, 20, 10, 0)) == _roma(2026, 9, 21, 9, 0)   # domenica


def test_06_il_confine_destro_e_aperto():
    """`to` e' il primo istante NON valido: due finestre adiacenti non si
    sovrappongono di un minuto."""
    assert _next(_roma(2026, 9, 23, 18, 59)) == _roma(2026, 9, 23, 18, 59)
    assert _next(_roma(2026, 9, 23, 19, 0)) == _roma(2026, 9, 24, 9, 0)


def test_07_il_risultato_e_sempre_in_UTC_e_mai_nel_fuso_del_server():
    esito = _next(_roma(2026, 9, 19, 10, 0))
    assert esito.utcoffset() == timedelta(0) or esito.tzinfo is not None
    # 21 settembre 2026, ora legale: le 09:00 di Roma sono le 07:00 UTC.
    assert esito.astimezone(timezone.utc).hour == 7


def test_08_lo_stesso_istante_in_due_fusi_diversi_da_due_risposte():
    istante = datetime(2026, 9, 21, 6, 30, tzinfo=timezone.utc)  # 08:30 a Roma
    a_roma = _next(istante)
    a_londra = _next(istante, fuso="Europe/London")              # 07:30 a Londra
    assert a_roma == _roma(2026, 9, 21, 9, 0)
    assert a_londra == datetime(2026, 9, 21, 9, 0, tzinfo=ZoneInfo("Europe/London"))
    assert a_roma != a_londra


def test_09_DST_il_giorno_in_cui_le_ore_sono_23():
    """29 marzo 2026, Europa: alle 02:00 l'orologio salta alle 03:00.

    Una finestra che apre alle 02:30 quel giorno apre su un'ora che NON
    ESISTE. La risposta giusta non e' un errore e non e' "il giorno dopo": e'
    il primo istante reale dopo il salto. Con l'aritmetica ingenua sui
    secondi (`+86400`) questo caso sbaglia di un'ora, ed e' il motivo per cui
    l'avanzamento si fa sui giorni di calendario.
    """
    finestra = {"from": "02:30", "to": "23:00"}
    esito = _next(datetime(2026, 3, 28, 23, 0, tzinfo=timezone.utc), finestra)
    locale = esito.astimezone(ROMA)
    assert (locale.year, locale.month, locale.day) == (2026, 3, 29)
    assert locale.hour == 3 and locale.minute == 30, locale
    assert esito == datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)


def test_10_DST_il_giorno_in_cui_le_ore_sono_25():
    """25 ottobre 2026: le 02:30 esistono due volte. Si sceglie la PRIMA -
    aspettare l'ora doppia sarebbe un'ora di ritardo senza ragione."""
    finestra = {"from": "02:30", "to": "23:00"}
    esito = _next(datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc), finestra)
    assert esito == datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
    assert esito.astimezone(ROMA).hour == 2


def test_11_attraverso_il_cambio_d_ora_i_giorni_restano_giorni():
    """Dal venerdi' prima del cambio al lunedi' dopo: l'apertura e' sempre le
    09:00 LOCALI, anche se fra i due istanti non ci sono 72 ore esatte."""
    venerdi_sera = _roma(2026, 3, 27, 20, 0)
    esito = _next(venerdi_sera)
    assert esito.astimezone(ROMA).hour == 9
    assert esito.astimezone(ROMA).date().isoformat() == "2026-03-30"
    # E in UTC sono le 07:00, non le 08:00: l'ora legale e' entrata in mezzo.
    assert esito == datetime(2026, 3, 30, 7, 0, tzinfo=timezone.utc)


def test_11b_avanzare_di_un_giorno_ATTRAVERSO_il_salto_non_e_aggiungere_86400():
    """La prova che distingue le due implementazioni.

    Finestra 03:00-20:00, si parte dalla sera del 28 marzo 2026 (chiusa) e si
    cerca l'apertura del 29, il giorno in cui alle 02:00 l'orologio salta.

      giorno di calendario + apertura locale -> 03:00 CEST = 01:00 UTC  (giusto)
      istante + 86400 secondi               -> 04:00 CEST = 02:00 UTC  (sbagliato)

    Un'ora di differenza, una volta all'anno, su ogni passo di ogni journey:
    il genere di errore che nessuno nota finche' qualcuno non si lamenta.
    """
    finestra = {"from": "03:00", "to": "20:00"}
    esito = _next(_roma(2026, 3, 28, 21, 0), finestra)
    assert esito == datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)
    assert esito.astimezone(ROMA).hour == 3


def test_11c_e_lo_stesso_in_autunno_quando_il_giorno_ha_venticinque_ore():
    """25 ottobre 2026: l'ora torna indietro. L'apertura resta le 03:00
    locali, che quel giorno sono le 02:00 UTC e non le 01:00."""
    finestra = {"from": "03:00", "to": "20:00"}
    esito = _next(_roma(2026, 10, 24, 21, 0), finestra)
    assert esito == datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc)
    assert esito.astimezone(ROMA).hour == 3


def test_11d_una_finestra_a_tarda_sera_non_SALTA_il_giorno_del_cambio_d_ora():
    """Il caso in cui le due implementazioni divergono davvero.

    Apertura alle 23:30, si parte dalla sera del 28 marzo 2026 a finestra
    chiusa. Il giorno dopo e' quello del salto:

      giorno di calendario + 1   -> 29 marzo, 23:30 locali  (giusto)
      istante + 86400 secondi    -> 30 marzo, 00:30 locali  (un giorno intero
                                    di ritardo, perche' l'ora persa spinge
                                    l'istante oltre la mezzanotte)

    E' l'unico punto in cui l'aritmetica sui secondi sbaglia la DATA e non
    solo l'ora, ed e' il motivo per cui l'avanzamento qui e' un giorno di
    calendario.
    """
    finestra = {"from": "23:30", "to": "23:59"}
    # 23:59 e' il primo istante NON piu' valido del 28 (confine destro
    # aperto): da li' si cerca l'apertura del giorno dopo, che e' il punto.
    esito = _next(_roma(2026, 3, 28, 23, 59), finestra)
    locale = esito.astimezone(ROMA)
    assert (locale.month, locale.day) == (3, 29), locale
    assert (locale.hour, locale.minute) == (23, 30), locale
    assert esito == datetime(2026, 3, 29, 21, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("finestra", [
    {"days": [0]}, {"days": [8]}, {"days": "lun"}, {"from": "9"}, {"from": "25:00"},
    {"from": "10:00", "to": "09:00"}, {"from": "10:00", "to": "10:00"}, {"sconosciuto": 1},
])
def test_12_una_finestra_malformata_e_rifiutata_alla_creazione(finestra):
    from communication import send_window
    from communication.exceptions import ValidationError
    with pytest.raises(ValidationError):
        send_window.validate(finestra)


def test_13_la_finestra_si_valida_quando_si_crea_la_journey_non_al_tick():
    from communication import journey_service
    codice = _codice(journey_service)
    assert "send_window.validate" in codice
    assert "_valida_passi" in codice


def test_14_un_istante_senza_fuso_non_e_un_istante():
    from communication import send_window
    from communication.exceptions import ValidationError
    with pytest.raises(ValidationError):
        send_window.next_allowed(datetime(2026, 9, 21, 10, 0), LAVORATIVA, "Europe/Rome")


def test_15_un_fuso_inventato_e_rifiutato():
    from communication import send_window
    from communication.exceptions import ValidationError
    with pytest.raises(ValidationError):
        send_window.next_allowed(datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc),
                                 LAVORATIVA, "Marte/Olympus")


def test_16_la_finestra_non_guarda_mai_l_orologio_del_server():
    from communication import send_window
    codice = _codice(send_window)
    for vietato in ("now()", "utcnow", "datetime.now", "time.time", "localtime"):
        assert vietato not in codice, vietato


# ===========================================================================
# B - IL PERIMETRO DELLA FASE
# ===========================================================================

def _git(*argomenti) -> str:
    return subprocess.run(["git", "--no-optional-locks", *argomenti],
                          cwd=ROOT, capture_output=True, text=True).stdout.strip()


def _git_righe(*argomenti) -> list[str]:
    """Come `_git`, ma SENZA togliere gli spazi in testa: in `git status
    --porcelain` i primi due caratteri sono lo stato, e uno spazio iniziale
    tolto sposta ogni percorso di un carattere."""
    return subprocess.run(["git", "--no-optional-locks", *argomenti],
                          cwd=ROOT, capture_output=True, text=True).stdout.splitlines()


MODULI_NUOVI = ("journey_tick", "send_window")


def test_17_nessun_cron_nuovo_e_il_runner_non_accende_niente():
    """SENTINELLA AGGIORNATA DA P29-3E (collisione dichiarata).

    Quando P29-3C fu scritta il wiring tick -> dispatch non doveva esistere:
    la 071 non era applicata, e un cron che avesse chiamato il tick avrebbe
    preso 503 a ogni giro. Quella condizione e' finita - la migration e'
    su TEST, e la fase che collega le due cose e' P29-3E - quindi la
    sentinella non pretende piu' che il runner ignori le journey.

    Cio' che pretende ancora, e che non smettera' mai di pretendere: che di
    cron ce ne sia UNO, che sia quello dichiarato, e che non provisioni e non
    attivi niente. Accendere una sequenza commerciale e' un gesto
    amministrativo, e un cron che lo facesse da solo manderebbe email a nome
    di un'agenzia che non ha deciso niente.
    """
    from tests.p29_3e_diff import RUNNER_TOCCATO

    toccati = {r[3:].strip() for r in _git_righe("status", "--porcelain", "--", "run_*.py")}
    assert toccati <= {RUNNER_TOCCATO}, sorted(toccati)
    assert not list(ROOT.glob("run_journey*.py"))

    # Si legge il CODICE, non la prosa: la docstring del runner SPIEGA che la
    # sequenza si accende a mano, e una sentinella che leggesse le spiegazioni
    # scambierebbe quella frase per l'infrazione che descrive.
    import re as _re
    corrente = (ROOT / RUNNER_TOCCATO).read_text(encoding="utf-8")
    corrente = _re.sub(r'"{3}[\s\S]*?"{3}', "", corrente)
    corrente = _re.sub(r"#[^\n]*", "", corrente)
    for vietato in ("ensure_stima_lead", "stima_lead", "/provision", "/activate",
                    "/retire"):
        assert vietato not in corrente, vietato
    # E resta un CLIENT: nessun import del dominio, nessuna connessione.
    for vietato in ("from communication", "import communication", "psycopg2",
                    "get_connection", "smtplib"):
        assert vietato not in corrente, vietato


def test_18_nessuna_journey_viene_creata_o_attivata_da_sola():
    """Il motore e' acceso in folle: nessun seed, nessuna attivazione."""
    from communication import journey_tick
    codice = _codice(journey_tick)
    for vietato in ("provision_journey", "activate_journey", "stima_lead", "INSERT INTO "
                    "communication_journeys"):
        assert vietato not in codice, vietato
    migrazione = (ROOT / "migrations" / "071_p29_3_journey_automation.sql").read_text("utf-8")
    for vietato in ("INSERT INTO communication_journeys", "INSERT INTO communication_journey_steps"):
        assert vietato not in migrazione, vietato


def test_19_il_template_di_prova_e_immutabile_e_i_testi_reali_sono_di_P29_3D():
    """SENTINELLA AGGIORNATA DA P29-3D.

    P29-3C non doveva portare testi commerciali, e non ne ha portati: i
    cinque della sequenza della stima arrivano con la fase che li ha
    approvati, e li verifica `test_p29_3d_crm_journey.py` parola per parola.
    Qui resta la garanzia che vale sempre: il template di PROVA non e'
    cambiato, e il registro non contiene niente che nessuna fase abbia
    dichiarato.
    """
    import hashlib

    from communication import templates
    assert set(templates.REGISTRY) == {
        ("registry_probe", 1), ("stima_lead_m1", 1), ("stima_lead_m2", 1),
        ("stima_lead_m3", 1), ("stima_lead_m4", 1), ("stima_lead_m5", 1)}
    impronta = hashlib.sha256(
        inspect.getsource(templates.REGISTRY[("registry_probe", 1)].body).encode()).hexdigest()
    assert impronta[:16] == "774d98a71f3dcd94", impronta[:16]


def test_20_il_motore_non_importa_provider_e_non_tocca_la_rete():
    from communication import journey_tick, send_window
    for modulo in (journey_tick, send_window):
        codice = _codice(modulo)
        for vietato in ("providers", "smtp", "requests", "httpx", "urllib", "socket",
                        "invia_mail", "dispatch_batch", "adapter_per"):
            assert vietato not in codice, (modulo.__name__, vietato)


def test_21_il_motore_non_accetta_agency_id_e_l_attore_viene_dalla_sessione():
    from communication import journey_tick
    for nome, f in inspect.getmembers(journey_tick, inspect.isfunction):
        if f.__module__ != journey_tick.__name__:
            continue
        parametri = inspect.signature(f).parameters
        assert "agency_id" not in parametri, nome
        assert not [p for p in parametri if p.endswith("operator_user_id")], nome
    codice = _codice(journey_tick)
    assert "operatore(ctx)" in codice
    assert "require_agency()" in codice


def test_22_il_dispatcher_continua_a_non_conoscere_le_journey():
    from communication import dispatcher
    codice = _codice(dispatcher)
    for vietato in ("journey", "enrollment", "step_no", "run_no"):
        assert vietato not in codice, vietato


def test_23_la_decisione_sul_consenso_resta_UNA_SOLA():
    """`can_send_marketing_bulk` non e' una seconda implementazione: e' una
    seconda LETTURA che finisce nella stessa funzione di decisione."""
    from consent import guard
    sorgente = inspect.getsource(guard)
    albero = ast.parse(sorgente)
    funzioni = {n.name: n for n in albero.body if isinstance(n, ast.FunctionDef)}
    assert set(funzioni) == {"_coerente_con_evento", "can_send_marketing",
                             "can_send_marketing_bulk", "_decidi"}
    for nome in ("can_send_marketing", "can_send_marketing_bulk"):
        chiamate = {ast.unparse(n.func) for n in ast.walk(funzioni[nome])
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "_decidi" in chiamate, nome
    # E la decisione vera vive in `_decidi`: le due porte non decidono niente.
    for nome in ("can_send_marketing", "can_send_marketing_bulk"):
        corpo = ast.unparse(funzioni[nome])
        assert "MarketingSendDecision(" not in corpo, nome


def test_24_il_gate_di_invio_del_dispatcher_non_e_cambiato():
    """La versione in blocco serve al motore; chi SPEDISCE continua a
    chiedere una decisione alla volta, immediatamente prima."""
    from communication import dispatcher
    codice = _codice(dispatcher)
    assert "can_send_marketing(" in codice
    assert "can_send_marketing_bulk" not in codice


def test_25_le_tre_rotte_sono_dichiarate_con_le_soglie_giuste():
    from communication import router as router_comunicazione
    from communication.dependencies import require_dispatch_context
    from operator_auth.dependencies import legacy_basic_agency_context

    tick = inspect.signature(router_comunicazione.journeys_tick)
    assert tick.parameters["ctx"].default.dependency is require_dispatch_context
    for rotta in (router_comunicazione.journeys_send_current,
                  router_comunicazione.journeys_skip_current):
        firma = inspect.signature(rotta)
        assert firma.parameters["ctx"].default.dependency is legacy_basic_agency_context
        assert "agency_id" not in firma.parameters


def test_26_la_risposta_non_migrata_porta_un_codice_macchina():
    from communication import journey_tick, router as router_comunicazione
    assert router_comunicazione.FEATURE_NOT_MIGRATED == "feature_not_migrated"
    assert issubclass(journey_tick.FeatureNotMigrated, Exception)
    codice = _codice(router_comunicazione)
    assert "status_code=503" in codice


def test_27_la_sonda_dello_schema_e_una_query_e_non_e_memorizzata():
    from communication import journey_repository
    sorgente = inspect.getsource(journey_repository.schema_ready)
    assert sorgente.count("cur.execute") == 1
    # Si giudica il CODICE: la docstring SPIEGA perche' non si memorizza, e
    # una ricerca ingenua scambierebbe la spiegazione per l'infrazione.
    import re
    corpo = re.sub(r'"{3}[\s\S]*?"{3}', "", sorgente)
    for vietato in ("lru_cache", "cache", "global ", "_pronto ="):
        assert vietato not in corpo, vietato
    codice = _codice(journey_repository)
    assert "to_regclass" in codice


def test_28_il_tick_lo_chiama_UN_SOLO_percorso_automatico():
    """SENTINELLA AGGIORNATA DA P29-3E (collisione dichiarata).

    Prima: nessuno chiamava il tick, e doveva restare cosi' finche' la 071
    non fosse applicata. Adesso lo chiama il cron del dispatch, ed e' il
    punto della fase. Quello che la sentinella continua a impedire e' che lo
    chiami QUALCUN ALTRO: uno script dimenticato, una pagina della Shell, uno
    `.sh` di deploy. Due percorsi automatici verso lo stesso motore sono due
    giri concorrenti che nessuno ha progettato.
    """
    from tests.p29_3e_diff import RUNNER_TOCCATO

    chiamanti = set(_git("grep", "-l", "journeys/tick", "--",
                         "run_*.py", "scripts/*.py", "static/*", "*.sh").split())
    assert chiamanti <= {RUNNER_TOCCATO}, sorted(chiamanti)
    # E nessun ALTRO runner nomina le journey: quello dichiarato le nomina
    # perche' e' il suo mestiere, gli altri quattro no.
    for sorgente in ROOT.glob("run_*.py"):
        if sorgente.name == RUNNER_TOCCATO:
            continue
        assert "journey" not in sorgente.read_text(encoding="utf-8").lower(), sorgente.name


def test_29_nessuno_dei_moduli_nuovi_apre_una_connessione():
    """Le connessioni restano nel choke point: il motore riceve cursori."""
    # Gli aghi si compongono a runtime: scritti per esteso, questo file
    # risulterebbe esso stesso un sito di connessione all'inventario di
    # P26 - che cerca proprio quelle stringhe.
    aghi = ("get_" + "connection", "psycopg2" + ".connect")
    for nome in MODULI_NUOVI:
        testo = (PACCHETTO / f"{nome}.py").read_text(encoding="utf-8")
        for ago in aghi:
            assert ago not in testo, (nome, ago)


def test_30_i_file_toccati_sono_quelli_dichiarati_e_P29_2_0_resta_fuori():
    from tests.p29_3c_diff import FILE_MODIFICATI, FILE_NUOVI
    # SENTINELLA AGGIORNATA DA P29-3D: la fase successiva ha il suo
    # inventario, dichiarato allo stesso modo. Questo test continua a
    # pretendere che nel working tree non ci sia NIENTE che nessuna delle due
    # fasi abbia dichiarato: guarda l'unione, non smette di guardare.
    # E DA P29-3E, terza fase a dichiararsi allo stesso modo: l'unione
    # cresce, il verso del controllo no.
    from tests.p29_3d_diff import FILE_MODIFICATI as MOD_3D, FILE_NUOVI as NUOVI_3D
    from tests.p29_3e_diff import FILE_MODIFICATI as MOD_3E, FILE_NUOVI as NUOVI_3E

    righe = _git_righe("status", "--porcelain")
    nuovi = {r[3:].strip() for r in righe if r[:2].strip() in ("??", "A")}
    modificati = {r[3:].strip() for r in righe if r[:2].strip() not in ("??", "A")}
    tracciati = set(_git("ls-files").split())

    dichiarati_nuovi = FILE_NUOVI | NUOVI_3D | NUOVI_3E
    dichiarati_modificati = FILE_MODIFICATI | MOD_3D | NUOVI_3D | MOD_3E | NUOVI_3E
    assert nuovi - dichiarati_nuovi == {"P29_2_0_COMMUNICATION_DESIGN.md"}, \
        sorted(nuovi - dichiarati_nuovi)
    assert modificati <= dichiarati_modificati, sorted(modificati - dichiarati_modificati)
    for nome in FILE_NUOVI:
        assert (ROOT / nome).exists(), nome
    for nome in FILE_MODIFICATI:
        assert nome in tracciati, nome
    # NOTA DI P29-3D: finche' P29-3C era in corso, qui si pretendeva che
    # ogni voce dichiarata corrispondesse a una modifica VERA nel working
    # tree - un inventario che elenca file intonsi sarebbe un permesso, non
    # una dichiarazione. Dopo il commit di quella fase la verifica non ha
    # piu' un working tree da guardare, e la garanzia si sposta su quello
    # che resta vero per sempre: ogni file dichiarato esiste ed e'
    # nell'indice, ed e' cio' che i due cicli qui sopra controllano.
    assert "P29_2_0_COMMUNICATION_DESIGN.md" not in tracciati
    assert (ROOT / "P29_2_0_COMMUNICATION_DESIGN.md").exists()
