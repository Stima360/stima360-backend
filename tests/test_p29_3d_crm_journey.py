"""P29-3D senza database: i TESTI della sequenza, il CATALOGO, e cio' che
la scheda contatto mostra a una persona.

PERCHE' QUI E NON IN POSTGRES. Quello che questa fase aggiunge di piu'
delicato non e' una transazione: e' cio' che l'agenzia DICE al proprietario di
casa, e cio' che il CRM mette su uno schermo. Sono funzioni pure - un
template e' un dizionario che diventa testo, il catalogo e' una costante, la
traduzione di una riga del ledger e' una `dict` - e si provano dove stanno,
parola per parola. Un test che avesse bisogno di PostgreSQL per rispondere
"questa mail promette qualcosa che non sappiamo?" starebbe misurando la cosa
sbagliata.

LE SENTINELLE tengono il perimetro: i cinque testi sono immutabili, il
provisioning non accende niente, nessuna colonna operativa esce dall'API,
nessun cron nuovo, e il documento P29-2.0 resta fuori dall'indice.

Le prove che hanno bisogno di righe vere - idempotenza del provisioning,
cutoff dell'attivazione, permessi, 503 senza la 071 - stanno nel modulo
gemello `test_p29_3d_crm_journey_postgres.py`.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACCHETTO = ROOT / "communication"
STATICI = ROOT / "static" / "os_shell" / "assets"

CHIAVI = ("stima_lead_m1", "stima_lead_m2", "stima_lead_m3", "stima_lead_m4",
          "stima_lead_m5")

#: Un contesto di rendering completo, con indirizzi SENZA CIFRE: cosi' un
#: test puo' dire "nel corpo non c'e' nessun numero" senza inciampare
#: nell'URL, che i numeri li porta per conto suo.
CONTESTO = {
    "contact_first_name": "Anna",
    "agency_name": "Immobiliare Rossi",
    "unsubscribe_url": "https://esempio.test/u/disiscrizione",
    "stima_url": "https://esempio.test/s/stima",
    "owner_portal_url": "https://esempio.test/owner/",
}


def _codice(modulo) -> str:
    """Il CODICE, senza docstring e senza commenti: una sentinella che
    leggesse la prosa scambierebbe una spiegazione per un'infrazione."""
    testo = inspect.getsource(modulo)
    testo = re.sub(r'"{3}[\s\S]*?"{3}', "", testo)
    return re.sub(r"#[^\n]*", "", testo)


def _reso(chiave: str):
    from communication import templates
    return templates.render(chiave, 1, CONTESTO)


# ===========================================================================
# A - I TESTI DELLA SEQUENZA
# ===========================================================================

def test_01_i_cinque_testi_sono_registrati_email_marketing_versione_uno():
    from communication import templates
    from communication.enums import TYPE_MARKETING
    for chiave in CHIAVI:
        t = templates.get(chiave, 1)
        assert t.channel == "email", chiave
        assert t.communication_type == TYPE_MARKETING, chiave
        assert t.version == 1, chiave
        assert t.subject is not None, chiave
    # E niente di piu': un sesto testo commerciale sarebbe una fase non fatta.
    assert set(templates.REGISTRY) == {("registry_probe", 1)} | {(k, 1) for k in CHIAVI}


def test_02_ogni_testo_di_marketing_PRETENDE_il_link_di_disiscrizione():
    """Non "lo contiene se glielo passi": lo dichiara fra i campi richiesti, e
    senza il rendering fallisce prima di produrre una riga di testo."""
    from communication import templates
    for chiave in CHIAVI:
        assert templates.UNSUBSCRIBE_FIELD in templates.get(chiave, 1).required_fields, chiave


def test_03_il_link_di_disiscrizione_e_DENTRO_ogni_corpo_spedito():
    """Il campo richiesto garantisce che arrivi; questo garantisce che venga
    scritto. Un template che lo chiedesse e poi non lo stampasse passerebbe
    il test precedente e manderebbe una mail senza via d'uscita."""
    for chiave in CHIAVI:
        _, corpo = _reso(chiave)
        assert CONTESTO["unsubscribe_url"] in corpo, chiave
        assert "non vuoi piu' ricevere" in corpo.lower(), chiave


def test_04_la_firma_e_l_agenzia_e_nessun_nome_di_agente_e_inventato():
    """Il sistema non sa chi segue quel contatto. Firmare con un nome proprio
    sarebbe una bugia piccola, inutile, e ripetuta cinque volte."""
    for chiave in CHIAVI:
        _, corpo = _reso(chiave)
        assert corpo.rstrip().count(CONTESTO["agency_name"]) >= 1, chiave
        assert "su Stima360" in corpo, chiave
        # La chiusura e' l'ultima cosa: sotto la firma non si aggiunge altro
        # oltre alla riga della disiscrizione.
        coda = corpo.split(CONTESTO["agency_name"])[-1]
        assert coda.strip().startswith("su Stima360"), chiave


VIETATE = (
    # urgenza e scarsita'
    "affrett", "urgent", "ultima occasion", "ultimo giorno", "solo per pochi",
    "posti limitat", "non perdere", "approfitta", "scadenz", "offerta valida",
    "subito",
    # acquirenti inventati
    "acquirent", "comprator", "ho gia' qualcuno", "una persona interessata",
    # promesse
    "garant", "promettiamo", "ti promett", "venderemo", "venderai",
    "miglior prezzo", "sicuramente",
    # statistiche che il sistema non conosce
    "in media", "mediamente", "statistic", "percentual", "%",
    # esche
    "esclusiv", "gratis", "occasione",
)


@pytest.mark.parametrize("chiave", CHIAVI)
def test_05_nessuna_urgenza_nessun_acquirente_nessuna_promessa(chiave):
    soggetto, corpo = _reso(chiave)
    testo = f"{soggetto}\n{corpo}".lower()
    trovate = [v for v in VIETATE if v in testo]
    assert trovate == [], (chiave, trovate)


@pytest.mark.parametrize("chiave", CHIAVI)
def test_06_nessuna_cifra_perche_il_sistema_non_conosce_nessun_numero(chiave):
    """Tempi medi di vendita, percentuali di rialzo, "il 70% degli immobili":
    sono i numeri che una mail di questo tipo di solito porta, e nessuno di
    essi e' un fatto che questo sistema possieda. Gli indirizzi del contesto
    non ne contengono, quindi ogni cifra nel corpo verrebbe dal testo."""
    _, corpo = _reso(chiave)
    # Il nome del prodotto nella firma e' l'unica cifra ammessa, e non e'
    # un'affermazione sul mercato.
    assert corpo.count("Stima360") == 1, chiave
    cifre = [c for c in corpo.replace("Stima360", "Stima") if c.isdigit()]
    assert cifre == [], (chiave, cifre)


@pytest.mark.parametrize("chiave,parole", list(zip(CHIAVI, (94, 101, 97, 113, 71))))
def test_07_ogni_testo_resta_corto(chiave, parole):
    """Centoventi-centosessanta parole era il tetto; questi stanno sotto. La
    lunghezza e' fissata esattamente perche' un testo non si allunga per
    caso: se cambia, cambia in una versione nuova."""
    _, corpo = _reso(chiave)
    assert len(corpo.split()) == parole, (chiave, len(corpo.split()))
    assert len(corpo.split()) <= 160, chiave


def test_08_i_cinque_soggetti_sono_distinti_e_non_urlano():
    soggetti = [_reso(k)[0] for k in CHIAVI]
    assert len(set(soggetti)) == 5, soggetti
    for s in soggetti:
        assert s == s.strip() and s != "", repr(s)
        assert len(s) <= 60, s
        assert "!" not in s, s
        assert s != s.upper(), s


def test_09_un_campo_mancante_e_un_errore_non_un_testo_con_un_buco():
    from communication import templates
    from communication.exceptions import ValidationError
    for chiave in CHIAVI:
        richiesti = templates.get(chiave, 1).required_fields
        for campo in sorted(richiesti):
            monco = {**CONTESTO, campo: "   "}
            with pytest.raises(ValidationError):
                templates.render(chiave, 1, monco)


def test_10_ogni_testo_chiede_SOLO_gli_indirizzi_di_cui_parla():
    """M1 rimanda alla stima, M2 e M5 alla scheda della casa, M3 e M4 a
    nessun posto. Un template che chiedesse un campo che non stampa
    obbligherebbe il motore a calcolarlo per niente."""
    from communication import templates
    atteso = {
        "stima_lead_m1": {"stima_url"},
        "stima_lead_m2": {"owner_portal_url"},
        "stima_lead_m3": set(),
        "stima_lead_m4": set(),
        "stima_lead_m5": {"owner_portal_url"},
    }
    for chiave, extra in atteso.items():
        t = templates.get(chiave, 1)
        assert t.required_fields == templates.BASE_STIMA | extra, chiave
        _, corpo = _reso(chiave)
        for campo in extra:
            assert CONTESTO[campo] in corpo, (chiave, campo)
        for assente in {"stima_url", "owner_portal_url"} - extra:
            assert CONTESTO[assente] not in corpo, (chiave, assente)


IMPRONTE = {
    "stima_lead_m1": "57633d44a89a6df9",
    "stima_lead_m2": "929d7e224aa554a9",
    "stima_lead_m3": "37c5b021227bb2a1",
    "stima_lead_m4": "272af4a5c51ee83c",
    "stima_lead_m5": "6c1190114db4dc27",
}


@pytest.mark.parametrize("chiave", CHIAVI)
def test_11_i_cinque_corpi_sono_IMMUTABILI(chiave):
    """Il ledger conserva cio' che e' stato spedito con `(chiave, 1)` e deve
    restare ricostruibile parola per parola. Correggere una virgola qui
    dentro riscriverebbe il passato: si aggiunge la v2."""
    from communication import templates
    sorgente = inspect.getsource(templates.REGISTRY[(chiave, 1)].body)
    impronta = hashlib.sha256(sorgente.encode()).hexdigest()[:16]
    assert impronta == IMPRONTE[chiave], (chiave, impronta)


def test_12_nessun_testo_chiede_un_campo_che_il_motore_non_sa_dare():
    """I campi richiesti si leggono dal dizionario che il tick costruisce: se
    un template ne chiedesse uno in piu', il passo morirebbe al rendering, in
    produzione, sul primo contatto."""
    from communication import journey_tick, templates
    albero = ast.parse(inspect.getsource(journey_tick._contesto_di_rendering))
    dizionari = [n for n in ast.walk(albero) if isinstance(n, ast.Dict)]
    assert dizionari, "il contesto non e' piu' un dizionario letterale"
    offerti = {k.value for d in dizionari for k in d.keys
               if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    for chiave in CHIAVI:
        mancanti = templates.get(chiave, 1).required_fields - offerti
        assert mancanti == set(), (chiave, mancanti)


# ===========================================================================
# B - IL CATALOGO DELLA SEQUENZA
# ===========================================================================

def test_13_i_passi_sono_cinque_numerati_da_uno_senza_buchi():
    from communication import journey_catalog as cat
    passi = cat.PASSI_STIMA_LEAD
    assert len(passi) == 5
    assert [p["step_no"] for p in passi] == [1, 2, 3, 4, 5]
    assert [p["step_key"] for p in passi] == ["M1", "M2", "M3", "M4", "M5"]
    assert [p["reason_code"] for p in passi] == ["m1", "m2", "m3", "m4", "m5"]


def test_14_i_ritardi_sono_uno_quattro_sette_quattordici_trenta_giorni():
    from communication import journey_catalog as cat
    giorni = [p["delay_seconds"] // 86400 for p in cat.PASSI_STIMA_LEAD]
    assert giorni == [1, 4, 7, 14, 30]
    for p in cat.PASSI_STIMA_LEAD:
        assert p["delay_seconds"] % 86400 == 0, p["step_key"]


def test_15_M1_parte_dal_trigger_gli_altri_dall_invio_del_passo_prima():
    """Mai dalla nascita dell'iscrizione: un tick in ritardo sposterebbe
    tutta la sequenza in avanti insieme a se'."""
    from communication import journey_catalog as cat
    from communication.journey_enums import DELAY_FROM
    da = [p["delay_from"] for p in cat.PASSI_STIMA_LEAD]
    assert da == ["trigger"] + ["previous_step_sent"] * 4
    assert set(da) <= DELAY_FROM


def test_16_solo_M3_e_assistito():
    """M3 propone un sopralluogo, cioe' impegna il tempo di una persona: non
    puo' partire da solo. Gli altri quattro sono testo, e partono."""
    from communication import journey_catalog as cat
    modi = {p["step_key"]: p["default_mode"] for p in cat.PASSI_STIMA_LEAD}
    assert modi == {"M1": "automatic", "M2": "automatic", "M3": "assisted",
                    "M4": "automatic", "M5": "automatic"}


def test_17_ogni_passo_punta_al_proprio_template_registrato():
    from communication import journey_catalog as cat, templates
    for passo in cat._passi_completi():
        assert passo["template_key"] == f"stima_lead_{passo['reason_code']}"
        assert passo["template_version"] == 1
        t = templates.get(passo["template_key"], passo["template_version"])
        assert t.channel == passo["channel"] == "email"
        assert t.communication_type == passo["communication_type"] == "marketing"


def test_18_la_finestra_e_lunedi_sabato_nove_diciannove_ora_di_Roma():
    from communication import journey_catalog as cat
    assert cat.FINESTRA_LAVORATIVA == {"days": [1, 2, 3, 4, 5, 6],
                                       "from": "09:00", "to": "19:00"}
    assert cat.STIMA_LEAD_TIMEZONE == "Europe/Rome"
    assert 7 not in cat.FINESTRA_LAVORATIVA["days"], "la domenica non si manda"
    # Ogni passo porta la finestra, e una COPIA: due passi che condividessero
    # lo stesso dizionario si modificherebbero a vicenda.
    passi = cat._passi_completi()
    for p in passi:
        assert p["send_window"] == cat.FINESTRA_LAVORATIVA
    assert len({id(p["send_window"]) for p in passi}) == len(passi)
    assert passi[0]["send_window"] is not cat.FINESTRA_LAVORATIVA


def test_19_la_finestra_del_catalogo_e_accettata_dal_calcolo_della_finestra():
    """La finestra e' un dato, e un dato malformato si scopre al primo invio
    reale. Qui si scopre adesso: la si fa attraversare dalla funzione che la
    usera', su un istante di domenica, che e' il caso che il prodotto ha."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from communication import journey_catalog as cat, send_window
    roma = ZoneInfo(cat.STIMA_LEAD_TIMEZONE)
    domenica = datetime(2026, 9, 20, 11, 0, tzinfo=roma)
    quando = send_window.next_allowed(domenica, cat.FINESTRA_LAVORATIVA,
                                      cat.STIMA_LEAD_TIMEZONE)
    assert quando == datetime(2026, 9, 21, 9, 0, tzinfo=roma)
    # E il sabato, che in questa finestra e' un giorno di lavoro, non si sposta.
    sabato = datetime(2026, 9, 19, 11, 0, tzinfo=roma)
    assert send_window.next_allowed(sabato, cat.FINESTRA_LAVORATIVA,
                                    cat.STIMA_LEAD_TIMEZONE) == sabato


def test_20_nessun_passo_dichiara_condizioni_di_stop_proprie():
    """Le condizioni sono GLOBALI del motore - incarico, sopralluogo,
    consulenza, lead chiuso, contatto inattivo, consenso - e valgono per
    tutti i passi. Un elenco per passo darebbe l'impressione che ce ne siano
    altre, e che questo passo ne osservi solo alcune."""
    from communication import journey_catalog as cat
    for passo in cat._passi_completi():
        assert passo["stop_on"] == [], passo["step_key"]


def test_21_il_provisioning_NON_attiva_niente():
    """Preparare una sequenza e accenderla sono due gesti, e il secondo porta
    con se' il cutoff storico. Una funzione che li facesse insieme
    iscriverebbe contatti nel momento in cui qualcuno chiede di "creare" la
    journey per leggerla."""
    from communication import journey_catalog as cat
    codice = _codice(cat)
    for vietato in ("activate_journey", "JOURNEY_ACTIVE =", "set_status",
                    "UPDATE communication_journeys"):
        assert vietato not in codice, vietato
    sorgente = inspect.getsource(cat.ensure_stima_lead_v1)
    assert "activate" not in sorgente
    # E la firma dice che non spedisce e non attiva: restituisce cio' che
    # c'e' e dice se l'ha creato lei.
    assert "created" in sorgente


def test_22_nessuno_accende_la_sequenza_da_solo():
    """Nessun seed nella migration, nessuna chiamata al provisioning da un
    percorso che parte da se'. La journey nasce quando un'agenzia la chiede,
    e si accende quando qualcuno preme il secondo pulsante."""
    from communication import journey_catalog as cat
    migrazione = (ROOT / "migrations" / "071_p29_3_journey_automation.sql").read_text("utf-8")
    for vietato in ("INSERT INTO communication_journeys",
                    "INSERT INTO communication_journey_steps",
                    "stima_lead"):
        assert vietato not in migrazione, vietato
    # Chi nomina `ensure_stima_lead_v1`: la rotta che lo espone, il catalogo
    # che lo definisce, e i test. Nessun runner, nessuno script, nessun tick.
    chiamanti = set(_git("grep", "-l", "ensure_stima_lead_v1").split())
    chiamanti |= {p.name for p in ROOT.glob("*.py")
                  if "ensure_stima_lead_v1" in p.read_text("utf-8", errors="replace")}
    estranei = {c for c in chiamanti
                if not (c.startswith("tests/") or c in (
                    "communication/journey_catalog.py", "communication/router.py"))}
    assert estranei == set(), sorted(estranei)
    assert "ensure_stima_lead" not in _codice(__import__(
        "communication.journey_tick", fromlist=["x"]))
    assert cat.STIMA_LEAD_KEY == "stima_lead" and cat.STIMA_LEAD_VERSION == 1


# ===========================================================================
# C - LA TRADUZIONE: COSA ARRIVA A UNO SCHERMO
# ===========================================================================

#: Una riga del ledger com'e' davvero: con dentro tutte le colonne operative
#: che NON devono uscire. Se domani il ledger ne guadagna un'altra, il test
#: 23 la vedra' solo se qualcuno la aggiunge qui - ed e' per questo che il
#: test 24 controlla la whitelist dal verso opposto.
RIGA_LEDGER = {
    "id": 7, "agency_id": 3, "contact_id": 11, "stima_id": 4,
    "created_at": "2026-09-19T10:00:00Z", "scheduled_at": "2026-09-20T09:00:00Z",
    "sent_at": None, "channel": "email", "direction": "outbound",
    "communication_type": "marketing", "reason_code": "m2", "status": "queued",
    "mode": "automatic", "subject_snapshot": "Cosa cambia il valore",
    "rendered_body": "Ciao Anna,\n\n" + ("parola " * 60),
    "destination_snapshot": "anna@esempio.test",
    "template_key": "stima_lead_m2", "template_version": 1,
    "enrollment_id": 21, "step_no": 2, "run_no": 1,
    # LE COLONNE OPERATIVE.
    "claim_token": "5f0a-segreto", "claimed_at": "2026-09-20T09:00:01Z",
    "idempotency_key": "journey:21:2:1", "attempt_count": 3,
    "last_attempt_at": "2026-09-20T09:00:02Z",
    "last_error": "smtp: 550 mailbox unavailable",
    "provider": "smtp", "provider_message_id": "<abc@mx>",
    "metadata": {"pdf_url": "https://esempio.test/s/1"},
    "unsubscribe_token": "tok-segreto", "suppressed_reason": None,
    "updated_at": "2026-09-20T09:00:03Z",
}

OPERATIVE = ("claim_token", "claimed_at", "idempotency_key", "attempt_count",
             "last_attempt_at", "last_error", "provider", "provider_message_id",
             "metadata", "unsubscribe_token", "destination_snapshot", "agency_id")


def test_23_nessuna_colonna_operativa_esce_dall_API():
    """Non perche' siano segrete, ma perche' non dicono niente a chi legge
    una scheda contatto e, messe in una pagina, diventano la cosa che
    qualcuno un giorno incollera' in un ticket."""
    from communication import contact_view
    visibile = contact_view._messaggio_visibile(dict(RIGA_LEDGER))
    for campo in OPERATIVE:
        assert campo not in visibile, campo
    # E nemmeno i VALORI: un campo rinominato "note" che portasse il token
    # sarebbe la stessa fuga con un'etichetta diversa.
    testo = repr(visibile)
    for valore in ("5f0a-segreto", "tok-segreto", "journey:21:2:1",
                   "550 mailbox unavailable", "anna@esempio.test"):
        assert valore not in testo, valore


def test_24_la_whitelist_e_una_whitelist_e_non_una_blacklist():
    """Una colonna aggiunta domani al ledger non deve comparire per errore in
    una risposta HTTP. Lo si prova aggiungendone una alla riga: l'uscita non
    cambia di una chiave."""
    from communication import contact_view
    prima = set(contact_view._messaggio_visibile(dict(RIGA_LEDGER)))
    dopo = set(contact_view._messaggio_visibile(
        {**RIGA_LEDGER, "colonna_inventata_domani": "sorpresa",
         "second_claim_token": "anche-questo"}))
    assert prima == dopo
    sorgente = inspect.getsource(contact_view._messaggio_visibile)
    for vietato in ("**riga", "riga.items()", "dict(riga)", "{**"):
        assert vietato not in sorgente, vietato


def test_25_le_etichette_coprono_tutti_gli_stati_tutti_i_modi_tutti_i_motivi():
    """Un'etichetta mancante non rompe niente - il codice tecnico passa
    intero - ma mette `indeterminate` davanti a un agente immobiliare."""
    from communication import contact_view
    from communication.enums import MODES, REASON_CODES, STATUSES
    assert set(contact_view.ETICHETTE_STATO) == set(STATUSES)
    assert set(contact_view.ETICHETTE_MODO) == set(MODES)
    assert set(contact_view.ETICHETTE_MOTIVO) == set(REASON_CODES)
    for tabella in (contact_view.ETICHETTE_STATO, contact_view.ETICHETTE_MODO,
                    contact_view.ETICHETTE_MOTIVO):
        for chiave, etichetta in tabella.items():
            assert etichetta and etichetta.strip() == etichetta, chiave


def test_26_il_prossimo_invio_si_mostra_SOLO_se_il_messaggio_e_ancora_in_coda():
    """Su una riga gia' spedita `scheduled_at` e' storia: mostrarla come
    "prossimo invio" farebbe credere che partira' di nuovo."""
    from communication import contact_view
    in_coda = contact_view._messaggio_visibile({**RIGA_LEDGER, "status": "queued"})
    assert in_coda["next_send_at"] == RIGA_LEDGER["scheduled_at"]
    assert in_coda["can_cancel"] is True and in_coda["can_send_now"] is True
    for stato in ("sending", "sent", "failed", "indeterminate", "suppressed",
                  "cancelled"):
        riga = contact_view._messaggio_visibile({**RIGA_LEDGER, "status": stato})
        assert riga["next_send_at"] is None, stato
        assert riga["can_cancel"] is False and riga["can_send_now"] is False, stato
        # `scheduled_at` resta leggibile come data storica: non si nasconde,
        # si smette di chiamarla "prossimo invio".
        assert riga["scheduled_at"] == RIGA_LEDGER["scheduled_at"], stato


def test_27_l_anteprima_e_corta_e_su_una_riga_sola():
    from communication import contact_view
    riga = contact_view._messaggio_visibile(dict(RIGA_LEDGER))
    assert len(riga["preview"]) <= contact_view.ANTEPRIMA
    assert "\n" not in riga["preview"]
    assert riga["preview"].startswith("Ciao Anna,")
    vuoto = contact_view._messaggio_visibile({**RIGA_LEDGER, "rendered_body": None})
    assert vuoto["preview"] == ""


def test_28_la_nota_sull_inbound_dice_cio_che_sa_e_non_cio_che_non_sa():
    """Le risposte in arrivo non sono modellate, quindi la pagina NON puo'
    dire "nessuna risposta": non lo sa, e scriverlo sarebbe
    un'informazione falsa travestita da interfaccia vuota."""
    from communication import contact_view
    nota = contact_view.NOTA_INBOUND
    assert "non sono ancora sincronizzate" in nota
    basso = nota.lower()
    for vietata in ("nessuna risposta", "non ha risposto", "nessun riscontro"):
        assert vietata not in basso, vietata
    # E la nota accompagna sempre lo storico, non e' un extra opzionale.
    assert "inbound_note" in inspect.getsource(contact_view.messages)


# ===========================================================================
# D - L'INTERFACCIA
# ===========================================================================

COMPONENTE = STATICI / "components" / "communications.js"
SCHEDA = STATICI / "views" / "contatto-dettaglio.js"


def _js(percorso: Path) -> str:
    """Il codice, senza i commenti: le sentinelle qui sotto leggono cio' che
    il browser esegue, non cio' che le righe `//` spiegano."""
    testo = percorso.read_text(encoding="utf-8")
    testo = re.sub(r"/\*[\s\S]*?\*/", "", testo)
    return re.sub(r"(?m)^\s*//[^\n]*$", "", testo)


def test_29_la_tab_comunicazioni_e_registrata_nella_scheda_contatto():
    codice = _js(SCHEDA)
    assert "mountCommunications" in codice
    assert "'comunicazioni'" in codice
    assert "components/communications.js" in codice
    assert "Comunicazioni" in codice
    assert COMPONENTE.exists()


def test_30_il_componente_non_nomina_nessuna_colonna_operativa():
    codice = _js(COMPONENTE)
    for vietato in ("claim_token", "claimToken", "idempotency", "idempotenc",
                    "attempt_count", "last_error", "provider_message_id",
                    "destination_snapshot", "agency_id"):
        assert vietato not in codice, vietato


def test_31_il_form_manuale_non_manda_il_destinatario_ne_l_agenzia():
    """Accettare un indirizzo dal browser vorrebbe dire permettere di mandare
    la posta di un'agenzia a chiunque. Il corpo che parte ha tre campi."""
    codice = _js(COMPONENTE)
    assert "apiPost(`/api/communication/contacts/${contactId}/messages`" in codice
    corpo = codice.split("apiPost(`/api/communication/contacts/${contactId}/messages`", 1)[-1]
    corpo = corpo.split("});", 1)[0]
    for campo in ("subject", "body", "communication_type"):
        assert campo in corpo, campo
    for vietato in ("destination", "email", "agency", "to:"):
        assert vietato not in corpo, vietato


def test_32_la_pagina_ha_uno_stato_per_ogni_cosa_che_puo_mancare():
    """Caricamento, errore, storico vuoto, journey assente: quattro schermate
    che esistono davvero. Una pagina bianca al posto di una di queste e' il
    motivo per cui qualcuno apre un ticket."""
    codice = _js(COMPONENTE)
    assert "'loading'" in codice and "'error'" in codice
    assert "Nessun messaggio inviato o programmato" in codice
    assert "Nessuna automazione in corso" in codice
    assert "available === false" in codice
    assert "Automazioni non disponibili" in codice


def test_33_senza_la_071_lo_storico_si_vede_lo_stesso():
    """Lo storico esiste dalla 064 e non dipende dalle journey: la lettura
    della journey ha il suo `catch`, e non porta giu' la pagina con se'."""
    codice = _js(COMPONENTE)
    assert "available: false" in codice
    caricamento = codice.split("export async function loadCommunications", 1)[-1]
    caricamento = caricamento.split("export ", 1)[0]
    assert ".catch(" in caricamento
    # Il `catch` sta sulla journey, non sullo storico.
    riga_journey = [r for r in caricamento.splitlines() if "/journey`" in r]
    assert riga_journey and ".catch(" in riga_journey[0], riga_journey


def test_34_la_nota_sull_inbound_arriva_dall_API_e_non_e_riscritta_nel_browser():
    """Una frase scritta due volte e' una frase che un giorno divergera'."""
    from communication import contact_view
    codice = _js(COMPONENTE)
    assert "inboundNote" in codice and "inbound_note" in codice
    assert contact_view.NOTA_INBOUND not in codice
    for vietata in ("nessuna risposta", "Nessuna risposta"):
        assert vietata not in codice, vietata


# ===========================================================================
# E - IL PERIMETRO DELLA FASE
# ===========================================================================

def _git(*argomenti) -> str:
    return subprocess.run(["git", "--no-optional-locks", *argomenti],
                          cwd=ROOT, capture_output=True, text=True).stdout.strip()


def _git_righe(*argomenti) -> list[str]:
    """Come `_git`, ma SENZA togliere gli spazi in testa: in `git status
    --porcelain` i primi due caratteri sono lo stato."""
    return subprocess.run(["git", "--no-optional-locks", *argomenti],
                          cwd=ROOT, capture_output=True, text=True).stdout.splitlines()


def test_35_nessun_cron_nuovo_e_il_cron_non_accende_la_sequenza():
    """SENTINELLA AGGIORNATA DA P29-3E (collisione dichiarata).

    Il wiring tick -> dispatch e' la fase P29-3E, ed e' stato fatto li'. Cio'
    che questa sentinella difendeva e che resta vero: di cron ce n'e' UNO, e
    non provisiona e non attiva la sequenza della stima. Il collegamento al
    motore e' un'altra cosa dall'accendere una sequenza commerciale, e la
    seconda resta un gesto che fa una persona.
    """
    from tests.p29_3e_diff import RUNNER_TOCCATO

    toccati = {r[3:].strip() for r in _git_righe("status", "--porcelain", "--", "run_*.py")}
    assert toccati <= {RUNNER_TOCCATO}, sorted(toccati)
    assert not list(ROOT.glob("run_journey*.py"))
    corrente = re.sub(r'"{3}[\s\S]*?"{3}', "",
                      (ROOT / RUNNER_TOCCATO).read_text(encoding="utf-8"))
    corrente = re.sub(r"#[^\n]*", "", corrente)
    for vietato in ("stima_lead", "ensure_stima_lead", "/provision", "/activate"):
        assert vietato not in corrente, vietato


def test_36_i_moduli_nuovi_non_toccano_la_rete_e_non_spediscono():
    """"Invia ora" sposta una data; il messaggio parte quando il dispatcher
    lo prende. Nessuno di questi moduli conosce un provider."""
    from communication import contact_view, journey_catalog
    for modulo in (journey_catalog, contact_view):
        codice = _codice(modulo)
        for vietato in ("providers", "smtp", "requests", "httpx", "urllib",
                        "socket", "invia_mail", "dispatch_batch", "adapter_per",
                        "send_email"):
            assert vietato not in codice, (modulo.__name__, vietato)
    from communication import service as comm_service
    da_service = re.sub(r'"{3}[\s\S]*?"{3}', "",
                        inspect.getsource(comm_service.send_now))
    da_service = re.sub(r"#[^\n]*", "", da_service).lower()
    for vietato in ("provider", "smtp", "dispatch", "adapter", "invia"):
        assert vietato not in da_service, vietato
    # Cio' che fa e' una cosa sola: rimettere in coda una riga in coda.
    assert "reschedule_queued" in da_service


def test_37_nessuno_dei_moduli_nuovi_apre_una_connessione():
    """Le connessioni restano nel choke point: questi moduli ricevono
    cursori, o li chiedono al cursore di dominio gia' esistente."""
    aghi = ("get_" + "connection", "psycopg2" + ".connect")
    for nome in ("journey_catalog", "contact_view"):
        testo = (PACCHETTO / f"{nome}.py").read_text(encoding="utf-8")
        for ago in aghi:
            assert ago not in testo, (nome, ago)


def test_38_le_rotte_nuove_non_accettano_l_agenzia_dal_client():
    """L'attore e lo scope vengono dalla sessione. Un `agency_id` nel corpo o
    nel path e' una rotta che si puo' puntare altrove."""
    from communication import schemas
    campi = set(schemas.ManualMessageRequest.model_fields)
    assert campi == {"subject", "body", "communication_type"}, sorted(campi)
    # E un campo in piu' e' un errore, non un campo ignorato: chi prova a
    # mandare `agency_id` deve sentirselo dire.
    with pytest.raises(Exception):
        schemas.ManualMessageRequest(subject="x", body="y", agency_id=9)
    with pytest.raises(Exception):
        schemas.ManualMessageRequest(subject="x", body="y",
                                     destination_snapshot="chiunque@esempio.test")
    rotte = (PACCHETTO / "router.py").read_text(encoding="utf-8")
    assert "{agency_id}" not in rotte


def test_39_i_file_toccati_sono_quelli_dichiarati_e_P29_2_0_resta_fuori():
    from tests.p29_3c_diff import FILE_MODIFICATI as MOD_3C, FILE_NUOVI as NUOVI_3C
    from tests.p29_3d_diff import FILE_MODIFICATI, FILE_NUOVI
    # P29-3E si dichiara allo stesso modo: l'unione cresce di una fase.
    # P29-3G si dichiara allo stesso modo: l'unione cresce di una fase,
    # il verso del controllo no.
    from tests.p29_3e_diff import FILE_MODIFICATI as MOD_3E, FILE_NUOVI as NUOVI_3E
    from tests.p29_3g_diff import FILE_MODIFICATI as MOD_3G, FILE_NUOVI as NUOVI_3G
    # FLOW GLOBAL SECURITY si dichiara allo stesso modo: l'unione cresce
    # di una fase, il verso del controllo no. Non e' una fase di P29 - e'
    # il catalogo globale di FLOW - ma questa sentinella guarda il working
    # tree intero, quindi la collisione c'e' e va nominata.
    from tests.flow_global_security_diff import (
        FILE_MODIFICATI as MOD_FGS, FILE_NUOVI as NUOVI_FGS)

    righe = _git_righe("status", "--porcelain")
    nuovi = {r[3:].strip() for r in righe if r[:2].strip() in ("??", "A")}
    modificati = {r[3:].strip() for r in righe if r[:2].strip() not in ("??", "A")}
    tracciati = set(_git("ls-files").split())

    # Nel working tree non c'e' NIENTE che nessuna delle due fasi abbia
    # dichiarato - tranne il documento di design, che resta fuori apposta.
    tutti_nuovi = FILE_NUOVI | NUOVI_3C | NUOVI_3E | NUOVI_3G | NUOVI_FGS
    tutti_modificati = (FILE_MODIFICATI | MOD_3C | MOD_3E | MOD_3G | MOD_FGS
                        | tutti_nuovi)
    assert nuovi - tutti_nuovi == {"P29_2_0_COMMUNICATION_DESIGN.md"}, \
        sorted(nuovi - tutti_nuovi)
    assert modificati <= tutti_modificati, sorted(modificati - tutti_modificati)
    # NOTA DI P29-3E: finche' P29-3D era in corso, qui si pretendeva anche
    # che ogni voce dichiarata da quella fase fosse VERAMENTE modificata nel
    # working tree. Dopo il suo commit non c'e' piu' un working tree da
    # guardare, e la garanzia si sposta su cio' che resta vero per sempre:
    # ogni file dichiarato esiste ed e' nell'indice.
    for nome in FILE_NUOVI:
        assert (ROOT / nome).exists(), nome
        assert nome in tracciati, nome
    for nome in FILE_MODIFICATI:
        assert nome in tracciati, nome
    assert "P29_2_0_COMMUNICATION_DESIGN.md" not in tracciati
    assert (ROOT / "P29_2_0_COMMUNICATION_DESIGN.md").exists()


def test_40_il_documento_di_design_non_e_stato_toccato():
    percorso = ROOT / "P29_2_0_COMMUNICATION_DESIGN.md"
    impronta = hashlib.md5(percorso.read_bytes()).hexdigest()
    assert impronta == "37b3066fc5cf41b905964e1f31fe93f4", impronta


def test_41_il_componente_e_un_modulo_javascript_VALIDO():
    """Una sentinella nata da un errore vero: una virgoletta sfuggita in una
    stringa aveva reso il file impossibile da compilare, e la Shell INTERA
    non partiva piu' - non la tab, la Shell, perche' un modulo che non
    compila porta giu' l'import che lo nomina. Una sentinella che legge il
    testo del file non se ne accorge; questa lo fa compilare davvero.
    """
    import shutil

    node = shutil.which("node")
    if node is None:  # pragma: no cover - ambiente senza node
        pytest.skip("node non e' installato in questo ambiente")
    driver = (
        "const fs=require('fs'), vm=require('vm');"
        "const s=fs.readFileSync(process.argv[1],'utf8');"
        "try{ new vm.SourceTextModule(s); console.log('OK'); }"
        "catch(e){ console.log('ERRORE: '+e.message); }"
    )
    esito = subprocess.run(
        [node, "--experimental-vm-modules", "-e", driver, str(COMPONENTE)],
        capture_output=True, text=True, cwd=ROOT)
    assert "OK" in esito.stdout, (esito.stdout, esito.stderr)
