"""CRM-OPS-3 - ACQUISIZIONI: le prove senza database.

  A. Catalogo e migration: gli enum del backend sono lo specchio dei CHECK
     della 081; la 081 e' valida per il runner, additiva, con una down sicura.
  B. Confini del codice: il package nuovo non e' il ponte LMC-15, l'Agenda
     chiama i suoi hook nella propria transazione, nessuna rotta accetta
     `agency_id`, `acquired` non si imposta a mano.
  C. PROPERTY: la regola "nessun incarico nuovo senza acquisizione" nel
     service, con gli incarichi storici al riparo (grandfathering).
  D. Shell eseguita (stub DOM, nessun database): elenco, creazione con il
     dialog dell'Agenda e UNA sola scrittura, scheda, form e scheda Immobile.

Il giro completo su PostgreSQL vero e' in
`tests/test_crm_ops_3_acquisitions_postgres.py`.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
from datetime import date
from pathlib import Path

import pytest

from acquisitions import enums
from core.exceptions import ValidationError
from operator_auth.context import OperatorContext

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
PACCHETTO = ROOT / "acquisitions"
SU = (ROOT / "migrations" / "081_crm_ops_3_acquisitions.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / "081_crm_ops_3_acquisitions_down.sql").read_text(encoding="utf-8")
MESSAGGIO = "L’incarico può essere generato solo da un’acquisizione."


def _lista_check(nome):
    """I valori di un CHECK ... IN (...) della 081, nell'ordine."""
    trovato = re.search(rf"CONSTRAINT {nome} CHECK \((.*?)\)\)", SU, re.S)
    assert trovato, nome
    return re.findall(r"'([a-z_0-9]+)'", trovato.group(1))


# ---------------------------------------------------------------------------
# A - catalogo e migration
# ---------------------------------------------------------------------------

def test_a01_gli_enum_sono_lo_specchio_dei_check():
    assert _lista_check("acquisitions_status_chk") == list(enums.STATUSES)
    assert _lista_check("acquisitions_lost_reason_chk") == list(enums.LOST_REASONS)
    assert _lista_check("acquisitions_sale_timing_chk") == list(enums.SALE_TIMINGS)
    assert _lista_check("acquisition_events_type_chk") == list(enums.EVENT_TYPES)
    for catalogo, etichette in ((enums.STATUSES, enums.STATUS_LABELS_IT),
                                (enums.LOST_REASONS, enums.LOST_REASON_LABELS_IT),
                                (enums.SALE_TIMINGS, enums.SALE_TIMING_LABELS_IT),
                                (enums.SOURCES, enums.SOURCE_LABELS_IT),
                                (enums.EVENT_TYPES, enums.EVENT_LABELS_IT)):
        assert set(etichette) == set(catalogo)


def test_a02_pipeline_e_transizioni_lato_server():
    # La pipeline parte dall'appuntamento: un'acquisizione nasce SEMPRE con il
    # suo appuntamento Agenda (corpo obbligatorio, `appointment_id` NOT NULL),
    # quindi non esistono stati "prima" (nessun `new`, nessun `contacted`).
    assert enums.STATUSES == ("appointment_set", "inspection_done", "valuation_presented",
                              "mandate_negotiation", "acquired", "lost")
    assert enums.STATUS_LABELS_IT == {
        "appointment_set": "Appuntamento fissato", "inspection_done": "Sopralluogo effettuato",
        "valuation_presented": "Valutazione presentata",
        "mandate_negotiation": "Trattativa incarico", "acquired": "Acquisita", "lost": "Persa"}
    assert enums.INITIAL_STATUS == "appointment_set" and enums.INITIAL_STATUS in enums.STATUSES
    assert enums.TERMINAL_STATUSES == ("acquired", "lost")
    assert enums.OPEN_STATUSES == ("appointment_set", "inspection_done", "valuation_presented",
                                   "mandate_negotiation")
    assert enums.MANDATE_FROM_STATUSES == ("inspection_done", "valuation_presented",
                                           "mandate_negotiation")
    assert enums.MANUAL_TRANSITIONS == {
        "appointment_set": (),
        "inspection_done": ("valuation_presented", "mandate_negotiation"),
        "valuation_presented": ("mandate_negotiation",),
        "mandate_negotiation": (), "acquired": (), "lost": ()}
    assert set(enums.MANUAL_TRANSITIONS) == set(enums.STATUSES)
    # l'unica via per `inspection_done` e' il COMPLETE dell'Agenda
    assert not any("inspection_done" in v for v in enums.MANUAL_TRANSITIONS.values())
    for da, verso in enums.MANUAL_TRANSITIONS.items():
        assert "acquired" not in verso and "lost" not in verso, da     # solo i loro endpoint
        for v in verso:                                                # mai indietro
            assert enums.STATUSES.index(v) > enums.STATUSES.index(da), (da, v)
    assert enums.MANUAL_TRANSITIONS["appointment_set"] == ()           # lo decide l'Agenda
    assert enums.AGENDA_COMPLETED_ADVANCES == {"appointment_set": "inspection_done"}
    assert enums.REPLACEABLE_APPOINTMENT_STATUSES == ("cancelled", "no_show")
    assert enums.APPOINTMENT_TYPE == "seller_meeting" and enums.DEFAULT_DURATION_MINUTES == 60
    from appointments.enums import default_duration_minutes
    assert default_duration_minutes(enums.APPOINTMENT_TYPE) == enums.DEFAULT_DURATION_MINUTES


def test_a03_la_081_e_valida_per_il_runner_e_l_ultima():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner

    trovate = {m.version: m for m in runner.discover_migrations()}
    m = trovate["081_crm_ops_3_acquisitions"]
    assert runner.validate_migration(m) == [] and m.down_path is not None
    assert not m.non_transactional
    numeri = sorted(x.number for x in trovate.values())
    # SENTINELLA AGGIORNATA DA CRM-OPS-4: la 082 aggiunge a `activities` il
    # legame con l'immobile (`property_id`, storico interazioni), approvata
    # dal design CRM-OPS-4. Si nomina invece di smettere di guardare: la serie
    # resta contigua e qualunque ALTRA migration farebbe ancora fallire.
    # SENTINELLA AGGIORNATA DA CENSIMENTO-1: la 083 crea `buildings` e
    # `property_accessories` e aggiunge a `properties` le colonne NULLABLE del
    # censimento (edificio, pertinenza, catasto, record_kind); additiva, nessun
    # backfill. Si nomina invece di smettere di guardare: la serie resta
    # contigua e qualunque ALTRA migration farebbe ancora fallire.
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 1A: la 084 aggiunge
    # `appointments.cancelled_kind` (NULLABLE, CHECK) ed estende il CHECK dei
    # motivi di perdita con `created_by_mistake`; additiva, nessun backfill.
    # Si nomina invece di smettere di guardare: la serie resta contigua e
    # qualunque ALTRA migration farebbe ancora fallire.
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 2B1: la 085 (Cestino Immobili: properties.deleted_*,
    # record_lifecycle_events), additiva, e' ora l'ultima; la catena si allunga di uno.
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 2B2: la 086 (guardie del Cestino Immobili), additiva, e' ora l'ultima.
    # SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1: la 087 (attributi del sito, provenienza), additiva, e' ora l'ultima.
    # SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1: la 088 (ricezione degli invii del sito, colonne della dettagliata), additiva, e' ora l'ultima.
    assert numeri[-1] == 88 and numeri[-2] == 87 and numeri[-3] == 86 and numeri[-4] == 85 and numeri[-5] == 84 and numeri[-6] == 83 and numeri[-7] == 82 and numeri[-8] == 81 and numeri[-9] == 80


def test_a04_la_up_e_additiva_e_la_down_rifiuta_con_dati():
    corpo = "\n".join(r for r in SU.splitlines() if not r.strip().startswith("--"))
    assert "BEGIN;" not in corpo and "COMMIT;" not in corpo        # la transazione e' del runner
    for vietato in ("DROP TABLE", "DROP COLUMN", "DELETE FROM", "TRUNCATE"):
        assert vietato not in corpo, vietato
    # nessuna riga esistente riscritta: l'unico UPDATE e' nel testo dei trigger
    assert not re.search(r"^\s*UPDATE\s", corpo, re.M)
    assert "ADD COLUMN IF NOT EXISTS acquisition_id BIGINT;" in corpo   # NULLABLE
    assert "idx_acquisitions_open_property" in corpo and "WHERE status NOT IN ('acquired', 'lost')" in corpo
    assert "CONSTRAINT acquisitions_appointment_unq UNIQUE (appointment_id)" in corpo
    assert "USING ERRCODE = 'check_violation'" in corpo
    # la down: BEGIN/COMMIT propri, rifiuta se esiste anche una sola acquisizione
    assert GIU.strip().startswith("--") and "BEGIN;" in GIU and "COMMIT;" in GIU
    assert GIU.index("RAISE EXCEPTION") < GIU.index("DROP TRIGGER")
    assert "SELECT count(*) FROM acquisitions" in GIU


# ---------------------------------------------------------------------------
# B - confini del codice
# ---------------------------------------------------------------------------

def _import(file):
    albero = ast.parse(file.read_text(encoding="utf-8"))
    moduli = set()
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.ImportFrom) and nodo.module:
            moduli.add(nodo.module)
        elif isinstance(nodo, ast.Import):
            moduli.update(a.name for a in nodo.names)
    return moduli


def test_b01_pacchetto_distinto_dal_ponte_lmc15():
    tutti = set()
    for f in PACCHETTO.glob("*.py"):
        tutti |= _import(f)
    assert "main" not in tutti
    assert not [m for m in tutti if m == "acquisition" or m.startswith("acquisition.")]
    # l'unica porta verso l'Agenda e' quella pubblica del suo service/repository
    assert {m for m in tutti if m.startswith("appointments")} <= {
        "appointments", "appointments.enums", "appointments.schemas"}
    assert "from acquisition" not in (ROOT / "main.py").read_text(encoding="utf-8").replace(
        "from acquisition.router", "").replace("from acquisitions.router", "")


def test_b02_l_agenda_chiama_gli_hook_nella_propria_transazione():
    codice = (ROOT / "appointments" / "service.py").read_text(encoding="utf-8")
    assert "from acquisitions import integration as _acquisizioni" in codice
    conteggi = {nome: codice.count(f"_acquisizioni.{nome}(") for nome in
                ("on_status", "on_reschedule", "before_patch", "on_create", "on_reassign",
                 "on_patch")}
    assert conteggi == {"on_status": 3, "on_reschedule": 1, "before_patch": 1,
                        "on_create": 0, "on_reassign": 0, "on_patch": 0}
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 1A: l'hook dell'annullamento
    # sta in `_annulla`, condiviso da annullamento reale e «creato per errore».
    for funzione in ("reschedule_appointment", "_annulla", "complete_appointment",
                     "no_show_appointment", "patch_appointment"):
        corpo = codice[codice.index(f"def {funzione}("):]
        corpo = corpo[:corpo.index("\ndef ", 1)]
        assert "_acquisizioni." in corpo, funzione
        # dopo la proiezione A31-2, prima del mark dirty Google (se c'e')
        assert corpo.index("_visite.") < corpo.index("_acquisizioni."), funzione
        if "_gcal.on_appointment_mutation" in corpo:
            assert corpo.index("_acquisizioni.") < corpo.index("_gcal.on_appointment_mutation")
    # gli hook non aprono transazioni e non fanno commit: sono sul cursore dell'Agenda
    integrazione = PACCHETTO / "integration.py"
    assert "core.database" not in _import(integrazione)
    chiamate = {n.func.attr for n in ast.walk(ast.parse(integrazione.read_text(encoding="utf-8")))
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not chiamate & {"commit", "rollback", "core_cursor"}


def test_b03_nessuna_rotta_accetta_agenzia_o_attore():
    from acquisitions import schemas
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 1A: + MistakeBody, stesse regole.
    for nome in ("AcquisitionCreate", "AcquisitionAppointment", "AcquisitionPatch", "StatusBody",
                 "LostBody", "NewAppointmentBody", "MandateBody", "MistakeBody"):
        modello = getattr(schemas, nome)
        assert modello.model_config.get("extra") == "forbid", nome
        campi = set(modello.model_fields)
        assert not campi & {"agency_id", "created_by_user_id", "actor_user_id", "acquired_at",
                            "acquisition_id", "appointment_id", "appointment_type"}, nome
    assert "status" not in schemas.AcquisitionPatch.model_fields
    router = (PACCHETTO / "router.py").read_text(encoding="utf-8")
    rotte = re.findall(r"@router\.(get|post|patch|put|delete)\(", router)
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 1A: 9 -> 10, POST /{id}/mistake
    # («Segna come creata per errore»), stesso gate, nessuna DELETE.
    assert len(rotte) == 10 and "delete" not in rotte
    assert router.count("ctx: OperatorContext = Depends(require_operator)") == 10
    assert 'APIRouter(prefix="/api/acquisitions"' in router


def test_b04_acquired_solo_dall_incarico():
    from acquisitions import service
    from acquisitions.schemas import StatusBody
    ctx = OperatorContext(user_id=1, agency_id=1, role="agency_owner", is_platform_admin=False,
                          session_id=None, auth_channel="operator_session")
    for stato in ("acquired", "lost"):
        with pytest.raises(Exception) as errore:
            service.change_status(ctx, 1, StatusBody(version=1, status=stato))
        assert getattr(errore.value, "code", None) == "INVALID_TRANSITION"


def test_b05_lo_schema_rifiuta_dati_non_validi():
    import pydantic
    from acquisitions.schemas import AcquisitionCreate, MandateBody
    base = {"property_id": 1, "owner_contact_id": 2,
            "appointment": {"start_at": "2031-01-10T10:00:00+01:00",
                            "client_request_id": "0f8fad5b-d9cb-469f-a165-70867728950e"}}
    AcquisitionCreate.model_validate(base)
    for difetto in ({"asking_price": "-1"}, {"agency_id": 3}, {"status": "acquired"},
                    {"appointment": {"start_at": "2031-01-10T10:00:00+01:00"}}):
        with pytest.raises(pydantic.ValidationError):
            AcquisitionCreate.model_validate({**base, **difetto})
    with pytest.raises(pydantic.ValidationError):
        MandateBody.model_validate({"version": 1, "mandate_type": "Esclusiva",
                                    "mandate_start": "2026-10-01", "mandate_end": "2026-09-01"})


# ---------------------------------------------------------------------------
# C - PROPERTY: la regola nel service
# ---------------------------------------------------------------------------

def _ctx():
    return OperatorContext(user_id=1, agency_id=35, role="agency_owner", is_platform_admin=False,
                           session_id=None, auth_channel="operator_session")


@pytest.fixture
def immobili(monkeypatch):
    from property import repository, service
    stato = {"current": {}, "scritto": None}
    monkeypatch.setattr(repository, "get_property", lambda ctx, i: dict(stato["current"]))
    monkeypatch.setattr(repository, "update_property",
                        lambda ctx, i, data, **kw: stato.__setitem__("scritto", data) or data)
    monkeypatch.setattr(repository, "create_property",
                        lambda ctx, data, **kw: stato.__setitem__("scritto", data) or data)
    return service, stato


def test_c01_nessun_immobile_nasce_con_un_incarico(immobili):
    from property.schemas import PropertyCreate
    service, stato = immobili
    for campi in ({"mandate_end": date(2027, 1, 1)}, {"mandate_type": "Esclusiva"},
                  {"commercial_status": "mandate"}):
        with pytest.raises(ValidationError, match=MESSAGGIO):
            service.create_property(_ctx(), PropertyCreate(**campi))
    assert stato["scritto"] is None
    service.create_property(_ctx(), PropertyCreate(commercial_status="active"))
    assert stato["scritto"]["commercial_status"] == "active"


def test_c02_incarico_storico_rimandato_invariato_o_azzerato_passa(immobili):
    from property.schemas import PropertyUpdate
    service, stato = immobili
    stato["current"] = {"mandate_type": "Esclusiva", "mandate_start": date(2026, 1, 1),
                        "mandate_end": date(2026, 12, 31), "commercial_status": "mandate",
                        "acquisition_id": None}
    # property_admin rimanda tutto a ogni salvataggio, con un altro campo
    service.update_property(_ctx(), 7, PropertyUpdate(
        title="Trilocale", mandate_type="Esclusiva", mandate_start=date(2026, 1, 1),
        mandate_end=date(2026, 12, 31), commercial_status="mandate"))
    service.update_property(_ctx(), 7, PropertyUpdate(mandate_end=None))
    service.update_property(_ctx(), 7, PropertyUpdate(commercial_status="active"))
    with pytest.raises(ValidationError, match=MESSAGGIO):
        service.update_property(_ctx(), 7, PropertyUpdate(mandate_end=date(2027, 6, 30)))


def test_c03_con_acquisizione_d_origine_l_incarico_si_modifica(immobili):
    from property.schemas import PropertyUpdate
    service, stato = immobili
    stato["current"] = {"mandate_type": "Esclusiva", "commercial_status": "mandate",
                        "acquisition_id": 501}
    service.update_property(_ctx(), 7, PropertyUpdate(mandate_type="Non esclusiva",
                                                      mandate_end=date(2027, 3, 31)))
    assert stato["scritto"]["mandate_type"] == "Non esclusiva"
    with pytest.raises(Exception):
        PropertyUpdate(acquisition_id=None)            # mai scollegabile dal form


def test_c04_un_aggiornamento_estraneo_non_legge_ne_rifiuta(immobili):
    from property.schemas import PropertyUpdate
    service, stato = immobili
    stato["current"] = {"commercial_status": "draft", "acquisition_id": None}
    service.update_property(_ctx(), 7, PropertyUpdate(title="Nuovo titolo", internal_notes="x"))
    assert stato["scritto"] == {"title": "Nuovo titolo", "internal_notes": "x"}


# ---------------------------------------------------------------------------
# D - Shell eseguita (stub DOM, nessun database)
# ---------------------------------------------------------------------------

from tests import test_a30_5_create_ui as a30_5  # noqa: E402
from tests import test_a30_13b_quick_booking_ui as a30_13b  # noqa: E402
from tests.test_a30_5_create_ui import staged  # noqa: E402,F401  (fixture riusata)

node = pytest.mark.skipif(a30_5.NODE is None, reason="node non disponibile: livello D NON eseguito (BLOCKED)")

OPZIONI = {
    "statuses": [{"value": s, "label": enums.STATUS_LABELS_IT[s], "terminal": s in enums.TERMINAL_STATUSES}
                 for s in enums.STATUSES],
    "manual_transitions": {k: list(v) for k, v in enums.MANUAL_TRANSITIONS.items()},
    "mandate_from_statuses": list(enums.MANDATE_FROM_STATUSES),
    "lost_reasons": [{"value": r, "label": enums.LOST_REASON_LABELS_IT[r]} for r in enums.LOST_REASONS],
    "sale_timings": [{"value": t, "label": enums.SALE_TIMING_LABELS_IT[t]} for t in enums.SALE_TIMINGS],
    "sources": [{"value": s, "label": enums.SOURCE_LABELS_IT[s]} for s in enums.SOURCES],
    "event_labels": dict(enums.EVENT_LABELS_IT),
    "agents": [{"id": 3, "role": "agent", "name": "Anna Agente", "is_me": False},
               {"id": 4, "role": "agent", "name": "Bruno Collega", "is_me": False}],
    "can_assign": True, "appointment_type": "seller_meeting", "default_duration_minutes": 60,
    "mandate_only_from_acquisition": MESSAGGIO,
}
AGENTI = {"items": [{"id": 3, "name": "Anna Agente", "is_me": False},
                    {"id": 4, "name": "Bruno Collega", "is_me": False}]}
IMMOBILE = {"id": 30, "code": "IMM-30", "title": "Trilocale", "address": "Via Roma", "civic_number": "1",
            "city": "Giulianova", "property_type": "apartment", "commercial_status": "draft",
            "asking_price": "180000.00", "acquisition_id": None, "mandate_type": None,
            "mandate_start": None, "mandate_end": None, "leads": [], "photos": [],
            "documents": [], "visits": [],
            "contacts": [
                {"contact_id": 41, "role": "owner", "is_primary": True, "display_name": "Mario Rossi",
                 "phone": "333 111", "email": "mario@example.test"},
                {"contact_id": 42, "role": "seller", "is_primary": False, "display_name": "Bruno Bianchi",
                 "phone": None, "email": None},
                {"contact_id": 43, "role": "tenant", "is_primary": False, "display_name": "Carla Inquilina",
                 "phone": None, "email": None}]}
SENZA_PROPRIETARI = {**IMMOBILE, "id": 31, "contacts": []}
APPUNTAMENTO = {"id": 900, "status": "scheduled", "appointment_type": "seller_meeting",
                "start_at": "2031-01-10T10:00:00+01:00", "end_at": "2031-01-10T11:00:00+01:00",
                "assigned_user_id": 3, "notes": "Citofono Rossi", "location_text": None,
                "version": 1, "agent_name": "Anna Agente"}


def _acquisizione(**extra):
    riga = {"id": 501, "status": "appointment_set", "status_label": "Appuntamento fissato",
            "property_id": 30, "owner_contact_id": 41, "assigned_agent_id": 3,
            "appointment_id": 900, "lead_id": None, "version": 2, "asking_price": "185000.00",
            "valuation_price": None, "sale_timing": "within_3_months",
            "sale_timing_label": "Entro 3 mesi", "source": "referral", "source_label": "Segnalazione",
            "notes": "Vuole vendere entro l'estate", "lost_reason": None, "lost_reason_label": None,
            "lost_notes": None, "lost_at": None, "acquired_at": None, "agent_name": "Anna Agente",
            "property": {k: IMMOBILE[k] for k in ("id", "code", "title", "address", "civic_number",
                                                  "city", "property_type", "commercial_status",
                                                  "acquisition_id", "asking_price", "mandate_type",
                                                  "mandate_start", "mandate_end")},
            "owners": [{"contact_id": 41, "display_name": "Mario Rossi", "phone": "333 111",
                        "email": "mario@example.test", "roles": ["owner"], "is_primary": True,
                        "is_main": True},
                       {"contact_id": 42, "display_name": "Bruno Bianchi", "phone": None,
                        "email": None, "roles": ["seller"], "is_primary": False, "is_main": False}],
            "appointment": APPUNTAMENTO, "lead": None,
            "events": [{"id": 1, "event_type": "created", "from_status": None,
                        "to_status": "appointment_set", "occurred_at": "2026-10-01T10:00:00+02:00",
                        "actor_name": "Giorgio"}],
            "allowed_actions": {"edit": True, "reassign": True, "transitions": [], "lost": True,
                                "new_appointment": False, "mandate": False}}
    riga.update(extra)
    return riga


RIGA_ELENCO = {"id": 501, "status": "appointment_set", "status_label": "Appuntamento fissato",
               "property_id": 30, "property_code": "IMM-30", "property_title": "Trilocale",
               "property_address": "Via Roma", "property_civic_number": "1",
               "property_city": "Giulianova", "owner_name": "Mario Rossi", "agent_name": "Anna Agente",
               "appointment_start_at": "2031-01-10T10:00:00+01:00", "appointment_status": "scheduled",
               "asking_price": "185000.00", "last_activity_at": "2026-10-01T10:00:00+02:00"}


def _rotte(sessione="agency_owner", *, acq=None, post=None, immobile=IMMOBILE, appuntamento=None):
    rt = a30_5._rt()
    acq = acq or _acquisizione()
    post = post or ({"status": 201, "body": {**acq, "replayed": False}},)
    appuntamento = appuntamento or {"appointment": APPUNTAMENTO,
                                    "allowed_actions": ["confirm", "reschedule", "cancel", "patch"]}
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
        ("GET", "/api/acquisitions/options", [rt.ok(OPZIONI)]),
        ("GET", "/api/acquisitions?", [rt.ok({"items": [RIGA_ELENCO]})]),
        ("GET", "/api/acquisitions/501", [rt.ok(acq)]),
        ("POST", "/api/acquisitions/501/lost", [rt.ok({**acq, "status": "lost"})]),
        ("POST", "/api/acquisitions/501/status", [rt.ok(acq)]),
        ("POST", "/api/acquisitions/501/mandate", [rt.ok({**acq, "status": "acquired"})]),
        ("PATCH", "/api/acquisitions/501", [rt.ok(acq)]),
        ("POST", "/api/acquisitions", list(post)),
        ("GET", "/api/appointments/agents", [rt.ok(AGENTI)]),
        ("POST", "/api/appointments/availability/check", [rt.ok(a30_5.LIBERO)]),
        ("GET", "/api/appointments/900", [rt.ok(appuntamento)]),
        ("GET", "/api/property/properties/31", [rt.ok(SENZA_PROPRIETARI)]),
        ("GET", f"/api/property/properties/{immobile['id']}", [rt.ok(immobile)]),
        ("GET", "/api/property/properties?", [rt.ok({"items": [IMMOBILE]})]),
        ("GET", "/api/property/form-options", [rt.ok({"territory": [], "energy_classes": [],
                                                      "property_types": [], "can_assign": False,
                                                      "agents": []})]),
        ("GET", "/api/proposals?", [rt.ok({"items": []})]),
        ("GET", "/api/sales?", [rt.ok({"items": []})]),
        ("GET", "/api/match/", [rt.ok({"items": []})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


D_HELPERS = r"""
const D = () => __dom.byId['content'].querySelectorAll('dialog').find((d) => d._open);
const q = (s) => D().querySelector(s);
const opz = (el) => el.querySelectorAll('option').map((o) => o.getAttribute('value'));
function scritture() { return chiamate().filter((c) => ['POST', 'PATCH', 'PUT', 'DELETE'].includes(c.m)
  && c.url !== '/api/appointments/availability/check'); }
"""


def _run(staged, scenario, rotte, hash):  # noqa: F811
    rt = a30_5._rt()
    driver = staged.parent / "driver-crm-ops-3.mjs"
    driver.write_text(
        a30_5._dom() + rt.FETCH + a30_13b._extra_dom() + a30_5.IS_CONNECTED + a30_5.ROUTED_FETCH
        + f"\n{rotte}\n" + f"window.location.hash = '{hash}';\n"
        + f"await import('{(staged / 'main.js').as_posix()}');\n" + "await __settle(40);\n"
        + a30_5.HELPERS + D_HELPERS + scenario + "\n", encoding="utf-8")
    esito = subprocess.run([a30_5.NODE, str(driver)], capture_output=True, text=True, timeout=60,
                           cwd=staged.parent, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    if esito.returncode != 0:
        raise AssertionError(f"driver node fallito:\n{esito.stderr[-3000:]}\n{esito.stdout[-1500:]}")
    return json.loads(esito.stdout.strip().splitlines()[-1])


def _scritture(out):
    return [c for c in out["calls"] if c["m"] in ("POST", "PATCH", "PUT", "DELETE")
            and c["url"] != "/api/appointments/availability/check"]


@node
def test_d01_voce_di_menu_ed_elenco_con_colonne_e_filtri(staged):  # noqa: F811
    scenario = r"""
      await wait();
      const nav = __dom.byId['nav'].children.map((b) => b.dataset.route);
      const stato = C().querySelector('#acq-status');
      stato.value = 'lost'; stato.dispatch('change'); await wait();
      const agente = C().querySelector('#acq-agent');
      agente.value = '4'; agente.dispatch('change'); await wait();
      report({ nav, agenteVisibile: !agente.hidden, stati: opz(stato) });
    """
    out = _run(staged, scenario, _rotte(), "#/acquisizioni")
    # SENTINELLA AGGIORNATA DA VENDITORI-1: il flusso reale e' Immobili ->
    # Venditori -> Acquisizioni; le Acquisizioni restano subito dopo i Venditori,
    # che stanno subito dopo gli Immobili. Le posizioni restano esatte.
    assert out["nav"].index("venditori") == out["nav"].index("immobili") + 1
    assert out["nav"].index("acquisizioni") == out["nav"].index("venditori") + 1
    testo = out["content"]
    for atteso in ("IMM-30", "Mario Rossi", "Anna Agente", "Appuntamento fissato", "Ultima attività",
                   "Stato appuntamento", "Prezzo richiesto", "+ Nuova acquisizione"):
        assert atteso in testo, atteso
    assert out["agenteVisibile"] is True
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 1A: in coda il filtro esplicito
    # «Creati per errore» (le acquisizioni create per errore non sono nelle altre voci).
    assert out["stati"][2:-1] == list(enums.STATUSES) and out["stati"][-1] == "mistakes"
    elenchi = [c["url"] for c in out["calls"] if c["m"] == "GET" and c["url"].startswith("/api/acquisitions?")]
    assert "statuses=lost" in elenchi[-1] and "agent_id=4" in elenchi[-1]
    assert _scritture(out) == []


@node
def test_d02_creazione_proprietari_reali_dialog_agenda_una_sola_scrittura(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const proprietari = opz(q('#acq-owner'));
      const etichette = q('#acq-owner').querySelectorAll('option').map((o) => o.textContent);
      const prezzo = q('#acq-asking').value;
      q('#acq-owner').value = '42';
      q('#acq-valuation').value = '175000';
      q('#acq-timing').value = 'within_6_months';
      q('#acq-source').value = 'referral';
      q('#acq-notes').value = '  Vuole vendere  ';
      q('form').dispatch('submit'); await wait(); await wait();
      const titolo = f('[data-title]').textContent;
      const tipo = { valore: f('[data-field="type"]').value, bloccato: f('[data-field="type"]').disabled === true };
      const crm = !!f('[data-contact-picker]');
      const a = f('[data-field="agent"]'); a.value = '4'; a.dispatch('change');
      orario('2031-01-10', '10:00');
      f('[data-field="notes"]').value = 'Citofono Rossi';
      await conferma(); await wait();
      report({ proprietari, etichette, prezzo, titolo, tipo, crm });
    """
    out = _run(staged, scenario, _rotte(), "#/acquisizioni/nuova/30")
    assert out["proprietari"] == ["41", "42"]                # owner + seller, non l'inquilino
    assert out["etichette"][0].startswith("Mario Rossi")
    assert out["prezzo"] == "180000.00"                       # proposto dall'immobile
    assert out["titolo"] == "Appuntamento di acquisizione"
    assert out["tipo"] == {"valore": "seller_meeting", "bloccato": True} and out["crm"] is False
    scritture = _scritture(out)
    assert [c["url"] for c in scritture] == ["/api/acquisitions"]    # UNA scrittura, e non l'Agenda
    corpo = scritture[0]["body"]
    assert {k: corpo[k] for k in ("property_id", "owner_contact_id", "asking_price",
                                  "valuation_price", "sale_timing", "source", "notes")} == {
        "property_id": 30, "owner_contact_id": 42, "asking_price": "180000.00",
        "valuation_price": "175000", "sale_timing": "within_6_months", "source": "referral",
        "notes": "Vuole vendere"}
    app = corpo["appointment"]
    assert set(app) == {"start_at", "end_at", "assigned_user_id", "client_request_id", "notes"}
    assert (app["start_at"], app["end_at"], app["assigned_user_id"], app["notes"]) == (
        "2031-01-10T10:00:00+01:00", "2031-01-10T11:00:00+01:00", 4, "Citofono Rossi")
    assert a30_5.UUID4.match(app["client_request_id"])
    assert "agency_id" not in corpo and "appointment_type" not in app
    assert out["hash"] == "#/acquisizioni/501"                # si apre la scheda


@node
def test_d03_immobile_senza_proprietari_nessuna_scrittura(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const avviso = q('#acq-no-owner') ? q('#acq-no-owner').textContent : null;
      q('form').dispatch('submit'); await wait();
      report({ avviso, errore: q('#acq-new-error').textContent });
    """
    out = _run(staged, scenario, _rotte(), "#/acquisizioni/nuova/31")
    assert out["avviso"] and "non ha proprietari" in out["avviso"]
    assert "non ha proprietari" in out["errore"]
    assert _scritture(out) == []


@node
def test_d04_scheda_sezioni_azioni_e_persa_con_motivo(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const pulsanti = C().querySelectorAll('button').map((b) => b.textContent.trim());
      const agenda = C().querySelector('#acq-open-agenda').getAttribute('href');
      const azioniAgenda = C().querySelectorAll('[data-agenda-action]').map((b) => b.dataset.agendaAction);
      const noteApp = C().querySelector('#acq-appointment-notes').textContent;
      const noteComm = C().querySelector('#acq-commercial-notes').textContent;
      C().querySelector('#acq-lost-btn').dispatch('click'); await wait();
      q('form').dispatch('submit'); await wait();
      const erroreSenzaMotivo = q('[data-error]').textContent;
      const motivi = opz(q('#acq-lost-reason'));
      q('#acq-lost-reason').value = 'other_agency';
      q('#acq-lost-notes').value = 'Firmato con X';
      q('form').dispatch('submit'); await wait(); await wait();
      report({ pulsanti, agenda, azioniAgenda, noteApp, noteComm, erroreSenzaMotivo, motivi });
    """
    out = _run(staged, scenario, _rotte(), "#/acquisizioni/501")
    testo = out["content"]
    for atteso in ("Acquisizione #501", "Appuntamento fissato", "Immobile", "IMM-30", "Apri immobile",
                   "Proprietari", "Mario Rossi", "Bruno Bianchi", "Principale", "Appuntamento",
                   "Apri in Agenda", "Informazioni commerciali", "Storico", "Acquisizione creata"):
        assert atteso in testo, atteso
    assert "Genera incarico" not in out["pulsanti"]          # non ammesso dal server
    assert "Segna come persa" in out["pulsanti"]
    assert out["agenda"] == "#/agenda/giorno/2031-01-10"
    assert out["azioniAgenda"] == ["confirm", "reschedule", "cancel"]
    # lo stub non decodifica le entita': l'apostrofo arriva come `&#39;` (escapeHtml)
    assert out["noteApp"] == "Citofono Rossi"
    assert out["noteComm"].replace("&#39;", "'") == "Vuole vendere entro l'estate"
    assert "motivo" in out["erroreSenzaMotivo"]
    assert out["motivi"] == ["", *enums.LOST_REASONS]
    scritture = _scritture(out)
    assert [c["url"] for c in scritture] == ["/api/acquisitions/501/lost"]
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 1A: con l'appuntamento ancora
    # aperto la perdita lo annulla insieme (casella proposta spuntata, «dall'agenzia»).
    assert scritture[0]["body"] == {"version": 2, "lost_reason": "other_agency",
                                    "lost_notes": "Firmato con X", "cancel_appointment": True,
                                    "appointment_cancelled_kind": "agency"}


@node
def test_d05_genera_incarico_solo_se_ammesso(staged):  # noqa: F811
    acq = _acquisizione(status="inspection_done", status_label="Sopralluogo effettuato",
                        allowed_actions={"edit": True, "reassign": True,
                                         "transitions": ["valuation_presented", "mandate_negotiation"],
                                         "lost": True, "new_appointment": False, "mandate": True})
    scenario = r"""
      await wait(); await wait();
      const transizioni = C().querySelectorAll('[data-transition]').map((b) => b.textContent.trim());
      C().querySelector('#acq-mandate-btn').dispatch('click'); await wait();
      q('form').dispatch('submit'); await wait();
      const errore = q('[data-error]').textContent;
      q('#acq-mandate-type').value = 'Esclusiva';
      q('#acq-mandate-start').value = '2026-10-01';
      q('#acq-mandate-end').value = '2027-03-31';
      q('form').dispatch('submit'); await wait(); await wait();
      report({ transizioni, errore });
    """
    out = _run(staged, scenario, _rotte(acq=acq), "#/acquisizioni/501")
    assert out["transizioni"] == ["Valutazione presentata", "Trattativa incarico"]
    assert "tipo di incarico" in out["errore"]
    scritture = _scritture(out)
    assert [c["url"] for c in scritture] == ["/api/acquisitions/501/mandate"]
    assert scritture[0]["body"] == {"version": 2, "mandate_type": "Esclusiva",
                                    "mandate_start": "2026-10-01", "mandate_end": "2027-03-31",
                                    "agreed_price": "185000.00"}


@node
def test_d06_scheda_immobile_incarico_senza_acquisizione_sola_lettura(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const sezione = C().querySelector('#incarico-origin-required');
      const cta = C().querySelector('#incarico-acquisition-cta');
      report({ messaggio: sezione ? sezione.textContent : null,
               cta: cta ? cta.getAttribute('href') : null,
               modifica: !!C().querySelector('#incarico-edit-btn') });
    """
    out = _run(staged, scenario, _rotte(), "#/immobili/30")
    assert out["messaggio"] == MESSAGGIO
    assert out["cta"] == "#/acquisizioni/nuova/30" and out["modifica"] is False


@node
def test_d07_scheda_immobile_con_origine_modificabile(staged):  # noqa: F811
    immobile = {**IMMOBILE, "acquisition_id": 501, "commercial_status": "mandate",
                "mandate_type": "Esclusiva", "mandate_start": "2026-10-01", "mandate_end": "2027-03-31"}
    scenario = r"""
      await wait(); await wait();
      const origine = C().querySelector('#incarico-origin');
      report({ origine: origine ? origine.textContent : null,
               link: origine ? origine.querySelector('a').getAttribute('href') : null,
               modifica: !!C().querySelector('#incarico-edit-btn'),
               cta: !!C().querySelector('#incarico-acquisition-cta') });
    """
    out = _run(staged, scenario, _rotte(immobile=immobile), "#/immobili/30")
    assert "acquisizione #501" in out["origine"] and out["link"] == "#/acquisizioni/501"
    assert out["modifica"] is True and out["cta"] is False



@node
def test_d10_transizione_e_azione_agenda_dalla_scheda(staged):  # noqa: F811
    acq = _acquisizione(status="inspection_done", status_label="Sopralluogo effettuato",
                        allowed_actions={"edit": True, "reassign": True,
                                         "transitions": ["valuation_presented", "mandate_negotiation"],
                                         "lost": True, "new_appointment": False, "mandate": True})
    scenario = r"""
      await wait(); await wait();
      C().querySelector('[data-agenda-action]').dispatch('click'); await wait(); await wait();
      const dialogoAgenda = dlg() ? f('[data-title]').textContent : null;
      dlg().close(); await wait();
      C().querySelector('[data-transition]').dispatch('click'); await wait(); await wait();
      report({ dialogoAgenda });
    """
    out = _run(staged, scenario, _rotte(acq=acq), "#/acquisizioni/501")
    assert out["dialogoAgenda"] and "Conferma" in out["dialogoAgenda"]   # il dialog dell'Agenda
    scritture = _scritture(out)
    assert [c["url"] for c in scritture] == ["/api/acquisitions/501/status"]
    assert scritture[0]["body"] == {"version": 2, "status": "valuation_presented"}


@node
def test_d11_ricerca_immobile_dal_pulsante_nuova(staged):  # noqa: F811
    scenario = r"""
      await wait();
      bottone(C(), '+ Nuova acquisizione').dispatch('click'); await wait();
      const cerca = q('#acq-property-search'); cerca.value = 'trilo'; cerca.dispatch('input'); await wait(); await wait();
      q('[data-property-id]').dispatch('click'); await wait(); await wait();
      // post-commit RC-2: la scelta e' il riquadro `#acq-property-selected` con il suo id
      const sel = q('#acq-property-selected');
      report({ scelto: sel.textContent, id: sel.dataset.propertyId, nascosto: sel.hidden === true,
               proprietari: opz(q('#acq-owner')) });
    """
    out = _run(staged, scenario, _rotte(), "#/acquisizioni")
    assert "Immobile selezionato" in out["scelto"] and "IMM-30" in out["scelto"]
    assert out["id"] == "30" and out["nascosto"] is False and out["proprietari"] == ["41", "42"]
    ricerche = [c["url"] for c in out["calls"] if c["url"].startswith("/api/property/properties?")]
    assert ricerche and "search=trilo" in ricerche[0]
    assert _scritture(out) == []


def test_d08_form_e_scheda_immobile_senza_incarico_manuale():
    form = (ASSETS / "components" / "property-form.js").read_text(encoding="utf-8")
    assert "Scadenza incarico" not in form and "pf-mandate-end" not in form
    assert "const CREATE_STATUSES = ['draft', 'evaluation', 'active', 'reserved', 'under_offer', 'withdrawn'];" in form
    scheda = (ASSETS / "views" / "immobile-dettaglio.js").read_text(encoding="utf-8")
    assert "export function selectableCommercialStatuses(p)" in scheda
    assert "selectableCommercialStatuses(p).map((s) =>" in scheda
    assert "if (editBtn && property.acquisition_id) {" in scheda


def test_d09_niente_liste_scritte_a_mano_e_layout_smartphone():
    for vista in ("acquisizioni.js", "acquisizione-dettaglio.js"):
        codice = (ASSETS / "views" / vista).read_text(encoding="utf-8")
        for etichetta in ("Sopralluogo effettuato", "Altra agenzia", "Entro 3 mesi", "Trattativa incarico"):
            assert etichetta not in codice, (vista, etichetta)      # vengono da /options
        assert "/api/appointments" not in codice                    # solo il client dell'Agenda
        assert "agency_id" not in codice
    css = (ASSETS / "app.css").read_text(encoding="utf-8")
    blocco = css[css.index("/* CRM-OPS-3"):]
    assert "@media (max-width: 767px)" in blocco and ".acq-toolbar { flex-direction: column;" in blocco
