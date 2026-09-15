-- Down for 062. Removes the consent event history and nothing else.
--
-- 062 ha creato UNA tabella, due indici, due funzioni e due trigger, e non ha
-- alterato nulla di preesistente. L'inverso e' esatto.
--
-- COSA SIGNIFICA TOGLIERE QUESTA TABELLA
--
-- Distrugge lo storico dei consensi. E' la descrizione onesta, ed e' il motivo
-- per cui questo file esiste invece di rifiutare.
--
-- Cio' che NON distrugge e' lo stato corrente: `contacts.marketing_consent`,
-- `contacts.marketing_consent_at` e le colonne di proiezione di 063 restano
-- dove sono, con i valori che hanno. Il sistema torna esattamente allo stato
-- pre-P29: uno stato corrente affidabile quanto lo era prima, cioe' senza
-- prova di come ci sia arrivato. P24 continua a funzionare senza accorgersi di
-- niente, perche' legge `marketing_consent IS TRUE` e quella colonna e'
-- intatta.
--
-- Prima di eseguirlo su un ambiente dove sia stato registrato anche un solo
-- evento, esportare la tabella. Le righe non sono ricostruibili da nient'altro
-- nel database: e' il punto di uno storico append-only.
--
-- ORDINE OBBLIGATO
--
-- Questo down gira PRIMA del down di 061 (che porta via `consent_notices`,
-- verso cui questa tabella ha una FK) e PRIMA o DOPO quello di 063
-- indifferentemente: 063 tocca `contacts`, non questa tabella, e la FK fra le
-- colonne di proiezione e `consent_notices` non passa da qui.
--
-- I trigger prima delle funzioni, le funzioni prima della tabella, per la
-- stessa ragione di 057_down e 061_down: DROP TABLE non si porta via le
-- funzioni, che resterebbero orfane.
--
-- Down files bracket their own transaction; the runner owns none here.

BEGIN;

DROP TRIGGER IF EXISTS trg_consent_events_append_only ON consent_events;
DROP TRIGGER IF EXISTS trg_consent_events_no_truncate ON consent_events;

DROP FUNCTION IF EXISTS consent_events_append_only();
DROP FUNCTION IF EXISTS consent_events_no_truncate();

-- Senza CASCADE, di proposito: nulla deve dipendere da questa tabella, e se
-- qualcosa ne dipende questo file deve fallire e dirlo.
DROP TABLE IF EXISTS consent_events;

DELETE FROM schema_migrations WHERE version = '062_p29_consent_events';

COMMIT;
