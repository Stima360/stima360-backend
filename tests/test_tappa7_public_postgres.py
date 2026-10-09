"""Real public writer; isolated fixtures capture every delivery."""
import pytest
from tests.test_public_submission_receipts_postgres import (
    completo, mondo, sito, submission, _only_the_disposable_cluster, _post, _q,
)

@pytest.mark.parametrize('value,expected', [('false',False),('no',False),('0',False),(1,False),(False,False),(True,True)])
def test_only_explicit_json_true_grants_marketing(submission,value,expected):
    p=submission
    result=_post(p,p.identity(consenso_marketing=value))
    row=_q(p.s.m,"""SELECT s.consenso_marketing,c.marketing_consent,
       (SELECT count(*) FROM consent_events e WHERE e.contact_id=c.id AND e.purpose='marketing' AND e.decision='granted')
       FROM stime s JOIN lead_stime ls ON ls.stima_id=s.id JOIN leads l ON l.id=ls.lead_id
       JOIN contacts c ON c.id=l.contact_id WHERE s.id=%s""",(result['id'],))[0]
    assert row[0] is expected
    assert bool(row[1]) is expected
    assert row[2]==int(expected)

@pytest.mark.parametrize('label,expected',[('Nessuna vista mare',False),('-',None)])
def test_site_sea_view_reaches_crm_without_invented_positive(submission,label,expected):
    p=submission
    result=_post(p,p.identity(vistaMareYN=None,vistaMareDettaglio=None,vistaMare=label))
    row=_q(p.s.m,"""SELECT p.sea_view,p.sea_view_detail FROM properties p
      JOIN property_site_sources ps ON ps.property_id=p.id WHERE ps.stima_id=%s AND ps.status='active'""",(result['id'],))[0]
    assert row[0] is expected
    assert row[1] is None
