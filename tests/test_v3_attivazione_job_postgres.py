"""`scripts/v3_attivazione_job.py`: il job di attivazione, su PostgreSQL usa-e-getta.

Spunta del sito con prova riconosciuta, esclusi in pausa, idonei iscritti,
prima email accettata dal server (SMTP intercettato), revocati e senza prova
elencati con il motivo; nessun consenso inventato; nessun indirizzo stampato.
"""
from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo  # noqa: F401
from tests.test_delete_arch_0_postgres import mondo, operatori  # noqa: F401
from tests.test_fase_b_followup_postgres import GIORNO, _contatto, _iscrizioni, fb  # noqa: F401
from tests.test_site_recognition_postgres import _informativa_sito, _spunta_sito
from tests.test_v3_followup_postgres import T0, _attiva, v3  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "v3_attivazione_job.py"
AUTORIZZATO = "collaudo@stima360-test.invalid"


def test_01_il_job_attiva_gli_idonei_ed_esclude_gli_altri(v3, monkeypatch, capsys):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from communication.providers import email_smtp
    m = v3
    _attiva(m)
    monkeypatch.setenv("COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS", AUTORIZZATO)
    posta = []
    monkeypatch.setattr(email_smtp, "invia_mail", lambda a, o, c, allegato=None, **kw: posta.append(a) or True)
    idoneo = _contatto(m, AUTORIZZATO, consenso="granted", quando=T0 - 10 * GIORNO)
    escluso = _contatto(m, "escluso@example.it", consenso="granted", quando=T0 - 10 * GIORNO)
    revocato = _contatto(m, "revocato@example.it", consenso="revoked", quando=T0 - 10 * GIORNO)
    senza = _contatto(m, "senza@example.it")
    spunta = T0 - 20 * GIORNO
    _informativa_sito(m, registrata=spunta - timedelta(hours=2))
    dal_sito, stima = _spunta_sito(m, "sito@example.it", quando=spunta)
    from tests.test_delete_arch_0_postgres import _lead
    _q(m, "INSERT INTO lead_stime (lead_id, stima_id, relation_type) VALUES (%s, %s, 'origin')", (_lead(m, dal_sito), stima))
    spec = importlib.util.spec_from_file_location("v3_attivazione_job", SCRIPT)
    job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(job)
    conn = psycopg2.connect(m["dsn"], cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            email_operatore = _q(m, "SELECT email FROM operator_users WHERE id = %s", (m["ids"]["owner_a"],))[0][0]
            ctx = job._ctx_operatore(cur, email_operatore, 1)
        conn.rollback()
        with conn.cursor() as cur:
            esito = job.esegui(cur, ctx, [escluso])
        conn.rollback()
        with conn.cursor() as cur:
            r = job.rapporto(cur, ctx, [escluso])
        conn.rollback()
    finally:
        conn.close()
    assert esito["riconosciuti"] >= 1 and esito["tick"]["enrolled_active"] == 2
    assert esito["dispatch"]["sent"] == 1 and posta == [AUTORIZZATO]
    stati = {c["contact_id"]: c for c in r["contatti"]}
    assert stati[idoneo]["enrollment"]["status"] == "active" and stati[dal_sito]["enrollment"]["status"] == "active"
    assert stati[escluso]["enrollment"] is None and "escluso" in stati[escluso]["why"]
    assert stati[revocato]["why"] == "revocato/disiscritto"
    assert "da regolarizzare" in stati[senza]["why"]
    assert _q(m, "SELECT count(*) FROM consent_events WHERE contact_id = %s", (senza,))[0][0] == 0, "niente inventato"
    job.stampa(r, [escluso])
    testo = capsys.readouterr().out
    assert "@" not in testo and "consenso email valido: 3" in testo and "iscritti alla sequenza: 2" in testo
