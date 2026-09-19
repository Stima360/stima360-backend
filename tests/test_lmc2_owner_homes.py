"""LMC-2 - "La Mia Casa": il read-model, senza database.

Qui si provano le DECISIONI del servizio: quale valore iniziale si sceglie e
con quale ordine, come si calcola la completezza, quali capability sono vere,
che cosa NON esce dalle API, e che il router resti sottile. Tenancy, grant e
404 neutro - che solo un database puo' dire davvero - stanno in
tests/test_lmc2_owner_homes_postgres.py.

Mappa:
    A  il valore iniziale e i suoi due soli fallback reali
    B  la completezza del profilo: deterministica e spiegabile
    C  le capability: false finche' il dato non c'e'
    D  lo stato dei dati e la storia
    E  la superficie: niente dati privati, niente ricalcolo, niente scritture
    F  il router e il perimetro
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from owner import home_service

ROOT = Path(__file__).resolve().parents[1]

STIMA = {
    "id": 501, "agency_id": 7, "comune": "Alba Adriatica", "microzona": "Lungomare",
    "via": "Via Trieste", "civico": "12", "tipologia": "Appartamento", "mq": 95,
    "piano": "3", "locali": 4, "bagni": 2, "ascensore": True, "anno": 1998,
    "stato": "buono", "pertinenze": "garage", "vistamareyn": "si",
    "distanzamare": "0-100", "altrodescrizione": "ristrutturato nel 2019",
    "data": "2026-09-01T10:00:00+00:00",
}


def stima(**override):
    return {**STIMA, **override}


# ---------------------------------------------------------------------------
# A - il valore iniziale
# ---------------------------------------------------------------------------

def test_a1_la_baseline_del_watch_e_la_prima_fonte():
    valore, fonte = home_service.initial_value(
        baseline_payload={"price_exact": 185000, "eur_mq_finale": 1947},
        completed_payload={"price_exact": 999999})
    assert valore == 185000
    assert fonte == "property_watch_baseline"


def test_a2_senza_baseline_si_ripiega_sull_evento_stima_completata():
    valore, fonte = home_service.initial_value(
        baseline_payload=None, completed_payload={"price_exact": 185000})
    assert valore == 185000
    assert fonte == "seller_timeline_event"


def test_a3_senza_nessuna_delle_due_il_valore_e_null():
    valore, fonte = home_service.initial_value(baseline_payload=None, completed_payload=None)
    assert valore is None and fonte is None


@pytest.mark.parametrize("baseline", [{}, {"eur_mq_finale": 1947}, {"price_exact": None}])
def test_a4_una_baseline_senza_prezzo_non_e_un_valore(baseline):
    valore, fonte = home_service.initial_value(
        baseline_payload=baseline, completed_payload={"price_exact": 185000})
    assert valore == 185000 and fonte == "seller_timeline_event"


def test_a5_il_valore_corrente_non_esiste_in_lmc2():
    vista = home_service.build_home_detail(
        stima=stima(), watch=None, observations=[], baseline_payload=None,
        completed_payload={"price_exact": 185000})
    assert vista["valuation"]["initial_value"] == 185000
    assert vista["valuation"]["current_value"] is None
    assert vista["valuation"]["current_value_status"] == "history_not_available"


def test_a6_la_baseline_non_viene_spacciata_per_valore_corrente():
    vista = home_service.build_home_detail(
        stima=stima(), watch={"id": 3, "status": "active"},
        observations=[{"observation_type": "watch_started", "observed_at": "2026-09-01T10:00:00+00:00",
                       "payload": {"price_exact": 185000}}],
        baseline_payload={"price_exact": 185000}, completed_payload=None)
    assert vista["valuation"]["initial_value"] == 185000
    assert vista["valuation"]["current_value"] is None


# ---------------------------------------------------------------------------
# B - la completezza
# ---------------------------------------------------------------------------

def test_b1_i_campi_considerati_sono_dichiarati_e_solo_descrittivi():
    campi = home_service.PROFILE_FIELDS
    assert isinstance(campi, tuple) and len(campi) == len(set(campi))
    for privato in ("nome", "cognome", "email", "telefono", "note_internal",
                    "lead_status", "token", "prezzo_mq_base", "agency_id",
                    "consenso_marketing"):
        assert privato not in campi, privato


def test_b2_la_percentuale_e_conosciuti_su_considerati():
    profilo = home_service.build_profile(stima())
    totali = len(home_service.PROFILE_FIELDS)
    assert len(profilo["known_fields"]) + len(profilo["missing_fields"]) == totali
    atteso = round(len(profilo["known_fields"]) / totali * 100)
    assert profilo["completion_percent"] == atteso
    assert profilo["considered_fields"] == list(home_service.PROFILE_FIELDS)


def test_b3_tutti_i_campi_presenti_danno_cento():
    piena = {campo: "x" for campo in home_service.PROFILE_FIELDS}
    profilo = home_service.build_profile(piena)
    assert profilo["completion_percent"] == 100
    assert profilo["missing_fields"] == []


def test_b4_nessun_campo_presente_da_zero():
    profilo = home_service.build_profile({campo: None for campo in home_service.PROFILE_FIELDS})
    assert profilo["completion_percent"] == 0
    assert profilo["known_fields"] == []
    assert profilo["missing_fields"] == list(home_service.PROFILE_FIELDS)


@pytest.mark.parametrize("vuoto", [None, "", "   "])
def test_b5_una_stringa_vuota_non_e_un_dato(vuoto):
    profilo = home_service.build_profile(stima(civico=vuoto))
    assert "civico" in profilo["missing_fields"]


def test_b6_e_deterministica_e_non_dipende_dall_ordine():
    prima = home_service.build_profile(stima())
    dopo = home_service.build_profile(dict(reversed(list(stima().items()))))
    assert prima == dopo
    assert prima["known_fields"] == sorted(prima["known_fields"],
                                           key=home_service.PROFILE_FIELDS.index)


def test_b7_nessun_peso_commerciale_ne_punteggio():
    """Guarda il CODICE, non la prosa: la docstring dice "nessun peso" apposta."""
    albero = ast.parse(inspect.getsource(home_service.build_profile))
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            nodo.value = ""
    codice = ast.unparse(albero).lower()
    for vietato in ("weight", "peso", "score", "punteggio", "bonus", "*"):
        assert vietato not in codice.replace("* 100", ""), vietato


# ---------------------------------------------------------------------------
# C - le capability
# ---------------------------------------------------------------------------

def test_c1_in_lmc2_le_capability_sono_tutte_false():
    vista = home_service.build_home_detail(
        stima=stima(), watch={"id": 3, "status": "active"},
        observations=[{"observation_type": "watch_started", "observed_at": "2026-09-01T10:00:00+00:00",
                       "payload": {"price_exact": 185000}},
                      {"observation_type": "microzone_price_changed",
                       "observed_at": "2026-09-10T10:00:00+00:00", "payload": {}}],
        baseline_payload={"price_exact": 185000}, completed_payload=None)
    assert vista["capabilities"] == {"valuation_history": False, "buyer_demand": False,
                                     "comparables": False, "profile_update": False}


def test_c2_valuation_history_richiede_una_storia_del_valore():
    """Le osservazioni di PROPERTY WATCH sono monitoraggio del MERCATO, non
    rivalutazioni della casa: per quante ce ne siano, la capability resta
    false. L'unica cosa che fa storia del valore e' il `valuation_snapshot`
    introdotto da LMC-3, e nessuno degli scenari qui sotto ne ha uno."""
    assert home_service.VALUATION_HISTORY_OBSERVATIONS == frozenset({"valuation_snapshot"})

    for osservazioni in (
        [],
        [{"observation_type": "watch_started", "observed_at": "2026-09-01T10:00:00+00:00",
          "payload": {"price_exact": 185000}}],
        [{"observation_type": "watch_started", "observed_at": "2026-09-01T10:00:00+00:00", "payload": {}},
         {"observation_type": "microzone_price_changed", "observed_at": "2026-09-05T10:00:00+00:00", "payload": {}},
         {"observation_type": "internal_supply_snapshot", "observed_at": "2026-09-08T10:00:00+00:00", "payload": {}},
         {"observation_type": "buyer_pressure_snapshot", "observed_at": "2026-09-10T10:00:00+00:00", "payload": {}}],
    ):
        vista = home_service.build_home_detail(
            stima=stima(), watch={"id": 3, "status": "active"}, observations=osservazioni,
            baseline_payload={"price_exact": 185000}, completed_payload=None)
        assert vista["capabilities"]["valuation_history"] is False, osservazioni
        assert vista["history"]["history_available"] is False
        assert vista["history"]["history_status"] == "building"
        assert vista["valuation"]["current_value"] is None
        assert vista["valuation"]["current_value_status"] == "history_not_available"
        # Il monitoraggio reale resta contato.
        assert vista["history"]["observation_count"] == len(osservazioni)


def test_c2b_il_caso_dichiarato_nel_gate_lmc2():
    """Baseline + buyer pressure + offerta interna, nessun valuation_snapshot."""
    vista = home_service.build_home_detail(
        stima=stima(), watch={"id": 3, "status": "active"},
        observations=[
            {"observation_type": "watch_started", "observed_at": "2026-09-01T10:00:00+00:00",
             "payload": {"price_exact": 185000}},
            {"observation_type": "buyer_pressure_snapshot", "observed_at": "2026-09-10T10:00:00+00:00",
             "payload": {"score": 82}},
            {"observation_type": "internal_supply_snapshot", "observed_at": "2026-09-12T10:00:00+00:00",
             "payload": {"supply_count": 9}},
        ],
        baseline_payload={"price_exact": 185000}, completed_payload=None)

    assert vista["valuation"]["current_value"] is None
    assert vista["valuation"]["current_value_status"] == "history_not_available"
    assert vista["capabilities"]["valuation_history"] is False
    assert vista["history"]["history_status"] == "building"
    # Le tre osservazioni ci sono davvero, e si vedono nel conteggio.
    assert vista["history"]["observation_count"] == 3
    assert vista["history"]["first_observed_at"] == "2026-09-01T10:00:00+00:00"
    assert vista["history"]["last_observed_at"] == "2026-09-12T10:00:00+00:00"
    # E il valore ORIGINARIO resta quello, intatto.
    assert vista["valuation"]["initial_value"] == 185000


def test_c3_senza_watch_nessuna_capability_diventa_vera():
    vista = home_service.build_home_detail(
        stima=stima(), watch=None, observations=[], baseline_payload=None,
        completed_payload=None)
    assert set(vista["capabilities"].values()) == {False}


# ---------------------------------------------------------------------------
# D - stato dei dati e storia
# ---------------------------------------------------------------------------

def test_d1_ready_richiede_valore_e_watch():
    vista = home_service.build_home_detail(
        stima=stima(), watch={"id": 3, "status": "active"},
        observations=[{"observation_type": "watch_started", "observed_at": "2026-09-01T10:00:00+00:00",
                       "payload": {"price_exact": 185000}}],
        baseline_payload={"price_exact": 185000}, completed_payload=None)
    assert vista["data_status"] == "ready"


def test_d2_senza_valore_lo_stato_e_partial():
    vista = home_service.build_home_detail(
        stima=stima(), watch={"id": 3, "status": "active"}, observations=[],
        baseline_payload=None, completed_payload=None)
    assert vista["data_status"] == "partial"
    assert vista["valuation"]["initial_value"] is None


def test_d3_valore_ma_nessun_watch_e_building_history():
    vista = home_service.build_home_detail(
        stima=stima(), watch=None, observations=[], baseline_payload=None,
        completed_payload={"price_exact": 185000})
    assert vista["data_status"] == "building_history"
    assert vista["watch"]["has_watch"] is False


def test_d4_la_storia_riporta_solo_osservazioni_reali():
    osservazioni = [
        {"observation_type": "watch_started", "observed_at": "2026-09-01T10:00:00+00:00", "payload": {}},
        {"observation_type": "microzone_price_changed", "observed_at": "2026-09-10T10:00:00+00:00", "payload": {}},
        {"observation_type": "internal_supply_changed", "observed_at": "2026-09-05T10:00:00+00:00", "payload": {}},
    ]
    vista = home_service.build_home_detail(
        stima=stima(), watch={"id": 3, "status": "active"}, observations=osservazioni,
        baseline_payload={"price_exact": 185000}, completed_payload=None)
    storia = vista["history"]
    assert storia["observation_count"] == 3
    assert storia["first_observed_at"] == "2026-09-01T10:00:00+00:00"
    assert storia["last_observed_at"] == "2026-09-10T10:00:00+00:00"
    # Il monitoraggio e' reale e contato, ma non e' storia del VALORE.
    assert storia["history_available"] is False
    assert storia["history_status"] == "building"
    assert "observations" not in storia, "in LMC-2 non si espone il dettaglio delle osservazioni"


def test_d5_nessuna_serie_sintetica_ne_snapshot():
    vista = home_service.build_home_detail(
        stima=stima(), watch={"id": 3, "status": "active"},
        observations=[{"observation_type": "watch_started", "observed_at": "2026-09-01T10:00:00+00:00",
                       "payload": {"price_exact": 185000}}],
        baseline_payload={"price_exact": 185000}, completed_payload=None)
    testo = repr(vista)
    for vietato in ("series", "serie", "chart", "grafico", "valuation_snapshot", "trend"):
        assert vietato not in testo.lower(), vietato


# ---------------------------------------------------------------------------
# E - la superficie
# ---------------------------------------------------------------------------

def test_e1_il_detail_non_espone_dati_privati():
    riga = stima(nome="Mario", cognome="Rossi", email="mario@example.it",
                 telefono="+39 333 1234567", lead_status="nuovo",
                 note_internal="richiamare", prezzo_mq_base=1500, token="abc",
                 agency_id=7, consenso_marketing=True)
    vista = home_service.build_home_detail(
        stima=riga, watch={"id": 3, "status": "active"},
        observations=[{"observation_type": "watch_started", "observed_at": "2026-09-01T10:00:00+00:00",
                       "payload": {"price_exact": 185000, "prezzo_mq_base": 1500}}],
        baseline_payload={"price_exact": 185000, "prezzo_mq_base": 1500}, completed_payload=None)
    testo = repr(vista).lower()
    for vietato in ("mario", "rossi", "mario@example.it", "333", "richiamare",
                    "lead_status", "note_internal", "token", "agency_id",
                    "consenso", "prezzo_mq_base"):
        assert vietato.lower() not in testo, vietato


def test_e2_niente_buyer_pressure_ne_offerta_interna():
    """Il watch di PROPERTY WATCH porta con se' metriche interne - pressione
    degli acquirenti, inventario dei concorrenti. In LMC-2 esce solo la
    presenza del watch."""
    vista = home_service.build_home_detail(
        stima=stima(), watch={"id": 3, "status": "active", "agency_id": 7},
        observations=[
            {"observation_type": "watch_started", "observed_at": "2026-09-01T10:00:00+00:00", "payload": {}},
            {"observation_type": "buyer_pressure_snapshot", "observed_at": "2026-09-10T10:00:00+00:00",
             "payload": {"score": 82, "buy_requests": [11, 12], "budget": 210000}},
            {"observation_type": "internal_supply_snapshot", "observed_at": "2026-09-11T10:00:00+00:00",
             "payload": {"supply_count": 9}},
        ],
        baseline_payload={}, completed_payload=None)
    testo = repr(vista).lower()
    for vietato in ("buyer_pressure", "pressure", "supply", "budget", "score",
                    "buy_request", "match", "82", "210000"):
        assert vietato not in testo, vietato
    assert vista["watch"] == {"has_watch": True, "status": "active"}


def test_e3_il_watch_non_espone_il_proprio_id_interno():
    vista = home_service.build_home_detail(
        stima=stima(), watch={"id": 999, "status": "active"}, observations=[],
        baseline_payload=None, completed_payload=None)
    assert "999" not in repr(vista["watch"])


def test_e4_la_lista_espone_solo_i_campi_dichiarati():
    voce = home_service.build_home_summary(
        stima=stima(nome="Mario", email="mario@example.it"), has_watch=True,
        initial_value=185000, last_observed_at="2026-09-10T10:00:00+00:00",
        observation_count=2)
    assert set(voce) == {
        "stima_id", "comune", "microzona", "via", "civico", "tipologia", "mq",
        "created_at", "has_watch", "initial_value", "last_update_at", "data_status",
    }
    assert voce["stima_id"] == 501
    assert "mario" not in repr(voce).lower()


def test_e5_il_servizio_non_ricalcola_e_non_scrive():
    sorgente = (ROOT / "owner" / "home_service.py").read_text(encoding="utf-8")
    albero = ast.parse(sorgente)
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            nodo.value = ""
    codice = ast.unparse(albero)
    for vietato in ("compute_from_payload", "compute_base_from_payload",
                    "import valuation", "from valuation", "BASE_MQ",
                    "INSERT", "UPDATE", "DELETE", "record_observation",
                    "ensure_watch", "safe_record_event", "insert_observation"):
        assert vietato not in codice, vietato
    # E il modulo non importa affatto il motore di valutazione.
    importati = {n.names[0].name.split(".")[0] for n in ast.walk(ast.parse(sorgente))
                 if isinstance(n, (ast.Import, ast.ImportFrom)) and n.names
                 } | {n.module.split(".")[0] for n in ast.walk(ast.parse(sorgente))
                      if isinstance(n, ast.ImportFrom) and n.module}
    assert "valuation" not in importati and "valuation_base" not in importati


# ---------------------------------------------------------------------------
# F - il router e il perimetro
# ---------------------------------------------------------------------------

def test_f1_le_due_rotte_esistono_e_sono_legate_alla_sessione():
    albero = ast.parse((ROOT / "owner" / "router_portal.py").read_text(encoding="utf-8"))
    rotte = {}
    for nodo in ast.walk(albero):
        if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decoratore in nodo.decorator_list:
            if isinstance(decoratore, ast.Call) and decoratore.args:
                percorso = getattr(decoratore.args[0], "value", None)
                if percorso in ("/homes", "/homes/{stima_id}"):
                    rotte[percorso] = nodo
    assert set(rotte) == {"/homes", "/homes/{stima_id}"}
    for percorso, nodo in rotte.items():
        legata = any(
            isinstance(d, ast.Call) and getattr(d.func, "id", None) == "Depends"
            and getattr(d.args[0], "id", None) == "current_owner"
            for d in nodo.args.defaults if isinstance(d, ast.Call) and d.args)
        assert legata, percorso
        for argomento in nodo.args.args:
            assert argomento.arg not in {"agency_id", "owner_account_id", "account_id"}, percorso


def test_f2_il_router_resta_sottile():
    from owner import router_portal
    for nome in ("homes", "home_detail"):
        corpo = inspect.getsource(getattr(router_portal, nome))
        for vietato in ("SELECT", "INSERT", "JOIN", "price_exact", "completion_percent",
                        "PROFILE_FIELDS", "capabilities"):
            assert vietato not in corpo, (nome, vietato)
        assert len([r for r in corpo.splitlines() if r.strip() and not r.strip().startswith("#")]) < 20


def test_f3_nessuna_migration_in_lmc2():
    migrazioni = sorted(p.name for p in (ROOT / "migrations").glob("*.sql")
                        if not p.name.endswith("_down.sql"))
    assert migrazioni[-1] == "067_lmc1b_owner_login_reason.sql", migrazioni[-2:]


def test_f4_il_read_model_non_tocca_il_funnel_ne_i_domini_vicini():
    """`property_watch/` NON e' in questo elenco dal LMC-3 in poi: quella fase
    ha aggiunto li' lo snapshot del valore, ed e' il suo posto. Cio' che
    questo test protegge resta il funnel pubblico e i domini che il portale
    proprietario non deve toccare per leggere una casa."""
    import subprocess
    diff = subprocess.run(
        ["git", "--no-optional-locks", "diff", "--name-only", "--",
         "main.py", "seller_intelligence/", "seller_intent/",
         "next_best_action/", "followup/", "communication/", "operator_auth/"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert diff == "", diff


def test_f5_lo_scope_passato_a_property_watch_espone_solo_l_agenzia():
    scope = home_service.OwnerAgencyScope(7)
    assert scope.require_agency() == 7
    assert [n for n in dir(scope) if not n.startswith("_")] == ["require_agency"]
    assert not hasattr(scope, "__dict__")
