from unittest.mock import patch
from types import SimpleNamespace
from flow import service

def test_simulation_records_no_live_action():
    payload=SimpleNamespace(entity_type='lead',entity_id=1,requested_by='test')
    payload.dict=lambda exclude_unset=False:{'entity_type':'lead','entity_id':1,'requested_by':'test'}
    row={'parameters':{'inactivity_hours':24,'task_priority':'high','cooldown_minutes':1440}}
    with patch('flow.service.repository.get_rule_row',return_value=row),patch('flow.service.load_entity',return_value={'id':1,'entity_type':'lead','entity_id':1,'status':'open','activity_count':0,'open_task_count':0}),patch('flow.service.repository.record_simulation',return_value={'status':'matched'}) as rec,patch('flow.service.repository.execute_live') as live:
        out=service.simulate('FLOW-R001',payload); assert out['status']=='matched'; rec.assert_called_once(); live.assert_not_called()

def test_retry_calls_increment_before_execute():
    payload=SimpleNamespace(requested_by='test'); payload.dict=lambda exclude_unset=False:{'requested_by':'test'}
    ex={'rule_code':'FLOW-R001','entity_type':'lead','entity_id':1}
    row={'parameters':{'inactivity_hours':24,'task_priority':'high','cooldown_minutes':1440}}
    entity={'id':1,'entity_type':'lead','entity_id':1,'status':'open','activity_count':0,'open_task_count':0}
    with patch('flow.service.repository.increment_retry',return_value={}) as inc,patch('flow.service.repository.get_execution',return_value=ex),patch('flow.service.load_entity',return_value=entity),patch('flow.service.repository.get_rule_row',return_value=row),patch('flow.service.repository.execute_live',return_value={'status':'executed'}) as run:
        assert service.retry(4,payload)['status']=='executed'; inc.assert_called_once_with(4); run.assert_called_once()
