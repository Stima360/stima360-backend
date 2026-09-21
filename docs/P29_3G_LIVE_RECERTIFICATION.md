# P29-3G — Micro-ricertificazione live

**Non eseguita.** Tre prove, dopo il push su TEST. Tutto il resto della
catena — provision, cutoff, M1, M2, stop autorevole, unsubscribe, cron,
concorrenza — è già certificato in P29-3F e **non va rifatto**: questa fase
ha toccato una lettura e un contratto temporale, non il motore.

Serve una sessione operatore che veda tutti i record dell'agenzia.

## LIVE TEST A — l'anteprima

Aprire il Contact 360 di un contatto che ha una `stima_pdf` già in archivio
(su TEST, il contatto 2 ne ha tre) e guardare la colonna dell'anteprima nella
tab Comunicazioni.

Atteso: testo leggibile — "Il tuo Piano Vendita…" — e **nessun tag**. Prima
si leggeva `<div style="font-family:Arial,Helvetica,sans-serif; color:#222…`,
identico su tutte e tre le righe. Controllare anche una riga di testo puro
(un messaggio manuale): deve essere rimasta esattamente com'era.

## LIVE TEST B — assistito NON ancora dovuto

Serve un'iscrizione ferma su M3 con `next_action_at` nel futuro. Sul TEST
attuale la journey è `retired` e l'iscrizione `stopped`, quindi va ricreata:
riattivare `stima_lead` v1, una stima sintetica nuova verso la casella
controllata, e portarla fino a M3 con i time-shift già documentati in
P29-3F — **senza** l'ultimo, così M3 resta dovuta fra sette giorni.

Atteso, in due punti:

Nella card, i due bottoni "Approva e invia" e "Salta questo passo"
**non compaiono**; l'etichetta continua a dire che l'iscrizione aspetta una
persona, perché è vero.

Chiamando direttamente l'API, senza passare dall'interfaccia:
`POST /api/communication/journeys/enrollments/{id}/send-current` → **409**, e
`POST /api/communication/journeys/enrollments/{id}/skip-current` → **409**.
Il secondo è il difetto vero: prima rispondeva 200 e saltava il passo.

Subito dopo i due 409, rileggere l'iscrizione: `next_step_no`, `run_no`,
`next_action_kind` e `next_action_at` devono essere **identici** a prima, e
non deve esistere nessun messaggio nuovo.

## LIVE TEST C — assistito dovuto

Applicare l'ultimo time-shift (`next_action_at = NOW() - interval '1 minute'`).

Atteso: i due bottoni compaiono; `send-current` crea M3 con `mode: assisted`;
su un'iscrizione separata, `skip-current` salta e avanza. Le due azioni vanno
provate su **iscrizioni diverse**, altrimenti la prima consuma il passo della
seconda — è l'errore che ha bruciato M3 durante P29-3F.

## Cosa fermerebbe la ricertificazione

Un tag nell'anteprima. Un bottone offerto prima della scadenza. Uno
`skip-current` anticipato che risponde 200. Un'iscrizione che cambia di un
campo dopo un 409.
