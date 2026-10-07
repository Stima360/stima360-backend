"""CESTINO-EDIFICI-1 (FASE G) - Cestino e Ripristino degli EDIFICI, su PostgreSQL VERO.

Schema completo dal runner (migration 091 compresa), rotte VERE del router
Immobili (edifici, unita' di censimento, Cestino Immobili), fixture del
censimento (CENSIMENTO-1 Fase 3). Nessuna cancellazione fisica, nessuna
cascata: ogni prova controlla che le unita' restino dove sono.

  01  edificio vuoto: controllo, spostamento (motivo + nota, registro),
      fuori da lista Edifici e candidati della creazione guidata, scheda in
      sola lettura, modifica e unita' nuove rifiutate (servizio e database),
      ripristino sullo stesso id con i dati intatti;
  02  blocchi: unita' attiva, archiviata, pertinenza da collegare, immobile
      «intero stabile», unita' nel Cestino Immobili - ciascuna da sola
      blocca, con il collegamento; nulla si scollega; la guardia nel database
      ferma anche una UPDATE diretta; il Cestino dell'«intero stabile» non
      tocca l'edificio;
  03  permessi e agenzie: altra agenzia 404 e Cestino vuoto; un agent sposta
      un edificio vuoto, ripristina solo i suoi, vede solo i suoi;
  04  creazione guidata: un retry con la chiave di un edificio nel Cestino e'
      un 409 leggibile, mai un doppione;
  05  ripristino con possibili doppioni: segnalati, nulla unito o modificato;
      un edificio nel Cestino non e' una «palazzina simile»;
  06  concorrenza: due spostamenti -> uno; un'unita' nasce mentre lo
      spostamento aspetta (lo blocca); lo spostamento e' in corso mentre
      nasce un'unita' (rifiutata) - mai entrambi;
  99  codice su un database SENZA la 091: 503 leggibile, lista e scheda come
      prima; la down si ferma con edifici nel Cestino o eventi; up di nuovo.
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401
    DSN, _in_attesa_di_lock, _in_parallelo, _q, _unita, completo, mondo,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

MIGRAZIONI = Path(__file__).resolve().parents[1] / "migrations"
SU = MIGRAZIONI / "091_cestino_edifici_1_building_trash.sql"
GIU = MIGRAZIONI / "091_cestino_edifici_1_building_trash_down.sql"


def _edificio(m, chi="owner_a", **kw):
    corpo = {"city": "Fermo", "address": f"Via Cestino {uuid.uuid4().hex[:6]}", "civic_number": "1",
             "units_declared": 4, "units_declared_source": "survey", "name": "Palazzina di prova", **kw}
    r = m["api"](chi).post("/api/property/buildings", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()


def _check(m, bid, chi="owner_a"):
    return m["api"](chi).get(f"/api/property/buildings/{bid}/deletion-check")


def _sposta(m, bid, chi="owner_a", reason="created_by_mistake", note=None):
    corpo = {"reason_code": reason, **({"note": note} if note is not None else {})}
    return m["api"](chi).post(f"/api/property/buildings/{bid}/trash", json=corpo)


def _ripristina(m, bid, chi="owner_a"):
    return m["api"](chi).post(f"/api/property/buildings/{bid}/restore")


def _cestino(m, chi="owner_a"):
    r = m["api"](chi).get("/api/property/trash/buildings", params={"limit": 200})
    assert r.status_code == 200, r.text
    return {x["id"]: x for x in r.json()["items"]}


def _lista(m, chi="owner_a", **params):
    r = m["api"](chi).get("/api/property/buildings", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return {x["id"] for x in r.json()["items"]}


def _errore(m, sql, params=None):
    psycopg2 = m["psycopg2"]
    try:
        _q(m, sql, params)
    except psycopg2.Error as exc:
        with m["conn"].cursor() as cur:
            cur.execute("ROLLBACK")
        return str(exc).splitlines()[0]
    return None


def _impronta(m, bid):
    return _q(m, "SELECT md5((to_jsonb(b) - 'updated_at' - 'deleted_at' - 'deleted_by_user_id' - 'deleted_reason')::text) "
                 "FROM buildings b WHERE id = %s", (bid,))[0][0]


def _codice(r, atteso):
    assert r.status_code == atteso, r.text
    return r.json()


# ---------------------------------------------------------------------------

def test_01_edificio_vuoto_sposta_legge_ripristina(mondo):
    m = mondo
    api = m["api"]()
    e = _edificio(m, city="Grottammare", notes="Rilievo marzo", cadastral_municipality_code="E207",
                  cadastral_sheet="12", cadastral_parcel="345")
    prima = _impronta(m, e["id"])
    assert e["id"] in _lista(m) and e["id"] in _lista(m, city="Grottammare", sort="address")
    r = _check(m, e["id"])
    assert r.status_code == 200 and r.json() == {"can_trash": True, "blockers": []}
    assert _codice(_sposta(m, e["id"], reason="boh"), 400)["code"] == "INVALID_TRASH_REASON"
    assert _sposta(m, e["id"], note="x" * 501).status_code == 422
    riga = _codice(_sposta(m, e["id"], reason="duplicate", note="  Doppione della palazzina 12  "), 200)
    assert riga["id"] == e["id"] and riga["deleted_reason"] == "duplicate"
    ev = _q(m, "SELECT action, reason_code, note, actor_user_id FROM record_lifecycle_events "
               "WHERE entity_type = 'building' AND entity_id = %s ORDER BY id", (e["id"],))
    assert ev == [["trash", "duplicate", "Doppione della palazzina 12", m["ids"]["owner_a"]]]
    # fuori dalla lista Edifici e dai candidati della creazione guidata (stessa rotta)
    assert e["id"] not in _lista(m) and e["id"] not in _lista(m, city="Grottammare", sort="address")
    assert e["id"] not in _lista(m, search="Grottammare")
    # scheda in sola lettura, con chi/quando/perche'
    scheda = _codice(api.get(f"/api/property/buildings/{e['id']}"), 200)
    assert scheda["trash"]["deleted_reason"] == "duplicate" and scheda["trash"]["deleted_by_name"] == "Olga"
    assert scheda["trash"]["deleted_note"] == "Doppione della palazzina 12" and scheda["trash"]["can_restore"] is True
    voce = _cestino(m)[e["id"]]
    assert (voce["city"], voce["deleted_by_name"], voce["deleted_note"]) == ("Grottammare", "Olga",
                                                                             "Doppione della palazzina 12")
    # modifica e unita' nuove: 409 BUILDING_IN_TRASH (servizio); nel database la guardia
    assert _codice(api.patch(f"/api/property/buildings/{e['id']}", json={"name": "x"}), 409)["code"] == "BUILDING_IN_TRASH"
    assert _codice(api.post("/api/property/census/units", json={"building_id": e["id"], "floor": "1"}),
                   409)["code"] == "BUILDING_IN_TRASH"
    assert "BUILDING_IN_TRASH" in _errore(m, "INSERT INTO properties (title, agency_id, building_id) VALUES ('x', 1, %s)",
                                          (e["id"],))
    assert "BUILDING_IN_TRASH" in _errore(m, "UPDATE properties SET building_id = %s WHERE id = 20", (e["id"],))
    assert "BUILDING_IN_TRASH" in _errore(m, "UPDATE buildings SET notes = 'x' WHERE id = %s", (e["id"],))
    assert _codice(_sposta(m, e["id"]), 409)["code"] == "ALREADY_DELETED"
    assert [b["code"] for b in _check(m, e["id"]).json()["blockers"]] == ["ALREADY_DELETED"]
    # ripristino: stesso id, dati intatti, di nuovo in lista
    r = _codice(_ripristina(m, e["id"]), 200)
    assert r["id"] == e["id"] and r["deleted_at"] is None and r["possible_duplicates"] == []
    assert _impronta(m, e["id"]) == prima
    assert e["id"] in _lista(m) and e["id"] not in _cestino(m)
    assert [x[0] for x in _q(m, "SELECT action FROM record_lifecycle_events WHERE entity_type = 'building' "
                               "AND entity_id = %s ORDER BY id", (e["id"],))] == ["trash", "restore"]
    assert _codice(_ripristina(m, e["id"]), 409)["code"] == "NOT_DELETED"
    # dopo il ripristino si usa come prima
    assert _unita(m, building_id=e["id"], floor="1", internal_number="1")["building_id"] == e["id"]


def test_02_unita_collegate_bloccano_di_ogni_tipo(mondo):
    m = mondo
    api = m["api"]()

    def blocco(bid):
        r = _check(m, bid)
        assert r.status_code == 200 and r.json()["can_trash"] is False, r.text
        [b] = r.json()["blockers"]
        assert b["code"] == "BUILDING_HAS_UNITS" and b["link"]["href"] == f"#/edifici/{bid}"
        assert _codice(_sposta(m, bid), 409)["code"] == "TRASH_BLOCKED"
        assert _q(m, "SELECT deleted_at FROM buildings WHERE id = %s", (bid,))[0][0] is None
        return b

    # unita' attiva
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="2", internal_number="3")
    b = blocco(e["id"])
    assert b["counts"] == {"active": 1, "archived": 0, "trash": 0, "total": 1}
    assert b["items"][0]["href"] == f"#/immobili/{u['id']}" and "piano 2" in b["items"][0]["label"]
    # pertinenza da collegare (sola)
    e2 = _edificio(m)
    posto = _unita(m, building_id=e2["id"], is_pertinenza=True, pertinenza_kind="posto_auto", property_type="garage")
    b = blocco(e2["id"])
    assert "pertinenza" in b["items"][0]["label"] and b["items"][0]["id"] == posto["id"]
    # immobile «intero stabile» (resta un immobile: blocca l'edificio)
    e3 = _edificio(m)
    intero = _unita(m, building_id=e3["id"], property_type="building", whole_building=True)
    b = blocco(e3["id"])
    assert "intero stabile" in b["items"][0]["label"]
    # il Cestino Immobili dell'«intero stabile» non tocca l'edificio, che resta bloccato
    assert _codice(api.post(f"/api/property/properties/{intero['id']}/trash",
                            json={"reason_code": "test_record"}), 200)
    assert _q(m, "SELECT deleted_at FROM buildings WHERE id = %s", (e3["id"],))[0][0] is None
    b = blocco(e3["id"])
    assert b["counts"]["trash"] == 1 and b["items"][0]["href"] == "#/cestino"
    assert "nel Cestino Immobili" in b["label"]
    # unita' archiviata (una riga storica dell'agenzia collegata all'edificio)
    e4 = _edificio(m)
    _q(m, "UPDATE properties SET building_id = %s WHERE id = 12", (e4["id"],))
    b = blocco(e4["id"])
    assert b["counts"]["archived"] == 1 and "archiviata" in b["items"][0]["label"]
    # nulla si scollega o si sposta
    assert _q(m, "SELECT building_id FROM properties WHERE id IN (%s, %s, %s, 12) ORDER BY id",
              (u["id"], posto["id"], intero["id"])) == [[e4["id"]], [e["id"]], [e2["id"]], [e3["id"]]]
    # la guardia nel database ferma anche una UPDATE diretta
    assert "BUILDING_HAS_UNITS" in _errore(m, "UPDATE buildings SET deleted_at = NOW(), deleted_reason = 'other' "
                                              "WHERE id = %s", (e["id"],))
    # svuotato (a mano, dai suoi posti), l'edificio si sposta
    _q(m, "UPDATE properties SET building_id = NULL WHERE id = 12")
    assert _check(m, e4["id"]).json()["can_trash"] is True and _sposta(m, e4["id"]).status_code == 200


def test_03_permessi_e_agenzie(mondo):
    m = mondo
    mio = _edificio(m, "agent_a")
    del_titolare = _edificio(m)
    altrui = _edificio(m, "owner_b")
    # altra agenzia: 404 indistinguibile, Cestino separato
    assert _check(m, altrui["id"]).status_code == 404 and _sposta(m, altrui["id"]).status_code == 404
    assert _check(m, mio["id"], "owner_b").status_code == 404
    # un agent sposta un edificio vuoto
    assert _codice(_sposta(m, mio["id"], "agent_a"), 200)["deleted_by_user_id"] == m["ids"]["agent_a"]
    assert _codice(_sposta(m, del_titolare["id"]), 200)
    assert _ripristina(m, mio["id"], "owner_b").status_code == 404
    assert mio["id"] not in _cestino(m, "owner_b") and _cestino(m, "owner_b") == {}
    # elenco: l'agent i suoi, il titolare tutti
    assert set(_cestino(m, "agent_a")) == {mio["id"]}
    assert {mio["id"], del_titolare["id"]} <= set(_cestino(m))
    # ripristino: l'agent solo i suoi
    r = _codice(_ripristina(m, del_titolare["id"], "agent_a"), 403)
    assert r["code"] == "NOT_DELETED_BY_YOU"
    assert m["api"]("agent_a").get(f"/api/property/buildings/{del_titolare['id']}").json()["trash"]["can_restore"] is False
    assert _ripristina(m, mio["id"], "agent_a").status_code == 200
    assert _ripristina(m, del_titolare["id"]).status_code == 200
    # l'edificio di B resta di B
    assert _sposta(m, altrui["id"], "owner_b").status_code == 200
    assert altrui["id"] not in _cestino(m) and altrui["id"] in _cestino(m, "owner_b")


def test_04_creazione_guidata_retry_di_un_edificio_nel_cestino(mondo):
    m = mondo
    chiave = str(uuid.uuid4())
    corpo = {"city": "Cupra Marittima", "address": "Via Retry", "civic_number": "3", "client_request_id": chiave}
    r = m["api"]().post("/api/property/buildings", json=corpo)
    assert r.status_code == 201
    bid = r.json()["id"]
    assert _sposta(m, bid).status_code == 200
    r = m["api"]().post("/api/property/buildings", json=corpo)
    assert r.status_code == 409 and r.json()["code"] == "BUILDING_IN_TRASH" and "ripristinala" in r.json()["detail"]
    assert _q(m, "SELECT count(*) FROM buildings WHERE client_request_id = %s", (chiave,))[0][0] == 1


def test_05_ripristino_segnala_possibili_doppioni(mondo):
    m = mondo
    vecchio = _edificio(m, city="Fermo", address="Via Gemella", civic_number="7",
                        cadastral_municipality_code="H321", cadastral_sheet="5", cadastral_parcel="88")
    assert _sposta(m, vecchio["id"], reason="duplicate").status_code == 200
    # nel Cestino non e' una «palazzina simile»: la nuova nasce senza conferma
    r = m["api"]().post("/api/property/buildings", json={"city": "Fermo", "address": "Via Gemella",
                                                         "civic_number": "7"})
    assert r.status_code == 201, r.text
    nuovo = r.json()["id"]
    catastale = _edificio(m, city="Fermo", cadastral_municipality_code="H321", cadastral_sheet="5",
                          cadastral_parcel="88")
    prima = {nuovo: _impronta(m, nuovo), catastale["id"]: _impronta(m, catastale["id"])}
    r = _codice(_ripristina(m, vecchio["id"]), 200)
    assert sorted(d["id"] for d in r["possible_duplicates"]) == sorted([nuovo, catastale["id"]])
    assert {k: _impronta(m, k) for k in prima} == prima                       # nulla unito o modificato
    assert {vecchio["id"], nuovo, catastale["id"]} <= _lista(m)
    meta = _q(m, "SELECT metadata->'possible_duplicates' FROM record_lifecycle_events WHERE entity_type = 'building' "
                 "AND entity_id = %s AND action = 'restore'", (vecchio["id"],))[0][0]
    assert sorted(meta) == sorted([nuovo, catastale["id"]])


def test_06_concorrenza_unita_e_cestino(mondo):
    m = mondo
    from core import database as core_database
    # due spostamenti: uno solo
    e = _edificio(m)
    esiti = _in_parallelo([lambda: _sposta(m, e["id"]), lambda: _sposta(m, e["id"])])
    assert sorted(r.status_code for r in esiti) == [200, 409]
    assert [r.json()["code"] for r in esiti if r.status_code == 409] == ["ALREADY_DELETED"]

    # un'unita' nasce mentre lo spostamento aspetta: lo spostamento la vede e si ferma
    e = _edificio(m)
    altra = core_database.get_connection()
    try:
        with altra.cursor() as cur:
            cur.execute("INSERT INTO properties (title, agency_id, building_id, record_kind) "
                        "VALUES ('in corsa', 1, %s, 'census')", (e["id"],))
        esito = {}
        filo = threading.Thread(target=lambda: esito.update(r=_sposta(m, e["id"])))
        filo.start()
        assert _in_attesa_di_lock(m, "%FROM buildings WHERE id = % FOR UPDATE%")
        altra.commit()
        filo.join(30)
    finally:
        altra.close()
    assert esito["r"].status_code == 409 and esito["r"].json()["code"] == "TRASH_BLOCKED"
    assert _q(m, "SELECT deleted_at FROM buildings WHERE id = %s", (e["id"],))[0][0] is None

    # lo spostamento e' in corso mentre nasce un'unita': l'unita' aspetta ed e' rifiutata
    e = _edificio(m)
    altra = core_database.get_connection()
    try:
        with altra.cursor() as cur:
            cur.execute("SELECT id FROM buildings WHERE id = %s FOR UPDATE", (e["id"],))
            cur.execute("UPDATE buildings SET deleted_at = NOW(), deleted_reason = 'duplicate' WHERE id = %s", (e["id"],))
        esito = {}
        filo = threading.Thread(target=lambda: esito.update(
            r=m["api"]().post("/api/property/census/units", json={"building_id": e["id"], "floor": "3"})))
        filo.start()
        assert _in_attesa_di_lock(m, "%FROM buildings%FOR SHARE%")
        altra.commit()
        filo.join(30)
    finally:
        altra.close()
    assert esito["r"].status_code == 409 and esito["r"].json()["code"] == "BUILDING_IN_TRASH"
    assert _q(m, "SELECT count(*) FROM properties WHERE building_id = %s", (e["id"],))[0][0] == 0

    # stessa corsa direttamente nel database (nessun controllo del servizio): la guardia della 091
    e = _edificio(m)
    altra = core_database.get_connection()
    try:
        with altra.cursor() as cur:
            cur.execute("UPDATE buildings SET deleted_at = NOW(), deleted_reason = 'other' WHERE id = %s", (e["id"],))
        esito = {}

        def inserisci():
            # una connessione PROPRIA: quella della fixture serve a osservare l'attesa
            terza = core_database.get_connection()
            try:
                with terza.cursor() as cur:
                    cur.execute("INSERT INTO properties (title, agency_id, building_id) VALUES ('diretta', 1, %s)",
                                (e["id"],))
                terza.commit()
                esito["e"] = None
            except m["psycopg2"].Error as exc:
                esito["e"] = str(exc).splitlines()[0]
            finally:
                terza.close()
        filo = threading.Thread(target=inserisci)
        filo.start()
        assert _in_attesa_di_lock(m, "%INSERT INTO properties%diretta%")
        altra.commit()
        filo.join(30)
    finally:
        altra.close()
    assert "BUILDING_IN_TRASH" in esito["e"]


def test_99_senza_la_091_e_la_down(mondo):
    m = mondo
    api = m["api"]()
    e = _edificio(m)
    assert _sposta(m, e["id"]).status_code == 200
    giu = GIU.read_text(encoding="utf-8")
    assert "edifici nel Cestino" in _errore(m, giu)
    assert _ripristina(m, e["id"]).status_code == 200
    _q(m, "UPDATE buildings SET deleted_at = NULL, deleted_by_user_id = NULL, deleted_reason = NULL "
          "WHERE deleted_at IS NOT NULL")
    assert "eventi di edifici" in _errore(m, giu)
    trigger = [t for (t,) in _q(m, "SELECT tgname FROM pg_trigger WHERE tgrelid = 'record_lifecycle_events'::regclass "
                                  "AND NOT tgisinternal")]
    for t in trigger:
        _q(m, f"ALTER TABLE record_lifecycle_events DISABLE TRIGGER {t}")
    _q(m, "DELETE FROM record_lifecycle_events WHERE entity_type = 'building'")
    for t in trigger:
        _q(m, f"ALTER TABLE record_lifecycle_events ENABLE TRIGGER {t}")
    assert _errore(m, giu) is None
    assert _q(m, "SELECT count(*) FROM information_schema.columns WHERE table_name = 'buildings' "
                 "AND column_name LIKE 'deleted%%'")[0][0] == 0
    # codice nuovo senza la 091: lista, scheda, modifica e unita' come prima; Cestino 503 leggibile
    assert e["id"] in _lista(m)
    assert api.get(f"/api/property/buildings/{e['id']}").status_code == 200
    assert api.patch(f"/api/property/buildings/{e['id']}", json={"name": "Senza 091"}).status_code == 200
    assert _unita(m, building_id=e["id"], floor="1")["building_id"] == e["id"]
    r = _check(m, e["id"])
    assert r.status_code == 503 and r.json()["code"] == "TRASH_NOT_INSTALLED"
    assert api.get("/api/property/trash/buildings").json()["code"] == "TRASH_NOT_INSTALLED"
    # up di nuovo
    with m["conn"].cursor() as cur:
        cur.execute("BEGIN")
        cur.execute(SU.read_text(encoding="utf-8"))
        cur.execute("COMMIT")
    assert _q(m, "SELECT count(*) FROM pg_trigger WHERE tgname IN ('trg_properties_building_trash_guard', "
                 "'trg_buildings_trash_freeze')")[0][0] == 2
    assert [b["code"] for b in _check(m, e["id"]).json()["blockers"]] == ["BUILDING_HAS_UNITS"]
