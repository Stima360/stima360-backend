"""P29-2.2 - repository e service del dominio COMMUNICATION, senza database.

Qui si prova cio' che si puo' provare senza PostgreSQL: gli insiemi chiusi
coincidono con i CHECK della 064, la validazione rifiuta prima di scrivere, lo
scope non lascia nominare una tabella senza predicato, e il modulo NON contiene
cio' che appartiene alle fasi successive.

Le proprieta' che dipendono davvero dal database - ON CONFLICT per tenant, il
compare-and-set di `cancel`, la composizione nella transazione del chiamante -
stanno in tests/test_p29_2_2_communication_postgres.py, che un PostgreSQL vero
ce l'ha. E' la stessa divisione di P29-1.1 e di P29-2.1, e per la stessa
ragione: un test di testo prova che la decisione e' SCRITTA, un test su
PostgreSQL prova che FUNZIONA.

Mappa:

    E   gli enum coincidono con i CHECK della migration 064
    S   lo scope: una tabella sola, mai senza predicato
    V   la validazione: cosa si rifiuta, e prima di toccare il database
    A   l'attore e' derivato, non ricevuto
    R   il repository: quali colonne scrive e quali no
    N   cio' che P29-2.2 NON e': niente rete, niente claim, niente provider
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from communication import enums, repository, scope, service
from communication.exceptions import (
    CommunicationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)

ROOT = Path(__file__).resolve().parents[1]
PACCHETTO = ROOT / "communication"
MIGRAZIONE = (ROOT / "migrations" / "064_p29_communication_foundation.sql").read_text(
    encoding="utf-8"
)

AGENZIA = 7
OPERATORE = 42


class Ctx:
    """Il minimo che `AgencyScope` richiede. Un operatore vero di un'agenzia."""

    def __init__(self, agency_id=AGENZIA, user_id=OPERATORE, role="agency_admin"):
        self.agency_id = agency_id
        self.user_id = user_id
        self.role = role
        self.is_platform_admin = False

    def require_agency(self):
        if self.agency_id is None:
            raise AssertionError("contesto senza agenzia")
        return self.agency_id


class CtxSistema(Ctx):
    """Un contesto di sistema: agenzia, e nessuna persona."""

    def __init__(self, agency_id=AGENZIA):
        super().__init__(agency_id=agency_id, user_id=None, role=None)


def dati_validi(**override):
    """Il dizionario che `enqueue` costruisce prima di chiamare `_validated`.

    Porta `direction` perche' lo porta `enqueue`: il default sta nella firma
    pubblica e in nessun altro posto. Un secondo default dentro `_validated`
    sarebbe una seconda risposta alla stessa domanda, e il giorno in cui i due
    divergessero nessuno saprebbe quale vale.
    """
    base = {
        "direction": enums.DIRECTION_OUTBOUND,
        "contact_id": 1,
        "channel": enums.CHANNEL_EMAIL,
        "communication_type": enums.TYPE_SERVICE,
        "mode": enums.MODE_AUTOMATIC,
        "reason_code": enums.REASON_STIMA_PDF,
        "rendered_body": "<p>corpo</p>",
        "destination_snapshot": "mario@example.invalid",
        "idempotency_key": "stima_pdf:email:1",
        "subject_snapshot": "La tua stima",
    }
    base.update(override)
    return base


#: LMC-1B: la 067 allarga `reason_code` di un valore. Il CHECK che il database
#: ha davvero e' quello della 064 EMENDATO dalle migration successive, quindi si
#: legge l'ultima definizione della serie e non la prima: confrontare l'enum con
#: la sola 064 direbbe che il codice ha un valore di troppo, mentre e' la 064 ad
#: avere un valore in meno.
EMENDAMENTI = tuple(
    (ROOT / "migrations" / f"{v}.sql").read_text(encoding="utf-8")
    for v in ("067_lmc1b_owner_login_reason",)
)


def valori_del_check(nome: str) -> set[str]:
    """I letterali ammessi da un CHECK ... IN (...), come risulta dopo la serie."""
    clausole = re.findall(
        rf"CONSTRAINT {nome}\s*CHECK \(([^)]*\))", MIGRAZIONE + "".join(EMENDAMENTI))
    assert clausole, f"{nome} non e' nella 064"
    clausola = re.match(r"(?s)(.*)", clausole[-1])
    return set(re.findall(r"'([a-z_0-9]+)'", clausola.group(1)))


# ---------------------------------------------------------------------------
# E  Gli enum coincidono con i CHECK della 064
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("insieme,vincolo", [
    ("CHANNELS", "communication_messages_channel_chk"),
    ("DIRECTIONS", "communication_messages_direction_chk"),
    ("COMMUNICATION_TYPES", "communication_messages_type_chk"),
    ("MODES", "communication_messages_mode_chk"),
    ("STATUSES", "communication_messages_status_chk"),
    ("REASON_CODES", "communication_messages_reason_code_chk"),
    ("ACTOR_TYPES", "communication_messages_actor_type_chk"),
    ("ERROR_CODES", "communication_messages_error_code_chk"),
    ("FAILURE_CLASSES", "communication_messages_failure_class_chk"),
])
def test_e1_ogni_insieme_e_esattamente_il_suo_check(insieme, vincolo):
    """Un valore aggiunto da una parte sola produrrebbe un rifiuto del database
    nel momento peggiore - su un messaggio che qualcuno aspettava - invece che un
    test rosso adesso."""
    assert getattr(enums, insieme) == valori_del_check(vincolo), insieme


def test_e2_sette_stati_e_nessuno_rimandato():
    assert len(enums.STATUSES) == 7
    for rimandato in ("scheduled", "delivered", "draft"):
        assert rimandato not in enums.STATUSES


def test_e3_lo_stato_iniziale_e_queued():
    assert enums.INITIAL_STATUS == enums.STATUS_QUEUED
    assert service.INITIAL_STATUS == enums.STATUS_QUEUED


def test_e4_si_annulla_solo_dalla_coda():
    """Dopo il claim il messaggio appartiene a un dispatcher."""
    assert enums.CANCELLABLE_STATUSES == frozenset({enums.STATUS_QUEUED})


def test_e5_p29_2_scrive_solo_outbound():
    """`inbound` e' nel CHECK perche' l'inbound di domani entri in questo ledger,
    ma nessun percorso di P29-2 lo scrive."""
    assert enums.DIRECTION_INBOUND in enums.DIRECTIONS
    assert enums.WRITABLE_DIRECTIONS == frozenset({enums.DIRECTION_OUTBOUND})


def test_e6_gli_attori_sono_due_e_subject_non_ce():
    """Il soggetto non manda comunicazioni: le riceve. In `consent/` esiste
    perche' li' il soggetto DECIDE."""
    assert enums.ACTOR_TYPES == {"system", "operator"}
    assert "subject" not in enums.ACTOR_TYPES


def test_e7_admin_lead_alert_non_e_un_motivo():
    """L'alert all'amministratore e' fuori dal perimetro del dominio."""
    assert "admin_lead_alert" not in enums.REASON_CODES


# ---------------------------------------------------------------------------
# S  Scope
# ---------------------------------------------------------------------------

def test_s1_le_tabelle_scopate_sono_quelle_che_il_modulo_legge():
    """In P29-2.2 era una sola: i tentativi non avevano un lettore, e una
    sorgente scopata che nessuno chiama non e' protezione - e' codice non
    esercitato che sembra protezione. P29-2.3 li legge, quindi ci sono.

    L'insieme resta affermato per UGUAGLIANZA: una terza tabella aggiunta per
    distrazione e' precisamente cio' che questo test esiste per vedere."""
    assert scope.COMMUNICATION_SCOPED_TABLES == frozenset({
        "communication_messages", "communication_attempts",
    })


def test_s2_nessuna_tabella_fuori_dal_dominio_e_scopata_qui():
    """`contacts` passa da core.scope, non da qui: una seconda risposta alla
    stessa domanda divergerebbe il giorno in cui una delle due cambia."""
    for estranea in ("contacts", "leads", "stime", "properties", "consent_events"):
        assert estranea not in scope.COMMUNICATION_SCOPED_TABLES


def test_s3_la_sorgente_porta_sempre_il_predicato():
    sorgente, parametri = scope.communication_scoped_source(Ctx(), "communication_messages", "m")
    assert sorgente == "communication_messages m WHERE m.agency_id = %s"
    assert parametri == [AGENZIA]


def test_s4_una_tabella_non_scopata_e_un_difetto_non_un_errore_utente():
    with pytest.raises(scope.ProgrammingError):
        scope.communication_scoped_source(Ctx(), "contacts", "c")
    assert not issubclass(scope.ProgrammingError, CommunicationError)


def test_s5_il_predicato_sui_contatti_passa_da_core():
    """La regola su chi vede quali contatti - compreso il restringimento di un
    agente - e' gia' in core.scope. Riscriverla qui vorrebbe dire avere due
    risposte alla stessa domanda il giorno in cui una cambia."""
    testo = (PACCHETTO / "repository.py").read_text(encoding="utf-8")
    assert "core.scope import scoped_predicate" in testo
    assert 'core_scoped_predicate(ctx, "contacts", "c")' in testo


# ---------------------------------------------------------------------------
# V  Validazione
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("campo,valore", [
    ("channel", "sms"),
    ("communication_type", "transactional"),
    ("mode", "auto"),
    ("reason_code", "admin_lead_alert"),
])
def test_v1_un_valore_fuori_dallinsieme_e_rifiutato(campo, valore):
    with pytest.raises(ValidationError, match=campo):
        service._validated(Ctx(), dati_validi(**{campo: valore}))


def test_v2_inbound_e_rifiutato_anche_se_il_database_lo_accetterebbe():
    with pytest.raises(ValidationError, match="direction"):
        service._validated(Ctx(), dati_validi(direction=enums.DIRECTION_INBOUND))


def test_v3_unemail_senza_oggetto_e_rifiutata():
    with pytest.raises(ValidationError, match="subject_snapshot"):
        service._validated(Ctx(), dati_validi(subject_snapshot=None))


def test_v4_un_whatsapp_con_un_oggetto_e_rifiutato():
    with pytest.raises(ValidationError, match="subject_snapshot"):
        service._validated(Ctx(), dati_validi(
            channel=enums.CHANNEL_WHATSAPP, subject_snapshot="un oggetto",
            destination_snapshot="393331234567"))


def test_v5_un_whatsapp_senza_oggetto_passa():
    preparato = service._validated(Ctx(), dati_validi(
        channel=enums.CHANNEL_WHATSAPP, subject_snapshot=None,
        destination_snapshot="393331234567"))
    assert preparato["subject_snapshot"] is None


@pytest.mark.parametrize("campo", ["rendered_body", "destination_snapshot", "idempotency_key"])
def test_v6_i_campi_obbligatori_non_possono_essere_vuoti(campo):
    with pytest.raises(ValidationError, match=campo):
        service._validated(Ctx(), dati_validi(**{campo: "   "}))


def test_v7_le_due_meta_del_template_stanno_insieme():
    with pytest.raises(ValidationError, match="template"):
        service._validated(Ctx(), dati_validi(template_key="m2_v1"))
    with pytest.raises(ValidationError, match="template"):
        service._validated(Ctx(), dati_validi(template_version=1))
    preparato = service._validated(Ctx(), dati_validi(template_key="m2_v1", template_version=1))
    assert (preparato["template_key"], preparato["template_version"]) == ("m2_v1", 1)


def test_v8_lagenzia_viene_dal_contesto_non_dai_dati():
    """Un `agency_id` fra i dati sarebbe la strada per scrivere nell'agenzia di
    qualcun altro."""
    preparato = service._validated(Ctx(), dati_validi(agency_id=999))
    assert preparato["agency_id"] == AGENZIA
    assert "enqueue" in dir(service)
    assert "agency_id" not in inspect.signature(service.enqueue).parameters


def test_v9_il_limite_delle_letture_e_verificato():
    for cattivo in (0, -1, service.MAX_LIST_LIMIT + 1, "dieci", None):
        with pytest.raises(ValidationError, match="limit"):
            service.list_for_contact(Ctx(), 1, limit=cattivo)


# ---------------------------------------------------------------------------
# A  L'attore e' derivato
# ---------------------------------------------------------------------------

def test_a1_automatic_e_un_atto_del_sistema():
    preparato = service._validated(Ctx(), dati_validi(mode=enums.MODE_AUTOMATIC))
    assert preparato["actor_type"] == enums.ACTOR_SYSTEM
    assert preparato["actor_user_id"] is None


@pytest.mark.parametrize("mode", ["manual", "assisted"])
def test_a2_un_atto_delloperatore_porta_il_nome_delloperatore(mode):
    preparato = service._validated(Ctx(), dati_validi(
        mode=mode, communication_type=enums.TYPE_MARKETING,
        reason_code=enums.REASON_OPERATOR_MANUAL))
    assert preparato["actor_type"] == enums.ACTOR_OPERATOR
    assert preparato["actor_user_id"] == OPERATORE


@pytest.mark.parametrize("mode", ["manual", "assisted"])
def test_a3_un_atto_delloperatore_senza_operatore_e_rifiutato(mode):
    with pytest.raises(ValidationError, match="operator"):
        service._validated(CtxSistema(), dati_validi(mode=mode))


def test_a4_lattore_non_e_un_parametro():
    """Due campi che possono contraddirsi sono un campo di troppo: un chiamante
    non deve poter dichiarare un invio automatico firmato da un operatore."""
    parametri = inspect.signature(service.enqueue).parameters
    assert "actor_type" not in parametri
    assert "actor_user_id" not in parametri


def test_a5_un_contesto_di_sistema_puo_accodare_un_automatico():
    preparato = service._validated(CtxSistema(), dati_validi(mode=enums.MODE_AUTOMATIC))
    assert preparato["actor_type"] == enums.ACTOR_SYSTEM


# ---------------------------------------------------------------------------
# R  Il repository
# ---------------------------------------------------------------------------

def test_r1_le_colonne_del_dispatch_non_sono_scrivibili():
    """Appartengono a P29-2.3 e a P29-2.4. Un chiamante che potesse scriverle
    accoderebbe un messaggio gia' reclamato, o gia' inviato."""
    for vietata in ("status", "claim_token", "claimed_at", "attempt_count",
                    "last_attempt_at", "sent_at", "failed_at", "failure_class",
                    "provider", "provider_message_id", "error_code",
                    "error_detail", "suppressed_reason"):
        assert vietata not in repository.INSERTABLE_COLUMNS, vietata


def test_r2_lo_stato_non_e_un_parametro_di_enqueue():
    assert "status" not in inspect.signature(service.enqueue).parameters


def test_r3_linsert_usa_on_conflict_per_tenant():
    testo = (PACCHETTO / "repository.py").read_text(encoding="utf-8")
    assert "ON CONFLICT (agency_id, idempotency_key) DO NOTHING" in testo


def test_r4_cancel_e_un_compare_and_set():
    """Fra la lettura e la scrittura un dispatcher puo' aver reclamato il
    messaggio: una UPDATE incondizionata glielo strapperebbe di mano."""
    testo = (PACCHETTO / "repository.py").read_text(encoding="utf-8")
    corpo = testo[testo.index("def cancel_queued"):]
    assert "AND status = ANY(%s)" in corpo
    assert "AND agency_id = %s" in corpo


def test_r5_ogni_lettura_porta_il_predicato_di_agenzia():
    """Nessuna SELECT su communication_messages senza la sorgente scopata."""
    testo = (PACCHETTO / "repository.py").read_text(encoding="utf-8")
    for select in re.findall(r"SELECT[^\"']*?FROM\s+communication_messages", testo):
        raise AssertionError(
            f"SELECT che nomina la tabella senza passare dallo scope: {select!r}"
        )
    assert testo.count("communication_scoped_source(ctx") >= 3


def test_r6_nessuna_funzione_del_repository_apre_una_connessione():
    testo = (PACCHETTO / "repository.py").read_text(encoding="utf-8")
    assert "get_connection" not in testo
    assert "communication_cursor" not in testo


def test_r7_il_database_e_lunico_punto_di_connessione():
    """Il choke point censito in docs/P26_DB_ENTRYPOINTS.md."""
    testo = (PACCHETTO / "database.py").read_text(encoding="utf-8")
    assert "from database import get_connection" in testo
    # Il letterale si compone invece di essere scritto: la sentinella
    # tests/test_p26_db_entrypoints.py::test_h11 cerca l'apertura diretta come
    # SOTTOSTRINGA in ogni file del repository, e scriverlo qui dichiarerebbe
    # questo file un punto di connessione - che non e'. Stessa precauzione gia'
    # presa in P29-1.1 per la stessa sentinella.
    apertura_diretta = "psycopg2" + "." + "connect"
    assert apertura_diretta not in testo


# ---------------------------------------------------------------------------
# N  Cio' che P29-2.2 NON e'
# ---------------------------------------------------------------------------

def test_n1_nessuna_rete_nel_pacchetto():
    """Il primo dei cinque confini del design, e strutturale: un modulo che puo'
    scrivere una riga e non puo' chiamare nessuno non puo' mandare un messaggio
    per sbaglio."""
    for percorso in sorted(PACCHETTO.rglob("*.py")):
        testo = percorso.read_text(encoding="utf-8")
        for vietato in ("import requests", "import smtplib", "import httpx",
                        "urllib.request", "http.client", "socket"):
            assert vietato not in testo, f"{percorso.name} importa {vietato}"


def test_n2_le_scritture_di_p29_2_2_restano_quelle_di_p29_2_2():
    """Il claim, il fencing e la recovery sono arrivati con P29-2.3 e vivono
    nelle loro funzioni. Cio' che questo test continua a proteggere e' che NON
    siano entrati nelle due scritture di questa fase: `enqueue` accoda un
    messaggio in coda, `cancel` lo ritira, e nessuna delle due sa cosa sia un
    token.

    La sorveglianza si e' spostata dal FILE alla FUNZIONE, che e' il livello a
    cui il confine e' ancora vero."""
    for funzione in (repository.insert_message, repository.cancel_queued,
                     service.enqueue, service.cancel):
        sorgente = inspect.getsource(funzione)
        corpo = re.sub(r'"{3}[\s\S]*?"{3}', "", sorgente)
        corpo = re.sub(r"#[^\n]*", "", corpo)
        for anticipato in ("SKIP LOCKED", "FOR UPDATE", "claim_token",
                           "claimed_at", "attempt_count", "communication_attempts"):
            assert anticipato not in corpo, (
                f"{funzione.__name__} usa {anticipato!r}: non appartiene a questa scrittura"
            )


#: Il NUCLEO DB-safe: i file che P29-2.2 possiede. Il confine si e' spostato
#: con P29-2.4, che introduce legittimamente `dispatcher.py` e `providers/`:
#: applicare a quei file i divieti di P29-2.2 vieterebbe la fase che li
#: possiede. Cio' che resta protetto e' il nucleo.
NUCLEO = frozenset({
    "__init__.py", "database.py", "enums.py", "exceptions.py",
    "repository.py", "scope.py", "service.py",
})


def file_del_nucleo():
    return [p for p in sorted(PACCHETTO.glob("*.py")) if p.name in NUCLEO]


def test_n3_nessun_dispatcher_nessun_provider_nessun_template():
    """`templates.py` non esiste ancora: appartiene a P29-2.5.

    `dispatcher.py` e `providers/` esistevano in questa lista finche' P29-2.4
    non era implementata. Adesso ci sono per progetto, e il divieto si e'
    spostato dove conta: il nucleo non li importa.
    """
    # SENTINELLA AGGIORNATA DA P29-3B.2A: `templates.py` ORA ESISTE, per
    # progetto - il registry versionato e immutabile (P29-3A.1 SS I), senza
    # testi commerciali. Il divieto che questa fase manteneva non era sul
    # file ma sul NUCLEO, che continua a non importarlo: e' quello che si
    # verifica qui sotto.
    assert (PACCHETTO / "templates.py").exists()
    assert set(p.name for p in file_del_nucleo()) == set(NUCLEO), "il nucleo non e' intero"
    for percorso in file_del_nucleo():
        # Si giudica il CODICE, non la prosa: un commento che spiega che il
        # dispatcher NON e' qui lo nomina, e una ricerca ingenua lo scambierebbe
        # per il dispatcher. Via le docstring e via i commenti.
        testo = percorso.read_text(encoding="utf-8")
        corpo = re.sub(r'""".*?"""', "", testo, flags=re.DOTALL)
        corpo = re.sub(r"#[^\n]*", "", corpo)
        for vietato in ("dispatcher", "providers", "templates"):
            assert vietato not in corpo, f"{percorso.name} nomina {vietato}"


def test_n4_il_consenso_non_e_interrogato_qui():
    """Il gate sta immediatamente prima del dispatch, che e' P29-2.4: una
    decisione presa adesso su un messaggio che partira' domani sarebbe una
    decisione su ieri."""
    for percorso in file_del_nucleo():
        testo = percorso.read_text(encoding="utf-8")
        corpo = re.sub(r'""".*?"""', "", testo, flags=re.DOTALL)
        assert "can_send_marketing" not in corpo, percorso.name
        assert "marketing_consent" not in corpo, percorso.name


def test_n5_nessun_sender_esistente_e_stato_toccato():
    assert "def invia_mail(destinatario, oggetto, corpo_html, allegato=None):" in \
        (ROOT / "database.py").read_text(encoding="utf-8")
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "def invia_whatsapp(numero: str | None, p1: str, p2: str, p3: str):" in main_py
    # P29-2.6E monta la rotta, quindi `main.py` nomina `communication` in due
    # righe. Cio' che questa sentinella protegge non e' quella parola: e' che i
    # sender legacy siano ancora li', intatti.
    assert "from communication.router import router as communication_router" in main_py
    assert "communication.service" not in main_py


def test_n6_il_dominio_consenso_e_intatto():
    guardia = (ROOT / "consent" / "guard.py").read_text(encoding="utf-8")
    assert "def can_send_marketing(ctx, contact_id: int) -> MarketingSendDecision:" in guardia


def test_n7_nessuna_migration_nuova():
    numeri = sorted(
        int(p.name[:3]) for p in (ROOT / "migrations").glob("*.sql")
        if not p.name.endswith("_down.sql") and p.name[:3].isdigit()
    )
    # P29-2.2 non ha introdotto migration, e non lo fa adesso: la 065 e' di
    # P29-2.6E, la 066 di LMC-1A, la 067 di LMC-1B (il reason code del magic
    # link owner). Cio' che resta vietato qui e' che una migration nasca DA
    # QUESTA fase, e nessuna di quelle le appartiene.
    # LMC-10 (collisione autorizzata): la 068 e' la tabella degli
    # override del proprietario, approvata dallo STORAGE GATE di quella
    # fase. Non appartiene a questa, ed e' proprio cio' che la
    # sentinella continua a dire: la coda della serie e' nominata una
    # per una, quindi una migration nata QUI farebbe ancora fallire.
    # LMC-12 ha aggiunto la 069 (`owner_home_notifications`, lo stream di
    # notifiche PRE-INCARICO, approvata dal DESIGN GATE): dominio OWNER, non
    # di questa fase. La coda si nomina, come sempre.
    # LMC-15 ha aggiunto la 070 (`stima_acquisitions` e
    # `stima_inspections`, il ponte di acquisizione approvato dallo SCHEMA
    # GATE di LMC-15A.2): dominio ACQUISITION, non di questa fase. La coda
    # si nomina, come sempre.
    # P29-3B ha aggiunto la 071 (la fondazione delle journey: definizioni,
    # iscrizioni, controlli per contatto e la provenienza sul ledger),
    # approvata da P29-3A.1. La coda si nomina, come sempre.
    # A30-1 ha aggiunto la 072 (`appointments` e `appointment_events`, il
    # modello dell'Agenda CRM approvato dal GATE A30-0): dominio AGENDA, non
    # di questa fase. La coda si nomina, come sempre.
    # SENTINELLA AGGIORNATA DA A30-2P: la 073 ridefinisce due CHECK di
    # `appointments` per la facade LMC-15 (fonte `lmc15_facade`), approvata
    # dal GATE A30-2P FACADE DESIGN. Si nomina invece di smettere di
    # guardare: qualunque ALTRA migration comparisse farebbe ancora fallire.
    assert numeri[-1] == 73, "la serie si e' fermata o e' andata oltre la 073"
    assert 64 in numeri, "la 064 di P29-2.1 non c'e' piu'"


def test_n8_le_eccezioni_sono_del_dominio():
    for classe in (NotFoundError, ValidationError, ConflictError):
        assert issubclass(classe, CommunicationError)
    assert not issubclass(NotFoundError, ConflictError)


def test_n9_la_superficie_pubblica_del_service_e_dichiarata():
    """Per UGUAGLIANZA, non per inclusione: una funzione aggiunta per sbaglio -
    o lasciata in piedi da un esperimento - e' precisamente cio' che questo test
    esiste per vedere.

    Le quattro di P29-2.2 sono due scritture e due letture. Le otto di P29-2.3
    sono il claim, le quattro finalizzazioni, il risultato tardivo, la recovery
    e la lettura dei tentativi."""
    pubbliche = {
        n for n, v in vars(service).items()
        if callable(v) and not n.startswith("_") and getattr(v, "__module__", "") == service.__name__
    }
    # SENTINELLA AGGIORNATA DA P29-3D: `send_now` e' la dodicesima, e sta
    # QUI e non altrove perche' scrive sul ledger - sposta `scheduled_at` di
    # un messaggio in coda - e il ledger ha un solo scrittore. Non spedisce:
    # il consenso, il claim e il trasporto restano dove sono, e il
    # dispatcher continua a essere l'unico che parla con un provider.
    assert pubbliche == {
        "enqueue", "cancel", "send_now", "get_message", "list_for_contact",
        "claim_due", "finalize_sent", "finalize_failed", "finalize_indeterminate",
        "finalize_suppressed", "recover_stale", "list_attempts",
    }, sorted(pubbliche)
