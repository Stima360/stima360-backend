"""P27-6 - il routing territoriale del lead pubblico, su alias DICHIARATI.

PERCHE' LA PRIMA VERSIONE DI P27-6 ERA SBAGLIATA

Calcolava la `canonical_key` dal comune ('Alba Adriatica' -> 'alba-adriatica')
e cercava quella. P27-5 pero' ha separato `label` e `canonical_key` di
proposito, e i suoi test lo asseriscono: `network_territories_identity_unq`
nomina `(kind, canonical_key)` e mai `label`, e le chiavi che i test stessi
usano - 'te', 'ap', 'abruzzo' - non sono slug di etichette. Un territorio con
chiave '067001' ed etichetta 'Alba Adriatica' e' legale, e il routing calcolato
non lo avrebbe mai trovato: il lead sarebbe finito al ripiego senza che nessuno
capisse perche'.

La 059 sostituisce il calcolo con una DICHIARAZIONE. Gli unici test che
restano sul confronto fra testi sono quelli sulla normalizzazione minima, che
e' in SQL e non produce identita'.

IL DOPPIO, E COSA NON PROVA

Non c'e' PostgreSQL in questo file: il doppio tiene righe vere e applica i
filtri LEGGENDOLI dalla query, cosi' che togliere una condizione dalla WHERE
faccia fallire i test di comportamento e non solo quelli statici.

La normalizzazione SQL - `lower(btrim(regexp_replace(...)))` - il doppio la
imita in Python. Che le due coincidano davvero lo prova
`tests/test_p27_6_postgres_real.py` su PostgreSQL vero: qui si prova la logica
attorno, la' la query.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from network_routing import repository as routing_repository  # noqa: E402
from network_routing.repository import (  # noqa: E402
    ALIAS_SOURCE_PUBLIC_STIMA,
    NORMALISED,
    ROUTING_KIND,
    RoutingAmbiguous,
)
from network_routing.service import (  # noqa: E402
    RoutingDecision,
    RoutingUnavailable,
    resolve_agency_for_public_stima,
)

DEFAULT_SLUG = "stima360"


def _norm(valore: str) -> str:
    """Le tre pieghe della 059, in Python: lo stesso che fa la query.

    Che questa imitazione e l'SQL coincidano non e' dato per scontato: lo prova
    il file su PostgreSQL reale, che esegue l'espressione vera.
    """
    return " ".join(str(valore).split()).lower()


class FakeCursor:
    """Esegue in Python le due query del routing, sulle righe dichiarate.

    `alias`        [{id, territory_id, source, match_value, status}]
    `territori`    [{id, kind, canonical_key, label}]
    `assegnazioni` [{territory_id, agency_id, status}]
    `agenzie`      [{id, slug, status}]
    """

    def __init__(self, *, alias=(), territori=(), assegnazioni=(), agenzie=()):
        self.alias = [dict(r) for r in alias]
        self.territori = [dict(r) for r in territori]
        self.assegnazioni = [dict(r) for r in assegnazioni]
        self.agenzie = [dict(r) for r in agenzie]
        self.eseguite: list[tuple[str, tuple]] = []
        self._rows: list[dict] = []

    def execute(self, sql, params=None):
        compatta = " ".join(sql.split())
        self.eseguite.append((compatta, tuple(params or ())))

        if "FROM network_territory_aliases" in compatta:
            source, comune, kind = params
            # I FILTRI SI LEGGONO DALLA QUERY, NON SI DANNO PER SCONTATI.
            #
            # Un doppio che applicasse `status = 'active'` per conto proprio
            # resterebbe verde con la WHERE svuotata: sarebbe un'asserzione sul
            # doppio. Qui si guarda che cosa la query CHIEDE.
            f_alias = "al.status = 'active'" in compatta
            f_kind = "t.kind = %s" in compatta
            f_assegnazione = "a.status = 'active'" in compatta
            f_agenzia = "ag.status = 'active'" in compatta
            trovate = [
                {"agency_id": ag["id"]}
                for al in self.alias
                if al["source"] == source
                and _norm(al["match_value"]) == _norm(comune)
                and (al["status"] == "active" or not f_alias)
                for t in self.territori
                if t["id"] == al["territory_id"]
                and (t["kind"] == kind or not f_kind)
                for a in self.assegnazioni
                if a["territory_id"] == t["id"]
                and (a["status"] == "active" or not f_assegnazione)
                for ag in self.agenzie
                if ag["id"] == a["agency_id"]
                and (ag["status"] == "active" or not f_agenzia)
            ]
            trovate.sort(key=lambda r: r["agency_id"])
            # `COUNT(*) OVER ()`: il totale della risposta dentro ogni riga. Se
            # la query smettesse di chiederlo, il doppio deve restituire cio'
            # che chiede al suo posto - altrimenti il rilevamento
            # dell'ambiguita' resterebbe verde con la finestra rimossa.
            if "COUNT(*) OVER ()" in compatta:
                quante = len(trovate)
            else:
                letterale = re.search(r"(\d+) AS instradabili", compatta)
                quante = int(letterale.group(1)) if letterale else len(trovate)
            self._rows = [dict(r, instradabili=quante) for r in trovate]
            return

        if "FROM agencies WHERE slug" in compatta:
            (slug,) = params
            self._rows = [
                {"id": ag["id"]}
                for ag in self.agenzie
                if ag["slug"] == slug and ag["status"] == "active"
            ]
            return

        raise AssertionError(f"query non prevista dal doppio: {compatta}")

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _rete(*, alias_value="Alba Adriatica", alias_status="active",
          territorio_kind="municipality", assegnata_a=7,
          assignment_status="active", agency_status="active",
          con_alias=True, con_fallback=True, fallback_status="active",
          alias_source=ALIAS_SOURCE_PUBLIC_STIMA):
    """Una rete minima: un alias, un territorio, un'assegnazione, il ripiego.

    La `canonical_key` del territorio e' DELIBERATAMENTE un codice che non
    somiglia al comune: '067001'. Se il routing tornasse a calcolarla dal
    testo, ogni test qui dentro fallirebbe - ed e' il punto.
    """
    agenzie = [{"id": 7, "slug": "agenzia-alba", "status": agency_status}]
    if con_fallback:
        agenzie.append({"id": 1, "slug": DEFAULT_SLUG, "status": fallback_status})
    territori = [{"id": 30, "kind": territorio_kind,
                  "canonical_key": "067001", "label": "Alba Adriatica"}]
    alias = []
    if con_alias:
        alias.append({"id": 500, "territory_id": 30, "source": alias_source,
                      "match_value": alias_value, "status": alias_status})
    assegnazioni = []
    if assegnata_a is not None:
        assegnazioni.append({"territory_id": 30, "agency_id": assegnata_a,
                             "status": assignment_status})
    return FakeCursor(alias=alias, territori=territori,
                      assegnazioni=assegnazioni, agenzie=agenzie)


def _decidi(cur, comune="Alba Adriatica"):
    return resolve_agency_for_public_stima(
        cur, comune=comune, fallback_slug=DEFAULT_SLUG
    )


def _solo_codice(sorgente: str) -> str:
    """Il codice eseguibile: via commenti e docstring.

    Le docstring di questo pacchetto spiegano perche' NON c'e' un round-robin e
    perche' la canonicalizzazione e' stata rimossa: cercare quelle parole nel
    testo grezzo vieterebbe la spiegazione insieme alla cosa spiegata.
    """
    albero = ast.parse(sorgente)
    for nodo in ast.walk(albero):
        if isinstance(nodo, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            if (nodo.body and isinstance(nodo.body[0], ast.Expr)
                    and isinstance(nodo.body[0].value, ast.Constant)
                    and isinstance(nodo.body[0].value.value, str)):
                nodo.body[0].value.value = ""
    return ast.unparse(albero)


# ---------------------------------------------------------------------------
# A - il match passa dall'alias, non da una chiave calcolata
# ---------------------------------------------------------------------------

def test_a1_a_declared_alias_routes_to_the_holding_agency():
    decisione = _decidi(_rete())
    assert decisione.agency_id == 7
    assert decisione.source == RoutingDecision.TERRITORY
    assert decisione.matched_value == "Alba Adriatica"


def test_a2_the_canonical_key_is_never_computed_from_the_comune():
    """IL DIFETTO CHE LA 059 CORREGGE, come test.

    Il territorio ha `canonical_key = '067001'` e `label = 'Alba Adriatica'` -
    legale sotto P27-5, e il primo P27-6 lo avrebbe mancato cercando
    'alba-adriatica'. Con l'alias dichiarato instrada.
    """
    cur = _rete()
    assert _decidi(cur).agency_id == 7
    sql = cur.eseguite[0][0]
    assert "canonical_key" not in sql, sql
    assert "label" not in sql, sql
    assert "network_territory_aliases" in sql, sql


def test_a3_no_canonicalizer_module_survives():
    """`network_routing/canonical.py` e' stato rimosso, non solo aggirato."""
    assert not (ROOT / "network_routing" / "canonical.py").exists()
    for modulo in ("repository.py", "service.py", "__init__.py"):
        codice = _solo_codice(
            (ROOT / "network_routing" / modulo).read_text(encoding="utf-8")
        )
        assert "canonical" not in codice.lower(), modulo


def test_a4_only_the_declared_source_is_consulted():
    decisione = _decidi(_rete(alias_source="un_altra_sorgente"))
    assert decisione.source == RoutingDecision.FALLBACK


def test_a5_only_a_municipality_territory_routes():
    """Un alias appeso a una provincia non instrada: il filtro sul kind.

    La gestione alias lo rifiuterebbe gia', ma una riga scritta da SQL
    salterebbe quel controllo - e senza questo filtro instraderebbe un'intera
    provincia a chi presidia un paese.
    """
    for altro in ("province", "postal_code"):
        decisione = _decidi(_rete(territorio_kind=altro))
        assert decisione.source == RoutingDecision.FALLBACK, altro
    assert routing_repository.ROUTING_KIND == "municipality"


def test_a6_no_postal_or_province_branch_exists():
    """I livelli assenti all'ingresso non hanno un ramo nel codice.

    `/api/salva_stima` non riceve CAP ne' provincia e `stime` non ha quelle
    colonne: un ramo che li cercasse sarebbe irraggiungibile.
    """
    codice = _solo_codice(
        (ROOT / "network_routing" / "repository.py").read_text(encoding="utf-8")
    )
    assert "postal_code" not in codice
    assert "'province'" not in codice


# ---------------------------------------------------------------------------
# B - eleggibilita': alias, assegnazione, agenzia
# ---------------------------------------------------------------------------

def test_b1_a_revoked_alias_does_not_route():
    assert _decidi(_rete(alias_status="revoked")).source == RoutingDecision.FALLBACK


@pytest.mark.parametrize("stato", ["suspended", "revoked"])
def test_b2_a_non_active_assignment_does_not_route(stato):
    assert _decidi(_rete(assignment_status=stato)).source == RoutingDecision.FALLBACK


@pytest.mark.parametrize("stato", ["suspended", "archived"])
def test_b3_a_non_active_agency_does_not_route(stato):
    """L'assegnazione e' attiva ma l'agenzia no: il lead non ci va.

    E' il caso che una JOIN senza filtro su `agencies.status` lascerebbe
    passare, consegnando lead a un affiliato sospeso.
    """
    assert _decidi(_rete(agency_status=stato)).source == RoutingDecision.FALLBACK


def test_b4_all_four_conditions_are_in_the_sql():
    """I filtri stanno nella WHERE, non in Python.

    In Python sarebbero righe lette e poi scartate: la query restituirebbe
    l'agenzia sospesa, e qualcuno un giorno userebbe quel risultato prima dello
    scarto.
    """
    codice = _solo_codice(
        (ROOT / "network_routing" / "repository.py").read_text(encoding="utf-8")
    )
    for condizione in ("al.status = 'active'", "a.status = 'active'",
                       "ag.status = 'active'", "t.kind = %s"):
        assert condizione in codice, condizione


# ---------------------------------------------------------------------------
# C - normalizzazione minima, e niente di piu'
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("comune", [
    "Alba Adriatica", "alba adriatica", "ALBA ADRIATICA",
    "  Alba   Adriatica  ", "alba  ADRIATICA",
])
def test_c1_the_three_minimal_foldings_match(comune):
    """Maiuscole, spazi ai lati, spazi interni ripetuti: le tre pieghe."""
    assert _decidi(_rete(), comune=comune).agency_id == 7


@pytest.mark.parametrize("comune", [
    "alba-adriatica",      # lo SLUG non e' il comune
    "AlbaAdriatica",       # niente spazi
    "Alba Adriatic",       # un carattere in meno
    "Alba Adriatica Alta",
])
def test_c2_nothing_beyond_the_three_foldings_matches(comune):
    """Niente slug, niente traslitterazione, niente somiglianze.

    'alba-adriatica' NON deve instradare: e' esattamente la trasformazione che
    la prima versione applicava, e che la 059 ha sostituito con una
    dichiarazione.
    """
    assert _decidi(_rete(), comune=comune).source == RoutingDecision.FALLBACK


def test_c3_the_normalisation_is_one_expression_in_three_places():
    """Migration, repository di routing e repository di gestione: la stessa.

    Se divergessero, il database garantirebbe un'unicita' su cui la query non
    conta, e il controllo di esistenza direbbe "libero" su un valore che
    l'indice rifiuta - un 500 al posto di un 409.
    """
    espressione = NORMALISED.format("match_value")
    migrazione = (ROOT / "migrations" / "059_p27_territory_aliases.sql").read_text(
        encoding="utf-8"
    )
    assert espressione in migrazione, espressione
    from platform_admin import aliases_repository
    assert aliases_repository.NORMALISED == NORMALISED


def test_c4_the_accents_are_not_removed():
    """Un comune accentato si dichiara e instrada com'e'.

    E' il limite che la prima versione aveva - rifiutava gli accenti perche' non
    sapeva traslitterarli - e che la dichiarazione elimina: nessuno deve
    indovinare una convenzione, basta scrivere il nome.
    """
    cur = _rete(alias_value="Citta' Sant'Angelo")
    assert _decidi(cur, comune="citta' sant'angelo").agency_id == 7


# ---------------------------------------------------------------------------
# D - il ripiego
# ---------------------------------------------------------------------------

def test_d1_no_alias_falls_back():
    decisione = _decidi(_rete(con_alias=False))
    assert decisione.agency_id == 1
    assert decisione.source == RoutingDecision.FALLBACK
    assert decisione.matched_value == "Alba Adriatica"


@pytest.mark.parametrize("comune", [None, "", "   "])
def test_d2_an_empty_comune_falls_back_without_querying(comune):
    cur = _rete()
    decisione = _decidi(cur, comune=comune)
    assert decisione.source == RoutingDecision.FALLBACK
    assert decisione.matched_value is None
    assert all("network_territory_aliases" not in q for q, _p in cur.eseguite)


def test_d3_no_fallback_available_raises_deterministically():
    """Nessun alias e nessun ripiego attivo: errore, mai un'agenzia a caso.

    La rete contiene un'agenzia perfettamente valida (7) che non presidia il
    comune: se il codice scegliesse "una qualsiasi attiva", questo test
    passerebbe con `agency_id == 7`.
    """
    cur = _rete(con_alias=False, con_fallback=False)
    with pytest.raises(RoutingUnavailable) as errore:
        _decidi(cur)
    assert DEFAULT_SLUG in str(errore.value)


def test_d4_a_suspended_fallback_is_not_a_fallback():
    with pytest.raises(RoutingUnavailable):
        _decidi(_rete(con_alias=False, fallback_status="suspended"))


# ---------------------------------------------------------------------------
# E - determinismo
# ---------------------------------------------------------------------------

def test_e1_the_same_question_gives_the_same_answer():
    for _ in range(25):
        assert _decidi(_rete()).agency_id == 7


def test_e2_no_selection_machinery_anywhere():
    """Nessun random, contatore, punteggio o ordinamento per carico."""
    for modulo in ("repository.py", "service.py", "__init__.py"):
        codice = _solo_codice(
            (ROOT / "network_routing" / modulo).read_text(encoding="utf-8")
        )
        for vietato in ("random", "shuffle", "choice", "round_robin", "score",
                        "weight", "ORDER BY random", "LIMIT 1"):
            assert vietato not in codice, (modulo, vietato)
        assert "ORDER BY instradabili" not in codice, modulo


def test_e3_two_routable_agencies_raise_instead_of_choosing():
    """Invariante del database violata: non si sceglie, si solleva.

    `uq_territory_alias_active_value` e `uq_agency_territory_single_active`
    rendono il caso irraggiungibile. Il doppio non ha indici, quindi qui si
    puo' costruire - ed e' l'unico posto in cui si prova che non si tira a
    sorte.
    """
    cur = FakeCursor(
        alias=[{"id": 500, "territory_id": 30,
                "source": ALIAS_SOURCE_PUBLIC_STIMA,
                "match_value": "Alba Adriatica", "status": "active"}],
        territori=[{"id": 30, "kind": "municipality",
                    "canonical_key": "067001", "label": "Alba"}],
        assegnazioni=[
            {"territory_id": 30, "agency_id": 7, "status": "active"},
            {"territory_id": 30, "agency_id": 8, "status": "active"},
        ],
        agenzie=[{"id": 7, "slug": "a", "status": "active"},
                 {"id": 8, "slug": "b", "status": "active"},
                 {"id": 1, "slug": DEFAULT_SLUG, "status": "active"}],
    )
    with pytest.raises(RoutingAmbiguous):
        _decidi(cur)


# ---------------------------------------------------------------------------
# F - sicurezza
# ---------------------------------------------------------------------------

def test_f1_the_signature_admits_no_agency_from_the_caller():
    import inspect
    parametri = set(inspect.signature(resolve_agency_for_public_stima).parameters)
    assert parametri == {"cur", "comune", "fallback_slug"}, parametri


def test_f2_a_client_supplied_agency_id_is_not_accepted():
    with pytest.raises(TypeError):
        resolve_agency_for_public_stima(
            _rete(), comune="Alba Adriatica",
            fallback_slug=DEFAULT_SLUG, agency_id=99,
        )


def test_f3_the_public_payload_never_reaches_the_agency_column():
    sorgente = (ROOT / "main.py").read_text(encoding="utf-8")
    inizio = sorgente.index("INSERT INTO stime\n")
    blocco = sorgente[inizio:sorgente.index("RETURNING id", inizio)]
    assert "agency_id" in blocco
    assert "system_ctx.require_agency()" in sorgente[inizio:inizio + 2000]
    for vietato in ('data["agency_id"]', 'data.get("agency_id")',
                    'raw.get("agency_id")'):
        assert vietato not in sorgente, vietato


# ---------------------------------------------------------------------------
# G - integrazione, transazione, idempotenza
# ---------------------------------------------------------------------------

def test_g1_the_routed_factory_returns_the_type_the_bridge_accepts():
    """Il bridge accetta SOLO un `SystemAgencyContext` con origin public_stima.

    La fabbrica sta in `network_routing` e non in `core/scope.py`: quel modulo
    e' sigillato da tre invarianti P26-1 - un solo punto di costruzione, import
    chiusi, superficie pubblica dichiarata - e il primo tentativo di P27-6,
    che ce l'aveva messa, e' stato respinto da
    `test_p26_1_scope_enforcement`.
    """
    from operator_auth.context import SystemAgencyContext
    from network_routing.service import system_context_for_routed_public_stima

    ctx, decisione = system_context_for_routed_public_stima(
        _rete(), comune="Alba Adriatica"
    )
    assert type(ctx) is SystemAgencyContext
    assert ctx.origin == "public_stima"
    assert ctx.agency_id == 7
    assert decisione.source == RoutingDecision.TERRITORY


def test_g2_routing_and_the_stima_insert_share_one_connection():
    """Stessa connessione, quindi stessa transazione: atomici.

    E' l'INSERT su `stime` che condivide la transazione con la decisione - non
    quella sui `leads`, che il bridge esegue dopo il commit su una connessione
    propria. La distinzione conta e la prova il test g4.
    """
    sorgente = (ROOT / "main.py").read_text(encoding="utf-8")
    inizio = sorgente.index("def _routed_public_stima_system_context")
    corpo = sorgente[inizio:sorgente.index("\n\n\n", inizio)]
    assert "conn.cursor(" in corpo, corpo
    assert "get_connection()" not in corpo, corpo
    chiamata = sorgente.index("_routed_public_stima_system_context(\n")
    assert "conn, comune=comune_db" in sorgente[chiamata:chiamata + 200]


def test_g3_the_routing_runs_before_the_insert_and_the_commit():
    sorgente = (ROOT / "main.py").read_text(encoding="utf-8")
    decisione = sorgente.index("_routed_public_stima_system_context(\n")
    insert = sorgente.index("INSERT INTO stime\n")
    commit = sorgente.index("conn.commit()", insert)
    assert decisione < insert < commit


def test_g4_a_lead_can_never_be_linked_to_a_stima_of_another_agency():
    """IDEMPOTENZA, GARANTITA DAL DATABASE E NON DAL PERCORSO.

    Il lead nasce DOPO il commit della stima, su un'altra connessione. Se un
    retry del bridge ricalcolasse il routing e i territori fossero cambiati nel
    frattempo, il lead potrebbe finire in un'altra agenzia.

    Non puo': `trg_lead_stime_agency_coherence` (migration 033) e' BEFORE
    INSERT OR UPDATE su `lead_stime` e SOLLEVA quando
    `leads.agency_id <> stime.agency_id`. L'inserimento del legame verrebbe
    rifiutato e la transazione del bridge annullata - quindi `stime.agency_id`
    e' la fonte stabile IMPOSTA, non una convenzione.
    """
    migrazione = (ROOT / "migrations" / "033_p26_stima_agency_enforce.sql").read_text(
        encoding="utf-8"
    )
    assert "CREATE OR REPLACE FUNCTION lead_stime_agency_coherence()" in migrazione
    assert "a_lead <> a_stima" in migrazione
    assert "trg_lead_stime_agency_coherence" in migrazione
    assert "BEFORE INSERT OR UPDATE OF lead_id, stima_id" in migrazione


def test_g5_an_already_linked_stima_is_never_re_routed():
    """E il bridge esce prima di scrivere, se il lead esiste gia'.

    Un retry sullo stesso `stima_id` trova `lead_stime` e ritorna
    `already_linked`: il lead resta dov'e' nato, qualunque cosa sia successa
    ad alias e territori nel frattempo.
    """
    sorgente = (ROOT / "core" / "repository.py").read_text(encoding="utf-8")
    inizio = sorgente.index("def bridge_public_stima")
    corpo = sorgente[inizio:inizio + 4000]
    assert "already_linked" in corpo
    posizione = corpo.index("already_linked")
    for scrittura in ("INSERT INTO contacts", "INSERT INTO leads"):
        if scrittura in corpo:
            assert posizione < corpo.index(scrittura)


def test_g6_the_bridge_receives_the_context_and_never_rebuilds_it():
    """Il bridge non chiama il routing: riceve il contesto gia' deciso."""
    sorgente = (ROOT / "core" / "repository.py").read_text(encoding="utf-8")
    assert "network_routing" not in sorgente
    servizio = (ROOT / "core" / "service.py").read_text(encoding="utf-8")
    assert "network_routing" not in servizio


def test_g7_the_routing_decision_is_not_written_to_platform_audit_log():
    """Il registro amministrativo non diventa un log di traffico."""
    for modulo in ("repository.py", "service.py"):
        codice = (ROOT / "network_routing" / modulo).read_text(encoding="utf-8")
        assert "platform_audit_log" not in codice, modulo
    sorgente = (ROOT / "main.py").read_text(encoding="utf-8")
    inizio = sorgente.index("public_stima_routing")
    finestra = sorgente[inizio - 1500:inizio + 500]
    istruzioni = "\n".join(
        r for r in finestra.split("\n") if not r.strip().startswith("#")
    )
    assert "platform_audit_log" not in istruzioni


def test_g8_the_detail_funnel_still_uses_the_default_factory():
    """`salva_stima_dettagliata` non viene instradata: non fa nascere un lead
    e nel caso orfano non ha un comune proprio su cui decidere."""
    sorgente = (ROOT / "main.py").read_text(encoding="utf-8")
    inizio = sorgente.index("if detail_agency_id is None:")
    blocco = sorgente[inizio:inizio + 200]
    assert "_public_stima_system_context(conn)" in blocco
    assert "_routed" not in blocco


# ---------------------------------------------------------------------------
# H - perimetro
# ---------------------------------------------------------------------------

def test_h1_the_routing_package_imports_no_tenant_module():
    for modulo in ("repository.py", "service.py", "__init__.py"):
        albero = ast.parse(
            (ROOT / "network_routing" / modulo).read_text(encoding="utf-8")
        )
        importati = set()
        for nodo in ast.walk(albero):
            if isinstance(nodo, ast.Import):
                importati |= {a.name.split(".")[0] for a in nodo.names}
            elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
                importati.add(nodo.module.split(".")[0])
        for vietato in ("core", "main", "platform_admin", "owner", "flow",
                        "buy", "match", "property"):
            assert vietato not in importati, (modulo, vietato, sorted(importati))


def test_h2_the_routing_package_never_writes():
    for modulo in ("repository.py", "service.py"):
        codice = _solo_codice(
            (ROOT / "network_routing" / modulo).read_text(encoding="utf-8")
        )
        for vietato in ("INSERT", "UPDATE", "DELETE", "commit"):
            assert vietato not in codice, (modulo, vietato)


def test_h3_the_default_factory_is_unchanged():
    """La fabbrica P26-1 resta quella: il ripiego E' il vecchio comportamento."""
    import inspect
    from core.scope import system_context_for_public_stima
    corpo = inspect.getsource(system_context_for_public_stima)
    assert "resolve_default_agency_id(cur)" in corpo
    assert "alias" not in _solo_codice(corpo).lower()


def test_h4_the_alias_source_is_the_one_the_enums_declare():
    from platform_admin.enums import ALIAS_SOURCES
    assert ALIAS_SOURCE_PUBLIC_STIMA in ALIAS_SOURCES
    assert len(ALIAS_SOURCES) == 1, ALIAS_SOURCES
    assert ROUTING_KIND == "municipality"


# ---------------------------------------------------------------------------
# I - L'INVENTARIO ESAUSTIVO
#
# Arrivato qui da tests/test_p27_5_territories.py, dove la sezione K dichiara
# che un elenco esaustivo appartiene SEMPRE alla fase piu' recente. Tenerlo
# indietro significherebbe romperlo a ogni fase nuova, e chi lo ripara finisce
# per allentarlo - da uguaglianza a sottoinsieme - che e' il modo in cui una
# sorveglianza smette di sorvegliare.
#
# Aggiornato con le tre route e le due mutazioni degli alias, e con la 059.
# ---------------------------------------------------------------------------

ROUTER_PREFIX = "/api/platform"


def test_i1_059_is_the_highest_version_and_follows_058():
    """La cima della sequenza, affermata dalla fase che l'ha alzata."""
    from scripts import p26_migrate as runner

    numeri = sorted(m.number for m in runner.discover_migrations())
    # 64 e' la 064 di P29-2.1 (fondazione del Communication Domain: il ledger
    # dei messaggi e l'audit dei tentativi, nella stessa migration). Prima era
    # 63, la proiezione dei consensi di P29-1.1. Il perno resta quello che era -
    # la 059 e' l'ULTIMA di P27 e segue la 058 - e sale di una fase per volta,
    # deliberatamente.
    # P29-2.6E ha aggiunto la 065 (`contact_id` nullable per le comunicazioni
    # SERVICE senza contatto, con la stima come genitore di lifecycle), LMC-1A
    # la 066 (il grant pre-incarico owner/stima) e LMC-1B la 067 (il motivo
    # `owner_login_link` nel ledger). Il perno sale di una fase per volta,
    # deliberatamente.
    # LMC-10 ha aggiunto la 068 (la tabella degli override del
    # proprietario, approvata dallo STORAGE GATE).
    # LMC-12 ha aggiunto la 069 (`owner_home_notifications`, lo stream di
    # notifiche PRE-INCARICO, approvata dal DESIGN GATE): dominio OWNER, non
    # di questa fase. La coda si nomina, come sempre.
    # LMC-15 ha aggiunto la 070 (`stima_acquisitions` e
    # `stima_inspections`, il ponte di acquisizione approvato dallo SCHEMA
    # GATE di LMC-15A.2): dominio ACQUISITION, non di questa fase. La coda
    # si nomina, come sempre.
    assert numeri[-1] == 70, numeri[-4:]
    assert 64 in numeri, numeri[-4:]
    assert 59 in numeri and 58 in numeri, numeri[-4:]
    assert 58 in numeri, numeri[-4:]
    runner.verify_contiguous(runner.discover_migrations())

    migrazioni = {m.version: m for m in runner.discover_migrations()}
    assert "059_p27_territory_aliases" in migrazioni, sorted(migrazioni)[-4:]
    assert runner.validate_migration(migrazioni["059_p27_territory_aliases"]) == []


def test_i2_the_real_application_exposes_exactly_the_platform_surface():
    """Ogni route di /api/platform, per uguaglianza. Le tre nuove incluse.

    Uguaglianza e non inclusione: una route aggiunta per sbaglio - o lasciata
    in piedi da un esperimento - e' precisamente cio' che questo test esiste
    per vedere, e un `>=` non la vedrebbe.
    """
    import main

    spec = main.app.openapi()
    trovate = {
        (metodo.upper(), percorso)
        for percorso, operazioni in spec["paths"].items()
        if percorso.startswith(ROUTER_PREFIX)
        for metodo in operazioni
    }
    assert trovate == {
        ("GET", f"{ROUTER_PREFIX}/me"),
        # P27-2
        ("GET", f"{ROUTER_PREFIX}/agencies"),
        ("POST", f"{ROUTER_PREFIX}/agencies"),
        ("GET", f"{ROUTER_PREFIX}/agencies/{{agency_id}}"),
        ("PATCH", f"{ROUTER_PREFIX}/agencies/{{agency_id}}"),
        # P27-3
        ("GET", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/operators"),
        ("POST", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/operators"),
        ("GET", f"{ROUTER_PREFIX}/operators/{{operator_user_id}}"),
        ("PATCH", f"{ROUTER_PREFIX}/operators/{{operator_user_id}}"),
        ("PATCH", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/operators"
                  "/{operator_user_id}/membership"),
        ("PUT", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/owner"),
        # P27-4
        ("GET", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/configuration"),
        ("PATCH", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/configuration"),
        # P27-5
        ("GET", f"{ROUTER_PREFIX}/territories"),
        ("POST", f"{ROUTER_PREFIX}/territories"),
        ("GET", f"{ROUTER_PREFIX}/territories/{{territory_id}}"),
        ("POST", f"{ROUTER_PREFIX}/territories/{{territory_id}}/transfer"),
        ("GET", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/territories"),
        ("POST", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/territories"),
        ("PATCH", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/territories"
                  "/{assignment_id}"),
        # P27-6 - la dichiarazione degli alias. Nessuna DELETE: si revoca.
        ("GET", f"{ROUTER_PREFIX}/territories/{{territory_id}}/aliases"),
        ("POST", f"{ROUTER_PREFIX}/territories/{{territory_id}}/aliases"),
        ("PATCH", f"{ROUTER_PREFIX}/aliases/{{alias_id}}"),
        # P28 - il contesto di agenzia del Superadmin. Due route, e nessuna
        # DELETE: si esce con una POST dichiarata, non cancellando una risorsa.
        ("POST", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/enter"),
        ("POST", f"{ROUTER_PREFIX}/agency-context/exit"),
    }, sorted(trovate)


def test_i3_no_platform_route_deletes():
    """Nessuna route della piattaforma cancella. Gli alias non fanno eccezione."""
    import main

    spec = main.app.openapi()
    assert not [
        percorso for percorso, operazioni in spec["paths"].items()
        if percorso.startswith(ROUTER_PREFIX) and "delete" in operazioni
    ]


def test_i4_every_mutation_in_the_package_goes_through_the_shared_order():
    """UNA copia dell'ordine, e TREDICI mutazioni che ci passano tutte.

    Undici erano; le due degli alias si aggiungono e passano dalla stessa
    `audit_then_commit`. L'uguaglianza esatta e' il perno: un `>=` lascerebbe
    passare una mutazione nuova che committa per conto suo, che e' il difetto
    che questo test esiste per intercettare.
    """
    pacchetto = ROOT / "platform_admin"

    commit = {
        p.name: p.read_text(encoding="utf-8").count("conn.commit()")
        for p in sorted(pacchetto.glob("*.py"))
        if p.name != "database.py"
    }
    # P28 - DUE, ed entrambi in `transaction.py`. Il perno non e' mai stato
    # "un commit": e' "l'ordine fra scrittura e audit sta in un posto solo".
    # `commit_then_audit` e' la deroga per le operazioni che TOLGONO un
    # accesso - senza, un registro non scrivibile terrebbe il Superadmin
    # DENTRO un'agenzia - e vive accanto alla regola che deroga.
    assert sum(commit.values()) == 2, commit
    assert commit["transaction.py"] == 2, commit

    chiamanti = []
    for p in sorted(pacchetto.glob("*_service.py")):
        albero = ast.parse(p.read_text(encoding="utf-8"))
        chiamanti += [
            n.name for n in ast.walk(albero)
            if isinstance(n, ast.FunctionDef)
            and any(
                isinstance(i, ast.Call) and isinstance(i.func, ast.Name)
                and i.func.id == "audit_then_commit"
                for i in ast.walk(n)
            )
        ]
    assert set(chiamanti) == {
        "create_agency", "update_agency",                       # P27-2
        "create_agency_operator", "update_operator",            # P27-3
        "update_membership", "transfer_owner",                  # P27-3
        "update_configuration",                                 # P27-4
        "create_territory", "assign_territory",                 # P27-5
        "update_assignment", "transfer_territory",              # P27-5
        "create_alias", "update_alias",                         # P27-6
        # P28. `exit_agency` NON compare, e l'assenza e' la decisione: passa da
        # `commit_then_audit`, la deroga per le operazioni che TOLGONO un
        # accesso. Vive nello stesso file della regola - `transaction.py` -
        # quindi resta sorvegliata esattamente come le altre.
        "enter_agency",                                         # P28
    }, sorted(chiamanti)


def test_i5_the_alias_package_never_deletes_a_row():
    """Nessuna DELETE in tutta la superficie alias. Si revoca, si conserva."""
    for modulo in ("aliases_repository.py", "aliases_service.py"):
        codice = _solo_codice(
            (ROOT / "platform_admin" / modulo).read_text(encoding="utf-8")
        )
        assert "DELETE" not in codice.upper(), modulo


# ---------------------------------------------------------------------------
# J - IDEMPOTENZA: la rete cambia, il lead no
#
# La decisione vive in `stime.agency_id`, scritta e committata prima che il
# bridge parta. Se qualcuno rieseguisse il bridge sullo stesso `stima_id` dopo
# che alias e assegnazioni sono cambiati, il lead potrebbe finire altrove.
#
# Due meccanismi lo impediscono, e vanno provati SEPARATAMENTE perche' coprono
# casi diversi:
#
#   1. stessa agenzia -> il bridge trova il legame e ritorna `already_linked`
#      senza scrivere niente;
#   2. agenzia diversa -> il legame esistente NON si vede (la ricerca e'
#      scopata sull'agenzia del contesto), il bridge prova a crearne uno nuovo,
#      e `trg_lead_stime_agency_coherence` della 033 RIFIUTA l'inserimento
#      perche' `leads.agency_id <> stime.agency_id`.
#
# Il secondo e' quello che conta: e' il database a dire di no, non una
# convenzione del codice, e quindi vale anche per una strada che oggi non
# esiste.
# ---------------------------------------------------------------------------

class BridgeCursor:
    """Il piccolo insieme di istruzioni che il bridge emette, piu' il TRIGGER.

    `stime_agency_id` e' la decisione gia' PERSISTITA. L'inserimento del legame
    la confronta con l'agenzia del lead e solleva se divergono: e' la 033
    riprodotta, ed e' l'unica ragione per cui questo doppio puo' dire qualcosa
    sull'idempotenza invece di limitarsi a registrare chiamate.
    """

    class CoerenzaViolata(RuntimeError):
        """Quel che il trigger solleva: il legame non viene creato."""

    def __init__(self, *, stime_agency_id: int, leads=(), links=(),
                 fallisce_su=None):
        #: L'istruzione su cui il primo passaggio si interrompe, una volta
        #: sola. Serve al caso in cui il bridge muore PRIMA di creare il
        #: legame: senza, l'idempotenza sarebbe provata solo dove un legame
        #: gia' esiste, che e' il caso facile.
        self.fallisce_su = fallisce_su
        self.stime_agency_id = stime_agency_id
        self.leads = {l["id"]: dict(l) for l in leads}
        self.links = [dict(x) for x in links]
        self.inseriti = {"contacts": [], "leads": [], "lead_stime": []}
        self.calls: list[tuple[str, object]] = []
        self._rows: list = []
        self._prossimo = 900

    def execute(self, sql, params=None):
        compatta = " ".join(str(sql).split())
        self.calls.append((compatta, params))
        basso = compatta.lower()

        if self.fallisce_su and self.fallisce_su in basso:
            self.fallisce_su = None
            raise RuntimeError("la connessione e' caduta a meta' del bridge")

        if "pg_advisory_xact_lock" in basso:
            self._rows = [{"locked": True}]
        elif "from lead_stime ls" in basso:
            # La ricerca E' SCOPATA: l'agenzia del contesto e' il primo
            # parametro dopo lo stima_id. Un legame di un'altra agenzia non si
            # vede - ed e' proprio per questo che serve il trigger.
            stima_id = params[0]
            agenzia = params[1] if len(params) > 1 else None
            self._rows = [
                {"lead_id": x["lead_id"], "contact_id": self.leads[x["lead_id"]]["contact_id"]}
                for x in self.links
                if x["stima_id"] == stima_id
                and (agenzia is None or self.leads[x["lead_id"]]["agency_id"] == agenzia)
            ]
        elif "from contacts" in basso:
            self._rows = []
        elif "insert into contacts" in basso:
            self._prossimo += 1
            riga = {**params, "id": self._prossimo}
            self.inseriti["contacts"].append(riga)
            self._rows = [riga]
        elif "insert into leads" in basso:
            self._prossimo += 1
            riga = {**params, "id": self._prossimo}
            self.inseriti["leads"].append(riga)
            self.leads[riga["id"]] = riga
            self._rows = [riga]
        elif "insert into lead_stime" in basso:
            lead_id, stima_id = params[0], params[1]
            agenzia_lead = self.leads[lead_id]["agency_id"]
            if agenzia_lead != self.stime_agency_id:
                raise self.CoerenzaViolata(
                    "trg_lead_stime_agency_coherence: "
                    f"leads.agency_id={agenzia_lead} <> "
                    f"stime.agency_id={self.stime_agency_id}"
                )
            legame = {"lead_id": lead_id, "stima_id": stima_id}
            self.inseriti["lead_stime"].append(legame)
            self.links.append(legame)
            self._rows = [{"lead_id": lead_id, "stima_id": stima_id}]
        else:  # pragma: no cover - un'istruzione nuova va notata
            raise AssertionError(f"istruzione non prevista: {compatta}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


@pytest.fixture
def esegui_bridge(monkeypatch):
    """Esegue il bridge vero su un `BridgeCursor`."""
    from contextlib import contextmanager

    from core import repository as core_repository

    def _run(cursore, *, agency_id, stima_id=901):
        from operator_auth.context import SystemAgencyContext

        @contextmanager
        def _core_cursor(*a, **k):
            yield (None, cursore)

        monkeypatch.setattr(core_repository, "core_cursor", _core_cursor)
        ctx = SystemAgencyContext(agency_id=agency_id, origin="public_stima")
        contatto = {
            "contact_type": "person", "first_name": "Mario", "last_name": "Rossi",
            "company_name": None, "display_name": "Mario Rossi",
            "email": "mario@example.test", "email_normalized": "mario@example.test",
            "phone": None, "phone_normalized": None, "secondary_phone": None,
            # P29-1.4: il consenso non e' piu' un campo del contatto. Il
            # bridge lo riceve come decisione separata e lo consegna al
            # dominio `consent/`, quindi `contact_data` non lo nomina piu'
            # - e `_reject_consent_owned` rifiuta chi ci prova.
            "source": "public_stima", "status": "active",
            "notes": None,
        }
        lead = {
            "source": "public_stima", "pipeline": "sell", "stage": "new",
            "priority": "normal", "status": "open", "assigned_to": None,
            "estimated_value": None, "next_action_at": None, "lost_reason": None,
            "notes": None,
        }
        return core_repository.bridge_public_stima(
            stima_id, contatto, lead, "related", system_ctx=ctx
        )

    return _run


def test_j1_the_first_run_lands_the_lead_in_the_routed_agency(esegui_bridge):
    cur = BridgeCursor(stime_agency_id=7)
    esito = esegui_bridge(cur, agency_id=7)
    assert esito["status"] == "linked"
    assert cur.inseriti["leads"][0]["agency_id"] == 7
    assert len(cur.inseriti["lead_stime"]) == 1


def test_j2_a_retry_with_the_same_agency_writes_nothing_more(esegui_bridge):
    """Stesso `stima_id`, secondo giro: `already_linked` e zero scritture."""
    cur = BridgeCursor(stime_agency_id=7)
    primo = esegui_bridge(cur, agency_id=7)
    prima = {k: len(v) for k, v in cur.inseriti.items()}

    secondo = esegui_bridge(cur, agency_id=7)
    assert secondo["status"] == "already_linked"
    assert secondo["lead_id"] == primo["lead_id"]
    assert {k: len(v) for k, v in cur.inseriti.items()} == prima


def test_j3_the_033_trigger_is_the_last_defence_and_not_the_retry_path(
    esegui_bridge,
):
    """Il trigger resta, e fa il suo mestiere: fermare cio' che non deve esistere.

    Questo test costruisce a mano la situazione che l'idempotenza ora IMPEDISCE:
    un contesto dell'agenzia 8 su una stima che porta scritto 7. Il legame
    dell'agenzia 7 non si vede - la ricerca e' scopata - il bridge arriva fino
    all'inserimento del legame, e li' `trg_lead_stime_agency_coherence` lo
    rifiuta.

    ATTENZIONE A COSA QUESTO NON E'. Fino alla revisione, questa era la strada
    che un retry normale percorreva davvero, e finiva in ECCEZIONE invece che
    bene: coerenza, non idempotenza. Adesso il contesto si ricava da
    `stime.agency_id` (test j5 e j6), quindi un'agenzia diversa non puo' piu'
    arrivare qui passando dal funnel. Il trigger resta come ultima difesa del
    database contro una strada che oggi non esiste - e questo test e' l'unico
    posto in cui quella strada viene costruita apposta per vederla fallire.
    """
    cur = BridgeCursor(stime_agency_id=7)
    primo = esegui_bridge(cur, agency_id=7)
    assert cur.inseriti["leads"][0]["agency_id"] == 7

    with pytest.raises(BridgeCursor.CoerenzaViolata):
        esegui_bridge(cur, agency_id=8)

    # Il legame originale e' ancora l'unico, e punta al lead dell'agenzia 7.
    assert len(cur.inseriti["lead_stime"]) == 1
    assert cur.inseriti["lead_stime"][0]["lead_id"] == primo["lead_id"]
    assert cur.stime_agency_id == 7


def test_j4_the_writer_stamps_the_stima_before_the_bridge_runs(esegui_bridge):
    """E la decisione e' gia' persistita quando il bridge parte.

    E' quel che rende il trigger capace di rifiutare: senza `stime.agency_id`
    scritto e committato prima, non ci sarebbe niente con cui confrontare.
    """
    sorgente = (ROOT / "main.py").read_text(encoding="utf-8")
    insert = sorgente.index("INSERT INTO stime\n")
    commit = sorgente.index("conn.commit()", insert)
    bridge = sorgente.index("bridge_public_stima", commit)
    assert insert < commit < bridge


# ---------------------------------------------------------------------------
# L'IDEMPOTENZA VERA: il contesto si LEGGE dalla stima
#
# Il trigger 033 garantisce COERENZA: impedisce che un lead di un'agenzia si
# leghi a una stima di un'altra. Non garantisce IDEMPOTENZA: un retry che ci
# finisse contro riceverebbe un'eccezione invece di completare.
#
# La regola, dal `COMMIT` della stima in poi: `stime.agency_id` e' la fonte di
# verita' immutabile per quel processo. Chi lo riprende la LEGGE. Non rifa' il
# routing, non guarda gli alias di oggi, e non sceglie.
#
# Questi test provano le due meta' del caso: con il legame gia' creato, e -
# quella che conta - senza.
# ---------------------------------------------------------------------------

class StimaCursor:
    """Espone la riga `stime` e registra OGNI query ricevuta.

    Registrare serve quanto rispondere: la prova che non si rifa' il routing e'
    che alias, territori e assegnazioni non vengono nemmeno nominati.
    """

    def __init__(self, *, righe):
        self.righe = dict(righe)
        self.eseguite: list[str] = []
        self._riga = None

    def execute(self, sql, params=None):
        compatta = " ".join(str(sql).split())
        self.eseguite.append(compatta)
        if "FROM stime WHERE id" not in compatta:
            raise AssertionError(
                f"la lettura della decisione persistita ha interrogato: {compatta}"
            )
        (stima_id,) = params
        valore = self.righe.get(stima_id)
        self._riga = None if valore is None else {"agency_id": valore}

    def fetchone(self):
        return self._riga


def test_j5_a_retry_after_the_network_changed_reuses_the_persisted_agency(
    esegui_bridge,
):
    """LO SCENARIO OBBLIGATORIO, con il legame gia' esistente.

    1. l'alias 'Alba Adriatica' porta all'agenzia 7;
    2. la stima nasce e viene incisa con 7;
    3. commit;
    4. la rete cambia: lo stesso alias ora porterebbe all'agenzia 8;
    5. secondo passaggio sullo stesso `stima_id`.

    Atteso: nessun routing verso 8, nessun errore del trigger, `already_linked`,
    e il lead resta dov'e'.
    """
    # 1-2. La rete di partenza decide 7, ed e' 7 che viene inciso.
    assert _decidi(_rete(assegnata_a=7)).agency_id == 7
    bridge = BridgeCursor(stime_agency_id=7)
    primo = esegui_bridge(bridge, agency_id=7, stima_id=901)
    assert bridge.inseriti["leads"][0]["agency_id"] == 7

    # 4. La rete cambia davvero: chi rifacesse il routing otterrebbe 8.
    rete_nuova = _rete(assegnata_a=8)
    rete_nuova.agenzie.append({"id": 8, "slug": "agenzia-due", "status": "active"})
    assert _decidi(rete_nuova).agency_id == 8

    # 5. Il contesto del secondo passaggio viene dalla STIMA, non dalla rete.
    from network_routing.service import system_context_for_persisted_public_stima

    stima = StimaCursor(righe={901: 7})
    ctx = system_context_for_persisted_public_stima(stima, stima_id=901)
    assert ctx.agency_id == 7
    assert ctx.origin == "public_stima"

    secondo = esegui_bridge(bridge, agency_id=ctx.agency_id, stima_id=901)
    assert secondo["status"] == "already_linked"
    assert secondo["lead_id"] == primo["lead_id"]
    assert len(bridge.inseriti["lead_stime"]) == 1
    assert bridge.stime_agency_id == 7


def test_j6_a_retry_completes_under_the_persisted_agency_with_no_link_yet(
    esegui_bridge,
):
    """LO SCENARIO CHE CONTA: il primo bridge muore PRIMA di creare il legame.

    E' il caso importante perche' dimostra che l'idempotenza non dipende
    dall'esistenza preventiva del legame - che e' quel che il ritorno
    `already_linked` sembrerebbe suggerire. Qui non c'e' nessun legame da
    trovare: il retry deve comunque completare SOTTO L'AGENZIA PERSISTITA, e
    non sotto quella che la rete indicherebbe adesso.
    """
    from network_routing.service import system_context_for_persisted_public_stima

    # Primo passaggio: cade inserendo il lead, prima del legame.
    bridge = BridgeCursor(stime_agency_id=7, fallisce_su="insert into leads")
    with pytest.raises(RuntimeError):
        esegui_bridge(bridge, agency_id=7, stima_id=902)
    assert bridge.inseriti["lead_stime"] == []

    # La rete cambia verso 8.
    rete_nuova = _rete(assegnata_a=8)
    rete_nuova.agenzie.append({"id": 8, "slug": "agenzia-due", "status": "active"})
    assert _decidi(rete_nuova).agency_id == 8

    # Il retry legge la stima, non la rete, e completa.
    stima = StimaCursor(righe={902: 7})
    ctx = system_context_for_persisted_public_stima(stima, stima_id=902)
    assert ctx.agency_id == 7

    esito = esegui_bridge(bridge, agency_id=ctx.agency_id, stima_id=902)
    assert esito["status"] == "linked"
    assert bridge.inseriti["leads"][-1]["agency_id"] == 7
    assert len(bridge.inseriti["lead_stime"]) == 1
    assert bridge.stime_agency_id == 7


def test_j7_the_persisted_lookup_never_touches_alias_or_territory():
    """Non e' una seconda decisione di routing: e' una lettura, e si vede.

    Lo `StimaCursor` solleva su qualunque query che non sia la lettura della
    riga `stime`, quindi un ritorno al routing - o una consultazione degli
    alias "solo per controllare" - fallirebbe qui.
    """
    from network_routing.service import system_context_for_persisted_public_stima

    stima = StimaCursor(righe={903: 42})
    ctx = system_context_for_persisted_public_stima(stima, stima_id=903)
    assert ctx.agency_id == 42
    assert len(stima.eseguite) == 1
    (query,) = stima.eseguite
    for vietato in ("network_territory_aliases", "network_territories",
                    "agency_territory_assignments", "agencies"):
        assert vietato not in query, query


def test_j8_a_stima_without_a_persisted_agency_does_not_fall_back():
    """Nessuna riga, nessuna agenzia: si solleva, non si ripiega.

    Ripiegare su STIMA360 qui significherebbe scegliere un'agenzia OGGI per una
    stima che non dice a chi appartiene - cioe' la seconda decisione che questa
    correzione esiste per eliminare.
    """
    from network_routing.service import (
        PersistedAgencyMissing,
        RoutingUnavailable,
        system_context_for_persisted_public_stima,
    )

    stima = StimaCursor(righe={})
    with pytest.raises(PersistedAgencyMissing) as errore:
        system_context_for_persisted_public_stima(stima, stima_id=904)
    assert "904" in str(errore.value)
    # Chi gia' gestisce "nessuna agenzia puo' ricevere" gestisce anche questo.
    assert isinstance(errore.value, RoutingUnavailable)


def test_j9_the_funnel_builds_the_bridge_context_from_the_persisted_row():
    """E il funnel passa SEMPRE di li': una strada sola, non due.

    Se il contesto della decisione arrivasse ancora al bridge, tutto quel che
    c'e' sopra resterebbe vero e inutile - il retry userebbe comunque la strada
    sbagliata, perche' nessuno si ricorderebbe di usare l'altra.
    """
    sorgente = (ROOT / "main.py").read_text(encoding="utf-8")
    handler = sorgente[sorgente.index('@app.post("/api/salva_stima")'):]
    handler = handler[: handler.index("safe_run_followup")]

    commit = handler.index("conn.commit()")
    lettura = handler.index("_persisted_public_stima_system_context(")
    chiamata = handler.index("bridge_public_stima")
    assert commit < lettura < chiamata, "la rilettura non sta fra commit e bridge"

    blocco = handler[chiamata:handler.index("\n        )", chiamata)]
    istruzioni = "\n".join(
        r for r in blocco.split("\n") if not r.strip().startswith("#")
    )
    assert "system_ctx=bridge_ctx" in istruzioni, istruzioni
    assert "system_ctx=system_ctx" not in istruzioni, istruzioni
