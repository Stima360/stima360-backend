"""`scripts/v3_diagnosi.py`: sola lettura, nessun indirizzo stampato, e dice
le cose giuste sui casi tipici (idoneo, senza consenso, stima agganciata,
telefono di un altro contatto)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo  # noqa: F401
from tests.test_delete_arch_0_postgres import _lead, _stima, mondo, operatori  # noqa: F401
from tests.test_fase_b_followup_postgres import GIORNO, _contatto, _sistema, fb  # noqa: F401
from tests.test_v3_followup_postgres import T0, _attiva, _tick, v3  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "v3_diagnosi.py"


def _modulo():
    spec = importlib.util.spec_from_file_location("v3_diagnosi", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_01_quadro_sola_lettura_e_senza_dati_personali(v3, monkeypatch):
    import psycopg2
    m = v3
    _attiva(m)
    idoneo = _contatto(m, "idoneo@example.it", consenso="granted", quando=T0 - 10 * GIORNO)
    senza = _contatto(m, "senza@example.it")
    _tick(m, T0)
    stima = _stima(m)
    _q(m, "INSERT INTO lead_stime (lead_id, stima_id, relation_type) VALUES (%s, %s, 'origin')", (_lead(m, idoneo), stima))
    _q(m, "UPDATE stime SET email = 'altra@example.it', telefono = '333 000 1111' WHERE id = %s", (stima,))
    _q(m, "UPDATE contacts SET phone = '333 000 1111', phone_normalized = '3330001111' WHERE id = %s", (senza,))
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "stima360-backend-test.onrender.com")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://stima360-backend.onrender.com")
    monkeypatch.delenv("COMMUNICATION_CRON_TOKEN", raising=False)
    diagnosi = _modulo()
    conn = psycopg2.connect(m["dsn"])
    conn.set_session(readonly=True)
    try:
        with conn.cursor() as cur:
            righe = diagnosi.quadro(cur, [idoneo, senza, 999999], [stima])
    finally:
        conn.rollback()
        conn.close()
    testo = "\n".join(righe + diagnosi.ambiente())
    for pezzo in ("idoneo@", "senza@", "altra@", "3330001111", "333 000"):
        assert pezzo not in testo, pezzo
    assert "stima_lead v3 | active" in testo
    assert f"== CONTATTO {idoneo} ==" in testo and "iscrizione" in testo and "email granted" in testo
    assert "nessun consenso EMAIL valido: NON entra nel V3" in testo
    assert "non esiste" in testo
    assert "email nuova ma telefono di un contatto esistente" in testo
    assert "puntano a UN ALTRO servizio" in testo and "NON configurata" in testo
