# A30-2P — Proiezione LMC-15, backfill, facade (piano operativo)

Branch `core-0.1-test`. Parte da A30-2 certificata. Stato di questo documento: **backfill certificato su TEST, proiezione approvata**. Il backfill è stato eseguito e certificato su `stima360_db_test`; `PROJECTION_ENABLED = True` è approvato (gate A30-2P PROJECTION chiuso). La facade è in fase di design (Project: `roadmap/A30-2P_FACADE_DESIGN.md`) e il router non è montato.

## Sequenza e gate

| Passo | Contenuto | Stato |
|---|---|---|
| 1 | Backfill `stima_inspections` → `appointments` (codice, test, census read-only) | **fatto, approvato** |
| 2 | Census su `stima360_db_test` (SQL read-only, lo esegue Giorgio) | **completato e certificato** |
| 3 | Backfill `--dry-run`, poi `--apply` su `stima360_db_test`, poi census e certificazione | **completato e certificato**: dry-run OK; apply `committed=true`; 1 riga importata (`source=stima_inspections_backfill`, stato Agenda `requested`); post-census `gia_collegati=1`, `gia_backfill=1`, `da_importare=0`, Q7 e Q7b tutti 0, Q8=0, Q9=0/0. Da non rieseguire senza nuova approvazione. |
| 4 | `PROJECTION_ENABLED = True` (senza mount non cambia nulla nelle rotte reali) | **approvato, gate chiuso** |
| 5 | Facade LMC-15 → Agenda (stesso contratto HTTP), con la migration 073 se approvata | **design in review** (Project: `roadmap/A30-2P_FACADE_DESIGN.md`) |
| 6 | Mount del router in `main.py` | fase successiva, fuori da A30-2P |

## Backfill: regole (implementate in `appointments/backfill.py`)

- **Chiave d'idempotenza:** `source='stima_inspections_backfill'`, `source_record_id='stima_inspections:<id>'`. Più corse non producono doppioni. Lo garantiscono:
  - l'indice unico `(source, source_record_id)` della 072;
  - l'indice unico su `stima_inspection_id`;
  - un `pg_advisory_xact_lock` che mette in fila due corse concorrenti.
- **Candidati:** righe con `stima_id` la cui stima ha un'agenzia, non già collegate da un appuntamento e non già importate.
- **Orfani:** una riga con `stima_id` NULL (stima cancellata) non ha un'agenzia deducibile. Viene esclusa e contata.
- **Mapping:**

  | LMC-15 | Agenda | Note |
  |---|---|---|
  | `scheduled` | `requested` | la 072 esige l'agente per `scheduled` e l'agente non si inferisce (Q4) |
  | `completed` | `completed` | `completed_at` copiato identico; agente NULL (ammesso per la fonte storica) |
  | `cancelled` | `cancelled` | `cancelled_at` e `cancelled_reason` copiati identici |

- **Orari:** `start_at` = `scheduled_for`. Per un completato registrato a posteriori (`scheduled_for` NULL) `start_at` = `completed_at`, dichiarato nel report come `start_reconstructed`. `end_at` = `start_at` + 60 minuti, la durata di default del sopralluogo.
- **Campi non ricostruiti:** `created_by_user_id` NULL (import di sistema); `contact_id`, `lead_id` e `property_id` NULL, cioè non inferiti; buffer a 0.
- **Audit:** un evento `created` per riga, con attore NULL.
- **Scritture:** `stima_inspections` e `seller_timeline_events` restano identiche (provato byte per byte). Il backfill non tocca `stime_dettagliate`, `property_visits` o Google.
- **Script `scripts/a30_2p_backfill.py`:**
  - modi `--census` (predefinito, sola lettura), `--dry-run` (esegue e fa sempre rollback) e `--apply --confirm-database <nome>`;
  - allowlist: `stima360_db_test` sia per nome sia nel database; PROD rifiutata anche per il census.

## Proiezione (accesa; `False` resta come arresto d'emergenza)

- Una richiesta importata è **già collegata** alla sua riga LMC-15. Quando qualcuno la fissa, con agente e orario, `projection.on_schedule` **riusa quella riga** con `reschedule_inspection_in`, invece di crearne una seconda.
- Se LMC-15 l'ha già chiusa, la risposta è `PROJECTION_CONFLICT` e non viene scritto nulla.
- Annullare una richiesta importata chiude la riga LMC-15 (serve il motivo).
- Guardie: `on_complete` e `on_cancel` (e quindi `on_no_show`) senza `stima_inspection_id` sollevano `PROJECTION_CONFLICT` e non chiamano LMC-15.
- Con l'interruttore a `False` (arresto d'emergenza) ogni azione che toccherebbe `stima_inspections` risponde `INSPECTION_PROJECTION_NOT_ACTIVE`.

## Deriva fino alla facade

Finché la facade non è attiva, LMC-15 resta lo scrittore reale: l'Agenda non è montata. Una riga già importata può quindi essere chiusa o spostata da LMC-15. Il census lo misura (`deriva_chiuse_in_lmc15`, `deriva_spostate_in_lmc15`), il backfill non la corregge.

Prima di accendere la facade:

1. rieseguire il backfill, che è idempotente e porta dentro le righe nuove;
2. allineare le richieste importate ancora intatte (`version = 1`), con una regola da approvare a quel gate.

## Facade LMC-15 (solo piano)

Il contratto HTTP di `/api/acquisition/...` resta identico: stessi corpi, stesse risposte (`INSPECTION_COLUMNS` della riga proiettata), stessi eventi di timeline, prodotti dalle stesse funzioni `*_in`. Punti che richiedono una decisione prima di scriverla:

1. **Creazione senza agente.** Il contratto LMC-15 non ha un agente. La facade crea una `requested` e proietta subito la riga `scheduled`: oggi una `requested` si proietta solo quando la si fissa.
2. **Completato senza agente.** Riguarda `inspections/completed` e `complete` su una richiesta. La 072 lo ammette solo per le fonti storiche, quindi serve una **migration 073** additiva: per esempio, ammettere l'agente NULL su completed e no_show quando la riga è un `inspection` collegato a `stima_inspections`.
3. **Regole di completamento più permissive in LMC-15.** LMC-15 accetta `completed_at` prima di `scheduled_for` e prima dell'inizio. L'Agenda (D11) no. Per preservare il contratto serve un percorso facade che conservi la semantica LMC-15.
4. **Motivo di annullo.** È facoltativo in LMC-15 e obbligatorio nell'Agenda per i sopralluoghi con stima. Anche qui va preservato il contratto.
5. LMC-15 non ha una rotta di spostamento: nessuna divergenza.
