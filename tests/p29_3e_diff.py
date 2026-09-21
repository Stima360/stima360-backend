"""P29-3E - l'inventario della fase, scritto a mano.

Come `p29_3b_diff`, `p29_3c_diff` e `p29_3d_diff`: un elenco ricavato da `git`
direbbe soltanto che il working tree e' uguale a se' stesso. Questo e' cio'
che la fase DICHIARA di toccare, e le sentinelle lo confrontano con la realta'
nei due versi - prima del commit (niente fuori elenco) e dopo (ogni voce
nell'indice).

LA COLLISIONE DI QUESTA FASE HA UN NOME PRECISO. Tre fasi hanno scritto una
sentinella che dice "il runner del dispatch NON nomina le journey": era vero,
ed era il punto - il collegamento andava fatto DOPO la 071 su TEST, non prima.
La 071 e' applicata, la fase e' questa, e quelle sentinelle non si cancellano:
si restringono a cio' che resta vero per sempre, cioe' che il cron non
provisiona e non attiva niente, che non ne nasce un secondo, e che il runner
resta un client HTTP senza dominio dentro.
"""
from __future__ import annotations

#: L'UNICO runner che questa fase puo' toccare. Nominato qui perche' le
#: sentinelle delle fasi precedenti lo confrontino con `git status` invece di
#: pretendere che nessun runner sia cambiato.
RUNNER_TOCCATO = "run_communication_dispatch_cron.py"

FILE_MODIFICATI = frozenset({
    # Il cron: tick prima, dispatch poi, logout nel finally.
    RUNNER_TOCCATO,
    # Il contratto del log: due righe per giro, e i nomi ammessi.
    "tests/test_p29_2_6e_cron_exit_semantics.py",
    # Le tre sentinelle che dicevano "il runner non nomina le journey".
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3d_crm_journey.py",
    # LMC-15 verificava l'assenza di modifiche a QUALUNQUE runner.
    "tests/test_lmc15_acquisition_bridge.py",
})

FILE_NUOVI = frozenset({
    "tests/p29_3e_diff.py",
    "tests/test_p29_3e_cron_integration.py",
    "tests/test_p29_3e_cron_integration_postgres.py",
    "docs/P29_3E_TEST_CERTIFICATION.md",
})
