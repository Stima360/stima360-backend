# STIMA360 CRM — ADM-6: Ruolo di piattaforma e sostituzione del Super Admin

- Branch: `core-0.1-test`, base pubblicata `d843f94` (ADM-5B).
- Nessuna migration, nessuna interfaccia (ADM-7).
- Nessun commit, push o deploy; Render, database TEST e produzione non toccati.
- Prove solo su PostgreSQL 16 locale e usa-e-getta.

## 1. Cosa è stato realizzato

**`PATCH /api/platform/operators/{id}/platform-admin`**, con corpo `{"is_platform_admin": true | false}`.

- Una rotta dedicata (D1): la modifica ordinaria dell'operatore continua a non conoscere il flag.
- Valore booleano stretto: `"true"` o `1` danno 422.
- **Solo il Super Admin designato:** `require_super_admin_context` sulla rotta, e `lock_super_admin` nel servizio con il ruolo riletto e la riga bloccata. Gli altri amministratori ricevono 403, gli operatori 403, chi non è autenticato 401.

**Guardie**, nella stessa transazione e serializzate come ogni modifica della Rete:

| Operazione | Regola | Esito se violata |
|---|---|---|
| Concedere | account `active` | 409 |
| Concedere | almeno un login riuscito, cioè `last_login_at` presente (D2) | 409 |
| Togliere | mai al Super Admin designato | 409 |
| Togliere | mai l'ultimo amministratore di piattaforma attivo | 409 |
| Entrambe | un flag già nello stato richiesto non è un'operazione | 409 |

**Altri effetti**

- **Revoca delle sessioni:** togliere il flag revoca tutte le sessioni dell'operatore nella stessa transazione.
- **Membership invariata (D3):** l'appartenenza all'agenzia, il ruolo e lo stato non vengono toccati.
- **Registro:** nuova azione `platform.operator.platform_admin.update`, con metadati `{"changed_fields": ["is_platform_admin"], "platform_admin": {"from": …, "to": …}}`. Passa dalla stessa `audit_then_commit` delle altre modifiche: se il registro non è scrivibile, l'operazione non avviene.
- **Designazione (D4):** resta in `STIMA360_SUPER_ADMIN_USER_ID`. Nessuna configurazione Render è stata cambiata.

**Ultimo amministratore attivo.** Finché il designato è attivo, e la piattaforma non permette di disabilitarlo né di togliergli il flag, questa guardia non si raggiunge per via ordinaria. Resta come difesa in profondità, provata simulando il conteggio.

## 2. Procedura di sostituzione del Super Admin (da A a B)

1. **Preparazione.** B ha un account attivo e ha fatto **almeno un login riuscito**.
2. **Promozione.** A (designato) chiama `PATCH /api/platform/operators/{id_B}/platform-admin` con `{"is_platform_admin": true}`. Il registro scrive l'azione con autore A.
3. **Designazione.** Il proprietario imposta `STIMA360_SUPER_ADMIN_USER_ID = id_B` sui servizi che espongono la piattaforma, cioè il backend, e ridistribuisce. Durante il passaggio A mantiene il flag, quindi la piattaforma non resta mai senza amministratore.
4. **Verifica.** B accede e controlla:
   - `GET /api/platform/me` risponde `is_super_admin: true`;
   - `GET /api/platform/audit` risponde 200;
   - A riceve ora 403 sulle operazioni riservate.
5. **Revoca di A (facoltativa).** B chiama la stessa rotta per A con `{"is_platform_admin": false}`. Le sessioni di A vengono revocate.
6. **Storico.** Nessun dato di A viene toccato: le sue righe nel registro e la sua identità di autore restano.

Se al passo 3 la variabile è assente o non valida, **tutte** le operazioni riservate vengono negate (fail-closed). Nessuno ottiene i privilegi per errore: si corregge la variabile e si ridistribuisce.

## 3. Recupero d'emergenza

Si usa quando il designato non è più utilizzabile: account perso, disabilitato fuori dalla piattaforma, credenziali smarrite. **Non esiste e non va creata una scorciatoia applicativa.** Il recupero è manuale, autorizzato dal proprietario, e lascia traccia:

1. **Scegli l'account sostitutivo** B: un operatore esistente, attivo, che ha già fatto login.
2. **Abilita B, se non lo è già.** Con un accesso SQL autorizzato al database del servizio interessato, solo per B:
   - `is_platform_admin = TRUE`;
   - `status = 'active'`.

   Nessun'altra riga va modificata.
3. **Registra l'intervento.** Nella stessa sessione SQL inserisci una riga in `platform_audit_log`:
   - `action = 'platform.operator.platform_admin.update'`;
   - `actor_label` con chi è intervenuto;
   - `target_type = 'operator'` e `target_id = id_B`;
   - `metadata` con `{"platform_admin": {"from": false, "to": true}, "emergency": true}`.
4. **Designa B.** Imposta `STIMA360_SUPER_ADMIN_USER_ID = id_B` e ridistribuisci.
5. **Verifica.** Esegui il passo 4 della procedura ordinaria.
6. **Revoca l'account precedente,** se serve, dalla piattaforma come nel passo 5.

Mai cancellare l'account precedente: il suo id resta nel registro e come autore storico (ADM-4).

## 4. File modificati

| File | Modifica |
|---|---|
| `platform_admin/enums.py` | azione `platform.operator.platform_admin.update`, chiave dei metadati, messaggi delle guardie |
| `platform_admin/operators_repository.py` | `set_platform_admin` (l'unico punto che scrive il flag), `count_active_platform_admins` |
| `platform_admin/operators_service.py` | `set_platform_admin`: guardie, revoca sessioni, registro |
| `platform_admin/schemas.py` | `PlatformAdminUpdateRequest` (`StrictBool`) |
| `platform_admin/router.py` | la rotta |
| `tests/test_adm6_platform_admin_postgres.py` (nuovo) | collaudo |
| `tests/test_adm1_super_admin_guard.py`, `tests/test_p27_6_lead_routing.py` | sentinelle aggiornate in modo dichiarato |

## 5. Test e risultati

**Collaudo:** `tests/test_adm6_platform_admin_postgres.py`, **4 superati**.

| Test | Verifica |
|---|---|
| `test_01` | permessi: altro amministratore 403, agente 403, anonimo 401; valore non booleano 422; nessuna modifica |
| `test_02` | promozione rifiutata senza login, su account disabilitato e se già amministratore; promozione riuscita dopo il login; membership invariata; registro con autore e `from: false, to: true` |
| `test_03` | revoca rifiutata sul designato e sull'ultimo amministratore attivo (simulato), con flag e sessione invariati; revoca riuscita: sessione revocata, membership invariata, registro con `from: true, to: false` |
| `test_04` | sostituzione completa da A a B con il cambio di `STIMA360_SUPER_ADMIN_USER_ID` nel test: B verifica (`is_super_admin`, registro), A perde l'accesso riservato, B toglie il ruolo ad A e ne revoca le sessioni, lo storico di A resta, B designato non si toglie il ruolo |

**Regressione pertinente**

- **PostgreSQL** ADM-1, ADM-2, ADM-3 (con browser), ADM-4, ADM-5A, ADM-5B e ADM-6 insieme: **46 superati**.
- **P28 su PostgreSQL reale:** **22 superati**.
- **Unitari** di piattaforma, P28 e sentinelle `git status`, su un clone con le modifiche committate: **nessun nuovo fallimento**. I 41 fallimenti e i 10 errori sono tutti nella baseline: test UI in Node, `test_i1_059`, `p27_7_network_e2e`.
- `git diff --check`: pulito.

**Sentinelle aggiornate in modo dichiarato**

- `test_adm1_super_admin_guard`: le rotte che richiedono il designato passano da 28 a **29**.
- `test_p27_6_lead_routing::test_i2`: la superficie di piattaforma include la nuova rotta.
- `test_p27_6_lead_routing::test_i4`: `set_platform_admin` è tra le modifiche che passano da `audit_then_commit`.

## 6. Limiti

- **Cambio della designazione:** resta un'azione di configurazione del proprietario su Render.
- **Recupero d'emergenza:** è manuale per scelta (§3).
- **Interfaccia per concedere o togliere il ruolo:** ADM-7.

**STOP.** Nessun commit, push o deploy. Attendo il prossimo via libera.
