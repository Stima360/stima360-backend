"""P30 - la pagina pubblica `/prenota/{token}` CONTRO il server vero.

Un server HTTP reale (uvicorn, in un thread) serve la pagina (`PublicBookingPage`)
e l'API pubblica A30-12 (`public_booking.public_router`) su un PostgreSQL
usa-e-getta; il JavaScript VERO della pagina (`booking-page.js`) gira in node
dentro lo stub di DOM di P26-4 e parla con quel server con il `fetch` vero di
node. Nessun doppio dell'API: cio' che la pagina mostra e invia e' quello che
il backend certificato restituisce e accetta.

Cosa si prova (lettere del gate P30):

  A/J  apertura, scelta, invio: nasce UN appuntamento con agente, tipo e durata
       del LINK (mai del client), e il contatto con i dati inseriti;
  B/C/D token inesistente, scaduto, disattivato, ruotato: la stessa pagina neutra;
  F/T  gli orari mostrati sono ESATTAMENTE quelli dell'API, e la disponibilita'
       HARD (orari settimanali, chiusure, eccezioni) arriva invariata;
  G    agente senza orari configurati: nessun orario, messaggio chiaro;
  K    un altro cliente prende l'orario mentre si compila: 409, nuovo
       submission_token, orari ricaricati, si prenota un altro orario;
  L    doppio click + risposta persa + retry: UN solo appuntamento;
  L2   (server) submit concorrenti con lo stesso submission_token: mai due
       appuntamenti.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Senza node: SKIPPED (BLOCKED).
"""
from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from tests import test_p30_public_booking_page as pagina
from tests.test_a30_12_public_booking_postgres import (  # noqa: F401
    DSN, _crea_link, _lunedi_prossimo, db, http, mondo)

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL"),
    pytest.mark.skipif(pagina.NODE is None, reason="node non disponibile: prove P30 NON eseguite (BLOCKED)"),
]


@pytest.fixture
def server(mondo):  # noqa: F811
    import uvicorn
    from fastapi import FastAPI

    from public_booking.page import PAGE_PREFIX, PublicBookingPage
    from public_booking.public_router import router

    app = FastAPI()
    app.include_router(router)
    app.mount(PAGE_PREFIX, PublicBookingPage(), name="public-booking-page")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning",
                                       access_log=False, lifespan="off"))
    filo = threading.Thread(target=srv.run, daemon=True)
    filo.start()
    limite = time.time() + 15
    while not srv.started:
        if time.time() > limite:
            raise RuntimeError("il server di prova non e' partito")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{porta}"
    srv.should_exit = True
    filo.join(timeout=15)


# Il fetch VERO di node verso il server di prova. `__perdi(n)` fa arrivare la
# richiesta al server ma butta via le prossime n risposte del submit (la rete
# che cade dopo l'invio): e' il caso in cui un retry non deve duplicare nulla.
FETCH_VERO = r"""
const __vero = globalThis.fetch;
const __chiamate = [];
let __daPerdere = 0;
let __ultimiSlot = null;
globalThis.__perdi = (n) => { __daPerdere = n; };
globalThis.__diretto = (url, opzioni) => __vero(__BASE__ + url, opzioni);
// il primo orario dell'ultima risposta slot = quello che PRIMO_ORARIO sceglie
globalThis.__slotScelto = () => __ultimiSlot.slots[0].start_at;
globalThis.__inVolo = 0;
globalThis.fetch = async (url, options = {}) => {
  __chiamate.push({ url: String(url), options });
  globalThis.__inVolo += 1;
  try {
    const r = await __vero(__BASE__ + url, options);
    if (String(url).includes('/slots?') && r.status === 200) __ultimiSlot = await r.clone().json();
    if (__daPerdere > 0 && String(url).endsWith('/submit')) {
      await r.text();
      __daPerdere -= 1;
      throw new TypeError('risposta persa');
    }
    return r;
  } finally {
    globalThis.__inVolo -= 1;
  }
};
"""


def _pagina(asset, base, token, scenario):
    adesso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return pagina._run(asset, "", scenario, path=f"/prenota/{token}", adesso=adesso,
                       fetch=FETCH_VERO.replace("__BASE__", json.dumps(base)))


@pytest.fixture(scope="module")
def asset(tmp_path_factory):
    import shutil
    dst = tmp_path_factory.mktemp("p30-e2e") / "assets"
    shutil.copytree(pagina.ASSETS, dst)
    return dst


def _appuntamenti(mondo):  # noqa: F811
    return mondo["sql"](
        "SELECT a.assigned_user_id, a.appointment_type, a.status, a.start_at, a.end_at, "
        "       a.source, c.display_name, c.phone, c.email "
        "FROM appointments a LEFT JOIN contacts c ON c.id = a.contact_id ORDER BY a.start_at")


def _ora_roma(dt):
    from zoneinfo import ZoneInfo
    return dt.astimezone(ZoneInfo("Europe/Rome")).strftime("%H:%M")


PRIMO_ORARIO = """
  const giornoScelto = tutti('[data-day]')[0].getAttribute('aria-label');
  await giorno(0);
  const scelto = tutti('[data-slot]')[0].textContent;
  await orario(scelto);
"""


def test_01_la_pagina_vera_e_servita_con_no_store(server, mondo, http):  # noqa: F811
    import urllib.request
    token = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"]).json()["token"]
    with urllib.request.urlopen(f"{server}/prenota/{token}") as r:
        corpo = r.read().decode("utf-8")
        assert r.status == 200 and r.headers["Cache-Control"] == "no-store"
        assert r.headers["Referrer-Policy"] == "no-referrer"
    assert token not in corpo and 'id="booking-app"' in corpo


def test_A_J_prenotazione_completa_crea_un_solo_appuntamento_del_link(server, asset, mondo, http):  # noqa: F811
    mondo["orario_settimanale"](mondo["luca"])
    token = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"], appointment_type="inspection",
                       duration_minutes=45, buffer_after_minutes=15).json()["token"]
    out = _pagina(asset, server, token, PRIMO_ORARIO + """
      await compila({ nome: 'Mario Rossi', telefono: '+39 333 1234567', email: 'mario@example.it' });
      await conferma();
      report({ scelto });
    """)
    assert out["passo"] == "done", out["testo"]
    assert "Prenotazione confermata" in out["testo"]
    assert "Sopralluogo" in out["testo"] and "Luca Agente" in out["testo"]
    (riga,) = _appuntamenti(mondo)
    assert riga["assigned_user_id"] == mondo["luca"]          # dal link, non dal client
    assert riga["appointment_type"] == "inspection"
    assert riga["end_at"] - riga["start_at"] == timedelta(minutes=45)
    assert riga["status"] == "scheduled" and riga["source"] == "booking_link"
    assert (riga["display_name"], riga["phone"], riga["email"]) == (
        "Mario Rossi", "+39 333 1234567", "mario@example.it")
    assert _ora_roma(riga["start_at"]) == out["scelto"]       # l'orario scelto, esatto
    ((post,),) = [[c for c in out["chiamate"] if c["m"] == "POST"]]
    assert set(post["body"]) == {"submission_token", "start_at", "name", "phone", "email"}
    assert mondo["sql"]("SELECT status FROM public_booking_submissions")[0][0] == "succeeded"
    # gli id di questo database sono interi piccoli (confondibili con "2 ott"):
    # la prova sugli id interni e' test_E_O (valori distinguibili); qui i token.
    for segreto in (token, post["body"]["submission_token"]):
        assert segreto not in out["dom"] and segreto not in out["testo"]
    for chiave in ("agency_id", "user_id", "contact_id", "appointment_id"):
        assert chiave not in out["dom"]


@pytest.mark.parametrize("caso", ["inesistente", "scaduto", "disattivato", "ruotato"])
def test_B_C_D_link_non_prenotabile_stessa_pagina_neutra(server, asset, mondo, http, caso):  # noqa: F811
    mondo["orario_settimanale"](mondo["luca"])
    link = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"]).json()
    token = link["token"]
    if caso == "inesistente":
        token = "nessunoLoHaMaiCreato_" + "z" * 22
    elif caso == "scaduto":
        mondo["sql"]("UPDATE public_booking_links SET expires_at = now() - interval '1 minute' "
                     "WHERE id = %s", (link["id"],))
    elif caso == "disattivato":
        http("giorgio").post(f"/api/appointments/booking-links/{link['id']}/disable")
    elif caso == "ruotato":
        http("giorgio").post(f"/api/appointments/booking-links/{link['id']}/rotate")
    out = _pagina(asset, server, token, "report();")
    assert out["stato"] == "unavailable"
    assert out["testo"] == "Questo link non è disponibile.Contatta l’agenzia per riceverne uno nuovo."
    assert [c["url"] for c in out["chiamate"]] == [f"/api/public/booking/{token}"]
    assert mondo["sql"]("SELECT count(*) FROM appointments")[0][0] == 0


def test_R_rotate_il_vecchio_link_non_funziona_il_nuovo_apre_p30(server, asset, mondo, http):  # noqa: F811
    """Il link mostrato da create/rotate e' `/prenota/<token>`: il nuovo apre
    davvero la pagina e i suoi orari, il vecchio mostra la pagina neutra."""
    import urllib.request
    mondo["orario_settimanale"](mondo["luca"])
    link = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"]).json()
    vecchio = link["token"]
    nuovo = http("giorgio").post(f"/api/appointments/booking-links/{link['id']}/rotate").json()["token"]
    assert nuovo != vecchio
    # lo stesso percorso che la Booking Links UI mostra (PUBLIC_BOOKING_PATH = '/prenota/')
    for token in (vecchio, nuovo):
        with urllib.request.urlopen(f"{server}/prenota/{token}") as r:
            assert r.status == 200 and b'id="booking-app"' in r.read()
    out_vecchio = _pagina(asset, server, vecchio, "report();")
    out_nuovo = _pagina(asset, server, nuovo, "report();")
    assert out_vecchio["stato"] == "unavailable"
    assert out_nuovo["passo"] == "choose" and out_nuovo["orari"]
    assert "Luca Agente" in out_nuovo["testo"]


def test_F_T_orari_mostrati_identici_all_api_con_disponibilita_hard(server, asset, mondo, http):  # noqa: F811
    """Orari 09:00-18:00 tutti i giorni, ma: una CHIUSURA d'agenzia un giorno,
    un'eccezione NEGATIVA (assenza) un altro, un'eccezione POSITIVA serale un
    terzo, e un appuntamento gia' preso. La pagina deve mostrare ESATTAMENTE
    gli orari che l'API restituisce per la stessa finestra."""
    from zoneinfo import ZoneInfo
    roma = ZoneInfo("Europe/Rome")
    mondo["orario_settimanale"](mondo["luca"])
    oggi = datetime.now(roma).date()
    chiuso, assente, serale = (oggi + timedelta(days=n) for n in (2, 3, 4))
    mondo["sql"]("INSERT INTO agency_closures (agency_id, closure_date, start_minute, end_minute) "
                 "VALUES (%s,%s,0,1440)", (mondo["a"], chiuso))
    mondo["sql"]("INSERT INTO agent_availability_exceptions (agency_id, user_id, exception_date, "
                 "is_available, start_minute, end_minute) VALUES (%s,%s,%s,false,0,1440)",
                 (mondo["a"], mondo["luca"], assente))
    mondo["sql"]("INSERT INTO agent_availability_exceptions (agency_id, user_id, exception_date, "
                 "is_available, start_minute, end_minute) VALUES (%s,%s,%s,true,1140,1260)",
                 (mondo["a"], mondo["luca"], serale))
    token = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"], duration_minutes=60).json()["token"]
    # un appuntamento gia' preso domani alle 10:00 (buffer 0): 10:00 sparisce
    domani = oggi + timedelta(days=1)
    dieci = datetime(domani.year, domani.month, domani.day, 10, 0, tzinfo=roma)
    meta = http(None).get(f"/api/public/booking/{token}").json()
    r = http(None).post(f"/api/public/booking/{token}/submit", json={
        "submission_token": meta["submission_token"], "start_at": dieci.isoformat(),
        "name": "Gia' Prenotato", "phone": "3330000000"})
    assert r.status_code == 201, r.text

    out = _pagina(asset, server, token, """
      const perGiorno = {};
      for (let i = 0; i < tutti('[data-day]').length; i += 1) {
        await giorno(i);
        perGiorno[tutti('[data-day]')[i].getAttribute('aria-label')] = tutti('[data-slot]').map((b) => b.textContent);
      }
      report({ perGiorno });
    """)
    (slots_page,) = [c for c in out["chiamate"] if "/slots?" in c["url"]]
    diretto = http(None).get(slots_page["url"]).json()["slots"]
    atteso = {}
    for s in diretto:
        inizio = datetime.fromisoformat(s["start_at"]).astimezone(roma)
        atteso.setdefault(inizio.date(), []).append(inizio.strftime("%H:%M"))
    mostrati = out["perGiorno"]
    assert sum(len(v) for v in mostrati.values()) == len(diretto)
    assert sorted(len(v) for v in mostrati.values()) == sorted(len(v) for v in atteso.values())
    etichette = {giorno: [k for k in mostrati if k.split()[1] == str(giorno.day)] for giorno in atteso}
    for giorno, orari in atteso.items():
        (k,) = etichette[giorno]
        assert mostrati[k] == orari, giorno
    assert chiuso not in atteso and assente not in atteso    # HARD: nessun orario quei giorni
    assert "19:00" in atteso[serale] and "20:00" in atteso[serale]   # eccezione positiva
    assert "10:00" not in atteso[domani]                     # occupato


def test_G_agente_senza_orari_nessuno_slot(server, asset, mondo, http):  # noqa: F811
    token = _crea_link(http, "giorgio", assigned_user_id=mondo["marta"]).json()["token"]
    out = _pagina(asset, server, token, "report();")
    assert "Al momento non ci sono orari disponibili nelle prossime settimane." in out["testo"]
    assert out["orari"] == [] and len([c for c in out["chiamate"] if "/slots?" in c["url"]]) == 4


def test_K_orario_preso_da_un_altro_cliente_durante_la_compilazione(server, asset, mondo, http):  # noqa: F811
    mondo["orario_settimanale"](mondo["luca"])
    token = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"]).json()["token"]
    out = _pagina(asset, server, token, PRIMO_ORARIO + """
      await compila({ nome: 'Cliente Lento', telefono: '3471112233' });
      // un altro cliente, con la SUA pagina, prende lo stesso orario adesso
      const meta = await (await __diretto('/api/public/booking/' + '__TOKEN__', {})).json();
      const altro = await __diretto('/api/public/booking/__TOKEN__/submit', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ submission_token: meta.submission_token, start_at: __slotScelto(),
                               name: 'Cliente Veloce', phone: '3339998877' }) });
      await conferma();
      const dopo = { passo: sel('[data-step]').dataset.step, avvisi: tutti('.notice').map((p) => p.textContent),
                     orari: tutti('[data-slot]').map((b) => b.textContent), altro: altro.status,
                     giorno: (tutti('[data-day]').find((b) => b.getAttribute('aria-pressed') === 'true')
                              || { getAttribute: () => null }).getAttribute('aria-label') };
      await clic(tutti('[data-slot]')[0]);
      sel('form').dispatch('submit'); await attendi();
      await conferma();
      report({ scelto, giornoScelto, dopo });
    """.replace("__TOKEN__", token))
    assert out["dopo"]["altro"] == 201
    assert out["dopo"]["passo"] == "choose"
    assert out["dopo"]["avvisi"] == ["Questo orario non è più disponibile. Scegline un altro."]
    posts = [c for c in out["chiamate"] if c["m"] == "POST"]
    assert len(posts) == 2
    assert posts[0]["body"]["submission_token"] != posts[1]["body"]["submission_token"]
    assert out["passo"] == "done"
    righe = _appuntamenti(mondo)
    assert sorted(r["display_name"] for r in righe) == ["Cliente Lento", "Cliente Veloce"]
    assert len({r["start_at"] for r in righe}) == 2             # mai lo stesso orario due volte
    veloce = next(r for r in righe if r["display_name"] == "Cliente Veloce")
    assert _ora_roma(veloce["start_at"]) == out["scelto"]       # aveva preso proprio quello
    if out["dopo"]["giorno"] == out["giornoScelto"]:           # stesso giorno ricaricato:
        assert out["scelto"] not in out["dopo"]["orari"]         # l'orario preso non c'e' piu'


def test_L_doppio_click_e_risposta_persa_un_solo_appuntamento(server, asset, mondo, http):  # noqa: F811
    mondo["orario_settimanale"](mondo["luca"])
    token = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"]).json()["token"]
    out = _pagina(asset, server, token, PRIMO_ORARIO + """
      await compila();
      __perdi(1);                                   // il server riceve, la risposta si perde
      const b = sel('[data-confirm]');
      b.dispatch('click'); b.dispatch('click');     // doppio click
      await attendi(200);
      const incerto = sel('.step-title').textContent;
      await clic(bottone('Riprova')); await attendi(200);
      report({ incerto });
    """)
    assert out["incerto"] == "Conferma in sospeso"
    posts = [c for c in out["chiamate"] if c["m"] == "POST"]
    assert len(posts) == 2 and posts[0]["body"] == posts[1]["body"]
    assert out["passo"] == "done"
    assert mondo["sql"]("SELECT count(*) FROM appointments")[0][0] == 1
    assert mondo["sql"]("SELECT count(*), min(status) FROM public_booking_submissions")[0] == [1, "succeeded"]


def test_L2_server_submit_concorrenti_con_lo_stesso_token_mai_due_appuntamenti(mondo, http):  # noqa: F811
    """Non passa dalla pagina (che invia una richiesta alla volta): e' la difesa
    del server, dimostrata. Stesso submission_token, stesso corpo, 4 richieste
    insieme: UN appuntamento. (Chi perde la corsa oggi riceve 409 invece dello
    stesso 201: nessun duplicato, vedi rischi residui del report P30.)"""
    mondo["orario_settimanale"](mondo["luca"])
    token = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"]).json()["token"]
    meta = http(None).get(f"/api/public/booking/{token}").json()
    inizio = datetime.combine(_lunedi_prossimo(), datetime.min.time(),
                              tzinfo=timezone.utc) + timedelta(hours=10)
    corpo = {"submission_token": meta["submission_token"], "start_at": inizio.isoformat(),
             "name": "Mario Rossi", "phone": "3331234567"}
    esiti = []

    def invia():
        esiti.append(http(None).post(f"/api/public/booking/{token}/submit", json=corpo).status_code)

    fili = [threading.Thread(target=invia) for _ in range(4)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=30)
    assert len(esiti) == 4 and 201 in esiti and set(esiti) <= {201, 409}, esiti
    assert mondo["sql"]("SELECT count(*) FROM appointments")[0][0] == 1
    # e un retry SEQUENZIALE dopo la corsa restituisce la stessa prenotazione
    dopo = http(None).post(f"/api/public/booking/{token}/submit", json=corpo)
    assert dopo.status_code == 201
    assert datetime.fromisoformat(dopo.json()["start_at"]) == inizio
    assert mondo["sql"]("SELECT count(*) FROM appointments")[0][0] == 1
