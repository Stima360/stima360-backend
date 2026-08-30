STIMA360 CORE Admin 0.3 — SOLO AMBIENTE TEST

Sostituire sul branch core-0.1-test:
- core/service.py
- static/core_admin/index.html
- static/core_admin/assets/app.css
- static/core_admin/assets/app.js

Facoltativo per test locali:
- tests/test_core_service_regressions.py

Nessuna migrazione database richiesta.
Non fare merge su main e non distribuire su produzione.
