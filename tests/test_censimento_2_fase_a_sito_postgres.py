"""CENSIMENTO-2 Fase A - la sincronizzazione dal sito non sovrascrive una
superficie VERIFICATA (property/site_sync.py::_applica), su PostgreSQL VERO e
con il percorso reale del sito (stima rapida -> stima dettagliata), lo stesso
banco di tests/test_catalogo_canonico_1_postgres.py (database locale usa-e-getta).

  s01  superficie verificata + nuova superficie dal sito -> nessuna scrittura:
       conflitto «Applica / Ignora»; valore, provenienza, fonte e storico
       intatti; gli altri campi non verificati si aggiornano come prima;
  s02  «Applica» sulla superficie verificata -> 409, nulla scritto; «Ignora»
       -> 200 e il valore ignorato non torna;
  s03  superficie NON verificata: si aggiorna dal sito come prima;
  s04  concorrenza (lock, nessuna attesa a tempo): la sincronizzazione arriva
       mentre una verifica e' in corso -> attende la riga e la trova
       verificata: conflitto, nessuna sovrascrittura;
  s05  concorrenza nell'altro ordine: la verifica arriva mentre la
       sincronizzazione sta scrivendo -> attende; con lo stato letto dal
       modulo (expected_updated_at) e' rifiutata (STALE_DATA), nulla perso.
"""
from __future__ import annotations

import threading
import time
from decimal import Decimal

import pytest

from tests.test_catalogo_canonico_1_postgres import COMPLETA, _fonti, _persona, sito  # noqa: F401
from tests.test_censimento_3_backend_postgres import DSN, _in_parallelo, _q, completo, mondo  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

IDENTITA = {"cadastral_municipality_code": "L307", "cadastral_section": "", "cadastral_sheet": "5",
            "cadastral_parcel": "61", "cadastral_subunit": "2"}
VERIFICA = {"cadastral_evidence_type": "visura", "cadastral_evidence_date": "2026-10-01",
            "cadastral_evidence_ref": "prot. 9"}
COLONNE = ("surface_sqm", "surface_source", "cadastral_evidence_type", "cadastral_evidence_date",
           "cadastral_evidence_ref", "cadastral_evidence_identity", "commercial_status", "parent_property_id",
           "building_id", "record_kind")


def _scheda(s, pid):
    return dict(zip(COLONNE, _q(s.m, f"SELECT {', '.join(COLONNE)} FROM properties WHERE id = %s", (pid,))[0]))


def _storico_dati(s, pid):
    return _q(s.m, "SELECT id, metadata FROM activities WHERE property_id = %s AND metadata->>'census_data' = 'true' "
                   "ORDER BY id", (pid,))


def _stima_verificata(s):
    sid = s.stima({**COMPLETA, **_persona()})["id"]
    pid = s.scheda_di(sid)
    assert _scheda(s, pid)["surface_sqm"] == Decimal("85.50")                # dal sito
    r = s.api().patch(f"/api/property/properties/{pid}/census-data",
                      json={**IDENTITA, "surface_source": "verified", **VERIFICA})
    assert r.status_code == 200 and r.json()["surface_source"] == "verified", r.text
    return sid, pid


def _aperti(s, pid):
    voce = _fonti(s, pid)["items"][0]
    return voce, {c["field"]: c for c in voce["conflicts"] if c["status"] == "open"}


def test_s01_superficie_verificata_non_sovrascritta_dal_sito(sito):
    s = sito
    sid, pid = _stima_verificata(s)
    prima, storico = _scheda(s, pid), _storico_dati(s, pid)
    # il cliente corregge la superficie (90) e i locali (2) nella stima dettagliata
    assert s.dettaglio_dal_prefill(sid, mq="90", locali="2") == {"ok": True}
    dopo = _scheda(s, pid)
    assert dopo == prima                                    # valore, provenienza, fonte, collegamenti, stato: intatti
    assert _storico_dati(s, pid) == storico                 # nessuna riga di storico toccata o aggiunta
    _voce, aperti = _aperti(s, pid)
    assert Decimal(str(aperti["surface_sqm"]["site_value"])) == 90 and Decimal(str(aperti["surface_sqm"]["current_value"])) == Decimal("85.5")
    # i campi NON verificati e non toccati dall'agente seguono il sito come prima
    assert _q(s.m, "SELECT rooms FROM properties WHERE id = %s", (pid,))[0][0] == 2 and "rooms" not in aperti


def test_s02_applica_rifiutato_ignora_ammesso(sito):
    s = sito
    sid, pid = _stima_verificata(s)
    s.dettaglio_dal_prefill(sid, mq="90")
    voce, aperti = _aperti(s, pid)
    base = f"/api/property/properties/{pid}/site-sources/{voce['id']}/conflicts"
    r = s.api().post(base, json={"conflict_id": aperti["surface_sqm"]["id"], "action": "apply"})
    assert (r.status_code, r.json()["code"]) == (409, "VERIFIED_DATA_LOCKED"), r.text
    assert _scheda(s, pid)["surface_source"] == "verified"
    r = s.api().post(base, json={"conflict_id": aperti["surface_sqm"]["id"], "action": "ignore"})
    assert r.status_code == 200, r.text
    # lo stesso valore ignorato non torna come conflitto
    s.dettaglio_dal_prefill(sid, mq="90", locali="4")
    _voce, aperti = _aperti(s, pid)
    assert "surface_sqm" not in aperti
    assert _scheda(s, pid)["surface_sqm"] == Decimal("85.50") and _scheda(s, pid)["surface_source"] == "verified"


def test_s03_superficie_non_verificata_si_aggiorna_come_prima(sito):
    s = sito
    sid = s.stima({**COMPLETA, **_persona()})["id"]
    pid = s.scheda_di(sid)
    assert s.api().patch(f"/api/property/properties/{pid}/census-data",
                         json={"surface_source": "declared"}).status_code == 200
    s.dettaglio_dal_prefill(sid, mq="90")
    dopo = _scheda(s, pid)
    assert dopo["surface_sqm"] == Decimal("90.00")
    assert dopo["surface_source"] is None                   # regola della 100: valore cambiato senza riconferma
    _voce, aperti = _aperti(s, pid)
    assert "surface_sqm" not in aperti


def _in_attesa(s, frammento, timeout=10):
    scadenza = time.monotonic() + timeout
    while time.monotonic() < scadenza:
        if _q(s.m, "SELECT count(*) FROM pg_stat_activity WHERE datname = %s AND wait_event_type = 'Lock' "
                   "AND query LIKE %s", (s.m["nome"], frammento))[0][0]:
            return True
        time.sleep(0.02)
    return False


def test_s04_sincronizzazione_durante_una_verifica(sito, monkeypatch):
    s = sito
    from property import census
    sid = s.stima({**COMPLETA, **_persona()})["id"]
    pid = s.scheda_di(sid)
    entrata, via = threading.Event(), threading.Event()
    originale = census._attivita_sistema

    def lento(*a, **k):
        entrata.set()
        assert via.wait(20)
        return originale(*a, **k)
    monkeypatch.setattr(census, "_attivita_sistema", lento)

    def verifica():
        return s.api().patch(f"/api/property/properties/{pid}/census-data",
                             json={**IDENTITA, "surface_source": "verified", **VERIFICA})

    def sincronizza():
        assert entrata.wait(20)
        return s.dettaglio_dal_prefill(sid, mq="90")

    def sblocca():
        assert entrata.wait(20)
        ferma = _in_attesa(s, "%FROM properties WHERE id = % FOR UPDATE%", timeout=5)
        via.set()
        return ferma

    r_ver, r_sync, ferma = _in_parallelo([verifica, sincronizza, sblocca])
    assert r_ver.status_code == 200 and r_sync == {"ok": True}
    dopo = _scheda(s, pid)
    assert (dopo["surface_sqm"], dopo["surface_source"]) == (Decimal("85.50"), "verified")
    _voce, aperti = _aperti(s, pid)
    assert Decimal(str(aperti["surface_sqm"]["site_value"])) == 90
    assert ferma is True                                    # la sincronizzazione ha atteso la riga bloccata


def test_s05_verifica_durante_la_sincronizzazione(sito, monkeypatch):
    s = sito
    from property import site_sync
    sid = s.stima({**COMPLETA, **_persona()})["id"]
    pid = s.scheda_di(sid)
    letto = s.api().get(f"/api/property/properties/{pid}").json()["updated_at"]   # il modulo aperto
    entrata, via = threading.Event(), threading.Event()
    originale = site_sync._scrivi_campi

    def lenta(cur, prop, valori):
        if "surface_sqm" in valori:
            entrata.set()
            assert via.wait(20)
        return originale(cur, prop, valori)
    monkeypatch.setattr(site_sync, "_scrivi_campi", lenta)

    def sincronizza():
        return s.dettaglio_dal_prefill(sid, mq="90")

    def verifica():
        assert entrata.wait(20)
        return s.api().patch(f"/api/property/properties/{pid}/census-data",
                             json={**IDENTITA, "surface_source": "verified", **VERIFICA, "expected_updated_at": letto})

    def sblocca():
        assert entrata.wait(20)
        ferma = _in_attesa(s, "%FROM properties WHERE id = % AND agency_id = % FOR UPDATE%", timeout=5)
        via.set()
        return ferma

    r_sync, r_ver, ferma = _in_parallelo([sincronizza, verifica, sblocca])
    assert r_sync == {"ok": True}
    assert (r_ver.status_code, r_ver.json()["code"]) == (409, "STALE_DATA"), r_ver.text
    dopo = _scheda(s, pid)
    assert (dopo["surface_sqm"], dopo["surface_source"]) == (Decimal("90.00"), None)   # nessuna verifica cieca
    assert _storico_dati(s, pid) == []
    assert ferma is True
