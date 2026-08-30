import pytest
from property.schemas import PropertyCreate, DocumentCreate, VisitCreate

def test_property_classification():
    assert PropertyCreate(title='Casa',classification='A').classification == 'A'
    with pytest.raises(Exception): PropertyCreate(title='Casa',classification='D')

def test_document_requires_location_when_available():
    with pytest.raises(Exception): DocumentCreate(document_type='ape',title='APE',status='available')
    assert DocumentCreate(document_type='ape',title='APE',status='missing').status == 'missing'

def test_visit_rating_range():
    with pytest.raises(Exception): VisitCreate(scheduled_at='2026-07-20T10:00:00',rating=6)
