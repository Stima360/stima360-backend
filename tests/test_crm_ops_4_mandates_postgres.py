"""CRM-OPS-4 - INCARICHI e STORICO INTERAZIONI su PostgreSQL vero.

Banco: quello di CRM-OPS-3 (`tests/test_crm_ops_3_acquisitions_postgres.py`:
agenzie A/B, operatori, contatti, immobili, Agenda, la 081 vera) piu'
`activities` con le colonne REALI di 001 e 028, la funzione
`core_agency_integrity()` REALE di 033 col trigger di 030, e la 082 vera.
Router VERI (Acquisizioni, Agenda, Immobili) via TestClient.

Gira solo con P29_TEST_DSN su un cluster locale (stessa guardia del banco
CRM-OPS-3): mai TEST, mai PROD.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests import test_crm_ops_3_acquisitions_postgres as ops3
from tests.test_crm_ops_3_acquisitions_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _solo_locale, k, schema_acq)
from tests.test_a30_2_appointments_postgres import db, mondo  # noqa: F401
from tests.test_a30_13b_create_permissions_postgres import agenda_completa, w  # noqa: F401
from tests.test_a31_2_buyer_visits_postgres import schema_a31, v  # noqa: F401

pytestmark = ops3.pytestmark

MIGRAZIONI = ops3.MIGRAZIONI
VERSIONE = "082_crm_ops_4_property_interactions"


def _da_migration(file, inizio, fine):
    testo = (MIGRAZIONI / file).read_text(encoding="utf-8")
    i = testo.index(inizio)
    j = testo.index(fine, i) + len(fine)
    return testo[i:j]


#: `activities` come la lasciano 001 (colonne e CHECK) e 028/030 (agenzia,
#: autore): il banco CRM-OPS-3 ne ha solo lo scheletro (id).
ACTIVITIES_REALI = """
ALTER TABLE activities
    ADD COLUMN IF NOT EXISTS contact_id BIGINT REFERENCES contacts(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS lead_id BIGINT REFERENCES leads(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS stima_id INTEGER,
    ADD COLUMN IF NOT EXISTS activity_type VARCHAR(30) NOT NULL DEFAULT 'note',
    ADD COLUMN IF NOT EXISTS direction VARCHAR(20),
    ADD COLUMN IF NOT EXISTS channel VARCHAR(50),
    ADD COLUMN IF NOT EXISTS subject VARCHAR(200),
    ADD COLUMN IF NOT EXISTS description TEXT,
    ADD COLUMN IF NOT EXISTS outcome VARCHAR(100),
    ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS created_by VARCHAR(200),
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS created_by_user_id BIGINT REFERENCES operator_users(id) ON DELETE SET NULL;
ALTER TABLE activities ALTER COLUMN activity_type DROP DEFAULT;
-- 002: il prezzo minimo dell'immobile (il banco CRM-OPS-3 non lo usava)
ALTER TABLE properties ADD COLUMN IF NOT EXISTS minimum_price NUMERIC(14,2);
-- 028: l'assegnatario del contatto, su cui core.scope restringe l'agente
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS assigned_agent_id BIGINT REFERENCES operator_users(id);
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'activities_type_chk') THEN
    ALTER TABLE activities ADD CONSTRAINT activities_type_chk CHECK (activity_type IN (
        'note', 'call', 'email', 'whatsapp', 'meeting', 'valuation', 'status_change', 'system'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'activities_reference_chk') THEN
    ALTER TABLE activities ADD CONSTRAINT activities_reference_chk CHECK (
        contact_id IS NOT NULL OR lead_id IS NOT NULL OR stima_id IS NOT NULL);
  END IF;
END $$;
"""


@pytest.fixture(scope="module")
def schema_ops4(schema_acq):  # noqa: F811
    conn = schema_acq["conn"]
    with conn.cursor() as cur:
        cur.execute(ACTIVITIES_REALI)
        cur.execute(_da_migration("033_p26_stima_agency_enforce.sql",
                                  "CREATE OR REPLACE FUNCTION core_agency_integrity()",
                                  "$fn$ LANGUAGE plpgsql;"))
        cur.execute("DROP TRIGGER IF EXISTS trg_activities_agency_integrity ON activities")
        cur.execute("CREATE TRIGGER trg_activities_agency_integrity "
                    "BEFORE INSERT OR UPDATE OF agency_id, contact_id, lead_id, stima_id "
                    "ON activities FOR EACH ROW EXECUTE FUNCTION core_agency_integrity()")
        cur.execute((MIGRAZIONI / f"{VERSIONE}.sql").read_text(encoding="utf-8"))
    conn.commit()
    return schema_acq


def _svuota_attivita(sql):
    """Solo per il banco: lo storico d'immobile NON si cancella (trigger 082),
    e qui lo si spegne per la sola pulizia, come `_pulisci` di CRM-OPS-3 fa
    con le acquisizioni."""
    if sql("SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_activities_property_history'")[0][0]:
        sql("ALTER TABLE activities DISABLE TRIGGER trg_activities_property_history")
        sql("DELETE FROM activities")
        sql("ALTER TABLE activities ENABLE TRIGGER trg_activities_property_history")
    else:
        sql("DELETE FROM activities")


@pytest.fixture
def m(k, schema_ops4):  # noqa: F811
    _svuota_attivita(k["sql"])
    yield k
    k["conn"].rollback()
    _svuota_attivita(k["sql"])


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------

def _incarico(m, chi="giorgio", **kw):
    """Acquisizione -> sopralluogo -> incarico (il contratto CRM-OPS-3)."""
    det = ops3._pronta(m, chi=chi)
    corpo = dict(ops3._incarico(), version=det["version"])
    corpo.update(kw)
    r = m["api"](chi).post(f"/api/acquisitions/{det['id']}/mandate", json=corpo)
    assert r.status_code == 200, r.text
    return r.json()


def _elenco(m, chi="giorgio", **filtri):
    r = m["api"](chi).get("/api/property/mandates", params=filtri)
    assert r.status_code == 200, r.text
    return r.json()


def _nota(m, property_id, chi="giorgio", **kw):
    corpo = {"interaction_type": "call", "note": "Ho chiamato Mario: richiamare lunedi'"}
    corpo.update(kw)
    return m["api"](chi).post(f"/api/property/properties/{property_id}/interactions", json=corpo)


def _conta(m, tabella, where="TRUE", par=None):
    return m["sql"](f"SELECT count(*) FROM {tabella} WHERE {where}", par)[0][0]


# ---------------------------------------------------------------------------
# A - MIGRATION 082
# ---------------------------------------------------------------------------

def test_01_migrazione_082_oggetti(m):
    sql = m["sql"]
    assert sql("SELECT is_nullable FROM information_schema.columns WHERE table_name = 'activities' "
               "AND column_name = 'property_id'")[0][0] == "YES"
    definizione = sql("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                      "WHERE conname = 'activities_reference_chk'")[0][0]
    assert "property_id IS NOT NULL" in definizione
    assert _conta(m, "pg_trigger", "tgname = 'trg_activities_property_history'") == 1
    assert sql("SELECT confdeltype FROM pg_constraint WHERE conname = 'activities_property_id_fkey'")[0][0] == "r"
    assert _conta(m, "pg_trigger", "tgname = 'trg_activities_property_scope'") == 1
    assert _conta(m, "pg_indexes", "indexname = 'idx_activities_agency_property_occurred'") == 1


def test_02_down_rifiuta_con_dati_e_riapplicazione_idempotente(m):
    import psycopg2
    sql = m["sql"]
    giu = (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")
    su = (MIGRAZIONI / f"{VERSIONE}.sql").read_text(encoding="utf-8")
    assert _nota(m, m["casa"]).status_code == 201
    with pytest.raises(psycopg2.Error, match="082 down"):
        sql(giu)
    sql("ROLLBACK")
    assert _conta(m, "activities", "property_id IS NOT NULL") == 1
    _svuota_attivita(sql)
    try:
        sql(giu)
        assert _conta(m, "information_schema.columns",
                      "table_name = 'activities' AND column_name = 'property_id'") == 0
        assert "property_id" not in sql("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                                         "WHERE conname = 'activities_reference_chk'")[0][0]
    finally:
        sql(su)
        sql(su)
    assert _conta(m, "pg_trigger", "tgname = 'trg_activities_property_scope'") == 1


def test_03_guardie_del_database(m):
    """Il trigger rifiuta l'immobile di un'altra agenzia e il referente non
    collegato all'immobile, anche scrivendo a mano; senza riferimenti la
    riga resta rifiutata come prima."""
    import psycopg2
    sql = m["sql"]
    with pytest.raises(psycopg2.errors.CheckViolation, match="CRM-OPS-4 tenancy"):
        sql("INSERT INTO activities (agency_id, property_id, activity_type, description) "
            "VALUES (%s, %s, 'note', 'x')", (m["a"], m["altra"]))
    m["conn"].rollback()
    with pytest.raises(psycopg2.errors.CheckViolation, match="not linked"):
        sql("INSERT INTO activities (agency_id, property_id, contact_id, activity_type, description) "
            "VALUES (%s, %s, %s, 'call', 'x')", (m["a"], m["vuota"], m["mario"]))
    m["conn"].rollback()
    with pytest.raises(psycopg2.Error):
        # contatto di B su immobile di A: l'agenzia esplicita contraddice il contatto (030)
        sql("INSERT INTO activities (agency_id, property_id, contact_id, activity_type, description) "
            "VALUES (%s, %s, %s, 'call', 'x')", (m["a"], m["casa"], m["contatto_b"]))
    m["conn"].rollback()
    with pytest.raises(psycopg2.errors.CheckViolation, match="activities_reference_chk"):
        sql("INSERT INTO activities (agency_id, activity_type, description) VALUES (%s, 'note', 'x')",
            (m["a"],))
    m["conn"].rollback()
    # un'attivita' "vecchia maniera" (solo contatto) e' intatta
    sql("INSERT INTO activities (contact_id, activity_type, description) VALUES (%s, 'call', 'x')",
        (m["mario"],))
    assert _conta(m, "activities", "property_id IS NULL AND agency_id = %s", (m["a"],)) == 1
    # e l'immobile non si cancella sotto il suo storico
    _nota(m, m["vuota"])
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        sql("DELETE FROM properties WHERE id = %s", (m["vuota"],))
    m["conn"].rollback()


# ---------------------------------------------------------------------------
# B - INCARICHI: una vista, alimentata solo dall'acquisizione
# ---------------------------------------------------------------------------

def test_04_l_incarico_generato_compare_da_solo_gli_altri_no(m):
    sql = m["sql"]
    assert _elenco(m)["items"] == []
    # un incarico STORICO (senza acquisizione) non e' un incarico CRM-OPS-4
    sql("ALTER TABLE properties DISABLE TRIGGER trg_properties_mandate_origin")
    sql("UPDATE properties SET mandate_type = 'Storico', mandate_start = '2026-01-01', "
        "commercial_status = 'mandate' WHERE id = %s", (m["seconda"],))
    sql("ALTER TABLE properties ENABLE TRIGGER trg_properties_mandate_origin")
    # un'acquisizione aperta (al sopralluogo) non e' ancora un incarico
    det = ops3._pronta(m)
    assert _elenco(m)["items"] == []
    r = m["api"]("giorgio").post(f"/api/acquisitions/{det['id']}/mandate",
                                 json=dict(ops3._incarico(), version=det["version"]))
    assert r.status_code == 200, r.text
    acq = r.json()
    voci = _elenco(m)["items"]
    assert [v["property_id"] for v in voci] == [m["casa"]]
    v = voci[0]
    assert v["acquisition"]["id"] == acq["id"] and v["acquisition"]["status"] == "acquired"
    assert v["acquisition"]["visible"] is True
    assert (v["mandate_type"], v["mandate_start"]) == ("Esclusiva", date.today().isoformat())
    assert v["days_to_expiry"] == 180 and v["expiry_state"] == "active"
    assert v["commercial_status"] == "mandate"
    assert v["asking_price"] == "179000.00" or Decimal(str(v["asking_price"])) == Decimal("179000")
    assert v["main_owner_id"] == m["mario"] and v["main_owner_name"] == "Mario Rossi"
    assert [o["contact_id"] for o in v["owners"]] == [m["mario"], m["bruno"]]
    assert [o["contact_id"] for o in v["other_owners"]] == [m["bruno"]]
    assert v["owners"][0]["is_main"] is True and v["owners"][1]["roles"] == ["seller"]
    assert v["agent_id"] == m["luca"] and v["agent_name"]          # l'agente dell'acquisizione
    assert v["last_interaction"] is None
    assert "Esclusiva" in _elenco(m)["mandate_types"]


def test_05_dettaglio_collegamenti_e_404(m):
    acq = _incarico(m)
    r = m["api"]("giorgio").get(f"/api/property/mandates/{m['casa']}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["property_id"] == m["casa"] and d["acquisition"]["id"] == acq["id"]
    assert d["agreed_price"] == "179000.00"
    assert {t["value"] for t in d["interaction_options"]["types"]} == {"call", "meeting", "note", "email", "whatsapp"}
    # un immobile senza incarico, uno di un'altra agenzia: 404
    assert m["api"]("giorgio").get(f"/api/property/mandates/{m['vuota']}").status_code == 404
    assert m["api"]("giorgio").get(f"/api/property/mandates/{m['altra']}").status_code == 404
    assert m["api"]("estraneo").get(f"/api/property/mandates/{m['casa']}").status_code == 404
    assert _elenco(m, "estraneo")["items"] == []


def test_06_scope_agente_come_gli_immobili_acquisizione_solo_se_sua(m):
    """L'incarico e' un immobile: l'agente lo vede come vede gli immobili
    (agenzia intera). L'acquisizione d'origine segue la regola delle
    Acquisizioni: apribile solo dal suo agente o da chi vede tutto."""
    _incarico(m, chi="luca")
    luca = _elenco(m, "luca")["items"][0]
    marta = _elenco(m, "marta")["items"][0]
    assert luca["acquisition"]["visible"] is True
    assert marta["acquisition"]["visible"] is False
    assert m["api"]("marta").get(f"/api/acquisitions/{marta['acquisition']['id']}").status_code == 404
    # nessun allargamento: Marta vedeva gia' immobile, incarico e origine
    # dalla scheda Immobile (GET /api/property/properties/{id})
    scheda = m["api"]("marta").get(f"/api/property/properties/{m['casa']}")
    assert scheda.status_code == 200, scheda.text
    s = scheda.json()
    assert (s["mandate_type"], s["acquisition_id"]) == ("Esclusiva", marta["acquisition"]["id"])


def test_07_filtri_scadenze_e_ordinamento(m):
    sql = m["sql"]
    oggi = date.today()
    _incarico(m)                                              # casa: +180 giorni
    # altri tre incarichi veri, su immobili nuovi con Mario proprietario
    pid = {}
    for nome, giorni, citta, ora in (("tre", 3, "Teramo", 12), ("dodici", 12, "Giulianova", 14),
                                     ("scaduto", -5, "Teramo", 16)):
        pid[nome] = ops3._altro_immobile(m, f"Immobile {nome}")
        sql("UPDATE properties SET city = %s WHERE id = %s", (citta, pid[nome]))
        det = ops3._pronta(m, h=ora, property_id=pid[nome],
                           appointment=ops3._appuntamento(m["luca"], h=ora))
        fine = (oggi + timedelta(days=giorni))
        inizio = min(oggi, fine) - timedelta(days=1)
        r = m["api"]("giorgio").post(f"/api/acquisitions/{det['id']}/mandate", json={
            "version": det["version"], "mandate_type": "Non esclusiva" if nome == "dodici" else "Esclusiva",
            "mandate_start": inizio.isoformat(), "mandate_end": fine.isoformat()})
        assert r.status_code == 200, r.text
    ids = lambda **f: [v["property_id"] for v in _elenco(m, **f)["items"]]  # noqa: E731
    assert ids() == [pid["scaduto"], pid["tre"], pid["dodici"], m["casa"]]   # per scadenza
    assert ids(expiry="within_7") == [pid["tre"]]
    assert ids(expiry="within_15") == [pid["tre"], pid["dodici"]]
    assert ids(expiry="within_30") == [pid["tre"], pid["dodici"]]
    assert ids(expiry="expired") == [pid["scaduto"]]
    assert ids(city="teramo") == [pid["scaduto"], pid["tre"]]
    assert ids(mandate_type="non esclusiva") == [pid["dodici"]]
    assert ids(agent_id=m["luca"]) == ids() and ids(agent_id=m["marta"]) == []
    assert ids(search="Immobile tre") == [pid["tre"]]
    assert ids(search="mario") == ids()                         # anche per proprietario
    assert ids(commercial_status="mandate") == ids()
    assert ids(sort="start")[0] == m["casa"]
    stati = {v["property_id"]: (v["days_to_expiry"], v["expiry_state"]) for v in _elenco(m)["items"]}
    assert stati[pid["tre"]] == (3, "expiring") and stati[pid["scaduto"]] == (-5, "expired")
    # un incarico poi venduto/archiviato resta incarico; l'archiviato esce dal default
    sql("UPDATE properties SET commercial_status = 'active' WHERE id = %s", (m["casa"],))
    assert m["casa"] in ids() and ids(commercial_status="active") == [m["casa"]]
    sql("UPDATE properties SET commercial_status = 'archived', archived_at = NOW() WHERE id = %s",
        (pid["tre"],))
    assert pid["tre"] not in ids() and ids(commercial_status="archived") == [pid["tre"]]
    for sbagliato in ({"expiry": "domani"}, {"sort": "prezzo"}):
        r = m["api"]("giorgio").get("/api/property/mandates", params=sbagliato)
        assert r.status_code == 400, sbagliato


# ---------------------------------------------------------------------------
# C - STORICO INTERAZIONI: una sola fonte, due viste
# ---------------------------------------------------------------------------

def test_08_telefonata_incontro_nota_con_ora_e_autore_del_server(m):
    _incarico(m)
    prima = m["sql"]("SELECT NOW()")[0][0]
    voci = []
    for tipo, testo, extra in (("call", "Ho chiamato Mario: vuole aspettare lunedi'", {"contact_id": m["mario"]}),
                               ("meeting", "Incontro in agenzia: riduzione a 245.000", {"context": "mandate"}),
                               ("note", "  Documentazione completa  ", {})):
        r = _nota(m, m["casa"], interaction_type=tipo, note=testo, **extra)
        assert r.status_code == 201, r.text
        voci.append(r.json())
    dopo = m["sql"]("SELECT NOW()")[0][0]
    chiamata, incontro, nota = voci
    assert chiamata["type_label"] == "Telefonata" and chiamata["contact_name"] == "Mario Rossi"
    assert incontro["type_label"] == "Incontro" and incontro["context"] == "mandate"
    assert nota["type_label"] == "Nota" and nota["note"] == "Documentazione completa"
    assert nota["context"] == "property" and nota["contact_id"] is None
    for v in voci:
        assert v["author_name"].startswith("Giorgio") and v["created_by_user_id"] == m["giorgio"]
    righe = m["sql"]("SELECT occurred_at, agency_id, created_by_user_id FROM activities ORDER BY id")
    assert all(prima <= r[0] <= dopo and r[1] == m["a"] for r in righe)
    # la data/ora non si manda: il corpo la rifiuta (422), come agency_id e l'autore
    for campo, valore in (("occurred_at", "2020-01-01T10:00:00+01:00"), ("agency_id", m["b"]),
                          ("created_by_user_id", m["luca"])):
        r = _nota(m, m["casa"], **{campo: valore})
        assert r.status_code == 422, (campo, r.text)
    # dati non validi: 400 (service) / 422 (schema), nulla scritto
    assert _nota(m, m["casa"], interaction_type="status_change").status_code == 400
    assert _nota(m, m["casa"], note="   ").status_code == 400
    assert _nota(m, m["casa"], note="x" * 5001).status_code == 422
    assert _nota(m, m["casa"], context="acquisizione").status_code == 422
    assert _nota(m, m["vuota"], context="mandate").status_code == 400   # nessun incarico
    assert _conta(m, "activities") == 3


def test_09_stessa_riga_da_immobile_incarico_contatto_in_ordine(m):
    _incarico(m)
    r1 = _nota(m, m["casa"], interaction_type="note", note="dalla scheda Immobile").json()
    r2 = _nota(m, m["casa"], interaction_type="call", note="dalla scheda Incarico",
               context="mandate", contact_id=m["mario"]).json()
    m["sql"]("UPDATE activities SET occurred_at = occurred_at - interval '1 hour' WHERE id = %s",
             (r1["id"],))
    elenco = m["api"]("giorgio").get(f"/api/property/properties/{m['casa']}/interactions").json()
    assert [v["id"] for v in elenco["items"]] == [r2["id"], r1["id"]]     # piu' recente prima
    assert {t["value"] for t in elenco["types"]} == {"call", "meeting", "note", "email", "whatsapp"}
    # l'incarico mostra l'ultima interazione, la stessa riga
    inc = m["api"]("giorgio").get(f"/api/property/mandates/{m['casa']}").json()
    assert inc["last_interaction"]["type"] == "call"
    assert inc["last_interaction"]["author_name"].startswith("Giorgio")
    assert _elenco(m, sort="last_interaction")["items"][0]["last_interaction"]["type"] == "call"
    # e la vede il contatto nel registro Attivita' (stessa riga, nessuna copia)
    from core.repository import list_activities
    from operator_auth.context import OperatorContext
    ctx = OperatorContext(user_id=m["giorgio"], agency_id=m["a"], role="agency_owner",
                          is_platform_admin=False, session_id=None, auth_channel="operator_session")
    del_contatto = list_activities(ctx, 50, 0, m["mario"], None, None)
    assert [a["id"] for a in del_contatto] == [r2["id"]] and del_contatto[0]["property_id"] == m["casa"]
    assert _conta(m, "activities") == 2


def test_10_tenancy_e_referente(m):
    _incarico(m)
    # immobile di un'altra agenzia: 404 in lettura e scrittura, nulla scritto
    assert _nota(m, m["altra"]).status_code == 404
    assert m["api"]("giorgio").get(f"/api/property/properties/{m['altra']}/interactions").status_code == 404
    assert _nota(m, m["casa"], chi="estraneo").status_code == 404
    assert m["api"]("estraneo").get(f"/api/property/properties/{m['casa']}/interactions").status_code == 404
    # referente: deve essere collegato all'immobile; di un'altra agenzia lo e' mai
    assert _nota(m, m["casa"], contact_id=m["contatto_b"]).status_code == 400
    assert _nota(m, m["vuota"], contact_id=m["mario"]).status_code == 400
    assert _nota(m, m["casa"], contact_id=999999).status_code == 400
    assert _conta(m, "activities") == 0
    # l'agente scrive e legge sugli immobili dell'agenzia (scope di activities e immobili)
    r = _nota(m, m["casa"], chi="marta", interaction_type="note", note="vista dall'agente")
    assert r.status_code == 201, r.text
    assert r.json()["author_name"].startswith("Marta")
    assert len(m["api"]("luca").get(f"/api/property/properties/{m['casa']}/interactions").json()["items"]) == 1


def test_11_una_nota_non_e_un_evento_di_stato(m):
    acq = _incarico(m)
    eventi = _conta(m, "acquisition_events")
    storico = _conta(m, "property_status_history")
    agenda = _conta(m, "appointments")
    prima = ops3._riga(m, acq["id"])
    immobile = ops3._immobile(m)
    for tipo in ("call", "meeting", "note", "email", "whatsapp"):
        assert _nota(m, m["casa"], interaction_type=tipo, note=f"prova {tipo}").status_code == 201
    assert _conta(m, "acquisition_events") == eventi
    assert _conta(m, "property_status_history") == storico
    assert _conta(m, "appointments") == agenda            # un incontro non e' un appuntamento
    assert ops3._riga(m, acq["id"]) == prima and ops3._immobile(m) == immobile
    assert _conta(m, "activities") == 5                    # una riga per interazione, nessun duplicato


def test_12_referente_e_scope_contatti_dell_agente(m):
    """Il referente passa dalla stessa verifica di POST /api/core/activities
    (core._validate_references con lo scope): un agente nomina solo un
    contatto che vede. Nessun allargamento; owner/admin lo nominano sempre."""
    _incarico(m)
    r = _nota(m, m["casa"], chi="marta", contact_id=m["mario"])
    assert r.status_code == 404 and "Referente non disponibile" in r.text, r.text
    assert _conta(m, "activities") == 0
    m["sql"]("UPDATE contacts SET assigned_agent_id = %s WHERE id = %s", (m["marta"], m["mario"]))
    r = _nota(m, m["casa"], chi="marta", contact_id=m["mario"])
    assert r.status_code == 201, r.text
    assert _nota(m, m["casa"], chi="anna", contact_id=m["bruno"]).status_code == 201
    m["sql"]("UPDATE contacts SET assigned_agent_id = NULL WHERE id = %s", (m["mario"],))



# ---------------------------------------------------------------------------
# D - STORICO NON CANCELLABILE
# ---------------------------------------------------------------------------

def _api_core(m, chi="giorgio"):
    """Il router CORE vero, con lo stesso contesto del banco."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.router import router as core_router
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import require_operator

    ruoli = {"giorgio": (m["giorgio"], m["a"], "agency_owner"),
             "marta": (m["marta"], m["a"], "agent"),
             "estraneo": (m["estraneo"], m["b"], "agent")}
    user_id, agenzia, ruolo = ruoli[chi]
    app = FastAPI()
    app.include_router(core_router)
    app.dependency_overrides[require_operator] = lambda: OperatorContext(
        user_id=user_id, agency_id=agenzia, role=ruolo, is_platform_admin=False,
        session_id=None, auth_channel="operator_session")
    return TestClient(app)


def test_13_interazione_d_immobile_non_si_cancella_le_altre_si(m):
    import psycopg2
    sql = m["sql"]
    _incarico(m)
    nota = _nota(m, m["casa"], interaction_type="note", note="sbagliata").json()
    legacy = sql("INSERT INTO activities (contact_id, activity_type, description) "
                 "VALUES (%s, 'call', 'legacy') RETURNING id", (m["mario"],))[0][0]
    # API: 409 con il messaggio, la riga resta
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 0: un agente (marta) non
    # cancella attivita' - 403 prima di qualunque lettura della riga; per il
    # titolare lo storico d'immobile resta un 409 con lo stesso messaggio.
    r = _api_core(m, "giorgio").delete(f"/api/core/activities/{nota['id']}")
    assert r.status_code == 409, r.text
    assert "storico commerciale" in r.json()["detail"] and "nuova interazione" in r.json()["detail"]
    r = _api_core(m, "marta").delete(f"/api/core/activities/{nota['id']}")
    assert r.status_code == 403, r.text
    assert _conta(m, "activities", "id = %s", (nota["id"],)) == 1
    # un'altra agenzia: non si rivela la riga (D-6). DELETE-ARCH Fase 0: per un
    # agente - estraneo lo e' - il rifiuto e' 403 PRIMA di qualunque lettura, e
    # identico per un id inesistente: la riga non viene rivelata comunque.
    assert _api_core(m, "estraneo").delete(f"/api/core/activities/{nota['id']}").status_code == 403
    assert _api_core(m, "estraneo").delete("/api/core/activities/999999").status_code == 403
    assert _api_core(m).delete("/api/core/activities/999999").status_code == 404
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 0 (contratto REV 2, §15: owner e
    # admin cancellano "sui propri"): un'attivita' legacy senza autore
    # (`created_by_user_id` NULL) non e' di nessuno e risponde 403, con la riga
    # che resta; una senza immobile creata da chi cancella si cancella ancora.
    r = _api_core(m).delete(f"/api/core/activities/{legacy}")
    assert r.status_code == 403, r.text
    assert _conta(m, "activities", "id = %s", (legacy,)) == 1
    propria = sql("INSERT INTO activities (contact_id, activity_type, description, created_by_user_id) "
                  "VALUES (%s, 'call', 'propria', %s) RETURNING id", (m["mario"], m["giorgio"]))[0][0]
    assert _api_core(m).delete(f"/api/core/activities/{propria}").status_code == 204
    assert _conta(m, "activities", "id = %s", (propria,)) == 0
    # e la correzione e' una nuova interazione: lo storico ne conta due
    r = _nota(m, m["casa"], interaction_type="note", note="correzione: la nota precedente era errata")
    assert r.status_code == 201
    assert _conta(m, "activities", "property_id = %s", (m["casa"],)) == 2
    # DB: anche SQL diretto e' rifiutato, anche "cancella tutto"
    with pytest.raises(psycopg2.errors.CheckViolation, match="commercial history"):
        sql("DELETE FROM activities WHERE id = %s", (nota["id"],))
    m["conn"].rollback()
    with pytest.raises(psycopg2.errors.CheckViolation, match="commercial history"):
        sql("DELETE FROM activities")
    m["conn"].rollback()
    assert _conta(m, "activities", "property_id = %s", (m["casa"],)) == 2
    # nessun soft-delete: la riga non ha colonne nuove oltre a property_id
    colonne = {r[0] for r in sql("SELECT column_name FROM information_schema.columns "
                                 "WHERE table_name = 'activities'")}
    assert not {c for c in colonne if "deleted" in c or "archived" in c}


def test_14_le_sonde_p26_6_sul_server_vero(m):
    """Le stesse richieste di `certify_property_mandates`, contro il router
    VERO: 404 senza dati verso l'altra agenzia, referente altrui rifiutato
    COME REFERENTE (il controllo di tenant viene prima del contesto), e il
    corpo innocuo (`context: mandate` su un immobile non incarico) non scrive
    nemmeno quando il tenant e' lecito."""
    from property import interactions as inter
    sonda = {"interaction_type": "note", "note": "sonda P26-6", "context": "mandate"}
    _incarico(m)                                          # casa (agenzia A) e' un incarico
    api_b = m["api"]("estraneo")                           # agenzia B
    for metodo, url, corpo in (("GET", f"/api/property/mandates/{m['casa']}", None),
                               ("GET", f"/api/property/mandates/{m['altra']}", None),
                               ("GET", f"/api/property/properties/{m['casa']}/interactions", None),
                               ("POST", f"/api/property/properties/{m['casa']}/interactions", sonda)):
        r = api_b.request(metodo, url, json=corpo)
        assert r.status_code == 404, (url, r.text)
        assert set(r.json()) <= {"detail", "code"} and "Esclusiva" not in r.text, r.text
    elenco = api_b.get("/api/property/mandates", params={"limit": 200}).json()["items"]
    assert m["casa"] not in [v["property_id"] for v in elenco]
    # referente dell'altra agenzia sul proprio immobile (non incarico): rifiuto del REFERENTE
    r = m["api"]("giorgio").post(f"/api/property/properties/{m['vuota']}/interactions",
                                 json={**sonda, "contact_id": m["contatto_b"]})
    assert r.status_code == 400 and r.json()["detail"] == inter.REFERENTE_NON_COLLEGATO
    # tenant lecito, corpo della sonda: si ferma sul contesto, non scrive
    r = m["api"]("giorgio").post(f"/api/property/properties/{m['vuota']}/interactions", json=sonda)
    assert r.status_code == 400 and r.json()["detail"] == inter.SENZA_INCARICO
    # agency_id nel corpo: 422
    r = m["api"]("giorgio").post(f"/api/property/properties/{m['vuota']}/interactions",
                                 json={**sonda, "agency_id": m["b"]})
    assert r.status_code == 422
    assert _conta(m, "activities") == 0                    # nessuna scrittura, nemmeno parziale
