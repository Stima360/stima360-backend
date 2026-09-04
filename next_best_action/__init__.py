"""P23 Next Best Action module (read-derivative, deterministic, on-demand).

Aggregates signals already produced by P17-P22 (seller_intent, followup,
property_watch/invisible sale, match via FLOW) into a single primary
recommended action per subject (lead, buy_request, stima or match). This
module never recomputes or duplicates those signals - it only reads them
through their existing public service/repository functions and applies a
fixed precedence order (see engine.py) to pick one winner per subject.
"""
