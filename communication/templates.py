"""P29-3B.2A - il registro dei template, versionato e immutabile. SOLO STRUTTURA.

`template_key` e `template_version` sul ledger erano due colonne senza un
sistema dietro: chi accodava un messaggio costruiva il testo per conto suo.
Questo modulo e' il posto in cui una coppia (chiave, versione) corrisponde a
UN renderer, e a uno solo.

TRE REGOLE

1. Una versione presente qui non si modifica: si aggiunge la successiva.
   Cio' che il ledger dice di aver mandato con `(m1, 1)` deve restare
   ricostruibile, e una sentinella confronta l'impronta di ogni renderer
   con quella fissata nel test.
2. Una versione assente e' un errore, mai un ripiego su un'altra: un
   messaggio mandato "col template piu' vicino" e' un messaggio che nessuno
   ha approvato.
3. Un template di marketing DEVE ricevere `unsubscribe_url`: senza, il
   rendering e' rifiutato prima di produrre una riga di testo. E' la regola di
   P29-3B.0, applicata dove il testo nasce.

IN QUESTO BLOCCO NON C'E' NESSUN TESTO COMMERCIALE. Il registro contiene un
solo template, di prova, che esiste per dimostrare il contratto e non viene
mai spedito da nessun percorso applicativo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from .exceptions import ValidationError

from .enums import CHANNELS, COMMUNICATION_TYPES, TYPE_MARKETING

#: Il campo che ogni template di marketing deve ricevere. Nominato una volta.
UNSUBSCRIBE_FIELD = "unsubscribe_url"


@dataclass(frozen=True)
class Template:
    key: str
    version: int
    channel: str
    communication_type: str
    required_fields: frozenset[str]
    subject: Callable[[Mapping[str, str]], str] | None
    body: Callable[[Mapping[str, str]], str]

    def __post_init__(self) -> None:
        if self.channel not in CHANNELS:
            raise ValueError(f"template {self.key!r}: unknown channel {self.channel!r}")
        if self.communication_type not in COMMUNICATION_TYPES:
            raise ValueError(f"template {self.key!r}: unknown type {self.communication_type!r}")
        if self.version < 1:
            raise ValueError(f"template {self.key!r}: version must be >= 1")
        if (self.channel == "email") != (self.subject is not None):
            raise ValueError(f"template {self.key!r}: email needs a subject, other channels none")
        if self.communication_type == TYPE_MARKETING and UNSUBSCRIBE_FIELD not in self.required_fields:
            raise ValueError(
                f"template {self.key!r} v{self.version}: a marketing template must require "
                f"{UNSUBSCRIBE_FIELD!r}"
            )


class Rendered(tuple):
    """`(subject, body)`; `subject` e' None fuori dall'email."""


def _prova_soggetto(c: Mapping[str, str]) -> str:
    return f"[prova] {c['agency_name']}"


def _prova_corpo(c: Mapping[str, str]) -> str:
    return (
        f"Questo e' un template di PROVA del registro, per {c['contact_first_name']}.\n"
        f"Non viene spedito da nessun percorso applicativo.\n"
        f"Disiscrizione: {c[UNSUBSCRIBE_FIELD]}\n"
    )


#: IL REGISTRO. Chiuso, immutabile, indicizzato per (chiave, versione).
REGISTRY: Mapping[tuple[str, int], Template] = {
    ("registry_probe", 1): Template(
        key="registry_probe", version=1, channel="email",
        communication_type=TYPE_MARKETING,
        required_fields=frozenset({"contact_first_name", "agency_name", UNSUBSCRIBE_FIELD}),
        subject=_prova_soggetto, body=_prova_corpo,
    ),
}


def get(key: str, version: int) -> Template:
    """La versione chiesta, o un errore. Nessun fallback."""
    try:
        return REGISTRY[(key, int(version))]
    except (KeyError, TypeError, ValueError):
        raise ValidationError(
            f"template {key!r} version {version!r} is not registered; "
            "a message is never rendered with a template nobody approved"
        ) from None


def render(key: str, version: int, context: Mapping[str, str]) -> Rendered:
    """`(subject, body)` dal template, o un errore se manca un campo richiesto."""
    template = get(key, version)
    mancanti = sorted(f for f in template.required_fields
                      if not (context.get(f) or "").strip())
    if mancanti:
        raise ValidationError(
            f"template {key!r} v{version}: missing required fields {mancanti}"
        )
    soggetto = template.subject(context) if template.subject else None
    return Rendered((soggetto, template.body(context)))
