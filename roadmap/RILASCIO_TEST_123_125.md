# Rilascio TEST delle migrazioni 123–125 e piano produzione

Pacchetto per `core-0.1-test` → servizio TEST e database `stima360_db_test`.
Nulla di questo documento è stato eseguito su Render: è **pronto**, non applicato.

## 1. Cosa contiene

| Migrazione | Scopo | Additiva | DOWN |
|---|---|---|---|
| `123_whatsapp_auto_reply_send` | modo d'invio del motore (spento), motivo `auto_reply` nel registro, righe d'invio, collegamenti di prenotazione dedicati | sì | rifiuta se esistono dati del modo d'invio |
| `124_do_not_contact_and_link_revocation` | divieto totale di contatto, motivo e nota della disattivazione dei collegamenti | sì | rifiuta se esistono divieti o motivi |
| `125_signature_provider_readiness` | ambiente della firma, livello, audit trail, eventi del fornitore | sì | rifiuta se esistono dati di un fornitore reale |

Checksum SHA-256 (incorporati in `scripts/release_123_125_migrations.py`):

```
123 up   e2ef4fcf67fe40b6d3e98c3e39a1446d7b91fff87f24551dc434c819732d5117
123 down b133220c95cd4c8ee0fcee39ca9e86cf90ef5a2eab8c60343891b98be6c0cc00
124 up   60928f8a41d8dad4c410d81bbacedc76815ab91c0b108395864b036b3785eaf7
124 down 70e6c80f558cc3d84000deb119bc7f64bc22bc4e0d39cbc5d019f3495875070b
125 up   2047ee3e160b6a586e3fba1bf5058975bab68c95a31b90f1fe227eaa15cdc888
125 down 392f97873c1d7e55470e749d57e756cb80a264d945a4dfd9d595042da8fb26fd
```

Runner ufficiale invariato: `scripts/p26_migrate.py`, SHA-256
`0a4fce2ec08d5bdc8dfb50a2312313bd51089c2f043aecc87f33dec73b72249a`.

## 2. Ordine obbligatorio

Il codice di questo pacchetto **tollera** un database ancora a 122 (ogni lettura nuova è
protetta: senza 123 il modo d'invio non si attiva, senza 124 il divieto non esiste e i
collegamenti si disattivano senza motivo, senza 125 la firma resta simulata). Quindi:

1. pubblicare il codice su TEST (push di `core-0.1-test`, auto-deploy);
2. attendere il deploy riuscito e verificare il commit LIVE;
3. Render One-Off Job sul servizio TEST, **prima** `check`, poi `apply`;
4. `verify` finale (lo esegue anche `apply`).

## 3. startCommand (Render One-Off Job, servizio TEST)

Sostituire `<HOST_DB_TEST_VERIFICATO>` con l'host del database TEST letto dalle variabili
del servizio TEST (non incollarlo nel repository).

Solo lettura:

```bash
python scripts/release_123_125_migrations.py check --expected-host <HOST_DB_TEST_VERIFICATO>
```

Applicazione (fa da sé tutti i controlli di `check`, applica con il runner ufficiale e verifica):

```bash
python scripts/release_123_125_migrations.py apply --expected-host <HOST_DB_TEST_VERIFICATO>
```

Verifica finale (ripetibile, sola lettura):

```bash
python scripts/release_123_125_migrations.py verify --expected-host <HOST_DB_TEST_VERIFICATO>
```

Controlli dello script (si ferma al primo `STOP`, codice d'uscita 1): runner con lo SHA atteso;
115–122 nel deploy e nel registro con i checksum attesi; 123–125 nel deploy con checksum up/down;
nessun file oltre la 125; `DB_NAME = stima360_db_test`; `DB_HOST` uguale all'host indicato;
sessione di controllo in sola lettura e database primario; nessuna migrazione estranea; pendenti
esattamente 123, 124, 125; `plan` statico superato. Dopo: tre tabelle, tre trigger, quattro vincoli
presenti, nulla pendente. Un secondo `apply` rifiuta.

## 4. Prova generale eseguita in locale (cluster PostgreSQL 16 usa-e-getta)

Replica costruita come il TEST: storico fino alla 118, poi 119–122 applicate con lo script
`release_119_122_migrations.py` del commit LIVE `2d797f3`; quindi, dal codice attuale:

| Passo | Esito |
|---|---|
| `check` con host sbagliato | STOP prima di aprire una connessione |
| `check` | controlli superati, pendenti esattamente 123–125, nessuna migrazione applicata |
| `apply` | 123, 124, 125 applicate e registrate; verifica finale superata |
| secondo `apply` | rifiutato (`123-125 non ancora registrate`: STOP) |
| `verify` | superata |
| DOWN 125 → 124 → 123 in transazione annullata | eseguiti, tabelle rimosse |
| DOWN 123 con un'agenzia in modo `send` | rifiutato (`123 down refused`) |

Il runner è forward-only: un rientro con i file DOWN va eseguito a mano, nell'ordine inverso,
e richiede di allineare il registro `schema_migrations` secondo la procedura P26 (non eseguita qui).

## 5. Variabili d'ambiente: cosa NON impostare su TEST in questa fase

| Variabile | Valore in questa fase | Effetto |
|---|---|---|
| `WHATSAPP_AUTO_REPLY_MODE` | assente (o `simulate`) | con `send` il motore potrebbe inviare, se anche l'agenzia è su `send` |
| `COMMUNICATION_AUTO_REPLY_TICK` | assente | con `1` il runner di dispatch chiama il giro delle risposte |
| `WHATSAPP_OUTBOUND_MODE` | assente | trasporto Meta del 4B spento |
| `ESIGN_PROVIDER` | assente o `simulated` | nessun fornitore reale esiste comunque |
| `PUBLIC_BASE_URL` | quella del servizio TEST (https) | serve solo al collegamento dedicato nel modo d'invio |

## 6. Rientro su TEST

1. **Codice:** ridistribuire `2d797f3` su TEST. Dall'analisi del codice è compatibile con un
   database a 125 (colonne nuove facoltative o con default, vincoli nuovi non toccati dai suoi
   percorsi), ma questa compatibilità **non è stata provata con test**: provarla su una replica
   prima di farvi affidamento.
2. **Database:** solo se serve davvero, eseguire i DOWN in ordine inverso (125, 124, 123). Rifiutano
   se esistono dati nuovi: in quel caso conservarli o ripristinare il backup verificato.

## 7. Piano produzione (non eseguito, nessuna data)

`main` e `core-0.1-test` hanno storie **separate** (nessuna base comune): nessun merge.

1. **Prerequisiti:** decisioni del titolare elencate in `roadmap/AVANZAMENTO_ROADMAP.md` § 4;
   collaudo manuale delle schermate su TEST completato; backup verificato del database di produzione.
2. **Servizio distinto:** il CRM va in produzione come servizio proprio (codice `core-0.1-test` a un
   commit fissato), con il suo database o con le migrazioni del CRM applicate a un database
   dedicato. Il servizio attuale di `main` resta com'è finché la fase 3 del piano WhatsApp non è
   chiusa (vedi `roadmap/BLOCCO-4_NUMERO_OPERATIVO_PIANO.md`).
3. **Migrazioni:** stesso metodo del TEST (script con checksum, `check` → `apply` → `verify`),
   con `EXPECTED_DB` del database di produzione da fissare in uno script dedicato: lo script attuale
   rifiuta qualsiasi database diverso da `stima360_db_test`.
4. **Attivazione a gradini:** prima tutto spento (invio WhatsApp, risposte automatiche, firma reale),
   poi ricezione, poi invio manuale, poi risposte automatiche in simulazione, infine invio
   automatico per una sola agenzia. A ogni gradino c'è un criterio d'uscita misurabile e il ritorno
   al gradino precedente si ottiene togliendo una variabile d'ambiente.
5. **Recupero:** il codice si ripristina ridistribuendo il commit precedente; il database con i DOWN
   (che rifiutano se esistono dati) o con il ripristino del backup.
