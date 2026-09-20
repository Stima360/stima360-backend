"""LMC-10 - "Aggiorna la tua casa": la scrittura, e cio' che le sta intorno.

LA STIMA ORIGINALE NON SI TOCCA.

`stime` e' la fotografia del dato inserito al momento della valutazione, e
tre cose ci poggiano sopra: `initial_value`, la baseline `watch_started`, e
l'`input_digest` di ogni snapshot gia' scritto. Questo modulo non scrive mai
su `stime`. Scrive su `owner_home_overrides`, e il profilo che il resto del
sistema legge e' la somma dei due (`home_profile`).

L'ORDINE DEI CANCELLI, CHE E' ANCHE L'ORDINE DEI COSTI.

  1. il grant - se non c'e', 404 neutro, e non si e' letto nient'altro;
  2. la versione attesa - se non corrisponde, 409, e non si e' validato nulla;
  3. la patch - se non e' valida, 422, e non si e' scritto nulla;
  4. il confronto con il profilo effettivo corrente - se non cambia niente,
     "unchanged", e non si e' scritto nulla;
  5. la scrittura, con la versione ancora nel predicato;
  6. il ricalcolo del valore, che puo' fallire senza portarsi via il punto 5;
  7. l'evento per il radar, che puo' fallire senza portarsi via il punto 5.

I punti 6 e 7 vengono DOPO la scrittura e non possono annullarla. Il dato
della casa e' la verita' primaria: il ricalcolo e' un servizio, il radar e'
un segnale, e nessuno dei due vale quanto il fatto che il proprietario ci ha
appena detto che la sua casa ha tre bagni.

LA COSA CHE NON SI PUO' DIRE.

"Nuovo valore calcolato" solo se un nuovo snapshot e' nato davvero. Non se
il motore ha ridato lo stesso identico input (`reused`), non se il watch non
c'e', non se il refresh e' fallito. In tutti quei casi la risposta dice che
i dati sono aggiornati e tace sul valore, perche' tacere e' vero e la frase
comoda no.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

import home_profile
from core.exceptions import NotFoundError  # noqa: F401  (documenta la fonte del 404)

from . import home_service, tracking
from . import repository as owner_repository

logger = logging.getLogger(__name__)

#: Il `reason` con cui nasce lo snapshot dopo un aggiornamento. Finisce nel
#: payload dell'osservazione: serve a distinguere, guardando lo storico, un
#: punto nato dal mercato da uno nato perche' i dati sono cambiati.
REFRESH_REASON = "owner_profile_updated"

#: Gli esiti del ricalcolo, dichiarati. Solo il primo autorizza a dire al
#: proprietario che il valore e' stato ricalcolato.
VALUATION_RECALCULATED = "recalculated"
VALUATION_UNCHANGED = "unchanged"
VALUATION_UNAVAILABLE = "unavailable"

#: Gli esiti dell'aggiornamento.
STATUS_UPDATED = "updated"
STATUS_UNCHANGED = "unchanged"

#: I valori di `piano` che il motore riconosce come non numerici
#: (`valuation._parse_piano`). Gli altri ammessi sono i numeri interi, come
#: testo, entro `PIANO_MAX`.
PIANO_PAROLE = ("terra", "ultimo")
PIANO_MAX = 60

#: Gli stati che il motore distingue (`valuation.coeff_stato`). Qualunque
#: altra parola per lui vale 1.00: lasciarla scrivere significherebbe offrire
#: al proprietario una casella che non produce nessun effetto e non dirglielo.
STATO_VALORI = ("nuovo", "ristrutturato", "buono", "scarso", "grezzo")

#: Come l'ascensore e' scritto in tabella dal funnel pubblico.
ASCENSORE_SI = "True"
ASCENSORE_NO = "False"

_ASCENSORE_VERO = frozenset({"si", "sì", "true", "1", "yes", "y"})

#: I limiti dei campi numerici. Non sono precisione: sono il confine oltre il
#: quale un numero non descrive piu' una casa, e servono a non far arrivare
#: al motore un `mq` di nove cifre.
LIMITI_INTERI = {
    "mq": (1, 10000),
    "locali": (1, 100),
    "bagni": (0, 50),
    "anno": (1500, 2100),
    "mqgiardino": (0, 100000),
    "mqgarage": (0, 10000),
    "mqcantina": (0, 10000),
    "mqpostoauto": (0, 10000),
    "mqtaverna": (0, 10000),
    "mqsoffitta": (0, 10000),
    "mqterrazzo": (0, 10000),
    "numbalconi": (0, 50),
}

#: Quanto testo libero. `altrodescrizione` e' TEXT in tabella, quindi il
#: limite e' una decisione, non un vincolo ereditato.
ALTRODESCRIZIONE_MAX = 2000


class InvalidHomeUpdate(ValueError):
    """La patch non e' accettabile. Porta i campi, non un messaggio solo.

    I campi servono al frontend per segnare le caselle sbagliate; il testo
    resta generico perche' un messaggio per ogni combinazione sarebbe una
    seconda copia delle regole qui sotto.
    """

    def __init__(self, fields):
        self.fields = tuple(fields)
        super().__init__("campi non validi: " + ", ".join(self.fields))


class HomeVersionConflict(Exception):
    """Qualcun altro ha scritto nel frattempo. Mai una sovrascrittura."""


# ---------------------------------------------------------------------------
# VALIDAZIONE
# ---------------------------------------------------------------------------

def _intero(campo: str, valore: Any) -> int:
    if isinstance(valore, bool):
        raise ValueError
    if isinstance(valore, int):
        numero = valore
    elif isinstance(valore, str) and valore.strip():
        numero = int(valore.strip())
    else:
        raise ValueError
    minimo, massimo = LIMITI_INTERI[campo]
    if not minimo <= numero <= massimo:
        raise ValueError
    return numero


def _ascensore(valore: Any) -> str:
    if isinstance(valore, bool):
        return ASCENSORE_SI if valore else ASCENSORE_NO
    testo = str(valore or "").strip().lower()
    if testo in _ASCENSORE_VERO:
        return ASCENSORE_SI
    if testo in ("no", "false", "0", "n"):
        return ASCENSORE_NO
    raise ValueError


def _piano(valore: Any, attuale: Any) -> str:
    testo = str(valore).strip() if valore is not None else ""
    if not testo:
        raise ValueError
    if testo.lower() in PIANO_PAROLE:
        return testo.lower()
    if testo.isdigit() and 0 <= int(testo) <= PIANO_MAX:
        return str(int(testo))
    # Rimandare indietro quello che c'e' gia' non e' una modifica, e non deve
    # diventare un errore: una casa nata con `piano = "attico"` ha un valore
    # che l'insieme chiuso non contiene, e il form lo rispedisce tale e quale
    # finche' il proprietario non ne sceglie un altro.
    if attuale is not None and testo == str(attuale).strip():
        return testo
    raise ValueError


def _stato(valore: Any, attuale: Any) -> str:
    testo = str(valore or "").strip().lower()
    if testo in STATO_VALORI:
        return testo
    if attuale is not None and str(valore).strip() == str(attuale).strip():
        return str(valore).strip()
    raise ValueError


def _pertinenze(valore: Any, originale: Any) -> str:
    """Token, non testo libero. Gli extra dell'originale si conservano.

    Il client manda una lista di token dell'insieme chiuso. Il server la
    serializza nel formato storico e vi riattacca i pezzi che il motore non
    riconosce e che erano gia' scritti nell'originale ("box auto", "posto
    barca"): non sono modificabili dal proprietario, e farli sparire al primo
    salvataggio sarebbe cancellare il lavoro di qualcun altro senza dirlo.

    LA LISTA VUOTA E' LECITA, ed e' il punto di questa revisione. Serializza
    a stringa vuota, che in `owner_home_overrides.pertinenze` significa
    "nessuna pertinenza" e non "nessun override" (vedi
    `home_profile.EMPTY_MEANS_OVERRIDE`). Senza di essa una stima nata con
    un garage che non esiste non sarebbe correggibile: tolta l'ultima
    pertinenza, il profilo effettivo sarebbe tornato a mostrare quella
    dell'originale.

    Con un extra nell'originale la lista vuota non produce una stringa
    vuota ma quel solo extra, ed e' coerente: "box auto" non e' un token che
    il proprietario possa togliere, quindi non se ne va perche' ha
    deselezionato le caselle.
    """
    if isinstance(valore, str) or not isinstance(valore, (list, tuple)):
        raise ValueError
    scelti = []
    for token in valore:
        if not isinstance(token, str):
            raise ValueError
        pulito = token.strip().lower()
        if pulito not in home_profile.PERTINENZE_TOKENS:
            raise ValueError
        if pulito not in scelti:
            scelti.append(pulito)
    return home_profile.serialize_pertinenze(
        scelti, extra=home_profile.extra_pertinenze(originale))


def _altrodescrizione(valore: Any) -> str:
    testo = str(valore or "").strip()
    if not testo or len(testo) > ALTRODESCRIZIONE_MAX:
        raise ValueError
    return testo


def normalize_patch(patch: Mapping[str, Any], *, effettivo: Mapping[str, Any],
                    originale: Mapping[str, Any]) -> dict[str, Any]:
    """Da cio' che il client manda a cio' che va in tabella, o `InvalidHomeUpdate`.

    Tre rifiuti secchi, prima di guardare i valori: una chiave fuori dalla
    whitelist, una patch vuota, un `null`. Il `null` in particolare non e'
    "cancella questo campo": in LMC-10 quel gesto non esiste, e interpretarlo
    per cortesia significherebbe inventarlo.
    """
    if not isinstance(patch, Mapping) or not patch:
        raise InvalidHomeUpdate(("patch",))

    fuori = [k for k in patch if k not in home_profile.OVERRIDABLE_FIELDS]
    if fuori:
        raise InvalidHomeUpdate(sorted(fuori))

    valori: dict[str, Any] = {}
    errori: list[str] = []
    for campo in home_profile.OVERRIDABLE_FIELDS:
        if campo not in patch:
            continue
        grezzo = patch[campo]
        if grezzo is None:
            errori.append(campo)
            continue
        try:
            if campo in LIMITI_INTERI:
                valori[campo] = _intero(campo, grezzo)
            elif campo == "ascensore":
                valori[campo] = _ascensore(grezzo)
            elif campo == "piano":
                valori[campo] = _piano(grezzo, effettivo.get("piano"))
            elif campo == "stato":
                valori[campo] = _stato(grezzo, effettivo.get("stato"))
            elif campo == "pertinenze":
                valori[campo] = _pertinenze(grezzo, originale.get("pertinenze"))
            elif campo == "altrodescrizione":
                valori[campo] = _altrodescrizione(grezzo)
            else:  # pragma: no cover - la whitelist e' coperta per intero
                errori.append(campo)
        except (ValueError, TypeError):
            errori.append(campo)
    if errori:
        raise InvalidHomeUpdate(sorted(errori))
    return valori


def _equivalente(campo: str, a: Any, b: Any) -> bool:
    """Due valori dicono la stessa cosa della casa?

    Non e' `==` e basta, perche' due scritture diverse possono significare la
    stessa cosa e finire per contare come una modifica che non c'e':
    `ascensore` vale "True" o "true" a seconda di chi ha scritto la riga, e
    `pertinenze` e' una stringa il cui ordine e la cui spaziatura non
    contano. Confrontarli alla lettera farebbe scattare un aggiornamento (e
    un evento sul radar, e forse uno snapshot) per un nulla di fatto.
    """
    if campo == "ascensore":
        return (str(a or "").strip().lower() in _ASCENSORE_VERO) == \
               (str(b or "").strip().lower() in _ASCENSORE_VERO)
    if campo == "pertinenze":
        return ((home_profile.parse_pertinenze(a), home_profile.extra_pertinenze(a))
                == (home_profile.parse_pertinenze(b), home_profile.extra_pertinenze(b)))
    if isinstance(a, str) or isinstance(b, str):
        return str(a or "").strip() == str(b or "").strip()
    return a == b


def _cambia(campo: str, valore: Any, corrente: Mapping[str, Any],
            gia_dichiarati) -> bool:
    """Questa patch cambia qualcosa? Non sempre e' una domanda sul VALORE.

    Per quasi tutti i campi lo e', e basta `_equivalente`. Per `pertinenze`
    no, e il caso e' preciso: una stima senza pertinenze dichiarate e una
    casa per cui il proprietario ha detto "non ce ne sono" hanno lo stesso
    valore effettivo - niente - ma non sono la stessa cosa. La prima e'
    silenzio, la seconda e' una risposta, e la completezza del profilo le
    distingue (vedi `home_service._dichiarato_vuoto`).

    Quindi passare dal silenzio alla risposta E' una modifica, anche se il
    valore non si muove: nasce la riga, sale la versione, il radar vede
    l'aggiornamento. Salvare la stessa risposta una seconda volta non lo e'
    piu', perche' il campo e' gia' dichiarato - ed e' il NO-OP atteso.

    La regola vale solo dove la stringa vuota e' un'affermazione, cioe' solo
    per le pertinenze: `EMPTY_MEANS_OVERRIDE` decide, non un elenco scritto
    qui.
    """
    if not _equivalente(campo, valore, corrente.get(campo)):
        return True
    return (campo in home_profile.EMPTY_MEANS_OVERRIDE
            and isinstance(valore, str) and not valore.strip()
            and campo not in (gia_dichiarati or ()))


# ---------------------------------------------------------------------------
# IL SERVIZIO
# ---------------------------------------------------------------------------

def _refresh(agency_id: int, stima_id: int, now: datetime | None) -> str:
    """Il ricalcolo, che puo' fallire senza conseguenze sull'aggiornamento.

    Import locale: `property_watch.service` importa a sua volta il dominio
    Property Watch per intero, e questo modulo viene caricato dal router del
    portale. Tenerlo qui dentro evita di legare l'avvio del portale al
    caricamento di quel dominio, ed e' la stessa scelta gia' fatta in
    `tracking._capability_reale`.
    """
    from property_watch import service as pw_service

    try:
        esito = pw_service.refresh_valuation_snapshot_scoped(
            home_service.OwnerAgencyScope(agency_id), stima_id,
            reason=REFRESH_REASON, now=now)
    except Exception as exc:  # noqa: BLE001 - il valore e' un servizio, il dato no
        # Il caso piu' comune non e' un guasto: e' una casa senza watch, per
        # cui LMC-3 solleva `WatchNotFoundError`. In entrambi i casi la
        # risposta al proprietario e' la stessa, perche' in entrambi non c'e'
        # nessun valore nuovo da annunciare.
        logger.warning("owner_home_update_refresh_failed stima_id=%s error_type=%s",
                       stima_id, type(exc).__name__)
        return VALUATION_UNAVAILABLE
    return (VALUATION_RECALCULATED if esito.get("status") == "created"
            else VALUATION_UNCHANGED)


def update_home(owner_account_id: int, stima_id: int, patch: Mapping[str, Any],
                expected_version: Any, *, now: datetime | None = None
                ) -> dict[str, Any]:
    """Aggiorna i dati della casa. Vedi l'ordine dei cancelli in testa al file.

    `NotFoundError` quando il grant non c'e' (404 neutro), `HomeVersionConflict`
    quando la versione attesa non e' quella corrente (409), `InvalidHomeUpdate`
    quando la patch non passa (422). Ogni altra eccezione risale: perdere una
    correzione e rispondere "fatto" sarebbe la stessa bugia di LMC-9.
    """
    stima = owner_repository.get_home_grant(owner_account_id, stima_id)
    agency_id = stima["agency_id"]

    override = owner_repository.home_override(agency_id, stima_id)
    corrente = home_profile.build_effective_home_profile(stima, override)

    if not isinstance(expected_version, int) or isinstance(expected_version, bool):
        raise InvalidHomeUpdate(("expected_version",))
    if expected_version != corrente["version"]:
        raise HomeVersionConflict("versione non corrispondente")

    valori = normalize_patch(patch, effettivo=corrente["home"], originale=stima)

    cambiati = {campo: valore for campo, valore in valori.items()
                if _cambia(campo, valore, corrente["home"],
                           corrente["overridden_fields"])}
    if not cambiati:
        # NO-OP. Niente riga, niente versione, niente `updated_at`, niente
        # evento, niente snapshot. Una modifica che non modifica non e' un
        # segnale: farla contare come tale insegnerebbe all'operatore a
        # richiamare chi ha solo riaperto il form.
        return {"status": STATUS_UNCHANGED,
                "profile_version": corrente["version"],
                "updated_fields": [],
                "value_recalculated": False,
                "valuation_status": VALUATION_UNCHANGED,
                "home": home_service.compose_home(owner_account_id, stima_id)}

    riga = owner_repository.upsert_home_override(
        owner_account_id, stima_id, cambiati, corrente["version"])
    if riga is None:
        raise HomeVersionConflict("versione non corrispondente")

    stato_valore = _refresh(agency_id, stima_id, now)

    # Il radar, per ultimo e fail-open: l'aggiornamento e' gia' salvato, e un
    # guasto di Seller Intelligence non deve trasformarlo in un errore. Il
    # prezzo dichiarato e' che in quel caso il segnale si perde - e' la stessa
    # scelta di LMC-7, e vale perche' qui il dato della casa non e' l'evento.
    tracking.track_home_updated(owner_account_id, stima_id, when=now)

    return {"status": STATUS_UPDATED,
            "profile_version": int(riga["version"]),
            "updated_fields": sorted(cambiati),
            "value_recalculated": stato_valore == VALUATION_RECALCULATED,
            "valuation_status": stato_valore,
            "home": home_service.compose_home(owner_account_id, stima_id)}
