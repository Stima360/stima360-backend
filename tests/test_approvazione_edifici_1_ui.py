"""APPROVAZIONE-EDIFICI-1 - l'HTML (funzioni pure, node) del collegamento «dove si
approva» nella scheda dell'edificio e della conferma di «Approva» nel pannello
del Super Admin. La campagna completa in browser e'
`test_approvazione_edifici_1_browser_postgres.py`.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests.test_eliminazione_edificio_1_ui import _codice

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
MODULO = ASSETS / "building-deletion" / "building-deletion.js"
RETE = ASSETS / "views" / "rete-eliminazioni.js"

node = pytest.mark.skipif(a30_5.NODE is None, reason="node non disponibile: livello NON eseguito (BLOCKED)")


def _valuta(expr):
    script = (f"import * as m from '{MODULO.as_posix()}';\nimport * as r from '{RETE.as_posix()}';\n"
              f"console.log(JSON.stringify({expr}));")
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True,
                           text=True, timeout=30, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


RICHIESTA = {"id": 3, "status": "pending", "requested_by_name": "Edoardo", "requested_at": "2026-10-13T10:00:00+02:00",
             "reason_code": "created_by_mistake", "request_note": "", "agency_name": "STIMA360 Giulianova",
             "building_label": "Censori casa · Via Filippo Turati 1, Alba Adriatica", "units_total": 0}


@node
def test_u01_scheda_edificio_dice_dove_si_approva():
    out = _valuta("""({
      superAdmin: m.deletionPanelHtml({ units: { total: 0 }, request: %s, can_request: false, can_withdraw: false, blockers: [] },
                                      { platformAdmin: true }),
      operatore: m.deletionPanelHtml({ units: { total: 0 }, request: %s, can_request: false, can_withdraw: false, blockers: [] }),
      decisa: m.deletionPanelHtml({ units: { total: 0 }, request: { ...%s, status: 'approved', decided_at: '2026-10-13T11:00:00+02:00' },
                                    can_request: false, can_withdraw: false, blockers: [] }, { platformAdmin: true }),
      hash: m.DECISION_HASH,
    })""" % (json.dumps(RICHIESTA), json.dumps(RICHIESTA), json.dumps(RICHIESTA)))
    assert out["hash"] == "#/cestino/richieste-edifici"
    s = out["superAdmin"]
    assert "In attesa di approvazione" in s and 'data-deletion-decide="super-admin"' in s
    assert 'href="#/cestino/richieste-edifici" data-deletion-decide-link' in s and "Apri Cestino › Richieste edifici" in s
    assert "data-deletion-approve" not in s                     # nella scheda non si approva: si va dove si decide
    o = out["operatore"]
    assert 'data-deletion-decide="operator"' in o and "La decide il Super Admin" in o and "Vedi le richieste della tua agenzia" in o
    assert "Apri Cestino › Richieste edifici" not in o
    assert "data-deletion-decide" not in out["decisa"]           # solo finche' e' in attesa


@node
def test_u02_approva_apre_la_conferma_e_lo_dice_nella_card():
    dettaglio = {"request": RICHIESTA, "building": {"id": 9, "label": "Censori casa · Via Filippo Turati 1, Alba Adriatica"},
                 "units": {"total": 0, "active": 0}, "fingerprint": "a" * 64, "stale": False, "blockers": [],
                 "can_approve": True}
    out = _valuta("""({
      card: r.requestRowHtml(%s),
      cardDecisa: r.requestRowHtml({ ...%s, status: 'rejected' }),
      dettaglio: r.requestDetailHtml(%s),
      conUnita: r.requestDetailHtml({ ...%s, units: { total: 3, active: 2, trash: 1 } }),
    })""" % (json.dumps(RICHIESTA), json.dumps(RICHIESTA), json.dumps(dettaglio), json.dumps(dettaglio)))
    assert "Apri il dettaglio per approvare o rifiutare." in out["card"] and "STIMA360 Giulianova" in out["card"]
    assert "Edoardo" in out["card"] and "Creato per errore" in out["card"]
    assert "data-deletion-hint" not in out["cardDecisa"]
    d = out["dettaglio"]
    assert "data-deletion-approve>" in d and "data-deletion-approve-confirm hidden" in d
    assert "data-deletion-approve-yes" in d and "data-deletion-approve-no" in d
    assert "Confermi l’approvazione?" in d and "Censori casa" in d and "(STIMA360 Giulianova)" in d
    assert "Nessuna cancellazione definitiva" in d and "con 0 unità" not in d and " con " not in d.split("Confermi")[1].split("Nessuna")[0]
    assert "con 2 unità attive" in out["conUnita"]


def test_s01_nessuna_regola_di_permesso_e_nessuna_rotta_nuova():
    for p in (MODULO, RETE):
        testo = _codice(p)
        for regola in ("is_platform_admin", "getSession", ".role", "agency_owner"):
            assert regola not in testo, (p.name, regola)
    # la scheda passa solo cio' che la sessione gia' dice; nessuna decisione di permesso nel client
    scheda = _codice(ASSETS / "views" / "edificio-dettaglio.js")
    assert "platformAdmin: canUsePlatformSurface(sessione)" in scheda
    # «Sì, approva» e' l'unico punto che chiama l'approvazione
    rete = _codice(RETE)
    assert rete.count("approveDeletion(") == 1 and "data-deletion-approve-yes" in rete
