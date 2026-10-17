# STIMA360 CRM — ADM-1: protezione del Super Admin, autorizzazioni e sessioni

- Branch: `core-0.1-test`. Base `4eebf29` (rapporto ADM-0), verificata uguale al remoto prima di iniziare.
- Perimetro: solo ADM-1. Nessuna migration, nessun accesso a `stima360_db` o a `stima360_db_test`, nessun dato reale modificato, nessun push o deploy.
- Collaudo: PostgreSQL 16 **locale e usa-e-getta** (`localhost:55432`, database creato e distrutto dai test), mai il database TEST operativo.

## 1. File modificati

**Backend**

| File | Modifica |
|---|---|
| `platform_admin/super_admin.py` | `is_designated()`; `lock_super_admin()` (identità designata + ruolo riletto con `FOR UPDATE`); `require_super_admin_db(..., for_update=False)` |
| `platform_admin/dependencies.py` | `require_super_admin_context` (guardia centralizzata delle rotte) |
| `platform_admin/enums.py` | azione `platform.super_admin.required`; messaggi: ultimo Super Admin, agenzia di sistema, archiviazione solo da sospesa |
| `platform_admin/router.py` | 23 rotte Rete su `require_super_admin_context`; `/me` espone `is_super_admin`; 409 per agenzia e operatore; classe di rotta `_PlatformRoute` (un `SuperAdminDenied` dentro la transazione diventa 403 con codice, mai 500) |
| `platform_admin/schemas.py` | `PlatformMeResponse.is_super_admin` |
| `platform_admin/agencies_repository.py`, `operators_repository.py` | `lock_agency`, `lock_operator` (`SELECT … FOR UPDATE`) |
| `platform_admin/agencies_service.py` | lock attore e agenzia; agenzia di sistema; archiviazione solo da sospesa; revoca sessioni e chiusura acting |
| `platform_admin/operators_service.py` | lock; account designato non disabilitabile; revoca sessioni su stato operatore e membership |
| `platform_admin/configuration_service.py`, `territories_service.py`, `aliases_service.py`, `acting_service.py` | `lock_super_admin` all'inizio di ogni modifica (ingresso in agenzia compreso) |
| `operator_auth/repository.py` | `revoke_operator_sessions`, `revoke_agency_member_sessions`, `clear_acting_for_agency` (riuso di `operator_sessions.revoked_at`) |
| `property/territorial_changes.py` | approvazione/rifiuto/lettura C2: identità designata oltre al ruolo nel DB |

**Interfaccia Rete** (nessun pannello nuovo)

- `static/os_shell/assets/components/network.js`: `mostraSoloSuperAdmin()` (avviso statico con `textContent`), messaggi 409 `agenzia-aggiorna` e `operatore-accesso`.
- `static/os_shell/assets/views/rete.js`, `rete-agenzia.js`, `rete-territorio.js`: secondo cancello dopo `/me`; azioni corrette per i messaggi d'errore.

**Test**

- Nuovi: `tests/test_adm1_super_admin_postgres.py` (11 scenari reali), `tests/test_adm1_super_admin_guard.py` (8, senza DB), `tests/adm1_support.py` (supporto per i test in memoria).
- Adeguati alla regola approvata:
  - `test_p27_2/3/4/5`, `test_p27_6_alias_admin`, `test_p28_superadmin_acting`: designazione del platform admin di prova;
  - `test_p27_1_platform_admission`: nuovo campo `is_super_admin`;
  - `test_c2_*_postgres`, `test_eliminazione_edificio_1_browser_postgres`, `test_lmc15_acquisition_bridge_postgres`: designazione o `is_super_admin` nei mock di `/me`;
  - `test_eliminazione_edificio_2_postgres::test_02`: senza configurazione `GET /api/platform/agencies` ora risponde 403 (prima 200), come richiesto dalla regola "solo il Super Admin designato";
  - `test_voice_4_undo_postgres.py`: sentinella dei file protetti aggiornata con l'elenco dichiarato `AGENZIE_ADM1`, come hanno fatto C1, C2 e C3.

`main.py` **non è modificato**: la sentinella Voice che lo protegge resta invariata.

## 2. Guardie implementate

1. **Ammissione alla Rete** (`require_super_admin_context`): sessione viva → `is_platform_admin` (riletto dal DB a ogni richiesta dalla sessione) → account attivo → id = `STIMA360_SUPER_ADMIN_USER_ID`. Altrimenti 403 e riga `denied` in `platform_audit_log`. Configurazione assente o non valida: nessuno passa. L'email non concede nulla.
2. **Dentro ogni transazione di modifica** (`lock_super_admin`): stessi controlli, più la riga dell'attore riletta con `FOR UPDATE` e tenuta fino al commit. Se il ruolo cambia nel frattempo: 403 `SUPER_ADMIN_REQUIRED`, nulla committato.
3. **Account designato**: non può passare a `disabled` o `invited` da nessuno, nemmeno da sé stesso (409).
4. **Agenzia di sistema** (`slug = stima360`): non si sospende e non si archivia (409). Restano possibili le altre modifiche (es. nome).
5. **Archiviazione**: solo da `suspended` (409 altrimenti).
6. **Ordine dei lock** unico: attore → agenzia → operatore → sessioni. Tutte le modifiche della Rete si serializzano sulla riga del Super Admin.

## 3. Endpoint protetti

**Riservati al Super Admin designato (guardia di rotta + lock nel servizio dove modificano):**

- Agenzie: `GET/POST /agencies`, `GET/PATCH /agencies/{id}`, `GET/PATCH /agencies/{id}/configuration`, `POST /agencies/{id}/enter`.
- Operatori: `GET /agencies/{id}/operators`, `POST /agencies/{id}/operators`, `GET/PATCH /operators/{id}`, `PATCH /agencies/{id}/operators/{uid}/membership`, `PUT /agencies/{id}/owner`.
- Territori: `GET/POST /territories`, `GET /territories/{id}`, `GET/POST /agencies/{id}/territories`, `PATCH /agencies/{id}/territories/{aid}`, `POST /territories/{id}/transfer`.
- Alias: `GET/POST /territories/{id}/aliases`, `PATCH /aliases/{id}`.

**Riservati al Super Admin designato dal proprio servizio** (contratto `{detail, code}` già esistente, conservato): richieste ed eliminazioni edifici, Cestino di piattaforma e purge, proposte C2 (`/territorial-change-requests`: elenco, dettaglio, approvazione, rifiuto). Per C2 ADM-1 ha aggiunto il controllo d'identità designata, prima assente.

**Aperti a ogni platform admin:** `GET /me` (cancello della UI, ora con `is_super_admin`) e `POST /agency-context/exit` (uscire non deve mai essere impedito).

Le rotte tenant del CRM non sono state toccate.

## 4. Revoca delle sessioni

Riuso di `operator_sessions.revoked_at`, già letto a ogni richiesta da `resolve_session`. Nessuna migration, nessun nuovo sistema di autenticazione. Tutto avviene **nella stessa transazione** della modifica, prima dell'audit e del commit.

- **Operatore**: ogni cambio di `status` (incluso il ritorno ad `active`) revoca tutte le sue sessioni. Dopo la riattivazione le vecchie sessioni restano revocate e serve un nuovo login.
- **Membership**: ogni cambio di `status` (sospesa, revocata, riattivata) revoca le sessioni dell'operatore, salvo un platform admin, la cui sessione non dipende da una membership.
- **Agenzia**: ogni cambio di `status` revoca le sessioni dei membri attivi (esclusi i platform admin) e chiude l'acting di qualunque platform admin su quell'agenzia. La sessione del Super Admin resta valida.
- Il ricontrollo per richiesta già esistente resta: la revoca impedisce solo che una sessione torni valida.
- Dati, assegnazioni territoriali, membership storiche e riferimenti all'autore non vengono toccati.

## 5. Protezione dell'ultimo Super Admin

- Il Super Admin generale è un solo account (`STIMA360_SUPER_ADMIN_USER_ID`). L'API non può disabilitarlo né mandarlo a `invited`, nemmeno su sua richiesta (409 "L'account del Super Admin generale designato non si disabilita…"). Nessuna modifica viene fatta.
- Il flag `is_platform_admin` non è modificabile da alcuna API (già prima di ADM-1); la membership e lo stato dell'agenzia non incidono sull'accesso di un platform admin. Non esiste quindi un percorso API, diretto o indiretto, che lasci la piattaforma senza Super Admin attivo.
- Concorrenza: le modifiche si serializzano sulla riga del Super Admin (`FOR UPDATE`). Provato: due disabilitazioni simultanee → 409/409; ruolo tolto mentre una sospensione attende il lock → 403 e agenzia invariata; sospensione e ingresso simultanei → nessun acting rimasto su un'agenzia sospesa.

## 6. Limitazioni

- **Configurazione obbligatoria:** se `STIMA360_SUPER_ADMIN_USER_ID` non è impostata nel servizio, la gestione della Rete è negata a tutti (fail-closed). Prima del deploy va verificato che l'ambiente la contenga, con l'id dell'account `info@stima360.it` attivo e con `is_platform_admin = TRUE`. **Questo controllo non è stato fatto qui**, perché non ho letto i database né le configurazioni reali.
- Gli **altri platform admin** non possono più entrare nelle agenzie (acting), né consultare o gestire la Rete. Nella UI vedono la voce "Rete" con un avviso esplicito; il backend risponde 403.
- Modifiche via SQL diretto (flag, stati) restano fuori dalla portata dell'API; la protezione database è prevista in ADM-4.
- Le sessioni già revocate in passato non vengono ricostruite. Le sessioni rimaste in vita da sospensioni anteriori ad ADM-1 vengono revocate al successivo cambio di stato.
- Un'agenzia può ancora essere **creata** direttamente con stato `archived` o `suspended`. I requisiti di attivazione sono una fase successiva.
- La regola "nessuna eliminazione definitiva dal Cestino di un'agenzia non attiva" **non** è implementata qui (fase successiva).

## 7. Risultati dei test

Ultima esecuzione completa, sul codice definitivo.

**Unitari (senza DB)**: `test_adm1_super_admin_guard.py` + suite P26/P27/P28 della Rete

```
1 failed, 1155 passed in 68.15s
FAILED tests/test_p27_6_lead_routing.py::test_i1_059_is_the_highest_version_and_follows_058
```

Il fallimento è **preesistente** e identico sulla base `4eebf29`: il test pretende che la 059 sia l'ultima migration, ma il repository arriva alla 104. È un CODE FAILURE del test, fuori dal perimetro.

**PostgreSQL locale + browser (Playwright/Chromium)**: ADM-1, C1, C2 e C3 (API e browser), Zona-3 C2, eliminazione edifici 1/2/Voice, Cestino generale 1/2/3, approvazione edifici, LMC-15, Censimento 3, Voice-4 (sentinella dei file protetti):

```
1 failed, 207 passed in 197.72s   (0 skipped)
FAILED tests/test_voice_4_undo_postgres.py::test_m1_migration_099_is_valid_contiguous_and_has_a_down
```

`test_m1` fallisce **identico sulla base** `4eebf29`: pretende che la 099 sia l'ultima migration. Preesistente.

**Il nuovo collaudo è un RED vero**: eseguito sul codice originale `4eebf29`, fallisce in 9 scenari su 11. Passano solo il 03 (tenant invariato) e l'11 (nessun dato cancellato), che sono controlli di non regressione. Sul codice ADM-1 passano tutti e 11. I due scenari di concorrenza (05, 06) sono stati ripetuti 5 volte: sempre verdi.

Copertura dei 14 punti richiesti:

| # | Verifica | Test |
|---|---|---|
| 1 | Super Admin designato opera | `test_01` |
| 2 | altro platform admin non sospende/archivia agenzie, operatori, territori | `test_02` (13 richieste → 403, audit `denied`, DB invariato) |
| 3 | altro platform admin non approva C2 | `test_02`, suite C2 |
| 4 | utenti ordinari invariati | `test_03`, C1/C2/Censimento |
| 5 | ultimo Super Admin non disabilitabile | `test_04` |
| 6 | tentativi simultanei | `test_05`, `test_06` |
| 7 | disabilitazione invalida le sessioni | `test_07` |
| 8 | riattivazione non ripristina sessioni | `test_07`, `test_08`, `test_09` |
| 9 | revoca membership | `test_08` |
| 10 | sospensione agenzia | `test_09` |
| 11 | agenzia di sistema protetta | `test_10` |
| 12 | nessun dato cancellato | impronta di 16 tabelle in `test_02`, `07`, `09`, `11` |
| 13 | C1, C2, C3 funzionano | suite C1, C2, C3 (API e browser) |
| 14 | nessuna regressione autenticazione e CRM | `test_p26_1_operator_auth`, suite completa sotto |

**Suite completa senza DB** (`python -m pytest -q`), confronto base `4eebf29` / ADM-1 (su clone locale con le modifiche committate, perche' diversi test di perimetro leggono `git status` rispetto a `HEAD`):

- base: `617 failed, 9094 passed, 2676 skipped, 63 errors`;
- ADM-1: `617 failed, 9102 passed, 2687 skipped, 63 errors`.
- L'insieme dei test falliti o in errore e' **identico** fra le due esecuzioni: nessun test che passava sulla base fallisce con ADM-1, nessuno in piu', nessuno in meno.

**Fallimenti preesistenti per ambiente** (non causati da ADM-1, presenti anche sulla base):

- `test_p27_7_network_runtime.py` e `test_p28_acting_runtime.py` (78 test): il DOM simulato in Node non implementa `document.createElementNS`. CODE FAILURE del banco di prova, preesistente.
- `test_p27_6_postgres_real.py` e `test_p28_acting_postgres_real.py`: **saltati**. Creano un proprio cluster con `initdb`, che rifiuta di girare come root in questo container. ENVIRONMENT FAILURE. Il comportamento reale dell'acting è comunque coperto da `test_adm1` (06, 09) e da LMC-15.
- Fra i 617 fallimenti della suite completa della base ci sono test che cercano un PostgreSQL su `/var/run/postgresql`: ENVIRONMENT FAILURE, invariati.

Altri controlli:

- `node --check` sui 4 file JS modificati: ok.
- `git diff --check`: pulito.

Strumenti installati solo nel container effimero per il collaudo: dipendenze di `requirements*.txt` e `playwright==1.56.0` (Chromium era già presente). Nessuna modifica ai file di requisiti.

## 8. Funzioni rinviate alle fasi successive

- **ADM-2**: anteprima delle dipendenze prima di Sospendi/Archivia/Ripristina.
- **ADM-3**: comportamento dei servizi di un'agenzia sospesa (prenotazioni pubbliche, promemoria, calendario, portale proprietari), percorso autorizzato di follow-up dei lead, segnalazione dei Comuni presidiati da agenzie non attive.
- **ADM-4**: migration 105 (divieto di DELETE fisiche, rimozione delle CASCADE da `agencies`, trigger di audit `ENABLE ALWAYS`).
- **ADM-5**: registro operazioni consultabile; consultazione in sola lettura delle agenzie sospese/archiviate da parte del Super Admin.
- **ADM-6**: procedura completa di sostituzione e revoca del Super Admin.
- **ADM-7**: interfaccia Rete mobile con azioni esplicite e liste attivi/archiviati.
- Da collocare: requisiti di attivazione delle nuove agenzie; blocco della purge dal Cestino per agenzie non attive.

**STOP.** ADM-1 è chiusa con un commit locale su `core-0.1-test`. Nessun push, deploy o migration. ADM-2 e le fasi successive non sono state avviate.
