"""STIMA-CRM-AGENDA-1 - la richiesta di sopralluogo del modulo pubblico entra
nell'Agenda da sola, appena salvata.

COSA CAMBIA RISPETTO AD A30-7 (D2)

A30-7 aveva deciso "solo su richiesta: nessun hook nel form pubblico". Da
qui quella decisione e' superata per il SOLO salvataggio riuscito di una
riga `stime_dettagliate` con `sopralluogo`: `POST /api/salva_stima_dettagliata`
(`main._save_detail_submission`), dopo il COMMIT della riga, chiama
`safe_import_for_detail(<id>)`. La sincronizzazione esplicita dall'Agenda
(`POST /api/appointments/legacy-requests/sync`) resta il ripiego per tutto
cio' che questo aggancio non ha portato.

UNA SOLA RICHIESTA APERTA PER STIMA

L'aggancio (e la sincronizzazione manuale) chiamano l'import con
`una_aperta_per_stima=True`: la regola e' quella che l'Agenda applica gia'
quando si fissa un sopralluogo (`lock_stima` + `open_inspection_for_stima`
con `state_machine.OPEN_STATUSES`). Se la stima ha gia' un sopralluogo
aperto, la dettagliata resta salvata e nessuna seconda richiesta nasce
(contata in `open_request_exists`); se il precedente e' terminale, la
richiesta nuova si importa.

NESSUNA SECONDA LOGICA AGENDA

Qui non si costruisce nessun appuntamento. Si chiama `run_import` di A30-6
con due filtri gia' suoi: l'agenzia del record e il record stesso
(`record_id`). Stesse regole (requested, mai scheduled; ora a parete
Europe/Rome; orfani esclusi; lead solo se unico; property NULL), stessa
chiave `('legacy_stime_dettagliate', 'stime_dettagliate:<id>')`, stesso
`INSERT ... ON CONFLICT (source, source_record_id) DO NOTHING` attraverso
`appointments.repository.insert_appointment`. L'indice unico della 072
decide anche fra questo aggancio e una sincronizzazione manuale
contemporanea: al massimo UNA riga per record, zero eventi per chi perde.
Google Calendar non c'entra: l'Agenda lo coinvolge solo da `scheduled` in
poi, secondo le sue regole.

FAIL-OPEN

La riga `stime_dettagliate` e' gia' committata quando si arriva qui: un
problema dell'Agenda non deve diventare un errore per il cliente ne' una
ricevuta "parziale" da riprendere. Qualunque eccezione viene registrata nel
log applicativo (id del record, classe dell'errore, contatori: nessun dato
personale) e inghiottita; il record resta importabile con la
sincronizzazione manuale. Per lo stesso motivo questa funzione NON e' un
passo della ricevuta F04.

Transazione propria (come `router.sync_for_session`): nessuna connessione
del chiamante, commit solo a import riuscito, rollback altrimenti.
"""
from __future__ import annotations

import logging

from psycopg2.extras import RealDictCursor

from database import get_connection

from . import stime_dettagliate_import as legacy

log = logging.getLogger(__name__)

#: Le categorie che l'import scarta (le stesse del router), per il log.
ESCLUSE = ("orphan", "dst_nonexistent", "dst_ambiguous", "missing_agency", "errors")


def safe_import_for_detail(detail_id, *, connection_factory=None):
    """Porta nell'Agenda la richiesta del SOLO record `stime_dettagliate`
    `detail_id`, se ha un sopralluogo ed e' idoneo. Non solleva mai.

    Ritorna il report di `run_import` (contatori, `inserted_ids`) oppure
    None se l'aggancio non ha potuto lavorare (record assente, errore).
    """
    try:
        record_id = int(detail_id)
    except (TypeError, ValueError):
        log.warning("legacy_detail_auto_import_skipped reason=invalid_detail_id")
        return None
    factory = connection_factory if connection_factory is not None else get_connection
    conn = None
    try:
        conn = factory()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            # L'agenzia e' quella scritta sul record: il filtro di tenant e' il
            # suo, non uno scelto qui. Senza agenzia l'import conta
            # `missing_agency` e non inserisce nulla (D3).
            cur.execute("SELECT agency_id FROM stime_dettagliate WHERE id = %s", (record_id,))
            riga = cur.fetchone()
            if riga is None:
                conn.rollback()
                log.warning("legacy_detail_auto_import_skipped detail_id=%s reason=detail_not_found",
                            record_id)
                return None
            esito = legacy.run_import(cur, apply=True, agency_id=riga["agency_id"],
                                      record_id=record_id, una_aperta_per_stima=True)
            conn.commit()
        finally:
            try:
                cur.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001 - fail-open: la dettagliata e' gia' salvata
        if conn is not None:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
        log.warning("legacy_detail_auto_import_failed detail_id=%s error_type=%s",
                    record_id, type(exc).__name__)
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    # Un record scartato o un INSERT fallito nel suo SAVEPOINT non e' un
    # errore dell'aggancio: e' committato come tale e resta per il ripiego
    # manuale, ma va letto nel log con il suo motivo.
    scartati = sum(esito[k] for k in ESCLUSE)
    livello = log.warning if scartati else log.info
    livello("legacy_detail_auto_import detail_id=%s agency_id=%s with_sopralluogo=%s inserted=%s "
            "already_imported=%s open_request_exists=%s excluded=%s error_kinds=%s",
            record_id, esito["agency_id"], esito["with_sopralluogo"], esito["inserted"],
            esito["already_imported"], esito["open_request_exists"], scartati,
            sorted({d.get("kind") for d in esito["error_details"]}))
    return esito
