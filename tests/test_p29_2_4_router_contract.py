"""P29-2.4 - C19, C20, C21: i tre contratti che si provano senza database.

PERCHE' UN MODULO A PARTE, E PERCHE' SENZA POSTGRESQL

I due test del 422 vivono anche in `tests/test_p29_2_4_dispatch_postgres.py`,
che pero' e' opt-in: senza `P29_TEST_DSN` l'intero modulo viene saltato, quindi
in una regressione ordinaria quei due contratti NON vengono provati. Cio' che
qui si verifica - autenticazione, derivazione dell'agenzia, identita' del
provider, ordine del gate - non ha bisogno di un database, e quindi non deve
dipenderne: gira sempre, in ogni esecuzione della suite.

    C19  la rotta e' sicura anche se domani qualcuno la monta
    C20  l'identita' del provider viene dall'adapter, non da una stringa
    C21  il gate e' ADIACENTE al dispatch: fra ALLOW e send non c'e' nulla

Il criterio di chiusura del design - la revoca che sopprime con il suo
compare-and-set - resta dove puo' essere provato davvero, cioe' su PostgreSQL.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from communication import dispatcher, enums as enums_communication
from communication import router as communication_router, service
from communication.providers import base as provider_base
from communication.providers import null as provider_null
from operator_auth.context import OperatorContext, SystemAgencyContext
from operator_auth.dependencies import legacy_basic_agency_context

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

AGENZIA_A = 11
AGENZIA_B = 22


def operatore(agency_id: int, role: str = "admin") -> OperatorContext:
    return OperatorContext(
        user_id=1, agency_id=agency_id, role=role, is_platform_admin=False,
        session_id=1, auth_channel="session",
    )


def app_autenticata(ctx: OperatorContext) -> FastAPI:
    """L'app autonoma, con la sessione gia' risolta su `ctx`.

    Si sostituisce SOLO la dipendenza di autenticazione, con lo stesso
    meccanismo che FastAPI offre a chiunque monti questo router: cio' che resta
    sotto prova e' il router vero, non una sua imitazione.
    """
    app = FastAPI()
    app.include_router(communication_router.router)
    app.dependency_overrides[legacy_basic_agency_context] = lambda: ctx
    return app


def app_nuda() -> FastAPI:
    """L'app autonoma con la dipendenza VERA: nessuna sessione, nessun cookie."""
    app = FastAPI()
    app.include_router(communication_router.router)
    return app


@pytest.fixture
def tracciato(monkeypatch):
    """Registra l'ordine reale delle chiamate del dispatcher.

    Non e' un mock del dispatcher: il dispatcher gira davvero. Si sostituiscono
    i suoi COLLABORATORI - claim, gate, finalizzazioni, provider - con spie che
    annotano e restituiscono un valore plausibile. Cio' che si osserva e'
    quindi la sequenza che il codice di produzione esegue.
    """
    eventi: list[tuple] = []
    stato = {"reclamati": [], "consenso": None, "ctx": None}

    def finto_claim(ctx, *, provider, limit):
        stato["ctx"] = ctx
        eventi.append(("claim", provider, limit, ctx.agency_id, ctx.origin))
        return stato["reclamati"]

    def finto_gate(ctx, contact_id):
        eventi.append(("gate", contact_id))
        if stato["consenso"] is None:
            raise AssertionError("gate interrogato senza una decisione preparata")
        return stato["consenso"]

    def finalizzatore(nome):
        def finalizza(ctx, message_id, token, **campi):
            eventi.append((nome, message_id, token))
            return {"id": message_id}
        return finalizza

    monkeypatch.setattr(service, "claim_due", finto_claim)
    monkeypatch.setattr(dispatcher, "can_send_marketing", finto_gate)
    for nome in ("finalize_sent", "finalize_failed", "finalize_indeterminate",
                 "finalize_suppressed"):
        monkeypatch.setattr(service, nome, finalizzatore(nome))
    return eventi, stato


class Decisione:
    def __init__(self, allowed: bool, reason: str | None = None):
        self.allowed = allowed
        self.reason = reason


def messaggio(tipo: str, *, id: int = 1) -> dict:
    return {"id": id, "contact_id": 100 + id, "communication_type": tipo,
            "claim_token": f"tok-{id}", "channel": "email"}


def reclamato(tipo: str, *, id: int = 1) -> dict:
    return {"message": messaggio(tipo, id=id)}


# ===========================================================================
# C19  Router: autenticazione e derivazione dell'agenzia
# ===========================================================================

def test_C19_1_la_rotta_richiede_autenticazione():
    """Il pattern del repository: `legacy_basic_agency_context` -> 401.

    Nessun cookie, nessuna sessione, e nessun database interrogato: il rifiuto
    arriva prima. E' lo stesso rifiuto di circa centocinquanta rotte esistenti.
    """
    with TestClient(app_nuda()) as client:
        risposta = client.post("/api/communication/dispatch", json={"limit": 5})
    assert risposta.status_code == 401, risposta.text


def test_C19_1b_la_dipendenza_dichiarata_e_quella_del_repository():
    """Letta dalla firma, non dal comportamento: se domani qualcuno sostituisse
    la dipendenza con una piu' larga, il 401 qui sopra potrebbe continuare a
    passare mentre lo scope non viene piu' dalla sessione."""
    firma = inspect.signature(communication_router.dispatch)
    dipendenza = firma.parameters["ctx"].default.dependency
    assert dipendenza is legacy_basic_agency_context


def test_C19_2_agency_id_nel_corpo_e_un_422():
    with TestClient(app_autenticata(operatore(AGENZIA_A))) as client:
        risposta = client.post("/api/communication/dispatch",
                               json={"limit": 5, "agency_id": AGENZIA_B})
    assert risposta.status_code == 422, risposta.text
    assert "agency_id" in risposta.text


def test_C19_2b_il_corpo_dichiara_un_campo_solo():
    campi = set(communication_router.DispatchRequest.model_fields)
    assert campi == {"limit"}, campi


def test_C19_2c_la_firma_della_rotta_non_prende_ne_query_ne_path(tracciato):
    """`agency_id` non puo' arrivare da nessun canale, non solo dal corpo.

    Un parametro di query o di path si dichiara nella firma della funzione: se
    non c'e', FastAPI non ha da dove leggerlo. E si prova anche mandandolo: la
    query viene ignorata, e l'agenzia resta quella della sessione.
    """
    eventi, _ = tracciato
    parametri = set(inspect.signature(communication_router.dispatch).parameters)
    assert parametri == {"payload", "ctx"}, parametri

    with TestClient(app_autenticata(operatore(AGENZIA_A))) as client:
        risposta = client.post(
            f"/api/communication/dispatch?agency_id={AGENZIA_B}", json={"limit": 5})
    assert risposta.status_code == 200, risposta.text
    assert [e for e in eventi if e[0] == "claim"][0][3] == AGENZIA_A


def test_C19_3_e_4_lagenzia_e_lorigin_vengono_dalla_sessione(tracciato):
    """Payload valido + sessione dell'agenzia A -> si lavora sull'agenzia A, con
    un contesto di sistema e con l'origin esatto del design."""
    eventi, stato = tracciato
    with TestClient(app_autenticata(operatore(AGENZIA_A))) as client:
        risposta = client.post("/api/communication/dispatch", json={"limit": 7})
    assert risposta.status_code == 200, risposta.text

    ctx = stato["ctx"]
    assert isinstance(ctx, SystemAgencyContext)
    assert ctx.agency_id == AGENZIA_A
    assert ctx.origin == "communication_dispatch"
    assert [e for e in eventi if e[0] == "claim"][0][2] == 7, "il limit non e' passato"


def test_C19_5_un_operatore_di_A_non_puo_dispacciare_per_B(tracciato):
    """Nessun canale - corpo, query, header - sposta lo scope. L'unica agenzia
    raggiungibile e' quella della sessione, e la si misura."""
    eventi, _ = tracciato
    with TestClient(app_autenticata(operatore(AGENZIA_A))) as client:
        assert client.post("/api/communication/dispatch",
                           json={"limit": 5, "agency_id": AGENZIA_B}).status_code == 422
        assert client.post(f"/api/communication/dispatch?agency_id={AGENZIA_B}",
                           json={"limit": 5}).status_code == 200
        assert client.post("/api/communication/dispatch", json={"limit": 5},
                           headers={"X-Agency-Id": str(AGENZIA_B)}).status_code == 200

    agenzie = {e[3] for e in eventi if e[0] == "claim"}
    assert agenzie == {AGENZIA_A}, agenzie


def test_C19_6_un_limite_fuori_scala_e_un_422():
    with TestClient(app_autenticata(operatore(AGENZIA_A))) as client:
        assert client.post("/api/communication/dispatch",
                           json={"limit": 0}).status_code == 422
        assert client.post("/api/communication/dispatch",
                           json={"limit": 51}).status_code == 422


def test_C19_7_un_contesto_senza_agenzia_e_un_403(tracciato):
    """Un platform admin senza membership non ha un'agenzia per cui dispacciare,
    e indovinarne una sarebbe il dispatcher cross-tenant che il design vieta."""
    ctx = OperatorContext(user_id=1, agency_id=None, role="platform_admin",
                          is_platform_admin=True, session_id=1, auth_channel="session")
    with TestClient(app_autenticata(ctx)) as client:
        risposta = client.post("/api/communication/dispatch", json={"limit": 5})
    assert risposta.status_code == 403, risposta.text


def test_C19_8_main_py_resta_intatto():
    """La rotta resta DICHIARATA e non montata: `main.py` non la nomina in
    nessuna forma - ne' l'import del modulo, ne' un `include_router`."""
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "communication" not in main_py
    assert "/api/communication" not in main_py


# ===========================================================================
# C20  L'identita' del provider viene dall'adapter
# ===========================================================================

class ProviderFinto:
    """Un adapter completo, con un nome proprio, che annota di essere chiamato."""

    NAME = "provider-di-prova"
    CAPABILITIES = provider_base.ProviderCapabilities(
        returns_message_id=False, distinguishes_failure_class=False,
        reports_delivery=False)

    def __init__(self, outcome: str = provider_base.OUTCOME_ACCEPTED):
        self.outcome = outcome
        self.chiamate: list[dict] = []

    def send(self, message):
        self.chiamate.append(message)
        return provider_base.ProviderResult(outcome=self.outcome)


def test_C20_1_il_nome_reclamato_e_quello_delladapter_invocato(tracciato):
    """Un valore solo, non due. Il nome che finisce nel regular attempt al claim
    e l'oggetto che viene poi invocato sono la stessa cosa: il primo si legge
    DAL secondo."""
    eventi, stato = tracciato
    stato["reclamati"] = [reclamato("service")]
    finto = ProviderFinto()

    dispatcher.dispatch_batch(operatore(AGENZIA_A), provider=finto)

    claim = [e for e in eventi if e[0] == "claim"][0]
    assert claim[1] == ProviderFinto.NAME, "il claim non ha registrato l'adapter reale"
    assert len(finto.chiamate) == 1, "non e' stato invocato quell'oggetto"


def test_C20_2_non_esiste_un_percorso_per_reclamare_A_e_chiamare_B():
    """Letto dal codice, non dal comportamento.

    `dispatch_batch` accetta un ADAPTER, mai una stringa, e la sola espressione
    che raggiunge `claim_due` come provider e' `provider.NAME`. Due valori
    indipendenti che possono divergere non esistono perche' non c'e' dove
    scriverli.
    """
    parametri = inspect.signature(dispatcher.dispatch_batch).parameters
    assert set(parametri) == {"ctx_operatore", "limit", "provider"}
    assert "provider_name" not in parametri and "provider_key" not in parametri

    corpo = inspect.getsource(dispatcher.dispatch_batch)
    assert "provider=provider.NAME" in corpo, (
        "l'identita' del provider non deriva piu' dall'adapter"
    )
    for letterale in ('provider="', "provider='"):
        assert letterale not in corpo, "una stringa provider scritta a mano"


def test_C20_3_il_chiamante_http_non_puo_scegliere_il_provider():
    """La rotta passa `limit` e nient'altro: l'adapter e' il default del
    dispatcher, e nessun campo del corpo lo raggiunge."""
    assert set(communication_router.DispatchRequest.model_fields) == {"limit"}
    corpo = inspect.getsource(communication_router.dispatch)
    assert "provider" not in corpo


def test_C20_4_il_default_e_un_adapter_non_una_stringa():
    default = inspect.signature(dispatcher.dispatch_batch).parameters["provider"].default
    assert default is provider_null
    assert isinstance(default.NAME, str) and default.NAME
    assert callable(default.send)


def test_C20_5_la_finalizzazione_non_ripassa_il_provider():
    """C15 di P29-2.3: il provider dell'esito si deriva dal regular attempt, non
    lo si rimanda. Se il dispatcher potesse ripassarlo, i due valori potrebbero
    divergere - ed e' precisamente cio' che C20 vieta.

    NESSUNA SPIA QUI, DI PROPOSITO: la fixture `tracciato` sostituisce proprio
    queste funzioni, e ispezionare una spia significherebbe leggere la firma
    della spia. Si leggono le funzioni vere.
    """
    import communication.service as modulo_service

    for nome in ("finalize_sent", "finalize_failed", "finalize_indeterminate",
                 "finalize_suppressed"):
        firma = inspect.signature(getattr(modulo_service, nome))
        assert "provider" not in firma.parameters, f"{nome} riceve un provider"

    corpo = inspect.getsource(dispatcher.dispatch_batch)
    assert corpo.count("provider=provider.NAME") == 1, (
        "l'identita' del provider viene passata piu' di una volta: due punti di "
        "passaggio sono due valori che possono divergere"
    )
    assert 'finalizza(ctx, message["id"], token, **campi)' in corpo, (
        "la finalizzazione non passa piu' solo l'esito osservato"
    )


# ===========================================================================
# C21  Il gate e' adiacente al dispatch
# ===========================================================================

def test_C21_1_marketing_allow_gate_poi_provider(tracciato):
    """L'ordine, misurato: claim, gate, provider. Fra il gate e il provider non
    c'e' nessun altro evento."""
    eventi, stato = tracciato
    stato["reclamati"] = [reclamato("marketing")]
    stato["consenso"] = Decisione(True)
    finto = ProviderFinto()

    dispatcher.dispatch_batch(operatore(AGENZIA_A), provider=finto)

    nomi = [e[0] for e in eventi]
    assert nomi == ["claim", "gate", "finalize_sent"], nomi
    assert len(finto.chiamate) == 1
    # Fra ALLOW e send: nessuna decisione, nessun UPDATE, nessuna lettura.
    assert nomi.index("gate") + 1 == nomi.index("finalize_sent"), (
        "qualcosa si e' infilato fra il gate e il provider"
    )


def test_C21_2_marketing_deny_gate_poi_soppressione_e_nessun_provider(tracciato):
    eventi, stato = tracciato
    stato["reclamati"] = [reclamato("marketing")]
    stato["consenso"] = Decisione(False, reason="deny_revoked")
    finto = ProviderFinto()

    conteggi = dispatcher.dispatch_batch(operatore(AGENZIA_A), provider=finto)

    assert [e[0] for e in eventi] == ["claim", "gate", "finalize_suppressed"]
    assert finto.chiamate == [], "il provider e' stato chiamato su una soppressione"
    assert conteggi["suppressed"] == 1 and conteggi["sent"] == 0


def test_C21_3_il_servizio_non_passa_dal_gate(tracciato):
    eventi, stato = tracciato
    stato["reclamati"] = [reclamato("service")]
    finto = ProviderFinto()

    dispatcher.dispatch_batch(operatore(AGENZIA_A), provider=finto)

    assert [e[0] for e in eventi] == ["claim", "finalize_sent"]
    assert "gate" not in [e[0] for e in eventi], "il servizio ha interrogato il consenso"
    assert len(finto.chiamate) == 1


def test_C21_4_il_gate_e_interrogato_a_ogni_giro_mai_memorizzato(tracciato):
    """Due giri sullo stesso messaggio: due interrogazioni. Una decisione presa
    all'accodamento - o messa in cache - sarebbe una decisione su ieri."""
    eventi, stato = tracciato
    stato["reclamati"] = [reclamato("marketing")]
    stato["consenso"] = Decisione(True)
    dispatcher.dispatch_batch(operatore(AGENZIA_A), provider=ProviderFinto())

    stato["consenso"] = Decisione(False, reason="deny_revoked")
    dispatcher.dispatch_batch(operatore(AGENZIA_A), provider=ProviderFinto())

    assert [e[0] for e in eventi].count("gate") == 2
    assert [e[0] for e in eventi][-1] == "finalize_suppressed", (
        "la seconda decisione non ha avuto effetto: il gate e' memorizzato"
    )


def test_C21_5_il_gate_e_per_messaggio_non_per_batch(tracciato):
    """Tre messaggi marketing: tre interrogazioni, non una."""
    eventi, stato = tracciato
    stato["reclamati"] = [reclamato("marketing", id=i) for i in (1, 2, 3)]
    stato["consenso"] = Decisione(True)

    dispatcher.dispatch_batch(operatore(AGENZIA_A), provider=ProviderFinto())

    contatti = [e[1] for e in eventi if e[0] == "gate"]
    assert contatti == [101, 102, 103], contatti


def test_C21_6_il_gate_e_dopo_il_claim_non_prima(tracciato):
    eventi, stato = tracciato
    stato["reclamati"] = [reclamato("marketing")]
    stato["consenso"] = Decisione(True)
    dispatcher.dispatch_batch(operatore(AGENZIA_A), provider=ProviderFinto())
    nomi = [e[0] for e in eventi]
    assert nomi.index("claim") < nomi.index("gate")


# ===========================================================================
# C22  A + B: contratto dell'adapter, e rete di sicurezza del batch
# ===========================================================================

class ProviderCheSolleva(ProviderFinto):
    """Un adapter che VIOLA il contratto del ramo A, di proposito.

    Solleva su un `message_id` scelto e si comporta normalmente sugli altri:
    e' cosi' che si misura l'isolamento, che per definizione riguarda cio' che
    succede ai messaggi DOPO quello guasto.
    """

    def __init__(self, guasto_su: int, eccezione: BaseException | None = None):
        super().__init__()
        self.guasto_su = guasto_su
        self.eccezione = eccezione or ConnectionResetError("la connessione e' caduta")

    def send(self, message):
        self.chiamate.append(message)
        if message["id"] == self.guasto_su:
            raise self.eccezione
        return provider_base.ProviderResult(outcome=provider_base.OUTCOME_ACCEPTED)


def test_C22_A_il_contratto_e_dichiarato_e_il_provider_finto_lo_rispetta():
    """Ramo A. Il contratto vive nel modulo che definisce il tipo dell'esito, e
    il provider di questa fase lo rispetta: non solleva, e ritorna il tipo."""
    contratto = provider_base.__doc__
    assert "send(message) -> ProviderResult" in contratto
    assert "RAMO A" in contratto

    risultato = provider_null.send({"id": 1, "channel": "email"})
    assert isinstance(risultato, provider_base.ProviderResult)
    assert risultato.outcome == provider_base.OUTCOME_ACCEPTED


def test_C22_B1_una_eccezione_non_ferma_il_batch(tracciato):
    """Il test centrale di C22: #1 solleva, #2 arriva comunque a `sent`."""
    eventi, stato = tracciato
    stato["reclamati"] = [reclamato("service", id=1), reclamato("service", id=2)]
    finto = ProviderCheSolleva(guasto_su=1)

    conteggi = dispatcher.dispatch_batch(operatore(AGENZIA_A), provider=finto)

    assert [m["id"] for m in finto.chiamate] == [1, 2], (
        "il secondo messaggio non e' stato nemmeno provato"
    )
    assert [e[0] for e in eventi] == ["claim", "finalize_indeterminate",
                                      "finalize_sent"]
    assert conteggi == {"claimed": 2, "sent": 1, "suppressed": 0, "failed": 0,
                        "indeterminate": 1, "lost": 0}


def test_C22_B2_lo_stato_e_indeterminate_mai_failed_mai_sent():
    """Sulla traduzione, letta direttamente: `indeterminate`, e un codice gia'
    ammesso dal CHECK della 064. Nessun `sent`, nessun `failed`."""
    stato, campi = dispatcher._esito_di_una_eccezione(ConnectionResetError("giu'"))
    assert stato == "indeterminate"
    assert campi["error_code"] == enums_communication.ERROR_OUTCOME_UNKNOWN
    assert campi["error_code"] in enums_communication.ERROR_CODES
    assert "provider_message_id" not in campi, "un esito ignoto non porta un id"


def test_C22_B3_error_detail_porta_la_classe_non_il_messaggio():
    """Il testo di un'eccezione di rete contiene spesso il destinatario: il
    ledger non e' il posto dove duplicarlo. Nel log ci va tutto."""
    _, campi = dispatcher._esito_di_una_eccezione(
        ConnectionResetError("smtp: mario.rossi@example.it rifiutato"))
    assert campi["error_detail"] == "builtins.ConnectionResetError"
    assert "example.it" not in campi["error_detail"]


def test_C22_B4_la_finalizzazione_passa_dalla_api_fenced_di_p29_2_3(tracciato):
    """Nessuna scrittura diretta: si chiama `finalize_indeterminate` con il
    token del claim, cioe' il compare-and-set di P29-2.3."""
    eventi, stato = tracciato
    stato["reclamati"] = [reclamato("service", id=1)]

    dispatcher.dispatch_batch(operatore(AGENZIA_A),
                              provider=ProviderCheSolleva(guasto_su=1))

    finalizzazioni = [e for e in eventi if e[0].startswith("finalize")]
    assert len(finalizzazioni) == 1
    nome, message_id, token = finalizzazioni[0]
    assert nome == "finalize_indeterminate"
    assert message_id == 1 and token == "tok-1", "il token del claim non e' stato usato"


def test_C22_B5_non_si_cattura_BaseException(tracciato):
    """Un KeyboardInterrupt o un SystemExit devono continuare a fermare il
    processo: la barriera isola il batch dai guasti, non l'operatore dal suo
    Ctrl-C."""
    _, stato = tracciato
    stato["reclamati"] = [reclamato("service", id=1), reclamato("service", id=2)]
    finto = ProviderCheSolleva(guasto_su=1, eccezione=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        dispatcher.dispatch_batch(operatore(AGENZIA_A), provider=finto)

    corpo = inspect.getsource(dispatcher.dispatch_batch)
    assert "except Exception" in corpo and "except BaseException" not in corpo


def test_C22_B6_il_try_avvolge_solo_la_chiamata_al_provider():
    """Una barriera, non un silenziatore: un'eccezione del NOSTRO codice di
    finalizzazione non deve essere scambiata per un guasto del trasporto."""
    corpo = inspect.getsource(dispatcher.dispatch_batch)
    dentro = corpo.split("try:")[1].split("except Exception")[0]
    assert "provider.send(message)" in dentro
    for vietato in ("finalizza(", "finalize_", "claim_due", "_consenso_nega"):
        assert vietato not in dentro, f"{vietato} sta dentro il try"


def test_C22_B7_nessun_retry_e_nessun_tentativo_in_piu(tracciato):
    """Il ramo dell'eccezione finalizza UNA volta e passa oltre: non richiama il
    provider, non riaccoda, non apre un secondo tentativo."""
    eventi, stato = tracciato
    stato["reclamati"] = [reclamato("service", id=1)]
    finto = ProviderCheSolleva(guasto_su=1)

    dispatcher.dispatch_batch(operatore(AGENZIA_A), provider=finto)

    assert len(finto.chiamate) == 1, "il provider e' stato richiamato: e' un retry"
    assert [e[0] for e in eventi].count("finalize_indeterminate") == 1
    corpo = inspect.getsource(dispatcher.dispatch_batch)
    for vietato in ("retry", "riaccoda", "requeue", "while "):
        assert vietato not in corpo


def test_C22_B8_unknown_ed_eccezione_condividono_la_finalizzazione(tracciato):
    """Requisito 9: nessuna logica di finalizzazione duplicata.

    Un `ProviderResult(unknown)` e un'eccezione arrivano allo stesso stato per
    la stessa strada - la tabella `finalizza` - e differiscono solo nel codice
    d'errore, che e' esattamente l'informazione che li distingue.
    """
    eventi, stato = tracciato

    stato["reclamati"] = [reclamato("service", id=1)]
    dispatcher.dispatch_batch(
        operatore(AGENZIA_A),
        provider=ProviderFinto(outcome=provider_base.OUTCOME_UNKNOWN))
    da_unknown = [e[0] for e in eventi if e[0].startswith("finalize")]

    eventi.clear()
    stato["reclamati"] = [reclamato("service", id=1)]
    dispatcher.dispatch_batch(operatore(AGENZIA_A),
                              provider=ProviderCheSolleva(guasto_su=1))
    da_eccezione = [e[0] for e in eventi if e[0].startswith("finalize")]

    assert da_unknown == da_eccezione == ["finalize_indeterminate"]
    corpo = inspect.getsource(dispatcher.dispatch_batch)
    assert corpo.count('finalizza(ctx, message["id"], token, **campi)') == 1, (
        "esiste piu' di un punto di finalizzazione"
    )


def test_C22_B9_leccezione_viene_loggata_con_il_contesto(tracciato, caplog):
    """Non si ingoia senza traccia: stack trace, message_id, agency_id e
    provider, che sono le quattro cose che servono per indagare."""
    import logging

    _, stato = tracciato
    stato["reclamati"] = [reclamato("service", id=7)]

    with caplog.at_level(logging.ERROR, logger="communication.dispatcher"):
        dispatcher.dispatch_batch(operatore(AGENZIA_A),
                                  provider=ProviderCheSolleva(guasto_su=7))

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.exc_info is not None, "nessuno stack trace"
    assert "ConnectionResetError" in caplog.text
    for atteso in ("7", str(AGENZIA_A), ProviderFinto.NAME):
        assert atteso in record.getMessage(), atteso


def test_C22_B10_una_eccezione_non_diventa_mai_un_successo(tracciato):
    eventi, stato = tracciato
    stato["reclamati"] = [reclamato("service", id=1)]

    conteggi = dispatcher.dispatch_batch(operatore(AGENZIA_A),
                                         provider=ProviderCheSolleva(guasto_su=1))

    assert conteggi["sent"] == 0 and conteggi["failed"] == 0
    assert conteggi["indeterminate"] == 1
    assert "finalize_sent" not in [e[0] for e in eventi]
