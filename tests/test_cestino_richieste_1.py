"""CESTINO-RICHIESTE-1 (FASE H) - senza database: la 092 per il runner, le rotte,
le esclusioni operative, i confini.

  m01  la 092 e' l'ultima, valida per il runner, additiva, senza BEGIN/COMMIT;
       la down si ferma con richieste nel Cestino o eventi nel registro;
  m02  guardie: righe nuove verso una richiesta nel Cestino (FOR SHARE) su
       ogni tabella che la punta, proposta tramite l'abbinamento, Cestino
       rifiutato con processi aperti, riga congelata (FK SET NULL ammesse);
  r01  quattro rotte nel router buy, tradotte da `tr_trash`, contesto di agenzia;
  r02  il servizio: stessi motivi degli altri Cestini, nessuna cancellazione
       fisica, nessuna UPDATE di righe collegate, nessuna comunicazione toccata;
  f01  ogni superficie operativa sa del Cestino: elenco buy, dashboard,
       scritture (un solo cancello), abbinamenti, proposte, vendite, FLOW, NBA,
       pressione acquirenti, vendita invisibile, Cestino Contatti;
  c01  il 409 BUY_REQUEST_IN_TRASH nella forma {detail, code} in buy, match,
       proposte e vendite; il database lo traduce.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SU = (ROOT / "migrations" / "092_cestino_richieste_1_buy_request_trash.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / "092_cestino_richieste_1_buy_request_trash_down.sql").read_text(encoding="utf-8")
SERVIZIO = ROOT / "buy" / "lifecycle.py"


def _eseguibile(testo):
    return "\n".join(r for r in testo.splitlines() if not r.strip().startswith("--"))


def _testo(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_m01_la_092_e_l_ultima_valida_e_additiva():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    m092 = tutte[-1]
    assert m092.version == "092_cestino_richieste_1_buy_request_trash"
    assert tutte[-2].version == "091_cestino_edifici_1_building_trash"
    assert m092.down_available and not m092.non_transactional and runner.validate_migration(m092) == []
    su = _eseguibile(SU)
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", su, re.M)
    assert not re.search(r"\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b|\bDROP\s+TABLE\b|\bDROP\s+COLUMN\b|\bTRUNCATE\b|"
                         r"\bALTER\s+COLUMN\b", su, re.I)
    for colonna in ("deleted_at TIMESTAMPTZ", "deleted_by_user_id BIGINT", "deleted_reason VARCHAR(30)"):
        assert f"ALTER TABLE buy_requests ADD COLUMN IF NOT EXISTS {colonna};" in su
    assert "'created_by_mistake', 'duplicate', 'invalid_data', 'test_record', 'other'" in su
    assert "CHECK (entity_type IN ('property', 'contact', 'building', 'buy_request'))" in su
    assert "a pre-existing buy request was moved to the trash by the migration" in su
    giu = _eseguibile(GIU)
    assert re.search(r"^BEGIN;", giu, re.M) and re.search(r"^COMMIT;", giu, re.M)
    assert "esistono richieste acquirente nel Cestino; nessuna modifica eseguita" in giu
    assert "il registro contiene eventi di richieste acquirente; nessuna modifica eseguita" in giu
    assert "CHECK (entity_type IN ('property', 'contact', 'building'))" in giu


def test_m02_guardie_dichiarate():
    su = _eseguibile(SU)
    guardia = su[su.index("CREATE OR REPLACE FUNCTION cestino_richieste_guard()"):
                 su.index("CREATE OR REPLACE FUNCTION cestino_richieste_proposal_guard()")]
    assert "FROM buy_requests b WHERE b.id = v_id FOR SHARE" in guardia
    assert "(to_jsonb(OLD) ->> v_colonna) IS NOT DISTINCT FROM (to_jsonb(NEW) ->> v_colonna)" in guardia   # esistenti intatti
    assert "'BUY_REQUEST_IN_TRASH: " in guardia
    tabelle = re.search(r"FOREACH v_tabella IN ARRAY ARRAY\[(.*?)\] LOOP", su, re.S).group(1)
    assert set(re.findall(r"'(\w+)'", tabelle)) == {
        "buy_request_locations", "buy_request_typologies", "buy_request_features", "buy_request_interactions",
        "buy_request_task_links", "buy_request_history", "matches", "match_runs", "match_exclusions",
        "property_sales", "invisible_sale_candidates"}
    assert "BEFORE INSERT OR UPDATE OF buy_request_id ON %I" in su                 # mai DELETE: nessuna cascata bloccata
    proposta = su[su.index("CREATE OR REPLACE FUNCTION cestino_richieste_proposal_guard()"):
                  su.index("CREATE OR REPLACE FUNCTION cestino_richieste_freeze()")]
    assert "SELECT m.buy_request_id INTO v_richiesta FROM matches m WHERE m.id = NEW.match_id" in proposta
    assert "FROM buy_requests b WHERE b.id = v_richiesta FOR SHARE" in proposta
    assert "BEFORE INSERT OR UPDATE OF match_id ON property_proposals" in su
    congela = su[su.index("CREATE OR REPLACE FUNCTION cestino_richieste_freeze()"):]
    for processo in ("pp.status IN ('draft', 'submitted')", "s.status = 'pending'", "t.status IN ('open', 'in_progress')",
                     "v.status IN ('scheduled', 'confirmed') AND v.scheduled_at >= NOW()"):
        assert processo in congela, processo
    assert "'BUY_REQUEST_HAS_OPEN_PROCESSES: " in congela
    ammessi = re.search(r"v_ammessi TEXT\[\] := ARRAY\[(.*?)\];", congela, re.S).group(1)
    assert set(re.findall(r"'(\w+)'", ammessi)) == {"deleted_by_user_id", "lead_id", "updated_at"}   # le FK SET NULL
    assert "(SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'trg_%_buy_trash_guard') <> 12" in su


def test_r01_rotte_nel_router_buy():
    from buy.router import router
    presenti = {(m, r.path) for r in router.routes for m in r.methods}
    assert {("GET", "/api/buy/requests/{request_id}/deletion-check"),
            ("POST", "/api/buy/requests/{request_id}/trash"),
            ("POST", "/api/buy/requests/{request_id}/restore"),
            ("GET", "/api/buy/trash/requests")} <= presenti
    sorgente = _testo("buy/router.py")
    for nome in ("buy_request_deletion_check", "trash_buy_request", "restore_buy_request", "list_buy_request_trash"):
        nodo = next(n for n in ast.walk(ast.parse(sorgente)) if isinstance(n, ast.FunctionDef) and n.name == nome)
        testo = ast.unparse(nodo)
        assert "tr_trash(buy_lifecycle." in testo and "Depends(legacy_basic_agency_context)" in testo, nome
    assert "except buy_lifecycle.BuyTrashNotInstalled as e:\n        return corpo(503, e, 'TRASH_NOT_INSTALLED')" in sorgente


def test_r02_servizio_motivi_nessuna_cancellazione_nessuna_chiusura():
    from buy import lifecycle as bl
    from core import contact_lifecycle as cl
    assert bl.TRASH_REASONS == cl.TRASH_REASONS and bl.TRASH_NOTE_MAX == cl.TRASH_NOTE_MAX
    assert bl.BUY_OPEN == cl.BUY_OPEN and bl.BUY_CLOSED == cl.BUY_CLOSED and bl.PROPOSAL_OPEN == cl.PROPOSAL_OPEN
    codice = re.sub(r'"""[\s\S]*?"""', "", SERVIZIO.read_text(encoding="utf-8"))
    assert not re.search(r"DELETE\s+FROM", codice, re.I)
    scritture = re.findall(r"UPDATE\s+(\w+)\s+SET", codice, re.I)
    assert set(scritture) == {"buy_requests"}, scritture                  # nulla si chiude, si scollega o si sposta
    assert re.findall(r"INSERT INTO (\w+)", codice) == ["record_lifecycle_events"]
    for vietato in ("from communication", "communication_messages", "journey", "pause_automations", "cancel_queued",
                    "appointments SET"):
        assert vietato not in codice, vietato                              # le comunicazioni del contatto non cambiano
    assert "FOR UPDATE OF b" in codice and "FOR KEY SHARE" in codice      # Cestino / ripristino contro il contatto
    assert '"#/contatti/{contatto[\'id\']}"' in SERVIZIO.read_text(encoding="utf-8")


def test_f01_ogni_superficie_sa_del_cestino():
    buy = _testo("buy/repository.py")
    assert 'filters = ["b.archived_at IS NULL", "b.agency_id=%s", _buy_trash.live("b")]' in buy       # elenco, ricerca, 360
    assert buy.count('viva=_buy_trash.live(') == 3                                                    # dashboard
    assert "if not trashed_ok and result.get(\"deleted_at\") is not None:\n        raise _buy_trash.BuyRequestInTrash()" in buy
    assert buy.count("agency_id, trashed_ok=True)") == 2                                                           # sole letture
    assert len(re.findall(r"(?<!def )_refuse_child_of_trashed\(cur, ", buy)) == 4     # update + tre DELETE
    assert '_ciclo.trash_info(cur, ctx, data)' in buy
    match = _testo("match/repository.py")
    assert "JOIN buy_requests b ON b.id=m.buy_request_id AND (to_jsonb(b)->>'deleted_at') IS NULL" in match
    assert match.count("raise _buy_trash.BuyRequestInTrash()") == 2
    assert "AND {_buy_trash.live('b')} ORDER BY id" in match                                           # calcolo per immobile
    assert match.count("_buy_trash.refuse_if_request_in_trash(") == 2
    assert _testo("proposal/repository.py").count("raise BuyRequestInTrash()") == 2
    assert "_buy_trash.refuse_if_request_in_trash(cur, result[\"buy_request_id\"], lock=True)" in _testo("sale/repository.py")
    flow = _testo("flow/adapters.py")
    assert flow.count("br_trash.id=buy_request_id") == 2 and flow.count("br_trash.id=m.buy_request_id") == 2
    assert flow.count("(to_jsonb(buy_requests)->>'deleted_at') IS NULL") == 3
    assert "nba.subject_type = 'buy_request' AND br_trash.id = nba.subject_id" in _testo("next_best_action/repository.py")
    assert _testo("property_watch/repository.py").count("AND (to_jsonb(b)->>'deleted_at') IS NULL") == 2
    vendita = _testo("property_watch/invisible_sale_repository.py")
    assert vendita.count("AND (to_jsonb(b)->>'deleted_at') IS NULL") == 2 and vendita.count("br_trash.id = invisible_sale_candidates.buy_request_id") == 2
    assert "AND (to_jsonb(b)->>'deleted_at') IS NULL ORDER BY id" in _testo("core/contact_lifecycle.py")


def test_c01_forma_del_rifiuto():
    from core.buy_trash import BUY_REQUEST_IN_TRASH, BuyRequestInTrash, is_buy_trash_db_error, live, live_buy_id
    from core.exceptions import ConflictError
    assert BUY_REQUEST_IN_TRASH == "BUY_REQUEST_IN_TRASH" and issubclass(BuyRequestInTrash, ConflictError)
    assert live("x") == "(to_jsonb(x)->>'deleted_at') IS NULL"
    assert "br_trash.id = m.buy_request_id" in live_buy_id("m.buy_request_id")
    assert is_buy_trash_db_error(ValueError("BUY_REQUEST_IN_TRASH")) is False
    assert "except (PropertyInTrash, ContactInTrash, BuyRequestInTrash) as e:return trash_409(e)" in _testo("buy/router.py")
    for router in ("proposal/router.py", "sale/router.py"):
        assert "except (PropertyInTrash, ContactInTrash, BuyRequestInTrash) as exc:" in _testo(router), router
    assert "except BuyRequestInTrash as exc:   # CESTINO-RICHIESTE-1: {detail, code}\n        return trash_409(exc)" in _testo("match/router.py")
    assert "raise BuyRequestInTrash() from exc" in _testo("core/database.py")
