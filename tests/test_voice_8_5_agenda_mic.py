"""STIMA Voice V8-5 - il microfono nel dialog «Nuovo appuntamento» (stub di DOM).

  01  senza il permesso del server (stato spento o non letto) il dialog non ha microfono
  02  con il permesso ma senza supporto del browser: pulsante disattivo e messaggio chiaro
  03  dettatura -> POST /api/voice/interpret -> campi precompilati (tipo, data, ora,
      cliente, incarico, agente, note) -> l'operatore salva dal dialog: UNA POST
      /api/appointments con quei dati; nessun comando vocale, nessun salvataggio automatico
  04  una domanda del server resta a schermo e non salva nulla; i campi restano modificabili
  05  microfono negato dal browser: messaggio chiaro, campi a mano
  06  piu' contatti possibili: nessuna scelta automatica, lo si dice
"""
from __future__ import annotations

import json
import subprocess

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests.test_a30_5_create_ui import HELPERS, IS_CONNECTED, NODE, ROUTED_FETCH, _dom, _rotte, _rt, staged  # noqa: F401

node = pytest.mark.skipif(NODE is None, reason="node non disponibile: prove UI non eseguite (BLOCKED, non PASS)")

ACCESA = {"status": 200, "body": {"enabled": True, "speech_input": True}}
SPENTA = {"status": 200, "body": {"enabled": True, "speech_input": False}}
PROPRIETARIO = {"status": 200, "body": {**_rt().TENANT, "role": "agency_owner"}}

CAPITO = {"status": 200, "body": {"steps": [{
    "appointment_type": "buyer_visit", "type_label": "Visita immobile", "needs_mandate": True,
    "start_at": "2026-10-01T16:00:00+02:00", "end_at": "2026-10-01T17:00:00+02:00", "duration_minutes": 60,
    "notes": "porta i documenti", "location_text": None,
    "contact": {"id": 41, "label": "Mario Rossi"}, "contact_candidates": [{"id": 41, "label": "Mario Rossi"}],
    "property": {"id": 77, "label": "Via Roma 1, Giulianova (commerciale)"},
    "property_candidates": [{"id": 77, "label": "Via Roma 1, Giulianova (commerciale)"}],
    "agent": {"id": 3, "label": "Anna Agente"}, "actor": "Anna Agente", "decision": "ask", "questions": [], "blocked": []}],
    "clarifications": []}}
DOMANDA = {"status": 200, "body": {"steps": [{
    "appointment_type": "buyer_visit", "type_label": "Visita immobile", "needs_mandate": True,
    "start_at": "2026-10-01T16:00:00+02:00", "end_at": "2026-10-01T17:00:00+02:00", "duration_minutes": 60,
    "notes": None, "location_text": None, "contact": {"id": 41, "label": "Mario Rossi"}, "contact_candidates": [{"id": 41, "label": "Mario Rossi"}],
    "property": None, "property_candidates": [], "agent": None, "actor": "Anna Agente", "decision": "ask",
    "questions": ["Quale immobile deve visitare Mario Rossi?"], "blocked": []}], "clarifications": []}}
OMONIMI = {"status": 200, "body": {"steps": [{
    "appointment_type": "inspection", "type_label": "Sopralluogo", "needs_mandate": False,
    "start_at": "2026-10-01T16:00:00+02:00", "end_at": "2026-10-01T17:00:00+02:00", "duration_minutes": 60,
    "notes": None, "location_text": None, "contact": None,
    "contact_candidates": [{"id": 41, "label": "Mario Rossi"}, {"id": 42, "label": "Mario Rossi"}],
    "property": None, "property_candidates": [], "agent": None, "actor": "Anna Agente", "decision": "ask",
    "questions": [], "blocked": []}], "clarifications": []}}

# Il finto riconoscitore del browser (lo stesso delle prove della pagina Voce, Fase 6).
FAKE_REC = r"""
class FakeRec {
  constructor() { FakeRec.made.push(this); }
  start() { this.started = true; }
  stop() { this.stopped = true; if (this.onend) this.onend(); }
  abort() { this.aborted = true; if (this.onend) this.onend(); }
  say(text, isFinal = true) {
    const r = [{ transcript: text }]; r.isFinal = isFinal;
    this.onresult({ resultIndex: 0, results: [r] });
  }
}
FakeRec.made = [];
globalThis.FakeRec = FakeRec;
"""
PRELUDIO = FAKE_REC + r"""
window.webkitSpeechRecognition = FakeRec;
"""


def _run(staged, scenario, rotte, *, con_microfono=True, hash="#/agenda/lista/2026-09-26"):  # noqa: F811
    rt = _rt()
    driver = staged.parent / "driver-voice-8-5.mjs"
    driver.write_text(
        _dom() + rt.FETCH + rt.EXTRA_DOM + IS_CONNECTED + ROUTED_FETCH
        + (PRELUDIO if con_microfono else FAKE_REC)
        + f"\n{rotte}\n"
        + f"window.location.hash = '{hash}';\n"
        + f"await import('{(staged / 'main.js').as_posix()}');\n"
        + "await __settle(40);\n"
        + HELPERS + scenario + "\n", encoding="utf-8")
    esito = subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=60,
                           cwd=staged.parent, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    if esito.returncode != 0:
        raise AssertionError(f"driver node fallito:\n{esito.stderr[-3000:]}\n{esito.stdout[-1500:]}")
    return json.loads(esito.stdout.strip().splitlines()[-1])


def _route(m, p, *r):
    return f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(list(r))});\n"


SCENA_STATO = r"""
  await apriNuovo();
  report({ blocco: !!f('[data-voice-block]'), bottone: f('[data-voice-dictate]') ? { disabled: !!f('[data-voice-dictate]').disabled } : null,
           stato: f('[data-voice-dictation-status]') ? f('[data-voice-dictation-status]').visibleText() : null });
"""


@node
def test_01_senza_permesso_nessun_microfono(staged):  # noqa: F811
    out = _run(staged, SCENA_STATO, _rotte())                                             # stato non letto: 500 -> spento
    assert out["blocco"] is False and out["bottone"] is None
    out = _run(staged, SCENA_STATO, _route("GET", "/api/voice/status", SPENTA) + _rotte())
    assert out["blocco"] is False


@node
def test_02_browser_senza_riconoscimento_messaggio_chiaro(staged):  # noqa: F811
    out = _run(staged, SCENA_STATO, _route("GET", "/api/voice/status", ACCESA) + _rotte(), con_microfono=False)
    assert out["blocco"] is True and out["bottone"] == {"disabled": True}
    assert "non è disponibile in questo browser" in out["stato"] and "compila i campi a mano" in out["stato"]


@node
def test_03_dettatura_precompila_e_si_salva_dal_dialog(staged):  # noqa: F811
    scena = r"""
      await apriNuovo();
      f('[data-voice-dictate]').dispatch('click'); await wait();
      const ascolto = f('[data-voice-dictation-status]').visibleText();
      FakeRec.made[0].say('Mario Rossi vuole vedere casa in via Roma 1 il primo ottobre alle 16');
      FakeRec.made[0].stop(); await wait();
      const campi = { tipo: f('[data-field="type"]').value, data: f('[data-field="date"]').value,
        inizio: f('[data-field="start"]').value, fine: f('[data-field="end"]').value, agente: f('[data-field="agent"]').value,
        cliente: f('.contact-picker-selected').visibleText(), incarico: f('[data-property-selected]').visibleText(),
        note: f('[data-field="notes"]').value, stato: f('[data-voice-dictation-status]').visibleText(),
        trascritto: f('[data-voice-transcript]').visibleText(), durata: f('[data-duration-text]').textContent };
      const primaDiSalvare = chiamate().filter((c) => c.m === 'POST').map((c) => c.url);
      await conferma();
      report({ ascolto, campi, primaDiSalvare });
    """
    creato = {"status": 201, "body": a30_5._creato(appointment_type="buyer_visit", property_id=77,
                                                  start_at="2026-10-01T16:00:00+02:00", end_at="2026-10-01T17:00:00+02:00")}
    out = _run(staged, scena, _route("GET", "/api/operator-auth/me", PROPRIETARIO)
               + _route("GET", "/api/voice/status", ACCESA) + _route("POST", "/api/voice/interpret", CAPITO)
               + _rotte(create=(creato,)))
    assert "In ascolto" in out["ascolto"]
    c = out["campi"]
    assert c["tipo"] == "buyer_visit" and c["data"] == "2026-10-01" and c["inizio"] == "16:00" and c["fine"] == "17:00"
    assert c["agente"] == "3" and "Mario Rossi" in c["cliente"] and "Via Roma 1" in c["incarico"]
    assert c["note"] == "porta i documenti" and c["durata"] == "Durata: 1 h"
    assert c["stato"].startswith("Ho capito: Visita immobile. Controlla i campi e salva.") and "vedere casa" in c["trascritto"]
    assert out["primaDiSalvare"] == ["/api/voice/interpret"]                                 # nessun comando, nessun salvataggio
    post = [cm for cm in out["calls"] if cm["m"] == "POST" and cm["url"] == "/api/appointments"]
    assert len(post) == 1
    corpo = post[0]["body"]
    assert corpo["appointment_type"] == "buyer_visit" and corpo["contact_id"] == 41 and corpo["property_id"] == 77
    assert corpo["assigned_user_id"] == 3 and corpo["notes"] == "porta i documenti"
    assert corpo["start_at"].startswith("2026-10-01T16:00") and corpo["end_at"].startswith("2026-10-01T17:00")
    assert not any(cm["url"].startswith("/api/voice/commands") for cm in out["calls"])


@node
def test_04_una_domanda_resta_a_schermo_e_non_salva(staged):  # noqa: F811
    scena = r"""
      await apriNuovo();
      f('[data-voice-dictate]').dispatch('click'); await wait();
      FakeRec.made[0].say('Visita con Mario Rossi il primo ottobre alle 16'); FakeRec.made[0].stop(); await wait();
      report({ stato: f('[data-voice-dictation-status]').visibleText(), tipo: f('[data-field="type"]').value,
               incarico: f('[data-property-selected]').hidden, cliente: f('.contact-picker-selected').visibleText(),
               modificabile: !f('[data-field="start"]').disabled });
    """
    out = _run(staged, scena, _route("GET", "/api/voice/status", ACCESA) + _route("POST", "/api/voice/interpret", DOMANDA) + _rotte())
    assert "Quale immobile deve visitare Mario Rossi?" in out["stato"] and out["tipo"] == "buyer_visit"
    assert out["incarico"] is True and "Mario Rossi" in out["cliente"] and out["modificabile"] is True
    assert not any(cm["m"] == "POST" and cm["url"] == "/api/appointments" for cm in out["calls"])


@node
def test_05_microfono_negato_dal_browser(staged):  # noqa: F811
    scena = r"""
      await apriNuovo();
      f('[data-voice-dictate]').dispatch('click'); await wait();
      FakeRec.made[0].onerror({ error: 'not-allowed' }); FakeRec.made[0].stop(); await wait();
      report({ stato: f('[data-voice-dictation-status]').visibleText(), bottone: f('[data-voice-dictate]').textContent });
    """
    out = _run(staged, scena, _route("GET", "/api/voice/status", ACCESA) + _rotte())
    assert "Permesso del microfono negato" in out["stato"] and out["bottone"] == "🎤 Parla"
    assert not any(cm["url"] == "/api/voice/interpret" for cm in out["calls"])


@node
def test_06_omonimi_nessuna_scelta_automatica(staged):  # noqa: F811
    scena = r"""
      await apriNuovo();
      f('[data-voice-dictate]').dispatch('click'); await wait();
      FakeRec.made[0].say('Sopralluogo con Mario Rossi il primo ottobre alle 16'); FakeRec.made[0].stop(); await wait();
      report({ stato: f('[data-voice-dictation-status]').visibleText(), tipo: f('[data-field="type"]').value,
               cliente: f('.contact-picker-selected').hidden });
    """
    out = _run(staged, scena, _route("GET", "/api/voice/status", ACCESA) + _route("POST", "/api/voice/interpret", OMONIMI) + _rotte())
    assert out["tipo"] == "inspection" and out["cliente"] is True
    assert "Più contatti possibili" in out["stato"] and "scegli il cliente" in out["stato"]
