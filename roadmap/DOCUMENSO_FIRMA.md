# Firma elettronica del foglio visita con Documenso

Branch `core-0.1-test`. Stato: **integrazione tecnica pronta e provata contro un Documenso FINTO; nessuna firma reale
è ancora stata eseguita** (nessuna credenziale, nessun account). Gli invii restano disabilitati.

## Perimetro e garanzie

| Garanzia | Come è ottenuta |
|---|---|
| **Nessun invio** | La busta si crea e si «distribuisce» con `distributionMethod = NONE` e **tutte** le email di Documenso spente (`emailSettings`). Non esiste un'impostazione che le accenda. I collegamenti di firma li mostra il CRM all'operatore (link e QR). Mai `redistribute`. |
| **Livello di firma = semplice** | `signature_level` è sempre `ses`. Il collegamento personale non identifica il firmatario con un documento: nulla nel codice o nell'interfaccia lo chiama avanzata o qualificata (c'è un test). |
| **Sandbox mai «valida»** | `DOCUMENSO_ENVIRONMENT` vale `sandbox` per difetto. Solo con `production` la richiesta può risultare `valid_signature` (condizione **tecnica**, non giuridica; resta «semplice»). |
| **Nessun servizio nuovo** | Nessun servizio Render creato; l'URL è configurabile per Cloud Free ora e Community self-hosted poi. |
| **Credenziali** | Solo variabili d'ambiente. `repr`, errori e log non contengono token né segreti. |
| **Percorso visite collaudato** | `visit_sheets`, PDF, versioni e archivio non cambiano: i riquadri di firma si leggono dal PDF già archiviato. |

## Come funziona

1. L'operatore, dalla scheda della visita (Agenda o scheda immobile), preme **Richiedi firma elettronica** e indica nome ed
   **email del cliente** (serve solo a identificarlo presso Documenso, che non gli scrive). L'agente è quello assegnato alla visita
   (dati dal server, mai dal browser). Devono avere indirizzi diversi.
2. Il CRM legge dal PDF archiviato dove sono «Firma del visitatore» e «Firma dell'agente», carica **quel PDF** in una busta
   (`externalId` = chiave di idempotenza) con **due firmatari distinti**, ciascuno col suo campo firma, e la distribuisce senza invii.
3. **Collegamenti di firma**: «Collegamenti di firma» mostra, per chi deve ancora firmare, «Apri per firmare», «Copia collegamento»
   e il **QR** del cliente. Il cliente apre il collegamento sul telefono e firma. I collegamenti sono segreti: si caricano solo su
   richiesta, mai in cache, solo a chi vede il foglio, e si azzerano alla chiusura.
4. **Esito**: arriva dal webhook (`X-Documenso-Secret`) **oppure** dalla riconciliazione/«Aggiorna stato». In entrambi i casi lo stato
   si **rilegge con `GET /envelope/{id}`**: un evento non decide mai da solo.
5. **Archivio**: alla firma completa si scaricano il PDF firmato (dopo aver verificato che l'originale presso Documenso abbia lo
   stesso SHA-256 del foglio caricato) e il registro eventi (audit log, JSON); si conservano con la loro impronta in
   `signature_requests` (`received_document`, `audit_trail`), con `signature_level`, `signed_at` e l'ambiente. Il foglio resta immutabile.
6. **Annullamento**: `POST /envelope/delete` presso Documenso e poi in locale; se Documenso non conferma, la richiesta resta aperta.
7. **Scadenza**: Documenso (API v2 dell'SDK 0.6.0) non espone una scadenza della busta: la scadenza è locale. Per Documenso la chiude
   **solo la riconciliazione**, che **annulla la busta presso Documenso** prima di segnare `expired` (segnarla scaduta senza revocarla
   lascerebbe un collegamento ancora valido). Dopo la scadenza il CRM non consegna più i collegamenti e non permette un'altra richiesta
   finché la prima non è chiusa o annullata.

## Dove sta cosa

| Pezzo | File |
|---|---|
| Adattatore Documenso (API v2, buste) | `esign/documenso.py` |
| Contratto comune (firmatari, errori) | `esign/providers.py` |
| Servizio: creazione, link, QR, webhook, riconciliazione, «aggiorna stato» | `esign/service.py`, `esign/router.py` |
| Riquadri firma dal PDF archiviato | `esign/layout.py`, `esign/pdf_text.py` |
| Firmatari (cliente e agente), migrazione additiva | `migrations/128_signature_request_signers.sql` (+ down), `scripts/release_128_migration.py` |
| Interfaccia | `static/os_shell/assets/components/visit-sheets.js` |
| Test | `tests/test_documenso_adapter.py`, `test_documenso_postgres.py`, `test_documenso_browser_postgres.py`, `test_documenso_ui_static.py`, `test_release_128_*.py`; Documenso finto in `tests/documenso_fake.py` |

## Configurazione (variabili d'ambiente del servizio TEST)

| Variabile | Valore | Note |
|---|---|---|
| `ESIGN_PROVIDER` | `documenso` | Assente o `simulated` = adattatore simulato (default). |
| `DOCUMENSO_API_URL` | `https://app.documenso.com/api/v2` | Cloud (default). Self-hosted: `https://<host>/api/v2`. Solo https (http solo su localhost). |
| `DOCUMENSO_API_TOKEN` | token API | **Segreto.** Intestazione `Authorization: <token>` (senza prefisso), come l'SDK ufficiale. |
| `DOCUMENSO_WEBHOOK_SECRET` | ≥ 16 caratteri | **Segreto**, la stessa stringa configurata nel webhook di Documenso. Assente = ogni webhook rifiutato. |
| `DOCUMENSO_ENVIRONMENT` | `sandbox` (default) \| `production` | Cambia solo come il CRM conta la firma, non le chiamate. |
| `DOCUMENSO_TIMEOUT_SECONDS` | `20` | |
| `DOCUMENSO_VERIFY_ORIGINAL` | `1` | Confronto dell'originale con l'impronta del foglio caricato. Spegnerlo toglie una verifica di provenienza. |
| `COMMUNICATION_CRON_TOKEN`, `COMMUNICATION_CRON_AGENCY_IDS` | già esistenti | Servono alla rotta di riconciliazione. |

Webhook da configurare in Documenso: `POST https://<host-test>/api/public/esign/webhooks/documenso`, con segreto, per gli eventi
di invio, firma, completamento, rifiuto e annullamento del documento. Senza `ESIGN_PROVIDER=documenso` la rotta risponde 404 neutro.

**Riconciliazione periodica** (per i webhook assenti o persi): `POST /api/internal/esign/reconcile`
con intestazione `X-Stima360-Cron-Token` e corpo `{"agency_id": <id>, "limit": 50}`: stesso segreto e stesse agenzie del cron delle
comunicazioni (nessun servizio nuovo: si aggiunge una chiamata al cron esistente). Restituisce solo conteggi.

## Piano gratuito: cosa è noto e cosa no

Fonti consultate il 03/11/2026 con ricerca web (le pagine ufficiali non sono raggiungibili da questa sessione): vanno **riconfermate**
nel proprio account prima di decidere.

| Voce | Esito |
|---|---|
| Cloud Free: documenti | 5 documenti al mese, fino a 10 destinatari per documento, senza carta ([prezzi](https://documenso.com/pricing), [limiti](https://docs.documenso.com/docs/developers/api/rate-limits)). Ogni busta di prova ne consuma uno. |
| Cloud Free: API | **Non confermato.** Una fonte la dà compresa (sui 5 documenti), un'altra la lega ai piani a pagamento. Si verifica creando un token in *Impostazioni → Token API*. |
| Cloud Free: **webhook** | **Non confermato.** Le fonti discordano. Si verifica cercando *Impostazioni → Webhook*. **Se non c'è o richiede un piano superiore, il collaudo si fa con «Aggiorna stato» e con la riconciliazione: il codice non dipende dai webhook.** |
| Piani a pagamento (indicativi, da riverificare) | Individual 25 $/mese, Teams 40 $/mese (documenti illimitati, API), Platform 250 $/mese. **Nessun acquisto è stato fatto né proposto.** |
| Community self-hosted | Gratuita (AGPL-3.0). **Non confermato** se includa token API e webhook: una fonte dice di no. Da verificare prima di installarla; nessuna installazione senza autorizzazione. |
| Livelli di firma | Nessuna fonte ufficiale trovata che dichiari firma avanzata o qualificata: il CRM la tratta **sempre come semplice**. |

## Prima firma reale di prova: cosa serve

1. **Account Documenso Cloud Free** aperto dal titolare (email aziendale). Nessun acquisto.
2. **Token API** (*Impostazioni → Token API*) → `DOCUMENSO_API_TOKEN` nel servizio TEST di Render.
3. **Webhook** (solo se il piano lo offre): URL sopra, segreto casuale ≥ 16 caratteri → `DOCUMENSO_WEBHOOK_SECRET` (stessa stringa nei due posti).
4. Variabili: `ESIGN_PROVIDER=documenso`, `DOCUMENSO_ENVIRONMENT=sandbox` (default), `DOCUMENSO_API_URL` solo se non è il Cloud.
5. **Migrazione 128** sul TEST, con il metodo di sempre (One-Off Job): `python scripts/release_128_migration.py check|apply|verify --expected-host <HOST_DB_TEST_VERIFICATO>`.
6. Una visita **sintetica** con un foglio generato; come cliente un indirizzo che controlli tu (l'agente è quello assegnato alla visita). Il PDF e i
   dati dei firmatari passano da Documenso: usare solo dati sintetici e, prima di qualunque uso reale, DPA e informativa privacy.
7. Dal CRM: *Richiedi firma elettronica* → *Collegamenti di firma* → aprire il link del cliente **sul telefono** e firmare; firmare come agente;
   *Aggiorna stato* (o attendere il webhook). Verificare: stato «Firmata», «Firmato in prova — non utilizzabile» (sandbox), PDF firmato e rapporto scaricabili.

## Assunzioni da confermare con la prima firma reale

Scritto sul contratto dell'SDK ufficiale `documenso-sdk-python 0.6.0` (specifica OpenAPI v2); i punti seguenti l'SDK **non** definisce e sono isolati nel codice:

1. **Coordinate dei campi**: percentuali della pagina con origine in alto a sinistra (`esign.documenso.field_geometry`). Se i campi finissero fuori posto, si corregge solo quella funzione.
2. **Download del documento**: la specifica dichiara risposta JSON; l'adattatore accetta sia il PDF diretto sia un JSON con un URL https (`downloadUrl`/`url`/`href`).
3. **Webhook**: nomi evento (`DOCUMENT_COMPLETED`…), campo `externalId` nel `payload` (è l'àncora con cui si ritrova la richiesta) e assenza di un id evento: l'id è l'SHA-256 del corpo grezzo (lo stesso corpo riconsegnato è lo stesso evento). Anche senza questi dettagli lo stato si rilegge via API.
4. **`identifier` del file** nei campi: omesso (busta con un solo documento).
5. L'email del firmatario è obbligatoria in Documenso anche con distribuzione `NONE`: per questo il CRM chiede l'email del cliente.

## Limiti noti

- Finché non c'è una firma reale riuscita, **non si può dire che l'integrazione funzioni con Documenso vero**: è provata sul filo contro un finto che parla come l'SDK.
- La scadenza è locale (vedi sopra): fra la scadenza e il giro di riconciliazione la busta è ancora firmabile presso Documenso e, se firmata in quel tempo, la firma viene registrata normalmente. Più frequente è la riconciliazione, più breve è la finestra.
- Il testo del foglio è ancora una **bozza di modello** da far rivedere; un legale deve dire se la firma semplice basti per il foglio visita.
- Una busta resta presso Documenso (conservazione secondo il loro piano); la conservazione a norma e il periodo di conservazione sono decisioni aperte.
