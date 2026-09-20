"""LMC-13 - le metriche di acquisizione di "La Mia Casa": la parte senza database.

COSA SI PROVA QUI.

A. Il modulo puro (`owner/home_metrics.py`): la finestra UTC, l'insieme chiuso
   di `days`, i tassi, e la distinzione fra `null` (non misurabile) e `0`
   (misurato, nessun caso) - che e' la regola piu' importante del DTO.
B. Il DTO: whitelist chiusa, zero PII, nessun punteggio, sopralluogo e
   incarico sempre `null` con la ragione.
C. La query: UNA sola, sola lettura, unita' = stima, tenancy in ogni ramo,
   nessun N+1.
D. L'endpoint: operator-facing, scope dal contesto, `days` vincolato.
E. Le sentinelle: nessuna migration nuova, nessun cron nuovo, i file vietati
   intatti.

Le prove di comportamento - coorte, ritorno per persona, isolamento fra
tenant, grant revocati - stanno in `test_lmc13_home_metrics_postgres.py`,
perche' un doppio proverebbe la mia idea della query, non la query.
"""
from __future__ import annotations

import ast
import inspect
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from owner import home_metrics
from owner import repository as owner_repository
from owner import tracking
from owner.schemas import HomeMetricsRates, HomeMetricsResponse

ROOT = Path(__file__).resolve().parents[1]
ADESSO = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

#: Una riga di conteggi "piena", come la produce la query.
PIENA = {"cohort_homes": 40, "activated_owners": 37, "active_homes_now": 55,
         "viewed_homes": 28, "returning_homes": 10, "value_interest_homes": 9,
         "demand_interest_homes": 6, "updated_homes": 4,
         "strong_interest_homes": 13, "consultation_homes": 5}


def _codice(percorso: Path) -> str:
    """Il codice SENZA docstring: una sentinella per sottostringa deve
    guardare cio' che il modulo fa, non cio' che spiega di non fare."""
    albero = ast.parse(percorso.read_text(encoding="utf-8"))
    for nodo in ast.walk(albero):
        if isinstance(nodo, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (nodo.body and isinstance(nodo.body[0], ast.Expr)
                    and isinstance(getattr(nodo.body[0], "value", None), ast.Constant)
                    and isinstance(nodo.body[0].value.value, str)):
                nodo.body = nodo.body[1:] or [ast.Pass()]
    return ast.unparse(albero)


# ---------------------------------------------------------------------------
# A - IL MODULO PURO
# ---------------------------------------------------------------------------

def test_a1_days_e_un_insieme_chiuso():
    assert home_metrics.ALLOWED_DAYS == (7, 30, 90, 365)
    assert home_metrics.DEFAULT_DAYS == 30
    for valido in home_metrics.ALLOWED_DAYS:
        assert home_metrics.validate_days(valido) == valido
        assert home_metrics.validate_days(str(valido)) == valido
    for rifiutato in (0, 1, 29, 31, 366, -30, None, "trenta"):
        with pytest.raises(home_metrics.InvalidPeriod):
            home_metrics.validate_days(rifiutato)


def test_a2_la_finestra_e_utc_reale_e_semiaperta():
    da, a = home_metrics.window(30, now=ADESSO)
    assert a == ADESSO and da == ADESSO - timedelta(days=30)
    assert da.tzinfo is not None and a.tzinfo is not None
    # Anche partendo da un orario con un altro fuso, i confini restano UTC.
    altro = datetime(2026, 9, 20, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    da2, a2 = home_metrics.window(30, now=altro)
    assert a2 == ADESSO and da2 == da


def test_a3_i_tassi_usano_sempre_cohort_homes():
    assert home_metrics.RATES == {
        "view_rate": "viewed_homes",
        "return_rate": "returning_homes",
        "strong_interest_rate": "strong_interest_homes",
        "consultation_rate": "consultation_homes",
    }
    da, a = home_metrics.window(30, now=ADESSO)
    dto = home_metrics.build(PIENA, days=30, cohort_from=da, cohort_to=a)
    assert dto["rates"]["view_rate"] == pytest.approx(28 / 40)
    assert dto["rates"]["return_rate"] == pytest.approx(10 / 40)
    assert dto["rates"]["strong_interest_rate"] == pytest.approx(13 / 40)
    assert dto["rates"]["consultation_rate"] == pytest.approx(5 / 40)
    # `active_homes_now` e' uno stock: non ha un tasso, e non deve averlo.
    assert not any("active" in nome for nome in dto["rates"])


def test_a4_coorte_vuota_da_null_non_zero():
    """LA DISTINZIONE. Zero significherebbe "nessuna di quelle case ha
    convertito"; senza case non c'e' niente di cui dirlo."""
    da, a = home_metrics.window(7, now=ADESSO)
    dto = home_metrics.build({"active_homes_now": 12}, days=7, cohort_from=da, cohort_to=a)
    assert dto["cohort_homes"] == 0
    assert dto["viewed_homes"] == 0 and dto["consultation_homes"] == 0
    assert all(valore is None for valore in dto["rates"].values())
    # Lo stock resta misurato anche con la coorte vuota.
    assert dto["active_homes_now"] == 12


def test_a5_conteggio_assente_vale_zero_mai_none():
    da, a = home_metrics.window(30, now=ADESSO)
    dto = home_metrics.build({}, days=30, cohort_from=da, cohort_to=a)
    for nome in (*home_metrics.COHORT_COUNTS, *home_metrics.STOCK_COUNTS):
        assert dto[nome] == 0, nome
        assert dto[nome] is not None


def test_a6_sopralluogo_e_incarico_sono_sempre_null_con_la_ragione():
    da, a = home_metrics.window(30, now=ADESSO)
    dto = home_metrics.build(
        {**PIENA, "inspection_homes": 99, "mandate_homes": 99},
        days=30, cohort_from=da, cohort_to=a)
    assert dto["inspection_homes"] is None and dto["mandate_homes"] is None
    assert dto["rates"]["inspection_rate"] is None and dto["rates"]["mandate_rate"] is None
    assert set(dto["not_measurable"]) == {"inspection_homes", "mandate_homes"}
    for ragione in dto["not_measurable"].values():
        assert isinstance(ragione, str) and ragione.strip()


def test_a7_il_modulo_non_tocca_il_database_ne_calcola_punteggi():
    sorgente = _codice(ROOT / "owner" / "home_metrics.py")
    for vietato in ("SELECT", "INSERT", "core_cursor", "psycopg2", "score",
                    "punteggio", "weight", "peso"):
        assert vietato not in sorgente, vietato
    albero = ast.parse((ROOT / "owner" / "home_metrics.py").read_text(encoding="utf-8"))
    importati = set()
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.ImportFrom):
            importati.add(nodo.module or "")
        elif isinstance(nodo, ast.Import):
            importati.update(a.name for a in nodo.names)
    assert importati == {"__future__", "datetime", "typing"}, importati


# ---------------------------------------------------------------------------
# B - IL DTO
# ---------------------------------------------------------------------------

#: IL CONTRATTO APPROVATO, scritto a mano. Derivarlo dalle costanti del
#: modulo lo renderebbe una tautologia: una chiave `email` aggiunta a
#: `COHORT_COUNTS` comparirebbe in entrambi i lati del confronto e il test
#: continuerebbe a passare. Qui l'elenco e' indipendente dal codice, e
#: qualunque chiave in piu' o in meno fa fallire.
CHIAVI_DTO_APPROVATE = {
    "period_days", "cohort_from", "cohort_to", "unit",
    # LMC-15 - dichiara da quando sopralluogo e incarico sono misurati.
    "measurement_started_at",
    "cohort_homes", "active_homes_now", "activated_owners",
    "viewed_homes", "returning_homes",
    "value_interest_homes", "demand_interest_homes", "updated_homes",
    "strong_interest_homes", "consultation_homes",
    "inspection_homes", "mandate_homes",
    "rates", "not_measurable",
}
CHIAVI_RATES_APPROVATE = {
    "view_rate", "return_rate", "strong_interest_rate",
    "consultation_rate", "inspection_rate", "mandate_rate",
}
CHIAVI_VIETATE = {
    "stima_id", "owner_account_id", "account_id", "contact_id", "lead_id",
    "property_id", "email", "phone", "telefono", "nome", "cognome", "via",
    "civico", "indirizzo", "address", "score", "punteggio", "payload",
    "buyer", "budget", "idempotency_key", "agency_id",
}


def test_b1_il_dto_e_una_whitelist_chiusa():
    da, a = home_metrics.window(30, now=ADESSO)
    dto = home_metrics.build(PIENA, days=30, cohort_from=da, cohort_to=a)
    assert set(dto) == CHIAVI_DTO_APPROVATE
    assert set(dto["rates"]) == CHIAVI_RATES_APPROVATE
    # Lo schema Pydantic dice la stessa cosa: se i due divergessero, la rotta
    # risponderebbe con una forma diversa da quella che `build` compone.
    assert set(HomeMetricsResponse.model_fields) == CHIAVI_DTO_APPROVATE
    assert set(HomeMetricsRates.model_fields) == CHIAVI_RATES_APPROVATE
    # E le costanti del modulo non devono aver aggiunto niente di proprio.
    assert set(home_metrics.COHORT_COUNTS) | set(home_metrics.STOCK_COUNTS) \
        | set(home_metrics.BRIDGE_COUNTS) | {"period_days", "cohort_from",
                                             "cohort_to", "unit", "rates",
                                             "not_measurable",
                                             "measurement_started_at"} == CHIAVI_DTO_APPROVATE
    HomeMetricsResponse.model_validate(dto)


def test_b2_nessuna_pii_e_nessun_identificativo_nel_dto():
    da, a = home_metrics.window(30, now=ADESSO)
    # Anche se la query restituisse per sbaglio colonne che non le competono,
    # nel DTO non entrano: `build` legge per nome da un elenco chiuso.
    dto = home_metrics.build(
        {**PIENA, "stima_id": 7, "owner_account_id": 9, "contact_id": 3,
         "email": "mario@example.it", "telefono": "+39 333", "via": "Via Trieste"},
        days=30, cohort_from=da, cohort_to=a)
    # La prova e' sulle CHIAVI e sui TIPI, mai sui valori numerici: un
    # conteggio legittimo puo' valere qualunque intero.
    assert set(dto) == CHIAVI_DTO_APPROVATE
    for chiave in dto:
        for vietata in CHIAVI_VIETATE:
            assert vietata not in chiave.lower(), (chiave, vietata)
    # Le stringhe che il DTO porta sono solo quelle dichiarate: due date,
    # l'unita' e - quando il ponte non misura - le ragioni, che sono costanti
    # del modulo e non valori che arrivino dai dati.
    assert set(v for k, v in dto.items() if isinstance(v, str)) == {
        dto["cohort_from"], dto["cohort_to"], "stima"}
    assert set(dto["not_measurable"].values()) <= {
        home_metrics.REASON_NOT_APPLIED, home_metrics.REASON_BEFORE_START}
    for valore in dto.values():
        assert not isinstance(valore, (list, tuple)), "nessuna lista di righe nel DTO"


def test_b3_l_unita_e_dichiarata_ed_e_la_stima():
    da, a = home_metrics.window(30, now=ADESSO)
    assert home_metrics.UNIT == "stima"
    assert home_metrics.build(PIENA, days=30, cohort_from=da, cohort_to=a)["unit"] == "stima"
    assert HomeMetricsResponse.model_fields["unit"].annotation.__args__ == ("stima",)


def test_b4_i_confini_escono_in_utc_con_la_z():
    da, a = home_metrics.window(90, now=ADESSO)
    dto = home_metrics.build(PIENA, days=90, cohort_from=da, cohort_to=a)
    assert dto["cohort_to"] == "2026-09-20T10:00:00Z"
    assert dto["cohort_from"] == "2026-06-22T10:00:00Z"
    for chiave in ("cohort_from", "cohort_to"):
        assert dto[chiave].endswith("Z") and "+00:00" not in dto[chiave]


# ---------------------------------------------------------------------------
# C - LA QUERY
# ---------------------------------------------------------------------------

def test_c1_una_sola_query_nessun_n_piu_uno():
    """Il funnel e' un aggregato: una `execute` sola, e nessun ciclo."""
    sorgente = inspect.getsource(owner_repository.home_metrics_counts)
    assert sorgente.count("c.execute(") == 1
    for nodo in ast.walk(ast.parse(sorgente)):
        assert not isinstance(nodo, (ast.For, ast.While, ast.AsyncFor)), "nessun ciclo"


def test_c2_e_sola_lettura():
    sql = owner_repository._HOME_METRICS_SQL.upper()
    for vietato in ("INSERT", "UPDATE ", "DELETE", "MERGE", "TRUNCATE", "FOR UPDATE"):
        assert vietato not in sql, vietato
    sorgente = inspect.getsource(owner_repository.home_metrics_counts)
    assert "commit=True" not in sorgente
    assert "audit" not in sorgente.lower()


def test_c3_l_unita_e_la_stima_in_ogni_conteggio_di_case():
    """`COUNT(DISTINCT stima_id)` ovunque tranne dove si contano persone: una
    casa con tre grant e' una opportunita' sola."""
    sql = owner_repository._HOME_METRICS_SQL
    for metrica in ("viewed_homes", "value_interest_homes", "demand_interest_homes",
                    "updated_homes", "strong_interest_homes", "active_homes_now",
                    "consultation_homes"):
        blocco = sql[:sql.index(f"AS {metrica}")]
        ultimo = blocco.rindex("COUNT(")
        assert "COUNT(DISTINCT stima_id" in blocco[ultimo:ultimo + 40], metrica
    # Il cohort e' gia' una riga per stima (GROUP BY stima_id), quindi COUNT(*).
    assert "GROUP BY stima_id" in sql
    assert "MIN(created_at) AS cohort_at" in sql
    # Le persone si contano una volta sola, e la metrica non si chiama `homes`.
    assert "SELECT DISTINCT g.owner_account_id" in sql
    assert "activated_owners" in sql and "activated_homes" not in sql


def test_c4_il_ritorno_e_per_persona_non_per_casa():
    """Due comproprietari che aprono in due giorni diversi sono due prime
    visite. Il raggruppamento per account, PRIMA del conteggio delle case, e'
    cio' che lo impedisce."""
    sql = owner_repository._HOME_METRICS_SQL
    blocco = sql[sql.index("returning_homes AS ("):sql.index("activated AS (")]
    assert "GROUP BY stima_id, owner_account_id" in blocco
    assert "HAVING COUNT(DISTINCT giorno_utc) >= 2" in blocco
    assert "owner_home_viewed" in blocco
    # Il giorno e' UTC esplicito, non il fuso della sessione.
    assert "(e.occurred_at AT TIME ZONE 'UTC')::date" in sql


def test_c5_l_owner_dell_evento_non_si_indovina():
    """`owner_accounts.contact_id` e' NOT NULL UNIQUE, quindi il contatto di
    un evento identifica UN account e uno solo; il join sul grant lo conferma
    su QUELLA casa. Nessun parsing della chiave di idempotenza."""
    sql = owner_repository._HOME_METRICS_SQL
    assert "g.contact_id = e.contact_id" in sql
    assert "idempotency_key" not in sql
    ddl = (ROOT / "migrations" / "009_owner_01.sql").read_text(encoding="utf-8")
    assert "contact_id BIGINT NOT NULL UNIQUE REFERENCES contacts(id)" in ddl


def test_c6_ogni_ramo_e_tenant_safe():
    sql = owner_repository._HOME_METRICS_SQL
    # L'unica porta d'ingresso impone agenzia e accordo fra le due radici.
    porta = sql[sql.index("coherent_grants AS ("):sql.index("home_cohort AS (")]
    assert "s.agency_id = %(agency_id)s" in porta
    assert "ct.agency_id = s.agency_id" in porta
    assert "JOIN owner_accounts oa" in porta and "JOIN contacts ct" in porta
    # Gli eventi portano il proprio agency_id e passano comunque dalla porta.
    eventi = sql[sql.index("cohort_events AS ("):sql.index("returning_homes AS (")]
    assert "e.agency_id = %(agency_id)s" in eventi
    assert "e.event_source = 'owner_portal'" in eventi
    assert "e.event_type = ANY(%(event_types)s)" in eventi
    assert "e.occurred_at >= c.cohort_at" in eventi
    assert "e.occurred_at <  %(cohort_to)s" in eventi
    assert "JOIN coherent_grants g" in eventi
    # `owner_stima_access` e `stime` si leggono SOLO dentro la porta. I
    # commenti SQL si tolgono prima: la parola "stime" compare anche nella
    # prosa italiana, e una sentinella deve guardare il codice.
    nudo = re.sub(r"--[^\n]*", "", sql)
    confine = nudo.index("home_cohort AS (")
    for tabella in ("owner_stima_access", "stime"):
        for occorrenza in re.finditer(rf"\b{tabella}\b", nudo):
            assert occorrenza.start() < confine, (tabella, occorrenza.start())


def test_c7_l_agenzia_e_il_primo_parametro_e_non_ha_default():
    parametri = list(inspect.signature(owner_repository.home_metrics_counts).parameters.values())
    assert parametri[0].name == "agency_id"
    assert parametri[0].default is inspect.Parameter.empty
    assert parametri[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert [p.name for p in parametri[1:]] == ["cohort_from", "cohort_to"]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parametri[1:])


def test_c8_l_insieme_degli_eventi_e_chiuso_e_allineato_a_lmc7():
    assert set(owner_repository.HOME_METRIC_EVENTS) == set(tracking.EVENT_TYPES)
    for tipo in owner_repository.HOME_METRIC_EVENTS:
        assert tipo in owner_repository._HOME_METRICS_SQL


def test_c9_la_query_non_legge_dati_personali():
    sql = owner_repository._HOME_METRICS_SQL.lower()
    for vietato in ("email", "telefono", "nome", "cognome", "civico",
                    "indirizzo", "payload", "budget", "score", "buyer_pressure",
                    "title", "body", "token"):
        assert vietato not in sql, vietato


# ---------------------------------------------------------------------------
# D - L'ENDPOINT
# ---------------------------------------------------------------------------

def test_d1_la_rotta_e_interna_e_scopata():
    from owner import router_admin
    rotte = {(sorted(r.methods)[0], r.path) for r in router_admin.router.routes
             if hasattr(r, "path")}
    assert ("GET", "/api/owner/admin/home-metrics") in rotte
    sorgente = inspect.getsource(router_admin.home_metrics_view)
    assert "require_owner_admin_context" in sorgente
    assert "agency_of(ctx)" in sorgente
    # Nessun identificativo dal client: solo `days`.
    assert set(inspect.signature(router_admin.home_metrics_view).parameters) == {"days", "ctx"}
    # E il portale non la espone.
    portale = (ROOT / "owner" / "router_portal.py").read_text(encoding="utf-8")
    assert "home-metrics" not in portale and "home_metrics" not in portale


def test_d2_days_fuori_insieme_da_422(monkeypatch):
    from fastapi import HTTPException
    from owner import router_admin

    class Ctx:
        def require_agency(self):
            return 1
    monkeypatch.setattr(router_admin.r, "home_metrics_counts",
                        lambda a, **kw: (dict(PIENA), None))
    for rifiutato in (1, 29, 31, 366, 0):
        with pytest.raises(HTTPException) as info:
            router_admin.home_metrics_view(days=rifiutato, ctx=Ctx())
        assert info.value.status_code == 422
    for accettato in home_metrics.ALLOWED_DAYS:
        assert router_admin.home_metrics_view(days=accettato, ctx=Ctx())["period_days"] == accettato


def test_d3_platform_admin_senza_agenzia_non_ottiene_un_totale(monkeypatch):
    """`agency_of` rifiuta prima che la query parta: nessun aggregato di
    piattaforma, nemmeno per errore."""
    from fastapi import HTTPException
    from operator_auth.exceptions import PlatformAdminAgencyRequired
    from owner import router_admin
    chiamate = []
    monkeypatch.setattr(router_admin.r, "home_metrics_counts",
                        lambda a, **kw: chiamate.append(a) or dict(PIENA))

    class SenzaAgenzia:
        def require_agency(self):
            raise PlatformAdminAgencyRequired("nessuna agenzia")
    with pytest.raises(HTTPException) as info:
        router_admin.home_metrics_view(days=30, ctx=SenzaAgenzia())
    assert info.value.status_code == 403
    assert chiamate == [], "la query non deve essere raggiunta"


def test_d4_l_agenzia_passata_alla_query_e_quella_del_contesto(monkeypatch):
    from owner import router_admin
    visto = {}

    def finta(agency_id, *, cohort_from, cohort_to):
        visto.update(agency=agency_id, da=cohort_from, a=cohort_to)
        return dict(PIENA), None
    monkeypatch.setattr(router_admin.r, "home_metrics_counts", finta)

    class Ctx:
        def require_agency(self):
            return 77
    esito = router_admin.home_metrics_view(days=90, ctx=Ctx())
    assert visto["agency"] == 77
    assert (visto["a"] - visto["da"]).days == 90
    assert visto["da"].tzinfo is not None
    assert esito["unit"] == "stima"


# ---------------------------------------------------------------------------
# E - LE SENTINELLE
# ---------------------------------------------------------------------------

def test_e1_nessuna_migration_nuova():
    """LMC-13 non ha creato schema, e continua a non averne creato.

    SENTINELLA AGGIORNATA DA LMC-15: nel working tree c'e' ora la 070, il
    ponte di acquisizione approvato dallo SCHEMA GATE di LMC-15A.2. LMC-15
    ha esteso le metriche di LMC-13 - da qui la modifica a questo file - ma
    le due tabelle nuove sono sue, non di LMC-13. La si nomina invece di
    smettere di guardare: qualunque ALTRA migration comparisse nel working
    tree farebbe ancora fallire questo test.
    """
    nuovi = {riga[3:].strip() for riga in subprocess.run(
        ["git", "--no-optional-locks", "status", "--porcelain", "--", "migrations/"],
        cwd=ROOT, capture_output=True, text=True).stdout.splitlines()}
    atteso = {"migrations/070_lmc15_acquisition_bridge.sql",
              "migrations/070_lmc15_acquisition_bridge_down.sql"}
    assert nuovi - atteso == set(), sorted(nuovi - atteso)


def test_e2_nessun_cron_nuovo():
    runner = sorted(p.name for p in ROOT.glob("run_*cron*.py"))
    assert runner == ["run_communication_dispatch_cron.py", "run_flow_p2b_cron.py",
                      "run_followup_p18d_cron.py", "run_owner_home_alert_cron.py",
                      "run_property_watch_valuation_cron.py"], runner
    sorgente = _codice(ROOT / "owner" / "home_metrics.py")
    for vietato in ("advisory_job_lock", "argparse", "schedule", "cron"):
        assert vietato not in sorgente, vietato


def test_e3_i_file_vietati_non_sono_stati_toccati():
    diff = subprocess.run(
        ["git", "--no-optional-locks", "diff", "--name-only", "--",
         "crm/", "property_watch/", "valuation.py", "run_property_watch_valuation_cron.py",
         "run_owner_home_alert_cron.py", "owner/router_portal.py", "owner/home_alerts.py",
         "owner/home_alert_service.py", "migrations/", "P29_2_0_COMMUNICATION_DESIGN.md"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert diff == "", diff


def test_e4_le_metriche_non_scrivono_eventi_ne_notifiche():
    """Guardare un numero non e' un fatto del dominio."""
    sorgente = _codice(ROOT / "owner" / "home_metrics.py") + \
        inspect.getsource(owner_repository.home_metrics_counts)
    for vietato in ("record_event", "track_", "seller_intelligence", "notification",
                    "communication", "whatsapp", "email"):
        assert vietato not in sorgente, vietato
