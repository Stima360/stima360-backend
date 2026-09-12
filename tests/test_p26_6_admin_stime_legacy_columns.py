"""P26-6 - `/api/admin/stime` non puo' morire sulle colonne del gestionale.

PERCHE' QUESTO FILE ESISTE

I run live `38e341f68f8a` e `58aa0e189aaa` hanno chiuso `LEGACY_ADMIN-list-A`
con `-> 500` e un corpo di ventun caratteri: "Internal Server Error", cioe'
un'eccezione non gestita, non un problema di isolamento.

La causa non era diagnosticabile dalla risposta - il corpo non porta nulla - ma
e' scritta nel repository. `lead_status` e `note_internal` nascono SOLO da
`migrazione_gestionale_stime()`, che vive nel blocco `__main__` di
`database.py` e che nessun file sotto `migrations/` esegue.
`docs/P26_BASELINE_CERTIFICATE_TEST.md` §3.0.2 le elenca fra le trenta colonne
"dichiarate ma assenti" su TEST, e avverte esplicitamente di non eseguire
`database.py` come script per aggiungerle, perche' invaliderebbe l'impronta
certificata. Una SELECT che le nomina come riferimenti di colonna abortisce con
`UndefinedColumn`, e la route non la intercetta.

E' la stessa forma del difetto `executed_at` in `followup/repository.py`: una
istruzione che nomina una colonna che nello schema reale non c'e'.

COSA PROVANO QUESTI TEST, E PERCHE' IN DUE STRATI

Uno strato solo non basterebbe, e nessuno dei due e' decorativo:

* STRUTTURALE - la proiezione delle route `/api/admin` non nomina, come
  riferimento di colonna, nessuna delle colonne che il CERTIFICATO dichiara
  assenti. L'elenco non e' scritto a mano qui: si legge dal certificato, che e'
  la prova d'ambiente, cosi' che modificare il documento o la route faccia
  reagire il test invece di lasciarlo verde per inerzia.

* DI COMPORTAMENTO - la route viene eseguita davvero, contro un cursore il cui
  `stime` ha ESATTAMENTE le colonne che TEST ha. Il doppio e' ostile: un
  riferimento a una colonna che non possiede solleva `UndefinedColumn` come
  farebbe PostgreSQL. `test_06` lo dimostra rieseguendo la SELECT PRECEDENTE
  contro lo stesso doppio e pretendendo che fallisca - senza quella prova il
  doppio potrebbe essere indulgente e tutto il resto varrebbe zero.

LA LETTURA E' MEZZA CORREZIONE, E QUESTO FILE LO DICE.

`to_jsonb(s) ->> 'lead_status'` impedisce alla lista di morire, ma
`POST /api/admin/stime/{id}/update` ha ESATTAMENTE quelle due colonne come
unici campi scrivibili: con la sola lettura la funzione resta dimezzata - si
legge sempre NULL e non si puo' scrivere nulla - e una scrittura non si aggira
con una proiezione. La migration `056_p26_stime_gestionale_columns.sql` crea le
colonne per via tracciata, che e' l'unica strada che la baseline ammetta:
eseguire `database.py` ne aggiungerebbe trenta, senza versione e senza down,
invalidando l'impronta certificata.

056 E' SCRITTA E NON APPLICATA. Finche' resta tale, TEST e' ancora lo stato che
il certificato descrive - ed e' il motivo per cui la forma `->>` non va tolta
adesso che le colonne "esistono": il codice deve reggere entrambi gli stati,
perche' non sa in quale dei due sta girando.

Le altre ventotto colonne di §3.0.2 restano assenti di proposito: nessuna route
le nomina, e crearle "per allineare" sarebbe una modifica di schema senza un
chiamante.
"""

from __future__ import annotations

import ast
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import psycopg2
import pytest
from fastapi.testclient import TestClient

from operator_auth.context import OperatorContext

ROOT = Path(__file__).resolve().parents[1]
CERTIFICATO = ROOT / "docs" / "P26_BASELINE_CERTIFICATE_TEST.md"

A, B = 61, 94
STIMA_A, STIMA_B = 11, 22

# Le route di main.py che leggono `stime` o `stime_dettagliate`.
ROUTE_DI_LETTURA = ("admin_lista_stime", "admin_lista_stime_pro")


# ---------------------------------------------------------------------------
# Lo schema, ricavato e non dichiarato
# ---------------------------------------------------------------------------

def _colonne_dichiarate(tabella: str) -> set[str]:
    """Tutte le colonne che il repository DICHIAREREBBE per `tabella`.

    Somma di `database.py` (CREATE TABLE piu' le funzioni `migrazione_*`) e di
    ogni file UP sotto `migrations/`. E' il superinsieme: contiene anche cio'
    che su TEST non e' mai stato eseguito. Il sottoinsieme reale si ottiene
    sottraendo l'elenco del certificato.
    """
    testi = [(ROOT / "database.py").read_text(encoding="utf-8")]
    for percorso in sorted((ROOT / "migrations").glob("*.sql")):
        if percorso.name.endswith("_down.sql"):
            continue
        testi.append(percorso.read_text(encoding="utf-8"))

    trovate: set[str] = set()
    riservate = {"CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK"}
    for testo in testi:
        schema = rf"CREATE TABLE (?:IF NOT EXISTS )?{tabella}\s*\((.*?)\n\s*\)\s*;"
        for blocco in re.finditer(schema, testo, re.S | re.I):
            for riga in blocco.group(1).splitlines():
                riga = riga.strip()
                if not riga or riga.startswith("--"):
                    continue
                nome = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s", riga)
                if nome and nome.group(1).upper() not in riservate:
                    trovate.add(nome.group(1).lower())
        alter = rf"ALTER TABLE\s+(?:public\.)?{tabella}\b(.*?);"
        for blocco in re.finditer(alter, testo, re.S | re.I):
            for nome in re.finditer(
                r"ADD COLUMN (?:IF NOT EXISTS )?([A-Za-z_][A-Za-z0-9_]*)",
                blocco.group(1), re.I,
            ):
                trovate.add(nome.group(1).lower())
    return trovate


def _colonne_assenti_dal_certificato() -> dict[str, set[str]]:
    """§3.0.2 del certificato: le colonne dichiarate ma ASSENTI su TEST.

    Letto dal documento e non ricopiato qui, perche' il documento e' la prova
    d'ambiente e questi test devono reagire se cambia.

    E' lo stato ALLA BASELINE `P26-BASELINE-001`, non necessariamente quello
    di adesso: la migration 056 crea due di queste trenta colonne. Finche' 056
    non e' applicata su TEST l'elenco resta vero parola per parola, e la route
    deve funzionare in ENTRAMBI gli stati - prima e dopo - perche' nessuno,
    scrivendo il codice, sa in quale dei due girera'.
    """
    testo = CERTIFICATO.read_text(encoding="utf-8")
    sezione = re.search(
        r"### 3\.0\.2 Declared but absent.*?\n(.*?)\nAll 30 originate",
        testo, re.S,
    )
    assert sezione, "§3.0.2 non trovata nel certificato: la prova ha perso la sua fonte"

    assenti: dict[str, set[str]] = {}
    corpo = sezione.group(1)
    for voce in re.finditer(r"^- `(\w+)`:(.*?)(?=^- `|\Z)", corpo, re.S | re.M):
        tabella = voce.group(1)
        # Ogni voce cita anche la funzione di `database.py` che le avrebbe
        # create: e' un'origine, non una colonna, e va tolta.
        assenti[tabella] = {
            nome.lower() for nome in re.findall(r"`(\w+)`", voce.group(2))
            if not nome.startswith("migrazione_")
        }
    return assenti


ASSENTI_ALLA_BASELINE = _colonne_assenti_dal_certificato()
COLONNE_TEST_STIME = (
    _colonne_dichiarate("stime") - ASSENTI_ALLA_BASELINE.get("stime", set()))
COLONNE_TEST_DETTAGLIATE = (
    _colonne_dichiarate("stime_dettagliate")
    - ASSENTI_ALLA_BASELINE.get("stime_dettagliate", set())
)

#: La migration che completa `POST /api/admin/stime/{id}/update`. Scritta e
#: NON applicata: applicarla cambia l'impronta certificata in §2 e richiede
#: una nuova voce di baseline, che non e' una decisione di questo file.
MIGRAZIONE_056 = "056_p26_stime_gestionale_columns.sql"


def _sql_della_route(nome: str) -> str:
    sorgente = (ROOT / "main.py").read_text(encoding="utf-8")
    for nodo in ast.walk(ast.parse(sorgente)):
        if not isinstance(nodo, ast.FunctionDef) or nodo.name != nome:
            continue
        for sotto in ast.walk(nodo):
            if (
                isinstance(sotto, ast.Call)
                and isinstance(sotto.func, ast.Attribute)
                and sotto.func.attr == "execute"
                and sotto.args
                and isinstance(sotto.args[0], ast.Constant)
            ):
                return sotto.args[0].value
    raise AssertionError(f"nessuna execute trovata in {nome}")


# ---------------------------------------------------------------------------
# Il doppio: un cursore che conosce lo schema di TEST e non perdona
# ---------------------------------------------------------------------------

_JSONB = re.compile(
    r"to_jsonb\(\s*(\w+)\s*\)\s*->>\s*'(\w+)'(?:\s+AS\s+(\w+))?", re.I)
_QUALIFICATA = re.compile(r"\b([a-z]{1,3})\.([a-z_][a-z0-9_]*)\b", re.I)


class CursoreStimeTest:
    """Un cursore con lo schema che `stima360_db_test` ha davvero.

    Due sole regole, ed entrambe copiano PostgreSQL:

    * un riferimento `alias.colonna` a una colonna che la relazione non ha
      solleva `UndefinedColumn` - prima di qualunque riga, come farebbe il
      parser;
    * `to_jsonb(alias) ->> 'chiave'` risolve sulla RIGA: il valore se la chiave
      c'e', NULL se non c'e'. E' esattamente la ragione per cui la route usa
      quella forma, quindi il doppio deve modellarla o il test non prova nulla.
    """

    def __init__(self, colonne_stime=None, colonne_dettagliate=None):
        self.colonne = {
            "s": set(colonne_stime if colonne_stime is not None else COLONNE_TEST_STIME),
            "sd": set(colonne_dettagliate if colonne_dettagliate is not None
                      else COLONNE_TEST_DETTAGLIATE),
        }
        self.istruzioni: list[tuple[str, object]] = []
        self.description = None
        self.rowcount = 0
        self._righe: list[tuple] = []

    # -- le righe, scritte solo con colonne che esistono -------------------
    def _righe_stime(self) -> list[dict]:
        base = [
            {"id": STIMA_A, "agency_id": A, "nome": "AGENZIA A",
             "cognome": "A", "email": "a@example.test", "telefono": "1",
             "data": datetime(2026, 9, 12, 9, 0), "comune": "COMUNE A",
             "microzona": "MZ A", "via": "VIA A", "civico": "1",
             "tipologia": "appartamento", "mq": 80, "piano": "1",
             "locali": 3, "bagni": 1, "pertinenze": "", "ascensore": "si",
             "consenso_marketing": False, "lead_status": "nuovo",
             "note_internal": "riservato A"},
            {"id": STIMA_B, "agency_id": B, "nome": "AGENZIA B",
             "cognome": "B", "email": "b@example.test", "telefono": "2",
             "data": datetime(2026, 9, 12, 8, 0), "comune": "COMUNE B",
             "microzona": "MZ B", "via": "VIA B", "civico": "2",
             "tipologia": "villa", "mq": 200, "piano": "0",
             "locali": 6, "bagni": 3, "pertinenze": "", "ascensore": "no",
             "consenso_marketing": True, "lead_status": "contattato",
             "note_internal": "riservato B"},
        ]
        # Una riga non puo' portare una colonna che la tabella non ha.
        return [{k: v for k, v in r.items() if k in self.colonne["s"]} for r in base]

    def execute(self, sql, params=None):
        piatta = " ".join(str(sql).split())
        self.istruzioni.append((piatta, params))

        # 1. Ogni riferimento qualificato deve esistere. I frammenti dentro
        #    `to_jsonb(...) ->> '...'` sono chiavi, non riferimenti: si tolgono
        #    prima di guardare, altrimenti il doppio boccerebbe la correzione.
        senza_json = _JSONB.sub(" ", piatta)
        for alias, colonna in _QUALIFICATA.findall(senza_json):
            if alias not in self.colonne:
                continue
            if colonna.lower() not in self.colonne[alias]:
                relazione = "stime" if alias == "s" else "stime_dettagliate"
                raise psycopg2.errors.UndefinedColumn(
                    f'column {alias}.{colonna} does not exist\n'
                    f'LINE 1: ... FROM {relazione} {alias} ...'
                )

        # 1b. LA SCRITTURA, CHE NESSUNA PROIEZIONE PUO' SALVARE.
        #
        # `UPDATE stime SET lead_status=%s ...` nomina la colonna NUDA, quindi
        # il controllo qualificato sopra non la vede. E' esattamente la meta'
        # che `to_jsonb` non cura: in una colonna che non esiste non si scrive,
        # e PostgreSQL risponde con lo stesso `UndefinedColumn`. Senza questo
        # ramo il doppio avrebbe accettato in silenzio la UPDATE su uno schema
        # che non puo' riceverla, e il test della scrittura sarebbe stato
        # verde per gentilezza.
        aggiorna = re.match(r"UPDATE\s+stime\s+SET\s+(.*?)\s+WHERE\s+(.*)$",
                            piatta, re.I)
        if aggiorna:
            assegnate = [c.lower() for c in
                         re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=",
                                    aggiorna.group(1))]
            for colonna in assegnate:
                if colonna not in self.colonne["s"]:
                    raise psycopg2.errors.UndefinedColumn(
                        f'column "{colonna}" of relation "stime" does not exist')
            # Le righe raggiunte: id E agenzia, come la WHERE della route.
            valori = list(params or ())
            id_chiesto = valori[-2] if len(valori) >= 2 else None
            agenzia = valori[-1] if valori else None
            colpite = [r for r in self._righe_stime()
                       if r.get("id") == id_chiesto
                       and r.get("agency_id") == agenzia]
            self.description, self._righe = None, []
            self.rowcount = len(colpite)
            return

        # 2. La proiezione, nell'ordine in cui e' scritta.
        selezione = re.search(r"SELECT (.*?) FROM ", piatta, re.I)
        if not selezione:
            self.description, self._righe, self.rowcount = None, [], 0
            return
        voci, profondita, corrente = [], 0, ""
        for carattere in selezione.group(1):
            if carattere == "(":
                profondita += 1
            elif carattere == ")":
                profondita -= 1
            if carattere == "," and profondita == 0:
                voci.append(corrente.strip())
                corrente = ""
                continue
            corrente += carattere
        voci.append(corrente.strip())

        proiezione: list[tuple[str, str, str]] = []   # (modo, chiave, nome)
        for voce in voci:
            via_json = _JSONB.search(voce)
            if via_json:
                nome = via_json.group(3) or via_json.group(2)
                proiezione.append(("json", via_json.group(2).lower(), nome))
                continue
            diretta = re.match(
                r"(\w+)\.(\w+)(?:\s+AS\s+(\w+))?$", voce.strip(), re.I)
            assert diretta, f"voce di SELECT non riconosciuta dal doppio: {voce!r}"
            nome = diretta.group(3) or diretta.group(2)
            proiezione.append(
                ("diretta", f"{diretta.group(1).lower()}.{diretta.group(2).lower()}", nome))

        # 3. Il predicato di tenant, che e' l'altra cosa che questi test
        #    osservano: una correzione che cura il 500 e perde lo scope
        #    sarebbe un peggioramento.
        valori = list(params or ())
        righe = self._righe_stime()
        if re.search(r"agency_id\s*=\s*%s", piatta, re.I):
            chieste = [v for v in valori if v in (A, B)]
            righe = [r for r in righe if not chieste or r.get("agency_id") in chieste]

        materializzate = []
        for riga in righe:
            valori_riga = []
            for modo, chiave, _ in proiezione:
                if modo == "json":
                    valori_riga.append(riga.get(chiave))
                elif chiave.startswith("sd."):
                    valori_riga.append(None)      # LEFT JOIN senza dettaglio
                else:
                    valori_riga.append(riga.get(chiave.split(".", 1)[1]))
            materializzate.append(tuple(valori_riga))

        self._righe = materializzate
        self.rowcount = len(materializzate)
        self.description = [(nome,) for _, _, nome in proiezione]

    def fetchall(self):
        return list(self._righe)

    def fetchone(self):
        return self._righe[0] if self._righe else None

    def close(self):
        pass


class ConnessioneStimeTest:
    def __init__(self, cursore):
        self._cursore = cursore

    def cursor(self, *a, **k):
        return self._cursore

    def commit(self):
        pass

    def close(self):
        pass


@contextmanager
def _connessione(cursore):
    import main as main_module

    originale = main_module.get_connection
    main_module.get_connection = lambda *a, **k: ConnessioneStimeTest(cursore)
    try:
        yield cursore
    finally:
        main_module.get_connection = originale


def _client(monkeypatch, agency_id=A):
    import main as main_module

    from operator_auth.enums import COOKIE_NAME
    from tests.operator_session_helpers import SessionDouble, TEST_TOKEN

    sessioni = SessionDouble(monkeypatch)
    sessioni.login(agency_id=agency_id, role="agency_owner")
    main_module.app.dependency_overrides[
        main_module.legacy_basic_agency_context
    ] = lambda: OperatorContext(
        user_id=None, agency_id=agency_id, role="agency_owner",
        is_platform_admin=False, session_id=None, auth_channel="legacy_basic",
    )
    client = TestClient(main_module.app, raise_server_exceptions=False)
    client.cookies.set(COOKIE_NAME, TEST_TOKEN)
    return client


@pytest.fixture(autouse=True)
def _ripulisci_override():
    yield
    import main as main_module
    main_module.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. La fonte: il certificato dice davvero quello che si sostiene dica
# ---------------------------------------------------------------------------

def test_01_il_certificato_dichiara_assenti_le_due_colonne_del_gestionale():
    """Se questa riga sparisse dal certificato, tutto il resto sarebbe aria."""
    assert ASSENTI_ALLA_BASELINE.get("stime") == {"lead_status", "note_internal"}, ASSENTI_ALLA_BASELINE
    assert len(ASSENTI_ALLA_BASELINE.get("stime_dettagliate", ())) == 28, ASSENTI_ALLA_BASELINE


def _colonne_stime_per_migration() -> dict[str, str]:
    """{colonna: file di migration che la aggiunge a `stime`}."""
    per_colonna: dict[str, str] = {}
    for percorso in sorted((ROOT / "migrations").glob("*.sql")):
        if percorso.name.endswith("_down.sql"):
            continue
        testo = percorso.read_text(encoding="utf-8")
        for blocco in re.finditer(
            r"^ALTER TABLE\s+(?:public\.)?stime\b(.*?);", testo, re.S | re.I | re.M
        ):
            for nome in re.finditer(
                r"ADD COLUMN (?:IF NOT EXISTS )?([A-Za-z_][A-Za-z0-9_]*)",
                blocco.group(1), re.I,
            ):
                per_colonna.setdefault(nome.group(1).lower(), percorso.name)
    return per_colonna


def test_02_ora_le_crea_una_migration_e_una_sola():
    """LA CORREZIONE COMPLETA: le colonne esistono, per via tracciata.

    Prima erano dichiarate da `database.py` e create da nessuna migration, e
    quell'asimmetria rendeva la route rotta per costruzione. La lettura e' stata
    resa non fatale, ma una SCRITTURA non si aggira: o la colonna c'e', o
    `POST /api/admin/stime/{id}/update` non ha nulla su cui scrivere e la
    funzione resta dimezzata.

    056 le crea. Il test pretende che sia UNA SOLA a farlo: due migration che
    aggiungono la stessa colonna sono due opinioni sul suo tipo, e la seconda
    passerebbe in silenzio grazie a `IF NOT EXISTS`.
    """
    per_colonna = _colonne_stime_per_migration()
    for colonna in ("lead_status", "note_internal"):
        assert per_colonna.get(colonna) == MIGRAZIONE_056, per_colonna
        # E restano dichiarate anche da `database.py`: 056 copia quella forma,
        # non ne inventa una nuova.
        assert colonna in _colonne_dichiarate("stime")


def test_02b_la_migration_e_additiva_e_reversibile():
    """Nessun NOT NULL, nessun backfill, nessun vincolo, e un down che esiste.

    056 si applica a caldo: finche' nessuno scrive, il comportamento del
    sistema non cambia di una riga. Se cosi' non fosse, sarebbe una modifica
    di schema mascherata da correzione di route.
    """
    up = (ROOT / "migrations" / MIGRAZIONE_056).read_text(encoding="utf-8")
    corpo = "\n".join(r for r in up.split("\n") if not r.strip().startswith("--"))

    assert re.search(r"ADD COLUMN IF NOT EXISTS\s+lead_status\s+VARCHAR\(32\)",
                     corpo, re.I), corpo
    assert re.search(r"ADD COLUMN IF NOT EXISTS\s+note_internal\s+TEXT",
                     corpo, re.I), corpo
    for vietato in ("SET NOT NULL", "UPDATE stime", "DROP COLUMN",
                    "CREATE INDEX", "ADD CONSTRAINT"):
        assert vietato.lower() not in corpo.lower(), (vietato, corpo)
    # Il runner possiede la transazione UP: un BEGIN qui la spezzerebbe.
    assert "BEGIN;" not in corpo, corpo

    giu = (ROOT / "migrations" / MIGRAZIONE_056.replace(".sql", "_down.sql"))
    assert giu.exists(), giu
    testo_giu = giu.read_text(encoding="utf-8")
    assert "DROP COLUMN IF EXISTS note_internal" in testo_giu
    assert "DROP COLUMN IF EXISTS lead_status" in testo_giu
    assert "BEGIN;" in testo_giu and "COMMIT;" in testo_giu
    # E dice che perde dati: un down che cancella colonne scritte da un
    # operatore non puo' presentarsi come innocuo.
    assert "PERDE DATI" in testo_giu, testo_giu
    # Non tocca le altre ventotto di §3.0.2. Si guardano le ISTRUZIONI, non la
    # prosa: "anno" e' dentro "annota", e un test che cercasse nel commento
    # fallirebbe per una parola italiana.
    istruzioni_giu = "\n".join(
        r for r in testo_giu.split("\n") if not r.strip().startswith("--")).lower()
    for colonna in ASSENTI_ALLA_BASELINE.get("stime_dettagliate", ()):
        assert colonna not in istruzioni_giu, colonna
    assert "stime_dettagliate" not in istruzioni_giu, istruzioni_giu


def test_02c_la_numerazione_resta_contigua():
    """`verify_contiguous` del runner: da 026 in su, senza salti ne' doppioni.

    Una migration con un numero gia' usato, o che apre un buco, fa fallire il
    runner PRIMA di applicare qualunque cosa - quindi anche 055 e tutte le
    altre resterebbero ferme.
    """
    numeri = sorted(
        int(p.name[:3]) for p in (ROOT / "migrations").glob("*.sql")
        if not p.name.endswith("_down.sql") and p.name[:3].isdigit()
        and int(p.name[:3]) >= 26
    )
    assert numeri == list(range(26, 26 + len(numeri))), numeri
    assert numeri[-1] == int(MIGRAZIONE_056[:3]), numeri[-3:]
    assert len(numeri) == len(set(numeri))


# ---------------------------------------------------------------------------
# 2. Strutturale: nessuna proiezione nomina una colonna assente
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("route", ROUTE_DI_LETTURA)
def test_03_nessuna_lettura_admin_nomina_una_colonna_assente(route):
    sql = _sql_della_route(route)
    senza_json = _JSONB.sub(" ", " ".join(sql.split()))

    alias_a_tabella = {"s": "stime", "sd": "stime_dettagliate"}
    colpevoli = []
    for alias, colonna in _QUALIFICATA.findall(senza_json):
        tabella = alias_a_tabella.get(alias.lower())
        if tabella and colonna.lower() in ASSENTI_ALLA_BASELINE.get(tabella, set()):
            colpevoli.append(f"{alias}.{colonna}")
    assert not colpevoli, (
        f"{route} nomina colonne che il certificato dichiara assenti su TEST: "
        f"{sorted(set(colpevoli))} - e' il 500 del run 58aa0e189aaa"
    )


def test_04_la_lista_legge_comunque_i_due_campi_senza_nominarli_come_colonne():
    """La correzione non e' una rimozione: i campi restano nella risposta.

    Toglierli sarebbe stato un cambio di contratto per gli ambienti dove le
    colonne esistono. Il test pretende entrambe le cose - presenti nella
    proiezione, assenti come riferimento di colonna - perche' soddisfarne una
    sola e' il modo piu' facile di sbagliare questa correzione.
    """
    sql = " ".join(_sql_della_route("admin_lista_stime").split())
    for colonna in ("lead_status", "note_internal"):
        assert re.search(rf"->>\s*'{colonna}'", sql), sql
        assert not re.search(rf"\bs\.{colonna}\b", sql), sql


# ---------------------------------------------------------------------------
# 3. Comportamento: la route eseguita contro lo schema di TEST
# ---------------------------------------------------------------------------

def test_05_la_lista_risponde_200_sullo_schema_di_test(monkeypatch):
    """La prova che chiude il FAIL: 200, non 500."""
    client = _client(monkeypatch)
    cursore = CursoreStimeTest()
    assert "lead_status" not in cursore.colonne["s"]

    with _connessione(cursore):
        risposta = client.get("/api/admin/stime?day=oggi")

    assert risposta.status_code == 200, risposta.text
    voci = risposta.json()["items"]
    assert len(voci) == 1, voci
    assert voci[0]["id"] == STIMA_A
    assert voci[0]["nome"] == "AGENZIA A"
    # I campi CI SONO, e valgono null perche' la colonna non c'e'.
    assert "lead_status" in voci[0] and voci[0]["lead_status"] is None, voci[0]
    assert "note_internal" in voci[0] and voci[0]["note_internal"] is None, voci[0]


def test_06_il_doppio_boccia_la_SELECT_precedente(monkeypatch):
    """IL DOPPIO NON E' INDULGENTE, e senza questo test non si saprebbe.

    Si riesegue la proiezione che la route aveva prima della correzione contro
    lo stesso cursore: deve sollevare `UndefinedColumn`. Se non lo facesse,
    `test_05` sarebbe verde per gentilezza del doppio e non per la correzione.
    """
    cursore = CursoreStimeTest()
    with pytest.raises(psycopg2.errors.UndefinedColumn) as errore:
        cursore.execute(
            "SELECT s.id, s.nome, s.lead_status, s.note_internal "
            "FROM stime s WHERE s.agency_id = %s", (A,))
    assert "lead_status" in str(errore.value)


def test_07_la_route_torna_a_500_se_qualcuno_rimette_il_riferimento(monkeypatch):
    """La mutazione, eseguita davvero e non descritta.

    La SELECT della route viene riscritta al volo nella forma precedente e
    rieseguita dallo stesso percorso HTTP: deve tornare 500. E' la prova che
    il 500 del run nasce esattamente li'.
    """
    client = _client(monkeypatch)
    cursore = CursoreStimeTest()

    originale = cursore.execute

    def mutata(sql, params=None):
        regredita = re.sub(
            r"to_jsonb\(s\)\s*->>\s*'(\w+)'\s+AS\s+\w+", r"s.\1", str(sql))
        return originale(regredita, params)

    cursore.execute = mutata
    with _connessione(cursore):
        risposta = client.get("/api/admin/stime?day=oggi")

    assert risposta.status_code == 500, risposta.status_code
    assert risposta.text == "Internal Server Error", risposta.text
    assert len(risposta.text) == 21     # i ventun caratteri del report live


def test_08_dove_le_colonne_esistono_il_valore_arriva(monkeypatch):
    """La correzione non e' "restituisci sempre null".

    Su un ambiente che ha eseguito `migrazione_gestionale_stime` le colonne ci
    sono, e la route deve restituirne il contenuto. Senza questo test, una
    correzione che avesse semplicemente scritto `NULL AS lead_status` sarebbe
    passata come buona.
    """
    client = _client(monkeypatch)
    cursore = CursoreStimeTest(
        colonne_stime=COLONNE_TEST_STIME | {"lead_status", "note_internal"})

    with _connessione(cursore):
        risposta = client.get("/api/admin/stime?day=oggi")

    assert risposta.status_code == 200, risposta.text
    voce = risposta.json()["items"][0]
    assert voce["lead_status"] == "nuovo", voce
    assert voce["note_internal"] == "riservato A", voce


def test_09_la_correzione_non_ha_allargato_lo_scope(monkeypatch):
    """Curare il 500 non deve costare l'isolamento.

    Il run ostile chiede due cose alla stessa risposta: che esista e che non
    contenga l'altra agenzia. Il doppio tiene una riga per agenzia proprio per
    poterlo pretendere qui.
    """
    client = _client(monkeypatch, agency_id=A)
    cursore = CursoreStimeTest()
    with _connessione(cursore):
        risposta = client.get("/api/admin/stime?day=oggi")

    assert "AGENZIA A" in risposta.text
    assert "AGENZIA B" not in risposta.text, risposta.text[:400]
    assert any(
        re.search(r"agency_id\s*=\s*%s", sql, re.I)
        for sql, _ in cursore.istruzioni
    ), cursore.istruzioni


# ---------------------------------------------------------------------------
# 4. La scrittura: la meta' che la proiezione non poteva curare
# ---------------------------------------------------------------------------

def _corpo_update() -> str:
    sorgente = (ROOT / "main.py").read_text(encoding="utf-8")
    corpo = re.search(
        r"def admin_update_stima\(.*?\n(?=@app\.|# ----)", sorgente, re.S)
    assert corpo, "admin_update_stima non trovata"
    return corpo.group(0)


def test_10_la_migration_copre_esattamente_le_colonne_che_la_update_scrive():
    """IL CONTRATTO CHIUSO: cio' che la route scrive, 056 lo crea.

    `LeadUpdate` ha due campi e la route li traduce in due assegnamenti. Se
    domani ne comparisse un terzo - o se 056 ne creasse uno di meno - la
    funzione tornerebbe dimezzata nello stesso modo di prima, e nessuno se ne
    accorgerebbe fino al 500 successivo. Qui i due insiemi si confrontano.
    """
    scritte = set(re.findall(r"updates\.append\(\"(\w+)=", _corpo_update()))
    assert scritte == {"lead_status", "note_internal"}, scritte

    # Gli stessi due campi del modello, non di piu' e non di meno.
    from main import LeadUpdate
    assert set(LeadUpdate.model_fields) == scritte, LeadUpdate.model_fields

    creati = {c for c, f in _colonne_stime_per_migration().items()
              if f == MIGRAZIONE_056}
    assert creati == scritte, (creati, scritte)


def test_11_la_update_non_e_stata_trasformata_in_un_rifiuto():
    """LA FUNZIONE E' RICHIESTA: resta una UPDATE, non diventa un errore.

    Il modo piu' facile di far sparire il 500 sarebbe stato togliere i due
    campi, o rispondere 501 - e in entrambi i casi la route avrebbe smesso di
    fare la cosa per cui esiste, con i test ancora verdi. La correzione e'
    altrove: le colonne esistono.
    """
    corpo = _corpo_update()
    assert "UPDATE stime SET" in corpo, corpo
    assert "{\",\".join(updates)}" in corpo, corpo
    for rifiuto in ("501", "503", "not implemented", "non disponibile"):
        assert rifiuto not in corpo.lower(), (rifiuto, corpo)
    # E il predicato di tenant resta nella WHERE, dove una riga altrui non
    # puo' essere raggiunta nemmeno per un istante.
    assert "WHERE id=%s AND agency_id=%s" in corpo, corpo


def test_12_la_update_resta_scoped_e_404_su_una_stima_altrui(monkeypatch):
    """La correzione di contorno non ha aperto la scrittura ad altre agenzie."""
    client = _client(monkeypatch, agency_id=A)
    cursore = CursoreStimeTest(
        colonne_stime=COLONNE_TEST_STIME | {"lead_status", "note_internal"})

    with _connessione(cursore):
        risposta = client.post(f"/api/admin/stime/{STIMA_B}/update",
                               json={"lead_status": "dirottato"})

    assert risposta.status_code == 404, risposta.text
    scritture = [(sql, p) for sql, p in cursore.istruzioni
                 if sql.upper().startswith("UPDATE")]
    assert scritture, cursore.istruzioni
    for sql, params in scritture:
        assert "agency_id=%s" in sql, sql
        assert A in params, (sql, params)
        assert B not in params, (sql, params)


def test_13_con_le_colonne_la_update_scrive_davvero(monkeypatch):
    """Applicata 056, la funzione fa quello per cui esiste.

    E' la prova che chiude il punto: non "non fallisce piu'", ma "scrive".
    """
    client = _client(monkeypatch, agency_id=A)
    cursore = CursoreStimeTest(
        colonne_stime=COLONNE_TEST_STIME | {"lead_status", "note_internal"})

    with _connessione(cursore):
        risposta = client.post(
            f"/api/admin/stime/{STIMA_A}/update",
            json={"lead_status": "contattato", "note_internal": "richiamare"})

    assert risposta.status_code == 200, risposta.text
    scritture = [(sql, p) for sql, p in cursore.istruzioni
                 if sql.upper().startswith("UPDATE")]
    assert len(scritture) == 1, cursore.istruzioni
    sql, params = scritture[0]
    assert "lead_status=%s" in sql and "note_internal=%s" in sql, sql
    assert "contattato" in params and "richiamare" in params, params


def test_14_senza_le_colonne_la_update_non_puo_funzionare(monkeypatch):
    """L'IMPEDIMENTO, SCRITTO COME TEST INVECE CHE COME NOTA.

    Finche' 056 non e' applicata, lo schema non ha dove mettere quei valori:
    PostgreSQL solleva `UndefinedColumn` e la route non lo intercetta. Questo
    test non celebra il difetto, lo DATA: il giorno in cui TEST ha le colonne,
    il doppio predefinito le avra' e questo test dovra' essere riscritto -
    che e' il momento giusto per rileggerlo.

    Ed e' anche la ragione per cui la correzione non poteva essere solo la
    proiezione: qui non c'e' nessun `->>` che possa salvare la situazione.
    """
    cursore = CursoreStimeTest()
    assert "lead_status" not in cursore.colonne["s"]
    with pytest.raises(psycopg2.errors.UndefinedColumn) as errore:
        cursore.execute(
            "UPDATE stime SET lead_status=%s WHERE id=%s AND agency_id=%s",
            ("contattato", STIMA_A, A))
    assert "lead_status" in str(errore.value)

    client = _client(monkeypatch, agency_id=A)
    with _connessione(CursoreStimeTest()):
        risposta = client.post(f"/api/admin/stime/{STIMA_A}/update",
                               json={"lead_status": "contattato"})
    assert risposta.status_code == 500, risposta.status_code


# ---------------------------------------------------------------------------
# 5. Il secondo difetto: PlatformAdminAgencyRequired non gestita
# ---------------------------------------------------------------------------

SEI_ROUTE_ADMIN = (
    "admin_lista_stime",
    "admin_lista_stime_pro",
    "admin_update_stima",
    "admin_delete_stime",
    "admin_delete_stime_dettagliate",
    "admin_whatsapp_messages",
)

#: Le sei route, con un verbo e un corpo che le raggiunga davvero. Una chiamata
#: mal formata prenderebbe 422 dalla validazione e non arriverebbe mai allo
#: scope: il 403 sembrerebbe assente per il motivo sbagliato.
CHIAMATE_ADMIN = (
    ("get", "/api/admin/stime?day=oggi", None),
    ("get", "/api/admin/stime_pro?day=oggi", None),
    ("post", f"/api/admin/stime/{STIMA_A}/update", {"lead_status": "x"}),
    ("post", "/api/admin/stime/delete", {"ids": [STIMA_A]}),
    ("post", "/api/admin/stime_dettagliate/delete", {"ids": [STIMA_A]}),
    ("get", "/api/admin/whatsapp/messages", None),
)


def _senza_agenzia() -> OperatorContext:
    """Un amministratore di piattaforma senza membership.

    Non e' un caso di laboratorio: `OperatorContext.agency_id` e' `int | None`
    proprio per questo, e `require_agency` solleva invece di inventare
    un'agenzia - che sarebbe il difetto peggiore dei due.
    """
    return OperatorContext(
        user_id=9, agency_id=None, role="platform_admin",
        is_platform_admin=True, session_id=1, auth_channel="operator_session",
    )


def _client_senza_agenzia(monkeypatch):
    import main as main_module

    from operator_auth.enums import COOKIE_NAME
    from tests.operator_session_helpers import SessionDouble, TEST_TOKEN

    sessioni = SessionDouble(monkeypatch)
    sessioni.login(agency_id=A, role="agency_owner")
    main_module.app.dependency_overrides[
        main_module.legacy_basic_agency_context] = _senza_agenzia
    client = TestClient(main_module.app, raise_server_exceptions=False)
    client.cookies.set(COOKIE_NAME, TEST_TOKEN)
    return client


@pytest.mark.parametrize("verbo,percorso,corpo", CHIAMATE_ADMIN)
def test_15_un_contesto_senza_agenzia_riceve_403_non_500(
        monkeypatch, verbo, percorso, corpo):
    """Il 403 che ogni altro router dava gia'.

    Prima `ctx.require_agency()` era nudo in tutte e sei: l'eccezione usciva
    fino a Starlette, che risponde `Internal Server Error`. La stessa domanda
    - "non ho un'agenzia" - riceveva 403 su centocinquanta route e 500 su sei.
    """
    client = _client_senza_agenzia(monkeypatch)
    cursore = CursoreStimeTest()
    with _connessione(cursore):
        risposta = getattr(client, verbo)(percorso, json=corpo) if corpo \
            else getattr(client, verbo)(percorso)

    assert risposta.status_code == 403, (percorso, risposta.status_code,
                                         risposta.text[:200])
    assert risposta.text != "Internal Server Error", percorso
    # E NESSUNA QUERY: il rifiuto arriva prima del database, come in
    # `owner/router_admin.agency_of`. Un 403 emesso dopo aver gia' letto
    # sarebbe un 403 su una lettura avvenuta.
    assert not cursore.istruzioni, (percorso, cursore.istruzioni)


def test_16_tutte_e_sei_le_route_passano_dal_traduttore():
    """Strutturale: nessuna chiama `ctx.require_agency()` nuda.

    Il test di comportamento sopra copre le sei di oggi. Questo copre la
    settima, quella che qualcuno aggiungera' copiando una delle sei.
    """
    sorgente = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(sorgente)
    nude, tradotte = [], []
    for nodo in ast.walk(tree):
        if not isinstance(nodo, ast.FunctionDef) or nodo.name not in SEI_ROUTE_ADMIN:
            continue
        corpo = ast.get_source_segment(sorgente, nodo) or ""
        if "ctx.require_agency()" in corpo:
            nude.append(nodo.name)
        if "agency_of(ctx)" in corpo:
            tradotte.append(nodo.name)
    assert sorted(tradotte) == sorted(SEI_ROUTE_ADMIN), tradotte
    assert not nude, nude


def test_17_il_traduttore_risponde_come_quello_di_owner_admin():
    """Stesso stato e stessa eccezione dell'originale che imita.

    Se OWNER Admin cambiasse idea - 404 invece di 403, per nascondere la route
    - due superfici direbbero cose diverse sulla stessa condizione. Meglio
    accorgersene qui che da un report live.
    """
    import inspect

    import main as main_module
    from owner import router_admin

    mio = inspect.getsource(main_module.agency_of)
    suo = inspect.getsource(router_admin.agency_of)
    for frammento in ("except PlatformAdminAgencyRequired", "403"):
        assert frammento in mio, mio
        assert frammento in suo, suo

    # E si comporta cosi' davvero, non solo a leggerlo.
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as errore:
        main_module.agency_of(_senza_agenzia())
    assert errore.value.status_code == 403
    assert main_module.agency_of(ctx_di_prova(A)) == A


def ctx_di_prova(agency_id: int) -> OperatorContext:
    return OperatorContext(
        user_id=None, agency_id=agency_id, role="agency_owner",
        is_platform_admin=False, session_id=None, auth_channel="legacy_basic",
    )


def test_18_il_traduttore_non_sceglie_un_agenzia_al_posto_di_nessuna():
    """LA CORREZIONE PIU' PERICOLOSA SAREBBE STATA UN VALORE DI RIPIEGO.

    Un `return DEFAULT_AGENCY` avrebbe tolto il 500 e dato a un amministratore
    di piattaforma i dati di un tenant scelto dal codice. Il traduttore deve
    RIFIUTARE, e l'unico intero che puo' restituire e' quello del contesto.
    """
    import inspect

    import main as main_module

    corpo = inspect.getsource(main_module.agency_of)
    assert corpo.count("return") == 1, corpo
    assert "return ctx.require_agency()" in corpo, corpo
    for agenzia in (0, 1, A, B):
        assert main_module.agency_of(ctx_di_prova(agenzia)) == agenzia


# ---------------------------------------------------------------------------
# 6. Copertura: ENTRAMBE le colonne, e tutte le route admin
# ---------------------------------------------------------------------------

def test_19_ogni_colonna_assente_e_coperta_o_non_e_nominata():
    """La verifica richiesta, derivata e non scritta a mano.

    L'elenco viene dal certificato: se §3.0.2 ne aggiungesse una terza, questo
    test la pretenderebbe coperta senza che nessuno debba ricordarsene. Per
    ciascuna, due possibilita' e nessuna terza: o la route non la nomina
    affatto, o la nomina nella forma `->>` che regge anche quando la colonna
    non c'e'.
    """
    alias_a_tabella = {"s": "stime", "sd": "stime_dettagliate"}
    coperte, nude = set(), set()

    for route in ROUTE_DI_LETTURA:
        sql = " ".join(_sql_della_route(route).split())
        senza_json = _JSONB.sub(" ", sql)
        nude |= {
            f"{alias_a_tabella[a.lower()]}.{c.lower()}"
            for a, c in _QUALIFICATA.findall(senza_json)
            if a.lower() in alias_a_tabella
        }
        for tabella, colonne in ASSENTI_ALLA_BASELINE.items():
            for colonna in colonne:
                if re.search(rf"->>\s*'{colonna}'", sql):
                    coperte.add(f"{tabella}.{colonna}")

    tutte = {f"{t}.{c}" for t, colonne in ASSENTI_ALLA_BASELINE.items()
             for c in colonne}
    assert len(tutte) == 30, sorted(tutte)

    # Nessuna assente compare come riferimento di colonna, in nessuna route.
    assert not (tutte & nude), sorted(tutte & nude)
    # Le due del gestionale sono coperte - ENTRAMBE, che e' il punto richiesto.
    assert coperte == {"stime.lead_status", "stime.note_internal"}, coperte
    # E le altre ventotto non sono nominate affatto: `stime_pro` fa `SELECT *`,
    # che una colonna assente non puo' rompere.
    assert len(tutte - coperte) == 28, sorted(tutte - coperte)


def test_20_nessuna_route_admin_nomina_una_colonna_assente():
    """Tutte e sei, non solo le due che leggono `stime`.

    Le DELETE e la lista WhatsApp non proiettano colonne del gestionale oggi.
    "Oggi" e' il motivo per cui il test esiste.
    """
    sorgente = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(sorgente)
    # L'alias decide la TABELLA, e quindi quale elenco di assenti si applica:
    # `s.ascensore` sta su `stime`, dove la colonna c'e'; `sd.ascensore`
    # starebbe su `stime_dettagliate`, dove non c'e'. Confonderli fa fallire il
    # test su codice corretto.
    alias_a_tabella = {"s": "stime", "sd": "stime_dettagliate"}

    colpevoli = []
    for nodo in ast.walk(tree):
        if not isinstance(nodo, ast.FunctionDef) or nodo.name not in SEI_ROUTE_ADMIN:
            continue
        for sotto in ast.walk(nodo):
            if not (isinstance(sotto, ast.Call)
                    and isinstance(sotto.func, ast.Attribute)
                    and sotto.func.attr == "execute"
                    and sotto.args
                    and isinstance(sotto.args[0], ast.Constant)):
                continue
            sql = " ".join(str(sotto.args[0].value).split())
            senza_json = _JSONB.sub(" ", sql)
            for alias, colonna in _QUALIFICATA.findall(senza_json):
                tabella = alias_a_tabella.get(alias.lower())
                if tabella and colonna.lower() in ASSENTI_ALLA_BASELINE.get(
                        tabella, set()):
                    colpevoli.append((nodo.name, f"riferimento: {alias}.{colonna}"))
            # La UPDATE le nomina NUDE, ed e' legittimo SOLO perche' 056 le
            # crea: quel legame lo tiene test_10. Ovunque altro, un
            # assegnamento a una colonna che TEST non ha e' lo stesso difetto
            # in forma di scrittura.
            if nodo.name == "admin_update_stima":
                continue
            for colonna in sorted(ASSENTI_ALLA_BASELINE.get("stime", set())):
                if re.search(rf"(?<![.\w]){colonna}\s*=\s*%s", senza_json):
                    colpevoli.append((nodo.name, f"assegnamento: {colonna}"))
    assert not colpevoli, colpevoli
