# P26-3 — Client/Auth Transition: certificazione

**Stato: CERTIFICATA su Render TEST.**

| | |
|---|---|
| Commit applicativo certificato | `94d8eca8c0028fb0ac2a2f5be6fbf944cc8afaa1` |
| Commit dello script di certificazione | `d4679836b238e31ee093d3d54880e72a71932697` |
| Branch | `core-0.1-test` |
| Database reale | `stima360_db_test` |
| Esito | **58 PASS, 0 FAIL, 0 BLOCKED** |
| Residuo dopo il run | 0 utenti, 0 membership, 0 sessioni temporanee |

Questo documento registra l'evidenza. Non la riassume in meglio di com'è: dove
una prova è statica lo dice, dove è locale lo dice, e ciò che P26-3 **non**
chiude è elencato per esteso in fondo.

---

## 1. Che cosa cambia P26-3

La OS Shell teneva username e password in una variabile di modulo e ricostruiva
`Authorization: Basic` a ogni richiesta. Adesso li invia **una volta** a
`/api/operator-auth/login`, non conserva nulla, e lascia al browser un cookie
HttpOnly che il codice di pagina non può leggere né copiare.

Il cambiamento non è "un login più moderno". È che l'agenzia smette di essere
una proprietà del server condivisa e diventa una proprietà **della persona che
ha fatto login**: `/me` la legge dalla sessione, e nessun percorso del
frontend può chiederne un'altra.

Il canale Basic legacy resta funzionante fino a P26-5, deliberatamente e con
scadenza dichiarata.

---

## 2. Prove locali (offline)

Nessuna di queste tocca un PostgreSQL o la rete. Sono la difesa quotidiana:
girano a ogni esecuzione della suite.

### 2.1 Backend — `tests/test_p26_3_client_auth_transition.py` (35 prove)

TestClient FastAPI, sessione sostituita, database assente.

- il cookie autentica e porta con sé la propria agenzia;
- la sessione **prevale** su Basic quando ci sono entrambi;
- il canale Basic senza cookie continua a funzionare identico;
- un cookie **rifiutato** insieme a un Basic valido dà 401 e **non** ricade sul
  Basic della stessa richiesta;
- un server senza `ADMIN_USER`/`ADMIN_PASS` risponde ancora 503, non 401;
- nessuna delle due dipendenze legge alcunché dalla richiesta;
- l'allargamento di D-1 è enumerato mount per mount;
- OWNER Admin resta fuori dall'allargamento, e una sessione da sola non lo
  ammette (test 30-35).

### 2.2 Frontend — controlli statici (12 prove)

Leggono il JavaScript spedito: nessun `Authorization`, nessun `btoa`, nessun
`localStorage`, nessun `agency_id`.

### 2.3 Frontend — **eseguito** in node (16 prove)

`tests/test_p26_3_shell_runtime.py`. I due moduli girano davvero, con `fetch`
scriptato e trappole che **sollevano** se qualcuno tocca `localStorage`,
`sessionStorage`, `indexedDB` o `document.cookie`. La prova 16 dimostra che
l'harness sa fallire — senza, i quindici PASS sopra non direbbero nulla.

### 2.4 Mutation testing

13 mutazioni mirate su precedenza sessione/Basic, cookie rifiutato, ammissione
di mount e scope OWNER Admin: **13 uccise, 0 sopravvissute**. Due erano
sopravvissute al primo giro e sono state chiuse con test nuovi, non aggirate.

---

## 3. Prova live su Render TEST

Eseguita con `scripts/p26_3_live_cert.py`, contro l'applicazione realmente in
esecuzione, via HTTP, sul database `stima360_db_test`.

**58 PASS, 0 FAIL, 0 BLOCKED.**

Provato via HTTP reale, non simulato:

| Prova | Esito |
|---|---|
| login valido → 204, corpo vuoto | PASS |
| cookie HttpOnly, Secure, SameSite=Lax, Path=/, Max-Age 43200 | PASS |
| password errata → 401 col messaggio unico | PASS |
| `/me` col solo cookie → la proiezione esatta di `MeResponse` | PASS |
| route OS non-CORE con la **sola sessione** | PASS |
| CORE con la sola sessione | PASS |
| Basic **senza** cookie ancora funzionante | PASS |
| Basic errato senza cookie → 401 | PASS |
| sessione valida + Basic errato → la sessione vince | PASS |
| sessione agenzia B + Basic **valido** → lo scope resta B | PASS |
| cookie invalido + Basic valido → 401, **nessun fallback** | PASS |
| cookie revocato + Basic valido → 401 | PASS |
| logout → 204 e cookie cancellato | PASS |
| dopo il logout `/me` e la route protetta rifiutano il cookie | PASS |
| nessuna risposta espone password, token o cookie | PASS |
| cleanup completo: 0 utenti, 0 membership, 0 sessioni | PASS |

**Due agenzie reali TEST distinte.** La precedenza della sessione sul Basic è
provata *per identità*: la sessione dell'agenzia B, presentata insieme al Basic
dell'agenzia Default, continua a rispondere con l'agenzia B. Non per assenza di
401 — per il nome dell'agenzia che torna da `/me`.

Lo strumento stesso è provato: `tests/test_p26_3_live_cert_script.py`, 46
prove, rifiuto del database sbagliato, cleanup garantito anche dopo un
fallimento, impossibilità di dichiarare PASS con un cleanup incompleto, e
sopravvivenza delle fixture di un run concorrente.

---

## 4. Che cosa chiude P26-3

- La OS Shell non conserva più credenziali nel browser, in nessuna forma.
- La password lascia il client una volta sola e non torna in nessun header.
- L'agenzia arriva dalla sessione, decisa dal server, e il frontend non ha modo
  di chiederne un'altra.
- Un refresh non costringe a rifare il login.
- Una revoca **è** una revoca: non si aggira presentando anche il Basic.
- Le undici superfici che la Shell chiama ammettono il cookie (allargamento di
  D-1, enumerato e provato mount per mount).
- OWNER Admin non è entrato nell'allargamento, e la sua ammissione e il suo
  scope vengono ora dalla stessa credenziale — niente stato ibrido
  «cookie + Basic».

---

## 5. Che cosa **non** chiude — e a chi tocca

### P26-4 — OS Shell
Il ciclo di vita del DOM attraverso un cambio di sessione: P26-3 certifica due
moduli che non toccano il documento, e non poteva dire nulla su cosa resta
sullo schermo quando una sessione finisce.

### P26-5 — contenimento e rimozione del Basic legacy
**Il Basic è ancora un secondo modo di entrare, di pari valore, su ogni router
tenant.** Finché un segreto condiviso può autenticare una route di tenant, la
piattaforma non è certificata per una seconda agenzia reale. P26-5 deve inoltre
stabilire una politica di ruolo esplicita per OWNER Admin: oggi quella
superficie è Basic-only proprio perché ammettere una sessione qualunque sarebbe
un'escalation, e `require_authenticated_operator` verifica che il chiamante sia
autenticato, non cosa gli è permesso fare.

### P26-6 — certificazione ostile live A/B
Una matrice che provi, su TEST, che un operatore dell'agenzia A non può
osservare né modificare risorse dell'agenzia B, e viceversa, su tutte le
superfici tenant.

---

## 6. GATE-MA1

**APERTO.**

`tests/test_p26_6c_backend_gate_closure.py` continua a calcolare il verdetto da
evidenze osservabili, e `LIVE_HOSTILE_MATRIX_PASSED` resta `False`. Il residuo
strutturale vuoto è **necessario ma non sufficiente**: manca l'esito della
matrice ostile live.

Nessuna seconda agenzia reale è autorizzata.
