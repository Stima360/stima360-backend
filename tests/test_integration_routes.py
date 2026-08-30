from collections import Counter
from integration_p2_support import require_test_environment, import_main_app

EXPECTED_MOUNTS={"/core-admin","/property-admin","/buy-admin","/match-admin","/flow-admin","/owner-admin","/owner"}

def app():
    require_test_environment(require_http=True)
    return import_main_app()

def test_no_exact_method_path_collisions():
    pairs=[]
    for route in app().routes:
        for method in (getattr(route,"methods",None) or set()):
            if method not in {"HEAD","OPTIONS"} and getattr(route,"path",None):
                pairs.append((method,route.path))
    assert {k:v for k,v in Counter(pairs).items() if v>1}=={}

def test_expected_mounts():
    mounts={getattr(r,"path",None) for r in app().routes if r.__class__.__name__=="Mount"}
    assert EXPECTED_MOUNTS<=mounts

def test_openapi_prefixes():
    paths=set(app().openapi()["paths"])
    for prefix in ("/api/core","/api/property","/api/buy","/api/match","/api/flow","/api/owner/admin","/api/owner/portal"):
        assert any(p.startswith(prefix) for p in paths)
