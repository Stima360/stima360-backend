"""LMC-9 - "Richiedi verifica gratuita": la prima conversione vera.

LA DIFFERENZA CON LMC-7, CHE E' TUTTO IL PUNTO.

Gli eventi di LMC-7 sono osservazioni: il proprietario ha aperto una pagina,
e se il tracciamento fallisce non e' successo niente di importante - la
pagina l'ha vista comunque. Questa e' una RICHIESTA: la persona ha detto
"chiamatemi". Se non la registriamo e le rispondiamo "fatto", le abbiamo
mentito, e lei aspettera' una telefonata che nessuno fara'.

Da qui tre regole che rovesciano quelle di LMC-7: la scrittura non e'
fail-open, il successo si dichiara solo se la riga esiste davvero, e la CTA
chiede conferma prima di partire - uno scroll che diventa una richiesta
commerciale e' esattamente il genere di falso segnale che questo progetto ha
evitato in ogni fase.

Resta invece identica l'idempotenza: chi tocca due volte ha chiesto una cosa
sola.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from owner import interest_service, tracking

ROOT = Path(__file__).resolve().parents[1]
PORTAL = ROOT / "static" / "owner_portal"
INDEX = PORTAL / "index.html"
APP_JS = PORTAL / "assets" / "app.js"
TIMELINE_JS = ROOT / "static" / "os_shell" / "assets" / "components" / "timeline.js"

ADESSO = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
CONSULTATION = "owner_consultation_requested"


def _senza_commenti(testo: str) -> str:
    testo = re.sub(r"/\*.*?\*/", "", testo, flags=re.S)
    return "\n".join(riga.split("//", 1)[0] for riga in testo.splitlines())


def _regioni(testo: str, apertura: str, chiusura: str) -> str:
    regioni = re.findall(re.escape(apertura) + r"(.*?)" + re.escape(chiusura),
                         testo, flags=re.S)
    assert regioni, f"marcatori {apertura} assenti"
    return _senza_commenti("\n".join(regioni))


def evento(tipo, quando, source="owner_portal"):
    return {"event_type": tipo, "event_source": source,
            "occurred_at": quando, "payload": {}}


def giorni_fa(n, ora=10):
    return (ADESSO - timedelta(days=n)).replace(hour=ora, minute=0)


def interesse(eventi):
    return interest_service.build_interest(eventi, now=ADESSO)["interest"]


# ---------------------------------------------------------------------------
# A - l'evento
# ---------------------------------------------------------------------------

def test_a1_il_tipo_esiste_ed_e_dichiarato():
    assert tracking.CONSULTATION_REQUESTED == CONSULTATION
    assert CONSULTATION in tracking.EVENT_TYPES
    assert len(CONSULTATION) <= 50, "event_type e' VARCHAR(50)"


def test_a2_non_e_un_azione_dichiarabile_dal_client():
    """Ha una rotta sua: passa dalla porta delle richieste, non da quella
    delle osservazioni."""
    assert CONSULTATION not in tracking.ACTIONS.values()
    assert "consultation_requested" not in tracking.ACTIONS


def test_a3_il_payload_resta_minimo():
    payload = tracking.event_payload(CONSULTATION)
    assert payload == {"action": "consultation_requested"}


def test_a4_la_chiave_e_per_owner_stima_e_giorno_utc():
    chiave = tracking.idempotency_key(CONSULTATION, owner_account_id=7,
                                      stima_id=501, when=ADESSO)
    assert chiave == ("owner_portal:owner_consultation_requested:"
                      "owner:7:stima:501:day:2026-09-19")
    assert len(chiave) <= 255


def test_a5_nessun_dato_personale_nella_chiave_ne_nel_payload():
    chiave = tracking.idempotency_key(CONSULTATION, owner_account_id=7,
                                      stima_id=501, when=ADESSO)
    assert re.fullmatch(r"[a-z0-9_:\-]+", chiave)
    testo = json.dumps(tracking.event_payload(CONSULTATION)).lower()
    for vietato in ("email", "telefono", "@", "nome", "ip", "agent", "token",
                    "budget", "score"):
        assert vietato not in testo, vietato


def test_a6_la_richiesta_non_passa_dal_percorso_fail_open():
    """`track_consultation_request` non deve ingoiare: se ingoiasse,
    l'unico modo di sapere che la richiesta e' andata persa sarebbe
    chiederlo al proprietario che aspetta la telefonata."""
    # Nel CODICE, non nella prosa: il docstring SPIEGA perche' non si usa
    # `safe_record_event`, quindi lo nomina.
    albero = ast.parse(inspect.getsource(tracking))
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            nodo.value = ""
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == "track_consultation_request":
            codice = ast.unparse(nodo)
            break
    else:
        raise AssertionError("funzione non trovata")
    assert "_fail_open" not in codice
    assert "except" not in codice
    assert "safe_record_event" not in ast.unparse(albero)


# ---------------------------------------------------------------------------
# B - il radar: la richiesta vale piu' di qualunque conteggio
# ---------------------------------------------------------------------------

def test_b1_una_richiesta_accende_consultation_requested():
    vista = interesse([evento(CONSULTATION, giorni_fa(1))])
    assert vista["consultation_requested"] is True


def test_b2_e_porta_il_livello_a_high_da_sola():
    """Nessuna apertura precedente, nessun ritorno: ha chiesto di essere
    richiamata, e questo batte qualunque conteggio."""
    vista = interesse([evento(CONSULTATION, giorni_fa(1))])
    assert vista["level"] == "high"
    assert vista["active_days_7d"] == 1
    assert vista["value_history_viewed"] is False
    assert vista["buyer_demand_viewed"] is False


def test_b3_la_ragione_e_esplicita_e_viene_per_prima():
    vista = interesse([evento("owner_home_viewed", giorni_fa(2)),
                       evento("owner_home_viewed", giorni_fa(1)),
                       evento(CONSULTATION, giorni_fa(1))])
    assert vista["reasons"][0] == "Ha richiesto una verifica gratuita"
    assert vista["level"] == "high"


def test_b4_oltre_la_finestra_non_e_piu_una_richiesta_attuale():
    """Il read-model dichiara 30 giorni: una richiesta di quaranta giorni fa
    non e' un fatto di oggi. L'evento resta nel database, ma il radar
    racconta la finestra che dice di raccontare."""
    vista = interesse([evento(CONSULTATION, giorni_fa(40))])
    assert vista["consultation_requested"] is False
    assert vista["level"] == "none"
    assert vista["active_days_30d"] == 0


def test_b5_la_finestra_e_quella_dichiarata_e_non_una_seconda():
    assert interest_service.WINDOW_DAYS == 30
    assert interest_service.CONSULTATION_REQUESTED == CONSULTATION
    assert CONSULTATION in interest_service.TRACKED_EVENTS


def test_b6_la_richiesta_conta_come_giorno_attivo():
    vista = interesse([evento(CONSULTATION, giorni_fa(3))])
    assert vista["active_days_30d"] == 1
    assert vista["active_days_7d"] == 1
    assert vista["last_activity_at"] == giorni_fa(3).isoformat()


def test_b7_nessun_punteggio():
    vista = interesse([evento(CONSULTATION, giorni_fa(1))])
    assert "score" not in json.dumps(vista).lower()


def test_b8_home_updated_resta_di_lmc10():
    vista = interesse([evento(CONSULTATION, giorni_fa(1))])
    assert vista["home_updated"] is False


# ---------------------------------------------------------------------------
# C - la rotta: forte, non fail-open
# ---------------------------------------------------------------------------

def _rotta():
    from owner import router_portal

    albero = ast.parse(inspect.getsource(router_portal))
    for nodo in albero.body:
        if isinstance(nodo, ast.FunctionDef) and nodo.name == "consultation_request":
            return nodo
    raise AssertionError("rotta consultation_request non trovata")


def test_c1_la_rotta_esiste_ed_e_semanticamente_sua():
    from owner import router_portal

    sorgente = inspect.getsource(router_portal)
    assert "/homes/{stima_id}/consultation-request" in sorgente
    corpo = "\n".join(ast.unparse(riga) for riga in _rotta().body)
    assert "track_consultation_request" in corpo


def test_c2_non_riusa_la_rotta_analytics():
    corpo = "\n".join(ast.unparse(riga) for riga in _rotta().body)
    assert "track_action" not in corpo, "semantica diversa, percorso diverso"


def test_c3_distingue_il_rifiuto_dal_guasto():
    """404 quando l'accesso non c'e', 503 quando non siamo riusciti a
    scrivere: sono due cose diverse e il proprietario deve poterle
    distinguere, perche' una si riprova e l'altra no."""
    corpo = "\n".join(ast.unparse(riga) for riga in _rotta().body)
    assert "404" in corpo and "503" in corpo


def test_c4_non_e_fail_open():
    corpo = "\n".join(ast.unparse(riga) for riga in _rotta().body)
    assert "nf(" not in corpo, "`nf` trasformerebbe un guasto in un 404"
    assert "status_code=204" not in corpo


# ---------------------------------------------------------------------------
# D - il frontend: una CTA sola, e una conferma
# ---------------------------------------------------------------------------

def test_d1_una_sola_cta_nel_dettaglio():
    html = INDEX.read_text(encoding="utf-8")
    assert "Richiedi verifica gratuita" in html
    assert html.count("Richiedi verifica gratuita") == 1, "una CTA, non tre"
    for concorrente in ("Voglio essere ricontattato", "Parla con un agente",
                        "Richiedi valutazione"):
        assert concorrente not in html, concorrente


def test_d2_la_cta_sta_nel_dettaglio_non_nella_lista():
    html = INDEX.read_text(encoding="utf-8")
    dettaglio = html[html.index('id="home-detail-content"'):]
    assert "Richiedi verifica gratuita" in dettaglio


def test_d3_esiste_una_conferma_esplicita():
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="home-consultation-confirm"' in html
    assert 'id="home-consultation-cancel"' in html
    assert 'id="home-consultation-send"' in html
    # Il testo va a capo nel markup: si confronta lo spazio normalizzato.
    piatto = " ".join(html.lower().split())
    assert "vuoi essere ricontattato da stima360 per una verifica gratuita" in piatto
    assert "della tua casa?" in piatto
    assert "annulla" in piatto and "invia richiesta" in piatto.lower()


def test_d4_annulla_non_invia_niente():
    js = _regioni(APP_JS.read_text(encoding="utf-8"), "LMC9_START", "LMC9_END")
    assert "Cancel" in js or "cancel" in js
    blocco = js[js.index("consultationCancel"):] if "consultationCancel" in js else ""
    assert "apiRequest" not in blocco.split("consultationSend")[0], \
        "il ramo Annulla non deve chiamare l'API"


def test_d5_il_doppio_tap_e_bloccato():
    js = _regioni(APP_JS.read_text(encoding="utf-8"), "LMC9_START", "LMC9_END")
    # Due guardie, e servono entrambe: il flag impedisce la seconda chiamata
    # anche se il pulsante venisse premuto prima del ridisegno, e il
    # `disabled` lo rende visibile a chi guarda.
    assert "state.consultationInFlight" in js
    assert "if (stimaId === null || stimaId === undefined || state.consultationInFlight)" in js
    assert re.search(r"disabled = name === 'sending'", js), "il pulsante si disabilita"


def test_d6_le_tre_copy_esistono_e_sono_distinte():
    js = APP_JS.read_text(encoding="utf-8")
    assert "Richiesta inviata. Ti ricontatteremo." in js
    assert "Non siamo riusciti a inviare la richiesta. Riprova." in js
    assert "Invio…" in js or "Invio..." in js


def test_d7_il_fallimento_non_diventa_un_successo():
    js = _regioni(APP_JS.read_text(encoding="utf-8"), "LMC9_START", "LMC9_END")
    ramo_errore = js[js.index("catch"):] if "catch" in js else ""
    assert "Richiesta inviata" not in ramo_errore, \
        "il ramo di errore non deve mostrare la copy di successo"


def test_d8_nessun_form_nuovo():
    """Il proprietario e' gia' autenticato e il contatto esiste: chiedere
    di nuovo nome, email o telefono sarebbe attrito e dati duplicati."""
    regione = _regioni(INDEX.read_text(encoding="utf-8"),
                       "<!--LMC9:START-->", "<!--LMC9:END-->")
    for campo in ("<input", "<textarea", "<select", "type=\"email\"",
                  "telefono", "budget"):
        assert campo not in regione.lower(), campo


def test_d9_il_portale_non_mostra_il_radar():
    js = APP_JS.read_text(encoding="utf-8").lower()
    for vietato in ("consultation_requested\": true", "interesse", "lead caldo",
                    "owner_consultation_requested"):
        assert vietato not in js, vietato


# ---------------------------------------------------------------------------
# E - CRM e timeline
# ---------------------------------------------------------------------------

def test_e1_la_timeline_ha_l_etichetta():
    js = TIMELINE_JS.read_text(encoding="utf-8")
    assert CONSULTATION in js
    assert "Ha richiesto una verifica gratuita" in js


def test_e2_niente_gergo_nell_etichetta():
    js = TIMELINE_JS.read_text(encoding="utf-8")
    riga = [r for r in js.splitlines() if CONSULTATION in r and ":" in r][0].lower()
    for vietato in ("buy", "match", "score", "consultation_requested'"):
        assert vietato not in riga.replace(CONSULTATION, ""), vietato


def test_e3_il_payload_tecnico_resta_nascosto():
    js = TIMELINE_JS.read_text(encoding="utf-8")
    nascosti = js[js.index("PAYLOAD_KEYS_HIDDEN_BY_EVENT_TYPE"):]
    nascosti = nascosti[:nascosti.index("}")]
    assert CONSULTATION in nascosti


def test_e4_il_crm_non_ricalcola_niente():
    from owner import crm_radar

    albero = ast.parse(inspect.getsource(crm_radar))
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            nodo.value = ""
    codice = ast.unparse(albero)
    assert "consultation" not in codice.lower(), \
        "il CRM riceve la proiezione, non la interpreta"


# ---------------------------------------------------------------------------
# F - cosa LMC-9 NON fa
# ---------------------------------------------------------------------------

def test_f1_nessun_task_ne_attivita_automatici():
    sorgente = (inspect.getsource(tracking) +
                (ROOT / "owner" / "router_portal.py").read_text(encoding="utf-8"))
    for vietato in ("create_task", "create_activity", "TaskCreate",
                    "ActivityCreate", "tasks", "activities"):
        assert vietato not in sorgente, vietato


def test_f2_nessuna_comunicazione():
    sorgente = inspect.getsource(tracking)
    for vietato in ("enqueue", "communication", "whatsapp", "sms", "email"):
        assert vietato not in sorgente.lower(), vietato


def test_f3_i_domini_vietati_non_sono_stati_toccati():
    """LMC-10 (collisione segnalata): `property_watch/` e `migrations/`
    escono dall'elenco, come gia' per LMC-7 e LMC-8.

    LMC-9 non li ha toccati. Il controllo pero' guarda il WORKING TREE
    condiviso, e LMC-10 - con lo STORAGE GATE approvato - vi ha creato la
    068 e vi ha fatto leggere al motore il profilo effettivo. La garanzia
    cambia soggetto e resta nel test sotto; `valuation.py`, `main.py` e
    `oggi.js` restano sorvegliati qui.
    """
    import subprocess
    diff = subprocess.run(
        ["git", "--no-optional-locks", "diff", "--name-only", "--",
         "seller_intent/", "next_best_action/", "followup/", "communication/",
         "valuation.py",
         "static/os_shell/assets/views/oggi.js"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert diff == "", diff
    # LMC-15 (collisione autorizzata, dichiarata): `main.py` esce dall'elenco
    # dei percorsi sorvegliati IN BLOCCO perche' LMC-15 vi monta il router del
    # ponte di acquisizione. Non smette di essere guardato - sarebbe la
    # risposta comoda e sbagliata: il suo diff viene controllato riga per riga
    # qui sotto, e qualunque modifica che non sia quel montaggio fa ancora
    # fallire questo test.
    from tests.lmc15_main_diff import righe_impreviste_in_main
    assert righe_impreviste_in_main(ROOT) == [], righe_impreviste_in_main(ROOT)


def test_f3b_il_percorso_di_lmc9_non_nomina_property_watch_ne_lo_schema():
    import inspect

    from owner import tracking as owner_tracking
    sorgente = inspect.getsource(owner_tracking.track_consultation_request)
    for vietato in ("property_watch", "migration", "owner_home_overrides"):
        assert vietato not in sorgente, vietato


def test_f4_nessuna_migration():
    """SENTINELLA AGGIORNATA DA LMC-10: la sola 068 e' attesa, e nominata."""
    import subprocess
    nuovi = {riga[3:].strip() for riga in subprocess.run(
        ["git", "--no-optional-locks", "status", "--porcelain", "--", "migrations/"],
        cwd=ROOT, capture_output=True, text=True).stdout.splitlines()}
    # SENTINELLA AGGIORNATA DA LMC-12: la 069 e' lo stream di notifiche
    # PRE-INCARICO `owner_home_notifications`, approvata dal DESIGN GATE di
    # LMC-12 (dominio OWNER, radice `stime` + `owner_stima_access`). Si nomina
    # invece di smettere di guardare: qualunque ALTRA migration comparisse
    # farebbe ancora fallire questo test.
    # SENTINELLA AGGIORNATA DA LMC-15: la 070 e' il ponte di acquisizione
    # (`stima_acquisitions`, `stima_inspections`), approvato dallo SCHEMA
    # GATE di LMC-15A.2. Si nomina invece di smettere di guardare:
    # qualunque ALTRA migration comparisse farebbe ancora fallire il test.
    atteso = {"migrations/068_lmc10_owner_home_overrides.sql",
              "migrations/068_lmc10_owner_home_overrides_down.sql",
              "migrations/069_lmc12_owner_home_notifications.sql",
              "migrations/069_lmc12_owner_home_notifications_down.sql",
              "migrations/070_lmc15_acquisition_bridge.sql",
              "migrations/070_lmc15_acquisition_bridge_down.sql"}
    assert nuovi - atteso == set(), sorted(nuovi - atteso)


# ---------------------------------------------------------------------------
# G - il flusso reale nel portale, eseguito.
#
# Si riusa l'armatura DOM certificata di P6, come in LMC-6 e LMC-7: app.js
# gira per davvero con una fetch finta, e si guarda cosa succede ai click.
# ---------------------------------------------------------------------------

from test_lmc6_owner_portal_home import BASE, CASA, DETTAGLIO_RICCO, scenario  # noqa: E402


def _aggiungi_id_armatura():
    """I nuovi id devono stare nel registro dell'armatura P6."""
    testo = (ROOT / "tests" / "test_owner_06_p6.py").read_text(encoding="utf-8")
    for elemento in ("home-consultation-cta", "home-consultation-confirm",
                     "home-consultation-cancel", "home-consultation-send",
                     "home-consultation-status"):
        assert f"'{elemento}'" in testo, elemento


def test_g0_l_armatura_conosce_i_nuovi_elementi():
    _aggiungi_id_armatura()


def test_g1_la_cta_c_e_e_la_conferma_e_chiusa():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
assert(ids['home-consultation-cta'].hidden === false, 'la CTA si vede');
assert(ids['home-consultation-confirm'].hidden === true, 'la conferma e chiusa');
assert(ids['home-consultation-status'].hidden === true, 'nessun messaggio');
assert(!calls.some((c) => c.url.includes('consultation-request')), 'niente parte da solo');
""")


def test_g2_il_primo_tocco_chiede_conferma_e_non_invia():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
await ids['home-consultation-cta'].trigger('click');
assert(ids['home-consultation-confirm'].hidden === false, 'la conferma si apre');
assert(ids['home-consultation-cta'].hidden === true, 'la CTA si toglie di mezzo');
assert(!calls.some((c) => c.url.includes('consultation-request')),
       'nessuna richiesta prima della conferma');
""")


def test_g3_annulla_non_invia_niente():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
await ids['home-consultation-cta'].trigger('click');
await ids['home-consultation-cancel'].trigger('click');
assert(ids['home-consultation-confirm'].hidden === true, 'la conferma si chiude');
assert(ids['home-consultation-cta'].hidden === false, 'la CTA torna disponibile');
assert(!calls.some((c) => c.url.includes('consultation-request')), 'zero richieste');
""")


def test_g4_conferma_invia_e_dichiara_il_successo():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
await ids['home-consultation-cta'].trigger('click');
await ids['home-consultation-send'].trigger('click');
const invio = calls.find((c) => c.url.includes('consultation-request'));
assert(invio, 'la richiesta parte');
assert(invio.method === 'POST', 'metodo: ' + invio.method);
assert(invio.url.endsWith('/homes/500/consultation-request'), 'rotta: ' + invio.url);
assert(ids['home-consultation-status'].textContent === 'Richiesta inviata. Ti ricontatteremo.',
       'copy: ' + ids['home-consultation-status'].textContent);
assert(ids['home-consultation-cta'].hidden === true, 'la CTA non torna attiva');
assert(ids['home-consultation-confirm'].hidden === true, 'la conferma si chiude');
""", extra={f"{BASE}/homes/500/consultation-request": [
    {"status": 200, "body": {"status": "recorded"}}]})


def test_g5_un_errore_non_diventa_un_successo():
    """La cosa piu' importante di questa fase: se il server non ha
    registrato, il proprietario non deve leggere "Richiesta inviata"."""
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
await ids['home-consultation-cta'].trigger('click');
await ids['home-consultation-send'].trigger('click');
const messaggio = ids['home-consultation-status'].textContent;
assert(messaggio === 'Non siamo riusciti a inviare la richiesta. Riprova.',
       'copy: ' + messaggio);
assert(!messaggio.includes('inviata'), 'mai la copy di successo su un errore');
assert(ids['home-consultation-cta'].hidden === false, 'si puo riprovare');
for (const tecnico of ['503', 'error', 'sql', 'exception']) {
  assert(!messaggio.toLowerCase().includes(tecnico), 'dettaglio tecnico: ' + tecnico);
}
""", extra={f"{BASE}/homes/500/consultation-request": [{"status": 503, "body": None}]})


def test_g6_dopo_l_errore_il_secondo_tentativo_funziona():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
await ids['home-consultation-cta'].trigger('click');
await ids['home-consultation-send'].trigger('click');
await ids['home-consultation-cta'].trigger('click');
await ids['home-consultation-send'].trigger('click');
const invii = calls.filter((c) => c.url.includes('consultation-request'));
assert(invii.length === 2, 'tentativi: ' + invii.length);
assert(ids['home-consultation-status'].textContent === 'Richiesta inviata. Ti ricontatteremo.',
       'il secondo tentativo riesce');
""", extra={f"{BASE}/homes/500/consultation-request": [
    {"status": 503, "body": None}, {"status": 200, "body": {"status": "recorded"}}]})


def test_g7_il_doppio_tocco_non_invia_due_volte():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
await ids['home-consultation-cta'].trigger('click');
await Promise.all([
  ids['home-consultation-send'].trigger('click'),
  ids['home-consultation-send'].trigger('click'),
  ids['home-consultation-send'].trigger('click'),
]);
const invii = calls.filter((c) => c.url.includes('consultation-request'));
assert(invii.length === 1, 'invii: ' + invii.length);
""", extra={f"{BASE}/homes/500/consultation-request": [
    {"status": 200, "body": {"status": "recorded"}, "delay_ms": 20}]})


def test_g8_il_404_non_mostra_un_falso_successo():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
await ids['home-consultation-cta'].trigger('click');
await ids['home-consultation-send'].trigger('click');
assert(!ids['home-consultation-status'].textContent.includes('inviata'), 'nessun falso ok');
assert(ids['login-view'].hidden === true, 'un 404 di risorsa non butta fuori');
""", extra={f"{BASE}/homes/500/consultation-request": [{"status": 404, "body": None}]})
