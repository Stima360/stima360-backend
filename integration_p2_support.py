from __future__ import annotations

import copy
import contextlib
import importlib
import sys
import traceback
import datetime as dt
import hashlib
import json
import os
import socket
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import psycopg2
import requests
from psycopg2.extras import RealDictCursor, Json
from psycopg2 import sql

EXPECTED_DB = "stima360_db_test"
EXPECTED_BACKEND_HOST = "stima360-backend-test.onrender.com"
EXPECTED_BRANCH = "core-0.1-test"
DEFAULT_BASE_URL = "https://stima360-backend-test.onrender.com"
MANIFEST_VERSION = 1

class IntegrationStop(RuntimeError):
    pass

@dataclass(frozen=True)
class EnvironmentContext:
    database: str
    backend: str
    branch: str
    commit: str
    hostname: str


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def require_test_environment(*, require_http: bool = True, require_branch: bool = True) -> EnvironmentContext:
    database = (os.getenv("DB_NAME") or os.getenv("PGDATABASE") or "").strip()
    backend = (os.getenv("INTEGRATION_BASE_URL") or os.getenv("PUBLIC_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    branch = (os.getenv("RENDER_GIT_BRANCH") or os.getenv("GIT_BRANCH") or _git("rev-parse", "--abbrev-ref", "HEAD")).strip()
    commit = (os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or _git("rev-parse", "HEAD")).strip()
    host = (urlparse(backend).hostname or "").lower()

    if database != EXPECTED_DB:
        raise IntegrationStop(f"BLOCCATO: database non TEST ({database!r}; atteso {EXPECTED_DB!r})")
    if require_http:
        if urlparse(backend).scheme != "https":
            raise IntegrationStop(f"BLOCCATO: backend non HTTPS ({backend!r})")
        if host != EXPECTED_BACKEND_HOST:
            raise IntegrationStop(f"BLOCCATO: backend non TEST ({backend!r}; atteso host {EXPECTED_BACKEND_HOST!r})")
    if require_branch and branch != EXPECTED_BRANCH:
        raise IntegrationStop(f"BLOCCATO: branch non TEST ({branch!r}; atteso {EXPECTED_BRANCH!r})")
    return EnvironmentContext(database, backend, branch, commit, socket.gethostname())


def db_connect(*, readonly: bool = False):
    require_test_environment(require_http=False)
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


REPOSITORY_ROOT = Path(__file__).resolve().parent

def _is_within(path: str | Path | None, root: Path = REPOSITORY_ROOT) -> bool:
    if not path:
        return False
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False

def import_diagnostics(exc: BaseException | None = None) -> str:
    lines = [
        "=== INTEGRATION IMPORT DIAGNOSTICS ===",
        f"cwd={Path.cwd()}",
        f"repository_root={REPOSITORY_ROOT}",
        "sys.path:",
        *[f"  [{i}] {value}" for i, value in enumerate(sys.path)],
        "core* modules in sys.modules:",
    ]
    names = sorted(name for name in sys.modules if name == "core" or name.startswith("core."))
    if not names:
        lines.append("  <none>")
    for name in names:
        module = sys.modules.get(name)
        lines.append(f"  {name}: file={getattr(module, '__file__', None)!r} path={getattr(module, '__path__', None)!r}")
    if exc is not None:
        lines.extend([f"exception={exc!r}", traceback.format_exc()])
    return "\n".join(lines)

@contextlib.contextmanager
def deterministic_project_imports():
    """Importa i package del progetto senza contaminazioni pytest e ripristina lo stato globale.

    Il conftest storico di OWNER inserisce stub ``core*`` in ``sys.modules``.
    Questo contesto li rimuove temporaneamente, mette la root del repository in
    testa a ``sys.path``, verifica la provenienza dei moduli e ripristina tutto
    all'uscita. La diagnostica viene emessa soltanto in caso di errore.
    """
    old_path = list(sys.path)
    saved_modules = {
        name: module for name, module in list(sys.modules.items())
        if name == "core" or name.startswith("core.") or name == "main"
    }
    for name in list(saved_modules):
        sys.modules.pop(name, None)
    root = str(REPOSITORY_ROOT)
    sys.path[:] = [root] + [item for item in old_path if Path(item or '.').resolve() != REPOSITORY_ROOT.resolve()]
    importlib.invalidate_caches()
    try:
        yield
    except Exception as exc:
        print(import_diagnostics(exc), file=sys.stderr)
        raise
    finally:
        for name in list(sys.modules):
            if name == "core" or name.startswith("core.") or name == "main":
                sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
        sys.path[:] = old_path
        importlib.invalidate_caches()

def import_project_module(name: str):
    with deterministic_project_imports():
        module = importlib.import_module(name)
        module_file = getattr(module, "__file__", None)
        if not _is_within(module_file):
            exc = ImportError(f"Modulo {name!r} caricato fuori dal progetto: {module_file!r}")
            print(import_diagnostics(exc), file=sys.stderr)
            raise exc
        # Verifica esplicita dei moduli CORE critici quando presenti.
        for critical in ("core", "core.router", "core.exceptions"):
            loaded = sys.modules.get(critical)
            if loaded is not None and not _is_within(getattr(loaded, "__file__", None)):
                exc = ImportError(
                    f"Modulo {critical!r} non proviene dal progetto: "
                    f"{getattr(loaded, '__file__', None)!r}"
                )
                print(import_diagnostics(exc), file=sys.stderr)
                raise exc
        return module

def import_main_app():
    return import_project_module("main").app


@dataclass
class PKRecord:
    table: str
    pk: int
    scenario: str
    removed: bool = False

@dataclass
class OriginalRecord:
    table: str
    pk: int
    values: dict[str, Any]
    restored: bool = False

@dataclass
class TestResult:
    name: str
    status: str
    details: str = ""

@dataclass
class RunManifest:
    run_id: str
    branch: str
    commit: str
    backend: str
    database: str
    started_at: str
    finished_at: str | None = None
    status: str = "running"
    tests: list[TestResult] = field(default_factory=list)
    created_pks: list[PKRecord] = field(default_factory=list)
    original_records: list[OriginalRecord] = field(default_factory=list)
    teardown_errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def start(cls, run_id: str, env: EnvironmentContext) -> "RunManifest":
        return cls(
            run_id=run_id,
            branch=env.branch,
            commit=env.commit,
            backend=env.backend,
            database=env.database,
            started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        )

    def register_pk(self, table: str, pk: int | None, scenario: str) -> None:
        if pk is None:
            return
        value = int(pk)
        if not any(x.table == table and x.pk == value for x in self.created_pks):
            self.created_pks.append(PKRecord(table, value, scenario))
            self.write()

    def register_many(self, table: str, pks: Iterable[int], scenario: str) -> None:
        for pk in pks:
            self.register_pk(table, pk, scenario)

    def snapshot(self, table: str, pk: int, values: dict[str, Any]) -> None:
        if not any(x.table == table and x.pk == int(pk) for x in self.original_records):
            self.original_records.append(OriginalRecord(table, int(pk), _json_safe(values)))
            self.write()

    def result(self, name: str, status: str, details: str = "") -> None:
        self.tests.append(TestResult(name, status, details))
        self.write()

    def path(self) -> Path:
        return Path(os.getenv("INTEGRATION_MANIFEST_PATH", f"INTEGRATION_P2_RUN_MANIFEST_{self.run_id}.json"))

    def write(self) -> None:
        payload = asdict(self)
        self.path().write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _json_safe(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def api_request(base: str, method: str, path: str, *, expected: tuple[int, ...] = (200, 201, 204), session: requests.Session | None = None, **kwargs):
    client = session or requests
    response = client.request(method, base + path, timeout=45, **kwargs)
    if response.status_code in (500, 502):
        raise IntegrationStop(f"ARRESTO: {method} {path} ha restituito {response.status_code}: {response.text[:500]}")
    if response.status_code not in expected:
        raise AssertionError(f"{method} {path}: HTTP {response.status_code}, atteso {expected}; {response.text[:500]}")
    if response.status_code == 204 or not response.content:
        return None, response
    return response.json(), response


def normalize_openapi_path(path: str) -> str:
    """Normalizza esclusivamente i nomi dei parametri path, preservando la struttura."""
    return re.sub(r"\{[^{}]+\}", "{}", path)


class OpenAPIContract:
    def __init__(self, base: str):
        response = requests.get(base + "/openapi.json", timeout=45)
        if response.status_code != 200:
            raise IntegrationStop(f"OpenAPI non disponibile: HTTP {response.status_code}")
        self.document = response.json()
        self.paths = self.document.get("paths", {})
        self._normalized: dict[str, list[str]] = {}
        for runtime_path in self.paths:
            self._normalized.setdefault(normalize_openapi_path(runtime_path), []).append(runtime_path)

    def resolve(self, method: str, path_template: str) -> str:
        method_l = method.lower()
        exact = self.paths.get(path_template, {})
        if method_l in exact:
            return path_template
        normalized = normalize_openapi_path(path_template)
        matches = [p for p in self._normalized.get(normalized, []) if method_l in self.paths.get(p, {})]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise IntegrationStop(
                f"Route OpenAPI ambigua dopo normalizzazione parametri: {method.upper()} {path_template}; match={matches}"
            )
        raise IntegrationStop(f"Route hardcoded non presente in OpenAPI: {method.upper()} {path_template}")

    def require(self, method: str, path_template: str) -> None:
        self.resolve(method, path_template)

    def require_all(self, routes: Iterable[tuple[str, str]]) -> None:
        for method, path in routes:
            self.require(method, path)


HARDCODED_ROUTES = {
    ("POST", "/api/core/contacts"),
    ("POST", "/api/core/contacts/{contact_id}/roles"),
    ("POST", "/api/core/leads"),
    ("POST", "/api/core/activities"),
    ("POST", "/api/core/tasks"),
    ("POST", "/api/property/properties"),
    ("POST", "/api/property/properties/{property_id}/contacts"),
    ("POST", "/api/property/properties/{property_id}/leads"),
    ("POST", "/api/buy/requests"),
    ("POST", "/api/buy/requests/{request_id}/locations"),
    ("POST", "/api/buy/requests/{request_id}/typologies"),
    ("POST", "/api/match/calculate"),
    ("POST", "/api/match/matches/{match_id}/refresh"),
    ("GET", "/api/flow/rules/{code}"),
    ("POST", "/api/flow/rules/{code}/deactivate"),
    ("PATCH", "/api/flow/rules/{code}/parameters"),
    ("POST", "/api/flow/rules/{code}/simulate"),
    ("POST", "/api/flow/rules/{code}/activate"),
    ("POST", "/api/flow/events"),
    ("POST", "/api/flow/executions/{execution_id}/retry"),
    ("POST", "/api/owner/admin/accounts"),
    ("POST", "/api/owner/admin/access"),
    ("POST", "/api/owner/admin/accounts/{account_id}/tokens"),
    ("POST", "/api/owner/admin/publications"),
    ("PATCH", "/api/owner/admin/publications/{publication_id}"),
    ("POST", "/api/owner/admin/publications/{publication_id}/publish"),
    ("POST", "/api/owner/admin/publications/{publication_id}/supersede"),
    ("POST", "/api/owner/admin/access/{access_id}/revoke"),
    ("POST", "/api/owner/portal/auth/token"),
    ("GET", "/api/owner/portal/dashboard"),
    ("GET", "/api/owner/portal/publications/{publication_id}"),
    ("POST", "/api/owner/portal/publications/{publication_id}/acknowledge"),
    ("POST", "/api/owner/portal/properties/{property_id}/feedback"),
}


def validate_openapi_routes(base: str) -> OpenAPIContract:
    contract = OpenAPIContract(base)
    contract.require_all(sorted(HARDCODED_ROUTES))
    return contract


def fetch_row(table: str, pk: int) -> dict[str, Any] | None:
    with db_connect(readonly=True) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f'SELECT * FROM "{table}" WHERE id=%s', (pk,))
            row = cur.fetchone()
        conn.rollback()
    return dict(row) if row else None


def rows_for_fk(table: str, column: str, value: int) -> list[int]:
    with db_connect(readonly=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SELECT id FROM "{table}" WHERE "{column}"=%s ORDER BY id', (value,))
            out = [int(r[0]) for r in cur.fetchall()]
        conn.rollback()
    return out


def _mark_removed_after_commit(manifest: RunManifest, removed: list[tuple[str, int]]) -> None:
    changed = False
    removed_set = {(t, int(pk)) for t, pk in removed}
    for item in manifest.created_pks:
        if (item.table, item.pk) in removed_set and not item.removed:
            item.removed = True
            changed = True
    if changed:
        manifest.write()


def delete_registered_in_transaction(conn, manifest: RunManifest, table: str, removed: list[tuple[str, int]]) -> None:
    items = [x for x in manifest.created_pks if x.table == table and not x.removed]
    if not items:
        return
    with conn.cursor() as cur:
        for item in sorted(items, key=lambda x: x.pk, reverse=True):
            cur.execute(sql.SQL('DELETE FROM {} WHERE id=%s').format(sql.Identifier(table)), (item.pk,))
            if cur.rowcount != 1:
                raise IntegrationStop(f"Teardown parziale: {table} id={item.pk}, righe eliminate={cur.rowcount}")
            cur.execute(sql.SQL('SELECT 1 FROM {} WHERE id=%s').format(sql.Identifier(table)), (item.pk,))
            if cur.fetchone() is not None:
                raise IntegrationStop(f"Teardown non verificato nella transazione: {table} id={item.pk}")
            removed.append((table, item.pk))


def restore_flow_rule_in_transaction(conn, original: OriginalRecord) -> None:
    values = original.values
    cols = [k for k in values if k != "id"]
    assignments = sql.SQL(",").join(
        sql.SQL("{}=%s").format(sql.Identifier(c)) for c in cols
    )
    params = []
    json_cols = {"parameters", "default_parameters", "allowed_parameters"}
    for c in cols:
        v = values[c]
        params.append(Json(v) if c in json_cols else v)
    params.append(original.pk)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("UPDATE flow_rules SET {} WHERE id=%s").format(assignments),
            params,
        )
        if cur.rowcount != 1:
            raise IntegrationStop(f"Ripristino flow_rules fallito per id={original.pk}")
        cur.execute("SELECT * FROM flow_rules WHERE id=%s", (original.pk,))
        if cur.fetchone() is None:
            raise IntegrationStop(f"Verifica ripristino flow_rules fallita per id={original.pk}")


def teardown(manifest: RunManifest) -> None:
    require_test_environment(require_http=True)
    conn = db_connect(readonly=False)
    removed: list[tuple[str, int]] = []
    restored: list[OriginalRecord] = []
    try:
        with conn.cursor() as cur:
            owner_pub_ids = [x.pk for x in manifest.created_pks if x.table == "owner_publications" and not x.removed]
            if owner_pub_ids:
                cur.execute(
                    "UPDATE owner_publications SET supersedes_publication_id=NULL, "
                    "superseded_by_publication_id=NULL WHERE id=ANY(%s)",
                    (owner_pub_ids,),
                )
        for table in (
            "owner_publication_reads", "owner_feedback", "owner_sessions", "owner_access_tokens", "owner_audit_log",
            "owner_publications", "owner_property_access", "owner_accounts",
            "flow_action_records", "flow_executions", "flow_events", "flow_suppressions",
            "match_feedback", "match_refresh_history", "match_requirement_results", "match_runs", "matches", "match_exclusions",
            "buy_request_history", "buy_request_interactions", "buy_request_task_links", "buy_request_features",
            "buy_request_typologies", "buy_request_locations", "buy_requests",
            "property_visits", "property_photos", "property_documents", "property_price_history",
            "property_status_history", "property_leads", "property_contacts", "properties",
            "lead_stime", "tasks", "activities", "leads", "contact_roles", "contacts",
        ):
            delete_registered_in_transaction(conn, manifest, table, removed)
        for original in manifest.original_records:
            if original.table == "flow_rules" and not original.restored:
                restore_flow_rule_in_transaction(conn, original)
                restored.append(original)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        manifest.teardown_errors.append(str(exc))
        manifest.write()
        raise
    finally:
        conn.close()

    # Il manifest viene aggiornato solo dopo commit riuscito. In caso di rollback
    # tutte le PK restano removed=false e il teardown può essere ritentato.
    _mark_removed_after_commit(manifest, removed)
    for original in restored:
        original.restored = True
    if restored:
        manifest.write()

def _compare_value(current: Any, expected: Any) -> bool:
    return _json_safe(current) == _json_safe(expected)



def escape_like_literal(value: str, escape: str = "\\") -> str:
    return value.replace(escape, escape + escape).replace("%", escape + "%").replace("_", escape + "_")


def find_foreign_key_orphans() -> list[dict[str, Any]]:
    """Verifica realmente ogni FK con LEFT JOIN fra colonne figlie e parent."""
    findings: list[dict[str, Any]] = []
    with db_connect(readonly=True) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT con.oid, con.conname,
                       child_ns.nspname AS child_schema, child.relname AS child_table,
                       parent_ns.nspname AS parent_schema, parent.relname AS parent_table,
                       ARRAY(
                         SELECT att.attname FROM unnest(con.conkey) WITH ORDINALITY k(attnum, ord)
                         JOIN pg_attribute att ON att.attrelid=con.conrelid AND att.attnum=k.attnum
                         ORDER BY k.ord
                       ) AS child_columns,
                       ARRAY(
                         SELECT att.attname FROM unnest(con.confkey) WITH ORDINALITY k(attnum, ord)
                         JOIN pg_attribute att ON att.attrelid=con.confrelid AND att.attnum=k.attnum
                         ORDER BY k.ord
                       ) AS parent_columns
                FROM pg_constraint con
                JOIN pg_class child ON child.oid=con.conrelid
                JOIN pg_namespace child_ns ON child_ns.oid=child.relnamespace
                JOIN pg_class parent ON parent.oid=con.confrelid
                JOIN pg_namespace parent_ns ON parent_ns.oid=parent.relnamespace
                WHERE con.contype='f' AND child_ns.nspname='public'
                ORDER BY child.relname, con.conname
            """)
            constraints = cur.fetchall()
            for fk in constraints:
                child_cols = list(fk["child_columns"])
                parent_cols = list(fk["parent_columns"])
                join_parts = [
                    sql.SQL("c.{} = p.{}").format(sql.Identifier(cc), sql.Identifier(pc))
                    for cc, pc in zip(child_cols, parent_cols)
                ]
                not_null = [sql.SQL("c.{} IS NOT NULL").format(sql.Identifier(cc)) for cc in child_cols]
                parent_missing = sql.SQL("p.{} IS NULL").format(sql.Identifier(parent_cols[0]))
                query = sql.SQL("SELECT count(*) AS orphan_count FROM {}.{} c LEFT JOIN {}.{} p ON {} WHERE {} AND {}")
                query = query.format(
                    sql.Identifier(fk["child_schema"]), sql.Identifier(fk["child_table"]),
                    sql.Identifier(fk["parent_schema"]), sql.Identifier(fk["parent_table"]),
                    sql.SQL(" AND ").join(join_parts),
                    sql.SQL(" AND ").join(not_null), parent_missing,
                )
                cur.execute(query)
                count = int(cur.fetchone()["orphan_count"])
                findings.append({
                    "constraint": fk["conname"],
                    "child_table": fk["child_table"],
                    "child_columns": child_cols,
                    "parent_table": fk["parent_table"],
                    "parent_columns": parent_cols,
                    "orphan_count": count,
                })
        conn.rollback()
    return findings


def load_manifest(path: Path | str) -> RunManifest:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    raw["tests"] = [TestResult(**x) for x in raw.get("tests", [])]
    raw["created_pks"] = [PKRecord(**x) for x in raw.get("created_pks", [])]
    raw["original_records"] = [OriginalRecord(**x) for x in raw.get("original_records", [])]
    return RunManifest(**raw)


def require_manifest_result(name: str, *, status: str = "passed") -> TestResult:
    path = os.getenv("INTEGRATION_MANIFEST_PATH", "").strip()
    if not path:
        raise IntegrationStop("INTEGRATION_MANIFEST_PATH non impostato")
    manifest = load_manifest(path)
    matches = [x for x in manifest.tests if x.name == name]
    if not matches:
        raise AssertionError(f"Esito {name!r} assente dal manifest {path}")
    result = matches[-1]
    if result.status != status:
        raise AssertionError(f"Esito {name!r}={result.status!r}, atteso {status!r}: {result.details}")
    return result

def postcheck(manifest: RunManifest) -> dict[str, Any]:
    require_test_environment(require_http=True)
    remaining=[]
    modified=[]
    with db_connect(readonly=True) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for item in manifest.created_pks:
                cur.execute(f'SELECT id FROM "{item.table}" WHERE id=%s', (item.pk,))
                if cur.fetchone():
                    remaining.append({"table":item.table,"pk":item.pk})
            for original in manifest.original_records:
                cur.execute(f'SELECT * FROM "{original.table}" WHERE id=%s', (original.pk,))
                row=cur.fetchone()
                if row is None or not all(_compare_value(row.get(k), v) for k,v in original.values.items()):
                    modified.append({"table":original.table,"pk":original.pk})
            run_residues=[]
            cur.execute("""SELECT c.table_name,c.column_name FROM information_schema.columns c
                           WHERE c.table_schema='public' AND c.data_type IN ('character varying','text','json','jsonb')
                             AND EXISTS (SELECT 1 FROM information_schema.columns i WHERE i.table_schema='public' AND i.table_name=c.table_name AND i.column_name='id')""")
            searchable_columns = cur.fetchall()
            escaped_run_id = escape_like_literal(manifest.run_id)
            for column_row in searchable_columns:
                table = column_row["table_name"]
                column = column_row["column_name"]
                cur.execute(
                    sql.SQL("SELECT id FROM {} WHERE CAST({} AS text) LIKE %s ESCAPE E'\\' LIMIT 20").format(
                        sql.Identifier(table), sql.Identifier(column)
                    ),
                    (f"%{escaped_run_id}%",),
                )
                for row in cur.fetchall():
                    run_residues.append({"table":table,"column":column,"pk":int(row["id"])})
        conn.rollback()
    result={
        "run_id":manifest.run_id,
        "remaining_created_pks":remaining,
        "modified_preexisting_records":modified,
        "teardown_errors":manifest.teardown_errors,
        "run_id_residues":run_residues,
    }
    if remaining or modified or manifest.teardown_errors or run_residues:
        raise IntegrationStop("POSTCHECK BLOCCANTE: "+json.dumps(result,ensure_ascii=False,default=str))
    return result
