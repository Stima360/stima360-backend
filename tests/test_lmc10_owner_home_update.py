"""LMC-10 - "Aggiorna la tua casa": la parte che si prova senza database.

TRE DOMANDE, E OGNI TEST QUI SOTTO RISPONDE A UNA DI QUESTE.

La prima: la somma e' giusta? ORIGINALE + OVERRIDE = PROFILO EFFETTIVO e'
una frase corta, e il modo di sbagliarla e' trattare `None` come "cancella"
oppure lasciare entrare un campo che il proprietario non puo' toccare.

La seconda: le tre whitelist coincidono? Le colonne della migration 068, la
tupla di `home_profile.OVERRIDABLE_FIELDS` e i campi dello schema pydantic
dicono la stessa cosa in tre posti. Tre elenchi che devono coincidere e che
nessuno confronta prima o poi non coincidono.

La terza: una modifica che non modifica e' davvero un nulla di fatto? E'
la domanda piu' facile da sbagliare, perche' due valori diversi alla lettera
possono significare la stessa cosa ("True" e "true", "garage,cantina" e
"cantina, garage") e ognuna di quelle differenze farebbe nascere una
versione nuova, un evento sul radar e forse uno snapshot - cioe' farebbe
richiamare qualcuno che ha solo riaperto il form.

La concorrenza, la tenancy e il motore stanno nel file PostgreSQL: qui non
si finge di provarli con un doppio finto.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
from pathlib import Path

import pytest

import home_profile
from owner import home_service, home_update, interest_service, tracking
from owner.schemas import HomeProfileUpdate

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONE = ROOT / "migrations" / "068_lmc10_owner_home_overrides.sql"
MIGRAZIONE_DOWN = ROOT / "migrations" / "068_lmc10_owner_home_overrides_down.sql"


def stima(**over):
    base = {"id": 501, "agency_id": 7, "comune": "Alba Adriatica",
            "microzona": "Villa Fiore", "via": "Via Trieste", "civico": "12",
            "tipologia": "Appartamento", "mq": 95, "piano": "3", "locali": 4,
            "bagni": 2, "pertinenze": "garage, cantina", "ascensore": "True",
            "anno": 1998, "stato": "buono", "vistamareyn": "si",
            "distanzamare": "0-100", "altrodescrizione": "ristrutturato",
            "mqgiardino": 0, "mqgarage": 18, "mqcantina": 6, "mqpostoauto": 0,
            "mqtaverna": 0, "mqsoffitta": 0, "mqterrazzo": 12, "numbalconi": 2,
            "data": "2026-05-02T10:00:00+00:00"}
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# A - il composer: ORIGINALE + OVERRIDE = PROFILO EFFETTIVO
# ---------------------------------------------------------------------------

def test_a1_senza_override_il_profilo_effettivo_e_l_originale():
    profilo = home_profile.build_effective_home_profile(stima(), None)
    assert profilo["home"] == stima()
    assert profilo["overridden_fields"] == ()
    assert profilo["version"] == home_profile.NO_OVERRIDE_VERSION == 0
    assert profilo["updated_at"] is None


def test_a2_l_override_vince_e_solo_dove_c_e():
    profilo = home_profile.build_effective_home_profile(
        stima(), {"mq": 110, "bagni": None, "version": 3})
    assert profilo["home"]["mq"] == 110
    assert profilo["home"]["bagni"] == 2, "un override nullo non cancella"
    assert profilo["overridden_fields"] == ("mq",)
    assert profilo["version"] == 3


def test_a3_l_originale_non_viene_modificato():
    """La fotografia della valutazione non si tocca, nemmeno in memoria."""
    originale = stima()
    home_profile.build_effective_home_profile(originale, {"mq": 110})
    assert originale["mq"] == 95


def test_a4_una_stringa_vuota_non_e_un_override():
    profilo = home_profile.build_effective_home_profile(
        stima(), {"altrodescrizione": "   "})
    assert profilo["home"]["altrodescrizione"] == "ristrutturato"
    assert profilo["overridden_fields"] == ()


def test_a5_un_campo_fuori_whitelist_non_entra_mai():
    """Anche se la riga di override lo portasse: e' la colonna che non esiste,
    ma il composer non deve poter essere la strada di servizio."""
    profilo = home_profile.build_effective_home_profile(
        stima(), {"comune": "Milano", "microzona": "Brera", "tipologia": "Villa",
                  "via": "Via Altra", "civico": "1", "agency_id": 99})
    for campo in ("comune", "microzona", "tipologia", "via", "civico"):
        assert profilo["home"][campo] == stima()[campo], campo
    assert profilo["home"]["agency_id"] == 7


def test_a6_il_composer_e_puro():
    """Niente database, niente orologio, niente import di dominio.

    Si guarda il CODICE con l'AST, non la prosa: le stringhe vengono
    svuotate prima di cercare, altrimenti basterebbe la parola "cursor" in
    un commento per far fallire (o passare) il test per il motivo sbagliato.
    """
    albero = ast.parse(MIGRAZIONE.with_suffix(".sql").read_text(encoding="utf-8")
                       and inspect.getsource(home_profile))
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            nodo.value = ""
    codice = ast.unparse(albero)
    for vietato in ("cursor", "connect", "execute", "datetime", "now",
                    "owner", "property_watch", "crm"):
        assert vietato not in codice.lower(), vietato


def test_a7_due_chiamate_uguali_danno_lo_stesso_risultato():
    a = home_profile.build_effective_home_profile(stima(), {"mq": 110, "version": 2})
    b = home_profile.build_effective_home_profile(stima(), {"mq": 110, "version": 2})
    assert a == b


# ---------------------------------------------------------------------------
# B - LE TRE WHITELIST COINCIDONO (test 51 compreso)
# ---------------------------------------------------------------------------

def _colonne_migration():
    """Le colonne dichiarate nella CREATE TABLE della 068, col loro tipo."""
    testo = MIGRAZIONE.read_text(encoding="utf-8")
    corpo = testo[testo.index("CREATE TABLE IF NOT EXISTS owner_home_overrides"):]
    corpo = corpo[corpo.index("(") + 1:corpo.index("\n);")]
    colonne = {}
    for riga in corpo.splitlines():
        riga = riga.split("--")[0].strip().rstrip(",")
        if not riga or riga.upper().startswith(("CONSTRAINT", "UNIQUE", "CHECK")):
            continue
        pezzi = riga.split()
        if len(pezzi) < 2:
            continue
        colonne[pezzi[0]] = pezzi[1].upper()
    return colonne


def test_b1_la_migration_ha_una_colonna_per_ogni_campo_modificabile():
    colonne = _colonne_migration()
    for campo in home_profile.OVERRIDABLE_FIELDS:
        assert campo in colonne, campo
    servizio = set(home_profile.OVERRIDE_METADATA_FIELDS)
    dati = set(colonne) - servizio
    assert dati == set(home_profile.OVERRIDABLE_FIELDS), dati ^ set(home_profile.OVERRIDABLE_FIELDS)


def test_b2_lo_schema_api_espone_esattamente_la_whitelist():
    campi = set(HomeProfileUpdate.model_fields) - {"expected_version"}
    assert campi == set(home_profile.OVERRIDABLE_FIELDS), campi ^ set(home_profile.OVERRIDABLE_FIELDS)


def test_b3_i_campi_vietati_non_hanno_colonna_ne_campo_api():
    vietati = ("comune", "microzona", "via", "civico", "tipologia",
               "posizionemare", "distanzamare", "barrieramare", "vistamareyn",
               "vistamare", "vistamaredettaglio", "agency_id", "contact_id",
               "lead_id", "owner_account_id", "token", "prezzo_mq_base",
               "lead_status", "note_internal", "nome", "cognome", "email",
               "telefono", "data")
    colonne = _colonne_migration()
    for campo in vietati:
        assert campo not in colonne, f"colonna vietata nella 068: {campo}"
        assert campo not in HomeProfileUpdate.model_fields, f"campo API vietato: {campo}"
        assert campo not in home_profile.OVERRIDABLE_FIELDS, campo


def test_b4_il_motore_riceve_gli_stessi_campi():
    from property_watch import repository as pw_repository
    assert set(pw_repository.VALUATION_OVERRIDE_COLUMNS) == set(home_profile.OVERRIDABLE_FIELDS)


def test_51_i_tipi_della_migration_combaciano_con_stime():
    """TEST 51 - I TIPI NON SI INDOVINANO.

    La sorgente e' `database.py`, che e' dove `stime` nasce davvero: si
    legge la sua CREATE TABLE e le sue ALTER, si estrae il tipo dichiarato
    per ogni campo modificabile e lo si confronta con la colonna omonima
    della 068.

    Il campo che questo test esiste per sorvegliare e' `locali`: in
    `stime` e' INTEGER, ma nella tabella VICINA `stime_dettagliate` la
    stessa parola e' VARCHAR(50), e copiare dalla tabella sbagliata
    darebbe un override che accetta "quattro" e un motore che legge zero.

    Il controllo a runtime, su `information_schema`, e' nel file PostgreSQL:
    quello vede il database vero, questo vede la sorgente della verita'.
    """
    sorgente = (ROOT / "database.py").read_text(encoding="utf-8")
    inizio = sorgente.index("CREATE TABLE IF NOT EXISTS stime (")
    create = sorgente[inizio:sorgente.index(");", inizio)]
    # Le ALTER di `stime`, escluse quelle di `stime_dettagliate`.
    alter = ""
    for m in re.finditer(r"ALTER TABLE stime\b(?!_dettagliate)(.*?);", sorgente, re.S):
        alter += m.group(1)

    def tipo_dichiarato(campo: str) -> str:
        for blocco in (create, alter):
            trovato = re.search(rf"\b{campo}\s+([A-Za-z]+(?:\(\d+\))?)",
                                blocco, re.I)
            if trovato:
                return trovato.group(1).upper()
        raise AssertionError(f"{campo} non dichiarato in database.py")

    mie = _colonne_migration()
    righe = []
    for campo in home_profile.OVERRIDABLE_FIELDS:
        suo = tipo_dichiarato(campo)
        mio = mie[campo]
        righe.append((campo, suo, mio))
        assert mio == suo, f"{campo}: stime={suo} owner_home_overrides={mio}"
    assert ("locali", "INTEGER", "INTEGER") in righe, righe


def test_52_la_down_rifiuta_se_ci_sono_dati():
    """TEST 52 - il testo della DOWN contiene il controllo prima del DROP.

    La prova vera e' su PostgreSQL (il rollback eseguito davvero); qui si
    sorveglia che il controllo non venga tolto dal file: `RAISE EXCEPTION`
    deve comparire PRIMA del primo `DROP`, altrimenti sarebbe un avviso
    dopo il funerale.
    """
    testo = MIGRAZIONE_DOWN.read_text(encoding="utf-8")
    assert testo.index("RAISE EXCEPTION") < testo.index("DROP "), \
        "il controllo deve precedere il DROP"
    assert "count(*) INTO v_righe FROM owner_home_overrides" in testo
    corpo = testo[testo.index("BEGIN;"):]
    assert "DELETE FROM owner_home_overrides" not in corpo, \
        "la down non deve svuotare la tabella per poterla cancellare"


# ---------------------------------------------------------------------------
# C - la validazione della patch
# ---------------------------------------------------------------------------

def _normalizza(patch, effettivo=None, originale=None):
    base = stima()
    return home_update.normalize_patch(patch, effettivo=effettivo or base,
                                       originale=originale or base)


def test_c1_una_chiave_fuori_whitelist_e_rifiutata():
    for chiave in ("comune", "microzona", "agency_id", "tipologia", "prezzo_mq_base"):
        with pytest.raises(home_update.InvalidHomeUpdate) as errore:
            _normalizza({chiave: "x"})
        assert chiave in errore.value.fields


def test_c2_una_patch_vuota_e_rifiutata():
    with pytest.raises(home_update.InvalidHomeUpdate):
        _normalizza({})


def test_c3_null_non_significa_cancella():
    with pytest.raises(home_update.InvalidHomeUpdate) as errore:
        _normalizza({"mq": None})
    assert errore.value.fields == ("mq",)


def test_c4_i_numeri_hanno_limiti_e_i_booleani_non_passano_per_numeri():
    assert _normalizza({"mq": 110})["mq"] == 110
    for valore in (0, -5, 10001, True, "novantacinque", 1.5):
        with pytest.raises(home_update.InvalidHomeUpdate):
            _normalizza({"mq": valore})


def test_c5_ascensore_torna_nella_forma_che_il_motore_legge():
    import valuation
    assert _normalizza({"ascensore": True})["ascensore"] == "True"
    assert _normalizza({"ascensore": False})["ascensore"] == "False"
    # E il motore deve davvero riconoscerla come "c'e' l'ascensore".
    assert valuation.coeff_ascensore("True", "3") > 1.0
    assert valuation.coeff_ascensore("False", "3") == 1.0


def test_c6_stato_e_un_insieme_chiuso_e_sono_quelli_del_motore():
    import valuation
    for valore in home_update.STATO_VALORI:
        assert _normalizza({"stato": valore})["stato"] == valore
    # Ognuno di essi e' un valore che il motore distingue davvero: se uno
    # non lo fosse, il proprietario sceglierebbe una casella senza effetto.
    coefficienti = {v: valuation.coeff_stato(v) for v in home_update.STATO_VALORI}
    assert len(set(coefficienti.values())) > 1, coefficienti
    with pytest.raises(home_update.InvalidHomeUpdate):
        _normalizza({"stato": "abitabile"})


def test_c7_rimandare_indietro_il_valore_corrente_non_e_un_errore():
    """Una casa nata con uno `stato` o un `piano` fuori dall'insieme chiuso
    deve poter restare com'e': il form rispedisce quel valore finche' il
    proprietario non ne sceglie un altro, e rifiutarlo renderebbe la casa
    non salvabile per un campo che nessuno ha toccato."""
    corrente = stima(stato="abitabile", piano="attico")
    assert _normalizza({"stato": "abitabile"}, effettivo=corrente)["stato"] == "abitabile"
    assert _normalizza({"piano": "attico"}, effettivo=corrente)["piano"] == "attico"
    # Ma un valore inventato diverso da quello corrente resta rifiutato.
    with pytest.raises(home_update.InvalidHomeUpdate):
        _normalizza({"stato": "semi-abitabile"}, effettivo=corrente)


def test_c8_piano_accetta_le_parole_del_motore_e_i_numeri():
    import valuation
    assert _normalizza({"piano": "terra"})["piano"] == "terra"
    assert _normalizza({"piano": "ultimo"})["piano"] == "ultimo"
    assert _normalizza({"piano": "4"})["piano"] == "4"
    assert valuation._parse_piano("terra")[0] == "terra"
    assert valuation._parse_piano("ultimo")[0] == "ultimo"
    assert valuation._parse_piano("4") == ("numero", 4)
    with pytest.raises(home_update.InvalidHomeUpdate):
        _normalizza({"piano": "999"})


def test_c9_altrodescrizione_non_puo_essere_vuota_ne_infinita():
    assert _normalizza({"altrodescrizione": "  nuovo bagno "})["altrodescrizione"] == "nuovo bagno"
    for valore in ("", "   ", "x" * (home_update.ALTRODESCRIZIONE_MAX + 1)):
        with pytest.raises(home_update.InvalidHomeUpdate):
            _normalizza({"altrodescrizione": valore})


# ---------------------------------------------------------------------------
# D - LE PERTINENZE (punto 7): token, non testo libero
# ---------------------------------------------------------------------------

def test_d1_i_token_sono_quelli_che_il_motore_riconosce_davvero():
    """L'elenco non e' una convenzione: sono le parole che
    `valuation.compute_from_payload` e `valore_pertinenze` cercano. Una in
    piu' sarebbe una casella senza effetto, una in meno una pertinenza che
    il proprietario non puo' dichiarare pur valendo dei soldi."""
    import valuation
    sorgente = inspect.getsource(valuation.compute_from_payload) + \
        inspect.getsource(valuation.valore_pertinenze)
    for token in home_profile.PERTINENZE_TOKENS:
        assert f'"{token}"' in sorgente, token


def test_65_un_token_sconosciuto_e_rifiutato():
    """TEST 65."""
    for sconosciuto in (["box auto"], ["garage", "elicottero"], ["GARAGE ", 3],
                        "garage"):
        with pytest.raises(home_update.InvalidHomeUpdate):
            _normalizza({"pertinenze": sconosciuto})


def test_d2_aggiungere_il_garage():
    corrente = stima(pertinenze="cantina")
    valore = _normalizza({"pertinenze": ["cantina", "garage"]},
                         effettivo=corrente, originale=corrente)["pertinenze"]
    assert home_profile.parse_pertinenze(valore) == ("garage", "cantina")


def test_66_rimuovere_il_garage_e_coerente():
    """TEST 66 - togliere una pertinenza la toglie davvero, e solo quella."""
    corrente = stima(pertinenze="garage, cantina, terrazzo")
    valore = _normalizza({"pertinenze": ["cantina", "terrazzo"]},
                         effettivo=corrente, originale=corrente)["pertinenze"]
    token = home_profile.parse_pertinenze(valore)
    assert "garage" not in token
    assert token == ("cantina", "terrazzo")


def test_d3_la_lista_vuota_e_un_override_esplicito():
    """TEST 1 e 2 del fix finale - "questa casa non ha nessuna pertinenza".

    La versione precedente di questo test rifiutava la lista vuota, e la
    ragione era reale: una stringa vuota letta come "campo non corretto"
    avrebbe fatto riaffiorare le pertinenze dell'originale. La risposta
    giusta pero' non era rifiutare il gesto - era dare alla stringa vuota un
    significato, che e' quello che `EMPTY_MEANS_OVERRIDE` fa adesso. Senza,
    una stima nata con un garage che non esiste non sarebbe correggibile.
    """
    corrente = stima(pertinenze="garage")
    valore = _normalizza({"pertinenze": []},
                         effettivo=corrente, originale=corrente)["pertinenze"]
    assert valore == "", repr(valore)
    # E la distinzione vale SOLO per le pertinenze.
    assert home_profile.EMPTY_MEANS_OVERRIDE == frozenset({"pertinenze"})
    for campo in ("altrodescrizione", "piano", "stato", "ascensore"):
        assert campo not in home_profile.EMPTY_MEANS_OVERRIDE, campo


def test_d3b_la_stringa_vuota_non_ricade_sull_originale():
    """TEST 3 - il composer distingue `None` da `''`."""
    originale = stima(pertinenze="garage")
    assert home_profile.build_effective_home_profile(
        originale, {"pertinenze": None})["home"]["pertinenze"] == "garage"
    effettivo = home_profile.build_effective_home_profile(
        originale, {"pertinenze": "", "version": 1})
    assert effettivo["home"]["pertinenze"] == ""
    assert effettivo["overridden_fields"] == ("pertinenze",)


def test_d3c_per_gli_altri_campi_la_stringa_vuota_resta_nessun_override():
    """TEST 10, dal verso opposto: la semantica generale non e' cambiata."""
    originale = stima(altrodescrizione="ristrutturato", piano="3")
    effettivo = home_profile.build_effective_home_profile(
        originale, {"altrodescrizione": "", "piano": "   ", "version": 1})["home"]
    assert effettivo["altrodescrizione"] == "ristrutturato"
    assert effettivo["piano"] == "3"


def test_d3d_niente_pertinenze_e_gia_niente_pertinenze_e_un_no_op():
    """TEST 9, sulla regola di equivalenza."""
    assert home_update._equivalente("pertinenze", "", "")
    assert home_update._equivalente("pertinenze", "", None)
    assert not home_update._equivalente("pertinenze", "", "garage")


def test_d3e_dopo_la_lista_vuota_si_puo_aggiungere_di_nuovo():
    """TEST 12."""
    originale = stima(pertinenze="garage")
    svuotato = home_profile.build_effective_home_profile(
        originale, {"pertinenze": "", "version": 1})["home"]
    valore = _normalizza({"pertinenze": ["cantina"]},
                         effettivo=svuotato, originale=originale)["pertinenze"]
    assert home_profile.parse_pertinenze(valore) == ("cantina",)
    assert "garage" not in valore


def test_d4_gli_extra_dell_originale_non_si_perdono():
    """"box auto" non e' modificabile dal proprietario perche' il motore non
    lo conosce, ma qualcuno l'ha scritto: farlo sparire al primo salvataggio
    sarebbe cancellare il lavoro di un operatore senza dirlo."""
    corrente = stima(pertinenze="garage, box auto")
    valore = _normalizza({"pertinenze": ["garage", "cantina"]},
                         effettivo=corrente, originale=corrente)["pertinenze"]
    assert "box auto" in valore
    assert home_profile.extra_pertinenze(valore) == ("box auto",)


def test_d5_la_serializzazione_e_deterministica():
    a = home_profile.serialize_pertinenze(["cantina", "garage"])
    b = home_profile.serialize_pertinenze(["garage", "cantina"])
    assert a == b == "garage, cantina"


def test_d6_il_motore_legge_davvero_il_valore_serializzato():
    """Il giro completo: token -> stringa -> motore. Se la serializzazione
    usasse un separatore che il motore non normalizza, la pertinenza
    varrebbe zero e nessuno se ne accorgerebbe."""
    import valuation
    senza = valuation.compute_from_payload({
        "comune": "Alba Adriatica", "microzona": "Villa Fiore", "mq": 95,
        "tipologia": "Appartamento", "pertinenze": ""})
    con = valuation.compute_from_payload({
        "comune": "Alba Adriatica", "microzona": "Villa Fiore", "mq": 95,
        "tipologia": "Appartamento",
        "pertinenze": home_profile.serialize_pertinenze(["garage", "cantina"]),
        "mqGarage": 18, "mqCantina": 6})
    assert con["price_exact"] > senza["price_exact"]


# ---------------------------------------------------------------------------
# E - IL NO-OP (punto 6): una modifica che non modifica non e' un segnale
# ---------------------------------------------------------------------------

def test_e1_stessi_valori_alla_lettera_sono_equivalenti():
    assert home_update._equivalente("mq", 95, 95)
    assert not home_update._equivalente("mq", 95, 110)


def test_e2_ascensore_si_confronta_per_significato():
    """"True" e "true" dicono la stessa cosa: contarle come una modifica
    farebbe nascere una versione, un evento e forse uno snapshot per una
    differenza di maiuscola scritta anni fa dal funnel."""
    assert home_update._equivalente("ascensore", "True", "true")
    assert home_update._equivalente("ascensore", "True", "si")
    assert not home_update._equivalente("ascensore", "True", "False")


def test_e3_le_pertinenze_si_confrontano_per_insieme():
    assert home_update._equivalente("pertinenze", "garage, cantina", "cantina,garage")
    assert home_update._equivalente("pertinenze", "garage, cantina", "Garage;  Cantina")
    assert not home_update._equivalente("pertinenze", "garage", "garage, cantina")


def test_e4_il_testo_si_confronta_ripulito_dagli_spazi():
    assert home_update._equivalente("altrodescrizione", "ristrutturato", " ristrutturato ")


# ---------------------------------------------------------------------------
# F - il read-model: `profile_version` e cio' che il form riceve
# ---------------------------------------------------------------------------

def _vista(override=None):
    return home_service.build_home_detail(
        stima=stima(), watch=None, observations=[], baseline_payload=None,
        completed_payload=None, overrides=override)


def test_54_profile_version_e_zero_senza_override():
    """TEST 54."""
    vista = _vista()
    assert vista["profile_version"] == 0
    assert vista["profile"]["version"] == 0
    assert vista["profile"]["overridden_fields"] == []
    assert vista["profile"]["updated_at"] is None


def test_f1_con_override_la_versione_e_quella_della_riga():
    vista = _vista({"mq": 110, "version": 4, "updated_at": "2026-09-19T10:00:00+00:00"})
    assert vista["profile_version"] == 4
    assert vista["property"]["mq"] == 110
    assert vista["profile"]["overridden_fields"] == ["mq"]
    assert vista["profile"]["updated_at"] == "2026-09-19T10:00:00+00:00"


def test_f2_il_read_model_dichiara_i_campi_modificabili():
    assert _vista()["profile"]["editable_fields"] == list(home_profile.OVERRIDABLE_FIELDS)


def test_f3_la_capability_e_vera_e_il_read_model_resta_chiuso():
    vista = _vista()
    assert vista["capabilities"]["profile_update"] is True
    assert set(vista["property"]) == {
        "comune", "microzona", "via", "civico", "tipologia", "mq", "piano",
        "locali", "bagni", "pertinenze", "ascensore", "anno", "stato",
        "vistamareyn", "distanzamare", "altrodescrizione"}


def test_f4_niente_dati_personali_ne_gestionali_nella_vista():
    completa = stima(nome="Mario", cognome="Rossi", email="m@e.it",
                     telefono="333", lead_status="nuovo", note_internal="x",
                     prezzo_mq_base=1500, token="abc")
    vista = home_service.build_home_detail(
        stima=completa, watch=None, observations=[], baseline_payload=None,
        completed_payload=None, overrides={"mq": 110, "version": 1})
    testo = json.dumps(vista, default=str).lower()
    for vietato in ("mario", "rossi", "m@e.it", "333", "note_internal",
                    "prezzo_mq_base", "lead_status", "abc"):
        assert vietato not in testo, vietato


# ---------------------------------------------------------------------------
# G - IL RADAR (punto 12)
# ---------------------------------------------------------------------------

def evento(tipo, quando, sorgente=tracking.EVENT_SOURCE):
    return {"event_type": tipo, "event_source": sorgente, "occurred_at": quando}


ADESSO = "2026-09-20T12:00:00+00:00"


def _interesse(eventi):
    from datetime import datetime
    return interest_service.build_interest(
        eventi, now=datetime.fromisoformat(ADESSO))["interest"]


def test_g1_home_updated_e_vero_solo_con_l_evento_nella_finestra():
    assert _interesse([])["home_updated"] is False
    assert _interesse([evento(tracking.HOME_UPDATED,
                              "2026-09-19T10:00:00+00:00")])["home_updated"] is True
    # Fuori finestra: l'evento resta nel database, l'affermazione "di
    # recente" no.
    assert _interesse([evento(tracking.HOME_UPDATED,
                              "2026-07-01T10:00:00+00:00")])["home_updated"] is False


def test_g2_la_motivazione_e_quella_dichiarata():
    radar = _interesse([evento(tracking.HOME_UPDATED, "2026-09-19T10:00:00+00:00")])
    assert interest_service.HOME_UPDATED_REASON in radar["reasons"]
    assert interest_service.HOME_UPDATED_REASON == "Ha aggiornato i dati della casa"


def test_g3_da_solo_l_aggiornamento_non_fa_high():
    """Punto 12: e' un segnale ad alta intenzione, non un livello."""
    radar = _interesse([evento(tracking.HOME_UPDATED, "2026-09-19T10:00:00+00:00")])
    assert radar["level"] == "medium"


def test_g4_con_ritorni_recenti_diventa_high():
    radar = _interesse([evento(tracking.HOME_UPDATED, "2026-09-19T10:00:00+00:00"),
                        evento(tracking.HOME_VIEWED, "2026-09-17T10:00:00+00:00")])
    assert radar["level"] == "high"


def test_70_la_richiesta_resta_high_anche_dopo_l_aggiornamento():
    """TEST 70 - LMC-9 non perde la precedenza."""
    radar = _interesse([evento(tracking.CONSULTATION_REQUESTED, "2026-09-18T10:00:00+00:00"),
                        evento(tracking.HOME_UPDATED, "2026-09-19T10:00:00+00:00")])
    assert radar["level"] == "high"
    assert radar["reasons"][0] == interest_service.CONSULTATION_REASON
    assert radar["reasons"][1] == interest_service.HOME_UPDATED_REASON


def test_g5_i_gradini_di_lmc7_non_sono_cambiati():
    assert _interesse([])["level"] == "none"
    assert _interesse([evento(tracking.HOME_VIEWED, "2026-09-19T10:00:00+00:00")
                       ])["level"] == "low"
    assert _interesse([evento(tracking.HOME_VIEWED, "2026-09-19T10:00:00+00:00"),
                       evento(tracking.HOME_VIEWED, "2026-09-10T10:00:00+00:00")
                       ])["level"] == "medium"


def test_g6_nessun_punteggio_nel_radar():
    radar = _interesse([evento(tracking.HOME_UPDATED, "2026-09-19T10:00:00+00:00")])
    testo = json.dumps(radar, ensure_ascii=False).lower()
    for vietato in ("score", "punteggio", "/100", "points"):
        assert vietato not in testo, vietato


def test_g7_un_evento_di_un_altra_sorgente_non_conta():
    assert _interesse([evento(tracking.HOME_UPDATED, "2026-09-19T10:00:00+00:00",
                              sorgente="stima360_it")])["home_updated"] is False


# ---------------------------------------------------------------------------
# H - il tracciamento: chi puo' dichiarare cosa
# ---------------------------------------------------------------------------

def test_h1_il_client_non_puo_dichiarare_l_aggiornamento():
    assert "home_updated" not in tracking.ACTIONS
    assert tracking.HOME_UPDATED not in tracking.ACTIONS.values()
    from owner.schemas import HomeEventCreate
    campo = HomeEventCreate.model_fields["action"]
    assert "home_updated" not in str(campo.annotation)


def test_h2_il_payload_e_quello_dichiarato():
    assert tracking.event_payload(tracking.HOME_UPDATED) == {"action": "home_updated"}


def test_h3_la_chiave_di_idempotenza_e_per_giorno_utc():
    from datetime import datetime, timezone
    chiave = tracking.idempotency_key(
        tracking.HOME_UPDATED, owner_account_id=9, stima_id=501,
        when=datetime(2026, 9, 20, 1, 30, tzinfo=timezone.utc))
    assert chiave == "owner_portal:owner_home_updated:owner:9:stima:501:day:2026-09-20"


def test_h4_l_evento_si_scrive_solo_dopo_la_persistenza():
    """Nel servizio, `track_home_updated` viene DOPO `upsert_home_override`
    e dopo il controllo del no-op. Si guarda l'ordine nel codice, perche' e'
    l'ordine che rende vera l'affermazione dell'evento."""
    sorgente = inspect.getsource(home_update.update_home)
    assert sorgente.index("upsert_home_override") < sorgente.index("track_home_updated")
    ramo_noop = sorgente[sorgente.index("if not cambiati:"):sorgente.index("riga = ")]
    assert "track_home_updated" not in ramo_noop
    assert "_refresh" not in ramo_noop


def test_h5_il_tracciamento_dell_aggiornamento_e_fail_open():
    sorgente = inspect.getsource(tracking.track_home_updated)
    assert "_fail_open" in sorgente
    assert "safe_record_event" not in sorgente


# ---------------------------------------------------------------------------
# I - il servizio: l'ordine dei cancelli, senza database
# ---------------------------------------------------------------------------

def test_i1_l_ordine_dei_cancelli_e_quello_dichiarato():
    sorgente = inspect.getsource(home_update.update_home)
    # Il corpo, non la firma ne' la docstring: `expected_version` compare in
    # entrambe, e cercarlo nel testo intero misurerebbe l'ordine delle
    # parole invece di quello delle istruzioni.
    corpo = sorgente[sorgente.index("owner_repository.get_home_grant"):]
    for prima, dopo in (("get_home_grant", "raise HomeVersionConflict"),
                        ("raise HomeVersionConflict", "normalize_patch"),
                        ("normalize_patch", "if not cambiati"),
                        ("if not cambiati", "upsert_home_override"),
                        ("upsert_home_override", "_refresh(")):
        assert corpo.index(prima) < corpo.index(dopo), (prima, dopo)


def test_i2_il_reason_del_refresh_e_dichiarato():
    assert home_update.REFRESH_REASON == "owner_profile_updated"


def test_i3_solo_created_autorizza_a_dire_valore_ricalcolato():
    sorgente = inspect.getsource(home_update._refresh)
    assert 'esito.get("status") == "created"' in sorgente
    assert home_update.VALUATION_RECALCULATED != home_update.VALUATION_UNCHANGED


def test_i4_un_guasto_del_refresh_non_annulla_l_aggiornamento():
    """Il `try` copre il refresh e restituisce uno stato, non solleva: la
    riga di override e' gia' scritta quando si arriva li'."""
    sorgente = inspect.getsource(home_update._refresh)
    assert "except Exception" in sorgente
    assert "raise" not in sorgente.split("except Exception")[1]


def test_i5_stime_non_viene_mai_aggiornata():
    """Il modulo non scrive su `stime`, e non deve poterlo fare per errore."""
    for modulo in (home_update, home_profile):
        sorgente = inspect.getsource(modulo)
        assert "UPDATE stime" not in sorgente
        assert "update stime" not in sorgente.lower()


def test_i6_il_repository_non_aggiorna_stime():
    from owner import repository as owner_repository
    sorgente = inspect.getsource(owner_repository.upsert_home_override)
    assert "UPDATE stime" not in sorgente
    assert "owner_home_overrides" in sorgente
    # Il grant si riverifica DENTRO la transazione della scrittura.
    assert sorgente.index("owner_stima_access") < sorgente.index("INSERT INTO")


# ---------------------------------------------------------------------------
# J - il frontend, eseguito davvero (armatura P6, come LMC-6/7/9)
# ---------------------------------------------------------------------------

from test_lmc6_owner_portal_home import BASE, CASA, detail, scenario  # noqa: E402

DETTAGLIO_MODIFICABILE = detail(
    capabilities={"profile_update": True},
    profile_version=0,
    profile={"considered_fields": ["comune", "mq"], "known_fields": ["comune", "mq"],
             "missing_fields": [], "completion_percent": 100,
             "editable_fields": list(home_profile.OVERRIDABLE_FIELDS),
             "overridden_fields": [], "version": 0, "updated_at": None})

DETTAGLIO_SOLA_LETTURA = detail(
    capabilities={"profile_update": False},
    profile_version=0,
    profile={"considered_fields": ["comune", "mq"], "known_fields": ["comune", "mq"],
             "missing_fields": [], "completion_percent": 100,
             "editable_fields": list(home_profile.OVERRIDABLE_FIELDS),
             "overridden_fields": [], "version": 0, "updated_at": None})


def test_j0_l_armatura_conosce_i_nuovi_elementi():
    testo = (ROOT / "tests" / "test_owner_06_p6.py").read_text(encoding="utf-8")
    for elemento in ("home-profile-edit", "home-profile-form", "home-profile-fields",
                     "home-profile-pertinenze-list", "home-profile-altro",
                     "home-profile-cancel", "home-profile-save",
                     "home-profile-status", "home-profile-updated"):
        assert f"'{elemento}'" in testo, elemento


def test_j1_la_cta_compare_e_il_form_resta_chiuso():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_MODIFICABILE}, """
assert(ids['home-profile-edit'].hidden === false, 'la CTA si vede');
assert(ids['home-profile-form'].hidden === true, 'il form nasce chiuso');
assert(ids['home-profile-status'].hidden === true, 'nessun messaggio');
""")


def test_j2_senza_capability_non_c_e_nessuna_via_per_modificare():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_SOLA_LETTURA}, """
assert(ids['home-profile-edit'].hidden === true, 'nessuna CTA');
assert(ids['home-profile-form'].hidden === true, 'nessun form');
""")


def test_j3_il_form_e_precompilato_con_il_profilo_effettivo():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_MODIFICABILE}, """
await ids['home-profile-edit'].trigger('click');
assert(ids['home-profile-form'].hidden === false, 'il form si apre');
const campi = ids['home-profile-fields'].children;
assert(campi.length > 0, 'ci sono campi');
const mq = campi.find((r) => r.children.some((c) => c.id === 'home-profile-field-mq'));
assert(mq, 'la casella mq esiste');
const input = mq.children.find((c) => c.id === 'home-profile-field-mq');
assert(input.value === '95', 'mq precompilato: ' + input.value);
assert(ids['home-profile-altro'].value === 'ristrutturato',
       'altro precompilato: ' + ids['home-profile-altro'].value);
const garage = ids['home-profile-pertinenze-list'].children
  .flatMap((l) => l.children).find((c) => c.value === 'garage');
assert(garage && garage.checked === true, 'garage spuntato');
""")


def test_j4_il_form_non_mostra_i_campi_non_modificabili():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_MODIFICABILE}, """
await ids['home-profile-edit'].trigger('click');
const idsCampi = ids['home-profile-fields'].children
  .flatMap((r) => r.children).map((c) => c.id).filter(Boolean);
for (const vietato of ['comune','microzona','via','civico','tipologia',
                       'posizionemare','distanzamare','vistamareyn']) {
  assert(!idsCampi.includes('home-profile-field-' + vietato), 'campo vietato: ' + vietato);
}
""")


def test_j5_annulla_non_manda_nessuna_patch():
    """PUNTO 14: zero PATCH."""
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_MODIFICABILE}, """
await ids['home-profile-edit'].trigger('click');
await ids['home-profile-cancel'].trigger('click');
assert(ids['home-profile-form'].hidden === true, 'il form si chiude');
assert(!calls.some((c) => c.method === 'PATCH'), 'nessuna PATCH');
""")


def test_j6_il_salvataggio_manda_expected_version_e_solo_la_whitelist():
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_MODIFICABILE}, """
await ids['home-profile-edit'].trigger('click');
const mq = ids['home-profile-fields'].children
  .flatMap((r) => r.children).find((c) => c.id === 'home-profile-field-mq');
mq.value = '110';
await ids['home-profile-form'].trigger('submit');
const patch = calls.find((c) => c.method === 'PATCH');
assert(patch, 'la PATCH parte');
assert(patch.url.endsWith('/homes/500'), 'rotta: ' + patch.url);
const corpo = JSON.parse(patch.body);
assert(corpo.expected_version === 0, 'expected_version: ' + corpo.expected_version);
assert(corpo.mq === 110, 'mq: ' + corpo.mq);
for (const vietato of ['comune','microzona','tipologia','agency_id','contact_id']) {
  assert(!(vietato in corpo), 'campo vietato nel corpo: ' + vietato);
}
""", extra={f"{BASE}/homes/500": [
        {"status": 200, "body": DETTAGLIO_MODIFICABILE},
        {"status": 200, "body": {"status": "updated", "profile_version": 1,
                                 "updated_fields": ["mq"], "value_recalculated": False,
                                 "home": DETTAGLIO_MODIFICABILE}}]})


def test_j7_successo_senza_snapshot_non_promette_un_valore_nuovo():
    scenario([CASA], [], None, """
await ids['home-profile-edit'].trigger('click');
await ids['home-profile-form'].trigger('submit');
assert(ids['home-profile-status'].textContent === 'Dati della casa aggiornati.',
       'copy: ' + ids['home-profile-status'].textContent);
assert(ids['home-profile-form'].hidden === true, 'il form si chiude');
""", extra={f"{BASE}/homes/500": [
        {"status": 200, "body": DETTAGLIO_MODIFICABILE},
        {"status": 200, "body": {"status": "updated", "profile_version": 1,
                                 "updated_fields": ["mq"], "value_recalculated": False,
                                 "home": DETTAGLIO_MODIFICABILE}}]})


def test_j8_con_uno_snapshot_vero_la_frase_cambia():
    scenario([CASA], [], None, """
await ids['home-profile-edit'].trigger('click');
await ids['home-profile-form'].trigger('submit');
assert(ids['home-profile-status'].textContent === 'Dati aggiornati e valore ricalcolato.',
       'copy: ' + ids['home-profile-status'].textContent);
""", extra={f"{BASE}/homes/500": [
        {"status": 200, "body": DETTAGLIO_MODIFICABILE},
        {"status": 200, "body": {"status": "updated", "profile_version": 1,
                                 "updated_fields": ["mq"], "value_recalculated": True,
                                 "home": DETTAGLIO_MODIFICABILE}}]})


def test_j9_il_no_op_lo_dice_e_lascia_il_form_aperto():
    scenario([CASA], [], None, """
await ids['home-profile-edit'].trigger('click');
await ids['home-profile-form'].trigger('submit');
assert(ids['home-profile-status'].textContent === 'Non ci sono modifiche da salvare.',
       'copy: ' + ids['home-profile-status'].textContent);
assert(ids['home-profile-form'].hidden === false, 'il form resta aperto');
""", extra={f"{BASE}/homes/500": [
        {"status": 200, "body": DETTAGLIO_MODIFICABILE},
        {"status": 200, "body": {"status": "unchanged", "profile_version": 0,
                                 "updated_fields": [], "value_recalculated": False,
                                 "home": DETTAGLIO_MODIFICABILE}}]})


def test_j10_il_409_dice_di_ricaricare_e_non_riprova_da_solo():
    scenario([CASA], [], None, """
await ids['home-profile-edit'].trigger('click');
await ids['home-profile-form'].trigger('submit');
assert(ids['home-profile-status'].textContent.indexOf('un’altra sessione') !== -1,
       'copy: ' + ids['home-profile-status'].textContent);
assert(ids['home-profile-form'].hidden === false, 'il form resta aperto');
assert(calls.filter((c) => c.method === 'PATCH').length === 1,
       'un solo tentativo, nessun ritentativo automatico');
""", extra={f"{BASE}/homes/500": [
        {"status": 200, "body": DETTAGLIO_MODIFICABILE},
        {"status": 409, "body": {"detail": "conflitto"}}]})


def test_j11_su_errore_i_dati_restano_nel_form():
    scenario([CASA], [], None, """
await ids['home-profile-edit'].trigger('click');
const mq = ids['home-profile-fields'].children
  .flatMap((r) => r.children).find((c) => c.id === 'home-profile-field-mq');
mq.value = '123';
await ids['home-profile-form'].trigger('submit');
assert(ids['home-profile-form'].hidden === false, 'il form resta aperto');
assert(mq.value === '123', 'il valore digitato resta: ' + mq.value);
""", extra={f"{BASE}/homes/500": [
        {"status": 200, "body": DETTAGLIO_MODIFICABILE},
        {"status": 503, "body": {"detail": "guasto"}}]})


def test_j12_il_doppio_invio_manda_una_sola_patch():
    scenario([CASA], [], None, """
await ids['home-profile-edit'].trigger('click');
const primo = ids['home-profile-form'].trigger('submit');
const secondo = ids['home-profile-form'].trigger('submit');
await primo; await secondo;
assert(calls.filter((c) => c.method === 'PATCH').length === 1,
       'una sola PATCH, non ' + calls.filter((c) => c.method === 'PATCH').length);
""", extra={f"{BASE}/homes/500": [
        {"status": 200, "body": DETTAGLIO_MODIFICABILE},
        {"status": 200, "body": {"status": "updated", "profile_version": 1,
                                 "updated_fields": ["mq"], "value_recalculated": False,
                                 "home": DETTAGLIO_MODIFICABILE}}]})


def test_j13_il_portale_non_promette_maggiore_precisione():
    testo = (ROOT / "static" / "owner_portal" / "assets" / "app.js").read_text(encoding="utf-8")
    regione = testo[testo.index("// LMC10_START"):testo.index("// LMC10_END")]
    for promessa in ("più preciso", "piu' preciso", "più accurat", "maggiore precisione",
                     "stima migliore"):
        assert promessa not in regione.lower(), promessa


def test_j14_la_timeline_del_crm_ha_l_etichetta():
    testo = (ROOT / "static" / "os_shell" / "assets" / "components"
             / "timeline.js").read_text(encoding="utf-8")
    assert "owner_home_updated: 'Ha aggiornato i dati della casa'" in testo
    assert interest_service.HOME_UPDATED_REASON in testo


# ---------------------------------------------------------------------------
# K - "nessuna pertinenza" nel portale, eseguito
# ---------------------------------------------------------------------------

DETTAGLIO_SENZA_PERTINENZE = detail(
    capabilities={"profile_update": True},
    profile_version=1,
    property={"pertinenze": ""},
    profile={"considered_fields": ["comune", "mq"], "known_fields": ["comune", "mq"],
             "missing_fields": [], "completion_percent": 100,
             "editable_fields": list(home_profile.OVERRIDABLE_FIELDS),
             "overridden_fields": ["pertinenze"], "version": 1,
             "updated_at": "2026-09-20T09:00:00+00:00"})


def test_k1_il_form_riaperto_non_ha_nessuna_casella_spuntata():
    """TEST 5 - il profilo effettivo dice "nessuna pertinenza", e il form lo
    mostra: nessuna casella accesa, nemmeno quella dell'originale."""
    scenario([CASA], [], {"status": 200, "body": DETTAGLIO_SENZA_PERTINENZE}, """
await ids['home-profile-edit'].trigger('click');
const caselle = ids['home-profile-pertinenze-list'].children
  .flatMap((l) => l.children).filter((c) => c.type === 'checkbox');
assert(caselle.length === 11, 'ci sono tutte le caselle: ' + caselle.length);
const accese = caselle.filter((c) => c.checked === true).map((c) => c.value);
assert(accese.length === 0, 'nessuna spuntata, invece: ' + accese.join(','));
""")


def test_k2_deselezionare_tutto_manda_una_lista_vuota():
    """TEST 1 dal browser: il client deve poter DIRE "nessuna"."""
    scenario([CASA], [], None, """
await ids['home-profile-edit'].trigger('click');
const caselle = ids['home-profile-pertinenze-list'].children
  .flatMap((l) => l.children).filter((c) => c.type === 'checkbox');
caselle.forEach((c) => { c.checked = false; });
await ids['home-profile-form'].trigger('submit');
const patch = calls.find((c) => c.method === 'PATCH');
assert(patch, 'la PATCH parte');
const corpo = JSON.parse(patch.body);
assert(Array.isArray(corpo.pertinenze), 'pertinenze e un array');
assert(corpo.pertinenze.length === 0, 'lista vuota, invece: ' + JSON.stringify(corpo.pertinenze));
""", extra={f"{BASE}/homes/500": [
        {"status": 200, "body": DETTAGLIO_MODIFICABILE},
        {"status": 200, "body": {"status": "updated", "profile_version": 1,
                                 "updated_fields": ["pertinenze"],
                                 "value_recalculated": True,
                                 "home": DETTAGLIO_SENZA_PERTINENZE}}]})


def test_k3_dopo_il_salvataggio_la_scheda_mostra_nessuna_pertinenza():
    scenario([CASA], [], None, """
await ids['home-profile-edit'].trigger('click');
const caselle = ids['home-profile-pertinenze-list'].children
  .flatMap((l) => l.children).filter((c) => c.type === 'checkbox');
caselle.forEach((c) => { c.checked = false; });
await ids['home-profile-form'].trigger('submit');
assert(ids['home-profile-status'].textContent === 'Dati aggiornati e valore ricalcolato.',
       'copy: ' + ids['home-profile-status'].textContent);
await ids['home-profile-edit'].trigger('click');
const riaperte = ids['home-profile-pertinenze-list'].children
  .flatMap((l) => l.children).filter((c) => c.type === 'checkbox' && c.checked);
assert(riaperte.length === 0, 'il garage non torna: ' + riaperte.map((c) => c.value).join(','));
""", extra={f"{BASE}/homes/500": [
        {"status": 200, "body": DETTAGLIO_MODIFICABILE},
        {"status": 200, "body": {"status": "updated", "profile_version": 1,
                                 "updated_fields": ["pertinenze"],
                                 "value_recalculated": True,
                                 "home": DETTAGLIO_SENZA_PERTINENZE}}]})


def test_k4_il_client_non_puo_inventare_un_token():
    """TEST 11 dal browser: le caselle sono quelle, e sono quelle del motore."""
    testo = (ROOT / "static" / "owner_portal" / "assets" / "app.js").read_text(encoding="utf-8")
    regione = testo[testo.index("const PROFILE_PERTINENZE"):]
    regione = regione[:regione.index("];") + 2]
    for token in home_profile.PERTINENZE_TOKENS:
        assert f"'{token}'" in regione, token
    dichiarati = re.findall(r"'([a-z ]+)'", regione)
    assert set(dichiarati) == set(home_profile.PERTINENZE_TOKENS), \
        set(dichiarati) ^ set(home_profile.PERTINENZE_TOKENS)


# ---------------------------------------------------------------------------
# L - "NESSUNA PERTINENZA" E' UNA RISPOSTA, NON UN CAMPO VUOTO
#
# La completezza di LMC-2 conta i campi che il database SA. Dopo il fix
# precedente `owner_home_overrides.pertinenze = ''` significa "il
# proprietario ha dichiarato che non ce ne sono": un dato fornito, non un
# dato mancante. Contarlo fra i mancanti abbasserebbe la percentuale proprio
# a chi ha appena risposto, ed e' il contrario di quello che quella barra
# dovrebbe incoraggiare.
#
# La distinzione che conta non e' "la stringa e' vuota" ma "da dove viene la
# stringa vuota": `''` in tabella e' una risposta, NULL e' silenzio. Per
# questo si guarda `overridden_fields` e non il valore.
# ---------------------------------------------------------------------------

def _profilo(stima_dict, override=None):
    return home_service.build_home_detail(
        stima=stima_dict, watch=None, observations=[], baseline_payload=None,
        completed_payload=None, overrides=override)["profile"]


def test_l1_senza_override_la_completezza_e_quella_di_sempre():
    """TEST 1 - il comportamento storico non si muove di un punto."""
    con_garage = _profilo(stima(pertinenze="garage"))
    assert "pertinenze" in con_garage["known_fields"]
    assert "pertinenze" not in con_garage["missing_fields"]

    # E una stima che non le ha mai dichiarate resta incompleta, come prima.
    senza = _profilo(stima(pertinenze=None))
    assert "pertinenze" in senza["missing_fields"]
    assert "pertinenze" not in senza["known_fields"]
    # Il confronto con la regola pura di LMC-2, non con un numero copiato.
    atteso = [c for c in home_service.PROFILE_FIELDS
              if home_service._has_value(stima(pertinenze=None).get(c))]
    assert senza["known_fields"] == atteso


def test_l2_l_override_vuoto_non_e_un_campo_mancante():
    """TEST 2."""
    profilo = _profilo(stima(pertinenze="garage"),
                       {"pertinenze": "", "version": 1})
    assert "pertinenze" not in profilo["missing_fields"], profilo["missing_fields"]


def test_l3_l_override_vuoto_conta_fra_i_conosciuti():
    """TEST 3 - e la differenza si misura contro il caso davvero ignoto.

    Tre situazioni, tre esiti, e il punto e' che la seconda e la terza NON
    coincidono pur avendo lo stesso valore effettivo:

        originale "garage", nessun override  -> conosciuto (valore)
        originale NULL,     nessun override  -> SCONOSCIUTO (silenzio)
        originale NULL,     override ''      -> conosciuto (risposta)
    """
    ignoto = _profilo(stima(pertinenze=None))
    dichiarato = _profilo(stima(pertinenze=None), {"pertinenze": "", "version": 1})

    assert "pertinenze" not in ignoto["known_fields"]
    assert "pertinenze" in dichiarato["known_fields"]
    assert len(dichiarato["known_fields"]) == len(ignoto["known_fields"]) + 1
    assert set(dichiarato["known_fields"]) - set(ignoto["known_fields"]) == {"pertinenze"}


def test_l4_la_percentuale_resta_conosciuti_su_considerati():
    """TEST 4 - la formula non e' cambiata, solo l'insieme dei conosciuti."""
    for override in (None, {"pertinenze": "", "version": 1},
                     {"pertinenze": "cantina", "version": 1}):
        for originale in (stima(pertinenze="garage"), stima(pertinenze=None)):
            profilo = _profilo(originale, override)
            totali = len(profilo["considered_fields"])
            assert profilo["completion_percent"] == round(
                len(profilo["known_fields"]) / totali * 100), (override, profilo)
            assert (len(profilo["known_fields"]) + len(profilo["missing_fields"])
                    == totali)
            assert set(profilo["known_fields"]).isdisjoint(profilo["missing_fields"])


def test_l5_altrodescrizione_vuota_non_acquisisce_la_semantica():
    """TEST 5 - l'eccezione resta una sola, e vale solo per le pertinenze.

    Si forza la situazione piu' sfavorevole possibile: `altrodescrizione`
    nell'insieme degli override dichiarati e con valore vuoto. Deve restare
    un campo mancante, perche' per quel campo "vuoto" non e' una risposta.
    """
    profilo = home_service.build_profile(
        stima(altrodescrizione="", pertinenze=""),
        overridden=("altrodescrizione", "pertinenze"))
    assert "altrodescrizione" in profilo["missing_fields"]
    assert "pertinenze" in profilo["known_fields"]
    # E la funzione che decide lo dice da sola, campo per campo.
    assert home_service._dichiarato_vuoto("pertinenze", ("pertinenze",)) is True
    for campo in home_service.PROFILE_FIELDS:
        if campo == "pertinenze":
            continue
        assert home_service._dichiarato_vuoto(campo, (campo,)) is False, campo


def test_l6_un_override_null_usa_ancora_la_regola_originale():
    """TEST 6 - NULL e' silenzio, non una risposta.

    La riga di override esiste (il proprietario ha corretto i metri quadri)
    ma `pertinenze` e' NULL: la completezza guarda l'originale, come sempre.
    """
    con_originale = _profilo(stima(pertinenze="garage"), {"mq": 140, "version": 1})
    assert "pertinenze" in con_originale["known_fields"]

    senza_originale = _profilo(stima(pertinenze=None), {"mq": 140, "version": 1})
    assert "pertinenze" in senza_originale["missing_fields"]
    assert "pertinenze" not in senza_originale["overridden_fields"]


def test_l7_la_decisione_passa_da_overridden_non_dal_valore():
    """Il cuore della regola, provato sulla funzione e non sul contorno.

    Lo stesso valore effettivo - stringa vuota - da' due esiti diversi a
    seconda che il proprietario abbia risposto o no. Se un giorno qualcuno
    riscrivesse `_dichiarato_vuoto` leggendo il valore invece della
    provenienza, questo test lo fermerebbe.
    """
    assert home_service._dichiarato_vuoto("pertinenze", ()) is False
    assert home_service._dichiarato_vuoto("pertinenze", None) is False
    assert home_service._dichiarato_vuoto("pertinenze", ("mq",)) is False
    assert home_service._dichiarato_vuoto("pertinenze", ("mq", "pertinenze")) is True


def test_l8_l_insieme_speciale_non_e_ricopiato():
    """`home_service` non tiene un proprio elenco: lo chiede a `home_profile`.

    Due elenchi che devono coincidere e che nessuno confronta prima o poi non
    coincidono; qui non ce n'e' un secondo da confrontare.
    """
    sorgente = inspect.getsource(home_service._dichiarato_vuoto)
    assert "home_profile.EMPTY_MEANS_OVERRIDE" in sorgente
    assert '"pertinenze"' not in sorgente.split('"""')[2], \
        "l'elenco non va ricopiato nel corpo della funzione"


def test_l9_il_read_model_completo_resta_coerente():
    """TEST 7, lato portale: la vista che il proprietario riceve."""
    vista = home_service.build_home_detail(
        stima=stima(pertinenze="garage"), watch=None, observations=[],
        baseline_payload=None, completed_payload=None,
        overrides={"pertinenze": "", "version": 2,
                   "updated_at": "2026-09-20T09:00:00+00:00"})
    assert vista["property"]["pertinenze"] == ""
    assert vista["profile"]["overridden_fields"] == ["pertinenze"]
    assert "pertinenze" in vista["profile"]["known_fields"]
    assert "pertinenze" not in vista["profile"]["missing_fields"]
    assert vista["profile_version"] == 2


def test_l10_dal_silenzio_alla_risposta_e_una_modifica():
    """Il corollario della regola di completezza, sul no-op.

    Stesso valore effettivo - niente pertinenze - ma due stati diversi del
    sapere. Se questa distinzione non ci fosse, il primo salvataggio su una
    stima senza pertinenze dichiarate sarebbe un no-op: il proprietario
    risponderebbe, non succederebbe niente, e la completezza non salirebbe
    mai.
    """
    cambia = home_update._cambia
    # Silenzio -> risposta: modifica.
    assert cambia("pertinenze", "", {"pertinenze": None}, ()) is True
    assert cambia("pertinenze", "", {"pertinenze": ""}, ()) is True
    # Risposta -> stessa risposta: no-op.
    assert cambia("pertinenze", "", {"pertinenze": ""}, ("pertinenze",)) is False
    # Valore diverso: modifica, dichiarato o no.
    assert cambia("pertinenze", "", {"pertinenze": "garage"}, ()) is True
    assert cambia("pertinenze", "garage", {"pertinenze": ""}, ("pertinenze",)) is True


def test_l11_la_regola_del_silenzio_vale_solo_per_le_pertinenze():
    """Nessun altro campo puo' arrivarci: la validazione rifiuta il vuoto
    prima, e anche se non lo facesse `_cambia` non lo tratterebbe cosi'."""
    cambia = home_update._cambia
    assert cambia("altrodescrizione", "", {"altrodescrizione": None}, ()) is False
    assert cambia("stato", "", {"stato": None}, ()) is False
    sorgente = inspect.getsource(home_update._cambia)
    assert "EMPTY_MEANS_OVERRIDE" in sorgente
