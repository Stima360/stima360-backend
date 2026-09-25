"""A30-2 senza database: state machine, disponibilita', schemi, perimetro.

Il comportamento su PostgreSQL e via HTTP (app FastAPI di test, router NON
montato in `main.py`) e' in `test_a30_2_appointments_postgres.py`.
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACCHETTO = ROOT / "appointments"
ROMA = ZoneInfo("Europe/Rome")
UTC = timezone.utc


def _t(*a):
    return datetime(*a, tzinfo=ROMA)


# ---------------------------------------------------------------------------
# A - STATE MACHINE
# ---------------------------------------------------------------------------

from appointments import errors, state_machine as sm  # noqa: E402

TUTTI_GLI_STATI = ("requested", "scheduled", "confirmed", "completed", "cancelled",
                   "no_show", "rescheduled")

ATTESO = {
    "schedule": {"requested"},
    "confirm": {"scheduled", "confirmed"},
    "reschedule": {"scheduled", "confirmed"},
    "reassign": {"requested", "scheduled", "confirmed"},
    "cancel": {"requested", "scheduled", "confirmed"},
    "complete": {"scheduled", "confirmed"},
    "no_show": {"scheduled", "confirmed"},
    "patch": {"requested", "scheduled", "confirmed"},
}


@pytest.mark.parametrize("azione", sorted(ATTESO))
@pytest.mark.parametrize("stato", TUTTI_GLI_STATI)
def test_01_ogni_coppia_stato_azione(azione, stato):
    if stato in ATTESO[azione]:
        sm.check_transition(azione, stato)
    else:
        with pytest.raises(errors.InvalidTransition):
            sm.check_transition(azione, stato)


def test_02_i_terminali_non_hanno_uscite_q6():
    assert set(sm.TERMINAL_STATUSES) == {"completed", "cancelled", "no_show", "rescheduled"}
    for stato in sm.TERMINAL_STATUSES:
        for azione in sm.ACTIONS:
            with pytest.raises(errors.InvalidTransition):
                sm.check_transition(azione, stato)


def test_03_complete_solo_da_start_no_show_solo_da_end_d11():
    s, e = _t(2026, 10, 5, 10), _t(2026, 10, 5, 11)
    with pytest.raises(errors.InvalidTransition):
        sm.check_time_guard("complete", start_at=s, end_at=e, now=s - timedelta(seconds=1))
    sm.check_time_guard("complete", start_at=s, end_at=e, now=s)
    with pytest.raises(errors.InvalidTransition):
        sm.check_time_guard("no_show", start_at=s, end_at=e, now=e - timedelta(seconds=1))
    sm.check_time_guard("no_show", start_at=s, end_at=e, now=e)


def test_04_completed_at_reale_mai_start_d_ufficio_q3():
    """Gate A30-2, correzione A: assente -> NOW() del database; dichiarato ->
    conservato esattamente se start <= completed_at <= db_now; altrimenti
    errore. Nessuna tolleranza, nessuna correzione."""
    s, db_now = _t(2026, 10, 5, 10), _t(2026, 10, 5, 12)
    assert sm.resolve_completed_at(start_at=s, declared=None, db_now=db_now) == db_now
    dichiarato = _t(2026, 10, 5, 10, 40, 17)
    assert sm.resolve_completed_at(start_at=s, declared=dichiarato, db_now=db_now) is dichiarato
    # gli estremi sono ammessi e restano identici
    assert sm.resolve_completed_at(start_at=s, declared=s, db_now=db_now) == s
    assert sm.resolve_completed_at(start_at=s, declared=db_now, db_now=db_now) == db_now
    # un microsecondo nel futuro, un secondo, un'ora: sempre errore
    for sbagliato in (db_now + timedelta(microseconds=1), db_now + timedelta(seconds=1),
                      db_now + timedelta(hours=1), s - timedelta(microseconds=1)):
        with pytest.raises(errors.CompletedAtInvalid):
            sm.resolve_completed_at(start_at=s, declared=sbagliato, db_now=db_now)


def test_04b_nessuna_tolleranza_ne_correzione_silenziosa():
    assert not hasattr(sm, "CLOCK_SKEW")
    assert not hasattr(sm, "align_to_recorded")
    import inspect
    from appointments import service
    corpo = inspect.getsource(service.complete_appointment)
    # l'"adesso" del completamento e' il NOW() del DB nella stessa transazione
    assert "repository.db_now(cur)" in corpo and "_adesso()" not in corpo
    assert corpo.index("db_now(cur)") < corpo.index("on_complete")


def test_05_allowed_actions_per_ruolo_e_tempo():
    riga = {"status": "confirmed", "start_at": _t(2026, 10, 5, 10), "end_at": _t(2026, 10, 5, 11)}
    prima = _t(2026, 10, 5, 9)
    assert sm.allowed_actions(riga, is_manager=False, is_own=False, now=prima) == []
    agente = sm.allowed_actions(riga, is_manager=False, is_own=True, now=prima)
    assert "reassign" not in agente and "confirm" not in agente
    assert "complete" not in agente and "no_show" not in agente
    capo = sm.allowed_actions(riga, is_manager=True, is_own=False, now=_t(2026, 10, 5, 12))
    assert {"reassign", "complete", "no_show", "cancel", "reschedule"} <= set(capo)


# ---------------------------------------------------------------------------
# B - DISPONIBILITA' (pura)
# ---------------------------------------------------------------------------

from appointments import availability as av  # noqa: E402


def test_10_slot_liberi_e_occupati_intervalli_semiaperti():
    occupato = [(_t(2026, 10, 5, 10), _t(2026, 10, 5, 11))]
    slot = av.slots(_t(2026, 10, 5, 9), _t(2026, 10, 5, 13), duration=60, step=60,
                    busy=occupato)
    stati = [(s["start_at"].astimezone(ROMA).hour, s["available"]) for s in slot]
    assert stati == [(9, True), (10, False), (11, True), (12, True)]


def test_11_buffer_rendono_occupato_lo_slot_adiacente():
    occupato = [(_t(2026, 10, 5, 10), _t(2026, 10, 5, 11))]
    assert av.is_free(_t(2026, 10, 5, 11), _t(2026, 10, 5, 12), occupato)
    assert not av.is_free(_t(2026, 10, 5, 11), _t(2026, 10, 5, 12), occupato,
                          buffer_before=15)


def test_12_nessun_orario_lavorativo_d7():
    """Anche le 23:00 sono uno slot: la finestra la decide il chiamante."""
    slot = av.slots(_t(2026, 10, 5, 22), _t(2026, 10, 6, 1), duration=60, step=60, busy=[])
    assert [s["start_at"].astimezone(ROMA).hour for s in slot] == [22, 23, 0]


@pytest.mark.parametrize("giorno,ore", [((2026, 3, 29), 23), ((2026, 10, 25), 25)])
def test_13_cambio_d_ora(giorno, ore):
    inizio = datetime(*giorno, tzinfo=ROMA)
    fine = (inizio + timedelta(days=1)).replace(tzinfo=None).replace(tzinfo=ROMA)
    slot = av.slots(inizio, fine, duration=60, step=60, busy=[])
    assert len(slot) == ore
    etichette = [s["start_at"].astimezone(ROMA).strftime("%H:%M") for s in slot]
    assert all(e.endswith(":00") for e in etichette)
    for a, b in zip(slot, slot[1:]):
        assert b["start_at"] - a["start_at"] == timedelta(hours=1)


def test_14_finestra_passo_e_durata_validati():
    with pytest.raises(ValueError):
        av.slots(_t(2026, 10, 5, 0), _t(2026, 10, 13, 0), duration=60, step=60, busy=[])
    with pytest.raises(ValueError):
        av.slots(_t(2026, 10, 5, 0), _t(2026, 10, 5, 3), duration=60, step=20, busy=[])
    with pytest.raises(ValueError):
        av.slots(_t(2026, 10, 5, 0), _t(2026, 10, 5, 3), duration=2, step=15, busy=[])


def test_15_alternative_dopo_l_orario_richiesto():
    occupato = [(_t(2026, 10, 5, 10), _t(2026, 10, 5, 11, 30))]
    alt = av.alternatives(_t(2026, 10, 5, 10), duration=60, busy=occupato, step=15)
    assert [a["start_at"].astimezone(ROMA).strftime("%H:%M") for a in alt] == [
        "11:30", "11:45", "12:00"]


# ---------------------------------------------------------------------------
# C - SCHEMI
# ---------------------------------------------------------------------------

from pydantic import ValidationError as PydanticError  # noqa: E402

from appointments import schemas  # noqa: E402

CHIAVE = "0b7c5a52-3a1e-4f8b-9a2d-1c3e5f7a9b0d"


def _crea(**kw):
    base = {"appointment_type": "call", "assigned_user_id": 1,
            "start_at": _t(2026, 10, 5, 10), "end_at": _t(2026, 10, 5, 11),
            "client_request_id": CHIAVE}
    base.update(kw)
    return schemas.AppointmentCreateBody(**base)


def _tipo_errore(exc):
    return exc.value.errors()[0]["type"]


def test_20_client_request_id_obbligatorio_uuid4_canonico():
    assert _crea().client_request_id == CHIAVE
    with pytest.raises(PydanticError):
        schemas.AppointmentCreateBody(appointment_type="call", assigned_user_id=1,
                                      start_at=_t(2026, 10, 5, 10), end_at=_t(2026, 10, 5, 11))
    for cattiva in (CHIAVE.upper(), "abc", "0b7c5a52-3a1e-1f8b-9a2d-1c3e5f7a9b0d"):
        with pytest.raises(PydanticError) as info:
            _crea(client_request_id=cattiva)
        assert _tipo_errore(info) == "client_request_id_invalid"


def test_21_agente_obbligatorio_per_fissare_d2():
    with pytest.raises(PydanticError) as info:
        _crea(assigned_user_id=None)
    assert _tipo_errore(info) == "agent_required"
    assert _crea(assigned_user_id=None, status="requested").assigned_user_id is None
    with pytest.raises(PydanticError) as info:
        schemas.ScheduleBody(version=1, start_at=_t(2026, 10, 5, 10), end_at=_t(2026, 10, 5, 11))
    assert _tipo_errore(info) == "agent_required"


def test_22_fuso_obbligatorio():
    with pytest.raises(PydanticError) as info:
        _crea(start_at=datetime(2026, 10, 5, 10))
    assert _tipo_errore(info) == "timezone_required"


def test_23_ogni_scrittura_su_riga_porta_version():
    for modello in (schemas.ScheduleBody, schemas.ConfirmBody, schemas.RescheduleBody,
                    schemas.ReassignBody, schemas.CancelBody, schemas.CompleteBody,
                    schemas.NoShowBody, schemas.PatchBody):
        assert modello.model_fields["version"].is_required(), modello.__name__


def test_24_patch_non_tocca_orari_agente_tipo_stato():
    for vietato in ("start_at", "end_at", "assigned_user_id", "appointment_type", "status",
                    "source", "agency_id"):
        with pytest.raises(PydanticError):
            schemas.PatchBody(version=1, **{vietato: 1})
    assert schemas.PatchBody(version=1, notes=None).changes() == {"notes": None}


def test_25_nessun_campo_di_attore_agenzia_o_fonte_nei_corpi():
    for modello in (schemas.AppointmentCreateBody, schemas.ScheduleBody, schemas.ReassignBody,
                    schemas.RescheduleBody, schemas.PatchBody, schemas.AvailabilityCheckBody):
        assert not set(modello.model_fields) & {
            "agency_id", "created_by_user_id", "source", "source_record_id", "test_run_id",
            "actor_user_id", "blocked_range"}, modello.__name__


# ---------------------------------------------------------------------------
# D - PERIMETRO (vincoli del gate)
# ---------------------------------------------------------------------------

def _senza_commenti_py(testo):
    return "\n".join(r.split("#", 1)[0] for r in testo.splitlines())


def test_30_main_py_non_nomina_appointments_d1():
    # SENTINELLA AGGIORNATA DAL MOUNT A30: D1 e' chiuso dal mount. `main.py`
    # nomina l'Agenda solo per import + mount, nient'altro.
    from tests.test_a30_mount_api import _righe_codice_agenda
    righe = _righe_codice_agenda((ROOT / "main.py").read_text(encoding="utf-8"))
    assert righe == [
        "from appointments.router import router as appointments_router",
        "app.include_router(appointments_router, "
        "dependencies=[Depends(require_authenticated_operator)])",
    ]


def test_31_ogni_rotta_ha_require_operator_scritto_per_esteso():
    from appointments.router import router
    for rotta in router.routes:
        dipendenze = [d.call for d in rotta.dependant.dependencies]
        from operator_auth.dependencies import require_operator
        assert require_operator in dipendenze, rotta.path
    testo = (PACCHETTO / "router.py").read_text(encoding="utf-8")
    assert testo.count("Depends(require_operator)") == len(router.routes)


def test_32_nessuna_lettura_di_stime_dettagliate_ne_property_visits():
    for file in PACCHETTO.glob("*.py"):
        codice = _senza_commenti_py(file.read_text(encoding="utf-8"))
        codice = re.sub(r'""".*?"""', "", codice, flags=re.S)
        assert "stime_dettagliate" not in codice.replace("legacy_stime_dettagliate", ""), file
        assert "property_visits" not in codice, file


def test_33_proiezione_spenta_e_facciata_lmc15_invariata_d3():
    # SENTINELLA AGGIORNATA DA A30-2P: la proiezione e' ACCESA (D3, dopo il
    # backfill); resta invariato che la facciata LMC-15 non esiste ancora.
    from appointments import projection
    assert projection.PROJECTION_ENABLED is True
    testo = (PACCHETTO / "projection.py").read_text(encoding="utf-8")
    assert "PROJECTION_ENABLED = True" in testo
    # SENTINELLA AGGIORNATA DA A30-2P FACADE: la facade LMC-15 esiste. Il
    # service LMC-15 delega SOLO a `appointments.lmc15_facade`; il router
    # LMC-15 non nomina l'Agenda (contratto HTTP invariato).
    servizio = (ROOT / "acquisition/service.py").read_text(encoding="utf-8")
    assert "from appointments import lmc15_facade" in servizio
    import re as _re
    importati = set(_re.findall(r"from appointments(?:\.\w+)? import (\w+)", servizio))
    assert importati == {"lmc15_facade"}, importati
    assert "appointments.service" not in servizio
    assert "appointments" not in (ROOT / "acquisition/router.py").read_text(encoding="utf-8")


def test_34_le_varianti_lmc15_non_fanno_commit():
    testo = (ROOT / "acquisition/repository.py").read_text(encoding="utf-8")
    albero = ast.parse(testo)
    for nodo in albero.body:
        if isinstance(nodo, ast.FunctionDef) and nodo.name.endswith("_in"):
            corpo = ast.get_source_segment(testo, nodo)
            corpo = re.sub(r'""".*?"""', "", corpo, flags=re.S)
            assert "core_cursor" not in corpo and ".commit(" not in corpo, nodo.name
    for nome in ("create_inspection_in", "complete_inspection_in", "cancel_inspection_in",
                 "reschedule_inspection_in"):
        assert f"def {nome}(cur," in testo, nome


def test_35_il_modulo_os_shell_non_e_toccato():
    for file in ("static/os_shell/assets/main.js",):
        assert "appointments" not in (ROOT / file).read_text(encoding="utf-8")
