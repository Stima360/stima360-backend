"""LMC-8 - il radar dell'interesse dentro il CRM: read-model e scheda contatto.

COSA AGGIUNGE QUESTA FASE, E COSA NON AGGIUNGE.

Aggiunge un blocco `owner_home` al Contact 360 e un riquadro nella scheda
contatto. Non aggiunge nessuna decisione: il livello di interesse lo calcola
LMC-7 e qui si traduce soltanto in italiano, le motivazioni arrivano gia'
scritte, e nessun task, nessuna attivita' e nessun follow-up nasce da solo.
Il CRM informa; chi decide e' chi legge.

I test sotto sono divisi per quello che devono impedire: che il livello venga
ricalcolato altrove, che una casa di un'altra agenzia entri nella scheda, che
la diagnostica tecnica di Buyer Pressure finisca sotto gli occhi di un
operatore, e che LMC-8 tocchi qualcosa che non le appartiene.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from owner import crm_radar

ROOT = Path(__file__).resolve().parents[1]
OS_SHELL = ROOT / "static" / "os_shell" / "assets"
CONTATTO_JS = OS_SHELL / "views" / "contatto-dettaglio.js"
TIMELINE_JS = OS_SHELL / "components" / "timeline.js"

ADESSO = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def _senza_commenti(testo: str) -> str:
    testo = re.sub(r"/\*.*?\*/", "", testo, flags=re.S)
    return "\n".join(riga.split("//", 1)[0] for riga in testo.splitlines())


def _regione_lmc8(percorso: Path) -> str:
    """Il tratto LMC-8 del file, con i commenti tolti DOPO il ritaglio.

    I marcatori vivono dentro i commenti: cercarli nel testo gia' ripulito
    non troverebbe niente.
    """
    testo = percorso.read_text(encoding="utf-8")
    regioni = re.findall(r"LMC8_START(.*?)LMC8_END", testo, flags=re.S)
    assert regioni, "marcatori LMC8 assenti"
    return _senza_commenti("\n".join(regioni))


def _codice(modulo) -> str:
    """Il solo codice eseguibile di un modulo Python, senza prosa."""
    albero = ast.parse(inspect.getsource(modulo))
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            nodo.value = ""
    return ast.unparse(albero)


def casa(stima_id=501, **over):
    base = {
        "id": stima_id, "comune": "Alba Adriatica", "microzona": "Villa Fiore",
        "via": "Via Trieste", "civico": "12", "tipologia": "Appartamento",
        "mq": 95, "data": datetime(2026, 5, 2, tzinfo=timezone.utc),
    }
    base.update(over)
    return base


def interesse(level="none", **over):
    base = {
        "level": level, "active_days_7d": 0, "active_days_30d": 0,
        "last_activity_at": None, "home_viewed_days_30d": 0,
        "value_history_viewed": False, "buyer_demand_viewed": False,
        "home_updated": False, "consultation_requested": False,
        "reasons": ["Nessuna attività nel portale negli ultimi 30 giorni"],
    }
    base.update(over)
    return {"interest": base}


class Ctx:
    def __init__(self, agency_id=7):
        self._a = agency_id

    def require_agency(self):
        return self._a


def blocco(monkeypatch, case, valori=None, interessi=None, override=None):
    """Il blocco costruito con repository e proiezione sostituiti da spie."""
    from owner import repository as owner_repository

    monkeypatch.setattr(owner_repository, "contact_homes",
                        lambda agency_id, contact_id: list(case))
    # LMC-10: la scheda mostra il PROFILO EFFETTIVO, quindi il blocco legge
    # anche gli override del proprietario. Senza questa spia il test
    # aprirebbe una connessione vera. `None` di default: nessuna correzione,
    # cioe' il caso normale e quello in cui tutti i test precedenti erano
    # gia' scritti.
    monkeypatch.setattr(owner_repository, "home_override",
                        lambda agency_id, stima_id: (override or {}).get(stima_id))
    monkeypatch.setattr(crm_radar, "_valori_casa",
                        lambda agency_id, stima_id: (valori or {}).get(
                            stima_id, {"initial_value": None, "current_value": None,
                                       "current_value_computed_at": None}))
    monkeypatch.setattr(crm_radar.interest_service, "interest_for_stima_scoped",
                        lambda ctx, stima_id, **kw: (interessi or {}).get(
                            stima_id, interesse()))
    return crm_radar.contact_homes_block(Ctx(), 31)


# ---------------------------------------------------------------------------
# A - il blocco esiste, ed e' additivo
# ---------------------------------------------------------------------------

def test_a1_contatto_senza_case_blocco_non_disponibile(monkeypatch):
    vista = blocco(monkeypatch, [])
    assert vista == {"available": False, "homes": []}


def test_a2_contatto_con_una_casa(monkeypatch):
    vista = blocco(monkeypatch, [casa()])
    assert vista["available"] is True
    assert len(vista["homes"]) == 1
    assert vista["homes"][0]["stima_id"] == 501


def test_a3_contatto_con_piu_case_tutte_presenti(monkeypatch):
    vista = blocco(monkeypatch, [casa(501), casa(502), casa(503)])
    assert [h["stima_id"] for h in vista["homes"]] == [501, 502, 503]


def test_a4_il_360_espone_il_blocco_senza_perdere_le_nove_sezioni(monkeypatch):
    import crm.service as crm_service

    for nome in ("get_contact", "list_leads", "list_properties",
                 "list_requests_scoped", "list_matches_scoped",
                 "list_visits_by_contact", "list_activities", "list_tasks"):
        if nome == "get_contact":
            monkeypatch.setattr(crm_service, nome,
                                lambda ctx, cid: {"id": cid, "roles": []})
        else:
            monkeypatch.setattr(crm_service, nome, lambda *a, **k: [])
    monkeypatch.setattr(crm_service, "owner_home_block",
                        lambda ctx, cid: {"available": False, "homes": []})

    vista = crm_service.get_contact_360(Ctx(), 31)
    storiche = {"contact", "roles", "leads", "properties", "buy_requests",
                "matches", "visits", "activities", "tasks"}
    assert storiche <= set(vista), "le nove sezioni restano"
    assert set(vista) == storiche | {"owner_home"}


# ---------------------------------------------------------------------------
# B - l'ordine, e la regola dichiarata
# ---------------------------------------------------------------------------

def test_b1_ordine_per_ultima_attivita_piu_recente(monkeypatch):
    vista = blocco(monkeypatch, [casa(501), casa(502), casa(503)], interessi={
        501: interesse("low", last_activity_at=(ADESSO - timedelta(days=9)).isoformat()),
        502: interesse("high", last_activity_at=(ADESSO - timedelta(days=1)).isoformat()),
        503: interesse("medium", last_activity_at=(ADESSO - timedelta(days=4)).isoformat()),
    })
    assert [h["stima_id"] for h in vista["homes"]] == [502, 503, 501]


def test_b2_senza_attivita_ordine_per_stima_piu_recente(monkeypatch):
    vista = blocco(monkeypatch, [
        casa(501, data=datetime(2026, 1, 5, tzinfo=timezone.utc)),
        casa(502, data=datetime(2026, 7, 20, tzinfo=timezone.utc)),
        casa(503, data=datetime(2026, 4, 2, tzinfo=timezone.utc)),
    ])
    assert [h["stima_id"] for h in vista["homes"]] == [502, 503, 501]


def test_b3_chi_ha_attivita_precede_chi_non_ne_ha(monkeypatch):
    """Una casa vista ieri viene prima di una stima di stamattina senza
    attivita': il radar ordina per interesse osservato, non per anagrafica."""
    vista = blocco(monkeypatch, [
        casa(501, data=datetime(2026, 9, 19, tzinfo=timezone.utc)),
        casa(502, data=datetime(2025, 1, 1, tzinfo=timezone.utc)),
    ], interessi={502: interesse("low",
                                 last_activity_at=(ADESSO - timedelta(days=1)).isoformat())})
    assert [h["stima_id"] for h in vista["homes"]] == [502, 501]


def test_b4_la_regola_di_ordinamento_e_documentata():
    testo = (inspect.getdoc(crm_radar.contact_homes_block) or "").lower()
    testo += (inspect.getdoc(crm_radar._ordine) or "").lower()
    assert "attivita" in testo.replace("à", "a")
    assert "piu' recente" in testo or "più recente" in testo
    assert "stima piu' recente" in testo or "stima più recente" in testo


# ---------------------------------------------------------------------------
# C - i valori della casa
# ---------------------------------------------------------------------------

def test_c1_valore_iniziale_attuale_e_timestamp(monkeypatch):
    vista = blocco(monkeypatch, [casa()], valori={501: {
        "initial_value": 185000, "current_value": 212000,
        "current_value_computed_at": "2026-09-10T08:00:00+00:00"}})
    home = vista["homes"][0]
    assert home["initial_value"] == 185000
    assert home["current_value"] == 212000
    assert home["current_value_computed_at"] == "2026-09-10T08:00:00+00:00"


def test_c2_senza_valore_attuale_i_campi_sono_null_non_zero(monkeypatch):
    vista = blocco(monkeypatch, [casa()], valori={501: {
        "initial_value": 185000, "current_value": None,
        "current_value_computed_at": None}})
    home = vista["homes"][0]
    assert home["current_value"] is None
    assert home["current_value_computed_at"] is None
    assert 0 not in (home["current_value"], home["initial_value"])


def test_c3_l_indirizzo_e_i_dati_descrittivi_ci_sono(monkeypatch):
    home = blocco(monkeypatch, [casa()])["homes"][0]
    assert "Via Trieste" in home["address"] and "12" in home["address"]
    assert home["tipologia"] == "Appartamento"
    assert home["mq"] == 95


# ---------------------------------------------------------------------------
# D - il radar arriva da LMC-7, non viene rifatto
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("livello", ["high", "medium", "low", "none"])
def test_d1_i_quattro_livelli_passano_inalterati(monkeypatch, livello):
    vista = blocco(monkeypatch, [casa()], interessi={501: interesse(livello)})
    assert vista["homes"][0]["interest"]["level"] == livello


def test_d2_le_reasons_passano_senza_essere_riscritte(monkeypatch):
    ragioni = ["È tornato 3 giorni negli ultimi 7 giorni",
               "Ha consultato l’andamento del valore",
               "Ha consultato la domanda di immobili simili"]
    vista = blocco(monkeypatch, [casa()],
                   interessi={501: interesse("high", reasons=list(ragioni))})
    assert vista["homes"][0]["interest"]["reasons"] == ragioni


def test_d3_i_contatori_passano_inalterati(monkeypatch):
    vista = blocco(monkeypatch, [casa()], interessi={501: interesse(
        "high", active_days_7d=3, active_days_30d=5, home_viewed_days_30d=5,
        value_history_viewed=True, buyer_demand_viewed=True,
        last_activity_at="2026-09-18T09:00:00+00:00")})
    radar = vista["homes"][0]["interest"]
    assert radar["active_days_7d"] == 3
    assert radar["active_days_30d"] == 5
    assert radar["value_history_viewed"] is True
    assert radar["buyer_demand_viewed"] is True
    assert radar["last_activity_at"] == "2026-09-18T09:00:00+00:00"


def test_d4_le_fasi_future_restano_false(monkeypatch):
    radar = blocco(monkeypatch, [casa()])["homes"][0]["interest"]
    assert radar["home_updated"] is False
    assert radar["consultation_requested"] is False


def test_d5_il_crm_non_contiene_nessuna_soglia():
    """Il livello lo decide `interest_service` e nessun altro: se una soglia
    comparisse qui, esisterebbero due verita' che possono divergere."""
    import crm.service as crm_service

    for modulo in (crm_radar, crm_service):
        codice = _codice(modulo)
        for soglia in ("ACTIVE_DAYS", "active_days_7d >", "active_days_30d >",
                       ">= 2", "'high'", '"high"'):
            assert soglia not in codice, (modulo.__name__, soglia)


def test_d6_il_livello_non_viene_tradotto_nel_backend(monkeypatch):
    """La traduzione in italiano e' una scelta di presentazione e sta nella
    UI: il JSON porta il valore del dominio."""
    vista = blocco(monkeypatch, [casa()], interessi={501: interesse("high")})
    testo = json.dumps(vista, default=str)
    for italiano in ("ALTO", "MEDIO", "BASSO", "NESSUNO"):
        assert italiano not in testo, italiano


# ---------------------------------------------------------------------------
# E - privacy: il segnale, non la diagnostica
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("vietato", [
    "score", "punteggio", "pressure", "buy_request", "match_id", "budget",
    "factor", "fattore", "token", "session", "idempotency", "email",
    "telefono", "evaluated", "fingerprint", "algorithm",
])
def test_e1_niente_diagnostica_nel_blocco(monkeypatch, vietato):
    vista = blocco(monkeypatch, [casa()], valori={501: {
        "initial_value": 185000, "current_value": 212000,
        "current_value_computed_at": "2026-09-10T08:00:00+00:00"}},
        interessi={501: interesse("high", active_days_7d=3, value_history_viewed=True)})
    assert vietato not in json.dumps(vista, default=str).lower(), vietato


def test_e2_la_forma_di_una_casa_e_chiusa(monkeypatch):
    atteso = {"stima_id", "address", "tipologia", "mq", "created_at",
              "initial_value", "current_value", "current_value_computed_at",
              "interest"}
    assert set(blocco(monkeypatch, [casa()])["homes"][0]) == atteso


def test_e3_il_blocco_non_legge_la_rilevazione_buyer_pressure():
    """Il CRM vede SE il proprietario ha aperto la domanda, non cosa c'era
    dentro: la rilevazione resta in Property Watch."""
    # Nel CODICE, non nella prosa: il commento SPIEGA che la rilevazione non
    # si carica, quindi la nomina.
    codice = _codice(crm_radar)
    assert "home_buyer_pressure" not in codice
    assert "buyer_pressure=None" in codice or "buyer_pressure" in codice, \
        "il compositore riceve esplicitamente None"
    assert "demand" not in codice.replace("buyer_pressure", "")


# ---------------------------------------------------------------------------
# F - la scheda contatto
# ---------------------------------------------------------------------------

def test_f1_esiste_il_riquadro_la_mia_casa():
    js = CONTATTO_JS.read_text(encoding="utf-8")
    assert "owner_home" in js
    assert "LA MIA CASA" in js.upper()


def test_f2_i_quattro_livelli_hanno_un_etichetta_italiana():
    blocco_js = _regione_lmc8(CONTATTO_JS)
    for chiave, etichetta in (("high", "ALTO"), ("medium", "MEDIO"),
                              ("low", "BASSO"), ("none", "NESSUNO")):
        assert chiave in blocco_js and etichetta in blocco_js, chiave


def test_f3_le_reasons_si_vedono_ma_non_all_infinito():
    blocco_js = _regione_lmc8(CONTATTO_JS)
    assert "reasons" in blocco_js
    # Il limite e' una costante dichiarata, non un numero magico dentro la
    # chiamata: si verifica che esista e che valga 3 o 4.
    assert "OWNER_MAX_REASONS" in blocco_js
    assert re.search(r"OWNER_MAX_REASONS\s*=\s*[34]\b",
                     CONTATTO_JS.read_text(encoding="utf-8")), \
        "le motivazioni vanno limitate a un numero ragionevole"
    assert "slice(0, OWNER_MAX_REASONS)" in blocco_js


def test_f4_nessun_punteggio_nella_scheda():
    blocco_js = _regione_lmc8(CONTATTO_JS)
    for vietato in ("score", "punteggio", "/100", "% "):
        assert vietato not in blocco_js, vietato


def test_f5_nessun_gergo_tecnico_nella_scheda():
    blocco_js = _regione_lmc8(CONTATTO_JS).lower()
    for vietato in ("buy_request", "match_id", "budget", "pressure",
                    "buyer pressure", "idempotency", "agency_id"):
        assert vietato not in blocco_js, vietato


def test_f6_il_riquadro_scompare_se_non_c_e_niente():
    blocco_js = _regione_lmc8(CONTATTO_JS)
    assert "available" in blocco_js, "il blocco deve rispettare `available`"


# ---------------------------------------------------------------------------
# G - le etichette della timeline
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("evento,atteso", [
    ("owner_home_viewed", "Ha aperto La Mia Casa"),
    ("owner_value_history_viewed", "Ha consultato l’andamento del valore"),
    ("owner_buyer_demand_viewed", "Ha consultato la domanda di immobili simili"),
])
def test_g1_le_tre_etichette_sono_leggibili(evento, atteso):
    js = TIMELINE_JS.read_text(encoding="utf-8")
    assert evento in js, evento
    assert atteso in js, atteso


def test_g2_la_sorgente_del_portale_ha_un_nome_leggibile():
    js = TIMELINE_JS.read_text(encoding="utf-8")
    assert "owner_portal" in js
    assert re.search(r"owner_portal:\s*'[^']+'", js), "serve un'etichetta, non il codice"


def test_g3_nessun_gergo_nelle_etichette_nuove():
    js = TIMELINE_JS.read_text(encoding="utf-8")
    righe = [r for r in js.splitlines() if "owner_" in r and ":" in r]
    testo = " ".join(righe).lower()
    for vietato in ("buy", "match", "score", "punteggio"):
        assert vietato not in testo.replace("owner_buyer_demand_viewed", ""), vietato


def test_g4_il_payload_tecnico_non_viene_mostrato():
    """`{"action": "home_viewed"}` non aggiunge niente all'etichetta e
    ripeterebbe in gergo cio' che la frase dice gia' in italiano."""
    js = _senza_commenti(TIMELINE_JS.read_text(encoding="utf-8"))
    assert "action" in js, "la chiave va gestita esplicitamente"
    assert re.search(r"HIDDEN|nascost|skip", js, re.IGNORECASE), \
        "serve un meccanismo dichiarato per non mostrarla"


# ---------------------------------------------------------------------------
# H - cosa LMC-8 non ha toccato
# ---------------------------------------------------------------------------

def test_h1_nessuna_automazione_commerciale():
    sorgente = inspect.getsource(crm_radar)
    for vietato in ("create_task", "create_activity", "insert_task", "enqueue",
                    "send_", "next_best_action", "followup", "seller_intent"):
        assert vietato not in sorgente, vietato


def test_h2_il_blocco_e_sola_lettura():
    codice = _codice(crm_radar)
    for sql in ("INSERT", "UPDATE", "DELETE", "COMMIT"):
        assert not re.search(rf"\b{sql}\b", codice, re.IGNORECASE), sql


def test_h3_i_domini_vietati_non_sono_stati_toccati():
    """LMC-10 (collisione segnalata): `property_watch/` e `migrations/`
    escono dall'elenco.

    LMC-8 non li ha toccati e non doveva. Ma questo controllo guarda il
    WORKING TREE condiviso, e LMC-10 ha il mandato esplicito - lo STORAGE
    GATE approvato - di creare la 068 e di far leggere al motore il profilo
    effettivo. Lasciandoli qui, il guard accuserebbe LMC-8 di modifiche
    della fase successiva; e' la stessa ragione per cui `crm/` usci'
    dall'elenco di LMC-7.

    La garanzia cambia soggetto e resta, sotto: il modulo di LMC-8 non
    nomina ne' Property Watch ne' lo schema. `valuation.py`, `main.py` e
    `oggi.js` restano sorvegliati sul working tree.
    """
    import subprocess
    diff = subprocess.run(
        ["git", "--no-optional-locks", "diff", "--name-only", "--",
         "seller_intent/", "next_best_action/", "followup/", "communication/",
         "valuation.py", "main.py",
         "static/os_shell/assets/views/oggi.js"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert diff == "", diff


def test_h3b_il_modulo_di_lmc8_non_nomina_property_watch_ne_lo_schema():
    import inspect
    sorgente = inspect.getsource(crm_radar)
    for vietato in ("property_watch", "migration", "CREATE TABLE",
                    "owner_home_overrides"):
        assert vietato not in sorgente, vietato


def test_h4_il_portale_proprietario_non_e_cambiato():
    """LMC-8 e' CRM: il portale di LMC-6/7 deve restare identico."""
    import subprocess
    atteso = {
        "static/owner_portal/assets/app.css",
        "static/owner_portal/assets/app.js",
        "static/owner_portal/index.html",
    }
    toccati = {riga[3:].strip() for riga in subprocess.run(
        ["git", "--no-optional-locks", "status", "--porcelain", "--",
         "static/owner_portal/"],
        cwd=ROOT, capture_output=True, text=True).stdout.splitlines()}
    nuovi = toccati - atteso - {"static/owner_portal/assets/home-view-model.js"}
    assert nuovi == set(), f"LMC-8 ha toccato il portale: {sorted(nuovi)}"


def test_h5_nessuna_migration():
    """SENTINELLA AGGIORNATA DA LMC-10: la sola 068 e' attesa, e nominata.

    LMC-8 non ha creato schema. La 068 e' di LMC-10 e passa dallo STORAGE
    GATE approvato; qualunque ALTRA migration comparisse nel working tree
    farebbe ancora fallire questo test.
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


def _senza_chiamate(testo: str, funzione: str) -> str:
    """Toglie ogni `funzione(...)` con le parentesi bilanciate.

    Serve a modellare l'escape invece di inseguirlo: ciò che sta DENTRO
    `escapeHtml(...)` e' ripulito per definizione, comprese le
    interpolazioni annidate, quindi si rimuove prima di cercare i `${...}`
    rimasti scoperti.
    """
    while True:
        inizio = testo.find(funzione + "(")
        if inizio == -1:
            return testo
        i = inizio + len(funzione)
        livello = 0
        while i < len(testo):
            if testo[i] == "(":
                livello += 1
            elif testo[i] == ")":
                livello -= 1
                if livello == 0:
                    break
            i += 1
        testo = testo[:inizio] + "ESCAPED" + testo[i + 1:]


def test_f7_ogni_valore_interpolato_passa_da_un_escape():
    """La scheda contatto costruisce HTML per stringhe, quindi l'escape non
    e' un dettaglio: un indirizzo arriva da un form pubblico.

    Si tolgono le chiamate che ripuliscono - `escapeHtml`, `renderBadge` che
    e' il componente condiviso, `formatEuro` che ripulisce al suo interno - e
    poi si pretende che nessuna interpolazione resti scoperta. Restano
    ammessi pochi locali nominati, che contengono HTML gia' costruito da
    pezzi ripuliti: l'elenco e' corto di proposito, e va riletto quando
    cambia.
    """
    blocco_js = _regione_lmc8(CONTATTO_JS)
    for funzione in ("escapeHtml", "renderBadge", "formatEuro", "ownerHomeValue"):
        blocco_js = _senza_chiamate(blocco_js, funzione)

    locali_sicuri = {"titolo", "ultima", "sottotitolo"}
    # Composizioni di frammenti gia' costruiti da questi stessi renderer:
    # non introducono un valore nuovo, mettono insieme quelli ripuliti.
    composizioni = ("renderOwnerHomeCard", "motivi.map(")
    espressioni = re.findall(r"\$\{([^{}]*)\}", blocco_js)
    assert espressioni, "nessuna interpolazione trovata: la regione e' sbagliata"
    for espressione in espressioni:
        testo = espressione.strip()
        if testo in ("", "''") or testo.startswith("motivi.length") or "ESCAPED" in testo:
            continue
        if any(c in testo for c in composizioni):
            continue
        assert testo in locali_sicuri, testo


def test_f8_il_blocco_non_chiama_nessuna_api_ne_scrive():
    """Il riquadro si disegna con i dati che il 360 ha gia' portato: non fa
    una seconda chiamata, e non ne fa nessuna che scriva."""
    blocco_js = _regione_lmc8(CONTATTO_JS)
    for vietato in ("apiGet", "apiPost", "apiPatch", "apiDelete", "fetch("):
        assert vietato not in blocco_js, vietato


def test_f9_le_azioni_esistenti_restano_le_uniche():
    """LMC-8 non inventa endpoint. Attivita' e task si creano con i dialoghi
    che la scheda ha gia', e li apre una persona: nel riquadro del radar non
    c'e' nessun pulsante che crei qualcosa."""
    js = CONTATTO_JS.read_text(encoding="utf-8")
    assert "openNewActivityDialog" in js and "openNewTaskDialog" in js, \
        "le azioni esistenti restano nella scheda"
    blocco_js = _regione_lmc8(CONTATTO_JS)
    for vietato in ("openNewActivityDialog", "openNewTaskDialog", "addEventListener"):
        assert vietato not in blocco_js, f"{vietato}: il radar non agisce, mostra"
