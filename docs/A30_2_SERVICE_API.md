# A30-2 — Agenda: service, state machine, API (NON montata)

Fase: A30-2 · Branch: `core-0.1-test` · Riferimento: `roadmap/A30-2_IMPLEMENTATION_PLAN.md` rev. 2
Dipende da: A30-1 (migration 072, certificata su TEST `stima360_db_test`).

## Perimetro

- Router `appointments/router.py` **preparato e non montato** (D1): `main.py` non nomina `appointments`; il router si prova su un'app FastAPI isolata nei test.
- **Proiezione LMC-15** (`projection.PROJECTION_ENABLED`, D3): spenta in A30-2, **accesa in A30-2P**. Un sopralluogo legato a una stima si fissa, si sposta e si chiude dall'Agenda, e `stima_inspections` è scritta nella stessa transazione. A interruttore spento (arresto d'emergenza) quelle azioni rispondono `INSPECTION_PROJECTION_NOT_ACTIVE`. La facade LMC-15 → Agenda non esiste ancora.
- Nessuna lettura runtime di `stime_dettagliate` (D10), nessun uso di `property_visits`, nessun Google Calendar, nessuna nuova migration.

## Moduli

| Modulo | Ruolo |
|---|---|
| `errors.py` | Eccezioni con `code` di contratto (sotto le classi `core.exceptions`) |
| `state_machine.py` | Transizioni pure, guardie di tempo (D11), `completed_at` reale (Q3), `allowed_actions` |
| `availability.py` | Slot e alternative pure; allineamento in Europe/Rome, passo in UTC (DST-safe); nessun orario di lavoro (D7) |
| `schemas.py` | Corpi di richiesta (`extra="forbid"`, nessun campo attore/agenzia/source) |
| `repository.py` | SQL: insert idempotente, update con evento di audit, letture calendario/dettaglio |
| `service.py` | Regole: visibilità per ruolo, tre livelli anti-sovrapposizione, lock, versioni |
| `projection.py` | Mapping verso LMC-15 sul cursore dell'Agenda; acceso da A30-2P |
| `router.py` | `/api/appointments`, `require_operator` su ogni rotta; errori `{"detail","code",...}` |

## State machine

| Azione | Da | A | Note |
|---|---|---|---|
| schedule | requested | scheduled | agente esplicito obbligatorio (D2) |
| confirm | scheduled, confirmed | confirmed | idempotente su confirmed |
| reschedule | scheduled, confirmed | vecchia → rescheduled, **nuova riga** | `rescheduled_from_id`, 201 |
| reassign | requested, scheduled, confirmed | invariato | stessa riga, solo owner/admin, check disponibilità, evento (D5) |
| cancel | requested, scheduled, confirmed | cancelled | ragione obbligatoria se proiettabile |
| complete | scheduled, confirmed | completed | solo da `start_at` (D11); `completed_at` dichiarato (conservato esatto, ≤ NOW() del DB) o NOW() del DB, mai `start_at` (Q3) |
| no_show | scheduled, confirmed | no_show | solo da `end_at` (D11) |
| patch | requested, scheduled, confirmed | invariato | solo note, luogo, contatto, lead, immobile |

Terminali: completed, cancelled, no_show, rescheduled. Ogni scrittura su riga esistente richiede `version` (409 `VERSION_CONFLICT` con `current_version`).

## Concorrenza e ordine dei lock

Riga `appointments` (`FOR UPDATE`) → lock consultivi per agente in ordine crescente → stima → riga `stima_inspections` (solo a proiezione accesa). Tre livelli: vincolo EXCLUDE (072), `pg_advisory_xact_lock` per agente, controllo del service con alternative. Una violazione del vincolo arrivata comunque si traduce in `APPOINTMENT_CONFLICT`.

## Idempotenza

`POST /api/appointments` richiede `client_request_id` (UUID v4), salvato come `source_record_id` con `source='crm_manual'` sull'indice unico esistente di 072. Replica identica → 200 + `Idempotent-Replay: true`; stessa chiave con contenuto diverso → 409 `IDEMPOTENCY_KEY_REUSED`.

## Visibilità (D4, D6)

- owner/admin: tutto l'agenda dell'agenzia, triage delle richieste.
- agent: i propri appuntamenti e le richieste assegnate a lui; dei colleghi vede solo blocchi "Occupato" (orario + nome agente). Un appuntamento non visibile risponde 404.
- platform admin senza agenzia scelta: 403 `PLATFORM_ADMIN_AGENCY_REQUIRED`. Senza sessione: 403 `SESSION_REQUIRED`.

## Rotte

| Metodo | Percorso | Esito |
|---|---|---|
| GET | `/api/appointments/calendar?from&to&agents&types&statuses&show_colleagues` | finestra ≤ 42 giorni |
| GET | `/api/appointments/agents` | agenti attivi |
| GET | `/api/appointments/availability?user_id&from&to&duration&step&buffer_*` | slot (passo 15/30/60, finestra ≤ 7 giorni) |
| POST | `/api/appointments/availability/check` | libero/occupato + alternative |
| GET | `/api/appointments` | elenco filtrato |
| GET | `/api/appointments/{id}` | dettaglio, `allowed_actions`, collegamenti |
| GET | `/api/appointments/{id}/events` | registro di audit |
| POST | `/api/appointments` | 201 / 200 replica |
| PATCH | `/api/appointments/{id}` | campi descrittivi |
| POST | `/api/appointments/{id}/schedule`, `confirm`, `reschedule` (201), `reassign`, `cancel`, `complete`, `no-show` | transizioni |

## Codici di errore

`APPOINTMENT_CONFLICT` (409, con `conflicts` e `alternatives`), `INVALID_TRANSITION`, `VERSION_CONFLICT`, `IDEMPOTENCY_KEY_REUSED`, `PROJECTION_CONFLICT` (409); `AGENT_REQUIRED`, `AGENT_NOT_ACTIVE`, `REASON_REQUIRED`, `COMPLETED_AT_INVALID`, `TIMEZONE_REQUIRED`, `RANGE_TOO_LARGE`, `LINK_MISMATCH`, `INSPECTION_PROJECTION_NOT_ACTIVE`, `VALIDATION_ERROR` (422); `NOT_FOUND` (404); `FORBIDDEN_ROLE`, `SESSION_REQUIRED`, `PLATFORM_ADMIN_AGENCY_REQUIRED` (403).

## Collisione dichiarata: `acquisition/repository.py` (LMC-15)

Q1: aggiunte le varianti sul cursore `create_inspection_in`, `complete_inspection_in`, `cancel_inspection_in`, `reschedule_inspection_in` (nuova: UPDATE di `scheduled_for` solo se `scheduled`) e `_chiudi_sopralluogo_in`, senza commit interno. Le funzioni pubbliche esistenti mantengono firma e comportamento (wrapper su `core_cursor(commit=True)`); `acquisition/service.py` e `acquisition/router.py` non sono toccati. Usate solo da `projection.py` (accesa da A30-2P).

`completed_at` (correzione A del gate A30-2): se assente vale il `NOW()` del database letto nella stessa transazione della mutazione e della proiezione; se dichiarato si conserva esattamente, purché `start_at <= completed_at <= db_now`, altrimenti `COMPLETED_AT_INVALID`. Nessuna tolleranza di orologio, nessuna correzione silenziosa. Anche la guardia "si completa solo da `start_at`" usa quel `NOW()`. LMC-15 registra `completed_recorded_at` con lo stesso `NOW()`, quindi `completed_recorded_at >= completed_at` vale per costruzione.

`cancelled_at` e `no_show_at` (A30-2P F9): sono il `NOW()` del database della stessa transazione, cioè lo stesso istante che LMC-15 registra come `cancelled_at` della riga proiettata (Q10 = 0). Anche la guardia dell'assenza (solo da `end_at`) usa lo stesso `db_now`: un solo orologio, quindi `no_show_at >= end_at` per costruzione. Le regole restano invariate: motivo obbligatorio per un sopralluogo con stima, assenza solo da `end_at`, permessi, versione, state machine.

## Test

- `tests/test_a30_2_appointments.py`: state machine, disponibilità (DST 29/03 e 25/10), schemi, perimetro statico.
- `tests/test_a30_2_appointments_postgres.py` (opt-in `P29_TEST_DSN`, DB usa-e-getta; orologio ancorato al `NOW()` del PostgreSQL di test, nessuna data fissa): creazione idempotente, transizioni via HTTP, visibilità, disponibilità, sessione/platform admin, proiezione accesa e — solo dentro il test — spenta (arresto d'emergenza), con rollback totale su errore.
