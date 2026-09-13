"""Il contratto di risposta della superficie Platform.

Una proiezione esplicita, campo per campo, come `operator_auth.schemas`: una
colonna nuova su `operator_users` o su `operator_sessions` non deve poter
raggiungere un client per il solo fatto di esistere.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    AGENCY_EMPTY_PATCH_MESSAGE,
    AGENCY_LOCALES,
    CONFIGURATION_EMPTY_PATCH_MESSAGE,
    EMPTY_PATCH_MESSAGE,
    MEMBERSHIP_ROLES,
    MEMBERSHIP_STATUSES,
    OPERATOR_DEFAULT_STATUS,
    OPERATOR_EMAIL_MAX,
    OPERATOR_NAME_MAX,
    OPERATOR_STATUSES,
    AGENCY_NAME_MAX,
    AGENCY_SLUG_MAX,
    AGENCY_SLUG_PATTERN,
    AGENCY_STATUS_ACTIVE,
    AGENCY_STATUSES,
)


class PlatformMeResponse(BaseModel):
    """Chi e' il chiamante sulla superficie Platform.

    Quattro campi, e nessuno di essi appartiene a un'agenzia diversa da quella
    del chiamante:

    * `user_id`            - la sua identita'.
    * `is_platform_admin`  - sempre True qui: se fosse False la dipendenza
                             avrebbe gia' risposto 403. Presente lo stesso,
                             perche' un client che lo legge non deve dedurlo
                             dallo status code.
    * `agency_id`          - la SUA membership, se ne ha una (D4 la permette),
                             None se non ne ha. E' lo stesso valore che
                             `/api/operator-auth/me` gli restituisce gia': non
                             e' un dato di tenant e non allarga nulla.
    * `session_expires_at` - la scadenza della sessione.

    Deliberatamente assenti: il nome dell'agenzia, l'email, il ruolo, il numero
    di agenzie della rete, qualunque conteggio. P27-1 non consegna una
    dashboard: consegna la prova che la superficie esiste ed e' chiusa.
    """

    user_id: int | None
    is_platform_admin: bool
    agency_id: int | None
    session_expires_at: datetime


# ---------------------------------------------------------------------------
# P27-2 - GESTIONE AGENZIE
# ---------------------------------------------------------------------------

class PlatformModel(BaseModel):
    """La base dei corpi di richiesta della superficie Platform.

    `extra="forbid"`, come `core.schemas.CoreModel`. Non e' pedanteria: senza,
    un corpo che contiene `id`, `created_at` o `updated_at` verrebbe accettato
    e quei campi ignorati in silenzio, e chi lo ha inviato crederebbe di averli
    impostati. Con il divieto sono un 422 prima che il gestore parta, e le
    colonne di provenienza restano inesprimibili da un client - la stessa
    regola che CORE applica a `agency_id` e `is_platform_admin`.
    """

    model_config = ConfigDict(extra="forbid")


def _validate_name(value: str) -> str:
    """Nome non vuoto una volta tolti gli spazi, entro la lunghezza di colonna.

    Il valore viene restituito GIA' ripulito: `"  Agenzia  "` e' salvato come
    `"Agenzia"`. Il CHECK del database (`BTRIM(name) <> ''`) accetterebbe gli
    spazi ai lati, quindi senza questo passaggio due agenzie potrebbero
    chiamarsi in modo indistinguibile sullo schermo e diverso nel database.

    La lunghezza si misura DOPO il trim, che e' l'unica misura che descrive
    cio' che viene poi scritto.
    """
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("name non puo' essere vuoto")
    if len(cleaned) > AGENCY_NAME_MAX:
        raise ValueError(f"name supera {AGENCY_NAME_MAX} caratteri")
    return cleaned


def _validate_slug(value: str) -> str:
    """Slug entro la lunghezza di colonna e conforme al CHECK della 027.

    Non viene ne' ripulito ne' abbassato di maiuscole. Uno slug e' un
    identificatore che finira' in un URL e in una configurazione: trasformarlo
    in silenzio significherebbe che il chiamante ne ha chiesto uno e ne ha
    ottenuto un altro - e, con due richieste diverse che normalizzano allo
    stesso valore, un conflitto che nessuno dei due si aspetta. `"Alba"` e'
    quindi un 422 che dice cosa non va, non una correzione tacita in `"alba"`.
    """
    if len(value) > AGENCY_SLUG_MAX:
        raise ValueError(f"slug supera {AGENCY_SLUG_MAX} caratteri")
    if not re.fullmatch(AGENCY_SLUG_PATTERN, value):
        raise ValueError(
            "slug ammette solo minuscole, cifre e trattini interni "
            f"(regola: {AGENCY_SLUG_PATTERN})"
        )
    return value


def _validate_status(value: str) -> str:
    if value not in AGENCY_STATUSES:
        raise ValueError(f"status deve essere uno fra {list(AGENCY_STATUSES)}")
    return value


# ---------------------------------------------------------------------------
# P27-4 - CONFIGURAZIONE AGENZIA
#
# Definita QUI, prima di `AgencyCreateRequest`, e non in fondo al file dove il
# resto di P27-4 sarebbe finito naturalmente: quel modello la usa come tipo del
# campo `settings`, e un'annotazione a un nome definito piu' sotto resta un
# `ForwardRef` finche' qualcuno non la risolve. Pydantic la risolve al primo
# uso, quindi la validazione funziona - ma `model_fields[...].annotation`
# resterebbe un riferimento non risolto, e in un ordine di import diverso lo
# resterebbe anche a runtime. Definirla prima toglie il problema invece di
# rattopparlo con un `model_rebuild()`.
# ---------------------------------------------------------------------------

def _validate_timezone(value: str) -> str:
    """Un identificatore IANA che `zoneinfo` sa risolvere davvero.

    Nessun elenco mantenuto a mano: il database dei fusi cambia piu' volte
    l'anno - l'Egitto ha reintrodotto l'ora legale nel 2023, il Messico l'ha
    abolita nel 2022 - e una lista nel codice sarebbe sbagliata entro pochi
    mesi senza che nessuno se ne accorga. `ZoneInfo` interroga il database di
    sistema, che si aggiorna con il sistema.

    `ZoneInfoNotFoundError` e' la risposta per un fuso inesistente, `ValueError`
    per una chiave malformata (percorso assoluto, risalita di directory): sono
    entrambe richieste sbagliate e diventano entrambe un 422 che nomina il
    campo.
    """
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise ValueError(
            f"timezone {value!r} non e' un identificatore IANA valido"
        ) from exc
    return value


def _validate_locale(value: str) -> str:
    if value not in AGENCY_LOCALES:
        raise ValueError(f"locale deve essere uno fra {list(AGENCY_LOCALES)}")
    return value


class AgencyConfigurationResponse(BaseModel):
    """La configurazione di un'agenzia, con i default gia' applicati.

    Ogni campo e' sempre presente: chi legge non deve sapere se la riga nel
    database contenga `{}`, una configurazione parziale o una completa. E' cio'
    che permette di non fare un backfill - la 027 ha messo `DEFAULT '{}'` su
    ogni agenzia, e i default vivono nell'applicazione.

    Eventuali chiavi legacy sconosciute presenti nel JSONB non compaiono qui:
    la risposta e' il contratto, non un'eco della riga.
    """

    timezone: str
    locale: str


class AgencyConfigurationInput(PlatformModel):
    """La configurazione come la si puo' SCRIVERE. Campi facoltativi.

    Usato in due posti, ed e' il motivo per cui esiste invece di due modelli
    quasi uguali:

    * il corpo della PATCH su `/configuration`;
    * il campo `settings` della POST che crea un'agenzia (P27-2).

    Due modelli avrebbero significato due idee di cosa sia una configurazione
    valida, e la piu' permissiva delle due sarebbe diventata la vera - cioe' la
    strada per scrivere in `settings` qualunque cosa.
    """

    timezone: str | None = None
    locale: str | None = None

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value):
        return value if value is None else _validate_timezone(value)

    @field_validator("locale")
    @classmethod
    def _check_locale(cls, value):
        return value if value is None else _validate_locale(value)

    @model_validator(mode="after")
    def _reject_null_fields(self):
        nulls = sorted(
            name for name in self.model_fields_set if getattr(self, name) is None
        )
        if nulls:
            raise ValueError(f"questi campi non ammettono null: {nulls}")
        return self

    def supplied(self) -> dict[str, Any]:
        """I soli campi indicati, con i loro valori.

        E' quello che finisce nel merge, e da cui si ricavano i nomi per
        `metadata.changed_fields`. I valori restano qui e non entrano mai nel
        registro.
        """
        return {name: getattr(self, name) for name in sorted(self.model_fields_set)}


class AgencyConfigurationUpdateRequest(AgencyConfigurationInput):
    """Il corpo della PATCH su `/configuration`.

    Identico a `AgencyConfigurationInput` tranne che per una cosa: un corpo
    vuoto e' un 422. Su una POST `settings: {}` significa legittimamente "nessuna
    configurazione, usa i default"; su una PATCH significa una richiesta che
    non dice cosa fare, e rispondere 200 con la riga invariata la farebbe
    sembrare applicata.
    """

    @model_validator(mode="after")
    def _reject_empty(self):
        if not self.model_fields_set:
            raise ValueError(CONFIGURATION_EMPTY_PATCH_MESSAGE)
        return self


# ---------------------------------------------------------------------------
# P27-2 - AGENZIE (continua)
# ---------------------------------------------------------------------------


class AgencyResponse(BaseModel):
    """Una agenzia, proiettata campo per campo.

    Sette campi, gli stessi che `agencies_repository.AGENCY_COLUMNS` legge.
    Non e' una eco della riga: una colonna aggiunta ad `agencies` da una fase
    successiva - P27-4 lavorera' su `settings`, P27-5 sui territori - non deve
    poter raggiungere un client per il solo fatto di esistere.
    """

    id: int
    name: str
    slug: str
    status: str
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AgencyCreateRequest(PlatformModel):
    """Il corpo di POST /api/platform/agencies.

    `name` e `slug` obbligatori; `status` e `settings` con un valore
    predefinito. I predefiniti sono gli stessi della colonna in 027
    (`'active'` e `'{}'`), scritti qui e non lasciati al database perche'
    l'INSERT nomina sempre tutte e quattro le colonne: cosi' il valore che
    finisce nella riga e' quello che questo schema dichiara, e non dipende da
    quale dei due posti venga letto per primo.
    """

    name: str
    slug: str
    status: str = AGENCY_STATUS_ACTIVE
    # P27-4: era `dict[str, Any]`, cioe' qualunque cosa. Adesso e' il modello
    # della configurazione, lo STESSO che valida la PATCH su `/configuration`.
    # Due strade verso lo stesso JSONB, una validata e una libera, significano
    # che quella libera e' il contratto vero.
    #
    # Omettendolo si scrive `{}` e i default si applicano in lettura: non si
    # scrivono nella riga, per non avere due sorgenti di verita' su cosa
    # significhi "non configurato".
    settings: AgencyConfigurationInput = Field(
        default_factory=lambda: AgencyConfigurationInput()
    )

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _validate_name(value)

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, value: str) -> str:
        return _validate_slug(value)

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str) -> str:
        return _validate_status(value)

    def created_fields(self) -> list[str]:
        """I campi che il CHIAMANTE ha indicato, non tutti e quattro.

        E' cio' che finisce in `metadata.created_fields`. L'elenco completo
        sarebbe identico su ogni riga del registro, quindi non direbbe nulla;
        sapere che `status` era esplicito distingue un'agenzia aperta gia'
        sospesa da una aperta con il valore predefinito. Solo nomi, mai valori.
        """
        return sorted(self.model_fields_set)


class AgencyUpdateRequest(PlatformModel):
    """Il corpo di PATCH /api/platform/agencies/{id}.

    Due campi, tutti facoltativi, e vengono aggiornati SOLO quelli presenti:
    e' cosa distingue una PATCH da una PUT, e il motivo per cui
    `model_fields_set` - non il valore dei campi - decide cosa viene scritto.

    `settings` NON E' PIU' QUI, ED E' LA CHIUSURA DI UNA SUPERFICIE LIBERA.

    P27-2 lo accettava come `dict[str, Any]`: qualunque struttura, nessuna
    validazione, direttamente nel JSONB. P27-4 da' un contratto a quella
    colonna, e lasciare aperta anche questa strada avrebbe significato che il
    contratto valeva solo per chi sceglieva di rispettarlo. La configurazione
    si aggiorna da `PATCH /agencies/{id}/configuration`, dove ogni campo e'
    dichiarato e validato.

    `slug` NON E' FRA QUESTI, ED E' LA RAGIONE PER CUI SONO TRE E NON QUATTRO.

    La migration 027 lo definisce come chiave stabile e ne vincola la forma
    perche' viene RISOLTO: il funnel pubblico trova la Default Agency per slug,
    e il backfill della 029 ci ha attaccato ogni record legacy. Rinominarlo da
    qui romperebbe quei lookup lasciando la riga intatta - un guasto che non si
    vede finche' un'estimazione pubblica non finisce nell'agenzia sbagliata.

    Non essendo dichiarato, `extra="forbid"` fa il resto: una PATCH che
    contiene `slug` e' un 422 prima che il gestore parta, con il nome del campo
    nel messaggio. E' il rifiuto giusto - la richiesta chiede qualcosa che
    questa superficie non offre - e non un 200 che ignora il campo in silenzio.

    DUE RIFIUTI CHE POTREBBERO SEMBRARE ECCESSIVI E NON LO SONO

    * Un corpo `{}` e' un 422. Non e' un aggiornamento vuoto riuscito: e' una
      richiesta che non dice cosa fare, e rispondere 200 con la riga invariata
      la farebbe sembrare applicata. Una PATCH che non cambia nulla e ottiene
      200 e' esattamente il modo in cui un errore di un client resta invisibile.
    * Un campo esplicitamente `null` e' un 422. Nessuna delle quattro colonne
      e' annullabile: `name`, `slug` e `status` sono tutti NOT NULL. Accettare
      `{"status": null}` significherebbe scrivere NULL e farsi rifiutare dal
      database con un 500 - o, peggio, lasciare intendere che ci sia un modo di
      svuotare un campo.
    """

    name: str | None = None
    status: str | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value):
        return value if value is None else _validate_name(value)

    @field_validator("status")
    @classmethod
    def _check_status(cls, value):
        return value if value is None else _validate_status(value)

    @model_validator(mode="after")
    def _reject_empty_and_null(self):
        provided = self.model_fields_set
        if not provided:
            raise ValueError(AGENCY_EMPTY_PATCH_MESSAGE)
        nulls = sorted(name for name in provided if getattr(self, name) is None)
        if nulls:
            raise ValueError(
                f"questi campi non ammettono null: {nulls}"
            )
        return self

    def changed_fields(self) -> dict[str, Any]:
        """I soli campi presenti nella richiesta, con i loro valori.

        E' cio' che il service passa al repository, ed e' anche da cui si
        ricavano i nomi per `metadata.changed_fields`. I valori restano qui e
        non entrano mai nel registro.
        """
        return {name: getattr(self, name) for name in sorted(self.model_fields_set)}


# ---------------------------------------------------------------------------
# P27-3 - OPERATORI, TITOLARI E RUOLI
# ---------------------------------------------------------------------------

def _validate_optional_name(value):
    """Nome o cognome: possono mancare, non possono essere spazi.

    `None` e' legittimo - la 027 li dichiara nullable - ma `"   "` no: sarebbe
    un valore che sembra esserci e non c'e'. Viene restituito ripulito.
    """
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > OPERATOR_NAME_MAX:
        raise ValueError(f"supera {OPERATOR_NAME_MAX} caratteri")
    return cleaned


class OperatorResponse(BaseModel):
    """L'IDENTITA' di un operatore. Nove campi, e `password_hash` non c'e'.

    Non e' un'omissione da correggere: e' la ragione per cui questa classe
    esiste invece di restituire la riga. La stessa disciplina vale per il token
    di sessione e per il suo hash, che non sono su questa tabella e non devono
    arrivarci per nessuna strada.
    """

    id: int
    email: str
    first_name: str | None
    last_name: str | None
    status: str
    is_platform_admin: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MembershipResponse(BaseModel):
    """L'APPARTENENZA a un'agenzia. Separata dall'identita', di proposito.

    Una persona e' una cosa, il suo rapporto con un'agenzia un'altra: la stessa
    identita' puo' avere una membership attiva qui e due revocate altrove, e
    appiattirle in un oggetto solo renderebbe impossibile dire di quale
    agenzia si stia parlando.
    """

    id: int
    agency_id: int
    operator_user_id: int
    role: str
    status: str
    created_at: datetime
    updated_at: datetime


class AgencyOperatorResponse(BaseModel):
    """Una persona e la sua membership IN QUELLA agenzia."""

    operator: OperatorResponse
    membership: MembershipResponse


class OperatorDetailResponse(BaseModel):
    """Una persona e TUTTE le sue membership, in ogni agenzia e in ogni stato.

    Comprese le revocate: sono la sua storia nella rete, ed e' esattamente
    quello che un amministratore guarda prima di riassegnarla.
    """

    operator: OperatorResponse
    memberships: list[MembershipResponse]


class OwnerTransferResponse(BaseModel):
    """L'esito di un trasferimento di titolarita'.

    `demoted` e' la membership di chi era titolare prima, degradata ad
    `agency_admin`, oppure None se l'agenzia non ne aveva uno. Restituirla e'
    cio' che rende visibile l'altra meta' dell'operazione: un trasferimento
    cambia DUE righe, e una risposta che ne mostrasse una sola lascerebbe
    credere che l'altra persona sia rimasta titolare.
    """

    membership: MembershipResponse
    demoted: MembershipResponse | None


class OperatorCreateRequest(PlatformModel):
    """Il corpo di POST /api/platform/agencies/{agency_id}/operators.

    LA PASSWORD E' FACOLTATIVA QUI, E OBBLIGATORIA IN UNO DEI DUE CASI.

    Lo schema non puo' deciderlo: se serva o meno dipende da chi sia quella
    email, e lo si sa solo dopo aver interrogato `operator_users`. Il campo e'
    quindi `None` per difetto e il contratto lo fa rispettare il service:

        email nuova       -> la password SERVE (senza, 422). Una persona nuova
                             non puo' esistere senza credenziale:
                             `password_hash` e' NOT NULL con un CHECK sul
                             formato PBKDF2.
        email gia' nota   -> la password NON deve essere inviata (se c'e', 409).
                             Si crea solo la membership, e la credenziale di
                             quella persona non viene toccata. Ignorarla
                             lascerebbe credere di averla impostata; applicarla
                             renderebbe questa route un reimposta-password
                             implicito.

    Non esiste un flusso di invito - nessuna email, nessun token di primo
    accesso, nessuna pagina di scelta password - quindi la strada coerente con
    l'infrastruttura attuale e' quella che gli script TEST gia' usano: il
    chiamante fornisce una password e il server la trasforma con
    `operator_auth.security.hash_password`, la stessa funzione del login.

    NESSUN VINCOLO DI LUNGHEZZA. Una prima stesura ne aveva due; sono stati
    tolti perche' `operator_auth` non ne ha nessuno e imporli qui avrebbe
    voluto dire decidere la politica password del prodotto da dentro questa
    fase. Vedi il commento in enums.py: il gap e' dichiarato come rischio.

    La password non viene loggata, non entra nell'audit, non torna nella
    risposta e non e' aggiornabile da questa superficie.

    `role` e' obbligatorio: assegnare un ruolo predefinito significherebbe
    sceglierlo per chi apre l'agenzia, ed e' la cosa piu' importante della
    richiesta.
    """

    email: str = Field(max_length=OPERATOR_EMAIL_MAX)
    password: str | None = None
    role: str
    first_name: str | None = None
    last_name: str | None = None
    status: str = OPERATOR_DEFAULT_STATUS

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or "@" not in cleaned:
            raise ValueError("email non valida")
        return cleaned

    @field_validator("role")
    @classmethod
    def _check_role(cls, value: str) -> str:
        if value not in MEMBERSHIP_ROLES:
            raise ValueError(f"role deve essere uno fra {list(MEMBERSHIP_ROLES)}")
        return value

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str) -> str:
        if value not in OPERATOR_STATUSES:
            raise ValueError(f"status deve essere uno fra {list(OPERATOR_STATUSES)}")
        return value

    @field_validator("first_name", "last_name")
    @classmethod
    def _check_names(cls, value):
        return _validate_optional_name(value)

    def supplies_password(self) -> bool:
        """True quando il chiamante ha davvero mandato una credenziale.

        `None` e campo assente sono la stessa cosa - `{"password": null}` e'
        un modo di non mandarla - cosi' la regola e' una sola e non dipende da
        quale delle due forme il client abbia scelto.
        """
        return self.password is not None

    def created_fields(self) -> list[str]:
        """I nomi dei campi indicati dal chiamante. MAI `password`.

        L'esclusione e' esplicita e non affidata al fatto che il campo si
        chiami cosi': e' l'unico valore di questa richiesta che non deve poter
        comparire nemmeno come NOME in una tabella append-only, perche' la sua
        presenza nell'elenco direbbe comunque qualcosa su come e' stato creato
        quel conto.
        """
        return sorted(self.model_fields_set - {"password"})


class OperatorUpdateRequest(PlatformModel):
    """Il corpo di PATCH /api/platform/operators/{operator_user_id}.

    Tre campi. `email` non c'e' perche' e' la chiave di identita' globale su
    cui il login risolve la persona - stessa ragione per cui P27-2 ha reso
    immutabile lo slug dell'agenzia. `is_platform_admin` non c'e' perche'
    concedere l'amministrazione della rete da una route che si chiama
    "aggiorna operatore" sarebbe un'escalation nascosta in una modifica di
    routine. `password` non c'e' perche' cambiarla e' un'operazione di
    credenziale, non anagrafica.

    Con `extra="forbid"`, ognuno dei tre e' un 422 che nomina il campo.
    """

    first_name: str | None = None
    last_name: str | None = None
    status: str | None = None

    @field_validator("status")
    @classmethod
    def _check_status(cls, value):
        if value is None:
            return None
        if value not in OPERATOR_STATUSES:
            raise ValueError(f"status deve essere uno fra {list(OPERATOR_STATUSES)}")
        return value

    @field_validator("first_name", "last_name")
    @classmethod
    def _check_names(cls, value):
        return _validate_optional_name(value)

    @model_validator(mode="after")
    def _reject_empty(self):
        if not self.model_fields_set:
            raise ValueError(EMPTY_PATCH_MESSAGE)
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status non ammette null")
        return self

    def changed_fields(self) -> dict[str, Any]:
        """I soli campi presenti. `first_name`/`last_name` ammettono null:
        sono nullable nello schema, e azzerarli e' un'operazione legittima."""
        return {name: getattr(self, name) for name in sorted(self.model_fields_set)}


class MembershipUpdateRequest(PlatformModel):
    """Il corpo della PATCH sulla membership.

    `role='agency_owner'` passa la validazione e viene rifiutato dal service
    con 409, non qui con 422. La distinzione e' voluta: il ruolo esiste e la
    richiesta e' ben formata - cio' che non va e' lo STATO del sistema, perche'
    assegnare quel ruolo richiede di degradare qualcun altro. E' un conflitto,
    e il 409 rimanda all'operazione che fa entrambe le cose insieme.
    """

    role: str | None = None
    status: str | None = None

    @field_validator("role")
    @classmethod
    def _check_role(cls, value):
        if value is None:
            return None
        if value not in MEMBERSHIP_ROLES:
            raise ValueError(f"role deve essere uno fra {list(MEMBERSHIP_ROLES)}")
        return value

    @field_validator("status")
    @classmethod
    def _check_status(cls, value):
        if value is None:
            return None
        if value not in MEMBERSHIP_STATUSES:
            raise ValueError(
                f"status deve essere uno fra {list(MEMBERSHIP_STATUSES)}"
            )
        return value

    @model_validator(mode="after")
    def _reject_empty_and_null(self):
        provided = self.model_fields_set
        if not provided:
            raise ValueError(EMPTY_PATCH_MESSAGE)
        nulls = sorted(name for name in provided if getattr(self, name) is None)
        if nulls:
            raise ValueError(f"questi campi non ammettono null: {nulls}")
        return self

    def changed_fields(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in sorted(self.model_fields_set)}


class OwnerTransferRequest(PlatformModel):
    """Chi diventa titolare. Un campo, e nessun altro.

    Niente ruolo da assegnare a chi lascia, niente stato: entrambe le cose
    sono decise dall'operazione e non dal chiamante, perche' sono cio' che
    rende il trasferimento una cosa sola invece di due modifiche coordinate a
    mano.
    """

    operator_user_id: int
