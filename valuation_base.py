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

    if a >= 2025: return 1.28
    if a == 2024: return 1.26
    if a == 2023: return 1.24
    if a == 2022: return 1.22
    if a == 2021: return 1.20

    if 2010 <= a <= 2020: return 1.15
    if 2005 <= a <= 2009: return 1.12
    if 1995 <= a <= 2004: return 1.06
    if 1980 <= a <= 1994: return 1.00
    if 1970 <= a <= 1979: return 0.92
    if 1960 <= a <= 1969: return 0.88
    if 1950 <= a <= 1959: return 0.82

    return 1.00


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

    # cap più stretto: stima prudente
    coeff_tot = max(0.80, min(c_anno, 1.35))

    return base * coeff_tot


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
