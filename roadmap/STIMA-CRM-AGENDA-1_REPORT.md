# STIMA-CRM-AGENDA-1 — STIMA → CRM → AGENDA (7 ottobre 2026)

**Stato: due commit locali nel worktree isolato (ramo `impl-stima-crm-agenda` da `b0a2e05`). Nessun push, nessun deploy, PROD mai toccata. Repo Mac originale non toccato; `.git/objects/maintenance.lock` sul Mac lasciato com'è.** Gli SHA dei commit sono nel messaggio di review (un file dentro un commit non può contenere il proprio SHA).

| Commit | Contenuto |
|---|---|
| 1 `feat(public): integrate reliable stima submission flow` | candidata F01/F13/F16/R1/F07/F04/F06, migration 093/094, filtro Cestino sulla ricevuta, test legacy e sentinelle riallineati al contratto F04 (112 file, +53.382/−1.089, nessun binario) |
| 2 `feat(agenda): import site inspection requests automatically` | `run_import(record_id, una_aperta_per_stima)`, `site_hook.safe_import_for_detail`, aggancio in `_save_detail_submission`, regola «una sola richiesta aperta per stima» su aggancio e sync manuale, test A–I/J1–J5/flusso 1, sentinelle A30, questo rapporto (10 file) |

La separazione è pulita: l'unico file con modifiche di entrambe le fasi è `main.py`, la cui differenza fra le due è esattamente l'import e la chiamata dell'aggancio (11 righe). Il commit 1 da solo non contiene nulla che dipenda dal commit 2.

## Chiusura review — Decisione 1: immagini della preview

- **20 immagini, 27 MB: tutte escluse dal commit. 0 incluse.**
- Provenienza: `site-preview/asset-manifest.json` le registra come GET da `https://www.stima360.it/` (`copy_status: copied`); SHA-256 e dimensione **verificati 20/20** sui file. Il README della candidata dichiara gli asset «byte per byte» identici agli originali dello `Stima360.zip`. Nessuna è stata modificata dalla candidata. Una (`3.png`) è anche byte-identica a `stimacentrato.jpg` già nel ramo; le altre 19 non esistono nel ramo.
- Uso nei test: nessuno. I test browser servono solo `index/dati_personali/stima_dettagliata/pdf_redirect.html`, `preview-config.js`, `submission.js` e abortiscono ogni altra richiesta; i journey montano `public/` ma non controllano le immagini (solo `pageerror`, prefill e ricevute).
- Prova senza immagini: browser ricevute **27/27**, browser sito **69/69**, journey sito e ricevute PASS, gate completo PASS.
- Restano nel commit: `asset-manifest.json` (URL + SHA-256, per ricopiarle), `html-provenance.json`, `original-source-manifest.json`, gli HTML/JS. Nota aggiunta in `site-preview/README.md`. Le 20 immagini sono conservate fuori dal repository in `scratchpad/img-escluse/`.

## Chiusura review — Decisione 2: una sola richiesta aperta per stima

**Implementata riusando la regola D5 dell'Agenda, senza nuova logica, nessuna migration, nessuna UNIQUE nuova.**

- `run_import(..., una_aperta_per_stima=False)`: con `True`, per ogni record idoneo e prima dell'INSERT, `repository.lock_stima` (FOR UPDATE, fino al commit) e poi `repository.open_inspection_for_stima(..., statuses=state_machine.OPEN_STATUSES)` — esattamente la guardia di `schedule_appointment`. Stati aperti = quelli del modello: `requested`, `scheduled`, `confirmed`; terminali (`completed`, `cancelled`, `no_show`) non bloccano.
- Aperto trovato → nessun appointment, record contato in `open_request_exists` (oppure `already_imported` se l'aperto è la riga di quello stesso record, importata da una corsa concorrente: `repository.find_by_source_key`).
- Chi la usa: l'aggancio del modulo pubblico **e** la sincronizzazione manuale dell'Agenda (la rotta conta questi record in «già presenti»), così un record saltato dall'aggancio non rinasce al sync successivo. La CLI A30-6 (import storico) resta com'era (`False`).
- Lock: il FOR UPDATE si prende **prima** del FOR KEY SHARE di `stima_agency`. La prima versione partiva da KEY SHARE e saliva a FOR UPDATE: in un gate una corsa aggancio + sync è andata in deadlock (un record contato come errore). Corretto, e i test di concorrenza ora verificano anche `errors == 0`; **60/60 esecuzioni concorrenti** (D, I2, J4, J5 × 15) senza errori.
- Comportamento: (A) nessun aperto → `requested` normale; (B) aperto esistente → dettagliata salvata, risposta `{"ok": true}`, nessun secondo appointment, nessun evento, nessuna riga Google, nessuna notifica (l'import non ne crea); log `open_request_exists=1` senza dati personali; (C) precedente annullato → nuova richiesta creata.

Test nuovi (`tests/test_stima_crm_agenda_1_postgres.py`):

| Test | RED (regola disattivata) | GREEN |
|---|---|---|
| J1 stessa stima, due dettagliate distinte con sopralluogo → max 1 aperto | FAIL | PASS |
| J2 aperto esistente + nuovo submit → dettagliata salvata, nessun duplicato, nessun Google/evento; il sync dopo non lo crea | FAIL | PASS |
| J3 dopo annullamento (stato terminale) → nuova richiesta consentita | PASS (guardia contro il blocco eccessivo) | PASS |
| J4 due submit concorrenti, `request_id` diversi → 1 aperto, nessun errore | FAIL | PASS |
| J5 sync manuale + aggancio concorrenti su record diversi della stessa stima → 1 aperto, nessun errore; secondo sync nessun duplicato | FAIL | PASS |

File completo: **22/22 PASS in 5 esecuzioni consecutive**.

Test A30-7 riallineati (premessa superata: la sync creava due richieste aperte per la stessa stima): `test_02_03_04` (i conteggi riflettono la regola), `test_06` (due stime diverse), `test_40`/`test_41` (le due aperte si costruiscono con l'import diretto A30-6 per continuare a provare D5 al «Fissa»; la terza richiesta ora non nasce). Sentinelle: `test_a30_6::test_22` (+ `lock_stima`, `open_inspection_for_stima`, `find_by_source_key`), `test_a30_7_static::test_03`. Tutte con marker.

## Chiusura review — Suite completa: tabella differenziale

Baseline = suite completa sul clone pulito di `b0a2e05`. Candidata «stato commit» = suite completa su un worktree di prova con i due commit identici a quelli finali (unica differenza: il testo di questo rapporto).

| TEST | BASELINE b0a2e05 | CANDIDATA prima (worktree, 35 fail) | CANDIDATA stato commit | REGRESSIONE? |
|---|---|---|---|---|
| `test_a30_1_appointments_postgres.py::test_50_il_registro_lo_scrive_il_service_con_l_attore` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_a30_1_appointments_postgres.py::test_67_spostare_crea_una_riga_nuova_e_libera_la_vecchia` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_a30_1_appointments_postgres.py::test_68_uno_spostamento_in_conflitto_non_lascia_niente_a_meta` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_a30_1_appointments_postgres.py::test_98a_test_certificato_insert_purge_e_spostamento` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_a30_2_appointments_postgres.py::test_30_availability_slot_e_finestra` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_a30_2_appointments_postgres.py::test_31_check_redige_i_colleghi_per_un_agent` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_a30_7_legacy_schedule_postgres.py::test_30_disponibilita_slot_occupato_e_libero` | FAIL | FAIL | FAIL | No — baseline (fixture A30-2 senza `agent_working_hours`, riprodotto su b0a2e05) |
| `test_a30_7_legacy_schedule_postgres.py::test_31_slot_occupato_409_con_alternative_e_nulla_cambia` | FAIL | FAIL | FAIL | No — baseline (fixture A30-2 senza `agent_working_hours`, riprodotto su b0a2e05) |
| `test_a30_8_outcome_postgres.py::test_35_disponibilita_dopo_gli_stati_terminali` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_a32_2_reminder_static.py::test_s14_inventario_chiuso_contro_git_status` | PASS | FAIL | PASS | No — sentinella di working tree: FAIL solo a modifiche non committate, PASS nello stato del commit |
| `test_a32_2_reminder_static.py::test_s15_main_aggiunge_solo_le_righe_dichiarate` | PASS | FAIL | PASS | No — sentinella di working tree: FAIL solo a modifiche non committate, PASS nello stato del commit (rieseguito nello stato del commit: PASS) |
| `test_followup_isolation.py::test_core_property_buy_match_proposal_owner_flow_do_not_import_followup` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_lmc11_valuation_cron.py::test_h1_nessuna_migration_nuova` | PASS | FAIL | PASS | No — sentinella di working tree: FAIL solo a modifiche non committate, PASS nello stato del commit |
| `test_lmc13_home_metrics.py::test_e1_nessuna_migration_nuova` | PASS | FAIL | PASS | No — sentinella di working tree: FAIL solo a modifiche non committate, PASS nello stato del commit |
| `test_lmc15_acquisition_bridge.py::test_37_nessuna_migration_oltre_la_070` | PASS | FAIL | PASS | No — sentinella di working tree: FAIL solo a modifiche non committate, PASS nello stato del commit |
| `test_lmc15_acquisition_bridge.py::test_41_i_file_toccati_sono_solo_quelli_dichiarati` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_lmc15_acquisition_bridge.py::test_42_il_documento_di_p29_2_0_resta_fuori_dall_indice` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_lmc1b_owner_login_link_postgres.py::test_9e_il_limite_di_un_account_non_tocca_l_altro` | PASS | PASS | FAIL | No — flaky preesistente: 2/40 FAIL anche su b0a2e05 (hash casuale che inizia per `0`) |
| `test_lmc2_owner_homes.py::test_f4_il_read_model_non_tocca_il_funnel_ne_i_domini_vicini` | PASS | FAIL | PASS | No — sentinella di working tree: FAIL solo a modifiche non committate, PASS nello stato del commit |
| `test_lmc7_owner_radar.py::test_h4_i_domini_vietati_non_sono_stati_toccati` | PASS | FAIL | PASS | No — sentinella di working tree: FAIL solo a modifiche non committate, PASS nello stato del commit |
| `test_lmc7_owner_radar.py::test_h5_nessuna_migration_creata` | PASS | FAIL | PASS | No — sentinella di working tree: FAIL solo a modifiche non committate, PASS nello stato del commit |
| `test_lmc8_crm_radar.py::test_h3_i_domini_vietati_non_sono_stati_toccati` | PASS | FAIL | PASS | No — sentinella di working tree: FAIL solo a modifiche non committate, PASS nello stato del commit |
| `test_lmc8_crm_radar.py::test_h5_nessuna_migration` | PASS | FAIL | PASS | No — sentinella di working tree: FAIL solo a modifiche non committate, PASS nello stato del commit |
| `test_lmc9_consultation_request.py::test_f3_i_domini_vietati_non_sono_stati_toccati` | PASS | FAIL | PASS | No — sentinella di working tree: FAIL solo a modifiche non committate, PASS nello stato del commit |
| `test_lmc9_consultation_request.py::test_f4_nessuna_migration` | PASS | FAIL | PASS | No — sentinella di working tree: FAIL solo a modifiche non committate, PASS nello stato del commit |
| `test_next2_router_hardening.py::test_1_all_100_routes_are_still_mounted` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_next2_router_hardening.py::test_2_every_certified_admin_route_has_security_gate` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_p26_6_agenda_realapp_postgres.py::test_03_anonimo_e_basic_si_fermano_al_mount` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_p29_3_journey_foundation.py::test_19_i_file_toccati_sono_quelli_dichiarati_e_P29_2_0_resta_fuori` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_p29_3c_orchestrator.py::test_30_i_file_toccati_sono_quelli_dichiarati_e_P29_2_0_resta_fuori` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_p29_3d_crm_journey.py::test_39_i_file_toccati_sono_quelli_dichiarati_e_P29_2_0_resta_fuori` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_p29_3d_crm_journey.py::test_40_il_documento_di_design_non_e_stato_toccato` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_p29_3e_cron_integration.py::test_28_i_file_toccati_sono_quelli_dichiarati_e_P29_2_0_resta_fuori` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_p29_3e_cron_integration.py::test_29_il_documento_di_design_non_e_stato_toccato` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_p29_3g_final_fixes.py::test_20_i_file_toccati_sono_quelli_dichiarati_e_P29_2_0_resta_fuori` | FAIL | FAIL | FAIL | No — stesso esito del baseline |
| `test_p29_3g_final_fixes.py::test_21_il_documento_di_design_non_e_stato_toccato` | FAIL | FAIL | FAIL | No — stesso esito del baseline |

Totali: baseline 23 FAIL; candidata non committata 35; stato commit 24. Nuovi reali: 0.

| Suite completa | PASS | FAIL | SKIP |
|---|---|---|---|
| Baseline `b0a2e05` | 11.051 | 23 | 133 |
| Candidata, stato commit | **11.262** | **24** (23 = baseline + `test_9e` flaky) | 133 |

`test_9e` (LMC-1B): filtra il token nuovo con `token_hash NOT LIKE '0%'`, ma l'hash vero è SHA-256 casuale → fallisce quando inizia per `0` (≈ 1/16). File e modulo `owner/` non toccati; su `b0a2e05` pulito: **2 FAIL su 40** con la stessa asserzione. Non è una regressione; segnalato come difetto di test preesistente (P2), non corretto.

**Nessuna regressione prodotto introdotta da STIMA-CRM-AGENDA-1.**

## Chiusura review — P1 che restano

- `property_id` sull'appointment: resta NULL. L'appointment è raggiungibile da agenzia (`agency_id`), stima (`stima_id`), dettaglio (`source_record_id = stime_dettagliate:<id>`), contatto (`contact_id`/`lead_id` se il lead è unico, altrimenti via stima → `lead_stime`). La scheda immobile è raggiungibile dalla stima (`property_site_sources`).
- Pixel `Lead`, dashboard ricevute, provider reali, Safari/Firefox: non toccati (fuori perimetro).
- Runtime macOS (PG 18.6, Chrome for Testing) non ricertificato in questo ambiente.

---

## Storico: rapporto di implementazione (prima della chiusura)

Le sezioni sotto sono il rapporto consegnato prima della review. Dove differiscono, valgono le sezioni sopra (in particolare: immagini escluse; regola «una sola richiesta aperta» implementata; I3 sostituito da J1).

## 1. Branch e HEAD di partenza

| | |
|---|---|
| Ramo atteso | `core-0.1-test` |
| `origin/core-0.1-test` | `b0a2e05` fix(platform): P27-3 roster keeps operator and membership apart |
| Repo Mac | HEAD `ddd2f7f`, **19 commit dietro** origin, 2 file non tracciati (`P29_2_0_COMMUNICATION_DESIGN.md`, `d8_TEST_20261005_172319.txt`) — **non toccato** |
| Worktree di lavoro | clone pulito di `b0a2e05` → ramo `impl-stima-crm-agenda` (container, `$S/impl`) |
| Candidata | `/private/tmp/stima360-f0406-backend`, HEAD `f7251ac`, 57 voci locali (+ il mio rapporto R1) — **letta, non alterata** |

Nota residua sul Mac: il `git fetch` di una sessione precedente ha lasciato `.git/objects/maintenance.lock` nel repo Mac. Non l'ho rimosso (nessun permesso di cancellazione; non richiesto di nuovo). Non blocca commit/pull ordinari ma può far saltare `git maintenance`; si rimuove a mano con `rm .git/objects/maintenance.lock` quando nessun processo git è attivo.

## 2. Come ho protetto le modifiche esistenti

- Nessun `reset --hard`, `clean`, `checkout`, `pull`, `rebase`, `stash` o `amend`, né sul Mac né nel worktree.
- Il Mac resta a `ddd2f7f` con i suoi file non tracciati; la candidata resta com'era (le ho solo lette; il confronto è stato fatto su una ricostruzione `f7251ac` + i file della candidata, `cand_vs_f7251ac.patch`, 97 file).
- Integrazione con `git apply --3way` della candidata sopra `b0a2e05`: il prodotto è entrato pulito; i soli conflitti erano in **4 file di sentinelle** (ultima migration), risolti a mano con marker. Tutte le modifiche di origin successive a `f7251ac` (P27-3, A–H, Cestini, Agenda, Immobili, Edifici, routing, multi-agenzia, permessi) sono quelle del ramo e non sono state toccate dall'apply.
- Le migrazioni esistenti non sono state modificate: le due della candidata sono state **rinumerate** (§4).
- Le cartelle di evidenza della candidata (`docs/*_evidence/`) non sono state portate: non servono all'esecuzione.
- Nessun dato condiviso toccato: tutti i test girano su PostgreSQL 16 usa-e-getta nel container (socket Unix, `listen_addresses=''`), con la guardia `sitecustomize` che toglie SMTP/WhatsApp/DB/Google dall'ambiente e consente socket solo locali. Email, WhatsApp e PDF sono doppi nei test; nessun invio reale, nessuna importazione storica.

## 3. Diff integrato dalla candidata

`git diff --cached --stat HEAD`: **101 file, +52.196 / −681** (82 nuovi, 19 modificati). Per area:

| Area | File | Note |
|---|---|---|
| `main.py` | 1 (+585/−447) | F04: `salva_stima`/`salva_stima_dettagliata` diventano involucri di `_receive_submission`; pipeline in `_save_quick_submission`/`_save_detail_submission` con `receipt.step(...)`; rotte `/api/submissions/{id}`, `/resume`, `/api/stime/{id}/pdf(/retry)` e le equivalenti admin |
| `public_submissions.py` | nuovo (+367) | ricevute, identità (`request_id` + `receipt_key`), `Receipt`, `open_receipt`, `authorize_detail`, R1 `result_available` |
| `stima_pdf.py`, `pdf_report.py` | nuovo / (+11/−101) | F07: PDF privato (`stima_pdf_artifacts`) |
| `followup/` | 2 (+74/−10) | `run_followup(recover=True, due_at_override=...)`, agenzia dai riferimenti con `FOR SHARE` |
| `database.py`, `stime_purge.py` | 1+1 | ritocchi minimi della candidata |
| `migrations/` | 4 | `093_stima_private_pdf(.sql/_down.sql)`, `094_public_submission_receipts(.sql/_down.sql)` (rinumerate, §4) |
| `site-preview/` | 45 (+15.632, 20 binari ≈ 27 MB) | HTML pubblici (F01/F13/F16/R1/F07), `release/preview-config.js`, `baseline/`, patch frontend. **Punto di review**: i 20 PNG/JPG di `site-preview/public` pesano 27 MB; decidere se tenerli nel repo o spostarli |
| `docs/` | 13 (+29.852) | review e patch della candidata (con nota di rinumerazione in testa a 6 documenti) |
| `tests/` | 35 | 25 file nuovi (ricevute, PDF, journey, frontend), 10 aggiornati |

Non portato: `docs/*_evidence/` (log), il mio `docs/F04_F06_R1_VERIFICA_2026-10-07.md` (resta nella candidata).

**Correzione alla candidata fatta in integrazione (1 riga di prodotto):** `public_submissions.Receipt.envelope` leggeva `properties` senza il predicato del Cestino Immobili; la sentinella `test_delete_arch_2b2::test_01` lo ha rilevato. Aggiunto `AND {core.property_trash.live('p')}` (lo stesso predicato di `property/site_sync.py`): una scheda cestinata non è più riportata come `property_id` della ricevuta e `result_available` torna `False`. Prova: `test_f1_e_una_scheda_nel_cestino_non_e_un_collegamento_della_ricevuta` (PostgreSQL).

## 4. Tabella migration rinumerate

| NUMERO | ORIGIN (`b0a2e05`) | CANDIDATA (`f7251ac`) | AZIONE |
|---|---|---|---|
| 092 | `092_cestino_richieste_1_buy_request_trash` | `092_stima_private_pdf` | collisione → candidata rinumerata **093** |
| 093 | — | `093_public_submission_receipts` | rinumerata **094** (per restare dopo la 093) |
| 094 | — | — | nuovo slot usato dalle ricevute |

Contenuto SQL invariato (nessuna dipendenza fra le due e la 092 di origin). Riferimenti aggiornati: 3 test della candidata (`test_private_stima_pdf_postgres`, `test_private_stima_pdf_failures`, `test_public_submission_receipts_postgres`), nota di rinumerazione in 6 documenti (`F07_REVIEW`, `F07_PDF_STORICI_PROCEDURA`, `F04_F06_REVIEW`, `F04_F06_R1_REVIEW`, `F04_F06_PLAN`, `site-preview/README.md`), 22 sentinelle di «ultima migration» (§5c). Il runner (`scripts/p26_migrate`) scopre le migrazioni dai file: catena contigua 001→094 verificata dai test del runner; applicate solo al PostgreSQL isolato (ledger: 66 → 68 righe).

## 5. Test legacy aggiornati (contratto F04, mai allentato)

Criterio seguito: il test descrive il contratto nuovo. Con F04 un passo a valle della INSERT che fallisce **non è più fail-open con risposta di successo e consegne**: la riga resta scritta una volta, la risposta è 202 `partial` (nessun falso successo, nessuna consegna a metà) e la **stessa identità** riprende dal passo fallito senza duplicare nulla. I test che asserivano «il funnel continua e PDF/alert/WhatsApp partono» sono stati riscritti in questa forma (con ripresa); i test che asserivano ordine, firme, isolamento e idempotenza restano con le stesse asserzioni, lette sulla pipeline. Ogni modifica porta il marker `SENTINELLA AGGIORNATA DA STIMA-CRM-AGENDA-1`.

### 5a. I 5 file della review (26 fail / 70 pass → 96/96)

Nuovo doppio condiviso `tests/public_submission_fakes.py`: emula in memoria la sola tabella `public_submission_receipts` (gli statement che il modulo ricevute emette) e delega tutto il resto ai doppi legacy; la classe `Receipt` usata è quella **vera** (nessuna semantica reimplementata). Stesso modello del `private_pdf_fakes.py` della candidata.

| File | Prima | Dopo | Cosa cambia |
|---|---|---|---|
| `test_public_link_token_failure.py` (F16) | 1 fail | 1 pass | token KO → `fields_token` fallito, 202 `partial`, rollback della transazione del token, zero consegne, una sola INSERT |
| `test_public_stima_core_crm_bridge.py` | 1 fail | 10 pass | bridge KO → `partial` al bridge, zero consegne; ripresa a bridge riparato: flusso completo UNA volta (1 INSERT, 1 PDF, 1 alert, 1 WhatsApp, 1 accodamento); terzo invio identico: niente di nuovo |
| `test_seller_intelligence_p17b1_integration.py` | 12 fail | 14 pass | identità nel payload; SI KO → `partial` a `event_requested`/`event_completed` e ripresa; bridge+SI KO recuperati in ordine; retry stessa identità = ricevuta completata, 2 eventi; `mail_result=False` → alert `indeterminate`, ricevuta `attention` |
| `test_p27_6_lead_routing.py` | 2 fail | 50 pass | g8: identità presente → 403 della capability senza SQL su `stime`; j9: lettura della pipeline |
| `test_followup_p18c_integration.py` | 10 fail | 9 pass | `run_followup(recover=True, due_at_override=ISO)` dentro `receipt.step("followup")`; bridge KO → follow-up non parte con riferimenti vuoti, parte alla ripresa; P18 KO → `partial` dopo P17, ripresa con scadenza congelata; doppio del repository con i riferimenti dell'azione (ripresa `recover=True`) |

Rinominati 5 test il cui nome affermava il contratto vecchio (es. `..._continues_when_..._fails_completely` → `..._outage_yields_a_partial_receipt_then_the_resume_completes`).

### 5b. Sentinelle della struttura del funnel (emerse dalla suite completa)

`test_catalogo_canonico_1` h01/h02; `test_p26_1_public_stima_routing` j5/j6/j7 (8); `test_p26_2b_public_stima_writer` (harness via ricevute, AST su `_save_quick_submission`, B3 fail-closed = nessuna riga e ricevuta `partial`, 27/27); `test_lmc1a_owner_provisioning` d1–d6 (provisioning = `receipt.step("owner")`, `not_applicable` per account disattivato); `test_p20_property_watch` (watch dopo `valuation`); `test_followup_isolation`, `test_seller_intelligence_isolation`, `test_p26_6a_seller_engine_isolation` (il confine che non solleva è la ricevuta; `safe_*` non più usati nel funnel); `test_followup_p18d2_repository` (doppio: `SELECT agency_id ... FOR SHARE`).

### 5c. Sentinelle di migration, inventari, Agenda

- «la 092 è l'ultima» → 093/094 in 18 file (a30_1, a30_2p, a32_1, censimento_1, censimento_2 PG, crm_ops_3, crm_ops_4, delete_arch_1a, delete_arch_2b2, lmc15, lmc1b, lmc2, lmc3, p29_1, p29_2_1, p29_2_2, p29_2_3, p29_3) + i 4 risolti in FASE 1 (cestino_edifici_1, cestino_richieste_1, p27_6, p29_2_4/p29_2_5e, a32_2 s10, delete_arch_1b/1c).
- Inventari: `test_p26_5_basic_containment` (4 rotte pubbliche F04/F07 con la loro giustificazione), `test_p26_6c_backend_gate_closure` test_27 (`salva_stima` non ha più SQL proprio; la pipeline è misurata con la stessa regola), `test_p26_6_live_cert_script` test_86i (2 FK `agency_id → agencies NO ACTION` esaminate).
- Agenda: `test_a30_mount_api` 30b/31, `test_a30_6_legacy_import` 34, `test_a30_7_legacy_schedule_postgres` 54 (vedi §6).

## 6. File modificati per dettagliata → Agenda

| File | Modifica |
|---|---|
| `appointments_legacy/stime_dettagliate_import.py` (+14/−3) | `run_import(..., record_id=None)`: filtro opzionale `AND (%s::bigint IS NULL OR d.id = %s::bigint)` nei candidati e nel conteggio `without_sopralluogo`; `esito["record_id"]`. Stesse regole, stessa chiave, stesso `INSERT ... ON CONFLICT`. |
| `appointments_legacy/site_hook.py` (nuovo, 97 righe) | `safe_import_for_detail(detail_id, *, connection_factory=None)`: legge `agency_id` del record, chiama `run_import(cur, apply=True, agency_id=<del record>, record_id=<id>)` in una transazione propria (come `router.sync_for_session`), commit solo a import riuscito; **non solleva mai**: ogni errore → `log.warning("legacy_detail_auto_import_failed detail_id=… error_type=…")` e ritorno `None`; esito nel log (contatori, mai dati personali; `warning` se il record è stato scartato). Documenta il superamento della D2 di A30-7. |
| `main.py` (+11) | `from appointments_legacy.site_hook import safe_import_for_detail`; in `_save_detail_submission`, **dopo il commit della riga** e prima del passo `property_detail`: `safe_import_for_detail(detail_id, connection_factory=get_connection)`. Fuori dalla ricevuta F04 (non è un `receipt.step`): un guasto Agenda non rende la ricevuta `partial`. Nessun SQL di appointment in `main.py`. |
| sentinelle | `test_a30_mount_api` 30b/31 (una funzione in più importata dal package legacy, chiamata una volta), `test_a30_6_legacy_import` 34, `test_a30_7_legacy_schedule_postgres` 54 (il handler chiama UNA volta l'adattatore; non nomina `appointments`, `insert_appointment`, `run_import`). |
| `tests/test_stima_crm_agenda_1_postgres.py` (nuovo, 18 test) | RED/GREEN A–I + flusso 1 + Cestino (§8). |

Semantica: `status='requested'`, `appointment_type='inspection'`, `start_at` = ora a parete Europe/Rome (regola DST A30-6), `end_at = +60'` (policy), agente/creatore/`property_id`/`location_text` NULL, lead e contatto solo se il lead è unico, `source='legacy_stime_dettagliate'`, `source_record_id='stime_dettagliate:<id>'`, note con il solo numero del record. Google: `requested` non è in `APPOINTMENT_REMOTE_PRESENT` → nessuna riga di sync, `google_sync_status='not_synced'`; Google entra solo da `scheduled` con le regole Agenda esistenti. L'UI Agenda mostra già `source==='legacy_stime_dettagliate'` + `requested` come «Richiesta dal sito». Il sync manuale (`POST /api/appointments/legacy-requests/sync`) resta identico ed è il ripiego.

## 7. Idempotenza

Due livelli, entrambi già esistenti:

1. **F04 (richiesta HTTP).** La dettagliata porta `request_id` (UUID) + `receipt_key`; `open_receipt` prende `pg_advisory_lock('public-submission:<request_id>')` e trova/crea la ricevuta. Stesso POST ripetuto, doppio click, risposta persa, retry: la **stessa identità** trova la ricevuta → una sola riga `stime_dettagliate` (`detail_id` sulla ricevuta, UNIQUE) e la pipeline, se completata, non si riesegue. Due richieste concorrenti con la stessa identità sono serializzate dal lock.
2. **A30-6 (Agenda).** `source_record_id = 'stime_dettagliate:<id>'` con l'indice unico `uq_appointments_source_record` (072) e `INSERT ... ON CONFLICT (source, source_record_id) DO NOTHING RETURNING` in `appointments.repository.insert_appointment`; l'evento `created` nasce solo se la riga è stata inserita, nella stessa transazione. Aggancio e sync manuale contemporanei sullo stesso record: uno inserisce, l'altro riceve `None` → `already_imported`. Ripresa F04 che ripassa dal hook: `gia_importato` → nessuna riga nuova. Una chiave annullata resta occupata (INSERT-ONCE).

Caso coperto anche: aggancio fallito (DB Agenda giù, import KO) → la dettagliata resta committata, la risposta al sito è `completed`, il record resta importabile dal sync manuale (nessuna riga né evento a metà: il hook ha transazione propria).

**«Una sola richiesta aperta per stima».** Il modello Agenda vieta un secondo sopralluogo *aperto* per stima **solo al momento di fissarlo** (`_sopralluogo_unico` in `schedule_appointment`, D5 di A30-7, con `lock_stima` + `open_inspection_for_stima`); né `create_appointment` né l'import A30-6 né il sync manuale lo applicano alla creazione di una `requested`. Applicarlo al solo percorso automatico avrebbe creato una regola nuova per `requested` e una divergenza dal sync manuale (oltre a richiedere al modulo di import di dipendere dal `service`, vietato da `test_a30_6::test_21`). Quindi **non implementato: P1** (§9). Comportamento oggi, provato: due dettagliate distinte della stessa stima → due `requested`; al «Fissa» la seconda riceve il 409 esistente.

## 8. Risultati test

Ambiente: PostgreSQL 16.13 usa-e-getta (socket Unix), Chromium headless per i journey/frontend, guardia di rete. Log in `$S/implv/`.

| Gate | PASS | FAIL | SKIP |
|---|---|---|---|
| Candidata integrata (FASE 1): gate integrato PG (ricevute, R1, backup, journey) | 66 | 0 | 0 |
| Compatibilità F07/F01/CRM (8 file) | 121 | 0 | 0 |
| Offline (12 file) | 174 | 0 | 1 (design P29 fuori repo, preesistente) |
| Suite fasi A–H + P27-3 (FASE 1, prima dei riallineamenti) | 450 | 2 → 0 (h01/h02 riallineati) | — |
| 5 file legacy (FASE 2) | 96 | 0 | 0 |
| `test_catalogo_canonico_1` | 17 | 0 | 0 |
| Sentinelle Agenda statiche (a30_mount_api, a30_6, a30_7_static, a30_2, catalogo) | 218 | 0 | 0 |
| **RED** A–I/flusso1 con aggancio neutralizzato (`main.safe_import_for_detail = no-op`) | 3 (A; D ed E[database] chiamano l'adattatore direttamente) | **10** | 0 |
| **GREEN** `test_stima_crm_agenda_1_postgres.py` (A, B, B2, C, D, E×2, E2, F/G, H, I, I2, I3, F1 a–e) | **18** (×3 run) | 0 | 0 |
| Sentinelle riallineate (§5b/5c, p26_1, p26_2b, lmc1a, p20, isolamenti, inventari, migration, p18d2) | tutti verdi nei rispettivi file | 0 | — |
| **Suite completa** `pytest tests` (1ª, prima dei riallineamenti §5b/5c) | 11.186 | 95 | 133 |
| **Suite completa** (finale, 22'54") | **11.247** | **35** (tutti preesistenti al baseline, vedi sotto) | 133 |
| Baseline di riferimento (`b0a2e05`, CESTINO-RICHIESTE-1, oggi 14:54) | 10.997 | 41 + 49 error | 115 |

Copertura RED/GREEN richiesta: A (0 appointment), B (1 `requested` con tutti i collegamenti, evento `created` di sistema, nessuna `stima_inspections`), C (hook ripetuto + sync manuale: 1 riga, 1 evento), D (hook e sync manuale in due thread con barriera: 1 riga, 1 vincitore), E (import KO e DB Agenda KO: dettagliata salvata, `{"ok": true}`, log senza PII, stessa identità → nessuna seconda riga, recupero con sync manuale; E2: ora DST inesistente → scartata e segnalata), F/G (agenzie 1 e 2; `repository.get_appointment` dell'altra agenzia → `None`), H (`not_synced`, nessuna riga in `appointment_calendar_sync`), I (retry stessa identità ×3 → 1 dettagliata, 1 appointment; I2 doppio click concorrente → 1/1; I3 identità nuove → 2 record, 2 `requested`). Flusso 1: nuovo cliente (stima → routing agenzia 2 → contatto → lead → `lead_stime` → scheda → 3 pertinenze, 2 eventi SI), cliente esistente (stesso contatto, nuovo lead), email di A + telefono di B (bridge `conflict` → ricevuta `partial`, nessun contatto/lead inventato, stima scritta), retry e doppio click concorrente (nessun duplicato).

### I 35 fallimenti finali della suite completa

Confronto per nome con il baseline di oggi (`full_ch1_pg.failed`, 90 voci: 41 fail + 49 error):

- **32 su 35 sono gli stessi del baseline**: sentinelle di working tree e ambiente (`test_a32_2 s14`, `lmc15 37/41/42`, `p29_3 19`, `p29_3c/3d/3e/3g` «file toccati / P29_2_0», `lmc2 f4`, `lmc7/8/9` «domini vietati / nessuna migration», `followup_isolation` «followup» in `core/repository.py`, `lmc11 h1`, `lmc13 e1`, `next2_router_hardening` 1/2, `p26_6_agenda_realapp 03`, fixture PG di `a30_1` (4), `a30_2` (2), `a30_8` (1), `lmc2`). Nessuno riguarda il funnel pubblico, le ricevute, il PDF o l'Agenda.
- **3 nuovi rispetto al baseline, nessuno causato da questo lavoro**: `a30_7_legacy_schedule_postgres::test_30/31` (prima nascosti dai 22 ERROR di fixture del file, che ora gira: falliscono per `agent_working_hours` assente nella fixture A30-2 — **riprodotto identico sul clone pulito `b0a2e05`**); `test_a32_2 s15` (diff di `main.py` non ancora committato → verde a commit fatto, come `s14`; non toccati per regola).
- **59 voci del baseline tornano verdi** (le 49 ERROR di `a30_6/a30_7_postgres` e 10 FAILED, fra cui `cestino_edifici_1`, `p27_7`, `p29_3g`).

## 9. Punti P1 rimasti

1. **`property_id` sull'appointment** (D6 A30-6): NULL. La scheda esiste ed è collegata alla stima (`property_site_sources`, provata in B/F1), ma collegarla all'appointment richiede una regola nuova nell'import (quale scheda se più d'una, Cestino, agenzia). Non fatto. Oggi l'operatore la vede dalla stima.
2. **Una sola richiesta aperta per stima alla creazione**: il modello lo applica solo al «Fissa» (D5). Se la si vuole anche alla richiesta automatica, va applicata **anche al sync manuale e alla creazione manuale**, con `lock_stima`+`open_inspection_for_stima` — scelta di dominio, non di integrazione.
3. **Immagini della preview** (20 binari, 27 MB in `site-preview/public`): decidere se restano nel repo.
4. **Decisione R1 aperta**: il Pixel `Lead` parte anche per un risultato certificato con notifica incerta (già segnalata nella review R1).
5. Runtime macOS originale (PG 18.6, Chrome for Testing) non ricertificato in questo ambiente (PG 16 / Chromium Linux), come già per R1.
6. Dopo il commit: rieseguire `test_a32_2 s14/s15` (working tree pulito) e, se si vuole, la suite completa sul Mac.
7. La `.git/objects/maintenance.lock` nel repo Mac (§1).

## 10. `git diff --stat` (worktree vs `b0a2e05`)

- Totale: **138 file, +54.071 / −1.096** (di cui `site-preview/` 45 file e `docs/` 13 file della candidata).
- Senza `site-preview/` e `docs/`: **80 file, +8.587 / −1.096**.
- Prodotto e test dell'aggancio: `appointments_legacy/site_hook.py` +118 (nuovo), `appointments_legacy/stime_dettagliate_import.py` +14/−3, `main.py` (candidata +11 dell'aggancio), `tests/public_submission_fakes.py` +192 (nuovo), `tests/test_stima_crm_agenda_1_postgres.py` +518 (nuovo).
- Stat completo per file nel messaggio di review e in `$S/implv/patch/`.

## 11. `git status`

Nell'indice (staged): i 101 file della candidata con le rinumerazioni e le 4 sentinelle risolte in FASE 1. Nel working tree (non staged): FASE 2/3 e i riallineamenti — 44 file modificati e 3 nuovi (`appointments_legacy/site_hook.py`, `tests/public_submission_fakes.py`, `tests/test_stima_crm_agenda_1_postgres.py`), più questo rapporto in `roadmap/`. Elenco completo nel messaggio di review. Nulla è committato.

## 12. Conferme

- Nessun accesso a PROD né a Render; nessun deploy; nessun push; nessun commit.
- Nessun invio reale (email/WhatsApp/SMS): provider sostituiti da doppi, variabili d'ambiente rimosse dalla guardia.
- Nessuna importazione storica: l'aggancio importa SOLO il record appena salvato; nessuna CLI eseguita su dati veri.
- Nessuna migration applicata a database condivisi; nessuna migration esistente modificata.
- Repo Mac e candidata intatti; HTML/grafica/logo/PDF/email/WhatsApp/frontend pubblico/UI Agenda invariati rispetto alla candidata verificata (R1).
- PostgreSQL isolato del container: arrestato a fine lavoro.
