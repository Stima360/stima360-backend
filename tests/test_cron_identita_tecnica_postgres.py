"""Il cron delle comunicazioni con l'IDENTITA' TECNICA, su PostgreSQL usa-e-getta.

La rotta interna vera (`communication/cron_router.py`), il motore delle
sequenze, il dispatcher e l'adapter email reale; la sola primitiva
`invia_mail` registra invece di spedire. Il limite dei destinatari TEST e'
attivo come sul CRM TEST.

  01  senza segreto la rotta e' spenta; segreto sbagliato 401; agenzia non
      consentita 403; agenzia sospesa 409; campo in piu' 422.
  02  il giro: iscrive il proprietario idoneo, spedisce SOLO all'indirizzo
      autorizzato, il cliente resta in coda intatto; il secondo giro non
      duplica niente.
  03  il segreto non apre nessun'altra rotta del CRM.
"""
from __future__ import annotations

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo  # noqa: F401
from tests.test_delete_arch_0_postgres import mondo, operatori  # noqa: F401
from tests.test_fase_b_followup_postgres import (  # noqa: F401
    GIORNO, _contatto, _ctx, _iscrizioni, fb)
from tests.test_v3_followup_postgres import T0, _attiva, v3  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

SEGRETO = "s" * 40
AUTORIZZATO = "collaudo@stima360-test.invalid"


@pytest.fixture
def giro(v3, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from communication import cron_router
    from communication.providers import email_smtp

    monkeypatch.setenv(cron_router.TOKEN_ENV, SEGRETO)
    monkeypatch.setenv(cron_router.AGENCIES_ENV, "1")
    monkeypatch.setenv("COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS", AUTORIZZATO)
    posta = []
    monkeypatch.setattr(email_smtp, "invia_mail",
                        lambda a, oggetto, corpo, allegato=None, **kw: posta.append(a) or True)
    app = FastAPI()
    app.include_router(cron_router.router)
    client = TestClient(app)

    def chiama(corpo=None, segreto=SEGRETO):
        intestazioni = {cron_router.HEADER: segreto} if segreto is not None else {}
        return client.post("/api/internal/communication/cron/run", headers=intestazioni,
                           json=corpo if corpo is not None else {"agency_id": 1, "channel": "email"})
    return {"m": v3, "chiama": chiama, "posta": posta}


def test_01_rifiuti(giro, monkeypatch):
    from communication import cron_router
    chiama, m = giro["chiama"], giro["m"]
    assert chiama(segreto=None).status_code == 401
    assert chiama(segreto="x" * 40).status_code == 401
    assert chiama({"agency_id": 2, "channel": "email"}).status_code == 403
    assert chiama({"agency_id": 1, "channel": "email", "agency_ids": [2]}).status_code == 422
    monkeypatch.setenv(cron_router.AGENCIES_ENV, "1,2")
    _q(m, "UPDATE agencies SET status = 'suspended' WHERE id = 2")
    assert chiama({"agency_id": 2, "channel": "email"}).status_code == 409
    monkeypatch.setenv(cron_router.TOKEN_ENV, "corto")
    risposta = chiama(segreto="corto")
    assert risposta.status_code == 503 and risposta.json()["detail"]["code"] == "cron_auth_not_configured"
    monkeypatch.delenv(cron_router.TOKEN_ENV)
    assert chiama(segreto="").status_code == 503
    assert giro["posta"] == [] and _q(m, "SELECT count(*) FROM communication_messages")[0][0] == 0


def test_02_il_giro_iscrive_e_spedisce_solo_all_autorizzato(giro):
    from communication import contact_view
    chiama, m = giro["chiama"], giro["m"]
    _attiva(m)
    prova = _contatto(m, AUTORIZZATO, consenso="granted", quando=T0 - 10 * GIORNO)
    cliente = _contatto(m, "cliente.reale@example.it", consenso="granted", quando=T0 - 10 * GIORNO)
    for cid in (prova, cliente):
        contact_view.send_manual(_ctx(m, "owner_a"), cid, subject="[PROVA] collaudo", body="Prova di invio.")

    primo = chiama()
    assert primo.status_code == 200, primo.text
    corpo = primo.json()
    assert corpo["journey"]["status"] == "completed" and corpo["journey"]["counts"]["enrolled_active"] == 2
    assert corpo["dispatch"]["sent"] == 1 and corpo["dispatch"]["failed"] == 0
    assert giro["posta"] == [AUTORIZZATO]
    fermo = _q(m, "SELECT status, attempt_count FROM communication_messages WHERE destination_snapshot = %s",
               ("cliente.reale@example.it",))
    assert [tuple(r) for r in fermo] == [("queued", 0)], "il cliente resta in coda, intatto"
    assert {e["contact_id"] for e in _iscrizioni(m)} == {prova, cliente}

    secondo = chiama()
    assert secondo.status_code == 200
    assert secondo.json()["journey"]["counts"]["enrolled_active"] == 0
    assert secondo.json()["dispatch"]["sent"] == 0
    assert giro["posta"] == [AUTORIZZATO]
    assert len(_iscrizioni(m)) == 2


def test_03_il_segreto_non_apre_nessun_altra_rotta(giro):
    from fastapi.testclient import TestClient
    import main as main_module
    from communication import cron_router
    # Il mondo di test sostituisce l'autenticazione dell'app (`mondo`): qui
    # serve quella VERA, quindi le sostituzioni si tolgono e poi si rimettono.
    salvate = dict(main_module.app.dependency_overrides)
    main_module.app.dependency_overrides.clear()
    try:
        _nessun_altra_rotta(TestClient(main_module.app), {cron_router.HEADER: SEGRETO})
    finally:
        main_module.app.dependency_overrides.update(salvate)
    # E nel codice dell'applicazione il segreto lo legge UN file solo.
    from pathlib import Path
    radice = Path(__file__).resolve().parents[1]
    lettori = {str(f.relative_to(radice)) for f in radice.rglob("*.py")
               if "tests" not in f.parts and f.name != "run_communication_dispatch_cron.py"
               and ("COMMUNICATION_CRON_TOKEN" in f.read_text(encoding="utf-8", errors="ignore")
                    or "X-Stima360-Cron-Token" in f.read_text(encoding="utf-8", errors="ignore"))}
    assert lettori == {"communication/cron_router.py"}, lettori


def _nessun_altra_rotta(client, intestazioni):
    for metodo, percorso, corpo in (
            ("post", "/api/communication/dispatch", {"channel": "email"}),
            ("post", "/api/communication/journeys/tick", {}),
            ("get", "/api/communication/journeys/overview", None),
            ("post", "/api/communication/journeys/followup-v3/provision", None),
            ("post", "/api/communication/journeys/2/activate", None),
            ("get", "/api/communication/contacts/1/messages", None)):
        risposta = getattr(client, metodo)(percorso, headers=intestazioni,
                                           **({"json": corpo} if corpo is not None else {}))
        assert risposta.status_code == 401, (percorso, risposta.status_code)
