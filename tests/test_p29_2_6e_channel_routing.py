"""P29-2.6E - il canale e' obbligatorio, e filtra PRIMA del lock.

IL BUCO CHE QUESTO MODULO CHIUDE

Fino a P29-2.5E `claim_due` non guardava il canale. Un worker email avrebbe
reclamato anche un messaggio WhatsApp e lo avrebbe dato a `email_smtp.send`,
dove `subject_snapshot` e' NULL per costruzione - ma il danno vero sarebbe
arrivato prima: il messaggio sarebbe gia' stato portato a `sending`, con un
token e un tentativo aperto, e nessuna uscita da quello stato sarebbe stata
onesta.

LA PARTE DI COMPORTAMENTO STA SU POSTGRESQL

Qui vive cio' che si prova leggendo: che il parametro esista, sia obbligatorio,
non abbia default e sia validato. Che il messaggio dell'altro canale resti
davvero intatto si prova su un database vero, in
`tests/test_p29_2_6e_channel_postgres.py`.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from communication import dispatcher, repository, service
from communication.enums import CHANNELS
from communication.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
PACCHETTO = ROOT / "communication"


def codice(percorso: Path) -> str:
    testo = percorso.read_text(encoding="utf-8")
    testo = re.sub(r'"{3}[\s\S]*?"{3}', "", testo)
    return re.sub(r"#[^\n]*", "", testo)


# ---------------------------------------------------------------------------
# Il parametro esiste, e' obbligatorio, e non ha un default
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("funzione, atteso", [
    (repository.claim_due, {"cur", "ctx", "limit", "provider", "channel", "token_factory"}),
    (service.claim_due, {"ctx", "provider", "channel", "limit", "cur", "token_factory"}),
    (dispatcher.dispatch_batch, {"ctx_operatore", "channel", "limit", "provider"}),
])
def test_1_channel_e_nella_firma_delle_tre_funzioni(funzione, atteso):
    assert set(inspect.signature(funzione).parameters) == atteso


@pytest.mark.parametrize("funzione", [repository.claim_due, service.claim_due,
                                      dispatcher.dispatch_batch])
def test_2_channel_e_keyword_only_e_senza_default(funzione):
    """SENZA DEFAULT, e non e' un dettaglio.

    `channel=None` che significa "tutti" sarebbe la porta da cui un worker
    futuro torna per distrazione a reclamare messaggi che non sa mandare. Chi
    volesse davvero tutti i canali deve scriverlo a mano, canale per canale.
    """
    parametro = inspect.signature(funzione).parameters["channel"]
    assert parametro.kind is inspect.Parameter.KEYWORD_ONLY
    assert parametro.default is inspect.Parameter.empty, "channel ha un default"


@pytest.mark.parametrize("funzione", [repository.claim_due, service.claim_due,
                                      dispatcher.dispatch_batch])
def test_3_chiamare_senza_channel_e_un_TypeError(funzione):
    with pytest.raises(TypeError):
        funzione(None, provider="x", limit=1)


def test_4_il_canale_e_validato_contro_linsieme_chiuso():
    class Ctx:
        def require_agency(self):
            return 1

    for cattivo in ("sms", "", "  ", "EMAIL", None, 7):
        with pytest.raises(ValidationError):
            service.claim_due(Ctx(), provider="p", channel=cattivo, cur=object())


def test_5_i_canali_ammessi_sono_quelli_della_064():
    assert CHANNELS == frozenset({"email", "whatsapp"})


# ---------------------------------------------------------------------------
# Il filtro sta PRIMA del lock
# ---------------------------------------------------------------------------

def test_6_il_predicato_precede_for_update_skip_locked():
    """Letto sull'SQL reale: `m.channel` deve comparire prima di
    `FOR UPDATE SKIP LOCKED`, altrimenti il lock verrebbe preso anche sulle
    righe dell'altro canale - che e' meta' del danno che si vuole evitare."""
    testo = (PACCHETTO / "repository.py").read_text(encoding="utf-8")
    sql = re.findall(r"cur\.execute\(\s*f?\"{3}(.*?)\"{3}", testo, re.DOTALL)
    candidati = [q for q in sql if "FOR UPDATE SKIP LOCKED" in q]
    assert len(candidati) == 1, "la SELECT dei candidati non e' piu' una sola"
    query = candidati[0]
    assert "m.channel = %s" in query, "il claim non filtra per canale"
    assert query.index("m.channel") < query.index("FOR UPDATE SKIP LOCKED")
    assert query.index("m.channel") < query.index("LIMIT")


def test_7_il_dispatcher_passa_il_proprio_canale_al_claim():
    corpo = codice(PACCHETTO / "dispatcher.py")
    assert "channel=channel" in corpo, (
        "il dispatcher non inoltra il canale: reclamerebbe un canale diverso da "
        "quello che il suo adapter sa mandare"
    )


def test_8_nessun_caller_del_dominio_reclama_senza_canale():
    """Sentinella: dentro `communication/` non deve esistere una chiamata a
    `claim_due` priva di `channel`."""
    for percorso in sorted(PACCHETTO.rglob("*.py")):
        if "__pycache__" in percorso.parts:
            continue
        corpo = codice(percorso)
        # `(?<!def )` esclude le DEFINIZIONI: li' `channel` e' un parametro
        # dichiarato, non un argomento passato, e cio' che si sorveglia qui
        # sono le CHIAMATE.
        for chiamata in re.finditer(r"(?<!def )claim_due\(([^)]*)\)", corpo, re.DOTALL):
            argomenti = chiamata.group(1)
            assert "channel=" in argomenti, (
                f"{percorso.name}: claim_due chiamata senza channel"
            )


def test_9_il_corpo_della_rotta_dichiara_il_canale():
    from communication.schemas import DispatchRequest

    campo = DispatchRequest.model_fields["channel"]
    assert campo.is_required(), "channel ha un default nello schema"
    with pytest.raises(Exception):
        DispatchRequest(channel="sms", limit=5)
    assert DispatchRequest(channel="whatsapp").channel == "whatsapp"


def test_10_nessun_provider_registry_e_stato_anticipato():
    """P29-2.6E instrada il CLAIM, non i provider. Un registry canale -> adapter
    appartiene alla fase in cui esistono due adapter, e P29-2.5W e' bloccata da
    R3."""
    for percorso in sorted(PACCHETTO.rglob("*.py")):
        if "__pycache__" in percorso.parts:
            continue
        corpo = codice(percorso)
        for vietato in ("PROVIDERS = {", "PROVIDER_REGISTRY", "registry",
                        "whatsapp_meta"):
            assert vietato not in corpo, f"{percorso.name} nomina {vietato}"
