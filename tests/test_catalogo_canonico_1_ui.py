"""CATALOGO-CANONICO-1 (FASE D) - la scheda Immobile, ESEGUITA (stub DOM).

Stesso harness del censimento (tests/test_censimento_4_ui.py): moduli VERI
della Shell in node, `fetch` scriptato. La prova con backend e browser veri e'
tests/test_catalogo_canonico_1_browser_postgres.py.

  m01  funzioni pure del pannello: Si'/No, etichette di catalogo, pertinenze
  u01  «Modifica immobile»: Piano, Anno, Stato (catalogo + valore storico
       conservato e non inviato), Mare (Si'/No/Non indicato), Impianti, Altre
       caratteristiche; in modifica viaggia solo cio' che cambia
  u02  Panoramica: «Mare e dotazioni» con le etichette del catalogo
  u03  tab Censimento: «Dal sito Stima360» - stime, da verificare, differenze
       (Applica/Ignora), possibile doppione («Collega» in due clic, «Non e' lo
       stesso»); nessun nome di codice mostrato
  u04  chi non gestisce l'immobile vede la provenienza ma nessun bottone
  u05  accessori: quantita' in creazione e in modifica, badge «Dal sito»
  s01  contratti statici: nessuna lista di valori del sito scritta a mano
"""
from __future__ import annotations

import json
import re
import subprocess

from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)
from tests.test_censimento_4_ui import (
    CENSUS, CENSUS_DIR, IMMOBILE_CENSUS, OPZIONI, ROOT, _rotte, _run, _scritture, a30_5, node, rt,
)

OPZIONI_CC = {**OPZIONI,
              "conditions": [{"value": "nuovo", "label": "Nuovo"}, {"value": "ristrutturato", "label": "Ristrutturato"},
                             {"value": "buono", "label": "Buono"}],
              "sea_positions": [{"value": "frontemare", "label": "Fronte mare"}, {"value": "seconda", "label": "Seconda fila"}],
              "sea_distances": [{"value": "0-100", "label": "0-100 m"}, {"value": "100-300", "label": "100-300 m"}],
              "accessory_kinds": [*OPZIONI["accessory_kinds"], {"value": "balcone", "label": "Balcone"}]}
IMMOBILE = {**IMMOBILE_CENSUS, "floor": "3", "year_built": 1998, "condition": "abitabile (storico)", "elevator": True,
            "sea_position": "seconda", "sea_distance": "100-300", "sea_barrier": False, "sea_view": True,
            "sea_view_detail": "laterale", "sea_band": None, "heating": "Autonomo", "air_conditioning": None,
            "air_conditioning_type": None, "exposure": "Sud", "furnishing": None, "condo_fees": "60.00",
            "other_features": "Finemente arredato", "source": "stima360"}
LABELS = {"rooms": "Locali", "condition": "Stato", "sea_position": "Posizione mare", "surface_sqm": "Superficie (m²)",
          "sea_view": "Vista mare"}
FONTE = {"id": 3, "stima_id": 501, "stima_at": "2026-10-05T10:00:00", "origin": "auto", "status": "active",
         "contact_id": 11, "lead_id": 22, "detail_ids": [9],
         "declared": {"rooms": {"value": 3, "raw": "Trilocale"}, "sea_position": {"value": "seconda", "raw": "seconda"},
                      "sea_view": {"value": True, "raw": "si"},
                      "accessories": {"balcone": {"present": True, "surface_sqm": None, "quantity": 2, "raw": "balconi"}}},
         "unmapped": [{"site_field": "stato", "raw": "abitabile", "reason": "Stato non presente nel catalogo"},
                      {"site_field": "pertinenze", "raw": "posto barca", "reason": "Pertinenza non presente nel catalogo"}],
         "conflicts": [{"id": "c1", "field": "rooms", "site_value": 3, "current_value": 4, "status": "open"},
                       {"id": "c2", "field": "accessory:balcone", "site_value": {"present": True, "quantity": 2},
                        "current_value": {"present": True, "quantity": 3}, "status": "open"},
                       {"id": "c0", "field": "sea_view", "site_value": True, "current_value": False, "status": "ignored"}],
         "duplicates": [{"property_id": 12, "code": "IMM-12", "title": "x", "reasons": ["same_contact", "same_address"], "dismissed": False}],
         "relinked_to": None, "created_at": "2026-10-05T10:00:00Z", "updated_at": "2026-10-05T10:00:00Z"}


def _fonti(can_manage=True, items=None):
    return {"installed": True, "can_manage": can_manage, "labels": LABELS, "items": [FONTE] if items is None else items}


def _prima(*voci):
    return "".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});\n" for m, p, r in voci)


def _base(**kw):
    return _prima(("GET", "/api/property/form-options", [rt.ok(OPZIONI_CC)]),
                  ("GET", "/api/property/properties/30/census", [rt.ok(kw.pop("census", CENSUS))]),
                  ("GET", "/api/property/properties/30/site-sources", list(kw.pop("fonti", [rt.ok(_fonti())]))),
                  *kw.pop("extra", ())) + _rotte(immobile=IMMOBILE, **kw)


def _js(rel, expr):
    script = f"import * as m from '{(ROOT / rel).as_posix()}';\nconsole.log(JSON.stringify({expr}));"
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


@node
def test_m01_funzioni_pure_del_pannello():
    out = _js("static/os_shell/assets/census/site-provenance.js", f"""[
      m.siteValueText('sea_view', false), m.siteValueText('sea_view', null),
      m.siteValueText('condition', 'nuovo', {json.dumps(OPZIONI_CC)}), m.siteValueText('condition', 'abitabile', {json.dumps(OPZIONI_CC)}),
      m.siteValueText('accessory:box', {{present: true, surface_sqm: '18.00', quantity: null}}),
      m.siteValueText('accessory:box', {{present: false}}),
      m.siteFieldLabel('accessory:balcone', {{}}, {json.dumps(OPZIONI_CC)}),
      m.declaredRows({json.dumps(FONTE['declared'])}, {json.dumps(LABELS)}, {json.dumps(OPZIONI_CC)})]""")
    assert out[:7] == ["No", "—", "Nuovo", "abitabile", "presente, 18.00 m²", "non più dichiarata", "Pertinenza: Balcone"]
    righe = {r["label"]: r for r in out[7]}
    assert righe["Locali"] == {"label": "Locali", "text": "3", "raw": "Trilocale"}
    assert righe["Posizione mare"]["text"] == "Seconda fila" and righe["Vista mare"]["text"] == "Sì"
    assert righe["Pertinenza: Balcone"]["text"] == "presente, × 2"


@node
def test_u01_modifica_immobile_campi_del_sito(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait();
      C().querySelector('#property-edit-btn').dispatch('click'); await wait(); await wait();
      const valori = { floor: q('#pf-floor').value, year: q('#pf-year').value, condition: q('#pf-condition').value,
        conditionText: q('#pf-condition').visibleText(), seaPos: q('#pf-sea-position').value, seaView: q('#pf-sea-view').value,
        barrier: q('#pf-sea-barrier').value, fees: q('#pf-condo-fees').value, other: q('#pf-other-features').textContent,
        elevator: q('#pf-elevator').value };
      const testo = D().visibleText();
      campo('#pf-floor', 'Ultimo'); campo('#pf-sea-view', 'false', 'change'); campo('#pf-sea-barrier', 'true', 'change');
      campo('#pf-sea-distance', '0-100', 'change'); campo('#pf-heating', 'Pompa di calore');
      q('#property-form').dispatch('submit'); await wait(); await wait();
      report({ valori, testo, post: scritture() });
    """
    patch = ("PATCH", "/api/property/properties/30", [rt.ok({**IMMOBILE, "floor": "Ultimo"})])
    out = _run(staged, scenario, _base(extra=(patch,)), "#/immobili/30")
    v = out["valori"]
    assert (v["floor"], v["year"], v["seaPos"], v["seaView"], v["barrier"], v["fees"], v["other"], v["elevator"]) == (
        "3", "1998", "seconda", "true", "false", "60.00", "Finemente arredato", "true")
    assert v["condition"] == "abitabile (storico)" and "(valore storico)" in v["conditionText"]
    for etichetta in ("Piano", "Anno di costruzione", "Stato", "Mare", "Posizione", "Distanza dal mare", "Vista mare",
                      "Ferrovia o strada verso il mare", "Impianti e dotazioni", "Riscaldamento", "Spese condominiali",
                      "Altre caratteristiche", "Non indicato"):
        assert etichetta in out["testo"], etichetta
    post = out["post"]
    assert len(post) == 1 and post[0]["url"] == "/api/property/properties/30"
    # solo cio' che e' cambiato; lo stato storico non viaggia («Non indicato» -> null:
    # lo stub non sa svuotare una <select>, lo prova il browser vero)
    assert post[0]["body"] == {"floor": "Ultimo", "sea_view": False, "sea_barrier": True, "sea_distance": "0-100",
                               "heating": "Pompa di calore"}


@node
def test_u02_panoramica_mare_e_dotazioni(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait();
      C().querySelectorAll('.tab-btn').find((b) => b.dataset.tab === 'panoramica').dispatch('click'); await wait(); await wait();
      report({ testo: C().querySelector('#property-site-attributes').visibleText(), tutto: C().querySelector('#property-tab-content').visibleText() });
    """
    out = _run(staged, scenario, _base(), "#/immobili/30")
    for atteso in ("Posizione mare", "Seconda fila", "100-300 m", "Vista mare", "Sì", "laterale", "Autonomo", "Sud", "60.00"):
        assert atteso in out["testo"], atteso
    assert "Finemente arredato" in out["tutto"] and "abitabile (storico)" in out["tutto"]


@node
def test_u03_dal_sito_stima360_differenze_e_doppioni(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait(); await wait();
      const pannello = C().querySelector('#site-provenance');
      const testo = pannello ? pannello.visibleText() : '';
      const bottoni = { applica: C().querySelectorAll('[data-conflict-apply]').length, ignora: C().querySelectorAll('[data-conflict-ignore]').length };
      C().querySelector('[data-conflict-apply="c1"]').dispatch('click'); await wait(); await wait(); await wait();
      C().querySelector('[data-conflict-ignore="c2"]').dispatch('click'); await wait(); await wait();
      C().querySelector('[data-dismiss="12"]').dispatch('click'); await wait(); await wait();
      const collega = C().querySelector('[data-relink-to="12"]');
      collega.dispatch('click'); await wait();
      const testoConferma = C().querySelector('[data-relink-to="12"]').textContent;
      const primaDelSecondo = scritture().length;
      C().querySelector('[data-relink-to="12"]').dispatch('click'); await wait(); await wait();
      report({ testo, bottoni, testoConferma, primaDelSecondo, post: scritture(), hash: window.location.hash });
    """
    rilink = ("POST", "/api/property/properties/30/site-sources/3/relink", [rt.ok({"source_id": 4, "property_id": 12, "written": [], "conflicts": []})])
    conflitti = ("POST", "/api/property/properties/30/site-sources/3/conflicts", [rt.ok({"conflict": {"status": "applied"}})])
    scarta = ("POST", "/api/property/properties/30/site-sources/3/duplicates/12/dismiss", [rt.ok({"duplicates": []})])
    out = _run(staged, scenario, _base(extra=(rilink, conflitti, scarta)), "#/immobili/30")
    t = out["testo"]
    for atteso in ("Dal sito Stima360", "Stima n. 501", "Scheda nata dalla stima", "stima dettagliata", "2 da verificare",
                   "2 differenze", "Locali", "sito: 3", "scheda: 4", "Pertinenza: Balcone", "presente, × 2", "presente, × 3",
                   "Da verificare", "abitabile", "posto barca", "Pertinenza non presente nel catalogo",
                   "Possibile doppione", "IMM-12", "stesso contatto, stesso indirizzo", "Collega questa stima a IMM-12",
                   "Non è lo stesso", "Valori dichiarati", "sito: «Trilocale»", "Seconda fila"):
        assert atteso in t, atteso
    assert "Vista mare" not in t.split("Valori dichiarati")[0]      # il conflitto ignorato non torna fra le differenze
    assert out["bottoni"] == {"applica": 2, "ignora": 2}
    assert not re.search(r"[A-Z]{3,}_[A-Z_]+|same_contact|accessory:", t)
    assert "Confermi" in out["testoConferma"] and out["primaDelSecondo"] == 3
    post = [(c["url"], c["body"]) for c in out["post"]]
    assert post == [
        ("/api/property/properties/30/site-sources/3/conflicts", {"conflict_id": "c1", "action": "apply"}),
        ("/api/property/properties/30/site-sources/3/conflicts", {"conflict_id": "c2", "action": "ignore"}),
        ("/api/property/properties/30/site-sources/3/duplicates/12/dismiss", None),
        ("/api/property/properties/30/site-sources/3/relink", {"target_property_id": 12}),
    ]
    assert out["hash"].startswith("#/immobili/12")


@node
def test_u04_senza_gestione_nessun_bottone_e_pannello_assente_senza_stime(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait(); await wait();
      const p = C().querySelector('#site-provenance');
      report({ c: !!p, testo: p ? p.visibleText() : '', bottoni: C().querySelectorAll('[data-conflict-apply], [data-relink-to], [data-dismiss], [data-relink-code]').length });
    """
    out = _run(staged, scenario, _base(fonti=[rt.ok(_fonti(can_manage=False))]), "#/immobili/30")
    assert out["c"] and "Stima n. 501" in out["testo"] and out["bottoni"] == 0
    vuoto = _run(staged, scenario, _base(fonti=[rt.ok(_fonti(items=[]))]), "#/immobili/30")
    assert vuoto["c"] is False
    assente = _run(staged, scenario, _base(fonti=[{"status": 404, "body": {"detail": "x"}}]), "#/immobili/30")
    assert assente["c"] is False


@node
def test_u05_accessori_quantita_e_badge_dal_sito(staged):  # noqa: F811
    census = {**CENSUS, "accessories": [{"id": 5, "property_id": 30, "kind": "balcone", "cadastral_status": "included",
                                         "surface_sqm": None, "quantity": 2, "notes": None, "source": "stima360"}],
              "accessories_unknown": 0}
    scenario = r"""
      await wait(); await wait(); await wait();
      const lista = C().querySelector('[data-accessory-id="5"]').visibleText();
      C().querySelector('[data-edit-accessory="5"]').dispatch('click'); await wait();
      const quanti = q('#ae-quantity').value;
      campo('#ae-quantity', '3');
      q('[data-accessory-form]').dispatch('submit'); await wait(); await wait();
      C().querySelector('#census-add-pertinenza').dispatch('click'); await wait();
      CH('answer', 'no').dispatch('click'); await wait();
      CH('kind', 'cantina').dispatch('click'); campo('#pa-quantity', '2');
      q('[data-pertinenza-form]').dispatch('submit'); await wait(); await wait();
      report({ lista, quanti, post: scritture() });
    """
    modifica = ("PATCH", "/api/property/properties/30/accessories/5", [rt.ok({"id": 5, "kind": "balcone", "quantity": 3})])
    out = _run(staged, scenario, _base(census=census, extra=(modifica,)), "#/immobili/30")
    assert "Balcone" in out["lista"] and "× 2" in out["lista"] and "Dal sito" in out["lista"] and "Compreso" in out["lista"]
    assert out["quanti"] == "2"
    post = out["post"]
    assert post[0]["url"] == "/api/property/properties/30/accessories/5" and post[0]["body"] == {"quantity": 3}
    assert post[1]["url"] == "/api/property/properties/30/accessories" and post[1]["body"]["quantity"] == 2


def test_s01_nessuna_lista_di_valori_del_sito_scritta_a_mano():
    for rel in ("static/os_shell/assets/census/site-provenance.js", "static/os_shell/assets/components/property-form.js",
                "static/os_shell/assets/views/immobile-dettaglio.js"):
        testo = (ROOT / rel).read_text(encoding="utf-8")
        for vietato in ("'frontemare'", "'ristrutturato'", "'500-1000'", "'balcone'", "'taverna'", "Seconda fila", "'Fronte mare'"):
            assert vietato not in testo, (rel, vietato)
    api = (CENSUS_DIR / "census-api.js").read_text(encoding="utf-8")
    assert "/site-sources" in api
    pannello = (CENSUS_DIR / "site-provenance.js").read_text(encoding="utf-8")
    assert "fetch(" not in pannello and "agency_id" not in pannello
