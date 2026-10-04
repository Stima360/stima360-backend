"""CENSIMENTO-1 Fase 5 su un database SENZA la 083 (PROD prima della
migration, ordine DB-first): l'elenco immobili, i suoi filtri e la dashboard
continuano a funzionare - ogni riga e' operativa - e `record_kind=census` e'
semplicemente vuoto. Stessa fixture di `test_censimento_3_senza_083_postgres`."""
from __future__ import annotations

import pytest

from tests.test_censimento_3_senza_083_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _q, api, fino_alla_082,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def test_01_elenco_dashboard_e_avvisi_senza_083(fino_alla_082, api):
    tutte = {r[0] for r in _q(fino_alla_082, "SELECT id FROM properties WHERE agency_id = 1")}
    assert not _q(fino_alla_082, "SELECT 1 FROM information_schema.columns WHERE table_name = 'properties' "
                                 "AND column_name = 'record_kind'")
    for url in ("/api/property/properties?limit=200", "/api/property/properties?record_kind=all&limit=200"):
        r = api.get(url)
        assert r.status_code == 200, r.text
        assert {x["id"] for x in r.json()["items"]} == tutte, url
    r = api.get("/api/property/properties?record_kind=census&limit=200")
    assert r.status_code == 200 and r.json()["items"] == []
    d = api.get("/api/property/dashboard")
    assert d.status_code == 200, d.text
    assert d.json()["total"] == _q(fino_alla_082, "SELECT count(*) FROM properties WHERE agency_id = 1 AND archived_at IS NULL")[0][0]
    assert d.json()["census_units"] == 0
    assert api.get("/api/property/alerts").status_code == 200
