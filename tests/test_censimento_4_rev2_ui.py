"""CENSIMENTO-1 Fase 4 - REV 2: i rilievi R1-R8 della review, RIPRODOTTI.

Ogni test ricostruisce il flusso intero sui moduli VERI della Shell (stesso
stub di DOM e `fetch` scriptato di `test_censimento_4_ui.py`) e fallisce sul
codice della Fase 4 cosi' com'era prima della REV 2:

  R1  sezione catastale a tre stati (non conosciuta / nessuna / valore) in
      creazione, modifica e «Chiarisci»
  R2  pertinenza «Si'» dalla scheda di un'unita' in palazzina: la palazzina
      viaggia nel corpo (building_id), con «Ingresso diverso?» disponibile;
      la principale senza palazzina resta a indirizzo proprio
  R3  «Stabile intero»: whole_building segue la tipologia scelta NEL foglio
  R4  «Salva e aggiungine un'altra» riparte da tipologia, piano e scala della
      riga davvero creata
  R5  il timer del toast precedente non chiude il toast nuovo (timer finti)
  R6  «Apri» nei simili della palazzina (id senza codice immobile) e
      destinazione esatta per edificio e immobile
  R7  invio dall'esito incerto: retry invariato = stessa chiave; retry dopo
      modifica = stessa chiave, poi recupero della riga gia' salvata e
      «Applica le modifiche» come PATCH, mai una seconda creazione
  R8  HTTP 201 con corpo illeggibile = esito incerto (non un successo);
      il 204 legittimo resta null
"""
from __future__ import annotations

import json
import re
import subprocess

import pytest

from tests import test_a30_5_create_ui as a30_5  # noqa: E402
from tests import test_censimento_4_ui as f4  # noqa: E402
from tests.test_a30_5_create_ui import staged  # noqa: E402,F401  (fixture riusata)
from tests.test_censimento_4_ui import (CENSUS, CREATA, DETTAGLIO, IMMOBILE_CENSUS, UUID4,  # noqa: E402
                                        _modello, _rotte, _run, _scritture)

node = f4.node
rt = f4.rt

CREATA_REPLICA = {**CREATA, "replica": True}
CHIAVE_RIUSATA = {"status": 409, "body": {"detail": "chiave gia' usata con dati diversi", "code": "IDEMPOTENCY_KEY_REUSED"}}

# timer finti SOLO per i timeout lunghi (il toast): `wait()` dello stub resta reale
TIMER_FINTI = r"""
const __realSet = globalThis.setTimeout, __realClear = globalThis.clearTimeout;
const __finti = []; let __ora = 0; let __seq = 0;
globalThis.setTimeout = (fn, ms, ...a) => {
  if (ms >= 5000) { const h = { id: ++__seq, at: __ora + ms, fn }; __finti.push(h); return h; }
  return __realSet(fn, ms, ...a);
};
globalThis.clearTimeout = (h) => {
  if (h && typeof h === 'object' && h.id) { const i = __finti.indexOf(h); if (i >= 0) __finti.splice(i, 1); return; }
  return __realClear(h);
};
async function avanza(ms) {
  __ora += ms;
  for (const h of [...__finti].sort((a, b) => a.at - b.at)) {
    if (h.at <= __ora) { __finti.splice(__finti.indexOf(h), 1); h.fn(); }
  }
  await wait();
}
"""


def _corpo_senza_chiave(c):
    return {k: v for k, v in c["body"].items() if k not in ("client_request_id", "confirm_similar")}


# ---------------------------------------------------------------------------
# R1 - sezione catastale: non conosciuta / nessuna / valore
# ---------------------------------------------------------------------------

@node
def test_r1a_payload_unita_sezione_a_tre_stati():
    sconosciuta = _modello("m.buildUnitPayload({property_type: 'apartment', cadastral_section_mode: 'unknown', cadastral_section: 'X'})")
    nessuna = _modello("m.buildUnitPayload({property_type: 'apartment', cadastral_section_mode: 'none', cadastral_section: ''})")
    valore = _modello("m.buildUnitPayload({property_type: 'apartment', cadastral_section_mode: 'value', cadastral_section: ' a '})")
    valore_vuoto = _modello("m.buildUnitPayload({property_type: 'apartment', cadastral_section_mode: 'value', cadastral_section: ''})")
    legacy = _modello("m.buildUnitPayload({property_type: 'apartment', cadastral_section: ''})")
    assert "cadastral_section" not in sconosciuta                 # non conosciuta: il campo non viaggia (NULL)
    assert nessuna["cadastral_section"] == ""                     # nessuna sezione, accertato: stringa vuota
    assert valore["cadastral_section"] == "a"
    assert "cadastral_section" not in valore_vuoto                # «con sezione» ma vuota: NON diventa «nessuna»
    assert "cadastral_section" not in legacy                      # senza il tri-stato: il comportamento di prima


@node
def test_r1b_foglio_unita_nessuna_sezione_viaggia_come_stringa_vuota(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#unit-add-apartment').dispatch('click'); await wait(); await wait();
      const chips = D().querySelectorAll('[data-chip="cadastral_section_mode"]').map((b) => b.dataset.value);
      const iniziale = chipAttivo('cadastral_section_mode').value;
      campo('#us-belfiore', 'A125'); campo('#us-sheet', '12'); campo('#us-parcel', '345'); campo('#us-subunit', '6');
      // 1) non conosciuta (default): la sezione non viaggia
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      // 2) nessuna sezione, accertato
      C().querySelector('#unit-add-apartment').dispatch('click'); await wait(); await wait();
      campo('#us-belfiore', 'A125'); campo('#us-sheet', '12'); campo('#us-parcel', '345'); campo('#us-subunit', '6');
      CH('cadastral_section_mode', 'none').dispatch('click'); await wait();
      const inputNascosto = q('#us-section').hidden || q('#us-section').disabled;
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      // 3) con sezione ma vuota: errore, nessun invio; poi valorizzata
      C().querySelector('#unit-add-apartment').dispatch('click'); await wait(); await wait();
      CH('cadastral_section_mode', 'value').dispatch('click'); await wait();
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      const erroreVuota = q('[data-error]').textContent;
      const inviiDopoVuota = scritture().length;
      campo('#us-section', 'B');
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      report({ chips, iniziale, inputNascosto, erroreVuota, inviiDopoVuota, post: scritture() });
    """
    out = _run(staged, scenario, _rotte(post_unit=({"status": 201, "body": CREATA},)), "#/immobili/edifici/7")
    assert out["chips"] == ["unknown", "none", "value"] and out["iniziale"] == "unknown"
    post = out["post"]
    assert [c["url"] for c in post] == ["/api/property/census/units"] * 3
    assert "cadastral_section" not in post[0]["body"] and post[0]["body"]["cadastral_subunit"] == "6"
    assert post[1]["body"]["cadastral_section"] == "" and out["inputNascosto"]
    assert out["inviiDopoVuota"] == 2 and "sezione" in out["erroreVuota"].lower()
    assert post[2]["body"]["cadastral_section"] == "B"


@node
def test_r1c_modifica_catastale_distingue_null_e_stringa_vuota_anche_in_patch(staged):  # noqa: F811
    nessuna = {**IMMOBILE_CENSUS, "cadastral_section": "", "cadastral_municipality_code": "A125", "cadastral_sheet": "12",
               "cadastral_parcel": "345", "cadastral_subunit": "6"}
    scenario = r"""
      await wait(); await wait(); await wait();
      const riga = C().querySelector('#property-tab-content').visibleText();
      // sezione NULL -> «Nessuna» -> PATCH {cadastral_section: ""}
      C().querySelector('#census-edit-cadastral').dispatch('click'); await wait();
      const form = C().querySelector('#census-cadastral-form');
      const chipIniziale = form.querySelectorAll('[data-chip="cadastral_section_mode"]').find((b) => b.classList.contains('active')).dataset.value;
      form.querySelectorAll('[data-chip="cadastral_section_mode"]').find((b) => b.dataset.value === 'none').dispatch('click'); await wait();
      form.querySelector('#cc-save').dispatch('click'); await wait(); await wait(); await wait();
      const rigaDopo = C().querySelector('#property-tab-content').visibleText();
      // ora la scheda ha sezione '' : riaprire e salvare senza cambiare = nessuna PATCH
      C().querySelector('#census-edit-cadastral').dispatch('click'); await wait();
      const form2 = C().querySelector('#census-cadastral-form');
      const chipDopo = form2.querySelectorAll('[data-chip="cadastral_section_mode"]').find((b) => b.classList.contains('active')).dataset.value;
      form2.querySelector('#cc-save').dispatch('click'); await wait(); await wait();
      const patchInvariata = scritture().length;
      // '' -> «Non conosciuta» -> PATCH {cadastral_section: null}
      C().querySelector('#census-edit-cadastral').dispatch('click'); await wait();
      const form3 = C().querySelector('#census-cadastral-form');
      form3.querySelectorAll('[data-chip="cadastral_section_mode"]').find((b) => b.dataset.value === 'unknown').dispatch('click'); await wait();
      form3.querySelector('#cc-save').dispatch('click'); await wait(); await wait(); await wait();
      report({ riga, chipIniziale, rigaDopo, chipDopo, patchInvariata, post: scritture() });
    """
    patch = ("\n".join([
        f"__route('PATCH', '/api/property/properties/30', {json.dumps({'status': 200, 'body': nessuna})}, "
        f"{json.dumps({'status': 200, 'body': {**nessuna, 'cadastral_section': None}})});",
    ]) + "\n" + _rotte())
    out = _run(staged, scenario, patch, "#/immobili/30")
    assert "Non conosciuta" in out["riga"] and out["chipIniziale"] == "unknown"
    assert "Nessuna" in out["rigaDopo"] and out["chipDopo"] == "none"
    post = out["post"]
    assert out["patchInvariata"] == 1                                # salvare senza cambiare non scrive
    assert [c["url"] for c in post] == ["/api/property/properties/30"] * 2
    assert post[0]["body"] == {"cadastral_section": ""}
    assert post[1]["body"] == {"cadastral_section": None}


@node
def test_r1d_chiarisci_crea_offre_la_sezione_a_tre_stati(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait();
      C().querySelectorAll('[data-resolve]')[0].dispatch('click'); await wait();
      CH('outcome', 'separate').dispatch('click'); await wait();
      const chips = D().querySelectorAll('[data-chip="cadastral_section_mode"]').map((b) => b.dataset.value);
      campo('#rs-belfiore', 'A125'); campo('#rs-sheet', '12'); campo('#rs-parcel', '345'); campo('#rs-subunit', '7');
      CH('cadastral_section_mode', 'none').dispatch('click'); await wait();
      q('[data-resolve-form]').dispatch('submit'); await wait(); await wait();
      report({ chips, post: scritture() });
    """
    out = _run(staged, scenario, _rotte(), "#/immobili/30")
    assert out["chips"] == ["unknown", "none", "value"]
    post = out["post"]
    assert post[0]["url"] == "/api/property/properties/30/accessories/5/resolve"
    assert post[0]["body"]["cadastral_section"] == "" and post[0]["body"]["cadastral_subunit"] == "7"


# ---------------------------------------------------------------------------
# R2 - pertinenza «Si'» conserva la palazzina
# ---------------------------------------------------------------------------

@node
def test_r2_pertinenza_si_porta_building_id_e_ingresso_diverso_disponibile(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait();
      C().querySelector('#census-add-pertinenza').dispatch('click'); await wait();
      CH('answer', 'yes').dispatch('click'); await wait();
      q('[data-pertinenza-form]').dispatch('submit'); await wait(); await wait();
      const titolo = D().querySelector('.census-sheet-title').textContent;
      const spunta = !!q('[data-own-address]');
      const bloccoNascosto = q('[data-address-block]').hidden;
      CH('property_type', 'garage').dispatch('click');
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      report({ titolo, spunta, bloccoNascosto, post: scritture() });
    """
    out = _run(staged, scenario, _rotte(post_unit=({"status": 201, "body": {**CREATA, "id": 417, "code": "IMM-417", "parent_property_id": 30}},)), "#/immobili/30")
    assert "pertinenza di IMM-30" in out["titolo"]
    assert out["spunta"] and out["bloccoNascosto"]                # in palazzina: «Ingresso diverso?» c'e', indirizzo ereditato di default
    post = out["post"]
    assert post[0]["url"] == "/api/property/census/units"
    corpo = post[0]["body"]
    assert corpo["parent_property_id"] == 30 and corpo["building_id"] == 7 and corpo["property_type"] == "garage"
    assert "address" not in corpo and "city" not in corpo


@node
def test_r2b_pertinenza_si_di_principale_senza_palazzina_resta_a_indirizzo_proprio(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait();
      C().querySelector('#census-add-pertinenza').dispatch('click'); await wait();
      CH('answer', 'yes').dispatch('click'); await wait();
      q('[data-pertinenza-form]').dispatch('submit'); await wait(); await wait();
      const spunta = !!q('[data-own-address]');
      const bloccoVisibile = !q('[data-address-block]').hidden;
      await selezionaTerritorio('us');
      campo('#us-address', 'Via Po'); campo('#us-civic', '3');
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      report({ spunta, bloccoVisibile, post: scritture() });
    """
    senza = {**IMMOBILE_CENSUS, "building_id": None, "address_inherited": False}
    out = _run(staged, scenario, _rotte(immobile=senza, census={**CENSUS, "building": None, "address_inherited": False},
                                        post_unit=({"status": 201, "body": {**CREATA, "id": 417, "code": "IMM-417", "parent_property_id": 30, "building_id": None}},)),
               "#/immobili/30")
    assert not out["spunta"] and out["bloccoVisibile"]
    corpo = out["post"][0]["body"]
    assert corpo["parent_property_id"] == 30 and "building_id" not in corpo
    assert corpo["address"] == "Via Po" and corpo["civic_number"] == "3" and corpo["city"] == "Tortoreto"


# ---------------------------------------------------------------------------
# R3 - «Stabile intero»
# ---------------------------------------------------------------------------

@node
def test_r3_stabile_intero_segue_la_tipologia_scelta_nel_foglio(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#unit-add-other').dispatch('click'); await wait();
      C().querySelector('#unit-type-chips').querySelectorAll('[data-add-type]').find((b) => b.dataset.addType === 'building').dispatch('click'); await wait(); await wait();
      const tipoAttivo = chipAttivo('property_type').value;
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      // di nuovo Stabile, poi cambio tipologia dentro il foglio
      C().querySelector('#unit-add-other').dispatch('click'); await wait();
      C().querySelector('#unit-type-chips').querySelectorAll('[data-add-type]').find((b) => b.dataset.addType === 'building').dispatch('click'); await wait(); await wait();
      CH('property_type', 'apartment').dispatch('click');
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      // e il contrario: da Appartamento a Stabile dentro il foglio
      C().querySelector('#unit-add-apartment').dispatch('click'); await wait(); await wait();
      CH('property_type', 'building').dispatch('click');
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      report({ tipoAttivo, post: scritture() });
    """
    stabile = {**CREATA, "property_type": "building", "whole_building": True}
    out = _run(staged, scenario, _rotte(post_unit=({"status": 201, "body": stabile}, {"status": 201, "body": CREATA}, {"status": 201, "body": stabile})), "#/immobili/edifici/7")
    assert out["tipoAttivo"] == "building"
    post = out["post"]
    assert len(post) == 3
    assert post[0]["body"]["property_type"] == "building" and post[0]["body"]["whole_building"] is True and post[0]["body"]["building_id"] == 7
    assert post[1]["body"]["property_type"] == "apartment" and "whole_building" not in post[1]["body"]
    assert post[2]["body"]["property_type"] == "building" and post[2]["body"]["whole_building"] is True


# ---------------------------------------------------------------------------
# R4 - «Salva e aggiungine un'altra» riparte dalla riga creata
# ---------------------------------------------------------------------------

@node
def test_r4_inserimento_consecutivo_riparte_da_tipologia_piano_e_scala_salvati(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#unit-add-apartment').dispatch('click'); await wait(); await wait();
      CH('property_type', 'storage').dispatch('click');
      CH('floor', '-1').dispatch('click'); campo('#us-staircase', 'B');
      q('[data-submit-another]').dispatch('click'); await wait(); await wait(); await wait();
      const riaperto = !!(D() && D()._open);
      const tipo = chipAttivo('property_type').value;
      const piano = chipAttivo('floor').value;
      const scala = q('#us-staircase').value;
      // seconda consecutiva: cambio piano e tipologia, di nuovo «e un'altra»
      CH('property_type', 'garage').dispatch('click'); CH('floor', 'T').dispatch('click');
      q('[data-submit-another]').dispatch('click'); await wait(); await wait(); await wait();
      report({ riaperto, tipo, piano, scala, tipo2: chipAttivo('property_type').value, piano2: chipAttivo('floor').value, post: scritture() });
    """
    cantina = {**CREATA, "id": 420, "code": "IMM-420", "property_type": "storage", "floor": "-1", "staircase": "B"}
    box = {**CREATA, "id": 421, "code": "IMM-421", "property_type": "garage", "floor": "T", "staircase": "B"}
    out = _run(staged, scenario, _rotte(post_unit=({"status": 201, "body": cantina}, {"status": 201, "body": box})), "#/immobili/edifici/7")
    assert out["riaperto"]
    assert out["tipo"] == "storage" and out["piano"] == "-1" and out["scala"] == "B"
    assert out["tipo2"] == "garage" and out["piano2"] == "T"
    post = out["post"]
    assert post[0]["body"]["property_type"] == "storage" and post[0]["body"]["floor"] == "-1" and post[0]["body"]["staircase"] == "B"
    assert post[1]["body"]["property_type"] == "garage" and post[1]["body"]["floor"] == "T" and post[1]["body"]["staircase"] == "B"


# ---------------------------------------------------------------------------
# R5 - timer del toast
# ---------------------------------------------------------------------------

@node
def test_r5_il_toast_nuovo_ha_dieci_secondi_propri_anche_durante_l_azione(staged):  # noqa: F811
    scenario = TIMER_FINTI + r"""
      await wait(); await wait();
      async function crea() {
        C().querySelector('#unit-add-apartment').dispatch('click'); await wait(); await wait();
        q('[data-unit-form]').dispatch('submit'); await wait(); await wait(); await wait();
      }
      await crea();
      const toast1 = testoToast();
      await avanza(9000);
      await crea();                                   // seconda creazione a 9 s
      const toast2 = testoToast();
      await avanza(1500);                             // t = 10.5 s: il timer del primo toast e' scaduto
      const toast2Vivo = testoToast();
      const annullaVivo = !!bottoneToast();
      await avanza(9000);                             // t = 19.5 s: i 10 s propri del secondo sono passati
      const toast2Scaduto = testoToast();
      // sostituzione DURANTE l'azione asincrona: «Annulla» di IMM-415 in corso
      // (risposta trattenuta), arriva il toast di IMM-416, poi l'azione finisce
      const __fetchOrig = globalThis.fetch; let __sblocca = null;
      globalThis.fetch = async (url, options = {}) => {
        const p = __fetchOrig(url, options);
        if (String(url).includes('/415/undo-create')) await new Promise((r) => { __sblocca = r; });
        return p;
      };
      await crea();                                   // IMM-415 a t0 (timer a t0+10 s)
      await avanza(5000);
      bottoneToast().dispatch('click'); await wait();  // «Annulla» trattenuto
      await crea();                                   // IMM-416 a t0+5 s (timer a t0+15 s)
      const toastDuranteAzione = testoToast();
      __sblocca(); await wait(); await wait(); await wait();
      const toastDopoAzione = testoToast();           // «IMM-415 annullata.» a t0+5 s (timer a t0+15 s)
      await avanza(5000);                             // t0+10 s: scade il timer del toast di IMM-415
      const ancoraVivo = testoToast();
      await avanza(5000);                             // t0+15 s
      report({ toast1, toast2, toast2Vivo, annullaVivo, toast2Scaduto, toastDuranteAzione, toastDopoAzione, ancoraVivo, fine: testoToast(), post: scritture() });
    """
    out = _run(staged, scenario,
               "__route('POST', '/api/property/properties/415/undo-create', " + json.dumps({"status": 200, "body": {**CREATA, "id": 415, "code": "IMM-415", "commercial_status": "archived"}}) + ");\n"
               + _rotte(post_unit=({"status": 201, "body": CREATA}, {"status": 201, "body": {**CREATA, "id": 414, "code": "IMM-414"}},
                                   {"status": 201, "body": {**CREATA, "id": 415, "code": "IMM-415"}}, {"status": 201, "body": {**CREATA, "id": 416, "code": "IMM-416"}})),
               "#/immobili/edifici/7")
    assert out["toast1"].startswith("IMM-413") and out["toast2"].startswith("IMM-414")
    assert out["toast2Vivo"] is not None and out["toast2Vivo"].startswith("IMM-414") and out["annullaVivo"]
    assert out["toast2Scaduto"] is None
    assert out["toastDuranteAzione"].startswith("IMM-416")
    assert "IMM-415 annullata" in (out["toastDopoAzione"] or "")
    assert "IMM-415 annullata" in (out["ancoraVivo"] or "")         # il timer scaduto di IMM-415 non chiude il toast nuovo
    assert out["fine"] is None
    assert [c["url"] for c in out["post"] if "undo-create" in c["url"]] == ["/api/property/properties/415/undo-create"]


# ---------------------------------------------------------------------------
# R6 - «Apri» nei simili
# ---------------------------------------------------------------------------

@node
def test_r6_apri_nei_simili_edificio_e_immobile_con_destinazione_esatta(staged):  # noqa: F811
    simili_edificio = {"status": 409, "body": {"detail": "Edifici simili", "code": "SIMILAR_FOUND",
                                               "similar": [{"id": 7, "name": "Palazzina via Roma 10", "address": "Via Roma", "civic_number": "10", "city": "Tortoreto"}]}}
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#immobili-mode-tabs').querySelectorAll('.tab-btn').find((b) => b.dataset.mode === 'census').dispatch('click'); await wait();
      C().querySelector('#census-new').dispatch('click'); await wait();
      C().querySelector('#census-new-building').dispatch('click'); await wait(); await wait();
      await selezionaTerritorio('bs');
      campo('#bs-address', 'Via Roma'); campo('#bs-civic', '10');
      q('[data-building-form]').dispatch('submit'); await wait(); await wait();
      const apri = D().querySelectorAll('[data-open-similar]').map((b) => b.dataset.openSimilar);
      const salvaComunque = !!D().querySelector('[data-save-anyway]');
      D().querySelector('[data-open-similar]').dispatch('click'); await wait(); await wait();
      report({ apri, salvaComunque, post: scritture() });
    """
    out = _run(staged, scenario, _rotte(post_building=(simili_edificio,)), "#/immobili")
    assert out["apri"] == ["7"] and out["salvaComunque"]
    assert out["hash"] == "#/immobili/edifici/7"
    assert len(out["post"]) == 1                                   # «Apri» non scrive

    simili_unita = {"status": 409, "body": {"detail": "Unita' simili", "code": "SIMILAR_FOUND",
                                            "similar": [{"id": 412, "code": "IMM-412", "floor": "2", "internal_number": "2", "reason": "position"}]}}
    scenario2 = r"""
      await wait(); await wait();
      C().querySelector('#unit-add-apartment').dispatch('click'); await wait(); await wait();
      CH('floor', '2').dispatch('click'); campo('#us-internal', '2');
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      const apri = D().querySelectorAll('[data-open-similar]').map((b) => b.dataset.openSimilar);
      const chiave1 = scritture()[0].body.client_request_id;
      D().querySelector('[data-save-anyway]').dispatch('click'); await wait(); await wait();
      const chiave2 = scritture()[1].body.client_request_id;
      report({ apri, chiave1, chiave2, confirm: scritture()[1].body.confirm_similar, post: scritture() });
    """
    out2 = _run(staged, scenario2, _rotte(post_unit=(simili_unita, {"status": 201, "body": CREATA})), "#/immobili/edifici/7")
    assert out2["apri"] == ["412"] and out2["chiave1"] == out2["chiave2"] and out2["confirm"] is True
    scenario3 = r"""
      await wait(); await wait();
      C().querySelector('#unit-add-apartment').dispatch('click'); await wait(); await wait();
      CH('floor', '2').dispatch('click'); campo('#us-internal', '2');
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      D().querySelector('[data-open-similar]').dispatch('click'); await wait(); await wait();
      report({ post: scritture() });
    """
    out3 = _run(staged, scenario3, _rotte(post_unit=(simili_unita,)), "#/immobili/edifici/7")
    assert out3["hash"] == "#/immobili/412" and len(out3["post"]) == 1


# ---------------------------------------------------------------------------
# R7 - esito incerto: retry invariato e retry dopo modifica
# ---------------------------------------------------------------------------

@node
def test_r7a_risposta_persa_retry_invariato_stessa_chiave_una_sola_creazione(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#unit-add-apartment').dispatch('click'); await wait(); await wait();
      CH('floor', '2').dispatch('click'); campo('#us-surface', '85');
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      const avviso = q('[data-uncertain-banner]') ? q('[data-uncertain-banner]').visibleText() : null;
      const testoBottone = q('[data-submit]').textContent;
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait(); await wait();
      report({ avviso, testoBottone, chiuso: !D(), toast: testoToast(), post: scritture() });
    """
    out = _run(staged, scenario, _rotte(post_unit=({"throw": True}, {"status": 200, "body": CREATA_REPLICA})), "#/immobili/edifici/7")
    assert out["avviso"] and "potrebbe" in out["avviso"] and "doppion" in out["avviso"]
    assert out["testoBottone"] == "Riprova"
    post = out["post"]
    assert len(post) == 2 and post[0]["body"]["client_request_id"] == post[1]["body"]["client_request_id"]
    assert _corpo_senza_chiave(post[0]) == _corpo_senza_chiave(post[1])
    assert out["chiuso"] and out["toast"].startswith("IMM-413")


@node
def test_r7b_retry_dopo_modifica_recupera_la_riga_salvata_e_applica_le_modifiche_senza_doppioni(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#unit-add-apartment').dispatch('click'); await wait(); await wait();
      CH('floor', '2').dispatch('click'); campo('#us-surface', '85');
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();      // risposta persa (il server ha salvato)
      campo('#us-surface', '90'); campo('#us-internal', '3');                     // l'operatore modifica e riprova
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait(); await wait();
      const aperto = !!(D() && D()._open);
      const banner = q('[data-recovered-banner]') ? q('[data-recovered-banner]').visibleText() : null;
      const azioni = D().querySelectorAll('[data-recovered-banner] button').map((b) => b.textContent.trim());
      const errore = q('[data-error]').textContent;
      q('[data-apply-changes]').dispatch('click'); await wait(); await wait(); await wait();
      report({ aperto, banner, azioni, errore, chiuso: !D(), toast: testoToast(), post: scritture() });
    """
    out = _run(staged, scenario,
               "__route('PATCH', '/api/property/properties/413', " + json.dumps({"status": 200, "body": {**CREATA, "surface_sqm": "90.00", "internal_number": "3"}}) + ");\n"
               + _rotte(post_unit=({"throw": True}, CHIAVE_RIUSATA, {"status": 200, "body": CREATA_REPLICA})),
               "#/immobili/edifici/7")
    assert out["aperto"] and out["banner"] and "IMM-413" in out["banner"] and "già" in out["banner"]
    assert any("Applica" in a for a in out["azioni"]) and any("Apri" in a for a in out["azioni"])
    assert "dati diversi" not in out["errore"]                      # niente vicolo cieco «conferma di nuovo»
    post = out["post"]
    creazioni = [c for c in post if c["url"] == "/api/property/census/units"]
    patch = [c for c in post if c["m"] == "PATCH"]
    assert len(creazioni) == 3 and len({c["body"]["client_request_id"] for c in creazioni}) == 1
    assert _corpo_senza_chiave(creazioni[0]) == _corpo_senza_chiave(creazioni[2])       # il recupero rimanda il PRIMO corpo
    assert creazioni[1]["body"]["surface_sqm"] == 90 and creazioni[1]["body"]["internal_number"] == "3"
    assert patch == [{**patch[0], "url": "/api/property/properties/413", "body": {"surface_sqm": 90, "internal_number": "3"}}]
    assert out["chiuso"] and out["toast"].startswith("IMM-413")
    assert not re.search(r"[A-Z]{3,}_[A-Z_]+", out["banner"])


# ---------------------------------------------------------------------------
# R8 - 201 con corpo illeggibile
# ---------------------------------------------------------------------------

@node
def test_r8a_request_distingue_errore_prima_degli_header_corpo_interrotto_e_204():
    script = f"""
      globalThis.window = {{ location: {{ hash: '' }}, addEventListener() {{}} }};
      globalThis.document = {{ addEventListener() {{}}, querySelector() {{ return null; }} }};
      const risposte = [];
      globalThis.fetch = async () => {{ const r = risposte.shift(); if (r.throw) throw new TypeError('rete'); return r; }};
      const api = await import('{(f4.CENSUS_DIR / 'census-api.js').as_posix()}');
      const esiti = [];
      async function prova(spec) {{
        risposte.push(spec);
        try {{ const d = await api.createUnit({{ property_type: 'apartment' }}); esiti.push({{ ok: true, data: d }}); }}
        catch (e) {{ esiti.push({{ ok: false, code: e.code, status: e.status, uncertain: e.uncertain === true }}); }}
      }}
      await prova({{ throw: true }});
      await prova({{ status: 201, ok: true, async json() {{ throw new SyntaxError('Unexpected end of JSON input'); }} }});
      await prova({{ status: 204, ok: true, async json() {{ throw new Error('no body'); }} }});
      await prova({{ status: 201, ok: true, async json() {{ return {{ id: 1 }}; }} }});
      await prova({{ status: 409, ok: false, async json() {{ throw new Error('corpo rotto'); }} }});
      console.log(JSON.stringify(esiti));
    """
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert esito.returncode == 0, esito.stderr
    rete, interrotto, vuoto, ok, errore_rotto = json.loads(esito.stdout.strip().splitlines()[-1])
    assert rete == {"ok": False, "code": "NETWORK", "status": 0, "uncertain": True}                 # prima degli header
    assert interrotto == {"ok": False, "code": "RESPONSE_LOST", "status": 201, "uncertain": True}  # dopo HTTP 201
    assert vuoto == {"ok": True, "data": None}                                                       # 204 legittimo
    assert ok == {"ok": True, "data": {"id": 1}}
    assert errore_rotto["ok"] is False and errore_rotto["status"] == 409 and not errore_rotto["uncertain"]


@node
def test_r8b_201_con_corpo_interrotto_non_chiude_il_foglio_e_il_retry_riusa_la_chiave(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#unit-add-apartment').dispatch('click'); await wait(); await wait();
      CH('floor', '2').dispatch('click');
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait();
      const aperto = !!(D() && D()._open);
      const errore = q('[data-error]').textContent;
      const avviso = q('[data-uncertain-banner]') ? q('[data-uncertain-banner]').visibleText() : null;
      const toastPrima = testoToast();
      q('[data-unit-form]').dispatch('submit'); await wait(); await wait(); await wait();
      report({ aperto, errore, avviso, toastPrima, chiuso: !D(), toast: testoToast(), post: scritture() });
    """
    out = _run(staged, scenario, _rotte(post_unit=({"status": 201}, {"status": 200, "body": CREATA_REPLICA})), "#/immobili/edifici/7")
    assert out["aperto"] and out["toastPrima"] is None
    assert "incompleta" in out["errore"] and out["avviso"]
    post = out["post"]
    assert len(post) == 2 and post[0]["body"]["client_request_id"] == post[1]["body"]["client_request_id"]
    assert out["chiuso"] and out["toast"].startswith("IMM-413")
