-- Down for 110. Toglie link personali, invii della pagina, contatore anti-abuso
-- e il codice `consent_confirmation`; riporta il CHECK dei codici alla 108.
--
-- Si FERMA, senza modifiche, se esiste anche un solo link, un solo invio della
-- pagina o una sola email di conferma: toglierli significherebbe cancellare la
-- prova di consensi reali.
BEGIN;

DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM consent_links)
    OR EXISTS (SELECT 1 FROM consent_submissions)
    OR EXISTS (SELECT 1 FROM communication_messages WHERE reason_code = 'consent_confirmation') THEN
        RAISE EXCEPTION '110 down: esistono link, invii o conferme del consenso; nessuna modifica eseguita';
    END IF;
END
$do$;

ALTER TABLE communication_messages DROP CONSTRAINT communication_messages_reason_code_chk;
ALTER TABLE communication_messages ADD CONSTRAINT communication_messages_reason_code_chk
    CHECK (reason_code IN ('stima_pdf','operator_manual','operator_reply','owner_login_link','appointment_reminder',
        'm1','m2','m3','m4','m5','m6','m7','m8','m9','m10',
        'm11','m12','m13','m14','m15','m16','m17','m18','m19','m20'));

DROP TABLE IF EXISTS consent_page_rate_limits;

DROP TRIGGER IF EXISTS trg_consent_submissions_guard ON consent_submissions;
DROP FUNCTION IF EXISTS consent_submissions_guard();
DROP TABLE IF EXISTS consent_submissions;

DROP TRIGGER IF EXISTS trg_consent_links_guard ON consent_links;
DROP FUNCTION IF EXISTS consent_links_guard();
DROP TABLE IF EXISTS consent_links;

COMMIT;
