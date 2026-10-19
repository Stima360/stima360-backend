# STIMA360 CRM — ADM-5B: Consultazione delle agenzie non attive

- Branch: `core-0.1-test`, base pubblicata `9e1cb2a` (ADM-5A).
- Nessuna migration, nessun commit, push o deploy; Render, database TEST e produzione non toccati.
- Email, follow-up, Cestino, cron e automazioni invariati.
- Prove solo su PostgreSQL 16 locale e usa-e-getta.

## 1. Cosa cambia

Il **Super Admin designato** può entrare in un'agenzia **sospesa o archiviata** con il normale ingresso (`POST /api/platform/agencies/{id}/enter`), **solo per consultare**:

- L'agenzia **non** viene riattivata: lo stato resta quello che era.
- Nessun dato cambia proprietario.
- Chiunque altro, compresi gli altri amministratori di piattaforma, non entra.
- Ogni stato non attivo diverso da sospesa o archiviata resta rifiutato (409).
- Per le agenzie **attive** il comportamento è quello di prima, con pieni poteri.

### Decisioni applicate

**D1 — Sicurezza**

- `operator_auth/designation.py` (nuovo) legge `STIMA360_SUPER_ADMIN_USER_ID` in un posto solo. `platform_admin/super_admin.py` la riusa: nessuna dipendenza circolare, perché `platform_admin` dipende da `operator_auth` e non il contrario.
- L'ingresso resta protetto da `lock_super_admin`: identità designata più ruolo riletto nel database.
- **A ogni richiesta** la risoluzione della sessione (`_acting_or_cleared`) ricontrolla il designato. Se la configurazione cambia o l'utente non è più il designato, l'acting su un'agenzia non attiva viene cancellato alla richiesta successiva.

**D2 — Interfaccia**

- La barra della Shell scrive «SOLA LETTURA - Stai consultando: …», con `textContent` e senza nuovo HTML dinamico.
- Il dato arriva da `/me`, che ora riporta `acting.read_only`.
- Nessun'altra modifica all'interfaccia: i comandi restano visibili e il backend li rifiuta.

**D3 — Documenti**

- Visualizzazione e download sono `GET` e restano consentiti.
- Il download continua a scrivere la sua riga tecnica in `owner_audit_log`, invariata.
- Modifica ed eliminazione dei documenti sono rifiutate come ogni altra scrittura.

**D4 — Protezione**

- **Contesto:** `OperatorContext.read_only` (nuovo campo, default `False`). Vale `True` solo durante la consultazione.
- **Blocco centrale:** `refuse_writes_in_read_only`, chiamato da `optional_session`, da cui passano tutte le dipendenze delle API delle agenzie (`require_operator`, `require_authenticated_operator`, `legacy_basic_agency_context`, `require_owner_admin_context`). Con `read_only`, `POST`, `PUT`, `PATCH` e `DELETE` ricevono 403 «Consultazione in sola lettura…».
  - Restano fuori dal blocco `/api/platform/*` (uscita, registro, operazioni del Super Admin con le loro guardie) e `/api/operator-auth/*` (logout).
  - **STIMA Voice** scrive solo con `POST`, quindi è bloccata. I suoi `GET` sono di sola lettura.
- **Cruscotto abbinamenti:** `GET /api/match/dashboard` e `/dashboard/stale` in sola lettura non eseguono il ricalcolo che scrive su `matches`, e mostrano lo stato già registrato.
- **Google Calendar:** `GET /api/calendar/google/callback` in sola lettura risponde 403 prima di leggere qualunque parametro o contattare Google.
- **Nessuna transazione `READ ONLY` imposta dal database**, come deciso.

**Uscita sicura.** `POST /api/platform/agency-context/exit` funziona qualunque sia lo stato dell'agenzia ed è registrata nel registro. Un cambio di stato dell'agenzia continua a chiudere l'acting di tutti, come in ADM-1.

## 2. File modificati

| File | Modifica |
|---|---|
| `operator_auth/designation.py` (nuovo) | lettura condivisa del Super Admin designato |
| `platform_admin/super_admin.py` | riusa `designation` invece della propria copia |
| `operator_auth/context.py` | campo `read_only` |
| `operator_auth/enums.py` | `AGENCY_STATUSES_READ_ONLY = ("suspended", "archived")` |
| `operator_auth/service.py` | acting mantenuto in sola lettura per il solo designato |
| `operator_auth/dependencies.py` | blocco centrale delle scritture; `acting_read_only` nella sessione |
| `operator_auth/schemas.py`, `operator_auth/router.py` | `acting.read_only` in `/me` |
| `platform_admin/acting_service.py`, `platform_admin/schemas.py` | ingresso in sola lettura su agenzia sospesa o archiviata; `read_only` nella risposta e nei metadati dell'ingresso |
| `match/repository.py` | nessun ricalcolo nei due cruscotti in sola lettura |
| `calendar_sync/router.py` | callback rifiutato in sola lettura |
| `static/os_shell/assets/main.js` | «SOLA LETTURA» nella barra |
| `tests/test_adm5b_read_only_consultation_postgres.py` (nuovo) | collaudo |
| Test esistenti adeguati alla nuova regola | vedi §3 |

## 3. Test e risultati

**Collaudo:** `tests/test_adm5b_read_only_consultation_postgres.py`, **8 superati**. Sessioni vere e router veri montati come in `main.py`: core, immobili, abbinamenti, calendario, documenti del proprietario, Voice.

| Verifica richiesta | Test |
|---|---|
| 1. Ingresso del designato in agenzia sospesa e archiviata | `test_01[suspended]`, `test_01[archived]`: `/me` `read_only`, metadati dell'ingresso `read_only: true`, stato dell'agenzia invariato |
| 2. «SOLA LETTURA» nell'interfaccia | `test_02` (Chromium, 390 px): la barra della Shell la mostra con il nome dell'agenzia, nessun errore JavaScript |
| 3. Consultazione di immobili, contatti e documenti | `test_03`: contatti, elenco immobili, scheda immobile, documenti dell'area proprietario |
| 4. Blocco delle scritture, API dirette e Voice | `test_04`: `POST`, `PUT`, `PATCH`, `DELETE` su contatti, immobili, documenti e abbinamenti, più `POST /api/voice/commands`: tutti 403; righe di contatti, immobili, stime, lead, documenti e abbinamenti identiche; impronta di 16 tabelle invariata |
| 5. Nessuna modifica tramite GET | `test_05`: sette `GET` senza righe cambiate; il ricalcolo degli abbinamenti non parte in sola lettura e riparte su agenzia attiva; callback Google 403 |
| 6. Uscita sicura | `test_06`: uscita 204 e registrata, stato dell'agenzia invariato; cambiando il designato l'acting sparisce alla richiesta successiva |
| 7. Nessuna regressione | `test_07`: agenzia attiva con pieni poteri; titolare ordinario che scrive come prima; titolare di agenzia sospesa ancora fuori (ADM-1); un altro amministratore di piattaforma non entra (403) |

**Regressione**

- **PostgreSQL** ADM-1, ADM-2, ADM-3 (con browser), ADM-4, ADM-5A, ADM-5B, P29-3E e C1, insieme: **62 superati**.
- **PostgreSQL** di Cestino 1 e 2, approvazione ed eliminazione edifici, C2 browser, LMC-15, dispatch: **106 superati**.
- **P28 su PostgreSQL reale** (`test_p28_acting_postgres_real`, eseguita come utente `postgres` perché `initdb` non gira come root): **22 superati**.
- **Unitari pertinenti** (P26, P27, P28, autenticazione, calendario, abbinamenti, Shell): nessun nuovo fallimento rispetto alla baseline, salvo la sentinella `test_p29_3d::test_39`. Quella legge `git status` del working tree ed è verificata sul clone committato (vedi sotto).
- **Suite completa senza DB:** vedi §5.
- `node --check static/os_shell/assets/main.js`: ok. `git diff --check`: pulito.

**Test esistenti adeguati alla nuova regola, senza indebolirli**

- `test_p28_superadmin_acting.py`:
  - **c7, c7b, c7c:** la cancellazione dell'acting su agenzia non attiva resta provata per un amministratore **non designato**. Nuovi casi c7_adm5b: il designato mantiene la consultazione in sola lettura; un'agenzia attiva non è mai in sola lettura.
  - **e6, f7:** l'ingresso in agenzia sospesa o archiviata ora riesce in sola lettura (era 409), registrato e senza riattivazione.
  - **Nuovi e6b, e6c, e6d:** un non designato è rifiutato; l'agenzia attiva entra con pieni poteri; uno stato non attivo diverso resta 409.
  - **i1:** sette campi del contesto.
- `test_p26_1_scope_enforcement::test_c2`: sette campi del contesto (`read_only`).
- `test_p26_1_operator_auth::test_no_later_operator_auth_module_exists`: nuovo modulo `designation.py`, dichiarato.
- `test_adm1_super_admin_postgres::test_06`: sospensione e ingresso simultanei. Prima nessuno poteva restare dentro un'agenzia sospesa; ora può restarci **solo** il designato, **solo** in sola lettura. Un acting aperto prima della sospensione viene ancora chiuso (ADM-1). Superato 5 volte su 5.

## 4. Limiti residui

1. **GET con scritture non individuate.** Il censimento statico ha seguito tutte le rotte `GET` delle agenzie, ma non può seguire le chiamate dinamiche. Un `GET` che scrive e che è sfuggito non verrebbe bloccato, perché per decisione D4 non c'è una transazione `READ ONLY` imposta dal database.
2. **Scritture tecniche ammesse in consultazione:**
   - l'aggiornamento di `last_seen_at` della sessione del Super Admin;
   - la riga di audit del download dei documenti (D3);
   - le righe di ingresso e uscita nel registro.

   Nessuna riguarda i dati dell'agenzia.
3. **Comandi visibili nell'interfaccia:** i pulsanti di modifica restano visibili (D2) e rispondono con il messaggio di sola lettura. La revisione dell'interfaccia è prevista in ADM-7.
4. **Download reale dei documenti:** non provato fino al file, perché richiede lo storage esterno. È provata la consultazione dei documenti; il download è un `GET`, quindi non è toccato dal blocco, e il suo audit è quello esistente.

## 5. Suite completa

Eseguita senza database su un clone locale con le modifiche committate: `617 failed, 9110 passed, 2731 skipped, 63 errors`.

Confronto con l'ultima esecuzione completa precedente (`617 failed, 9102 passed, 2714 skipped, 63 errors`):

- **stessi fallimenti ed errori, uno per uno**: nessun nuovo fallimento, nessuno sparito;
- **8 superati in più**: i nuovi casi P28;
- **saltati in più**: i collaudi PostgreSQL di ADM-4, ADM-5A e ADM-5B, che senza DSN non partono;
- la sentinella `test_p29_3d::test_39`, che legge `git status`, sul clone committato passa.

**STOP.** Nessun commit, push o deploy. Attendo il prossimo via libera.
