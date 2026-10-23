"""STIMA Voice Fase 5 - il servizio delle API: collega fornitore AI, planner
(Fasi 1-2), registro (Fase 3) ed esecutore (Fase 4). Nessuna logica del CRM:
ogni scrittura passa dall'esecutore, che usa i servizi esistenti.

Abilitazione fail-closed, su tre livelli (tutti necessari):
  1. `STIMA_VOICE_ENABLED=1` nell'ambiente del server (assente = spento);
  2. `voice_agency_settings.enabled` per l'agenzia (default FALSE; tabella
     assente, cioe' migrazione 098 non applicata = spento);
  3. il fornitore AI configurato (`voice.provider`), solo per interpretare.
Fase 7: in piu', se impostato, l'elenco pilota `STIMA_VOICE_PILOT_USER_IDS`
limita STIMA Voice a quegli operatori (valore non valido = nessuno).
Fase 7: il router (`voice.router`) e' montato in `main.py`; montato non vuol
dire acceso - ogni richiesta passa da `guard`.

Chi parla, l'agenzia, il ruolo e i permessi vengono SOLO dalla sessione
(`OperatorContext` del router): mai dal testo, dal modello o dal corpo della
richiesta. Le scelte fra candidati che arrivano dal client sono valide solo
se compaiono fra i candidati che il CRM mostra a quell'operatore (executor).

Limite d'uso: `voice_agency_settings.max_commands_per_day` comandi NUOVI per
agenzia nel giorno di calendario Europe/Rome (una ripetizione della stessa
chiave non conta). Limite morbido: due richieste concorrenti al confine possono
superarlo di poco. `max_audio_seconds_month` non si applica: in Fase 5 non c'e'
audio.

Limiti noti, dichiarati (non nascosti):
  * un comando chiuso solo con la riconciliazione resta `partial` come stato
    complessivo anche con tutti i passi conclusi (la 098 non ammette
    `partial -> completed` senza esecuzione): la vista lo dice con
    `all_steps_concluded` e una nota;
  * il comune fuori dal catalogo del CRM ora si rileva nel piano
    (`municipality_not_supported`, domanda non confermabile) con la stessa
    regola del censimento; il censimento resta comunque l'ultima difesa.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from collections import deque
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import psycopg2

from . import dates, executor, ledger, mandate_rules, planner, reconcile, undo
from . import provider as voice_provider
from . import repository as voice_repository
from .executor import VoiceRefused
from .ledger import CommandBusy
from .schemas import PropertyRef, parse_plan_output

log = logging.getLogger("stima360.voice")
ROMA = ZoneInfo("Europe/Rome")

GLOBALLY_DISABLED = "VOICE_GLOBALLY_DISABLED"
DAILY_LIMIT = "VOICE_DAILY_LIMIT"
#: V8-0: troppe richieste dello stesso operatore in un minuto.
RATE_LIMITED = "VOICE_RATE_LIMITED"
TEXT_INVALID = "VOICE_TEXT_INVALID"
#: Fase 7 - conversazione: una risposta si da' solo a un comando che non ha
#: ancora eseguito nulla; il trascritto (contesto) deve esistere ancora.
REPLY_NOT_ALLOWED = "VOICE_REPLY_NOT_ALLOWED"
CONVERSATION_EXPIRED = "VOICE_CONVERSATION_EXPIRED"
#: Il prefisso con cui la risposta dell'agente si aggiunge al testo originale.
REPLY_PREFIX = "Precisazione: "

#: I messaggi delle domande e dei blocchi, per codice (stabili, in italiano).
MESSAGES = {
    "contact_identity_weak": "Serve il cognome, il telefono o l'email del contatto.",
    "record_kind_unknown": "Scheda commerciale o di censimento?",
    "record_kind_unverified": "Non ho capito se e' una scheda commerciale o di censimento.",
    "record_kind_conflict": "Il vocale parla sia di vendita sia di censimento: che scheda serve?",
    "building_location_missing": "Dove si trova la palazzina? Serve l'indirizzo o un nome.",
    "task_without_contact": "A quale contatto si riferisce il promemoria?",
    "appointment_purpose_missing": "Per che cosa e' l'appuntamento con l'acquirente? (visita di un immobile, proposta, telefonata...)",
    "visit_mandate_required": "Quale immobile deve visitare il cliente? Una visita si fissa solo su un incarico di vendita.",
    "visit_client_required": "Con quale cliente e' la visita?",
    "visit_client_is_owner": "Il cliente indicato e' il proprietario dell'immobile: chi e' il cliente che lo visita?",
    "visit_no_usable_mandate": "L'immobile indicato non ha un incarico di vendita in corso o scaduto: la visita non si fissa.",
    "agent_named": "L'appuntamento e' per un altro agente: serve il permesso di assegnare.",
    dates.UNRECOGNIZED_DATE: "Non ho capito il giorno.",
    dates.UNRECOGNIZED_TIME: "Non ho capito l'orario.",
    dates.AMBIGUOUS_HOUR: "Mattina o pomeriggio?",
    dates.AMBIGUOUS_WEEK: "Quale giorno, esattamente?",
    dates.NONEXISTENT_LOCAL: "Quell'orario non esiste nella notte del cambio d'ora.",
    dates.DOUBLE_LOCAL: "Quell'orario cade due volte nella notte del cambio d'ora.",
    dates.PAST: "Il momento indicato e' gia' passato.",
    "unsupported_intent": "Questa richiesta non e' fra i comandi vocali.",
    "units_invented": "Le unita' non dettate non si creano: si aggiungono con il censimento.",
    "invalid_payload": "Dati non validi per il CRM.",
    "too_many_commands": "Troppi comandi in un solo vocale.",
    "reference_not_found": "Non trovo il record indicato fra quelli che puoi vedere.",
    "reference_ambiguous": "Ci sono piu' record possibili: scegli quello giusto.",
    "reference_fuzzy": "Ho trovato un record simile: confermi che e' questo?",
    "note_target_not_exact": "Confermi il record su cui scrivere la nota?",
    "contact_confirmation_required": "Trovato per nome: confermi che e' questo contatto?",
    "possible_duplicate_contact": "Esiste gia' un contatto con questi dati: crearne un altro?",
    "possible_duplicate_hidden": "Un collega segue gia' un contatto con questi recapiti: crearne un altro?",
    "similar_building_found": "Esiste gia' una palazzina simile: salvarne un'altra?",
    "similar_unit_found": "Esiste gia' un'unita' simile: salvarne un'altra?",
    "cadastral_duplicate": "Unita' gia' censita con gli stessi dati catastali: non si crea.",
    "agenda_conflict": "In quell'orario hai gia' un impegno: scegli un altro orario.",
    "agent_cannot_assign": "Non puoi fissare appuntamenti per altri agenti.",
    "depends_on_question": "Dipende da una domanda di un passo precedente.",
    "depends_on_blocked": "Dipende da un passo bloccato.",
    "mode_review": "Modalita' revisione: ogni passo va confermato.",
    "mode_assisted": "Modalita' assistita: questo passo va confermato.",
    executor.OWNER_LINK_CONFIRMATION: "Confermi il collegamento del proprietario? Non si annulla con la voce.",
    executor.COMPOSITE: "Comando in piu' passi: conferma prima di qualsiasi scrittura.",
    executor.PLAN_HAS_BLOCKED: "Una parte del comando e' bloccata: confermi di eseguire il resto?",
    executor.PROPERTY_NOT_MANAGEABLE: "L'immobile non e' gestito da te.",
    executor.MUNICIPALITY_NOT_SUPPORTED: "Il comune indicato non e' fra quelli gestiti dal CRM: indica un comune valido.",
    executor.REFERENCE_CHANGED: "I dati sono cambiati dopo la conferma: ripeti il comando.",
    # V9: incarichi e acquisizioni. L'operazione si dice per esteso: e' cio' che si conferma.
    mandate_rules.WILL_GENERATE: ("Confermando generi un INCARICO EFFETTIVO nel CRM, come con «Genera incarico»: "
                                  "l'acquisizione diventa «Acquisita» e l'immobile va in incarico. "
                                  "STIMA non registra alcuna firma del proprietario."),
    mandate_rules.WILL_UPDATE_ACQUISITION: ("Confermando aggiorni il prezzo richiesto dell'acquisizione. "
                                            "L'incarico si potra' generare dopo il sopralluogo."),
    mandate_rules.WILL_SET_PROPERTY_PRICE: "Confermando cambi il prezzo richiesto dell'immobile, con lo storico prezzi.",
    mandate_rules.WILL_SET_ACQUISITION_PRICE: "Confermando cambi il prezzo richiesto registrato sull'acquisizione.",
    mandate_rules.WILL_SET_VALUATION: "Confermando cambi la valutazione registrata sull'acquisizione.",
    mandate_rules.WILL_START_ACQUISITION: ("Confermando crei l'acquisizione con il suo appuntamento in Agenda "
                                           "(«Appuntamento fissato»). Il sopralluogo risultera' svolto solo quando "
                                           "completerai l'appuntamento; l'incarico si genera dopo."),
    mandate_rules.ALREADY_EXISTS: "Questo immobile ha gia' un incarico: nessun doppione. Aprilo dalla scheda.",
    mandate_rules.PROPERTY_CENSUS: "L'immobile e' una scheda di censimento: prima va preso in carico dalla scheda Immobile.",
    mandate_rules.PROPERTY_CLOSED: "L'immobile e' venduto o archiviato: nessun incarico nuovo.",
    mandate_rules.ACQUISITION_OTHER_AGENT: "L'acquisizione di questo immobile e' seguita da un collega: la puo' gestire solo lui o il titolare.",
    mandate_rules.ACQUISITION_NOT_READY: "L'incarico si potra' generare dopo il sopralluogo, completando l'appuntamento di acquisizione.",
    mandate_rules.NO_OPEN_ACQUISITION: "Per questo immobile non c'e' un'acquisizione aperta.",
    mandate_rules.NOTHING_TO_PRICE: "Questo immobile non ha ne' un incarico ne' un'acquisizione aperta: il prezzo si cambia dalla scheda Immobile.",
    mandate_rules.PRICE_UNCHANGED: "Il prezzo e' gia' questo: nessuna modifica.",
    mandate_rules.NOT_MANAGEABLE: "Il prezzo di questo immobile lo cambia l'agente a cui e' assegnato, o il titolare.",
    mandate_rules.OWNER_NOT_LINKED: ("La persona indicata non risulta proprietaria di questo immobile: collega il "
                                     "proprietario dalla scheda Immobile, poi riprova."),
    mandate_rules.ACQUISITION_ALREADY_OPEN: "Per questo immobile c'e' gia' un'acquisizione aperta: aprila dalla sezione Acquisizioni.",
    mandate_rules.ACQUISITION_OWNER_MISSING: "L'immobile non ha un proprietario collegato: collegalo dalla scheda Immobile.",
    mandate_rules.ACQUISITION_OWNER_AMBIGUOUS: "L'immobile ha piu' proprietari: dimmi il nome di quello di riferimento.",
    mandate_rules.NO_ACQUISITION: ("Per questo immobile non c'e' ancora un'acquisizione: l'incarico nasce "
                                   "dall'acquisizione, dopo il sopralluogo. Vuoi iniziare il percorso? Dimmi giorno "
                                   "e ora dell'appuntamento di acquisizione."),
    mandate_rules.TYPE_MISSING: "Che tipo di incarico e'? (es. vendita in esclusiva)",
    mandate_rules.START_MISSING: "Da quando decorre l'incarico?",
    mandate_rules.PRICE_MISSING: "Qual e' il prezzo richiesto?",
    mandate_rules.PRICE_KIND_UNCLEAR: ("Quale prezzo vuoi cambiare: quello richiesto dell'immobile, quello registrato "
                                       "sull'acquisizione o la valutazione?"),
    mandate_rules.PROPERTY_CHOICE: "Ci sono piu' immobili possibili: quale? Rispondi con il numero o l'indirizzo.",
    planner.MANDATE_PRICE_UNCLEAR: "Non ho capito il prezzo: dimmelo in cifre (es. 185 mila euro).",
    planner.MANDATE_DATE_UNCLEAR: "Non ho capito la data dell'incarico.",
    planner.MANDATE_DATES_INVALID: "La scadenza non puo' precedere l'inizio.",
    planner.MANDATE_CHANGE_ALONE: "Un incarico o un'acquisizione si conferma da solo: dillo in un comando a parte.",
}
PARTIAL_NOTICE = ("Tutti i passi sono conclusi, ma lo stato complessivo resta «parziale» perche' almeno "
                  "un passo e' stato chiuso con una riconciliazione (limite noto).")


# ---------------------------------------------------------------------------
# Abilitazione
# ---------------------------------------------------------------------------

def globally_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return env.get("STIMA_VOICE_ENABLED", "").strip() == "1"


PILOT_USER_REQUIRED = "VOICE_PILOT_USER_REQUIRED"


def pilot_user_ids(env=None) -> frozenset[int] | None:
    """Fase 7: l'elenco pilota `STIMA_VOICE_PILOT_USER_IDS` (id di
    `operator_users`, separati da virgola). Assente o vuoto = nessuna
    restrizione per operatore (vale solo l'agenzia). Presente ma non valido =
    NESSUNO (fail-closed): un refuso non apre STIMA Voice a tutti."""
    env = os.environ if env is None else env
    grezzo = env.get("STIMA_VOICE_PILOT_USER_IDS", "").strip()
    if not grezzo:
        return None
    try:
        ids = frozenset(int(p) for p in grezzo.split(","))
    except ValueError:
        return frozenset()
    return ids if all(i > 0 for i in ids) else frozenset()


def guard(ctx):
    """Tutti i livelli d'abilitazione e il cancello dell'esecutore (D-E)."""
    if not globally_enabled():
        raise VoiceRefused(GLOBALLY_DISABLED, "STIMA Voice non e' attivo su questo server")
    pilota = pilot_user_ids()
    if pilota is not None and getattr(ctx, "user_id", None) not in pilota:
        raise VoiceRefused(PILOT_USER_REQUIRED, "STIMA Voice e' attivo solo per gli operatori della prova pilota")
    try:
        return executor.gate(ctx)
    except psycopg2.Error as exc:
        if getattr(exc, "pgcode", None) == "42P01":          # 098 non applicata: spento
            raise VoiceRefused(executor.DISABLED, "STIMA Voice non e' installato su questo database") from None
        raise


def browser_speech_allowed(env=None) -> bool:
    """Fase 6: il microfono dell'interfaccia usa il riconoscimento vocale del
    BROWSER, che su Chrome e Safari puo' elaborare l'audio sui server del
    fornitore del browser (Google, Apple). E' un servizio esterno: spento
    finche' `STIMA_VOICE_BROWSER_SPEECH=1` non lo autorizza. Senza, resta
    l'inserimento testuale (e la dettatura della tastiera del telefono)."""
    env = os.environ if env is None else env
    return env.get("STIMA_VOICE_BROWSER_SPEECH", "").strip() == "1"


def status(ctx) -> dict[str, bool]:
    """Fase 6: cio' che l'interfaccia deve sapere per mostrare (o NON
    mostrare) STIMA Voice. Mai un errore e mai un dettaglio: due booleani.
    Attivo solo se tutti i livelli lo sono, compreso il fornitore AI
    configurato: un'interfaccia che porta a un 503 non si mostra."""
    try:
        guard(ctx)
        voice_provider.ProviderConfig.from_env()
    except Exception:  # noqa: BLE001 - qualunque motivo = spento
        return {"enabled": False, "speech_input": False}
    return {"enabled": True, "speech_input": browser_speech_allowed()}


# ---------------------------------------------------------------------------
# Ricezione del testo -> piano (-> esecuzione automatica se tutto e' `auto`)
# ---------------------------------------------------------------------------

def _gia_ricevuto(ctx, chiave) -> bool:
    with voice_repository.read_only() as cur:
        cur.execute("SELECT 1 FROM voice_commands WHERE agency_id = %s AND actor_user_id = %s AND client_command_id = %s",
                    (ctx.require_agency(), ctx.user_id, str(chiave)))
        return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# V8-0: freno contro l'uso eccessivo, in memoria del processo
# ---------------------------------------------------------------------------
#
# Ogni interpretazione e' una chiamata al fornitore AI, i cui limiti valgono
# per l'intera organizzazione (richieste e token al minuto e al giorno). Due
# freni, oltre al limite giornaliero per agenzia del registro:
#   * per operatore: al massimo OPERATOR_PER_MINUTE interpretazioni in 60
#     secondi (comando, risposta e dettatura del dialog insieme);
#   * le dettature del dialog Agenda (`interpret`) non creano un comando nel
#     registro: si contano qui, per agenzia e giorno di Roma, nello stesso
#     limite `max_commands_per_day`.
# In memoria: si azzerano al riavvio e valgono per processo. Un freno, non una
# contabilita'; la contabilita' resta il registro (098).

OPERATOR_PER_MINUTE = 6
_FINESTRA = timedelta(seconds=60)
_freno_lock = threading.Lock()
_per_operatore: dict[tuple[int, int], deque] = {}
_interpretazioni: dict[tuple[int, Any], int] = {}


#: V8-0: dopo un 429 del fornitore il limite e' dell'ORGANIZZAZIONE, non
#: dell'operatore: fino a quell'istante nessuno chiama il modello (risposta
#: immediata con il tempo d'attesa restante, nessun invio inutile al fornitore).
_fornitore_occupato_fino: list = [None]


def _attendi_fornitore() -> None:
    fino = _fornitore_occupato_fino[0]
    if fino is None:
        return
    resto = (fino - datetime.now(ROMA)).total_seconds()
    if resto > 0:
        raise voice_provider.ProviderBusy(int(resto) + 1)
    _fornitore_occupato_fino[0] = None


def _interpreta(provider, testo: str, quando: datetime, *, context=None):
    """L'UNICA chiamata al modello del servizio: rispetta la pausa dopo un 429
    e la registra quando arriva."""
    _attendi_fornitore()
    try:
        return voice_provider.interpret(provider, testo, quando, context=context)
    except voice_provider.ProviderBusy as occupato:
        with _freno_lock:
            _fornitore_occupato_fino[0] = datetime.now(ROMA) + timedelta(seconds=occupato.retry_after)
        raise


def _freno_operatore(ctx, adesso: datetime) -> None:
    chiave = (int(ctx.require_agency()), int(ctx.user_id))
    with _freno_lock:
        coda = _per_operatore.setdefault(chiave, deque())
        while coda and adesso - coda[0] >= _FINESTRA:
            coda.popleft()
        if len(coda) >= OPERATOR_PER_MINUTE:
            raise VoiceRefused(RATE_LIMITED, "Troppe richieste vocali in un minuto: attendi qualche secondo")
        coda.append(adesso)


def _conta_interpretazione(ctx, impostazioni, adesso: datetime) -> None:
    giorno = adesso.astimezone(ROMA).date()
    chiave = (int(ctx.require_agency()), giorno)
    with _freno_lock:
        for k in [k for k in _interpretazioni if k[1] != giorno]:
            del _interpretazioni[k]
        if _interpretazioni.get(chiave, 0) >= impostazioni.max_commands_per_day:
            raise VoiceRefused(DAILY_LIMIT, "Limite giornaliero dei comandi vocali raggiunto")
        _interpretazioni[chiave] = _interpretazioni.get(chiave, 0) + 1


def _limite_giornaliero(ctx, impostazioni, adesso: datetime) -> None:
    giorno = adesso.astimezone(ROMA).date()
    inizio = datetime.combine(giorno, time(0, 0), tzinfo=ROMA)
    fine = datetime.combine(giorno + timedelta(days=1), time(0, 0), tzinfo=ROMA)
    with voice_repository.read_only() as cur:
        cur.execute("SELECT count(*) AS n FROM voice_commands WHERE agency_id = %s AND recorded_at >= %s "
                    "AND recorded_at < %s", (ctx.require_agency(), inizio, fine))
        usati = int(cur.fetchone()["n"])
    if usati >= impostazioni.max_commands_per_day:
        raise VoiceRefused(DAILY_LIMIT, "Limite giornaliero dei comandi vocali raggiunto")


#: Collaudo 3: l'output del modello di ogni comando, congelato nel registro
#: (`frozen`, immutabile; nessuna colonna nuova): una risposta che si capisce
#: senza il modello lo riusa (`planner.apply_local_reply`).
MODEL_OUTPUT_KEY = "model_output"
#: Collaudo 3: gli stati di un comando ancora «in corso» per chi lo ha dettato.
IN_CORSO = ("planned", "awaiting_answers", "executing", "partial")
#: ...e quelli che un comando nuovo chiude, se non hanno mai eseguito nulla.
_SOSTITUIBILE = ("received", "planned", "awaiting_answers")


def submit(ctx, client_command_id, text: str, *, provider, now: datetime | None = None,
           context: list[str] | None = None, output=None, supersede: bool = False) -> dict[str, Any]:
    """Testo dettato -> comando nel registro -> piano. Idempotente sulla
    chiave del client: la stessa chiave con lo stesso testo restituisce lo
    stesso comando senza richiamare il modello; con un testo diverso e' un
    conflitto. Un errore del fornitore lascia il comando senza piano e
    senza effetti: si ripete con la stessa chiave.

    Collaudo 3: `output` gia' pronto (una risposta capita senza il modello):
    nessuna chiamata, nessuna attesa dopo un 429, nessun freno per minuto;
    restano abilitazione, limite giornaliero, idempotenza e tutto il resto.
    `supersede` (un comando NUOVO dalla pagina): quando il suo piano esiste,
    i comandi precedenti dello stesso operatore mai eseguiti si annullano,
    cosi' resta una sola conversazione viva."""
    impostazioni = guard(ctx)
    testo = (text or "").strip()
    if not testo or len(testo) > voice_provider.MAX_TEXT:
        raise VoiceRefused(TEXT_INVALID, "testo vuoto o troppo lungo")
    adesso = now or datetime.now(ROMA)
    if output is None:
        _attendi_fornitore()                   # pausa dopo un 429: nessun comando nuovo, nessuna chiamata
    if not _gia_ricevuto(ctx, client_command_id):
        _limite_giornaliero(ctx, impostazioni, adesso)
        if output is None:
            _freno_operatore(ctx, datetime.now(ROMA))   # solo un comando NUOVO chiama il fornitore
    riga, _ = ledger.open_command(ctx, client_command_id, input_kind="text",
                                  request_sha256=ledger.request_fingerprint("text", testo), recorded_at=adesso)
    if riga["plan_sha256"] is None:
        with ledger.executing(ctx, riga["id"]):            # una sola interpretazione per comando
            comando = ledger.get_command(ctx, riga["id"])
            if comando["plan_sha256"] is None:
                if output is None:
                    output = _interpreta(provider, testo, comando["recorded_at"], context=context)
                piano = planner.build_plan(output, testo, comando["recorded_at"])
                executor.prepare(ctx, riga["id"], piano)
                ledger.freeze(ctx, riga["id"], MODEL_OUTPUT_KEY,
                              lambda: output.model_dump(mode="json", exclude_none=True))
                registrato = ledger.get_command(ctx, riga["id"])
                if registrato["steps"] and all(p["decision"] == "auto" for p in registrato["steps"]):
                    automatico = True
                else:
                    automatico = False
            else:
                automatico = False
        if automatico:
            executor.execute(ctx, riga["id"])
    if supersede:
        _chiudi_precedenti(ctx, riga["id"])
    return view(ctx, riga["id"])


def _chiudi_precedenti(ctx, tenere: int) -> None:
    """Collaudo 3: un comando nuovo sostituisce quelli dello stesso operatore
    ancora aperti e MAI eseguiti (nessun passo partito): niente conversazioni
    sovrapposte. Uno in esecuzione in questo momento (lock) resta com'e'."""
    with voice_repository.read_only() as cur:
        cur.execute("SELECT id FROM voice_commands WHERE agency_id = %s AND actor_user_id = %s AND id <> %s "
                    "AND status = ANY(%s) ORDER BY id", (ctx.require_agency(), ctx.user_id, tenere, list(_SOSTITUIBILE)))
        aperti = [int(r["id"]) for r in cur.fetchall()]
    for cid in aperti:
        try:
            with ledger.executing(ctx, cid):
                c = ledger.get_command(ctx, cid)
                if c["status"] in _SOSTITUIBILE and all(p["state"] in _MAI_ESEGUITO for p in c["steps"]):
                    ledger.cancel(ctx, cid)
        except CommandBusy:
            continue


def current(ctx) -> dict[str, Any]:
    """Collaudo 3: il comando IN CORSO di chi chiede (l'ultimo non concluso,
    con un piano), per ritrovarlo rientrando nella pagina: la vista dal
    registro e le parole della conversazione. Sola lettura: nessuna
    interpretazione, nessuna chiamata al modello."""
    guard(ctx)
    with voice_repository.read_only() as cur:
        cur.execute("SELECT id, transcript FROM voice_commands WHERE agency_id = %s AND actor_user_id = %s "
                    "AND status = ANY(%s) AND plan IS NOT NULL ORDER BY id DESC LIMIT 1",
                    (ctx.require_agency(), ctx.user_id, list(IN_CORSO)))
        riga = cur.fetchone()
    if riga is None:
        return {"command": None, "conversation": []}
    conversazione = (riga["transcript"] or "").split(f"\n{REPLY_PREFIX}") if riga["transcript"] else []
    return {"command": view(ctx, int(riga["id"])), "conversation": [x.strip() for x in conversazione if x.strip()]}


# ---------------------------------------------------------------------------
# V8-5: interpretare per compilare il dialog dell'Agenda (nessuna scrittura)
# ---------------------------------------------------------------------------

def interpret(ctx, text: str, *, provider, now: datetime | None = None) -> dict[str, Any]:
    """Testo dettato -> cosa STIMA ha capito, per PRECOMPILARE il dialog
    «Nuovo appuntamento». Stesso fornitore, stesso planner, stesso risolutore
    con i permessi di chi parla, stessa politica: ma NESSUN comando nel
    registro e NESSUNA esecuzione. Il salvataggio resta quello del dialog
    manuale (l'Agenda ricontrolla tutto). Vale il limite giornaliero come
    lettura: non consuma un comando."""
    from . import policy, resolver
    impostazioni = guard(ctx)
    testo = (text or "").strip()
    if not testo or len(testo) > voice_provider.MAX_TEXT:
        raise VoiceRefused(TEXT_INVALID, "testo vuoto o troppo lungo")
    adesso = now or datetime.now(ROMA)
    _attendi_fornitore()                       # prima di consumare un'interpretazione del limite
    _limite_giornaliero(ctx, impostazioni, adesso)
    _freno_operatore(ctx, datetime.now(ROMA))
    _conta_interpretazione(ctx, impostazioni, adesso)
    output = _interpreta(provider, testo, adesso)
    piano = planner.build_plan(output, testo, adesso)
    risolto = resolver.resolve(piano, ctx)
    decisione = policy.decide(piano, risolto.facts, impostazioni.policy)
    with voice_repository.read_only() as cur:
        attore = voice_repository.operator_name(ctx, cur, ctx.user_id)
    passi = []
    for s in piano.steps:
        if s.intent != "create_appointment":
            continue
        d = decisione.of(s.ordinal)
        def candidati(nome):
            m = risolto.matches.get((s.ordinal, nome))
            return [{"id": c.id, "label": c.label} for c in (m.candidates if m else ())]
        def unico(nome):
            lista = candidati(nome)
            return lista[0] if len(lista) == 1 else None
        motivi = _con_orario(_motivi(d.reasons), {k: (x.isoformat() if hasattr(x, "isoformat") else x)
                                                   for k, x in s.payload.items()}, adesso)
        motivi = _con_proprietario(motivi, SimpleNamespace(owner_labels=[
            {"id": c.id, "label": c.label} for c in risolto.owner_hints.get(s.ordinal, ())]))
        tipo = s.payload.get("appointment_type")
        passi.append({
            "appointment_type": tipo, "type_label": planner.APPOINTMENT_TYPE_LABELS.get(tipo, tipo),
            "needs_mandate": voice_repository.requires_mandate(tipo),
            "start_at": s.start_at.isoformat() if s.start_at else None,
            "end_at": s.end_at.isoformat() if s.end_at else None,
            "heard_start_at": s.payload["heard_start_at"].isoformat() if s.payload.get("heard_start_at") else None,
            "heard_end_at": s.payload["heard_end_at"].isoformat() if s.payload.get("heard_end_at") else None,
            "duration_minutes": s.payload.get("duration_minutes"),
            "notes": s.payload.get("notes"), "location_text": s.payload.get("location_text"),
            "contact": unico("contact"), "contact_candidates": candidati("contact"),
            "property": unico("property"), "property_candidates": candidati("property"),
            "agent": unico("agent") if "agent" in s.refs else None, "actor": attore,
            "decision": d.decision,
            "questions": [m["message"] for m in motivi if not m["confirmable"] and d.decision != "blocked"],
            "blocked": [m["message"] for m in motivi] if d.decision == "blocked" else [],
        })
    return {"steps": passi, "clarifications": list(output.clarifications)}


# ---------------------------------------------------------------------------
# Conversazione (Fase 7): rispondere a una domanda senza perdere il comando
# ---------------------------------------------------------------------------

_MAI_ESEGUITO = ("pending", "skipped")
#: Un comando annullato (dall'agente o da una risposta precedente) non si
#: riprende: una seconda risposta con un'altra chiave non apre un secondo piano.
#: La ripetizione della STESSA risposta passa dalla chiave (sopra).
_RISPONDIBILE = ("received", "planned", "awaiting_answers")


def _domande(vista: dict[str, Any]) -> list[str]:
    """Cio' che STIMA ha chiesto: i chiarimenti del modello e le domande di
    DATI dei passi in attesa (non le semplici conferme)."""
    domande = list(vista.get("clarifications") or [])
    for passo in vista.get("steps") or []:
        if passo["state"] == "pending" or passo["decision"] == "blocked":
            domande += [r["message"] for r in passo["reasons"] if not r["confirmable"]]
    return list(dict.fromkeys(d for d in domande if d))


def reply(ctx, command_id: int, client_command_id, text: str, *, provider,
          now: datetime | None = None) -> dict[str, Any]:
    """La risposta dell'agente a una domanda (o una correzione) prima che il
    comando abbia eseguito qualcosa. NON e' una scorciatoia: il testo
    originale + «Precisazione: <risposta>» diventa un comando NUOVO che passa
    per `submit` (abilitazione, limite giornaliero, idempotenza della chiave,
    fornitore, planner, resolver con i permessi di chi parla, policy,
    conferme). Il comando precedente, mai eseguito, viene annullato: un solo
    piano vivo per conversazione. Le domande di STIMA vanno al modello come
    contesto, NON nel trascritto: il planner cerca le prove (vendita,
    censimento...) solo nelle parole dell'agente.

    Stessa chiave = stesso comando nuovo (ripetibile dopo un errore di rete o
    del fornitore). Il contesto viene dal registro, mai dal client."""
    impostazioni = guard(ctx)
    risposta = (text or "").strip()
    if not risposta:
        raise VoiceRefused(TEXT_INVALID, "risposta vuota")
    vista = view(ctx, command_id)                       # di chi chiede, o 404
    comando = ledger.get_command(ctx, command_id)
    if comando.get("transcript") is None:
        raise VoiceRefused(CONVERSATION_EXPIRED, "Il testo del comando non e' piu' disponibile: detta di nuovo il comando")
    testo = f"{comando['transcript'].rstrip()}\n{REPLY_PREFIX}{risposta}"
    if len(testo) > voice_provider.MAX_TEXT:
        raise VoiceRefused(TEXT_INVALID, "La conversazione e' troppo lunga: inizia un nuovo comando")
    contesto = _domande(vista)
    # Collaudo 3: un giorno o un orario chiesto da STIMA si capisce qui, senza
    # il modello (nemmeno durante la pausa dopo un 429).
    locale = _risposta_locale(ctx, comando, vista, risposta, now or datetime.now(ROMA))
    # V8-0: il comando a cui si risponde si annulla SOLO quando il nuovo piano
    # esiste. Se il fornitore non risponde (429, rete), la conversazione resta
    # quella di prima, intatta, e la stessa risposta si ripete con la stessa chiave.
    if locale is None:
        _attendi_fornitore()
    if not _gia_ricevuto(ctx, client_command_id):
        with ledger.executing(ctx, command_id):         # nessuna esecuzione del precedente nel frattempo
            _rispondibile(ctx, command_id)
            _limite_giornaliero(ctx, impostazioni, now or datetime.now(ROMA))
    nuovo = submit(ctx, client_command_id, testo, provider=provider, now=now, context=contesto, output=locale)
    with ledger.executing(ctx, command_id):
        precedente = ledger.get_command(ctx, command_id)
        if precedente["status"] != "cancelled":
            _rispondibile(ctx, command_id, comando=precedente)
            ledger.cancel(ctx, command_id)
    return nuovo


_ORDINALI = {"primo": 1, "prima": 1, "uno": 1, "secondo": 2, "seconda": 2, "due": 2, "terzo": 3, "terza": 3,
             "tre": 3, "quarto": 4, "quarta": 4, "quattro": 4, "quinto": 5, "quinta": 5, "cinque": 5}


def _scelta_immobile(ctx, output, vista: dict, risposta: str):
    """V9: «quale immobile?» -> «il secondo», «2», «quello di via Sanzio»: si
    sceglie fra i candidati GIA' mostrati e si restringe per codice
    (letto qui, nell'agenzia), senza il modello. None se non e' univoca."""
    passi = [p for p in vista.get("steps") or []
             if any(r["code"] == mandate_rules.PROPERTY_CHOICE for r in p.get("reasons") or [])]
    if len(passi) != 1 or len(output.commands) != 1:
        return None
    candidati = (passi[0].get("candidates") or {}).get("property") or []
    t = dates.fold(risposta).strip(" .")
    t = re.sub(r"^(?:e |il |la |l |quello |quella |di |del |in )+", "", t).strip()
    if not t or not candidati:
        return None
    numero = int(t) if t.isdigit() else _ORDINALI.get(t.split()[0]) if len(t.split()) <= 2 else None
    if numero:
        scelti = [candidati[numero - 1]] if 1 <= numero <= len(candidati) else []
    else:
        parole = [p for p in re.sub(r"[^a-z0-9 ]+", " ", t).split() if len(p) > 1 and p not in ("di", "in", "via")]
        scelti = [c for c in candidati if parole and all(p in dates.fold(c["label"]) for p in parole)]
    if len(scelti) != 1:
        return None
    with voice_repository.read_only() as cur:
        codice = voice_repository.property_code(ctx, cur, int(scelti[0]["id"]))
    if not codice:
        return None
    c = output.commands[0]
    rif = PropertyRef(code=codice, owner=c.property.owner if c.property is not None else None)
    return output.model_copy(update={"commands": [c.model_copy(update={"property": rif})]})


def _risposta_locale(ctx, comando: dict, vista: dict, risposta: str, adesso: datetime):
    """L'output del modello del comando con la risposta applicata, o None
    (risposta da far capire al modello, o output non conservato)."""
    grezzo = (comando.get("frozen") or {}).get(MODEL_OUTPUT_KEY)
    if not grezzo or not comando.get("plan"):
        return None
    try:
        output = parse_plan_output(grezzo)
    except Exception:  # noqa: BLE001 - un output non piu' valido: si chiede al modello
        return None
    domande = {int(s["ordinal"]): set(s.get("issues") or []) for s in comando["plan"]["steps"]}
    for p in comando.get("steps") or []:
        domande.setdefault(int(p["ordinal"]), set()).update(p.get("reasons") or [])
    for p in vista.get("steps") or []:                  # V9: le domande di adesso (situazione reale)
        domande.setdefault(int(p["ordinal"]), set()).update(r["code"] for r in p.get("reasons") or [])
    return (planner.apply_local_reply(output, domande, risposta, adesso)
            or planner.apply_local_mandate_reply(output, domande, risposta, adesso)
            or _scelta_immobile(ctx, output, vista, risposta))


def _rispondibile(ctx, command_id: int, *, comando=None) -> None:
    comando = comando or ledger.get_command(ctx, command_id)
    if comando["status"] not in _RISPONDIBILE or any(p["state"] not in _MAI_ESEGUITO for p in comando["steps"]):
        raise VoiceRefused(REPLY_NOT_ALLOWED, "Questo comando ha gia' eseguito delle operazioni: "
                                              "per cambiarle usa «Annulla» o inizia un nuovo comando")


# ---------------------------------------------------------------------------
# Vista
# ---------------------------------------------------------------------------

def _motivi(codici) -> list[dict]:
    return [{"code": c, "message": MESSAGES.get(c, c), "confirmable": c in executor.CONFIRMABLE} for c in codici]


_RISULTATO = ("entity", "id", "record_kind", "created", "preexisting", "origin", "property_id", "contact_id",
              "lead_id", "reconciled", "start_at",
              # V9
              "acquisition_id", "appointment_id", "mode", "price", "previous_price", "replica")


_MESI = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre",
         "ottobre", "novembre", "dicembre")


def messaggio_orario_passato(sentito_iso: str | None, registrato: datetime | None) -> str:
    """Collaudo 21/10: «Le 10:30 di oggi sono già passate. Vuoi scegliere un
    altro orario?» (con il giorno e l'ora DETTI); senza, il messaggio generico."""
    if not sentito_iso:
        return MESSAGES[dates.PAST]
    sentito = datetime.fromisoformat(str(sentito_iso)).astimezone(ROMA)
    oggi = (registrato or datetime.now(ROMA)).astimezone(ROMA).date()
    if sentito.date() == oggi:
        giorno = "di oggi"
    elif sentito.date() == oggi - timedelta(days=1):
        giorno = "di ieri"
    else:
        giorno = f"del {sentito.day} {_MESI[sentito.month - 1]}"
    ora = f"{sentito.hour}:{sentito.minute:02d}"
    if sentito.hour == 1:
        return f"L'{ora} {giorno} è già passata. Vuoi scegliere un altro orario?"
    return f"Le {ora} {giorno} sono già passate. Vuoi scegliere un altro orario?"


def _con_orario(motivi: list[dict], payload: dict, registrato) -> list[dict]:
    for m in motivi:
        if m["code"] == dates.PAST:
            m["message"] = messaggio_orario_passato(payload.get("heard_start_at"), registrato)
    return motivi


def _con_proprietario(motivi: list[dict], vista) -> list[dict]:
    """Collaudo 3: «venditore Francesco Rivo» senza immobili trovati per quel
    nome. Se il nome corrisponde a contatti visibili, la domanda lo dice
    (candidati, non scelte: l'immobile resta da indicare)."""
    nomi = [x["label"] for x in getattr(vista, "owner_labels", None) or []]
    if not nomi:
        return motivi
    for m in motivi:
        if m["code"] == "reference_not_found":
            chi = nomi[0] if len(nomi) == 1 else ", ".join(nomi[:-1]) + " o " + nomi[-1]
            m["message"] = (f"Tra i contatti ho trovato {chi}, ma nessun immobile con incarico collegato a "
                            "questo nome: quale immobile deve visitare il cliente?")
    return motivi


def _con_immobile(motivi: list[dict], vista) -> list[dict]:
    """V9: per un incarico, l'immobile trovato per il proprietario si conferma
    con le sue parole: «Ho trovato Via Sanzio 14... Vuoi utilizzare questo immobile?»."""
    info = getattr(vista, "mandate", None)
    for m in motivi:
        if info and m["code"] == "reference_fuzzy":
            m["message"] = f"Ho trovato {info['label']}. Vuoi utilizzare questo immobile?"
    return motivi


def _testo_prezzo(valore) -> str | None:
    return None if valore is None else str(valore)


def _scheda_incarico(passo_piano: dict, vista) -> dict[str, Any]:
    """V9: la scheda di un passo sugli incarichi, dalla situazione REALE
    dell'immobile (rilevata a ogni vista, con i permessi di chi chiede):
    proprietario, immobile, prezzi, tipo, date, stato effettivo, cosa manca,
    l'operazione che la conferma esegue e i collegamenti alle schede."""
    payload = passo_piano.get("payload") or {}
    base = {"kind": "mandate", "purpose": payload.get("purpose"), "property": None, "owner": None,
            "state": None, "missing": [], "operation": None, "links": {}}
    info = getattr(vista, "mandate", None)
    if not info:
        return base
    sit, ev = info["situation"], info["evaluation"]
    immobile, acq = sit["property"], sit.get("acquisition")
    esiste = mandate_rules.has_mandate(sit)
    nomi = {o["contact_id"]: o["name"] for o in sit.get("owners") or []}
    chi = (nomi.get(acq["owner_contact_id"]) if acq and acq.get("visible") else None) \
        or nomi.get(sit.get("owner_choice")) or ", ".join(n for n in nomi.values() if n) or None
    if "agreed_price" in ev.values:
        proposto, etichetta = ev.values["agreed_price"], "Prezzo concordato (incarico)"
    elif "column" in ev.values:
        proposto = ev.values.get("price")
        etichetta = "Nuovo prezzo richiesto (acquisizione)" if ev.values["column"] == "asking_price" else "Nuova valutazione"
    elif "price" in ev.values:
        proposto, etichetta = ev.values["price"], "Nuovo prezzo richiesto (immobile)"
    else:
        proposto, etichetta = ev.values.get("asking_price"), "Prezzo richiesto (acquisizione)"
    collegamenti = {"property": f"#/immobili/{immobile['id']}"}
    if immobile.get("acquisition_id"):
        collegamenti["mandate"] = f"#/incarichi/{immobile['id']}"
    if acq and acq.get("visible"):
        collegamenti["acquisition"] = f"#/acquisizioni/{acq['id']}"
    return {**base,
            "property": info["label"], "owner": chi,
            "mandate_exists": esiste,
            "mandate_type": immobile.get("mandate_type") if esiste else payload.get("mandate_type"),
            "mandate_start": str(immobile["mandate_start"]) if esiste and immobile.get("mandate_start")
            else payload.get("mandate_start"),
            "mandate_end": str(immobile["mandate_end"]) if esiste and immobile.get("mandate_end")
            else payload.get("mandate_end"),
            "property_price": _testo_prezzo(immobile.get("asking_price")),
            "acquisition_price": _testo_prezzo(acq.get("asking_price")) if acq and acq.get("visible") else None,
            "valuation": _testo_prezzo(acq.get("valuation_price")) if acq and acq.get("visible") else None,
            "proposed_price": _testo_prezzo(proposto), "proposed_price_label": etichetta,
            "price_from_acquisition": bool(ev.values.get("price_from_acquisition")),
            "appointment_start": acq["appointment_start"].isoformat()
            if acq and acq.get("visible") and acq.get("appointment_start") else None,
            "state": mandate_rules.effective_state(sit),
            "missing": list(ev.missing),
            "operation": MESSAGES.get(ev.mode) if ev.mode else None, "mode": ev.mode,
            "links": collegamenti}


def _riepilogo(ctx, passo_piano: dict, vista, attore: str | None) -> dict[str, Any]:
    """V8-4: la scheda riepilogativa di un appuntamento, con le parole del
    CRM: tipo, cliente, incarico (quando serve), data e ora, agente, note.
    Solo etichette che chi parla vede gia' (le stesse dei candidati)."""
    def unico(nome):
        lista = vista.labels.get(nome) or []
        return lista[0]["label"] if len(lista) == 1 else None
    payload = passo_piano.get("payload") or {}
    tipo = payload.get("appointment_type")
    agente = unico("agent") if "agent" in (passo_piano.get("refs") or {}) else None
    return {
        "type": tipo, "type_label": planner.APPOINTMENT_TYPE_LABELS.get(tipo, tipo),
        "contact": unico("contact"), "property": unico("property"),
        "needs_mandate": voice_repository.requires_mandate(tipo),
        "agent": agente or attore,
        # l'orario DETTO anche quando e' gia' passato (non eseguibile: lo dice la domanda)
        "start_at": passo_piano.get("start_at") or payload.get("heard_start_at"),
        "end_at": passo_piano.get("end_at") or payload.get("heard_end_at"),
        "time_in_past": not passo_piano.get("start_at") and bool(payload.get("heard_start_at")),
        "notes": payload.get("notes"),
    }


def view(ctx, command_id: int) -> dict[str, Any]:
    guard(ctx)
    comando = ledger.get_command(ctx, command_id)
    base = {"command_id": comando["id"], "status": comando["status"], "mode": comando["mode"],
            "recorded_at": comando["recorded_at"].isoformat() if comando["recorded_at"] else None}
    if comando["plan"] is None:
        return {**base, "steps": [], "clarifications": [], "all_steps_concluded": False, "notice": None}
    viste = executor.situation(ctx, command_id)
    annullabili = {u["ordinal"]: u for u in undo.assess(ctx, command_id)}
    piano = {s["ordinal"]: s for s in comando["plan"]["steps"]}
    attore = None
    if any(p["intent"] == "create_appointment" for p in comando["steps"]):
        with voice_repository.read_only() as cur:
            attore = voice_repository.operator_name(ctx, cur, ctx.user_id)
    passi = []
    for p in comando["steps"]:
        n, v = p["ordinal"], viste[p["ordinal"]]
        attesa = None
        if p["state"] == "pending":
            attesa = executor._attesa(v, executor._conferme(comando["frozen"], n))
            if attesa is None and v.decision == "auto" and p["decision"] == "ask" \
                    and executor._conferme(comando["frozen"], n) is None:
                attesa = "needs_confirmation"
        scelte = {k: x for k, x in v.labels.items() if len(x) > 1 or v.decision == "ask"}
        passi.append({
            "ordinal": n, "intent": p["intent"], "quote": piano[n]["quote"], "state": p["state"],
            "decision": v.decision if p["state"] in ("pending", "failed") else p["decision"],
            "needs": attesa, "reasons": _con_immobile(_con_proprietario(_con_orario(
                _motivi(v.reasons if p["state"] in ("pending", "failed") else p["reasons"]),
                piano[n].get("payload") or {}, comando["recorded_at"]), v), v),
            "candidates": scelte, "duplicates": v.duplicate_labels, "hidden_duplicate": v.hidden,
            "start_at": piano[n].get("start_at"), "end_at": piano[n].get("end_at"),
            "result": {k: x for k, x in (p["result"] or {}).items() if k in _RISULTATO},
            "error": p["error_type"], "undo": annullabili[n]["status"],
            "summary": (_riepilogo(ctx, piano[n], v, attore) if p["intent"] == "create_appointment"
                        else _scheda_incarico(piano[n], v) if p["intent"] in mandate_rules.INTENTS else None),
        })
    conclusi = all(p["state"] in ("succeeded", "skipped") for p in comando["steps"])
    return {**base, "steps": passi, "clarifications": list(comando["plan"].get("clarifications") or []),
            "all_steps_concluded": conclusi,
            "notice": PARTIAL_NOTICE if conclusi and comando["status"] == "partial" else None}


# ---------------------------------------------------------------------------
# Azioni (ognuna ricontrolla l'abilitazione e restituisce la vista)
# ---------------------------------------------------------------------------

def confirm(ctx, command_id: int, *, steps: list[dict] | None = None, all_steps: bool = False) -> dict[str, Any]:
    guard(ctx)
    if all_steps:
        scelte = {int(s["ordinal"]): s.get("choices") or {} for s in (steps or [])}
        executor.confirm_all(ctx, command_id, choices=scelte)
    else:
        for s in steps or []:
            executor.confirm(ctx, command_id, int(s["ordinal"]), choices=s.get("choices") or None)
    return view(ctx, command_id)


def execute(ctx, command_id: int) -> dict[str, Any]:
    guard(ctx)
    executor.execute(ctx, command_id)
    return view(ctx, command_id)


def retry(ctx, command_id: int, ordinal: int) -> dict[str, Any]:
    guard(ctx)
    executor.retry(ctx, command_id, ordinal)
    return view(ctx, command_id)


def cancel(ctx, command_id: int) -> dict[str, Any]:
    guard(ctx)
    ledger.cancel(ctx, command_id)
    return view(ctx, command_id)


def undo_status(ctx, command_id: int) -> list[dict]:
    guard(ctx)
    return undo.assess(ctx, command_id)


def undo_step(ctx, command_id: int, ordinal: int) -> dict:
    guard(ctx)
    return undo.undo_step(ctx, command_id, ordinal)


def undo_command(ctx, command_id: int) -> list[dict]:
    guard(ctx)
    return undo.undo_command(ctx, command_id)


def reconcile_inspect(ctx, command_id: int, ordinal: int) -> dict:
    guard(ctx)
    return reconcile.inspect(ctx, command_id, ordinal)


def reconcile_decide(ctx, command_id: int, ordinal: int, *, outcome: str, record_id: int | None) -> dict:
    guard(ctx)
    reconcile.decide(ctx, command_id, ordinal, outcome=outcome, record_id=record_id)
    return view(ctx, command_id)
