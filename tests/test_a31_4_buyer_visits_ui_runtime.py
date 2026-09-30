"""A31-4 - "Programma visita" dai tre caller della OS Shell, ESEGUITO.

`main.js` VERO, router, sessione, viste e dialog veri, dentro lo stub di DOM
di P26-4/P27-7 con il `fetch` instradato di A30-5 e la <select> letta per
proprieta' di A30-13B (riusati, non copiati). Nessun database: il server e'
il `fetch` instradato; il permesso VERO e' del server (vedi
`tests/test_a31_4_legacy_lane_closed_postgres.py`).

Cosa si prova (matrice A31-4 §16 + i test UI aggiuntivi):

* A    Agenda normale senza opzioni: invariata (titolo, tipo, "Nessuno",
       luogo, note, CRM completo, POST /api/appointments);
* B/C  tipo `buyer_visit` e durata 60' bloccati;
* D    owner/admin/Supreme: vero selettore, nessun "Nessuno", senza agente
       nessuna richiesta (ne' availability ne' POST);
* E    agent: "Io" bloccato su se stesso;
* F-H  BUY: nessun blocco CRM, note visibili, luogo nascosto;
* I-K  BUY: stessa `client_request_id` del dialog, payload esatto, UNA POST;
* L-P  PROPERTY: cliente e lead si', stima/immobile/luogo/note no, payload
       esatto, UNA POST;
* Q/R  il controllo disponibilita' parte sempre; un conflitto ferma la POST;
* S    doppio invio: una sola POST;
* T/U  il modal legacy resta per lo storico; una riga proiettata offre solo
       l'esito (niente data/stato/elimina).

Senza node le prove sono SKIPPED (da riportare come BLOCKED), mai passate.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests import test_a30_13b_quick_booking_ui as a30_13b
# Fixture riusata: la copia degli asset in una cartella temporanea.
from tests.test_a30_5_create_ui import staged  # noqa: F401

NODE = a30_5.NODE

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node non disponibile: prove A31-4 NON eseguite (BLOCKED)")

AGENTI = a30_13b.AGENTI
LIBERO = a30_5.LIBERO
OCCUPATO = a30_5.OCCUPATO

MATCH = {"id": 19, "buy_request_id": 16, "property_id": 30, "buyer_name": "Mario Rossi",
         "property_title": "Trilocale", "match_class": "strong", "commercial_status": "interested",
         "freshness_status": "fresh", "score_total": 80, "criteria": [], "strengths": [],
         "warnings": [], "blocking_reasons": []}
WORKFLOW = {"id": 16, "title": "Cerca bilocale", "status": "active", "contact_id": 41,
            "lead_id": 501, "contact_name": "Mario Rossi", "matches": [MATCH],
            "interactions": [], "history": [], "tasks": [], "locations": [], "typologies": [],
            "features": []}
VISITA_LEGACY = {"id": 71, "property_id": 30, "scheduled_at": "2026-09-01T10:00:00+02:00",
                 "status": "completed", "contact_id": 41, "contact_name": "Mario Rossi",
                 "lead_id": None, "outcome": "ok", "feedback": None, "rating": 4,
                 "assigned_to": "Anna Agente", "appointment_id": None}
VISITA_PROIETTATA = {"id": 72, "property_id": 30, "scheduled_at": "2031-01-10T10:00:00+01:00",
                     "status": "scheduled", "contact_id": 41, "contact_name": "Mario Rossi",
                     "lead_id": None, "outcome": None, "feedback": None, "rating": None,
                     "assigned_to": "Anna Agente", "appointment_id": 900}
IMMOBILE = {"id": 30, "title": "Trilocale", "code": "GIU-30", "status": "active",
            "contacts": [], "leads": [], "photos": [], "documents": [],
            "visits": [VISITA_PROIETTATA, VISITA_LEGACY]}

URL_BUY = "/api/buy/requests/16/matches/19/decision"
URL_PROPERTY = "/api/property/properties/30/visits"


def _rotte(sessione="agent", *, check=(LIBERO,), buy=None, visita=None):
    rt = a30_5._rt()
    buy = buy or ({"status": 201, "body": {"id": 5, "interaction_type": "visit_scheduled"}},)
    visita = visita or ({"status": 201, "body": {**VISITA_PROIETTATA, "id": 73}},)
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
        ("GET", "/api/appointments/agents", [rt.ok(AGENTI)]),
        ("GET", "/api/appointments/calendar?", [rt.ok({"items": []})]),
        ("GET", "/api/appointments?", [rt.ok({"items": []})]),
        ("POST", "/api/appointments/availability/check", [rt.ok(c) for c in check]),
        ("POST", "/api/appointments", [{"status": 201, "body": a30_5._creato()}]),
        ("GET", "/api/match/matches/19/", [rt.ok({"items": []})]),
        ("GET", "/api/match/matches/19", [rt.ok(MATCH)]),
        ("POST", URL_BUY, list(buy)),
        ("GET", "/api/buy/requests/16/workflow", [rt.ok(WORKFLOW)]),
        ("GET", "/api/buy/requests/16", [rt.ok(WORKFLOW)]),
        ("GET", "/api/proposals?", [rt.ok({"items": []})]),
        ("GET", "/api/sales?", [rt.ok({"items": []})]),
        ("POST", URL_PROPERTY, list(visita)),
        ("GET", "/api/property/properties/30", [rt.ok(IMMOBILE)]),
        ("GET", "/api/core/contacts?search=", [rt.ok(a30_5.CONTATTI)]),
        ("GET", "/api/core/leads?contact_id=41", [rt.ok(a30_5.LEADS_41)]),
        ("GET", "/api/core/leads?contact_id=42", [rt.ok({"items": []})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});"
                     for m, p, r in voci)


A31_4_HELPERS = r"""
// il bottone della riga match ferma la propagazione (click sulla riga = apri):
// lo stub non ha `stopPropagation`, qui lo si passa con l'evento.
const EVENTO = { stopPropagation() {}, preventDefault() {} };
async function clic(sel) { C().querySelector(sel).dispatch('click', EVENTO); await wait(); }
function statoProgramma() {
  const agente = f('[data-field="agent"]');
  const tipo = f('[data-field="type"]');
  return {
    open: !!(dlg() && dlg()._open),
    titolo: f('[data-title]').textContent,
    tipo: tipo.value, tipoBloccato: tipo.disabled === true,
    durataBloccata: f('[data-field="duration"]').disabled === true,
    fineBloccata: f('[data-field="end"]').disabled === true,
    inizio: f('[data-field="start"]').value, fine: f('[data-field="end"]').value,
    agenteDisabilitato: agente.disabled === true, agenteValore: agente.value,
    agenteOpzioni: agente.querySelectorAll('option').map((o) => o.textContent),
    crm: !!f('.agenda-links'), cliente: !!f('[data-contact-picker]'), lead: !!f('[data-field="lead"]'),
    stima: !!f('[data-stima-search]'), immobile: !!f('[data-property-search]'),
    luogo: !!f('[data-field="location"]'), note: !!f('[data-field="notes"]'),
  };
}
function scegliAgente(id) { const a = f('[data-field="agent"]'); a.value = String(id); a.dispatch('change'); }
function orarioFuturo(inizio = '10:00') { orario('2031-01-10', inizio); }
"""


def run(staged: Path, scenario: str, rotte: str, hash: str) -> dict:  # noqa: F811
    rt = a30_5._rt()
    driver = staged.parent / "driver-a31-4.mjs"
    driver.write_text(
        a30_5._dom() + rt.FETCH + a30_13b._extra_dom() + a30_5.IS_CONNECTED + a30_5.ROUTED_FETCH
        + f"\n{rotte}\n"
        + f"window.location.hash = '{hash}';\n"
        + f"await import('{(staged / 'main.js').as_posix()}');\n"
        + "await __settle(40);\n"
        + a30_5.HELPERS + A31_4_HELPERS + scenario + "\n",
        encoding="utf-8")
    esito = subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=60,
                           cwd=staged.parent, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    if esito.returncode != 0:
        raise AssertionError(f"driver node fallito:\n{esito.stderr[-3000:]}\n{esito.stdout[-1500:]}")
    return json.loads(esito.stdout.strip().splitlines()[-1])


def _post(out, url):
    return [c for c in out["calls"] if c["m"] == "POST" and c["url"] == url]


def _scritture(out):
    """Ogni scrittura della prova, qualunque URL (le letture sono GET)."""
    return [c for c in out["calls"] if c["m"] in ("POST", "PATCH", "PUT", "DELETE")
            and c["url"] != "/api/appointments/availability/check"]


ABBINAMENTO = "#/abbinamenti/19"
ACQUIRENTE = "#/acquirenti/16"
IMMOBILE_VISITE = "#/immobili/30/visite"

APRI = {
    ABBINAMENTO: "await clic('#match-schedule-visit');",
    ACQUIRENTE: ("await clic(\"[data-tab='abbinamenti']\");"
                 "await clic('.match-visit-btn');"),
    IMMOBILE_VISITE: "await clic('#visit-schedule-btn');",
}
BUY = (ABBINAMENTO, ACQUIRENTE)
TUTTI = (ABBINAMENTO, ACQUIRENTE, IMMOBILE_VISITE)


# ---------------------------------------------------------------------------
# A - L'Agenda normale resta identica
# ---------------------------------------------------------------------------

def test_A_agenda_normale_senza_opzioni_invariata(staged):
    out = run(staged, """
      await apriNuovo();
      const s = statoProgramma();
      orario('2031-01-10', '10:00');
      await conferma();
      report({ s });
    """, _rotte("agency_owner"), "#/agenda/lista/2026-09-26")
    s = out["s"]
    assert s["titolo"] == "Nuovo appuntamento"
    assert s["tipo"] == "seller_meeting" and not s["tipoBloccato"]
    assert not s["durataBloccata"] and not s["fineBloccata"]
    assert s["agenteOpzioni"][0] == "Nessuno: salva come richiesta"
    assert all(s[k] for k in ("crm", "cliente", "lead", "stima", "immobile", "luogo", "note"))
    # senza agente: una richiesta, direttamente all'Agenda, nessun controllo
    scritture = _scritture(out)
    assert [c["url"] for c in scritture] == ["/api/appointments"]
    assert scritture[0]["body"]["status"] == "requested"


# ---------------------------------------------------------------------------
# B/C/E/F/G/H/L/M/N - la forma del dialog per ciascun caller
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pagina", TUTTI)
def test_B_C_E_forma_comune_tipo_e_durata_bloccati_agent_se_stesso(staged, pagina):
    out = run(staged, APRI[pagina] + "report({ s: statoProgramma() });", _rotte("agent"), pagina)
    s = out["s"]
    assert s["open"] and s["titolo"] == "Programma visita"
    assert (s["tipo"], s["tipoBloccato"]) == ("buyer_visit", True)
    assert s["durataBloccata"] and s["fineBloccata"]
    assert (s["inizio"], s["fine"]) == ("09:00", "10:00")
    assert s["agenteDisabilitato"] and s["agenteOpzioni"] == ["Io — Anna Agente"]
    assert s["agenteValore"] == "3"


@pytest.mark.parametrize("pagina", BUY)
def test_F_G_H_buy_niente_crm_note_si_luogo_no(staged, pagina):
    s = run(staged, APRI[pagina] + "report({ s: statoProgramma() });", _rotte(), pagina)["s"]
    assert not any(s[k] for k in ("crm", "cliente", "lead", "stima", "immobile"))
    assert s["note"] is True and s["luogo"] is False


def test_L_M_N_property_cliente_e_lead_niente_stima_immobile_note_luogo(staged):
    s = run(staged, APRI[IMMOBILE_VISITE] + "report({ s: statoProgramma() });", _rotte(),
            IMMOBILE_VISITE)["s"]
    assert s["crm"] and s["cliente"] and s["lead"]
    assert not any(s[k] for k in ("stima", "immobile", "note", "luogo"))


# ---------------------------------------------------------------------------
# D - owner/admin/Supreme: vero selettore, agente obbligatorio
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ruolo", ["agency_owner", "agency_admin", "supreme"])
@pytest.mark.parametrize("pagina", TUTTI)
def test_D_owner_admin_supreme_devono_scegliere_senza_agente_nessuna_richiesta(
        staged, pagina, ruolo):
    out = run(staged, APRI[pagina] + """
      const s = statoProgramma();
      orarioFuturo();
      await conferma();
      report({ s });
    """, _rotte(ruolo), pagina)
    s = out["s"]
    assert not s["agenteDisabilitato"]
    assert s["agenteOpzioni"] == ["Scegli un agente", "Anna Agente", "Bruno Collega"]
    assert "Nessuno" not in " ".join(s["agenteOpzioni"])
    assert s["agenteValore"] == ""
    assert out["open"] is True and "agente" in out["error"]
    # errore locale: nessun controllo di disponibilita', nessuna scrittura
    assert _post(out, "/api/appointments/availability/check") == []
    assert _scritture(out) == []


# ---------------------------------------------------------------------------
# I/J/K/Q - BUY: payload esatto, stessa chiave, una POST, controllo prima
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pagina", BUY)
def test_I_J_K_Q_buy_payload_esatto_una_post_dopo_il_controllo(staged, pagina):
    out = run(staged, APRI[pagina] + """
      scegliAgente(4);
      orarioFuturo();
      f('[data-field="notes"]').value = '  citofono B  ';
      await conferma();
      report();
    """, _rotte("agency_owner"), pagina)
    controlli = _post(out, "/api/appointments/availability/check")
    assert len(controlli) == 1
    assert controlli[0]["body"] == {"assigned_user_id": 4,
                                    "start_at": "2031-01-10T10:00:00+01:00",
                                    "end_at": "2031-01-10T11:00:00+01:00"}
    scritture = _scritture(out)
    assert [c["url"] for c in scritture] == [URL_BUY]          # UNA, e solo la BUY
    corpo = scritture[0]["body"]
    assert set(corpo) == {"action", "scheduled_at", "assigned_user_id", "client_request_id",
                          "notes"}
    assert (corpo["action"], corpo["scheduled_at"], corpo["assigned_user_id"],
            corpo["notes"]) == ("visit_scheduled", "2031-01-10T10:00:00+01:00", 4, "citofono B")
    assert a30_5.UUID4.match(corpo["client_request_id"])
    # la POST parte DOPO il controllo
    ordine = [c["url"] for c in out["calls"] if c["m"] == "POST"]
    assert ordine == ["/api/appointments/availability/check", URL_BUY]
    assert out["open"] is False


@pytest.mark.parametrize("pagina", BUY)
def test_I_buy_la_chiave_del_dialog_resta_la_stessa_nel_retry(staged, pagina):
    """Un 500 (o un timeout) e poi un nuovo invio: la stessa `client_request_id`
    arriva due volte alla facade BUY - nessuna seconda chiave inventata."""
    out = run(staged, APRI[pagina] + """
      orarioFuturo();
      await conferma();
      await conferma();
      report();
    """, _rotte("agent", check=(LIBERO, LIBERO), buy=(
        {"status": 500, "body": {"detail": "errore temporaneo"}},
        {"status": 201, "body": {"id": 5}})), pagina)
    scritture = _post(out, URL_BUY)
    assert len(scritture) == 2
    assert scritture[0]["body"]["client_request_id"] == scritture[1]["body"]["client_request_id"]
    assert scritture[0]["body"]["assigned_user_id"] == 3        # l'agent e' se stesso


@pytest.mark.parametrize("pagina", BUY)
def test_buy_senza_note_niente_notes(staged, pagina):
    out = run(staged, APRI[pagina] + "orarioFuturo(); await conferma(); report();",
              _rotte("agent"), pagina)
    assert set(_post(out, URL_BUY)[0]["body"]) == {
        "action", "scheduled_at", "assigned_user_id", "client_request_id"}


# ---------------------------------------------------------------------------
# O/P - PROPERTY: payload esatto, una POST
# ---------------------------------------------------------------------------

def test_O_P_property_payload_esatto_con_cliente_e_lead(staged):
    out = run(staged, APRI[IMMOBILE_VISITE] + """
      scegliAgente(4);
      await scegliCliente(0);
      await wait(50);
      const lead = f('[data-field="lead"]'); lead.value = '501'; lead.dispatch('change');
      orarioFuturo('15:30');
      await conferma();
      report();
    """, _rotte("agency_admin"), IMMOBILE_VISITE)
    scritture = _scritture(out)
    assert [c["url"] for c in scritture] == [URL_PROPERTY]
    corpo = scritture[0]["body"]
    assert set(corpo) == {"scheduled_at", "status", "assigned_user_id", "client_request_id",
                          "contact_id", "lead_id"}
    assert (corpo["scheduled_at"], corpo["status"], corpo["assigned_user_id"],
            corpo["contact_id"], corpo["lead_id"]) == (
        "2031-01-10T15:30:00+01:00", "scheduled", 4, 41, 501)
    assert a30_5.UUID4.match(corpo["client_request_id"])


def test_O_property_senza_cliente_solo_i_quattro_campi(staged):
    out = run(staged, APRI[IMMOBILE_VISITE] + "orarioFuturo(); await conferma(); report();",
              _rotte("agent"), IMMOBILE_VISITE)
    corpo = _post(out, URL_PROPERTY)[0]["body"]
    assert set(corpo) == {"scheduled_at", "status", "assigned_user_id", "client_request_id"}
    assert "Visita programmata" in out["content"]


# ---------------------------------------------------------------------------
# R/S - conflitto e doppio invio
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pagina", TUTTI)
def test_R_conflitto_ferma_la_post_e_mostra_le_alternative(staged, pagina):
    out = run(staged, APRI[pagina] + "orarioFuturo(); await conferma(); report();",
              _rotte("agent", check=(OCCUPATO,)), pagina)
    assert _scritture(out) == []
    assert out["open"] is True
    assert "ORARIO NON DISPONIBILE" in out["status"]
    assert len(out["alt"]) == 2


@pytest.mark.parametrize("pagina", TUTTI)
def test_S_doppio_click_una_sola_post(staged, pagina):
    out = run(staged, APRI[pagina] + """
      orarioFuturo();
      f('form').dispatch('submit');
      f('form').dispatch('submit');
      await wait();
      report();
    """, _rotte("agent"), pagina)
    assert len(_scritture(out)) == 1


@pytest.mark.parametrize("pagina", TUTTI)
def test_orario_passato_nessuna_post(staged, pagina):
    out = run(staged, APRI[pagina] + """
      orario('2020-01-10', '10:00');
      await conferma();
      report();
    """, _rotte("agent"), pagina)
    assert _scritture(out) == [] and "passato" in out["error"]


# ---------------------------------------------------------------------------
# T/U - il modal legacy: storico si', riga proiettata solo esito
# ---------------------------------------------------------------------------

LEGACY_HELPERS = """
const ld = () => C().querySelector('#visit-dialog');
const riga = (id) => C().querySelector(`tr[data-row-id="${id}"]`);
"""


def test_T_legacy_storico_resta_operativo_e_non_crea_visite_future(staged):
    out = run(staged, LEGACY_HELPERS + """
      await clic('#visit-new-btn');
      const titolo = ld().querySelector('h3').textContent;
      const d = ld().querySelector('#visit-scheduled-at');
      d.value = '2031-01-10T10:00';
      const st = ld().querySelector('#visit-status'); st.value = 'scheduled';
      ld().querySelector('#visit-form').dispatch('submit'); await wait();
      const errore = ld().querySelector('#visit-form-error').textContent;
      const primaPost = __calls.filter((c) => (c.options.method || 'GET') !== 'GET').length;
      d.value = '2026-09-01T10:00'; st.value = 'completed';
      ld().querySelector('#visit-form').dispatch('submit'); await wait();
      report({ titolo, errore, primaPost });
    """, _rotte("agency_owner", visita=(
        {"status": 201, "body": {**VISITA_LEGACY, "id": 74}},)), IMMOBILE_VISITE)
    assert out["titolo"] == "Registra visita passata"
    assert "Programma visita" in out["errore"] and out["primaPost"] == 0
    scritture = _scritture(out)
    assert [c["url"] for c in scritture] == [URL_PROPERTY]
    assert scritture[0]["body"]["status"] == "completed"
    assert "assigned_user_id" not in scritture[0]["body"]


def test_T2_legacy_edit_riga_storica_invariato(staged):
    out = run(staged, LEGACY_HELPERS + """
      riga(71).querySelector('.visit-edit-btn').dispatch('click'); await wait();
      const campi = ['#visit-scheduled-at', '#visit-status', '#visit-assigned-to']
        .map((s) => ld().querySelector(s).disabled === true);
      const elimina = !!riga(71).querySelector('.visit-remove-btn');
      ld().querySelector('#visit-feedback').value = 'molto interessato';
      ld().querySelector('#visit-form').dispatch('submit'); await wait();
      report({ campi, elimina });
    """, _rotte("agency_owner"), IMMOBILE_VISITE)
    assert out["campi"] == [False, False, False] and out["elimina"] is True
    patch = [c for c in out["calls"] if c["m"] == "PATCH"]
    assert len(patch) == 1 and patch[0]["url"] == "/api/property/visits/71"
    assert {"scheduled_at", "status", "outcome", "feedback", "rating"} <= set(patch[0]["body"])


def test_U_riga_proiettata_solo_esito_niente_data_stato_elimina(staged):
    out = run(staged, LEGACY_HELPERS + """
      const r = riga(72);
      const bottoni = r.querySelectorAll('button').map((b) => b.textContent.trim());
      r.querySelector('.visit-edit-btn').dispatch('click'); await wait();
      const campi = ['#visit-scheduled-at', '#visit-status', '#visit-assigned-to']
        .map((s) => ld().querySelector(s).disabled === true);
      const cambiaCliente = !!ld().querySelector('#visit-contact-change-btn');
      ld().querySelector('#visit-outcome').value = 'interessato';
      ld().querySelector('#visit-rating').value = '4';
      ld().querySelector('#visit-form').dispatch('submit'); await wait();
      report({ bottoni, campi, cambiaCliente });
    """, _rotte("agency_owner"), IMMOBILE_VISITE)
    assert out["bottoni"] == ["Registra esito"]                 # niente Elimina
    assert out["campi"] == [True, True, True] and out["cambiaCliente"] is False
    patch = [c for c in out["calls"] if c["m"] == "PATCH"]
    assert len(patch) == 1
    assert patch[0]["body"] == {"outcome": "interessato", "feedback": None, "rating": 4}
