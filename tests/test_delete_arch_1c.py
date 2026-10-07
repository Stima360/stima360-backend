"""DELETE-ARCH Fase 1C - task e attivita' «creati per errore»: contratti
statici e UI (nessun database). Compagno di `test_delete_arch_1c_postgres.py`.
"""
from __future__ import annotations

import inspect
import subprocess

from tests.test_delete_arch_0 import ASSETS, ATTIVITA_JS, ROOT, _node
from tests.test_os_shell_p25_3_property_lifecycle import _function_block, _read, _strip_js_line_comments

DIALOGS_JS = ASSETS / "components" / "activity-task-dialogs.js"
OGGI_JS = ASSETS / "views" / "oggi.js"


def test_b01_nessuna_migration_rotte_e_schema():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 2B1: la 1C resta senza migration; dopo la 084 viene la 085
    # (Cestino Immobili).
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 2B2: poi la 086.
    # SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1: la 087 e' degli attributi del sito, nominata; nessuna oltre.
    # SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1: la 088 (ricezione degli invii del sito), nominata; nessuna oltre.
    # SENTINELLA AGGIORNATA DA PERTINENZE-1: poi la 089 (natura di pertinenza), che ora e' l'ultima.
    assert runner.discover_migrations()[-8].version == "084_delete_arch_1a_mistakes"
    assert runner.discover_migrations()[-7].version == "085_delete_arch_2b1_property_trash"
    assert runner.discover_migrations()[-6].version == "086_delete_arch_2b2_property_trash_guards"
    assert runner.discover_migrations()[-5].version == "087_catalogo_canonico_1_site_attributes"
    assert runner.discover_migrations()[-4].version == "088_catalogo_canonico_1b_site_inbox"
    # SENTINELLA AGGIORNATA DA CESTINO-CONTATTI-1: poi la 090 (Cestino contatti), che ora e' l'ultima.
    assert runner.discover_migrations()[-3].version == "089_pertinenze_1_unit_nature"
    # SENTINELLA AGGIORNATA DA CESTINO-EDIFICI-1: poi la 091 (Cestino edifici), che ora e' l'ultima.
    assert runner.discover_migrations()[-2].version == "090_cestino_contatti_1_contact_trash"
    assert runner.discover_migrations()[-1].version == "091_cestino_edifici_1_building_trash"
    from core.router import router
    rotte = {(m, r.path) for r in router.routes for m in r.methods}
    assert ("POST", "/api/core/tasks/{task_id}/mark-mistake") in rotte
    assert ("POST", "/api/core/activities/{activity_id}/mark-mistake") in rotte
    from core.schemas import MistakeMark
    assert set(MistakeMark.__fields__) == {"note"}


def test_b02_mai_delete_e_flag_del_server():
    from core import repository
    for fn in (repository.mark_task_mistake, repository.mark_activity_mistake):
        corpo = inspect.getsource(fn)
        assert "DELETE FROM" not in corpo and "FOR UPDATE" in corpo and "metadata ||" in corpo
        assert "description" not in corpo.split("UPDATE")[-1]          # il testo non si tocca
    assert "status = 'cancelled'" in inspect.getsource(repository.mark_task_mistake)
    assert repository.MISTAKE_KEYS[0] == "mistake"
    from core import service
    for fn in (service.create_task, service.create_activity, service.update_task):
        assert "_reject_mistake_keys(data.get(\"metadata\"))" in inspect.getsource(fn), fn


def test_b03_contatori_operativi_escludono_gli_errori():
    from flow import adapters
    assert "COALESCE((metadata->>'mistake')::boolean, FALSE) = FALSE" in adapters._ACTIVITY_COUNT_SQL
    assert inspect.getsource(adapters).count("_ACTIVITY_COUNT_SQL, (entity_id,)") == 2
    from database_revival import eligibility
    for sql in (eligibility._LAST_ACTIVITY_EXPR_SQL, eligibility._LAST_ACTIVITY_EXPR_SQL_SCOPED):
        assert "COALESCE((a.metadata->>'mistake')::boolean, FALSE) = FALSE" in sql
    assert "/api/core/activities?limit=20&mistakes=false" in _read(OGGI_JS)


def _azioni(sessione, task, attivita, conferma=""):
    testo = _read(ATTIVITA_JS)
    blocchi = "\n".join(_function_block(testo, n) for n in (
        "isMistake", "canMarkMistake", "mistakeConfirm", "renderTaskActions", "renderCronologiaActions"))
    import json
    return _node(f"""
const escapeHtml = (v) => String(v ?? '');
const OPEN_TASK_STATUSES = ['open', 'in_progress'];
const GENERATED_TASK_SOURCES = ['flow', 'followup', 'automated'];
const GENERATED_ACTIVITY_TYPES = ['status_change', 'system', 'valuation'];
const getSession = () => ({json.dumps(sessione)});
{blocchi}
const conferma = new Set({json.dumps([conferma] if conferma else [])});
process.stdout.write(JSON.stringify({{
  task: renderTaskActions({json.dumps(task)}, conferma),
  att: renderCronologiaActions({{ kind: 'attivita', data: {json.dumps(attivita)} }}, conferma),
}}));
""")


def test_u01_nessun_elimina_si_creato_per_errore_con_le_regole_del_backend():
    agente = {"role": "agent", "user_id": 3}
    proprio_t = {"id": 5, "status": "open", "created_by_user_id": 3, "metadata": {}}
    proprio_a = {"id": 9, "activity_type": "call", "created_by_user_id": 3, "metadata": {}}
    e = _azioni(agente, proprio_t, proprio_a)
    assert 'data-mistake-task="5"' in e["task"] and "Creato per errore" in e["task"]
    assert 'data-mistake-activity="9"' in e["att"] and "Inserita per errore" in e["att"]
    for html in e.values():
        assert "data-delete" not in html and "Elimina" not in html
    # agente sul record di un collega: nessuna voce (il backend risponde 403)
    e = _azioni(agente, {**proprio_t, "created_by_user_id": 4}, {**proprio_a, "created_by_user_id": 4})
    assert "data-mistake" not in e["task"] + e["att"] and "data-edit-task" in e["task"]
    # admin, owner, platform: su tutta l'agenzia
    for sessione in ({"role": "agency_admin", "user_id": 1}, {"role": "agency_owner", "user_id": 1},
                     {"role": None, "user_id": 1, "is_platform_admin": True}):
        e = _azioni(sessione, {**proprio_t, "created_by_user_id": 4}, {**proprio_a, "created_by_user_id": 4})
        assert "data-mistake-task" in e["task"] and "data-mistake-activity" in e["att"]
    # completato, annullato, generato, gia' segnato: nessuna voce
    admin = {"role": "agency_owner", "user_id": 1}
    for t in ({**proprio_t, "status": "completed"}, {**proprio_t, "status": "cancelled"},
              {**proprio_t, "metadata": {"source": "flow"}}, {**proprio_t, "metadata": {"mistake": True}}):
        assert "data-mistake" not in _azioni(admin, t, proprio_a)["task"], t
    assert _azioni(admin, proprio_t, {**proprio_t, "metadata": {"mistake": True}})["task"] != ""
    for a in ({**proprio_a, "activity_type": "status_change"}, {**proprio_a, "metadata": {"mistake": True}}):
        assert "data-mistake" not in _azioni(admin, proprio_t, a)["att"], a
    # un task segnato non offre piu' nemmeno Modifica
    assert _azioni(admin, {**proprio_t, "metadata": {"mistake": True}}, proprio_a)["task"] == ""
    # conferma inline con nota facoltativa
    e = _azioni(agente, proprio_t, proprio_a, conferma="task:5")
    assert 'data-mistake-task-confirm="5"' in e["task"] and 'data-mistake-note="task:5"' in e["task"]


def test_u02_cronologia_distingue_errore_da_annullamento_e_mostra_il_testo():
    testo = _read(ATTIVITA_JS)
    blocchi = "\n".join(_function_block(testo, n) for n in (
        "isMistake", "renderCronologiaTypeBadge", "renderCronologiaDescription"))
    e = _node(f"""
const escapeHtml = (v) => String(v ?? '');
const renderBadge = (l, tono) => `[${{l}}|${{tono}}]`;
const TASK_STATUS_LABELS = {{ cancelled: 'Annullato', completed: 'Completato' }};
const VISIT_STATUS_LABELS = {{}};
const ACTIVITY_TYPE_LABELS = {{ call: 'Telefonata' }};
{blocchi}
const errore = {{ kind: 'task', data: {{ status: 'cancelled', metadata: {{ mistake: true }} }} }};
const annullato = {{ kind: 'task', data: {{ status: 'cancelled', metadata: {{}} }} }};
const att = {{ kind: 'attivita', data: {{ activity_type: 'call', description: 'Ha chiesto il prezzo',
               metadata: {{ mistake: true, mistake_note: 'contatto sbagliato' }} }} }};
process.stdout.write(JSON.stringify({{
  errore: renderCronologiaTypeBadge(errore), annullato: renderCronologiaTypeBadge(annullato),
  attBadge: renderCronologiaTypeBadge(att), attTesto: renderCronologiaDescription(att),
}}));
""")
    assert e["errore"] == "[Task · Creato per errore|gray]" and e["annullato"] == "[Task · Annullato|danger]"
    assert "[Inserita per errore|warn]" in e["attBadge"] and "[Telefonata|gray]" in e["attBadge"]
    assert e["attTesto"].startswith("Ha chiesto il prezzo") and "contatto sbagliato" in e["attTesto"]


def test_u03_dialogs_senza_delete_e_js_valido():
    dialoghi = _strip_js_line_comments(_read(DIALOGS_JS))
    assert "apiDelete" not in dialoghi and "deleteTask" not in dialoghi and "deleteActivity" not in dialoghi
    assert "/mark-mistake`" in dialoghi and dialoghi.count("export async function mark") == 2
    vista = _strip_js_line_comments(_read(ATTIVITA_JS))
    assert "deleteTask" not in vista and "deleteActivity" not in vista and "canDeleteHistory" not in vista
    for js in (ATTIVITA_JS, DIALOGS_JS, OGGI_JS):
        assert subprocess.run(["node", "--check", str(js)], capture_output=True).returncode == 0, js


def test_b04_le_delete_http_non_raggiungono_il_database():
    """REVIEW 2: DELETE /api/core/tasks|activities/{id} restano solo per
    compatibilita' e rispondono 405 senza chiamare service o repository."""
    from core import router
    for fn, azione in ((router.delete_task, "/mark-mistake"), (router.delete_activity, "/mark-mistake")):
        corpo = inspect.getsource(fn)
        assert "service." not in corpo and "repository." not in corpo and "_delete_disabled(" in corpo
        assert azione in corpo
    assert "status_code=405" in inspect.getsource(router._delete_disabled)
    assert "HARD_DELETE_DISABLED" in inspect.getsource(router._delete_disabled)
    # nessun altro modulo HTTP chiama le delete fisiche del CORE
    for py in ROOT.rglob("*.py"):
        rel = py.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", "core/")) or "/site-packages/" in rel:
            continue
        testo = py.read_text(encoding="utf-8", errors="ignore")
        assert "delete_task(" not in testo and "delete_activity(" not in testo, rel
