"""P29-2.6E / 065 - il genitore di lifecycle, letto dal testo della migration.

Il comportamento sta in `tests/test_p29_2_6e_lifecycle_postgres.py`, che un
database vero ce l'ha. Qui vive cio' che si prova leggendo, e due cose che
DEVONO provarsi leggendo:

  * che il ramo UPDATE del guard riscritto protegga le STESSE dodici colonne
    della 064 - sostituire una funzione la riscrive tutta, e una colonna persa
    non produce nessun errore, solo un'immutabilita' che non c'e' piu';
  * che i percorsi di cancellazione verso `communication_messages` restino i
    tre certificati. L'allentamento del guard per le righe senza contatto regge
    su quell'enumerazione: se qualcuno aggiunge una FK verso questa tabella,
    l'enumerazione cambia sotto i piedi del guard e nessun test di
    comportamento se ne accorgerebbe.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
UP = MIGRAZIONI / "065_p29_service_lifecycle_parent.sql"
DOWN = MIGRAZIONI / "065_p29_service_lifecycle_parent_down.sql"
UP_064 = MIGRAZIONI / "064_p29_communication_foundation.sql"


def senza_commenti(sql: str) -> str:
    return re.sub(r"(?m)^\s*--.*$", "", sql)


def ramo_update(sql: str) -> str:
    """Il corpo del ramo `TG_OP = 'UPDATE'` di `communication_messages_guard`."""
    inizio = sql.index("CREATE OR REPLACE FUNCTION communication_messages_guard()")
    corpo = sql[inizio:]
    apertura = corpo.index("IF TG_OP = 'UPDATE' THEN")
    chiusura = corpo.index("RETURN NEW;", apertura)
    return corpo[apertura:chiusura]


#: Le dodici colonne che C6 rende immutabili. NON si scrivono a mano qui: si
#: estraggono dalla 064, cosi' che l'elenco atteso sia quello certificato e non
#: quello che chi scrive il test si ricorda.
def colonne_immutabili(sql: str) -> list[str]:
    return re.findall(r"NEW\.(\w+)\s+IS DISTINCT FROM OLD\.\1", ramo_update(sql))


# ---------------------------------------------------------------------------
# A  La migration esiste, ed e' disciplinata come le altre
# ---------------------------------------------------------------------------

def test_A1_up_e_down_esistono_e_il_runner_le_vede():
    from scripts import p26_migrate as runner

    assert UP.exists() and DOWN.exists()
    migrazioni = {m.version: m for m in runner.discover_migrations()}
    assert "065_p29_service_lifecycle_parent" in migrazioni
    assert runner.validate_migration(
        migrazioni["065_p29_service_lifecycle_parent"]) == []


def test_A2_la_up_non_apre_la_transazione():
    """Dalla 027 il runner la possiede."""
    eseguibile = senza_commenti(UP.read_text(encoding="utf-8"))
    assert not re.search(r"(?m)^\s*BEGIN\s*;", eseguibile)
    assert not re.search(r"(?m)^\s*COMMIT\s*;", eseguibile)


def test_A3_la_down_si_bracketta_e_si_cancella_dal_ledger():
    eseguibile = senza_commenti(DOWN.read_text(encoding="utf-8"))
    assert re.search(r"(?m)^\s*BEGIN\s*;", eseguibile)
    assert re.search(r"(?m)^\s*COMMIT\s*;", eseguibile)
    assert ("DELETE FROM schema_migrations WHERE version = "
            "'065_p29_service_lifecycle_parent'") in eseguibile


def test_A4_la_up_non_crea_tabelle_e_non_scrive_righe():
    eseguibile = senza_commenti(UP.read_text(encoding="utf-8"))
    for vietato in ("CREATE TABLE", "INSERT INTO", "UPDATE ", "DROP TABLE"):
        assert vietato not in eseguibile, f"la 065 contiene {vietato}"
    # L'unico DELETE e' quello del trigger, dentro la sua funzione.
    assert eseguibile.count("DELETE FROM") == 1


def test_A5_la_066_non_e_di_p29():
    """P29-2.6E finisce con la 065. La 066 esiste, ma e' di LMC-1A (il grant
    pre-incarico `owner_stima_access`, dominio OWNER): qui si verifica che sia
    quella e che non tocchi il ledger delle comunicazioni."""
    assert sorted(p.name for p in MIGRAZIONI.glob("066*.sql")) == [
        "066_lmc1_owner_stima_access.sql",
        "066_lmc1_owner_stima_access_down.sql",
    ]
    up_066 = (MIGRAZIONI / "066_lmc1_owner_stima_access.sql").read_text(encoding="utf-8")
    assert "communication_messages" not in up_066
    assert "communication_attempts" not in up_066


# ---------------------------------------------------------------------------
# B  L'immutabilita' non e' stata persa nella riscrittura
# ---------------------------------------------------------------------------

def test_B1_le_colonne_immutabili_sono_quelle_della_064():
    """LA prova che questa fase deve avere.

    Non "esiste un trigger": le colonne protette, una per una, confrontate con
    il testo della 064. Uguaglianza e ordine, perche' una colonna sparita in
    mezzo e' esattamente cio' che si vuole vedere.
    """
    attese = colonne_immutabili(UP_064.read_text(encoding="utf-8"))
    trovate = colonne_immutabili(UP.read_text(encoding="utf-8"))

    assert len(attese) == 12, f"la 064 ne protegge {len(attese)}, non 12"
    assert trovate == attese, (
        "il ramo UPDATE del guard 065 non protegge le stesse colonne della 064: "
        f"mancanti={sorted(set(attese) - set(trovate))} "
        f"aggiunte={sorted(set(trovate) - set(attese))}"
    )


def test_B2_il_ramo_update_e_testualmente_equivalente():
    """Non solo le colonne: anche il messaggio d'errore e la forma."""
    a = re.sub(r"\s+", " ", ramo_update(UP_064.read_text(encoding="utf-8"))).strip()
    b = re.sub(r"\s+", " ", ramo_update(UP.read_text(encoding="utf-8"))).strip()
    assert a == b, "il ramo UPDATE e' stato riscritto, non ricopiato"


def test_B3_anche_la_down_ripristina_le_dodici_colonne():
    attese = colonne_immutabili(UP_064.read_text(encoding="utf-8"))
    assert colonne_immutabili(DOWN.read_text(encoding="utf-8")) == attese


def test_B4_la_down_ripristina_il_guard_con_una_sola_domanda():
    """Dopo il down `contact_id` e' di nuovo NOT NULL, quindi la domanda a un
    genitore solo torna ad avere sempre una risposta."""
    eseguibile = senza_commenti(DOWN.read_text(encoding="utf-8"))
    assert ("v_genitore_sparito :=\n        NOT EXISTS (SELECT 1 FROM contacts "
            "WHERE id = OLD.contact_id);") in eseguibile
    assert "OLD.contact_id IS NULL" not in eseguibile.split("v_genitore_sparito")[1]


# ---------------------------------------------------------------------------
# C  Il DELETE diretto resta vietato, e l'allentamento e' quello dichiarato
# ---------------------------------------------------------------------------

def test_C1_la_profondita_resta_la_prima_meta_della_congiunzione():
    eseguibile = senza_commenti(UP.read_text(encoding="utf-8"))
    assert "IF pg_trigger_depth() > 1 AND v_genitore_sparito THEN" in eseguibile
    assert "DELETE is refused" in eseguibile


def test_C2_la_formula_e_null_aware():
    """Il primo ramo vale solo quando un contatto c'e' davvero: `WHERE id =
    NULL` non trova nulla e renderebbe `NOT EXISTS` vero per costruzione."""
    eseguibile = senza_commenti(UP.read_text(encoding="utf-8"))
    formula = eseguibile.split("v_genitore_sparito :=")[1].split(";")[0]
    assert "OLD.contact_id IS NOT NULL" in formula
    assert "OLD.contact_id IS NULL" in formula


def test_C3_nessuna_variabile_di_sessione_entra_nel_guard():
    """064 lo dichiara: l'esistenza del genitore si legge, non si dichiara. Un
    `set_config` qui sarebbe un interruttore per spegnere C6."""
    eseguibile = senza_commenti(UP.read_text(encoding="utf-8"))
    for vietato in ("set_config", "current_setting", "session_replication_role"):
        assert vietato not in eseguibile, f"la 065 usa {vietato}"


def test_C4_il_trigger_su_stime_tocca_solo_le_righe_senza_contatto():
    eseguibile = senza_commenti(UP.read_text(encoding="utf-8"))
    corpo = eseguibile.split("CREATE OR REPLACE FUNCTION stime_purge_contactless_messages()")[1]
    cancellazione = corpo.split("DELETE FROM communication_messages")[1].split(";")[0]
    assert "contact_id IS NULL" in cancellazione, "cancellerebbe anche righe legate"
    assert "agency_id  = OLD.agency_id" in cancellazione, "manca il predicato di tenancy"
    assert "stima_id   = OLD.id" in cancellazione


def test_C5_il_trigger_e_before_delete_e_enable_always():
    eseguibile = senza_commenti(UP.read_text(encoding="utf-8"))
    assert "BEFORE DELETE ON stime" in eseguibile, (
        "AFTER DELETE arriverebbe dopo il SET NULL della 064, che fa perdere il "
        "legame con la stima"
    )
    assert ("ALTER TABLE stime\n    ENABLE ALWAYS TRIGGER "
            "trg_stime_purge_contactless_messages;") in eseguibile


def test_C6_la_064_non_e_stata_riscritta():
    """La FK `stima_id -> stime ON DELETE SET NULL` della 064 resta: e' la meta'
    del comportamento condizionale che vale per i messaggi CON contatto."""
    sql_064 = UP_064.read_text(encoding="utf-8")
    assert "stima_id             INTEGER      REFERENCES stime(id)      ON DELETE SET NULL" in sql_064
    eseguibile = senza_commenti(UP.read_text(encoding="utf-8"))
    assert "communication_messages_stima_id_fkey" not in eseguibile
    assert "lifecycle_stima_id" not in eseguibile, (
        "la colonna generata e' stata scartata: con due FK verso stime il SET "
        "NULL e il CASCADE si contendono la stessa riga"
    )
    assert "stime_agency_scope_unq" not in eseguibile, (
        "la UNIQUE su stime non serve: non c'e' nessuna FK composita verso stime"
    )


# ---------------------------------------------------------------------------
# D  La down rifiuta invece di distruggere
# ---------------------------------------------------------------------------

def test_D1_il_conteggio_precede_ogni_ddl():
    eseguibile = senza_commenti(DOWN.read_text(encoding="utf-8"))
    posizione_conteggio = eseguibile.index("WHERE contact_id IS NULL")
    for ddl in ("DROP TRIGGER", "DROP FUNCTION", "ALTER TABLE", "CREATE OR REPLACE"):
        assert eseguibile.index(ddl) > posizione_conteggio, (
            f"{ddl} viene prima del controllo: un down su righe contactless "
            "modificherebbe qualcosa prima di accorgersene"
        )


def test_D2_la_down_non_cancella_e_non_inventa_dati():
    eseguibile = senza_commenti(DOWN.read_text(encoding="utf-8"))
    assert "DELETE FROM communication_messages" not in eseguibile
    assert "UPDATE communication_messages" not in eseguibile
    assert "RAISE EXCEPTION" in eseguibile


def test_D3_la_down_rimette_il_not_null():
    eseguibile = senza_commenti(DOWN.read_text(encoding="utf-8"))
    assert "ALTER COLUMN contact_id SET NOT NULL" in eseguibile


# ---------------------------------------------------------------------------
# E  Il service rifiuta prima del database
# ---------------------------------------------------------------------------

def test_E1_le_due_regole_stanno_anche_nel_service():
    import inspect

    from communication import service

    corpo = inspect.getsource(service.enqueue) + inspect.getsource(service._prepara) \
        if hasattr(service, "_prepara") else inspect.getsource(service)
    assert "contact_id is required for marketing" in corpo
    assert "needs a stima_id" in corpo


def test_E2_la_firma_ammette_contact_id_assente():
    import inspect

    from communication import service

    parametro = inspect.signature(service.enqueue).parameters["contact_id"]
    assert parametro.annotation in ("int | None", int.__class__) or "None" in str(
        parametro.annotation), parametro.annotation


def test_E3_nessun_contatto_sintetico_viene_creato():
    """La regola e' rifiutare o accettare il NULL, mai fabbricare una riga."""
    import re as _re

    corpo = (ROOT / "communication" / "service.py").read_text(encoding="utf-8")
    corpo = _re.sub(r'"{3}[\s\S]*?"{3}', "", corpo)
    for vietato in ("INSERT INTO contacts", "create_contact", "ensure_contact"):
        assert vietato not in corpo, f"il service fabbrica un contatto: {vietato}"


# ---------------------------------------------------------------------------
# F  L'enumerazione dei percorsi di cancellazione
# ---------------------------------------------------------------------------
#
# L'allentamento del guard per le righe senza contatto regge su un'enumerazione
# chiusa: a profondita' > 1 `communication_messages` e' raggiungibile SOLO dai
# suoi genitori. Se qualcuno aggiunge una quarta strada, l'enumerazione cambia
# sotto i piedi del guard e nessun test di comportamento se ne accorge - perche'
# continuerebbe a comportarsi bene sui tre percorsi che conosce.

#: I tre percorsi certificati. Cambiare questo insieme e' una decisione, e va
#: presa insieme al testo del guard nella 065.
PERCORSI_DI_CANCELLAZIONE = {
    "contacts:  FK composita (agency_id, contact_id) ON DELETE CASCADE  [064]",
    "agencies:  FK diretta   (agency_id)             ON DELETE CASCADE  [065]",
    "stime:     trigger BEFORE DELETE, solo contact_id IS NULL          [065]",
}


def test_F1_i_percorsi_di_cancellazione_sono_tre_e_dichiarati():
    assert len(PERCORSI_DI_CANCELLAZIONE) == 3


def test_F2_nessuna_migration_aggiunge_una_fk_verso_i_messaggi():
    """Una FK che PUNTA a `communication_messages` con ON DELETE CASCADE
    aprirebbe un quarto percorso: quel genitore potrebbe portarsi via una riga
    senza contatto senza che il guard lo sappia.

    L'unica ammessa e' quella di `communication_attempts`, che e' figlia e non
    genitore - e che infatti non cancella nulla dei messaggi.
    """
    ammesse = {"communication_attempts_message_same_agency_fk"}
    trovate = set()
    for percorso in sorted(MIGRAZIONI.glob("*.sql")):
        testo = senza_commenti(percorso.read_text(encoding="utf-8"))
        for nome, riferimento in re.findall(
                r"CONSTRAINT\s+(\w+)\s+FOREIGN KEY[^;]*?REFERENCES\s+(\w+)", testo,
                re.DOTALL | re.IGNORECASE):
            if riferimento.lower() == "communication_messages":
                trovate.add(nome)
    assert trovate <= ammesse, (
        f"una FK nuova punta a communication_messages: {sorted(trovate - ammesse)}. "
        "Sarebbe un quarto percorso di cancellazione, e il guard della 065 regge "
        "su un'enumerazione di tre."
    )


def test_F3_una_sola_migration_cancella_dai_messaggi_e_lo_fa_da_un_trigger():
    """Stessa ragione, per la strada dei trigger.

    Due file nominano `DELETE FROM communication_messages`, e solo uno cancella
    davvero: nella 064 quella riga sta dentro una SONDA che verifica che il
    DELETE venga RIFIUTATO - e' racchiusa in un `BEGIN ... EXCEPTION WHEN
    raise_exception`, cioe' si aspetta di fallire. Contarla come percorso di
    cancellazione sarebbe scambiare la prova del divieto per una sua
    violazione.
    """
    autori = {}
    for percorso in sorted(MIGRAZIONI.glob("*.sql")):
        if percorso.name.endswith("_down.sql"):
            continue
        testo = senza_commenti(percorso.read_text(encoding="utf-8"))
        if "DELETE FROM communication_messages" in testo:
            autori[percorso.name] = testo
    assert set(autori) == {"064_p29_communication_foundation.sql",
                           "065_p29_service_lifecycle_parent.sql"}, sorted(autori)

    # 064: la riga vive in una sonda che si aspetta il rifiuto.
    sonda = autori["064_p29_communication_foundation.sql"]
    coda = sonda.split("DELETE FROM communication_messages")[1][:200]
    assert "EXCEPTION WHEN raise_exception" in coda, (
        "la 064 cancella davvero dai messaggi: non e' piu' una sonda"
    )
    assert sonda.count("DELETE FROM communication_messages") == 1

    # 065: la riga vive nella funzione del trigger di lifecycle, e solo li'.
    lifecycle = autori["065_p29_service_lifecycle_parent.sql"]
    assert lifecycle.count("DELETE FROM communication_messages") == 1
    prima = lifecycle.split("DELETE FROM communication_messages")[0]
    assert prima.rstrip().endswith("BEGIN"), "la cancellazione non e' nel trigger"
    assert "CREATE OR REPLACE FUNCTION stime_purge_contactless_messages()" in prima


def test_F4_il_dominio_non_cancella_messaggi():
    """E nemmeno dal lato applicativo: il ledger si svuota solo con un
    genitore."""
    pacchetto = ROOT / "communication"
    for percorso in sorted(pacchetto.rglob("*.py")):
        if "__pycache__" in percorso.parts:
            continue
        corpo = re.sub(r'"{3}[\s\S]*?"{3}', "",
                       percorso.read_text(encoding="utf-8"))
        corpo = re.sub(r"#[^\n]*", "", corpo)
        assert "DELETE FROM communication_messages" not in corpo, percorso.name
