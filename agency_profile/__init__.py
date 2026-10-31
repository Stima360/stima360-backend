"""BLOCCO-1B - i dati dell'agenzia per i report e il suo logo.

I dati vivono in `agencies.settings` (la stessa anagrafica della 027, sotto la
chiave `report_profile`), il logo nella tabella `agency_logos` (migrazione 115).
`service.report_identity(ctx)` e' il punto di lettura condiviso per i futuri
report: dati e logo dell'agenzia del contesto autenticato, mai di un'altra.
"""
