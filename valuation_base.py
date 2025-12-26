from typing import Dict, Any

# --------------------------------------------------
# BASE €/mq
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
# COEFFICIENTE ANNO
# --------------------------------------------------
def coeff_anno(anno: int) -> float:
    try:
        a = float(anno)
    except:
        return 1.00

    if a <= 1950:
        return 0.40

    points = [
        (1950, 0.40),
        (1965, 0.45),
        (1970, 0.60),
        (1975, 0.80),
        (1990, 1.00),
        (2000, 1.30),
        (2010, 1.50),
        (2015, 1.90),
        (2020, 2.10),
        (2025, 2.20),
    ]

    if a >= 2025:
        return 2.20

    for i in range(len(points) - 1):
        y0, c0 = points[i]
        y1, c1 = points[i + 1]
        if y0 <= a <= y1:
            t = (a - y0) / (y1 - y0)
            return round(c0 + t * (c1 - c0), 4)

    return 1.00


# --------------------------------------------------
# COEFFICIENTE RUSTICO PROGRESSIVO PER MQ
# --------------------------------------------------
def coeff_rustico_superficie(mq: float) -> float:
    if mq <= 100:
        return 0.60
    elif mq <= 200:
        return 0.55
    elif mq <= 400:
        return 0.45
    elif mq <= 600:
        return 0.40
    else:
        return 0.35


# --------------------------------------------------
# PREZZO €/mq BASE
# --------------------------------------------------
def prezzo_mq_base(comune: str, microzona: str, anno: int) -> float:
    base = get_base_mq(comune, microzona)
    if base <= 0:
        return 0.0
    return base * coeff_anno(anno)


# --------------------------------------------------
# FUNZIONE PRINCIPALE
# --------------------------------------------------
def compute_base_from_payload(payload: Dict[str, Any]) -> Dict[str, float]:
    comune     = payload.get("comune", "")
    microzona  = payload.get("microzona", "")
    anno       = int(payload.get("anno", 0))
    tipologia  = (payload.get("tipologia") or "").lower().strip()

    try:
        mq = float(payload.get("mq") or 0)
    except:
        mq = 0.0

    # €/mq con solo coeff anno
    eur_mq = prezzo_mq_base(comune, microzona, anno)

    # coeff tipologia (UNA SOLA VOLTA)
    coeff_tipologia = 1.0

    if "villa" in tipologia:
        coeff_tipologia = 1.20
    elif "rustico" in tipologia:
        coeff_tipologia = coeff_rustico_superficie(mq)

    eur_mq_finale = eur_mq * coeff_tipologia
    valore_finale = eur_mq_finale * mq

    return {
        "base_mq": round(get_base_mq(comune, microzona), 2),
        "eur_mq_base": round(eur_mq, 2),            # tecnico
        "eur_mq_visuale": round(eur_mq_finale, 0),  # MOSTRA QUESTO
        "mq": round(mq, 0),
        "price_base": round(valore_finale, 0),
    }
