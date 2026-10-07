"""PERTINENZE-1 (FASE E) - pertinenze nel percorso palazzina / unita', su PostgreSQL VERO.

Schema completo dal runner (migration 089 compresa), rotte vere del router
Immobili, fixture del censimento (CENSIMENTO-1 Fase 3). Nessun sistema
parallelo: accessori, unita' di censimento, «Collega esistente», «Scollega» e
«Chiarisci» sono quelli di sempre; la 089 aggiunge la NATURA di pertinenza
(`is_pertinenza`) e il suo tipo (`pertinenza_kind`).

  01  «Aggiungi pertinenza» dalla scheda: accessorio compreso (senza sub),
      «Da verificare» (accessorio da chiarire, nessuna unita' inventata),
      pertinenza autonoma con sub (scheda collegata, tipo conservato, vincoli
      catastali di sempre); conteggi: gli accessori non contano mai;
  02  pertinenza censita nella palazzina e NON collegata: conta come
      pertinenza «da collegare», mai come principale; candidate = principali
      della stessa palazzina; collegamento successivo; edificio invariato;
      una pertinenza non puo' avere pertinenze; due collegamenti concorrenti;
  03  scollegamento senza perdita: record, edificio, tipo, proprietari e
      documenti invariati; resta pertinenza «da collegare»; ricollegabile;
  04  accessorio -> unita' autonoma («Chiarisci»): dati e provenienza (anche
      «Dal sito») conservati, accessorio rimosso, nessun doppio conteggio;
      box e posto auto distinti; ramo «collega esistente»;
  05  sito: un accessorio diventato unita' non si ricrea dalla stima
      dettagliata, e «Applica» su una differenza precedente non lo duplica;
  06  agenzie separate, Cestino, permessi; nessun edificio dedotto
      dall'indirizzo; censimento/commerciale e presa in carico;
  99  codice nuovo su un database SENZA la 089 (ordine di rilascio): tutto
      come prima, una pertinenza dichiarata si rifiuta in modo leggibile; la
      down si ferma con pertinenze marcate; up di nuovo.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401
    DSN, _edificio, _in_parallelo, _q, _unita, completo, mondo,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

MIGRAZIONI = Path(__file__).resolve().parents[1] / "migrations"


def _scheda_edificio(m, building_id, chi="owner_a"):
    r = m["api"](chi).get(f"/api/property/buildings/{building_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _censimento(m, property_id, chi="owner_a"):
    r = m["api"](chi).get(f"/api/property/properties/{property_id}/census")
    assert r.status_code == 200, r.text
    return r.json()


def _collega(m, principale_id, pertinenza_id, chi="owner_a"):
    return m["api"](chi).post(f"/api/property/properties/{principale_id}/pertinenze/link",
                              json={"pertinenza_id": pertinenza_id})


def _scollega(m, principale_id, pertinenza_id, chi="owner_a"):
    return m["api"](chi).post(f"/api/property/properties/{principale_id}/pertinenze/{pertinenza_id}/unlink")


def _riga(m, pid, colonne="parent_property_id, is_pertinenza, pertinenza_kind, building_id, property_type"):
    return _q(m, f"SELECT {colonne} FROM properties WHERE id = %s", (pid,))[0]


def _split(m, building_id):
    s = _scheda_edificio(m, building_id)["census_summary"]
    return (s["units_main"], s["units_pertinenze"], s["units_pertinenze_unlinked"], s["units_counted"])


# ---------------------------------------------------------------------------

def test_01_aggiungi_pertinenza_accessorio_da_verificare_o_autonoma(mondo):
    m = mondo
    api = m["api"]()
    e = _edificio(m, units_declared=3)
    app = _unita(m, building_id=e["id"], floor="2", internal_number="4")
    base = f"/api/property/properties/{app['id']}/accessories"
    # senza sub proprio: accessorio compreso, mq con decimali, quantita'
    r = api.post(base, json={"kind": "posto_auto", "cadastral_status": "included", "surface_sqm": "12.5", "quantity": 2})
    assert r.status_code == 201, r.text
    assert (r.json()["kind"], Decimal(str(r.json()["surface_sqm"])), r.json()["quantity"]) == ("posto_auto", Decimal("12.50"), 2)
    # da verificare: accessorio «da chiarire», nessun subalterno, nessuna unita'
    r = api.post(base, json={"kind": "cantina", "cadastral_status": "unknown", "surface_sqm": "6.25"})
    assert r.status_code == 201 and r.json()["cadastral_status"] == "unknown"
    # con sub proprio: scheda autonoma collegata, tipo conservato, catasto di sempre
    box = _unita(m, building_id=e["id"], parent_property_id=app["id"], property_type="garage", pertinenza_kind="box",
                 surface_sqm="18.5", cadastral_category="C/6", cadastral_municipality_code="D542", cadastral_section="",
                 cadastral_sheet="12", cadastral_parcel="345", cadastral_subunit="9")
    assert _riga(m, box["id"]) == [app["id"], True, "box", e["id"], "garage"]
    assert Decimal(str(box["surface_sqm"])) == Decimal("18.50") and box["record_kind"] == "census"
    # stessa identita' catastale completa: bloccata come sempre
    r = api.post("/api/property/census/units", json={"building_id": e["id"], "parent_property_id": app["id"],
                                                    "property_type": "garage", "pertinenza_kind": "posto_auto",
                                                    "cadastral_municipality_code": "D542", "cadastral_section": "",
                                                    "cadastral_sheet": "12", "cadastral_parcel": "345", "cadastral_subunit": "9"})
    assert (r.status_code, r.json()["code"]) == (409, "CADASTRAL_DUPLICATE")
    # un tipo fuori catalogo non passa; «stabile intero» non e' una pertinenza
    assert api.post("/api/property/census/units", json={"pertinenza_kind": "posto_barca"}).status_code == 422
    r = api.post("/api/property/census/units", json={"building_id": e["id"], "property_type": "building",
                                                    "whole_building": True, "is_pertinenza": True})
    assert (r.status_code, r.json()["code"]) == (400, "LINK_INVALID")
    # conteggi: 1 principale + 1 pertinenza; gli accessori non contano mai
    assert _split(m, e["id"]) == (1, 1, 0, 2)
    c = _censimento(m, app["id"])
    assert [x["id"] for x in c["pertinenze"]] == [box["id"]] and c["pertinenze"][0]["pertinenza_kind"] == "box"
    assert c["accessories_unknown"] == 1 and c["is_pertinenza"] is False
    figlia = _censimento(m, box["id"])
    assert (figlia["is_pertinenza"], figlia["pertinenza_unlinked"], figlia["pertinenza_kind"]) == (True, False, "box")
    assert figlia["parent"]["id"] == app["id"] and figlia["parent"]["same_building"] is True
    # una pertinenza non ha pertinenze
    r = api.post("/api/property/census/units", json={"parent_property_id": box["id"], "property_type": "storage"})
    assert (r.status_code, r.json()["code"]) == (400, "LINK_INVALID")


def test_02_pertinenza_da_collegare_poi_collegata(mondo):
    m = mondo
    e = _edificio(m, units_declared=4)
    a1 = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    a2 = _unita(m, building_id=e["id"], floor="2", internal_number="2")
    # censita nella palazzina, senza principale: pertinenza «da collegare»
    posto = _unita(m, building_id=e["id"], is_pertinenza=True, pertinenza_kind="posto_auto", property_type="garage",
                   floor="-1", surface_sqm="12.75")
    assert _riga(m, posto["id"]) == [None, True, "posto_auto", e["id"], "garage"]
    assert _split(m, e["id"]) == (2, 1, 1, 3)                     # mai contata come principale
    s = _scheda_edificio(m, e["id"])
    riga = next(u for u in s["units"] if u["id"] == posto["id"])
    assert (riga["is_pertinenza"], riga["pertinenza"], riga["pertinenza_kind"], riga["parent"]) == (True, True, "posto_auto", None)
    assert s["counters"]["units_pertinenze_unlinked"] == 1 and s["counters"]["units_main"] == 2
    c = _censimento(m, posto["id"])
    assert c["pertinenza_unlinked"] is True and [x["id"] for x in c["link_candidates"]] == [a1["id"], a2["id"]]
    # una pertinenza non fa da principale (nemmeno se non collegata)
    altra = _unita(m, building_id=e["id"], is_pertinenza=True, pertinenza_kind="cantina", property_type="storage")
    r = _collega(m, altra["id"], posto["id"])
    assert (r.status_code, r.json()["code"]) == (400, "LINK_INVALID")
    r = m["api"]().post("/api/property/census/units", json={"parent_property_id": posto["id"]})
    assert (r.status_code, r.json()["code"]) == (400, "LINK_INVALID")
    # collegamento successivo: edificio invariato, storico su entrambe
    r = _collega(m, a2["id"], posto["id"])
    assert r.status_code == 200 and r.json()["linked"] is True
    assert _riga(m, posto["id"]) == [a2["id"], True, "posto_auto", e["id"], "garage"]
    assert _split(m, e["id"]) == (2, 2, 1, 4)                     # resta da collegare solo la cantina
    assert _censimento(m, posto["id"])["link_candidates"] == []
    storia = [r[0] for r in _q(m, "SELECT description FROM activities WHERE property_id = ANY(%s) ORDER BY id",
                               ([a2["id"], posto["id"]],))]
    assert any("collegata" in d.lower() for d in storia) and len(storia) >= 2
    # due collegamenti concorrenti della stessa pertinenza: uno solo vince
    esiti = _in_parallelo([lambda: _collega(m, a1["id"], altra["id"]), lambda: _collega(m, a2["id"], altra["id"])])
    assert sorted(x.status_code for x in esiti) == [200, 409]
    assert next(x for x in esiti if x.status_code == 409).json()["code"] == "ALREADY_LINKED"
    vincitore = next(x for x in esiti if x.status_code == 200).json()["property_id"]
    assert _riga(m, altra["id"])[0] == vincitore


def test_03_scollegare_senza_perdere_dati_proprietari_documenti(mondo):
    m = mondo
    api = m["api"]()
    e = _edificio(m)
    app = _unita(m, building_id=e["id"], floor="1", record_kind="crm")
    cantina = _unita(m, building_id=e["id"], parent_property_id=app["id"], property_type="storage",
                     pertinenza_kind="cantina", record_kind="crm", surface_sqm="8.4", cadastral_subunit="11",
                     internal_notes="cantina a nord")
    contatto = _q(m, "SELECT id FROM contacts WHERE agency_id = 1 ORDER BY id LIMIT 1")[0][0]
    r = api.post(f"/api/property/properties/{cantina['id']}/contacts", json={"contact_id": contatto, "role": "owner"})
    assert r.status_code == 201, r.text
    r = api.post(f"/api/property/properties/{cantina['id']}/documents",
                 json={"document_type": "visura", "title": "Visura cantina", "url": "https://example.test/v.pdf"})
    assert r.status_code == 201, r.text
    prima = _q(m, "SELECT code, surface_sqm, cadastral_subunit, internal_notes, record_kind, building_id FROM properties WHERE id = %s",
               (cantina["id"],))[0]
    r = _scollega(m, app["id"], cantina["id"])
    assert r.status_code == 200, r.text
    # record intatto, resta pertinenza «da collegare» con il suo tipo e il suo edificio
    assert _riga(m, cantina["id"]) == [None, True, "cantina", e["id"], "storage"]
    assert _q(m, "SELECT code, surface_sqm, cadastral_subunit, internal_notes, record_kind, building_id FROM properties WHERE id = %s",
              (cantina["id"],))[0] == prima
    assert _q(m, "SELECT contact_id, role FROM property_contacts WHERE property_id = %s", (cantina["id"],)) == [[contatto, "owner"]]
    assert _q(m, "SELECT count(*) FROM property_documents WHERE property_id = %s", (cantina["id"],))[0][0] == 1
    assert _q(m, "SELECT archived_at, commercial_status FROM properties WHERE id = %s", (cantina["id"],))[0][0] is None
    assert _split(m, e["id"]) == (1, 1, 1, 2)                     # censite invariate, nessun doppio
    assert _censimento(m, cantina["id"])["pertinenza_unlinked"] is True
    # ricollegabile, anche a un'unita' di un altro edificio (il garage della palazzina di fronte)
    e2 = _edificio(m, address="Via Roma", civic_number="11", name="Di fronte")
    altro = _unita(m, building_id=e2["id"], floor="3", record_kind="crm")
    assert _collega(m, altro["id"], cantina["id"]).status_code == 200
    assert _riga(m, cantina["id"])[:4] == [altro["id"], True, "cantina", e["id"]]     # l'edificio NON cambia
    c = _censimento(m, cantina["id"])
    assert c["parent"]["id"] == altro["id"] and c["parent"]["same_building"] is False
    assert _split(m, e["id"]) == (1, 1, 0, 2)
    assert [p["id"] for p in _censimento(m, altro["id"])["pertinenze"]] == [cantina["id"]]


def test_04_accessorio_diventa_unita_autonoma_con_provenienza(mondo):
    m = mondo
    api = m["api"]()
    e = _edificio(m)
    app = _unita(m, building_id=e["id"], floor="2")
    base = f"/api/property/properties/{app['id']}/accessories"
    box = api.post(base, json={"kind": "box", "cadastral_status": "unknown", "surface_sqm": "18.5", "notes": "box doppio"}).json()
    posto = api.post(base, json={"kind": "posto_auto", "cadastral_status": "unknown", "surface_sqm": "12.5", "quantity": 1}).json()
    _q(m, "UPDATE property_accessories SET source = 'stima360' WHERE id = %s", (box["id"],))     # arrivato dal sito
    assert _scheda_edificio(m, e["id"])["census_summary"]["accessories_unknown"] == 2
    r = api.post(f"{base}/{box['id']}/resolve", json={"outcome": "separate", "cadastral_subunit": "9"})
    assert r.status_code == 200, r.text
    u = r.json()["pertinenza"]
    assert _riga(m, u["id"]) == [app["id"], True, "box", e["id"], "garage"]
    meta = _q(m, "SELECT metadata FROM properties WHERE id = %s", (u["id"],))[0][0]
    assert meta["from_accessory"]["accessory_id"] == box["id"] and meta["from_accessory"]["source"] == "stima360"
    assert (meta["from_accessory"]["kind"], meta["from_accessory"]["surface_sqm"], meta["from_accessory"]["notes"]) == ("box", "18.50", "box doppio")
    assert Decimal(str(u["surface_sqm"])) == Decimal("18.50") and u["internal_notes"] == "box doppio"
    # l'accessorio non c'e' piu': nessun doppio conteggio
    assert _q(m, "SELECT count(*) FROM property_accessories WHERE id = %s", (box["id"],))[0][0] == 0
    s = _scheda_edificio(m, e["id"])["census_summary"]
    assert (s["units_pertinenze"], s["accessories_unknown"], s["units_counted"]) == (1, 1, 2)
    storia = _q(m, "SELECT metadata FROM activities WHERE property_id = %s AND metadata ->> 'operation' = 'accessory_resolve_create'",
                (app["id"],))[0][0]
    assert storia["accessory"]["source"] == "stima360"
    c = _censimento(m, u["id"])
    assert c["from_accessory"]["kind"] == "box"
    # posto auto: tipologia garage, tipo posto_auto (distinto dal box)
    r = api.post(f"{base}/{posto['id']}/resolve", json={"outcome": "separate"})
    assert r.status_code == 200, r.text
    assert _riga(m, r.json()["pertinenza"]["id"])[1:3] == [True, "posto_auto"]
    # «collega esistente»: una pertinenza da collegare (senza tipo) prende tipo e provenienza
    sciolta = _unita(m, building_id=e["id"], is_pertinenza=True, property_type="storage")
    cantina = api.post(base, json={"kind": "cantina", "cadastral_status": "unknown", "surface_sqm": "5"}).json()
    r = api.post(f"{base}/{cantina['id']}/resolve", json={"outcome": "separate", "existing_property_id": sciolta["id"]})
    assert r.status_code == 200, r.text
    assert _riga(m, sciolta["id"])[:3] == [app["id"], True, "cantina"]
    assert _q(m, "SELECT metadata -> 'from_accessory' ->> 'kind' FROM properties WHERE id = %s", (sciolta["id"],))[0][0] == "cantina"
    # anche un accessorio «Compreso» si puo' rivedere (ha un suo sub): stessa conversione
    terrazzo = api.post(base, json={"kind": "terrazzo", "cadastral_status": "included", "surface_sqm": "20"}).json()
    r = api.post(f"{base}/{terrazzo['id']}/resolve", json={"outcome": "separate"})
    assert r.status_code == 200 and _riga(m, r.json()["pertinenza"]["id"])[2] == "terrazzo"


def test_06_agenzie_cestino_indirizzo_e_presa_in_carico(mondo):
    m = mondo
    e = _edificio(m)
    app = _unita(m, building_id=e["id"], floor="1")
    sciolta = _unita(m, building_id=e["id"], is_pertinenza=True, pertinenza_kind="box", property_type="garage")
    # agenzia B: non vede, non collega
    eb = _edificio(m, chi="owner_b")
    ub = _unita(m, chi="owner_b", building_id=eb["id"], floor="1")
    assert _collega(m, ub["id"], sciolta["id"], chi="owner_b").status_code == 404
    assert _collega(m, app["id"], sciolta["id"], chi="owner_b").status_code == 404
    assert m["api"]("owner_b").get(f"/api/property/properties/{sciolta['id']}/census").status_code == 404
    # nessun edificio dedotto dall'indirizzo: stessa via e civico, senza building_id
    fuori = _unita(m, is_pertinenza=True, pertinenza_kind="cantina", property_type="storage",
                   city="Fermo", address="Via Roma", civic_number="10")
    assert fuori["building_id"] is None and _split(m, e["id"]) == (1, 1, 1, 2)
    assert _censimento(m, fuori["id"])["link_candidates"] == []
    # Cestino: non conta, non si collega, non si scollega
    r = m["api"]().post(f"/api/property/properties/{sciolta['id']}/trash", json={"reason_code": "duplicate"})
    assert r.status_code == 200, r.text
    assert _split(m, e["id"]) == (1, 0, 0, 1)
    assert _collega(m, app["id"], sciolta["id"]).status_code in (404, 409)
    # presa in carico: le pertinenze COLLEGATE seguono, quelle da collegare no
    collegata = _unita(m, building_id=e["id"], parent_property_id=app["id"], pertinenza_kind="posto_auto", property_type="garage")
    da_collegare = _unita(m, building_id=e["id"], is_pertinenza=True, pertinenza_kind="cantina", property_type="storage")
    r = m["api"]().post(f"/api/property/properties/{app['id']}/take-in-charge", json={"include_pertinenze": True})
    assert r.status_code == 200 and r.json()["pertinenze_taken"] == [collegata["id"]]
    assert _q(m, "SELECT record_kind FROM properties WHERE id = %s", (da_collegare["id"],))[0][0] == "census"
    # l'agente non assegnato non archivia la pertinenza (regola di sempre)
    r = m["api"]("agent_a").post(f"/api/property/properties/{collegata['id']}/archive")
    assert r.status_code == 403


def test_99_ordine_di_rilascio_senza_la_089_down_e_up(mondo):
    """Codice nuovo su un database senza la 089: tutto come prima (nessuna
    pertinenza «da collegare»), una pertinenza dichiarata si rifiuta in modo
    leggibile. La down si ferma con pertinenze marcate; senza, toglie le
    colonne; la up si riapplica."""
    m = mondo
    su = (MIGRAZIONI / "089_pertinenze_1_unit_nature.sql").read_text(encoding="utf-8")
    giu = (MIGRAZIONI / "089_pertinenze_1_unit_nature_down.sql").read_text(encoding="utf-8")
    e = _edificio(m)
    marcata = _unita(m, building_id=e["id"], is_pertinenza=True, property_type="garage")
    with m["conn"].cursor() as cur:
        with pytest.raises(Exception, match="089 down"):
            cur.execute(giu)
        cur.execute("ROLLBACK")
    _q(m, "DELETE FROM properties WHERE id = %s", (marcata["id"],))
    _q(m, giu)
    try:
        assert _q(m, "SELECT count(*) FROM information_schema.columns WHERE table_name = 'properties' "
                     "AND column_name IN ('is_pertinenza', 'pertinenza_kind')")[0][0] == 0
        app = _unita(m, building_id=e["id"], floor="1")
        garage = _unita(m, building_id=e["id"], parent_property_id=app["id"], property_type="garage")     # come prima
        assert _split(m, e["id"]) == (1, 1, 0, 2)
        c = _censimento(m, garage["id"])
        assert c["is_pertinenza"] is True and c["pertinenza_kind"] is None
        r = m["api"]().post("/api/property/census/units", json={"building_id": e["id"], "is_pertinenza": True})
        assert (r.status_code, r.json()["code"]) == (409, "PERTINENZE_NOT_INSTALLED")
        assert _scollega(m, app["id"], garage["id"]).status_code == 200
        assert _split(m, e["id"]) == (2, 0, 0, 2)                 # senza la 089: come prima
        # «Chiarisci» funziona; la provenienza resta nei metadata
        acc = m["api"]().post(f"/api/property/properties/{app['id']}/accessories",
                              json={"kind": "posto_auto", "cadastral_status": "unknown"}).json()
        r = m["api"]().post(f"/api/property/properties/{app['id']}/accessories/{acc['id']}/resolve", json={"outcome": "separate"})
        assert r.status_code == 200, r.text
        assert _q(m, "SELECT metadata -> 'from_accessory' ->> 'kind' FROM properties WHERE id = %s",
                  (r.json()["pertinenza"]["id"],))[0][0] == "posto_auto"
        assert m["api"]().get(f"/api/property/buildings/{e['id']}").status_code == 200
        assert m["api"]().get("/api/property/buildings").status_code == 200
    finally:
        with m["conn"].cursor() as cur:
            cur.execute("BEGIN")
            cur.execute(su)
            cur.execute("COMMIT")
    assert _q(m, "SELECT count(*) FROM information_schema.columns WHERE table_name = 'properties' "
                 "AND column_name IN ('is_pertinenza', 'pertinenza_kind')")[0][0] == 2
    assert _unita(m, building_id=e["id"], is_pertinenza=True, pertinenza_kind="box", property_type="garage")["id"]
