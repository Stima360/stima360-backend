"""ELIMINAZIONE-EDIFICIO-1 - UI (Shell OS), senza database.

  M  funzioni pure di building-deletion/building-deletion.js (node): riepilogo
     delle unita', stato della richiesta, blocchi con i collegamenti (solo
     interni), riepilogo della cancellazione definitiva con la seconda conferma;
  S  statici: le rotte nominate solo da building-deletion.js; nessuna regola di
     permesso nel client; il client del Cestino resta senza comandi del Super
     Admin; Voice non nomina queste rotte;
  D  Shell VERA (main.js, router, sessione, Rete) nello stub DOM di P26-4/A30-5
     con fetch instradato: la scheda «Eliminazioni edifici» del Super Admin -
     dettaglio, «Rifiuta» senza motivo fermo, «Approva» con l'impronta vista;
     cancellazione definitiva: riepilogo, conferma disabilitata finche' il testo
     non e' esatto, POST con testo e impronta; dipendenze -> nessun comando.

Senza node le prove M/D sono SKIPPED (BLOCKED), mai passate.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests import test_a30_13b_quick_booking_ui as a30_13b
from tests import test_delete_arch_2b3_ui as t2b3
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
MODULO = ASSETS / "building-deletion" / "building-deletion.js"
RETE = ASSETS / "views" / "rete-eliminazioni.js"

node = pytest.mark.skipif(a30_5.NODE is None, reason="node non disponibile: livello NON eseguito (BLOCKED)")
_codice = t2b3._codice


def _modello(expr):
    """Valuta `expr` con `m` = building-deletion.js (stessa tecnica di t2b3._modello)."""
    import subprocess
    script = f"import * as m from '{MODULO.as_posix()}';\nconsole.log(JSON.stringify({expr}));"
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True,
                           text=True, timeout=30, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# M - funzioni pure
# ---------------------------------------------------------------------------

@node
def test_m01_testi_stato_blocchi_e_conferma():
    out = _modello("""({
      unita: [m.unitsSummaryText({ total: 5, active: 3, archived: 1, trash: 1 }), m.unitsSummaryText({ total: 0 }),
              m.unitsSummaryText({ total: 1, active: 1 })],
      attesa: m.requestStatusView({ status: 'pending', requested_by_name: 'Cesare', requested_at: '2026-10-12T09:00:00+02:00',
                                    reason_code: 'duplicate', stale: true }),
      rifiutata: m.requestStatusView({ status: 'rejected', requested_by_name: 'Giorgio', requested_at: '2026-10-12T09:00:00+02:00',
                                       decided_by_name: 'Super', decided_at: '2026-10-12T10:00:00+02:00',
                                       decision_note: 'Esiste davvero', reason_code: 'other' }),
      blocchi: m.blockersHtml([{ code: 'FK_activities', label: 'Attività', count: 2,
                                 items: [{ property_id: 41, label: 'IMM-41' }, { property_id: 42, label: 'IMM-42' }] },
                               { code: 'PROPERTY_SOLD', label: 'Venduto', property_id: 43, property_label: 'IMM-43',
                                 href: '#/immobili/43' }]),
      bloccata: m.purgeSummaryHtml({ eligible: false, blockers: [{ code: 'VOICE_HISTORY', label: 'Storico di STIMA Voice', count: 1, items: [] }] }),
      pronta: m.purgeSummaryHtml({ eligible: true, building: { label: 'Palazzina 7' }, units: { total: 2, active: 0, trash: 2 },
                                   blockers: [], will_delete: [{ table: 'property_contacts', label: 'Collegamenti ai proprietari (i contatti restano)', count: 1 }] }),
      testo: m.PURGE_CONFIRM_TEXT,
    })""")
    assert out["unita"] == ["Coinvolge 5 unità: 3 attive, 1 archiviata, 1 già nel Cestino.",
                            "Nessuna unità collegata: si elimina solo l’edificio.", "Coinvolge 1 unità: 1 attiva."]
    a = out["attesa"]
    assert (a["label"], a["tone"], a["stale"], a["reason"]) == ("In attesa di approvazione", "warn", True, "Duplicato")
    assert a["requested"].startswith("Richiesta da Cesare il 12/10/2026")
    r = out["rifiutata"]
    assert r["label"] == "Rifiutata" and r["outcome"].startswith("Rifiutata da Super il 12/10/2026") and "«Esiste davvero»" in r["outcome"]
    assert 'data-blocker-code="FK_activities"' in out["blocchi"] and 'href="#/immobili/41"' in out["blocchi"]
    assert 'href="#/immobili/43"' in out["blocchi"] and "(2)" in out["blocchi"]
    assert "data-purge-blocked" in out["bloccata"] and "data-purge-confirm" not in out["bloccata"]
    assert "Storico di STIMA Voice" in out["bloccata"]
    assert "data-purge-ready" in out["pronta"] and "data-purge-confirm disabled" in out["pronta"]
    assert "Contatti, proprietari, stime e storico del registro restano." in out["pronta"]
    assert out["testo"] == "ELIMINA DEFINITIVAMENTE"


# ---------------------------------------------------------------------------
# S - statici
# ---------------------------------------------------------------------------

ROTTE = re.compile(r"/(deletion-request|building-deletion-requests|building-deletions|building-groups)\b")


def test_s01_rotte_solo_nel_client_proprio():
    fuori = sorted(p.relative_to(ASSETS).as_posix() for p in ASSETS.rglob("*.js") if ROTTE.search(_codice(p)))
    assert fuori == ["building-deletion/building-deletion.js"], fuori


def test_s02_nessuna_regola_di_permesso_nel_client():
    for p in (MODULO, RETE):
        testo = _codice(p)
        for regola in ("is_platform_admin", "getSession", ".role", "agency_owner", "deleted_by_user_id", "agency_id ="):
            assert regola not in testo, (p.name, regola)
        assert "apiDelete" not in testo and "'DELETE'" not in testo, p.name
    # il client del Cestino (tenant) non ha comandi del Super Admin
    cestino = "\n".join(_codice(p) for p in (*(ASSETS / "trash").glob("*.js"), ASSETS / "views" / "cestino.js"))
    for vietata in ("purge", "Elimina definitivamente", "restoreTrashedBuilding", "approveDeletion"):
        assert vietata not in cestino, vietata
    # STIMA Voice non conosce queste rotte
    voce = "\n".join(_codice(p) for p in (ASSETS / "voice").glob("*.js"))
    assert not ROTTE.search(voce)


def test_s03_rete_monta_la_scheda_eliminazioni():
    # SENTINELLA AGGIORNATA DA CESTINO-GENERALE-1: la gestione e' SPOSTATA in Cestino › Richieste edifici
    # (stesso codice, `renderEliminazioni`); in Rete la vecchia scheda e' solo un collegamento, senza duplicati.
    rete = _codice(ASSETS / "views" / "rete.js")
    assert "{ key: 'eliminazioni', label: 'Eliminazioni edifici' }" in rete
    assert "if (key === 'eliminazioni') return renderCollegamentoEliminazioni(contentEl);" in rete
    assert "renderEliminazioni" not in rete and 'href="#/cestino/richieste-edifici"' in rete
    cestino = _codice(ASSETS / "views" / "cestino.js")
    assert "? () => renderEliminazioni(hostRichiesteEdifici)" in cestino
    assert "approveDeletion(id, d.fingerprint, nota())" in _codice(RETE)
    assert "purgeBuilding(id, testo.value, check.fingerprint)" in _codice(RETE)
    assert "conferma.disabled = testo.value.trim() !== PURGE_CONFIRM_TEXT;" in _codice(RETE)


@node
def test_s04_node_check():
    import subprocess
    for p in (MODULO, RETE, ASSETS / "views" / "rete.js", ASSETS / "views" / "cestino.js",
              ASSETS / "views" / "edificio-dettaglio.js"):
        esito = subprocess.run([a30_5.NODE, "--check", str(p)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, (p.name, esito.stderr)


# ---------------------------------------------------------------------------
# D - Rete del Super Admin (stub DOM)
# ---------------------------------------------------------------------------

rt = a30_5._rt()

RICHIESTA = {"id": 55, "agency_id": 7, "agency_name": "Agenzia A", "building_id": 7, "building_label": "Palazzina 7 · Via Roma 7, Tortoreto",
             "status": "pending", "reason_code": "duplicate", "request_note": "Doppione",
             "requested_by_user_id": 3, "requested_by_name": "Cesare", "requested_at": "2026-10-12T09:00:00+02:00",
             "units_total": 3, "units_already_trashed": 1, "units_fingerprint": "a" * 64, "building_exists": True,
             "building_in_trash": False, "stale": False}
DETTAGLIO = {"request": RICHIESTA, "building": {"id": 7, "label": "Palazzina 7 · Via Roma 7, Tortoreto", "deleted_at": None},
             "units": {"total": 3, "active": 2, "archived": 0, "trash": 1, "items": [
                 {"id": 410, "label": "IMM-410", "state": "active"}, {"id": 415, "label": "IMM-415", "state": "active"},
                 {"id": 420, "label": "IMM-420", "state": "trash"}]},
             "fingerprint": "c" * 64, "blockers": [], "stale": False, "can_approve": True, "can_reject": True}
GRUPPO = {"id": 9, "agency_id": 7, "agency_name": "Agenzia A", "label": "Palazzina 9 · Via Roma 9, Tortoreto",
          "deleted_at": "2026-10-12T11:00:00+02:00", "deleted_reason": "duplicate", "deleted_by_name": "Super",
          "units_total": 2, "units_moved_by_approval": 2,
          "units": [{"id": 500, "label": "IMM-500", "state": "trash", "moved_by_approval": True},
                    {"id": 501, "label": "IMM-501", "state": "trash", "moved_by_approval": True}],
          "fingerprint": "d" * 64, "request": None}
CHECK_OK = {"building": {"id": 9, "label": "Palazzina 9"}, "units": {"total": 2, "active": 0, "archived": 0, "trash": 2},
            "fingerprint": "e" * 64, "eligible": True, "blockers": [],
            "will_delete": [{"table": "property_contacts", "label": "Collegamenti ai proprietari (i contatti restano)", "count": 1}],
            "confirm_text": "ELIMINA DEFINITIVAMENTE"}
CHECK_NO = {**CHECK_OK, "eligible": False, "will_delete": [],
            "blockers": [{"code": "FK_activities", "label": "Attività o interazioni (storico commerciale)", "count": 1,
                          "items": [{"property_id": 500, "label": "IMM-500"}]}]}


def _rotte(*, check=(CHECK_OK,), dettaglio=(DETTAGLIO,)):
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione("supreme"))]),
        ("GET", "/api/platform/me", [rt.ok({"user_id": 1, "is_platform_admin": True})]),
        ("GET", "/api/platform/agencies", [rt.ok([])]),
        ("GET", "/api/platform/building-deletion-requests/55", [rt.ok(d) for d in dettaglio]),
        ("POST", "/api/platform/building-deletion-requests/55/approve", [rt.ok({**RICHIESTA, "status": "approved"})]),
        ("POST", "/api/platform/building-deletion-requests/55/reject", [rt.ok({**RICHIESTA, "status": "rejected"})]),
        ("GET", "/api/platform/building-deletion-requests?", [rt.ok({"items": [RICHIESTA], "has_more": False})]),
        ("GET", "/api/platform/building-deletions/trash", [rt.ok({"items": [GRUPPO], "has_more": False})]),
        ("GET", "/api/platform/building-deletions/9/purge-check", [rt.ok(c) for c in check]),
        ("POST", "/api/platform/building-deletions/9/purge", [rt.ok({"building_id": 9, "purged_unit_ids": [500, 501]})]),
        ("POST", "/api/platform/building-deletions/9/restore", [rt.ok({"building": {"id": 9}, "restored_unit_ids": [500, 501]})]),
        ("GET", "/api/", [rt.ok({"items": []})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


HELPERS = r"""
// SENTINELLA AGGIORNATA DA CESTINO-GENERALE-1: la scheda del Super Admin e' ora Cestino › Richieste edifici
const tabE = () => C().querySelector('[data-trash-tab="richieste-edifici"]');
const cartaR = (id) => C().querySelector(`[data-deletion-request="${id}"]`);
const grp = (id) => C().querySelector(`[data-building-group="${id}"]`);
"""


def _run(staged, scenario, rotte):  # noqa: F811
    # SENTINELLA AGGIORNATA DA CESTINO-GENERALE-1: stessa gestione, nuova posizione (prima «#/rete»)
    return t2b3._run(staged, HELPERS + scenario, rotte, "#/cestino/richieste-edifici")


def _scritture(out):
    return [(c["m"], c["url"]) for c in out["calls"] if c["m"] in ("POST", "PATCH", "PUT", "DELETE")]


@node
def test_d01_richieste_dettaglio_rifiuto_e_approvazione(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      tabE().dispatch('click'); await wait(); await wait();
      const elenco = cartaR(55).visibleText();
      cartaR(55).querySelector('[data-deletion-detail-open]').dispatch('click'); await wait(); await wait();
      const dettaglio = cartaR(55).querySelector('[data-deletion-detail]').visibleText();
      const unita = cartaR(55).querySelector('[data-deletion-units]').querySelectorAll('a').map((a) => a.getAttribute('href'));
      cartaR(55).querySelector('[data-deletion-reject]').dispatch('click'); await wait();
      const erroreRifiuto = cartaR(55).querySelector('[data-trash-card-error]').visibleText();
      const scrittePrima = __calls.filter((c) => (c.options.method || 'GET') !== 'GET').length;
      cartaR(55).querySelector('[data-deletion-decision-note]').value = 'ok, verificato';
      cartaR(55).querySelector('[data-deletion-approve]').dispatch('click'); await wait(); await wait();
      report({ elenco, dettaglio, unita, erroreRifiuto, scrittePrima });
    """
    out = _run(staged, scenario, _rotte())
    assert "Palazzina 7 · Via Roma 7, Tortoreto" in out["elenco"] and "Agenzia A" in out["elenco"]
    assert "In attesa di approvazione" in out["elenco"] and "Cesare" in out["elenco"] and "3 unità, 1 già nel Cestino" in out["elenco"]
    assert "Coinvolge 3 unità: 2 attive, 1 già nel Cestino." in out["dettaglio"]
    assert out["unita"] == ["#/immobili/410", "#/immobili/415", "#/immobili/420"]
    assert out["erroreRifiuto"] == "Scrivi il motivo del rifiuto." and out["scrittePrima"] == 0
    assert _scritture(out) == [("POST", "/api/platform/building-deletion-requests/55/approve")]
    corpo = next(c for c in out["calls"] if c["m"] == "POST")["body"]
    assert corpo == {"fingerprint": "c" * 64, "note": "ok, verificato"}         # l'impronta VISTA nel dettaglio


@node
def test_d02_richiesta_da_riverificare_non_approvabile(staged):  # noqa: F811
    d = {**DETTAGLIO, "stale": True, "can_approve": False, "request": {**RICHIESTA, "stale": True}}
    scenario = r"""
      await wait(); await wait();
      tabE().dispatch('click'); await wait(); await wait();
      cartaR(55).querySelector('[data-deletion-detail-open]').dispatch('click'); await wait(); await wait();
      report({ testo: cartaR(55).visibleText(), approva: cartaR(55).querySelector('[data-deletion-approve]').disabled });
    """
    out = _run(staged, scenario, _rotte(dettaglio=(d,)))
    assert out["approva"] is True and "Le unità sono cambiate dalla richiesta" in out["testo"]
    assert _scritture(out) == []


@node
def test_d03_cancellazione_definitiva_seconda_conferma(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      tabE().dispatch('click'); await wait(); await wait();
      const carta = grp(9).visibleText();
      grp(9).querySelector('[data-group-purge]').dispatch('click'); await wait(); await wait();
      const riepilogo = grp(9).querySelector('[data-group-purge-host]').visibleText();
      const bottonePurge = () => grp(9).querySelector('[data-purge-confirm]');
      const campo = grp(9).querySelector('[data-purge-text]');
      const iniziale = bottonePurge().disabled;
      campo.value = 'elimina'; campo.dispatch('input');
      const sbagliato = bottonePurge().disabled;
      campo.value = 'ELIMINA DEFINITIVAMENTE'; campo.dispatch('input');
      const giusto = bottonePurge().disabled;
      bottonePurge().dispatch('click'); await wait(); await wait();
      report({ carta, riepilogo, iniziale, sbagliato, giusto });
    """
    out = _run(staged, scenario, _rotte())
    assert "2 unità nel Cestino con l’edificio (2 spostate dall’approvazione)" in out["carta"]
    assert "non si può annullare" in out["riepilogo"] and "i contatti restano" in out["riepilogo"]
    assert (out["iniziale"], out["sbagliato"], out["giusto"]) == (True, True, False)
    assert _scritture(out) == [("POST", "/api/platform/building-deletions/9/purge")]
    corpo = next(c for c in out["calls"] if c["m"] == "POST")["body"]
    assert corpo == {"confirm_text": "ELIMINA DEFINITIVAMENTE", "fingerprint": "e" * 64}


@node
def test_d04_dipendenze_nessun_comando_e_ripristino(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      tabE().dispatch('click'); await wait(); await wait();
      grp(9).querySelector('[data-group-purge]').dispatch('click'); await wait(); await wait();
      const host = grp(9).querySelector('[data-group-purge-host]');
      const bloccata = { testo: host.visibleText(), conferma: !!host.querySelector('[data-purge-confirm]') };
      grp(9).querySelector('[data-group-restore]').dispatch('click'); await wait(); await wait();
      report({ bloccata });
    """
    out = _run(staged, scenario, _rotte(check=(CHECK_NO,)))
    assert out["bloccata"]["conferma"] is False and "Non si può eliminare definitivamente" in out["bloccata"]["testo"]
    assert "Attività o interazioni (storico commerciale)" in out["bloccata"]["testo"]
    assert _scritture(out) == [("POST", "/api/platform/building-deletions/9/restore")]
