"""Il contratto di risposta della superficie Platform.

Una proiezione esplicita, campo per campo, come `operator_auth.schemas`: una
colonna nuova su `operator_users` o su `operator_sessions` non deve poter
raggiungere un client per il solo fatto di esistere.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    AGENCY_EMPTY_PATCH_MESSAGE,
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
    settings: dict[str, Any] = Field(default_factory=dict)

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

    Tre campi, tutti facoltativi, e vengono aggiornati SOLO quelli presenti:
    e' cosa distingue una PATCH da una PUT, e il motivo per cui
    `model_fields_set` - non il valore dei campi - decide cosa viene scritto.

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
      e' annullabile: `name`, `slug` e `status` sono NOT NULL e `settings` ha
      NOT NULL DEFAULT '{}'. Accettare `{"settings": null}` significherebbe
      scrivere NULL e farsi rifiutare dal database con un 500 - o, peggio,
      lasciare intendere che ci sia un modo di svuotare un campo. Per svuotare
      `settings` si scrive `{}`.
    """

    name: str | None = None
    status: str | None = None
    settings: dict[str, Any] | None = None

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
