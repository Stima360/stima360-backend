from .base import RuleDefinition

P = {"type": "enum", "values": ["low", "normal", "high", "urgent"]}
C = {"type": "integer", "min": 0, "max": 43200}

RULES = {
"FLOW-R001": RuleDefinition("FLOW-R001",1,"Lead senza attività","Crea un task per un lead aperto senza attività o task recenti.","core.lead_created","lead",{"inactivity_hours":24,"task_priority":"high","cooldown_minutes":1440},{"inactivity_hours":{"type":"integer","min":1,"max":720},"task_priority":P,"cooldown_minutes":C},"create_core_task"),
"FLOW-R002": RuleDefinition("FLOW-R002",1,"Incarico in scadenza","Crea un task per incarichi immobiliari prossimi alla scadenza.","property.mandate_expiring","property",{"days_before_expiry":15,"task_priority":"high","cooldown_minutes":10080},{"days_before_expiry":{"type":"integer","min":1,"max":90},"task_priority":P,"cooldown_minutes":C},"create_core_task"),
"FLOW-R003": RuleDefinition("FLOW-R003",1,"Problema documentale","Crea un task in presenza di documenti mancanti, scaduti o rifiutati.","property.document_issue","property",{"minimum_issue_count":1,"task_priority":"high","cooldown_minutes":1440},{"minimum_issue_count":{"type":"integer","min":1,"max":100},"task_priority":P,"cooldown_minutes":C},"create_core_task"),
"FLOW-R004": RuleDefinition("FLOW-R004",1,"Prossima azione BUY scaduta","Crea un task per richieste BUY attive con prossima azione scaduta.","buy.next_action_due","buy_request",{"overdue_hours":0,"task_priority":"high","cooldown_minutes":1440},{"overdue_hours":{"type":"integer","min":0,"max":720},"task_priority":P,"cooldown_minutes":C},"create_core_task"),
"FLOW-R005": RuleDefinition("FLOW-R005",1,"Match forte non proposto","Crea un task per match strong/excellent freschi non ancora proposti.","match.became_strong","match",{"minimum_score":80,"maximum_days_without_proposal":2,"task_priority":"high","cooldown_minutes":2880},{"minimum_score":{"type":"integer","min":0,"max":100},"maximum_days_without_proposal":{"type":"integer","min":0,"max":90},"task_priority":P,"cooldown_minutes":C},"create_core_task"),
"FLOW-R006": RuleDefinition("FLOW-R006",1,"Match da revisionare","Crea un task quando MATCH richiede revisione.","match.review_required","match",{"task_priority":"high","cooldown_minutes":1440},{"task_priority":P,"cooldown_minutes":C},"create_core_task"),
"FLOW-R007": RuleDefinition("FLOW-R007",1,"Visita senza feedback","Crea un task per visite completate senza feedback entro la soglia.","property.visit_completed","property_visit",{"feedback_wait_hours":24,"task_priority":"normal","cooldown_minutes":1440},{"feedback_wait_hours":{"type":"integer","min":1,"max":168},"task_priority":P,"cooldown_minutes":C},"create_core_task"),
}

def get_rule(code):
    try: return RULES[code]
    except KeyError: raise ValueError(f"unknown predefined rule {code}")
