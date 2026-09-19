"""LMC-3 - lo snapshot del valore: il motore ufficiale, e niente altro.

QUESTO MODULO NON VALUTA NIENTE. Prende i dati che la stima porta scritti, li
traduce nelle chiavi che `valuation.compute_from_payload` si aspetta, chiama
QUELLA funzione - la stessa che `/api/salva_stima` chiama alla riga 1080 di
`main.py` - e confeziona il risultato in un'osservazione di Property Watch.
Nessun coefficiente vive qui, nessuna formula, nessun `BASE_MQ`: una seconda
formula sarebbe una seconda verita', e il giorno in cui le due divergessero
nessuno saprebbe quale delle due il proprietario sta guardando.

L'IMPRONTA DELL'ALGORITMO

Serve distinguere "il mercato si e' mosso" da "abbiamo cambiato il metodo".
Il progetto non aveva una versione ufficiale del motore di valutazione: MATCH
ha `ALGORITHM_VERSION = "match-0.1"` scritto a mano e P22 ha
`P22_ALGORITHM_VERSION`, ma sono etichette che qualcuno deve ricordarsi di
alzare - e una versione che nessuno alza e' peggio di nessuna versione,
perche' afferma una stabilita' che non c'e'.

Qui l'impronta e' DERIVATA dal codice realmente eseguito: lo sha256 del file
`valuation.py`, troncato a 16 caratteri esadecimali, prefissato da
`valuation-`. E' deterministica (stesso file, stessa impronta), non e'
inventata, e cambia esattamente quando cambia un coefficiente, una soglia o
la formula.

Il suo limite, dichiarato: cambia anche per una modifica che non tocca il
risultato - un commento, uno spazio. L'errore e' dalla parte sicura: si dira'
"metodologia cambiata, percentuale non confrontabile" quando in realta' era
confrontabile, mai il contrario. Se un giorno servira' distinguere le due
cose, la strada e' una costante esplicita in `valuation.py` che il motore
dichiari di se': e' una decisione di quel modulo, non di questo.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import valuation
from valuation import compute_from_payload

#: Il tipo di osservazione che porta uno snapshot del valore. `observation_type`
#: e' `VARCHAR(100)` senza CHECK (migration 022), quindi un tipo nuovo non
#: richiede nessuna migration: e' il motivo per cui LMC-3 non ne ha una.
SNAPSHOT_OBSERVATION = "valuation_snapshot"

#: La sorgente, come per ogni altra osservazione calcolata dal sistema.
SNAPSHOT_SOURCE = "internal"

#: Il file del motore. Variabile di modulo perche' i test ne sostituiscono uno
#: finto per dimostrare che l'impronta cambia davvero con il codice.
_ENGINE_SOURCE = Path(valuation.__file__)

#: Le chiavi che `compute_from_payload` legge dal proprio payload. Elencate qui
#: per poter verificare - con un test - che la mappa sotto le copra tutte.
ENGINE_PAYLOAD_KEYS = (
    "comune", "microzona", "tipologia", "mq", "piano", "locali", "bagni",
    "ascensore", "anno", "stato", "posizioneMare", "distanzaMare",
    "barrieraMare", "vistaMareYN", "vistaMareDettaglio", "vistaMare",
    "pertinenze", "mqGiardino", "mqGarage", "mqCantina", "mqPostoAuto",
    "mqTaverna", "mqSoffitta", "mqTerrazzo", "numBalconi", "via",
    "altroDescrizione",
)

#: Dalla colonna di `stime` alla chiave del motore. E' la stessa
#: corrispondenza che `main.py` costruisce a mano in `payload_rules` quando
#: la stima nasce; qui e' dichiarata una volta, in una tabella leggibile,
#: invece di essere ricopiata.
#:
#: `ascensore` non c'e': in `stime` e' una colonna di testo che porta cio' che
#: il funnel vi ha scritto (`True`/`False`), mentre il motore si aspetta la
#: forma "Sì"/"No". La conversione ha una funzione sua, sotto.
ENGINE_FIELD_MAP = (
    ("comune", "comune"),
    ("microzona", "microzona"),
    ("tipologia", "tipologia"),
    ("mq", "mq"),
    ("piano", "piano"),
    ("locali", "locali"),
    ("bagni", "bagni"),
    ("anno", "anno"),
    ("stato", "stato"),
    ("posizionemare", "posizioneMare"),
    ("distanzamare", "distanzaMare"),
    ("barrieramare", "barrieraMare"),
    ("vistamareyn", "vistaMareYN"),
    ("vistamaredettaglio", "vistaMareDettaglio"),
    ("vistamare", "vistaMare"),
    ("pertinenze", "pertinenze"),
    ("mqgiardino", "mqGiardino"),
    ("mqgarage", "mqGarage"),
    ("mqcantina", "mqCantina"),
    ("mqpostoauto", "mqPostoAuto"),
    ("mqtaverna", "mqTaverna"),
    ("mqsoffitta", "mqSoffitta"),
    ("mqterrazzo", "mqTerrazzo"),
    ("numbalconi", "numBalconi"),
    ("via", "via"),
    ("altrodescrizione", "altroDescrizione"),
)

#: I valori che il motore stesso riconosce come "c'e' l'ascensore" (vedi
#: `valuation.coeff_ascensore`), piu' quelli che il funnel scrive in tabella.
_TRUTHY = frozenset({"si", "sì", "true", "1", "yes", "y"})


def algorithm_fingerprint() -> str:
    """Lo sha256 del motore, troncato. Ricalcolato a ogni chiamata: costa una
    lettura di file e non puo' restare indietro rispetto al codice caricato."""
    digest = hashlib.sha256(_ENGINE_SOURCE.read_bytes()).hexdigest()[:16]
    return f"valuation-{digest}"


#: Calcolata all'import per chi la vuole come costante; le funzioni usano
#: comunque questo nome, cosi' un test puo' sostituirla per simulare un
#: cambio di metodologia.
ALGORITHM_FINGERPRINT = algorithm_fingerprint()


def _lift(value: Any) -> str:
    """"Sì" o "No", con la stessa semantica del funnel pubblico.

    Il funnel legge il form con `to_bool` e passa al motore "Sì"/"No"; in
    tabella la colonna conserva la rappresentazione testuale di quel booleano.
    Qui si riconoscono le stesse parole che il motore gia' considera vere, e
    tutto il resto - compreso l'assente - e' "No". Non e' un default nuovo: e'
    la lettura di un campo che, quando manca, il motore tratta gia' cosi'.
    """
    return "Sì" if str(value or "").strip().lower() in _TRUTHY else "No"


def build_engine_payload(stima: dict[str, Any]) -> dict[str, Any]:
    """Il payload per `compute_from_payload`, dai dati persistiti della stima.

    Un campo assente resta `None`: i ripieghi restano quelli storici del
    motore, che li applica da sempre. Aggiungerne di nuovi qui significherebbe
    decidere oggi, per conto del proprietario, un valore che nessuno gli ha
    chiesto.

    Niente dato personale entra nel payload: la mappa nomina solo colonne che
    descrivono l'immobile, e `nome`, `email`, `telefono`, `prezzo_mq_base`,
    `lead_status` non vi compaiono.
    """
    payload: dict[str, Any] = {
        destinazione: stima.get(origine) for origine, destinazione in ENGINE_FIELD_MAP
    }
    payload["ascensore"] = _lift(stima.get("ascensore"))
    return payload


def payload_digest(payload: dict[str, Any]) -> str:
    """L'impronta dell'INPUT: sha256 del JSON canonico.

    Stessa tecnica di `property_watch.buyer_pressure.metrics_digest`: chiavi
    ordinate, separatori stretti, niente NaN. L'ordine con cui il dizionario e'
    stato costruito non conta, il contenuto si'.
    """
    grezzo = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=True, allow_nan=False, default=str).encode("utf-8")
    return hashlib.sha256(grezzo).hexdigest()


def snapshot_idempotency_key(*, watch_id: int, computed_at: datetime,
                             input_digest: str, fingerprint: str) -> str:
    """La chiave: watch, GIORNO, impronta dell'input, impronta dell'algoritmo.

    Quattro componenti, e ognuna risponde a una domanda diversa.

    Il GIORNO (UTC) impedisce le venti righe identiche nello stesso minuto: un
    cron che gira ogni ora, o un operatore che preme due volte, trovano la
    chiave gia' usata e non scrivono niente. Il giorno dopo, con gli stessi
    dati, nasce un punto nuovo - ed e' giusto, perche' "il 20 valeva ancora
    185.000" e' un'informazione vera sul tempo, non un duplicato.

    L'IMPRONTA DELL'INPUT fa nascere subito un punto quando i dati della casa
    cambiano, senza aspettare domani.

    L'IMPRONTA DELL'ALGORITMO fa nascere un punto quando cambia il metodo, ed
    e' cio' che permette poi di dire "questa variazione non e' mercato".

    Nessuna componente sovrascrive: la INSERT e' `ON CONFLICT DO NOTHING`, e
    uno snapshot scritto non viene mai modificato.
    """
    giorno = computed_at.astimezone(timezone.utc).date().isoformat()
    return (f"property_watch:{SNAPSHOT_OBSERVATION}:watch:{watch_id}:"
            f"{giorno}:{input_digest[:16]}:{fingerprint}")


def compute_snapshot(stima: dict[str, Any], *, reason: str,
                     computed_at: datetime | None = None) -> dict[str, Any]:
    """Calcola il valore con il motore ufficiale e confeziona il payload.

    Non scrive niente: qui si calcola, la scrittura e' del servizio. Il
    risultato porta i tre numeri del motore, quando e' stato calcolato, perche'
    (`reason`), e le due impronte - input e algoritmo - senza le quali una
    serie di valori non si puo' leggere.
    """
    quando = computed_at or datetime.now(timezone.utc)
    payload_motore = build_engine_payload(stima)
    risultato = compute_from_payload(payload_motore)
    return {
        "price_exact": risultato["price_exact"],
        "eur_mq_finale": risultato["eur_mq_finale"],
        "base_mq": risultato["base_mq"],
        "computed_at": quando.astimezone(timezone.utc).isoformat(),
        "reason": reason,
        "algorithm_fingerprint": ALGORITHM_FINGERPRINT,
        "input_digest": payload_digest(payload_motore),
    }
