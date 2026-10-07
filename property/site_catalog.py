"""CATALOGO-CANONICO-1 (FASE D) - il catalogo canonico Stima360: sito -> CRM.

UN SOLO POSTO in cui si dice che cosa significa un valore del sito stima360.it
per la scheda immobile del CRM. Il form Immobili riceve le stesse liste da
`form-options` (nessun elenco scritto a mano nella Shell); la sincronizzazione
dal sito (`property/site_sync.py`) traduce con le funzioni di questo modulo.

FONTI E VERSIONE
----------------
Il contratto e' ricostruito dal REPOSITORY, ramo `core-0.1-test`: `main.py`
(`/api/salva_stima`, `/api/prefill`, `/api/salva_stima_dettagliata`),
`valuation.py` (le parole che il motore distingue), `home_profile.py` (i token
delle pertinenze) e `owner/home_update.py` (gli stati del motore). Il sito
pubblico non era consultabile da questa sessione (proxy: 403): le ETICHETTE
delle opzioni del sito non sono state verificate dal vivo. Per questo il
catalogo riconosce solo i valori che il codice del backend dimostra, e ogni
altro valore arrivato dal sito si CONSERVA (grezzo, "Da verificare") invece di
essere indovinato.

REGOLE
------
* `id` stabile (quello che il sito manda e il motore riconosce), `label`
  mostrata nel CRM, `aliases` solo per scritture che sono davvero la stessa
  cosa (maiuscole, spazi, il plurale "balconi"). Garage e posto auto restano
  distinti; "garage" del sito e il `box` del censimento sono la stessa rimessa
  chiusa.
* Non dichiarato / non so / zero / no sono quattro cose diverse:
    - campo assente o vuoto  -> None (non dichiarato), nessun valore scritto;
    - 0 nelle superfici del sito (mq*, numBalconi) -> None: il sito usa 0 per
      "campo non compilato", e una superficie di 0 m2 non e' un dato;
    - "no" esplicito (ascensore, vista, barriera) -> False;
    - una pertinenza non spuntata -> nessuna riga (non "assente": il sito non
      chiede di negarla).
* I prezzi e i coefficienti calcolati dal motore NON sono dati dichiarati e
  qui non compaiono. Il motore non cambia.
"""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from home_profile import PERTINENZE_TOKENS

from .catalog import ACCESSORY_KIND_LABELS, ENERGY_CLASSES, PROPERTY_TYPE_LABELS, TERRITORY

SOURCE = "stima360"

# ---------------------------------------------------------------------------
# Liste (id -> etichetta)
# ---------------------------------------------------------------------------

#: Stato dell'immobile: esattamente le parole di `valuation.coeff_stato`
#: (= owner/home_update.STATO_VALORI; un test le riconfronta).
CONDITIONS: dict[str, str] = {
    "nuovo": "Nuovo", "ristrutturato": "Ristrutturato", "buono": "Buono",
    "scarso": "Scarso", "grezzo": "Grezzo",
}

#: Posizione rispetto al mare (`posizioneMare`): `frontemare` e `seconda` sono
#: le parole di `valuation._posizione_coeff`; `oltre` e' il valore che il
#: backend scrive quando il form non lo manda. Nessun'altra forma e' unificata
#: (per esempio "fronte" NON e' "frontemare": il motore non le tratta allo
#: stesso modo, e il catalogo non decide al posto suo).
SEA_POSITIONS: dict[str, str] = {
    "frontemare": "Fronte mare", "seconda": "Seconda fila", "oltre": "Oltre la seconda fila",
}

#: Distanza dal mare (`distanzaMare`), le fasce di `valuation._distanza_coeff`.
SEA_DISTANCES: dict[str, str] = {
    "0-100": "0-100 m", "100-300": "100-300 m", "300-500": "300-500 m", "500-1000": "500-1000 m",
}

#: Tipi di accessorio (property_accessories.kind): quelli del censimento piu'
#: quelli del sito (migration 087). Le etichette sono `catalog.ACCESSORY_KIND_LABELS`
#: (un test confronta le chiavi con questo elenco e con `schemas.ACCESSORY_KINDS`). `site_tokens`: come il sito li scrive in
#: `pertinenze`; `mq`/`qty`: il campo del sito con la superficie o il numero;
#: `status`: lo stato catastale con cui nasce l'accessorio dal sito
#: (`unknown` = "Da chiarire": il sito non dice se ha un subalterno proprio;
#: solo i balconi sono per natura parte dell'unita').
ACCESSORY_KINDS: dict[str, dict[str, Any]] = {
    "box":        {"site_tokens": ("garage", "box", "box auto"), "mq": "mqGarage", "status": "unknown"},
    "posto_auto": {"site_tokens": ("posto auto",), "mq": "mqPostoAuto", "status": "unknown"},
    "cantina":    {"site_tokens": ("cantina",), "mq": "mqCantina", "status": "unknown"},
    "taverna":    {"site_tokens": ("taverna",), "mq": "mqTaverna", "status": "unknown"},
    "soffitta":   {"site_tokens": ("soffitta",), "mq": "mqSoffitta", "status": "unknown"},
    "balcone":    {"site_tokens": ("balconi", "balcone"), "qty": "numBalconi", "status": "included"},
    "terrazzo":   {"site_tokens": ("terrazzo",), "mq": "mqTerrazzo", "status": "unknown"},
    "giardino":   {"site_tokens": ("giardino",), "mq": "mqGiardino", "status": "unknown"},
    "piscina":    {"site_tokens": ("piscina",), "status": "unknown"},
    "posto_moto": {"site_tokens": ("posto moto",), "status": "unknown"},
    "posto_bici": {"site_tokens": ("posto bici", "posto bicicletta", "posto biciclette"), "status": "unknown"},
    "deposito":   {"site_tokens": (), "status": "unknown"},
    "altro":      {"site_tokens": (), "status": "unknown"},
}

#: I token che il motore cerca dentro il testo e non come elemento della
#: lista (home_profile.parse_pertinenze): qui valgono allo stesso modo.
_TOKEN_NEL_TESTO = ("piscina", "posto moto", "posto bici")

#: Etichette dei campi nuovi della scheda (form e pannello provenienza).
FIELD_LABELS: dict[str, str] = {
    "property_type": "Tipologia", "city": "Comune", "region": "Regione", "province": "Provincia",
    "microzone": "Microzona", "address": "Via", "civic_number": "Civico",
    "surface_sqm": "Superficie (m²)", "floor": "Piano", "rooms": "Locali", "bathrooms": "Bagni",
    "elevator": "Ascensore", "year_built": "Anno di costruzione", "condition": "Stato",
    "energy_class": "Classe energetica", "sea_position": "Posizione mare", "sea_distance": "Distanza dal mare",
    "sea_band": "Fascia mare (sito)", "sea_barrier": "Ferrovia o strada verso il mare",
    "sea_view": "Vista mare", "sea_view_detail": "Dettaglio vista mare", "heating": "Riscaldamento",
    "air_conditioning": "Climatizzazione", "air_conditioning_type": "Tipo di climatizzazione",
    "exposure": "Esposizione", "furnishing": "Arredamento", "condo_fees": "Spese condominiali (€, periodicità non specificata)",
    "other_features": "Altre caratteristiche",
}

#: Le colonne della 087 sulla scheda: il form le invia, gli schemi le accettano.
SITE_PROPERTY_FIELDS = ("sea_position", "sea_distance", "sea_band", "sea_barrier", "sea_view", "sea_view_detail",
                        "heating", "air_conditioning", "air_conditioning_type", "exposure", "furnishing",
                        "condo_fees", "other_features")


def property_type_aliases() -> dict[str, str]:
    """Tipologia del sito -> `property_type`. Le parole del motore
    (`valuation.coeff_tipologia`: appartamento, villa, rustico) e le etichette
    del CRM stesse; nient'altro. "Altro" e' un valore vero (-> other)."""
    alias = {label.casefold(): value for value, label in PROPERTY_TYPE_LABELS.items()}
    alias.update({"appartamento": "apartment", "villa": "villa", "rustico": "rustic"})
    return alias


def labels_for_form() -> dict[str, list[dict[str, str]]]:
    """Le liste per `form-options`, nella forma `{value, label}`."""
    return {
        "conditions": [{"value": k, "label": v} for k, v in CONDITIONS.items()],
        "sea_positions": [{"value": k, "label": v} for k, v in SEA_POSITIONS.items()],
        "sea_distances": [{"value": k, "label": v} for k, v in SEA_DISTANCES.items()],
        "site_field_labels": [{"value": k, "label": v} for k, v in FIELD_LABELS.items()],
    }


# ---------------------------------------------------------------------------
# Lettura dei valori grezzi
# ---------------------------------------------------------------------------

_VERO = frozenset({"si", "sì", "true", "1", "yes", "y", "on"})
_FALSO = frozenset({"no", "false", "0", "n", "off"})
_NON_SO = frozenset({"non so", "non lo so", "nd", "n.d.", "n/d", "non indicato", "non indicata", "-"})


def _testo(valore) -> str | None:
    if valore is None or isinstance(valore, bool):
        return None if valore is None else ("true" if valore else "false")
    testo = re.sub(r"\s+", " ", str(valore)).strip()
    return testo or None


def _chiave(testo: str) -> str:
    """Confronto senza maiuscole, spazi doppi e apostrofi tipografici."""
    return re.sub(r"\s+", " ", testo.replace("’", "'").replace("`", "'")).strip().casefold()


class Esito:
    """Il risultato di una traduzione: valori per colonna, accessori,
    dichiarazione (valore + grezzo) e valori non riconosciuti."""

    def __init__(self):
        self.fields: dict[str, Any] = {}
        self.accessories: dict[str, dict[str, Any]] = {}
        self.declared: dict[str, Any] = {}
        self.unmapped: list[dict[str, Any]] = []
        self.pertinenze_declared = False     # il payload portava la lista delle pertinenze
        #: campi che la scheda deve mostrare «Da verificare» anche se la colonna
        #: ha un valore tecnico (oggi solo la tipologia, NOT NULL nel database)
        self.unverified: dict[str, dict[str, Any]] = {}

    def metti(self, campo: str, valore, grezzo) -> None:
        if valore is None:
            return
        self.fields[campo] = valore
        self.declared[campo] = {"value": _json(valore), "raw": _json(grezzo)}

    def ignoto(self, campo_sito: str, grezzo, motivo: str) -> None:
        self.unmapped.append({"site_field": campo_sito, "raw": _json(grezzo), "reason": motivo})


def _json(valore):
    if isinstance(valore, Decimal):
        return str(valore)
    return valore


def parse_bool(valore) -> tuple[bool | None, bool]:
    """(valore, riconosciuto). None + riconosciuto = non dichiarato / non so."""
    if isinstance(valore, bool):
        return valore, True
    testo = _testo(valore)
    if testo is None:
        return None, True
    k = _chiave(testo)
    if k in _VERO:
        return True, True
    if k in _FALSO:
        return False, True
    if k in _NON_SO:
        return None, True
    return None, False


def parse_decimal(valore, *, zero_is_unknown: bool) -> tuple[Decimal | None, bool]:
    """Numero con la virgola o il punto, due decimali (la precisione del
    CRM): niente troncamento all'intero come fa `stime`."""
    if isinstance(valore, bool):
        return None, False
    testo = _testo(valore)
    if testo is None:
        return None, True
    testo = testo.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", testo):        # 1.200,50
        testo = testo.replace(".", "").replace(",", ".")
    else:
        testo = testo.replace(",", ".")
    try:
        numero = Decimal(testo)
    except InvalidOperation:
        return None, False
    if not numero.is_finite() or numero < 0:
        return None, False
    if numero == 0 and zero_is_unknown:
        return None, True
    return numero.quantize(Decimal("0.01")), True


def parse_int(valore, *, minimo: int, massimo: int, zero_is_unknown: bool = False) -> tuple[int | None, bool]:
    numero, ok = parse_decimal(valore, zero_is_unknown=zero_is_unknown)
    if not ok or numero is None:
        return None, ok
    if numero != numero.to_integral_value() or not minimo <= numero <= massimo:
        return None, False
    return int(numero), True


_LOCALI_PAROLE = (("mono", 1), ("bilocale", 2), ("trilocale", 3), ("quadrilocale", 4), ("pentalocale", 5))


def parse_rooms(valore) -> tuple[int | None, bool]:
    """"3" o "Trilocale" (la forma che `valuation.coeff_locali` legge)."""
    testo = _testo(valore)
    if testo is None:
        return None, True
    k = _chiave(testo)
    for parola, numero in _LOCALI_PAROLE:
        if k == parola or k.startswith(parola):
            return numero, True
    return parse_int(testo, minimo=1, massimo=100)


_PIANO = {"terra": "T", "piano terra": "T", "pt": "T", "t": "T", "rialzato": "R", "piano rialzato": "R",
          "seminterrato": "S", "interrato": "S", "ultimo": "Ultimo", "attico": "Attico"}


def parse_floor(valore) -> tuple[str | None, bool]:
    """Piano: le parole del motore (`valuation._parse_piano`: terra, ultimo,
    attico) e i numeri. Terra -> T, come i chips del censimento."""
    testo = _testo(valore)
    if testo is None:
        return None, True
    k = _chiave(testo)
    if k in _PIANO:
        return _PIANO[k], True
    if re.fullmatch(r"-?\d{1,2}", k):
        return str(int(k)), True
    return None, False


def parse_energy_class(valore) -> tuple[str | None, bool]:
    testo = _testo(valore)
    if testo is None:
        return None, True
    k = _chiave(testo)
    if k in _NON_SO or k in ("non so", "in attesa"):
        return None, True
    codice = k.replace(" ", "").upper()
    return (codice, True) if codice in ENERGY_CLASSES else (None, False)


def _municipio(comune: str | None):
    """(regione, provincia, nome canonico, microzone) dal catalogo territoriale."""
    if not comune:
        return None
    k = _chiave(comune)
    for regione, province in TERRITORY.items():
        for sigla, provincia in province.items():
            for nome, (_istat, zone) in provincia["municipalities"].items():
                if _chiave(nome) == k:
                    return regione, sigla, nome, zone
    return None


def pertinenze_tokens(grezzo) -> tuple[list[tuple[str, str]], list[str]]:
    """[(kind, token del sito)] riconosciuti e [pezzi non riconosciuti].

    Stessa tokenizzazione del motore e di `home_profile` (virgole; `;|/\\`
    diventano virgole); piscina, posto moto e posto bici valgono anche dentro
    un pezzo di testo, come per il motore."""
    testo = str(grezzo or "").lower()
    for sep in (";", "|", "/", "\\"):
        testo = testo.replace(sep, ",")
    pezzi = [re.sub(r"\s+", " ", p).strip() for p in testo.split(",") if p.strip()]
    trovati: list[tuple[str, str]] = []
    ignoti: list[str] = []
    for pezzo in pezzi:
        kind = next((k for k, d in ACCESSORY_KINDS.items() if pezzo in d["site_tokens"]), None)
        if kind is None:
            kind = next((k for k, d in ACCESSORY_KINDS.items()
                         if any(t in pezzo for t in d["site_tokens"] if t in _TOKEN_NEL_TESTO)), None)
        if kind is None:
            if pezzo not in ignoti:
                ignoti.append(pezzo)
        elif kind not in [k for k, _ in trovati]:
            trovati.append((kind, pezzo))
    return trovati, ignoti


# ---------------------------------------------------------------------------
# Payload del sito -> scheda
# ---------------------------------------------------------------------------

def _primo(raw: dict, *nomi):
    """Il valore del primo nome presente (il dettagliato accetta camelCase e
    minuscolo, come `salva_stima_dettagliata`)."""
    for nome in nomi:
        if nome in raw and _testo(raw.get(nome)) is not None:
            return nome, raw.get(nome)
    return nomi[0], None


def _campo(esito: Esito, raw: dict, colonna: str, nomi: tuple, parser, motivo: str) -> None:
    nome, grezzo = _primo(raw, *nomi)
    if grezzo is None:
        return
    valore, ok = parser(grezzo)
    if not ok:
        esito.ignoto(nome, grezzo, motivo)
        return
    esito.metti(colonna, valore, grezzo)


def _libero(esito: Esito, raw: dict, colonna: str, nomi: tuple, massimo: int | None = None) -> None:
    """Testo libero del cliente: si conserva com'e' (spazi normalizzati)."""
    nome, grezzo = _primo(raw, *nomi)
    testo = _testo(grezzo)
    if testo is None:
        return
    if massimo is not None and len(testo) > massimo:
        esito.ignoto(nome, testo, f"Testo piu' lungo di {massimo} caratteri")
        return
    esito.metti(colonna, testo, grezzo)


def _da_catalogo(elenco: dict[str, str], *, normalizza=None):
    def parser(grezzo):
        k = _chiave(str(grezzo))
        if normalizza:
            k = normalizza(k)
        if k in _NON_SO:
            return None, True
        return (k, True) if k in elenco else (None, False)
    return parser


def _distanza(k: str) -> str:
    return k.replace("–", "-").replace("m", "").replace(" ", "")


def map_site_payload(raw: dict | None, *, comune: str | None = None, detailed: bool = False) -> Esito:
    """Traduce il payload REALMENTE inviato dal sito (mai la riga `stime`
    con i default del backend) nei campi della scheda.

    `comune`: il comune come la stima lo ha salvato (quello su cui e' stata
    instradata l'agenzia). Il dettagliato non porta comune, via e civico:
    porta un indirizzo a testo libero, che non si interpreta."""
    raw = dict(raw or {})
    e = Esito()

    # --- territorio ---------------------------------------------------------
    if not detailed:
        nome_comune = _testo(comune) or _testo(raw.get("comune"))
        trovato = _municipio(nome_comune)
        if trovato is not None:
            regione, sigla, canonico, zone = trovato
            e.metti("city", canonico, raw.get("comune") or comune)
            e.metti("region", regione, None)
            e.metti("province", sigla, None)
        elif nome_comune is not None:
            e.metti("city", nome_comune, raw.get("comune") or comune)
            e.ignoto("comune", nome_comune, "Comune non presente nel catalogo territoriale")
            zone = None
        else:
            zone = None
        micro = _testo(raw.get("microzona"))
        if micro is not None:
            canonica = next((z for z in (zone or []) if _chiave(z) == _chiave(micro)), None)
            if canonica is not None:
                e.metti("microzone", canonica, micro)
            else:
                e.ignoto("microzona", micro, "Microzona non presente nel catalogo del comune")
        via = _testo(raw.get("via"))
        if via is not None and _chiave(via) != "zona":            # "Zona" e' il segnaposto del backend
            if len(via) <= 250:
                e.metti("address", via, raw.get("via"))
            else:
                e.ignoto("via", via, "Via piu' lunga di 250 caratteri")
        civico = _testo(raw.get("civico"))
        if civico is not None:
            if len(civico) <= 30:
                e.metti("civic_number", civico, raw.get("civico"))
            else:
                e.ignoto("civico", civico, "Civico piu' lungo di 30 caratteri")
    else:
        micro = _testo(raw.get("microzona"))
        if micro is not None:
            e.declared["microzone_detail"] = {"value": None, "raw": micro}
        indirizzo = _testo(raw.get("indirizzo"))
        if indirizzo is not None:
            # Testo libero ("Via Roma 12, Tortoreto"): mai spezzato a indovinare.
            e.ignoto("indirizzo", indirizzo, "Indirizzo a testo libero della stima dettagliata: da riportare a mano")

    # --- caratteristiche ------------------------------------------------------
    alias = property_type_aliases()
    _campo(e, raw, "property_type", ("tipologia",),
           lambda g: (alias.get(_chiave(str(g))), _chiave(str(g)) in alias), "Tipologia non presente nel catalogo")
    _nome, tip = _primo(raw, "tipologia")
    if tip is not None and "property_type" not in e.fields:
        e.unverified["property_type"] = {"raw": _testo(tip), "reason": "Tipologia non presente nel catalogo"}
    _campo(e, raw, "surface_sqm", ("mq",), lambda g: parse_decimal(g, zero_is_unknown=True), "Superficie non valida")
    _campo(e, raw, "floor", ("piano",), parse_floor, "Piano non riconosciuto")
    _campo(e, raw, "rooms", ("locali",), parse_rooms, "Locali non riconosciuti")
    _campo(e, raw, "bathrooms", ("bagni",), lambda g: parse_int(g, minimo=0, massimo=50), "Bagni non validi")
    _campo(e, raw, "elevator", ("ascensore",), parse_bool, "Ascensore: valore non riconosciuto")
    _campo(e, raw, "year_built", ("anno",), lambda g: parse_int(g, minimo=1000, massimo=2200), "Anno non valido")
    _campo(e, raw, "condition", ("stato",), _da_catalogo(CONDITIONS), "Stato non presente nel catalogo")

    # --- mare -----------------------------------------------------------------
    _campo(e, raw, "sea_position", ("posizioneMare", "posizionemare"), _da_catalogo(SEA_POSITIONS),
           "Posizione mare non presente nel catalogo")
    _campo(e, raw, "sea_distance", ("distanzaMare", "distanzamare"), _da_catalogo(SEA_DISTANCES, normalizza=_distanza),
           "Distanza dal mare non presente nel catalogo")
    _libero(e, raw, "sea_band", ("fascia_mare",), 32)
    _campo(e, raw, "sea_barrier", ("barrieraMare", "barrieramare"), parse_bool, "Barriera: valore non riconosciuto")
    nome_yn, yn = _primo(raw, "vistaMareYN", "vistamareyn")
    vista, ok = parse_bool(yn)
    if yn is not None and not ok:
        e.ignoto(nome_yn, yn, "Vista mare: valore non riconosciuto")
    dettaglio = _testo(_primo(raw, "vistaMareDettaglio", "vistamaredettaglio")[1]) \
        or _testo(_primo(raw, "vistaMare", "vistamare")[1])
    if vista is None and dettaglio is not None and _chiave(dettaglio) not in _FALSO:
        vista = True                       # una vista descritta e' una vista dichiarata
    if vista is not None:
        e.metti("sea_view", vista, yn if yn is not None else dettaglio)
    if dettaglio is not None and vista is not False:
        if len(dettaglio) <= 200:
            e.metti("sea_view_detail", dettaglio, dettaglio)
        else:
            e.ignoto("vistaMareDettaglio", dettaglio, "Testo piu' lungo di 200 caratteri")

    # --- testo libero e impianti (dettagliata) -------------------------------
    _libero(e, raw, "other_features", ("altroDescrizione", "altrodescrizione"))
    _campo(e, raw, "energy_class", ("classe",), parse_energy_class, "Classe energetica non presente nel catalogo")
    _libero(e, raw, "heating", ("riscaldamento",), 120)
    _libero(e, raw, "air_conditioning", ("condizionatore",), 120)
    _libero(e, raw, "air_conditioning_type", ("condiz_tipo",), 120)
    _libero(e, raw, "exposure", ("esposizione",), 120)
    _libero(e, raw, "furnishing", ("arredo",), 120)
    _campo(e, raw, "condo_fees", ("spese_cond",), lambda g: parse_decimal(g, zero_is_unknown=False),
           "Spese condominiali non valide")

    # --- pertinenze -------------------------------------------------------------
    if "pertinenze" in raw:
        e.pertinenze_declared = True
        trovati, ignoti = pertinenze_tokens(raw.get("pertinenze"))
        for kind, token in trovati:
            definizione = ACCESSORY_KINDS[kind]
            voce: dict[str, Any] = {"present": True, "surface_sqm": None, "quantity": None, "raw": token}
            if definizione.get("mq"):
                nome, grezzo = _primo(raw, definizione["mq"], definizione["mq"].lower())
                mq, ok = parse_decimal(grezzo, zero_is_unknown=True)
                if not ok:
                    e.ignoto(nome, grezzo, "Superficie della pertinenza non valida")
                voce["surface_sqm"] = mq
            if definizione.get("qty"):
                nome, grezzo = _primo(raw, definizione["qty"], definizione["qty"].lower())
                n, ok = parse_int(grezzo, minimo=1, massimo=1000, zero_is_unknown=True)
                if not ok:
                    e.ignoto(nome, grezzo, "Numero non valido")
                voce["quantity"] = n
            e.accessories[kind] = voce
        for pezzo in ignoti:
            e.ignoto("pertinenze", pezzo, "Pertinenza non presente nel catalogo")
        e.declared["pertinenze"] = {"value": [k for k, _ in trovati], "raw": _testo(raw.get("pertinenze"))}
    return e


def fingerprint(esito: Esito) -> str:
    """L'impronta di cio' che il sito ha dichiarato (senza dati di contatto):
    due invii identici hanno la stessa impronta."""
    corpo = {"fields": {k: _json(v) for k, v in esito.fields.items()},
             "accessories": {k: {kk: _json(vv) for kk, vv in v.items() if kk != "raw"}
                             for k, v in esito.accessories.items()},
             "unmapped": esito.unmapped}
    return hashlib.sha256(json.dumps(corpo, sort_keys=True, default=str).encode()).hexdigest()


def engine_tokens_covered() -> bool:
    """Ogni token che il motore riconosce ha un tipo di accessorio."""
    return all(any(t in d["site_tokens"] for d in ACCESSORY_KINDS.values()) for t in PERTINENZE_TOKENS)


# ---------------------------------------------------------------------------
# Cio' che si conserva di un invio (senza dati di contatto)
# ---------------------------------------------------------------------------

#: Le chiavi del payload del sito che descrivono l'immobile: le sole lette da
#: `map_site_payload`. Si conservano COME il form le ha inviate.
SITE_DECLARED_KEYS: tuple[str, ...] = (
    "comune", "microzona", "via", "civico", "tipologia", "mq", "piano", "locali", "bagni", "ascensore",
    "anno", "stato", "posizioneMare", "posizionemare", "distanzaMare", "distanzamare", "fascia_mare",
    "barrieraMare", "barrieramare", "vistaMareYN", "vistamareyn", "vistaMareDettaglio", "vistamaredettaglio",
    "vistaMare", "vistamare", "altroDescrizione", "altrodescrizione", "classe", "riscaldamento",
    "condizionatore", "condiz_tipo", "esposizione", "arredo", "spese_cond", "indirizzo", "pertinenze",
    *(d[k] for d in ACCESSORY_KINDS.values() for k in ("mq", "qty") if d.get(k)),
    *(d[k].lower() for d in ACCESSORY_KINDS.values() for k in ("mq", "qty") if d.get(k)),
)

#: Chiavi di servizio del sito: l'identita' della richiesta e i campi che il
#: cliente ha dichiarato (modificato o confermato) nella dettagliata.
REQUEST_ID_KEY = "client_request_id"
DECLARED_FIELDS_KEY = "campi_dichiarati"


def declared_payload(raw: dict | None) -> tuple[dict, list[str]]:
    """(valori dell'immobile come inviati, nomi delle altre chiavi).

    Nessun dato di contatto: nome, email, telefono, consensi, note restano
    dove sono gia' (`stime`, `stime_dettagliate`, contatti). Delle chiavi che
    il catalogo non conosce si conserva solo il NOME: dice che il sito manda
    qualcosa che il CRM non legge ancora, senza copiarne il contenuto."""
    raw = dict(raw or {})
    conosciute = set(SITE_DECLARED_KEYS)
    valori = {k: _json(v) if not isinstance(v, (dict, list)) else v for k, v in raw.items() if k in conosciute}
    altre = sorted(k for k in raw if k not in conosciute and k not in (REQUEST_ID_KEY, DECLARED_FIELDS_KEY, "stima_id"))
    return valori, altre


def request_id(raw: dict | None):
    """L'identita' stabile della richiesta, se il sito la manda (UUID)."""
    import uuid as _uuid
    valore = (raw or {}).get(REQUEST_ID_KEY)
    if valore in (None, ""):
        return None
    try:
        return _uuid.UUID(str(valore))
    except ValueError:
        return None


def declared_fields(raw: dict | None) -> list[str] | None:
    """`campi_dichiarati` della dettagliata: lista di chiavi del payload (o
    stringa separata da virgole). None = il sito non lo manda (client attuale)."""
    valore = (raw or {}).get(DECLARED_FIELDS_KEY)
    if valore is None:
        return None
    if isinstance(valore, str):
        valore = [v for v in re.split(r"[,;\s]+", valore) if v]
    if not isinstance(valore, (list, tuple)):
        return None
    return sorted({str(v).strip() for v in valore if str(v).strip()})


# ---------------------------------------------------------------------------
# La dettagliata arriva PRECOMPILATA da /api/prefill
# ---------------------------------------------------------------------------

#: `/api/prefill` (main.py): chiave della risposta -> colonna di `stime`. Le
#: stesse, nello stesso ordine; un test lo riconfronta con il sorgente.
PREFILL_KEYS: tuple[tuple[str, str], ...] = (
    ("comune", "comune"), ("microzona", "microzona"), ("via", "via"), ("civico", "civico"),
    ("tipologia", "tipologia"), ("mq", "mq"), ("piano", "piano"), ("locali", "locali"), ("bagni", "bagni"),
    ("pertinenze", "pertinenze"), ("ascensore", "ascensore"), ("anno", "anno"), ("stato", "stato"),
    ("posizioneMare", "posizionemare"), ("distanzaMare", "distanzamare"), ("barrieraMare", "barrieramare"),
    ("vistaMareYN", "vistamareyn"), ("vistaMareDettaglio", "vistamaredettaglio"), ("vistaMare", "vistamare"),
    ("mqGiardino", "mqgiardino"), ("mqGarage", "mqgarage"), ("mqCantina", "mqcantina"),
    ("mqPostoAuto", "mqpostoauto"), ("mqTaverna", "mqtaverna"), ("mqSoffitta", "mqsoffitta"),
    ("mqTerrazzo", "mqterrazzo"), ("numBalconi", "numbalconi"), ("altroDescrizione", "altrodescrizione"),
)

#: Le chiavi del payload da cui nasce ciascun campo della scheda.
FIELD_SOURCE_KEYS: dict[str, tuple[str, ...]] = {
    "property_type": ("tipologia",), "surface_sqm": ("mq",), "floor": ("piano",), "rooms": ("locali",),
    "bathrooms": ("bagni",), "elevator": ("ascensore",), "year_built": ("anno",), "condition": ("stato",),
    "sea_position": ("posizioneMare", "posizionemare"), "sea_distance": ("distanzaMare", "distanzamare"),
    "sea_band": ("fascia_mare",), "sea_barrier": ("barrieraMare", "barrieramare"),
    "sea_view": ("vistaMareYN", "vistamareyn", "vistaMareDettaglio", "vistamaredettaglio", "vistaMare", "vistamare"),
    "sea_view_detail": ("vistaMareDettaglio", "vistamaredettaglio", "vistaMare", "vistamare"),
    "other_features": ("altroDescrizione", "altrodescrizione"), "energy_class": ("classe",),
    "heating": ("riscaldamento",), "air_conditioning": ("condizionatore",), "air_conditioning_type": ("condiz_tipo",),
    "exposure": ("esposizione",), "furnishing": ("arredo",), "condo_fees": ("spese_cond",),
}


def canon(valore):
    """Confronto fra valore del database, del sito e salvato in JSON."""
    if valore is None:
        return None
    if isinstance(valore, bool):
        return ("b", valore)
    if isinstance(valore, (int, float, Decimal)):
        return ("n", Decimal(str(valore)).normalize())
    testo = str(valore).strip()
    if testo == "":
        return None
    try:
        numero = Decimal(testo)
        if numero.is_finite():
            return ("n", numero.normalize())
    except InvalidOperation:
        pass
    return ("s", testo)


def same(a, b) -> bool:
    return canon(a) == canon(b)


def _dichiarato_da(chiavi: tuple[str, ...], dichiarati: set[str]) -> bool:
    return any(_chiave(k) in dichiarati for k in chiavi)


def separate_prefilled(dettaglio: Esito, precompilato: Esito | None, dichiarati: list[str] | None) -> list[str]:
    """Toglie dalla dettagliata i valori RIMASTI COME IL SITO LI AVEVA
    PRECOMPILATI e restituisce i nomi dei campi tolti.

    `/api/prefill` legge `stime`, cioe' i default del backend (piano 1,
    3 locali, anno 2000...) e i metri quadri ridotti a intero: un valore
    rimandato tale e quale NON e' una dichiarazione del cliente, e
    trattarlo come tale inventerebbe dati o perderebbe i decimali della
    stima rapida. Il form attuale non dice quali campi il cliente ha
    toccato, quindi la regola e' conservativa: uguale al precompilato =
    nessuna informazione nuova. Un valore DIVERSO e' una correzione
    esplicita.

    `dichiarati` (`campi_dichiarati`, quando il sito lo manda) vince: un
    campo li' elencato e' una dichiarazione anche se coincide con il
    precompilato (il cliente l'ha confermato)."""
    if precompilato is None:
        return []
    scelti = {_chiave(k) for k in (dichiarati or [])}
    tolti: list[str] = []
    for campo in list(dettaglio.fields):
        if campo not in precompilato.fields or _dichiarato_da(FIELD_SOURCE_KEYS.get(campo, ()), scelti):
            continue
        if same(dettaglio.fields[campo], precompilato.fields[campo]):
            dettaglio.fields.pop(campo)
            dettaglio.declared.pop(campo, None)
            tolti.append(campo)
    for kind in list(dettaglio.accessories):
        definizione = ACCESSORY_KINDS[kind]
        chiavi = ("pertinenze", *(definizione[k] for k in ("mq", "qty") if definizione.get(k)))
        prima = precompilato.accessories.get(kind)
        if prima is None or _dichiarato_da(chiavi, scelti):
            continue
        voce = dettaglio.accessories[kind]
        if all(same(voce.get(k), prima.get(k)) for k in ("surface_sqm", "quantity")):
            dettaglio.accessories.pop(kind)
            tolti.append(f"accessory:{kind}")
    if dettaglio.pertinenze_declared and precompilato.pertinenze_declared and "pertinenze" not in scelti:
        if set(dettaglio.declared.get("pertinenze", {}).get("value") or []) \
                == set(precompilato.declared.get("pertinenze", {}).get("value") or []):
            dettaglio.pertinenze_declared = False          # elenco invariato: nessuna pertinenza tolta
    ignoti_prima = {(_chiave(u["site_field"]), _chiave(str(u["raw"]))) for u in precompilato.unmapped}
    dettaglio.unmapped = [u for u in dettaglio.unmapped
                          if (_chiave(u["site_field"]), _chiave(str(u["raw"]))) not in ignoti_prima
                          or _chiave(u["site_field"]) in scelti]
    if "property_type" in dettaglio.unverified and "property_type" in precompilato.unverified \
            and same(dettaglio.unverified["property_type"]["raw"], precompilato.unverified["property_type"]["raw"]):
        dettaglio.unverified.pop("property_type")
    return tolti
