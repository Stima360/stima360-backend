"""DELETE-ARCH Fase 2B3 - UI del Cestino Immobili (Shell OS).

Tre livelli, nessun database:

  M  funzioni pure di trash/trash-model.js (node): motivi, blocchi, messaggi;
  S  statici: le rotte del Cestino nominate solo da trash/trash-api.js, mai la
     DELETE legacy dell'immobile, nessuna regola di permesso/blocco nel client,
     lessico («Elimina…», «Sposta nel Cestino», «Ripristina»; mai «Cancella»),
     node --check;
  A-G  Shell VERA (main.js, router, sessione, scheda immobile, pagina Cestino)
     eseguita nello stub DOM di P26-4/A30-5 con fetch instradata (stesso
     harness di VENDITORI-1 e CRM-OPS-3):
       A  «Elimina…» presente quando autorizzato (stessa regola di «Archivia»);
       B  deletion-check con blocchi: i blocchi del backend, conferma disabilitata;
       C  trash riuscito: toast, lista Immobili, nessuna DELETE;
       D  403 HISTORY_REQUIRES_ADMIN: messaggio corretto, nessun bypass;
       E  pagina Cestino: admin vede gli immobili dell'agenzia, agent i propri
          (lo decide il backend: la pagina non filtra e non aggiunge parametri);
       F  Ripristina: la card sparisce + toast, nessuna rilettura;
       G  RESTORE_CONFLICT: messaggio leggibile con l'immobile in conflitto,
          nessuna modifica locale finta;
  H  mobile: regole CSS (bottom-sheet, 44px, niente larghezze fisse) e misura
     vera in Chromium (Playwright) a 360px: nessuno sforamento orizzontale.

Senza node le prove A-G sono SKIPPED (da riportare come BLOCKED), mai passate.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
from pathlib import Path

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests import test_a30_13b_quick_booking_ui as a30_13b
from tests import test_crm_ops_3_acquisitions as base
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "static" / "os_shell"
ASSETS = SHELL / "assets"
TRASH_DIR = ASSETS / "trash"

node = pytest.mark.skipif(a30_5.NODE is None, reason="node non disponibile: livello NON eseguito (BLOCKED)")
HISTORY_TEXT = "Questo immobile ha uno storico operativo. Serve un amministratore per spostarlo nel Cestino."


def _codice(p):
    """Il file senza le righe di commento: conta solo cio' che il browser esegue."""
    return "\n".join(r for r in p.read_text(encoding="utf-8").splitlines()
                     if not r.lstrip().startswith(("//", "*", "/*")))


# ---------------------------------------------------------------------------
# M - funzioni pure
# ---------------------------------------------------------------------------

def _modello(expr):
    script = (f"import * as m from '{(TRASH_DIR / 'trash-model.js').as_posix()}';\n"
              f"console.log(JSON.stringify({expr}));")
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True,
                           text=True, timeout=30, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


@node
def test_m01_motivi_sono_quelli_del_backend_nello_stesso_ordine():
    out = _modello("m.TRASH_REASONS")
    assert [(r["value"], r["label"]) for r in out] == [
        ("created_by_mistake", "Creato per errore"), ("duplicate", "Duplicato"),
        ("invalid_data", "Dati non validi"), ("test_record", "Record di prova"), ("other", "Altro")]
    sorgente = (ROOT / "property" / "lifecycle.py").read_text(encoding="utf-8")
    assert ('TRASH_REASONS = ("created_by_mistake", "duplicate", "invalid_data", "test_record", "other")'
            in sorgente)


@node
def test_m02_blocchi_mostrati_come_arrivano_e_storico_con_la_sua_frase():
    out = _modello("""({
      blocchi: m.blockerView([{ code: 'ACQUISITION_OPEN', label: 'Acquisizione aperta', items: [{id: 1}, {id: 2}] },
                              { code: 'X_NUOVO', label: 'Blocco che la UI non conosce', items: [] }]),
      storico: m.blockerView([{ code: 'HISTORY_REQUIRES_ADMIN', label: 'testo backend',
                                items: [{ code: 'PROPERTY_ACTIVITY', label: 'Attività registrate', count: 3 }] }]),
      e403: m.trashErrorText({ status: 403, code: 'HISTORY_REQUIRES_ADMIN', message: 'altro' }),
      e403b: m.errorBlockers({ status: 403, code: 'HISTORY_REQUIRES_ADMIN',
                               data: { history: [{ code: 'SALE_HISTORY', label: 'Vendite', count: 1 }] } }),
      e409: m.errorBlockers({ status: 409, code: 'TRASH_BLOCKED', data: { blockers: [{ code: 'SALE_PENDING', label: 'Vendita' }] } }),
      altro: m.trashErrorText({ status: 403, code: 'NOT_ASSIGNED', message: 'Questo immobile non è assegnato a te' }),
    })""")
    # SENTINELLA AGGIORNATA DA FIX-MANDATE-1: ogni voce porta anche `link`
    # (il collegamento interno che il backend puo' indicare; qui nessuno).
    assert out["blocchi"] == [
        {"code": "ACQUISITION_OPEN", "label": "Acquisizione aperta", "count": 2, "history": None, "link": None},
        {"code": "X_NUOVO", "label": "Blocco che la UI non conosce", "count": None, "history": None, "link": None}]
    assert out["storico"] == [{"code": "HISTORY_REQUIRES_ADMIN", "label": HISTORY_TEXT, "count": None,
                               "history": [{"label": "Attività registrate", "count": 3}]}]
    assert out["e403"] == HISTORY_TEXT
    assert out["e403b"] == [{"code": "HISTORY_REQUIRES_ADMIN",
                             "items": [{"code": "SALE_HISTORY", "label": "Vendite", "count": 1}]}]
    assert out["e409"] == [{"code": "SALE_PENDING", "label": "Vendita"}]
    assert out["altro"] == "Questo immobile non è assegnato a te"     # il detail del backend, invariato


@node
def test_m03_conflitto_di_ripristino_e_percorso_elenco():
    out = _modello("""({
      conflitto: m.restoreErrorView({ status: 409, code: 'RESTORE_CONFLICT', message: 'Non si può ripristinare',
        data: { conflicts: [{ id: 31, code: 'IMM-31', index: 'cadastral_identity' },
                            { id: 32, code: null, index: 'client_request' }] } }),
      altro: m.restoreErrorView({ status: 403, code: 'NOT_DELETED_BY_YOU', message: 'Solo i tuoi' }),
      percorso: m.trashListPath(50, 50),
      riga: m.propertyLine({ address: 'Via Roma', civic_number: '1', city: 'Giulianova', title: 'T' }),
      soloTitolo: m.propertyLine({ title: 'Trilocale' }),
    })""")
    assert out["conflitto"] == {"text": "Non si può ripristinare", "conflicts": [
        {"id": 31, "code": "IMM-31", "label": "stessa identità catastale"},
        {"id": 32, "code": "#32", "label": "stessa richiesta di creazione"}]}
    assert out["altro"] == {"text": "Solo i tuoi", "conflicts": []}
    assert out["percorso"] == "/api/property/trash?limit=50&offset=50"
    assert out["riga"] == "Via Roma 1, Giulianova" and out["soloTitolo"] == "Trilocale"


# ---------------------------------------------------------------------------
# S - statici
# ---------------------------------------------------------------------------

ROTTE_CESTINO = (re.compile(r"/deletion-check"), re.compile(r"\}/trash`"), re.compile(r"\}/restore`"),
                 re.compile(r"/api/property/trash"))


def test_s01_le_rotte_del_cestino_sono_nominate_solo_dal_client_proprio():
    for rotta in ROTTE_CESTINO:
        fuori = sorted(p.relative_to(ASSETS).as_posix() for p in ASSETS.rglob("*.js")
                       if rotta.search(_codice(p)))
        # trash-model.js costruisce solo il percorso dell'elenco, per trash-api.js
        attesi = ["trash/trash-model.js"] if rotta.pattern == "/api/property/trash" else ["trash/trash-api.js"]
        assert fuori == attesi, (rotta.pattern, fuori)


def test_s02_nessuna_delete_legacy_e_nessuna_regola_nel_client():
    for p in [*TRASH_DIR.glob("*.js"), ASSETS / "views" / "cestino.js"]:
        testo = _codice(p)
        assert "apiDelete" not in testo and "'DELETE'" not in testo, p.name
        assert "agency_id" not in testo, p.name
        # chi puo', i blocchi e i conflitti li decide il backend
        for regola in ("assigned_agent_id", "deleted_by_user_id", ".role", "getSession", "is_platform_admin",
                       "client_request_id", "acquisition_id", "mandate_"):
            assert regola not in testo, (p.name, regola)
    # la scheda chiama il foglio, non le rotte
    scheda = _codice(ASSETS / "views" / "immobile-dettaglio.js")
    assert "trashButtonHtml(canManagePropertyLifecycle(property, getSession()))" in scheda
    assert "bindTrashButton(container, property, () => navigate('immobili'));" in scheda
    assert "deletion-check" not in scheda
    assert not re.search(r"\}/(trash|restore)`", scheda)


def test_s03_lessico():
    testi = [_codice(p) for p in (*TRASH_DIR.glob("*.js"), ASSETS / "views" / "cestino.js")]
    tutto = "\n".join(testi)
    for parola in ("Elimina…", "Sposta nel Cestino", "Ripristina", "Immobile spostato nel Cestino",
                   "Immobile ripristinato"):
        assert parola in tutto or parola in _codice(ASSETS / "views" / "immobile-dettaglio.js"), parola
    for vietata in ("Cancella", "cancella", "Annulla", "Elimina definitivamente", "purge", "Svuota"):
        assert vietata not in tutto, vietata
    # «Archivia» resta un'azione separata nella scheda; «Elimina…» vive nel foglio del Cestino
    scheda = _codice(ASSETS / "views" / "immobile-dettaglio.js")
    assert 'id="property-archive-btn"' in scheda and "Archivia" in scheda
    assert 'id="property-trash-btn" class="btn ghost trash-open">Elimina…</button>' in _codice(TRASH_DIR / "trash-dialog.js")


@node
def test_s04_node_check():
    for rel in ("trash/trash-model.js", "trash/trash-api.js", "trash/trash-dialog.js", "views/cestino.js",
                "views/immobile-dettaglio.js", "views/immobili.js", "main.js"):
        esito = subprocess.run([a30_5.NODE, "--check", str(ASSETS / rel)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, (rel, esito.stderr)


def test_s05_main_js_una_voce_discreta_e_una_rotta():
    main = (ASSETS / "main.js").read_text(encoding="utf-8")
    inizio = main.index("const SECTIONS = [")
    sezioni = main[inizio:main.index("];", inizio)]
    assert re.findall(r"name:\s*'([a-z]+)'", sezioni)[-1] == "cestino"       # in fondo
    assert main.count("registerRoute('cestino'") == 1
    assert "registerRoute('cestino', (container) => renderCestino(container));" in main
    css = (ASSETS / "app.css").read_text(encoding="utf-8")
    assert '.nav-item[data-route="cestino"] { margin-top: auto; font-size: 13px; color: var(--muted);' in css


# ---------------------------------------------------------------------------
# A-G - Shell eseguita (stub DOM)
# ---------------------------------------------------------------------------

rt = a30_5._rt()
IMMOBILE = {**base.IMMOBILE, "record_kind": "crm", "assigned_agent_id": 3}
PUO = {"can_trash": True, "blockers": []}
BLOCCATO = {"can_trash": False, "blockers": [
    {"code": "ACQUISITION_OPEN", "label": "Acquisizione aperta", "items": [{"id": 501}]},
    {"code": "MANDATE_PRESENT", "label": "Incarico presente", "items": [{"acquisition_id": 501}]},
    {"code": "SELLER_OPPORTUNITY_OPEN", "label": "Opportunità Venditore aperta: chiudila prima da Venditori («Smetti…»)",
     "items": [{"id": 77}, {"id": 78}]}]}
STORICO = {"can_trash": False, "blockers": [
    {"code": "HISTORY_REQUIRES_ADMIN", "label": "L'immobile ha uno storico operativo (backend)",
     "items": [{"code": "PROPERTY_ACTIVITY", "label": "Attività o interazioni registrate sull'immobile", "count": 4},
               {"code": "SALE_HISTORY", "label": "Vendite registrate", "count": 1}]}]}
SPOSTATO = {"status": 200, "body": {**IMMOBILE, "deleted_at": "2026-10-06T08:00:00+02:00", "deleted_reason": "duplicate"}}


def _voce(pid, code, **extra):
    v = {"id": pid, "code": code, "title": "Trilocale", "address": "Via Roma", "civic_number": str(pid), "city": "Giulianova",
         "province": "TE", "property_type": "apartment", "commercial_status": "draft", "archived_at": None,
         "deleted_at": "2026-10-05T18:30:00+02:00", "deleted_reason": "duplicate", "deleted_by_user_id": 3,
         "deleted_by_name": "Anna Agente", "deleted_note": None}
    v.update(extra)
    return v


CESTINO_AGENZIA = {"items": [_voce(31, "IMM-31", deleted_note="Doppione di IMM-12"),
                             _voce(32, "IMM-32", deleted_by_user_id=9, deleted_by_name="Ada Admin",
                                   deleted_reason="test_record", commercial_status="active",
                                   archived_at="2026-09-01T10:00:00+02:00")],
                   "has_more": False, "limit": 50, "offset": 0}
CESTINO_AGENTE = {"items": [_voce(31, "IMM-31")], "has_more": False, "limit": 50, "offset": 0}
CONFLITTO = {"status": 409, "body": {
    "detail": "Non si può ripristinare: un altro immobile attivo ha la stessa identità catastale o la stessa "
              "richiesta di creazione. Correggi prima l'altro immobile",
    "code": "RESTORE_CONFLICT", "conflicts": [{"id": 12, "code": "IMM-12", "index": "cadastral_identity"}]}}


def _rotte(sessione="agency_owner", *, immobile=IMMOBILE, check=(PUO,), trash=(SPOSTATO,), lista=(CESTINO_AGENZIA,),
           restore=({"status": 200, "body": _voce(31, "IMM-31", deleted_at=None)},)):
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
        ("GET", "/api/property/properties/30/deletion-check", [c if "status" in c else rt.ok(c) for c in check]),
        ("POST", "/api/property/properties/30/trash", list(trash)),
        ("GET", "/api/property/trash", [rt.ok(x) for x in lista]),
        ("POST", "/api/property/properties/31/restore", list(restore)),
        ("POST", "/api/property/properties/32/restore", [{"status": 200, "body": _voce(32, "IMM-32", deleted_at=None)}]),
        ("GET", "/api/property/properties/30", [rt.ok(immobile)]),
        ("GET", "/api/property/properties?", [rt.ok({"items": []})]),
        ("GET", "/api/property/form-options", [rt.ok({"territory": [], "energy_classes": [], "property_types": [],
                                                      "can_assign": True, "agents": []})]),
        ("GET", "/api/property/", [rt.ok({"items": []})]),
        ("GET", "/api/proposals?", [rt.ok({"items": []})]),
        ("GET", "/api/sales?", [rt.ok({"items": []})]),
        ("GET", "/api/match/", [rt.ok({"items": []})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


HELPERS = r"""
const TD = () => C().querySelector('#property-trash-dialog');
const tq = (s) => TD().querySelector(s);
const toast = () => { const t = __dom.main.querySelector('[data-census-toast]'); return t && !t.hidden ? t.visibleText() : null; };
const card = (id) => C().querySelector(`[data-trash-item="${id}"]`);
function scegliMotivo(valore) {
  for (const r of TD().querySelectorAll('input[name="trash-reason"]')) {
    r.checked = r.getAttribute('value') === valore;
    if (r.checked) r.dispatch('change');
  }
}
async function apriElimina() { C().querySelector('#property-trash-btn').dispatch('click'); await wait(); await wait(); }
function foglio() {
  if (!TD()) return null;
  return { open: !!TD()._open, testo: TD().visibleText(),
           blocchi: TD().querySelectorAll('[data-blocker]').map((li) => li.dataset.blocker),
           confermaDisabilitata: tq('[data-trash-confirm]').disabled,
           motivi: TD().querySelectorAll('input[name="trash-reason"]').map((r) => r.getAttribute('value')),
           errore: tq('[data-trash-error]').textContent };
}
"""


def _run(staged, scenario, rotte, hash):  # noqa: F811
    driver = staged.parent / "driver-delete-arch-2b3.mjs"
    driver.write_text(
        a30_5._dom() + rt.FETCH + a30_13b._extra_dom() + a30_5.IS_CONNECTED + a30_5.ROUTED_FETCH
        + f"\n{rotte}\n" + f"window.location.hash = '{hash}';\n"
        + f"await import('{(staged / 'main.js').as_posix()}');\n" + "await __settle(40);\n"
        + a30_5.HELPERS + base.D_HELPERS + HELPERS + scenario + "\n", encoding="utf-8")
    esito = subprocess.run([a30_5.NODE, str(driver)], capture_output=True, text=True, timeout=60,
                           cwd=staged.parent, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    if esito.returncode != 0:
        raise AssertionError(f"driver node fallito:\n{esito.stderr[-3000:]}\n{esito.stdout[-1500:]}")
    return _testi(json.loads(esito.stdout.strip().splitlines()[-1]))


def _testi(valore):
    """Lo stub DOM non decodifica le entita' HTML: qui si legge come un utente."""
    if isinstance(valore, str):
        return html.unescape(valore)
    if isinstance(valore, list):
        return [_testi(v) for v in valore]
    if isinstance(valore, dict):
        return {k: _testi(v) for k, v in valore.items()}
    return valore


def _scritture(out):
    return [(c["m"], c["url"]) for c in out["calls"] if c["m"] in ("POST", "PATCH", "PUT", "DELETE")]


@node
def test_a_elimina_presente_quando_autorizzato(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      report({ bottone: C().querySelector('#property-trash-btn') ? C().querySelector('#property-trash-btn').textContent : null,
               archivia: !!C().querySelector('#property-archive-btn') });
    """
    titolare = _run(staged, scenario, _rotte("agency_owner"), "#/immobili/30")
    agente = _run(staged, scenario, _rotte("agent"), "#/immobili/30")                        # assegnato a lui (3)
    altro = _run(staged, scenario, _rotte("agent", immobile={**IMMOBILE, "assigned_agent_id": 4}), "#/immobili/30")
    assert titolare["bottone"] == "Elimina…" and agente["bottone"] == "Elimina…"
    assert altro["bottone"] is None                       # stessa regola di «Archivia»: niente 403 offerto
    assert titolare["archivia"] is True                   # «Archivia» resta separata
    for out in (titolare, agente, altro):
        assert _scritture(out) == [] and not any("deletion-check" in c["url"] for c in out["calls"])


@node
def test_b_blocchi_del_backend_conferma_disabilitata(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      await apriElimina();
      report({ f: foglio() });
    """
    out = _run(staged, scenario, _rotte(check=(BLOCCATO,)), "#/immobili/30")
    f = out["f"]
    assert f["open"] is True
    assert f["blocchi"] == ["ACQUISITION_OPEN", "MANDATE_PRESENT", "SELLER_OPPORTUNITY_OPEN"]
    for b in BLOCCATO["blockers"]:
        assert b["label"] in f["testo"]                   # ESATTAMENTE il testo del backend
    assert "(2)" in f["testo"]                            # quante voci, dal backend
    assert f["confermaDisabilitata"] is True and f["motivi"] == []
    assert [c["url"] for c in out["calls"] if "deletion-check" in c["url"]] == ["/api/property/properties/30/deletion-check"]
    assert _scritture(out) == []


@node
def test_b2_storico_protetto_in_deletion_check(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      await apriElimina();
      tq('[data-trash-form]').dispatch('submit'); await wait();
      report({ f: foglio() });
    """
    out = _run(staged, scenario, _rotte("agent", check=(STORICO,)), "#/immobili/30")
    f = out["f"]
    assert HISTORY_TEXT in f["testo"]
    assert "Attività o interazioni registrate sull'immobile" in f["testo"] and "(4)" in f["testo"]
    assert f["confermaDisabilitata"] is True
    assert _scritture(out) == []                          # nessun bypass, nemmeno forzando il submit


@node
def test_c_trash_riuscito_toast_e_lista(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      await apriElimina();
      const prima = foglio();
      scegliMotivo('duplicate');
      const dopoMotivo = tq('[data-trash-confirm]').disabled;
      tq('[data-trash-note]').value = '  Doppione di IMM-12  ';
      tq('[data-trash-form]').dispatch('submit'); await wait(); await wait();
      const hashDopo = window.location.hash;
      const aperto = TD() ? !!TD()._open : false;
      const t = toast();
      await window._fire('hashchange'); await wait(); await wait();
      report({ prima, dopoMotivo, hashDopo, aperto, t, t2: toast() });
    """
    out = _run(staged, scenario, _rotte(), "#/immobili/30")
    assert out["prima"]["confermaDisabilitata"] is True            # nessun motivo ancora
    assert out["prima"]["motivi"] == ["created_by_mistake", "duplicate", "invalid_data", "test_record", "other"]
    for etichetta in ("Creato per errore", "Duplicato", "Dati non validi", "Record di prova", "Altro"):
        assert etichetta in out["prima"]["testo"]
    assert out["dopoMotivo"] is False
    trash = [c for c in out["calls"] if c["m"] == "POST" and c["url"] == "/api/property/properties/30/trash"]
    assert len(trash) == 1 and trash[0]["body"] == {"reason_code": "duplicate", "note": "Doppione di IMM-12"}
    assert trash[0]["cred"] == "include"
    assert out["aperto"] is False and out["hashDopo"] == "#/immobili"
    assert out["t"] == "Immobile spostato nel Cestino" and out["t2"] == "Immobile spostato nel Cestino"
    # la lista Immobili fa la sua GET normale; la scheda non viene riletta, nessuna DELETE
    assert any(c["url"].startswith("/api/property/properties?") for c in out["calls"])
    assert [c["url"] for c in out["calls"] if c["url"] == "/api/property/properties/30"] == ["/api/property/properties/30"]
    assert not any(c["m"] == "DELETE" for c in out["calls"])
    assert _scritture(out) == [("POST", "/api/property/properties/30/trash")]


@node
def test_c2_senza_nota_nessun_campo_note(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      await apriElimina();
      scegliMotivo('created_by_mistake');
      tq('[data-trash-form]').dispatch('submit'); await wait(); await wait();
      report({});
    """
    out = _run(staged, scenario, _rotte(), "#/immobili/30")
    trash = [c for c in out["calls"] if c["m"] == "POST" and c["url"].endswith("/trash")]
    assert trash[0]["body"] == {"reason_code": "created_by_mistake"}


@node
def test_d_403_history_requires_admin_alla_conferma(staged):  # noqa: F811
    rifiuto = {"status": 403, "body": {"detail": "L'immobile ha uno storico operativo (backend)",
                                       "code": "HISTORY_REQUIRES_ADMIN",
                                       "history": [{"code": "APPOINTMENT_HISTORY", "label": "Appuntamenti avvenuti", "count": 2}]}}
    scenario = r"""
      await wait(); await wait();
      await apriElimina();
      scegliMotivo('other');
      tq('[data-trash-form]').dispatch('submit'); await wait(); await wait();
      const esito = foglio();
      tq('[data-trash-form]').dispatch('submit'); await wait();
      report({ f: esito, hash: window.location.hash, t: toast() });
    """
    out = _run(staged, scenario, _rotte("agent", trash=(rifiuto,)), "#/immobili/30")
    f = out["f"]
    assert f["open"] is True and HISTORY_TEXT in f["testo"]
    assert "Appuntamenti avvenuti" in f["testo"] and "(2)" in f["testo"]
    assert f["confermaDisabilitata"] is True
    assert out["hash"] == "#/immobili/30" and out["t"] is None
    assert _scritture(out) == [("POST", "/api/property/properties/30/trash")]     # un tentativo, nessun bypass


@node
def test_d2_409_trash_blocked_alla_conferma_mostra_i_blocchi(staged):  # noqa: F811
    rifiuto = {"status": 409, "body": {"detail": "L'immobile ha processi aperti: chiudili prima di spostarlo nel Cestino",
                                       "code": "TRASH_BLOCKED",
                                       "blockers": [{"code": "FUTURE_APPOINTMENT", "label": "Appuntamento futuro", "items": [{"id": 9}]}]}}
    scenario = r"""
      await wait(); await wait();
      await apriElimina();
      scegliMotivo('other');
      tq('[data-trash-form]').dispatch('submit'); await wait(); await wait();
      report({ f: foglio() });
    """
    out = _run(staged, scenario, _rotte(trash=(rifiuto,)), "#/immobili/30")
    f = out["f"]
    assert f["blocchi"] == ["FUTURE_APPOINTMENT"] and "Appuntamento futuro" in f["testo"]
    assert "L'immobile ha processi aperti" in f["errore"]
    assert f["confermaDisabilitata"] is True


@node
def test_e_pagina_cestino_admin_agenzia_agent_propri(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      report({ cards: C().querySelectorAll('[data-trash-item]').map((c) => c.dataset.trashItem),
               testi: C().querySelectorAll('[data-trash-item]').map((c) => c.visibleText()),
               bottoni: C().querySelectorAll('[data-trash-restore]').map((b) => b.textContent),
               nav: __dom.byId['nav'].children.map((b) => b.dataset.route || ''),
               titolo: __dom.byId['page-title'].textContent });
    """
    admin = _run(staged, scenario, _rotte("agency_admin", lista=(CESTINO_AGENZIA,)), "#/cestino")
    agente = _run(staged, scenario, _rotte("agent", lista=(CESTINO_AGENTE,)), "#/cestino")
    assert admin["cards"] == ["31", "32"] and agente["cards"] == ["31"]
    assert admin["bottoni"] == ["Ripristina", "Ripristina"]
    uno, due = admin["testi"]
    assert "IMM-31" in uno and "Via Roma 31, Giulianova" in uno and "Duplicato — Doppione di IMM-12" in uno
    assert "Anna Agente" in uno and "Bozza" in uno
    assert "IMM-32" in due and "Record di prova" in due and "Ada Admin" in due and "Attivo · archiviato" in due
    assert admin["nav"][-1] == "cestino" and admin["titolo"] == "Cestino"
    # la pagina non filtra e non aggiunge parametri: lo scope lo decide il backend
    for out in (admin, agente):
        letture = [c["url"] for c in out["calls"] if c["url"].startswith("/api/property/trash")]
        assert letture == ["/api/property/trash?limit=50&offset=0"]
        assert _scritture(out) == []
    assert "Eliminato" in uno and "Motivo" in uno and "Stato" in uno


@node
def test_e2_cestino_vuoto(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      report({});
    """
    out = _run(staged, scenario, _rotte(lista=({"items": [], "has_more": False, "limit": 50, "offset": 0},)), "#/cestino")
    assert "Il Cestino è vuoto." in out["content"]
    assert "Elimina definitivamente" not in out["content"] and "Svuota" not in out["content"]


@node
def test_f_ripristina_card_sparisce_e_toast(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      card(31).querySelector('[data-trash-restore]').dispatch('click'); await wait(); await wait();
      report({ cards: C().querySelectorAll('[data-trash-item]').map((c) => c.dataset.trashItem), t: toast() });
    """
    out = _run(staged, scenario, _rotte(), "#/cestino")
    assert out["cards"] == ["32"]
    assert out["t"] == "Immobile ripristinato"
    assert _scritture(out) == [("POST", "/api/property/properties/31/restore")]
    restore = [c for c in out["calls"] if c["url"].endswith("/restore")][0]
    assert restore["body"] is None and restore["cred"] == "include"
    # nessuna rilettura forzata dell'elenco
    assert len([c for c in out["calls"] if c["url"].startswith("/api/property/trash")]) == 1


@node
def test_g_restore_conflict_leggibile_nessuna_modifica_locale(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const prima = card(31).querySelector('.trash-facts').visibleText();
      card(31).querySelector('[data-trash-restore]').dispatch('click'); await wait(); await wait();
      const c = card(31);
      report({ cards: C().querySelectorAll('[data-trash-item]').map((x) => x.dataset.trashItem),
               errore: c.querySelector('[data-trash-card-error]').visibleText(),
               link: c.querySelector('[data-trash-conflicts]').querySelectorAll('a').map((a) => [a.getAttribute('href'), a.textContent]),
               bottone: [c.querySelector('[data-trash-restore]').textContent, c.querySelector('[data-trash-restore]').disabled],
               prima, dopo: c.querySelector('.trash-facts').visibleText(), t: toast() });
    """
    out = _run(staged, scenario, _rotte(restore=(CONFLITTO,)), "#/cestino")
    assert out["cards"] == ["31", "32"]                                   # la card resta
    assert "Non si può ripristinare" in out["errore"] and "IMM-12" in out["errore"]
    assert "stessa identità catastale" in out["errore"]
    assert out["link"] == [["#/immobili/12", "IMM-12"]]
    assert out["bottone"] == ["Ripristina", False]
    assert out["prima"] == out["dopo"]                                    # nessuna modifica locale finta
    assert out["t"] is None
    assert _scritture(out) == [("POST", "/api/property/properties/31/restore")]   # nessuna correzione automatica


@node
def test_g2_ripristino_di_un_altro_agente_rifiutato_dal_backend(staged):  # noqa: F811
    rifiuto = {"status": 403, "body": {"detail": "Puoi ripristinare solo gli immobili che hai spostato tu nel Cestino: "
                                                 "chiedi a un amministratore", "code": "NOT_DELETED_BY_YOU"}}
    scenario = r"""
      await wait(); await wait();
      card(31).querySelector('[data-trash-restore]').dispatch('click'); await wait(); await wait();
      report({ cards: C().querySelectorAll('[data-trash-item]').map((x) => x.dataset.trashItem),
               errore: card(31).querySelector('[data-trash-card-error]').visibleText() });
    """
    out = _run(staged, scenario, _rotte("agent", restore=(rifiuto,)), "#/cestino")
    assert out["cards"] == ["31", "32"]
    assert out["errore"].startswith("Puoi ripristinare solo gli immobili che hai spostato tu")


# ---------------------------------------------------------------------------
# H - mobile
# ---------------------------------------------------------------------------

def test_h01_css_mobile_bottom_sheet_e_azioni_toccabili():
    css = (ASSETS / "app.css").read_text(encoding="utf-8")
    blocco = css[css.index("/* DELETE-ARCH Fase 2B3"):]
    assert "@media (max-width: 767px)" in blocco
    assert "dialog.modal.trash-sheet { margin: auto 0 0 0; width: 100vw; max-width: 100vw;" in blocco
    assert ".trash-card-actions .btn, .trash-actions .btn { min-height: 44px; }" in blocco
    assert "overflow-wrap: anywhere" in blocco
    # nessuna larghezza fissa in px oltre il max-width del foglio su desktop, nessun overflow nascosto
    larghezze = re.findall(r"(?<![-\w])width:\s*(\d+)px", blocco)
    assert larghezze == [], larghezze
    assert "overflow-x: hidden" not in blocco and "zoom" not in blocco
    # nessuna regola esistente ridefinita: solo selettori trash-/cestino
    for selettore in re.findall(r"^([^@/{}\n][^{]*)\{", blocco, flags=re.M):
        assert "trash" in selettore or "cestino" in selettore, selettore


_ME = {"user_id": 3, "agency_id": 7, "agency_name": "Agenzia A", "role": "agency_owner",
       "is_platform_admin": False, "expires_at": "2030-01-01T00:00:00Z"}


def _chromium():
    pytest.importorskip("playwright.sync_api", reason="Playwright non disponibile: misura mobile NON eseguita (BLOCKED)")
    from playwright.sync_api import sync_playwright
    return sync_playwright


def _servi(page, api):
    """La Shell VERA (index.html, app.css, moduli) servita da file, le API da `api`."""
    def gestore(route):
        url = route.request.url
        percorso = url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        if percorso.startswith("os/"):
            file = SHELL / (percorso[3:] or "index.html")
            if percorso == "os/":
                file = SHELL / "index.html"
            tipo = ("text/css" if file.suffix == ".css" else "text/javascript" if file.suffix == ".js"
                    else "text/html")
            return route.fulfill(status=200, body=file.read_bytes(), content_type=tipo)
        for prefisso, corpo in api:
            if ("/" + percorso + ("?" + url.split("?", 1)[1] if "?" in url else "")).startswith(prefisso):
                return route.fulfill(status=200, body=json.dumps(corpo), content_type="application/json")
        return route.fulfill(status=200, body=json.dumps({"items": []}), content_type="application/json")
    page.route("**/*", gestore)


def _sfora(page):
    return page.evaluate("""() => {
      const doc = document.documentElement;
      const fuori = [...document.querySelectorAll('.trash-card, .trash-sheet, .trash-form, .trash-blockers li, .trash-page-head')]
        .filter((e) => e.getClientRects().length && e.getBoundingClientRect().right > window.innerWidth + 0.5)
        .map((e) => e.className);
      return { scroll: doc.scrollWidth, inner: window.innerWidth, fuori };
    }""")


@pytest.mark.parametrize("larghezza", [320, 360, 390])
def test_h02_chromium_cestino_e_foglio_senza_sforamento(larghezza):
    sync_playwright = _chromium()
    lungo = "Contrada San Giovanni Battista Vecchia Strada Provinciale"
    cestino = {"items": [_voce(31, "IMM-31", address=lungo, deleted_note="Nota molto lunga " * 12),
                         _voce(32, "IMM-32")], "has_more": True, "limit": 50, "offset": 0}
    api = [("/api/operator-auth/me", _ME), ("/api/property/trash", cestino),
           ("/api/property/properties/30/deletion-check", BLOCCATO),
           ("/api/property/properties/30", {**IMMOBILE, "address": lungo}),
           ("/api/property/form-options", {"territory": [], "energy_classes": [], "property_types": [],
                                           "can_assign": True, "agents": []})]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": larghezza, "height": 780})
            _servi(page, api)
            page.goto("http://stima360.test/os/#/cestino")
            page.wait_for_selector("[data-trash-item='31']")
            pagina = _sfora(page)
            page.goto("http://stima360.test/os/#/immobili/30")
            page.wait_for_selector("#property-trash-btn")
            page.click("#property-trash-btn")
            page.wait_for_selector("[data-blocker='SELLER_OPPORTUNITY_OPEN']")
            foglio = _sfora(page)
            sheet = page.evaluate("""() => { const r = document.querySelector('#property-trash-dialog').getBoundingClientRect();
                                             return { w: r.width, bottom: r.bottom, h: innerHeight }; }""")
            bottoni = page.evaluate("""() => [...document.querySelectorAll('.trash-actions .btn')]
                                         .map((b) => b.getBoundingClientRect().height)""")
            browser.close()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium non disponibile: misura mobile NON eseguita (BLOCKED)")
        raise
    for misura in (pagina, foglio):
        assert misura["scroll"] <= misura["inner"], misura
        assert misura["fuori"] == [], misura
    assert abs(sheet["w"] - larghezza) <= 1 and abs(sheet["bottom"] - sheet["h"]) <= 1   # bottom-sheet a tutta larghezza
    assert all(h >= 44 for h in bottoni), bottoni
