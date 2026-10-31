# BLOCCO-5 — Firma elettronica dei fogli visita (e, in futuro, degli incarichi): ricerca fornitori

Data di consultazione di tutte le fonti: **31/10/2026**.
Natura del documento: **sola ricerca**. Nessun account creato, nessun acquisto, nessun codice o schema modificato.

## 0. Metodo e limiti (da leggere prima)

- Fonti ammesse: solo siti e documentazione API ufficiali dei fornitori e fonti normative ufficiali
  (EUR-Lex, AgID, Gazzetta Ufficiale/testo CAD pubblicato da AgID e docs.italia.it).
- **Limite tecnico di questa sessione**: la lettura diretta delle pagine era bloccata dalla rete della sessione
  (`developers.yousign.com`, `eur-lex.europa.eu`, `developers.docusign.com` non raggiungibili). Le informazioni
  sono state ricavate dal **motore di ricerca limitato ai domini ufficiali**, che restituisce estratti delle pagine
  indicizzate. Ogni affermazione ha l'URL ufficiale della pagina da cui proviene, ma **prima di scrivere codice
  ogni dato va riletto direttamente sulla pagina**: alcuni estratti provenivano da pagine indicizzate anni fa.
- Dove un dato non è emerso dalle fonti ufficiali è scritto **"non pubblico / da chiedere al fornitore"** oppure
  **"non verificato"**. Nessun prezzo, endpoint o valore legale è stato dedotto o inventato.
- Terminologia: FES = firma elettronica (semplice) / SES; FEA = firma elettronica avanzata / AES;
  FEQ = firma elettronica qualificata / QES; QTSP = prestatore di servizi fiduciari qualificato.

---

## 1. Schede fornitori

### 1.1 Yousign (oggi anche marchio "Youtrust")

| Voce | Dato | Fonte |
|---|---|---|
| Tipi di firma | SES e AES (consensi registrati nell'audit trail "for both SES and AES"); QES lanciata nell'API, con identificazione tramite documento e video in tempo reale | https://developers.yousign.com/changelog/request-consents-from-signers ; https://developers.yousign.com/changelog/qualified-electronic-signature-is-out ; https://yousign.com/advanced-electronic-signature |
| QTSP | Dichiara di essere qualificata da ANSSI e inclusa nella Trusted List francese prodotta per la Commissione europea (quindi non nell'elenco AgID, che riguarda i prestatori stabiliti in Italia). Verificare l'iscrizione sull'elenco di fiducia UE | https://yousign.com/terms ; https://yousign.com/en-eu/eidas |
| API | REST v3, JSON (upload del documento in binario); base URL sandbox `https://api-sandbox.yousign.app/v3/`; autenticazione Bearer | https://developers.yousign.com/docs ; https://developers.yousign.com/reference/get-signature_requests-signaturerequestid-documents-documentid-1 |
| Creazione richiesta | Esiste la risorsa Signature Request (creazione con `name`, `delivery_mode: "email"`, `timezone`; l'id serve per le chiamate successive). Lingua dell'audit trail impostabile con `audit_trail_locale` (incluso `it`). Metadati associabili alla richiesta (`GET /signature_requests/{id}/metadata`) | https://developers.yousign.com/docs/metadata ; https://developers.yousign.com/changelog/configure-pt-for-your-audit-trails-in-portuguese ; https://developers.yousign.com/docs/signature-request-2 |
| Documento / firmatari / attivazione | Lettura documento: `GET /v3/signature_requests/{signatureRequestId}/documents/{documentId}`. **Percorsi esatti di upload documento, aggiunta firmatario, attivazione, annullamento: non verificati** in questa sessione → leggere la specifica OpenAPI | https://developers.yousign.com/reference/get-signature_requests-signaturerequestid-documents-documentid-1 ; https://developers.yousign.com/reference/oas-specification ; https://developers.yousign.com/openapi |
| OTP / identificazione | OTP via SMS esistente (testo personalizzabile via API); OTP via email esistente (citato negli eventi di notifica); QES con documento d'identità + video. **Nomi esatti dei campi (livello di firma, modalità di autenticazione): non verificati** | https://developers.yousign.com/changelog/custom-one-time-password-otp-sms ; https://developers.yousign.com/reference/signer-notification_delivery_failed ; https://developers.yousign.com/changelog/qualified-electronic-signature-is-out |
| Documento firmato e audit trail | Audit trail esistente (localizzabile, registra i consensi). **Endpoint di download del documento firmato e dell'audit trail: non verificati** | https://developers.yousign.com/changelog/request-consents-from-signers |
| Webhook — eventi confermati | `signature_request.done` (richiesta completata, tutti i firmatari richiesti hanno firmato: ongoing → done); `signature_request.declined` (ongoing → declined); `signature_request.canceled` (annullata manualmente: approval/ongoing → canceled); `signature_request.approved`; `signature_request.reminder_executed` (stato invariato); `signer.done` (firmatario ha firmato); `signer.notification_delivery_failed` (fallita la prima email al firmatario). **`signature_request.activated` / `signature_request.expired`: non verificati** | https://developers.yousign.com/reference/signature_request-done ; https://developers.yousign.com/reference/signature_request-declined ; https://developers.yousign.com/reference/signature_request-canceled ; https://developers.yousign.com/reference/signature_request-approved ; https://developers.yousign.com/reference/signature_request-reminder_executed ; https://developers.yousign.com/reference/signer-done ; https://developers.yousign.com/reference/signer-notification_delivery_failed ; https://developers.yousign.com/docs/follow-the-signature-requests-status |
| Webhook — autenticità | Header **`X-Yousign-Signature-256`**; valore = `"sha256="` + **HMAC-SHA256** del **payload grezzo** con il `secret_key` della Webhook Subscription (copiato dall'app); confronto a tempo costante (`hmac.compare_digest` negli esempi) | https://developers.yousign.com/docs/security ; https://developers.yousign.com/docs/use-webhooks-in-your-app |
| Webhook — retry / idempotenza | Header **`X-Yousign-Retry`** (contatore dei tentativi) e **`X-Yousign-Issued-At`** (istante di invio). Reti private non supportate come destinazione (192.168.x.x, 10.x.x.x, 172.16.x.x). Payload con campo booleano `sandbox`. Iscrizione filtrabile per workspace. **Numero/intervallo dei retry e nome esatto del campo ID evento: non verificati** (schema eventi in `webhook-events.json`) | https://developers.yousign.com/docs/use-webhooks-in-your-app ; https://developers.yousign.com/docs/retrieve-bank-account-lookup-verification-results ; https://developers.yousign.com/changelog/associate-workspaces-with-webhook-subscriptions ; https://developers.yousign.com/openapi |
| Sandbox | Gratuita per **40 giorni**; le firme in sandbox non consumano quota; limiti di rate diversi tra sandbox e produzione | https://yousign.com/pricing-api ; https://developers.yousign.com/changelog/title-to-be-defined |
| Data residency / GDPR | Infrastruttura in Francia; DPA ex art. 28 GDPR (legge francese, CNIL); elenco subresponsabili con alcuni fornitori extra-UE coperti da SCC/DPF; archiviazione delle prove presso Arkhineo; ISO 27001 dichiarata | https://yousign.com/security ; https://yousign.com/data-processing ; https://yousign.com/subprocessors ; https://yousign.com/privacy |
| Costi pubblici (31/10/2026) | **API Plus** da 104 €/mese IVA escl. (1.248 €/anno), **API Pro** da 129 €/mese (1.548 €/anno), entrambi con 500 firme semplici/anno; oltre quota 2,00 € a firma; **API Scale** su preventivo; piani API solo annuali. Prezzo AES/QES via API: **non pubblico / da chiedere al fornitore** (la pagina dell'applicazione web, non API, indica QES a 10–15 € l'una). Nota: la home riporta "da 106 €/mese": discrepanza da chiarire | https://yousign.com/pricing-api ; https://yousign.com/pricing-application ; https://yousign.com/api |
| Requisiti tecnici | HTTPS pubblico per i webhook (no IP privati); corpo grezzo disponibile al router per l'HMAC; chiave API Bearer distinta tra sandbox e produzione; upload binario del PDF | fonti sopra |

### 1.2 Namirial — eSignAnyWhere

| Voce | Dato | Fonte |
|---|---|---|
| Tipi di firma | FES, FEA (anche OTP SMS e grafometrica), FEQ; certificati per firme qualificate o avanzate | https://www.namirial.it/dettagli/esignanywhere/ ; https://www.namirial.it/gestione-pratiche-firma/ |
| QTSP | QTSP eIDAS; **iscritta nell'elenco AgID** dei prestatori attivi in Italia (dal 2010) | https://www.agid.gov.it/it/piattaforme/firma-elettronica-qualificata/prestatori-di-servizi-fiduciari-attivi-in-italia ; https://www.namirial.it/gestione-pratiche-firma/ |
| API | REST v6, sandbox `https://demo.esignanywhere.net/Api/v6.0/` con Swagger su `demo.esignanywhere.net/Api`; stato `GET .../envelope/{envelopeId}`; file finali sotto `/files` (solo per buste completate); esiste anche API SOAP | https://www.esignanywhere.net/esignature-api/api-documentation/ ; https://www.esignanywhere.net/esignature-api/api-documentation/api-reference-rest/ ; https://confluence.namirial.com/display/eSign/Serverside+Postprocessing+using+Callback+Handlers ; https://confluence.namirial.com/display/eSign/API+Reference+-+SOAP |
| Creazione busta / firmatari / OTP | **Forma esatta della richiesta di creazione v6: non verificata** (leggere lo Swagger) | https://www.esignanywhere.net/esignature-api/tutorial-hello-world/ |
| Callback / eventi | *Envelope callback* solo a stato finale (completed, rejected); *Envelope status callback* con `workstepFinished`, `workstepRejected`, `workstepDelegated`, `workstepOpened`, `sendSignNotification`, `envelopeExpired` | https://confluence.namirial.com/display/eSign/Serverside+Postprocessing+using+Callback+Handlers |
| Autenticità callback | **Nessuna firma HMAC documentata**: autenticazione del callback "None o Basic" (configurabile per pattern URL), solo HTTPS; il callback è una chiamata URL con l'envelope-id: il fornitore raccomanda di **riverificare via API** (`GET envelope/{id}`) | https://confluence.namirial.com/display/eSign/Organization+Settings ; https://confluence.namirial.com/display/eSign/Developer+FAQ |
| Retry / idempotenza | Il callback viene richiamato fino a **30 volte** se non riceve subito HTTP 200; il gestore deve essere idempotente (deduplica per envelope-id) | https://confluence.namirial.com/display/eSign/Developer+FAQ ; https://confluence.namirial.com/display/eSign/Serverside+Postprocessing+using+Callback+Handlers |
| Sandbox | Account di test gratuito su `demo.esignanywhere.net` (solo test; produzione su SaaS o privato) | https://demo.esignanywhere.net/start.html ; https://confluence.namirial.com/display/eSign/Developer+Mode |
| Data residency / DPA | **Non pubblico / da chiedere al fornitore** (non emerso dalle fonti consultate) | — |
| Costi pubblici (31/10/2026) | eSignAnyWhere **Basic 240 €/anno + IVA** (1.000 pratiche/anno, solo FES); **Premium 360 €/anno + IVA** (2.000 pratiche/anno, include FEQ, OTP SMS, grafometrica); varianti 2–3 utenti. **Se il piano includa l'accesso API: non verificato / da chiedere** | https://www.namirial.it/dettagli/esignanywhere/ ; https://www.namirial.it/shop/ |
| Requisiti tecnici | Callback HTTPS; riconciliazione obbligatoria via API dopo ogni callback | fonti sopra |

### 1.3 InfoCert (Tinexta) — GoSign / infocert-sign

| Voce | Dato | Fonte |
|---|---|---|
| Tipi di firma | FEA e FEQ utilizzabili a seconda del certificato configurato | https://knowledgecenter.infocert.digital/documents/Manuale_Utente_GoSign_Web-ITA.pdf |
| QTSP | **Iscritta nell'elenco AgID** (dal 2007) | https://www.agid.gov.it/it/piattaforme/firma-elettronica-qualificata/prestatori-di-servizi-fiduciari-attivi-in-italia |
| API | REST con Swagger; servizio principale *Insert Envelope*; OAuth2 client credentials (token JWT con client id/secret/scope); stati busta `IN_PROGRESS`, `WORKED`, `REJECTED`, `EXPIRED`, `STORED`, `CANCELLED` | https://developers.infocert.digital/gosign/api-reference/ ; https://developers.infocert.digital/gosign/before-you-begin/ ; https://devportal.infocert.digital/gosigndp/documentation/GoSign5ExtApi5_1 |
| Notifiche | Servizi di notifica *Get Status*, *Notify*, *Status changes*, *User Workability*. **Payload, autenticità, retry: non verificati / da chiedere** | https://developers.infocert.digital/gosign/use-cases/ ; https://developers.infocert.digital/gosign/api-reference/api-swagger/ |
| Sandbox | Accesso gratuito alla sandbox Tinexta InfoCert su richiesta (programma Business Partner); disponibilità specifica per GoSign da confermare | https://www.infocert.it/business-partner |
| Data residency / DPA | **Non pubblico / da chiedere al fornitore** | — |
| Costi pubblici (31/10/2026) | GoSign Business 149 € + IVA/anno (promo 99,90 €); PRO 49 € + IVA/anno; bundle Business + Firma Remota 228 € (promo 179 €). Sono licenze applicative: **prezzo API non pubblico / da chiedere** | https://www.infocert.it/pagina-trasparenza-tariffaria ; https://firma.infocert.it/prodotti/gosign |

### 1.4 DocuSign

| Voce | Dato | Fonte |
|---|---|---|
| Tipi di firma | SES, AES, QES | https://www.docusign.com/en-gb/products/electronic-signature/advanced-electronic-signature ; https://www.docusign.com/en-gb/products/electronic-signature/qualified-electronic-signature |
| QTSP | DocuSign France SAS dichiarata QTSP nella Trusted List francese (ANSSI) | https://www.docusign.com/trust/compliance/global-standards |
| API | eSignature REST API (envelope); stati busta e destinatari documentati | https://developers.docusign.com/docs/esign-rest-api/esign101/concepts/envelopes/status-codes/ ; https://developers.docusign.com/docs/esign-rest-api/esign101/concepts/recipients/status-codes/ |
| Webhook (Connect) | Eventi tra cui `envelope-completed`, `envelope-declined`, `envelope-voided` (anche scadenza, distinta dal motivo di annullamento); formato JSON SIM raccomandato | https://developers.docusign.com/platform/webhooks/connect/event-triggers/ ; https://developers.docusign.com/platform/webhooks/connect/json-sim-event-model/ |
| Autenticità | Header `X-DocuSign-Signature-1` (…`-2` per ogni chiave aggiuntiva): HMAC-SHA256 del corpo, confronto in base64; basta una corrispondenza | https://developers.docusign.com/platform/webhooks/connect/hmac/ ; https://developers.docusign.com/platform/webhooks/connect/validate/ |
| Retry | ACK = HTTP 200 entro 100 s; back-off 5′, 10′, 20′, 40′, 1h, 2h, 1 giorno, poi giornaliero per 15 giorni | https://developers.docusign.com/platform/webhooks/connect/ ; https://developers.docusign.com/platform/webhooks/connect/legacy-message-formats/ |
| Sandbox | Account sviluppatore gratuito in ambiente demo, senza valore legale | https://ecom.docusign.com/plans-and-pricing/developer |
| Data residency | Regione Europa selezionabile; alcuni dati personali (nomi, email per notifiche/audit) possono essere trattati fuori UE con garanzie (BCR) | https://www.docusign.com/privacy/data-residency |
| Costi pubblici | Developer Starter 50 $/mese (600 $/anno, 40 buste/mese), Intermediate 300 $/mese, Advanced 480 $/mese. **Pagine indicizzate datate: riverificare**; prezzo in EUR per l'Italia non emerso | https://ecom.docusign.com/plans-and-pricing/developer |

### 1.5 Adobe Acrobat Sign

| Voce | Dato | Fonte |
|---|---|---|
| Tipi di firma / QTSP | **Non verificato** in questa sessione (stato QTSP di Adobe non emerso) | — |
| API | REST con shard regionali (`na1`, `eu1`, …); API riservata a piani enterprise e developer | https://developer.adobe.com/acrobat-sign/docs/overview/developer_guide/apiusage ; https://helpx.adobe.com/sign/faq/api.html |
| Webhook | Verifica dell'endpoint: GET con header `X-AdobeSign-ClientId` che va **ricopiato** nella risposta (header o JSON `xAdobeSignClientId`); stesso header nelle notifiche. **Nessun HMAC**: il meccanismo è l'eco del client id. Evento `AGREEMENT_WORKFLOW_COMPLETED` può includere il PDF firmato in base64 (`includeSignedDocuments`) | https://developer.adobe.com/acrobat-sign/docs/overview/acrobat_sign_events/ ; https://helpx.adobe.com/sign/developer/webhook/create.html ; https://developer.adobe.com/acrobat-sign/docs/overview/acrobat_sign_events/webhookpayloadoverview |
| Retry | Back-off esponenziale da ~1 minuto fino a 12 h, ~15 tentativi in 72 h; poi webhook disattivato e notifiche perse | https://developer.adobe.com/acrobat-sign/docs/overview/developer_guide/webhookapis |
| Sandbox | Developer Edition gratuita (documenti marcati di test); vera sandbox a pagamento, solo per account enterprise | https://www.adobe.com/acrobat/business/developer-form.html ; https://helpx.adobe.com/sign/web/developers/sandbox/overview.html |
| Data residency / costi | Shard `eu1` esistente; dettagli residency e **prezzi API: non pubblico / da chiedere** | https://developer.adobe.com/acrobat-sign/docs/overview/developer_guide/apiusage |

### 1.6 Intesi Group (Time4Mind, IG SIGN, PkBox)

| Voce | Dato | Fonte |
|---|---|---|
| Tipi di firma | Firma semplice con tratto grafico/email e con OTP SMS; FEA remota con OTP SMS/app (certificato avanzato); FEQ | https://www.intesigroup.com/it/wp-content/uploads/sites/3/2024/08/IGSIGN_Server_Integration5.2.pdf ; https://www.intesigroup.com/it/wp-content/uploads/sites/3/2023/10/IGSIGN_DocumentoFunzionale.pdf |
| QTSP | **Iscritta nell'elenco AgID** (dal 2018) | https://www.agid.gov.it/it/piattaforme/firma-elettronica-qualificata/prestatori-di-servizi-fiduciari-attivi-in-italia ; https://www.intesigroup.com/en/faq/ |
| API | IG SIGN: REST JSON, client registrato da un amministratore, token JWT, documentazione completa in ambiente demo. PkBox Remote: componente server che invia a Time4Mind solo l'hash | https://www.intesigroup.com/it/wp-content/uploads/sites/3/2024/08/IGSIGN_Server_Integration5.2.pdf ; https://www.intesigroup.com/it/software-firma-digitale/pkbox-remote-as-a-service/chiavi-crittografiche-e-integrazione-della-firma-digitale/ |
| Eventi | IG SIGN ha un *Event Engine* con URL di callback per evento; **autenticità e retry: non verificati** | https://www.intesigroup.com/it/wp-content/uploads/sites/3/2023/10/IGSIGN_DocumentoFunzionale.pdf |
| Sandbox | Certificato cloud di prova gratuito 90 giorni; sandbox API: **da chiedere** | https://store.intesigroup.com/index.php?path=20&route=product%2Fcategory |
| Costi | Certificato cloud avanzato 40 € + IVA (3 anni), qualificato 55 € + IVA (3 anni); **prezzo API non pubblico / da chiedere** | https://store.intesigroup.com/index.php?path=20&route=product%2Fcategory |

### 1.7 Aruba (Aruba PEC)

| Voce | Dato | Fonte |
|---|---|---|
| QTSP | **Iscritta nell'elenco AgID** (dal 2007) | https://www.agid.gov.it/it/piattaforme/firma-elettronica-qualificata/prestatori-di-servizi-fiduciari-attivi-in-italia |
| API di firma | ARSS (Aruba Remote Signing Service): componente server di firma digitale remota con chiave in HSM; "firma automatica" via API per alti volumi. È **firma digitale/FEQ dell'agenzia** (firma massiva), non un flusso documentato "invia al visitatore e raccogli la sua firma" | https://enterprise.aruba.it/documents/presentazioni/it/techbrief_soluzioni-firma_it.aspx ; https://www.pec.it/firma-digitale/soluzioni-business-per-imprese.aspx |
| Webhook, sandbox, DPA, prezzi API | **Non pubblico / da chiedere al fornitore** (documentazione tecnica non pubblica) | — |

### 1.8 Dropbox Sign (ex HelloSign)

| Voce | Dato | Fonte |
|---|---|---|
| Tipi di firma | Firma elettronica standard; AES/QES tramite add-on eID (Premium API), con QTSP terzi (InfoCert; in passato Namirial + IDnow). Dropbox Sign **non** risulta QTSP in proprio | https://help.dropbox.com/plans/dropbox-sign-and-eid ; https://sign.dropbox.com/blog/qualified-electronic-signatures-from-dropbox-sign |
| Eventi / autenticità | Callback con `event_hash` = **HMAC-SHA256** di `event_time + event_type` con la **chiave API primaria** (non del corpo intero) | https://developers.hellosign.com/docs/guides/events-and-callbacks/walkthrough ; https://help.dropbox.com/integrations/php-callback-handler |
| Retry | Risposta di successo richiesta (gli esempi restituiscono "Hello API Event Received"); altrimenti fino a 6 ulteriori tentativi (da commento negli esempi ufficiali) | https://help.dropbox.com/integrations/php-callback-handler |
| Sandbox | `test_mode` gratuito, firme senza valore legale | https://sign.dropbox.com/products/dropbox-sign-api/pricing |
| Data residency | Opzioni di residency globali; residenza solo UE citata per Premium in una pagina datata: **da verificare** | https://sign.dropbox.com/features ; https://sign.dropbox.com/blog/qualified-electronic-signatures-from-dropbox-sign |
| Costi pubblici | API Essentials 75 $/mese (900 $/anno), Standard 250 $/mese (3.000 $/anno); Premium **non pubblico / da chiedere** | https://sign.dropbox.com/products/dropbox-sign-api/pricing |

---

## 2. Tabella comparativa

| Fornitore | FES | FEA | FEQ | QTSP (elenco) | API REST documentata pubblicamente | Autenticità webhook | Retry documentato | Sandbox gratuita | Hosting UE | Prezzo API pubblico |
|---|---|---|---|---|---|---|---|---|---|---|
| **Yousign** | Sì | Sì | Sì | Sì (FR/ANSSI, dichiarato) | Sì (v3) | **HMAC-SHA256 corpo grezzo, `X-Yousign-Signature-256`** | Header `X-Yousign-Retry`; politica non verificata | Sì, 40 gg | Francia | **Sì** (da 104 €/mese) |
| **Namirial eSAW** | Sì | Sì | Sì | **Sì (AgID)** | Sì (v6, Swagger) | Solo Basic auth + riverifica API | Sì, 30 tentativi | Sì (demo) | Da chiedere | Piani app pubblici; API da chiedere |
| InfoCert GoSign | n.v. | Sì | Sì | **Sì (AgID)** | Sì (OAuth2) | Non verificato | Non verificato | Su richiesta | Da chiedere | No |
| DocuSign | Sì | Sì | Sì | Sì (FR, dichiarato) | Sì | HMAC-SHA256, `X-DocuSign-Signature-1` | Sì, 15 giorni | Sì (demo) | Regione EU (con eccezioni) | Sì in USD (da riverificare) |
| Adobe Acrobat Sign | Sì | n.v. | n.v. | n.v. | Sì | Eco `X-AdobeSign-ClientId` (no HMAC) | Sì, 72 h | Developer Edition sì; sandbox a pagamento | Shard `eu1` | No |
| Intesi Group | Sì | Sì | Sì | **Sì (AgID)** | Manuale PDF (REST/JWT) | Non verificato | Non verificato | Certificato prova 90 gg | Da chiedere | Solo certificati |
| Aruba | n.v. | n.v. | Sì (firma digitale remota) | **Sì (AgID)** | Non pubblica | Non pubblico | Non pubblico | Non pubblico | Da chiedere | No |
| Dropbox Sign | Sì | Add-on eID | Add-on eID (QTSP terzi) | No (si appoggia a terzi) | Sì | HMAC-SHA256 di `event_time+event_type` | Fino a 6 retry | Sì (`test_mode`) | Da verificare | Sì in USD |

(n.v. = non verificato in questa sessione)

---

## 3. Quadro normativo sintetico (con fonti)

### 3.1 Norme

- **eIDAS, Reg. (UE) 910/2014**, testo consolidato: https://eur-lex.europa.eu/legal-content/IT/TXT/HTML/?uri=CELEX:02014R0910-20240520
  - art. 25 par. 1: a una firma elettronica non possono essere negati effetti giuridici e ammissibilità come prova
    per il solo motivo della forma elettronica o perché non qualificata;
  - art. 25 par. 2: la firma qualificata ha effetti giuridici equivalenti a quelli della firma autografa;
  - considerando 49: gli effetti giuridici delle firme (salvo l'equivalenza della qualificata) sono rimessi al diritto nazionale.
  - Definizioni di firma avanzata (connessa unicamente al firmatario, idonea a identificarlo, creata con mezzi sotto il suo
    controllo esclusivo, collegata ai dati in modo da rilevare modifiche) riepilogate da AgID: https://www.agid.gov.it/en/node/1674
- **Reg. (UE) 2024/1183** (eIDAS 2, portafoglio europeo di identità digitale): https://eur-lex.europa.eu/legal-content/IT/TXT/?uri=OJ:L_202401183 — rilevante in prospettiva per l'identificazione del firmatario; non cambia le scelte del test.
- **CAD, D.Lgs. 82/2005, art. 20 c. 1-bis**: il documento informatico soddisfa la forma scritta e ha l'efficacia dell'art. 2702 c.c.
  se sottoscritto con firma digitale, altra firma qualificata **o avanzata**, o formato con processo AgID di identificazione
  informatica dell'autore; **negli altri casi** l'idoneità a soddisfare la forma scritta e il valore probatorio sono
  **liberamente valutabili in giudizio** (sicurezza, integrità, immodificabilità).
  Fonti: https://www.gazzettaufficiale.it/atto/serie_generale/caricaArticolo?art.versione=1&art.idGruppo=0&art.flagTipoArticolo=0&art.codiceRedazionale=18G00003&art.idArticolo=20&art.idSottoArticolo=1&art.idSottoArticolo1=10&art.dataPubblicazioneGazzetta=2018-01-12&art.progressivo=0 ;
  https://www.agid.gov.it/sites/default/files/repository_files/leggi_decreti_direttive/dl-7-marzo-2005-82_0.pdf
- **CAD art. 21 c. 2-bis** (come riassunto da AgID): gli atti dell'art. 1350 nn. 1–12 c.c. su documento informatico richiedono,
  a pena di nullità, firma qualificata o digitale; per il n. 13 basta anche la FEA.
  Fonte: https://www.agid.gov.it/sites/default/files/repository_files/linee_guida_per_la_sottoscrizione_elettronica_di_documenti_ai_sensi_dellart.20_del_cad.pdf
- **DPCM 22/02/2013**, artt. 56–57 (regole tecniche FEA): requisiti tecnici (identificazione, connessione univoca, controllo
  esclusivo, verifica di non modifica) e **obblighi di chi eroga la FEA** ai propri clienti: identificazione con documento,
  dichiarazione di accettazione, conservazione per almeno 20 anni, pubblicazione delle caratteristiche del sistema,
  **assicurazione RC con massimale non inferiore a 500.000 €**.
  Fonti: https://www.gazzettaufficiale.it/atto/serie_generale/caricaArticolo?art.versione=1&art.idGruppo=5&art.flagTipoArticolo=0&art.codiceRedazionale=13A04284&art.idArticolo=57&art.idSottoArticolo=1&art.idSottoArticolo1=10&art.dataPubblicazioneGazzetta=2013-05-21&art.progressivo=0 ;
  https://www.agid.gov.it//sites/default/files/repository_files/leggi_decreti_direttive/dpcm_22_febbraio_2013_-_nuove_regole_tecniche.pdf
- **Elenco AgID** dei prestatori attivi in Italia (Aruba PEC, InfoCert, Intesi Group, Namirial presenti) e trusted list
  italiana: https://www.agid.gov.it/it/piattaforme/firma-elettronica-qualificata/prestatori-di-servizi-fiduciari-attivi-in-italia ;
  https://eidas.agid.gov.it/TL/TSL-IT.xml

### 3.2 Applicazione ai due documenti

| Documento | FES (es. click + OTP email) | FEA (es. OTP SMS su certificato avanzato, grafometrica conforme) | FEQ |
|---|---|---|---|
| **Foglio visita** | Ammessa come prova, ma valore **liberamente valutato dal giudice** (CAD art. 20 c.1-bis, seconda parte; eIDAS art. 25.1) | Forma scritta ed efficacia ex art. 2702 c.c. (CAD art. 20 c.1-bis) | Equivalente all'autografa (eIDAS art. 25.2) |
| **Incarico di mediazione** | Come sopra | Come sopra | Come sopra |

Punti che **richiedono parere legale** (le fonti consultate non li risolvono):
1. Se per il foglio visita (prova della visita ai fini della provvigione) una FES con OTP sia sufficiente in pratica o se
   serva FEA. Le norme dicono solo che la FES è valutata liberamente.
2. Se l'incarico di mediazione richieda la forma scritta, e quali obblighi derivino dalla disciplina del consumatore
   (contratti conclusi a distanza o fuori dai locali, informative, recesso) quando l'incarico è firmato a distanza.
   Non è stata verificata in questa sessione alcuna fonte specifica sulla mediazione: **non si trae alcuna conclusione**.
3. **Chi è "soggetto che eroga la FEA"** ai sensi del DPCM 22/02/2013 art. 57 quando l'agenzia la offre ai propri clienti
   tramite una piattaforma terza: se gli obblighi (identificazione, dichiarazione, conservazione ventennale, assicurazione
   500.000 €) ricadano sull'agenzia/STIMA360 o siano coperti dal QTSP. Decisivo per scegliere FEA vs FES/FEQ.
4. Valore in Italia delle prove e dell'audit trail di un QTSP stabilito in un altro Stato membro (es. Yousign/ANSSI):
   la FEQ è riconosciuta in tutta l'UE per eIDAS; per FES/FEA vale la libera valutazione o i requisiti nazionali.

---

## 4. Raccomandazione per il TEST con sandbox

### 4.1 Principale: **Yousign**
Motivi (tutti con fonte in §1.1):
- è l'unico, tra quelli con sandbox gratuita e self-service, che documenta pubblicamente **sia** un'API REST moderna
  **sia** una **firma HMAC-SHA256 dei webhook** sul corpo grezzo (`X-Yousign-Signature-256`) con header di retry;
- offre FES, FEA e FEQ sulla stessa API, quindi il livello di firma è una scelta di configurazione e non di fornitore;
- sandbox gratuita 40 giorni, hosting in Francia, DPA ex art. 28 pubblicato, prezzi API pubblici in euro;
- audit trail in italiano (`audit_trail_locale`).
Limiti da tenere presenti: QTSP vigilato da ANSSI (non nell'elenco AgID); politica di retry, campo ID evento ed endpoint di
download/annullamento **da rileggere sulla documentazione** prima di implementare; i webhook non arrivano a IP privati, quindi
in test serve un endpoint HTTPS pubblico (staging).

### 4.2 Alternativa: **Namirial eSignAnyWhere**
- QTSP italiano nell'elenco AgID, FEA/FEQ, grafometrica e OTP SMS, account demo gratuito, prezzi di piano pubblici e bassi.
- Svantaggio tecnico: callback **senza firma crittografica** (solo Basic auth), quindi ogni callback va trattato come
  "segnale" e lo stato va **sempre** riletto con `GET envelope/{id}` autenticato; fino a 30 ritentativi → idempotenza obbligatoria.
- Da preferire se il parere legale indica di usare un QTSP italiano o la grafometrica/FEA "all'italiana".

### 4.3 Cosa deve decidere / fornire il titolare
1. **Account sandbox**: chi lo apre (titolare, non lo sviluppatore) e con quale email aziendale; nessun account è stato creato.
2. **Tipo di firma** per foglio visita e, separatamente, per incarico (FES+OTP / FEA / FEQ), **dopo il parere legale** (§3.2).
3. **Contratto e DPA**: accettazione del DPA del fornitore, verifica dei subresponsabili extra-UE, aggiornamento
   dell'informativa privacy ai visitatori e del registro dei trattamenti.
4. **Budget**: piano (es. Yousign API Plus vs Pro), costo AES/QES via API (da chiedere), costo SMS OTP (non pubblico),
   volume annuo stimato di fogli visita e incarichi.
5. **Ambiente**: URL pubblico HTTPS di staging per i webhook; gestione dei segreti (chiave API e `secret_key` webhook)
   fuori dal repository.
6. **Conservazione**: dove e per quanto conservare PDF firmato e audit trail (oggi lo schema conserva un solo documento).
7. **Stato sandbox vs produzione**: confermare che una firma ottenuta in sandbox non dovrà mai risultare "valida" (vedi §5.4).

---

## 5. Mappatura verso l'interfaccia neutra esistente (Yousign)

File letti, **non modificati**: `esign/providers.py`, `esign/service.py`, `migrations/122_signature_requests.sql`.

### 5.1 Metodi del `SignatureProvider`

| Metodo esistente | Yousign (da confermare sulla OpenAPI) | Note |
|---|---|---|
| `create_request(document, document_sha256, signer_name, signer_contact, expires_at, idempotency_key) -> ProviderRequest(reference)` | Sequenza: crea Signature Request (`name`, `delivery_mode`, `timezone`, `audit_trail_locale="it"`, scadenza da `expires_at`) → carica il PDF (binario) → aggiungi firmatario (nome, email; telefono se OTP SMS) → attiva. `reference` = id della Signature Request. `idempotency_key` salvabile nei **metadati** della richiesta per la riconciliazione | Endpoint esatti non verificati. Un solo campo `signer_name`/`signer_contact` contro i dati richiesti dal fornitore (nome/cognome separati? email **e** telefono per OTP SMS?) → da verificare |
| `cancel_request(reference)` | Annullamento della Signature Request → evento `signature_request.canceled` | Endpoint non verificato. Nota: in `service.cancel` oggi il provider viene chiamato **solo** se simulato; per un fornitore reale andrà chiamato (fuori dal perimetro di questo documento) |
| `fetch_document(reference, document_sha256, signer_name) -> ProviderDocument` | Download del/i documento/i firmato/i dopo `signature_request.done` | Endpoint non verificato. L'audit trail è un file distinto: l'interfaccia non ha un metodo per scaricarlo |

### 5.2 Eventi webhook → stati esistenti

| Stato STIMA360 | Evento Yousign | Stato confermato? |
|---|---|---|
| `sent` | creazione + attivazione (risposta sincrona); evento `signature_request.activated` **non verificato** | — |
| `signed` | `signature_request.done` (tutti i firmatari richiesti hanno firmato) → poi `fetch_document` | Sì |
| `declined` | `signature_request.declined` | Sì |
| `cancelled` | `signature_request.canceled` (annullamento manuale) | Sì |
| `expired` | evento di scadenza **non verificato**; oggi la scadenza è applicata localmente alla lettura (`_expire_if_due`) | — |
| `simulated_completed` | nessuno (solo adattatore simulato) | — |
| (nessuno stato) | `signer.done`, `signature_request.reminder_executed`, `signer.notification_delivery_failed`, `signature_request.approved` → da registrare solo come evento informativo, **senza** cambiare stato | Sì |

Regola: `signer.done` **non** va mappato a `signed` (stato del firmatario, non della richiesta); con un solo firmatario
precede `signature_request.done`, che resta l'unico evento di chiusura.

### 5.3 Verifica di autenticità da implementare (in un passo successivo, non ora)
1. leggere il corpo **grezzo** (byte) prima di qualunque parsing JSON;
2. calcolare `"sha256=" + hex(HMAC_SHA256(secret_key_subscription, raw_body))` e confrontarlo a tempo costante con
   l'header `X-Yousign-Signature-256` (https://developers.yousign.com/docs/security);
3. rifiutare eventi con `sandbox` incoerente con l'ambiente configurato;
4. deduplicare per ID evento (campo da confermare in `webhook-events.json`, https://developers.yousign.com/openapi) e
   trattare `X-Yousign-Retry` > 0 come possibile duplicato;
5. per sicurezza, rileggere lo stato della richiesta via API prima di passare a `signed`.

### 5.4 Cosa manca nello schema/servizio (solo constatazioni, nessuna modifica)
1. **Sandbox vs produzione**: `_view` calcola `valid_signature = status == 'signed' and provider != 'simulated'`. Una firma
   ottenuta nella **sandbox** Yousign (senza valore legale) risulterebbe "valida". Manca una colonna/vincolo che distingua
   l'ambiente del fornitore (o un valore `provider` dedicato e vietato in `signed`): stato tecnico e stato legale vanno tenuti distinti.
2. **Ricezione webhook idempotente**: nessuna tabella degli eventi in ingresso con **ID evento del fornitore univoco**;
   `signature_request_events` registra solo le transizioni e non ha `provider_event_id`.
3. **Ricerca per riferimento**: nessun indice/vincolo `UNIQUE (provider, provider_reference)` per trovare la richiesta dal webhook.
4. **Audit trail / certificato di completamento**: una sola coppia `received_document` + `received_document_sha256`; manca
   lo spazio per l'audit trail (byte + SHA-256) e il vincolo `signature_requests_received_chk` lo legherebbe comunque allo stato.
5. **Livello di firma e metodo di autenticazione** (FES/FEA/FEQ, OTP email/SMS, identificazione): nessuna colonna; senza di
   essa non si può dimostrare a posteriori con quale livello è stato firmato un foglio visita o un incarico.
6. **Recapiti del firmatario**: un solo `signer_contact` (200 caratteri); l'OTP SMS richiede telefono oltre all'email.
7. **Stato del fornitore grezzo** e istante dell'evento del fornitore (`X-Yousign-Issued-At`): non memorizzati; utili per audit e ordinamento degli eventi.
8. **Errore/creazione non riuscita**: nessuno stato per una richiesta rifiutata dal fornitore. Inoltre in `service.create`
   la chiamata esterna `provider.create_request` avviene **dentro** la transazione DB prima dell'`INSERT`: se l'`INSERT`
   fallisce resta una richiesta orfana presso il fornitore, e un nuovo tentativo con la stessa chiave ne creerebbe un'altra
   (salvo idempotenza lato fornitore, **non verificata** per Yousign). Da progettare prima dell'integrazione reale.
9. **Interfaccia**: mancano metodi per verificare/interpretare un webhook, rileggere lo stato (`get_status`) e scaricare l'audit trail.
10. **Scadenza**: la scadenza locale va trasmessa al fornitore e allineata, altrimenti STIMA360 può marcare `expired` una
    richiesta che il firmatario riesce ancora a firmare presso il fornitore (poi `signature_request.done` troverebbe la riga chiusa).

### 5.5 Stato dopo il BLOCCO-5 (migrazione 125, commit `aee4842`)

Le constatazioni del § 5.4 indipendenti dal fornitore sono state risolte nel codice,
senza scegliere un fornitore e senza scrivere chiamate a API reali:

| Constatazione | Stato |
|---|---|
| Una firma ottenuta in sandbox risulterebbe valida | Risolto: `provider_environment` (`simulation`/`sandbox`/`production`); `valid_signature` solo in `production` |
| Nessuna deduplica degli eventi webhook | Risolto: `signature_provider_events`, unico per (fornitore, id evento), append-only |
| Nessun UNIQUE su (provider, provider_reference) | Risolto: indice unico parziale |
| Audit trail, livello di firma, data non conservati | Risolto: `audit_trail` (+ SHA-256), `signature_level` dichiarato, `signed_at`; il database rifiuta `signed` senza queste prove |
| Chiamata al fornitore dentro la transazione prima dell'INSERT | Risolto: chiamata fuori transazione, `create_request` idempotente sulla chiave per contratto |
| `cancel` chiama il fornitore solo se simulato | Risolto: `cancel` chiama l'adattatore della richiesta, poi chiude |
| Verifica di autenticità | Pronto lo strumento neutro (`verify_hmac_sha256`, corpo grezzo, confronto a tempo costante) e la rotta `POST /api/public/esign/webhooks/{provider}`; header e formato esatti saranno quelli documentati dal fornitore scelto |

Restano aperti: l'adattatore del fornitore (da scrivere sulla sua specifica OpenAPI
ufficiale, dopo la scelta e l'account sandbox), la politica di retry e il nome del campo
"id evento" del fornitore, la scadenza allineata con quella del fornitore, il parere legale
del § 3.2, la conservazione a norma dei documenti firmati.
