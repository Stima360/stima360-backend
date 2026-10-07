"""CESTINO-EDIFICI-1 (FASE G) - UI del Cestino Edifici (Shell OS), senza database.

  M  funzioni pure di trash/trash-model.js (node): blocchi con le unita' e i
     loro collegamenti (solo interni), nome e riga dell'edificio, percorso
     dell'elenco, possibili doppioni (stessa chiave catastale / indirizzo);
  S  statici: rotte nominate solo da trash/trash-api.js (e il percorso
     dell'elenco solo da trash-model.js); la scheda edificio usa il foglio
     condiviso; nessuna regola di permesso nel client; lessico;
  D  Shell VERA (main.js, router, sessione, scheda edificio, pagina Cestino)
     nello stub DOM di P26-4/A30-5 con fetch instradato:
       d01  «Elimina…» di un edificio con unita': blocco con le unita' e i link,
            conferma disabilitata, nessuna scrittura;
       d02  edificio vuoto: motivo -> POST .../trash, toast, lista Edifici;
       d03  edificio nel Cestino: scheda in sola lettura (niente «Modifica»,
            «Elimina…», «+ Aggiungi unita'»), «Ripristina» -> stessa scheda
            ridisegnata, avviso dei possibili doppioni con link, nulla unito;
            senza permesso, nessun bottone;
       d04  pagina Cestino: Immobili resta la scheda predefinita; Edifici al
            tocco o con #/cestino/edifici; card e «Ripristina».

Senza node le prove M/D sono SKIPPED (BLOCKED), mai passate. La prova in
Chromium e' tests/test_cestino_edifici_1_browser_postgres.py.
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
from tests.test_edifici_1_ui import OPZIONI_E1, SCHEDA

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
TRASH_DIR = ASSETS / "trash"
SCHEDA_JS = ASSETS / "views" / "edificio-dettaglio.js"

node = pytest.mark.skipif(a30_5.NODE is None, reason="node non disponibile: livello NON eseguito (BLOCKED)")
_codice = t2b3._codice


def _modello(expr):
    return t2b3._modello(expr)


# ---------------------------------------------------------------------------
# M - funzioni pure
# ---------------------------------------------------------------------------

@node
def test_m01_blocchi_nomi_percorso_errori():
    out = _modello("""({
      b: m.buildingBlockerView([{ code: 'BUILDING_HAS_UNITS', label: "2 unità collegate (1 attiva, 1 nel Cestino)",
           link: { href: '#/edifici/7', label: "Apri l'edificio" },
           items: [{ id: 410, label: 'IMM-410 · Appartamento · attiva', href: '#/immobili/410' },
                   { id: 411, label: 'IMM-411 · nel Cestino', href: '#/cestino' },
                   { id: 412, label: 'IMM-412', href: 'https://evil.test/' }] }]),
      nome: [m.buildingName({ id: 7, name: 'Residenza', address: 'Via Roma', civic_number: '10' }),
             m.buildingName({ id: 7, address: 'Via Roma', civic_number: '10' }), m.buildingName({ id: 7 })],
      riga: m.buildingLine({ address: 'Via Roma', civic_number: '10', city: 'Tortoreto' }),
      percorso: m.buildingTrashListPath(50, 50),
      e404: m.buildingTrashErrorText({ status: 404 }),
      altro: m.buildingTrashErrorText({ status: 409, message: 'dal backend' }),
      toast: [m.BUILDING_TRASHED_TOAST, m.BUILDING_RESTORED_TOAST],
    })""")
    b = out["b"][0]
    assert b["code"] == "BUILDING_HAS_UNITS" and b["count"] == 3
    assert b["link"] == {"href": "#/edifici/7", "label": "Apri l'edificio"}
    assert b["items"] == [{"label": "IMM-410 · Appartamento · attiva", "link": {"href": "#/immobili/410", "label": "Apri"}},
                          {"label": "IMM-411 · nel Cestino", "link": {"href": "#/cestino", "label": "Apri"}},
                          {"label": "IMM-412", "link": None}]               # mai un indirizzo esterno
    assert out["nome"] == ["Residenza", "Via Roma 10", "Edificio #7"]
    assert out["riga"] == "Via Roma 10, Tortoreto"
    assert out["percorso"] == "/api/property/trash/buildings?limit=50&offset=50"
    assert out["e404"] == "Edificio non trovato." and out["altro"] == "dal backend"
    assert out["toast"] == ["Edificio spostato nel Cestino", "Edificio ripristinato"]


@node
def test_m02_possibili_doppioni():
    out = _modello("""({
      d: m.buildingDuplicatesView({ id: 7, city: 'Tortoreto', address: 'Via Roma', cadastral_municipality_code: 'L307',
           cadastral_sheet: '12', cadastral_parcel: '345', possible_duplicates: [
        { id: 8, name: 'Gemella', city: 'tortoreto', address: 'VIA ROMA', civic_number: '10',
          cadastral_municipality_code: 'L307', cadastral_sheet: '12', cadastral_parcel: '345' },
        { id: 9, city: 'Tortoreto', address: 'Via Roma', civic_number: '12' },
        { id: 10, name: 'Solo catasto', city: 'Alba', address: 'Via X', cadastral_municipality_code: 'L307',
          cadastral_sheet: '12', cadastral_parcel: '345' }] }),
      vuoto: m.buildingDuplicatesView({ id: 7 }),
    })""")
    assert out["d"] == [
        {"id": 8, "name": "Gemella · VIA ROMA 10, tortoreto", "reason": "stessa chiave catastale e stesso indirizzo"},
        {"id": 9, "name": "Via Roma 12 · Via Roma 12, Tortoreto", "reason": "stesso indirizzo"},
        {"id": 10, "name": "Solo catasto · Via X, Alba", "reason": "stessa chiave catastale"}]
    assert out["vuoto"] == []


# ---------------------------------------------------------------------------
# S - statici
# ---------------------------------------------------------------------------

ROTTA_EDIFICI = re.compile(r"/api/property/buildings/\$\{[^}]+\}/(deletion-check|trash|restore)")


def test_s01_rotte_solo_nel_client_del_cestino():
    fuori = sorted(p.relative_to(ASSETS).as_posix() for p in ASSETS.rglob("*.js") if ROTTA_EDIFICI.search(_codice(p)))
    assert fuori == ["trash/trash-api.js"], fuori
    elenco = sorted(p.relative_to(ASSETS).as_posix() for p in ASSETS.rglob("*.js")
                    if "/api/property/trash/buildings" in _codice(p))
    assert elenco == ["trash/trash-model.js"]


def test_s02_scheda_edificio_usa_il_foglio_condiviso_e_nessuna_regola():
    scheda = _codice(SCHEDA_JS)
    assert "buildingTrashButtonHtml()" in scheda
    assert "bindBuildingTrashButton(container, edificio, () => navigate('edifici'));" in scheda
    assert "const inTrash = Boolean(edificio.deleted_at);" in scheda
    assert "const dallaProcedura = params[1] === 'aggiungi' && !inTrash;" in scheda   # mai unita' nuove dal Cestino
    assert "info.can_restore" in scheda and "deletion-check" not in scheda
    assert "{ kind: 'building' }" in scheda
    for p in [*TRASH_DIR.glob("*.js"), ASSETS / "views" / "cestino.js"]:
        testo = _codice(p)
        for regola in ("deleted_by_user_id", ".role", "getSession", "agency_owner", "is_platform_admin"):
            assert regola not in testo, (p.name, regola)


def test_s03_lessico_e_distinzione_intero_stabile():
    tutto = "\n".join(_codice(p) for p in (*TRASH_DIR.glob("*.js"), ASSETS / "views" / "cestino.js", SCHEDA_JS))
    for parola in ("Elimina…", "Elimina edificio", "Sposta nel Cestino", "Ripristina", "Edificio spostato nel Cestino",
                   "Edificio ripristinato", "Nel Cestino", "Nessun edificio è stato unito o modificato."):
        assert parola in tutto, parola
    cestino = "\n".join(_codice(p) for p in (*TRASH_DIR.glob("*.js"), ASSETS / "views" / "cestino.js"))
    for vietata in ("Cancella", "cancella", "Elimina definitivamente", "purge", "Svuota", "Scollega"):
        assert vietata not in cestino, vietata
    # la palazzina contenitore non e' l'immobile «intero stabile»: le rotte restano separate
    assert "/api/property/properties/${propertyId}/trash" in _codice(TRASH_DIR / "trash-api.js")


@node
def test_s04_node_check():
    for rel in ("trash/trash-model.js", "trash/trash-api.js", "trash/trash-dialog.js", "views/cestino.js",
                "views/edificio-dettaglio.js", "main.js"):
        esito = subprocess.run([a30_5.NODE, "--check", str(ASSETS / rel)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, (rel, esito.stderr)


# ---------------------------------------------------------------------------
# D - Shell eseguita (stub DOM)
# ---------------------------------------------------------------------------

rt = a30_5._rt()

BLOCCATO = {"can_trash": False, "history": [], "blockers": [
    {"code": "BUILDING_HAS_UNITS", "label": "3 unità collegate (2 attive, 1 nel Cestino): l'edificio deve essere vuoto",
     "link": {"href": "#/edifici/7", "label": "Apri l'edificio"},
     "items": [{"id": 410, "label": "IMM-410 · Appartamento · attiva", "href": "#/immobili/410"},
               {"id": 415, "label": "IMM-415 · Garage · pertinenza · attiva", "href": "#/immobili/415"},
               {"id": 420, "label": "IMM-420 · nel Cestino", "href": "#/cestino"}]}]}
PUO = {"can_trash": True, "blockers": [], "history": []}
VUOTO = {**SCHEDA, "units": [], "archived_units": []}
NEL_CESTINO = {**VUOTO, "deleted_at": "2026-10-06T08:00:00+02:00", "deleted_reason": "duplicate", "deleted_by_user_id": 3,
               "trash": {"deleted_at": "2026-10-06T08:00:00+02:00", "deleted_reason": "duplicate", "deleted_by_user_id": 3,
                         "deleted_by_name": "Anna Agente", "deleted_note": "Doppione di via Roma", "can_restore": True}}
RIPRISTINATO = {"status": 200, "body": {**{k: v for k, v in VUOTO.items() if k not in ("units", "archived_units")},
                                        "deleted_at": None, "possible_duplicates": [
                                            {"id": 8, "name": "Gemella", "city": "Tortoreto", "address": "Via Roma",
                                             "civic_number": "10"}]}}


def _voce(bid, **extra):
    v = {"id": bid, "name": f"Palazzina {bid}", "address": "Via Roma", "civic_number": str(bid), "city": "Tortoreto",
         "building_type": "condominio", "units_declared": None, "deleted_at": "2026-10-05T18:30:00+02:00",
         "deleted_reason": "created_by_mistake", "deleted_by_user_id": 3, "deleted_by_name": "Anna Agente",
         "deleted_note": None}
    v.update(extra)
    return v


ELENCO_EDIFICI = {"items": [_voce(7, deleted_note="Creata due volte", units_declared=6), _voce(9)],
                  "has_more": False, "limit": 50, "offset": 0}


def _rotte(sessione="agency_owner", *, scheda=(VUOTO,), check=(PUO,), trash=None, restore=(RIPRISTINATO,),
           lista=(ELENCO_EDIFICI,)):
    trash = trash or ({"status": 200, "body": {**NEL_CESTINO}},)
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
        ("GET", "/api/property/form-options", [rt.ok(OPZIONI_E1)]),
        ("GET", "/api/property/buildings/7/deletion-check", [c if "status" in c else rt.ok(c) for c in check]),
        ("POST", "/api/property/buildings/7/trash", list(trash)),
        ("POST", "/api/property/buildings/7/restore", list(restore)),
        ("POST", "/api/property/buildings/9/restore", [{"status": 200, "body": {"id": 9, "possible_duplicates": []}}]),
        ("GET", "/api/property/buildings/7", [rt.ok(s) for s in scheda]),
        ("GET", "/api/property/buildings", [rt.ok({"items": [], "total": 0, "limit": 25, "offset": 0})]),
        ("GET", "/api/property/trash/buildings", [rt.ok(x) for x in lista]),
        ("GET", "/api/property/trash", [rt.ok({"items": [], "has_more": False, "limit": 50, "offset": 0})]),
        ("GET", "/api/core/trash/contacts", [rt.ok({"items": [], "has_more": False, "limit": 50, "offset": 0})]),
        ("GET", "/api/", [rt.ok({"items": []})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


HELPERS = r"""
const BD = () => C().querySelector('#building-trash-dialog');
const bq = (s) => BD().querySelector(s);
const btoast = () => { const t = __dom.main.querySelector('[data-census-toast]'); return t && !t.hidden ? t.visibleText() : null; };
function bScegli(valore) {
  for (const r of BD().querySelectorAll('input[name="trash-reason"]')) {
    r.checked = r.getAttribute('value') === valore;
    if (r.checked) r.dispatch('change');
  }
}
function bFoglio() {
  if (!BD()) return null;
  return { open: !!BD()._open, testo: BD().visibleText(),
           blocchi: BD().querySelectorAll('[data-blocker]').map((li) => li.dataset.blocker),
           voci: BD().querySelectorAll('[data-blocker-item-link]').map((a) => [a.getAttribute('href'), a.textContent]),
           confermaDisabilitata: bq('[data-trash-confirm]').disabled,
           motivi: BD().querySelectorAll('input[name="trash-reason"]').map((r) => r.getAttribute('value')) };
}
"""


def _run(staged, scenario, rotte, hash):  # noqa: F811
    return t2b3._run(staged, HELPERS + scenario, rotte, hash)


def _scritture(out):
    return [(c["m"], c["url"]) for c in out["calls"] if c["m"] in ("POST", "PATCH", "PUT", "DELETE")]


@node
def test_d01_edificio_con_unita_bloccato_con_collegamenti(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const elimina = C().querySelector('#building-trash-btn');
      const etichetta = elimina ? elimina.textContent : null;
      elimina.dispatch('click'); await wait(); await wait();
      const statoFoglio = bFoglio();
      bq('[data-trash-form]').dispatch('submit'); await wait();
      report({ etichetta, f: statoFoglio });
    """
    out = _run(staged, scenario, _rotte(scheda=(SCHEDA,), check=(BLOCCATO,)), "#/edifici/7")
    assert out["etichetta"] == "Elimina…"
    f = out["f"]
    assert f["open"] is True and f["blocchi"] == ["BUILDING_HAS_UNITS"]
    assert "Elimina edificio" in f["testo"] and "Non si può spostare nel Cestino" in f["testo"]
    assert "3 unità collegate (2 attive, 1 nel Cestino)" in f["testo"]
    assert f["voci"] == [["#/immobili/410", "IMM-410 · Appartamento · attiva"],
                         ["#/immobili/415", "IMM-415 · Garage · pertinenza · attiva"],
                         ["#/cestino", "IMM-420 · nel Cestino"]]
    assert f["confermaDisabilitata"] is True and f["motivi"] == []
    assert _scritture(out) == []                          # nulla si scollega, sposta o cestina


@node
def test_d02_edificio_vuoto_motivo_cestino_lista_edifici(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#building-trash-btn').dispatch('click'); await wait(); await wait();
      const statoFoglio = bFoglio();
      const primaDelMotivo = bq('[data-trash-confirm]').disabled;
      bScegli('duplicate');
      const dopoMotivo = bq('[data-trash-confirm]').disabled;
      bq('[data-trash-note]').value = '  Doppione di via Roma  ';
      bq('[data-trash-form]').dispatch('submit'); await wait(); await wait();
      report({ f: statoFoglio, primaDelMotivo, dopoMotivo, hashDopo: window.location.hash, t: btoast() });
    """
    out = _run(staged, scenario, _rotte(), "#/edifici/7")
    f = out["f"]
    assert f["blocchi"] == [] and f["motivi"] == ["created_by_mistake", "duplicate", "invalid_data", "test_record", "other"]
    assert "L'edificio è vuoto: esce dalla lista Edifici e dalla creazione guidata." in f["testo"]
    assert out["primaDelMotivo"] is True and out["dopoMotivo"] is False
    assert _scritture(out) == [("POST", "/api/property/buildings/7/trash")]
    corpo = next(c for c in out["calls"] if c["m"] == "POST")["body"]
    assert corpo == {"reason_code": "duplicate", "note": "Doppione di via Roma"}
    assert out["hashDopo"] == "#/edifici" and out["t"] == "Edificio spostato nel Cestino"


@node
def test_d03_scheda_nel_cestino_sola_lettura_e_ripristino(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const banner = C().querySelector('[data-building-in-trash]');
      const prima = { banner: banner ? banner.visibleText() : null,
                      modifica: !!C().querySelector('#building-edit'), elimina: !!C().querySelector('#building-trash-btn'),
                      aggiungi: !!C().querySelector('#unit-add-apartment'), pertinenza: !!C().querySelector('#unit-add-pertinenza'),
                      unita: C().querySelector('#building-units').visibleText(),
                      link: banner ? banner.querySelectorAll('a').map((a) => a.getAttribute('href')) : [] };
      C().querySelector('[data-building-restore-btn]').dispatch('click'); await wait(); await wait(); await wait();
      const avviso = C().querySelector('[data-building-duplicates]');
      report({ prima, avviso: avviso ? avviso.visibleText() : null,
               linkDoppioni: avviso ? avviso.querySelectorAll('a').map((a) => a.getAttribute('href')) : [],
               t: btoast(), dopo: { banner: !!C().querySelector('[data-building-in-trash]'),
                                    elimina: !!C().querySelector('#building-trash-btn'),
                                    modifica: !!C().querySelector('#building-edit') } });
    """
    out = _run(staged, scenario, _rotte(scheda=(NEL_CESTINO, VUOTO)), "#/edifici/7")
    p = out["prima"]
    assert "Nel Cestino" in p["banner"] and "Anna Agente" in p["banner"] and "Duplicato — Doppione di via Roma" in p["banner"]
    assert "non si modifica e non riceve unità" in p["banner"]
    assert (p["modifica"], p["elimina"], p["aggiungi"], p["pertinenza"]) == (False, False, False, False)
    assert "Nessuna unità: l’edificio è nel Cestino." in p["unita"] and p["link"] == ["#/cestino/edifici"]
    assert _scritture(out) == [("POST", "/api/property/buildings/7/restore")]
    assert out["t"] == "Edificio ripristinato"
    assert "Possibili doppioni attivi" in out["avviso"] and "Gemella" in out["avviso"]
    assert "stesso indirizzo" in out["avviso"] and "Nessun edificio è stato unito o modificato." in out["avviso"]
    assert out["linkDoppioni"] == ["#/edifici/8"]
    assert out["dopo"] == {"banner": False, "elimina": True, "modifica": True}   # stesso id, di nuovo operativo


@node
def test_d03b_scheda_nel_cestino_dalla_procedura_non_apre_il_foglio_unita(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      report({ banner: !!C().querySelector('[data-building-in-trash]'), aggiungi: !!C().querySelector('#unit-add-apartment'),
               foglioUnita: !!C().querySelector('dialog[open]') });
    """
    out = _run(staged, scenario, _rotte(scheda=(NEL_CESTINO,)), "#/edifici/7/aggiungi/crm")
    assert out["banner"] is True and out["aggiungi"] is False and out["foglioUnita"] is False
    assert _scritture(out) == []


@node
def test_d03c_senza_permesso_di_ripristino_nessun_bottone(staged):  # noqa: F811
    nel = {**NEL_CESTINO, "trash": {**NEL_CESTINO["trash"], "can_restore": False}}
    scenario = r"""
      await wait(); await wait();
      report({ bottone: !!C().querySelector('[data-building-restore-btn]'),
               testo: C().querySelector('[data-building-in-trash]').visibleText() });
    """
    out = _run(staged, scenario, _rotte("agent", scheda=(nel,)), "#/edifici/7")
    assert out["bottone"] is False and "Può ripristinarlo chi lo ha spostato o un amministratore." in out["testo"]


@node
def test_d04_pagina_cestino_scheda_edifici_e_ripristino(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const tabs = C().querySelectorAll('[data-trash-tab]').map((b) => b.dataset.trashTab);
      const iniziale = { immobili: !C().querySelector('[data-trash-panel="immobili"]').hidden,
                         edifici: !C().querySelector('[data-trash-panel="edifici"]').hidden,
                         letture: __calls.filter((c) => c.url.startsWith('/api/property/trash/buildings')).length };
      C().querySelector('[data-trash-tab="edifici"]').dispatch('click'); await wait(); await wait();
      const carte = C().querySelectorAll('[data-building-trash-item]').map((c) => [c.dataset.buildingTrashItem, c.visibleText()]);
      const apri = C().querySelectorAll('[data-building-trash-open]').map((a) => a.getAttribute('href'));
      C().querySelector('[data-building-restore="7"]').dispatch('click'); await wait(); await wait();
      const avviso = C().querySelector('[data-building-duplicates]');
      report({ tabs, iniziale, carte, apri, dopo: C().querySelectorAll('[data-building-trash-item]').map((c) => c.dataset.buildingTrashItem),
               avviso: avviso ? avviso.visibleText() : null, t: btoast() });
    """
    out = _run(staged, scenario, _rotte(), "#/cestino")
    # SENTINELLA AGGIORNATA DA CESTINO-RICHIESTE-1: la quarta scheda, «Richieste», dopo «Edifici»
    assert out["tabs"] == ["immobili", "contatti", "edifici", "richieste"]
    assert out["iniziale"] == {"immobili": True, "edifici": False, "letture": 0}     # Immobili resta la predefinita
    assert [c[0] for c in out["carte"]] == ["7", "9"]
    uno = out["carte"][0][1]
    assert "Palazzina 7" in uno and "Via Roma 7, Tortoreto" in uno and "Condominio" in uno
    assert "Creato per errore — Creata due volte" in uno and "Anna Agente" in uno and "6" in uno
    assert "non note" in out["carte"][1][1]
    assert out["apri"] == ["#/edifici/7", "#/edifici/9"]
    assert _scritture(out) == [("POST", "/api/property/buildings/7/restore")]
    assert out["dopo"] == ["9"] and out["t"] == "Edificio ripristinato" and "Gemella" in out["avviso"]


@node
def test_d04b_hash_diretto_apre_la_scheda_edifici(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      report({ edifici: !C().querySelector('[data-trash-panel="edifici"]').hidden,
               immobili: !C().querySelector('[data-trash-panel="immobili"]').hidden,
               letture: __calls.filter((c) => c.url.startsWith('/api/property/trash')).map((c) => c.url) });
    """
    out = _run(staged, scenario, _rotte(), "#/cestino/edifici")
    assert out["edifici"] is True and out["immobili"] is False
    assert out["letture"] == ["/api/property/trash/buildings?limit=50&offset=0"]
