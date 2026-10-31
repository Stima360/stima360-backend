-- Down for 126. I segni di lettura sono solo uno stato dell'interfaccia: la
-- loro perdita fa tornare «non letti» i messaggi, nessun messaggio si perde.
-- La transazione e' del runner.
DROP TABLE whatsapp_inbound_reads;
DROP FUNCTION whatsapp_inbound_reads_guard();
