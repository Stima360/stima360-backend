"""CESTINO-EDIFICI-1 (FASE G) - senza database: la 091 per il runner, le rotte,
i filtri, i confini.

  m01  la 091 e' l'ultima, valida per il runner, additiva, senza BEGIN/COMMIT;
       la down si ferma con edifici nel Cestino o eventi nel registro;
  m02  guardie: unita' nuove verso un edificio nel Cestino (FOR SHARE, come il
       trigger 083), Cestino rifiutato con QUALUNQUE riga collegata, riga
       congelata; `uq_buildings_client_request` non toccato;
  r01  quattro rotte nel router Immobili, tradotte da `trc`, contesto di agenzia;
  r02  il servizio: stessi motivi del Cestino Immobili, nessuna cancellazione
       fisica, nessuna UPDATE di `properties` (nulla si scollega o si sposta);
  f01  lista Edifici / candidati, «palazzina simile», scrittura e creazione
       unita' sanno del Cestino; la scheda resta leggibile;
  c01  il 409 BUILDING_IN_TRASH nella forma {detail, code}; la sonda anonima
       del certificatore copre le rotte nuove.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SU = (ROOT / "migrations" / "091_cestino_edifici_1_building_trash.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / "091_cestino_edifici_1_building_trash_down.sql").read_text(encoding="utf-8")
SERVIZIO = ROOT / "property" / "building_lifecycle.py"


def _eseguibile(testo):
    return "\n".join(r for r in testo.splitlines() if not r.strip().startswith("--"))


def test_m01_la_091_e_l_ultima_valida_e_additiva():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    # SENTINELLA AGGIORNATA DA STIMA-CRM-AGENDA-1: la 093 (PDF privato, F07) e la 094 (ricevute, F04/F06) seguono la 092 (Cestino richieste); la 091 resta valida e additiva.
    # SENTINELLA AGGIORNATA DA SITE-IMPORT-1: la 095 (registro dell'importazione dal sito, site_import_records), additiva, e' ora l'ultima.
    assert tutte[-1].version == "095_site_import_ledger"
    assert tutte[-2].version == "094_public_submission_receipts"
    assert tutte[-3].version == "093_stima_private_pdf"
    assert tutte[-4].version == "092_cestino_richieste_1_buy_request_trash"
    m091 = tutte[-5]
    assert m091.version == "091_cestino_edifici_1_building_trash"
    assert m091.down_available and not m091.non_transactional and runner.validate_migration(m091) == []
    su = _eseguibile(SU)
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", su, re.M)
    assert not re.search(r"\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b|\bDROP\s+TABLE\b|\bDROP\s+COLUMN\b|\bTRUNCATE\b|"
                         r"\bALTER\s+COLUMN\b", su, re.I)
    for colonna in ("deleted_at TIMESTAMPTZ", "deleted_by_user_id BIGINT", "deleted_reason VARCHAR(30)"):
        assert f"ALTER TABLE buildings ADD COLUMN IF NOT EXISTS {colonna};" in su
    assert "'created_by_mistake', 'duplicate', 'invalid_data', 'test_record', 'other'" in su
    assert "CHECK (entity_type IN ('property', 'contact', 'building'))" in su
    assert "a pre-existing building was moved to the trash by the migration" in su
    giu = _eseguibile(GIU)
    assert re.search(r"^BEGIN;", giu, re.M) and re.search(r"^COMMIT;", giu, re.M)
    assert "esistono edifici nel Cestino; nessuna modifica eseguita" in giu
    assert "il registro contiene eventi di edifici; nessuna modifica eseguita" in giu
    assert "CHECK (entity_type IN ('property', 'contact'))" in giu


def test_m02_guardie_dichiarate():
    su = _eseguibile(SU)
    guardia = su[su.index("CREATE OR REPLACE FUNCTION cestino_edifici_unit_guard()"):su.index("CREATE OR REPLACE FUNCTION cestino_edifici_freeze()")]
    assert "FROM buildings b WHERE b.id = NEW.building_id FOR SHARE" in guardia
    assert "OLD.building_id IS NOT DISTINCT FROM NEW.building_id" in guardia          # collegamenti esistenti intatti
    assert "'BUILDING_IN_TRASH: " in guardia
    congela = su[su.index("CREATE OR REPLACE FUNCTION cestino_edifici_freeze()"):]
    assert "EXISTS (SELECT 1 FROM properties p WHERE p.building_id = OLD.id)" in congela     # QUALUNQUE riga
    assert "'BUILDING_HAS_UNITS: " in congela
    ammessi = re.search(r"v_ammessi TEXT\[\] := ARRAY\[(.*?)\];", congela, re.S).group(1)
    assert set(re.findall(r"'(\w+)'", ammessi)) == {"deleted_by_user_id", "updated_at"}
    assert "BEFORE INSERT OR UPDATE OF building_id ON properties" in su
    assert "DROP INDEX" not in su and "uq_buildings_client_request" not in su


def test_r01_rotte_nel_router_immobili():
    from property.router import router
    presenti = {(m, r.path) for r in router.routes for m in r.methods}
    assert {("GET", "/api/property/buildings/{building_id}/deletion-check"),
            ("POST", "/api/property/buildings/{building_id}/trash"),
            ("POST", "/api/property/buildings/{building_id}/restore"),
            ("GET", "/api/property/trash/buildings")} <= presenti
    sorgente = (ROOT / "property" / "router.py").read_text(encoding="utf-8")
    for nome in ("building_deletion_check", "trash_building", "restore_building", "list_building_trash"):
        nodo = next(n for n in ast.walk(ast.parse(sorgente)) if isinstance(n, ast.FunctionDef) and n.name == nome)
        testo = ast.unparse(nodo)
        assert "trc(building_lifecycle." in testo and "Depends(legacy_basic_agency_context)" in testo, nome


def test_r02_servizio_motivi_nessuna_cancellazione_nessuno_scollegamento():
    from property import building_lifecycle as bl
    from property import lifecycle as pl
    assert bl.TRASH_REASONS == pl.TRASH_REASONS and bl.TRASH_NOTE_MAX == pl.TRASH_NOTE_MAX
    codice = re.sub(r'"""[\s\S]*?"""', "", SERVIZIO.read_text(encoding="utf-8"))
    assert not re.search(r"DELETE\s+FROM", codice, re.I)
    assert not re.search(r"UPDATE\s+properties", codice, re.I)               # nulla si scollega o si sposta
    assert re.search(r"SELECT \* FROM buildings WHERE id = %s AND agency_id = %s\{' FOR UPDATE' if lock", codice)
    # il blocco conta TUTTE le righe dell'edificio: nessun filtro su archivio o Cestino
    grezzo = SERVIZIO.read_text(encoding="utf-8")
    unita = grezzo[grezzo.index("def _unita_collegate"):grezzo.index("def _stato_unita")]
    assert "FROM properties p WHERE p.building_id = %s ORDER BY p.id" in unita
    assert "archived_at IS NULL" not in unita and "deleted_at') IS NULL" not in unita
    ripristino = codice[codice.index("def restore_building"):codice.index("TRASH_LIST_MAX")]
    assert "census._simili_edificio" in codice and "possible_duplicates" in ripristino


def test_f01_letture_e_scritture_sanno_del_cestino():
    census = (ROOT / "property" / "census.py").read_text(encoding="utf-8")
    assert 'condizioni, params = ["b.agency_id = %s", "b.archived_at IS NULL", _building_trash.live("b")], [agency_id]' in census
    assert "f\"AND {_building_trash.live('b')} \"" in census                    # «palazzina simile»
    assert "if riga.get(\"deleted_at\") is not None and not trashed_ok:\n        raise BuildingInTrash()" in census
    assert "edificio = _edificio(cur, agency_id, building_id, trashed_ok=True)" in census          # scheda leggibile
    assert "raise BuildingInTrash(REPLICA_IN_TRASH_MESSAGE)" in census                            # retry guidata
    assert "edificio = _edificio(cur, agency_id, data[\"building_id\"], share=True)" in census     # unita' nuove


def test_c01_forma_del_rifiuto_e_certificatore():
    from core.building_trash import BUILDING_IN_TRASH, BuildingInTrash, is_building_trash_db_error, live
    from core.exceptions import ConflictError
    assert BUILDING_IN_TRASH == "BUILDING_IN_TRASH" and issubclass(BuildingInTrash, ConflictError)
    assert live("x") == "(to_jsonb(x)->>'deleted_at') IS NULL"
    assert is_building_trash_db_error(ValueError("BUILDING_IN_TRASH")) is False
    assert "except (PropertyInTrash, ContactInTrash, BuildingInTrash) as " in (ROOT / "property" / "router.py").read_text()
    assert "raise BuildingInTrash() from exc" in (ROOT / "core" / "database.py").read_text()
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_6_live_cert as cert
    assert {("GET", "/api/property/buildings/{id}/deletion-check"), ("POST", "/api/property/buildings/{id}/trash"),
            ("POST", "/api/property/buildings/{id}/restore")} <= set(cert.BUILDINGS_OPERAZIONI)
