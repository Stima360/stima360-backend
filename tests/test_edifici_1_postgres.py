"""EDIFICI-1 - la sezione Edifici: lista, scheda e contatori su PostgreSQL VERO.

Schema completo e router veri (fixture `completo`/`mondo` della Fase 3 del
censimento). Si prova:

  01  lista: Comune e Microzona (filtri esatti), ricerca per parole su via,
      civico e nome, ordinamento per indirizzo, totale e paginazione;
  02  la lista aggrega: un numero fisso di query per pagina, mai una per
      edificio;
  03  edificio vuoto: dichiarate note / NON note / zero sono tre cose diverse;
  04  edificio con piu' unita', pertinenze e accessori: gli accessori non
      contano, nessuna unita' contata due volte, scale dalle unita';
  05  Cestino (non conta, relazioni conservate, il ripristino le ritrova),
      archiviate (restano censite, a parte) e «Annulla» della creazione;
  06  censite oltre le dichiarate: eccedenza segnalata, mai negativi;
  07  pertinenze per relazione reale: una pertinenza appartiene a un edificio
      solo con `building_id`, mai per indirizzo;
  08  immobili autonomi (senza edificio) e scheda immobile con l'edificio;
  09  permessi e separazione fra agenzie (lista, scheda, accesso diretto).
"""
from __future__ import annotations

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _edificio, _q, _unita, completo, mondo,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

BASE = "/api/property/buildings"


def _lista(m, chi="owner_a", **params):
    r = m["api"](chi).get(BASE, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _scheda(m, bid, chi="owner_a"):
    r = m["api"](chi).get(f"{BASE}/{bid}")
    assert r.status_code == 200, r.text
    return r.json()


def test_01_filtri_ricerca_ordinamento_e_paginazione(mondo):
    m = mondo
    a = _edificio(m, region="Abruzzo", province="TE", city="Tortoreto", microzone="Lido Nord",
                  address="Via Roma", civic_number="10", name="Residenza Gabbiano")
    b = _edificio(m, region="Abruzzo", province="TE", city="Tortoreto", microzone="Alto",
                  address="Via Roma", civic_number="2", name=None, confirm_similar=True)
    c = _edificio(m, region="Abruzzo", province="TE", city="Alba Adriatica", microzone="Nord",
                  address="Via Nazario Sauro", civic_number="5", name="Condominio Sole")
    assert {e["id"] for e in _lista(m)["items"]} == {a["id"], b["id"], c["id"]}
    # Comune, poi Comune + Microzona (valori del catalogo)
    assert {e["id"] for e in _lista(m, city="Tortoreto")["items"]} == {a["id"], b["id"]}
    assert [e["id"] for e in _lista(m, city="Tortoreto", microzone="Alto")["items"]] == [b["id"]]
    assert _lista(m, city="Alba Adriatica", microzone="Alto") == {"items": [], "total": 0, "limit": 50, "offset": 0}
    # ricerca per parole: via + civico, nome, parola parziale; nessun pattern SQL dal client
    assert [e["id"] for e in _lista(m, search="roma 10")["items"]] == [a["id"]]
    assert [e["id"] for e in _lista(m, search="gabbiano")["items"]] == [a["id"]]
    assert {e["id"] for e in _lista(m, search="sauro")["items"]} == {c["id"]}
    assert _lista(m, search="%")["total"] == 0 and _lista(m, search="_")["total"] == 0
    # ordinamento per indirizzo: comune, via, civico NUMERICO (2 prima di 10)
    assert [e["id"] for e in _lista(m, sort="address")["items"]] == [c["id"], b["id"], a["id"]]
    assert m["api"]().get(BASE, params={"sort": "x"}).status_code == 422
    # totale e paginazione
    pagina = _lista(m, sort="address", limit=2, offset=0)
    assert pagina["total"] == 3 and len(pagina["items"]) == 2
    seconda = _lista(m, sort="address", limit=2, offset=2)
    assert seconda["total"] == 3 and [e["id"] for e in seconda["items"]] == [a["id"]]


def test_02_la_lista_aggrega_senza_una_query_per_edificio(mondo, monkeypatch):
    m = mondo
    for i in range(12):
        e = _edificio(m, address=f"Via Conta {i}", civic_number=str(i), name=f"Edificio {i}", units_declared=2)
        _unita(m, building_id=e["id"], floor="1")
    from property import census
    contate = []
    originale = census.core_cursor

    class _Cur:
        def __init__(self, cur):
            self._cur = cur

        def execute(self, *a, **k):
            contate.append(a[0])
            return self._cur.execute(*a, **k)

        def __getattr__(self, nome):
            return getattr(self._cur, nome)

    from contextlib import contextmanager

    @contextmanager
    def conta(*a, **k):
        with originale(*a, **k) as (conn, cur):
            yield conn, _Cur(cur)

    monkeypatch.setattr(census, "core_cursor", conta)
    corpo = _lista(m, limit=50)
    assert corpo["total"] == 12 and all(e["census_summary"]["units_counted"] == 1 for e in corpo["items"])
    assert len(contate) <= 5, len(contate)        # 083 + totale + pagina + 2 aggregati


def test_03_edificio_vuoto_dichiarate_note_ignote_e_zero(mondo):
    m = mondo
    sei = _edificio(m)
    r = _scheda(m, sei["id"])
    assert r["units"] == [] and r["archived_units"] == [] and r["staircases"] == []
    s = r["census_summary"]
    assert (s["units_declared"], s["units_counted"], s["units_to_complete"], s["units_over_declared"]) == (6, 0, 6, 0)
    ignote = _edificio(m, address="Via Ignota", civic_number="1", units_declared=None, units_declared_source=None)
    s = _scheda(m, ignote["id"])["census_summary"]
    assert s["units_declared"] is None and s["units_declared_known"] is False and s["units_to_complete"] is None
    zero = _edificio(m, address="Via Zero", civic_number="1", units_declared=0)
    s = _scheda(m, zero["id"])["census_summary"]
    assert s["units_declared"] == 0 and s["units_declared_known"] is True and s["units_to_complete"] == 0
    # stesso riepilogo in lista e in scheda
    in_lista = {e["id"]: e["census_summary"] for e in _lista(m)["items"]}
    assert in_lista[sei["id"]] == _scheda(m, sei["id"])["census_summary"]


def test_04_piu_unita_pertinenze_e_accessori(mondo):
    m = mondo
    e = _edificio(m, units_declared=5)
    u1 = _unita(m, building_id=e["id"], floor="1", internal_number="1", staircase="A", surface_sqm=80)
    u2 = _unita(m, building_id=e["id"], floor="1", internal_number="2", staircase="B", cadastral_category="A/2")
    u3 = _unita(m, building_id=e["id"], floor="2", internal_number="3", staircase="A")
    garage = _unita(m, building_id=e["id"], parent_property_id=u1["id"], property_type="garage", floor="-1")
    # accessori: compreso e «da chiarire» - non sono unita'
    api = m["api"]()
    assert api.post(f"/api/property/properties/{u2['id']}/accessories", json={"kind": "cantina", "cadastral_status": "included"}).status_code == 201
    assert api.post(f"/api/property/properties/{u3['id']}/accessories", json={"kind": "soffitta", "cadastral_status": "unknown"}).status_code == 201
    r = _scheda(m, e["id"])
    s = r["census_summary"]
    assert (s["units_counted"], s["units_main"], s["units_pertinenze"], s["units_to_complete"]) == (4, 3, 1, 1)
    assert s["accessories_unknown"] == 1 and s["category_to_verify"] == 3 and s["units_in_census"] == 4
    assert len(r["units"]) == 4 and len({u["id"] for u in r["units"]}) == 4
    righe = {u["id"]: u for u in r["units"]}
    assert righe[garage["id"]]["parent"] == {"id": u1["id"], "code": u1["code"], "same_building": True}
    assert righe[u1["id"]]["pertinenze_count"] == 1 and righe[u2["id"]]["pertinenze_count"] == 0
    assert r["staircases"] == ["A", "B"]
    # i contatori storici della Fase 3 restano identici (contratto certificato)
    assert r["counters"]["units_census"] == 4 and r["counters"]["accessories_unknown"] == 1
    # piani in ordine fisico: Terra prima del 1º (anche se e' una lettera)
    terra = _unita(m, building_id=e["id"], floor="T", property_type="commercial")
    assert [u["id"] for u in _scheda(m, e["id"])["units"]][:2] == [garage["id"], terra["id"]]
    # dati utili a distinguere le unita'
    assert {"code", "property_type", "staircase", "floor", "internal_number", "surface_sqm",
            "commercial_status", "record_kind"} <= set(righe[u1["id"]])


def test_05_cestino_archiviate_e_annullate(mondo):
    m = mondo
    api = m["api"]()
    e = _edificio(m, units_declared=4)
    tenuta = _unita(m, building_id=e["id"], floor="1")
    cestinata = _unita(m, building_id=e["id"], floor="2")
    archiviata = _unita(m, building_id=e["id"], floor="3")
    annullata = _unita(m, building_id=e["id"], floor="4")
    assert api.post(f"/api/property/properties/{annullata['id']}/undo-create").status_code == 200
    assert api.post(f"/api/property/properties/{archiviata['id']}/archive").status_code == 200
    assert api.post(f"/api/property/properties/{cestinata['id']}/trash", json={"reason_code": "created_by_mistake"}).status_code == 200
    r = _scheda(m, e["id"])
    s = r["census_summary"]
    # censite = attiva + archiviata; Cestino e annullata fuori
    assert (s["units_counted"], s["units_active"], s["units_archived"], s["units_to_complete"]) == (2, 1, 1, 2)
    assert [u["id"] for u in r["units"]] == [tenuta["id"]]
    assert [u["id"] for u in r["archived_units"]] == [archiviata["id"]]
    # il Cestino conserva la relazione: il ripristino la ritrova
    assert _q(m, "SELECT building_id FROM properties WHERE id = %s", (cestinata["id"],))[0][0] == e["id"]
    assert api.post(f"/api/property/properties/{cestinata['id']}/restore").status_code == 200
    assert _scheda(m, e["id"])["census_summary"]["units_counted"] == 3
    # riattivata, l'archiviata torna fra le attive
    assert api.post(f"/api/property/properties/{archiviata['id']}/unarchive").status_code == 200
    s = _scheda(m, e["id"])["census_summary"]
    assert (s["units_active"], s["units_archived"], s["units_counted"]) == (3, 0, 3)


def test_06_censite_oltre_le_dichiarate(mondo):
    m = mondo
    e = _edificio(m, units_declared=2)
    for piano in ("1", "2", "3"):
        _unita(m, building_id=e["id"], floor=piano)
    s = _scheda(m, e["id"])["census_summary"]
    assert (s["units_declared"], s["units_counted"], s["units_to_complete"], s["units_over_declared"]) == (2, 3, 0, 1)
    assert _q(m, "SELECT units_declared FROM buildings WHERE id = %s", (e["id"],))[0][0] == 2   # nessuna correzione


def test_07_pertinenze_per_relazione_reale_non_per_indirizzo(mondo):
    m = mondo
    a = _edificio(m, units_declared=2)
    b = _edificio(m, address="Via Roma", civic_number="11", name="Di fronte", units_declared=1)
    principale = _unita(m, building_id=a["id"], floor="1")
    # garage nella palazzina di fronte: conta in B, resta pertinenza della principale
    di_fronte = _unita(m, building_id=b["id"], parent_property_id=principale["id"], property_type="garage")
    # posto auto senza edificio, stesso indirizzo di A: NON conta in A
    sciolto = _unita(m, parent_property_id=principale["id"], property_type="garage",
                     city="Fermo", address="Via Roma", civic_number="10")
    sa, sb = _scheda(m, a["id"]), _scheda(m, b["id"])
    assert sa["census_summary"]["units_counted"] == 1 and [u["id"] for u in sa["units"]] == [principale["id"]]
    assert sa["units"][0]["pertinenze_count"] == 2
    assert sb["census_summary"]["units_pertinenze"] == 1
    assert sb["units"][0]["parent"] == {"id": principale["id"], "code": principale["code"], "same_building": False}
    assert sciolto["building_id"] is None
    # scollegata, resta nella sua palazzina senza principale (dato preservato).
    # SENTINELLA AGGIORNATA DA PERTINENZE-1: con la 089 resta una PERTINENZA
    # «da collegare» (prima tornava a contare come principale: la natura si
    # perdeva con il collegamento). Censite invariate, nessun doppio conteggio.
    assert m["api"]().post(f"/api/property/properties/{principale['id']}/pertinenze/{di_fronte['id']}/unlink").status_code == 200
    sb = _scheda(m, b["id"])
    assert (sb["census_summary"]["units_main"], sb["census_summary"]["units_pertinenze"],
            sb["census_summary"]["units_pertinenze_unlinked"], sb["census_summary"]["units_counted"]) == (0, 1, 1, 1)
    assert sb["units"][0]["parent"] is None and sb["units"][0]["is_pertinenza"] is True


def test_08_immobili_autonomi_e_scheda_immobile_con_l_edificio(mondo):
    m = mondo
    api = m["api"]()
    villa = api.post("/api/property/properties", json={"property_type": "villa", "commercial_status": "draft",
                                                       "city": "Fermo", "address": "Via Roma", "civic_number": "10"})
    assert villa.status_code == 201, villa.text
    scheda = api.get(f"/api/property/properties/{villa.json()['id']}").json()
    assert scheda["building_id"] is None and "building" not in scheda
    e = _edificio(m)
    # nessuna associazione automatica per indirizzo
    assert _scheda(m, e["id"])["census_summary"]["units_counted"] == 0
    u = _unita(m, building_id=e["id"], floor="2")
    scheda = api.get(f"/api/property/properties/{u['id']}").json()
    assert scheda["building"]["id"] == e["id"] and scheda["building"]["name"] == "Palazzina Roma 10"
    assert {"city", "microzone", "address", "civic_number"} <= set(scheda["building"])


def test_09_permessi_e_separazione_fra_agenzie(mondo):
    m = mondo
    e = _edificio(m)
    _unita(m, building_id=e["id"], floor="1")
    altra = _edificio(m, "owner_b", address="Via Bis", civic_number="1", name="Agenzia B")
    _unita(m, "owner_b", building_id=altra["id"], floor="1")
    # l'agente vede gli edifici e le unita' della propria agenzia (come l'elenco Immobili)
    assert [x["id"] for x in _lista(m, "agent_a")["items"]] == [e["id"]]
    assert _scheda(m, e["id"], "agent_a")["census_summary"]["units_counted"] == 1
    # l'altra agenzia: ne' in lista ne' in accesso diretto, e i contatori non ne parlano
    assert [x["id"] for x in _lista(m, "owner_b")["items"]] == [altra["id"]]
    assert _lista(m, "owner_b", search="Palazzina Roma")["total"] == 0
    assert m["api"]("owner_b").get(f"{BASE}/{e['id']}").status_code == 404
    assert m["api"]().get(f"{BASE}/{altra['id']}").status_code == 404
    assert _scheda(m, altra["id"], "owner_b")["census_summary"]["units_counted"] == 1
