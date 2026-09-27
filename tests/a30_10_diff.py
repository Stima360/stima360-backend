"""A30-10B - l'inventario dichiarato di questa fase (INBOUND: Google Calendar
-> Agenda), tredicesima dichiarazione dell'Agenda accanto a `a30_1_diff` ...
`a30_9b_diff`: le sentinelle di working tree ne leggono l'UNIONE, come per
ogni fase precedente.

Due file NUOVI nei package esistenti (`operator_auth/calendar_inbound.py`, la
factory server-side del TERZO `auth_channel`, decisione D2; e
`calendar_sync/inbound.py`, il worker inbound), una migration NUOVA (075: la
coda inbound, colonne PROPRIE su `appointment_calendar_sync`, decisione D6 -
nessuna colonna outbound riusata) e due file di test NUOVI (la sentinella
statica e la suite PostgreSQL di questa fase).

Modificati: `calendar_sync/provider.py` (il contratto `RemoteEvent`/
`get_event`), `calendar_sync/google_provider.py` (l'implementazione REST della
GET), `calendar_sync/fake_provider.py` (le simulazioni inbound per i test),
`calendar_sync/repository.py` (le primitive della coda inbound: claim, CAS,
finalize/retry/release - colonne proprie, mai quelle outbound),
`run_calendar_sync_cron.py` (l'ordine inbound-poi-outbound nello stesso
giro), e le sentinelle aggiornate "in un modo dichiarato" (§44) altrove in
questo file: la stessa disciplina di A30-9A/9B quando hanno alzato la coda
delle migration.

REVIEW FIX GATE (dopo la certificazione iniziale, tre fix dichiarati):

* FIX 1 - `operator_auth/context.py`: `AUTH_CHANNELS` passa da due a tre
  elementi, registrando esplicitamente `calendar_inbound` (che esisteva
  gia' in codice, come stringa/costante nella factory, ma non nel modello
  autorevole). Nessun comportamento HTTP/cookie/session cambia - la tupla
  non ha enforcement a runtime (nessun `__post_init__`), e' documentazione
  del set chiuso. `tests/test_p26_1_operator_auth.py` e
  `tests/test_p28_superadmin_acting.py` (sentinelle di fase precedenti che
  asserivano il vecchio 2-tuple) e la sentinella statica di questa fase
  (`tests/test_a30_10_calendar_inbound_static.py::test_d2_04`) sono
  aggiornate di conseguenza. Questo file dichiara `operator_auth/context.py`
  qui sotto perche' e' uno dei domini sorvegliati da `p29_3b_diff`
  (`tests/p29_3b_diff.py`, a sua volta esteso qui per riconoscere questa
  fase come dichiarante, cosi' come fece P29-3C/3D).
* FIX 2 - `calendar_sync/inbound.py`: il ramo D1 (assignee-mismatch) ora
  richiede la riconciliazione OUTBOUND della stessa catena
  (`repository.request_resync`), la STESSA primitiva del ramo D4. Prima si
  limitava a rilasciare il claim senza chiedere nulla all'outbound - un gap,
  non un secondo comportamento nuovo - quindi non serve un file NUOVO, solo
  il fix minimo su un file gia' dichiarato sopra.
* FIX 3/4 non toccano file applicativi (temp file sul Mac; ambiente
  PostgreSQL di test).
"""
from __future__ import annotations

FILE_NUOVI = frozenset({
    "migrations/075_a30_10_calendar_inbound.sql",
    "migrations/075_a30_10_calendar_inbound_down.sql",
    "operator_auth/calendar_inbound.py",
    "calendar_sync/inbound.py",
    "tests/a30_10_diff.py",
    "tests/test_a30_10_calendar_inbound_static.py",
    "tests/test_a30_10_calendar_inbound_postgres.py",
})

FILE_MODIFICATI = frozenset({
    # il contratto GET + l'implementazione REST + il provider finto (§ nuovo
    # metodo `get_event`/`RemoteEvent`, nessun contratto esistente cambiato)
    "calendar_sync/provider.py",
    "calendar_sync/google_provider.py",
    "calendar_sync/fake_provider.py",
    # le primitive della coda inbound (colonne PROPRIE, 075: nessuna funzione
    # outbound esistente cambia comportamento) + FIX 2 (D1: request_resync)
    "calendar_sync/repository.py",
    # l'ordine di UN giro: inbound, poi outbound (stesso runner di A30-9B)
    "run_calendar_sync_cron.py",
    # REVIEW FIX GATE, FIX 1: il modello autorevole dei channel (§ sopra).
    # Uno dei domini sorvegliati da `p29_3b_diff` - dichiarato qui, non
    # riga per riga in quel file, come P29-3C/3D per `communication/`.
    "operator_auth/context.py",
    # la sentinella che riconosce questa fase come dichiarante di
    # `operator_auth/context.py` (v. `_dichiarati_da_fasi_successive`).
    "tests/p29_3b_diff.py",
    # sentinelle di fase precedente che asserivano il vecchio 2-tuple.
    "tests/test_p28_superadmin_acting.py",
    # le sentinelle aggiornate in un modo dichiarato (§44): la 075 e' la
    # nuova cima della serie delle migration, e il TERZO `auth_channel`
    # (`calendar_inbound`) e' il nuovo modulo atteso in `operator_auth/`.
    "tests/test_a30_1_appointments.py",
    "tests/test_a30_2p_073.py",
    "tests/test_lmc11_valuation_cron.py",
    "tests/test_lmc13_home_metrics.py",
    "tests/test_lmc15_acquisition_bridge.py",
    "tests/test_lmc1b_owner_login_link.py",
    "tests/test_lmc2_owner_homes.py",
    "tests/test_lmc3_valuation_snapshot.py",
    "tests/test_lmc7_owner_radar.py",
    "tests/test_lmc8_crm_radar.py",
    "tests/test_lmc9_consultation_request.py",
    "tests/test_p26_1_operator_auth.py",
    "tests/test_p27_6_lead_routing.py",
    "tests/test_p29_1_consent_migrations.py",
    "tests/test_p29_2_1_communication_foundation.py",
    "tests/test_p29_2_2_communication_service.py",
    "tests/test_p29_2_3_claim_sentinels.py",
    "tests/test_p29_2_4_dispatch_sentinels.py",
    "tests/test_p29_2_5e_email_adapter.py",
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3d_crm_journey.py",
    "tests/test_p29_3e_cron_integration.py",
    "tests/test_p29_3g_final_fixes.py",
})
