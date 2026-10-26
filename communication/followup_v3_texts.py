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
  * NON scrivere firma ne' link di disiscrizione: li aggiunge il sistema in
    fondo a ogni email (agenzia, «su Stima360», link di disiscrizione).
"""
from __future__ import annotations

import re

#: (giorno, oggetto) - il calendario approvato. Il giorno e' contato dal
#: momento iniziale della sequenza (data del consenso recente o ingresso).
CALENDARIO: tuple[tuple[int, str], ...] = (
    (4, "Quanto ti costa davvero vendere casa?"),
    (8, "Ti regalo le 7 fotografie che possono fare la differenza 📸"),
    (12, "Il tuo prezzo di vendita supera questo test?"),
    (18, "Chi comprerebbe la tua casa? Facciamo un gioco"),
    (25, "I documenti che conviene cercare prima di vendere"),
    (33, "🎁 Un regalo per te: la guida completa per vendere casa"),
    (42, "La tua casa ha un superpotere. Qual è?"),
    (52, "Prima di spendere soldi per ristrutturare, guarda questo"),
    (63, "Accetteresti 200.000 € oppure 195.000 €?"),
    (75, "Il tuo annuncio non riceve chiamate? Prova questa diagnosi"),
    (88, "La tua planimetria racconta tutta la verità?"),
    (102, "Un trucco per confrontare i prezzi delle case vicine"),
    (117, "Hai 15 minuti? Prepara la casa per una visita"),
    (135, "Prima di dire SÌ a un acquirente, chiedi queste 5 cose"),
    (157, "Ti regalo una mini-diagnosi della tua situazione immobiliare"),
    (180, "Il tuo piano per vendere casa, in 30 giorni"),
)

#: L'email con la guida PDF, e quella a cui si risponde A/B/C/D.
EMAIL_GUIDA = 6
EMAIL_RISPOSTE = 15

SEGNAPOSTO_NOME = "{{nome}}"
SEGNAPOSTO_GUIDA = "{{link_guida}}"
_SEGNAPOSTO = re.compile(r"\{\{[^{}]*\}\}")

#: I corpi approvati dal titolare (consegnati il 2026-10-26, «Edizione 1»),
#: per numero di email. Parole, emoji ed elenchi come consegnati; le righe
#: vuote separano i paragrafi. Adattamenti approvati dal titolare: i link
#: «[www.stima360.it](https://www.stima360.it)» sono l'indirizzo
#: https://www.stima360.it (cliccabile nel testo semplice); nessuna firma
#: «Il team STIMA360» (firma e disiscrizione le aggiunge il CRM); l'email 6
#: offre la guida con il link di download, non come allegato.
TESTI: dict[int, str] = {
    1: """Ciao!

Facciamo un piccolo esperimento.

Se vendessi casa a 200.000 € e dovessi pagare una provvigione del 3% più IVA, spenderesti 7.320 €.

🎁 Il tuo regalo: una formula per fare i conti.

Prezzo di vendita × percentuale di provvigione × 1,22 (se soggetta a IVA ordinaria).

Prova a inserire il prezzo che vorresti ottenere per la tua casa.

Con STIMA360 il venditore non paga provvigioni di mediazione.

Scopri il nostro modello su https://www.stima360.it.""",
    2: """Ciao!

Vuoi sapere se la tua casa è pronta per essere presentata online?

🎁 Ecco la nostra checklist fotografica gratuita:

1. Una foto luminosa del soggiorno.
2. La cucina vista dall'angolazione migliore.
3. La camera principale.
4. Il bagno, ordinato e ben illuminato.
5. Balconi, terrazzi o giardini.
6. La vista e gli eventuali punti panoramici.
7. Il dettaglio che rende speciale la tua casa.

Piccolo trucco: per ogni stanza prova a fotografare sia dalla porta sia dall'angolo opposto.

Non alterare dimensioni o caratteristiche nelle fotografie.

Puoi usare questa lista già oggi, anche se non hai ancora deciso di vendere.""",
    3: """Ciao!

A volte il prezzo che desideriamo ottenere non coincide con quello che il mercato è disposto a riconoscere.

🎁 Ti regalo il test delle 3 domande:

* Hai confrontato almeno tre immobili davvero simili al tuo?
* Hai distinto i prezzi richiesti negli annunci da quelli delle vendite concluse?
* Sai spiegare perché la tua casa dovrebbe valere più o meno delle altre?

Se hai risposto NO anche a una sola domanda, forse vale la pena approfondire la valutazione.

Non significa che il tuo prezzo sia sbagliato. Significa che merita una verifica.

Puoi richiedere una valutazione gratuita su https://www.stima360.it.""",
    4: """Ciao!

Immagina di essere tu l'acquirente.

🎁 Ti regalo un piccolo esercizio di marketing immobiliare.

Completa queste tre frasi:

* La mia casa sarebbe perfetta per ______.
* La caratteristica che potrebbe interessare di più è ______.
* Il motivo per cui qualcuno dovrebbe sceglierla è ______.

Una coppia giovane potrebbe avere esigenze diverse da una famiglia con bambini o da chi cerca una seconda casa.

Non basta pubblicare un immobile: bisogna capire a chi raccontarlo.

Se vuoi, rispondi a questa email con le tre frasi completate.""",
    5: """Ciao!

Oggi ti regalo qualcosa di molto pratico.

🎁 La checklist salva-tempo del venditore:

☐ Atto di provenienza dell'immobile
☐ Visura catastale
☐ Planimetria catastale
☐ Titoli edilizi e pratiche comunali
☐ Attestato di Prestazione Energetica (APE)
☐ Informazioni su eventuale mutuo o ipoteca
☐ Situazione delle spese condominiali

Non tutti i documenti hanno lo stesso ruolo e alcuni controlli richiedono un tecnico o un notaio.

Ma raccoglierli in anticipo può evitare tanti ritardi.

Conserva questa email: potrebbe tornarti utile quando deciderai di vendere.""",
    6: """Ciao!

Questa volta non ti mandiamo soltanto un consiglio.

Abbiamo preparato una guida completa per chi vuole vendere un immobile evitando errori e spese inutili.

🎁 Puoi scaricarla gratuitamente qui: {{link_guida}}

VENDERE UN IMMOBILE SENZA ERRORI — EDIZIONE 2026

All'interno trovi 28 capitoli e 2 checklist, con argomenti come:

* Come scegliere il prezzo.
* Quali documenti servono.
* Come preparare l'immobile.
* Come gestire proposte e caparre.
* Quali aspetti fiscali controllare.
* Come evitare spese inutili.

È una guida da conservare e consultare quando ne hai bisogno.

Non devi acquistare nulla.

Buona lettura!""",
    7: """Ciao!

Oggi ti regalo una formula che usiamo per ragionare sulla presentazione degli immobili.

🎁 La formula del punto di forza:

Caratteristica + vantaggio concreto + persona interessata.

Esempio:

«Terrazzo di 40 m²» è una caratteristica.

«Uno spazio esterno dove pranzare con gli amici» è un vantaggio.

«Perfetto per chi ama vivere all'aperto» identifica il possibile pubblico.

Adesso prova con la tua casa.

Se vuoi, rispondimi con una caratteristica del tuo immobile.""",
    8: """Ciao!

Stai pensando di fare lavori prima di vendere casa?

🎁 Ti regalo il semaforo delle ristrutturazioni.

🟢 Verde: pulizia, riordino, piccole riparazioni. Spesso sono un buon punto di partenza.

🟡 Giallo: tinteggiatura, sostituzioni e interventi estetici. Prima confronta costi e benefici.

🔴 Rosso: grandi lavori fatti soltanto nella speranza di aumentare il prezzo. Senza una valutazione economica rischi di non recuperare la spesa.

Attenzione: eventuali problemi di sicurezza o regolarità vanno valutati separatamente con professionisti abilitati.

Il trucco non è spendere di più. È spendere dove serve.""",
    9: """Ciao!

Ti propongo un piccolo quiz.

Un acquirente offre 200.000 €, ma deve ancora ottenere il mutuo.

Un altro offre 195.000 €, con disponibilità economica già documentata e tempi di acquisto compatibili.

Quale sceglieresti?

🎁 Ti regalo una scheda di confronto:

Per ogni proposta assegna un giudizio a:

* Prezzo offerto.
* Condizioni di finanziamento.
* Caparra prevista.
* Tempi per il rogito.
* Condizioni particolari.
* Compatibilità con le tue necessità.

La proposta migliore non è sempre quella con il numero più alto.

Le condizioni vanno sempre esaminate con attenzione prima di firmare.""",
    10: """Ciao!

Una casa può essere interessante e avere comunque un annuncio poco efficace.

🎁 Ecco la diagnosi STIMA360 in 60 secondi.

Controlla questi cinque elementi:

1. La prima fotografia colpisce?
2. Il prezzo è motivato dal mercato?
3. Sono indicate le caratteristiche decisive?
4. Le fotografie mostrano gli ambienti in modo fedele?
5. La descrizione risponde alle domande più comuni?

Se diversi elementi sono deboli, migliorare l'annuncio potrebbe essere più utile di un ribasso immediato.

Hai già pubblicato casa tua? Puoi rispondere a questa email raccontandoci come sta andando.""",
    11: """Ciao!

Oggi ti regalo un controllo preliminare che molti proprietari trascurano.

🎁 Il test delle quattro verifiche:

* La disposizione delle stanze corrisponde alla planimetria catastale?
* Hai fatto modifiche interne nel corso degli anni?
* Possiedi la documentazione edilizia delle modifiche?
* Sai distinguere conformità catastale e regolarità urbanistica?

Se hai dubbi, non significa automaticamente che ci sia un abuso.

Ma è il momento giusto per far verificare la documentazione a un tecnico.

Meglio scoprirlo prima di una proposta d'acquisto.""",
    12: """Ciao!

Due immobili nello stesso palazzo possono avere valori diversi.

🎁 Ti regalo il metodo delle cinque differenze.

Quando confronti casa tua con un altro immobile, annota:

1. Metratura e criterio di superficie.
2. Piano e presenza di ascensore.
3. Stato di conservazione.
4. Balconi, garage e altre pertinenze.
5. Esposizione, luminosità e posizione.

Ricorda che il prezzo di un annuncio non dimostra il prezzo effettivo di vendita.

Questo esercizio non sostituisce una valutazione professionale, ma aiuta a capire perché due case apparentemente simili possano valere cifre diverse.

Per una valutazione gratuita: https://www.stima360.it.""",
    13: """Ciao!

Una buona impressione comincia prima ancora di parlare del prezzo.

🎁 Ti regalo la sfida dei 15 minuti.

* Minuti 1–3: apri tende e persiane.
* Minuti 4–6: libera tavoli e superfici.
* Minuti 7–9: sistema cucina e bagno.
* Minuti 10–12: arieggia gli ambienti e controlla gli odori.
* Minuti 13–15: prepara le informazioni principali e verifica che l'accesso sia agevole.

Naturalmente eventuali difetti importanti non devono essere nascosti.

Sono piccoli gesti che aiutano a presentare meglio l'immobile.

Salva questa email per la prossima visita.""",
    14: """Ciao!

Quando arriva un'offerta interessante, è facile concentrarsi soltanto sul prezzo.

🎁 Ti regalo cinque domande da controllare prima di accettare:

1. Come verrà pagato il prezzo?
2. L'acquisto dipende dall'ottenimento di un mutuo?
3. Quali somme sono previste alla firma e con quale qualificazione?
4. Quando avverrà il rogito?
5. Ci sono condizioni sospensive o altri impegni particolari?

Una proposta accettata può creare obblighi importanti.

La fretta non deve sostituire la verifica.""",
    15: """Ciao!

Magari stai ancora valutando se vendere. Magari hai già messo casa sul mercato.

🎁 Ecco il tuo percorso personalizzato: scegli una risposta.

A — Non ho ancora deciso.
Il primo passo è capire quanto potresti ricavare e quanto tempo hai a disposizione.

B — Ho pubblicato ma non ricevo richieste.
Il primo controllo riguarda prezzo, fotografie, descrizione e visibilità.

C — Ricevo visite ma nessuna proposta.
Conviene analizzare i commenti ricorrenti degli acquirenti.

D — Ho già venduto.
Complimenti! Non ha senso continuare a proporti consigli per vendere lo stesso immobile.

Puoi rispondere semplicemente con A, B, C oppure D.""",
    16: """Ciao!

Per chiudere questo percorso, vogliamo lasciarti un ultimo strumento.

🎁 Il piano STIMA360 dei primi 30 giorni:

Settimana 1: chiarisci obiettivo, tempi, costi e documenti disponibili.
Settimana 2: verifica la situazione tecnica con i professionisti e definisci il prezzo sulla base del mercato.
Settimana 3: prepara la casa, le fotografie e l'annuncio.
Settimana 4: valuta le richieste ricevute e correggi la strategia, se necessario.

Non è una promessa di vendita in 30 giorni: è un modo per organizzare bene la partenza.

Se vuoi un supporto professionale, STIMA360 è a disposizione.

Puoi richiedere una valutazione gratuita su https://www.stima360.it.

Un saluto,""",
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
