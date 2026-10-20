"""FASE A del follow-up a 180 giorni - il trasporto email.

Cosa cambia, e cosa resta uguale, provato senza rete e senza database:

  * `database.invia_mail` con i tre argomenti di sempre produce lo stesso
    messaggio di prima: una parte html, nessuna intestazione in piu';
  * con `corpo_testo` produce multipart/alternative (testo + html), con
    `reply_to` e le sole intestazioni ammesse; un valore con un a capo non
    entra mai in un'intestazione;
  * un errore SMTP resta False, UNA sola `sendmail`, nessun ritentativo;
  * l'adapter manda i corpi di testo con gli argomenti nuovi e tutto il resto
    con i tre di sempre; il testo diventa html con escape e link;
  * il Reply-To viene dalla configurazione, solo per il marketing;
  * il POST pubblico di disiscrizione accetta il token nella query (one-click,
    RFC 8058); la GET continua a non disiscrivere;
  * le sentinelle di confine del pacchetto restano vere per i file nuovi.

In coda, con PostgreSQL (opt-in `P29_TEST_DSN`): un passo di sequenza nasce
con i metadata di spedizione e arriva alla primitiva con testo, html,
Reply-To e List-Unsubscribe; una email di servizio no.
"""
from __future__ import annotations

import email
import os
import re
from email import policy
from pathlib import Path

import pytest

import database
from communication import email_metadata
from communication.providers import email_format, email_smtp

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# il finto SMTP
# ---------------------------------------------------------------------------

@pytest.fixture
def smtp(monkeypatch):
    for chiave, valore in {"SMTP_HOST": "smtp.finto.invalid", "SMTP_PORT": "587",
                           "SMTP_USER": "mittente@finto.invalid",
                           "SMTP_PASS": "segreto-locale-mai-in-uscita"}.items():
        monkeypatch.setenv(chiave, valore)
    registro = {"spediti": [], "esplode": None}

    class FintoSMTP:
        def __init__(self, host, port, timeout=None):
            registro["host"] = (host, port)

        def ehlo(self): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def quit(self): pass

        def sendmail(self, da, a, testo):
            if registro["esplode"] is not None:
                raise registro["esplode"]
            registro["spediti"].append((da, a, testo))

    monkeypatch.setattr(database.smtplib, "SMTP", FintoSMTP)
    return registro


def _messaggio(registro):
    assert len(registro["spediti"]) == 1
    return email.message_from_string(registro["spediti"][0][2], policy=policy.default)


# ---------------------------------------------------------------------------
# A  invia_mail
# ---------------------------------------------------------------------------

def test_a1_la_chiamata_di_sempre_produce_il_messaggio_di_sempre(smtp):
    assert database.invia_mail("a@b.it", "Oggetto", "<p>ciao</p>") is True
    m = _messaggio(smtp)
    parti = list(m.walk())
    assert [p.get_content_type() for p in parti] == ["multipart/mixed", "text/html"]
    assert parti[1].get_content().strip() == "<p>ciao</p>"
    for intestazione in ("Reply-To", "List-Unsubscribe", "List-Unsubscribe-Post"):
        assert m[intestazione] is None
    assert m["From"] == "mittente@finto.invalid" and m["To"] == "a@b.it"


def test_a2_con_il_testo_nasce_un_alternative_testo_prima_html_dopo(smtp):
    testo = "Ciao Anna,\n\n• primo\n• secondo\n"
    database.invia_mail("a@b.it", "Oggetto", "<p>html</p>", corpo_testo=testo,
                        reply_to="info@stima360.it",
                        intestazioni={"List-Unsubscribe": "<https://x.it/u?t=1>",
                                      "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
    m = _messaggio(smtp)
    tipi = [p.get_content_type() for p in m.walk()]
    assert tipi == ["multipart/mixed", "multipart/alternative", "text/plain", "text/html"]
    parti = [p for p in m.walk() if not p.is_multipart()]
    assert parti[0].get_content() == testo, "gli a capo del testo si conservano"
    assert parti[1].get_content().strip() == "<p>html</p>"
    assert m["Reply-To"] == "info@stima360.it"
    assert m["List-Unsubscribe"] == "<https://x.it/u?t=1>"
    assert m["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert m["From"] == "mittente@finto.invalid"


def test_a3_solo_le_intestazioni_ammesse_e_mai_con_un_a_capo(smtp):
    database.invia_mail("a@b.it", "O", "<p>x</p>", corpo_testo="x",
                        reply_to="a@b.it\r\nBcc: spia@x.it",
                        intestazioni={"Bcc": "spia@x.it", "From": "altro@x.it",
                                      "List-Unsubscribe": "<https://x.it>\nBcc: spia@x.it",
                                      "List-Unsubscribe-Post": "  List-Unsubscribe=One-Click "})
    m = _messaggio(smtp)
    assert m["Reply-To"] is None and m["List-Unsubscribe"] is None
    assert m["Bcc"] is None and m["From"] == "mittente@finto.invalid"
    assert m["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "spia@x.it" not in smtp["spediti"][0][2]


def test_a4_un_errore_smtp_e_false_una_sola_sendmail_nessun_ritentativo(smtp, capsys):
    import smtplib
    smtp["esplode"] = smtplib.SMTPRecipientsRefused({"a@b.it": (550, b"no")})
    assert database.invia_mail("a@b.it", "O", "<p>x</p>", corpo_testo="x") is False
    assert smtp["spediti"] == []
    uscita = capsys.readouterr().out
    assert "SMTPRecipientsRefused" in uscita
    assert "segreto-locale-mai-in-uscita" not in uscita


def test_a5_un_timeout_dopo_sendmail_resta_false_senza_secondo_invio(smtp, monkeypatch):
    """Il caso incerto per eccellenza: il server ha preso il messaggio e la
    connessione cade. False, e nessuno rimanda."""
    chiamate = []

    class SMTPCheCade(database.smtplib.SMTP):
        def sendmail(self, da, a, testo):
            chiamate.append(a)
            raise TimeoutError("caduto dopo l'accettazione")

    monkeypatch.setattr(database.smtplib, "SMTP", SMTPCheCade)
    assert database.invia_mail("a@b.it", "O", "<p>x</p>", corpo_testo="x") is False
    assert chiamate == ["a@b.it"]


def test_a6_la_firma_resta_compatibile_con_chi_chiama_oggi():
    import inspect
    firma = inspect.signature(database.invia_mail)
    posizionali = [n for n, p in firma.parameters.items()
                   if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    assert posizionali == ["destinatario", "oggetto", "corpo_html", "allegato"]
    for nome in ("corpo_testo", "reply_to", "intestazioni"):
        p = firma.parameters[nome]
        assert p.kind is p.KEYWORD_ONLY and p.default is None


# ---------------------------------------------------------------------------
# B  il testo che diventa html
# ---------------------------------------------------------------------------

def test_b1_escape_di_tutto_e_a_capo_conservati():
    h = email_format.testo_in_html("Ciao <script>alert(1)</script> & \"Rossi\"\n\nriga 1\nriga 2")
    assert "<script>" not in h and "&lt;script&gt;" in h
    assert "&amp;" in h and "&quot;Rossi&quot;" in h
    assert h.count("<p>") == 2 and "riga 1<br>\nriga 2" in h


def test_b2_gli_url_diventano_link_dopo_lescape():
    h = email_format.testo_in_html('Vai su https://x.it/u?t=a&b=c"><i>x</i>. Fine')
    # l'URL si ferma alla virgoletta; quello che segue e' testo, con escape
    assert '<a href="https://x.it/u?t=a&amp;b=c">https://x.it/u?t=a&amp;b=c</a>&quot;&gt;&lt;i&gt;x&lt;/i&gt;. Fine' in h
    assert "<i>" not in h
    h2 = email_format.testo_in_html("Leggi https://x.it/p. Poi (https://y.it).")
    assert '<a href="https://x.it/p">https://x.it/p</a>.' in h2
    assert '<a href="https://y.it">https://y.it</a>).' in h2


def test_b3_gli_argomenti_di_invio_vengono_dai_metadata():
    md = {"body_format": "text", "reply_to": "info@stima360.it",
          "unsubscribe_url": "https://x.it/api/public/communication/unsubscribe?t=abc"}
    a = email_format.argomenti_di_invio("corpo", md)
    assert a == {"corpo_testo": "corpo", "reply_to": "info@stima360.it",
                 "intestazioni": {"List-Unsubscribe": "<https://x.it/api/public/communication/unsubscribe?t=abc>",
                                  "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}}
    # senza url valido: nessuna List-Unsubscribe, e nessuna -Post orfana
    for cattivo in (None, "", "javascript:x", "https://x.it/u?t=a\r\nBcc: x", 12):
        assert "intestazioni" not in email_format.argomenti_di_invio("c", {"unsubscribe_url": cattivo})
    assert email_format.argomenti_di_invio("c", {}) == {"corpo_testo": "c"}
    assert email_format.e_testo({"body_format": "text"}) and not email_format.e_testo({}) \
        and not email_format.e_testo(None) and not email_format.e_testo({"body_format": "html"})


# ---------------------------------------------------------------------------
# C  l'adapter
# ---------------------------------------------------------------------------

@pytest.fixture
def spia(monkeypatch):
    chiamate = []
    monkeypatch.setattr(email_smtp, "invia_mail",
                        lambda *a, **k: (chiamate.append((a, k)), True)[1])
    return chiamate


def _riga(**extra):
    base = {"id": 1, "destination_snapshot": "c@x.it", "subject_snapshot": "S",
            "rendered_body": "<p>html</p>", "metadata": {}}
    base.update(extra)
    return base


def test_c1_un_corpo_html_passa_con_i_tre_argomenti_di_sempre(spia):
    for md in ({}, None, {"pdf_url": "https://x.it/p.pdf"}, {"central_continuity": True}):
        spia.clear()
        assert email_smtp.send(_riga(metadata=md)).outcome == "accepted"
        assert spia == [(("c@x.it", "S", "<p>html</p>"), {})]


def test_c2_un_corpo_di_testo_passa_con_testo_html_e_intestazioni(spia):
    md = email_metadata.metadata_testo(communication_type="marketing",
                                       unsubscribe_url="https://x.it/u?t=1")
    esito = email_smtp.send(_riga(rendered_body="Ciao <b>Anna</b>\n\nhttps://x.it/p", metadata=md))
    assert esito.outcome == "accepted"
    (args, kw), = spia
    assert args[:2] == ("c@x.it", "S")
    assert "&lt;b&gt;Anna&lt;/b&gt;" in args[2] and '<a href="https://x.it/p">' in args[2]
    assert kw["corpo_testo"] == "Ciao <b>Anna</b>\n\nhttps://x.it/p"
    assert kw["reply_to"] == "info@stima360.it"
    assert kw["intestazioni"]["List-Unsubscribe"] == "<https://x.it/u?t=1>"


def test_c3_un_testo_di_servizio_non_porta_reply_to_ne_disiscrizione(spia):
    md = email_metadata.metadata_testo(communication_type="service")
    assert md == {"body_format": "text"}
    email_smtp.send(_riga(rendered_body="ciao", metadata=md))
    (_, kw), = spia
    assert kw == {"corpo_testo": "ciao"}


def test_c4_un_insuccesso_sul_ramo_testo_resta_indeterminato_senza_secondo_invio(monkeypatch):
    chiamate = []

    def esplode(*a, **k):
        chiamate.append(a)
        raise RuntimeError("boom")

    monkeypatch.setattr(email_smtp, "invia_mail", esplode)
    esito = email_smtp.send(_riga(rendered_body="x", metadata={"body_format": "text"}))
    assert esito.outcome == "unknown" and len(chiamate) == 1
    assert email_smtp.CAPABILITIES.distinguishes_failure_class is False


# ---------------------------------------------------------------------------
# D  la configurazione del Reply-To
# ---------------------------------------------------------------------------

def test_d1_reply_to_predefinito_configurabile_e_mai_malformato(monkeypatch):
    monkeypatch.delenv(email_metadata.REPLY_TO_ENV, raising=False)
    assert email_metadata.reply_to_marketing() == "info@stima360.it"
    monkeypatch.setenv(email_metadata.REPLY_TO_ENV, "  test@stima360.it ")
    assert email_metadata.reply_to_marketing() == "test@stima360.it"
    monkeypatch.setenv(email_metadata.REPLY_TO_ENV, "")
    assert email_metadata.reply_to_marketing() == "info@stima360.it"
    for cattivo in ("Nome <a@b.it>", "a@b.it, c@d.it", "a@b.it\nBcc: x", "non-un-indirizzo"):
        monkeypatch.setenv(email_metadata.REPLY_TO_ENV, cattivo)
        assert email_metadata.reply_to_marketing() is None
        md = email_metadata.metadata_testo(communication_type="marketing", unsubscribe_url="https://x.it/u")
        assert "reply_to" not in md and md["unsubscribe_url"] == "https://x.it/u"


# ---------------------------------------------------------------------------
# E  il POST pubblico di disiscrizione (one-click)
# ---------------------------------------------------------------------------

@pytest.fixture
def rotta(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from communication import public_router, unsubscribe

    ricevuti = []
    monkeypatch.setattr(unsubscribe, "process", lambda t: (ricevuti.append(t), False)[1])
    app = FastAPI()
    app.include_router(public_router.router)
    return TestClient(app), ricevuti


def test_e1_il_post_one_click_porta_il_token_dalla_query(rotta):
    client, ricevuti = rotta
    r = client.post("/api/public/communication/unsubscribe?t=tok-query",
                    data={"List-Unsubscribe": "One-Click"})
    assert r.status_code == 200 and ricevuti == ["tok-query"]
    r = client.post("/api/public/communication/unsubscribe?t=tok-query")
    assert r.status_code == 200 and ricevuti == ["tok-query", "tok-query"]


def test_e2_il_form_vince_e_la_get_non_disiscrive(rotta):
    client, ricevuti = rotta
    client.post("/api/public/communication/unsubscribe?t=dalla-query", data={"t": "dal-form"})
    assert ricevuti == ["dal-form"]
    client.post("/api/public/communication/unsubscribe", data={})
    assert ricevuti == ["dal-form", ""]
    r = client.get("/api/public/communication/unsubscribe?t=tok")
    assert r.status_code == 200 and "Conferma disiscrizione" in r.text
    assert ricevuti == ["dal-form", ""], "la GET non esegue"
    assert r.headers.get("cache-control") == "no-store"


# ---------------------------------------------------------------------------
# F  sentinelle di confine sui file nuovi
# ---------------------------------------------------------------------------

def _codice(percorso: Path) -> str:
    testo = percorso.read_text(encoding="utf-8")
    testo = re.sub(r'"{3}[\s\S]*?"{3}', "", testo)
    return re.sub(r"#[^\n]*", "", testo)


def test_f1_i_file_nuovi_rispettano_i_confini_del_dominio():
    formato = _codice(ROOT / "communication" / "providers" / "email_format.py")
    for vietato in ("requests", "smtplib", "httpx", "urllib", "http.client", "socket", "aiohttp",
                    "os.getenv", "cur.execute", "SELECT", "agency_id", "ctx", "invia_mail", "SMTP_"):
        assert vietato not in formato, vietato
    metadati = _codice(ROOT / "communication" / "email_metadata.py")
    for vietato in ("providers", "invia_mail", "SMTP_", "smtplib", "cur.execute"):
        assert vietato not in metadati, vietato
    adapter = _codice(ROOT / "communication" / "providers" / "email_smtp.py")
    assert "os.getenv" not in adapter



# ---------------------------------------------------------------------------
# G  con PostgreSQL: dal passo di sequenza alla primitiva
# ---------------------------------------------------------------------------

pg = pytest.mark.skipif(not os.getenv("P29_TEST_DSN"), reason="P29_TEST_DSN non impostata")

if os.getenv("P29_TEST_DSN"):
    from tests.test_adm3_bis_central_followup_postgres import (  # noqa: F401,E402
        PASSI, _normale, _passi, _riga as _riga_db, _stima_in_coda, db, iscrizioni,
        journey_attiva, modulo, mondo,
    )


@pg
def test_g1_un_passo_di_sequenza_parte_come_testo_con_reply_to_e_disiscrizione(
        mondo, modulo, monkeypatch):
    chiamate = []
    monkeypatch.setattr(email_smtp, "invia_mail",
                        lambda *a, **k: (chiamate.append((a, k)), True)[1])
    monkeypatch.delenv(email_metadata.REPLY_TO_ENV, raising=False)
    a = mondo["a"]
    journey_attiva(modulo, mondo["ctx"](), passi=PASSI)
    c = mondo["contatto"]()
    stima = _stima_in_coda(mondo, c)
    modulo["tick"].tick(mondo["ctx"]())
    m1 = _passi(mondo, a)[0]
    assert m1["metadata"]["body_format"] == "text"
    assert m1["metadata"]["reply_to"] == "info@stima360.it"
    assert m1["metadata"]["unsubscribe_url"].startswith("https://") \
        and "/api/public/communication/unsubscribe?t=" in m1["metadata"]["unsubscribe_url"]
    assert "central_continuity" not in m1["metadata"]

    conteggi = _normale(mondo, a)
    assert conteggi["sent"] == 2 and len(chiamate) == 2
    per_oggetto = {args[1]: (args, kw) for args, kw in chiamate}
    args, kw = per_oggetto[m1["subject_snapshot"]]
    assert kw["corpo_testo"] == m1["rendered_body"]
    assert kw["reply_to"] == "info@stima360.it"
    assert kw["intestazioni"]["List-Unsubscribe"] == f"<{m1['metadata']['unsubscribe_url']}>"
    assert "<div" in args[2]
    # la email di servizio della stima: i tre argomenti di sempre, nient'altro
    args_s, kw_s = per_oggetto["Oggetto"]
    assert kw_s == {} and args_s[2] == "corpo"
    assert _riga_db(mondo, stima)["status"] == "sent"
