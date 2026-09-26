"""P29-1.1 - le migration 061, 062 e 063, lette come testo e come regola.

Questo file NON tocca un database. Prova che le tre migration sono conformi
alle regole che il runner di P26 fa rispettare, che dicono le stesse cose che
dice il codice del modulo `consent/`, e che non hanno perso nessuna delle
garanzie per cui sono state scritte.

Stessa forma e stessa disciplina di tests/test_p27_1_migration_057.py: ogni
asserzione su un COMPORTAMENTO guarda l'SQL eseguibile, mai i commenti - che
nominano di proposito UPDATE, DELETE e TRUNCATE per spiegare cosa viene
rifiutato, e cercarli nel testo grezzo proverebbe soltanto che qualcuno li ha
scritti in un commento.

L'applicazione vera su PostgreSQL non e' stata eseguita in questa fase: le
credenziali del DB TEST vivono su Render e questa sessione non ha un canale
verso quel database (censimento P29-1.0, sezione A). E' un limite dichiarato,
non aggirato: le sonde DO $do$ dentro le migration sono scritte per fallire
rumorosamente al momento dell'applicazione, e questo file e' cio' che resta a
guardia nella suite.

Mappa:

    M1  le regole del runner: validazione, contiguita', era 027+
    M2  i down: esistono, si brackettano, rimuovono cio' che la up crea
    M3  061 il registro: immutabilita', una sola corrente, legacy onesto
    M4  062 lo storico: append-only, FK, revoca auditabile
    M5  063 la proiezione: additiva, e cosa NON tocca
    M6  l'accordo fra SQL e consent/enums.py
    M7  il perimetro: P29-1.1 non tocca P26/P27/P28 ne' i domini altrui
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from consent.enums import (
    ACTOR_TYPES,
    DECISIONS,
    PROJECTION_COLUMNS,
    PURPOSES,
    PURPOSE_MARKETING,
    PURPOSE_PRIVACY_TERMS,
    REVOKING_ACTOR_TYPES,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"

NOTICES = "061_p29_consent_notices"
EVENTS = "062_p29_consent_events"
PROJECTION = "063_p29_contacts_consent_projection"
VERSIONS = (NOTICES, EVENTS, PROJECTION)


@pytest.fixture(scope="module")
def runner():
    from scripts import p26_migrate

    return p26_migrate


def up_text(version: str) -> str:
    return (MIGRATIONS / f"{version}.sql").read_text(encoding="utf-8")


def down_text(version: str) -> str:
    return (MIGRATIONS / f"{version}_down.sql").read_text(encoding="utf-8")


def executable(runner, text: str) -> str:
    """Il solo SQL eseguibile, senza commenti."""
    return runner.strip_sql_comments(text)


def without_probes(sql: str) -> str:
    """L'SQL eseguibile SENZA i blocchi DO $do$ ... $do$;.

    Le sonde di 060, 061, 062 e 063 scrivono righe vere e le annullano con il
    savepoint implicito di BEGIN ... EXCEPTION. Cercare una INSERT nel testo
    intero direbbe che la migration semina dati, che e' falso: nessuna di quelle
    righe sopravvive al proprio sentinel. Le asserzioni sul SEMINARE guardano
    quindi cio' che resta fuori dalle sonde.
    """
    return re.sub(r"DO \$do\$.*?\$do\$\s*;", "", sql, flags=re.DOTALL)


# ===========================================================================
# M1  le regole del runner
# ===========================================================================
def test_m1_i_tre_file_esistono_con_il_proprio_down():
    for version in VERSIONS:
        assert (MIGRATIONS / f"{version}.sql").is_file(), version
        assert (MIGRATIONS / f"{version}_down.sql").is_file(), version


def test_m1_il_runner_non_solleva_violazioni(runner):
    scoperte = {m.version: m for m in runner.discover_migrations()}
    for version in VERSIONS:
        assert version in scoperte, f"{version} non e' stata scoperta dal runner"
        violazioni = runner.validate_migration(scoperte[version])
        assert violazioni == [], violazioni


def test_m1_l_intera_serie_resta_valida(runner):
    """Non basta che le nuove siano valide: la serie deve restarlo."""
    violazioni = []
    for migration in runner.discover_migrations():
        violazioni.extend(runner.validate_migration(migration))
    assert violazioni == [], violazioni


def test_m1_numerazione_contigua_e_non_sovrascrive_nulla(runner):
    numeri = [m.number for m in runner.discover_migrations()]
    assert numeri == sorted(numeri)
    assert len(numeri) == len(set(numeri)), "due migration con lo stesso numero"
    assert {61, 62, 63} <= set(numeri)
    # P29-1.1 resta in coda a SE STESSA: 061-063 sono le sue, e nessuno si e'
    # infilato fra loro. Il massimo della serie e' salito con le fasi
    # successive (064 di P29-2.1, 065 di P29-2.6E, 066 di LMC-1A, 067 di LMC-1B).
    assert {61, 62, 63} <= set(numeri)
    # ... e 068 di LMC-10.
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
    # SENTINELLA AGGIORNATA DA A30-9A: la 074 crea le fondamenta della
    # sincronizzazione in uscita verso Google Calendar (`calendar_connections`,
    # `calendar_oauth_states`, `appointment_calendar_sync`), approvata dal
    # GATE A30-9A. Si nomina invece di smettere di guardare: qualunque ALTRA
    # migration comparisse farebbe ancora fallire.
    assert max(numeri) == 74, "la serie non e' piu' contigua in coda"


def test_m1_era_027_nessuna_transazione_nel_file_up(runner):
    """Dalla 027 il runner possiede la transazione: la up non la apre."""
    for version in VERSIONS:
        sql = executable(runner, up_text(version))
        assert not re.search(r"^\s*BEGIN\s*;", sql, re.MULTILINE), version
        assert not re.search(r"^\s*COMMIT\s*;", sql, re.MULTILINE), version


def test_m1_la_up_non_tocca_il_ledger(runner):
    for version in VERSIONS:
        sql = executable(runner, up_text(version))
        assert "schema_migrations" not in sql.lower(), version


def test_m1_nessun_concurrently(runner):
    for version in VERSIONS:
        assert not re.search(r"\bCONCURRENTLY\b", executable(runner, up_text(version)), re.I)


# ===========================================================================
# M2  i down
# ===========================================================================
def test_m2_ogni_down_si_bracketta_e_libera_il_ledger(runner):
    for version in VERSIONS:
        sql = executable(runner, down_text(version))
        assert re.search(r"^\s*BEGIN\s*;", sql, re.MULTILINE), version
        assert re.search(r"^\s*COMMIT\s*;", sql, re.MULTILINE), version
        assert f"DELETE FROM schema_migrations WHERE version = '{version}'" in sql, version


def test_m2_i_down_rimuovono_cio_che_le_up_creano(runner):
    coppie = {
        NOTICES: ["consent_notices"],
        EVENTS: ["consent_events"],
    }
    for version, oggetti in coppie.items():
        sql = executable(runner, down_text(version)).lower()
        for oggetto in oggetti:
            assert f"drop table if exists {oggetto}" in sql, (version, oggetto)


def test_m2_il_down_della_proiezione_toglie_le_otto_colonne(runner):
    sql = executable(runner, down_text(PROJECTION)).lower()
    attese = set()
    for colonne in PROJECTION_COLUMNS.values():
        attese.update(colonne.values())
    attese -= {"marketing_consent", "marketing_consent_at"}
    assert len(attese) == 8
    for colonna in attese:
        assert f"drop column if exists {colonna}" in sql, colonna


def test_m2_il_down_della_proiezione_non_tocca_le_colonne_storiche(runner):
    """La regressione piu' costosa possibile, e la piu' facile da introdurre."""
    sql = executable(runner, down_text(PROJECTION)).lower()
    assert "drop column if exists marketing_consent;" not in sql
    assert "drop column if exists marketing_consent_at" not in sql


def test_m2_nessun_down_usa_cascade(runner):
    for version in VERSIONS:
        assert "cascade" not in executable(runner, down_text(version)).lower(), version


def test_m2_i_down_non_cancellano_dati(runner):
    """Un down puo' togliere strutture, non righe di altri."""
    for version in VERSIONS:
        sql = executable(runner, down_text(version)).lower()
        for istruzione in re.findall(r"delete\s+from\s+(\w+)", sql):
            assert istruzione == "schema_migrations", (version, istruzione)
        assert not re.search(r"^\s*truncate\b", sql, re.M | re.I), version
        assert not re.search(r"\bupdate\s+contacts\b", sql), version


# ===========================================================================
# M3  061 - il registro delle notice
# ===========================================================================
def test_m3_la_tabella_esiste_e_non_e_tenant_scoped(runner):
    sql = executable(runner, up_text(NOTICES))
    assert "CREATE TABLE IF NOT EXISTS consent_notices" in sql
    corpo = sql[sql.index("CREATE TABLE IF NOT EXISTS consent_notices"):]
    corpo = corpo[: corpo.index(");")]
    assert "agency_id" not in corpo, (
        "il registro e' di piattaforma: un agency_id ammetterebbe due testi "
        "diversi sotto lo stesso identificativo di versione"
    )


def test_m3_una_sola_versione_corrente_per_purpose(runner):
    sql = executable(runner, up_text(NOTICES)).lower()
    assert "create unique index if not exists consent_notices_current_unq" in sql
    assert "where valid_to is null" in sql, "l'indice deve essere PARZIALE"


def test_m3_purpose_version_unica(runner):
    sql = executable(runner, up_text(NOTICES))
    assert "UNIQUE (purpose, version)" in sql


def test_m3_il_contenuto_e_immutabile(runner):
    sql = executable(runner, up_text(NOTICES))
    assert "CREATE OR REPLACE FUNCTION consent_notices_immutable()" in sql
    assert "BEFORE UPDATE OR DELETE ON consent_notices" in sql
    for colonna in ("content", "content_hash", "purpose", "version", "valid_from", "created_by"):
        assert f"NEW.{colonna}" in sql, colonna


def test_m3_una_notice_pensionata_non_si_riapre(runner):
    sql = executable(runner, up_text(NOTICES))
    assert "OLD.valid_to IS NOT NULL" in sql
    assert "NEW.valid_to IS NULL" in sql


def test_m3_truncate_e_coperto(runner):
    sql = executable(runner, up_text(NOTICES))
    assert "BEFORE TRUNCATE ON consent_notices" in sql
    assert "FOR EACH STATEMENT" in sql


def test_m3_hash_vincolato_nella_forma(runner):
    sql = executable(runner, up_text(NOTICES))
    assert "content_hash ~ '^[0-9a-f]{64}$'" in sql


def test_m3_il_legacy_e_esplicito_e_non_viene_seminato(runner):
    sql = executable(runner, up_text(NOTICES))
    assert "content_available" in sql, (
        "serve un modo onesto di registrare una notice il cui testo non e' noto"
    )
    assert not re.search(
        r"\bINSERT\s+INTO\s+consent_notices\b", without_probes(sql), re.I
    ), "nessuna notice viene seminata: una riga verosimile sarebbe una prova fabbricata"


def test_m3_la_up_non_semina_nulla(runner):
    for version in VERSIONS:
        sql = executable(runner, up_text(version))
        # Gli unici INSERT ammessi sono quelli delle sonde, che vivono dentro un
        # blocco DO ... EXCEPTION e vengono annullati dal proprio sentinel.
        assert not re.findall(r"INSERT\s+INTO\s+(\w+)", without_probes(sql), re.I), version
        if re.search(r"INSERT\s+INTO", sql, re.I):
            assert "PROBE_ROLLBACK" in sql, version


# ===========================================================================
# M4  062 - lo storico degli eventi
# ===========================================================================
def test_m4_tenant_scoped_con_agency_non_nulla(runner):
    sql = executable(runner, up_text(EVENTS))
    assert "agency_id       BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE CASCADE" in sql


def test_m4_append_only_in_entrambe_le_direzioni(runner):
    sql = executable(runner, up_text(EVENTS))
    assert "CREATE OR REPLACE FUNCTION consent_events_append_only()" in sql
    assert "BEFORE UPDATE OR DELETE ON consent_events" in sql
    assert "BEFORE TRUNCATE ON consent_events" in sql


def test_m4_nessuna_eccezione_all_append_only(runner):
    """A differenza di 061 qui non esiste nemmeno una transizione ammessa."""
    sql = executable(runner, up_text(EVENTS))
    corpo = sql[sql.index("FUNCTION consent_events_append_only()"):]
    corpo = corpo[: corpo.index("$fn$;")]
    assert "RETURN NEW" not in corpo, "una via d'uscita renderebbe lo storico riscrivibile"


def test_m4_le_due_fk_sono_cascade_per_il_purge(runner):
    """DECISIONE C1: la cancellazione fisica e' un purge eccezionale.

    RESTRICT avrebbe fatto cadere l'intera transazione del cleanup di agenzia
    di P26-6 al primo contatto con un consenso registrato (lo ha mostrato la
    sentinella test_86i). Le prove che il purge funziona davvero, e che una
    DELETE diretta resta vietata, sono in tests/test_p29_1_consent_postgres.py:
    dipendono da come PostgreSQL esegue il CASCADE e non si possono fare
    leggendo il testo.
    """
    sql = executable(runner, up_text(EVENTS))
    assert "REFERENCES agencies(id) ON DELETE CASCADE" in sql
    assert "REFERENCES contacts(id) ON DELETE CASCADE" in sql
    assert "REFERENCES consent_notices(id) ON DELETE RESTRICT" in sql, (
        "il registro delle notice non e' un soggetto e non e' un parent del "
        "cleanup: una notice accettata non deve sparire"
    )
    assert "ON DELETE SET NULL" not in sql, (
        "SET NULL e' una UPDATE, che una tabella append-only rifiuta (vedi 057)"
    )


def test_m4_i_trigger_sono_enable_always(runner):
    """Un trigger ordinario e' spento da session_replication_role='replica'.

    Misurato durante P29-1.1: con quel parametro la DELETE passava. ENABLE
    ALWAYS chiude la strada, e la prova a runtime e' in
    tests/test_p29_1_consent_postgres.py.
    """
    for version, tabella in ((EVENTS, "consent_events"), (NOTICES, "consent_notices")):
        sql = executable(runner, up_text(version))
        trigger = re.findall(r"CREATE TRIGGER (\w+)", sql)
        assert trigger, version
        for nome in trigger:
            assert f"ENABLE ALWAYS TRIGGER {nome}" in sql, (version, nome)
        assert "tgenabled <> 'A'" in sql, (
            f"{version}: la prova dal catalogo deve esigere ENABLE ALWAYS"
        )


def test_m4_il_guardiano_ha_una_sola_eccezione_e_solo_per_delete(runner):
    sql = executable(runner, up_text(EVENTS))
    corpo = sql[sql.index("FUNCTION consent_events_append_only()"):]
    corpo = corpo[: corpo.index("$fn$;")]
    assert "pg_trigger_depth() > 1" in corpo, "manca la condizione di annidamento"
    assert "NOT EXISTS (SELECT 1 FROM contacts" in corpo, "manca la prova che il genitore sia sparito"
    assert "NOT EXISTS (SELECT 1 FROM agencies" in corpo
    assert corpo.count("RETURN OLD") == 1, "una sola via d'uscita, e una sola"
    assert "TG_OP = 'UPDATE'" in corpo, "l'UPDATE non ha eccezioni"
    for interruttore in ("current_setting", "session_replication_role", "set_config"):
        assert interruttore not in corpo, (
            f"{interruttore}: il guardiano non deve avere un interruttore"
        )


def test_m4_una_revoca_e_sempre_riconducibile(runner):
    sql = executable(runner, up_text(EVENTS))
    assert "consent_events_revoked_actor_chk" in sql
    assert "decision <> 'revoked' OR actor_type IN ('subject', 'operator')" in sql


def test_m4_un_operatore_porta_il_proprio_nome(runner):
    sql = executable(runner, up_text(EVENTS))
    assert "consent_events_operator_ref_chk" in sql


def test_m4_la_provenienza_e_obbligatoria(runner):
    sql = executable(runner, up_text(EVENTS))
    assert "source          VARCHAR(40)  NOT NULL" in sql
    assert "consent_events_source_chk" in sql


def test_m4_la_notice_e_opzionale_per_il_legacy(runner):
    sql = executable(runner, up_text(EVENTS))
    assert "notice_id       BIGINT       REFERENCES consent_notices(id) ON DELETE RESTRICT" in sql
    assert "notice_id       BIGINT       NOT NULL" not in sql


def test_m4_idempotenza_unica(runner):
    sql = executable(runner, up_text(EVENTS))
    assert "idempotency_key VARCHAR(300) UNIQUE" in sql


def test_m4_indice_sulla_regola_di_derivazione(runner):
    sql = executable(runner, up_text(EVENTS)).lower()
    assert "idx_consent_events_current" in sql
    assert "(contact_id, purpose, decided_at desc, id desc)" in sql


# ===========================================================================
# M5  063 - la proiezione
# ===========================================================================
def test_m5_solo_add_column(runner):
    sql = executable(runner, up_text(PROJECTION))
    for istruzione in re.findall(r"ALTER TABLE contacts\s+(\w+)", sql):
        assert istruzione in {"ADD"}, istruzione
    assert "DROP COLUMN" not in sql


def test_m5_le_otto_colonne(runner):
    sql = executable(runner, up_text(PROJECTION))
    attese = set()
    for colonne in PROJECTION_COLUMNS.values():
        attese.update(colonne.values())
    attese -= {"marketing_consent", "marketing_consent_at"}
    assert len(attese) == 8
    for colonna in attese:
        assert f"ADD COLUMN IF NOT EXISTS {colonna}" in sql, colonna


def test_m5_nessuna_colonna_marketing_status(runner):
    """Una seconda colonna sullo stato sarebbe in disaccordo dal primo giorno."""
    sql = executable(runner, up_text(PROJECTION))
    assert "marketing_status" not in sql


def test_m5_nessun_backfill(runner):
    sql = executable(runner, up_text(PROJECTION))
    # L'unico UPDATE ammesso e' quello della sonda, annullata dal sentinel.
    assert not re.search(r"UPDATE\s+contacts", without_probes(sql), re.I)
    assert "P29_063_PROBE_ROLLBACK" in sql
    assert "SET DEFAULT" not in sql.upper(), (
        "un DEFAULT riscriverebbe il significato delle righe esistenti"
    )


def test_m5_le_colonne_storiche_sono_protette_da_una_prova(runner):
    sql = executable(runner, up_text(PROJECTION))
    assert "marketing_consent'," in sql or "'marketing_consent'" in sql
    assert "database_revival" in up_text(PROJECTION), (
        "la migration deve dire ad alta voce chi si romperebbe"
    )


def test_m5_i_tre_check_di_coerenza(runner):
    sql = executable(runner, up_text(PROJECTION))
    for vincolo in (
        "contacts_marketing_state_chk",
        "contacts_privacy_terms_state_chk",
        "contacts_privacy_terms_accepted_chk",
    ):
        assert vincolo in sql, vincolo


def test_m5_nessun_check_sul_granted_at_del_marketing(runner):
    """L'asimmetria e' voluta: contact 12 del censimento resta legacy.

    Un CHECK simmetrico farebbe fallire la migration, oppure obbligherebbe a
    inventare un timestamp per quella riga.
    """
    sql = executable(runner, up_text(PROJECTION))
    assert "marketing_consent IS NOT TRUE OR marketing_consent_at IS NOT NULL" not in sql
    assert "contact_id 12" in up_text(PROJECTION), (
        "la decisione va registrata dove vive la sua conseguenza"
    )


def test_m5_le_fk_verso_il_registro_non_distruggono(runner):
    sql = executable(runner, up_text(PROJECTION))
    assert sql.count("REFERENCES consent_notices(id)") == 2
    assert "ON DELETE CASCADE" not in sql


# ===========================================================================
# M6  SQL ed enums dicono la stessa cosa
# ===========================================================================
def test_m6_i_purposes_coincidono(runner):
    for version in (NOTICES, EVENTS):
        sql = executable(runner, up_text(version))
        trovati = set(re.findall(r"purpose IN \(([^)]*)\)", sql))
        assert trovati, version
        for gruppo in trovati:
            valori = {v.strip().strip("'") for v in gruppo.split(",")}
            assert valori == PURPOSES, (version, valori)


def test_m6_le_decisioni_coincidono(runner):
    sql = executable(runner, up_text(EVENTS))
    gruppo = re.search(r"decision IN \(([^)]*)\)", sql).group(1)
    valori = {v.strip().strip("'") for v in gruppo.split(",")}
    assert valori == DECISIONS


def test_m6_gli_attori_coincidono(runner):
    sql = executable(runner, up_text(EVENTS))
    gruppo = re.search(r"actor_type IN \('subject', 'operator', 'system'\)", sql)
    assert gruppo, "l'elenco degli attori nel DB non coincide con consent/enums.py"
    assert ACTOR_TYPES == {"subject", "operator", "system"}


def test_m6_chi_puo_revocare_coincide(runner):
    sql = executable(runner, up_text(EVENTS))
    gruppo = re.search(r"actor_type IN \('subject', 'operator'\)\)", sql)
    assert gruppo
    assert REVOKING_ACTOR_TYPES == {"subject", "operator"}


def test_m6_i_nomi_delle_colonne_di_proiezione_esistono_nella_migration(runner):
    sql = executable(runner, up_text(PROJECTION))
    legacy = {"marketing_consent", "marketing_consent_at"}
    for purpose, colonne in PROJECTION_COLUMNS.items():
        for ruolo, colonna in colonne.items():
            if colonna in legacy:
                continue
            assert colonna in sql, (purpose, ruolo, colonna)


def test_m6_privacy_terms_non_si_chiama_privacy_consent(runner):
    """Il nome deve dire cosa il funnel raccoglie davvero (decisione 3)."""
    assert PURPOSE_PRIVACY_TERMS == "privacy_terms"
    sql = executable(runner, up_text(PROJECTION))
    assert "privacy_consent" not in sql


def test_m6_il_marketing_resta_unico(runner):
    assert PURPOSE_MARKETING == "marketing"
    for version in VERSIONS:
        sql = executable(runner, up_text(version))
        assert "marketing_email" not in sql, version
        assert "marketing_whatsapp" not in sql, version


# ===========================================================================
# M7  il perimetro
# ===========================================================================
def test_m7_nessuna_migration_tocca_tabelle_di_altri_domini(runner):
    vietate = (
        "activities", "tasks", "leads", "lead_stime", "stime",
        "seller_timeline_events", "next_best_actions", "followup_actions",
        "flow_rules", "flow_events", "flow_executions", "flow_suppressions",
        "platform_audit_log", "agency_memberships", "operator_sessions",
        "seller_revival_suppressions", "whatsapp_incoming",
    )
    for version in VERSIONS:
        sql = executable(runner, up_text(version)).lower()
        for tabella in vietate:
            assert not re.search(rf"\balter\s+table\s+{tabella}\b", sql), (version, tabella)
            assert not re.search(rf"\bdrop\s+table[^;]*\b{tabella}\b", sql), (version, tabella)
            assert not re.search(rf"\bupdate\s+{tabella}\b", sql), (version, tabella)


def test_m7_si_altera_solo_contacts_e_le_tabelle_appena_create(runner):
    """`ALTER TABLE consent_*` compare solo per ENABLE ALWAYS sui propri trigger."""
    alterate = set()
    for version in VERSIONS:
        sql = executable(runner, up_text(version))
        for tabella in re.findall(r"ALTER TABLE (\w+)", sql):
            alterate.add(tabella)
            if tabella != "contacts":
                assert tabella in {"consent_notices", "consent_events"}, tabella
    assert alterate <= {"contacts", "consent_notices", "consent_events"}
    assert "contacts" in alterate

    # Su `contacts` - l'unica tabella preesistente toccata - solo ADD COLUMN.
    sql = executable(runner, up_text(PROJECTION))
    for istruzione in re.findall(r"ALTER TABLE contacts\s+(\w+)", sql):
        assert istruzione == "ADD", istruzione


def test_m7_solo_due_tabelle_vengono_create(runner):
    create = set()
    for version in VERSIONS:
        sql = executable(runner, up_text(version))
        create.update(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql))
    assert create == {"consent_notices", "consent_events"}


def test_m7_il_modulo_consent_non_importa_domini_altrui():
    """Il codice, non solo lo schema: `consent/` conosce CORE e nient'altro."""
    vietati = (
        "followup", "next_best_action", "seller_intelligence", "seller_intent",
        "database_revival", "flow", "platform_admin", "crm", "owner",
    )
    for sorgente in (ROOT / "consent").glob("*.py"):
        testo = sorgente.read_text(encoding="utf-8")
        righe = [r for r in testo.splitlines() if r.startswith(("import ", "from "))]
        for riga in righe:
            for modulo in vietati:
                assert not re.match(rf"(from|import)\s+{modulo}\b", riga), (sorgente.name, riga)
