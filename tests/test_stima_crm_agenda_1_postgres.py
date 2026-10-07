"""STIMA-CRM-AGENDA-1 - dalla dettagliata del sito alla richiesta in Agenda, da sola.

Su PostgreSQL usa-e-getta (schema completo, migration 094 compresa), con gli
endpoint VERI del sito (`main.salva_stima` / `main.salva_stima_dettagliata`
dietro la ricevuta F04), il bridge CORE vero, la scheda immobile vera e
l'import A30-6 vero. Si sostituiscono solo PDF, mail/WhatsApp e la decisione
di routing, come in `test_catalogo_canonico_1_postgres.py`.

  A  dettagliata senza sopralluogo            -> 0 appointment
  B  dettagliata con sopralluogo              -> 1 appointment `requested`
  C  stesso record importato due volte        -> sempre 1
  D  aggancio e sync manuale contemporanei    -> 1
  E  Agenda/import guasti                     -> dettagliata salvata, nessun
                                                 falso errore, recupero manuale
  F  agenzia corretta
  G  l'altra agenzia non vede la richiesta
  H  Google: `requested` non genera sync ne' evento
  I  retry / doppio click F04                 -> una dettagliata, un appointment

Il collegamento `property_id` resta NULL (D6 di A30-6, P1 documentato nel
rapporto): la richiesta arriva in Agenda con agenzia, stima, lead/contatto
quando univoci, origine `legacy_stime_dettagliate` e record dettagliato.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.test_catalogo_canonico_1_postgres import (  # noqa: F401 - fixture
    COMPLETA, DSN, _persona, completo, mondo, sito,
)
from tests.test_censimento_3_backend_postgres import _q

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: serve PostgreSQL isolato")

SOURCE = "legacy_stime_dettagliate"
ROMA = ZoneInfo("Europe/Rome")
#: Ora a parete che il form manda (`datetime-local`, senza fuso), lontana dai cambi d'ora.
SOPRALLUOGO = "2026-11-05T10:30"


# ---------------------------------------------------------------------------
# banco
# ---------------------------------------------------------------------------

def _quick(s, *, agency=1):
    risposta = s.stima({**COMPLETA, **_persona()}, agency=agency)
    assert risposta["receipt"]["status"] == "completed", risposta["receipt"]
    return risposta["id"]


def _payload(s, sid, **extra):
    """Il corpo della dettagliata, con la sua identita' F04 (riusabile per un retry)."""
    return {"stima_id": sid, "token": s.token(sid), "classe": "A4", "request_id": str(uuid.uuid4()), **extra}


def _dettaglio(s, payload):
    esito = s.dettaglio(payload)
    assert esito == {"ok": True}, esito
    return _detail_ids(s, payload["stima_id"])[-1]


def _detail_ids(s, sid):
    return [r[0] for r in _q(s.m, "SELECT id FROM stime_dettagliate WHERE stima_id = %s ORDER BY id", (sid,))]


def _richieste(s, detail_id):
    righe = _q(s.m, "SELECT * FROM appointments WHERE source = %s AND source_record_id = %s",
               (SOURCE, f"stime_dettagliate:{detail_id}"))
    colonne = [d[0] for d in _colonne(s, "appointments")]
    return [dict(zip(colonne, r)) for r in righe]


def _colonne(s, tabella):
    with s.m["conn"].cursor() as cur:
        cur.execute(f"SELECT * FROM {tabella} LIMIT 0")
        return cur.description


def _conta(s, tabella, where="TRUE", par=None):
    return _q(s.m, f"SELECT count(*) FROM {tabella} WHERE {where}", par)[0][0]


def _eventi(s, appointment_id):
    return [tuple(r) for r in _q(s.m, "SELECT event_type, from_status, to_status, actor_user_id FROM appointment_events "
                                      "WHERE appointment_id = %s ORDER BY id", (appointment_id,))]


def _lead_della_stima(s, sid):
    righe = _q(s.m, "SELECT l.id, l.contact_id FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id "
                    "WHERE ls.stima_id = %s", (sid,))
    assert len(righe) == 1, righe
    return tuple(righe[0])


def _sync_manuale(s, agency_id):
    """La sincronizzazione esplicita dall'Agenda (A30-7): la funzione VERA della
    rotta `POST /api/appointments/legacy-requests/sync`, con un titolare della
    sessione. Ritorna i contatori della risposta (`counts`) piu' la risposta.
    Nessun doppio sull'import: gira in parallelo con l'aggancio (prova D/J5)."""
    from operator_auth.context import OperatorContext
    from appointments_legacy import router
    operatore = _q(s.m, "SELECT id FROM operator_users ORDER BY id LIMIT 1")[0][0]
    ctx = OperatorContext(user_id=operatore, agency_id=agency_id, role="agency_owner",
                          is_platform_admin=False, session_id=None, auth_channel="session")
    risposta = router.sync_for_session(ctx)
    return {**risposta["counts"], "risposta": risposta}


def _aperte(s, sid):
    """Sopralluoghi APERTI della stima secondo il modello Agenda (OPEN_STATUSES)."""
    from appointments.state_machine import OPEN_STATUSES
    return _conta(s, "appointments", "stima_id = %s AND appointment_type = 'inspection' "
                                     "AND status = ANY(%s)", (sid, list(OPEN_STATUSES)))


def _annulla(s, appointment_id):
    """Annullamento con le funzioni del repository dell'Agenda (stessa forma del
    rollback A30-6): status `cancelled`, qualifica `agency`, evento."""
    from psycopg2.extras import RealDictCursor
    from appointments import repository
    conn = s.m["psycopg2"].connect(s.m["dsn"])
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT status FROM appointments WHERE id = %s FOR UPDATE", (appointment_id,))
            da = cur.fetchone()["status"]
            repository.update_appointment(
                cur, appointment_id,
                {"status": "cancelled", "cancelled_at": repository.db_now(cur),
                 "cancelled_reason": "COLLAUDO CLAUDE annullata",
                 **repository.cancelled_kind_changes(cur, "agency")},
                actor_user_id=None, event_type="status_changed", from_status=da, azione="cancel")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# A / B - la dettagliata porta (o no) una richiesta in Agenda
# ---------------------------------------------------------------------------

def test_a_dettagliata_senza_sopralluogo_nessun_appointment(sito):
    s = sito
    sid = _quick(s)
    prima = _conta(s, "appointments")
    did = _dettaglio(s, _payload(s, sid))
    assert _conta(s, "stime_dettagliate", "id = %s AND sopralluogo IS NULL", (did,)) == 1
    assert _richieste(s, did) == []
    assert _conta(s, "appointments") == prima
    assert _conta(s, "appointments", "stima_id = %s", (sid,)) == 0


def test_b_dettagliata_con_sopralluogo_una_richiesta_requested(sito):
    s = sito
    sid = _quick(s)
    lead_id, contact_id = _lead_della_stima(s, sid)
    did = _dettaglio(s, _payload(s, sid, sopralluogo=SOPRALLUOGO))

    righe = _richieste(s, did)
    assert len(righe) == 1
    a = righe[0]
    assert a["status"] == "requested"
    assert a["appointment_type"] == "inspection"
    assert a["agency_id"] == 1
    assert a["stima_id"] == sid
    assert a["source"] == SOURCE and a["source_record_id"] == f"stime_dettagliate:{did}"
    # ora a parete Europe/Rome (regola DST di A30-6), durata = policy 60'
    assert a["start_at"] == datetime(2026, 11, 5, 10, 30, tzinfo=ROMA)
    assert a["end_at"] - a["start_at"] == timedelta(minutes=60)
    # mai fissato: nessun agente, nessun esito, nessuna proiezione
    assert a["assigned_user_id"] is None and a["created_by_user_id"] is None
    assert a["stima_inspection_id"] is None
    # collegamenti CRM: il lead e' unico, quindi lead e contatto del lead
    assert a["lead_id"] == lead_id and a["contact_id"] == contact_id
    # D6/P1: l'immobile non si inferisce (la scheda c'e', collegata alla stima)
    assert a["property_id"] is None and s.scheda_di(sid) is not None
    assert a["location_text"] is None
    assert f"#{did}" in a["notes"]
    assert _eventi(s, a["id"]) == [("created", None, "requested", None)]
    assert _conta(s, "stima_inspections") == 0
    # e la dettagliata e' stata sincronizzata sulla scheda come prima
    assert s.invio("detail", did)["status"] == "synced"


def test_b2_la_richiesta_e_quella_che_il_sync_manuale_avrebbe_creato(sito):
    """Stessa riga, stessa chiave, stesso evento: dopo l'aggancio il sync
    manuale non ha nulla da fare per quel record."""
    s = sito
    sid = _quick(s)
    did = _dettaglio(s, _payload(s, sid, sopralluogo=SOPRALLUOGO))
    [a] = _richieste(s, did)
    esito = _sync_manuale(s, 1)
    # (il sync puo' importare record di altre prove rimasti; per QUESTO record nulla)
    assert esito["already_imported"] >= 1
    assert _richieste(s, did) == [a]


# ---------------------------------------------------------------------------
# C / D - idempotenza: un record, una riga, anche in concorrenza
# ---------------------------------------------------------------------------

def test_c_stesso_record_importato_due_volte_sempre_una_riga(sito):
    from appointments_legacy.site_hook import safe_import_for_detail
    s = sito
    sid = _quick(s)
    did = _dettaglio(s, _payload(s, sid, sopralluogo=SOPRALLUOGO))
    [a] = _richieste(s, did)

    di_nuovo = safe_import_for_detail(did, connection_factory=s.main.get_connection)
    assert di_nuovo["inserted"] == 0 and di_nuovo["already_imported"] == 1
    assert di_nuovo["with_sopralluogo"] == 1 and di_nuovo["record_id"] == did
    manuale = _sync_manuale(s, 1)
    assert manuale["inserted"] == 0
    assert _richieste(s, did) == [a]
    assert len(_eventi(s, a["id"])) == 1


def test_d_aggancio_e_sync_manuale_contemporanei_una_sola_riga(sito):
    """Il record esiste gia' senza richiesta (come un legacy mai importato);
    l'aggancio e il sync manuale partono insieme: l'indice unico della 072
    decide, una riga e un evento in tutto."""
    from appointments_legacy.site_hook import safe_import_for_detail
    s = sito
    sid = _quick(s)
    did = _q(s.m, "INSERT INTO stime_dettagliate (stima_id, agency_id, sopralluogo) VALUES (%s, 1, %s) RETURNING id",
             (sid, SOPRALLUOGO.replace("T", " ")))[0][0]
    assert _richieste(s, did) == []

    pronti = threading.Barrier(2)
    esiti = {}

    def aggancio():
        pronti.wait()
        esiti["hook"] = safe_import_for_detail(did, connection_factory=lambda: s.m["psycopg2"].connect(s.m["dsn"]))

    def manuale():
        pronti.wait()
        esiti["sync"] = _sync_manuale(s, 1)

    fili = [threading.Thread(target=aggancio), threading.Thread(target=manuale)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=30)
    assert set(esiti) == {"hook", "sync"} and esiti["hook"] is not None
    righe = _richieste(s, did)
    assert len(righe) == 1 and righe[0]["status"] == "requested"
    # per QUESTO record l'aggancio o ha inserito o l'ha trovato gia' importato
    # (il sync puo' aver importato altri record rimasti dalle prove precedenti:
    # non contano qui); in ogni caso UNA riga e UN evento
    assert esiti["hook"]["with_sopralluogo"] == 1
    # nessun errore d'import (un deadlock fra le due corse finirebbe qui)
    assert esiti["hook"]["errors"] == 0 and esiti["sync"]["errors"] == 0, esiti
    assert esiti["hook"]["inserted"] + esiti["hook"]["already_imported"] == 1, esiti["hook"]
    assert len(_eventi(s, righe[0]["id"])) == 1


# ---------------------------------------------------------------------------
# E - fail-open: l'Agenda guasta non costa la dettagliata
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("guasto", ["import", "database"])
def test_e_import_guasto_dettagliata_salvata_nessun_falso_errore_recupero_manuale(sito, monkeypatch, caplog, guasto):
    from appointments_legacy import site_hook
    s = sito
    sid = _quick(s)
    if guasto == "import":
        def rotto(*a, **k):
            raise RuntimeError("Agenda indisponibile (simulato)")
        monkeypatch.setattr(site_hook.legacy, "run_import", rotto)
    else:
        reale = site_hook.safe_import_for_detail

        def connessione_rotta():
            raise ConnectionError("database Agenda indisponibile (simulato)")

        # solo la connessione dell'aggancio: il salvataggio della dettagliata
        # continua a usare quella vera
        monkeypatch.setattr(s.main, "safe_import_for_detail",
                            lambda detail_id, **_: reale(detail_id, connection_factory=connessione_rotta))

    caplog.set_level(logging.INFO)
    payload = _payload(s, sid, sopralluogo=SOPRALLUOGO)
    did = _dettaglio(s, payload)                      # {"ok": True}: la ricevuta e' `completed`
    assert _conta(s, "stime_dettagliate", "id = %s AND sopralluogo IS NOT NULL", (did,)) == 1
    assert _richieste(s, did) == []
    assert s.invio("detail", did)["status"] == "synced"
    assert "legacy_detail_auto_import_failed" in caplog.text and f"detail_id={did}" in caplog.text
    assert "Rossi" not in caplog.text and "@example.test" not in caplog.text

    # la stessa identita' risponde di nuovo `completed` senza una seconda riga
    assert s.dettaglio(payload) == {"ok": True}
    assert _detail_ids(s, sid) == [did]

    # recupero: il ripiego manuale di A30-7 importa il record come prima
    monkeypatch.undo()
    esito = _sync_manuale(s, 1)
    assert esito["inserted"] >= 1
    [a] = _richieste(s, did)
    assert a["status"] == "requested" and a["stima_id"] == sid


def test_e2_record_scartato_dall_import_resta_salvato_e_segnalato(sito, caplog):
    """Un orario a parete che a Roma non esiste (salto di primavera) non si
    importa (D-DST): la dettagliata resta, la risposta e' completa, il log dice
    il motivo senza dati personali."""
    s = sito
    sid = _quick(s)
    caplog.set_level(logging.INFO)
    did = _dettaglio(s, _payload(s, sid, sopralluogo="2026-03-29T02:30"))
    assert _richieste(s, did) == []
    assert "legacy_detail_auto_import " in caplog.text and "excluded=1" in caplog.text
    assert "dst_nonexistent" in caplog.text or "excluded=1" in caplog.text


# ---------------------------------------------------------------------------
# F / G - agenzia
# ---------------------------------------------------------------------------

def test_f_g_agenzia_corretta_e_l_altra_non_vede(sito):
    from appointments import repository
    from psycopg2.extras import RealDictCursor
    s = sito
    sid1, sid2 = _quick(s, agency=1), _quick(s, agency=2)
    did1 = _dettaglio(s, _payload(s, sid1, sopralluogo=SOPRALLUOGO))
    did2 = _dettaglio(s, _payload(s, sid2, sopralluogo="2026-11-06T15:00"))
    [a1], [a2] = _richieste(s, did1), _richieste(s, did2)
    assert (a1["agency_id"], a2["agency_id"]) == (1, 2)
    assert [tuple(r) for r in _q(s.m, "SELECT agency_id FROM stime_dettagliate WHERE id IN (%s, %s) ORDER BY id", (did1, did2))] == [(1,), (2,)]
    assert [tuple(r) for r in _q(s.m, "SELECT agency_id FROM stime WHERE id IN (%s, %s) ORDER BY id", (sid1, sid2))] == [(1,), (2,)]

    conn = s.m["psycopg2"].connect(s.m["dsn"])
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # la lettura autorevole dell'Agenda e' per agenzia
            assert repository.get_appointment(cur, 1, a1["id"])["id"] == a1["id"]
            assert repository.get_appointment(cur, 2, a1["id"]) is None
            assert repository.get_appointment(cur, 2, a2["id"])["id"] == a2["id"]
            assert repository.get_appointment(cur, 1, a2["id"]) is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# H - Google non parte su `requested`
# ---------------------------------------------------------------------------

def test_h_requested_non_genera_sync_ne_evento_google(sito):
    from calendar_sync.constants import APPOINTMENT_REMOTE_PRESENT
    s = sito
    sid = _quick(s)
    did = _dettaglio(s, _payload(s, sid, sopralluogo=SOPRALLUOGO))
    [a] = _richieste(s, did)
    assert "requested" not in APPOINTMENT_REMOTE_PRESENT
    # colonne 072: nascono `not_synced`, senza evento ne' istante di sync
    assert a["google_event_id"] is None and a["google_calendar_id"] is None
    assert a["google_sync_status"] == "not_synced" and a["google_last_synced_at"] is None
    assert _conta(s, "appointment_calendar_sync",
                  "current_appointment_id = %s OR chain_root_appointment_id = %s", (a["id"], a["id"])) == 0
    assert _conta(s, "appointment_calendar_sync") == 0


# ---------------------------------------------------------------------------
# I - F04: retry e doppio click della dettagliata
# ---------------------------------------------------------------------------

def test_i_retry_stessa_identita_una_dettagliata_un_appointment(sito):
    s = sito
    sid = _quick(s)
    payload = _payload(s, sid, sopralluogo=SOPRALLUOGO)
    did = _dettaglio(s, payload)
    for _ in range(2):                                  # risposta persa / retry
        assert s.dettaglio(payload) == {"ok": True}
    assert _detail_ids(s, sid) == [did]
    assert len(_richieste(s, did)) == 1
    assert _conta(s, "appointments", "stima_id = %s", (sid,)) == 1


def test_i2_doppio_click_concorrente_una_dettagliata_un_appointment(sito):
    s = sito
    sid = _quick(s)
    payload = _payload(s, sid, sopralluogo=SOPRALLUOGO)
    pronti = threading.Barrier(2)
    esiti = []

    def invia():
        pronti.wait()
        esiti.append(s.dettaglio(payload))

    fili = [threading.Thread(target=invia) for _ in range(2)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=60)
    assert esiti == [{"ok": True}, {"ok": True}], esiti
    [did] = _detail_ids(s, sid)
    assert len(_richieste(s, did)) == 1
    assert _conta(s, "appointments", "stima_id = %s", (sid,)) == 1


# ---------------------------------------------------------------------------
# J - UNA SOLA RICHIESTA APERTA PER STIMA (regola D5 dell'Agenda, riusata)
# ---------------------------------------------------------------------------

def test_j1_stessa_stima_due_dettagliate_distinte_al_massimo_una_richiesta_aperta(sito):
    """Due dettagliate DISTINTE della stessa stima (identita' F04 diverse):
    entrambe salvate, una sola richiesta aperta."""
    s = sito
    sid = _quick(s)
    did1 = _dettaglio(s, _payload(s, sid, sopralluogo=SOPRALLUOGO))
    did2 = _dettaglio(s, _payload(s, sid, sopralluogo="2026-11-07T09:00"))
    assert did1 != did2 and _detail_ids(s, sid) == [did1, did2]
    assert len(_richieste(s, did1)) == 1 and _richieste(s, did2) == []
    assert _aperte(s, sid) == 1


def test_j2_appuntamento_aperto_nuovo_submit_dettagliata_salvata_nessun_duplicato(sito, caplog):
    """Esiste gia' una richiesta aperta: la nuova dettagliata si salva, la
    risposta al sito e' completa, nessun secondo appointment, nessun Google,
    nessun evento nuovo; il log dice perche' (senza dati personali)."""
    s = sito
    sid = _quick(s)
    did1 = _dettaglio(s, _payload(s, sid, sopralluogo=SOPRALLUOGO))
    [a] = _richieste(s, did1)
    eventi_prima = _conta(s, "appointment_events")
    sync_prima = _conta(s, "appointment_calendar_sync")
    caplog.set_level(logging.INFO)
    payload = _payload(s, sid, sopralluogo="2026-11-09T16:00")
    assert s.dettaglio(payload) == {"ok": True}                 # nessun errore al cliente
    did2 = _detail_ids(s, sid)[-1]
    assert did2 != did1
    assert _conta(s, "stime_dettagliate", "id = %s AND sopralluogo IS NOT NULL", (did2,)) == 1
    assert _richieste(s, did2) == [] and _richieste(s, did1) == [a]
    assert _aperte(s, sid) == 1
    assert _conta(s, "appointment_events") == eventi_prima
    assert _conta(s, "appointment_calendar_sync") == sync_prima == 0
    assert f"detail_id={did2}" in caplog.text and "open_request_exists=1" in caplog.text
    assert "Rossi" not in caplog.text and "@example.test" not in caplog.text
    # il ripiego manuale non la fa nascere dopo
    esito = _sync_manuale(s, 1)
    assert _richieste(s, did2) == [] and _aperte(s, sid) == 1
    assert esito["open_request_exists"] >= 1


def test_j3_dopo_l_annullamento_del_precedente_nuova_richiesta_consentita(sito):
    """Un sopralluogo in stato terminale (annullato) non e' aperto per il
    modello Agenda: la dettagliata successiva genera la sua richiesta."""
    from appointments.state_machine import OPEN_STATUSES
    s = sito
    sid = _quick(s)
    did1 = _dettaglio(s, _payload(s, sid, sopralluogo=SOPRALLUOGO))
    [a] = _richieste(s, did1)
    _annulla(s, a["id"])
    assert "cancelled" not in OPEN_STATUSES and _aperte(s, sid) == 0
    did2 = _dettaglio(s, _payload(s, sid, sopralluogo="2026-11-10T11:00"))
    [b] = _richieste(s, did2)
    assert b["status"] == "requested" and b["id"] != a["id"]
    assert _aperte(s, sid) == 1


def test_j4_due_submit_concorrenti_della_stessa_stima_identita_diverse_una_richiesta(sito, caplog):
    s = sito
    caplog.set_level(logging.INFO)
    sid = _quick(s)
    payloads = [_payload(s, sid, sopralluogo=SOPRALLUOGO), _payload(s, sid, sopralluogo="2026-11-12T10:00")]
    pronti = threading.Barrier(2)
    esiti = []

    def invia(p):
        pronti.wait()
        esiti.append(s.dettaglio(p))

    fili = [threading.Thread(target=invia, args=(p,)) for p in payloads]
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=60)
    assert esiti == [{"ok": True}, {"ok": True}], esiti
    assert "legacy_detail_auto_import_failed" not in caplog.text
    assert "errors': [{" not in caplog.text and "error_kinds=['" not in caplog.text
    dids = _detail_ids(s, sid)
    assert len(dids) == 2
    assert sum(len(_richieste(s, d)) for d in dids) == 1
    assert _aperte(s, sid) == 1


def test_j5_sync_manuale_e_aggancio_su_record_diversi_della_stessa_stima_nessun_duplicato(sito):
    """Un record della stima mai importato (legacy) e una dettagliata nuova:
    aggancio e sincronizzazione manuale partono insieme. Una sola richiesta
    aperta per la stima, chiunque vinca; e un secondo giro di sync non ne
    aggiunge."""
    from appointments_legacy.site_hook import safe_import_for_detail
    s = sito
    sid = _quick(s)
    vecchio = _q(s.m, "INSERT INTO stime_dettagliate (stima_id, agency_id, sopralluogo) VALUES (%s, 1, %s) "
                      "RETURNING id", (sid, "2026-11-03 09:00"))[0][0]
    nuovo = _q(s.m, "INSERT INTO stime_dettagliate (stima_id, agency_id, sopralluogo) VALUES (%s, 1, %s) "
                    "RETURNING id", (sid, "2026-11-04 09:00"))[0][0]
    pronti = threading.Barrier(2)
    esiti = {}

    def aggancio():
        pronti.wait()
        esiti["hook"] = safe_import_for_detail(nuovo, connection_factory=lambda: s.m["psycopg2"].connect(s.m["dsn"]))

    def manuale():
        pronti.wait()
        esiti["sync"] = _sync_manuale(s, 1)

    fili = [threading.Thread(target=aggancio), threading.Thread(target=manuale)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=60)
    assert esiti["hook"] is not None and "risposta" in esiti["sync"], esiti
    assert esiti["hook"]["errors"] == 0 and esiti["sync"]["errors"] == 0, esiti
    assert _aperte(s, sid) == 1
    assert len(_richieste(s, vecchio)) + len(_richieste(s, nuovo)) == 1
    _sync_manuale(s, 1)
    assert _aperte(s, sid) == 1


# ---------------------------------------------------------------------------
# FLUSSO 1 - dal sito al CRM, dopo l'integrazione della candidata F04/F06
#
# Il dettaglio dei singoli passi ha le sue prove (bridge: test_public_stima_
# core_crm_bridge; ricevute: test_public_submission_receipts_postgres;
# scheda: test_catalogo_canonico_1_postgres; routing: test_p27_6). Qui si
# percorre il flusso per intero, come lo vede il cliente.
# ---------------------------------------------------------------------------

def _crm(s):
    return {t: _conta(s, t) for t in ("stime", "contacts", "leads", "lead_stime", "properties",
                                      "property_site_sources", "property_accessories", "seller_timeline_events")}


def _collegamenti(s, sid):
    riga = _q(s.m, "SELECT s.agency_id, l.id, l.contact_id, l.agency_id, c.agency_id, pss.property_id "
                   "FROM stime s LEFT JOIN lead_stime ls ON ls.stima_id = s.id "
                   "LEFT JOIN leads l ON l.id = ls.lead_id LEFT JOIN contacts c ON c.id = l.contact_id "
                   "LEFT JOIN property_site_sources pss ON pss.stima_id = s.id AND pss.status = 'active' "
                   "WHERE s.id = %s", (sid,))
    assert len(riga) == 1, riga
    return dict(zip(("stima_agency", "lead_id", "contact_id", "lead_agency", "contact_agency", "property_id"), riga[0]))


def test_f1_a_nuovo_cliente_stima_routing_contatto_lead_scheda_pertinenze(sito):
    s = sito
    prima = _crm(s)
    persona = _persona()
    risposta = s.stima({**COMPLETA, **persona}, agency=2)       # routing (sostituito): agenzia 2
    assert risposta["receipt"]["status"] == "completed" and risposta["success"] is True
    sid = risposta["id"]
    dopo = _crm(s)
    assert {t: dopo[t] - prima[t] for t in ("stime", "contacts", "leads", "lead_stime", "properties", "property_site_sources")} \
        == {"stime": 1, "contacts": 1, "leads": 1, "lead_stime": 1, "properties": 1, "property_site_sources": 1}
    c = _collegamenti(s, sid)
    assert c["stima_agency"] == c["lead_agency"] == c["contact_agency"] == 2
    assert c["lead_id"] and c["contact_id"] and c["property_id"]
    assert _q(s.m, "SELECT email FROM contacts WHERE id = %s", (c["contact_id"],))[0][0] == persona["email"]
    pertinenze = {r[0]: (r[1], r[2]) for r in _q(
        s.m, "SELECT kind, surface_sqm, quantity FROM property_accessories WHERE property_id = %s", (c["property_id"],))}
    assert pertinenze == {"box": (18, None), "cantina": (None, None), "balcone": (None, 2)}, pertinenze
    assert risposta["receipt"]["contact_id"] == c["contact_id"] and risposta["receipt"]["lead_id"] == c["lead_id"]
    assert risposta["receipt"]["property_id"] == c["property_id"]
    assert _conta(s, "seller_timeline_events", "stima_id = %s", (sid,)) == 2    # stima_richiesta + stima_completata


def test_f1_b_cliente_esistente_stesso_contatto_nuovo_lead(sito):
    s = sito
    persona = _persona()
    prima_sid = s.stima({**COMPLETA, **persona})["id"]
    prima = _crm(s)
    seconda = s.stima({**COMPLETA, **persona, "via": "Via Nuova", "civico": "7"})
    assert seconda["receipt"]["status"] == "completed"
    dopo = _crm(s)
    assert dopo["contacts"] == prima["contacts"], "stessa email e telefono: nessun secondo contatto"
    assert dopo["stime"] == prima["stime"] + 1 and dopo["leads"] == prima["leads"] + 1
    assert _collegamenti(s, seconda["id"])["contact_id"] == _collegamenti(s, prima_sid)["contact_id"]
    assert _collegamenti(s, seconda["id"])["lead_id"] != _collegamenti(s, prima_sid)["lead_id"]


def test_f1_c_email_e_telefono_di_contatti_diversi_nessuna_associazione_errata(sito):
    s = sito
    a, b = _persona(), _persona()
    s.stima({**COMPLETA, **a})
    s.stima({**COMPLETA, **b})
    prima = _crm(s)
    conflitto = s.stima({**COMPLETA, **_persona(email=a["email"], telefono=b["telefono"])})
    dopo = _crm(s)
    # F04: il bridge risponde `conflict`; la ricevuta non lo spaccia per fatto.
    assert conflitto["receipt"]["status"] == "partial" and conflitto["success"] is False
    assert conflitto["receipt"]["steps"]["bridge"] == "failed"
    assert conflitto["receipt"]["errors"]["bridge"]["domain_status"] == "conflict"
    assert dopo["stime"] == prima["stime"] + 1, "la stima resta scritta"
    assert dopo["contacts"] == prima["contacts"] and dopo["leads"] == prima["leads"], "nessun contatto o lead inventato"
    c = _collegamenti(s, conflitto["id"])
    assert c["lead_id"] is None and c["contact_id"] is None and c["property_id"] is None


def test_f1_d_retry_e_doppio_click_nessun_duplicato(sito):
    s = sito
    payload = {**COMPLETA, **_persona(), "request_id": str(uuid.uuid4())}
    prima = _crm(s)
    prima_risposta = s.stima(payload)
    assert prima_risposta["receipt"]["status"] == "completed"
    for _ in range(2):                                  # retry sequenziale / risposta persa
        assert s.stima(payload)["id"] == prima_risposta["id"]
    pronti = threading.Barrier(2)
    esiti = []

    def invia():                                        # doppio click concorrente
        pronti.wait()
        esiti.append(s.stima(payload)["id"])

    fili = [threading.Thread(target=invia) for _ in range(2)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=60)
    assert esiti == [prima_risposta["id"]] * 2
    dopo = _crm(s)
    assert {t: dopo[t] - prima[t] for t in ("stime", "contacts", "leads", "lead_stime", "properties")} \
        == {"stime": 1, "contacts": 1, "leads": 1, "lead_stime": 1, "properties": 1}


def test_f1_e_una_scheda_nel_cestino_non_e_un_collegamento_della_ricevuta(sito):
    """Correzione alla candidata F04 durante l'integrazione (regola DELETE-ARCH
    2B2, `test_delete_arch_2b2::test_01`): la ricevuta legge `properties` con il
    predicato del Cestino. Una scheda cestinata dopo la stima non viene piu'
    riportata come collegamento, e il risultato non e' piu' certificato."""
    from fastapi.testclient import TestClient
    from tests.test_catalogo_canonico_1_postgres import _RECEIPT_PROOFS
    s = sito
    risposta = s.stima({**COMPLETA, **_persona()})
    sid, rid = risposta["id"], risposta["receipt"]["request_id"]
    pid = s.scheda_di(sid)
    assert risposta["receipt"]["property_id"] == pid and risposta["receipt"]["result_available"] is True

    def ricevuta():
        with TestClient(s.main.app) as client:
            r = client.get(f"/api/submissions/{rid}", headers={"X-Receipt-Key": _RECEIPT_PROOFS[rid]})
            assert r.status_code == 200, r.text
            return r.json()["receipt"]

    assert ricevuta()["property_id"] == pid
    # nel Cestino con lo stato della 085 (motivo del catalogo chiuso)
    _q(s.m, "UPDATE properties SET deleted_at = NOW(), deleted_reason = 'test_record' WHERE id = %s", (pid,))
    dopo = ricevuta()
    assert dopo["property_id"] is None and dopo["result_available"] is False
    assert dopo["status"] == "completed", "la ricevuta non cambia stato: cambia solo cio' che certifica"
