"""CENSIMENTO-2 - composizione degli edifici e schede delle unita', su PostgreSQL VERO.

Schema completo dal runner (migration 100 compresa), rotte vere del router
Immobili, fixture del censimento (CENSIMENTO-1 Fase 3). Solo il database
locale usa-e-getta di P29_TEST_DSN.

  01  migration 100: nessun backfill; provenienza solo con il dato;
      «verificata» solo con l'identita' catastale completa; la provenienza
      segue il valore e l'identita' (trigger); composizione >= 0 e coerente;
  02  composizione: bozza parziale sempre salvabile; totale solo su scelta
      esplicita; 409 se il totale noto non torna; stato calcolato;
  03  anteprima in sola lettura; composizione obbligatoria per generare;
  04  conferma: schede census in bozza, pertinenze «da collegare», nessuna
      categoria dedotta, provenienza esplicita, numerazione facoltativa e
      provvisoria, indirizzo ereditato, codici; due indicatori separati;
  05  completamento di un edificio ESISTENTE: solo la differenza per classe,
      nessuna unita' esistente toccata o duplicata; idempotenza del lotto;
      conteggi cambiati -> 409; chiave riusata -> 409;
  06  concorrenza deterministica: due lotti diversi -> uno vince, l'altro 409;
      lo stesso lotto due volte -> una sola serie di unita';
  07  eccedenza: conferma esplicita;
  08  agenzie separate, Cestino, validazione (verified in blocco, 300);
  09  annullamento del lotto: solo le schede intatte e senza collegamenti;
  10  modifica di gruppo: atomica, elenco chiuso, «verificata» solo singola e
      con evidenza, provenienza senza dato rifiutata, storico, permessi crm;
  11  classi (D1): rustico abitazione solo se esplicito; box/cantine autonomi
      non pertinenze; compatibilita' (presa in carico, elenchi commerciali);
  12  prestazioni: un lotto di 300 unita' e il suo annullamento;
  99  la down si ferma con dati; senza, toglie tutto; la up si riapplica; il
      codice senza la 100 risponde 503 leggibile e il resto funziona.
"""
from __future__ import annotations

import time
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401
    DSN, _edificio, _in_parallelo, _q, _unita, completo, mondo,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

MIGRAZIONI = Path(__file__).resolve().parents[1] / "migrations"
CLASSI = ("residential", "commercial", "other", "pertinenze")


def _composizione(m, bid, chi="owner_a", **kw):
    corpo = {"residential": None, "commercial": None, "other": None, "pertinenze": None, **kw}
    return m["api"](chi).put(f"/api/property/buildings/{bid}/composition", json=corpo)


def _stato(m, bid, chi="owner_a"):
    r = m["api"](chi).get(f"/api/property/buildings/{bid}/composition")
    assert r.status_code == 200, r.text
    return r.json()


def _genera(m, bid, gruppi, chi="owner_a", chiave=None, **kw):
    corpo = {"client_request_id": str(chiave or uuid.uuid4()), "groups": gruppi, **kw}
    return m["api"](chi).post(f"/api/property/buildings/{bid}/units/generate", json=corpo)


def _conferma(m, bid, gruppi, chi="owner_a", chiave=None, **kw):
    """Anteprima, poi conferma con i conteggi dell'anteprima (il flusso della UI)."""
    chiave = chiave or uuid.uuid4()
    a = _genera(m, bid, gruppi, chi=chi, chiave=chiave)
    assert a.status_code == 200, a.text
    return _genera(m, bid, gruppi, chi=chi, chiave=chiave, dry_run=False,
                   expected_existing=a.json()["existing"], **kw), chiave


def _edificio_composto(m, res=4, com=1, oth=1, per=2, **kw):
    e = _edificio(m, units_declared=None, **kw)
    r = _composizione(m, e["id"], residential=res, commercial=com, other=oth, pertinenze=per, set_total=True,
                      units_declared_source="survey")
    assert r.status_code == 200, r.text
    return e


def _n(m, bid):
    return _q(m, "SELECT count(*) FROM properties WHERE building_id = %s", (bid,))[0][0]


# ---------------------------------------------------------------------------

def test_01_migration_100_vincoli_e_trigger(mondo):
    m = mondo
    pg = m["psycopg2"]
    # nessun backfill: le righe storiche della fixture restano senza provenienza
    assert _q(m, "SELECT count(*) FROM properties WHERE surface_source IS NOT NULL "
                 "OR cadastral_category_source IS NOT NULL")[0][0] == 0
    e = _edificio(m)
    assert _q(m, "SELECT units_declared_residential, units_declared_commercial, units_declared_other, "
                 "units_declared_pertinenze FROM buildings WHERE id = %s", (e["id"],))[0] == [None] * 4
    u = _unita(m, building_id=e["id"], floor="1", surface_sqm="65", cadastral_category="A/2")
    pid = u["id"]
    # provenienza senza dato: rifiutata dal database
    with pytest.raises(pg.errors.CheckViolation):
        _q(m, "UPDATE properties SET surface_sqm = NULL, surface_source = 'declared' WHERE id = %s", (pid,))
    # «verificata»: i soli campi catastali compilati NON bastano (nessuna falsa verifica);
    # serve la fonte consultata e registrata (tipo e data), legata a QUELLA identita'
    with pytest.raises(pg.errors.CheckViolation):
        _q(m, "UPDATE properties SET cadastral_category_source = 'verified' WHERE id = %s", (pid,))
    _q(m, "UPDATE properties SET cadastral_municipality_code = 'd542', cadastral_sheet = '12', "
          "cadastral_parcel = '345', cadastral_subunit = '7' WHERE id = %s", (pid,))
    assert _q(m, "SELECT cadastral_section FROM properties WHERE id = %s", (pid,))[0][0] is None
    with pytest.raises(pg.errors.CheckViolation):                                  # identita' completa, nessuna fonte
        _q(m, "UPDATE properties SET cadastral_category_source = 'verified' WHERE id = %s", (pid,))
    with pytest.raises(pg.errors.CheckViolation):                                  # tipo di documento fuori elenco
        _q(m, "UPDATE properties SET cadastral_evidence_type = 'foto' WHERE id = %s", (pid,))
    _q(m, "UPDATE properties SET cadastral_category_source = 'verified', surface_source = 'declared', "
          "cadastral_evidence_type = 'visura', cadastral_evidence_date = '2026-10-01' WHERE id = %s", (pid,))
    # la verifica si lega all'identita' attuale (sezione facoltativa)
    assert _q(m, "SELECT cadastral_evidence_identity FROM properties WHERE id = %s", (pid,))[0][0] == "D542||12|345|7"
    # rimandare gli stessi valori (anche non normalizzati) non tocca nulla
    _q(m, "UPDATE properties SET cadastral_municipality_code = 'd542', surface_sqm = 65.00, cadastral_category = 'A/2' "
          "WHERE id = %s", (pid,))
    assert _q(m, "SELECT surface_source, cadastral_category_source FROM properties WHERE id = %s", (pid,))[0] == [
        "declared", "verified"]
    # campi non correlati (scala, note, locali): nulla cambia
    _q(m, "UPDATE properties SET staircase = 'A', internal_notes = 'x', rooms = 3 WHERE id = %s", (pid,))
    assert _q(m, "SELECT surface_source, cadastral_category_source FROM properties WHERE id = %s", (pid,))[0] == [
        "declared", "verified"]
    # cambiano i riferimenti catastali: la «verificata» va rivalutata (non indicata), la dichiarata resta;
    # la fonte registrata resta come storico e dice «da riconfermare»
    _q(m, "UPDATE properties SET cadastral_subunit = '8' WHERE id = %s", (pid,))
    assert _q(m, "SELECT surface_source, cadastral_category_source, cadastral_evidence_type, cadastral_evidence_identity "
                 "FROM properties WHERE id = %s", (pid,))[0] == ["declared", None, "visura", "D542||12|345|7"]
    # riconferma esplicita con la nuova identita'
    _q(m, "UPDATE properties SET cadastral_category_source = 'verified', cadastral_evidence_date = '2026-10-05' WHERE id = %s", (pid,))
    assert _q(m, "SELECT cadastral_category_source, cadastral_evidence_identity FROM properties WHERE id = %s", (pid,))[0] == [
        "verified", "D542||12|345|8"]
    # identita' cambiata E riconferma nella stessa istruzione: resta verificata
    _q(m, "UPDATE properties SET cadastral_subunit = '9', cadastral_category_source = 'verified' WHERE id = %s", (pid,))
    assert _q(m, "SELECT cadastral_category_source, cadastral_evidence_identity FROM properties WHERE id = %s", (pid,))[0] == [
        "verified", "D542||12|345|9"]
    # il valore cambia senza nuova conferma: torna «non indicata» (solo la sua)
    _q(m, "UPDATE properties SET surface_sqm = 70 WHERE id = %s", (pid,))
    assert _q(m, "SELECT surface_source, cadastral_category_source FROM properties WHERE id = %s", (pid,))[0] == [
        None, "verified"]
    # il valore cambia CON la conferma nella stessa istruzione, anche con la stessa provenienza: resta
    _q(m, "UPDATE properties SET surface_source = 'declared' WHERE id = %s", (pid,))
    _q(m, "UPDATE properties SET surface_sqm = 72, surface_source = 'declared' WHERE id = %s", (pid,))
    assert _q(m, "SELECT surface_sqm, surface_source FROM properties WHERE id = %s", (pid,))[0] == [Decimal("72.00"), "declared"]
    # la conferma vale per la sua riga: un aggiornamento di piu' righe non la presta alle altre
    altra = _unita(m, building_id=e["id"], floor="2", surface_sqm="50")["id"]
    _q(m, "UPDATE properties SET surface_source = 'estimated' WHERE id = %s", (altra,))
    _q(m, "UPDATE properties SET surface_sqm = surface_sqm + 1, "
          "surface_source = CASE WHEN id = %s THEN surface_source ELSE surface_source END WHERE id = ANY(%s)",
       (pid, [pid, altra]))
    assert _q(m, "SELECT id, surface_source FROM properties WHERE id = ANY(%s) ORDER BY id", ([pid, altra],)) == [
        [pid, "declared"], [altra, "estimated"]]                                   # entrambe confermate nella SET
    _q(m, "UPDATE properties SET surface_sqm = surface_sqm + 1 WHERE id = ANY(%s)", ([pid, altra],))
    assert _q(m, "SELECT surface_source FROM properties WHERE id = ANY(%s)", ([pid, altra],)) == [[None], [None]]
    # dalla scheda dell'unita' (PATCH generica): «verificata» solo con la fonte nella stessa richiesta
    api = m["api"]()
    r = api.patch(f"/api/property/properties/{pid}", json={"surface_sqm": "80", "surface_source": "verified"})
    assert r.status_code == 422                                                    # nessuna fonte indicata
    r = api.patch(f"/api/property/properties/{pid}", json={"surface_sqm": "80", "surface_source": "verified",
                                                         "cadastral_evidence_type": "planimetria",
                                                         "cadastral_evidence_date": "2026-10-09",
                                                         "cadastral_evidence_ref": "prot. TE0012345"})
    assert r.status_code == 200 and r.json()["surface_source"] == "verified", r.text
    assert (r.json()["cadastral_evidence_type"], r.json()["cadastral_evidence_ref"]) == ("planimetria", "prot. TE0012345")
    assert api.patch(f"/api/property/properties/{pid}", json={"surface_source": "verified", "cadastral_evidence_type": "visura",
                                                            "cadastral_evidence_date": "2999-01-01"}).status_code == 422
    r = api.patch(f"/api/property/properties/{pid}", json={"rooms": 4, "internal_notes": "altro"})
    assert r.status_code == 200 and (r.json()["surface_source"], r.json()["cadastral_category_source"]) == ("verified", "verified")
    # togliere la fonte a una «verificata»: 400 leggibile, nessuna modifica
    r = api.patch(f"/api/property/properties/{pid}", json={"cadastral_evidence_date": None})
    assert (r.status_code, r.json()["code"]) == (400, "VERIFIED_NEEDS_CADASTRAL_EVIDENCE"), r.text
    # un riferimento catastale cambiato dalla scheda: «verificata» da riconfermare, le altre restano
    r = api.patch(f"/api/property/properties/{pid}", json={"cadastral_subunit": None})
    assert r.status_code == 200, r.text
    assert (r.json()["surface_source"], r.json()["cadastral_category_source"]) == (None, None)
    assert r.json()["cadastral_evidence_type"] == "planimetria"                   # storico della verifica
    # «verificata» su un'unita' senza identita' completa: 400; provenienza senza dato: 400; fuori elenco: 422
    r = api.patch(f"/api/property/properties/{altra}", json={"cadastral_category": "A/3", "cadastral_category_source": "verified",
                                                           "cadastral_evidence_type": "visura", "cadastral_evidence_date": "2026-10-01"})
    assert (r.status_code, r.json()["code"]) == (400, "VERIFIED_NEEDS_CADASTRAL_EVIDENCE")
    r = api.patch(f"/api/property/properties/{altra}", json={"cadastral_category_source": "declared"})
    assert (r.status_code, r.json()["code"]) == (400, "SOURCE_WITHOUT_VALUE")
    assert api.patch(f"/api/property/properties/{altra}", json={"surface_source": "misurata"}).status_code == 422
    _q(m, "UPDATE properties SET cadastral_category = 'A/2' WHERE id = %s", (pid,))
    # cancellare il dato cancella la sua provenienza (mai un CHECK violato da una PATCH)
    _q(m, "UPDATE properties SET cadastral_category_source = 'declared' WHERE id = %s", (pid,))
    r = api.patch(f"/api/property/properties/{pid}", json={"cadastral_category": None})
    assert r.status_code == 200, r.text
    assert _q(m, "SELECT cadastral_category, cadastral_category_source FROM properties WHERE id = %s", (pid,))[0] == [None, None]
    # composizione: mai negativa; coerente col totale solo con le quattro note
    with pytest.raises(pg.errors.CheckViolation):
        _q(m, "UPDATE buildings SET units_declared_other = -1 WHERE id = %s", (e["id"],))
    _q(m, "UPDATE buildings SET units_declared = 6, units_declared_residential = 4, units_declared_commercial = NULL "
          "WHERE id = %s", (e["id"],))                                     # bozza: nessun controllo di somma
    with pytest.raises(pg.errors.CheckViolation):
        _q(m, "UPDATE buildings SET units_declared_commercial = 1, units_declared_other = 0, units_declared_pertinenze = 0 "
              "WHERE id = %s", (e["id"],))
    # la PATCH generica dell'edificio non scavalca la coerenza: 400 leggibile, mai 500
    _q(m, "UPDATE buildings SET units_declared_commercial = 1, units_declared_other = 0, units_declared_pertinenze = 1 "
          "WHERE id = %s", (e["id"],))
    r = m["api"]().patch(f"/api/property/buildings/{e['id']}", json={"units_declared": 9})
    assert r.status_code == 400 and r.json()["code"] == "VALIDATION_ERROR", r.text
    assert _q(m, "SELECT units_declared FROM buildings WHERE id = %s", (e["id"],))[0][0] == 6


def test_02_composizione_bozza_totale_esplicito(mondo):
    m = mondo
    e = _edificio(m, units_declared=10)
    s = _stato(m, e["id"])
    assert s["completion"] == "draft" and s["composition"]["complete"] is False
    # bozza: «Non so» su alcune classi, sempre salvabile, totale invariato
    r = _composizione(m, e["id"], residential=8, commercial=None)
    assert r.status_code == 200, r.text
    assert r.json()["composition"]["residential"] == 8 and r.json()["completion"] == "draft"
    assert _q(m, "SELECT units_declared FROM buildings WHERE id = %s", (e["id"],))[0][0] == 10
    # quattro note, totale noto diverso: 409, nessuna correzione silenziosa
    r = _composizione(m, e["id"], residential=8, commercial=1, other=0, pertinenze=4)
    assert (r.status_code, r.json()["code"], r.json()["sum"]) == (409, "COMPOSITION_TOTAL_MISMATCH", 13)
    assert _q(m, "SELECT units_declared_commercial FROM buildings WHERE id = %s", (e["id"],))[0][0] is None
    # scelta esplicita: il totale diventa la somma
    r = _composizione(m, e["id"], residential=8, commercial=1, other=0, pertinenze=4, set_total=True)
    assert r.status_code == 200, r.text
    c = r.json()["composition"]
    assert (c["sum"], c["units_declared"], c["total_matches"], c["complete"]) == (13, 13, True, True)
    assert r.json()["completion"] == "composition_declared"
    # set_total senza le quattro classi: 422
    assert _composizione(m, e["id"], residential=8, set_total=True).status_code == 422
    # la scheda edificio di sempre legge il totale nuovo
    assert m["api"]().get(f"/api/property/buildings/{e['id']}").json()["census_summary"]["units_declared"] == 13


def test_03_anteprima_in_sola_lettura_e_composizione_obbligatoria(mondo):
    m = mondo
    e = _edificio(m, units_declared=6)
    gruppi = [{"unit_class": "residential", "count": 4, "property_type": "apartment"}]
    r = _genera(m, e["id"], gruppi)
    assert (r.status_code, r.json()["code"]) == (409, "COMPOSITION_REQUIRED")
    _composizione(m, e["id"], residential=4, commercial=0, other=0, pertinenze=2, set_total=True)
    prima = _n(m, e["id"])
    r = _genera(m, e["id"], gruppi)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["dry_run"] is True and a["existing"] == {c: 0 for c in CLASSI}
    assert a["plan"]["residential"] == {"declared": 4, "present": 0, "missing": 4, "requested": 4, "over_declared": 0}
    assert _n(m, e["id"]) == prima == 0
    # la conferma senza i conteggi dell'anteprima e' rifiutata dallo schema
    r = _genera(m, e["id"], gruppi, dry_run=False)
    assert r.status_code == 422


def test_04_conferma_crea_schede_census_corrette(mondo):
    m = mondo
    e = _edificio_composto(m, res=4, com=1, oth=1, per=4)
    gruppi = [
        {"unit_class": "residential", "count": 4, "property_type": "apartment", "surface_sqm": "65",
         "surface_source": "estimated", "floor": "1", "numbering_start": 1},
        {"unit_class": "commercial", "count": 1, "property_type": "commercial", "cadastral_category": "c/1",
         "cadastral_category_source": "declared"},
        {"unit_class": "other", "count": 1, "property_type": "garage"},
        {"unit_class": "pertinenze", "count": 2, "property_type": "storage", "pertinenza_kind": "cantina"},
        {"unit_class": "pertinenze", "count": 2, "property_type": "garage", "pertinenza_kind": "box"},
    ]
    r, chiave = _conferma(m, e["id"], gruppi)
    assert r.status_code == 200, r.text
    esito = r.json()
    assert esito["created"] == 10 and esito["replica"] is False and esito["batch_key"] == str(chiave)
    righe = _q(m, "SELECT record_kind, commercial_status, is_pertinenza, parent_property_id, cadastral_category, "
                  "cadastral_category_source, surface_sqm, surface_source, internal_number, address_inherited, "
                  "address, code, property_type, pertinenza_kind, metadata->'census_batch'->>'provisional_numbering' "
                  "FROM properties WHERE building_id = %s ORDER BY id", (e["id"],))
    assert len(righe) == 10
    for riga in righe:
        assert riga[0:2] == ["census", "draft"]                       # mai commerciali, mai attive
        assert riga[3] is None                                        # nessun collegamento inventato
        assert riga[9] is True and riga[10] == "Via Roma"             # indirizzo ereditato
        assert riga[11].startswith("IMM-")
    appartamenti = [r for r in righe if r[12] == "apartment"]
    assert [r[8] for r in appartamenti] == ["1", "2", "3", "4"] and all(r[14] == "true" for r in appartamenti)
    assert all((r[6], r[7]) == (Decimal("65.00"), "estimated") for r in appartamenti)
    assert all(r[4] is None and r[5] is None for r in righe if r[12] != "commercial")    # nessuna categoria dedotta
    negozio = next(r for r in righe if r[12] == "commercial")
    assert (negozio[4], negozio[5], negozio[8]) == ("C/1", "declared", None)
    pertinenze = [r for r in righe if r[2]]
    assert sorted(r[13] for r in pertinenze) == ["box", "box", "cantina", "cantina"]
    garage = next(r for r in righe if r[12] == "garage" and not r[2])
    assert garage[13] is None                                         # box autonomo: non pertinenza
    # due indicatori separati
    s = _stato(m, e["id"])
    assert s["units_created"] == {"declared": 10, "present": 10, "to_create": 0, "complete": True}
    assert s["data_completeness"]["units"] == 10 and s["data_completeness"]["complete"] == 0
    assert s["completion"] == "units_created"
    assert {c: s["by_class"][c]["present"] for c in CLASSI} == {"residential": 4, "commercial": 1, "other": 1,
                                                                "pertinenze": 4}
    # la scheda edificio di sempre: 6 principali + 4 pertinenze da collegare
    cs = m["api"]().get(f"/api/property/buildings/{e['id']}").json()["census_summary"]
    assert (cs["units_main"], cs["units_pertinenze"], cs["units_pertinenze_unlinked"], cs["units_to_complete"]) == (6, 4, 4, 0)
    # ogni scheda resta modificabile singolarmente
    pid = next(u["id"] for u in esito["units"] if u["unit_class"] == "residential")
    r = m["api"]().patch(f"/api/property/properties/{pid}", json={"surface_sqm": "71", "rooms": 3})
    assert r.status_code == 200, r.text
    assert _q(m, "SELECT surface_sqm, surface_source, rooms FROM properties WHERE id = %s", (pid,))[0] == [
        Decimal("71.00"), None, 3]
    # e le pertinenze si collegano dopo, con la rotta di sempre
    cantina = next(u["id"] for u in esito["units"] if u["unit_class"] == "pertinenze")
    r = m["api"]().post(f"/api/property/properties/{pid}/pertinenze/link", json={"pertinenza_id": cantina})
    assert r.status_code == 200 and r.json()["linked"] is True
    # gli interni: pertinenze reali, fonte catastale, catalogo per la Shell (nessuna lista nel browser)
    s = _stato(m, e["id"])
    righe = {u["id"]: u for u in s["units"]}
    assert righe[pid]["pertinenze_count"] == 1 and righe[pid]["parent"] is None
    assert righe[cantina]["parent"] == {"id": pid, "code": righe[pid]["code"], "same_building": True}
    assert righe[pid]["cadastral_source"] is False and "cadastral_sheet" not in righe[pid]
    assert [c["value"] for c in s["catalog"]["classes"]] == ["residential", "commercial", "other", "pertinenze"]
    assert [x["value"] for x in s["catalog"]["data_sources"] if x["bulk"]] == ["estimated", "declared"]
    assert s["catalog"]["max_batch_units"] == 300


def test_05_edificio_esistente_solo_la_differenza_e_idempotenza(mondo):
    m = mondo
    e = _edificio(m, units_declared=None)
    a1 = _unita(m, building_id=e["id"], floor="1", internal_number="1", cadastral_category="A/3")
    a2 = _unita(m, building_id=e["id"], floor="2", internal_number="2")
    cant = _unita(m, building_id=e["id"], is_pertinenza=True, pertinenza_kind="cantina", property_type="storage")
    prima = _q(m, "SELECT id, updated_at, cadastral_category, cadastral_category_source FROM properties "
                  "WHERE building_id = %s ORDER BY id", (e["id"],))
    assert _composizione(m, e["id"], residential=5, commercial=0, other=0, pertinenze=2, set_total=True).status_code == 200
    gruppi = [{"unit_class": "residential", "count": 3, "property_type": "apartment"},
              {"unit_class": "pertinenze", "count": 1, "property_type": "storage", "pertinenza_kind": "cantina"}]
    a = _genera(m, e["id"], gruppi).json()
    assert a["existing"] == {"residential": 2, "commercial": 0, "other": 0, "pertinenze": 1}
    assert a["plan"]["residential"]["missing"] == 3 and a["plan"]["pertinenze"]["missing"] == 1
    chiave = uuid.uuid4()
    r = _genera(m, e["id"], gruppi, chiave=chiave, dry_run=False, expected_existing=a["existing"])
    assert r.status_code == 200 and r.json()["created"] == 4, r.text
    ids = [u["id"] for u in r.json()["units"]]
    # le unita' esistenti non sono state toccate
    assert _q(m, "SELECT id, updated_at, cadastral_category, cadastral_category_source FROM properties "
                 "WHERE id = ANY(%s) ORDER BY id", ([a1["id"], a2["id"], cant["id"]],)) == prima
    # stessa richiesta ripetuta (doppio tocco, rete): stesse righe, nessun doppione
    r2 = _genera(m, e["id"], gruppi, chiave=chiave, dry_run=False, expected_existing=a["existing"])
    assert r2.status_code == 200 and r2.json()["replica"] is True
    assert [u["id"] for u in r2.json()["units"]] == ids and _n(m, e["id"]) == 7
    # stessa chiave, contenuto diverso: 409
    r3 = _genera(m, e["id"], [{**gruppi[0], "count": 2}], chiave=chiave, dry_run=False, expected_existing=a["existing"])
    assert (r3.status_code, r3.json()["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
    # un nuovo lotto con i conteggi vecchi dell'anteprima: 409, nessuna scrittura
    r4 = _genera(m, e["id"], [gruppi[0]], dry_run=False, expected_existing=a["existing"])
    assert (r4.status_code, r4.json()["code"]) == (409, "UNITS_CHANGED")
    assert r4.json()["existing"]["residential"] == 5 and _n(m, e["id"]) == 7
    s = _stato(m, e["id"])
    assert s["units_created"]["to_create"] == 0 and s["completion"] == "units_created"


def test_06_concorrenza_due_lotti_e_stesso_lotto(mondo):
    m = mondo
    e = _edificio_composto(m, res=4, com=0, oth=0, per=0)
    gruppi = [{"unit_class": "residential", "count": 4, "property_type": "apartment"}]
    attese = {c: 0 for c in CLASSI}
    # due operatori, due lotti diversi sugli stessi conteggi: uno solo passa
    esiti = _in_parallelo([
        lambda: _genera(m, e["id"], gruppi, dry_run=False, expected_existing=attese),
        lambda: _genera(m, e["id"], gruppi, chi="agent_a", dry_run=False, expected_existing=attese)])
    assert sorted(x.status_code for x in esiti) == [200, 409]
    assert next(x for x in esiti if x.status_code == 409).json()["code"] == "UNITS_CHANGED"
    assert _n(m, e["id"]) == 4
    # lo stesso lotto inviato due volte insieme: una sola serie di unita'
    e2 = _edificio_composto(m, res=3, com=0, oth=0, per=0, address="Via Verdi", name="Verdi")
    chiave = uuid.uuid4()
    gruppi2 = [{"unit_class": "residential", "count": 3, "property_type": "apartment"}]
    esiti = _in_parallelo([
        lambda: _genera(m, e2["id"], gruppi2, chiave=chiave, dry_run=False, expected_existing=attese),
        lambda: _genera(m, e2["id"], gruppi2, chiave=chiave, dry_run=False, expected_existing=attese)])
    assert [x.status_code for x in esiti] == [200, 200], [x.text for x in esiti]
    assert sorted(x.json()["replica"] for x in esiti) == [False, True]
    assert esiti[0].json()["units"] == esiti[1].json()["units"] and _n(m, e2["id"]) == 3


def test_07_eccedenza_solo_con_conferma_esplicita(mondo):
    m = mondo
    e = _edificio_composto(m, res=2, com=0, oth=0, per=0)
    gruppi = [{"unit_class": "residential", "count": 3, "property_type": "apartment"}]
    a = _genera(m, e["id"], gruppi).json()
    assert a["plan"]["residential"]["over_declared"] == 1
    r = _genera(m, e["id"], gruppi, dry_run=False, expected_existing=a["existing"])
    assert (r.status_code, r.json()["code"]) == (409, "OVER_DECLARED") and _n(m, e["id"]) == 0
    r = _genera(m, e["id"], gruppi, dry_run=False, expected_existing=a["existing"], confirm_over_declared=True)
    assert r.status_code == 200 and r.json()["created"] == 3
    s = _stato(m, e["id"])
    assert s["by_class"]["residential"]["over_declared"] == 1          # segnalata, mai corretta
    assert m["api"]().get(f"/api/property/buildings/{e['id']}").json()["census_summary"]["units_over_declared"] == 1


def test_08_agenzie_cestino_e_validazione(mondo):
    m = mondo
    e = _edificio_composto(m, res=2, com=0, oth=0, per=0)
    gruppi = [{"unit_class": "residential", "count": 2, "property_type": "apartment"}]
    attese = {c: 0 for c in CLASSI}
    # un'altra agenzia: l'edificio non esiste (404), nessuna scrittura
    for r in (m["api"]("owner_b").get(f"/api/property/buildings/{e['id']}/composition"),
              _composizione(m, e["id"], chi="owner_b", residential=1),
              _genera(m, e["id"], gruppi, chi="owner_b"),
              _genera(m, e["id"], gruppi, chi="owner_b", dry_run=False, expected_existing=attese),
              m["api"]("owner_b").post(f"/api/property/buildings/{e['id']}/units/generate/{uuid.uuid4()}/undo")):
        assert r.status_code == 404, r.text
    assert _n(m, e["id"]) == 0
    # un'unita' di un altro edificio (o di un'altra agenzia) non si modifica da qui
    altro = _edificio(m, address="Via Altra", name="Altra")
    u = _unita(m, building_id=altro["id"], floor="1")
    r = m["api"]().post(f"/api/property/buildings/{e['id']}/units/bulk-update",
                        json={"property_ids": [u["id"]], "changes": {"floor": "2"}})
    assert r.status_code == 404
    eb = _edificio(m, chi="owner_b", address="Via B")
    ub = _unita(m, chi="owner_b", building_id=eb["id"], floor="1")
    r = m["api"]().post(f"/api/property/buildings/{eb['id']}/units/bulk-update",
                        json={"property_ids": [ub["id"]], "changes": {"floor": "2"}})
    assert r.status_code == 404
    assert _q(m, "SELECT floor FROM properties WHERE id = %s", (ub["id"],))[0][0] == "1"
    # validazione: «verificata» in blocco, tipologia fuori classe, oltre 300, campi del server
    def corpo(**g):
        return [{"unit_class": "residential", "count": 2, "property_type": "apartment", **g}]
    for gruppi_errati in (corpo(cadastral_category="A/2", cadastral_category_source="verified"),
                          corpo(surface_sqm="60"), corpo(property_type="garage"),
                          corpo(pertinenza_kind="cantina"), corpo(count=301),
                          [{"unit_class": "pertinenze", "count": 1, "property_type": "storage", "pertinenza_kind": "box"}],
                          corpo(agency_id=2), corpo(record_kind="crm"), corpo(parent_property_id=1)):
        assert _genera(m, e["id"], gruppi_errati).status_code == 422, gruppi_errati
    assert _genera(m, e["id"], corpo(count=200) + corpo(count=101)).status_code == 422
    assert _genera(m, e["id"], corpo(cadastral_category="Z/9", cadastral_category_source="declared")).status_code == 400
    # edificio nel Cestino: 409 leggibile
    vuoto = _edificio(m, address="Via Vuota", name="Vuota")
    r = m["api"]().post(f"/api/property/buildings/{vuoto['id']}/trash", json={"reason_code": "duplicate"})
    assert r.status_code == 200, r.text
    r = _composizione(m, vuoto["id"], residential=1)
    assert (r.status_code, r.json()["code"]) == (409, "BUILDING_IN_TRASH")


def test_09_annullamento_del_lotto(mondo):
    m = mondo
    e = _edificio_composto(m, res=3, com=0, oth=0, per=1)
    gruppi = [{"unit_class": "residential", "count": 3, "property_type": "apartment"},
              {"unit_class": "pertinenze", "count": 1, "property_type": "storage"}]
    r, chiave = _conferma(m, e["id"], gruppi)
    ids = [u["id"] for u in r.json()["units"]]
    # una modificata, una con un proprietario: restano
    assert m["api"]().patch(f"/api/property/properties/{ids[0]}", json={"rooms": 4}).status_code == 200
    assert m["api"]().post(f"/api/property/properties/{ids[1]}/contacts",
                           json={"contact_id": m["mario"], "role": "owner"}).status_code == 201
    r = m["api"]().post(f"/api/property/buildings/{e['id']}/units/generate/{chiave}/undo")
    assert r.status_code == 200, r.text
    u = r.json()
    assert sorted(u["undone"]) == sorted(ids[2:])
    assert {x["id"]: x["reason"] for x in u["not_undone"]} == {ids[0]: "modified", ids[1]: "linked"}
    # le annullate non contano (regola di «Annulla»): si possono ricreare
    s = _stato(m, e["id"])
    assert s["by_class"]["residential"]["present"] == 2 and s["by_class"]["pertinenze"]["present"] == 0
    assert m["api"]().get(f"/api/property/buildings/{e['id']}").json()["census_summary"]["units_counted"] == 2
    # ripetere il lotto annullato non lo ricrea (replica)
    r = _genera(m, e["id"], gruppi, chiave=chiave, dry_run=False, expected_existing={c: 0 for c in CLASSI})
    assert r.status_code == 200 and r.json()["replica"] is True and _n(m, e["id"]) == 4
    # ripetere l'annullamento: nulla di nuovo
    u2 = m["api"]().post(f"/api/property/buildings/{e['id']}/units/generate/{chiave}/undo").json()
    assert u2["undone"] == [] and sorted(u2["already_archived"]) == sorted(ids[2:])
    assert m["api"]().post(f"/api/property/buildings/{e['id']}/units/generate/{uuid.uuid4()}/undo").status_code == 404
    assert m["api"]().post(f"/api/property/buildings/{e['id']}/units/generate/non-uuid/undo").status_code == 400


def test_10_modifica_di_gruppo(mondo):
    m = mondo
    e = _edificio_composto(m, res=3, com=0, oth=0, per=0)
    r, _ = _conferma(m, e["id"], [{"unit_class": "residential", "count": 3, "property_type": "apartment",
                                   "surface_sqm": "60", "surface_source": "estimated"}])
    ids = [u["id"] for u in r.json()["units"]]
    url = f"/api/property/buildings/{e['id']}/units/bulk-update"
    api = m["api"]()
    # elenco chiuso di campi
    for vietato in ({"commercial_status": "available"}, {"record_kind": "crm"}, {"parent_property_id": ids[0]},
                    {"building_id": 1}, {"cadastral_subunit": "3"}, {}):
        assert api.post(url, json={"property_ids": ids, "changes": vietato}).status_code == 422, vietato
    # categoria e provenienza dichiarata su tutte, piano e scala: atomico, con storico
    r = api.post(url, json={"property_ids": ids, "changes": {"cadastral_category": "a/2",
                                                              "cadastral_category_source": "declared",
                                                              "floor": "2", "staircase": "B"}})
    assert r.status_code == 200 and r.json()["updated"] == 3, r.text
    assert _q(m, "SELECT DISTINCT cadastral_category, cadastral_category_source, floor, staircase, surface_source "
                 "FROM properties WHERE id = ANY(%s)", (ids,)) == [["A/2", "declared", "2", "B", "estimated"]]
    storia = _q(m, "SELECT property_id, description FROM activities WHERE property_id = ANY(%s) ORDER BY id", (ids,))
    assert len(storia) == 3 and all(d.startswith("Modifica di gruppo") for _, d in storia)
    # «verificata» mai dalla modifica di gruppo, neanche per una sola unita': si fa dalla scheda con la fonte
    for gruppo in (ids, [ids[0]]):
        r = api.post(url, json={"property_ids": gruppo, "changes": {"cadastral_category_source": "verified"}})
        assert (r.status_code, r.json()["code"]) == (400, "VERIFIED_NOT_IN_BULK")
    # una unita' non valida = nessuna modifica (atomicita')
    _q(m, "UPDATE properties SET surface_sqm = NULL WHERE id = %s", (ids[2],))
    r = api.post(url, json={"property_ids": ids, "changes": {"surface_source": "declared"}})
    assert (r.status_code, r.json()["code"], r.json()["property_id"]) == (400, "SOURCE_WITHOUT_VALUE", ids[2])
    assert _q(m, "SELECT surface_source FROM properties WHERE id = %s", (ids[0],))[0][0] == "estimated"
    # valore nuovo con la stessa provenienza di prima: la provenienza resta quella indicata
    r = api.post(url, json={"property_ids": ids, "changes": {"surface_sqm": "62", "surface_source": "estimated"}})
    assert r.status_code == 200
    assert _q(m, "SELECT DISTINCT surface_sqm, surface_source FROM properties WHERE id = ANY(%s)", (ids,)) == [
        [Decimal("62.00"), "estimated"]]
    # valore nuovo senza provenienza: «non indicata»
    api.post(url, json={"property_ids": ids, "changes": {"surface_sqm": "64"}})
    assert _q(m, "SELECT DISTINCT surface_source FROM properties WHERE id = ANY(%s)", (ids,)) == [[None]]
    # una scheda commerciale non assegnata: l'agente non la modifica (regola di gestione)
    crm = _unita(m, building_id=e["id"], floor="3", record_kind="crm")
    r = m["api"]("agent_a").post(url, json={"property_ids": [crm["id"]], "changes": {"floor": "4"}})
    assert r.status_code == 403
    assert m["api"]("agent_a").post(url, json={"property_ids": ids, "changes": {"floor": "4"}}).status_code == 200
    # una scheda archiviata: 409
    _q(m, "UPDATE properties SET archived_at = NOW(), commercial_status = 'archived' WHERE id = %s", (ids[1],))
    r = api.post(url, json={"property_ids": ids, "changes": {"floor": "5"}})
    assert (r.status_code, r.json()["code"]) == (409, "UNIT_ARCHIVED")


def test_11_classi_e_compatibilita(mondo):
    m = mondo
    e = _edificio(m, units_declared=None)
    rustico = _unita(m, building_id=e["id"], property_type="rustic")
    box = _unita(m, building_id=e["id"], property_type="garage")
    cantina = _unita(m, building_id=e["id"], property_type="storage")
    s = _stato(m, e["id"])
    assert {c: s["by_class"][c]["present"] for c in CLASSI} == {"residential": 0, "commercial": 0, "other": 3,
                                                                "pertinenze": 0}
    # rustico abitazione solo su scelta esplicita
    url = f"/api/property/buildings/{e['id']}/units/bulk-update"
    r = m["api"]().post(url, json={"property_ids": [rustico["id"]], "changes": {"census_class": "residential"}})
    assert r.status_code == 200, r.text
    r = m["api"]().post(url, json={"property_ids": [box["id"]], "changes": {"census_class": "residential"}})
    assert (r.status_code, r.json()["code"]) == (400, "CLASS_NOT_APPLICABLE")
    s = _stato(m, e["id"])
    assert (s["by_class"]["residential"]["present"], s["by_class"]["other"]["present"]) == (1, 2)
    # generato come abitazione: classificato esplicitamente; come «altra»: no
    _composizione(m, e["id"], residential=2, commercial=0, other=3, pertinenze=0, set_total=True)
    r, _ = _conferma(m, e["id"], [{"unit_class": "residential", "count": 1, "property_type": "rustic"}])
    assert r.status_code == 200, r.text
    assert _stato(m, e["id"])["by_class"]["residential"]["present"] == 2
    # box e cantina autonomi restano «altre» finche' non diventano pertinenze
    assert _q(m, "SELECT is_pertinenza FROM properties WHERE id = ANY(%s)", ([box["id"], cantina["id"]],)) == [[False], [False]]
    # compatibilita': fuori dagli elenchi commerciali, presa in carico di sempre
    pid = r.json()["units"][0]["id"]
    elenco = m["api"]().get("/api/property/properties?limit=200").json()["items"]
    assert pid not in [x["id"] for x in elenco]
    assert pid in [x["id"] for x in m["api"]().get("/api/property/properties?record_kind=census&limit=200").json()["items"]]
    r = m["api"]().post(f"/api/property/properties/{pid}/take-in-charge", json={"include_pertinenze": False})
    assert r.status_code == 200 and r.json()["record_kind"] == "crm"


def test_12_prestazioni_lotto_di_300(mondo):
    m = mondo
    e = _edificio_composto(m, res=200, com=20, oth=20, per=60)
    gruppi = [{"unit_class": "residential", "count": 200, "property_type": "apartment", "numbering_start": 1},
              {"unit_class": "commercial", "count": 20, "property_type": "office"},
              {"unit_class": "other", "count": 20, "property_type": "garage"},
              {"unit_class": "pertinenze", "count": 60, "property_type": "storage", "pertinenza_kind": "cantina"}]
    t0 = time.monotonic()
    r, chiave = _conferma(m, e["id"], gruppi)
    durata = time.monotonic() - t0
    assert r.status_code == 200 and r.json()["created"] == 300, r.text
    assert _n(m, e["id"]) == 300
    t0 = time.monotonic()
    s = _stato(m, e["id"])
    lettura = time.monotonic() - t0
    assert s["units_created"]["complete"] is True and len(s["units"]) == 300
    t0 = time.monotonic()
    u = m["api"]().post(f"/api/property/buildings/{e['id']}/units/generate/{chiave}/undo")
    annullo = time.monotonic() - t0
    assert u.status_code == 200 and len(u.json()["undone"]) == 300
    print(f"\nCENSIMENTO-2 prestazioni: conferma 300 = {durata:.2f}s, riepilogo = {lettura:.2f}s, annullo = {annullo:.2f}s")
    assert durata < 30 and lettura < 5 and annullo < 30


def test_13_completezza_senza_piano_verifica_separata_e_composizione_obbligatoria(mondo):
    """Precisazioni finali: (1) piano, scala e interno non servono alla
    completezza; (2-3) la verifica e' un indicatore SEPARATO, «da
    riconfermare» se cambiano i riferimenti; (5) senza composizione l'edificio
    resta in bozza anche con tutte le schede complete, e la bozza si salva."""
    m = mondo
    api = m["api"]()
    e = _edificio(m, units_declared=None)                                   # bozza: nessun totale, nessuna composizione
    u = _unita(m, building_id=e["id"], surface_sqm="80", cadastral_category="A/3")   # nessun piano, scala, interno
    s = _stato(m, e["id"])
    riga = next(x for x in s["units"] if x["id"] == u["id"])
    assert riga["missing"] == ["surface_source", "cadastral_category_source"]
    assert "floor" not in [x["value"] for x in s["catalog"]["minimum_data"]]
    assert api.patch(f"/api/property/properties/{u['id']}", json={"surface_source": "estimated",
                                                                 "cadastral_category_source": "declared"}).status_code == 200
    s = _stato(m, e["id"])
    riga = next(x for x in s["units"] if x["id"] == u["id"])
    assert riga["missing"] == [] and riga["verification"] is None and s["data_completeness"]["complete"] == 1
    # schede complete ma composizione assente: l'edificio NON e' completo, resta in bozza
    assert s["completion"] == "draft" and s["composition"]["complete"] is False
    # bozza parziale salvabile, sempre in bozza; con la composizione completa e coerente: completo
    assert _composizione(m, e["id"], residential=1).json()["completion"] == "draft"
    r = _composizione(m, e["id"], residential=1, commercial=0, other=0, pertinenze=0, set_total=True)
    assert r.status_code == 200 and r.json()["completion"] == "data_complete", r.text
    # la verifica: indicatore separato dalla completezza
    assert api.patch(f"/api/property/properties/{u['id']}", json={
        "cadastral_municipality_code": "L307", "cadastral_sheet": "5", "cadastral_parcel": "61", "cadastral_subunit": "2",
        "cadastral_category_source": "verified", "cadastral_evidence_type": "visura",
        "cadastral_evidence_date": "2026-10-10"}).status_code == 200
    riga = next(x for x in _stato(m, e["id"])["units"] if x["id"] == u["id"])
    assert riga["verification"] == "verified" and riga["missing"] == []
    assert api.patch(f"/api/property/properties/{u['id']}", json={"cadastral_parcel": "62"}).status_code == 200
    s = _stato(m, e["id"])
    riga = next(x for x in s["units"] if x["id"] == u["id"])
    assert riga["verification"] == "to_reconfirm" and riga["cadastral_category_source"] is None
    assert riga["surface_source"] == "estimated"                             # non dipende dalla verifica
    assert riga["missing"] == ["cadastral_category_source"] and s["completion"] == "units_created"
    assert [x["value"] for x in s["catalog"]["evidence_types"]] == ["visura", "planimetria", "atto", "altro"]
    # l'identita' della verifica la scrive solo il database; la modifica di gruppo non tocca la fonte
    r = api.patch(f"/api/property/properties/{u['id']}", json={"cadastral_evidence_identity": "L307||5|62|2"})
    assert r.status_code == 422
    r = api.post(f"/api/property/buildings/{e['id']}/units/bulk-update",
                 json={"property_ids": [u["id"]], "changes": {"cadastral_evidence_type": "visura"}})
    assert r.status_code == 422
    # un'altra agenzia non legge ne' scrive la fonte
    assert m["api"]("owner_b").patch(f"/api/property/properties/{u['id']}", json={
        "cadastral_evidence_type": "atto", "cadastral_evidence_date": "2026-10-10"}).status_code == 404
    assert _q(m, "SELECT cadastral_evidence_type FROM properties WHERE id = %s", (u["id"],))[0][0] == "visura"


def test_99_down_e_up_e_codice_senza_la_100(mondo):
    m = mondo
    su = (MIGRAZIONI / "100_censimento_2_composition.sql").read_text(encoding="utf-8")
    giu = (MIGRAZIONI / "100_censimento_2_composition_down.sql").read_text(encoding="utf-8")
    e = _edificio_composto(m, res=1, com=0, oth=0, per=0)
    with m["conn"].cursor() as cur:
        with pytest.raises(Exception, match="100 down"):
            cur.execute(giu)
        cur.execute("ROLLBACK")
    _q(m, "UPDATE buildings SET units_declared_residential = NULL, units_declared_commercial = NULL, "
          "units_declared_other = NULL, units_declared_pertinenze = NULL")
    _q(m, giu)
    try:
        assert _q(m, "SELECT count(*) FROM information_schema.columns WHERE column_name IN "
                     "('surface_source', 'cadastral_category_source', 'units_declared_residential')")[0][0] == 0
        r = m["api"]().get(f"/api/property/buildings/{e['id']}/composition")
        assert (r.status_code, r.json()["code"]) == (503, "COMPOSITION_NOT_INSTALLED")
        # il resto del censimento funziona come prima
        assert m["api"]().get(f"/api/property/buildings/{e['id']}").status_code == 200
        u = _unita(m, building_id=e["id"], floor="1", surface_sqm="50")
        assert m["api"]().patch(f"/api/property/properties/{u['id']}", json={"surface_sqm": "55"}).status_code == 200
    finally:
        with m["conn"].cursor() as cur:
            cur.execute("BEGIN")
            cur.execute(su)
            cur.execute("COMMIT")
    assert _stato(m, e["id"])["composition"]["complete"] is False
