STIMA360 PROPERTY 0.2.1 — verifica e correzioni

File da sostituire sul branch core-0.1-test:
- property/repository.py
- static/property_admin/assets/app.js

Nessuna migrazione database richiesta.

Correzioni:
- creazione nuovo immobile dalla UI: rimossi i campi di storico non ammessi dal POST;
- completezza disponibile anche nella lista immobili;
- documenti scaduti per data conteggiati come criticità;
- KPI e avvisi escludono gli immobili archiviati;
- incarichi scaduti o in scadenza entro 30 giorni evidenziati;
- visite odierne calcolate nel fuso Europe/Rome;
- verifica esistenza contatto/lead nelle visite;
- aggiornamento documenti validato prima del vincolo DB;
- duplicazione codice immobile restituisce conflitto gestito;
- filtri UI per città, assegnatario, incarichi e documenti;
- tipi immobile rustic e building disponibili nella UI.

Verifiche locali:
- compilazione Python: OK
- sintassi JavaScript: OK
- test schema PROPERTY: 3 superati

Produzione non coinvolta.
