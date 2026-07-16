import pytest
from pydantic import ValidationError
from buy.schemas import BuyRequestCreate,LocationCreate,FeatureCreate

def test_buy_request_valid():
    x=BuyRequestCreate(contact_id=1,title='Casa mare',budget_target=200000,budget_max=250000)
    assert x.status=='draft'

def test_budget_order():
    with pytest.raises(ValidationError): BuyRequestCreate(contact_id=1,title='X',budget_target=300000,budget_max=200000)

def test_location_not_required_and_excluded():
    with pytest.raises(ValidationError): LocationCreate(location_type='municipality',municipality='Tortoreto',is_required=True,is_excluded=True)

def test_feature_levels():
    x=FeatureCreate(feature_code='elevator',requirement_level='required',value_boolean=True)
    assert x.requirement_level=='required'
