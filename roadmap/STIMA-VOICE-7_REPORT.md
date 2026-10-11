# STIMA Voice Fase 7 — preparazione dell'attivazione controllata su TEST

Data: 2026-10-11 · Base: `core-0.1-test` @ `0c84397` (= origin) · `main` @ `c3af7d3`, invariato.
Stato: **PRONTO PER REVIEW. Nulla è stato eseguito su Render, sul database TEST o su PROD.** Nessun commit, nessun push, nessuna migrazione applicata, nessuna variabile cambiata, STIMA Voice spento.

## 1. Stato di preparazione e problemi trovati

**Pronto:**
- elenco pilota per operatore;
- runbook in 11 passi con controlli, arresti e ritorno allo stato sicuro;
- tre SQL provati: preflight in sola lettura, verifica dopo le migrazioni, interruttore dell'agenzia;
- script di certificazione live (`--plan-only`, completo, `--verify-off`);
- patch del montaggio.

L'intero runbook è stato **eseguito per intero in locale**, su un PostgreSQL usa-e-getta chiamato `stima360_db_test`, con:
- lo schema reale fino alla 097, come il TEST;
- il runner vero `p26_migrate.py` (`status`, `plan`, `apply`);
- l'app vera `main.app`, con il router montato in memoria come nella patch;
- un fornitore AI simulato, compatibile OpenAI, su HTTPS: il client HTTP reale di `voice/provider.py`.

Risultato della certificazione locale: **52 PASS / 0 FAIL / 0 BLOCKED.**

**Problemi trovati:**

1. **Mancava il controllo per singolo operatore.** L'abilitazione esisteva solo per agenzia: «una sola agenzia e un solo operatore» non era realizzabile. Aggiunto `STIMA_VOICE_PILOT_USER_IDS`:
   - un valore non valido spegne Voice per tutti;
   - non scavalca mai l'agenzia spenta né il blocco del platform admin.
2. **Il montaggio di `main.py` non è applicato.** Il sistema di sicurezza dell'ambiente ha bloccato la modifica di `main.py` («scrittura di feature flag»). Non l'ho aggirata. La modifica è pronta come patch (`STIMA-VOICE-7_MOUNT_main.patch`, `git apply --check` pulito).
3. **Il montaggio richiede altri 5 cambi nello stesso commit**, misurati su un clone con la patch (vedi §3). Uno non è banale: la matrice ostile P26-6 **non ammette esenzioni**, quindi serve un certificatore VOICE «solo rifiuti» in `scripts/p26_6_live_cert.py`.
4. **La cancellazione dei trascritti scaduti non è pianificata.** `purge_expired_transcripts()` esiste, ma nessuno la chiama. Durante il pilota si esegue a mano (runbook). Una pianificazione automatica richiede la tua approvazione.
5. **Testi del server senza accenti.** Nell'interfaccia compare per esempio «confermi che e' questo contatto?» (preesistente, Fase 5). È cosmetico e non l'ho toccato.
6. **Il database TEST è destinato a diventare il database operativo del CRM** (decisione dell'08/10). La certificazione lascia nel Cestino:
   - 2 contatti sintetici, 1 immobile e 1 edificio;
   - 1 appuntamento annullato e 1 «Vende» chiuso;
   - 1 collegamento proprietario: per D-A non si annulla con la voce;
   - le righe del registro vocale, append-only.

   Nessuna DELETE. Va deciso se è accettabile, oppure se questi residui rientrano nella procedura CRM-PULIZIA.

## 2. File

**Modificati (4):**
- `voice/service.py`: elenco pilota fail-closed, controllato prima di qualunque accesso al database;
- `voice/router.py`: `VOICE_PILOT_USER_REQUIRED` → 403;
- `static/os_shell/assets/voice/voice-model.js`: messaggio comprensibile per il nuovo codice;
- `README_VOICE.txt`: variabile pilota, patch del montaggio, runbook, spegnimento.

**Nuovi (9):**
- `tests/test_voice_7_activation_postgres.py`: 14 test (elenco pilota, 12 rotte senza sessione, spegnimento a metà comando);
- `tests/test_voice_7_live_cert_script.py`: 12 test offline dello script;
- `scripts/voice_7_live_cert.py`: certificazione live su TEST;
- `roadmap/STIMA-VOICE-7_TEST_RUNBOOK.md`;
- `roadmap/STIMA-VOICE-7_1_preflight_readonly.sql`;
- `roadmap/STIMA-VOICE-7_2_postapply_readonly.sql`;
- `roadmap/STIMA-VOICE-7_3_pilot_switch.sql`;
- `roadmap/STIMA-VOICE-7_MOUNT_main.patch` (non applicata);
- questo rapporto.

`main.py`, le migrazioni, il CRM, il censimento e il sito non sono toccati.

## 3. Test

| Prova | Esito |
|---|---|
| Voice, tutti i `tests/test_voice_*.py` (working tree) | **549 passati** |
| Nuovi Fase 7 (attivazione + script) | **26/26** |
| Regressione completa, Fase 7 committata in un clone usa-e-getta (mai pubblicato, cancellato) | 593 falliti / 11.032 passati / 124 saltati / 342 errori. **Insieme dei fallimenti identico, per nome, alla baseline Fase 6**: 935 id, **0 nuovi, 0 spariti**. Preesistenti: soprattutto `document.createElementNS` del DOM simulato e le fixture `agent_working_hours` |
| Stessa regressione + patch del montaggio | baseline + **5**: `test_voice_1::test_05`, `test_voice_5::test_e4`, `test_voice_4::test_n1`, `test_p26_5::test_7`, `test_p26_6_live_cert_script::test_4`. Sono tutte sentinelle del perimetro, nessun difetto di prodotto |
| Prova generale locale del runbook | passi 1, 3, 5, 7, 8 (`--plan-only`), 9 e 10 eseguiti: tutti **PASS**. Passo 6 (fornitore configurato, Voice ancora spento) coperto dal test `st2`; passo 2 (backup) solo su Render |
| Certificazione locale | **52 PASS**: A, B e C completi, S.1–S.11, U.1–U.3, pulizia, sessioni revocate |
| Interruttori | spento per agenzia (senza riavvio) → `VOICE_DISABLED`; globale → `VOICE_GLOBALLY_DISABLED`; elenco pilota con refuso → nessuno. Sempre nessuna scrittura e CRM raggiungibile |
| Log del servizio | nessun testo dettato, nome, chiave o `Bearer` |
| Fornitore | richiesta conforme a chat completions (`model`, `temperature 0`, `system` + `user`, `json_schema`); un'interpretazione per comando; nessuna ripetizione per la stessa chiave |
| Interfaccia vera, Chromium a viewport iPhone 13, backend vero | menu «Voce» solo per il pilota; il collega legge «non è attivo»; conflitto mostrato senza «Conferma»; testo → piano → conferma → eseguito → «Annulla questo passo» → «Annullato»; nessuno scorrimento orizzontale; Agenda, Contatti, Immobili ed Edifici invariati; 0 errori JavaScript |
| `git diff --check`, `node --check` | puliti |

**Non eseguiti:** nulla su Render TEST. Fornitore AI reale: non c'è una chiave, e la rete di questo ambiente non lo raggiunge. Safari su iPhone fisico.

**Chromium simulato contro iPhone reale.**
- Provato in Chromium: layout a 390 px, pulsanti da 48 px, testo a 16 px, flusso completo, permesso del microfono negato (simulato).
- Solo su iPhone: resa di Safari, tastiera e dettatura di iOS, rotazione, `webkitSpeechRecognition` (spento finché non si decide).

## 4. Piano di attivazione TEST

`roadmap/STIMA-VOICE-7_TEST_RUNBOOK.md`:

0. Decisioni.
1. Salute del CRM (sola lettura).
2. Backup e ripristino.
3. Migrazioni 098 e 099.
4. Montaggio (patch + 5 sentinelle).
5. Deploy con Voice spento e `--verify-off`.
6. Fornitore AI.
7. Agenzia pilota + operatore pilota.
8. `STIMA_VOICE_ENABLED=1`, solo testo, e `--plan-only`.
9. Certificazione dei tre scenari e prova da iPhone.
10. Spegnimento (sempre ammesso).
11. Microfono, solo dopo decisione.

## 5. Migrazioni e configurazioni necessarie

**Migrazioni:** 098 poi 099, con `scripts/p26_migrate.py apply`. Creano solo tabelle nuove: 5 tabelle e 6 trigger, nessuna riga esistente toccata. Dipendono da `agencies` e `operator_users`; le operazioni vocali si appoggiano allo schema fino alla 097, come controlla il preflight §4. Il TEST ha PostgreSQL 18, il locale 16: nessuna sintassi specifica di versione.

**Variabili Render:**

| Variabile | Uso |
|---|---|
| `STIMA_VOICE_AI_URL` | endpoint, solo HTTPS |
| `STIMA_VOICE_AI_MODEL` | modello, nessun default |
| `STIMA_VOICE_AI_KEY` | chiave, come secret |
| `STIMA_VOICE_AI_TIMEOUT` | facoltativa |
| `STIMA_VOICE_PILOT_USER_IDS` | operatore pilota |
| `STIMA_VOICE_ENABLED=1` | accensione globale |
| `STIMA_VOICE_BROWSER_SPEECH` | **non impostata** |

**Database:** una riga in `voice_agency_settings` per l'agenzia pilota, con la modalità `auto` o `review`.

## 6. Rischi residui e rollback

**Rischi:**
- **Il modello reale non è deterministico.** Se interpreta male, il comando non si esegue o chiede. Lo script lo riporta come FAIL o BLOCKED, mai come PASS.
- **Il testo dettato va al fornitore**, ed è un dato personale: serve il DPA. Nessun dato del CRM entra nel prompt.
- **Residui sintetici** nel database destinato al CRM operativo (§1.6).
- **Isolamento fra operatori dal vivo:** con l'elenco pilota attivo, colleghi e altre agenzie sono fermati dall'elenco (403) prima del controllo di proprietà. L'isolamento per proprietà (404) è provato dai test locali.
- **Trascritti:** cancellazione manuale finché non viene pianificata.
- **Microfono del browser:** Google o Apple, da decidere.

**Rollback:**
- Lo stato sicuro è **Voice spento**:
  - `voice_agency_settings.enabled=FALSE` è immediato;
  - togliere `STIMA_VOICE_ENABLED` agisce in modo globale, con riavvio.
- Il codice montato resta inerte.
- Le migrazioni non si annullano: il runner è forward-only e la down della 098 rifiuta se il registro contiene comandi. Non serve annullarle.
- Il codice: revert del commit del montaggio.
- Il backup del passo 2 è l'ultima risorsa.

## 7. Operazioni che richiedono una tua autorizzazione specifica

1. Commit e push di questa preparazione (13 file); il push avvia il deploy su TEST.
2. Il montaggio: applicare la patch a `main.py` (bloccata qui dal sistema di sicurezza: va autorizzata e poi eseguita da te o con un permesso esplicito) e preparare il certificatore VOICE della matrice P26-6 più le altre 4 sentinelle.
3. Le migrazioni 098 e 099 sul database TEST.
4. Le variabili Render: fornitore, elenco pilota, `STIMA_VOICE_ENABLED`.
5. La riga dell'agenzia pilota e la sua modalità.
6. La certificazione live, con i suoi dati sintetici e i residui nel Cestino.
7. La decisione su Google e Apple per il microfono.
8. La pianificazione della cancellazione dei trascritti.

Non iniziata la Fase 8.

---

## Aggiornamento 2026-10-11 — completamento autorizzato del codice

Autorizzati: montaggio, 5 sentinelle, commit e push su `core-0.1-test`. **Non** autorizzati: migrazioni, database, variabili Render, agenzie e operatori, fornitore reale, certificazione con scritture, microfono, PROD.

**Fatto**

- La patch `STIMA-VOICE-7_MOUNT_main.patch` è applicata a `main.py`, con `git apply`. La protezione dell'ambiente ora lo consente: nessun aggiramento.
- Sentinelle aggiornate, senza indebolirle:
  - `test_voice_1::test_05` e `test_voice_5::test_e4`: in `main.py` sono ammesse **solo** la riga di import e quella di mount;
  - `test_voice_4::test_n1`: `main.py` diverso dalla base solo per righe aggiunte, verificate riga per riga; tutti gli altri file protetti restano identici;
  - `test_p26_5`: `/api/voice` è in `TENANT_PREFIXES`, quindi ogni rotta Voice deve dichiarare il cookie e **solo** il cookie (test 6);
  - `test_p26_6`: dominio **VOICE** nella matrice ostile con `certify_voice`, «solo rifiuti».
- Cosa verifica `certify_voice`:
  - 12 rotte anonime → 401 e 12 con Basic finto → 401;
  - `/status` → esattamente due booleani;
  - `agency_id` o `actor_user_id` nel corpo → 422;
  - il comando di un'altra agenzia, letto in sola lettura, oppure un id inesistente → 403 (spento) o 404 (acceso), mai il comando;
  - registro vocale invariato.
- Due prove offline nuove:
  - la sezione VOICE gira davvero ed è tutta PASS;
  - un'app che restituisce il comando altrui fa **fallire** la matrice.

**Voice resta spento con la configurazione attuale**

Su TEST non ci sono le variabili `STIMA_VOICE_*` e le tabelle della 098 non esistono. Quindi `guard` rifiuta ogni rotta con `VOICE_GLOBALLY_DISABLED` prima di toccare il database, `/status` risponde `{enabled: false, speech_input: false}` e il menu «Voce» non compare.
