"""CENSIMENTO-1 / migration 083 - la prova statica che accompagna la migration
nel commit DB-FIRST (prima del codice che la usa).

Solo i file e il runner: nessun import applicativo, cosi' questa prova vale
identica nel commit che porta SOLO la 083 e in quelli che porteranno backend
e UI. Il comportamento su PostgreSQL vero e' in
`tests/test_censimento_1_schema_postgres.py`.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONE = "083_censimento_1_buildings_units"
SU = (ROOT / "migrations" / f"{VERSIONE}.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")


def _eseguibile(testo):
    return "\n".join(r for r in testo.splitlines() if not r.lstrip().startswith("--"))


def test_m01_la_083_e_valida_per_il_runner_e_l_ultima():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 1A: la 083 resta valida ma non
    # e' piu' l'ultima; la 084 (cancelled_kind, created_by_mistake) la segue.
    m083 = [m for m in tutte if m.version == VERSIONE][0]
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 2B1: poi la 085 (Cestino Immobili).
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 2B2: poi la 086 (guardie del Cestino).
    # SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1: poi la 087 (attributi del sito).
    # SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1: poi la 088 (ricezione degli invii del sito), che ora e' l'ultima.
    # SENTINELLA AGGIORNATA DA PERTINENZE-1: poi la 089 (natura di pertinenza), che ora e' l'ultima.
    assert tutte[-9].version == "086_delete_arch_2b2_property_trash_guards"
    assert tutte[-8].version == "087_catalogo_canonico_1_site_attributes"
    assert tutte[-10].version == "085_delete_arch_2b1_property_trash"
    assert tutte[-11].version == "084_delete_arch_1a_mistakes" and tutte[-12] is m083
    assert tutte[-7].version == "088_catalogo_canonico_1b_site_inbox"
    # SENTINELLA AGGIORNATA DA CESTINO-CONTATTI-1: poi la 090 (Cestino contatti), che ora e' l'ultima.
    assert tutte[-6].version == "089_pertinenze_1_unit_nature"
    # SENTINELLA AGGIORNATA DA CESTINO-EDIFICI-1: poi la 091 (Cestino edifici), che ora e' l'ultima.
    assert tutte[-5].version == "090_cestino_contatti_1_contact_trash"
    assert tutte[-4].version == "091_cestino_edifici_1_building_trash"
    # SENTINELLA AGGIORNATA DA CESTINO-RICHIESTE-1: poi la 092 (Cestino richieste), che ora e' l'ultima.
    assert tutte[-3].version == "092_cestino_richieste_1_buy_request_trash"
    # SENTINELLA AGGIORNATA DA STIMA-CRM-AGENDA-1: finestra spostata di due, poi la 093 (PDF privato della stima, F07) e la 094 (ricevute degli invii pubblici, F04); la 094 e' ora l'ultima.
    assert tutte[-2].version == "093_stima_private_pdf"
    assert tutte[-1].version == "094_public_submission_receipts"
    assert m083.down_available and not m083.non_transactional
    assert runner.validate_migration(m083) == []
    # il runner possiede la transazione: nessun BEGIN/COMMIT nel file su,
    # la down si bracketta da sola
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", _eseguibile(SU), re.M)
    assert re.search(r"^\s*BEGIN\s*;", GIU, re.M) and re.search(r"^\s*COMMIT\s*;", GIU, re.M)
    assert "DELETE FROM schema_migrations" not in GIU


def test_m02_additiva_e_senza_backfill():
    e = _eseguibile(SU)
    # solo due tabelle nuove; nessuna riga storica letta per essere riscritta
    assert re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", e) == ["buildings", "property_accessories"]
    for vietato in ("UPDATE properties", "UPDATE buildings", "INSERT INTO", "DELETE FROM", "TRUNCATE",
                    "DROP COLUMN", "SET NOT NULL", "ALTER COLUMN"):
        assert vietato not in e, vietato
    # ogni colonna nuova su properties e' NULLABLE o ha un default neutro
    colonne = re.findall(r"ALTER TABLE properties ADD COLUMN IF NOT EXISTS\s+(\w+)\s+([^;]+);", e)
    assert {c for c, _ in colonne} == {
        "building_id", "parent_property_id", "whole_building", "staircase", "internal_number",
        "cadastral_municipality_code", "cadastral_section", "cadastral_sheet", "cadastral_parcel",
        "cadastral_subunit", "cadastral_category", "record_kind", "address_inherited",
        "client_request_id", "client_request_fingerprint"}
    for nome, definizione in colonne:
        if "NOT NULL" in definizione:
            assert "DEFAULT" in definizione, nome
    assert "record_kind                 VARCHAR(10) NOT NULL DEFAULT 'crm'" in SU
    assert "whole_building              BOOLEAN NOT NULL DEFAULT FALSE" in SU
    assert "address_inherited           BOOLEAN NOT NULL DEFAULT FALSE" in SU


def test_m03_le_regole_della_rev_3_1_sono_nel_file():
    # nessun UNIQUE catastale sugli edifici, nessun UNIQUE su scala/piano/interno
    assert "uq_buildings_cadastral" not in SU
    assert not re.search(r"UNIQUE INDEX[^;]*staircase", SU)
    # identita' catastale delle unita' solo se completa, sezione conosciuta
    idx = SU.split("uq_properties_cadastral_identity")[1].split(";")[0]
    assert "cadastral_section IS NOT NULL" in idx and "cadastral_subunit IS NOT NULL" in idx
    # guardia census su INSERT e UPDATE, promozione senza altri cambi
    assert "BEFORE INSERT OR UPDATE OF record_kind, commercial_status" in SU
    assert "cannot go back to census" in SU and "cannot change its commercial fields in the same statement" in SU
    assert "NOT IN ('draft', 'archived')" in SU
    # collegamenti: stessa agenzia, profondita' 1, niente cicli
    assert "cannot be a parent" in SU and "cannot become a pertinenza" in SU
    assert "properties_parent_not_self_chk" in SU
    # "Da verificare" non e' mai un codice; idempotenza con impronta
    assert "'^[A-F]/[0-9]{1,2}$'" in SU
    assert SU.count("client_request_fingerprint") >= 6
    # lato genitore: ne' l'edificio con unita' ne' l'unita' con pertinenze cambiano agenzia
    assert "has linked properties and cannot change agency" in SU and "has linked pertinenze and cannot change agency" in SU
    assert "BEFORE UPDATE OF agency_id ON buildings" in SU
    # concorrenza: lock advisory in ordine canonico + edificio letto FOR SHARE
    assert "pg_advisory_xact_lock(83" in SU and "ORDER BY x" in SU and "WHERE id = NEW.building_id FOR SHARE" in SU
    # concorrenza (REV 3): la protezione vale solo in READ COMMITTED; fuori, le sole operazioni
    # di collegamento/cambio agenzia sono rifiutate esplicitamente (SQLSTATE 25000), il resto no
    assert "CREATE OR REPLACE FUNCTION censimento_require_read_committed(" in SU
    assert "ERRCODE = 'invalid_transaction_state'" in SU
    assert SU.count("PERFORM censimento_require_read_committed(") == 2
    assert "requires a READ COMMITTED transaction" in SU
    assert "un lock advisory NON\n-- rinnova lo snapshot" in SU          # la frase corretta della REV 3
    assert "L'ordine crescente vale dentro la SINGOLA chiamata" in SU    # deadlock: portata ridimensionata
    assert "40P01" in SU and "ritenta" in SU
    # idempotenza: chiave e impronta immutabili su tutte e tre le tabelle
    assert SU.count("EXECUTE FUNCTION client_request_immutable()") == 3
    # il CHECK sulla categoria e' dichiarato di FORMATO, non di catalogo
    assert "Il CHECK e' di FORMATO" in SU
    # la down rifiuta anche per i valori scritti sugli immobili storici, e toglie tutto cio' che la up ha creato
    assert "RAISE EXCEPTION" in GIU and "cadastral_category IS NOT NULL" in GIU and "record_kind <> 'crm'" in GIU
    for fn in re.findall(r"CREATE OR REPLACE FUNCTION (\w+)\(", SU):
        assert f"DROP FUNCTION IF EXISTS {fn}(" in GIU, fn
    for tg in re.findall(r"CREATE TRIGGER (\w+)", SU):
        assert f"DROP TRIGGER IF EXISTS {tg} ON" in GIU, tg
