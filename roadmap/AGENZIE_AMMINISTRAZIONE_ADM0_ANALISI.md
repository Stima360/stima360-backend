# STIMA360 CRM — ADM-0: analisi e roadmap per la rimozione sicura di account, agenzie e territori

- Branch: `core-0.1-test`, HEAD `9449d6f` (C3), verificato prima dell'analisi.
- Tipo: **analisi in sola lettura**. Nessun file applicativo, migration, dato, commit o push. Unico file creato: questo documento.
- Fonti: codice Python (`platform_admin/`, `operator_auth/`, `network_routing/`, `property/`, `core/`, `trash/` e moduli di dominio), migration `migrations/*.sql` (solo i file "up"), UI `static/os_shell/assets/views/rete*.js`.
- **Non eseguito:** nessuna query SQL su `stima360_db_test`. Lo schema descritto è quello **dichiarato dalle migration**: che tutte le migration (in particolare 101–104) siano applicate su TEST non è stato verificato qui e va controllato con SELECT sul catalogo prima di ADM-1.

Terminologia usata (mappatura sul codice reale, nessuna entità nuova):

| Termine di prodotto | Entità reale |
|---|---|
| Super Admin generale | `operator_users.id` = env `STIMA360_SUPER_ADMIN_USER_ID` **e** `is_platform_admin = TRUE` **e** `status = 'active'` |
| Amministratore di piattaforma | `operator_users.is_platform_admin = TRUE` |
| Supervisore / titolare | `agency_memberships.role = 'agency_owner'` |
| Direttore / amministratore d'agenzia | `agency_memberships.role = 'agency_admin'` (da confermare, §8) |
| Agente / collaboratore | `agency_memberships.role = 'agent'` |
| Zona / territorio | `network_territories` (kind `province` / `municipality` / `postal_code`) |
| Assegnazione comunale | `agency_territory_assignments` |
| Alias comunale | `network_territory_aliases` (source `public_stima_comune`) |

---

## 1. Inventario delle funzionalità esistenti

### 1.1 Stati già presenti (nessuna nuova entità necessaria)

| Tabella | Colonna di stato | Valori | Vincoli rilevanti |
|---|---|---|---|
| `agencies` (027:48) | `status` | `active` / `suspended` / `archived` | slug unico e immutabile; **nessun** `archived_at`/`suspended_at`; nessun trigger |
| `operator_users` (027:81) | `status`, `is_platform_admin` | `invited` / `active` / `disabled` | email unica; nessun trigger |
| `agency_memberships` (027:121) | `role`, `status` | ruoli owner/admin/agent; stati `active` / `suspended` / `revoked` | `uq_agency_memberships_single_active` (una membership attiva per persona), `uq_agency_memberships_single_owner` (un titolare attivo per agenzia) |
| `operator_sessions` (027:157, 060) | `revoked_at`, `expires_at`, `acting_agency_id` | — | token solo come hash SHA-256 |
| `network_territories` (058:99) | — (nessuno stato) | — | identità unica `(kind, canonical_key)` |
| `agency_territory_assignments` (058:154) | `status` | `active` / `suspended` / `revoked` (revoked definitivo) | **`uq_agency_territory_single_active`**: una sola assegnazione attiva per territorio |
| `network_territory_aliases` (059:75) | `status` | `active` / `revoked` | un alias attivo per valore normalizzato |
| `platform_audit_log` (057:133) | — | `success` / `denied` / `error` | nessuna FK (id come istantanee), trigger append-only e no-truncate |

**Conclusione:** Sospensione e archiviazione **esistono già come stati** di agenzia, operatore, membership e assegnazione. Non serve inventare un nuovo modello: serve proteggerlo, completarlo e renderlo coerente.

### 1.2 Endpoint amministrativi esistenti (`/api/platform`, `platform_admin/router.py`)

Montati con `require_platform_admin` (`main.py:219`), ogni ammissione registrata in `platform_audit_log`.

- Agenzie: `GET/POST /agencies`, `GET/PATCH /agencies/{id}` (nome, stato), `GET/PATCH /agencies/{id}/configuration`, `POST /agencies/{id}/enter`, `POST /agency-context/exit`.
- Operatori: `GET /agencies/{id}/operators`, `POST /agencies/{id}/operators`, `GET/PATCH /operators/{id}` (nome, cognome, stato), `PATCH /agencies/{id}/operators/{uid}/membership` (ruolo, stato), `PUT /agencies/{id}/owner` (trasferimento titolare).
- Territori: `GET/POST /territories`, `GET /territories/{id}`, `GET/POST /agencies/{id}/territories`, `PATCH /agencies/{id}/territories/{assignment_id}`, `POST /territories/{id}/transfer`, alias `GET/POST /territories/{id}/aliases`, `PATCH /aliases/{id}`.
- Edifici/Cestino/C2: richieste di eliminazione edifici, Cestino di piattaforma, purge, `territorial-change-requests` approve/reject.
- **Nessun endpoint DELETE** per agenzie, operatori, membership, territori, assegnazioni o alias. **Nessun endpoint di lettura del registro di audit.**
- Il flag `is_platform_admin` **non è modificabile da API** (non è fra le colonne aggiornabili): oggi si cambia solo direttamente nel DB.

### 1.3 Interfaccia Rete (`static/os_shell/assets/views/`)

- `rete.js`: schede Agenzie (elenco + Nuova agenzia), Territori (catalogo, filtri, Nuovo territorio), Modifiche territoriali (C2), collegamento al Cestino per le eliminazioni edifici.
- `rete-agenzia.js`: Entra nell'agenzia; Panoramica (nome e stato active/suspended/archived con conferma); Configurazione; Operatori (aggiungi, Trasferisci titolare, Accesso active/invited/disabled, Membership ruolo/stato con conferma sulla revoca); Territori (Sospendi, Riattiva, Revoca, Assegna).
- `rete-territorio.js`: presidio, Trasferisci territorio, catena di routing, alias (Revoca, Dichiara valore).
- `rete-modifiche.js` (C2) e `rete-eliminazioni.js` (edifici, ora sotto Cestino).
- Visibilità: voce "Rete" solo se `session.is_platform_admin`; ogni vista chiama prima `GET /api/platform/me`; l'autorizzazione vera è nel backend.
- Rendering: `innerHTML` con template literal ovunque, ma i valori dinamici passano per `escapeHtml`; non è stata trovata stringa API non escapata.
- Mobile: layout responsive generico (sidebar che diventa barra, tab scorrevoli, tabelle con scroll orizzontale, dialog al 100% fino a 560px). Mancano: card al posto delle tabelle, `.form-grid-2` a una colonna su smartphone, media query nel blocco CSS Rete (`app.css:470-490`).
- **Assenti:** filtro attivi/archiviati, avvisi sulle dipendenze, registro operazioni, distinzione visiva Super Admin generale vs amministratore di piattaforma.

### 1.4 Autenticazione e sessioni (`operator_auth/`)

- Sessioni in tabella (`operator_sessions`), cookie HttpOnly/Secure/SameSite=Lax, durata assoluta 12h, inattività 240 min.
- **Ricontrollo a ogni richiesta** (`repository.resolve_session` + `service._scope_is_usable`): utente `active`; se non platform admin, membership `active` **e** agenzia `active`. Il flag `is_platform_admin` è riletto a ogni richiesta.
- Sospendere operatore, membership o agenzia **blocca l'accesso dalla richiesta successiva**. Il contesto "acting" di un platform admin dentro un'agenzia non più attiva viene azzerato alla richiesta successiva.
- **Le sessioni non vengono revocate** alla sospensione: se l'elemento viene riattivato entro 12h/4h, il vecchio cookie torna valido. Non esiste "revoca tutte le sessioni di un utente".
- Nessun rate limit o blocco sui tentativi di login.

### 1.5 Super Admin

- `platform_admin/super_admin.py`: tre controlli, **mai l'email**: flag di sessione, id uguale a `STIMA360_SUPER_ADMIN_USER_ID` (riletto a ogni chiamata; assente = tutto negato), ruolo riletto nel DB nella stessa transazione (`require_super_admin_db`).
- `info@stima360.it` compare solo come destinatario/mittente di email e come dato di seed di test (`scripts/p26_seed_agencies_test.py:84`): **non concede alcun privilegio**. Conforme al requisito.
- **Due livelli diversi oggi coesistono:**
  - richiedono il **Super Admin generale designato**: eliminazione/purge edifici, Cestino di piattaforma, purge;
  - richiedono **qualunque amministratore di piattaforma**: tutta la Rete (agenzie, operatori, membership, territori, alias, acting) e l'approvazione/rifiuto C2 (`require_super_admin_db` senza confronto con l'id designato).

---

## 2. Mappa delle dipendenze dei dati

### 2.1 Chi dipende dalle agenzie (`agencies.id`)

- **RESTRICT** (una DELETE dell'agenzia fallirebbe, i dati sono protetti): contatti, lead, attività, task, stime, stime dettagliate, immobili, edifici, richieste di acquisto, timeline venditori, follow-up, property watch, next best action, flow, appuntamenti, acquisizioni, calendar sync, orari di lavoro, chiusure, link di prenotazione pubblica, Voice, `record_lifecycle_events`, richieste di eliminazione edifici, proposte e eventi C2, membership, assegnazioni territoriali, sessioni in acting.
- **NO ACTION** (equivalente in pratica): `stima_pdf_artifacts`, `public_submission_receipts`, `site_import_records`.
- **CASCADE (pericolosi):**
  - `consent_events.agency_id` (062:98)
  - `communication_messages.agency_id` (065:145) → a cascata `communication_attempts`
  - `communication_journeys.agency_id` (071:36) → a cascata `communication_journey_steps`
- Tabelle senza `agency_id` (match, proposte, vendite, owner): la tenancy è derivata dal padre e protetta da trigger di coerenza.

Nota: oggi una DELETE su `agencies` verrebbe quasi certamente fermata dalle molte RESTRICT. Ma un'agenzia **senza** immobili/contatti e con soli consensi o comunicazioni perderebbe quei dati in silenzio. Il consenso è un dato con valore legale.

### 2.2 Chi dipende dagli operatori (`operator_users.id`)

- **CASCADE:** `agency_memberships` (027:124), `operator_sessions` (027:159); da membership: `calendar_oauth_states` (074:110).
- **SET NULL (perdita del riferimento all'autore):** `contacts.assigned_agent_id`, `contacts.created_by_user_id`, `leads.assigned_agent_id`, `leads.created_by_user_id`, `activities.created_by_user_id`, `tasks.created_by_user_id`, `deleted_by_user_id` su immobili/contatti/richieste/edifici, `position_verified_by_user_id` su edifici e immobili, i quattro operatori di `building_deletion_requests`, `requested_by_user_id`/`decided_by_user_id` delle proposte C2, `actor_user_id` degli eventi C2.
- **RESTRICT:** appuntamenti e loro eventi, acquisizioni e loro eventi, stima_acquisitions/inspections, comunicazioni (journey, enrollment, automation), Voice, `record_lifecycle_events`, link di prenotazione.
- **FK composite verso membership** (NO ACTION/RESTRICT): `properties.assigned_agent_id`, `appointments.assigned_user_id`, `acquisitions.assigned_agent_id`, `public_booking_links.assigned_user_id`, orari ed eccezioni, `calendar_connections`.
- **Testo libero, non collegato a operatori** (non si perde, ma non è nemmeno verificabile): `created_by`/`assigned_to` di buy, match, attività/task legacy, flow, owner, proposte, vendite, storico prezzi/stati.
- **Istantanee senza FK:** `platform_audit_log.actor_user_id` + `actor_label`, `communication_messages.actor_user_id`.

### 2.3 Territori, assegnazioni, alias

- Solo RESTRICT, nessuna CASCADE o SET NULL. Nessuna tabella operativa punta ad assegnazioni o alias.
- `property_territorial_change_requests.territory_id` è un BIGINT senza FK (istantanea).
- Le assegnazioni non hanno date di validità: lo storico delle competenze esiste **solo** come righe `revoked` + `platform_audit_log`. Una riga `revoked` non si riattiva mai (si crea una riga nuova), quindi lo storico non viene riscritto.

### 2.4 Trigger e protezioni esistenti

- **Append-only:** `platform_audit_log` (057), `record_lifecycle_events` (085), eventi appuntamenti (072), eventi acquisizioni (081), eventi Voice (098/099), richieste eliminazione edifici (101), proposte ed eventi C2 (104), consent_notices/consent_events (061/062), comunicazioni (064/065/071).
- **Rifiuto DELETE:** appuntamenti, acquisizioni, righe Voice, richieste edifici, proposte C2.
- **Immutabilità `agency_id`:** flow (055), comunicazioni, appuntamenti, acquisizioni, Voice; trigger di coerenza tenancy su quasi tutti i domini (030–054).
- **Cestino:** guardie e freeze su immobili, contatti, edifici, richieste (086, 090, 091, 092, 101).
- **Nessun trigger** su `agencies`, `operator_users`, `agency_memberships`, `network_territories`, `agency_territory_assignments`, `network_territory_aliases`. Nessun `REVOKE`/`GRANT` nelle migration.
- I trigger di `platform_audit_log` **non sono `ENABLE ALWAYS`** (a differenza di 061/062/064): con `session_replication_role = replica` sono disattivabili.

### 2.5 Visibilità dei dati di un'agenzia sospesa o archiviata

- Nessun codice sposta, riassegna o nasconde righe quando cambia lo stato (`agencies_service.update_agency` aggiorna solo la colonna).
- Gli operatori dell'agenzia non ottengono più una sessione; il platform admin **non può entrare** (acting rifiutato se l'agenzia non è `active`).
- **Quindi i dati di un'agenzia sospesa/archiviata non sono consultabili da nessuno**, Super Admin compreso, tranne:
  - Cestino di piattaforma (solo righe già nel Cestino);
  - richieste edifici e proposte C2;
  - lettura territoriale C1: gli immobili vivi dell'agenzia d'origine sospesa restano leggibili dall'agenzia **competente** attiva (si controlla lo stato del lettore, non dell'origine).
- Non esiste un percorso Super Admin di sola lettura sui dati vivi (contatti, lead, immobili) di un'agenzia non attiva.

### 2.6 Processi che ignorano lo stato dell'agenzia

Continuano a funzionare per un'agenzia sospesa: prenotazione pubblica (`public_booking/service.py`), worker di calendar sync, promemoria appuntamenti, portale proprietari/login owner, ritentativo bridge di una stima già salvata e site sync (`property/site_sync.py`), purge dal Cestino, approvazione C2 sull'immobile d'origine.

Si fermano correttamente: sessioni, acting, routing nuovi lead, flow/revival/property watch/owner alert/follow-up cron (solo agenzie attive), dispatch comunicazioni (login come agency_admin), calendar inbound.

### 2.7 Routing dei nuovi lead (`network_routing/`)

- Catena: `stime.comune` → alias attivo → territorio `municipality` → assegnazione attiva → **agenzia attiva**. Più di una corrispondenza → `RoutingAmbiguous`; nessuna → agenzia di default (slug `stima360`) se attiva; altrimenti la stima pubblica fallisce con 500 e **non viene salvata**.
- Se l'agenzia competente viene sospesa, la sua assegnazione resta `active` ma la catena si interrompe: i nuovi lead vanno **in silenzio** all'agenzia di default (solo una riga di log `source=fallback`).
- Province e CAP esistono come tipi di territorio ma **non sono usati** né dal routing né dalla competenza C1: oggi la competenza territoriale è di fatto solo comunale, e l'indice unico garantisce un'unica agenzia attiva per Comune. Un'assegnazione di provincia e una comunale dentro di essa possono coesistere, ma oggi non producono doppia competenza.

---

## 3. Rischi di perdita o di perdita di accessibilità

Legenda: **PERSO** = il dato viene cancellato o il riferimento azzerato; **INACCESSIBILE** = conservato ma non consultabile; **INCOERENTE** = conservato ma lo stato operativo è sbagliato.

| # | Scenario | Effetto | Esito | Raggiungibile oggi da API? |
|---|---|---|---|---|
| R1 | DELETE fisica di un'agenzia (SQL diretto/script) | CASCADE su `consent_events`, `communication_messages`/`attempts`, `communication_journeys`/`steps` (se nient'altro la blocca) | **PERSO** | No, solo SQL |
| R2 | DELETE fisica di un operatore | CASCADE su membership e sessioni; SET NULL su autori/assegnatari di contatti, lead, attività, task, Cestino, verifiche di posizione, richieste edifici, C2 | **PERSO** (autore storico) | No, solo SQL |
| R3 | Stesso R2: conflitti fra SET NULL e trigger append-only (eventi C2 104:132 vs 104:146; richieste edifici 101:104-108; freeze Cestino) e FK composite verso membership | la DELETE fallisce in modo rumoroso o parziale a seconda dell'ordine | errore; protezione **accidentale**, non progettata | No |
| R4 | Sospensione/archiviazione di un'agenzia | dati conservati ma nessuno, Super Admin compreso, può consultarli | **INACCESSIBILE** | Sì (`PATCH /agencies/{id}`) |
| R5 | Agenzia sospesa con assegnazioni comunali ancora `active` | nuovi lead dirottati in silenzio sull'agenzia di default; il Comune risulta "presidiato" in Rete ma non lo è | **INCOERENTE** | Sì |
| R6 | Agenzia sospesa: prenotazioni pubbliche, promemoria, calendar sync, portale proprietari, bridge stime già salvate | continuano a produrre scritture e comunicazioni a nome di un'agenzia sospesa | **INCOERENTE** | Sì |
| R7 | Operatore disabilitato o membership sospesa | contatti/lead/immobili/acquisizioni restano assegnati a lui; gli altri agenti non li vedono più (owner e admin sì) | **INACCESSIBILE** parziale | Sì |
| R8 | Sospensione e poi riattivazione entro 12h | i vecchi cookie tornano validi | privilegio **riattivato involontariamente** | Sì |
| R9 | Un amministratore di piattaforma disabilita sé stesso, il Super Admin designato o l'ultimo platform admin (`PATCH /operators/{id}`) | nessuna guardia: piattaforma potenzialmente senza Super Admin attivo; recupero solo via SQL | **perdita di controllo** | Sì |
| R10 | Un platform admin non designato sospende/archivia agenzie, operatori, territori | consentito: la Rete richiede solo `is_platform_admin` | requisito "solo il Super Admin" non rispettato | Sì |
| R11 | Archiviazione diretta `active → archived` di un'agenzia con assegnazioni, titolare attivo, prenotazioni attive | nessun controllo di dipendenze né avviso, nessun lock di riga | **INCOERENTE** | Sì |
| R12 | Agenzia creata `active` senza titolare, senza territorio, senza configurazione | consentito | requisito "nessuna agenzia attiva senza requisiti" non rispettato | Sì |
| R13 | Disattivazione dei trigger di audit con `session_replication_role = replica` | `platform_audit_log` alterabile da chi ha accesso DB privilegiato | rischio residuo | No, solo DB |
| R14 | Purge dal Cestino di elementi di un'agenzia sospesa | consentito (nessun controllo sullo stato agenzia); è comunque riservato al Super Admin designato, con conferma e copia storica | da decidere | Sì, solo Super Admin |

Dati **mai persi** con le sole operazioni API attuali: immobili, edifici, unità, contatti, lead, appuntamenti, incarichi, documenti, interazioni, censimento, posizioni verificate, assegnazioni storiche, richieste, C2, audit. Il rischio di **perdita** reale (R1–R3) esiste solo per interventi SQL diretti o futuri endpoint scritti male. Il rischio **attuale tramite API** è di inaccessibilità, incoerenza e perdita di controllo (R4–R12).

---

## 4. Protezioni mancanti

1. **Divieto strutturale di DELETE** su `agencies`, `operator_users`, `agency_memberships`, `network_territories`, `agency_territory_assignments`, `network_territory_aliases` (oggi nessun trigger).
2. **Rimozione delle CASCADE da `agencies`** su consenso e comunicazioni (R1) e, se si vieta la DELETE degli operatori, neutralizzazione di fatto delle CASCADE/SET NULL da `operator_users` (R2).
3. **Guardia "ultimo Super Admin"**: nessuna disabilitazione dell'account designato né dell'ultimo platform admin attivo; nessuna auto-disabilitazione.
4. **Livello di autorizzazione unico**: le mutazioni di stato della Rete (e C2, da decidere) riservate al Super Admin generale con rilettura nel DB, non a qualunque platform admin.
5. **Revoca delle sessioni** nella stessa transazione della sospensione/disabilitazione/archiviazione (R8).
6. **Lock e transizioni**: `SELECT … FOR UPDATE` sulle righe agenzia/operatore/membership coinvolte, con ordine di lock unico (agenzia → membership → operatore → sessioni); macchina a stati esplicita (es. `archived` solo da `suspended`, ripristino con verifica dei conflitti).
7. **Anteprima delle dipendenze** prima di ogni Sospendi/Archivia/Ripristina (conteggi di immobili, contatti, lead, appuntamenti futuri, assegnazioni attive, membership attive, link di prenotazione, connessioni calendario, proposte C2 pending, journey attivi).
8. **Coerenza territoriale alla sospensione** (R5): decisione sul destino delle assegnazioni e segnalazione del fallback del routing.
9. **Processi in background** che ignorano lo stato dell'agenzia (R6).
10. **Consultazione Super Admin in sola lettura** dei dati di un'agenzia non attiva (R4).
11. **Registro operazioni leggibile**: endpoint di sola lettura su `platform_audit_log` e vista in Rete; trigger di audit `ENABLE ALWAYS`.
12. **Requisiti minimi di un'agenzia attiva** (R12): da definire (§8).
13. **Gestione del flag `is_platform_admin`** da una procedura controllata, invece che via SQL.
14. Marcatura temporale dello stato (`status_changed_at` o equivalente): **non indispensabile** se si usa `platform_audit_log` come fonte dello storico (scelta consigliata: nessuna nuova colonna).

---

## 5. Soluzione tecnica consigliata (la più semplice)

Principio: **riusare gli stati già presenti** e la pipeline `audit_then_commit` di `platform_admin/transaction.py`; aggiungere solo guardie, revoca sessioni e un divieto di DELETE nel database.

- **Sospensione** = `status = 'suspended'` (agenzia / membership) o `disabled` (operatore), già esistenti. Nella stessa transazione: lock delle righe, `UPDATE operator_sessions SET revoked_at = NOW()` per le sessioni interessate (operatore, oppure tutti i membri dell'agenzia e gli acting su di essa), audit prima del commit. Nessun dato operativo toccato, nessun riassegnamento automatico.
- **Archiviazione** = `status = 'archived'` dell'agenzia (esiste) e `revoked` per membership/assegnazioni (esiste, definitivo). Per l'operatore non esiste `archived`: `disabled` copre già il caso; se il proprietario vuole distinguerli nelle liste, si valuta un valore aggiuntivo (§8). Le liste operative mostrano già solo ciò che è attivo nei punti rilevanti; la Rete aggiunge un filtro Attivi/Sospesi/Archiviati.
- **Ripristino** = ritorno ad `active` con le guardie già presenti (una membership attiva per persona, un titolare per agenzia, una assegnazione attiva per territorio) più quelle nuove: agenzia con requisiti minimi, conflitti territoriali risolti esplicitamente (mai revoca silenziosa di un'altra agenzia). Le sessioni non rinascono: l'utente rifà il login.
- **Eliminazione definitiva**: **non prevista**. Trigger `BEFORE DELETE` che rifiuta sulle sei tabelle amministrative, e sostituzione delle tre CASCADE da `agencies` con RESTRICT. Un'eventuale anonimizzazione per obblighi normativi resta una procedura separata da autorizzare.
- **Autorizzazione**: tutte le mutazioni di stato richiedono `require_super_admin` + `require_super_admin_db` (lo stesso modulo già usato per edifici e Cestino). Le letture della Rete possono restare aperte ai platform admin (da decidere).
- **Consultazione dello storico**: consentire al Super Admin l'acting **in sola lettura** dentro un'agenzia sospesa/archiviata (solo metodi GET, ogni scrittura rifiutata nel backend), riusando il meccanismo di acting esistente invece di creare una seconda interfaccia.
- **Audit**: `platform_audit_log` resta l'unica fonte; si aggiunge un endpoint di sola lettura e una vista "Registro"; i trigger diventano `ENABLE ALWAYS`.

---

## 6. Gestione della sostituzione del Super Admin

### 6.1 Stato attuale

- Il Super Admin generale è **un solo id**, configurato nell'ambiente del servizio (`STIMA360_SUPER_ADMIN_USER_ID`). Cambiarlo richiede di modificare la configurazione e riavviare/ridistribuire il servizio. Se il valore manca o non è valido, **tutte** le operazioni riservate sono negate (fail-closed: corretto).
- Il flag `is_platform_admin` si cambia solo via SQL. Qualunque platform admin può oggi disabilitare l'account designato (R9).

### 6.2 Procedura consigliata (da implementare in ADM-6, non ora)

Sostituzione A → B, senza finestra priva di Super Admin e senza perdita di audit:

1. **Preparazione**: B esiste in `operator_users`, `status = 'active'`, accesso verificato (login riuscito).
2. **Promozione**: A (Super Admin designato), da un'operazione dedicata e auditata, imposta `is_platform_admin = TRUE` per B. Guardie: solo il designato può farlo; B attivo; audit prima del commit.
3. **Designazione**: il proprietario aggiorna `STIMA360_SUPER_ADMIN_USER_ID = id(B)` nella configurazione del servizio e ridistribuisce. Durante il passaggio A conserva il flag, quindi la piattaforma non resta mai senza amministratore.
4. **Verifica**: B esegue un'operazione riservata di sola lettura (es. lettura Cestino di piattaforma) con esito `success` nel registro; A riceve ora `not_designated`.
5. **Revoca di A** (facoltativa, eseguita da B): rimozione del flag e/o `disabled`, con revoca delle sessioni di A. Guardia: rifiutata se A è ancora il designato o se resterebbe zero platform admin attivi.
6. Nessun dato di A viene toccato: il suo id resta nell'audit, negli eventi e come autore storico.

Guardie permanenti: mai disabilitare/declassare l'account designato; mai portare a zero i platform admin attivi; mai auto-disabilitarsi; ogni modifica del flag scritta in `platform_audit_log`.

**Recupero d'emergenza** (designato perso o disabilitato): resta una procedura manuale fuori dalla piattaforma (SQL autorizzato + configurazione), documentata a parte, con riga di audit scritta dall'operatore che interviene.

Alternativa (non consigliata ora): spostare la designazione dall'ambiente al DB. Elimina il deploy ma apre la possibilità che un errore applicativo cambi il Super Admin; la configurazione d'ambiente è oggi il controllo più robusto.

---

## 7. Roadmap operativa

Fasi indipendenti, ciascuna verificabile da sola. Ordine consigliato: ADM-1 → ADM-2 → ADM-3 → ADM-4 → ADM-5 → ADM-6 → ADM-7. Ogni fase richiede autorizzazione esplicita.

### ADM-1 — Guardie backend su autorizzazione, Super Admin e sessioni (senza migration)

- **Obiettivo:** chiudere R8, R9, R10, R11 in parte.
- **File:** `platform_admin/router.py`, `operators_service.py`, `agencies_service.py`, `territories_service.py`, `aliases_service.py`, `operators_repository.py`, `super_admin.py`, `operator_auth/repository.py` (funzione di revoca sessioni).
- **Tabelle:** solo scritture già esistenti (`agencies`, `operator_users`, `agency_memberships`, `agency_territory_assignments`, `operator_sessions`).
- **Migration:** no.
- **Protezioni:** mutazioni di stato riservate al Super Admin designato con rilettura DB; guardia ultimo/designato/auto-disabilitazione; revoca sessioni nella stessa transazione; `FOR UPDATE` con ordine unico; transizioni ammesse esplicite.
- **Test (pytest, RED prima):** platform admin non designato → 403 + riga `denied`; disabilitare il designato → 409; disabilitare l'ultimo platform admin → 409; dopo sospensione il vecchio cookie resta invalido anche dopo riattivazione; agenzia sospesa → sessioni membri e acting revocati; audit non scrivibile → nessuna modifica; transizioni non ammesse → 409.
- **Rischi:** blocco operativo se `STIMA360_SUPER_ADMIN_USER_ID` non è configurato su TEST (verificare prima); test esistenti P27 che usano un platform admin generico vanno adeguati.
- **Autorizzazione:** sì.

### ADM-2 — Anteprima delle dipendenze (sola lettura, senza migration)

- **Obiettivo:** avvisi prima di Sospendi/Archivia/Ripristina (R11, R7).
- **File:** nuovo endpoint `GET /api/platform/agencies/{id}/impact` e `GET /api/platform/operators/{id}/impact` in `platform_admin/` (repository di sola lettura con conteggi).
- **Tabelle (solo SELECT):** immobili, edifici, contatti, lead, appuntamenti futuri, acquisizioni, membership, assegnazioni attive, link di prenotazione attivi, connessioni calendario, proposte C2 pending, richieste edifici pending, journey attivi; per l'operatore: record assegnati.
- **Migration:** no.
- **Protezioni:** solo Super Admin; nessuna scrittura; nessun dato personale nella risposta (solo conteggi).
- **Test:** conteggi corretti su dati di fixture; 403 per non designato; nessuna riga scritta oltre all'ammissione.
- **Rischi:** costo delle query su agenzie grandi (conteggi con indici esistenti su `agency_id`).
- **Autorizzazione:** sì.

### ADM-3 — Coerenza operativa di un'agenzia non attiva (senza migration)

- **Obiettivo:** R5, R6.
- **File:** `public_booking/service.py`, `appointment_reminders/repository.py`, `calendar_sync/repository.py`, `owner/login_service.py` (da decidere), bridge stime persistite in `core/repository.py`/`network_routing/service.py`, `property/site_sync.py`, `main.py` (log/segnalazione fallback).
- **Migration:** no.
- **Protezioni:** filtro `agencies.status = 'active'` nei soli punti decisi dal proprietario; nessun trasferimento automatico; segnalazione visibile in Rete dei Comuni con assegnazione attiva a un'agenzia non attiva.
- **Test:** agenzia sospesa → prenotazione pubblica rifiutata, promemoria non inviati, worker calendario la salta; riattivazione → tutto riprende.
- **Rischi:** clienti finali e proprietari che perdono un servizio: richiede decisione esplicita (§8, D5–D7).
- **Autorizzazione:** sì, dopo le decisioni.

### ADM-4 — Protezione strutturale nel database (migration 105)

- **Obiettivo:** R1, R2, R3, R13.
- **File:** `migrations/105_adm_no_delete_admin_entities.sql` + `_down.sql`.
- **Contenuto:** trigger `BEFORE DELETE` (e `BEFORE TRUNCATE`) che rifiuta su `agencies`, `operator_users`, `agency_memberships`, `network_territories`, `agency_territory_assignments`, `network_territory_aliases`; `consent_events`, `communication_messages`, `communication_journeys`: FK verso `agencies` da CASCADE a RESTRICT; trigger di `platform_audit_log` in `ENABLE ALWAYS`.
- **Protezioni:** migration idempotente con verifica di catalogo, come 057/104.
- **Test:** DELETE su ciascuna tabella → eccezione; FK con `confdeltype = 'r'`; trigger `tgenabled = 'A'`; suite esistente verde.
- **Rischi:** script di test/purge esistenti che cancellano agenzie o operatori di fixture (da censire: `a30_test_purge`, `integration_p2_support.py`, runner e2e); le funzioni di purge comunicazioni che si appoggiano alla CASCADE da agenzia (062 consente DELETE solo in cascata) vanno riverificate.
- **Autorizzazione:** sì, esplicita per migration, con applicazione solo su TEST e verifica separata dello stato DB.

### ADM-5 — Registro operazioni e consultazione storica (senza migration)

- **Obiettivo:** registro leggibile; R4.
- **File:** `GET /api/platform/audit` (filtri per azione, target, agenzia, periodo, paginato) in `platform_admin/`; acting in sola lettura su agenzia non attiva in `platform_admin/acting_service.py` e `operator_auth/service.py` (`_acting_or_cleared`, `_effective_agency`) con rifiuto delle scritture nel backend.
- **Migration:** no (la colonna `acting_agency_id` esiste; il modo sola lettura è derivato dallo stato dell'agenzia).
- **Protezioni:** solo Super Admin; audit dell'ingresso; ogni metodo non GET rifiutato dal backend quando l'agenzia di acting non è attiva.
- **Test:** POST/PATCH/DELETE in acting read-only → 403 su tutte le superfici tenant; GET funzionanti; registro filtrabile e non modificabile.
- **Rischi:** rotte tenant che scrivono su GET (da censire); cron che leggono l'acting.
- **Autorizzazione:** sì.

### ADM-6 — Gestione del flag di piattaforma e sostituzione del Super Admin

- **Obiettivo:** §6.
- **File:** `platform_admin/operators_service.py`, `router.py`, `schemas.py`.
- **Migration:** no.
- **Protezioni:** solo il designato; mai zero platform admin attivi; mai declassare il designato; revoca sessioni del revocato; audit.
- **Test:** promozione/revoca, tutte le guardie, sequenza completa di sostituzione con cambio della variabile d'ambiente nel test.
- **Rischi:** errore di configurazione al passaggio della variabile (mitigato dal fail-closed e dal mantenimento del flag su A fino alla verifica).
- **Autorizzazione:** sì.

### ADM-7 — Interfaccia Rete

- **Obiettivo:** UI semplice, mobile, riservata.
- **File:** `static/os_shell/assets/views/rete.js`, `rete-agenzia.js`, `rete-territorio.js`, nuovo `rete-registro.js`, `app.css` (blocco Rete).
- **Contenuto:** filtro Attivi/Sospesi/Archiviati; pulsanti espliciti **Sospendi / Archivia / Ripristina** con dialog che mostra l'anteprima di ADM-2 e richiede conferma; scheda **Registro**; badge di stato; segnalazione dei Comuni presidiati da agenzie non attive; card su smartphone al posto della tabella operatori; `.form-grid-2` a una colonna sotto 767px; azioni nascoste se `/me` indica che l'utente non è il Super Admin designato (il backend resta l'autorità).
- **Migration:** no.
- **Protezioni:** nuovo codice con DOM API e `textContent`, nessun nuovo `innerHTML` dinamico; `node --check` sui file toccati.
- **Test:** test esistenti delle viste, `node --check`, prova manuale a 375px; screenshot nella PR.
- **Rischi:** regressioni visive su C1/C2/Zona/Cestino: modifiche limitate ai file Rete.
- **Autorizzazione:** sì.

Moduli preservati in tutte le fasi: C1, C2, C3, Zona, Voice, Censimento, edifici, Cestino. Nessun nuovo servizio, nessun costo, nessun refactoring.

---

## 8. Decisioni del proprietario prima dello sviluppo

- **D1 — Chi è "Super Admin" per la Rete.** Le mutazioni di stato (agenzie, operatori, membership, territori, alias) vanno riservate al **solo Super Admin generale designato** (consigliato) o restano a tutti i platform admin? Stessa domanda per approvazione/rifiuto C2, oggi aperta a ogni platform admin.
- **D2 — Letture della Rete.** Gli altri platform admin possono continuare a consultare la Rete in sola lettura?
- **D3 — Ruoli.** Confermare: Supervisore = `agency_owner`; Direttore = `agency_admin`; Agente/Collaboratore = `agent`. Se "collaboratore" o "direttore" devono essere ruoli distinti, va deciso ora (sarebbe una migration del CHECK).
- **D4 — Requisiti minimi di un'agenzia attiva.** Quali sono obbligatori: titolare attivo? almeno un Comune assegnato? configurazione (fuso, lingua)? dati anagrafici (oggi non esistono colonne per ragione sociale/P.IVA)? Un'agenzia nuova nasce `suspended` finché i requisiti non sono soddisfatti?
- **D5 — Assegnazioni comunali alla sospensione/archiviazione di un'agenzia.** (a) restano invariate e il Comune passa all'agenzia di default con segnalazione in Rete (comportamento attuale, reso visibile); (b) vengono sospese con l'agenzia; (c) all'archiviazione vengono revocate. Consigliato: sospensione → (a) con segnalazione; archiviazione → richiede che il Super Admin trasferisca o revochi prima esplicitamente ciascun Comune (nessun automatismo).
- **D6 — Servizi verso terzi di un'agenzia sospesa:** prenotazioni pubbliche, promemoria appuntamenti, sincronizzazione calendario, portale proprietari. Si fermano tutti, alcuni o nessuno?
- **D7 — Lead da stime già salvate e site sync** di un'agenzia sospesa: continuano a scrivere nell'agenzia d'origine (oggi) o vengono messi in attesa?
- **D8 — Record assegnati a un operatore disabilitato.** Restano assegnati (consigliato, nessun trasferimento automatico) e la Rete mostra solo l'elenco per una riassegnazione manuale del titolare?
- **D9 — Operatore archiviato.** È sufficiente `disabled`, o serve un valore distinto `archived` per toglierlo dalle liste (migration del CHECK)?
- **D10 — Sospensione vs archiviazione dell'agenzia.** Archiviazione solo da sospesa (consigliato) o anche diretta da attiva?
- **D11 — Consultazione storica.** Approvare l'acting in sola lettura del Super Admin nelle agenzie sospese/archiviate (consigliato) come unico modo di consultarne i dati.
- **D12 — Cestino di agenzie non attive.** La purge definitiva di elementi di un'agenzia sospesa/archiviata resta consentita al Super Admin o va bloccata?
- **D13 — Designazione del Super Admin.** Mantenere la variabile d'ambiente (consigliato) e la procedura §6.2.
- **D14 — Visibilità territoriale C1 di un'agenzia d'origine sospesa.** L'agenzia competente continua a vederne gli immobili in sola lettura (oggi sì)?
- **D15 — Esecuzione di ADM-4 (migration 105).** Autorizzazione separata e verifica preliminare con SELECT sul catalogo di `stima360_db_test`.

---

**STOP.** Fine di ADM-0. Nessun codice applicativo, migration, commit o push. ADM-1 non è stata avviata e attende autorizzazione esplicita.
