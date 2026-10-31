# BLOCCO-4 — Numero operativo: collegare il numero WhatsApp esistente al nuovo CRM

**Tipo:** piano tecnico (DESIGN ONLY). Nessun codice modificato, nessun commit,
nessuna chiamata a Meta, Render o altri servizi esterni. Nessuna configurazione
Meta cambiata.

**Fonti:** `origin/main` (produzione: sito stima360 + servizio stime), letto solo
con `git show` / `git grep`; `core-0.1-test` (HEAD, nuovo CRM in TEST). Le due
storie git non hanno base comune.

**Regola di riservatezza del documento:** si riportano solo NOMI di variabili
d'ambiente. Nessun token, segreto, numero di telefono, `phone_number_id`, WABA id,
id di servizio o stringa di connessione. Nota: alcuni di questi valori compaiono
in chiaro in file già presenti nel repository (vedi §6.3); qui non vengono
ripetuti.

---

## 0. Sintesi

1. In `main` l'invio automatico WhatsApp della stima **non** chiama Meta: fa una
   `POST` a un **servizio esterno** (`WHATSAPP_SERVICE_URL`, predefinito un servizio
   Render dal nome `stima360-whatsapp-webhook-test`, percorso `/send`) con
   `to, p1, p2, p3`. Il nome del template, la lingua e le credenziali usate da
   quel servizio **non sono nel codice** di questo repository.
2. In `main` esiste un webhook `/webhook/whatsapp` (firma `X-Hub-Signature-256`
   verificata). **Difetto attuale:** la sua verifica GET legge parametri
   `hub_mode`/`hub_verify_token` (trattino basso) mentre Meta invia
   `hub.mode`/`hub.verify_token` (punto): con il codice di `main` **di oggi** una
   nuova verifica Meta di questo URL non riuscirebbe. Questo **non** dimostra a quale
   URL punti il callback né che non sia mai stato verificato: la verifica può essere
   avvenuta con codice o componenti precedenti, o su un altro servizio. Dove punta il
   callback e dove arrivano davvero le consegne restano **da verificare** (console
   Meta, log dei servizi). `roadmap/BLOCCO-4D_ATTIVAZIONE.md` afferma che punta a
   `main`: affermazione non verificata da questo documento.
3. Il CRM (`core-0.1-test`) ha una ricezione affidabile (4A: tutti gli eventi,
   una transazione, idempotenza su `provider_message_id`, 200 solo dopo commit),
   instradamento per `phone_number_id` (`WHATSAPP_INBOUND_ROUTES`), risposte
   manuali (4B, spente salvo `WHATSAPP_OUTBOUND_MODE=meta`) e risposte automatiche
   solo simulate (4D). Il percorso del webhook è **lo stesso** di `main`
   (`/webhook/whatsapp`).
4. **Raccomandazione:** il numero si collega al **futuro CRM di produzione**, non al
   TEST. Opzione **A** — il ricevitore attuale resta l'unico callback Meta e inoltra
   al CRM di produzione il corpo originale con la firma originale (fan-out
   "trasparente"), con fasi ombra → parallelo → invio dal CRM. Template della stima,
   PDF e collegamento Stima Pro restano sul percorso attuale finché non c'è una
   decisione separata. **Sul TEST solo dati sintetici**: nessun inoltro di clienti
   reali verso il TEST come scelta predefinita; una prova reale richiede destinatari
   e perimetro concordati per iscritto (§4.2-bis). Prima di tutto (Fase 0) va
   accertato **chi** è oggi il ricevitore. Nessun cambio di callback in questo
   blocco; un eventuale cambio futuro richiede prima che il ripristino sia
   verificabile (§4.6).

---

## 1. Come funziona OGGI (`origin/main`)

Riferimenti nella forma `origin/main:<file>:<riga>`.

### 1a. Invio automatico del "template" WhatsApp della stima

| Aspetto | Dove | Cosa fa |
|---|---|---|
| Configurazione | `main.py:36` | `WHATSAPP_SERVICE_URL = os.getenv("WHATSAPP_SERVICE_URL", <URL Render predefinito>/send)` |
| Funzione d'invio | `main.py:136-158` (`invia_whatsapp`) | normalizza il numero (`main.py:107-115`, prefisso `39`), poi `requests.post(WHATSAPP_SERVICE_URL, json={"to","p1","p2","p3"}, timeout=10)`; errori solo stampati, nessun esito salvato |
| Chiamante | `main.py:896-905`, dentro `POST /api/salva_stima` (`main.py:390-916`) | `p1 = nome`, `p2 = indirizzo` (`format_indirizzo`, `main.py:644`), `p3 = link_token` |
| `p3` | `main.py:571-587` | `link_token = https://www.stima360.it/stima_dettagliata.html?token=<uuid>`; token salvato in `stime.token`, scadenza 7 giorni (`main.py:572-579`) |
| Momento | `main.py:896` | dopo INSERT stima, bridge CRM, PDF ed email; un errore WhatsApp non blocca la risposta |

- **Servizio template esterno:** sì, è il destinatario di `WHATSAPP_SERVICE_URL`.
  Il suo codice **non è in questo repository** (`roadmap/CATALOGO-CANONICO-1_REPORT.md:474`
  lo indica come repository separato `stima360-whatsapp-webhook-test`).
  **Nome del template, lingua, mappatura `p1..p3` sulle variabili del template,
  `phone_number_id` e token usati da quel servizio: non trovati nel codice.**
  Serve leggere quel repository e le sue variabili d'ambiente su Render (solo nomi).
- **Codice legacy non attivo:** `whatsapp.py` è una vecchia copia di un `main.py`
  (crea un proprio `FastAPI()`, `whatsapp.py:69`) che importa
  `send_template_stima` da se stesso (`whatsapp.py:29`) e lo chiama a
  `whatsapp.py:527-531` con `(telefono, indirizzo, pdf_link)`; la funzione
  **non è definita da nessuna parte** in `origin/main` e nessun file importa
  `whatsapp.py`. Non è un percorso attivo, ma documenta che in passato il template
  portava il link del PDF. Variabili lette lì: `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`,
  `WHATSAPP_API_VERSION` (`whatsapp.py:36-38`).
- **Email collegate** (stesso endpoint): email al cliente (`main.py:728-839`, link PDF
  tramite `loader_url`, pulsante `wa.me` verso il numero aziendale scritto in
  chiaro nel codice, `main.py:731`), email interna (`main.py:845-891`, destinatario
  `ADMIN_EMAIL`). SMTP: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`
  (`database.py:257-261`).

### 1b. PDF della stima e "Stima Pro"

- **Generazione:** `pdf_report.genera_pdf_stima` (`pdf_report.py:279`), chiamata da
  `main.py:651-696` con nome `stima_<id>.pdf`. Scrive in `/var/tmp/reports`
  (`pdf_report.py:285-287`) e poi **carica obbligatoriamente su GitHub** con la
  Contents API (`pdf_report.py:221-273`, `611-620`): se il caricamento fallisce,
  errore 500 (`main.py:695-696`). Ritorna l'URL pubblico "raw" del file
  (`GITHUB_PDF_BASE_URL`, `pdf_report.py:42-45`, `272-273`).
  Variabili: `GITHUB_USER`, `GITHUB_REPO`, `GITHUB_TOKEN`, `GITHUB_BRANCH`,
  `GITHUB_PDF_BASE_URL`. (`github_upload.py` è un'altra variante via Release, non
  chiamata da `main.py`.) Il PDF è quindi **pubblico a chi conosce l'URL**.
- **Consegna:** URL nel JSON di risposta (`pdf_url`, `main.py:908-916`) e nell'email
  cliente tramite `https://www.stima360.it/pdf_redirect.html?pdf=…&token=…`
  (`main.py:704-708`). **Il PDF non viene inviato su WhatsApp**: su WhatsApp va solo
  `link_token` (§1a). `app.mount("/reports", …)` (`main.py:102`) serve anche i file
  locali, ma l'URL restituito è quello GitHub.
- **"Stima Pro" = stima dettagliata:** il cliente apre `stima_dettagliata.html?token=…`
  (pagina del sito, **non in questo repository**), che legge i dati con
  `GET /api/prefill?t=` (`main.py:921-989`, token non scaduto) e salva con
  `POST /api/salva_stima_dettagliata` (`main.py:994-1114`) nella tabella
  `stime_dettagliate`. Elenco amministrativo: `GET /api/admin/stime_pro`
  (`main.py:1119-1147`). Nessun PDF "Pro" generato da questo endpoint: **non
  trovato nel codice**.

### 1c. Richiesta di sopralluogo dal sito

- **Endpoint:** lo stesso `POST /api/salva_stima_dettagliata` (`main.py:994`), campo
  `sopralluogo` (stringa ISO o vuoto, `main.py:1068`), colonna
  `stime_dettagliate.sopralluogo TIMESTAMP` (`database.py:209`).
- **Dove finisce:** solo la riga `stime_dettagliate`; visibile in
  `GET /api/admin/stime_pro`.
- **Notifiche:** **nessuna** (né email né WhatsApp) nel codice dell'endpoint
  (`main.py:994-1114`). Eventuali avvisi lato sito o lato servizio esterno: non
  trovati nel codice.

### 1d. Ricezione risposte / webhook

| Aspetto | Dove | Cosa fa |
|---|---|---|
| Verifica GET | `main.py:1221-1232` | parametri Python `hub_mode`, `hub_challenge`, `hub_verify_token` confrontati con `WHATSAPP_VERIFY_TOKEN`. **FastAPI legge le query `hub_mode` ecc., non `hub.mode`**: con il codice attuale una nuova verifica di Meta riceverebbe 403 (difetto presente anche nelle versioni `fa4547b`, `ff34dd7` di questo repository). Non dice dove punti oggi il callback né come sia stato verificato: da accertare in console e nei log |
| POST | `main.py:1234-1278` | `verify_whatsapp_signature` (`main.py:117-134`): HMAC-SHA256 del corpo grezzo con `WHATSAPP_APP_SECRET`, confronto a tempo costante con `X-Hub-Signature-256`; senza segreto 503, firma assente/errata 403 (introdotto con `884c442`, 2026-08-29) |
| Elaborazione | `main.py:1243-1273` | legge **solo** `entry[0].changes[0].messages[0]`; ignora gli stati di consegna e gli altri messaggi dello stesso payload; tipi non testuali salvati come `[TIPO]`; INSERT in `whatsapp_incoming (from_number, message_type, text, received_at, direction='in')` |
| Errori | `main.py:1275-1278` | ogni eccezione è stampata e la risposta è comunque **200**: Meta non ritenta → **messaggio perso** in caso di errore DB |
| Duplicati | — | nessun id del provider salvato, nessun vincolo: un reinvio Meta crea un doppione |
| Tabella | — | DDL di `whatsapp_incoming` **non trovato nel codice** (nessuna migrazione la crea) |
| Inbox admin | `main.py:213-261` | `GET /api/admin/whatsapp/messages`, abbina per telefono a `stime` |
| Risposta manuale | `main.py:266-297` + `invia_whatsapp_text` `main.py:1283-1306` | `POST /api/admin/whatsapp/reply`: testo libero a Graph `v18.0/{WHATSAPP_PHONE_ID}/messages` con `WHATSAPP_TOKEN`; esito solo stampato; salva comunque una riga `direction='out'` anche se l'invio fallisce |

### 1e. `phone_number_id` / WABA in `main`

- Invio manuale: `WHATSAPP_PHONE_ID` + `WHATSAPP_TOKEN` (`main.py:1284-1285`), versione
  Graph fissa nel codice `v18.0` (`main.py:1290`).
- Invio template: dipende dal servizio esterno — **non trovato nel codice**.
- Ricezione: `main` non legge `metadata.phone_number_id` e non distingue numeri.
- `register_whatsapp.py` (script manuale, non importato): registrazione del numero
  su Graph `v19.0/{id}/register` con `whatsapp_certificate.txt`; l'id è **scritto
  in chiaro nel file** (`register_whatsapp.py:3`), non letto da variabile.
- **WABA id:** nessuna variabile e nessun uso nel codice → **non trovato nel codice**.
- Nomi variabili WhatsApp in `main` (`git grep`): `WHATSAPP_SERVICE_URL`,
  `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_PHONE_ID`,
  `WHATSAPP_TOKEN`, `WHATSAPP_API_VERSION` (solo nel `whatsapp.py` inattivo).
  Altre: `PUBLIC_BASE_URL` (`main.py:35`), `ADMIN_EMAIL`, `ADMIN_USER`,
  `ADMIN_PASS`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`.

---

## 2. Le parti equivalenti nel CRM (`core-0.1-test`)

Riferimenti nella forma `<file>:<riga>` sul working tree.

### 2.1 Ricezione (BLOCCO-4A)

- **Percorso:** `GET/POST /webhook/whatsapp`, identico a `main` (`main.py:2237`,
  `main.py:2259`). La differenza non è nel percorso ma nel comportamento.
- **GET corretto:** legge `hub.mode`, `hub.verify_token`, `hub.challenge` (accetta
  anche le forme con trattino basso), rifiuta senza `WHATSAPP_VERIFY_TOKEN`,
  confronto `hmac.compare_digest`, challenge solo numerica (`main.py:2244-2257`).
- **POST:** stessa verifica di firma di `main` (`main.py:468-485`, `2270-2274`),
  sul corpo originale e prima di ogni accesso al DB; poi
  `inbound_parser.parse` (`inbound_messages/parser.py:121-177`: tutti gli `entry`,
  `changes`, messaggi e stati; allegati solo metadati, nessun download) e
  `inbound_service.ingest` in una transazione (`inbound_messages/service.py:76-115`).
  Payload malformato 400; migrazione 118 assente 503; errore di scrittura 500 →
  Meta ritenta (`main.py:2275-2285`). 200 solo dopo il commit.
- **Idempotenza:** `UNIQUE (provider_message_id)` e `UNIQUE (dedupe_key)`
  (`migrations/118_whatsapp_inbound.sql:44`, `:145`) con `ON CONFLICT DO NOTHING`
  (`inbound_messages/repository.py:31`, `:42`). Un messaggio ricevuto due volte
  (da Meta o da un inoltro) non duplica.
- **Instradamento:** `WHATSAPP_INBOUND_ROUTES` = `phone_number_id:agency_id,…`
  (`inbound_messages/enums.py:46`, `inbound_messages/routing.py:15-39`). Numero non
  elencato, elencato per due agenzie, o agenzia inesistente → salvato `unrouted`,
  invisibile a tutti; nessuna agenzia di ripiego.
- **Effetti:** contatto collegato solo se il telefono corrisponde a UN contatto
  dell'agenzia; uno "STOP" revoca il consenso marketing WhatsApp di quel contatto
  nell'agenzia (`inbound_messages/service.py:105-108`, `118-140`). Nessuna
  risposta inviata. Consultazione: `/api/inbound-messages/*`
  (`inbound_messages/router.py:25-62`).
- **Tabella storica:** il webhook 4A **non scrive** più in `whatsapp_incoming`;
  l'inbox legacy `GET /api/admin/whatsapp/messages` (`main.py:568-660`) nel CRM resta
  ma non riceve nuove righe in ingresso.

### 2.2 Invio manuale (BLOCCO-4B) — `message_replies/transport.py`

- Spento per impostazione predefinita: `current()` restituisce un trasporto solo se
  `WHATSAPP_OUTBOUND_MODE=meta`, `WHATSAPP_TOKEN` presente e `WHATSAPP_GRAPH_VERSION`
  esplicita e ben formata (`message_replies/transport.py:42-53`, `115-120`;
  `message_replies/enums.py:6-12`).
- Invio solo testo libero: `POST https://graph.facebook.com/{versione}/{phone_number_id}/messages`
  (`message_replies/transport.py:66-82`). **Il `phone_number_id` mittente non viene da
  una variabile**: è quello che ha ricevuto l'ultimo messaggio del cliente, e deve
  essere instradato all'agenzia dell'operatore (`message_replies/service.py:119-145`,
  `243-261`); ricontrollato subito prima dell'invio (`message_replies/service.py:188-204`).
- Finestra 24 ore con margine (`message_replies/enums.py:18-21`); **template non
  supportati** (`message_replies/enums.py:40-41`).
- Esiti `accepted` / `rejected` / `indeterminate`; un esito incerto non è mai
  ritentato (`message_replies/transport.py:1-14`, `92-112`).

### 2.3 Risposte automatiche (BLOCCO-4D) — `auto_replies/`

- Arresto generale: gira solo con `WHATSAPP_AUTO_REPLY_MODE=simulate`
  (`auto_replies/enums.py:7-8`, `auto_replies/service.py:119-120`); non esiste un
  valore d'invio. Le decisioni sono registrate come "Simulazione — non inviato".
- Il giro si avvia solo con `POST /api/auto-replies/tick` (`auto_replies/router.py:56`);
  nessun cron collegato (`roadmap/BLOCCO-4D_ATTIVAZIONE.md:46-48`).
- **Attenzione — lavoro non committato nel working tree** (rilevato durante la
  stesura, non scritto da questo incarico, non analizzato a fondo): modifiche a
  `auto_replies/enums.py`, `auto_replies/repository.py`, `auto_replies/service.py`,
  `communication/enums.py`, `visit_booking/*` e nuove
  `migrations/123_whatsapp_auto_reply_send*.sql` introducono un modo
  `WHATSAPP_AUTO_REPLY_MODE=send` (invio reale delle risposte automatiche). I
  riferimenti di questo documento valgono per il commit `2d797f3`. Se quel lavoro
  viene integrato, nelle Fasi 1-2 di §4 la variabile deve restare **assente** (o
  al massimo `simulate`) su ogni servizio che riceve messaggi reali, altrimenti
  si aggiunge un terzo canale d'invio e il rischio di doppie risposte.

### 2.4 Stima, PDF, Stima Pro, sopralluogo nel CRM

- **Stima pubblica:** `POST /api/salva_stima` esiste (`main.py:887`) con ricevuta
  idempotente (`public_submissions`). **Invia ancora il WhatsApp tramite il servizio
  esterno** (`main.py:1724-1731`, `invia_whatsapp` `main.py:487-499`), con lo stesso
  URL predefinito di `main` (`main.py:108`) e gli stessi `p1..p3`
  (`link_token` costruito con `PUBLIC_SITE_BASE_URL`, `main.py:1342`).
- **PDF:** privato. `stima_pdf.prepare/generate` (`main.py:1479-1489`), consegna con
  `GET /api/stime/{id}/pdf?t=<token>` e `POST …/pdf/retry` (`main.py:1764-1773`),
  versioni operatore `/api/admin/stime/{id}/pdf` (`main.py:1776-1785`). Il
  renderer ritorna i byte (`pdf_report.py:530`), **nessun caricamento su GitHub**
  in questo ramo.
- **Stima Pro:** `/api/prefill` (`main.py:1791`), `/api/salva_stima_dettagliata`
  (`main.py:1872`), `/api/admin/stime_pro` (`main.py:2063`).
- **Sopralluogo:** dopo il commit della dettagliata, `safe_import_for_detail`
  porta la richiesta in Agenda (`main.py:2043-2049`,
  `appointments_legacy/site_hook.py:1-20`, `67-125`), più la sincronizzazione
  manuale `POST /api/appointments/legacy-requests/sync`
  (`appointments_legacy/router.py:77`). Prenotazione visite acquirenti:
  `public_booking` (`/prenota`, `/api/public/booking/*`) e `visit_booking` —
  sono un'altra cosa (visite agli immobili, non sopralluogo del venditore).
  Notifiche WhatsApp di sopralluogo o prenotazione: **non trovate nel codice**.
- **Invio template dal CRM:** **non esiste**. Il canale `whatsapp` è dichiarato in
  `communication/enums.py:22-23` ma senza adattatore (`communication/dispatcher.py:77`).
- `property_valuations/` (BLOCCO-2A) sono le valutazioni dell'agente, con PDF
  proprio: non riguardano il flusso del sito.

### 2.5 Differenze che contano per il collegamento

| Tema | `main` | CRM |
|---|---|---|
| Percorso webhook | `/webhook/whatsapp` | `/webhook/whatsapp` (uguale) |
| Verifica GET | non compatibile con i parametri di Meta | compatibile |
| Firma POST | `WHATSAPP_APP_SECRET`, corpo grezzo | identica |
| Copertura payload | primo messaggio, stati ignorati | tutto |
| Errore DB | 200 (perdita) | 500 (Meta ritenta) |
| Duplicati | possibili | impossibili (vincoli UNIQUE) |
| Multi-numero | no | sì, `WHATSAPP_INBOUND_ROUTES` |
| Invio manuale | sempre attivo se `WHATSAPP_PHONE_ID`+`WHATSAPP_TOKEN` | 4B spento salvo `WHATSAPP_OUTBOUND_MODE=meta` **ma** l'endpoint legacy `POST /api/admin/whatsapp/reply` è ancora presente (`main.py:662-693`) e invia con `WHATSAPP_PHONE_ID`+`WHATSAPP_TOKEN` **senza** quel blocco |
| Template stima | servizio esterno | stesso servizio esterno (URL predefinito non vuoto) |
| Versione Graph | `v18.0` fissa | `WHATSAPP_GRAPH_VERSION` obbligatoria (4B); `v18.0` nel legacy |

Due conseguenze operative immediate per TEST (nessuna modifica fatta qui):

1. Se sul servizio TEST sono valorizzate `WHATSAPP_PHONE_ID` e `WHATSAPP_TOKEN`,
   l'endpoint legacy di risposta può inviare messaggi reali scavalcando il blocco
   4B. Va verificato (solo i nomi delle variabili presenti) e, se serve, tolta
   `WHATSAPP_PHONE_ID` dal servizio TEST.
2. Se `WHATSAPP_SERVICE_URL` non è impostata su TEST, il CRM usa l'URL predefinito
   del servizio esterno: una stima inserita su TEST con un numero reale
   **farebbe partire un template reale**. Va verificato.

---

## 3. Il problema: un solo callback per app Meta

Per WhatsApp Cloud API ogni app Meta ha **un** callback URL per l'oggetto
`whatsapp_business_account`; tutti gli eventi del numero (messaggi in ingresso,
stati `sent/delivered/read/failed`) arrivano lì, firmati con l'App Secret di
quell'app (`X-Hub-Signature-256`). Esistono meccanismi Meta di override del
callback per WABA o per numero: **da verificare sulla documentazione ufficiale**
e comunque sono un cambio di callback, quindi fuori da questo incarico.

### Opzione A — il ricevitore attuale resta e inoltra al CRM (fan-out)

Il ricevitore attuale (da accertare: servizio esterno oppure `main`) verifica la
firma, fa quello che fa oggi e **inoltra al CRM i byte originali del corpo con
l'intestazione `X-Hub-Signature-256` originale** a `https://<CRM>/webhook/whatsapp`.

- **Pro:** nessun cambio in Meta; il CRM non va toccato (verifica la firma con lo
  stesso `WHATSAPP_APP_SECRET` già previsto, è idempotente, risponde 500 se non
  salva); produzione continua identica; rientro = spegnere l'inoltro.
- **Contro:** serve una modifica al ricevitore attuale (codice di produzione o del
  servizio esterno); il CRM deve conoscere l'App Secret (segreto in un secondo
  servizio); se l'inoltro fallisce, Meta non ritenta verso il CRM (il ricevitore
  ha già risposto 200) → serve un ritentativo lato ricevitore.
- **Firma:** funziona solo se il corpo inoltrato è **identico byte per byte**
  (niente `json.loads`/`json.dumps`, niente ricodifica). Alternativa: ri-firmare
  con un segreto interno dedicato (richiede una modifica nel CRM e un nuovo nome di
  variabile — non esistente oggi).
- **Perdita messaggi:** se l'inoltro è sincrono e il ricevitore risponde 500 quando
  il CRM fallisce, Meta ritenta anche verso il ricevitore: con `main` come
  ricevitore questo crea **doppioni in `whatsapp_incoming`** (nessuna
  deduplicazione). Meglio: rispondere a Meta come oggi, inoltrare dopo, con coda
  locale e ritentativi; il CRM deduplica.
- **Latenza:** Meta vuole una risposta rapida; l'inoltro non deve allungare la
  risposta (timeout breve o asincrono).

### Opzione B — il CRM diventa ricevitore e inoltra a produzione

Callback Meta spostato sul CRM; il CRM salva e inoltra al ricevitore attuale.

- **Pro:** la ricezione più affidabile (4A) è la prima a vedere i dati; nessuna
  perdita da errore DB.
- **Contro:** **richiede il cambio del callback** (escluso qui); il CRM è un
  servizio di TEST: disponibilità, piano Render, deploy automatici del branch e
  migrazioni di TEST diventano un punto di guasto della produzione; l'inoltro verso
  `main` creerebbe doppioni (nessuna deduplicazione lì); se il ricevitore attuale
  è il servizio esterno, anche quello dovrebbe accettare inoltri; nuovo codice di
  inoltro nel CRM. Il **ripristino** del callback verso `main` non è verificabile
  con il codice attuale di `main` (§4.6).

### Opzione C — stesso URL, instradamento a monte

Un componente davanti all'URL registrato (proxy/edge sul dominio, o un piccolo
servizio dedicato "smistatore") riceve da Meta e duplica verso produzione e CRM.

- **Pro:** separa la ricezione dai due applicativi; ognuno resta com'è.
- **Contro:** se l'URL registrato oggi è un host `onrender.com`, mettere un
  componente "davanti" significa comunque cambiare il callback (o il DNS di un
  dominio proprio, se il callback ne usa uno — **da verificare**); un terzo
  servizio da gestire; stesso problema di firma e ritentativi di A.

### Rischi comuni

| Rischio | Dove nasce | Mitigazione |
|---|---|---|
| Doppie risposte al cliente | due inbox che rispondono (legacy `main`/servizio esterno e CRM 4B), endpoint legacy nel CRM, auto-reply | un solo canale d'invio per fase; 4B spento finché la produzione risponde; `WHATSAPP_PHONE_ID` rimossa dove non deve inviare; 4D solo `simulate` |
| Doppio template della stima | `salva_stima` sia in `main` sia nel CRM chiamano il servizio esterno | il sito deve inviare a UN backend; su TEST `WHATSAPP_SERVICE_URL` verso un destinatario non reale |
| Perdita messaggi | `main` risponde 200 anche se non salva; inoltro senza ritentativi | coda e ritentativi lato ricevitore; riconciliazione giornaliera |
| Firma non valida sul CRM | corpo ricodificato; App Secret diverso | inoltro dei byte originali; stesso segreto, conservato solo nelle variabili del servizio |
| Dati reali su TEST | un inoltro verso il CRM di TEST | escluso per impostazione: sul TEST solo dati sintetici; una prova reale solo con destinatari e perimetro concordati (§4.2-bis) |
| Attribuzione sbagliata | `WHATSAPP_INBOUND_ROUTES` errata | fase ombra con verifica dei conteggi `routed`/`unrouted` |

### Raccomandazione

**Opzione A, nella forma "inoltro trasparente asincrono" verso il CRM di
produzione**, perché è l'unica che: non cambia il callback Meta; non richiede
modifiche al CRM (il 4A accetta già corpo+firma originali ed è idempotente);
lascia la produzione nel comportamento attuale (template della stima, PDF, Stima
Pro, sopralluogo dal sito); ha un rientro immediato (spegnere l'inoltro). La
destinazione dell'inoltro è il CRM **di produzione**, quando esisterà: il TEST non
riceve messaggi di clienti reali. Il punto in cui inserire
l'inoltro dipende dalla Fase 0: se il ricevitore è il servizio esterno, l'inoltro
va lì (altro repository); se è `main`, va in `main` con una patch minima e un
interruttore. B e C restano opzioni successive, da valutare solo quando il CRM
sarà un servizio di produzione e il rientro del callback sarà verificabile.

**Serve una decisione del titolare o un accesso Meta per:** sapere dove punta oggi
il callback; approvare una modifica al ricevitore di produzione; condividere
l'App Secret con il servizio CRM di produzione; l'eventuale prova reale circoscritta
sul TEST (destinatari e perimetro); qualsiasi cambio futuro di callback (B/C).

---

## 4. Strategia di rilascio verificabile

### 4.1 Principi

- **Niente merge** tra `origin/main` e `core-0.1-test` (storie separate): la sola
  modifica di produzione ammessa è una patch minima dedicata all'inoltro, scritta
  sulla storia di `main` (o del servizio esterno), revisionata a parte.
- **Servizi distinti:** produzione (sito + stime + ricevitore attuale), CRM di
  produzione (futuro, con il suo database) e CRM di TEST (solo dati sintetici).
  Ogni fase si dichiara per servizio. Le fasi 1-3 riguardano il CRM **di
  produzione**.
- **Da preservare in ogni fase:** invio del template della stima (oggi tramite il
  servizio esterno), PDF della stima e collegamento Stima Pro, richiesta di
  sopralluogo dal sito, ricezione delle risposte. Nessuna fase sposta questi
  percorsi: il CRM riceve una copia e, solo in Fase 3, risponde.
- **Codice deployato e stato del database si verificano separatamente**; nessuna
  migrazione applicata implicitamente (118/119 sul CRM sono prerequisito
  dichiarato, `roadmap/BLOCCO-4D_ATTIVAZIONE.md:14-16`).
- **Un solo canale che invia** in ogni fase.

### 4.2 Fase 0 — ricognizione in sola lettura (nessun effetto)

Azioni (accessi in sola lettura):
1. Console Meta: callback URL attuale, campi sottoscritti, app collegata, numero e
   WABA collegati, data dell'ultima verifica. Fotografia della pagina (senza
   segreti) conservata come riferimento di ripristino.
2. Repository e variabili del servizio esterno: chi riceve, chi invia il template,
   nome/lingua del template, mappatura `p1..p3`, eventuali risposte automatiche.
3. Servizio produzione: elenco dei NOMI delle variabili WhatsApp presenti; log del
   percorso `/webhook/whatsapp` (riceve traffico? codici 200/403/503?).
4. Servizio CRM TEST: nomi delle variabili presenti (`WHATSAPP_APP_SECRET`,
   `WHATSAPP_INBOUND_ROUTES`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_OUTBOUND_MODE`,
   `WHATSAPP_GRAPH_VERSION`, `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`,
   `WHATSAPP_SERVICE_URL`, `WHATSAPP_AUTO_REPLY_MODE`); stato migrazioni 118/119.

**Criteri di uscita:** ricevitore attuale identificato con prova (console Meta +
log); conflitto con `BLOCCO-4D_ATTIVAZIONE.md` risolto per iscritto; su TEST
`WHATSAPP_OUTBOUND_MODE` e `WHATSAPP_AUTO_REPLY_MODE` assenti, `WHATSAPP_PHONE_ID`
assente o giustificata, `WHATSAPP_SERVICE_URL` non diretta al servizio reale.
**Rientro:** nessuno necessario.

### 4.2-bis TEST — solo dati sintetici

Sul CRM di TEST il collegamento si prova **senza** il numero reale: webhook firmati
con un segreto di prova, numeri e contatti sintetici, `WHATSAPP_OUTBOUND_MODE` e
`WHATSAPP_AUTO_REPLY_MODE` assenti (è ciò che i test e i collaudi locali già fanno).
**Nessun inoltro di clienti reali verso il TEST** come scelta predefinita.

Una **prova reale circoscritta** è possibile solo con un accordo scritto del titolare
che fissi: destinatari (es. telefoni interni dell'agenzia, elencati), perimetro
(numero, agenzia, durata in giorni), cosa si invia (nessun cliente, nessun template
della stima), conservazione e cancellazione dei dati raccolti. Senza questo accordo
la prova non si fa.

### 4.3 Fase 1 — ombra (copie al CRM di produzione, invisibili)

Prerequisito: il CRM di produzione esiste come servizio proprio, con il suo
database e le migrazioni verificate (`roadmap/RILASCIO_TEST_123_125.md` § 7).
Modifiche: inoltro nel ricevitore attuale dietro un interruttore spento per
default (nome da definire, oggi non esistente), destinazione il webhook del CRM
di produzione;
sul CRM `WHATSAPP_APP_SECRET` uguale a quello dell'app e
`WHATSAPP_INBOUND_ROUTES` **vuota** (tutto salvato `unrouted`, invisibile).

**Controlli:** risposta del ricevitore verso Meta invariata (codici e tempi);
sul CRM 0 risposte 403 (firma) e 0 risposte 400; conteggio giornaliero
`whatsapp_inbound_messages` e `whatsapp_webhook_events` confrontato con il
ricevitore attuale. Limite: `whatsapp_incoming` di `main` non salva l'id Meta,
quindi il confronto è per conteggio e per (mittente, tipo, minuto), salvo che
il ricevitore attuale conservi l'id.
**Criteri di uscita (proposti, da confermare):** 7 giorni consecutivi; differenza
di conteggio messaggi = 0 (o ogni differenza spiegata); 0 firme rifiutate;
p95 del tempo di risposta del ricevitore non peggiorato oltre una soglia decisa.
**Rientro:** interruttore spento → nessuna copia; i dati già ricevuti restano nel
CRM (la down 118 rifiuta con righe presenti, `roadmap/BLOCCO-4A_TEST_RUNBOOK.md`
"Rientro"): conservarli o cancellarli secondo decisione del titolare.

### 4.4 Fase 2 — ricezione in parallelo, visibile nel CRM

Modifiche: `WHATSAPP_INBOUND_ROUTES` con il `phone_number_id` del numero verso
l'agenzia corretta; operatori leggono nel CRM. **Le risposte partono ancora solo
dal canale attuale**; nel CRM 4B spento, endpoint legacy di risposta reso inerte
(nessuna `WHATSAPP_PHONE_ID`), 4D al massimo `simulate`
(`roadmap/BLOCCO-4D_ATTIVAZIONE.md` Fase 2).

**Controlli:** `routed` = 100% dei messaggi del numero; collegamenti contatto
corretti su un campione; nessun messaggio in uscita registrato dal CRM.
**Criteri di uscita:** 14 giorni senza differenze tra le due inbox; decisione
scritta su quale inbox è quella di lavoro.
**Rientro:** togliere `WHATSAPP_INBOUND_ROUTES` (i nuovi messaggi tornano
`unrouted`, invisibili) e riavviare; eventualmente spegnere l'inoltro (Fase 1).

### 4.5 Fase 3 — invio dal CRM

Prerequisito: il CRM su un servizio di produzione (non TEST), con migrazioni
verificate.
Modifiche: sul CRM `WHATSAPP_OUTBOUND_MODE=meta`, `WHATSAPP_GRAPH_VERSION`
scelta sulla documentazione Meta, `WHATSAPP_TOKEN` del numero; sul canale attuale
si spegne la risposta manuale (in `main` basta togliere `WHATSAPP_PHONE_ID`, che è
letta solo da `invia_whatsapp_text`, `main.py:1284` — **non** usata dal template,
che passa dal servizio esterno). Il template della stima resta sul servizio
esterno: il CRM non ha invio template (§2.4).

**Controlli:** ogni risposta del CRM con esito `accepted` e `wamid`; stati di
consegna ricevuti tramite inoltro e collegati; 0 invii dal canale vecchio;
nessuna doppia risposta su un campione di conversazioni.
**Criteri di uscita:** 14 giorni, 0 doppie risposte, 0 esiti `indeterminate` non
gestiti.
**Rientro:** togliere `WHATSAPP_OUTBOUND_MODE` dal CRM e riavviare (invio subito
bloccato, `message_replies/transport.py:115-120`); rimettere `WHATSAPP_PHONE_ID`
su `main` se si torna alla risposta legacy.

### 4.6 Callback e configurazione originale: come ripristinarli

Questo piano non cambia il callback. Per qualsiasi fase futura che lo cambi:

1. **Prima:** fotografare la configurazione Meta (URL, campi sottoscritti, app),
   annotare i NOMI delle variabili collegate (`WHATSAPP_VERIFY_TOKEN`,
   `WHATSAPP_APP_SECRET`) e dove sono custodite.
2. **Rischio di non ritorno:** Meta verifica ogni nuovo URL con una GET. Se il
   callback originale è `main`, una **nuova** verifica con il codice attuale di
   `main` non riuscirebbe (§1d): una volta spostato, tornare a `main` richiederebbe
   prima di correggerne la GET. Quindi: o il ricevitore originale è il servizio
   esterno ed è verificabile, o la correzione della GET di `main` va rilasciata e
   provata **prima** di qualunque spostamento.
3. **Ripristino:** reimpostare in console l'URL fotografato con lo stesso verify
   token, confermare la verifica, ricontrollare i campi sottoscritti, inviare un
   messaggio di prova da un telefono di prova e vederlo nel ricevitore originale.
4. **Messaggi durante il passaggio:** Meta ritenta le consegne non riuscite per un
   periodo limitato (durata da riconfermare sulla documentazione Meta); il 4A del
   CRM deduplica, `main` no.

---

## 5. Accessi e decisioni mancanti

**Accessi (sola lettura salvo diversa indicazione):**
1. Console Meta dell'app WhatsApp: callback URL, campi sottoscritti, numero/WABA
   collegati, override eventuali.
2. Repository e variabili (solo nomi) del servizio esterno
   `stima360-whatsapp-webhook-test`: ricezione, invio template, nome/lingua del
   template, eventuali risposte automatiche.
3. Log del servizio di produzione per `/webhook/whatsapp` e per l'invio al
   servizio esterno.
4. Elenco dei nomi delle variabili sui servizi produzione e CRM TEST.
5. Stato delle migrazioni 118/119 sul database del CRM (e su quale database).
6. Codice del sito (`stima_dettagliata.html`, `pdf_redirect.html`,
   `dati_personali.html`): verso quale backend invia, oggi non in nessun repository
   accessibile.

**Decisioni del titolare:**
1. Quale componente modificare per l'inoltro (servizio esterno o `main`) e
   approvazione della patch di produzione.
2. Condivisione dell'App Secret con il servizio CRM, oppure ri-firma con un
   segreto interno (richiede modifica al CRM).
3. Eventuale prova reale circoscritta sul TEST (§4.2-bis: destinatari, perimetro,
   durata, cancellazione); per la produzione, conservazione dei dati raccolti in
   ombra.
4. Agenzia di destinazione del numero (`WHATSAPP_INBOUND_ROUTES`).
5. Quale inbox è quella di lavoro in Fase 2 e quando si spegne la risposta legacy.
6. Quando e dove il CRM diventa servizio di produzione (Fase 3).
7. Soglie dei criteri di uscita (giorni, latenza, tolleranze).
8. Eventuale spostamento futuro del callback (B/C) e correzione preliminare della
   GET di `main`.
9. Template: restano sul servizio esterno o verranno portati nel CRM (oggi non
   implementato).

---

## 6. Note

### 6.1 Conflitto tra documenti
`roadmap/BLOCCO-4D_ATTIVAZIONE.md:7-10` dichiara il callback su `main`;
`roadmap/BLOCCO-4A_TEST_RUNBOOK.md:31` lo dichiara non verificabile e indica il
servizio esterno come possibile destinatario; il codice attuale di `main` (§1d)
non supererebbe una nuova verifica, ma questo non dice dove punti oggi il callback.
Solo la console Meta e i log delle consegne reali possono decidere.

### 6.2 Difetti di `main` rilevanti (non corretti qui)
GET di verifica incompatibile con i parametri di Meta nel codice attuale (una nuova
verifica non riuscirebbe); solo il primo messaggio del payload; 200 anche su
errore; nessuna deduplicazione; risposta manuale salvata come inviata anche se
fallita; PDF pubblici su GitHub.

### 6.3 Dati sensibili già presenti nel repository
Su entrambi i rami sono in chiaro: il numero aziendale nelle email di
`main.py`, un identificativo di numero in `register_whatsapp.py:3`, il file
`whatsapp_certificate.txt`; nel CRM anche `roadmap/BLOCCO-4D_ATTIVAZIONE.md:7-8`
(numero, Phone ID, WABA). Da valutare se rimuoverli o spostarli in variabili
(fuori da questo incarico).
