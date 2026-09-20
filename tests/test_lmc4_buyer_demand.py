"""LMC-4: la sezione Buyer Demand del portale proprietario, in memoria.

Qui si prova la TRADUZIONE: da metriche interne di Buyer Pressure a un blocco
che un proprietario puo' leggere. Il calcolo non e' in discussione - e' quello
di P21-A/P21-B e non viene toccato - quello che si prova e' cosa ne esce e,
soprattutto, cosa NON ne esce.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
import unicodedata
from decimal import Decimal
from pathlib import Path

import pytest

from owner import demand as buyer_demand
from owner import home_service
from property_watch import buyer_pressure, buyer_pressure_score

ROOT = Path(__file__).resolve().parents[1]


def metriche(compatibili=8, altamente=3, recenti=3, evaluated=20,
             media="72.00", massimo="88.00"):
    """Una rilevazione come la legge il repository: senza `average_budget`."""
    return {
        "evaluated_buyers": evaluated,
        "compatible_buyers": compatibili,
        "highly_compatible_buyers": altamente,
        "recent_compatible_buyers_30d": recenti,
        "average_match_score": None if media is None else Decimal(media),
        "maximum_match_score": None if massimo is None else Decimal(massimo),
        "algorithm_version": "match-1.0",
    }


def rilevazione(quando="2026-09-18T09:00:00+00:00", **kw):
    return {"metrics": metriche(**kw), "observed_at": quando}


def detail(buyer=None, osservazioni=None):
    return home_service.build_home_detail(
        stima={"id": 501, "comune": "Alba Adriatica"},
        watch={"status": "active"},
        observations=osservazioni or [],
        baseline_payload={"price_exact": 185000},
        completed_payload=None,
        buyer_pressure=buyer,
    )


# ---------------------------------------------------------------------------
# A - il blocco esiste e segue il dato reale
# ---------------------------------------------------------------------------

def test_a1_buyer_pressure_presente_blocco_disponibile():
    blocco = detail(rilevazione())["buyer_demand"]
    assert blocco["status"] in ("high", "medium", "low")
    assert blocco["compatible_requests"] == 8
    assert blocco["recent_compatible_requests"] == 3
    assert blocco["updated_at"] == "2026-09-18T09:00:00+00:00"
    assert blocco["disclaimer"]
    assert blocco["label"] and blocco["message"]


def test_a2_buyer_pressure_assente_unavailable_e_capability_false():
    vista = detail(None)
    blocco = vista["buyer_demand"]
    assert blocco["status"] == "unavailable"
    assert blocco["compatible_requests"] is None
    assert blocco["recent_compatible_requests"] is None
    assert blocco["updated_at"] is None
    assert blocco["disclaimer"], "il disclaimer c'e' anche quando il dato manca"
    assert vista["capabilities"]["buyer_demand"] is False


def test_a3_capability_true_solo_con_un_dato_reale():
    assert detail(rilevazione())["capabilities"]["buyer_demand"] is True
    assert detail(None)["capabilities"]["buyer_demand"] is False


def test_a4_la_forma_del_blocco_e_sempre_la_stessa():
    """Chiavi stabili: il frontend non deve indovinare se un campo c'e'."""
    atteso = {"status", "label", "message", "compatible_requests",
              "recent_compatible_requests", "recency_days", "updated_at", "disclaimer"}
    assert set(detail(rilevazione())["buyer_demand"]) == atteso
    assert set(detail(None)["buyer_demand"]) == atteso


def test_a5_una_rilevazione_illeggibile_non_e_un_errore_ma_unavailable():
    """Fail-closed: un payload che non e' una rilevazione canonica non puo'
    far esplodere il portale ne' uscire cosi' com'e'."""
    for rotta in ({"metrics": {"score": 82, "budget_reference": 210000},
                   "observed_at": "2026-09-18T09:00:00+00:00"},
                  {"metrics": {}, "observed_at": None},
                  {"metrics": None, "observed_at": "2026-09-18T09:00:00+00:00"},
                  {"metrics": metriche(), "observed_at": None}):
        blocco = detail(rotta)["buyer_demand"]
        assert blocco["status"] == "unavailable", rotta
        testo = json.dumps(blocco, default=str).lower()
        for vietato in ("82", "210000", "budget", "score"):
            assert vietato not in testo, (vietato, rotta)


# ---------------------------------------------------------------------------
# B - le tre fasce, derivate dal dominio esistente
# ---------------------------------------------------------------------------

def test_b1_status_high():
    blocco = detail(rilevazione(compatibili=10, altamente=5, recenti=8,
                                media="90.00", massimo="98.00"))["buyer_demand"]
    assert blocco["status"] == "high"
    assert blocco["label"] == "Domanda alta"


def test_b2_status_medium():
    blocco = detail(rilevazione(compatibili=6, altamente=2, recenti=3,
                                media="70.00", massimo="80.00"))["buyer_demand"]
    assert blocco["status"] == "medium"
    assert blocco["label"] == "Domanda media"


def test_b3_status_low():
    blocco = detail(rilevazione(compatibili=1, altamente=0, recenti=0,
                                media="56.00", massimo="56.00"))["buyer_demand"]
    assert blocco["status"] == "low"
    assert blocco["label"] == "Domanda bassa"


def test_b4_lo_status_e_quello_della_banda_del_dominio():
    """Nessuna soglia nuova: per ogni punteggio possibile la fascia esposta e'
    quella che `derive_buyer_pressure_insight` ha gia' deciso."""
    assert set(buyer_demand.BAND_TO_STATUS) == {"none", "low", "medium", "high"}, \
        "le bande del dominio sono cambiate: rivedere la mappatura"
    for banda, atteso in (("high", "high"), ("medium", "medium"), ("low", "low")):
        assert buyer_demand.BAND_TO_STATUS[banda] == atteso


def test_b5_zero_richieste_compatibili_e_il_fondo_della_scala_non_un_buco():
    """La banda `none` del dominio non e' prevista fra gli status ammessi.

    Mappata sul fondo della scala (`low`), non su `unavailable`: il dato
    ESISTE e dice zero, e dire "non disponibile" sarebbe falso. Etichetta e
    messaggio restano quelli del dominio per la banda `none`, quindi il
    proprietario legge "Nessuna domanda rilevata" e non "Domanda bassa".
    """
    blocco = detail(rilevazione(compatibili=0, altamente=0, recenti=0,
                                evaluated=14, media=None, massimo=None))["buyer_demand"]
    assert buyer_demand.BAND_TO_STATUS["none"] == "low"
    assert blocco["status"] == "low"
    assert blocco["label"] == "Nessuna domanda rilevata"
    assert blocco["compatible_requests"] == 0
    assert blocco["recent_compatible_requests"] == 0


# ---------------------------------------------------------------------------
# BC - la copy: owner-safe in ogni caso, e indipendente dal dominio
# ---------------------------------------------------------------------------

#: Termini che non devono comparire in nessun testo mostrato al proprietario:
#: nomi di moduli e di domini interni, il punteggio tecnico e il vocabolario
#: con cui e' costruito.
GERGO_INTERNO = (
    "buy", "match", "flow", "score", "punteggio", "/100",
    "pressure", "pressione", "algorithm", "algoritmo", "fingerprint",
    "factor", "fattore", "metric", "metrica", "digest", "payload",
    "snapshot", "watch", "band", "version", "evaluated", "budget",
    "compatibility", "readiness", "engine", "internal", "interno",
)

#: I cinque casi che il blocco puo' assumere: le quattro bande del dominio
#: piu' l'assenza di rilevazione. Le metriche sono scelte per cadere ognuna
#: nella propria banda (verificato da `test_bc2`).
CASI = {
    "high": dict(compatibili=10, altamente=5, recenti=8, media="90.00", massimo="98.00"),
    "medium": dict(compatibili=6, altamente=2, recenti=3, media="70.00", massimo="80.00"),
    "low": dict(compatibili=1, altamente=0, recenti=0, media="56.00", massimo="56.00"),
    "none": dict(compatibili=0, altamente=0, recenti=0, media=None, massimo=None),
    "unavailable": None,
}


def _blocco_del_caso(caso):
    parametri = CASI[caso]
    return detail(None if parametri is None else rilevazione(**parametri))["buyer_demand"]


@pytest.mark.parametrize("caso", list(CASI))
def test_bc1_nessun_gergo_interno_in_nessuno_dei_cinque_casi(caso):
    """Il test richiesto dalla rifinitura: si serializza ogni caso e si
    cerca il vocabolario interno in TUTTO cio' che ne esce, chiavi comprese."""
    blocco = _blocco_del_caso(caso)
    testo = json.dumps(blocco, ensure_ascii=False, default=str).lower()
    for termine in GERGO_INTERNO:
        assert termine not in testo, (caso, termine)


@pytest.mark.parametrize("caso", list(CASI))
def test_bc2_ogni_caso_produce_davvero_la_sua_fascia(caso):
    """Senza questo, `test_bc1` potrebbe passare provando cinque volte lo
    stesso blocco."""
    blocco = _blocco_del_caso(caso)
    atteso = {"high": "high", "medium": "medium", "low": "low",
              "none": "low", "unavailable": "unavailable"}[caso]
    assert blocco["status"] == atteso
    etichette = {"high": "Domanda alta", "medium": "Domanda media",
                 "low": "Domanda bassa", "none": "Nessuna domanda rilevata",
                 "unavailable": "Domanda non disponibile"}
    assert blocco["label"] == etichette[caso]


def test_bc3_la_copy_e_dell_adapter_non_del_dominio():
    """Le parole non arrivano piu' da P21-B.

    Il dominio resta la fonte della FASCIA; le frasi sono di qui, perche' un
    domani qualcuno potrebbe aggiungere una sigla a un messaggio interno
    senza sapere che finisce sotto gli occhi di un proprietario. Il caso
    `none` lo dimostra gia' oggi: il messaggio del dominio nomina un modulo.
    """
    messaggi_dominio = {banda[2]: banda[5] for banda in buyer_pressure_score._BANDS}
    assert "buy" in messaggi_dominio["none"].lower(), \
        "il messaggio interno per `none` nominava un modulo: se non lo fa piu', " \
        "questa motivazione va riscritta"
    for banda, (_etichetta, messaggio) in buyer_demand.OWNER_COPY.items():
        if banda == "none":
            assert messaggio != messaggi_dominio[banda], "non si inoltra quella frase"
        assert messaggio not in messaggi_dominio.values() or banda != "none"


def test_bc4_ogni_banda_del_dominio_ha_una_copy_e_uno_status():
    """Se il dominio aggiungesse una banda, questo test cade subito invece di
    lasciare che il portale la incontri in produzione."""
    bande = {b[2] for b in buyer_pressure_score._BANDS}
    assert bande == set(buyer_demand.OWNER_COPY), bande ^ set(buyer_demand.OWNER_COPY)
    assert bande == set(buyer_demand.BAND_TO_STATUS)


def test_bc5_una_banda_sconosciuta_degrada_a_unavailable(monkeypatch):
    """La difesa vera contro il gergo futuro: se domani arrivasse una banda
    senza copy, non si inoltra la frase del dominio - si dice che il dato non
    e' disponibile."""
    vero = buyer_pressure_score.derive_buyer_pressure_insight

    def finto(metriche):
        return {**vero(metriche), "band": "iper",
                "band_label": "DOMANDA IPER \u2014 120/100",
                "message": "score MATCH fuori scala sulle richieste BUY"}

    monkeypatch.setattr(buyer_demand.fascia, "derive_buyer_pressure_insight", finto)
    blocco = detail(rilevazione())["buyer_demand"]
    assert blocco["status"] == "unavailable"
    testo = json.dumps(blocco, ensure_ascii=False).lower()
    for termine in GERGO_INTERNO:
        assert termine not in testo, termine


def test_bc6_tutte_le_costanti_di_testo_sono_owner_safe():
    """Non solo cio' che i cinque casi producono: ogni stringa che l'adapter
    potrebbe mostrare."""
    testi = [buyer_demand.DISCLAIMER, buyer_demand.UNAVAILABLE_LABEL,
             buyer_demand.UNAVAILABLE_MESSAGE]
    for etichetta, messaggio in buyer_demand.OWNER_COPY.values():
        testi += [etichetta, messaggio]
    for testo in testi:
        for termine in GERGO_INTERNO:
            assert termine not in testo.lower(), (termine, testo)


def test_bc7_i_messaggi_hanno_tutti_la_stessa_forma():
    """Coerenza: le quattro bande parlano del database STIMA360 e di immobili
    simili, con la stessa voce. Solo il caso "non disponibile" fa eccezione,
    perche' non sta descrivendo una domanda."""
    for banda, (_etichetta, messaggio) in buyer_demand.OWNER_COPY.items():
        assert "STIMA360" in messaggio, banda
        assert "simili" in messaggio.lower(), banda
        assert messaggio.endswith("."), banda
    assert "STIMA360" not in buyer_demand.UNAVAILABLE_MESSAGE


# ---------------------------------------------------------------------------
# C - cosa non deve uscire
# ---------------------------------------------------------------------------

def _testo(blocco):
    return json.dumps(blocco, default=str).lower()


@pytest.mark.parametrize("vietato", [
    "score", "pressure", "punteggio", "factor", "fattor", "band", "version",
    "buy_request", "match_id", "ranking", "evaluated", "budget",
    "email", "telefono", "nome", "cognome", "note",
])
def test_c1_nessuna_parola_interna_nel_blocco(vietato):
    assert vietato not in _testo(detail(rilevazione())["buyer_demand"])


def test_c2_il_punteggio_interno_non_compare_in_nessuna_forma():
    """Ne' il numero, ne' una chiave che lo nomini, ne' i fattori che lo
    compongono - che sono la ricetta con cui e' stato costruito."""
    insight = buyer_pressure_score.derive_buyer_pressure_insight(
        {**metriche(), "average_budget": None})
    blocco = detail(rilevazione())["buyer_demand"]
    # Il punteggio e' un numero: cercarlo come sottostringa nel JSON darebbe
    # falsi positivi ("60" vive dentro "stima360"). Si guarda la struttura.
    stringhe = " ".join(v for v in blocco.values() if isinstance(v, str)).lower()
    numeri = [v for v in blocco.values() if isinstance(v, int) and not isinstance(v, bool)]

    assert insight["score"] not in numeri, "il punteggio non e' fra i valori esposti"
    assert not any("score" in chiave for chiave in blocco), "nessuna chiave lo nomina"
    assert "/100" not in stringhe, "ne' la forma 'n/100' dell'headline"
    assert insight["headline"].lower() not in stringhe
    for fattore in insight["factors"]:
        assert fattore["code"] not in stringhe
        assert fattore["label"].lower() not in stringhe
    assert insight["score_version"] not in stringhe
    assert all(not isinstance(v, (list, dict)) for v in blocco.values()), \
        "nessuna struttura annidata in cui nascondere dettagli"


def test_c3_nessun_identificativo_di_buy_o_match():
    blocco = detail(rilevazione())["buyer_demand"]
    for chiave in blocco:
        assert not chiave.endswith("_id"), chiave
        assert "id" != chiave


def test_c4_le_metriche_interne_non_finiscono_nel_dettaglio_intero():
    """Non solo il blocco: l'intera risposta non deve portarle."""
    vista = detail(rilevazione(evaluated=97))
    testo = json.dumps(vista, default=str).lower()
    for vietato in ("97", "evaluated", "88.00", "72.00", "match-1.0", "score"):
        assert vietato not in testo, vietato


def test_c5_average_budget_non_viene_nemmeno_letto():
    """Con UN solo compatibile, la media dei budget E' il budget di quella
    persona. Il modo sicuro di non pubblicarla e' non caricarla: il set di
    metriche che il portale legge non la contiene."""
    assert "average_budget" in buyer_pressure.METRIC_KEYS, "e' una metrica del dominio"
    assert "average_budget" not in buyer_demand.METRICHE_LETTE
    letto = detail(rilevazione(compatibili=1, altamente=0, recenti=1,
                               media="60.00", massimo="60.00"))["buyer_demand"]
    assert "budget" not in _testo(letto)


def _sql_di(funzione):
    """Le stringhe letterali di una funzione: la SQL, senza il docstring.

    Il docstring SPIEGA perche' il budget medio non si seleziona, quindi lo
    nomina: cercare la parola nel sorgente intero direbbe il contrario di
    quello che si vuole provare. Qui si guarda cio' che il database riceve.
    """
    albero = ast.parse(textwrap.dedent(inspect.getsource(funzione)))
    corpo = albero.body[0].body
    inizio = 1 if (isinstance(corpo[0], ast.Expr)
                   and isinstance(corpo[0].value, ast.Constant)
                   and isinstance(corpo[0].value.value, str)) else 0
    return [n.value for ramo in corpo[inizio:] for n in ast.walk(ramo)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_c6_la_query_del_portale_non_seleziona_il_budget():
    from owner import repository as owner_repository
    sql = " ".join(_sql_di(owner_repository.home_buyer_pressure))
    assert "SELECT" in sql, "la query e' stata trovata"
    assert "average_budget" not in sql, "il budget medio non deve uscire da PostgreSQL"
    for chiave in buyer_demand.METRICHE_LETTE:
        assert chiave in sql, f"{chiave} deve essere selezionata esplicitamente"
    assert "payload," not in sql and "o.payload " not in sql, \
        "il payload intero non viene mai selezionato"


# ---------------------------------------------------------------------------
# D - conteggi e recency
# ---------------------------------------------------------------------------

def test_d1_i_conteggi_sono_quelli_del_dominio_non_ricostruiti():
    blocco = detail(rilevazione(compatibili=7, recenti=2))["buyer_demand"]
    assert blocco["compatible_requests"] == 7
    assert blocco["recent_compatible_requests"] == 2


def test_d2_la_recency_e_quella_reale_del_dominio():
    """30 giorni non e' un numero scelto qui: e' la finestra che
    `calculate_buyer_pressure_metrics` usa davvero, ed e' scritta nel nome
    stesso della metrica. Se il dominio la cambiasse, questo test cade."""
    assert "recent_compatible_buyers_30d" in buyer_pressure.METRIC_KEYS
    assert buyer_demand.RECENCY_DAYS == 30
    assert f"_{buyer_demand.RECENCY_DAYS}d" in buyer_demand.RECENT_METRIC
    sorgente = (ROOT / "property_watch" / "buyer_pressure.py").read_text(encoding="utf-8")
    assert "timedelta(days=30)" in sorgente, "la finestra reale del collettore"
    assert detail(rilevazione())["buyer_demand"]["recency_days"] == 30


def test_d3_senza_rilevazione_i_conteggi_sono_null_non_zero():
    blocco = detail(None)["buyer_demand"]
    assert blocco["compatible_requests"] is None
    assert blocco["recent_compatible_requests"] is None
    assert blocco["recency_days"] is None


# ---------------------------------------------------------------------------
# E - il disclaimer
# ---------------------------------------------------------------------------

def test_e1_il_disclaimer_c_e_sempre():
    for caso in (rilevazione(), None):
        assert detail(caso)["buyer_demand"]["disclaimer"].strip()


def _senza_accenti(testo):
    """Minuscolo, senza accenti e senza apostrofi: confronta il TESTO, non la
    tipografia (il dominio scrive "ne'" con l'accento, qui si scrive ASCII)."""
    piatto = unicodedata.normalize("NFKD", testo.lower())
    piatto = "".join(c for c in piatto if not unicodedata.combining(c))
    return piatto.replace("\u2019", "").replace("'", "")


def test_e2_il_disclaimer_riusa_la_clausola_di_garanzia_del_dominio():
    """La frase che nega la garanzia e' quella del dominio, parola per parola.

    Cambia solo il contorno: nel dominio nomina i moduli BUY e MATCH, che
    sono nomenclatura interna e nel portale non devono comparire.
    """
    assert _senza_accenti(buyer_demand.GUARANTEE_CLAUSE) in \
        _senza_accenti(buyer_pressure_score.DISCLAIMER), \
        "la clausola di garanzia non e' piu' quella del dominio"
    assert buyer_demand.GUARANTEE_CLAUSE in buyer_demand.DISCLAIMER
    for interna in ("match", "buy ", "indicatore interno"):
        assert interna not in buyer_demand.DISCLAIMER.lower(), interna


def test_e3_il_disclaimer_non_garantisce_la_vendita():
    testo = buyer_demand.DISCLAIMER.lower()
    assert "non garantisce" in testo
    for promessa in ("garantiamo", "sara' venduto", "sarà venduto", "vendita assicurata",
                     "certamente", "sicuramente"):
        assert promessa not in testo, promessa


def test_e4_il_disclaimer_non_pretende_di_essere_tutto_il_mercato():
    testo = _senza_accenti(buyer_demand.DISCLAIMER)
    assert "tutto il mercato" not in testo
    assert "non rappresenta lintero mercato immobiliare" in testo, \
        "confronto senza apostrofi: la copy usa quello tipografico"
    assert "stima360" in testo


def test_e5_il_messaggio_parla_di_immobili_simili_non_di_questo():
    """Il dominio lo dice gia' bene: la domanda riguarda immobili con
    caratteristiche simili, non un interesse per QUESTA casa."""
    blocco = detail(rilevazione(compatibili=10, altamente=5, recenti=8,
                                media="90.00", massimo="98.00"))["buyer_demand"]
    assert "simili" in blocco["message"].lower()
    assert "stima360" in blocco["message"].lower()


# ---------------------------------------------------------------------------
# F - il perimetro: LMC-4 e' sola lettura
# ---------------------------------------------------------------------------

def test_f1_nessuna_scrittura_nel_percorso_owner():
    for modulo in (buyer_demand,):
        sorgente = inspect.getsource(modulo).lower()
        for vietato in ("insert ", "update ", "delete ", "commit("):
            assert vietato not in sorgente, (modulo.__name__, vietato)


def test_f2_la_lettura_del_portale_non_scrive():
    from owner import repository as owner_repository
    sql = " ".join(_sql_di(owner_repository.home_buyer_pressure)).lower()
    for vietato in ("insert", "update", "delete", "commit"):
        assert vietato not in sql, vietato


def test_f2b_il_portale_non_importa_buy_match_o_flow():
    """La sostanza della sentinella `test_integration_privacy`, verificata
    sugli import veri invece che per sottostringa.

    Quel controllo cerca "import buy" nel testo di `owner/*.py` e un modulo
    che si chiama `buyer_pressure` lo farebbe scattare per omonimia. Qui si
    guarda l'albero sintattico: quali PACCHETTI importa davvero il portale.
    """
    vietati = {"buy", "match", "flow", "crm", "seller_intelligence",
               "next_best_action", "followup"}
    # LMC-7 (collisione autorizzata). Il radar dell'interesse registra il
    # comportamento del proprietario nella timeline di vendita, quindi due
    # file - uno che scrive, uno che legge - devono nominare Seller
    # Intelligence. La dipendenza e' voluta, circoscritta e fail-open:
    # `test_seller_intelligence_isolation` la ammette con la stessa
    # motivazione e ne verifica il confine, e un test su PostgreSQL fa
    # sollevare il dominio per provare che il portale regge.
    consumatori_autorizzati = {"tracking.py", "interest_service.py"}
    for sorgente in sorted(Path(ROOT / "owner").glob("*.py")):
        if sorgente.name in consumatori_autorizzati:
            continue
        albero = ast.parse(sorgente.read_text(encoding="utf-8"))
        for nodo in ast.walk(albero):
            if isinstance(nodo, ast.Import):
                radici = [alias.name.split(".")[0] for alias in nodo.names]
            elif isinstance(nodo, ast.ImportFrom):
                radici = [(nodo.module or "").split(".")[0]]
            else:
                continue
            for radice in radici:
                assert radice not in vietati, f"{sorgente.name} importa {radice}"


def test_f3_l_algoritmo_del_dominio_non_e_stato_toccato():
    """LMC-4 non tocca ne' le metriche ne' il punteggio: le due firme e le
    costanti restano quelle certificate da P21-A/P21-B."""
    assert buyer_pressure.METRIC_KEYS == (
        "evaluated_buyers", "compatible_buyers", "highly_compatible_buyers",
        "recent_compatible_buyers_30d", "average_match_score",
        "maximum_match_score", "average_budget", "algorithm_version")
    assert buyer_pressure_score.SCORE_VERSION == "buyer-pressure-score-1.0"
    bande = [b[2] for b in buyer_pressure_score._BANDS]
    assert bande == ["none", "low", "medium", "high"]


def test_f4_il_budget_assente_non_cambia_la_fascia():
    """La sostituzione `average_budget = None` e' sicura perche' quel campo
    non entra nel punteggio: con o senza, la banda e' la stessa."""
    piene = {**metriche(), "average_budget": Decimal("240000.00")}
    senza = {**metriche(), "average_budget": None}
    a = buyer_pressure_score.derive_buyer_pressure_insight(piene)
    b = buyer_pressure_score.derive_buyer_pressure_insight(senza)
    assert a["score"] == b["score"] and a["band"] == b["band"]
    assert a["message"] == b["message"]
