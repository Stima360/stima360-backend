"""La configurazione di un'agenzia. P27-4.

`agencies.settings` esiste dalla 027 come JSONB libero, e la 027 stessa lo
dichiarava un contenitore in attesa: "settings holds non-sensitive display
configuration only. P26-1 defines no key in it and reads it nowhere". L'audit
lo conferma - nessun modulo legge una chiave la' dentro.

P27-4 non aggiunge una colonna, da' un CONTRATTO a quella che c'e'. Il che
significa tre cose, e la terza e' quella che si dimentica:

1. cio' che si scrive e' validato (`AgencyConfigurationInput`);
2. cio' che si legge ha sempre tutti i campi, perche' i default vivono
   nell'applicazione e non nel database;
3. cio' che era gia' dentro e non e' del contratto NON viene distrutto.

IL TERZO PUNTO, PER ESTESO

Oggi nessuna riga contiene chiavi sconosciute: la 027 ha messo `DEFAULT '{}'`
e nessun codice ha mai scritto altro. Ma il contenitore e' stato libero per
tutta la durata di P26 e P27-2, e uno script, una fixture o una mano umana
possono averci lasciato qualcosa in un ambiente che non e' questo.

La regola e' quindi: le chiavi sconosciute non si espongono - la risposta e'
il contratto, non un'eco della riga - e non si cancellano. Una PATCH che
sostituisse l'intero JSON le porterebbe via in silenzio, e in una colonna che
nessuno guarda sarebbe una perdita che nessuno nota.

I DEFAULT NON SI SCRIVONO

Una riga con `{}` legge `Europe/Rome` e `it-IT` senza che nessuno glieli abbia
messi dentro, e senza backfill. Scriverli sarebbe creare una seconda sorgente
di verita' su cosa significhi "non configurato": con i default in due posti,
cambiarne uno lascerebbe le agenzie vecchie sul valore vecchio e le nuove su
quello nuovo, che e' il tipo di differenza che si scopre da un comportamento
sbagliato e non da un errore.

UN SOLO PERCORSO VERSO IL JSONB

P27-2 accettava `settings` come `dict[str, Any]` sia nella POST sia nella
PATCH generica. P27-4 chiude quella superficie: la PATCH generica non conosce
piu' il campo, e la POST lo accetta solo attraverso lo stesso modello validato
che usa questa fase. Due strade verso la stessa colonna, una validata e una
libera, significano che quella libera e' il contratto vero.
"""
from __future__ import annotations

from typing import Any

from operator_auth.context import OperatorContext

from . import agencies_repository
from .database import platform_operation_cursor
from .enums import (
    ACTION_AGENCY_CONFIGURATION_UPDATE,
    AGENCY_NOT_FOUND_MESSAGE,
    CONFIGURATION_DEFAULTS,
    CONFIGURATION_FIELDS,
    TARGET_TYPE_AGENCY,
)
from .exceptions import AgencyConfigurationCorrupted, AgencyNotFound
from .schemas import AgencyConfigurationInput
from .transaction import audit_then_commit


def _projected(stored: dict[str, Any] | None) -> dict[str, Any]:
    """La configurazione come la si legge: sempre completa, mai di piu'.

    Tre regole, e la terza e' quella che una prima stesura aveva sbagliato:

    * CHIAVE MANCANTE -> default applicativo. La 027 ha messo `DEFAULT '{}'`
      su ogni agenzia, quindi "non configurato" e' la norma e non un'anomalia.
    * CHIAVE SCONOSCIUTA -> resta fuori dalla risposta. Il contratto e' questo,
      non un'eco della riga.
    * CHIAVE NOTA CON VALORE FUORI CONTRATTO -> la configurazione persistita e'
      CORROTTA, e si solleva.

    PERCHE' NON IL DEFAULT SUL TERZO CASO

    Era la prima scelta, con un argomento che sembrava buono: sollevare qui
    rende 500 anche la GET, e la PATCH legge prima di scrivere, quindi
    sembrerebbe non esserci via d'uscita. L'argomento e' sbagliato in un punto
    - la PATCH che sostituisce il campo corrotto funziona benissimo, perche'
    il valore corrotto viene rimpiazzato prima della validazione (vedi
    `update_configuration`) - e il prezzo era troppo alto: rispondere
    `Europe/Rome` mentre nel database c'e' `Mars/Olympus` significa che
    l'agenzia lavora su un fuso e la riga ne dice un altro, per sempre e senza
    che nessuno se ne accorga.

    Un errore rumoroso si nota e si ripara. Un valore inventato no.
    """
    stored = stored or {}
    configuration = dict(CONFIGURATION_DEFAULTS)
    corrupted = []
    for field in CONFIGURATION_FIELDS:
        if field not in stored:
            continue
        try:
            AgencyConfigurationInput(**{field: stored[field]})
        except Exception:
            corrupted.append(field)
            continue
        configuration[field] = stored[field]

    if corrupted:
        # Il nome dei campi resta nell'eccezione, per il log del server: e'
        # cio' che permette a un amministratore di trovare la riga. Non
        # raggiunge il chiamante - il router risponde con la costante.
        raise AgencyConfigurationCorrupted(
            f"configurazione persistita non valida nei campi {sorted(corrupted)}"
        )
    return configuration


def get_configuration(agency_id: int) -> dict[str, Any]:
    with platform_operation_cursor() as (_, cur):
        agency = agencies_repository.get_agency(cur, agency_id)
    if agency is None:
        raise AgencyNotFound(AGENCY_NOT_FOUND_MESSAGE)
    return _projected(agency["settings"])


def update_configuration(
    actor: OperatorContext, agency_id: int, supplied: dict[str, Any]
) -> dict[str, Any]:
    """Scrive i soli campi indicati, preservando tutto il resto della riga.

    MERGE, NON SOSTITUZIONE. Il nuovo JSONB e' la riga com'era piu' i campi
    forniti: `{**stored, **supplied}`. Le chiavi note non indicate restano al
    loro valore, e le sconosciute restano dov'erano.

    LO STATO DELL'AGENZIA NON CONTA. Una agenzia sospesa o archiviata e'
    configurabile dalla Platform come una attiva: e' il momento in cui serve di
    piu', e non allenta nulla sul lato tenant, dove `operator_auth` continua a
    pretendere `agency_status='active'`.

    `changed_fields` elenca i campi SCRITTI, non un diff semantico: indicare un
    valore identico a quello attuale e' una PATCH valida che ha scritto quel
    campo, e l'audit dice cosa l'operazione ha toccato.

    SI VALIDA IL RISULTATO, NON L'INGRESSO

    Il valore che arriva dalla richiesta e' gia' passato dallo schema. Cio' che
    resta da controllare sono le chiavi conosciute che la PATCH NON sostituisce
    e che restano nella riga: se una di quelle e' corrotta, l'operazione si
    ferma senza scrivere nulla.

    E' la regola che rende una PATCH di riparazione possibile e una PATCH
    parziale impossibile, sulla stessa riga corrotta:

        DB {"timezone": "Mars/Olympus", "legacy_x": 123}
        PATCH {"timezone": "Europe/Rome"}  -> riesce: il campo corrotto e'
                                              proprio quello sostituito, e
                                              `legacy_x` sopravvive.
        PATCH {"locale": "it-IT"}          -> fallisce: scriverebbe una riga
                                              che contiene ancora un timezone
                                              illeggibile, e la GET successiva
                                              continuerebbe a dare 500.

    La validazione avviene PRIMA della UPDATE, non dopo: una scrittura poi
    annullata lascerebbe comunque bruciato un valore di sequenza e, soprattutto,
    dipenderebbe dal rollback per una cosa che si puo' semplicemente non fare.
    """
    with platform_operation_cursor() as (conn, cur):
        agency = agencies_repository.get_agency(cur, agency_id)
        if agency is None:
            raise AgencyNotFound(AGENCY_NOT_FOUND_MESSAGE)

        merged = {**(agency["settings"] or {}), **supplied}

        # Solleva `AgencyConfigurationCorrupted` se una chiave nota NON
        # sostituita dalla richiesta e' rimasta fuori contratto.
        _projected(merged)

        updated = agencies_repository.update_agency(
            cur, agency_id, {"settings": merged}
        )
        if updated is None:
            raise AgencyNotFound(AGENCY_NOT_FOUND_MESSAGE)

        audit_then_commit(
            conn,
            actor,
            action=ACTION_AGENCY_CONFIGURATION_UPDATE,
            target_type=TARGET_TYPE_AGENCY,
            target_id=agency_id,
            target_agency_id=agency_id,
            # Solo nomi di campo. Il fuso orario di un affiliato non e' un
            # segreto, ma una tabella append-only non e' il posto in cui far
            # entrare il contenuto di una configurazione per comodita' di
            # lettura - e la regola vale prima che qualcuno ci aggiunga un
            # campo per cui conta.
            metadata={"changed_fields": sorted(supplied)},
        )
        return _projected(updated["settings"])
