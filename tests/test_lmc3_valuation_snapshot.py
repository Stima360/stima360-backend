"""LMC-3 - la cronologia REALE del valore, senza database.

Qui si prova che il valore nuovo esce dal motore UFFICIALE e da nessun'altra
parte, che l'impronta dell'algoritmo e' derivata dal codice davvero usato,
che la chiave di idempotenza e' deterministica, e che il read-model di LMC-2
cambia esattamente dove deve. Scritture, tenancy e idempotenza su righe vere
stanno in tests/test_lmc3_valuation_snapshot_postgres.py.

Mappa:
    A  il motore: un solo entry point, nessuna seconda formula
    B  l'impronta dell'algoritmo
    C  il payload: dai campi della stima a quelli del motore
    D  lo snapshot e la sua chiave
    E  il read-model: current_value, storico, capability
    F  30/90/365 e methodology_changed
    G  il perimetro
"""

from __future__ import annotations

import ast
import hashlib
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import valuation
from owner import home_service
from property_watch import valuation_snapshot as vs

ROOT = Path(__file__).resolve().parents[1]
ORA = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)

STIMA = {
    "id": 501, "comune": "Alba Adriatica", "microzona": "Villa Fiore",
    "via": "Via Trieste", "civico": "12", "tipologia": "Appartamento", "mq": 95,
    "piano": "3", "locali": 4, "bagni": 2, "pertinenze": "garage, cantina",
    "ascensore": "True", "anno": 1998, "stato": "buono",
    "posizionemare": "fronte", "distanzamare": "0-100", "barrieramare": "no",
    "vistamareyn": "si", "vistamaredettaglio": "frontale", "vistamare": "mare",
    "mqgiardino": 0, "mqgarage": 18, "mqcantina": 6, "mqpostoauto": 0,
    "mqtaverna": 0, "mqsoffitta": 0, "mqterrazzo": 12, "numbalconi": 2,
    "altrodescrizione": "ristrutturato nel 2019",
}


def stima(**override):
    return {**STIMA, **override}


# ---------------------------------------------------------------------------
# A - il motore
# ---------------------------------------------------------------------------

def test_a1_il_calcolo_passa_dal_motore_ufficiale(monkeypatch):
    chiamate = []
    vero = valuation.compute_from_payload

    def spia(payload):
        chiamate.append(payload)
        return vero(payload)

    monkeypatch.setattr(vs, "compute_from_payload", spia)
    esito = vs.compute_snapshot(stima(), reason="manual", computed_at=ORA)

    assert len(chiamate) == 1
    assert esito["price_exact"] == vero(vs.build_engine_payload(stima()))["price_exact"]


def test_a2_nessuna_seconda_formula_nel_modulo():
    """Il modulo non ricalcola niente per conto proprio: non nomina
    coefficienti, non moltiplica prezzi, non conosce BASE_MQ."""
    albero = ast.parse((ROOT / "property_watch" / "valuation_snapshot.py").read_text(encoding="utf-8"))
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            nodo.value = ""
    codice = ast.unparse(albero)
    for vietato in ("BASE_MQ", "coeff_", "prezzo_mq_finale", "valore_totale",
                    "valore_pertinenze", "get_base_mq"):
        assert vietato not in codice, vietato
    # L'unica funzione del motore che importa e' quella ufficiale.
    da_valuation = {n.names[0].name for n in ast.walk(ast.parse(
        (ROOT / "property_watch" / "valuation_snapshot.py").read_text(encoding="utf-8")))
        if isinstance(n, ast.ImportFrom) and n.module == "valuation"}
    assert da_valuation == {"compute_from_payload"}


def test_a3_il_motore_e_lo_stesso_del_funnel_pubblico():
    """`/api/salva_stima` chiama `valuation.compute_from_payload`: LMC-3 usa
    quella stessa funzione, non una copia."""
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "from valuation import compute_from_payload" in main_py
    assert vs.compute_from_payload is valuation.compute_from_payload


# ---------------------------------------------------------------------------
# B - l'impronta dell'algoritmo
# ---------------------------------------------------------------------------

def test_b1_l_impronta_deriva_dal_codice_realmente_usato():
    atteso = hashlib.sha256(Path(valuation.__file__).read_bytes()).hexdigest()[:16]
    assert vs.ALGORITHM_FINGERPRINT == f"valuation-{atteso}"


def test_b2_e_stabile_e_deterministica():
    assert vs.algorithm_fingerprint() == vs.algorithm_fingerprint() == vs.ALGORITHM_FINGERPRINT


def test_b3_non_e_un_numero_di_versione_inventato():
    sorgente = (ROOT / "property_watch" / "valuation_snapshot.py").read_text(encoding="utf-8")
    albero = ast.parse(sorgente)
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            assert nodo.value not in ("v1", "1.0", "v1.0", "valuation-1.0"), nodo.value


def test_b4_cambiare_il_motore_cambia_l_impronta(tmp_path, monkeypatch):
    finto = tmp_path / "valuation.py"
    finto.write_text("def compute_from_payload(p):\n    return {}\n", encoding="utf-8")
    monkeypatch.setattr(vs, "_ENGINE_SOURCE", finto)
    nuova = vs.algorithm_fingerprint()
    assert nuova != vs.ALGORITHM_FINGERPRINT
    assert nuova.startswith("valuation-")


# ---------------------------------------------------------------------------
# C - il payload per il motore
# ---------------------------------------------------------------------------

def test_c1_la_mappa_dei_campi_e_dichiarata_e_copre_il_motore():
    mappa = vs.ENGINE_FIELD_MAP
    assert isinstance(mappa, tuple)
    chiavi_motore = {destinazione for _origine, destinazione in mappa}
    # Ogni chiave che il motore legge, o e' mappata o e' derivata (ascensore).
    lette_dal_motore = set(vs.ENGINE_PAYLOAD_KEYS)
    assert chiavi_motore | {"ascensore"} >= lette_dal_motore, lette_dal_motore - chiavi_motore


def test_c2_le_colonne_della_stima_diventano_le_chiavi_del_motore():
    payload = vs.build_engine_payload(stima())
    assert payload["comune"] == "Alba Adriatica"
    assert payload["microzona"] == "Villa Fiore"
    assert payload["posizioneMare"] == "fronte"
    assert payload["distanzaMare"] == "0-100"
    assert payload["vistaMareYN"] == "si"
    assert payload["vistaMareDettaglio"] == "frontale"
    assert payload["mqGarage"] == 18
    assert payload["numBalconi"] == 2
    assert payload["altroDescrizione"] == "ristrutturato nel 2019"


def test_c3_l_ascensore_usa_la_stessa_semantica_del_funnel():
    """Il funnel passa "Sì"/"No"; in tabella la colonna e' testo. La
    conversione riusa i valori che il motore gia' riconosce come vero."""
    for vero in ("True", "true", "si", "sì", "1", "yes"):
        assert vs.build_engine_payload(stima(ascensore=vero))["ascensore"] == "Sì", vero
    for falso in ("False", "false", "no", "0", None, ""):
        assert vs.build_engine_payload(stima(ascensore=falso))["ascensore"] == "No", falso


def test_c4_nessun_default_commerciale_nuovo():
    """Un campo assente resta assente: i ripieghi sono quelli storici del
    motore, non nuovi valori decisi qui."""
    vuota = {"id": 1}
    payload = vs.build_engine_payload(vuota)
    for chiave, valore in payload.items():
        if chiave == "ascensore":
            assert valore == "No"
            continue
        assert valore is None, (chiave, valore)


def test_c5_il_payload_non_porta_dati_personali():
    payload = vs.build_engine_payload(stima(nome="Mario", cognome="Rossi",
                                            email="m@example.it", telefono="333",
                                            prezzo_mq_base=1500, lead_status="nuovo"))
    testo = repr(payload).lower()
    for vietato in ("mario", "rossi", "example.it", "333", "1500", "lead_status"):
        assert vietato not in testo, vietato


# ---------------------------------------------------------------------------
# D - lo snapshot e la sua chiave
# ---------------------------------------------------------------------------

def test_d1_lo_snapshot_porta_tutto_cio_che_serve():
    esito = vs.compute_snapshot(stima(), reason="manual", computed_at=ORA)
    for campo in ("price_exact", "eur_mq_finale", "base_mq", "computed_at", "reason",
                  "algorithm_fingerprint", "input_digest"):
        assert campo in esito, campo
    assert esito["reason"] == "manual"
    assert esito["computed_at"] == ORA.isoformat()
    assert esito["algorithm_fingerprint"] == vs.ALGORITHM_FINGERPRINT
    assert isinstance(esito["price_exact"], (int, float))
    assert isinstance(esito["eur_mq_finale"], (int, float))
    assert isinstance(esito["base_mq"], (int, float))


def test_d2_l_impronta_dell_input_e_deterministica_e_ignora_l_ordine():
    uno = vs.payload_digest({"a": 1, "b": "x"})
    due = vs.payload_digest({"b": "x", "a": 1})
    assert uno == due and len(uno) == 64
    assert vs.payload_digest({"a": 2, "b": "x"}) != uno


def test_d3_la_chiave_e_watch_finestra_input_e_algoritmo():
    chiave = vs.snapshot_idempotency_key(watch_id=3, computed_at=ORA,
                                         input_digest="a" * 64, fingerprint="valuation-abc")
    assert chiave == "property_watch:valuation_snapshot:watch:3:2026-09-19:aaaaaaaaaaaaaaaa:valuation-abc"


@pytest.mark.parametrize("cambia,uguale", [
    ({"watch_id": 4}, False), ({"input_digest": "b" * 64}, False),
    ({"fingerprint": "valuation-xyz"}, False),
    ({"computed_at": ORA + timedelta(hours=6)}, True),
    ({"computed_at": ORA + timedelta(days=1)}, False),
])
def test_d4_cosa_cambia_la_chiave_e_cosa_no(cambia, uguale):
    base = dict(watch_id=3, computed_at=ORA, input_digest="a" * 64, fingerprint="valuation-abc")
    assert (vs.snapshot_idempotency_key(**{**base, **cambia})
            == vs.snapshot_idempotency_key(**base)) is uguale


def test_d5_il_tipo_di_osservazione_e_dichiarato():
    assert vs.SNAPSHOT_OBSERVATION == "valuation_snapshot"
    assert vs.SNAPSHOT_SOURCE == "internal"


# ---------------------------------------------------------------------------
# E - il read-model
# ---------------------------------------------------------------------------

def snapshot(price, quando, fingerprint=None, eur_mq=1900):
    return {"observation_type": "valuation_snapshot", "observed_at": quando,
            "payload": {"price_exact": price, "eur_mq_finale": eur_mq, "base_mq": 1500,
                        "computed_at": quando, "reason": "scheduled",
                        "algorithm_fingerprint": fingerprint or vs.ALGORITHM_FINGERPRINT,
                        "input_digest": "a" * 64}}


def test_e1_ora_lo_storico_del_valore_esiste_ed_e_solo_lo_snapshot():
    assert home_service.VALUATION_HISTORY_OBSERVATIONS == frozenset({"valuation_snapshot"})


def test_e2_current_value_e_l_ultimo_snapshot():
    vista = home_service.build_home_detail(
        stima={"id": 501}, watch={"status": "active"},
        observations=[
            {"observation_type": "watch_started", "observed_at": "2026-01-01T00:00:00+00:00"},
            snapshot(190000, "2026-06-01T00:00:00+00:00"),
            snapshot(195000, "2026-09-01T00:00:00+00:00"),
        ],
        baseline_payload={"price_exact": 185000}, completed_payload=None)

    assert vista["valuation"]["initial_value"] == 185000, "la baseline non si tocca"
    assert vista["valuation"]["current_value"] == 195000
    assert vista["valuation"]["current_value_status"] == "available"
    assert vista["capabilities"]["valuation_history"] is True
    assert vista["history"]["history_status"] == "available"


def test_e3_senza_snapshot_niente_valore_corrente():
    vista = home_service.build_home_detail(
        stima={"id": 501}, watch={"status": "active"},
        observations=[
            {"observation_type": "watch_started", "observed_at": "2026-01-01T00:00:00+00:00"},
            {"observation_type": "buyer_pressure_snapshot", "observed_at": "2026-09-01T00:00:00+00:00"},
            {"observation_type": "internal_supply_snapshot", "observed_at": "2026-09-02T00:00:00+00:00"},
            {"observation_type": "microzone_price_changed", "observed_at": "2026-09-03T00:00:00+00:00"},
        ],
        baseline_payload={"price_exact": 185000}, completed_payload=None)

    assert vista["valuation"]["current_value"] is None
    assert vista["valuation"]["current_value_status"] == "history_not_available"
    assert vista["capabilities"]["valuation_history"] is False
    assert vista["history"]["history_status"] == "building"
    assert vista["history"]["observation_count"] == 4, "il monitoraggio resta contato"
    assert vista["valuation_history"] == []


def test_e4_lo_storico_espone_solo_snapshot_reali():
    vista = home_service.build_home_detail(
        stima={"id": 501}, watch={"status": "active"},
        observations=[
            {"observation_type": "watch_started", "observed_at": "2026-01-01T00:00:00+00:00"},
            snapshot(190000, "2026-06-01T00:00:00+00:00"),
            {"observation_type": "buyer_pressure_snapshot", "observed_at": "2026-07-01T00:00:00+00:00",
             "payload": {"score": 82, "budget_reference": 210000}},
            snapshot(195000, "2026-09-01T00:00:00+00:00"),
        ],
        baseline_payload={"price_exact": 185000}, completed_payload=None)

    storico = vista["valuation_history"]
    assert [p["price_exact"] for p in storico] == [190000, 195000], "in ordine, e solo snapshot"
    for punto in storico:
        assert set(punto) == {"computed_at", "price_exact", "eur_mq_finale",
                              "algorithm_fingerprint"}
    testo = repr(storico).lower()
    for vietato in ("82", "210000", "budget", "score", "input_digest", "reason"):
        assert vietato not in testo, vietato


def test_e5_nessuna_interpolazione_ne_punto_sintetico():
    vista = home_service.build_home_detail(
        stima={"id": 501}, watch={"status": "active"},
        observations=[snapshot(195000, "2026-09-01T00:00:00+00:00")],
        baseline_payload={"price_exact": 185000}, completed_payload=None)
    assert len(vista["valuation_history"]) == 1, "un solo snapshot, un solo punto"
    assert vista["valuation"]["initial_value"] == 185000
    assert 185000 not in [p["price_exact"] for p in vista["valuation_history"]], \
        "la baseline non e' un punto dello storico"


# ---------------------------------------------------------------------------
# F - 30 / 90 / 365 e la metodologia
# ---------------------------------------------------------------------------

def test_f1_senza_storia_abbastanza_vecchia_le_variazioni_sono_null():
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    vista = home_service.build_home_detail(
        stima={"id": 501}, watch={"status": "active"},
        observations=[snapshot(195000, (adesso - timedelta(days=5)).isoformat())],
        baseline_payload={"price_exact": 185000}, completed_payload=None)
    for periodo in ("change_30d", "change_90d", "change_365d"):
        assert vista["valuation"][periodo] is None, periodo


def test_f2_la_variazione_si_calcola_solo_su_snapshot_reali():
    """Per ogni finestra si prende il PIU' RECENTE fra gli snapshot piu'
    vecchi del confine: quello che dice "com'era allora" senza andare
    indietro piu' del necessario."""
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    vista = home_service.build_home_detail(
        stima={"id": 501}, watch={"status": "active"},
        observations=[
            snapshot(170000, (adesso - timedelta(days=400)).isoformat()),
            snapshot(180000, (adesso - timedelta(days=200)).isoformat()),
            snapshot(190000, (adesso - timedelta(days=40)).isoformat()),
            snapshot(200000, adesso.isoformat()),
        ],
        baseline_payload={"price_exact": 185000}, completed_payload=None)

    trenta = vista["valuation"]["change_30d"]
    assert trenta["from_value"] == 190000 and trenta["to_value"] == 200000
    assert trenta["change_percent"] == pytest.approx(5.26, abs=0.01)
    assert trenta["methodology_changed"] is False
    assert trenta["from_computed_at"] == (adesso - timedelta(days=40)).isoformat()

    novanta = vista["valuation"]["change_90d"]
    assert novanta["from_value"] == 180000, "il piu' recente fra quelli oltre i 90 giorni"
    assert novanta["change_percent"] == pytest.approx(11.11, abs=0.01)

    anno = vista["valuation"]["change_365d"]
    assert anno["from_value"] == 170000
    assert anno["change_percent"] == pytest.approx(17.65, abs=0.01)


def test_f2b_una_finestra_senza_snapshot_abbastanza_vecchio_resta_null():
    """Gli stessi dati, senza il punto a 400 giorni: l'anno non si puo' dire."""
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    vista = home_service.build_home_detail(
        stima={"id": 501}, watch={"status": "active"},
        observations=[
            snapshot(180000, (adesso - timedelta(days=200)).isoformat()),
            snapshot(200000, adesso.isoformat()),
        ],
        baseline_payload={"price_exact": 185000}, completed_payload=None)
    assert vista["valuation"]["change_30d"]["from_value"] == 180000
    assert vista["valuation"]["change_90d"]["from_value"] == 180000
    assert vista["valuation"]["change_365d"] is None


def test_f3_la_baseline_non_viene_usata_come_punto_di_partenza():
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    vista = home_service.build_home_detail(
        stima={"id": 501}, watch={"status": "active"},
        observations=[
            {"observation_type": "watch_started",
             "observed_at": (adesso - timedelta(days=400)).isoformat(),
             "payload": {"price_exact": 185000}},
            snapshot(200000, adesso.isoformat()),
        ],
        baseline_payload={"price_exact": 185000}, completed_payload=None)
    assert vista["valuation"]["change_365d"] is None, \
        "la baseline e' vecchia ma non e' uno snapshot"


def test_f4_metodologia_diversa_niente_percentuale():
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    vista = home_service.build_home_detail(
        stima={"id": 501}, watch={"status": "active"},
        observations=[
            snapshot(180000, (adesso - timedelta(days=40)).isoformat(),
                     fingerprint="valuation-vecchia"),
            snapshot(200000, adesso.isoformat(), fingerprint="valuation-nuova"),
        ],
        baseline_payload={"price_exact": 185000}, completed_payload=None)

    trenta = vista["valuation"]["change_30d"]
    assert trenta["methodology_changed"] is True
    assert trenta["change_percent"] is None, "due metodi diversi non si sottraggono"
    assert trenta["from_value"] == 180000 and trenta["to_value"] == 200000


def test_f5_stessa_metodologia_percentuale_presente():
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    vista = home_service.build_home_detail(
        stima={"id": 501}, watch={"status": "active"},
        observations=[
            snapshot(180000, (adesso - timedelta(days=40)).isoformat(), fingerprint="x"),
            snapshot(200000, adesso.isoformat(), fingerprint="x"),
        ],
        baseline_payload={"price_exact": 185000}, completed_payload=None)
    trenta = vista["valuation"]["change_30d"]
    assert trenta["methodology_changed"] is False
    assert trenta["change_percent"] == pytest.approx(11.11, abs=0.01)


# ---------------------------------------------------------------------------
# FA - l'ancora delle finestre e' l'ultimo snapshot, non l'orologio
# ---------------------------------------------------------------------------

def _detail(observations):
    return home_service.build_home_detail(
        stima={"id": 501}, watch={"status": "active"}, observations=observations,
        baseline_payload={"price_exact": 185000}, completed_payload=None)


def test_fa1_ancora_ultimo_snapshot_calcolato_oggi():
    """A - l'ultimo snapshot e' di oggi: la finestra e' quella di sempre."""
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    vista = _detail([
        snapshot(180000, (adesso - timedelta(days=40)).isoformat()),
        snapshot(200000, adesso.isoformat()),
    ])
    trenta = vista["valuation"]["change_30d"]
    assert trenta["from_value"] == 180000 and trenta["to_value"] == 200000
    assert trenta["from_computed_at"] == (adesso - timedelta(days=40)).isoformat()
    assert trenta["to_computed_at"] == adesso.isoformat()


def test_fa2_ancora_ultimo_snapshot_vecchio_di_40_giorni():
    """B - l'ultimo ricalcolo e' di 40 giorni fa.

    Il confine dei 30 giorni e' `40 + 30 = 70 giorni fa`, non `30 giorni fa`.
    Lo snapshot di 80 giorni fa lo supera, quello di 40 (che E' l'ultimo) no:
    il confronto e' 80gg -> 40gg. Ancorando all'orologio, invece, il confine
    cadrebbe a 30 giorni fa e il punto di 40 giorni fa - cioe' lo stesso
    ultimo snapshot - risulterebbe "vecchio abbastanza".
    """
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    ottanta = (adesso - timedelta(days=80)).isoformat()
    quaranta = (adesso - timedelta(days=40)).isoformat()
    vista = _detail([snapshot(180000, ottanta), snapshot(200000, quaranta)])

    trenta = vista["valuation"]["change_30d"]
    assert trenta is not None
    assert trenta["from_computed_at"] == ottanta, "deve usare lo snapshot di 80 giorni fa"
    assert trenta["from_computed_at"] != quaranta, "l'ultimo non e' mai anche il FROM"
    assert trenta["from_value"] == 180000 and trenta["to_value"] == 200000
    assert trenta["to_computed_at"] == quaranta


def test_fa2b_un_punto_fra_i_due_confini_non_e_abbastanza_vecchio():
    """Lo stesso caso B con un intruso a 50 giorni fa.

    Confine ancorato: `40 + 30 = 70 giorni fa`. Il punto di 50 giorni fa sta
    DENTRO la finestra, quindi non puo' rappresentare "com'era 30 giorni
    prima": il FROM resta quello di 80 giorni fa. Ancorando all'orologio il
    confine sarebbe stato "30 giorni fa" e sarebbe stato scelto l'intruso,
    etichettando come "variazione a 30 giorni" un intervallo di 10.
    """
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    ottanta = (adesso - timedelta(days=80)).isoformat()
    cinquanta = (adesso - timedelta(days=50)).isoformat()
    vista = _detail([
        snapshot(180000, ottanta),
        snapshot(192000, cinquanta),
        snapshot(200000, (adesso - timedelta(days=40)).isoformat()),
    ])
    trenta = vista["valuation"]["change_30d"]
    assert trenta["from_computed_at"] == ottanta
    assert trenta["from_computed_at"] != cinquanta, \
        "50 giorni fa e' dentro la finestra ancorata, non prima di essa"
    assert trenta["from_value"] == 180000


def test_fa3_ancora_vecchia_e_storia_insufficiente_da_null_non_zero():
    """C - un solo snapshot, vecchio di 40 giorni: non c'e' variazione da dire."""
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    vista = _detail([snapshot(200000, (adesso - timedelta(days=40)).isoformat())])
    for periodo in ("change_30d", "change_90d", "change_365d"):
        assert vista["valuation"][periodo] is None, periodo
    assert vista["valuation"]["current_value"] == 200000, \
        "il valore corrente esiste comunque: e' la VARIAZIONE che non si puo' dire"


def test_fa3b_storia_tutta_dentro_la_finestra_ancorata_da_null():
    """C, nella forma che distingue le due ancore: due snapshot, 50 e 40
    giorni fa. Il confine e' 70 giorni fa e nessuno dei due lo supera, quindi
    `change_30d` e' `None`. Con l'orologio sarebbe uscito un numero che
    descrive 10 giorni chiamandoli 30."""
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    vista = _detail([
        snapshot(195000, (adesso - timedelta(days=50)).isoformat()),
        snapshot(200000, (adesso - timedelta(days=40)).isoformat()),
    ])
    assert vista["valuation"]["change_30d"] is None
    assert vista["valuation"]["change_30d"] != 0, "null, non zero per cento"
    assert len(vista["valuation_history"]) == 2, "i due punti restano visibili"


def test_fa4_finestra_piu_lunga_dell_ancora_resta_null():
    """A 90 giorni il confine e' `40 + 90 = 130 giorni fa`: il punto di 100
    giorni fa non lo supera, quindi `change_90d` e' `None`.

    E' il caso che separa le due ancore: con l'orologio il confine sarebbe
    stato "90 giorni fa" e quel punto sarebbe entrato, producendo una
    variazione "su 90 giorni" che in realta' ne copre 60.
    """
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    vista = _detail([
        snapshot(180000, (adesso - timedelta(days=100)).isoformat()),
        snapshot(200000, (adesso - timedelta(days=40)).isoformat()),
    ])
    assert vista["valuation"]["change_30d"] is not None, "100 > 40 + 30: questo entra"
    assert vista["valuation"]["change_90d"] is None, "100 < 40 + 90: questo no"
    assert vista["valuation"]["change_365d"] is None


def test_fa5_il_risultato_non_dipende_dal_momento_della_lettura():
    """La stessa storia letta "dopo" da lo stesso identico blocco valuation.

    E' la proprieta' che l'ancora garantisce: nessun campo si muove da solo
    mentre il proprietario non guarda.
    """
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    osservazioni = [
        snapshot(180000, (adesso - timedelta(days=80)).isoformat()),
        snapshot(200000, (adesso - timedelta(days=40)).isoformat()),
    ]
    assert _detail(osservazioni)["valuation"] == _detail(list(osservazioni))["valuation"]


def test_fa6_la_baseline_non_diventa_il_from_nemmeno_con_ancora_vecchia():
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    vista = _detail([
        {"observation_type": "watch_started",
         "observed_at": (adesso - timedelta(days=400)).isoformat(),
         "payload": {"price_exact": 185000}},
        snapshot(200000, (adesso - timedelta(days=40)).isoformat()),
    ])
    for periodo in ("change_30d", "change_90d", "change_365d"):
        assert vista["valuation"][periodo] is None, periodo


def test_fa7_metodologia_invariata_con_ancora_vecchia():
    """E - due snapshot vecchi ma con la stessa impronta: la percentuale c'e'."""
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    vista = _detail([
        snapshot(180000, (adesso - timedelta(days=80)).isoformat(), fingerprint="x"),
        snapshot(200000, (adesso - timedelta(days=40)).isoformat(), fingerprint="x"),
    ])
    trenta = vista["valuation"]["change_30d"]
    assert trenta["methodology_changed"] is False
    assert trenta["change_percent"] == pytest.approx(11.11, abs=0.01)


def test_fa8_metodologia_cambiata_con_ancora_vecchia():
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    vista = _detail([
        snapshot(180000, (adesso - timedelta(days=80)).isoformat(), fingerprint="vecchia"),
        snapshot(200000, (adesso - timedelta(days=40)).isoformat(), fingerprint="nuova"),
    ])
    trenta = vista["valuation"]["change_30d"]
    assert trenta["methodology_changed"] is True
    assert trenta["change_percent"] is None
    assert trenta["from_value"] == 180000 and trenta["to_value"] == 200000


# ---------------------------------------------------------------------------
# FB - il timestamp del valore corrente
# ---------------------------------------------------------------------------

def test_fb1_current_value_computed_at_e_la_data_dell_ultimo_snapshot():
    """D - con snapshot: la data c'e', ed e' quella dell'ultimo."""
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    quaranta = (adesso - timedelta(days=40)).isoformat()
    vista = _detail([
        snapshot(180000, (adesso - timedelta(days=80)).isoformat()),
        snapshot(200000, quaranta),
    ])
    valutazione = vista["valuation"]
    assert valutazione["current_value"] == 200000
    assert valutazione["current_value_status"] == "available"
    assert valutazione["current_value_computed_at"] == quaranta
    assert valutazione["current_value_computed_at"] == \
        vista["valuation_history"][-1]["computed_at"], "la stessa data dello storico"


def test_fb2_senza_snapshot_il_timestamp_e_null():
    """D - senza snapshot: `None`, come il valore corrente."""
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    vista = _detail([{"observation_type": "microzone_price_changed",
                      "observed_at": adesso.isoformat()}])
    valutazione = vista["valuation"]
    assert valutazione["current_value"] is None
    assert valutazione["current_value_status"] == "history_not_available"
    assert valutazione["current_value_computed_at"] is None
    assert valutazione["initial_value"] == 185000, "il valore originario resta"


def test_fb3_nessuno_stato_di_freschezza_inventato():
    """Un timestamp, non un giudizio: niente `stale`, `fresh`, `age_days`."""
    adesso = datetime(2026, 9, 19, tzinfo=timezone.utc)
    valutazione = _detail([snapshot(200000, adesso.isoformat())])["valuation"]
    atteso = {"initial_value", "initial_value_source", "current_value",
              "current_value_status", "current_value_computed_at",
              "change_30d", "change_90d", "change_365d"}
    assert set(valutazione) == atteso, "nessun campo in piu' rispetto a quelli concordati"


# ---------------------------------------------------------------------------
# G - il perimetro
# ---------------------------------------------------------------------------

def test_g1_nessuna_rotta_nuova_sul_router_property_watch():
    sorgente = (ROOT / "property_watch" / "router.py").read_text(encoding="utf-8")
    assert "valuation" not in sorgente.lower()


def test_g2_nessuna_migration_in_lmc3():
    """SENTINELLA AGGIORNATA DA LMC-10.

    LMC-3 non ha creato schema e continua a non averne bisogno:
    `observation_type` e' VARCHAR(100) senza CHECK, ed e' la ragione per cui
    lo snapshot del valore non richiese una migration. La 068 e' di LMC-10,
    approvata dallo STORAGE GATE: si nomina invece di smettere di guardare,
    cosi' una migration inattesa farebbe ancora fallire il test.
    """
    migrazioni = sorted(p.name for p in (ROOT / "migrations").glob("*.sql")
                        if not p.name.endswith("_down.sql"))
    # SENTINELLA AGGIORNATA DA LMC-12: la 069 e' lo stream di notifiche
    # PRE-INCARICO `owner_home_notifications`, approvata dal DESIGN GATE di
    # LMC-12 (dominio OWNER, radice `stime` + `owner_stima_access`). Si nomina
    # invece di smettere di guardare: qualunque ALTRA migration comparisse
    # farebbe ancora fallire questo test.
    # SENTINELLA AGGIORNATA DA LMC-15: la 070 e' il ponte di acquisizione
    # (`stima_acquisitions`, `stima_inspections`), approvato dallo SCHEMA
    # GATE di LMC-15A.2. Si nomina invece di smettere di guardare.
    # SENTINELLA AGGIORNATA DA P29-3B: la 071 e' la fondazione delle journey
    # (`communication_journeys`, `_journey_steps`, `_enrollments`,
    # `_automation_controls` + tre colonne di provenienza sul ledger),
    # approvata da P29-3A.1 SCHEMA FROZEN. Si nomina invece di smettere di
    # guardare: qualunque ALTRA migration comparisse farebbe ancora fallire.
    assert migrazioni[-5:] == ["067_lmc1b_owner_login_reason.sql",
                               "068_lmc10_owner_home_overrides.sql",
                               "069_lmc12_owner_home_notifications.sql",
                               "070_lmc15_acquisition_bridge.sql",
                               "071_p29_3_journey_automation.sql"], migrazioni[-6:]


DOMINI_VIETATI_LMC3 = (
    "main.py", "communication/", "operator_auth/", "seller_intelligence/",
    "seller_intent/", "next_best_action/", "followup/", "static/", "crm/",
    "migrations/",
)


def test_g3_lmc3_non_tocca_i_domini_vietati():
    """Il commit di LMC-3 non ha toccato nessuno di questi domini.

    CORRETTO IN LMC-6 (collisione segnalata). La versione originale guardava
    `git diff`, cioe' il WORKING TREE: andava bene finche' LMC-3 era l'unico
    lavoro non committato, ma da allora il significato e' cambiato sotto i
    piedi al test. Con LMC-3 committato, `git diff` non mostra piu' le
    modifiche di LMC-3 - mostra quelle delle fasi successive, e LMC-6 ha il
    mandato esplicito di lavorare su `static/owner_portal`. Cosi' com'era,
    il test avrebbe accusato LMC-3 di una modifica fatta da qualcun altro
    tre fasi dopo.

    La garanzia non cambia, cambia il soggetto: si guarda il commit che ha
    introdotto LMC-3, che e' immutabile, invece dello stato di lavoro
    condiviso. Se quel commit non si trova (storia troncata), il test si
    salta invece di dare un verde che non ha verificato niente.
    """
    import subprocess

    def git(*argomenti):
        return subprocess.run(["git", "--no-optional-locks", *argomenti],
                              cwd=ROOT, capture_output=True, text=True).stdout.strip()

    commit = git("log", "--diff-filter=A", "--format=%H", "-1", "--",
                 "property_watch/valuation_snapshot.py")
    if not commit:
        pytest.skip("commit LMC-3 non trovato nella storia")
    toccati = git("show", "--name-only", "--format=", commit, "--",
                  *DOMINI_VIETATI_LMC3)
    assert toccati == "", toccati


def test_g4_il_servizio_scoped_non_ha_una_gemella_senza_contesto():
    from property_watch import service
    assert hasattr(service, "refresh_valuation_snapshot_scoped")
    assert not hasattr(service, "refresh_valuation_snapshot")
    firma = inspect.signature(service.refresh_valuation_snapshot_scoped)
    assert "agency_id" not in firma.parameters
    assert list(firma.parameters)[:2] == ["ctx", "stima_id"]
