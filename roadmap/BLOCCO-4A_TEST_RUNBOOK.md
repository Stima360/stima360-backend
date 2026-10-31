# BLOCCO-4A — runbook TEST: ricezione WhatsApp nel CRM

Solo **TEST** (`stima360_db_test`, servizio Render TEST `srv-d9agc4vaqgkc739jkjhg`). PROD esclusa.

## Stato di partenza

| Cosa | Stato |
|---|---|
| **118** (`whatsapp_inbound_messages`, `whatsapp_inbound_message_links`, `whatsapp_webhook_events`) | **preparata, non applicata** |
| Codice BLOCCO-4A | **commit locale**, nessun push |
| `whatsapp_incoming` (tabella storica, non creata da nessuna migrazione) | **non toccata**: i vecchi messaggi restano dove sono, non attribuiti |
| Traffico Meta verso il webhook del CRM | **non verificato** (vedi sotto) |

## L'ordine: migrazione PRIMA del push

Il branch ha l'auto-deploy. Prima la **118** col runner, poi il push. Se l'ordine si inverte il webhook risponde **503** (Meta ritenta, nessun messaggio perso finché ritenta) e la pagina «Messaggi WhatsApp» mostra «la migrazione 118 non è stata applicata».

```bash
python scripts/p26_migrate.py status --operator "<operatore>"
python scripts/p26_migrate.py plan   --operator "<operatore>"
python scripts/p26_migrate.py apply  --operator "<operatore>"
```

**Atteso:** pendente solo `118_whatsapp_inbound`.

## Configurazioni necessarie per un vero messaggio in ingresso

1. **`WHATSAPP_APP_SECRET`** sul servizio TEST: il segreto dell'app Meta; senza, il webhook risponde 503 prima di toccare il database.
2. **`WHATSAPP_INBOUND_ROUTES`** sul servizio TEST: `phone_number_id:agency_id` per ogni numero WhatsApp Business che riceve, separati da virgola (es. `123456789012345:2`). Il `phone_number_id` è quello di Meta (non il numero visibile). Un numero non elencato, elencato per due agenzie o legato a un'agenzia inesistente è salvato **senza agenzia** e non compare a nessuna agenzia.
3. **`WHATSAPP_VERIFY_TOKEN`**: per la verifica GET della sottoscrizione (ora accetta i parametri `hub.mode`/`hub.verify_token`/`hub.challenge` inviati da Meta).
4. **Webhook su Meta** puntato a `https://<servizio TEST>/webhook/whatsapp` con il campo `messages` sottoscritto. **Non modificato da questo blocco.** Oggi l'URL configurato su Meta non è verificabile da qui: il servizio separato `stima360-whatsapp-webhook-test` (altro repository) potrebbe essere il destinatario attuale; in questo repository risulta usato solo per l'invio (`invia_whatsapp`).

## Collaudo live (dopo push e configurazione, dati di prova)

1. inviare un messaggio di testo da un telefono di prova al numero configurato: compare in «Messaggi WhatsApp» dell'agenzia giusta, con data di invio e di ricezione;
2. inviare un'immagine: compare come «[Immagine]» (con didascalia), nessun file scaricato;
3. un contatto di prova con lo stesso telefono: il messaggio risulta collegato; con due contatti: «Contatto da scegliere» e associazione manuale;
4. associare contatto e immobile da titolare/amministratore; un agente vede solo i messaggi dei suoi contatti e non associa;
5. il report proprietario dell'immobile continua a dire «Richieste ricevute: Dato non disponibile».

## Rientro

Il codice precedente ignora le tabelle. La down `118_whatsapp_inbound_down.sql` **rifiuta** se esistono messaggi, associazioni o eventi: conservarle o ripristinare da un backup verificato.
