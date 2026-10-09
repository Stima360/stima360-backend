"""Percorso locale reale: proposta, vendita, idempotenza e agenzia estranea."""
import uuid
from datetime import datetime, timedelta, timezone
from tests.test_cestino_richieste_1_postgres import completo, k, _contatto, _richiesta, _abbinamento, _q, IMMOBILE


def test_proposal_sale_lifecycle_idempotent_and_tenant_safe(k):
    buyer = _contatto(k)
    seller = _contatto(k)
    request_id = _richiesta(k, buyer)
    match_id = _abbinamento(k, request_id)
    _q(k, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner')", (IMMOBILE, seller))
    api = k['api']()
    foreign = k['api']('owner_b')
    body = dict(match_id=match_id, amount=190000,
                expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
                idempotency_key=str(uuid.uuid4()))
    response = api.post('/api/proposals', json=body)
    assert response.status_code == 201, response.text
    proposal = response.json()['id']
    assert api.post('/api/proposals', json=body).json()['id'] == proposal
    assert foreign.get(f'/api/proposals/{proposal}').status_code == 404
    for state in ('submitted', 'accepted'):
        response = api.post(f'/api/proposals/{proposal}/transition', json={'target_status': state})
        assert response.status_code == 200, response.text
        assert response.json()['status'] == state
    body = dict(proposal_id=proposal, idempotency_key=str(uuid.uuid4()))
    response = api.post('/api/sales', json=body)
    assert response.status_code == 201, response.text
    sale = response.json()['id']
    assert response.json()['status'] == 'pending'
    assert api.post('/api/sales', json=body).json()['id'] == sale
    assert foreign.get(f'/api/sales/{sale}').status_code == 404
    assert foreign.post(f'/api/sales/{sale}/complete').status_code == 404
    assert _q(k, 'SELECT status FROM property_sales WHERE id=%s', (sale,))[0][0] == 'pending'
    response = api.post(f'/api/sales/{sale}/complete')
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'completed'
    assert _q(k, 'SELECT commercial_status FROM properties WHERE id=%s', (IMMOBILE,))[0][0] == 'sold'
    assert _q(k, 'SELECT status FROM buy_requests WHERE id=%s', (request_id,))[0][0] == 'satisfied'
    history = _q(k, 'SELECT count(*) FROM property_status_history WHERE property_id=%s', (IMMOBILE,))[0][0]
    assert api.post(f'/api/sales/{sale}/complete').status_code == 200
    assert _q(k, 'SELECT count(*) FROM property_status_history WHERE property_id=%s', (IMMOBILE,))[0][0] == history
    assert _q(k, 'SELECT count(*) FROM property_sales WHERE proposal_id=%s', (proposal,))[0][0] == 1
