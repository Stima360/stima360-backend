STIMA VOICE - comandi vocali del CRM (Fasi 1-5)
===============================================

STATO: SPENTO. Nessun utente TEST o PROD puo' usarlo. Accenderlo richiede
un'autorizzazione esplicita e tutti i passi della sezione "Abilitazione".


ARCHITETTURA
------------
Il modello AI interpreta; il codice deterministico decide e scrive.

  testo dettato
    -> voice/provider.py   fornitore AI: JSON validato contro lo schema chiuso
                           di voice/schemas.py (intenti elencati, nessun id,
                           nessun SQL). Al modello va SOLO il testo.
    -> voice/planner.py    piano deterministico, validato con gli schemi CRM
    -> voice/executor.py   risoluzione (resolver.py, permessi della sessione),
                           politica (policy.py) + guardie Fase 4/5, registro
    -> voice/ledger.py     registro idempotente (migration 098)
    -> voice/operations.py servizi CRM ESISTENTI (nessuna scrittura diretta)
    -> voice/undo.py       annullamento protetto (migration 099)
    -> voice/reconcile.py  passi incerti: analisi in sola lettura + decisione umana
  voice/service.py         orchestrazione delle API, abilitazione, limiti
  voice/router.py          API /api/voice (NON montato in main.py)

Agenzia, operatore, ruolo e permessi vengono solo dalla sessione
(require_operator). Il modello non sceglie id, agenti o azioni: le scelte fra
candidati che arrivano dal client sono valide solo se sono fra i candidati che
il CRM mostra a quell'operatore.


ABILITAZIONE (fail-closed: servono TUTTI)
-----------------------------------------
1. Migrazioni 098_voice_commands e 099_voice_undo applicate con
   scripts/p26_migrate.py (oggi NON applicate su TEST ne' PROD). Senza la 098
   le API rispondono 403 VOICE_DISABLED.
2. Router montato in main.py (oggi NON montato; decisione separata).
3. STIMA_VOICE_ENABLED=1 nell'ambiente del server (qualsiasi altro valore o
   assenza = spento, 403 VOICE_GLOBALLY_DISABLED).
4. Riga in voice_agency_settings con enabled=TRUE per l'agenzia (default
   FALSE). La modalita' di partenza e' 'review': ogni passo chiede conferma.
5. Fornitore AI: STIMA_VOICE_AI_URL (HTTPS, chat completions compatibile
   OpenAI), STIMA_VOICE_AI_MODEL, STIMA_VOICE_AI_KEY, opzionale
   STIMA_VOICE_AI_TIMEOUT (secondi di lettura, default 20). Nessun modello di
   default. Senza configurazione: 503 VOICE_PROVIDER_NOT_CONFIGURED.
Il platform admin non usa STIMA Voice, neanche in acting (D-E).


API (prefisso /api/voice, sessione obbligatoria)
------------------------------------------------
POST /commands                         {client_command_id: uuid, text}
                                       -> piano con domande/conferme; esegue da
                                       solo SOLO se ogni passo e' "auto".
                                       Stessa chiave = stesso comando (nessuna
                                       nuova interpretazione).
GET  /commands/{id}                    stato, passi, domande, candidati, undo
POST /commands/{id}/confirm            {all: bool, steps: [{ordinal, choices}]}
POST /commands/{id}/execute            esegue cio' che e' confermato/automatico
POST /commands/{id}/cancel
POST /commands/{id}/steps/{n}/retry    un passo fallito, solo su richiesta
GET  /commands/{id}/undo               stato di annullamento per passo
POST /commands/{id}/undo               tutto il comando, in ordine inverso
POST /commands/{id}/steps/{n}/undo
GET  /commands/{id}/steps/{n}/reconcile   analisi di un passo incerto
POST /commands/{id}/steps/{n}/reconcile   {outcome: present|absent, record_id}

Errori: sempre {"detail", "code"}. Fornitore non raggiungibile: 503
VOICE_PROVIDER_UNAVAILABLE (nessuna azione, si ripete con la stessa chiave).
Output del modello fuori schema: 502 VOICE_MODEL_OUTPUT_INVALID (nessuna
azione). Limite giornaliero: 429 VOICE_DAILY_LIMIT.

Una domanda che chiede DATI (cognome, indirizzo, comune valido, un altro
orario) non si scioglie con una conferma: si detta di nuovo con i dati.


LIMITI NOTI (dichiarati, non nascosti)
--------------------------------------
1. Un comando chiuso solo con la riconciliazione resta "partial" come stato
   complessivo anche con tutti i passi conclusi: la 098 non ammette
   partial -> completed senza esecuzione. La vista lo segnala con
   all_steps_concluded=true e una nota.
2. Comuni fuori dal catalogo del CRM: dalla Fase 5 si rilevano nel piano
   (municipality_not_supported, domanda non confermabile) con la stessa regola
   del censimento (property.catalog.validate_location), PRIMA di scrivere.
   Il censimento resta comunque l'ultima difesa alla scrittura.
Altri:
- il limite giornaliero (max_commands_per_day, per agenzia, giorno Europe/Rome)
  e' morbido: due richieste concorrenti al confine possono superarlo di poco;
- max_audio_seconds_month non si applica: in Fase 5 non c'e' audio (Fase 6);
- il lock d'identita' dei contatti mette in fila i dettati concorrenti, non una
  creazione contemporanea fatta dall'interfaccia del CRM;
- collegamento proprietario e «Vende» riaperto/riutilizzato non si annullano
  con la voce (D-A); edifici con unita' e appuntamenti avvenuti nemmeno.


TEST
----
tests/test_voice_*.py (Fasi 1-5). Solo PostgreSQL locale usa-e-getta
(P29_TEST_DSN) e fornitore FINTO (voice/fake_provider.py): nessun modello
reale, nessuna scrittura su TEST o PROD.
