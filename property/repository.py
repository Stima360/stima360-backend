from __future__ import annotations
from datetime import datetime, timezone
from psycopg2 import errors
from psycopg2.extras import Json
from core.database import core_cursor
from core import property_trash as _property_trash
from core.exceptions import ConflictError, NotFoundError, ValidationError
from core.scope import ProgrammingError

# A31-3: visite acquirente - facade verso l'Agenda e regole D5-D7.
from buyer_visits import facade as _visite_facade
from buyer_visits import guards as _visite_regole

# CRM-OPS-2: descrizione sintetica e codice generati; elenco degli agenti
# assegnabili. L'elenco riusa la query gia' certificata dell'Agenda
# (membri ATTIVI dell'agenzia, stesso nome visualizzato) invece di
# scriverne una seconda: e' lo stesso predicato di
# operator_auth.membership_exists, con cui il bersaglio viene validato.
from appointments import repository as _agenda_repository
from .catalog import TITLE_SOURCE_FIELDS, generated_code, generated_title
# CRM-OPS-3: il rifiuto del trigger della 081 (incarico nuovo senza
# acquisizione) arriva come CHECK violation; il service lo ha gia' escluso,
# questo copre la corsa fra la sua lettura e l'UPDATE.
from acquisitions.enums import MANDATE_ONLY_FROM_ACQUISITION


# Il driver finto dei test senza database non ha CheckViolation: `()` non
# intercetta nulla.
_CHECK_VIOLATION = getattr(errors, 'CheckViolation', ())
# CENSIMENTO-1 Fase 3: le colonne della 083 negli schemi generici. Scritte su
# un database senza la 083 (PROD prima della migration) la statement cade con
# UndefinedColumn: si risponde 503 "modulo non installato", mai un 500 muto -
# e SOLO per queste colonne, qualunque altra colonna mancante resta un errore.
_UNDEFINED_COLUMN = getattr(errors, 'UndefinedColumn', ())
_COLONNE_083 = ('staircase', 'internal_number', 'cadastral_municipality_code', 'cadastral_section',
                'cadastral_sheet', 'cadastral_parcel', 'cadastral_subunit', 'cadastral_category', 'address_inherited')


def _census_not_installed(exc):
    if any(f'"{c}"' in str(exc) for c in _COLONNE_083):
        from . import census as _census
        return _census.CensusNotInstalled(_census.CENSUS_NOT_INSTALLED_MESSAGE)
    return None


def _cadastral_duplicate(exc):
    """CENSIMENTO-1 Fase 4 REV 2 (R1): la PATCH generica che porta un'unita'
    sull'identita' catastale COMPLETA di un'altra (sezione '' compresa) cade
    sull'UNIQUE della 083: 409 CADASTRAL_DUPLICATE leggibile, non «property
    code already exists»."""
    vincolo = getattr(getattr(exc, 'diag', None), 'constraint_name', None) or ''
    if vincolo == 'uq_properties_cadastral_identity':
        from . import census as _census
        return _census._tradotto(exc)
    return None


def _mandate_origin_refused(exc):
    if 'CRM-OPS-3' in str(exc):
        return ValidationError(MANDATE_ONLY_FROM_ACQUISITION)
    if 'CENSIMENTO-1' in str(exc):
        # la guardia della 083 (censimento/commerciale, collegamenti): il
        # messaggio leggibile lo da' property/census.py
        from . import census as _census
        return _census._tradotto(exc)
    return None

def row(x): return dict(x) if x else None


# CENSIMENTO-1 Fase 5: una scheda di censimento (`record_kind = 'census'`) non
# e' un immobile operativo finche' non e' presa in carico. Elenco di default,
# dashboard e avvisi leggono solo le operative (`crm`); le censite si chiedono
# esplicitamente. `record_kind` e' letto dal JSON della riga (stessa scelta di
# acquisitions/repository.py::lock_property): la statement resta eseguibile
# dove la 083 non e' applicata, e li' ogni riga e' operativa.
RECORD_KINDS = ('crm', 'census', 'all')


def record_kind_sql(alias='p'):
    return f"COALESCE(to_jsonb({alias}) ->> 'record_kind', 'crm')"


def record_kind_filter(record_kind, alias='p'):
    """(condizione SQL, parametri) per `crm` / `census`; None per `all`."""
    if record_kind not in RECORD_KINDS:
        raise ValidationError('record_kind must be crm, census or all')
    if record_kind == 'all':
        return None
    return f"{record_kind_sql(alias)} = %s", [record_kind]

def ensure(cur, table, id_, label):
    cur.execute(f"SELECT 1 FROM {table} WHERE id=%s",(id_,))
    if not cur.fetchone(): raise NotFoundError(f"{label} {id_} not found")

def ensure_scoped(cur, table, id_, agency_id, label):
    # DELETE-ARCH Fase 2B2: per l'immobile la stessa lettura porta anche
    # `deleted_at` -> nessun nuovo collegamento verso un immobile nel Cestino
    # (409 PROPERTY_IN_TRASH; la 086 lo ripete nel database).
    colonne = f"{_property_trash.deleted_at_sql(table)} AS deleted_at" if table == 'properties' else "1"
    if agency_id is not None:
        cur.execute(f"SELECT {colonne} FROM {table} WHERE id=%s AND agency_id=%s", (id_, agency_id))
    else:
        cur.execute(f"SELECT {colonne} FROM {table} WHERE id=%s", (id_,))
    riga = cur.fetchone()
    if not riga:
        raise NotFoundError(f"{label} {id_} not found")
    if table == 'properties' and hasattr(riga, 'get') and riga.get('deleted_at') is not None:
        raise _property_trash.PropertyInTrash()

def _free_generated_code(cur, property_id):
    """Il primo IMM-<id>[-n] non occupato. `code` e' UNIQUE su tutta la
    tabella (migration 002): un codice storico scritto a mano potrebbe gia'
    usare la forma IMM-<id>, e in quel caso si aggiunge un suffisso invece di
    sovrascriverlo."""
    for attempt in range(50):
        candidate = generated_code(property_id, attempt)
        cur.execute("SELECT 1 FROM properties WHERE code=%s", (candidate,))
        if not cur.fetchone():
            return candidate
    raise ConflictError('cannot generate a free property code')


def _assign_generated_code(cur, property_id, agency_id):
    candidate = _free_generated_code(cur, property_id)
    cur.execute("UPDATE properties SET code=%s WHERE id=%s AND agency_id=%s AND code IS NULL RETURNING *",
                (candidate, property_id, agency_id))
    return row(cur.fetchone())


def list_assignable_agents(ctx):
    """CRM-OPS-2: i membri ATTIVI dell'agenzia dello scope (id, ruolo, nome)."""
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        return _agenda_repository.agents(cur, agency_id)


def assignable_agent_name(ctx, operator_user_id):
    """Il nome dell'operatore se e' un membro ATTIVO dell'agenzia dello scope,
    altrimenti None. Non dice ne' chi e' ne' dove sta: solo si' o no."""
    for agent in list_assignable_agents(ctx):
        if int(agent["id"]) == int(operator_user_id):
            return agent["name"]
    return None


def create_property(ctx,data,*,generate_identity=False):
    """Create one property, owned by the agency the caller's scope names.

    P26-2C2B. `ctx` is resolved once by the router and passed down; nothing here
    resolves an agency of its own. That distinction is the lesson of P26-2B2B-R1,
    where two calls to the same factory produced two independent decisions that
    could disagree: calling a resolver here would place the row using an answer
    obtained after the one the request was authorised with.

    Ownership is taken from the scope and refused from the payload. The route's
    schema forbids unknown fields, so `agency_id` cannot arrive over HTTP at
    all - the check below is the repository-level restatement of the same rule,
    for a future schema change or an internal caller building a dict by hand.

    `require_agency()` runs before the transaction opens, so a scope with no
    agency - a platform admin holding no membership - leaves nothing behind
    rather than being rolled back.
    """
    if 'agency_id' in data:
        raise ProgrammingError(
            "'agency_id' is derived from the agency scope and must not be supplied"
        )
    agency_id=ctx.require_agency()
    if generate_identity and not (data.get('code') or '').strip():
        # CRM-OPS-2: codice omesso, NULL, vuoto o di soli spazi = mancante.
        # Si salva NULL, cosi' l'UPDATE ... WHERE code IS NULL qui sotto lo
        # assegna nella stessa transazione (e '' non occupa il vincolo UNIQUE).
        data={**data,'code':None}
    if generate_identity and not (data.get('title') or '').strip():
        # CRM-OPS-2: nessun titolo manuale -> descrizione sintetica dai dati.
        data={**data,'title':generated_title(data)}
    data={**data,'metadata':Json(data.get('metadata') or {}),'agency_id':agency_id}
    cols=list(data); vals=[data[x] for x in cols]
    with core_cursor(commit=True) as (_,cur):
        try:
            cur.execute(f"INSERT INTO properties ({','.join(cols)}) VALUES ({','.join(['%s']*len(cols))}) RETURNING *",vals)
        except errors.UniqueViolation as exc: raise ConflictError('property code already exists') from exc
        except _UNDEFINED_COLUMN as exc:
            assente=_census_not_installed(exc)
            if assente is not None: raise assente from exc
            raise
        except _CHECK_VIOLATION as exc:
            refused=_mandate_origin_refused(exc)
            if refused is not None: raise refused from exc
            raise
        created=row(cur.fetchone())
        if generate_identity and not (created.get('code') or '').strip():
            # CRM-OPS-2: codice generato nella stessa transazione, mai dopo.
            created=_assign_generated_code(cur,created['id'],agency_id) or created
        if created.get('asking_price') is not None:
            cur.execute("INSERT INTO property_price_history(property_id,new_price,change_reason) VALUES(%s,%s,%s)",(created['id'],created['asking_price'],'initial price'))
        if created.get('commercial_status'):
            cur.execute("INSERT INTO property_status_history(property_id,field_name,new_value,note) VALUES(%s,'commercial_status',%s,%s)",(created['id'],created['commercial_status'],'initial status'))
        if created.get('classification'):
            cur.execute("INSERT INTO property_status_history(property_id,field_name,new_value,note) VALUES(%s,'classification',%s,%s)",(created['id'],created['classification'],'initial classification'))
        return created

def list_properties(*args, **kwargs):
    # CENSIMENTO-1 Fase 5: solo per nome, cosi' le firme posizionali storiche
    # (11 filtri, con o senza ctx) restano quelle di prima. Default: operative.
    record_kind = kwargs.pop('record_kind', 'crm')
    # DELETE-ARCH Fase 0: gli archiviati escono dalle superfici operative.
    # Si includono solo a richiesta esplicita (`include_archived=True`), quando
    # il filtro di stato chiede proprio `archived`, o nelle viste per
    # relazione (contact_id / lead_id: "gli immobili di questo contatto" sono
    # storia, e lo stato resta visibile riga per riga).
    include_archived = kwargs.pop('include_archived', False)
    if len(args) > 0 and hasattr(args[0], 'require_agency'):
        ctx = args[0]
        agency_id = ctx.require_agency()
        args = args[1:]
    elif 'ctx' in kwargs:
        ctx = kwargs.pop('ctx')
        agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None
    elif len(args) == 12:
        ctx = args[0]
        agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None
        args = args[1:]
    else:
        ctx = None
        agency_id = None

    limit, offset, search, status, classification, city, contact_id, lead_id, assigned_to, mandate_expiring, missing_documents = args
    filters = []
    params = []
    joins = []
    if agency_id is not None:
        filters.append("p.agency_id = %s")
        params.append(agency_id)
    tipo = record_kind_filter(record_kind)
    if tipo is not None:
        filters.append(tipo[0])
        params += tipo[1]
    if search:
        filters.append("(p.title ILIKE %s OR p.code ILIKE %s OR p.address ILIKE %s OR p.city ILIKE %s)")
        params += [f'%{search}%'] * 4
    if status:
        filters.append('p.commercial_status=%s')
        params.append(status)
    if not include_archived and status != 'archived' and not contact_id and not lead_id:
        filters.append("p.archived_at IS NULL AND p.commercial_status <> 'archived'")
    # DELETE-ARCH Fase 2B1: un immobile nel Cestino non compare MAI nella lista
    # principale (nemmeno con include_archived, per contatto o per lead).
    # Letta da to_jsonb (stesso idioma di lost_reason in Fase 1B): il codice
    # resta valido su un database senza la 085.
    filters.append(_property_trash.live('p'))
    if classification:
        filters.append('p.classification=%s')
        params.append(classification)
    if city:
        filters.append('p.city ILIKE %s')
        params.append(f'%{city}%')
    if assigned_to:
        filters.append('p.assigned_to ILIKE %s')
        params.append(f'%{assigned_to}%')
    if contact_id:
        joins.append('JOIN property_contacts pc_filter ON pc_filter.property_id=p.id')
        filters.append('pc_filter.contact_id=%s')
        params.append(contact_id)
    if lead_id:
        joins.append('JOIN property_leads pl_filter ON pl_filter.property_id=p.id')
        filters.append('pl_filter.lead_id=%s')
        params.append(lead_id)
    if mandate_expiring:
        filters.append("p.archived_at IS NULL AND p.mandate_end IS NOT NULL AND p.mandate_end <= CURRENT_DATE + INTERVAL '30 days' AND p.commercial_status NOT IN ('sold','withdrawn','archived')")
    if missing_documents:
        filters.append("EXISTS (SELECT 1 FROM property_documents pd WHERE pd.property_id=p.id AND (pd.status IN ('missing','requested','expired','rejected') OR (pd.expires_at IS NOT NULL AND pd.expires_at < CURRENT_DATE)))")
    where = ' WHERE ' + ' AND '.join(filters) if filters else ''
    params += [limit, offset]
    with core_cursor() as (_, cur):
        cur.execute(f"""
            SELECT DISTINCT p.*,
              (SELECT COUNT(*) FROM property_documents pd WHERE pd.property_id=p.id AND (pd.status IN ('missing','requested','expired','rejected') OR (pd.expires_at IS NOT NULL AND pd.expires_at < CURRENT_DATE))) AS document_issues,
              (SELECT COUNT(*) FROM property_visits pv WHERE pv.property_id=p.id AND pv.status IN ('scheduled','confirmed') AND pv.scheduled_at >= NOW()) AS upcoming_visits,
              ROUND(((CASE WHEN NULLIF(BTRIM(p.title),'') IS NOT NULL THEN 1 ELSE 0 END) +
                     (CASE WHEN NULLIF(BTRIM(p.city),'') IS NOT NULL THEN 1 ELSE 0 END) +
                     (CASE WHEN p.surface_sqm IS NOT NULL AND p.surface_sqm > 0 THEN 1 ELSE 0 END) +
                     (CASE WHEN p.asking_price IS NOT NULL AND p.asking_price > 0 THEN 1 ELSE 0 END) +
                     (CASE WHEN EXISTS (SELECT 1 FROM property_contacts pc WHERE pc.property_id=p.id) THEN 1 ELSE 0 END) +
                     (CASE WHEN EXISTS (SELECT 1 FROM property_photos ph WHERE ph.property_id=p.id) THEN 1 ELSE 0 END) +
                     (CASE WHEN EXISTS (SELECT 1 FROM property_documents pd0 WHERE pd0.property_id=p.id)
                                AND NOT EXISTS (SELECT 1 FROM property_documents pd1 WHERE pd1.property_id=p.id AND (pd1.status IN ('missing','requested','expired','rejected') OR (pd1.expires_at IS NOT NULL AND pd1.expires_at < CURRENT_DATE)))
                           THEN 1 ELSE 0 END) +
                     (CASE WHEN p.classification IS NOT NULL THEN 1 ELSE 0 END)) * 100.0 / 8) AS readiness_score
            FROM properties p {' '.join(joins)}{where}
            ORDER BY p.updated_at DESC,p.id DESC LIMIT %s OFFSET %s
        """, params)
        return [dict(x) for x in cur.fetchall()]

def get_property(*args, **kwargs):
    if len(args) == 1:
        ctx = None
        agency_id = None
        property_id = args[0]
    else:
        ctx = args[0]
        agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None
        property_id = args[1]
    with core_cursor() as (_, cur):
        if agency_id is not None:
            cur.execute('SELECT * FROM properties WHERE id=%s AND agency_id = %s', (property_id, agency_id))
        else:
            cur.execute('SELECT * FROM properties WHERE id=%s', (property_id,))
        p = row(cur.fetchone())
        if not p:
            raise NotFoundError(f'property {property_id} not found')
        cur.execute('SELECT pc.*,c.display_name,c.email,c.phone FROM property_contacts pc JOIN contacts c ON c.id=pc.contact_id WHERE pc.property_id=%s ORDER BY pc.is_primary DESC,pc.id', (property_id,))
        p['contacts'] = [dict(x) for x in cur.fetchall()]
        cur.execute("SELECT pl.*,l.pipeline,l.stage,l.status,l.contact_id,to_jsonb(l)->>'lost_reason' AS lost_reason FROM property_leads pl JOIN leads l ON l.id=pl.lead_id WHERE pl.property_id=%s ORDER BY pl.id", (property_id,))
        p['leads'] = [dict(x) for x in cur.fetchall()]
        for table, key in [('property_documents', 'documents'), ('property_photos', 'photos'), ('property_visits', 'visits')]:
            order = 'sort_order,id' if table == 'property_photos' else ('scheduled_at DESC,id DESC' if table == 'property_visits' else 'created_at DESC,id DESC')
            if table == 'property_visits':
                cur.execute("""SELECT v.*,scheduled.buy_request_id,scheduled.match_id,
                latest.id AS last_commercial_interaction_id,
                latest.interaction_type AS last_commercial_interaction_type
                FROM property_visits v
                LEFT JOIN LATERAL (
                    SELECT i.buy_request_id,i.match_id
                    FROM buy_request_interactions i
                    WHERE i.property_visit_id=v.id AND i.interaction_type='visit_scheduled'
                    ORDER BY i.occurred_at DESC,i.id DESC LIMIT 1
                ) scheduled ON TRUE
                LEFT JOIN LATERAL (
                    SELECT i.id,i.interaction_type
                    FROM buy_request_interactions i
                    WHERE i.property_visit_id=v.id
                      AND i.interaction_type IN ('visited','interested','discarded','offer_candidate')
                    ORDER BY i.occurred_at DESC,i.id DESC LIMIT 1
                ) latest ON TRUE
                WHERE v.property_id=%s ORDER BY v.scheduled_at DESC,v.id DESC""", (property_id,))
            else:
                cur.execute(f'SELECT * FROM {table} WHERE property_id=%s ORDER BY {order}', (property_id,))
            p[key] = [dict(x) for x in cur.fetchall()]
        cur.execute('SELECT * FROM property_price_history WHERE property_id=%s ORDER BY created_at DESC,id DESC', (property_id,))
        p['price_history'] = [dict(x) for x in cur.fetchall()]
        cur.execute('SELECT * FROM property_status_history WHERE property_id=%s ORDER BY created_at DESC,id DESC', (property_id,))
        p['status_history'] = [dict(x) for x in cur.fetchall()]
        today = __import__('datetime').date.today()
        p['document_issues'] = sum(1 for x in p['documents'] if x['status'] in {'missing', 'requested', 'expired', 'rejected'} or (x.get('expires_at') is not None and x['expires_at'] < today))
        p['readiness_score'] = readiness_score(p)
        return p

def readiness_score(p):
    checks=[bool(p.get('title')),bool(p.get('city')),bool(p.get('surface_sqm')),bool(p.get('asking_price')),bool(p.get('contacts')),bool(p.get('photos'))]
    docs=p.get('documents') or []
    today=__import__('datetime').date.today()
    checks.append(bool(docs) and not any(x['status'] in {'missing','requested','expired','rejected'} or (x.get('expires_at') is not None and x['expires_at'] < today) for x in docs))
    checks.append(bool(p.get('classification')))
    return round(sum(checks)/len(checks)*100)

def _not_in_trash(p):
    """DELETE-ARCH Fase 2B1: anche una PATCH vuota su un immobile nel Cestino
    e' PROPERTY_IN_TRASH (non restituisce la scheda)."""
    from . import lifecycle as _lifecycle_trash
    _lifecycle_trash.refuse_if_in_trash(p)
    return p

def update_property(*args, **kwargs):
    # CRM-OPS-2: `derive_identity=True` (solo dal service del form) aggiorna la
    # descrizione generata quando cambiano i dati da cui dipende - mai un
    # titolo scritto a mano - e assegna il codice a un immobile storico che
    # non lo ha. Senza il flag il comportamento e' quello di prima.
    derive_identity = kwargs.pop('derive_identity', False)
    if len(args) == 2:
        ctx = None
        agency_id = None
        property_id = args[0]
        data = args[1]
    else:
        ctx = args[0]
        agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None
        property_id = args[1]
        data = args[2]

    if 'agency_id' in data:
        raise ProgrammingError("'agency_id' is derived from the agency scope and must not be supplied")

    if not data:
        return _not_in_trash(get_property(ctx, property_id) if ctx is not None else get_property(property_id))
    change_reason = data.pop('change_reason', None)
    changed_by = data.pop('changed_by', None)
    history_note = data.pop('history_note', None)
    if 'metadata' in data:
        data['metadata'] = Json(data.get('metadata') or {})
    if not data:
        return _not_in_trash(get_property(ctx, property_id) if ctx is not None else get_property(property_id))

    with core_cursor(commit=True) as (_, cur):
        # DELETE-ARCH Fase 2B1: `deleted_at` via to_jsonb, valido anche senza la 085.
        old_cols = "asking_price,commercial_status,classification,to_jsonb(properties)->>'deleted_at' AS deleted_at"
        if derive_identity:
            old_cols += ',title,code,archived_at,' + ','.join(TITLE_SOURCE_FIELDS)
        if agency_id is not None:
            cur.execute(f'SELECT {old_cols} FROM properties WHERE id=%s AND agency_id = %s FOR UPDATE', (property_id, agency_id))
        else:
            cur.execute(f'SELECT {old_cols} FROM properties WHERE id=%s FOR UPDATE', (property_id,))
        old = cur.fetchone()
        if not old:
            raise NotFoundError(f'property {property_id} not found')
        old = dict(old)
        # DELETE-ARCH Fase 2B1: guardia sulla riga bloccata (nessuna finestra
        # con uno spostamento nel Cestino concorrente).
        from . import lifecycle as _lifecycle_trash
        _lifecycle_trash.refuse_if_in_trash(old)
        if derive_identity:
            # DELETE-ARCH Fase 0, review 2: la stessa regola del service, sulla
            # riga bloccata (la PATCH generica non archivia ne' riattiva).
            from . import lifecycle as _lifecycle
            _lifecycle.check_status_patch(data, old)
            if ('title' not in data and old.get('title') == generated_title(old)
                    and any(f in data for f in TITLE_SOURCE_FIELDS)):
                data['title'] = generated_title({**old, **{f: data[f] for f in TITLE_SOURCE_FIELDS if f in data}})
            if not (old.get('code') or '').strip() and not (data.get('code') or '').strip():
                data['code'] = _free_generated_code(cur, property_id)
        try:
            if agency_id is not None:
                cur.execute(f"UPDATE properties SET {','.join(f'{k}=%s' for k in data)},updated_at=NOW() WHERE id=%s AND agency_id = %s RETURNING *", list(data.values()) + [property_id, agency_id])
            else:
                cur.execute(f"UPDATE properties SET {','.join(f'{k}=%s' for k in data)},updated_at=NOW() WHERE id=%s RETURNING *", list(data.values()) + [property_id])
        except errors.UniqueViolation as exc:
            duplicato = _cadastral_duplicate(exc)
            if duplicato is not None:
                raise duplicato from exc
            raise ConflictError('property code already exists') from exc
        except _UNDEFINED_COLUMN as exc:
            assente = _census_not_installed(exc)
            if assente is not None:
                raise assente from exc
            raise
        except _CHECK_VIOLATION as exc:
            refused = _mandate_origin_refused(exc)
            if refused is not None:
                raise refused from exc
            raise
        r = row(cur.fetchone())
        # CENSIMENTO-1 Fase 3 (§0 p.6): una pertinenza collegata che viene
        # venduta da sola o archiviata perde il collegamento NELLA STESSA
        # transazione, con lo storico su entrambe le schede.
        if (ctx is not None and 'commercial_status' in data and data['commercial_status'] in ('sold', 'archived')
                and data['commercial_status'] != old.get('commercial_status') and r.get('parent_property_id') is not None):
            from . import census as _census
            _census.detach_on_close(ctx, cur, r, data['commercial_status'])
            r['parent_property_id'] = None
        if 'asking_price' in data and data['asking_price'] != old.get('asking_price'):
            cur.execute("INSERT INTO property_price_history(property_id,old_price,new_price,change_reason,changed_by) VALUES(%s,%s,%s,%s,%s)", (property_id, old.get('asking_price'), data['asking_price'], change_reason, changed_by))
        for field in ('commercial_status', 'classification'):
            if field in data and data[field] != old.get(field):
                cur.execute("INSERT INTO property_status_history(property_id,field_name,old_value,new_value,note,changed_by) VALUES(%s,%s,%s,%s,%s,%s)", (property_id, field, old.get(field), data[field], history_note, changed_by))
        return r

def archive_property(*args):
    if len(args) == 1:
        ctx = None
        property_id = args[0]
    else:
        ctx = args[0]
        property_id = args[1]
    payload = {
        'commercial_status': 'archived',
        'archived_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc),
        'history_note': 'archived from admin',
    }
    if ctx is not None:
        return update_property(ctx, property_id, payload)
    return update_property(property_id, payload)

def add_contact(ctx, property_id, data):
    agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None
    with core_cursor(commit=True) as (_, cur):
        ensure_scoped(cur, 'properties', property_id, agency_id, 'property')
        ensure_scoped(cur, 'contacts', data['contact_id'], agency_id, 'contact')
        if data.get('is_primary'):
            cur.execute("UPDATE property_contacts SET is_primary=FALSE WHERE property_id=%s AND role=%s", (property_id, data['role']))
        try:
            cur.execute("INSERT INTO property_contacts(property_id,contact_id,role,is_primary,ownership_share,notes) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *", (property_id, data['contact_id'], data['role'], data.get('is_primary', False), data.get('ownership_share'), data.get('notes')))
        except errors.UniqueViolation as exc:
            raise ConflictError('contact already linked with this role') from exc
        return row(cur.fetchone())

def delete_contact(ctx, property_id, contact_id, role):
    agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None
    with core_cursor(commit=True) as (_, cur):
        ensure_scoped(cur, 'properties', property_id, agency_id, 'property')
        cur.execute('DELETE FROM property_contacts WHERE property_id=%s AND contact_id=%s AND role=%s', (property_id, contact_id, role))
        if not cur.rowcount:
            raise NotFoundError('property contact link not found')

def add_lead(ctx, property_id, data):
    agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None
    with core_cursor(commit=True) as (_, cur):
        ensure_scoped(cur, 'properties', property_id, agency_id, 'property')
        ensure_scoped(cur, 'leads', data['lead_id'], agency_id, 'lead')
        try:
            cur.execute('INSERT INTO property_leads(property_id,lead_id,relation_type) VALUES(%s,%s,%s) RETURNING *', (property_id, data['lead_id'], data['relation_type']))
        except errors.UniqueViolation as exc:
            raise ConflictError('lead already linked') from exc
        return row(cur.fetchone())

def delete_lead(ctx, property_id, lead_id):
    agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None
    with core_cursor(commit=True) as (_, cur):
        ensure_scoped(cur, 'properties', property_id, agency_id, 'property')
        cur.execute('DELETE FROM property_leads WHERE property_id=%s AND lead_id=%s', (property_id, lead_id))
        if not cur.rowcount:
            raise NotFoundError('property lead link not found')

_OWNER_BINARY_METADATA_KEYS = {
    "sha256",
    "size_bytes",
    "mime_detected",
    "sanitized_filename",
    "storage_provider",
}

def _document_has_published_owner_share(cur, document_id):
    cur.execute(
        """SELECT 1 FROM owner_shared_documents
           WHERE property_document_id=%s
             AND (published_at IS NOT NULL OR status IN ('published','revoked','archived'))
           LIMIT 1""",
        (document_id,),
    )
    return bool(cur.fetchone())

def _binary_document_change(current, updates):
    for field in ("url", "storage_key"):
        if field in updates and updates[field] != current.get(field):
            return True
    if "metadata" in updates:
        previous = current.get("metadata") or {}
        incoming = updates.get("metadata") or {}
        if not isinstance(previous, dict) or not isinstance(incoming, dict):
            return True
        for key in _OWNER_BINARY_METADATA_KEYS:
            if incoming.get(key) != previous.get(key):
                return True
    return False

def create_child(*args, **kwargs):
    if len(args) == 3:
        ctx = None
        agency_id = None
        table, property_id, data = args
    else:
        ctx, table, property_id, data = args
        agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None

    with core_cursor(commit=True) as (_, cur):
        ensure_scoped(cur, 'properties', property_id, agency_id, 'property')
        if table == 'property_visits':
            if data.get('contact_id') is not None:
                ensure_scoped(cur, 'contacts', data['contact_id'], agency_id, 'contact')
            if data.get('lead_id') is not None:
                ensure_scoped(cur, 'leads', data['lead_id'], agency_id, 'lead')
        if table in {'property_documents', 'property_photos'} and 'metadata' in data:
            data['metadata'] = Json(data.get('metadata') or {})
        if table == 'property_photos' and data.get('is_cover'):
            cur.execute('UPDATE property_photos SET is_cover=FALSE WHERE property_id=%s', (property_id,))
        data = {**data, 'property_id': property_id}
        cols = list(data)
        cur.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join(['%s']*len(cols))}) RETURNING *", list(data.values()))
        return row(cur.fetchone())

def update_child(*args, **kwargs):
    if len(args) == 4:
        ctx = None
        agency_id = None
        table, item_id, data, label = args
    else:
        ctx, table, item_id, data, label = args
        agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None

    if not data:
        with core_cursor() as (_, cur):
            if agency_id is not None:
                cur.execute(f"SELECT c.* FROM {table} c JOIN properties p ON p.id=c.property_id WHERE c.id=%s AND p.agency_id=%s AND {_property_trash.live('p')}", (item_id, agency_id))
            else:
                cur.execute(f'SELECT * FROM {table} WHERE id=%s', (item_id,))
            r = cur.fetchone()
            if not r:
                raise NotFoundError(f'{label} {item_id} not found')
            return row(r)

    with core_cursor(commit=True) as (_, cur):
        if agency_id is not None:
            cur.execute(
                f"SELECT c.*, {_property_trash.deleted_at_sql('p')} AS property_deleted_at "
                f"FROM {table} c JOIN properties p ON p.id=c.property_id WHERE c.id=%s AND p.agency_id=%s FOR UPDATE",
                (item_id, agency_id),
            )
        else:
            cur.execute(f'SELECT * FROM {table} WHERE id=%s FOR UPDATE', (item_id,))
        current = cur.fetchone()
        if not current:
            raise NotFoundError(f'{label} {item_id} not found')
        current = dict(current)
        if current.pop('property_deleted_at', None) is not None:   # DELETE-ARCH 2B2: congelato
            raise _property_trash.PropertyInTrash()
        if table == 'property_documents':
            if _binary_document_change(current, data) and _document_has_published_owner_share(cur, item_id):
                raise ConflictError('Il file di un documento già pubblicato in OWNER è immutabile; creare una nuova versione')
            merged = {**current, **data}
            if not merged.get('url') and not merged.get('storage_key') and merged.get('status') not in {'missing', 'requested'}:
                raise ValidationError('url or storage_key required for this document status')
        if table in {'property_documents', 'property_photos'} and 'metadata' in data:
            data['metadata'] = Json(data.get('metadata') or {})
        if table == 'property_photos' and data.get('is_cover'):
            cur.execute('UPDATE property_photos SET is_cover=FALSE WHERE property_id=%s', (current['property_id'],))
        extra = ',updated_at=NOW()' if table == 'property_documents' else ''
        cur.execute(f"UPDATE {table} SET {','.join(f'{k}=%s' for k in data)}{extra} WHERE id=%s RETURNING *", list(data.values()) + [item_id])
        r = cur.fetchone()
        if not r:
            raise NotFoundError(f'{label} {item_id} not found')
        return row(r)

def list_visits(*args, **kwargs):
    if len(args) > 0 and hasattr(args[0], 'require_agency'):
        ctx = args[0]
        agency_id = ctx.require_agency()
        args = args[1:]
    elif 'ctx' in kwargs:
        ctx = kwargs.pop('ctx')
        agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None
    elif len(args) == 6:
        ctx = args[0]
        agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None
        args = args[1:]
    else:
        ctx = None
        agency_id = None

    limit, offset, status, from_date, to_date = args
    filters = [_property_trash.live('p')]          # DELETE-ARCH 2B2
    params = []
    if agency_id is not None:
        filters.append('p.agency_id = %s')
        params.append(agency_id)
    if status:
        filters.append('v.status=%s')
        params.append(status)
    if from_date:
        filters.append('v.scheduled_at >= %s')
        params.append(from_date)
    if to_date:
        filters.append('v.scheduled_at <= %s')
        params.append(to_date)
    where = ' WHERE ' + ' AND '.join(filters) if filters else ''
    params += [limit, offset]
    with core_cursor() as (_, cur):
        cur.execute(
            f"SELECT v.*,p.title AS property_title,p.code AS property_code,c.display_name AS contact_name FROM property_visits v JOIN properties p ON p.id=v.property_id LEFT JOIN contacts c ON c.id=v.contact_id{where} ORDER BY v.scheduled_at DESC,v.id DESC LIMIT %s OFFSET %s",
            params,
        )
        return [dict(x) for x in cur.fetchall()]

def list_visits_by_contact(*args, **kwargs):
    if len(args) == 1:
        ctx = None
        agency_id = None
        contact_id = args[0]
    else:
        ctx = args[0]
        agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None
        contact_id = args[1]
    with core_cursor() as (_, cur):
        if agency_id is not None:
            cur.execute(
                """SELECT v.*,p.title AS property_title,p.code AS property_code,c.display_name AS contact_name
                   FROM property_visits v
                   JOIN properties p ON p.id=v.property_id
                   LEFT JOIN contacts c ON c.id=v.contact_id
                   WHERE v.contact_id=%s AND p.agency_id=%s AND (to_jsonb(p)->>'deleted_at') IS NULL
                   ORDER BY v.scheduled_at DESC,v.id DESC""",
                (contact_id, agency_id),
            )
        else:
            cur.execute(
                """SELECT v.*,p.title AS property_title,p.code AS property_code,c.display_name AS contact_name
                   FROM property_visits v
                   JOIN properties p ON p.id=v.property_id
                   LEFT JOIN contacts c ON c.id=v.contact_id
                   WHERE v.contact_id=%s AND (to_jsonb(p)->>'deleted_at') IS NULL
                   ORDER BY v.scheduled_at DESC,v.id DESC""",
                (contact_id,),
            )
        return [dict(x) for x in cur.fetchall()]

#: A31-4: una visita futura da svolgere senza agente certo non nasce piu'.
_VISITA_SENZA_AGENTE = ("Una visita futura da svolgere si programma dall'Agenda: "
                        "scegli l'agente (assigned_user_id obbligatorio)")


def _visita_da_agenda(data, agente):
    """A31-3 §7: una NUOVA visita da svolgere (`scheduled`/`confirmed`) nel
    futuro nasce nell'Agenda. Le visite passate o gia' concluse si
    registrano come prima (storico). A31-4: senza un agente certo una visita
    futura aperta e' un errore, mai piu' una riga legacy."""
    if data.get('status') not in _visite_facade.STATI_APERTI:
        return False
    quando = data.get('scheduled_at')
    if quando is None:
        return False
    if quando.tzinfo is None or quando.utcoffset() is None:
        futura = True   # senza fuso: la facade lo rifiuta con un errore pulito
    else:
        futura = quando > datetime.now(timezone.utc)
    if futura and agente is None:
        raise ValidationError(_VISITA_SENZA_AGENTE)
    return futura


def add_visit(ctx, property_id, data):
    """POST /properties/{id}/visits. A31-3: facade verso l'Agenda per una
    visita futura da svolgere con agente certo (esplicito, o l'agent stesso);
    risposta invariata: la riga `property_visits` (la proiezione). Owner/admin
    senza agente esplicito: ValidationError (A31-4, percorso legacy spento
    per le visite future aperte). Visite storiche: percorso legacy invariato."""
    data = dict(data)
    richiesto = data.pop('assigned_user_id', None)
    client_request_id = data.pop('client_request_id', None)
    agente = _visite_facade.resolve_agent(ctx, richiesto) if ctx is not None else None
    if not _visita_da_agenda(data, agente):
        return create_child(ctx, 'property_visits', property_id, data)
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        ensure_scoped(cur, 'properties', property_id, agency_id, 'property')
        if data.get('contact_id') is not None:
            ensure_scoped(cur, 'contacts', data['contact_id'], agency_id, 'contact')
        if data.get('lead_id') is not None:
            ensure_scoped(cur, 'leads', data['lead_id'], agency_id, 'lead')
        _, visita, _ = _visite_facade.schedule(
            cur, ctx, property_id=property_id, contact_id=data.get('contact_id'),
            lead_id=data.get('lead_id'), start_at=data['scheduled_at'],
            assigned_user_id=agente, status=data['status'],
            client_request_id=client_request_id)
        # D4: esito/feedback/voto restano del percorso legacy: se il form li
        # manda gia' in creazione, li scrive questo repository, non la proiezione.
        legacy = {k: data[k] for k in _visite_regole.CAMPI_LEGACY if data.get(k) is not None}
        if legacy:
            cur.execute(
                f"UPDATE property_visits SET {','.join(f'{k}=%s' for k in legacy)},"
                "updated_at=NOW() WHERE id=%s",
                list(legacy.values()) + [visita['id']],
            )
        cur.execute('SELECT * FROM property_visits WHERE id=%s', (visita['id'],))
        return row(cur.fetchone())

def update_visit(*args, **kwargs):
    if len(args) == 2:
        ctx = None
        agency_id = None
        visit_id = args[0]
        data = args[1]
    else:
        ctx = args[0]
        agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None
        visit_id = args[1]
        data = args[2]

    if not data:
        with core_cursor() as (_, cur):
            if agency_id is not None:
                cur.execute(
                    f"SELECT v.* FROM property_visits v JOIN properties p ON p.id = v.property_id WHERE v.id = %s AND p.agency_id = %s AND {_property_trash.live('p')}",
                    (visit_id, agency_id),
                )
            else:
                cur.execute('SELECT * FROM property_visits WHERE id=%s', (visit_id,))
            r = cur.fetchone()
            if not r:
                raise NotFoundError(f'visit {visit_id} not found')
            return row(r)

    with core_cursor(commit=True) as (_, cur):
        if agency_id is not None:
            cur.execute(
                f"SELECT v.*, {_property_trash.deleted_at_sql('p')} AS property_deleted_at "
                "FROM property_visits v JOIN properties p ON p.id = v.property_id WHERE v.id = %s AND p.agency_id = %s FOR UPDATE",
                (visit_id, agency_id),
            )
            current = cur.fetchone()
            if not current:
                raise NotFoundError(f'visit {visit_id} not found')
            current = dict(current)
            if current.pop('property_deleted_at', None) is not None:   # DELETE-ARCH 2B2: congelato
                raise _property_trash.PropertyInTrash()
            # A31-3 (D5/D6 + regola A31-1): prima di scrivere, sulla riga bloccata.
            _visite_regole.check_patch(dict(current), data, datetime.now(timezone.utc))
            if data.get('contact_id') is not None:
                ensure_scoped(cur, 'contacts', data['contact_id'], agency_id, 'contact')
            if data.get('lead_id') is not None:
                ensure_scoped(cur, 'leads', data['lead_id'], agency_id, 'lead')
        else:
            # Firma storica senza ctx: nessuna route la usa (il router PROPERTY
            # passa sempre ctx). Resta com'era; le regole A31-3 stanno sopra.
            if data.get('contact_id') is not None:
                ensure(cur, 'contacts', data['contact_id'], 'contact')
            if data.get('lead_id') is not None:
                ensure(cur, 'leads', data['lead_id'], 'lead')
        cur.execute(
            f"UPDATE property_visits SET {','.join(f'{k}=%s' for k in data)},updated_at=NOW() WHERE id=%s RETURNING *",
            list(data.values()) + [visit_id],
        )
        r = cur.fetchone()
        if not r:
            raise NotFoundError(f'visit {visit_id} not found')
        return row(r)

def delete_child(*args, **kwargs):
    if len(args) == 3:
        ctx = None
        agency_id = None
        table, item_id, label = args
    else:
        ctx, table, item_id, label = args
        agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None

    with core_cursor(commit=True) as (_, cur):
        if agency_id is not None:
            cur.execute(
                f"SELECT c.id, c.property_id, {_property_trash.deleted_at_sql('p')} AS property_deleted_at "
                f"FROM {table} c JOIN properties p ON p.id = c.property_id WHERE c.id = %s AND p.agency_id = %s",
                (item_id, agency_id),
            )
            rec = cur.fetchone()
            if not rec:
                raise NotFoundError(f'{label} {item_id} not found')
            if hasattr(rec, 'get') and rec.get('property_deleted_at') is not None:   # DELETE-ARCH 2B2: congelato
                raise _property_trash.PropertyInTrash()
        if table == 'property_documents' and _document_has_published_owner_share(cur, item_id):
            raise ConflictError('Un documento già pubblicato in OWNER non può essere eliminato')
        if table == 'property_visits':
            # A31-3, D7: una visita proiettata si annulla dall'Agenda.
            cur.execute('SELECT appointment_id FROM property_visits WHERE id=%s FOR UPDATE', (item_id,))
            visita = cur.fetchone()
            if visita:
                _visite_regole.check_delete(dict(visita))
        cur.execute(f'DELETE FROM {table} WHERE id=%s', (item_id,))
        if not cur.rowcount:
            raise NotFoundError(f'{label} {item_id} not found')

def delete_visit(*args, **kwargs):
    if len(args) == 1:
        ctx = None
        visit_id = args[0]
    else:
        ctx = args[0]
        visit_id = args[1]
    return delete_child(ctx, 'property_visits', visit_id, 'visit') if ctx is not None else delete_child('property_visits', visit_id, 'visit')

def dashboard(ctx=None):
    agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None
    with core_cursor() as (_, cur):
        if agency_id is not None:
            cur.execute("""
            SELECT
              COUNT(*) FILTER (WHERE archived_at IS NULL) AS total,
              COUNT(*) FILTER (WHERE archived_at IS NULL AND commercial_status IN ('mandate','active','reserved','under_offer')) AS active,
              COUNT(*) FILTER (WHERE archived_at IS NULL AND classification='A') AS class_a,
              COUNT(*) FILTER (WHERE archived_at IS NULL AND classification='B') AS class_b,
              COUNT(*) FILTER (WHERE archived_at IS NULL AND classification='C') AS class_c,
              COALESCE(SUM(asking_price) FILTER (WHERE archived_at IS NULL AND commercial_status IN ('mandate','active','reserved','under_offer')),0) AS active_value,
              COUNT(*) FILTER (WHERE archived_at IS NULL AND mandate_end IS NOT NULL AND mandate_end <= CURRENT_DATE + INTERVAL '30 days' AND commercial_status NOT IN ('sold','withdrawn','archived')) AS expiring_mandates
            FROM properties p
            WHERE agency_id = %s AND (to_jsonb(p)->>'deleted_at') IS NULL AND COALESCE(to_jsonb(p) ->> 'record_kind', 'crm') = 'crm'
            """, (agency_id,))
            kpi = dict(cur.fetchone())
            cur.execute("""SELECT COUNT(*) AS count FROM property_documents d JOIN properties p ON p.id=d.property_id
                WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL AND p.agency_id = %s AND COALESCE(to_jsonb(p) ->> 'record_kind', 'crm') = 'crm' AND (d.status IN ('missing','requested','expired','rejected') OR (d.expires_at IS NOT NULL AND d.expires_at < CURRENT_DATE))""", (agency_id,))
            kpi['document_issues'] = cur.fetchone()['count']
            cur.execute("""SELECT COUNT(*) AS count FROM property_visits v JOIN properties p ON p.id=v.property_id
                WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL AND p.agency_id = %s AND v.status IN ('scheduled','confirmed') AND (v.scheduled_at AT TIME ZONE 'Europe/Rome')::date=(NOW() AT TIME ZONE 'Europe/Rome')::date""", (agency_id,))
            kpi['visits_today'] = cur.fetchone()['count']
            cur.execute("""SELECT COUNT(*) AS count FROM property_visits v JOIN properties p ON p.id=v.property_id
                WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL AND p.agency_id = %s AND v.status IN ('scheduled','confirmed') AND v.scheduled_at > NOW()""", (agency_id,))
            kpi['upcoming_visits'] = cur.fetchone()['count']
            cur.execute("""SELECT p.id,p.code,p.title,p.commercial_status,p.classification,p.mandate_end,p.asking_price,
              (SELECT COUNT(*) FROM property_documents d WHERE d.property_id=p.id AND (d.status IN ('missing','requested','expired','rejected') OR (d.expires_at IS NOT NULL AND d.expires_at < CURRENT_DATE))) AS document_issues
              FROM properties p WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL AND p.agency_id = %s AND COALESCE(to_jsonb(p) ->> 'record_kind', 'crm') = 'crm' ORDER BY p.updated_at DESC LIMIT 8""", (agency_id,))
            kpi['recent_properties'] = [dict(x) for x in cur.fetchall()]
            cur.execute("""SELECT v.*,p.title AS property_title FROM property_visits v JOIN properties p ON p.id=v.property_id
              WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL AND p.agency_id = %s AND v.status IN ('scheduled','confirmed') AND v.scheduled_at>=NOW() ORDER BY v.scheduled_at LIMIT 8""", (agency_id,))
            kpi['next_visits'] = [dict(x) for x in cur.fetchall()]
        else:
            cur.execute("""
            SELECT
              COUNT(*) FILTER (WHERE archived_at IS NULL) AS total,
              COUNT(*) FILTER (WHERE archived_at IS NULL AND commercial_status IN ('mandate','active','reserved','under_offer')) AS active,
              COUNT(*) FILTER (WHERE archived_at IS NULL AND classification='A') AS class_a,
              COUNT(*) FILTER (WHERE archived_at IS NULL AND classification='B') AS class_b,
              COUNT(*) FILTER (WHERE archived_at IS NULL AND classification='C') AS class_c,
              COALESCE(SUM(asking_price) FILTER (WHERE archived_at IS NULL AND commercial_status IN ('mandate','active','reserved','under_offer')),0) AS active_value,
              COUNT(*) FILTER (WHERE archived_at IS NULL AND mandate_end IS NOT NULL AND mandate_end <= CURRENT_DATE + INTERVAL '30 days' AND commercial_status NOT IN ('sold','withdrawn','archived')) AS expiring_mandates
            FROM properties p
            WHERE (to_jsonb(p)->>'deleted_at') IS NULL AND COALESCE(to_jsonb(p) ->> 'record_kind', 'crm') = 'crm'
            """)
            kpi = dict(cur.fetchone())
            cur.execute("SELECT COUNT(*) AS count FROM property_documents d JOIN properties p ON p.id=d.property_id WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL AND COALESCE(to_jsonb(p) ->> 'record_kind', 'crm') = 'crm' AND (d.status IN ('missing','requested','expired','rejected') OR (d.expires_at IS NOT NULL AND d.expires_at < CURRENT_DATE))")
            kpi['document_issues'] = cur.fetchone()['count']
            cur.execute("SELECT COUNT(*) AS count FROM property_visits v JOIN properties p ON p.id=v.property_id WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL AND v.status IN ('scheduled','confirmed') AND (v.scheduled_at AT TIME ZONE 'Europe/Rome')::date=(NOW() AT TIME ZONE 'Europe/Rome')::date")
            kpi['visits_today'] = cur.fetchone()['count']
            cur.execute("SELECT COUNT(*) AS count FROM property_visits v JOIN properties p ON p.id=v.property_id WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL AND v.status IN ('scheduled','confirmed') AND v.scheduled_at > NOW()")
            kpi['upcoming_visits'] = cur.fetchone()['count']
            cur.execute("""SELECT p.id,p.code,p.title,p.commercial_status,p.classification,p.mandate_end,p.asking_price,
              (SELECT COUNT(*) FROM property_documents d WHERE d.property_id=p.id AND (d.status IN ('missing','requested','expired','rejected') OR (d.expires_at IS NOT NULL AND d.expires_at < CURRENT_DATE))) AS document_issues
              FROM properties p WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL AND COALESCE(to_jsonb(p) ->> 'record_kind', 'crm') = 'crm' ORDER BY p.updated_at DESC LIMIT 8""")
            kpi['recent_properties'] = [dict(x) for x in cur.fetchall()]
            cur.execute("""SELECT v.*,p.title AS property_title FROM property_visits v JOIN properties p ON p.id=v.property_id
              WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL AND v.status IN ('scheduled','confirmed') AND v.scheduled_at>=NOW() ORDER BY v.scheduled_at LIMIT 8""")
            kpi['next_visits'] = [dict(x) for x in cur.fetchall()]
        # CENSIMENTO-1 Fase 5: le unita' censite hanno il loro contatore, mai
        # mescolato ai KPI operativi qui sopra. Le visite restano visite
        # (eventi dell'Agenda), qualunque sia la scheda.
        cur.execute("SELECT count(*) AS count FROM properties p WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL"
                    + (" AND p.agency_id = %s" if agency_id is not None else "")
                    + " AND COALESCE(to_jsonb(p) ->> 'record_kind', 'crm') = 'census'",
                    (agency_id,) if agency_id is not None else ())
        kpi['census_units'] = cur.fetchone()['count']
        return kpi

def alerts(ctx=None):
    agency_id = ctx.require_agency() if ctx is not None and hasattr(ctx, 'require_agency') else None
    with core_cursor() as (_, cur):
        if agency_id is not None:
            cur.execute("""
            SELECT 'mandate' AS alert_type,p.id AS property_id,p.title,p.code,p.mandate_end AS due_date,
                   'Incarico in scadenza' AS message
            FROM properties p
            WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL
              AND p.agency_id = %s
              AND p.commercial_status NOT IN ('sold','withdrawn','archived')
              AND p.mandate_end IS NOT NULL
              AND p.mandate_end <= CURRENT_DATE + INTERVAL '30 days'
            UNION ALL
            SELECT 'document',p.id,p.title,p.code,d.expires_at,
                   'Documento: '||d.title||' ('||d.status||')'
            FROM property_documents d JOIN properties p ON p.id=d.property_id
            WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL
              AND p.agency_id = %s
              AND COALESCE(to_jsonb(p) ->> 'record_kind', 'crm') = 'crm'
              AND (
                 d.status IN ('missing','requested','expired','rejected')
                 OR (d.expires_at IS NOT NULL AND d.expires_at <= CURRENT_DATE + INTERVAL '30 days')
            )
            UNION ALL
            SELECT 'visit',p.id,p.title,p.code,v.scheduled_at::date,
                   'Visita '||v.status||' alle '||to_char(v.scheduled_at,'DD/MM/YYYY HH24:MI')
            FROM property_visits v JOIN properties p ON p.id=v.property_id
            WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL
              AND p.agency_id = %s
              AND v.status IN ('scheduled','confirmed')
              AND v.scheduled_at BETWEEN NOW() AND NOW()+INTERVAL '7 days'
            ORDER BY due_date NULLS LAST
            """, (agency_id, agency_id, agency_id))
        else:
            cur.execute("""
            SELECT 'mandate' AS alert_type,p.id AS property_id,p.title,p.code,p.mandate_end AS due_date,
                   'Incarico in scadenza' AS message
            FROM properties p
            WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL
              AND p.commercial_status NOT IN ('sold','withdrawn','archived')
              AND p.mandate_end IS NOT NULL
              AND p.mandate_end <= CURRENT_DATE + INTERVAL '30 days'
            UNION ALL
            SELECT 'document',p.id,p.title,p.code,d.expires_at,
                   'Documento: '||d.title||' ('||d.status||')'
            FROM property_documents d JOIN properties p ON p.id=d.property_id
            WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL AND COALESCE(to_jsonb(p) ->> 'record_kind', 'crm') = 'crm' AND (
                 d.status IN ('missing','requested','expired','rejected')
                 OR (d.expires_at IS NOT NULL AND d.expires_at <= CURRENT_DATE + INTERVAL '30 days')
            )
            UNION ALL
            SELECT 'visit',p.id,p.title,p.code,v.scheduled_at::date,
                   'Visita '||v.status||' alle '||to_char(v.scheduled_at,'DD/MM/YYYY HH24:MI')
            FROM property_visits v JOIN properties p ON p.id=v.property_id
            WHERE p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL AND v.status IN ('scheduled','confirmed') AND v.scheduled_at BETWEEN NOW() AND NOW()+INTERVAL '7 days'
            ORDER BY due_date NULLS LAST
            """)
        return {'items': [dict(x) for x in cur.fetchall()]}
