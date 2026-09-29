"""A30-13B.1 - click su uno slot vuoto -> quick booking, ESEGUITO.

`main.js` VERO, router, sessione, pagina Agenda, viste e dialog veri, dentro
lo stub di DOM di P26-4/P27-7 con il `fetch` instradato di A30-5 (riusati,
non copiati). Cosa si prova:

* un click (o Invio) su un'ora vuota della SETTIMANA o del GIORNO apre
  "Nuovo appuntamento" con giorno e ora dello slot; la Lista non ha slot;
* "+ Nuovo appuntamento" resta com'era (09:00 del giorno della pagina);
* un click su una card apre il suo pannello, non la creazione;
* un `agent` non ha un selettore: solo "Io", bloccato, e il POST porta il suo
  id; titolare, amministratore e Supreme "acting" vedono gli agenti attivi
  che il server ha restituito, piu' "Nessuno" (richiesta);
* l'invio passa da `POST /api/appointments/availability/check` e poi
  `POST /api/appointments` - l'API Agenda esistente, nessun'altra, nessun
  Google;
* un 409 del server tiene aperto il dialog e non fa comparire nulla.

Il permesso VERO e' del server: vedi
`tests/test_a30_13b_create_permissions_postgres.py`. Qui si prova solo che la
UI non offre cio' che il server rifiuterebbe e manda cio' che deve.

Senza node le prove sono SKIPPED (da riportare come BLOCKED), mai passate.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests import test_a30_5_create_ui as a30_5
# Fixture riusata: la copia degli asset in una cartella temporanea.
from tests.test_a30_5_create_ui import staged  # noqa: F401

NODE = a30_5.NODE

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node non disponibile: prove A30-13B.1 NON eseguite (BLOCKED)")


# Il `value` di una <select> nello stub di P27-7 legge solo gli ATTRIBUTI delle
# opzioni. Il dialog le crea come fa un browser, per PROPRIETA'
# (`o.value = ...`, `o.selected = true`), e un browser ne legge le proprieta'.
# I test A30-5 scrivono `select.value` a mano e non se ne accorgono; qui si
# prova proprio cio' che il form manda SENZA che nessuno tocchi il campo,
# quindi - SOLO in questo driver - la <select> legge anche le proprieta'. Il
# sostituto e' controllato: se lo stub cambia, il test lo dice.
_SEL_OLD = """      const scelta = opzioni.find((o) => o.getAttribute('selected') !== null) || opzioni[0];
      return scelta ? (scelta.getAttribute('value') ?? '') : '';"""
_SEL_NEW = """      const scelta = opzioni.find((o) => o.selected === true || o.getAttribute('selected') !== null)
        || opzioni[0];
      if (!scelta) return '';
      return scelta._value !== undefined ? scelta._value : (scelta.getAttribute('value') ?? '');"""


def _extra_dom():
    extra = a30_5._rt().EXTRA_DOM
    assert extra.count(_SEL_OLD) == 1, "lo stub <select> di P27-7 e' cambiato"
    return extra.replace(_SEL_OLD, _SEL_NEW) + GRIGLIA


# La griglia SETTIMANA/GIORNO usa due API del browser che nessun test di runtime
# aveva ancora chiesto allo stub (A30-4 prova la griglia con le funzioni pure
# e la pagina con la Lista): `style.setProperty` (la variabile CSS del numero di
# giorni) e `requestAnimationFrame` (lo scroll iniziale alle 08:00). Qui le
# minime, per disegnarla davvero.
GRIGLIA = r"""
const __creaElemento = document.createElement;
document.createElement = (tag) => {
  const nodo = __creaElemento(tag);
  nodo.style.setProperty = (nome, valore) => { nodo.style[nome] = String(valore); };
  return nodo;
};
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
"""


QB_HELPERS = r"""
const colonna = (giorno) => C().querySelectorAll('.agenda-day-column').find((c) => c.dataset.day === giorno);
const slot = (giorno, ora) => colonna(giorno).querySelectorAll('.agenda-hour-slot')[ora];
async function clickSlot(giorno, ora) { slot(giorno, ora).dispatch('click'); await wait(); }
function statoDialog() {
  const agente = f('[data-field="agent"]');
  return {
    open: !!(dlg() && dlg()._open),
    titolo: dlg() ? dlg().visibleText() : '',
    data: f('[data-field="date"]').value,
    inizio: f('[data-field="start"]').value,
    fine: f('[data-field="end"]').value,
    agenteDisabilitato: agente.disabled === true,
    agenteValore: agente.value,
    agenteOpzioni: agente.querySelectorAll('option').map((o) => o.textContent),
  };
}
"""

AGENTI = {"items": [{"id": 3, "name": "Anna Agente", "role": "agent", "is_me": True},
                    {"id": 4, "name": "Bruno Collega", "role": "agent", "is_me": False}]}
# Una card gia' in calendario, martedi' 29/09 10:00-11:00, di Anna.
CALENDARIO = {"items": [{"id": 700, "kind": "appointment", "appointment_type": "seller_meeting",
                         "status": "scheduled", "start_at": "2026-09-29T10:00:00+02:00",
                         "end_at": "2026-09-29T11:00:00+02:00", "assigned_user_id": 3,
                         "agent_name": "Anna Agente"}]}


def _sessione(tipo):
    rt = a30_5._rt()
    if tipo == "agent":
        return rt.TENANT                                   # role: agent, user 3
    if tipo in ("agency_owner", "agency_admin"):
        return {**rt.TENANT, "role": tipo}
    if tipo == "supreme":
        return {**rt.PLATFORM_ADMIN, "agency_id": 7, "agency_name": "Agenzia A",
                "acting": {"agency_id": 7, "agency_name": "Agenzia A",
                           "entered_at": "2026-09-28T08:00:00Z"},
                "home_agency_id": None, "home_agency_name": None}
    raise ValueError(tipo)


def _rotte(sessione="agent", *, check=(a30_5.LIBERO,), create=None):
    rt = a30_5._rt()
    create = create or ({"status": 201, "body": a30_5._creato(
        start_at="2026-09-30T15:00:00+02:00", end_at="2026-09-30T16:00:00+02:00")},)
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(_sessione(sessione))]),
        ("GET", "/api/appointments/agents", [rt.ok(AGENTI)]),
        ("GET", "/api/appointments/calendar?", [rt.ok(CALENDARIO)]),
        ("POST", "/api/appointments/availability/check", [rt.ok(c) for c in check]),
        ("POST", "/api/appointments", list(create)),
        ("GET", "/api/appointments?", [rt.ok({"items": []})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});"
                     for m, p, r in voci)


def run(staged: Path, scenario: str, rotte: str,  # noqa: F811
        hash: str = "#/agenda/settimana/2026-09-28") -> dict:
    rt = a30_5._rt()
    driver = staged.parent / "driver-a30-13b.mjs"
    driver.write_text(
        a30_5._dom() + rt.FETCH + _extra_dom() + a30_5.IS_CONNECTED + a30_5.ROUTED_FETCH
        + f"\n{rotte}\n"
        + f"window.location.hash = '{hash}';\n"
        + f"await import('{(staged / 'main.js').as_posix()}');\n"
        + "await __settle(40);\n"
        + a30_5.HELPERS + QB_HELPERS + scenario + "\n",
        encoding="utf-8")
    esito = subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=60,
                           cwd=staged.parent, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    if esito.returncode != 0:
        raise AssertionError(f"driver node fallito:\n{esito.stderr[-3000:]}\n{esito.stdout[-1500:]}")
    return json.loads(esito.stdout.strip().splitlines()[-1])


def _post(out, url="/api/appointments"):
    return [c for c in out["calls"] if c["m"] == "POST" and c["url"] == url]


# ---------------------------------------------------------------------------
# A - CLICK SU UNO SLOT VUOTO
# ---------------------------------------------------------------------------

def test_01_settimana_click_su_slot_vuoto_apre_nuovo_appuntamento_con_giorno_e_ora(staged):
    out = run(staged, """
      const prima = !!(dlg() && dlg()._open);
      await clickSlot('2026-09-30', 15);
      report({ prima, stato: statoDialog() });
    """, _rotte())
    s = out["stato"]
    assert out["prima"] is False and s["open"] is True
    assert "Nuovo appuntamento" in s["titolo"]
    assert (s["data"], s["inizio"], s["fine"]) == ("2026-09-30", "15:00", "16:00")
    assert out["duration"] == "Durata: 1 h"


def test_02_giorno_click_su_slot_vuoto_precompila_quel_giorno_e_quell_ora(staged):
    out = run(staged, """
      await clickSlot('2026-10-02', 8);
      report({ stato: statoDialog() });
    """, _rotte(), hash="#/agenda/giorno/2026-10-02")
    s = out["stato"]
    assert s["open"] is True
    assert (s["data"], s["inizio"], s["fine"]) == ("2026-10-02", "08:00", "09:00")


def test_03_slot_raggiungibile_da_tastiera(staged):
    out = run(staged, """
      const s = slot('2026-09-28', 7);
      const attr = { role: s.getAttribute('role'), tab: s.tabIndex, label: s.getAttribute('aria-label') };
      s.dispatch('keydown', { key: 'Tab' });            // un tasto qualunque: niente
      await wait();
      const dopoTab = !!(dlg() && dlg()._open);
      s.dispatch('keydown', { key: 'Enter' });
      await wait();
      report({ attr, dopoTab, stato: statoDialog() });
    """, _rotte())
    assert out["attr"] == {"role": "button", "tab": 0, "label": "Nuovo appuntamento alle 07:00"}
    assert out["dopoTab"] is False
    assert out["stato"]["open"] is True
    assert (out["stato"]["data"], out["stato"]["inizio"]) == ("2026-09-28", "07:00")


def test_04_le_24_ore_di_ogni_giorno_sono_slot_e_la_lista_non_ne_ha(staged):
    settimana = run(staged, """
      report({ giorni: C().querySelectorAll('.agenda-day-column').length,
               slot: C().querySelectorAll('.agenda-hour-slot-clickable').length });
    """, _rotte())
    assert (settimana["giorni"], settimana["slot"]) == (7, 7 * 24)
    lista = run(staged, """
      report({ slot: C().querySelectorAll('.agenda-hour-slot').length });
    """, _rotte(), hash="#/agenda/lista/2026-09-28")
    assert lista["slot"] == 0


def test_05_click_su_una_card_apre_il_pannello_non_la_creazione(staged):
    out = run(staged, """
      colonna('2026-09-29').querySelector('.agenda-card').dispatch('click');
      await wait();
      report({ creazioneAperta: !!(dlg() && dlg()._open),
               dettaglio: __calls.some((c) => c.url === '/api/appointments/700') });
    """, _rotte())
    assert out["creazioneAperta"] is False
    assert out["dettaglio"] is True


def test_06_il_pulsante_nuovo_appuntamento_resta_invariato(staged):
    out = run(staged, """
      await apriNuovo();
      report({ stato: statoDialog() });
    """, _rotte(), hash="#/agenda/settimana/2026-09-30")
    s = out["stato"]
    assert s["open"] is True
    assert (s["data"], s["inizio"], s["fine"]) == ("2026-09-30", "09:00", "10:00")


# ---------------------------------------------------------------------------
# B - IL CAMPO AGENTE SECONDO IL RUOLO
# ---------------------------------------------------------------------------

def test_07_agent_vede_solo_io_bloccato_senza_nessuno(staged):
    out = run(staged, """
      await clickSlot('2026-09-30', 15);
      report({ stato: statoDialog() });
    """, _rotte("agent"))
    s = out["stato"]
    assert s["agenteDisabilitato"] is True
    assert s["agenteOpzioni"] == ["Io — Anna Agente"]
    assert s["agenteValore"] == "3"
    assert "Bruno Collega" not in s["titolo"] and "Nessuno" not in s["titolo"]


@pytest.mark.parametrize("ruolo", ["agency_owner", "agency_admin", "supreme"])
def test_08_owner_admin_e_supreme_in_acting_vedono_gli_agenti_attivi(staged, ruolo):
    out = run(staged, """
      await clickSlot('2026-09-30', 15);
      report({ stato: statoDialog() });
    """, _rotte(ruolo))
    s = out["stato"]
    assert s["open"] is True
    assert s["agenteDisabilitato"] is False
    assert s["agenteOpzioni"] == ["Nessuno: salva come richiesta", "Anna Agente", "Bruno Collega"]


def test_09_agent_invia_con_se_stesso_via_api_agenda(staged):
    out = run(staged, """
      await clickSlot('2026-09-30', 15);
      await conferma();
      report();
    """, _rotte("agent"))
    controlli = _post(out, "/api/appointments/availability/check")
    scritture = _post(out)
    assert len(controlli) == 1 and len(scritture) == 1
    assert controlli[0]["body"] == {"assigned_user_id": 3,
                                    "start_at": "2026-09-30T15:00:00+02:00",
                                    "end_at": "2026-09-30T16:00:00+02:00"}
    corpo = scritture[0]["body"]
    assert corpo["assigned_user_id"] == 3 and corpo["status"] == "scheduled"
    assert (corpo["start_at"], corpo["end_at"]) == ("2026-09-30T15:00:00+02:00",
                                                    "2026-09-30T16:00:00+02:00")
    assert "agency_id" not in corpo and "created_by_user_id" not in corpo
    assert a30_5.UUID4.match(corpo["client_request_id"])
    # solo l'API Agenda: nessun Google, nessun altro endpoint di scrittura
    assert not any("google" in c["url"] for c in out["calls"] if c["m"] != "GET")
    assert {c["url"] for c in out["calls"] if c["m"] == "POST"} == {
        "/api/appointments/availability/check", "/api/appointments"}
    assert out["open"] is False


def test_10_owner_sceglie_un_collega_e_lo_invia(staged):
    out = run(staged, """
      await clickSlot('2026-09-30', 15);
      f('[data-field="agent"]').value = '4'; f('[data-field="agent"]').dispatch('change');
      await conferma();
      report();
    """, _rotte("agency_owner", create=({"status": 201, "body": a30_5._creato(
        assigned_user_id=4, start_at="2026-09-30T15:00:00+02:00",
        end_at="2026-09-30T16:00:00+02:00")},)))
    assert _post(out)[0]["body"]["assigned_user_id"] == 4


# ---------------------------------------------------------------------------
# C - CONFLITTO: nessun appuntamento fantasma
# ---------------------------------------------------------------------------

def test_11_conflitto_409_resta_aperto_e_non_mostra_nulla_di_creato(staged):
    conflitto = {"status": 409, "body": {"detail": "Orario non disponibile per l'agente",
                                         "code": "APPOINTMENT_CONFLICT",
                                         "conflicts": a30_5.OCCUPATO["conflicts"],
                                         "alternatives": a30_5.OCCUPATO["alternatives"]}}
    out = run(staged, """
      const letture = () => __calls.filter((c) => (c.options.method || 'GET') === 'GET'
        && c.url.startsWith('/api/appointments/calendar')).length;
      const prima = letture();
      await clickSlot('2026-09-30', 15);
      await conferma();
      report({ rilette: letture() - prima });
    """, _rotte("agent", create=(conflitto,)))
    assert out["open"] is True
    assert out["error"] == "Orario non disponibile per l'agente."
    assert out["status"].startswith("ORARIO NON DISPONIBILE") and len(out["alt"]) == 2
    assert len(_post(out)) == 1
    assert out["rilette"] == 0                         # la griglia non si ridisegna
    assert "Appuntamento creato" not in out["content"]


def test_12_orario_occupato_alla_verifica_non_scrive(staged):
    out = run(staged, """
      await clickSlot('2026-09-30', 15);
      await conferma();
      report();
    """, _rotte("agent", check=(a30_5.OCCUPATO,)))
    assert _post(out) == []
    assert out["open"] is True
