# Recupero del run `42e32975ccd6` — procedura

Un solo passo eseguibile adesso: il **censimento**. Tutto il resto dipende dal
suo output, e inventarlo sarebbe l'errore che questo documento esiste per
evitare.

Nessun comando qui cancella righe sul database o oggetti nel bucket.

---

## 1. Trasferire il file

`censimento_42e32975ccd6.sql` **non è ancora su Render**: è stato scritto in
questo giro e non è stato trasferito. Va portato lì prima di eseguirlo.

Il modo più semplice, senza incollare file in una shell, è usare il
repository: il file è nell'albero di lavoro locale e sale con il commit e il
deploy che farai tu. Finché quel deploy non c'è, in alternativa si può
ricrearlo sulla shell di Render con un editor (`cat > file`, poi incolla), ma
**non** con un heredoc dentro una pipeline di comandi: il file contiene
`$guardia$` e `\copy`, e una shell che li interpreta lo corrompe in silenzio.

Verifica che sia arrivato integro **prima** di eseguirlo, confrontando
l'impronta con quella locale:

```
sha256sum censimento_42e32975ccd6.sql
```

---

## 2. Eseguire il censimento

Il wrapper crea una directory nuova per ogni esecuzione e ci entra: da lì i
nomi dei file sono **letterali** e il percorso esatto lo conosce chi li ha
creati — non lo si ricerca con `ls | head`.

```
set -euo pipefail
umask 077

ESECUZIONE="$PWD/censimento_42e32975ccd6_$(date -u +%Y%m%dT%H%M%SZ)"
REPO="$PWD"
mkdir "$ESECUZIONE"                     # fallisce se esiste: nessuna collisione
cp censimento_42e32975ccd6.sql "$ESECUZIONE"/
cd "$ESECUZIONE"

PGPASSWORD="$DB_PASSWORD" psql -X \
  --host="$DB_HOST" --port="${DB_PORT:-5432}" \
  --username="$DB_USER" --dbname="$DB_NAME" \
  --no-password --set=ON_ERROR_STOP=1 \
  --file=censimento_42e32975ccd6.sql \
  > censimento.out 2>&1

echo "psql uscito con 0; esecuzione in $ESECUZIONE"
```

`ESECUZIONE` è **assoluto** e `REPO` conserva la directory di partenza: dopo
il `cd` i due percorsi restano validi comunque, e nessun passo successivo deve
indovinare dove si trova. Un secondo `cd "$ESECUZIONE"` relativo — come c'era
prima — sarebbe fallito proprio perché il primo `cd` era già andato a buon
fine: si sarebbe cercata una sottodirectory dentro se stessa.

`mkdir` senza `-p` è parte del controllo: se la directory esiste, il comando
fallisce e `set -e` ferma tutto, invece di mescolare due esecuzioni.

`set -e` più `ON_ERROR_STOP=1` sono la coppia che serve: il primo ferma la
shell, il secondo ferma psql alla prima istruzione fallita — compresa la
guardia sul nome del database. Senza il primo, un psql fallito lascerebbe
proseguire i comandi successivi, che presenterebbero un manifest precedente
con l'aria di essere il risultato di oggi.

`-X` ignora `~/.psqlrc`: un `\set`, un `\timing` o un `AUTOCOMMIT` diverso nel
file personale dell'utente cambierebbero il comportamento senza che nulla lo
dica.

Il censimento produce due file, con nomi letterali, nella directory corrente:

| File | Contenuto |
|---|---|
| `manifest.csv.parziale` | id, property_id, chiave, impronta della chiave, stato |
| `controllo_manifest.tsv` | una riga, **due colonne** separate dal TAB delimitatore: conteggio e impronta d'insieme |

**Perché `COPY ... TO STDOUT` e non `\copy`.** psql documenta l'eccezione: per
`\copy` l'intero resto della riga è preso alla lettera, e «neither variable
interpolation nor backquote expansion are performed». Un `\copy ... TO
:'parziale'` avrebbe creato un file chiamato letteralmente `:'parziale'`. Il
percorso documentato è `COPY (...) TO STDOUT` — una lettura lato server, che
non rompe la transazione `READ ONLY` — con `\g` che ne dirotta l'uscita su un
file del client.

### Verificare e promuovere

Solo se il comando sopra è arrivato in fondo, e nella stessa shell — dove
`$ESECUZIONE` e `$REPO` sono ancora definiti:

```
set -euo pipefail
python3 "$REPO/scripts/p26_6_verifica_manifest.py" \
  --manifest   "$ESECUZIONE/manifest.csv.parziale" \
  --controllo  "$ESECUZIONE/controllo_manifest.tsv" \
  --definitivo "$ESECUZIONE/manifest.csv"
```

Il verificatore legge il CSV con un parser CSV — non con `wc -l`, che su un
CSV conta righe di testo e non record — e prima di promuovere controlla tre
cose:

- il **numero di righe** coincide con quello dichiarato dal database;
- l'**impronta d'insieme** coincide: ricompone `md5` delle impronte per id,
  esattamente come la query del censimento. Un manifest di un'altra
  esecuzione, anche con lo stesso numero di righe, non passa;
- **ogni riga** ha chiave e impronta coerenti. È il controllo che vede un file
  troncato dentro un record, dove la struttura resta plausibile e la chiave no.

Esce `0` solo se ha promosso. In ogni altro caso esce `2`, il motivo è la
prima riga di stderr, il parziale resta dov'è per essere esaminato e nessun
file preesistente viene toccato.

Il conteggio atteso arriva dal file di controllo, in formato macchina: **due
colonne** in formato `text`, separate dal TAB delimitatore — non una stringa
con un TAB concatenato dentro, che il formato non distinguerebbe da un tab
facente parte del dato. Con zero righe `string_agg` restituisce NULL, che in
formato testo esce come `\N`: il `coalesce(..., md5(''))` lo evita, ed è lo
stesso valore che il verificatore Python calcola sulla concatenazione vuota.

L'estrazione precedente spremeva il conteggio dal log con `tr -dc '0-9\n'`,
che toglie ogni carattere non numerico: finiva incollato alle cifre dell'md5
stampato accanto, e `2` con `3f9a1…` diventavano un numero solo.

`PGPASSWORD` sta in variabile d'ambiente e non compare in `ps` né nella
cronologia. Nessuna chiave di storage viene stampata: a video solo conteggi,
impronte e percorsi.

---

## 3. Il manifest: dove vive e perché viene prima

`property_documents.storage_key` è **l'unico posto** in cui quelle chiavi
esistono. Il recupero cancella quella riga: da quel momento l'oggetto nel
bucket non è più raggiungibile per nessuna via — nessun censimento SQL lo
vedrebbe, perché non è sul database.

Database e bucket **non condividono una transazione**. L'ordine è obbligato, e
ogni passo deve poter essere ripreso:

1. **manifest** — questo censimento; file `600` in una directory per
   esecuzione, promosso a definitivo solo dopo la verifica con parser CSV;
2. **copia fuori dall'istanza** — vedi sotto, ed è un prerequisito del punto 4,
   non un suggerimento;
3. **prova con ROLLBACK** — esegue davvero le DELETE e verifica il ripristino
   completo; **nessun oggetto rimosso dal bucket**;
4. **esecuzione** — DELETE + `COMMIT`, vincolata al perimetro verificato;
5. **pulizia del bucket** — legge il manifest, rimuove un oggetto alla volta e
   annota l'esito nella colonna `stato`.

### La copia deve sopravvivere alla perdita dell'istanza

Il filesystem di un'istanza Render è **effimero**: un riavvio, un nuovo deploy
o la fine della sessione di shell portano via `manifest_*.csv`. Se questo
accade fra il punto 4 e il punto 5, gli oggetti restano nel bucket e non c'è
più nessun modo di sapere quali fossero — le righe che li nominavano sono state
cancellate al punto 4.

Quindi **prima del COMMIT distruttivo** il manifest va copiato fuori
dall'istanza, e la copia va verificata:

```
set -euo pipefail
cat "$ESECUZIONE/manifest.csv.sha256"
```

Percorso assoluto, non un `cd`: siamo già dentro quella directory dal passo 2,
e un `cd "$ESECUZIONE"` relativo cercherebbe una sottodirectory omonima.

Poi si scarica quel file su una postazione che non sia l'istanza — scaricandolo
dalla shell del browser, oppure copiandolo in un bucket o in un archivio
sicuro — e si confronta lo `sha256` della copia con quello stampato qui. Il
punto 4 non va eseguito finché i due digest non coincidono.

**Il manifest non va nel repository.** Contiene chiavi di storage: non è
codice, non è documentazione, e un file committato è un file pubblicato per
sempre nella storia di git. `.gitignore` esclude le directory di esecuzione
`censimento_42e32975ccd6_*/` e i file `manifest.csv*` e
`controllo_manifest.tsv`.

### Riprendere dopo un'interruzione

- interruzione **fra 4 e 5**: il manifest (locale o la copia esterna) elenca
  tutte le chiavi con `stato = da_rimuovere`; la pulizia parte da lì;
- interruzione **dentro 5**: si riprende dalle righe ancora marcate
  `da_rimuovere`, perché la pulizia aggiorna quella colonna man mano;
- il manifest si cancella **solo** quando ogni riga è marcata rimossa e il
  bucket è stato verificato.

---

## 4. Cosa manca ancora, e perché

Il recupero eseguibile (punti 3 e 4 della sequenza qui sopra) **non è
scritto**: richiede gli id derivati che solo il censimento produce. Scriverlo adesso significherebbe
inventarli, e ogni run precedente ha mostrato quanto costa.

Con l'output del censimento diventa meccanico: il perimetro è quello, le
DELETE seguono l'ordine delle FK già stabilito dal cleanup della matrice, la
prova generale finisce in `ROLLBACK` verificando che tutte le righe siano
ancora presenti e identiche.

Il censimento raccoglie anche le due configurazioni che servono alle fixture
ancora aperte:

- **punto 11** — quali regole `flow_rules` sono attive su TEST. Un'esecuzione
  FLOW nasce solo se una regola attiva corrisponde all'evento: senza questo
  elenco una prova deterministica sulle esecuzioni non si può scrivere.
- **punto 11b** — i lead aperti e già scaduti nelle agenzie dedicate. Il
  segnale NEXT_BEST_ACTION più semplice e deterministico è
  `next_action_overdue`, che esige un lead aperto con `next_action_at` nel
  passato; serve sapere che non ce ne sono di preesistenti, altrimenti il
  confronto fra le due agenzie partirebbe sporco.

---

## 5. I sei audit

`560, 562, 563, 564, 566, 567`. Origine **non dimostrata**, e il punto 10c del
censimento aggiunge l'unico indizio che si possa raccogliere senza ipotesi:
se l'entità nominata da `entity_id` esiste ancora. Nessuna delle due risposte
chiude la questione da sola — `owner_account_id` e `property_id` sono entrambi
`ON DELETE SET NULL`, quindi "nato NULL" e "svuotato dopo" producono la stessa
riga. L'attribuzione resta aperta e il FAIL resta.
