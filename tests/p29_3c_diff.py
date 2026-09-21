"""P29-3C - l'inventario della fase, scritto a mano.

Come `p29_3b_diff` per la fase precedente: un elenco ricavato da `git`
direbbe soltanto che il working tree e' uguale a se' stesso. Questo invece
e' cio' che la fase DICHIARA di toccare, e una sentinella lo confronta con
la realta' in entrambe le direzioni.

Vale prima e dopo il commit: prima, nel working tree non puo' comparire
nulla che non sia qui; dopo, ogni file di questo elenco deve essere
nell'indice.
"""
from __future__ import annotations

#: Il codice dell'orchestratore, le sue rotte, e le due estensioni.
FILE_MODIFICATI = frozenset({
    # Il motore si innesta su cio' che esiste: il service delle journey gli
    # presta due helper e valida la finestra, il repository ospita le sue
    # query, il router monta le tre rotte, gli schemi il corpo del tick.
    "communication/journey_service.py",
    "communication/journey_repository.py",
    "communication/router.py",
    "communication/schemas.py",
    # La timeline dell'invio, nella transazione della finalizzazione.
    "communication/integrations.py",
    "communication/journey_enums.py",
    # Il consenso in blocco: una seconda LETTURA, non una seconda decisione.
    "consent/guard.py",
    "consent/repository.py",
    # IL FENCE, l'altra meta' del contratto: gli scrittori dei fatti di stop
    # prendono `stime` FOR UPDATE nella stessa transazione in cui scrivono,
    # cosi' che il motore possa decidere con le righe in mano. Nessuna
    # semantica di questi domini cambia - si aggiunge solo il coordinamento,
    # e in `seller_intelligence` un flag spento per tutti tranne l'evento
    # della consulenza.
    "acquisition/repository.py",
    "owner/tracking.py",
    "seller_intelligence/repository.py",
    "seller_intelligence/service.py",
    # La governance: il nuovo modulo postgres e' un entrypoint dichiarato.
    "docs/P26_DB_ENTRYPOINTS.md",
    "tests/test_p26_db_entrypoints.py",
    # Le sentinelle di fase che nominano la collisione con P29-3C. L'elenco
    # e' ESATTO: una voce che non corrisponde a una modifica reale
    # renderebbe questo inventario un permesso invece di una dichiarazione.
    "tests/test_p29_1_5_marketing_send_guard.py",
    "tests/test_p29_2_3_claim_sentinels.py",
    "tests/test_p29_2_4_dispatch_sentinels.py",
    "tests/test_p29_3_journey_foundation.py",
    # Il guardiano dei domini di P29-3B impara a riconoscere l'inventario di
    # questa fase invece di accusarla: la modifica e' li', e va dichiarata.
    "tests/p29_3b_diff.py",
    # LMC-1B sorvegliava `communication/router.py` in blocco: P29-3C vi monta
    # le tre rotte delle journey.
    "tests/test_lmc1b_owner_login_link.py",
    # LMC-15 sorvegliava `seller_intelligence/` in blocco e `acquisition/`
    # file per file: il fence tocca entrambi, e le due sentinelle lo
    # nominano invece di smettere di guardare.
    "tests/test_lmc15_acquisition_bridge.py",
    # LMC-2 e LMC-12 sorvegliavano a loro volta `seller_intelligence/`.
    "tests/test_lmc2_owner_homes.py",
    "tests/test_lmc12_home_notifications.py",
})

FILE_NUOVI = frozenset({
    "communication/journey_tick.py",
    "communication/send_window.py",
    "tests/p29_3c_diff.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3c_orchestrator_postgres.py",
})
