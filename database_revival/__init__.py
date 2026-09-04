"""P24 Database Revival - Seller revival daily batch + cooldown.

Isolated, additive domain package (same shape as seller_intelligence/,
followup/, seller_intent/, property_watch/): reads leads/contacts/
properties/activities/tasks (read-only) to find dormant, paused seller
leads, and writes exclusively to its own seller_revival_suppressions table
to enforce the 90-day cooldown and the 20-per-day batch cap.

V1 scope: no router.py/schemas.py/exceptions.py - this module exposes no
API of its own; it is consumed only by next_best_action/signals.py and
next_best_action/service.py as source_signal="database_revival" (P23
precedence rank #6). It never creates CORE tasks and never sends any
communication.
"""
