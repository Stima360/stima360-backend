"""P29-3D - l'inventario della fase, scritto a mano.

Come `p29_3b_diff` e `p29_3c_diff`: un elenco ricavato da `git` direbbe
soltanto che il working tree e' uguale a se' stesso. Questo e' cio' che la
fase DICHIARA di toccare, e una sentinella lo confronta con la realta' nei
due versi. Vale prima del commit (niente fuori elenco) e dopo (ogni voce
nell'indice).
"""
from __future__ import annotations

FILE_MODIFICATI = frozenset({
    # I testi reali della sequenza, e le due URL che chiedono.
    "communication/templates.py",
    "communication/journey_tick.py",
    "communication/journey_repository.py",
    # La sonda dello schema serve anche alle primitive chiamate dal Contact
    # 360, non solo al tick.
    "communication/journey_service.py",
    # "invia ora": sposta la data di un messaggio in coda, non lo spedisce.
    "communication/service.py",
    "communication/repository.py",
    # Le rotte del Contact 360 e il corpo del messaggio manuale.
    "communication/router.py",
    "communication/schemas.py",
    # La tab nella scheda contatto.
    "static/os_shell/assets/views/contatto-dettaglio.js",
    # Le sentinelle delle fasi precedenti che nominano questa collisione.
    "tests/test_p29_2_2_communication_service.py",
    "tests/test_p29_2_4_dispatch_sentinels.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3c_orchestrator_postgres.py",
    "tests/test_p29_2_1_communication_foundation.py",
    "tests/test_p29_2_3_claim_sentinels.py",
    # Il punto unico in cui le sentinelle LMC leggono quali fasi hanno
    # dichiarato una modifica in `communication/`: ora ne elenca due.
    "tests/p29_3b_diff.py",
    # Il registro dei template e l'inventario visti da P29-3B.
    "tests/test_p29_3_journey_foundation.py",
    # L'elenco delle pagine della Shell ammesse fuori dalla Rete.
    "tests/test_p27_7_network_contracts.py",
})

FILE_NUOVI = frozenset({
    "communication/journey_catalog.py",
    "communication/contact_view.py",
    "static/os_shell/assets/components/communications.js",
    "tests/p29_3d_diff.py",
    "tests/test_p29_3d_crm_journey.py",
    "tests/test_p29_3d_crm_journey_postgres.py",
})
