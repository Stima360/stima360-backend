"""LMC-7 - il radar dell'interesse: gli eventi e la proiezione, in memoria.

DUE COSE DIVERSE, E IL FILE E' DIVISO COSI'.

`owner/tracking.py` traduce un'AZIONE del proprietario in un evento Seller
Intelligence: decide il tipo, la chiave di idempotenza e il payload, e non
decide niente altro. `owner/interest_service.py` fa il contrario: legge gli
eventi e ne ricava una frase che un operatore possa leggere.

Nessuno dei due inventa un punteggio. Il livello e' una funzione a gradini di
fatti contabili - quanti giorni, quali sezioni - e le `reasons` sono la
spiegazione, non un ornamento: se il livello non si puo' giustificare con una
riga di testo, la soglia e' sbagliata.
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from owner import interest_service, tracking

ROOT = Path(__file__).resolve().parents[1]
ADESSO = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def _codice(modulo) -> str:
    """Il solo codice eseguibile: via docstring e commenti.

    Serve perche' i divieti vanno cercati in cio' che gira. La docstring di
    `interest_service` SPIEGA perche' non esiste un punteggio, e per farlo
    nomina "87/100": cercare quella stringa nel file intero proverebbe il
    contrario di quello che si vuole provare.
    """
    import ast

    albero = ast.parse(inspect.getsource(modulo))
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            nodo.value = ""
    return ast.unparse(albero)


def evento(tipo, quando, *, source="owner_portal", payload=None):
    return {"event_type": tipo, "event_source": source,
            "occurred_at": quando, "payload": payload or {}}


def giorni_fa(n, ora=10):
    return (ADESSO - timedelta(days=n)).replace(hour=ora, minute=0)


def interesse(eventi, adesso=ADESSO):
    return interest_service.build_interest(eventi, now=adesso)["interest"]


# ---------------------------------------------------------------------------
# A - il vocabolario degli eventi
# ---------------------------------------------------------------------------

def test_a1_tre_tipi_di_evento_e_nessun_altro():
    """LMC-9 (collisione autorizzata): i tipi diventano quattro.

    I tre di LMC-7 restano osservazioni; il quarto e' una richiesta, ha una
    rotta sua e un modello di errore opposto. L'insieme resta dichiarato
    per intero, perche' e' l'elenco di tutto cio' che il portale puo'
    scrivere nella timeline di vendita.
    """
    assert tracking.EVENT_TYPES == frozenset({
        "owner_home_viewed", "owner_value_history_viewed",
        "owner_buyer_demand_viewed", "owner_consultation_requested",
        # LMC-10 (collisione autorizzata): il quinto. Come il quarto non e'
        # un'osservazione - nasce da una scrittura riuscita in
        # `owner_home_overrides` - e come il quarto non e' dichiarabile dal
        # client.
        "owner_home_updated"})
    osservazioni = tracking.EVENT_TYPES - {tracking.CONSULTATION_REQUESTED,
                                           tracking.HOME_UPDATED}
    assert len(osservazioni) == 3, "le tre osservazioni di LMC-7 sono ancora tre"


def test_a2_i_comparabili_non_esistono():
    """LMC-5 ha chiuso la fonte dati: non c'e' niente da guardare, quindi non
    c'e' niente da registrare."""
    sorgente = inspect.getsource(tracking)
    assert "comparables" not in sorgente
    assert "owner_comparables_viewed" not in sorgente


def test_a3_le_fasi_future_non_sono_qui():
    """LMC-10 (collisione autorizzata): non ci sono piu' fasi future qui.

    Questo test nacque per impedire che LMC-7 si portasse avanti con eventi
    che nessuna fase aveva ancora approvato. Le due che aspettava sono
    arrivate entrambe - la richiesta con LMC-9, l'aggiornamento con LMC-10 -
    quindi la lista di cio' che "non deve esserci" e' vuota, e il test
    cambia soggetto senza perdere il proprio scopo: sorveglia che l'insieme
    dichiarato e quello realmente scrivibile coincidano, cioe' che nessun
    tipo di evento arrivi nella timeline senza essere passato da qui.
    """
    sorgente = inspect.getsource(tracking)
    assert tracking.CONSULTATION_REQUESTED in sorgente
    assert tracking.HOME_UPDATED in sorgente
    # I tipi realmente scritti sono quelli passati a `record_event_scoped`,
    # e arrivano tutti da `EVENT_TYPES`: nessuna stringa `owner_*` vive nel
    # modulo senza essere dichiarata nell'insieme.
    import re
    dichiarati = set(tracking.EVENT_TYPES) | {tracking.EVENT_SOURCE}
    trovati = set(re.findall(r'"(owner_[a-z_]+)"', sorgente))
    assert trovati - dichiarati == set(), trovati - dichiarati


def test_a4_il_client_sceglie_fra_due_azioni_soltanto():
    assert set(tracking.ACTIONS) == {"value_history_viewed", "buyer_demand_viewed"}
    assert tracking.ACTIONS["value_history_viewed"] == "owner_value_history_viewed"
    assert tracking.ACTIONS["buyer_demand_viewed"] == "owner_buyer_demand_viewed"


def test_a5_home_viewed_non_e_una_azione_del_client():
    """Nasce dalla GET del dettaglio, non da una dichiarazione: il client non
    puo' inventarla."""
    assert "home_viewed" not in tracking.ACTIONS
    assert tracking.HOME_VIEWED == "owner_home_viewed"


def test_a6_la_sorgente_e_fissa():
    assert tracking.EVENT_SOURCE == "owner_portal"
    assert len(tracking.EVENT_SOURCE) <= 30, "event_source e' VARCHAR(30)"
    for tipo in tracking.EVENT_TYPES:
        assert len(tipo) <= 50, f"{tipo}: event_type e' VARCHAR(50)"


# ---------------------------------------------------------------------------
# B - la chiave di idempotenza
# ---------------------------------------------------------------------------

def test_b1_la_chiave_e_deterministica_e_per_giorno_utc():
    chiave = tracking.idempotency_key("owner_home_viewed", owner_account_id=7,
                                      stima_id=501, when=ADESSO)
    assert chiave == "owner_portal:owner_home_viewed:owner:7:stima:501:day:2026-09-19"
    assert len(chiave) <= 255, "idempotency_key e' VARCHAR(255)"


def test_b2_stesso_giorno_stessa_chiave_ora_diversa():
    mattina = tracking.idempotency_key("owner_home_viewed", owner_account_id=7,
                                       stima_id=501, when=ADESSO.replace(hour=1))
    sera = tracking.idempotency_key("owner_home_viewed", owner_account_id=7,
                                    stima_id=501, when=ADESSO.replace(hour=23))
    assert mattina == sera


def test_b3_il_giorno_e_utc_non_locale():
    """Le 23:30 a Roma sono le 21:30 UTC dello stesso giorno; le 00:30 sono
    gia' il giorno dopo in entrambi. Il confine dichiarato e' UTC."""
    fuso = timezone(timedelta(hours=2))
    tardi = datetime(2026, 9, 19, 23, 30, tzinfo=fuso)      # 21:30 UTC, 19/09
    dopo = datetime(2026, 9, 20, 1, 30, tzinfo=fuso)        # 23:30 UTC, 19/09
    oltre = datetime(2026, 9, 20, 3, 30, tzinfo=fuso)       # 01:30 UTC, 20/09
    chiave = lambda q: tracking.idempotency_key(  # noqa: E731
        "owner_home_viewed", owner_account_id=7, stima_id=501, when=q)
    assert chiave(tardi).endswith("2026-09-19")
    assert chiave(dopo).endswith("2026-09-19"), "ancora il 19 in UTC"
    assert chiave(oltre).endswith("2026-09-20")


def test_b4_giorni_diversi_chiavi_diverse():
    oggi = tracking.idempotency_key("owner_home_viewed", owner_account_id=7,
                                    stima_id=501, when=ADESSO)
    domani = tracking.idempotency_key("owner_home_viewed", owner_account_id=7,
                                      stima_id=501, when=ADESSO + timedelta(days=1))
    assert oggi != domani


def test_b5_chiavi_diverse_per_owner_stima_e_tipo():
    base = dict(owner_account_id=7, stima_id=501, when=ADESSO)
    chiavi = {
        tracking.idempotency_key("owner_home_viewed", **base),
        tracking.idempotency_key("owner_value_history_viewed", **base),
        tracking.idempotency_key("owner_buyer_demand_viewed", **base),
        tracking.idempotency_key("owner_home_viewed", **{**base, "owner_account_id": 8}),
        tracking.idempotency_key("owner_home_viewed", **{**base, "stima_id": 502}),
    }
    assert len(chiavi) == 5


def test_b6_nessun_dato_personale_nella_chiave():
    chiave = tracking.idempotency_key("owner_home_viewed", owner_account_id=7,
                                      stima_id=501, when=ADESSO)
    assert re.fullmatch(r"[a-z0-9_:\-]+", chiave), chiave
    for pii in ("@", "mario", "rossi", "+39"):
        assert pii not in chiave


# ---------------------------------------------------------------------------
# C - il payload: minimo per costruzione
# ---------------------------------------------------------------------------

def test_c1_il_payload_contiene_solo_l_azione():
    for tipo in sorted(tracking.EVENT_TYPES):
        payload = tracking.event_payload(tipo)
        assert set(payload) == {"action"}, (tipo, payload)
        assert payload["action"] == tipo.replace("owner_", "", 1)


@pytest.mark.parametrize("vietato", [
    "email", "telefono", "phone", "nome", "cognome", "user_agent",
    "browser", "score", "budget", "buy_request", "match_id", "price", "eur",
    "importo", "pressure", "referrer", "session", "@",
])
def test_c2_niente_pii_ne_metriche_nel_payload(vietato):
    """`value_history_viewed` contiene "value" ed e' il nome dell'azione, non
    il valore della casa: si vietano i termini concreti, non la sillaba."""
    payloads = [tracking.event_payload(t) for t in sorted(tracking.EVENT_TYPES)]
    testo = json.dumps(payloads)
    assert vietato not in testo.lower(), vietato


def test_c2b_nel_payload_non_c_e_nessun_numero():
    """Un payload senza numeri non puo' portare un prezzo, un punteggio o un
    budget: e' il divieto nella sua forma piu' robusta."""
    for tipo in sorted(tracking.EVENT_TYPES):
        for chiave, valore in tracking.event_payload(tipo).items():
            assert isinstance(valore, str), (chiave, valore)
            assert not any(c.isdigit() for c in valore), (chiave, valore)


def test_c3_il_modulo_non_raccoglie_niente_dalla_richiesta():
    """Nessun accesso a header, IP o user agent: cio' che non si legge non si
    puo' registrare per sbaglio."""
    sorgente = inspect.getsource(tracking).lower()
    for vietato in ("request.", "headers", "user_agent", "remote_addr",
                    "x-forwarded", "client.host"):
        assert vietato not in sorgente, vietato


# ---------------------------------------------------------------------------
# D - la proiezione: giorni attivi
# ---------------------------------------------------------------------------

def test_d1_tre_eventi_nello_stesso_giorno_fanno_un_giorno_solo():
    eventi = [evento("owner_home_viewed", giorni_fa(2, ora)) for ora in (8, 13, 20)]
    vista = interesse(eventi)
    assert vista["active_days_30d"] == 1
    assert vista["active_days_7d"] == 1
    assert vista["home_viewed_days_30d"] == 1


def test_d2_giorni_distinti_si_contano_una_volta_ciascuno():
    eventi = [evento("owner_home_viewed", giorni_fa(n)) for n in (1, 3, 5, 12, 20)]
    vista = interesse(eventi)
    assert vista["active_days_30d"] == 5
    assert vista["active_days_7d"] == 3, "1, 3 e 5 giorni fa"


def test_d3_oltre_i_30_giorni_non_conta():
    eventi = [evento("owner_home_viewed", giorni_fa(n)) for n in (2, 31, 45)]
    vista = interesse(eventi)
    assert vista["active_days_30d"] == 1
    assert vista["home_viewed_days_30d"] == 1


def test_d4_l_ultima_attivita_e_quella_vera():
    eventi = [evento("owner_home_viewed", giorni_fa(9)),
              evento("owner_buyer_demand_viewed", giorni_fa(2, 15))]
    vista = interesse(eventi)
    assert vista["last_activity_at"] == giorni_fa(2, 15).isoformat()


def test_d5_solo_gli_eventi_del_portale_proprietario():
    """`stima_richiesta` e gli altri eventi del funnel non sono attivita' del
    proprietario dentro La Mia Casa."""
    eventi = [evento("stima_richiesta", giorni_fa(1), source="stima360_it"),
              evento("stima_completata", giorni_fa(1), source="stima360_it"),
              evento("nota_agente", giorni_fa(1), source="crm")]
    vista = interesse(eventi)
    assert vista["active_days_30d"] == 0
    assert vista["level"] == "none"


def test_d6_un_tipo_sconosciuto_non_entra():
    eventi = [evento("owner_qualcosa_di_nuovo", giorni_fa(1))]
    assert interesse(eventi)["active_days_30d"] == 0


# ---------------------------------------------------------------------------
# E - i livelli
# ---------------------------------------------------------------------------

def test_e1_none_senza_attivita():
    vista = interesse([])
    assert vista["level"] == "none"
    assert vista["active_days_30d"] == 0
    assert vista["last_activity_at"] is None
    assert vista["reasons"] == ["Nessuna attività nel portale negli ultimi 30 giorni"]


def test_e2_low_una_sola_apertura():
    vista = interesse([evento("owner_home_viewed", giorni_fa(4))])
    assert vista["level"] == "low"
    assert vista["active_days_30d"] == 1
    assert vista["value_history_viewed"] is False
    assert vista["buyer_demand_viewed"] is False


def test_e3_medium_per_ritorni_ripetuti():
    eventi = [evento("owner_home_viewed", giorni_fa(n)) for n in (3, 18)]
    vista = interesse(eventi)
    assert vista["level"] == "medium"
    assert vista["active_days_7d"] == 1, "solo uno dei due e' dentro i 7 giorni"


def test_e4_medium_per_una_sezione_ad_alta_intenzione():
    """Una sola visita, ma ha aperto l'andamento: e' piu' di un passaggio."""
    quando = giorni_fa(4)
    vista = interesse([evento("owner_home_viewed", quando),
                       evento("owner_value_history_viewed", quando)])
    assert vista["level"] == "medium"
    assert vista["active_days_30d"] == 1
    assert vista["value_history_viewed"] is True


def test_e5_high_ritorni_recenti_piu_sezione_ad_alta_intenzione():
    eventi = [evento("owner_home_viewed", giorni_fa(1)),
              evento("owner_home_viewed", giorni_fa(3)),
              evento("owner_buyer_demand_viewed", giorni_fa(3))]
    vista = interesse(eventi)
    assert vista["level"] == "high"
    assert vista["active_days_7d"] == 2
    assert vista["buyer_demand_viewed"] is True


def test_e6_ritorni_recenti_senza_sezioni_restano_medium():
    """Tornare e basta non e' alta intenzione: ha guardato la copertina."""
    eventi = [evento("owner_home_viewed", giorni_fa(n)) for n in (1, 2, 4)]
    vista = interesse(eventi)
    assert vista["active_days_7d"] == 3
    assert vista["level"] == "medium"


def test_e7_sezioni_viste_ma_vecchie_non_fanno_high():
    eventi = [evento("owner_home_viewed", giorni_fa(20)),
              evento("owner_value_history_viewed", giorni_fa(20)),
              evento("owner_home_viewed", giorni_fa(19))]
    vista = interesse(eventi)
    assert vista["active_days_7d"] == 0
    assert vista["level"] == "medium"


def test_e8_le_soglie_sono_dichiarate_e_non_sparse():
    assert interest_service.ACTIVE_DAYS_FOR_HIGH_7D == 2
    assert interest_service.ACTIVE_DAYS_FOR_MEDIUM_30D == 2
    assert interest_service.WINDOW_DAYS == 30
    assert interest_service.RECENT_WINDOW_DAYS == 7
    assert interest_service.LEVELS == ("none", "low", "medium", "high")


# ---------------------------------------------------------------------------
# F - le fasi future, dichiarate e sempre false
# ---------------------------------------------------------------------------

def test_f1_home_updated_e_consultation_sono_false_per_ora():
    for eventi in ([], [evento("owner_home_viewed", giorni_fa(1))]):
        vista = interesse(eventi)
        assert vista["home_updated"] is False
        assert vista["consultation_requested"] is False


def test_f2_la_forma_della_proiezione_e_quella_concordata():
    atteso = {"level", "active_days_7d", "active_days_30d", "last_activity_at",
              "home_viewed_days_30d", "value_history_viewed", "buyer_demand_viewed",
              "home_updated", "consultation_requested", "reasons"}
    assert set(interesse([])) == atteso


def test_f3_nessun_punteggio_numerico():
    eventi = [evento("owner_home_viewed", giorni_fa(n)) for n in (1, 2, 3)]
    eventi.append(evento("owner_value_history_viewed", giorni_fa(1)))
    vista = interesse(eventi)
    assert "score" not in json.dumps(vista).lower()
    for chiave in vista:
        assert "score" not in chiave and "punteggio" not in chiave
    codice = _codice(interest_service).lower()
    assert "score" not in codice, "nessun punteggio nemmeno interno"
    assert "/100" not in codice
    assert "punteggio" not in codice


# ---------------------------------------------------------------------------
# G - le motivazioni
# ---------------------------------------------------------------------------

def test_g1_le_reasons_spiegano_il_livello():
    eventi = [evento("owner_home_viewed", giorni_fa(1)),
              evento("owner_home_viewed", giorni_fa(3)),
              evento("owner_value_history_viewed", giorni_fa(3)),
              evento("owner_buyer_demand_viewed", giorni_fa(1))]
    vista = interesse(eventi)
    testo = " | ".join(vista["reasons"])
    assert "3 giorni" in testo or "2 giorni" in testo
    assert "andamento" in testo.lower()
    assert "domanda" in testo.lower()


def test_g2_le_reasons_sono_frasi_non_codici():
    eventi = [evento("owner_home_viewed", giorni_fa(2)),
              evento("owner_value_history_viewed", giorni_fa(2))]
    for ragione in interesse(eventi)["reasons"]:
        assert ragione == ragione.strip() and ragione
        assert ragione[0].isupper(), ragione
        assert "_" not in ragione, f"un codice interno e sfuggito: {ragione}"
        assert "owner_" not in ragione


def test_g3_ogni_livello_ha_almeno_una_ragione():
    casi = {
        "none": [],
        "low": [evento("owner_home_viewed", giorni_fa(4))],
        "medium": [evento("owner_home_viewed", giorni_fa(n)) for n in (3, 18)],
        "high": [evento("owner_home_viewed", giorni_fa(1)),
                 evento("owner_home_viewed", giorni_fa(3)),
                 evento("owner_value_history_viewed", giorni_fa(1))],
    }
    for livello, eventi in casi.items():
        vista = interesse(eventi)
        assert vista["level"] == livello, (livello, vista["level"])
        assert vista["reasons"], livello


def test_g4_nessuna_reason_e_rivolta_al_proprietario():
    """Il radar e' per l'operatore: le frasi parlano di lui in terza persona,
    mai a lui."""
    eventi = [evento("owner_home_viewed", giorni_fa(1)),
              evento("owner_buyer_demand_viewed", giorni_fa(1))]
    testo = " ".join(interesse(eventi)["reasons"]).lower()
    for seconda_persona in ("sei ", "hai ", "tu ", "lead caldo", "ti "):
        assert seconda_persona not in testo, seconda_persona


# ---------------------------------------------------------------------------
# H - il perimetro
# ---------------------------------------------------------------------------

def test_h1_nessuna_tabella_nuova():
    sorgenti = inspect.getsource(tracking) + inspect.getsource(interest_service)
    assert "CREATE TABLE" not in sorgenti.upper()
    for tabella in ("owner_events", "owner_interest", "owner_activity"):
        assert tabella not in sorgenti


def test_h2_il_radar_scrive_solo_attraverso_seller_intelligence():
    sorgente = inspect.getsource(tracking)
    assert "seller_intelligence" in sorgente
    codice = _codice(tracking)
    for sql in ("INSERT", "UPDATE", "DELETE"):
        assert not re.search(rf"\b{sql}\b", codice, re.IGNORECASE), sql


def test_h3_la_proiezione_legge_e_basta():
    """`home_updated` e' un campo della proiezione e contiene "updated": si
    cercano i verbi SQL come parole intere, nel codice eseguibile."""
    codice = _codice(interest_service)
    for sql in ("INSERT", "UPDATE", "DELETE", "COMMIT"):
        assert not re.search(rf"\b{sql}\b", codice, re.IGNORECASE), sql
    assert "cursor" not in codice.lower(), "la proiezione non apre connessioni"


def test_h4_i_domini_vietati_non_sono_stati_toccati():
    """LMC-8 (collisione segnalata): `crm/` esce da questo elenco.

    LMC-7 non doveva toccare il CRM, ed e' vero: i suoi file sono
    `owner/tracking.py`, `owner/interest_service.py` e i propri test. Ma
    questo controllo guarda il WORKING TREE condiviso, non le modifiche di
    LMC-7, e LMC-8 ha il mandato esplicito di portare il radar dentro il
    Contact 360. Lasciando `crm/` qui, il guard accuserebbe LMC-7 di una
    modifica fatta dalla fase successiva.

    La garanzia non si perde, cambia soggetto: sotto si verifica che i
    moduli di LMC-7 non nominino il CRM, che e' cio' che "LMC-7 non tocca
    il CRM" significa davvero. Per tutti gli altri domini il controllo sul
    working tree resta.
    """
    import subprocess
    diff = subprocess.run(
        ["git", "--no-optional-locks", "diff", "--name-only", "--",
         "seller_intent/", "next_best_action/", "followup/", "communication/",
         "valuation.py", "main.py"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert diff == "", diff


def test_h4c_property_watch_e_le_migration_restano_fuori_da_lmc7():
    """LMC-10 (collisione segnalata): `property_watch/` e `migrations/`
    escono dall'elenco sopra, per la stessa ragione per cui `crm/` ne usci'
    con LMC-8.

    LMC-10 ha il mandato esplicito - lo STORAGE GATE approvato - di creare la
    068 e di far leggere al motore il profilo effettivo, e quel lavoro vive
    in `property_watch/`. Il controllo sul working tree accuserebbe LMC-7 di
    modifiche della fase successiva.

    La garanzia cambia soggetto e resta: i moduli di LMC-7 non nominano
    Property Watch ne' lo schema. `valuation.py` e `main.py` restano invece
    sorvegliati sul working tree, perche' nessuna fase LMC li ha mai
    toccati.
    """
    for modulo in (tracking, interest_service):
        sorgente = inspect.getsource(modulo)
        for vietato in ("property_watch", "migration", "CREATE TABLE"):
            assert vietato not in sorgente, (modulo.__name__, vietato)


def test_h4b_i_moduli_di_lmc7_non_nominano_il_crm():
    for modulo in (tracking, interest_service):
        sorgente = inspect.getsource(modulo)
        for vietato in ("crm", "next_best_action", "followup", "seller_intent"):
            assert vietato not in sorgente, (modulo.__name__, vietato)


def test_h5_nessuna_migration_creata():
    """SENTINELLA AGGIORNATA DA LMC-10.

    LMC-7 non ha creato schema e continua a non averne bisogno: gli eventi
    stanno in `seller_timeline_events`, il cui `event_type` e' VARCHAR senza
    CHECK. L'unica migration comparsa nel working tree e' la 068 di LMC-10,
    approvata dallo STORAGE GATE, e la si nomina invece di smettere di
    guardare: qualunque ALTRA migration comparisse qui farebbe ancora
    fallire il test.
    """
    import subprocess
    nuovi = {riga[3:].strip() for riga in subprocess.run(
        ["git", "--no-optional-locks", "status", "--porcelain", "--", "migrations/"],
        cwd=ROOT, capture_output=True, text=True).stdout.splitlines()}
    # SENTINELLA AGGIORNATA DA LMC-12: la 069 e' lo stream di notifiche
    # PRE-INCARICO `owner_home_notifications`, approvata dal DESIGN GATE di
    # LMC-12 (dominio OWNER, radice `stime` + `owner_stima_access`). Si nomina
    # invece di smettere di guardare: qualunque ALTRA migration comparisse
    # farebbe ancora fallire questo test.
    atteso = {"migrations/068_lmc10_owner_home_overrides.sql",
              "migrations/068_lmc10_owner_home_overrides_down.sql",
              "migrations/069_lmc12_owner_home_notifications.sql",
              "migrations/069_lmc12_owner_home_notifications_down.sql"}
    assert nuovi - atteso == set(), sorted(nuovi - atteso)


# ---------------------------------------------------------------------------
# I - il frontend: il gesto esiste davvero, e il radar resta invisibile
#
# Si riusa l'armatura DOM certificata di P6 (la stessa di LMC-6): app.js gira
# per davvero, con una fetch finta, e si guarda cosa succede al click.
# ---------------------------------------------------------------------------

from test_lmc6_owner_portal_home import CASA, DETTAGLIO_RICCO, BASE, scenario  # noqa: E402
from test_lmc6_owner_portal_home import detail as lmc6_detail  # noqa: E402


def test_i1_le_sezioni_partono_chiuse():
    """Se stessero aperte, il caricamento della pagina basterebbe a dire "ha
    guardato l'andamento": un segnale che descrive qualcosa che non e'
    successo."""
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
assert(ids['home-history-chart'].hidden === true, 'andamento chiuso');
assert(ids['home-history-changes'].hidden === true, 'variazioni chiuse');
assert(ids['home-demand-content'].hidden === true, 'domanda chiusa');
assert(ids['home-history-toggle'].hidden === false, 'il pulsante andamento c e');
assert(ids['home-demand-toggle'].hidden === false, 'il pulsante domanda c e');
assert(!calls.some((c) => c.url.includes('/events')), 'nessun evento senza gesto');
""")


def test_i2_aprire_l_andamento_registra_l_azione():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
await ids['home-history-toggle'].trigger('click');
const invio = calls.find((c) => c.url.includes('/events'));
assert(invio, 'la richiesta parte');
assert(invio.method === 'POST', 'metodo: ' + invio.method);
assert(invio.url.endsWith('/homes/500/events'), 'rotta: ' + invio.url);
assert(invio.body === JSON.stringify({ action: 'value_history_viewed' }),
       'corpo: ' + invio.body);
assert(ids['home-history-chart'].hidden === false, 'la sezione si apre');
""", extra={f"{BASE}/homes/500/events": [{"status": 204, "body": None},
                                         {"status": 204, "body": None}]})


def test_i3_aprire_la_domanda_registra_l_azione():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
await ids['home-demand-toggle'].trigger('click');
const invio = calls.find((c) => c.url.includes('/events'));
assert(invio.body === JSON.stringify({ action: 'buyer_demand_viewed' }),
       'corpo: ' + invio.body);
assert(ids['home-demand-content'].hidden === false, 'la sezione si apre');
""", extra={f"{BASE}/homes/500/events": [{"status": 204, "body": None}]})


def test_i4_due_click_una_sola_richiesta():
    """L'idempotenza del server c'e' comunque, ma non ha senso bussare due
    volte per la stessa cosa nella stessa pagina."""
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
await ids['home-history-toggle'].trigger('click');
await ids['home-history-toggle'].trigger('click');
await ids['home-history-toggle'].trigger('click');
const invii = calls.filter((c) => c.url.includes('/events'));
assert(invii.length === 1, 'richieste: ' + invii.length);
""", extra={f"{BASE}/homes/500/events": [{"status": 204, "body": None}]})


def test_i5_senza_capability_non_c_e_nemmeno_il_pulsante():
    scenario([CASA], [], {"status": 200, "body": lmc6_detail()}, """
assert(ids['home-history-toggle'].hidden === true, 'niente pulsante andamento');
assert(ids['home-demand-toggle'].hidden === true, 'niente pulsante domanda');
assert(!calls.some((c) => c.url.includes('/events')), 'niente da registrare');
""")


def test_i6_un_guasto_del_tracciamento_non_si_vede():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
await ids['home-history-toggle'].trigger('click');
assert(ids['home-history-chart'].hidden === false, 'la sezione si apre lo stesso');
assert(ids['app-message'].textContent === '', 'nessun messaggio di errore');
assert(ids['home-detail-error'].hidden === true, 'nessuno stato di errore');
assert(ids['login-view'].hidden === true, 'e non butta fuori');
""", extra={f"{BASE}/homes/500/events": [{"status": 500, "body": None}]})


def test_i7_il_radar_e_invisibile_al_proprietario():
    """Niente livello, niente motivazioni, niente parola "tracciamento": il
    portale mostra la casa, non cosa il CRM pensa di chi la guarda."""
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_RICCO}, """
await ids['home-history-toggle'].trigger('click');
await ids['home-demand-toggle'].trigger('click');
const ovunque = Object.values(ids).map(allText).join(' ').toLowerCase();
for (const vietato of ['interesse', 'lead', 'caldo', 'tracc', 'radar',
                       'high', 'medium', 'punteggio', 'attivo da']) {
  assert(!ovunque.includes(vietato), 'compare: ' + vietato);
}
""", extra={f"{BASE}/homes/500/events": [{"status": 204, "body": None},
                                         {"status": 204, "body": None}]})


def test_i8_nel_sorgente_del_portale_non_c_e_il_vocabolario_del_radar():
    portale = (ROOT / "static" / "owner_portal")
    testo = ((portale / "index.html").read_text(encoding="utf-8") +
             (portale / "assets" / "app.js").read_text(encoding="utf-8") +
             (portale / "assets" / "home-view-model.js").read_text(encoding="utf-8"))
    testo = testo.lower()
    for vietato in ("interest_service", "active_days", "reasons", "lead caldo",
                    "owner_home_viewed", "owner_value_history_viewed",
                    "owner_buyer_demand_viewed", "seller_timeline"):
        assert vietato not in testo, vietato
