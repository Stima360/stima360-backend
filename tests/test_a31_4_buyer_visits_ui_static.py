"""A31-4 - regole strutturali: i tre caller della OS Shell programmano una
visita SOLO dal dialog condiviso dell'Agenda, e l'Agenda non conosce BUY o
immobili.

Senza node e senza database: si legge il codice VERO. La prova eseguita e'
in `test_a31_4_buyer_visits_ui_runtime.py`; il backend in
`test_a31_4_legacy_lane_closed_postgres.py`.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
DIALOGS = ASSETS / "components" / "agenda" / "agenda-dialogs.js"
ABBINAMENTO = ASSETS / "views" / "abbinamento-dettaglio.js"
ACQUIRENTE = ASSETS / "views" / "acquirente-dettaglio.js"
IMMOBILE = ASSETS / "views" / "immobile-dettaglio.js"
CALLER = (ABBINAMENTO, ACQUIRENTE, IMMOBILE)
CALLER_BUY = (ABBINAMENTO, ACQUIRENTE)
AGENDA_MODULI = tuple(sorted(
    list((ASSETS / "agenda").glob("*.js")) + list((ASSETS / "components" / "agenda").glob("*.js"))
    + list((ASSETS / "views" / "agenda").glob("*.js"))))


def _testo(f: Path) -> str:
    return f.read_text(encoding="utf-8")


def _senza_commenti(testo: str) -> str:
    testo = re.sub(r"/\*.*?\*/", "", testo, flags=re.S)
    return "\n".join(r for r in testo.splitlines() if not r.strip().startswith("//"))


def _blocco(testo: str, inizio: str) -> str:
    """Il corpo di una funzione dall'intestazione alla prima riga '  }' o '}'."""
    parte = testo[testo.index(inizio):]
    fine = re.search(r"\n\}\n|\n  \}\n", parte)
    return parte[:fine.end()]


def _opzioni(f: Path) -> str:
    codice = _senza_commenti(_testo(f))
    return codice[codice.index("openCreateDialog(dialogEl, {"):].split("\n    });", 1)[0]


# ---------------------------------------------------------------------------
# V - l'Agenda non conosce BUY, immobili, visite
# ---------------------------------------------------------------------------

def test_V_nessun_literal_visits_ne_buy_nei_moduli_agenda():
    assert len(AGENDA_MODULI) >= 8
    for f in AGENDA_MODULI:
        codice = _senza_commenti(_testo(f))
        for vietato in ("/visits", "/api/buy", "/api/property/properties/", "/decision",
                        "match_id", "buy_request_id", "visit_scheduled"):
            assert vietato not in codice, (f.name, vietato)


def test_dialog_opzioni_generiche_con_default_invariati():
    codice = _senza_commenti(_testo(DIALOGS))
    firma = codice[codice.index("export function openCreateDialog(dialogEl, {"):].split("}) {", 1)[0]
    for default in ("title = 'Nuovo appuntamento'", "appointmentType = null",
                    "lockAppointmentType = false", "durationMinutes: durataFissa = null",
                    "lockDuration = false", "requireAgent = false", "crmMode = 'full'",
                    "showLocation = true", "showNotes = true", "submitAppointment = null"):
        assert default in firma, default
    # una sola scrittura: quella dell'Agenda OPPURE quella del chiamante
    assert codice.count(
        "return submitAppointment ? submitAppointment({ ...corpo }) : createAppointment(corpo);") == 1
    assert codice.count("createAppointment(") == 1
    # l'agente obbligatorio si controlla PRIMA della disponibilita'
    invio = codice[codice.index("if (requireAgent && !idAgente)"):]
    assert invio.index("throw new Error(MSG_AGENTE_OBBLIGATORIO)") < invio.index("disponibilePrima(")
    # nessun "Nessuno" con requireAgent, e l'agent resta "Io" (canAssignRecords)
    assert "vuoto: requireAgent ? 'Scegli un agente' : 'Nessuno: salva come richiesta'" in codice
    assert codice.count("canAssignRecords(session)") == 3


# ---------------------------------------------------------------------------
# I tre caller
# ---------------------------------------------------------------------------

def test_i_tre_caller_usano_il_dialog_agenda_con_agente_obbligatorio():
    for f in CALLER:
        opzioni = _opzioni(f)
        for attesa in ("title: 'Programma visita'", "appointmentType: 'buyer_visit'",
                       "lockAppointmentType: true", "durationMinutes: 60", "lockDuration: true",
                       "requireAgent: true", "showLocation: false", "session: getSession()"):
            assert attesa in opzioni, (f.name, attesa)
        codice = _senza_commenti(_testo(f))
        assert "const esito = await getAgents();" in codice, f.name
        assert codice.count("openCreateDialog(dialogEl, {") == 1, f.name


def test_buy_e_property_configurazione_crm_note():
    for f in CALLER_BUY:
        opzioni = _opzioni(f)
        assert "crmMode: 'none'" in opzioni and "showNotes: true" in opzioni, f.name
    opzioni = _opzioni(IMMOBILE)
    assert "crmMode: 'contact_lead'" in opzioni and "showNotes: false" in opzioni


def test_nessun_datetime_local_standalone_per_programmare_una_visita():
    for f in CALLER:
        codice = _senza_commenti(_testo(f))
        for vecchio in ('id="visit-schedule-at"', 'name="scheduled_at"',
                        "match-decision-schedule-field", "new Date(raw)"):
            assert vecchio not in codice, (f.name, vecchio)
    # Il solo datetime-local di visita rimasto e' quello del modal legacy
    # dell'immobile (storico / esito): non crea visite future da svolgere.
    codice = _senza_commenti(_testo(IMMOBILE))
    assert codice.count('id="visit-scheduled-at"') == 1
    assert "if (!isEdit && isFutureOpenVisit(payload)) {" in codice
    for f in CALLER_BUY:
        assert "datetime-local" not in _blocco(_senza_commenti(_testo(f)),
                                               "async function openVisitScheduleDialog("), f.name


def test_i_due_caller_buy_mandano_lo_stesso_payload():
    corpi = {f.name: _blocco(_senza_commenti(_testo(f)), "export function visitDecisionPayload(corpo) {")
             for f in CALLER_BUY}
    assert len(set(corpi.values())) == 1, corpi
    corpo = next(iter(corpi.values()))
    assert set(re.findall(r"^\s{4}(\w+):", corpo, re.M)) == {
        "action", "scheduled_at", "assigned_user_id", "client_request_id"}
    assert "if (corpo.notes) payload.notes = corpo.notes;" in corpo
    for vietato in ("end_at", "property_id", "contact_id", "lead_id", "assigned_to",
                    "status", "appointment_type"):
        assert vietato not in corpo, vietato


def test_property_payload():
    corpo = _blocco(_senza_commenti(_testo(IMMOBILE)), "export function propertyVisitPayload(corpo) {")
    assert set(re.findall(r"^\s{4}(\w+):", corpo, re.M)) == {
        "scheduled_at", "status", "assigned_user_id", "client_request_id"}
    assert "status: 'scheduled'" in corpo
    assert "if (corpo.contact_id) payload.contact_id = corpo.contact_id;" in corpo
    assert "if (corpo.lead_id) payload.lead_id = corpo.lead_id;" in corpo
    for vietato in ("end_at", "location_text", "notes", "assigned_to", "outcome", "feedback",
                    "rating", "created_by"):
        assert vietato not in corpo, vietato


def test_una_sola_scrittura_nessuna_post_diretta_agli_appuntamenti():
    attese = {ABBINAMENTO: "`/api/buy/requests/${match.buy_request_id}/matches/${matchId}/decision`",
              ACQUIRENTE: "`/api/buy/requests/${requestId}/matches/${match.id}/decision`",
              IMMOBILE: "`/api/property/properties/${property.id}/visits`"}
    for f, url in attese.items():
        codice = _senza_commenti(_testo(f))
        assert "/api/appointments" not in codice, f.name
        assert "createAppointment" not in codice, f.name
        opzioni = _opzioni(f)
        invio = opzioni[opzioni.index("submitAppointment: (corpo) => {"):opzioni.index("onDone:")]
        assert invio.count("apiPost(") == 1 and url in invio, f.name
        assert "client_request_id" not in invio, f.name       # la chiave e' quella del dialog


def test_riga_proiettata_solo_esito():
    codice = _senza_commenti(_testo(IMMOBILE))
    azioni = codice[codice.index("function renderVisitActionButtons(v, visitRemoveConfirm) {"):
                    codice.index("function renderVisite(")]
    ramo = azioni[azioni.index("if (v.appointment_id != null) {"):azioni.index("const confirming")]
    assert "visit-edit-btn" in ramo and "visit-remove-btn" not in ramo
    assert "if (proiettata) payload = legacyOutcomePayload(payload);" in codice
    assert "const LEGACY_OUTCOME_FIELDS = ['outcome', 'feedback', 'rating'];" in codice
    assert "if (!isEdit && isFutureOpenVisit(payload)) {" in codice
