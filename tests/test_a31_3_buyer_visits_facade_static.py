"""A31-3 - regole strutturali delle facade BUY/PROPERTY e compatibilita' UI.

Senza database: si legge il codice e i caller JS VERI.

COMPATIBILITA' UI (A31-3 §1, §16). Nessun caller attuale manda
`assigned_user_id`: gli agent restano pienamente funzionanti (l'agente e'
la sessione stessa, facade ATTIVA), owner/admin/Supreme restano sul percorso
legacy dichiarato finche' la UI non avra' un selettore agente (A31-4). Se
un caller cominciasse a mandarlo, `test_02` cade e ricorda di spegnere il
percorso legacy nello stesso gate.

SENTINELLA AGGIORNATA DA A31-4: e' successo. I tre caller della OS Shell
(abbinamento, acquirente, immobile) mandano `assigned_user_id` dal corpo del
dialog Agenda, e nello stesso gate il percorso legacy scoped owner/admin e'
spento (BUY: nessun fallback; PROPERTY: visita futura aperta senza agente =
errore). `test_02` ora difende questo stato.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CALLER_DECISIONE = (
    "static/buy_admin/assets/app.js",
    "static/os_shell/assets/views/acquirente-dettaglio.js",
    "static/os_shell/assets/views/abbinamento-dettaglio.js",
)
CALLER_VISITE = (
    "static/os_shell/assets/views/immobile-dettaglio.js",
    "static/property_admin/assets/app.js",
)
#: A31-4: i caller della OS Shell che programmano una visita dal dialog Agenda.
CALLER_OS_SHELL = (
    "static/os_shell/assets/views/abbinamento-dettaglio.js",
    "static/os_shell/assets/views/acquirente-dettaglio.js",
    "static/os_shell/assets/views/immobile-dettaglio.js",
)
#: A31-4: le vecchie app admin restano fuori (debito dichiarato): non
#: improvvisano un agente, e il backend rifiuta una loro visita futura aperta.
CALLER_LEGACY_ADMIN = (
    "static/buy_admin/assets/app.js",
    "static/property_admin/assets/app.js",
)


def _testo(percorso: str) -> str:
    return (ROOT / percorso).read_text(encoding="utf-8")


def _codice(percorso: str) -> str:
    testo = _testo(percorso)
    testo = re.sub(r'""".*?"""', "", testo, flags=re.S)
    return "\n".join(r.split("#", 1)[0] for r in testo.splitlines())


def _funzione(codice: str, nome: str) -> str:
    return codice.split(f"def {nome}(", 1)[1].split("\ndef ", 1)[0]


def _campi(modello) -> set:
    campi = getattr(modello, "model_fields", None) or getattr(modello, "__fields__")
    return set(campi)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def test_01_i_payload_ui_attuali_restano_validi():
    from buy.schemas import MatchDecision
    from property.schemas import VisitCreate, VisitUpdate

    decisione = _campi(MatchDecision)
    assert {"action", "scheduled_at", "notes", "reason_code", "property_visit_id"} <= decisione
    corpo = _testo("static/os_shell/assets/views/immobile-dettaglio.js").split(
        "function buildVisitPayload", 1)[1].split("\nfunction ", 1)[0]
    inviati = set(re.findall(r"\b(status|contact_id|outcome|feedback|assigned_to|lead_id|"
                             r"scheduled_at|rating)\b\s*[:=]", corpo))
    inviati |= set(re.findall(r"payload\.(\w+)\s*=", corpo))
    assert inviati == {"status", "contact_id", "outcome", "feedback", "assigned_to", "lead_id",
                       "scheduled_at", "rating"}, inviati
    assert inviati <= _campi(VisitCreate) and inviati <= _campi(VisitUpdate)
    # campi nuovi solo facoltativi: nessun caller e' obbligato a conoscerli
    assert MatchDecision(action="visit_scheduled",
                         scheduled_at="2031-01-10T10:00:00+01:00").assigned_user_id is None
    assert VisitCreate(scheduled_at="2031-01-10T10:00:00+01:00").assigned_user_id is None


def _js_senza_commenti(percorso: str) -> str:
    return "\n".join(r for r in _testo(percorso).splitlines() if not r.strip().startswith("//"))


def test_02_sentinella_a31_4_agente_solo_dal_dialog_agenda_e_legacy_spento():
    """SENTINELLA AGGIORNATA DA A31-4 (era: "nessun caller invia ancora un
    agente"). L'agente arriva SOLO dal corpo del dialog Agenda nei tre caller
    della OS Shell; le app admin legacy non lo improvvisano; il backend scoped
    non ha piu' il fallback owner/admin legacy."""
    for percorso in CALLER_OS_SHELL:
        codice = _js_senza_commenti(percorso)
        assert "openCreateDialog(" in codice and "requireAgent: true" in codice, percorso
        # una sola riga: `assigned_user_id: corpo.assigned_user_id`
        assert codice.count("assigned_user_id") == 2, percorso
        assert re.findall(r"\bassigned_user_id:\s*([\w.]+)", codice) == ["corpo.assigned_user_id"]
        assert re.findall(r"\bclient_request_id:\s*([\w.]+)", codice) == ["corpo.client_request_id"]
    for percorso in CALLER_LEGACY_ADMIN:
        assert "assigned_user_id" not in _testo(percorso), percorso
    buy = _codice("buy/repository.py")
    assert "_schedule_match_visit_scoped_legacy" not in buy
    assert "raise ValidationError(_VISITA_SENZA_AGENTE)" in _funzione(buy, "schedule_match_visit_scoped")
    immobili = _codice("property/repository.py")
    assert "raise ValidationError(_VISITA_SENZA_AGENTE)" in _funzione(immobili, "_visita_da_agenda")


def test_03_url_e_router_invariati():
    buy = _testo("buy/router.py")
    assert "@router.post('/requests/{request_id}/matches/{match_id}/decision',status_code=201)" in buy
    assert "@router.post('/requests/{request_id}/interactions',status_code=201)" in buy
    assert buy.count("Depends(legacy_basic_agency_context)") == 23
    immobili = _testo("property/router.py")
    for rotta in ("@router.post('/properties/{property_id}/visits',status_code=201)",
                  "@router.patch('/visits/{visit_id}')",
                  "@router.delete('/visits/{visit_id}',status_code=204)"):
        assert rotta in immobili, rotta
    # un permesso negato dell'Agenda e' un 403, non un 500
    for testo in (buy, immobili):
        assert "except PermissionDenied as e:raise HTTPException(403,str(e))" in testo


# ---------------------------------------------------------------------------
# APPOINTMENTS: una sola logica di creazione
# ---------------------------------------------------------------------------

def test_04_una_sola_creazione_due_transazioni_possibili():
    codice = _codice("appointments/service.py")
    assert codice.count("def _creazione(") == 1
    assert "repository.insert_appointment(cur, valori" in _funzione(codice, "_creazione")
    idempotente = _funzione(codice, "create_appointment_idempotent")
    assert "_in_transazione(_creazione(ctx, payload))" in idempotente
    con_cursore = _funzione(codice, "create_appointment_with_cursor")
    assert "_creazione(ctx, payload)(cur)" in con_cursore
    assert "_traduci_esclusione(exc)" in con_cursore
    for vietato in ("core_cursor", "commit", "_in_transazione", "insert_appointment"):
        assert vietato not in con_cursore, vietato


def test_05_la_facade_usa_il_cursore_del_chiamante():
    facade = _codice("buyer_visits/facade.py")
    assert "_agenda.create_appointment_with_cursor(ctx, cur, corpo)" in facade
    for vietato in ("create_appointment_idempotent", "INSERT INTO", "commit(",
                    "on_appointment_mutation"):
        assert vietato not in facade, vietato


# ---------------------------------------------------------------------------
# BUY
# ---------------------------------------------------------------------------

def test_06_buy_la_programmazione_passa_dall_agenda():
    codice = _testo("buy/repository.py")            # con l'SQL: non si tolgono le stringhe
    facade = _funzione(codice, "schedule_match_visit_scoped")
    assert "_visite_facade.schedule(" in facade
    assert "_visite_facade.resolve_agent(" in facade
    assert "INSERT INTO property_visits" not in facade
    assert "_insert_visit_scheduled(" in facade
    assert facade.count("core_cursor(") == 1
    # SENTINELLA AGGIORNATA DA A31-4: lo scoped legacy e' spento. L'unico
    # INSERT diretto rimasto e' l'unscoped storico `schedule_match_visit`,
    # che nessuna route raggiunge (test_08).
    assert "_schedule_match_visit_scoped_legacy" not in codice
    assert "raise ValidationError(_VISITA_SENZA_AGENTE)" in facade
    assert codice.count("INSERT INTO property_visits") == 1
    assert "INSERT INTO property_visits" in _funzione(codice, "schedule_match_visit")


def test_07_d8_l_interazione_generica_non_crea_visit_scheduled():
    codice = _codice("buy/repository.py")
    for funzione in ("add_interaction_scoped", "update_interaction_scoped"):
        assert "BuyerVisitScheduleViaAgenda(" in _funzione(codice, funzione), funzione


def test_08_nessun_secondo_scrittore_non_dichiarato():
    """Inventario degli INSERT diretti su `property_visits` fuori dai test:
    la proiezione A31-2, il percorso legacy BUY unscoped dichiarato (A31-4:
    lo scoped e' spento) e lo script E2E storico. `schedule_match_visit` (senza ctx) non ha caller di
    produzione: lo chiama solo `buy.service.match_decision`, che nessuna
    route usa (il router usa `match_decision_scoped`)."""
    trovati = set()
    for file in ROOT.rglob("*.py"):
        relativo = file.relative_to(ROOT).as_posix()
        if relativo.startswith(("tests/", ".git/")) or "/." in relativo:
            continue
        if "INSERT INTO property_visits" in file.read_text(encoding="utf-8", errors="ignore"):
            trovati.add(relativo)
    assert trovati == {"buy/repository.py", "buyer_visits/projection.py", "run_buy_021_e2e.py"}
    chiamanti = set()
    for file in ROOT.rglob("*.py"):
        relativo = file.relative_to(ROOT).as_posix()
        if relativo.startswith(("tests/", ".git/")):
            continue
        testo = _codice(relativo)
        if re.search(r"\bschedule_match_visit\(", testo) and relativo != "buy/repository.py":
            chiamanti.add(relativo)
        for trovato in re.finditer(r"\bmatch_decision\(", testo):
            if not testo[:trovato.start()].endswith("def "):
                chiamanti.add(relativo + ":match_decision")
    assert chiamanti == {"buy/service.py"}, chiamanti
    assert "service.match_decision_scoped" in _testo("buy/router.py")


# ---------------------------------------------------------------------------
# PROPERTY
# ---------------------------------------------------------------------------

def test_09_property_post_patch_delete():
    codice = _codice("property/repository.py")
    crea = _funzione(codice, "add_visit")
    assert "_visite_facade.schedule(" in crea and "_visite_facade.resolve_agent(" in crea
    assert "create_child(ctx, 'property_visits'" in crea         # il percorso legacy dichiarato
    modifica = _funzione(codice, "update_visit")
    assert "_visite_regole.check_patch(" in modifica
    elimina = _funzione(codice, "delete_child")
    assert "_visite_regole.check_delete(" in elimina


def test_10_regole_pure_d5_d7():
    from datetime import datetime, timedelta, timezone

    from buyer_visits import errors, guards

    adesso = datetime(2031, 1, 1, 12, tzinfo=timezone.utc)
    quando = adesso + timedelta(days=3)
    proiettata = {"appointment_id": 7, "scheduled_at": quando, "status": "scheduled",
                  "contact_id": 1, "lead_id": None, "assigned_to": "Luca Test"}
    guards.check_patch(proiettata, {"outcome": "x", "feedback": "y", "rating": 3,
                                    "scheduled_at": quando.replace(second=41),
                                    "status": "scheduled", "contact_id": 1}, adesso)
    for cambio in ({"scheduled_at": quando + timedelta(minutes=1)}, {"status": "completed"},
                   {"contact_id": 2}, {"lead_id": 3}, {"assigned_to": "Altro"},
                   {"property_id": 9}, {"created_by": "x"}):
        try:
            guards.check_patch(proiettata, cambio, adesso)
        except errors.BuyerVisitManagedByAgenda:
            continue
        raise AssertionError(cambio)
    try:
        guards.check_delete(proiettata)
        raise AssertionError("delete ammesso")
    except errors.BuyerVisitManagedByAgenda:
        pass
    guards.check_delete({"appointment_id": None})
    legacy = {"appointment_id": None, "scheduled_at": adesso - timedelta(days=3),
              "status": "completed"}
    guards.check_patch(legacy, {"outcome": "ok", "status": "cancelled"}, adesso)
    try:
        guards.check_patch(legacy, {"scheduled_at": quando, "status": "confirmed"}, adesso)
        raise AssertionError("riapertura ammessa")
    except errors.BuyerVisitLegacyReopen:
        pass
