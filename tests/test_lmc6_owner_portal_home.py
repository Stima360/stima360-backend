"""LMC-6 - "La Mia Casa" nel portale proprietario, lato frontend.

COME SI PROVA UN FRONTEND SENZA UN BROWSER

Il repository non ha jsdom, non ha un bundler e non ha un package.json, e
questa fase non e' il posto per introdurli. La strada gia' battuta qui
(`test_os_shell_p25_*`) e' l'asserzione testuale sul sorgente piu' `node
--check`: va bene per dire che una funzione esiste, non per dire che decide
bene. Per LMC-6 quasi tutto cio' che conta e' una DECISIONE - mostrare o non
mostrare un valore, una percentuale, una sezione - e un grep che trova la
stringa giusta non prova niente su quando quella stringa compare.

Percio' le decisioni stanno in un modulo puro, `assets/home-view-model.js`:
riceve la risposta dell'API e restituisce un oggetto che dice cosa si vede,
senza toccare il DOM e senza fare fetch. Quel modulo si esegue in Node vero,
da qui, con `node -e`: i test sotto lo interrogano davvero invece di leggerlo.
Il DOM resta cosi' sottile da poter essere verificato per contratto: scrive
`textContent` di cio' che il view model ha gia' deciso.

Restano testuali le proprieta' che sono davvero testuali - niente
`innerHTML` su dati dell'API, nessuna sezione comparabili, nessuna CTA
LMC-9, il token ripulito dalla URL - e ognuna dice nel proprio nome che cosa
sta controllando.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PORTAL = ROOT / "static" / "owner_portal"
INDEX = PORTAL / "index.html"
APP_JS = PORTAL / "assets" / "app.js"
APP_CSS = PORTAL / "assets" / "app.css"
VIEW_MODEL = PORTAL / "assets" / "home-view-model.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _senza_commenti(testo: str) -> str:
    """Le righe di commento non sono codice: un divieto va cercato in cio'
    che viene eseguito, non nella prosa che lo spiega."""
    testo = re.sub(r"/\*.*?\*/", "", testo, flags=re.S)
    return "\n".join(riga.split("//", 1)[0] for riga in testo.splitlines())


# ---------------------------------------------------------------------------
# Il ponte verso Node: il view model viene ESEGUITO, non letto.
# ---------------------------------------------------------------------------

def vm(script: str):
    codice = f"const VM = require({str(VIEW_MODEL)!r});\n{script}"
    esito = subprocess.run(["node", "-e", codice], capture_output=True, text=True)
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout)


def detail_vm(payload: dict):
    return vm(f"process.stdout.write(JSON.stringify("
              f"VM.homeDetail({json.dumps(payload)})));")


def card_vm(payload: dict):
    return vm(f"process.stdout.write(JSON.stringify("
              f"VM.homeCard({json.dumps(payload)})));")


# ---------------------------------------------------------------------------
# Le risposte dell'API, nella forma che LMC-2/3/4 producono davvero.
# ---------------------------------------------------------------------------

def detail(**over):
    base = {
        "stima_id": 501,
        "property": {
            "comune": "Alba Adriatica", "microzona": "Villa Fiore",
            "via": "Via Trieste", "civico": "12", "tipologia": "Appartamento",
            "mq": 95, "piano": "3", "locali": 4, "bagni": 2,
            "pertinenze": "garage", "ascensore": "True", "anno": 1998,
            "stato": "buono", "vistamareyn": "si", "distanzamare": "0-100",
            "altrodescrizione": "ristrutturato",
        },
        "created_at": "2026-05-02T10:00:00+00:00",
        "valuation": {
            "initial_value": 185000, "initial_value_source": "property_watch_baseline",
            "current_value": None, "current_value_status": "history_not_available",
            "current_value_computed_at": None,
            "change_30d": None, "change_90d": None, "change_365d": None,
        },
        "valuation_history": [],
        "buyer_demand": {
            "status": "unavailable", "label": "Domanda non disponibile",
            "message": "Non è ancora disponibile una rilevazione della domanda per questo immobile.",
            "compatible_requests": None, "recent_compatible_requests": None,
            "recency_days": None, "updated_at": None,
            "disclaimer": "Indicatore basato sulle richieste di acquisto presenti nel database STIMA360.",
        },
        "watch": {"has_watch": True, "status": "active"},
        "history": {"history_available": False, "history_status": "building",
                    "observation_count": 1, "first_observed_at": None,
                    "last_observed_at": None},
        "profile": {"considered_fields": ["comune", "mq", "piano", "anno"],
                    "known_fields": ["comune", "mq", "piano"],
                    "missing_fields": ["anno"], "completion_percent": 75},
        "capabilities": {"valuation_history": False, "buyer_demand": False,
                         "comparables": False, "profile_update": False},
        "data_status": "ready",
    }
    for chiave, valore in over.items():
        if isinstance(valore, dict) and isinstance(base.get(chiave), dict):
            base[chiave] = {**base[chiave], **valore}
        else:
            base[chiave] = valore
    return base


def snapshot(valore, quando, impronta="valuation-a"):
    return {"computed_at": quando, "price_exact": valore,
            "eur_mq_finale": 1900, "algorithm_fingerprint": impronta}


def cambio(da, a, dal, al, percentuale, metodo=False):
    return {"from_value": da, "to_value": a, "from_computed_at": dal,
            "to_computed_at": al, "change_percent": percentuale,
            "methodology_changed": metodo}


# ---------------------------------------------------------------------------
# 1-3. Accesso: il form email e il token legacy
# ---------------------------------------------------------------------------

def test_01_esiste_il_form_di_accesso_via_email():
    html = _read(INDEX)
    assert 'id="email-login-form"' in html
    assert 'id="email-input"' in html
    assert 'type="email"' in html
    js = _read(APP_JS)
    assert "/auth/request-link" in js
    assert "email-login-form" in js


def test_02_la_risposta_al_form_email_e_sempre_la_stessa():
    """Anti-enumerazione: il messaggio non dipende dall'esito.

    Si verifica che esista UNA sola costante di risposta e che venga
    mostrata sia sul ramo riuscito sia su quello fallito.
    """
    js = _senza_commenti(_read(APP_JS))
    blocco = js[js.index("requestLoginLink"):]
    blocco = blocco[:blocco.index("\n  async function loadDashboard")] if \
        "\n  async function loadDashboard" in blocco else blocco[:6000]
    assert blocco.count("EMAIL_LINK_NEUTRAL_MESSAGE") >= 2, \
        "lo stesso messaggio deve comparire sul successo e sull'errore"
    for vietato in ("non esiste", "non trovata", "rate limit", "troppe richieste",
                    "disabilitat", "sconosciut"):
        assert vietato not in blocco.lower(), vietato


def test_03_il_login_con_token_nella_url_resta_invariato():
    js = _read(APP_JS)
    for pezzo in ("readTokenFromUrl", "removeTokenFromUrl", "authenticateWithToken",
                  "/auth/token", "one-time-code"):
        assert pezzo in js or pezzo in _read(INDEX), pezzo
    assert 'id="login-form"' in _read(INDEX), "il form del codice monouso resta"


# ---------------------------------------------------------------------------
# 4-8. Le quattro combinazioni owner, e la lista
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("homes,properties,atteso", [
    (1, 0, {"homes": True, "properties": False, "empty": False}),
    (0, 1, {"homes": False, "properties": True, "empty": False}),
    (2, 3, {"homes": True, "properties": True, "empty": False}),
    (0, 0, {"homes": False, "properties": False, "empty": True}),
])
def test_04_a_07_le_quattro_combinazioni_owner(homes, properties, atteso):
    """Solo-home, solo-property, entrambe, nessuna.

    Il caso che LMC-6 deve correggere e' il primo: fino a oggi zero
    properties accendeva la schermata "nessun immobile" anche a chi ha una
    casa in monitoraggio.
    """
    esito = vm(
        "process.stdout.write(JSON.stringify(VM.dashboardSections({"
        f"homes: Array.from({{length: {homes}}}, (_, i) => ({{stima_id: i + 1}})),"
        f"properties: Array.from({{length: {properties}}}, (_, i) => ({{id: i + 1}}))"
        "})));")
    assert esito["showHomes"] is atteso["homes"]
    assert esito["showProperties"] is atteso["properties"]
    assert esito["showEmpty"] is atteso["empty"]


def test_08_la_lista_delle_case_mostra_solo_campi_dichiarati():
    carta = card_vm({
        "stima_id": 7, "comune": "Alba Adriatica", "microzona": "Villa Fiore",
        "via": "Via Trieste", "civico": "12", "tipologia": "Appartamento",
        "mq": 95, "initial_value": 185000, "has_watch": True,
        "last_update_at": "2026-09-01T10:00:00+00:00", "data_status": "ready",
    })
    assert carta["stimaId"] == 7
    assert "Via Trieste" in carta["title"] and "12" in carta["title"]
    assert "Appartamento" in carta["subtitle"] and "95" in carta["subtitle"]
    assert carta["initialValue"].startswith("185.000")
    assert carta["statusLabel"]


def test_08b_una_casa_senza_indirizzo_non_mostra_buchi():
    carta = card_vm({"stima_id": 9, "comune": "Tortoreto", "tipologia": "Villa"})
    assert carta["title"], "un titolo c'e' sempre"
    assert "undefined" not in json.dumps(carta) and "null" not in carta["title"]
    assert carta["initialValue"] is None


# ---------------------------------------------------------------------------
# 9-13. Il valore
# ---------------------------------------------------------------------------

def test_09_e_10_il_dettaglio_mostra_il_valore_iniziale():
    vista = detail_vm(detail())
    assert vista["header"]["title"] == "La Mia Casa"
    assert "Via Trieste" in vista["header"]["address"]
    assert "Appartamento" in vista["header"]["summary"]
    assert vista["value"]["initialValue"].startswith("185.000")
    assert vista["value"]["initialLabel"] == "Valore iniziale"


def test_11_il_valore_monitorato_si_vede_solo_se_c_e():
    vista = detail_vm(detail(
        valuation={"current_value": 192000, "current_value_status": "available",
                   "current_value_computed_at": "2026-09-10T08:00:00+00:00"}))
    assert vista["value"]["hasCurrent"] is True
    assert vista["value"]["currentValue"].startswith("192.000")
    assert vista["value"]["currentLabel"] == "Valore monitorato"
    assert vista["value"]["buildingHistory"] is False


def test_12_senza_valore_monitorato_si_dice_storico_in_costruzione():
    vista = detail_vm(detail())["value"]
    assert vista["hasCurrent"] is False
    assert vista["currentValue"] is None, "mai un valore al posto del nulla"
    assert vista["buildingHistory"] is True
    assert vista["buildingMessage"] == "Storico in costruzione"


def test_12b_mai_zero_euro_al_posto_di_un_valore_mancante():
    vista = detail_vm(detail(valuation={"initial_value": None, "current_value": None}))
    testo = json.dumps(vista)
    assert vista["value"]["initialValue"] is None
    assert "0 €" not in testo and "€ 0" not in testo and "0,00" not in testo


def test_13_la_data_del_calcolo_accompagna_il_valore_monitorato():
    vista = detail_vm(detail(
        valuation={"current_value": 192000, "current_value_status": "available",
                   "current_value_computed_at": "2026-09-10T08:00:00+00:00"}))["value"]
    assert vista["computedAtLabel"] == "Calcolato il"
    assert vista["computedAt"] == "10/09/2026"


def test_13b_senza_data_non_si_spaccia_il_valore_per_odierno():
    vista = detail_vm(detail(
        valuation={"current_value": 192000, "current_value_status": "available",
                   "current_value_computed_at": None}))["value"]
    assert vista["computedAt"] is None
    for parola in ("oggi", "aggiornato ora", "in tempo reale"):
        assert parola not in json.dumps(vista).lower(), parola


# ---------------------------------------------------------------------------
# 14-18. L'andamento
# ---------------------------------------------------------------------------

def test_14_capability_falsa_niente_grafico():
    storia = detail_vm(detail())["history"]
    assert storia["available"] is False
    assert storia["message"] == "Storico in costruzione"
    assert storia["points"] == []
    assert storia["changes"] == []


def test_15_capability_vera_grafico_con_i_soli_snapshot_reali():
    payload = detail(
        capabilities={"valuation_history": True},
        valuation={"current_value": 200000, "current_value_status": "available",
                   "current_value_computed_at": "2026-09-10T08:00:00+00:00",
                   "change_30d": cambio(190000, 200000, "2026-07-02T08:00:00+00:00",
                                        "2026-09-10T08:00:00+00:00", 5.26)},
        valuation_history=[snapshot(190000, "2026-07-02T08:00:00+00:00"),
                           snapshot(200000, "2026-09-10T08:00:00+00:00")])
    storia = detail_vm(payload)["history"]
    assert storia["available"] is True
    assert len(storia["points"]) == 2
    assert [p["value"] for p in storia["points"]] == [190000, 200000]
    assert storia["points"][0]["dateLabel"] == "02/07/2026"


def test_16_nessun_punto_sintetico_ne_baseline_travestita():
    """La baseline vale 185.000 e non deve entrare nel grafico; i punti sono
    esattamente quelli di `valuation_history`, nello stesso ordine."""
    payload = detail(
        capabilities={"valuation_history": True},
        valuation_history=[snapshot(190000, "2026-07-02T08:00:00+00:00"),
                           snapshot(200000, "2026-09-10T08:00:00+00:00")])
    storia = detail_vm(payload)["history"]
    valori = [p["value"] for p in storia["points"]]
    assert 185000 not in valori, "la baseline non e' uno snapshot"
    assert len(valori) == 2, "nessun punto aggiunto per riempire"


def test_17_un_periodo_nullo_non_viene_mostrato():
    payload = detail(
        capabilities={"valuation_history": True},
        valuation={"change_30d": cambio(190000, 200000, "2026-07-02T08:00:00+00:00",
                                        "2026-09-10T08:00:00+00:00", 5.26),
                   "change_90d": None, "change_365d": None},
        valuation_history=[snapshot(190000, "2026-07-02T08:00:00+00:00"),
                           snapshot(200000, "2026-09-10T08:00:00+00:00")])
    chiavi = [c["key"] for c in detail_vm(payload)["history"]["changes"]]
    assert chiavi == ["change_30d"], "90 e 365 sono null: non esistono in pagina"


def test_18_metodologia_cambiata_nessuna_percentuale():
    payload = detail(
        capabilities={"valuation_history": True},
        valuation={"change_30d": cambio(180000, 200000, "2026-07-02T08:00:00+00:00",
                                        "2026-09-10T08:00:00+00:00", None, metodo=True)},
        valuation_history=[snapshot(180000, "2026-07-02T08:00:00+00:00", "vecchia"),
                           snapshot(200000, "2026-09-10T08:00:00+00:00", "nuova")])
    variazione = detail_vm(payload)["history"]["changes"][0]
    assert variazione["methodologyChanged"] is True
    assert variazione["percentText"] is None, "due metodi diversi non si sottraggono"
    assert "non sono direttamente confrontabili" in variazione["note"]
    assert "%" not in json.dumps(variazione)


def test_18b_metodologia_invariata_la_percentuale_si_vede():
    payload = detail(
        capabilities={"valuation_history": True},
        valuation={"change_30d": cambio(190000, 200000, "2026-07-02T08:00:00+00:00",
                                        "2026-09-10T08:00:00+00:00", 5.26)},
        valuation_history=[snapshot(190000, "2026-07-02T08:00:00+00:00"),
                           snapshot(200000, "2026-09-10T08:00:00+00:00")])
    variazione = detail_vm(payload)["history"]["changes"][0]
    assert variazione["methodologyChanged"] is False
    assert variazione["percentText"].startswith("+5,26")
    assert variazione["note"] is None


# ---------------------------------------------------------------------------
# 19-21. La domanda acquirenti
# ---------------------------------------------------------------------------

def test_19_buyer_demand_disponibile():
    payload = detail(
        capabilities={"buyer_demand": True},
        buyer_demand={"status": "high", "label": "Domanda alta",
                      "message": "Nel database STIMA360 è presente una domanda elevata "
                                 "di acquirenti per immobili con caratteristiche simili.",
                      "compatible_requests": 9, "recent_compatible_requests": 5,
                      "recency_days": 30, "updated_at": "2026-09-18T09:00:00+00:00",
                      "disclaimer": "Indicatore basato sulle richieste di acquisto "
                                    "presenti nel database STIMA360."})
    domanda = detail_vm(payload)["demand"]
    assert domanda["available"] is True
    assert domanda["status"] == "high"
    assert domanda["label"] == "Domanda alta"
    assert domanda["compatibleText"] == "9"
    assert domanda["recentText"] == "5"
    assert domanda["updatedAt"] == "18/09/2026"
    assert domanda["disclaimer"]


def test_20_buyer_demand_non_disponibile():
    domanda = detail_vm(detail())["demand"]
    assert domanda["available"] is False
    assert domanda["message"] == "Domanda non ancora disponibile"
    assert domanda["compatibleText"] is None and domanda["recentText"] is None


def test_20b_il_frontend_non_reinterpreta_il_dato():
    """Etichetta e messaggio arrivano dall'API cosi' come sono: il portale
    non ha una seconda tabella di soglie."""
    payload = detail(capabilities={"buyer_demand": True},
                     buyer_demand={"status": "medium", "label": "ETICHETTA DAL BACKEND",
                                   "message": "MESSAGGIO DAL BACKEND",
                                   "compatible_requests": 3,
                                   "recent_compatible_requests": 1,
                                   "updated_at": "2026-09-18T09:00:00+00:00"})
    domanda = detail_vm(payload)["demand"]
    assert domanda["label"] == "ETICHETTA DAL BACKEND"
    assert domanda["message"] == "MESSAGGIO DAL BACKEND"
    js = _senza_commenti(_read(APP_JS)) + _senza_commenti(_read(VIEW_MODEL))
    for soglia in ("Domanda alta", "Domanda media", "Domanda bassa"):
        assert soglia not in js, f"{soglia}: le fasce le decide il backend"


@pytest.mark.parametrize("gergo", [
    "score", "punteggio", "buy_request", "match_id", "ranking", "budget",
    "pressure", "fingerprint", "evaluated", "algorithm", "agency_id",
    "owner_account_id", "watch_id", "idempotency",
])
def test_21_nessun_gergo_interno_nel_frontend(gergo):
    """Ne' nel codice che costruisce il DOM, ne' nel markup."""
    sorgenti = _senza_commenti(_read(APP_JS)) + _senza_commenti(_read(VIEW_MODEL))
    sorgenti += re.sub(r"<!--.*?-->", "", _read(INDEX), flags=re.S)
    assert gergo not in sorgenti.lower(), gergo


def test_21b_il_dom_non_riceve_mai_un_campo_non_dichiarato():
    """Il view model costruisce oggetti campo per campo: anche se l'API
    aggiungesse domani una metrica interna al blocco, non arriverebbe in
    pagina."""
    payload = detail(capabilities={"buyer_demand": True})
    payload["buyer_demand"] = {**payload["buyer_demand"], "status": "high",
                               "pressure_score": 87, "average_budget": 238000,
                               "factors": [{"code": "x", "points": 30}]}
    vista = detail_vm(payload)
    testo = json.dumps(vista)
    for vietato in ("87", "238000", "factors", "pressure_score", "average_budget"):
        assert vietato not in testo, vietato


# ---------------------------------------------------------------------------
# 22-25. Comparabili, profilo, CTA
# ---------------------------------------------------------------------------

def test_22_comparabili_nessuna_sezione():
    """LMC-5 ha chiuso la fonte dati: la sezione non esiste, ne' vuota ne'
    con un segnaposto."""
    vista = detail_vm(detail(capabilities={"comparables": False}))
    assert "comparables" not in vista
    html = re.sub(r"<!--.*?-->", "", _read(INDEX), flags=re.S).lower()
    js = _senza_commenti(_read(APP_JS)).lower() + _senza_commenti(_read(VIEW_MODEL)).lower()
    for parola in ("comparabil", "immobili simili", "case simili"):
        assert parola not in html, parola
        assert parola not in js, parola


def test_22b_nemmeno_se_il_backend_dicesse_true():
    """Difesa in profondita': la capability oggi e' false per decisione di
    LMC-5, e il frontend non ha comunque niente da mostrare."""
    vista = detail_vm(detail(capabilities={"comparables": True}))
    assert "comparables" not in vista


def test_23_il_profilo_casa_mostra_la_completezza():
    profilo = detail_vm(detail())["profile"]
    assert profilo["percentText"] == "75%"
    assert profilo["known"] == ["comune", "mq", "piano"]
    assert profilo["missing"] == ["anno"]
    assert "monitoraggio" in profilo["note"]


def test_23b_il_profilo_non_promette_precisione_matematica():
    profilo = detail_vm(detail())["profile"]
    testo = profilo["note"].lower()
    for promessa in ("più preciso il valore", "piu' preciso il valore",
                     "valutazione più accurata", "aumenta la precisione del calcolo"):
        assert promessa not in testo, promessa


def test_24_profile_update_falso_nessun_form():
    """SENTINELLA AGGIORNATA DA LMC-10, e la garanzia resta la stessa.

    Fino a LMC-9 la si dimostrava nel modo piu' forte possibile: il form non
    esisteva proprio nel markup. LMC-10 lo ha scritto, quindi quella prova
    non e' piu' disponibile - ma la cosa che contava non era l'assenza del
    markup, era che con `profile_update: false` il proprietario non veda
    nessuna via per modificare. Quella si prova ancora, e in due modi:
    il view-model dice `editable: false`, e il form nasce `hidden` nel
    markup invece di comparire e poi essere nascosto da JavaScript.

    Che il form resti davvero chiuso a runtime quando la capability e' falsa
    lo prova `test_lmc10_owner_home_update.py` con lo scenario Node, che qui
    non esisterebbe come prova perche' questo test non esegue app.js.
    """
    vista = detail_vm(detail(capabilities={"profile_update": False}))
    assert vista["profile"]["editable"] is False
    html = _read(INDEX)
    assert 'id="home-profile-form"' in html, "LMC-10 ha scritto il form"
    forma = html[html.index('id="home-profile-form"'):]
    assert "hidden" in forma[:forma.index(">")], "il form deve nascere nascosto"
    pulsante = html[html.index('id="home-profile-edit"'):]
    assert "hidden" in pulsante[:pulsante.index(">")], "anche la CTA nasce nascosta"


def _regioni_lmc6(testo, apertura, chiusura):
    """Tutti i tratti PRE-INCARICO, uniti, con i commenti tolti dopo.

    I marcatori vivono dentro i commenti, quindi si ritagliano sul sorgente
    grezzo e solo dopo si toglie la prosa: cercare i marcatori nel testo gia'
    ripulito non troverebbe niente.

    Il controllo va ristretto a questi tratti perche' "Essere ricontattato"
    esiste gia' nel portale POST-INCARICO come opzione del form richieste, ed
    e' una funzione vera con un backend vero (`contact_request`). Vietarla in
    tutto il file vieterebbe il legacy invece della CTA che LMC-6 non deve
    inventare.
    """
    regioni = re.findall(re.escape(apertura) + r"(.*?)" + re.escape(chiusura),
                         testo, flags=re.S)
    assert regioni, f"marcatori {apertura}/{chiusura} assenti"
    return _senza_commenti("\n".join(regioni)).lower()


def test_25_nessuna_cta_lmc9():
    """LMC-9 (collisione autorizzata): la CTA ORA ESISTE, e ha un backend.

    Questo test nasceva per impedire un finto pulsante finche' la
    conversione non fosse reale. Adesso lo e': "Richiedi verifica gratuita"
    ha una rotta, una conferma esplicita e un modello di errore che non
    finge. Il divieto resta per tutto il resto - nessuna seconda CTA, nessun
    pulsante disattivato - e la regione LMC-9 viene esclusa dal controllo
    invece di allentarlo, perche' li' dentro il pulsante e' legittimo.
    """
    # La regione LMC-9 si toglie dal testo GREZZO: dopo `_regioni_lmc6` i
    # marcatori, che sono commenti, non esistono piu'.
    grezzo = re.sub(r"<!--LMC9:START-->.*?<!--LMC9:END-->", "",
                    _read(INDEX), flags=re.S)
    html = _regioni_lmc6(grezzo, "<!--LMC6:START-->", "<!--LMC6:END-->")
    js_grezzo = re.sub(r"LMC9_START.*?LMC9_END", "", _read(APP_JS), flags=re.S)
    js = _regioni_lmc6(js_grezzo, "LMC6_START", "LMC6_END")
    assert "la mia casa" in html, "la regione ritagliata e' quella giusta"

    # E la CTA vera c'e' una volta sola, nella sua regione.
    intero = _read(INDEX)
    assert intero.count("Richiedi verifica gratuita") == 1
    for cta in ("richiedi verifica", "verifica gratuita", "essere ricontattato",
                "ricontattami", "richiedi contatto"):
        assert cta not in html, cta
        assert cta not in js, cta
    # Nessun pulsante disattivato nel markup: un bottone grigio e' comunque
    # una promessa. (Nel JS `disabled` esiste, ma e' la guardia anti
    # doppio-invio del form email, che e' una funzione vera.)
    assert "disabled" not in html


# ---------------------------------------------------------------------------
# 26-27. Sicurezza del DOM
# ---------------------------------------------------------------------------

def test_26_nessun_innerhtml_nel_portale():
    js = _senza_commenti(_read(APP_JS)) + _senza_commenti(_read(VIEW_MODEL))
    for pericoloso in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                       "document.write", "eval("):
        assert pericoloso not in js, pericoloso


def test_26b_il_view_model_non_tocca_il_dom_ne_la_rete():
    sorgente = _senza_commenti(_read(VIEW_MODEL))
    for vietato in ("document", "window.fetch", "fetch(", "XMLHttpRequest",
                    "localStorage", "sessionStorage"):
        assert vietato not in sorgente, vietato


def test_27_il_token_viene_tolto_dalla_url():
    js = _senza_commenti(_read(APP_JS))
    assert "searchParams.delete('token')" in js
    assert "history.replaceState" in js
    assert js.index("removeTokenFromUrl()") < js.index("authenticateWithToken(token)"), \
        "prima si pulisce la URL, poi si usa il codice"


# ---------------------------------------------------------------------------
# 28-29. Il portale legacy e la resa su schermo piccolo
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("elemento", [
    "property-list", "property-detail-content", "timeline-list",
    "publication-detail-content", "visit-feedback-list", "documents-list",
    "request-form", "requests-list", "notifications-list",
    "notification-preferences-form", "logout-button", "login-form",
])
def test_28_il_portale_legacy_e_intatto(elemento):
    assert f'id="{elemento}"' in _read(INDEX), elemento
    assert elemento in _read(APP_JS), elemento


def test_28b_le_funzioni_legacy_non_sono_state_riscritte():
    js = _read(APP_JS)
    for funzione in ("loadDashboard", "selectProperty", "loadTimeline",
                     "openPublication", "acknowledgeCurrentPublication",
                     "loadDocuments", "loadRequests", "loadNotifications",
                     "startP68DataLoads"):
        assert f"function {funzione}" in js, funzione


def test_29_niente_overflow_orizzontale_e_numeri_che_non_si_spezzano():
    css = _read(APP_CSS)
    assert "overflow-x: hidden" in css or "max-width: 100%" in css
    blocco = css[css.index("/* LMC-6"):] if "/* LMC-6" in css else ""
    assert blocco, "il foglio di stile deve avere una sezione LMC-6"
    assert "min-width: 0" in blocco, "le griglie devono poter restringersi"
    assert "overflow-wrap" in blocco or "word-break" in blocco
    assert "@media" in blocco, "almeno una regola responsive dichiarata"


def test_29b_il_grafico_e_responsive():
    js = _senza_commenti(_read(APP_JS))
    assert "viewBox" in js, "l'SVG scala con il contenitore"
    assert "createElementNS" in js, "l'SVG si costruisce per nodi, non per stringa"
    assert "preserveAspectRatio" in js


# ---------------------------------------------------------------------------
# 30. Il sorgente e' valido, e il view model e' l'unica fonte delle decisioni
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("percorso", ["assets/app.js", "assets/home-view-model.js"])
def test_30_node_check(percorso):
    esito = subprocess.run(["node", "--check", str(PORTAL / percorso)],
                           capture_output=True, text=True)
    assert esito.returncode == 0, esito.stderr


def test_30b_il_frontend_non_ricalcola_niente():
    """Nessuna aritmetica sui valori: le percentuali, le differenze e le
    fasce arrivano gia' decise dal backend."""
    sorgente = _senza_commenti(_read(VIEW_MODEL))
    for formula in ("change_percent =", "/ from_value", "* 100", "Math.round(("):
        assert formula not in sorgente, formula
    js = _senza_commenti(_read(APP_JS))
    for chiamata in ("compute_from_payload", "/valuation", "buyer_pressure"):
        assert chiamata not in js, chiamata


def test_30c_il_portale_usa_solo_le_tre_api_previste():
    js = _senza_commenti(_read(APP_JS))
    nuove = set(re.findall(r"apiRequest\(\s*'(/homes[^']*)'", js))
    nuove |= set(re.findall(r"apiRequest\(\s*`(/homes[^`]*)`", js))
    for percorso in nuove:
        assert percorso.startswith("/homes"), percorso
    assert "/auth/request-link" in js
    assert "'/dashboard'" in js


# ---------------------------------------------------------------------------
# Runtime: app.js eseguito davvero, sull'armatura DOM gia' certificata da P6.
#
# `_run_node_scenario` carica il portale vero in un contesto Node con un DOM
# minimo e una fetch finta, e lascia interrogare gli elementi dopo il
# bootstrap. Si riusa invece di copiarla: una seconda armatura avrebbe potuto
# divergere da quella che il legacy usa, e allora i due test non starebbero
# piu' provando la stessa pagina.
# ---------------------------------------------------------------------------

from test_owner_06_p6 import _run_node_scenario  # noqa: E402

BASE = "/api/owner/portal"

CASA = {
    "stima_id": 500, "comune": "Alba Adriatica", "microzona": "Villa Fiore",
    "via": "Via Trieste", "civico": "12", "tipologia": "Appartamento", "mq": 95,
    "created_at": "2026-05-02T10:00:00+00:00", "has_watch": True,
    "initial_value": 185000, "last_update_at": "2026-09-01T10:00:00+00:00",
    "data_status": "ready",
}

DETTAGLIO_RICCO = detail(
    capabilities={"valuation_history": True, "buyer_demand": True},
    valuation={"current_value": 212000, "current_value_status": "available",
               "current_value_computed_at": "2026-09-10T08:00:00+00:00",
               "change_30d": cambio(198000, 212000, "2026-07-02T08:00:00+00:00",
                                    "2026-09-10T08:00:00+00:00", 7.07),
               "change_90d": cambio(190000, 212000, "2026-03-15T08:00:00+00:00",
                                    "2026-09-10T08:00:00+00:00", None, metodo=True),
               "change_365d": None},
    valuation_history=[snapshot(190000, "2026-03-15T08:00:00+00:00", "v-vecchia"),
                       snapshot(198000, "2026-07-02T08:00:00+00:00"),
                       snapshot(212000, "2026-09-10T08:00:00+00:00")],
    buyer_demand={"status": "high", "label": "Domanda alta",
                  "message": "Nel database STIMA360 è presente una domanda elevata "
                             "di acquirenti per immobili con caratteristiche simili.",
                  "compatible_requests": 9, "recent_compatible_requests": 5,
                  "recency_days": 30, "updated_at": "2026-09-18T09:00:00+00:00",
                  "disclaimer": "Indicatore basato sulle richieste di acquisto "
                                "presenti nel database STIMA360."})


def scenario(homes, properties, dettaglio, asserzioni, extra=None):
    rotte = {
        f"{BASE}/session": [{"status": 200, "body": {"authenticated": True}}],
        f"{BASE}/dashboard": [{"status": 200, "body": {
            "properties": properties, "property_count": len(properties),
            "homes": homes, "home_count": len(homes)}}],
    }
    if dettaglio is not None:
        rotte[f"{BASE}/homes/500"] = [dettaglio]
    for chiave, valore in (extra or {}).items():
        rotte[chiave] = valore
    return _run_node_scenario(rotte, asserzioni)


def test_r1_solo_casa_la_schermata_vuota_non_compare():
    """Il bug che LMC-6 chiude: zero immobili non significa piu' "niente qui"
    per chi ha una casa in monitoraggio."""
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
assert(ids['homes-section'].hidden === false, 'la sezione case deve comparire');
assert(ids['shell-empty'].hidden === true, 'la schermata vuota NON deve comparire');
assert(ids['dashboard-section'].hidden === true, 'senza immobili il legacy resta nascosto');
assert(ids['home-count'].textContent === '1 casa', 'conteggio: ' + ids['home-count'].textContent);
assert(ids['home-list'].children.length === 1, 'una scheda');
assert(calls.some((c) => c.url.endsWith('/homes/500')), 'il dettaglio viene caricato');
""")


def test_r2_solo_immobili_il_portale_legacy_e_intatto():
    scenario([], [{"id": 1, "title": "Immobile", "address": "Via Roma 1",
                   "city": "Alba Adriatica", "access_role": "owner", "is_primary": True}],
             None, """
assert(ids['homes-section'].hidden === true, 'nessuna casa: sezione nascosta');
assert(ids['shell-empty'].hidden === true, 'con un immobile non e vuoto');
assert(ids['dashboard-content'].hidden === false, 'il legacy deve mostrarsi');
assert(!calls.some((c) => c.url.includes('/homes/')), 'nessuna chiamata alle case');
""", extra={f"{BASE}/properties/1": [{"status": 200, "body": {
                 "property": {"id": 1, "title": "Immobile", "address": "Via Roma 1",
                              "city": "Alba Adriatica"}}}],
             f"{BASE}/properties/1/timeline": [{"status": 200, "body": {"items": []}}],
             f"{BASE}/properties/1/visit-feedback?limit=20&offset=0": [
                 {"status": 200, "body": {"items": [], "has_more": False}}],
             f"{BASE}/properties/1/documents": [{"status": 200, "body": {"items": []}}],
             f"{BASE}/properties/1/requests": [{"status": 200, "body": {"items": []}}]})


def test_r3_entrambe_le_superfici_convivono():
    scenario([CASA], [{"id": 1, "title": "Immobile", "address": "Via Roma 1",
                       "city": "Alba Adriatica", "access_role": "owner", "is_primary": True}],
             {"status": 200, "body": DETTAGLIO_RICCO}, """
assert(ids['homes-section'].hidden === false, 'le case ci sono');
assert(ids['dashboard-section'].hidden === false, 'gli immobili anche');
assert(ids['shell-empty'].hidden === true, 'niente schermata vuota');
""", extra={f"{BASE}/properties/1": [{"status": 200, "body": {
                 "property": {"id": 1, "title": "Immobile", "address": "Via Roma 1",
                              "city": "Alba Adriatica"}}}],
             f"{BASE}/properties/1/timeline": [{"status": 200, "body": {"items": []}}],
             f"{BASE}/properties/1/visit-feedback?limit=20&offset=0": [
                 {"status": 200, "body": {"items": [], "has_more": False}}],
             f"{BASE}/properties/1/documents": [{"status": 200, "body": {"items": []}}],
             f"{BASE}/properties/1/requests": [{"status": 200, "body": {"items": []}}]})


def test_r4_nessuna_delle_due_la_schermata_vuota_resta():
    scenario([], [], None, """
assert(ids['homes-section'].hidden === true, 'nessuna casa');
assert(ids['shell-empty'].hidden === false, 'la schermata vuota resta quella di sempre');
""")


def test_r5_il_dettaglio_finisce_davvero_nel_dom():
    # Nell'armatura gli elementi sono piatti (ogni id e' un nodo a se', senza
    # gerarchia), quindi si guarda nodo per nodo invece del testo aggregato.
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
assert(ids['home-detail-content'].hidden === false, 'il dettaglio e visibile');
assert(ids['home-detail-address'].textContent === 'Via Trieste 12, Villa Fiore, Alba Adriatica',
       'indirizzo: ' + ids['home-detail-address'].textContent);
assert(ids['home-detail-summary'].textContent.includes('Appartamento'), 'tipologia');
const valori = allText(ids['home-value-list']);
assert(valori.includes('185.000'), 'valore iniziale: ' + valori);
assert(valori.includes('212.000'), 'valore monitorato: ' + valori);
assert(valori.includes('10/09/2026'), 'data del calcolo: ' + valori);
const cambi = allText(ids['home-history-changes']);
assert(cambi.includes('+7,07%'), 'variazione a 30 giorni: ' + cambi);
assert(ids['home-demand-content'].hidden === true, 'domanda chiusa prima del gesto');
await ids['home-demand-toggle'].trigger('click');
assert(ids['home-demand-content'].hidden === false, 'domanda aperta dopo il gesto');
assert(ids['home-demand-label'].textContent === 'Domanda alta', 'fascia di domanda');
assert(allText(ids['home-demand-counts']).includes('9'), 'richieste compatibili');
assert(ids['home-profile-percent'].textContent === 'Completezza 75%', 'completezza');
// LMC-7 (collisione autorizzata): le due sezioni ad alta intenzione ora si
// aprono con un gesto, perche' il caricamento della pagina non deve poter
// dire "ha guardato l'andamento". Il CONTENUTO e' quello di LMC-6, e questo
// test lo verifica dopo il click invece che al caricamento.
assert(ids['home-history-chart'].hidden === true, 'chiuso prima del gesto');
assert(ids['home-history-toggle'].hidden === false, 'il pulsante c e');
await ids['home-history-toggle'].trigger('click');
assert(ids['home-history-chart'].hidden === false, 'il grafico c e dopo il gesto');
const svg = ids['home-history-chart'].children[0];
assert(svg && svg.tagName === 'SVG', 'un nodo SVG vero');
assert(svg.getAttribute('viewBox'), 'il grafico scala col contenitore');
assert(svg.children.filter((n) => n.tagName === 'CIRCLE').length === 3,
       'tre punti, quanti gli snapshot reali');
""")


def test_r6_metodologia_cambiata_nessuna_percentuale_nel_dom():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
await ids['home-history-toggle'].trigger('click');
const righe = ids['home-history-changes'].children.map(allText);
assert(righe.length === 2, 'solo 30 e 90 giorni: 365 e null, ' + righe.length);
const novanta = righe.find((r) => r.includes('90 giorni'));
assert(novanta.includes('non sono direttamente confrontabili'), 'manca la spiegazione');
assert(!novanta.includes('%'), 'nessuna percentuale quando il metodo cambia');
assert(!righe.some((r) => r.includes('12 mesi')), '365 e null: non si mostra');
""")


def test_r7_senza_storico_ne_domanda_il_dom_non_inventa():
    scenario([CASA], [], {"status": 200, "body": detail()}, """
const testo = allText(ids['home-detail-content']);
assert(ids['home-history-message'].textContent === 'Storico in costruzione', 'storico');
assert(ids['home-history-chart'].hidden === true, 'nessun grafico');
assert(ids['home-history-toggle'].hidden === true, 'niente pulsante senza storico');
assert(ids['home-demand-toggle'].hidden === true, 'niente pulsante senza domanda');
assert(ids['home-history-changes'].children.length === 0, 'nessuna variazione');
assert(ids['home-demand-unavailable'].textContent === 'Domanda non ancora disponibile', 'domanda');
assert(ids['home-demand-content'].hidden === true, 'il blocco domanda resta chiuso');
assert(!testo.includes('0 €'), 'mai zero euro al posto di un valore mancante');
assert(!testo.toLowerCase().includes('comparabil'), 'nessuna sezione comparabili');
""")


def test_r8_il_404_di_una_casa_e_neutro():
    scenario([CASA], [], {"status": 404, "body": {"detail": "Risorsa non trovata"}}, """
assert(ids['home-detail-error'].hidden === false, 'stato di errore');
assert(ids['home-detail-content'].hidden === true, 'nessun contenuto');
const messaggio = ids['home-detail-error-message'].textContent;
assert(messaggio.includes('non disponibile'), 'messaggio: ' + messaggio);
assert(!messaggio.toLowerCase().includes('404'), 'niente codice di stato');
assert(ids['login-view'].hidden === true, 'un 404 di risorsa non butta fuori');
""")


def test_r9_il_form_email_spedisce_e_risponde_sempre_uguale():
    _run_node_scenario(
        {f"{BASE}/session": [{"status": 404, "body": None}],
         f"{BASE}/auth/request-link": [{"status": 204, "body": None}]},
        """
ids['email-input'].value = 'mario@example.it';
await ids['email-login-form'].trigger('submit');
const invio = calls.find((c) => c.url.endsWith('/auth/request-link'));
assert(invio, 'la richiesta parte');
assert(invio.method === 'POST', 'metodo: ' + invio.method);
assert(invio.body === JSON.stringify({ email: 'mario@example.it' }), 'corpo: ' + invio.body);
const messaggio = ids['email-login-message'].textContent;
assert(messaggio.includes('Se l'), 'messaggio neutro assente: ' + messaggio);
assert(!messaggio.toLowerCase().includes('inviata'), 'non conferma un invio avvenuto');
""")


def test_r10_il_form_email_non_distingue_l_errore():
    """Il messaggio e' lo stesso anche quando la chiamata fallisce: l'esito
    reale non deve poter essere dedotto dallo schermo."""
    esito = _run_node_scenario(
        {f"{BASE}/session": [{"status": 404, "body": None}],
         f"{BASE}/auth/request-link": [{"status": 500, "body": None}]},
        """
ids['email-input'].value = 'ignoto@example.it';
await ids['email-login-form'].trigger('submit');
console.log(JSON.stringify({ messaggio: ids['email-login-message'].textContent }));
""")
    riga = [r for r in esito.splitlines() if r.startswith("{")][-1]
    messaggio = json.loads(riga)["messaggio"]
    assert "Se l" in messaggio
    for parola in ("errore", "non riuscit", "riprova", "500", "limite"):
        assert parola not in messaggio.lower(), parola


def test_r11_il_token_nella_url_viene_pulito_prima_dello_scambio():
    """L'armatura parte da una URL senza token, quindi il percorso con token
    resta coperto dalla sentinella statica di ordine (test 27) e da
    `test_p6_2_url_token_is_removed_before_exchange_attempt`, gia' verde.
    Qui si verifica solo che il bootstrap non lasci un token in giro."""
    _run_node_scenario(
        {f"{BASE}/session": [{"status": 200, "body": {"authenticated": True}}],
         f"{BASE}/dashboard": [{"status": 200, "body": {
             "properties": [], "property_count": 0, "homes": [], "home_count": 0}}]},
        """
assert(!window.location.href.includes('token='), 'il token resta nella URL');
""",
    )
