"""V3 - la guida PDF ufficiale dell'email 6, servita dal CRM con un link.

PERCHE' UN LINK E NON UN ALLEGATO. L'adapter email (`providers/email_smtp`)
non passa allegati, e `database.invia_mail` - lo stesso canale della mail di
valutazione - non si tocca. Un link di download controllato non cambia niente
di quel percorso.

CONTROLLATO, CIOE':
  * il file e' QUESTO (`FILE`), con QUESTA impronta (`SHA256`), versionato
    con il codice;
  * la rotta pubblica `PERCORSO` lo serve solo se il file c'e' e l'impronta
    coincide; altrimenti 404;
  * il link entra nell'email 6 solo se il file e' disponibile in quel
    momento: senza, l'email 6 non si compone e non parte (nessun link rotto).
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

TITOLO = "Vendere un immobile senza errori - Edizione 2026"
FILE = Path(__file__).resolve().parent / "guida" / "STIMA360_Guida_Vendere_Casa_2026.pdf"
SHA256 = "3b6b079984813f46c48b2b17c88961c62e633b532310ae468de0ec8f5b75ba2d"
NOME_DOWNLOAD = "STIMA360_Guida_Vendere_Casa_2026.pdf"
#: Sotto il router pubblico della comunicazione (`/api/public/communication`).
PERCORSO_RELATIVO = "/guida-vendere-casa-2026.pdf"
PERCORSO = "/api/public/communication" + PERCORSO_RELATIVO


@lru_cache(maxsize=1)
def disponibile() -> bool:
    """Il file c'e' ed e' quello approvato."""
    try:
        return hashlib.sha256(FILE.read_bytes()).hexdigest() == SHA256
    except OSError:
        return False


def url(base_pubblica: str) -> str | None:
    """Il link di download, o None se il file non e' disponibile."""
    if not disponibile() or not (base_pubblica or "").strip():
        return None
    return base_pubblica.rstrip("/") + PERCORSO


__all__ = ["TITOLO", "FILE", "SHA256", "PERCORSO", "disponibile", "url"]
