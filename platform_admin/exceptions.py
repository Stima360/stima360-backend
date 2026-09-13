"""Eccezioni di dominio della superficie Platform.

Tipi puri: nessun import di framework web. La traduzione in uno status HTTP
appartiene al livello router, come in operator_auth/exceptions.py.
"""
from __future__ import annotations


class PlatformAuditUnavailable(Exception):
    """La riga di audit non e' stata scritta.

    Sollevata dal writer quando la scrittura fallisce per qualunque ragione -
    database irraggiungibile, vincolo violato, transazione rifiutata.

    Non viene inghiottita dal writer di proposito. Chi chiama deve DECIDERE
    cosa farne, e le due decisioni in P27-1 sono diverse:

    * sul percorso di ammissione riuscita l'operazione non procede (503): un
      atto amministrativo non registrato non deve avvenire;
    * sul percorso di rifiuto il 403 resta comunque, perche' l'operazione non
      stava avvenendo in nessun caso e trasformare un rifiuto in un 500
      direbbe al chiamante qualcosa sullo stato interno del server.

    Un writer che loggasse e basta renderebbe impossibile la prima delle due.
    """
