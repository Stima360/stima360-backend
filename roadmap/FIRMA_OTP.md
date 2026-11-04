# Firma elettronica OTP STIMA360 (FES) del foglio visita — sistema proprietario, senza fornitori esterni

Branch `core-0.1-test`. Ambiente: solo TEST. Qualificazione: **firma elettronica semplice (FES)**: nessuna dicitura di
firma «certificata», «avanzata» o «qualificata» compare nel codice, nei PDF o nell'interfaccia.

## 1. Audit (fase zero): cosa esisteva e cosa è stato usato

| Componente | Stato trovato | Decisione |
|---|---|---|
| `visit_sheets` (121) | PDF immutabile e versionato, SHA-256, `data_snapshot`; già collegato a contatto → immobile → visita | **Riusato** come documento congelato: la firma non lo modifica mai |
| `signature_requests` (122, 125) | richiesta con stato, scadenza, chiave di idempotenza, `received_document`, `audit_trail`, `signature_level`, `signed_at`, `provider_environment`; guardie del database | **Riusato**: `provider = 'stima360_otp'`; `received_document` = documento sottoscritto; `audit_trail` = rapporto delle prove (PDF) |
| `signature_request_events` (122) | transizioni append-only | **Riusato** |
| `signature_request_signers` (128, Documenso) | cliente e agente, stato per firmatario; colonna `signing_url` | **Riusato**; `signing_url` resta vuota (il collegamento non si scrive più in chiaro) |
| `signature_provider_events` (125) | eventi webhook dei fornitori | **Non usato** (nessun fornitore); la tabella resta, vuota |
| `communication_*` + `dispatcher` + SMTP (`database.invia_mail`) | registro, claim, trasporto, divieto di contatto | **Riusato** per le due email (servizio), con un giro di invio immediato |
| pagine pubbliche a token (`public_booking.page.PublicBookingPage`, `/visita`) | file statici, intestazioni di sicurezza, 404 neutro | **Riusata** la stessa classe per `/firma/<token>` |
| `operator_auth.security` (PBKDF2, 600.000 iterazioni) | hash delle password degli operatori | **Riusato** per derivare i codici OTP (con pepe) e per riverificare la password dell'agente |
| `esign.pdf_text`, `esign.layout` | lettura del PDF e dei riquadri firma | **Riusati** per la dicitura nel documento sottoscritto |
| Documenso (`esign/documenso.py`, webhook, riconciliazione, test, doc) | integrazione mai usata in produzione | **Eliminata** (§2) |

## 2. Documenso: eliminato

- Rimossi: `esign/documenso.py`; la rotta webhook `POST /api/public/esign/webhooks/{provider}`; la rotta interna
  `POST /api/internal/esign/reconcile` e la riconciliazione; `fetch_status`/`verify_event`/`InvalidEventSignature`/
  `verify_hmac_sha256` dal contratto; i `signing-links`, il QR e `refresh` dell'API operatore; i test
  `tests/documenso_fake.py`, `test_documenso_*.py`, `test_blocco_5_esign_provider_postgres.py`; `roadmap/DOCUMENSO_FIRMA.md`.
- Variabili: `DOCUMENSO_API_URL`, `DOCUMENSO_API_TOKEN`, `DOCUMENSO_WEBHOOK_SECRET`, `DOCUMENSO_ENVIRONMENT`,
  `DOCUMENSO_TIMEOUT_SECONDS`, `DOCUMENSO_VERIFY_ORIGINAL` non sono più lette da nessun file: **vanno tolte dal servizio
  TEST su Render** (nessun token è mai stato scritto nel repository). `ESIGN_PROVIDER` resta, con due soli valori:
  `simulated` (default) e `stima360_otp`.
- Database: la migrazione 128 **resta** (è applicata e i firmatari servono); nessuna cancellazione.
- Dipendenze: nessun SDK Documenso era in `requirements.txt`; `requests` resta per altri moduli. Aggiunto `pypdf==6.21.4`
  (puro Python) per copiare le pagine originali nel documento sottoscritto.
- Una sentinella (`tests/test_firma_otp_static.py`) fallisce se «documenso» ricompare in `esign/`, nei componenti o nei requisiti.

## 3. Il procedimento

```
AGENTE (CRM, scheda visita)                              CLIENTE (smartphone, /firma/<token>)
 1. foglio visita generato (PDF congelato, SHA-256)
 2. «Invia per firma OTP»: nome + email del cliente
    → richiesta `sent`, procedimento S360-…, firmatari
      (cliente + agente), collegamento personale
    → email «STIMA360 — Foglio visita da firmare» ──────▶ 3. apre il collegamento: dati, PDF (lettore del telefono),
                                                             dichiarazione (nessuna casella preselezionata)
                                                          4. «Conferma e richiedi codice OTP»
                                                             → email «STIMA360 — Il tuo codice di conferma» (6 cifre)
                                                          5. inserisce il codice: verifica atomica, UNA firma
 6. «Firma come agente»: password + dichiarazione
 7. documento sottoscritto (PDF originale + dicitura + attestazione) e rapporto delle prove archiviati; stato «Completato»
```

Regole: il documento non cambia mai dopo l'avvio (è il PDF archiviato, immutabile per trigger); ogni nuova versione del
foglio richiede una nuova richiesta; una sola richiesta aperta per foglio (lock sul foglio); la firma non è mai attivata
dall'apertura del collegamento; l'agente firma solo se è l'agente assegnato, dalla sua sessione, riconfermando la password.

## 4. Migrazione 129 (`migrations/129_signature_otp.sql`, additiva, con down che rifiuta se ci sono righe)

| Oggetto | Contenuto |
|---|---|
| `communication_messages_reason_code_chk` | aggiunti `signature_link` e `signature_otp` |
| `signature_challenges` | `access` (SHA-256 del token) e `otp` (`sale$pbkdf2_sha256$…` con pepe); scadenza, consumo, invalidazione con motivo, tentativi/massimo, messaggio email, impronta IP e user-agent; guardia: nessuna cancellazione, una sfida chiusa non si riapre, i tentativi non calano |
| `signature_evidence` | registro probatorio append-only: `seq`, evento, data, attore, firmatario, dettagli minimi, `prev_hash`, `entry_hash`, `seal` |

Rilascio sul TEST: `python scripts/release_129_migration.py check|apply|verify --expected-host <HOST_DB_TEST_VERIFICATO>`
(stesso metodo dei pacchetti 127 e 128; prova del `verify` su un database costruito dal runner in `tests/test_release_129_postgres.py`).

## 5. Endpoint

Operatore (`/api/signature-requests`, sessione, scope dell'Agenda sul foglio):
`POST` crea (`include_agent`), `GET ?visit_sheet_id=`, `GET /config/provider`, `POST /{id}/cancel` (revoca),
`POST /{id}/simulate` (solo simulazione), `POST /{id}/links` (`send:true` reinvia; `send:false` restituisce il collegamento una volta),
`POST /{id}/sign-as-agent`, `GET /{id}/document`, `GET /{id}/audit-trail` (rapporto), `GET /{id}/events`, `GET /{id}/verify`.
Rimossi: `/refresh`, `/signing-links`, `/signing-links/{role}/qr.svg`, webhook e `internal/esign/reconcile`.

Pubblico (`/api/public/esign/sign/{token}`, nessuna sessione, 404 senza `ESIGN_PROVIDER=stima360_otp`):
`GET` stato, `GET /pdf`, `POST /otp`, `POST /confirm`, `POST /decline`, `GET /signed.pdf`. Pagina: `/firma/<token>`.

## 6. Sicurezza

1. **HTTPS**: il collegamento è costruito su `PUBLIC_BASE_URL` (https); le pagine a token hanno `no-store`, `no-referrer`, `noindex`, CSP.
2. **Token**: `secrets.token_urlsafe(32)`; nel database solo lo SHA-256; un collegamento nuovo chiude i precedenti.
3. **OTP**: 6 cifre da `secrets.randbelow`; derivazione PBKDF2-HMAC-SHA256 (600.000 iterazioni) con sale per sfida e **pepe del
   servizio** (`ESIGN_OTP_PEPPER`, fuori dal database); confronto a tempo costante; mai in chiaro, mai nei log, mai nelle prove.
4. **Brute force**: 5 tentativi per codice (poi invalidato), 10 minuti di validità, codice nuovo = precedente invalidato, mai riutilizzabile.
5. **Rate limit**: 60 s fra due codici, 5 codici/ora per firmatario, 20/ora per impronta IP, 6 collegamenti/ora per richiesta.
6. **Consenso esplicito**: la conferma richiede `consent_accepted: true` e la versione della dichiarazione; l'agente lo stesso.
7. **Documento immutabile**: `visit_sheets` è immutabile per trigger; la richiesta porta l'impronta; `verify` la ricontrolla.
8. **Log**: codici e token non vengono scritti nei log; gli errori riportano solo codici e conteggi.
9. **PDF**: originale e sottoscritto solo con sessione operatore nello scope, o con il token del firmatario.
10. **Isolamento**: agenzia e ruolo come per i fogli visita; firma dell'agente solo per `operator_user_id` del firmatario; trigger di agenzia su sfide e prove.
11. **Replay/concorrenza**: lock di riga sulla richiesta e sulla sfida; sfida consumata una volta; doppia conferma = 409; lock sul foglio alla creazione.
12. **CSRF**: API pubblica senza cookie, POST JSON; API operatore con la sessione del CRM come le altre.
13. **Abuso email**: `ESIGN_ALLOWED_RECIPIENTS` chiusa per difetto (assente = nessun destinatario; `*` esplicito = tutti); limiti sopra; il
    dispatcher applica il divieto di contatto.
14. **GDPR**: IP solo come HMAC con pepe; user-agent troncato; email mascherata nella pagina; dichiarazione e informativa mostrate.
15. **Integrità**: catena + sigillo HMAC (`ESIGN_EVIDENCE_KEY`) e impronte dei tre PDF, verificate da `GET /verify`.

## 7. Prove e loro protezione

Ogni passo scrive una voce in `signature_evidence` (vedi `esign/report_pdf.EVENT_LABELS`): richiesta creata, collegamento
generato/accodato/revocato, collegamento aperto (= pagina caricata, non prova di lettura), documento visualizzato (= PDF richiesto),
OTP richiesto/accodato/errato/verificato/invalidato, dichiarazione accettata, firma del firmatario, agente autenticato,
rifiuto, revoca, scadenza, completamento, documenti archiviati. L'email è registrata come «accettata dal server di posta», mai
come «consegnata» o «letta».

Catena: `entry_hash = SHA-256(prev_hash ‖ voce canonica)`, `seal = HMAC-SHA256(ESIGN_EVIDENCE_KEY, entry_hash)`. Una voce
alterata, tolta o aggiunta rompe la catena; rifarla richiede la chiave del servizio, che il solo accesso al database non ha.
La tabella è append-only per trigger. Il rapporto delle prove (PDF) riporta la cronologia, le impronte e la testa della
catena e si conserva anche fuori dal CRM. **Non** è una marca temporale qualificata né una conservazione a norma (§12).

## 8. Documenti archiviati

| Documento | Dove | Impronta |
|---|---|---|
| originale congelato | `visit_sheets.pdf_bytes` | `visit_sheets.sha256` = `signature_requests.document_sha256` |
| documento sottoscritto (originale copiato con pypdf + dicitura negli spazi firma + pagina di attestazione) | `signature_requests.received_document` | `received_document_sha256` |
| rapporto delle prove | `signature_requests.audit_trail` | `audit_trail_sha256` |

Collegamento: contatto → immobile → visita (`appointments`) → foglio → richiesta → firmatari/sfide/prove. Nessuna
pubblicazione automatica al proprietario. La durata di conservazione non è fissata dal codice (§12).

## 9. Configurazione (variabili d'ambiente del servizio TEST)

| Variabile | Valore | Note |
|---|---|---|
| `ESIGN_PROVIDER` | `stima360_otp` | assente/`simulated` = simulazione |
| `ESIGN_OTP_PEPPER` | segreto ≥ 32 caratteri | **mai nel repository**; cambiarlo invalida i codici in corso |
| `ESIGN_EVIDENCE_KEY` | segreto ≥ 32 caratteri | sigillo delle prove; cambiarlo rende non verificabili i sigilli precedenti |
| `ESIGN_ALLOWED_RECIPIENTS` | elenco esplicito (sul TEST: solo gli indirizzi autorizzati) | chiusa per difetto; `*` = tutti (solo per uso reale autorizzato) |
| `PUBLIC_BASE_URL` | `https://stima360-backend-test.onrender.com` | base dei collegamenti |
| `ESIGN_OTP_TTL_MINUTES`, `ESIGN_OTP_MAX_ATTEMPTS`, `ESIGN_OTP_RESEND_SECONDS`, `ESIGN_OTP_MAX_PER_HOUR_SIGNER`, `ESIGN_OTP_MAX_PER_HOUR_IP`, `ESIGN_LINK_MAX_PER_HOUR_REQUEST` | 10, 5, 60, 5, 20, 6 | facoltative |
| `ESIGN_DISPATCH_IMMEDIATE` | `1` (default) | `0` = le email le spedisce solo il cron |
| SMTP_* | già presenti | lo stesso trasporto del CRM |

## 10. Stati nel CRM (dal server)

`Pronto per firma`, `Invio in corso`, `Inviato`, `In attesa di OTP`, `In attesa della firma agente`, `Completato`, `Scaduto`,
`Rifiutato`, `Revocato`, `Errore di invio`. Badge del foglio: «Inviato / In attesa…» e «Sottoscritto (firma elettronica semplice)».

## 11. Rollback

1. `ESIGN_PROVIDER=simulated` (o assente): le rotte pubbliche rispondono 404, il riquadro torna alla simulazione; le
   richieste esistenti restano leggibili. Nessun dato perso.
2. Codice: `git revert` dei commit della firma OTP su `core-0.1-test` (la 128 e la 129 restano applicate: additive e
   compatibili con il codice precedente, che le ignora).
3. Migrazione: `129_signature_otp_down.sql` dal runner, **solo** se non ci sono sfide, prove né messaggi `signature_*` (altrimenti rifiuta).

## 12. Limiti residui e verifiche legali/privacy ancora necessarie

- Il controllo di una casella email prova il possesso della casella, non l'identità certa della persona (FES). Prevista come
  evoluzione: OTP su telefono verificato o identificazione documentale.
- Le date sono dell'orologio del server: nessuna marca temporale qualificata. Per opponibilità più forte: marca temporale di un
  QTSP e conservazione a norma fuori dal database (copie del rapporto e dei PDF).
- La protezione delle prove regge finché `ESIGN_EVIDENCE_KEY` resta fuori dal database e dai backup del database; backup separati
  delle chiavi e dei documenti sono da organizzare.
- Testo del foglio visita e dichiarazione: bozze da far rivedere a un legale prima dell'uso commerciale; informativa privacy
  del procedimento e registro dei trattamenti da aggiornare; durata di conservazione da definire per finalità.
- La lettura effettiva del documento non è dimostrabile: si registra l'apertura della pagina e la richiesta del PDF.
- L'anteprima del PDF nella pagina usa il lettore del dispositivo (sul telefono si apre a tutto schermo).
