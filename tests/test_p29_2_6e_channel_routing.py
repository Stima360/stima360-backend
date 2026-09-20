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
        # SENTINELLA AGGIORNATA DA P29-3B.2A: la parola "registry" e' ammessa
        # SOLO in templates.py (registry dei template, non dei provider) e nel
        # journey_service che lo consulta. Il registry canale -> adapter resta
        # vietato ovunque.
        vietati = ("PROVIDERS = {", "PROVIDER_REGISTRY", "whatsapp_meta")
        if percorso.name not in ("templates.py", "journey_service.py"):
            vietati = vietati + ("registry",)
        for vietato in vietati:
            assert vietato not in corpo, f"{percorso.name} nomina {vietato}"


# ---------------------------------------------------------------------------
# P29-2.6E OPS - il provider finto non deve poter servire una rotta viva
# ---------------------------------------------------------------------------
#
# Il difetto che queste sentinelle chiudono e' stato trovato eseguendo, non
# leggendo: la rotta montata chiamava `dispatch_batch` senza `provider`, quindi
# cadeva sul default - il provider FINTO - e restituiva `sent=1` senza che una
# sola email partisse. Nessun errore, nessun log, un ledger che dice di aver
# mandato.

def test_11_la_rotta_risolve_sempre_il_provider_dal_canale():
    from communication import router as router_comunicazione

    corpo = inspect.getsource(router_comunicazione.dispatch)
    assert "provider=dispatcher.adapter_per(payload.channel)" in corpo, (
        "la rotta non risolve il trasporto dal canale: cadrebbe sul provider "
        "finto e direbbe `sent` senza aver mandato niente"
    )


def test_12_adapter_per_non_conosce_il_provider_finto():
    """La mappa porta SOLO trasporti reali.

    Se `null` finisse li' dentro, un canale servito dal provider finto sarebbe
    indistinguibile da uno servito davvero - e la rotta e' viva.
    """
    from communication.providers import null as provider_finto

    assert dispatcher.ADAPTER_PER_CANALE, "la mappa e' vuota: nessun canale parte"
    assert provider_finto not in dispatcher.ADAPTER_PER_CANALE.values()
    for canale, adapter in dispatcher.ADAPTER_PER_CANALE.items():
        assert canale in CHANNELS, canale
        assert adapter.NAME != provider_finto.NAME


def test_13_un_canale_senza_trasporto_reale_viene_rifiutato():
    """E non servito dal finto. `whatsapp` e' il caso di oggi: P29-2.5W e'
    deferita e R3 e' OPEN."""
    from communication.exceptions import ValidationError

    assert "whatsapp" not in dispatcher.ADAPTER_PER_CANALE
    with pytest.raises(ValidationError) as exc:
        dispatcher.adapter_per("whatsapp")
    assert "no real transport" in str(exc.value)


def test_14_nessun_caller_applicativo_vivo_usa_il_provider_finto():
    """La sentinella che generalizza il difetto.

    Si guardano TUTTI i sorgenti applicativi - non solo la rotta - e si esige
    che ogni chiamata a `dispatch_batch` porti un `provider` esplicito. Il
    default resta, perche' i test di P29-2.4 provano il percorso senza mandare
    niente, ma nessun percorso VIVO puo' caderci sopra.
    """
    sorgenti = [p for p in ROOT.rglob("*.py")
                if "__pycache__" not in p.parts
                and "tests" not in p.parts
                and not p.name.startswith("run_")]
    chiamanti = []
    for percorso in sorgenti:
        corpo = codice(percorso)
        for chiamata in re.finditer(r"(?<!def )dispatch_batch\(([^)]*)\)", corpo,
                                    re.DOTALL):
            chiamanti.append((percorso.name, chiamata.group(1)))
            assert "provider=" in chiamata.group(1), (
                f"{percorso.name}: dispatch_batch senza provider esplicito - "
                "cadrebbe sul provider finto"
            )
    assert chiamanti, "nessun chiamante applicativo trovato: la rotta e' sparita?"


def test_15_il_provider_finto_resta_solo_una_utility_di_prova():
    """Fuori dal dominio e dai test, `null` non e' importato da nessuno."""
    for percorso in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in percorso.parts or "tests" in percorso.parts:
            continue
        if percorso.parent.name == "providers" or percorso.name == "dispatcher.py":
            continue
        corpo = codice(percorso)
        assert "providers import null" not in corpo, percorso.name
        assert "provider_finto" not in corpo, percorso.name
