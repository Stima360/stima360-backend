"""LMC-12 - "La Mia Casa": quando un fatto sulla casa merita una notifica.

COSA DECIDE QUESTO MODULO, E COSA NO.

Qui si decide SOLTANTO se una serie di osservazioni gia' scritte contiene un
cambiamento che vale la pena raccontare al proprietario, e con quali parole.
Non si calcola nessun valore: gli snapshot del valore li scrive LMC-3 (e
LMC-11 ogni giorno), le rilevazioni della domanda le scrive Property Watch.
LMC-12 li CONFRONTA. Per questo il modulo non importa il motore di stima, ne'
`home_profile`, ne' apre una connessione: riceve liste e restituisce
decisioni, ed e' interamente verificabile senza database.

LA REGOLA FONDAMENTALE: UNO SNAPSHOT NUOVO NON E' UNA NOTIFICA.

LMC-11 scrive uno snapshot al giorno per ogni casa, anche quando il valore
non cambia. Se ogni snapshot fosse una notifica, il proprietario ne
riceverebbe una al giorno con lo stesso numero dentro. Qui una notifica del
valore nasce solo quando, rispetto all'ultimo stato ASSORBITO, il valore e'
cambiato di almeno `VALUE_CHANGE_MIN_PCT` per cento E di almeno
`VALUE_CHANGE_MIN_EUR` euro - entrambe le soglie, non una delle due.

IL RIFERIMENTO, CIOE' L'ULTIMO STATO ASSORBITO.

Il confronto non e' "con ieri": confrontare sempre con il giorno prima
perderebbe la variazione cumulativa (100k -> 102k -> 104k -> 106k non
supera mai la soglia un giorno alla volta, ma dal primo all'ultimo e' il 6%).
Il confronto e' con il RIFERIMENTO, che rappresenta l'ultimo stato che il
proprietario ha gia' visto o provocato, e che avanza in tre casi soltanto:

  1. il primo snapshot in assoluto e' la baseline di partenza;
  2. uno snapshot con `reason = owner_profile_updated` (LMC-10) diventa il
     nuovo riferimento IN SILENZIO: quella variazione l'ha provocata il
     proprietario e la vede gia' nel portale, e cio' che verra' dopo si
     misura da li';
  3. una notifica emessa: lo snapshot notificato (`to_observation_id`)
     diventa il riferimento, cosi' il giorno dopo non si rinotifica lo
     stesso salto.

Gli snapshot intermedi identici o sotto soglia NON fanno avanzare il
riferimento: e' cosi' che la variazione cumulativa viene colta.

Il riferimento persistito e' l'`evidence.to_observation_id` dell'ultima
notifica di quella casa per quel proprietario. I casi 1 e 2 sono ricostruiti
dalla serie a ogni giro, in modo deterministico: non serve una tabella di
stato, e la chiave di idempotenza (che non contiene la data) garantisce che
rigiocare la stessa serie non produca duplicati.

METODO E VALORE.

Se `algorithm_fingerprint` cambia NEL CANDIDATO - cioe' rispetto allo
snapshot cronologicamente precedente, non rispetto al riferimento economico
- il salto non e' della casa ma del metodo con cui la si stima, e la copy lo
dice sobriamente, senza attribuirlo al mercato. Ma vale la stessa soglia: un
fingerprint nuovo con valore invariato o quasi non e' una notizia.

I due confronti sono deliberatamente diversi. La SOGLIA e' cumulativa e si
misura dal riferimento, perche' e' li' che sta l'ultimo stato che il
proprietario ha visto. La CLASSIFICAZIONE guarda solo al passo corrente:
se il metodo e' cambiato due snapshot fa, sotto soglia, e oggi il valore
supera la soglia con lo stesso metodo di ieri, il fatto di oggi e' un
cambiamento di valore. Dire "abbiamo aggiornato il metodo" descriverebbe
un evento che non e' avvenuto oggi e che, da solo, non era una notizia.

LA DOMANDA.

Dalle rilevazioni Buyer Pressure si prende SOLO la fascia owner-safe che
`owner/demand.py` gia' calcola (`low`/`medium`/`high`/`unavailable`), con le
sue stesse etichette. Notifica solo un cambio REALE di fascia fra due fasce
disponibili: `low -> low` no, `unavailable -> *` no, `* -> unavailable` no.
Punteggi, conteggi di acquirenti, budget e metriche interne non entrano
nell'evidence e non entrano nella copy: la decisione vede due parole, non
due numeri.
"""
from __future__ import annotations

from typing import Any

from . import demand

#: I tre tipi, gli stessi del CHECK della 069. Elenco chiuso.
VALUE_TYPE = "home_value_changed"
DEMAND_TYPE = "home_demand_changed"
METHOD_TYPE = "home_method_changed"
NOTIFICATION_TYPES = (VALUE_TYPE, DEMAND_TYPE, METHOD_TYPE)

#: LE DUE SOGLIE, ENTRAMBE OBBLIGATORIE. Regola di prodotto fissata in LMC-12.
VALUE_CHANGE_MIN_PCT = 5.0
VALUE_CHANGE_MIN_EUR = 5000

#: Il `reason` con cui LMC-10 marca lo snapshot nato da una correzione del
#: proprietario. E' la stessa stringa di `owner.home_update.REFRESH_REASON`;
#: si ripete qui, e un test lo verifica, perche' importare `home_update`
#: porterebbe con se' `home_profile`, che questo modulo non deve conoscere.
OWNER_EDIT_REASON = "owner_profile_updated"

#: Prefisso delle chiavi di idempotenza. Versionato: cambiare la semantica
#: del confronto e' cambiare `v1`.
KEY_PREFIX = "lmc12:v1"

#: Le fasce fra cui un cambio si notifica. `unavailable` non c'e': non e'
#: una fascia, e' l'assenza di una misura.
NOTIFIABLE_DEMAND_STATUSES = frozenset({demand.STATUS_LOW, demand.STATUS_MEDIUM,
                                        demand.STATUS_HIGH})


# ---------------------------------------------------------------------------
# Il valore
# ---------------------------------------------------------------------------

def _prezzo(snapshot: dict[str, Any]) -> float | None:
    valore = snapshot.get("price_exact")
    if isinstance(valore, bool) or not isinstance(valore, (int, float)):
        return None
    return float(valore)


def is_significant(from_value: float | None, to_value: float | None) -> bool:
    """Entrambe le soglie, in valore assoluto. Da zero o da niente non si
    misura una percentuale, quindi non e' significativo."""
    if from_value is None or to_value is None or from_value <= 0:
        return False
    delta = to_value - from_value
    if abs(delta) < VALUE_CHANGE_MIN_EUR:
        return False
    return abs(delta) / from_value * 100.0 >= VALUE_CHANGE_MIN_PCT


def _indice_riferimento(serie: list[dict[str, Any]], assorbito: int | None) -> int:
    """Da dove ripartire: lo snapshot gia' assorbito se e' nella serie,
    altrimenti il primo. Un id assorbito che non si trova (osservazione
    sparita) ricomincia dalla baseline: e' l'unica scelta deterministica."""
    if assorbito is not None:
        for indice, snapshot in enumerate(serie):
            if snapshot.get("observation_id") == assorbito:
                return indice
    return 0


def decide_value_alerts(snapshots: list[dict[str, Any]], *,
                        absorbed_observation_id: int | None = None) -> list[dict[str, Any]]:
    """Le notifiche del valore che questa serie contiene, da emettere in ordine.

    `snapshots` e' la serie `valuation_snapshot` di UNA casa, in ordine
    cronologico, con `observation_id`, `price_exact`, `algorithm_fingerprint`
    e `reason`. `absorbed_observation_id` e' l'ultimo `to_observation_id`
    gia' notificato a questo proprietario (None se nessuno).

    Ritorna zero o piu' decisioni; piu' di una solo se dall'ultimo giro sono
    passati diversi salti significativi, ciascuno gia' assorbito dal
    successivo.
    """
    serie = [s for s in snapshots if _prezzo(s) is not None]
    if not serie:
        return []
    riferimento = serie[_indice_riferimento(serie, absorbed_observation_id)]
    decisioni: list[dict[str, Any]] = []
    # Lo snapshot cronologicamente precedente al candidato: e' con QUESTO che
    # si confronta l'impronta dell'algoritmo, non con il riferimento.
    precedente = riferimento
    for candidato in serie[serie.index(riferimento) + 1:]:
        if candidato.get("reason") == OWNER_EDIT_REASON:
            # Il proprietario ha appena provocato questa variazione e la vede
            # gia': si riparte da qui, senza dire niente.
            riferimento = candidato
            precedente = candidato
            continue
        da, a = _prezzo(riferimento), _prezzo(candidato)
        metodo_cambiato = (candidato.get("algorithm_fingerprint")
                           != precedente.get("algorithm_fingerprint"))
        precedente = candidato
        if not is_significant(da, a):
            continue
        decisioni.append({
            "type": METHOD_TYPE if metodo_cambiato else VALUE_TYPE,
            "from_observation_id": riferimento["observation_id"],
            "to_observation_id": candidato["observation_id"],
            "from_value": da,
            "to_value": a,
            "delta_eur": a - da,
            "delta_pct": (a - da) / da * 100.0,
        })
        riferimento = candidato
    return decisioni


# ---------------------------------------------------------------------------
# La domanda
# ---------------------------------------------------------------------------

def demand_status(reading: dict[str, Any]) -> tuple[str, str]:
    """(fascia, etichetta) di una rilevazione, da `owner/demand.py` e basta.
    Qualunque rilevazione che il portale non saprebbe mostrare e'
    `unavailable` anche qui: la stessa funzione, lo stesso fail-closed."""
    blocco = demand.build({"metrics": reading.get("metrics"),
                           "observed_at": reading.get("observed_at")})
    return blocco["status"], blocco["label"]


def decide_demand_alerts(readings: list[dict[str, Any]], *,
                         absorbed_observation_id: int | None = None) -> list[dict[str, Any]]:
    """Le notifiche della domanda che questa serie contiene.

    `readings` sono le rilevazioni Buyer Pressure di UNA casa in ordine
    cronologico, con `observation_id`, `observed_at` e `metrics` (le sole
    chiavi che il portale legge). Il riferimento e' la rilevazione gia'
    notificata, altrimenti la prima; una rilevazione `unavailable` non fa
    mai da riferimento ne' da candidato.
    """
    if not readings:
        return []
    serie = [(r, *demand_status(r)) for r in readings]
    indice = _indice_riferimento([r for r, _, _ in serie], absorbed_observation_id)
    _, stato_rif, _ = serie[indice]
    decisioni: list[dict[str, Any]] = []
    etichetta_rif = serie[indice][2]
    for lettura, stato, etichetta in serie[indice + 1:]:
        if stato not in NOTIFIABLE_DEMAND_STATUSES:
            continue
        if stato_rif not in NOTIFIABLE_DEMAND_STATUSES:
            # Da "nessuna misura" a una fascia non si notifica: si assorbe in
            # silenzio, e il prossimo cambio si misura da qui.
            stato_rif, etichetta_rif = stato, etichetta
            continue
        if stato == stato_rif:
            continue
        decisioni.append({
            "type": DEMAND_TYPE,
            "observation_id": lettura["observation_id"],
            "from_status": stato_rif,
            "to_status": stato,
            "from_label": etichetta_rif,
            "to_label": etichetta,
        })
        stato_rif, etichetta_rif = stato, etichetta
    return decisioni


# ---------------------------------------------------------------------------
# Chiavi, evidence, parole
# ---------------------------------------------------------------------------

def idempotency_key(decision: dict[str, Any], *, stima_id: int, owner_account_id: int) -> str:
    """Deterministica e SENZA data: lo stesso fatto ha la stessa chiave in
    ogni giro, e il database la rifiuta la seconda volta."""
    tipo = decision["type"]
    if tipo == DEMAND_TYPE:
        return (f"{KEY_PREFIX}:{tipo}:stima:{stima_id}:"
                f"obs:{decision['observation_id']}:account:{owner_account_id}")
    return (f"{KEY_PREFIX}:{tipo}:stima:{stima_id}:"
            f"from:{decision['from_observation_id']}:to:{decision['to_observation_id']}:"
            f"account:{owner_account_id}")


def evidence(decision: dict[str, Any]) -> dict[str, Any]:
    """La prova interna, e solo quella. Identificativi di osservazione, i due
    valori o le due fasce. Mai un punteggio, mai un conteggio."""
    if decision["type"] == DEMAND_TYPE:
        return {"observation_id": decision["observation_id"],
                "from_status": decision["from_status"],
                "to_status": decision["to_status"]}
    return {"from_observation_id": decision["from_observation_id"],
            "to_observation_id": decision["to_observation_id"],
            "from_value": round(decision["from_value"]),
            "to_value": round(decision["to_value"]),
            "delta_pct": round(decision["delta_pct"], 1)}


def _euro(valore: float) -> str:
    return f"{int(round(valore)):,}".replace(",", ".") + " €"


def _percento(valore: float) -> str:
    segno = "+" if valore > 0 else "−" if valore < 0 else ""
    return f"{segno}{abs(valore):.1f}%".replace(".", ",")


#: Le parole, e la regola: sobrie, senza promesse, e MAI "il mercato e'
#: cambiato" - la fonte del valore e' il metodo di stima su dati della casa,
#: e la frase che attribuisce il salto al mercato sarebbe falsa.
def compose(decision: dict[str, Any]) -> tuple[str, str]:
    tipo = decision["type"]
    if tipo == DEMAND_TYPE:
        return (
            "La domanda per case come la tua è cambiata",
            f"Il livello di domanda nel database STIMA360 per immobili simili alla tua "
            f"casa è passato da «{decision['from_label']}» a «{decision['to_label']}». "
            "Trovi il dettaglio nella scheda della casa.",
        )
    da, a = _euro(decision["from_value"]), _euro(decision["to_value"])
    pct = _percento(decision["delta_pct"])
    if tipo == METHOD_TYPE:
        return (
            "Abbiamo aggiornato il metodo di stima",
            f"Il metodo con cui calcoliamo il valore stimato è stato aggiornato. "
            f"Con il nuovo metodo la stima della tua casa passa da {da} a {a} ({pct}). "
            "I dati della casa non sono cambiati.",
        )
    direzione = "è aumentato" if decision["delta_eur"] > 0 else "è diminuito"
    return (
        "Il valore stimato della tua casa è cambiato",
        f"Il valore stimato della tua casa {direzione}: da {da} a {a} ({pct}). "
        "Trovi l’andamento completo nella scheda della casa.",
    )
