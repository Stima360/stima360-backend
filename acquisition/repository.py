"""LMC-15 - le scritture del ponte, tutte scopate e tutte atomiche.

TRE REGOLE CHE VALGONO PER OGNI FUNZIONE DI QUESTO FILE.

L'AGENZIA E' IL PRIMO PARAMETRO e non ha default: arriva sempre da
`ctx.require_agency()`, mai dal client. Ogni predicato la nomina accanto alla
stima o alla property, perche' un id da solo non e' un tenant.

L'ATTORE E' UN PARAMETRO, non un campo del corpo: chi chiama lo prende dalla
sessione. Il database lo riverifica comunque (trigger della 070), ma la prima
difesa e' che non esiste un percorso in cui il client lo scelga.

IL FATTO E LA SUA PROIEZIONE SONO UNA TRANSAZIONE SOLA. `seller_timeline_events`
e' una PROIEZIONE, non la fonte: se la scrittura dell'evento fallisce, fallisce
anche il fatto. Per questo la riga della timeline si scrive con lo STESSO
cursore, attraverso le due funzioni gia' certificate di Seller Intelligence -
quella che verifica ogni riferimento contro l'agenzia e quella che inserisce -
invece che da `record_event_scoped`, che aprirebbe una connessione sua e
lascerebbe i due fatti liberi di divergere. E' lo stesso motivo per cui
`integration_owner_request` ha una variante `*_with_cursor`.
"""
from __future__ import annotations

from typing import Any

from core.database import core_cursor
from core.exceptions import ConflictError, NotFoundError, ValidationError
from seller_intelligence import repository as si_repository

#: Gli eventi che il ponte proietta. Insieme CHIUSO.
LINKED_EVENT = "acquisition_linked"
MANDATE_EVENT = "mandate_signed"
REVOKED_EVENT = "acquisition_revoked"
INSPECTION_SCHEDULED_EVENT = "inspection_scheduled"
INSPECTION_COMPLETED_EVENT = "inspection_completed"
INSPECTION_CANCELLED_EVENT = "inspection_cancelled"
PROJECTED_EVENTS = (LINKED_EVENT, MANDATE_EVENT, REVOKED_EVENT,
                    INSPECTION_SCHEDULED_EVENT, INSPECTION_COMPLETED_EVENT,
                    INSPECTION_CANCELLED_EVENT)

EVENT_SOURCE = "crm_acquisition"

#: Le colonne che escono verso il servizio. Elenco CHIUSO: `stima_id_snapshot`
#: non c'e' perche' e' interno, e nessuna API lo espone.
ACQUISITION_COLUMNS = (
    "id", "stima_id", "property_id", "link_status", "linked_at",
    "mandate_signed_at", "mandate_recorded_at", "mandate_reference",
    "revoked_at", "revoked_reason",
)
INSPECTION_COLUMNS = (
    "id", "stima_id", "status", "scheduled_for", "completed_at", "cancelled_at",
    "cancelled_reason",
)


def _proietta(cur, *, agency_id, event_type, stima_id, property_id, payload,
              idempotency_key, created_by, occurred_at):
    """La riga di timeline, nello stesso cursore del fatto.

    `occurred_at` arriva dalla RIGA appena scritta, non da un `now()` chiesto
    una seconda volta: il fatto e la sua proiezione portano cosi' lo stesso
    istante, e non due istanti vicini che qualcuno dovra' spiegare.

    Ed e' l'istante del FATTO, non quello della registrazione, dove i due
    sono distinti: un sopralluogo avvenuto a marzo e scritto ad aprile sta
    sulla timeline a marzo, che e' la sola data che racconti la storia del
    venditore. Il momento della registrazione resta nella tabella del ponte,
    per chi deve fare l'audit.
    """
    dati = {"contact_id": None, "lead_id": None, "stima_id": stima_id,
            "property_id": property_id, "event_type": event_type,
            "event_source": EVENT_SOURCE, "occurred_at": occurred_at,
            "payload": payload, "idempotency_key": idempotency_key,
            "created_by": created_by}
    si_repository._assert_references_in_agency(cur, dati, agency_id)
    si_repository._insert_event_with_agency(cur, dati, agency_id)


def _riga(cur, colonne):
    r = cur.fetchone()
    if r is None:
        raise NotFoundError("Risorsa non trovata")
    return {c: r[c] for c in colonne}


# ---------------------------------------------------------------------------
# IL LINK
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# P29-3C - IL FENCE SULLA STIMA
#
# Questi scrittori producono i FATTI che fermano una journey: un incarico
# firmato, un link di acquisizione, un sopralluogo. Il motore delle journey
# (P29-3C) decide se far partire un messaggio tenendo bloccate contatto,
# lead e stima, e rileggendo i fatti con quelle righe in mano.
#
# Perche' quel contratto valga servono DUE meta': chi legge prende il lock, e
# chi scrive lo rispetta. Queste funzioni prendono `stime` FOR UPDATE nella
# STESSA transazione della scrittura, prima di scrivere. Da li' in poi solo
# uno dei due passa alla volta:
#
#   se lo stop ha committato prima del lock del motore, il motore lo vede;
#   se il lock lo ha preso prima il motore, questa scrittura ASPETTA il suo
#   commit, e il messaggio che il motore stava per accodare e' partito con
#   una decisione che al suo istante era vera.
#
# Nessuna semantica di questo dominio cambia: nessun predicato nuovo, nessun
# campo nuovo, nessun errore nuovo. Si aggiunge solo il coordinamento.
#
# ORDINE DEI LOCK: qui si prende SOLO la stima. Il motore prende contatto,
# poi lead, poi stima; il consenso prende solo il contatto. Ogni percorso
# prende un sottoinsieme nello stesso ordine relativo, quindi nessun ciclo
# di attesa puo' formarsi.
# ---------------------------------------------------------------------------

def _blocca_stima(cur, agency_id, stima_id):
    """`stime` FOR UPDATE, dentro l'agenzia. Una stima assente non e' un
    errore qui: lo diranno i predicati della scrittura, come hanno sempre
    fatto."""
    if stima_id is None:
        return
    cur.execute("SELECT id FROM stime WHERE id = %s AND agency_id = %s FOR UPDATE",
                (stima_id, agency_id))


def _blocca_stima_dell_acquisizione(cur, agency_id, acquisition_id):
    """La stima del link, bloccata prima di toccarlo. Passa dalla PROPERTY
    come il predicato della revoca: dopo un hard delete della stima
    `a.stima_id` e' NULL, e non c'e' niente da bloccare."""
    cur.execute(
        """SELECT a.stima_id FROM stima_acquisitions a
             JOIN properties p ON p.id = a.property_id
            WHERE a.id = %s AND p.agency_id = %s""",
        (acquisition_id, agency_id))
    riga = cur.fetchone()
    if riga is not None:
        _blocca_stima(cur, agency_id, riga["stima_id"])


def _blocca_stima_del_sopralluogo(cur, agency_id, inspection_id):
    cur.execute(
        """SELECT i.stima_id FROM stima_inspections i
             JOIN stime s ON s.id = i.stima_id
            WHERE i.id = %s AND s.agency_id = %s""",
        (inspection_id, agency_id))
    riga = cur.fetchone()
    if riga is not None:
        _blocca_stima(cur, agency_id, riga["stima_id"])


def create_acquisition_link(agency_id, *, stima_id, property_id, actor_user_id):
    """Collega una property alla stima da cui nasce.

    L'INSERT e' un `INSERT ... SELECT` da `stime` e `properties`: se una
    delle due non e' di questa agenzia non seleziona niente, e non c'e'
    nessun ramo in cui il tenant arrivi da altrove. Il trigger della 070 e'
    la seconda difesa, e l'indice unico parziale la terza.
    """
    with core_cursor(commit=True) as (_, cur):
        _blocca_stima(cur, agency_id, stima_id)
        cur.execute(
            """
            INSERT INTO stima_acquisitions
                   (stima_id, stima_id_snapshot, property_id, linked_by_operator_user_id)
            SELECT s.id, s.id, p.id, %s
              FROM stime s JOIN properties p ON p.agency_id = s.agency_id
             WHERE s.id = %s AND p.id = %s AND s.agency_id = %s
            RETURNING *
            """,
            (actor_user_id, stima_id, property_id, agency_id),
        )
        riga = cur.fetchone()
        if riga is None:
            raise NotFoundError("Risorsa non trovata")
        _proietta(cur, agency_id=agency_id, event_type=LINKED_EVENT,
                  stima_id=riga["stima_id"], property_id=riga["property_id"],
                  payload={"acquisition_id": riga["id"]},
                  idempotency_key=f"lmc15:v1:{LINKED_EVENT}:acq:{riga['id']}",
                  created_by=str(actor_user_id), occurred_at=riga["linked_at"])
        return {c: riga[c] for c in ACQUISITION_COLUMNS}


def record_mandate(agency_id, *, acquisition_id, signed_at, reference, actor_user_id):
    """Registra la firma dell'incarico su un link ATTIVO.

    Un mandato gia' registrato non si sovrascrive in silenzio: il predicato
    esige `mandate_signed_at IS NULL`, e il chiamante distingue "non trovato"
    da "gia' registrato" rileggendo la riga.
    """
    with core_cursor(commit=True) as (_, cur):
        _blocca_stima_dell_acquisizione(cur, agency_id, acquisition_id)
        cur.execute(
            """
            UPDATE stima_acquisitions a
               SET mandate_signed_at = %s,
                   mandate_recorded_at = NOW(),
                   mandate_recorded_by_operator_user_id = %s,
                   mandate_reference = %s,
                   updated_at = NOW()
              FROM stime s, properties p
             WHERE a.id = %s
               AND a.link_status = 'active'
               AND a.mandate_signed_at IS NULL
               AND p.id = a.property_id
               AND s.id = a.stima_id
               AND s.agency_id = %s
               AND p.agency_id = s.agency_id
            RETURNING a.*
            """,
            (signed_at, actor_user_id, reference, acquisition_id, agency_id),
        )
        riga = cur.fetchone()
        if riga is None:
            _rifiuto_mandato(cur, agency_id, acquisition_id)
        _proietta(cur, agency_id=agency_id, event_type=MANDATE_EVENT,
                  stima_id=riga["stima_id"], property_id=riga["property_id"],
                  payload={"acquisition_id": riga["id"]},
                  idempotency_key=f"lmc15:v1:{MANDATE_EVENT}:acq:{riga['id']}",
                  created_by=str(actor_user_id),
                  occurred_at=riga["mandate_signed_at"])
        return {c: riga[c] for c in ACQUISITION_COLUMNS}


def _rifiuto_mandato(cur, agency_id, acquisition_id):
    """Perche' l'UPDATE non ha trovato niente. Solleva sempre."""
    cur.execute(
        """SELECT a.link_status, a.mandate_signed_at
             FROM stima_acquisitions a
             JOIN properties p ON p.id = a.property_id
            WHERE a.id = %s AND p.agency_id = %s""",
        (acquisition_id, agency_id),
    )
    r = cur.fetchone()
    if r is None:
        raise NotFoundError("Risorsa non trovata")
    if r["mandate_signed_at"] is not None:
        raise ConflictError("Incarico gia' registrato su questo collegamento")
    raise ConflictError("Collegamento revocato: nessun incarico registrabile")


def revoke_acquisition_link(agency_id, *, acquisition_id, reason, actor_user_id):
    """Revoca il LINK. Non tocca i campi del mandato.

    La tenancy passa dalla PROPERTY e non dalla stima: dopo un hard delete
    della stima `a.stima_id` e' NULL, e un predicato che la nominasse
    renderebbe irrevocabile proprio il link orfano.
    """
    with core_cursor(commit=True) as (_, cur):
        _blocca_stima_dell_acquisizione(cur, agency_id, acquisition_id)
        cur.execute(
            """
            UPDATE stima_acquisitions a
               SET link_status = 'revoked',
                   revoked_at = NOW(),
                   revoked_by_operator_user_id = %s,
                   revoked_reason = %s,
                   updated_at = NOW()
              FROM properties p
             WHERE a.id = %s
               AND a.link_status = 'active'
               AND p.id = a.property_id
               AND p.agency_id = %s
            RETURNING a.*
            """,
            (actor_user_id, reason, acquisition_id, agency_id),
        )
        riga = cur.fetchone()
        if riga is None:
            cur.execute(
                """SELECT a.link_status FROM stima_acquisitions a
                     JOIN properties p ON p.id = a.property_id
                    WHERE a.id = %s AND p.agency_id = %s""",
                (acquisition_id, agency_id),
            )
            if cur.fetchone() is None:
                raise NotFoundError("Risorsa non trovata")
            raise ConflictError("Collegamento gia' revocato")
        # La stima puo' non esistere piu': l'evento non la inventa, porta la
        # sola property e lo snapshot nel payload.
        _proietta(cur, agency_id=agency_id, event_type=REVOKED_EVENT,
                  stima_id=riga["stima_id"], property_id=riga["property_id"],
                  payload={"acquisition_id": riga["id"],
                           "stima_id_snapshot": riga["stima_id_snapshot"]},
                  idempotency_key=f"lmc15:v1:{REVOKED_EVENT}:acq:{riga['id']}",
                  created_by=str(actor_user_id), occurred_at=riga["revoked_at"])
        return {c: riga[c] for c in ACQUISITION_COLUMNS}


# ---------------------------------------------------------------------------
# IL SOPRALLUOGO
# ---------------------------------------------------------------------------

# A30-2 (Q1) - VARIANTI SUL CURSORE RICEVUTO.
#
# L'Agenda (A30-2P) deve scrivere il sopralluogo nella SUA transazione, con
# l'appuntamento: le funzioni `*_in(cur, ...)` fanno esattamente cio' che
# facevano le funzioni pubbliche, ma sul cursore del chiamante e SENZA
# commit. Le funzioni pubbliche sono ora un guscio che apre la transazione e
# le chiama: stesso SQL, stessi lock, stesse chiavi di idempotenza della
# timeline, stesse risposte. Una sola implementazione, niente copie.

def create_inspection(agency_id, *, stima_id, scheduled_for, actor_user_id):
    with core_cursor(commit=True) as (_, cur):
        return create_inspection_in(cur, agency_id, stima_id=stima_id,
                                    scheduled_for=scheduled_for, actor_user_id=actor_user_id)


def create_inspection_in(cur, agency_id, *, stima_id, scheduled_for, actor_user_id):
    """Come `create_inspection`, sul cursore del chiamante, senza commit."""
    _blocca_stima(cur, agency_id, stima_id)
    cur.execute(
        """
        INSERT INTO stima_inspections
               (stima_id, stima_id_snapshot, status, scheduled_for,
                created_by_operator_user_id)
        SELECT s.id, s.id, 'scheduled', %s, %s
          FROM stime s WHERE s.id = %s AND s.agency_id = %s
        RETURNING *
        """,
        (scheduled_for, actor_user_id, stima_id, agency_id),
    )
    riga = cur.fetchone()
    if riga is None:
        raise NotFoundError("Risorsa non trovata")
    _proietta(cur, agency_id=agency_id, event_type=INSPECTION_SCHEDULED_EVENT,
              stima_id=riga["stima_id"], property_id=None,
              payload={"inspection_id": riga["id"]},
              idempotency_key=f"lmc15:v1:{INSPECTION_SCHEDULED_EVENT}:insp:{riga['id']}",
              created_by=str(actor_user_id), occurred_at=riga["created_at"])
    return {c: riga[c] for c in INSPECTION_COLUMNS}


def create_completed_inspection(agency_id, *, stima_id, completed_at, actor_user_id):
    """Un sopralluogo avvenuto e mai fissato a sistema: una riga gia' chiusa.

    `scheduled_for` resta NULL, ed e' l'unico stato in cui e' ammesso: la
    matrice della 070 lo consente solo per `completed`, perche' pretendere una
    data di appuntamento mai esistita costringerebbe a inventarla.
    """
    with core_cursor(commit=True) as (_, cur):
        return create_completed_inspection_in(
            cur, agency_id, stima_id=stima_id, completed_at=completed_at,
            actor_user_id=actor_user_id)


def create_completed_inspection_in(cur, agency_id, *, stima_id, completed_at, actor_user_id):
    """Come `create_completed_inspection`, sul cursore del chiamante, senza
    commit (A30-2P: la facade LMC-15 la scrive nella transazione dell'Agenda)."""
    _blocca_stima(cur, agency_id, stima_id)
    cur.execute(
        """
        INSERT INTO stima_inspections
               (stima_id, stima_id_snapshot, status, completed_at,
                completed_recorded_at, completed_by_operator_user_id,
                created_by_operator_user_id)
        SELECT s.id, s.id, 'completed', %s, NOW(), %s, %s
          FROM stime s WHERE s.id = %s AND s.agency_id = %s
        RETURNING *
        """,
        (completed_at, actor_user_id, actor_user_id, stima_id, agency_id),
    )
    riga = cur.fetchone()
    if riga is None:
        raise NotFoundError("Risorsa non trovata")
    _proietta(cur, agency_id=agency_id, event_type=INSPECTION_COMPLETED_EVENT,
              stima_id=riga["stima_id"], property_id=None,
              payload={"inspection_id": riga["id"]},
              idempotency_key=f"lmc15:v1:{INSPECTION_COMPLETED_EVENT}:insp:{riga['id']}",
              created_by=str(actor_user_id), occurred_at=riga["completed_at"])
    return {c: riga[c] for c in INSPECTION_COLUMNS}


def complete_inspection(agency_id, *, inspection_id, completed_at, actor_user_id):
    return _chiudi_sopralluogo(
        agency_id, inspection_id=inspection_id, actor_user_id=actor_user_id,
        assegnazioni="status='completed', completed_at=%s, completed_recorded_at=NOW(), "
                     "completed_by_operator_user_id=%s, updated_at=NOW()",
        valori=(completed_at, actor_user_id),
        evento=INSPECTION_COMPLETED_EVENT, campo_quando="completed_at")


def cancel_inspection(agency_id, *, inspection_id, reason, actor_user_id):
    return _chiudi_sopralluogo(
        agency_id, inspection_id=inspection_id, actor_user_id=actor_user_id,
        assegnazioni="status='cancelled', cancelled_at=NOW(), cancelled_recorded_at=NOW(), "
                     "cancelled_by_operator_user_id=%s, cancelled_reason=%s, updated_at=NOW()",
        valori=(actor_user_id, reason),
        evento=INSPECTION_CANCELLED_EVENT, campo_quando="cancelled_at")


def complete_inspection_in(cur, agency_id, *, inspection_id, completed_at, actor_user_id):
    """Come `complete_inspection`, sul cursore del chiamante, senza commit."""
    return _chiudi_sopralluogo_in(
        cur, agency_id, inspection_id=inspection_id, actor_user_id=actor_user_id,
        assegnazioni="status='completed', completed_at=%s, completed_recorded_at=NOW(), "
                     "completed_by_operator_user_id=%s, updated_at=NOW()",
        valori=(completed_at, actor_user_id),
        evento=INSPECTION_COMPLETED_EVENT, campo_quando="completed_at")


def cancel_inspection_in(cur, agency_id, *, inspection_id, reason, actor_user_id):
    """Come `cancel_inspection`, sul cursore del chiamante, senza commit."""
    return _chiudi_sopralluogo_in(
        cur, agency_id, inspection_id=inspection_id, actor_user_id=actor_user_id,
        assegnazioni="status='cancelled', cancelled_at=NOW(), cancelled_recorded_at=NOW(), "
                     "cancelled_by_operator_user_id=%s, cancelled_reason=%s, updated_at=NOW()",
        valori=(actor_user_id, reason),
        evento=INSPECTION_CANCELLED_EVENT, campo_quando="cancelled_at")


def reschedule_inspection_in(cur, agency_id, *, inspection_id, scheduled_for):
    """A30-2P: lo SPOSTAMENTO di un sopralluogo ancora `scheduled`.

    LMC-15 non aveva uno spostamento: lo porta l'Agenda. Aggiorna solo
    `scheduled_for`; nessun evento di timeline, perche' LMC-15 non ne
    definisce uno per lo spostamento e inventarlo cambierebbe cio' che
    journey e metriche leggono. Stesso lock sulla stima, stesso predicato di
    stato e di agenzia della chiusura.
    """
    _blocca_stima_del_sopralluogo(cur, agency_id, inspection_id)
    cur.execute(
        """
        UPDATE stima_inspections i
           SET scheduled_for = %s, updated_at = NOW()
          FROM stime s
         WHERE i.id = %s
           AND i.status = 'scheduled'
           AND s.id = i.stima_id
           AND s.agency_id = %s
        RETURNING i.*
        """,
        (scheduled_for, inspection_id, agency_id),
    )
    riga = cur.fetchone()
    if riga is None:
        cur.execute(
            """SELECT i.status FROM stima_inspections i
                 JOIN stime s ON s.id = i.stima_id
                WHERE i.id = %s AND s.agency_id = %s""",
            (inspection_id, agency_id),
        )
        r = cur.fetchone()
        if r is None:
            raise NotFoundError("Risorsa non trovata")
        raise ConflictError(f"Sopralluogo gia' {r['status']}: nessuna transizione possibile")
    return {c: riga[c] for c in INSPECTION_COLUMNS}


def _chiudi_sopralluogo(agency_id, *, inspection_id, actor_user_id, assegnazioni,
                        valori, evento, campo_quando):
    """Chiude un sopralluogo ANCORA `scheduled`: entrambi gli stati finali sono
    terminali, quindi il predicato esige lo stato di partenza."""
    with core_cursor(commit=True) as (_, cur):
        return _chiudi_sopralluogo_in(
            cur, agency_id, inspection_id=inspection_id, actor_user_id=actor_user_id,
            assegnazioni=assegnazioni, valori=valori, evento=evento,
            campo_quando=campo_quando)


def _chiudi_sopralluogo_in(cur, agency_id, *, inspection_id, actor_user_id, assegnazioni,
                           valori, evento, campo_quando):
    _blocca_stima_del_sopralluogo(cur, agency_id, inspection_id)
    cur.execute(
        f"""
        UPDATE stima_inspections i
           SET {assegnazioni}
          FROM stime s
         WHERE i.id = %s
           AND i.status = 'scheduled'
           AND s.id = i.stima_id
           AND s.agency_id = %s
        RETURNING i.*
        """,
        (*valori, inspection_id, agency_id),
    )
    riga = cur.fetchone()
    if riga is None:
        cur.execute(
            """SELECT i.status FROM stima_inspections i
                 JOIN stime s ON s.id = i.stima_id
                WHERE i.id = %s AND s.agency_id = %s""",
            (inspection_id, agency_id),
        )
        r = cur.fetchone()
        if r is None:
            raise NotFoundError("Risorsa non trovata")
        raise ConflictError(f"Sopralluogo gia' {r['status']}: nessuna transizione possibile")
    _proietta(cur, agency_id=agency_id, event_type=evento,
              stima_id=riga["stima_id"], property_id=None,
              payload={"inspection_id": riga["id"]},
              idempotency_key=f"lmc15:v1:{evento}:insp:{riga['id']}",
              created_by=str(actor_user_id), occurred_at=riga[campo_quando])
    return {c: riga[c] for c in INSPECTION_COLUMNS}
