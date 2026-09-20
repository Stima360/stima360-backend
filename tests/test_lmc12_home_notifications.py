"""LMC-12 - le notifiche in-app PRE-INCARICO "Novita' sulla tua casa".

COSA SI PROVA QUI, SENZA DATABASE.

A. Il rilevatore puro (`owner/home_alerts.py`): le due soglie, il
   riferimento che avanza solo quando deve, la variazione cumulativa, la
   correzione del proprietario che riparte in silenzio, il metodo che cambia,
   la fascia della domanda. Sono i test 1-14 obbligatori della fase.
B. Chiavi, evidence e parole: deterministiche, senza data, senza numeri
   privati, senza "mercato".
C. Il giro (`owner/home_alert_service.py`) con doppi del repository: la
   preferenza spenta, il cursore keyset, l'isolamento dei guasti.
D. Il runner: modalita', pagina, codici di uscita, lock, log chiuso.
E. L'API e il DTO: rotte, whitelist, 404 neutro auditato.
F. Le sentinelle: P5, LMC-2/11, CRM Radar, P29, i domini vietati - invariati.
G. Il frontend, eseguito davvero nell'armatura P6.

Le prove che chiedono PostgreSQL vero (trigger, idempotenza, tenancy,
concorrenza, paginazione, migration up/down/up) stanno in
`test_lmc12_home_notifications_postgres.py`.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import re
import subprocess
from pathlib import Path

import pytest

from owner import demand, home_alert_service, home_alerts, home_update
from owner import repository as owner_repository
from owner.schemas import OwnerHomeNotificationDTO, OwnerNotificationDTO
import run_owner_home_alert_cron as runner

ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations" / "069_lmc12_owner_home_notifications.sql"
DOWN = ROOT / "migrations" / "069_lmc12_owner_home_notifications_down.sql"


def _codice(percorso: Path) -> str:
    """Il codice SENZA docstring ne' commenti: le sentinelle per sottostringa
    cercano cio' che il modulo fa, non cio' che spiega di non fare."""
    albero = ast.parse(percorso.read_text(encoding="utf-8"))
    for nodo in ast.walk(albero):
        if isinstance(nodo, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (nodo.body and isinstance(nodo.body[0], ast.Expr)
                    and isinstance(getattr(nodo.body[0], "value", None), ast.Constant)
                    and isinstance(nodo.body[0].value.value, str)):
                nodo.body = nodo.body[1:] or [ast.Pass()]
    return ast.unparse(albero)


def _codice_funzione(funzione) -> str:
    sorgente = inspect.getsource(funzione)
    return _codice_testo(sorgente)


def _codice_testo(sorgente: str) -> str:
    import textwrap
    albero = ast.parse(textwrap.dedent(sorgente))
    for nodo in ast.walk(albero):
        if isinstance(nodo, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (nodo.body and isinstance(nodo.body[0], ast.Expr)
                    and isinstance(getattr(nodo.body[0], "value", None), ast.Constant)
                    and isinstance(nodo.body[0].value.value, str)):
                nodo.body = nodo.body[1:] or [ast.Pass()]
    return ast.unparse(albero)


def snap(obs_id, prezzo, *, fp="fp-a", reason="scheduled_refresh"):
    return {"observation_id": obs_id, "price_exact": prezzo,
            "algorithm_fingerprint": fp, "reason": reason}


def serie(*prezzi, fp="fp-a"):
    return [snap(i + 1, p, fp=fp) for i, p in enumerate(prezzi)]


def metriche(compatibili, alti=0, recenti=0, valutati=40):
    """Una rilevazione Buyer Pressure minima ma canonica, come la legge il
    portale (LMC-4)."""
    return {"evaluated_buyers": valutati, "compatible_buyers": compatibili,
            "highly_compatible_buyers": alti, "recent_compatible_buyers_30d": recenti,
            "average_match_score": 60 if compatibili else None,
            "maximum_match_score": 80 if compatibili else None,
            "algorithm_version": "p21a-v1"}


def lettura(obs_id, compatibili, **kw):
    return {"observation_id": obs_id, "observed_at": f"2026-09-{obs_id:02d}T09:00:00+00:00",
            "metrics": metriche(compatibili, **kw)}


# ---------------------------------------------------------------------------
# A - IL RILEVATORE DEL VALORE (test obbligatori 1-11)
# ---------------------------------------------------------------------------

def test_a01_snapshot_identici_zero_notifiche():
    assert home_alerts.decide_value_alerts(serie(200000, 200000, 200000, 200000)) == []


def test_a02_quattro_virgola_nove_percento_e_diecimila_euro_zero():
    # 300.000 -> 314.700: +4,9% e +14.700 EUR. La percentuale manca.
    assert home_alerts.decide_value_alerts(serie(300000, 314700)) == []


def test_a03_otto_percento_ma_quattromila_novecento_novantanove_euro_zero():
    # 62.000 -> 66.999: +8,06% e +4.999 EUR. Gli euro mancano.
    assert home_alerts.decide_value_alerts(serie(62000, 66999)) == []


def test_a04_cinque_percento_e_cinquemila_euro_una():
    decisioni = home_alerts.decide_value_alerts(serie(100000, 105000))
    assert len(decisioni) == 1
    assert decisioni[0]["type"] == home_alerts.VALUE_TYPE
    assert decisioni[0]["from_observation_id"] == 1
    assert decisioni[0]["to_observation_id"] == 2
    assert decisioni[0]["delta_eur"] == 5000
    assert decisioni[0]["delta_pct"] == pytest.approx(5.0)


def test_a04b_le_soglie_sono_quelle_fissate():
    assert home_alerts.VALUE_CHANGE_MIN_PCT == 5.0
    assert home_alerts.VALUE_CHANGE_MIN_EUR == 5000
    assert home_alerts.is_significant(100000, 104999) is False
    assert home_alerts.is_significant(100000, 105000) is True
    # Da zero non si misura una percentuale.
    assert home_alerts.is_significant(0, 105000) is False
    assert home_alerts.is_significant(None, 105000) is False


def test_a05_variazione_negativa_significativa_una():
    decisioni = home_alerts.decide_value_alerts(serie(100000, 94000))
    assert len(decisioni) == 1
    assert decisioni[0]["delta_eur"] == -6000
    titolo, corpo = home_alerts.compose(decisioni[0])
    assert "diminuito" in corpo and "94.000 €" in corpo and "−6,0%" in corpo


def test_a06_rigiocare_lo_stesso_confronto_da_la_stessa_chiave():
    """La chiave e' del FATTO, non del giro: due esecuzioni sullo stesso
    confronto producono la stessa stringa, e la UNIQUE fa il resto."""
    s = serie(100000, 105000)
    prima = home_alerts.decide_value_alerts(s)[0]
    seconda = home_alerts.decide_value_alerts(s)[0]
    k1 = home_alerts.idempotency_key(prima, stima_id=7, owner_account_id=3)
    k2 = home_alerts.idempotency_key(seconda, stima_id=7, owner_account_id=3)
    assert k1 == k2 == "lmc12:v1:home_value_changed:stima:7:from:1:to:2:account:3"
    assert not re.search(r"20\d\d-\d\d-\d\d", k1), "la data del giro non entra nella chiave"


def test_a07_tre_giorni_identici_dopo_l_evento_nessun_duplicato():
    """Notificato il salto 1->2, i giorni 3, 4 e 5 valgono quanto il 2: con il
    riferimento assorbito (to=2) non nasce niente."""
    s = serie(100000, 105000, 105000, 105000, 105000)
    assert len(home_alerts.decide_value_alerts(s)) == 1
    assert home_alerts.decide_value_alerts(s, absorbed_observation_id=2) == []


def test_a08_la_variazione_cumulativa_viene_colta():
    """100k -> 102k -> 104k -> 106k: nessun giorno supera la soglia da solo,
    ma dal riferimento (100k) all'ultimo (106k) e' +6% e +6.000."""
    decisioni = home_alerts.decide_value_alerts(serie(100000, 102000, 104000, 106000))
    assert len(decisioni) == 1
    assert (decisioni[0]["from_observation_id"], decisioni[0]["to_observation_id"]) == (1, 4)
    assert decisioni[0]["delta_pct"] == pytest.approx(6.0)


def test_a08b_il_riferimento_e_l_ultimo_stato_assorbito_definizione():
    """LA REGOLA, per esteso. Il riferimento avanza in tre casi soltanto:
    primo snapshot, snapshot del proprietario, snapshot notificato. Gli
    intermedi sotto soglia NON lo fanno avanzare."""
    s = [snap(1, 100000), snap(2, 102000), snap(3, 104000), snap(4, 106000),  # +6% -> notifica (1->4)
         snap(5, 107000), snap(6, 108000),                                    # +1,9% da 4: niente
         snap(7, 111500)]                                                     # +5,2% e +5.500 da 4 -> notifica (4->7)
    decisioni = home_alerts.decide_value_alerts(s)
    assert [(d["from_observation_id"], d["to_observation_id"]) for d in decisioni] == [(1, 4), (4, 7)]
    # Con la prima gia' assorbita, resta solo la seconda.
    dopo = home_alerts.decide_value_alerts(s, absorbed_observation_id=4)
    assert [(d["from_observation_id"], d["to_observation_id"]) for d in dopo] == [(4, 7)]


def test_a09_owner_profile_updated_avanza_il_riferimento_senza_notifica():
    s = [snap(1, 100000), snap(2, 130000, reason=home_alerts.OWNER_EDIT_REASON),
         snap(3, 131000), snap(4, 132000)]
    assert home_alerts.decide_value_alerts(s) == []
    # E il riferimento successivo parte da quel nuovo stato: +5% e +6.500 da
    # 130k e' una notifica; da 100k sarebbe stata un'altra cosa.
    s.append(snap(5, 136500))
    decisioni = home_alerts.decide_value_alerts(s)
    assert len(decisioni) == 1
    assert (decisioni[0]["from_observation_id"], decisioni[0]["to_observation_id"]) == (2, 5)


def test_a09b_il_reason_e_quello_di_lmc10():
    assert home_alerts.OWNER_EDIT_REASON == home_update.REFRESH_REASON


def test_a10_fingerprint_cambia_ma_sotto_soglia_zero():
    s = [snap(1, 100000, fp="fp-a"), snap(2, 101000, fp="fp-b")]
    assert home_alerts.decide_value_alerts(s) == []
    s = [snap(1, 100000, fp="fp-a"), snap(2, 100000, fp="fp-b")]
    assert home_alerts.decide_value_alerts(s) == []


def test_a11_fingerprint_cambia_e_sopra_soglia_home_method_changed():
    s = [snap(1, 100000, fp="fp-a"), snap(2, 108000, fp="fp-b")]
    decisioni = home_alerts.decide_value_alerts(s)
    assert len(decisioni) == 1
    assert decisioni[0]["type"] == home_alerts.METHOD_TYPE
    titolo, corpo = home_alerts.compose(decisioni[0])
    assert "metodo di stima" in titolo.lower()
    assert "mercato" not in (titolo + corpo).lower()
    assert "108.000 €" in corpo


def test_a11d_il_metodo_si_classifica_sul_passo_corrente_non_sull_ancora_economica():
    """IL CASO DEL FINAL GATE. Il metodo cambia fra 1 e 2 (sotto soglia: niente);
    il 3 usa gia' il metodo del 2 e supera la soglia CUMULATIVA da 1. Il fatto
    di oggi e' un cambiamento di VALORE: la classificazione guarda il candidato
    rispetto allo snapshot cronologicamente precedente, la soglia resta
    misurata dall'ancora economica (from = 1)."""
    s = [snap(1, 100000, fp="A"), snap(2, 104000, fp="B"), snap(3, 106000, fp="B")]
    decisioni = home_alerts.decide_value_alerts(s)
    assert len(decisioni) == 1
    assert decisioni[0]["type"] == home_alerts.VALUE_TYPE
    assert (decisioni[0]["from_observation_id"], decisioni[0]["to_observation_id"]) == (1, 3)
    assert decisioni[0]["delta_pct"] == pytest.approx(6.0)
    # Controprova: se il metodo cambia NEL candidato, e' un cambio di metodo -
    # anche quando l'ancora economica e' piu' indietro di uno snapshot.
    s = [snap(1, 100000, fp="A"), snap(2, 102000, fp="A"), snap(3, 108000, fp="B")]
    decisioni = home_alerts.decide_value_alerts(s)
    assert [(d["type"], d["from_observation_id"], d["to_observation_id"]) for d in decisioni] == [
        (home_alerts.METHOD_TYPE, 1, 3)]
    # E la correzione del proprietario azzera anche il "precedente": il passo
    # dopo l'edit si confronta con l'edit, non con cio' che c'era prima.
    s = [snap(1, 100000, fp="A"), snap(2, 130000, fp="B", reason=home_alerts.OWNER_EDIT_REASON),
         snap(3, 137000, fp="B")]
    decisioni = home_alerts.decide_value_alerts(s)
    assert [(d["type"], d["from_observation_id"], d["to_observation_id"]) for d in decisioni] == [
        (home_alerts.VALUE_TYPE, 2, 3)]


def test_a11b_un_id_assorbito_sconosciuto_riparte_dalla_baseline():
    s = serie(100000, 105000)
    assert len(home_alerts.decide_value_alerts(s, absorbed_observation_id=999)) == 1


def test_a11c_snapshot_senza_prezzo_numerico_non_contano():
    s = [snap(1, 100000), snap(2, None), snap(3, "abc"), snap(4, True), snap(5, 105000)]
    decisioni = home_alerts.decide_value_alerts(s)
    assert [(d["from_observation_id"], d["to_observation_id"]) for d in decisioni] == [(1, 5)]


# ---------------------------------------------------------------------------
# A - IL RILEVATORE DELLA DOMANDA (test obbligatori 12-14)
# ---------------------------------------------------------------------------

def test_a12_stessa_fascia_zero():
    # Tre rilevazioni tutte "high": nessun cambio.
    letture = [lettura(1, 30, alti=12, recenti=10), lettura(2, 31, alti=12, recenti=10),
               lettura(3, 33, alti=13, recenti=11)]
    assert {home_alerts.demand_status(l)[0] for l in letture} == {demand.STATUS_HIGH}
    assert home_alerts.decide_demand_alerts(letture) == []


def test_a13_cambio_di_fascia_una():
    letture = [lettura(1, 0), lettura(2, 30, alti=12, recenti=10)]
    assert home_alerts.demand_status(letture[0])[0] == demand.STATUS_LOW
    assert home_alerts.demand_status(letture[1])[0] == demand.STATUS_HIGH
    decisioni = home_alerts.decide_demand_alerts(letture)
    assert len(decisioni) == 1
    d = decisioni[0]
    assert d["type"] == home_alerts.DEMAND_TYPE and d["observation_id"] == 2
    assert (d["from_status"], d["to_status"]) == (demand.STATUS_LOW, demand.STATUS_HIGH)
    # Le etichette sono quelle di `owner/demand.py`, non parole nuove.
    assert d["to_label"] == demand.OWNER_COPY["high"][0]
    # Gia' assorbita: niente.
    assert home_alerts.decide_demand_alerts(letture, absorbed_observation_id=2) == []


def test_a13b_none_e_low_sono_la_stessa_fascia_owner_safe():
    """`none` -> `low` cambia l'etichetta ma non la fascia (entrambe
    STATUS_LOW): non e' un cambio reale, non si notifica."""
    letture = [lettura(1, 0), lettura(2, 2)]
    assert home_alerts.decide_demand_alerts(letture) == []


def test_a14_unavailable_coinvolto_zero():
    rotta = {"observation_id": 1, "observed_at": "2026-09-01T09:00:00+00:00",
             "metrics": {"evaluated_buyers": "x"}}
    assert home_alerts.demand_status(rotta)[0] == demand.STATUS_UNAVAILABLE
    # unavailable -> high: si assorbe in silenzio.
    assert home_alerts.decide_demand_alerts([rotta, lettura(2, 30, alti=12, recenti=10)]) == []
    # high -> unavailable: niente.
    assert home_alerts.decide_demand_alerts([lettura(1, 30, alti=12, recenti=10),
                                             dict(rotta, observation_id=2)]) == []
    # high -> unavailable -> low: la rilevazione rotta non fa da riferimento,
    # e il cambio si misura fra le due valide.
    decisioni = home_alerts.decide_demand_alerts(
        [lettura(1, 30, alti=12, recenti=10), dict(rotta, observation_id=2), lettura(3, 0)])
    assert [(d["from_status"], d["to_status"]) for d in decisioni] == [(demand.STATUS_HIGH, demand.STATUS_LOW)]


def test_a14b_la_fascia_viene_da_demand_py_e_basta():
    sorgente = inspect.getsource(home_alerts.demand_status)
    assert "demand.build(" in sorgente
    albero = ast.parse((ROOT / "owner" / "home_alerts.py").read_text(encoding="utf-8"))
    importati = set()
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.ImportFrom):
            importati.update((nodo.module or "") + ":" + a.name for a in nodo.names)
        elif isinstance(nodo, ast.Import):
            importati.update(a.name for a in nodo.names)
    assert importati == {"__future__:annotations", "typing:Any", ":demand"}, importati


# ---------------------------------------------------------------------------
# B - CHIAVI, EVIDENCE, PAROLE
# ---------------------------------------------------------------------------

def test_b1_la_chiave_della_domanda_ha_la_forma_dichiarata():
    d = home_alerts.decide_demand_alerts([lettura(1, 0), lettura(9, 30, alti=12, recenti=10)])[0]
    assert (home_alerts.idempotency_key(d, stima_id=5, owner_account_id=2)
            == "lmc12:v1:home_demand_changed:stima:5:obs:9:account:2")


def test_b2_la_chiave_del_metodo_e_analoga_a_quella_del_valore():
    d = home_alerts.decide_value_alerts([snap(1, 100000, fp="a"), snap(4, 108000, fp="b")])[0]
    assert (home_alerts.idempotency_key(d, stima_id=5, owner_account_id=2)
            == "lmc12:v1:home_method_changed:stima:5:from:1:to:4:account:2")


def test_b3_l_evidence_porta_solo_identificativi_e_i_due_stati():
    v = home_alerts.decide_value_alerts(serie(100000, 105000))[0]
    assert set(home_alerts.evidence(v)) == {"from_observation_id", "to_observation_id",
                                            "from_value", "to_value", "delta_pct"}
    d = home_alerts.decide_demand_alerts([lettura(1, 0), lettura(2, 30, alti=12, recenti=10)])[0]
    assert set(home_alerts.evidence(d)) == {"observation_id", "from_status", "to_status"}
    # Nessun punteggio, nessun conteggio di acquirenti, nessun budget.
    for parola in ("score", "buyer", "budget", "evaluated", "compatible", "match"):
        assert parola not in " ".join(home_alerts.evidence(d))


def test_b4_le_parole_non_dicono_mercato_ne_gergo():
    v = home_alerts.decide_value_alerts(serie(100000, 105000))[0]
    m = home_alerts.decide_value_alerts([snap(1, 100000, fp="a"), snap(2, 108000, fp="b")])[0]
    d = home_alerts.decide_demand_alerts([lettura(1, 0), lettura(2, 30, alti=12, recenti=10)])[0]
    for decisione in (v, m, d):
        titolo, corpo = home_alerts.compose(decisione)
        testo = (titolo + " " + corpo).lower()
        assert "mercato" not in testo
        for gergo in ("buy", "match", "score", "budget", "fingerprint", "snapshot", "watch"):
            assert gergo not in testo, (gergo, testo)
        assert 0 < len(titolo) <= 200 and 0 < len(corpo) <= 5000
    # La domanda parla per fasce, mai per numeri.
    assert not re.search(r"\d", home_alerts.compose(d)[1].replace("360", ""))


def test_b5_i_tipi_sono_tre_e_coincidono_con_il_check_della_069():
    assert set(home_alerts.NOTIFICATION_TYPES) == {"home_value_changed", "home_demand_changed",
                                                   "home_method_changed"}
    sql = UP.read_text(encoding="utf-8")
    for tipo in home_alerts.NOTIFICATION_TYPES:
        assert f"'{tipo}'" in sql
    assert set(owner_repository._HOME_NOTIFICATION_TYPES) == set(home_alerts.NOTIFICATION_TYPES)


def test_b6_il_rilevatore_non_ricalcola_nulla():
    sorgente = _codice(ROOT / "owner" / "home_alerts.py")
    for vietato in ("compute_from_payload", "BASE_MQ", "home_profile", "import valuation",
                    "psycopg2", "core_cursor", "SELECT "):
        assert vietato not in sorgente, vietato


# ---------------------------------------------------------------------------
# C - IL GIRO, CON DOPPI DEL REPOSITORY
# ---------------------------------------------------------------------------

class Repo:
    """Un doppio di `owner.repository` per il giro: pagine di grant, serie per
    stima, ancore, scritture registrate."""

    def __init__(self, grants, valori=None, domanda=None, ancore=None):
        self.grants = grants
        self.valori = valori or {}
        self.domanda = domanda or {}
        self.ancore = ancore or {}
        self.creati = []
        self.soppressi = []
        self.letture_pagina = []
        self.esito = "created"
        self.gia_soppresse = set()

    def list_home_alert_grants_page(self, agency_id, *, after_grant_id, page_size):
        self.letture_pagina.append(after_grant_id)
        return [g for g in self.grants if g["grant_id"] > after_grant_id][:page_size]

    def home_alert_anchors(self, account, stima):
        return self.ancore.get((account, stima), {"value": None, "demand": None})

    def home_value_series(self, agency_id, stima):
        return self.valori.get(stima, [])

    def home_demand_series(self, agency_id, stima):
        return self.domanda.get(stima, [])

    def home_alert_suppressed_keys(self, account, keys):
        return {k for k in keys if k in self.gia_soppresse}

    def audit_home_notification_suppressed(self, account, stima, *, notification_type, idempotency_key):
        self.soppressi.append((account, stima, notification_type, idempotency_key))

    def create_home_notification(self, account, stima, **kw):
        if callable(self.esito):
            return self.esito(account, stima, kw)
        self.creati.append((account, stima, kw["notification_type"], kw["idempotency_key"]))
        return self.esito


def grant(gid, account, stima, in_app=True):
    return {"grant_id": gid, "owner_account_id": account, "stima_id": stima, "in_app_enabled": in_app}


@pytest.fixture
def doppio(monkeypatch):
    def installa(repo):
        monkeypatch.setattr(home_alert_service, "repository", repo)
        return repo
    return installa


def test_c1_un_grant_con_un_salto_produce_una_riga(doppio):
    repo = doppio(Repo([grant(1, 10, 500)], valori={500: serie(100000, 106000)}))
    esito = home_alert_service.run_for_agency(1)
    assert esito["processed"] == 1 and esito["created"] == 1
    assert repo.creati == [(10, 500, "home_value_changed",
                            "lmc12:v1:home_value_changed:stima:500:from:1:to:2:account:10")]


def test_c2_in_app_disabilitato_zero_righe_e_una_soppressione_sola(doppio):
    repo = doppio(Repo([grant(1, 10, 500, in_app=False)], valori={500: serie(100000, 106000)}))
    esito = home_alert_service.run_for_agency(1)
    assert esito["created"] == 0 and esito["suppressed"] == 1
    assert repo.creati == []
    assert len(repo.soppressi) == 1
    # Il giro dopo trova la soppressione gia' scritta e non la riscrive.
    repo.gia_soppresse = {repo.soppressi[0][3]}
    esito = home_alert_service.run_for_agency(1)
    assert esito["suppressed"] == 1 and len(repo.soppressi) == 1


def test_c3_la_soppressione_non_avanza_il_riferimento():
    """E' nel servizio per costruzione: il riferimento viene dalle righe in
    `owner_home_notifications`, e una soppressione non ne scrive."""
    sorgente = inspect.getsource(home_alert_service.process_grant)
    ramo = sorgente.split("if not grant.get(\"in_app_enabled\"")[1].split("return conteggi")[0]
    assert "create_home_notification" not in ramo


def test_c4_la_pagina_e_una_dimensione_non_un_tetto(doppio):
    grants = [grant(i, 10 + i, 500 + i) for i in range(1, 8)]
    repo = doppio(Repo(grants, valori={500 + i: serie(100000, 106000) for i in range(1, 8)}))
    esito = home_alert_service.run_for_agency(1, page_size=3)
    assert esito["processed"] == 7 and esito["created"] == 7
    # 3 + 3 + 1: l'ultima pagina e' corta e chiude; nessun OFFSET, solo l'id.
    assert repo.letture_pagina == [0, 3, 6]


def test_c4b_keyset_non_offset():
    sorgente = _codice_funzione(owner_repository.list_home_alert_grants_page)
    assert "OFFSET" not in sorgente.upper()
    assert "x.id > %s" in sorgente and "ORDER BY x.id ASC" in sorgente


def test_c5_un_guasto_sull_ultimo_elemento_della_pagina_non_crea_un_loop(doppio):
    grants = [grant(i, 10 + i, 500 + i) for i in range(1, 7)]
    repo = doppio(Repo(grants, valori={500 + i: serie(100000, 106000) for i in range(1, 7)}))

    def esito(account, stima, kw):
        if stima == 503:  # ultimo della prima pagina da 3
            raise RuntimeError("boom")
        repo.creati.append((account, stima, kw["notification_type"], kw["idempotency_key"]))
        return "created"
    repo.esito = esito
    riepilogo = home_alert_service.run_for_agency(1, page_size=3)
    assert riepilogo["processed"] == 6 and riepilogo["failed"] == 1 and riepilogo["created"] == 5
    assert repo.letture_pagina == [0, 3, 6], "il cursore avanza PRIMA di elaborare"


def test_c6_un_grant_che_solleva_non_ferma_il_successivo(doppio):
    repo = doppio(Repo([grant(1, 10, 500), grant(2, 11, 501)],
                       valori={500: serie(100000, 106000), 501: serie(100000, 106000)}))

    def esito(account, stima, kw):
        if stima == 500:
            raise RuntimeError("boom")
        repo.creati.append((account, stima, kw["notification_type"], kw["idempotency_key"]))
        return "created"
    repo.esito = esito
    riepilogo = home_alert_service.run_for_agency(1)
    assert riepilogo["failed"] == 1 and riepilogo["created"] == 1
    assert [c[1] for c in repo.creati] == [501]


def test_c7_un_agenzia_che_solleva_non_ferma_le_altre(monkeypatch):
    chiamate = []

    def per_agenzia(agency_id, *, page_size=None):
        chiamate.append(agency_id)
        if agency_id == 1:
            raise RuntimeError("tenant A giu'")
        return {"agency_id": agency_id, "processed": 2, "created": 1, "reused": 1,
                "suppressed": 0, "skipped": 0, "failed": 0}

    import property_watch.repository as pw
    monkeypatch.setattr(pw, "list_active_agency_ids", lambda: [1, 2, 3])
    monkeypatch.setattr(home_alert_service, "run_for_agency", per_agenzia)
    riepilogo = home_alert_service.run_for_all_agencies()
    assert chiamate == [1, 2, 3]
    assert riepilogo["agencies"] == 3 and riepilogo["failed"] == 1 and riepilogo["created"] == 2


def test_c8_grant_sparito_fra_lettura_e_scrittura_e_skipped(doppio):
    from core.exceptions import NotFoundError
    repo = doppio(Repo([grant(1, 10, 500)], valori={500: serie(100000, 106000)}))

    def esito(account, stima, kw):
        raise NotFoundError("Risorsa non trovata")
    repo.esito = esito
    riepilogo = home_alert_service.run_for_agency(1)
    assert riepilogo["skipped"] == 1 and riepilogo["failed"] == 0


def test_c9_il_giro_non_produce_side_effect_commerciali():
    for nome in ("home_alert_service.py", "home_alerts.py"):
        sorgente = _codice(ROOT / "owner" / nome).lower()
        for vietato in ("communication", "whatsapp", "email", "sms", "p29", "enqueue",
                        "next_best_action", "followup", "follow_up", "seller_intent",
                        "create_activity", "record_event", "task"):
            assert vietato not in sorgente, (nome, vietato)


# ---------------------------------------------------------------------------
# D - IL RUNNER
# ---------------------------------------------------------------------------

def test_d1_senza_modalita_non_parte(capsys):
    assert runner.main([]) == 1
    assert "status=failed" in capsys.readouterr().out


def test_d2_le_due_modalita_si_escludono():
    with pytest.raises(runner.ConfigurationError):
        runner.load_config(["--agency-id", "1", "--all-agencies"])
    assert runner.load_config(["--agency-id", "4"]).agency_id == 4
    assert runner.load_config(["--all-agencies"]).all_agencies is True


def test_d3_la_pagina_e_configurabile_e_limitata(monkeypatch):
    monkeypatch.setenv("OWNER_HOME_ALERT_PAGE_SIZE", "25")
    assert runner.load_config(["--all-agencies"]).page_size == 25
    monkeypatch.setenv("OWNER_HOME_ALERT_PAGE_SIZE", "0")
    with pytest.raises(runner.ConfigurationError):
        runner.load_config(["--all-agencies"])


def test_d4_lo_scope_del_lock_e_suo_e_diverso_da_lmc11():
    from property_watch.database import VALUATION_CRON_LOCK_SCOPE
    assert home_alert_service.HOME_ALERT_LOCK_SCOPE == "owner:home_alerts"
    assert home_alert_service.HOME_ALERT_LOCK_SCOPE != VALUATION_CRON_LOCK_SCOPE
    sorgente = inspect.getsource(runner.main)
    assert "advisory_job_lock(HOME_ALERT_LOCK_SCOPE)" in sorgente
    assert sorgente.index("advisory_job_lock(") < sorgente.index("run_cycle(")


def test_d5_lock_non_ottenuto_esce_pulito(monkeypatch, capsys):
    from contextlib import contextmanager
    import property_watch.database as pw_db
    monkeypatch.setattr(runner, "_active_agency_ids", lambda: [1])

    @contextmanager
    def occupato(scope):
        yield False
    monkeypatch.setattr(pw_db, "advisory_job_lock", occupato)
    assert runner.main(["--agency-id", "1"]) == 0
    assert "another_run_active" in capsys.readouterr().out


def test_d6_una_casa_fallita_da_exit_2_e_suppressed_no(monkeypatch, capsys):
    from contextlib import contextmanager
    import property_watch.database as pw_db
    monkeypatch.setattr(runner, "_active_agency_ids", lambda: [1])

    @contextmanager
    def libero(scope):
        yield True
    monkeypatch.setattr(pw_db, "advisory_job_lock", libero)
    monkeypatch.setattr(home_alert_service, "run_for_agency",
                        lambda a, page_size=None: {"agency_id": a, "processed": 3, "created": 1,
                                                   "reused": 0, "suppressed": 1, "skipped": 1, "failed": 0})
    assert runner.main(["--agency-id", "1"]) == 0
    out = capsys.readouterr().out
    assert "suppressed=1" in out and "status=completed" in out
    monkeypatch.setattr(home_alert_service, "run_for_agency",
                        lambda a, page_size=None: {"agency_id": a, "processed": 1, "created": 0,
                                                   "reused": 0, "suppressed": 0, "skipped": 0, "failed": 1})
    assert runner.main(["--agency-id", "1"]) == 2


def test_d7_agenzia_non_attiva_e_database_giu_sono_exit_1(monkeypatch):
    monkeypatch.setattr(runner, "_active_agency_ids", lambda: [2])
    assert runner.main(["--agency-id", "1"]) == 1

    def giu():
        raise ConnectionError("db")
    monkeypatch.setattr(runner, "_active_agency_ids", giu)
    assert runner.main(["--all-agencies"]) == 1


def test_d8_i_campi_del_log_sono_un_elenco_chiuso(capsys):
    assert runner.CAMPI_LOG == ("agencies", "processed", "created", "reused", "suppressed",
                                "skipped", "failed")
    runner._log("runner", "completed", 5, counts={"created": 1, "email": "x@y", "title": "T"})
    out = capsys.readouterr().out
    assert "created=1" in out and "email" not in out and "title" not in out


def test_d9_il_runner_non_apre_socket_ne_importa_domini_commerciali():
    albero = ast.parse((ROOT / "run_owner_home_alert_cron.py").read_text(encoding="utf-8"))
    moduli = set()
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Import):
            moduli.update(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom):
            moduli.add(nodo.module or "")
    assert moduli <= {"__future__", "argparse", "os", "time", "dataclasses",
                      "property_watch", "owner", "owner.home_alert_service",
                      "property_watch.database"}, moduli


def test_d10_i_runner_e_lock_di_lmc11_non_sono_stati_toccati():
    diff = subprocess.run(
        ["git", "--no-optional-locks", "diff", "--name-only", "--",
         "run_property_watch_valuation_cron.py", "property_watch/", "valuation.py"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert diff == "", diff


# ---------------------------------------------------------------------------
# E - L'API E IL DTO
# ---------------------------------------------------------------------------

def test_e1_il_dto_e_una_whitelist_chiusa_senza_evidence():
    assert set(OwnerHomeNotificationDTO.model_fields) == {
        "id", "type", "stima_id", "title", "body", "created_at", "read_at"}
    for vietato in ("evidence", "idempotency_key", "owner_account_id", "algorithm_fingerprint",
                    "observation_id", "expires_at"):
        assert vietato not in OwnerHomeNotificationDTO.model_fields
    riga = {"id": 1, "notification_type": "home_value_changed", "stima_id": 5, "title": "t",
            "body": "b", "created_at": "now", "read_at": None,
            "evidence": {"to_observation_id": 9}, "idempotency_key": "k", "owner_account_id": 7}
    pubblico = owner_repository._public_home_notification(riga)
    assert set(pubblico) == set(OwnerHomeNotificationDTO.model_fields)


def test_e2_le_due_rotte_esistono_e_quelle_p5_sono_intatte():
    from owner import router_portal
    rotte = {(sorted(r.methods)[0], r.path) for r in router_portal.router.routes}
    assert ("GET", "/api/owner/portal/home-notifications") in rotte
    assert ("POST", "/api/owner/portal/home-notifications/{i}/read") in rotte
    assert ("GET", "/api/owner/portal/notifications") in rotte
    assert ("POST", "/api/owner/portal/notifications/{i}/read") in rotte
    assert ("GET", "/api/owner/portal/notification-preferences") in rotte
    assert ("PUT", "/api/owner/portal/notification-preferences") in rotte


def test_e3_ogni_lettura_rivalida_il_grant_pre_incarico():
    for funzione in (owner_repository.portal_home_notifications,
                     owner_repository.mark_home_notification_read,
                     owner_repository.create_home_notification,
                     owner_repository.list_home_alert_grants_page):
        sorgente = inspect.getsource(funzione)
        # Il grant entra o per esteso o attraverso la costante condivisa: in
        # entrambi i casi il testo raggiungibile lo nomina.
        raggiungibile = sorgente + (owner_repository._HOME_ALERT_GRANT_JOIN
                                    if "_HOME_ALERT_GRANT_JOIN" in sorgente else "")
        assert "owner_stima_access" in raggiungibile, funzione.__name__
        assert "_HOME_ALERT_GRANT_VALID" in sorgente, funzione.__name__
        assert "owner_property_access" not in raggiungibile, funzione.__name__
    predicato = owner_repository._HOME_ALERT_GRANT_VALID
    for pezzo in ("ct.agency_id = s.agency_id", "x.access_status = 'active'", "x.revoked_at IS NULL",
                  "x.valid_until IS NULL OR x.valid_until > NOW()", "oa.status <> 'disabled'"):
        assert pezzo in predicato, pezzo
    for funzione in (owner_repository.portal_home_notifications,
                     owner_repository.mark_home_notification_read):
        assert "expires_at > NOW()" in inspect.getsource(funzione)


def test_e4_il_rifiuto_e_un_404_neutro_auditato(monkeypatch):
    from fastapi import HTTPException
    from owner import router_portal
    auditati = []

    def rifiuta(a, i):
        raise Exception("qualunque cosa")
    monkeypatch.setattr(router_portal.r, "mark_home_notification_read", rifiuta)
    monkeypatch.setattr(router_portal.r, "audit_home_notification_access_denied",
                        lambda a, i, scope="read": auditati.append((a, i, scope)))
    with pytest.raises(HTTPException) as info:
        router_portal.home_notification_read(42, s={"owner_account_id": 7})
    assert info.value.status_code == 404 and info.value.detail == "Risorsa non trovata"
    assert auditati == [(7, 42, "read")]


def test_e5_la_lista_usa_limit_piu_uno_per_has_more(monkeypatch):
    from owner import router_portal
    visto = {}

    def lista(a, limit, offset, unread_only):
        visto.update(a=a, limit=limit, offset=offset, unread_only=unread_only)
        return [{"id": i} for i in range(limit)]
    monkeypatch.setattr(router_portal.r, "portal_home_notifications", lista)
    esito = router_portal.home_notifications(limit=2, offset=4, unread_only=True,
                                             s={"owner_account_id": 7})
    assert visto == {"a": 7, "limit": 3, "offset": 4, "unread_only": True}
    assert esito["has_more"] is True and len(esito["items"]) == 2


def test_e6_gli_audit_hanno_property_id_nullo_e_metadata_minimi():
    for nome in ("create_home_notification", "audit_home_notification_suppressed",
                 "mark_home_notification_read", "audit_home_notification_access_denied"):
        sorgente = inspect.getsource(getattr(owner_repository, nome))
        for vietato in ('"title"', '"body"', "email", "telefono", "address", "via"):
            assert vietato not in sorgente.split("meta=")[-1] if "meta=" in sorgente else True, (nome, vietato)
    sorgente = inspect.getsource(owner_repository.create_home_notification)
    assert '"home_notification_created",owner_account_id,None' in sorgente
    assert "home_notification_read" in inspect.getsource(owner_repository.mark_home_notification_read)
    assert "home_notification_access_denied" in inspect.getsource(owner_repository.audit_home_notification_access_denied)
    assert "home_notification_suppressed" in inspect.getsource(owner_repository.audit_home_notification_suppressed)


def test_e7_la_serie_della_domanda_non_legge_il_budget():
    sorgente = inspect.getsource(owner_repository.home_demand_series)
    assert "average_budget" not in sorgente
    assert "average_budget" not in owner_repository._HOME_DEMAND_METRICS
    assert set(owner_repository._HOME_DEMAND_METRICS) == set(demand.METRICHE_LETTE) | {"algorithm_version"} \
        or set(owner_repository._HOME_DEMAND_METRICS) >= set(demand.METRICHE_LETTE)


# ---------------------------------------------------------------------------
# F - LE SENTINELLE: CIO' CHE NON E' CAMBIATO
# ---------------------------------------------------------------------------

def test_f1_p5_legacy_invariato():
    """Le tre funzioni P5 e il suo DTO non sanno niente dello stream nuovo."""
    for funzione in (owner_repository._emit_notification_event,
                     owner_repository.portal_notifications,
                     owner_repository.mark_notification_read):
        sorgente = inspect.getsource(funzione)
        assert "owner_home_notifications" not in sorgente
        assert "owner_stima_access" not in sorgente
        assert "owner_property_access" in sorgente
    assert set(OwnerNotificationDTO.model_fields) == {
        "id", "type", "title", "body", "created_at", "read_at", "target_type", "target_id"}
    assert set(owner_repository._NOTIFICATION_TYPES) == {
        "publication_published", "visit_feedback_published", "shared_document_published",
        "request_handled"}
    assert set(owner_repository._NOTIFICATION_PREFERENCE_COLUMNS) == {
        "publication_enabled", "visit_feedback_enabled", "document_enabled", "request_update_enabled"}


def test_f2_le_migration_p5_e_lmc_precedenti_sono_byte_per_byte_quelle():
    attese = {
        "011_owner_02_p5.sql": "5d8cc996cfb58cabcf336989611cb24779976b84efcc02c03a263f9970b5116e",
        "011_owner_02_p5_down.sql": "a94e504aa7a3aa03e5eb7f8dda6bd3eeb524b59d120294487b1e4f103245210c",
        "015_owner_02_p5_prod.sql": "b69da371d80f4a2eb7f020bd36ad57d77b88899e29344ae957da864ac4209169",
        "015_owner_02_p5_prod_down.sql": "76ceadd45f01dc6802e849b5cb1c5a3ae7963a8629bfed4d6a0ade4ea9be8c19",
        "066_lmc1_owner_stima_access.sql": "2155ef126e11a7ce2c5ff040c5651a48f55c51bc90955dc9fca85f21b2be1471",
        "068_lmc10_owner_home_overrides.sql": "4d16059a934c0c81826b870dd478073e6444bda08888c847d5700a688490936d",
    }
    for nome, atteso in attese.items():
        assert hashlib.sha256((ROOT / "migrations" / nome).read_bytes()).hexdigest() == atteso, nome


def test_f3_le_preferenze_p5_non_hanno_colonne_nuove():
    sorgente = inspect.getsource(owner_repository.update_notification_preferences)
    for vietato in ("home_enabled", "value_enabled", "demand_enabled"):
        assert vietato not in sorgente
        assert vietato not in UP.read_text(encoding="utf-8")
        assert vietato not in (ROOT / "owner" / "schemas.py").read_text(encoding="utf-8")


def test_f4_owner_homes_lmc11_crm_radar_p29_invariati():
    diff = subprocess.run(
        ["git", "--no-optional-locks", "diff", "--name-only", "--",
         "owner/home_service.py", "owner/crm_radar.py", "owner/demand.py",
         "owner/home_update.py", "home_profile.py", "communication/", "seller_intelligence/",
         "seller_intent/", "next_best_action/", "followup/", "valuation.py",
         "property_watch/", "run_property_watch_valuation_cron.py",
         "migrations/011_owner_02_p5.sql", "migrations/015_owner_02_p5_prod.sql",
         "migrations/066_lmc1_owner_stima_access.sql",
         "migrations/068_lmc10_owner_home_overrides.sql"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert diff == "", diff


def test_f5_la_069_e_additiva_canonica_e_senza_guardie_sul_database():
    sql = UP.read_text(encoding="utf-8")
    assert "current_database()" not in sql
    assert not re.search(r"^\s*BEGIN\s*;", sql, re.M) and not re.search(r"^\s*COMMIT\s*;", sql, re.M)
    assert "CREATE TABLE IF NOT EXISTS owner_home_notifications" in sql
    assert "ALTER TABLE" not in sql and "DROP TABLE" not in sql and "INSERT INTO" not in sql
    for colonna in ("owner_account_id", "stima_id", "notification_type", "title", "body",
                    "evidence", "idempotency_key", "created_at", "read_at", "expires_at"):
        assert re.search(rf"^\s+{colonna}\s", sql, re.M), colonna
    for vietata in ("agency_id ", "property_id ", "target_type ", "target_id "):
        assert not re.search(rf"^\s+{vietata}", sql, re.M), vietata
    assert "REFERENCES owner_accounts(id) ON DELETE CASCADE" in sql
    assert "REFERENCES stime(id)          ON DELETE CASCADE" in sql
    assert "UNIQUE (idempotency_key)" in sql
    assert "(owner_account_id, created_at DESC, id DESC)" in sql
    assert "(stima_id, notification_type, created_at DESC, id DESC)" in sql
    assert "trg_owner_home_notifications_agency_integrity" in sql
    giu = DOWN.read_text(encoding="utf-8")
    assert "RAISE EXCEPTION" in giu and "count(*)" in giu
    assert giu.index("RAISE EXCEPTION") < giu.index("DROP TABLE IF EXISTS owner_home_notifications")
    from scripts import p26_migrate as migrate
    trovate = {m.version: m for m in migrate.discover_migrations()}
    assert "069_lmc12_owner_home_notifications" in trovate
    assert migrate.validate_migration(trovate["069_lmc12_owner_home_notifications"]) == []


def test_f6_niente_canali_esterni_in_lmc12():
    for percorso in ("owner/home_alerts.py", "owner/home_alert_service.py",
                     "run_owner_home_alert_cron.py", "migrations/069_lmc12_owner_home_notifications.sql"):
        testo = (ROOT / percorso).read_text(encoding="utf-8").lower()
        for vietato in ("smtp", "twilio", "sendgrid", "communication_messages", "enqueue_message",
                        "phone_number", "whatsapp_enabled", "email_enabled", "push_enabled"):
            assert vietato not in testo, (percorso, vietato)


def test_f7_lo_schema_owner_non_importa_buy_match_flow():
    for percorso in ("owner/home_alerts.py", "owner/home_alert_service.py"):
        testo = (ROOT / percorso).read_text(encoding="utf-8")
        for frammento in ("from buy", "import buy", "from match", "import match", "from flow", "import flow"):
            assert frammento not in testo, (percorso, frammento)


def test_f8_h11_registra_il_test_postgres():
    testo = (ROOT / "tests" / "test_p26_db_entrypoints.py").read_text(encoding="utf-8")
    assert "tests/test_lmc12_home_notifications_postgres.py" in testo
    doc = (ROOT / "docs" / "P26_DB_ENTRYPOINTS.md").read_text(encoding="utf-8")
    assert "test_lmc12_home_notifications_postgres.py" in doc


# ---------------------------------------------------------------------------
# G - IL FRONTEND, ESEGUITO DAVVERO (armatura P6, come LMC-6/7/9/10)
# ---------------------------------------------------------------------------

from test_lmc6_owner_portal_home import BASE, CASA, DETTAGLIO_RICCO, scenario  # noqa: E402

NOVITA = {"id": 11, "type": "home_value_changed", "stima_id": 500,
          "title": "Il valore stimato della tua casa è cambiato",
          "body": "Il valore stimato della tua casa è aumentato: da 100.000 € a 106.000 € (+6,0%).",
          "created_at": "2026-09-20T09:00:00+00:00", "read_at": None}
NOVITA_LETTA = dict(NOVITA, id=12, type="home_demand_changed",
                    title="La domanda per case come la tua è cambiata",
                    body="Il livello di domanda è passato da «Domanda bassa» a «Domanda alta».",
                    read_at="2026-09-19T09:00:00+00:00")


def pagina(items, has_more=False):
    return {"status": 200, "body": {"items": items, "limit": 20, "offset": 0, "has_more": has_more}}


def test_g0_l_armatura_conosce_i_nuovi_elementi():
    testo = (ROOT / "tests" / "test_owner_06_p6.py").read_text(encoding="utf-8")
    for elemento in ("home-notifications-section", "home-notifications-unread-only",
                     "home-notifications-list", "home-notifications-load-more",
                     "home-notifications-retry", "home-notifications-empty-message"):
        assert f"'{elemento}'" in testo, elemento
    assert "/api/owner/portal/home-notifications?" in testo


def test_g1_con_una_casa_la_sezione_compare_e_le_card_si_vedono():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
assert(ids['home-notifications-section'].hidden === false, 'la sezione novita si vede');
assert(ids['home-notifications-content'].hidden === false, 'contenuto visibile');
assert(ids['home-notifications-list'].children.length === 2, 'due card: ' + ids['home-notifications-list'].children.length);
const prima = ids['home-notifications-list'].children[0];
assert(prima.classList.contains('is-unread'), 'la prima e non letta');
assert(prima.children[0].children[0].textContent === 'Valore stimato', 'tipo: ' + prima.children[0].children[0].textContent);
assert(prima.children[1].textContent.includes('valore stimato'), 'titolo');
assert(prima.children[3].textContent.includes('Via Trieste 12, Alba Adriatica'), 'la card dice quale casa: ' + prima.children[3].textContent);
assert(prima.children[4].children[0].textContent === 'Segna come letta', 'bottone leggi');
assert(prima.children[4].children[1].textContent === 'Vedi la casa', 'bottone casa');
const seconda = ids['home-notifications-list'].children[1];
assert(!seconda.classList.contains('is-unread'), 'la seconda e letta');
assert(seconda.children[4].children[0].disabled === true, 'letta: bottone disabilitato');
assert(seconda.children[0].children[0].textContent === 'Domanda', 'tipo domanda');
assert(calls.some((c) => c.url.includes('/home-notifications?limit=20&offset=0&unread_only=false')), 'la lista viene chiesta');
assert(!calls.some((c) => c.url.includes('/notifications?') && c.url.includes('/home-')), 'nessuna confusione con P5');
""", extra={f"{BASE}/home-notifications?limit=20&offset=0&unread_only=false": [pagina([NOVITA, NOVITA_LETTA])]})


def test_g2_senza_case_la_sezione_resta_nascosta_e_nessuna_richiesta_parte():
    scenario([], [{"id": 1, "title": "Immobile", "address": "Via Roma 1",
                   "city": "Alba Adriatica", "access_role": "owner", "is_primary": True}],
             None, """
assert(ids['home-notifications-section'].hidden === true, 'nessuna casa: niente novita');
assert(!calls.some((c) => c.url.includes('/home-notifications')), 'nessuna chiamata');
assert(ids['notifications-section'] === undefined || true, 'P5 non e toccato dal test');
""", extra={f"{BASE}/properties/1": [{"status": 200, "body": {
                 "property": {"id": 1, "title": "Immobile", "address": "Via Roma 1",
                              "city": "Alba Adriatica"}}}],
             f"{BASE}/properties/1/timeline": [{"status": 200, "body": {"items": []}}],
             f"{BASE}/properties/1/visit-feedback?limit=20&offset=0": [
                 {"status": 200, "body": {"items": [], "has_more": False}}],
             f"{BASE}/properties/1/documents?limit=20&offset=0": [
                 {"status": 200, "body": {"items": [], "has_more": False}}],
             f"{BASE}/properties/1/feedback": [{"status": 200, "body": {"items": []}}]})


def test_g3_segna_come_letta_aggiorna_la_card_e_chiama_la_rotta_giusta():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
const card = ids['home-notifications-list'].children[0];
await card.children[4].children[0].trigger('click');
await flush();
const letta = calls.find((c) => c.url.endsWith('/home-notifications/11/read'));
assert(letta && letta.method === 'POST', 'POST sulla rotta LMC-12');
assert(!calls.some((c) => c.url.endsWith('/notifications/11/read')), 'MAI la rotta P5');
assert(!card.classList.contains('is-unread'), 'la card non e piu non letta');
assert(card.children[0].children[1].textContent === 'Letta', 'badge letta');
assert(card.children[4].children[0].disabled === true, 'bottone disabilitato');
assert(card.children[4].children[2].textContent === 'Novità segnata come letta.', 'conferma: ' + card.children[4].children[2].textContent);
""", extra={f"{BASE}/home-notifications?limit=20&offset=0&unread_only=false": [pagina([NOVITA])],
            f"{BASE}/home-notifications/11/read": [
                {"status": 200, "body": dict(NOVITA, read_at="2026-09-20T10:00:00+00:00")}]})


def test_g4_vedi_la_casa_seleziona_la_casa_giusta():
    scenario([CASA, dict(CASA, stima_id=501, via="Via Verdi", civico="3")], [],
             {"status": 200, "body": DETTAGLIO_RICCO}, """
const card = ids['home-notifications-list'].children[0];
assert(card.dataset.stimaId === '501', 'la card sa la sua casa');
await card.children[4].children[1].trigger('click');
await flush();
assert(calls.some((c) => c.url.endsWith('/homes/501')), 'il dettaglio della casa 501 viene chiesto');
const schede = ids['home-list'].children;
assert(schede.some((s) => s.dataset.stimaId === '501' && s.classList.contains('is-selected')), 'la casa 501 e selezionata');
""", extra={f"{BASE}/home-notifications?limit=20&offset=0&unread_only=false": [
                pagina([dict(NOVITA, stima_id=501)])],
            f"{BASE}/homes/501": [{"status": 200, "body": DETTAGLIO_RICCO}]})


def test_g5_solo_non_lette_ricarica_con_il_filtro():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
ids['home-notifications-unread-only'].checked = true;
await ids['home-notifications-unread-only'].trigger('change');
await flush();
assert(calls.some((c) => c.url.includes('/home-notifications?limit=20&offset=0&unread_only=true')), 'filtro');
assert(ids['home-notifications-empty'].hidden === false, 'vuoto con filtro');
assert(ids['home-notifications-empty-message'].textContent === 'Non ci sono novità non lette.', ids['home-notifications-empty-message'].textContent);
""", extra={f"{BASE}/home-notifications?limit=20&offset=0&unread_only=false": [pagina([NOVITA_LETTA])],
            f"{BASE}/home-notifications?limit=20&offset=0&unread_only=true": [pagina([])]})


def test_g6_p5_notifiche_legacy_invariato_nel_frontend():
    js = (ROOT / "static" / "owner_portal" / "assets" / "app.js").read_text(encoding="utf-8")
    # Il loader P5 chiama ancora la sua rotta e solo quella.
    p5 = js[js.index("function notificationPageUrl("):js.index("function notificationCardById(")]
    assert "/notifications?" in p5 and "home-notifications" not in p5
    p5_read = js[js.index("async function markNotificationRead("):js.index("// LMC12_START - \"Novita' sulla tua casa\".")]
    assert "`/notifications/${" in p5_read and "home-notifications" not in p5_read
    # I due stream hanno stati separati.
    assert "homeNotificationItems" in js and "notificationItems" in js
    html = (ROOT / "static" / "owner_portal" / "index.html").read_text(encoding="utf-8")
    # Dentro la sezione delle case (che chiude con l'ULTIMO marcatore LMC6),
    # e prima della sezione P5, che resta dov'era.
    fine_case = html.rindex("<!--LMC6:END-->")
    assert html.index('id="homes-section"') < html.index('id="home-notifications-section"') < fine_case
    assert html.index('id="notifications-section"') > fine_case
