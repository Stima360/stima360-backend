"""LMC-11 - il giro periodico del valore: la parte che si prova senza database.

TRE DOMANDE.

La prima: il cron passa davvero da LMC-3? La tentazione di un job periodico e'
costruirsi il payload e la chiave di idempotenza per conto proprio - sarebbe
piu' diretto, e romperebbe in silenzio la semantica "un punto al giorno"
insieme al profilo effettivo di LMC-10. Qui si guarda il CODICE, non la prosa.

La seconda: il cron non fa nient'altro. Nessun evento commerciale, nessuna
email, nessun task. Un giro di ricalcolo che mandasse anche una notifica
diventerebbe, la notte in cui qualcuno lo schedula ogni ora, una campagna.

La terza: cosa finisce nei log. Un log di cron finisce in posti che non
controlliamo, e l'elenco dei campi stampabili e' chiuso apposta.

Tenancy, lock, idempotenza e storico stanno nel file PostgreSQL: qui non si
finge di provarli con un doppio.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

import run_property_watch_valuation_cron as cron
from property_watch import database as pw_database
from property_watch import repository as pw_repository
from property_watch import service as pw_service

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "run_property_watch_valuation_cron.py"


def _codice(oggetto) -> str:
    """Il sorgente con le STRINGHE SVUOTATE.

    Cercare una parola nel testo grezzo trova anche i commenti, ed e' il modo
    piu' rapido di scrivere un test che fallisce - o peggio passa - per la
    prosa invece che per il codice. Qui le costanti stringa diventano vuote
    prima di guardare.
    """
    albero = ast.parse(inspect.getsource(oggetto))
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            nodo.value = ""
    return ast.unparse(albero)


# ---------------------------------------------------------------------------
# A - il cron passa da LMC-3, e da nient'altro
# ---------------------------------------------------------------------------

def test_a1_il_ciclo_chiama_solo_la_funzione_scopata():
    codice = _codice(pw_service.refresh_valuation_snapshots_for_agency)
    assert "refresh_valuation_snapshot_scoped" in codice
    # Nessuna gemella senza contesto: non esiste, e non deve comparire.
    assert not hasattr(pw_service, "refresh_valuation_snapshot")
    assert "_AgencyScope" in codice, "lo scope per-agenzia e' quello certificato"


def test_a2_il_cron_non_costruisce_payload_ne_chiavi():
    """La composizione del profilo effettivo e il motore stanno in LMC-3/LMC-10.

    Una seconda strada verso il valore sarebbe una seconda verita', e la
    differenza si vedrebbe solo il giorno in cui le due divergono.
    """
    sorgenti = [_codice(pw_service.refresh_valuation_snapshots_for_agency),
                _codice(pw_service.refresh_valuation_snapshots_for_all_agencies),
                _codice(cron)]
    for codice in sorgenti:
        for vietato in ("compute_from_payload", "build_engine_payload",
                        "snapshot_idempotency_key", "payload_digest",
                        "home_profile", "effective_home", "BASE_MQ",
                        "get_base_mq"):
            assert vietato not in codice, vietato


def test_a3_il_reason_e_dichiarato_e_distingue_il_cron():
    assert pw_service.CRON_REFRESH_REASON == "scheduled_refresh"
    # Non lo stesso di LMC-10: guardando uno snapshot si deve poter dire se e'
    # nato dal cron o perche' il proprietario ha corretto i dati.
    from owner import home_update
    assert pw_service.CRON_REFRESH_REASON != home_update.REFRESH_REASON


def test_a4_gli_esiti_sono_quattro_e_dichiarati():
    assert pw_service.VALUATION_CYCLE_STATUSES == (
        "created", "reused", "skipped", "failed")


# ---------------------------------------------------------------------------
# B - NESSUN SIDE EFFECT COMMERCIALE (punti 22-25)
# ---------------------------------------------------------------------------

DOMINI_VIETATI = ("seller_intelligence", "record_event_scoped", "safe_record_event",
                  "communication", "dispatch", "email", "smtp", "whatsapp",
                  "sms", "notification", "notifica", "task", "activity",
                  "next_best_action", "followup", "seller_intent", "enqueue")


def test_b1_il_servizio_non_nomina_nessun_dominio_commerciale():
    for funzione in (pw_service.refresh_valuation_snapshots_for_agency,
                     pw_service.refresh_valuation_snapshots_for_all_agencies):
        codice = _codice(funzione).lower()
        for vietato in DOMINI_VIETATI:
            assert vietato not in codice, (funzione.__name__, vietato)


def test_b2_il_runner_non_nomina_nessun_dominio_commerciale():
    codice = _codice(cron).lower()
    for vietato in DOMINI_VIETATI:
        assert vietato not in codice, vietato


def test_b3_il_runner_non_importa_nulla_di_commerciale():
    """Guarda gli import veri dell'albero, non le parole."""
    albero = ast.parse(RUNNER.read_text(encoding="utf-8"))
    moduli = set()
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Import):
            moduli.update(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            moduli.add(nodo.module)
    ammessi = {"__future__", "argparse", "os", "time", "dataclasses",
               "property_watch", "property_watch.repository",
               "property_watch.service", "property_watch.database"}
    assert moduli <= ammessi, moduli - ammessi


def test_b4_il_runner_non_apre_socket_ne_http():
    """Cercato sull'ALBERO, non sul testo.

    La prima stesura cercava la sottostringa `get(` e falliva su
    `riepilogo.get('failed', 0)` - un dizionario, non una richiesta HTTP.
    Qui si guardano i nomi effettivamente chiamati e gli attributi di
    modulo: `dict.get` non e' un client di rete, e nessuna parola in un
    commento puo' far passare o fallire questo test.
    """
    albero = ast.parse(RUNNER.read_text(encoding="utf-8"))
    chiamate = set()
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Call):
            bersaglio = nodo.func
            if isinstance(bersaglio, ast.Name):
                chiamate.add(bersaglio.id)
            elif isinstance(bersaglio, ast.Attribute) and isinstance(bersaglio.value, ast.Name):
                chiamate.add(f"{bersaglio.value.id}.{bersaglio.attr}")
    rete = {"requests.get", "requests.post", "requests.Session", "urlopen",
            "socket.socket", "urlparse", "Session"}
    assert chiamate & rete == set(), chiamate & rete
    # E nessun modulo di rete importato: lo fissa gia' test_b3, qui si
    # verifica che non ne compaia uno dentro una funzione.
    for nodo in ast.walk(albero):
        if isinstance(nodo, (ast.Import, ast.ImportFrom)):
            nome = getattr(nodo, "module", None) or ""
            nomi = {nome} | {a.name for a in nodo.names}
            assert not ({"requests", "urllib", "http", "socket", "httpx"} & nomi), nomi


# ---------------------------------------------------------------------------
# C - LOG E PRIVACY (punto 21)
# ---------------------------------------------------------------------------

def test_c1_i_campi_stampabili_sono_un_elenco_chiuso():
    assert cron.CAMPI_LOG == ("agencies", "processed", "created", "reused",
                              "skipped", "failed")


def test_c2_una_chiave_non_prevista_non_viene_stampata(capsys):
    """La prova vera: si passa al log un dizionario pieno di dati personali e
    si verifica che non ne esca niente. Non basta che il codice "non li usi" -
    deve essere impossibile che li stampi."""
    cron._log("runner", "completed", 12, counts={
        "agencies": 2, "processed": 5, "created": 5,
        "email": "mario@example.it", "telefono": "+39 333 1234567",
        "indirizzo": "Via Trieste 12, Alba Adriatica", "token": "abc-123",
        "payload": {"price_exact": 210000}, "nome": "Mario", "budget": 210000,
    })
    uscita = capsys.readouterr().out
    for vietato in ("mario", "333", "Via Trieste", "abc-123", "price_exact",
                    "budget", "@example.it"):
        assert vietato.lower() not in uscita.lower(), (vietato, uscita)
    assert "agencies=2" in uscita and "created=5" in uscita


def test_c3_i_log_del_servizio_portano_solo_identificativi():
    """Nel servizio i log sono a mano: si verifica che le stringhe di formato
    nominino solo id interni, esito e classe di errore."""
    for funzione in (pw_service.refresh_valuation_snapshots_for_agency,
                     pw_service.refresh_valuation_snapshots_for_all_agencies):
        sorgente = inspect.getsource(funzione)
        for formato in re.findall(r'"(valuation_cron[^"]*)"', sorgente):
            campi = re.findall(r"(\w+)=%s", formato)
            assert set(campi) <= {"agency_id", "watch_id", "stima_id",
                                  "reason", "error_type",
                                  # LMC-11 FINAL: il cursore della paginazione
                                  # e' un id interno come gli altri.
                                  "after_watch_id"}, (formato, campi)


def test_c4_nessun_dato_personale_nelle_query_del_cron():
    """La lettura dei watch nomina due colonne e nient'altro."""
    sorgente = inspect.getsource(pw_repository.list_active_watch_page_for_agency)
    assert "SELECT w.id AS watch_id, w.stima_id AS stima_id" in sorgente
    for vietato in ("nome", "cognome", "email", "telefono", "s.*", "w.*",
                    "SELECT *"):
        assert vietato not in sorgente.split('"""')[2], vietato


# ---------------------------------------------------------------------------
# D - ELEGGIBILITA' (punti 1-4), sulla query
# ---------------------------------------------------------------------------

def test_d1_il_predicato_di_eleggibilita_e_quello_certificato():
    """I filtri certificati restano TUTTI; il cursore e' l'unica aggiunta."""
    sorgente = inspect.getsource(pw_repository.list_active_watch_page_for_agency)
    sql = sorgente.split('"""')[3]
    for pezzo in ("w.status = 'active'", "w.stima_id IS NOT NULL",
                  "w.agency_id = %s", "s.agency_id = w.agency_id",
                  "w.id > %s", "ORDER BY w.id ASC", "LIMIT %s"):
        assert pezzo in sql, pezzo
    # KEYSET, NON OFFSET: con OFFSET una riga che sparisce durante il giro
    # sposta tutte le altre, e la finestra salta o duplica elementi.
    assert "OFFSET" not in sql.upper()


def test_d2_lo_stato_attivo_e_l_unico_che_il_dominio_scrive():
    """SCOPERTA DELL'AUDIT, fissata qui perche' e' su questo che il cron conta.

    `property_watches.status` e' VARCHAR(30) senza CHECK, quindi lo schema non
    vieta niente. Ma l'unico valore che il codice scrive e' `'active'`, e
    l'unico che qualunque lettura accetta e' `'active'`: `stopped`, `paused` e
    `disabled` non esistono da nessuna parte. Se un giorno qualcuno ne
    introducesse uno, questo test lo fa vedere subito - e il cron, che filtra
    per `= 'active'`, lo escluderebbe comunque.
    """
    sorgenti = "".join(
        (ROOT / "property_watch" / nome).read_text(encoding="utf-8")
        for nome in ("repository.py", "service.py", "invisible_sale_repository.py"))
    scritture = re.findall(r"INSERT INTO property_watches[^;]*?'(\w+)'", sorgenti, re.S)
    assert set(scritture) == {"active"}, scritture
    assert not re.search(r"UPDATE property_watches[^;]*SET[^;]*status", sorgenti, re.S), \
        "nessun percorso cambia lo stato di un watch"


def test_d3_il_cron_non_disabilita_mai_un_watch():
    """Punto 11: un guasto non deve togliere di mezzo la casa."""
    for codice in (_codice(pw_service.refresh_valuation_snapshots_for_agency),
                   _codice(pw_service.refresh_valuation_snapshots_for_all_agencies),
                   _codice(cron)):
        for vietato in ("UPDATE", "DELETE", "status =", "set_status", "disable"):
            assert vietato not in codice, vietato


# ---------------------------------------------------------------------------
# E - IL LOCK (punti 26-27), sulla forma
# ---------------------------------------------------------------------------

def test_e1_lo_scope_del_lock_e_dichiarato():
    assert pw_database.VALUATION_CRON_LOCK_SCOPE == "property_watch:valuation_cron"


def test_e2_il_lock_e_di_sessione_e_non_bloccante():
    """Transazionale non basterebbe: cadrebbe alla prima commit, cioe' dopo il
    primo watch. E bloccante accumulerebbe processi invece di uscire."""
    sorgente = inspect.getsource(pw_database.advisory_job_lock)
    assert "pg_try_advisory_lock" in sorgente
    assert "pg_advisory_xact_lock" not in sorgente
    assert "pg_advisory_unlock" in sorgente
    assert "hashtextextended" in sorgente, "stessa derivazione di chiave del repo"


def test_e3_il_lock_si_rilascia_anche_dopo_un_errore():
    sorgente = inspect.getsource(pw_database.advisory_job_lock)
    corpo = sorgente[sorgente.index("finally:"):]
    assert "pg_advisory_unlock" in corpo
    assert "conn.close()" in corpo


def test_e4_il_runner_prende_il_lock_prima_di_lavorare():
    sorgente = inspect.getsource(cron.main)
    assert sorgente.index("advisory_job_lock") < sorgente.index("run_cycle")
    assert "another_run_active" in sorgente


def test_e5_lock_non_ottenuto_esce_pulito(monkeypatch, capsys):
    """Punto 26: non un errore, l'altra esecuzione sta lavorando."""
    from contextlib import contextmanager

    @contextmanager
    def occupato(_scope):
        yield False

    chiamate = []
    monkeypatch.setattr(pw_database, "advisory_job_lock", occupato)
    monkeypatch.setattr(cron, "_active_agency_ids", lambda: [1])
    monkeypatch.setattr(cron, "run_cycle", lambda c: chiamate.append(c))

    assert cron.main(["--all-agencies"]) == 0
    assert chiamate == [], "nessun lavoro senza lock"
    assert "status=another_run_active" in capsys.readouterr().out


def test_e6_lock_rilasciato_anche_se_il_giro_solleva(monkeypatch, capsys):
    from contextlib import contextmanager

    rilasci = []

    @contextmanager
    def libero(_scope):
        try:
            yield True
        finally:
            rilasci.append("rilasciato")

    monkeypatch.setattr(pw_database, "advisory_job_lock", libero)
    monkeypatch.setattr(cron, "_active_agency_ids", lambda: [1])

    def esplode(_config):
        raise RuntimeError("giro rotto")

    monkeypatch.setattr(cron, "run_cycle", esplode)
    assert cron.main(["--all-agencies"]) == 1
    assert rilasci == ["rilasciato"]
    assert "status=failed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# F - CONFIGURAZIONE, BATCH E CODICI DI USCITA (punti 17-20)
# ---------------------------------------------------------------------------

def test_f1_senza_modalita_non_parte(capsys):
    assert cron.main([]) == 1
    assert "phase=config status=failed" in capsys.readouterr().out


def test_f2_le_due_modalita_si_escludono():
    with pytest.raises(cron.ConfigurationError):
        cron.load_config(["--agency-id", "1", "--all-agencies"])
    with pytest.raises(cron.ConfigurationError):
        cron.load_config(["--agency-id", "0"])


def test_f3_la_dimensione_di_pagina_e_configurabile(monkeypatch):
    """Il nome della variabile resta, il significato e' "pagina"."""
    assert cron.load_config(["--all-agencies"]).page_size == cron.PAGE_SIZE_DEFAULT == 500
    monkeypatch.setenv("PROPERTY_WATCH_VALUATION_LIMIT", "25")
    assert cron.load_config(["--all-agencies"]).page_size == 25
    for cattivo in ("0", "-1", "abc", str(cron.PAGE_SIZE_MASSIMA + 1)):
        monkeypatch.setenv("PROPERTY_WATCH_VALUATION_LIMIT", cattivo)
        with pytest.raises(cron.ConfigurationError):
            cron.load_config(["--all-agencies"])


def test_f4_ogni_agenzia_viene_percorsa_per_intero():
    """Non un tetto per agenzia e non un tetto globale: una dimensione di
    pagina, con il giro che scorre tutte le pagine di ogni tenant."""
    sorgente = inspect.getsource(pw_service.refresh_valuation_snapshots_for_all_agencies)
    assert "page_size=page_size" in sorgente
    ciclo = inspect.getsource(pw_service.refresh_valuation_snapshots_for_agency)
    assert "while True:" in ciclo, "il ciclo scorre le pagine"


def test_f5_nessun_watch_da_fare_non_e_un_guasto(monkeypatch, capsys):
    from contextlib import contextmanager

    @contextmanager
    def libero(_scope):
        yield True

    monkeypatch.setattr(pw_database, "advisory_job_lock", libero)
    monkeypatch.setattr(cron, "_active_agency_ids", lambda: [1])
    monkeypatch.setattr(cron, "run_cycle", lambda c: {
        "agencies": 1, "processed": 0, "created": 0, "reused": 0,
        "skipped": 0, "failed": 0, "runs": []})
    assert cron.main(["--all-agencies"]) == 0
    assert "status=completed" in capsys.readouterr().out


def test_f6_un_watch_fallito_da_exit_2(monkeypatch, capsys):
    from contextlib import contextmanager

    @contextmanager
    def libero(_scope):
        yield True

    monkeypatch.setattr(pw_database, "advisory_job_lock", libero)
    monkeypatch.setattr(cron, "_active_agency_ids", lambda: [1])
    monkeypatch.setattr(cron, "run_cycle", lambda c: {
        "agencies": 1, "processed": 3, "created": 2, "reused": 0,
        "skipped": 0, "failed": 1, "runs": []})
    assert cron.main(["--all-agencies"]) == 2
    uscita = capsys.readouterr().out
    assert "status=failed" in uscita and "failed=1" in uscita


def test_f7_skipped_non_e_un_guasto(monkeypatch, capsys):
    """Punto 9: `skipped` e' uno stato lecito. Trasformarlo in allarme
    renderebbe rosso un cron che funziona."""
    from contextlib import contextmanager

    @contextmanager
    def libero(_scope):
        yield True

    monkeypatch.setattr(pw_database, "advisory_job_lock", libero)
    monkeypatch.setattr(cron, "_active_agency_ids", lambda: [1])
    monkeypatch.setattr(cron, "run_cycle", lambda c: {
        "agencies": 1, "processed": 3, "created": 1, "reused": 0,
        "skipped": 2, "failed": 0, "runs": []})
    assert cron.main(["--all-agencies"]) == 0
    assert "skipped=2" in capsys.readouterr().out


def test_f8_agenzia_non_attiva_e_un_guasto_tecnico(monkeypatch, capsys):
    monkeypatch.setattr(cron, "_active_agency_ids", lambda: [1, 2])
    assert cron.main(["--agency-id", "9"]) == 1
    assert "reason=agenzia_non_attiva" in capsys.readouterr().out


def test_f9_database_irraggiungibile_e_exit_1(monkeypatch, capsys):
    def esplode():
        raise RuntimeError("db giu'")

    monkeypatch.setattr(cron, "_active_agency_ids", esplode)
    assert cron.main(["--all-agencies"]) == 1
    assert "phase=agencies status=failed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# G - ISOLAMENTO DEI GUASTI (punto 15-16), senza database
# ---------------------------------------------------------------------------

def _finto_ciclo(monkeypatch, bersagli, esiti):
    """Sostituisce lettura e refresh, lasciando il ciclo vero al suo posto.

    La lettura finta rispetta il CURSORE: restituisce i bersagli con
    `watch_id > after_watch_id`, a pagine. Un doppio che ignorasse il
    cursore restituirebbe sempre la stessa pagina e il ciclo vero girerebbe
    all'infinito - il che sarebbe anche un modo di scoprire che il cursore
    non avanza, ma dentro un test che non finisce mai.
    """
    def pagina(agency_id, *, after_watch_id=0, page_size):
        resto = [b for b in bersagli if b["watch_id"] > after_watch_id]
        return resto[:page_size]

    monkeypatch.setattr(pw_repository, "list_active_watch_page_for_agency", pagina)

    def refresh(ctx, stima_id, *, reason, now=None):
        esito = esiti[stima_id]
        if isinstance(esito, Exception):
            raise esito
        return {"status": esito}

    monkeypatch.setattr(pw_service, "refresh_valuation_snapshot_scoped", refresh)


def test_g1_un_watch_che_fallisce_non_ferma_il_successivo(monkeypatch):
    _finto_ciclo(monkeypatch,
                 [{"watch_id": 1, "stima_id": 10}, {"watch_id": 2, "stima_id": 20},
                  {"watch_id": 3, "stima_id": 30}],
                 {10: "created", 20: RuntimeError("rotto"), 30: "created"})
    esito = pw_service.refresh_valuation_snapshots_for_agency(7)
    assert esito["processed"] == 3
    assert esito["created"] == 2, "il terzo e' stato lavorato lo stesso"
    assert esito["failed"] == 1


def test_g2_le_eccezioni_di_dominio_sono_skipped_non_failed(monkeypatch):
    from property_watch.exceptions import (StimaNotFoundError, ValidationError,
                                           WatchNotFoundError)
    _finto_ciclo(monkeypatch,
                 [{"watch_id": 1, "stima_id": 10}, {"watch_id": 2, "stima_id": 20},
                  {"watch_id": 3, "stima_id": 30}, {"watch_id": 4, "stima_id": 40}],
                 {10: StimaNotFoundError("x"), 20: WatchNotFoundError("x"),
                  30: ValidationError("x"), 40: "created"})
    esito = pw_service.refresh_valuation_snapshots_for_agency(7)
    assert esito["skipped"] == 3
    assert esito["failed"] == 0
    assert esito["created"] == 1


def test_g3_un_esito_sconosciuto_conta_come_failed(monkeypatch):
    """Se LMC-3 cambiasse contratto, il cron non deve contarlo come successo."""
    _finto_ciclo(monkeypatch, [{"watch_id": 1, "stima_id": 10}],
                 {10: "qualcosa_di_nuovo"})
    assert pw_service.refresh_valuation_snapshots_for_agency(7)["failed"] == 1


def test_g4_un_agenzia_che_solleva_non_ferma_le_altre(monkeypatch):
    monkeypatch.setattr(pw_repository, "list_active_agency_ids", lambda: [1, 2, 3])

    def ciclo(agency_id, *, page_size=None, now=None):
        if agency_id == 2:
            raise RuntimeError("tenant rotto")
        return {"agency_id": agency_id, "processed": 1, "created": 1,
                "reused": 0, "skipped": 0, "failed": 0}

    monkeypatch.setattr(pw_service, "refresh_valuation_snapshots_for_agency", ciclo)
    esito = pw_service.refresh_valuation_snapshots_for_all_agencies()
    assert esito["agencies"] == 3
    assert esito["created"] == 2
    assert esito["failed"] == 1


def test_g5_il_try_sta_dentro_il_ciclo():
    """Attorno al ciclo, il primo guasto lascerebbe senza valore tutte le case
    successive: un tenant intero fermo per una riga sola."""
    sorgente = inspect.getsource(pw_service.refresh_valuation_snapshots_for_agency)
    assert sorgente.index("for bersaglio in pagina") < sorgente.index("try:")


# ---------------------------------------------------------------------------
# H - NESSUNA MIGRATION, NESSUN NUOVO ENTRYPOINT (punto 17)
# ---------------------------------------------------------------------------

def test_h1_nessuna_migration_nuova():
    """SENTINELLA AGGIORNATA DA LMC-12.

    LMC-11 non ha creato schema e continua a non averne bisogno. La 069 e' di
    LMC-12 - lo stream di notifiche PRE-INCARICO `owner_home_notifications`,
    approvato dal DESIGN GATE - e si nomina invece di smettere di guardare:
    qualunque ALTRA migration comparisse nel working tree farebbe ancora
    fallire questo test.
    """
    import subprocess
    nuovi = {riga[3:].strip() for riga in subprocess.run(
        ["git", "--no-optional-locks", "status", "--porcelain", "--", "migrations/"],
        cwd=ROOT, capture_output=True, text=True).stdout.splitlines()}
    atteso = {"migrations/069_lmc12_owner_home_notifications.sql",
              "migrations/069_lmc12_owner_home_notifications_down.sql"}
    assert nuovi - atteso == set(), sorted(nuovi - atteso)


def test_h2_il_lock_passa_dal_choke_point_del_repository():
    """Il lock apre la sua connessione dal choke point dell'applicazione, non
    per conto proprio: la governance H11 conta i file che ne aprono una, e
    `property_watch/database.py` non deve entrare in quell'elenco.

    Il nome della chiamata vietata e' composto a pezzi di proposito: scritto
    per esteso, questo file comparirebbe lui stesso fra i siti di connessione
    rilevati da `tests/test_p26_db_entrypoints.py`, che cerca proprio quella
    stringa. Un test che si fa segnalare dalla governance che sta
    verificando e' un falso positivo che qualcuno poi mette in whitelist, ed
    e' cosi' che una whitelist smette di voler dire qualcosa.
    """
    vietata = "psycopg2" + "." + "connect"
    sorgente = (ROOT / "property_watch" / "database.py").read_text(encoding="utf-8")
    assert vietata not in sorgente
    assert "from database import get_connection" in sorgente


def test_h3_valuation_non_e_stato_toccato():
    import subprocess
    diff = subprocess.run(
        ["git", "--no-optional-locks", "diff", "--name-only", "--",
         "valuation.py", "database.py", "migrations/"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert diff == "", diff


# ---------------------------------------------------------------------------
# I - LA FONTE DEL MERCATO, COM'E' DAVVERO OGGI (punto 7)
# ---------------------------------------------------------------------------

def test_i1_il_motore_non_legge_il_database():
    """Fissa il risultato dell'audit: `compute_from_payload` non ha una fonte
    di mercato dinamica. `valuation.py` importa tre moduli di libreria
    standard e nient'altro - nessuna connessione, nessuna query, nessun
    `zone_valori`."""
    import valuation
    albero = ast.parse((ROOT / "valuation.py").read_text(encoding="utf-8"))
    moduli = set()
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Import):
            moduli.update(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            moduli.add(nodo.module)
    assert moduli == {"typing", "math", "decimal"}, moduli
    assert "zone_valori" not in inspect.getsource(valuation)
    assert isinstance(valuation.BASE_MQ, dict)


def test_i2_base_mq_arriva_dal_dizionario_in_sorgente():
    import valuation
    comune, zone = next(iter(valuation.BASE_MQ.items()))
    microzona, atteso = next(iter(zone.items()))
    assert valuation.get_base_mq(comune, microzona) == float(atteso)
    assert valuation.get_base_mq("Comune Inventato", "Zona Inventata") == 0.0
