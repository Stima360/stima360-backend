"""FLOW GLOBAL SECURITY - l'inventario della fase, scritto a mano.

Tre difetti soli, tutti sul catalogo globale delle regole, e nessun altro: le
cinque rotte che lo SCRIVONO erano dietro alla sola guardia del mount, le due
che lo LEGGONO scrivevano, e `scan` - raggiungibile da una rotta tenant e dal
cron - lo sincronizzava a ogni giro. Il perimetro dichiarato dalla fase e' il
pacchetto `flow` - router, repository, service - piu' i propri test.

PERCHE' QUESTA FASE COMPARE IN UN INVENTARIO NATO DENTRO P29

Perche' le sentinelle di P29 non guardano P29: guardano il WORKING TREE, e
pretendono che non vi sia niente che nessuna fase abbia dichiarato. E' un
controllo volutamente onnivoro - e' cio' che lo rende utile - quindi qualunque
fase successiva, anche su un modulo che con le journey non c'entra nulla,
collide con esse e si dichiara allo stesso modo. Le quattro sentinelle sono
elencate qui sotto fra i file modificati: la collisione e' nominata, non
aggirata.

NESSUNA MIGRATION, NESSUN FILE DI P29. Del frontend si tocca una sola vista,
e solo perche' due delle cinque rotte erano premute da li': i bottoni
Attiva/Disattiva di un'automazione, che a un ruolo tenant adesso risponderebbero
sempre 403. Le due GET restano aperte a tutti i ruoli, quindi nessuna
schermata perde dati e non c'e' nient'altro da nascondere.
"""
from __future__ import annotations

FILE_MODIFICATI = frozenset({
    # La porta delle cinque scritture globali: `require_platform_admin`.
    "flow/router.py",
    # Le due letture, che adesso leggono e basta: via il parametro
    # `synchronize`, che era il difetto travestito da opzione.
    "flow/repository.py",
    # I due chiamanti che quel parametro lo passavano, e - K1 - la riga con
    # cui `scan` sincronizzava il catalogo globale da una superficie tenant.
    "flow/service.py",
    # L'unica schermata che premeva due delle cinque rotte. I bottoni
    # Attiva/Disattiva compaiono solo a chi amministra la piattaforma: non
    # e' la difesa - quella e' lato server - ma un controllo che risponde
    # sempre 403 e' un difetto dell'interfaccia. Le letture restano di tutti.
    "static/os_shell/assets/views/automazione-dettaglio.js",
    # Le sentinelle di inventario che nominano questa collisione: ognuna
    # pretende che nel working tree non ci sia niente che nessuna fase abbia
    # dichiarato, e adesso le fasi sono sei.
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3d_crm_journey.py",
    "tests/test_p29_3e_cron_integration.py",
    # Il quinto file di sentinelle, che e' quello PROPRIO di P29-3G. Qui
    # sono nominate due collisioni e nient'altro: l'elenco dei file nuovi
    # (test_20) e il diff che doveva restare vuoto sotto `static/` e gli
    # altri prefissi intatti (test_18), da cui si sottrae esattamente cio'
    # che questa fase dichiara. Il suo test_20 resta comunque rosso per una
    # ragione sua, che questa fase non ha creato e non corregge: vedi il
    # rilievo in fondo a questo file.
    "tests/test_p29_3g_final_fixes.py",
    # E la sentinella di P27-7, che tiene un elenco nominativo dei file della
    # Shell toccati da ogni fase. Qui si aggiunge una riga a quell'elenco, con
    # la sua motivazione: la garanzia - "nessuna pagina fuori dalla Rete e'
    # stata toccata" - resta identica per ogni altro file.
    "tests/test_p27_7_network_contracts.py",
    # E il test di NEXT6-P2A che certificava, come garanzia, proprio la
    # chiamata a `sync_rules` dentro `scan`: l'asserzione si rovescia
    # (`assert_not_called`), il soggetto del test - il round robin - resta
    # intatto. Collisione nominata nel file stesso.
    "tests/test_next6_p2a_flow_execution_safety.py",
})

FILE_NUOVI = frozenset({
    "tests/flow_global_security_diff.py",
    "tests/test_flow_global_security.py",
})


# IL ROSSO PREESISTENTE CHE QUESTA FASE NON HA CREATO E NON CORREGGE
#
# `tests/test_p29_3g_final_fixes.py::test_20` fallisce anche senza questa
# fase, e falliva gia' prima che cominciasse. La sua ultima asserzione e'
#
#     for nome in FILE_MODIFICATI:
#         assert nome in tracciati and nome in modificati, nome
#
# cioe' pretende che ogni file dichiarato da P29-3G risulti MODIFICATO nel
# working tree. Era vero finche' quella fase era in corso; dal commit
# ecc9cc2 in poi il working tree non ha piu' nulla da mostrare e la
# condizione non puo' essere soddisfatta. Le quattro sentinelle sorelle
# avevano ricevuto esattamente questa attenuazione al commit della fase
# precedente - "la garanzia si sposta su cio' che resta vero per sempre:
# ogni file dichiarato esiste ed e' nell'indice" - e la sentinella propria
# di P29-3G non l'ha ricevuta, perche' quando fu scritta la fase non era
# ancora stata committata.
#
# La correzione e' una riga (`and nome in modificati` va tolto, come nelle
# sorelle), ma e' una modifica a un test certificato di un'altra fase e sta
# fuori dal perimetro di questa: e' riportata, non applicata.
