"""CRM-OPS-4 / migration 082 - la prova statica che accompagna la migration
nel commit DB-FIRST (prima del codice che la usa).

Solo il file e il runner: nessun import applicativo, cosi' questa prova vale
identica nel commit che porta SOLO la 082 e in quello che porta il codice.
Il comportamento su PostgreSQL vero e' in
`tests/test_crm_ops_4_mandates_postgres.py` (commit applicativo).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SU = (ROOT / "migrations" / "082_crm_ops_4_property_interactions.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / "082_crm_ops_4_property_interactions_down.sql").read_text(encoding="utf-8")


def test_m01_la_082_e_valida_per_il_runner_e_l_ultima():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    # SENTINELLA AGGIORNATA DA CENSIMENTO-1: la 082 resta valida ma non e' piu'
    # l'ultima; la 083 (edifici e unita' censite) la segue e viene nominata.
    m082 = [m for m in tutte if m.version == "082_crm_ops_4_property_interactions"][0]
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 1A: dopo la 083 viene la 084.
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 2B1: dopo la 084 viene la 085.
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 2B2: dopo la 085 viene la 086.
    # SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1: dopo la 086 viene la 087.
    # SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1: poi la 088 (ricezione degli invii del sito), che ora e' l'ultima.
    assert tutte[-6].version == "083_censimento_1_buildings_units" and tutte[-7] is m082
    assert tutte[-5].version == "084_delete_arch_1a_mistakes"
    assert tutte[-4].version == "085_delete_arch_2b1_property_trash"
    assert tutte[-3].version == "086_delete_arch_2b2_property_trash_guards"
    assert tutte[-2].version == "087_catalogo_canonico_1_site_attributes"
    assert tutte[-1].version == "088_catalogo_canonico_1b_site_inbox"
    assert runner.validate_migration(m082) == []
    # additiva: nessuna tabella nuova, nessun backfill, la down rifiuta con dati
    eseguibile = "\n".join(r for r in SU.splitlines() if not r.lstrip().startswith("--"))
    for vietato in ("CREATE TABLE", "UPDATE activities", "INSERT INTO", "DELETE FROM"):
        assert vietato not in eseguibile, vietato
    assert "ADD COLUMN IF NOT EXISTS property_id BIGINT;" in SU and "ON DELETE RESTRICT" in SU
    assert "property_id IS NOT NULL" in SU.split("activities_reference_chk CHECK")[1][:300]
    assert "RAISE EXCEPTION" in GIU and "property_id IS NOT NULL" in GIU



def test_m02_compatibile_con_il_codice_che_non_la_usa_ancora():
    """DB-FIRST: con la 082 applicata, il codice di PRIMA (che non conosce
    `property_id`) continua a funzionare: colonna NULLABLE senza default
    obbligatorio, CHECK piu' largo (ogni riga esistente lo soddisfa), trigger
    che scattano SOLO su righe con property_id, nessun NOT NULL nuovo, nessuna
    riscrittura di dati."""
    eseguibile = "\n".join(r for r in SU.splitlines() if not r.lstrip().startswith("--"))
    assert "ADD COLUMN IF NOT EXISTS property_id BIGINT;" in eseguibile
    assert "SET NOT NULL" not in eseguibile and "NOT NULL DEFAULT" not in eseguibile
    assert "contact_id IS NOT NULL OR lead_id IS NOT NULL OR stima_id IS NOT NULL" in eseguibile
    assert "WHEN (OLD.property_id IS NOT NULL)" in eseguibile
    assert "IF NEW.property_id IS NULL THEN\n        RETURN NEW;" in eseguibile
    # la down rifiuta se esiste storico, e toglie tutto il resto
    for oggetto in ("trg_activities_property_history", "trg_activities_property_scope",
                    "idx_activities_agency_property_occurred", "activities_property_id_fkey"):
        assert oggetto in GIU, oggetto
