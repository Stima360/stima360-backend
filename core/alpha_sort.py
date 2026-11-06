"""ORDINAMENTO ALFABETICO degli elenchi (Contatti, Immobili, Incarichi): UN solo punto.

La chiave di ordinamento di un testo, in SQL, uguale per tutti gli elenchi:

  * maiuscole e minuscole non contano («de luca» = «De Luca»);
  * gli accenti non contano («Èrica» sta con le E, «Nicolò» con «Nicolo»):
    le lettere latine accentate diventano la lettera semplice PRIMA di
    confrontare, senza estensioni del database (niente `unaccent`);
  * spazi e punteggiatura iniziali non contano («"Studio Alfa"», « Rossi»);
  * il confronto finale e' in collation "C" sul testo gia' normalizzato: lo
    stesso ordine qualunque sia la lingua del database;
  * un testo vuoto o mancante va SEMPRE in fondo, in A–Z come in Z–A, e non
    altera l'ordine degli altri.

Le espressioni passate qui sono letterali dello sviluppatore (colonne), mai
input della richiesta: la direzione arriva solo da DIRECTIONS.
"""
from __future__ import annotations

_PAIRS = (
    ("ÀÁÂÃÄÅĀĂĄàáâãäåāăą", "a"), ("Ææ", "a"), ("ÇĆČçćč", "c"), ("ĎĐďđ", "d"),
    ("ÈÉÊËĒĖĘĚèéêëēėęě", "e"), ("ÌÍÎÏĪĮìíîïīį", "i"), ("Łł", "l"), ("ÑŃŇñńň", "n"),
    ("ÒÓÔÕÖØŌŒòóôõöøōœ", "o"), ("ŔŘŕř", "r"), ("ŚŠŞśšşß", "s"), ("ŤŢťţ", "t"),
    ("ÙÚÛÜŪŮŰùúûüūůű", "u"), ("ÝŸýÿ", "y"), ("ŹŻŽźżž", "z"),
)
ACCENTED = "".join(chars for chars, _ in _PAIRS)
PLAIN = "".join(base * len(chars) for chars, base in _PAIRS)
assert len(ACCENTED) == len(PLAIN)

ASC, DESC = "asc", "desc"
DIRECTIONS = {ASC: "ASC", DESC: "DESC"}


def key(expr: str) -> str:
    """La chiave normalizzata di `expr` (mai NULL: un valore mancante e' '')."""
    return ("lower(translate(regexp_replace(btrim(coalesce(" + expr + ", '')), "
            "'^[[:space:][:punct:]«»“”‘’]+', ''), '" + ACCENTED + "', '" + PLAIN + "'))")


def order_by(keys: list[str], direction: str, tiebreak: str) -> str:
    """ORDER BY su piu' chiavi testuali, ciascuna con i vuoti in fondo, poi
    `tiebreak` (es. l'id) per un ordine totale e una paginazione stabile."""
    sql_dir = DIRECTIONS[direction]
    parts = []
    for k in keys:
        parts.append(f"({k} = '') ASC")
        parts.append(f"{k} COLLATE \"C\" {sql_dir}")
    parts.append(f"{tiebreak} {sql_dir}")
    return ", ".join(parts)


def leading_number(expr: str) -> str:
    """Il numero iniziale di un civico («12/B» -> 12) per «Via Roma 2» prima di «Via Roma 10»."""
    return f"NULLIF(substring(coalesce({expr}, '') from '^[0-9]{{1,9}}'), '')::int"


def numeric_order(expr: str, direction: str) -> str:
    return f"({leading_number(expr)} IS NULL) ASC, {leading_number(expr)} {DIRECTIONS[direction]}"
