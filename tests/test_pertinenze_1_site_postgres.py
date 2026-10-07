"""PERTINENZE-1 (FASE E) - accessorio dal sito diventato unita' autonoma: nessun
doppio conteggio quando il sito lo ridichiara. PostgreSQL VERO, endpoint VERI
del sito (stesso harness di tests/test_catalogo_canonico_1_postgres.py).

  05a  stima con garage -> accessorio «Garage / box» Dal sito, «Da chiarire»;
       «Chiarisci › E' separata» -> unita' autonoma con la provenienza del
       sito; la stima dettagliata che ridichiara il garage (anche con mq
       diversi) NON ricrea l'accessorio e non apre differenze;
  05b  una differenza sul garage aperta PRIMA della trasformazione: «Applica»
       non ricrea l'accessorio (409 PERTINENZA_IS_UNIT, nulla scritto).
"""
from __future__ import annotations

from tests.test_catalogo_canonico_1_postgres import COMPLETA, _accessori, _fonti, _persona, sito  # noqa: F401
from tests.test_censimento_3_backend_postgres import DSN, _q, completo, mondo  # noqa: F401

import pytest

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def _trasforma_garage(s, pid):
    box = _q(s.m, "SELECT id FROM property_accessories WHERE property_id = %s AND kind = 'box'", (pid,))[0][0]
    r = s.api().post(f"/api/property/properties/{pid}/accessories/{box}/resolve", json={"outcome": "separate"})
    assert r.status_code == 200, r.text
    return r.json()["pertinenza"]


def test_05a_accessorio_del_sito_diventato_unita_non_si_ricrea(sito):
    s = sito
    sid = s.stima({**COMPLETA, **_persona()})["id"]
    pid = s.scheda_di(sid)
    assert _accessori(s.m, pid)["box"][2:] == ("unknown", "stima360")          # «Da chiarire», «Dal sito»
    unita = _trasforma_garage(s, pid)
    meta = _q(s.m, "SELECT metadata, pertinenza_kind, is_pertinenza FROM properties WHERE id = %s", (unita["id"],))[0]
    assert meta[0]["from_accessory"]["source"] == "stima360" and meta[1:] == ["box", True]
    assert "box" not in _accessori(s.m, pid)
    # la dettagliata ridichiara il garage, anche con mq diversi
    assert s.dettaglio_dal_prefill(sid, mqGarage="20", pertinenze="garage, cantina, balconi") == {"ok": True}
    assert "box" not in _accessori(s.m, pid)                                   # nessun doppio
    voce = _fonti(s, pid)["items"][0]
    assert [c for c in voce["conflicts"] if c["status"] == "open" and c["field"] == "accessory:box"] == []
    assert _q(s.m, "SELECT count(*) FROM properties WHERE parent_property_id = %s", (pid,))[0][0] == 1


def test_05b_applica_una_differenza_precedente_non_duplica(sito):
    s = sito
    sid = s.stima({**COMPLETA, **_persona()})["id"]
    pid = s.scheda_di(sid)
    box = _q(s.m, "SELECT id FROM property_accessories WHERE property_id = %s AND kind = 'box'", (pid,))[0][0]
    # l'agente corregge il garage; la dettagliata dice altro: differenza aperta
    assert s.api().patch(f"/api/property/properties/{pid}/accessories/{box}", json={"surface_sqm": 22}).status_code == 200
    s.dettaglio_dal_prefill(sid, mqGarage="19")
    voce = _fonti(s, pid)["items"][0]
    aperta = next(c for c in voce["conflicts"] if c["status"] == "open" and c["field"] == "accessory:box")
    # poi il garage risulta avere un suo sub: diventa unita' autonoma
    _trasforma_garage(s, pid)
    r = s.api().post(f"/api/property/properties/{pid}/site-sources/{voce['id']}/conflicts",
                     json={"conflict_id": aperta["id"], "action": "apply"})
    assert (r.status_code, r.json()["code"]) == (409, "PERTINENZA_IS_UNIT")
    assert "box" not in _accessori(s.m, pid)
    # «Ignora» resta possibile: chiude la differenza senza scrivere
    r = s.api().post(f"/api/property/properties/{pid}/site-sources/{voce['id']}/conflicts",
                     json={"conflict_id": aperta["id"], "action": "ignore"})
    assert r.status_code == 200 and "box" not in _accessori(s.m, pid)
