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
        a = float(anno)
    except Exception:
        return 1.00

    # Ancora minima
    if a <= 1950:
        return 0.40

    # Coppie (anno, coeff) — SOLO PUNTI CHIAVE
    points = [
        (1950, 0.40),
        (1965, 0.45),
        (1970, 0.60),
        (1975, 0.80),
        (1990, 1.00),  # zero spostato qui
        (2000, 1.30),
        (2010, 1.50),
        (2015, 1.90),
        (2020, 2.10),
        (2025, 2.20),
    ]


    # Oltre il massimo
    if a >= 2025:
        return 2.20

    # Interpolazione lineare CONTINUA
    for i in range(len(points) - 1):
        y0, c0 = points[i]
        y1, c1 = points[i + 1]

        if y0 <= a <= y1:
            t = (a - y0) / (y1 - y0)
            coeff = c0 + t * (c1 - c0)
            return round(coeff, 4)

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
    tipologia = (payload.get("tipologia") or "").lower().strip()

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

    # ----------------------------------
    # CORREZIONE PER TIPOLOGIA
    # ----------------------------------
    if "villa" in tipologia:
        totale *= 1.20
    elif "rustico" in tipologia:
        totale *= 0.40

    return {
        "base_mq": round(get_base_mq(comune, microzona), 2),
        "eur_mq_base": round(prezzo_mq, 2),
        "mq": round(mq, 0),
        "price_base": round(totale, 0),
    }
