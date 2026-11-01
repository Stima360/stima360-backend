"""Riconoscimento del numero WhatsApp nella rubrica Contatti: UN solo punto.

Regole:
- si confronta il numero intero in formato internazionale (`core.normalization
  .normalize_phone`: `+39`, `0039`, `39`, spazi, trattini e numeri nazionali
  diventano lo stesso valore), mai le ultime cifre o una parte;
- si guardano TUTTI i campi telefonici dello schema `contacts`: `phone`
  (tramite `phone_normalized`, ricalcolato dal valore grezzo se manca) e
  `secondary_phone` (normalizzato in lettura, non ha una colonna normalizzata);
- solo i contatti vivi della STESSA agenzia;
- con UN contatto si collega quello; con PIU' contatti si collega da solo il
  contatto ATTIVO (`status = 'active'`, non eliminato) creato per primo
  (`created_at` piu' vecchio, a parita' l'id piu' basso): gli altri restano
  candidati e l'operatore puo' cambiare la scelta; se nessuno e' attivo si
  resta in `ambiguous` e sceglie l'operatore; senza contatti si resta in
  `none` (l'operatore puo' crearlo);
- un'associazione manuale precedente della stessa conversazione vince sempre
  su quella automatica e si eredita sui messaggi successivi;
- e' il comportamento PREDEFINITO, senza azioni dell'operatore: all'arrivo di
  un messaggio (anche per i messaggi precedenti della stessa conversazione) e
  all'apertura della casella (riconciliazione dell'agenzia, idempotente);
- il numero non collega mai da solo un immobile o una stima.
"""
from __future__ import annotations

from core.normalization import normalize_phone

from .enums import MATCH_AMBIGUOUS, MATCH_MANUAL, MATCH_MATCHED, MATCH_NONE

_LIVE_CONTACT = "(to_jsonb(c)->>'deleted_at') IS NULL"

# Stessa regola di core.normalization.normalize_phone, in SQL: cifre, via il
# `00` iniziale, prefisso 39 ai numeri nazionali (senza lo 0 iniziale).
_DIGITS = "regexp_replace(coalesce({col}, ''), '\\D', '', 'g')"
_NO_00 = "regexp_replace(" + _DIGITS + ", '^00', '')"
_NORMALIZED = ("(CASE WHEN " + _NO_00 + " = '' THEN NULL WHEN " + _NO_00 + " LIKE '39%%' THEN " + _NO_00
               + " ELSE '39' || ltrim(" + _NO_00 + ", '0') END)")

MATCH_SQL = ("(coalesce(c.phone_normalized, " + _NORMALIZED.format(col="c.phone") + ") = %(phone)s "
             "OR " + _NORMALIZED.format(col="c.secondary_phone") + " = %(phone)s)")


def canonical(phone: str | None) -> str | None:
    return normalize_phone(phone)


def candidates(cur, agency_id: int, phone: str, *, limit: int = 20) -> list[dict]:
    """I contatti vivi dell'agenzia con quel numero (intero) in uno dei campi
    telefonici. Ordinati per id: l'ordine non decide mai nulla."""
    canon = canonical(phone)
    if not canon:
        return []
    cur.execute(
        "SELECT c.id, c.display_name, c.first_name, c.last_name, c.status, c.created_at, "
        "(coalesce(c.phone_normalized, " + _NORMALIZED.format(col="c.phone") + ") = %(phone)s) AS on_phone "
        f"FROM contacts c WHERE c.agency_id = %(agency)s AND {_LIVE_CONTACT} AND {MATCH_SQL} "
        "ORDER BY c.created_at, c.id LIMIT %(limit)s",
        {"phone": canon, "agency": agency_id, "limit": limit})
    return [dict(r) for r in cur.fetchall()]


def automatic_choice(found: list[dict]) -> dict | None:
    """Il contatto da collegare da solo fra i candidati (gia' ordinati per
    `created_at`, id): l'unico, oppure il primo ATTIVO. None se non c'e'."""
    if len(found) == 1:
        return found[0]
    return next((c for c in found if c.get("status") == "active"), None)


def previous_manual_link(cur, agency_id: int, sender_phone: str) -> dict | None:
    """L'ultima associazione MANUALE della conversazione, se il contatto e'
    ancora vivo: vince sull'automatismo e si eredita."""
    cur.execute(
        "SELECT m.contact_id, m.linked_by_user_id, m.linked_at FROM whatsapp_inbound_messages m "
        "JOIN contacts c ON c.id = m.contact_id AND c.agency_id = m.agency_id "
        f"WHERE m.agency_id = %s AND m.sender_phone = %s AND m.contact_match = '{MATCH_MANUAL}' "
        f"AND m.contact_id IS NOT NULL AND {_LIVE_CONTACT} ORDER BY m.linked_at DESC NULLS LAST, m.id DESC LIMIT 1",
        (agency_id, sender_phone))
    row = cur.fetchone()
    return dict(row) if row else None


def resolve(cur, agency_id: int, sender_phone: str) -> dict:
    """{contact_id, match, linked_by_user_id, linked_at} per un nuovo messaggio."""
    manual = previous_manual_link(cur, agency_id, sender_phone)
    if manual is not None:
        return {"contact_id": manual["contact_id"], "match": MATCH_MANUAL,
                "linked_by_user_id": manual["linked_by_user_id"], "linked_at": manual["linked_at"]}
    found = candidates(cur, agency_id, sender_phone)
    chosen = automatic_choice(found)
    if chosen is not None:
        return {"contact_id": chosen["id"], "match": MATCH_MATCHED, "linked_by_user_id": None, "linked_at": None}
    return {"contact_id": None, "match": MATCH_AMBIGUOUS if found else MATCH_NONE,
            "linked_by_user_id": None, "linked_at": None}


def backfill_conversation(cur, agency_id: int, sender_phone: str, decided: dict) -> int:
    """I messaggi PRECEDENTI della stessa conversazione ancora senza contatto e
    non decisi a mano prendono il contatto appena deciso. Ritorna quanti."""
    if decided["contact_id"] is None:
        return 0
    cur.execute(
        "UPDATE whatsapp_inbound_messages SET contact_id = %s, contact_match = %s, linked_by_user_id = %s, "
        "linked_at = %s WHERE agency_id = %s AND sender_phone = %s AND routing_status = 'routed' "
        "AND contact_id IS NULL AND contact_match IN (%s, %s) RETURNING id",
        (decided["contact_id"], decided["match"], decided["linked_by_user_id"], decided["linked_at"],
         agency_id, sender_phone, MATCH_NONE, MATCH_AMBIGUOUS))
    return len(cur.fetchall())


def reconcile(cur, agency_id: int) -> dict:
    """Riconciliazione delle conversazioni gia' ricevute: i messaggi instradati
    dell'agenzia SENZA contatto e non decisi a mano (`none`/`ambiguous`)
    prendono il contatto scelto dalla stessa regola della ricezione (unico,
    o il piu' vecchio attivo), o ereditano l'associazione manuale della
    conversazione. `manual` (anche lo
    scollegamento manuale) non si tocca. Immobili e stime non cambiano."""
    cur.execute("SELECT DISTINCT sender_phone FROM whatsapp_inbound_messages WHERE agency_id = %s "
                "AND routing_status = 'routed' AND contact_id IS NULL AND contact_match IN (%s, %s) ORDER BY 1",
                (agency_id, MATCH_NONE, MATCH_AMBIGUOUS))
    senders = [r["sender_phone"] for r in cur.fetchall()]
    out = {"conversations": len(senders), "linked_conversations": 0, "linked_messages": 0,
           "ambiguous_conversations": 0, "unmatched_conversations": 0}
    for sender in senders:
        decided = resolve(cur, agency_id, sender)
        if decided["contact_id"] is not None:
            out["linked_conversations"] += 1
            out["linked_messages"] += backfill_conversation(cur, agency_id, sender, decided)
        elif decided["match"] == MATCH_AMBIGUOUS:
            cur.execute("UPDATE whatsapp_inbound_messages SET contact_match = %s WHERE agency_id = %s "
                        "AND sender_phone = %s AND contact_id IS NULL AND contact_match = %s",
                        (MATCH_AMBIGUOUS, agency_id, sender, MATCH_NONE))
            out["ambiguous_conversations"] += 1
        else:
            out["unmatched_conversations"] += 1
    return out
