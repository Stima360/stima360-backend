"""EDIFICI-1 - la sezione Edifici nella Shell, ESEGUITA (stub DOM in node).

Stesso harness di CENSIMENTO-1 Fase 4 (tests/test_censimento_4_ui.py): i
moduli VERI della Shell girano in node con `fetch` scriptato. Sono controlli
UI SIMULATI (nessun browser): la prova in Chromium e' in
tests/test_edifici_1_browser.py.

  m01-m03  funzioni pure: contatori (non nota / zero / eccedenza /
           archiviate), catalogo comuni-microzone e filtri coerenti, righe unita'
  01  voce «Edifici» subito sopra «Immobili»; lista a card con dichiarate /
      censite / da completare; una sola richiesta per pagina
  02  filtri Comune -> Microzona coerenti, ricerca, paginazione con totale
  03  i filtri restano tornando dalla scheda (memoria di sessione)
  04  stati vuoti ed errore con «Riprova»
  05  scheda edificio: dati, contatori, unita' con stato e relazione, link
      all'unita', archiviate a parte, ritorno alla lista
  06  «Archivia» solo a chi puo' gestire l'unita'
  07  scheda immobile: collegamento all'edificio; nessuno per l'autonomo
"""
from __future__ import annotations

import json

from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)
from tests.test_censimento_4_ui import (
    DETTAGLIO, EDIFICIO, IMMOBILE_CENSUS, OPZIONI, UNITA, _modello, _run, _scritture, a30_13b, base, node, rt,
)

RIEPILOGO = {"units_declared": 6, "units_declared_known": True, "units_counted": 5, "units_active": 4,
             "units_main": 3, "units_pertinenze": 1, "units_archived": 1, "units_to_complete": 1,
             "units_over_declared": 0, "units_declared_source": "survey", "units_in_census": 4,
             "category_to_verify": 2, "accessories_unknown": 1}
OPZIONI_E1 = {**OPZIONI,
              "territory": [{"name": "Abruzzo", "provinces": [{"code": "TE", "name": "Teramo", "municipalities": [
                  {"name": "Tortoreto", "microzones": ["Lido Nord", "Alto"]},
                  {"name": "Alba Adriatica", "microzones": ["Nord"]}]}]}],
              "building_census_statuses": [{"value": "verified", "label": "Verificato"},
                                           {"value": "partial", "label": "Parziale"},
                                           {"value": "estimated", "label": "Stimato"}]}
EDIFICIO_L = {**EDIFICIO, "microzone": "Lido Nord", "census_summary": RIEPILOGO}
ALTRO = {**EDIFICIO, "id": 8, "name": None, "address": "Via Nazionale", "civic_number": "3", "city": "Alba Adriatica",
         "microzone": "Nord", "census_summary": {**RIEPILOGO, "units_declared": None, "units_declared_known": False,
                                                  "units_counted": 2, "units_archived": 0, "units_to_complete": None}}
OLTRE = {**EDIFICIO, "id": 9, "name": "Torre", "census_summary": {**RIEPILOGO, "units_declared": 2, "units_counted": 3,
                                                                   "units_archived": 0, "units_to_complete": 0,
                                                                   "units_over_declared": 1}}
PERTINENZA = {**UNITA[0], "id": 415, "code": "IMM-415", "property_type": "garage", "floor": "-1", "parent_property_id": 412,
              "parent": {"id": 412, "code": "IMM-412", "same_building": True}, "pertinenze_count": 0}
ARCHIVIATA = {**UNITA[1], "id": 420, "code": "IMM-420", "commercial_status": "archived", "archived_at": "2026-09-01T10:00:00Z",
              "record_kind": "crm", "parent": None, "pertinenze_count": 0}
SCHEDA = {**DETTAGLIO, "microzone": "Lido Nord", "census_summary": RIEPILOGO, "staircases": ["A", "B"],
          "units": [{**u, "parent": None, "pertinenze_count": 1 if u["id"] == 412 else 0, "assigned_agent_id": 3 if u["id"] == 410 else None}
                    for u in UNITA] + [{**PERTINENZA, "assigned_agent_id": None}],
          "archived_units": [ARCHIVIATA]}


def _rotte(sessione="agency_owner", liste=None, immobile=None):
    liste = liste or [{"status": 200, "body": {"items": [EDIFICIO_L, ALTRO, OLTRE], "total": 3, "limit": 25, "offset": 0}}]
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
        ("GET", "/api/property/form-options", [rt.ok(OPZIONI_E1)]),
        ("GET", "/api/property/buildings/7", [rt.ok(SCHEDA)]),
        ("GET", "/api/property/buildings", list(liste)),
        ("GET", "/api/property/properties/30/census", [rt.ok({"id": 30, "code": "IMM-30", "record_kind": "census",
                                                             "address_inherited": True, "whole_building": False,
                                                             "building": None, "parent": None, "pertinenze": [],
                                                             "accessories": [], "accessories_unknown": 0})]),
        ("GET", "/api/property/properties/30", [rt.ok(immobile or IMMOBILE_CENSUS)]),
        ("GET", "/api/property/properties?", [rt.ok({"items": []})]),
        ("GET", "/api/proposals?", [rt.ok({"items": []})]),
        ("GET", "/api/sales?", [rt.ok({"items": []})]),
        ("GET", "/api/match/", [rt.ok({"items": []})]),
        ("GET", "/api/core/", [rt.ok({"items": []})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


LETTURE = "chiamate().filter((c) => c.m === 'GET' && c.url.startsWith('/api/property/buildings?')).map((c) => c.url)"


# ---------------------------------------------------------------------------
# funzioni pure
# ---------------------------------------------------------------------------

@node
def test_m01_contatori_non_nota_zero_eccedenza_e_archiviate():
    out = _modello("""(() => {
      const v = (s) => { const x = m.summaryView(s); return [x.declared, x.counted, x.countedNote, x.toComplete, x.over]; };
      return {
        noto: v({units_declared: 6, units_counted: 5, units_archived: 1, units_to_complete: 1, units_over_declared: 0}),
        ignoto: v({units_declared: null, units_counted: 2, units_archived: 0, units_to_complete: null}),
        zero: v({units_declared: 0, units_counted: 0, units_archived: 0, units_to_complete: 0}),
        oltre: v({units_declared: 2, units_counted: 3, units_archived: 0, units_to_complete: 0, units_over_declared: 1}),
        vuoto: v(undefined),
        split: m.summaryView({units_main: 1, units_pertinenze: 2}).split,
      };
    })()""")
    assert out["noto"] == ["6", "5", "di cui 1 archiviata", "1", ""]
    assert out["ignoto"] == ["Non note", "2", "", "—", ""]                     # non nota: mai 0
    assert out["zero"] == ["0", "0", "", "0", ""]
    assert out["oltre"][3] == "0" and "1 unità censita oltre le dichiarate" in out["oltre"][4]   # mai negativi
    assert out["vuoto"] == ["Non note", "0", "", "—", ""]
    assert out["split"] == "1 principale + 2 pertinenze"


@node
def test_m02_catalogo_comuni_microzone_e_filtri_coerenti():
    albero = json.dumps(OPZIONI_E1["territory"])
    out = _modello(f"""(() => {{
      const comuni = m.catalogMunicipalities({albero});
      return {{
        nomi: comuni.map((c) => c.name),
        zone: comuni.find((c) => c.name === 'Tortoreto').microzones,
        ok: m.coherentFilters({{city: 'Tortoreto', microzone: 'Alto', search: ' roma 10 '}}, comuni),
        zonaAltrui: m.coherentFilters({{city: 'Alba Adriatica', microzone: 'Alto'}}, comuni),
        senzaComune: m.coherentFilters({{microzone: 'Alto'}}, comuni),
        q: m.buildingListQuery({{search: 'roma', city: 'Tortoreto', microzone: 'Alto'}}, 25, 25),
        qSoloZona: m.buildingListQuery({{microzone: 'Alto'}}),
      }};
    }})()""")
    assert out["nomi"] == ["Alba Adriatica", "Tortoreto"] and out["zone"] == ["Lido Nord", "Alto"]
    assert out["ok"] == {"search": "roma 10", "city": "Tortoreto", "microzone": "Alto"}
    assert out["zonaAltrui"]["microzone"] == "" and out["senzaComune"]["microzone"] == ""
    assert out["q"] == {"sort": "address", "limit": 25, "offset": 25, "search": "roma", "city": "Tortoreto", "microzone": "Alto"}
    assert "microzone" not in out["qSoloZona"]


@node
def test_m03_righe_unita_e_relazioni_reali():
    out = _modello("""(() => ({
      fatti: m.unitFacts({property_type: 'apartment', staircase: 'B', floor: '2', internal_number: '5', surface_sqm: '85.00'},
                         [{value: 'apartment', label: 'Appartamento'}]),
      senza: m.unitFacts({property_type: 'garage'}, []),
      pert: m.unitRelationText({parent_property_id: 1, parent: {id: 1, code: 'IMM-1', same_building: true}}),
      altrove: m.unitRelationText({parent_property_id: 1, parent: {id: 1, code: 'IMM-1', same_building: false}}),
      perso: m.unitRelationText({parent_property_id: 1, parent: null}),
      principale: m.unitRelationText({pertinenze_count: 2}),
      sola: m.unitRelationText({pertinenze_count: 0}),
      titolo: [m.buildingTitle({id: 3, name: ' '}), m.buildingTitle({id: 3, address: 'Via Po', civic_number: '1'}), m.buildingTitle({id: 3})],
    }))()""")
    assert out["fatti"] == "Appartamento · scala B · piano 2º · int. 5 · 85 m²"
    assert out["senza"] == "garage"
    assert out["pert"] == "Pertinenza di IMM-1" and out["altrove"] == "Pertinenza di IMM-1 (in un altro edificio)"
    assert out["perso"].startswith("Pertinenza") and out["principale"] == "Con 2 pertinenze" and out["sola"] == ""
    assert out["titolo"] == ["Palazzina #3", "Via Po 1", "Palazzina #3"]


# ---------------------------------------------------------------------------
# Shell eseguita
# ---------------------------------------------------------------------------

@node
def test_01_voce_edifici_e_lista_a_card_con_i_contatori(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const voci = __dom.byId['nav'].querySelectorAll('[data-route]').map((b) => b.dataset.route);
      const card = C().querySelectorAll('.building-card').map((c) => ({ href: c.getAttribute('href'), testo: c.visibleText() }));
      report({ voci, card, titolo: __dom.byId['page-title'].textContent, letture: """ + LETTURE + r""",
               range: C().querySelector('#buildings-range').textContent });
    """
    out = _run(staged, scenario, _rotte(), "#/edifici")
    assert out["voci"].index("edifici") == out["voci"].index("immobili") - 1        # accanto a Immobili
    assert out["voci"].index("venditori") == out["voci"].index("immobili") + 1      # flusso VENDITORI-1 intatto
    assert out["titolo"] == "Edifici"
    assert out["letture"] == ["/api/property/buildings?sort=address&limit=25&offset=0"]        # una sola richiesta
    assert [c["href"] for c in out["card"]] == ["#/edifici/7", "#/edifici/8", "#/edifici/9"]
    primo = out["card"][0]["testo"]
    assert "Palazzina via Roma 10" in primo and "Via Roma 10" in primo and "Tortoreto · Lido Nord" in primo
    assert "Dichiarate6" in primo and "Censite5" in primo and "di cui 1 archiviata" in primo and "Da completare1" in primo
    assert "Dichiarate" in out["card"][1]["testo"] and "Non note" in out["card"][1]["testo"]
    assert "Da completare—" in out["card"][1]["testo"]
    assert "Censite oltre le dichiarate" in out["card"][2]["testo"]
    assert out["range"] == "1–3 di 3"
    assert _scritture(out) == []


@node
def test_02_filtri_coerenti_ricerca_e_paginazione(staged):  # noqa: F811
    pagina1 = {"status": 200, "body": {"items": [EDIFICIO_L] * 25, "total": 30, "limit": 25, "offset": 0}}
    pagina2 = {"status": 200, "body": {"items": [EDIFICIO_L] * 5, "total": 30, "limit": 25, "offset": 25}}
    scenario = r"""
      await wait(); await wait();
      const zonaPrima = C().querySelector('#buildings-microzone').disabled;
      const comuni = C().querySelector('#buildings-city').querySelectorAll('option').map((o) => o.value);
      const sel = C().querySelector('#buildings-city'); sel.value = 'Tortoreto'; sel.dispatch('change'); await wait();
      const zone = C().querySelector('#buildings-microzone').querySelectorAll('option').map((o) => o.value);
      const zonaDopo = C().querySelector('#buildings-microzone').disabled;
      const z = C().querySelector('#buildings-microzone'); z.value = 'Alto'; z.dispatch('change'); await wait();
      const s = C().querySelector('#buildings-search'); s.value = 'roma 10'; s.dispatch('input'); await wait(350);
      C().querySelector('#buildings-next').dispatch('click'); await wait();
      const range = C().querySelector('#buildings-range').textContent;
      const avantiFermo = C().querySelector('#buildings-next').disabled;
      // cambiare comune azzera la microzona (filtri coerenti)
      sel.value = 'Alba Adriatica'; sel.dispatch('change'); await wait();
      report({ zonaPrima, comuni, zone, zonaDopo, range, avantiFermo, letture: """ + LETTURE + r""" });
    """
    out = _run(staged, scenario, _rotte(liste=[pagina1, pagina1, pagina1, pagina1, pagina2, pagina1]), "#/edifici")
    assert out["zonaPrima"] is True and out["zonaDopo"] is False
    assert out["comuni"] == ["", "Alba Adriatica", "Tortoreto"] and out["zone"] == ["", "Lido Nord", "Alto"]
    assert out["letture"] == [
        "/api/property/buildings?sort=address&limit=25&offset=0",
        "/api/property/buildings?city=Tortoreto&sort=address&limit=25&offset=0",
        "/api/property/buildings?city=Tortoreto&microzone=Alto&sort=address&limit=25&offset=0",
        "/api/property/buildings?search=roma%2010&city=Tortoreto&microzone=Alto&sort=address&limit=25&offset=0",
        "/api/property/buildings?search=roma%2010&city=Tortoreto&microzone=Alto&sort=address&limit=25&offset=25",
        "/api/property/buildings?search=roma%2010&city=Alba%20Adriatica&sort=address&limit=25&offset=0",
    ]
    assert out["range"] == "26–30 di 30" and out["avantiFermo"] is True


@node
def test_03_i_filtri_restano_tornando_dalla_scheda(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const sel = C().querySelector('#buildings-city'); sel.value = 'Tortoreto'; sel.dispatch('change'); await wait();
      const s = C().querySelector('#buildings-search'); s.value = 'roma'; s.dispatch('input'); await wait(350);
      window.location.hash = '#/edifici/7'; await window._fire('hashchange'); await wait(); await wait();
      const inScheda = !!C().querySelector('#building-title');
      const ritorno = C().querySelector('#building-back').getAttribute('href');
      window.location.hash = '#/edifici'; await window._fire('hashchange'); await wait(); await wait();
      report({ inScheda, ritorno, cerca: C().querySelector('#buildings-search').value,
               comune: C().querySelector('#buildings-city').value, letture: """ + LETTURE + r""" });
    """
    out = _run(staged, scenario, _rotte(), "#/edifici")
    assert out["inScheda"] and out["ritorno"] == "#/edifici"
    assert out["cerca"] == "roma" and out["comune"] == "Tortoreto"
    assert out["letture"][-1] == "/api/property/buildings?search=roma&city=Tortoreto&sort=address&limit=25&offset=0"


@node
def test_04_stati_vuoti_ed_errore_con_riprova(staged):  # noqa: F811
    vuota = {"status": 200, "body": {"items": [], "total": 0, "limit": 25, "offset": 0}}
    errore = {"status": 500, "body": {"detail": "Errore del server"}}
    scenario = r"""
      await wait(); await wait();
      const primo = C().querySelector('#buildings-area').visibleText();
      C().querySelector('#buildings-retry').dispatch('click'); await wait();
      const dopo = C().querySelector('#buildings-area').visibleText();
      const s = C().querySelector('#buildings-search'); s.value = 'zzz'; s.dispatch('input'); await wait(350);
      report({ primo, dopo, filtrata: C().querySelector('#buildings-area').visibleText(),
               azzera: !C().querySelector('#buildings-reset').hidden });
    """
    out = _run(staged, scenario, _rotte(liste=[errore, vuota, vuota]), "#/edifici")
    assert "Impossibile caricare gli edifici" in out["primo"] and "Riprova" in out["primo"]
    assert "Nessun edificio censito" in out["dopo"]
    assert "Nessun edificio per questi filtri" in out["filtrata"] and out["azzera"] is True


@node
def test_05_scheda_edificio_dati_contatori_unita_e_link(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const fatti = C().querySelector('#building-facts').visibleText();
      const contatori = C().querySelector('#building-counters').visibleText();
      const segnali = C().querySelector('#building-signals').visibleText();
      const righe = C().querySelector('#building-units').querySelectorAll('.census-unit-row').map((r) => r.visibleText());
      const link = C().querySelector('#building-units').querySelectorAll('[data-open-unit]').map((a) => a.getAttribute('href'));
      const archiviate = C().querySelector('#building-archived').visibleText();
      const titolo = __dom.byId['page-title'].textContent;
      C().querySelector('#building-units').querySelector('[data-open-unit="412"]').dispatch('click'); await wait();
      report({ fatti, contatori, segnali, righe, link, archiviate, titolo });
    """
    out = _run(staged, scenario, _rotte(), "#/edifici/7")
    assert out["titolo"] == "Edifici"
    for atteso in ("ComuneTortoreto", "MicrozonaLido Nord", "ViaVia Roma", "Civico10", "NomePalazzina via Roma 10",
                   "Stato censimentoParziale", "ScaleA, B"):
        assert atteso in out["fatti"], atteso
    assert "Dichiarate6" in out["contatori"] and "Sopralluogo" in out["contatori"]
    assert "Censite5" in out["contatori"] and "di cui 1 archiviata" in out["contatori"] and "Da completare1" in out["contatori"]
    assert "1 accessorio da chiarire" in out["segnali"] and "2 categorie da verificare" in out["segnali"]
    assert out["link"] == ["#/immobili/410", "#/immobili/411", "#/immobili/412", "#/immobili/415"]
    riga412 = next(r for r in out["righe"] if "IMM-412" in r)
    assert "Appartamento · scala B · piano 2º · int. 2 · 85 m²" in riga412 and "Censimento · Bozza" in riga412
    assert "Con 1 pertinenza" in riga412
    assert any("IMM-415" in r and "Pertinenza di IMM-412" in r for r in out["righe"])
    assert "Archiviate (1)" in out["archiviate"] and "IMM-420" in out["archiviate"] and "Archiviato" in out["archiviate"]
    assert out["hash"] == "#/immobili/412"


@node
def test_06_archivia_solo_a_chi_puo_gestire_l_unita(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      report({ archivia: C().querySelectorAll('[data-archive-unit]').map((b) => b.dataset.archiveUnit),
               duplica: C().querySelectorAll('[data-duplicate-unit]').length,
               nuova: !!C().querySelector('#unit-add-apartment') });
    """
    titolare = _run(staged, scenario, _rotte("agency_owner"), "#/edifici/7")
    agente = _run(staged, scenario, _rotte("agent"), "#/edifici/7")
    assert titolare["archivia"] == ["410", "411", "412", "415"]
    assert agente["archivia"] == ["410"]                  # solo l'unita' assegnata all'agente (user 3)
    assert agente["duplica"] == 4 and agente["nuova"]      # le azioni del censimento restano raggiungibili


@node
def test_07_scheda_immobile_collegamento_all_edificio(staged):  # noqa: F811
    con = {**IMMOBILE_CENSUS, "building": {"id": 7, "name": "Palazzina via Roma 10", "city": "Tortoreto",
                                           "microzone": "Lido Nord", "address": "Via Roma", "civic_number": "10"}}
    autonomo = {**IMMOBILE_CENSUS, "building_id": None, "address_inherited": False}
    scenario = r"""
      await wait(); await wait();
      const a = C().querySelector('#property-building-link');
      report({ href: a ? a.getAttribute('href') : null, testo: a ? a.visibleText() : null });
    """
    out = _run(staged, scenario, _rotte(immobile=con), "#/immobili/30")
    assert out["href"] == "#/edifici/7"
    assert "Palazzina via Roma 10" in out["testo"] and "Via Roma 10, Tortoreto" in out["testo"]
    assert _run(staged, scenario, _rotte(immobile=autonomo), "#/immobili/30")["href"] is None
