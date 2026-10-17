# STIMA360 CRM — ADM-2: anteprima delle dipendenze («Verifica conseguenze»)

- Branch: `core-0.1-test`. Base `817cd99` (ADM-1), verificata uguale al remoto prima di iniziare.
- Perimetro: solo ADM-2.
  - Nessuna migration, nessuna tabella nuova.
  - Nessuna scrittura su `stima360_db` o su `stima360_db_test`.
  - Nessun push né deploy.
  - Nessuna modifica a C1, C2, C3, lead, email o follow-up.
- Collaudo: PostgreSQL 16 locale e usa-e-getta (`localhost:55432`), mai il database TEST operativo.

## 1. File modificati

| File | Modifica |
|---|---|
| `platform_admin/impact_repository.py` (nuovo) | Solo `SELECT`: conteggi per agenzia, operatore, territorio, alias; controllo `to_regclass` delle tabelle |
| `platform_admin/impact_service.py` (nuovo) | Transazione `SET TRANSACTION READ ONLY`, mai committata; composizione dell'anteprima |
| `platform_admin/router.py` | 4 rotte `GET .../impact` con `require_super_admin_context` (guardia ADM-1) |
| `static/os_shell/assets/components/network.js` | `mostraConseguenze()`: dialogo di sola lettura, costruito con DOM e `textContent` |
| `static/os_shell/assets/views/rete-agenzia.js` | Pulsante «Verifica conseguenze» in Panoramica, su ogni operatore e su ogni assegnazione non revocata |
| `static/os_shell/assets/views/rete-territorio.js` | Pulsante «Verifica conseguenze» su ogni alias attivo |
| `static/os_shell/assets/app.css` | 5 regole additive per il dialogo (scorrimento verticale e a capo su smartphone) |

**Test**

- Nuovo: `tests/test_adm2_impact_postgres.py` (7 test).
- Sentinelle aggiornate, come ogni fase precedente:
  - `tests/test_adm1_super_admin_guard.py`: le rotte Rete protette passano da 23 a 27;
  - `tests/test_p27_6_lead_routing.py::test_i2`: inventario esatto delle rotte Platform, con le 4 GET;
  - `tests/test_voice_4_undo_postgres.py`: dichiarato `app.css` come file toccato da ADM-2.

I flussi di conferma esistenti (Sospendi, Archivia, Revoca, Accesso, Membership) **non sono stati modificati**. «Verifica conseguenze» è un pulsante a parte, con un attributo proprio (`data-impatto-*`), quindi non interferisce con i gestori esistenti.

## 2. Endpoint

Tutti `GET`, riservati al Super Admin generale designato (`require_super_admin_context` di ADM-1):

| Endpoint | Anteprima |
|---|---|
| `/api/platform/agencies/{agency_id}/impact` | agenzia |
| `/api/platform/operators/{operator_user_id}/impact` | operatore (agente, Direttore, Supervisore) |
| `/api/platform/agencies/{agency_id}/territories/{assignment_id}/impact` | assegnazione comunale |
| `/api/platform/aliases/{alias_id}/impact` | alias territoriale |

Risposta: `{kind, target, scenario, preserved, access_lost, may_stop, needs_decision, not_verified, notice}`. Ogni voce ha la forma `{key, label, count, note}`.

- Le cinque liste corrispondono alle categorie richieste: dati conservati, accessi disabilitati, funzionalità che possono interrompersi, decisioni amministrative, più ciò che non si è potuto verificare.
- `count: null` significa che il dato non è rilevabile in quel database: la voce finisce in `not_verified` e non viene mai taciuta.
- L'anteprima non dichiara mai un'operazione «sicura». Il campo `notice` ricorda sempre che le verifiche vere si ripetono, in transazione, al momento dell'operazione (ADM-1).

## 3. Dipendenze coperte

**Agenzia**

- **Conservati:**
  - immobili, edifici e contatti, vivi e nel Cestino;
  - lead, richieste d'acquisto, appuntamenti (storico), acquisizioni;
  - assegnazioni territoriali e membership;
  - immobili dell'agenzia letti da agenzie competenti (C1).
- **Accessi**, solo se l'agenzia è attiva: Supervisori, Direttori, agenti e collaboratori che accedono oggi; sessioni aperte dei membri; amministratori entrati nell'agenzia.
- **Possono interrompersi:**
  - Comuni che ricevono le stime pubbliche, con la destinazione di riserva (agenzia di sistema e suo stato);
  - stime ricevute negli ultimi 90 giorni;
  - immobili di altre agenzie letti per competenza (C1).
- **Decisioni:**
  - agenzia di sistema (operazione rifiutata);
  - archiviazione solo dopo la sospensione;
  - appuntamenti futuri e acquisizioni aperte;
  - proposte C2 pendenti, sia come origine sia come agenzia competente;
  - richieste di eliminazione edifici pendenti;
  - link di prenotazione pubblica, calendari collegati e percorsi di comunicazione attivi, che oggi **non** si fermano con la sospensione (adeguamento previsto in ADM-3).

**Operatore** (Supervisore = `agency_owner`, Direttore = `agency_admin`, agente o collaboratore = `agent`)

- Agenzie di appartenenza, membership e ruoli, in ogni stato.
- Contatti, lead, immobili e acquisizioni assegnati: restano assegnati, nessun trasferimento.
- Appuntamenti futuri assegnati; link di prenotazione e calendari.
- Sessioni aperte.
- Proposte C2 e richieste di eliminazione edifici pendenti presentate dall'operatore.
- **Responsabilità territoriale:** se è l'unico Supervisore attivo di un'agenzia, l'anteprima indica i Comuni attivi che quell'agenzia presidia (senza Supervisore non si presentano proposte C2).
- Se l'operatore è il Super Admin designato, l'anteprima dice che la disabilitazione verrebbe rifiutata.

**Assegnazione comunale**

- Agenzia competente e suo stato; Comune e valori dichiarati (alias).
- **Instradamento:**
  - se oggi è attivo, le nuove stime andrebbero all'agenzia di sistema;
  - se oggi non è attivo, l'anteprima lo dice;
  - stime per quel Comune negli ultimi 90 giorni.
- **C1:** immobili di altre agenzie nel Comune letti oggi per competenza; immobili propri nel Comune, che restano all'agenzia.
- **Conflitti:** altre assegnazioni attive o sospese per lo stesso territorio; territorio che non è un Comune; proposte C2 pendenti, la cui approvazione verrebbe bloccata; revoca definitiva.

**Alias**

- Valore, territorio, agenzia competente.
- Effetto sull'instradamento e sulla lettura C1 se l'alias viene revocato.
- Revoca definitiva.

**Dichiarato come non verificato:** richieste d'acquisto, abbinamenti, attività e task registrano l'incaricato in testo libero. Non sono collegati a un operatore e non vengono contati.

## 4. Limiti

- L'anteprima è una fotografia: due amministratori possono cambiare i dati subito dopo. Le guardie transazionali di ADM-1 restano l'unica autorità.
- **Registro:** ogni chiamata, come ogni rotta `/api/platform` dal P27-1, scrive la propria riga di **ammissione** in `platform_audit_log` e aggiorna `last_seen_at` della sessione. È il comportamento esistente. L'anteprima in sé non scrive nulla: lo prova anche il database, che rifiuta qualunque scrittura nella sua transazione (`ReadOnlySqlTransaction`).
- Gli elenchi interni sono limitati a 200 righe (territori, alias); i conteggi sono sempre completi.
- «Stime recenti» usa `stime.data` sugli ultimi 90 giorni: è un indicatore del flusso di lead, non una previsione.
- Gli appuntamenti futuri sono quelli in stato `requested`, `scheduled` o `confirmed` con inizio dal momento della lettura. Le acquisizioni aperte sono tutte quelle tranne `acquired` e `lost`.
- Nessuna archiviazione o revoca aggiuntiva, nessun adeguamento di servizi o lead: rinviati ad ADM-3 e oltre.

## 5. Risultati dei test

**Collaudo mirato**: `tests/test_adm2_impact_postgres.py`

```
7 passed in 22.10s
```

| # | Richiesta | Test |
|---|---|---|
| 1 | Solo il Super Admin designato | `test_01`: designato 200 sui 4 endpoint; altro platform admin 403 («Super Admin»); tenant 403; anonimo 401; 404 sugli id inesistenti |
| 2 | Conteggi uguali ai dati | `test_02`: ogni conteggio confrontato con una query SQL indipendente (immobili, edifici, contatti, lead, appuntamenti, acquisizioni, assegnazioni, membership, sessioni, stime, contatti assegnati) |
| 3 | Nessuna modifica | `test_03`: impronta di 16 tabelle e stati di agenzie, assegnazioni, alias e operatori invariati; nel registro solo `platform.admission`; una scrittura dentro l'anteprima viene rifiutata dal database (`ReadOnlySqlTransaction`) |
| 4 | Agenzie sospese, archiviate, di sistema | `test_04` |
| 5 | Dipendenze territoriali | `test_05`: Comune, instradamento verso l'agenzia di sistema, C1, stime recenti, conflitto con un'assegnazione sospesa, alias, Supervisore unico, Super Admin designato |
| 6 | Collegamenti storici invariati | `test_03`, `test_06` (impronta prima e dopo) |
| 7 | UI desktop e smartphone | `test_06[390]`, `test_06[1280]`: Chromium reale con sessione vera. Si aprono le quattro anteprime (agenzia, operatore, assegnazione, alias); il dialogo resta nello schermo, la pagina non scorre in orizzontale e non ci sono errori JavaScript |
| 8 | Nessuna regressione | verifica qui sotto |

**Verifica di regressione (una sola esecuzione)**

- Unitari Rete (P26/P27/P28 + guardia ADM-1): `1 failed, 1155 passed`. L'unico fallimento è il preesistente `test_i1_059...`, quello che pretende la 059 come ultima migration.
- PostgreSQL e browser (ADM-1, ADM-2, C1, C2 API e browser, C3 API e browser, Zona-3 C2, eliminazione edifici API e browser, Cestino API e browser, LMC-15, Voice-4): `1 failed, 157 passed`, **0 saltati**. L'unico fallimento è il preesistente `test_voice_4::test_m1_migration_099...`.
- Suite completa senza DB, su un clone locale con le modifiche committate, confrontata con ADM-1 (`817cd99`): un solo test in più fallito, la sentinella `test_i2` dell'inventario delle rotte. Ho aggiornato quella sentinella e, rieseguito il suo file, resta solo il fallimento preesistente sulla 059. Nessun altro test che passava con ADM-1 fallisce.
- `node --check` sui file JS modificati: ok. `git diff --check`: pulito.

Restano invariati, e non dipendono da ADM-2:

- i test runtime in Node, il cui DOM simulato non implementa `createElementNS` (CODE FAILURE del banco di prova);
- i test `*_postgres_real`, saltati perché `initdb` non gira come root (ENVIRONMENT).

## 6. Rinviato

- **ADM-3**: comportamento di prenotazioni, promemoria, calendario, portale proprietari e follow-up dei lead per le agenzie sospese.
- **ADM-4**: migration 105.
- **ADM-5**: registro e consultazione storica.
- **ADM-6**: sostituzione del Super Admin.
- **ADM-7**: interfaccia mobile completa.

**STOP.** ADM-2 è chiusa con un commit locale. Nessun push o deploy. ADM-3 non è stata avviata.
