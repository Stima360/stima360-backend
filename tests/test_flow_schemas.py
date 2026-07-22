import pytest
from pydantic import ValidationError
from flow.schemas import ScanRequest,EventCreate

def test_scan_default_limit(): assert ScanRequest().limit==50
def test_scan_max_limit(): assert ScanRequest(limit=200).limit==200
def test_scan_over_limit_rejected():
    with pytest.raises(ValidationError): ScanRequest(limit=201)
def test_event_source_allowlist():
    with pytest.raises(ValidationError): EventCreate(event_type='x',entity_type='lead',entity_id=1,source_module='email')
