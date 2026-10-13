"""CESTINO-GENERALE-2 (CG-2) - «Elimina…» dagli elenchi. Shell, nessun database.

  A  funzioni pure (node) di trash/trash-row-actions.js: comando, colonna,
     foglio della richiesta edificio;
  B  statici: nessun motore nuovo (gli elenchi non nominano rotte di Cestino o
     di eliminazione: usano i fogli esistenti), nessun DELETE, nessuna regola
     di permesso nel file condiviso, l'edificio passa SOLO dalla richiesta;
  C  shell sullo stub DOM (harness di DELETE-ARCH 2B3), per ogni elenco:
       c01 Immobili: comando sulle righe gestibili (titolare: tutte), il clic
           NON apre la scheda, stesso controllo della scheda, motivo, conferma,
           POST .../trash, toast, l'elenco si ricarica;
       c02 Immobili agente: comando solo sulle righe assegnate a lui; nessuna
           colonna se non ne ha;
       c03 blocco: blocchi del backend (con l'aiuto «Venditori sospesi»),
           conferma disabilitata, nessuna scrittura;
       c04 ultima riga di una pagina successiva: dopo lo spostamento si torna
           alla pagina precedente;
       c05 Contatti e c06 Richieste acquirente: stesso foglio della scheda;
       c07 Edifici: il comando apre la RICHIESTA (motivo), POST
           .../deletion-request; mai una rotta di Cestino dell'edificio.
"""
from __future__ import annotations

import json
import re
import subprocess

from tests import test_a30_5_create_ui as a30_5
from tests import test_a30_13b_quick_booking_ui as a30_13b
from tests import test_delete_arch_2b3_ui as t2b3
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

node = t2b3.node
ASSETS = t2b3.ASSETS
ROW = ASSETS / "trash" / "trash-row-actions.js"
rt = a30_5._rt()
_codice = t2b3._codice


def _pure(expr):
    script = (f"import * as m from '{ROW.as_posix()}';\n"
              f"console.log(JSON.stringify({expr}));")
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True,
                           text=True, timeout=30, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# A - funzioni pure
# ---------------------------------------------------------------------------

@node
def test_a01_comando_colonna_e_foglio_edificio():
    out = _pure("""(() => ({
      kinds: m.ROW_DELETE_KINDS,
      bottone: m.rowDeleteButtonHtml('contact', 41),
      ignoto: m.rowDeleteButtonHtml('lead', 1),
      colonna: (() => { const c = m.rowDeleteColumn('property', (r) => r.id === 1); return [c.label, c.render({id: 1}), c.render({id: 2})]; })(),
      foglio: m.rowDeleteDialogHtml(),
      edificio: m.buildingRequestSheetHtml({ id: 9, name: 'Palazzina <b>A</b>' }),
    }))()""")
    assert out["kinds"] == ["property", "contact", "buy_request", "building"]
    assert 'data-row-delete="contact:41"' in out["bottone"] and "Elimina…" in out["bottone"]
    assert 'aria-label="Elimina contatto"' in out["bottone"]
    assert out["ignoto"] == ""
    assert out["colonna"][0] == "Azioni" and 'data-row-delete="property:1"' in out["colonna"][1]
    assert out["colonna"][2] == ""
    assert out["foglio"] == '<dialog class="modal trash-sheet" data-row-delete-dialog></dialog>'
    e = out["edificio"]
    assert "Elimina edificio" in e and "Palazzina &lt;b&gt;A&lt;/b&gt;" in e and 'href="#/edifici/9"' in e


# ---------------------------------------------------------------------------
# B - statici
# ---------------------------------------------------------------------------

def test_b01_nessun_motore_nuovo_nessuna_regola_nel_file_condiviso():
    riga = _codice(ROW)
    for vietata in ("apiPost", "apiGet", "apiDelete", "'DELETE'", "/api/", "getSession", ".role", "is_platform_admin",
                    "agency_id", "assigned_agent_id", "purge", "Annulla", "Cancella", "Svuota"):
        assert vietata not in riga, vietata
    # i fogli e il pannello sono quelli delle schede, non copie
    for usato in ("openTrashDialog", "openContactTrashDialog", "openBuyTrashDialog", "mountDeletionPanel"):
        assert usato in riga, usato
    assert "trashBuilding" not in riga and "openBuildingTrashDialog" not in riga      # edificio: solo richiesta
    for vista, kind in (("immobili.js", "'property'"), ("contatti.js", "'contact'"), ("acquirenti.js", "'buy_request'"),
                        ("edifici.js", "'building'")):
        testo = _codice(ASSETS / "views" / vista)
        assert "bindRowDeletes(" in testo and kind in testo, vista
        assert not re.search(r"/api/[^`'\"]*(trash|restore)", testo) and "deletion-check" not in testo, vista
        assert "deletion-request" not in testo and "apiPost(" not in testo.split("bindRowDeletes(")[1][:400], vista
    # Immobili: la stessa regola della scheda per mostrare il comando
    assert "canManagePropertyLifecycle(p, sessione)" in _codice(ASSETS / "views" / "immobili.js")


@node
def test_b02_node_check():
    for rel in ("trash/trash-row-actions.js", "views/immobili.js", "views/contatti.js", "views/acquirenti.js",
                "views/edifici.js", "building-deletion/building-deletion.js"):
        esito = subprocess.run([a30_5.NODE, "--check", str(ASSETS / rel)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, (rel, esito.stderr)


def test_b03_css_tocco_e_colonna_visibile():
    css = (ASSETS / "app.css").read_text(encoding="utf-8")
    blocco = css[css.index("/* CESTINO-GENERALE-2"):]
    assert "min-height: 44px" in blocco and "position: sticky" in blocco and ":has(.trash-row-cell)" in blocco


# ---------------------------------------------------------------------------
# C - shell sullo stub DOM
# ---------------------------------------------------------------------------

def _immobile(pid, agente=None, **extra):
    return {"id": pid, "code": f"IMM-{pid}", "title": "Trilocale", "address": "Via Roma", "civic_number": str(pid),
            "city": "Giulianova", "property_type": "apartment", "commercial_status": "draft", "record_kind": "crm",
            "assigned_agent_id": agente, "updated_at": "2026-10-10T10:00:00+02:00", **extra}


PUO = {"can_trash": True, "blockers": []}
OPZIONI = {"territory": [], "energy_classes": [], "property_types": [], "can_assign": True, "agents": [],
           "building_types": []}


def _rotte(sessione="agency_owner", *, extra=(), liste=()):
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
        *extra,
        *liste,
        ("GET", "/api/property/form-options", [rt.ok(OPZIONI)]),
        ("GET", "/api/", [rt.ok({"items": []})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


HELPERS = r"""
const RD = () => C().querySelector('[data-row-delete-dialog]');
const comandi = () => C().querySelectorAll('[data-row-delete]').map((b) => b.dataset.rowDelete);
async function elimina(chiave) {
  C().querySelector(`[data-row-delete="${chiave}"]`).dispatch('click'); await wait(); await wait();
}
async function confermaRiga(motivo = 'duplicate') {
  RD().querySelectorAll('input[name="trash-reason"]').forEach((r) => { r.checked = r.getAttribute('value') === motivo; r.dispatch('change'); });
  RD().querySelector('[data-trash-form]').dispatch('submit'); await wait(); await wait(); await wait();
}
const toastTesto = () => { const t = __dom.main.querySelector('[data-census-toast]'); return t && !t.hidden ? t.visibleText() : null; };
"""


def _run(staged, scenario, rotte, hash):  # noqa: F811
    return t2b3._run(staged, HELPERS + scenario, rotte, hash)


def _scritture(out):
    return [(c["m"], c["url"]) for c in out["calls"] if c["m"] in ("POST", "PATCH", "PUT", "DELETE")]


def _get(out, prefisso):
    return [c["url"] for c in out["calls"] if c["m"] == "GET" and c["url"].startswith(prefisso)]


@node
def test_c01_immobili_elimina_dalla_riga_stesso_foglio_poi_elenco_ricaricato(staged):  # noqa: F811
    prima = {"items": [_immobile(30), _immobile(31)]}
    dopo = {"items": [_immobile(31)]}
    scenario = r"""
      await wait(); await wait();
      const visti = comandi();
      const hashPrima = window.location.hash;
      await elimina('property:30');
      const hashDopo = window.location.hash;
      const fase = { aperto: !!RD()._open, motivi: RD().querySelectorAll('input[name="trash-reason"]').length,
                     titolo: RD().visibleText() };
      await confermaRiga('duplicate');
      report({ visti, hashPrima, hashDopo, fase, dopo: comandi(), toast: toastTesto() });
    """
    rotte = _rotte(extra=[("GET", "/api/property/properties/30/deletion-check", [rt.ok(PUO)]),
                          ("POST", "/api/property/properties/30/trash", [rt.ok({**_immobile(30), "deleted_at": "2026-10-13T10:00:00+02:00"})])],
                   liste=[("GET", "/api/property/properties?", [rt.ok(prima), rt.ok(dopo)])])
    out = _run(staged, scenario, rotte, "#/immobili")
    assert out["visti"] == ["property:30", "property:31"]
    assert out["hashDopo"] == out["hashPrima"] == "#/immobili"          # il clic non apre la scheda
    assert out["fase"]["aperto"] is True and out["fase"]["motivi"] == 5
    assert "Elimina immobile" in out["fase"]["titolo"] and "IMM-30" in out["fase"]["titolo"]
    assert _scritture(out) == [("POST", "/api/property/properties/30/trash")]
    body = [c["body"] for c in out["calls"] if c["m"] == "POST"][0]
    assert body["reason_code"] == "duplicate"
    assert _get(out, "/api/property/properties/30/deletion-check") == ["/api/property/properties/30/deletion-check"]
    assert len(_get(out, "/api/property/properties?")) == 2                # elenco ricaricato
    assert out["dopo"] == ["property:31"]
    assert out["toast"] == "Immobile spostato nel Cestino"


@node
def test_c02_immobili_agente_solo_le_righe_assegnate(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      report({ visti: comandi(), colonne: C().querySelectorAll('th').map((t) => t.textContent.trim()) });
    """
    misto = {"items": [_immobile(30, agente=3), _immobile(31, agente=4), _immobile(32)]}
    out = _run(staged, scenario, _rotte("agent", liste=[("GET", "/api/property/properties?", [rt.ok(misto)])]), "#/immobili")
    assert out["visti"] == ["property:30"]                                 # rt.TENANT: agente, user 3
    assert "Azioni" in out["colonne"]
    nessuna = {"items": [_immobile(31, agente=4), _immobile(32)]}
    out = _run(staged, scenario, _rotte("agent", liste=[("GET", "/api/property/properties?", [rt.ok(nessuna)])]), "#/immobili")
    assert out["visti"] == [] and "Azioni" not in out["colonne"]


@node
def test_c03_immobili_bloccato_nessuna_scrittura(staged):  # noqa: F811
    bloccato = {"can_trash": False, "blockers": [
        {"code": "SELLER_OPPORTUNITY_OPEN", "label": "Opportunità Venditore aperta: chiudila prima da Venditori («Smetti…»)",
         "items": [{"id": 85, "status": "paused", "contact_id": 182}]}]}
    scenario = r"""
      await wait(); await wait();
      await elimina('property:30');
      RD().querySelector('[data-trash-form]').dispatch('submit'); await wait(); await wait();
      report({ testo: RD().visibleText(), disabilitata: RD().querySelector('[data-trash-confirm]').disabled,
               link: RD().querySelectorAll('[data-blocker-help-link]').map((a) => a.getAttribute('href')) });
    """
    rotte = _rotte(extra=[("GET", "/api/property/properties/30/deletion-check", [rt.ok(bloccato)])],
                   liste=[("GET", "/api/property/properties?", [rt.ok({"items": [_immobile(30)]})])])
    out = _run(staged, scenario, rotte, "#/immobili")
    assert "Non si può spostare nel Cestino" in out["testo"] and "Venditori › Sospesi" in out["testo"]
    assert out["disabilitata"] is True and out["link"] == ["#/venditori/sospesi"]
    assert _scritture(out) == []


@node
def test_c04_ultima_riga_di_una_pagina_successiva_torna_indietro(staged):  # noqa: F811
    pagina1 = {"items": [_immobile(i) for i in range(100, 150)]}
    pagina2 = {"items": [_immobile(200)]}
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#immobili-next').onclick(); await wait(); await wait();   // il pager usa onclick
      await elimina('property:200');
      await confermaRiga('test_record');
      report({ visti: comandi().length });
    """
    rotte = _rotte(extra=[("GET", "/api/property/properties/200/deletion-check", [rt.ok(PUO)]),
                          ("POST", "/api/property/properties/200/trash", [rt.ok({**_immobile(200), "deleted_at": "x"})])],
                   liste=[("GET", "/api/property/properties?", [rt.ok(pagina1), rt.ok(pagina2), rt.ok(pagina1)])])
    out = _run(staged, scenario, rotte, "#/immobili")
    urls = _get(out, "/api/property/properties?")
    assert [u.split("offset=")[1].split("&")[0] for u in urls] == ["0", "50", "0"], urls
    assert out["visti"] == 50


@node
def test_c05_contatti_stesso_foglio_della_scheda(staged):  # noqa: F811
    contatti = {"items": [{"id": 159, "display_name": "Gino Rossi", "email": "gino@x.test", "phone": "333",
                           "status": "active", "created_at": "2026-10-01T10:00:00+02:00"}]}
    scenario = r"""
      await wait(); await wait();
      const hashPrima = window.location.hash;
      await elimina('contact:159');
      const titolo = RD().visibleText();
      await confermaRiga('invalid_data');
      report({ hashPrima, hashDopo: window.location.hash, titolo, toast: toastTesto() });
    """
    rotte = _rotte(extra=[("GET", "/api/core/contacts/159/deletion-check", [rt.ok({**PUO, "history": [], "effects": {}})]),
                          ("POST", "/api/core/contacts/159/trash", [rt.ok({"id": 159, "deleted_at": "x"})])],
                   liste=[("GET", "/api/core/contacts?", [rt.ok(contatti), rt.ok({"items": []})])])
    out = _run(staged, scenario, rotte, "#/contatti")
    assert out["hashDopo"] == out["hashPrima"] == "#/contatti"
    assert "Elimina contatto" in out["titolo"] and "Gino Rossi" in out["titolo"]
    assert _scritture(out) == [("POST", "/api/core/contacts/159/trash")]
    assert len(_get(out, "/api/core/contacts?")) == 2
    assert out["toast"] == "Contatto spostato nel Cestino"


@node
def test_c06_richieste_acquirente_stesso_foglio_della_scheda(staged):  # noqa: F811
    richieste = {"items": [{"id": 12, "title": "Trilocale al mare", "contact_id": 159, "contact_name": "Gino Rossi",
                            "status": "active", "updated_at": "2026-10-01T10:00:00+02:00"}]}
    scenario = r"""
      await wait(); await wait();
      await elimina('buy_request:12');
      const titolo = RD().visibleText();
      await confermaRiga('duplicate');
      report({ hash: window.location.hash, titolo, toast: toastTesto() });
    """
    rotte = _rotte(extra=[("GET", "/api/buy/requests/12/deletion-check", [rt.ok({**PUO, "history": [], "effects": {}})]),
                          ("POST", "/api/buy/requests/12/trash", [rt.ok({"id": 12, "deleted_at": "x"})])],
                   liste=[("GET", "/api/buy/requests?", [rt.ok(richieste), rt.ok({"items": []})])])
    out = _run(staged, scenario, rotte, "#/acquirenti")
    assert out["hash"] == "#/acquirenti"
    assert "Elimina richiesta" in out["titolo"] and "Trilocale al mare" in out["titolo"]
    assert _scritture(out) == [("POST", "/api/buy/requests/12/trash")]
    assert len(_get(out, "/api/buy/requests?")) == 2
    assert out["toast"] == "Richiesta spostata nel Cestino"


@node
def test_c07_edifici_la_riga_apre_la_richiesta_mai_il_cestino_diretto(staged):  # noqa: F811
    edifici = {"items": [{"id": 9, "name": "Palazzina Mare", "address": "Via Mare", "civic_number": "3", "city": "Giulianova",
                          "census_summary": {}}], "total": 1}
    stato = {"building_id": 9, "units": {"total": 2, "active": 2, "items": []}, "request": None, "can_request": True,
             "can_withdraw": False, "in_trash": False, "blockers": []}
    in_attesa = {**stato, "can_request": False, "request": {"id": 55, "status": "pending", "requested_at": "2026-10-13T10:00:00+02:00",
                                                           "requested_by_name": "Olga", "reason_code": "duplicate"}}
    scenario = r"""
      await wait(); await wait();
      const visti = comandi();
      await elimina('building:9');
      const pannello = RD().visibleText();
      const motivo = !!RD().querySelector('[data-deletion-reason]');
      RD().querySelector('[data-deletion-confirm]').dispatch('click'); await wait(); await wait(); await wait();
      report({ visti, pannello, motivo, dopo: RD().visibleText(), hash: window.location.hash });
    """
    rotte = _rotte(extra=[("GET", "/api/property/buildings/9/deletion-request", [rt.ok(stato), rt.ok(in_attesa)]),
                          ("POST", "/api/property/buildings/9/deletion-request", [{"status": 201, "body": in_attesa["request"]}])],
                   liste=[("GET", "/api/property/buildings?", [rt.ok(edifici)])])
    out = _run(staged, scenario, rotte, "#/edifici")
    assert out["visti"] == ["building:9"] and out["hash"] == "#/edifici"
    assert out["motivo"] is True and "Coinvolge 2 unità" in out["pannello"] and "Conferma richiesta" in out["pannello"]
    assert "In attesa" in out["dopo"]
    assert _scritture(out) == [("POST", "/api/property/buildings/9/deletion-request")]
    assert not any("/trash" in c["url"] for c in out["calls"])
