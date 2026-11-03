"""V3 - i testi della sequenza «Follow-up proprietari 180 giorni».

UN SOLO POSTO PER I TESTI APPROVATI DAL TITOLARE. Il calendario e gli oggetti
qui sotto sono quelli approvati (16 email, giorno 4 -> giorno 180). I CORPI
si inseriscono in `TESTI`, uno per numero di email, cosi' come il titolare li
ha approvati: nessun testo viene scritto o completato dal codice.

Finche' un corpo manca, il suo template non esiste nel registro
(`communication.templates`) e la sequenza NON si puo' nemmeno predisporre in
bozza: e' la regola di sempre, un messaggio non nasce da un testo che nessuno
ha approvato.

FORMATO DI OGNI CORPO
  * testo semplice, a capo con "\\n";
  * `{{nome}}` diventa il nome del contatto;
  * `{{link_guida}}` (SOLO nella email 6) diventa il link di download della
    guida PDF ufficiale, servito dal CRM soltanto se il file e' presente e
    integro (`communication.guida`); senza, l'email 6 non parte;
  * la firma «Il team STIMA360» fa parte del testo approvato; il link di
    disiscrizione NON si scrive: lo aggiunge il sistema in fondo a ogni
    email (agenzia, «su Stima360», link di disiscrizione).
"""
from __future__ import annotations

import re

#: (giorno, oggetto) - il calendario approvato. Il giorno e' contato dal
#: momento iniziale della sequenza (data del consenso recente o ingresso).
CALENDARIO: tuple[tuple[int, str], ...] = (
    (4, 'Vendi a 200.000 €: quanto ti rimane davvero?'),
    (8, 'Le 7 foto da non dimenticare nel tuo annuncio'),
    (12, 'Hai scelto il prezzo guardando le altre case in vendita?'),
    (18, 'Quale acquirente potrebbe scegliere casa tua?'),
    (25, 'Trovi l’acquirente. Ma i documenti sono pronti?'),
    (33, 'La tua guida per preparare la vendita di casa'),
    (42, '“Terrazzo di 40 m²”: cosa significa per chi compra?'),
    (52, 'Fare lavori prima di vendere: ne vale la pena?'),
    (63, 'Meglio un’offerta da 200.000 € o da 195.000 €?'),
    (75, 'Il tuo annuncio è online, ma non arrivano chiamate?'),
    (88, 'Hai spostato una parete? Controlla questo prima di vendere'),
    (102, 'Stessa via, stessa metratura. Perché due prezzi diversi?'),
    (117, 'Cinque cose da fare prima di una visita'),
    (135, 'Prima di firmare una proposta, verifica questi 5 punti'),
    (157, 'A che punto sei con la vendita di casa?'),
    (180, 'I primi 30 giorni per mettere in vendita casa'),
)

#: L'email con la guida PDF, e quella a cui si risponde A/B/C/D.
EMAIL_GUIDA = 6
EMAIL_RISPOSTE = 15

SEGNAPOSTO_NOME = "{{nome}}"
SEGNAPOSTO_GUIDA = "{{link_guida}}"
_SEGNAPOSTO = re.compile(r"\{\{[^{}]*\}\}")

#: I corpi approvati dal titolare, per numero di email: i TESTI DEFINITIVI
#: consegnati il 2026-11-03 (copia fedele in
#: `tests/data/followup_v3_testi_definitivi.txt`, confrontata dai test), con
#: parole, punteggiatura, apostrofi, emoji, elenchi e firma «Il team STIMA360»
#: come consegnati; le righe vuote separano i paragrafi.
#: ADATTAMENTO APPROVATO DAL TITOLARE - EMAIL 6 (2026-11-03): il canale email
#: spedisce la guida con il link di download (`guida_pdf`), non come allegato;
#: «che trovi in allegato» diventa «che puoi scaricare qui: {{link_guida}}» e
#: «Scarica l’allegato» diventa «Scarica la guida gratuita». Nient'altro cambia.
TESTI: dict[int, str] = {
    1: """Ciao!

Quando pensi al prezzo di vendita della tua casa, probabilmente hai in mente la cifra che vorresti ottenere.

Ma prezzo di vendita e somma che ti rimane non sono sempre la stessa cosa.

Un esempio: su una vendita da 200.000 €, una provvigione del 3% più IVA al 22% equivale a 7.320 €.

È una voce da considerare quando fai i tuoi conti.

Con STIMA360 il venditore non paga provvigioni di mediazione. Un vantaggio concreto da conoscere prima di scegliere a chi affidare la vendita.

Vuoi capire come funziona?

Scopri il modello STIMA360 su www.stima360.it.

Il team STIMA360""",
    2: """Ciao!

Chi cerca casa online vede le fotografie prima ancora di leggere la descrizione.

Le tue mostrano ciò che vorrebbe conoscere?

Ecco sette scatti da controllare:

1. Soggiorno: una vista ampia, con luce naturale.
2. Cucina: piano di lavoro libero e ambiente ordinato.
3. Camera principale: un’inquadratura che faccia capire gli spazi.
4. Bagno: luminoso, con pochi oggetti in vista.
5. Balcone, terrazzo o giardino, se presenti.
6. Vista dall’immobile, se rappresenta un punto di forza.
7. Un dettaglio utile che distingue la casa dalle altre.

Prima di scattare, pulisci la lente, accendi le luci dove serve e tieni il telefono dritto. Evita filtri e inquadrature che deformano le dimensioni.

Apri il tuo annuncio e confrontalo con questa lista: manca qualche immagine importante?

Il team STIMA360""",
    3: """Ciao!

Hai cercato case simili nella tua zona e hai usato quei prezzi per decidere quanto chiedere?

È un punto di partenza. Ma manca una domanda: quelle case stanno trovando acquirenti a quelle cifre?

Un annuncio mostra quanto chiede il venditore. Da solo non dimostra quanto un compratore sia disposto a pagare.

Prima di fissare il prezzo, controlla tre cose:

- Gli immobili confrontati sono davvero simili per zona, superficie, piano e condizioni?
- Sai da quanto tempo sono in vendita?
- Quali caratteristiche giustificano un prezzo più alto o più basso per casa tua?

Non serve indovinare la cifra perfetta: serve partire da elementi concreti.

Vuoi verificare il prezzo della tua casa? Richiedi una valutazione gratuita su www.stima360.it.

Il team STIMA360""",
    4: """Ciao!

Una stanza in più può essere una cameretta per una famiglia oppure uno studio per chi lavora da casa.

La caratteristica è la stessa. Il motivo per comprarla cambia.

Per presentare bene il tuo immobile, prova a completare queste tre frasi:

- La mia casa potrebbe essere adatta a…
- A questa persona sarebbe utile soprattutto…
- Rispetto alle alternative, potrebbe sceglierla perché…

Per esempio: “Potrebbe interessare a chi lavora da casa, perché la terza stanza permette di avere uno studio separato dal soggiorno”.

Questo esercizio aiuta a scegliere cosa mostrare nelle foto e cosa mettere in evidenza nell’annuncio.

Rispondi con le tre frasi completate: ci aiuteranno a capire come immagini il futuro acquirente della tua casa.

Il team STIMA360""",
    5: """Ciao!

Un acquirente si interessa alla casa e vuole approfondire. È proprio in quel momento che cercare documenti mancanti può rallentare tutto.

Conviene iniziare prima.

Tra i documenti da recuperare e far verificare ci sono:

☐ Atto di provenienza, per esempio l’atto di acquisto.
☐ Visura e planimetria catastale.
☐ Titoli edilizi e documentazione degli eventuali lavori.
☐ Attestato di Prestazione Energetica, APE.

Raccogli anche le informazioni su eventuali mutui, ipoteche e spese condominiali.

L’elenco è un punto di partenza: i documenti e i controlli necessari dipendono dalla situazione dell’immobile. Per le verifiche tecniche e giuridiche servono i professionisti competenti.

Da dove cominciare oggi? Cerca l’atto di acquisto e la planimetria, poi raccoglili nella stessa cartella.

Il team STIMA360""",
    6: """Ciao!

Come scegliere il prezzo? Quali documenti recuperare? Cosa controllare quando arriva una proposta?

Abbiamo raccolto questi argomenti nella guida gratuita STIMA360 che puoi scaricare qui: {{link_guida}}.

Puoi usarla in base al punto in cui ti trovi:

- Se stai pensando di vendere, comincia dalle parti dedicate al prezzo e ai costi.
- Se vuoi mettere casa sul mercato, passa a documenti e preparazione dell’immobile.
- Se hai già un acquirente interessato, approfondisci proposte, caparre e passaggi successivi.

Non occorre leggerla tutta insieme. Tienila a portata di mano e consulta ciò che ti serve.

Scarica la guida gratuita e parti dall’argomento più utile per te oggi.

Buona lettura!

Il team STIMA360""",
    7: """Ciao!

“Appartamento con terrazzo di 40 m².”

L’informazione è utile, ma chi legge deve ancora immaginare come potrebbe usarlo.

Prova così:

“Un terrazzo di 40 m² dove organizzare una cena all’aperto e ricavare un angolo per rilassarsi.”

La misura resta. In più, aiuti il lettore a immaginare una situazione concreta.

Puoi applicare lo stesso metodo agli altri punti di forza:

- Una stanza aggiuntiva: spazio per uno studio separato.
- Un garage: comodità per parcheggiare e riporre oggetti.
- Una cucina separata: un ambiente dedicato alla preparazione dei pasti.

Descrivi possibilità reali, coerenti con gli spazi della casa.

Scegli una caratteristica del tuo immobile e prova a spiegare quale vantaggio offre a chi lo abiterà.

Il team STIMA360""",
    8: """Ciao!

Prima di rifare la cucina o cambiare i pavimenti per vendere meglio, fermati su una domanda:

L’acquirente riconoscerà nel prezzo ciò che stai per spendere?

Una cucina scelta secondo i tuoi gusti potrebbe non piacere a chi comprerà.

Per orientarti, usa questo semaforo:

🟢 Pulizia, riordino e piccole riparazioni: aiutano a presentare una casa curata.

🟡 Tinteggiatura e interventi estetici: valuta lo stato attuale, il costo e l’effetto sulla presentazione.

🔴 Ristrutturazioni importanti fatte solo per aumentare il prezzo: prima serve un’analisi economica, perché recuperare tutta la spesa non è garantito.

I problemi di sicurezza e regolarità richiedono una valutazione separata da parte dei professionisti competenti.

Stai pensando a un intervento prima della vendita? Rispondi indicando quale e con quale obiettivo.

Il team STIMA360""",
    9: """Ciao!

Arrivano due offerte per casa tua.

La prima è da 200.000 €, ma l’acquirente deve ancora ottenere il mutuo.

La seconda è da 195.000 €, con disponibilità economica documentata e tempi compatibili con le tue esigenze.

Quale conviene?

Con queste sole informazioni non possiamo ancora deciderlo. Ma possiamo capire perché il prezzo, da solo, non basta.

Per confrontare le offerte, metti una accanto all’altra queste voci:

- Prezzo.
- Condizioni legate al finanziamento.
- Somme previste alla firma.
- Tempi del rogito e della consegna.
- Altre condizioni.
- Compatibilità con il tuo prossimo acquisto o trasferimento.

Anche un’offerta più bassa merita di essere esaminata. E un’offerta più alta merita di essere verificata con la stessa attenzione.

Quando confronti due proposte, confronta l’intera operazione.

Il team STIMA360""",
    10: """Ciao!

Apri il portale, controlli l’annuncio e aspetti. Ma le richieste non arrivano.

Prima di cambiare il prezzo, prova a capire dove si interrompe il percorso.

Poche visualizzazioni?
Controlla visibilità, fotografia principale e informazioni mostrate nell’anteprima.

Visualizzazioni ma pochi contatti?
Rivedi prezzo, fotografie, descrizione e informazioni mancanti: cosa potrebbe far esitare chi legge?

Contatti ma poche visite?
Verifica quali domande fanno le persone e in quale momento perdono interesse.

Sono indizi da leggere insieme: nessuno, da solo, identifica con certezza il problema.

Se hai già pubblicato casa tua, rispondi indicando da quanto tempo è online, quanti contatti hai ricevuto e quante visite hai fatto.

Sono tre dati utili per iniziare a ragionare sulla situazione.

Il team STIMA360""",
    11: """Ciao!

Negli anni hai unito due stanze, spostato una porta oppure modificato la cucina?

Prima di vendere, conviene ricostruire quali lavori sono stati fatti e quali documenti li accompagnano.

Comincia da quattro domande:

- La disposizione attuale corrisponde alla planimetria catastale?
- Quali modifiche sono state eseguite?
- Hai conservato le pratiche relative ai lavori?
- Un tecnico ha già verificato la situazione?

La corrispondenza con la planimetria catastale, da sola, non esaurisce i controlli sulla regolarità edilizia e urbanistica.

Se manca un documento o qualcosa non coincide, evita conclusioni affrettate: fai verificare il caso a un tecnico.

Recuperare queste informazioni prima di una trattativa ti permette di affrontarla con maggiore chiarezza.

Il team STIMA360""",
    12: """Ciao!

Immagina due appartamenti di circa 90 m² nella stessa via.

Il primo è ristrutturato, ha l’ascensore e un garage.

Il secondo necessita di lavori, è a un piano alto senza ascensore e non ha parcheggio.

La metratura è simile. Il confronto, però, deve considerare molto altro.

Quando guardi un annuncio vicino a casa tua, annota:

1. Quale superficie viene indicata e cosa comprende.
2. Piano e presenza dell’ascensore.
3. Condizioni dell’immobile.
4. Spazi esterni, garage e altre pertinenze.
5. Esposizione, luminosità e posizione precisa.

Poi chiediti: quali differenze potrebbero contare per un acquirente?

Ricorda anche che stai confrontando prezzi richiesti, che possono essere diversi da quelli effettivamente concordati.

Vuoi capire come incidono le caratteristiche della tua casa? Richiedi una valutazione gratuita su www.stima360.it.

Il team STIMA360""",
    13: """Ciao!

Tra poco arriva una persona a vedere casa. Cosa conviene controllare?

Ecco cinque cose semplici:

1. Luce: apri tende e persiane e illumina gli ambienti dove serve.
2. Ordine: libera tavoli e piani di lavoro per rendere leggibili gli spazi.
3. Cucina e bagno: controlla pulizia e oggetti lasciati in vista.
4. Aria: arieggia ed evita profumi troppo intensi.
5. Informazioni: prepara i dati su spese, lavori eseguiti, pertinenze e disponibilità della casa.

Se ci sono problemi conosciuti, preparati a descriverli con chiarezza.

L’obiettivo è permettere a chi visita di capire gli ambienti e ottenere risposte utili.

Conserva questa lista e rileggila prima del prossimo appuntamento.

Il team STIMA360""",
    14: """Ciao!

Il prezzo ti convince e vorresti accettare.

Prima di firmare, assicurati di aver compreso anche gli altri impegni contenuti nella proposta.

Verifica questi cinque punti:

1. Come e quando verrà pagato il prezzo.
2. Se l’acquisto dipende dall’ottenimento di un mutuo o da altre condizioni.
3. Quali somme vengono versate, quando e come sono definite nel documento.
4. Quali date sono previste per rogito e consegna.
5. Quali altri obblighi assumi, compresi eventuali lavori o arredi inclusi.

Se una frase è poco chiara, chiedi che venga spiegata prima di decidere.

L’accettazione di una proposta può comportare impegni vincolanti: fai esaminare il documento dal professionista che ti assiste.

Una cifra interessante merita una lettura attenta di tutte le condizioni.

Il team STIMA360""",
    15: """Ciao!

Da quando abbiamo iniziato a scriverti, la tua situazione potrebbe essere cambiata.

Quale di queste descrizioni ti rappresenta oggi?

A — Sto ancora decidendo se vendere.
Vuoi capire quanto potresti ricavare e se il momento è adatto alle tue esigenze.

B — La casa è in vendita, ma arrivano poche richieste.
Ti serve capire cosa controllare tra prezzo, presentazione e visibilità.

C — Ricevo visite, ma nessuna proposta.
Vuoi capire cosa frena le persone dopo l’appuntamento.

D — Ho già venduto oppure non intendo più vendere.
Il tuo progetto si è concluso o è cambiato.

Rispondi semplicemente con A, B, C oppure D.

Ci aiuterai a capire la tua situazione attuale.

Il team STIMA360""",
    16: """Ciao!

Vuoi vendere, ma hai tante cose da fare e non sai da dove cominciare?

Ecco una traccia per organizzare il primo mese.

Settimana 1 — Metti a fuoco il tuo obiettivo.
Chiarisci tempi, somma che vorresti ricavare e necessità legate a un eventuale trasferimento. Inizia a raccogliere i documenti.

Settimana 2 — Avvia le verifiche.
Fai esaminare la situazione tecnica ai professionisti competenti e confronta il tuo obiettivo di prezzo con gli elementi di mercato.

Settimana 3 — Prepara la presentazione.
Dopo le verifiche necessarie, organizza casa, fotografie e informazioni per l’annuncio.

Settimana 4 — Leggi i primi riscontri.
Se l’immobile è già online, osserva richieste, visite e domande degli interessati per capire se correggere qualcosa.

I tempi sono indicativi: alcune verifiche possono richiedere più di una settimana. Questa traccia serve a organizzare la partenza, senza promettere una vendita in 30 giorni.

Vuoi capire da dove iniziare nel tuo caso?

Richiedi una valutazione gratuita su www.stima360.it.

Il team STIMA360""",
}


def problemi(numero: int, testo: str | None) -> list[str]:
    """Perche' questo corpo non si puo' usare, o [] se va bene."""
    if not (testo or "").strip():
        return ["testo mancante"]
    errori = []
    sconosciuti = sorted(set(_SEGNAPOSTO.findall(testo)) - {SEGNAPOSTO_NOME, SEGNAPOSTO_GUIDA})
    if sconosciuti:
        errori.append(f"segnaposto sconosciuti {sconosciuti}")
    if numero == EMAIL_GUIDA and SEGNAPOSTO_GUIDA not in testo:
        errori.append(f"l'email {EMAIL_GUIDA} deve contenere {SEGNAPOSTO_GUIDA}")
    if numero == EMAIL_GUIDA and "allegat" in testo.lower():
        errori.append(f"l'email {EMAIL_GUIDA} non puo' promettere un allegato: il canale email spedisce il link")
    if numero != EMAIL_GUIDA and SEGNAPOSTO_GUIDA in testo:
        errori.append(f"{SEGNAPOSTO_GUIDA} e' ammesso solo nell'email {EMAIL_GUIDA}")
    return errori


def componi(testo: str, *, nome: str, link_guida: str | None = None) -> str:
    """Il corpo con i segnaposto sostituiti (la chiusura la aggiunge il registro)."""
    corpo = testo.replace(SEGNAPOSTO_NOME, nome)
    if link_guida is not None:
        corpo = corpo.replace(SEGNAPOSTO_GUIDA, link_guida)
    return corpo


__all__ = ["CALENDARIO", "EMAIL_GUIDA", "EMAIL_RISPOSTE", "TESTI", "problemi", "componi"]
