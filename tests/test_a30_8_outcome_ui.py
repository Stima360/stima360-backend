"""A30-8 - esito dell'appuntamento e follow-up nella UI Agenda, ESEGUITI.

Stesso banco di A30-5/A30-7 (`main.js` VERO, router, sessione, pagina Agenda,
drawer e dialog veri, stub di DOM di P26-4/P27-7 e `fetch` instradato per
URL), riusato e non copiato. Si prova cio' che l'operatore vede e quali
richieste partono:

* Completa / Non presentato / Annulla: nota di esito (non su Annulla), blocco
  follow-up SPENTO all'apertura e assente se l'appuntamento non ha contatto,
  lead o stima;
* il corpo porta solo `version`, i campi dell'esito e `follow_up` con
  scadenza (con fuso), titolo e nota: MAI contatto, lead, stima, agenzia,
  assegnatario;
* il messaggio di successo arriva solo dopo il 2xx; 409/422/500/rete non
  mostrano successo e lasciano il dialog aperto;
* il pannello: date terminali, nota di esito letta dagli eventi gia' caricati,
  "disponibile dal" per un esito non ancora registrabile (non eseguibile).

Senza node le prove sono SKIPPED (da riportare come BLOCKED), mai passate.
"""
from __future__ import annotations

import json
import shutil

import pytest

from tests.test_a30_5_create_ui import run as _run_a30_5

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node non disponibile: prove A30-8 NON eseguite (BLOCKED)")


def _rt():
    from tests import test_p27_7_network_runtime as rt
    return rt


AGENTI = {"items": [{"id": 3, "name": "Anna Agente"}, {"id": 4, "name": "Bruno Collega"}]}
RIGA = {"id": 12, "agency_id": 7, "appointment_type": "seller_meeting", "status": "scheduled",
        "start_at": "2026-09-20T10:00:00+02:00", "end_at": "2026-09-20T11:00:00+02:00",
        "assigned_user_id": 3, "version": 4, "contact_id": 41, "lead_id": 501,
        "stima_id": None, "property_id": None, "source": "crm_manual", "notes": None,
        "location_text": None, "buffer_before_minutes": 0, "buffer_after_minutes": 0,
        "confirmed_at": None, "completed_at": None, "no_show_at": None,
        "cancelled_at": None, "cancelled_reason": None}
AZIONI = ["confirm", "reschedule", "reassign", "complete", "no_show", "cancel", "patch"]
FU = {"due_at": "2030-01-10T09:30:00+01:00", "title": "Richiamare", "note": "Portare planimetria"}


def _dettaglio(riga=None, azioni=None):
    riga = riga or RIGA
    return {"appointment": riga, "agent": {"id": 3, "name": "Anna Agente", "active": True},
            "contact": {"id": 41, "display_name": "Mario Rossi"} if riga.get("contact_id") else None,
            "property": None, "stima": None, "lead": None,
            "allowed_actions": AZIONI if azioni is None else azioni, "allowed_links": []}


def _rotte(*, dettaglio=None, azione=None, risposta=None, eventi=None):
    rt = _rt()
    dettaglio = dettaglio or _dettaglio()
    riga = dettaglio["appointment"]
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(rt.TENANT)]),
        ("GET", "/api/appointments/agents", [rt.ok(AGENTI)]),
        ("GET", "/api/appointments/12/events", [rt.ok({"items": eventi or []})]),
        ("GET", "/api/appointments/12", [rt.ok(dettaglio)]),
        ("GET", "/api/appointments?", [rt.ok({"items": [riga]})]),
    ]
    if azione:
        voci.insert(0, ("POST", f"/api/appointments/12/{azione}",
                        [risposta or rt.ok({**riga, "status": "completed", "version": 5})]))
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});"
                     for m, p, r in voci)


PASSI = r"""
const drawer = () => C().querySelector('dialog.agenda-drawer');
async function apriRiga() {
  C().querySelectorAll('button').find((b) => b.dataset.appointmentId === '12').dispatch('click');
  await wait();
}
async function azione(nome) {
  await apriRiga();
  drawer().querySelectorAll('button').find((b) => b.dataset.action === nome).dispatch('click');
  await wait();
}
function followUp(data = '2030-01-10', ora = '09:30', titolo = 'Richiamare', nota = 'Portare planimetria') {
  const t = f('[data-field="follow-up"]');
  t.checked = true; t.dispatch('change');
  f('[data-field="follow-up-date"]').value = data;
  set('[data-field="follow-up-time"]', ora);
  set('[data-field="follow-up-title"]', titolo);
  set('[data-field="follow-up-note"]', nota);
}
function stato() {
  const t = dlg() && f('[data-field="follow-up"]');
  return { toggle: t ? !!t.checked : null,
           campiNascosti: t ? !!f('[data-follow-up-fields]').hidden : null,
           nota: !!(dlg() && f('[data-field="outcome-note"]')),
           testo: dlg() ? dlg().visibleText() : '' };
}
function avviso() {
  const n = C().querySelector('.agenda-notice');
  return n ? n.visibleText() : '';
}
"""


@pytest.fixture(scope="module")
def staged(tmp_path_factory):
    return _rt()._stage(tmp_path_factory.mktemp("a30-8"))


def run(staged, scenario, rotte, giorno="2026-09-20"):
    return _run_a30_5(staged, PASSI + scenario, rotte, hash=f"#/agenda/lista/{giorno}")


def _post(out, azione):
    return [c for c in out["calls"] if c["m"] == "POST"
            and c["url"] == f"/api/appointments/12/{azione}"]


VIETATI = {"contact_id", "lead_id", "stima_id", "agency_id", "assigned_to",
           "created_by_user_id", "task_type", "metadata"}


# ---------------------------------------------------------------------------
# COMPLETA (36)
# ---------------------------------------------------------------------------

def test_01_completa_nota_e_follow_up_spento_all_apertura(staged):
    out = run(staged, "await azione('complete'); report(stato());", _rotte())
    assert out["nota"] is True
    assert out["toggle"] is False and out["campiNascosti"] is True
    assert "Crea follow-up" in out["testo"] and "Registra esito" in out["testo"]


def test_02_completa_senza_follow_up_corpo_minimo_e_messaggio_dopo_il_2xx(staged):
    out = run(staged, """
      await azione('complete');
      set('[data-field="outcome-note"]', '  Interessato a vendere  ');
      await conferma();
      report({ avviso: avviso() });
    """, _rotte(azione="complete"))
    (scrittura,) = _post(out, "complete")
    assert scrittura["body"] == {"version": 4, "outcome_note": "Interessato a vendere"}
    assert out["open"] is False
    assert "Appuntamento completato." in out["avviso"]


def test_03_completa_con_follow_up_solo_scadenza_titolo_nota(staged):
    out = run(staged, """
      await azione('complete'); followUp(); await conferma(); report({ avviso: avviso() });
    """, _rotte(azione="complete"))
    (scrittura,) = _post(out, "complete")
    assert scrittura["body"] == {"version": 4, "follow_up": FU}
    assert not (VIETATI & set(scrittura["body"]["follow_up"]))            # 24
    assert "Authorization" not in scrittura["headers"] and scrittura["cred"] == "include"
    assert "Appuntamento completato." in out["avviso"]


def test_04_follow_up_incompleto_o_nel_passato_non_parte(staged):
    for passi, atteso in (
            ("followUp('2030-01-10', ''); ", "Indica data e ora del follow-up."),
            ("followUp('2020-01-10', '09:30'); ", "La scadenza del follow-up deve essere nel futuro.")):
        out = run(staged, "await azione('complete'); " + passi
                  + "await conferma(); report({ avviso: avviso() });", _rotte(azione="complete"))
        assert out["error"] == atteso
        assert _post(out, "complete") == [] and out["open"] is True
        assert "completato" not in out["avviso"]


# ---------------------------------------------------------------------------
# NON PRESENTATO (37) e ANNULLA (38)
# ---------------------------------------------------------------------------

def test_05_non_presentato_nota_follow_up_e_messaggio(staged):
    out = run(staged, """
      await azione('no_show');
      const prima = stato();
      set('[data-field="outcome-note"]', 'Citofono muto');
      followUp();
      await conferma();
      report({ prima, avviso: avviso() });
    """, _rotte(azione="no-show", risposta=_rt().ok({**RIGA, "status": "no_show"})))
    assert out["prima"]["toggle"] is False and out["prima"]["nota"] is True
    assert "Cliente non presentato" in out["prima"]["testo"]
    (scrittura,) = _post(out, "no-show")
    assert scrittura["body"] == {"version": 4, "outcome_note": "Citofono muto", "follow_up": FU}
    assert "Cliente segnato come non presentato." in out["avviso"]


def test_06_annulla_motivo_esistente_follow_up_nessuna_nota_di_esito(staged):
    out = run(staged, """
      await azione('cancel');
      const prima = stato();
      set('[data-field="reason"]', 'Rinviato dal cliente');
      followUp();
      await conferma();
      report({ prima, avviso: avviso() });
    """, _rotte(azione="cancel", risposta=_rt().ok({**RIGA, "status": "cancelled"})))
    assert out["prima"]["nota"] is False and out["prima"]["toggle"] is False
    (scrittura,) = _post(out, "cancel")
    assert scrittura["body"] == {"version": 4, "reason": "Rinviato dal cliente", "follow_up": FU}
    assert "outcome_note" not in scrittura["body"]
    assert "Appuntamento annullato." in out["avviso"]


def test_07_annulla_senza_follow_up_resta_il_contratto_di_prima(staged):
    out = run(staged, "await azione('cancel'); await conferma(); report({});",
              _rotte(azione="cancel", risposta=_rt().ok({**RIGA, "status": "cancelled"})))
    (scrittura,) = _post(out, "cancel")
    assert scrittura["body"] == {"version": 4, "reason": None}


# ---------------------------------------------------------------------------
# FOLLOW-UP NASCOSTO SENZA COLLEGAMENTI (39)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nome", ["complete", "no_show", "cancel"])
def test_08_senza_contatto_lead_stima_nessun_blocco_follow_up(staged, nome):
    riga = {**RIGA, "contact_id": None, "lead_id": None, "stima_id": None}
    out = run(staged, f"await azione('{nome}'); report(stato());",
              _rotte(dettaglio=_dettaglio(riga)))
    assert out["toggle"] is None and "Crea follow-up" not in out["testo"]


def test_08b_basta_la_stima(staged):
    riga = {**RIGA, "contact_id": None, "lead_id": None, "stima_id": 900}
    out = run(staged, "await azione('complete'); report(stato());",
              _rotte(dettaglio=_dettaglio(riga)))
    assert out["toggle"] is False


# ---------------------------------------------------------------------------
# ERRORI: nessun successo prima del backend (40-41)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("risposta,atteso", [
    ({"status": 422, "body": {"detail": "x", "code": "COMPLETE_TOO_EARLY",
                              "available_from": "2030-01-01T10:00:00+01:00"}},
     "L'appuntamento non è ancora iniziato: potrai completarlo dall'orario di inizio."),
    ({"status": 422, "body": {"detail": "x", "code": "FOLLOW_UP_IN_PAST"}},
     "La scadenza del follow-up deve essere nel futuro."),
    ({"status": 422, "body": {"detail": "x", "code": "FOLLOW_UP_REQUIRES_LINK"}},
     "Il follow-up richiede un cliente, un lead o una stima collegati."),
    ({"status": 409, "body": {"detail": "x", "code": "VERSION_CONFLICT", "current_version": 6}},
     "Questo appuntamento è stato modificato da un altro operatore. Ricarica e riprova."),
    ({"status": 409, "body": {"detail": "Azione non piu' possibile: lo stato e' 'Completato'",
                              "code": "INVALID_TRANSITION"}},
     "Azione non piu' possibile: lo stato e' 'Completato'"),
    ({"status": 403, "body": {"detail": "Non consentito", "code": "FORBIDDEN_ROLE"}},
     "Non consentito"),
    ({"status": 404, "body": {"detail": "Risorsa non trovata", "code": "NOT_FOUND"}},
     "Appuntamento non trovato o non più disponibile."),
    ({"status": 500, "body": {"detail": "boom"}}, "Errore del server (500)"),
    ({"throw": True}, "Impossibile contattare il server"),
])
def test_09_errori_leggibili_dialog_aperto_nessun_successo(staged, risposta, atteso):
    out = run(staged, """
      await azione('complete'); followUp(); await conferma(); report({ avviso: avviso() });
    """, _rotte(azione="complete", risposta=risposta))
    assert atteso in out["error"], out["error"]
    assert out["open"] is True
    assert "completato" not in out["avviso"].lower()


def test_10_no_show_troppo_presto_messaggio(staged):
    out = run(staged, "await azione('no_show'); await conferma(); report({});",
              _rotte(azione="no-show", risposta={"status": 422, "body": {
                  "detail": "x", "code": "NO_SHOW_TOO_EARLY"}}))
    assert out["error"] == ("Potrai segnare il cliente come non presentato solo dopo la fine "
                            "dell'appuntamento.")


def test_11_sposta_nel_passato_non_parte_e_422_leggibile(staged):
    out = run(staged, """
      await azione('reschedule');
      orario('2020-01-08', '09:00', '10:00');
      await conferma();
      report({});
    """, _rotte(azione="reschedule"))
    assert out["error"] == "Il nuovo orario è già passato: scegli un orario futuro."
    assert _post(out, "reschedule") == []


# ---------------------------------------------------------------------------
# PANNELLO (35)
# ---------------------------------------------------------------------------

def test_12_pannello_terminale_date_e_nota_di_esito_dagli_eventi(staged):
    riga = {**RIGA, "status": "no_show", "confirmed_at": "2026-09-19T08:00:00+02:00",
            "no_show_at": "2026-09-20T11:05:00+02:00"}
    eventi = [
        {"event_type": "created", "from_status": None, "to_status": "scheduled",
         "actor_user_id": 3, "occurred_at": "2026-09-18T10:00:00+02:00", "changes": {}},
        {"event_type": "status_changed", "from_status": "confirmed", "to_status": "no_show",
         "actor_user_id": 3, "occurred_at": "2026-09-20T11:05:00+02:00",
         "changes": {"azione": "no_show", "outcome_note": "Citofono muto",
                     "follow_up_task_id": 88}},
    ]
    out = run(staged, """
      await apriRiga();
      report({ testo: drawer().visibleText(),
               azioni: drawer().querySelectorAll('button').map((b) => b.dataset.action || null)
                 .filter(Boolean) });
    """, _rotte(dettaglio=_dettaglio(riga, []), eventi=eventi))
    assert "Confermato il" in out["testo"] and "Non presentato il" in out["testo"]
    assert "Nota esito" in out["testo"] and "Citofono muto" in out["testo"]
    assert out["azioni"] == []
    # nessuna richiesta in piu' per la nota: solo dettaglio ed eventi
    letture = [c["url"] for c in out["calls"] if c["url"].startswith("/api/appointments/12")]
    assert sorted(set(letture)) == ["/api/appointments/12", "/api/appointments/12/events"]


def test_13_esito_non_ancora_registrabile_non_e_eseguibile(staged):
    riga = {**RIGA, "start_at": "2030-01-08T10:00:00+01:00", "end_at": "2030-01-08T11:00:00+01:00"}
    out = run(staged, """
      await apriRiga();
      const attesa = drawer().querySelectorAll('button').filter((b) => b.dataset.pendingAction);
      report({ testo: drawer().visibleText(),
               attesa: attesa.map((b) => ({ a: b.dataset.pendingAction, disabled: !!b.disabled })),
               eseguibili: drawer().querySelectorAll('button').map((b) => b.dataset.action || null)
                 .filter(Boolean) });
    """, _rotte(dettaglio=_dettaglio(riga, ["confirm", "reschedule", "reassign", "cancel", "patch"])),
        giorno="2030-01-07")
    assert out["attesa"] == [{"a": "complete", "disabled": True}, {"a": "no_show", "disabled": True}]
    assert "complete" not in out["eseguibili"] and "no_show" not in out["eseguibili"]
    assert "Completa: disponibile dal" in out["testo"]


def test_14_chi_non_puo_agire_non_vede_indicazioni(staged):
    riga = {**RIGA, "start_at": "2030-01-08T10:00:00+01:00", "end_at": "2030-01-08T11:00:00+01:00"}
    out = run(staged, """
      await apriRiga();
      report({ attesa: drawer().querySelectorAll('button').filter((b) => b.dataset.pendingAction)
        .length });
    """, _rotte(dettaglio=_dettaglio(riga, [])), giorno="2030-01-07")
    assert out["attesa"] == 0
