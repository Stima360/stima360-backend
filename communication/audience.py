"""Fase B - LA PLATEA della journey sul contatto: chi puo' entrare, oggi.

Nessuna anagrafica nuova: la platea e' una lettura di `contacts` con le
esclusioni che il CRM gia' conosce, piu' la dedup per indirizzo. Qui si
PREFILTRA e si ordina; la decisione sul consenso la prende la guardia, nel
tick, contatto per contatto (il prefiltro usa il predicato che il pacchetto
del consenso fornisce, e non conosce le sue colonne).

Chi NON entra, per costruzione:
  * (V3) chi non e' proprietario/venditore VERIFICATO: vedi
    `PROPRIETARIO_VERIFICATO` (acquirenti e richiedenti dal sito senza ruolo
    registrato da un operatore restano fuori);
  * contatti non attivi, nel Cestino, archiviati, senza email scrivibile;
  * chi e' gia' in una sequenza aperta (indice unico per contatto) o in pausa
    manuale (`communication_automation_controls`);
  * chi ha gia' COMPLETATO un ciclo con questo indirizzo, in qualunque
    agenzia: una sola sequenza per destinatario, nessuna ripartenza; chi e'
    stato fermato rientra solo con una concessione NUOVA, successiva allo stop;
  * chi ha detto "basta STIMA360" (lista di piattaforma) DOPO la sua
    concessione: una concessione nuova, documentata e successiva all'opt-out
    rientra per quell'agenzia soltanto (la guardia lo conferma riga per riga);
  * chi e' nel programma di recupero P24 (cooldown): due programmi non si
    sovrappongono;
  * lo stesso indirizzo due volte: nella stessa agenzia resta UN contatto
    (quello con la concessione piu' recente); fra agenzie, chi ha gia' una
    sequenza aperta altrove esclude gli altri.

L'ordine e' dal consenso piu' recente al piu' vecchio: a parita' di tetto
giornaliero, prima chi ha detto si' da poco.

LA SOLA LETTURA FUORI DALLO SCOPE di agenzia e' la dedup di piattaforma
(`emails_with_open_enrollment_anywhere`, `emails_with_recent_cycle_anywhere`):
confinata nel repository, nominata per quello che e', e sorvegliata da una
sentinella. Nessun dato di un'altra agenzia esce da qui: solo "questo
indirizzo e' gia' preso".
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from consent.audience import (channel_granted_at_sql, channel_optout_after_grant_sql,
                              channel_prefilter_sql)

from . import journey_repository as repo
from .followup_rules import email_valida

#: Quanti contatti STORICI una journey ammette al giorno, per agenzia
#: (decisione del proprietario: ammissione graduale, mai invii di massa).
TETTO_GIORNALIERO = 20
#: 109: la journey sul contatto manda EMAIL: la platea e' chi ha concesso
#: QUEL canale. Il vecchio flag di consenso del contatto non entra nel prefiltro.
CANALE = "email"
#: Un consenso piu' recente di cosi' e' "futuro": il calendario parte dalla
#: data del consenso. Piu' vecchio e' "storico": parte dall'ammissione.
FINESTRA_CONSENSO_RECENTE = timedelta(days=7)
#: Quanti contatti leggere per trovare il lotto del giorno: il prefiltro e'
#: largo, la guardia e la dedup scremano.
LETTURA = 200


#: V3: la sequenza e' SOLO per proprietari/venditori con ruolo VERIFICATO,
#: cioe' registrato da un operatore: il ruolo `owner`/`seller` del contatto
#: valido adesso, oppure il contatto collegato come `owner`/`seller` a un
#: immobile della stessa agenzia non nel Cestino. La sola richiesta di
#: valutazione dal sito NON basta (il sito collega il contatto all'immobile
#: come `contact`): acquirenti e contatti senza ruolo verificato non entrano.
PROPRIETARIO_VERIFICATO = """(EXISTS (SELECT 1 FROM contact_roles r
                     WHERE r.contact_id = c.id AND r.role IN ('owner', 'seller')
                       AND (r.valid_from IS NULL OR r.valid_from <= %s)
                       AND (r.valid_to IS NULL OR r.valid_to >= %s))
            OR EXISTS (SELECT 1 FROM property_contacts pc JOIN properties p ON p.id = pc.property_id
                        WHERE pc.contact_id = c.id AND pc.role IN ('owner', 'seller')
                          AND p.agency_id = c.agency_id AND p.deleted_at IS NULL))"""


def candidates(cur, ctx, *, now: datetime, limit: int) -> list[dict[str, Any]]:
    """I candidati di oggi, gia' deduplicati per indirizzo, al massimo `limit`.

    Ogni voce: `contact_id`, `email_normalized`, `granted_at`. La guardia del
    consenso NON e' ancora stata interrogata: lo fa il tick, nel fence.
    """
    if limit <= 0:
        return []
    agenzia = ctx.require_agency()
    cur.execute(
        f"""SELECT c.id AS contact_id, c.email_normalized, {channel_granted_at_sql('c', CANALE)} AS granted_at
              FROM contacts c
             WHERE c.agency_id = %s
               AND c.status = 'active' AND c.deleted_at IS NULL AND c.archived_at IS NULL
               AND c.email_normalized IS NOT NULL
               AND {channel_prefilter_sql('c', CANALE)}
               AND NOT EXISTS (SELECT 1 FROM communication_enrollments e
                                WHERE e.agency_id = c.agency_id AND e.contact_id = c.id
                                  AND e.status IN ('active', 'paused'))
               AND NOT EXISTS (SELECT 1 FROM communication_enrollments e
                                WHERE e.agency_id = c.agency_id AND e.contact_id = c.id
                                  AND e.email_normalized_snapshot IS NOT NULL
                                  AND (e.status = 'completed'
                                       OR (e.status = 'stopped'
                                           AND e.stopped_at >= COALESCE({channel_granted_at_sql('c', CANALE)}, e.stopped_at))))
               AND NOT EXISTS (SELECT 1 FROM communication_automation_controls k
                                WHERE k.agency_id = c.agency_id AND k.contact_id = c.id AND k.paused)
               AND NOT {channel_optout_after_grant_sql('c', CANALE)}
               AND NOT EXISTS (SELECT 1 FROM seller_revival_suppressions s
                                WHERE s.contact_id = c.id AND s.expires_at > %s)
               AND {PROPRIETARIO_VERIFICATO}
             ORDER BY {channel_granted_at_sql('c', CANALE)} DESC NULLS LAST, c.id
             LIMIT %s""",
        (agenzia, now, now, now, LETTURA))
    righe = [dict(r) for r in cur.fetchall()]

    # dedup dentro l'agenzia: una email, un contatto (la concessione piu' recente vince)
    per_email: dict[str, dict[str, Any]] = {}
    for r in righe:
        if not email_valida(r["email_normalized"]):
            continue
        if r["email_normalized"] not in per_email:
            per_email[r["email_normalized"]] = r
    emails = list(per_email)
    # dedup di piattaforma: una email, una sequenza, ovunque
    prese = repo.emails_with_open_enrollment_anywhere(cur, emails)
    recenti = repo.emails_with_completed_cycle_anywhere(cur, emails)
    scelti = [r for e, r in per_email.items() if e not in prese and e not in recenti]
    return scelti[:limit]


def momento_iniziale(granted_at: datetime | None, now: datetime) -> datetime:
    """Il giorno zero del calendario per questo contatto.

    Futuro (consenso recente): la data del consenso, un fatto certo. Storico:
    l'ammissione di adesso. Mai una richiesta o una data inventata.
    """
    if granted_at is not None and now - granted_at <= FINESTRA_CONSENSO_RECENTE:
        return granted_at
    return now


def tetto_residuo(cur, ctx, journey_id: int, now: datetime) -> int:
    return max(0, TETTO_GIORNALIERO - repo.admitted_today(cur, ctx, journey_id, now))
