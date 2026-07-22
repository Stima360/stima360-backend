import pytest
from flow.rules import RULES,get_rule

def test_seven_predefined_rules(): assert set(RULES)=={f'FLOW-R00{i}' for i in range(1,8)}
def test_rules_versioned(): assert all(r.version>=1 for r in RULES.values())
def test_unknown_parameter_rejected():
    with pytest.raises(ValueError): get_rule('FLOW-R002').validate_parameters({'sql':'DROP'})
def test_parameter_bounds():
    with pytest.raises(ValueError): get_rule('FLOW-R002').validate_parameters({'days_before_expiry':91})
def test_parameters_hash_stable():
    r=get_rule('FLOW-R002'); assert r.parameters_hash({'a':1,'b':2})==r.parameters_hash({'b':2,'a':1})
def test_no_external_actions(): assert all(r.action_type=='create_core_task' for r in RULES.values())
