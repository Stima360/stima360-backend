"""P29-1 CONSENT - the single write path for consent in STIMA360.

Un modulo additivo. Non sostituisce niente, non riscrive niente, e non
conosce ne' provider, ne' template, ne' scheduler, ne' messaggi.

COSA FA

Registra due sole cose - una concessione e una revoca - e mantiene allineata
la proiezione dello stato corrente su `contacts`. Evento e proiezione nella
stessa transazione, sempre: non esiste un percorso che scriva l'uno senza
l'altro.

COSA NON FA, E QUANDO LO FARA'

* non e' agganciato al bridge pubblico  -> P29-1.4
* non espone `can_send_marketing`       -> P29-1.5
* non ha UI                             -> P29-1.7
* non tocca `core.repository.update_contact`, che oggi puo' ancora cambiare
  `marketing_consent` fuori da qui      -> P29-1.3

L'ultimo punto e' il rischio noto e dichiarato di questa fase: il percorso
unico esiste, ma la vecchia porta e' ancora aperta finche' P29-1.3 non la
chiude.

IL CASO D, PER COSTRUZIONE

Una casella marketing non spuntata su una nuova richiesta di stima NON e' una
revoca. Questo modulo non ha alcuna API che trasformi un booleano falso in un
evento: c'e' `record_grant`, c'e' `record_revocation`, e c'e'
`record_optional_grant` che non fa nulla quando non c'e' niente da
concedere. Non esiste un `set_marketing(bool)`, e non deve esistere.
"""
