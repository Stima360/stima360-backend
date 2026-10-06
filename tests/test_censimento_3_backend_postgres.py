"""CENSIMENTO-1 Fase 3 - BACKEND su PostgreSQL VERO, schema COMPLETO, rotte VERE.

Schema ricostruito da zero nella fixture di questo modulo con il metodo
verificato in Fase 1/2 (legacy -> 001..025 -> 026/080/fixture/082/083 dal
runner vero). Il client HTTP monta i router veri (property, acquisitions) con
il contesto operatore sostituito per ruolo; `core.database.get_connection`
punta al database usa-e-getta. Nessun test dipende da un altro: ogni test
ripulisce cio' che crea tramite `mondo` (TRUNCATE mirati).

Cosa si prova (progetto CENSIMENTO-0 §0 p.5-6, §2, §4, §6, §7):
  * edifici: crea/legge/elenca/modifica; avvisi non bloccanti con conferma;
    idempotenza; propagazione dell'indirizzo SOLO alle unita' ereditate;
  * unita' census: nascono `census`/`draft` con codice e descrizione; indirizzo
    ereditato o proprio; duplicato catastale completo bloccato; simili
    avvisati; relazioni mancanti/altrui -> 404; campi protetti -> 422;
  * pertinenze: crea, collega esistente, scollega con storico `system`;
    profondita' 1 e cicli rifiutati; vendita autonoma azzera il collegamento;
  * accessori: Si'/No/Non lo so, Chiarisci -> compresa / separata (stessa
    transazione, rollback se la creazione fallisce);
  * guardie §7: PATCH active/incarico su census -> 409 CENSUS_LOCKED,
    acquisizione su census -> 409, readiness con «Immobile in censimento»;
  * presa in carico (con/senza pertinenze), undo-create nei due rami;
  * isolamento fra due agenzie su ogni rotta, anonimo -> 401 via sessione;
  * idempotenza e concorrenza (due richieste con la stessa chiave: una riga);
  * lo storico `crm` e le acquisizioni non cambiano.
Opt-in `P29_TEST_DSN`, SOLO database locale; DB `stima360_db_test`.
"""
from __future__ import annotations

import argparse
import threading
import uuid
from decimal import Decimal

import pytest

from tests.test_censimento_1_fullschema_postgres import (
    DSN, IMPRONTA_CERTIFICATA, MIGRAZIONI, NOME_DB, _cartella_fino_a, _dsn_locale, _dsn_per,
    _env_runner, _fixture, _impronte, _pre_baseline, _runner,
)

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare CENSIMENTO-1 Fase 3")

OPERATORE = "censimento.fase3"
ACCESSORI = ("property_accessories",)


@pytest.fixture(scope="module")
def completo():
    psycopg2 = pytest.importorskip("psycopg2")
    from psycopg2.extras import DictCursor
    _dsn_locale(DSN)
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (NOME_DB,))
        if cur.fetchone():
            servizio.close()
            pytest.skip(f"sul cluster locale esiste gia' un database {NOME_DB}: non lo tocco")
        cur.execute(f'CREATE DATABASE "{NOME_DB}"')
    dsn = _dsn_per(NOME_DB)
    conn = psycopg2.connect(dsn, cursor_factory=DictCursor)
    conn.autocommit = True
    mp = pytest.MonkeyPatch()
    try:
        import database as legacy
        originale = legacy.get_connection
        legacy.get_connection = lambda: psycopg2.connect(dsn)
        try:
            legacy.crea_tabella_stime()
            legacy.crea_tabella_stime_dettagliate()
            legacy.crea_tabella_zone_valori()
            legacy.migrazione_allinea_stime()
        finally:
            legacy.get_connection = originale
        with conn.cursor() as cur:
            for p in _pre_baseline():
                cur.execute(p.read_text(encoding="utf-8"))
        c = {"conn": conn, "dsn": dsn, "nome": NOME_DB, "psycopg2": psycopg2}
        runner = _runner()
        _env_runner(mp, dsn, NOME_DB)
        args = argparse.Namespace(**(runner.SHARED_DEFAULTS | {
            "operator": OPERATORE, "baseline_fingerprint": IMPRONTA_CERTIFICATA,
            "baseline_artifact": "reports/p26_baseline_TEST_20260905T170601Z.json"}))
        for massimo in (26, 80):
            mp.setattr(runner, "MIGRATIONS_DIR", _cartella_fino_a(massimo))
            assert runner.command_apply(args) == 0
        _fixture(c)
        mp.setattr(runner, "MIGRATIONS_DIR", _cartella_fino_a(82))
        assert runner.command_apply(args) == 0
        mp.setattr(runner, "MIGRATIONS_DIR", MIGRAZIONI)
        assert runner.command_apply(args) == 0
        from core import database as core_database
        mp.setattr(core_database, "get_connection", lambda: psycopg2.connect(dsn))
        c["impronte_storiche"] = _impronte(c)
        # operatori e membership: due agenzie (1 = Uno, 2 = Due della fixture)
        with conn.cursor() as cur:
            def operatore(email, nome, agenzia, ruolo):
                cur.execute("INSERT INTO operator_users (email, email_normalized, password_hash, first_name) "
                            "VALUES (%s, %s, 'pbkdf2_sha256$1$x$y', %s) RETURNING id", (email, email, nome))
                uid = cur.fetchone()[0]
                cur.execute("INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) "
                            "VALUES (%s, %s, %s, 'active')", (agenzia, uid, ruolo))
                return uid
            c["ids"] = {"owner_a": operatore("o.a@x.test", "Olga", 1, "agency_owner"),
                        "agent_a": operatore("a.a@x.test", "Anna", 1, "agent"),
                        "owner_b": operatore("o.b@x.test", "Bea", 2, "agency_owner")}
            cur.execute("INSERT INTO contacts (agency_id, display_name, first_name, last_name) VALUES (1, 'Mario Rossi', 'Mario', 'Rossi') RETURNING id")
            c["mario"] = cur.fetchone()[0]
        yield c
    finally:
        mp.undo()
        conn.close()
        with servizio.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (NOME_DB,))
            cur.execute(f'DROP DATABASE IF EXISTS "{NOME_DB}"')
        servizio.close()


def _q(c, sql, params=None):
    with c["conn"].cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture
def mondo(completo):
    """Client HTTP con i router veri e il contesto per ruolo; pulizia di
    tutto cio' che il censimento crea (gli storici della fixture restano)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from acquisitions.router import router as acquisizioni
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import legacy_basic_agency_context, require_operator
    from property.router import router as immobili

    ids = completo["ids"]
    ruoli = {"owner_a": (ids["owner_a"], 1, "agency_owner"), "agent_a": (ids["agent_a"], 1, "agent"),
             "owner_b": (ids["owner_b"], 2, "agency_owner")}
    stato = {"chi": "owner_a"}

    def contesto():
        uid, agenzia, ruolo = ruoli[stato["chi"]]
        return OperatorContext(user_id=uid, agency_id=agenzia, role=ruolo, is_platform_admin=False,
                               session_id=None, auth_channel="operator_session")

    app = FastAPI()
    app.include_router(immobili)
    app.include_router(acquisizioni)
    app.dependency_overrides[legacy_basic_agency_context] = contesto
    app.dependency_overrides[require_operator] = contesto
    client = TestClient(app)

    class _Come:
        """Il client PER UN RUOLO: ogni richiesta imposta il contesto al
        momento dell'invio, cosi' due client di ruoli diversi convivono."""
        def __init__(self, chi):
            self.chi = chi

        def __getattr__(self, metodo):
            def invia(*a, **k):
                stato["chi"] = self.chi
                return getattr(client, metodo)(*a, **k)
            return invia

    def api(chi="owner_a"):
        return _Come(chi)

    def pulisci():
        # lo storico d'immobile non si cancella (guardia CRM-OPS-4): qui si
        # ripulisce il database usa-e-getta sospendendo il trigger, come fanno
        # le suite di CRM-OPS-3/4
        _q(completo, "ALTER TABLE activities DISABLE TRIGGER trg_activities_property_history")
        _q(completo, "DELETE FROM activities WHERE property_id IS NOT NULL")
        _q(completo, "ALTER TABLE activities ENABLE TRIGGER trg_activities_property_history")
        # acquisizioni e appuntamenti nati dal controllo positivo di test_19
        for tabella, trigger in (("acquisition_events", "trg_acquisition_events_append_only"),
                                 ("acquisitions", "trg_acquisitions_refuse_delete"),
                                 ("appointment_events", "trg_appointment_events_append_only"),
                                 ("appointments", "trg_appointments_refuse_delete")):
            _q(completo, f"ALTER TABLE {tabella} DISABLE TRIGGER {trigger}")
        _q(completo, "ALTER TABLE properties DISABLE TRIGGER trg_properties_mandate_origin")
        _q(completo, "UPDATE properties SET acquisition_id = NULL WHERE id > 25")
        _q(completo, "ALTER TABLE properties ENABLE TRIGGER trg_properties_mandate_origin")
        _q(completo, "DELETE FROM acquisition_events WHERE acquisition_id IN (SELECT id FROM acquisitions WHERE property_id > 25)")
        _q(completo, "DELETE FROM acquisitions WHERE property_id > 25")
        _q(completo, "DELETE FROM appointment_events WHERE appointment_id IN (SELECT id FROM appointments WHERE property_id > 25)")
        _q(completo, "DELETE FROM appointments WHERE property_id > 25")
        for tabella, trigger in (("acquisition_events", "trg_acquisition_events_append_only"),
                                 ("acquisitions", "trg_acquisitions_refuse_delete"),
                                 ("appointment_events", "trg_appointment_events_append_only"),
                                 ("appointments", "trg_appointments_refuse_delete")):
            _q(completo, f"ALTER TABLE {tabella} ENABLE TRIGGER {trigger}")
        _q(completo, "DELETE FROM property_accessories")
        _q(completo, "DELETE FROM property_status_history WHERE property_id > 25")
        _q(completo, "DELETE FROM property_price_history WHERE property_id > 25")
        _q(completo, "UPDATE properties SET parent_property_id = NULL WHERE id > 25")
        _q(completo, "DELETE FROM properties WHERE id > 25")
        _q(completo, "DELETE FROM buildings")
        _q(completo, "UPDATE properties SET parent_property_id = NULL, building_id = NULL, cadastral_category = NULL, "
                     "cadastral_municipality_code = NULL, cadastral_section = NULL, cadastral_sheet = NULL, "
                     "cadastral_parcel = NULL, cadastral_subunit = NULL, staircase = NULL, internal_number = NULL "
                     "WHERE id <= 25")
    pulisci()
    yield {**completo, "api": api, "ctx": contesto, "ruoli": ruoli, "stato": stato}
    pulisci()


def _in_parallelo(lavori, timeout=60):
    """Esegue le funzioni in thread e restituisce i risultati NELL'ORDINE dei
    lavori. Ogni eccezione di un thread viene raccolta e fatta fallire
    esplicitamente; nessun thread puo' restare vivo alla fine."""
    esiti, errori = [None] * len(lavori), [None] * len(lavori)

    def corpo(i, fn):
        try:
            esiti[i] = fn()
        except BaseException as exc:  # noqa: BLE001 - raccolta, poi rilanciata nel test
            errori[i] = exc

    fili = [threading.Thread(target=corpo, args=(i, fn), daemon=True) for i, fn in enumerate(lavori)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=timeout)
    assert not [f for f in fili if f.is_alive()], "thread ancora attivi dopo il timeout"
    assert not [e for e in errori if e is not None], [repr(e) for e in errori if e is not None]
    assert len(esiti) == len(lavori)
    return esiti


def _in_attesa_di_lock(c, frammento, timeout=10):
    """Vero quando una sessione del database, la cui query corrente contiene
    `frammento`, e' ferma in attesa di un lock: la sincronizzazione esplicita
    delle prove di concorrenza (niente attese temporali arbitrarie)."""
    import time
    scadenza = time.monotonic() + timeout
    while time.monotonic() < scadenza:
        righe = _q(c, "SELECT 1 FROM pg_stat_activity WHERE datname = %s AND wait_event_type = 'Lock' "
                      "AND query LIKE %s", (c["nome"], frammento))
        if righe:
            return True
        time.sleep(0.02)
    return False


def _edificio(m, chi="owner_a", **kw):
    corpo = {"city": "Fermo", "address": "Via Roma", "civic_number": "10", "units_declared": 6,
             "units_declared_source": "survey", "name": "Palazzina Roma 10", **kw}
    r = m["api"](chi).post("/api/property/buildings", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()


def _unita(m, chi="owner_a", **kw):
    r = m["api"](chi).post("/api/property/census/units", json=kw)
    assert r.status_code == 201, r.text
    return r.json()


def _storico_crm(m):
    return _q(m, "SELECT id FROM properties WHERE id <= 25 AND record_kind = 'crm' AND commercial_status = 'draft' ORDER BY id")[0][0]


# ---------------------------------------------------------------------------
# 1. Edifici
# ---------------------------------------------------------------------------

def test_01_palazzina_nasce_si_legge_si_elenca(mondo):
    e = _edificio(mondo)
    assert e["replica"] is False and e["agency_id"] == 1 and e["census_status"] == "partial"
    assert e["counters"] == {"units_census": 0, "units_main": 0, "units_pertinenze": 0,
                             "units_address_inherited": 0, "units_address_custom": 0, "accessories_unknown": 0}
    letto = mondo["api"]().get(f"/api/property/buildings/{e['id']}")
    assert letto.status_code == 200 and letto.json()["units"] == [] and letto.json()["units_declared"] == 6
    elenco = mondo["api"]().get("/api/property/buildings?search=roma").json()["items"]
    assert [b["id"] for b in elenco] == [e["id"]] and elenco[0]["units_census"] == 0
    # l'altra agenzia non lo vede ne' in elenco ne' in dettaglio ne' in modifica
    assert mondo["api"]("owner_b").get("/api/property/buildings").json()["items"] == []
    assert mondo["api"]("owner_b").get(f"/api/property/buildings/{e['id']}").status_code == 404
    assert mondo["api"]("owner_b").patch(f"/api/property/buildings/{e['id']}", json={"name": "x"}).status_code == 404
    assert _q(mondo, "SELECT name FROM buildings WHERE id = %s", (e["id"],))[0][0] == "Palazzina Roma 10"


def test_02_campi_protetti_e_valori_invalidi(mondo):
    api = mondo["api"]()
    for corpo in ({"agency_id": 2, "city": "Fermo"}, {"client_request_fingerprint": "a" * 64},
                  {"building_type": "grattacielo"}, {"units_declared": -1}, {"census_status": "x"}):
        assert api.post("/api/property/buildings", json=corpo).status_code == 422, corpo
    # territorio e Belfiore giudicati dal service (400), non dal database
    r = api.post("/api/property/buildings", json={"region": "Abruzzo", "city": "Giulianova"})
    assert r.status_code == 400 and "Comune" in r.json()["detail"]
    r = api.post("/api/property/buildings", json={"cadastral_municipality_code": "12AB"})
    assert r.status_code == 400 and r.json()["code"] == "VALIDATION_ERROR"
    assert _q(mondo, "SELECT count(*) FROM buildings")[0][0] == 0


def test_03_edifici_simili_avviso_superabile_mai_blocco(mondo):
    e = _edificio(mondo)
    r = mondo["api"]().post("/api/property/buildings", json={"city": "Fermo", "address": "via roma ", "civic_number": "10"})
    assert r.status_code == 409 and r.json()["code"] == "SIMILAR_FOUND"
    assert [s["id"] for s in r.json()["similar"]] == [e["id"]]
    assert _q(mondo, "SELECT count(*) FROM buildings")[0][0] == 1
    r = mondo["api"]().post("/api/property/buildings", json={"city": "Fermo", "address": "via roma ", "civic_number": "10",
                                                             "confirm_similar": True})
    assert r.status_code == 201 and r.json()["similar"][0]["id"] == e["id"]
    # stessa chiave catastale: avviso anche senza indirizzo
    _edificio(mondo, name="Catasto", cadastral_municipality_code="d542", cadastral_sheet="012", cadastral_parcel="0345",
              address="Via Altra")
    r = mondo["api"]().post("/api/property/buildings", json={"cadastral_municipality_code": "D542", "cadastral_sheet": "12",
                                                             "cadastral_parcel": "345"})
    assert r.status_code == 409 and r.json()["similar"][0]["name"] == "Catasto"


def test_04_idempotenza_edificio(mondo):
    chiave = str(uuid.uuid4())
    primo = _edificio(mondo, client_request_id=chiave)
    secondo = _edificio(mondo, client_request_id=chiave)
    assert secondo["id"] == primo["id"] and secondo["replica"] is True
    assert _q(mondo, "SELECT count(*) FROM buildings")[0][0] == 1
    r = mondo["api"]().post("/api/property/buildings", json={"city": "Fermo", "name": "Altra", "client_request_id": chiave})
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    # la stessa chiave in un'altra agenzia e' un'altra richiesta
    assert _edificio(mondo, "owner_b", client_request_id=chiave)["id"] != primo["id"]


def test_05_indirizzo_edificio_si_propaga_solo_alle_unita_ereditate(mondo):
    e = _edificio(mondo, region="Marche", province="FM", city="Fermo", microzone="Lido di Fermo")
    ereditata = _unita(mondo, building_id=e["id"], floor="1", internal_number="1")
    propria = _unita(mondo, building_id=e["id"], floor="1", internal_number="2", address="Via Laterale", civic_number="3",
                     city="Fermo", region="Marche", province="FM")
    assert ereditata["address_inherited"] is True and ereditata["address"] == "Via Roma" and ereditata["city"] == "Fermo"
    assert ereditata["title"] == "Appartamento · Fermo (Lido di Fermo) · Via Roma 10"
    assert propria["address_inherited"] is False and propria["address"] == "Via Laterale"
    r = mondo["api"]().patch(f"/api/property/buildings/{e['id']}", json={"address": "Via Roma Nuova", "civic_number": "12"})
    assert r.status_code == 200 and r.json()["propagated_units"] == 1 and r.json()["custom_units"] == 1
    assert r.json()["counters"]["units_address_inherited"] == 1
    righe = dict(_q(mondo, "SELECT id, address || ' ' || civic_number FROM properties WHERE building_id = %s", (e["id"],)))
    assert righe[ereditata["id"]] == "Via Roma Nuova 12" and righe[propria["id"]] == "Via Laterale 3"
    assert _q(mondo, "SELECT title FROM properties WHERE id = %s", (ereditata["id"],))[0][0] == \
        "Appartamento · Fermo (Lido di Fermo) · Via Roma Nuova 12"
    # una modifica senza indirizzo non tocca le unita'
    r = mondo["api"]().patch(f"/api/property/buildings/{e['id']}", json={"units_declared": 8})
    assert r.json()["propagated_units"] == 0 and r.json()["units_declared"] == 8
    # «Ingresso diverso?» dalla scheda: un campo di indirizzo nel PATCH rende l'unita' personalizzata
    r = mondo["api"]().patch(f"/api/property/properties/{ereditata['id']}", json={"civic_number": "12/A"})
    assert r.status_code == 200 and r.json()["address_inherited"] is False
    r = mondo["api"]().patch(f"/api/property/buildings/{e['id']}", json={"civic_number": "14"})
    assert r.json()["propagated_units"] == 0 and r.json()["custom_units"] == 2


# ---------------------------------------------------------------------------
# 2. Unita' di censimento
# ---------------------------------------------------------------------------

def test_06_unita_in_palazzina_nasce_census_draft_con_codice(mondo):
    e = _edificio(mondo)
    u = _unita(mondo, building_id=e["id"], property_type="commercial", floor="T", cadastral_category="c/1", surface_sqm="45.5")
    riga = _q(mondo, "SELECT record_kind, commercial_status, code, title, cadastral_category, building_id, agency_id, "
                     "address_inherited FROM properties WHERE id = %s", (u["id"],))[0]
    assert list(riga) == ["census", "draft", f"IMM-{u['id']}", "Locale commerciale · Fermo · Via Roma 10", "C/1", e["id"], 1, True]
    assert u["replica"] is False and u["code"] == f"IMM-{u['id']}"
    assert _q(mondo, "SELECT count(*) FROM property_status_history WHERE property_id = %s", (u["id"],))[0][0] == 1
    dettaglio = mondo["api"]().get(f"/api/property/buildings/{e['id']}").json()
    assert dettaglio["counters"]["units_census"] == 1 and dettaglio["units"][0]["id"] == u["id"]
    # la 360 esistente la legge; l'altra agenzia no
    assert mondo["api"]().get(f"/api/property/properties/{u['id']}").status_code == 200
    assert mondo["api"]("owner_b").get(f"/api/property/properties/{u['id']}").status_code == 404
    assert mondo["api"]("owner_b").get(f"/api/property/properties/{u['id']}/census").status_code == 404


def test_07_unita_relazioni_mancanti_altrui_e_campi_protetti(mondo):
    api = mondo["api"]()
    e_b = _edificio(mondo, "owner_b")
    assert api.post("/api/property/census/units", json={"building_id": 999999}).status_code == 404
    assert api.post("/api/property/census/units", json={"building_id": e_b["id"]}).status_code == 404
    assert api.post("/api/property/census/units", json={"parent_property_id": 999999}).status_code == 404
    # SENTINELLA AGGIORNATA DA CREAZIONE-GUIDATA-1: `record_kind` 'crm' e' ora
    # ammesso (unita' commerciale in palazzina, decisione della fase C); restano
    # rifiutati un tipo inesistente e l'assegnazione su una scheda di censimento.
    for corpo in ({"record_kind": "listing"}, {"assigned_agent_id": 1}, {"agency_id": 1}, {"address_inherited": True}, {"commercial_status": "active"},
                  {"client_request_fingerprint": "a" * 64}, {"property_type": "cantina"}, {"surface_sqm": -1}):
        assert api.post("/api/property/census/units", json=corpo).status_code == 422, corpo
    r = api.post("/api/property/census/units", json={"cadastral_category": "A/99"})
    assert r.status_code == 400 and "catalogo" in r.json()["detail"]
    r = api.post("/api/property/census/units", json={"whole_building": True})
    assert r.status_code == 400
    assert _q(mondo, "SELECT count(*) FROM properties WHERE id > 25")[0][0] == 0


def test_08_duplicato_catastale_completo_blocca_simili_avvisano(mondo):
    e = _edificio(mondo)
    catasto = dict(cadastral_municipality_code="d542", cadastral_section="", cadastral_sheet="12", cadastral_parcel="345",
                   cadastral_subunit="4")
    u = _unita(mondo, building_id=e["id"], floor="2", internal_number="2", **catasto)
    r = mondo["api"]().post("/api/property/census/units", json={**catasto, "cadastral_sheet": "0012", "floor": "3"})
    assert r.status_code == 409 and r.json()["code"] == "CADASTRAL_DUPLICATE" and r.json()["existing"]["code"] == u["code"]
    assert "confirm" not in r.json() and _q(mondo, "SELECT count(*) FROM properties WHERE id > 25")[0][0] == 1
    # con conferma NON si supera: e' un blocco
    r = mondo["api"]().post("/api/property/census/units", json={**catasto, "confirm_similar": True})
    assert r.status_code == 409 and r.json()["code"] == "CADASTRAL_DUPLICATE"
    # stessa posizione nella palazzina: avviso, poi conferma
    r = mondo["api"]().post("/api/property/census/units", json={"building_id": e["id"], "floor": "2", "internal_number": "2"})
    assert r.status_code == 409 and r.json()["code"] == "SIMILAR_FOUND" and r.json()["similar"][0]["reason"] == "position"
    r = mondo["api"]().post("/api/property/census/units", json={"building_id": e["id"], "floor": "2", "internal_number": "2",
                                                                "confirm_similar": True})
    assert r.status_code == 201
    # sezione NON conosciuta: coincidenza di Belfiore+foglio+particella+sub = avviso, non blocco
    r = mondo["api"]().post("/api/property/census/units", json={**catasto, "cadastral_section": None})
    assert r.status_code == 409 and r.json()["similar"][0]["reason"] == "cadastral_section_unknown"
    r = mondo["api"]().post("/api/property/census/units", json={**catasto, "cadastral_section": None, "confirm_similar": True})
    assert r.status_code == 201
    # l'altra agenzia puo' avere la stessa identita' (isolamento)
    assert _unita(mondo, "owner_b", **catasto)["agency_id"] == 2


def test_09_idempotenza_unita_anche_sotto_concorrenza(mondo):
    e = _edificio(mondo)
    chiave = str(uuid.uuid4())
    corpo = {"building_id": e["id"], "floor": "1", "client_request_id": chiave}
    primo = _unita(mondo, **corpo)
    assert _unita(mondo, **corpo)["id"] == primo["id"]
    r = mondo["api"]().post("/api/property/census/units", json={**corpo, "floor": "2"})
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    # concorrenza: N richieste con una chiave nuova -> una riga, tutte 201 con lo stesso id
    chiave2 = str(uuid.uuid4())
    barriera = threading.Barrier(4)

    def invia():
        barriera.wait(timeout=10)
        r = mondo["api"]().post("/api/property/census/units", json={"building_id": e["id"], "floor": "5",
                                                                    "client_request_id": chiave2})
        return r.status_code, r.json().get("id")

    esiti = _in_parallelo([invia] * 4)
    assert len(esiti) == 4 and all(s == 201 for s, _ in esiti) and len({i for _, i in esiti}) == 1
    assert sum(1 for _ in esiti) == 4
    assert _q(mondo, "SELECT count(*) FROM properties WHERE client_request_id = %s", (chiave2,))[0][0] == 1


def test_10_patch_di_un_unita_census_campi_consentiti_e_guardia(mondo):
    u = _unita(mondo, floor="1")
    api = mondo["api"]()
    r = api.patch(f"/api/property/properties/{u['id']}", json={"surface_sqm": "70", "cadastral_category": " a/3 ",
                                                              "cadastral_sheet": "0012", "staircase": "b"})
    assert r.status_code == 200
    assert _q(mondo, "SELECT cadastral_category, cadastral_sheet, staircase FROM properties WHERE id = %s", (u["id"],))[0][:3] == \
        ["A/3", "12", "b"]      # scala: testo libero, la 083 normalizza solo il catasto
    r = api.patch(f"/api/property/properties/{u['id']}", json={"cadastral_category": "Z/1"})
    assert r.status_code == 400
    # §7: lo stato commerciale e l'incarico sono chiusi finche' e' census
    for corpo in ({"commercial_status": "active"}, {"commercial_status": "evaluation"}, {"mandate_type": "esclusiva"}):
        r = api.patch(f"/api/property/properties/{u['id']}", json=corpo)
        assert r.status_code == 409 and "Prendi in carico" in r.json()["detail"], corpo
        assert r.json()["code"] == "CENSUS_LOCKED", corpo            # Fase 4: il residuo chiuso, sulla rotta reale
    assert api.patch(f"/api/property/properties/{u['id']}", json={"record_kind": "crm"}).status_code == 422
    assert _q(mondo, "SELECT commercial_status, record_kind FROM properties WHERE id = %s", (u["id"],))[0][:2] == ["draft", "census"]
    # archiviare resta possibile (draft -> archived)
    assert api.delete(f"/api/property/properties/{u['id']}").status_code == 200
    assert _q(mondo, "SELECT commercial_status FROM properties WHERE id = %s", (u["id"],))[0][0] == "archived"


def test_11_il_trigger_resta_la_garanzia_dietro_il_service(mondo):
    """Un UPDATE SQL combinato (fuori dall'API) e' rifiutato dalla 083 e
    l'errore, se arriva dal repository, e' tradotto in 409 leggibile."""
    u = _unita(mondo)
    from core.exceptions import ConflictError
    from property import repository, service
    with pytest.raises(ConflictError):
        repository.update_property(mondo["ctx"](), u["id"], {"record_kind": "crm", "commercial_status": "active"})
    assert _q(mondo, "SELECT record_kind, commercial_status FROM properties WHERE id = %s", (u["id"],))[0][:2] == ["census", "draft"]


# ---------------------------------------------------------------------------
# 3. Pertinenze
# ---------------------------------------------------------------------------

def test_12_pertinenza_creata_collegata_e_scollegata_con_storico(mondo):
    e = _edificio(mondo)
    principale = _unita(mondo, building_id=e["id"], floor="2", internal_number="2")
    garage = _unita(mondo, building_id=e["id"], parent_property_id=principale["id"], property_type="garage", floor="-1",
                    cadastral_category="C/6")
    assert garage["parent_property_id"] == principale["id"] and garage["record_kind"] == "census"
    c = mondo["api"]().get(f"/api/property/properties/{principale['id']}/census").json()
    assert [p["id"] for p in c["pertinenze"]] == [garage["id"]] and c["building"]["id"] == e["id"]
    assert mondo["api"]().get(f"/api/property/buildings/{e['id']}").json()["counters"] == \
        {"units_census": 2, "units_main": 1, "units_pertinenze": 1, "units_address_inherited": 2,
         "units_address_custom": 0, "accessories_unknown": 0}
    # profondita' 1: una pertinenza non ha pertinenze; e nessun ciclo
    r = mondo["api"]().post("/api/property/census/units", json={"parent_property_id": garage["id"]})
    assert r.status_code == 400 and r.json()["code"] == "LINK_INVALID"
    r = mondo["api"]().post(f"/api/property/properties/{garage['id']}/pertinenze/link", json={"pertinenza_id": principale["id"]})
    assert r.status_code == 400 and r.json()["code"] == "LINK_INVALID"
    # scollegamento: relazione azzerata, storico su entrambe
    r = mondo["api"]().post(f"/api/property/properties/{principale['id']}/pertinenze/{garage['id']}/unlink")
    assert r.status_code == 200 and r.json()["pertinenza"]["parent_property_id"] is None
    storico = _q(mondo, "SELECT property_id, activity_type, description FROM activities WHERE property_id IN (%s, %s) ORDER BY id",
                 (principale["id"], garage["id"]))
    assert len(storico) == 2 and {s[1] for s in storico} == {"system"}
    assert any(f"Scollegata da {principale['code']}" in s[2] for s in storico)
    # «Collega esistente»: la ricollega; un secondo collegamento identico non duplica nulla
    r = mondo["api"]().post(f"/api/property/properties/{principale['id']}/pertinenze/link", json={"pertinenza_id": garage["id"]})
    assert r.status_code == 200 and r.json()["linked"] is True
    r = mondo["api"]().post(f"/api/property/properties/{principale['id']}/pertinenze/link", json={"pertinenza_id": garage["id"]})
    assert r.status_code == 200 and r.json()["linked"] is False
    altra = _unita(mondo, floor="3")
    r = mondo["api"]().post(f"/api/property/properties/{altra['id']}/pertinenze/link", json={"pertinenza_id": garage["id"]})
    assert r.status_code == 409 and r.json()["code"] == "ALREADY_LINKED"


def test_13_pertinenza_di_altra_agenzia_e_mai_collegabile(mondo):
    mia = _unita(mondo)
    sua = _unita(mondo, "owner_b")
    r = mondo["api"]().post(f"/api/property/properties/{mia['id']}/pertinenze/link", json={"pertinenza_id": sua["id"]})
    assert r.status_code == 404
    r = mondo["api"]("owner_b").post(f"/api/property/properties/{sua['id']}/pertinenze/link", json={"pertinenza_id": mia["id"]})
    assert r.status_code == 404
    r = mondo["api"]("owner_b").post("/api/property/census/units", json={"parent_property_id": mia["id"]})
    assert r.status_code == 404
    assert _q(mondo, "SELECT count(*) FROM properties WHERE parent_property_id IS NOT NULL")[0][0] == 0
    assert mondo["api"]("owner_b").post(f"/api/property/properties/{mia['id']}/pertinenze/{sua['id']}/unlink").status_code == 404


def test_14_vendita_autonoma_della_pertinenza_azzera_il_collegamento(mondo):
    principale = _unita(mondo)
    garage = _unita(mondo, parent_property_id=principale["id"], property_type="garage")
    api = mondo["api"]()
    assert api.post(f"/api/property/properties/{garage['id']}/take-in-charge", json={"include_pertinenze": False}).status_code == 200
    r = api.patch(f"/api/property/properties/{garage['id']}", json={"commercial_status": "sold"})
    assert r.status_code == 200 and r.json()["parent_property_id"] is None
    assert _q(mondo, "SELECT parent_property_id FROM properties WHERE id = %s", (garage["id"],))[0][0] is None
    testi = [r[0] for r in _q(mondo, "SELECT description FROM activities WHERE property_id IN (%s,%s) AND activity_type = 'system' ORDER BY id",
                              (principale["id"], garage["id"]))]
    assert any("venduta autonomamente" in t for t in testi)
    # archiviazione di una pertinenza ancora collegata: idem
    cantina = _unita(mondo, parent_property_id=principale["id"], property_type="storage")
    assert api.delete(f"/api/property/properties/{cantina['id']}").status_code == 200
    assert _q(mondo, "SELECT parent_property_id FROM properties WHERE id = %s", (cantina["id"],))[0][0] is None
    # archiviare la PRINCIPALE non tocca le pertinenze (il collegamento resta)
    box = _unita(mondo, parent_property_id=principale["id"], property_type="garage")
    assert api.delete(f"/api/property/properties/{principale['id']}").status_code == 200
    assert _q(mondo, "SELECT parent_property_id FROM properties WHERE id = %s", (box["id"],))[0][0] == principale["id"]


# ---------------------------------------------------------------------------
# 4. Accessori
# ---------------------------------------------------------------------------

def test_15_accessori_si_no_non_lo_so_e_chiarisci_compresa(mondo):
    e = _edificio(mondo)
    u = _unita(mondo, building_id=e["id"], floor="1")
    api = mondo["api"]()
    r = api.post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "cantina", "cadastral_status": "unknown", "surface_sqm": "6"})
    assert r.status_code == 201 and r.json()["cadastral_status"] == "unknown"
    acc = r.json()
    assert api.post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "garage"}).status_code == 422
    assert api.post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "box", "cadastral_status": "separate"}).status_code == 422
    c = api.get(f"/api/property/properties/{u['id']}/census").json()
    assert c["accessories_unknown"] == 1 and len(c["accessories"]) == 1
    assert api.get(f"/api/property/buildings/{e['id']}").json()["counters"]["accessories_unknown"] == 1
    assert api.get(f"/api/property/buildings/{e['id']}").json()["counters"]["units_census"] == 1   # mai un'unita' inventata
    # modifica e isolamento
    assert api.patch(f"/api/property/properties/{u['id']}/accessories/{acc['id']}", json={"notes": "sotto le scale"}).status_code == 200
    assert mondo["api"]("owner_b").patch(f"/api/property/properties/{u['id']}/accessories/{acc['id']}", json={"notes": "x"}).status_code == 404
    assert mondo["api"]("owner_b").delete(f"/api/property/properties/{u['id']}/accessories/{acc['id']}").status_code == 404
    assert mondo["api"]("owner_b").post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "box"}).status_code == 404
    # «E' compresa»
    r = api.post(f"/api/property/properties/{u['id']}/accessories/{acc['id']}/resolve", json={"outcome": "included"})
    assert r.status_code == 200 and r.json()["accessory"]["cadastral_status"] == "included" and r.json()["pertinenza"] is None
    assert api.get(f"/api/property/buildings/{e['id']}").json()["counters"]["accessories_unknown"] == 0
    # idempotenza dell'accessorio
    chiave = str(uuid.uuid4())
    a1 = api.post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "box", "client_request_id": chiave}).json()
    a2 = api.post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "box", "client_request_id": chiave}).json()
    assert a1["id"] == a2["id"] and a2["replica"] is True
    assert api.post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "giardino", "client_request_id": chiave}).status_code == 409
    assert api.delete(f"/api/property/properties/{u['id']}/accessories/{a1['id']}").status_code == 204
    assert _q(mondo, "SELECT count(*) FROM property_accessories WHERE property_id = %s", (u["id"],))[0][0] == 1


def test_16_chiarisci_e_separata_crea_la_pertinenza_e_toglie_l_accessorio_in_una_transazione(mondo):
    e = _edificio(mondo)
    u = _unita(mondo, building_id=e["id"], floor="1", internal_number="1")
    api = mondo["api"]()
    acc = api.post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "cantina", "cadastral_status": "unknown",
                                                                             "surface_sqm": "6", "notes": "cantina a nord"}).json()
    r = api.post(f"/api/property/properties/{u['id']}/accessories/{acc['id']}/resolve",
                 json={"outcome": "separate", "cadastral_category": "C/2", "cadastral_municipality_code": "D542",
                       "cadastral_section": "", "cadastral_sheet": "12", "cadastral_parcel": "345", "cadastral_subunit": "11"})
    assert r.status_code == 200, r.text
    p = r.json()["pertinenza"]
    assert r.json()["accessory"] is None and p["parent_property_id"] == u["id"] and p["property_type"] == "storage"
    assert p["building_id"] == e["id"] and p["cadastral_category"] == "C/2" and p["cadastral_subunit"] == "11"
    assert Decimal(str(p["surface_sqm"])) == Decimal("6") and p["internal_notes"] == "cantina a nord" and p["record_kind"] == "census"
    assert _q(mondo, "SELECT count(*) FROM property_accessories WHERE property_id = %s", (u["id"],))[0][0] == 0
    assert api.get(f"/api/property/buildings/{e['id']}").json()["counters"] == \
        {"units_census": 2, "units_main": 1, "units_pertinenze": 1, "units_address_inherited": 2,
         "units_address_custom": 0, "accessories_unknown": 0}
    # ROLLBACK: la creazione fallisce (subalterno gia' censito) -> l'accessorio resta com'era
    acc2 = api.post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "box", "cadastral_status": "unknown"}).json()
    r = api.post(f"/api/property/properties/{u['id']}/accessories/{acc2['id']}/resolve",
                 json={"outcome": "separate", "cadastral_municipality_code": "D542", "cadastral_section": "",
                       "cadastral_sheet": "12", "cadastral_parcel": "345", "cadastral_subunit": "11"})
    assert r.status_code == 409 and r.json()["code"] == "CADASTRAL_DUPLICATE"
    assert _q(mondo, "SELECT cadastral_status FROM property_accessories WHERE id = %s", (acc2["id"],))[0][0] == "unknown"
    assert _q(mondo, "SELECT count(*) FROM properties WHERE parent_property_id = %s", (u["id"],))[0][0] == 1
    # «E' separata» collegando un immobile gia' censito: note accodate, mq conservati se la scheda li ha
    esistente = _unita(mondo, property_type="garage", surface_sqm="15", internal_notes="box")
    r = api.post(f"/api/property/properties/{u['id']}/accessories/{acc2['id']}/resolve",
                 json={"outcome": "separate", "existing_property_id": esistente["id"]})
    assert r.status_code == 200 and r.json()["pertinenza"]["id"] == esistente["id"]
    riga = _q(mondo, "SELECT parent_property_id, surface_sqm, internal_notes FROM properties WHERE id = %s", (esistente["id"],))[0]
    assert riga[0] == u["id"] and Decimal(riga[1]) == Decimal("15") and riga[2] == "box"
    assert _q(mondo, "SELECT count(*) FROM property_accessories WHERE id = %s", (acc2["id"],))[0][0] == 0
    # con un immobile di un'altra agenzia: 404 e niente cambia
    acc3 = api.post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "posto_auto", "cadastral_status": "unknown"}).json()
    sua = _unita(mondo, "owner_b")
    r = api.post(f"/api/property/properties/{u['id']}/accessories/{acc3['id']}/resolve",
                 json={"outcome": "separate", "existing_property_id": sua["id"]})
    assert r.status_code == 404
    assert _q(mondo, "SELECT count(*) FROM property_accessories WHERE id = %s", (acc3["id"],))[0][0] == 1


# ---------------------------------------------------------------------------
# 5. Presa in carico, undo, guardie commerciali, readiness, storico
# ---------------------------------------------------------------------------

def test_17_presa_in_carico_con_e_senza_pertinenze(mondo):
    principale = _unita(mondo)
    g1 = _unita(mondo, parent_property_id=principale["id"], property_type="garage")
    g2 = _unita(mondo, parent_property_id=principale["id"], property_type="storage")
    api = mondo["api"]()
    assert mondo["api"]("owner_b").post(f"/api/property/properties/{principale['id']}/take-in-charge", json={}).status_code == 404
    r = api.post(f"/api/property/properties/{principale['id']}/take-in-charge", json={"include_pertinenze": True})
    assert r.status_code == 200 and r.json()["record_kind"] == "crm" and sorted(r.json()["pertinenze_taken"]) == sorted([g1["id"], g2["id"]])
    assert r.json()["code"] == principale["code"] and r.json()["commercial_status"] == "draft"
    assert {x[0] for x in _q(mondo, "SELECT record_kind FROM properties WHERE id IN (%s,%s,%s)", (principale["id"], g1["id"], g2["id"]))} == {"crm"}
    # ora i flussi commerciali esistenti valgono
    assert api.patch(f"/api/property/properties/{principale['id']}", json={"commercial_status": "active"}).status_code == 200
    r = api.post(f"/api/property/properties/{principale['id']}/take-in-charge", json={})
    assert r.status_code == 409 and r.json()["code"] == "NOT_CENSUS"
    # senza pertinenze: restano census, collegate
    p2 = _unita(mondo)
    g3 = _unita(mondo, parent_property_id=p2["id"], property_type="garage")
    r = api.post(f"/api/property/properties/{p2['id']}/take-in-charge", json={"include_pertinenze": False})
    assert r.json()["pertinenze_taken"] == []
    assert _q(mondo, "SELECT record_kind, parent_property_id FROM properties WHERE id = %s", (g3["id"],))[0][:2] == ["census", p2["id"]]


def test_18_undo_create_solo_se_intatta(mondo):
    api = mondo["api"]()
    u = _unita(mondo, floor="4")
    assert mondo["api"]("owner_b").post(f"/api/property/properties/{u['id']}/undo-create").status_code == 404
    r = api.post(f"/api/property/properties/{u['id']}/undo-create")
    assert r.status_code == 200 and r.json()["commercial_status"] == "archived" and r.json()["archived_at"]
    assert api.post(f"/api/property/properties/{u['id']}/undo-create").status_code == 404   # archiviata: non piu' nello scope attivo
    # modificata -> non annullabile
    u2 = _unita(mondo)
    api.patch(f"/api/property/properties/{u2['id']}", json={"surface_sqm": "50"})
    r = api.post(f"/api/property/properties/{u2['id']}/undo-create")
    assert r.status_code == 409 and r.json()["code"] == "UNDO_NOT_POSSIBLE"
    # con un collegamento (accessorio, pertinenza, contatto) -> non annullabile
    u3 = _unita(mondo)
    api.post(f"/api/property/properties/{u3['id']}/accessories", json={"kind": "box"})
    r = api.post(f"/api/property/properties/{u3['id']}/undo-create")
    assert r.status_code == 409 and r.json()["linked"] == ["property_accessories"]
    u4 = _unita(mondo)
    api.post(f"/api/property/properties/{u4['id']}/contacts", json={"contact_id": mondo["mario"], "role": "owner"})
    assert api.post(f"/api/property/properties/{u4['id']}/undo-create").json()["linked"] == ["property_contacts"]
    # presa in carico -> non annullabile
    u5 = _unita(mondo)
    api.post(f"/api/property/properties/{u5['id']}/take-in-charge", json={})
    assert api.post(f"/api/property/properties/{u5['id']}/undo-create").status_code == 409


def test_19_acquisizione_su_census_rifiutata_409(mondo):
    u = _unita(mondo)
    api = mondo["api"]()
    api.post(f"/api/property/properties/{u['id']}/contacts", json={"contact_id": mondo["mario"], "role": "owner"})
    corpo = {"property_id": u["id"], "owner_contact_id": mondo["mario"],
             "appointment": {"start_at": "2027-03-01T10:00:00+01:00", "assigned_user_id": mondo["ids"]["owner_a"],
                             "client_request_id": str(uuid.uuid4())}}
    r = api.post("/api/acquisitions", json=corpo)
    assert r.status_code == 409 and r.json()["code"] == "PROPERTY_IN_CENSUS", r.text
    assert _q(mondo, "SELECT count(*) FROM acquisitions WHERE property_id = %s", (u["id"],))[0][0] == 0
    assert _q(mondo, "SELECT count(*) FROM appointments WHERE property_id = %s", (u["id"],))[0][0] == 0
    # presa in carico -> l'acquisizione nasce (flussi esistenti invariati)
    api.post(f"/api/property/properties/{u['id']}/take-in-charge", json={})
    r = api.post("/api/acquisitions", json=corpo)
    assert r.status_code == 201, r.text


def test_20_readiness_e_storico_crm_invariati(mondo):
    from match.readiness import property_readiness
    u = _unita(mondo)
    riga = _q(mondo, "SELECT row_to_json(p) FROM properties p WHERE id = %s", (u["id"],))[0][0]
    pronto = property_readiness(riga)
    assert pronto["eligible"] is False and "Immobile in censimento" in pronto["eligibility_reasons"]
    storico = _q(mondo, "SELECT row_to_json(p) FROM properties p WHERE id = %s", (_storico_crm(mondo),))[0][0]
    assert "Immobile in censimento" not in property_readiness(storico)["eligibility_reasons"]
    # le 25 righe storiche hanno le stesse impronte di prima di ogni test di questo modulo
    assert _impronte(mondo) | {} and all(_impronte(mondo)[k] == v for k, v in mondo["impronte_storiche"].items())


def test_21_agente_e_titolare_stesse_rotte_anonimo_escluso(mondo):
    """Lo scope del censimento e' quello degli immobili (l'agenzia intera per
    ogni ruolo). Senza sessione le rotte rispondono 401 come tutte le altre."""
    e = _edificio(mondo, "agent_a")
    u = _unita(mondo, "agent_a", building_id=e["id"], floor="1")
    assert mondo["api"]("owner_a").get(f"/api/property/buildings/{e['id']}").json()["units"][0]["id"] == u["id"]
    assert mondo["api"]("agent_a").post(f"/api/property/properties/{u['id']}/take-in-charge", json={}).status_code == 200
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from property.router import router as immobili
    nudo = TestClient(FastAPI(), raise_server_exceptions=False)
    nudo.app.include_router(immobili)
    for metodo, percorso in (("get", "/api/property/buildings"), ("post", "/api/property/buildings"),
                             ("post", "/api/property/census/units"), ("get", f"/api/property/properties/{u['id']}/census"),
                             ("post", f"/api/property/properties/{u['id']}/take-in-charge"),
                             ("post", f"/api/property/properties/{u['id']}/undo-create")):
        risposta = nudo.get(percorso) if metodo == "get" else nudo.post(percorso, json={})
        assert risposta.status_code == 401, percorso


def test_22_collegamenti_concorrenti_speculari_passano_tutti(mondo):
    """Un collegamento per transazione (083, §0-bis REV 3): due giri API
    speculari non si bloccano a vicenda e tutti i collegamenti passano. Il
    retry sul solo 40P01 e' provato a parte, senza database
    (tests/test_censimento_3_backend.py)."""
    p1, p2 = _unita(mondo, floor="1"), _unita(mondo, floor="2")
    figli = [_unita(mondo, property_type="garage") for _ in range(4)]
    barriera = threading.Barrier(2)

    def giro(coppie):
        def corpo():
            barriera.wait(timeout=10)
            return [mondo["api"]().post(f"/api/property/properties/{g}/pertinenze/link", json={"pertinenza_id": f}).status_code
                    for g, f in coppie]
        return corpo

    esiti = _in_parallelo([giro([(p1["id"], figli[0]["id"]), (p2["id"], figli[1]["id"])]),
                           giro([(p2["id"], figli[2]["id"]), (p1["id"], figli[3]["id"])])])
    assert esiti == [[200, 200], [200, 200]]
    assert _q(mondo, "SELECT count(*) FROM properties WHERE parent_property_id IN (%s,%s)", (p1["id"], p2["id"]))[0][0] == 4


# ---------------------------------------------------------------------------
# 6. Revisione: idempotenza di «Chiarisci», null nei PATCH, indirizzo ereditato
#    sotto concorrenza
# ---------------------------------------------------------------------------

def test_23_chiarisci_separata_con_creazione_e_idempotente_sulla_chiave(mondo):
    api = mondo["api"]()
    u = _unita(mondo, floor="1")
    acc = api.post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "cantina", "cadastral_status": "unknown",
                                                                             "surface_sqm": "6"}).json()
    chiave = str(uuid.uuid4())
    corpo = {"outcome": "separate", "client_request_id": chiave, "cadastral_category": "C/2"}
    r1 = api.post(f"/api/property/properties/{u['id']}/accessories/{acc['id']}/resolve", json=corpo)
    r2 = api.post(f"/api/property/properties/{u['id']}/accessories/{acc['id']}/resolve", json=corpo)
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    assert r1.json()["replica"] is False and r2.json()["replica"] is True
    assert r2.json()["pertinenza"]["id"] == r1.json()["pertinenza"]["id"] and r2.json()["accessory"] is None
    # nessun duplicato: una pertinenza, un solo collegamento, lo storico di una sola conversione
    assert _q(mondo, "SELECT count(*) FROM properties WHERE parent_property_id = %s", (u["id"],))[0][0] == 1
    assert _q(mondo, "SELECT count(*) FROM properties WHERE client_request_id = %s", (chiave,))[0][0] == 1
    assert _q(mondo, "SELECT count(*) FROM activities WHERE property_id = %s AND activity_type = 'system'", (u["id"],))[0][0] == 1
    assert _q(mondo, "SELECT count(*) FROM property_accessories WHERE property_id = %s", (u["id"],))[0][0] == 0
    # stessa chiave, payload diverso -> 409, niente scritto
    r = api.post(f"/api/property/properties/{u['id']}/accessories/{acc['id']}/resolve",
                 json={**corpo, "cadastral_category": "C/6"})
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    # stessa chiave su un ALTRO immobile della stessa agenzia -> 409 (l'impronta porta l'immobile)
    altra = _unita(mondo, floor="2")
    acc2 = api.post(f"/api/property/properties/{altra['id']}/accessories", json={"kind": "box", "cadastral_status": "unknown"}).json()
    r = api.post(f"/api/property/properties/{altra['id']}/accessories/{acc2['id']}/resolve", json=corpo)
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert _q(mondo, "SELECT count(*) FROM property_accessories WHERE id = %s", (acc2["id"],))[0][0] == 1
    # isolamento: l'altra agenzia, con la stessa chiave, non ottiene la replica (404 sull'unita')
    assert mondo["api"]("owner_b").post(f"/api/property/properties/{u['id']}/accessories/{acc['id']}/resolve", json=corpo).status_code == 404
    # la replica si verifica DOPO l'autorizzazione: l'unita' di un'altra agenzia resta 404 anche con la chiave giusta
    assert _q(mondo, "SELECT count(*) FROM properties WHERE client_request_id = %s", (chiave,))[0][0] == 1


def test_24_chiarisci_separata_concorrente_stessa_chiave_una_sola_pertinenza(mondo):
    api = mondo["api"]()
    u = _unita(mondo, floor="3")
    acc = api.post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "cantina", "cadastral_status": "unknown"}).json()
    chiave = str(uuid.uuid4())
    barriera = threading.Barrier(3)

    def invia():
        barriera.wait(timeout=10)
        r = mondo["api"]().post(f"/api/property/properties/{u['id']}/accessories/{acc['id']}/resolve",
                                json={"outcome": "separate", "client_request_id": chiave})
        return r.status_code, (r.json().get("pertinenza") or {}).get("id")

    esiti = _in_parallelo([invia] * 3)
    assert len(esiti) == 3 and all(s == 200 for s, _ in esiti) and len({i for _, i in esiti}) == 1, esiti
    assert _q(mondo, "SELECT count(*) FROM properties WHERE parent_property_id = %s", (u["id"],))[0][0] == 1
    assert _q(mondo, "SELECT count(*) FROM property_accessories WHERE id = %s", (acc["id"],))[0][0] == 0
    assert _q(mondo, "SELECT count(*) FROM activities WHERE property_id = %s AND activity_type = 'system'", (u["id"],))[0][0] == 1


def test_25_chiarisci_con_esistente_e_compresa_idempotenti_per_stato_senza_chiave(mondo):
    """Con `existing_property_id` e con `included` nessuna riga nuova
    conserverebbe la chiave: la chiave e' rifiutata (422). La ripetizione
    del collegamento e' riconosciuta dalla PROVA della conversione nello
    storico (test_29 per i casi negativi); `included` ripetuto riscrive lo
    stesso stato."""
    api = mondo["api"]()
    u = _unita(mondo, floor="1")
    box = _unita(mondo, property_type="garage", surface_sqm="15", internal_notes="chiavi dal custode")
    acc = api.post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "box", "cadastral_status": "unknown",
                                                                             "notes": "nel cortile"}).json()
    corpo = {"outcome": "separate", "existing_property_id": box["id"]}
    r = api.post(f"/api/property/properties/{u['id']}/accessories/{acc['id']}/resolve", json={**corpo, "client_request_id": str(uuid.uuid4())})
    assert r.status_code == 422
    r1 = api.post(f"/api/property/properties/{u['id']}/accessories/{acc['id']}/resolve", json=corpo)
    assert r1.status_code == 200 and r1.json()["replica"] is False, r1.text
    stato = _q(mondo, "SELECT parent_property_id, internal_notes, surface_sqm, updated_at FROM properties WHERE id = %s", (box["id"],))[0]
    assert stato[0] == u["id"] and stato[1] == "chiavi dal custode\nnel cortile" and stato[2] == Decimal("15")
    storico = _q(mondo, "SELECT id, metadata FROM activities WHERE property_id IN (%s, %s) AND activity_type = 'system' ORDER BY id", (u["id"], box["id"]))
    assert len(storico) == 2 and {m["operation"] for _, m in storico} == {"accessory_resolve_link"}
    assert {m["accessory_id"] for _, m in storico} == {acc["id"]}
    # la ripetizione REALE: 200, replica, e ne' le note ne' lo storico si duplicano
    r2 = api.post(f"/api/property/properties/{u['id']}/accessories/{acc['id']}/resolve", json=corpo)
    assert r2.status_code == 200 and r2.json()["replica"] is True and r2.json()["pertinenza"]["id"] == box["id"], r2.text
    assert r2.json()["accessory"] is None and r2.json()["pertinenza"]["internal_notes"] == "chiavi dal custode\nnel cortile"
    assert _q(mondo, "SELECT parent_property_id, internal_notes, surface_sqm, updated_at FROM properties WHERE id = %s", (box["id"],))[0] == stato
    assert _q(mondo, "SELECT id, metadata FROM activities WHERE property_id IN (%s, %s) AND activity_type = 'system' ORDER BY id", (u["id"], box["id"])) == storico
    # un ALTRO immobile con l'accessorio ormai sparito: 404 (nessuno stato lo rende "gia' fatto")
    terzo = _unita(mondo, property_type="garage")
    r = api.post(f"/api/property/properties/{u['id']}/accessories/{acc['id']}/resolve", json={"outcome": "separate", "existing_property_id": terzo["id"]})
    assert r.status_code == 404 and _q(mondo, "SELECT parent_property_id FROM properties WHERE id = %s", (terzo["id"],))[0][0] is None
    # «E' compresa» ripetuto: stesso stato, replica per stato
    acc2 = api.post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "cantina", "cadastral_status": "unknown"}).json()
    assert api.post(f"/api/property/properties/{u['id']}/accessories/{acc2['id']}/resolve", json={"outcome": "included", "client_request_id": str(uuid.uuid4())}).status_code == 422
    r1 = api.post(f"/api/property/properties/{u['id']}/accessories/{acc2['id']}/resolve", json={"outcome": "included"})
    r2 = api.post(f"/api/property/properties/{u['id']}/accessories/{acc2['id']}/resolve", json={"outcome": "included"})
    assert r1.json()["replica"] is False and r2.json()["replica"] is True and r2.json()["accessory"]["cadastral_status"] == "included"
    assert _q(mondo, "SELECT count(*) FROM property_accessories WHERE property_id = %s", (u["id"],))[0][0] == 1


def test_26_patch_con_null_su_colonne_not_null_rifiutato_senza_modifiche_parziali(mondo):
    api = mondo["api"]()
    e = _edificio(mondo, name="Prima")
    u = _unita(mondo, building_id=e["id"])
    acc = api.post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "box", "notes": "n1"}).json()
    for corpo in ({"building_type": None, "name": "Dopo"}, {"census_status": None, "name": "Dopo"}, {"metadata": None, "name": "Dopo"}):
        r = api.patch(f"/api/property/buildings/{e['id']}", json=corpo)
        assert r.status_code == 422, corpo
    assert _q(mondo, "SELECT name, building_type, census_status FROM buildings WHERE id = %s", (e["id"],))[0][:3] == ["Prima", "condominio", "partial"]
    r = api.patch(f"/api/property/properties/{u['id']}/accessories/{acc['id']}", json={"kind": None, "notes": "n2"})
    assert r.status_code == 422
    assert _q(mondo, "SELECT kind, notes FROM property_accessories WHERE id = %s", (acc["id"],))[0][:2] == ["box", "n1"]
    # omesso = invariato; null ammesso = azzera; valore = scrive
    r = api.patch(f"/api/property/buildings/{e['id']}", json={"name": None, "units_declared": None})
    assert r.status_code == 200 and r.json()["name"] is None and r.json()["units_declared"] is None and r.json()["building_type"] == "condominio"
    r = api.patch(f"/api/property/buildings/{e['id']}", json={"building_type": "villa"})
    assert r.status_code == 200 and r.json()["building_type"] == "villa" and r.json()["name"] is None
    r = api.patch(f"/api/property/properties/{u['id']}/accessories/{acc['id']}", json={"notes": None, "surface_sqm": None})
    assert r.status_code == 200 and r.json()["notes"] is None and r.json()["kind"] == "box"
    r = api.patch(f"/api/property/properties/{u['id']}/accessories/{acc['id']}", json={})
    assert r.status_code == 200 and r.json()["kind"] == "box"


def test_27_creazione_unita_e_patch_edificio_concorrenti_mai_un_indirizzo_ereditato_obsoleto(mondo, monkeypatch):
    """Coreografia deterministica (eventi + attesa del lock sul catalogo):
    1. la creazione legge l'edificio (FOR SHARE) e si ferma;
    2. la PATCH dell'edificio parte e resta BLOCCATA sul lock;
    3. la creazione riprende e termina;
    4. la PATCH prosegue e propaga anche alla riga nuova.
    Senza il FOR SHARE la PATCH terminava al passo 2 e la riga nuova nasceva
    ereditata con l'indirizzo vecchio (riprodotto prima della correzione)."""
    from property import census
    e = _edificio(mondo, name="Coreo")
    letto, prosegui = threading.Event(), threading.Event()
    originale = census._prepara_unita

    def lento(cur, agency_id, data):
        originale(cur, agency_id, data)
        letto.set()
        assert prosegui.wait(timeout=30)
    monkeypatch.setattr(census, "_prepara_unita", lento)

    def crea():
        r = mondo["api"]().post("/api/property/census/units", json={"building_id": e["id"], "floor": "1"})
        return r.status_code, r.json()

    def patch():
        assert letto.wait(timeout=30)
        return mondo["api"]().patch(f"/api/property/buildings/{e['id']}", json={"address": "Via Nuova", "civic_number": "1"}).json()

    def arbitro():
        assert letto.wait(timeout=30)
        # la PATCH deve essere FERMA sul lock dell'edificio mentre la creazione e' sospesa
        assert _in_attesa_di_lock(mondo, "%FROM buildings WHERE id%FOR UPDATE%"), "la PATCH non si e' fermata sul lock dell'edificio"
        prosegui.set()
        return True

    creato, patchato, _ = _in_parallelo([crea, patch, arbitro])
    assert creato[0] == 201 and patchato["propagated_units"] == 1
    riga = _q(mondo, "SELECT address, civic_number, address_inherited, title FROM properties WHERE id = %s", (creato[1]["id"],))[0]
    assert list(riga)[:3] == ["Via Nuova", "1", True] and riga[3].endswith("Via Nuova 1")


def test_28_patch_edificio_e_personalizzazione_dell_unita_concorrenti(mondo, monkeypatch):
    """La PATCH dell'edificio blocca le unita' ereditate FOR UPDATE; la
    personalizzazione («Ingresso diverso?») aspetta e poi vince: alla fine
    l'unita' e' personalizzata con il SUO indirizzo, mai con quello
    dell'edificio."""
    from property import census
    e = _edificio(mondo, name="Coreo2")
    u = _unita(mondo, building_id=e["id"], floor="1")
    bloccate, prosegui = threading.Event(), threading.Event()
    originale = census._unita_ereditate

    def lento(cur, building_id):
        esito = originale(cur, building_id)
        bloccate.set()
        assert prosegui.wait(timeout=30)
        return esito
    monkeypatch.setattr(census, "_unita_ereditate", lento)

    def patch_edificio():
        return mondo["api"]().patch(f"/api/property/buildings/{e['id']}", json={"address": "Via Edificio"}).json()

    def personalizza():
        assert bloccate.wait(timeout=30)
        return mondo["api"]().patch(f"/api/property/properties/{u['id']}", json={"address": "Via Mia", "civic_number": "7"}).json()

    def arbitro():
        assert bloccate.wait(timeout=30)
        assert _in_attesa_di_lock(mondo, "%FROM properties WHERE id=%FOR UPDATE%"), "la personalizzazione non si e' fermata sul lock della riga"
        prosegui.set()
        return True

    edificio, unita, _ = _in_parallelo([patch_edificio, personalizza, arbitro])
    assert edificio["propagated_units"] == 1 and unita["address_inherited"] is False
    assert _q(mondo, "SELECT address, civic_number, address_inherited FROM properties WHERE id = %s", (u["id"],))[0][:3] == ["Via Mia", "7", False]
    assert mondo["api"]().get(f"/api/property/buildings/{e['id']}").json()["counters"]["units_address_custom"] == 1
    # ordine inverso: personalizzazione prima, poi la PATCH non la tocca (predicato rivalutato)
    u2 = _unita(mondo, building_id=e["id"], floor="2")
    monkeypatch.setattr(census, "_unita_ereditate", originale)
    assert mondo["api"]().patch(f"/api/property/properties/{u2['id']}", json={"civic_number": "9"}).json()["address_inherited"] is False
    r = mondo["api"]().patch(f"/api/property/buildings/{e['id']}", json={"address": "Via Edificio 2"}).json()
    assert r["propagated_units"] == 0 and r["custom_units"] == 2


def test_29_la_replica_del_collegamento_esiste_solo_con_la_prova_della_conversione(mondo):
    """`existing_property_id`: `parent_property_id` da solo NON e' una prova.
    Una pertinenza collegata normalmente + un `accessory_id` inventato era un
    falso successo (200 replica); deve essere 404. La prova e' lo storico
    scritto nella stessa transazione della conversione (agenzia, unita',
    accessorio, immobile collegato, operazione)."""
    api = mondo["api"]()
    chiama = lambda unita, accessorio, esistente, chi="owner_a": mondo["api"](chi).post(  # noqa: E731
        f"/api/property/properties/{unita}/accessories/{accessorio}/resolve",
        json={"outcome": "separate", "existing_property_id": esistente})
    genitore = lambda pid: _q(mondo, "SELECT parent_property_id FROM properties WHERE id = %s", (pid,))[0][0]  # noqa: E731
    storico = lambda *pid: _q(mondo, "SELECT count(*) FROM activities WHERE property_id = ANY(%s) AND activity_type = 'system'", (list(pid),))[0][0]  # noqa: E731

    # 1. pertinenza collegata NORMALMENTE, poi "chiarisci" con un accessorio inventato -> 404, niente scritto
    u = _unita(mondo, floor="1")
    p = _unita(mondo, property_type="storage")
    assert api.post(f"/api/property/properties/{u['id']}/pertinenze/link", json={"pertinenza_id": p["id"]}).status_code == 200
    prima = storico(u["id"], p["id"])
    r = chiama(u["id"], 999999, p["id"])
    assert r.status_code == 404, r.text
    assert genitore(p["id"]) == u["id"] and storico(u["id"], p["id"]) == prima

    # 2. accessorio ELIMINATO senza conversione (pertinenza collegata o no) -> 404
    acc = api.post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "box", "cadastral_status": "unknown"}).json()
    assert api.delete(f"/api/property/properties/{u['id']}/accessories/{acc['id']}").status_code == 204
    assert chiama(u["id"], acc["id"], p["id"]).status_code == 404
    sciolta = _unita(mondo, property_type="garage")
    assert chiama(u["id"], acc["id"], sciolta["id"]).status_code == 404 and genitore(sciolta["id"]) is None
    assert storico(u["id"], p["id"], sciolta["id"]) == prima

    # 3. una conversione REALE (u2 + acc2 -> p2): la ripetizione e' replica
    u2 = _unita(mondo, floor="2")
    p2 = _unita(mondo, property_type="garage")
    acc2 = api.post(f"/api/property/properties/{u2['id']}/accessories", json={"kind": "box", "cadastral_status": "unknown"}).json()
    assert chiama(u2["id"], acc2["id"], p2["id"]).json()["replica"] is False
    r = chiama(u2["id"], acc2["id"], p2["id"])
    assert r.status_code == 200 and r.json()["replica"] is True and storico(u2["id"], p2["id"]) == 2
    # ...ma l'accessory_id di QUELLA conversione non vale per un altro immobile collegato normalmente alla stessa unita'
    p3 = _unita(mondo, property_type="storage")
    assert api.post(f"/api/property/properties/{u2['id']}/pertinenze/link", json={"pertinenza_id": p3["id"]}).status_code == 200
    assert chiama(u2["id"], acc2["id"], p3["id"]).status_code == 404
    # ...ne' per la pertinenza di un'ALTRA unita' (u + p, collegate normalmente): la prova e' per unita'
    assert chiama(u["id"], acc2["id"], p["id"]).status_code == 404
    # ...ne' vale, sull'unita' giusta, l'accessory_id di una conversione per CREAZIONE (operazione diversa)
    acc3 = api.post(f"/api/property/properties/{u2['id']}/accessories", json={"kind": "cantina", "cadastral_status": "unknown"}).json()
    creata = api.post(f"/api/property/properties/{u2['id']}/accessories/{acc3['id']}/resolve", json={"outcome": "separate"}).json()["pertinenza"]
    assert genitore(creata["id"]) == u2["id"] and chiama(u2["id"], acc3["id"], creata["id"]).status_code == 404
    assert chiama(u2["id"], acc3["id"], p2["id"]).status_code == 404
    assert storico(u["id"], p["id"], u2["id"], p2["id"], p3["id"], creata["id"]) == prima + 2 + 2 + 1

    # 4. isolamento tra agenzie: la conversione di B non e' una prova per A, e B non vede le unita' di A
    uB = _unita(mondo, chi="owner_b", floor="1")
    pB = _unita(mondo, chi="owner_b", property_type="garage")
    accB = api.post(f"/api/property/properties/{u2['id']}/accessories", json={"kind": "box", "cadastral_status": "unknown"}).json()
    accB2 = mondo["api"]("owner_b").post(f"/api/property/properties/{uB['id']}/accessories", json={"kind": "box", "cadastral_status": "unknown"}).json()
    assert chiama(uB["id"], accB2["id"], pB["id"], chi="owner_b").json()["replica"] is False
    assert chiama(uB["id"], accB2["id"], pB["id"], chi="owner_b").json()["replica"] is True
    assert chiama(u2["id"], accB2["id"], p2["id"]).status_code == 404            # accessorio di B su una conversione di A
    assert chiama(uB["id"], accB2["id"], pB["id"]).status_code == 404            # A non vede l'unita' di B
    assert chiama(u2["id"], acc2["id"], p2["id"], chi="owner_b").status_code == 404   # B non vede la replica di A
    assert api.delete(f"/api/property/properties/{u2['id']}/accessories/{accB['id']}").status_code == 204
    assert genitore(pB["id"]) == uB["id"] and genitore(p2["id"]) == u2["id"]

    # 5. pertinenza SCOLLEGATA dopo la conversione: il retry non la ricollega (409, niente scritto)
    assert api.post(f"/api/property/properties/{u2['id']}/pertinenze/{p2['id']}/unlink").status_code == 200
    assert genitore(p2["id"]) is None
    prima = storico(u2["id"], p2["id"])
    r = chiama(u2["id"], acc2["id"], p2["id"])
    assert r.status_code == 409 and r.json()["code"] == "ALREADY_RESOLVED", r.text
    assert genitore(p2["id"]) is None and storico(u2["id"], p2["id"]) == prima
    # anche se nel frattempo p2 viene collegata a un'altra unita': nessun furto, nessuna replica
    u4 = _unita(mondo, floor="4")
    assert api.post(f"/api/property/properties/{u4['id']}/pertinenze/link", json={"pertinenza_id": p2["id"]}).status_code == 200
    r = chiama(u2["id"], acc2["id"], p2["id"])
    assert r.status_code == 409 and r.json()["code"] == "ALREADY_RESOLVED" and genitore(p2["id"]) == u4["id"]
