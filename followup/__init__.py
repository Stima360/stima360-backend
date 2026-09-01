"""P18-B Automated Follow-up Engine - foundation only.

Isolated, additive module. Unlike seller_intelligence/, this module is
allowed to depend on core/ - the whole point of P18 is turning events and
CRM state into CORE tasks, and core.repository.create_task_with_cursor is
the exact, already-proven entry point FLOW itself uses for the same reason
(see flow/repository.py). followup/ does not import from property/, buy/,
match/, owner/, flow/ or seller_intelligence/, and no existing module
imports from here.

P18-B ships the engine "off": run_followup()/safe_run_followup() exist and
are fully tested, but nothing in main.py calls them yet. No router, no
cron script - both arrive in a later milestone (P18-C for the immediate
stima_richiesta trigger wiring, P18-D for the time-based scan endpoint).
"""
