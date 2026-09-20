"""P29-2.5E - l'adapter email reale, e il confine che tiene WhatsApp fuori.

NESSUNA EMAIL PARTE DA QUI

`database.invia_mail` viene sempre sostituita con una spia: nessun test di
questo modulo apre una connessione SMTP, legge una configurazione SMTP reale o
manda un messaggio a chicchessia. E' la stessa scelta del provider finto di
P29-2.4, per la stessa ragione: una prova del trasporto che manda davvero e'
una prova che costa una email a una persona vera.

Mappa:

    A   l'adapter come provider: protocollo, identita', capability
    B   la mappatura degli esiti - e perche' `False` non e' `rejected`
    C   `provider_message_id`: mai sintetico
    D   il contratto C22 ramo A, e la rete di sicurezza ramo B
    W   WhatsApp e' fuori scope, e R3 resta OPEN
    N   cio' che P29-2.5E non tocca
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from communication import dispatcher
from communication.providers import base as provider_base
from communication.providers import email_smtp
from communication.providers import null as provider_null

ROOT = Path(__file__).resolve().parents[1]
PACCHETTO = ROOT / "communication"


def codice(percorso: Path) -> str:
    testo = percorso.read_text(encoding="utf-8")
    testo = re.sub(r'"{3}[\s\S]*?"{3}', "", testo)
    return re.sub(r"#[^\n]*", "", testo)


def messaggio(**extra):
    base = {
        "id": 1,
        "destination_snapshot": "cliente@example.it",
        "subject_snapshot": "La tua stima",
        "rendered_body": "<p>ecco la stima</p>",
    }
    base.update(extra)
    return base


@pytest.fixture
def spia(monkeypatch):
    """`invia_mail` sostituita: registra gli argomenti e restituisce cio' che
    le si dice di restituire, o solleva."""
    chiamate: list[tuple] = []
    comportamento = {"ritorna": True, "solleva": None}

    def finta_invia_mail(destinatario, oggetto, corpo_html, allegato=None):
        chiamate.append((destinatario, oggetto, corpo_html, allegato))
        if comportamento["solleva"] is not None:
            raise comportamento["solleva"]
        return comportamento["ritorna"]

    monkeypatch.setattr(email_smtp, "invia_mail", finta_invia_mail)
    return chiamate, comportamento


# ---------------------------------------------------------------------------
# A  L'adapter come provider
# ---------------------------------------------------------------------------

def test_A1_implementa_il_protocollo_del_provider():
    """La stessa forma del provider finto: un nome, delle capability, un
    `send`. E' cio' che permette al dispatcher di non sapere chi ha davanti."""
    for attributo in ("NAME", "CAPABILITIES", "send"):
        assert hasattr(email_smtp, attributo), attributo
    assert isinstance(email_smtp.NAME, str) and email_smtp.NAME
    assert isinstance(email_smtp.CAPABILITIES, provider_base.ProviderCapabilities)
    assert callable(email_smtp.send)

    forma_finta = set(inspect.signature(provider_null.send).parameters)
    assert set(inspect.signature(email_smtp.send).parameters) == forma_finta


def test_A2_lidentita_e_coerente_col_claim(spia):
    """C20: il nome che finisce nel tentativo si legge DALL'adapter. Qui si
    verifica che l'adapter ne abbia uno proprio e che il dispatcher lo prenda
    da li'."""
    assert email_smtp.NAME == "email_smtp"
    assert email_smtp.NAME != provider_null.NAME

    corpo = inspect.getsource(dispatcher.dispatch_batch)
    assert "provider=provider.NAME" in corpo


def test_A3_le_capability_dicono_quello_che_invia_mail_non_sa(spia):
    """§20.1. Tutte e tre False, e ognuna per una ragione misurabile su
    `database.invia_mail`: ritorna `True`/`False`, nessun id, nessuna consegna."""
    c = email_smtp.CAPABILITIES
    assert c.returns_message_id is False
    assert c.distinguishes_failure_class is False
    assert c.reports_delivery is False


def test_A4_manda_destinazione_oggetto_e_corpo_del_messaggio(spia):
    chiamate, _ = spia
    email_smtp.send(messaggio())
    assert chiamate == [("cliente@example.it", "La tua stima",
                         "<p>ecco la stima</p>", None)]


def test_A5_non_tocca_il_database_e_non_conosce_il_dominio():
    """Terzo confine del design: prende una destinazione e un corpo."""
    corpo = codice(PACCHETTO / "providers" / "email_smtp.py")
    for vietato in ("cur.execute", "communication_cursor", "get_connection",
                    "SELECT", "INSERT", "UPDATE", "agency_id", "ctx",
                    "can_send_marketing", "contact_id"):
        assert vietato not in corpo, f"l'adapter nomina {vietato}"


# ---------------------------------------------------------------------------
# B  La mappatura degli esiti
# ---------------------------------------------------------------------------

def test_B1_successo_produce_un_risultato_accettato(spia):
    _, comportamento = spia
    comportamento["ritorna"] = True

    risultato = email_smtp.send(messaggio())

    assert isinstance(risultato, provider_base.ProviderResult)
    assert risultato.outcome == provider_base.OUTCOME_ACCEPTED
    assert risultato.error_code is None and risultato.error_detail is None


def test_B2_un_insuccesso_non_e_un_rifiuto_certo(spia):
    """`invia_mail` che dice `False` non sta dimostrando un rifiuto: sta dicendo
    l'unica cosa che sa dire. `rejected` sarebbe una certezza inventata."""
    _, comportamento = spia
    comportamento["ritorna"] = False

    risultato = email_smtp.send(messaggio())

    assert risultato.outcome == provider_base.OUTCOME_UNKNOWN
    assert risultato.outcome != provider_base.OUTCOME_REJECTED
    assert risultato.error_detail == email_smtp.DETTAGLIO_RIFIUTO


def test_B3_ladapter_non_produce_mai_rejected():
    """Letto dal codice: `rejected` non compare, e non per dimenticanza - la
    capability dice che questo trasporto non sa distinguere."""
    corpo = codice(PACCHETTO / "providers" / "email_smtp.py")
    assert "OUTCOME_REJECTED" not in corpo
    assert "rejected" not in corpo


def test_B4_un_esito_ambiguo_diventa_indeterminate_mai_failed(spia):
    """Il punto 6 dei requisiti, misurato end-to-end sulla traduzione del
    dispatcher: con `distinguishes_failure_class=False` non esiste un ingresso
    che produca `failed`."""
    _, comportamento = spia

    comportamento["ritorna"] = False
    stato, campi = dispatcher._esito_del_provider(
        email_smtp.send(messaggio()), email_smtp.CAPABILITIES)
    assert stato == "indeterminate"
    assert campi["error_code"] == "unknown"

    comportamento["solleva"] = TimeoutError("il server non risponde")
    stato, _ = dispatcher._esito_del_provider(
        email_smtp.send(messaggio()), email_smtp.CAPABILITIES)
    assert stato == "indeterminate"


def test_B5_nessun_esito_di_questo_adapter_puo_produrre_failed(spia):
    """Esaustivo sui tre esiti possibili del tipo, non solo sui due che
    l'adapter produce: finche' la capability e' False, `failed` e'
    irraggiungibile."""
    for outcome in (provider_base.OUTCOME_ACCEPTED, provider_base.OUTCOME_REJECTED,
                    provider_base.OUTCOME_UNKNOWN):
        risultato = provider_base.ProviderResult(outcome=outcome)
        stato, _ = dispatcher._esito_del_provider(risultato, email_smtp.CAPABILITIES)
        assert stato != "failed", outcome


# ---------------------------------------------------------------------------
# C  provider_message_id
# ---------------------------------------------------------------------------

def test_C1_nessun_id_viene_inventato(spia):
    _, comportamento = spia
    comportamento["ritorna"] = True
    assert email_smtp.send(messaggio()).provider_message_id is None

    comportamento["ritorna"] = False
    assert email_smtp.send(messaggio()).provider_message_id is None


def test_C2_lid_non_e_costruito_da_nessuna_parte():
    """Non basta che oggi sia None: non deve esistere il materiale per
    fabbricarlo - nessun uuid, nessun timestamp, nessuna concatenazione."""
    corpo = codice(PACCHETTO / "providers" / "email_smtp.py")
    for fabbrica in ("uuid", "token_hex", "sha", "md5", "time()", "now()",
                     "provider_message_id="):
        assert fabbrica not in corpo, f"l'adapter costruisce un id con {fabbrica}"


def test_C3_il_tipo_rifiuterebbe_comunque_un_id_su_un_esito_non_accettato():
    with pytest.raises(ValueError):
        provider_base.ProviderResult(outcome=provider_base.OUTCOME_UNKNOWN,
                                     provider_message_id="smtp-123")


# ---------------------------------------------------------------------------
# D  C22: ramo A nell'adapter, ramo B nel dispatcher
# ---------------------------------------------------------------------------

def test_D1_una_eccezione_operativa_e_normalizzata_dalladapter(spia):
    """Ramo A. L'eccezione non esce da `send`: diventa un esito."""
    _, comportamento = spia
    comportamento["solleva"] = ConnectionResetError("connessione caduta")

    risultato = email_smtp.send(messaggio())

    assert risultato.outcome == provider_base.OUTCOME_UNKNOWN
    assert risultato.error_detail == "builtins.ConnectionResetError"


def test_D2_error_detail_non_porta_il_destinatario(spia):
    """Il testo di un'eccezione SMTP contiene spesso l'indirizzo: il ledger non
    e' il posto dove duplicarlo."""
    _, comportamento = spia
    comportamento["solleva"] = ValueError("550 cliente@example.it unknown user")

    risultato = email_smtp.send(messaggio())

    assert "example.it" not in (risultato.error_detail or "")
    assert risultato.error_detail == "builtins.ValueError"


def test_D3_un_errore_inatteso_resta_protetto_dal_dispatcher(spia):
    """Ramo B. Se un giorno l'adapter lasciasse uscire qualcosa, il batch e'
    comunque isolato: e' la rete di sicurezza approvata in C22, e qui si
    verifica che sia ancora quella."""
    stato, campi = dispatcher._esito_di_una_eccezione(RuntimeError("imprevisto"))
    assert stato == "indeterminate"
    assert campi["error_code"] == "outcome_unknown"

    corpo = inspect.getsource(dispatcher.dispatch_batch)
    assert "except Exception" in corpo and "except BaseException" not in corpo


def test_D4_ladapter_non_ritenta(spia):
    """Una chiamata a `invia_mail` per una chiamata a `send`. Il retry e'
    P29-2.7 e appartiene al dominio, non al trasporto."""
    chiamate, comportamento = spia
    comportamento["ritorna"] = False

    email_smtp.send(messaggio())

    assert len(chiamate) == 1
    corpo = codice(PACCHETTO / "providers" / "email_smtp.py")
    for vietato in ("for ", "while ", "retry", "sleep"):
        assert vietato not in corpo, f"l'adapter contiene {vietato}"


# ---------------------------------------------------------------------------
# W  WhatsApp e' fuori scope, e R3 resta OPEN
# ---------------------------------------------------------------------------

def test_W1_nessun_adapter_whatsapp_esiste():
    assert not (PACCHETTO / "providers" / "whatsapp_meta.py").exists(), (
        "whatsapp_meta.py e' P29-2.5W: R3 e' OPEN"
    )


def test_W2_nessun_riferimento_whatsapp_nel_dominio():
    """Le quattro chiavi dell'audit, piu' i nomi delle funzioni legacy. Nessuna
    deve comparire in `communication/`, nemmeno in un commento: e' la sentinella
    che impedisce a P29-2.5E di far entrare WhatsApp dalla porta di servizio."""
    for percorso in sorted(PACCHETTO.rglob("*.py")):
        if "__pycache__" in percorso.parts:
            continue
        testo = percorso.read_text(encoding="utf-8")
        for chiave in ("WHATSAPP_SERVICE_URL", "WHATSAPP_PHONE_ID",
                       "WHATSAPP_TOKEN", "whatsapp_meta", "invia_whatsapp",
                       "graph.facebook", "onrender.com"):
            assert chiave not in testo, (
                f"{percorso.name} nomina {chiave}: WhatsApp e' P29-2.5W, deferita"
            )


def test_W3_ladapter_email_non_legge_nessuna_variabile_whatsapp():
    """Sul CODICE, non sulla prosa: la docstring dell'adapter spiega che
    WhatsApp e' fuori scope, e nominarlo per dire che non c'e' e' esattamente
    cio' che una ricerca ingenua scambierebbe per la cosa vietata. W2 qui sopra
    sorveglia il testo grezzo sulle chiavi esatte; questo sorveglia il codice."""
    corpo = codice(PACCHETTO / "providers" / "email_smtp.py")
    assert "WHATSAPP" not in corpo.upper()
    assert "os.getenv" not in corpo, (
        "l'adapter non legge configurazione: la legge `invia_mail`"
    )


def test_W4_r3_resta_open_nel_design():
    """Il design e' la source of truth, e deve dire che R3 e' OPEN. Se qualcuno
    lo dichiarasse PASS senza aver separato TEST e PROD, e' qui che si rompe."""
    design = ROOT / "P29_2_0_COMMUNICATION_DESIGN.md"
    if not design.exists():
        pytest.skip("il design vive fuori dal repository tracciato")
    testo = design.read_text(encoding="utf-8")
    assert "**HARD GATE, OPEN.**" in testo, "R3 non risulta piu' OPEN"
    assert "Blocca P29-2.5W" in testo
    assert "P29-2.5W" in testo and "DEFER" in testo


# ---------------------------------------------------------------------------
# N  Cio' che P29-2.5E non tocca
# ---------------------------------------------------------------------------

def test_N1_invia_mail_e_invariata():
    """Il criterio di chiusura: l'adapter AVVOLGE. La firma e il corpo restano
    esattamente quelli, e si verifica anche che la funzione ritorni ancora
    `True`/`False` e non sollevi - cioe' che le capability dicano il vero."""
    database_py = (ROOT / "database.py").read_text(encoding="utf-8")
    assert "def invia_mail(destinatario, oggetto, corpo_html, allegato=None):" in database_py
    assert 'print("❌ SMTP non configurato!")' in database_py
    assert "return False" in database_py and "return True" in database_py


def test_N2_di_main_py_il_dominio_conosce_solo_laccodamento():
    """Cio' che `main.py` puo' fare del dominio, e cio' che non puo'.

    Fino al cutover P29 questa sentinella diceva "`main.py` non e' stato
    toccato". Il cutover lo tocca - e' cio' che un cutover E' - quindi la
    sentinella dice adesso la cosa piu' stretta che resti vera: l'endpoint monta
    la rotta e ACCODA, e non conosce nient'altro. Non un provider, non il
    dispatcher, non una finalizzazione. Un produttore che potesse anche spedire
    riporterebbe dentro la richiesta la latenza e i guasti che il ledger esiste
    per portare fuori.

    Il giorno in cui questa asserzione dovesse cambiare di nuovo, dovra'
    cambiare deliberatamente - come e' cambiata oggi.
    """
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "from communication.router import router as communication_router" in main_py
    assert "from communication import service as communication_service" in main_py
    for vietato in ("communication.dispatcher", "email_smtp", "dispatch_batch",
                    "communication.providers", "finalize_sent", "invia_mail(data["):
        assert vietato not in main_py, f"main.py nomina {vietato}"
    assert "def invia_whatsapp(numero: str | None, p1: str, p2: str, p3: str):" in main_py


def test_N3_il_flusso_cliente_e_migrato_e_gli_altri_due_no():
    """IL test del cutover, letto come testo.

    Tre invii partivano da `/api/salva_stima`: la mail al cliente, l'alert
    amministratore, il WhatsApp. Il cutover ne migra UNO. Gli altri due devono
    restare dove erano, e questa e' la riga che lo pretende: se un giorno
    qualcuno migrasse anche l'alert "per coerenza", o toccasse il WhatsApp, lo
    si scoprirebbe qui e non in produzione.
    """
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")

    # MIGRATO: la mail al cliente si accoda, e non c'e' piu' nessun invio
    # diretto che la riguardi.
    assert "communication_service.enqueue(" in main_py
    assert 'invia_mail(data["email"]' not in main_py
    assert "mail_sent" not in main_py, (
        "il bool dell'invio diretto al cliente non esiste piu': non c'e' invio")

    # NON MIGRATI, letterali: l'alert amministratore e il WhatsApp.
    assert "invia_mail(admin_email, oggetto_admin, corpo_admin)" in main_py
    assert "invia_whatsapp(" in main_py

    # E il trasporto resta dietro l'adapter: `main.py` non lo nomina.
    assert "email_smtp" not in main_py


def test_N4_nessuna_migration_nuova():
    numeri = sorted(
        int(p.name[:3]) for p in (ROOT / "migrations").glob("*.sql")
        if not p.name.endswith("_down.sql") and p.name[:3].isdigit())
    # P29-2.5E non ha introdotto migration: la 065 e' di P29-2.6E, la fase
    # successiva, e riguarda il genitore di lifecycle delle SERVICE senza
    # contatto - non il trasporto email.
    # LMC-10 (collisione autorizzata): la 068 e' la tabella degli
    # override del proprietario, approvata dallo STORAGE GATE di quella
    # fase. Non appartiene a questa, ed e' proprio cio' che la
    # sentinella continua a dire: la coda della serie e' nominata una
    # per una, quindi una migration nata QUI farebbe ancora fallire.
    # LMC-12 ha aggiunto la 069 (`owner_home_notifications`, lo stream di
    # notifiche PRE-INCARICO, approvata dal DESIGN GATE): dominio OWNER, non
    # di questa fase. La coda si nomina, come sempre.
    # LMC-15 ha aggiunto la 070 (`stima_acquisitions` e
    # `stima_inspections`, il ponte di acquisizione approvato dallo SCHEMA
    # GATE di LMC-15A.2): dominio ACQUISITION, non di questa fase. La coda
    # si nomina, come sempre.
    assert numeri[-1] == 70, "la serie si e' fermata o e' andata oltre la 070"
    assert 64 in numeri


def test_N5_nessun_template_nessuno_scheduler_nessuna_ui():
    assert not (PACCHETTO / "templates.py").exists()
    for percorso in sorted(PACCHETTO.rglob("*.py")):
        if "__pycache__" in percorso.parts:
            continue
        corpo = codice(percorso)
        for vietato in ("APScheduler", "BackgroundScheduler", "Celery",
                        "crontab", "schedule.every", "M1_", "M2_", "M3_"):
            assert vietato not in corpo, f"{percorso.name} nomina {vietato}"


def test_N6_i_provider_restano_importabili_solo_dal_dispatcher():
    """S1 di P29-2.4, riverificata adesso che i provider sono due: un adapter
    reale importabile da un service sarebbe un invio senza gate del consenso."""
    for percorso in sorted(PACCHETTO.rglob("*.py")):
        if "__pycache__" in percorso.parts or percorso.parent.name == "providers":
            continue
        if percorso.name == "dispatcher.py":
            continue
        corpo = codice(percorso)
        assert "providers" not in corpo, f"{percorso.name} importa i provider"


# ---------------------------------------------------------------------------
# C23  Il contratto REALE di `database.invia_mail`, letto dalla funzione vera
# ---------------------------------------------------------------------------
#
# I test qui sopra sostituiscono `invia_mail` con una spia: certificano il
# wiring, non il contratto. Una spia che ritorna `True`/`False` dimostra solo
# che il test e' d'accordo con se stesso. Questi test leggono invece la
# FUNZIONE VERA - con `ast`, non con una ricerca di sottostringhe - e
# falliscono il giorno in cui qualcuno la cambia senza cambiare l'adapter.
#
# `database.py` non si tocca: e' un criterio di chiusura. Qui si misura, non si
# corregge.

def _funzione_invia_mail():
    import ast

    albero = ast.parse((ROOT / "database.py").read_text(encoding="utf-8"))
    for nodo in albero.body:
        if isinstance(nodo, ast.FunctionDef) and nodo.name == "invia_mail":
            return nodo
    raise AssertionError("database.invia_mail non esiste piu'")


def test_C23_1_la_firma_e_quella_attesa():
    import ast

    fn = _funzione_invia_mail()
    argomenti = [a.arg for a in fn.args.args]
    assert argomenti == ["destinatario", "oggetto", "corpo_html", "allegato"]
    # Tre obbligatori, uno solo con default.
    assert len(fn.args.defaults) == 1
    assert isinstance(fn.args.defaults[0], ast.Constant)
    assert fn.args.defaults[0].value is None
    assert fn.args.vararg is None and fn.args.kwarg is None


def test_C23_2_ritorna_soltanto_True_o_False():
    """Il punto che decide il mapping dell'adapter.

    Si enumerano TUTTI i `return` della funzione: se anche uno solo restituisse
    `None`, un `Response`, una tupla o un'espressione, il mapping
    `is True -> accepted` sarebbe costruito su un'assunzione falsa e questa fase
    andrebbe fermata.
    """
    import ast

    fn = _funzione_invia_mail()
    valori = []
    for nodo in ast.walk(fn):
        if isinstance(nodo, ast.Return):
            assert nodo.value is not None, "un `return` nudo restituisce None"
            assert isinstance(nodo.value, ast.Constant), (
                f"`invia_mail` restituisce un'espressione: {ast.dump(nodo.value)[:80]}"
            )
            valori.append(nodo.value.value)

    assert set(valori) == {True, False}, valori
    assert valori.count(True) == 1, "un solo successo possibile"
    assert valori.count(False) == 2, "SMTP non configurato, e invio fallito"
    for v in valori:
        assert isinstance(v, bool), f"{v!r} non e' un booleano"


def test_C23_3_non_puo_uscire_per_caduta_in_fondo():
    """Un ramo che arriva alla fine del corpo restituirebbe `None` implicito.
    L'ultima istruzione della funzione e' un `try` i cui due rami finiscono
    entrambi con un `return`: non esiste una via d'uscita silenziosa."""
    import ast

    fn = _funzione_invia_mail()
    ultima = fn.body[-1]
    assert isinstance(ultima, ast.Try), type(ultima).__name__
    assert isinstance(ultima.body[-1], ast.Return)
    for gestore in ultima.handlers:
        assert isinstance(gestore.body[-1], ast.Return)


def test_C23_4_cattura_internamente_le_eccezioni_dellinvio():
    """Le due `except Exception` che rendono `invia_mail` silenziosa: una
    attorno all'allegato, una attorno all'intera sessione SMTP. E' il motivo
    per cui quasi nessun guasto operativo arriva all'adapter come eccezione -
    e anche il motivo per cui `distinguishes_failure_class` e' False."""
    import ast

    fn = _funzione_invia_mail()
    gestori = [g for nodo in ast.walk(fn) if isinstance(nodo, ast.Try)
               for g in nodo.handlers]
    assert len(gestori) == 2, f"i blocchi except sono {len(gestori)}"
    for g in gestori:
        assert isinstance(g.type, ast.Name) and g.type.id == "Exception"


def test_C23_5_puo_comunque_sollevare_prima_del_try():
    """E PERCHE' L'ADAPTER HA COMUNQUE UN `try`.

    `int(os.getenv("SMTP_PORT", "587"))` sta FUORI da ogni `try`: con una
    variabile d'ambiente non numerica solleva `ValueError` prima ancora di
    arrivare a `smtplib`. "Ingoia le eccezioni" e' vero per l'invio, non per
    tutta la funzione - ed e' esattamente il buco che il ramo A di C22 chiude
    dentro l'adapter invece di lasciarlo alla rete di sicurezza.
    """
    import ast

    fn = _funzione_invia_mail()
    dentro_un_try = {id(n) for nodo in ast.walk(fn) if isinstance(nodo, ast.Try)
                     for ramo in (nodo.body, nodo.orelse, nodo.finalbody)
                     for stmt in ramo for n in ast.walk(stmt)}

    conversioni = [n for n in ast.walk(fn)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "int"]
    assert conversioni, "la conversione della porta non esiste piu'"
    assert any(id(c) not in dentro_un_try for c in conversioni), (
        "la conversione della porta e' protetta: il `try` dell'adapter sarebbe "
        "morto, e andrebbe rimosso con una motivazione"
    )


def test_C23_6_ladapter_riflette_questo_contratto_e_non_il_mock():
    """Il confronto letterale fra contratto e mapping.

    `True` e' l'unico successo, quindi l'adapter confronta con `is True` e non
    per veridicita': se un domani la funzione restituisse una stringa non vuota,
    `if accettata:` la leggerebbe come un invio riuscito, mentre `is True` la
    manda su `unknown` - che e' la lettura prudente giusta.
    """
    corpo = codice(PACCHETTO / "providers" / "email_smtp.py")
    assert "accettata is True" in corpo, "il confronto non e' identitario"
    assert "if accettata:" not in corpo
    assert "if not accettata" not in corpo
    # Nessun altro tipo di ritorno viene interpretato: due soli esiti.
    assert corpo.count("return ProviderResult") == 3  # accepted, unknown, unknown


def test_C23_7_i_tre_campi_del_ledger_esistono_nello_schema():
    """Il mapping message -> invia_mail regge solo se il ledger porta davvero
    quei tre campi. Si leggono dalla 064, non dalla memoria."""
    sql = (ROOT / "migrations" / "064_p29_communication_foundation.sql").read_text(
        encoding="utf-8")
    assert "destination_snapshot VARCHAR(320) NOT NULL" in sql
    assert "rendered_body        TEXT         NOT NULL" in sql
    assert "subject_snapshot     VARCHAR(300)" in sql
    # L'oggetto esiste se e solo se il canale e' email: il bicondizionale.
    assert "CHECK ((channel = 'email') = (subject_snapshot IS NOT NULL))" in sql
    # Nessuna colonna di allegato: l'adapter non ne inventa uno.
    assert "attachment" not in sql and "allegato" not in sql


def test_C23_8_lallegato_non_viene_passato(spia):
    """Il quarto parametro resta al suo default. Il ledger non ha allegati e
    questa fase non ne aggiunge: l'email della stima con il PDF e' P29-2.6."""
    chiamate, _ = spia
    email_smtp.send(messaggio())
    assert chiamate[0][3] is None
    corpo = codice(PACCHETTO / "providers" / "email_smtp.py")
    assert "allegato" not in corpo


# ---------------------------------------------------------------------------
# C23-B  Gli argomenti, esattamente
# ---------------------------------------------------------------------------

def test_C23B_gli_argomenti_sono_i_tre_campi_e_nientaltro(spia):
    """Non "e' stata chiamata": QUALI argomenti. Un messaggio realistico, e i
    quattro parametri verificati uno per uno."""
    chiamate, _ = spia
    email_smtp.send(messaggio(
        id=4321, destination_snapshot="mario.rossi@example.it",
        subject_snapshot="La tua stima e' pronta",
        rendered_body="<h1>Ciao Mario</h1><p>ecco la tua stima</p>"))

    assert len(chiamate) == 1
    destinatario, oggetto, corpo, allegato = chiamate[0]
    assert destinatario == "mario.rossi@example.it"
    assert oggetto == "La tua stima e' pronta"
    assert corpo == "<h1>Ciao Mario</h1><p>ecco la tua stima</p>"
    assert allegato is None


def test_C23B_nessun_identificatore_finisce_al_posto_dellindirizzo(spia):
    """L'errore che un mock permissivo nasconderebbe: un `id`, un `contact_id`
    o un `agency_id` passato come destinatario. Il messaggio di prova li porta
    tutti, e nessuno deve comparire negli argomenti."""
    chiamate, _ = spia
    email_smtp.send(messaggio(
        id=77, contact_id=88, agency_id=99, lead_id=111,
        destination_snapshot="cliente@example.it"))

    destinatario, oggetto, corpo, _ = chiamate[0]
    assert destinatario == "cliente@example.it"
    for identificatore in ("77", "88", "99", "111"):
        assert identificatore not in (destinatario, oggetto, corpo)


def test_C23B_un_campo_mancante_non_resta_nascosto():
    """Il mock non deve poter mascherare una chiave assente: l'adapter legge le
    tre colonne per nome, e un messaggio incompleto e' un `KeyError` rumoroso -
    non un'email mandata a `None`."""
    for mancante in ("destination_snapshot", "subject_snapshot", "rendered_body"):
        parziale = messaggio()
        del parziale[mancante]
        with pytest.raises(KeyError):
            email_smtp.send(parziale)
