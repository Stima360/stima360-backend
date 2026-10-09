# Fase 4 — configurazione proposta, non attivata

Clone di lavoro: `/private/tmp/stima360-phase4-clean`, branch `core-0.1-test`,
base `2fcde47`. Nessuna modifica al checkout originale.

Prerequisito: migration 096 additiva nel CRM, tramite la procedura migration
approvata per il target. La 095 resta invariata. Nessuna migration applicata
su database remoto. Nessuna modifica alle stime o al ledger storico.

Con DB_* del CRM e SITE_DB_URL della sorgente in sola lettura già configurati:

```sh
python3 run_site_import_cron.py --initialize-baseline
```

La baseline fotografa gli ID di entrambe le tabelle in un solo snapshot,
anche per righe ancora recenti; scrive solo nel CRM, sotto lo stesso lock
usato dall'importatore. Ripetere il comando non amplia la baseline. Nessun
MAX(id), nessun filtro sulla data: nuove righe con ID inferiori restano importabili.
Il confine è lo snapshot delle righe committate durante l'inizializzazione.
Senza baseline il cron rifiuta l'importazione. Un errore prima del commit
non lascia una baseline parziale. Le 21 stime già importate non vengono toccate.
Non reinizializzare dopo un riavvio o deploy.

## Cron Render esistente (proposta manuale)

- Repository: `Stima360/stima360-backend`; branch: `core-0.1-test`.
- Build: `pip install -r requirements.txt -r requirements-dev.txt`.
- Command: `python3 run_site_import_cron.py`.
- Schedule: `*/5 * * * *` (UTC).
- DB_*: CRM; SITE_DB_URL: database sito con utente SELECT-only.
- SITE_IMPORT_BATCH=100; SITE_IMPORT_SETTLE_MINUTES=10.
- SITE_PDF_GITHUB_REPO: archivio originale, SITE_PDF_GITHUB_BRANCH: branch archivio.
- SITE_PDF_GITHUB_TOKEN opzionale, solo lettura; mai inserirlo nel repository.
- Stato: mantenere sospeso; nessun servizio nuovo, nessuna attivazione ora.

Compatibile con un servizio cron Render Python esistente che sia dedicabile
all'importazione e abbia accesso ai due database. Non è stato identificato o
modificato alcuno dei sei servizi: se uno deve conservare altre funzioni,
non sostituirne il comando. Verificare il servizio concreto prima di riutilizzarlo.
Documentazione: https://render.com/docs/cronjobs

L'importatore riusa SITE-IMPORT-1, compresa Agenda e PDF originale. Nessuna
email, WhatsApp, sequenza o task commerciale. Errori HTTP GitHub (inclusi
403/429) mantengono il passo PDF in errore ritentabile anche oltre max_attempts;
i passi riusciti non si ripetono. Nessun PDF viene rigenerato.

Eccezione autorizzata: una dettagliata successiva alla baseline può importare
il solo genitore storico necessario, se assente nel CRM. Il genitore segue
SITE-IMPORT-1 (PDF incluso), con un marcatore persistente per riprendere i retry.
Le altre stime storiche e i genitori già presenti nel CRM restano intatti.
