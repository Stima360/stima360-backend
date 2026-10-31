"""Il documento di design P29-2.0 (`P29_2_0_COMMUNICATION_DESIGN.md`).

BLOCCO 1 PRE-COLLAUDO. Il file non e' MAI stato nel repository (nessun commit
nella storia lo contiene): era una copia locale, non tracciata, sulla
macchina di chi ha sviluppato P29. Le sentinelle che ne controllano
l'impronta o lo stato "non tracciato" fallivano quindi in ogni clone pulito
(CI, Render, ogni nuova sessione) senza dire nulla del codice. Dove il file
esiste i controlli restano identici; dove non esiste il test si dichiara
SALTATO, con il motivo, e compare nel riepilogo di pytest.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESIGN_DOC_NAME = "P29_2_0_COMMUNICATION_DESIGN.md"
DESIGN_DOC = ROOT / DESIGN_DOC_NAME
SKIP_REASON = (f"{DESIGN_DOC_NAME} non e' nel repository (mai versionato): il controllo vale solo "
               "dove la copia locale del documento esiste")


def present() -> bool:
    return DESIGN_DOC.is_file()


def require() -> None:
    if not present():
        pytest.skip(SKIP_REASON)
