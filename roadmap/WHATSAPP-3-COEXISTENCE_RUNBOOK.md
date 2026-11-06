# WhatsApp Coexistence — collegare il numero WhatsApp Business personale (fase C)

Solo **TEST** (`stima360-backend-test`, `stima360_db_test`). PROD, `main`, il ricevitore di produzione e il **numero API attuale non cambiano**. Il numero personale resta sull'app WhatsApp Business del telefono: Meta lo collega anche alla Cloud API («Coexistence»).

**Il numero personale non è collegato finché il pannello non dice «Confermato da Meta».** Il CRM distingue tre cose: ciò che ha *chiesto* a Meta (i passi), ciò che Meta *conferma*, ciò che è *arrivato* (messaggi, eco dal telefono, storico, rubrica).

## Che cosa porta questo rilascio

| Cosa | Dove |
|---|---|
| Migrazione **132**: `direction`/`origin` sui messaggi WhatsApp (immutabili), `whatsapp_app_contacts`, `whatsapp_coexistence_onboardings` | `migrations/132_whatsapp_coexistence.sql` (+ `_down`, che **rifiuta** se ci sono dati) |
| Ricezione di `smb_message_echoes` (messaggi scritti dall'app), `history` (storico condiviso) e `smb_app_state_sync` (rubrica) | `inbound_messages/parser.py`, `service.py` |
| Eco e storico nella stessa conversazione, con «Dal telefono» / «Dallo storico»; esclusi da non letti, risposte automatiche e finestra di 24 ore; lo STOP conta solo se scritto dal cliente | `inbound_messages/`, `auto_replies/repository.py` |
| Filtro dei mittenti **per casella**: `WHATSAPP_INBOUND_ALLOWED_SENDERS__<phone_number_id>` | `inbound_messages/relay.py` |
| Procedura ufficiale Meta (Embedded Signup) dal CRM: Impostazioni → **Caselle WhatsApp** → «Collega con Meta» (solo titolare) | `whatsapp_coexistence/`, `components/whatsapp-coexistence.js` |
| Allegati aperti a richiesta, letti da Meta al momento, mai salvati; tipi pericolosi solo come file da scaricare | `whatsapp_coexistence/media.py`, `GET /api/whatsapp-coexistence/messages/{id}/media` |
| Rubrica del telefono importabile con le regole della fase B (anteprima, ambigui decisi a mano, contatti riservati a chi importa) | Contatti → Importa rubrica → «Rubrica WhatsApp · …» |

Nessuna rotta invia messaggi a clienti. Le risposte dal CRM restano quelle della fase A (stessa casella, stesso token, allowlist dei destinatari).

## 0. Prima di tutto: tre cose da confermare con Meta

1. **Disponibilità per i numeri italiani (+39).** Al lancio Coexistence escludeva diversi Paesi, fra cui quelli dell'Unione Europea. Verifica nella documentazione Meta *WhatsApp Business app coexistence* (sezione requisiti e Paesi) che oggi i numeri italiani siano ammessi. Se non lo sono, la procedura di Meta lo dirà e non si può procedere.
2. **Requisiti del telefono**: app WhatsApp Business aggiornata (versione minima indicata da Meta), numero attivo sull'app da qualche tempo, telefono con l'app aperta durante la procedura. Dopo il collegamento l'app va aperta **almeno ogni 14 giorni**, altrimenti Meta scollega la Cloud API.
3. **Accesso alla procedura**: l'app Meta deve poter usare Embedded Signup con la funzione Coexistence (`featureType: whatsapp_business_app_onboarding`). Servono il **Business Manager verificato**, i permessi `whatsapp_business_management` e `whatsapp_business_messaging`, e una configurazione di *Facebook Login for Business* di tipo WhatsApp Embedded Signup. Per collegare un numero della tua stessa azienda in genere basta l'accesso standard; se Meta chiede lo stato di *Tech Provider* o l'App Review, va completato prima.

## 1. Costi da confermare (pagina prezzi Meta, mercato Italia)

| Voce | Atteso | Da confermare |
|---|---|---|
| Messaggi scritti **dall'app** sul telefono | gratuiti, come oggi | sì |
| Risposte **dal CRM** entro 24 ore dall'ultimo messaggio del cliente (testo libero) | gratuite (finestra di servizio) | sì |
| Modelli (template) inviati dal CRM fuori finestra | a pagamento, per messaggio, secondo categoria (marketing / utility / autenticazione) | sì, prezzi Italia |
| Embedded Signup, webhook, storico, rubrica | nessun costo Meta | sì |
| Hosting Render TEST, database | invariati | — |

Nessun BSP intermedio: si usa la Cloud API direttamente. Il CRM **non invia** template da questo rilascio.

## 2. Su Meta (una volta sola)

1. **App Meta** (la stessa del numero API, o una nuova dedicata al CRM):
   - *Facebook Login for Business* → **Configurazioni** → nuova configurazione **WhatsApp Embedded Signup**, con i permessi `whatsapp_business_management` e `whatsapp_business_messaging`. Annota l'**ID della configurazione**.
   - *Impostazioni → Di base*: annota **App ID** e **chiave segreta** (App Secret).
   - *Facebook Login for Business → Impostazioni*: aggiungi `https://stima360-backend-test.onrender.com` ai domini consentiti per l'SDK JavaScript e agli URI di reindirizzamento OAuth validi. L'SDK funziona solo su HTTPS.
2. **Token di sistema** (Business Manager → Utenti di sistema): l'utente di sistema dell'app deve avere accesso al **nuovo** account WhatsApp Business del numero personale (si assegna dopo il passo 4). Il suo token permanente diventa `WHATSAPP_TOKEN__<phone_number_id>` sul TEST.
3. **Webhook**: il CRM chiede a Meta di mandare i messaggi del numero personale **direttamente al TEST** (*override* dell'indirizzo per quel WABA). Per questo durante la procedura scegli **un account WhatsApp Business (WABA) nuovo**, con il solo numero personale. Se lo metti nello stesso WABA del numero API, il CRM **non** sposta l'indirizzo (lo protegge e lo segnala) e i messaggi andrebbero alla produzione: non farlo.

## 3. Sul servizio TEST (Render → Environment)

| Variabile | Valore | Pubblica? |
|---|---|---|
| `META_APP_ID` | App ID | sì (va al browser) |
| `META_COEXISTENCE_CONFIG_ID` | ID della configurazione Embedded Signup | sì (va al browser) |
| `WHATSAPP_APP_SECRET` | App Secret dell'app Meta | **no** (scambio del codice e firma dei webhook) |
| `WHATSAPP_GRAPH_VERSION` | versione esplicita, per esempio `v21.0` | sì |
| `WHATSAPP_VERIFY_TOKEN` | una stringa nuova, lunga, casuale | no |
| `WHATSAPP_COEXISTENCE_CALLBACK_URL` | `https://stima360-backend-test.onrender.com/webhook/whatsapp` | sì |
| `WHATSAPP_MEDIA_MAX_MB` | facoltativa, predefinito 25 | — |

Le variabili del numero API attuale (`WHATSAPP_INBOUND_ROUTES` con il numero 1, `WHATSAPP_INBOUND_ALLOWED_SENDERS`, `WHATSAPP_RELAY_SECRET`, `WHATSAPP_TOKEN`) **non si toccano**.

### Migrazione 132 sul TEST

Il codice funziona anche senza: in quel caso eco, storico e rubrica vengono **rifiutati** (503) e Meta li riprova. Va applicata prima del collegamento, con il runner come per la 130/131 (servizio TEST → Jobs):

```bash
python scripts/p26_migrate.py status --operator "<operatore>"
```

```bash
python scripts/p26_migrate.py plan --operator "<operatore>"
```

```bash
python scripts/p26_migrate.py apply --operator "<operatore>"
```

**Atteso:** pendente solo `132_whatsapp_coexistence`.

## 4. Il collegamento, sul telefono e nel CRM (insieme, durante il collaudo)

1. Accedi al CRM TEST come **titolare** → Impostazioni → **Caselle WhatsApp** → riquadro «Collega il numero WhatsApp Business» → **Collega con Meta**.
2. Nella finestra di Meta: accedi con il tuo account Facebook del Business Manager, scegli l'azienda, **crea un nuovo account WhatsApp Business**, scegli «collega l'app WhatsApp Business esistente» e inserisci il numero personale.
3. Sul telefono: apri **WhatsApp Business** → inquadra il **QR** mostrato da Meta (o conferma la notifica) → alla domanda sulla condivisione dello **storico delle chat** scegli **Condividi** (fino a 6 mesi; facoltativo, ma senza non arriva).
4. Il CRM mostra il risultato: i passi (codice accettato, numero trovato, iscrizione ai messaggi) e lo stato. Annota il **phone_number_id** del numero personale (lo vedi nel pannello o in Meta → WhatsApp Manager).
5. **Subito** sul servizio TEST (entro 24 ore: Meta concede la sincronizzazione solo per un periodo limitato dopo il collegamento):
   - `WHATSAPP_INBOUND_ROUTES` = valore attuale **più** `,<PNID_PERSONALE>:<id_agenzia>`
   - `WHATSAPP_INBOUND_ALLOWED_SENDERS__<PNID_PERSONALE>` = `*` (tutti i clienti del numero personale; il numero 1 resta filtrato come oggi)
   - `WHATSAPP_TOKEN__<PNID_PERSONALE>` = token dell'utente di sistema con accesso al nuovo WABA
6. Dopo il riavvio del servizio: nel pannello premi **Verifica con Meta** (deve dire «Confermato da Meta») e poi **Richiedi di nuovo la rubrica** e **Richiedi di nuovo lo storico**. Prima dell'instradamento il CRM non li chiede apposta: arrivati senza agenzia resterebbero invisibili.
7. Impostazioni → Caselle WhatsApp: la casella del numero personale nasce **riservata** (titolare e autorizzati) e **senza automazioni**. Lasciala così per il collaudo.

## 5. Collaudo reale (insieme)

| Prova | Atteso |
|---|---|
| Un amico scrive al numero personale | compare in WhatsApp nel CRM, casella «Numero personale», con il contatto se è in rubrica |
| Rispondi **dal telefono** | nel CRM compare a destra, in verde, «Dal telefono» |
| Rispondi **dal CRM** (con `WHATSAPP_OUTBOUND_MODE=meta` e il destinatario nell'allowlist `WHATSAPP_OUTBOUND_ALLOWED_RECIPIENTS`) | parte dal numero personale e compare anche sul telefono |
| Storico | le chat condivise compaiono con «Dallo storico», nell'ordine originale, senza non letti |
| Rubrica | Contatti → Importa rubrica → «Rubrica WhatsApp · Numero personale»: anteprima, poi «Importa tutti» |
| Allegato (foto) | «Apri allegato» lo mostra; un documento si scarica |
| Amministratore o agente | non vedono la casella riservata |
| Numero API attuale | riceve e risponde come prima, risposte automatiche comprese |

## 6. Rientro

- Scollegare: in Meta → WhatsApp Manager rimuovi il numero dalla Cloud API, oppure sull'app WhatsApp Business → Strumenti aziendali → disconnetti. Il telefono continua a funzionare come prima.
- Sul TEST: togli `<PNID_PERSONALE>` da `WHATSAPP_INBOUND_ROUTES`. I messaggi già ricevuti restano.
- `132_whatsapp_coexistence_down.sql` **rifiuta** se esistono messaggi Coexistence, rubrica o collegamenti registrati: conservali, oppure ripristina da un backup verificato.
