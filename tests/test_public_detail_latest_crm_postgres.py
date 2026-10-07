"""F01/F16 su upstream CESTINO-CONTATTI-1: identita' nel Cestino e tenant altrui.

Usa il ciclo di vita CORE e la sincronizzazione del sito reali; il database
usa-e-getta e i trasporti esterni isolati sono quelli delle fixture esistenti.
"""
from __future__ import annotations

import pytest

from tests.test_catalogo_canonico_1_postgres import COMPLETA, _persona, sito  # noqa: F401
from tests.test_censimento_3_backend_postgres import DSN, _q, completo, mondo  # noqa: F401
from tests.test_cestino_contatti_1_postgres import _contatto, _sposta, k  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def test_public_detail_uses_new_live_contact_in_its_server_agency(k, sito):
    """Riusare il contatto nel Cestino o l'identita' dell'altro tenant deve fallire."""
    s = sito
    assert k["dsn"] == s.m["dsn"]
    persona = _persona()
    campi = {"display_name": "Mario Rossi", "first_name": persona["nome"],
             "last_name": persona["cognome"], "email": persona["email"], "phone": persona["telefono"]}
    vecchio = _contatto(k, **campi)
    spostato = _sposta(k, vecchio)
    assert spostato.status_code == 200, spostato.text
    altrui = _contatto(k, "owner_b", **campi)

    # La stessa identita' e' attiva anche in un'altra agenzia, con una scheda.
    altra_stima = s.stima({**COMPLETA, **persona}, agency=2)
    altro_sid = altra_stima["id"]
    altro_pid = s.scheda_di(altro_sid)
    assert altro_pid is not None
    assert _q(s.m, "SELECT l.contact_id, l.agency_id FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id "
                    "WHERE ls.stima_id = %s", (altro_sid,)) == [[altrui, 2]]
    contatti_prima = _q(s.m, "SELECT c.id, to_jsonb(c) FROM contacts c WHERE c.id = ANY(%s) ORDER BY c.id",
                       ([vecchio, altrui],))
    assert contatti_prima[0][1]["deleted_at"] is not None
    assert contatti_prima[1][1]["deleted_at"] is None
    immobili_prima = _q(s.m, "SELECT p.id, to_jsonb(p) FROM properties p ORDER BY p.id")
    altra_stima_prima = _q(s.m, "SELECT to_jsonb(s) FROM stime s WHERE s.id = %s", (altro_sid,))

    nuova = s.stima({**COMPLETA, **persona}, agency=1)
    sid, pid = nuova["id"], s.scheda_di(nuova["id"])
    assert nuova["success"] is True and pid is not None and pid != altro_pid
    collegamenti = _q(s.m, "SELECT c.id, c.agency_id, c.deleted_at, l.id, l.agency_id, p.id, p.agency_id "
                          "FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id "
                          "JOIN contacts c ON c.id = l.contact_id "
                          "JOIN property_site_sources ps ON ps.stima_id = ls.stima_id AND ps.status = 'active' "
                          "JOIN properties p ON p.id = ps.property_id WHERE ls.stima_id = %s", (sid,))
    assert len(collegamenti) == 1
    nuovo_cid, contact_agency, deleted_at, lead_id, lead_agency, property_id, property_agency = collegamenti[0]
    assert nuovo_cid not in (vecchio, altrui) and lead_id is not None
    assert (contact_agency, deleted_at, lead_agency, property_id, property_agency) == (1, None, 1, pid, 1)
    assert _q(s.m, "SELECT contact_id FROM property_contacts WHERE property_id = %s", (pid,)) == [[nuovo_cid]]

    assert nuova["token"] == s.token(sid)
    prefill = s.prefill(sid)
    assert prefill["id"] == sid and prefill["email"] == persona["email"]
    assert s.dettaglio({"token": nuova["token"], "stima_id": prefill["id"],
                       "agency_id": 2, "classe": "A4"}) == {"ok": True}
    assert _q(s.m, "SELECT stima_id, agency_id FROM stime_dettagliate WHERE stima_id = %s", (sid,)) == [[sid, 1]]
    assert s.scheda_di(sid) == pid and s.scheda(pid)["energy_class"] == "A4"

    # Ne' il contatto nel Cestino ne' quello altrui vengono riusati o modificati.
    assert _q(s.m, "SELECT c.id, to_jsonb(c) FROM contacts c WHERE c.id = ANY(%s) ORDER BY c.id",
              ([vecchio, altrui],)) == contatti_prima
    assert _q(s.m, "SELECT p.id, to_jsonb(p) FROM properties p WHERE p.id <> %s ORDER BY p.id", (pid,)) == immobili_prima
    assert _q(s.m, "SELECT to_jsonb(s) FROM stime s WHERE s.id = %s", (altro_sid,)) == altra_stima_prima
    assert _q(s.m, "SELECT count(*) FROM stime_dettagliate WHERE stima_id = %s", (altro_sid,))[0][0] == 0
