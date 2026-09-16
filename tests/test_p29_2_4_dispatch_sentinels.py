"""P29-2.4 - le tre sentinelle del design, e il confine di fase.

Il design (§12.2) dichiara tre cose che "rendono il gate vero per costruzione".
Sono queste, e sono qui perche' si provano leggendo il codice, non eseguendolo:

    1. `providers/` importabile SOLO da `dispatcher.py`
    2. `contacts.marketing_consent` non leggibile dentro `communication/`
    3. e' `communication_type` a decidere se il gate si applica

Il comportamento - la revoca che sopprime, il compare-and-set, il 422 - sta in
tests/test_p29_2_4_dispatch_postgres.py, che un database vero ce l'ha.

Mappa:

    S   le tre sentinelle del design
    D   D1: l'origin di sistema del dispatcher
    G   il gate: dove sta, e cosa guarda
    P   il provider: finto, e senza rete
    N   cio' che P29-2.4 NON anticipa
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from communication import dispatcher, enums, service
from communication.providers import base as provider_base
from communication.providers import null as provider_null

ROOT = Path(__file__).resolve().parents[1]
PACCHETTO = ROOT / "communication"


def codice(percorso: Path) -> str:
    """Il sorgente senza docstring e senza commenti.

    Una sentinella di confine giudica il CODICE: un commento che spiega perche'
    una cosa non si fa la nomina, e una ricerca ingenua la scambierebbe per
    quella cosa.
    """
    testo = percorso.read_text(encoding="utf-8")
    testo = re.sub(r'"{3}[\s\S]*?"{3}', "", testo)
    return re.sub(r"#[^\n]*", "", testo)


def sorgenti() -> dict[str, str]:
    return {
        p.relative_to(PACCHETTO).as_posix(): codice(p)
        for p in sorted(PACCHETTO.rglob("*.py"))
        if "__pycache__" not in p.parts
    }


# ---------------------------------------------------------------------------
# S  Le tre sentinelle del design
# ---------------------------------------------------------------------------

def test_S1_i_provider_sono_importabili_solo_dal_dispatcher():
    """PRIMA SENTINELLA. Un service che potesse importare un provider potrebbe
    mandare un messaggio senza passare dal gate del consenso - ed e'
    precisamente cio' che il gate esiste per impedire."""
    importatori = set()
    for nome, corpo in sorgenti().items():
        if nome.startswith("providers/"):
            continue
        if re.search(r"(?m)^\s*from\s+\.providers|^\s*from\s+communication\.providers|"
                     r"^\s*import\s+communication\.providers", corpo):
            importatori.add(nome)
    assert importatori == {"dispatcher.py"}, (
        f"i provider sono importati anche da: {sorted(importatori - {'dispatcher.py'})}"
    )


def test_S1b_nessun_modulo_fuori_dal_dominio_importa_i_provider():
    """Fuori dal pacchetto, nessuno: nemmeno main.py."""
    for percorso in ROOT.rglob("*.py"):
        parti = percorso.parts
        if any(p in parti for p in (".venv", "__pycache__", "tests", "scripts")):
            continue
        if "communication" in parti:
            continue
        testo = percorso.read_text(encoding="utf-8")
        assert "communication.providers" not in testo, (
            f"{percorso.relative_to(ROOT)} importa i provider del dominio"
        )


def test_S2_marketing_consent_non_e_leggibile_dentro_il_dominio():
    """SECONDA SENTINELLA. La colonna resta leggibile altrove - P24
    `database_revival` la usa per l'eleggibilita' - ma qui l'autorizzazione
    passa da `can_send_marketing` e da nient'altro."""
    for nome, corpo in sorgenti().items():
        assert "marketing_consent" not in corpo, f"{nome} legge la colonna"


def test_S3_e_il_tipo_a_decidere_se_il_gate_si_applica():
    """TERZA SENTINELLA. Non il canale, non il `reason_code`, non il template.

    Un M4 confermato a mano da un operatore resta marketing e resta soggetto
    alla revoca; la mail di servizio con il PDF non lo e' mai."""
    gate = inspect.getsource(dispatcher._consenso_nega)
    corpo = re.sub(r'"{3}[\s\S]*?"{3}', "", gate)
    assert 'message["communication_type"] != TYPE_MARKETING' in corpo
    for irrilevante in ("channel", "reason_code", "template_key", "mode"):
        assert irrilevante not in corpo, (
            f"il gate guarda {irrilevante!r}: solo communication_type decide"
        )


# ---------------------------------------------------------------------------
# D  D1
# ---------------------------------------------------------------------------

def test_D1_linsieme_degli_origin_e_chiuso_e_contiene_il_dispatcher():
    from operator_auth import context

    assert context.SYSTEM_CONTEXT_ORIGINS == ("public_stima", "communication_dispatch")
    assert dispatcher.DISPATCH_ORIGIN == "communication_dispatch"


def test_D2_il_contesto_del_dispatcher_e_di_sistema_e_di_una_agenzia_sola():
    from operator_auth.context import SystemAgencyContext

    class Operatore:
        agency_id, user_id, role, is_platform_admin = 7, 42, "agent", False

        def require_agency(self):
            return self.agency_id

    ctx = dispatcher.contesto_di_sistema(Operatore())
    assert isinstance(ctx, SystemAgencyContext)
    assert ctx.agency_id == 7
    assert ctx.origin == "communication_dispatch"
    # L'operatore era un AGENTE: il contesto di sistema non porta con se' il suo
    # restringimento, altrimenti il dispatcher salterebbe in silenzio i messaggi
    # dei contatti non assegnati a lui.
    assert ctx.role is None
    assert ctx.is_platform_admin is False


def test_D3_un_contesto_senza_agenzia_non_produce_uno_scope():
    from operator_auth.exceptions import PlatformAdminAgencyRequired

    class SenzaAgenzia:
        agency_id = None

        def require_agency(self):
            raise PlatformAdminAgencyRequired("nessuna agenzia")

    with pytest.raises(PlatformAdminAgencyRequired):
        dispatcher.contesto_di_sistema(SenzaAgenzia())


def test_D4_lagenzia_non_puo_venire_dal_payload():
    from communication.schemas import DispatchRequest

    assert set(DispatchRequest.model_fields) == {"limit"}
    with pytest.raises(Exception):
        DispatchRequest(limit=5, agency_id=99)


# ---------------------------------------------------------------------------
# G  Il gate
# ---------------------------------------------------------------------------

def test_G1_il_gate_sta_fra_il_claim_e_il_provider():
    """Non a `enqueue`. Fra l'accodamento e l'invio possono passare giorni: una
    decisione presa all'accodamento sarebbe una decisione su ieri."""
    corpo = re.sub(r'"{3}[\s\S]*?"{3}', "", inspect.getsource(dispatcher.dispatch_batch))
    assert corpo.index("claim_due") < corpo.index("_consenso_nega") < corpo.index("provider.send")


def test_G2_una_soppressione_non_chiama_il_provider():
    """Terminale: il `continue` sta prima di `provider.send`."""
    corpo = re.sub(r'"{3}[\s\S]*?"{3}', "", inspect.getsource(dispatcher.dispatch_batch))
    ramo = corpo[corpo.index("if ragione is not None"):corpo.index("risultato = provider.send")]
    assert "finalize_suppressed" in ramo
    assert "continue" in ramo
    assert "provider" not in ramo


def test_G3_il_consenso_non_e_interrogato_fuori_dal_dispatcher():
    for nome, corpo in sorgenti().items():
        if nome == "dispatcher.py":
            continue
        assert "can_send_marketing" not in corpo, f"{nome} interroga il consenso"


def test_G4_un_contatto_sparito_sopprime_invece_di_mandare():
    """Fail closed: un messaggio senza un destinatario interrogabile non parte."""
    corpo = inspect.getsource(dispatcher._consenso_nega)
    assert "ConsentNotFoundError" in corpo
    assert dispatcher.REASON_CONTATTO_ASSENTE in enums.__dict__.get("SEND_REASONS", set()) or True
    from consent.enums import SEND_DECISION_REASONS
    assert dispatcher.REASON_CONTATTO_ASSENTE in SEND_DECISION_REASONS, (
        "la ragione della soppressione deve appartenere all'insieme chiuso della guardia"
    )


# ---------------------------------------------------------------------------
# P  Il provider
# ---------------------------------------------------------------------------

def test_P1_il_provider_di_questa_fase_non_ha_rete():
    for nome, corpo in sorgenti().items():
        for rete in ("requests", "smtplib", "httpx", "urllib", "http.client",
                     "socket", "aiohttp"):
            assert rete not in corpo, f"{nome} nomina {rete}"


def test_P2_lunico_adapter_reale_e_quello_email_e_sta_al_suo_posto():
    """IL CONFINE SI E' SPOSTATO, E SOLO DI MEZZO PASSO.

    Questa sentinella vietava entrambi gli adapter reali: era vera finche'
    P29-2.5 era una fase sola. Lo split di §23.4 l'ha divisa in due -
    **P29-2.5E** email, che procede, e **P29-2.5W** WhatsApp, deferita e ancora
    bloccata da **R3, che resta OPEN**.

    Quindi `email_smtp.py` adesso esiste per progetto, e `invia_mail` compare -
    ma SOLO li'. Tutto cio' che riguarda WhatsApp resta vietato ovunque, parola
    per parola, ed e' la meta' di questo test che non deve cedere: il giorno in
    cui `whatsapp_meta.py` comparira' senza che R3 sia chiuso, e' qui che si
    deve rompere.
    """
    # 1. WhatsApp: nessun adapter, in nessuna forma. R3 e' OPEN.
    assert not (PACCHETTO / "providers" / "whatsapp_meta.py").exists(), (
        "whatsapp_meta.py e' P29-2.5W, che R3 blocca"
    )
    for nome, corpo in sorgenti().items():
        for whatsapp in ("invia_whatsapp", "whatsapp_meta", "graph.facebook",
                         "WHATSAPP_SERVICE_URL", "WHATSAPP_PHONE_ID",
                         "WHATSAPP_TOKEN", "WHATSAPP_"):
            assert whatsapp not in corpo, (
                f"{nome} nomina {whatsapp}: il canale WhatsApp e' fuori da "
                "P29-2.5E e resta bloccato da R3"
            )

    # 2. Email: l'adapter esiste, e `invia_mail` vive SOLO dentro di lui.
    for nome, corpo in sorgenti().items():
        if nome == "providers/email_smtp.py":
            continue
        assert "invia_mail" not in corpo, (
            f"{nome} nomina invia_mail: il trasporto email ha un file solo"
        )

    # 3. La configurazione SMTP resta di `database.invia_mail`: l'adapter
    #    AVVOLGE, non riscrive, quindi non legge nessuna di quelle variabili.
    for nome, corpo in sorgenti().items():
        assert "SMTP_" not in corpo, f"{nome} legge la configurazione SMTP"


def test_P3_il_provider_non_tocca_il_database():
    """Terzo confine del design: prende una destinazione e un corpo, ritorna un
    risultato. Non sa cosa sia un contatto, un'agenzia o un consenso."""
    for nome, corpo in sorgenti().items():
        if not nome.startswith("providers/"):
            continue
        for vietato in ("cur.execute", "communication_cursor", "get_connection",
                        "SELECT", "INSERT", "UPDATE", "agency_id", "ctx"):
            assert vietato not in corpo, f"{nome} nomina {vietato}"


def test_P4_un_risultato_non_accettato_non_puo_portare_un_id():
    """Un trasporto che ha rifiutato, o che non sa, non puo' anche aver
    restituito un id: sarebbe la prova di una consegna che non dichiara."""
    with pytest.raises(ValueError):
        provider_base.ProviderResult(outcome="rejected", provider_message_id="x")
    with pytest.raises(ValueError):
        provider_base.ProviderResult(outcome="unknown", provider_message_id="x")


def test_P5_il_provider_finto_dichiara_di_non_sapere():
    assert provider_null.CAPABILITIES.returns_message_id is False
    assert provider_null.CAPABILITIES.distinguishes_failure_class is False
    assert provider_null.CAPABILITIES.reports_delivery is False
    assert provider_null.send({}).provider_message_id is None


def test_P6_un_trasporto_che_non_distingue_non_produce_mai_failed():
    """C10 del design, applicato qui: un `False` di `invia_mail` non e' un
    rifiuto certo, e mapparlo su `failed` sarebbe una bugia che autorizza un
    retry."""
    povero = provider_base.ProviderCapabilities(
        returns_message_id=False, distinguishes_failure_class=False,
        reports_delivery=False)
    ricco = provider_base.ProviderCapabilities(
        returns_message_id=True, distinguishes_failure_class=True,
        reports_delivery=False)
    rifiuto = provider_base.ProviderResult(outcome="rejected", error_code="provider_rejected")

    assert dispatcher._esito_del_provider(rifiuto, povero)[0] == "indeterminate"
    assert dispatcher._esito_del_provider(rifiuto, ricco)[0] == "failed"

    ignoto = provider_base.ProviderResult(outcome="unknown")
    assert dispatcher._esito_del_provider(ignoto, ricco)[0] == "indeterminate"


# ---------------------------------------------------------------------------
# N  Cio' che P29-2.4 non anticipa
# ---------------------------------------------------------------------------

def test_N1_nessuno_scheduler():
    for nome, corpo in sorgenti().items():
        for pianificatore in ("APScheduler", "BackgroundScheduler", "Celery",
                              "crontab", "schedule.every"):
            assert pianificatore not in corpo, f"{nome} nomina {pianificatore}"


def test_N2_nessun_template_e_nessun_M1_M5():
    assert not (PACCHETTO / "templates.py").exists()
    for nome, corpo in sorgenti().items():
        assert "render_template" not in corpo, nome


def test_N3_la_rotta_non_e_montata():
    """Concordato: con un provider finto la rotta non manderebbe niente, e una
    rotta viva che non fa nulla e' una rotta che qualcuno un giorno chiama
    credendo che faccia qualcosa. Il mount arriva con l'adapter reale."""
    import main

    assert not any("communication" in p for p in main.app.openapi()["paths"]), (
        "la rotta di dispatch e' montata: P29-2.4 la dichiara soltanto"
    )
    assert "communication" not in (ROOT / "main.py").read_text(encoding="utf-8")


def test_N4_nessuna_migration_nuova():
    numeri = sorted(
        int(p.name[:3]) for p in (ROOT / "migrations").glob("*.sql")
        if not p.name.endswith("_down.sql") and p.name[:3].isdigit())
    assert numeri[-1] == 64, "P29-2.4 non introduce migration: la 064 basta"


def test_N5_nessun_retry_automatico():
    """P29-2.7. Il dispatcher non riporta niente in coda."""
    corpo = re.sub(r'"{3}[\s\S]*?"{3}', "", inspect.getsource(dispatcher))
    assert "queued" not in corpo.replace("claim_due", ""), "il dispatcher riaccoda"


def test_N6_la_superficie_del_dispatcher_e_minima():
    pubbliche = {
        n for n, v in vars(dispatcher).items()
        if callable(v) and not n.startswith("_")
        and getattr(v, "__module__", "") == dispatcher.__name__
    }
    assert pubbliche == {"dispatch_batch", "contesto_di_sistema"}, sorted(pubbliche)


def test_N7_il_runtime_p29_2_3_non_e_stato_riscritto():
    """Baseline immutabile: il dispatcher USA le API di P29-2.3, non le
    sostituisce."""
    corpo = re.sub(r'"{3}[\s\S]*?"{3}', "", inspect.getsource(dispatcher))
    for api in ("service.claim_due", "service.finalize_sent", "service.finalize_failed",
                "service.finalize_indeterminate", "service.finalize_suppressed"):
        assert api in corpo, f"il dispatcher non passa da {api}"
    for sql in ("SELECT", "INSERT", "UPDATE", "cur.execute"):
        assert sql not in corpo, f"il dispatcher scrive SQL per conto suo: {sql}"
