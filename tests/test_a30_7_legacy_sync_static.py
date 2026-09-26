"""A30-7 - la rotta di sincronizzazione e le guardie di "Pianifica", senza
database: dipendenze, ruoli, tenant, confini del package, codici d'errore.
Il comportamento su PostgreSQL vero e' in
`test_a30_7_legacy_schedule_postgres.py`; la UI in `test_a30_7_schedule_ui.py`.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "appointments_legacy" / "router.py"


class _Ctx:
    def __init__(self, *, agency_id=7, user_id=3, may_assign=True, platform=False):
        self.agency_id, self.user_id = agency_id, user_id
        self.may_assign_records = may_assign
        self.is_platform_admin = platform

    def require_agency(self):
        if self.agency_id is None:
            from operator_auth.exceptions import PlatformAdminAgencyRequired
            raise PlatformAdminAgencyRequired("scegli un'agenzia")
        return self.agency_id


def test_01_una_sola_rotta_post_con_require_operator():
    from appointments_legacy.router import router
    from operator_auth.dependencies import require_operator
    assert router.prefix == "/api/appointments"                # lo stesso dominio
    assert router.tags == ["appointments"]
    assert [(sorted(r.methods), r.path) for r in router.routes] == \
        [(["POST"], "/api/appointments/legacy-requests/sync")]
    (rotta,) = router.routes
    assert require_operator in [d.call for d in rotta.dependant.dependencies]
    assert ROUTER.read_text(encoding="utf-8").count("Depends(require_operator)") == 1


def test_02_la_rotta_non_accetta_parametri_ne_corpo():
    """Il tenant viene SOLO dalla sessione: nessun agency_id possibile."""
    from appointments_legacy.router import sync_legacy_requests
    assert list(inspect.signature(sync_legacy_requests).parameters) == ["ctx"]


def _sync(monkeypatch, ctx, esito=None):
    from appointments_legacy import router as modulo
    chiamate = []

    class _Cursore:
        def __enter__(self):
            return None, object()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(modulo, "core_cursor", lambda commit=False: _Cursore())

    def finto(cur, *, apply, agency_id, today=None):
        chiamate.append({"apply": apply, "agency_id": agency_id})
        base = {k: 0 for k in ("eligible", "inserted", "already_imported", "past", "today",
                               "future", "orphan", "dst_nonexistent", "dst_ambiguous",
                               "missing_agency", "zero_lead", "one_lead", "multiple_leads",
                               "errors")}
        base.update(esito or {})
        return base

    monkeypatch.setattr(modulo.legacy, "run_import", finto)
    return modulo.sync_for_session(ctx), chiamate


def test_03_owner_admin_importano_solo_la_propria_agenzia(monkeypatch):
    esito, chiamate = _sync(monkeypatch, _Ctx(agency_id=42),
                            {"inserted": 2, "already_imported": 3, "orphan": 1,
                             "dst_ambiguous": 1, "errors": 1})
    assert chiamate == [{"apply": True, "agency_id": 42}]
    assert (esito["imported"], esito["already_present"], esito["excluded"]) == (2, 3, 3)


@pytest.mark.parametrize("ctx,errore", [
    (_Ctx(may_assign=False), "ForbiddenRole"),
    (_Ctx(user_id=None), "SessionRequired"),
    (_Ctx(agency_id=None, platform=True), "PlatformAdminAgencyRequired"),
])
def test_04_rifiuti_prima_di_qualunque_import(monkeypatch, ctx, errore):
    with pytest.raises(Exception) as info:
        _sync(monkeypatch, ctx)
    assert type(info.value).__name__ == errore


def test_05_il_router_non_ha_side_effect_ne_scritture_proprie():
    codice = re.sub(r'""".*?"""', "", ROUTER.read_text(encoding="utf-8"), flags=re.S)
    for vietato in (r"INSERT\s+INTO", r"UPDATE\s+\w+\s+SET", r"DELETE\s+FROM", r"\.commit\(",
                    "communication", "notification", "projection", "lmc15", "acquisition",
                    "stima_inspections", "google", r"Body\(", r"Query\(", r"Request\b"):
        assert not re.search(vietato, codice, re.I), vietato
    # l'agenzia viene dalla sessione, e solo da li'
    assert "agency_id = ctx.require_agency()" in codice
    importati = {n.module for n in ast.walk(ast.parse(ROUTER.read_text(encoding="utf-8")))
                 if isinstance(n, ast.ImportFrom)}
    assert importati == {"__future__", "fastapi", "appointments", "appointments.router",
                         "core.database", "operator_auth.context",
                         "operator_auth.dependencies", None}


def test_06_codici_d_errore_nuovi_stabili():
    from appointments import errors
    from core.exceptions import ConflictError, ValidationError
    assert errors.SCHEDULE_IN_PAST == "SCHEDULE_IN_PAST"
    assert errors.STIMA_INSPECTION_ALREADY_OPEN == "STIMA_INSPECTION_ALREADY_OPEN"
    assert issubclass(errors.ScheduleInPast, ValidationError)                 # 422
    assert issubclass(errors.StimaInspectionAlreadyOpen, ConflictError)       # 409


def test_07_le_guardie_stanno_in_schedule_nell_ordine_dei_lock():
    from appointments import service
    sorgente = inspect.getsource(service.schedule_appointment)
    ordine = [sorgente.index(s) for s in (
        "_controlla_agente(", "_non_nel_passato(", "_occupa(", "_sopralluogo_unico(",
        "repository.update_appointment(", "projection.on_schedule(")]
    assert ordine == sorted(ordine)
    guardia = inspect.getsource(service._sopralluogo_unico)
    assert guardia.index("repository.lock_stima(") < guardia.index("open_inspection_for_stima(")
    assert "state_machine.OPEN_STATUSES" in guardia            # nessuna lista inventata
    assert "_visibile(ctx, altro)" in guardia                   # l'id solo a chi lo vede


def test_08_la_guardia_del_passato_confronta_istanti():
    from appointments import service
    sorgente = inspect.getsource(service._non_nel_passato)
    assert "start_at < _adesso()" in sorgente
    assert "replace(tzinfo" not in sorgente and "Europe/Rome" not in sorgente


def test_09_lock_stima_e_solo_una_lettura_for_update():
    from appointments import repository
    sorgente = inspect.getsource(repository.lock_stima)
    assert "SELECT id FROM stime WHERE id = %s AND agency_id = %s FOR UPDATE" in sorgente
    sorgente = inspect.getsource(repository.open_inspection_for_stima)
    assert "agency_id = %(agency)s" in sorgente and "appointment_type = 'inspection'" in sorgente
