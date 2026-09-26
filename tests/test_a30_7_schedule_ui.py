"""A30-7 - "Pianifica / Fissa sopralluogo" e "Aggiorna richieste dal sito", ESEGUITI.

Stesso banco di A30-5 (`main.js` VERO, router, sessione, pagina Agenda,
drawer e dialog veri, stub di DOM di P26-4/P27-7 e `fetch` instradato per
URL), riusato e non copiato. Si prova cio' che l'operatore vede e quali
richieste partono:

* il bottone di sincronizzazione esiste solo per chi smista (owner/admin),
  non parte mai da solo e mostra il risultato del server;
* una richiesta dal sito ha il badge e la PREFERENZA del cliente;
* nel dialog l'agente e' obbligatorio; gli slot arrivano da GET /availability
  (durata 60, passo 30): liberi selezionabili, occupati disabilitati, passati
  assenti, fascia 08-20 con "tutta la giornata";
* la conferma chiede prima availability/check e poi `POST /{id}/schedule`
  sulla STESSA riga, con `version`;
* 409 (conflitto, sopralluogo gia' aperto), 422 (passato), 500 e rete: nessun
  falso "Sopralluogo fissato".

Senza node le prove sono SKIPPED (da riportare come BLOCKED), mai passate.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.test_a30_5_create_ui import run as _run_a30_5

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node non disponibile: prove A30-7 NON eseguite (BLOCKED)")

ROOT = Path(__file__).resolve().parents[1]


def _rt():
    from tests import test_p27_7_network_runtime as rt
    return rt


AGENTI = {"items": [{"id": 3, "name": "Anna Agente"}, {"id": 4, "name": "Bruno Collega"}]}
RIGA = {"id": 12, "agency_id": 7, "appointment_type": "inspection", "status": "requested",
        "start_at": "2030-01-08T10:00:00+01:00", "end_at": "2030-01-08T11:00:00+01:00",
        "assigned_user_id": None, "version": 1, "stima_id": 900,
        "source": "legacy_stime_dettagliate", "source_record_id": "stime_dettagliate:41",
        "notes": "Disponibilita' indicata dal cliente nel modulo stima dettagliata "
                 "(legacy stime_dettagliate #41). Orario non confermato: da fissare.",
        "location_text": None, "buffer_before_minutes": 0, "buffer_after_minutes": 0}
DETTAGLIO = {"appointment": RIGA, "agent": None, "contact": None, "property": None,
             "stima": {"id": 900, "comune": "Tortoreto"}, "lead": None,
             "allowed_actions": ["schedule", "reassign", "cancel", "patch"],
             "allowed_links": []}
SLOT = {"slots": [
    {"start_at": "2020-01-08T09:00:00+01:00", "end_at": "2020-01-08T10:00:00+01:00",
     "available": True},                                            # passato: assente
    {"start_at": "2030-01-08T06:00:00+01:00", "end_at": "2030-01-08T07:00:00+01:00",
     "available": True},                                            # fuori fascia 08-20
    {"start_at": "2030-01-08T09:00:00+01:00", "end_at": "2030-01-08T10:00:00+01:00",
     "available": True},
    {"start_at": "2030-01-08T10:00:00+01:00", "end_at": "2030-01-08T11:00:00+01:00",
     "available": False},                                           # occupato
    {"start_at": "2030-01-08T11:30:00+01:00", "end_at": "2030-01-08T12:30:00+01:00",
     "available": True},
], "timezone": "Europe/Rome", "duration": 60, "step": 30}
LIBERO = {"available": True, "conflicts": [], "alternatives": []}
FISSATO = {**RIGA, "status": "scheduled", "assigned_user_id": 3, "version": 2,
           "start_at": "2030-01-08T09:00:00+01:00", "end_at": "2030-01-08T10:00:00+01:00",
           "stima_inspection_id": 55}


def _sessione(ruolo):
    return {**_rt().TENANT, "role": ruolo}


def _rotte(*, ruolo="agency_owner", sync=None, schedule=None, check=(LIBERO,), slot=None,
           altro=None):
    rt = _rt()
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(_sessione(ruolo))]),
        ("GET", "/api/appointments/agents", [rt.ok(AGENTI)]),
        ("POST", "/api/appointments/legacy-requests/sync",
         list(sync or [rt.ok({"imported": 2, "already_present": 3, "excluded": 1})])),
        ("GET", "/api/appointments/availability?", list(slot or [rt.ok(SLOT)])),
        ("POST", "/api/appointments/availability/check", [rt.ok(c) for c in check]),
        ("POST", "/api/appointments/12/schedule",
         list(schedule or [rt.ok(FISSATO)])),
        ("GET", "/api/appointments/12/events", [rt.ok({"items": []})]),
        ("GET", "/api/appointments/12", [rt.ok(DETTAGLIO)]),
        ("GET", "/api/appointments/77/events", [rt.ok({"items": []})]),
        ("GET", "/api/appointments/77",
         [rt.ok(altro or {**DETTAGLIO, "appointment": {**RIGA, "id": 77, "source": "crm_manual"}})]),
        ("GET", "/api/appointments?", [rt.ok({"items": [RIGA]})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});"
                     for m, p, r in voci)


PASSI = r"""
const drawer = () => C().querySelector('dialog.agenda-drawer');
async function apriRiga() {
  C().querySelectorAll('button').find((b) => b.dataset.appointmentId === '12').dispatch('click');
  await wait();
}
async function pianifica() {
  await apriRiga();
  drawer().querySelectorAll('button').find((b) => b.dataset.action === 'schedule').dispatch('click');
  await wait();
}
async function agente(id = '3') { set('[data-field="agent"]', id, 'change'); await wait(); }
function slot() {
  return f('[data-slots]').querySelectorAll('button').map((b) => ({
    t: b.textContent, disabled: !!b.disabled, kind: b.dataset.slot || null }));
}
function avviso() {
  const n = C().querySelector('.agenda-notice');
  return n ? n.visibleText() : '';
}
"""


@pytest.fixture(scope="module")
def staged(tmp_path_factory):
    return _rt()._stage(tmp_path_factory.mktemp("a30-7"))


def run(staged, scenario, rotte):
    return _run_a30_5(staged, PASSI + scenario, rotte, hash="#/agenda/lista/2030-01-07")


def _chiamate(out, metodo, prefisso):
    return [c for c in out["calls"] if c["m"] == metodo and c["url"].startswith(prefisso)]


# ---------------------------------------------------------------------------
# SINCRONIZZAZIONE (D2)
# ---------------------------------------------------------------------------

def test_01_owner_vede_il_bottone_e_la_pagina_non_sincronizza_da_sola(staged):
    out = run(staged, """
      report({ bottone: !!bottone(C(), 'Aggiorna richieste dal sito') });
    """, _rotte())
    assert out["bottone"] is True
    assert _chiamate(out, "POST", "/api/appointments/legacy-requests/sync") == []


@pytest.mark.parametrize("ruolo", ["agent", "viewer"])
def test_02_agent_non_vede_il_bottone(staged, ruolo):
    out = run(staged, """
      report({ bottone: !!bottone(C(), 'Aggiorna richieste dal sito') });
    """, _rotte(ruolo=ruolo))
    assert out["bottone"] is False


def test_03_sincronizza_ricarica_e_mostra_i_contatori(staged):
    out = run(staged, """
      const prima = __calls.filter((c) => c.url.startsWith('/api/appointments?')).length;
      bottone(C(), 'Aggiorna richieste dal sito').dispatch('click'); await wait();
      const dopo = __calls.filter((c) => c.url.startsWith('/api/appointments?')).length;
      report({ prima, dopo, avviso: avviso() });
    """, _rotte())
    (sync,) = _chiamate(out, "POST", "/api/appointments/legacy-requests/sync")
    assert sync["body"] == {}                          # nessuna agenzia dal browser
    assert "Authorization" not in sync["headers"] and sync["cred"] == "include"
    assert out["dopo"] == out["prima"] + 1             # l'Agenda si rilegge
    assert "nuove 2 · già presenti 3 · escluse 1" in out["avviso"]


def test_04_errore_della_sincronizzazione_nessun_falso_successo(staged):
    for risposta, atteso in (
            ({"status": 500, "body": {"detail": "boom"}}, "Errore del server (500)"),
            ({"status": 403, "body": {"detail": "Solo titolare e amministratori possono "
                                                "aggiornare le richieste dal sito",
                                      "code": "FORBIDDEN_ROLE"}}, "Solo titolare"),
            ({"throw": True}, "Impossibile contattare il server")):
        out = run(staged, """
          bottone(C(), 'Aggiorna richieste dal sito').dispatch('click'); await wait();
          report({ avviso: avviso() });
        """, _rotte(sync=[risposta]))
        assert atteso in out["avviso"], out["avviso"]
        assert "Richieste dal sito aggiornate" not in out["avviso"]


# ---------------------------------------------------------------------------
# DRAWER: badge e preferenza
# ---------------------------------------------------------------------------

def test_10_richiesta_dal_sito_badge_e_preferenza(staged):
    out = run(staged, """
      await apriRiga();
      report({ testo: drawer().visibleText(),
               azioni: drawer().querySelectorAll('button').map((b) => b.dataset.action || null) });
    """, _rotte())
    assert "Richiesta dal sito" in out["testo"]
    assert "Preferenza cliente" in out["testo"] and "Quando" not in out["testo"]
    assert out["azioni"].count("schedule") == 1       # una sola azione "Pianifica"


# ---------------------------------------------------------------------------
# DIALOG PIANIFICA
# ---------------------------------------------------------------------------

def test_20_preferenza_agente_obbligatorio_e_nessuna_richiesta_senza(staged):
    out = run(staged, """
      await pianifica();
      const testo = dlg().visibleText();
      await conferma();
      report({ testo });
    """, _rotte())
    assert "Preferenza cliente:" in out["testo"] and "non ancora fissata" in out["testo"]
    assert "Durata: 1 h · slot ogni 30 minuti" in out["testo"]
    assert out["error"] == "Per pianificare scegli un agente."
    assert out["open"] is True
    assert _chiamate(out, "POST", "/api/appointments/12/schedule") == []
    assert _chiamate(out, "POST", "/api/appointments/availability/check") == []
    assert _chiamate(out, "GET", "/api/appointments/availability?") == []


def test_21_slot_liberi_occupati_passati_e_fascia(staged):
    out = run(staged, """
      await pianifica();
      await agente('3');
      const fascia = slot();
      bottone(dlg(), 'Mostra tutta la giornata').dispatch('click'); await wait();
      const tutta = slot();
      report({ fascia, tutta });
    """, _rotte())
    (disp, *_resto) = _chiamate(out, "GET", "/api/appointments/availability?")
    assert "user_id=3" in disp["url"] and "duration=60" in disp["url"] and "step=30" in disp["url"]
    assert "exclude_appointment_id=12" in disp["url"]
    fascia = {s["t"]: s for s in out["fascia"]}
    assert set(fascia) == {"09:00–10:00", "10:00–11:00 · Occupato", "11:30–12:30"}
    assert fascia["10:00–11:00 · Occupato"]["disabled"] is True
    assert fascia["09:00–10:00"]["disabled"] is False and fascia["09:00–10:00"]["kind"] == "free"
    tutta = {s["t"] for s in out["tutta"]}
    assert "06:00–07:00" in tutta                      # fuori fascia, ma la giornata c'e'
    assert not any(t.startswith("09:00") and "2020" in t for t in tutta)
    assert len(out["tutta"]) == 4                      # il passato non c'e' mai


def test_22_conferma_controlla_poi_schedule_sulla_stessa_riga(staged):
    out = run(staged, """
      await pianifica();
      await agente('3');
      f('[data-slots]').querySelectorAll('button').find((b) => b.textContent === '09:00–10:00')
        .dispatch('click'); await wait();
      await conferma();
      report({ avviso: avviso() });
    """, _rotte())
    controlli = _chiamate(out, "POST", "/api/appointments/availability/check")
    scritture = _chiamate(out, "POST", "/api/appointments/12/schedule")
    assert len(controlli) == 1 and len(scritture) == 1
    indici = [out["calls"].index(controlli[0]), out["calls"].index(scritture[0])]
    assert indici[0] < indici[1]                       # prima il controllo
    assert scritture[0]["body"] == {"version": 1, "assigned_user_id": 3,
                                    "start_at": "2030-01-08T09:00:00+01:00",
                                    "end_at": "2030-01-08T10:00:00+01:00"}
    assert controlli[0]["body"]["exclude_appointment_id"] == 12
    assert [c for c in out["calls"] if c["m"] == "POST"
            and c["url"] == "/api/appointments"] == []          # nessun appuntamento nuovo
    assert out["open"] is False
    assert "Sopralluogo fissato." in out["avviso"]


def test_23_errore_disponibilita_nessuno_slot_libero(staged):
    out = run(staged, """
      await pianifica();
      await agente('3');
      report({ slot: slot(), box: f('[data-slots]').visibleText() });
    """, _rotte(slot=[{"status": 500, "body": {"detail": "x"}}]))
    assert out["slot"] == []
    assert "Errore del server (500)" in out["box"]


def test_24_409_sopralluogo_gia_aperto_messaggio_e_apri_appuntamento(staged):
    risposta = {"status": 409, "body": {"detail": "Esiste gia' un sopralluogo aperto per questa stima",
                                        "code": "STIMA_INSPECTION_ALREADY_OPEN",
                                        "existing_appointment_id": 77}}
    out = run(staged, """
      await pianifica();
      await agente('3');
      await conferma();
      const errore = f('[data-error]').textContent;
      const apri = bottone(dlg(), 'Apri appuntamento');
      const aperto = dlg()._open;
      apri.dispatch('click'); await wait();
      report({ errore, aperto, avviso: avviso(), drawer: drawer().visibleText() });
    """, _rotte(schedule=[risposta]))
    assert out["errore"] == "Esiste già un sopralluogo aperto per questa stima."
    assert out["aperto"] is True
    assert "Sopralluogo fissato" not in out["avviso"]
    assert _chiamate(out, "GET", "/api/appointments/77")        # si apre l'altro


def test_25_409_senza_id_nessun_link(staged):
    risposta = {"status": 409, "body": {"detail": "x", "code": "STIMA_INSPECTION_ALREADY_OPEN"}}
    out = run(staged, """
      await pianifica(); await agente('3'); await conferma();
      report({ apri: !!bottone(dlg(), 'Apri appuntamento') });
    """, _rotte(schedule=[risposta]))
    assert out["apri"] is False
    assert out["error"] == "Esiste già un sopralluogo aperto per questa stima."


def test_26_409_conflitto_slot_alternative_e_dialog_aperto(staged):
    risposta = {"status": 409, "body": {
        "detail": "Orario non disponibile per l'agente", "code": "APPOINTMENT_CONFLICT",
        "conflicts": [{"start_at": "2030-01-08T10:00:00+01:00",
                       "end_at": "2030-01-08T11:00:00+01:00", "label": "Occupato"}],
        "alternatives": [{"start_at": "2030-01-08T11:30:00+01:00",
                          "end_at": "2030-01-08T12:30:00+01:00"}]}}
    out = run(staged, """
      await pianifica(); await agente('3'); await conferma();
      report({ avviso: avviso() });
    """, _rotte(schedule=[risposta]))
    assert out["open"] is True and "ORARIO NON DISPONIBILE" in out["status"]
    assert len(out["alt"]) == 1 and "11:30" in out["alt"][0]
    assert "Sopralluogo fissato" not in out["avviso"]


def test_27_422_passato_messaggio_leggibile(staged):
    risposta = {"status": 422, "body": {"detail": "L'orario scelto e' gia' passato",
                                        "code": "SCHEDULE_IN_PAST"}}
    out = run(staged, """
      await pianifica(); await agente('3'); await conferma();
      report({ avviso: avviso() });
    """, _rotte(schedule=[risposta]))
    assert out["error"] == "L'orario scelto è già passato: scegli un orario futuro."
    assert out["open"] is True and "Sopralluogo fissato" not in out["avviso"]


def test_28_orario_passato_scritto_a_mano_non_parte(staged):
    out = run(staged, """
      await pianifica(); await agente('3');
      orario('2020-01-08', '09:00', '10:00');
      await conferma();
      report({});
    """, _rotte())
    assert out["error"] == "L'orario scelto è già passato: scegli un orario futuro."
    assert _chiamate(out, "POST", "/api/appointments/12/schedule") == []
    assert _chiamate(out, "POST", "/api/appointments/availability/check") == []


def test_29_500_e_rete_nessuna_falsa_conferma(staged):
    for risposta, atteso in (({"status": 500, "body": {"detail": "x"}}, "Errore del server (500)"),
                             ({"throw": True}, "Impossibile contattare il server")):
        out = run(staged, """
          await pianifica(); await agente('3'); await conferma();
          report({ avviso: avviso() });
        """, _rotte(schedule=[risposta]))
        assert atteso in out["error"]
        assert out["open"] is True and "Sopralluogo fissato" not in out["avviso"]


def test_30_codice_gli_slot_non_nascono_senza_il_motore_agenda():
    dialoghi = (ROOT / "static" / "os_shell" / "assets" / "components" / "agenda"
                / "agenda-dialogs.js").read_text(encoding="utf-8")
    corpo = dialoghi[dialoghi.index("async function mostraSlotGiornata"):]
    corpo = corpo[:corpo.index("\n}\n")]
    # l'errore del server esce PRIMA di qualunque slot
    assert corpo.index("catch (errore)") < corpo.index("for (const s of visibili)")
    assert "PASSO_SLOT" in corpo and "Date.parse(s.start_at) > adesso" in corpo
