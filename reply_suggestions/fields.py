"""BLOCCO-4C - i dati dell'immobile che una risposta al cliente puo' usare.

Un elenco CHIUSO. Un campo che non e' qui non entra mai in un suggerimento,
qualunque cosa chieda il cliente: la risposta passa all'agente.

COMUNICABILI (colonna di `properties` o tabella collegata, e come si dice)
    asking_price             prezzo richiesto
    surface_sqm              superficie (mq), col suo tipo
    commercial_surface_sqm   superficie commerciale (mq), col suo tipo
    rooms / bedrooms / bathrooms   locali, camere, bagni
    floor (+ total_floors)   piano
    elevator                 ascensore (si / no; vuoto = non registrato)
    energy_class             classe energetica
    property_accessories     pertinenze registrate (balcone, terrazzo, box...)
    commercial_status        SOLO come condizione: i dati si comunicano solo
                             per un immobile `active`; ogni altro stato passa
                             all'agente, senza dire quale sia. La disponibilita'
                             NON si afferma mai (BLOCCO-4D): `active` e'
                             un'etichetta manuale («Attivo») senza data ne'
                             verifica, non la prova che l'immobile sia libero

ESCLUSI, mai letti da questo modulo: note interne e pubbliche, prezzo minimo,
dati e contatti del proprietario, indirizzo e numero civico, mandato,
documenti, valutazioni e stime, visite, provenienza, metadati.

Nessun valore si inventa: un campo vuoto e' «non registrato» e la domanda
passa all'agente. La disponibilita' commerciale non dice nulla sulle visite.
"""
from __future__ import annotations

from decimal import Decimal

from property.catalog import ACCESSORY_KIND_LABELS

#: Le sole colonne di `properties` lette per un suggerimento (oltre a id,
#: codice e titolo, per indicare la fonte).
PROPERTY_COLUMNS = (
    "asking_price", "surface_sqm", "commercial_surface_sqm", "rooms", "bedrooms", "bathrooms",
    "floor", "total_floors", "elevator", "energy_class", "commercial_status",
)

#: Mai lette: un test lo verifica sul codice del modulo.
EXCLUDED_COLUMNS = (
    "minimum_price", "internal_notes", "public_notes", "address", "civic_number", "mandate_type",
    "mandate_start", "mandate_end", "assigned_to", "source", "metadata", "valuation", "owner",
)

#: Lo stato commerciale che si puo' comunicare al cliente, e come.
AVAILABLE_STATUS = "active"

#: Pertinenze che si nominano (l'«altro» generico no: non dice che cosa sia).
ACCESSORY_LABELS = {k: v for k, v in ACCESSORY_KIND_LABELS.items() if k != "altro"}

FIELD_LABELS = {
    "asking_price": "Prezzo richiesto", "surface_sqm": "Superficie", "commercial_surface_sqm": "Superficie commerciale",
    "rooms": "Locali", "bedrooms": "Camere", "bathrooms": "Bagni", "floor": "Piano", "elevator": "Ascensore",
    "energy_class": "Classe energetica", "accessories": "Pertinenze", "commercial_status": "Stato commerciale",
}


def euro(value) -> str:
    amount = Decimal(value).quantize(Decimal("1"))
    return "€ " + f"{amount:,}".replace(",", ".")


def sqm(value) -> str:
    number = Decimal(value)
    text = f"{number.normalize():f}" if number != number.to_integral() else f"{int(number)}"
    return f"{text.replace('.', ',')} mq"
