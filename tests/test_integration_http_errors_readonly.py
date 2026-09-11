import base64
import secrets

import pytest
import requests
from integration_p2_support import require_test_environment

# `os` e `HTTPBasicAuth` erano importati soltanto per leggere ADMIN_USER /
# ADMIN_PASS e costruire l'header Basic. Dopo P26-5 nessun test di questo file
# presenta una credenziale: l'unico header Basic che resta e' un valore
# inventato, usato per provare che non apre nulla.

SQL_DETAIL_MARKERS=("psycopg","postgres","sqlstate","duplicate key","syntax error","traceback")

@pytest.fixture(scope="module")
def base():
    return require_test_environment(require_http=True).backend

def assert_no_server_or_sql_leak(response):
    assert response.status_code not in {500,502}
    body=response.text.lower()
    assert not any(marker in body for marker in SQL_DETAIL_MARKERS)

@pytest.mark.parametrize("path",[
    "/api/owner/portal/properties/999999999",
    "/api/owner/portal/publications/999999999",
    "/api/flow/rules/FLOW-NOT-EXISTING",
])
def test_nonexistent_never_500_or_sql_leak(base,path):
    response=requests.get(base+path,timeout=20)
    assert_no_server_or_sql_leak(response)

def test_formally_invalid_owner_token_payload(base):
    response=requests.post(base+"/api/owner/portal/auth/token",json={"token":"too-short"},timeout=20)
    assert response.status_code in {400,422}
    assert_no_server_or_sql_leak(response)

def test_formally_valid_but_nonexistent_owner_token(base):
    # Token URL-safe, 48 byte di entropia, lunghezza compatibile con min=32/max=512.
    token=secrets.token_urlsafe(48)
    assert 32 <= len(token) <= 512
    response=requests.post(base+"/api/owner/portal/auth/token",json={"token":token},timeout=20)
    assert response.status_code==404
    assert_no_server_or_sql_leak(response)

def test_owner_admin_write_is_refused_before_the_payload_is_examined(base):
    """La scrittura su OWNER Admin senza sessione e' rifiutata al confine.

    COSA CONTROLLAVA PRIMA, E PERCHE' NON POTEVA PIU' FUNZIONARE

    Questo test spediva HTTP Basic (ADMIN_USER/ADMIN_PASS) con un corpo vuoto e
    pretendeva 422. Dopo P26-5 quella superficie sta dietro
    `require_owner_admin_context`, che e' una dipendenza di router: FastAPI la
    risolve PRIMA di convalidare il corpo, quindi la richiesta riceve 401 e la
    validazione non viene mai raggiunta. Nessun 422 poteva piu' arrivare.

    COSA CONTROLLA ADESSO - E, SOPRATTUTTO, COSA NON PUO' CONTROLLARE

    Controlla che sull'app REALMENTE DISTRIBUITA quella scrittura esiga
    un'autenticazione che questa richiesta non porta, e che presentare un
    header Basic non cambi nulla di osservabile.

    NON dimostra che HTTP Basic sia morto. La credenziale qui e' fittizia, e un
    server che onorasse ancora Basic risponderebbe 401 a una credenziale
    sbagliata esattamente come fa questo: dal 401 le due ipotesi sono
    indistinguibili. Distinguerle richiederebbe una credenziale VALIDA, che
    questo test deliberatamente non possiede e non deve possedere.

    Che il canale sia morto e' provato altrove, e staticamente:
    tests/test_p26_5_basic_containment.py interroga l'OpenAPI dell'app servita
    (un solo schema, il cookie di sessione), scruta l'AST del modulo di
    autenticazione (nessuna istanza viva di HTTPBasic) e quello di main.py
    (una sola funzione legge ADMIN_USER/ADMIN_PASS, ed e' /api/admin/check).

    Il valore di questa prova e' complementare e non sostituibile: le prove
    statiche leggono il sorgente, questa interroga il deploy. Il codice puo'
    essere giusto e il servizio in esecuzione essere un altro.

    DOVE SONO FINITE LE ALTRE DUE PROPRIETA'

    * "payload invalido su OWNER Admin -> 422, senza fuga SQL" e' provato da
      tests/test_p26_5_basic_containment.py::test_11c, che raggiunge davvero la
      validazione con una sessione `agency_owner`. NON e' coperto da
      `test_formally_invalid_owner_token_payload` qui sopra: quella e' un'altra
      route, con un altro schema e un altro principale.
    * "la richiesta rifiutata non scrive" e' provato da ::test_11b, che
      sostituisce il repository e verifica che non venga mai invocato - con un
      payload valido, cosi' che a fermare la scrittura sia l'autenticazione e
      non la validazione. Qui, via HTTP, si potrebbe solo presumerlo.

    Nessuna credenziale di runtime viene letta, costruita o stampata.
    """
    fictitious = base64.b64encode(b"utente-fittizio:password-fittizia").decode()
    headers = {"Authorization": "Basic " + fictitious}
    with_basic = requests.post(base + "/api/owner/admin/accounts", json={},
                               headers=headers, timeout=20)
    anonymous = requests.post(base + "/api/owner/admin/accounts", json={}, timeout=20)

    assert with_basic.status_code == 401
    assert anonymous.status_code == 401
    assert with_basic.status_code == anonymous.status_code, (
        "presentare HTTP Basic cambia la risposta: qualcosa lo legge ancora"
    )
    # Un 'WWW-Authenticate: Basic' inviterebbe un client a ritentare con le
    # credenziali condivise, e direbbe che quel canale e' ancora previsto.
    assert "basic" not in with_basic.headers.get("WWW-Authenticate", "").lower()

    assert_no_server_or_sql_leak(with_basic)
    assert_no_server_or_sql_leak(anonymous)
