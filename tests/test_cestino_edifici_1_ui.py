"""CESTINO-EDIFICI-1 (FASE G) - UI del Cestino Edifici (Shell OS), senza database.

  M  funzioni pure di trash/trash-model.js (node): blocchi con le unita' e i
     loro collegamenti (solo interni), nome e riga dell'edificio, percorso
     dell'elenco, possibili doppioni (stessa chiave catastale / indirizzo);
  S  statici: rotte nominate solo da trash/trash-api.js (e il percorso
     dell'elenco solo da trash-model.js); la scheda edificio usa il foglio
     condiviso; nessuna regola di permesso nel client; lessico;
  D  Shell VERA (main.js, router, sessione, scheda edificio, pagina Cestino)
     nello stub DOM di P26-4/A30-5 con fetch instradato (ELIMINAZIONE-EDIFICIO-1):
       d01  «Richiedi eliminazione…»: unita' coinvolte con i link, conferma con
            motivo e nota -> POST .../deletion-request, stato «in attesa»;
       d02  blocchi con i collegamenti, nessuna richiesta; «Ritira» solo per chi
            l'ha fatta -> POST .../withdraw, stato «ritirata»;
       d03  edificio nel Cestino: scheda in sola lettura, ripristino riservato al
            Super Admin, esito della richiesta; senza permesso, nessun bottone;
       d04  pagina Cestino: Immobili resta la scheda predefinita; Edifici al
            tocco o con #/cestino/edifici; edificio e unita' raggruppati, in sola
            lettura.

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
    # SENTINELLA AGGIORNATA DA ELIMINAZIONE-EDIFICIO-1: al posto di «Elimina…» (spostamento diretto)
    # la scheda monta il pannello «Eliminazione edificio» (richiesta al Super Admin); il ripristino
    # dalla scheda non e' dell'operatore.
    assert "buildingTrashButtonHtml()" not in scheda and "bindBuildingTrashButton(" not in scheda
    assert "mountDeletionPanel(container.querySelector('#building-deletion'), edificio.id);" in scheda
    assert "info.restore_by === 'super_admin'" in scheda
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
#
# SENTINELLA AGGIORNATA DA ELIMINAZIONE-EDIFICIO-1: la scheda edificio non sposta piu' nel Cestino
# («Elimina…») ne' ripristina: monta il pannello «Eliminazione edificio» (richiesta al Super Admin,
# unita' coinvolte, stato, ritiro). La pagina Cestino mostra l'edificio con le sue unita'
# raggruppate, in sola lettura. Le prove D seguono il comportamento nuovo; il Super Admin (Rete) e'
# provato in tests/test_eliminazione_edificio_1_ui.py.
# ---------------------------------------------------------------------------

rt = a30_5._rt()

VUOTO = {**SCHEDA, "units": [], "archived_units": []}
NEL_CESTINO = {**VUOTO, "deleted_at": "2026-10-06T08:00:00+02:00", "deleted_reason": "duplicate", "deleted_by_user_id": 1,
               "trash": {"deleted_at": "2026-10-06T08:00:00+02:00", "deleted_reason": "duplicate", "deleted_by_user_id": 1,
                         "deleted_by_name": "Super", "deleted_note": "Eliminazione dell'edificio approvata",
                         "can_restore": False, "restore_by": "super_admin"}}
UNITA = {"total": 3, "active": 2, "archived": 0, "trash": 1, "items": [
    {"id": 410, "label": "IMM-410 · Appartamento · piano 1", "state": "active"},
    {"id": 415, "label": "IMM-415 · Garage", "state": "active"},
    {"id": 420, "label": "IMM-420 · piano 2", "state": "trash"}]}
RICHIESTA = {"id": 55, "status": "pending", "reason_code": "duplicate", "request_note": "Doppione di via Roma",
             "requested_by_user_id": 3, "requested_by_name": "Anna Agente", "requested_at": "2026-10-12T09:00:00+02:00",
             "decided_by_name": None, "decided_at": None, "decision_note": None, "units_total": 3,
             "units_already_trashed": 1}
PUO = {"building_id": 7, "building_label": "Palazzina 7", "in_trash": False, "units": UNITA, "fingerprint": "a" * 64,
       "blockers": [], "can_request": True, "can_withdraw": False, "request": None}
ATTESA = {**PUO, "can_request": False, "can_withdraw": True, "request": RICHIESTA}
RITIRATA = {**PUO, "request": {**RICHIESTA, "status": "withdrawn", "decided_at": "2026-10-12T10:00:00+02:00"}}
BLOCCATO = {**PUO, "can_request": False, "blockers": [
    {"code": "PROPERTY_SOLD", "label": "Immobile venduto", "property_id": 410,
     "property_label": "IMM-410 · Appartamento · piano 1", "href": "#/immobili/410"},
    {"code": "PERTINENZA_FUORI_EDIFICIO", "label": "Pertinenze collegate a cavallo dell'edificio",
     "items": [{"property_id": 430, "label": "IMM-430 · Cantina"}]}]}
APPROVATA = {**PUO, "in_trash": True, "can_request": False, "request": {
    **RICHIESTA, "status": "approved", "decided_by_name": "Super", "decided_at": "2026-10-12T11:00:00+02:00"}}


def _gruppo(bid, **extra):
    v = {"id": bid, "agency_id": 7, "agency_name": "Agenzia A", "name": f"Palazzina {bid}",
         "label": f"Palazzina {bid} · Via Roma {bid}, Tortoreto", "deleted_at": "2026-10-12T11:00:00+02:00",
         "deleted_reason": "duplicate", "deleted_by_user_id": 1, "deleted_by_name": "Super",
         "units_total": 3, "units_moved_by_approval": 2,
         "units": [{**u, "moved_by_approval": u["state"] != "trash"} for u in UNITA["items"]],
         "fingerprint": "b" * 64, "request": {**APPROVATA["request"]}}
    v.update(extra)
    return v


GRUPPI = {"items": [_gruppo(7), _gruppo(9, units_total=0, units_moved_by_approval=0, units=[], request=None)],
          "has_more": False, "limit": 50, "offset": 0}


def _rotte(sessione="agency_owner", *, scheda=(VUOTO,), stato=(PUO,), richiesta=None, ritiro=None, gruppi=(GRUPPI,)):
    richiesta = richiesta or ({"status": 201, "body": RICHIESTA},)
    ritiro = ritiro or ({"status": 200, "body": RITIRATA["request"]},)
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
        ("GET", "/api/property/form-options", [rt.ok(OPZIONI_E1)]),
        ("GET", "/api/property/buildings/7/deletion-request", [s if "status" in s and "body" in s else rt.ok(s)
                                                               for s in stato]),
        ("POST", "/api/property/buildings/7/deletion-request", list(richiesta)),
        ("POST", "/api/property/building-deletion-requests/55/withdraw", list(ritiro)),
        ("GET", "/api/property/buildings/7", [rt.ok(s) for s in scheda]),
        ("GET", "/api/property/buildings", [rt.ok({"items": [], "total": 0, "limit": 25, "offset": 0})]),
        ("GET", "/api/property/trash/building-groups", [rt.ok(x) for x in gruppi]),
        ("GET", "/api/property/trash", [rt.ok({"items": [], "has_more": False, "limit": 50, "offset": 0})]),
        ("GET", "/api/core/trash/contacts", [rt.ok({"items": [], "has_more": False, "limit": 50, "offset": 0})]),
        ("GET", "/api/", [rt.ok({"items": []})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


HELPERS = r"""
const P = () => C().querySelector('[data-deletion-panel]');
const pq = (s) => P().querySelector(s);
"""


def _run(staged, scenario, rotte, hash):  # noqa: F811
    return t2b3._run(staged, HELPERS + scenario, rotte, hash)


def _scritture(out):
    return [(c["m"], c["url"]) for c in out["calls"] if c["m"] in ("POST", "PATCH", "PUT", "DELETE")]


@node
def test_d01_richiesta_eliminazione_con_unita_coinvolte(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const prima = { elimina: !!C().querySelector('#building-trash-btn'), testo: P().visibleText(),
                      unita: pq('[data-deletion-units]').querySelectorAll('a').map((a) => [a.getAttribute('href'), a.textContent]),
                      apri: pq('[data-deletion-open]') ? pq('[data-deletion-open]').textContent : null };
      pq('[data-deletion-open]').dispatch('click'); await wait();
      const statoConferma = { motivo: !!pq('[data-deletion-reason]'), testo: P().visibleText() };
      pq('[data-deletion-reason]').value = 'duplicate';
      pq('[data-deletion-note]').value = '  Doppione di via Roma  ';
      pq('[data-deletion-confirm]').dispatch('click'); await wait(); await wait();
      report({ prima, statoConferma, dopo: P().visibleText(), stato: pq('[data-deletion-status]').dataset.deletionStatus,
               ritira: !!pq('[data-deletion-withdraw]') });
    """
    out = _run(staged, scenario, _rotte("agent", stato=(PUO, ATTESA)), "#/edifici/7")
    p = out["prima"]
    assert p["elimina"] is False and p["apri"] == "Richiedi eliminazione…"           # mai uno spostamento diretto
    assert "Coinvolge 3 unità: 2 attive, 1 già nel Cestino." in p["testo"]
    assert p["unita"] == [["#/immobili/410", "IMM-410 · Appartamento · piano 1"], ["#/immobili/415", "IMM-415 · Garage"],
                          ["#/immobili/420", "IMM-420 · piano 2"]]
    assert "la autorizza il Super Admin" in p["testo"]
    assert out["statoConferma"]["motivo"] is True and "finché il Super Admin non approva" in out["statoConferma"]["testo"]
    assert _scritture(out) == [("POST", "/api/property/buildings/7/deletion-request")]
    corpo = next(c for c in out["calls"] if c["m"] == "POST")["body"]
    assert corpo == {"reason_code": "duplicate", "note": "Doppione di via Roma"}
    assert out["stato"] == "pending" and out["ritira"] is True
    assert "In attesa di approvazione" in out["dopo"] and "Anna Agente" in out["dopo"]


@node
def test_d02_blocchi_e_ritiro(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      report({ testo: P().visibleText(), apri: !!pq('[data-deletion-open]'),
               blocchi: P().querySelectorAll('[data-blocker-code]').map((li) => li.dataset.blockerCode),
               link: pq('[data-deletion-blockers]').querySelectorAll('a').map((a) => a.getAttribute('href')) });
    """
    out = _run(staged, scenario, _rotte(stato=(BLOCCATO,)), "#/edifici/7")
    assert out["apri"] is False and out["blocchi"] == ["PROPERTY_SOLD", "PERTINENZA_FUORI_EDIFICIO"]
    assert "risolvi prima questi punti" in out["testo"] and "Immobile venduto" in out["testo"]
    assert out["link"] == ["#/immobili/410", "#/immobili/430"]
    assert _scritture(out) == []
    scenario = r"""
      await wait(); await wait();
      const prima = P().visibleText();
      pq('[data-deletion-withdraw]').dispatch('click'); await wait(); await wait();
      report({ prima, dopo: P().visibleText(), stato: pq('[data-deletion-status]').dataset.deletionStatus,
               apri: !!pq('[data-deletion-open]') });
    """
    out = _run(staged, scenario, _rotte("agent", stato=(ATTESA, RITIRATA)), "#/edifici/7")
    assert "In attesa di approvazione" in out["prima"] and "Ritira la richiesta" in out["prima"]
    assert _scritture(out) == [("POST", "/api/property/building-deletion-requests/55/withdraw")]
    assert out["stato"] == "withdrawn" and "Ritirata" in out["dopo"] and out["apri"] is True
    # un altro operatore vede la richiesta in attesa ma non la ritira
    scenario = r"""
      await wait(); await wait();
      report({ testo: P().visibleText(), ritira: !!pq('[data-deletion-withdraw]') });
    """
    out = _run(staged, scenario, _rotte(stato=({**ATTESA, "can_withdraw": False},)), "#/edifici/7")
    assert out["ritira"] is False and "Può ritirarla solo chi l’ha fatta." in out["testo"]


@node
def test_d03_scheda_nel_cestino_sola_lettura_ripristino_del_super_admin(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const banner = C().querySelector('[data-building-in-trash]');
      report({ banner: banner ? banner.visibleText() : null, ripristina: !!C().querySelector('[data-building-restore-btn]'),
               modifica: !!C().querySelector('#building-edit'), elimina: !!C().querySelector('#building-trash-btn'),
               aggiungi: !!C().querySelector('#unit-add-apartment'), pertinenza: !!C().querySelector('#unit-add-pertinenza'),
               unita: C().querySelector('#building-units').visibleText(), pannello: P().visibleText(),
               link: banner ? banner.querySelectorAll('a').map((a) => a.getAttribute('href')) : [] });
    """
    out = _run(staged, scenario, _rotte(scheda=(NEL_CESTINO,), stato=(APPROVATA,)), "#/edifici/7")
    assert "Nel Cestino" in out["banner"] and "Super" in out["banner"]
    assert "Il ripristino è riservato al Super Admin della piattaforma." in out["banner"]
    assert (out["ripristina"], out["modifica"], out["elimina"], out["aggiungi"], out["pertinenza"]) == (False,) * 5
    assert "Nessuna unità: l’edificio è nel Cestino." in out["unita"] and out["link"] == ["#/cestino/edifici"]
    assert "Approvata da Super" in out["pannello"]
    assert _scritture(out) == []


@node
def test_d03b_scheda_nel_cestino_dalla_procedura_non_apre_il_foglio_unita(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      report({ banner: !!C().querySelector('[data-building-in-trash]'), aggiungi: !!C().querySelector('#unit-add-apartment'),
               foglioUnita: !!C().querySelector('dialog[open]') });
    """
    out = _run(staged, scenario, _rotte(scheda=(NEL_CESTINO,), stato=(APPROVATA,)), "#/edifici/7/aggiungi/crm")
    assert out["banner"] is True and out["aggiungi"] is False and out["foglioUnita"] is False
    assert _scritture(out) == []


@node
def test_d03c_senza_permesso_di_ripristino_nessun_bottone(staged):  # noqa: F811
    nel = {**NEL_CESTINO, "trash": {**NEL_CESTINO["trash"], "can_restore": False, "restore_by": None}}
    scenario = r"""
      await wait(); await wait();
      report({ bottone: !!C().querySelector('[data-building-restore-btn]'),
               testo: C().querySelector('[data-building-in-trash]').visibleText() });
    """
    out = _run(staged, scenario, _rotte("agent", scheda=(nel,), stato=(APPROVATA,)), "#/edifici/7")
    assert out["bottone"] is False and "Può ripristinarlo chi lo ha spostato o un amministratore." in out["testo"]


@node
def test_d04_pagina_cestino_edifici_raggruppati_in_sola_lettura(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const tabs = C().querySelectorAll('[data-trash-tab]').map((b) => b.dataset.trashTab);
      const iniziale = { immobili: !C().querySelector('[data-trash-panel="immobili"]').hidden,
                         edifici: !C().querySelector('[data-trash-panel="edifici"]').hidden,
                         letture: __calls.filter((c) => c.url.startsWith('/api/property/trash/building-groups')).length };
      C().querySelector('[data-trash-tab="edifici"]').dispatch('click'); await wait(); await wait();
      const carte = C().querySelectorAll('[data-building-group]').map((c) => [c.dataset.buildingGroup, c.visibleText()]);
      report({ tabs, iniziale, carte, apri: C().querySelectorAll('[data-building-trash-open]').map((a) => a.getAttribute('href')),
               ripristina: C().querySelectorAll('[data-group-restore], [data-building-restore]').length,
               elimina: C().querySelectorAll('[data-group-purge]').length,
               intro: C().querySelector('[data-trash-panel="edifici"]').visibleText() });
    """
    out = _run(staged, scenario, _rotte(), "#/cestino")
    assert out["tabs"] == ["immobili", "contatti", "edifici", "richieste"]
    assert out["iniziale"] == {"immobili": True, "edifici": False, "letture": 0}     # Immobili resta la predefinita
    assert [c[0] for c in out["carte"]] == ["7", "9"]
    uno = out["carte"][0][1]
    assert "Palazzina 7 · Via Roma 7, Tortoreto" in uno and "Duplicato" in uno and "Super" in uno
    assert "3 unità nel Cestino con l’edificio (2 spostate dall’approvazione, 1 già nel Cestino prima)" in uno
    assert "IMM-410 · Appartamento · piano 1" in uno and "Richiesta da Anna Agente" in uno
    assert "Ripristino ed eliminazione definitiva: solo il Super Admin." in uno
    assert "Nessuna unità" in out["carte"][1][1]
    assert out["apri"] == ["#/edifici/7", "#/edifici/9"] and out["ripristina"] == 0 and out["elimina"] == 0
    assert "li decide il Super Admin" in out["intro"]
    assert _scritture(out) == []


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
    assert out["letture"] == ["/api/property/trash/building-groups?limit=50&offset=0"]
