"""STIMA360 TERRITORIO T4 - regole del client che girano SENZA database.

  A  funzioni pure (node): etichette del comando, riga «Posizione», punto da
     salvare (7 decimali, mai fuori intervallo), conferma solo a zoom >= 17;
  B  statici: il client invia SOLO latitude/longitude (mai operatore o data),
     nessun geocoding / geolocalizzazione, segnaposto provvisorio trascinabile
     solo lui, successo solo con «verified», nessuna voce nuova nella sidebar,
     la rotta vive dentro #/edifici/mappa; la 102 e' additiva e protetta.
Il percorso intero (backend + PostgreSQL + Chromium) e' in
tests/test_territorio_4_browser_postgres.py; le regole del database in
tests/test_territorio_4_postgres.py.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from tests import test_a30_5_create_ui as a30_5
from tests import test_delete_arch_2b3_ui as t2b3

node = t2b3.node
ROOT = Path(__file__).resolve().parents[1]
ASSETS = t2b3.ASSETS
MODELLO = ASSETS / "territorio" / "territorio-model.js"
POSIZIONA = ASSETS / "views" / "territorio-posiziona.js"
_codice = t2b3._codice


def _pure(expr):
    script = f"import * as m from '{MODELLO.as_posix()}';\nconsole.log(JSON.stringify({expr}));"
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                           timeout=30, env={"PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


@node
def test_a01_regole_del_client():
    out = _pure("""(() => {
      const v = { latitude: '42.8341', longitude: '13.9262', position_verified_at: '2026-10-13T08:30:00Z' };
      const u = { latitude: '42.8341', longitude: '13.9262' };
      return {
        etichette: ['verified', 'unverified', 'invalid', 'missing'].map(m.positionActionLabel),
        fatti: [m.positionFactText(v, 'Olga'), m.positionFactText(v), m.positionFactText(u),
                m.positionFactText({ latitude: '120', longitude: '13' }), m.positionFactText({})],
        punto: m.pointForSave(13.92621234567, 42.83414567891), fuori: m.pointForSave(200, 42), zero: m.pointForSave(0, 0),
        conferma: [m.canConfirm({ point: { lng: 1, lat: 1 }, zoom: 17 }), m.canConfirm({ point: { lng: 1, lat: 1 }, zoom: 16.9 }),
                   m.canConfirm({ point: null, zoom: 18 }), m.canConfirm({ point: { lng: 1, lat: 1 }, zoom: 18, saving: true })],
      };
    })()""")
    assert out["etichette"] == ["Correggi posizione", "Verifica posizione", "Posiziona sulla mappa", "Posiziona sulla mappa"]
    assert out["fatti"] == ["Verificata il 13/10/2026 da Olga", "Verificata il 13/10/2026", "Da verificare",
                            "Coordinate non valide: da posizionare", "Non indicata"]
    assert out["punto"] == {"latitude": "42.8341457", "longitude": "13.9262123"}
    assert out["fuori"] is None and out["zero"] is None
    assert out["conferma"] == [True, False, False, False]


def test_b01_il_client_manda_solo_le_coordinate():
    api = (ASSETS / "census" / "census-api.js").read_text(encoding="utf-8")
    corpo = api[api.index("export function confirmBuildingPosition"):]
    corpo = corpo[:corpo.index("\n}\n")]
    assert "{ latitude, longitude }" in corpo and "position_verified" not in corpo and "user" not in corpo
    vista = _codice(POSIZIONA)
    for vietata in ("geolocation", "Geolocate", "geocod", "nominatim", "fetch(", "apiPatch", "updateBuilding",
                    "position_verified_by", "user_id", "localStorage", "innerHTML = `${"):
        assert vietata not in vista, vietata
    assert vista.count("draggable: true") == 1 and vista.count("draggable: false") == 1
    assert "esito.status !== 'verified'" in vista and "highlightAfterConfirm(Number(id))" in vista
    assert "openMap(ml, area" in vista and "loadMapLibre()" in vista             # stessa mappa, stesso caricatore


def test_b02_rotta_dentro_edifici_nessuna_voce_nuova():
    main = (ASSETS / "main.js").read_text(encoding="utf-8")
    sezioni = main[main.index("const SECTIONS = ["):main.index("];", main.index("const SECTIONS = ["))]
    assert "mappa" not in sezioni and "territorio" not in sezioni and "posiziona" not in sezioni
    vista = (ASSETS / "views" / "territorio.js").read_text(encoding="utf-8")
    assert "if (parametri[1] === 'posiziona') return renderPosiziona(container, parametri[2]);" in vista
    scheda = (ASSETS / "views" / "edificio-dettaglio.js").read_text(encoding="utf-8")
    assert 'href="#/edifici/mappa/posiziona/${escapeHtml(edificio.id)}"' in scheda


def test_b03_la_102_e_additiva_e_protetta():
    su = (ROOT / "migrations" / "102_territorio_4_position_verification.sql").read_text(encoding="utf-8")
    giu = (ROOT / "migrations" / "102_territorio_4_position_verification_down.sql").read_text(encoding="utf-8")
    eseguibile = "\n".join(r for r in su.splitlines() if not r.lstrip().startswith("--"))
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", eseguibile, re.M)
    for vietata in ("UPDATE buildings SET", "DELETE FROM", "DROP COLUMN", "properties", "stime"):
        assert vietata not in eseguibile, vietata                            # nessun backfill, nessun altro dato
    assert "ADD COLUMN IF NOT EXISTS position_verified_at TIMESTAMPTZ" in su
    assert "REFERENCES operator_users(id) ON DELETE SET NULL" in su
    assert "NEW.position_verified_at := now();" in su
    assert "102 down: esistono posizioni verificate" in giu and giu.index("RAISE EXCEPTION") < giu.index("DROP COLUMN")


@node
def test_b04_node_check():
    for rel in ("views/territorio-posiziona.js", "views/territorio.js", "views/edificio-dettaglio.js",
                "territorio/territorio-model.js", "census/census-api.js", "census/census-model.js"):
        esito = subprocess.run([a30_5.NODE, "--check", str(ASSETS / rel)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, (rel, esito.stderr)
