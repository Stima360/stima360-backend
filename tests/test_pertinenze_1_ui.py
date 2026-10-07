"""PERTINENZE-1 (FASE E) - pertinenze nella Shell, ESEGUITE (stub DOM in node).

Stesso harness del censimento (tests/test_censimento_4_ui.py): moduli VERI
della Shell, `fetch` scriptato. La prova in Chromium con backend e PostgreSQL
veri e' tests/test_pertinenze_1_browser_postgres.py.

  m01  funzioni pure: natura (collegata / da collegare), etichetta del tipo,
       tipologia di partenza, pertinenze dell'edificio, candidate, provenienza,
       payload e riepilogo «(N da collegare)»
  u01  «Aggiungi pertinenza»: tipo, mq con decimali, quantita' (nascosta per
       «Si'»), «Da verificare» -> accessorio da chiarire; «Si'» -> scheda
       collegata con il tipo (box / posto auto distinti) e la tipologia derivata
  u02  scheda edificio: pannello «Pertinenze» collegate / da collegare dalle
       relazioni reali, «Collega a…» con le principali della palazzina,
       «+ Pertinenza» da collegare dopo
  u03  scheda della pertinenza da collegare: natura, provenienza «Dal sito»,
       nessun «Aggiungi pertinenza», «Collega a un'unita' principale»
  u04  scheda della pertinenza collegata: principale (in un altro edificio),
       «Scollega» in due tocchi dal lato della pertinenza
  s01  nessun elenco di tipi scritto a mano nella Shell
"""
from __future__ import annotations

import json
import re
import subprocess

from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)
from tests.test_censimento_4_ui import (
    CENSUS, IMMOBILE_CENSUS, OPZIONI, ROOT, UNITA, _rotte, _run, _scritture, a30_5, node, rt,
)
from tests import test_edifici_1_ui as e1

KINDS = [{"value": "cantina", "label": "Cantina"}, {"value": "box", "label": "Garage / box"},
         {"value": "posto_auto", "label": "Posto auto"}, {"value": "balcone", "label": "Balcone"}]
OPZIONI_P = {**OPZIONI, "accessory_kinds": KINDS,
             "pertinenza_property_types": [{"value": "cantina", "property_type": "storage"},
                                           {"value": "box", "property_type": "garage"},
                                           {"value": "posto_auto", "property_type": "garage"}]}


def _prima(*voci):
    return "".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});\n" for m, p, r in voci)


def _js(rel, expr):
    script = f"import * as m from '{(ROOT / rel).as_posix()}';\nconsole.log(JSON.stringify({expr}));"
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


@node
def test_m01_funzioni_pure():
    unita = [{"id": 1, "parent_property_id": None}, {"id": 2, "parent_property_id": 1, "pertinenza_kind": "box", "is_pertinenza": True},
             {"id": 3, "parent_property_id": None, "is_pertinenza": True, "pertinenza_kind": "posto_auto"},
             {"id": 4, "parent_property_id": None, "whole_building": True}, {"id": 5, "commercial_status": "archived"}]
    out = _js("static/os_shell/assets/census/census-model.js", f"""[
      m.isUnitPertinenza({{id: 3, is_pertinenza: true}}), m.isUnitPertinenza({{id: 1}}), m.isUnitPertinenza({{parent_property_id: 9}}),
      m.pertinenzaKindLabel({{pertinenza_kind: 'posto_auto'}}, {json.dumps(KINDS)}),
      m.pertinenzaPropertyType({json.dumps(OPZIONI_P)}, 'posto_auto'), m.pertinenzaPropertyType({json.dumps(OPZIONI_P)}, 'piscina'),
      m.buildingPertinenze({json.dumps(unita)}),
      m.principalCandidates({json.dumps(unita)}, 3).map((u) => u.id),
      m.fromAccessoryText({{kind: 'box', source: 'stima360', cadastral_status: 'unknown', surface_sqm: '18.50', quantity: null}}, {json.dumps(KINDS)}),
      m.buildUnitPayload({{property_type: 'garage', building_id: 7, is_pertinenza: true, pertinenza_kind: 'box'}}),
      m.buildUnitPayload({{property_type: 'garage', building_id: 7}}),
      m.summaryView({{units_main: 2, units_pertinenze: 3, units_pertinenze_unlinked: 1}}).split,
      m.unitRelationText({{id: 3, is_pertinenza: true}}), m.unitRowBadges({{id: 3, is_pertinenza: true, cadastral_category: 'C/6'}}),
      m.unitFacts({{property_type: 'garage', pertinenza_kind: 'posto_auto', surface_sqm: '12.50'}}, [], {json.dumps(KINDS)})]""")
    assert out[:3] == [True, False, True]
    assert out[3] == "Posto auto" and out[4:6] == ["garage", "other"]
    assert [u["id"] for u in out[6]["linked"]] == [2] and [u["id"] for u in out[6]["unlinked"]] == [3]
    assert out[7] == [1]                                   # niente pertinenze, stabile intero, archiviate
    assert out[8] == "Nata dall’accessorio «Garage / box» (dal sito Stima360, era «Da chiarire», 18.50 m²)"
    assert out[9]["is_pertinenza"] is True and out[9]["pertinenza_kind"] == "box"
    assert "is_pertinenza" not in out[10] and "pertinenza_kind" not in out[10]      # client di prima: corpo identico
    assert out[11] == "2 principali + 3 pertinenze (1 da collegare)"
    assert out[12] == "Pertinenza da collegare a un’unità" and out[13][0]["text"] == "Pertinenza da collegare"
    assert out[14].startswith("Posto auto")


def _scheda_rotte(census=None, extra=()):
    return _prima(("GET", "/api/property/form-options", [rt.ok(OPZIONI_P)]),
                  ("GET", "/api/property/properties/30/census", [rt.ok(census or CENSUS)]),
                  ("GET", "/api/property/properties/30/site-sources", [{"status": 404, "body": {"detail": "x"}}]),
                  *extra) + _rotte()


@node
def test_u01_aggiungi_pertinenza_tipo_mq_quantita_e_sub(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait();
      C().querySelector('#census-add-pertinenza').dispatch('click'); await wait();
      const tipi = D().querySelectorAll('[data-chip="kind"]').map((b) => b.dataset.value);
      const risposte = D().querySelectorAll('[data-chip="answer"]').map((b) => b.textContent.trim());
      // «Da verificare»: accessorio da chiarire, con mq decimali e quantita'
      CH('kind', 'box').dispatch('click'); campo('#pa-surface', '18.5'); campo('#pa-quantity', '1');
      CH('answer', 'unknown').dispatch('click'); await wait();
      const hint = q('[data-accessory-hint]').textContent;
      q('[data-pertinenza-form]').dispatch('submit'); await wait(); await wait();
      // «Si'»: scheda autonoma collegata, tipo posto auto (tipologia garage), quantita' nascosta
      C().querySelector('#census-add-pertinenza').dispatch('click'); await wait();
      CH('kind', 'posto_auto').dispatch('click'); campo('#pa-surface', '12.5'); campo('#pa-notes', 'sotto la rampa');
      CH('answer', 'yes').dispatch('click'); await wait();
      const quantitaNascosta = q('[data-quantity-field]').hidden;
      q('[data-pertinenza-form]').dispatch('submit'); await wait(); await wait();
      const titolo = D().querySelector('.census-sheet-title').textContent;
      const tipologia = chipAttivo('property_type').value;
      const tipoPertinenza = chipAttivo('pertinenza_kind').value;
      const mq = q('#us-surface').value;
      const note = q('#us-notes').textContent;
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      report({ tipi, risposte, hint, quantitaNascosta, titolo, tipologia, tipoPertinenza, mq, note, post: scritture() });
    """
    out = _run(staged, scenario, _scheda_rotte(), "#/immobili/30")
    assert out["tipi"] == ["cantina", "box", "posto_auto", "balcone"]
    assert out["risposte"] == ["Sì", "No", "Da verificare"]
    assert "nessun subalterno inventato" in out["hint"]
    acc = out["post"][0]
    assert acc["url"] == "/api/property/properties/30/accessories"
    assert {k: acc["body"][k] for k in ("kind", "cadastral_status", "surface_sqm", "quantity")} == \
        {"kind": "box", "cadastral_status": "unknown", "surface_sqm": 18.5, "quantity": 1}
    assert out["quantitaNascosta"] is True
    assert "pertinenza di IMM-30" in out["titolo"] and "Posto auto" in out["titolo"]
    assert (out["tipologia"], out["tipoPertinenza"], out["mq"], out["note"]) == ("garage", "posto_auto", "12.5", "sotto la rampa")
    unita = out["post"][1]
    assert unita["url"] == "/api/property/census/units"
    corpo = unita["body"]
    assert (corpo["parent_property_id"], corpo["building_id"], corpo["property_type"], corpo["pertinenza_kind"]) == (30, 7, "garage", "posto_auto")
    assert corpo["surface_sqm"] == 12.5 and corpo["internal_notes"] == "sotto la rampa" and corpo["is_pertinenza"] is True


PRINCIPALE = {**UNITA[1], "id": 412, "code": "IMM-412", "property_type": "apartment", "floor": "2", "parent": None,
              "pertinenze_count": 1, "is_pertinenza": False, "pertinenza": False}
COLLEGATA = {**UNITA[1], "id": 415, "code": "IMM-415", "property_type": "garage", "floor": "-1", "parent_property_id": 412,
             "parent": {"id": 412, "code": "IMM-412", "same_building": True}, "pertinenze_count": 0,
             "is_pertinenza": True, "pertinenza": True, "pertinenza_kind": "posto_auto", "cadastral_category": "C/6"}
SCIOLTA = {**UNITA[1], "id": 416, "code": "IMM-416", "property_type": "garage", "floor": "-1", "parent_property_id": None,
           "parent": None, "pertinenze_count": 0, "is_pertinenza": True, "pertinenza": True, "pertinenza_kind": "box",
           "cadastral_category": None}
SCHEDA_P = {**e1.SCHEDA, "units": [{**UNITA[0], "parent": None, "pertinenze_count": 0}, PRINCIPALE, COLLEGATA, SCIOLTA],
            "census_summary": {**e1.RIEPILOGO, "units_main": 2, "units_pertinenze": 2, "units_pertinenze_unlinked": 1}}


@node
def test_u02_scheda_edificio_pertinenze_collegate_e_da_collegare(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait();
      const pannello = C().querySelector('#building-pertinenze').visibleText();
      const split = C().querySelector('#building-split').textContent;
      const collegate = C().querySelector('[data-pertinenze-linked]').querySelectorAll('[data-pertinenza-row]').map((x) => x.dataset.pertinenzaRow);
      const sciolte = C().querySelector('[data-pertinenze-unlinked]').querySelectorAll('[data-pertinenza-row]').map((x) => x.dataset.pertinenzaRow);
      C().querySelector('[data-link-pertinenza="416"]').dispatch('click'); await wait();
      const candidate = D().querySelectorAll('[data-principal-id]').map((b) => b.dataset.principalId);
      D().querySelector('[data-principal-id="412"]').dispatch('click'); await wait(); await wait();
      // «+ Pertinenza»: da collegare dopo (nessuna principale scelta)
      C().querySelector('#unit-add-pertinenza').dispatch('click'); await wait();
      const titolo = D().querySelector('.census-sheet-title').textContent;
      const opzioni = q('#us-principal').querySelectorAll('option').map((o) => o.value);
      CH('pertinenza_kind', 'cantina').dispatch('click'); await wait();
      const tipologia = chipAttivo('property_type').value;
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      report({ pannello, split, collegate, sciolte, candidate, titolo, opzioni, tipologia, post: scritture() });
    """
    rotte = _prima(("GET", "/api/property/buildings/7", [rt.ok(SCHEDA_P)]),
                   ("GET", "/api/property/form-options", [rt.ok({**e1.OPZIONI_E1, **{k: OPZIONI_P[k] for k in ("accessory_kinds", "pertinenza_property_types")}})]),
                   ("POST", "/api/property/properties/412/pertinenze/link", [rt.ok({"property_id": 412, "pertinenza": {"id": 416}, "linked": True})]),
                   ("POST", "/api/property/census/units", [{"status": 201, "body": {**SCIOLTA, "id": 417, "code": "IMM-417", "pertinenza_kind": "cantina", "replica": False, "similar": []}}])) + e1._rotte()
    out = _run(staged, scenario, rotte, "#/edifici/7")
    assert "Collegate a un’unità (1)" in out["pannello"] and "Da collegare (1)" in out["pannello"]
    assert "Posto auto" in out["pannello"] and "IMM-412" in out["pannello"] and "Garage / box" in out["pannello"]
    assert "(1 da collegare)" in out["split"]
    assert out["collegate"] == ["415"] and out["sciolte"] == ["416"]
    assert out["candidate"] == ["410", "412"]                    # principali della palazzina, non le pertinenze
    post = out["post"]
    assert (post[0]["url"], post[0]["body"]) == ("/api/property/properties/412/pertinenze/link", {"pertinenza_id": 416})
    assert out["titolo"] == "Nuova pertinenza autonoma" and out["opzioni"][0] == "" and "412" in out["opzioni"] and "416" not in out["opzioni"]
    assert out["tipologia"] == "storage"
    corpo = post[1]["body"]
    assert post[1]["url"] == "/api/property/census/units"
    assert (corpo["building_id"], corpo["is_pertinenza"], corpo["pertinenza_kind"], corpo["property_type"]) == (7, True, "cantina", "storage")
    assert "parent_property_id" not in corpo


CENSUS_SCIOLTA = {**CENSUS, "pertinenze": [], "accessories": [], "accessories_unknown": 0, "is_pertinenza": True,
                  "pertinenza_unlinked": True, "pertinenza_kind": "box",
                  "from_accessory": {"accessory_id": 5, "kind": "box", "source": "stima360", "cadastral_status": "unknown",
                                     "surface_sqm": "18.50", "quantity": None},
                  "link_candidates": [{**PRINCIPALE}]}


@node
def test_u03_pertinenza_da_collegare_dalla_sua_scheda(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait();
      const testo = C().querySelector('#property-tab-content').visibleText();
      const aggiungi = !!C().querySelector('#census-add-pertinenza');
      C().querySelector('#census-link-principal').dispatch('click'); await wait();
      const candidate = D().querySelectorAll('[data-principal-id]').map((b) => b.dataset.principalId);
      D().querySelector('[data-principal-id="412"]').dispatch('click'); await wait(); await wait();
      report({ testo, aggiungi, candidate, post: scritture() });
    """
    out = _run(staged, scenario, _scheda_rotte(CENSUS_SCIOLTA, extra=(
        ("POST", "/api/property/properties/412/pertinenze/link", [rt.ok({"property_id": 412, "pertinenza": {"id": 30}, "linked": True})]),)),
        "#/immobili/30")
    t = out["testo"]
    assert "Pertinenza autonoma · Garage / box" in t and "Da collegare" in t
    assert "Nata dall’accessorio «Garage / box» (dal sito Stima360, era «Da chiarire», 18.50 m²)" in t
    assert out["aggiungi"] is False                              # una pertinenza non ha pertinenze
    assert out["candidate"] == ["412"]
    assert [(c["url"], c["body"]) for c in out["post"]] == [("/api/property/properties/412/pertinenze/link", {"pertinenza_id": 30})]


@node
def test_u04_pertinenza_collegata_scollega_dal_suo_lato(staged):  # noqa: F811
    collegata = {**CENSUS_SCIOLTA, "pertinenza_unlinked": False, "from_accessory": None, "link_candidates": [],
                 "parent": {"id": 412, "code": "IMM-412", "title": "x", "property_type": "apartment", "record_kind": "census",
                            "commercial_status": "draft", "archived_at": None, "building_id": 8, "same_building": False}}
    scenario = r"""
      await wait(); await wait(); await wait();
      const testo = C().querySelector('#property-tab-content').visibleText();
      C().querySelector('#census-unlink-self').dispatch('click'); await wait(); await wait();
      const testoConferma = C().querySelector('#census-unlink-self').textContent;
      const primaDellaConferma = scritture().length;
      C().querySelector('#census-unlink-self').dispatch('click'); await wait(); await wait();
      report({ testo, testoConferma, primaDellaConferma, post: scritture() });
    """
    out = _run(staged, scenario, _scheda_rotte(collegata, extra=(
        ("POST", "/api/property/properties/412/pertinenze/30/unlink", [rt.ok({"property_id": 412, "pertinenza": {"id": 30}})]),)),
        "#/immobili/30")
    assert "IMM-412" in out["testo"] and "(in un altro edificio)" in out["testo"] and "Scollega da IMM-412" in out["testo"]
    assert "Resta una pertinenza da collegare" in out["testoConferma"] and out["primaDellaConferma"] == 0
    assert [c["url"] for c in out["post"]] == ["/api/property/properties/412/pertinenze/30/unlink"]


def test_s01_nessun_elenco_di_tipi_scritto_a_mano():
    for rel in ("static/os_shell/assets/census/census-sheets.js", "static/os_shell/assets/census/property-census-tab.js",
                "static/os_shell/assets/views/edificio-dettaglio.js", "static/os_shell/assets/census/census-model.js"):
        testo = (ROOT / rel).read_text(encoding="utf-8")
        for vietato in ("'posto_auto'", "'box'", "'cantina'", "'Posto auto'", "'Garage / box'"):
            assert vietato not in testo, (rel, vietato)
    assert re.search(r"pertinenza_property_types", (ROOT / "static/os_shell/assets/census/census-model.js").read_text(encoding="utf-8"))
