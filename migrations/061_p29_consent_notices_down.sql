-- Down for 061. Removes the consent notice registry and nothing else.
--
-- 061 ha creato UNA tabella, due indici, due funzioni e due trigger, e non ha
-- alterato nulla di preesistente. L'inverso e' quindi esatto: si toglie cio'
-- che ha creato, in ordine di dipendenza, e si lascia intatto ogni oggetto di
-- P26, P27 e P28.
--
-- COSA SIGNIFICA TOGLIERE QUESTA TABELLA
--
-- Distrugge il registro delle notice. E' la descrizione onesta, ed e' il
-- motivo per cui questo file esiste invece di rifiutare: 061 e' reversibile
-- nel senso dello schema, e un down che mente sulla propria reversibilita' e'
-- peggio di uno che cancella.
--
-- Prima di eseguirlo su un ambiente dove sia stata pubblicata anche una sola
-- notice, esportare la tabella. Le righe non sono ricostruibili da nient'altro
-- nel database: e' esattamente il punto di un registro di prove.
--
-- ORDINE OBBLIGATO
--
-- Questo down deve girare DOPO il down di 062, non prima. `consent_events
-- .notice_id` porta una FK verso questa tabella: finche' 062 esiste, il DROP
-- qui sotto fallisce. Il fallimento e' corretto e voluto - segnala che si sta
-- smontando la pila dal lato sbagliato - e non viene addolcito con un CASCADE,
-- che porterebbe via `consent_events` in silenzio.
--
-- I trigger vengono tolti prima delle funzioni, e le funzioni prima della
-- tabella: DROP TABLE si porterebbe via i propri trigger ma non le due
-- funzioni, che sono oggetti di schema e sopravvivrebbero come orfani.
--
-- Down files bracket their own transaction; the runner owns none here.

BEGIN;

DROP TRIGGER IF EXISTS trg_consent_notices_immutable   ON consent_notices;
DROP TRIGGER IF EXISTS trg_consent_notices_no_truncate ON consent_notices;

DROP FUNCTION IF EXISTS consent_notices_immutable();
DROP FUNCTION IF EXISTS consent_notices_no_truncate();

-- I due indici se ne vanno con la tabella; nominarli sarebbe rumore. La
-- tabella viene tolta senza CASCADE di proposito: nulla dovrebbe dipenderne, e
-- se qualcosa ne dipende questo file deve fallire e dirlo, non portarsi via in
-- silenzio quel qualcosa.
DROP TABLE IF EXISTS consent_notices;

-- La riga di ledger, come ogni down della serie: la 061 torna applicabile.
-- La chiave e' lo STEM del file e non il numero, come in 057_down, 058_down,
-- 059_down e 060_down.
DELETE FROM schema_migrations WHERE version = '061_p29_consent_notices';

COMMIT;
