"""P30-HARDENING su PostgreSQL reale: A (niente prenotazioni nel passato),
B (idempotenza concorrente sullo stesso submission_token), C (access log
dell'app e log applicativi senza token, con l'API vera).

Fixture di A30-12 riusate (database usa-e-getta, API operatore e pubblica
vere su un'app FastAPI di test). Opt-in: senza `P29_TEST_DSN` si salta tutto.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tests.test_a30_12_public_booking_postgres import (  # noqa: F401
    DSN, _crea_link, _lunedi_prossimo, db, http, mondo)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL")

ROMA = ZoneInfo("Europe/Rome")
NON_PIU_DISPONIBILE = {"detail": "Questo orario non e' piu' disponibile"}


def _conta(mondo):  # noqa: F811
    return {t: mondo["sql"](f"SELECT count(*) FROM {t}")[0][0]
            for t in ("appointments", "appointment_events", "contacts",
                      "public_booking_submissions")}


@pytest.fixture
def spia_calendario(monkeypatch):
    """Quante volte l'Agenda segna 'da sincronizzare con Google' (A30-9B):
    la funzione vera viene chiamata, qui si contano le chiamate."""
    from appointments import service as appt_service
    vera = appt_service._gcal.on_appointment_mutation
    chiamate = []

    def spia(cur, agency_id, appointment_id):
        chiamate.append(appointment_id)
        return vera(cur, agency_id, appointment_id)

    monkeypatch.setattr(appt_service._gcal, "on_appointment_mutation", spia)
    return chiamate


def _link(http, mondo, **kw):  # noqa: F811
    mondo["orario_settimanale"](mondo["luca"], start=0, end=1440)    # tutto il giorno
    return _crea_link(http, "giorgio", assigned_user_id=mondo["luca"], **kw).json()["token"]


def _invia(http, token, start_at, *, sub=None, name="Mario Rossi", phone="3331234567"):  # noqa: F811
    if sub is None:
        sub = http(None).get(f"/api/public/booking/{token}").json()["submission_token"]
    return http(None).post(f"/api/public/booking/{token}/submit", json={
        "submission_token": sub, "start_at": start_at, "name": name, "phone": phone})


def _allinea(dt: datetime) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    return dt + timedelta(minutes=(15 - dt.minute % 15) % 15)


# ---------------------------------------------------------------------------
# A - START_AT NEL PASSATO
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("quanto_fa", [timedelta(days=2), timedelta(hours=1), timedelta(seconds=1)])
def test_A_B_start_nel_passato_rifiutato_senza_scritture(http, mondo, spia_calendario, quanto_fa):  # noqa: F811
    token = _link(http, mondo)
    prima = _conta(mondo)
    passato = datetime.now(timezone.utc) - quanto_fa
    for forma in (passato.isoformat(), passato.astimezone(ROMA).isoformat()):   # UTC o Roma: stesso istante
        r = _invia(http, token, forma)
        assert r.status_code == 409 and r.json() == NON_PIU_DISPONIBILE, r.text
        assert r.headers["cache-control"] == "no-store"
    assert _conta(mondo) == prima                    # B: nessun appuntamento/evento/contatto/submission
    assert spia_calendario == []                     # B: nessun calendar dirty


def test_A_orario_senza_fuso_resta_rifiutato_come_prima(http, mondo):  # noqa: F811
    token = _link(http, mondo)
    r = _invia(http, token, "2020-01-01T10:00:00")
    assert r.status_code == 422 and _conta(mondo)["appointments"] == 0


def test_C_start_futuro_201(http, mondo, spia_calendario):  # noqa: F811
    token = _link(http, mondo)
    futuro = _allinea(datetime.now(timezone.utc) + timedelta(hours=2))
    r = _invia(http, token, futuro.isoformat())
    assert r.status_code == 201, r.text
    assert datetime.fromisoformat(r.json()["start_at"]) == futuro
    assert len(spia_calendario) == 1


def _prossimo_cambio_ora_ottobre() -> date:
    oggi = datetime.now(ROMA).date()
    for anno in (oggi.year, oggi.year + 1):
        d = date(anno, 10, 31)
        d -= timedelta(days=(d.weekday() - 6) % 7)             # ultima domenica di ottobre
        if d > oggi + timedelta(days=1):
            return d
    raise AssertionError("irraggiungibile")


def test_D_prenotazione_futura_nel_giorno_del_cambio_ora(http, mondo):  # noqa: F811
    token = _link(http, mondo, duration_minutes=60)
    giorno = _prossimo_cambio_ora_ottobre()
    locale = datetime(giorno.year, giorno.month, giorno.day, 10, 0, tzinfo=ROMA)   # 10:00 CET (UTC+1)
    r = _invia(http, token, locale.isoformat())
    assert r.status_code == 201, r.text
    riga = mondo["sql"]("SELECT start_at, end_at FROM appointments")[0]
    assert riga[0] == locale and riga[0].utcoffset() is not None
    assert riga[0].astimezone(timezone.utc).hour == 9                            # dopo il cambio: UTC+1
    assert riga[1] - riga[0] == timedelta(minutes=60)


def test_A_retry_identico_di_una_prenotazione_riuscita_resta_idempotente_anche_dopo_l_orario(
        http, mondo, monkeypatch):  # noqa: F811
    """Il controllo del passato NON rompe l'idempotenza: il client che
    ripete (stesso token, stesso corpo) quando l'orario e' ormai passato
    riceve la stessa prenotazione, non un 409."""
    from appointments import service as appt_service
    token = _link(http, mondo)
    futuro = _allinea(datetime.now(timezone.utc) + timedelta(hours=1))
    sub = http(None).get(f"/api/public/booking/{token}").json()["submission_token"]
    primo = _invia(http, token, futuro.isoformat(), sub=sub)
    assert primo.status_code == 201
    monkeypatch.setattr(appt_service, "_adesso", lambda: futuro + timedelta(hours=3))
    ripetuto = _invia(http, token, futuro.isoformat(), sub=sub)
    assert ripetuto.status_code == 201 and ripetuto.json() == primo.json()
    nuovo = _invia(http, token, futuro.isoformat())                      # altro token: passato
    assert nuovo.status_code == 409
    assert _conta(mondo)["appointments"] == 1


# ---------------------------------------------------------------------------
# B - IDEMPOTENZA
# ---------------------------------------------------------------------------

def _inizio():
    return datetime.combine(_lunedi_prossimo(), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=10)


def test_E_doppio_submit_sequenziale_stesso_risultato(http, mondo, spia_calendario):  # noqa: F811
    token = _link(http, mondo)
    sub = http(None).get(f"/api/public/booking/{token}").json()["submission_token"]
    a = _invia(http, token, _inizio().isoformat(), sub=sub)
    b = _invia(http, token, _inizio().isoformat(), sub=sub)
    assert a.status_code == b.status_code == 201 and a.json() == b.json()
    assert _conta(mondo)["appointments"] == 1 and len(spia_calendario) == 1


def _in_parallelo(http, token, corpi):  # noqa: F811
    risposte = [None] * len(corpi)
    via = threading.Barrier(len(corpi))

    def invia(i):
        via.wait()
        risposte[i] = http(None).post(f"/api/public/booking/{token}/submit", json=corpi[i])

    fili = [threading.Thread(target=invia, args=(i,)) for i in range(len(corpi))]
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=60)
    return risposte


@pytest.mark.parametrize("giro", range(3))
def test_F_G_I_J_K_submit_concorrenti_stesso_token_stesso_payload(http, mondo, spia_calendario, giro):  # noqa: F811
    token = _link(http, mondo)
    sub = http(None).get(f"/api/public/booking/{token}").json()["submission_token"]
    corpo = {"submission_token": sub, "start_at": _inizio().isoformat(),
             "name": "Mario Rossi", "phone": "3331234567"}
    risposte = _in_parallelo(http, token, [corpo] * 5)
    assert [r.status_code for r in risposte] == [201] * 5, [r.text for r in risposte]   # G
    assert all(r.json() == risposte[0].json() for r in risposte)                          # stesso risultato
    conta = _conta(mondo)
    assert conta["appointments"] == 1                                                    # F
    assert conta["contacts"] == 1                                                        # K
    assert conta["public_booking_submissions"] == 1
    assert mondo["sql"]("SELECT count(*) FROM appointment_events WHERE event_type='created'")[0][0] == 1  # J
    assert len(spia_calendario) == 1                                                     # I
    assert mondo["sql"]("SELECT status FROM public_booking_submissions")[0][0] == "succeeded"


def test_G_il_ramo_del_perdente_e_esercitato_in_modo_deterministico(http, mondo, monkeypatch):  # noqa: F811
    """La corsa vera puo' serializzarsi da sola (il secondo arriva a cose
    fatte e prende il ramo 'gia riuscito'). Qui si forza il ramo del
    PERDENTE: il gemello crea l'appuntamento, poi questa richiesta vede
    il conflitto (come dopo il lock) - deve restituire lo stesso successo."""
    import public_booking.service as pb
    from appointments import errors as appt_errors
    vera = pb.create_public_booking_appointment

    def gemello_vince_poi_conflitto(ctx, **kw):
        vera(ctx, **kw)                                  # il gemello: crea e marca succeeded
        raise appt_errors.PublicSlotUnavailable("Questo orario non e' piu' disponibile")

    monkeypatch.setattr(pb, "create_public_booking_appointment", gemello_vince_poi_conflitto)
    token = _link(http, mondo)
    r = _invia(http, token, _inizio().isoformat())
    assert r.status_code == 201, r.text
    assert datetime.fromisoformat(r.json()["start_at"]) == _inizio()
    assert _conta(mondo)["appointments"] == 1


def test_H_stesso_token_payload_diverso_rifiuto_deterministico(http, mondo):  # noqa: F811
    token = _link(http, mondo)
    sub = http(None).get(f"/api/public/booking/{token}").json()["submission_token"]
    base = {"submission_token": sub, "start_at": _inizio().isoformat(), "phone": "3331234567"}
    # sequenziale
    assert _invia(http, token, base["start_at"], sub=sub, name="Mario Rossi").status_code == 201
    r = _invia(http, token, base["start_at"], sub=sub, name="Nome Diverso")
    assert r.status_code == 409 and r.json() == {
        "detail": "Questa richiesta risulta gia' inviata con dati diversi"}
    # concorrente, token nuovo: due payload diversi -> uno solo vince, l'altro rifiutato
    sub2 = http(None).get(f"/api/public/booking/{token}").json()["submission_token"]
    altro = (_inizio() + timedelta(hours=2)).isoformat()
    risposte = _in_parallelo(http, token, [
        {**base, "submission_token": sub2, "start_at": altro, "name": "Primo"},
        {**base, "submission_token": sub2, "start_at": altro, "name": "Secondo"}])
    assert sorted(r.status_code for r in risposte) == [201, 409], [r.text for r in risposte]
    assert _conta(mondo)["appointments"] == 2


def test_token_diversi_stesso_slot_resta_un_solo_vincitore(http, mondo):  # noqa: F811
    """NON ogni 409 diventa 201: token DIVERSI sullo stesso orario."""
    token = _link(http, mondo)
    corpi = [{"submission_token": http(None).get(f"/api/public/booking/{token}").json()["submission_token"],
              "start_at": _inizio().isoformat(), "name": f"Cliente {i}", "phone": "3331234567"}
             for i in range(4)]
    risposte = _in_parallelo(http, token, corpi)
    assert sorted(r.status_code for r in risposte) == [201, 409, 409, 409], [r.text for r in risposte]
    assert _conta(mondo)["appointments"] == 1


# ---------------------------------------------------------------------------
# MUTAZIONI (A, B): le prove sopra DEVONO accorgersene
# ---------------------------------------------------------------------------

def test_mut_senza_controllo_del_passato_la_prova_A_fallisce(http, mondo, monkeypatch):  # noqa: F811
    import public_booking.service as pb
    monkeypatch.setattr(pb, "_rifiuta_se_passato", lambda start_at: None)
    token = _link(http, mondo)
    r = _invia(http, token, (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    assert r.status_code == 201 and _conta(mondo)["appointments"] == 1     # test_A_B lo vede


def test_mut_senza_hardening_B_il_perdente_torna_409(http, mondo, monkeypatch):  # noqa: F811
    """Senza la rilettura della propria submission (comportamento pre-P30-
    hardening) il ramo forzato di test_G restituisce 409: la prova lo vede."""
    import public_booking.service as pb
    from appointments import errors as appt_errors
    vera = pb.create_public_booking_appointment

    def gemello_vince_poi_conflitto(ctx, **kw):
        vera(ctx, **kw)
        raise appt_errors.PublicSlotUnavailable("Questo orario non e' piu' disponibile")

    monkeypatch.setattr(pb, "create_public_booking_appointment", gemello_vince_poi_conflitto)
    monkeypatch.setattr(pb, "_esito_gia_riuscito", lambda cur, link, submission, fingerprint: None)
    token = _link(http, mondo)
    r = _invia(http, token, _inizio().isoformat())
    assert r.status_code == 409 and _conta(mondo)["appointments"] == 1


def test_mut_201_indiscriminato_dopo_il_conflitto_viene_scoperto(http, mondo, monkeypatch):  # noqa: F811
    """Una versione che, dopo un fallimento, restituisse un successo senza
    guardare la PROPRIA submission: token diversi sullo stesso slot avrebbero
    tutti 201 (test_token_diversi_... lo vede)."""
    import public_booking.service as pb
    from appointments import repository as appt_repo
    from core.database import core_cursor

    def qualunque_successo(cur, link, submission, fingerprint):
        with core_cursor() as (_, c):
            c.execute("SELECT id FROM appointments ORDER BY id LIMIT 1")
            riga = c.fetchone()
        if riga is None:
            return None
        return pb._prenotazione_pubblica(appt_repo.get_appointment(cur, link["agency_id"], riga["id"]))

    monkeypatch.setattr(pb, "_esito_gia_riuscito", qualunque_successo)
    token = _link(http, mondo)
    corpi = [{"submission_token": http(None).get(f"/api/public/booking/{token}").json()["submission_token"],
              "start_at": _inizio().isoformat(), "name": f"Cliente {i}", "phone": "3331234567"}
             for i in range(3)]
    risposte = [http(None).post(f"/api/public/booking/{token}/submit", json=c) for c in corpi]
    assert [r.status_code for r in risposte] != [201, 409, 409]


# ---------------------------------------------------------------------------
# C - ACCESS LOG VERO (uvicorn) CON L'API VERA, E LOG APPLICATIVI
# ---------------------------------------------------------------------------

@pytest.fixture
def server_con_access_log(mondo):  # noqa: F811
    """uvicorn vero, logging configurato da uvicorn (dictConfig di default)
    PRIMA dell'import dell'app come in produzione, access log ATTIVO; le
    righe vengono raccolte con il vero `AccessFormatter` di uvicorn."""
    import uvicorn
    from uvicorn.logging import AccessFormatter

    config = uvicorn.Config("tests.test_p30_hardening_postgres:_app_di_prova", factory=True,
                            host="127.0.0.1", port=_porta_libera(), access_log=True,
                            lifespan="off", log_level="info")
    righe: list[str] = []

    class Raccolta(logging.Handler):
        def emit(self, record):
            righe.append(self.format(record))

    raccolta = Raccolta()
    raccolta.setFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s',
                                          use_colors=False))
    logging.getLogger("uvicorn.access").addHandler(raccolta)
    srv = uvicorn.Server(config)
    filo = threading.Thread(target=srv.run, daemon=True)
    filo.start()
    limite = time.time() + 15
    while not srv.started:
        if time.time() > limite:
            raise RuntimeError("server di prova non partito")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{config.port}", righe
    srv.should_exit = True
    filo.join(timeout=15)
    logging.getLogger("uvicorn.access").removeHandler(raccolta)


def _app_di_prova():
    from fastapi import FastAPI

    from public_booking.page import PAGE_PREFIX, PublicBookingPage
    from public_booking.public_router import router
    app = FastAPI()
    app.include_router(router)
    app.mount(PAGE_PREFIX, PublicBookingPage(), name="public-booking-page")
    return app


def _porta_libera() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_L_M_N_O_access_log_e_log_applicativi_senza_token(server_con_access_log, http, mondo, caplog):  # noqa: F811
    import json
    import urllib.error
    import urllib.request
    base, righe = server_con_access_log
    token = _link(http, mondo)
    caplog.set_level(logging.DEBUG)                    # O: TUTTI i logger, al livello piu' basso

    def chiama(percorso, corpo=None):
        req = urllib.request.Request(base + percorso, method="POST" if corpo else "GET",
                                     data=json.dumps(corpo).encode() if corpo else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"null") if "api" in percorso else None
        except urllib.error.HTTPError as e:
            return e.code, None

    stato, meta = chiama(f"/api/public/booking/{token}")
    assert stato == 200
    sub = meta["submission_token"]
    oggi = datetime.now(timezone.utc)
    q = f"?from={oggi.strftime('%Y-%m-%dT%H:%M:%SZ')}&to={(oggi + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    assert chiama(f"/api/public/booking/{token}/slots{q}")[0] == 200
    assert chiama(f"/api/public/booking/{token}/submit", {
        "submission_token": sub, "start_at": _inizio().isoformat(),
        "name": "Mario Rossi", "phone": "3331234567"})[0] == 201
    assert chiama("/api/public/booking/tokenInesistente_" + "z" * 26)[0] == 404
    assert chiama(f"/prenota/{token}")[0] == 200
    assert chiama("/prenota/assets/booking.css")[0] == 200
    time.sleep(0.3)

    registro = "\n".join(righe)
    assert len(righe) >= 6, registro
    assert token not in registro and sub not in registro                  # L, M
    assert "tokenInesistente" not in registro                            # anche i token sbagliati
    assert '"GET /api/public/booking/[REDACTED] HTTP/1.1" 200' in registro
    assert f'"GET /api/public/booking/[REDACTED]/slots{q} HTTP/1.1" 200' in registro
    assert '"POST /api/public/booking/[REDACTED]/submit HTTP/1.1" 201' in registro
    assert '"GET /api/public/booking/[REDACTED] HTTP/1.1" 404' in registro
    assert '"GET /prenota/[REDACTED] HTTP/1.1" 200' in registro
    assert '"GET /prenota/assets/booking.css HTTP/1.1" 200' in registro   # N
    applicativi = "\n".join(f"{r.name} {r.getMessage()} {r.args!r}" for r in caplog.records)
    assert token not in applicativi and sub not in applicativi, applicativi   # O
