"""FIX-MANDATE-1 - la definizione canonica di incarico, senza database.

  u01-u04  core/property_mandate.py: origine, dati parziali, campi mancanti, stato;
  s01      ogni consumatore usa la definizione canonica (nessuna regola locale);
  s02      la vista Incarichi = definizione canonica, origine acquisizione.

Origine "signed_link" (firma LMC-15): u01/u02 qui, il resto in
tests/test_fix_mandate_1_postgres.py (test_08-09).
"""
from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path

import pytest

from core import property_mandate as pm

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("riga,origine", [
    ({}, None),
    ({"acquisition_id": 5}, "acquisition"),                                    # dati azzerati: resta incarico
    ({"acquisition_id": 5, "mandate_type": "Esclusiva", "mandate_start": date(2026, 1, 1)}, "acquisition"),
    ({"mandate_type": "Esclusiva", "mandate_start": date(2025, 1, 1)}, "historical"),
    ({"mandate_type": "  ", "mandate_start": date(2025, 1, 1)}, None),         # tipo vuoto = mancante
    ({"mandate_type": "Esclusiva"}, None),
    ({"mandate_start": date(2025, 1, 1), "mandate_end": date(2025, 6, 1)}, None),
    ({"commercial_status": "mandate"}, None),
    ({"signed_mandate": {"id": 7, "mandate_signed_at": date(2025, 3, 1)}}, "signed_link"),
    ({"signed_mandate": None, "mandate_end": date(2025, 6, 1)}, None),
    # l'acquisizione resta l'origine principale anche con una firma LMC-15
    ({"acquisition_id": 5, "signed_mandate": {"id": 7}}, "acquisition"),
])
def test_u01_origine(riga, origine):
    assert pm.mandate_origin(riga) == origine
    assert pm.is_real_mandate(riga) is (origine is not None)


def test_u02_dati_parziali():
    assert pm.has_partial_mandate_data({"mandate_type": "Esclusiva"})
    assert pm.has_partial_mandate_data({"mandate_end": date(2026, 1, 1)})
    assert pm.has_partial_mandate_data({"commercial_status": "mandate"})
    assert not pm.has_partial_mandate_data({})
    assert not pm.has_partial_mandate_data({"commercial_status": "active"})
    # un incarico reale non e' "parziale", nemmeno con dati mancanti
    assert not pm.has_partial_mandate_data({"acquisition_id": 5})
    assert pm.missing_fields({"acquisition_id": 5, "mandate_start": date(2026, 1, 1)}) == ["mandate_type"]
    assert pm.missing_fields({"acquisition_id": 5}) == ["mandate_type", "mandate_start"]
    assert pm.missing_fields({"mandate_type": "X"}) == []                      # non e' un incarico
    # una firma LMC-15 rende reale l'incarico: i dati sulla scheda non sono "parziali"
    firmato = {"mandate_end": date(2026, 1, 1), "signed_mandate": {"id": 7}}
    assert not pm.has_partial_mandate_data(firmato) and pm.missing_fields(firmato) == []


def test_u03_stato_distinto_dall_esistenza():
    oggi = date(2026, 10, 6)
    base = {"acquisition_id": 5, "commercial_status": "active"}
    assert pm.mandate_state({**base, "mandate_end": date(2026, 12, 1)}, oggi) == "active"
    assert pm.mandate_state({**base, "mandate_end": None}, oggi) == "active"
    assert pm.mandate_state({**base, "mandate_end": "2026-10-05"}, oggi) == "expired"
    assert pm.mandate_state({**base, "commercial_status": "sold"}, oggi) == "sold"
    assert pm.mandate_state({**base, "commercial_status": "withdrawn"}, oggi) == "withdrawn"
    assert pm.mandate_state({**base, "archived_at": "2026-09-01T10:00:00Z", "commercial_status": "archived"}, oggi) == "archived"


def test_u04_predicato_sql():
    assert pm.real_mandate_sql("p") == (
        "(p.acquisition_id IS NOT NULL OR (NULLIF(BTRIM(p.mandate_type), '') IS NOT NULL AND p.mandate_start IS NOT NULL) "
        "OR EXISTS (SELECT 1 FROM stima_acquisitions sa_m WHERE sa_m.property_id = p.id "
        "AND sa_m.mandate_signed_at IS NOT NULL))")
    assert "properties.acquisition_id" in pm.real_mandate_sql("properties")
    assert "sa_m.property_id = properties.id" in pm.real_mandate_sql("properties")
    assert pm.acquisition_mandate_sql("p") == "p.acquisition_id IS NOT NULL"


def test_s01_ogni_consumatore_usa_la_definizione_canonica():
    from flow import adapters
    from property import lifecycle, mandates, repository
    assert pm.acquisition_mandate_sql("p") in mandates.E_UN_INCARICO
    assert "_mandato.is_real_mandate(prop)" in inspect.getsource(lifecycle.trash_blockers)
    assert "_ha_incarico" not in inspect.getsource(lifecycle)
    dc = inspect.getsource(lifecycle.delete_contact)
    assert "is_real_mandate(" in dc and 'prop.get("mandate_type") is not None' not in dc
    assert "has_partial_mandate_data(" in inspect.getsource(lifecycle.protected_history)
    assert repository._INCARICO == pm.real_mandate_sql("p")
    for funzione in (repository.dashboard, repository.alerts, repository.list_properties):
        assert "{_INCARICO}" in inspect.getsource(funzione), funzione.__name__
    assert pm.real_mandate_sql("properties") in adapters._SCANS["FLOW-R002"]["where"]


def test_s02_la_vista_incarichi_e_la_definizione_canonica_con_origine_acquisizione():
    from property import mandates
    assert mandates.E_UN_INCARICO.startswith(pm.acquisition_mandate_sql("p"))
    # nessuna richiesta locale di tipo o data: un incarico con dati incompleti si vede;
    # storici e firme LMC-15 restano fuori dalla sezione (scelta CRM-OPS-4)
    assert "mandate_type" not in mandates.E_UN_INCARICO and "mandate_start" not in mandates.E_UN_INCARICO
    assert "stima_acquisitions" not in mandates.E_UN_INCARICO
