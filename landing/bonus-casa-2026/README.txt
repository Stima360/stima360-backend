Landing page STIMA360 — Diagnosi Vendita gratuita + guida Bonus Casa 2026
=========================================================================

Contenuto:
  index.html                 pagina (HTML + CSS integrato, nessun JavaScript, nessuna dipendenza)
  assets/logo.jpg            logo ufficiale (copia identica di communication/brand/stima360_logo.jpg)
  assets/locandina.webp      locandina promozionale usata come immagine separata
  assets/bonus-casa-2026.pdf guida da scaricare (copia identica di communication/guida/STIMA360_Guida_Vendere_Casa_2026.pdf)

Pubblicazione:
  caricare l'intera cartella (index.html + assets/) nella posizione scelta sul sito.
  Tutti i percorsi sono relativi; il pulsante "Verifica il tuo Comune e valuta casa"
  punta a https://www.stima360.it.

PDF:
  il pulsante "Scarica il PDF gratuito" punta a assets/bonus-casa-2026.pdf
  (attributo download, nome proposto "Bonus-Casa-2026-STIMA360.pdf").
  Per sostituire la guida basta sovrascrivere il file con lo stesso nome.

Pubblicazione su Render (Static Site separato dal CRM):
  Dashboard Render -> New -> Static Site -> repository stima360/stima360-backend
    Name:               stima360-bonus-casa-2026
    Branch:             core-0.1-test
    Root Directory:     landing/bonus-casa-2026
    Build Command:      (vuoto; se obbligatorio: echo ready)
    Publish Directory:  .
  Nessuna variabile d'ambiente, nessun database, nessun collegamento al CRM.
  URL atteso: https://stima360-bonus-casa-2026.onrender.com
  Prerequisito: la cartella deve essere committata e pushata su core-0.1-test.
