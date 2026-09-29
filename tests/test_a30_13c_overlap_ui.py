"""A30-13C - piu' di MAX_OVERLAP_COLUMNS appuntamenti sovrapposti: "+N", ESEGUITO.

Prima: oltre 4 sotto-colonne gli eventi di un gruppo finivano IMPILATI
nell'ultima (`overlapLayout`, `Math.min(column, columns - 1)`): a schermo
restava cliccabile solo quello sopra, gli altri erano irraggiungibili dalla
Settimana e dal Giorno.

Ora (`overflowLayout` + `components/agenda/agenda-views.js`):

* un gruppo che sta in 4 colonne si impagina ESATTAMENTE come prima;
* uno che ne chiederebbe di piu' mostra le prime 3 colonne e usa la quarta
  per un pulsante "+N" che apre l'elenco degli appuntamenti nascosti;
* ogni evento e' visibile oppure dietro UN "+N": mai perso (provato anche su
  gruppi casuali);
* Settimana e Giorno si comportano allo stesso modo; la Lista non cambia;
* dal pannello un appuntamento si apre (il suo dettaglio), un "Occupato" di un
  collega resta com'e' nella griglia (nessun dettaglio);
* `overlapLayout` restituisce gli stessi valori di prima (confronto con la
  versione originale su casi casuali).

`main.js` VERO dentro lo stub di DOM di P26-4/P27-7, con il driver di
A30-13B.1 (riusato, non copiato). Senza node: SKIPPED (BLOCKED), mai passate.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests import test_a30_13b_quick_booking_ui as a30_13b
# Fixture riusata: la copia degli asset in una cartella temporanea.
from tests.test_a30_5_create_ui import staged  # noqa: F401

NODE = a30_5.NODE

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node non disponibile: prove A30-13C NON eseguite (BLOCKED)")

MODEL = a30_5.ASSETS / "agenda" / "agenda-model.js"
GIORNO = "2026-09-30"


def _app(id_, agente, inizio="10:00", fine="11:00"):
    return {"id": id_, "kind": "appointment", "appointment_type": "seller_meeting",
            "type": "seller_meeting", "status": "scheduled",
            "start_at": f"{GIORNO}T{inizio}:00+02:00", "end_at": f"{GIORNO}T{fine}:00+02:00",
            "assigned_user_id": id_ % 100, "agent_name": agente}


OCCUPATO = {"kind": "busy", "agent_id": 99, "agent_name": "Collega Occupato", "label": "Occupato",
            "start_at": f"{GIORNO}T10:00:00+02:00", "end_at": f"{GIORNO}T11:00:00+02:00",
            "readonly": True}
# Sei impegni alle 10:00: cinque appuntamenti e un "Occupato" di un collega.
SEI = [_app(901, "Anna"), _app(902, "Bruno"), _app(903, "Carla"),
       _app(904, "Dario"), _app(905, "Elena"), OCCUPATO]
QUATTRO = SEI[:4]


def _rotte(items, sessione="agency_owner"):
    rt = a30_5._rt()
    voci = [("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
            ("GET", "/api/appointments/agents", [rt.ok(a30_13b.AGENTI)]),
            ("GET", "/api/appointments/calendar?", [rt.ok({"items": items})]),
            ("POST", "/api/appointments/availability/check", [rt.ok(a30_5.LIBERO)]),
            ("GET", "/api/appointments?",
             [rt.ok({"items": [i for i in items if i["kind"] == "appointment"]})])]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});"
                     for m, p, r in voci)


OVERLAP = r"""
const altri = () => C().querySelectorAll('.agenda-more');
const pannello = () => C().querySelector('dialog.agenda-overflow');
function griglia() {
  const col = colonna('2026-09-30');
  return {
    card: col.querySelectorAll('.agenda-card').map((c) => c.dataset.appointmentId || c.textContent),
    cliccabili: col.querySelectorAll('.agenda-card').filter((c) => c.tagName === 'BUTTON').length,
    altri: altri().map((b) => ({ tag: b.tagName, testo: b.textContent, n: b.dataset.overflowCount,
                                 label: b.getAttribute('aria-label'), left: b.style.left,
                                 width: b.style.width, top: b.style.top })),
    sinistre: col.querySelectorAll('.agenda-card').map((c) => c.style.left),
  };
}
function statoPannello() {
  const p = pannello();
  if (!p) return null;
  return {
    aperto: p._open === true,
    label: p.getAttribute('aria-label'),
    voci: p.querySelectorAll('.agenda-card').map((c) => ({
      tag: c.tagName, id: c.dataset.appointmentId || null, testo: c.visibleText() })),
    testo: p.visibleText(),
  };
}
"""


def run(staged, scenario, rotte, hash=f"#/agenda/settimana/2026-09-28"):  # noqa: F811
    return a30_13b.run(staged, OVERLAP + scenario, rotte, hash=hash)


# ---------------------------------------------------------------------------
# A - FUNZIONI PURE
# ---------------------------------------------------------------------------

# La versione di `overlapLayout` PRIMA di A30-13C, copiata com'era: il
# confronto prova che la sua uscita non e' cambiata.
OVERLAP_ORIGINALE = r"""
function overlapOriginale(items) {
  const ordinati = (items || []).map((it, i) => ({
    i, s: Date.parse(it.start_at), e: Date.parse(it.end_at),
  })).sort((a, b) => a.s - b.s || b.e - a.e || a.i - b.i);
  const esito = new Array((items || []).length);
  let gruppo = [];
  let fineGruppo = -Infinity;
  const chiudi = () => {
    const colonne = Math.min(Math.max(1, ...gruppo.map((g) => g.column + 1)), m.MAX_OVERLAP_COLUMNS);
    for (const g of gruppo) {
      esito[g.i] = { column: Math.min(g.column, colonne - 1), columns: colonne };
    }
    gruppo = [];
  };
  let fineColonne = [];
  for (const it of ordinati) {
    if (gruppo.length && it.s >= fineGruppo) {
      chiudi();
      fineColonne = [];
    }
    let colonna = fineColonne.findIndex((fine) => fine <= it.s);
    if (colonna === -1) {
      colonna = fineColonne.length;
      fineColonne.push(it.e);
    } else {
      fineColonne[colonna] = it.e;
    }
    gruppo.push({ i: it.i, column: colonna });
    fineGruppo = Math.max(fineGruppo === -Infinity ? it.e : fineGruppo, it.e);
  }
  if (gruppo.length) chiudi();
  return esito;
}
// Generatore deterministico (nessun Math.random: il test si ripete uguale).
let seme = 13;
const caso = (n) => { seme = (seme * 1103515245 + 12345) % 2147483648; return seme % n; };
function giornata() {
  const n = 1 + caso(14);
  return Array.from({ length: n }, () => {
    const inizio = 8 * 60 + caso(10) * 30;
    const durata = 15 * (1 + caso(8));
    const iso = (min) => `2026-09-30T${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}:00+02:00`;
    return { start_at: iso(inizio), end_at: iso(inizio + durata) };
  });
}
"""


def _modello(script: str):
    esito = subprocess.run(
        [NODE, "--input-type=module", "-e",
         f"import * as m from '{MODEL.as_posix()}';\n{OVERLAP_ORIGINALE}\n{script}"],
        capture_output=True, text=True, timeout=60,
        env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


def test_01_fino_a_quattro_colonne_nulla_cambia():
    out = _modello("""
      const t = (h, m2, h2, m3) => ({ start_at: `2026-09-30T${h}:${m2}:00+02:00`,
                                      end_at: `2026-09-30T${h2}:${m3}:00+02:00` });
      const casi = [
        [t('10','00','11','00'), t('10','30','11','30'), t('12','00','13','00')],
        [t('10','00','11','00'), t('10','00','11','00'), t('10','00','11','00'), t('10','00','11','00')],
        [],
      ];
      console.log(JSON.stringify(casi.map((c) => {
        const o = m.overflowLayout(c);
        return { uguale: JSON.stringify(o.placement) === JSON.stringify(m.overlapLayout(c)),
                 overflow: o.overflow.length };
      })));
    """)
    assert out == [{"uguale": True, "overflow": 0}] * 3


def test_02_cinque_simultanei_tre_colonne_e_un_piu_due():
    out = _modello("""
      const c = Array.from({ length: 5 }, () => ({ start_at: '2026-09-30T10:00:00+02:00',
                                                   end_at: '2026-09-30T11:00:00+02:00' }));
      console.log(JSON.stringify(m.overflowLayout(c)));
    """)
    assert out["placement"][:3] == [{"column": 0, "columns": 4}, {"column": 1, "columns": 4},
                                    {"column": 2, "columns": 4}]
    assert out["placement"][3:] == [None, None]
    assert out["overflow"] == [{"indices": [3, 4], "column": 3, "columns": 4,
                                "start_at": "2026-09-30T08:00:00.000Z",
                                "end_at": "2026-09-30T09:00:00.000Z"}]


def test_03_nessun_evento_perso_e_overlap_layout_invariato_su_casi_casuali():
    out = _modello("""
      let perso = 0, doppio = 0, diverso = 0, conOverflow = 0, fuoriCorsia = 0;
      for (let k = 0; k < 400; k += 1) {
        const g = giornata();
        const o = m.overflowLayout(g);
        const visti = new Array(g.length).fill(0);
        o.placement.forEach((p, i) => { if (p) { visti[i] += 1; if (p.column >= p.columns) fuoriCorsia += 1; } });
        for (const gr of o.overflow) { conOverflow += 1; for (const i of gr.indices) visti[i] += 1; }
        perso += visti.filter((v) => v === 0).length;
        doppio += visti.filter((v) => v > 1).length;
        if (JSON.stringify(m.overlapLayout(g)) !== JSON.stringify(overlapOriginale(g))) diverso += 1;
      }
      console.log(JSON.stringify({ perso, doppio, diverso, conOverflow, fuoriCorsia }));
    """)
    assert (out["perso"], out["doppio"], out["diverso"], out["fuoriCorsia"]) == (0, 0, 0, 0)
    assert out["conOverflow"] > 20            # i casi casuali esercitano davvero il "+N"


# ---------------------------------------------------------------------------
# B - SETTIMANA E GIORNO
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hash_", ["#/agenda/settimana/2026-09-28", f"#/agenda/giorno/{GIORNO}"])
def test_04_settimana_e_giorno_tre_card_e_un_piu_tre(staged, hash_):
    out = run(staged, "report({ g: griglia() });", _rotte(SEI), hash=hash_)
    g = out["g"]
    assert g["card"] == ["901", "902", "903"]
    assert g["cliccabili"] == 3
    assert g["sinistre"] == ["calc(0% + 2px)", "calc(25% + 2px)", "calc(50% + 2px)"]
    assert g["altri"] == [{"tag": "BUTTON", "testo": "+3", "n": "3",
                           "label": "Altri 3 appuntamenti, 10:00–11:00",
                           "left": "calc(75% + 2px)", "width": "calc(25% - 4px)",
                           "top": "640px"}]


def test_05_piu_n_apre_l_elenco_dei_nascosti(staged):
    out = run(staged, """
      altri()[0].dispatch('click');
      await wait();
      report({ p: statoPannello() });
    """, _rotte(SEI))
    p = out["p"]
    assert p["aperto"] is True
    assert p["label"].startswith("Altri 3 appuntamenti, ") and p["label"].endswith("10:00–11:00")
    assert [(v["tag"], v["id"]) for v in p["voci"]] == [
        ("BUTTON", "904"), ("BUTTON", "905"), ("DIV", None)]
    assert "Collega Occupato" in p["voci"][2]["testo"] and "Occupato" in p["voci"][2]["testo"]


def test_06_un_appuntamento_del_pannello_apre_il_suo_dettaglio(staged):
    out = run(staged, """
      altri()[0].dispatch('click');
      await wait();
      pannello().querySelectorAll('.agenda-card')[1].dispatch('click');
      await wait();
      report({ p: statoPannello(),
               dettaglio: __calls.filter((c) => c.url === '/api/appointments/905').length });
    """, _rotte(SEI))
    assert out["p"]["aperto"] is False
    assert out["dettaglio"] == 1


def test_07_chiudi_chiude_senza_aprire_nulla(staged):
    out = run(staged, """
      altri()[0].dispatch('click');
      await wait();
      bottone(pannello(), 'Chiudi').dispatch('click');
      await wait();
      report({ p: statoPannello(),
               dettagli: __calls.filter((c) => /^\\/api\\/appointments\\/\\d+$/.test(c.url)).length });
    """, _rotte(SEI))
    assert out["p"]["aperto"] is False and out["dettagli"] == 0


def test_08_ogni_appuntamento_e_raggiungibile(staged):
    out = run(staged, """
      const visibili = griglia().card;
      altri()[0].dispatch('click');
      await wait();
      report({ visibili, nascosti: statoPannello().voci.map((v) => v.id).filter(Boolean) });
    """, _rotte(SEI))
    tutti = sorted(str(i["id"]) for i in SEI if i["kind"] == "appointment")
    assert sorted(out["visibili"] + out["nascosti"]) == tutti


def test_09_quattro_sovrapposti_come_prima_nessun_piu_n(staged):
    out = run(staged, "report({ g: griglia() });", _rotte(QUATTRO))
    assert out["g"]["card"] == ["901", "902", "903", "904"]
    assert out["g"]["altri"] == []
    assert out["g"]["sinistre"][3] == "calc(75% + 2px)"


def test_10_lista_invariata(staged):
    out = run(staged, """
      report({ card: C().querySelectorAll('.agenda-card').map((c) => c.dataset.appointmentId),
               altri: altri().length });
    """, _rotte(SEI), hash="#/agenda/lista/2026-09-28")
    assert out["card"] == ["901", "902", "903", "904", "905"]
    assert out["altri"] == 0


def test_11_click_su_slot_vuoto_funziona_ancora(staged):
    out = run(staged, """
      await clickSlot('2026-09-30', 15);
      report({ stato: statoDialog(), pannello: statoPannello() });
    """, _rotte(SEI))
    assert out["stato"]["open"] is True
    assert (out["stato"]["data"], out["stato"]["inizio"]) == (GIORNO, "15:00")
    assert out["pannello"] is None                     # nessun "+N" aperto per sbaglio
