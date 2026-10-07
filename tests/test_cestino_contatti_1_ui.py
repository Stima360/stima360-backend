"""CESTINO-CONTATTI-1 (FASE F) - UI del Cestino Contatti (Shell OS), senza database.

  M  funzioni pure di trash/trash-model.js (node): blocchi con voci,
     collegamenti SOLO interni, azione «Disattiva accesso», effetti sulle
     comunicazioni, possibili doppioni, percorso dell'elenco;
  S  statici: le rotte nominate solo da trash/trash-api.js; la scheda contatto
     usa il foglio condiviso; nessuna regola di permesso nel client; lessico;
     il comportamento del foglio immobile invariato (stesso HTML);
  D  Shell VERA (main.js, router, sessione, scheda contatto, pagina Cestino)
     nello stub DOM di P26-4/A30-5 con fetch instradato:
       d01  «Elimina…» -> blocchi con i loro collegamenti, conferma disabilitata;
            «Disattiva accesso» chiama la rotta esistente e ripete il controllo;
            motivo -> spostamento, toast, elenco Contatti;
       d02  contatto nel Cestino: scheda in sola lettura con «Ripristina»; il
            ripristino segnala i possibili doppioni (collegamenti), nulla unito;
       d03  pagina Cestino: scheda Immobili invariata e predefinita, scheda
            Contatti al primo tocco (o con #/cestino/contatti), ripristino;
       d04  403 HISTORY_REQUIRES_ADMIN alla conferma: messaggio, nessun bypass;
  H  Chromium a 390 e 1280 px: pagina Cestino (Contatti) e foglio della scheda
     contatto senza sforamento orizzontale, azioni da 44 px su smartphone.

Senza node le prove M/D sono SKIPPED (BLOCKED), mai passate.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests import test_a30_13b_quick_booking_ui as a30_13b
from tests import test_delete_arch_2b3_ui as t2b3
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "static" / "os_shell"
ASSETS = SHELL / "assets"
TRASH_DIR = ASSETS / "trash"
SCHEDA_JS = ASSETS / "views" / "contatto-dettaglio.js"

node = pytest.mark.skipif(a30_5.NODE is None, reason="node non disponibile: livello NON eseguito (BLOCKED)")
_codice = t2b3._codice


def _modello(expr):
    return t2b3._modello(expr)


# ---------------------------------------------------------------------------
# M - funzioni pure
# ---------------------------------------------------------------------------

@node
def test_m01_blocchi_contatto_voci_collegamenti_e_azione():
    out = _modello("""m.contactBlockerView([
      { code: 'LEAD_OPEN', label: 'Opportunità aperte', link: { href: '#/venditori', label: 'Apri Venditori' },
        items: [{ id: 7, label: 'Lead #7 · Vendita · aperta', href: '#/venditori' },
                { id: 8, label: 'Lead #8', href: 'https://evil.test/' }] },
      { code: 'OWNER_PORTAL_ACTIVE', label: 'Portale attivo', items: [{ id: 3, label: 'Accesso' }],
        action: { kind: 'owner_account_disable', account_id: 3, allowed: true, label: 'Disattiva accesso' } },
      { code: 'OWNER_PORTAL_ACTIVE', label: 'Portale attivo', items: [], action: { kind: 'owner_account_disable', account_id: 4, allowed: false } },
      { code: 'X', label: 'Altro', items: [{ id: 1 }], action: { kind: 'boh' } },
      { code: 'HISTORY_REQUIRES_ADMIN', label: 'backend', items: [{ code: 'CONTACT_ACTIVITY', label: 'Attività', count: 2 }] },
    ])""")
    assert out[0] == {"code": "LEAD_OPEN", "label": "Opportunità aperte", "count": 2, "history": None,
                      "link": {"href": "#/venditori", "label": "Apri Venditori"},
                      "items": [{"label": "Lead #7 · Vendita · aperta", "link": {"href": "#/venditori", "label": "Apri"}},
                                {"label": "Lead #8", "link": None}],          # mai un indirizzo esterno
                      "action": None}
    assert out[1]["action"] == {"kind": "owner_account_disable", "accountId": 3, "allowed": True,
                                "label": "Disattiva accesso"}
    assert out[2]["action"]["allowed"] is False
    assert out[3]["items"] == [] and out[3]["action"] is None     # voce senza etichetta, azione sconosciuta
    assert out[4]["label"] == ("Questo contatto ha uno storico operativo. Serve un amministratore per "
                               "spostarlo nel Cestino.")
    assert out[4]["history"] == [{"label": "Attività", "count": 2}]


@node
def test_m02_effetti_doppioni_nomi_e_percorso():
    out = _modello("""({
      e1: m.contactEffectsText({ queued_messages: 2, automations_to_pause: true }),
      e2: m.contactEffectsText({ queued_messages: 1, automations_already_paused: true }),
      e3: m.contactEffectsText({}),
      d: m.duplicatesView({ possible_duplicates: [
        { id: 4, display_name: 'Mario Rossi', same_email: true, same_phone: true },
        { id: 5, first_name: 'Ada', last_name: 'B', same_email: false, same_phone: true }] }),
      d0: m.duplicatesView({}),
      nome: [m.contactName({ id: 9, company_name: 'ACME' }), m.contactName({ id: 9 })],
      riga: m.contactLine({ email: 'a@b.it', phone: '333' }),
      percorso: m.contactTrashListPath(50, 50),
      e404: m.contactTrashErrorText({ status: 404 }),
      e403: m.contactTrashErrorText({ status: 403, code: 'HISTORY_REQUIRES_ADMIN' }),
      altro: m.contactTrashErrorText({ status: 409, message: 'dal backend' }),
    })""")
    assert out["e1"] == ["Le automazioni del contatto verranno sospese.", "2 messaggi in coda verranno annullati."]
    assert out["e2"] == ["Le automazioni del contatto sono già sospese.", "1 messaggio in coda verrà annullato."]
    assert out["e3"] == []
    assert out["d"] == [{"id": 4, "name": "Mario Rossi", "reason": "stessa email e stesso telefono"},
                        {"id": 5, "name": "Ada B", "reason": "stesso telefono"}]
    assert out["d0"] == [] and out["nome"] == ["ACME", "Contatto #9"] and out["riga"] == "a@b.it · 333"
    assert out["percorso"] == "/api/core/trash/contacts?limit=50&offset=50"
    assert out["e404"] == "Contatto non trovato." and "storico operativo" in out["e403"]
    assert out["altro"] == "dal backend"


# ---------------------------------------------------------------------------
# S - statici
# ---------------------------------------------------------------------------

ROTTE_CONTATTI = (re.compile(r"/api/core/contacts/\$\{[^}]+\}/(deletion-check|trash|restore)"),
                  re.compile(r"/api/owner/admin/accounts/"))


def test_s01_rotte_solo_nel_client_del_cestino():
    for rotta in ROTTE_CONTATTI:
        fuori = sorted(p.relative_to(ASSETS).as_posix() for p in ASSETS.rglob("*.js") if rotta.search(_codice(p)))
        assert fuori == ["trash/trash-api.js"], (rotta.pattern, fuori)
    elenco = sorted(p.relative_to(ASSETS).as_posix() for p in ASSETS.rglob("*.js")
                    if "/api/core/trash/contacts" in _codice(p))
    assert elenco == ["trash/trash-model.js"]


def test_s02_scheda_contatto_usa_il_foglio_condiviso_e_nessuna_regola():
    scheda = _codice(SCHEDA_JS)
    assert "contactTrashButtonHtml()" in scheda
    assert "bindContactTrashButton(container, contact, () => navigate('contatti'));" in scheda
    assert "deletion-check" not in scheda and not re.search(r"/(trash|restore)`", scheda)
    # la scheda nel Cestino: nessun comando di modifica, solo «Ripristina» se il backend lo consente
    assert "const inTrash = Boolean(contact.deleted_at);" in scheda
    assert "info.can_restore" in scheda and "if (inTrash) control = '';" in scheda
    for p in [*TRASH_DIR.glob("*.js"), ASSETS / "views" / "cestino.js"]:
        testo = _codice(p)
        for regola in ("deleted_by_user_id", ".role", "getSession", "agency_owner", "is_platform_admin"):
            assert regola not in testo, (p.name, regola)


def test_s03_lessico_e_comunicazioni_in_sola_lettura():
    tutto = "\n".join(_codice(p) for p in (*TRASH_DIR.glob("*.js"), ASSETS / "views" / "cestino.js", SCHEDA_JS))
    for parola in ("Elimina…", "Sposta nel Cestino", "Ripristina", "Contatto spostato nel Cestino",
                   "Contatto ripristinato", "Nel Cestino", "Disattiva accesso"):
        assert parola in tutto, parola
    cestino = "\n".join(_codice(p) for p in (*TRASH_DIR.glob("*.js"), ASSETS / "views" / "cestino.js"))
    for vietata in ("Cancella", "cancella", "Annulla", "Elimina definitivamente", "purge", "Svuota"):
        assert vietata not in cestino, vietata
    comm = _codice(ASSETS / "components" / "communications.js")
    assert "export function mountCommunications(mount, contactId, { readOnly = false } = {})" in comm
    assert "if (inTrash) await mountCommunications(contentEl, contact.id, { readOnly: true });" in _codice(SCHEDA_JS)


@node
def test_s04_node_check_e_foglio_immobile_invariato():
    for rel in ("trash/trash-model.js", "trash/trash-api.js", "trash/trash-dialog.js", "views/cestino.js",
                "views/contatto-dettaglio.js", "components/communications.js", "main.js"):
        esito = subprocess.run([a30_5.NODE, "--check", str(ASSETS / rel)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, (rel, esito.stderr)
    # l'HTML del foglio immobile e' lo stesso di prima per ogni fase
    script = (f"import * as d from '{(TRASH_DIR / 'trash-dialog.js').as_posix()}';\n"
              "const p = { id: 30, code: 'IMM-30', address: 'Via Roma', civic_number: '1', city: 'Giulianova' };\n"
              "console.log(JSON.stringify(['loading','confirm','blocked','error'].map((f) => d.trashDialogHtml(p, "
              "{ phase: f, blockers: [{ code: 'X', label: 'L', items: [{}] }], error: 'e' }))));")
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                           timeout=30, cwd=ASSETS, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    if esito.returncode != 0 and "Cannot find" in esito.stderr:
        pytest.skip("import del componente fuori dalla Shell non risolvibile in questo ambiente")
    assert esito.returncode == 0, esito.stderr
    fasi = json.loads(esito.stdout.strip().splitlines()[-1])
    assert "Elimina immobile" in fasi[0] and "IMM-30 · Via Roma 1, Giulianova" in fasi[0]
    assert "L'immobile esce da tutte le liste operative. Potrai ripristinarlo dal Cestino." in fasi[1]
    assert 'data-blocker="X"' in fasi[2] and "data-blocker-items" not in fasi[2]


# ---------------------------------------------------------------------------
# D - Shell eseguita (stub DOM)
# ---------------------------------------------------------------------------

rt = a30_5._rt()


def _scheda(cid=159, **extra):
    contatto = {"id": cid, "contact_type": "person", "first_name": "Prova", "last_name": "Uno",
                "display_name": "Prova Uno", "email": "prova@example.it", "phone": "333 1234567",
                "assigned_agent_id": 3, "status": "active", **extra}
    return {"contact": contatto, "roles": [{"role": "buyer"}], "leads": [
        {"id": 7, "pipeline": "sell", "stage": "new", "status": "closed", "priority": "normal"}],
        "properties": [], "buy_requests": [], "matches": [], "visits": [], "activities": [], "tasks": [],
        "owner_home": {}}


BLOCCATO = {"can_trash": False, "history": [], "blockers": [
    {"code": "LEAD_OPEN", "label": "Opportunità aperte: chiudile prima",
     "link": {"href": "#/venditori", "label": "Apri Venditori"},
     "items": [{"id": 7, "label": "Lead #7 · Vendita · aperta", "href": "#/venditori"}]},
    {"code": "BUY_REQUEST_OPEN", "label": "Richieste d'acquisto aperte",
     "items": [{"id": 12, "label": "Richiesta #12 · attiva", "href": "#/acquirenti/12"}]},
    {"code": "OWNER_PORTAL_ACTIVE", "label": "Ha un accesso al portale proprietario attivo",
     "items": [{"id": 77, "label": "Accesso al portale proprietario · attivo"}],
     "action": {"kind": "owner_account_disable", "account_id": 77, "allowed": True, "label": "Disattiva accesso"}}]}
PUO = {"can_trash": True, "blockers": [],
       "history": [{"code": "CONTACT_ACTIVITY", "label": "Attività o interazioni registrate", "count": 3}],
       "effects": {"queued_messages": 2, "automations_to_pause": True, "automations_already_paused": False}}
NEL_CESTINO = {"deleted_at": "2026-10-06T08:00:00+02:00", "deleted_reason": "duplicate", "deleted_by_user_id": 3,
               "trash": {"deleted_at": "2026-10-06T08:00:00+02:00", "deleted_reason": "duplicate",
                         "deleted_by_user_id": 3, "deleted_by_name": "Anna Agente",
                         "deleted_note": "Doppione di Mario", "can_restore": True}}
RIPRISTINATO = {"status": 200, "body": {"id": 159, "display_name": "Prova Uno", "deleted_at": None,
                                        "possible_duplicates": [{"id": 4, "display_name": "Mario Rossi",
                                                                 "same_email": True, "same_phone": False}]}}


def _voce(cid, **extra):
    v = {"id": cid, "contact_type": "person", "display_name": f"Contatto {cid}", "email": f"c{cid}@example.it",
         "phone": "333", "status": "active", "archived_at": None, "deleted_at": "2026-10-05T18:30:00+02:00",
         "deleted_reason": "duplicate", "deleted_by_user_id": 3, "deleted_by_name": "Anna Agente", "deleted_note": None}
    v.update(extra)
    return v


ELENCO_CONTATTI = {"items": [_voce(159, deleted_note="Doppione di Mario"), _voce(160, deleted_reason="test_record")],
                   "has_more": False, "limit": 50, "offset": 0}


def _rotte(sessione="agency_owner", *, scheda=None, check=(PUO,), trash=None, restore=(RIPRISTINATO,),
           disable=({"status": 200, "body": {"id": 77, "status": "disabled"}},), lista=(ELENCO_CONTATTI,)):
    trash = trash or ({"status": 200, "body": {**_scheda()["contact"], **NEL_CESTINO,
                                               "communications": {"cancelled_messages": 2}}},)
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
        ("GET", "/api/crm/contacts/159/360", [rt.ok(scheda or _scheda())]),
        ("GET", "/api/core/contacts/159/deletion-check", [c if "status" in c else rt.ok(c) for c in check]),
        ("POST", "/api/core/contacts/159/trash", list(trash)),
        ("POST", "/api/core/contacts/159/restore", list(restore)),
        ("POST", "/api/core/contacts/160/restore", [{"status": 200, "body": {"id": 160, "possible_duplicates": []}}]),
        ("POST", "/api/owner/admin/accounts/77/disable", list(disable)),
        ("GET", "/api/core/trash/contacts", [rt.ok(x) for x in lista]),
        ("GET", "/api/property/trash", [rt.ok({"items": [], "has_more": False, "limit": 50, "offset": 0})]),
        ("GET", "/api/appointments/agents", [rt.ok({"items": []})]),
        ("GET", "/api/core/contacts", [rt.ok({"items": []})]),
        ("GET", "/api/", [rt.ok({"items": []})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


HELPERS = r"""
const CD = () => C().querySelector('#contact-trash-dialog');
const cq = (s) => CD().querySelector(s);
const ctoast = () => { const t = __dom.main.querySelector('[data-census-toast]'); return t && !t.hidden ? t.visibleText() : null; };
function cScegli(valore) {
  for (const r of CD().querySelectorAll('input[name="trash-reason"]')) {
    r.checked = r.getAttribute('value') === valore;
    if (r.checked) r.dispatch('change');
  }
}
function cFoglio() {
  if (!CD()) return null;
  return { open: !!CD()._open, testo: CD().visibleText(),
           blocchi: CD().querySelectorAll('[data-blocker]').map((li) => li.dataset.blocker),
           voci: CD().querySelectorAll('[data-blocker-item-link]').map((a) => [a.getAttribute('href'), a.textContent]),
           azioni: CD().querySelectorAll('[data-trash-action]').map((b) => [b.dataset.trashAction, b.dataset.accountId, b.textContent]),
           confermaDisabilitata: cq('[data-trash-confirm]').disabled,
           motivi: CD().querySelectorAll('input[name="trash-reason"]').map((r) => r.getAttribute('value')),
           errore: cq('[data-trash-error]').textContent };
}
"""


def _run(staged, scenario, rotte, hash):  # noqa: F811
    return t2b3._run(staged, HELPERS + scenario, rotte, hash)


def _scritture(out):
    return [(c["m"], c["url"]) for c in out["calls"] if c["m"] in ("POST", "PATCH", "PUT", "DELETE")]


@node
def test_d01_blocchi_disattiva_accesso_poi_sposta(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const elimina = C().querySelector('#contact-trash-btn');
      const etichetta = elimina ? elimina.textContent : null;
      elimina.dispatch('click'); await wait(); await wait();
      const bloccato = cFoglio();
      cq('[data-trash-form]').dispatch('submit'); await wait();
      const forzato = _writes();
      cq('[data-trash-action]').dispatch('click'); await wait(); await wait();
      const fase2 = cFoglio();
      cScegli('duplicate');
      const dopoMotivo = cq('[data-trash-confirm]').disabled;
      cq('[data-trash-note]').value = '  Doppione di Mario  ';
      cq('[data-trash-form]').dispatch('submit'); await wait(); await wait();
      const hashDopo = window.location.hash;
      report({ etichetta, bloccato, forzato, conferma: fase2, dopoMotivo, hashDopo, t: ctoast() });
      function _writes() { return __calls.filter((c) => (c.options.method || 'GET') !== 'GET').length; }
    """
    out = _run(staged, scenario, _rotte(check=(BLOCCATO, PUO)), "#/contatti/159")
    assert out["etichetta"] == "Elimina…"
    b = out["bloccato"]
    assert b["open"] is True and b["blocchi"] == ["LEAD_OPEN", "BUY_REQUEST_OPEN", "OWNER_PORTAL_ACTIVE"]
    assert ["#/venditori", "Lead #7 · Vendita · aperta"] in b["voci"]
    assert ["#/acquirenti/12", "Richiesta #12 · attiva"] in b["voci"]
    assert b["azioni"] == [["owner_account_disable", "77", "Disattiva accesso"]]
    assert b["confermaDisabilitata"] is True and b["motivi"] == [] and out["forzato"] == 0
    c = out["conferma"]
    assert c["blocchi"] == [] and c["motivi"] == ["created_by_mistake", "duplicate", "invalid_data", "test_record", "other"]
    assert "Le automazioni del contatto verranno sospese." in c["testo"]
    assert "2 messaggi in coda verranno annullati." in c["testo"]
    assert "Lo storico resta consultabile" in c["testo"] and "Attività o interazioni registrate" in c["testo"]
    assert out["dopoMotivo"] is False
    assert _scritture(out) == [("POST", "/api/owner/admin/accounts/77/disable"), ("POST", "/api/core/contacts/159/trash")]
    trash = [x for x in out["calls"] if x["m"] == "POST" and x["url"].endswith("/trash")]
    assert trash[0]["body"] == {"reason_code": "duplicate", "note": "Doppione di Mario"}
    assert [x["url"] for x in out["calls"] if "deletion-check" in x["url"]] == [
        "/api/core/contacts/159/deletion-check"] * 2              # dopo «Disattiva» il controllo si ripete
    assert out["hashDopo"] == "#/contatti" and out["t"] == "Contatto spostato nel Cestino"


@node
def test_d01b_azione_non_consentita_non_ha_bottone(staged):  # noqa: F811
    solo_testo = {"can_trash": False, "history": [], "blockers": [
        {"code": "OWNER_PORTAL_ACTIVE", "label": "Ha un accesso al portale proprietario attivo: lo disattiva il titolare.",
         "items": [{"id": 77, "label": "Accesso"}],
         "action": {"kind": "owner_account_disable", "account_id": 77, "allowed": False, "label": "Disattiva accesso"}}]}
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#contact-trash-btn').dispatch('click'); await wait(); await wait();
      report({ f: cFoglio() });
    """
    out = _run(staged, scenario, _rotte("agency_admin", check=(solo_testo,)), "#/contatti/159")
    assert out["f"]["azioni"] == [] and "lo disattiva il titolare" in out["f"]["testo"]
    assert _scritture(out) == []


@node
def test_d02_scheda_nel_cestino_sola_lettura_e_ripristino(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const banner = C().querySelector('[data-contact-in-trash]');
      const prima = { banner: banner ? banner.visibleText() : null,
                      modifica: !!C().querySelector('#contact-edit-btn'), elimina: !!C().querySelector('#contact-trash-btn'),
                      attivita: !!C().querySelector('#contact-quick-activity'),
                      ruoli: C().querySelectorAll('.role-remove-btn').length, assegna: !!C().querySelector('#assignment-select') };
      C().querySelectorAll('.tab-btn').find((b) => b.dataset.tab === 'lead').dispatch('click'); await wait();
      prima.leadBottoni = C().querySelectorAll('[data-lead-edit]').length + (C().querySelector('#lead-new-btn') ? 1 : 0);
      C().querySelector('[data-contact-restore-btn]').dispatch('click'); await wait(); await wait(); await wait();
      const avviso = C().querySelector('[data-contact-duplicates]');
      report({ prima, avviso: avviso ? avviso.visibleText() : null,
               link: avviso ? avviso.querySelectorAll('a').map((a) => a.getAttribute('href')) : [],
               t: ctoast(), modificaDopo: !!C().querySelector('#contact-edit-btn') });
    """
    nel = _scheda(**NEL_CESTINO)
    out = _run(staged, scenario, _rotte(scheda=nel), "#/contatti/159")
    p = out["prima"]
    assert "Nel Cestino" in p["banner"] and "Anna Agente" in p["banner"] and "Duplicato — Doppione di Mario" in p["banner"]
    assert "Il ripristino non riattiva automazioni né messaggi." in p["banner"]
    assert (p["modifica"], p["elimina"], p["attivita"], p["ruoli"], p["assegna"], p["leadBottoni"]) == (
        False, False, False, 0, False, 0)
    assert _scritture(out) == [("POST", "/api/core/contacts/159/restore")]
    assert out["t"] == "Contatto ripristinato"
    assert "Possibili doppioni attivi" in out["avviso"] and "Mario Rossi" in out["avviso"]
    assert "stessa email" in out["avviso"] and "Nessun contatto è stato unito o modificato." in out["avviso"]
    assert out["link"] == ["#/contatti/4"]


@node
def test_d02b_senza_permesso_di_ripristino_nessun_bottone(staged):  # noqa: F811
    nel = _scheda(**{**NEL_CESTINO, "trash": {**NEL_CESTINO["trash"], "can_restore": False}})
    scenario = r"""
      await wait(); await wait();
      report({ bottone: !!C().querySelector('[data-contact-restore-btn]'),
               testo: C().querySelector('[data-contact-in-trash]').visibleText() });
    """
    out = _run(staged, scenario, _rotte("agent", scheda=nel), "#/contatti/159")
    assert out["bottone"] is False and "Può ripristinarlo chi lo ha spostato o un amministratore." in out["testo"]


@node
def test_d03_pagina_cestino_schede_e_ripristino(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const iniziale = { immobili: !C().querySelector('[data-trash-panel="immobili"]').hidden,
                         contatti: !C().querySelector('[data-trash-panel="contatti"]').hidden,
                         letture: __calls.filter((c) => c.url.startsWith('/api/core/trash/contacts')).length };
      C().querySelector('[data-trash-tab="contatti"]').dispatch('click'); await wait(); await wait();
      const carte = C().querySelectorAll('[data-contact-trash-item]').map((c) => [c.dataset.contactTrashItem, c.visibleText()]);
      C().querySelector('[data-contact-restore="159"]').dispatch('click'); await wait(); await wait();
      report({ iniziale, carte, dopo: C().querySelectorAll('[data-contact-trash-item]').map((c) => c.dataset.contactTrashItem),
               avviso: C().querySelector('[data-contact-duplicates]') ? C().querySelector('[data-contact-duplicates]').visibleText() : null,
               t: ctoast() });
    """
    out = _run(staged, scenario, _rotte(), "#/cestino")
    assert out["iniziale"] == {"immobili": True, "contatti": False, "letture": 0}     # Immobili resta la predefinita
    assert [c[0] for c in out["carte"]] == ["159", "160"]
    uno = out["carte"][0][1]
    assert "Contatto 159" in uno and "c159@example.it" in uno and "Duplicato — Doppione di Mario" in uno
    assert "Anna Agente" in uno and "Attivo" in uno
    assert out["dopo"] == ["160"] and out["t"] == "Contatto ripristinato" and "Mario Rossi" in out["avviso"]
    letture = [c["url"] for c in out["calls"] if c["url"].startswith("/api/core/trash/contacts")]
    assert letture == ["/api/core/trash/contacts?limit=50&offset=0"]
    assert _scritture(out) == [("POST", "/api/core/contacts/159/restore")]


@node
def test_d03b_link_diretto_alla_scheda_contatti(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      report({ contatti: !C().querySelector('[data-trash-panel="contatti"]').hidden,
               carte: C().querySelectorAll('[data-contact-trash-item]').length });
    """
    out = _run(staged, scenario, _rotte(), "#/cestino/contatti")
    assert out["contatti"] is True and out["carte"] == 2
    assert not any(c["url"].startswith("/api/property/trash") for c in out["calls"])


@node
def test_d04_storico_alla_conferma_nessun_bypass(staged):  # noqa: F811
    rifiuto = {"status": 403, "body": {"detail": "storico (backend)", "code": "HISTORY_REQUIRES_ADMIN",
                                       "history": [{"code": "LEAD_HISTORY", "label": "Opportunità concluse", "count": 1}]}}
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#contact-trash-btn').dispatch('click'); await wait(); await wait();
      cScegli('other');
      cq('[data-trash-form]').dispatch('submit'); await wait(); await wait();
      const esito = cFoglio();
      cq('[data-trash-form]').dispatch('submit'); await wait();
      report({ f: esito, hash: window.location.hash });
    """
    out = _run(staged, scenario, _rotte("agent", trash=(rifiuto,)), "#/contatti/159")
    assert "Serve un amministratore" in out["f"]["testo"] and "Opportunità concluse" in out["f"]["testo"]
    assert out["f"]["confermaDisabilitata"] is True and out["hash"] == "#/contatti/159"
    assert _scritture(out) == [("POST", "/api/core/contacts/159/trash")]


# ---------------------------------------------------------------------------
# H - Chromium: smartphone e desktop
# ---------------------------------------------------------------------------

def test_h01_css_nuovo_solo_regole_trash():
    css = (ASSETS / "app.css").read_text(encoding="utf-8")
    blocco = css[css.index("/* CESTINO-CONTATTI-1"):]
    for selettore in re.findall(r"^([^@/{}\n][^{]*)\{", blocco, flags=re.M):
        assert "trash" in selettore, selettore
    assert re.findall(r"(?<![-\w])width:\s*(\d+)px", blocco) == []
    assert ".trash-blocker-action .btn, .trash-banner-actions .btn { width: 100%; min-height: 44px; }" in blocco


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_h02_chromium_cestino_contatti_e_foglio(larghezza):
    sync_playwright = t2b3._chromium()
    lungo = "Contatto con un nome davvero molto lungo che non deve sforare " * 2
    elenco = {"items": [_voce(159, display_name=lungo, email="indirizzo.lunghissimo.per.prova@example-dominio.it",
                              deleted_note="Nota molto lunga " * 12), _voce(160)],
              "has_more": True, "limit": 50, "offset": 0}
    bloccato = {**BLOCCATO, "blockers": [*BLOCCATO["blockers"],
                                         {"code": "MANDATE_OWNER", "label": "Proprietario di immobili con incarico " * 3,
                                          "items": [{"id": 30, "label": "IMM-30 · " + lungo, "href": "#/immobili/30"}]}]}
    api = [("/api/operator-auth/me", t2b3._ME), ("/api/core/trash/contacts", elenco),
           ("/api/property/trash", {"items": [], "has_more": False}),
           ("/api/core/contacts/159/deletion-check", bloccato),
           ("/api/crm/contacts/159/360", _scheda(display_name=lungo)), ("/api/appointments/agents", {"items": []})]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": larghezza, "height": 820})
            t2b3._servi(page, api)
            page.goto("http://stima360.test/os/#/cestino/contatti")
            page.wait_for_selector("[data-contact-trash-item='159']")
            pagina = t2b3._sfora(page)
            restore_h = page.evaluate("""() => document.querySelector('[data-contact-restore="159"]').getBoundingClientRect().height""")
            page.goto("http://stima360.test/os/#/contatti/159")
            page.wait_for_selector("#contact-trash-btn")
            page.click("#contact-trash-btn")
            page.wait_for_selector("[data-blocker='MANDATE_OWNER']")
            foglio = t2b3._sfora(page)
            azione_h = page.evaluate("""() => document.querySelector('[data-trash-action]').getBoundingClientRect().height""")
            sheet = page.evaluate("""() => { const r = document.querySelector('#contact-trash-dialog').getBoundingClientRect();
                                             return { w: r.width, h: innerHeight, bottom: r.bottom }; }""")
            browser.close()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium non disponibile: misura NON eseguita (BLOCKED)")
        raise
    for misura in (pagina, foglio):
        assert misura["scroll"] <= misura["inner"] and misura["fuori"] == [], misura
    if larghezza < 768:
        assert restore_h >= 44 and azione_h >= 44
        assert abs(sheet["w"] - larghezza) <= 1 and abs(sheet["bottom"] - sheet["h"]) <= 1    # bottom-sheet
    else:
        assert sheet["w"] <= 520
