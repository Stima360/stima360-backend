# valuation_base.py
from typing import Dict, Any

# --------------------------------------------------
# BASE €/mq (copiati da valuation.py, solo questi)
# --------------------------------------------------
BASE_MQ = {
    "Alba Adriatica": {
        "Nord": 1600,
        "Villa Fiore": 1700,
        "Zona Basciani": 1400,
    },
    "Tortoreto": {
        "Lido Sud": 1650,
        "Lido Centro": 1800,
        "Lido Nord": 1600,
        "Alto": 1100,
    },
    "Martinsicuro": {
        "Centro": 1500,
        "Villarosa": 1400,
        "Alto": 1150,
    },
}


def get_base_mq(comune: str, microzona: str) -> float:
    return float(BASE_MQ.get(comune, {}).get(microzona, 0.0))


# --------------------------------------------------
# Coefficiente ANNO (copiato identico)
# --------------------------------------------------
def coeff_anno(anno: int) -> float:
    try:
        a = int(anno)
    except Exception:
        return 1.00

    # ---- PERDITE INIZIALI ----
    if a <= 1950:
        return 0.40
    if a <= 1960:
        return 0.45
    if a <= 1965:
        return 0.50
    if a <= 1970:
        return 0.60
    if a <= 1975:
        return 0.80

    # ---- 1975 → 1980 : da 0.80 a 1.00 (+5%/anno circa) ----
    if a <= 1980:
        return round(0.80 + (a - 1975) * (0.20 / 5), 3)

    # ---- 1980 → 1990 : +10% totale (1%/anno) ----
    if a <= 1990:
        return round(1.00 + (a - 1980) * (0.10 / 10), 3)

    # ---- 1990 → 2000 : +20% totale (2%/anno) ----
    if a <= 2000:
        return round(1.10 + (a - 1990) * (0.20 / 10), 3)

    # ---- 2000 → 2010 : +20% totale ----
    if a <= 2010:
        return round(1.30 + (a - 2000) * (0.20 / 10), 3)

    # ---- 2010 → 2015 : +40% totale ----
    if a <= 2015:
        return round(1.50 + (a - 2010) * (0.40 / 5), 3)

    # ---- 2015 → 2020 : +20% totale ----
    if a <= 2020:
        return round(1.90 + (a - 2015) * (0.20 / 5), 3)

    # ---- 2020 → 2025 : verso 2.00 (100%) ----
    if a <= 2025:
        return round(2.10 - (2025 - a) * (0.10 / 5), 3)

    return 2.20

# --------------------------------------------------
# STIMA BASE €/mq
# --------------------------------------------------
def prezzo_mq_base(
    comune: str,
    microzona: str,
    anno: str
) -> float:
    base = get_base_mq(comune, microzona)
    if base <= 0:
        return 0.0

    c_anno = coeff_anno(anno)

    return base * c_anno



# --------------------------------------------------
# VALORE TOTALE BASE
# --------------------------------------------------
def valore_base(
    prezzo_mq: float,
    mq: float
) -> float:
    try:
        m = float(mq)
    except Exception:
        m = 0.0
    return prezzo_mq * m


# --------------------------------------------------
# FUNZIONE COMODA DAL PAYLOAD
# --------------------------------------------------
def compute_base_from_payload(payload: Dict[str, Any]) -> Dict[str, float]:
    comune = payload.get("comune", "")
    microzona = payload.get("microzona", "")
    anno = payload.get("anno", "")

    try:
        mq = float(payload.get("mq") or 0)
    except Exception:
        mq = 0.0

    prezzo_mq = prezzo_mq_base(
        comune=comune,
        microzona=microzona,
        anno=anno,
    )

    totale = valore_base(prezzo_mq, mq)

    return {
        "base_mq": round(get_base_mq(comune, microzona), 2),
        "eur_mq_base": round(prezzo_mq, 2),
        "mq": round(mq, 0),
        "price_base": round(totale, 0),
    }
