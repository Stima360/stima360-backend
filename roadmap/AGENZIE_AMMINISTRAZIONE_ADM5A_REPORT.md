# STIMA360 CRM — ADM-5A: Registro amministrativo

- Branch: `core-0.1-test`, base pubblicata `6c42c6c` (ADM-4).
- Nessuna migration, nessuna schermata, nessun accesso alle agenzie sospese (ADM-5B).
- Email, follow-up, Voice, Cestino e cron non toccati.
- Nessun commit, push o deploy.
- Prove solo su PostgreSQL 16 locale e usa-e-getta.

## 1. Cosa è stato realizzato

### 1.1 `GET /api/platform/audit`

- **Accesso:** solo il Super Admin designato (`require_super_admin_context`, come le altre rotte di Rete). Gli altri amministratori di piattaforma ricevono 403, gli agenti 403, gli anonimi 401.
- **Ordine:** dal più recente (`created_at DESC, id DESC`).
- **Paginazione:** `limit` (50 di default, massimo 200 imposto dalla rotta) e `offset`.
- **Filtri facoltativi e combinabili:**

  | Parametro | Campo |
  |---|---|
  | `action` | azione |
  | `result` | esito |
  | `agency_id` | agenzia |
  | `target_type`, `target_id` | oggetto |
  | `since` (incluso), `until` (escluso) | periodo |

  Come per le liste dei territori, un valore inesistente restituisce una lista vuota. Parametri incoerenti danno 422: `since` non precedente a `until`, oppure `limit` e `offset` fuori dai limiti.
- **Ammissioni escluse di default.** Le righe `platform.admission`, una per richiesta, non compaiono. Si consultano chiedendole per nome con `action=platform.admission`.
- **Ogni riga è restituita com'è stata scritta:**
  - `id`, `created_at`;
  - `actor_user_id` e `actor_label` originali, senza ricalcoli dall'anagrafica;
  - `action`, `result`;
  - `target_type`, `target_id`, `target_agency_id`;
  - `metadata`.
- **Sola lettura:** la transazione è `READ ONLY`, quindi il database rifiuterebbe qualunque scrittura. L'unica riga scritta è l'ammissione, già prevista da P27-1.

### 1.2 Valori prima e dopo dei cambi di stato (da ora in avanti)

Quando lo **stato** cambia davvero, i metadati aggiungono:

```json
"status_change": {"from": "active", "to": "suspended"}
```

| Operazione | Azione registrata |
|---|---|
| Agenzia | `platform.agency.update` |
| Operatore | `platform.operator.update` |
| Appartenenza | `platform.membership.update` |
| Assegnazione territoriale | `platform.territory.assignment.update` |
| Alias | `platform.territory.alias.update` |

Regole applicate:

- Le chiavi esistenti restano invariate: `changed_fields`, e per gli alias `from_status` e `from_territory_id`.
- Se lo stato non cambia, ad esempio quando si rinomina un'agenzia, non compare nessun `status_change`.
- Solo valori di stato, che appartengono a un elenco chiuso. La regola di P27-2 resta: nomi, indirizzi e configurazioni non entrano mai nel registro (`test_b7`, invariato e verde).
- Le righe registrate prima di ADM-5A **non sono toccate**: la tabella è append-only.

**Trasferimento di territorio: invariato.** Il codice documenta una scelta esplicita: l'agenzia che perde il territorio non va nei metadati, perché è già nella riga di assegnazione revocata. Lo storico resta ricostruibile da quella riga, il cui id è restituito dal trasferimento.

## 2. File modificati

| File | Modifica |
|---|---|
| `platform_admin/enums.py` | `AUDIT_STATUS_CHANGE_KEY`, `AUDIT_PAGE_DEFAULT`, `AUDIT_PAGE_MAX` |
| `platform_admin/transaction.py` | helper `status_change(prima, dopo)` |
| `platform_admin/agencies_service.py`, `operators_service.py`, `territories_service.py`, `aliases_service.py` | `status_change` nei metadati dei cinque aggiornamenti di stato |
| `platform_admin/audit_read_repository.py` (nuovo) | la SELECT del registro, con i filtri |
| `platform_admin/audit_read_service.py` (nuovo) | transazione `READ ONLY` |
| `platform_admin/schemas.py` | `AuditEntryResponse` |
| `platform_admin/router.py` | `GET /audit` |
| `tests/test_adm5a_audit_read_postgres.py` (nuovo) | collaudo |
| `tests/test_adm1_super_admin_guard.py`, `tests/test_p27_6_lead_routing.py` (`test_i2`), `tests/test_p27_2_agencies.py` (`test_b6`) | sentinelle aggiornate in modo dichiarato (vedi §3) |

`platform_admin/repository.py` resta il solo writer del registro e non contiene letture, come dichiara il suo docstring.

## 3. Test e risultati

**Collaudo mirato:** `tests/test_adm5a_audit_read_postgres.py`, **7 superati**.

| Test | Verifica |
|---|---|
| `test_01` | permessi: Super Admin designato 200; altro amministratore di piattaforma 403; agente 403; anonimo 401 |
| `test_02` | agenzia, operatore, appartenenza e assegnazione portate fuori stato e riattivate, più un alias revocato: due righe per oggetto dalla più recente, valori prima e dopo esatti, autore con id ed etichetta originali, chiavi precedenti dell'alias conservate |
| `test_02b` | una rinomina senza cambio di stato non produce `status_change` |
| `test_03` | ammissioni escluse di default, consultabili con `action=platform.admission` |
| `test_04` | filtri per esito (il rifiuto di un altro amministratore compare come `denied`), agenzia, periodo, azione inesistente; 422 su periodo invertito e su `limit`/`offset` fuori dai limiti |
| `test_05` | due pagine contigue uguali alla pagina intera, nessuna sovrapposizione; massimo di default 50 |
| `test_06` | dieci letture non cambiano nessuna riga del registro oltre alle ammissioni |

**Regressione pertinente**

- **PostgreSQL ADM-1, ADM-2, ADM-3 (continuità, browser compreso), ADM-4 e ADM-5A**, eseguite insieme: **34 superati**.
- **Unitari di piattaforma** (`test_p27_*`, guardia ADM-1): `38 failed, 871 passed, 38 skipped, 10 errors`.
  - I 38 fallimenti sono tutti nella baseline ADM-3: test UI in Node, `test_i1_059`, e altri preesistenti.
  - I 10 errori sono `p27_7_network_e2e`, che richiede un ambiente browser dedicato: preesistente.
- `git diff --check`: pulito.

**Sentinelle aggiornate in modo dichiarato**

- `test_adm1_super_admin_guard`: le rotte che richiedono il Super Admin designato passano da 27 a **28**. La nuova rotta è protetta.
- `test_p27_6_lead_routing::test_i2`: la superficie esatta di piattaforma include `GET /api/platform/audit`.
- `test_p27_2_agencies::test_b6`: il metadato di un cambio di stato ora contiene anche `status_change`. `test_b7` (nessun valore libero nel registro) non è stato toccato e resta verde.

## 4. Limiti e punti aperti

1. **Righe precedenti ad ADM-5A:** riportano solo i nomi dei campi cambiati, senza i valori. Per scelta non vengono riscritte.
2. **Chi ha agito: solo id ed etichetta originali.** Decisione del proprietario, approvata e definitiva: il registro non mostra nome ed email attuali dell'operatore.
3. **Rischi del registro, invariati:**
   - trigger non `ENABLE ALWAYS` (R13), rinviato insieme a D1 di ADM-4;
   - crescita delle righe di ammissione, che non vengono cancellate.
4. **Schermata «Registro»:** ADM-7.
5. **Accesso in sola lettura alle agenzie sospese o archiviate:** ADM-5B.

**STOP.** Nessun commit, push o deploy. Attendo il prossimo via libera.
