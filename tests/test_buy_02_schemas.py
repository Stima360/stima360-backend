import pytest
from buy.schemas import InteractionCreate,MatchDecision,BuyTaskCreate

def test_interaction_requires_reference():
    with pytest.raises(Exception): InteractionCreate(interaction_type='proposed')

def test_match_decision_rejects_unknown_action():
    with pytest.raises(Exception): MatchDecision(action='emailed')

def test_buy_task_valid():
    task=BuyTaskCreate(title='Richiamare acquirente',priority='high')
    assert task.task_type=='buy_follow_up'
