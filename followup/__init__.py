"""P18-B Automated Follow-up Engine - foundation only.

Isolated, additive module. Unlike seller_intelligence/, this module is
allowed to depend on core/ - the whole point of P18 is turning events and
CRM state into CORE tasks, and core.repository.create_task_with_cursor is
the exact, already-proven entry point FLOW itself uses for the same reason
(see flow/repository.py). followup/ does not import from property/, buy/,
match/, owner/, flow/ or seller_intelligence/, and no existing module
imports from here.

P18-C wires safe_run_followup() into /api/salva_stima for the immediate
event rule (FOLLOWUP_STIMA_RICHIESTA). P18-D2 adds a separate admin
temporal scan endpoint and external cron runner for one additive
time-based escalation rule, keeping all public-funnel behavior unchanged.
"""
