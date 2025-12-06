# /Users/censorsrc/Desktop/Stima360/backend/valuation.py
from typing import Dict, Any, Optional
import math
from decimal import Decimal

# ---------------------------
# Base €/mq per comune+microzona
# ---------------------------
BASE_MQ = {
    "Alba Adriatica": {
        "Nord": 1650,
        "Villa Fiore": 1850,
        "Zona Basciani": 1400,
    },

    "Tortoreto": {
        # B5 – via Indipendenza ecc: min ~1450
        "Lido Sud":   1750,
        # B4 – Lungomare Sirena: min ~1650
        "Lido Centro": 1950,
        # fascia intermedia tra B4 e B5
        "Lido Nord":  1600,
        # Tortoreto Alto: min ~1097 → 1100 arrotondato
        "Alto":       1100,
    },

    "Martinsicuro": {
        # min ~874 → stiamo bassi
        "Centro":    1500,
        "Villarosa": 1400,
        "Alto":      1150,
    },
}


def get_base_mq(comune: str, microzona: str) -> float:
    return float(BASE_MQ.get(comune, {}).get(microzona, 0.0))

# ---------------------------
# Coefficienti tipologia
# ---------------------------
def coeff_tipologia(tipologia: str) -> float:
    t = (tipologia or "").strip().lower()
    if t == "appartamento":
        return 1.00
    if t == "villa":
        return 1.20
    if t == "rustico":
        return 0.80
    # Altro / default
    return 1.00

# ---------------------------
# Bagni
# ---------------------------
def coeff_bagni(n_bagni: int) -> float:
    try:
        n = int(n_bagni)
    except Exception:
        n = 0
    return 1.10 if n >= 2 else 1.00

# ---------------------------
# Locali
# ---------------------------
def coeff_locali(locali: str) -> float:
    """
    Leggero coeff in base al numero di locali.
    Accetta sia "3", sia "Trilocale", ecc.
    """
    txt = (locali or "").strip().lower()
    n = 0

    # numero diretto
    if txt.isdigit():
        n = int(txt)
    else:
        if "mono" in txt:
            n = 1
        elif "bi" in txt:
            n = 2
        elif "tri" in txt:
            n = 3
        elif "quadri" in txt:
            n = 4
        elif "penta" in txt or "5" in txt:
            n = 5

    if n <= 1:
        return 0.96
    if n in (2, 3):
        return 1.00
    if n == 4:
        return 1.03
    if n >= 5:
        return 1.05
    return 1.00

# ---------------------------
# Ascensore (micro bonus separato)
# ---------------------------
def coeff_ascensore(ascensore: str, piano: str) -> float:
    """
    Micro bonus se c'è ascensore da 2° piano in su.
    Il grosso dell'effetto rimane dentro coeff_piano.
    """
    val = (ascensore or "").strip().lower()
    has_lift = val in ("si", "sì", "true", "1", "yes", "y")

    kind, num = _parse_piano(piano)
    if kind != "numero" or num is None:
        return 1.00

    if num >= 2 and has_lift:
        return 1.02

    return 1.00

# ---------------------------
# Anno
# ---------------------------
def coeff_anno(anno: int) -> float:
    try:
        a = int(anno)
    except Exception:
        return 1.00

    # Nuovi valori più realistici per immobili recenti
    if a >= 2025:
        return 1.28
    if a == 2024:
        return 1.26
    if a == 2023:
        return 1.24
    if a == 2022:
        return 1.22
    if a == 2021:
        return 1.20

    # Recenti (2010–2020)
    if 2010 <= a <= 2020:
        return 1.15

    # Buona modernità (2005–2009)
    if 2005 <= a <= 2009:
        return 1.12

    # Ottime condizioni (1995–2004)
    if 1995 <= a <= 2004:
        return 1.06

    # Standard
    if 1980 <= a <= 1994:
        return 1.00
    if 1970 <= a <= 1979:
        return 0.92
    if 1960 <= a <= 1969:
        return 0.88
    if 1950 <= a <= 1959:
        return 0.82

    # Molto vecchio o sconosciuto
    return 1.00



# ---------------------------
# Stato
# ---------------------------
def coeff_stato(stato: str) -> float:
    s = (stato or "").strip().lower()
    if s == "nuovo":         return 1.05   # prima 1.15
    if s == "ristrutturato": return 1.05   # prima 1.10
    if s == "buono":         return 1.00
    if s == "scarso":        return 0.85   # prima 0.80
    if s == "grezzo":        return 0.80   # prima 0.70
    return 1.00


# ---------------------------
# Mare
# ---------------------------
def _posizione_coeff(pos: str) -> float:
    p = (pos or "").strip().lower()
    if p == "frontemare": return 1.15   # prima 1.30
    if p == "seconda":    return 1.08   # prima 1.20
    return 1.00

def _distanza_coeff(dist: str) -> float:
    d = (dist or "").strip().lower()
    if d == "0-100":     return 1.15    # prima 1.15
    if d == "100-300":   return 1.03
    if d == "300-500":   return 1.01
    if d == "500-1000":  return 1.00
    return 0.97          # >1000

def _barriera_coeff(bar: str) -> float:
    b = (bar or "").strip().lower()
    return 0.90 if b == "si" or b == "sì" else 1.00

def _vista_coeff(vista: str) -> float:
    v = (vista or "").strip().lower()

    # Normalizza i nomi
    if v == "vista":
        v = "panoramica"

    if v == "panoramica":
        return 1.10
    if v == "parziale":
        return 1.04
    if v == "scarsa":
        return 1.02

    return 1.00


def coeff_mare(posizione: str, distanza: str, barriera: str, vista: str) -> float:
    return _posizione_coeff(posizione) * _distanza_coeff(distanza) * _barriera_coeff(barriera) * _vista_coeff(vista)


def normalize_vista_mare(vista_yn: str, vista_det: str, vista_raw: str = "") -> str:
    """
    Converte (vistaMareYN, vistaMareDettaglio, vistaMare) in una delle categorie:
    - 'panoramica'
    - 'parziale'
    - 'scarsa'
    - '' (nessuna vista)
    Priorità:
      1. se vista_raw è valorizzato lo usa così com'è (retrocompatibilità)
      2. altrimenti deriva da YN + dettaglio
    """
    # Se già arriva vistaMare 'puro', usiamo quello (per non rompere nulla)
    v0 = (vista_raw or "").strip().lower()
    if v0 in ("panoramica", "parziale", "scarsa"):
        return v0

    yn = (vista_yn or "").strip().lower()
    det = (vista_det or "").strip().lower()

    # Se non c'è vista
    if yn in ("no", "non", "", "0", "false"):
        return ""

    # C'è vista: decidiamo dal dettaglio
    if any(k in det for k in ("piena", "fronte", "totale", "panoram")):
        return "panoramica"
    if any(k in det for k in ("laterale", "angolare")):
        return "parziale"
    if any(k in det for k in ("scorcio", "parziale", "limitata", "scarsa")):
        return "scarsa"

    # fallback: vista sì ma non chiaro → parziale
    return "parziale"

# ---------------------------
# Piano (include bonus/penalità)
# ---------------------------
def _parse_piano(piano: str):
    p = (piano or "").strip().lower()
    if p in ("terra", "piano terra"):
        return "terra", 0
    if p in ("ultimo", "ult", "attico"):
        return "ultimo", None
    # numerico?
    try:
        n = int(p)
        return "numero", n
    except Exception:
        return "numero", None

def coeff_piano(piano: str, ascensore: str, posizioneMare: str, vistaMare: str) -> float:
    kind, num = _parse_piano(piano)
    val = str(ascensore).strip().lower() if ascensore is not None else ""
    has_lift = val in ("si", "sì", "true", "1", "yes", "y")

    coeff = 1.00

    if kind == "terra":
        coeff *= 1.03   # prima 1.05
    elif kind == "numero":
        if num is None:
            pass
        elif num in (1, 2):
            coeff *= 1.00
        elif num >= 3:
            if has_lift:
                # +2% per piano oltre il 2°
                coeff *= (1.00 + 0.02 * (num - 2))   # prima 3%
            else:
                if num == 3:
                    coeff *= 0.75   # meno punitivo
                elif num == 4:
                    coeff *= 0.70
                else:
                    coeff *= 0.60
    elif kind == "ultimo":
        if has_lift:
            coeff *= 1.06   # prima 1.10

    # ❌ NIENTE più extra frontemare+ultimo+vista
    return coeff

# ---------------------------
# Indirizzo
# ---------------------------
def coeff_indirizzo(via: str) -> float:
    """
    Piccola correzione in base alla via.
    """
    v = (via or "").strip().lower()
    if not v:
        return 1.00

    if "lungomare" in v:
        return 1.05
    if "sirena" in v:
        return 1.03
    if "nazionale" in v or "statale" in v:
        return 0.97

    return 1.00

# ---------------------------
# Altro descrizione
# ---------------------------
def coeff_altro_descrizione(altro: str) -> float:
    """
    Leggero aggiustamento sulla descrizione libera.
    """
    t = (altro or "").strip().lower()
    if not t:
        return 1.00

    if any(k in t for k in ("lusso", "signorile", "finemente", "di pregio")):
        return 1.03

    if any(k in t for k in ("da ristrutturare", "da completare", "grezzo", "allo stato originale")):
        return 0.95

    if "vista mare totale" in t or "vista mare panoramica" in t:
        # vista già entra nei coeff mare, ma diamo un +1% extra
        return 1.01

    return 1.00

# ---------------------------
# Pertinenze (somma in €)
# ---------------------------
def valore_pertinenze(flags: Dict[str, Any], base_mq: float, posizioneMare: str) -> float:
    fm = (posizioneMare or "").strip().lower() == "frontemare"
    euro = 0.0

    # Garage (già ok)
    if flags.get("Garage"):
        try:
            mq_gar = float(flags.get("mqGarage") or 0)
        except Exception:
            mq_gar = 0.0

        if mq_gar > 0:
            garage_base = max(18000.0, 500.0 * mq_gar)
        else:
            garage_base = 10000.0

        if fm:
            garage_base *= 1.15

        euro += garage_base

    # Posto Auto — con mq
    if flags.get("Posto Auto"):
        try:
            mq_pa = float(flags.get("mqPostoAuto") or 0)
        except:
            mq_pa = 0.0

        if mq_pa > 0:
            euro += mq_pa * 850.0
        else:
            euro += 10000.0

    # Cantina — con mq
    if flags.get("Cantina"):
        try:
            mq_ca = float(flags.get("mqCantina") or 0)
        except:
            mq_ca = 0.0

        if mq_ca > 0:
            euro += mq_ca * 550.0
        else:
            euro += 10000.0

    # Soffitta — con mq
    if flags.get("Soffitta"):
        try:
            mq_sf = float(flags.get("mqSoffitta") or 0)
        except:
            mq_sf = 0.0

        if mq_sf > 0:
            euro += mq_sf * 200.0
        else:
            euro += 20000.0

    # Taverna — con mq
    if flags.get("Taverna"):
        try:
            mq_tav = float(flags.get("mqTaverna") or 0)
        except:
            mq_tav = 0.0

        if mq_tav > 0:
            euro += mq_tav * 1150.0
        else:
            euro += 12000.0

    # Balconi — numerati
    if flags.get("Balconi"):
        try:
            n_bal = int(flags.get("numBalconi") or 0)
        except:
            n_bal = 0

        if n_bal > 0:
            euro += n_bal * 1000.0
        else:
            euro += 3000.0

    # Terrazzo — con mq
    if flags.get("Terrazzo"):
        try:
            mq_ter = float(flags.get("mqTerrazzo") or 0)
        except:
            mq_ter = 0.0

        if mq_ter > 0:
            euro += mq_ter * 250.0
        else:
            euro += 4500.0

    # Giardino — già ok
    if flags.get("Giardino"):
        try:
            mq_g = float(flags.get("mqGiardino") or 0)
        except:
            mq_g = 0.0

        euro += mq_g * (base_mq / 5.0)

    # Extra da testo generico pertinenze (es. Piscina, posto moto, bici)
    text = (flags.get("pertinenze_text") or "").strip().lower()
    if text:
        if "piscina" in text:
            euro += 15000.0
        if "posto moto" in text:
            euro += 3000.0
        if "posto bici" in text or "posto bicicle" in text:
            euro += 1000.0

    return euro



# ---------------------------
# Prezzi e totale
# ---------------------------
def prezzo_mq_finale(
    base_mq: float,
    tipologia: str,
    piano: str,
    ascensore: str,
    locali: str,
    bagni: str,
    anno: str,
    stato: str,
    posizioneMare: str,
    distanzaMare: str,
    barrieraMare: str,
    vistaMare: str,
    via: str = "",
    altro_descrizione: str = "",
) -> float:
    if base_mq <= 0:
        return 0.0

    c_tip   = coeff_tipologia(tipologia)
    c_piano = coeff_piano(piano, ascensore, posizioneMare, vistaMare)
    c_bagni = coeff_bagni(int(bagni) if f"{bagni}".strip().isdigit() else 0)
    c_anno  = coeff_anno(int(anno) if f"{anno}".strip().isdigit() else 0)
    c_stato = coeff_stato(stato)
    c_mare  = coeff_mare(posizioneMare, distanzaMare, barrieraMare, vistaMare)
    c_loc   = coeff_locali(locali)
    c_asc   = coeff_ascensore(ascensore, piano)
    c_via   = coeff_indirizzo(via)
    c_altro = coeff_altro_descrizione(altro_descrizione)

    coeff_tot = (
        c_tip *
        c_piano *
        c_bagni *
        c_anno *
        c_stato *
        c_mare *
        c_loc *
        c_asc *
        c_via *
        c_altro
    )

    # 🔒 CAP GLOBALE: non meno di 0.65x, non più di 1.80x
    coeff_tot = max(0.50, min(coeff_tot, 1.80))

    return base_mq * coeff_tot


def valore_totale(prezzo_mq_finale_: float, mq: float, pertinenze_euro: float) -> float:
    try:
        m = float(mq)
    except Exception:
        m = 0.0
    return m * prezzo_mq_finale_ + (pertinenze_euro or 0.0)

# ---------------------------
# Funzione comoda: input dal payload del form
# ---------------------------
def compute_from_payload(payload: Dict[str, Any]) -> Dict[str, float]:
    comune = payload.get("comune", "")
    microzona = payload.get("microzona", "")
    base = get_base_mq(comune, microzona)

    # 🔹 Normalizziamo la vista mare qui
    vista_norm = normalize_vista_mare(
        vista_yn   = payload.get("vistaMareYN", ""),
        vista_det  = payload.get("vistaMareDettaglio", ""),
        vista_raw  = payload.get("vistaMare", ""),   # retrocompatibilità
    )

    prezzo_mq = prezzo_mq_finale(
        base_mq=base,
        tipologia=payload.get("tipologia", ""),
        piano=payload.get("piano", ""),
        ascensore=payload.get("ascensore", ""),
        locali=payload.get("locali", ""),
        bagni=payload.get("bagni", "1"),
        anno=payload.get("anno", ""),
        stato=payload.get("stato", ""),
        posizioneMare=payload.get("posizioneMare", ""),
        distanzaMare=payload.get("distanzaMare", ""),
        barrieraMare=payload.get("barrieraMare", ""),
        vistaMare=vista_norm,
        via=payload.get("via", ""),
        altro_descrizione=payload.get("altroDescrizione", ""),
    )
    
    flags = {
        "Garage": "Garage" in (payload.get("pertinenze", "") or ""),
        "mqGarage": payload.get("mqGarage"),
        "Posto Auto": "Posto Auto" in (payload.get("pertinenze", "") or ""),
        "Cantina": "Cantina" in (payload.get("pertinenze", "") or ""),
        "Soffitta": "Soffitta" in (payload.get("pertinenze", "") or ""),
        "Taverna": "Taverna" in (payload.get("pertinenze", "") or ""),
        "Balconi": "Balconi" in (payload.get("pertinenze", "") or ""),
        "Terrazzo": "Terrazzo" in (payload.get("pertinenze", "") or ""),
        "Giardino": "Giardino" in (payload.get("pertinenze", "") or ""),
        "mqGiardino": payload.get("mqGiardino"),
        # testo completo pertinenze (per piscina/posto moto/bici)
        "pertinenze_text": payload.get("pertinenze", ""),
    }
    flags.update({
        "mqCantina": payload.get("mqCantina"),
        "mqPostoAuto": payload.get("mqPostoAuto"),
        "mqTaverna": payload.get("mqTaverna"),
        "mqSoffitta": payload.get("mqSoffitta"),
        "mqTerrazzo": payload.get("mqTerrazzo"),
        "numBalconi": payload.get("numBalconi"),
    })

    pert_eur = valore_pertinenze(flags, base_mq=base, posizioneMare=payload.get("posizioneMare", ""))

    try:
        mq_val = float(payload.get("mq") or 0)
    except Exception:
        mq_val = 0.0

    totale = valore_totale(prezzo_mq, mq_val, pert_eur)

    return {
        "base_mq": round(base, 2),
        "eur_mq_finale": round(prezzo_mq, 2),
        "valore_pertinenze": round(pert_eur, 2),
        "price_exact": round(totale, 0),
        "mq_calcolati": round(mq_val, 0),
    }


# ---------------------------
# Utility semplici per la risposta finale
# ---------------------------

def _to_float(x) -> float:
    """Converte in float, se fallisce restituisce 0.0 (robusto)."""
    try:
        return float(x)
    except Exception:
        return 0.0

def build_response(payload: Dict[str, Any],
                   stima_id: int,
                   pdf_url: str,
                   cover_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Costruisce il JSON da mandare al front-end:
    - id, pdf_url, cover_url
    - i numeri calcolati (identici a quelli usati per il PDF)
    """
    vals = compute_from_payload(payload)

    return {
        "success": True,
        "status": "ok",
        "id": int(stima_id),
        "pdf_url": pdf_url,
        "cover_url": cover_url,

        # numeri per la UI (già arrotondati in compute_from_payload)
        "price_exact":        _to_float(vals.get("price_exact")),
        "eur_mq_finale":      _to_float(vals.get("eur_mq_finale")),
        "valore_pertinenze":  _to_float(vals.get("valore_pertinenze")),
        "mq_calcolati":       _to_float(vals.get("mq_calcolati")),
    }
