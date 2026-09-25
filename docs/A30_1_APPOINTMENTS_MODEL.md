# A30-1 — MODELLO APPOINTMENTS

Stato: implementazione completata sul working tree (branch `core-0.1-test` @ `d8b23d0`), **non committata**, **non applicata** a nessun database reale.
Fonte delle decisioni: GATE A30-0 approvato (Q-A1…Q-A11) e REVIEW GATE A30-1 (Q1…Q7, approvato con correzioni pre-migration, recepite qui).

## 1. Perimetro

**Cosa c'è**
- La migration `072_a30_1_appointments` (+ down), additiva: estensione, 2 tabelle, 5 funzioni, 3 trigger (guardia, rifiuto della DELETE, registro append-only).
- `scripts/a30_test_cleanup.py`: la pulizia protetta dei dati di prova (Q7).
- Il pacchetto `appointments/` (enums, schemas, repository, service), **senza router**: non è montato in `main.py`.
- I test: statici e su PostgreSQL reale.

**Cosa non tocca**
`stima360.it`, `admin_stime_pro.html`, `stime`, `stime_dettagliate`, MATCH/`property_visits`, `stima_inspections` e i suoi consumer (LMC-13, LMC-15, P29-3), Google, `main.py`.

**Collisione con lavoro certificato**
Riguarda solo 24 file di test o d'inventario (vedi §8): sentinelle delle migration, inventario FK del cleanup P26-6 e registro dei punti di connessione. Nessun file applicativo certificato è stato modificato.

## 2. Schema definitivo

### `appointments`

| Colonna | Tipo | Note |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `agency_id` | BIGINT NOT NULL → `agencies` RESTRICT | immutabile (trigger) |
| `assigned_user_id` | BIGINT NULL | FK composita `(agency_id, assigned_user_id)` → `agency_memberships(agency_id, operator_user_id)`; membership **attiva** verificata dal trigger quando viene scritto. **Q4:** obbligatorio per scheduled e confirmed; può essere NULL per requested, cancelled e rescheduled, e per completed/no_show solo se la fonte è storica (`legacy_stime_dettagliate`, `stima_inspections_backfill`). Non si inferisce mai |
| `appointment_type` | VARCHAR(30) CHECK | 12 tipi |
| `status` | VARCHAR(20) CHECK, default `scheduled` | requested, scheduled, confirmed, completed, cancelled, no_show, rescheduled |
| `start_at`, `end_at` | **TIMESTAMPTZ** NOT NULL | `end_at > start_at`, durata ≤ 24h |
| `timezone` | VARCHAR(64) default `Europe/Rome` | CHECK = `Europe/Rome` (si allarga con una migration quando servirà) |
| `buffer_before_minutes`, `buffer_after_minutes` | SMALLINT default 0 | 0–240 |
| `blocked_range` | TSTZRANGE NOT NULL | **scritta solo dal trigger**: `[start−before, end+after)` |
| `stima_id` | INTEGER NULL | **riferimento morbido: nessuna FK verso `stime`** (Q-A6b); verificato dal service; non riassegnabile |
| `contact_id`, `lead_id`, `property_id` | BIGINT NULL, FK `ON DELETE SET NULL` | stessa agenzia (trigger); lead e contatto coerenti |
| `stima_inspection_id` | BIGINT NULL → `stima_inspections` SET NULL, UNIQUE | predisposto per la proiezione LMC-15, **non scritto da A30-1** |
| `location_text` (≤500), `notes` (≤5000) | TEXT | |
| `source` | VARCHAR(40) CHECK, default `crm_manual` | crm_manual, legacy_stime_dettagliate, stima_inspections_backfill, booking_link, system; immutabile |
| `source_record_id` | VARCHAR(100) NULL | obbligatorio per le fonti importate; immutabile |
| `test_run_id` | VARCHAR(64) NULL | **Q7:** presente se e solo se `source='a30_test'`; formato `[A-Za-z0-9_.:-]{1,64}`; immutabile |
| `rescheduled_from_id` | BIGINT NULL → `appointments` (NO ACTION) | UNIQUE: uno spostamento ha un solo successore; stesso marcatore di prova e stessa corsa del predecessore |
| `google_calendar_id`, `google_event_id`, `google_sync_status` (default `not_synced`), `google_last_synced_at` | | predisposti, non implementati |
| `created_by_user_id` | BIGINT NULL → `operator_users` | NULL = sistema/import; verificato con `lmc15_assert_operator_may_act` |
| `created_at`, `updated_at`, `version` | | `updated_at` e `version` (lock ottimistico) scritti dal trigger |
| `confirmed_at`, `completed_at`, `no_show_at`, `cancelled_at`, `cancelled_reason`, `rescheduled_at` | | coerenti con lo stato (matrice CHECK). **Q3:** `completed_at` è il momento reale dell'appuntamento, dichiarato dall'operatore oppure NOW() alla chiusura; mai ricavato da `start_at` |

### `appointment_events` (append-only)

Colonne: `id`, `agency_id`, `appointment_id` (FK composita `(agency_id, appointment_id)`), `event_type` (created / updated / status_changed), `from_status`, `to_status`, `actor_user_id`, `db_user`, `occurred_at`, `changes` (JSONB con le sole colonne cambiate).

**La scrive il repository e non un trigger**, nella stessa transazione della modifica.
- **Perché:** la certificazione P26-6 (serie 100, test 103d) vieta che un trigger scriva in una tabella di tenant. Sarebbe una scrittura che nessun sorgente Python nomina. La prima versione di A30-1 usava un trigger di audit e il test lo ha fermato; ho cambiato il disegno invece di chiedere un'esenzione.
- **Limite dichiarato:** una scrittura fatta fuori dal service, per esempio in psql, non lascia eventi.

## 3. Vincoli

- **Anti-sovrapposizione:** `EXCLUDE USING gist (assigned_user_id WITH =, blocked_range WITH &&) WHERE status IN (scheduled, confirmed, completed, no_show)`. Richiede `btree_gist`, che la migration crea con `CREATE EXTENSION IF NOT EXISTS`.
  - Gli intervalli sono `[)`, quindi 10–11 e 11–12 non sono in conflitto.
  - I buffer sono compresi nell'intervallo bloccato.
  - L'agenzia non entra nel vincolo, di proposito: una persona non può essere in due posti.
- **Matrice di stato:** gli stati aperti non hanno istanti di chiusura; ogni stato chiuso ha il proprio istante e non quelli degli altri.
- **Agente (Q4):** obbligatorio per scheduled e confirmed; NULL ammesso per requested, cancelled e rescheduled, e per completed/no_show solo con fonte storica.
- **Dati di prova (Q7):** `(source='a30_test') = (test_run_id IS NOT NULL)`; anche l'INSERT di un dato di prova è in **allowlist**: è ammesso solo se `a30_is_certified_test_database()`, altrimenti viene rifiutato (PROD, nome sconosciuto o simile). Non esiste più una blacklist di PROD: la funzione `a30_is_production_database()` è stata tolta.
- **Idempotenza import:** `UNIQUE (source, source_record_id) WHERE source_record_id IS NOT NULL`, più l'obbligo di `source_record_id` per le fonti importate.
- **Immutabili:** `agency_id`, `source`, `source_record_id`, `created_by_user_id`, `created_at`, `stima_id` una volta scritto.
- **Nessuna DELETE runtime** su `appointments`: un appuntamento si annulla, non si cancella. `appointment_events` è append-only. **Unica eccezione (Q7):** `a30_test_purge(run_id)`.
  - **Funziona solo in allowlist:** il database deve essere uno dei TEST certificati dal progetto, cioè `a30_is_certified_test_database()` con confronto esatto. Oggi l'elenco contiene solo `stima360_db_test`, lo stesso `REQUIRED_DB_NAME` della certificazione live P26-6. Qualunque altro nome (PROD, sconosciuto, simile) **fallisce chiuso**. Lo stesso vale per le eccezioni alla DELETE nei due trigger.
  - Dichiara la corsa solo per la propria transazione (`stima360.a30_test_purge`) e cancella soltanto le righe `a30_test` di quella corsa, insieme ai loro eventi.
  - Il comando per lanciarla è `scripts/a30_test_cleanup.py`, anch'esso in allowlist: `DB_NAME` deve superare la guardia del runner **ed** essere esattamente un TEST certificato. Senza `--apply` si limita a contare.

## 4. Indici

| Indice | Uso |
|---|---|
| GiST del vincolo EXCLUDE `(assigned_user_id, blocked_range)` parziale | conflitti e disponibilità |
| `uq_appointments_source_record` | idempotenza import |
| `uq_appointments_rescheduled_from`, `uq_appointments_stima_inspection`, `uq_appointments_google_event` | unicità dei collegamenti |
| `idx_appointments_agency_start (agency_id, start_at)` | calendario d'agenzia |
| `idx_appointments_agent_start (assigned_user_id, start_at)` parziale | calendario per agente |
| `idx_appointments_requested (agency_id, created_at) WHERE status='requested'` | coda da smistare |
| `idx_appointments_stima` / `_contact` / `_lead` / `_property` parziali | schede collegate |
| `idx_appointment_events_appointment`, `idx_appointment_events_agency` | storico |

## 5. Protezione anti-overlap: tre livelli

1. **Database:** il vincolo EXCLUDE. Due INSERT grezze in concorrenza: la seconda aspetta la prima e fallisce con `23P01` (test 71).
2. **Lock per agente:** `pg_advisory_xact_lock(hashtextextended('appointments:agent:<id>',0))`, preso in ordine crescente di id. Una seconda prenotazione dello stesso agente aspetta davvero (test 70, verificato con `pg_stat_activity` e non con uno `sleep`), poi vede la prima e riceve `AppointmentConflict` con l'elenco dei conflitti.
3. **Controllo nel service:** `find_conflicts` usa la stessa regola del vincolo, calcolata dal database. Se viene saltato, la violazione del vincolo viene comunque tradotta in `AppointmentConflict` (test 72).

**Ordine dei lock**, unico per tutti i percorsi: prima le righe `appointments` (`FOR UPDATE`), poi gli agenti in ordine crescente.

**Service A30-1**
- Funzioni: `create_appointment`, `reschedule_appointment` (la riga vecchia diventa `rescheduled`, la nuova ha `rescheduled_from_id`, tutto in una transazione) e `find_conflicts` in sola lettura.
- Chi fa cosa: un `agent` scrive e sposta solo nella propria agenda; owner, admin e platform admin possono assegnare ad altri (matrice P26-1).
- Stima: una stima di un'altra agenzia ha la stessa risposta di una stima inesistente (404).

## 6. Proiezione verso `stima_inspections` — DECISA, da implementare in A30-2

**Decisioni:**
- **Q1:** in `acquisition/repository.py` si aggiungono varianti che lavorano sul cursore ricevuto, senza commit interno.
- **Q2:** le rotte LMC-15 esistenti restano compatibili ma diventano una facciata verso il service dell'Agenda. **Un solo scrittore logico:** Agenda → `appointments` → proiezione su `stima_inspections`.
- **Q6:** gli stati terminali sono completed, cancelled, no_show e rescheduled; la macchina a stati si implementa in A30-2.

Finché la proiezione non c'è, il service **rifiuta** di fissare o spostare un `inspection` legato a una stima; è ammesso solo come `requested`.

| Evento appointment (type=inspection, stima_id ≠ NULL) | Effetto su `stima_inspections` (stessa transazione) |
|---|---|
| resta `requested` | nessuno |
| diventa scheduled/confirmed per la prima volta | INSERT `scheduled`, `scheduled_for = start_at`, `created_by` = attore; evento timeline `inspection_scheduled` con la chiave idempotente LMC-15; `appointments.stima_inspection_id` valorizzato |
| rescheduled (nuova riga) | UPDATE `scheduled_for` sulla stessa riga LMC-15; il collegamento passa alla riga nuova |
| completed | `complete_inspection` con `completed_at` = momento reale, dichiarato dall'operatore oppure NOW() (**Q3**, mai `start_at`) |
| cancelled | `cancel_inspection` con ragione obbligatoria (LMC-15 la richiede) |
| no_show | `cancel_inspection` con ragione `no_show`: il sopralluogo non è avvenuto e la journey deve continuare |

## 7. Backfill di `stima_inspections` — DECISO, fase dedicata

- `source='stima_inspections_backfill'`, `source_record_id = stima_inspections.id`, `stima_inspection_id` valorizzato; `INSERT … ON CONFLICT DO NOTHING`, quindi rieseguibile.
- `scheduled` → `scheduled`: `start_at = scheduled_for`, `end_at = start_at + durata standard` (sopralluogo 60').
- `completed` → `completed` (se `scheduled_for` è NULL, `start_at = completed_at`).
- `cancelled` → `cancelled`.
- **Q4:** l'agente **non** si ricava da `created_by_operator_user_id`. I record chiusi entrano senza agente, come lo schema ora consente.
- **Nota:** un `scheduled` storico senza agente non è rappresentabile come `scheduled`. La fase di backfill dovrà dire come trattarlo (per esempio come `requested`, da smistare). Lo segnalo, non lo decido.
- Le sovrapposizioni trovate vengono **riportate**, non forzate.

## 8. Collisione dichiarata: file di test e d'inventario aggiornati

Nessun file applicativo certificato è stato modificato. Sono stati aggiornati **24 file tracciati**, tutti di test o d'inventario, elencati in `tests/a30_1_diff.py`. Lo schema è lo stesso delle fasi precedenti: si nomina la 072 invece di smettere di guardare.

- **Sentinelle "nessuna migration dopo la 071" / "la coda è la 071" (21 file):**
  - LMC: `test_lmc15_acquisition_bridge`, `test_lmc13_home_metrics`, `test_lmc11_valuation_cron`, `test_lmc7_owner_radar`, `test_lmc3_valuation_snapshot`, `test_lmc2_owner_homes`, `test_lmc8_crm_radar`, `test_lmc9_consultation_request`, `test_lmc1b_owner_login_link`
  - P27/P29: `test_p27_6_lead_routing`, `test_p29_1_consent_migrations`, `test_p29_2_1_communication_foundation`, `test_p29_2_2_communication_service`, `test_p29_2_3_claim_sentinels`, `test_p29_2_4_dispatch_sentinels`, `test_p29_2_5e_email_adapter`
  - journey: `test_p29_3_journey_foundation`, `test_p29_3c_orchestrator`, `test_p29_3d_crm_journey`, `test_p29_3e_cron_integration`, `test_p29_3g_final_fixes`
  - Negli ultimi cinque i controlli del working tree aggiungono `tests/a30_1_diff.py` all'unione dei file dichiarati.
- **Inventario FK del cleanup della certificazione live P26-6** (`test_p26_6_live_cert_script.py`, test 86i): le sei nuove FK non-CASCADE sono esaminate e motivate. **Conseguenza dichiarata:** un'agenzia di prova che contiene appuntamenti non si può cancellare, perché la DELETE è rifiutata. Decisione Q7: le righe di prova usano `a30_test` + `test_run_id` e vanno tolte con `a30_test_purge` **prima** del cleanup delle agenzie dedicate.
- **Registro dei punti di connessione P26** (`test_p26_db_entrypoints.py` h11 + `docs/P26_DB_ENTRYPOINTS.md`): il nuovo test PostgreSQL è registrato con la sua giustificazione.

**Fallimento preesistente, non causato da A30-1:** `test_p29_3g_final_fixes.py::test_20` fallisce anche su HEAD pulito. Pretende che i file di P29-3G risultino *modificati* nel working tree, ma sono già committati. Non l'ho corretto.

## 9. `property_visits` (Q-A3, Q5)

Resta la fonte autorevole delle visite acquirente e non viene migrata. **Q5:**
- l'operatore **non** si ricava dal testo di `assigned_to`;
- l'integrazione completa è rinviata, con una futura colonna `assigned_operator_user_id` nullable su `property_visits`, in una fase MATCH dedicata. A30-1 non tocca MATCH;
- la durata standard di una visita acquirente è **60 minuti**, configurabile (`appointments/enums.py: DEFAULT_DURATION_MINUTES`; la configurazione per agenzia arriva con A30-11).

In A30-1 le visite non entrano nel controllo conflitti. Il lock per agente copre già la loro futura inclusione.

## 10. Rollback

- La down `072_a30_1_appointments_down.sql` **rifiuta se `appointments` ha righe** (i dati di prova si tolgono prima con `a30_test_purge`). Con tabelle vuote rimuove tabelle, trigger, le cinque funzioni (`appointments_guard`, `appointments_refuse_delete`, `appointment_events_append_only`, `a30_test_purge`, `a30_is_certified_test_database`) e la riga del ledger.
- **Non** rimuove `btree_gist` e **non** tocca la 070.
- Il codice non è montato: non c'è alcun effetto runtime da annullare.

## 11. Runbook TEST (lo esegue Giorgio, dopo l'approvazione del gate)

Dalla shell del servizio Render **TEST** (`DB_NAME=stima360_db_test`), con il codice A30-1 presente:

```bash
python scripts/p26_migrate.py status --operator "giorgio.larasa"
python scripts/p26_migrate.py plan   --operator "giorgio.larasa"   # deve elencare SOLO 072_a30_1_appointments
```

Pre-check SQL, in sola lettura:

```sql
SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name = 'btree_gist';
SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 3;   -- 070 e 071 presenti
```

Poi:

```bash
python scripts/p26_migrate.py apply --operator "giorgio.larasa"
```

Post-check:

```sql
SELECT extname FROM pg_extension WHERE extname = 'btree_gist';
SELECT conname FROM pg_constraint WHERE conname = 'appointments_no_overlap_excl';
SELECT count(*) FROM appointments;          -- 0
SELECT version, applied_at FROM schema_migrations WHERE version = '072_a30_1_appointments';
```

Se `CREATE EXTENSION` viene rifiutato per permessi, la migration si annulla per intero e non cambia niente: STOP e segnalare.
**PROD: nessuna azione.**

## 12. Decisioni del REVIEW GATE A30-1 e dove sono recepite

| # | Decisione | Dove |
|---|---|---|
| Q1 | Varianti di `acquisition/repository.py` che lavorano sul cursore, senza commit interno | A30-2 (progetto §6) |
| Q2 | Rotte LMC-15 come facciata dell'Agenda: un solo scrittore logico | A30-2 (progetto §6) |
| Q3 | `completed_at` = momento reale, dichiarato oppure NOW(), mai `start_at` | 072 (commento di colonna) + §6 |
| Q4 | Agente mai inferito; NULL per requested e per i record storici chiusi; obbligatorio per scheduled e confirmed | **072: `appointments_agent_required_chk` corretto** |
| Q5 | Nessuna inferenza da `assigned_to`; futura `assigned_operator_user_id`; visita acquirente 60' configurabile | §9 + `enums.DEFAULT_DURATION_MINUTES` |
| Q6 | Macchina a stati in A30-2; terminali completed, cancelled, no_show, rescheduled | A30-2 |
| Q7 | Nessuna DELETE runtime; dati di prova `a30_test` + `test_run_id`; purge protetta **in allowlist TEST** (fallisce chiuso fuori da `stima360_db_test`) | **072** + `scripts/a30_test_cleanup.py` |
