"""VENDITORI-1 - shell: interruttore «Vende», pagina Venditori, acquisizione precompilata.

Tre livelli, nessun database:

  A  funzioni pure di sellers/seller-model.js (node);
  B  statici: le rotte /api/crm/sellers nominate solo dal client proprio,
     node --check dei file nuovi, nessun nome tecnico nei messaggi;
  C  shell eseguita sullo stub DOM di P26-4/A30-5 (stesso harness di
     CRM-OPS-3 e CENSIMENTO-1 Fase 4), con fetch instradata:
       c01 tab Proprietari: una cella per proprietario (co-proprietari separati,
           l'inquilino no), «Vende?» -> UNA POST /api/crm/sellers;
       c02 censimento: 409 -> dialog -> presa in carico ESISTENTE poi Vende;
       c03 lead ambigui: scelta esplicita (lead o nuovo), mai indovinata;
       c04 Smetti: sospesa/non vende/errore, nessuna DELETE;
       c05 Venditori: UNA GET per la lista, card, interazione = la rotta delle
           interazioni dell'immobile con lead e context seller, richiamo = task;
       c06 filtri: parametri nella GET, filtro agente solo con can_assign;
       c07 «Avvia acquisizione» dal venditore: proprietario preselezionato, MAI
           «L'immobile non ha proprietari collegati», source seller_lead,
           lead_id nel payload.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
SELLERS_DIR = ASSETS / "sellers"

from tests import test_a30_5_create_ui as a30_5  # noqa: E402
from tests import test_a30_13b_quick_booking_ui as a30_13b  # noqa: E402
from tests import test_crm_ops_3_acquisitions as base  # noqa: E402
from tests.test_a30_5_create_ui import staged  # noqa: E402,F401  (fixture riusata)

node = pytest.mark.skipif(a30_5.NODE is None, reason="node non disponibile: livello NON eseguito (BLOCKED)")


# ---------------------------------------------------------------------------
# A - funzioni pure
# ---------------------------------------------------------------------------

def _modello(expr):
    script = (f"import * as m from '{(SELLERS_DIR / 'seller-model.js').as_posix()}';\n"
              f"console.log(JSON.stringify({expr}));")
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True,
                           text=True, timeout=30, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


@node
def test_a01_proprietari_vendibili_uno_per_contatto_inquilino_escluso():
    out = _modello("""m.sellableOwners([
      {contact_id: 41, role: 'owner'}, {contact_id: 41, role: 'seller'},
      {contact_id: 42, role: 'seller'}, {contact_id: 43, role: 'tenant'}, {contact_id: 44, role: 'contact'},
    ]).map((c) => [c.contact_id, c.role])""")
    assert out == [[41, "owner"], [42, "seller"]]


@node
def test_a02_stato_vende_dai_lead_dell_immobile_stesso_ordine_del_server():
    out = _modello("""(() => {
      const L = (lead_id, status, extra = {}) => ({ lead_id, status, relation_type: 'seller', pipeline: 'sell', contact_id: 41, stage: 'new', ...extra });
      return {
        nessuno: m.sellerState([], 41),
        altroRuolo: m.sellerState([L(1, 'open', { relation_type: 'origin' })], 41),
        altroPipeline: m.sellerState([L(1, 'open', { pipeline: 'buy' })], 41),
        altroContatto: m.sellerState([L(1, 'open', { contact_id: 42 })], 41),
        apertoVinceSuChiuso: m.sellerState([L(5, 'closed'), L(3, 'open', { stage: 'qualified' })], 41),
        sospeso: m.sellerState([L(5, 'closed'), L(3, 'paused')], 41),
        chiusoPiuRecente: m.sellerState([L(5, 'closed'), L(9, 'closed')], 41),
      };
    })()""")
    assert out["nessuno"] == {"state": "none", "leadId": None, "stage": None}
    assert out["altroRuolo"]["state"] == out["altroPipeline"]["state"] == out["altroContatto"]["state"] == "none"
    assert out["apertoVinceSuChiuso"] == {"state": "open", "leadId": 3, "stage": "qualified"}
    assert out["sospeso"]["state"] == "paused" and out["sospeso"]["leadId"] == 3
    assert out["chiusoPiuRecente"] == {"state": "closed", "leadId": 9, "stage": "new"}


@node
def test_a03_parametri_worklist_link_acquisizione_e_telefono():
    out = _modello("""({
      vuoti: m.worklistParams({}, 0),
      pieni: m.worklistParams({ view: 'overdue', status: 'paused', agentId: '0', city: ' Fermo ', search: ' Rossi ' }, 30, 30),
      tutti: m.worklistParams({ view: 'all', status: 'active', agentId: '' }, 0),
      href: m.acquisitionHref({ lead_id: 77, contact: { id: 41 }, property: { id: 30 } }),
      tel: m.telHref('333 111-22'), telVuoto: m.telHref(''),
      riga: m.propertyLine({ id: 30, address: 'Via Roma', civic_number: '1', city: 'Giulianova' }),
    })""")
    assert out["vuoti"] == "limit=30&offset=0" == out["tutti"]
    assert out["pieni"] == "view=overdue&status=paused&agent_id=0&city=Fermo&search=Rossi&limit=30&offset=30"
    assert out["href"] == "#/acquisizioni/nuova/30/41/77"
    assert out["tel"] == "tel:33311122" and out["telVuoto"] is None
    assert out["riga"] == "Via Roma 1, Giulianova"


CODICI = ("SELLER_NOT_OWNER", "PROPERTY_IN_CENSUS", "SELLER_PROPERTY_CLOSED", "SELLER_CONTACT_NOT_ASSIGNED",
          "SELLER_LEAD_NOT_VISIBLE", "SELLER_LEAD_AMBIGUOUS", "SELLER_LEAD_OTHER_PROPERTY", "SELLER_ALREADY_ACTIVE")


@node
def test_a04_ogni_codice_del_backend_ha_una_frase_senza_nomi_tecnici():
    out = _modello(f"{json.dumps(list(CODICI))}.map((code) => m.sellerErrorMessage({{ code, detail: 'x_tecnico' }}))")
    assert len(set(out)) == len(CODICI)
    for frase in out:
        assert "_" not in frase and "lead_id" not in frase and "property_contacts" not in frase, frase
    # ogni codice esiste davvero nel backend
    sorgente = (ROOT / "crm" / "sellers.py").read_text(encoding="utf-8")
    for code in CODICI:
        assert f'"{code}"' in sorgente, code


# ---------------------------------------------------------------------------
# B - statici
# ---------------------------------------------------------------------------

def _codice(p):
    """Il file senza le righe di commento: conta solo cio' che il browser esegue."""
    return "\n".join(r for r in p.read_text(encoding="utf-8").splitlines()
                     if not r.lstrip().startswith(("//", "*", "/*")))


def test_b01_le_rotte_venditori_sono_nominate_solo_dal_client_proprio():
    fuori = sorted(p.relative_to(ASSETS).as_posix() for p in ASSETS.rglob("*.js") if "/api/crm/sellers" in _codice(p))
    assert fuori == ["sellers/sellers-api.js"]


@node
def test_b02_file_nuovi_passano_node_check():
    for rel in ("sellers/seller-model.js", "sellers/sellers-api.js", "sellers/seller-toggle.js",
                "views/venditori.js", "views/immobile-dettaglio.js", "views/acquisizioni.js", "main.js"):
        esito = subprocess.run([a30_5.NODE, "--check", str(ASSETS / rel)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, (rel, esito.stderr)


def test_b03_nessuna_scorciatoia_ne_cancellazione_dal_client():
    api = _codice(SELLERS_DIR / "sellers-api.js")
    toggle = _codice(SELLERS_DIR / "seller-toggle.js")
    vista = _codice(ASSETS / "views" / "venditori.js")
    for testo in (api, toggle, vista):
        assert "'DELETE'" not in testo and "agency_id" not in testo
        assert "/api/acquisitions" not in testo            # «Vende» non crea acquisizioni
    # la presa in carico e' quella esistente di census-api, non una rotta nuova
    assert "import { takeInCharge } from '../census/census-api.js'" in toggle
    assert "take-in-charge" not in api
    # lo storico: la rotta delle interazioni dell'immobile, con il lead
    assert "/interactions`" in api and "context: 'seller'" in api


def test_b04_css_venditori_mobile_e_azioni_touch():
    css = (ASSETS / "app.css").read_text(encoding="utf-8")
    blocco = css[css.index("VENDITORI-1"):]
    assert ".seller-card" in blocco and ".seller-actions" in blocco
    assert re.search(r"@media \(max-width: ?\d+px\)", blocco)
    assert "min-height: 44px" in blocco


# ---------------------------------------------------------------------------
# C - shell eseguita (stub DOM)
# ---------------------------------------------------------------------------

rt = a30_5._rt()
LEAD_77 = {"id": 1, "property_id": 30, "lead_id": 77, "relation_type": "seller", "pipeline": "sell",
           "stage": "contacted", "status": "open", "contact_id": 41}
IMMOBILE = {**base.IMMOBILE, "record_kind": "crm", "leads": [LEAD_77]}
IMMOBILE_CENSUS = {**base.IMMOBILE, "record_kind": "census", "building_id": 7, "parent_property_id": None, "leads": []}
CENSUS = {"id": 30, "code": "IMM-30", "record_kind": "census", "address_inherited": True, "whole_building": False,
          "building": {"id": 7, "name": "Palazzina via Roma 10"}, "pertinenze": [], "accessories": [],
          "parent": None, "main_candidates": []}
OPZIONI_FORM = {"territory": [], "energy_classes": [], "property_types": [], "can_assign": True,
                "agents": [{"id": 3, "label": "Anna Agente"}, {"id": 4, "label": "Bruno Collega"}]}
OPZIONI_AGENTE = {**OPZIONI_FORM, "can_assign": False, "agents": []}


def _voce(lead_id=77, **extra):
    v = {"lead_id": lead_id, "stage": "contacted", "stage_label": "Contattato", "status": "open", "lost_reason": None,
         "agent_id": 3, "agent_name": "Anna Agente", "still_owner": True,
         "contact": {"id": 41, "name": "Mario Rossi", "phone": "333 111", "email": "mario@example.test"},
         "property": {"id": 30, "code": "IMM-30", "title": "Trilocale", "address": "Via Roma", "civic_number": "1",
                      "city": "Giulianova"},
         "last_interaction": {"interaction_type": "call", "occurred_at": "2026-10-01T10:00:00+02:00",
                              "note": "Vuole 220.000"},
         "next_action": {"task_id": 5, "title": "Richiamare Mario Rossi", "due_at": "2026-10-02T09:00:00+02:00",
                         "overdue": True, "source": "task"},
         "intent": {"score": 72, "band": "caldo", "state": "active"}, "acquisition": None}
    v.update(extra)
    return v


WORKLIST = {"items": [_voce(), _voce(78, contact={"id": 42, "name": "Bruno Bianchi", "phone": None, "email": None},
                                    status="paused", next_action=None, last_interaction=None, intent=None,
                                    still_owner=False)],
            "has_more": False, "limit": 30, "offset": 0,
            "views": [{"value": v, "label": l} for v, l in (("all", "Tutti"), ("new", "Nuovi"), ("overdue", "Richiami scaduti"))],
            "statuses": [{"value": v, "label": l} for v, l in (("active", "Attivi"), ("paused", "Sospesi"), ("closed", "Chiusi"), ("all", "Tutti"))],
            "stages": [{"value": v, "label": l} for v, l in (("new", "Nuovo"), ("contacted", "Contattato"), ("qualified", "Qualificato"), ("appointment", "Appuntamento"), ("lost", "Perso"))],
            "outcomes": [{"value": v, "label": l} for v, l in (("paused", "Vendita sospesa"), ("not_selling", "Non vende più"), ("mistake", "Inserito per errore"))]}
ATTIVATO = {"status": 201, "body": {"lead_id": 79, "property_id": 30, "contact_id": 42, "status": "open",
                                    "created": True, "reopened": False}}


def _rotte(sessione="agency_owner", *, immobile=IMMOBILE, post_sellers=None, opzioni=OPZIONI_FORM, worklist=WORKLIST):
    post_sellers = post_sellers or (ATTIVATO,)
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
        ("GET", "/api/crm/sellers", [rt.ok(worklist)]),
        ("POST", "/api/crm/sellers/deactivate", [rt.ok({"lead_id": 77, "status": "paused", "changed": True})]),
        ("POST", "/api/crm/sellers", list(post_sellers)),
        ("POST", "/api/property/properties/30/interactions", [{"status": 201, "body": {"id": 900}}]),
        ("POST", "/api/property/properties/30/take-in-charge", [rt.ok({**IMMOBILE_CENSUS, "record_kind": "crm", "pertinenze_taken": []})]),
        ("GET", "/api/property/properties/30/census", [rt.ok(CENSUS)]),
        ("POST", "/api/core/tasks", [{"status": 201, "body": {"id": 6}}]),
        ("PATCH", "/api/core/leads/77", [rt.ok({"id": 77, "stage": "qualified"})]),
        ("GET", "/api/acquisitions/options", [rt.ok(base.OPZIONI)]),
        ("GET", "/api/acquisitions?", [rt.ok({"items": []})]),
        ("POST", "/api/acquisitions", [{"status": 201, "body": {**base._acquisizione(lead_id=77), "replayed": False}}]),
        ("GET", "/api/acquisitions/501", [rt.ok(base._acquisizione(lead_id=77))]),
        ("GET", "/api/appointments/agents", [rt.ok(base.AGENTI)]),
        ("POST", "/api/appointments/availability/check", [rt.ok(a30_5.LIBERO)]),
        ("GET", "/api/appointments/900", [rt.ok({"appointment": base.APPUNTAMENTO, "allowed_actions": []})]),
        ("GET", "/api/property/properties/31", [rt.ok(base.SENZA_PROPRIETARI)]),
        ("GET", "/api/property/properties/30", [rt.ok(immobile)]),
        ("GET", "/api/property/properties?", [rt.ok({"items": [immobile]})]),
        ("GET", "/api/property/form-options", [rt.ok(opzioni)]),
        ("GET", "/api/proposals?", [rt.ok({"items": []})]),
        ("GET", "/api/sales?", [rt.ok({"items": []})]),
        ("GET", "/api/match/", [rt.ok({"items": []})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


HELPERS = r"""
const SD = () => C().querySelectorAll('dialog').find((d) => d._open);
const sq = (s) => SD().querySelector(s);
const cella = (cid) => C().querySelectorAll('.seller-cell').find((c) => c.querySelector(`[data-seller-on="${cid}"],[data-seller-stop="${cid}"]`));
const statiCelle = () => C().querySelectorAll('.seller-cell').map((c) => c.dataset.sellerState);
function spunta(nome, valore) {
  SD().querySelectorAll(`input[name="${nome}"]`).forEach((r) => { r.checked = r.getAttribute('value') === valore; });
}
"""


def _run(staged, scenario, rotte, hash):  # noqa: F811
    driver = staged.parent / "driver-venditori-1.mjs"
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
    return [c for c in out["calls"] if c["m"] in ("POST", "PATCH", "PUT", "DELETE")
            and c["url"] != "/api/appointments/availability/check"]


@node
def test_c01_tab_proprietari_una_cella_per_proprietario_e_vende_una_sola_post(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const stati = statiCelle();
      const intestazioni = C().querySelectorAll('th').map((t) => t.textContent.trim());
      const aperta = cella(41).visibleText();
      cella(42).querySelector('[data-seller-on="42"]').dispatch('click'); await wait(); await wait();
      report({ stati, intestazioni, aperta });
    """
    out = _run(staged, scenario, _rotte(), "#/immobili/30/proprietari")
    # Mario (owner, lead 77 aperto) e Bruno (seller, niente): due celle, l'inquilino nessuna
    assert out["stati"] == ["open", "none"]
    assert "Vende" in out["intestazioni"]
    assert "Vende" in out["aperta"] and "Contattato" in out["aperta"] and "Smetti" in out["aperta"]
    scritture = _scritture(out)
    assert [(c["m"], c["url"]) for c in scritture] == [("POST", "/api/crm/sellers")]
    assert scritture[0]["body"] == {"property_id": 30, "contact_id": 42}
    assert scritture[0]["cred"] == "include"
    assert "Bruno Bianchi ora è tra i Venditori." in out["content"]
    # dopo l'attivazione la scheda rilegge immobile (contatti + lead), nessuna acquisizione
    letture = [c["url"] for c in out["calls"] if c["m"] == "GET" and c["url"] == "/api/property/properties/30"]
    assert len(letture) == 2
    assert not any("/api/acquisitions" in c["url"] for c in out["calls"])


@node
def test_c02_censimento_dialog_poi_presa_in_carico_esistente_poi_vende(staged):  # noqa: F811
    censimento = {"status": 409, "body": {"detail": "Immobile in censimento", "code": "PROPERTY_IN_CENSUS"}}
    scenario = r"""
      await wait(); await wait();
      const tabs = C().querySelectorAll('[data-tab]').map((b) => b.dataset.tab);
      C().querySelectorAll('[data-tab]').find((b) => b.dataset.tab === 'proprietari').dispatch('click'); await wait();
      cella(41).querySelector('[data-seller-on="41"]').dispatch('click'); await wait(); await wait();
      const testo = SD() ? SD().visibleText() : null;
      const dopoClick = scritture().map((c) => c.url);
      sq('[data-seller-census]').dispatch('submit'); await wait(); await wait(); await wait();
      report({ tabs, testo, dopoClick, aperto: !!SD() });
    """
    out = _run(staged, scenario, _rotte(immobile=IMMOBILE_CENSUS, post_sellers=(censimento, ATTIVATO)),
               "#/immobili/30")
    assert "proprietari" in out["tabs"]
    assert "Mario Rossi vuole vendere questo immobile. Per lavorarlo nel CRM dobbiamo prima prenderlo in carico." in out["testo"]
    assert "Prendi in carico e continua" in out["testo"]
    assert out["dopoClick"] == ["/api/crm/sellers"]            # il 409 non scrive nulla
    scritture = _scritture(out)
    assert [c["url"] for c in scritture] == ["/api/crm/sellers", "/api/property/properties/30/take-in-charge",
                                             "/api/crm/sellers"]
    assert scritture[1]["body"] == {"include_pertinenze": True}
    assert scritture[2]["body"] == {"property_id": 30, "contact_id": 41}
    assert out["aperto"] is False
    assert "Immobile preso in carico: Mario Rossi ora è tra i Venditori." in out["content"]


@node
def test_c02b_presa_in_carico_riuscita_vende_rifiutato_lo_dice(staged):  # noqa: F811
    censimento = {"status": 409, "body": {"detail": "x", "code": "PROPERTY_IN_CENSUS"}}
    rifiuto = {"status": 409, "body": {"detail": "x", "code": "SELLER_CONTACT_NOT_ASSIGNED"}}
    scenario = r"""
      await wait(); await wait();
      C().querySelectorAll('[data-tab]').find((b) => b.dataset.tab === 'proprietari').dispatch('click'); await wait();
      cella(41).querySelector('[data-seller-on="41"]').dispatch('click'); await wait(); await wait();
      sq('[data-seller-census]').dispatch('submit'); await wait(); await wait(); await wait();
      report();
    """
    out = _run(staged, scenario, _rotte(immobile=IMMOBILE_CENSUS, post_sellers=(censimento, rifiuto)), "#/immobili/30")
    assert "Immobile preso in carico. Vende non attivato: Il proprietario è assegnato a un altro agente" in out["content"]


@node
def test_c03_lead_ambigui_scelta_esplicita_del_lead_o_nuovo(staged):  # noqa: F811
    ambiguo = {"status": 409, "body": {"detail": "x", "code": "SELLER_LEAD_AMBIGUOUS", "candidates": [
        {"id": 90, "source": "public_stima", "stage": "new", "created_at": "2026-09-01T10:00:00+02:00"},
        {"id": 91, "source": "manual", "stage": "contacted", "created_at": "2026-09-10T10:00:00+02:00"}]}}
    scenario = r"""
      await wait(); await wait();
      cella(42).querySelector('[data-seller-on="42"]').dispatch('click'); await wait(); await wait();
      const testo = SD().visibleText();
      const valori = SD().querySelectorAll('input[name="seller-lead"]').map((r) => r.getAttribute('value'));
      spunta('seller-lead', '91');
      sq('[data-seller-choice]').dispatch('submit'); await wait(); await wait();
      cella(42).querySelector('[data-seller-on="42"]').dispatch('click'); await wait(); await wait();
      spunta('seller-lead', 'new');
      sq('[data-seller-choice]').dispatch('submit'); await wait(); await wait();
      report({ testo, valori });
    """
    out = _run(staged, scenario, _rotte(post_sellers=(ambiguo, ATTIVATO, ambiguo, ATTIVATO)), "#/immobili/30/proprietari")
    assert "Quale lead collegare?" in out["testo"] and "Stima dal sito" in out["testo"]
    assert out["valori"] == ["90", "91", "new"]
    corpi = [c["body"] for c in _scritture(out)]
    assert corpi == [{"property_id": 30, "contact_id": 42}, {"property_id": 30, "contact_id": 42, "lead_id": 91},
                     {"property_id": 30, "contact_id": 42}, {"property_id": 30, "contact_id": 42, "new_lead": True}]


@node
def test_c04_smetti_cambia_stato_con_nota_nessuna_cancellazione(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      cella(41).querySelector('[data-seller-stop="41"]').dispatch('click'); await wait();
      const testo = SD().visibleText();
      spunta('seller-outcome', 'not_selling');
      sq('#seller-stop-note').value = '  ha deciso di affittare ';
      sq('[data-seller-stop-form]').dispatch('submit'); await wait(); await wait();
      report({ testo });
    """
    out = _run(staged, scenario, _rotte(), "#/immobili/30/proprietari")
    assert "Mario Rossi non vende più?" in out["testo"] and "Lo storico" in out["testo"]
    scritture = _scritture(out)
    assert [(c["m"], c["url"]) for c in scritture] == [("POST", "/api/crm/sellers/deactivate")]
    assert scritture[0]["body"] == {"property_id": 30, "contact_id": 41, "outcome": "not_selling",
                                    "note": "ha deciso di affittare"}
    assert "Stato aggiornato: lo storico resta." in out["content"]


@node
def test_c05_pagina_venditori_una_get_card_interazione_e_richiamo(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const nav = __dom.byId['nav'].children.map((b) => b.dataset.route);
      const listaPrima = chiamate().filter((c) => c.m === 'GET' && c.url.startsWith('/api/crm/sellers')).length;
      const card = C().querySelectorAll('.seller-card').map((c) => c.visibleText());
      const azioniBruno = C().querySelectorAll('.seller-card')[1].querySelectorAll('button,a').map((b) => b.textContent.trim());
      const avvia = C().querySelector('[data-acquire="77"]').getAttribute('href');
      const chiama = C().querySelector('[data-call="77"]').getAttribute('href');
      C().querySelector('[data-log="77"]').dispatch('click'); await wait();
      SD().querySelectorAll('[data-type]').find((b) => b.dataset.type === 'whatsapp').dispatch('click');
      sq('form').dispatch('submit'); await wait();
      const vuota = sq('[data-error]').textContent;
      sq('#seller-note').value = ' Vuole 220.000, richiamo venerdì ';
      sq('#seller-recall').value = '2026-10-09T10:00';
      sq('form').dispatch('submit'); await wait(); await wait();
      report({ nav, listaPrima, card, azioniBruno, avvia, chiama, vuota });
    """
    out = _run(staged, scenario, _rotte(), "#/venditori")
    assert out["nav"].index("venditori") == out["nav"].index("immobili") + 1
    assert out["listaPrima"] == 1                                # UNA GET per tutta la lista
    mario, bruno = out["card"]
    for atteso in ("Mario Rossi", "Contattato", "Caldo 72", "IMM-30 · Via Roma 1, Giulianova", "333 111",
                   "Anna Agente", "Telefonata", "Vuole 220.000", "Richiamare Mario Rossi", "Scaduta da"):
        assert atteso in mario, atteso
    assert "Sospesa" in bruno and "Non più collegato come proprietario" in bruno
    # REV 2 (R3): Bruno e' sospeso E non piu' proprietario -> niente «Riprendi»
    # (il backend la rifiuterebbe), resta «Smetti…» per chiudere
    assert "Riprendi" not in out["azioniBruno"] and "Smetti…" in out["azioniBruno"]
    assert "Registra interazione" not in out["azioniBruno"]
    assert out["avvia"] == "#/acquisizioni/nuova/30/41/77"
    assert out["chiama"] == "tel:333111"
    assert out["vuota"] == "Scrivi cosa vi siete detti."
    scritture = _scritture(out)
    assert [(c["m"], c["url"]) for c in scritture] == [("POST", "/api/property/properties/30/interactions"),
                                                       ("POST", "/api/core/tasks")]
    assert scritture[0]["body"] == {"interaction_type": "whatsapp", "note": "Vuole 220.000, richiamo venerdì",
                                    "contact_id": 41, "lead_id": 77, "context": "seller"}
    task = scritture[1]["body"]
    assert (task["lead_id"], task["contact_id"], task["title"], task["task_type"]) == (
        77, 41, "Richiamare Mario Rossi", "seller_followup")
    assert task["due_at"] == "2026-10-09T08:00:00.000Z"          # 10:00 a Roma
    assert "Interazione registrata e richiamo fissato." in out["content"]
    # dopo il salvataggio la lista si rilegge (una GET in piu', non una per card)
    assert len([c for c in out["calls"] if c["m"] == "GET" and c["url"].startswith("/api/crm/sellers")]) == 2


@node
def test_c06_filtri_nella_get_e_filtro_agente_solo_con_can_assign(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const agente = C().querySelector('#sellers-agent');
      const visibile = !agente.hidden;
      const opzioni = opz(agente);
      agente.value = '0'; agente.dispatch('change'); await wait();
      C().querySelectorAll('[data-view]').find((b) => b.dataset.view === 'overdue').dispatch('click'); await wait();
      const stato = C().querySelector('#sellers-status'); stato.value = 'paused'; stato.dispatch('change'); await wait();
      C().querySelector('[data-stage="77"]') && C().querySelector('[data-stage="77"]').dispatch('click'); await wait();
      const fasi = SD() ? opz(sq('#seller-stage')) : [];
      if (SD()) { sq('#seller-stage').value = 'qualified'; sq('form').dispatch('submit'); await wait(); }
      report({ visibile, opzioni, fasi });
    """
    out = _run(staged, scenario, _rotte(), "#/venditori")
    assert out["visibile"] is True and out["opzioni"] == ["", "0", "3", "4"]
    liste = [c["url"] for c in out["calls"] if c["m"] == "GET" and c["url"].startswith("/api/crm/sellers")]
    assert "agent_id=0" in liste[1]
    assert "view=overdue" in liste[2] and "agent_id=0" in liste[2]
    assert "status=paused" in liste[3]
    assert out["fasi"] == ["new", "contacted", "qualified", "appointment"]     # ne' vinto ne' perso a mano
    assert [(c["m"], c["url"], c["body"]) for c in _scritture(out)] == [
        ("PATCH", "/api/core/leads/77", {"stage": "qualified"})]

    agente = _run(staged, "await wait(); await wait(); report({ nascosto: C().querySelector('#sellers-agent').hidden === true });",
                  _rotte("agent", opzioni=OPZIONI_AGENTE), "#/venditori")
    assert agente["nascosto"] is True
    assert all("agent_id" not in c["url"] for c in agente["calls"] if c["url"].startswith("/api/crm/sellers"))


@node
def test_c07_avvia_acquisizione_dal_venditore_proprietario_preselezionato(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const avviso = q('#acq-no-owner') ? q('#acq-no-owner').textContent : '';
      const testo = D().visibleText();
      const proprietario = q('#acq-owner').value;
      const fonte = q('#acq-source').value;
      q('#acq-timing').value = 'within_6_months';
      q('form').dispatch('submit'); await wait(); await wait();
      const erroreForm = q('#acq-new-error') ? q('#acq-new-error').textContent : '';
      const a = f('[data-field="agent"]'); a.value = '3'; a.dispatch('change');
      orario('2031-01-10', '10:00');
      await conferma(); await wait();
      report({ avviso, testo, proprietario, fonte, erroreForm });
    """
    out = _run(staged, scenario, _rotte(), "#/acquisizioni/nuova/30/41/77")
    assert out["avviso"] == ""
    assert "non ha proprietari" not in out["testo"]
    assert out["proprietario"] == "41" and out["fonte"] == "seller_lead" and out["erroreForm"] == ""
    scritture = _scritture(out)
    assert [c["url"] for c in scritture] == ["/api/acquisitions"]
    corpo = scritture[0]["body"]
    assert (corpo["property_id"], corpo["owner_contact_id"], corpo["lead_id"], corpo["source"]) == (30, 41, 77, "seller_lead")
    assert "agency_id" not in corpo


@node
def test_c08_link_con_un_contatto_che_non_e_proprietario_non_preseleziona_ne_porta_il_lead(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      report({ proprietario: q('#acq-owner').value, avviso: q('#acq-no-owner') ? q('#acq-no-owner').textContent : '' });
    """
    out = _run(staged, scenario, _rotte(), "#/acquisizioni/nuova/30/43/77")   # 43 = inquilino
    assert out["proprietario"] != "43" and out["avviso"] == ""
    assert _scritture(out) == []


@node
def test_c09_immobile_senza_proprietari_resta_bloccato(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      report({ avviso: q('#acq-no-owner') ? q('#acq-no-owner').textContent : null });
    """
    out = _run(staged, scenario, _rotte(), "#/acquisizioni/nuova/31")
    assert out["avviso"] and "non ha proprietari" in out["avviso"]
    assert _scritture(out) == []


# ---------------------------------------------------------------------------
# REV 2 - R3: venditore non piu' collegato come proprietario
# ---------------------------------------------------------------------------

ORFANI = {**WORKLIST, "items": [
    _voce(80, still_owner=False, contact={"id": 44, "name": "Dario Ex", "phone": "333 222", "email": None}),
    _voce(81, still_owner=False, status="paused", contact={"id": 45, "name": "Elsa Ex", "phone": None, "email": None}),
    _voce(82, still_owner=False, status="closed", contact={"id": 46, "name": "Fabio Ex", "phone": None, "email": None}),
    _voce(77)]}


@node
def test_c10_r3_scollegato_solo_azioni_che_il_backend_accetta(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const card = (id) => C().querySelector(`.seller-card[data-lead-id="${id}"]`);
      const azioni = (id) => card(id).querySelectorAll('[data-log],[data-followup],[data-stage],[data-acquire],[data-stop],[data-resume],[data-call]')
        .map((b) => Object.keys(b.dataset).find((k) => ['log', 'followup', 'stage', 'acquire', 'stop', 'resume', 'call'].includes(k)));
      const link = (id) => card(id).querySelectorAll('a').map((a) => a.getAttribute('href'));
      const testo = card(80).visibleText();
      const out = { aperta: azioni(80), sospesa: azioni(81), chiusa: azioni(82), proprietario: azioni(77),
                    link: link(80), testo };
      card(81).querySelector('[data-stop]').dispatch('click'); await wait();
      sq('[data-stop-form]').dispatch('submit'); await wait(); await wait();
      report(out);
    """
    out = _run(staged, scenario, _rotte(worklist=ORFANI), "#/venditori")
    assert sorted(out["aperta"]) == ["call", "followup", "stop"]
    assert out["sospesa"] == ["stop"]                     # chiudere resta possibile
    assert out["chiusa"] == []                            # niente Riattiva: il backend la rifiuterebbe
    assert sorted(out["proprietario"]) == ["acquire", "call", "followup", "log", "stage", "stop"]
    assert "#/contatti/44" in out["link"] and "#/immobili/30" in out["link"]
    assert "#/immobili/30/proprietari" in out["link"]     # da li' si ricollega, non da qui
    assert "Non più collegato come proprietario" in out["testo"] and "ricollega Dario Ex" in out["testo"]
    scritture = _scritture(out)
    assert [(c["url"], c["body"]["contact_id"]) for c in scritture] == [("/api/crm/sellers/deactivate", 45)]
    assert not any(c["url"].startswith("/api/property/properties/30/contacts") for c in scritture)
