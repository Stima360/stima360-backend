"""CENSIMENTO-1 Fase 4 - l'interfaccia del censimento, ESEGUITA.

Stessa disciplina di CRM-OPS-3/4: i moduli VERI della Shell (`main.js`,
router, `immobili.js`, `edificio-dettaglio.js`, `immobile-dettaglio.js`, i
fogli di `census/`) girano in node dentro lo stub di DOM, con `fetch`
scriptato risposta per risposta. Si prova CIO' CHE PARTE (quali chiamate, con
quale corpo: la chiave di idempotenza riusata nel retry, `confirm_similar`
solo dopo il banner, nessuna scrittura nelle letture), CIO' CHE RESTA SULLO
SCHERMO (gruppi per piano, contatori, badge, toast con «Annulla», banner dei
simili, blocco del duplicato) e CIO' CHE NON COMPARE MAI (nomi di codice del
backend nel testo mostrato).

  A. funzioni pure di census/census-model.js (node, senza DOM)
  B. contratti statici: file ammessi, nessuna lista scritta a mano, rotte
     del censimento nominate solo in census-api.js, mobile, node --check
  C. Shell eseguita: S0 elenco e tab, S1 palazzina (simili, chiave),
     S2/S3/S5 palazzina + unita' + toast/undo, retry di rete e duplicato
     catastale, S6 Duplica, scheda immobile in censimento (tab, guardia,
     presa in carico, pertinenza «Non lo so», «Chiarisci», messaggi)
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
CENSUS_DIR = ASSETS / "census"

from tests import test_a30_5_create_ui as a30_5  # noqa: E402
from tests import test_a30_13b_quick_booking_ui as a30_13b  # noqa: E402
from tests import test_crm_ops_3_acquisitions as base  # noqa: E402
from tests.test_a30_5_create_ui import staged  # noqa: E402,F401  (fixture riusata)

node = pytest.mark.skipif(a30_5.NODE is None, reason="node non disponibile: livello C NON eseguito (BLOCKED)")
UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

# ---------------------------------------------------------------------------
# A - funzioni pure (node, nessun DOM)
# ---------------------------------------------------------------------------

def _modello(expr):
    script = (f"import * as m from '{(CENSUS_DIR / 'census-model.js').as_posix()}';\n"
              f"console.log(JSON.stringify({expr}));")
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


@node
def test_a01_piani_raggruppati_nell_ordine_del_server_con_etichette_leggibili():
    out = _modello("""(() => {
      const u = [{id:1,floor:'-1'},{id:2,floor:'T'},{id:3,floor:'T'},{id:4,floor:'1'},{id:5,floor:'T+1 duplex'},{id:6,floor:null}];
      return { gruppi: m.groupUnitsByFloor(u).map((g) => [g.label, g.units.length]),
               etichette: ['S','R','0','12','-2',''].map(m.floorLabel) };
    })()""")
    assert out["gruppi"] == [["Interrato", 1], ["Terra", 2], ["1º", 1], ["T+1 duplex", 1], ["Piano non indicato", 1]]
    assert out["etichette"] == ["Seminterrato", "Rialzato", "Terra", "12º", "Interrato", "Piano non indicato"]


@node
def test_a02_contatori_e_conferma_propagazione():
    out = _modello("""({
      a: m.countersText({units_census:5, units_main:4, units_pertinenze:1}, 6),
      b: m.countersText({units_census:0}, 6),
      c: m.countersText({units_census:1, units_main:1, units_pertinenze:0}, null),
      p1: m.propagationConfirmText({units_address_inherited:4, units_address_custom:2}),
      p2: m.propagationConfirmText({units_address_inherited:1, units_address_custom:0}),
    })""")
    assert out["a"] == "Censite 5 di 6 dichiarate — 4 principali + 1 pertinenza"
    assert out["b"] == "Censite 0 di 6 dichiarate"
    assert out["c"] == "Censite 1 — 1 principale + 0 pertinenze"
    assert out["p1"] == "Aggiorno 4 unità; 2 con ingresso personalizzato restano come sono."
    assert out["p2"] == "Aggiorno 1 unità."


@node
def test_a03_payload_unita_solo_campi_valorizzati_indirizzo_solo_se_proprio_e_duplica():
    out = _modello("""(() => {
      const base = { client_request_id: 'k', building_id: 7, property_type: 'apartment', floor: '2', staircase: '',
        internal_number: ' 4 ', surface_sqm: '85', rooms: '', bathrooms: '1', cadastral_category: '',
        internal_notes: '', own_address: false, region: 'Abruzzo', province: 'TE', city: 'Tortoreto', address: 'Via X', civic_number: '3' };
      return { ereditato: m.buildUnitPayload(base), proprio: m.buildUnitPayload({ ...base, own_address: true, confirm_similar: true }),
               seme: m.duplicateSeed({ id: 412, property_type: 'apartment', floor: '2', staircase: 'B', internal_number: '2', surface_sqm: '85.00',
                                       rooms: 4, bathrooms: 1, cadastral_category: 'A/3', cadastral_subunit: '4' }),
               edificio: m.buildBuildingPayload({ client_request_id: 'k2', city: 'Tortoreto', address: 'Via Roma', civic_number: '10', units_declared: '6', units_declared_source: 'survey', name: '' }) };
    })()""")
    assert out["ereditato"] == {"property_type": "apartment", "client_request_id": "k", "building_id": 7, "floor": "2",
                                "internal_number": "4", "surface_sqm": 85, "bathrooms": 1}
    assert out["proprio"]["address"] == "Via X" and out["proprio"]["city"] == "Tortoreto" and out["proprio"]["confirm_similar"] is True
    assert "address" not in out["ereditato"] and "confirm_similar" not in out["ereditato"]
    # decisione 4: copia tipologia/piano/scala/mq/locali/bagni; vuoti interno, categoria, catasto
    assert out["seme"] == {"property_type": "apartment", "floor": "2", "staircase": "B", "surface_sqm": "85.00", "rooms": 4, "bathrooms": 1}
    assert out["edificio"] == {"building_type": "condominio", "client_request_id": "k2", "city": "Tortoreto", "address": "Via Roma",
                               "civic_number": "10", "units_declared_source": "survey", "units_declared": 6}


@node
def test_a04_categorie_a_gruppi_ricerca_suggerimenti_e_messaggi_senza_nomi_tecnici():
    out = _modello("""(() => {
      const cat = [{code:'A/2',group:'A',label:'Abitazioni di tipo civile'},{code:'A/5',group:'A',label:'ultrapopolare',historical:true},
                   {code:'C/6',group:'C',label:'Stalle, scuderie, rimesse, autorimesse'},{code:'F/1',group:'F',label:'Aree urbane',no_income:true}];
      return { gruppi: m.categoryGroups(cat).map((g) => [g.label, g.items.map((c) => c.code)]),
               cerca: m.filterCategories(cat, 'c6').map((c) => c.code), parola: m.filterCategories(cat, 'autorim').map((c) => c.code),
               sugg: m.suggestionsFor({garage:{suggested:['C/6'],secondary:['C/7']}}, cat, 'garage').suggested.map((c) => c.code),
               msg: ['IDEMPOTENCY_KEY_REUSED','CENSUS_LOCKED','UNDO_NOT_POSSIBLE','ALREADY_RESOLVED','NETWORK'].map((code) => m.errorMessage({ code })),
               dup: m.errorMessage({ code: 'CADASTRAL_DUPLICATE', existing: { id: 405, code: 'IMM-405' } }),
               toast: m.createdToastText({ code: 'IMM-412', floor: '2' }), simile: m.similarText({ code: 'IMM-402', floor: '2', internal_number: '2' }) };
    })()""")
    assert out["gruppi"] == [["Gruppo A", ["A/2"]], ["Gruppo C", ["C/6"]], ["Storiche", ["A/5"]], ["Stati particolari (senza rendita)", ["F/1"]]]
    assert out["cerca"] == ["C/6"] and out["parola"] == ["C/6"] and out["sugg"] == ["C/6"]
    for testo in out["msg"] + [out["dup"]]:
        assert not re.search(r"[A-Z]{3,}_[A-Z_]+", testo), testo
    assert out["msg"][2] == "Non più annullabile: apri la scheda." and "IMM-405" in out["dup"]
    assert out["toast"] == "IMM-412 · 2º piano aggiunta" and out["simile"] == "Simile a IMM-402 (2º piano, int. 2)"


# ---------------------------------------------------------------------------
# B - contratti statici
# ---------------------------------------------------------------------------

FILE_FASE_4 = (
    "static/os_shell/assets/census/census-api.js",
    "static/os_shell/assets/census/census-model.js",
    "static/os_shell/assets/census/census-sheets.js",
    "static/os_shell/assets/census/property-census-tab.js",
    "static/os_shell/assets/views/edificio-dettaglio.js",
)


def _testo(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_b01_le_rotte_del_censimento_sono_nominate_solo_dal_client_proprio():
    """`/buildings`, `/census/units`, `.../pertinenze`, `.../accessories`,
    `take-in-charge`, `undo-create` compaiono in un solo file JS della Shell."""
    frammenti = ("/buildings", "/census/units", "/pertinenze", "/accessories", "take-in-charge", "undo-create")
    for f in ASSETS.rglob("*.js"):
        testo = f.read_text(encoding="utf-8")
        rel = f.relative_to(ROOT).as_posix()
        if rel == "static/os_shell/assets/census/census-api.js":
            for fr in frammenti:
                assert fr in testo, fr
            continue
        # i commenti possono citare i contratti; le CHIAMATE (stringhe e template) no
        codice = re.sub(r"/\*\*[\s\S]*?\*/", "", testo)
        codice = "\n".join(r for r in codice.splitlines() if not r.strip().startswith("//"))
        for fr in frammenti:
            assert not re.search(r"[`'\"][^`'\"\n]*" + re.escape(fr), codice), (rel, fr)


def test_b02_nessuna_lista_scritta_a_mano_cataloghi_solo_da_form_options():
    for rel in FILE_FASE_4:
        testo = _testo(rel)
        for vietato in ("'cantina'", "'Tortoreto'", "'A/2'", "'C/6'", "'survey'", "'villa'", "'posto_auto'"):
            # i valori del catalogo non compaiono nel browser: le liste arrivano da form-options
            assert vietato not in testo, (rel, vietato)
        # `condominio` solo come ripiego del default dello schema (`|| 'condominio'`), mai in una lista
        for m in re.finditer(r"'condominio'", testo):
            assert testo[max(0, m.start() - 3):m.start()] == "|| ", (rel, testo[max(0, m.start() - 20):m.end()])
    api = _testo("static/os_shell/assets/census/census-api.js")
    codice = "\n".join(r for r in re.sub(r"/\*\*[\s\S]*?\*/", "", api).splitlines() if not r.strip().startswith("//"))
    assert "credentials: 'include'" in codice and "Authorization" not in codice and "agency_id" not in codice
    assert "sessionExpired" in codice
    sheets = _testo("static/os_shell/assets/census/census-sheets.js")
    assert "fetch(" not in sheets and "loadFormOptions" not in sheets    # i fogli non chiamano nulla: le opzioni ARRIVANO, le API passano dal client


def test_b03_main_js_nessuna_voce_di_menu_nuova_e_la_palazzina_dentro_immobili():
    main = _testo("static/os_shell/assets/main.js")
    inizio = main.index("const SECTIONS = [")
    sezioni = main[inizio:main.index("];", inizio)]
    assert "censimento" not in sezioni.lower() and "edific" not in sezioni.lower()
    assert "if (params[0] === 'edifici') return renderEdificioDettaglio(container, params.slice(1));" in main
    assert main.count("registerRoute('immobili'") == 1


def test_b04_css_mobile_e_solo_regole_nuove():
    css = _testo("static/os_shell/assets/app.css")
    blocco = css.split("/* CENSIMENTO-1 Fase 4:")[1]
    assert "@media (max-width: 767px)" in blocco
    for regola in ("dialog.modal.census-sheet { margin: auto 0 0 0; width: 100vw;", ".census-floor-head { position: sticky;",
                   ".census-sheet .modal-actions .btn { flex: 1; min-height: 44px; }", ".chip {"):
        assert regola in blocco, regola
    # nessuna ridefinizione di regole esistenti nel blocco
    prima = css.split("/* CENSIMENTO-1 Fase 4:")[0]
    for selettore in re.findall(r"^(\.[a-z0-9_-]+(?:\s[^{]*)?)\s*\{", blocco, flags=re.M):
        assert f"{selettore} {{" not in prima, selettore


def test_b05_tutti_i_file_della_fase_passano_node_check():
    if a30_5.NODE is None:
        pytest.skip("node non disponibile")
    for rel in FILE_FASE_4 + ("static/os_shell/assets/views/immobili.js", "static/os_shell/assets/views/immobile-dettaglio.js",
                              "static/os_shell/assets/main.js"):
        esito = subprocess.run([a30_5.NODE, "--check", str(ROOT / rel)], capture_output=True, text=True)
        assert esito.returncode == 0, (rel, esito.stderr)


def test_b06_form_options_porta_le_etichette_dei_chips_e_coincide_con_gli_enum():
    from property import catalog, schemas
    assert set(catalog.BUILDING_TYPE_LABELS) == set(schemas.BUILDING_TYPES)
    assert set(catalog.UNITS_DECLARED_SOURCE_LABELS) == set(schemas.UNITS_DECLARED_SOURCES)
    assert set(catalog.ACCESSORY_KIND_LABELS) == set(schemas.ACCESSORY_KINDS)
    etichette = catalog.census_labels_for_form()
    assert [x["value"] for x in etichette["building_types"]] == list(schemas.BUILDING_TYPES)
    assert [x["value"] for x in etichette["accessory_kinds"]] == list(schemas.ACCESSORY_KINDS)
    assert [x["value"] for x in etichette["units_declared_sources"]] == list(schemas.UNITS_DECLARED_SOURCES)
    assert all(x["label"] and x["label"] != x["value"] for lista in etichette.values() for x in lista if x["value"] != "altro")
    sorgente = _testo("property/service.py")
    assert "**census_labels_for_form()" in sorgente


# ---------------------------------------------------------------------------
# C - Shell eseguita
# ---------------------------------------------------------------------------

rt = base.a30_5._rt()

OPZIONI = {
    "territory": [{"name": "Abruzzo", "provinces": [{"code": "TE", "name": "Teramo",
                   "municipalities": [{"name": "Tortoreto", "microzones": ["Lido"]}, {"name": "Alba Adriatica", "microzones": []}]}]}],
    "energy_classes": ["A4", "G"],
    "property_types": [{"value": "apartment", "label": "Appartamento"}, {"value": "commercial", "label": "Locale commerciale"},
                       {"value": "garage", "label": "Garage / box"}, {"value": "storage", "label": "Cantina / Deposito"},
                       {"value": "building", "label": "Stabile"}, {"value": "other", "label": "Altro"}],
    "cadastral_categories": [{"code": "A/2", "group": "A", "label": "Abitazioni di tipo civile", "historical": False, "no_income": False},
                             {"code": "A/3", "group": "A", "label": "Abitazioni di tipo economico", "historical": False, "no_income": False},
                             {"code": "A/5", "group": "A", "label": "Ultrapopolare (soppressa)", "historical": True, "no_income": False},
                             {"code": "C/2", "group": "C", "label": "Magazzini e locali di deposito", "historical": False, "no_income": False},
                             {"code": "C/6", "group": "C", "label": "Autorimesse", "historical": False, "no_income": False},
                             {"code": "F/3", "group": "F", "label": "In corso di costruzione", "historical": False, "no_income": True}],
    "cadastral_suggestions": {"apartment": {"suggested": ["A/2", "A/3"], "secondary": ["A/5"]}, "storage": {"suggested": ["C/2"], "secondary": []},
                              "garage": {"suggested": ["C/6"], "secondary": []}},
    "building_types": [{"value": "condominio", "label": "Palazzina / Condominio"}, {"value": "villa", "label": "Villa"}],
    "units_declared_sources": [{"value": "survey", "label": "Sopralluogo"}, {"value": "cadastre", "label": "Visura"}, {"value": "unknown", "label": "Non so"}],
    "accessory_kinds": [{"value": "cantina", "label": "Cantina"}, {"value": "box", "label": "Box"}, {"value": "posto_auto", "label": "Posto auto"}],
    "can_assign": False, "agents": [],
}
EDIFICIO = {"id": 7, "agency_id": 7, "name": "Palazzina via Roma 10", "building_type": "condominio", "region": "Abruzzo", "province": "TE",
            "city": "Tortoreto", "address": "Via Roma", "civic_number": "10", "units_declared": 6, "units_declared_source": "survey",
            "census_status": "partial", "notes": None, "metadata": {}, "updated_at": "2026-10-03T10:00:00Z",
            "units_census": 2, "accessories_unknown": 1}
UNITA = [
    {"id": 410, "code": "IMM-410", "property_type": "commercial", "floor": "T", "staircase": None, "internal_number": None,
     "surface_sqm": "45.00", "cadastral_category": "C/1", "parent_property_id": None, "building_id": 7, "record_kind": "census",
     "address_inherited": True, "accessories_unknown": 0, "commercial_status": "draft"},
    {"id": 411, "code": "IMM-411", "property_type": "apartment", "floor": "1", "staircase": None, "internal_number": "1",
     "surface_sqm": None, "cadastral_category": None, "parent_property_id": None, "building_id": 7, "record_kind": "census",
     "address_inherited": False, "accessories_unknown": 1, "commercial_status": "draft"},
    {"id": 412, "code": "IMM-412", "property_type": "apartment", "floor": "2", "staircase": "B", "internal_number": "2",
     "surface_sqm": "85.00", "cadastral_category": "A/3", "rooms": 4, "bathrooms": 1, "parent_property_id": None, "building_id": 7,
     "record_kind": "census", "address_inherited": True, "accessories_unknown": 0, "commercial_status": "draft"},
]
DETTAGLIO = {**EDIFICIO, "counters": {"units_census": 3, "units_main": 3, "units_pertinenze": 0, "units_address_inherited": 2,
                                      "units_address_custom": 1, "accessories_unknown": 1}, "units": UNITA}
CREATA = {**UNITA[2], "id": 413, "code": "IMM-413", "internal_number": "3", "replica": False, "similar": []}
IMMOBILE_CENSUS = {**base.IMMOBILE, "record_kind": "census", "commercial_status": "draft", "building_id": 7, "parent_property_id": None,
                   "address_inherited": True, "floor": "2", "staircase": "B", "internal_number": "2", "cadastral_category": None,
                   "cadastral_municipality_code": None, "cadastral_section": None, "cadastral_sheet": None, "cadastral_parcel": None,
                   "cadastral_subunit": None, "mandate_type": None, "acquisition_id": None, "contacts": [], "leads": [], "photos": [],
                   "documents": [], "visits": []}
CENSUS = {"id": 30, "code": "IMM-30", "record_kind": "census", "address_inherited": True, "whole_building": False,
          "building": {"id": 7, "name": "Palazzina via Roma 10", "building_type": "condominio", "city": "Tortoreto", "address": "Via Roma",
                       "civic_number": "10", "units_declared": 6},
          "parent": None,
          "pertinenze": [{"id": 414, "code": "IMM-414", "property_type": "garage", "surface_sqm": "15.00", "cadastral_category": "C/6",
                          "record_kind": "census", "commercial_status": "draft"}],
          "accessories": [{"id": 5, "property_id": 30, "kind": "cantina", "cadastral_status": "unknown", "surface_sqm": "6.00", "notes": None}],
          "accessories_unknown": 1}


def _rotte(sessione="agency_owner", *, post_building=None, post_unit=None, undo=None, resolve=None, take=None, accessory=None,
           immobile=None, census=None):
    post_building = post_building or ({"status": 201, "body": {**DETTAGLIO, "replica": False, "similar": []}},)
    post_unit = post_unit or ({"status": 201, "body": CREATA},)
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
        ("GET", "/api/property/form-options", [rt.ok(OPZIONI)]),
        ("GET", "/api/property/buildings/7", [rt.ok(DETTAGLIO)]),
        ("GET", "/api/property/buildings", [rt.ok({"items": [EDIFICIO]})]),
        ("POST", "/api/property/buildings", list(post_building)),
        ("PATCH", "/api/property/buildings/7", [rt.ok({**DETTAGLIO, "civic_number": "12", "propagated_units": 2, "custom_units": 1})]),
        ("POST", "/api/property/census/units", list(post_unit)),
        ("POST", "/api/property/properties/413/undo-create", list(undo or ({"status": 200, "body": {**CREATA, "commercial_status": "archived"}},))),
        ("POST", "/api/property/properties/30/undo-create", list(undo or ({"status": 200, "body": {**IMMOBILE_CENSUS, "commercial_status": "archived"}},))),
        ("POST", "/api/property/properties/30/take-in-charge", list(take or ({"status": 200, "body": {**IMMOBILE_CENSUS, "record_kind": "crm", "pertinenze_taken": [414]}},))),
        ("POST", "/api/property/properties/30/accessories/5/resolve", list(resolve or ({"status": 200, "body": {"accessory": None, "pertinenza": {**UNITA[2], "id": 415, "code": "IMM-415"}, "replica": False}},))),
        ("POST", "/api/property/properties/30/accessories", list(accessory or ({"status": 201, "body": {"id": 6, "kind": "cantina", "cadastral_status": "unknown", "replica": False}},))),
        ("POST", "/api/property/properties/30/pertinenze/414/unlink", [rt.ok({"property_id": 30, "pertinenza": {"id": 414}})]),
        ("POST", "/api/property/properties/30/pertinenze/link", [rt.ok({"property_id": 30, "pertinenza": {"id": 416}, "linked": True})]),
        ("GET", "/api/property/properties/30/census", [rt.ok(census or CENSUS)]),
        ("GET", "/api/property/properties/30", [rt.ok(immobile or IMMOBILE_CENSUS)]),
        ("PATCH", "/api/property/properties/30", [rt.ok({**(immobile or IMMOBILE_CENSUS), "cadastral_category": "A/2"})]),
        ("DELETE", "/api/property/properties/411", [rt.ok({**UNITA[1], "commercial_status": "archived"})]),
        ("GET", "/api/property/properties?", [rt.ok({"items": [{**base.IMMOBILE, "id": 416, "code": "IMM-416", "property_type": "garage"}]})]),
        ("GET", "/api/proposals?", [rt.ok({"items": []})]),
        ("GET", "/api/sales?", [rt.ok({"items": []})]),
        ("GET", "/api/match/", [rt.ok({"items": []})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


HELPERS = r"""
const CH = (nome, valore) => D().querySelectorAll(`[data-chip="${nome}"]`).find((b) => b.dataset.value === valore);
const chipAttivo = (nome) => (D().querySelectorAll(`[data-chip="${nome}"]`).find((b) => b.classList.contains('active')) || {}).dataset;
const campo = (sel, v, ev = 'input') => { const e = q(sel); e.value = v; e.dispatch(ev); };
const testoToast = () => { const t = C().querySelector('[data-census-toast]'); return t && !t.hidden ? t.visibleText() : null; };
const bottoneToast = () => { const t = C().querySelector('[data-census-toast]'); return t ? t.querySelector('[data-toast-action]') : null; };
async function selezionaTerritorio(prefix) {
  campo(`#${prefix}-region`, 'Abruzzo', 'change'); await wait();
  campo(`#${prefix}-province`, 'TE', 'change'); await wait();
  campo(`#${prefix}-city`, 'Tortoreto', 'change'); await wait();
}
"""


def _run(staged, scenario, rotte, hash):  # noqa: F811
    driver = staged.parent / "driver-censimento-4.mjs"
    driver.write_text(
        a30_5._dom() + rt.FETCH + a30_13b._extra_dom() + a30_5.IS_CONNECTED + a30_5.ROUTED_FETCH
        + f"\n{rotte}\n" + f"window.location.hash = '{hash}';\n"
        + f"await import('{(staged / 'main.js').as_posix()}');\n" + "await __settle(40);\n"
        + a30_5.HELPERS + base.D_HELPERS + HELPERS + scenario + "\n", encoding="utf-8")
    esito = subprocess.run([a30_5.NODE, str(driver)], capture_output=True, text=True, timeout=60,
                           cwd=staged.parent, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    if esito.returncode != 0:
        raise AssertionError(f"driver node fallito:\n{esito.stderr[-3000:]}\n{esito.stdout[-1500:]}")
    return json.loads(esito.stdout.strip().splitlines()[-1])


def _scritture(out):
    return [c for c in out["calls"] if c["m"] in ("POST", "PATCH", "PUT", "DELETE")]


@node
def test_c01_s0_elenco_con_due_tab_la_tab_censimento_elenca_le_palazzine(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const tabs = C().querySelector('#immobili-mode-tabs').querySelectorAll('.tab-btn').map((b) => b.textContent.trim());
      const primaDellaTab = chiamate().filter((c) => c.url.startsWith('/api/property/buildings')).length;
      C().querySelector('#immobili-mode-tabs').querySelectorAll('.tab-btn').find((b) => b.dataset.mode === 'census').dispatch('click'); await wait(); await wait();
      const censusVisibile = !C().querySelector('#immobili-census-panel').hidden;
      const crmNascosto = C().querySelector('#immobili-crm-panel').hidden;
      const righe = C().querySelector('#census-list-area').querySelectorAll('tr.row-clickable').map((r) => r.dataset.rowId);
      C().querySelector('#census-new').dispatch('click'); await wait();
      const card = C().querySelectorAll('.census-card').map((b) => b.textContent.trim());
      C().querySelector('#census-list-area').querySelector('tr.row-clickable').dispatch('click'); await wait();
      report({ tabs, primaDellaTab, censusVisibile, crmNascosto, righe, card, contenuto: C().visibleText() });
    """
    out = _run(staged, scenario, _rotte(), "#/immobili")
    assert out["tabs"] == ["Commerciale", "Censimento"]
    assert out["primaDellaTab"] == 0                     # le palazzine si leggono solo aprendo la tab
    assert out["censusVisibile"] and out["crmNascosto"] and out["righe"] == ["7"]
    assert any("Immobile singolo" in c for c in out["card"]) and any("Palazzina" in c for c in out["card"])
    assert "Palazzina via Roma 10" in out["contenuto"] and "2 di 6" in out["contenuto"]
    assert "Garage / box" in out["contenuto"] and "garage" not in out["contenuto"].replace("Garage", "")   # etichetta italiana nell'elenco commerciale (decisione 1)
    assert out["hash"] == "#/immobili/edifici/7"
    assert _scritture(out) == []


@node
def test_c02_s1_nuova_palazzina_simili_salva_comunque_stessa_chiave(staged):  # noqa: F811
    simili = {"status": 409, "body": {"detail": "Edifici simili", "code": "SIMILAR_FOUND",
                                      "similar": [{"id": 7, "name": "Palazzina via Roma 10", "address": "Via Roma", "civic_number": "10", "city": "Tortoreto"}]}}
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#immobili-mode-tabs').querySelectorAll('.tab-btn').find((b) => b.dataset.mode === 'census').dispatch('click'); await wait();
      C().querySelector('#census-new').dispatch('click'); await wait();
      C().querySelector('#census-new-building').dispatch('click'); await wait(); await wait();
      const aperto = !!D();
      await selezionaTerritorio('bs');
      campo('#bs-address', 'Via Roma'); campo('#bs-civic', '10'); campo('#bs-declared', '6');
      CH('units_declared_source', 'survey').dispatch('click');
      q('[data-building-form]').dispatch('submit'); await wait(); await wait();
      const banner = D().querySelector('[data-similar-banner]') ? D().querySelector('[data-similar-banner]').visibleText() : null;
      const ancoraAperto = !!(D() && D()._open);
      D().querySelector('[data-save-anyway]').dispatch('click'); await wait(); await wait();
      report({ aperto, banner, ancoraAperto, post: scritture() });
    """
    out = _run(staged, scenario, _rotte(post_building=(simili, {"status": 201, "body": {**DETTAGLIO, "replica": False, "similar": []}})), "#/immobili")
    assert out["aperto"] and out["ancoraAperto"]
    assert "Possibile doppione" in out["banner"] and "Palazzina via Roma 10" in out["banner"]
    post = out["post"]
    assert [c["url"] for c in post] == ["/api/property/buildings", "/api/property/buildings"]
    primo, secondo = post[0]["body"], post[1]["body"]
    assert UUID4.match(primo["client_request_id"]) and secondo["client_request_id"] == primo["client_request_id"]
    assert "confirm_similar" not in primo and secondo["confirm_similar"] is True
    assert primo["city"] == "Tortoreto" and primo["region"] == "Abruzzo" and primo["province"] == "TE"
    assert primo["address"] == "Via Roma" and primo["civic_number"] == "10" and primo["units_declared"] == 6
    assert primo["units_declared_source"] == "survey" and primo["building_type"] == "condominio"
    assert "agency_id" not in primo and all(c["cred"] == "include" and "Authorization" not in c["headers"] for c in post)
    assert out["hash"] == "#/immobili/edifici/7"


@node
def test_c03_s2_palazzina_per_piano_contatori_e_s3_s5_unita_con_toast_e_annulla(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const piani = C().querySelectorAll('.census-floor-head').map((h) => h.visibleText().trim());
      const contatori = C().querySelector('#building-counters').visibleText();
      const badgeDaChiarire = C().querySelectorAll('.census-unit-row').map((r) => r.visibleText());
      C().querySelector('#unit-add-apartment').dispatch('click'); await wait(); await wait();
      const titolo = D().querySelector('.census-sheet-title').textContent;
      const tipoAttivo = chipAttivo('property_type').value;
      CH('floor', '2').dispatch('click');
      campo('#us-internal', '3'); campo('#us-surface', '85');
      q('[data-submit-another]').dispatch('click'); await wait(); await wait(); await wait();
      const toast = testoToast();
      const riaperto = !!(D() && D()._open);
      const pianoPreselezionato = chipAttivo('floor').value;
      q('[data-cancel]').dispatch('click'); await wait();
      bottoneToast().dispatch('click'); await wait(); await wait();
      report({ piani, contatori, badgeDaChiarire, titolo, tipoAttivo, toast, riaperto, pianoPreselezionato, post: scritture(),
               letture: chiamate().filter((c) => c.m === 'GET' && c.url === '/api/property/buildings/7').length, toastDopo: testoToast() });
    """
    out = _run(staged, scenario, _rotte(), "#/immobili/edifici/7")
    assert out["piani"] == ["Terra 1", "1º 1", "2º 1"]
    assert "Censite 3 di 6 dichiarate — 3 principali + 0 pertinenze" in out["contatori"] and "1 da chiarire" in out["contatori"]
    assert any("IMM-411" in r and "Da verificare" in r and "1 da chiarire" in r and "Ingresso proprio" in r for r in out["badgeDaChiarire"])
    assert any("IMM-412" in r and "A/3" in r and "int. 2" in r and "scala B" in r and "85" in r for r in out["badgeDaChiarire"])
    assert out["titolo"] == "Nuova unità" and out["tipoAttivo"] == "apartment"
    assert out["toast"].startswith("IMM-413 · 2º piano aggiunta") and "Annulla" in out["toast"]
    assert out["riaperto"] and out["pianoPreselezionato"] == "2"          # «Salva e aggiungine un'altra»: piano conservato
    post = out["post"]
    assert post[0]["url"] == "/api/property/census/units"
    corpo = post[0]["body"]
    assert corpo["building_id"] == 7 and corpo["property_type"] == "apartment" and corpo["floor"] == "2"
    assert corpo["internal_number"] == "3" and corpo["surface_sqm"] == 85 and UUID4.match(corpo["client_request_id"])
    assert "address" not in corpo and "city" not in corpo               # indirizzo ereditato: non viaggia
    assert post[1]["url"] == "/api/property/properties/413/undo-create"
    assert out["letture"] >= 3                                          # palazzina riletta dopo la creazione e dopo l'annullamento
    assert "annullata" in (out["toastDopo"] or "")


@node
def test_c04_retry_di_rete_stessa_chiave_poi_duplicato_catastale_bloccante(staged):  # noqa: F811
    duplicato = {"status": 409, "body": {"detail": "Questo subalterno e' gia' censito nella tua agenzia", "code": "CADASTRAL_DUPLICATE",
                                         "existing": {"id": 405, "code": "IMM-405"}}}
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#unit-add-apartment').dispatch('click'); await wait(); await wait();
      CH('floor', '1').dispatch('click');
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      const dopoRete = { aperto: !!(D() && D()._open), errore: q('[data-error]').textContent, bottone: q('[data-submit]').textContent };
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      const blocco = D().querySelector('[data-duplicate-banner]') ? D().querySelector('[data-duplicate-banner]').visibleText() : null;
      const salvaComunque = !!D().querySelector('[data-save-anyway]');
      D().querySelector('[data-open-duplicate]').dispatch('click'); await wait();
      report({ dopoRete, blocco, salvaComunque, post: scritture() });
    """
    out = _run(staged, scenario, _rotte(post_unit=({"throw": True}, duplicato)), "#/immobili/edifici/7")
    assert out["dopoRete"]["aperto"] and out["dopoRete"]["bottone"] == "Riprova"
    assert "Connessione assente" in out["dopoRete"]["errore"]
    assert "già censito" in out["blocco"] and "IMM-405" in out["blocco"] and not out["salvaComunque"]
    post = out["post"]
    assert len(post) == 2 and post[0]["body"]["client_request_id"] == post[1]["body"]["client_request_id"]
    assert out["hash"] == "#/immobili/405"


@node
def test_c05_s6_duplica_precompila_senza_interno_categoria_e_catasto_e_s4_categoria(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      C().querySelectorAll('[data-duplicate-unit]').find((b) => b.dataset.duplicateUnit === '412').dispatch('click'); await wait(); await wait();
      const titolo = D().querySelector('.census-sheet-title').textContent;
      const stato = { tipo: chipAttivo('property_type').value, piano: chipAttivo('floor').value, scala: q('#us-staircase').value,
                      interno: q('#us-internal').value, mq: q('#us-surface').value, locali: q('#us-rooms').value,
                      categoria: q('[data-category-btn]').visibleText().trim(), sub: q('#us-subunit').value };
      q('[data-category-btn]').dispatch('click'); await wait();
      const picker = C().querySelectorAll('dialog').find((d) => d._open && d.querySelector('[data-category-picker]'));
      const suggerite = picker.querySelectorAll('.form-field').map((f) => f.visibleText().trim()).filter((t) => t.startsWith('Suggerite'));
      picker.querySelector('#cat-search').value = 'c6'; picker.querySelector('#cat-search').dispatch('input'); await wait();
      const cercate = picker.querySelector('[data-category-results]').querySelectorAll('[data-pick-category]').map((b) => b.dataset.pickCategory);
      picker.querySelectorAll('[data-pick-category]').find((b) => b.dataset.pickCategory === 'A/2').dispatch('click'); await wait();
      const categoriaScelta = q('[data-category-btn]').visibleText().trim();
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      report({ titolo, stato, suggerite, cercate, categoriaScelta, post: scritture() });
    """
    out = _run(staged, scenario, _rotte(), "#/immobili/edifici/7")
    assert out["titolo"] == "Copia di IMM-412 — completa interno e categoria"
    assert out["stato"]["tipo"] == "apartment" and out["stato"]["piano"] == "2" and out["stato"]["scala"] == "B"
    assert out["stato"]["mq"] == "85.00" and out["stato"]["locali"] == "4"
    assert out["stato"]["interno"] == "" and out["stato"]["categoria"] == "Da verificare ›" and out["stato"]["sub"] == ""
    assert out["suggerite"] and "A/2" in out["suggerite"][0] and "A/3" in out["suggerite"][0]
    assert out["cercate"] == ["C/6"] and out["categoriaScelta"].startswith("A/2")
    corpo = out["post"][0]["body"]
    assert corpo["cadastral_category"] == "A/2" and corpo["floor"] == "2" and corpo["staircase"] == "B" and "internal_number" not in corpo


@node
def test_c06_scheda_in_censimento_tab_guardia_e_presa_in_carico_con_pertinenze(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait();
      const tabs = C().querySelectorAll('.tab-btn').map((b) => b.textContent.trim());
      const attiva = C().querySelectorAll('.tab-btn').find((b) => b.classList.contains('active')).dataset.tab;
      const badge = C().querySelector('#property-status-badge').visibleText();
      const tabCens = C().querySelector('#property-tab-content').visibleText();
      C().querySelectorAll('.tab-btn').find((b) => b.dataset.tab === 'panoramica').dispatch('click'); await wait();
      const panoramica = C().querySelector('#property-tab-content');
      const cambiaStato = !!panoramica.querySelector('#commercial-status-edit-btn');
      const avvisoCensus = panoramica.querySelector('#commercial-status-census') ? panoramica.querySelector('#commercial-status-census').textContent : null;
      const incarico = panoramica.querySelector('#incarico-census') ? panoramica.querySelector('#incarico-census').textContent : null;
      C().querySelectorAll('.tab-btn').find((b) => b.dataset.tab === 'censimento').dispatch('click'); await wait(); await wait();
      C().querySelector('#census-take').dispatch('click'); await wait();
      const dialogo = D().visibleText();
      const spunta = q('[data-include-pertinenze]');
      q('[data-take-form]').dispatch('submit'); await wait(); await wait();
      report({ tabs, attiva, badge, tabCens, cambiaStato, avvisoCensus, incarico, dialogo, spuntata: spunta.checked === true, post: scritture() });
    """
    out = _run(staged, scenario, _rotte(), "#/immobili/30")
    assert out["tabs"][0] == "Censimento" and out["attiva"] == "censimento"
    assert "Censimento" in out["badge"] and "Indirizzo ereditato" in out["badge"]
    for atteso in ("Scheda in censimento", "Prendi in carico", "Annulla creazione", "Palazzina via Roma 10", "Ereditato dalla palazzina",
                   "Da verificare", "IMM-414", "Garage / box", "C/6", "Cantina", "6.00 m²", "Da chiarire", "Chiarisci", "+ Aggiungi pertinenza", "Collega esistente"):
        assert atteso in out["tabCens"], atteso
    assert not out["cambiaStato"] and "Prendi in carico" in out["avvisoCensus"] and "in censimento" in out["incarico"]
    assert "Porti IMM-30 nel lavoro commerciale?" in out["dialogo"] and "IMM-414" in out["dialogo"] and out["spuntata"]
    post = out["post"]
    assert post == [{**post[0], "url": "/api/property/properties/30/take-in-charge", "body": {"include_pertinenze": True}}]
    assert not re.search(r"[A-Z]{3,}_[A-Z_]+", out["tabCens"])


@node
def test_c07_pertinenza_non_lo_so_poi_chiarisci_separata_e_compresa(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait();
      C().querySelector('#census-add-pertinenza').dispatch('click'); await wait();
      const prima = q('[data-submit]').disabled;
      CH('answer', 'unknown').dispatch('click'); await wait();
      const hint = q('[data-accessory-hint]').textContent;
      q('[data-pertinenza-form]').dispatch('submit'); await wait();
      const senzaTipo = q('[data-error]').textContent;
      CH('kind', 'cantina').dispatch('click'); campo('#pa-surface', '6');
      q('[data-pertinenza-form]').dispatch('submit'); await wait(); await wait();
      const toast1 = testoToast();
      // «Chiarisci» -> E' separata, crea
      C().querySelectorAll('[data-resolve]')[0].dispatch('click'); await wait();
      CH('outcome', 'separate').dispatch('click'); await wait();
      CH('property_type', 'storage').dispatch('click');
      q('[data-resolve-form]').dispatch('submit'); await wait(); await wait();
      const toast2 = testoToast();
      // «Chiarisci» -> E' compresa (la tab e' stata riletta: stesso accessorio nel doppio)
      C().querySelectorAll('[data-resolve]')[0].dispatch('click'); await wait();
      CH('outcome', 'included').dispatch('click'); await wait();
      q('[data-resolve-form]').dispatch('submit'); await wait(); await wait();
      report({ prima, hint, senzaTipo, toast1, toast2, post: scritture() });
    """
    out = _run(staged, scenario, _rotte(resolve=({"status": 200, "body": {"accessory": None, "pertinenza": {**UNITA[2], "id": 415, "code": "IMM-415"}, "replica": False}},
                                                 {"status": 200, "body": {"accessory": {"id": 5, "cadastral_status": "included"}, "pertinenza": None, "replica": False}})),
               "#/immobili/30")
    assert out["prima"] is True and "Da chiarire" in out["hint"] and "cantina" in out["senzaTipo"]
    assert "da chiarire" in out["toast1"]
    assert "IMM-415" in out["toast2"]
    post = out["post"]
    assert [c["url"] for c in post] == ["/api/property/properties/30/accessories", "/api/property/properties/30/accessories/5/resolve",
                                        "/api/property/properties/30/accessories/5/resolve"]
    assert post[0]["body"]["kind"] == "cantina" and post[0]["body"]["cadastral_status"] == "unknown" and post[0]["body"]["surface_sqm"] == 6
    assert UUID4.match(post[0]["body"]["client_request_id"])
    assert post[1]["body"]["outcome"] == "separate" and post[1]["body"]["property_type"] == "storage" and UUID4.match(post[1]["body"]["client_request_id"])
    assert post[2]["body"] == {"outcome": "included"}          # nessuna chiave: il backend la rifiuta su «compresa»


@node
def test_c08_scheda_commerciale_tab_pertinenze_senza_guardia_e_messaggi_leggibili(staged):  # noqa: F811
    gia = {"status": 409, "body": {"detail": "Non piu' annullabile: apri la scheda", "code": "UNDO_NOT_POSSIBLE"}}
    scenario = r"""
      await wait(); await wait(); await wait();
      const tabs = C().querySelectorAll('.tab-btn').map((b) => b.textContent.trim());
      const attiva = C().querySelectorAll('.tab-btn').find((b) => b.classList.contains('active')).dataset.tab;
      const cambiaStato = !!C().querySelector('#commercial-status-edit-btn');
      C().querySelectorAll('.tab-btn').find((b) => b.dataset.tab === 'censimento').dispatch('click'); await wait(); await wait();
      const tab = C().querySelector('#property-tab-content').visibleText();
      report({ tabs, attiva, cambiaStato, tab, banner: !!C().querySelector('[data-census-state]') });
    """
    crm = {**IMMOBILE_CENSUS, "record_kind": "crm", "commercial_status": "active"}
    out = _run(staged, scenario, _rotte(immobile=crm, census={**CENSUS, "record_kind": "crm"}, undo=(gia,)), "#/immobili/30")
    assert out["tabs"][:2] == ["Panoramica", "Pertinenze"] and out["attiva"] == "panoramica" and out["cambiaStato"]
    assert not out["banner"] and "IMM-414" in out["tab"] and "Prendi in carico" not in out["tab"]

    # in censimento, «Annulla creazione» rifiutato dal server: frase leggibile, nessun nome di codice
    scenario2 = r"""
      await wait(); await wait(); await wait();
      C().querySelector('#census-undo').dispatch('click'); await wait();
      C().querySelector('#census-undo').dispatch('click'); await wait(); await wait();
      report({ toast: testoToast(), post: scritture() });
    """
    out2 = _run(staged, scenario2, _rotte(undo=(gia,)), "#/immobili/30")
    assert out2["toast"] == "Non più annullabile: apri la scheda."
    assert [c["url"] for c in out2["post"]] == ["/api/property/properties/30/undo-create"]


@node
def test_c09_modifica_palazzina_indirizzo_conferma_propagazione_e_archivia_unita(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#building-edit').dispatch('click'); await wait(); await wait();
      campo('#bs-civic', '12');
      q('[data-building-form]').dispatch('submit'); await wait();
      const avviso = D().querySelector('[data-propagation-banner]') ? D().querySelector('[data-propagation-banner]').visibleText() : null;
      const primaDellaConferma = scritture().length;
      D().querySelector('[data-confirm-propagation]').dispatch('click'); await wait(); await wait();
      const toast = testoToast();
      C().querySelectorAll('[data-archive-unit]').find((b) => b.dataset.archiveUnit === '411').dispatch('click'); await wait();
      const testoBottone = C().querySelectorAll('[data-archive-unit]').find((b) => b.dataset.archiveUnit === '411').textContent;
      C().querySelectorAll('[data-archive-unit]').find((b) => b.dataset.archiveUnit === '411').dispatch('click'); await wait(); await wait();
      report({ conferma: avviso, primaDellaConferma, toast, testoBottone, post: scritture() });
    """
    out = _run(staged, scenario, _rotte(), "#/immobili/edifici/7")
    assert out["conferma"].startswith("Aggiorno 2 unità; 1 con ingresso personalizzato resta")
    assert out["primaDellaConferma"] == 0
    assert "2 unità" in out["toast"] and "1 con ingresso proprio" in out["toast"]
    assert "Confermi" in out["testoBottone"]
    post = out["post"]
    assert post[0]["m"] == "PATCH" and post[0]["url"] == "/api/property/buildings/7" and post[0]["body"] == {"civic_number": "12"}
    assert post[1]["m"] == "DELETE" and post[1]["url"] == "/api/property/properties/411"
