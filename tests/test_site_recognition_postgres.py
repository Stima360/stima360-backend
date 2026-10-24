"""C3.4 - il riconoscimento dei consensi marketing espressi sul sito.

PostgreSQL usa-e-getta (fixture `fb`), dati fittizi. La spunta del sito e'
l'evento SENZA canale che il bridge pubblico scrive (fonte `public_stima`,
prova `stima:<id>`, data del server del sito).

  01  senza informativa del sito: prova incompleta, niente concesso, la
      scheda dice cosa manca; la spunta resta.
  02  informativa registrata DOPO la spunta, anche retrodatata: non vale.
  03  informativa del sito registrata PRIMA della spunta (email+whatsapp+sms):
      email e whatsapp «concesso dal sito» con data della spunta, richiesta e
      informativa come prove; sms mai; idempotente; la guardia autorizza.
  04  informativa che copre solo l'email: whatsapp «non coperto».
  05  una revoca successiva prevale: niente riconoscimento, guardia nega.
  06  un'informativa che non e' del sito (contesto del QR) non vale.
  07  solo il vecchio flag: prova incompleta, niente concesso.
  08  la lettura dello stato (GET consent) riconosce da sola.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo  # noqa: F401
from tests.test_delete_arch_0_postgres import mondo, operatori  # noqa: F401
from tests.test_fase_b_followup_postgres import _contatto, _ctx, _guardia, _sistema, fb  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")



def _quando(n):
    """Una finestra di date PROPRIA di ogni test (gennaio 2026, nel passato):
    le informative sono un registro e restano; finestre separate evitano che
    quella di un test risulti valida alla spunta di un altro."""
    return datetime(2026, 1, n, 13, 5, tzinfo=timezone.utc)


SPUNTA = _quando(2)


def _spunta_sito(m, email, *, quando=SPUNTA, telefono="393331234567"):
    """Un contatto come lo crea il bridge del sito: spunta senza canale, con
    la richiesta di valutazione come prova."""
    from consent import service as cs
    cid = _contatto(m, email)
    _q(m, "UPDATE contacts SET phone = '333 123 4567', phone_normalized = %s, source = 'public_stima' WHERE id = %s",
       (telefono, cid))
    stima = _q(m, "INSERT INTO stime (agency_id, comune, via, mq) VALUES (1, 'Alba', 'Via Sito', 80) RETURNING id")[0][0]
    cs.record_grant(_sistema(1), contact_id=cid, purpose="marketing", source="public_stima", actor_type="subject",
                    decided_at=quando, evidence_type="stima", evidence_ref=str(stima),
                    idempotency_key=f"public_stima:{stima}:marketing")
    return cid, stima


TESTO_CASELLA = "Acconsento a ricevere analisi di mercato e aggiornamenti da Stima360 tramite email e WhatsApp."


def _informativa_sito(m, *, registrata, valida_da=None, canali=("email", "whatsapp", "sms"),
                      contesto="Sito www.stima360.it - casella marketing del modulo di valutazione",
                      testo=TESTO_CASELLA):
    versione = f"sito-{uuid.uuid4().hex[:8]}"
    return _q(m, "INSERT INTO consent_notices (purpose, version, content, content_hash, content_available, context, "
                 "valid_from, valid_to, created_at, created_by, channels) VALUES ('marketing', %s, %s, "
                 "repeat('d', 64), TRUE, %s, %s, %s, %s, 'test', %s) RETURNING id",
              (versione, testo, contesto, valida_da or registrata, (valida_da or registrata) + timedelta(days=1),
               registrata, list(canali)))[0][0]


def _chiudi_informative(m):
    _q(m, "UPDATE consent_notices SET valid_to = GREATEST(valid_from, NOW() - interval '1 second') "
          "WHERE purpose = 'marketing' AND valid_to IS NULL")


@pytest.fixture
def sr(fb):
    _chiudi_informative(fb)
    yield fb
    _chiudi_informative(fb)


def _riconosci(m, cid):
    from consent import site_recognition
    return site_recognition.recognize(_ctx(m, "admin_a"), cid)


def _scheda(m, cid):
    from consent import operator_service
    return operator_service.marketing_state(_ctx(m, "admin_a"), cid)


def _eventi(m, cid):
    return [tuple(r) for r in _q(m, "SELECT channel, decision, source, evidence_type, evidence_ref, notice_id, "
                                     "decided_at, idempotency_key FROM consent_events WHERE contact_id = %s "
                                     "ORDER BY id", (cid,))]


def test_01_senza_informativa_del_sito_prova_incompleta(sr):
    m = sr
    cid, stima = _spunta_sito(m, "incompleta@example.it", quando=_quando(2))
    esito = _riconosci(m, cid)
    assert esito["granted"] == []
    val = esito["assessment"]["ticks"]
    assert len(val) == 1 and val[0]["stima_id"] == stima and val[0]["missing"] == ["notice"]
    assert val[0]["channels"]["email"]["status"] == "incomplete"
    assert "informativa del sito valida alla data della spunta" in val[0]["missing_labels"][0]
    for c in ("email", "whatsapp", "sms", "phone"):
        assert not _guardia(m, 1, cid, c).allowed, c
    # la spunta resta: un solo evento, senza canale
    assert [e[:2] for e in _eventi(m, cid)] == [(None, "granted")]
    scheda = _scheda(m, cid)
    assert scheda["site_record"]["recognition"]["ticks"][0]["missing"] == ["notice"]


def test_02_informativa_registrata_dopo_anche_retrodatata_non_vale(sr):
    m = sr
    cid, _ = _spunta_sito(m, "dopo@example.it", quando=_quando(4))
    _informativa_sito(m, registrata=_quando(4) + timedelta(days=1), valida_da=_quando(4) - timedelta(days=10))
    esito = _riconosci(m, cid)
    assert esito["granted"] == [] and esito["assessment"]["ticks"][0]["missing"] == ["notice"]
    assert not _guardia(m, 1, cid).allowed


def test_03_informativa_del_sito_valida_alla_spunta_concesso_dal_sito(sr):
    m = sr
    informativa = _informativa_sito(m, registrata=_quando(6) - timedelta(hours=5))
    cid, stima = _spunta_sito(m, "valida@example.it", quando=_quando(6))
    esito = _riconosci(m, cid)
    assert sorted(g["channel"] for g in esito["granted"]) == ["email", "whatsapp"]
    assert esito["assessment"]["ticks"][0]["notice"]["id"] == informativa
    eventi = _eventi(m, cid)
    assert eventi[0][:2] == (None, "granted")
    for canale in ("email", "whatsapp"):
        riga = next(e for e in eventi if e[0] == canale)
        assert riga[1:] == ("granted", "public_stima", "stima", str(stima), informativa, _quando(6),
                            f"public_stima:{stima}:marketing:{canale}")
    assert not any(e[0] in ("sms", "phone") for e in eventi), "SMS e telefonate non sono nella casella"
    assert _guardia(m, 1, cid, "email").allowed and _guardia(m, 1, cid, "whatsapp").allowed
    assert not _guardia(m, 1, cid, "sms").allowed and not _guardia(m, 1, cid, "phone").allowed
    # idempotente
    assert _riconosci(m, cid)["granted"] == [] and len(_eventi(m, cid)) == 3
    scheda = _scheda(m, cid)
    assert scheda["channels"]["email"]["last_event"]["source"] == "public_stima"
    assert scheda["channels"]["email"]["last_event"]["evidence_type"] == "stima"
    assert scheda["site_record"]["recognition"]["ticks"][0]["channels"]["whatsapp"]["status"] == "recognized"


def test_04_informativa_solo_email_whatsapp_non_coperto(sr):
    m = sr
    _informativa_sito(m, registrata=_quando(8) - timedelta(hours=5), canali=("email",))
    cid, _ = _spunta_sito(m, "soloemail@example.it", quando=_quando(8))
    esito = _riconosci(m, cid)
    assert [g["channel"] for g in esito["granted"]] == ["email"]
    assert esito["assessment"]["ticks"][0]["channels"]["whatsapp"]["status"] == "not_covered"
    assert _guardia(m, 1, cid, "email").allowed and not _guardia(m, 1, cid, "whatsapp").allowed


def test_05_una_revoca_successiva_prevale(sr):
    from consent import service as cs
    m = sr
    _informativa_sito(m, registrata=_quando(10) - timedelta(hours=5))
    cid, _ = _spunta_sito(m, "revocata@example.it", quando=_quando(10))
    cs.record_revocation(_sistema(1), contact_id=cid, purpose="marketing", channel="email",
                         source="unsubscribe_link", actor_type="subject", decided_at=_quando(10) + timedelta(days=1),
                         idempotency_key=f"rev:{cid}")
    esito = _riconosci(m, cid)
    assert [g["channel"] for g in esito["granted"]] == ["whatsapp"]
    assert esito["assessment"]["ticks"][0]["channels"]["email"]["status"] == "superseded"
    assert _guardia(m, 1, cid, "email").reason == "deny_revoked"
    assert _guardia(m, 1, cid, "whatsapp").allowed


def test_06_un_informativa_che_non_e_del_sito_non_vale(sr):
    m = sr
    _informativa_sito(m, registrata=_quando(12) - timedelta(hours=5), contesto="Link personale / QR dal CRM")
    cid, _ = _spunta_sito(m, "qrnotice@example.it", quando=_quando(12))
    assert _riconosci(m, cid)["granted"] == []
    assert not _guardia(m, 1, cid).allowed


def test_07_solo_il_vecchio_flag_prova_incompleta(sr):
    m = sr
    _informativa_sito(m, registrata=_quando(14) - timedelta(hours=5))
    cid = _contatto(m, "flag@example.it", consenso="legacy")
    _q(m, "UPDATE contacts SET source = 'public_stima' WHERE id = %s", (cid,))
    esito = _riconosci(m, cid)
    assert esito["granted"] == [] and esito["assessment"]["legacy_only"]
    assert "solo il vecchio flag" in esito["assessment"]["legacy_missing_labels"][0]
    assert not _guardia(m, 1, cid).allowed
    assert _scheda(m, cid)["site_record"]["recognition"]["legacy_only"]


def test_08_la_lettura_dello_stato_riconosce_da_sola(sr):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from communication.router import router
    from operator_auth.dependencies import legacy_basic_agency_context
    m = sr
    _informativa_sito(m, registrata=_quando(16) - timedelta(hours=5))
    cid, _ = _spunta_sito(m, "get@example.it", quando=_quando(16))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[legacy_basic_agency_context] = lambda: _ctx(m, "admin_a")
    corpo = TestClient(app).get(f"/api/communication/contacts/{cid}/consent").json()
    assert corpo["channels"]["email"]["allowed"] and corpo["channels"]["whatsapp"]["allowed"]
    assert corpo["channels"]["email"]["last_event"]["source"] == "public_stima"
    assert corpo["site_record"]["recognition"]["ticks"][0]["channels"]["email"]["status"] == "recognized"


# ---------------------------------------------------------------------------
# La prova storica: la copia datata e firmata del sito (consent.site_evidence)
# ---------------------------------------------------------------------------

SERENA = datetime(2026, 10, 22, 13, 5, tzinfo=timezone.utc)
COPIA = datetime(2026, 10, 7, tzinfo=timezone.utc)
FINE = datetime(2026, 10, 23, tzinfo=timezone.utc)    # nel passato: non resta corrente


def _informativa_copia(m, *, valida_da=COPIA, valida_fino=FINE, testo=None):
    """La versione registrata ADESSO (dopo la spunta) con il testo esatto
    della casella, dichiarata valida dalla data della copia del sito."""
    from consent.site_evidence import site_checkbox_evidence
    testo = testo or site_checkbox_evidence()["text"]
    return _q(m, "INSERT INTO consent_notices (purpose, version, content, content_hash, content_available, context, "
                 "valid_from, valid_to, created_by, channels) VALUES ('marketing', %s, %s, repeat('f', 64), TRUE, "
                 "'Sito www.stima360.it - casella marketing del modulo di valutazione', %s, %s, 'test', "
                 "ARRAY['email','whatsapp']) RETURNING id",
              (f"sito-copia-{uuid.uuid4().hex[:6]}", testo, valida_da, valida_fino))[0][0]


def _impronta_copia():
    import json
    from consent import site_evidence
    prov = json.loads(site_evidence.PROVENANCE.read_text(encoding="utf-8"))
    return next(p["sha256"] for p in prov["pages"] if p["url"].endswith("/dati_personali.html"))


def _prove(tmp_path, monkeypatch, voci):
    """Un `html-provenance.json` VERO ma temporaneo: la copia firmata del
    repository (stessa pagina, stessa impronta) piu' le voci di prova date.
    Si esercita il parser reale di `consent.site_evidence`, non un finto."""
    import json
    import shutil
    from consent import site_evidence
    originale = json.loads(site_evidence.PROVENANCE.read_text(encoding="utf-8"))
    pagina = next(p for p in originale["pages"] if p["url"].endswith("/dati_personali.html"))
    (tmp_path / "baseline").mkdir()
    shutil.copy(site_evidence.PROVENANCE.parent / pagina["baseline_path"], tmp_path / pagina["baseline_path"])
    prov = {**originale, "pages": [{**pagina, "evidence": voci}]}
    (tmp_path / "html-provenance.json").write_text(json.dumps(prov), encoding="utf-8")
    monkeypatch.setattr(site_evidence, "PROVENANCE", tmp_path / "html-provenance.json")
    site_evidence.site_checkbox_evidence.cache_clear()


@pytest.fixture
def prove_pulite():
    from consent import site_evidence
    vera = site_evidence.site_checkbox_evidence      # un test puo' sostituirla
    vera.cache_clear()
    yield
    vera.cache_clear()


def test_09_serena_con_la_sola_copia_del_7_10_resta_incompleta_e_dice_cosa_manca(sr, prove_pulite):
    """La copia firmata prova il testo il 7/10, non il 22/10: la spunta di
    Serena resta «prova incompleta», con la prova mancante detta per nome."""
    from consent.site_evidence import site_checkbox_evidence
    m = sr
    prova = site_checkbox_evidence()
    assert prova and [(f["kind"], f"{f['from']:%Y-%m-%d}") for f in prova["windows"]] == [("site_copy", "2026-10-07")]
    assert "email e WhatsApp" in prova["text"]
    cid, _ = _spunta_sito(m, "serena.copia@example.it", quando=SERENA)
    _informativa_copia(m, valida_da=SERENA - timedelta(days=1))   # registrata ADESSO, testo esatto
    esito = _riconosci(m, cid)
    assert esito["granted"] == []
    val = esito["assessment"]["ticks"][0]
    assert val["missing"] == ["text_proof"]
    etichetta = val["missing_labels"][0]
    assert "22/10/2026" in etichetta and "copia firmata del sito 07/10/2026" in etichetta, etichetta
    assert "backup datato di quel giorno o uno storico delle pubblicazioni" in etichetta, etichetta
    for c in ("email", "whatsapp", "sms", "phone"):
        assert not _guardia(m, 1, cid, c).allowed, c
    assert [e[:2] for e in _eventi(m, cid)] == [(None, "granted")], "la scelta resta conservata"


def test_09b_due_copie_identiche_e_last_modified_non_provano_il_22_10(sr, tmp_path, monkeypatch, prove_pulite):
    """Una seconda copia identica del 23/10 prova il 23/10, NON l'intervallo;
    un Last-Modified non e' una prova; una voce senza riferimento al
    documento, o con un'impronta diversa, e' ignorata."""
    m = sr
    impronta = _impronta_copia()
    _prove(tmp_path, monkeypatch, [
        {"kind": "site_copy", "date": "2026-10-23", "sha256": impronta, "reference": "seconda GET del 23/10"},
        {"kind": "backup", "date": "2026-10-23", "sha256": impronta, "reference": "backup hosting 23/10"},
        {"kind": "last_modified", "date": "2026-10-01", "sha256": impronta, "reference": "Last-Modified: 1/10"},
        {"kind": "backup", "date": "2026-10-22", "sha256": "0" * 64, "reference": "backup 22/10 di un'altra pagina"},
        {"kind": "backup", "date": "2026-10-22", "sha256": impronta, "reference": ""},
        {"kind": "publication_history", "from": "2026-10-23", "to": "2026-10-07", "sha256": impronta,
         "reference": "storico con date invertite"},
    ])
    from consent.site_evidence import site_checkbox_evidence
    finestre = [(f["kind"], f"{f['from']:%Y-%m-%d}", f"{f['to']:%Y-%m-%d}") for f in site_checkbox_evidence()["windows"]]
    assert finestre == [("site_copy", "2026-10-07", "2026-10-07"), ("backup", "2026-10-23", "2026-10-23")], finestre
    cid, _ = _spunta_sito(m, "serena.due.copie@example.it", quando=SERENA)
    _informativa_copia(m, valida_da=SERENA - timedelta(days=1))
    esito = _riconosci(m, cid)
    assert esito["granted"] == [] and esito["assessment"]["ticks"][0]["missing"] == ["text_proof"]
    assert not _guardia(m, 1, cid, "email").allowed and not _guardia(m, 1, cid, "whatsapp").allowed


def test_09d_un_backup_datato_del_22_10_prova_il_testo_e_serena_e_concessa_dal_sito(
        sr, tmp_path, monkeypatch, prove_pulite):
    m = sr
    impronta = _impronta_copia()
    _prove(tmp_path, monkeypatch, [{"kind": "backup", "date": "2026-10-22", "sha256": impronta,
                                    "reference": "backup giornaliero hosting 22/10/2026 03:00"}])
    cid, stima = _spunta_sito(m, "serena.backup@example.it", quando=SERENA)
    informativa = _informativa_copia(m, valida_da=SERENA - timedelta(days=1))
    esito = _riconosci(m, cid)
    assert sorted(g["channel"] for g in esito["granted"]) == ["email", "whatsapp"]
    val = esito["assessment"]["ticks"][0]
    assert val["notice"]["id"] == informativa and val["proof"]["evidence_kind"] == "backup"
    assert val["proof"]["window"] == ["2026-10-22", "2026-10-22"]
    note = _q(m, "SELECT note FROM consent_events WHERE contact_id = %s AND channel = 'email'", (cid,))[0][0]
    assert f"richiesta di valutazione n. {stima}" in note and "backup datato della pagina" in note
    assert "backup giornaliero hosting 22/10/2026 03:00" in note and impronta in note
    assert _guardia(m, 1, cid, "email").allowed and _guardia(m, 1, cid, "whatsapp").allowed
    assert not _guardia(m, 1, cid, "sms").allowed and not _guardia(m, 1, cid, "phone").allowed


def test_09e_uno_storico_delle_pubblicazioni_prova_l_intervallo_e_la_revoca_prevale(
        sr, tmp_path, monkeypatch, prove_pulite):
    from consent import service as cs
    m = sr
    impronta = _impronta_copia()
    _prove(tmp_path, monkeypatch, [{"kind": "publication_history", "from": "2026-10-07", "to": "2026-10-23",
                                    "sha256": impronta,
                                    "reference": "log FTP hosting 07-23/10/2026: nessun caricamento del file"}])
    cid, _ = _spunta_sito(m, "serena.storico@example.it", quando=SERENA)
    _informativa_copia(m, valida_da=SERENA - timedelta(days=1))
    cs.record_revocation(_sistema(1), contact_id=cid, purpose="marketing", channel="whatsapp",
                         source="unsubscribe_link", actor_type="subject", decided_at=SERENA + timedelta(hours=3),
                         idempotency_key=f"rev:{cid}:wa")
    esito = _riconosci(m, cid)
    assert [g["channel"] for g in esito["granted"]] == ["email"]
    val = esito["assessment"]["ticks"][0]
    assert val["proof"]["evidence_kind"] == "publication_history"
    assert val["proof"]["window"] == ["2026-10-07", "2026-10-23"]
    assert val["channels"]["whatsapp"]["status"] == "superseded"
    assert _guardia(m, 1, cid, "email").allowed
    assert _guardia(m, 1, cid, "whatsapp").reason == "deny_revoked"


def test_09c_una_spunta_nel_giorno_della_copia_e_provata(sr, prove_pulite):
    m = sr
    _informativa_copia(m, valida_fino=COPIA + timedelta(days=1))
    cid, _ = _spunta_sito(m, "stesso.giorno@example.it", quando=COPIA + timedelta(hours=12))
    esito = _riconosci(m, cid)
    assert sorted(g["channel"] for g in esito["granted"]) == ["email", "whatsapp"]
    assert esito["assessment"]["ticks"][0]["proof"]["window"] == ["2026-10-07", "2026-10-07"]


def test_10_senza_il_testo_esatto_o_prima_della_copia_niente(sr):
    m = sr
    # testo diverso da quello provato: non vale
    _informativa_copia(m, testo="Acconsento a ricevere comunicazioni commerciali via email e WhatsApp.",
                       valida_fino=COPIA + timedelta(days=4))
    cid, _ = _spunta_sito(m, "testo.diverso@example.it", quando=COPIA + timedelta(days=3))
    esito = _riconosci(m, cid)
    assert esito["granted"] == [] and esito["assessment"]["ticks"][0]["missing"] == ["notice"]
    assert not _guardia(m, 1, cid).allowed


def test_11_validita_anteriore_alla_copia_non_e_provabile(sr):
    m = sr
    _informativa_copia(m, valida_da=COPIA - timedelta(days=6),   # dichiarata valida dal 1/10: prima della copia
                       valida_fino=COPIA + timedelta(days=7))
    cid, _ = _spunta_sito(m, "prima.copia@example.it", quando=COPIA + timedelta(days=6))
    assert _riconosci(m, cid)["granted"] == []
    assert _riconosci(m, cid)["assessment"]["ticks"][0]["missing"] == ["notice"]


def test_12_una_spunta_precedente_alla_copia_resta_incompleta(sr):
    m = sr
    _informativa_copia(m, valida_fino=COPIA + timedelta(days=1))
    cid, _ = _spunta_sito(m, "spunta.vecchia@example.it", quando=COPIA - timedelta(days=1))
    assert _riconosci(m, cid)["granted"] == []
    assert not _guardia(m, 1, cid).allowed


def test_13_la_copia_alterata_chiude_la_via(sr, monkeypatch, prove_pulite):
    from consent import site_evidence
    m = sr
    _informativa_copia(m, valida_da=COPIA + timedelta(days=8), valida_fino=COPIA + timedelta(days=10))
    cid, _ = _spunta_sito(m, "copia.alterata@example.it", quando=COPIA + timedelta(days=9))
    monkeypatch.setattr(site_evidence, "site_checkbox_evidence", lambda: None)
    from consent import site_recognition
    monkeypatch.setattr(site_recognition, "site_checkbox_evidence", lambda: None)
    assert _riconosci(m, cid)["granted"] == []


def test_14_una_revoca_dopo_il_riconoscimento_non_viene_mai_scavalcata(sr):
    """Riconosciuto «concesso dal sito», poi revocato: la guardia nega, la
    scheda non dice piu' «concesso dal sito», e un nuovo riconoscimento (a
    ogni giro di import o lettura della scheda) non riconcede niente."""
    from consent import service as cs
    m = sr
    _informativa_sito(m, registrata=_quando(20) - timedelta(hours=5))
    cid, _ = _spunta_sito(m, "revoca.dopo@example.it", quando=_quando(20))
    assert sorted(g["channel"] for g in _riconosci(m, cid)["granted"]) == ["email", "whatsapp"]
    cs.record_revocation(_sistema(1), contact_id=cid, purpose="marketing", channel="email",
                         source="unsubscribe_link", actor_type="subject", decided_at=_quando(20) + timedelta(days=1),
                         idempotency_key=f"rev:{cid}:dopo")
    for _ in range(2):
        esito = _riconosci(m, cid)
        assert esito["granted"] == []
        assert esito["assessment"]["ticks"][0]["channels"]["email"]["status"] == "superseded"
        assert esito["assessment"]["ticks"][0]["channels"]["whatsapp"]["status"] == "recognized"
    assert _guardia(m, 1, cid, "email").reason == "deny_revoked"
    assert _guardia(m, 1, cid, "whatsapp").allowed
    eventi = [e[:2] for e in _eventi(m, cid)]
    assert eventi == [(None, "granted"), ("email", "granted"), ("whatsapp", "granted"), ("email", "revoked")], eventi


# ---------------------------------------------------------------------------
# Generalizzazione: le stesse regole per OGNI contatto del sito
# ---------------------------------------------------------------------------

def _pendenti(m, limite=200):
    from psycopg2.extras import RealDictCursor
    from consent import site_recognition
    with m["conn"].cursor(cursor_factory=RealDictCursor) as cur:
        return site_recognition.pending_contacts(cur, limit=limite)


def test_15_una_revoca_precedente_cede_a_una_nuova_spunta(sr):
    """Revocato in passato, poi di nuovo la casella spuntata sul sito con
    l'informativa valida: e' un consenso NUOVO, riconosciuto."""
    from consent import service as cs
    m = sr
    _informativa_sito(m, registrata=_quando(22) - timedelta(hours=5))
    cid = _contatto(m, "ritorno@example.it")
    _q(m, "UPDATE contacts SET phone_normalized = '393339990001', source = 'public_stima' WHERE id = %s", (cid,))
    for canale in ("email", "whatsapp"):
        cs.record_revocation(_sistema(1), contact_id=cid, purpose="marketing", channel=canale,
                             source="unsubscribe_link", actor_type="subject",
                             decided_at=_quando(22) - timedelta(days=30), idempotency_key=f"rev:{cid}:{canale}:prima")
    assert not _guardia(m, 1, cid, "email").allowed
    stima = _q(m, "INSERT INTO stime (agency_id, comune, via, mq) VALUES (1, 'Alba', 'Via Sito', 80) RETURNING id")[0][0]
    cs.record_grant(_sistema(1), contact_id=cid, purpose="marketing", source="public_stima", actor_type="subject",
                    decided_at=_quando(22), evidence_type="stima", evidence_ref=str(stima),
                    idempotency_key=f"public_stima:{stima}:marketing")
    assert (1, cid) in _pendenti(m)
    esito = _riconosci(m, cid)
    assert sorted(g["channel"] for g in esito["granted"]) == ["email", "whatsapp"]
    assert _guardia(m, 1, cid, "email").allowed and _guardia(m, 1, cid, "whatsapp").allowed
    assert not _guardia(m, 1, cid, "sms").allowed and not _guardia(m, 1, cid, "phone").allowed
    assert (1, cid) not in _pendenti(m)


def test_16_una_seconda_spunta_gia_concessa_non_resta_in_coda(sr):
    """La stessa persona torna sul sito: la seconda spunta trova i canali gia'
    concessi («gia' concesso»), non riconcede e non resta fra i pendenti."""
    from consent import service as cs
    m = sr
    _informativa_sito(m, registrata=_quando(24) - timedelta(hours=5))
    cid, _ = _spunta_sito(m, "due.stime@example.it", quando=_quando(24), telefono="393339990002")
    assert sorted(g["channel"] for g in _riconosci(m, cid)["granted"]) == ["email", "whatsapp"]
    seconda = _q(m, "INSERT INTO stime (agency_id, comune, via, mq) VALUES (1, 'Alba', 'Via Due', 70) RETURNING id")[0][0]
    cs.record_grant(_sistema(1), contact_id=cid, purpose="marketing", source="public_stima", actor_type="subject",
                    decided_at=_quando(24) + timedelta(hours=1), evidence_type="stima", evidence_ref=str(seconda),
                    idempotency_key=f"public_stima:{seconda}:marketing")
    assert (1, cid) not in _pendenti(m)
    esito = _riconosci(m, cid)
    assert esito["granted"] == []
    nuova = next(t for t in esito["assessment"]["ticks"] if t["stima_id"] == seconda)
    assert {c: v["status"] for c, v in nuova["channels"].items()} == {"email": "already_granted",
                                                                       "whatsapp": "already_granted"}


def test_17_le_spunte_senza_prova_non_affamano_il_lotto(sr):
    """Molte spunte senza prova (piu' del lotto) non nascondono quella
    riconoscibile: la riconciliazione seleziona solo cio' che ORA si puo'
    riconoscere; le altre restano incomplete, con la scelta conservata."""
    m = sr
    incomplete = [_spunta_sito(m, f"senza.prova.{i}@example.it", quando=datetime(2025, 11, 2 + i, 9, tzinfo=timezone.utc),
                               telefono=f"39333999100{i}")[0] for i in range(4)]
    _informativa_sito(m, registrata=datetime(2025, 11, 19, 9, tzinfo=timezone.utc))
    buona, _ = _spunta_sito(m, "con.prova@example.it", quando=datetime(2025, 11, 19, 15, tzinfo=timezone.utc),
                            telefono="393339991009")
    assert _pendenti(m, limite=1) == [(1, buona)]
    assert not any((1, c) in _pendenti(m) for c in incomplete)
    assert sorted(g["channel"] for g in _riconosci(m, buona)["granted"]) == ["email", "whatsapp"]
    assert (1, buona) not in _pendenti(m)
    for c in incomplete:
        assert _riconosci(m, c)["assessment"]["ticks"][0]["missing"] == ["notice"]
        assert not _guardia(m, 1, c, "email").allowed and not _guardia(m, 1, c, "whatsapp").allowed
        assert [e[:2] for e in _eventi(m, c)] == [(None, "granted")]


def test_18_un_informativa_registrata_contraddetta_dalla_documentazione_non_vale(
        sr, tmp_path, monkeypatch, prove_pulite):
    """Nei giorni coperti dalla documentazione del sito, un'informativa
    registrata nel CRM con un testo DIVERSO dalla casella documentata non
    autorizza: manca un'informativa coerente. Con il testo documentato, si."""
    m = sr
    impronta = _impronta_copia()
    _prove(tmp_path, monkeypatch, [
        {"kind": "backup", "date": "2025-09-15", "sha256": impronta, "reference": "backup hosting 15/09/2025"},
        {"kind": "backup", "date": "2025-09-16", "sha256": impronta, "reference": "backup hosting 16/09/2025"}])
    from consent.site_evidence import site_checkbox_evidence
    documentato = site_checkbox_evidence()["text"]
    _informativa_sito(m, registrata=datetime(2025, 9, 15, tzinfo=timezone.utc),
                      testo="Acconsento a ricevere offerte commerciali via SMS e telefonate.")
    cid, _ = _spunta_sito(m, "contraddetta@example.it", quando=datetime(2025, 9, 15, 12, tzinfo=timezone.utc),
                          telefono="393339992001")
    esito = _riconosci(m, cid)
    assert esito["granted"] == [] and esito["assessment"]["ticks"][0]["missing"] == ["text_mismatch"]
    assert "testo diverso" in esito["assessment"]["ticks"][0]["missing_labels"][0]
    assert not _guardia(m, 1, cid, "email").allowed and not _guardia(m, 1, cid, "whatsapp").allowed
    assert [e[:2] for e in _eventi(m, cid)] == [(None, "granted")]
    _informativa_sito(m, registrata=datetime(2025, 9, 16, 1, tzinfo=timezone.utc), testo=documentato)
    altro, _ = _spunta_sito(m, "coerente@example.it", quando=datetime(2025, 9, 16, 12, tzinfo=timezone.utc),
                            telefono="393339992002")
    assert sorted(g["channel"] for g in _riconosci(m, altro)["granted"]) == ["email", "whatsapp"]


def test_19_senza_spunta_niente_e_senza_numero_niente_whatsapp(sr):
    """Casella non spuntata (nessun evento del sito): nessuna autorizzazione.
    Spuntata ma senza numero: solo l'email; SMS e telefonate mai."""
    m = sr
    _informativa_sito(m, registrata=_quando(26) - timedelta(hours=5))
    senza = _contatto(m, "non.spuntata@example.it")
    _q(m, "UPDATE contacts SET source = 'public_stima' WHERE id = %s", (senza,))
    esito = _riconosci(m, senza)
    assert esito["granted"] == [] and esito["assessment"]["ticks"] == []
    assert (1, senza) not in _pendenti(m)
    cid, _ = _spunta_sito(m, "senza.numero@example.it", quando=_quando(26))
    _q(m, "UPDATE contacts SET phone = NULL, phone_normalized = NULL WHERE id = %s", (cid,))
    esito = _riconosci(m, cid)
    assert [g["channel"] for g in esito["granted"]] == ["email"]
    assert esito["assessment"]["ticks"][0]["channels"]["whatsapp"]["status"] == "no_address"
    assert (1, cid) not in _pendenti(m)
    for c in ("whatsapp", "sms", "phone"):
        assert not _guardia(m, 1, cid, c).allowed, c
        assert not _guardia(m, 1, senza, c).allowed, c
    assert not _guardia(m, 1, senza, "email").allowed
