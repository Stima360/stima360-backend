from integration_p2_support import require_test_environment, deterministic_project_imports
import importlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MODULES=("core.router","property.router","buy.router","match.router","flow.router","owner.router_admin","owner.router_portal")

def _assert_project_module(module, name):
    file=getattr(module,"__file__",None)
    assert file, f"{name} senza __file__"
    Path(file).resolve().relative_to(ROOT.resolve())

def test_all_frozen_modules_importable():
    require_test_environment(require_http=True)
    with deterministic_project_imports():
        for name in MODULES:
            module=importlib.import_module(name)
            _assert_project_module(module,name)
        exceptions=importlib.import_module("core.exceptions")
        _assert_project_module(exceptions,"core.exceptions")
        assert hasattr(exceptions,"ValidationError"), (
            "core.exceptions del progetto non espone ValidationError; "
            f"origine={exceptions.__file__}"
        )

def test_legacy_routes_present():
    require_test_environment(require_http=True)
    with deterministic_project_imports():
        main=importlib.import_module("main")
        _assert_project_module(main,"main")
        paths={getattr(r,"path","") for r in main.app.routes}
    for path in ("/api/stima_base","/api/salva_stima","/api/prefill","/sitemap.xml"):
        assert path in paths
