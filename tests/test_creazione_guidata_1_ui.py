"""CREAZIONE-GUIDATA-1 - la procedura guidata nella Shell, ESEGUITA (stub DOM).

Controlli UI SIMULATI (node + stub di DOM, `fetch` scriptato) con lo stesso
harness del censimento (tests/test_censimento_4_ui.py). La prova in Chromium
con backend reale e' tests/test_creazione_guidata_1_browser_postgres.py.

  m01      funzioni pure: totale («Non so» != 0, «1» non decide il percorso),
           territorio dal catalogo, candidati, corpo dell'edificio, avvisi
  01       Commerciale: passo 1 obbligatorio (Comune), indicazione sul totale,
           tre percorsi; «Indietro» conserva i dati; «Annulla» non scrive
  02       Censimento, unita' autonoma: scheda singola con il territorio gia'
           compilato, nessun edificio
  03       edificio esistente: candidati (stesso civico prima), «Usa questo»
           senza scritture ne' modifiche, avviso se il totale differisce
  04       una sola unita' in palazzina con totale ignoto, scheda commerciale:
           edificio salvato al passo 4 (rete caduta -> «Riprova» con la stessa
           chiave), poi l'unita' `crm` dalla scheda edificio, barra «salvata»
  05       doppio clic su «Salva edificio» = una sola richiesta
  06       dalla scheda edificio: «+ Aggiungi unita'» senza il passo 1, due
           unita' di fila e contatori riletti
  07       assegnazione: menu agenti solo su scheda commerciale e a chi puo'
           assegnare; mai sulla scheda di censimento
"""
from __future__ import annotations

import json

from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)
from tests.test_censimento_4_ui import (
    CREATA, DETTAGLIO, EDIFICIO, OPZIONI, _modello, _rotte, _run, _scritture, node, rt,
)

RIEPILOGO = {"units_declared": 6, "units_counted": 2, "units_archived": 0, "units_to_complete": 4}
CANDIDATI = {"items": [{**EDIFICIO, "id": 9, "name": "Altro civico", "civic_number": "12", "census_summary": RIEPILOGO},
                       {**EDIFICIO, "census_summary": RIEPILOGO}], "total": 2}
NUOVO = {**DETTAGLIO, "id": 7, "units": [], "replica": False, "similar": []}


def _prima(*voci):
    """Rotte che vincono su quelle di base (la prima che combacia risponde)."""
    return "".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});\n" for m, p, r in voci)


PASSO1 = r"""
async function passo1({ city = 'Tortoreto', zona = '', via = 'Via Roma', civico = '10', unita = '', nonSo = false, nome = '' } = {}) {
  const c = q('#wz-city'); c.value = city; c.dispatch('change'); await wait();
  if (zona) { const z = q('#wz-microzone'); z.value = zona; z.dispatch('change'); }
  campo('#wz-address', via); campo('#wz-civic', civico); campo('#wz-declared', unita); campo('#wz-name', nome);
  if (nonSo) { q('#wz-unknown').checked = true; q('#wz-unknown').dispatch('change'); }
  q('[data-wizard-step="1"]').dispatch('submit'); await wait(); await wait();
}
const passo = () => { const el = D() && D().querySelector('[data-wizard-step]'); return el ? el.dataset.wizardStep : null; };
"""


# ---------------------------------------------------------------------------
# funzioni pure
# ---------------------------------------------------------------------------

@node
def test_m01_totale_territorio_candidati_e_corpo_dell_edificio():
    albero = json.dumps(OPZIONI["territory"])
    out = _modello(f"""(() => {{
      const comuni = m.catalogMunicipalities({albero});
      const s = (x) => ({{ ...m.wizardInitialState(), ...x }});
      return {{
        nonSo: m.wizardDeclared(s({{units_unknown: true, units_declared: '6'}})),
        vuoto: m.wizardDeclared(s({{}})).error !== '',
        uno: m.wizardDeclared(s({{units_declared: '1'}})),
        zero: m.wizardDeclared(s({{units_declared: '0'}})),
        storto: m.wizardDeclared(s({{units_declared: '2.5'}})).error !== '',
        errori: m.wizardStep1Errors(s({{units_unknown: true}}), comuni),
        zonaAltrui: m.wizardStep1Errors(s({{city: 'Alba Adriatica', microzone: 'Lido', units_unknown: true}}), comuni),
        luogo: m.wizardLocation(s({{city: 'Tortoreto', microzone: 'Lido', address: ' Via Roma ', civic_number: '10'}}), comuni),
        cerca: [m.candidateSearch('Via Roma'), m.candidateSearch('Contrada San Giovanni'), m.candidateSearch('')],
        civico: [m.sameCivic('10/B', '10b'), m.sameCivic('10', '12'), m.sameCivic('', '')],
        ordine: m.rankCandidates([{{id: 1, civic_number: '12'}}, {{id: 2, civic_number: '10'}}], '10').map((b) => [b.id, b.same_civic]),
        corpoIgnoto: m.wizardBuildingPayload(s({{city: 'Tortoreto', units_unknown: true, building_name: 'Gabbiano'}}), comuni, {{clientRequestId: 'k'}}),
        corpoNoto: m.wizardBuildingPayload(s({{city: 'Tortoreto', units_declared: '6'}}), comuni, {{clientRequestId: 'k', confirmSimilar: true}}),
        diff: [m.declaredMismatchText(s({{units_declared: '8'}}), {{units_declared: 6}}),
               m.declaredMismatchText(s({{units_unknown: true}}), {{units_declared: 6}}),
               m.declaredMismatchText(s({{units_declared: '6'}}), {{units_declared: 6}})],
      }};
    }})()""")
    assert out["nonSo"] == {"value": None, "error": ""}                  # «Non so»: mai 0 o 1
    assert out["vuoto"] is True and out["storto"] is True
    assert out["uno"] == {"value": 1, "error": ""} and out["zero"] == {"value": 0, "error": ""}
    assert out["errori"] == ["Scegli il Comune."]
    assert out["zonaAltrui"] == ["La microzona non appartiene al Comune scelto."]
    assert out["luogo"] == {"region": "Abruzzo", "province": "TE", "city": "Tortoreto", "microzone": "Lido",
                            "address": "Via Roma", "civic_number": "10"}
    assert out["cerca"] == ["Roma", "San Giovanni", ""]
    assert out["civico"] == [True, False, False]
    assert out["ordine"] == [[2, True], [1, False]]
    assert out["corpoIgnoto"] == {"building_type": "condominio", "client_request_id": "k", "region": "Abruzzo",
                                  "province": "TE", "city": "Tortoreto", "name": "Gabbiano"}
    assert out["corpoNoto"]["units_declared"] == 6 and out["corpoNoto"]["confirm_similar"] is True
    assert "Hai indicato 8 unità" in out["diff"][0] and out["diff"][1:] == ["", ""]


# ---------------------------------------------------------------------------
# Shell eseguita
# ---------------------------------------------------------------------------

@node
def test_01_passo1_percorsi_indietro_e_annulla(staged):  # noqa: F811
    scenario = PASSO1 + r"""
      await wait(); await wait();
      C().querySelector('#immobili-new').dispatch('click'); await wait(); await wait();
      const primo = passo();
      const testo1 = D().visibleText();
      q('[data-wizard-step="1"]').dispatch('submit'); await wait();
      const erroreSenzaComune = q('[data-error]').textContent;
      const rimasto = passo();
      await passo1({ unita: '1', nome: 'Villa?' });
      const percorsi = D().querySelectorAll('[data-path]').map((b) => b.dataset.path);
      const nessunoScelto = D().querySelectorAll('.census-card.active').length;
      q('[data-back]').dispatch('click'); await wait();
      const conservati = { city: q('#wz-city').value, via: q('#wz-address').value, civico: q('#wz-civic').value,
                           unita: q('#wz-declared').value, nome: q('#wz-name').value };
      q('[data-cancel]').dispatch('click'); await wait();
      report({ primo, testo1, erroreSenzaComune, rimasto, percorsi, nessunoScelto, conservati, aperto: !!D(), post: scritture() });
    """
    out = _run(staged, scenario, _rotte(), "#/immobili")
    assert out["primo"] == "1" and "scheda commerciale" in out["testo1"]
    assert "pertinenze con subalterno proprio" in out["testo1"] and "accessori senza sub" in out["testo1"]
    assert "Non so" in out["testo1"] and "Nulla viene salvato" in out["testo1"]
    assert out["erroreSenzaComune"].startswith("Scegli il Comune") and out["rimasto"] == "1"
    assert out["percorsi"] == ["autonomous", "building", "single"]
    assert out["nessunoScelto"] == 0                     # «1» non sceglie «autonoma» al posto dell'operatore
    assert out["conservati"] == {"city": "Tortoreto", "via": "Via Roma", "civico": "10", "unita": "1", "nome": "Villa?"}
    assert out["aperto"] is False and out["post"] == []


@node
def test_02_censimento_unita_autonoma_con_territorio_gia_compilato(staged):  # noqa: F811
    scenario = PASSO1 + r"""
      await wait(); await wait();
      C().querySelector('#immobili-mode-tabs').querySelectorAll('.tab-btn').find((b) => b.dataset.mode === 'census').dispatch('click'); await wait();
      C().querySelector('#census-new').dispatch('click'); await wait(); await wait();
      await passo1({ zona: 'Lido', via: 'Contrada Colle', civico: '3', nonSo: true });
      q('[data-path="autonomous"]').dispatch('click'); await wait(); await wait();
      const foglio = { titolo: q('.census-sheet-title').textContent, nota: q('[data-record-kind]').dataset.recordKind,
                       comune: q('#us-city').value, zona: q('#us-microzone').value, via: q('#us-address').value, civico: q('#us-civic').value };
      q('[data-back]').dispatch('click'); await wait();
      const tornato = passo();
      q('[data-path="autonomous"]').dispatch('click'); await wait(); await wait();
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      report({ foglio, tornato, post: scritture() });
    """
    out = _run(staged, scenario, _rotte(), "#/immobili")
    assert out["foglio"] == {"titolo": "Nuovo immobile singolo", "nota": "census", "comune": "Tortoreto", "zona": "Lido",
                             "via": "Contrada Colle", "civico": "3"}
    assert out["tornato"] == "2"
    post = _scritture(out)
    assert [c["url"] for c in post] == ["/api/property/census/units"]
    corpo = post[0]["body"]
    assert "building_id" not in corpo and "record_kind" not in corpo and "assigned_agent_id" not in corpo
    assert {k: corpo[k] for k in ("region", "province", "city", "microzone", "address", "civic_number")} == {
        "region": "Abruzzo", "province": "TE", "city": "Tortoreto", "microzone": "Lido", "address": "Contrada Colle", "civic_number": "3"}
    assert out["hash"] == "#/immobili/413"


@node
def test_03_edificio_esistente_usato_senza_scritture(staged):  # noqa: F811
    scenario = PASSO1 + r"""
      await wait(); await wait();
      C().querySelector('#immobili-mode-tabs').querySelectorAll('.tab-btn').find((b) => b.dataset.mode === 'census').dispatch('click'); await wait();
      C().querySelector('#census-new').dispatch('click'); await wait(); await wait();
      await passo1({ unita: '8' });
      q('[data-path="building"]').dispatch('click'); await wait(); await wait();
      const ricerca = q('#wz-building-search').value;
      const candidati = D().querySelectorAll('[data-candidate]').map((li) => ({ id: li.dataset.candidate, testo: li.visibleText() }));
      const crea = q('[data-create-building]').textContent;
      q('[data-use-building="7"]').dispatch('click'); await wait();
      const pronto = q('[data-building-ready]').visibleText();
      const differenza = q('[data-declared-mismatch]') ? q('[data-declared-mismatch]').visibleText() : null;
      q('[data-add-units]').dispatch('click'); await window._fire('hashchange'); await wait(); await wait(); await wait();
      report({ ricerca, candidati, crea, pronto, differenza, foglio: !!(D() && D().querySelector('[data-unit-form]')),
               nota: D() && D().querySelector('[data-record-kind]') ? D().querySelector('[data-record-kind]').dataset.recordKind : null,
               letture: chiamate().filter((c) => c.m === 'GET' && c.url.startsWith('/api/property/buildings?')).map((c) => c.url),
               post: scritture() });
    """
    out = _run(staged, scenario, _prima(("GET", "/api/property/buildings?", [rt.ok(CANDIDATI)])) + _rotte(), "#/immobili")
    assert out["ricerca"] == "Roma"
    assert any("city=Tortoreto" in u and "search=Roma" in u for u in out["letture"])
    assert [c["id"] for c in out["candidati"]] == ["7", "9"]                       # stesso civico prima
    assert "Stesso civico" in out["candidati"][0]["testo"] and "Dichiarate 6" in out["candidati"][0]["testo"]
    assert out["crea"] == "Nessuno di questi: crea un edificio distinto"
    assert "Edificio esistente: nessun dato modificato" in out["pronto"]
    assert "Hai indicato 8 unità" in out["differenza"] and "resta invariato" in out["differenza"]
    assert out["hash"].startswith("#/edifici/7/aggiungi")                         # la scheda edificio con il foglio aperto
    assert out["foglio"] is True and out["nota"] == "census"
    assert out["post"] == []                                                      # usare un edificio non scrive nulla


@node
def test_04_una_unita_in_palazzina_totale_ignoto_scheda_commerciale(staged):  # noqa: F811
    scenario = PASSO1 + r"""
      await wait(); await wait();
      C().querySelector('#immobili-new').dispatch('click'); await wait(); await wait();
      await passo1({ via: 'Via Nuova', civico: '1', nonSo: true });
      q('[data-path="single"]').dispatch('click'); await wait(); await wait();
      const nessuno = D().querySelector('[data-candidates]').visibleText();
      q('[data-create-building]').dispatch('click'); await wait();
      const riepilogo = q('dl.building-facts').visibleText();
      q('[data-save-building]').dispatch('click'); await wait(); await wait();
      const errore = q('[data-error]').textContent;
      const testoBottone = q('[data-save-building]').textContent;
      q('[data-save-building]').dispatch('click'); await wait(); await wait();
      const pronto = q('[data-building-ready]').visibleText();
      const indietro = !!q('[data-change-building]');
      q('[data-add-units]').dispatch('click'); await window._fire('hashchange'); await wait(); await wait(); await wait();
      const modo = C().querySelector('#unit-add-mode') ? C().querySelector('#unit-add-mode').visibleText() : null;
      const nota = q('[data-record-kind]').dataset.recordKind;
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait(); await wait();
      const barra = C().querySelector('#unit-saved-bar');
      report({ nessuno, riepilogo, errore, bottone: testoBottone, pronto, indietro, modo, nota,
               barra: barra && !barra.hidden ? barra.visibleText() : null,
               apri: barra ? barra.querySelector('[data-open-saved]').getAttribute('href') : null, post: scritture() });
    """
    vuota = ("GET", "/api/property/buildings?", [rt.ok({"items": [], "total": 0})])
    out = _run(staged, scenario, _prima(vuota) + _rotte(
        post_building=({"throw": True}, {"status": 201, "body": NUOVO}),
        post_unit=({"status": 201, "body": {**CREATA, "record_kind": "crm"}},)), "#/immobili")
    assert "Nessun edificio trovato" in out["nessuno"]
    assert "Unità dichiarateNon note" in out["riepilogo"]
    assert "«Riprova» non ne crea un secondo" in out["errore"] and out["bottone"] == "Riprova"
    assert "Salvato ora" in out["pronto"] and out["indietro"] is False
    assert "schede commerciali" in out["modo"] and out["nota"] == "crm"
    post = _scritture(out)
    assert [c["url"] for c in post] == ["/api/property/buildings", "/api/property/buildings", "/api/property/census/units"]
    assert post[0]["body"]["client_request_id"] == post[1]["body"]["client_request_id"]     # stessa chiave nel ritentativo
    assert "units_declared" not in post[0]["body"]                                       # «Non so»: ne' 0 ne' 1
    assert "confirm_similar" not in post[0]["body"]                                      # nessun candidato mostrato
    unita = post[2]["body"]
    assert unita["building_id"] == 7 and unita["record_kind"] == "crm" and "address" not in unita   # indirizzo ereditato
    assert "IMM-413 salvata (scheda commerciale)" in out["barra"] and "Aggiungi un’altra unità" in out["barra"]
    assert out["apri"] == "#/immobili/413"


@node
def test_05_doppio_clic_su_salva_edificio_una_sola_richiesta(staged):  # noqa: F811
    scenario = PASSO1 + r"""
      await wait(); await wait();
      C().querySelector('#immobili-mode-tabs').querySelectorAll('.tab-btn').find((b) => b.dataset.mode === 'census').dispatch('click'); await wait();
      C().querySelector('#census-new').dispatch('click'); await wait(); await wait();
      await passo1({ unita: '4' });
      q('[data-path="building"]').dispatch('click'); await wait(); await wait();
      q('[data-create-building]').dispatch('click'); await wait();
      const b = q('[data-save-building]'); b.dispatch('click'); b.dispatch('click'); await wait(); await wait();
      // tornare indietro dopo il salvataggio non crea un secondo edificio
      report({ dopo: passo(), post: scritture() });
    """
    vuota = ("GET", "/api/property/buildings?", [rt.ok({"items": [], "total": 0})])
    out = _run(staged, scenario, _prima(vuota) + _rotte(post_building=({"status": 201, "body": NUOVO},)), "#/immobili")
    assert [c["url"] for c in _scritture(out)] == ["/api/property/buildings"]
    assert out["dopo"] == "5"


@node
def test_06_dalla_scheda_edificio_due_unita_di_fila_e_contatori_riletti(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#unit-add-apartment').dispatch('click'); await wait(); await wait();
      const senzaPasso1 = !D().querySelector('[data-wizard-step]') && !!D().querySelector('[data-unit-form]');
      const etichetta = C().querySelector('#unit-add-apartment').textContent;
      q('[data-submit-another]').dispatch('click'); await wait(); await wait(); await wait();
      const riaperto = !!(D() && D().querySelector('[data-unit-form]'));
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait(); await wait();
      report({ senzaPasso1, etichetta, riaperto, post: scritture(),
               letture: chiamate().filter((c) => c.m === 'GET' && c.url === '/api/property/buildings/7').length,
               barra: C().querySelector('#unit-saved-bar').visibleText() });
    """
    out = _run(staged, scenario, _rotte(post_unit=({"status": 201, "body": CREATA}, {"status": 201, "body": {**CREATA, "id": 414, "code": "IMM-414"}})),
               "#/edifici/7")
    assert out["senzaPasso1"] is True and out["etichetta"] == "+ Aggiungi unità"
    assert out["riaperto"] is True
    post = _scritture(out)
    assert [c["url"] for c in post] == ["/api/property/census/units"] * 2
    assert post[0]["body"]["client_request_id"] != post[1]["body"]["client_request_id"]   # due unita' = due richieste
    assert all("record_kind" not in c["body"] for c in post)                             # dalla scheda: censimento, come prima
    assert out["letture"] >= 3                                                           # contatori riletti dopo ogni salvataggio
    assert "IMM-414 salvata" in out["barra"]


@node
def test_07_assegnazione_solo_su_scheda_commerciale_e_a_chi_puo(staged):  # noqa: F811
    con_agenti = {**OPZIONI, "can_assign": True, "agents": [{"id": 3, "name": "Anna Agente", "role": "agent"},
                                                             {"id": 4, "name": "Bruno Agente", "role": "agent"}]}
    scenario = r"""
      await wait(); await wait();
      const select = D() && D().querySelector('#us-agent');
      const opzioni = select ? select.querySelectorAll('option').map((o) => o.getAttribute('value')) : null;
      if (select) { select.value = '4'; select.dispatch('change'); }
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      report({ opzioni, post: scritture() });
    """
    prima = _prima(("GET", "/api/property/form-options", [rt.ok(con_agenti)]))
    commerciale = _run(staged, scenario, prima + _rotte(), "#/edifici/7/aggiungi/crm")
    assert commerciale["opzioni"] == ["", "3", "4"]
    corpo = _scritture(commerciale)[0]["body"]
    assert corpo["record_kind"] == "crm" and corpo["assigned_agent_id"] == 4
    censimento = _run(staged, scenario, prima + _rotte(), "#/edifici/7/aggiungi/censimento")
    assert censimento["opzioni"] is None and "assigned_agent_id" not in _scritture(censimento)[0]["body"]
    # chi non puo' assegnare (agente) non vede il menu: decide il server (a se stesso)
    agente = _run(staged, scenario, _rotte("agent"), "#/edifici/7/aggiungi/crm")
    assert agente["opzioni"] is None and _scritture(agente)[0]["body"]["record_kind"] == "crm"
