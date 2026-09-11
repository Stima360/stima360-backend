"""La colonna che non esisteva: `followup_actions.executed_at`.

COSA E' SUCCESSO

La copia scoped dell'escalation - `execute_temporal_escalation_for_agency`,
scritta in P26-6A - chiudeva l'azione cosi':

    UPDATE followup_actions
       SET status = 'completed', task_id = %s, executed_at = NOW()
     WHERE id = %s AND agency_id = %s

`executed_at` non e' mai esistita in quella tabella. Ogni escalation per
agenzia falliva quindi con UndefinedColumn, e da li' in poi conta la
TRANSAZIONE, non solo l'eccezione:

  1. `followup_cursor(commit=True)` intercetta, esegue `conn.rollback()` e
     rilancia (followup/database.py). Le DUE scritture di quel blocco se ne
     vanno insieme: anche la prima, quella che aveva gia' portato
     l'attivita' a in_progress/high. L'attivita' torna com'era.
  2. `_mark_failed_best_effort` apre una transazione NUOVA e scrive
     `status='failed', error_message=<testo dell'eccezione>` con
     `WHERE id = %s AND status = 'pending'`. Non tocca `task_id`, che
     resta NULL.
  3. Il chiamante rilancia.

La riga dell'azione sopravvive perche' `_insert_pending_action_for_agency`
l'ha scritta in una transazione a parte, chiusa prima.

Sul TEST si e' visto esattamente questo: azione 867, agenzia 18, stato
failed, task_id NULL, 205 caratteri di messaggio - e nessuna attivita'
escalata a meta'.

PERCHE' NESSUNA PROVA LO VEDEVA

I doppi delle prove esistenti riconoscono le query per sottostringa
(`"update followup_actions" in sql`) e non hanno colonne: una colonna
inventata passa inosservata, perche' il doppio non ha uno schema da violare.
E i due percorsi legacy, che la prova copriva, quella colonna non la
scrivevano.

Da qui due prove di natura diversa:

  * una STRUTTURALE, che confronta ogni colonna assegnata negli UPDATE del
    modulo con lo schema vero letto dalle migration. Avrebbe intercettato
    `executed_at` senza bisogno di eseguire niente, ed e' l'unica che
    continuerebbe a funzionare se domani il doppio cambiasse forma;
  * una FUNZIONALE sul percorso corretto, che verifica i tre effetti attesi -
    attivita' escalata, azione chiusa con il suo task, confine d'agenzia su
    entrambe le scritture.
"""

from __future__ import annotations

import ast
import copy
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from followup import repository

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
REPOSITORY = ROOT / "followup" / "repository.py"


# ---------------------------------------------------------------------------
# Lo schema vero, letto dalle migration
# ---------------------------------------------------------------------------

def _colonne_reali(tabella: str) -> set[str]:
    """Le colonne di una tabella: CREATE TABLE piu' ogni ADD COLUMN.

    `followup_actions.agency_id` arriva da un ALTER nella 043: fermarsi alla
    CREATE TABLE direbbe che non esiste, e questa prova segnalerebbe come
    inventata una colonna che c'e'.
    """
    testo = "\n".join(p.read_text(encoding="utf-8")
                      for p in sorted(MIGRAZIONI.glob("*.sql")))
    colonne: set[str] = set()

    testa = re.search(r"CREATE TABLE (?:IF NOT EXISTS )?" + tabella + r"\s*\(", testo)
    assert testa, f"{tabella} non e' definita in nessuna migration"
    i, livello = testa.end(), 1
    while i < len(testo) and livello:
        livello += 1 if testo[i] == "(" else -1 if testo[i] == ")" else 0
        i += 1
    for riga in testo[testa.end():i - 1].splitlines():
        nuda = riga.strip()
        if not nuda or nuda.upper().startswith(
                ("CONSTRAINT", "UNIQUE", "CHECK", "PRIMARY KEY", "FOREIGN KEY", "--")):
            continue
        trovata = re.match(r"([a-z_][a-z0-9_]*)\s+[A-Za-z]", nuda)
        if trovata:
            colonne.add(trovata.group(1))
    # La forma compatta su una riga sola, usata da alcune migration.
    for nome in re.findall(r"[(,]\s*([a-z_][a-z0-9_]*)\s+[A-Z]", testo[testa.end():i - 1]):
        colonne.add(nome)

    for nome in re.finditer(
            r"ALTER TABLE\s+(?:IF EXISTS\s+)?(?:ONLY\s+)?" + tabella
            + r"\s+ADD COLUMN\s+(?:IF NOT EXISTS\s+)?([a-z_][a-z0-9_]*)",
            testo, re.IGNORECASE):
        colonne.add(nome.group(1).lower())
    return colonne


def _colonne_assegnate(sql: str) -> set[str]:
    """Le colonne che un UPDATE scrive: tutto fra SET e WHERE."""
    corpo = re.search(r"\bSET\b(.*?)\bWHERE\b", sql, re.IGNORECASE | re.DOTALL)
    if not corpo:
        return set()
    return {m.group(1).lower()
            for m in re.finditer(r"([a-z_][a-z0-9_]*)\s*=", corpo.group(1), re.IGNORECASE)}


def _update_del_modulo():
    """(tabella, colonne assegnate) per ogni UPDATE letterale del repository.

    Si legge l'AST e non il testo: una stringa concatenata o un commento che
    contenga 'UPDATE' non e' una query, e contarlo darebbe falsi allarmi -
    o, peggio, farebbe passare la prova cancellando un commento.
    """
    albero = ast.parse(REPOSITORY.read_text(encoding="utf-8"))
    for nodo in ast.walk(albero):
        if not isinstance(nodo, ast.Constant) or not isinstance(nodo.value, str):
            continue
        sql = " ".join(nodo.value.split())
        trovata = re.search(r"\bUPDATE\s+([a-z_][a-z0-9_]*)\b", sql, re.IGNORECASE)
        if trovata:
            yield trovata.group(1).lower(), _colonne_assegnate(sql)


def test_every_column_written_by_the_module_exists_in_the_schema():
    """OGNI colonna assegnata da un UPDATE esiste davvero.

    E' la prova che avrebbe fermato `executed_at` prima del TEST. Non conosce
    quel nome: confronta con lo schema, quindi vale anche per la prossima.
    """
    assegnate = list(_update_del_modulo())
    assert assegnate, "nessun UPDATE trovato: la prova non guarderebbe niente"
    schemi: dict[str, set[str]] = {}
    for tabella, colonne in assegnate:
        schemi.setdefault(tabella, _colonne_reali(tabella))
        inventate = colonne - schemi[tabella]
        assert not inventate, (
            f"{tabella}: colonne assegnate che non esistono nello schema: "
            f"{sorted(inventate)}. Le colonne vere sono {sorted(schemi[tabella])}")


def test_followup_actions_really_has_no_executed_at():
    """E la colonna non c'e' davvero: se un giorno qualcuno la aggiungesse,
    questa prova lo direbbe, invece di lasciare che la prova sopra passi per
    una ragione diversa da quella per cui e' stata scritta."""
    colonne = _colonne_reali("followup_actions")
    assert "executed_at" not in colonne, sorted(colonne)
    assert {"status", "task_id", "agency_id", "error_message"} <= colonne, sorted(colonne)


# ---------------------------------------------------------------------------
# Il percorso corretto
# ---------------------------------------------------------------------------

class CursoreConSchema:
    """Un doppio che RIFIUTA le colonne inesistenti.

    I doppi esistenti riconoscono le query per sottostringa e non hanno
    colonne: e' il motivo per cui `executed_at` e' arrivata viva fino al TEST.
    Questo doppio ha lo schema vero, letto dalle migration, e su una colonna
    inventata solleva - come farebbe PostgreSQL.
    """

    def __init__(self, db):
        self.db = db
        self.rows = []

    def execute(self, query, params=None):
        sql = " ".join(str(query).split())
        self.db.sql.append((sql, params))
        trovata = re.search(r"\bUPDATE\s+([a-z_][a-z0-9_]*)\b", sql, re.IGNORECASE)
        if trovata:
            tabella = trovata.group(1).lower()
            inventate = _colonne_assegnate(sql) - _colonne_reali(tabella)
            if inventate:
                raise RuntimeError(
                    f'column "{sorted(inventate)[0]}" of relation "{tabella}" '
                    "does not exist")

        basso = sql.lower()
        if "insert into followup_actions" in basso:
            riga = {"id": 867, "status": "pending", "task_id": None,
                    "error_message": None,
                    "idempotency_key": params["idempotency_key"],
                    "agency_id": params["agency_id"]}
            self.db.actions.append(riga)
            self.rows = [dict(riga)]
            return
        if basso.startswith("update tasks"):
            task_id, agency_id = params
            attivita = next((t for t in self.db.tasks
                             if t["id"] == task_id and t["agency_id"] == agency_id), None)
            if attivita is None:
                self.rows = []
                return
            # I VALORI SI LEGGONO DALLA QUERY, non si danno per scontati.
            # Un doppio che scrivesse 'in_progress' e 'high' di sua iniziativa
            # direbbe che l'escalation funziona anche dopo averla disattivata
            # nel codice: e' lo stesso genere di compiacenza che ha lasciato
            # passare `executed_at`.
            corpo = re.search(r"\bSET\b(.*?)\bWHERE\b", sql, re.IGNORECASE | re.DOTALL)
            for colonna, valore in re.findall(
                    r"([a-z_][a-z0-9_]*)\s*=\s*'([^']*)'", corpo.group(1), re.IGNORECASE):
                attivita[colonna.lower()] = valore
            self.rows = [{"id": task_id}]
            return
        if basso.startswith("update followup_actions"):
            # DUE query diverse, con due firme diverse.
            #
            # `_mark_failed_best_effort` passa (error_message, action_id):
            # due parametri, non tre, e la sua WHERE esige `status =
            # 'pending'` invece dell'agenzia. Pretendere tre parametri qui
            # faceva sollevare il doppio dentro un `except Exception: pass`,
            # che inghiottiva tutto e lasciava l'azione 'pending' - cioe' il
            # doppio raccontava un guasto diverso da quello vero.
            if "status = 'failed'" in basso:
                error_message, action_id = params
                for riga in self.db.actions:
                    if riga["id"] == action_id and riga["status"] == "pending":
                        riga["status"] = "failed"
                        riga["error_message"] = error_message
                self.rows = []
                return
            task_id, action_id, agency_id = params
            for riga in self.db.actions:
                if riga["id"] == action_id and riga["agency_id"] == agency_id:
                    riga["status"] = "completed"
                    riga["task_id"] = task_id
            self.rows = []
            return
        self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class DbConSchema:
    """Un database che fa ROLLBACK, perche' quello vero lo fa.

    `followup_cursor` intercetta l'eccezione, chiama `conn.rollback()` e
    rilancia: tutte le scritture di quel blocco spariscono insieme, non solo
    quella che ha sollevato. Un doppio che le tenesse racconterebbe un
    guasto piu' grave del vero - un'attivita' escalata a meta' - e una prova
    scritta su quel racconto difenderebbe un comportamento che non esiste.

    Il registro `sql` NON viene ripristinato: e' un'osservazione di cio' che
    e' stato mandato al database, non uno stato del database.
    """

    def __init__(self):
        self.tasks = []
        self.actions = []
        self.sql = []

    @contextmanager
    def cursor(self, *, commit=False):
        istantanea = (copy.deepcopy(self.tasks), copy.deepcopy(self.actions))
        try:
            yield self, CursoreConSchema(self)
        except Exception:
            self.tasks, self.actions = istantanea
            raise


def _escala(db, *, agency_id=18, task_id=40):
    return repository.execute_temporal_escalation_for_agency(
        agency_id,
        rule_code="FOLLOWUP_TASK_STALE_ESCALATE_V1",
        trigger_type="temporal",
        task_id=task_id,
        contact_id=86,
        lead_id=None,
        stima_id=None,
        idempotency_key=f"followup:time:v1:agency:{agency_id}:task:{task_id}",
        created_by="p26-6",
    )


def _attivita(task_id=40, agency_id=18):
    return {"id": task_id, "agency_id": agency_id, "status": "open",
            "priority": "normal",
            "due_at": datetime.now(timezone.utc) - timedelta(hours=30)}


def test_the_escalation_updates_the_task_to_in_progress_high(monkeypatch):
    """L'attivita' viene escalata: in_progress e high."""
    db = DbConSchema()
    db.tasks.append(_attivita())
    monkeypatch.setattr(repository, "followup_cursor", db.cursor)

    esito = _escala(db)

    assert esito["status"] == "completed", esito
    assert db.tasks[0]["status"] == "in_progress"
    assert db.tasks[0]["priority"] == "high"


def test_the_action_is_completed_with_its_task(monkeypatch):
    """L'azione si chiude con il task valorizzato.

    Prima restava 'failed' con task_id NULL: non perche' la regola non si
    applicasse, ma perche' la query che la chiudeva non era eseguibile.
    """
    db = DbConSchema()
    db.tasks.append(_attivita())
    monkeypatch.setattr(repository, "followup_cursor", db.cursor)

    esito = _escala(db)

    azione = db.actions[0]
    assert azione["status"] == "completed", azione
    assert azione["task_id"] == 40, azione
    assert esito["action_id"] == azione["id"]


def test_both_writes_stay_inside_the_agency(monkeypatch):
    """Le due UPDATE portano il confine d'agenzia, e non e' un dettaglio:
    e' l'unica cosa che impedisce a un'escalation di toccare l'attivita' di
    un altro tenant che per caso ha lo stesso id."""
    db = DbConSchema()
    db.tasks.append(_attivita())
    monkeypatch.setattr(repository, "followup_cursor", db.cursor)

    _escala(db)

    aggiornamenti = [(s, p) for s, p in db.sql if s.upper().startswith("UPDATE")]
    assert len(aggiornamenti) == 2, aggiornamenti
    for sql, params in aggiornamenti:
        assert "agency_id = %s" in sql, sql
        assert params[-1] == 18, (sql, params)


def test_a_task_of_another_agency_is_not_escalated(monkeypatch):
    """E se l'attivita' e' di un'altra agenzia, non viene toccata: la prima
    UPDATE non trova nulla, l'escalation si ferma e l'azione viene marcata
    fallita - senza che nessuna riga dell'agenzia 19 cambi."""
    db = DbConSchema()
    db.tasks.append(_attivita(agency_id=19))
    monkeypatch.setattr(repository, "followup_cursor", db.cursor)
    prima = copy.deepcopy(db.tasks)

    with pytest.raises(Exception):
        _escala(db, agency_id=18)

    assert db.tasks == prima, (db.tasks, prima)
    assert db.actions[0]["status"] == "failed", db.actions[0]
    assert db.actions[0]["task_id"] is None, db.actions[0]


def test_the_previous_sql_would_be_rejected_by_this_double(monkeypatch):
    """IL MUTANTE, dentro la prova.

    Si rimette `executed_at = NOW()` nella query e si verifica che il doppio
    con lo schema la rifiuti, che l'azione resti 'failed' e che `task_id`
    resti NULL - la fotografia esatta di cio' che il TEST ha mostrato. Senza
    questo caso, le prove qui sopra passerebbero anche con un doppio che non
    controlla niente.
    """
    db = DbConSchema()
    db.tasks.append(_attivita())
    monkeypatch.setattr(repository, "followup_cursor", db.cursor)
    prima = copy.deepcopy(db.tasks)

    originale = CursoreConSchema.execute

    def con_colonna_inventata(self, query, params=None):
        if str(query).lstrip().upper().startswith("UPDATE FOLLOWUP_ACTIONS") \
                and "completed" in str(query):
            query = str(query).replace(
                "SET status = 'completed', task_id = %s",
                "SET status = 'completed', task_id = %s, executed_at = NOW()")
        return originale(self, query, params)

    monkeypatch.setattr(CursoreConSchema, "execute", con_colonna_inventata)

    with pytest.raises(RuntimeError, match="executed_at"):
        _escala(db)

    # 1. L'ATTIVITA' E' COM'ERA. Il rollback ha annullato anche la prima
    #    UPDATE, che era gia' andata a buon fine: le due scritture stavano
    #    nella stessa transazione e se ne vanno insieme.
    assert db.tasks == prima, (db.tasks, prima)

    # 2-4. L'AZIONE, invece, resta: era stata scritta in una transazione
    #      precedente, gia' chiusa. `_mark_failed_best_effort` ne apre una
    #      nuova e la marca fallita, senza toccare `task_id`.
    azione = db.actions[0]
    assert azione["status"] == "failed", azione
    assert azione["task_id"] is None, azione
    assert "executed_at" in (azione["error_message"] or ""), azione["error_message"]
