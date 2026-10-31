# Inoltro dal ricevitore WhatsApp di produzione al CRM — procedura di attivazione

**Stato:** codice pronto e provato in locale. **Nulla è distribuito.** Nessuna modifica a
`main`, al servizio di produzione, al callback Meta, al servizio template o al numero.
Ogni passo su produzione richiede un'**autorizzazione separata** del titolare.

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

Un segreto si genera così, e si incolla nelle due configurazioni senza salvarlo altrove:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 4. Attivazione (ordine obbligatorio)

0. **Autorizzazioni scritte:**
   - modifica di produzione;
   - mittente di prova (numero, persona, durata);
   - cancellazione dei dati di prova dal TEST al termine (vedi
     `roadmap/BLOCCO-4_NUMERO_OPERATIVO_PIANO.md` § 4.2-bis).
1. **CRM TEST:**
   - pubblicare `core-0.1-test`;
   - impostare `WHATSAPP_RELAY_SECRET`, `WHATSAPP_INBOUND_ALLOWED_SENDERS` e
     `WHATSAPP_INBOUND_ROUTES`;
   - verificare che 4B e 4D siano spenti (§ 3).

   Facoltativo: migrazione 126 con `scripts/release_126_migration.py` (`check`, `apply`,
   `verify`), che serve solo per «non letti».
2. **Prova del CRM senza produzione:** un inoltro sintetico firmato verso il TEST (con un
   `phone_number_id` di prova) deve dare 200. Lo stesso inoltro con un'altra firma deve
   dare 403.
3. **Produzione, codice spento:**
   - copiare `forwarder.py` come `whatsapp_relay.py`;
   - applicare la patch con `git apply --check main_webhook.patch`, poi `git apply`;
   - pubblicare **senza** le variabili `WHATSAPP_RELAY_*`.

   Verificare che:
   - la ricezione resti come prima (un messaggio compare in `admin_whatsapp.html`);
   - l'invio del template della stima resti come prima.
4. **Produzione, inoltro acceso:** impostare le tre variabili `WHATSAPP_RELAY_*` con il
   solo mittente di prova.
5. **Messaggio di prova:** il mittente autorizzato scrive al numero. Atteso:
   - in produzione la riga in `whatsapp_incoming` (vecchia interfaccia) e il log
     `whatsapp_relay delivered`;
   - nel CRM TEST, in «Messaggi WhatsApp», la conversazione con il messaggio, il contatto
     collegato se il numero corrisponde a un solo contatto, e il badge «non letti» se c'è
     la 126.
6. **Controllo negativo:** un messaggio di un numero non in elenco compare solo in
   produzione e non nel CRM TEST.

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
