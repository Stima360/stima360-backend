"""IMPORTAZIONE RUBRICA (migrazione 131) - il contatto RISERVATO.

Un contatto importato dalla rubrica di un operatore nasce riservato a lui
(`contacts.private_user_id`): lo vede solo lui, chiunque sia il suo ruolo.
Tutti gli altri contatti (NULL) restano dell'agenzia, come oggi.

Volutamente FUORI da `core.scope.scoped_predicate`: quel predicato e' la
regola di agenzia certificata da P26-1/P27-1 e pinned dai suoi test; questa e'
una regola in piu' per la sola tabella `contacts`, che i lettori dei contatti
(core/repository.py, contact_imports, inbound_messages/contact_match.py)
compongono con ``AND``. Letta via ``to_jsonb`` cosi' la statement resta valida
anche su un database senza la 131 (dove nessun contatto e' riservato).
"""
from __future__ import annotations


def contact_privacy_predicate(ctx, alias: str) -> tuple[str, list]:
    """``('<predicato>', params)``: il contatto `alias` e' dell'agenzia oppure
    riservato a chi chiede. Un contesto senza operatore (sistema, legacy
    Basic) non vede alcun contatto riservato."""
    user_id = getattr(ctx, "user_id", None)
    return (f"((to_jsonb({alias})->>'private_user_id') IS NULL "
            f"OR (to_jsonb({alias})->>'private_user_id')::bigint = %s)", [user_id if user_id is not None else -1])
