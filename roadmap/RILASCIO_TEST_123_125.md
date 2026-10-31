# Rilascio TEST delle migrazioni 123–125 e piano produzione

Pacchetto per `core-0.1-test` → servizio TEST e database `stima360_db_test`.
Nulla di questo documento è stato eseguito su Render: è **pronto**, non applicato.
Tutte le prove citate sono locali (PostgreSQL 16 usa-e-getta, dati sintetici).

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
125 up   18ae7e31d956bf1d17c25836221df17b7c4eddbef00e0f33307d3cd7f4e7ea8f
125 down 392f97873c1d7e55470e749d57e756cb80a264d945a4dfd9d595042da8fb26fd
```

Runner ufficiale invariato: `scripts/p26_migrate.py`, SHA-256
`0a4fce2ec08d5bdc8dfb50a2312313bd51089c2f043aecc87f33dec73b72249a`.

## 2. Ordine obbligatorio (provato in locale)

1. Pubblicare il codice su TEST (push di `core-0.1-test`, auto-deploy).
2. Attendere il deploy riuscito e verificare il commit LIVE.
3. Render One-Off Job sul servizio TEST: **prima** `check`, poi `apply`.
4. `verify` finale (lo esegue anche `apply`).

Il codice nuovo funziona con il database ancora a 122 (§ 4.1): le funzioni esistenti non cambiano e
quelle nuove rifiutano in modo pulito, senza errori 500 e senza invii. Per questo il codice va
**prima** delle migrazioni.

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

Controlli dello script (si ferma al primo `STOP`, codice d'uscita 1):
- runner con lo SHA atteso;
- 115–122 nel deploy e nel registro con i checksum attesi;
- 123–125 nel deploy con checksum up/down;
- nessun file oltre la 125;
- `DB_NAME = stima360_db_test`;
- `DB_HOST` uguale all'host indicato;
- sessione di controllo in sola lettura e database primario;
- nessuna migrazione estranea;
- pendenti esattamente 123, 124, 125;
- `plan` statico superato.

Dopo l'apply devono esserci tre tabelle, tre trigger e quattro vincoli, e nulla di pendente. Un secondo `apply` rifiuta.

## 4. Prove eseguite in locale

Replica costruita come il TEST: storico fino alla 118, poi 119–122 applicate con lo script
`release_119_122_migrations.py` del commit LIVE `2d797f3`.

### 4.1 Codice nuovo, database fermo alla 122 — 39/39

**Funzioni esistenti:**
- login e Shell;
- contatti, scheda immobile, agenti, conversazioni, report operatore;
- calendario Agenda;
- prenotazione: collegamento, prenotazione pubblica confermata, visita in Agenda, riprogrammazione, annullamento, disattivazione;
- visita da Agenda, foglio visita e PDF;
- firma simulata, che non risulta mai firmata;
- accesso del proprietario, report e PDF, revoca dell'accesso (report non più disponibile);
- dispatcher email;
- risposte automatiche in simulazione.

**Funzioni nuove senza tabelle:**
- **modo `send` dell'agenzia:** rifiutato (503 `SEND_NOT_INSTALLED`) e nessuna riga scritta;
- **ambiente con `WHATSAPP_AUTO_REPLY_MODE=send`:** il giro resta in simulazione, nessun accodamento e nessun invio;
- **divieto di contatto:** stato «non installato», registrazione rifiutata (409);
- **revoca del proprietario:** rifiutata (`REVOCATION_NOT_INSTALLED`), il collegamento resta attivo;
- **webhook firma:** 404;
- anonimo: 401.

Nei log dei server nessun traceback e nessuna tabella o colonna mancante.

### 4.2 Applicazione 123–125 con lo script, poi codice nuovo — 35/35 e 117/117

`apply` superato con verifica finale. Le stesse funzioni esistenti passano. Le nuove funzionano:
- modo `send` accettato, ma con l'ambiente senza variabile resta spento;
- divieto registrato;
- revoca del proprietario registrata;
- firme esistenti nell'ambiente `simulation`.

Collaudo API completo 117/117, log puliti.

### 4.3 Ritorno al codice `2d797f3` sul database a 125 — 31/31

Tutte le funzioni esistenti del § 4.1 passano con il codice vecchio sul database a 125, log puliti.
Il ritorno indietro del **codice** quindi regge.

**Limite del ritorno indietro (analisi del codice):** `2d797f3` non conosce i dati nuovi.
- **Divieti di contatto registrati:** il dispatcher e la risposta manuale di `2d797f3` non li leggono.
- **Collegamenti dedicati** (esistono solo se è stato usato il modo d'invio): `2d797f3` non ricontrolla padre e immobile all'uso e non li chiude con il padre.
- **Motivo e nota di revoca:** restano nel database, ma `2d797f3` non li mostra.

Regola per il ritorno indietro:
- se dopo il rilascio non è stato registrato **nessun** divieto e il modo d'invio non è mai stato usato, il ritorno al codice è sicuro;
- altrimenti, prima di tornare a `2d797f3`:
  1. disattivare i collegamenti dedicati ancora attivi;
  2. trattare a mano i contatti con divieto, tenendo fermi i canali che potrebbero raggiungerli (dispatch email e risposta manuale);

  oppure non tornare indietro e correggere in avanti.

### 4.4 Script di rilascio

| Passo | Esito |
|---|---|
| `check` con host sbagliato | STOP prima di aprire una connessione |
| `check` | controlli superati, pendenti esattamente 123–125, nessuna migrazione applicata |
| `apply` | 123, 124, 125 applicate e registrate; verifica finale superata |
| secondo `apply` | rifiutato |
| `verify` | superata |
| DOWN 125 → 124 → 123 in transazione annullata | eseguiti, tabelle rimosse |
| DOWN 123 con un'agenzia in modo `send` | rifiutato (`123 down refused`) |

Dopo queste prove l'unico cambio a una migrazione è un commento della 125, che precisa che
«production» è una condizione tecnica e non una prova di validità legale. Il checksum qui sopra è
quello aggiornato. Lo script con il nuovo checksum è stato riprovato con i test statici e con le prove
PostgreSQL della firma.

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

L'invio legacy `POST /api/admin/whatsapp/reply` è disattivato nel codice da `504b705`: risponde
sempre 410, anche se `WHATSAPP_PHONE_ID` e `WHATSAPP_TOKEN` sono presenti.

Sul TEST si usano solo dati sintetici. Nessun inoltro di messaggi di clienti reali (vedi
`BLOCCO-4_NUMERO_OPERATIVO_PIANO.md` § 4.2-bis).

## 6. Rientro su TEST

1. **Codice:** ridistribuire `2d797f3`. Provato compatibile con il database a 125 (§ 4.3), con il
   limite sui dati nuovi descritto lì. Ricordare che `2d797f3` ha ancora l'invio legacy attivo.
2. **Database:** solo se serve davvero, eseguire i DOWN in ordine inverso (125, 124, 123). Rifiutano
   se esistono dati nuovi: in quel caso conservarli o ripristinare il backup verificato.

## 7. Piano produzione (non eseguito, nessuna data)

`main` e `core-0.1-test` hanno storie **separate** (nessuna base comune): nessun merge.

1. **Prerequisiti:**
   - decisioni del titolare elencate in `roadmap/AVANZAMENTO_ROADMAP.md` § 4;
   - collaudo manuale delle schermate su TEST completato;
   - backup verificato del database di produzione.
2. **Servizio distinto:** il CRM va in produzione come servizio proprio (codice `core-0.1-test` a un
   commit fissato), con un database dedicato. Il servizio attuale di `main` resta com'è: template
   della stima, PDF e Stima Pro, richiesta di sopralluogo dal sito. Il numero WhatsApp si collega a
   questo CRM di produzione secondo `roadmap/BLOCCO-4_NUMERO_OPERATIVO_PIANO.md`.
3. **Migrazioni:** stesso metodo del TEST (script con checksum, `check` → `apply` → `verify`),
   con `EXPECTED_DB` del database di produzione fissato in uno script dedicato: lo script attuale
   rifiuta qualsiasi database diverso da `stima360_db_test`.
4. **Attivazione a gradini:** prima tutto spento (invio WhatsApp, risposte automatiche, firma reale),
   poi ricezione, poi invio manuale, poi risposte automatiche in simulazione, infine invio
   automatico per una sola agenzia. A ogni gradino c'è un criterio d'uscita misurabile e il ritorno
   al gradino precedente si ottiene togliendo una variabile d'ambiente.
5. **Recupero:** il codice si ripristina ridistribuendo il commit precedente, con il limite sui dati
   nuovi del § 4.3; il database con i DOWN (che rifiutano se esistono dati) o con il ripristino del
   backup.
