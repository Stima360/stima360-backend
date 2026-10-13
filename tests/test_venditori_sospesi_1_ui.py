"""VENDITORI-SOSPESI-1 - shell: le opportunita' Venditore sospese restano
raggiungibili e chiudibili. Nessun database.

  A  funzioni pure (node): scelte di «Smetti…» per stato, filtro da indirizzo,
     testo «Ci sono N venditori sospesi», messaggio del 404 (altro agente),
     aiuto del blocco Venditore nel foglio Elimina;
  B  statici: nessuna scrittura nuova, nessun DELETE, nessun dato del lead nel
     foglio Elimina; la regola open/paused del backend non e' toccata dal client;
  C  shell sullo stub DOM (harness di VENDITORI-1 e DELETE-ARCH 2B3):
       c01 card sospesa ancora proprietario: «Riprendi» E «Smetti…»; il foglio
           propone solo «Non vende più» / «Inserito per errore»; UNA POST
           deactivate, nessuna riattivazione;
       c02 404 (lead di un altro agente): messaggio «rivolgiti a un
           amministratore», nessun'altra scrittura;
       c03 lista Attivi vuota con sospese: «Ci sono N venditori sospesi» e
           «Mostra sospesi» -> lista filtrata;
       c04 lista Attivi vuota senza sospese: il messaggio di sempre, nessun avviso;
       c05 `#/venditori/sospesi`: prima GET gia' filtrata, menu su Venditori;
           `#/venditori`: come prima;
       c06 tab Proprietari: sospesa -> «Riprendi» E «Smetti…», foglio senza
           «Vendita sospesa», UNA POST deactivate;
       c07 foglio Elimina: etichetta del backend invariata + come risolverlo +
           collegamento `#/venditori/sospesi`, nessuna scrittura.
"""
from __future__ import annotations

import json
import subprocess

from tests import test_a30_5_create_ui as a30_5
from tests import test_delete_arch_2b3_ui as t2b3
from tests import test_venditori_1_ui as v1
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

node = v1.node
ASSETS = v1.ASSETS
NON_TUOI = "Questa opportunità è assegnata a un altro agente o non è più disponibile: rivolgiti a un amministratore dell’agenzia."


def _pure(modulo, expr):
    script = (f"import * as m from '{(ASSETS / modulo).as_posix()}';\n"
              f"console.log(JSON.stringify({expr}));")
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True,
                           text=True, timeout=30, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# A - funzioni pure
# ---------------------------------------------------------------------------

@node
def test_a01_scelte_smetti_filtro_da_indirizzo_conteggio_e_messaggi():
    out = _pure("sellers/seller-model.js", """(() => ({
      sospesa: m.stopOutcomes(null, 'paused').map((o) => o.value),
      aperta: m.stopOutcomes(null, 'open').map((o) => o.value),
      dalServer: m.stopOutcomes([{value: 'paused', label: 'x'}, {value: 'not_selling', label: 'y'}], 'paused').map((o) => o.value),
      rotte: [m.sellersStatusFromRoute(['sospesi']), m.sellersStatusFromRoute([]), m.sellersStatusFromRoute(['altro']),
              m.sellersStatusFromRoute(undefined)],
      uno: m.pausedCountText(1), tre: m.pausedCountText(3), oltre: m.pausedCountText(200, true),
      nonTuo: m.sellerStopErrorMessage({ status: 404, code: 'NOT_FOUND' }),
      altro: m.sellerStopErrorMessage({ status: 409, code: 'SELLER_CONTACT_NOT_ASSIGNED' }),
      href: m.SELLERS_PAUSED_HREF,
    }))()""")
    assert out["sospesa"] == ["not_selling", "mistake"]                # mai di nuovo «Vendita sospesa»
    assert out["aperta"] == ["paused", "not_selling", "mistake"]       # invariato per un'aperta
    assert out["dalServer"] == ["not_selling"]
    assert out["rotte"] == ["paused", "active", "active", "active"]
    assert out["uno"] == "C’è 1 venditore sospeso." and out["tre"] == "Ci sono 3 venditori sospesi."
    assert out["oltre"] == "Ci sono più di 200 venditori sospesi."
    assert out["nonTuo"] == NON_TUOI
    assert out["altro"] == "Il proprietario è assegnato a un altro agente: chiedi a un amministratore."
    assert out["href"] == "#/venditori/sospesi"


@node
def test_a02_aiuto_del_blocco_venditore_nel_foglio_elimina():
    out = _pure("trash/trash-model.js", """(() => {
      const B = (stati) => m.blockerView([{ code: 'SELLER_OPPORTUNITY_OPEN', label: 'etichetta backend',
                                             items: stati.map((s, i) => ({ id: 80 + i, status: s, contact_id: 182 })) }])[0];
      return { sospesa: B(['paused']), aperta: B(['open']), entrambe: B(['open', 'paused']),
               altro: m.blockerView([{ code: 'ACQUISITION_OPEN', label: 'x', items: [{id: 1}] }])[0] };
    })()""")
    s = out["sospesa"]
    assert s["label"] == "etichetta backend" and s["count"] == 1 and s["link"] is None
    assert "Venditori › Sospesi" in s["help"]["text"] and "Non vende più o Inserito per errore" in s["help"]["text"]
    assert "rivolgiti a un amministratore dell’agenzia" in s["help"]["text"]
    assert s["help"]["links"] == [{"href": "#/venditori/sospesi", "label": "Apri Venditori sospesi"}]
    assert out["aperta"]["help"]["links"] == [{"href": "#/venditori", "label": "Apri Venditori"}]
    assert [l["href"] for l in out["entrambe"]["help"]["links"]] == ["#/venditori/sospesi", "#/venditori"]
    assert "help" not in out["altro"]                                   # gli altri blocchi invariati
    for v in (s, out["aperta"], out["entrambe"]):                       # nessun dato del lead nel testo
        assert "182" not in v["help"]["text"] and "80" not in v["help"]["text"]


# ---------------------------------------------------------------------------
# B - statici
# ---------------------------------------------------------------------------

def test_b01_nessuna_scrittura_nuova_e_regola_backend_non_replicata():
    for rel in ("sellers/seller-model.js", "sellers/seller-toggle.js", "views/venditori.js",
                "trash/trash-model.js", "trash/trash-dialog.js"):
        testo = v1._codice(ASSETS / rel)
        assert "'DELETE'" not in testo and "agency_id" not in testo and "apiDelete" not in testo, rel
    vista = v1._codice(ASSETS / "views" / "venditori.js")
    # la sola scrittura di «Smetti…» resta deactivate (sellers-api.js), mai una riattivazione
    assert "api.deactivateSeller(" in vista
    modello = v1._codice(ASSETS / "trash" / "trash-model.js")
    assert "'paused'" in modello and "SELLER_OPPORTUNITY_OPEN" in modello
    assert "can_trash" not in modello                                  # il client non decide il blocco


@node
def test_b02_node_check():
    for rel in ("sellers/seller-model.js", "sellers/seller-toggle.js", "views/venditori.js",
                "trash/trash-model.js", "trash/trash-dialog.js"):
        esito = subprocess.run([a30_5.NODE, "--check", str(ASSETS / rel)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, (rel, esito.stderr)


# ---------------------------------------------------------------------------
# C - shell sullo stub DOM
# ---------------------------------------------------------------------------

SOSPESA = v1._voce(85, status="paused", next_action=None, intent=None, still_owner=True,
                   contact={"id": 182, "name": "Gino Sospeso", "phone": "333 222", "email": None},
                   property={"id": 113, "code": "IMM-113", "title": "Bilocale", "address": "Via Mare",
                             "civic_number": "3", "city": "Giulianova"})
LISTA_SOSPESE = {**v1.WORKLIST, "items": [SOSPESA, v1._voce(86, status="paused", still_owner=True)]}
LISTA_VUOTA = {**v1.WORKLIST, "items": []}


def _sellers(*risposte, deactivate=None, sessione="agency_owner"):
    """Le rotte di VENDITORI-1 con le risposte della worklist in sequenza."""
    rotte = v1._rotte(sessione)
    righe = []
    for riga in rotte.splitlines():
        if riga.startswith('__route("GET", "/api/crm/sellers"'):
            riga = f'__route("GET", "/api/crm/sellers", ...{json.dumps([v1.rt.ok(r) for r in risposte])});'
        if deactivate is not None and riga.startswith('__route("POST", "/api/crm/sellers/deactivate"'):
            riga = f'__route("POST", "/api/crm/sellers/deactivate", ...{json.dumps(list(deactivate))});'
        righe.append(riga)
    return "\n".join(righe)


def _urls(out, metodo="GET", prefisso="/api/crm/sellers"):
    return [c["url"] for c in out["calls"] if c["m"] == metodo and c["url"].startswith(prefisso)]


@node
def test_c01_card_sospesa_riprendi_e_smetti_senza_riattivazione(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const carta = C().querySelectorAll('.seller-card').find((c) => c.dataset.leadId === '85');
      const azioni = carta.querySelectorAll('button').map((b) => b.textContent.trim());
      carta.querySelector('[data-stop="85"]').dispatch('click'); await wait();
      const scelte = SD().querySelectorAll('input[name="seller-outcome"]').map((r) => r.getAttribute('value'));
      const spuntata = SD().querySelectorAll('input[name="seller-outcome"]').find((r) => r.checked).getAttribute('value');
      SD().querySelector('form').dispatch('submit'); await wait(); await wait();
      report({ azioni, scelte, spuntata });
    """
    out = v1._run(staged, scenario, _sellers(LISTA_SOSPESE, LISTA_SOSPESE,
                  deactivate=[{"status": 200, "body": {"lead_id": 85, "status": "closed"}}]), "#/venditori/sospesi")
    assert "Riprendi" in out["azioni"] and "Smetti…" in out["azioni"]
    assert out["scelte"] == ["not_selling", "mistake"] and out["spuntata"] == "not_selling"
    scritture = v1._scritture(out)
    assert [(c["m"], c["url"]) for c in scritture] == [("POST", "/api/crm/sellers/deactivate")]
    assert scritture[0]["body"]["outcome"] == "not_selling"
    assert scritture[0]["body"]["property_id"] == 113 and scritture[0]["body"]["contact_id"] == 182
    assert "Stato aggiornato: lo storico resta." in out["content"]


@node
def test_c02_lead_di_un_altro_agente_messaggio_amministratore(staged):  # noqa: F811
    rifiuto = {"status": 404, "body": {"detail": "Nessuna opportunita' venditore", "code": "NOT_FOUND"}}
    scenario = r"""
      await wait(); await wait();
      C().querySelector('[data-stop="85"]').dispatch('click'); await wait();
      SD().querySelector('form').dispatch('submit'); await wait(); await wait();
      report({ errore: SD() ? SD().querySelector('[data-error]').textContent : null });
    """
    out = v1._run(staged, scenario, _sellers(LISTA_SOSPESE, deactivate=[rifiuto], sessione="agent"),
                  "#/venditori/sospesi")
    assert out["errore"] == NON_TUOI
    assert [(c["m"], c["url"]) for c in v1._scritture(out)] == [("POST", "/api/crm/sellers/deactivate")]


@node
def test_c03_attivi_vuota_con_sospese_avviso_e_mostra_sospesi(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait();
      const avviso = C().querySelector('[data-sellers-paused-notice]');
      const testo = avviso ? avviso.visibleText() : null;
      const lista = C().querySelector('#sellers-list').visibleText();
      avviso.querySelector('[data-show-paused]').dispatch('click'); await wait(); await wait();
      const carte = C().querySelectorAll('.seller-card').map((c) => c.dataset.leadId);
      report({ testo, lista, carte, filtro: C().querySelector('#sellers-status').value });
    """
    out = v1._run(staged, scenario, _sellers(LISTA_VUOTA, LISTA_SOSPESE, LISTA_SOSPESE), "#/venditori")
    assert "Nessun venditore attivo." in out["lista"]
    assert "Ci sono 2 venditori sospesi." in out["testo"] and "Mostra sospesi" in out["testo"]
    assert out["carte"] == ["85", "86"] and out["filtro"] == "paused"
    urls = _urls(out)
    assert urls[0] == "/api/crm/sellers?limit=30&offset=0"                      # gli Attivi, come prima
    assert urls[1] == "/api/crm/sellers?status=paused&limit=200&offset=0"       # quante sospese
    assert urls[2] == "/api/crm/sellers?status=paused&limit=30&offset=0"        # la lista filtrata
    assert v1._scritture(out) == []


@node
def test_c04_attivi_vuota_senza_sospese_nessun_avviso(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait();
      report({ avviso: !!C().querySelector('[data-sellers-paused-notice]') });
    """
    out = v1._run(staged, scenario, _sellers(LISTA_VUOTA, LISTA_VUOTA), "#/venditori")
    assert out["avviso"] is False
    assert "Nessun venditore attivo. Dalla scheda di un immobile, tab Proprietari, usa «Vende?»" in out["content"]
    assert v1._scritture(out) == []


@node
def test_c05_collegamento_diretto_sospesi_e_navigazione(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      report({ filtro: C().querySelector('#sellers-status').value,
               attiva: __dom.byId['nav'] ? __dom.byId['nav'].querySelectorAll('.nav-item.active').map((a) => a.dataset.route) : null });
    """
    out = v1._run(staged, scenario, _sellers(LISTA_SOSPESE), "#/venditori/sospesi")
    assert out["filtro"] == "paused"
    assert _urls(out)[0] == "/api/crm/sellers?status=paused&limit=30&offset=0"
    assert out["attiva"] in (None, ["venditori"]), out["attiva"]
    prima = v1._run(staged, scenario, _sellers(v1.WORKLIST), "#/venditori")
    assert prima["filtro"] == "active" and _urls(prima)[0] == "/api/crm/sellers?limit=30&offset=0"


@node
def test_c06_tab_proprietari_sospesa_riprendi_e_smetti(staged):  # noqa: F811
    sospesa = {**v1.LEAD_77, "status": "paused"}
    immobile = {**v1.IMMOBILE, "leads": [sospesa]}
    scenario = r"""
      await wait(); await wait();
      const c = cella(41);
      const azioni = c.querySelectorAll('button').map((b) => b.textContent.trim());
      c.querySelector('[data-seller-stop="41"]').dispatch('click'); await wait();
      const scelte = SD().querySelectorAll('input[name="seller-outcome"]').map((r) => r.getAttribute('value'));
      spunta('seller-outcome', 'mistake');
      SD().querySelector('[data-seller-stop-form]').dispatch('submit'); await wait(); await wait();
      report({ stato: c.dataset.sellerState, azioni, scelte });
    """
    out = v1._run(staged, scenario, v1._rotte(immobile=immobile), "#/immobili/30/proprietari")
    assert out["stato"] == "paused"
    assert out["azioni"] == ["Riprendi", "Smetti…"]
    assert out["scelte"] == ["not_selling", "mistake"]
    scritture = v1._scritture(out)
    assert [(c["m"], c["url"]) for c in scritture] == [("POST", "/api/crm/sellers/deactivate")]
    assert scritture[0]["body"]["outcome"] == "mistake" and scritture[0]["body"]["contact_id"] == 41


@node
def test_c07_foglio_elimina_spiega_e_collega_le_sospese(staged):  # noqa: F811
    bloccato = {"can_trash": False, "blockers": [
        {"code": "SELLER_OPPORTUNITY_OPEN", "label": "Opportunità Venditore aperta: chiudila prima da Venditori («Smetti…»)",
         "items": [{"id": 85, "status": "paused", "contact_id": 182}]}]}
    scenario = r"""
      await wait(); await wait();
      await apriElimina();
      const aiuto = TD().querySelector('[data-blocker-help]');
      report({ f: foglio(), aiuto: aiuto ? aiuto.visibleText() : null,
               link: TD().querySelectorAll('[data-blocker-help-link]').map((a) => a.getAttribute('href')) });
    """
    out = t2b3._run(staged, scenario, t2b3._rotte(check=(bloccato,)), "#/immobili/30")
    f = out["f"]
    assert f["blocchi"] == ["SELLER_OPPORTUNITY_OPEN"] and f["confermaDisabilitata"] is True
    assert bloccato["blockers"][0]["label"] in f["testo"] and "(1)" in f["testo"]   # backend invariato
    assert "Venditori › Sospesi" in out["aiuto"] and "rivolgiti a un amministratore dell’agenzia" in out["aiuto"]
    assert out["link"] == ["#/venditori/sospesi"]
    assert "182" not in out["aiuto"] and "#85" not in out["aiuto"]
    assert t2b3._scritture(out) == []
