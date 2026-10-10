"""STIMA Voice - Fase 1: contratto degli intenti, pianificatore, date e politica.

Solo funzioni pure: nessun database, nessuna rete, nessun fornitore reale.
Il modello linguistico produce un `VoicePlanOutput` (schemas.py); il
pianificatore lo trasforma in un `Plan` deterministico (planner.py); la
politica decide per ogni passo se eseguire, chiedere o bloccare (policy.py).
Gli enum e i validatori sono quelli del CRM: qui non nasce nessuna regola
nuova su immobili, censimenti o agenda.
"""
