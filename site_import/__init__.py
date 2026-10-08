"""SITE-IMPORT-1 - il CRM legge il sito, il sito non sa nulla del CRM.

`www.stima360.it` e il suo backend (`main` PROD) restano esattamente come
sono: salvano le stime nel LORO database, generano il PDF, mandano l'email
iniziale. Questo pacchetto e' il collegamento mancante dal lato CRM:

    database del sito (SOLA LETTURA)  ->  database del CRM
      stime, stime_dettagliate             stima con lo STESSO id, contatto,
      PDF nell'archivio del sito           lead, immobile, pertinenze, PDF
                                           originale, richiesta in Agenda

Riusa le funzioni gia' esistenti del CRM - le stesse che il funnel pubblico
del CRM chiama dopo il salvataggio - e non ne introduce di nuove:

  * routing territoriale dell'agenzia     network_routing.service
  * contatto, lead, consenso              core.service.bridge_public_stima
  * immobile e pertinenze                 property.site_sync
  * evento di storia                      seller_intelligence.service
  * task "Contattare proprietario"        followup.service (solo stime recenti)
  * richiesta di sopralluogo in Agenda    appointments_legacy.site_hook

COSA NON FA, DI PROPOSITO: non scrive MAI nel database del sito (la sessione
e' read-only e il modulo `source` emette solo SELECT), non manda email,
WhatsApp o altro (nessun messaggio accodato, nessuna sequenza commerciale
attivata: al piu' il task interno "Contattare proprietario" per l'agente),
non rigenera PDF (il PDF e' quello originale dell'archivio, o manca e lo dice).

Chiave univoca: `site_import_records (source, source_table, source_id)` (095).
"""
