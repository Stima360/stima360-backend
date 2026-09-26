"""A30-8 - esito e follow-up senza database: contratti, confini, ordine.

Il comportamento su PostgreSQL vero e' in `test_a30_8_outcome_postgres.py`;
la UI in `test_a30_8_outcome_ui.py`.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SALTA = {"tests", "venv", ".venv", "node_modules", ".git", "__pycache__"}


def _sorgenti_python():
    for file in ROOT.rglob("*.py"):
        if SALTA & set(file.relative_to(ROOT).parts):
            continue
        yield file


# ---------------------------------------------------------------------------
# CORE: l'autore esplicito non e' un bypass generico
# ---------------------------------------------------------------------------

def test_01_firma_core_autore_solo_keyword_e_ctx_ancora_facoltativo():
    from core import repository
    firma = inspect.signature(repository.create_task_with_cursor)
    assert list(firma.parameters) == ["cur", "data", "ctx", "created_by_user_id"]
    for nome in ("ctx", "created_by_user_id"):
        assert firma.parameters[nome].kind is inspect.Parameter.KEYWORD_ONLY
        assert firma.parameters[nome].default is None
    # nessun interruttore di comodo
    codice = inspect.getsource(repository.create_task_with_cursor)
    for vietato in ("skip_validation", "trusted", "bypass", "agency_id="):
        assert vietato not in codice, vietato


def test_02_solo_il_service_dell_agenda_passa_un_autore_esplicito():
    chiamanti = []
    for file in _sorgenti_python():
        testo = file.read_text(encoding="utf-8")
        if "create_task_with_cursor" not in testo:
            continue            # (alcuni script usano una sintassi di un Python piu' nuovo)
        albero = ast.parse(testo)
        for nodo in ast.walk(albero):
            if not isinstance(nodo, ast.Call):
                continue
            nome = nodo.func.attr if isinstance(nodo.func, ast.Attribute) else getattr(
                nodo.func, "id", None)
            if nome and "create_task_with_cursor" in nome:
                if any(k.arg == "created_by_user_id" for k in nodo.keywords):
                    chiamanti.append(str(file.relative_to(ROOT)))
    assert chiamanti == ["appointments/service.py"], chiamanti


def test_03_nessuno_schema_http_dichiara_l_autore_ne_l_agenzia():
    from core import schemas
    for modello in (schemas.TaskCreate, schemas.TaskUpdate):
        campi = getattr(modello, "model_fields", None) or getattr(modello, "__fields__", {})
        assert "created_by_user_id" not in campi and "agency_id" not in campi
    from appointments.schemas import FollowUpBody
    assert set(FollowUpBody.model_fields) == {"due_at", "title", "note"}
    assert FollowUpBody.model_config.get("extra") == "forbid"


def test_04_il_router_core_resta_sul_percorso_con_ctx():
    router = (ROOT / "core" / "router.py").read_text(encoding="utf-8")
    assert "create_task_with_cursor" not in router
    assert "created_by_user_id" not in router


# ---------------------------------------------------------------------------
# AGENDA: confini, riferimenti, ordine
# ---------------------------------------------------------------------------

def test_10_solo_service_py_importa_core_repository():
    for file in (ROOT / "appointments").glob("*.py"):
        albero = ast.parse(file.read_text(encoding="utf-8"))
        moduli = {n.module for n in ast.walk(albero) if isinstance(n, ast.ImportFrom)}
        if file.name == "service.py":
            assert "core.repository" in moduli
            nomi = [a.name for n in ast.walk(albero) if isinstance(n, ast.ImportFrom)
                    and n.module == "core.repository" for a in n.names]
            assert nomi == ["create_task_with_cursor"]
        else:
            assert "core.repository" not in moduli, file.name


def test_11_riferimenti_del_task_solo_dalla_riga_bloccata():
    from appointments import service
    riferimenti = inspect.getsource(service._riferimenti_follow_up)
    assert 'riferimenti = {c: row[c] for c in ("contact_id", "lead_id", "stima_id")}' in riferimenti
    crea = inspect.getsource(service._crea_follow_up)
    assert crea.count("create_task_with_cursor(") == 1
    assert "created_by_user_id=actor" in crea and "ctx=" not in crea
    assert '"metadata": {"appointment_id": row["id"], "outcome": esito}' in crea
    assert "follow_up.due_at" in crea and "**riferimenti" in crea
    # nessun campo del task arriva dal corpo oltre a scadenza, titolo e nota
    usati = set(re.findall(r"follow_up\.(\w+)", crea))
    assert usati == {"title", "note", "due_at"}, usati
    sorgente = inspect.getsource(service)
    assert sorgente.count("create_task_with_cursor(") == 1


@pytest.mark.parametrize("funzione,guardia,proiezione", [
    ("complete_appointment", 'check_time_guard("complete"', "projection.on_complete("),
    ("no_show_appointment", 'check_time_guard("no_show"', "projection.on_no_show("),
    ("cancel_appointment", "repository.db_now(cur)", "projection.on_cancel("),
])
def test_12_ordine_transizione_proiezione_task_evento(funzione, guardia, proiezione):
    from appointments import service
    corpo = inspect.getsource(getattr(service, funzione))
    ordine = [corpo.index(s) for s in (guardia, "_prepara_follow_up(", proiezione,
                                       "_crea_follow_up(", "repository.update_appointment(")]
    assert ordine == sorted(ordine), funzione
    assert "event_extra=_extra_evento(" in corpo
    assert '"_su_riga(ctx, appointment_id,' not in corpo           # sanita' della ricerca
    assert "return _su_riga(ctx, appointment_id," in corpo           # lock, ruolo, version


def test_13_outcome_note_solo_su_complete_e_no_show_follow_up_non_su_reschedule():
    from appointments import schemas
    assert "outcome_note" in schemas.CompleteBody.model_fields
    assert "outcome_note" in schemas.NoShowBody.model_fields
    assert "outcome_note" not in schemas.CancelBody.model_fields
    for corpo in (schemas.CompleteBody, schemas.NoShowBody, schemas.CancelBody):
        assert "follow_up" in corpo.model_fields
    for corpo in (schemas.RescheduleBody, schemas.ScheduleBody, schemas.ConfirmBody,
                  schemas.PatchBody, schemas.ReassignBody):
        assert "follow_up" not in corpo.model_fields and "outcome_note" not in corpo.model_fields
    from appointments import service
    extra = inspect.getsource(service._extra_evento)
    assert '"outcome_note"' in extra and "if outcome_note is not None" in extra


def test_14_la_nota_non_finisce_nelle_note_dell_appuntamento():
    from appointments import service
    for nome in ("complete_appointment", "no_show_appointment", "cancel_appointment"):
        corpo = inspect.getsource(getattr(service, nome))
        assert '"notes"' not in corpo, nome


# ---------------------------------------------------------------------------
# CODICI D'ERRORE (D6)
# ---------------------------------------------------------------------------

def test_20_codici_nuovi_422_e_invalid_transition_invariato():
    from appointments import errors, state_machine
    from core.exceptions import ConflictError, ValidationError
    for classe, codice in ((errors.CompleteTooEarly, "COMPLETE_TOO_EARLY"),
                           (errors.NoShowTooEarly, "NO_SHOW_TOO_EARLY"),
                           (errors.RescheduleInPast, "RESCHEDULE_IN_PAST"),
                           (errors.FollowUpInPast, "FOLLOW_UP_IN_PAST"),
                           (errors.FollowUpRequiresLink, "FOLLOW_UP_REQUIRES_LINK")):
        assert classe.code == codice and issubclass(classe, ValidationError)
        assert not issubclass(classe, ConflictError)
    # una transizione vietata dallo STATO resta 409
    for stato in state_machine.TERMINAL_STATUSES:
        with pytest.raises(errors.InvalidTransition):
            state_machine.check_transition("complete", stato)


def test_21_available_from_resta_nel_corpo_del_422():
    from datetime import datetime, timedelta, timezone

    from appointments import errors, state_machine
    s = datetime(2030, 1, 1, 10, tzinfo=timezone.utc)
    with pytest.raises(errors.CompleteTooEarly) as info:
        state_machine.check_time_guard("complete", start_at=s, end_at=s + timedelta(hours=1),
                                       now=s - timedelta(seconds=1))
    assert info.value.extra["available_from"] == s
    with pytest.raises(errors.NoShowTooEarly) as info:
        state_machine.check_time_guard("no_show", start_at=s, end_at=s + timedelta(hours=1),
                                       now=s + timedelta(minutes=59))
    assert info.value.extra["available_from"] == s + timedelta(hours=1)


def test_22_reschedule_guardia_sul_passato_prima_di_ogni_lock_d_agente():
    from appointments import service
    corpo = inspect.getsource(service.reschedule_appointment)
    assert corpo.index("payload.start_at < _adesso()") < corpo.index("repository.lock_agents(")
    assert "errors.RescheduleInPast(" in corpo


def test_23_cancel_nessuna_guardia_temporale_nuova_confirm_invariato():
    from appointments import service
    for nome in ("cancel_appointment", "confirm_appointment"):
        corpo = inspect.getsource(getattr(service, nome))
        assert "check_time_guard" not in corpo and "_adesso() <" not in corpo, nome


# ---------------------------------------------------------------------------
# NESSUN CAMBIO AUTOMATICO CRM, PROIEZIONE E JOURNEY INVARIATE (D8)
# ---------------------------------------------------------------------------

def test_30_nessuna_scrittura_su_lead_contatti_stime_mandati():
    for file in ("service.py", "repository.py"):
        codice = (ROOT / "appointments" / file).read_text(encoding="utf-8")
        for vietato in (r"UPDATE\s+leads", r"UPDATE\s+contacts", r"UPDATE\s+stime\b",
                        r"INSERT\s+INTO\s+stima_acquisitions", r"communication"):
            assert not re.search(vietato, codice, re.I), (file, vietato)


def test_31_no_show_proietta_come_prima_e_la_journey_non_si_ferma():
    from appointments import projection
    assert projection.NO_SHOW_REASON == "no_show"
    assert "on_cancel(cur, agency_id, row, reason=NO_SHOW_REASON" in inspect.getsource(
        projection.on_no_show)
    journey = (ROOT / "communication" / "journey_repository.py").read_text(encoding="utf-8")
    assert "'scheduled', 'completed'" in journey or "'scheduled','completed'" in journey


# ---------------------------------------------------------------------------
# UI: nessun riferimento dal browser, follow-up spento, successo dopo il 2xx
# ---------------------------------------------------------------------------

DIALOGS = ROOT / "static" / "os_shell" / "assets" / "components" / "agenda" / "agenda-dialogs.js"


def test_40_il_follow_up_del_browser_ha_solo_scadenza_titolo_nota():
    codice = DIALOGS.read_text(encoding="utf-8")
    corpo = codice[codice.index("function leggiFollowUp(form)"):]
    corpo = corpo[:corpo.index("\n}\n")]
    assert set(re.findall(r"corpo\.(\w+) =", corpo)) == {"title", "note"}
    assert "{ due_at: scadenza }" in corpo
    for vietato in ("contact_id", "lead_id", "stima_id", "agency_id", "assigned_to"):
        assert vietato not in corpo
    assert "interruttore.checked = false;" in codice


def test_41_messaggi_di_successo_dal_modello_solo_nell_on_done():
    pagina = (ROOT / "static" / "os_shell" / "assets" / "views" / "agenda"
              / "agenda-page.js").read_text(encoding="utf-8")
    on_done = pagina[pagina.index("onDone: async (esito) => {"):]
    on_done = on_done[:on_done.index("},\n        });")]
    assert "actionSuccessMessage(azione)" in on_done
    modello = (ROOT / "static" / "os_shell" / "assets" / "agenda"
               / "agenda-model.js").read_text(encoding="utf-8")
    for testo in ("Appuntamento completato.", "Cliente segnato come non presentato.",
                  "Appuntamento annullato."):
        assert testo in modello
