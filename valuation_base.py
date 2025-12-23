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

    # 1950 → -60%
    if a <= 1950:
        return 0.40

    # 1985 in poi → valore pieno
    if a >= 1985:
        return 1.00

    # Crescita graduale tra 1950 e 1985
    coeff = 0.40 + ((a - 1950) / (1985 - 1950)) * (1.00 - 0.40)

    return round(coeff, 3)

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
