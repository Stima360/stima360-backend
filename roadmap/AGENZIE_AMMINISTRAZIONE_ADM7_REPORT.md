# STIMA360 CRM — ADM-7: Interfaccia della Rete

- Branch: `core-0.1-test`, base pubblicata `076cf90` (ADM-6).
- **Solo frontend:** nessuna modifica al backend, al database o alle migration.
- Riusa esclusivamente le API esistenti (ADM-1 → ADM-6).
- Nessun commit, push o deploy; Render, produzione e Stima360.it non toccati.

## 1. Cosa è stato realizzato

| # | Richiesta | Realizzazione |
|---|---|---|
| 1 | Testi di sospensione, archiviazione, riattivazione | Allineati ad ADM-3 BIS (vedi sotto) |
| 2 | Filtro Attive / Sospese / Archiviate | Selettore nell'elenco agenzie (`rete.js`), locale sull'elenco già caricato |
| 3 | Scheda Registro | Nuovo `rete-registro.js`, su `GET /api/platform/audit` (ADM-5A) |
| 4 | Consulta | «Consulta (sola lettura)» sulle agenzie sospese o archiviate (ADM-5B) |
| 5 | Ruolo di amministratore di piattaforma | Comandi nella scheda Operatori (ADM-6) |
| 6 | Comuni presidiati da agenzie non attive | Badge «Agenzia sospesa» o «Agenzia archiviata» nell'elenco territori |
| 7 | Smartphone | Sotto i 767px operatori a card, form a una colonna, filtri del Registro che vanno a capo |
| 8 | Azioni nascoste ai non autorizzati | Invariato e verificato: il cancello di ADM-1 vale anche per Registro, Consulta e comandi di piattaforma |

### 1.1 Testi delle conferme

| Conferma | Cosa dice ora |
|---|---|
| **Sospendi** | Le email delle valutazioni e i follow-up automatici **continuano a nome di STIMA360**. Si fermano i promemoria degli appuntamenti, le prenotazioni online e i calendari collegati. L'agenzia resta consultabile in sola lettura. |
| **Archivia** | Le stesse indicazioni. Resta l'avvertenza sulle assegnazioni ancora attive. |
| **Riattiva** | Promemoria, prenotazioni e calendari riprendono; email e follow-up tornano a nome dell'agenzia. |

Il testo precedente diceva che «i follow-up automatici si fermano», in contrasto con la regola definitiva di ADM-3 BIS.

### 1.2 Consulta

- **Sul pulsante:**
  - per un'agenzia attiva resta «Entra nell'agenzia», con la conferma di sempre;
  - per un'agenzia sospesa o archiviata compare «Consulta (sola lettura)»;
  - il pulsante si aggiorna subito dopo ogni cambio di stato.
- **Conferma:** dice che si entra in SOLA LETTURA e che l'agenzia resta sospesa o archiviata.
- **Dopo l'ingresso:** la barra della Shell mostra «SOLA LETTURA» (ADM-5B) e l'uscita usa il pulsante di sempre.
- **Sottotitolo:** anche lo stato sotto il nome dell'agenzia si aggiorna dopo un cambio di stato. Prima restava quello vecchio.

### 1.3 Ruolo di piattaforma (scheda Operatori)

- **Badge:**
  - «Amministratore di piattaforma» su chi ha il ruolo;
  - «Super Admin designato» su chi sta usando la Rete, cioè il designato.
- **Comandi,** solo quando il backend li accetterebbe:
  - «Rendi amministratore di piattaforma» per un account attivo che ha già fatto login;
  - «Togli ruolo di piattaforma» per chi lo ha.
- **Designato:** **mai** comandi sulla riga del designato.
- **Conferme:** spiegano che il designato non cambia, che la membership resta e che, se il ruolo viene tolto, le sessioni vengono chiuse.
- **Autorità:** resta il backend (ADM-6), che ricontrolla tutto.

### 1.4 Registro

- **Filtri:** operazione, esito, agenzia, oggetto e id, periodo «dal» e «al». Le ammissioni si vedono solo scegliendole come operazione.
- **Colonne:** quando, chi (etichetta e id originali), operazione, esito, oggetto, agenzia.
- **Dettagli leggibili:** cambi di stato (`stato: attivo → sospeso`), ruolo di piattaforma (`no → sì`), sola lettura.
- **Paginazione:** Precedenti / Successivi, 50 righe per pagina.

Ogni dato del backend passa da `escapeHtml` o `textContent`, come nel resto della Rete.

## 2. File modificati

| File | Modifica |
|---|---|
| `static/os_shell/assets/views/rete.js` | filtro agenzie, badge sui territori, scheda Registro |
| `static/os_shell/assets/views/rete-agenzia.js` | testi delle conferme, Consulta, aggiornamento di pulsante e sottotitolo, comandi del ruolo di piattaforma, classe per le card |
| `static/os_shell/assets/views/rete-registro.js` (nuovo) | la scheda Registro |
| `static/os_shell/assets/app.css` | blocco smartphone limitato alla Rete |
| `tests/test_adm7_rete_ui_browser_postgres.py` (nuovo) | collaudo in Chromium |
| `tests/test_p27_7_network_contracts.py` | sentinelle aggiornate in modo dichiarato (vedi §3) |

## 3. Test e risultati

**Collaudo in Chromium:** `tests/test_adm7_rete_ui_browser_postgres.py`, **3 superati**. PostgreSQL usa-e-getta e sessioni vere.

| Test | Verifica |
|---|---|
| `test_rete_adm7_in_chromium[390]` e `[1280]` | conferma di sospensione con il testo nuovo e stato `suspended` nel database; testi di archiviazione e riattivazione; «Consulta (sola lettura)» con conferma e barra «SOLA LETTURA»; uscita senza acting residuo; filtro Sospese e Attive; badge «Agenzia sospesa» sul Comune; concessione e revoca del ruolo di piattaforma con conferma, verificate nel database; card degli operatori a 390 px (intestazione nascosta); Registro con riga «Ruolo di piattaforma» e dettagli `no → sì` e `sì → no`, filtro per operazione, «Precedenti» disabilitato alla prima pagina; nessun errore JavaScript e nessuno scorrimento orizzontale della pagina |
| `test_un_amministratore_non_designato_non_vede_comandi` | su `#/rete` e sul dettaglio agenzia un amministratore non designato vede solo l'avviso: nessuna scheda Registro, nessun Consulta, nessun comando |

**Regressione**

- **Browser e PostgreSQL:** **47 superati**. Comprende ADM-3 (Rete in Chromium), ADM-5A, ADM-5B (barra SOLA LETTURA), ADM-6, approvazione edifici, C2, Cestino 1 e 2, eliminazione edifici, Territorio-Zona 2, Zona-3 e il nuovo ADM-7.
- **Test statici e UI che toccano la Rete o la Shell** (39 file), su un clone con le modifiche committate: **nessun nuovo fallimento**. I 160 fallimenti sono tutti nella baseline: test UI in Node con DOM simulato senza `createElementNS`, e altri preesistenti.
- `node --check` su `rete.js`, `rete-agenzia.js` e `rete-registro.js`: ok.

**Sentinelle aggiornate in modo dichiarato** (`tests/test_p27_7_network_contracts.py`)

- **`test_c2`:** vieta `platform_admin: ` nel codice della Rete, perché il ruolo di piattaforma non deve comparire come ruolo di membership.
  - Ora ammette **una sola** occorrenza: il corpo della rotta dedicata di ADM-6.
  - In più controlla che il dialogo Membership non nomini il ruolo di piattaforma.
  - Il controllo dei ruoli assegnabili resta intatto.
- **`test_d3`:** l'elenco dei file della Shell toccabili dalla Rete include `rete-registro.js`.
- Le sentinelle che vietano la normalizzazione nel browser (`toLowerCase`) **non sono state toccate**: il codice usa invece etichette esplicite.

## 4. Problemi reali e limiti

1. **Testo errato corretto:** la conferma di sospensione contraddiceva ADM-3 BIS (punto 1.1).
2. **Comandi visibili durante la consultazione:** quando il Super Admin consulta un'agenzia in sola lettura, le altre viste del CRM mostrano ancora i comandi di modifica e il backend li rifiuta. È la decisione D2 di ADM-5B; ADM-7 non ha riprogettato le viste del CRM.
3. **«Rendi amministratore di piattaforma»** non compare per chi non ha mai fatto login. Il motivo non viene scritto: il pulsante è semplicemente assente, come per gli altri comandi della Rete non disponibili.
4. **Filtro agenzie:** è locale sull'elenco intero, che il backend restituisce senza paginazione, come dichiarato in P27-2.

**STOP.** Nessun commit, push o deploy. Attendo il prossimo via libera.
