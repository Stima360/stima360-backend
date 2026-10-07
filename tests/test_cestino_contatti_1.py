"""CESTINO-CONTATTI-1 (FASE F) - senza database: la 090 per il runner, le
rotte e il loro scope, i confini fra i moduli, i filtri operativi.

  m01  la 090 e' l'ultima, valida per il runner, additiva (nessun backfill,
       nessuna colonna esistente toccata), senza BEGIN/COMMIT; la down si
       ferma con contatti nel Cestino o eventi nel registro;
  m02  guardie: collegamenti nuovi (9 tabelle, FOR KEY SHARE), riaperture
       (5 tabelle), congelamento della riga con le sole eccezioni dichiarate
       (proiezione del consenso, FK di chi ha spostato);
  r01  quattro rotte nuove nel router CORE, ognuna inoltra il ctx di
       `require_operator` al service; `_translate` invariato; 404 costante;
  r02  il service: motivi del catalogo (gli stessi del Cestino Immobili),
       nessuna cancellazione fisica (nessun DELETE su contacts), il ledger
       delle comunicazioni interrogato solo dalle sue funzioni pubbliche;
  f01  i filtri "fuori dal Cestino" presenti nelle letture operative e nei
       percorsi pubblici, letti via to_jsonb (validi anche senza la 090);
  c01  il codice nuovo importa senza la 090 e i router mappano il 409
       CONTACT_IN_TRASH nella forma {detail, code}.
"""
from __future__ import annotations

import ast
import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SU = (ROOT / "migrations" / "090_cestino_contatti_1_contact_trash.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / "090_cestino_contatti_1_contact_trash_down.sql").read_text(encoding="utf-8")
GUARDATE = ("leads", "property_contacts", "buy_requests", "owner_accounts", "property_sale_sellers",
            "acquisitions", "appointments", "property_visits", "contact_roles")
RIAPERTURE = ("leads", "buy_requests", "acquisitions", "appointments", "owner_accounts")


def _eseguibile(testo):
    return "\n".join(r for r in testo.splitlines() if not r.strip().startswith("--"))


def test_m01_la_090_e_l_ultima_valida_e_additiva():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    m090 = tutte[-1]
    assert m090.version == "090_cestino_contatti_1_contact_trash"
    assert m090.down_available and not m090.non_transactional and runner.validate_migration(m090) == []
    su = _eseguibile(SU)
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", su, re.M)
    assert not re.search(r"\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b|\bDROP\s+TABLE\b|\bDROP\s+COLUMN\b|\bTRUNCATE\b|"
                         r"\bALTER\s+COLUMN\b", su, re.I)
    for colonna in ("deleted_at TIMESTAMPTZ", "deleted_by_user_id BIGINT", "deleted_reason VARCHAR(30)"):
        assert f"ALTER TABLE contacts ADD COLUMN IF NOT EXISTS {colonna};" in su
    assert "'created_by_mistake', 'duplicate', 'invalid_data', 'test_record', 'other'" in su
    assert "CHECK (entity_type IN ('property', 'contact'))" in su
    # nessun contatto entra nel Cestino con la migration
    assert "a pre-existing contact was moved to the trash by the migration" in su
    giu = _eseguibile(GIU)
    assert re.search(r"^BEGIN;", giu, re.M) and re.search(r"^COMMIT;", giu, re.M)
    assert "esistono contatti nel Cestino; nessuna modifica eseguita" in giu
    assert "il registro contiene eventi di contatti; nessuna modifica eseguita" in giu
    assert "CHECK (entity_type IN ('property'))" in giu


def test_m02_guardie_dichiarate():
    su = _eseguibile(SU)
    for tabella in GUARDATE:
        assert f"['{tabella}', " in su, tabella
    assert "FOR KEY SHARE" in su
    blocco = su[su.index("v_riaperture TEXT[][] := ARRAY["):su.index("i INTEGER;")]
    assert [r for r in RIAPERTURE if f"['{r}'," in blocco] == list(RIAPERTURE)
    assert "'open,paused'" in blocco and "'draft,active,paused'" in blocco and "'invited,active'" in blocco
    assert "'requested,scheduled,confirmed'" in blocco
    congelamento = su[su.index("CREATE OR REPLACE FUNCTION cestino_contatti_freeze()"):]
    ammessi = re.search(r"v_ammessi TEXT\[\] := ARRAY\[(.*?)\];", congelamento, re.S).group(1)
    assert set(re.findall(r"'(\w+)'", ammessi)) == {
        "deleted_by_user_id", "updated_at", "marketing_consent", "marketing_consent_at", "marketing_revoked_at",
        "marketing_consent_source", "marketing_consent_notice_id", "privacy_terms_accepted",
        "privacy_terms_accepted_at", "privacy_terms_revoked_at", "privacy_terms_source", "privacy_terms_notice_id"}
    assert "'CONTACT_IN_TRASH: " in su


def test_r01_rotte_nuove_inoltrano_il_proprio_scope():
    from core.router import router
    attese = {("GET", "/api/core/contacts/{contact_id}/deletion-check"), ("POST", "/api/core/contacts/{contact_id}/trash"),
              ("POST", "/api/core/contacts/{contact_id}/restore"), ("GET", "/api/core/trash/contacts")}
    presenti = {(m, r.path) for r in router.routes for m in r.methods}
    assert attese <= presenti
    sorgente = (ROOT / "core" / "router.py").read_text(encoding="utf-8")
    albero = ast.parse(sorgente)
    nomi = {"contact_deletion_check", "trash_contact", "restore_contact", "list_contact_trash"}
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.FunctionDef) and nodo.name in nomi:
            chiamata = next(c for c in ast.walk(nodo) if isinstance(c, ast.Call)
                            and isinstance(c.func, ast.Name) and c.func.id == "_translate_trash")
            assert ast.unparse(chiamata.args[0]) == f"service.{nodo.name}", nodo.name
            assert isinstance(chiamata.args[1], ast.Name) and chiamata.args[1].id == "ctx", nodo.name
            default = ast.unparse(nodo.args.defaults[-1])
            assert "Depends(require_operator)" in default, nodo.name
            nomi.discard(nodo.name)
    assert nomi == set()
    # `_translate` resta quello di prima (sentinelle P26-1 E2), il 404 costante anche qui
    traduci = sorgente[sorgente.index("def _translate_trash("):sorgente.index('@router.get("/contacts/{contact_id}/deletion-check")')]
    assert '"detail": NOT_FOUND_MESSAGE' in traduci
    import core.service as servizio
    for nome in ("contact_deletion_check", "trash_contact", "restore_contact", "list_contact_trash"):
        assert list(inspect.signature(getattr(servizio, nome)).parameters)[0] == "ctx"


def test_r02_service_motivi_nessuna_cancellazione_ledger_dalle_sue_funzioni():
    from core import contact_lifecycle as cl
    from property import lifecycle as pl
    assert cl.TRASH_REASONS == pl.TRASH_REASONS and cl.TRASH_NOTE_MAX == pl.TRASH_NOTE_MAX
    sorgente = (ROOT / "core" / "contact_lifecycle.py").read_text(encoding="utf-8")
    codice = re.sub(r'"""[\s\S]*?"""', "", sorgente)
    assert not re.search(r"DELETE\s+FROM", codice, re.I)
    for privata in ("communication_messages", "communication_automation_controls", "communication_enrollments"):
        assert privata not in codice, privata
    for funzione in ("contact_has_message_history", "count_queued_for_contact", "ledger_installed"):
        assert f"_communication.{funzione}(" in codice, funzione
    assert "journey_repository.cancel_queued_for_contact(" in codice
    assert 'journey_service.pause_automations(ctx, contact_id, reason="contact_trashed", cur=cur)' in codice
    # niente riattivazione al ripristino
    ripristino = codice[codice.index("def restore_contact("):codice.index("def list_trash(")]
    assert "resume" not in ripristino and "enable" not in ripristino


def test_f01_filtri_operativi_e_percorsi_pubblici():
    attesi = {
        "core/repository.py": ["where.append(contact_trash.live(\"c\"))",
                               "AND {contact_trash.live('c')} \"\n                \"ORDER BY c.id FOR UPDATE"],
        "buy/repository.py": ['filters.append(_contact_trash.live("c"))', '_contact_trash.live_contact_id("buy_requests.contact_id")'],
        "match/repository.py": ['filters.append(_contact_trash.live("c"))'],
        "crm/sellers.py": ["\"(to_jsonb(c)->>'deleted_at') IS NULL\""],
        "next_best_action/repository.py": ["AND (c.id IS NULL OR (to_jsonb(c)->>'deleted_at') IS NULL)"],
        "property/repository.py": ["\"AND (to_jsonb(c)->>'deleted_at') IS NULL ORDER BY pc.is_primary DESC,pc.id\")",
                                   "cur.execute(CONTATTI_DELL_IMMOBILE_SQL, (property_id,))"],
        "acquisitions/repository.py": ["AND (to_jsonb(c)->>'deleted_at') IS NULL"],
        "owner/admin_lookup_repository.py": ["AND (to_jsonb(contacts)->>'deleted_at') IS NULL"],
        "owner/repository.py": ["AND (to_jsonb(ct)->>'deleted_at') IS NULL"],
        "appointments/service.py": ["AND c.status <> 'archived' AND (to_jsonb(c)->>'deleted_at') IS NULL"],
        "communication/journey_repository.py": ["(status <> 'active' OR (to_jsonb(contacts)->>'deleted_at') IS NOT NULL)",
                                                "(c.status <> 'active' OR (to_jsonb(c)->>'deleted_at') IS NOT NULL)"],
        "communication/service.py": ['repository.refuse_if_contact_in_trash(cur, prepared["contact_id"])'],
        "property/site_sync.py": ["if stato is not None and stato[\"nel_cestino\"]:"],
    }
    for file, frammenti in attesi.items():
        testo = (ROOT / file).read_text(encoding="utf-8")
        for frammento in frammenti:
            assert frammento in testo, (file, frammento)


def test_c01_import_e_forma_dei_rifiuti():
    from core.contact_trash import CONTACT_IN_TRASH, ContactInTrash, is_contact_trash_db_error, live
    from core.exceptions import ConflictError
    assert CONTACT_IN_TRASH == "CONTACT_IN_TRASH" and issubclass(ContactInTrash, ConflictError)
    assert live("x") == "(to_jsonb(x)->>'deleted_at') IS NULL"
    assert is_contact_trash_db_error(ValueError("CONTACT_IN_TRASH")) is False      # solo errori del database
    for router in ("buy/router.py", "acquisition/router.py", "owner/router_admin.py", "proposal/router.py",
                   "sale/router.py", "property/router.py"):
        testo = (ROOT / router).read_text(encoding="utf-8")
        assert "except (PropertyInTrash, ContactInTrash) as " in testo, router
    database = (ROOT / "core" / "database.py").read_text(encoding="utf-8")
    assert "raise ContactInTrash() from exc" in database
