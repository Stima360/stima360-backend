"""CENSIMENTO-1 Fase 4 REV 2 (R1) - il contratto della sezione catastale sul
database VERO (083 applicata), attraverso le rotte reali: i tre stati
(NULL = non conosciuta, '' = nessuna accertata, stringa = valore) in
creazione e nelle transizioni della PATCH generica, con il blocco del
duplicato e l'avviso a sezione sconosciuta.

Stessa fixture di `test_censimento_3_backend_postgres` (P29_TEST_DSN, database
locale usa-e-getta `stima360_db_test`); senza DSN il modulo e' SKIP.
"""
from __future__ import annotations

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _edificio, _q, _unita, completo, mondo,
)

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare CENSIMENTO-1 Fase 4 REV 2")

IDENTITA = {"cadastral_municipality_code": "D542", "cadastral_sheet": "12", "cadastral_parcel": "345", "cadastral_subunit": "6"}


def _sezione(m, pid):
    return _q(m, "SELECT cadastral_section FROM properties WHERE id = %s", (pid,))[0][0]


def test_01_creazione_tre_stati_e_identita_completa_con_sezione_vuota(mondo):
    e = _edificio(mondo)
    api = mondo["api"]()
    # '' = nessuna sezione, accertato: entra nella chiave di unicita'
    nessuna = _unita(mondo, building_id=e["id"], floor="1", **IDENTITA, cadastral_section="")
    assert _sezione(mondo, nessuna["id"]) == ""                       # salvata come '', non NULL
    # stessa identita' con '' -> duplicato BLOCCANTE
    r = api.post("/api/property/census/units", json={"building_id": e["id"], "floor": "2", **IDENTITA, "cadastral_section": ""})
    assert r.status_code == 409 and r.json()["code"] == "CADASTRAL_DUPLICATE" and r.json()["existing"]["id"] == nessuna["id"]
    # stessi identificativi con sezione NON conosciuta (campo assente) -> solo AVVISO
    r = api.post("/api/property/census/units", json={"building_id": e["id"], "floor": "2", **IDENTITA})
    assert r.status_code == 409 and r.json()["code"] == "SIMILAR_FOUND"
    assert [s["reason"] for s in r.json()["similar"]] == ["cadastral_section_unknown"]
    r = api.post("/api/property/census/units", json={"building_id": e["id"], "floor": "2", **IDENTITA, "confirm_similar": True})
    assert r.status_code == 201
    sconosciuta = r.json()
    assert _sezione(mondo, sconosciuta["id"]) is None                 # NULL: non conosciuta
    # valore
    con_valore = _unita(mondo, building_id=e["id"], floor="3", **IDENTITA, cadastral_section=" b ")
    assert _sezione(mondo, con_valore["id"]) == "B"                   # normalizzata dalla 083
    # la chiave di unicita' e' per sezione: tre righe con la stessa particella/sub e sezioni '', NULL, 'B'
    assert _q(mondo, "SELECT count(*) FROM properties WHERE cadastral_subunit = '6' AND cadastral_parcel = '345' AND archived_at IS NULL")[0][0] == 3


def test_02_transizioni_della_sezione_in_patch_con_duplicato_leggibile(mondo):
    e = _edificio(mondo)
    api = mondo["api"]()
    nessuna = _unita(mondo, building_id=e["id"], floor="1", **IDENTITA, cadastral_section="")
    altra = _unita(mondo, building_id=e["id"], floor="2", **IDENTITA, confirm_similar=True)   # NULL
    assert _sezione(mondo, altra["id"]) is None
    # NULL -> '' sull'identita' di `nessuna`: la 083 blocca; la risposta e' 409 CADASTRAL_DUPLICATE, non «property code»
    r = api.patch(f"/api/property/properties/{altra['id']}", json={"cadastral_section": ""})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "CADASTRAL_DUPLICATE" and "censito" in r.json()["detail"]
    assert _sezione(mondo, altra["id"]) is None                       # invariata
    # NULL -> 'B': consentito
    r = api.patch(f"/api/property/properties/{altra['id']}", json={"cadastral_section": "b"})
    assert r.status_code == 200 and r.json()["cadastral_section"] == "B"
    assert _sezione(mondo, altra["id"]) == "B"
    # 'B' -> '' con un ALTRO subalterno: consentito, resta '' (non NULL)
    r = api.patch(f"/api/property/properties/{altra['id']}", json={"cadastral_section": "", "cadastral_subunit": "7"})
    assert r.status_code == 200 and r.json()["cadastral_section"] == ""
    assert _sezione(mondo, altra["id"]) == ""
    # '' -> NULL (torna non conosciuta): consentito
    r = api.patch(f"/api/property/properties/{altra['id']}", json={"cadastral_section": None})
    assert r.status_code == 200 and r.json()["cadastral_section"] is None
    assert _sezione(mondo, altra["id"]) is None
    # e la riga a sezione '' e' rimasta intatta
    assert _sezione(mondo, nessuna["id"]) == ""
    # il codice immobile duplicato resta l'altro caso del 409 generico
    r = api.patch(f"/api/property/properties/{altra['id']}", json={"code": nessuna["code"]})
    assert r.status_code == 409 and r.json()["code"] == "CONFLICT"


def test_03_pertinenza_in_palazzina_eredita_edificio_e_indirizzo(mondo):
    """R2 lato contratto: la pertinenza creata con building_id + parent ha
    l'indirizzo ereditato e conta fra le unita' della palazzina; senza
    building_id il backend NON lo deduce dal genitore."""
    e = _edificio(mondo)
    api = mondo["api"]()
    principale = _unita(mondo, building_id=e["id"], floor="2", internal_number="4")
    con = _unita(mondo, building_id=e["id"], parent_property_id=principale["id"], property_type="garage")
    senza = _unita(mondo, parent_property_id=principale["id"], property_type="storage", city="Fermo", address="Via Po", civic_number="3")
    righe = {r[0]: r[1:] for r in _q(mondo, "SELECT id, building_id, parent_property_id, address_inherited, address FROM properties WHERE id IN %s",
                                      ((con["id"], senza["id"]),))}
    assert righe[con["id"]] == [e["id"], principale["id"], True, "Via Roma"]
    assert righe[senza["id"]] == [None, principale["id"], False, "Via Po"]
    dettaglio = api.get(f"/api/property/buildings/{e['id']}").json()
    assert {u["id"] for u in dettaglio["units"]} == {principale["id"], con["id"]}
    assert dettaglio["counters"]["units_pertinenze"] == 1 and dettaglio["counters"]["units_main"] == 1
