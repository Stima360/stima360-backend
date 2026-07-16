from match.engine import calculate

def request():
    return {"budget_target":200000,"budget_max":220000,"budget_flexibility_percent":5,"surface_min":70,"surface_target":80,"rooms_min":3,"bedrooms_min":2,"bathrooms_min":1,"locations":[{"municipality":"Tortoreto","priority":10,"is_required":True,"is_excluded":False}],"typologies":[{"property_type":"apartment","requirement_level":"required"}],"features":[{"feature_code":"elevator","requirement_level":"required","value_type":"boolean","value_boolean":True}]}

def prop():
    return {"city":"Tortoreto","property_type":"apartment","asking_price":210000,"surface_sqm":80,"rooms":3,"bedrooms":2,"bathrooms":1,"elevator":True,"condition":"good","metadata":{}}

def test_good_match():
    result=calculate(request(),prop())
    assert result["compatibility_status"]=="compatible"
    assert result["score_total"]>=80

def test_hard_fail_budget():
    p=prop();p["asking_price"]=400000
    result=calculate(request(),p)
    assert result["compatibility_status"]=="incompatible"
    assert result["hard_fail_count"]>=1

def test_hard_fail_required_feature():
    p=prop();p["elevator"]=False
    result=calculate(request(),p)
    assert "incompatible"==result["match_class"]
