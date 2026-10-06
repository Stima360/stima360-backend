from core.exceptions import PermissionDenied, ValidationError
from operator_auth import permissions

from . import repository
# CRM-OPS-3: un incarico NUOVO nasce solo da un'acquisizione
# (`POST /api/acquisitions/{id}/mandate`). Stesse parole del service delle
# Acquisizioni e dell'interfaccia; il trigger della 081 resta la garanzia.
from acquisitions.enums import MANDATE_ONLY_FROM_ACQUISITION
from .catalog import (ENERGY_CLASSES, PROPERTY_TYPE_LABELS, TERRITORY_SOURCES,
                      cadastral_categories_for_form, cadastral_suggestions_for_form,
                      census_labels_for_form, territory_tree, validate_energy_class, validate_location)
from . import census as _census
from . import lifecycle as _lifecycle

# CRM-OPS-2: stesso testo del rifiuto di assegnazione di CORE.
ASSIGNMENT_DENIED_MESSAGE = (
    "Questa operazione richiede un ruolo di amministrazione dell'agenzia."
)
ASSIGNMENT_TARGET_INVALID = "L'operatore scelto non e' un membro attivo dell'agenzia."
LOCATION_FIELDS = ('region', 'province', 'city', 'microzone')
MANDATE_FIELDS = ('mandate_type', 'mandate_start', 'mandate_end')
# CENSIMENTO-1 Fase 3 (§7): su una scheda `census` il service rifiuta - prima
# del trigger della 083 - ogni stato fuori da draft/archived, i campi
# dell'incarico e (nelle Acquisizioni) la creazione di un'acquisizione.
ADDRESS_FIELDS = ('region', 'province', 'city', 'microzone', 'address', 'civic_number', 'postal_code')
CENSUS_ALLOWED_STATUSES = ('draft', 'archived')
# I campi della 083 negli schemi generici: entrano nell'INSERT solo se il
# client li ha inviati. Cosi' una creazione ordinaria resta la statement di
# prima anche dove la 083 non e' ancora applicata (ordine DB-first: il codice
# non deve rompere la creazione degli immobili su un database senza di essa).
CENSUS_SCHEMA_FIELDS = ('staircase', 'internal_number', 'cadastral_municipality_code', 'cadastral_section',
                        'cadastral_sheet', 'cadastral_parcel', 'cadastral_subunit', 'cadastral_category')


def _check_census_guard(data, current):
    if (current or {}).get('record_kind') != 'census':
        return
    if 'commercial_status' in data and data['commercial_status'] not in CENSUS_ALLOWED_STATUSES:
        raise _census.CensusConflict(_census.CENSUS_LOCKED, 'CENSUS_LOCKED')
    if any(data.get(f) is not None for f in MANDATE_FIELDS):
        raise _census.CensusConflict(_census.CENSUS_LOCKED, 'CENSUS_LOCKED')


def _check_inherited_address(data, current):
    """«Ingresso diverso?»: un campo di indirizzo scritto su un'unita' con
    indirizzo ereditato dalla palazzina la rende personalizzata (§6.4)."""
    if (current or {}).get('address_inherited') and any(f in data for f in ADDRESS_FIELDS):
        data['address_inherited'] = False


def _new_mandate(data, current=None):
    """True se la richiesta crea un incarico NUOVO: un campo dell'incarico
    portato a un valore diverso da quello salvato, o lo stato che DIVENTA
    `mandate`. Stessa definizione del trigger della 081. Rimandare invariati
    i valori storici (property_admin lo fa a ogni salvataggio), azzerarli o
    cambiare altri campi non e' un incarico nuovo."""
    current = current or {}
    for field in MANDATE_FIELDS:
        if field in data and data[field] is not None and data[field] != current.get(field):
            return True
    return data.get('commercial_status') == 'mandate' and current.get('commercial_status') != 'mandate'


#: FIX-MANDATE-1: un incarico nato da un'acquisizione resta un incarico
#: (definizione canonica, core/property_mandate.py). Tipo e data d'inizio si
#: possono cambiare, non svuotare: svuotarli lo faceva sparire dalla sezione
#: Incarichi mentre il Cestino lo vedeva ancora. La scadenza resta facoltativa.
MANDATE_FIELDS_REQUIRED = ("Un incarico generato da un'acquisizione resta un incarico: "
                           "tipo e data d'inizio si possono modificare, non svuotare")


def _check_mandate_origin(data, current=None):
    if _new_mandate(data, current) and (current or {}).get('acquisition_id') is None:
        raise ValidationError(MANDATE_ONLY_FROM_ACQUISITION)
    if (current or {}).get('acquisition_id') is not None:
        for field in ('mandate_type', 'mandate_start'):
            if field in data and (data[field] is None or not str(data[field]).strip()):
                raise ValidationError(MANDATE_FIELDS_REQUIRED)


def _may_assign(ctx):
    """La regola P26-1 delle assegnazioni (titolare, amministratore, platform
    admin dentro l'agenzia), la stessa di contatti e lead."""
    return permissions.may_assign_records(getattr(ctx, 'role', None),
                                          getattr(ctx, 'is_platform_admin', False))


def _apply_assignment(ctx, data, current_agent_id=None):
    """Verifica e completa `assigned_agent_id` nel payload.

    * invariato -> tolto dal payload: salvare il form senza toccare l'agente
      non richiede alcun permesso;
    * cambiato -> solo chi puo' assegnare (403 altrimenti), e solo verso un
      membro ATTIVO della stessa agenzia (400 altrimenti);
    * `assigned_to` diventa l'istantanea del nome, scritta dal server.
    """
    if 'assigned_agent_id' not in data:
        return
    target = data['assigned_agent_id']
    if target == current_agent_id:
        del data['assigned_agent_id']
        return
    if not _may_assign(ctx):
        raise PermissionDenied(ASSIGNMENT_DENIED_MESSAGE)
    if target is None:
        data['assigned_to'] = None
        return
    name = repository.assignable_agent_name(ctx, target)
    if name is None:
        raise ValidationError(ASSIGNMENT_TARGET_INVALID)
    data['assigned_to'] = name


def form_options(ctx):
    """Tutto cio' che il form Immobili deve offrire, deciso qui e non nel
    browser: territorio, classi energetiche, tipologie, il catalogo delle
    categorie catastali con i suggerimenti per tipologia (CENSIMENTO-1 Fase 2)
    e - solo per chi puo' assegnare - gli agenti assegnabili della propria
    agenzia."""
    can_assign = _may_assign(ctx)
    return {
        'territory': territory_tree(),
        'territory_sources': TERRITORY_SOURCES,
        'energy_classes': list(ENERGY_CLASSES),
        'property_types': [{'value': k, 'label': v} for k, v in PROPERTY_TYPE_LABELS.items()],
        'cadastral_categories': cadastral_categories_for_form(),
        'cadastral_suggestions': cadastral_suggestions_for_form(),
        # CENSIMENTO-1 Fase 4: le etichette dei chips del censimento (tipo di
        # edificio, fonte delle unita' dichiarate, tipo di accessorio).
        **census_labels_for_form(),
        'can_assign': can_assign,
        'agents': repository.list_assignable_agents(ctx) if can_assign else [],
    }


def dump(model,exclude_unset=False):
    return model.model_dump(exclude_unset=exclude_unset) if hasattr(model,'model_dump') else model.dict(exclude_unset=exclude_unset)
# P26-2C: ctx is threaded straight through, unread and unmodified. This layer
# decides what the record looks like, never which agency it belongs to.
def _fields_set(model):
    return set(getattr(model, 'model_fields_set', None) or getattr(model, '__fields_set__', set()))


def _check_catalog(data, current=None):
    """Le regole del catalogo, con i client storici al riparo.

    * Territorio: si giudica solo quando la richiesta porta `region`. Il form
      OS invia SEMPRE la scelta a cascata intera (regione compresa);
      property_admin non ha mai conosciuto la regione e continua a inviare
      comune/provincia/microzona a testo libero come prima. Il giudizio e' sul
      RISULTATO: i livelli non inviati restano quelli salvati.
    * Classe energetica: si giudica solo un valore NUOVO. Rimandare invariata
      una classe storica (es. "g", che property_admin rimanda a ogni
      salvataggio) non e' un errore; scriverne una fuori elenco si'.
    """
    current = current or {}
    try:
        if 'region' in data:
            merged = {f: data.get(f, current.get(f)) for f in LOCATION_FIELDS}
            validate_location(merged['region'], merged['province'], merged['city'], merged['microzone'])
        if 'energy_class' in data and data['energy_class'] != current.get('energy_class'):
            validate_energy_class(data['energy_class'])
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def create_property(ctx,p):
    data=dump(p)
    sent=_fields_set(p)
    data={k:v for k,v in data.items() if k not in CENSUS_SCHEMA_FIELDS or k in sent}
    _check_catalog({k:v for k,v in data.items() if k in sent})
    _census._check_cadastral(data)
    # CRM-OPS-3: un immobile nuovo non nasce con un incarico.
    _check_mandate_origin(data)
    # CRM-OPS-2: un agente su un immobile nuovo e' un'assegnazione.
    # DELETE-ARCH Fase 0 (D19): un `agent` crea un immobile operativo
    # assegnato a se', come `creator_assignment` fa per contatti e lead -
    # il valore e' `ctx.user_id`, mai il payload (che per un agente non puo'
    # comunque assegnare: _apply_assignment risponderebbe 403). Owner, admin
    # e platform admin in acting restano come prima: nessuna auto-assegnazione.
    if getattr(ctx,'role',None)=='agent' and not getattr(ctx,'is_platform_admin',False):
        data.pop('assigned_agent_id',None)
        data['assigned_agent_id']=ctx.user_id
        data['assigned_to']=repository.assignable_agent_name(ctx,ctx.user_id)
    else:
        _apply_assignment(ctx,data)
    return repository.create_property(ctx,data,generate_identity=True)
def list_properties(*a,**k):return repository.list_properties(*a,**k)
def get_property(ctx,i):
    # DELETE-ARCH Fase 2B1: il dettaglio operativo non mostra un immobile nel
    # Cestino (404, come un id inesistente). Trash/restore/deletion-check lo
    # leggono esplicitamente da property/lifecycle.py.
    p=repository.get_property(ctx,i)
    if _lifecycle.in_trash(p):
        from core.exceptions import NotFoundError
        raise NotFoundError(f'property {i} not found')
    # EDIFICI-1: l'edificio di appartenenza (relazione reale `building_id`,
    # nella stessa agenzia), per il collegamento evidente nella scheda.
    if p.get('building_id') is not None:
        p['building']=_census.building_summary(p['agency_id'],p['building_id'])
    return p
def update_property(ctx,i,p):
    data=dump(p,True)
    # CRM-OPS-2: il codice e' stabile. Un codice vuoto o nullo in modifica
    # (property_admin lo rimanda null se il campo e' vuoto) non lo cancella.
    if 'code' in data and not (data['code'] or '').strip():
        del data['code']
    current=None
    if {'assigned_agent_id','assigned_to','region','energy_class','commercial_status',*MANDATE_FIELDS,*ADDRESS_FIELDS} & set(data):
        current=repository.get_property(ctx,i)
        # DELETE-ARCH Fase 2B1: nel Cestino nessuna modifica, prima di ogni
        # altra regola (la guardia vera e' nel repository, sotto FOR UPDATE).
        _lifecycle.refuse_if_in_trash(current)
    # DELETE-ARCH Fase 0, review 2: nessun bypass di /archive e /unarchive.
    # Un `archived` invariato su un immobile gia' archiviato (property_admin
    # rimanda lo stato a ogni salvataggio) e' un no-op e si toglie.
    _lifecycle.check_status_patch(data,current)
    if data.get('commercial_status')=='archived' and current and current.get('commercial_status')=='archived':
        del data['commercial_status']
    _check_catalog(data,current)
    _census._check_cadastral(data)
    _check_census_guard(data,current)
    _check_inherited_address(data,current)
    _check_mandate_origin(data,current)
    if 'assigned_agent_id' in data:
        _apply_assignment(ctx,data,current.get('assigned_agent_id'))
    if ('assigned_to' in data and 'assigned_agent_id' not in data
            and current.get('assigned_agent_id') is not None):
        # CRM-OPS-2: con un agente assegnato per ID, l'ID e' la fonte
        # autorevole e il nome e' la sua istantanea scritta dal server.
        # property_admin rimanda a ogni salvataggio il testo "Assegnato a":
        # lo si ignora, cosi' il nome non puo' divergere dall'agente reale e
        # gli altri campi si salvano comunque. Cambiare agente resta possibile
        # solo inviando assigned_agent_id (permessi + membro attivo).
        # Senza ID il testo libero storico resta modificabile come prima.
        del data['assigned_to']
    return repository.update_property(ctx,i,data,derive_identity=True)
# DELETE-ARCH Fase 0: archivia/riattiva, rimozione collegamenti ed eliminazione
# dei figli passano da property/lifecycle.py (accesso D10, guardie, audit).
def archive_property(ctx,i):return _lifecycle.archive_property(ctx,i)
def unarchive_property(ctx,i):return _lifecycle.unarchive_property(ctx,i)
# DELETE-ARCH Fase 2B1: Cestino Immobili (property/lifecycle.py).
def deletion_check(ctx,i):return _lifecycle.deletion_check(ctx,i)
def trash_property(ctx,i,p):return _lifecycle.trash_property(ctx,i,p.reason_code,p.note)
def restore_property(ctx,i):return _lifecycle.restore_property(ctx,i)
# DELETE-ARCH Fase 2B3: l'elenco del Cestino (pagina «Cestino» della Shell).
def list_trash(ctx,limit,offset):return _lifecycle.list_trash(ctx,limit=limit,offset=offset)
def add_contact(ctx,i,p):return repository.add_contact(ctx,i,dump(p))
def delete_contact(ctx,i,c,r):return _lifecycle.delete_contact(ctx,i,c,r)
def add_lead(ctx,i,p):return repository.add_lead(ctx,i,dump(p))
def delete_lead(ctx,i,l):return _lifecycle.delete_lead(ctx,i,l)
def add_document(ctx,i,p):return repository.create_child(ctx,'property_documents',i,dump(p))
def update_document(ctx,i,p):return repository.update_child(ctx,'property_documents',i,dump(p,True),'document')
def delete_document(ctx,i):return _lifecycle.delete_child(ctx,'property_documents',i)
def add_photo(ctx,i,p):return repository.create_child(ctx,'property_photos',i,dump(p))
def update_photo(ctx,i,p):return repository.update_child(ctx,'property_photos',i,dump(p,True),'photo')
def delete_photo(ctx,i):return _lifecycle.delete_child(ctx,'property_photos',i)
def list_visits(*a,**k):return repository.list_visits(*a,**k)
def list_visits_by_contact(*a,**k):return repository.list_visits_by_contact(*a,**k)
def add_visit(ctx,i,p):return repository.add_visit(ctx,i,dump(p))
def update_visit(ctx,i,p):return repository.update_visit(ctx,i,dump(p,True))
def delete_visit(ctx,i):return _lifecycle.delete_child(ctx,'property_visits',i)
def dashboard(ctx):return repository.dashboard(ctx)
def alerts(ctx):return repository.alerts(ctx)
