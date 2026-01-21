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

    # =========================
    # 🔥 COMUNI ENTROTERRA
    # =========================

    "Colonnella": {
        "Centro storico": 1050,
        "Contrade": 1000,
        "Bivio": 1200,
    },

    "Controguerra": {
        "Centro storico": 825,
        "Contrade": 800,
    },

    "Nereto": {
        "Centro storico": 1050,
        "Contrade": 1000,
        "Bivio": 1250,
    },
    "Corropoli": {
        "Centro storico": 950,
        "Bivio": 1150,
        "Contrade": 900,
    },


    "Ancarano": {
        "Centro storico": 900,
        "Contrade": 850,
    },

    "Civitella del Tronto": {
        "Centro storico": 800,
        "Contrade": 750,
    },

    "Sant’Egidio alla Vibrata": {
        "Centro": 1100,
        "Bivio": 1250,
        "Contrade": 950,
    },

    "Sant’Omero": {
        "Centro storico": 1000,
        "Contrade": 900,
    },

    "Torano Nuovo": {
        "Centro storico": 850,
        "Contrade": 800,
    },

}

def get_base_mq(comune: str, microzona: str) -> float:
    return float(BASE_MQ.get(comune, {}).get(microzona, 0.0))


# --------------------------------------------------
# COEFFICIENTE ANNO (curva continua)
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
# COEFF RUSTICO PROGRESSIVO PER SUPERFICIE
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
# CAP €/mq PER RUSTICI (anti-follia)
# --------------------------------------------------
def cap_rustico_eur_mq(anno: int, mq: float) -> float:
    # grandi superfici
    if mq >= 800:
        return 300
    if mq >= 400:
        return 450
    if mq >= 200:
        return 600

    # piccoli rustici: crescono con l'anno
    if anno >= 2020:
        return 900
    elif anno >= 2010:
        return 750
    else:
        return 600


# --------------------------------------------------
# PREZZO €/mq BASE (solo zona + anno)
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
    comune    = payload.get("comune", "")
    microzona = payload.get("microzona", "")
    tipologia = (payload.get("tipologia") or "").lower().strip()

    try:
        anno = int(payload.get("anno", 0))
    except:
        anno = 0

    try:
        mq = float(payload.get("mq") or 0)
    except:
        mq = 0.0

    # €/mq base (zona + anno)
    eur_mq = prezzo_mq_base(comune, microzona, anno)

    # coeff tipologia (UNA SOLA VOLTA)
    coeff_tipologia = 1.0
    if "villa" in tipologia:
        coeff_tipologia = 1.20
    elif "rustico" in tipologia:
        coeff_tipologia = coeff_rustico_superficie(mq)

    eur_mq_finale = eur_mq * coeff_tipologia

    # CAP rustico €/mq
    if "rustico" in tipologia:
        cap = cap_rustico_eur_mq(anno, mq)
        eur_mq_finale = min(eur_mq_finale, cap)

    valore_finale = eur_mq_finale * mq

    return {
        "base_mq": round(get_base_mq(comune, microzona), 2),
        "eur_mq_base": round(eur_mq, 2),            # tecnico
        "eur_mq_visuale": round(eur_mq_finale, 0),  # MOSTRA QUESTO
        "mq": round(mq, 0),
        "price_base": round(valore_finale, 0),
    }
