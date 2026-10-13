"""CESTINO-GENERALE-1 - il motore condiviso del Cestino unico del CRM.

Fase CG-1: SOLA LETTURA. Un elenco unificato degli elementi nel Cestino di
tutte le categorie gia' dotate di Cestino (immobili, contatti, edifici,
richieste acquirente), con ricerca, filtri, contatori, anteprima delle
relazioni e le richieste di eliminazione degli edifici; per l'agenzia di chi
chiede, e per il Super Admin generale designato su tutte le agenzie.

Il pacchetto NON sposta, NON ripristina, NON cancella nulla: le operazioni
restano quelle dei moduli di ciclo di vita esistenti (property/lifecycle.py,
core/contact_lifecycle.py, property/building_deletion.py, buy/lifecycle.py),
invariati. Qui ci sono solo letture, con le STESSE regole di visibilita' dei
loro elenchi (una prova di parita' lo verifica categoria per categoria).
"""
