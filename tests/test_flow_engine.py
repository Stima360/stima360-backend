from datetime import datetime,timezone,timedelta,date
from flow.engine import evaluate,build_action

def test_lead_without_activity_matches():
    ok,_=evaluate('FLOW-R001',{'status':'open','activity_count':0,'open_task_count':0},{'inactivity_hours':24}); assert ok
def test_lead_with_task_not_match():
    ok,_=evaluate('FLOW-R001',{'status':'open','activity_count':0,'open_task_count':1},{'inactivity_hours':24}); assert not ok
def test_mandate_expiring_matches():
    ok,_=evaluate('FLOW-R002',{'commercial_status':'active','mandate_end':date.today()+timedelta(days=10)},{'days_before_expiry':15}); assert ok
def test_buy_overdue_matches():
    ok,_=evaluate('FLOW-R004',{'status':'active','next_action_at':datetime.now(timezone.utc)-timedelta(hours=2)},{'overdue_hours':1}); assert ok
def test_strong_match_not_proposed():
    ok,_=evaluate('FLOW-R005',{'freshness_status':'fresh','score_total':85,'commercial_status':'new','proposed_count':0},{'minimum_score':80}); assert ok
def test_action_is_internal_task():
    a=build_action('FLOW-R001',{'entity_type':'lead','entity_id':1,'id':1,'assigned_to':'g'}, {'task_priority':'high'}); assert a['action_type']=='create_core_task' and a['lead_id']==1
