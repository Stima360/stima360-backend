"""CRM-OPS-2 - cataloghi del form Immobili: territorio, classi energetiche,
etichette delle tipologie, descrizione sintetica e codice.

TERRITORIO: REGIONE -> PROVINCIA -> COMUNE -> MICROZONA
-------------------------------------------------------
Fonte dei comuni e delle microzone: il portale pubblico https://www.stima360.it,
letto il 2026-10-01. Il form di stima della home (`#regione`, `#comune`,
`#microzona`) contiene in uno script inline:

  * `comuniAbruzzo` e `comuniMarche`: i comuni offerti per ciascuna regione;
  * `zone`: comune -> elenco delle microzone.

Lo stesso catalogo e' stato confrontato con le altre due copie pubblicate dal
portale e coincide voce per voce: il mini-form di /PianoVenditaStima360.html
(`zoneMini`) e la sitemap generata dal backend da `seo_microzone`
(/sitemap.xml, 80 pagine /valutazione/<comune>/<microzona>; le differenze sono
solo nella forma degli slug). Totale: 2 regioni, 25 comuni, 80 microzone. I
nomi sono copiati come li pubblica il portale, apostrofo tipografico compreso
("Sant’Omero", "Porto d’Ascoli"): sono gli stessi valori che il sito salva in
`stime_dettagliate.comune/microzona`.

Fonte delle province: il portale NON ha il livello provincia (va da regione a
comune) e il repository non contiene alcuna mappa comune -> provincia (lo
dichiara anche migrations/058). L'associazione e' presa dall'elenco ufficiale
ISTAT dei comuni italiani
(https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv,
edizione con last-modified 2024-01-26): per ciascuno dei 25 comuni la regione
ISTAT coincide con quella del portale. Il codice ISTAT e' riportato accanto a
ogni comune come riferimento verificabile; non viene salvato.

La provincia si salva come sigla automobilistica (TE, AP, FM, MC): e' la forma
della colonna esistente `properties.province` VARCHAR(10) e del valore storico
gia' presente ("TE").

CLASSE ENERGETICA
-----------------
A4, A3, A2, A1, B, C, D, E, F, G: l'elenco indicato dal brief, con fonte il
Rapporto ENEA sulla Certificazione Energetica degli Edifici. "Non indicata" e'
NULL: nessuna classe viene mai assegnata d'ufficio. Nel sistema non esiste
oggi nessuno stato del tipo "APE in corso" o "Esente", e qui non se ne
inventa nessuno: se arrivera', sara' un campo distinto, non un valore in piu'
di questo elenco.

VALORI STORICI
--------------
Le regole qui sotto si applicano a cio' che l'operatore INVIA. Un immobile
salvato prima di CRM-OPS-2 con un comune fuori catalogo, una provincia scritta
per esteso o una classe minuscola resta com'e': si apre, si modifica negli
altri campi, e nessuna conversione viene fatta in silenzio.
"""
from __future__ import annotations

from typing import Any

# --- Territorio ---------------------------------------------------------------

#: Regione -> provincia (sigla, nome ISTAT) -> comune (codice ISTAT, microzone).
#: Le microzone sono nell'ordine del portale.
TERRITORY: dict[str, dict[str, Any]] = {
    "Abruzzo": {
        "TE": {"name": "Teramo", "municipalities": {
            "Alba Adriatica": ("067001", ["Nord", "Villa Fiore", "Zona Basciani"]),
            "Ancarano": ("067002", ["Centro storico", "Contrade"]),
            "Civitella del Tronto": ("067017", ["Centro storico", "Contrade"]),
            "Colonnella": ("067019", ["Bivio", "Centro storico", "Contrade"]),
            "Controguerra": ("067020", ["Centro storico", "Contrade"]),
            "Corropoli": ("067021", ["Centro storico", "Bivio", "Contrade"]),
            "Martinsicuro": ("067047", ["Centro", "Villarosa", "Alto"]),
            "Nereto": ("067031", ["Centro storico", "Bivio", "Contrade"]),
            "Sant’Egidio alla Vibrata": ("067038", ["Centro", "Bivio", "Contrade"]),
            "Sant’Omero": ("067039", ["Centro storico", "Contrade"]),
            "Torano Nuovo": ("067042", ["Centro storico", "Contrade"]),
            "Tortoreto": ("067044", ["Lido Sud", "Lido Centro", "Lido Nord", "Alto"]),
        }},
    },
    "Marche": {
        "AP": {"name": "Ascoli Piceno", "municipalities": {
            "Cupra Marittima": ("044017", ["Marina / Lungomare", "Centro", "Castello"]),
            "Grottammare": ("044023", ["Centro / Lungomare", "Ascolani", "Valtesino", "Vecchio Incasato"]),
            "Massignano": ("044029", ["Marina di Massignano", "Centro / Collina"]),
            "San Benedetto del Tronto": ("044066", ["Sentina", "Porto d’Ascoli", "Centro / Lungomare",
                                                    "Paese Alto", "Agraria", "Ponterotto"]),
        }},
        "FM": {"name": "Fermo", "municipalities": {
            "Altidona": ("109001", ["Marina", "Borgo"]),
            "Campofilone": ("109004", ["Marina di Campofilone", "Borgo"]),
            "Fermo": ("109006", ["Marina Palmense", "Lido di Fermo", "Casabianca", "Lido Tre Archi",
                                 "San Tommaso", "Torre di Palme", "Ponte Nina", "Tre Camini",
                                 "Santa Maria a Mare"]),
            "Pedaso": ("109030", ["Centro-Mare / Lungomare", "Collina"]),
            "Porto San Giorgio": ("109033", ["Centro", "Lungomare Nord", "Lungomare Sud", "Ovest"]),
            "Porto Sant’Elpidio": ("109034", ["Centro", "Faleriense", "Corva", "Lungomare"]),
        }},
        "MC": {"name": "Macerata", "municipalities": {
            "Civitanova Marche": ("043013", ["Sud", "Centro", "Nord / Fontespina", "San Marone",
                                             "Civitanova Alta"]),
            "Porto Recanati": ("043042", ["Centro / Lungomare", "Scossicci", "Montarice"]),
            "Potenza Picena": ("043043", ["Porto Potenza Picena (zona mare)", "Centro"]),
        }},
    },
}

TERRITORY_SOURCES = {
    "municipalities_and_microzones": "https://www.stima360.it (form di stima, letto il 2026-10-01)",
    "provinces": "ISTAT, Elenco comuni italiani (last-modified 2024-01-26)",
}


def _province_of(province: str) -> tuple[str, dict[str, Any]] | None:
    for region, provinces in TERRITORY.items():
        if province in provinces:
            return region, provinces[province]
    return None


def _municipality(city: str) -> tuple[str, str, list[str]] | None:
    """(regione, sigla provincia, microzone) di un comune del catalogo."""
    for region, provinces in TERRITORY.items():
        for code, province in provinces.items():
            if city in province["municipalities"]:
                return region, code, province["municipalities"][city][1]
    return None


def territory_tree() -> list[dict[str, Any]]:
    """Il catalogo per il form: regioni, province, comuni (alfabetici) e
    microzone (nell'ordine del portale)."""
    return [
        {"name": region, "provinces": [
            {"code": code, "name": province["name"], "municipalities": [
                {"name": name, "istat_code": istat, "microzones": list(zones)}
                for name, (istat, zones) in sorted(province["municipalities"].items(),
                                                   key=lambda item: item[0].casefold())
            ]}
            for code, province in provinces.items()
        ]}
        for region, provinces in TERRITORY.items()
    ]


def validate_location(region: str | None, province: str | None,
                      city: str | None, microzone: str | None) -> None:
    """Ogni livello indicato deve stare nel catalogo e dentro il livello sopra.

    I livelli sono facoltativi (un immobile puo' essere salvato con la sola
    regione), ma una microzona senza comune non ha senso. Solleva ValueError
    con un messaggio leggibile; il chiamante lo traduce in 400.
    """
    if region is not None and region not in TERRITORY:
        raise ValueError(f"Regione non presente nel catalogo: {region}")
    if province is not None:
        found = _province_of(province)
        if found is None:
            raise ValueError(f"Provincia non presente nel catalogo: {province}")
        if region is not None and found[0] != region:
            raise ValueError(f"La provincia {province} non appartiene alla regione {region}")
    if city is not None:
        found_city = _municipality(city)
        if found_city is None:
            raise ValueError(f"Comune non presente nel catalogo: {city}")
        if region is not None and found_city[0] != region:
            raise ValueError(f"Il comune {city} non appartiene alla regione {region}")
        if province is not None and found_city[1] != province:
            raise ValueError(f"Il comune {city} non appartiene alla provincia {province}")
    if microzone is not None:
        if city is None:
            raise ValueError("Una microzona richiede il comune")
        if microzone not in _municipality(city)[2]:
            raise ValueError(f"La microzona {microzone} non appartiene al comune {city}")


# --- Classe energetica ----------------------------------------------------------

ENERGY_CLASSES: tuple[str, ...] = ("A4", "A3", "A2", "A1", "B", "C", "D", "E", "F", "G")


def validate_energy_class(value: str | None) -> None:
    if value is not None and value not in ENERGY_CLASSES:
        raise ValueError("Classe energetica non valida: usa A4, A3, A2, A1, B, C, D, E, F o G")


# --- Tipologie: etichette leggibili --------------------------------------------

#: Le chiavi sono esattamente property/enums.py::PROPERTY_TYPES.
PROPERTY_TYPE_LABELS: dict[str, str] = {
    "apartment": "Appartamento", "villa": "Villa", "house": "Casa indipendente",
    "rustic": "Rustico", "land": "Terreno", "commercial": "Locale commerciale",
    "garage": "Garage / box", "office": "Ufficio", "building": "Stabile", "other": "Altro",
}


# --- Descrizione sintetica e codice --------------------------------------------

TITLE_MAX = 200


def generated_title(data: dict[str, Any]) -> str:
    """La descrizione sintetica che sostituisce il titolo manuale.

    Solo dati dell'immobile, nell'ordine in cui un operatore lo riconosce:
    tipologia, comune (microzona), indirizzo e civico. Esempio:
    "Appartamento · Tortoreto (Lido Sud) · Via Roma 12".
    """
    label = PROPERTY_TYPE_LABELS.get(data.get("property_type") or "apartment", "Immobile")
    parts = [label]
    city = (data.get("city") or "").strip()
    microzone = (data.get("microzone") or "").strip()
    if city:
        parts.append(f"{city} ({microzone})" if microzone else city)
    street = " ".join(x for x in ((data.get("address") or "").strip(),
                                  (data.get("civic_number") or "").strip()) if x)
    if street:
        parts.append(street)
    return " · ".join(parts)[:TITLE_MAX]


#: Campi da cui dipende la descrizione: se nessuno cambia, non la si tocca.
TITLE_SOURCE_FIELDS = ("property_type", "city", "microzone", "address", "civic_number")

CODE_PREFIX = "IMM-"


def generated_code(property_id: int, attempt: int = 0) -> str:
    """Codice di riferimento: IMM-<id>, con suffisso solo se gia' occupato da
    un codice storico inserito a mano. Mai rigenerato dopo l'assegnazione."""
    base = f"{CODE_PREFIX}{property_id}"
    return base if attempt == 0 else f"{base}-{attempt + 1}"
