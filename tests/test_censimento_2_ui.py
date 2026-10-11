"""CENSIMENTO-2 - la UI della composizione: funzioni PURE eseguite in node e
controlli statici dei moduli. La prova completa (Shell vera, backend e
PostgreSQL veri, Chromium a 390 e 1280 px) e' in
tests/test_censimento_2_browser_postgres.py.

  m01  lettura della composizione: stato, DUE indicatori separati, classi
  m02  interni: ordine (scala, piano, interno), filtro «Da completare»,
       pertinenze, dati mancanti in parole
  m03  corpo della composizione: vuoto = «Non so», totale solo esplicito
  m04  corpo della generazione: provenienza obbligatoria con il valore, mai
       «Verificata» in gruppo, numerazione facoltativa, limite 300
  m05  modifica di gruppo e scheda della singola unita': un valore cambiato
       porta sempre la sua provenienza; «Verificata» solo con fonte catastale
  m06  procedura guidata: composizione facoltativa, somma = totale
  s01  nessuna classe o tipologia scritta a mano nel pannello; rotte solo in
       census-api.js; dati del server sempre con escapeHtml
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from tests.test_censimento_4_ui import a30_5, node

ROOT = Path(__file__).resolve().parents[1]
CENSUS = ROOT / "static" / "os_shell" / "assets" / "census"
CATALOG = {
    "classes": [{"value": "residential", "label": "Abitazioni", "property_types": ["apartment", "villa", "house", "rustic"]},
                {"value": "commercial", "label": "Commerciali", "property_types": ["commercial", "office"]},
                {"value": "other", "label": "Altre unità", "property_types": ["rustic", "garage", "storage", "land", "other"]},
                {"value": "pertinenze", "label": "Pertinenze", "property_types": ["garage", "storage", "other"]}],
    "data_sources": [{"value": "estimated", "label": "Stimata", "bulk": True}, {"value": "declared", "label": "Dichiarata", "bulk": True},
                     {"value": "verified", "label": "Verificata", "bulk": False}],
    "minimum_data": [{"value": "surface_sqm", "label": "superficie"}, {"value": "surface_source", "label": "provenienza della superficie"},
                     {"value": "cadastral_category", "label": "categoria"},
                     {"value": "cadastral_category_source", "label": "provenienza della categoria"}],
    "completion": [{"value": "draft", "label": "Bozza: composizione da dichiarare"},
                   {"value": "composition_declared", "label": "Composizione dichiarata"},
                   {"value": "units_created", "label": "Unità create, schede da completare"},
                   {"value": "data_complete", "label": "Schede complete"}],
    "cadastral_source_fields": ["cadastral_municipality_code", "cadastral_sheet", "cadastral_parcel", "cadastral_subunit"],
    "max_batch_units": 300,
}


def _js(rel, expr):
    script = f"import * as m from '{(ROOT / rel).as_posix()}';\nconsole.log(JSON.stringify({expr}));"
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


MODELLO = "static/os_shell/assets/census/composition-model.js"


def _risposta(**kw):
    base = {"building_id": 9, "catalog": CATALOG, "completion": "draft",
            "composition": {"residential": None, "commercial": None, "other": None, "pertinenze": None, "complete": False,
                            "sum": None, "units_declared": 12, "total_matches": None},
            "by_class": {c: {"declared": None, "present": 0, "to_create": None, "over_declared": 0}
                         for c in ("residential", "commercial", "other", "pertinenze")},
            "units_created": {"declared": None, "present": 2, "to_create": None, "complete": False},
            "data_completeness": {"units": 2, "complete": 0, "incomplete": 2}, "units": []}
    base["by_class"]["residential"]["present"] = 2
    base.update(kw)
    return base


@node
def test_m01_lettura_della_composizione_e_due_indicatori():
    bozza = _risposta()
    dichiarata = _risposta(completion="composition_declared",
                           composition={"residential": 8, "commercial": 1, "other": 0, "pertinenze": 3, "complete": True,
                                        "sum": 12, "units_declared": 12, "total_matches": True},
                           by_class={"residential": {"declared": 8, "present": 2, "to_create": 6, "over_declared": 0},
                                     "commercial": {"declared": 1, "present": 0, "to_create": 1, "over_declared": 0},
                                     "other": {"declared": 0, "present": 1, "to_create": 0, "over_declared": 1},
                                     "pertinenze": {"declared": 3, "present": 0, "to_create": 3, "over_declared": 0}},
                           units_created={"declared": 12, "present": 3, "to_create": 10, "complete": False})
    out = _js(MODELLO, f"[m.compositionView({json.dumps(bozza)}), m.compositionView({json.dumps(dichiarata)}), m.compositionView({{}})]")
    b, d, nulla = out
    assert b["state"] == "draft" and b["draft"] and b["stateLabel"] == "Bozza: composizione da dichiarare"
    assert b["unitsIndicator"] == "2 unità presenti · composizione da dichiarare" and b["canGenerate"] is False
    assert b["dataIndicator"] == "0 di 2 schede complete"                 # indicatore separato
    assert [r["declared"] for r in b["rows"]] == ["Non so"] * 4               # mai 0 per difetto
    assert d["unitsIndicator"] == "3 di 12 dichiarate · 10 da creare" and d["canGenerate"] is True and d["toCreate"] == 10
    assert d["rows"][2]["over"] == "1 in più" and d["rows"][0]["toCreate"] == "6"
    assert nulla is None


@node
def test_m02_interni_ordine_filtro_pertinenze_e_mancanti():
    unita = [
        {"id": 5, "staircase": "B", "floor": "1", "internal_number": "1", "missing": []},
        {"id": 4, "staircase": "A", "floor": "2", "internal_number": "10", "missing": ["floor"]},
        {"id": 3, "staircase": "A", "floor": "2", "internal_number": "9", "missing": []},
        {"id": 2, "staircase": "A", "floor": "T", "internal_number": None, "missing": ["surface_sqm", "surface_source"]},
        {"id": 1, "staircase": None, "floor": None, "internal_number": None, "missing": ["cadastral_category_source"]},
    ]
    out = _js(MODELLO, f"""[
      m.interiorRows({json.dumps(unita)}).map((u) => u.id),
      m.interiorRows({json.dumps(unita)}, {{onlyIncomplete: true}}).map((u) => u.id),
      m.missingText({{missing: ['surface_sqm', 'surface_source', 'cadastral_category_source']}}, {json.dumps(CATALOG)}),
      m.missingText({{missing: []}}, {json.dumps(CATALOG)}),
      m.pertinenzeText({{parent_property_id: 3, parent: {{id: 3, code: 'IMM-3', same_building: false}}}}),
      m.pertinenzeText({{unit_class: 'pertinenze'}}), m.pertinenzeText({{pertinenze_count: 2}}), m.pertinenzeText({{}}),
      m.sourceLabel({json.dumps(CATALOG)}, null), m.sourceLabel({json.dumps(CATALOG)}, 'verified'),
      m.sourceOptions({json.dumps(CATALOG)}, {{bulk: true}}).map((x) => x.value),
    ]""")
    assert out[0] == [1, 2, 3, 4, 5]                                          # senza scala prima, poi A (T, 2/9, 2/10), poi B
    assert out[1] == [1, 2, 4]
    assert out[2] == "Mancano: superficie, provenienza della categoria"
    assert out[3] == "Completa"
    assert out[4] == "Pertinenza di IMM-3 (altro edificio)" and out[5] == "Pertinenza da collegare"
    assert out[6] == "Con 2 pertinenze" and out[7] == ""
    assert out[8:10] == ["Non indicata", "Verificata"] and out[10] == ["estimated", "declared"]


@node
def test_m03_composizione_vuoto_non_so_e_totale_esplicito():
    out = _js(MODELLO, """[
      m.compositionPayload({residential: '8', commercial: '', other: '0', pertinenze: ' 3 '}, {setTotal: true}),
      m.compositionPayload({residential: '8', commercial: '1', other: '0', pertinenze: '3'}, {setTotal: true, source: 'cadastre'}),
      m.compositionPayload({residential: '8', commercial: '1', other: '0', pertinenze: '3'}),
      m.compositionPayload({residential: '-1', commercial: '1.5'}),
      m.compositionForm({composition: {residential: 0, commercial: null}}),
    ]""")
    bozza, completa, senza, errata, form = out
    assert bozza["body"] == {"residential": 8, "commercial": None, "other": 0, "pertinenze": 3, "set_total": False}
    assert bozza["complete"] is False and bozza["sum"] is None
    assert completa["body"]["set_total"] is True and completa["body"]["units_declared_source"] == "cadastre" and completa["sum"] == 12
    assert senza["body"]["set_total"] is False                                 # mai implicito
    assert errata["errors"] == ["I numeri della composizione sono interi, 0 o più."]
    assert form == {"residential": "0", "commercial": "", "other": "", "pertinenze": ""}


@node
def test_m04_generazione_provenienze_verificata_numerazione_limite():
    gruppo = {"unit_class": "residential", "count": "4", "property_type": "apartment", "surface_sqm": "70,5",
              "surface_source": "estimated", "cadastral_category": "", "cadastral_category_source": "", "floor": "2",
              "staircase": "", "pertinenza_kind": "", "numbering": True, "numbering_start": "3"}
    pert = {"unit_class": "pertinenze", "count": "2", "property_type": "storage", "pertinenza_kind": "cantina",
            "numbering": False, "numbering_start": "1"}
    out = _js(MODELLO, f"""[
      m.generatePayload([{json.dumps(gruppo)}, {json.dumps(pert)}], {{clientRequestId: 'k'}}),
      m.generatePayload([{json.dumps(gruppo)}], {{clientRequestId: 'k', dryRun: false, expected: {{residential: 2}}, confirmOver: true}}),
      m.generatePayload([{{...{json.dumps(gruppo)}, surface_source: ''}}]).errors,
      m.generatePayload([{{...{json.dumps(gruppo)}, cadastral_category: 'A/2', cadastral_category_source: 'verified'}}]).errors,
      m.generatePayload([{{...{json.dumps(gruppo)}, count: '301'}}]).errors,
      m.generatePayload([{{...{json.dumps(gruppo)}, count: '0'}}]).errors,
      m.generatePayload([]).errors,
      m.defaultGroups({json.dumps(_risposta(by_class={"residential": {"to_create": 6}, "commercial": {"to_create": 0},
                                                         "other": {"to_create": 0}, "pertinenze": {"to_create": 3}}))}).map((g) => [g.unit_class, g.count, g.property_type]),
      m.planRows({{plan: {{residential: {{declared: 4, present: 2, missing: 2, requested: 3, over_declared: 1}}}}}}, {json.dumps(CATALOG)}),
      m.hasOverDeclared({{plan: {{residential: {{over_declared: 1}}}}}}),
    ]""")
    anteprima, conferma, senzaFonte, verificata, troppe, zero, vuoto, proposti, piano, oltre = out
    assert anteprima["errors"] == [] and anteprima["total"] == 6
    g0, g1 = anteprima["body"]["groups"]
    assert g0 == {"unit_class": "residential", "count": 4, "property_type": "apartment", "surface_sqm": 70.5,
                  "surface_source": "estimated", "floor": "2", "numbering_start": 3}
    assert "cadastral_category" not in g0                                      # nessuna categoria inventata
    assert g1 == {"unit_class": "pertinenze", "count": 2, "property_type": "storage", "pertinenza_kind": "cantina"}
    assert anteprima["body"]["dry_run"] is True and "expected_existing" not in anteprima["body"]
    assert conferma["body"]["expected_existing"] == {"residential": 2} and conferma["body"]["confirm_over_declared"] is True
    assert senzaFonte == ["Gruppo 1: indica la provenienza della superficie."]
    assert verificata == ["Gruppo 1: «Verificata» si indica unità per unità, non in gruppo."]
    assert troppe == ["Al massimo 300 unità per volta."] and zero == ["Gruppo 1: indica quante unità (almeno 1)."]
    assert vuoto == ["Aggiungi almeno un gruppo di unità."]
    assert proposti == [["residential", "6", "apartment"], ["pertinenze", "3", "garage"]]
    assert piano[0]["over"] == 1 and oltre is True


@node
def test_m05_modifica_di_gruppo_e_scheda_della_singola_unita():
    prop = {"surface_sqm": "65.00", "surface_source": "declared", "cadastral_category": "A/2",
            "cadastral_category_source": "verified"}
    out = _js(MODELLO, f"""[
      m.bulkPayload({{surface_sqm: '70', floor: ' 2 '}}, [3, 4, 4]),
      m.bulkPayload({{cadastral_category_source: 'verified'}}, [3]).errors,
      m.bulkPayload({{}}, []).errors,
      m.bulkPayload({{}}, [7], {{residentialRustic: true}}).body,
      m.unitSourcesPatch({json.dumps(prop)}, {{surface_sqm: '65', surface_source: 'declared', cadastral_category: 'A/2', cadastral_category_source: 'verified'}}),
      m.unitSourcesPatch({json.dumps(prop)}, {{surface_sqm: '70', surface_source: '', cadastral_category: 'A/2', cadastral_category_source: 'verified'}}),
      m.unitSourcesPatch({json.dumps(prop)}, {{surface_sqm: '70', surface_source: 'declared', cadastral_category: 'A/3', cadastral_category_source: 'estimated'}}),
      m.unitSourcesPatch({json.dumps(prop)}, {{surface_sqm: '', surface_source: 'declared', cadastral_category: '', cadastral_category_source: ''}}),
      m.unitSourcesPatch({{surface_sqm: null}}, {{surface_sqm: '', surface_source: 'estimated'}}).errors,
      m.hasCadastralSource({{cadastral_municipality_code: 'L307', cadastral_sheet: '1', cadastral_parcel: '2', cadastral_subunit: '3'}}),
      m.hasCadastralSource({{cadastral_municipality_code: 'L307', cadastral_sheet: '1', cadastral_parcel: '2'}}),
    ]""")
    gruppo, verificata, vuota, rustico, invariata, nuovaSenza, nuovaCon, cancella, senzaDato, conFonte, senzaSub = out
    assert gruppo["body"] == {"property_ids": [3, 4], "changes": {"surface_sqm": 70, "floor": "2"}}
    assert gruppo["warnings"] == ["La provenienza della superficie tornerà «Non indicata»."]
    assert verificata == ["«Verificata» si registra dalla scheda della singola unità, con la fonte consultata."]   # mai in gruppo, neanche per una
    assert vuota == ["Seleziona almeno un’unità.", "Indica almeno un dato da modificare."]
    assert rustico["changes"] == {"census_class": "residential"}
    assert invariata == {"patch": {}, "errors": []}                            # nessun campo non correlato toccato
    assert nuovaSenza["patch"] == {"surface_sqm": 70, "surface_source": None}  # cambiata senza conferma: non indicata
    assert nuovaCon["patch"] == {"surface_sqm": 70, "surface_source": "declared", "cadastral_category": "A/3",
                                 "cadastral_category_source": "estimated"}
    assert cancella["patch"] == {"surface_sqm": None, "surface_source": None, "cadastral_category": None,
                                 "cadastral_category_source": None}
    assert senzaDato == ["Una provenienza della superficie senza la superficie non si salva."]
    assert conFonte is True and senzaSub is False                               # la sezione non serve


@node
def test_m07_verificata_solo_con_fonte_registrata_e_riconferma():
    """Precisazioni 2-3: la sola compilazione dei campi non verifica; i
    riferimenti cambiati chiedono una riconferma; stimata/dichiarata restano."""
    base = {"surface_sqm": "65.00", "surface_source": "declared", "cadastral_category": "A/2",
            "cadastral_category_source": None, "cadastral_municipality_code": "L307", "cadastral_section": None,
            "cadastral_sheet": "5", "cadastral_parcel": "61", "cadastral_subunit": "2"}
    verificata = {**base, "cadastral_category_source": "verified", "cadastral_evidence_type": "visura",
                  "cadastral_evidence_date": "2026-10-01", "cadastral_evidence_ref": "prot. 9",
                  "cadastral_evidence_identity": "L307||5|61|2"}
    out = _js(MODELLO, f"""[
      m.unitSourcesPatch({json.dumps(base)}, {{surface_sqm: '65', surface_source: 'declared', cadastral_category: 'A/2', cadastral_category_source: 'verified'}}),
      m.unitSourcesPatch({json.dumps(base)}, {{surface_sqm: '65', surface_source: 'declared', cadastral_category: 'A/2', cadastral_category_source: 'verified',
                                               cadastral_evidence_type: 'visura', cadastral_evidence_date: '2026-10-01', cadastral_evidence_ref: ''}}, {{today: '2026-10-11'}}),
      m.unitSourcesPatch({json.dumps(base)}, {{surface_sqm: '65', surface_source: 'declared', cadastral_category: 'A/2', cadastral_category_source: 'verified',
                                               cadastral_evidence_type: 'visura', cadastral_evidence_date: '2027-01-01'}}, {{today: '2026-10-11'}}).errors,
      m.unitSourcesPatch({json.dumps(verificata)}, {{surface_sqm: '65', surface_source: 'declared', cadastral_category: 'A/2', cadastral_category_source: 'verified'}}),
      m.unitSourcesPatch({json.dumps(verificata)}, {{surface_sqm: '65', surface_source: 'declared', cadastral_category: 'A/2', cadastral_category_source: 'verified'}}, {{identityChanged: true}}).errors,
      m.verificationState({json.dumps(verificata)}),
      m.verificationState({{...{json.dumps(verificata)}, cadastral_category_source: null, cadastral_subunit: '3'}}),
      m.verificationState({json.dumps(base)}),
      m.evidenceText({json.dumps(verificata)}),
      m.identityKey({{cadastral_municipality_code: 'l307', cadastral_sheet: '5', cadastral_parcel: '61', cadastral_subunit: '2'}}),
    ]""")
    soloCampi, conFonte, futura, invariata, riconferma, stato, daRiconfermare, nessuna, testo, chiave = out
    assert soloCampi["errors"] == ["«Verificata» richiede la fonte consultata: tipo di documento e data."]
    assert conFonte["errors"] == [] and conFonte["patch"] == {
        "cadastral_category_source": "verified", "cadastral_evidence_type": "visura",
        "cadastral_evidence_date": "2026-10-01", "cadastral_evidence_ref": None}
    assert futura == ["La data della consultazione non è valida."]
    assert invariata == {"patch": {}, "errors": []}                            # nulla da riverificare
    assert riconferma == ["«Verificata» richiede la fonte consultata: tipo di documento e data."]
    assert (stato, daRiconfermare, nessuna) == ("verified", "to_reconfirm", None)
    assert testo == "Visura catastale del 01/10/2026 · rif. prot. 9" and chiave == "L307||5|61|2"


@node
def test_m06_procedura_guidata_composizione_facoltativa():
    out = _js("static/os_shell/assets/census/census-model.js", """(() => {
      const s = (x) => ({ ...m.wizardInitialState(), city: 'Tortoreto', ...x });
      const comuni = [{ name: 'Tortoreto', microzones: [], region: 'Abruzzo', province: 'TE' }];
      return [
        m.wizardComposition(s({})),
        m.wizardComposition(s({ comp_residential: '6', comp_other: '1' })),
        m.wizardComposition(s({ comp_residential: '6', comp_commercial: '0', comp_other: '1', comp_pertinenze: '3' })),
        m.wizardStep1Errors(s({ units_unknown: true, comp_residential: '6', comp_commercial: '0', comp_other: '1', comp_pertinenze: '3' }), comuni),
        m.wizardStep1Errors(s({ units_declared: '12', comp_residential: '6', comp_commercial: '0', comp_other: '1', comp_pertinenze: '3' }), comuni),
        m.wizardStep1Errors(s({ comp_residential: '6', comp_commercial: '0', comp_other: '1', comp_pertinenze: '3' }), comuni),
        m.wizardBuildingPayload(s({ units_unknown: true, comp_residential: '6', comp_commercial: '0', comp_other: '1', comp_pertinenze: '3' }), comuni, { clientRequestId: 'k' }),
        m.wizardBuildingPayload(s({ units_unknown: true, comp_residential: '6' }), comuni, { clientRequestId: 'k' }),
      ];
    })()""")
    nulla, parziale, completa, nonSo, diversa, soloComposizione, corpo, corpoParziale = out
    assert nulla["given"] is False
    assert parziale["given"] is True and parziale["complete"] is False and parziale["body"]["set_total"] is False
    assert parziale["body"]["commercial"] is None
    assert completa["sum"] == 10 and completa["body"]["set_total"] is True
    assert nonSo == [] and soloComposizione == []
    assert diversa == ["La somma della composizione (10) non corrisponde alle unità indicate (12)."]
    assert corpo["units_declared"] == 10 and "units_declared" not in corpoParziale


def test_s01_pannello_senza_liste_scritte_a_mano_e_con_escape():
    pannello = (CENSUS / "composition-panel.js").read_text(encoding="utf-8")
    modello = (CENSUS / "composition-model.js").read_text(encoding="utf-8")
    codice = lambda s: "\n".join(r for r in s.splitlines() if not r.lstrip().startswith("//"))  # noqa: E731
    for testo in (codice(pannello), codice(modello)):
        assert "fetch(" not in testo and "/api/" not in testo                   # rotte solo in census-api.js
        assert "'apartment'" not in testo and "'garage'" not in testo            # tipologie dal catalogo del server
    # ogni interpolazione nei template del pannello passa da escapeHtml o da un render gia' sicuro
    markup = "\n".join(r for r in pannello.splitlines() if "<" in r)          # solo le righe che scrivono HTML
    interpolazioni = re.findall(r"\$\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", markup)
    sospette = [i for i in interpolazioni if re.search(r"\b(u|g|r|c|k|x|t|esito|d)\.\w+$", i.strip())
                and "escapeHtml" not in i]
    assert sospette == [], sospette
    api = (CENSUS / "census-api.js").read_text(encoding="utf-8")
    for rotta in ("/composition`", "/units/generate`", "/units/generate/${", "/units/bulk-update`"):
        assert rotta in api, rotta
