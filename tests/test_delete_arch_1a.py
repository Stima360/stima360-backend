"""DELETE-ARCH Fase 1A - contratti statici e unitari (nessun database).

Compagno di `test_delete_arch_1a_postgres.py`: la migration 084 per il
runner, i contratti di schema/enum e la UI (Agenda: tre esiti di
annullamento e filtro «Creati per errore»; Acquisizioni: «Segna come creata
per errore», perdita con annullamento dell'appuntamento, filtro).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSIONE = "084_delete_arch_1a_mistakes"
SU = (ROOT / "migrations" / f"{VERSIONE}.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")
ASSETS = ROOT / "static" / "os_shell" / "assets"


def _eseguibile(testo):
    return "\n".join(r for r in testo.splitlines() if not r.lstrip().startswith("--"))


def _node(script):
    esito = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True,
                           text=True, cwd=ROOT, timeout=30)
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# migration 084
# ---------------------------------------------------------------------------

def test_m01_la_084_e_valida_per_il_runner_e_l_ultima():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    assert tutte[-1].version == VERSIONE and tutte[-2].version == "083_censimento_1_buildings_units"
    assert tutte[-1].down_available and not tutte[-1].non_transactional
    assert runner.validate_migration(tutte[-1]) == []
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", _eseguibile(SU), re.M)
    assert re.search(r"^\s*BEGIN\s*;", GIU, re.M) and re.search(r"^\s*COMMIT\s*;", GIU, re.M)
    assert "schema_migrations" not in GIU


def test_m02_solo_lo_scope_della_fase_1a():
    e = _eseguibile(SU)
    assert "ADD COLUMN IF NOT EXISTS cancelled_kind VARCHAR(10);" in e     # NULLABLE, nessun default
    assert "cancelled_kind IN ('client', 'agency', 'mistake') AND status = 'cancelled'" in e
    assert "'created_by_mistake'" in e and "acquisitions_lost_reason_chk" in e
    for vietato in ("deleted_at", "record_lifecycle_events", "activities", "tasks", "stima_id",
                    "UPDATE ", "INSERT INTO", "DELETE FROM", "TRUNCATE", "DROP COLUMN", "SET NOT NULL",
                    "created_by_mistake_status", "acquisitions_status_chk"):
        assert vietato not in e, vietato
    # i valori di 081 restano tutti
    for motivo in ("other_agency", "commission", "price_disagreement", "owner_no_longer_selling",
                   "unreachable", "property_or_documents_issue", "'other'"):
        assert motivo in e
    # la down rifiuta con righe qualificate prima di toccare qualcosa
    g = _eseguibile(GIU)
    assert g.index("RAISE EXCEPTION") < g.index("DROP CONSTRAINT")
    assert "created_by_mistake" in g.split("RAISE EXCEPTION")[0]


# ---------------------------------------------------------------------------
# contratti backend
# ---------------------------------------------------------------------------

def test_b01_enum_e_schemi():
    from appointments.enums import CANCELLED_KINDS, CANCELLED_KIND_LABELS_IT, APPOINTMENT_STATUSES
    from appointments.schemas import CancelBody
    from acquisitions.enums import LOST_REASONS, MISTAKE_REASON, STATUSES
    from acquisitions.schemas import LostBody, MistakeBody
    from pydantic import ValidationError
    assert CANCELLED_KINDS == ("client", "agency", "mistake")
    assert set(CANCELLED_KIND_LABELS_IT) == set(CANCELLED_KINDS)
    assert "created_by_mistake" not in APPOINTMENT_STATUSES and "created_by_mistake" not in STATUSES
    assert MISTAKE_REASON == "created_by_mistake" and MISTAKE_REASON not in LOST_REASONS
    assert CancelBody(version=1).kind is None and CancelBody(version=1, kind="mistake").kind == "mistake"
    with pytest.raises(ValidationError):
        CancelBody(version=1, kind="boh")
    corpo = LostBody(version=1, lost_reason="other")
    assert corpo.cancel_appointment is False and corpo.appointment_cancelled_kind == "agency"
    assert MistakeBody(version=1).notes is None


def test_b02_un_solo_flusso_di_annullamento():
    """L'annullamento reale e «creato per errore» passano dalla STESSA
    funzione (`_annulla`): proiezione, visite, acquisizione, Google."""
    import inspect
    from appointments import service
    corpo = inspect.getsource(service._annulla)
    for hook in ("projection.on_cancel", "_visite.on_status", "_acquisizioni.on_status",
                 "_gcal.on_appointment_mutation", "state_machine.check_transition"):
        assert hook in corpo, hook
    assert "DELETE FROM" not in corpo and "repository.update_appointment" in corpo
    assert "_annulla(" in inspect.getsource(service.cancel_appointment)
    assert "_annulla(" in inspect.getsource(service.cancel_locked)
    from acquisitions import service as acq
    assert "cancel_locked" in inspect.getsource(acq.mark_created_by_mistake)
    assert "cancel_locked" in inspect.getsource(acq.mark_lost)
    # REVIEW 1: un solo cursore, nessun commit separato
    locked = inspect.getsource(service.cancel_locked)
    for vietato in ("core_cursor", "commit", "_in_transazione", "get_connection"):
        assert vietato not in locked, vietato
    assert inspect.getsource(acq.mark_created_by_mistake).count("core_cursor(") == 1


def test_b03_ogni_writer_di_cancelled_ha_la_qualifica():
    """REVIEW 1: l'elenco CHIUSO dei writer di `status='cancelled'` su
    `appointments` nel codice di produzione; ognuno scrive la qualifica
    (default `agency`). Un writer nuovo fa fallire questo test finche' non
    e' censito. Il backfill da `stima_inspections` inserisce STORICO (NULL
    ammesso, nessun backfill della qualifica)."""
    attesi = {"appointments/service.py", "appointments/lmc15_facade.py",
              "appointments_legacy/stime_dettagliate_import.py"}
    trovati = set()
    for py in ROOT.rglob("*.py"):
        rel = py.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", ".venv/", "venv/")) or "/site-packages/" in rel:
            continue
        testo = py.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"[\"']status[\"']\s*:\s*[\"']cancelled[\"']", testo) and (
                "update_appointment" in testo):
            trovati.add(rel)
    assert trovati == attesi, trovati
    for rel in attesi:
        assert "cancelled_kind_changes(" in (ROOT / rel).read_text(encoding="utf-8"), rel
    from appointments import repository
    import inspect
    assert "cancelled_kind_changes(cur, None)" in inspect.getsource(repository.update_appointment)
    assert "SET status = 'cancelled'" not in "".join(
        p.read_text(encoding="utf-8", errors="ignore") for p in (ROOT / "appointments").glob("*.py"))


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def test_u01_agenda_specchio_delle_etichette_e_filtro():
    from appointments.enums import CANCELLED_KIND_LABELS_IT
    esito = _node(f"""
import * as m from '{(ASSETS / "agenda" / "agenda-model.js").as_posix()}';
process.stdout.write(JSON.stringify({{
  etichette: m.CANCELLED_KIND_LABELS,
  errore: m.calendarFilterParams({{ status: m.MISTAKES_FILTER }}),
  lista: m.listFilterParams({{ status: m.MISTAKES_FILTER }}),
  normale: m.calendarFilterParams({{ status: 'cancelled' }}),
  normalizzato: m.normalizeFilters({{ status: 'mistakes' }}).status,
  attivi: m.activeFilterCount({{ status: 'mistakes' }}),
  vuota: m.cancelledKindLabel(null),
}}));
""")
    assert esito["etichette"] == {k: v.replace("'", "'") for k, v in CANCELLED_KIND_LABELS_IT.items()}
    assert esito["errore"] == {"statuses": ["cancelled"], "mistakes": True}
    assert esito["lista"] == {"statuses": ["cancelled"], "mistakes": True}
    assert esito["normale"] == {"statuses": ["cancelled"]}
    assert esito["normalizzato"] == "mistakes" and esito["attivi"] == 1 and esito["vuota"] == ""


def test_u02_agenda_annulla_offre_tre_esiti_e_li_pretende():
    testo = (ASSETS / "components" / "agenda" / "agenda-dialogs.js").read_text(encoding="utf-8")
    blocco = testo[testo.index("if (action === 'cancel') {"):testo.index("if (action === 'complete') {")]
    for valore, etichetta in (("client", "Annullato dal cliente"), ("agency", "Annullato dall'agenzia"),
                              ("mistake", "Creato per errore")):
        assert f'value="{valore}"> {etichetta}' in blocco, valore
    assert "Scegli il tipo di annullamento." in blocco and "kind: scelto.value" in blocco
    pagina = (ASSETS / "views" / "agenda" / "agenda-page.js").read_text(encoding="utf-8")
    assert "[MISTAKES_FILTER, 'Creati per errore']" in pagina
    api = (ASSETS / "agenda" / "agenda-api.js").read_text(encoding="utf-8")
    assert api.count("mistakes: mistakes ? 'true' : undefined") == 2
    drawer = (ASSETS / "components" / "agenda" / "agenda-drawer.js").read_text(encoding="utf-8")
    assert "voce('Tipo di annullamento', cancelledKindLabel(riga.cancelled_kind) || null)" in drawer


def test_u03_acquisizioni_azione_filtro_e_perdita_con_annullamento():
    lista = (ASSETS / "views" / "acquisizioni.js").read_text(encoding="utf-8")
    assert "if (filters.statuses === MISTAKES_FILTER) params.set('mistakes', 'true');" in lista
    assert "<option value=\"${MISTAKES_FILTER}\">Creati per errore</option>" in lista
    dettaglio = (ASSETS / "views" / "acquisizione-dettaglio.js").read_text(encoding="utf-8")
    assert "azioni.mistake ?" in dettaglio and "Segna come creata per errore" in dettaglio
    assert "apiPost(`/api/acquisitions/${acq.id}/mistake`, corpo)" in dettaglio
    assert "corpo.cancel_appointment = true;" in dettaglio
    assert '<option value="agency">' in dettaglio and '<option value="client">' in dettaglio
    assert '<option value="mistake">' not in dettaglio
    for percorso in ("views/acquisizioni.js", "views/acquisizione-dettaglio.js", "agenda/agenda-model.js",
                     "agenda/agenda-api.js", "views/agenda/agenda-page.js",
                     "components/agenda/agenda-dialogs.js", "components/agenda/agenda-drawer.js"):
        esito = subprocess.run(["node", "--check", str(ASSETS / percorso)], capture_output=True, text=True)
        assert esito.returncode == 0, (percorso, esito.stderr)


def test_g01_nessuna_azione_tenant_scrive_nel_registro_di_piattaforma():
    """FINAL GATE: `platform_audit_log` registra gli atti `platform.*`
    (ingresso/uscita dall'acting compresi); le azioni tenant, nuove e
    vecchie, non lo scrivono. Le due azioni «per errore» seguono la stessa
    regola delle equivalenti (annulla, persa): nessun bypass, nessun audit
    nuovo inventato."""
    for modulo in ("appointments", "acquisitions"):
        for py in (ROOT / modulo).glob("*.py"):
            testo = py.read_text(encoding="utf-8")
            assert not re.search(r"^\s*(from|import)\s+platform_admin", testo, re.M), py.name
            assert "platform_audit_log" not in testo and "audit.record(" not in testo, py.name
    from platform_admin import enums
    assert enums.ACTION_ACTING_ENTER == "platform.acting.enter"
    assert enums.ACTION_ACTING_EXIT == "platform.acting.exit"
