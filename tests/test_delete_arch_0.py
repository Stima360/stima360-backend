"""DELETE-ARCH Fase 0 - contratti statici e unitari senza database.

Compagno di `test_delete_arch_0_postgres.py` (che prova le regole sul DB
vero): qui si fissano le superfici che non hanno bisogno di PostgreSQL:

  * la matrice di accesso di `property/lifecycle.py` (D10) e il modulo
    `stime_purge` (ruolo, eleggibilita' dal catalogo, tutto-o-niente) con
    doppi di cursore;
  * le rotte: `/archive` e `/unarchive` esistono, la DELETE legacy e'
    deprecata, le rotte stime passano da `stime_purge`;
  * la UI: il selettore di stato non offre piu' «archived», «Archivia» chiama
    POST .../archive, «Riattiva» compare solo sugli archiviati e solo a chi
    puo', l'agente non vede «Elimina» su attivita'/task, l'edificio archivia
    con POST, l'api-client espone `code` e `blockers`.

Prima della Fase 0 ogni test di questo modulo fallisce (fail-before).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from tests.test_os_shell_p25_3_property_lifecycle import (  # noqa: F401
    IMMOBILE_JS, VIEWS, _function_block, _read, _strip_js_line_comments,
)

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
ATTIVITA_JS = VIEWS / "attivita.js"
EDIFICIO_JS = VIEWS / "edificio-dettaglio.js"
CONTATTO_JS = VIEWS / "contatto-dettaglio.js"
API_CLIENT_JS = ASSETS / "core" / "api-client.js"


def _node(script: str) -> dict:
    esito = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


class _Ctx:
    def __init__(self, role, user_id=7, agency_id=1, platform=False):
        self.role, self.user_id, self.agency_id, self.is_platform_admin = role, user_id, agency_id, platform

    def require_agency(self):
        return self.agency_id


# ---------------------------------------------------------------------------
# lifecycle: accesso (D10)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ruolo,assegnato,atteso", [
    ("agent", 7, True), ("agent", 8, False), ("agent", None, False),
    ("agency_admin", None, True), ("agency_owner", 8, True),
])
def test_l01_may_manage_segue_l_assegnazione_per_l_agente(ruolo, assegnato, atteso):
    from property import lifecycle
    assert lifecycle.may_manage(_Ctx(ruolo), {"assigned_agent_id": assegnato}) is atteso


def test_l02_platform_admin_in_acting_e_ruolo_ignoto_falliscono_chiusi():
    from property import lifecycle
    assert lifecycle.may_manage(_Ctx("platform_admin", platform=True), {"assigned_agent_id": None}) is True
    assert lifecycle.may_manage(_Ctx("qualcosa"), {"assigned_agent_id": 7}) is False
    with pytest.raises(lifecycle.LifecycleForbidden) as exc:
        lifecycle.require_manage(_Ctx("agent"), {"assigned_agent_id": 9})
    assert exc.value.code == "NOT_ASSIGNED"


def test_l03_codici_e_messaggi_guidano_la_ui():
    from property import lifecycle
    assert "Smetti" in lifecycle.SELLER_OPPORTUNITY_OPEN_MESSAGE and "Smetti" in lifecycle.SELLER_LINK_ACTIVE_MESSAGE
    for codice in ("ARCHIVE_BLOCKED", "NOT_ASSIGNED", "OWNER_OF_OPEN_ACQUISITION", "SELLER_OPPORTUNITY_OPEN",
                   "LAST_OWNER_WITH_MANDATE", "SELLER_LINK_ACTIVE", "DOCUMENT_SHARED", "VISIT_HAS_FEEDBACK"):
        assert getattr(lifecycle, codice) == codice


# ---------------------------------------------------------------------------
# stime_purge: ruolo, catalogo, tutto-o-niente (con doppi)
# ---------------------------------------------------------------------------

class _Cur:
    """Risponde per statement: `SELECT id FROM stime` -> le stime presenti;
    `pg_constraint` -> le FK del catalogo; `FROM <tabella>` -> i conteggi."""

    def __init__(self, presenti=(), catalogo=(), conteggi=None):
        self.presenti, self.catalogo, self.conteggi = list(presenti), list(catalogo), conteggi or {}
        self.statements, self._rows, self.rowcount = [], [], 0

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.statements.append((flat, params))
        if flat.startswith("SELECT id FROM stime "):
            self._rows = [(i,) for i in self.presenti]
        elif "pg_constraint" in flat:
            self._rows = list(self.catalogo)
        elif flat.startswith("SELECT ") and " COUNT(*) FROM " in flat:
            tabella = flat.split(" FROM ")[1].split(" ")[0]
            self._rows = list(self.conteggi.get(tabella, []))
        elif flat.startswith("DELETE "):
            self._rows = []
            self.rowcount = len(self.presenti)
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class _Conn:
    def __init__(self, cur):
        self.cur, self.commits, self.rollbacks = cur, 0, 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_s01_solo_agency_owner_mai_il_platform_admin_in_acting():
    """Review Fase 0: hard-delete stime SOLO per `agency_owner`. Il platform
    admin in acting (contesto `role=None`, `is_platform_admin=True`) e'
    rifiutato, e nessuna statement raggiunge il database."""
    import stime_purge
    for ctx in (_Ctx("agent"), _Ctx("agency_admin"), _Ctx(None), _Ctx(None, platform=True),
                _Ctx("agency_owner", platform=True)):
        for funzione in (stime_purge.purge_stime, stime_purge.purge_stime_dettagliate):
            cur = _Cur(presenti=[1])
            conn = _Conn(cur)
            with pytest.raises(stime_purge.StimePurgeForbidden):
                funzione(conn, ctx, 1, [1])
            assert cur.statements == [] and conn.commits == 0, (ctx.role, ctx.is_platform_admin)
        assert stime_purge.may_purge(ctx) is False
    assert stime_purge.may_purge(_Ctx("agency_owner")) is True


def test_s02_la_lista_statica_copre_il_contratto_e_il_catalogo_aggiunge():
    import stime_purge
    tabelle = {t for t, _, _ in stime_purge.STIME_REFERENCES}
    contratto = {"activities", "tasks", "lead_stime", "owner_stima_access", "owner_home_overrides",
                 "owner_home_notifications", "communication_messages", "stima_acquisitions", "stima_inspections",
                 "seller_timeline_events", "followup_actions", "property_watches", "next_best_actions",
                 "communication_enrollments"}
    assert contratto <= tabelle and "appointments" in tabelle, tabelle ^ contratto
    assert "stime_dettagliate" in stime_purge.OWN_CONTENT and "stime_dettagliate" not in tabelle
    cur = _Cur(catalogo=[("tabella_nuova", "stima_id"), ("stime_dettagliate", "stima_id"), ("activities", "stima_id"), (111, 1)])
    elenco = stime_purge.references(cur)
    assert ("tabella_nuova", "stima_id", "tabella_nuova") in elenco
    assert [e for e in elenco if e[0] == "activities"] == [("activities", "stima_id", "attività")]
    assert not [e for e in elenco if e[0] in ("stime_dettagliate", "111")]


def test_s03_tutto_o_niente_con_rollback_e_blocchi_per_stima():
    import stime_purge
    cur = _Cur(presenti=[1, 2], conteggi={"tasks": [(2, 3)]})
    conn = _Conn(cur)
    with pytest.raises(stime_purge.StimeNotPurgeable) as exc:
        stime_purge.purge_stime(conn, _Ctx("agency_owner"), 1, [1, 2, 3])
    blocchi = {b["stima_id"]: b["blockers"] for b in exc.value.blockers}
    assert set(blocchi) == {2, 3}
    assert blocchi[3][0]["code"] == "not_found" and blocchi[2][0] == {"table": "tasks", "code": "referenced", "count": 3, "label": "task"}
    assert not [s for s, _ in cur.statements if s.startswith("DELETE")], "nessuna cancellazione"
    assert conn.commits == 0 and conn.rollbacks >= 1
    # pulite: una transazione, dettagli prima della stima
    cur = _Cur(presenti=[1, 2])
    conn = _Conn(cur)
    assert stime_purge.purge_stime(conn, _Ctx("agency_owner"), 1, [2, 1]) == 2
    cancellazioni = [s for s, _ in cur.statements if s.startswith("DELETE")]
    assert cancellazioni[0].startswith("DELETE FROM stime_dettagliate") and cancellazioni[1].startswith("DELETE FROM stime ")
    assert all("agency_id = %s" in s for s in cancellazioni) and conn.commits == 1


# ---------------------------------------------------------------------------
# rotte
# ---------------------------------------------------------------------------

def test_r01_archivia_e_riattiva_esplicite_delete_deprecata():
    from property.router import router
    rotte = {(tuple(sorted(r.methods))[0], r.path): r for r in router.routes if hasattr(r, "methods")}
    assert ("POST", "/api/property/properties/{property_id}/archive") in rotte
    assert ("POST", "/api/property/properties/{property_id}/unarchive") in rotte
    legacy = rotte[("DELETE", "/api/property/properties/{property_id}")]
    assert legacy.deprecated is True
    assert ("GET", "/api/property/properties") in rotte
    assert "include_archived" in {p.name for p in rotte[("GET", "/api/property/properties")].dependant.query_params}


def test_r02_le_rotte_stime_passano_da_stime_purge_e_la_patch_buy_non_archivia():
    import ast
    sorgente = (ROOT / "main.py").read_text(encoding="utf-8")
    for nome in ("admin_delete_stime", "admin_delete_stime_dettagliate"):
        nodo = next(n for n in ast.walk(ast.parse(sorgente)) if isinstance(n, ast.FunctionDef) and n.name == nome)
        corpo = ast.get_source_segment(sorgente, nodo)
        assert "stime_purge." in corpo and "DELETE FROM" not in corpo, nome
        assert "_risposta_non_eliminabile" in corpo
    buy = (ROOT / "buy" / "service.py").read_text(encoding="utf-8")
    assert "_require_archive_role" in buy and 'data["status"] == "archived" and corrente.get("status") != "archived"' in buy
    core = (ROOT / "core" / "service.py").read_text(encoding="utf-8")
    assert "_require_destructive_role(ctx, AGENT_CANNOT_DELETE_ACTIVITY)" in core
    assert "_require_destructive_role(ctx, AGENT_CANNOT_DELETE_TASK)" in core


def test_r03_le_superfici_operative_escludono_gli_archiviati():
    repo = (ROOT / "property" / "repository.py").read_text(encoding="utf-8")
    assert "p.archived_at IS NULL AND p.commercial_status <> 'archived'" in repo
    assert repo.count("COUNT(*) FILTER (WHERE archived_at IS NULL AND commercial_status IN ('mandate','active','reserved','under_offer')) AS active") == 2
    sellers = (ROOT / "crm" / "sellers.py").read_text(encoding="utf-8")
    assert '"p.archived_at IS NULL"' in sellers
    core_repo = (ROOT / "core" / "repository.py").read_text(encoding="utf-8")
    assert "where.append(\"status <> 'archived'\")" in core_repo


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def test_u01_il_selettore_non_offre_archived_e_archivia_chiama_post_archive():
    testo = _read(IMMOBILE_JS)
    match = re.search(r"MANUAL_COMMERCIAL_STATUSES = \[(.*?)\];", testo, re.DOTALL)
    assert "archived" not in set(re.findall(r"'([a-z_]+)'", match.group(1)))
    funzione = _function_block(testo, "applyCommercialStatusSave")
    assert "archiveRequest(`/api/property/properties/${property.id}/archive`)" in funzione
    assert "deleteRequest" not in funzione
    esito = _node(f"""
{funzione}
{_function_block(testo, "applyUnarchive")}
const calls = [];
const property = {{id: 12, commercial_status: 'draft', archived_at: null}};
const apiPost = async (path) => {{ calls.push(['POST', path]); return {{id: 12, commercial_status: 'archived', archived_at: '2026-10-04T10:00:00Z'}}; }};
const apiPatch = async (path, body) => {{ calls.push(['PATCH', path, body]); return {{id: 12, commercial_status: body.commercial_status, archived_at: null}}; }};
await applyCommercialStatusSave(property, 'archived', apiPost, apiPatch);
const dopoArchivio = {{...property}};
await applyUnarchive(property, async (path) => {{ calls.push(['POST', path]); return {{id: 12, commercial_status: 'active', archived_at: null, warnings: ['mandate_not_restored']}}; }});
await applyCommercialStatusSave(property, 'withdrawn', apiPost, apiPatch);
process.stdout.write(JSON.stringify({{calls, dopoArchivio, property}}));
""")
    assert esito["calls"] == [["POST", "/api/property/properties/12/archive"], ["POST", "/api/property/properties/12/unarchive"],
                              ["PATCH", "/api/property/properties/12", {"commercial_status": "withdrawn"}]]
    assert esito["dopoArchivio"]["commercial_status"] == "archived" and esito["dopoArchivio"]["archived_at"]
    assert esito["property"]["commercial_status"] == "withdrawn"
    assert "DELETE /api/property/properties/{id}" not in _strip_js_line_comments(testo)


def test_u02_archivia_e_riattiva_secondo_ruolo_e_stato():
    testo = _read(IMMOBILE_JS)
    blocchi = "\n".join(_function_block(testo, n) for n in (
        "canManagePropertyLifecycle", "isArchivedProperty", "renderArchiveButtons", "renderCommercialStatusSection",
        "selectableCommercialStatuses"))
    esito = _node(f"""
const escapeHtml = (v) => String(v ?? '');
const formatDate = (v) => v ? 'data' : '—';
const STATUS_LABELS = {{ draft: 'Bozza', active: 'Attivo', archived: 'Archiviato', sold: 'Venduto' }};
const MANUAL_COMMERCIAL_STATUSES = ['draft', 'active'];
{blocchi}
const owner = {{ role: 'agency_owner', user_id: 1 }};
const agente = {{ role: 'agent', user_id: 7 }};
const attivo = {{ id: 1, commercial_status: 'active', archived_at: null, assigned_agent_id: 7 }};
const archiviato = {{ id: 2, commercial_status: 'archived', archived_at: '2026-10-01T00:00:00Z', assigned_agent_id: 9 }};
const html = (p, s, conferma = false) => renderCommercialStatusSection(p, false, false, null, {{ session: s, archivePendingConfirm: conferma, lifecycleFeedback: '' }});
process.stdout.write(JSON.stringify({{
  puo: [canManagePropertyLifecycle(attivo, agente), canManagePropertyLifecycle(archiviato, agente), canManagePropertyLifecycle(archiviato, owner), canManagePropertyLifecycle(attivo, null)],
  attivoAgente: html(attivo, agente), attivoAltro: html(archiviato, agente), archiviatoOwner: html(archiviato, owner),
  conferma: html(attivo, owner, true),
  feedback: renderCommercialStatusSection(attivo, false, false, null, {{ session: owner, lifecycleFeedback: 'Bloccato: Acquisizione aperta (1)' }}),
}}));
""")
    assert esito["puo"] == [True, False, True, False]
    assert 'id="property-archive-btn"' in esito["attivoAgente"] and 'id="commercial-status-edit-btn"' in esito["attivoAgente"]
    assert 'id="property-unarchive-btn"' not in esito["attivoAgente"]
    # archiviato, agente non assegnato: niente bottoni, niente «Cambia stato»
    assert 'property-unarchive-btn' not in esito["attivoAltro"] and 'commercial-status-edit-btn' not in esito["attivoAltro"]
    assert "chiedi a un amministratore" in esito["attivoAltro"]
    assert 'id="property-unarchive-btn"' in esito["archiviatoOwner"] and 'commercial-status-edit-btn' not in esito["archiviatoOwner"]
    assert "Conferma archiviazione" in esito["conferma"] and 'id="property-archive-cancel-btn"' in esito["conferma"]
    assert "Acquisizione aperta (1)" in esito["feedback"]
    assert "window.confirm(" not in _strip_js_line_comments(testo)


def test_u03_blocchi_leggibili_e_api_client_con_codice():
    testo = _read(IMMOBILE_JS)
    esito = _node(f"""
{_function_block(testo, "archiveBlockersText")}
const e = new Error("L'immobile ha processi aperti"); e.code = 'ARCHIVE_BLOCKED';
e.data = {{ blockers: [{{ code: 'open_acquisition', label: 'Acquisizione aperta', items: [{{id: 1}}] }}, {{ code: 'sold', label: 'Venduto', items: [] }}] }};
process.stdout.write(JSON.stringify({{ testo: archiveBlockersText(e), vuoto: archiveBlockersText(new Error('x')) }}));
""")
    assert esito["testo"] == "L'immobile ha processi aperti: Acquisizione aperta (1) · Venduto" and esito["vuoto"] == "x"
    client = _strip_js_line_comments(_read(API_CLIENT_JS))
    assert "error.code = data.code" in client and "error.data = data" in client


def test_u04_l_agente_non_vede_elimina_su_attivita_e_task():
    testo = _read(ATTIVITA_JS)
    assert "import { getSession } from '../core/auth.js';" in testo
    blocchi = "\n".join(_function_block(testo, n) for n in ("canDeleteHistory", "renderTaskActions", "renderCronologiaActions"))
    esito = _node(f"""
const escapeHtml = (v) => String(v ?? '');
const OPEN_TASK_STATUSES = ['open', 'in_progress'];
let sessione = {{ role: 'agent', user_id: 3 }};
const getSession = () => sessione;
{blocchi}
const task = {{ id: 5, status: 'open' }};
const attivita = {{ kind: 'attivita', data: {{ id: 9, property_id: null }} }};
const agente = {{ task: renderTaskActions(task, new Set(['task:5'])), att: renderCronologiaActions(attivita, new Set()) }};
sessione = {{ role: 'agency_admin', user_id: 3 }};
const admin = {{ task: renderTaskActions(task, new Set()), att: renderCronologiaActions(attivita, new Set()) }};
process.stdout.write(JSON.stringify({{ agente, admin }}));
""")
    assert "data-delete-task" not in esito["agente"]["task"] and "data-complete-task" in esito["agente"]["task"]
    assert "data-edit-task" in esito["agente"]["task"]
    assert esito["agente"]["att"] == ""
    assert 'data-delete-task="5"' in esito["admin"]["task"] and 'data-delete-activity="9"' in esito["admin"]["att"]


def test_u05_edificio_archivia_con_post_e_contact_360_marca_gli_archiviati():
    edificio = _strip_js_line_comments(_read(EDIFICIO_JS))
    assert "apiPost(`/api/property/properties/${id}/archive`)" in edificio
    assert "apiDelete" not in edificio
    contatto = _read(CONTATTO_JS)
    assert "renderBadge('Archiviato', 'gray')" in contatto
    for percorso in (IMMOBILE_JS, EDIFICIO_JS, ATTIVITA_JS, CONTATTO_JS, API_CLIENT_JS):
        esito = subprocess.run(["node", "--check", str(percorso)], capture_output=True, text=True)
        assert esito.returncode == 0, (percorso.name, esito.stderr)
