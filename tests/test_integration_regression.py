import importlib
from integration_p2_support import require_test_environment
def test_all_frozen_modules_importable():
 require_test_environment(require_http=True)
 for name in ("core.router","property.router","buy.router","match.router","flow.router","owner.router_admin","owner.router_portal"): importlib.import_module(name)
def test_legacy_routes_present():
 paths={getattr(r,"path","") for r in importlib.import_module("main").app.routes}
 for path in ("/api/stima_base","/api/salva_stima","/api/prefill","/sitemap.xml"): assert path in paths
