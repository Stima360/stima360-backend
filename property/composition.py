"""CENSIMENTO-2 - COMPOSIZIONE DEGLI EDIFICI E SCHEDE DELLE UNITA'.

Sul modello del censimento esistente (property/census.py, migration 083 e 089)
e sulle colonne della migration 100. Nessun sistema parallelo: un'unita' e'
sempre una riga `properties` con `building_id` e `record_kind = 'census'`;
gli inserimenti passano dalle stesse funzioni di `POST /census/units`
(`census._prepara_unita`, `census._inserisci_unita`): indirizzo ereditato,
codice IMM, storico dello stato, blocco del duplicato catastale.

Cosa aggiunge:
  * COMPOSIZIONE dichiarata per classe (abitazioni, commerciali, altre,
    pertinenze: property/census_classes.py). «Non so» = NULL, bozza sempre
    salvabile; il totale diventa la somma solo su scelta esplicita;
  * DUE INDICATORI SEPARATI (D4): quante unita' ci sono rispetto a quelle
    dichiarate, e quanto sono complete le schede. Lo stato di completamento
    dell'edificio e' CALCOLATO, mai memorizzato;
  * GENERAZIONE delle unita' MANCANTI per classe: anteprima in sola lettura,
    poi conferma in UNA transazione, sotto il lock dell'edificio, con i
    conteggi dell'anteprima ricontrollati (409 UNITS_CHANGED se un collega
    ha aggiunto unita' nel frattempo). Idempotenza senza tabelle nuove: la
    chiave del lotto genera, per ogni unita', una chiave UUID5 deterministica
    e l'impronta del lotto; ripetere lo stesso lotto restituisce le stesse
    righe. Sempre schede di censimento in bozza; pertinenze «da collegare»;
    nessuna categoria dedotta; mai «verificata» in blocco; numerazione
    interna solo se chiesta e marcata provvisoria;
  * ANNULLAMENTO del lotto con la regola di «Annulla» (undo-create): solo
    le schede ancora intatte e senza collegamenti;
  * MODIFICA DI GRUPPO su un elenco chiuso di campi, atomica, con lo storico
    sulla scheda come interazione di sistema.

Permessi (D9): come il censimento (ogni operatore dell'agenzia sulle schede
`census`); sulle schede commerciali (`crm`) la regola di gestione di sempre
(`lifecycle.require_manage`). Ogni identificativo e' cercato nell'agenzia
dello scope: altrove = inesistente (404).

Ordine dei lock (uguale a census.update_building): edificio, poi unita' in
ordine di id.
"""
from __future__ import annotations

import uuid

from psycopg2 import errors as pg_errors
from psycopg2.extras import Json

from core.database import core_cursor
from core.exceptions import NotFoundError
from core import property_trash as _property_trash

from . import census, repository
from . import census_classes as classi
from .catalog import generated_title

COMPOSITION_NOT_INSTALLED_MESSAGE = ("La composizione degli edifici non e' ancora disponibile su questo database "
                                     "(migration 100 non applicata).")
COMPOSITION_REQUIRED = "Dichiara prima la composizione dell'edificio (anche come bozza non basta: servono le quattro classi)"
UNITS_CHANGED = "Nel frattempo le unita' dell'edificio sono cambiate: rifai l'anteprima"
OVER_DECLARED = "Stai creando piu' unita' di quelle dichiarate: conferma esplicitamente"
TOTAL_MISMATCH = "Il totale dichiarato non corrisponde alla somma delle classi: aggiorna il totale o correggi le classi"
VERIFIED_NOT_IN_BULK = "'Verificata' si indica dalla scheda della singola unita', con la fonte consultata"
VERIFIED_NEEDS_EVIDENCE = ("'Verificata' richiede una fonte catastale consultata e registrata: identita' "
                           "catastale dell'unita' (comune catastale, foglio, particella, subalterno; sezione solo se "
                           "prevista) e tipo e data del documento")
SOURCE_WITHOUT_VALUE = "Una provenienza senza il dato non si salva"
# Fase A (A2/A3): «Dati catastali e superficie» della singola scheda
VERIFIED_IN_BULK = ("Alcune schede selezionate hanno il dato verificato con una fonte documentata: la modifica di "
                    "gruppo non lo cambia. Modificale una per una dal modulo «Dati catastali e superficie»")
VERIFIED_DATA_LOCKED = ("Questo dato e' verificato con una fonte documentata: modificalo dal modulo «Dati catastali e "
                        "superficie» della scheda, che chiede di riconfermare la verifica o la toglie in modo esplicito")
EVIDENCE_WITHOUT_VERIFIED = "La fonte consultata si registra insieme a una provenienza «Verificata»"
INTERNAL_NUMBER_REQUIRED = "Indica l'interno prima di confermarlo"
UNIT_ARCHIVED = "La scheda e' archiviata: riattivala prima di modificarne i dati"
UNIT_IN_TRASH = "La scheda e' nel Cestino: ripristinala prima di modificarne i dati"
STALE_DATA = ("Nel frattempo la scheda e' stata modificata (da te o da un collega): rileggi i dati salvati e "
              "decidi se riapplicare le tue modifiche")
INTERNAL_SIMILAR = "Nella stessa scala e allo stesso piano c'e' gia' questo interno: controlla o salva comunque"

_BATCH_KEY = "census_batch"
_FONTI = (("surface_sqm", "surface_source"), ("cadastral_category", "cadastral_category_source"))


class CompositionNotInstalled(census.CensusNotInstalled):
    code = "COMPOSITION_NOT_INSTALLED"


#: Gli errori del database che il router traduce per le colonne e i vincoli
#: della migration 100 (PATCH generica dell'immobile e dell'edificio).
DB_ERRORS = (pg_errors.CheckViolation, pg_errors.UndefinedColumn)
_VINCOLI_100 = {
    "properties_verified_source_evidence_chk": (400, "VERIFIED_NEEDS_CADASTRAL_EVIDENCE", None),
    "properties_surface_source_chk": (400, "SOURCE_WITHOUT_VALUE", None),
    "properties_category_source_chk": (400, "SOURCE_WITHOUT_VALUE", None),
    "buildings_composition_total_chk": (409, "COMPOSITION_TOTAL_MISMATCH", None),
    "buildings_composition_nonneg_chk": (400, "VALIDATION_ERROR", None),
    "properties_cadastral_evidence_type_chk": (400, "VALIDATION_ERROR", None),
}
_COLONNE_100 = ("surface_source", "cadastral_category_source", "cadastral_evidence_", "units_declared_residential",
                "units_declared_commercial", "units_declared_other", "units_declared_pertinenze")


def translate_db_error(exc):
    """(stato HTTP, codice, messaggio) per un errore che nasce dalla 100, o
    None: ogni altro errore resta quello che era."""
    if isinstance(exc, pg_errors.UndefinedColumn):
        if any(c in str(exc) for c in _COLONNE_100):
            return 503, CompositionNotInstalled.code, COMPOSITION_NOT_INSTALLED_MESSAGE
        return None
    vincolo = getattr(getattr(exc, "diag", None), "constraint_name", None) or ""
    if vincolo not in _VINCOLI_100:
        return None
    http, code, _ = _VINCOLI_100[vincolo]
    testi = {"VERIFIED_NEEDS_CADASTRAL_EVIDENCE": VERIFIED_NEEDS_EVIDENCE, "SOURCE_WITHOUT_VALUE": SOURCE_WITHOUT_VALUE,
             "COMPOSITION_TOTAL_MISMATCH": TOTAL_MISMATCH, "VALIDATION_ERROR": "Valore non valido"}
    return http, code, testi[code]


def _assicura_100(cur) -> None:
    census._assicura_083(cur)
    cur.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'buildings' AND column_name = 'units_declared_pertinenze') "
                "AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'properties' AND column_name = 'cadastral_category_source') AS pronta")
    if not cur.fetchone()["pronta"]:
        raise CompositionNotInstalled(COMPOSITION_NOT_INSTALLED_MESSAGE)


# ---------------------------------------------------------------------------
# letture: presenti per classe, schede, riepilogo
# ---------------------------------------------------------------------------

def _presenti(cur, building_id) -> dict:
    """Le unita' PRESENTI per classe, con la regola del riepilogo di
    EDIFICI-1: attive + archiviate (un'unita' archiviata esiste ancora), mai
    quelle nel Cestino ne' quelle annullate subito dopo la creazione."""
    cur.execute(f"""
        SELECT {classi.class_sql('u')} AS classe, count(*) AS n
          FROM properties u
         WHERE u.building_id = %s AND {_property_trash.live('u')}
           AND NOT ({census._ARCHIVIATA} AND coalesce({census._ANNULLATA}, FALSE))
         GROUP BY 1""", (building_id,))
    esito = {c: 0 for c in classi.CLASSES}
    for r in cur.fetchall():
        esito[r["classe"]] = int(r["n"])
    return esito


def _schede(cur, building_id) -> list[dict]:
    """Le schede ATTIVE dell'edificio con i dati che servono al
    completamento e alla modifica di gruppo."""
    cur.execute(f"""
        SELECT u.id, u.code, u.property_type, u.record_kind, u.commercial_status, u.assigned_agent_id,
               u.floor, u.staircase, u.internal_number,
               u.surface_sqm, u.surface_source, u.cadastral_category, u.cadastral_category_source,
               u.cadastral_municipality_code, u.cadastral_section, u.cadastral_sheet, u.cadastral_parcel,
               u.cadastral_subunit, u.cadastral_evidence_type, u.cadastral_evidence_date, u.cadastral_evidence_identity,
               u.parent_property_id, u.metadata,
               {census._marcata('u')} AS is_pertinenza, to_jsonb(u)->>'pertinenza_kind' AS pertinenza_kind,
               {classi.class_sql('u')} AS unit_class,
               g.id AS parent_live_id, g.code AS parent_code, g.building_id AS parent_building_id,
               (SELECT count(*) FROM properties f WHERE f.parent_property_id = u.id AND {_property_trash.live('f')}
                   AND NOT (f.archived_at IS NOT NULL OR f.commercial_status = 'archived')) AS pertinenze_count
          FROM properties u
          LEFT JOIN properties g ON g.id = u.parent_property_id AND {_property_trash.live('g')}
         WHERE u.building_id = %s AND {_property_trash.live('u')} AND NOT {census._ARCHIVIATA}
         ORDER BY u.id""", (building_id,))
    righe = []
    for r in cur.fetchall():
        riga = dict(r)
        meta = riga.pop("metadata", None) or {}
        lotto = meta.get(_BATCH_KEY) if isinstance(meta, dict) else None
        riga["batch_key"] = lotto.get("key") if isinstance(lotto, dict) else None
        # A2: provvisorio finche' non confermato; senza interno non c'e' nulla di provvisorio da mostrare
        riga["provisional_numbering"] = bool(isinstance(lotto, dict) and lotto.get("provisional_numbering")
                                             and riga.get("internal_number"))
        riga["missing"] = classi.missing_data(riga)
        riga["cadastral_source"] = classi.has_cadastral_source(riga)
        # A1: indicatore SEPARATO dalla completezza: verificata / parziale / da verificare (o riconfermare)
        riga["verification"] = classi.verification_state(riga)
        riga["verification_reconfirm"] = classi.needs_reconfirm(riga)
        riga.pop("cadastral_evidence_identity", None)
        riga["census_class_explicit"] = bool(isinstance(meta, dict) and meta.get(classi.EXPLICIT_CLASS_KEY))
        riga["pertinenze_count"] = int(riga["pertinenze_count"] or 0)
        riga["parent"] = None if riga.get("parent_live_id") is None else {
            "id": riga["parent_live_id"], "code": riga.get("parent_code"),
            "same_building": riga.get("parent_building_id") == building_id}
        for k in ("parent_live_id", "parent_code", "parent_building_id", "cadastral_municipality_code", "cadastral_section",
                  "cadastral_sheet", "cadastral_parcel"):
            riga.pop(k, None)
        righe.append(riga)
    return righe


def _composizione(edificio: dict) -> dict:
    valori = {c: edificio.get(col) for c, col in classi.DECLARED_COLUMNS.items()}
    completa = classi.composition_complete(edificio)
    somma = sum(valori.values()) if completa else None
    totale = edificio.get("units_declared")
    return {**valori, "complete": completa, "sum": somma, "units_declared": totale,
            "units_declared_source": edificio.get("units_declared_source"),
            "total_matches": None if somma is None or totale is None else somma == totale}


def _per_classe(edificio: dict, presenti: dict) -> dict:
    esito = {}
    for c, col in classi.DECLARED_COLUMNS.items():
        dichiarate = edificio.get(col)
        esito[c] = {"declared": dichiarate, "present": presenti[c],
                    "to_create": None if dichiarate is None else max(dichiarate - presenti[c], 0),
                    "over_declared": 0 if dichiarate is None else max(presenti[c] - dichiarate, 0)}
    return esito


def _riepilogo(cur, edificio: dict) -> dict:
    presenti = _presenti(cur, edificio["id"])
    per_classe = _per_classe(edificio, presenti)
    schede = _schede(cur, edificio["id"])
    composizione = _composizione(edificio)
    da_creare = sum(v["to_create"] or 0 for v in per_classe.values()) if composizione["complete"] else None
    incomplete = [s for s in schede if s["missing"]]
    if not composizione["complete"]:
        stato = "draft"
    elif da_creare:
        stato = "composition_declared"
    elif incomplete:
        stato = "units_created"
    else:
        stato = "data_complete"
    return {
        "building_id": edificio["id"],
        "composition": composizione,
        "by_class": per_classe,
        # indicatore 1: quante unita' ci sono rispetto alle dichiarate
        "units_created": {"declared": composizione["sum"], "present": sum(presenti.values()),
                          "to_create": da_creare, "complete": da_creare == 0},
        # indicatore 2: quanto sono complete le schede (separato dal primo)
        "data_completeness": {"units": len(schede), "complete": len(schede) - len(incomplete),
                              "incomplete": len(incomplete), "minimum_data": list(classi.MINIMUM_DATA)},
        # indicatore 3 (A1): quanto sono attendibili le schede (separato dai primi due)
        "verification": {"units": len(schede),
                         **{s: sum(1 for x in schede if x["verification"] == s) for s in classi.VERIFICATION_STATES},
                         "to_reconfirm": sum(1 for x in schede if x["verification_reconfirm"])},
        "completion": stato,
        "units": schede,
        "catalog": classi.catalog(),
    }


def get_composition(ctx, building_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _assicura_100(cur)
        edificio = census._edificio(cur, agency_id, building_id, trashed_ok=True)
        return _riepilogo(cur, edificio)


# ---------------------------------------------------------------------------
# composizione dichiarata
# ---------------------------------------------------------------------------

def set_composition(ctx, building_id: int, body) -> dict:
    """Salva la composizione (anche parziale: bozza). Con `set_total` il
    totale dichiarato diventa la somma; senza, un totale noto diverso dalla
    somma delle quattro classi note e' un 409 leggibile (nessuna correzione
    silenziosa)."""
    agency_id = ctx.require_agency()
    data = body.model_dump()

    def operazione():
        with core_cursor(commit=True) as (_, cur):
            _assicura_100(cur)
            edificio = census._edificio(cur, agency_id, building_id, lock=True)
            valori = {col: data[c] for c, col in classi.DECLARED_COLUMNS.items()}
            completa = all(v is not None for v in valori.values())
            if data["set_total"]:
                valori["units_declared"] = sum(data[c] for c in classi.CLASSES)
                if data.get("units_declared_source") is not None:
                    valori["units_declared_source"] = data["units_declared_source"]
            elif completa and edificio.get("units_declared") is not None \
                    and edificio["units_declared"] != sum(data[c] for c in classi.CLASSES):
                raise census.CensusConflict(TOTAL_MISMATCH, "COMPOSITION_TOTAL_MISMATCH",
                                            units_declared=edificio["units_declared"],
                                            sum=sum(data[c] for c in classi.CLASSES))
            cur.execute(f"UPDATE buildings SET {', '.join(f'{k} = %s' for k in valori)}, updated_at = NOW() "
                        "WHERE id = %s AND agency_id = %s RETURNING *",
                        list(valori.values()) + [building_id, agency_id])
            return _riepilogo(cur, repository.row(cur.fetchone()))

    return census._esegui(operazione)


# ---------------------------------------------------------------------------
# generazione delle unita' mancanti
# ---------------------------------------------------------------------------

def _chiave_unita(chiave_lotto, gruppo: int, indice: int) -> uuid.UUID:
    return uuid.uuid5(uuid.UUID(str(chiave_lotto)), f"{gruppo}:{indice}")


def _impronta_lotto(data: dict) -> str:
    pulito = {k: v for k, v in data.items()
              if k not in ("dry_run", "expected_existing", "confirm_over_declared", "confirm_similar")}
    return census._fingerprint(pulito)


def _unita_del_lotto(cur, agency_id, building_id, chiave_lotto) -> list[dict]:
    cur.execute(f"SELECT u.id, u.code, u.property_type, u.floor, u.staircase, u.internal_number, u.record_kind, "
                f"u.commercial_status, u.archived_at, u.client_request_fingerprint, u.metadata, "
                f"{classi.class_sql('u')} AS unit_class "
                f"FROM properties u WHERE u.agency_id = %s AND u.building_id = %s AND {_property_trash.live('u')} "
                f"AND u.metadata->'{_BATCH_KEY}'->>'key' = %s ORDER BY u.id",
                (agency_id, building_id, str(chiave_lotto)))
    return [dict(r) for r in cur.fetchall()]


def _replica(righe: list[dict], impronta: str) -> dict | None:
    if not righe:
        return None
    if any(r.get("client_request_fingerprint") != impronta for r in righe):
        raise census.CensusConflict(census.IDEMPOTENCY_REUSED, "IDEMPOTENCY_KEY_REUSED")
    return {"replica": True, "dry_run": False, "created": len(righe),
            "units": [{k: r.get(k) for k in ("id", "code", "unit_class", "property_type", "floor", "staircase",
                                             "internal_number")} for r in righe]}


def _piano(edificio, presenti, gruppi) -> dict:
    richieste = {c: 0 for c in classi.CLASSES}
    for g in gruppi:
        richieste[g["unit_class"]] += g["count"]
    esito = {}
    for c, col in classi.DECLARED_COLUMNS.items():
        mancanti = max(edificio[col] - presenti[c], 0)
        esito[c] = {"declared": edificio[col], "present": presenti[c], "missing": mancanti,
                    "requested": richieste[c], "over_declared": max(richieste[c] - mancanti, 0)}
    return esito


def _interni(gruppi) -> list[tuple]:
    """Le posizioni (scala, piano, interno) della numerazione provvisoria."""
    posizioni = []
    for g in gruppi:
        if g.get("numbering_start") is None:
            continue
        for i in range(g["count"]):
            posizioni.append((g.get("staircase"), g.get("floor"), str(g["numbering_start"] + i)))
    return posizioni


def _norm_pos(v):
    return (v or "").strip().upper()


def _conflitti_numerazione(cur, building_id, gruppi) -> list[dict]:
    """Avviso (mai blocco silenzioso): un interno provvisorio che coincide con
    un'unita' esistente nella stessa posizione, o con un altro gruppo del
    lotto."""
    posizioni = _interni(gruppi)
    if not posizioni:
        return []
    conflitti, viste = [], set()
    for p in posizioni:
        chiave = tuple(_norm_pos(x) for x in p)
        if chiave in viste:
            conflitti.append({"staircase": p[0], "floor": p[1], "internal_number": p[2], "reason": "batch"})
        viste.add(chiave)
    cur.execute(f"SELECT id, code, staircase, floor, internal_number FROM properties u WHERE u.building_id = %s "
                f"AND u.archived_at IS NULL AND {_property_trash.live('u')} AND u.internal_number IS NOT NULL",
                (building_id,))
    for r in cur.fetchall():
        if (_norm_pos(r["staircase"]), _norm_pos(r["floor"]), _norm_pos(r["internal_number"])) in viste:
            conflitti.append({**dict(r), "reason": "position"})
    return conflitti


def _dati_unita(building_id, chiave_lotto, g_idx, g, i) -> dict:
    meta = {_BATCH_KEY: {"key": str(chiave_lotto), "group": g_idx, "seq": i,
                         "provisional_numbering": g.get("numbering_start") is not None}}
    if g["property_type"] == classi.RUSTIC and g["unit_class"] == "residential":
        meta[classi.EXPLICIT_CLASS_KEY] = "residential"          # D1: classificazione esplicita
    dati = {"building_id": building_id, "property_type": g["property_type"], "metadata": meta}
    for campo in ("floor", "staircase", "surface_sqm", "surface_source", "cadastral_category",
                  "cadastral_category_source"):
        if g.get(campo) is not None:
            dati[campo] = g[campo]
    if g.get("numbering_start") is not None:
        dati["internal_number"] = str(g["numbering_start"] + i)
    if g["unit_class"] == "pertinenze":
        dati["is_pertinenza"] = True                              # «da collegare»: nessun genitore inventato
        if g.get("pertinenza_kind") is not None:
            dati["pertinenza_kind"] = g["pertinenza_kind"]
    return dati


def generate_units(ctx, building_id: int, body) -> dict:
    agency_id = ctx.require_agency()
    data = body.model_dump()
    chiave = data["client_request_id"]
    impronta = _impronta_lotto(data)
    gruppi = data["groups"]
    for g in gruppi:
        if g.get("cadastral_category") is not None:
            census._check_cadastral(g)                    # catalogo della categoria, prima del database

    def anteprima_o_conferma(cur, *, scrivi):
        edificio = census._edificio(cur, agency_id, building_id, lock=scrivi)
        replica = _replica(_unita_del_lotto(cur, agency_id, building_id, chiave), impronta)
        if replica is not None:
            return replica
        if not classi.composition_complete(edificio):
            raise census.CensusConflict(COMPOSITION_REQUIRED, "COMPOSITION_REQUIRED",
                                        composition=_composizione(edificio))
        presenti = _presenti(cur, building_id)
        piano = _piano(edificio, presenti, gruppi)
        conflitti = _conflitti_numerazione(cur, building_id, gruppi)
        sintesi = {"existing": presenti, "plan": piano, "numbering_conflicts": conflitti,
                   "total": sum(g["count"] for g in gruppi)}
        if not scrivi:
            return {"replica": False, "dry_run": True, **sintesi}
        if {c: int(data["expected_existing"][c]) for c in classi.CLASSES} != presenti:
            raise census.CensusConflict(UNITS_CHANGED, "UNITS_CHANGED", existing=presenti)
        if any(v["over_declared"] for v in piano.values()) and not data["confirm_over_declared"]:
            raise census.CensusConflict(OVER_DECLARED, "OVER_DECLARED", plan=piano)
        if conflitti and not data["confirm_similar"]:
            raise census.CensusConflict("Alcuni interni provvisori coincidono con unita' esistenti",
                                        "SIMILAR_FOUND", similar=conflitti)
        create = []
        for g_idx, g in enumerate(gruppi):
            for i in range(g["count"]):
                dati = _dati_unita(building_id, chiave, g_idx, g, i)
                census._prepara_unita(cur, agency_id, dati)
                riga = census._inserisci_unita(ctx, cur, agency_id, dati, chiave=_chiave_unita(chiave, g_idx, i),
                                               impronta=impronta, conferma=True)
                create.append({"id": riga["id"], "code": riga.get("code"), "unit_class": g["unit_class"],
                               "property_type": riga["property_type"], "floor": riga.get("floor"),
                               "staircase": riga.get("staircase"), "internal_number": riga.get("internal_number")})
        return {"replica": False, "dry_run": False, **sintesi, "created": len(create), "units": create,
                "batch_key": str(chiave)}

    if data["dry_run"]:
        def lettura():
            with core_cursor() as (_, cur):
                _assicura_100(cur)
                return anteprima_o_conferma(cur, scrivi=False)
        return census._esegui(lettura)

    def operazione():
        with core_cursor(commit=True) as (_, cur):
            _assicura_100(cur)
            return anteprima_o_conferma(cur, scrivi=True)

    def leggi_replica():
        with core_cursor() as (_, cur):
            return _replica(_unita_del_lotto(cur, agency_id, building_id, chiave), impronta)

    try:
        return census._esegui(operazione)
    except census._ReplicaRace:
        # una richiesta gemella ha scritto le stesse chiavi nel frattempo
        esito = leggi_replica()
        if esito is None:
            raise census.CensusConflict(census.IDEMPOTENCY_REUSED, "IDEMPOTENCY_KEY_REUSED")
        return esito


# ---------------------------------------------------------------------------
# annullamento del lotto
# ---------------------------------------------------------------------------

def _collegate(cur, ids: list[int]) -> dict:
    """Come census._collegamenti, ma per piu' schede insieme (una query per
    tabella collegata, non una per scheda)."""
    cur.execute("""
        SELECT c.conrelid::regclass::text AS tabella, a.attname AS colonna
          FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
         WHERE c.contype = 'f' AND c.confrelid = 'properties'::regclass ORDER BY 1, 2""")
    trovati: dict[int, list[str]] = {}
    for tabella, colonna in [(r["tabella"], r["colonna"]) for r in cur.fetchall()]:
        if tabella in census._STORICI_PROPRI:
            continue
        cur.execute(f"SELECT DISTINCT {colonna} AS id FROM {tabella} WHERE {colonna} = ANY(%s)", (ids,))
        for r in cur.fetchall():
            trovati.setdefault(int(r["id"]), []).append(tabella)
    return trovati


def undo_batch(ctx, building_id: int, batch_key) -> dict:
    """La regola di «Annulla» (census.undo_create) su ogni scheda del lotto,
    in una transazione: si archiviano solo quelle ancora di censimento, mai
    modificate e senza collegamenti; le altre restano e la risposta lo dice."""
    agency_id = ctx.require_agency()
    try:
        chiave = uuid.UUID(str(batch_key))
    except ValueError as exc:
        raise census.CensusInvalid("Chiave del lotto non valida") from exc

    def operazione():
        with core_cursor(commit=True) as (_, cur):
            _assicura_100(cur)
            census._edificio(cur, agency_id, building_id, lock=True)
            cur.execute(f"SELECT * FROM properties u WHERE u.agency_id = %s AND u.building_id = %s "
                        f"AND {_property_trash.live('u')} AND u.metadata->'{_BATCH_KEY}'->>'key' = %s "
                        "ORDER BY u.id FOR UPDATE", (agency_id, building_id, str(chiave)))
            righe = [repository.row(r) for r in cur.fetchall()]
            if not righe:
                raise NotFoundError(f"batch {chiave} not found")
            vive = [r for r in righe if r.get("archived_at") is None]
            collegate = _collegate(cur, [r["id"] for r in vive]) if vive else {}
            annullate, rimaste = [], []
            for r in vive:
                if r["record_kind"] != "census" or r["updated_at"] != r["created_at"]:
                    rimaste.append({"id": r["id"], "code": r.get("code"), "reason": "modified"})
                    continue
                if r["id"] in collegate:
                    rimaste.append({"id": r["id"], "code": r.get("code"), "reason": "linked",
                                    "linked": collegate[r["id"]]})
                    continue
                cur.execute("UPDATE properties SET commercial_status = 'archived', archived_at = NOW(), "
                            "updated_at = NOW() WHERE id = %s", (r["id"],))
                cur.execute("INSERT INTO property_status_history(property_id,field_name,old_value,new_value,note) "
                            "VALUES(%s,'commercial_status','draft','archived','undo-create')", (r["id"],))
                annullate.append(r["id"])
            return {"batch_key": str(chiave), "undone": annullate, "not_undone": rimaste,
                    "already_archived": [r["id"] for r in righe if r.get("archived_at") is not None]}

    return census._esegui(operazione)


# ---------------------------------------------------------------------------
# modifica di gruppo
# ---------------------------------------------------------------------------

_ETICHETTE = {"surface_sqm": "superficie", "surface_source": "provenienza della superficie",
              "cadastral_category": "categoria catastale", "cadastral_category_source": "provenienza della categoria",
              "floor": "piano", "staircase": "scala", "census_class": "classe"}


def bulk_update(ctx, building_id: int, body) -> dict:
    agency_id = ctx.require_agency()
    ids = sorted(set(body.property_ids))
    cambi = body.changes.model_dump(exclude_unset=True)
    if "cadastral_category" in cambi and cambi["cadastral_category"] is not None:
        census._check_cadastral(cambi)
    if "verified" in (cambi.get("surface_source"), cambi.get("cadastral_category_source")):
        raise census.CensusInvalid(VERIFIED_NOT_IN_BULK, "VERIFIED_NOT_IN_BULK")
    valori = {k: v for k, v in cambi.items() if k in ("surface_sqm", "cadastral_category", "floor", "staircase")}
    fonti = {k: v for k, v in cambi.items() if k in ("surface_source", "cadastral_category_source")}
    from . import lifecycle as _lifecycle

    def operazione():
        with core_cursor(commit=True) as (_, cur):
            _assicura_100(cur)
            census._edificio(cur, agency_id, building_id, share=True)
            cur.execute("SELECT * FROM properties WHERE id = ANY(%s) AND agency_id = %s AND building_id = %s "
                        f"AND {_property_trash.live('properties')} ORDER BY id FOR UPDATE",
                        (ids, agency_id, building_id))
            righe = [repository.row(r) for r in cur.fetchall()]
            if len(righe) != len(ids):
                raise NotFoundError("Unita' non trovate in questo edificio")
            for r in righe:
                if r.get("archived_at") is not None or r.get("commercial_status") == "archived":
                    raise census.CensusConflict(f"{census._etichetta(r)} e' archiviata: riattivala prima",
                                                "UNIT_ARCHIVED", property_id=r["id"])
                if r["record_kind"] != "census":
                    _lifecycle.require_manage(ctx, r)
                if "census_class" in cambi and r["property_type"] != classi.RUSTIC:
                    raise census.CensusInvalid("La classe si sceglie solo per i rustici", "CLASS_NOT_APPLICABLE",
                                               property_id=r["id"])
            # Fase A (A3): la modifica di gruppo non invalida in silenzio una verifica
            # documentata: una scheda con il dato «verificato» si modifica da sola,
            # dal modulo «Dati catastali e superficie» (che lo registra nello storico)
            verificate = sorted({r["id"] for r in righe for valore, fonte in _FONTI
                                 if r.get(fonte) == "verified" and (fonte in fonti or (
                                     valore in valori and not _uguale(valore, r.get(valore), valori[valore])))})
            if verificate:
                raise census.CensusConflict(VERIFIED_IN_BULK, "VERIFIED_DATA_LOCKED", property_ids=verificate)
            for r in righe:
                dopo = {**r, **valori}
                for valore, fonte in _FONTI:
                    if fonti.get(fonte) is not None and dopo.get(valore) is None:
                        raise census.CensusInvalid(SOURCE_WITHOUT_VALUE, "SOURCE_WITHOUT_VALUE", property_id=r["id"])

            aggiornate = []
            for r in righe:
                prima = dict(r)
                insieme = dict(valori)
                if "census_class" in cambi:
                    meta = dict(r.get("metadata") or {})
                    if cambi["census_class"] is None:
                        meta.pop(classi.EXPLICIT_CLASS_KEY, None)
                    else:
                        meta[classi.EXPLICIT_CLASS_KEY] = cambi["census_class"]
                    insieme["metadata"] = Json(meta)
                if any(k in valori for k in ("floor", "staircase")) and r.get("title") == generated_title(r):
                    insieme["title"] = generated_title({**r, **valori})
                if insieme:
                    # 1) i valori: se un valore cambia senza la sua provenienza, il
                    #    trigger della 100 la riporta a «non indicata»
                    cur.execute(f"UPDATE properties SET {', '.join(f'{k} = %s' for k in insieme)}, updated_at = NOW() "
                                "WHERE id = %s", list(insieme.values()) + [r["id"]])
                if fonti:
                    # 2) le provenienze indicate, sul valore ormai scritto
                    cur.execute(f"UPDATE properties SET {', '.join(f'{k} = %s' for k in fonti)}, updated_at = NOW() "
                                "WHERE id = %s", list(fonti.values()) + [r["id"]])
                cur.execute("SELECT id, code, property_type, floor, staircase, surface_sqm, surface_source, "
                            "cadastral_category, cadastral_category_source, metadata FROM properties WHERE id = %s",
                            (r["id"],))
                nuova = repository.row(cur.fetchone())
                cambiati = sorted(k for k in cambi if k != "census_class" and prima.get(k) != nuova.get(k))
                if "census_class" in cambi and (prima.get("metadata") or {}).get(classi.EXPLICIT_CLASS_KEY) \
                        != (nuova.get("metadata") or {}).get(classi.EXPLICIT_CLASS_KEY):
                    cambiati.append("census_class")
                if cambiati:
                    census._attivita_sistema(ctx, cur, r["id"], "Modifica di gruppo: " +
                                             ", ".join(_ETICHETTE[k] for k in cambiati),
                                             bulk=True, fields=cambiati, building_id=building_id)
                nuova.pop("metadata", None)
                aggiornate.append({**nuova, "changed": cambiati})
            return {"building_id": building_id, "updated": sum(1 for a in aggiornate if a["changed"]),
                    "units": aggiornate}

    return census._esegui(operazione)


# ---------------------------------------------------------------------------
# Fase A (A2/A3): «Dati catastali e superficie» della singola scheda
# ---------------------------------------------------------------------------
#
# PATCH /properties/{id}/census-data: identita' catastale, superficie,
# categoria, provenienze, fonte della verifica, interno e sua conferma in UNA
# transazione, con UNA riga di storico (`activities` di sistema, come la
# modifica di gruppo: agenzia, autore e data li scrive l'audit esistente;
# campo, valore precedente e successivo in `metadata.changes`). Ogni rifiuto
# avviene prima dell'UPDATE; un errore dopo (vincolo, indice unico) annulla
# tutto, storico compreso. Mai lo stato commerciale.

_IDENTITA = ("cadastral_municipality_code", "cadastral_section", "cadastral_sheet", "cadastral_parcel",
             "cadastral_subunit")
_EVIDENZA = ("cadastral_evidence_type", "cadastral_evidence_date", "cadastral_evidence_ref")
_CAMPI_DATI = (*_IDENTITA, "surface_sqm", "surface_source", "cadastral_category", "cadastral_category_source",
               *_EVIDENZA, "internal_number")
_ETICHETTE_DATI = {"cadastral_municipality_code": "comune catastale", "cadastral_section": "sezione",
                   "cadastral_sheet": "foglio", "cadastral_parcel": "particella", "cadastral_subunit": "subalterno",
                   "surface_sqm": "superficie", "surface_source": "provenienza della superficie",
                   "cadastral_category": "categoria catastale", "cadastral_category_source": "provenienza della categoria",
                   "cadastral_evidence_type": "fonte consultata", "cadastral_evidence_date": "data della fonte",
                   "cadastral_evidence_ref": "riferimento della fonte", "internal_number": "interno",
                   "internal_number_status": "conferma dell'interno"}
#: i campi che una verifica documentata protegge, per provenienza: il valore e
#: l'identita' catastale a cui la fonte e' legata (vincolo e trigger della 100)
_PROTETTI_DA = {"surface_source": ("surface_sqm", *_IDENTITA),
                "cadastral_category_source": ("cadastral_category", *_IDENTITA)}


def _uguale(campo, a, b) -> bool:
    """Lo stesso valore come lo salva il database (normalizzazione della 083)."""
    if campo in _IDENTITA:
        return census._norm(a) == census._norm(b)
    if campo in ("cadastral_category", "internal_number", "cadastral_evidence_ref", "cadastral_evidence_type"):
        a = (str(a).strip() or None) if a is not None else None
        b = (str(b).strip() or None) if b is not None else None
        return (a.upper() if a and campo == "cadastral_category" else a) == \
            (b.upper() if b and campo == "cadastral_category" else b)
    return a == b                                        # Decimal('80') == Decimal('80.00'); date == date


def _per_storico(valore):
    if valore is None or isinstance(valore, (bool, int, str)):
        return valore
    return str(valore) if not hasattr(valore, "isoformat") else valore.isoformat()


def _lotto(riga: dict) -> dict:
    meta = riga.get("metadata")
    lotto = meta.get(_BATCH_KEY) if isinstance(meta, dict) else None
    return lotto if isinstance(lotto, dict) else {}


def _verificati_toccati(riga: dict, dati: dict) -> list[str]:
    """I dati di `riga` protetti da una provenienza «verificata» che `dati`
    cambierebbe (il trigger della 100 toglierebbe la verifica in silenzio)."""
    toccati = []
    for fonte, campi in _PROTETTI_DA.items():
        if riga.get(fonte) != "verified":
            continue
        toccati += [c for c in campi if c in dati and c not in toccati and not _uguale(c, riga.get(c), dati[c])]
    return toccati


def _interni_simili(cur, riga: dict, interno) -> list[dict]:
    """La stessa regola di posizione della numerazione (scala, piano, interno):
    un avviso superabile, mai un blocco silenzioso. Scale diverse = interni
    diversi."""
    if not riga.get("building_id") or not interno:
        return []
    cur.execute(f"SELECT id, code, staircase, floor, internal_number FROM properties u WHERE u.building_id = %s "
                f"AND u.id <> %s AND {_property_trash.live('u')} AND NOT {census._ARCHIVIATA} "
                "AND coalesce(upper(btrim(u.staircase)), '') = coalesce(upper(btrim(%s)), '') "
                "AND coalesce(upper(btrim(u.floor)), '') = coalesce(upper(btrim(%s)), '') "
                "AND upper(btrim(u.internal_number)) = upper(btrim(%s)) ORDER BY u.id LIMIT 10",
                (riga["building_id"], riga["id"], riga.get("staircase"), riga.get("floor"), interno))
    return [dict(r) for r in cur.fetchall()]


def _normalizza_dati(dati: dict) -> dict:
    for k in ("cadastral_municipality_code", "cadastral_sheet", "cadastral_parcel", "cadastral_subunit",
              "cadastral_category", "cadastral_evidence_ref", "internal_number"):
        if isinstance(dati.get(k), str):
            dati[k] = dati[k].strip() or None
    if isinstance(dati.get("cadastral_section"), str):
        dati["cadastral_section"] = dati["cadastral_section"].strip()          # '' = «nessuna», resta ''
    return dati


def update_unit_data(ctx, property_id: int, body) -> dict:
    agency_id = ctx.require_agency()
    dati = _normalizza_dati(body.model_dump(exclude_unset=True))
    conferma = bool(dati.pop("confirm_internal_number", False))
    simili_ok = bool(dati.pop("confirm_similar", False))
    atteso = dati.pop("expected_updated_at", None)
    census._check_cadastral(dati)                       # catalogo della categoria, formato del Belfiore
    verificate = [f for f in _PROTETTI_DA if dati.get(f) == "verified"]
    if any(k in dati for k in _EVIDENZA) and not verificate:
        raise census.CensusInvalid(EVIDENCE_WITHOUT_VERIFIED, "EVIDENCE_WITHOUT_VERIFIED")
    if verificate and (dati.get("cadastral_evidence_type") is None or dati.get("cadastral_evidence_date") is None):
        raise census.CensusInvalid(VERIFIED_NEEDS_EVIDENCE, "VERIFIED_NEEDS_CADASTRAL_EVIDENCE")
    from core.exceptions import PropertyInTrash

    def operazione():
        with core_cursor(commit=True) as (_, cur):
            _assicura_100(cur)
            # D2: lo stesso lock della scheda della PATCH generica e di «Applica» del
            # sito, PRIMA della riga: nessuna verifica si intreccia con quelle scritture
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (_chiave_scheda(property_id),))
            cur.execute("SELECT * FROM properties WHERE id = %s AND agency_id = %s FOR UPDATE", (property_id, agency_id))
            r = repository.row(cur.fetchone())
            if r is None:
                raise NotFoundError(f"property {property_id} not found")
            if r.get("deleted_at") is not None:
                raise PropertyInTrash(UNIT_IN_TRASH)
            if r.get("archived_at") is not None or r.get("commercial_status") == "archived":
                raise census.CensusConflict(UNIT_ARCHIVED, "UNIT_ARCHIVED", property_id=r["id"])
            # D1: le autorizzazioni della PATCH generica, che questo modulo usava prima:
            # ogni operatore dell'agenzia, su schede di censimento e commerciali
            if atteso is not None and r.get("updated_at") != atteso:
                raise census.CensusConflict(STALE_DATA, "STALE_DATA", updated_at=r.get("updated_at"))
            dopo = {**r, **dati}
            if verificate and not classi.has_cadastral_source(dopo):
                raise census.CensusInvalid(VERIFIED_NEEDS_EVIDENCE, "VERIFIED_NEEDS_CADASTRAL_EVIDENCE")
            for valore, fonte in _FONTI:
                if dati.get(fonte) is not None and dopo.get(valore) is None:
                    raise census.CensusInvalid(SOURCE_WITHOUT_VALUE, "SOURCE_WITHOUT_VALUE")
            if any(c in dati and not _uguale(c, r.get(c), dati[c]) for c in _IDENTITA):
                doppione = census._duplicato_catastale(cur, agency_id, {c: dopo.get(c) for c in _IDENTITA},
                                                       escluso=property_id)
                if doppione is not None:
                    raise census.CensusConflict(f"Questo subalterno e' gia' censito come {census._etichetta(doppione)}",
                                                "CADASTRAL_DUPLICATE",
                                                existing={"id": doppione["id"], "code": doppione.get("code")})
            provvisorio = bool(_lotto(r).get("provisional_numbering"))
            interno = dopo.get("internal_number")
            if conferma and not interno:
                raise census.CensusInvalid(INTERNAL_NUMBER_REQUIRED, "INTERNAL_NUMBER_REQUIRED")
            conferma_effettiva = conferma and provvisorio
            interno_cambiato = "internal_number" in dati and not _uguale("internal_number", r.get("internal_number"),
                                                                        interno)
            if (interno_cambiato or conferma_effettiva) and not simili_ok:
                simili = _interni_simili(cur, r, interno)
                if simili:
                    raise census.CensusConflict(INTERNAL_SIMILAR, "SIMILAR_FOUND", similar=simili)
            cambia = conferma_effettiva or any(not _uguale(k, r.get(k), v) for k, v in dati.items())
            if not cambia:
                return {**r, "changed": [], **_stato_scheda(r)}
            insieme = {k: v for k, v in dati.items() if k in _CAMPI_DATI}
            assegna = [f"{k} = %s" for k in insieme]
            valori = list(insieme.values())
            if conferma_effettiva:
                # stessa scheda, stesso id e codice: cade solo il «provvisorio»
                assegna.append(f"metadata = jsonb_set(coalesce(metadata, '{{}}'::jsonb), "
                               f"'{{{_BATCH_KEY},provisional_numbering}}', 'false'::jsonb)")
            # UNA istruzione: valori, provenienze e fonte insieme (per il trigger della
            # 100 una provenienza nella stessa SET e' una conferma esplicita)
            cur.execute(f"UPDATE properties SET {', '.join(assegna)}, updated_at = NOW() "
                        "WHERE id = %s AND agency_id = %s RETURNING *", valori + [property_id, agency_id])
            nuova = repository.row(cur.fetchone())
            modifiche = {k: [_per_storico(r.get(k)), _per_storico(nuova.get(k))] for k in _CAMPI_DATI
                         if not _uguale(k, r.get(k), nuova.get(k))}
            if conferma_effettiva:
                modifiche["internal_number_status"] = ["provvisorio", "confermato"]
            if modifiche:
                census._attivita_sistema(ctx, cur, property_id, "Dati catastali e superficie: " +
                                         ", ".join(_ETICHETTE_DATI[k] for k in modifiche),
                                         census_data=True, fields=sorted(modifiche), changes=modifiche,
                                         building_id=r.get("building_id"))
            return {**nuova, "changed": sorted(modifiche), **_stato_scheda(nuova)}

    return census._esegui(operazione)


def _stato_scheda(riga: dict) -> dict:
    return {"verification": classi.verification_state(riga), "verification_reconfirm": classi.needs_reconfirm(riga),
            "provisional_numbering": bool(_lotto(riga).get("provisional_numbering") and riga.get("internal_number"))}


# ---------------------------------------------------------------------------
# Fase A (A3, D2, D3): gli ALTRI percorsi di modifica non invalidano in
# silenzio una verifica documentata e non cambiano l'interno senza storico.
#
# Il service generico (property/service.py) e la sincronizzazione dal sito
# (property/site_sync.py) non si toccano. La loro scrittura apre una
# connessione propria, quindi il controllo non puo' stare nella loro stessa
# transazione: sta davanti, nel router, sotto un LOCK DI SERIALIZZAZIONE
# PER SCHEDA (advisory lock di sessione, `census-data:property:{id}`), preso
# PRIMA di leggere e tenuto fino a DOPO il commit della scrittura. Chi
# scrive una verifica (la rotta census-data, l'unico percorso che puo'
# scriverla) prende lo stesso lock nella SUA transazione (advisory lock di
# transazione) prima di leggere la riga: le due operazioni non possono piu'
# intrecciarsi. La modifica di gruppo controlla le righe che ha gia' bloccato
# FOR UPDATE, nella sua transazione. Ordine dei lock ovunque: advisory, poi
# riga. Il lock di sessione cade comunque alla chiusura della connessione.
# ---------------------------------------------------------------------------

def _chiave_scheda(property_id) -> str:
    return f"census-data:property:{int(property_id)}"


def _sotto_lock(ctx, property_id, controlla, scrivi):
    """Lock della scheda -> `controlla(cur, agency_id)` sullo stato committato
    -> `scrivi()` (il service, con la sua transazione) -> rilascio."""
    agency_id = ctx.require_agency()
    esito, errore = None, None
    with core_cursor() as (conn, cur):
        conn.autocommit = True                          # nessuna transazione aperta durante l'attesa
        cur.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (_chiave_scheda(property_id),))
        try:
            controlla(cur, agency_id)
            try:
                esito = scrivi()
            except Exception as exc:                    # noqa: BLE001 - rilanciato com'e', fuori dal cursore
                errore = exc
        finally:
            cur.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (_chiave_scheda(property_id),))
    if errore is not None:
        raise errore
    return esito


def _con_la_100(cur) -> bool:
    cur.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'properties' AND column_name = 'cadastral_category_source') AS pronta")
    return bool(cur.fetchone()["pronta"])


INTERNAL_NUMBER_TRACKED = ("L'interno si modifica dal modulo «Dati catastali e superficie» della scheda, che lo "
                           "registra nello storico (e lo conferma, se era provvisorio)")
_CAMPI_GUARDATI = ("surface_sqm", "cadastral_category", *_IDENTITA, "internal_number")


def update_property_guarded(ctx, property_id: int, body, scrivi):
    """PATCH generica dell'immobile (D2/D3), con la scrittura `scrivi` del
    service di sempre:
      * un valore protetto da una provenienza «verificata» (superficie;
        categoria e identita' catastale) non cambia da qui: 409
        VERIFIED_DATA_LOCKED;
      * l'interno non cambia da qui (passa dalla rotta tracciata): 409
        INTERNAL_NUMBER_TRACKED;
    nessuna scrittura in entrambi i casi. Rimandare lo stesso valore (il form
    Immobili rimanda tutto) resta possibile; senza la 100 tutto come prima.
    Una PATCH che non tocca questi campi non prende alcun lock."""
    dati = body.model_dump(exclude_unset=True)
    if not any(c in dati for c in _CAMPI_GUARDATI):
        return scrivi()
    prova = _normalizza_dati({k: v for k, v in dati.items() if k in _CAMPI_GUARDATI})
    try:
        census._check_cadastral(prova)
    except census.CensusInvalid:
        return scrivi()                                  # lo rifiuta il service, con il suo messaggio

    def controlla(cur, agency_id):
        if not _con_la_100(cur):
            return
        cur.execute(f"SELECT id, surface_sqm, surface_source, cadastral_category, cadastral_category_source, "
                    f"internal_number, {', '.join(_IDENTITA)} FROM properties p WHERE id = %s AND agency_id = %s "
                    f"AND {_property_trash.live('p')}", (property_id, agency_id))
        riga = cur.fetchone()
        if riga is None:
            return                                       # 404 / Cestino: li decide il service
        riga = dict(riga)
        if "internal_number" in prova and not _uguale("internal_number", riga.get("internal_number"),
                                                      prova["internal_number"]):
            raise census.CensusConflict(INTERNAL_NUMBER_TRACKED, "INTERNAL_NUMBER_TRACKED", fields=["internal_number"])
        toccati = _verificati_toccati(riga, prova)
        if toccati:
            raise census.CensusConflict(VERIFIED_DATA_LOCKED, "VERIFIED_DATA_LOCKED", fields=toccati)

    return _sotto_lock(ctx, property_id, controlla, scrivi)


def resolve_site_conflict_guarded(ctx, property_id: int, source_id: int, body, scrivi):
    """«Applica» un valore del sito (D2): non sulla superficie verificata,
    con lo stesso lock della scheda. «Ignora» non scrive dati: nessun lock."""
    if getattr(body, "action", None) != "apply":
        return scrivi()

    def controlla(cur, agency_id):
        if not _con_la_100(cur):
            return
        cur.execute(f"SELECT p.surface_source, s.conflicts FROM properties p JOIN property_site_sources s "
                    f"ON s.property_id = p.id AND s.id = %s WHERE p.id = %s AND p.agency_id = %s "
                    f"AND {_property_trash.live('p')}", (source_id, property_id, agency_id))
        riga = cur.fetchone()
        if riga is None or riga["surface_source"] != "verified":
            return
        conflitto = next((c for c in (riga["conflicts"] or []) if isinstance(c, dict)
                          and c.get("id") == body.conflict_id), None)
        if conflitto is not None and conflitto.get("field") == "surface_sqm":
            raise census.CensusConflict(VERIFIED_DATA_LOCKED, "VERIFIED_DATA_LOCKED", fields=["surface_sqm"])

    return _sotto_lock(ctx, property_id, controlla, scrivi)
