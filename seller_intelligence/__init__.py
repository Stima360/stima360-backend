"""P17 Seller Intelligence Foundation.

Isolated, additive module. It does not import from ``core/``, ``property/``,
``owner/``, ``flow/`` or any other domain package, and no existing module
imports from here yet (P17-A only). It depends solely on the shared
``database.get_connection`` helper, exactly like ``core/database.py`` does,
to avoid coupling with any other domain.

Nothing in ``main.py`` is registered or modified by P17-A: the router in
this package is exercised only by its own isolated test suite, mounted on a
throwaway FastAPI app (see tests/test_seller_intelligence_router.py).
"""
