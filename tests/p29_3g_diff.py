"""P29-3G - l'inventario della fase, scritto a mano.

Due correzioni sole, entrambe nate da difetti VISTI dal vivo durante la
certificazione P29-3F, e nessun'altra: l'anteprima del Contact 360 che
mostrava HTML grezzo, e le due azioni su un passo assistito che erano
disponibili prima della scadenza - una rifiutata con 409, l'altra riuscita.

Come le fasi precedenti, l'elenco e' dichiarato e una sentinella lo confronta
con `git status` nei due versi.
"""
from __future__ import annotations

FILE_MODIFICATI = frozenset({
    # La decisione condivisa: "un'azione assistita e' dovuta?".
    "communication/journey_enums.py",
    # Il contratto temporale applicato da invio e salto.
    "communication/journey_tick.py",
    # L'anteprima leggibile e i due flag legati alla scadenza.
    "communication/contact_view.py",
    # Le sentinelle di inventario che nominano questa collisione: ognuna
    # pretende che nel working tree non ci sia niente che nessuna fase abbia
    # dichiarato, e adesso le fasi sono cinque.
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3d_crm_journey.py",
    "tests/test_p29_3e_cron_integration.py",
})

FILE_NUOVI = frozenset({
    "tests/p29_3g_diff.py",
    "tests/test_p29_3g_final_fixes.py",
    "tests/test_p29_3g_final_fixes_postgres.py",
    "docs/P29_3G_LIVE_RECERTIFICATION.md",
})
