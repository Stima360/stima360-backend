"""P29-2.1 - la migration 064, letta come testo e come regola.

Questo file NON apre un database. Legge `064_p29_communication_foundation.sql`
e afferma cosa deve contenere e cosa non deve contenere; le proprieta' che
dipendono davvero dal comportamento di PostgreSQL - i trigger che scattano, il
CASCADE che passa, lo UNIQUE che morde - stanno in
tests/test_p29_2_1_communication_postgres.py, che un PostgreSQL vero ce l'ha.

La divisione e' la stessa di P29-1.1 fra test_p29_1_consent_migrations.py e
test_p29_1_consent_postgres.py, e per la stessa ragione: un test di testo prova
che la decisione e' SCRITTA, un test su PostgreSQL prova che FUNZIONA. Servono
entrambi, e nessuno dei due sostituisce l'altro.

Mappa:

    L   il ledger delle migration: 064 esiste, e' contigua, segue 063
    U   una sola migration per due tabelle
    M   communication_messages: colonne, vincoli, tenancy
    A   communication_attempts: audit, non una seconda coda
    G   i guardiani: immutabilita', DELETE, TRUNCATE
    N   cio' che 064 NON fa: nessun seed, nessun runtime, nessuna 065
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"

VERSIONE = "064_p29_communication_foundation"
UP = MIGRATIONS / f"{VERSIONE}.sql"
DOWN = MIGRATIONS / f"{VERSIONE}_down.sql"

MESSAGES = "communication_messages"
ATTEMPTS = "communication_attempts"


def sql_up() -> str:
    return UP.read_text(encoding="utf-8")


def senza_commenti(testo: str) -> str:
    """Solo SQL eseguibile.

    Serve per la stessa ragione per cui e' servito in P29-1.1: i commenti di
    questa migration NOMINANO cio' che vietano - `TRUNCATE`, `DELETE`, i nomi
    delle colonne rimosse - e una ricerca ingenua di quelle stringhe troverebbe
    la prosa invece dello statement, dichiarando vero cio' che non e' stato
    verificato.
    """
    return re.sub(r"--[^\n]*", "", testo)


def senza_sonde(testo: str) -> str:
    """Lo SQL eseguibile meno i blocchi DO, cioe' le prove che la migration fa
    su se stessa.

    Le sonde scrivono righe vere e le annullano, quindi contengono INSERT e
    UPDATE che non sono effetti della migration. Un test che cercasse "questa
    migration non scrive righe" li troverebbe e fallirebbe su una prova di
    correttezza.
    """
    return re.sub(r"DO \$do\$.*?\$do\$;", "", senza_commenti(testo), flags=re.DOTALL)


# ---------------------------------------------------------------------------
# L  Il ledger delle migration
# ---------------------------------------------------------------------------

def test_l1_la_064_esiste_con_il_suo_down():
    assert UP.exists(), f"{VERSIONE}.sql non esiste"
    assert DOWN.exists(), f"{VERSIONE}_down.sql non esiste: ogni migration porta il suo down"


def test_l2_il_runner_la_scopre_e_la_valida():
    from scripts import p26_migrate as runner

    migrazioni = {m.version: m for m in runner.discover_migrations()}
    assert VERSIONE in migrazioni, sorted(migrazioni)[-4:]
    assert runner.validate_migration(migrazioni[VERSIONE]) == []


def test_l3_064_esiste_segue_063_e_non_e_piu_la_piu_alta():
    """La 064 e' e resta la migration di P29-2.1, ma la serie e' andata avanti:
    P29-2.6E ha aggiunto la 065. Cio' che questa sentinella protegge non e' che
    064 sia l'ultima - lo era quando e' stata scritta - ma che esista, che segua
    la 063 e che nessuno si sia infilato fra le due."""
    from scripts import p26_migrate as runner

    numeri = sorted(m.number for m in runner.discover_migrations())
    assert 64 in numeri and 63 in numeri, numeri[-4:]
    # LMC-10 ha aggiunto la 068; LMC-12 la 069 (`owner_home_notifications`,
    # dominio OWNER, approvata dal DESIGN GATE). La piu' alta si nomina, cosi'
    # una migration inattesa fa ancora fallire il test.
    # LMC-15 ha aggiunto la 070 (`stima_acquisitions` e
    # `stima_inspections`, il ponte di acquisizione approvato dallo SCHEMA
    # GATE di LMC-15A.2): dominio ACQUISITION, non di questa fase. La coda
    # si nomina, come sempre.
    # P29-3B ha aggiunto la 071 (la fondazione delle journey: definizioni,
    # iscrizioni, controlli per contatto e la provenienza sul ledger),
    # approvata da P29-3A.1. La coda si nomina, come sempre.
    assert numeri[-1] == 71, numeri[-4:]


def test_l4_il_ledger_resta_contiguo():
    from scripts import p26_migrate as runner

    runner.verify_contiguous(runner.discover_migrations())


def test_l5_la_065_esiste_ma_non_e_di_questa_fase():
    """IL DIVIETO SI E' SPOSTATO, NON E' STATO TOLTO.

    Questa sentinella asseriva che NESSUNA 065 esistesse: era vera finche'
    P29-2.1 era schema foundation e basta, e serviva a impedire che una
    migration pianificata - `delivered_at`, i thread - comparisse prima della
    fase che la giustifica. Quel rischio non e' cambiato: una migration
    applicata prima di essere revisionata resta il difetto da evitare.

    La 065 adesso esiste, e ha una fase che la giustifica: P29-2.6E, che rende
    `contact_id` nullable per le comunicazioni SERVICE senza contatto e da'
    loro la stima come genitore di lifecycle. Cio' che si verifica qui e'
    quindi che sia QUELLA 065 - non `delivered_at`, non i thread, che restano
    non scritti.

    Lo stesso spostamento vale per la 066: LMC-1A l'ha giustificata (il grant
    pre-incarico `owner_stima_access`, dominio OWNER, non P29), quindi la
    sentinella verifica che sia QUELLA 066 e che la 067 continui a non
    esistere. Una 066 o una 067 di P29 - `delivered_at`, i thread - resterebbe
    il difetto da evitare.
    """
    trovate = sorted(p.name for p in MIGRATIONS.glob("065*.sql"))
    assert trovate == ["065_p29_service_lifecycle_parent.sql",
                       "065_p29_service_lifecycle_parent_down.sql"], trovate
    assert sorted(p.name for p in MIGRATIONS.glob("066*.sql")) == [
        "066_lmc1_owner_stima_access.sql",
        "066_lmc1_owner_stima_access_down.sql",
    ], "una 066 diversa da quella di LMC-1A e' comparsa senza la fase che la giustifica"
    assert sorted(p.name for p in MIGRATIONS.glob("067*.sql")) == [
        "067_lmc1b_owner_login_reason.sql",
        "067_lmc1b_owner_login_reason_down.sql",
    ], "una 067 diversa da quella di LMC-1B e' comparsa senza la fase che la giustifica"
    # Lo stesso spostamento, ancora: la 068 esiste e ha una fase che la
    # giustifica, LMC-10, il cui STORAGE GATE l'ha approvata. E' la tabella
    # degli override del proprietario, dominio OWNER e non P29. Una 068 di
    # P29 - `delivered_at`, i thread - resterebbe il difetto da evitare, ed
    # e' quello che le due righe sotto continuano a verificare.
    assert sorted(p.name for p in MIGRATIONS.glob("068*.sql")) == [
        "068_lmc10_owner_home_overrides.sql",
        "068_lmc10_owner_home_overrides_down.sql",
    ], "una 068 diversa da quella di LMC-10 e' comparsa senza la fase che la giustifica"
    up_068 = (MIGRATIONS / "068_lmc10_owner_home_overrides.sql").read_text(encoding="utf-8")
    assert "communication_messages" not in up_068, "la 068 non e' di P29"
    assert "delivered_at" not in up_068 and "thread" not in up_068
    up_066 = (MIGRATIONS / "066_lmc1_owner_stima_access.sql").read_text(encoding="utf-8")
    assert "communication_messages" not in up_066, "la 066 non e' di P29"
    # La 067 TOCCA `communication_messages`, ma non e' una migration di P29:
    # allarga di un valore il CHECK del motivo per un flusso OWNER, e non
    # aggiunge ne' `delivered_at` ne' i thread, che restano non scritti.
    up_067 = (MIGRATIONS / "067_lmc1b_owner_login_reason.sql").read_text(encoding="utf-8")
    assert "delivered_at" not in up_067 and "thread" not in up_067
    assert "CREATE TABLE" not in up_067
    up = (MIGRATIONS / "065_p29_service_lifecycle_parent.sql").read_text(encoding="utf-8")
    assert "delivered_at" not in up, "la 065 anticipa D3, che il design mette DOPO"


def test_l6_il_runner_possiede_la_transazione():
    """Dalla 027 in poi il file non si bracketta: lo fa il runner, insieme alla
    riga di `schema_migrations`. Un file che si brackettasse committerebbe lo
    schema prima del ledger, e i due potrebbero divergere."""
    eseguibile = senza_commenti(sql_up())
    assert not re.search(r"(?m)^\s*BEGIN\s*;", eseguibile)
    assert not re.search(r"(?m)^\s*COMMIT\s*;", eseguibile)


def test_l7_il_down_si_bracketta_e_cancella_la_sua_riga():
    testo = DOWN.read_text(encoding="utf-8")
    assert re.search(r"(?m)^BEGIN;", testo)
    assert re.search(r"(?m)^COMMIT;", testo)
    assert f"DELETE FROM schema_migrations WHERE version = '{VERSIONE}'" in testo


def test_l8_il_down_toglie_i_tentativi_prima_dei_messaggi():
    """La FK composita va dai tentativi al messaggio: senza CASCADE sul DROP, la
    genitrice non si lascia togliere per prima."""
    testo = senza_commenti(DOWN.read_text(encoding="utf-8"))
    pos_att = testo.index(f"DROP TABLE IF EXISTS {ATTEMPTS}")
    pos_msg = testo.index(f"DROP TABLE IF EXISTS {MESSAGES}")
    assert pos_att < pos_msg


def test_l9_il_down_non_usa_cascade():
    """Se qualcosa dipendesse da queste tabelle, il down deve fallire e dirlo."""
    testo = senza_commenti(DOWN.read_text(encoding="utf-8"))
    assert not re.search(r"DROP TABLE[^;]*CASCADE", testo, re.IGNORECASE)


# ---------------------------------------------------------------------------
# U  Una sola migration per due tabelle
# ---------------------------------------------------------------------------

def test_u1_entrambe_le_tabelle_nascono_qui():
    eseguibile = senza_sonde(sql_up())
    assert re.search(rf"CREATE TABLE IF NOT EXISTS {MESSAGES}\b", eseguibile)
    assert re.search(rf"CREATE TABLE IF NOT EXISTS {ATTEMPTS}\b", eseguibile)


def test_u2_nessun_altra_migration_le_crea():
    """Se un'altra migration nominasse queste tabelle in un CREATE, esisterebbero
    due definizioni e una delle due sarebbe sbagliata."""
    for percorso in MIGRATIONS.glob("*.sql"):
        if percorso.name.startswith("064"):
            continue
        testo = senza_commenti(percorso.read_text(encoding="utf-8"))
        for tabella in (MESSAGES, ATTEMPTS):
            # `CREATE TABLE ... {tabella}` e non `CREATE TABLE[^;]*{tabella}`:
            # la forma larga scattava sulla 071, che crea `communication_
            # enrollments` con una FK VERSO communication_messages nello
            # stesso statement - un riferimento, non una seconda definizione.
            # Corretto da P29-3B.
            assert not re.search(rf"CREATE TABLE\s+(IF NOT EXISTS\s+)?{tabella}\b", testo, re.IGNORECASE), (
                f"{percorso.name} crea anche {tabella}"
            )


def test_u3_i_messaggi_sono_definiti_prima_dei_tentativi():
    """La FK composita dei tentativi punta allo UNIQUE dei messaggi: l'ordine non
    e' estetico, e' l'ordine in cui PostgreSQL puo' accettarla."""
    eseguibile = senza_sonde(sql_up())
    assert eseguibile.index(f"CREATE TABLE IF NOT EXISTS {MESSAGES}") < \
           eseguibile.index(f"CREATE TABLE IF NOT EXISTS {ATTEMPTS}")


# ---------------------------------------------------------------------------
# M  communication_messages
# ---------------------------------------------------------------------------

def blocco(nome_tabella: str) -> str:
    """Il corpo del CREATE TABLE di una delle due, senza commenti."""
    eseguibile = senza_sonde(sql_up())
    inizio = eseguibile.index(f"CREATE TABLE IF NOT EXISTS {nome_tabella}")
    return eseguibile[inizio:eseguibile.index(");", inizio)]


COLONNE_MESSAGGIO = [
    ("agency_id", "BIGINT", True), ("contact_id", "BIGINT", True),
    ("lead_id", "BIGINT", False), ("stima_id", "INTEGER", False),
    ("property_id", "BIGINT", False),
    ("channel", "VARCHAR(20)", True), ("direction", "VARCHAR(10)", True),
    ("communication_type", "VARCHAR(20)", True), ("mode", "VARCHAR(20)", True),
    ("reason_code", "VARCHAR(60)", True),
    ("template_key", "VARCHAR(80)", False), ("template_version", "INTEGER", False),
    ("subject_snapshot", "VARCHAR(300)", False),
    ("rendered_body", "TEXT", True), ("destination_snapshot", "VARCHAR(320)", True),
    ("status", "VARCHAR(20)", True), ("failure_class", "VARCHAR(20)", False),
    ("provider", "VARCHAR(40)", False), ("provider_message_id", "VARCHAR(200)", False),
    ("scheduled_at", "TIMESTAMPTZ", True), ("claimed_at", "TIMESTAMPTZ", False),
    ("claim_token", "UUID", False), ("attempt_count", "INTEGER", True),
    ("last_attempt_at", "TIMESTAMPTZ", False), ("sent_at", "TIMESTAMPTZ", False),
    ("failed_at", "TIMESTAMPTZ", False),
    ("error_code", "VARCHAR(80)", False), ("error_detail", "TEXT", False),
    ("suppressed_reason", "VARCHAR(60)", False),
    ("actor_type", "VARCHAR(20)", True), ("actor_user_id", "BIGINT", False),
    ("idempotency_key", "VARCHAR(300)", True), ("metadata", "JSONB", True),
    ("created_at", "TIMESTAMPTZ", True), ("updated_at", "TIMESTAMPTZ", True),
]


def test_m1_le_colonne_del_ledger_sono_quelle_del_design():
    corpo = blocco(MESSAGES)
    for nome, tipo, obbligatoria in COLONNE_MESSAGGIO:
        riga = re.search(rf"(?m)^\s+{nome}\s+([^,\n]*)", corpo)
        assert riga, f"{MESSAGES}.{nome} manca"
        assert tipo in riga.group(1), f"{nome}: atteso {tipo}, trovato {riga.group(1).strip()}"
        if obbligatoria:
            assert "NOT NULL" in riga.group(1), f"{nome} deve essere NOT NULL"


def test_m2_nessuna_colonna_rimandata_e_gia_qui():
    """`delivered_at` e `thread_id` appartengono a 065 e 066. Una colonna che
    nessuno valorizza insegna a non fidarsi delle colonne."""
    corpo = blocco(MESSAGES)
    for rimandata in ("delivered_at", "thread_id"):
        assert not re.search(rf"(?m)^\s+{rimandata}\b", corpo), (
            f"{rimandata} e' rimandata dal design, non deve essere in 064"
        )


def test_m3_la_tenancy_e_una_fk_composita_verso_contacts():
    """Due colonne in un vincolo solo: il contatto esiste E appartiene a
    quell'agenzia. Con due FK separate, la seconda meta' non sarebbe garantita."""
    eseguibile = senza_sonde(sql_up())
    fk = re.search(
        r"FOREIGN KEY \(agency_id, contact_id\)\s*REFERENCES contacts \(agency_id, id\)\s*ON DELETE CASCADE",
        eseguibile,
    )
    assert fk, "la FK composita (agency_id, contact_id) -> contacts(agency_id, id) ON DELETE CASCADE manca"


def test_m4_non_esiste_una_fk_semplice_verso_agencies():
    """Decisione R4, certificata sul catalogo LIVE prima della migration: sarebbe
    ridondante, e costringerebbe a scegliere fra RESTRICT - che bloccherebbe il
    purge - e CASCADE, che duplicherebbe una semantica gia' espressa."""
    corpo = blocco(MESSAGES)
    assert not re.search(r"agency_id[^,\n]*REFERENCES\s+agencies", corpo, re.IGNORECASE)


def test_m5_lo_unique_che_regge_la_fk_dei_tentativi():
    assert re.search(r"UNIQUE \(agency_id, id\)", blocco(MESSAGES))


def test_m6_i_contesti_commerciali_sono_set_null():
    """Eliminare il contesto commerciale non cancella il messaggio scambiato con
    la persona."""
    corpo = blocco(MESSAGES)
    for colonna, tabella in (("lead_id", "leads"), ("stima_id", "stime"),
                             ("property_id", "properties")):
        assert re.search(rf"{colonna}\s+\w+\s+REFERENCES {tabella}\(id\)\s+ON DELETE SET NULL", corpo), (
            f"{colonna} deve essere ON DELETE SET NULL verso {tabella}"
        )


def test_m7_property_id_e_nella_fondazione():
    """C5. Aggiungerla dopo avrebbe richiesto un backfill su righe il cui immobile
    non e' piu' ricostruibile."""
    assert re.search(r"(?m)^\s+property_id\s+BIGINT", blocco(MESSAGES))


def test_m8_lidempotenza_e_per_tenant():
    eseguibile = senza_sonde(sql_up())
    assert re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS uq_communication_messages_idempotency\s*"
        r"ON communication_messages \(agency_id, idempotency_key\)",
        eseguibile,
    ), "lo UNIQUE dell'idempotenza deve essere (agency_id, idempotency_key), non globale"


CHECK_ATTESI = {
    "communication_messages_channel_chk": ["'email'", "'whatsapp'"],
    "communication_messages_direction_chk": ["'outbound'", "'inbound'"],
    "communication_messages_type_chk": ["'service'", "'marketing'"],
    "communication_messages_mode_chk": ["'manual'", "'assisted'", "'automatic'"],
    "communication_messages_status_chk": [
        "'queued'", "'sending'", "'sent'", "'failed'",
        "'indeterminate'", "'suppressed'", "'cancelled'",
    ],
    "communication_messages_failure_class_chk": ["'definite'", "'indeterminate'"],
    "communication_messages_actor_type_chk": ["'system'", "'operator'"],
    "communication_messages_reason_code_chk": [
        "'stima_pdf'", "'operator_manual'", "'operator_reply'",
        "'m1'", "'m2'", "'m3'", "'m4'", "'m5'",
    ],
}


def test_m9_gli_insiemi_chiusi_sono_esattamente_quelli_del_design():
    corpo = blocco(MESSAGES)
    for vincolo, valori in CHECK_ATTESI.items():
        clausola = re.search(rf"CONSTRAINT {vincolo}\s*CHECK \(([^)]*\))", corpo)
        assert clausola, f"{vincolo} manca"
        testo = clausola.group(1)
        for valore in valori:
            assert valore in testo, f"{vincolo}: manca {valore}"


def test_m10_nessuno_stato_futuro_per_comodita():
    """`scheduled`, `delivered` e `draft` sono stati che il design ha rimandato
    con una ragione ciascuno. Ammetterli adesso significherebbe avere righe che
    nessun codice produce e che ogni lettura deve ricordarsi di escludere."""
    clausola = re.search(
        r"CONSTRAINT communication_messages_status_chk\s*CHECK \(([^)]*\))", blocco(MESSAGES)
    ).group(1)
    for rimandato in ("'scheduled'", "'delivered'", "'draft'"):
        assert rimandato not in clausola, f"{rimandato} e' rimandato dal design"


def test_m11_i_vincoli_di_coerenza_del_claim():
    """C13. Le due colonne del claim stanno insieme, e il token esiste se e solo
    se il messaggio e' reclamato: un token su un messaggio finalizzato sarebbe
    una chiave che apre una porta chiusa, e il compare-and-set potrebbe riuscire
    due volte."""
    corpo = blocco(MESSAGES)
    assert re.search(r"CHECK \(\(claim_token IS NULL\) = \(claimed_at IS NULL\)\)", corpo)
    assert re.search(r"CHECK \(\(status = 'sending'\) = \(claim_token IS NOT NULL\)\)", corpo)


def test_m12_gli_altri_vincoli_di_coerenza():
    corpo = blocco(MESSAGES)
    # un insuccesso dice quale, e nessun altro stato porta quella classificazione
    assert re.search(
        r"CHECK \(\(status IN \('failed', 'indeterminate'\)\) = \(failure_class IS NOT NULL\)\)", corpo)
    # un atto di un operatore porta il nome dell'operatore
    assert re.search(r"CHECK \(\(actor_type = 'operator'\) = \(actor_user_id IS NOT NULL\)\)", corpo)
    # le due meta' dell'identita' del template
    assert re.search(r"CHECK \(\(template_key IS NULL\) = \(template_version IS NULL\)\)", corpo)
    # l'oggetto esiste per le email e non per WhatsApp
    assert re.search(r"CHECK \(\(channel = 'email'\) = \(subject_snapshot IS NOT NULL\)\)", corpo)
    # tre tentativi al massimo
    assert re.search(r"CHECK \(attempt_count BETWEEN 0 AND 3\)", corpo)


def test_m13_gli_indici_del_design():
    eseguibile = senza_sonde(sql_up())
    for indice in (
        "idx_communication_messages_contact",
        "idx_communication_messages_da_inviare",
        "idx_communication_messages_in_corso",
        "uq_communication_messages_provider_message_id",
        "idx_communication_messages_property",
    ):
        assert indice in eseguibile, f"manca l'indice {indice}"
    # i tre parziali restano piccoli quanto la coda, non quanto lo storico
    assert "WHERE status IN ('queued', 'failed')" in eseguibile
    assert "WHERE status = 'sending'" in eseguibile
    assert "WHERE provider_message_id IS NOT NULL" in eseguibile


# ---------------------------------------------------------------------------
# A  communication_attempts
# ---------------------------------------------------------------------------

COLONNE_TENTATIVO = [
    ("agency_id", "BIGINT", True), ("message_id", "BIGINT", True),
    ("attempt_no", "INTEGER", True), ("claim_token", "UUID", True),
    ("provider", "VARCHAR(40)", True), ("started_at", "TIMESTAMPTZ", True),
    ("finished_at", "TIMESTAMPTZ", False), ("outcome", "VARCHAR(20)", True),
    ("provider_message_id", "VARCHAR(200)", False),
    ("failure_class", "VARCHAR(20)", False),
    ("error_code", "VARCHAR(80)", False), ("error_detail", "TEXT", False),
    ("late_result", "BOOLEAN", True), ("recovered_at", "TIMESTAMPTZ", False),
    ("created_at", "TIMESTAMPTZ", True),
]


def test_a1_le_colonne_del_tentativo_sono_quelle_del_design():
    corpo = blocco(ATTEMPTS)
    for nome, tipo, obbligatoria in COLONNE_TENTATIVO:
        riga = re.search(rf"(?m)^\s+{nome}\s+([^,\n]*)", corpo)
        assert riga, f"{ATTEMPTS}.{nome} manca"
        assert tipo in riga.group(1), f"{nome}: atteso {tipo}, trovato {riga.group(1).strip()}"
        if obbligatoria:
            assert "NOT NULL" in riga.group(1), f"{nome} deve essere NOT NULL"


def test_a2_late_result_e_recovered_at_esistono():
    """Le due colonne che rendono raccontabile il risultato tardivo: `late_result`
    distingue la riga che lo porta, `recovered_at` segna quando la recovery ha
    chiuso il tentativo originale."""
    corpo = blocco(ATTEMPTS)
    assert re.search(r"(?m)^\s+late_result\s+BOOLEAN\s+NOT NULL\s+DEFAULT FALSE", corpo)
    assert re.search(r"(?m)^\s+recovered_at\s+TIMESTAMPTZ", corpo)


def test_a3_nessun_metadata_jsonb_sui_tentativi():
    """C14 l'ha rimossa esplicitamente: una JSONB vuota su ogni riga e' un invito
    a metterci dati che avrebbero dovuto essere colonne."""
    assert not re.search(r"(?m)^\s+metadata\b", blocco(ATTEMPTS))


def test_a4_lo_unique_porta_tre_colonne():
    """A due, il risultato tardivo non potrebbe essere registrato e il worker
    sopravvissuto resterebbe senza alcun modo di dire cosa ha visto."""
    assert re.search(r"UNIQUE \(message_id, attempt_no, late_result\)", blocco(ATTEMPTS))


def test_a5_la_fk_verso_i_messaggi_e_composita_e_cascade():
    eseguibile = senza_sonde(sql_up())
    assert re.search(
        r"FOREIGN KEY \(agency_id, message_id\)\s*"
        r"REFERENCES communication_messages \(agency_id, id\)\s*ON DELETE CASCADE",
        eseguibile,
    ), "un tentativo non deve poter puntare a un messaggio di un'altra agenzia"


def test_a6_i_vincoli_di_coerenza_del_tentativo():
    corpo = blocco(ATTEMPTS)
    assert re.search(
        r"CHECK \(outcome IN \('in_progress', 'accepted', 'rejected', 'indeterminate'\)\)", corpo)
    # un tentativo aperto non ha una fine, uno chiuso ce l'ha
    assert re.search(r"CHECK \(\(outcome = 'in_progress'\) = \(finished_at IS NULL\)\)", corpo)
    # un successo non ha una classe di fallimento
    assert re.search(r"CHECK \(outcome <> 'accepted' OR failure_class IS NULL\)", corpo)
    # un insuccesso deve dire quale
    assert re.search(
        r"CHECK \(outcome NOT IN \('rejected', 'indeterminate'\) OR failure_class IS NOT NULL\)", corpo)
    assert re.search(r"CHECK \(attempt_no >= 1\)", corpo)


def test_a7_i_tentativi_non_sono_una_coda():
    """Nessun indice su questa tabella seleziona lavoro da eseguire per
    `scheduled_at`: i due che esistono servono a leggere i tentativi di un
    messaggio e a trovare quelli ancora aperti per la recovery."""
    eseguibile = senza_sonde(sql_up())
    indici = re.findall(r"CREATE (?:UNIQUE )?INDEX[^;]*ON communication_attempts[^;]*;", eseguibile)
    assert indici, "i tentativi hanno almeno gli indici del design"
    for indice in indici:
        assert "scheduled_at" not in indice, (
            "un indice di scheduling su communication_attempts la renderebbe una seconda coda"
        )
    assert not re.search(r"(?m)^\s+(scheduled_at|status)\s+", blocco(ATTEMPTS)), (
        "i tentativi non hanno ne' schedulazione ne' uno stato di coda"
    )


# ---------------------------------------------------------------------------
# G  I guardiani
# ---------------------------------------------------------------------------

TRIGGER_ATTESI = (
    ("trg_communication_messages_guard", MESSAGES, "communication_messages_guard"),
    ("trg_communication_attempts_guard", ATTEMPTS, "communication_attempts_guard"),
    ("trg_communication_messages_no_truncate", MESSAGES, "communication_messages_no_truncate"),
    ("trg_communication_attempts_no_truncate", ATTEMPTS, "communication_attempts_no_truncate"),
)


def test_g1_i_quattro_trigger_esistono():
    eseguibile = senza_sonde(sql_up())
    for nome, tabella, funzione in TRIGGER_ATTESI:
        assert f"CREATE TRIGGER {nome}" in eseguibile, f"manca {nome}"
        assert f"EXECUTE FUNCTION {funzione}()" in eseguibile


def test_g2_tutti_e_quattro_sono_enable_always():
    """Un trigger ordinario (tgenabled='O') non scatta quando
    session_replication_role e' 'replica': esiste cioe' un parametro di sessione
    che lo spegne. Misurato in P29-1.1, e qui la forma e' la stessa."""
    eseguibile = senza_sonde(sql_up())
    for nome, tabella, _ in TRIGGER_ATTESI:
        assert re.search(rf"ALTER TABLE {tabella}\s*ENABLE ALWAYS TRIGGER {nome}", eseguibile), (
            f"{nome} non e' ENABLE ALWAYS"
        )


def test_g3_i_guardiani_scattano_prima_e_non_su_insert():
    eseguibile = senza_sonde(sql_up())
    for nome, tabella, _ in TRIGGER_ATTESI[:2]:
        assert re.search(rf"CREATE TRIGGER {nome}\s*BEFORE UPDATE OR DELETE ON {tabella}", eseguibile)
    for nome, tabella, _ in TRIGGER_ATTESI[2:]:
        assert re.search(rf"CREATE TRIGGER {nome}\s*BEFORE TRUNCATE ON {tabella}", eseguibile)


def test_g4_il_ledger_protegge_identita_e_snapshot():
    """C12: il template puo' cambiare domani, la riga storica no."""
    funzione = sql_up()[sql_up().index("FUNCTION communication_messages_guard()"):]
    funzione = funzione[:funzione.index("$fn$;")]
    for colonna in ("agency_id", "contact_id", "channel", "communication_type", "mode",
                    "reason_code", "rendered_body", "subject_snapshot",
                    "destination_snapshot", "idempotency_key", "created_at"):
        assert f"NEW.{colonna}" in funzione, f"{colonna} deve essere immutabile dopo l'INSERT"


def test_g5_lo_stato_resta_mutabile():
    """Non append-only: il messaggio cambia stato, ed e' il suo mestiere. Un
    guardiano che vietasse ogni UPDATE renderebbe impossibile il dispatch."""
    funzione = sql_up()[sql_up().index("FUNCTION communication_messages_guard()"):]
    funzione = funzione[:funzione.index("$fn$;")]
    for mutabile in ("NEW.status", "NEW.claim_token", "NEW.attempt_count", "NEW.sent_at"):
        assert mutabile not in funzione, (
            f"{mutabile} e' dichiarato mutabile dal design: il dispatch deve poterlo scrivere"
        )


def test_g6_il_tentativo_chiuso_non_si_riapre():
    """Il vincolo che rende onesto il modello del risultato tardivo."""
    funzione = sql_up()[sql_up().index("FUNCTION communication_attempts_guard()"):]
    funzione = funzione[:funzione.index("$fn$;")]
    assert "OLD.outcome <> 'in_progress'" in funzione
    assert "cannot be reopened" in funzione


def test_g7_leccezione_al_delete_e_solo_il_purge():
    """Due condizioni, entrambe necessarie. La prima da sola sarebbe fragile - un
    trigger futuro qualsiasi alzerebbe la profondita' - la seconda da sola non
    direbbe che la cancellazione e' PARTE di quel comando."""
    eseguibile = senza_sonde(sql_up())
    assert eseguibile.count("pg_trigger_depth() > 1") == 2, (
        "entrambi i guardiani devono usare la guardia purge-aware"
    )
    assert "NOT EXISTS (SELECT 1 FROM contacts WHERE id = OLD.contact_id)" in eseguibile
    assert "NOT EXISTS (SELECT 1 FROM communication_messages WHERE id = OLD.message_id)" in eseguibile


def test_g8_nessun_interruttore_di_sessione():
    """Nessun GUC, nessun SET LOCAL, nessuna variabile applicativa apre i
    guardiani: pg_trigger_depth() non e' assegnabile e l'esistenza del genitore
    si legge, non si dichiara."""
    eseguibile = senza_commenti(sql_up())
    # Cio' che va vietato e' l'ASSEGNAZIONE di un parametro, non la menzione: il
    # nome `session_replication_role` compare legittimamente nel messaggio della
    # prova sul catalogo, che spiega perche' ENABLE ALWAYS e' obbligatorio.
    for scorciatoia in ("current_setting", "set_config"):
        assert scorciatoia not in eseguibile, (
            f"{scorciatoia} nello SQL eseguibile sarebbe una porta apribile da fuori"
        )
    assert not re.search(r"SET\s+(LOCAL\s+)?session_replication_role", eseguibile, re.IGNORECASE)
    assert "IF pg_trigger_depth() > 1" in eseguibile, (
        "la guardia si legge dal motore, non da un flag dichiarato dal chiamante"
    )


def test_g9_la_migration_prova_se_stessa_sul_catalogo():
    """Le prove rileggono pg_trigger, pg_constraint e pg_index: una FK composita
    degradata a semplice, o un trigger non ENABLE ALWAYS, farebbero fallire la
    migration invece di farsi scoprire il giorno in cui contano."""
    testo = sql_up()
    for catalogo in ("pg_trigger", "pg_constraint", "pg_index", "pg_attribute"):
        assert catalogo in testo, f"la migration non rilegge {catalogo}"
    assert "tgenabled" in testo
    assert "confdeltype" in testo


# ---------------------------------------------------------------------------
# N  Cio' che 064 NON fa
# ---------------------------------------------------------------------------

def test_n1_nessun_seed_e_nessun_backfill():
    """Schema puro. Gli INSERT e gli UPDATE che restano stanno tutti dentro le
    sonde, che scrivono righe vere e le annullano con un sentinel."""
    senza = senza_sonde(sql_up())
    assert not re.search(r"\bINSERT\s+INTO\b", senza, re.IGNORECASE)
    # `UPDATE <tabella> SET`, e non il semplice `UPDATE`: `BEFORE UPDATE OR
    # DELETE` dichiara un trigger, non scrive una riga.
    assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", senza, re.IGNORECASE)
    assert not re.search(r"\bDELETE\s+FROM\b", senza, re.IGNORECASE)
    assert "P29_064_PROBE_ROLLBACK" in sql_up(), "le sonde devono annullarsi con il loro sentinel"


def test_n2_nessuna_tabella_preesistente_viene_alterata():
    """Le sole ALTER TABLE sono gli ENABLE ALWAYS sulle due tabelle nuove."""
    for alter in re.findall(r"ALTER TABLE (\w+)", senza_sonde(sql_up())):
        assert alter in (MESSAGES, ATTEMPTS), f"064 altera {alter}, che non ha creato"


def test_n3_nessuna_tabella_extra():
    """Niente outbox separata, niente thread: il design li ha esclusi da 064."""
    creati = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", senza_sonde(sql_up())))
    assert creati == {MESSAGES, ATTEMPTS}, creati
    eseguibile = senza_commenti(sql_up())
    for vietata in ("communication_outbox", "communication_threads"):
        assert vietata not in eseguibile


#: I sorgenti applicativi del pacchetto `communication/`, senza i loro commenti
#: e le loro docstring. Cio' che una sentinella di confine deve giudicare e' il
#: CODICE: un commento che spiega perche' il claim NON e' qui nomina il claim, e
#: una ricerca ingenua lo scambierebbe per il claim.
NUCLEO = frozenset({
    "__init__.py", "database.py", "enums.py", "exceptions.py",
    "repository.py", "scope.py", "service.py",
})


def sorgenti_communication() -> dict[str, str]:
    """Il NUCLEO DB-safe del pacchetto, senza commenti e senza docstring.

    Si guardano i file del nucleo e SOLO quelli. Il confine si e' spostato una
    terza volta con P29-2.4, che introduce legittimamente `dispatcher.py`,
    `providers/`, `schemas.py` e `router.py`: applicare a quei file i divieti
    delle fasi precedenti vieterebbe la fase che li possiede. Si noti anche che
    indicizzare per `percorso.name` su un `rglob` faceva collidere
    `communication/__init__.py` con `communication/providers/__init__.py`: uno
    dei due sarebbe sparito dall'esame. Il nucleo e' piatto, e la collisione non
    puo' piu' avvenire.
    """
    pacchetto = ROOT / "communication"
    if not pacchetto.exists():
        return {}
    sorgenti = {}
    for percorso in sorted(pacchetto.glob("*.py")):
        if percorso.name not in NUCLEO:
            continue
        testo = percorso.read_text(encoding="utf-8")
        senza_docstring = re.sub(r'"""(.*?)"""', "", testo, flags=re.DOTALL)
        sorgenti[percorso.name] = re.sub(r"#[^\n]*", "", senza_docstring)
    return sorgenti


def test_n4_il_confine_fra_le_fasi_del_dominio():
    """IL PERNO SI E' SPOSTATO, NON E' STATO TOLTO.

    Questa sentinella asseriva `not (ROOT / "communication").exists()`: era vera
    finche' P29-2.1 era schema puro, e ha smesso di esserlo con P29-2.2, che
    introduce legittimamente repository e service. Toglierla del tutto avrebbe
    lasciato il confine fra le fasi senza nessun guardiano; lasciarla com'era
    avrebbe prodotto un fallimento a ogni fase successiva.

    Il confine si e' poi spostato una seconda volta, con P29-2.3, che introduce
    legittimamente claim, fencing, tentativi e recovery. Quello che protegge
    adesso e':

        P29-2.2  repository e service
        P29-2.3  claim, fencing, tentativi, recovery - tutto dentro il database
        P29-2.4  il gate del consenso e il dispatcher
        P29-2.5  i provider reali

    Il modulo puo' esistere e puo' reclamare. Cio' che non puo' esistere qui e'
    qualcosa che DECIDA se mandare o che MANDI: il confine non e' piu' fra due
    fasi di codice, e' fra il database e il mondo.
    """
    pacchetto = ROOT / "communication"
    if not pacchetto.exists():
        # P29-2.2 non e' ancora stata implementata: nulla da sorvegliare, e il
        # confine e' banalmente rispettato.
        return

    # 1. `templates.py` appartiene a P29-2.5 e non esiste ancora. `dispatcher.py`
    #    e `providers/` stavano in questa lista finche' P29-2.4 non era
    #    implementata: adesso esistono per progetto, e il divieto si e' spostato
    #    sul nucleo, che non li conosce.
    # SENTINELLA AGGIORNATA DA P29-3B.2A: `templates.py` ORA ESISTE, per
    # progetto - il registry versionato e immutabile (P29-3A.1 SS I), senza
    # testi commerciali. Il divieto che questa fase manteneva non era sul
    # file ma sul NUCLEO, che continua a non importarlo: e' quello che si
    # verifica qui sotto.
    assert (pacchetto / "templates.py").exists()
    for nome, corpo in sorgenti_communication().items():
        assert "templates" not in corpo, f"il nucleo ({nome}) conosce il registry"
    assert set(sorgenti_communication()) == set(NUCLEO), (
        "il nucleo DB-safe non e' intero: la sentinella esaminerebbe meno di "
        "quanto crede"
    )

    sorgenti = sorgenti_communication()
    assert sorgenti, "il pacchetto communication/ non ha sorgenti leggibili"

    # 2. IL CONFINE SI E' SPOSTATO DI NUOVO, E QUESTA E' LA SECONDA VOLTA.
    #
    #    P29-2.2 vietava qui claim, fencing, tentativi e stale recovery. P29-2.3
    #    li introduce legittimamente: sono il runtime DB-safe, e vietarli adesso
    #    vieterebbe la fase che li possiede.
    #
    #    Il confine nuovo:
    #
    #        P29-2.3  claim, fencing, tentativi, recovery - SENZA RETE
    #        P29-2.4  il gate del consenso e il dispatcher
    #        P29-2.5  i provider reali
    #
    #    Cio' che resta vietato e' quindi tutto cio' che sta OLTRE il database:
    #    chi decide se mandare, e chi manda.

    # 3. Nessun dispatcher: nessuna funzione orchestra claim -> gate -> provider.
    for nome, codice in sorgenti.items():
        for orchestrazione in ("def dispatch", "def run_dispatch", "def send_",
                               "def deliver"):
            # SENTINELLA AGGIORNATA DA P29-3D: `send_now` NON spedisce. E'
            # una UPDATE su `scheduled_at` di un messaggio gia' in coda -
            # sposta il QUANDO, non il SE - e il dispatcher resta l'unico
            # che parla con un provider. Il divieto resta su cio' che manda
            # davvero, e il punto 4 qui sotto (nessuna rete nel nucleo) non
            # si tocca: e' quello che lo rende impossibile e non solo
            # vietato.
            if nome == "service.py" and orchestrazione == "def send_":
                assert "def send_now" in codice
                continue
            assert orchestrazione not in codice, (
                f"communication/{nome} contiene {orchestrazione!r}: il dispatcher "
                "e' P29-2.4"
            )

    # 4. Nessuna rete. E' il primo dei cinque confini del design, ed e' cio' che
    #    rende IMPOSSIBILE - non solo vietato - che P29-2.2 mandi un messaggio.
    for nome, codice in sorgenti.items():
        for rete in ("requests", "smtplib", "httpx", "urllib", "http.client",
                     "socket", "aiohttp"):
            assert rete not in codice, (
                f"communication/{nome} nomina {rete!r}: P29-2.2 non ha rete"
            )

    # 5. Nessun provider, nessun sender, nessuno scheduler.
    #    `provider` resta una ETICHETTA che finisce in una colonna: dice CHI
    #    e' stato chiamato, e in questa fase non si chiama nessuno.
    for nome, codice in sorgenti.items():
        # `scheduled_at` NON e' uno scheduler: e' la colonna "non prima di"
        # della 064, che `enqueue` scrive legittimamente. Si vietano i
        # costrutti di pianificazione, non la parola.
        for vietato in ("invia_mail", "invia_whatsapp", "send_template",
                        "ProviderResult", "ProviderCapabilities",
                        "graph.facebook", "WHATSAPP_", "SMTP_",
                        "APScheduler", "BackgroundScheduler", "Celery",
                        "crontab", "schedule.every"):
            assert vietato not in codice, (
                f"communication/{nome} nomina {vietato!r}: P29-2.2 non manda e "
                "non pianifica niente"
            )

    # 6. Il consenso non si interroga qui: il gate sta immediatamente prima del
    #    dispatch, che e' P29-2.4.
    for nome, codice in sorgenti.items():
        assert "can_send_marketing" not in codice, nome
        assert "marketing_consent" not in codice, nome

    # 7. FUORI dal pacchetto, nessun sorgente applicativo nomina le due tabelle.
    #    E' la meta' di questa sentinella che non e' cambiata: il ledger ha una
    #    via di scrittura sola, e quella via e' `communication/`.
    for percorso in ROOT.rglob("*.py"):
        parti = percorso.parts
        if any(p in parti for p in (".venv", "__pycache__", "tests", "scripts")):
            continue
        if "communication" in parti:
            continue
        testo = percorso.read_text(encoding="utf-8")
        for tabella in (MESSAGES, ATTEMPTS):
            assert tabella not in testo, (
                f"{percorso.relative_to(ROOT)} nomina {tabella} fuori dal "
                "dominio: il ledger ha una via di scrittura sola"
            )


def test_n5_nessun_invio_e_stato_toccato():
    """Le funzioni di invio esistenti restano esattamente dove sono."""
    database_py = (ROOT / "database.py").read_text(encoding="utf-8")
    assert "def invia_mail(destinatario, oggetto, corpo_html, allegato=None):" in database_py
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "def invia_whatsapp(numero: str | None, p1: str, p2: str, p3: str):" in main_py
    assert "def invia_whatsapp_text(numero: str, testo: str):" in main_py


def test_n6_il_consent_domain_non_e_stato_toccato():
    """P29-1.5 resta quello certificato: 064 non lo nomina e non lo modifica."""
    eseguibile = senza_commenti(sql_up())
    for tabella in ("consent_events", "consent_notices"):
        assert tabella not in eseguibile
    guardia = (ROOT / "consent" / "guard.py").read_text(encoding="utf-8")
    assert "def can_send_marketing(ctx, contact_id: int) -> MarketingSendDecision:" in guardia
