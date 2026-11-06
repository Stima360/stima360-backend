# WhatsApp a due caselle e importazione rubrica — runbook TEST (fasi A e B)

Solo **TEST** (`stima360_db_test`, servizio Render TEST). PROD esclusa. Dati sintetici: nessun numero reale collegato a Meta, nessuna rubrica reale importata in questa fase.

## Che cosa porta questo rilascio

| Cosa | Dove |
|---|---|
| Migration **130** `whatsapp_inboxes`, `whatsapp_inbox_access`; `phone_number_id` nella chiave dei segni di lettura e delle pause | `migrations/130_whatsapp_inboxes.sql` (+ `_down`) |
| Migration **131** `contacts.private_user_id`, `contact_phones`, `contact_imports` | `migrations/131_contact_import.sql` (+ `_down`) |
| Conversazione = (agenzia, **numero ricevente**, cliente); risposta dallo stesso numero; token per casella `WHATSAPP_TOKEN__<phone_number_id>` (ripiego `WHATSAPP_TOKEN`) | `inbound_messages/`, `message_replies/`, `auto_replies/` |
| Caselle: etichetta, tipo, automazioni on/off, accesso `agency`/`restricted` + operatori autorizzati (configura il titolare) | `whatsapp_inboxes/`, `GET/PUT /api/whatsapp-inboxes`, Impostazioni → **Caselle WhatsApp** |
| Vista WhatsApp: chip della casella, filtro per casella, «ricevuto su …», «Partirà da …», avviso «ha scritto anche a …» | `static/os_shell/assets/views/messaggi.js`, `components/whatsapp-reply.js`, `components/auto-reply.js` |
| Contatti → **Importa rubrica** (.vcf): anteprima, «Importa tutti», voci ambigue decise dall'operatore; contatti riservati a chi importa; «Condividi con l'agenzia» nella scheda contatto | `contact_imports/`, `POST /api/contact-imports/preview`, `.../{id}/commit`, `components/contact-import.js` |

Che cosa **non** cambia: il numero 1 (nessuna riga di casella = comportamento di oggi), il template della stima, i follow-up email, la produzione `main`.

## L'ordine: migrazioni PRIMA del deploy

Il branch ha l'auto-deploy. Senza la 130 la vista WhatsApp funziona ma letti/non letti risultano «non disponibili» e la pagina Caselle dice «migrazione 130 non applicata»; senza la 131 «Importa rubrica» risponde 503. Le pause delle risposte automatiche e i segni di lettura richiedono la 130 (colonna `phone_number_id`).

```bash
python scripts/p26_migrate.py status --operator "<operatore>"
python scripts/p26_migrate.py plan   --operator "<operatore>"
python scripts/p26_migrate.py apply  --operator "<operatore>"
```

**Atteso:** pendenti solo `130_whatsapp_inboxes` e `131_contact_import`, in quest'ordine.

## Variabili sul servizio TEST

| Variabile | Serve a |
|---|---|
| `WHATSAPP_INBOUND_ROUTES` | aggiungere il **secondo** `phone_number_id` di prova: `IDNUM1:<agency_id>,IDNUM2:<agency_id>`. Senza, la casella 2 non esiste (nessun numero di ripiego). |
| `WHATSAPP_TOKEN__<IDNUM2>` | facoltativa: token della casella 2 se diverso da `WHATSAPP_TOKEN`. Finché l'invio resta `WHATSAPP_OUTBOUND_MODE` ≠ `meta` non serve. |
| `WHATSAPP_INBOUND_ALLOWED_SENDERS`, `WHATSAPP_RELAY_SECRET` | come oggi (inoltro dalla produzione). Nota: `scripts/check_whatsapp_relay_config.py` continua a pretendere **una** voce in `WHATSAPP_INBOUND_ROUTES` (collaudo del relay a numero singolo): con due voci segnala un problema atteso. |

Il numero 2 **di prova** su TEST è un `phone_number_id` sintetico (es. `900000000000003`): i messaggi si simulano con il webhook firmato (`X-Hub-Signature-256` con `WHATSAPP_APP_SECRET`) o con l'inoltro firmato dalla produzione. Nessun numero reale va collegato al TEST.

## Prove sul TEST (titolare, poi amministratore e agente)

### Due caselle
1. **Impostazioni → Caselle WhatsApp**: compaiono entrambi i numeri instradati; il numero 1 è «impostazioni predefinite» (accesso di agenzia, automazioni attive). Sul numero 2: etichetta «Numero personale», tipo «Numero personale (app + API)», **Automazioni attive = no**, **Accesso = Solo il titolare e gli operatori autorizzati** → Salva.
2. Inviare al webhook due messaggi dello stesso mittente di prova, uno per numero. In **WhatsApp**: due chat distinte, con il chip della casella e il filtro «Tutte le caselle / etichette»; aprendo una chat, la testata dice «ricevuto su …» e l'avviso «Questo cliente ha scritto anche a …» porta all'altra; un solo contatto collegato (se il numero è in rubrica).
3. Rispondere da ciascuna chat: la conferma dice «Partirà da <casella>»; con `WHATSAPP_OUTBOUND_MODE` spento la risposta resta bloccata come oggi (nessuna chiamata a Meta); nel registro `communication_messages.metadata.phone_number_id` è quello della chat.
4. Letti/non letti: aprire la chat del numero 1 azzera solo i suoi non letti.
5. **Amministratore** e **agente**: la chat del numero 2 non compare, l'indirizzo diretto `#/messaggi/<telefono>/<IDNUM2>` dà «non trovata»; autorizzando l'amministratore dalla pagina Caselle la vede; l'agente continua a vedere solo i contatti assegnati.
6. Risposte automatiche (modo `simulate`): sul numero 2 ogni decisione è «Nessuna risposta automatica — Casella senza automazioni»; sul numero 1 come prima.

### Importazione rubrica
7. **Contatti → Importa rubrica** con un `.vcf` di prova (nomi e numeri inventati): l'anteprima conta nuovi / già in rubrica / da decidere / senza numero; «Importa tutti» crea i contatti **riservati** a chi importa (titolare e colleghi non li vedono), con tutti i numeri (scheda contatto → «Altri numeri»).
8. Ripetere lo stesso import: tutte le voci «già in rubrica», nessun doppione, nessun numero aggiunto.
9. Una voce con un numero presente su due contatti: nella finestra si sceglie «Unisci a…», «Crea un nuovo contatto» o «salta».
10. Una chat WhatsApp ricevuta prima dell'import senza contatto si collega da sola dopo l'import; un messaggio da un altro numero dello stesso contatto si collega all'arrivo. A chi non vede il contatto riservato la chat mostra «Contatto riservato».
11. Nessun invio: il registro comunicazioni non cambia durante l'import.
12. «Condividi con l'agenzia» nella scheda del contatto riservato: da quel momento lo vedono tutti.

## Rientro

`130_whatsapp_inboxes_down.sql` elimina caselle e accessi (configurazione) e riporta segni di lettura e pause alla chiave per mittente. `131_contact_import_down.sql` **rifiuta** se esistono numeri aggiuntivi o contatti riservati: conservare o ripristinare da un backup verificato.
