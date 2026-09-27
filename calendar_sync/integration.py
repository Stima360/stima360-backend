"""A30-9B - l'adattatore SOTTILE fra il dominio `appointments` e la
sincronizzazione calendario.

Il dominio `appointments` non conosce Google, non conosce lo stato della
sincronizzazione e non fa mai rete: chiama SOLO `on_appointment_mutation`,
nella STESSA transazione della sua scrittura (stesso `cur`), per dire "questa
catena va riconciliata". Nessun'altra funzione di questo modulo e' pensata
per essere chiamata dal dominio.

FAIL-OPEN (§17): se Google e' disabilitato o il namespace di deployment non
e' configurato, questa funzione e' un NO-OP silenzioso - nessuna eccezione,
nessuna riga scritta. Un errore del DATABASE dentro `mark_dirty_with_cursor`
(quando Google E' configurato) invece si propaga normalmente: e' un errore
della stessa transazione come qualunque altro, non va catturato qui.
"""
from __future__ import annotations

from . import config, repository


def on_appointment_mutation(cur, agency_id: int, appointment_id: int) -> None:
    """Segna la catena di `appointment_id` come da riconciliare, nella
    transazione del chiamante. NESSUNA rete. NO-OP se Google non e'
    configurato per questo deployment."""
    namespace = config.hook_deployment_namespace()
    if namespace is None:
        return
    repository.mark_dirty_with_cursor(cur, agency_id, appointment_id,
                                      deployment_namespace=namespace)
