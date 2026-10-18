# STIMA360 CRM — ADM-4: Protezione dalla cancellazione fisica

- Branch: `core-0.1-test`, base `10039f6`, già pubblicata.
- **Nessun codice applicativo modificato.** Aggiunti soltanto un test e questo rapporto.
- Nessuna migration, nessun accesso a `stima360_db_test` o `stima360_db`, nessun push né deploy. Render e cron non toccati.
- Tutte le prove su PostgreSQL 16 locale e usa-e-getta.

## 1. Esito in breve

**Nessun percorso amministrativo dell'applicazione cancella fisicamente agenzie, territori, zone, operatori o appartenenze.** Ogni operazione della Rete è un `INSERT` o un `UPDATE` di stato:

| Operazione | Cosa fa davvero |
|---|---|
| Sospendere, archiviare, riattivare un'agenzia | `UPDATE agencies SET status` |
| Disabilitare o riattivare un operatore | `UPDATE operator_users SET status` |
| Revocare o riattivare un'appartenenza | `UPDATE agency_memberships SET status` |
| Sospendere, riattivare o revocare un'assegnazione | `UPDATE agency_territory_assignments SET status` |
| Trasferire un territorio | revoca dell'assegnazione corrente più `INSERT` della nuova, nella stessa transazione; nessun dato CRM cambia agenzia |
| Revocare un alias | `UPDATE network_territory_aliases SET status`; un alias revocato non torna attivo, se ne dichiara uno nuovo |

Il rischio che resta è **solo a livello di database**: un `DELETE` scritto a mano in SQL, fuori dall'applicazione. Per chiuderlo serve una migration, quindi la protezione è solo proposta (sezione 5).

## 2. Verifica

### 2.1 Endpoint

- `platform_admin/router.py` **non ha nessuna rotta `DELETE`**, verificato sul router caricato.
  - I commenti delle sezioni lo dichiarano esplicitamente: agenzie, operatori, configurazione, territori, alias.
  - Tutte le mutazioni di Rete richiedono `require_super_admin_context`, cioè il Super Admin designato di ADM-1.
- `platform_admin`, `network_routing` e `operator_auth` **non contengono SQL `DELETE` o `TRUNCATE`**.
- Le «zone» (moduli Zona e Territorio) non hanno un percorso amministrativo proprio di cancellazione. Il territorio è gestito da `network_territories` e dalle assegnazioni, già coperti.

### 2.2 Cancellazioni presenti nel codice, ma non amministrative

| Dove | Cosa | Valutazione |
|---|---|---|
| `appointments/working_hours_repository.py` | `DELETE FROM agency_closures` | giorni di chiusura dell'Agenda: operazione ordinaria dell'agenzia, nessuno storico CRM |
| `scripts/p26_3_live_cert.py`, `scripts/p26_6_live_cert.py` | `DELETE` di `operator_users`, `agencies`, sessioni e appartenenze | pulizia delle **sole fixture create dagli script stessi**; non sono API e non girano da sole |
| Cestino (`trash/purge.py`) ed eliminazione edifici (`property/building_deletion.py`), esposti come `POST .../purge` | cancellazione definitiva di immobili, contatti, richieste ed edifici **già nel Cestino** | funzione approvata nelle fasi Cestino ed Eliminazione edificio, fuori dal perimetro di questa fase. Vedi sotto |

**Cestino ed eliminazione edifici.**

Questa funzione è protetta da più controlli:

- solo il Super Admin designato, ricontrollato nel database;
- l'elemento deve essere già nel Cestino;
- testo di conferma esatto e impronta del riepilogo;
- ogni collegamento verso schede altrui **blocca** la cancellazione;
- copia dello storico verificata prima del `DELETE`.

Consensi e messaggi bloccano la cancellazione di un contatto. Lead, attività, appuntamenti e richieste la bloccano sempre.

Resta comunque una cancellazione fisica di immobili e contatti, in tensione con il vincolo «nessuna cancellazione fisica» di questa fase. **Non l'ho toccata:** preservare le funzionalità esistenti è un vincolo esplicito. Vedi la decisione D2.

### 2.3 Vincoli del database (migration 027–104)

`ON DELETE CASCADE` e `SET NULL` verso le tabelle della Rete:

| Tabella figlia | Riferisce | Regola |
|---|---|---|
| `agency_memberships` | `operator_users` | CASCADE |
| `operator_sessions` | `operator_users` | CASCADE |
| `calendar_oauth_states` | `agency_memberships` | CASCADE |
| `communication_messages`, `consent_events`, `communication_journeys` | `agencies` | CASCADE (messaggi e consensi hanno un guardiano che rifiuta ogni `DELETE` diretto) |
| `contacts`, `leads`, `tasks`, `activities`, `properties`, `buildings`, `buy_requests`, richieste e eventi territoriali, richieste di eliminazione edifici | `operator_users` | SET NULL (assegnatario, autore, decisore) |

Le altre chiavi verso agenzie e territori (immobili, contatti, stime, assegnazioni…) non hanno `ON DELETE`, oppure sono `RESTRICT`.

### 2.4 Prova diretta del rischio residuo

PostgreSQL locale, dentro una transazione **annullata**, sulla fixture ADM-2:

| `DELETE` SQL diretto | Esito |
|---|---|
| agenzia con dati | **rifiutato** (`ForeignKeyViolation`, `agency_memberships`) |
| territorio con assegnazioni | **rifiutato** (`ForeignKeyViolation`) |
| appartenenza con contatti assegnati | **rifiutato** (`contacts_agent_same_agency_fk`) |
| **operatore** | **eseguito**: la sua appartenenza sparisce (CASCADE) e i suoi 2 contatti assegnati perdono l'assegnatario (SET NULL) |
| **assegnazioni di un territorio** | **eseguito**: storico delle assegnazioni perso |
| **alias** | **eseguito**: storico dell'alias perso |

Nessuno di questi `DELETE` è raggiungibile dall'applicazione. Richiede un accesso SQL diretto al database.

## 3. Protezioni già presenti

1. **Router di Rete senza `DELETE`.** Solo stati: `suspended`, `archived`, `disabled`, `revoked`.
2. **Super Admin designato** su ogni mutazione di Rete (ADM-1). Un altro amministratore di piattaforma riceve 403.
3. **Agenzia di sistema** non sospendibile; archiviazione solo dopo la sospensione; ultimo Super Admin protetto (ADM-1).
4. **Trasferimento di territorio:**
   - revoca più nuova assegnazione, in una transazione, con riga di registro;
   - nessun immobile, contatto o lead cambia agenzia;
   - lo schema rifiuta `agency_id` in un aggiornamento di stato.
5. **Database:** chiavi senza `ON DELETE` o `RESTRICT` verso agenzie e territori; guardiani append-only su consensi e messaggi.
6. **ADM-3:** lead, follow-up, calendari e prenotazioni restano dell'agenzia sospesa o chiusa. Le email della valutazione continuano (ADM-3 BIS), e qui non cambiano.

## 4. Interventi eseguiti

**Nessuna modifica al codice applicativo**: nessun percorso amministrativo cancella. Per impedire che una cancellazione venga aggiunta in futuro senza accorgersene, ho aggiunto un test:

- `tests/test_adm4_no_physical_delete_postgres.py` (nuovo):
  - **`test_01` (statico):** nessun `DELETE`/`TRUNCATE` nei moduli della Rete; nessuna rotta `DELETE` nel router di piattaforma;
  - **`test_02` (PostgreSQL, sessioni vere):** revoca e riattivazione di un'appartenenza; revoca di un alias e dichiarazione di uno nuovo; sospensione e riattivazione di un'assegnazione; trasferimento di un territorio e ritorno. Verifica che:
    - nessuna riga sparisca dalle sei tabelle della Rete;
    - assegnazioni e alias revocati restino con il loro id e stato `revoked`;
    - i conteggi delle tabelle CRM e i proprietari di immobili, contatti e stime non cambino;
  - **`test_03`:** le stesse operazioni, fatte da un altro amministratore di piattaforma, ricevono 403 e non cambiano nulla.

## 5. Decisioni che richiedono autorizzazione

**D1 — Blocco dei `DELETE` diretti nel database (richiede una migration).**

- **Proposta:** un trigger `BEFORE DELETE`, `ENABLE ALWAYS`, che rifiuta la cancellazione di righe di `agencies`, `operator_users`, `agency_memberships`, `network_territories`, `agency_territory_assignments` e `network_territory_aliases`. È lo stesso schema già usato per consensi e messaggi.
- **Effetto:** chiude il rischio residuo della sezione 2.4, anche contro script o SQL manuale.
- **Effetti collaterali da decidere:**
  - gli script `scripts/p26_3_live_cert.py` e `scripts/p26_6_live_cert.py` puliscono le proprie fixture con `DELETE` e smetterebbero di funzionare: andrebbero adattati, oppure il trigger dovrebbe ammettere un'eccezione;
  - nessun impatto sull'applicazione, che non cancella mai queste righe.
- **Decisione del proprietario: non implementare adesso.** Il rischio residuo resta documentato qui e sarà affrontato in una fase separata, previa autorizzazione.

**D2 — Cancellazione definitiva del Cestino (immobili, contatti, richieste) ed eliminazione edifici.**

- È una funzione approvata e protetta, ma resta una cancellazione fisica di dati protetti.
- **Decisione del proprietario: resta esattamente com'è**, secondo le regole già approvate. Nessuna modifica né disattivazione.

## 6. Test eseguiti

- `tests/test_adm4_no_physical_delete_postgres.py`: **3 superati**.
- Regressione mirata, eseguita insieme (ADM-1 guardia e PostgreSQL, ADM-2, ADM-3 continuità, ADM-3 BIS, ADM-4): **49 superati**, nessun fallimento.
- Sonda della sezione 2.4: eseguita su un database locale usa-e-getta, dentro transazioni annullate; non fa parte della suite.
- Nessuna suite completa, come richiesto: il codice applicativo non è cambiato.

**Chiusura.** D1 rinviata, D2 confermata così com'è. ADM-4 si chiude con un solo commit di test e rapporto. Nessun codice applicativo, database o migration modificati; nessun push o deploy.
