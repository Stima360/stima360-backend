# COLLAUDO-FINALE-A-H — Completamento dei punti rimasti aperti

Data: 7 ottobre 2026, 16:30–17:00 (Europe/Rome). Solo **TEST**. Fonte di partenza: `roadmap/COLLAUDO-FINALE-A-H_REPORT.md`.

Riletti prima di iniziare, per le aspettative esatte:
- i report e i runbook di PERTINENZE-1, CESTINO-CONTATTI-1, CESTINO-EDIFICI-1 e CESTINO-RICHIESTE-1;
- in particolare le matrici «Chi può fare cosa», gli «Storico operativo reale», i possibili doppioni e il portale proprietario.

> **Esito in una riga.** Due punti chiusi dal vivo: possibili doppioni Contatti e possibili doppioni Edifici. Nessuna regressione. Restano **NON VERIFICATO** permessi agente, separazione tra agenzie, incarico da acquisizione, portale proprietario (completo) e registro `record_lifecycle_events`; l'integrazione col sito resta **RINVIATA**. **A–H non è completamente collaudato.** Questo documento non è un'approvazione alla produzione.

## 1. Ambiente verificato

- **CRM:** `https://stima360-backend-test.onrender.com/os/`, badge «AMBIENTE TEST». Database `stima360_db_test`, secondo la configurazione del servizio TEST.
- **Sessione:** platform admin (Giorgio), Superadmin dentro l'agenzia 1 «STIMA360 Alba Adriatica» (`/api/platform/me` → `acting_agency_id = 1`). Nessun cambio di contesto in questa sessione.
- **Strumento:** browser integrato; UI vera e letture API nella stessa sessione.
- **Esclusi:** PROD mai aperta; sito pubblico mai usato.

## 2. Branch iniziale

`core-0.1-test`. Working tree pulito, allineato a `origin/core-0.1-test`.

## 3. Commit applicativo iniziale

- **HEAD:** `7454585` (docs: report COLLAUDO-FINALE-A-H).
- **Codice applicativo:** `71d2488`. `git diff --stat 71d2488 origin/core-0.1-test` tocca solo 9 file in `roadmap/`.

## 4. Commit effettivamente servito da Render TEST

- Non c'è un endpoint di versione, e da qui non si legge `RENDER_GIT_COMMIT`.
- Verificato dagli asset serviti:
  - `/os/assets/core/router.js` contiene `nuovaPagina`, il fix di `71d2488`;
  - gli asset di FASE H sono presenti (`restoreBuyRequest`, scheda `richieste`).
- Il deploy servito contiene quindi il codice di `71d2488`. L'ultimo deploy è di `7454585`, che aggiunge solo documenti.
- Nessuna modifica applicativa successiva.

## 5. Stato migration 087–092

**NON letto direttamente.** Da questa sessione non ci sono né accesso SQL a TEST, né shell Render, né una rotta che esponga `schema_migrations`. Non ho eseguito `apply` né down.

Indizi live coerenti con «applicate» (nessuno contraddittorio):

| Migration | Indizio live |
|---|---|
| 087 | `sea_position`, `sea_distance` e `sea_view` salvati e riletti su IMM-97 (colonne nuove della 087) |
| 088 | nessun indizio osservabile dal CRM senza invii dal sito |
| 089 | pertinenze autonome, `is_pertinenza` su IMM-98 |
| 090 | Cestino Contatti operativo, nessun 503 `TRASH_NOT_INSTALLED` |
| 091 | Cestino Edifici operativo, 409 `BUILDING_IN_TRASH` |
| 092 | Cestino Richieste operativo |

Per la lettura diretta, sola lettura, da shell Render TEST:

```bash
python scripts/p26_migrate.py status --operator "giorgio.larasa"
# oppure
psql ... -c "BEGIN READ ONLY; SELECT version, applied_at, rolled_back_at FROM schema_migrations WHERE version >= '087' ORDER BY version; ROLLBACK;"
```

**Atteso:** 087–092 `applied`, nessuna `pending` e nessuna `PROBLEM`.

## 6. Prove già considerate PASS e NON rieseguite

Le seguenti sono state usate solo come contesto, senza ripeterle:
- B Edifici (creazione, riuso, filtri, contatori, navigazione);
- C Immobili / creazione guidata;
- E Pertinenze;
- F, G e H, più il Cestino Immobili, come amministratore;
- D lato CRM;
- A per l'incarico storico;
- UI desktop e smartphone;
- fix del router.

Due di queste prove sono state toccate di nuovo solo come effetto collaterale delle prove nuove:
- il Cestino Edifici, con spostamento e ripristino dell'edificio #5;
- il Cestino Contatti, con spostamento e ripristino di #168.

Entrambe si sono comportate come nel collaudo precedente.

## 7. Prove nuove eseguite

| PUNTO | ESITO | PROVA | NOTE |
|---|---|---|---|
| Permessi agente Contatti | **NON VERIFICATO** | Organico dell'agenzia 1 letto in sola lettura (`/api/platform/agencies/1/operators`) | Gli agenti di prova (`agent.a`, `agent.a2@test.stima360.local`) hanno la membership **revocata**. L'unico agente attivo è una persona reale. Non ho modificato membership, e il login con password di un altro account non lo posso eseguire io. Vedi §16.1. |
| Permessi agente Edifici | **NON VERIFICATO** | Come sopra | Stessa causa. |
| Permessi agente Richieste | **NON VERIFICATO** | Come sopra | Stessa causa. |
| Separazione tra agenzie | **NON VERIFICATO** | Nessuna nuova prova | Nessun account di prova attivo in un'altra agenzia. L'agenzia 2 «Agenzia B (TEST)» è archiviata. Il tentativo precedente di contesto Superadmin nell'agenzia 35 era stato rifiutato dai permessi della sessione: non ripetuto né aggirato. Resta verificato solo il 401 senza sessione (collaudo precedente). Vedi §16.2. |
| Incarico da acquisizione | **NON VERIFICATO** | Sola lettura | Google Calendar su TEST: `configured: true`, `enabled: true`; l'utente corrente non è collegato, e le connessioni degli altri utenti non sono leggibili da nessuna rotta. Incarichi nell'agenzia 1: 0. Acquisizioni (`/api/acquisitions`): 0. Nessun immobile con origine acquisizione o firma LMC-15: la scansione in sola lettura dei 24 immobili trova solo incarichi storici. Crearne uno richiede un appuntamento e non posso garantire zero sincronizzazioni esterne, quindi **fermato** come da istruzioni. Vedi §16.3. |
| Possibili doppioni Contatti | **PASS** | Vedi sotto | Come atteso dal runbook F §5.7. |
| Possibili doppioni Edifici | **PASS** | Vedi sotto | Come atteso dal runbook G §5.7. |
| Portale proprietario | **NON VERIFICATO** (flusso completo) | Sola lettura sui due account portale esistenti | Verificato solo il blocco, vedi sotto. Il flusso completo non si può fare senza toccare dati preesistenti non di prova, quindi nessun clic su «Disattiva accesso». Vedi §16.4. |
| record_lifecycle_events | **NON VERIFICATO** | — | Nessun accesso SQL in sola lettura da questa sessione. Query pronta in §16.5. |
| Regressioni | **PASS** | Suite locali, più uso live | Nessun rosso. Live: Cestino Edifici, Cestino Contatti, creazione guidata e router (nessun contenuto di pagina sbagliato) si comportano come nel collaudo precedente. |

**Dettaglio — Possibili doppioni Contatti (PASS).**
1. Creato #168 «COLLAUDO CLAUDE Doppione A», email `collaudo-claude-doppione@example.invalid`, nessun telefono. Il controllo dice «0 messaggi in coda».
2. #168 spostato nel Cestino dalla UI: «Record di prova».
3. Creato #169 «COLLAUDO CLAUDE Doppione B», stessa email.
4. Cestino › Contatti › «Ripristina» su #168. La UI dice: «COLLAUDO CLAUDE Doppione A è di nuovo fra i contatti. Possibili doppioni attivi, da controllare: COLLAUDO CLAUDE Doppione B — stessa email», con il collegamento `#/contatti/169`, poi «Nessun contatto è stato unito o modificato.».
5. #169 invariato (`updated_at`, email e stato identici).

**Dettaglio — Possibili doppioni Edifici (PASS).**
1. Edificio #5 (vuoto, COLLAUDO) spostato nel Cestino dalla UI.
2. Creazione guidata a Tortoreto, «Via COLLAUDO Cestino» 9: fra i candidati c'è «Nessun edificio trovato» (#5 escluso, corretto).
3. Creato #6 «COLLAUDO CLAUDE Edificio Doppione».
4. «Ripristina» su #5. La UI dice: «Possibili doppioni attivi, da controllare: COLLAUDO CLAUDE Edificio Doppione · Via COLLAUDO Cestino 9, Tortoreto — stesso indirizzo», con il collegamento `#/edifici/6`, poi «Nessun edificio è stato unito o modificato.».
5. #6 invariato. #5 torna con lo stesso id.

**Dettaglio — Portale proprietario (sola lettura).** Gli account portale esistenti sono 2.

| Contatto | Account | Perché non è usabile per la prova |
|---|---|---|
| #2 «Stima Giorgio» | #3, attivo | Email `stima360.it`: non è un contatto esclusivamente di prova. |
| #1 «Mario Test» | #29, invitato | Ha anche 2 lead, 3 richieste e 5 task aperti, più storico. Arrivare al Cestino vorrebbe dire chiudere molti dati preesistenti. |

Sul contatto #1, «Elimina…» (foglio solo aperto e chiuso) mostra correttamente:
- il blocco «Ha un accesso al portale proprietario attivo: disattivalo prima («Disattiva accesso»)», con la voce «Accesso al portale proprietario · invitato»;
- il bottone **«Disattiva accesso»** (azione `owner_account_disable`, `allowed: true`);
- la conferma disabilitata.

## 8. Bug nuovi trovati

**Nessun bug nuovo in A–H.**

**Osservazione preesistente, fuori da A–H** (P27-3, commit `6589ff7` del 13/09/2026). Non corretta e da decidere.
- `GET /api/platform/agencies/{id}/operators` restituisce in `operator.status`, `operator.created_at` e `operator.updated_at` i valori della **membership**, non dell'operatore.
- Causa: la JOIN in `platform_admin/operators_repository.py::list_agency_operators` seleziona `u.status, u.created_at, u.updated_at` e poi `m.status, m.created_at, m.updated_at` con gli stessi nomi; il `RealDictCursor` tiene l'ultimo. Il docstring di `_agency_operator` dice che le colonne collidono solo su `id`.
- Effetto: nell'organico un operatore può apparire «revocato» con `last_login_at` recente (è la sua membership in quell'agenzia a essere revocata, non l'account). È un errore di presentazione, non di sicurezza: il login (`operator_auth`) legge lo stato vero dell'account.
- Non l'ho corretto: è fuori dal perimetro A–H e le istruzioni chiedono di non aprire nuove fasi.

## 9. Fix applicati

Nessuno.

## 10. Test aggiunti

Nessuno.

**Rieseguite in locale** (PostgreSQL 16 locale con `P29_TEST_DSN`, Chromium) sul codice di `71d2488`: tutte le suite delle fasi A–H, il router e il Cestino Immobili (2B3).
- `test_fix_mandate_1*`, `test_edifici_1*`, `test_creazione_guidata_1*`, `test_catalogo_canonico_1*`;
- `test_pertinenze_1*`, `test_cestino_contatti_1*`, `test_cestino_edifici_1*`, `test_cestino_richieste_1*`;
- `test_collaudo_finale_router.py`, `test_delete_arch_2b3*`.

Esito: **254 passed, 0 failed, 0 skipped.**

Un primo giro senza `P29_TEST_DSN` aveva saltato 94 prove PostgreSQL e browser. Non l'ho contato come esito: rieseguito con il DSN.

## 11. Commit eventualmente creati

Solo documentazione: questo report e la nota di rimando in testa a `COLLAUDO-FINALE-A-H_REPORT.md`. Nessun commit applicativo.

## 12. Dati COLLAUDO creati o modificati (agenzia 1, TEST)

| Dato | Azione | Stato finale |
|---|---|---|
| Edificio **#5** «COLLAUDO Edificio Vuoto» | Cestino (Record di prova), poi Ripristina | **attivo**, vuoto, stesso id |
| Edificio **#6** «COLLAUDO CLAUDE Edificio Doppione», Via COLLAUDO Cestino 9, Tortoreto | creato, poi Cestino (Duplicato) | **nel Cestino**, vuoto |
| Contatto **#168** «COLLAUDO CLAUDE Doppione A», `collaudo-claude-doppione@example.invalid`, senza telefono | creato, Cestino, Ripristina, di nuovo Cestino (Record di prova) | **nel Cestino**, automazioni in pausa, nessun messaggio |
| Contatto **#169** «COLLAUDO CLAUDE Doppione B», stessa email | creato, poi Cestino (Duplicato) | **nel Cestino**, automazioni in pausa, nessun messaggio |
| Registro del ciclo di vita | eventi nuovi | edificio 5 trash/restore, edificio 6 trash, contatto 168 trash/restore/trash, contatto 169 trash |
| Contatto #1 «Mario Test» | foglio «Elimina…» aperto e chiuso | **invariato** |

Il dominio `.invalid` è riservato (RFC 2606): non è consegnabile. In più nessun flusso accoda messaggi alla creazione manuale di un contatto:
- le journey si iscrivono solo dopo una mail di stima spedita;
- i promemoria A32 nascono solo da appuntamenti.

I dati del collaudo precedente sono invariati (§5 di `COLLAUDO-FINALE-A-H_REPORT.md`): edificio #4, IMM-93…100, contatto #167, richiesta #67, proposta #55, 3 abbinamenti.

## 13. Conferma PROD mai toccata

Confermato. Solo `stima360-backend-test.onrender.com`. Nessun push fuori da `core-0.1-test`, nessun merge.

## 14. Conferma nessuna comunicazione reale inviata

Confermato:
- nessuna email, WhatsApp o SMS;
- nessun appuntamento, acquisizione o visita creati;
- nessuna sincronizzazione Google provocata;
- la tab Comunicazioni di #167, #168 e #169 dice «Nessun messaggio inviato o programmato».

## 15. Conferma nessuna migration riapplicata

Confermato: nessun `apply`, nessuna down, nessuna scrittura SQL.

## 16. Lista ESATTA di tutto ciò che resta prima della produzione

### 16.1 Permessi agente F / G / H (NON VERIFICATO)

**Serve:** un account **agente di prova** attivo nell'agenzia 1. Per esempio un operatore nuovo `@test.stima360.local` con ruolo agent, creato da Giorgio: non va riattivata o cambiata una persona reale.

**Come:** Giorgio entra con quell'account nel browser integrato (io non inserisco password). Poi si eseguono:
- runbook F §5.5;
- runbook G §5.6;
- runbook H §5.5.

**Attesi:**
- spostamento consentito senza storico;
- Cestino filtrato sui propri record;
- ripristino solo dei propri;
- 403 `HISTORY_REQUIRES_ADMIN` con «Serve un amministratore» quando c'è storico;
- «Può ripristinarlo chi lo ha spostato o un amministratore.» senza bottone sui record spostati dal titolare.

### 16.2 Separazione tra agenzie (NON VERIFICATO)

**Serve una di queste:**
- un account di prova attivo in una seconda agenzia **attiva**;
- l'autorizzazione esplicita a usare, in sola lettura, il contesto Superadmin nell'agenzia 35.

**Prova:** GET per id sui record COLLAUDO dell'agenzia 1 (immobile 93/97, edificio 4/5, contatto 167, richiesta 67, `mandates`) ed elenchi del Cestino. **Atteso:** 404 o elenchi senza quei record.

### 16.3 Incarico da acquisizione (NON VERIFICATO)

**Serve la garanzia di zero effetti esterni**, in uno di questi modi:
- Google Calendar **disabilitato** sul servizio TEST per la durata della prova;
- una lettura SQL di `calendar_connections` che mostri nessuna connessione attiva per l'agente a cui verrà assegnato l'appuntamento.

**Poi:** un'acquisizione su un contatto COLLAUDO **senza email** («Genera incarico»), e le quattro verifiche:
1. «Dati da completare» in Incarichi;
2. «Apri incarico» nel blocco del Cestino;
3. rifiuto 400 dello svuotamento del tipo;
4. blocco da firma LMC-15.

### 16.4 Portale proprietario (NON VERIFICATO, flusso completo)

**Serve:** un contatto **esclusivamente di prova**, senza altri processi aperti, con un accesso portale attivo.

Il punto da chiarire prima: creare o attivare un accesso portale può spedire un invito. Va quindi stabilito come ottenerlo senza invii, oppure va fornito un account già esistente.

**Poi:** runbook F §5.6, cioè blocco → «Disattiva accesso» → Cestino → ripristino → accesso ancora disattivato.

### 16.5 Registro e stato migration (NON VERIFICATO, letture SQL di Giorgio)

Da shell Render TEST, sola lettura:

```sql
BEGIN READ ONLY;
SELECT version, applied_at, rolled_back_at FROM schema_migrations WHERE version >= '087' ORDER BY version;
SELECT id, entity_type, entity_id, action, reason_code, actor_user_id, occurred_at
  FROM record_lifecycle_events
 WHERE (entity_type, entity_id) IN (('property',95),('building',5),('building',6),
                                    ('contact',167),('contact',168),('contact',169),('buy_request',67))
 ORDER BY id;
SELECT tgname FROM pg_trigger WHERE tgname = 'trg_record_lifecycle_events_append_only';
ROLLBACK;
```

**Attesi:**
- 087–092 applicate;
- per ciascun record, eventi `trash` / `restore` alternati e in ordine di tempo:
  - property 95: trash, restore;
  - building 5: trash, restore, trash, restore;
  - building 6: trash;
  - contact 167: trash, restore;
  - contact 168: trash, restore, trash;
  - contact 169: trash;
  - buy_request 67: trash, restore, trash, restore;
- il trigger append-only presente.

### 16.6 Integrazione sito → CRM (RINVIATO)

Resta da fare quando esisterà un percorso sito TEST → CRM TEST (runbook D §6). Non dichiarabile PASS.

### 16.7 Osservazione preesistente da decidere

Elenco operatori della piattaforma, collisione di colonne (§8): correggere in una fase propria oppure accettare.

### 16.8 Produzione (decisione separata)

Fuori da questo collaudo:
- applicare 087–092 su PROD con il runner e i runbook;
- deploy;
- smoke su PROD.

---

**PRONTO PER REVIEW — COMPLETAMENTO COLLAUDO A–H**

A–H **non** è completamente collaudato: restano 5 punti NON VERIFICATO (permessi agente, separazione tra agenzie, incarico da acquisizione, portale proprietario, registro / stato migration letto da SQL) e 1 RINVIATO (sito). Nessuna fase nuova iniziata.
