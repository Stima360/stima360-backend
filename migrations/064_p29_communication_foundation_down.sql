-- Down for 064. Removes the communication foundation and nothing else.
--
-- 064 ha creato DUE tabelle, sette indici, quattro funzioni e quattro trigger,
-- e non ha alterato nulla di preesistente. L'inverso e' esatto.
--
-- COSA SIGNIFICA TOGLIERE QUESTE TABELLE
--
-- Distrugge lo storico delle comunicazioni e dei tentativi di invio. E' la
-- descrizione onesta, ed e' il motivo per cui questo file esiste invece di
-- rifiutare.
--
-- Cio' che NON distrugge e' nulla di preesistente. P29-2.1 e' schema puro:
-- nessuna riga di `contacts`, `leads`, `stime`, `properties`, `activities` o
-- `seller_timeline_events` e' stata scritta o modificata, e nessun percorso
-- applicativo scrive ancora in queste due tabelle. Al momento in cui questo
-- down e' scritto, eseguirlo subito dopo l'up non perde alcun dato, perche' non
-- ce n'e'.
--
-- Diventera' distruttivo appena P29-2.2 comincera' a scrivere. Prima di
-- eseguirlo su un ambiente dove sia stato registrato anche un solo messaggio,
-- esportare entrambe le tabelle: le righe non sono ricostruibili da
-- nient'altro nel database. `activities` ne conterra' al piu' una proiezione
-- sintetica dei soli messaggi inviati - non il corpo, non il destinatario reale,
-- non i tentativi, non gli errori.
--
-- ORDINE OBBLIGATO
--
-- `communication_attempts` PRIMA di `communication_messages`: la FK composita
-- va dai tentativi al messaggio, e senza CASCADE sul DROP la tabella genitore
-- non si lascia togliere per prima. E' voluto: e' la stessa dipendenza che in
-- su ha imposto di creare le due tabelle nella stessa migration.
--
-- I trigger prima delle funzioni, le funzioni prima delle tabelle, per la
-- stessa ragione di 057_down, 061_down e 062_down: DROP TABLE non si porta via
-- le funzioni, che resterebbero orfane.
--
-- Down files bracket their own transaction; the runner owns none here.

BEGIN;

DROP TRIGGER IF EXISTS trg_communication_attempts_guard ON communication_attempts;
DROP TRIGGER IF EXISTS trg_communication_attempts_no_truncate ON communication_attempts;
DROP TRIGGER IF EXISTS trg_communication_messages_guard ON communication_messages;
DROP TRIGGER IF EXISTS trg_communication_messages_no_truncate ON communication_messages;

DROP FUNCTION IF EXISTS communication_attempts_guard();
DROP FUNCTION IF EXISTS communication_attempts_no_truncate();
DROP FUNCTION IF EXISTS communication_messages_guard();
DROP FUNCTION IF EXISTS communication_messages_no_truncate();

-- Senza CASCADE, di proposito: nulla al di fuori di queste due tabelle deve
-- dipendere da loro, e se qualcosa ne dipende questo file deve fallire e dirlo.
DROP TABLE IF EXISTS communication_attempts;
DROP TABLE IF EXISTS communication_messages;

DELETE FROM schema_migrations WHERE version = '064_p29_communication_foundation';

COMMIT;
