"""P29 CUTOVER - la mail al cliente, dal producer alla finalizzazione `sent`.

COSA CAMBIA, E COSA NON DEVE CAMBIARE

Prima, `/api/salva_stima` faceva due cose in fila:

    mail_sent = invia_mail(data["email"], oggetto, corpo)
    if mail_sent: safe_record_event("email_stima_inviata", ...)

Adesso accoda, e la mail la manda il dispatcher. L'evento P17 deve conservare
il suo significato PAROLA PER PAROLA - "la mail al cliente e' partita" - quindi
non nasce ne' all'accodamento ne' al claim, ma nell'unico momento che
corrisponde a quel vecchio `True`: la finalizzazione `sent` che VINCE il
compare-and-set.

E deve nascere NELLA STESSA TRANSAZIONE di quel `sent`. Due transazioni
lascerebbero una finestra in cui il messaggio e' `sent` e l'evento non esiste; un
processo che muore la' dentro lascia i due registri in disaccordo per sempre,
perche' nessuno ripassa: il messaggio e' terminale e l'automazione non lo rivede.

Questo modulo prova l'atomicita' NEI DUE VERSI, che e' l'unico modo di provarla:

    l'evento non esiste senza il `sent`   (CAS perso, guasti, soppressione)
    il `sent` non esiste senza l'evento   (l'hook che solleva annulla tutto)

e in piu' guarda i COMMIT uno per uno, per escludere la finestra.

Le fixture sono quelle di `test_p29_2_6e_dispatch_ops_postgres`: la stessa app
vera, il solito PostgreSQL usa-e-getta, `invia_mail` sempre una spia. Opt-in:
senza `P29_TEST_DSN` si salta tutto.
"""

from __future__ import annotations

import os
import uuid

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare il cutover")

from tests.test_p29_2_6e_dispatch_ops_postgres import (  # noqa: E402,F401
    PASSWORD, accedi, accoda, client, db, mondo, riga)

EVENTO = "email_stima_inviata"


# ---------------------------------------------------------------------------
# Strumenti di misura
# ---------------------------------------------------------------------------

def eventi(mondo, tipo: str = EVENTO) -> list[dict]:
    with mondo["cur"]() as cur:
        cur.execute("SELECT * FROM seller_timeline_events WHERE event_type = %s "
                    "ORDER BY id", (tipo,))
        return [dict(r) for r in cur.fetchall()]


def tentativi(mondo, message_id: int) -> list[dict]:
    with mondo["cur"]() as cur:
        cur.execute("SELECT * FROM communication_attempts WHERE message_id = %s "
                    "ORDER BY id", (message_id,))
        return [dict(r) for r in cur.fetchall()]


def dispaccia(c):
    return c.post("/api/communication/dispatch", json={"channel": "email", "limit": 10})


# ---------------------------------------------------------------------------
# 6. Accodare non e' aver mandato
# ---------------------------------------------------------------------------

def test_cut_1_in_coda_levento_non_esiste(client, mondo):
    """`queued` e' un'intenzione. L'evento direbbe una cosa non ancora vera."""
    c, inviate = client
    m = accoda(mondo, chiave="cut-1")

    assert riga(mondo, m["id"])["status"] == "queued"
    assert eventi(mondo) == []
    assert inviate == []


def test_cut_2_al_claim_levento_non_esiste_ancora(client, mondo, monkeypatch):
    """Nemmeno a `sending`: il messaggio e' reclamato, non consegnato.

    Il provider viene fermato su un esito ignoto, cioe' il caso in cui davvero
    non si sa: se l'evento nascesse al claim, comparirebbe qui.
    """
    from communication.providers import base as provider_base
    from communication.providers import email_smtp

    c, inviate = client
    m = accoda(mondo, chiave="cut-2")

    monkeypatch.setattr(email_smtp, "send", lambda message: provider_base.ProviderResult(
        outcome=provider_base.OUTCOME_UNKNOWN, error_detail="nessuna risposta"))

    assert accedi(c, mondo).status_code == 204
    risposta = dispaccia(c)

    assert risposta.status_code == 200 and risposta.json()["indeterminate"] == 1
    assert riga(mondo, m["id"])["status"] == "indeterminate"
    assert eventi(mondo) == [], "l'evento e' nato su un esito ignoto"


# ---------------------------------------------------------------------------
# 7. Il giro riuscito, e i commit visti uno per uno
# ---------------------------------------------------------------------------

def test_cut_3_sent_tentativo_accettato_ed_evento_nello_stesso_commit(
        client, mondo, monkeypatch):
    """Il test centrale del cutover, e la prova che la finestra non esiste.

    Si guarda da una SECONDA connessione, che vede solo cio' che e' stato
    committato. L'osservazione avviene DENTRO l'hook, cioe' nell'istante fra il
    compare-and-set e il commit: se il `sent` avesse una transazione sua, a quel
    punto sarebbe gia' visibile da fuori e l'evento no - la finestra. Da fuori,
    in quell'istante, non si deve vedere nessuna delle due cose; dopo, entrambe.
    """
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from communication import integrations

    c, inviate = client
    m = accoda(mondo, chiave="cut-3")

    spettatore = psycopg2.connect(mondo["conn"].dsn)
    spettatore.autocommit = True
    osservato = {}
    vero_hook = integrations.dopo_invio

    def guarda_da_fuori(cur, esito):
        with spettatore.cursor(cursor_factory=RealDictCursor) as fuori:
            fuori.execute("SELECT status FROM communication_messages WHERE id = %s",
                          (m["id"],))
            riga_vista = fuori.fetchone()
            fuori.execute("SELECT count(*) AS quanti FROM seller_timeline_events "
                          "WHERE event_type = %s", (EVENTO,))
            osservato["stato_visibile"] = riga_vista["status"] if riga_vista else None
            osservato["eventi_visibili"] = fuori.fetchone()["quanti"]
        return vero_hook(cur, esito)

    monkeypatch.setattr(integrations, "dopo_invio", guarda_da_fuori)

    try:
        assert accedi(c, mondo).status_code == 204
        risposta = dispaccia(c)
        assert risposta.status_code == 200, risposta.text
        assert risposta.json()["sent"] == 1

        # DENTRO la transazione, da fuori non si vedeva ne' l'uno ne' l'altro.
        assert osservato, "l'hook non e' stato eseguito: il test non prova niente"
        assert osservato["stato_visibile"] != "sent", (
            "il `sent` era gia' committato mentre l'evento non c'era: la finestra")
        assert osservato["eventi_visibili"] == 0

        # DOPO, da fuori si vedono entrambi.
        with spettatore.cursor(cursor_factory=RealDictCursor) as fuori:
            fuori.execute("SELECT status FROM communication_messages WHERE id = %s",
                          (m["id"],))
            assert fuori.fetchone()["status"] == "sent"
            fuori.execute("SELECT count(*) AS quanti FROM seller_timeline_events "
                          "WHERE event_type = %s", (EVENTO,))
            assert fuori.fetchone()["quanti"] == 1
    finally:
        spettatore.close()

    # Il messaggio e' partito...
    finale = riga(mondo, m["id"])
    assert finale["status"] == "sent" and finale["sent_at"] is not None
    assert finale["provider"] == "email_smtp"
    assert len(inviate) == 1

    # ...il tentativo e' chiuso `accepted`, e ce n'e' UNO...
    storia = tentativi(mondo, m["id"])
    assert len(storia) == 1
    assert storia[0]["outcome"] == "accepted"
    assert finale["attempt_count"] == 1

    # ...e l'evento P17 e' identico a quello che scriveva il producer.
    righe = eventi(mondo)
    assert len(righe) == 1
    evento = righe[0]
    assert evento["event_source"] == "stima360_it"
    assert evento["stima_id"] == mondo["st"]
    assert evento["idempotency_key"] == f"{EVENTO}:{mondo['st']}"
    assert evento["payload"] == {"pdf_url": "https://example.it/x.pdf"}
    assert evento["agency_id"] == mondo["a"]


def test_cut_4_i_riferimenti_vengono_dal_messaggio(client, mondo):
    """Contatto e lead dell'evento sono quelli che il producer ha scritto sulla
    riga, non un ricalcolo: due derivazioni possono divergere."""
    c, _ = client
    accoda(mondo, chiave="cut-4", contact_id=mondo["k"])

    assert accedi(c, mondo).status_code == 204
    assert dispaccia(c).json()["sent"] == 1

    evento = eventi(mondo)[0]
    assert evento["contact_id"] == mondo["k"]
    assert evento["stima_id"] == mondo["st"]


# ---------------------------------------------------------------------------
# 8. Nessun esito che non sia `sent` crea l'evento
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("esito, conteggio", [
    ("rifiutato", "indeterminate"),
    ("eccezione", "indeterminate"),
])
def test_cut_5_un_insuccesso_non_crea_levento(client, mondo, monkeypatch,
                                              esito, conteggio):
    """`invia_mail` che ritorna False, e un provider che solleva.

    Entrambi finiscono `indeterminate` - e non `failed` - perche' l'adapter
    email dichiara di non distinguere un rifiuto certo da un esito ignoto. Cio'
    che si misura qui e' l'evento, che in nessuno dei due casi deve esistere.
    """
    from communication.providers import email_smtp

    c, inviate = client
    m = accoda(mondo, chiave=f"cut-5-{esito}")

    if esito == "rifiutato":
        monkeypatch.setattr(email_smtp, "invia_mail", lambda *a, **k: False)
    else:
        def esplode(*a, **k):
            raise RuntimeError("il trasporto e' caduto")
        monkeypatch.setattr(email_smtp, "invia_mail", esplode)

    assert accedi(c, mondo).status_code == 204
    risposta = dispaccia(c)

    assert risposta.status_code == 200, risposta.text
    assert risposta.json()[conteggio] == 1
    assert risposta.json()["sent"] == 0
    assert riga(mondo, m["id"])["status"] != "sent"
    assert eventi(mondo) == [], "un insuccesso ha creato l'evento"
    assert inviate == []


def test_cut_6_una_soppressione_non_crea_levento(client, mondo):
    """Un marketing senza consenso: `suppressed`, e nessun evento.

    Non e' la mail della stima - il gate non tocca le SERVICE - ma e' l'esito
    terminale che piu' assomiglia a un successo nei conteggi, e va escluso
    esplicitamente.
    """
    c, inviate = client
    m = accoda(mondo, chiave="cut-6", contact_id=mondo["k"], tipo="marketing")

    assert accedi(c, mondo).status_code == 204
    risposta = dispaccia(c)

    assert risposta.status_code == 200, risposta.text
    assert risposta.json()["suppressed"] == 1
    assert riga(mondo, m["id"])["status"] == "suppressed"
    assert eventi(mondo) == []
    assert inviate == []


def test_cut_7_un_cas_perso_non_crea_levento(client, mondo, monkeypatch):
    """`lost`: il messaggio non e' piu' nostro.

    Il token viene cambiato sotto i piedi del worker mentre il provider sta
    "mandando" - cioe' esattamente la corsa che il fencing esiste per perdere
    bene. La finalizzazione restituisce `None`, e un evento che dicesse "l'ho
    mandato io" sarebbe una bugia nel registro di un altro worker.
    """
    from communication.providers import email_smtp

    c, inviate = client
    m = accoda(mondo, chiave="cut-7")
    vero_invio = email_smtp.invia_mail

    def ruba_il_messaggio(*args, **kwargs):
        with mondo["conn"].cursor() as cur:
            cur.execute("UPDATE communication_messages SET claim_token = %s "
                        "WHERE id = %s", (str(uuid.uuid4()), m["id"]))
        mondo["conn"].commit()
        return vero_invio(*args, **kwargs)

    monkeypatch.setattr(email_smtp, "invia_mail", ruba_il_messaggio)

    assert accedi(c, mondo).status_code == 204
    risposta = dispaccia(c)

    assert risposta.status_code == 200, risposta.text
    conteggi = risposta.json()
    assert conteggi["lost"] == 1 and conteggi["sent"] == 0
    assert riga(mondo, m["id"])["status"] != "sent"
    assert eventi(mondo) == [], "un compare-and-set perso ha creato l'evento"
    # La mail era gia' uscita - il provider e' stato chiamato - e resta
    # registrata come risultato TARDIVO, che e' il posto giusto per dirlo.
    assert len(inviate) == 1


# ---------------------------------------------------------------------------
# Atomicita' nell'altro verso: il `sent` non esiste senza l'evento
# ---------------------------------------------------------------------------

def test_cut_8_se_levento_non_si_scrive_il_sent_torna_indietro(client, mondo,
                                                               monkeypatch):
    """Il prezzo dell'atomicita', dichiarato e misurato.

    L'hook e' forzato a sollevare: la transazione non committa, e il messaggio
    NON risulta `sent`. Resta `sending` con il suo token - da dove
    `recover_stale` lo porta a `indeterminate` e non lo rimette in fila: nessun
    reinvio cieco, nessun doppio invio, e `attempt_count` resta quello del
    claim. La mail e' uscita e il ledger non dice `sent`: e' la scelta
    fail-safe, ed e' preferibile a due registri che si contraddicono.
    """
    from communication import integrations

    c, inviate = client
    m = accoda(mondo, chiave="cut-8")

    def evento_che_non_si_scrive(cur, message):
        raise RuntimeError("seller_timeline_events non scrivibile")

    monkeypatch.setattr(integrations, "HOOK_DOPO_INVIO",
                        ((integrations._e_la_mail_della_stima,
                          evento_che_non_si_scrive),))

    assert accedi(c, mondo).status_code == 204
    risposta = dispaccia(c)

    # Il giro non mente: il messaggio non e' contato fra i `sent`.
    assert risposta.status_code == 200, risposta.text
    conteggi = risposta.json()
    assert conteggi["sent"] == 0
    # `lost`: il messaggio esiste, il suo esito non e' stato registrato. E' il
    # conteggio che descrive esattamente cio' che e' successo, e il runner del
    # cron lo legge come insuccesso applicativo (exit 2).
    assert conteggi["lost"] == 1

    riga_finale = riga(mondo, m["id"])
    assert riga_finale["status"] != "sent", "il `sent` ha committato senza l'evento"
    assert riga_finale["status"] == "sending"
    assert riga_finale["claim_token"] is not None
    assert riga_finale["attempt_count"] == 1, "un tentativo in piu' e' un reinvio"
    assert eventi(mondo) == []
    # Il provider ERA stato chiamato: la mail e' uscita. E' il caso che il
    # docstring dichiara.
    assert len(inviate) == 1


def test_cut_9_da_sending_la_recovery_non_riaccoda(client, mondo, monkeypatch):
    """Il seguito del caso sopra: `recover_stale` chiude, non rispedisce."""
    from communication import integrations, service
    from operator_auth.context import SystemAgencyContext

    c, inviate = client
    m = accoda(mondo, chiave="cut-9")

    monkeypatch.setattr(integrations, "HOOK_DOPO_INVIO",
                        ((integrations._e_la_mail_della_stima,
                          lambda cur, message: (_ for _ in ()).throw(
                              RuntimeError("non scrivibile"))),))
    assert accedi(c, mondo).status_code == 204
    primo = dispaccia(c).json()
    assert primo["sent"] == 0 and primo["lost"] == 1
    assert riga(mondo, m["id"])["status"] == "sending"

    # La riga e' vecchia abbastanza da essere stale: si invecchia il claim.
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE communication_messages "
                    "SET claimed_at = NOW() - INTERVAL '2 hours' WHERE id = %s",
                    (m["id"],))
    mondo["conn"].commit()

    ctx = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
    recuperati = service.recover_stale(ctx, stale_after_seconds=60)

    assert len(recuperati) == 1
    riga_finale = riga(mondo, m["id"])
    assert riga_finale["status"] == "indeterminate"
    assert riga_finale["attempt_count"] == 1, "la recovery ha consumato un tentativo"
    assert eventi(mondo) == []
    assert len(inviate) == 1, "la recovery ha rispedito la mail"


# ---------------------------------------------------------------------------
# 9. Il secondo giro non manda niente e non scrive niente
# ---------------------------------------------------------------------------

def test_cut_10_un_secondo_giro_non_crea_ne_mail_ne_evento(client, mondo):
    c, inviate = client
    m = accoda(mondo, chiave="cut-10")

    assert accedi(c, mondo).status_code == 204
    primo = dispaccia(c)
    secondo = dispaccia(c)

    assert primo.json()["sent"] == 1
    assert secondo.json() == {"claimed": 0, "sent": 0, "suppressed": 0,
                              "failed": 0, "indeterminate": 0, "lost": 0}
    assert len(inviate) == 1
    assert len(eventi(mondo)) == 1
    assert riga(mondo, m["id"])["status"] == "sent"


def test_cut_11_levento_e_idempotente_sulla_stima(client, mondo):
    """Due messaggi diversi per la STESSA stima: un evento solo.

    La chiave e' `email_stima_inviata:{stima_id}`, come prima del cutover, e la
    dedup e' quella dell'indice parziale di 017. Un rinvio della mail di quella
    stima non raddoppia la timeline.
    """
    c, inviate = client
    accoda(mondo, chiave="cut-11-a")

    assert accedi(c, mondo).status_code == 204
    assert dispaccia(c).json()["sent"] == 1
    assert len(eventi(mondo)) == 1

    accoda(mondo, chiave="cut-11-b")
    assert dispaccia(c).json()["sent"] == 1

    assert len(inviate) == 2, "la seconda mail e' partita"
    assert len(eventi(mondo)) == 1, "l'evento si e' duplicato"


# ---------------------------------------------------------------------------
# Il riconoscimento: dai dati, non dal testo
# ---------------------------------------------------------------------------

def test_cut_12_una_service_che_non_e_la_mail_della_stima_non_crea_levento(
        client, mondo):
    """Un altro `reason_code`, stesso canale e stesso tipo: nessun evento.

    Se il riconoscimento guardasse il canale o il tipo, questo messaggio lo
    creerebbe. Guarda invece i quattro campi strutturati, e `reason_code` e' uno
    dei quattro.

    Il `reason_code` si passa all'accodamento e non si corregge dopo: le colonne
    di identita' e di snapshot sono immutabili, e il guardiano della 064 rifiuta
    quell'UPDATE - come deve. Un primo tentativo di questo test lo ha scoperto
    cosi'.
    """
    from psycopg2.extras import RealDictCursor

    from communication import service
    from operator_auth.context import SystemAgencyContext

    c, inviate = client
    ctx = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
    cur = mondo["conn"].cursor(cursor_factory=RealDictCursor)
    try:
        esito = service.enqueue(
            ctx, cur=cur, contact_id=None, channel="email",
            communication_type="service", mode="automatic",
            reason_code="operator_manual",
            rendered_body="<p>non e' la stima</p>",
            destination_snapshot="mario@example.it",
            subject_snapshot="Un'altra cosa",
            idempotency_key="cut-12", stima_id=mondo["st"],
            metadata={"pdf_url": "https://example.it/x.pdf"})
    finally:
        cur.close()
        mondo["conn"].commit()
    m = esito["message"]

    assert accedi(c, mondo).status_code == 204
    assert dispaccia(c).json()["sent"] == 1
    assert riga(mondo, m["id"])["status"] == "sent"
    assert len(inviate) == 1
    assert eventi(mondo) == [], "un altro reason_code ha creato l'evento della stima"


def test_cut_13_il_riconoscimento_non_legge_ne_oggetto_ne_corpo():
    """Letto come testo: il riconoscimento guarda i campi strutturati.

    Un'euristica sull'oggetto o sul corpo si romperebbe la prima volta che
    qualcuno cambia una parola della mail - un cambiamento che nessuno
    penserebbe di far passare da qui.
    """
    import inspect
    import re

    from communication import integrations

    def codice(funzione) -> str:
        return re.sub(r'"{3}[\s\S]*?"{3}', "", inspect.getsource(funzione))

    riconoscimento = codice(integrations._e_la_mail_della_stima)
    for vietato in ("subject_snapshot", "rendered_body", "destination_snapshot"):
        assert vietato not in riconoscimento, f"il riconoscimento legge {vietato}"
    for atteso in ("communication_type", "reason_code"):
        assert atteso in riconoscimento, f"il riconoscimento non guarda {atteso}"

    # I dati che l'evento richiede stanno nel ramo che lo scrive, e li' sono
    # PRETESI: se mancassero, si solleva invece di non applicarsi.
    scrittura = codice(integrations._evento_email_stima) + codice(
        integrations._pdf_url_obbligatorio)
    for atteso in ("stima_id", "pdf_url", "raise"):
        assert atteso in scrittura, f"la scrittura dell'evento non pretende {atteso}"


# ---------------------------------------------------------------------------
# `metadata.pdf_url`: obbligatorio, e l'evento non nasce senza
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("metadata, come", [
    ({}, "metadata vuoti"),
    ({"altro": "x"}, "metadata senza la chiave"),
    ({"pdf_url": None}, "pdf_url nullo"),
    ({"pdf_url": ""}, "pdf_url stringa vuota"),
    ({"pdf_url": "   "}, "pdf_url di soli spazi"),
    ({"pdf_url": 42}, "pdf_url che non e' una stringa"),
])
def test_cut_14_senza_pdf_url_il_sent_non_committa(client, mondo, metadata, come):
    """L'evento non deve MAI nascere con un payload senza il documento.

    Una riga `email_stima_inviata` con payload vuoto avrebbe il nome giusto e
    nessun contenuto: direbbe "la mail e' partita" senza dire QUALE documento
    la persona ha ricevuto, ed e' il genere di riga che nessuno guarda finche'
    non serve. Il rifiuto e' rumoroso: la transazione non committa, il messaggio
    non risulta `sent`, e il giro lo conta `lost`.

    Il costo e' dichiarato: la mail E' uscita. Ma il messaggio resta `sending`,
    la recovery lo chiude `indeterminate` e non lo rimette in fila, quindi il
    prezzo e' un messaggio da guardare - non una mail mandata due volte.
    """
    from psycopg2.extras import RealDictCursor

    from communication import service
    from operator_auth.context import SystemAgencyContext

    c, inviate = client
    ctx = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
    cur = mondo["conn"].cursor(cursor_factory=RealDictCursor)
    try:
        esito = service.enqueue(
            ctx, cur=cur, contact_id=None, channel="email",
            communication_type="service", mode="automatic",
            reason_code="stima_pdf", rendered_body="<p>la stima</p>",
            destination_snapshot="mario@example.it",
            subject_snapshot="La tua stima", idempotency_key=f"cut-14-{come}",
            stima_id=mondo["st"], metadata=metadata)
    finally:
        cur.close()
        mondo["conn"].commit()
    m = esito["message"]

    assert accedi(c, mondo).status_code == 204
    risposta = dispaccia(c)

    assert risposta.status_code == 200, risposta.text
    conteggi = risposta.json()
    assert conteggi["sent"] == 0, f"{come}: il `sent` ha committato"
    assert conteggi["lost"] == 1, come

    riga_finale = riga(mondo, m["id"])
    assert riga_finale["status"] == "sending", come
    assert riga_finale["claim_token"] is not None
    assert riga_finale["attempt_count"] == 1, "un tentativo in piu' e' un reinvio"
    assert eventi(mondo) == [], f"{come}: l'evento e' nato senza il pdf_url"
    # Il provider era stato chiamato: la mail e' uscita, ed e' il costo
    # dichiarato nel docstring.
    assert len(inviate) == 1


def test_cut_15_il_payload_dellevento_porta_il_pdf_url_vero():
    """Non un segnaposto, non una stringa fissa: quello del messaggio.

    Si guarda la funzione che scrive: il valore passato al payload dev'essere
    quello che la validazione ha appena estratto dalla riga, cosi' un evento non
    puo' nascere con un URL che non e' quello di quella stima.
    """
    import inspect

    from communication import integrations

    sorgente = inspect.getsource(integrations._evento_email_stima)
    assert "pdf_url = _pdf_url_obbligatorio(message)" in sorgente
    assert 'payload={"pdf_url": pdf_url}' in sorgente


# ---------------------------------------------------------------------------
# LA RECOVERY DEGLI STALE, SUL PERCORSO OPERATIVO VERO
#
# `cut_9` prova che `service.recover_stale` fa la cosa giusta. Non provava che
# qualcuno la chiami: la invocava il test, a mano. E infatti nessuno la
# chiamava - non il cron, non la rotta, non il dispatcher - quindi il fail-safe
# del cutover era una funzione giusta su un percorso morto: un messaggio rimasto
# `sending` ci restava per sempre, e il ledger diceva "sto mandando" di una mail
# che nessuno avrebbe piu' toccato.
#
# Adesso il passo 0 di ogni giro di dispatch e' la recovery. Qui si misura DA
# FUORI, per HTTP, con il cron vero.
# ---------------------------------------------------------------------------

def _rendi_stale(mondo, message_id, *, ore=2):
    """Invecchia il claim di un messaggio `sending` oltre la soglia."""
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE communication_messages "
                    "SET claimed_at = NOW() - make_interval(hours => %s) "
                    "WHERE id = %s", (ore, message_id))
    mondo["conn"].commit()


def _lascia_in_sending(c, mondo, monkeypatch, *, chiave):
    """Un messaggio davvero `sending`, con il suo tentativo aperto.

    Si ottiene come si ottiene in produzione: un giro in cui il provider
    accetta e il seguito della finalizzazione non committa. Nessun UPDATE a mano
    sullo stato - il guardiano della 064 lo rifiuterebbe, e giustamente: uno
    stato scritto a mano non e' lo stato in cui il sistema ci finisce.
    """
    from communication import integrations

    # L'hook VERO si mette da parte e si rimette con un secondo `setattr`.
    #
    # NON con il metodo che annulla in blocco i monkeypatch: quello annulla
    # TUTTO cio' che questo monkeypatch ha installato, e la fixture `mondo` ci
    # ha messo dentro le sostituzioni dei cursori. Annullarle qui rimandava ogni
    # query successiva al DSN di default - e il primo tentativo di questi test
    # e' fallito esattamente cosi', con un socket /var/run/postgresql che in
    # questo ambiente non esiste.
    originale = integrations.HOOK_DOPO_INVIO

    m = accoda(mondo, chiave=chiave)
    monkeypatch.setattr(integrations, "HOOK_DOPO_INVIO",
                        ((integrations._e_la_mail_della_stima,
                          lambda cur, message: (_ for _ in ()).throw(
                              RuntimeError("la transazione finale non committa"))),))
    assert accedi(c, mondo).status_code == 204
    assert dispaccia(c).json()["lost"] == 1

    monkeypatch.setattr(integrations, "HOOK_DOPO_INVIO", originale)
    return m


def test_cut_16_un_giro_normale_chiude_gli_stale(client, mondo, monkeypatch):
    """IL test di questo audit: cron -> rotta -> dispatch -> recovery.

    Non si chiama `recover_stale` da nessuna parte in questo test. Si fa un
    normale `POST /api/communication/dispatch`, cioe' esattamente cio' che il
    cron Render fa ogni ora, e si pretende che lo stale sia stato chiuso.
    """
    c, inviate = client
    m = _lascia_in_sending(c, mondo, monkeypatch, chiave="cut-16")

    # L'helper ha gia' rimesso l'hook vero: il giro successivo e' normale.
    assert riga(mondo, m["id"])["status"] == "sending"
    assert len(inviate) == 1
    _rendi_stale(mondo, m["id"])

    assert accedi(c, mondo).status_code == 204
    risposta = dispaccia(c)

    assert risposta.status_code == 200, risposta.text
    conteggi = risposta.json()
    # Lo stale conta `indeterminate`, e NON `claimed`: il suo claim e' accaduto
    # in un giro precedente e la storia lo dice una volta sola.
    assert conteggi["indeterminate"] == 1, conteggi
    assert conteggi["claimed"] == 0, conteggi
    assert conteggi["sent"] == 0

    riga_finale = riga(mondo, m["id"])
    assert riga_finale["status"] == "indeterminate"
    assert riga_finale["failure_class"] == "indeterminate"
    assert riga_finale["error_code"] == "outcome_unknown"
    assert riga_finale["claim_token"] is None
    # Nessun tentativo in piu', e nessuna seconda chiamata al trasporto.
    assert riga_finale["attempt_count"] == 1, "il giro ha consumato un tentativo"
    assert len(tentativi(mondo, m["id"])) == 1
    assert len(inviate) == 1, "il provider e' stato interpellato una seconda volta"
    assert eventi(mondo) == []


def test_cut_17_il_tentativo_dello_stale_e_chiuso_e_marcato(client, mondo, monkeypatch):
    """Il tentativo aperto non resta aperto: si chiude, e dice com'e' finito."""
    c, _ = client
    m = _lascia_in_sending(c, mondo, monkeypatch, chiave="cut-17")
    _rendi_stale(mondo, m["id"])

    assert accedi(c, mondo).status_code == 204
    assert dispaccia(c).json()["indeterminate"] == 1

    storia = tentativi(mondo, m["id"])
    assert len(storia) == 1, "la recovery ha affiancato un tentativo invece di chiuderlo"
    tentativo = storia[0]
    assert tentativo["outcome"] != "in_progress", "il tentativo e' rimasto aperto"
    assert tentativo["outcome"] == "indeterminate"
    assert tentativo["failure_class"] == "indeterminate"
    assert tentativo["late_result"] is False


def test_cut_18_un_sending_recente_non_viene_toccato(client, mondo, monkeypatch):
    """Un worker al lavoro non e' uno stale.

    Il messaggio resta `sending` col suo token, e il giro non lo conta in nessun
    modo. Se la soglia sparisse, questo test vedrebbe un worker vivo derubato
    del suo messaggio mentre sta mandando.
    """
    c, _ = client
    m = _lascia_in_sending(c, mondo, monkeypatch, chiave="cut-18")
    # NON si invecchia: `claimed_at` e' di adesso.

    assert accedi(c, mondo).status_code == 204
    conteggi = dispaccia(c).json()

    assert conteggi == {"claimed": 0, "sent": 0, "suppressed": 0, "failed": 0,
                        "indeterminate": 0, "lost": 0}, conteggi
    riga_finale = riga(mondo, m["id"])
    assert riga_finale["status"] == "sending"
    assert riga_finale["claim_token"] is not None
    assert tentativi(mondo, m["id"])[0]["outcome"] == "in_progress"


def test_cut_19_dopo_la_recovery_la_coda_continua_a_essere_svuotata(
        client, mondo, monkeypatch):
    """La recovery non e' un'uscita anticipata: il giro prosegue.

    Uno stale da chiudere E una mail nuova da mandare, nello stesso giro. Se la
    recovery ritornasse presto, la mail nuova resterebbe in fila e nessuno se ne
    accorgerebbe - il conteggio direbbe `indeterminate=1` e sembrerebbe un giro
    fatto.
    """
    c, inviate = client
    vecchio = _lascia_in_sending(c, mondo, monkeypatch, chiave="cut-19-vecchio")
    _rendi_stale(mondo, vecchio["id"])

    nuovo = accoda(mondo, chiave="cut-19-nuovo")

    assert accedi(c, mondo).status_code == 204
    conteggi = dispaccia(c).json()

    assert conteggi["indeterminate"] == 1, conteggi
    assert conteggi["claimed"] == 1, conteggi
    assert conteggi["sent"] == 1, conteggi

    assert riga(mondo, vecchio["id"])["status"] == "indeterminate"
    assert riga(mondo, nuovo["id"])["status"] == "sent"
    # La mail nuova e' partita: due invii in tutto, il primo era del giro prima.
    assert len(inviate) == 2
    # E l'evento P17 esiste per la stima, una volta sola.
    assert len(eventi(mondo)) == 1


def test_cut_20_il_runner_del_cron_esce_2_su_uno_stale_recuperato(
        client, mondo, monkeypatch):
    """La catena intera, fino al codice di uscita che Render legge.

    Senza questo, un messaggio perso produrrebbe un cron verde: e' il motivo per
    cui `indeterminate` e' fra i conteggi di guasto del runner.
    """
    import importlib

    runner = importlib.import_module("run_communication_dispatch_cron")
    c, _ = client
    m = _lascia_in_sending(c, mondo, monkeypatch, chiave="cut-20")
    _rendi_stale(mondo, m["id"])

    config = runner.Config(base_url="", email=mondo["email"], password=PASSWORD,
                           channel="email", limit=10, timeout=(5.0, 60.0))
    dati = runner.run_once(config, sessione=c)

    assert dati["indeterminate"] == 1, dati
    assert runner._application_failure(dati) is True

    monkeypatch.setattr(runner, "run_once", lambda config, **kw: dati)
    monkeypatch.setenv("COMMUNICATION_DISPATCH_BASE_URL", "https://esempio.it")
    monkeypatch.setenv("COMMUNICATION_DISPATCH_EMAIL", mondo["email"])
    monkeypatch.setenv("COMMUNICATION_DISPATCH_PASSWORD", PASSWORD)
    assert runner.main() == 2


def test_cut_21_la_recovery_e_sul_percorso_operativo_non_in_un_secondo_cron():
    """Letto come testo: la catena esiste e non ce n'e' una seconda.

    Il fail-safe del cutover deve stare sul percorso che il cron percorre
    davvero. Un secondo cron da configurare a mano e' un fail-safe che esiste
    nel codice e non in produzione, ed e' come se non ci fosse.
    """
    import inspect
    import re
    from pathlib import Path

    from communication import dispatcher

    corpo = re.sub(r'"{3}[\s\S]*?"{3}', "", inspect.getsource(dispatcher.dispatch_batch))
    assert "service.recover_stale(" in corpo, (
        "il dispatcher non chiude piu' gli stale: il fail-safe del cutover e' "
        "tornato su un percorso che nessuno percorre")
    # Prima dei claim nuovi, non dopo.
    assert corpo.index("service.recover_stale(") < corpo.index("service.claim_due("), (
        "la recovery gira dopo il claim: uno stale aspetterebbe un giro in piu'")
    # Nessun provider fra la recovery e il claim.
    fra = corpo[corpo.index("service.recover_stale("):corpo.index("service.claim_due(")]
    assert "provider" not in fra, "la recovery interpella il trasporto"

    # E il runner del cron e' uno: non ne nasce un secondo per la recovery.
    radice = Path(inspect.getsourcefile(dispatcher)).resolve().parents[1]
    runner = [p.name for p in radice.glob("run_communication*_cron.py")]
    assert runner == ["run_communication_dispatch_cron.py"], runner
