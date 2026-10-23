"""C3.1 - la casella marketing del sito pubblicato nell'import del CRM.

Due database usa-e-getta (il "sito" con lo schema PROD e il CRM), il fixture
vero di SITE-IMPORT-1, dati fittizi. Il sito non cambia: manda ancora solo
`consenso_marketing` (e il suo server ne scrive l'istante).

  01  interruttore SPENTO (default): import completo, nessuna richiesta,
      nessun messaggio; la spunta resta prova senza autorizzazione.
  02  interruttore ACCESO: richiesta email in attesa + email di SERVIZIO in
      coda; nessun consenso finche' la persona non conferma; dopo la conferma
      solo l'email e' autorizzata, WhatsApp mai.
  03  casella non spuntata, istante mancante: nessuna richiesta.
  04  informativa registrata DOPO la spunta (anche se retrodatata): rifiutata.
  05  stime storiche (baseline) e stime importate a interruttore spento: mai
      un'email, nemmeno riaccendendo.
  06  due stime della stessa persona: una sola richiesta, una sola email.
  07  un errore del consenso non ferma contatto, lead, immobile, evento, PDF;
      il passo si riprende al giro dopo.

Opt-in: senza `P29_TEST_DSN` si salta tutto.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_censimento_3_backend_postgres import DSN, completo  # noqa: F401 - fixture
from tests.test_site_import_1_postgres import PDF, conta, mondo, sito_db  # noqa: F401 - fixture

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: serve PostgreSQL isolato")

BASE = "https://crm-test.example.invalid"


def _giorno(n):
    """Una finestra di date PROPRIA di ogni test (anno 2001): le informative
    sono un registro e restano; date isolate evitano che quella di un test
    risulti valida all'istante della spunta di un altro."""
    return datetime(2001, 1, n, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def ci(mondo, monkeypatch):
    """Il mondo dell'import, con il dominio C3 pulito e l'URL pubblico."""
    from communication import consent_page
    m = mondo
    q = m["q_crm"]
    for sql in ("ALTER TABLE consent_submissions DISABLE TRIGGER trg_consent_submissions_guard",
                "DELETE FROM consent_submissions",
                "ALTER TABLE consent_submissions ENABLE TRIGGER trg_consent_submissions_guard",
                "ALTER TABLE communication_messages DISABLE TRIGGER trg_communication_messages_guard",
                "DELETE FROM communication_messages WHERE reason_code = 'consent_confirmation'",
                "ALTER TABLE communication_messages ENABLE TRIGGER trg_communication_messages_guard",
                "DELETE FROM consent_page_rate_limits",
                # le informative sono un registro: si chiudono, non si cancellano
                "UPDATE consent_notices SET valid_to = NOW() - interval '1 second' "
                "WHERE purpose = 'marketing' AND (valid_to IS NULL OR valid_to >= NOW())"):
        q(sql)
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)
    monkeypatch.delenv(consent_page.SITE_DOUBLE_OPT_IN_ENV, raising=False)
    m["acceso"] = lambda: monkeypatch.setenv(consent_page.SITE_DOUBLE_OPT_IN_ENV, "on")
    m["spento"] = lambda: monkeypatch.delenv(consent_page.SITE_DOUBLE_OPT_IN_ENV, raising=False)
    return m


def _informativa(m, *, registrata, valida_da=None, valida_fino=None, canali=("email", "whatsapp"),
                 contesto="Link personale / QR dal CRM"):
    """Un'informativa marketing registrata nel CRM con l'istante VERO di
    registrazione (`created_at`, immutabile). Per default NON e' quella del
    sito (contesto del QR): i test 01-07 provano il double opt-in C3.1; per
    il riconoscimento C3.4 si usa `_informativa_sito`."""
    import uuid
    versione = f"sito-{uuid.uuid4().hex[:8]}"
    return m["q_crm"](
        "INSERT INTO consent_notices (purpose, version, content, content_hash, content_available, context, "
        "valid_from, valid_to, created_at, created_by, channels) VALUES ('marketing', %s, "
        "'Acconsento a ricevere analisi di mercato e aggiornamenti da Stima360 tramite email e WhatsApp.', "
        "repeat('c', 64), TRUE, %s, %s, %s, %s, 'test', %s) RETURNING id",
        (versione, contesto, valida_da or registrata, valida_fino or (valida_da or registrata) + timedelta(days=1),
         registrata, list(canali)))[0][0]


def _stima(m, *, spuntata=True, decisa=None, **campi):
    import random
    campi.setdefault("telefono", f"33{random.randint(10**7, 10**8 - 1)}")   # niente fusione per telefono
    campi.setdefault("email", f"c{random.randint(10**6, 10**7)}@example.invalid")
    return m["stima"](minuti_fa=30, consenso_marketing=spuntata,
                      consenso_marketing_at=decisa if spuntata else None, **campi)


def _passo(m, stima_id, nome="consent"):
    return m["ledger"]()[stima_id][1].get(nome)


def _contatto(m, stima_id):
    return m["ledger"]()[stima_id][1]["bridge"]["contact_id"]


def _guardia(m, stima_id, canale):
    from consent.guard import can_send_marketing
    from operator_auth.context import SystemAgencyContext
    agenzia = m["q_crm"]("SELECT agency_id FROM stime WHERE id = %s", (stima_id,))[0][0]
    return can_send_marketing(SystemAgencyContext(agency_id=agenzia, origin="public_stima"),
                              _contatto(m, stima_id), channel=canale)


def _import_completo(m, stima_id):
    """Contatto, lead, immobile, evento e PDF ci sono, qualunque sia il consenso."""
    passi = m["ledger"]()[stima_id][1]
    for nome in ("bridge", "property", "event", "pdf"):
        assert passi[nome]["state"] == "done", (nome, passi[nome])
    assert conta(m, "lead_stime", "stima_id = %s", (stima_id,)) == 1
    assert conta(m, "stima_pdf_artifacts", "stima_id = %s AND status = 'ready'", (stima_id,)) == 1


def _submissions(m, stima_id):
    return m["q_crm"]("SELECT origin, channels_requested, email_status, notice_id FROM consent_submissions "
                      "WHERE stima_id = %s", (stima_id,))


# ---------------------------------------------------------------------------

def test_01_interruttore_spento_import_completo_nessuna_email(ci):
    m = ci
    _informativa(m, registrata=_giorno(3) - timedelta(hours=2))
    s = _stima(m, decisa=_giorno(3) - timedelta(minutes=30))
    m["archivio"].pdf[s] = PDF
    esito = m["giro"]()
    assert esito["stime_imported"] == 1, esito
    _import_completo(m, s)
    assert _passo(m, s) == {"state": "not_applicable", "status": "disabled"}
    assert _submissions(m, s) == []
    assert conta(m, "communication_messages", "contact_id = %s", (_contatto(m, s),)) == 0
    # la spunta resta: prova pre-canale, nessuna autorizzazione
    ev = m["q_crm"]("SELECT channel, decision, source, evidence_ref FROM consent_events WHERE contact_id = %s",
                    (_contatto(m, s),))
    assert [tuple(r) for r in ev] == [(None, "granted", "public_stima", str(s))]
    for canale in ("email", "whatsapp"):
        assert not _guardia(m, s, canale).allowed, canale


def test_02_interruttore_acceso_double_opt_in_solo_email(ci):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from communication.public_router import router
    m = ci
    m["acceso"]()
    informativa = _informativa(m, registrata=_giorno(6) - timedelta(hours=2))
    s = _stima(m, decisa=_giorno(6) - timedelta(minutes=30), email="mario.rossi@example.invalid")
    m["archivio"].pdf[s] = PDF
    m["giro"]()
    _import_completo(m, s)
    passo = _passo(m, s)
    assert passo["state"] == "done" and passo["status"] == "queued", passo
    assert [tuple(r) for r in _submissions(m, s)] == [("public_stima", ["email"], "pending", informativa)]
    cid = _contatto(m, s)
    msg = m["q_crm"]("SELECT communication_type, reason_code, destination_snapshot, status, rendered_body "
                     "FROM communication_messages WHERE contact_id = %s", (cid,))
    assert [tuple(r[:4]) for r in msg] == [("service", "consent_confirmation", "mario.rossi@example.invalid",
                                            "queued")]
    # in attesa: nessun canale autorizzato
    for canale in ("email", "whatsapp", "sms", "phone"):
        assert not _guardia(m, s, canale).allowed, canale

    # la persona conferma dal link dell'email (POST: la GET non concede)
    url = re.search(re.escape(BASE) + r"(/api/public/communication/consent/confirm/[A-Za-z0-9_-]{43})",
                    msg[0][4]).group(1)
    app = FastAPI()
    app.include_router(router)
    http = TestClient(app)
    assert http.get(url).status_code == 200
    assert not _guardia(m, s, "email").allowed
    r = http.post(url)
    assert r.status_code == 200 and 'data-esito="confirmed"' in r.text
    ev = m["q_crm"]("SELECT channel, source, evidence_type, notice_id FROM consent_events "
                    "WHERE contact_id = %s AND channel IS NOT NULL", (cid,))
    assert [tuple(e) for e in ev] == [("email", "public_stima", "double_opt_in", informativa)]
    assert _guardia(m, s, "email").allowed
    assert not _guardia(m, s, "whatsapp").allowed, "WhatsApp non si autorizza mai dalla casella del sito"


def test_03_casella_non_spuntata_o_senza_istante(ci):
    m = ci
    m["acceso"]()
    _informativa(m, registrata=_giorno(9) - timedelta(hours=2))
    no = _stima(m, spuntata=False)
    senza_ora = m["stima"](minuti_fa=30, consenso_marketing=True)   # sito vecchio: nessun istante
    for s in (no, senza_ora):
        m["archivio"].pdf[s] = PDF
    m["giro"]()
    for s in (no, senza_ora):
        _import_completo(m, s)
    assert _passo(m, no) == {"state": "not_applicable", "status": "not_requested"}
    assert _passo(m, senza_ora) == {"state": "not_applicable", "status": "no_decision_time"}
    assert conta(m, "consent_submissions") == 0
    assert conta(m, "communication_messages", "reason_code = 'consent_confirmation'") == 0


def test_04_informativa_registrata_dopo_la_spunta_non_si_attribuisce(ci):
    m = ci
    m["acceso"]()
    decisa = _giorno(12) - timedelta(minutes=30)
    # registrata _giorno(12) ma retrodatata di un giorno: non era nel CRM alla spunta
    _informativa(m, registrata=_giorno(12), valida_da=_giorno(12) - timedelta(days=1))
    s = _stima(m, decisa=decisa)
    m["archivio"].pdf[s] = PDF
    m["giro"]()
    _import_completo(m, s)
    assert _passo(m, s) == {"state": "not_applicable", "status": "notice_missing"}
    assert _submissions(m, s) == []
    # anche un'informativa che non copre l'email non basta
    _informativa(m, registrata=_giorno(12) - timedelta(hours=3), valida_da=_giorno(12) - timedelta(hours=3),
                 canali=("whatsapp",))
    t = _stima(m, decisa=decisa, email="altro@example.invalid")
    m["archivio"].pdf[t] = PDF
    m["giro"]()
    assert _passo(m, t)["status"] == "notice_missing"


def test_05_storico_e_stime_importate_a_interruttore_spento_mai_email(ci):
    m = ci
    _informativa(m, registrata=_giorno(15) - timedelta(days=60), valida_fino=_giorno(15) + timedelta(hours=1))
    # 1) storico: nella baseline, quindi mai importato come nuova richiesta
    storica = _stima(m, decisa=_giorno(15) - timedelta(days=30), email="storico@example.invalid")
    m["q_crm"]("DELETE FROM site_import_baselines")
    m["giro"](initialize_baseline=True)
    # 2) nuova, importata a interruttore spento
    nuova = _stima(m, decisa=_giorno(15) - timedelta(minutes=30), email="spento@example.invalid")
    m["archivio"].pdf[nuova] = PDF
    m["giro"]()
    assert _passo(m, nuova)["status"] == "disabled"
    # si accende: nessun recupero, ne' dello storico ne' della stima gia' importata
    m["acceso"]()
    m["giro"]()
    m["giro"]()
    assert storica not in m["ledger"]()
    assert _passo(m, nuova)["status"] == "disabled"
    assert conta(m, "consent_submissions") == 0
    assert conta(m, "communication_messages", "reason_code = 'consent_confirmation'") == 0


def test_06_due_stime_della_stessa_persona_una_sola_email(ci):
    m = ci
    m["acceso"]()
    _informativa(m, registrata=_giorno(18) - timedelta(hours=2))
    a = _stima(m, decisa=_giorno(18) - timedelta(minutes=40), email="doppio@example.invalid")
    b = _stima(m, decisa=_giorno(18) - timedelta(minutes=35), email="doppio@example.invalid")
    for s in (a, b):
        m["archivio"].pdf[s] = PDF
    m["giro"]()
    m["giro"]()
    stati = sorted(_passo(m, s)["status"] for s in (a, b))
    assert stati == ["already_pending", "queued"], stati
    assert _contatto(m, a) == _contatto(m, b)
    assert conta(m, "consent_submissions") == 1
    assert conta(m, "communication_messages", "reason_code = 'consent_confirmation'") == 1


def test_07_un_errore_del_consenso_non_ferma_l_import(ci, monkeypatch):
    from communication import consent_page
    m = ci
    m["acceso"]()
    _informativa(m, registrata=_giorno(21) - timedelta(hours=2))
    s = _stima(m, decisa=_giorno(21) - timedelta(minutes=30), email="guasto@example.invalid")
    m["archivio"].pdf[s] = PDF

    def guasto(*a, **k):
        raise RuntimeError("consenso non disponibile")

    originale = consent_page.request_from_site_checkbox
    monkeypatch.setattr(consent_page, "request_from_site_checkbox", guasto)
    m["giro"]()
    _import_completo(m, s)
    assert _passo(m, s)["state"] == "error"
    assert m["ledger"]()[s][0] == "partial"
    # al giro dopo il solo passo del consenso si riprende
    monkeypatch.setattr(consent_page, "request_from_site_checkbox", originale)
    m["giro"]()
    assert _passo(m, s)["status"] == "queued"
    assert m["ledger"]()[s][0] == "imported"
    assert conta(m, "lead_stime", "stima_id = %s", (s,)) == 1


def _informativa_sito(m, *, registrata, canali=("email", "whatsapp")):
    """C3.4: il testo della casella del sito, registrato come informativa DEL
    SITO (il contesto nomina stima360.it) con l'istante vero di registrazione."""
    return _informativa(m, registrata=registrata, canali=canali, valida_fino=registrata + timedelta(days=3),
                        contesto="Sito www.stima360.it - casella marketing del modulo di valutazione")


def test_08_c34_nuovo_contatto_con_informativa_del_sito_concesso_senza_double_opt_in(ci):
    m = ci
    m["acceso"]()   # anche con il double opt-in acceso, se le prove bastano non serve
    informativa = _informativa_sito(m, registrata=_giorno(24) - timedelta(days=2))
    s = _stima(m, decisa=_giorno(24) - timedelta(minutes=30), email="riconosciuto@example.invalid")
    m["archivio"].pdf[s] = PDF
    m["giro"]()
    _import_completo(m, s)
    assert _passo(m, s) == {"state": "done", "status": "recognized", "channels": ["email", "whatsapp"]}
    cid = _contatto(m, s)
    ev = m["q_crm"]("SELECT channel, decision, source, evidence_type, evidence_ref, notice_id, decided_at "
                    "FROM consent_events WHERE contact_id = %s ORDER BY id", (cid,))
    assert [tuple(r[:2]) for r in ev] == [(None, "granted"), ("email", "granted"), ("whatsapp", "granted")]
    for r in ev[1:]:
        assert tuple(r[2:]) == ("public_stima", "stima", str(s), informativa, _giorno(24) - timedelta(minutes=30))
    assert _guardia(m, s, "email").allowed and _guardia(m, s, "whatsapp").allowed
    assert not _guardia(m, s, "sms").allowed and not _guardia(m, s, "phone").allowed
    assert conta(m, "consent_submissions") == 0, "nessun double opt-in: il consenso e' gia' valido"
    assert conta(m, "communication_messages", "reason_code = 'consent_confirmation'") == 0


def test_09_c34_riconciliazione_a_ogni_giro_e_nessuna_retrodatazione(ci):
    m = ci
    # 1) spunta importata SENZA informativa: prova incompleta
    s = _stima(m, decisa=_giorno(27) - timedelta(minutes=30), email="riconcilia@example.invalid")
    m["archivio"].pdf[s] = PDF
    m["giro"]()
    assert _passo(m, s)["status"] == "disabled"
    cid = _contatto(m, s)
    assert not _guardia(m, s, "email").allowed
    # 2) informativa del sito registrata DOPO la spunta, retrodatata: al giro seguente niente cambia
    _informativa(m, registrata=_giorno(27), valida_da=_giorno(27) - timedelta(days=3),
                 valida_fino=_giorno(27) + timedelta(days=1),
                 contesto="Sito www.stima360.it - casella marketing del modulo di valutazione")
    esito = m["giro"]()
    assert esito["site_consents_recognized"] == 0 and not _guardia(m, s, "email").allowed
    # 3) una spunta NUOVA, successiva alla registrazione dell'informativa: riconosciuta dal giro
    t = _stima(m, decisa=_giorno(27) + timedelta(hours=2), email="riconcilia@example.invalid")
    m["archivio"].pdf[t] = PDF
    esito = m["giro"]()
    assert _passo(m, t)["status"] == "recognized" and _contatto(m, t) == cid
    assert _guardia(m, s, "email").allowed and _guardia(m, s, "whatsapp").allowed
    # il riconoscimento cita la spunta nuova (t), non quella vecchia (s)
    ref = m["q_crm"]("SELECT DISTINCT evidence_ref FROM consent_events WHERE contact_id = %s AND channel IS NOT NULL",
                     (cid,))
    assert [r[0] for r in ref] == [str(t)]
