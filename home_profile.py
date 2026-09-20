"""LMC-10 - ORIGINALE + OVERRIDE = PROFILO EFFETTIVO. L'unico composer.

DOVE VIVE, E PERCHE' QUI.

Questo modulo sta alla radice, accanto a `valuation.py`, e non dentro
`owner/`. Non e' un capriccio di gusto: `owner/demand.py` importa gia'
`property_watch.buyer_pressure`, e `property_watch.service` deve chiamare
questo composer per costruire il payload del motore. Se il composer stesse
in `owner/`, le due direzioni si chiuderebbero in un ciclo. Qui non importa
niente da nessuno dei due domini - non importa niente e basta, oltre alla
libreria standard - e puo' essere chiamato da tutti.

COSA FA, E COSA NON FA.

Fa una cosa sola: sovrappone all'originale i campi che il proprietario ha
corretto, e dice quali sono. Non apre connessioni, non guarda l'orologio,
non decide chi puo' scrivere. Due chiamate sugli stessi dizionari danno lo
stesso identico risultato.

Non fa - e questo e' il punto - una SECONDA versione della regola. Il
portale, il CRM e Property Watch chiamano questa funzione: se un giorno
"override vince se non nullo" dovesse diventare qualcos'altro, diventerebbe
qualcos'altro in un posto solo. Una COALESCE scritta in SQL dentro la query
di Property Watch sarebbe stata una seconda implementazione della stessa
frase, e il giorno in cui le due divergessero nessuno saprebbe quale delle
due il proprietario sta guardando.

LA WHITELIST E' QUI, UNA VOLTA.

`OVERRIDABLE_FIELDS` e' la stessa lista che la migration 068 ha messo in
colonne. Un test la riconfronta con `information_schema` a ogni esecuzione,
perche' due elenchi che devono coincidere e che nessuno confronta prima o
poi non coincidono.

`NULL` NON E' "CANCELLA", CON UNA ECCEZIONE DICHIARATA.

Un override nullo significa "il proprietario non ha corretto questo campo",
non "il proprietario ha svuotato questo campo": il merge non deve poter
simulare una cancellazione per sbaglio.

L'eccezione e' `pertinenze`, e non e' una scorciatoia: "questa casa non ha
nessuna pertinenza" e' un valore vero della casa, e senza poterlo scrivere
una stima nata con un garage che non esiste resterebbe non correggibile.
Vedi `EMPTY_MEANS_OVERRIDE`.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

#: I campi che il proprietario puo' correggere. STESSO ORDINE e stesso
#: insieme delle colonne della migration 068.
#:
#: Cosa NON c'e', e perche': `comune` e `microzona` scelgono la base EUR/mq
#: (`valuation.get_base_mq`), quindi sarebbero il prezzo al metro quadro
#: lasciato scegliere a chi vende; `via` e `civico` sono identita' (e il
#: motore non li legge nemmeno); `tipologia`, `posizionemare`,
#: `distanzamare`, `barrieramare` e la vista mare sono classificazione e
#: posizione, non cambiano nel tempo e pesano sul coefficiente. Per tutti
#: questi la strada e' LMC-9, la richiesta di verifica gratuita: una zona
#: sbagliata e' una rivalutazione, non un aggiornamento.
OVERRIDABLE_FIELDS = (
    "mq", "piano", "locali", "bagni", "ascensore", "anno", "stato",
    "pertinenze", "mqgiardino", "mqgarage", "mqcantina", "mqpostoauto",
    "mqtaverna", "mqsoffitta", "mqterrazzo", "numbalconi", "altrodescrizione",
)

#: Le colonne di servizio della tabella override: non sono dati della casa e
#: non entrano mai nel profilo effettivo.
OVERRIDE_METADATA_FIELDS = ("id", "stima_id", "version", "created_at",
                            "updated_at", "updated_by_owner_account_id")

#: La versione quando nessun override esiste ancora. Non `null`: il client
#: deve poter mandare `expected_version` alla PRIMA modifica senza dover
#: distinguere "non c'e' riga" da "c'e' e vale zero" - qui zero significa
#: esattamente "non c'e' riga", e l'INSERT e' consentito solo con questo.
NO_OVERRIDE_VERSION = 0


#: I campi per cui la STRINGA VUOTA e' un override esplicito e non "colonna
#: mai riempita". Uno solo, e per una ragione precisa.
#:
#: Per ogni altro campo `''` significa "il proprietario non ha corretto
#: questo": un `altrodescrizione` salvato vuoto oscurerebbe per sempre quello
#: che c'era nell'originale, ed e' la cosa che LMC-10 non deve poter fare.
#:
#: `pertinenze` e' diverso perche' "nessuna pertinenza" e' un valore VERO
#: della casa, non l'assenza di un valore. Una stima nata con "garage"
#: sbagliato non sarebbe altrimenti correggibile: togliendo l'ultima
#: pertinenza il campo tornerebbe a mostrare il garage dell'originale, e il
#: proprietario si vedrebbe rifiutare - o peggio, ignorare - proprio la
#: correzione che stava facendo. Quindi:
#:
#:     NULL              nessun override, vale `stime.pertinenze`
#:     ''                override esplicito, nessuna pertinenza
#:     'garage, cantina' override esplicito, garage + cantina
#:
#: La distinzione vale SOLO qui. Aggiungere un campo a questo insieme
#: significa decidere che per quel campo "vuoto" e' un'affermazione, ed e'
#: una decisione di dominio, non una comodita' di implementazione.
EMPTY_MEANS_OVERRIDE = frozenset({"pertinenze"})


def _has_value(valore: Any, campo: str | None = None) -> bool:
    """C'e' un override su questo campo?

    `None` non e' mai un override. Una stringa vuota o di soli spazi lo e'
    solo per i campi di `EMPTY_MEANS_OVERRIDE`, dove "vuoto" e'
    un'affermazione sulla casa e non una colonna non riempita.
    """
    if valore is None:
        return False
    if isinstance(valore, str) and not valore.strip():
        return campo in EMPTY_MEANS_OVERRIDE
    return True


def overridden_fields(overrides: Mapping[str, Any] | None) -> tuple[str, ...]:
    """I campi effettivamente corretti, nell'ordine della whitelist."""
    if not overrides:
        return ()
    return tuple(campo for campo in OVERRIDABLE_FIELDS
                 if _has_value(overrides.get(campo), campo))


def override_version(overrides: Mapping[str, Any] | None) -> int:
    """La versione corrente del profilo: `0` quando nessun override esiste."""
    if not overrides:
        return NO_OVERRIDE_VERSION
    valore = overrides.get("version")
    return int(valore) if valore is not None else NO_OVERRIDE_VERSION


def build_effective_home_profile(original: Mapping[str, Any] | None,
                                 overrides: Mapping[str, Any] | None = None
                                 ) -> dict[str, Any]:
    """ORIGINALE + OVERRIDE = PROFILO EFFETTIVO.

    `original` e' la riga di `stime` come il chiamante l'ha letta (le sue
    colonne, non necessariamente tutte); `overrides` la riga di
    `owner_home_overrides`, oppure `None`.

    Ritorna:

        home              il dizionario effettivo: una COPIA di `original`
                          con i campi corretti sostituiti
        overridden_fields quali campi sono stati corretti
        version           la versione del profilo (0 = nessun override)
        updated_at        quando il proprietario lo ha corretto, o `None`

    Un campo che l'originale non porta e l'override si' entra comunque: e'
    il caso di una lettura che nomina meno colonne, e nasconderlo darebbe
    due profili effettivi diversi a seconda di chi legge.
    """
    effettivo: dict[str, Any] = dict(original or {})
    corretti = overridden_fields(overrides)
    for campo in corretti:
        effettivo[campo] = overrides[campo]  # type: ignore[index]
    return {
        "home": effettivo,
        "overridden_fields": corretti,
        "version": override_version(overrides),
        "updated_at": (overrides or {}).get("updated_at"),
    }


def effective_home(original: Mapping[str, Any] | None,
                   overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Solo il dizionario effettivo. Comodita', non una seconda regola."""
    return build_effective_home_profile(original, overrides)["home"]


# ---------------------------------------------------------------------------
# LE PERTINENZE: UN INSIEME CHIUSO, NON DEL TESTO LIBERO.
#
# `valuation.compute_from_payload` spezza `pertinenze` sui separatori e cerca
# TOKEN precisi (`garage`, `posto auto`, ...), piu' tre parole nel testo
# intero (`piscina`, `posto moto`, `posto bici`). Una parola che non e' in
# quell'elenco non vale zero euro: vale NIENTE, cioe' il motore non la vede
# affatto. Lasciare che il browser mandi testo libero significherebbe quindi
# offrire al proprietario di scrivere "box auto" e non dirgli mai che per il
# sistema non esiste.
#
# Percio' l'API accetta TOKEN, non una stringa, e il server serializza nel
# formato storico che `stime` e il motore gia' usano.
# ---------------------------------------------------------------------------

#: I token che il motore riconosce davvero. Derivati da
#: `valuation.compute_from_payload` e `valuation.valore_pertinenze`: i primi
#: otto sono cercati come elementi della lista, gli ultimi tre come parole
#: dentro il testo.
PERTINENZE_TOKENS = (
    "garage", "posto auto", "cantina", "soffitta", "taverna", "balconi",
    "terrazzo", "giardino", "piscina", "posto moto", "posto bici",
)

#: Il separatore con cui il valore torna in `stime`. Il motore normalizza
#: `;`, `|`, `/` e `\` in virgole prima di spezzare: la virgola e' quindi la
#: forma che non ha bisogno di essere tradotta.
PERTINENZE_SEPARATOR = ", "

_PERTINENZE_ALTRI_SEPARATORI = (";", "|", "/", "\\")


def _spezza(raw: Any) -> list[str]:
    """La stessa tokenizzazione del motore, applicata al testo persistito."""
    testo = str(raw or "").lower()
    for sep in _PERTINENZE_ALTRI_SEPARATORI:
        testo = testo.replace(sep, ",")
    return [pezzo.strip() for pezzo in testo.split(",") if pezzo.strip()]


def parse_pertinenze(raw: Any) -> tuple[str, ...]:
    """I token riconosciuti presenti nel valore, nell'ordine canonico.

    `piscina`, `posto moto` e `posto bici` il motore li cerca dentro il testo
    e non come elementi della lista, quindi si cercano allo stesso modo: un
    "posto moto coperto" scritto tutto attaccato vale per il motore, e deve
    valere anche per il form del proprietario.
    """
    elenco = _spezza(raw)
    testo = ", ".join(elenco)
    presenti = []
    for token in PERTINENZE_TOKENS:
        if token in elenco or (token in ("piscina", "posto moto", "posto bici")
                               and token in testo):
            presenti.append(token)
    return tuple(presenti)


def extra_pertinenze(raw: Any) -> tuple[str, ...]:
    """I pezzi che il motore non riconosce, nell'ordine in cui erano.

    Non si buttano. Sono spesso cio' che un operatore ha scritto a mano
    ("box auto", "posto barca"), e il proprietario non li puo' modificare
    perche' non sono nell'insieme chiuso: farli sparire al primo salvataggio
    sarebbe cancellare il lavoro di qualcun altro senza dirlo.
    """
    conosciuti = set(PERTINENZE_TOKENS)
    fuori = []
    for pezzo in _spezza(raw):
        if pezzo in conosciuti:
            continue
        if any(t in pezzo for t in ("piscina", "posto moto", "posto bici")):
            continue
        if pezzo not in fuori:
            fuori.append(pezzo)
    return tuple(fuori)


def serialize_pertinenze(tokens: Iterable[str], *,
                         extra: Iterable[str] = ()) -> str:
    """Token scelti (ordine canonico) piu' gli extra conservati, come testo.

    Deterministico: lo stesso insieme da' sempre la stessa stringa, che e'
    cio' che permette al confronto "questa modifica non cambia niente" di
    essere un confronto e non un indovinello.
    """
    scelti = set(tokens)
    ordinati = [t for t in PERTINENZE_TOKENS if t in scelti]
    return PERTINENZE_SEPARATOR.join([*ordinati, *extra])
