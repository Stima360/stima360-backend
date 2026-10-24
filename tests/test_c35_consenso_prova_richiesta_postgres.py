"""C3.5 - la prova del consenso marketing scritta con la richiesta.

`POST /api/salva_stima` vero (fixture `sito`), PostgreSQL usa-e-getta, dati
fittizi, nessun invio reale. La pagina modificata del sito
(`site-preview/c35-consenso/dati_personali.html`) manda con la scelta il testo
della casella e l'impronta SHA-256 di `privacy.html`; il server li scrive nella
riga `stime` (112) SOLO se coincidono con le versioni ufficiali conservate sul
server (`site_official`); il CRM ripete il confronto e cerca le
informative registrate.

  01  la richiesta conserva scelta, data, testo e impronta; la risposta della
      valutazione e' la stessa di una richiesta senza prova.
  02  testo e informativa privacy registrati nel CRM: Email e WhatsApp
      «concesso dal sito» subito, con la prova nella nota; SMS e telefonate mai.
      Vale per OGNI richiesta: piu' persone, stessa regola.
  03  le informative registrate DOPO la richiesta: la prova e' la richiesta,
      non la data della riga del CRM; si riconosce alla prima lettura.
  04  casella non spuntata: testo conservato, nessuna autorizzazione.
  05  testo diverso, informativa privacy non registrata, impronta mancante:
      prova incompleta, scelta conservata, invii bloccati; la scheda dice cosa.
  06  revoca dopo il riconoscimento e disiscrizione da tutta la piattaforma
      prevalgono, a ogni nuova lettura.
  07  sito vecchio (senza prova) e campi malformati: la stima procede uguale,
      le colonne restano vuote, niente si inventa.
  08  database senza la 112: la stima procede, la prova manca.
  09  il testo dell'informativa privacy incollato con ritorni a capo CRLF.
  10  valori del browser che non sono versioni ufficiali (testo alterato,
      privacy storica, impronta inventata): non si conservano, nessun consenso.
  11  rollback: la 112 tolta con la sua down e rimessa; la stima procede in
      entrambi i casi e i dati delle altre colonne restano.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_catalogo_canonico_1_postgres import COMPLETA, DSN, _persona, completo, mondo, sito  # noqa: F401
from tests.test_censimento_3_backend_postgres import _q
from tests.test_fase_b_followup_postgres import _guardia, _sistema, fb  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

ROOT = Path(__file__).resolve().parents[1]
PRIVACY = (ROOT / "site-preview" / "c35-consenso" / "privacy.html").read_bytes()
IMPRONTA = hashlib.sha256(PRIVACY).hexdigest()


def _testo_casella():
    from consent.site_evidence import site_checkbox_evidence
    return site_checkbox_evidence()["text"]


@pytest.fixture
def c35(fb, sito):
    s = sito
    s.fb = fb
    return s


def _adesso():
    return datetime.now(timezone.utc)


def _informativa_marketing(s, *, testo=None, canali=("email", "whatsapp"), registrata=None):
    testo = testo or _testo_casella()
    quando = registrata or (_adesso() - timedelta(hours=1))
    return _q(s.m, "INSERT INTO consent_notices (purpose, version, content, content_hash, content_available, "
                   "context, valid_from, valid_to, created_at, created_by, channels) VALUES ('marketing', %s, %s, %s, "
                   "TRUE, 'Sito www.stima360.it - casella marketing del modulo di valutazione', %s, %s, %s, 'test', %s) "
                   "RETURNING id",
              (f"sito-{uuid.uuid4().hex[:8]}", testo, hashlib.sha256(testo.encode()).hexdigest(),
               quando, quando + timedelta(days=2), quando, list(canali)))[0][0]


def _informativa_privacy(s, contenuto: bytes = PRIVACY, registrata=None):
    testo = contenuto.decode("utf-8")
    quando = registrata or (_adesso() - timedelta(hours=1))
    return _q(s.m, "INSERT INTO consent_notices (purpose, version, content, content_hash, content_available, "
                   "context, valid_from, valid_to, created_at, created_by) VALUES ('privacy_terms', %s, %s, %s, TRUE, "
                   "'Sito www.stima360.it - privacy.html', %s, %s, %s, 'test') RETURNING id",
              (f"privacy-{uuid.uuid4().hex[:8]}", testo, hashlib.sha256(testo.encode()).hexdigest(),
               quando, quando + timedelta(days=2), quando))[0][0]


def _versione_ufficiale_in_piu(monkeypatch, *, testo=None, impronta=None):
    """Una versione ufficiale ACCETTATA in piu' (solo per questo test), che
    nessuna informativa del CRM puo' ancora avere: per provare il caso
    «ufficiale ma non registrata nel CRM» senza dipendere dalle informative
    lasciate da altri test nel database condiviso."""
    import site_official
    vere = site_official.official_versions()
    extra = {"marketing_checkbox": list(vere["marketing_checkbox"]), "privacy": list(vere["privacy"])}
    if testo:
        extra["marketing_checkbox"].append({"version": "test-casella", "text": testo,
                                            "channels": ["email", "whatsapp"], "accepted": True})
    if impronta:
        extra["privacy"].append({"version": "test-privacy", "sha256": impronta, "file": "-", "accepted": True})
    monkeypatch.setattr(site_official, "official_versions", lambda: extra)


def _richiesta(s, *, spuntata=True, testo="__casella__", impronta=IMPRONTA, **persona):
    payload = {**COMPLETA, **_persona(**persona), "consenso_marketing": spuntata}
    if testo is not None:
        payload["consenso_marketing_testo"] = _testo_casella() if testo == "__casella__" else testo
    if impronta is not None:
        payload["informativa_privacy_sha256"] = impronta
    return s.stima(payload)


def _riga(s, sid):
    r = _q(s.m, "SELECT consenso_marketing, consenso_marketing_at, consenso_marketing_testo, "
                "consenso_marketing_privacy_sha256 FROM stime WHERE id = %s", (sid,))[0]
    return {"scelta": r[0], "data": r[1], "testo": r[2], "impronta": r[3]}


def _contatto(s, sid):
    return _q(s.m, "SELECT l.contact_id FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id "
                   "WHERE ls.stima_id = %s", (sid,))[0][0]


def _eventi(s, cid):
    return [tuple(r) for r in _q(s.m, "SELECT channel, decision, source, evidence_ref, notice_id, note "
                                      "FROM consent_events WHERE contact_id = %s ORDER BY id", (cid,))]


def _scheda(s, cid):
    """La stessa lettura della scheda e dell'import: valuta e riconosce."""
    from consent import site_recognition
    from operator_auth.context import SystemAgencyContext
    return site_recognition.recognize(SystemAgencyContext(agency_id=1, origin="public_stima"), cid)


def _consentiti(s, cid):
    return {c: _guardia(s.fb, 1, cid, c).allowed for c in ("email", "whatsapp", "sms", "phone")}


def test_01_la_richiesta_conserva_scelta_data_testo_e_impronta(c35):
    s = c35
    con = _richiesta(s)
    senza = _richiesta(s, testo=None, impronta=None)
    riga = _riga(s, con["id"])
    assert riga["scelta"] is True and riga["data"] is not None
    assert abs((_adesso() - riga["data"]).total_seconds()) < 300, "la data e' quella del server, adesso"
    assert riga["testo"] == _testo_casella()
    assert "tramite email e WhatsApp" in riga["testo"]
    assert riga["impronta"] == IMPRONTA
    # la valutazione non cambia: stessa risposta, stessi importi
    assert set(con) == set(senza)
    for chiave in ("price_exact", "eur_mq_finale"):
        assert con.get(chiave) == senza.get(chiave), chiave
    assert _riga(s, senza["id"])["testo"] is None and _riga(s, senza["id"])["impronta"] is None


def test_02_concesso_dal_sito_per_ogni_richiesta_mai_sms(c35):
    s = c35
    informativa = _informativa_marketing(s, canali=("email", "whatsapp", "sms"))
    privacy = _informativa_privacy(s)
    persone = [_richiesta(s, nome=n) for n in ("Serena", "Luca", "Giulia")]
    for r in persone:
        cid = _contatto(s, r["id"])
        assert _consentiti(s, cid) == {"email": True, "whatsapp": True, "sms": False, "phone": False}, r
        eventi = [e for e in _eventi(s, cid) if e[0] is not None]
        assert sorted(e[0] for e in eventi) == ["email", "whatsapp"]
        for e in eventi:
            assert e[1:5] == ("granted", "public_stima", str(r["id"]), informativa)
            assert IMPRONTA in e[5] and "registrati dal server con la richiesta" in e[5]
        tick = _scheda(s, cid)["assessment"]["ticks"][0]
        assert tick["proof"]["kind"] == "submission_record"
        assert tick["proof"]["privacy_notice"]["id"] == privacy
        assert tick["channels"]["email"]["status"] == "recognized"


def test_03_informative_registrate_dopo_la_richiesta(c35, monkeypatch):
    s = c35
    # una versione ufficiale della privacy che nel CRM non e' ancora registrata
    privacy = PRIVACY + f"<!-- {uuid.uuid4().hex} -->\n".encode()
    _versione_ufficiale_in_piu(monkeypatch, impronta=hashlib.sha256(privacy).hexdigest())
    _informativa_marketing(s)
    r = _richiesta(s, nome="Paolo", impronta=hashlib.sha256(privacy).hexdigest())
    cid = _contatto(s, r["id"])
    assert _consentiti(s, cid)["email"] is False
    assert _scheda(s, cid)["assessment"]["ticks"][0]["missing"] == ["recorded_privacy_unregistered"]
    _informativa_privacy(s, contenuto=privacy, registrata=_adesso())
    esito = _scheda(s, cid)
    assert sorted(g["channel"] for g in esito["granted"]) == ["email", "whatsapp"]
    assert _consentiti(s, cid) == {"email": True, "whatsapp": True, "sms": False, "phone": False}


def test_04_casella_non_spuntata_nessuna_autorizzazione(c35):
    s = c35
    _informativa_marketing(s)
    _informativa_privacy(s)
    r = _richiesta(s, spuntata=False, nome="Anna")
    riga = _riga(s, r["id"])
    assert riga["scelta"] is False and riga["data"] is None
    assert riga["testo"] == _testo_casella() and riga["impronta"] == IMPRONTA
    cid = _contatto(s, r["id"])
    assert all(e[0] is None for e in _eventi(s, cid))
    assert not any(e[1] == "granted" and e[0] is not None for e in _eventi(s, cid))
    assert _consentiti(s, cid) == {"email": False, "whatsapp": False, "sms": False, "phone": False}
    assert _scheda(s, cid)["assessment"]["ticks"] == []


def test_05_prova_incompleta_scelta_conservata_invii_bloccati(c35, monkeypatch):
    s = c35
    _informativa_marketing(s)
    _informativa_privacy(s)
    testo_nuovo = f"Acconsento a ricevere aggiornamenti da Stima360 tramite email e WhatsApp. {uuid.uuid4().hex}"
    impronta_nuova = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    _versione_ufficiale_in_piu(monkeypatch, testo=testo_nuovo, impronta=impronta_nuova)
    casi = {
        "recorded_text_unregistered": _richiesta(s, testo=testo_nuovo, nome="T1"),
        "recorded_privacy_unregistered": _richiesta(s, impronta=impronta_nuova, nome="T2"),
        "recorded_privacy": _richiesta(s, impronta="", nome="T3"),
        "recorded_text": _richiesta(s, testo=None, impronta=IMPRONTA, nome="T4"),
    }
    for mancante, r in casi.items():
        cid = _contatto(s, r["id"])
        tick = _scheda(s, cid)["assessment"]["ticks"][0]
        assert tick["missing"] == [mancante], (mancante, tick)
        assert tick["missing_labels"] and tick["channels"]["email"]["status"] == "incomplete"
        assert [e[:2] for e in _eventi(s, cid)] == [(None, "granted")], "la scelta resta"
        assert _consentiti(s, cid) == {"email": False, "whatsapp": False, "sms": False, "phone": False}


def test_06_revoca_e_disiscrizione_prevalgono(c35):
    from consent import service as cs
    s = c35
    _informativa_marketing(s)
    _informativa_privacy(s)
    r = _richiesta(s, nome="Revoca")
    cid = _contatto(s, r["id"])
    assert _consentiti(s, cid)["whatsapp"] is True
    cs.record_revocation(_sistema(1), contact_id=cid, purpose="marketing", channel="whatsapp",
                         source="unsubscribe_link", actor_type="subject", decided_at=_adesso(),
                         idempotency_key=f"rev:{cid}:wa")
    for _ in range(2):
        assert _scheda(s, cid)["granted"] == []
    assert _guardia(s.fb, 1, cid, "whatsapp").reason == "deny_revoked"
    assert _consentiti(s, cid)["email"] is True
    # disiscritto da tutta la piattaforma PRIMA della richiesta: email mai riconosciuta
    persona = _persona(nome="Disiscritta")
    _q(s.m, "INSERT INTO communication_optouts (channel, address_normalized, opted_out_at, source, actor_type, "
            "idempotency_key) VALUES ('email', %s, NOW(), 'unsubscribe_link', 'subject', %s)",
       (persona["email"].lower(), f"opt:{uuid.uuid4().hex}"))
    r2 = s.stima({**COMPLETA, **persona, "consenso_marketing": True, "consenso_marketing_testo": _testo_casella(),
                  "informativa_privacy_sha256": IMPRONTA})
    cid2 = _contatto(s, r2["id"])
    tick = _scheda(s, cid2)["assessment"]["ticks"][0]
    assert tick["channels"]["email"]["status"] == "platform_optout"
    assert _consentiti(s, cid2) == {"email": False, "whatsapp": True, "sms": False, "phone": False}


def test_07_sito_vecchio_e_campi_malformati_niente_si_inventa(c35):
    s = c35
    vecchio = _richiesta(s, testo=None, impronta=None, nome="Vecchio")
    storta = _richiesta(s, testo="x" * 2000, impronta="NON-UN-HASH", nome="Storta")
    for r in (vecchio, storta):
        riga = _riga(s, r["id"])
        assert riga["scelta"] is True and riga["data"] is not None
        assert riga["testo"] is None and riga["impronta"] is None
        tick = _scheda(s, _contatto(s, r["id"]))["assessment"]["ticks"][0]
        # nessuna prova scritta: valgono solo le regole per data di C3.4 (mai la 112)
        assert tick["record"] is None
        assert (tick["proof"] or {}).get("kind") in (None, "registered", "site_capture"), tick
    assert set(vecchio) == set(storta)
    assert vecchio.get("price_exact") == storta.get("price_exact")


def test_08_senza_la_112_la_stima_procede(c35):
    s = c35
    _q(s.m, "ALTER TABLE stime RENAME COLUMN consenso_marketing_testo TO c35_testo_assente")
    try:
        r = _richiesta(s, nome="Senza112")
        assert r.get("id") and r.get("price_exact")
        riga = _q(s.m, "SELECT consenso_marketing, c35_testo_assente FROM stime WHERE id = %s", (r["id"],))[0]
        assert riga[0] is True and riga[1] is None
        tick = _scheda(s, _contatto(s, r["id"]))["assessment"]["ticks"][0]
        assert tick["record"] is None
    finally:
        _q(s.m, "ALTER TABLE stime RENAME COLUMN c35_testo_assente TO consenso_marketing_testo")


def test_09_privacy_incollata_con_ritorni_a_capo_crlf(c35, monkeypatch):
    s = c35
    privacy = PRIVACY + f"<!-- crlf {uuid.uuid4().hex} -->\n".encode()
    _versione_ufficiale_in_piu(monkeypatch, impronta=hashlib.sha256(privacy).hexdigest())
    _informativa_marketing(s)
    _informativa_privacy(s, contenuto=privacy.replace(b"\n", b"\r\n"))
    r = _richiesta(s, nome="Crlf", impronta=hashlib.sha256(privacy).hexdigest())
    assert _consentiti(s, _contatto(s, r["id"]))["email"] is True


def test_10_valori_del_browser_non_ufficiali_non_si_conservano(c35):
    s = c35
    _informativa_marketing(s)
    _informativa_privacy(s)
    storica = hashlib.sha256((ROOT / "site_official" / "versions" / "privacy-d615e05d.html").read_bytes()).hexdigest()
    casi = {
        "alterato": _richiesta(s, testo=_testo_casella().replace("email e WhatsApp", "email, SMS e WhatsApp"),
                               nome="Alterato"),
        "privacy_storica": _richiesta(s, impronta=storica, nome="Storica"),
        "impronta_inventata": _richiesta(s, impronta="b" * 64, nome="Inventata"),
    }
    attesi = {"alterato": (None, IMPRONTA, "recorded_text"),
              "privacy_storica": (_testo_casella(), None, "recorded_privacy"),
              "impronta_inventata": (_testo_casella(), None, "recorded_privacy")}
    for nome, r in casi.items():
        riga = _riga(s, r["id"])
        testo, impronta, mancante = attesi[nome]
        assert (riga["testo"], riga["impronta"]) == (testo, impronta), nome
        assert riga["scelta"] is True and riga["data"] is not None, "la scelta resta"
        cid = _contatto(s, r["id"])
        assert _scheda(s, cid)["assessment"]["ticks"][0]["missing"] == [mancante], nome
        assert _consentiti(s, cid) == {"email": False, "whatsapp": False, "sms": False, "phone": False}, nome


def test_11_rollback_down_e_di_nuovo_up(c35):
    s = c35
    giu = (ROOT / "migrations" / "112_stime_consenso_marketing_prova_down.sql").read_text(encoding="utf-8")
    su = (ROOT / "migrations" / "112_stime_consenso_marketing_prova.sql").read_text(encoding="utf-8")
    prima = _richiesta(s, nome="PrimaDelRollback")
    altre = _q(s.m, "SELECT comune, mq, email, consenso_marketing, consenso_marketing_at FROM stime WHERE id = %s",
               (prima["id"],))[0]
    _q(s.m, giu)
    try:
        colonne = {r[0] for r in _q(s.m, "SELECT column_name FROM information_schema.columns "
                                         "WHERE table_name = 'stime'")}
        assert not {"consenso_marketing_testo", "consenso_marketing_privacy_sha256"} & colonne
        assert _q(s.m, "SELECT comune, mq, email, consenso_marketing, consenso_marketing_at FROM stime "
                       "WHERE id = %s", (prima["id"],))[0] == altre, "le altre colonne restano"
        durante = _richiesta(s, nome="DuranteIlRollback")
        assert durante.get("id") and durante.get("price_exact") == prima.get("price_exact")
        assert _scheda(s, _contatto(s, durante["id"]))["assessment"]["ticks"][0]["record"] is None
    finally:
        _q(s.m, su)
    dopo = _richiesta(s, nome="DopoIlRollback")
    assert _riga(s, dopo["id"])["impronta"] == IMPRONTA
    with pytest.raises(Exception):
        _q(s.m, "UPDATE stime SET consenso_marketing_privacy_sha256 = 'non-un-hash' WHERE id = %s", (dopo["id"],))
