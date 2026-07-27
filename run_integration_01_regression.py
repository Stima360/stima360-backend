#!/usr/bin/env python3
"""INTEGRATION 0.1 - preliminary regression runner.

Discovers existing test files with Python and always passes explicit paths to
pytest. No wildcard is delegated to pytest. Before collection, a clean Python
subprocess verifies that the real project package ``core`` and its critical
submodules resolve from the repository root. The regression never authorizes
E2E execution.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from integration_p2_support import REPOSITORY_ROOT, require_test_environment

ROOT = Path(__file__).resolve().parent
TESTS_DIR = ROOT / "tests"


@dataclass(frozen=True)
class GroupSpec:
    name: str
    patterns: tuple[str, ...] = ()
    required_files: tuple[str, ...] = ()
    description: str = ""


GROUPS: tuple[GroupSpec, ...] = (
    GroupSpec("CORE", ("test_core*.py",), description="Test applicativi CORE esistenti"),
    GroupSpec("PROPERTY", ("test_property*.py",), description="Test applicativi PROPERTY esistenti"),
    GroupSpec("BUY", ("test_buy*.py",), description="Test applicativi BUY esistenti"),
    GroupSpec("MATCH", ("test_match*.py",), description="Test applicativi MATCH esistenti"),
    GroupSpec("FLOW", ("test_flow*.py",), description="Test applicativi FLOW esistenti"),
    GroupSpec("OWNER", ("test_owner*.py",), description="Test applicativi OWNER esistenti"),
    GroupSpec(
        "LEGACY",
        required_files=("test_integration_regression.py",),
        description="Import moduli congelati e presenza route legacy",
    ),
    GroupSpec(
        "SMOKE_UI",
        required_files=("test_integration_routes.py",),
        description="Route runtime, mount amministrativi e OpenAPI",
    ),
    GroupSpec(
        "PACKAGING_DOCUMENTALI",
        patterns=("test_*packag*.py", "test_*document*.py", "test_*manifest*.py"),
        description="Test packaging/documentali separati quando presenti",
    ),
)

CRITICAL_IMPORTS: tuple[str, ...] = (
    "core",
    "core.normalization",
    "core.service",
    "core.router",
    "core.exceptions",
)


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _deduplicate(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in sorted(paths):
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(path)
    return result


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    root = str(REPOSITORY_ROOT.resolve())
    current = env.get("PYTHONPATH", "")
    existing = [entry for entry in current.split(os.pathsep) if entry]
    env["PYTHONPATH"] = os.pathsep.join([root, *[x for x in existing if Path(x).resolve() != REPOSITORY_ROOT.resolve()]])
    env["RUN_INTEGRATION_P2_E2E"] = "0"
    env["INTEGRATION_P2_E2E_AUTHORIZED"] = ""
    return env


def verify_project_imports() -> None:
    """Verify critical CORE imports in a clean subprocess before pytest.

    The subprocess starts without pytest collection and therefore without
    ``tests/conftest.py``. PYTHONPATH explicitly places the repository root
    first. Every imported module must resolve inside that root.
    """

    root = str(REPOSITORY_ROOT.resolve())
    module_names = repr(CRITICAL_IMPORTS)
    probe = f"""
import importlib
import json
import os
import sys
from pathlib import Path

root = Path({root!r}).resolve()
modules = {module_names}
result = {{
    'cwd': os.getcwd(),
    'repository_root': str(root),
    'sys_path': list(sys.path),
    'modules': {{}},
}}

def within_project(value):
    if not value:
        return False
    try:
        Path(value).resolve().relative_to(root)
        return True
    except Exception:
        return False

for name in modules:
    module = importlib.import_module(name)
    module_file = getattr(module, '__file__', None)
    module_path = [str(x) for x in getattr(module, '__path__', [])]
    result['modules'][name] = {{'file': module_file, 'path': module_path}}
    if not within_project(module_file):
        print(json.dumps(result, indent=2, ensure_ascii=False))
        raise SystemExit(f'IMPORT BLOCCATO: {{name}} proviene da {{module_file!r}}')

print(json.dumps(result, indent=2, ensure_ascii=False))
"""

    command = [sys.executable, "-c", probe]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=_subprocess_env(),
        text=True,
        capture_output=True,
        check=False,
    )

    if completed.returncode != 0:
        print("PRELIGHT IMPORT CORE: FALLITO")
        if completed.stdout:
            print(completed.stdout.rstrip())
        if completed.stderr:
            print(completed.stderr.rstrip(), file=sys.stderr)
        raise SystemExit(completed.returncode)

    print("PRELIGHT IMPORT CORE: OK")
    print(completed.stdout.rstrip())


def discover_group(spec: GroupSpec) -> list[Path]:
    found: list[Path] = []

    for pattern in spec.patterns:
        found.extend(path for path in TESTS_DIR.glob(pattern) if path.is_file())

    missing_required: list[str] = []
    for relative_name in spec.required_files:
        candidate = TESTS_DIR / relative_name
        if candidate.is_file():
            found.append(candidate)
        else:
            missing_required.append(relative_name)

    found = _deduplicate(found)

    if missing_required:
        actual = sorted(path.name for path in TESTS_DIR.glob("test_*.py") if path.is_file())
        raise SystemExit(
            "PREFLIGHT REGRESSIONE FALLITO\n"
            f"Gruppo: {spec.name}\n"
            f"Percorsi attesi mancanti: {', '.join('tests/' + name for name in missing_required)}\n"
            f"File test realmente trovati: {actual or ['NESSUNO']}"
        )

    return found


def print_plan(discovered: dict[str, list[Path]]) -> None:
    print("=" * 78)
    print("INTEGRATION 0.1 - REGRESSIONE PRELIMINARE")
    print("=" * 78)
    print(f"Repository root: {ROOT}")
    print(f"Tests directory: {TESTS_DIR}")

    for spec in GROUPS:
        files = discovered[spec.name]
        print(f"\n[{spec.name}] {spec.description}")
        if not files:
            print("  STATO: gruppo senza test")
            continue
        print(f"  STATO: {len(files)} file trovati")
        for path in files:
            print(f"  - {_relative(path)}")


def run_group(spec: GroupSpec, files: list[Path]) -> int:
    if not files:
        print(f"\nSKIP GRUPPO {spec.name}: gruppo senza test")
        return 0

    command = [
        sys.executable,
        "-m",
        "pytest",
        "--noconftest",
        "-q",
        "--continue-on-collection-errors",
        *[_relative(path) for path in files],
    ]

    print(f"\nESECUZIONE GRUPPO {spec.name}")
    print("Comando pytest:")
    print("  " + " ".join(command))

    completed = subprocess.run(command, cwd=ROOT, env=_subprocess_env(), check=False)
    if completed.returncode != 0:
        print(f"ARRESTO BLOCCANTE: gruppo {spec.name}, exit code {completed.returncode}")
    else:
        print(f"GRUPPO {spec.name}: SUPERATO")
    return completed.returncode


def main() -> int:
    require_test_environment(require_http=True, require_branch=True)

    if ROOT.resolve() != REPOSITORY_ROOT.resolve():
        raise SystemExit(
            "PREFLIGHT REGRESSIONE FALLITO: root runner e repository root divergenti: "
            f"{ROOT.resolve()} != {REPOSITORY_ROOT.resolve()}"
        )

    if not TESTS_DIR.is_dir():
        raise SystemExit(f"PREFLIGHT REGRESSIONE FALLITO: directory assente: {TESTS_DIR}")

    verify_project_imports()

    discovered: dict[str, list[Path]] = {}
    for spec in GROUPS:
        discovered[spec.name] = discover_group(spec)

    print_plan(discovered)
    print("\nPREFLIGHT FILE REGRESSIONE: OK")

    for spec in GROUPS:
        result = run_group(spec, discovered[spec.name])
        if result != 0:
            return result

    print("\nREGRESSIONE PRELIMINARE: TUTTI I GRUPPI DISPONIBILI SUPERATI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
