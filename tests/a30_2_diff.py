"""A30-2 - l'inventario dichiarato dei file della fase.

Si aggiunge a `tests/a30_1_diff.py` (non lo sostituisce): le sentinelle di
working tree leggono l'UNIONE delle due dichiarazioni. Un file che non compare
in nessuna delle due, e in nessun'altra fase, le fa ancora fallire.
"""

FILE_NUOVI = frozenset({
    # moduli nuovi del pacchetto Agenda (il pacchetto e' gia' dichiarato da
    # A30-1 come directory non tracciata)
    "appointments/errors.py",
    "appointments/state_machine.py",
    "appointments/availability.py",
    "appointments/projection.py",
    "appointments/router.py",
    "tests/a30_2_diff.py",
    "tests/test_a30_2_appointments.py",
    "tests/test_a30_2_appointments_postgres.py",
    "docs/A30_2_SERVICE_API.md",
})

#: File di A30-1 (ancora non tracciati) che A30-2 estende. Sono gia' nuovi per
#: git: si nominano qui perche' il report li dichiari, non per le sentinelle.
FILE_A30_1_ESTESI = frozenset({
    "appointments/schemas.py",
    "appointments/repository.py",
    "appointments/service.py",
    "tests/test_a30_1_appointments.py",
})

#: File gia' tracciati che A30-2 modifica.
#:  - `acquisition/repository.py`: COLLISIONE DICHIARATA con LMC-15 certificato
#:    (Q1): varianti `*_in(cur, ...)` senza commit interno; le funzioni
#:    pubbliche restano con la stessa firma e lo stesso comportamento.
#:  - le cinque sentinelle di working tree che imparano questa dichiarazione;
#:  - il registro dei punti di connessione P26 (test h11 + documento), per il
#:    test Postgres opt-in di A30-2.
FILE_MODIFICATI = frozenset({
    "acquisition/repository.py",
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3d_crm_journey.py",
    "tests/test_p29_3e_cron_integration.py",
    "tests/test_p29_3g_final_fixes.py",
    "tests/test_p26_db_entrypoints.py",
    "docs/P26_DB_ENTRYPOINTS.md",
})
