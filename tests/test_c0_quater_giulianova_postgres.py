"""AGENZIE-TERRITORI C0-quater - Giulianova nel catalogo dei Comuni del CRM.

PostgreSQL usa-e-getta (schema completo dal runner) e router veri; nessun dato
reale. Il catalogo e' lo stesso (`property/catalog.py`): nessun secondo elenco.

  g01  Giulianova (ISTAT 067025, TE) e' nell'albero del form
       (`GET /api/property/form-options`), in ordine alfabetico fra i comuni TE,
       con le 4 microzone decise dal titolare (Sud, Centro, Nord, Alta); gli
       altri 25 comuni e le loro 80 microzone restano identici (84 in tutto);
  g02  un EDIFICIO a Giulianova si crea con la cascata regione/provincia/comune
       (census) e si modifica; una microzona inventata e' rifiutata;
  g03  un IMMOBILE indipendente a Giulianova si crea e si modifica dal form
       (property service) con la stessa regola;
  g04  Alba Adriatica e Tortoreto continuano a funzionare (edificio + immobile
       con microzona); un comune fuori catalogo e' ancora rifiutato;
  g05  STIMA Voice (`municipality_supported`) riconosce Giulianova con la stessa
       regola del censimento;
  g06  nessun dato esistente cambia: le righe presenti prima della prova sono
       identiche dopo (immobili ed edifici), e nessuna riga di `agencies`,
       `agency_memberships`, `network_*` o `agency_territory_assignments` viene
       toccata dal catalogo.
  g08  ognuna delle 4 microzone di Giulianova si salva e si rilegge su edificio
       e immobile indipendente (anche in modifica).
In Chromium (stesso server delle prove Edifici): il filtro Comune della lista
Edifici offre Giulianova e filtra l'edificio creato; il filtro Microzona offre
le 4 microzone di Giulianova e filtra l'edificio salvato con una di esse.
"""
from __future__ import annotations

import uuid

import pytest

from tests.test_censimento_3_backend_postgres import _edificio, _q, _unita  # noqa: F401
from tests.test_edifici_1_browser_postgres import DSN, completo, mondo, server  # noqa: F401
from tests.test_eliminazione_edificio_1_browser_postgres import _sfora, _vai

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

TE = {"region": "Abruzzo", "province": "TE"}
GIULIANOVA_ZONE = ["Giulianova Sud", "Giulianova Centro", "Giulianova Nord", "Giulianova Alta"]


def _impronta(m):
    """Le righe che il catalogo non deve mai toccare."""
    return {
        "properties": _q(m, "SELECT id, city, province, microzone, agency_id, updated_at FROM properties ORDER BY id"),
        "buildings": _q(m, "SELECT id, city, province, microzone, agency_id, updated_at FROM buildings ORDER BY id"),
        "rete": _q(m, "SELECT (SELECT count(*) FROM agencies), (SELECT count(*) FROM agency_memberships), "
                      "(SELECT count(*) FROM network_territories), (SELECT count(*) FROM agency_territory_assignments), "
                      "(SELECT count(*) FROM network_territory_aliases)"),
    }


def test_g01_g06_giulianova_nel_catalogo(mondo):
    from property import catalog
    from voice.operations import municipality_supported

    m = mondo
    prima = _impronta(m)
    api = m["api"]()

    # g01 - albero del form
    r = api.get("/api/property/form-options")
    assert r.status_code == 200, r.text
    albero = r.json()["territory"]
    te = next(p for reg in albero if reg["name"] == "Abruzzo" for p in reg["provinces"] if p["code"] == "TE")
    nomi = [c["name"] for c in te["municipalities"]]
    assert nomi == sorted(nomi, key=str.casefold) and "Giulianova" in nomi
    giul = next(c for c in te["municipalities"] if c["name"] == "Giulianova")
    assert giul == {"name": "Giulianova", "istat_code": "067025", "microzones": GIULIANOVA_ZONE}
    tutti = {c["name"]: c for reg in albero for p in reg["provinces"] for c in p["municipalities"]}
    assert len(tutti) == 26 and sum(len(c["microzones"]) for c in tutti.values()) == 84
    assert list(nomi).count("Giulianova") == 1                           # nessun comune duplicato
    assert tutti["Alba Adriatica"]["microzones"] == ["Nord", "Villa Fiore", "Zona Basciani"]

    # g02 - edificio a Giulianova
    tag = uuid.uuid4().hex[:6]
    e = _edificio(m, **TE, city="Giulianova", microzone=None, address=f"Via Prova {tag}", civic_number="1",
                  name=None, units_declared=None, units_declared_source=None)
    assert (e["city"], e["province"], e["region"]) == ("Giulianova", "TE", "Abruzzo")
    r = api.patch(f"/api/property/buildings/{e['id']}", json={**TE, "city": "Giulianova", "civic_number": "1/A"})
    assert r.status_code == 200, r.text
    r = api.post("/api/property/buildings", json={**TE, "city": "Giulianova", "microzone": "Lido",
                                                  "address": f"Via Prova {tag}", "civic_number": "2"})
    assert r.status_code == 400 and "microzona" in r.json()["detail"].lower(), r.text
    u = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    assert _q(m, "SELECT city FROM properties WHERE id = %s", (u["id"],))[0][0] == "Giulianova"

    # g03 - immobile indipendente a Giulianova
    r = api.post("/api/property/properties", json={"title": f"Prova {tag}", **TE, "city": "Giulianova",
                                                   "address": f"Via Prova {tag}", "civic_number": "3"})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]
    r = api.patch(f"/api/property/properties/{pid}", json={**TE, "city": "Giulianova", "civic_number": "3/B"})
    assert r.status_code == 200, r.text
    r = api.patch(f"/api/property/properties/{pid}", json={**TE, "city": "Giulianova", "microzone": "Centro"})
    assert r.status_code == 400, r.text

    # g04 - gli altri comuni invariati
    a = _edificio(m, **TE, city="Alba Adriatica", microzone="Nord", address=f"Via Prova {tag}", civic_number="4")
    assert a["microzone"] == "Nord"
    r = api.post("/api/property/properties", json={"title": f"Prova T {tag}", **TE, "city": "Tortoreto",
                                                   "microzone": "Lido Nord"})
    assert r.status_code in (200, 201), r.text
    r = api.post("/api/property/buildings", json={**TE, "city": "Roseto degli Abruzzi", "address": "Via X",
                                                  "civic_number": "1"})
    assert r.status_code == 400 and "Comune non presente" in r.json()["detail"], r.text

    # g05 - STIMA Voice, stessa regola
    assert municipality_supported({**TE, "city": "Giulianova"}) is True
    assert municipality_supported({**TE, "city": "Giulianova", "microzone": "Lido"}) is False
    assert municipality_supported({**TE, "city": "Roseto degli Abruzzi"}) is False
    assert set(catalog.CRM_ADDED_MUNICIPALITIES) == {"Giulianova"}

    # g06 - nessun dato preesistente cambiato, nessuna tabella di rete toccata
    dopo = _impronta(m)
    vecchi_p = {r[0] for r in prima["properties"]}
    vecchi_b = {r[0] for r in prima["buildings"]}
    assert [r for r in dopo["properties"] if r[0] in vecchi_p] == prima["properties"]
    assert [r for r in dopo["buildings"] if r[0] in vecchi_b] == prima["buildings"]
    assert dopo["rete"] == prima["rete"]


def test_g08_le_quattro_microzone_si_salvano(mondo):
    m = mondo
    api = m["api"]()
    tag = uuid.uuid4().hex[:6]
    for i, zona in enumerate(GIULIANOVA_ZONE):
        # edificio: creazione con la microzona, rilettura dal database
        e = _edificio(m, **TE, city="Giulianova", microzone=zona, address=f"Via Zona {tag}",
                      civic_number=str(10 + i), name=None, units_declared=None, units_declared_source=None)
        assert e["microzone"] == zona
        assert [tuple(r) for r in _q(m, "SELECT city, microzone FROM buildings WHERE id = %s", (e["id"],))] == [("Giulianova", zona)]
        # immobile indipendente: creazione senza microzona, poi modifica con la microzona
        r = api.post("/api/property/properties", json={"title": f"Zona {tag} {i}", **TE, "city": "Giulianova",
                                                       "address": f"Via Zona {tag}", "civic_number": str(20 + i)})
        assert r.status_code in (200, 201), r.text
        pid = r.json()["id"]
        r = api.patch(f"/api/property/properties/{pid}", json={**TE, "city": "Giulianova", "microzone": zona})
        assert r.status_code == 200, r.text
        assert r.json()["microzone"] == zona
        assert [tuple(r) for r in _q(m, "SELECT city, microzone FROM properties WHERE id = %s", (pid,))] == [("Giulianova", zona)]
        # immobile indipendente creato direttamente con la microzona
        r = api.post("/api/property/properties", json={"title": f"Zona {tag} {i}b", **TE, "city": "Giulianova",
                                                       "microzone": zona})
        assert r.status_code in (200, 201), r.text
        assert r.json()["microzone"] == zona


def test_g07_giulianova_selezionabile_in_chromium(server):  # noqa: F811
    from playwright.sync_api import sync_playwright
    m = server
    tag = uuid.uuid4().hex[:6]
    e = _edificio(m, **TE, city="Giulianova", microzone=None, address=f"Via Chromium {tag}", civic_number="7",
                  name=f"Palazzina Giulianova {tag}", units_declared=None, units_declared_source=None)
    z = _edificio(m, **TE, city="Giulianova", microzone="Giulianova Alta", address=f"Via Chromium {tag}",
                  civic_number="9", name=f"Palazzina Alta {tag}", units_declared=None, units_declared_source=None)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 860})
        errori = []
        page.on("pageerror", lambda x: errori.append(str(x)))
        _vai(page, m["url"], "#/edifici")
        page.wait_for_selector("#buildings-city option[value='Giulianova']", state="attached")
        page.select_option("#buildings-city", "Giulianova")
        page.wait_for_selector(f"[data-building-id='{e['id']}']")
        assert page.locator("[data-building-id]").count() >= 1
        testo = page.inner_text(f"[data-building-id='{e['id']}']")
        assert "Giulianova" in testo, testo
        zone = page.eval_on_selector_all("#buildings-microzone option", "os => os.map(o => o.value).filter(Boolean)")
        assert zone == GIULIANOVA_ZONE, zone
        page.select_option("#buildings-microzone", "Giulianova Alta")
        page.wait_for_selector(f"[data-building-id='{z['id']}']")
        page.wait_for_selector(f"[data-building-id='{e['id']}']", state="detached")
        mis = _sfora(page)
        assert mis["scroll"] <= mis["inner"] + 1 and mis["fuori"] == [], mis
        assert errori == [], errori
        browser.close()
