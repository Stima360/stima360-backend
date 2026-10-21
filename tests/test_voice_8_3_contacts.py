"""STIMA Voice V8-3 - ricerca tollerante dei contatti: le regole pure.

  01  la somiglianza: accenti e maiuscole non contano, «Rosi» ~ «Rossi»
  02  le soglie sono fisse (sentinella) e sotto la soglia non e' la stessa persona
  03  le famiglie di ruolo: proprietario/venditore insieme, acquirente con prospect
"""
from __future__ import annotations

import pytest

from voice import repository


@pytest.mark.parametrize("detto, registrato, almeno", [
    ("Rosi", "Rossi", 0.88), ("rossi", "ROSSI", 1.0), ("Nicolo", "Nicolò", 1.0), ("Verde", "Verdi", 0.79),
    ("Galli", "Gallo", 0.79), ("Bianco", "Bianchi", 0.76), ("Francesco", "Franceso", 0.9),
])
def test_01_somiglianza(detto, registrato, almeno):
    assert repository.name_similarity(detto, registrato) >= almeno


def test_02_soglie_fisse_e_non_la_stessa_persona():
    # SENTINELLA AGGIORNATA DAL COLLAUDO 3: si confrontano fino a 2000 contatti visibili (dai piu'
    # recenti), con il prefiltro sulle iniziali di nome, cognome e nome completo
    assert repository.NAME_SIMILARITY == 0.75 and repository.SIMILARITY_SCAN == 2000
    assert repository.SKELETON_SIMILARITY == 0.78
    assert repository.name_similarity("Rossi", "Bianchi") < repository.NAME_SIMILARITY
    assert repository.name_similarity("", "Rossi") == 0.0 and repository.name_similarity(None, None) == 0.0
    assert repository.name_similarity("Zxqv", "Rossi") < repository.NAME_SIMILARITY


def test_03_famiglie_di_ruolo():
    assert repository._ROLE_FAMILIES == {"owner": ("owner", "seller"), "seller": ("owner", "seller"),
                                         "buyer": ("buyer", "prospect")}
