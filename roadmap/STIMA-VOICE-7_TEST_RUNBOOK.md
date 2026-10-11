# STIMA Voice Fase 7 — runbook di attivazione controllata su TEST

Solo **TEST**: servizio Render `core-0.1-test`, database `stima360_db_test`. **PROD esclusa.** `main` intoccabile.

> Stato alla consegna: **nessun passo eseguito su Render o sul database TEST.** Tutti i passi sono stati provati per intero su un PostgreSQL locale usa-e-getta chiamato `stima360_db_test` (schema reale fino alla 097, runner vero, app vera, fornitore AI simulato su HTTPS): esiti nel rapporto `roadmap/STIMA-VOICE-7_REPORT.md`.

Ogni blocco si incolla intero nella **Shell del servizio TEST**. Nessun blocco stampa password, chiavi o token. Sostituire `<PILOTA>`, `<AGENZIA>`, `<COLLEGA>`, `<ALTRA>` e `<URL_TEST>` con i valori decisi al passo 0.

**Regola generale:** ogni passo ha un esito atteso. Qualunque differenza = **STOP** e ritorno allo stato sicuro del passo. Lo **stato sicuro** di STIMA Voice è sempre lo stesso: **spento** (passo 10). Spento significa che nessuna rotta Voice scrive, l'interfaccia è nascosta e il CRM funziona come oggi.

## Autorizzazioni

| Passo | Cosa cambia | Autorizzazione specifica |
|---|---|---|
| 0 | niente (decisioni) | — |
| 1 | niente (sola lettura) | — |
| 2 | niente (verifica del backup) | — |
| 3 | **database TEST**: migrazioni 098 + 099 | **sì** |
| 4 | **codice**: montaggio del router | **fatto** (autorizzato l'11/10) |
| 5 | **deploy** del codice, Voice ancora spento | **fatto** con il push dell'11/10 (resta la verifica `--verify-off`) |
| 6 | **variabili Render**: fornitore AI | **sì** |
| 7 | **database TEST** (una riga) + **variabile Render** (elenco pilota) | **sì** |
| 8 | **variabile Render**: `STIMA_VOICE_ENABLED=1` (solo testo) | **sì** |
| 9 | **dati TEST**: dati sintetici + tre scenari + pulizia | **sì** |
| 10 | spegnimento | sempre ammesso, in qualunque momento |
| 11 | microfono del browser | **sì**, dopo la decisione su Google/Apple |

---

## Passo 0 — Decisioni prima di iniziare

1. **Agenzia pilota** `<AGENZIA>` e **operatore pilota** `<PILOTA>` (id di `operator_users`). Requisiti del pilota: attivo; **nessun Google Calendar collegato** (altrimenti l'appuntamento di prova finirebbe in un calendario reale); nessun impegno domani 15:00-16:00 il giorno della certificazione.
2. Facoltativi, per le prove di isolamento live: un **collega** `<COLLEGA>` della stessa agenzia che NON sarà nell'elenco pilota, e un operatore `<ALTRA>` di un'altra agenzia. Senza di loro le prove S.10 e S.11 risultano BLOCKED (coperte dai test locali).
3. **Fornitore AI**: servizio, modello e chiave, con il contratto per il trattamento dei dati (DPA). Il testo dettato contiene dati personali (nomi, telefoni) e viene inviato al fornitore.
4. **Modalità** dell'agenzia per la certificazione: `auto` (comportamento finale: esegue da solo ciò che è sicuro, chiede su ambiguità, doppioni e rischi) oppure `review` (ogni passo chiede conferma, solo misura). Lo script funziona con entrambe.
5. **Dati sintetici sul database destinato a diventare il CRM operativo**: vedi rapporto §6. Restano, nel Cestino, 2 contatti, 1 immobile, 1 edificio, 1 appuntamento annullato, 1 «Vende» chiuso e 1 collegamento proprietario, più le righe del registro vocale.

## Passo 1 — Salute del CRM TEST (sola lettura)

```bash
cd /opt/render/project/src 2>/dev/null || true
echo "commit in esecuzione: $RENDER_GIT_COMMIT"
echo "database: $DB_NAME"
ls -l migrations/098_voice_commands.sql migrations/099_voice_undo.sql scripts/voice_7_live_cert.py
```

```bash
cat > /tmp/voice7_1.sql <<'SQL'
-- STIMA Voice Fase 7 - passo 1/3: fotografia del CRM TEST PRIMA di ogni intervento.
-- SOLA LETTURA (BEGIN READ ONLY ... ROLLBACK). Nessun nome di persona stampato.
-- Uso: psql ... -v ON_ERROR_STOP=1 -v pilot=<id operatore pilota> -f questo_file
BEGIN READ ONLY;

\echo '== 1. database e orologio'
SELECT current_database() AS database, now() AS adesso, current_setting('TimeZone') AS fuso,
       split_part(version(), ' ', 2) AS postgres;

\echo '== 2. ultime migrazioni (atteso: ultima 097, 098 e 099 assenti)'
SELECT version, applied_at, rolled_back_at FROM schema_migrations ORDER BY version DESC LIMIT 5;
SELECT count(*) AS voice_in_registro FROM schema_migrations WHERE version LIKE '09%voice%';

\echo '== 3. oggetti Voice gia'' presenti (atteso: tutti 0)'
SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name LIKE 'voice\_%') AS tabelle,
       (SELECT count(*) FROM pg_proc WHERE proname LIKE 'voice\_%') AS funzioni,
       (SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'trg\_voice\_%') AS trigger;

\echo '== 4. tabelle e colonne su cui poggiano 098, 099 e le operazioni vocali (atteso: mancanti = 0)'
WITH attese(tabella, colonna) AS (VALUES
  ('agencies', 'id'), ('operator_users', 'id'), ('agency_memberships', 'role'),
  ('contacts', 'deleted_at'), ('contacts', 'assigned_agent_id'), ('contacts', 'phone_normalized'),
  ('properties', 'record_kind'), ('properties', 'client_request_id'), ('properties', 'deleted_at'),
  ('properties', 'assigned_agent_id'), ('properties', 'building_id'),
  ('buildings', 'units_declared'), ('buildings', 'client_request_id'), ('buildings', 'deleted_at'),
  ('appointments', 'assigned_user_id'), ('appointments', 'appointment_type'), ('leads', 'pipeline'),
  ('property_contacts', 'role'), ('activities', 'metadata'), ('tasks', 'metadata'),
  ('record_lifecycle_events', 'entity_type'), ('calendar_connections', 'status'))
SELECT count(*) FILTER (WHERE c.column_name IS NULL) AS mancanti,
       string_agg(a.tabella || '.' || a.colonna, ', ') FILTER (WHERE c.column_name IS NULL) AS elenco
  FROM attese a LEFT JOIN information_schema.columns c
    ON c.table_schema = 'public' AND c.table_name = a.tabella AND c.column_name = a.colonna;

\echo '== 5. agenzie e operatori attivi per ruolo (solo conteggi)'
SELECT a.id, a.slug, m.role, count(*) AS operatori_attivi
  FROM agencies a JOIN agency_memberships m ON m.agency_id = a.id AND m.status = 'active'
  JOIN operator_users u ON u.id = m.operator_user_id AND u.status = 'active'
 GROUP BY a.id, a.slug, m.role ORDER BY a.id, m.role;

\echo '== 6. il pilota (atteso: una riga, attivo, Google non collegato, agenda libera domani 15-16)'
SELECT u.id, u.status AS utente, m.agency_id, m.role, m.status AS membership,
       (SELECT count(*) FROM calendar_connections c WHERE c.user_id = u.id AND c.status <> 'disconnected') AS google_collegato,
       (SELECT count(*) FROM appointments ap WHERE ap.assigned_user_id = u.id AND ap.agency_id = m.agency_id
           AND ap.status IN ('requested', 'scheduled', 'confirmed')
           AND ap.start_at < (((now() AT TIME ZONE 'Europe/Rome')::date + 1) + time '16:00') AT TIME ZONE 'Europe/Rome'
           AND ap.end_at > (((now() AT TIME ZONE 'Europe/Rome')::date + 1) + time '15:00') AT TIME ZONE 'Europe/Rome') AS impegni_domani_15_16
  FROM operator_users u JOIN agency_memberships m ON m.operator_user_id = u.id
 WHERE u.id = :pilot;

\echo '== 7. salute del CRM: conteggi da confrontare dopo ogni passo'
SELECT (SELECT count(*) FROM contacts) AS contatti, (SELECT count(*) FROM properties) AS immobili,
       (SELECT count(*) FROM buildings) AS edifici, (SELECT count(*) FROM appointments) AS appuntamenti,
       (SELECT count(*) FROM leads) AS lead, (SELECT count(*) FROM property_contacts) AS legami_immobile,
       (SELECT count(*) FROM activities) AS attivita, (SELECT count(*) FROM tasks) AS task,
       (SELECT count(*) FROM operator_sessions WHERE revoked_at IS NULL AND expires_at > now()) AS sessioni_vive;

\echo '== 8. residui sintetici di run precedenti (atteso: 0 e 0)'
SELECT (SELECT count(*) FROM contacts WHERE last_name ILIKE 'Collaudovoce%' AND deleted_at IS NULL) AS contatti_attivi,
       (SELECT count(*) FROM buildings WHERE address ILIKE 'Via Collaudo Voce%'
           AND (to_jsonb(buildings)->>'deleted_at') IS NULL) AS edifici_attivi;

ROLLBACK;
SQL
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -v pilot=<PILOTA> -f /tmp/voice7_1.sql
```

**Atteso:**
- `DB_NAME` = `stima360_db_test`; i tre file presenti;
- §2: l'ultima migrazione è la **097**; `voice_in_registro` = 0;
- §3: 0 tabelle, 0 funzioni, 0 trigger Voice;
- §4: `mancanti` = 0;
- §6: una riga, `utente` e `membership` attivi, `google_collegato` = 0, `impegni_domani_15_16` = 0;
- §8: 0 e 0.

**Annotare** i conteggi del §7: si confrontano dopo i passi 3, 5 e 9.

**Arresto:** una qualunque differenza. **Stato sicuro:** nulla è cambiato.

## Passo 2 — Backup e ripristino del database TEST

Da qui non si vede Render: lo verifica il titolare dalla dashboard.

1. Render → database `stima360-db-test` → **Recovery / Backups**: annotare l'orario dell'ultimo backup disponibile e la finestra di ripristino (Point-in-Time Recovery, se il piano la offre).
2. Annotare l'**orario esatto** di inizio del passo 3: è il punto a cui tornare in caso di danno.

**Atteso:** un backup ripristinabile più recente dell'ultima modifica significativa, oppure una finestra PITR che copre il passo 3.
**Arresto:** nessun backup ripristinabile. In quel caso non si va avanti.
**Ritorno:** il ripristino è l'ultima risorsa. Le migrazioni 098 e 099 creano solo tabelle nuove e non toccano righe esistenti: un errore si risolve con lo spegnimento (passo 10), non con un ripristino.

## Passo 3 — Migrazioni 098 e 099 (scrive lo schema) · autorizzazione

```bash
python scripts/p26_migrate.py status --operator "giorgio.larasa"
python scripts/p26_migrate.py plan   --operator "giorgio.larasa"
```

**Atteso:** `status` senza righe `PROBLEM` e **solo** `pending 098_voice_commands` e `pending 099_voice_undo`; `plan`: `static validation: OK`.
**Arresto:** qualunque altra migrazione in sospeso o un `PROBLEM`: **non applicare**.

```bash
python scripts/p26_migrate.py apply --operator "giorgio.larasa"
```

```bash
cat > /tmp/voice7_2.sql <<'SQL'
-- STIMA Voice Fase 7 - passo 2/3: verifica DOPO l'applicazione di 098 e 099.
-- SOLA LETTURA. Atteso: due righe di registro, 5 tabelle, 6 trigger, nessuna
-- agenzia accesa (STIMA Voice resta spento), conteggi CRM uguali al passo 1.
BEGIN READ ONLY;

\echo '== 1. registro delle migrazioni'
SELECT version, applied_at, applied_by_operator, rolled_back_at FROM schema_migrations
 WHERE version IN ('098_voice_commands', '099_voice_undo') ORDER BY version;

\echo '== 2. oggetti creati (atteso: 5 tabelle, 6 trigger)'
SELECT string_agg(table_name, ', ' ORDER BY table_name) AS tabelle, count(*) AS n
  FROM information_schema.tables WHERE table_schema = 'public' AND table_name LIKE 'voice\_%';
SELECT string_agg(tgname, ', ' ORDER BY tgname) AS trigger, count(*) AS n
  FROM pg_trigger WHERE tgname LIKE 'trg\_voice\_%';

\echo '== 3. STIMA Voice spento ovunque (atteso: 0 agenzie accese, 0 comandi)'
SELECT (SELECT count(*) FROM voice_agency_settings WHERE enabled) AS agenzie_accese,
       (SELECT count(*) FROM voice_commands) AS comandi;

\echo '== 4. salute del CRM: confrontare con il passo 1'
SELECT (SELECT count(*) FROM contacts) AS contatti, (SELECT count(*) FROM properties) AS immobili,
       (SELECT count(*) FROM buildings) AS edifici, (SELECT count(*) FROM appointments) AS appuntamenti,
       (SELECT count(*) FROM leads) AS lead, (SELECT count(*) FROM property_contacts) AS legami_immobile,
       (SELECT count(*) FROM activities) AS attivita, (SELECT count(*) FROM tasks) AS task;

ROLLBACK;
SQL
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -f /tmp/voice7_2.sql
```

**Atteso:**
- `applied 098_voice_commands` e `applied 099_voice_undo`, ciascuna nella propria transazione;
- 5 tabelle `voice_*` e 6 trigger;
- `agenzie_accese` = 0 e `comandi` = 0: STIMA Voice resta **spento**;
- conteggi CRM identici al passo 1.

**Ordine:** 098 prima di 099; il runner lo garantisce. La 099 riferisce `voice_command_steps` della 098.

**Ritorno allo stato sicuro:** non serve. Senza una riga accesa in `voice_agency_settings`, le tabelle sono inerti. Le `*_down.sql` esistono ma il runner è forward-only: una versione annullata è consumata. Inoltre la down della 098 rifiuta di girare se il registro contiene comandi. Si usano solo prima del primo comando e con una decisione esplicita.

## Passo 4 — Montaggio del router (codice) · FATTO (autorizzato l'11/10)

`main.py` monta `/api/voice` come gli altri domini (`require_authenticated_operator`), insieme alle 5 sentinelle:
- `test_voice_1::test_05` e `test_voice_5::test_e4`: accettano solo la riga di import e quella di mount;
- `test_voice_4::test_n1`: `main.py` cambia solo per quelle righe, senza righe tolte;
- `test_p26_5::TENANT_PREFIXES`: contiene `/api/voice`, quindi cookie e solo cookie;
- `test_p26_6`: dominio VOICE nella matrice ostile, con un certificatore «solo rifiuti» e la prova che un comando altrui restituito fa fallire la matrice.

Montato non significa acceso: ogni richiesta passa da `voice.service.guard`.

## Passo 5 — Deploy con Voice spento (verifica)

Il push su `core-0.1-test` avvia il deploy automatico. **Nessuna variabile `STIMA_VOICE_*` impostata.**

```bash
python scripts/voice_7_live_cert.py --approved-commit "$RENDER_GIT_COMMIT" --base-url "<URL_TEST>" --pilot-user-id <PILOTA> --verify-off
```

**Atteso:** `K.1` stato `{enabled: false}`, `K.2` invio rifiutato `403 VOICE_GLOBALLY_DISABLED`, `K.3` nessun comando, `K.4` CRM raggiungibile, `Z.1` sessione revocata, **ESITO: PASS**. Nell'interfaccia `/os` la voce «Voce» **non compare** per nessuno.
**Arresto:** un solo FAIL. **Ritorno:** revert del commit del passo 4 e nuovo push.

## Passo 6 — Fornitore AI (variabili Render) · autorizzazione

Render → servizio TEST → Environment, come **Secret** dove previsto:
- `STIMA_VOICE_AI_URL` — endpoint HTTPS compatibile chat completions (OpenAI). `http://` è rifiutato.
- `STIMA_VOICE_AI_MODEL` — nome del modello. Non esiste un valore di default.
- `STIMA_VOICE_AI_KEY` — la chiave: **mai** nel repository, nei log o in chat.
- `STIMA_VOICE_AI_TIMEOUT` — facoltativo, secondi di lettura; default 20.

`STIMA_VOICE_ENABLED` resta **non impostato**: Voice resta spento. Ripetere il `--verify-off` del passo 5.

**Atteso:** PASS come al passo 5.
**Ritorno:** togliere le quattro variabili.

## Passo 7 — Una sola agenzia e un solo operatore · autorizzazione

1. Variabile Render `STIMA_VOICE_PILOT_USER_IDS=<PILOTA>`: solo quell'id. Un valore non valido, per esempio `12;`, spegne Voice per **tutti**.
2. La riga dell'agenzia pilota (scrive **una** riga):

```bash
cat > /tmp/voice7_3.sql <<'SQL'
-- STIMA Voice Fase 7 - passo 3/3: interruttore dell'agenzia pilota. SCRIVE (una riga).
-- Solo su `stima360_db_test`, solo con autorizzazione esplicita del passo.
-- Uso:
--   accensione: psql ... -v ON_ERROR_STOP=1 -v agency=<id> -v enabled=true  -v mode=auto -f questo_file
--   spegnimento: psql ... -v ON_ERROR_STOP=1 -v agency=<id> -v enabled=false -v mode=auto -f questo_file
-- `mode`: 'auto' (comportamento finale: esegue da solo cio' che e' sicuro, chiede su
-- ambiguita', doppioni e rischi), 'assisted' o 'review' (ogni passo chiede conferma).
-- Lo spegnimento vale dalla richiesta successiva, senza riavvio.
SELECT current_database() = 'stima360_db_test' AS database_giusto \gset
\if :database_giusto
BEGIN;
SELECT id, slug FROM agencies WHERE id = :agency;
INSERT INTO voice_agency_settings (agency_id, enabled, mode)
VALUES (:agency, :enabled, :'mode')
ON CONFLICT (agency_id) DO UPDATE SET enabled = EXCLUDED.enabled, mode = EXCLUDED.mode, updated_at = NOW();
SELECT agency_id, enabled, mode, auto_intents, max_commands_per_day, transcript_retention_days, updated_at
  FROM voice_agency_settings ORDER BY agency_id;
COMMIT;
\else
\echo 'BLOCCATO: questo non e'' stima360_db_test. Nessuna modifica.'
\endif
SQL
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -v agency=<AGENZIA> -v enabled=true -v mode=auto -f /tmp/voice7_3.sql
```

**Atteso:** una riga `enabled = t` per `<AGENZIA>`, nessun'altra agenzia accesa. Con `STIMA_VOICE_ENABLED` ancora assente, Voice resta **spento**: il `--verify-off` del passo 5 dà ancora PASS.
**Ritorno:** stesso blocco con `-v enabled=false`.

## Passo 8 — Solo inserimento testuale · autorizzazione

Variabile Render `STIMA_VOICE_ENABLED=1`. Il cambio di variabile riavvia il servizio. `STIMA_VOICE_BROWSER_SPEECH` resta **non impostato**: niente pulsante «Parla», il microfono del browser resta spento. Sul telefono resta la dettatura della tastiera.

```bash
python scripts/voice_7_live_cert.py --approved-commit "$RENDER_GIT_COMMIT" --base-url "<URL_TEST>" --pilot-user-id <PILOTA> --plan-only
```

**Atteso:** guardie `0.1`–`0.7` PASS, impostazioni `{enabled: true, mode: ...}`, nessuna scrittura.
In `/os`, **solo** il pilota vede «Voce», in fondo al menu. Tutti gli altri operatori non la vedono e, aprendo `#/voce`, leggono «STIMA Voice non è attivo.».
**Ritorno:** passo 10.

## Passo 9 — Certificazione dei tre scenari e della sicurezza · autorizzazione

```bash
python scripts/voice_7_live_cert.py --approved-commit "$RENDER_GIT_COMMIT" --base-url "<URL_TEST>" \
  --pilot-user-id <PILOTA> --colleague-user-id <COLLEGA> --other-agency-user-id <ALTRA>
```

Cosa fa, in circa 2–5 minuti, ben dentro la finestra di annullamento di 15 minuti:
1. guardie, poi 13 rotte senza sessione, con un cookie inventato e con credenziali Basic finte: tutte 401;
2. due contatti «Fabio Collaudovoce&lt;run&gt;», senza email né telefono;
3. **A**: piano, scelta obbligata fra più «Fabio», id iniettato rifiutato, collegamento proprietario solo dopo conferma, unità commerciale assegnata al pilota, «Vende», doppia esecuzione senza doppioni;
4. **B**: domanda dell'indirizzo senza scritture, poi un edificio con `units_declared = 12` e nessuna unità;
5. **C**: contatto scelto, Agenda del pilota, domani 15:00-16:00 Europe/Rome, tipo `other`; lo stesso comando ripetuto trova il conflitto e non lo forza;
6. collega fuori pilota e operatore di un'altra agenzia: Voice spento, comando del pilota non raggiungibile;
7. annullamento dei tre comandi, Cestino dei contatti sintetici, inventario finale, sessioni revocate.

**Atteso:** **ESITO: PASS**, oppure `INCOMPLETO` solo per le prove senza `--colleague-user-id` o `--other-agency-user-id`. L'inventario elenca tutti gli id con il loro stato finale.
**Controllo dei log:** nei log del servizio cercare il marcatore stampato alla fine (`Collaudovoce...`) e il testo dello scenario A: **non devono comparire**.
**Arresto:** un FAIL. Spegnere (passo 10) prima di analizzare.
**Il modello reale non è deterministico:** se interpreta male un testo, la prova risulta FAIL o BLOCKED e il comando resta non eseguito o viene annullato. È un esito da riportare, non da forzare.

Interfaccia da iPhone (Safari reale), con il pilota:
1. `Voce`, scrivere o dettare con la tastiera «Domani alle 17 appuntamento con Fabio»;
2. controllare il piano, scegliere il contatto se richiesto, «Conferma ed esegui»;
3. «Annulla questo passo» entro 15 minuti.

Annotare l'esito di ogni punto: layout, leggibilità, tastiera che non copre i pulsanti, rotazione.

## Passo 10 — Spegnimento (stato sicuro, sempre ammesso)

Due interruttori indipendenti:
- **immediato, senza riavvio:** il blocco del passo 7 con `-v enabled=false`;
- **globale:** togliere `STIMA_VOICE_ENABLED`, o metterla a `0`. Render riavvia il servizio.

Verifica:

```bash
python scripts/voice_7_live_cert.py --approved-commit "$RENDER_GIT_COMMIT" --base-url "<URL_TEST>" --pilot-user-id <PILOTA> --verify-off
```

**Atteso:** PASS (`VOICE_DISABLED` per l'agenzia, `VOICE_GLOBALLY_DISABLED` globale). L'interfaccia nasconde «Voce» al caricamento successivo della pagina. Un comando a metà non si esegue più e non si annulla: resta nel registro com'era.

## Passo 11 — Microfono del browser (solo dopo decisione)

`STIMA_VOICE_BROWSER_SPEECH=1` mostra «Parla», che usa il riconoscimento vocale del browser: Chrome invia l'audio a Google, Safari può usare Apple. Si accende solo dopo una decisione esplicita su questi servizi. Si prova da iPhone: permesso concesso, permesso negato, nessun parlato, uscita dalla pagina.
**Ritorno:** togliere la variabile.

## Trascritti

Ogni comando conserva il testo dettato per `transcript_retention_days` (30). La cancellazione dei trascritti scaduti **non è pianificata**. Durante il pilota si esegue a mano, ed è anche un'operazione di scrittura sul TEST:

```bash
python -c "from voice import ledger; print('trascritti cancellati:', ledger.purge_expired_transcripts())"
```
