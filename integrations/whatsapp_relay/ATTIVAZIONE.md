# Inoltro dal ricevitore WhatsApp di produzione al CRM — procedura di attivazione

**Stato:** codice pronto e provato in locale. **Nulla è distribuito.** Nessuna modifica a
`main`, al servizio di produzione, al callback Meta, al servizio template o al numero.
Ogni passo su produzione richiede un'**autorizzazione separata** del titolare.

## 0. Migrazione 126 sul TEST (Render One-Off Job)

Servizio: **TEST** (`stima360-backend-test`, commit LIVE `ff0a32f` o successivo). Database
atteso: `stima360_db_test`. Lo script si ferma prima di connettersi se `DB_NAME` o
`DB_HOST` del servizio non sono quelli attesi. `<HOST_DB_TEST_VERIFICATO>` è l'host letto
dalle variabili del servizio TEST, da non incollare nel repository.

Dalla dashboard (servizio TEST → Jobs → Run Job), un comando per volta, passando al
successivo solo con esito 0:

```bash
python scripts/release_126_migration.py check --expected-host <HOST_DB_TEST_VERIFICATO>
```

```bash
python scripts/release_126_migration.py apply --expected-host <HOST_DB_TEST_VERIFICATO>
```

```bash
python scripts/release_126_migration.py verify --expected-host <HOST_DB_TEST_VERIFICATO>
```

Stessi comandi tramite API Render (`RENDER_API_KEY` e `SERVICE_ID` del servizio TEST in
variabili della propria shell, mai nel repository):

```bash
curl -sS -X POST "https://api.render.com/v1/services/$SERVICE_ID/jobs" -H "Authorization: Bearer $RENDER_API_KEY" -H "Content-Type: application/json" -d '{"startCommand": "python scripts/release_126_migration.py check --expected-host <HOST_DB_TEST_VERIFICATO>"}'
```

```bash
curl -sS "https://api.render.com/v1/services/$SERVICE_ID/jobs/$JOB_ID" -H "Authorization: Bearer $RENDER_API_KEY"
```

Il secondo comando legge lo stato del job (`id` restituito dal primo). L'output dei
controlli (`OK` / `STOP` / `ESITO`) è nei log del job.

| Esito atteso | Dove |
|---|---|
| `check`: «pendenti esattamente: 126_whatsapp_inbound_reads», `ESITO: CONTROLLI SUPERATI` | nessuna modifica |
| `apply`: `applied 126_whatsapp_inbound_reads`, `VERIFICA FINALE SUPERATA` | tabella, trigger, vincoli |
| un secondo `apply` | rifiutato |
| `verify` | `VERIFICA FINALE SUPERATA: 126 applicata e registrata` |

**Dopo la migrazione (nessun riavvio necessario):** in «Messaggi WhatsApp» compaiono i
badge «Non letti» e aprire una conversazione li azzera per chi la apre.
`GET /api/inbound-messages/conversations` risponde con `"unread_available": true`. Prima
della 126 vale `false` e la casella funziona lo stesso.

## 1. Che cosa fa

Il numero WhatsApp Business ha un solo callback Meta, oggi `POST /webhook/whatsapp` del
servizio di produzione (`main`). Il collegamento al CRM **non** sposta il callback e **non**
crea un secondo ricevitore:

```
Meta ──► produzione /webhook/whatsapp
            1. verifica firma Meta          (invariato)
            2. accoda l'inoltro              (nuovo, spento senza variabili)
            3. INSERT whatsapp_incoming       (invariato)
            4. risposta 200 a Meta            (invariato)
            5. dopo la risposta: inoltro firmato ──► CRM /webhook/whatsapp
                                                    (stesso ricevitore 4A del CRM)
```

- **Produzione non cambia comportamento:** stessa firma Meta, stesso salvataggio nella
  tabella storica, stessa risposta. L'inoltro parte dopo la risposta a Meta. Un errore,
  un timeout o un CRM irraggiungibile non toccano né la risposta né il salvataggio.
- **Firma propria:** `WHATSAPP_RELAY_SECRET`, HMAC-SHA256 di `<ora>.<corpo>`, finestra di
  5 minuti. L'App Secret di Meta resta solo in produzione.
- **Filtro dei mittenti doppio:**
  - in produzione `WHATSAPP_RELAY_SENDERS`: con un elenco escono solo messaggi e stati di
    quei numeri;
  - nel CRM `WHATSAPP_INBOUND_ALLOWED_SENDERS`: obbligatorio per un inoltro.

  Gli altri clienti non vengono copiati.
- **Nessun duplicato:** il CRM deduplica sugli identificativi Meta (`wamid`). I tentativi
  dell'inoltro e i reinvii di Meta non creano doppioni.
- **Agenzia:** la decide il CRM dal `phone_number_id` destinatario
  (`WHATSAPP_INBOUND_ROUTES`), mai l'inoltro.
- **Niente invii:** né l'inoltro né la ricezione del CRM inviano messaggi.
  - La risposta manuale (4B) resta spenta finché manca `WHATSAPP_OUTBOUND_MODE=meta`.
  - Le risposte automatiche (4D) restano spente finché manca `WHATSAPP_AUTO_REPLY_MODE=send`.

### Tentativi dell'inoltro

| Condizione | Comportamento |
|---|---|
| Massimo tentativi | 3 |
| Timeout | 3 s di connessione e 5 s di lettura |
| Attese tra i tentativi | 1 s e 3 s |
| Rifiuto definitivo (4xx diverso da 429) | nessun nuovo tentativo |

I log riportano solo esito, tentativo e codice HTTP, senza numeri, nomi, testi o segreti.

## 2. File

| File | Uso |
|---|---|
| `integrations/whatsapp_relay/forwarder.py` | da copiare in produzione come `whatsapp_relay.py` (radice del repository di `main`). Solo libreria standard + `requests` |
| `integrations/whatsapp_relay/main_webhook.patch` | modifica di `main.py` di produzione: import, parametro `BackgroundTasks`, una chiamata dopo la verifica della firma. Generata su `origin/main:main.py` con SHA-256 `b195b8f29f841b63ee63e86c62275b5b72953ef47a3e084da664211ca34d4b64` |
| `inbound_messages/relay.py` (CRM) | verifica dell'inoltro e filtro dei mittenti nel webhook esistente |
| `tests/test_whatsapp_relay_forwarder.py` | prove dell'inoltro con un ricevitore che riproduce quello di produzione sotto uvicorn reale |
| `tests/test_whatsapp_relay_postgres.py` | prove del CRM su PostgreSQL, compreso l'inoltro end-to-end |

## 3. Variabili

### CRM (servizio TEST, `stima360-backend-test`)

| Variabile | Valore | Nota |
|---|---|---|
| `WHATSAPP_RELAY_SECRET` | segreto nuovo, almeno 32 caratteri | uguale a quello di produzione; mai nel repository |
| `WHATSAPP_INBOUND_ALLOWED_SENDERS` | il numero del mittente di prova autorizzato (formato `39…`) | obbligatoria per l'inoltro; `*` solo per il futuro CRM di produzione |
| `WHATSAPP_INBOUND_ROUTES` | `<phone_number_id>:<agency_id>` | il `phone_number_id` è quello del numero esistente (§ 6); l'agenzia è da decidere |
| `WHATSAPP_OUTBOUND_MODE` | **assente** | invio manuale spento |
| `WHATSAPP_AUTO_REPLY_MODE` | **assente** | risposte automatiche spente |
| `COMMUNICATION_AUTO_REPLY_TICK` | **assente** | |

### Produzione (servizio `stima360-backend`, solo dopo l'autorizzazione)

| Variabile | Valore |
|---|---|
| `WHATSAPP_RELAY_URL` | `https://stima360-backend-test.onrender.com/webhook/whatsapp` |
| `WHATSAPP_RELAY_SECRET` | lo stesso segreto del CRM |
| `WHATSAPP_RELAY_SENDERS` | il numero del mittente di prova autorizzato (mai `*` verso il TEST) |

Senza una qualunque delle tre variabili, o con un URL non HTTPS, l'inoltro è spento.

### Tre identità da non confondere

| Che cosa | Dove si usa | Regola |
|---|---|---|
| Numero business **+39 329 567 4589** | destinatario dei clienti; in Meta è `display_phone_number` | **mai** in `WHATSAPP_INBOUND_ALLOWED_SENDERS` né in `WHATSAPP_RELAY_SENDERS`; resta su Meta e sul servizio template, invariato |
| Phone ID del numero business | chiave di `WHATSAPP_INBOUND_ROUTES` | non è il numero di telefono; va confermato sulla console Meta (§ 6) |
| Mittente di prova autorizzato | unico valore di `WHATSAPP_INBOUND_ALLOWED_SENDERS` e `WHATSAPP_RELAY_SENDERS` | un telefono **diverso** dal numero business, che scrive **al** numero business |

### Controlli della configurazione (sola lettura, nessun valore stampato)

CRM TEST, come One-Off Job sul servizio TEST:

```bash
python scripts/check_whatsapp_relay_config.py --expected-phone-number-id <PHONE_ID_CONFERMATO_IN_META>
```

Controlla:
- il segreto;
- **un solo** mittente, diverso dal numero business;
- una sola rotta, la cui chiave è il Phone ID e non un numero di telefono;
- invio manuale, risposte automatiche, loro giro e template della stima spenti.

Esce con 0 solo se è tutto superato.

Produzione, dopo la copia del modulo:

```bash
python whatsapp_relay.py --check
```

Risponde `inoltro SPENTO` senza variabili. Con le variabili rifiuta `*` verso il TEST, il
numero business tra i mittenti e più di un mittente.

Un segreto si genera così, e si incolla nelle due configurazioni senza salvarlo altrove:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 4. Attivazione (ordine obbligatorio)

Ogni casella si spunta solo con l'esito indicato. Se un passo fallisce ci si ferma e si
torna al passo precedente (§ 5).

### 4.1 Prima di tutto

- [ ] **Autorizzazioni scritte del titolare:**
  - [ ] modifica del backend di produzione (solo codice, inoltro spento);
  - [ ] accensione dell'inoltro;
  - [ ] mittente di prova: telefono, persona, durata;
  - [ ] cancellazione dei dati di prova dal TEST al termine (vedi
    `roadmap/BLOCCO-4_NUMERO_OPERATIVO_PIANO.md` § 4.2-bis).
- [ ] **Phone ID del numero business** confermato sulla console Meta.
- [ ] **Agenzia di destinazione** decisa.

### 4.2 CRM TEST

- [ ] Migrazione 126: `check`, `apply`, `verify` (§ 0) con `VERIFICA FINALE SUPERATA`.
- [ ] Variabili impostate (§ 3): `WHATSAPP_RELAY_SECRET`, `WHATSAPP_INBOUND_ALLOWED_SENDERS`
      con il solo mittente di prova, `WHATSAPP_INBOUND_ROUTES` con il Phone ID.
- [ ] `check_whatsapp_relay_config.py --expected-phone-number-id …` con esito 0.
- [ ] Inbox aperta da un operatore: pagina «Messaggi WhatsApp» senza errori; risposta
      manuale che mostra «L'invio WhatsApp è disattivato su questo ambiente».

### 4.3 Backend di produzione, codice con inoltro SPENTO

- [ ] Nel repository di `main`:
  - [ ] copiare `forwarder.py` come `whatsapp_relay.py`;
  - [ ] eseguire `git apply --check main_webhook.patch`, poi `git apply main_webhook.patch`;
    se `--check` fallisce, `main.py` è cambiato e la patch va rigenerata.
- [ ] Pubblicare **senza** alcuna variabile `WHATSAPP_RELAY_*`.
- [ ] `python whatsapp_relay.py --check` (One-Off Job su produzione) risponde
      `OK inoltro SPENTO`.
- [ ] Il comportamento resta quello di prima:
  - [ ] un messaggio ricevuto compare in `admin_whatsapp.html`;
  - [ ] una stima dal sito invia il template come prima;
  - [ ] il webhook Meta e la sua verifica non sono stati toccati.
- [ ] Nei log non compare alcuna riga `whatsapp_relay`: con l'inoltro spento non ne
      produce.

### 4.4 Accensione per il solo mittente di prova

- [ ] In produzione impostare:
  - `WHATSAPP_RELAY_URL=https://stima360-backend-test.onrender.com/webhook/whatsapp`;
  - `WHATSAPP_RELAY_SECRET`, uguale al CRM;
  - `WHATSAPP_RELAY_SENDERS`, il solo mittente di prova.
- [ ] `python whatsapp_relay.py --check` risponde `OK inoltro ACCESO verso
      stima360-backend-test.onrender.com/webhook/whatsapp per 1 mittente/i`.

### 4.5 Primo messaggio

- [ ] Il mittente di prova scrive al numero business +39 329 567 4589.
- [ ] In produzione: la riga in `whatsapp_incoming` e il log `whatsapp_relay delivered`.
- [ ] Nel CRM TEST, in «Messaggi WhatsApp»:
  - la conversazione del mittente di prova con il messaggio;
  - il contatto collegato, se il numero corrisponde a un solo contatto dell'agenzia;
  - il badge «Non letti».
- [ ] **Controllo negativo:** un messaggio di qualunque altro numero compare solo in
      produzione e mai nel CRM TEST.
- [ ] **Nessuna risposta parte:** né dal CRM né in automatico.

## 5. Ritorno indietro

| Livello | Azione | Effetto |
|---|---|---|
| Immediato | togliere `WHATSAPP_RELAY_URL` in produzione | inoltro spento al riavvio; ricezione invariata |
| Codice | ridistribuire il commit precedente di `main` | patch rimossa |
| CRM | togliere `WHATSAPP_RELAY_SECRET` | ogni inoltro rifiutato (503) |
| Dati di prova | eliminazione dei messaggi del mittente di prova dal TEST | da eseguire con procedura autorizzata (le tabelle 4A non consentono `DELETE` dall'applicazione) |

Il callback Meta, il token di verifica e il servizio template non vengono mai toccati.

## 6. Identificativi del numero

Il `phone_number_id` e il WABA del numero esistente sono già scritti in chiaro in:
- `roadmap/BLOCCO-4D_ATTIVAZIONE.md` (righe 7-8);
- `register_whatsapp.py`.

Qui non vengono ripetuti. Prima dell'uso vanno confermati sulla console Meta: il valore nei
file non è una prova della configurazione attuale.

## 7. Limiti noti (di produzione, non corretti qui)

- **Primo messaggio solo:** il ricevitore di `main` salva solo il primo messaggio di ogni
  payload e risponde 200 anche se il salvataggio fallisce. L'inoltro invece porta al CRM
  l'intero payload (dei mittenti ammessi).
- **Verifica GET:** in `main` legge `hub_mode` invece di `hub.mode`, quindi una
  **nuova** verifica del callback non riuscirebbe. La procedura non richiede di rifare la
  verifica.
- **Template della stima:** l'invio automatico (servizio `/send`), i template approvati, il
  PDF, i link della valutazione e Stima Pro non sono toccati da questa procedura.
