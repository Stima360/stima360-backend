"""CENSIMENTO-2 - le CLASSI di un'unita' censita, in un posto solo.

Funzioni pure e frammenti SQL: niente database, niente import applicativi.
Le usano la composizione dell'edificio (property/composition.py), la
validazione dei gruppi (property/schemas.py) e, in futuro, la UI tramite
form-options.

Regole approvate (D1):
  * PERTINENZA: la regola della 089, `parent_property_id IS NOT NULL OR
    is_pertinenza`. Box e cantine autonomi (garage/storage senza
    `is_pertinenza`) NON diventano automaticamente pertinenze;
  * ABITAZIONE: apartment, villa, house. Un RUSTICO e' un'abitazione SOLO se
    classificato esplicitamente come tale (`metadata.census_class =
    'residential'`, scritto da una scelta dell'operatore: gruppo
    «abitazioni» nella generazione o «Modifica selezionate»); altrimenti e'
    un'«altra» unita';
  * COMMERCIALE: commercial, office;
  * ALTRA: ogni altro tipo.
"""
from __future__ import annotations

CLASSES = ("residential", "commercial", "other", "pertinenze")
CLASS_LABELS = {"residential": "Abitazioni", "commercial": "Commerciali", "other": "Altre unità",
                "pertinenze": "Pertinenze"}
#: la colonna di `buildings` con il numero dichiarato di ogni classe (migration 100)
DECLARED_COLUMNS = {"residential": "units_declared_residential", "commercial": "units_declared_commercial",
                    "other": "units_declared_other", "pertinenze": "units_declared_pertinenze"}

RESIDENTIAL_TYPES = ("apartment", "villa", "house")
RUSTIC = "rustic"
COMMERCIAL_TYPES = ("commercial", "office")
#: la chiave di `properties.metadata` con la classificazione esplicita
EXPLICIT_CLASS_KEY = "census_class"

#: Le tipologie ammesse per ogni classe in un gruppo della generazione.
#: `building` (stabile intero) non si genera in blocco.
GROUP_TYPES = {
    "residential": ("apartment", "villa", "house", "rustic"),
    "commercial": ("commercial", "office"),
    "other": ("rustic", "garage", "storage", "land", "other"),
    "pertinenze": ("garage", "storage", "other"),
}

#: Provenienza del dato (migration 100). NULL = «non indicata».
DATA_SOURCES = ("estimated", "declared", "verified")
DATA_SOURCE_LABELS = {"estimated": "Stimata", "declared": "Dichiarata", "verified": "Verificata"}

#: I dati che una scheda deve avere perche' il suo censimento sia «completo»
#: (indicatore SEPARATO dal numero di unita' create, D4). Elenco unico,
#: proposto: va confermato prima della UI.
MINIMUM_DATA = ("surface_sqm", "surface_source", "cadastral_category", "cadastral_category_source")
#: Piano, scala e interno possono restare sconosciuti e completarsi dopo: non
#: entrano nella completezza (precisazione 1). La verifica catastale e' un
#: indicatore a parte (`verification`), mai richiesta per la completezza.


def _metadata(riga: dict) -> dict:
    m = riga.get("metadata")
    return m if isinstance(m, dict) else {}


def is_pertinenza(riga: dict) -> bool:
    return riga.get("parent_property_id") is not None or bool(riga.get("is_pertinenza"))


def unit_class(riga: dict) -> str:
    if is_pertinenza(riga):
        return "pertinenze"
    tipo = riga.get("property_type")
    if tipo in RESIDENTIAL_TYPES:
        return "residential"
    if tipo == RUSTIC and _metadata(riga).get(EXPLICIT_CLASS_KEY) == "residential":
        return "residential"
    if tipo in COMMERCIAL_TYPES:
        return "commercial"
    return "other"


def class_sql(alias: str) -> str:
    """La stessa regola in SQL (valida anche senza la 089: `to_jsonb`)."""
    marcata = f"coalesce((to_jsonb({alias})->>'is_pertinenza')::boolean, FALSE)"
    residenziali = ", ".join(f"'{t}'" for t in RESIDENTIAL_TYPES)
    commerciali = ", ".join(f"'{t}'" for t in COMMERCIAL_TYPES)
    return (f"(CASE WHEN {alias}.parent_property_id IS NOT NULL OR {marcata} THEN 'pertinenze' "
            f"WHEN {alias}.property_type IN ({residenziali}) THEN 'residential' "
            f"WHEN {alias}.property_type = '{RUSTIC}' AND {alias}.metadata->>'{EXPLICIT_CLASS_KEY}' = 'residential' "
            f"THEN 'residential' "
            f"WHEN {alias}.property_type IN ({commerciali}) THEN 'commercial' ELSE 'other' END)")


def missing_data(riga: dict) -> list[str]:
    """I dati minimi che mancano alla scheda (vuoto = completa)."""
    mancanti = []
    for campo in MINIMUM_DATA:
        valore = riga.get(campo)
        if valore is None or (isinstance(valore, str) and not valore.strip()):
            mancanti.append(campo)
    return mancanti


def composition_complete(edificio: dict) -> bool:
    return all(edificio.get(c) is not None for c in DECLARED_COLUMNS.values())


#: D3 (precisazione 3): la fonte catastale documentabile che «Verificata»
#: richiede. La sezione e' facoltativa (non tutti i comuni la prevedono).
CADASTRAL_SOURCE_FIELDS = ("cadastral_municipality_code", "cadastral_sheet", "cadastral_parcel", "cadastral_subunit")


def has_cadastral_source(riga: dict) -> bool:
    return all(riga.get(k) is not None and str(riga.get(k)).strip() != "" for k in CADASTRAL_SOURCE_FIELDS)


MINIMUM_DATA_LABELS = {"surface_sqm": "superficie", "surface_source": "provenienza della superficie",
                       "cadastral_category": "categoria", "cadastral_category_source": "provenienza della categoria",
                       }
COMPLETION_LABELS = {"draft": "Bozza: composizione da dichiarare", "composition_declared": "Composizione dichiarata",
                     "units_created": "Unità create, schede da completare", "data_complete": "Schede complete"}


#: La fonte consultata e registrata che «Verificata» richiede (migration 100).
EVIDENCE_TYPES = ("visura", "planimetria", "atto", "altro")
EVIDENCE_TYPE_LABELS = {"visura": "Visura catastale", "planimetria": "Planimetria catastale",
                        "atto": "Atto (rogito, notarile)", "altro": "Altro documento"}


def identity_key(riga: dict) -> str | None:
    """La stessa chiave di `census_identity_key` della 100: comune, sezione
    (facoltativa), foglio, particella, subalterno; None se incompleta."""
    parti = [str(riga.get(k) or "").strip() for k in ("cadastral_municipality_code", "cadastral_section",
                                                      "cadastral_sheet", "cadastral_parcel", "cadastral_subunit")]
    if not all(parti[i] for i in (0, 2, 3, 4)):
        return None
    return "|".join(parti)


#: CENSIMENTO-2 Fase A (A1): tre condizioni, sempre una per scheda. Indicatore
#: di ATTENDIBILITA', separato dalla quantita' (unita' create) e dalla
#: completezza (dati minimi). «Verificata» = superficie E categoria con
#: provenienza `verified`: il vincolo della 100 garantisce che ognuna abbia la
#: fonte consultata (tipo e data) legata all'identita' catastale attuale.
VERIFICATION_STATES = ("verified", "partial", "to_verify")
VERIFICATION_LABELS = {"verified": "Verificata", "partial": "Parzialmente verificata", "to_verify": "Da verificare"}
RECONFIRM_LABEL = "Da riconfermare"


def verification_state(riga: dict) -> str:
    """'verified' (superficie e categoria entrambe verificate), 'partial' (una
    sola delle due), 'to_verify' (nessuna; comprende la verifica non piu'
    valida, vedi `needs_reconfirm`)."""
    verificati = sum(1 for k in ("surface_source", "cadastral_category_source") if riga.get(k) == "verified")
    return ("to_verify", "partial", "verified")[verificati]


def needs_reconfirm(riga: dict) -> bool:
    """Una fonte registrata per un'identita' catastale che non e' piu' quella
    dell'unita' (i riferimenti sono cambiati dopo la verifica): la verifica
    non vale piu' e va riconfermata. Il trigger della 100 ha gia' riportato le
    provenienze «verificate» a «non indicata»; la fonte resta come storico."""
    registrata = riga.get("cadastral_evidence_identity")
    return bool(registrata) and registrata != identity_key(riga) and verification_state(riga) == "to_verify"


def catalog() -> dict:
    """Le liste della composizione per la Shell (nessun elenco scritto nel
    browser): classi con le tipologie ammesse nella generazione, provenienze,
    dati minimi, stati del completamento."""
    return {
        "classes": [{"value": c, "label": CLASS_LABELS[c], "property_types": list(GROUP_TYPES[c])} for c in CLASSES],
        "data_sources": [{"value": v, "label": DATA_SOURCE_LABELS[v], "bulk": v != "verified"} for v in DATA_SOURCES],
        "minimum_data": [{"value": v, "label": MINIMUM_DATA_LABELS[v]} for v in MINIMUM_DATA],
        "completion": [{"value": k, "label": v} for k, v in COMPLETION_LABELS.items()],
        "cadastral_source_fields": list(CADASTRAL_SOURCE_FIELDS),
        "evidence_types": [{"value": v, "label": EVIDENCE_TYPE_LABELS[v]} for v in EVIDENCE_TYPES],
        "verification": [{"value": v, "label": VERIFICATION_LABELS[v]} for v in VERIFICATION_STATES],
        "max_batch_units": 300,
    }
