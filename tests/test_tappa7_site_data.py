import pytest
from property.site_catalog import map_site_payload

@pytest.mark.parametrize('label,expected', [('Nessuna vista mare',False),('No',False),('-',None),('Non indicato',None),('Non lo so',None)])
def test_negative_and_unknown_sea_view_are_not_positive(label,expected):
    result=map_site_payload({'comune':'Tortoreto','vistaMare':label},comune='Tortoreto')
    assert result.fields.get('sea_view') is expected
    assert 'sea_view_detail' not in result.fields

@pytest.mark.parametrize('label', ['panoramica','parziale','scarsa'])
def test_declared_sea_view_is_preserved(label):
    result=map_site_payload({'comune':'Tortoreto','vistaMare':label},comune='Tortoreto')
    assert result.fields['sea_view'] is True
    assert result.fields['sea_view_detail']==label
