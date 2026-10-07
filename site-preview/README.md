# Copia locale Stima360: F01/F13/F16 e recupero R1

> **Integrazione su `core-0.1-test` (STIMA-CRM-AGENDA-1, 7 ottobre 2026).** Le migrazioni di questa consegna sono state rinumerate perché il ramo aveva già una 092 (`092_cestino_richieste_1_buy_request_trash`): `092_stima_private_pdf` → **`093_stima_private_pdf`**, `093_public_submission_receipts` → **`094_public_submission_receipts`**. I numeri 092/093 citati sotto e nelle patch allegate sono quelli della candidata originale su `f7251ac`.
>
> **Asset grafici non versionati (STIMA-CRM-AGENDA-1).** Le 20 immagini di `public/` elencate in `asset-manifest.json` (≈27 MB) non sono nel commit: sono copie byte per byte degli asset pubblicati su `https://www.stima360.it/` (URL, SHA-256 e dimensione nel manifesto, verificati 20/20) e nessun test le usa (i test browser servono solo HTML/JS e bloccano il resto; i journey non controllano le immagini). Per una verifica visiva locale si ricopiano in `public/` dagli originali, controllando gli hash del manifesto.


Questa directory contiene copie dei quattro HTML realmente serviti il 7 ottobre 2026, una baseline immutata, una copia modificata per verifica locale e un config separato candidato per il futuro rilascio. Non è stato modificato il sito pubblico. Non sono stati inviati POST pubblici, email o WhatsApp reali.

## Fonte e limiti

- `baseline/`: risposte GET integrali di `index.html`, `dati_personali.html`, `stima_dettagliata.html` e `pdf_redirect.html` su `https://www.stima360.it/`. I quattro hash corrispondono alle copie dell'audit. `html-provenance.json` registra gli hash.
- `public/`: copie modificabili e asset necessari alla grafica; `asset-manifest.json` documenta 20 copie statiche con hash e dimensioni.
- Ricevuto `Stima360.zip`: 137 originali, inclusi PHP e CSV, conservati in `/private/tmp/stima360-r1-originals-20261007`, cartella privata non servita. I quattro HTML e i venti asset della preview coincidono byte per byte con gli originali. `original-source-manifest.json` documenta tutti gli hash. Non sono disponibili repository/build del frontend, configurazione del server e ricevitori Apps Script. Il pacchetto originale contiene anche log e staging diretta alle API pubbliche: non avviarlo o pubblicarlo integralmente.
- Le altre pagine del sito e i loro link restano quelli originali; qui non sono clonate tutte le pagine collegate. Le API di successo/SEO, font e librerie esterne non costituiscono una nuova implementazione locale.
- `nuvole.png`, `cloud/cloud.png` e `cloud/cloud5.png` hanno restituito HTTP 404 durante le GET. Nessun asset mancante è stato inventato. `cloud/Cloud.png` esiste: su filesystem macOS un server permissivo può risolvere erroneamente anche la richiesta minuscola. Per riprodurre quel 404, rispettare il case dei percorsi del manifesto.
- Le dichiarazioni CSS e tutti gli `src`/`href` statici originali sono preservati. R1 estende soltanto cinque selettori del pulsante personali al suo stato `type=button`, mantenendone stili e pseudo-elementi. L'unico nuovo riferimento statico è `preview-config.js`. F03 (vista mare) e gli altri rilievi non sono stati corretti.

## Correzione proposta

F13: i valori di query/prefill entrano nei riepiloghi e nel risultato mediante nodi DOM e `textContent`; le etichette, il markup statico e gli stili rimangono. Un marcatore innocuo `<b>...</b>` viene mostrato come testo.

F16: `dati_personali.html` salva l'ID del server e il token solo dopo una risposta valida di `salva_stima`. I link restituiti dal backend devono contenere quel token. Il redirect PDF autorizza il pulsante soltanto dopo un prefill valido del token presente nella propria URL, senza fallback a un'altra stima in `localStorage`. La dettagliata disabilita il modulo finché il token/prefill non è valido e invia il token più l'ID del prefill, sovrascrivendo eventuali ID hidden alterati. Un POST 401/403/404 chiude nuovamente il modulo.

Il backend deve sempre convalidare token, scadenza, stima e agency sul server. La verifica client non costituisce autorizzazione server e non introduce alcun bypass.

## Verifica locale

Servire soltanto `public/` su un'origine loopback e usare un backend isolato autorizzato. `public/preview-config.js` usa `http://127.0.0.1:8000` per le API, riscrive i link del server sull'origine della pagina locale, limita API/PDF a loopback e disattiva gli eventi analytics nella sola copia.

Per cambiare la porta del backend, definire `window.STIMA360_PREVIEW_API_BASE` prima del caricamento di `preview-config.js` (per esempio con `context.addInitScript` nel test browser). Il config locale rifiuta un host esterno. Non servire `baseline/` come un sito operativo: contiene gli script originali diretti ai servizi pubblici.

Test browser, con Playwright e Chromium disponibili:

```sh
node --test tests/test_public_site_frontend.mjs
```

Sono supportate le variabili `NODE_PATH` per il runtime Playwright, `STIMA360_CHROMIUM_PATH` per il browser e `STIMA360_SITE_DIR` per una directory HTML alternativa. Tutte le richieste dei test sono intercettate: nessun servizio esterno viene contattato. I test di config release valutano solo helper e valori URL nel browser, senza eseguire richieste pubbliche.

RED registrato prima della patch: 18/18 casi iniziali falliti; quattro casi aggiunti per token/risposte incoerenti falliti; il caso di scadenza tra prefill e POST403 fallito. I tre casi di config release hanno fallito prima della creazione di quel candidato. Verifica finale del comando browser: 26 test passati, 0 falliti, 0 saltati. Una precedente esecuzione del caso POST403 aveva letto lo stato prima del completamento del callback: il test ora attende il feedback effettivo del modulo. Ogni cambiamento richiede un nuovo comando.

Sono disponibili otto screenshot in `evidence/` e il rapporto `layout-review.json`: stili confrontati uguali su tutte e quattro le pagine; personali, dettagliata e redirect pixel-identici nel viewport desktop 1280×950. La home differisce esclusivamente nell’istante campionato del titolo con animazione di scrittura già esistente. Font/CDN esterni sono bloccati in entrambi i rendering, gli asset sono le copie locali e le API rispondono con fixture sintetiche.

## Candidato per attivazione futura coordinata

`release/preview-config.js` conserva lo stesso contratto degli helper ma usa esplicitamente `https://stima360-backend.onrender.com` e `https://www.stima360.it`, verifica origine/percorso/token dei link, costruisce soltanto endpoint PDF del backend autorizzato. La home/personali mantengono analytics originali; redirect e dettagliata non caricano Pixel o noscript con capability nell'URL. Nessuna pagina locale carica questo file.

Prima di una futura attivazione autorizzata, verificare che i sorgenti effettivamente installati coincidano ancora con il pacchetto originale ricevuto; integrare le sole differenze della proposta. Il confronto ZIP/base è già positivo. La candidata F07 è ora `f7251ac`, con gli avanzamenti CESTINO-EDIFICI-1 conservati; `4833eae` resta la base della precedente consegna R1. Il patch coordinato comprende i quattro HTML modificati, il nuovo helper con il contenuto di `release/preview-config.js` al percorso pubblico `preview-config.js` e il backend con i campi additivi `token`, `detail_url`, `pdf_redirect_url` più il controllo server obbligatorio del dettaglio. Non pubblicare il config locale loopback. Non caricare `baseline/`, manifesti, screenshot o test sul sito pubblico.

Concordare una finestra di attivazione e invalidare le copie in cache degli HTML/helper nella stessa finestra: vecchie pagine senza token devono essere rifiutate, senza un periodo di bypass. F07 ora richiede la migrazione backend 092 e download privati; gli storici pubblici restano invariati e costituiscono un blocco separato. Non pubblicare una sola metà della modifica. Commit, push, deploy e operazioni di produzione restano una successiva azione esplicitamente autorizzata.

## Aggiornamento R1 e verifica sui sorgenti originali

Il pulsante riappare dopo errori rete, HTTP, JSON o risposta senza capability valida, con il testo «Ricarica la pagina». Il messaggio indica un invio NON CONFERMATO e invita a controllare email/WhatsApp. La ricarica richiede il click dell'utente e non effettua un altro POST. Il modulo blocca ulteriori submit in pending, success e uncertain; dati inseriti conservati fino alla ricarica. Nessun recupero da token di un'altra stima. F04/F06 restano aperti oltre questa guardia della pagina.

I conteggi precedenti sopra descrivono la consegna storica. Prove nuove: 39 browser passati, 2 journey integrati passati sul backend `4833eae`, 47 PostgreSQL token/catalogo/pertinenze/cestino+tenant e 11 Cestino. 140+11 regressioni offline passate. La suite completa non è stata rieseguita sul nuovo SHA; i 22 fallimenti storici non sono stati cancellati. Vedere `../docs/F01_F13_F16_R1_REVIEW_2026-10-07.md` ed evidenze.

Diff corrente completo: `frontend-review-r1.diff`; soli HTML applicabili agli originali: `frontend-html-r1.patch`; incremento rispetto alla precedente consegna: `personal-recovery-r1.patch`. Il vecchio diff è conservato come evidenza della consegna precedente.

## Aggiornamento F07 — candidato, nessun deploy

`pdf_redirect.html` ignora ogni query `pdf`, verifica `/api/prefill`, ricava l'ID dal server e chiama `GET /api/stime/{id}/pdf?t=token`. Dopo byte PDF validi offre «Scarica il PDF» tramite blob privato nella stessa pagina.503 offre «Recupera il PDF», che chiama solo `POST /api/stime/{id}/pdf/retry?t=token`: non ripete l'invio personale. Token mancante, negato o scaduto chiude i link. Il ritorno BFCache ripete la verifica e scarta risposte obsolete; nessun fallback da localStorage.

Pixel/noscript sono rimossi dalle sole pagine con token (redirect e dettagliata), e `no-referrer` precede le risorse esterne. Gli stili originali e il corpo della dettagliata sono conservati. Il confronto pixel-identico riguarda la scena con GET PDF pendente e nuovi controlli nascosti; lo stato pronto mostra il nuovo link tecnico e l'errore il pulsante di recupero.

Prove F07 browser: 69 PASS, 0 fail, 0 skip. RED riprodotti prima dei fix:23 casi PDF,6 privacy,3 BFCache. I conteggi precedenti nelle sezioni sopra sono evidenza storica. Per backend, migrazione, prove integrate aggiornate e limiti vedere `../docs/F07_REVIEW_2026-10-07.md` e la procedura separata per PDF storici. Il backend di produzione e il sito online non sono stati modificati.


## Aggiornamento F04/F06 — PRONTO PER REVIEW, nessun deploy

Le sezioni precedenti registrano le consegne F01/F13/F16/R1/F07. La candidata aggiornata è `/private/tmp/stima360-f0406-backend`, core-0.1-test/f7251ac, e aggiunge `submission.js` a index/personali/dettagliata. Il redirect PDF F07 e gli asset/logo restano identici.

Il nuovo helper salva UUID/prova/snapshot prima del POST; al reload legge la ricevuta senza nuovi invii. Partial/received richiedono ripresa esplicita; attention non reinvia trasporti incerti;410 richiede un nuovo intento. Sono collaudati18 nuovi test e69 regressioni browser, più6 journey integrati. Chromium collaudato; altri browser non verificati. Storage persistente/crypto e Web Locks per la prima dettagliata condivisa sono prerequisiti: fallimento chiude il form prima del POST.

Per la futura attivazione coordinata occorrono migrazioni092/093, backend cumulativo e frontend con `submission.js`/helper release. Vecchi HTML senza ricevuta vengono rifiutati: non attivare una sola metà. Il report `../docs/F04_F06_REVIEW_2026-10-07.md` contiene risultati, diff, limiti, istruzioni e rollback. Tutti gli altri blocchi di produzione restano aperti.

## Aggiornamento F04/F06 R1 — PRONTO PER REVIEW R1, nessun deploy

Una notifica email amministrativa/WhatsApp incerta (`attention`) o fallita prima del trasporto (`partial`) non nasconde più un risultato pronto. Se il server certifica `receipt.result_available` (stima, collegamenti CRM, capability valida e PDF privato pronti), `dati_personali.html` mostra valore, ricevuta, redirect allo stesso PDF privato e link alla dettagliata, con la nota che la conferma via email/WhatsApp è in verifica; nessun reinvio automatico e nessun pulsante di recupero. Ogni altro partial/attention conserva il recupero esplicito. `submission.js` rifiuta certificazioni non valide. Grafica, logo e CSS invariati.

Patch: `frontend-release-f0406-r1.patch` (cumulativa sui quattro HTML originali, con helper release e `submission.js`) e `frontend-f0406-r1-incremental.patch` (solo R1). `frontend-release-f0406.patch` resta l'evidenza pre-R1. Rapporto e prove: `../docs/F04_F06_R1_REVIEW_2026-10-07.md`, `../docs/F04_F06_R1_evidence/`.
