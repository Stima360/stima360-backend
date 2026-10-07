"""PERTINENZE-1 (FASE E) - senza database: la 089 per il runner, il catalogo
dei tipi di pertinenza, i contratti dello schema.

La prova su PostgreSQL vero e' tests/test_pertinenze_1_postgres.py (e
_site_postgres per il sito); la Shell in tests/test_pertinenze_1_ui.py e in
Chromium in tests/test_pertinenze_1_browser_postgres.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SU = (ROOT / "migrations" / "089_pertinenze_1_unit_nature.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / "089_pertinenze_1_unit_nature_down.sql").read_text(encoding="utf-8")


def _eseguibile(testo):
    return "\n".join(r for r in testo.splitlines() if not r.strip().startswith("--"))


def test_m01_la_089_e_l_ultima_valida_per_il_runner_e_additiva():
    # SENTINELLA AGGIORNATA DA PERTINENZE-1: la 089 e' l'ultima
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    m089 = tutte[-1]
    assert m089.version == "089_pertinenze_1_unit_nature"
    assert m089.down_available and not m089.non_transactional and runner.validate_migration(m089) == []
    su = _eseguibile(SU)
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", su, re.M)
    # additiva: nessun backfill, nessuna colonna esistente toccata
    assert not re.search(r"\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b|\bDROP\s+TABLE\b|\bDROP\s+COLUMN\b|\bTRUNCATE\b|"
                         r"\bALTER\s+COLUMN\b", su, re.I)
    assert "ADD COLUMN IF NOT EXISTS is_pertinenza BOOLEAN NOT NULL DEFAULT FALSE" in su
    assert "ADD COLUMN IF NOT EXISTS pertinenza_kind VARCHAR(30)" in su
    # la down: transazione propria, si ferma con pertinenze marcate
    assert re.search(r"^\s*BEGIN\s*;", GIU, re.M) and re.search(r"^\s*COMMIT\s*;", GIU, re.M)
    assert "RAISE EXCEPTION" in GIU and "089 down" in GIU


def test_m02_i_tipi_di_pertinenza_sono_quelli_degli_accessori():
    from property import catalog, schemas
    check = SU.split("properties_pertinenza_kind_chk CHECK")[1].split(";")[0]
    nella_089 = re.findall(r"'(\w+)'", check)
    assert set(nella_089) == set(schemas.ACCESSORY_KINDS)
    nella_087 = re.findall(r"'(\w+)'", (ROOT / "migrations" / "087_catalogo_canonico_1_site_attributes.sql")
                           .read_text(encoding="utf-8").split("property_accessories_kind_chk CHECK (kind IN")[1].split(";")[0])
    assert set(nella_089) == set(nella_087)
    # la tipologia di partenza: solo tipi del catalogo, verso tipologie valide
    assert set(catalog.PERTINENZA_PROPERTY_TYPE) <= set(schemas.ACCESSORY_KINDS)
    assert set(catalog.PERTINENZA_PROPERTY_TYPE.values()) <= set(schemas.PROPERTY_TYPES)
    assert catalog.PERTINENZA_PROPERTY_TYPE["box"] == catalog.PERTINENZA_PROPERTY_TYPE["posto_auto"] == "garage"
    from property import census
    assert census.ACCESSORY_TO_TYPE is catalog.PERTINENZA_PROPERTY_TYPE          # un solo elenco


def test_s01_lo_schema_della_unita_accetta_la_natura_e_valida_il_tipo():
    from pydantic import ValidationError
    from property.schemas import CensusUnitCreate
    u = CensusUnitCreate(is_pertinenza=True, pertinenza_kind="posto_auto", property_type="garage")
    assert (u.is_pertinenza, u.pertinenza_kind) == (True, "posto_auto")
    assert CensusUnitCreate().is_pertinenza is False and CensusUnitCreate().pertinenza_kind is None
    with pytest.raises(ValidationError):
        CensusUnitCreate(pertinenza_kind="posto_barca")


def test_s02_le_letture_valgono_anche_senza_la_089():
    """Codice prima dello schema: ogni lettura della natura passa da
    to_jsonb(riga), mai dalla colonna nuova direttamente."""
    from property import census
    for alias in ("p", "u"):
        assert "to_jsonb(" in census.pertinenza_sql(alias) and f"{alias}.parent_property_id" in census.pertinenza_sql(alias)
        assert "is_pertinenza" not in census.pertinenza_sql(alias).replace("'is_pertinenza'", "")
    sorgente = (ROOT / "property" / "census.py").read_text(encoding="utf-8")
    # le scritture della natura passano solo da _ha_089
    for riga in [r for r in sorgente.splitlines() if "is_pertinenza = TRUE" in r]:
        assert "_ha_089" in riga or "if _ha_089(cur)" in riga or "natura" in riga, riga
    site = (ROOT / "property" / "site_sync.py").read_text(encoding="utf-8")
    assert "to_jsonb(p)->>'pertinenza_kind'" in site and "from_accessory" in site


def test_s03_summary_from_counts_conta_una_volta_sola():
    from property import census
    s = census.summary_from_counts(6, 5, 3, 2, 1, 1)
    assert (s["units_counted"], s["units_main"], s["units_pertinenze"], s["units_pertinenze_unlinked"]) == (6, 3, 2, 1)
    assert s["units_to_complete"] == 0
    assert census.summary_from_counts(None, 1, 1, 0, 0)["units_pertinenze_unlinked"] == 0
