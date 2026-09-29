"""Filtri dell'Agenda (piano A30-4 congelato, §3 AgendaFilters, §4.1, §7, §11), ESEGUITI.

Residuo del piano A30-4: la barra dei filtri era nel wireframe approvato e il
backend la accetta gia' (`GET /calendar?agents&types&statuses&show_colleagues`,
`GET /api/appointments?types&statuses`), ma la pagina non la offriva.

`main.js` VERO, router, sessione, pagina Agenda e viste vere dentro lo stub di
DOM di P26-4/P27-7, con il driver di A30-13B.1 (riusato, non copiato). Cosa si
prova:

* con i valori di partenza la richiesta e' IDENTICA a prima (nessun parametro
  in piu'), in Settimana, Giorno e Lista: ogni vista tiene il default del
  server;
* owner/admin/Supreme in acting: filtro Agente con gli agenti che il server
  ha restituito; un `agent` non ha il filtro Agente ma l'interruttore dei
  colleghi "Occupato";
* Tipo e Stato diventano `types` / `statuses`; "Tutti gli stati" li chiede
  tutti; la Lista non riceve mai `agents`;
* "Azzera filtri" compare solo con un filtro attivo e riporta la richiesta
  di partenza;
* i filtri restano cambiando settimana (in memoria, per la sessione);
* vince l'ultima richiesta: una risposta superata non si disegna;
* il click su uno slot vuoto (A30-13B.1) continua a funzionare.

Le funzioni pure (`agenda-model.js`) si provano anche da sole, con node.

Il permesso VERO resta del server: vedi `tests/test_a30_4_filtri_postgres.py`.

Senza node le prove sono SKIPPED (da riportare come BLOCKED), mai passate.
"""
from __future__ import annotations

import json
import subprocess
from urllib.parse import parse_qs, urlsplit

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests import test_a30_13b_quick_booking_ui as a30_13b
# Fixture riusata: la copia degli asset in una cartella temporanea.
from tests.test_a30_5_create_ui import staged  # noqa: F401

NODE = a30_5.NODE

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node non disponibile: prove filtri Agenda NON eseguite (BLOCKED)")

MODEL = a30_5.ASSETS / "agenda" / "agenda-model.js"
TUTTI_GLI_STATI = ["requested", "scheduled", "confirmed", "completed",
                   "cancelled", "no_show", "rescheduled"]
SETTIMANA = "from=2026-09-28T00%3A00%3A00%2B02%3A00&to=2026-10-05T00%3A00%3A00%2B02%3A00"


def _voce(id_, tipo, ora):
    return {"id": id_, "kind": "appointment", "appointment_type": tipo, "type": tipo,
            "status": "scheduled", "start_at": f"2026-09-30T{ora}:00:00+02:00",
            "end_at": f"2026-09-30T{ora}:30:00+02:00", "assigned_user_id": 3,
            "agent_name": "Anna Agente"}


TELEFONATA = {"items": [_voce(801, "call", "10")]}
SOPRALLUOGO = {"items": [_voce(802, "inspection", "11")]}


def _rotte(sessione="agency_owner", *, calendario=None, lenta=None):
    """Rotte del driver A30-13B. `calendario` aggiunge risposte per URL
    esatti (le piu' specifiche prima); `lenta` e' un prefisso di URL la cui
    risposta arriva dopo 300 ms."""
    rt = a30_5._rt()
    voci = [("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
            ("GET", "/api/appointments/agents", [rt.ok(a30_13b.AGENTI)])]
    for prefisso, corpo in (calendario or []):
        voci.append(("GET", prefisso, [rt.ok(corpo)]))
    voci += [("GET", "/api/appointments/calendar?", [rt.ok(a30_13b.CALENDARIO)]),
             ("POST", "/api/appointments/availability/check", [rt.ok(a30_5.LIBERO)]),
             ("GET", "/api/appointments?", [rt.ok({"items": []})])]
    righe = [f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});"
             for m, p, r in voci]
    if lenta:
        # Ritardo SOLO su quell'URL: la risposta arriva dopo quella successiva.
        righe.append(f"""
const __fetchVeloce = globalThis.fetch;
globalThis.fetch = async (url, options) => {{
  if (String(url).startsWith({json.dumps(lenta)})) await new Promise((r) => setTimeout(r, 300));
  return __fetchVeloce(url, options);
}};""")
    return "\n".join(righe)


FILTRI = r"""
const fsel = (nome) => C().querySelector(`[data-filter="${nome}"]`);
async function filtro(nome, valore) {
  const e = fsel(nome);
  if (e.type === 'checkbox') e.checked = valore; else e.value = valore;
  e.dispatch('change');
  await wait();
}
const azzera = () => bottone(C(), 'Azzera filtri');
function statoFiltri() {
  return {
    agente: fsel('agent') ? fsel('agent').querySelectorAll('option').map((o) => o.textContent) : null,
    colleghi: fsel('colleagues') ? fsel('colleagues').checked : null,
    tipo: fsel('type') ? fsel('type').value : null,
    stato: fsel('status') ? fsel('status').value : null,
    azzeraNascosto: azzera() ? azzera().hidden === true : null,
    gruppo: C().querySelector('.agenda-filters').getAttribute('aria-label'),
  };
}
"""


def run(staged, scenario, rotte, hash="#/agenda/settimana/2026-09-28"):  # noqa: F811
    return a30_13b.run(staged, FILTRI + scenario, rotte, hash=hash)


def _get(out, prefisso):
    """I parametri di ogni GET verso `prefisso`, nell'ordine in cui sono partiti."""
    esiti = []
    for c in out["calls"]:
        if c["m"] == "GET" and c["url"].startswith(prefisso):
            q = parse_qs(urlsplit(c["url"]).query, keep_blank_values=True)
            esiti.append({k: v[0] for k, v in q.items()})
    return esiti


def _calendario(out):
    return _get(out, "/api/appointments/calendar")


# ---------------------------------------------------------------------------
# A - VALORI DI PARTENZA: la richiesta non cambia
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sessione", ["agency_owner", "agent"])
@pytest.mark.parametrize("hash_", ["#/agenda/settimana/2026-09-28", "#/agenda/giorno/2026-09-30"])
def test_01_valori_di_partenza_richiesta_calendario_identica(staged, sessione, hash_):
    out = run(staged, "report({ f: statoFiltri() });", _rotte(sessione), hash=hash_)
    richieste = _calendario(out)
    assert len(richieste) == 1
    assert set(richieste[0]) == {"from", "to"}
    assert out["f"]["azzeraNascosto"] is True
    assert out["f"]["gruppo"] == "Filtri"


def test_02_valori_di_partenza_lista_identica(staged):
    out = run(staged, "report({ f: statoFiltri() });", _rotte(),
              hash="#/agenda/lista/2026-09-28")
    lista = _get(out, "/api/appointments?")
    assert len(lista) == 1 and set(lista[0]) == {"from", "to", "limit", "offset"}
    assert lista[0]["limit"] == "200" and lista[0]["offset"] == "0"
    # nella Lista l'API non ha il filtro agente: non lo si offre
    assert out["f"]["agente"] is None and out["f"]["colleghi"] is None


# ---------------------------------------------------------------------------
# B - IL CONTROLLO SECONDO IL RUOLO
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sessione", ["agency_owner", "agency_admin", "supreme"])
def test_03_chi_assegna_ha_il_filtro_agente_con_gli_agenti_del_server(staged, sessione):
    out = run(staged, "report({ f: statoFiltri() });", _rotte(sessione))
    f = out["f"]
    assert f["agente"] == ["Tutti gli agenti", "Anna Agente", "Bruno Collega"]
    assert f["colleghi"] is None
    assert (f["tipo"], f["stato"]) == ("", "")


def test_04_agent_non_ha_il_filtro_agente_ma_i_colleghi_occupato(staged):
    out = run(staged, """
      const prima = statoFiltri();
      await filtro('colleagues', false);
      report({ prima, dopo: statoFiltri() });
    """, _rotte("agent"))
    assert out["prima"]["agente"] is None and out["prima"]["colleghi"] is True
    richieste = _calendario(out)
    assert richieste[-1] == {**richieste[0], "show_colleagues": "false"}
    assert "agents" not in richieste[-1]
    assert out["dopo"]["azzeraNascosto"] is False


# ---------------------------------------------------------------------------
# C - DAL CONTROLLO AL PARAMETRO
# ---------------------------------------------------------------------------

def test_05_owner_agente_tipo_stato_diventano_parametri_e_si_azzerano(staged):
    out = run(staged, """
      await filtro('agent', '4');
      await filtro('type', 'inspection');
      await filtro('status', 'requested');
      const pieno = statoFiltri();
      azzera().dispatch('click');
      await wait();
      report({ pieno, vuoto: statoFiltri() });
    """, _rotte())
    r = _calendario(out)
    assert r[1]["agents"] == "4"
    assert (r[2]["agents"], r[2]["types"]) == ("4", "inspection")
    assert (r[3]["agents"], r[3]["types"], r[3]["statuses"]) == ("4", "inspection", "requested")
    assert out["pieno"]["azzeraNascosto"] is False
    assert set(r[-1]) == {"from", "to"}                     # azzerati: come all'inizio
    v = out["vuoto"]
    assert (v["tipo"], v["stato"], v["azzeraNascosto"]) == ("", "", True)


def test_06_tutti_gli_stati_chiede_anche_annullati_e_spostati(staged):
    out = run(staged, "await filtro('status', 'all'); report();", _rotte())
    assert _calendario(out)[-1]["statuses"].split(",") == TUTTI_GLI_STATI


def test_07_lista_tipo_e_stato_mai_agente(staged):
    out = run(staged, """
      await filtro('type', 'call');
      await filtro('status', 'all');
      report({ f: statoFiltri() });
    """, _rotte(), hash="#/agenda/lista/2026-09-28")
    ultima = _get(out, "/api/appointments?")[-1]
    assert ultima["types"] == "call"
    assert ultima["statuses"].split(",") == TUTTI_GLI_STATI
    assert "agents" not in ultima and "show_colleagues" not in ultima


# ---------------------------------------------------------------------------
# D - MEMORIA, ULTIMA RICHIESTA, SLOT
# ---------------------------------------------------------------------------

def test_08_i_filtri_restano_cambiando_settimana(staged):
    out = run(staged, """
      await filtro('type', 'inspection');
      await filtro('agent', '3');
      bottone(C(), '▶').dispatch('click');
      await window._fire('hashchange');
      await wait();
      report({ f: statoFiltri() });
    """, _rotte())
    ultima = _calendario(out)[-1]
    assert ultima["from"].startswith("2026-10-05")           # la settimana dopo
    assert (ultima["types"], ultima["agents"]) == ("inspection", "3")
    assert out["f"]["tipo"] == "inspection"
    assert out["f"]["azzeraNascosto"] is False


def test_09_vince_l_ultima_richiesta(staged):
    lenta = f"/api/appointments/calendar?{SETTIMANA}&types=call"
    veloce = f"/api/appointments/calendar?{SETTIMANA}&types=inspection"
    out = run(staged, """
      const t = fsel('type');
      t.value = 'call'; t.dispatch('change');          // parte, e la risposta tarda
      await wait();                                    // ... e' gia' in volo
      t.value = 'inspection'; t.dispatch('change');    // parte dopo, risponde prima
      await wait(500);                                 // ora e' arrivata anche la lenta
      report({ card: C().querySelectorAll('.agenda-card').map((c) => c.dataset.appointmentId) });
    """, _rotte(calendario=[(lenta, TELEFONATA), (veloce, SOPRALLUOGO)], lenta=lenta))
    # Il registro delle chiamate segue l'ARRIVO: la telefonata risponde per
    # ultima, eppure a schermo resta il sopralluogo (l'ultima scelta).
    tipi = [r.get("types") for r in _calendario(out)]
    assert tipi[-2:] == ["inspection", "call"]
    assert out["card"] == ["802"]


def test_10_click_su_slot_vuoto_funziona_con_i_filtri_attivi(staged):
    out = run(staged, """
      await filtro('type', 'call');
      await clickSlot('2026-09-30', 15);
      report({ stato: statoDialog() });
    """, _rotte())
    s = out["stato"]
    assert s["open"] is True and (s["data"], s["inizio"]) == ("2026-09-30", "15:00")


# ---------------------------------------------------------------------------
# E - FUNZIONI PURE (agenda-model.js), senza DOM
# ---------------------------------------------------------------------------

def _modello(script: str):
    esito = subprocess.run(
        [NODE, "--input-type=module", "-e",
         f"import * as m from '{MODEL.as_posix()}';\n{script}"],
        capture_output=True, text=True, timeout=30, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


def test_11_funzioni_pure_dei_filtri():
    out = _modello("""
      const D = m.DEFAULT_FILTERS;
      console.log(JSON.stringify({
        partenza: [m.calendarFilterParams(D, { canAssign: true }),
                   m.calendarFilterParams(D, { canAssign: false }), m.listFilterParams(D)],
        sporco: m.normalizeFilters({ agent: '3; DROP', type: 'buyer', status: 'booked',
                                     colleagues: 'no', extra: 1 }),
        agenteNegativo: m.normalizeFilters({ agent: '-4' }).agent,
        agentIgnoraAgente: m.calendarFilterParams({ agent: '4' }, { canAssign: false }),
        ownerIgnoraColleghi: m.calendarFilterParams({ colleagues: false }, { canAssign: true }),
        listaSenzaAgente: m.listFilterParams({ agent: '4', type: 'call', colleagues: false }),
        tutti: m.calendarFilterParams({ status: 'all' }).statuses,
        conta: [m.activeFilterCount(D),
                m.activeFilterCount({ agent: '4', type: 'call' }, { canAssign: true }),
                m.activeFilterCount({ agent: '4', type: 'call' }, { canAssign: true, view: 'list' }),
                m.activeFilterCount({ colleagues: false }, { canAssign: false }),
                m.activeFilterCount({ colleagues: false }, { canAssign: false, view: 'list' })],
        congelato: Object.isFrozen(D),
      }));
    """)
    assert out["partenza"] == [{}, {}, {}]
    assert out["sporco"] == {"agent": "", "type": "", "status": "", "colleagues": True}
    assert out["agenteNegativo"] == ""
    assert out["agentIgnoraAgente"] == {}
    assert out["ownerIgnoraColleghi"] == {}
    assert out["listaSenzaAgente"] == {"types": ["call"]}
    assert out["tutti"] == TUTTI_GLI_STATI
    assert out["conta"] == [0, 2, 1, 1, 0]
    assert out["congelato"] is True
