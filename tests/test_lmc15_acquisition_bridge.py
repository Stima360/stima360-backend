"""LMC-15 - il ponte di acquisizione, senza database.

COSA PROVA QUESTO FILE, E COSA NO.

Qui stanno le prove che non hanno bisogno di PostgreSQL: la forma della
superficie (quali rotte esistono, cosa accettano, chi le scopa), la forma dei
corpi, la forma della migration come TESTO, e le decisioni pure delle
metriche. Il comportamento vero - la matrice dei CHECK, i trigger, la
cardinalita', la tenancy imposta dal database, la cancellazione della stima -
sta in `test_lmc15_acquisition_bridge_postgres.py`, perche' su un doppio non
si dimostra.

LA DOMANDA CHE IL PONTE RISPONDE. Da quale stima PRE-incarico nasce questa
property POST-incarico, e quando e' avvenuto il sopralluogo. Entrambe le
risposte sono DICHIARATE da un operatore e registrate con il suo nome, mai
dedotte da una somiglianza di email, telefono, nome, indirizzo o comune: una
corrispondenza non e' una relazione, e LMC-15A lo ha messo per iscritto.
"""
from __future__ import annotations

import ast
import inspect
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONE = ROOT / "migrations" / "070_lmc15_acquisition_bridge.sql"
DISCESA = ROOT / "migrations" / "070_lmc15_acquisition_bridge_down.sql"

SQL_SU = MIGRAZIONE.read_text(encoding="utf-8")
SQL_GIU = DISCESA.read_text(encoding="utf-8")


def _senza_commenti(sql: str) -> str:
    """Il SQL senza i commenti `--`.

    Le sentinelle che cercano una parola nel SQL devono cercarla nel CODICE,
    non nella prosa che lo spiega: questo file di migration parla molto, e
    una sentinella ingenua troverebbe `agency_id` in una frase che dice
    proprio che `agency_id` non c'e'.
    """
    import re
    return re.sub(r"--[^\n]*", "", sql)


SU = _senza_commenti(SQL_SU)
GIU = _senza_commenti(SQL_GIU)


def _codice(modulo) -> str:
    """Il sorgente di un modulo SENZA docstring.

    Stessa ragione di `_senza_commenti`: cercare `agency_id` o `OFFSET` nel
    testo di una docstring che ne parla non prova niente.
    """
    albero = ast.parse(inspect.getsource(modulo))
    for nodo in ast.walk(albero):
        if isinstance(nodo, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            corpo = nodo.body
            if (corpo and isinstance(corpo[0], ast.Expr)
                    and isinstance(corpo[0].value, ast.Constant)
                    and isinstance(corpo[0].value.value, str)):
                corpo[0].value.value = ""
    return ast.unparse(albero)


# ---------------------------------------------------------------------------
# A - LA SUPERFICIE
# ---------------------------------------------------------------------------

#: LE SETTE ROTTE APPROVATE, scritte a mano. Derivarle dal router direbbe
#: soltanto che il router e' uguale a se' stesso.
ROTTE_APPROVATE = {
    ("POST", "/api/acquisition/stime/{stima_id}/links"),
    ("POST", "/api/acquisition/links/{acquisition_id}/mandate"),
    ("POST", "/api/acquisition/links/{acquisition_id}/revoke"),
    ("POST", "/api/acquisition/stime/{stima_id}/inspections"),
    ("POST", "/api/acquisition/stime/{stima_id}/inspections/completed"),
    ("POST", "/api/acquisition/inspections/{inspection_id}/complete"),
    ("POST", "/api/acquisition/inspections/{inspection_id}/cancel"),
}


def test_01_le_sette_rotte_sono_quelle_approvate_e_nessun_altra():
    from acquisition.router import router
    viste = {(m, r.path) for r in router.routes for m in r.methods
             if m != "HEAD"}
    assert viste == ROTTE_APPROVATE, sorted(viste ^ ROTTE_APPROVATE)


def test_02_ogni_rotta_dichiara_require_operator_per_esteso():
    """P26-4 dimostro' che un alias di modulo fa leggere una rotta come non
    scopata al prover AST che certifica queste superfici. Qui la dipendenza
    si cerca nel TESTO, nella forma esatta che quel prover riconosce."""
    sorgente = (ROOT / "acquisition" / "router.py").read_text(encoding="utf-8")
    decoratori = [r for r in sorgente.split("@router.") if r.strip()][1:]
    assert len(decoratori) == 7, len(decoratori)
    for blocco in decoratori:
        assert "ctx: OperatorContext = Depends(require_operator)" in blocco, blocco[:80]


def test_03_nessuna_rotta_accetta_agency_id_ne_un_attore():
    """Il tenant viene da `ctx.require_agency()` e l'attore da `ctx.user_id`.
    Se una firma li accettasse, il client potrebbe sceglierli."""
    from acquisition import router as router_module
    for rotta in router_module.router.routes:
        parametri = set(inspect.signature(rotta.endpoint).parameters)
        assert "agency_id" not in parametri, rotta.path
        assert not [p for p in parametri if p.endswith("operator_user_id")], rotta.path


def test_04_i_corpi_non_hanno_nessun_campo_operatore():
    from acquisition import schemas
    modelli = [v for v in vars(schemas).values()
               if isinstance(v, type) and issubclass(v, schemas._Corpo)
               and v is not schemas._Corpo]
    assert len(modelli) == 6, [m.__name__ for m in modelli]
    for modello in modelli:
        for campo in modello.model_fields:
            assert not campo.endswith("operator_user_id"), (modello.__name__, campo)
            assert campo != "agency_id", modello.__name__
            assert campo != "actor_user_id", modello.__name__


def test_05_i_corpi_rifiutano_i_campi_in_piu():
    """`extra="forbid"`: un `linked_by_operator_user_id` iniettato nel corpo
    e' un 422, non un campo ignorato in silenzio."""
    from pydantic import ValidationError as PydanticError

    from acquisition import schemas
    with pytest.raises(PydanticError):
        schemas.AcquisitionLinkCreate(property_id=1,
                                      linked_by_operator_user_id=99)
    with pytest.raises(PydanticError):
        schemas.AcquisitionRevoke(revoked_reason="x", agency_id=2)
    with pytest.raises(PydanticError):
        schemas.MandateRecord(mandate_signed_at=datetime.now(timezone.utc),
                              mandate_recorded_at=datetime.now(timezone.utc))


def test_06_le_ragioni_vuote_sono_rifiutate():
    from pydantic import ValidationError as PydanticError

    from acquisition import schemas
    with pytest.raises(PydanticError):
        schemas.AcquisitionRevoke(revoked_reason="   ")
    with pytest.raises(PydanticError):
        schemas.InspectionCancel(cancelled_reason="  ")
    with pytest.raises(PydanticError):
        schemas.MandateRecord(mandate_signed_at=datetime.now(timezone.utc),
                              mandate_reference=" ")
    # Una ragione assente per la cancellazione e' invece ammessa.
    assert schemas.InspectionCancel().cancelled_reason is None
    assert schemas.AcquisitionRevoke(revoked_reason=" vero ").revoked_reason == "vero"


def test_07_il_router_e_montato_in_main_una_volta_sola():
    sorgente = (ROOT / "main.py").read_text(encoding="utf-8")
    assert sorgente.count("from acquisition.router import router as acquisition_router") == 1
    assert sorgente.count("app.include_router(acquisition_router") == 1
    assert "require_authenticated_operator" in sorgente.split(
        "app.include_router(acquisition_router")[1].split("\n")[0]


def test_08_il_ponte_non_esiste_sul_portale_del_proprietario():
    """Sono fatti che registra l'agenzia. Il proprietario non li scrive e non
    li legge: nessuna rotta del portale li nomina."""
    portale = (ROOT / "owner" / "router_portal.py").read_text(encoding="utf-8")
    for vietato in ("acquisition", "stima_acquisitions", "stima_inspections",
                    "mandate", "inspection"):
        assert vietato not in portale.lower(), vietato


# ---------------------------------------------------------------------------
# B - IL SERVIZIO: L'ATTORE
# ---------------------------------------------------------------------------

class _Ctx:
    def __init__(self, user_id=7, agency=3):
        self.user_id = user_id
        self._agency = agency

    def require_agency(self):
        return self._agency


def test_09_il_canale_basic_senza_persona_non_scrive_il_ponte():
    """`OperatorContext.user_id` e' `None` sul canale Basic legacy: un
    segreto condiviso, non una persona. Un registro di acquisizione senza
    attore sarebbe un audit che non dice chi, quindi si rifiuta."""
    from core.exceptions import PermissionDenied

    from acquisition import service
    with pytest.raises(PermissionDenied):
        service._attore(_Ctx(user_id=None))
    assert service._attore(_Ctx(user_id="7")) == 7


def test_10_ogni_funzione_del_servizio_passa_agenzia_e_attore(monkeypatch):
    """Nessuna scorciatoia: tutte e sette prendono il tenant da
    `require_agency()` e l'attore da `ctx.user_id`."""
    from acquisition import service
    visti = []

    class FintoRepo:
        def __getattr__(self, nome):
            def finta(agency_id, **kw):
                visti.append((nome, agency_id, kw.get("actor_user_id")))
                return {"id": 1}
            return finta

    monkeypatch.setattr(service, "repository", FintoRepo())
    ctx = _Ctx(user_id=7, agency=3)
    quando = datetime.now(timezone.utc)

    from acquisition import schemas
    service.link_property_to_stima(ctx, 1, schemas.AcquisitionLinkCreate(property_id=2))
    service.record_mandate(ctx, 1, schemas.MandateRecord(mandate_signed_at=quando))
    service.revoke_link(ctx, 1, schemas.AcquisitionRevoke(revoked_reason="x"))
    service.schedule_inspection(ctx, 1, schemas.InspectionSchedule(scheduled_for=quando))
    service.record_completed_inspection(ctx, 1, schemas.InspectionComplete(completed_at=quando))
    service.complete_inspection(ctx, 1, schemas.InspectionComplete(completed_at=quando))
    service.cancel_inspection(ctx, 1, schemas.InspectionCancel())

    assert len(visti) == 7
    for nome, agency, attore in visti:
        assert agency == 3, nome
        assert attore == 7, nome


def test_11_senza_persona_il_repository_non_viene_nemmeno_raggiunto(monkeypatch):
    from core.exceptions import PermissionDenied

    from acquisition import schemas, service
    chiamate = []

    class FintoRepo:
        def __getattr__(self, nome):
            def finta(*a, **kw):
                chiamate.append(nome)
            return finta

    monkeypatch.setattr(service, "repository", FintoRepo())
    with pytest.raises(PermissionDenied):
        service.link_property_to_stima(_Ctx(user_id=None), 1,
                                       schemas.AcquisitionLinkCreate(property_id=2))
    assert chiamate == []


# ---------------------------------------------------------------------------
# C - IL REPOSITORY: FORMA E DISCIPLINA
# ---------------------------------------------------------------------------

FUNZIONI_PUBBLICHE = (
    "create_acquisition_link", "record_mandate", "revoke_acquisition_link",
    "create_inspection", "create_completed_inspection",
    "complete_inspection", "cancel_inspection",
)


def test_12_agency_id_e_il_primo_parametro_e_non_ha_default():
    from acquisition import repository
    for nome in FUNZIONI_PUBBLICHE:
        firma = inspect.signature(getattr(repository, nome))
        parametri = list(firma.parameters)
        assert parametri[0] == "agency_id", nome
        assert firma.parameters["agency_id"].default is inspect.Parameter.empty, nome
        # tutto il resto e' keyword-only: un argomento posizionale in piu' non
        # puo' scivolare al posto sbagliato.
        for altro in parametri[1:]:
            assert firma.parameters[altro].kind is inspect.Parameter.KEYWORD_ONLY, (nome, altro)


def test_13_nessuna_paginazione_a_offset_e_nessun_select_stella_verso_l_esterno():
    from acquisition import repository
    codice = _codice(repository)
    assert "OFFSET" not in codice.upper(), "keyset, mai OFFSET"
    # `RETURNING *` c'e' ed e' interno: cio' che esce e' filtrato per nome.
    assert set(repository.ACQUISITION_COLUMNS).isdisjoint({"stima_id_snapshot"})
    assert set(repository.INSPECTION_COLUMNS).isdisjoint({"stima_id_snapshot"})


def test_14_lo_snapshot_non_esce_mai_da_una_api():
    """E' un campo interno di sola ricostruzione storica. Nessun DTO lo porta."""
    from acquisition import repository
    for colonne in (repository.ACQUISITION_COLUMNS, repository.INSPECTION_COLUMNS):
        assert not [c for c in colonne if "snapshot" in c]


def test_15_nessuna_colonna_di_attore_esce_verso_il_client():
    from acquisition import repository
    for colonne in (repository.ACQUISITION_COLUMNS, repository.INSPECTION_COLUMNS):
        assert not [c for c in colonne if c.endswith("operator_user_id")], colonne


def test_16_gli_eventi_proiettati_sono_un_insieme_chiuso():
    from acquisition import repository
    assert repository.PROJECTED_EVENTS == (
        "acquisition_linked", "mandate_signed", "acquisition_revoked",
        "inspection_scheduled", "inspection_completed", "inspection_cancelled")
    assert repository.EVENT_SOURCE == "crm_acquisition"
    codice = _codice(repository)
    for chiave in repository.PROJECTED_EVENTS:
        assert f"lmc15:v1:" in codice
    assert codice.count("idempotency_key=f\"lmc15:v1:") == 6


def test_17_la_proiezione_usa_lo_STESSO_cursore_del_fatto():
    """`seller_timeline_events` e' una proiezione, non la fonte: se la riga di
    timeline fallisce deve fallire anche il fatto. Una connessione propria
    dentro `_proietta` lascerebbe i due liberi di divergere."""
    from acquisition import repository
    sorgente = inspect.getsource(repository._proietta)
    assert "core_cursor" not in sorgente
    assert "record_event_scoped" not in sorgente
    assert "_insert_event_with_agency" in sorgente
    assert "_assert_references_in_agency" in sorgente
    # E il cursore arriva da fuori, primo parametro.
    assert list(inspect.signature(repository._proietta).parameters)[0] == "cur"


def test_18_nessuna_euristica_di_somiglianza_da_nessuna_parte():
    """Il divieto centrale di LMC-15A: la relazione stima -> property e'
    DICHIARATA, mai dedotta."""
    from acquisition import repository, service
    testo = (_codice(repository) + _codice(service) + SU).lower()
    for vietato in ("similarity", "levenshtein", "soundex", "ilike", "trigram",
                    "pg_trgm", "fuzzy", "email_normalized", "telefono",
                    "indirizzo", "civico", "microzona", "difflib"):
        assert vietato not in testo, vietato


# ---------------------------------------------------------------------------
# D - LA MIGRATION, COME TESTO
# ---------------------------------------------------------------------------

def test_19_la_070_e_valida_per_il_runner_e_in_coda_alla_serie():
    from scripts import p26_migrate as runner
    trovate = {m.version: m for m in runner.discover_migrations()}
    assert "070_lmc15_acquisition_bridge" in trovate
    assert runner.validate_migration(trovate["070_lmc15_acquisition_bridge"]) == []
    numeri = sorted(m.number for m in trovate.values())
    # SENTINELLA AGGIORNATA DA P29-3B: la 070 segue la 069 senza buchi, come
    # LMC-15 voleva; in coda alla serie ora c'e' la 071 della journey
    # automation, approvata da P29-3A.1 (SCHEMA FROZEN). Si nomina invece di
    # smettere di guardare: una 072 farebbe ancora fallire questo test.
    assert numeri[numeri.index(70) - 1] == 69
    assert numeri[-1] == 71 and numeri[-2] == 70
    assert len(numeri) == len(set(numeri))


def test_20_il_ponte_non_porta_agency_id():
    """La tenancy e' DERIVATA da `stime` e `properties`. Una colonna propria
    sarebbe una terza verita' che puo' divergere dalle altre due."""
    corpo = SU[SU.index("CREATE TABLE IF NOT EXISTS stima_acquisitions"):]
    assert "agency_id" not in corpo.split("CREATE OR REPLACE FUNCTION")[0]
    # E la migration stessa lo verifica a runtime.
    assert "must not carry agency_id" in SQL_SU


def test_21_cardinalita_una_sola_origine_attiva_per_property():
    """UNIQUE PARZIALE su `property_id WHERE link_status='active'`, e NON su
    `stima_id`: una stima puo' generare piu' immobili (un frazionamento), una
    property nasce da una sola stima per volta."""
    assert ("CREATE UNIQUE INDEX IF NOT EXISTS idx_stima_acq_active_property\n"
            "    ON stima_acquisitions (property_id) WHERE link_status = 'active'") in SU
    assert "UNIQUE INDEX" not in SU.split("idx_stima_acq_stima")[1].split(";")[0]


def test_22_la_matrice_della_revoca_e_completa_nelle_due_direzioni():
    """ACTIVE: tutti e tre i campi NULL. REVOKED: tutti e tre presenti, e la
    ragione non vuota. Uno stato parziale non e' rappresentabile."""
    assert "stima_acq_active_chk" in SU and "stima_acq_revoked_chk" in SU
    attivo = SU.split("stima_acq_active_chk")[1].split("CONSTRAINT")[0]
    for campo in ("revoked_at", "revoked_by_operator_user_id", "revoked_reason"):
        assert f"{campo} IS NULL" in attivo, campo
    revocato = SU.split("stima_acq_revoked_chk")[1].split("CONSTRAINT")[0]
    for campo in ("revoked_at", "revoked_by_operator_user_id", "revoked_reason"):
        assert f"{campo} IS NOT NULL" in revocato, campo
    assert "BTRIM(revoked_reason) <> ''" in revocato


def test_23_il_mandato_e_una_tripla_o_niente():
    """Firma, registrazione e chi ha registrato stanno insieme: `num_nonnulls`
    IN (0,3) rende impossibile una firma senza autore."""
    assert "num_nonnulls" in SU
    tripla = SU.split("stima_acq_mandate_triple_chk")[1].split("CONSTRAINT")[0]
    assert "IN (0, 3)" in tripla or "IN (0,3)" in tripla
    for campo in ("mandate_signed_at", "mandate_recorded_at",
                  "mandate_recorded_by_operator_user_id"):
        assert campo in tripla, campo
    # E un riferimento senza firma non sta in piedi.
    assert "stima_acq_mandate_ref_chk" in SU
    assert "stima_acq_mandate_order_chk" in SU


def test_24_il_sopralluogo_annullato_deve_avere_una_data_di_appuntamento():
    """Annullare significa disdire un appuntamento: se non c'era, non c'e'
    niente da annullare. `completed` invece ammette `scheduled_for` NULL,
    perche' un sopralluogo avvenuto e mai fissato a sistema e' un caso vero."""
    annullato = SU.split("stima_insp_cancelled_chk")[1].split("CONSTRAINT")[0]
    assert "scheduled_for IS NOT NULL" in annullato
    concluso = SU.split("stima_insp_completed_chk")[1].split("CONSTRAINT")[0]
    assert "scheduled_for IS NOT NULL" not in concluso


def test_25_le_policy_di_cancellazione_sono_quelle_decise():
    """`stime` e' hard-deleted dal repo (main.py:622), `properties` non lo e'
    mai. Da qui: SET NULL verso la stima con lo snapshot a conservarne il
    numero, RESTRICT verso la property e verso gli operatori."""
    assert SU.count("REFERENCES stime(id) ON DELETE SET NULL") == 2
    assert "REFERENCES properties(id) ON DELETE RESTRICT" in SU
    # Sei attori in tutto: tre sul link (linked_by, mandate_recorded_by,
    # revoked_by) e tre sul sopralluogo (created_by, completed_by,
    # cancelled_by). Nessuno di loro si puo' cancellare lasciando un registro
    # che non dice piu' chi ha fatto cosa.
    assert SU.count("REFERENCES operator_users(id) ON DELETE RESTRICT") == 6
    # Lo snapshot e' NOT NULL su entrambe (l'allineamento delle colonne nel
    # file differisce, quindi si normalizzano gli spazi prima di contare) e
    # ciascuna tabella impone che, finche' la stima c'e', i due coincidano.
    import re as _re
    compatto = _re.sub(r"[ \t]+", " ", SU)
    assert compatto.count("stima_id_snapshot INTEGER NOT NULL") == 2
    assert SU.count("CHECK (stima_id IS NULL OR stima_id = stima_id_snapshot)") == 2


def test_26_la_discesa_rifiuta_di_cancellare_dati():
    """Una `down` che porta via righe reali e' una perdita di dati travestita
    da rollback."""
    assert "RAISE EXCEPTION" in GIU
    assert "DROP TABLE" in GIU
    for tabella in ("stima_acquisitions", "stima_inspections"):
        assert tabella in GIU
    assert "DELETE FROM schema_migrations" in GIU


def test_27_nessun_backfill_storico():
    """NO BACKFILL e' una decisione, non una dimenticanza: la migration crea
    le tabelle vuote e non prova a indovinare un solo legame dal passato."""
    corpo = SU.upper()
    assert "INSERT INTO STIMA_ACQUISITIONS" not in corpo
    assert "INSERT INTO STIMA_INSPECTIONS" not in corpo


# ---------------------------------------------------------------------------
# E - LE METRICHE (decisioni pure)
# ---------------------------------------------------------------------------

ORA = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
VUOTI = {"cohort_homes": 10}


def test_28_le_due_metriche_del_ponte_sono_un_insieme_chiuso():
    from owner import home_metrics as hm
    assert hm.BRIDGE_COUNTS == ("inspection_homes", "mandate_homes")
    assert hm.BRIDGE_RATES == {"inspection_rate": "inspection_homes",
                               "mandate_rate": "mandate_homes"}
    # Il denominatore e' lo stesso di tutti gli altri tassi.
    assert set(hm.RATES.values()) | set(hm.BRIDGE_RATES.values())
    assert hm.UNIT == "stima"


def test_29_la_misurabilita_dipende_dal_registro_non_da_una_costante():
    from owner import home_metrics as hm
    assert hm.bridge_measurable(None, ORA) is False
    acceso = ORA - timedelta(days=10)
    assert hm.bridge_measurable(acceso, acceso) is True
    assert hm.bridge_measurable(acceso, acceso + timedelta(seconds=1)) is True
    assert hm.bridge_measurable(acceso, acceso - timedelta(seconds=1)) is False


def test_30_le_due_ragioni_del_null_sono_distinte():
    from owner import home_metrics as hm
    assert hm.bridge_reason(None) == hm.REASON_NOT_APPLIED
    assert hm.bridge_reason(ORA) == hm.REASON_BEFORE_START
    assert hm.REASON_NOT_APPLIED != hm.REASON_BEFORE_START
    for ragione in (hm.REASON_NOT_APPLIED, hm.REASON_BEFORE_START):
        assert len(ragione) > 20, "una ragione deve spiegare, non etichettare"


def test_31_null_e_zero_restano_due_risposte_diverse():
    from owner import home_metrics as hm
    da, a = hm.window(30, now=ORA)
    # Ponte spento: assente.
    spento = hm.build(VUOTI, days=30, cohort_from=da, cohort_to=a)
    assert spento["inspection_homes"] is None and spento["mandate_homes"] is None
    assert set(spento["not_measurable"]) == set(hm.BRIDGE_COUNTS)
    # Ponte acceso e coorte dentro: misurato, e zero e' un numero.
    acceso = hm.build({**VUOTI}, days=30, cohort_from=da, cohort_to=a,
                      measurement_started_at=da)
    assert acceso["inspection_homes"] == 0 and acceso["mandate_homes"] == 0
    assert acceso["rates"]["inspection_rate"] == 0.0
    assert acceso["not_measurable"] == {}
    assert acceso["measurement_started_at"] == hm._iso(da)


def test_32_una_coorte_vuota_lascia_i_tassi_a_null_anche_col_ponte_acceso():
    """Zero su zero non e' zero: e' non misurabile, come per ogni altro tasso."""
    from owner import home_metrics as hm
    da, a = hm.window(30, now=ORA)
    d = hm.build({"cohort_homes": 0, "inspection_homes": 0, "mandate_homes": 0},
                 days=30, cohort_from=da, cohort_to=a, measurement_started_at=da)
    assert d["inspection_homes"] == 0
    assert d["rates"]["inspection_rate"] is None
    assert d["rates"]["view_rate"] is None


def test_33_il_conteggio_del_ponte_non_entra_nel_dto_quando_non_si_misura():
    """Anche se la query - per errore - producesse quei numeri, il DTO non li
    mostra finche' la misura non e' cominciata."""
    from owner import home_metrics as hm
    da, a = hm.window(30, now=ORA)
    d = hm.build({**VUOTI, "inspection_homes": 99, "mandate_homes": 99},
                 days=30, cohort_from=da, cohort_to=a, measurement_started_at=None)
    assert d["inspection_homes"] is None and d["mandate_homes"] is None


def test_34_la_versione_della_migration_non_e_una_data_scritta_a_mano():
    from owner import repository as owner_repository
    assert owner_repository.BRIDGE_MIGRATION_VERSION == "070_lmc15_acquisition_bridge"
    assert MIGRAZIONE.name == owner_repository.BRIDGE_MIGRATION_VERSION + ".sql"
    sorgente = (ROOT / "owner" / "home_metrics.py").read_text(encoding="utf-8")
    sorgente += (ROOT / "owner" / "repository.py").read_text(encoding="utf-8")
    import re
    assert not re.search(r"20\d\d-\d\d-\d\d", sorgente), "nessuna data hardcodata"


def test_35_la_query_del_ponte_e_una_variante_non_una_seconda_verita():
    """Le due varianti condividono la parte comune: i numeri che hanno in
    comune non possono divergere."""
    from owner import repository as owner_repository
    base = owner_repository._HOME_METRICS_SQL
    esteso = owner_repository._HOME_METRICS_SQL_BRIDGE
    assert owner_repository._HOME_METRICS_CTE in base
    assert owner_repository._HOME_METRICS_CTE in esteso
    assert owner_repository._HOME_METRICS_SELECT in base
    assert owner_repository._HOME_METRICS_SELECT in esteso
    for nome in ("stima_inspections", "stima_acquisitions"):
        assert nome not in base, nome
        assert nome in esteso, nome


def test_36_il_denominatore_dei_due_tassi_nuovi_e_cohort_homes():
    from owner import home_metrics as hm
    da, a = hm.window(30, now=ORA)
    d = hm.build({"cohort_homes": 4, "inspection_homes": 1, "mandate_homes": 2},
                 days=30, cohort_from=da, cohort_to=a, measurement_started_at=da)
    assert d["rates"]["inspection_rate"] == 0.25
    assert d["rates"]["mandate_rate"] == 0.5


# ---------------------------------------------------------------------------
# F - LE SENTINELLE
# ---------------------------------------------------------------------------

def test_37_nessuna_migration_oltre_la_070():
    """SENTINELLA AGGIORNATA DA P29-3B, in due punti.

    Prima: LMC-15 e' COMMITTATA (7aa76b0), quindi la 070 non compare piu'
    nel working tree ma nell'indice - e' questo che si verifica, con
    `git ls-files`, invece di un elenco di non tracciati che dopo il commit
    diceva soltanto "LMC-15 non e' in corso".

    Poi: la sola migration nuova nel working tree e' la 071 della journey
    automation, approvata da P29-3A.1. Si nomina invece di smettere di
    guardare: qualunque ALTRA migration comparisse farebbe ancora fallire.
    """
    tracciate = set(subprocess.run(
        ["git", "--no-optional-locks", "ls-files", "--", "migrations/070_*"],
        cwd=ROOT, capture_output=True, text=True).stdout.split())
    assert tracciate == {"migrations/070_lmc15_acquisition_bridge.sql",
                         "migrations/070_lmc15_acquisition_bridge_down.sql"}
    nuovi = {riga[3:].strip() for riga in subprocess.run(
        ["git", "--no-optional-locks", "status", "--porcelain", "--", "migrations/"],
        cwd=ROOT, capture_output=True, text=True).stdout.splitlines()}
    atteso = {"migrations/071_p29_3_journey_automation.sql",
              "migrations/071_p29_3_journey_automation_down.sql"}
    assert nuovi <= atteso, sorted(nuovi - atteso)


def test_38_nessuna_migration_storica_e_stata_toccata():
    """Il ledger e' append-only: una migration gia' applicata che cambia e'
    una migration che cambia sotto i piedi di chi l'aveva applicata.

    SENTINELLA AGGIORNATA DA P29-3B (collisione dichiarata con LMC-15, che
    e' committata). La versione originale pretendeva che OGNI riga di
    `git status` sotto `migrations/` fosse `??`, e cosi' scritta diceva due
    cose insieme: "nessuna migration storica e' cambiata" - che e' la
    garanzia - e "nessuna migration nuova e' in stage" - che non lo e'. La
    seconda rendeva il test rosso nella finestra fra `git add` e
    `git commit` di QUALUNQUE fase successiva, cioe' in uno stato normale
    del repository, e verde solo per l'assenza di lavoro in corso.

    La garanzia non cambia, cambia cio' che si misura: una migration gia'
    TRACCIATA non puo' risultare modificata, cancellata o rinominata, in
    stage o nel working tree. Una migration NUOVA puo' essere non tracciata
    (`??`) o aggiunta (`A`), e nient'altro: e' esattamente cio' che una
    fase autorizzata a crearne una fa. Qualunque `M`, `D` o `R` su un file
    di `migrations/` fa ancora fallire questo test.
    """
    righe = subprocess.run(
        ["git", "--no-optional-locks", "status", "--porcelain", "--", "migrations/"],
        cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    tracciate = set(subprocess.run(
        ["git", "--no-optional-locks", "ls-files", "--", "migrations/"],
        cwd=ROOT, capture_output=True, text=True).stdout.split())
    for riga in righe:
        stato, percorso = riga[:2], riga[3:].strip()
        assert stato.strip() in ("??", "A"), riga
        if stato.strip() == "A":
            # Un'aggiunta: il file non esisteva nel commit precedente. Se
            # esisteva, `git status` direbbe `M`, e siamo nel caso di sopra.
            assert percorso in tracciate, riga


def test_39_nessun_cron_nuovo():
    """Il ponte e' fatto di gesti di un operatore. Non c'e' niente da girare
    la notte, e un cron sarebbe l'inizio di una deduzione automatica."""
    testo = (_codice(__import__("acquisition.repository", fromlist=["x"]))
             + SU)
    for vietato in ("advisory_lock", "pg_try_advisory_lock", "cron", "schedule("):
        assert vietato not in testo, vietato
    nuovi = subprocess.run(
        ["git", "--no-optional-locks", "status", "--porcelain", "--", "run_*.py"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert nuovi == "", nuovi


def test_40_il_ponte_non_tocca_i_domini_vicini():
    diff = subprocess.run(
        ["git", "--no-optional-locks", "diff", "--name-only", "--",
         "seller_intent/", "next_best_action/",
         "followup/", "property_watch/",
         "valuation.py", "database.py"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert diff == "", diff
    # P29-3C (collisione autorizzata, dichiarata): `seller_intelligence/`
    # esce dall'elenco IN BLOCCO perche' quella fase vi aggiunge UN flag -
    # `lock_stima` - che blocca la stima nella stessa transazione
    # dell'evento, per il solo evento che il motore delle journey tratta
    # come fatto di stop. Non smette di essere guardato: i file toccati
    # devono essere esattamente i due dichiarati, e il flag deve essere
    # spento per default, altrimenti questo test fallisce ancora.
    toccati = subprocess.run(
        ["git", "--no-optional-locks", "diff", "--name-only", "--", "seller_intelligence/"],
        cwd=ROOT, capture_output=True, text=True).stdout.split()
    from tests.p29_3c_diff import FILE_MODIFICATI as MOD_3C
    assert set(toccati) <= MOD_3C, sorted(set(toccati) - MOD_3C)
    import inspect as _inspect
    from seller_intelligence import repository as si_repo
    assert "lock_stima: bool = False" in _inspect.getsource(si_repo.insert_event_scoped)
    # P29-3B (collisione autorizzata, dichiarata): `communication/` e `operator_auth/` esce
    # dall'elenco IN BLOCCO perche' P29-3B vi estende `enqueue` con la
    # provenienza di journey e aggiunge l'origine `public_unsubscribe`. Non smette di essere guardato: il diff di
    # quei domini viene controllato file per file e riga per riga qui sotto,
    # e qualunque modifica che non sia quella dichiarata fa ancora fallire.
    from tests.p29_3b_diff import diff_imprevisto_nei_domini
    assert diff_imprevisto_nei_domini(ROOT) == [], diff_imprevisto_nei_domini(ROOT)


def test_41_i_file_toccati_sono_solo_quelli_dichiarati():
    """L'ELENCO APPROVATO DI LMC-15B, modificati e nuovi.

    Scritto a mano e non derivato da `git`: un elenco ricavato dallo stato
    del working tree direbbe soltanto che il working tree e' uguale a se'
    stesso.

    SENTINELLA AGGIORNATA DA P29-3B: LMC-15 e' COMMITTATA (7aa76b0), e un
    confronto con `git status` diceva, dopo il commit, che LMC-15 "non aveva
    toccato niente" - cioe' niente. La verifica cambia forma e resta: ogni
    file dell'elenco e' nell'indice (`git ls-files`), e nel working tree ne
    e' modificato solo cio' che la fase successiva, P29-3B, dichiara per nome
    in `p29_3b_diff` - qualunque altro file di LMC-15 toccato fa fallire.

    `P29_2_0_COMMUNICATION_DESIGN.md` compare fra i non tracciati e NON e'
    di questa fase: e' un documento che deve restare fuori dall'indice, e
    questo test e' anche il posto in cui si verifica che LMC-15 non lo abbia
    ne' toccato ne' messo in stage.
    """
    from tests.p29_3b_diff import FILE_MODIFICATI as MODIFICATI_P29_3B
    # P29-3C dichiara il proprio inventario allo stesso modo: fra i suoi
    # file c'e' `acquisition/repository.py`, che prende il fence sulla
    # stima. Si guarda l'unione delle due dichiarazioni - nessuna fase puo'
    # toccare un file di LMC-15 senza averlo scritto nel proprio elenco.
    from tests.p29_3c_diff import FILE_MODIFICATI as MODIFICATI_P29_3C

    lmc15_modificati = {
        # Il codice: le metriche di LMC-13 estese, e il router montato.
        "main.py", "owner/home_metrics.py", "owner/repository.py",
        "owner/router_admin.py", "owner/schemas.py",
        # La governance: la matrice ostile guadagna un dominio, l'inventario
        # delle connessioni una voce, l'elenco dei prefissi di tenant un
        # prefisso.
        "scripts/p26_6_live_cert.py", "docs/P26_DB_ENTRYPOINTS.md",
        "tests/test_p26_db_entrypoints.py", "tests/test_p26_5_basic_containment.py",
        "tests/test_p26_6_live_cert_script.py",
        # Le sentinelle di fase che la 070 e la modifica a `main.py`
        # superano legittimamente. Ognuna NOMINA cio' che ammette: nessuna
        # ha smesso di guardare.
        "tests/test_lmc13_home_metrics.py",
        "tests/test_lmc13_home_metrics_postgres.py",
        "tests/test_lmc1b_owner_login_link.py",
        "tests/test_lmc2_owner_homes.py",
        "tests/test_lmc3_valuation_snapshot.py",
        "tests/test_lmc7_owner_radar.py",
        "tests/test_lmc8_crm_radar.py",
        "tests/test_lmc9_consultation_request.py",
        "tests/test_lmc11_valuation_cron.py",
        "tests/test_p27_6_lead_routing.py",
        "tests/test_p29_1_consent_migrations.py",
        "tests/test_p29_2_1_communication_foundation.py",
        "tests/test_p29_2_2_communication_service.py",
        "tests/test_p29_2_3_claim_sentinels.py",
        "tests/test_p29_2_4_dispatch_sentinels.py",
        "tests/test_p29_2_5e_email_adapter.py",
    }
    lmc15_nuovi = {
        "acquisition/__init__.py",
        "acquisition/repository.py", "acquisition/router.py",
        "acquisition/schemas.py", "acquisition/service.py",
        "migrations/070_lmc15_acquisition_bridge.sql",
        "migrations/070_lmc15_acquisition_bridge_down.sql",
        "tests/lmc15_main_diff.py",
        "tests/test_lmc15_acquisition_bridge.py",
        "tests/test_lmc15_acquisition_bridge_postgres.py",
    }
    tracciati = set(subprocess.run(
        ["git", "--no-optional-locks", "ls-files"],
        cwd=ROOT, capture_output=True, text=True).stdout.split())
    mancanti = (lmc15_modificati | lmc15_nuovi) - tracciati
    assert mancanti == set(), sorted(mancanti)
    assert "P29_2_0_COMMUNICATION_DESIGN.md" not in tracciati

    righe = subprocess.run(
        ["git", "--no-optional-locks", "status", "--porcelain"],
        cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    modificati = {r[3:].strip() for r in righe if not r.startswith("??")}
    nuovi = {r[3:].strip() for r in righe if r.startswith("??")}
    # Di LMC-15 nel working tree puo' essere toccato solo cio' che P29-3B
    # dichiara; e la 070 non puo' comparire ne' modificata ne' nuova.
    fuori = ((modificati & (lmc15_modificati | lmc15_nuovi))
             - MODIFICATI_P29_3B - MODIFICATI_P29_3C)
    assert fuori == set(), sorted(fuori)
    assert not any(n.startswith("migrations/070_") for n in modificati | nuovi)
    assert "P29_2_0_COMMUNICATION_DESIGN.md" in nuovi


def test_42_il_documento_di_p29_2_0_resta_fuori_dall_indice():
    """Un vincolo permanente del progetto, ricordato qui perche' LMC-15 e' la
    prima fase che tocca `main.py` dopo che e' stato posto."""
    stato = subprocess.run(
        ["git", "--no-optional-locks", "status", "--porcelain", "--",
         "P29_2_0_COMMUNICATION_DESIGN.md"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert stato.startswith("??"), stato


def test_43_i_due_numeratori_nominano_lo_stato_CHE_RICHIEDONO():
    """UNA SENTINELLA DI TESTO, e va detto perche' lo e'.

    `a.link_status = 'active'` e' portante: un link revocato ha ancora la sua
    `mandate_signed_at`, e toglierlo cambia i numeri - lo prova il test 84 su
    PostgreSQL.

    `i.status = 'completed'` invece OGGI non cambia nessun numero: la matrice
    di CHECK della 070 impone `completed_at IS NULL` in ogni altro stato, e il
    confronto sulle date esclude da solo i sopralluoghi fissati e annullati.
    Neutralizzarlo lascia la suite verde, e l'ho verificato invece di
    supporlo.

    Si tiene lo stesso, e questa prova e' il motivo per cui puo' restare: se
    un giorno la matrice ammettesse `completed_at` in uno stato diverso -
    "avvenuto ma poi disdetto", per dire - la sua assenza diventerebbe un
    difetto silenzioso che nessuna prova di comportamento vedrebbe nascere.
    Una sentinella di testo e' una prova debole, e la si dichiara debole
    invece di farla passare per una prova di comportamento.
    """
    from owner import repository as owner_repository
    ponte = owner_repository._BRIDGE_CTE
    assert "i.status = 'completed'" in ponte
    assert "a.link_status = 'active'" in ponte
    assert "a.mandate_signed_at IS NOT NULL" in ponte
