"""V3 - il marchio STIMA360 delle email del follow-up: il file del logo e la
rotta pubblica che lo serve. Solo costanti: le usano la rotta pubblica
(`public_router`) e la grafica delle email (nell'adapter), senza che l'una
importi l'altra."""
from __future__ import annotations

from pathlib import Path

LOGO_FILE = Path(__file__).resolve().parent / "brand" / "stima360_logo.jpg"
LOGO_PERCORSO_RELATIVO = "/logo-stima360.jpg"
LOGO_PERCORSO = "/api/public/communication" + LOGO_PERCORSO_RELATIVO
LOGO_ALT = "STIMA360 - Immobiliare di nuova generazione"
SITO = "https://www.stima360.it"

__all__ = ["LOGO_FILE", "LOGO_PERCORSO_RELATIVO", "LOGO_PERCORSO", "LOGO_ALT", "SITO"]
