"""A31-2 - regole strutturali della proiezione delle visite acquirente.

Senza database: si legge il codice. Il package `buyer_visits/` lavora solo
sul cursore del chiamante (niente commit, niente connessioni), non scrive mai
outcome/feedback/rating (D4), e `appointments/service.py` lo chiama su OGNI
mutazione del contratto A31-1. La 078 e' additiva: niente backfill, niente
`appointments`, niente trigger P26.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACCHETTO = ROOT / "buyer_visits"
SERVICE = ROOT / "appointments" / "service.py"
SU = (ROOT / "migrations" / "078_a31_2_buyer_visits_projection.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / "078_a31_2_buyer_visits_projection_down.sql").read_text(encoding="utf-8")


def _codice(percorso: Path) -> str:
    testo = percorso.read_text(encoding="utf-8")
    testo = re.sub(r'""".*?"""', "", testo, flags=re.S)
    return "\n".join(r.split("#", 1)[0] for r in testo.splitlines())


def _sql(testo: str) -> str:
    return "\n".join(r.split("--", 1)[0] for r in testo.splitlines())


def test_01_il_package_non_ha_transazioni_ne_connessioni_proprie():
    for file in PACCHETTO.glob("*.py"):
        codice = _codice(file)
        for vietato in ("commit(", "rollback(", "core_cursor", "get_connection", "connect(",
                        "autocommit", "psycopg2"):
            assert vietato not in codice, (file.name, vietato)


def test_02_d4_mai_outcome_feedback_rating_nelle_scritture():
    grezzo = (PACCHETTO / "projection.py").read_text(encoding="utf-8")
    inserimento = re.search(r"INSERT INTO property_visits\s*\((.*?)\)", grezzo, re.S).group(1)
    for vietato in ("outcome", "feedback", "rating"):
        assert vietato not in inserimento, vietato
    integrazione = _codice(PACCHETTO / "integration.py")
    for vietato in ('"outcome"', '"feedback"', '"rating"', "outcome_note"):
        assert vietato not in integrazione, vietato


def test_03_il_service_chiama_gli_hook_su_ogni_mutazione():
    codice = _codice(SERVICE)
    assert "from buyer_visits import integration as _visite" in codice
    conteggi = {nome: codice.count(f"_visite.{nome}(") for nome in
                ("on_create", "on_status", "on_reschedule", "on_reassign", "before_patch",
                 "on_patch")}
    # on_status: schedule, confirm, cancel, complete, no_show
    assert conteggi == {"on_create": 1, "on_status": 5, "on_reschedule": 1, "on_reassign": 1,
                        "before_patch": 1, "on_patch": 1}, conteggi
    # e sempre PRIMA del mark dirty Google nelle mutazioni che lo fanno
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 1A: gli hook dell'annullamento
    # stanno in `_annulla` (condiviso con «creato per errore»), che
    # `cancel_appointment` chiama: la regola si verifica li'.
    for funzione in ("schedule_appointment", "reassign_appointment", "reschedule_appointment",
                     "_annulla"):
        corpo = codice.split(f"def {funzione}(", 1)[1].split("\ndef ", 1)[0]
        assert corpo.index("_visite.") < corpo.index("_gcal.on_appointment_mutation"), funzione


def test_04_nessun_nuovo_riferimento_alla_tabella_legacy_in_appointments():
    for file in (ROOT / "appointments").glob("*.py"):
        assert "property_visits" not in _codice(file), file.name


def test_05_la_078_e_additiva_senza_backfill():
    su = _sql(SU)
    for vietato in ("INSERT INTO", "UPDATE property_visits", "ALTER TABLE appointments",
                    "property_agency_integrity", "trg_property_visits_agency_integrity",
                    "IF NOT EXISTS", "BEGIN;", "COMMIT;", "DROP "):
        assert vietato not in su, vietato
    assert "REFERENCES appointments (id) ON DELETE RESTRICT" in su
    assert "UNIQUE (appointment_id)" in su
    assert "ADD COLUMN appointment_id BIGINT;" in su
    giu = _sql(GIU)
    for atteso in ("DROP TRIGGER IF EXISTS trg_property_visits_appointment_guard",
                   "DROP FUNCTION IF EXISTS property_visits_appointment_guard()",
                   "DROP COLUMN IF EXISTS appointment_id",
                   "DELETE FROM schema_migrations WHERE version = '078_a31_2_buyer_visits_projection'"):
        assert atteso in giu, atteso
    assert "property_agency_integrity" not in giu and "appointments " not in giu.replace(
        "appointments (id)", "")


def test_06_la_078_e_valida_per_il_runner():
    from scripts import p26_migrate as runner
    trovate = {m.version: m for m in runner.discover_migrations()}
    m = trovate["078_a31_2_buyer_visits_projection"]
    assert runner.validate_migration(m) == [] and m.down_available
    assert not m.non_transactional
