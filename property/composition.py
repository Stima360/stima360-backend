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
        riga["provisional_numbering"] = bool(isinstance(lotto, dict) and lotto.get("provisional_numbering"))
        riga["missing"] = classi.missing_data(riga)
        riga["cadastral_source"] = classi.has_cadastral_source(riga)
        # indicatore SEPARATO dalla completezza: verificata / da riconfermare
        riga["verification"] = classi.verification_state(riga)
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
