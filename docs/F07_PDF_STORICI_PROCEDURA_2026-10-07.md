# F07 — Procedura separata per PDF storici pubblicati

> **Integrazione su `core-0.1-test` (STIMA-CRM-AGENDA-1, 7 ottobre 2026).** Le migrazioni di questa consegna sono state rinumerate perché il ramo aveva già una 092 (`092_cestino_richieste_1_buy_request_trash`): `092_stima_private_pdf` → **`093_stima_private_pdf`**, `093_public_submission_receipts` → **`094_public_submission_receipts`**. I numeri 092/093 citati sotto e nelle patch allegate sono quelli della candidata originale su `f7251ac`.


**Documento per review. Procedura NON eseguita. Nessun PDF storico letto, scaricato, importato, cancellato o modificato durante F07.**

La patch protegge i nuovi documenti della candidata isolata. Non revoca le copie già pubblicate su GitHub, raw/CDN, backend o altri servizi. F07 non può essere dichiarato risolto globalmente fino alla bonifica autorizzata e verificata dello storico. I file già scaricati da terzi non sono revocabili tecnicamente.

## Prima dell'operazione, con autorizzazione distinta

1. Identificare servizi/entrypoint effettivamente attivi, account e custodi dei dati. La candidata approvata usa `main:app`; `whatsapp.py` è un'app legacy separata con `/reports`, `/api/genera_pdf` e `/api/test_pdf`, non importata/montata dal backend principale e non certificata per avvio. Non abilitarla. `cover_pdf.py` e `github_upload.py` sono utility legacy: verificare job o applicazioni esterne che le richiamano prima di dichiarare assenti altre pubblicazioni. Non basta ispezionare la sola candidata.
2. Inventariare privatamente gli origin di pubblicazione effettivi: repository PDF GitHub e cronologia Git, raw.githubusercontent.com, eventuali Pages, CDN/cache, directory `/reports` persistenti/volatili, cartelle servite staticamente, allegati già consegnati e altri storage. Il repository contiene riferimenti, non prova quali servizi siano oggi attivi. Non enumerare URL prevedibili di documenti reali tramite accesso pubblico.
3. Raccogliere in un registro protetto identificatore del documento, digest SHA256, origine, date, stima, agenzia e stato del collegamento. Un nome `stima_<id>.pdf` da solo non prova la relazione. Verificarla contro database e metadati affidabili; documenti ambigui/orfani vanno in quarantena privata, mai associati per supposizione. Non inserire dati personali, token o URL con capability in report, ticket, diff o log generici.
4. Concordare conservazione, accessi e trattamento dei casi ambigui con il responsabile. Nessuna importazione automatica di stime/lead/contatti storici è prevista. L'eventuale recupero dei PDF è un'operazione distinta dall'importazione commerciale dello storico.

## Preparazione e prova su copia privata

5. Eseguire backup privato verificato di database e documenti con manifesti/digest; includere snapshot e BYTEA della nuova tabella, non soltanto i file del vecchio backend. Proteggere accessi, cifratura, retention e custodia delle chiavi secondo l'infrastruttura effettiva. Il test locale F07 dimostra il formato, non certifica backup PROD.
6. Ripristinare il backup su backend/database isolati con invii esterni disabilitati. Verificare corrispondenze, autorizzazioni per agenzia, token mancanti/scaduti/di altra stima, integrità byte/digest, assenza di mount pubblici e completa revoca degli origin simulati.
7. Progettare e approvare l'import dei soli PDF storici con relazione certa nell'archivio privato. Non ricostruire un risultato storico con il motore/catalogo attuale. La 092 non esegue backfill e il servizio corrente non importa né rigenera automaticamente gli storici senza snapshot. Un import storico richiederà un adattamento dedicato: il suo snapshot deve attestare origine/esattezza e impedire una rigenerazione impropria, senza inventare valori mancanti.
8. Definire separatamente l'eventuale sostituzione dei link storici consegnati. Gli operatori usano la propria sessione e agenzia; per accessi pubblici occorre una capability server valida per quella stima. Non riattivare o prolungare automaticamente token scaduti, non inviare messaggi reali e non rilasciare nuovi link senza autorizzazione specifica.

## Finestra coordinata futura

9. Mettere in pausa nuove pubblicazioni e code che possono usare vecchi link; chiudere gli invii per una finestra concordata se necessario. Accertare codice, schema e origini in uso; registrare gli hash di rilascio e un backup immediatamente precedente.
10. Rendere privato o disattivare ciascun origin pubblico individuato; eliminare mount statici e job/upload pubblici. Su GitHub valutare anche cronologia Git, fork/copie e raw/CDN: cancellare il solo file dal branch corrente non è una revoca completa. Le azioni esatte dipendono dagli account e dai servizi effettivi, oggi non verificati.
11. Invalidare cache/CDN e verificare tutti gli URL storici noti da una sessione anonima: nessun byte PDF deve essere restituito. Se un servizio non garantisce la revoca, registrare il limite e mantenere il blocco di produzione; una nuova destinazione privata non elimina la vecchia copia.
12. Abilitare soltanto endpoint privati verificati, recupero di nuovi PDF e, se approvato/implementato, accesso ai documenti storici importati. Collaudare browser, loader email, WhatsApp, operatore stessa agenzia e agenzia diversa. Riattivare le code solo dopo verifica dei link ancora validi; M1 usa `metadata.pdf_url` a J+1 e un ritardo oltre i sette giorni rende il token inutilizzabile.

## Ripristino e criterio di chiusura

13. Conservare lo schema additivo 092 in un rollback applicativo. Il DOWN rifiuta dati presenti; non eliminarli per farlo passare. Un ritorno al codice precedente che ripubblichi su GitHub o riapra `/reports` è un rollback di sicurezza inaccettabile. In caso di incidente sospendere gli invii e usare la versione precedente già compatibile con accesso privato, oppure ripristinare database e applicazione compatibili da un backup collaudato.
14. Il ripristino del database deve considerare anche gli invii successivi al backup, i collegamenti CRM, le notifiche/ledger e le capability già consegnate. Non rigiocare form/email/WhatsApp senza una decisione di recupero e senza garanzie idempotenti; F04/F06 restano aperti.
15. Chiudere F07 storico solo con evidenza di inventario completo, relazione verificata, backup/restore, autorizzazioni corrette e risposta anonima senza PDF su ogni origin noto. Conservare evidenze redatte e dichiarare copie scaricate/fork/cache non controllabili. Nessuna di queste operazioni è autorizzata o eseguita dalla presente consegna.
