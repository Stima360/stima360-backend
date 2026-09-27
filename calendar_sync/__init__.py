"""A30-9 - sincronizzazione OUTBOUND Agenda -> calendario esterno (Google).

A30-9A: le FONDAMENTA. Tabelle (migration 074), cifratura dei token, contratto
del provider con un provider finto, coda di riconciliazione e riconciliatore.

A30-9B: OAuth reale (`oauth.py`, `router.py`, `google_provider.py`),
configurazione runtime (`config.py`) e l'hook SOTTILE verso l'Agenda
(`integration.py`), montato su `main.py` (solo il router: nessuna logica
Google in `main.py`) e chiamato da `appointments/service.py` e
`appointments/lmc15_facade.py`. Import sempre sicuro: nessuna di queste
funzioni legge l'ambiente, fa rete o tocca il database al momento
dell'IMPORT (§36); con Google disabilitato o non configurato l'hook e' un
NO-OP e il CRM funziona esattamente come in A30-9A.

L'Agenda (`appointments`) resta la FONTE AUTOREVOLE; il calendario esterno e'
una proiezione derivata. Niente legge da Google per modificare un
appuntamento (A30-10, inbound, resta fuori da A30-9).
"""
