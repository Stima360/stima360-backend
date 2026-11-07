"""SELEZIONE MULTIPLA - le funzioni pure di components/contact-bulk.js, eseguite in node
(nessuna rete: la chiamata al server e' una funzione finta che conta i blocchi).

    U1  «Interrompi» dopo il primo blocco: nessun blocco successivo parte, il riepilogo
        conserva i gia' spostati e dice quanti restano
    U2  «Interrompi» durante il secondo blocco: il blocco in corso finisce, poi stop
    U3  un errore a meta': i blocchi gia' fatti restano nel riepilogo, gli altri sono «da fare»
    U4  un server che non avanza non fa girare il ciclo all'infinito
    U5  le note sulle comunicazioni: dopo lo spostamento e dopo il ripristino si dice
        che automazioni sospese e messaggi annullati NON si riattivano da soli
    U6  la selezione: singoli, pagina, tutti i risultati meno quelli tolti, azzeramento
    U7  il numero scritto per confermare
"""
from __future__ import annotations

import json
import subprocess

import pytest

from tests import test_a30_5_create_ui as a30_5

ASSETS = a30_5.ASSETS
BULK = (ASSETS / "components" / "contact-bulk.js").as_posix()

PROVA = r"""
import * as b from '%s';
const ids = Array.from({ length: 250 }, (_, i) => i + 1);
const out = {};
const finto = (log, { failAt = null, stall = false } = {}) => async (pezzo, op) => {
  log.push(pezzo.length);
  if (failAt !== null && log.length === failAt) throw new Error('rete giù');
  return { operation_id: op || 'op-1', trashed: pezzo.slice(0, pezzo.length - 1),
           blocked: [{ id: pezzo[pezzo.length - 1], label: 'Bloccato', blockers: [{ code: 'LEAD_OPEN', label: 'Lead aperto' }] }],
           remaining: stall ? pezzo.slice() : [] };
};
// U1
let log = []; let stop = false;
let r = await b.runInBlocks(ids, async (p, op) => { const x = await finto(log)(p, op); stop = true; return x; }, { shouldStop: () => stop });
out.u1 = { calls: log.length, done: r.done.length, blocked: r.blocked.length, stopped: r.stopped, remaining: r.remaining.length,
           op: r.operation_id, html: b.bulkSummaryHtml(r, 'trash') };
// U2
log = []; stop = false;
r = await b.runInBlocks(ids, async (p, op) => { if (log.length === 1) stop = true; return finto(log)(p, op); }, { shouldStop: () => stop });
out.u2 = { calls: log.length, done: r.done.length, stopped: r.stopped, remaining: r.remaining.length };
// U3
log = [];
r = await b.runInBlocks(ids, finto(log, { failAt: 2 }));
out.u3 = { calls: log.length, done: r.done.length, remaining: r.remaining.length, error: r.error && r.error.message,
           html: b.bulkSummaryHtml(r, 'trash') };
// U4
log = [];
r = await b.runInBlocks(ids, finto(log, { stall: true }));
out.u4 = { calls: log.length, error: r.error && r.error.message, remaining: r.remaining.length };
// U5
const fatto = { done: [1, 2], operation_id: 'x', stopped: false, error: null, remaining: [], blocked: [], needs_admin: [],
                not_yours: [], not_available: [], busy: [], already: [] };
out.u5 = { trash: b.bulkSummaryHtml(fatto, 'trash'), restore: b.bulkSummaryHtml(fatto, 'restore'),
           vuoto: b.bulkSummaryHtml({ ...fatto, done: [] }, 'trash') };
// U6
const s = b.createSelection();
s.set(5, true); s.set(3, true); s.set(5, false); s.set('abc', true); s.set(-1, true);
out.u6 = { singoli: s.list() };
s.setMany([10, 11, 12], true);
out.u6.pagina = s.count();
s.selectAll([1, 2, 3, 4, 5]); s.set(2, false); s.set(99, true);
out.u6.tutti = { count: s.count(), list: s.list(), mode: s.mode, has2: s.has(2), has99: s.has(99) };
s.clear();
out.u6.azzerata = { count: s.count(), mode: s.mode };
// U7
out.u7 = ['5200', '5.200', '5 200', "5'200", '520', ''].map((t) => b.typedMatches(t, 5200));
console.log(JSON.stringify(out));
""" % BULK


@pytest.fixture(scope="module")
def esito():
    if not a30_5.NODE:
        pytest.skip("node non disponibile")
    r = subprocess.run([a30_5.NODE, "--input-type=module", "-e", PROVA], capture_output=True, text=True, timeout=60,
                       cwd=ASSETS, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_u1_interrompi_dopo_il_primo_blocco(esito):
    u = esito["u1"]
    assert u["calls"] == 1 and u["stopped"] is True                 # nessun blocco dopo lo stop
    assert u["done"] == 99 and u["blocked"] == 1 and u["remaining"] == 150 and u["op"] == "op-1"
    assert "99 contatti spostati nel Cestino" in u["html"] and "Interrotto: 150 contatti non spostati" in u["html"]
    assert 'data-bulk-group="blocked"' in u["html"] and "Lead aperto" in u["html"]


def test_u2_interrompi_durante_il_secondo_blocco(esito):
    u = esito["u2"]
    assert u == {"calls": 2, "done": 198, "stopped": True, "remaining": 50}


def test_u3_un_errore_a_meta_conserva_il_gia_fatto(esito):
    u = esito["u3"]
    assert u["calls"] == 2 and u["done"] == 99 and u["remaining"] == 150 and u["error"] == "rete giù"
    assert "99 contatti spostati nel Cestino" in u["html"] and "150 contatti non ancora spostati" in u["html"]


def test_u4_un_server_che_non_avanza_si_ferma(esito):
    u = esito["u4"]
    assert u["calls"] == 1 and u["remaining"] == 250 and "non riesce a procedere" in u["error"]


def test_u5_le_comunicazioni_non_si_riattivano_e_lo_si_dice(esito):
    u = esito["u5"]
    assert "automazioni sono state sospese e i messaggi in coda annullati" in u["trash"]
    assert "non le automazioni né i messaggi" in u["trash"]
    assert "restano tali" in u["restore"] and "a mano" in u["restore"]
    assert "data-bulk-comm-note" not in u["vuoto"]                     # nulla spostato: nessuna nota


def test_u6_la_selezione(esito):
    u = esito["u6"]
    assert u["singoli"] == [3]
    assert u["pagina"] == 4
    assert u["tutti"] == {"count": 5, "list": [1, 3, 4, 5, 99], "mode": "all", "has2": False, "has99": True}
    assert u["azzerata"] == {"count": 0, "mode": "ids"}


def test_u7_il_numero_scritto(esito):
    assert esito["u7"] == [True, True, True, True, False, False]


def test_u8_le_rotte_senza_sessione_rispondono_401():
    """L'app VERA, senza sessione e con solo HTTP Basic: le cinque rotte nuove sono 401."""
    import base64

    from fastapi.testclient import TestClient

    import main

    client = TestClient(main.app, raise_server_exceptions=False)
    basic = {"Authorization": "Basic " + base64.b64encode(b"x:y").decode()}
    rotte = [("get", "/api/core/contacts/selection", None),
             ("post", "/api/core/contacts/bulk-trash", {"ids": [1], "reason_code": "duplicate", "confirm": True}),
             ("post", "/api/core/contacts/bulk-restore", {"ids": [1], "confirm": True}),
             ("get", "/api/core/contacts/trash-operations", None),
             ("get", "/api/core/contacts/trash-operations/00000000-0000-0000-0000-000000000000", None)]
    for headers in ({}, basic):
        for metodo, percorso, corpo in rotte:
            r = getattr(client, metodo)(percorso, headers=headers, **({"json": corpo} if corpo else {}))
            assert r.status_code == 401, (percorso, headers, r.status_code)
