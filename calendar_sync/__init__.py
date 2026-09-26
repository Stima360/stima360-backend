"""A30-9 - sincronizzazione OUTBOUND Agenda -> calendario esterno (Google).

A30-9A: le FONDAMENTA. Tabelle (migration 074), cifratura dei token, contratto
del provider con un provider finto, coda di riconciliazione e riconciliatore.
Nessuna rotta, nessuna chiamata di rete, nessun hook nell'Agenda: il package
non e' importato da `main.py` ne' da `appointments/`.

L'Agenda (`appointments`) e' la FONTE AUTOREVOLE; il calendario esterno e' una
proiezione derivata. Niente legge da Google per modificare un appuntamento.
"""
