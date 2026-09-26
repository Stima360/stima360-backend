"""A30-6 - adattatori di import dalle tabelle legacy del sito verso l'Agenda.

Questo package NON fa parte del dominio autorevole dell'Agenda
(`appointments/`): e' un adattatore di backfill, il solo punto del CRM che
legge `stime_dettagliate` per portarne i sopralluoghi richiesti in
`appointments` (decisione D5 del gate A30-6). Il package `appointments/`
continua a non nominare mai la tabella del sito (D10, `test_a30_2::test_32`).
"""
