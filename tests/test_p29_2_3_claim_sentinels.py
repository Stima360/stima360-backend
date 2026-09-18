"""P29-2.3 - le sentinelle: cio' che il codice del claim non deve poter fare.

Il test F del design (§9.6) vive qui, insieme alle regole strutturali che
accompagnano il fencing. Le prove di COMPORTAMENTO - SKIP LOCKED, il
compare-and-set, i trigger - stanno in tests/test_p29_2_3_claim_postgres.py, che
un PostgreSQL vero ce l'ha.

Mappa:

    F   nessun UPDATE di stato senza il suo predicato
    T   il token: uno per messaggio, uuid, azzerato alla finalizzazione
    R   nessun retry automatico: P29-2.3 non decide quando ritentare
    A   l'API: una implementazione per transizione, nessun wrapper duplicato
    N   niente rete, niente provider, niente consenso
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from communication import enums, repository, service

ROOT = Path(__file__).resolve().parents[1]
PACCHETTO = ROOT / "communication"
REPOSITORY = (PACCHETTO / "repository.py").read_text(encoding="utf-8")


def codice(percorso: Path) -> str:
    """Il sorgente senza docstring e senza commenti.

    Una sentinella di confine deve giudicare il CODICE: un commento che spiega
    perche' una cosa NON si fa la nomina, e una ricerca ingenua la scambierebbe
    per quella cosa.
    """
    testo = percorso.read_text(encoding="utf-8")
    testo = re.sub(r'""".*?"""', "", testo, flags=re.DOTALL)
    return re.sub(r"#[^\n]*", "", testo)


#: I file del NUCLEO DB-safe: quelli che P29-2.3 possiede e sui quali i suoi
#: divieti continuano a valere parola per parola. Il confine si e' spostato con
#: P29-2.4, che introduce legittimamente `dispatcher.py` e `providers/`: quei
#: file NON sono di questa fase e non vanno giudicati con le sue regole -
#: giudicarli significherebbe vietare la fase che li possiede. Cio' che questa
#: sentinella protegge resta intero: il nucleo che parla col database non
#: conosce provider, non interroga il consenso, non manda niente.
NUCLEO = frozenset({
    "__init__.py", "database.py", "enums.py", "exceptions.py",
    "repository.py", "scope.py", "service.py",
})

#: I file che appartengono alle fasi successive e che il nucleo non include.
#: `dependencies.py` e' di P29-2.6E: dice CHI puo' chiedere un giro di
#: dispatch, non parla col database, e le regole del nucleo non lo riguardano.
#: `integrations.py` e' del cutover P29: e' l'unico modulo che conosce due
#: domini, e il nucleo non deve conoscerlo.
FUORI_DAL_NUCLEO = ("dispatcher.py", "schemas.py", "router.py", "dependencies.py",
                    "integrations.py")


def sorgenti() -> dict[str, str]:
    return {p.name: codice(p) for p in sorted(PACCHETTO.glob("*.py"))
            if p.name in NUCLEO}


def test_N0_il_nucleo_sorvegliato_e_quello_che_esiste():
    """La sentinella si sorveglia da sola.

    Se un file del nucleo sparisce o cambia nome, `sorgenti()` lo salta in
    silenzio e ogni divieto qui sotto passerebbe esaminando un insieme piu'
    piccolo. E se un file nuovo comparisse nel pacchetto senza essere ne' nel
    nucleo ne' fra quelli noti delle fasi successive, nessuno lo guarderebbe.
    """
    visti = set(sorgenti())
    assert visti == set(NUCLEO), f"il nucleo sorvegliato non e' quello atteso: {visti}"
    noti = set(NUCLEO) | set(FUORI_DAL_NUCLEO)
    presenti = {p.name for p in PACCHETTO.glob("*.py")}
    assert presenti <= noti, (
        f"file non classificati in communication/: {sorted(presenti - noti)}. "
        "Un file nuovo va messo nel nucleo o dichiarato di una fase successiva."
    )


def sql_eseguito() -> list[str]:
    """Le stringhe SQL passate a `cur.execute`, e nient'altro.

    NON si puo' usare `codice()` per questo: quella funzione toglie le docstring
    riconoscendole dai delimitatori a tre apici, e le stringhe SQL di questo
    repository usano gli stessi delimitatori - verrebbero cancellate insieme
    alle docstring, e ogni sentinella sull'SQL passerebbe esaminando un file
    vuoto. E' un errore che questo test ha commesso davvero prima di essere
    corretto.

    Estrarre l'argomento di `cur.execute(` e' preciso e non ambiguo: cio' che
    finisce al database e' esattamente questo, e la prosa non ci entra mai.
    """
    testo = (PACCHETTO / "repository.py").read_text(encoding="utf-8")
    tripli = re.findall(r"cur\.execute\(\s*f?\"{3}(.*?)\"{3}", testo, re.DOTALL)
    singoli = re.findall(r"cur\.execute\(\s*f?\"([^\"\n]+)\"", testo)
    return tripli + singoli


def _statements(prefisso: str) -> list[str]:
    return [sql for sql in sql_eseguito()
            if sql.strip().upper().startswith(prefisso.upper())]


def statement_update(tabella: str = "communication_messages") -> list[str]:
    return [s for s in _statements("UPDATE") if tabella in s.split("SET")[0]]


# ---------------------------------------------------------------------------
# F  Nessun UPDATE di stato senza il suo predicato
# ---------------------------------------------------------------------------

def test_F_ogni_update_di_stato_e_condizionato():
    """IL TEST F DEL DESIGN, nella forma vera.

    Il design lo enuncia come "ogni UPDATE che tocca `status` porta
    `claim_token` nel WHERE". Alla lettera sarebbe falso, e per una ragione
    corretta: due transizioni avvengono PRIMA che un claim esista, e in quel
    momento il token non c'e' - la 064 lo impone con
    `(status = 'sending') = (claim_token IS NOT NULL)`.

    Le due sono `cancel_queued` (queued -> cancelled) e il claim stesso
    (queued -> sending), ed entrambe portano invece `status = <atteso>` nel
    WHERE: sono compare-and-set sullo stato, non sul token.

    L'invariante vera, che questo test afferma, e' quindi piu' forte e piu'
    precisa: NESSUN UPDATE di `status` e' incondizionato, e ogni UPDATE che
    parte da `sending` - cioe' ogni scrittura POST-CLAIM - porta anche il token.
    """
    statements = statement_update()
    assert statements, "nessun UPDATE su communication_messages: il test non prova niente"

    for sql in statements:
        if "SET" not in sql or "status" not in sql.split("WHERE")[0]:
            continue
        assert "WHERE" in sql, f"UPDATE di stato senza WHERE:\n{sql}"
        where = sql.split("WHERE", 1)[1]

        assert "agency_id" in where, f"UPDATE di stato senza agency_id:\n{sql}"
        assert re.search(r"\bstatus\s*=", where), (
            f"UPDATE di stato che non dichiara lo stato atteso:\n{sql}")

        # Se parte da `sending`, e' post-claim: deve portare il token.
        parte_da_sending = "CLAIMED_STATUS" in where or "'sending'" in where
        if parte_da_sending:
            assert "claim_token" in where, (
                f"scrittura post-claim senza fencing token:\n{sql}")


def test_F2_le_due_transizioni_pre_claim_sono_solo_due():
    """Se ne comparisse una terza, il test F sopra la lascerebbe passare senza
    token: qui si fissa quali sono."""
    pre_claim = [s for s in statement_update()
                 if "claim_token" not in s.split("WHERE", 1)[1]]
    assert len(pre_claim) == 2, f"transizioni pre-claim: {len(pre_claim)}"

    # Le due si riconoscono dalla FORMA dell'SQL, non da un nome di costante
    # Python: lo stato viaggia come parametro `%s`, quindi nella stringa il
    # letterale non compare mai. Cercarlo li' era l'errore di questo test prima
    # che venisse corretto.
    #
    #   annullamento  ->  WHERE ... status = ANY(%s)    l'insieme annullabile
    #   claim         ->  SET ... claim_token = %s      il token nasce qui
    annulla = [x for x in pre_claim if "status = ANY(%s)" in x]
    reclama = [x for x in pre_claim if "claim_token = %s" in x.split("WHERE")[0]]
    assert len(annulla) == 1, "manca la transizione di annullamento"
    assert len(reclama) == 1, "manca la transizione di claim"
    assert annulla[0] is not reclama[0]


def test_F3_ogni_update_dei_tentativi_e_condizionato():
    """Un tentativo si chiude una volta sola: ogni UPDATE porta
    `outcome = 'in_progress'` nel WHERE, cosi' il codice non prova nemmeno a
    fare cio' che il trigger rifiuterebbe."""
    for sql in statement_update("communication_attempts"):
        where = sql.split("WHERE", 1)[1]
        assert "agency_id" in where, sql
        assert "claim_token" in where, f"UPDATE di tentativo senza token:\n{sql}"
        assert "outcome" in where, f"UPDATE di tentativo senza stato atteso:\n{sql}"


def test_F4_nessun_update_usa_solo_lid():
    """`WHERE id = %s` da solo sarebbe una scrittura cross-tenant in attesa di
    accadere."""
    for sql in _statements("UPDATE"):
        where = sql.split("WHERE", 1)[1]
        assert "agency_id" in where, f"UPDATE senza predicato di agenzia:\n{sql}"


# ---------------------------------------------------------------------------
# T  Il token
# ---------------------------------------------------------------------------

def test_T1_il_token_e_per_messaggio_non_per_batch():
    """`token_factory` viene chiamata dentro il ciclo sui candidati."""
    sorgente = inspect.getsource(repository.claim_due)
    corpo = re.sub(r'""".*?"""', "", sorgente, flags=re.DOTALL)
    ciclo = corpo.index("for message_id in candidati")
    assert corpo.index("token_factory()") > ciclo, (
        "il token e' generato una volta sola: sarebbe un token di batch")


def test_T2_il_token_non_e_indovinabile():
    assert "uuid4" in codice(PACCHETTO / "service.py")
    primi = {service._token() for _ in range(50)}
    assert len(primi) == 50


def test_T3_la_finalizzazione_azzera_il_token():
    """E' cio' che rende il WHERE di una seconda finalizzazione falso per
    costruzione, invece che per un controllo applicativo."""
    sorgente = inspect.getsource(repository.finalize)
    assert "claim_token = NULL" in sorgente
    assert "claimed_at = NULL" in sorgente


def test_T4_anche_la_recovery_azzera_il_token():
    sorgente = inspect.getsource(repository.recover_stale_message)
    assert "claim_token = NULL" in sorgente
    assert "claimed_at = NULL" in sorgente


def test_T5_la_recovery_e_condizionata_anche_sulla_soglia():
    """Le cinque condizioni: agenzia, id, stato atteso, token osservato, soglia
    ancora superata."""
    sorgente = inspect.getsource(repository.recover_stale_message)
    where = sorgente.split("WHERE", 1)[1].split("RETURNING")[0]
    for condizione in ("agency_id", "id =", "status =", "claim_token =", "claimed_at <"):
        assert condizione in where, f"manca {condizione!r} nel WHERE della recovery"


# ---------------------------------------------------------------------------
# R  Nessun retry automatico
# ---------------------------------------------------------------------------

def test_R1_nessuna_transizione_riporta_in_coda():
    """P29-2.3 non decide quando ritentare: `failed -> queued` appartiene a
    P29-2.7, e qui non esiste nessuna scrittura che rimetta uno stato a
    `queued`."""
    for sql in statement_update():
        assegnazioni = sql.split("WHERE")[0]
        assert "'queued'" not in assegnazioni, f"riaccodamento automatico:\n{sql}"
        assert "INITIAL_STATUS" not in assegnazioni.replace("status = %s", ""), sql


def test_R2_la_recovery_non_tocca_attempt_count():
    sorgente = inspect.getsource(repository.recover_stale_message)
    assegnazioni = sorgente.split("SET", 1)[1].split("WHERE")[0]
    assert "attempt_count" not in assegnazioni


def test_R3_la_recovery_non_crea_tentativi():
    sorgente = inspect.getsource(repository.recover_stale_message)
    assert "INSERT INTO" not in sorgente, "la recovery affianca invece di chiudere"


def test_R4_solo_il_claim_incrementa_attempt_count():
    incrementi = [s for s in statement_update()
                  if re.search(r"attempt_count\s*=\s*attempt_count\s*\+\s*1", s)]
    assert len(incrementi) == 1, f"attempt_count incrementato in {len(incrementi)} punti"
    assert "attempt_count = attempt_count + 1" in inspect.getsource(repository.claim_due)


def test_R5_il_risultato_tardivo_non_tocca_il_messaggio():
    sorgente = re.sub(r'"""[\s\S]*?"""', "", inspect.getsource(repository.record_late_result),
                      count=1)
    assert "communication_messages" not in sorgente
    assert "UPDATE" not in sorgente


# ---------------------------------------------------------------------------
# A  L'API
# ---------------------------------------------------------------------------

def test_A1_una_sola_implementazione_del_fencing():
    """Quattro ingressi pubblici, un compare-and-set. Se il WHERE del fencing
    fosse scritto quattro volte, tre copie potrebbero divergere in silenzio."""
    fencing = [s for s in statement_update()
               if "AND claim_token = %(claim_token)s" in s]
    assert len(fencing) == 1, f"il fencing del messaggio e' scritto {len(fencing)} volte"
    for nome in ("finalize_sent", "finalize_failed", "finalize_indeterminate",
                 "finalize_suppressed"):
        sorgente = inspect.getsource(getattr(service, nome))
        assert "_finalizza(" in sorgente, f"{nome} non passa dall'unica implementazione"
        assert "UPDATE" not in sorgente, f"{nome} scrive SQL per conto suo"


def test_A2_la_superficie_pubblica_e_quella_prevista():
    pubbliche = {
        n for n, v in vars(service).items()
        if callable(v) and not n.startswith("_")
        and getattr(v, "__module__", "") == service.__name__
    }
    assert pubbliche == {
        # P29-2.2
        "enqueue", "cancel", "get_message", "list_for_contact",
        # P29-2.3
        "claim_due", "finalize_sent", "finalize_failed", "finalize_indeterminate",
        "finalize_suppressed", "recover_stale", "list_attempts",
    }, sorted(pubbliche)


def test_A2b_il_risultato_tardivo_non_e_un_ingresso_pubblico():
    """C18. Una riga `late_result` nasce come CONSEGUENZA di una finalizzazione
    che ha trovato l'ownership persa, mai come atto di un chiamante.

    Esposta, avrebbe permesso di fabbricare a mano righe di audit che raccontano
    invii mai tentati - e l'audit dei tentativi vale esattamente quanto e'
    difficile scriverci dentro una cosa falsa."""
    assert not hasattr(service, "record_late_result")
    assert hasattr(service, "_record_late_result")

    # E' invocato dalle finalizzazioni, non da fuori.
    ramo = inspect.getsource(service._finalizza)
    assert "_record_late_result(" in ramo


def test_A2c_il_late_result_richiede_il_claim_e_la_scrittura_e_idempotente():
    """C16 e C17, letti sull'SQL: la riga tardiva nasce solo se il tentativo
    regolare di quel token esiste, e un secondo arrivo identico non solleva."""
    sorgente = inspect.getsource(repository.record_late_result)
    assert "regular_attempt(cur, ctx, message_id, claim_token)" in sorgente
    assert "return None, False" in sorgente
    assert "ON CONFLICT (message_id, attempt_no, late_result) DO NOTHING" in sorgente
    # `attempt_no` non e' un parametro: viene dal tentativo regolare.
    assert "attempt_no" not in inspect.signature(repository.record_late_result).parameters
    assert 'regolare["attempt_no"]' in sorgente


def test_A2d_il_provider_non_e_un_parametro_di_nessuna_finalizzazione():
    """C15. Non e' un controllo: e' l'assenza del modo di sbagliare."""
    for nome in ("finalize_sent", "finalize_failed", "finalize_indeterminate",
                 "finalize_suppressed"):
        assert "provider" not in inspect.signature(getattr(service, nome)).parameters, nome
    assert "provider" not in inspect.signature(repository.finalize).parameters
    sorgente = inspect.getsource(repository.finalize)
    assert 'provider = regolare["provider"]' in sorgente, (
        "il provider non viene letto dal tentativo regolare del claim")


def test_A3_ogni_stato_terminale_ha_un_solo_ingresso():
    from communication.repository import ESITO_DEL_TENTATIVO
    assert set(ESITO_DEL_TENTATIVO) == {
        enums.STATUS_SENT, enums.STATUS_FAILED,
        enums.STATUS_INDETERMINATE, enums.STATUS_SUPPRESSED,
    }


def test_A4_finalize_non_e_pubblica_nel_service():
    """Il `status` non deve essere un parametro del chiamante: sceglierlo
    significherebbe poter scrivere `sent` su un messaggio mai partito."""
    assert not hasattr(service, "finalize")
    for nome in ("finalize_sent", "finalize_failed", "finalize_indeterminate",
                 "finalize_suppressed"):
        assert "status" not in inspect.signature(getattr(service, nome)).parameters


def test_A5_gli_esiti_del_tentativo_coincidono_con_il_check_della_064():
    migrazione = (ROOT / "migrations" / "064_p29_communication_foundation.sql").read_text(
        encoding="utf-8")
    clausola = re.search(
        r"CONSTRAINT communication_attempts_outcome_chk\s*CHECK \(([^)]*\))", migrazione)
    assert enums.ATTEMPT_OUTCOMES == set(re.findall(r"'([a-z_]+)'", clausola.group(1)))


def test_A6_suppressed_lascia_il_messaggio_senza_classe_di_fallimento():
    """La 064 lo impone: un messaggio soppresso non e' fallito, e' stato fermato.
    Il suo TENTATIVO invece porta `definite`, perche' l'esito e' `rejected`."""
    from communication.repository import ESITO_DEL_TENTATIVO
    esito, classe_tentativo, classe_messaggio = ESITO_DEL_TENTATIVO[enums.STATUS_SUPPRESSED]
    assert (esito, classe_tentativo, classe_messaggio) == ("rejected", "definite", None)


# ---------------------------------------------------------------------------
# N  Niente rete, niente provider, niente consenso
# ---------------------------------------------------------------------------

def test_N1_nessuna_rete_nel_pacchetto():
    for nome, corpo in sorgenti().items():
        for rete in ("requests", "smtplib", "httpx", "urllib", "http.client",
                     "socket", "aiohttp"):
            assert rete not in corpo, f"{nome} nomina {rete}"


def test_N2_provider_e_una_etichetta_non_un_modulo():
    """`provider` finisce in una colonna e dice CHI e' stato chiamato. P29-2.3
    non chiama nessuno."""
    for nome, corpo in sorgenti().items():
        assert not re.search(r"(?m)^\s*(from|import)\s+.*provider", corpo), nome
        for tipo in ("ProviderResult", "ProviderCapabilities"):
            assert tipo not in corpo, f"{nome} nomina {tipo}"
    # Le due asserzioni di esistenza che stavano qui - `providers/` e
    # `dispatcher.py` non esistono - erano vere finche' P29-2.4 non era
    # implementata. P29-2.4 li introduce legittimamente, e il divieto si e'
    # spostato: non "non esistono", ma "il nucleo non li conosce", che e'
    # esattamente cio' che il ciclo qui sopra continua a provare. Che
    # `providers/` sia importabile SOLO dal dispatcher e' la sentinella S1 di
    # `tests/test_p29_2_4_dispatch_sentinels.py`.


def test_N3_nessun_sender_esistente_e_richiamato():
    for nome, corpo in sorgenti().items():
        for sender in ("invia_mail", "invia_whatsapp", "send_template",
                       "graph.facebook", "WHATSAPP_", "SMTP_"):
            assert sender not in corpo, f"{nome} nomina {sender}"


def test_N4_il_consenso_non_e_interrogato():
    """Il gate sta immediatamente prima del provider dispatch, che e' P29-2.4."""
    for nome, corpo in sorgenti().items():
        assert "can_send_marketing" not in corpo, nome
        assert "marketing_consent" not in corpo, nome


def test_N5_nessuno_scheduler():
    for nome, corpo in sorgenti().items():
        for pianificatore in ("APScheduler", "BackgroundScheduler", "Celery",
                              "crontab", "schedule.every"):
            assert pianificatore not in corpo, f"{nome} nomina {pianificatore}"


def test_N6_nessuna_migration_nuova():
    numeri = sorted(
        int(p.name[:3]) for p in (ROOT / "migrations").glob("*.sql")
        if not p.name.endswith("_down.sql") and p.name[:3].isdigit())
    # P29-2.3 non ha introdotto migration, e non lo fa adesso: la 065 e'
    # di P29-2.6E (`contact_id` nullable per le SERVICE senza contatto).
    # Cio' che resta vietato qui e' che una migration nasca DA QUESTA fase,
    # e la 065 non le appartiene.
    assert numeri[-1] == 65, "la serie si e' fermata o e' andata oltre la 065"
    assert 64 in numeri, "la 064 di P29-2.1 non c'e' piu'"


def test_N7_il_claim_non_chiama_nulla_fra_il_lock_e_il_commit():
    """La transazione di claim deve restare BREVE: nessuna chiamata di rete puo'
    starci dentro, e questo e' cio' che lo rende vero per costruzione.

    Si cercano CHIAMATE, non sottostringhe: "send" e' contenuto in "sending",
    che e' lo stato con cui il claim marca il messaggio ed e' quindi legittimo.
    Una sentinella che vietasse la sottostringa vieterebbe il claim stesso - ed
    e' l'errore che questo test ha commesso prima di essere corretto.
    """
    sorgente = inspect.getsource(repository.claim_due)
    # Si toglie OGNI letterale a tre apici, non solo la docstring: restano cosi'
    # le sole chiamate PYTHON. Senza, `NOW()`, `VALUES (` e il nome di una
    # tabella dentro l'SQL verrebbero contati come funzioni chiamate.
    senza_docstring = re.sub(r'\"{3}[\s\S]*?\"{3}', "", sorgente)

    for vietato in (r"\btime\.sleep\s*\(", r"\brequests?\.", r"\bhttpx\.",
                    r"\burlopen\s*\(", r"\bsocket\.", r"\bsleep\s*\("):
        assert not re.search(vietato, senza_docstring), f"claim_due contiene {vietato}"

    # Le sole chiamate del corpo: il cursore, la fabbrica di token, e le
    # utilita' locali. Una qualunque altra funzione qui dentro andrebbe guardata
    # prima di essere ammessa, perche' la transazione e' aperta.
    chiamate = set(re.findall(r"(\w+)\s*\(", senza_docstring))
    ammesse = {"execute", "fetchall", "fetchone", "_row", "token_factory",
               "require_agency", "append", "claim_due", "dict",
               # La sorgente scopata: e' il predicato di agenzia, non una
               # chiamata verso l'esterno, ed e' obbligatoria.
               "communication_scoped_source"}
    assert chiamate <= ammesse, f"chiamate non previste dentro il claim: {chiamate - ammesse}"
