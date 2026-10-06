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
#: CENSIMENTO-1 Fase 2: `storage` (Cantina / Deposito) e' la tipologia nuova
#: approvata per le pertinenze autonome (decisione 1 del progetto).
PROPERTY_TYPE_LABELS: dict[str, str] = {
    "apartment": "Appartamento", "villa": "Villa", "house": "Casa indipendente",
    "rustic": "Rustico", "land": "Terreno", "commercial": "Locale commerciale",
    "garage": "Garage / box", "office": "Ufficio", "building": "Stabile",
    "storage": "Cantina / Deposito", "other": "Altro",
}


# --- Categorie catastali (CENSIMENTO-1 Fase 2) ----------------------------------

# Il quadro generale delle categorie catastali, 52 voci: gruppi A (11), B (8),
# C (7), D (10), E (9) e F (7).
#
# Fonti (verifica documentale chiusa il 2026-10-03, review della Fase 2):
# * quadro A-E: scheda del Comune di Padova (https://padovanet.it/categorie-catastali),
#   coincidente con l'elenco di Money.it 2025 e con il quadro riportato nelle
#   "Linee guida operative Docfa" dell'Agenzia delle Entrate - Direzione regionale
#   della Lombardia (v. 1.0, 16/01/2019, pubblicate dal Collegio Geometri di
#   Milano: https://geometri.mi.it/wp-content/uploads/2022/05/vademecum-docfa-finale_compressed.pdf);
# * F/1-F/5: D.M. 2 gennaio 1998 n. 28, art. 3 c. 2 (G.U. n. 45 del 24/02/1998,
#   https://www.normattiva.it/eli/id/1998/02/24/098G0063/CONSOLIDATED) - unita'
#   censite "senza attribuzione di rendita catastale"; cosi' anche MEF,
#   Risoluzione n. 8/DF del 22/07/2013 ("ad esse non e' associabile una
#   rendita catastale");
# * F/6 "fabbricato in attesa di dichiarazione": Agenzia del Territorio,
#   circolare n. 1/T dell'8 maggio 2009, prot. 25818 (Servizio di
#   documentazione tributaria: https://def.finanze.it/DocTribFrontend/getContent.do?id=%7BCBECAA35-9690-48A6-90C3-42A8B0C4DF5E%7D):
#   identificativo transitorio costituito all'approvazione del tipo mappale e
#   soppresso con la dichiarazione Docfa, quindi prima di qualunque classamento;
#   le Linee guida AdE Lombardia (p. 29) lo dicono esplicitamente: "le unita'
#   immobiliari censite nel gruppo F, oltre a non avere alcuna rendita
#   catastale, sono rappresentate solo sull'elaborato planimetrico";
# * F/7 "infrastrutture di reti pubbliche di comunicazione": Agenzia delle
#   Entrate, circolare n. 18/E dell'8 giugno 2017 (art. 12 c. 2 D.Lgs. 33/2016):
#   "attribuzione della categoria F/7 ... senza attribuzione di rendita"
#   (comunicato: https://www.agenziaentrate.gov.it/portale/documents/20143/313156/cs+08062017+circolare+n.+18+nuovi+profili+catastali_135_Com.+st.+Circolare+catasto+reti+di+comunicazione+08.06.17.pdf).
#   Quindi TUTTE le sette voci del gruppo F sono senza rendita (`no_income`);
# * A/5 e A/6 "storiche": Ministero delle Finanze - Direzione Generale del
#   Catasto, circolare n. 5 del 14 marzo 1992 (Servizio di documentazione
#   tributaria, copia: https://www.studiopetrillo.com/files/circ_5_14-03-1992.pdf):
#   "non rappresentano piu' tipologie abitative ordinarie", da riclassare in A/4
#   alla prima variazione; nota Min. Finanze 4 maggio 1994 n. C1/1022 per il
#   trattamento delle unita' gia' censite. Restano nelle visure datate e nel
#   quadro delle Linee guida AdE Lombardia: SELEZIONABILI, con la nota "storica";
# * discrepanze chiuse: la stessa circolare 5/1992 dichiarava B/8 "non piu'
#   riscontrabile nell'ordinarieta'" (resta nel quadro corrente: selezionabile)
#   e istituiva D/10 "residence", D/11 "scuole private", D/12 "posti barca e
#   stabilimenti balneari", mai entrate nei quadri correnti (il D/10 odierno,
#   funzioni produttive agricole, viene dal D.P.R. 139/1998): D/11 e D/12 NON
#   incluse. Le pagine HTML di agenziaentrate.gov.it rispondono 403 da qui: i
#   documenti AdE sono stati letti nelle copie pubblicate sopra indicate.
#
# Meccanica: il database salva SOLO il codice (`properties.cadastral_category`
# VARCHAR(5), CHECK di formato `^[A-F]/[0-9]{1,2}$` della migration 083, mai
# un CHECK di catalogo); l'appartenenza al catalogo la giudica il service con
# `validate_cadastral_category`. "Da verificare" e' NULL: solo un'etichetta
# della UI, mai un codice. La categoria non viene MAI dedotta dalla tipologia:
# `CADASTRAL_SUGGESTIONS` sono suggerimenti morbidi per il foglio di scelta,
# con "Tutte le categorie" sempre raggiungibile.

#: (codice, descrizione, note) per gruppo. Le note sono quelle che la UI
#: mostra accanto alla voce: "storica" (A/5, A/6) e "senza rendita" (tutto il
#: gruppo F: F/1-F/5 per il D.M. 28/1998, F/6 per la circ. 1/T 2009, F/7 per
#: la circ. 18/E 2017).
CADASTRAL_CATEGORIES: tuple[dict[str, Any], ...] = tuple(
    {"code": code, "group": code[0], "label": label, "historical": code in ("A/5", "A/6"),
     "no_income": code.startswith("F/")}
    for code, label in (
        # Gruppo A - abitazioni e uffici privati (11)
        ("A/1", "Abitazioni di tipo signorile"),
        ("A/2", "Abitazioni di tipo civile"),
        ("A/3", "Abitazioni di tipo economico"),
        ("A/4", "Abitazioni di tipo popolare"),
        ("A/5", "Abitazioni di tipo ultrapopolare (categoria soppressa)"),
        ("A/6", "Abitazioni di tipo rurale (categoria soppressa)"),
        ("A/7", "Abitazioni in villini"),
        ("A/8", "Abitazioni in ville"),
        ("A/9", "Castelli, palazzi di eminenti pregi artistici o storici"),
        ("A/10", "Uffici e studi privati"),
        ("A/11", "Abitazioni ed alloggi tipici dei luoghi"),
        # Gruppo B - usi collettivi senza fine di lucro (8)
        ("B/1", "Collegi, convitti, educandati, ricoveri, orfanotrofi, ospizi, conventi, seminari, caserme"),
        ("B/2", "Case di cura ed ospedali (senza fine di lucro)"),
        ("B/3", "Prigioni e riformatori"),
        ("B/4", "Uffici pubblici"),
        ("B/5", "Scuole e laboratori scientifici"),
        ("B/6", "Biblioteche, pinacoteche, musei, gallerie, accademie non in edifici A/9"),
        ("B/7", "Cappelle ed oratori non destinati all'esercizio pubblico del culto"),
        ("B/8", "Magazzini sotterranei per depositi di derrate"),
        # Gruppo C - usi commerciali e pertinenze (7)
        ("C/1", "Negozi e botteghe"),
        ("C/2", "Magazzini e locali di deposito"),
        ("C/3", "Laboratori per arti e mestieri"),
        ("C/4", "Fabbricati e locali per esercizi sportivi (senza fine di lucro)"),
        ("C/5", "Stabilimenti balneari e di acque curative (senza fine di lucro)"),
        ("C/6", "Stalle, scuderie, rimesse, autorimesse (senza fine di lucro)"),
        ("C/7", "Tettoie chiuse od aperte"),
        # Gruppo D - immobili a destinazione speciale (10)
        ("D/1", "Opifici"),
        ("D/2", "Alberghi e pensioni (con fine di lucro)"),
        ("D/3", "Teatri, cinematografi, sale per concerti e spettacoli (con fine di lucro)"),
        ("D/4", "Case di cura ed ospedali (con fine di lucro)"),
        ("D/5", "Istituti di credito, cambio e assicurazione (con fine di lucro)"),
        ("D/6", "Fabbricati e locali per esercizi sportivi (con fine di lucro)"),
        ("D/7", "Fabbricati per le speciali esigenze di un'attivita' industriale"),
        ("D/8", "Fabbricati per le speciali esigenze di un'attivita' commerciale"),
        ("D/9", "Edifici galleggianti o sospesi, ponti privati soggetti a pedaggio"),
        ("D/10", "Fabbricati per funzioni produttive connesse alle attivita' agricole"),
        # Gruppo E - immobili a destinazione particolare (9)
        ("E/1", "Stazioni per servizi di trasporto terrestri, marittimi ed aerei"),
        ("E/2", "Ponti comunali e provinciali soggetti a pedaggio"),
        ("E/3", "Costruzioni e fabbricati per speciali esigenze pubbliche"),
        ("E/4", "Recinti chiusi per speciali esigenze pubbliche"),
        ("E/5", "Fabbricati costituenti fortificazioni e loro dipendenze"),
        ("E/6", "Fari, semafori, torri per rendere d'uso pubblico l'orologio comunale"),
        ("E/7", "Fabbricati destinati all'esercizio pubblico dei culti"),
        ("E/8", "Fabbricati e costruzioni nei cimiteri, esclusi colombari, sepolcri e tombe di famiglia"),
        ("E/9", "Edifici a destinazione particolare non compresi nelle altre categorie del gruppo E"),
        # Gruppo F - stati particolari, tutte senza rendita (7)
        ("F/1", "Area urbana"),
        ("F/2", "Unita' collabente"),
        ("F/3", "Unita' in corso di costruzione"),
        ("F/4", "Unita' in corso di definizione"),
        ("F/5", "Lastrico solare"),
        ("F/6", "Fabbricato in attesa di dichiarazione (circ. 1/T 2009)"),
        ("F/7", "Infrastrutture di reti pubbliche di comunicazione (circ. 18/E)"),
    )
)

CADASTRAL_CATEGORY_CODES: frozenset[str] = frozenset(c["code"] for c in CADASTRAL_CATEGORIES)

#: Suggerimenti per tipologia (progetto §5): "suggested" in testa al foglio di
#: scelta, "secondary" subito dopo. Mai un'assegnazione automatica; le
#: eccezioni reali (una villa A/3, un ufficio A/2, un rustico F/2) si
#: raggiungono da "Tutte le categorie". Le chiavi sono tipologie di
#: property/enums.py; una tipologia senza voce ha solo l'elenco completo.
CADASTRAL_SUGGESTIONS: dict[str, dict[str, tuple[str, ...]]] = {
    "apartment":  {"suggested": ("A/2", "A/3", "A/4"), "secondary": ("A/1", "A/11", "A/5", "A/6")},
    "house":      {"suggested": ("A/2", "A/3", "A/4"), "secondary": ("A/7", "A/11", "A/5", "A/6")},
    "villa":      {"suggested": ("A/7", "A/8"),        "secondary": ("A/2", "A/3", "A/9")},
    "rustic":     {"suggested": ("A/6", "A/11", "C/2"), "secondary": ("A/3", "A/4", "D/10")},
    "commercial": {"suggested": ("C/1", "C/3"),        "secondary": ("C/2", "D/8")},
    "office":     {"suggested": ("A/10",),             "secondary": ("D/5", "B/4")},
    "garage":     {"suggested": ("C/6",),              "secondary": ("C/7",)},
    "storage":    {"suggested": ("C/2",),              "secondary": ("C/7", "C/3")},
    "building":   {"suggested": (),                    "secondary": ("D/1", "D/2", "D/8")},
    "land":       {"suggested": (),                    "secondary": ("F/1",)},
    "other":      {"suggested": (),                    "secondary": ()},
}


def cadastral_categories_for_form() -> list[dict[str, Any]]:
    """Il catalogo come lo riceve il form (form-options): voci nell'ordine del
    quadro, con gruppo ed etichetta, piu' le note che la UI deve mostrare."""
    return [dict(c) for c in CADASTRAL_CATEGORIES]


def cadastral_suggestions_for_form() -> dict[str, dict[str, list[str]]]:
    return {t: {"suggested": list(v["suggested"]), "secondary": list(v["secondary"])}
            for t, v in CADASTRAL_SUGGESTIONS.items()}


def validate_cadastral_category(value: str | None) -> str | None:
    """Il codice nella forma che il database salva (maiuscolo, senza spazi
    attorno) oppure None per "Da verificare". Fuori catalogo -> ValueError;
    il chiamante lo traduce in 400. Nessun codice viene mai dedotto."""
    if value is None:
        return None
    code = value.strip().upper()
    if code == "":
        return None
    if code not in CADASTRAL_CATEGORY_CODES:
        raise ValueError(f"Categoria catastale non presente nel catalogo: {value}")
    return code


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


# --- Etichette del censimento (CENSIMENTO-1 Fase 4) -------------------------------

# Le tre liste che il foglio «Nuova palazzina» e il foglio «Pertinenza» mostrano
# come chips: i VALORI sono quelli che `property/schemas.py` valida
# (BUILDING_TYPES, UNITS_DECLARED_SOURCES, ACCESSORY_KINDS), le etichette vivono
# qui e arrivano al browser da `form-options`, come le tipologie: nessuna lista
# scritta a mano nella Shell. Un test confronta queste chiavi con gli enum.
BUILDING_TYPE_LABELS: dict[str, str] = {
    "condominio": "Palazzina / Condominio", "villa": "Villa", "rustico": "Rustico",
    "capannone": "Capannone", "commerciale": "Edificio commerciale", "misto": "Uso misto",
    "altro": "Altro",
}
UNITS_DECLARED_SOURCE_LABELS: dict[str, str] = {
    "survey": "Sopralluogo", "cadastre": "Visura", "owner": "Proprietario", "unknown": "Non so",
}
#: EDIFICI-1: lo stato del censimento dell'edificio (`buildings.census_status`).
BUILDING_CENSUS_STATUS_LABELS: dict[str, str] = {
    "verified": "Verificato", "partial": "Parziale", "estimated": "Stimato",
}
#: CATALOGO-CANONICO-1: + i tipi del sito (taverna, balcone, piscina, posto
#: moto, posto bici; migration 087). Il "garage" del sito e' il `box`: stessa
#: rimessa chiusa, etichetta "Garage / box" come la tipologia `garage`.
ACCESSORY_KIND_LABELS: dict[str, str] = {
    "cantina": "Cantina", "soffitta": "Soffitta", "posto_auto": "Posto auto", "giardino": "Giardino",
    "terrazzo": "Terrazzo", "box": "Garage / box", "deposito": "Deposito", "altro": "Altro",
    "taverna": "Taverna", "balcone": "Balcone", "piscina": "Piscina", "posto_moto": "Posto moto",
    "posto_bici": "Posto bici",
}


def census_labels_for_form() -> dict[str, list[dict[str, str]]]:
    """Le liste (tre di Fase 4, piu' lo stato del censimento di EDIFICI-1),
    nella forma `{value, label}` gia' usata per `property_types`."""
    return {
        "building_types": [{"value": k, "label": v} for k, v in BUILDING_TYPE_LABELS.items()],
        "units_declared_sources": [{"value": k, "label": v} for k, v in UNITS_DECLARED_SOURCE_LABELS.items()],
        "accessory_kinds": [{"value": k, "label": v} for k, v in ACCESSORY_KIND_LABELS.items()],
        "building_census_statuses": [{"value": k, "label": v} for k, v in BUILDING_CENSUS_STATUS_LABELS.items()],
    }
