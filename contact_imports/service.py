"""IMPORTAZIONE RUBRICA (fase B) - Contatti → «Importa rubrica».

Il file .vcf si analizza sul server (contact_imports/vcard.py) e si mostra
un'ANTEPRIMA; i contatti si scrivono solo con «Importa tutti». Regole:

  * si conservano TUTTI i numeri di ogni scheda (contacts.phone,
    secondary_phone e contact_phones, migrazione 131) e l'email;
  * i numeri si confrontano interi, nel formato internazionale
    (core.normalization.normalize_phone), con TUTTI i campi telefonici dei
    contatti che l'importatore puo' vedere (agenzia + riservati propri);
  * una scheda con un numero gia' in rubrica NON crea un doppione: completa
    il contatto esistente (numeri mancanti, email se vuota), senza
    sovrascrivere nulla; un secondo import dello stesso file non cambia nulla;
  * un numero presente su PIU' contatti: lo decide l'operatore (unire a uno,
    creare nuovo, saltare); senza decisione la scheda si salta e lo si dice;
  * due schede del file con lo stesso numero diventano una;
  * i contatti creati nascono RISERVATI all'operatore che importa
    (contacts.private_user_id) e, se e' un agente, assegnati a lui; li
    condivide con l'agenzia solo lui («Condividi con l'agenzia»);
  * dopo la scrittura si riconciliano le conversazioni WhatsApp senza
    contatto (inbound_messages.contact_match.reconcile): quelle gia' ricevute
    si collegano subito, quelle future al loro arrivo;
  * NESSUN invio: nessuna comunicazione, follow-up o automazione parte da
    un'importazione.

RUBRICHE GRANDI. Nessun limite al numero di schede. Il file si legge a pezzi
e le foto (e ogni altra parte binaria) si scartano mentre arriva
(vcard.Stripper), cosi' conta solo il testo; l'unico limite e' la dimensione
del file inviato (`CONTACT_IMPORT_MAX_MB`, predefinito 200 MB), con un
messaggio chiaro. L'anteprima analizza tutto ma ne restituisce al browser solo
un campione (piu' tutte le voci da decidere). «Importa tutti» lavora A BLOCCHI
di `BLOCK_SIZE` schede, ciascuno nella propria transazione, con l'avanzamento
scritto nel registro (`contact_imports.summary.progress`): ogni chiamata
elabora i blocchi che stanno in `TIME_BUDGET_SECONDS` e il browser richiama
finche' non e' finita. Un'interruzione non perde nulla e non duplica nulla: si
riprende dal primo blocco non confermato e ogni scheda si riconfronta con la
rubrica di quel momento. La riconciliazione WhatsApp gira una volta, alla fine.
"""
from __future__ import annotations

import os
import time

from core.database import core_cursor
from core.normalization import normalize_email, normalize_phone
from core.scope import creator_assignment
from inbound_messages import contact_match

from . import repository, vcard

SOURCE_VCF = "vcf"
CONTACT_SOURCE = "rubrica_import"
#: Dimensione massima del file INVIATO (foto comprese), in MB. Configurabile.
MAX_FILE_MB_ENV = "CONTACT_IMPORT_MAX_MB"
DEFAULT_MAX_FILE_MB = 200
#: Testo utile conservato dopo aver scartato foto e parti binarie: oltre questo
#: non e' piu' una rubrica (centinaia di migliaia di schede).
MAX_TEXT_BYTES = 64 * 1024 * 1024
#: Schede per blocco (una transazione ciascuno) e tempo massimo di una chiamata.
BLOCK_SIZE = 500
TIME_BUDGET_SECONDS = 20.0
#: Quante schede «normali» l'anteprima restituisce al browser (tutte restano nel registro).
PREVIEW_SAMPLE = 200

STATUS_NEW = "new"
STATUS_EXISTING = "existing"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_NO_PHONE = "no_phone"

ACTION_MERGE, ACTION_CREATE, ACTION_SKIP = "merge", "create", "skip"

NOT_INSTALLED_MESSAGE = ("L'importazione della rubrica non è ancora attiva su questo ambiente: "
                         "la migrazione 131 non è stata applicata.")


class ImportError_(Exception):
    status = 400

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class NotInstalled(ImportError_):
    status = 503


class NotFound(ImportError_):
    status = 404


class Forbidden(ImportError_):
    status = 403


class Conflict(ImportError_):
    status = 409


class TooLarge(ImportError_):
    status = 413


def max_file_bytes() -> int:
    raw = (os.getenv(MAX_FILE_MB_ENV) or "").strip()
    mb = int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_MAX_FILE_MB
    return mb * 1024 * 1024


def limits() -> dict:
    """I limiti reali, per l'interfaccia: nessun limite di schede."""
    return {"max_file_mb": max_file_bytes() // (1024 * 1024), "block_size": BLOCK_SIZE, "max_entries": None}


def too_large_message(size_bytes: int | None = None) -> str:
    mb = max_file_bytes() // (1024 * 1024)
    seen = f" (almeno {size_bytes / 1024 / 1024:.0f} MB)" if size_bytes else ""
    return (f"Il file è troppo grande{seen}: il massimo è {mb} MB. Quasi sempre il peso sono le foto dei contatti: "
            "esporta la rubrica senza foto, oppure dividila in più file e importali uno dopo l'altro "
            "(i contatti già importati non si duplicano).")


def read_upload_chunks(chunks) -> tuple[bytes, int]:
    """Legge il file a pezzi scartando foto e parti binarie; si ferma appena
    supera il limite. Ritorna (testo utile, byte ricevuti)."""
    limit = max_file_bytes()
    stripper = vcard.Stripper()
    for chunk in chunks:
        stripper.feed(chunk)
        if stripper.total > limit:
            raise TooLarge("FILE_TOO_LARGE", too_large_message(stripper.total))
        if stripper.kept > MAX_TEXT_BYTES:
            raise TooLarge("TEXT_TOO_LARGE", "Il file contiene troppo testo per una sola rubrica: dividilo in più file "
                                             "e importali uno dopo l'altro.")
    return stripper.finish(), stripper.total


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _require_operator(ctx) -> None:
    if getattr(ctx, "user_id", None) is None or getattr(ctx, "read_only", False):
        raise Forbidden("IMPORT_FORBIDDEN", "Serve la sessione di un operatore dell'agenzia.")


# ---------------------------------------------------------------------------
# analisi
# ---------------------------------------------------------------------------

def _normalized_phones(entry: dict) -> tuple[list[dict], list[str]]:
    """I numeri validi della scheda (normalizzati, senza doppioni) e quelli scartati."""
    good, bad, seen = [], [], set()
    for p in entry.get("phones") or []:
        raw = (p.get("raw") or "").strip()
        n = normalize_phone(raw)
        if not n or not (6 <= len(n) <= 15):
            if raw:
                bad.append(raw)
            continue
        if n in seen:
            continue
        seen.add(n)
        good.append({"raw": raw, "normalized": n, "label": p.get("label")})
    return good, bad


def _name(entry: dict) -> dict:
    fn = (entry.get("fn") or "").strip() or None
    first, last = (entry.get("first_name") or "").strip() or None, (entry.get("last_name") or "").strip() or None
    org = (entry.get("org") or "").strip() or None
    if not (fn or first or last) and org:
        return {"contact_type": "company", "first_name": None, "last_name": None, "company_name": org, "display_name": org}
    display = fn or " ".join(x for x in (first, last) if x) or None
    if not (first or last) and fn:
        parts = fn.split()
        first, last = (parts[0], " ".join(parts[1:]) or None) if parts else (None, None)
    return {"contact_type": "person", "first_name": first, "last_name": last, "company_name": org, "display_name": display}


def _merge_duplicates(entries: list[dict]) -> list[dict]:
    """Due schede del file con lo stesso numero diventano una (nomi della
    prima, numeri ed email di entrambe)."""
    out: list[dict] = []
    by_number: dict[str, int] = {}
    for e in entries:
        phones, bad = _normalized_phones(e)
        target = next((by_number[p["normalized"]] for p in phones if p["normalized"] in by_number), None)
        if target is None:
            item = {**_name(e), "emails": list(e.get("emails") or []), "phones": phones, "invalid_phones": bad,
                    "merged": 0}
            out.append(item)
            target = len(out) - 1
        else:
            item = out[target]
            item["merged"] += 1
            for p in phones:
                if p["normalized"] not in {x["normalized"] for x in item["phones"]}:
                    item["phones"].append(p)
            for m in e.get("emails") or []:
                if m not in item["emails"]:
                    item["emails"].append(m)
            item["invalid_phones"].extend(bad)
            if not item["display_name"] and _name(e)["display_name"]:
                item.update(_name(e))
        for p in phones:
            by_number.setdefault(p["normalized"], target)
    return out


def _candidate_view(c: dict) -> dict:
    name = c.get("display_name") or " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x) \
        or c.get("company_name") or f"Contatto n. {c['id']}"
    return {"id": c["id"], "name": name, "phone": c.get("phone"), "secondary_phone": c.get("secondary_phone"),
            "email": c.get("email"), "status": c.get("status"), "private": c.get("private_user_id") is not None}


def _classify(entries: list[dict], contacts: list[dict], index: dict[str, set[int]]) -> None:
    _classify_by_id(entries, {c["id"]: c for c in contacts}, index)


def _classify_by_id(entries: list[dict], by_id: dict, index: dict[str, set[int]]) -> None:
    for e in entries:
        if not e["phones"]:
            e["status"], e["candidates"] = STATUS_NO_PHONE, []
            continue
        ids = sorted({cid for p in e["phones"] for cid in index.get(p["normalized"], ())})
        e["candidates"] = [_candidate_view(by_id[i]) for i in ids if i in by_id]
        e["status"] = STATUS_NEW if not ids else (STATUS_EXISTING if len(ids) == 1 else STATUS_AMBIGUOUS)


def _summary(entries: list[dict]) -> dict:
    counts = {STATUS_NEW: 0, STATUS_EXISTING: 0, STATUS_AMBIGUOUS: 0, STATUS_NO_PHONE: 0}
    for e in entries:
        counts[e["status"]] += 1
    return {"entries": len(entries), "merged_in_file": sum(e.get("merged", 0) for e in entries),
            "invalid_phones": sum(len(e.get("invalid_phones") or []) for e in entries), **counts}


def _preview_view(entries: list[dict]) -> list[dict]:
    """Al browser: TUTTE le voci da decidere e un campione delle altre."""
    others = {id(e) for e in [e for e in entries if e["status"] != STATUS_AMBIGUOUS][:PREVIEW_SAMPLE]}
    return [e for e in entries if e["status"] == STATUS_AMBIGUOUS or id(e) in others]


def preview(ctx, *, file_name: str | None, data: bytes, source: str = SOURCE_VCF,
            received_bytes: int | None = None) -> dict:
    """Analizza il file e salva l'anteprima (nessun contatto scritto).
    `received_bytes`: il file e' gia' passato da read_upload_chunks (foto
    scartate); senza, `data` e' il file intero e si filtra qui."""
    _require_operator(ctx)
    agency_id = ctx.require_agency()
    if received_bytes is None:
        data, received_bytes = read_upload_chunks([data])
    raw_entries = vcard.parse(data)
    if not raw_entries:
        raise ImportError_("NO_ENTRIES", "Nel file non ci sono schede vCard (BEGIN:VCARD … END:VCARD).")
    entries = _merge_duplicates(raw_entries)
    with core_cursor(commit=True) as (_, cur):
        if not repository.installed(cur):
            raise NotInstalled("NOT_INSTALLED", NOT_INSTALLED_MESSAGE)
        contacts, index = repository.visible_contacts(cur, ctx)
        _classify(entries, contacts, index)
        for i, e in enumerate(entries):
            e["index"] = i
        summary = _summary(entries)
        summary["file_bytes"] = received_bytes
        row = repository.insert_preview(cur, agency_id=agency_id, user_id=ctx.user_id, source=source,
                                        file_name=(file_name or "")[:200] or None, entries=entries, summary=summary)
    shown = _preview_view(entries)
    return {"import_id": row["id"], "status": row["status"], "file_name": file_name, "summary": summary,
            "entries": shown, "entries_shown": len(shown), "entries_total": len(entries)}


# ---------------------------------------------------------------------------
# conferma
# ---------------------------------------------------------------------------

def _decision(decisions: dict, entry: dict) -> tuple[str, int | None]:
    d = (decisions or {}).get(str(entry["index"])) or {}
    action = d.get("action")
    contact_id = d.get("contact_id")
    if entry["status"] == STATUS_NO_PHONE:
        return ACTION_SKIP, None
    if entry["status"] == STATUS_NEW:
        return (ACTION_SKIP if action == ACTION_SKIP else ACTION_CREATE), None
    if entry["status"] == STATUS_EXISTING:
        return (ACTION_SKIP if action == ACTION_SKIP else ACTION_MERGE), entry["candidates"][0]["id"]
    # ambiguo: solo una scelta esplicita
    if action == ACTION_MERGE and contact_id in {c["id"] for c in entry["candidates"]}:
        return ACTION_MERGE, int(contact_id)
    if action == ACTION_CREATE:
        return ACTION_CREATE, None
    return ACTION_SKIP, None


def _create(cur, ctx, entry: dict, agency_id: int) -> int:
    phones = entry["phones"]
    email = (entry.get("emails") or [None])[0]
    contact_id = repository.insert_contact(
        cur, agency_id=agency_id, user_id=ctx.user_id, assigned_agent_id=creator_assignment(ctx, "contacts"),
        private_user_id=ctx.user_id, contact_type=entry["contact_type"], first_name=entry.get("first_name"),
        last_name=entry.get("last_name"), company_name=entry.get("company_name"), display_name=entry.get("display_name"),
        email=email, email_normalized=normalize_email(email), phone=phones[0]["raw"],
        phone_normalized=phones[0]["normalized"], secondary_phone=phones[1]["raw"] if len(phones) > 1 else None,
        source=CONTACT_SOURCE)
    for pos, p in enumerate(phones):
        repository.add_phone(cur, agency_id=agency_id, contact_id=contact_id, phone=p["raw"],
                             phone_normalized=p["normalized"], label=p.get("label"), position=pos)
    return contact_id


def _merge(cur, entry: dict, agency_id: int, contact_id: int) -> int:
    """Completa il contatto esistente: numeri che mancano (anche come secondo
    telefono se vuoto) ed email se vuota. Ritorna quanti numeri ha aggiunto."""
    known = repository.contact_numbers(cur, contact_id)
    added = 0
    missing_raw = None
    for pos, p in enumerate(entry["phones"]):
        if p["normalized"] in known:
            continue
        if repository.add_phone(cur, agency_id=agency_id, contact_id=contact_id, phone=p["raw"],
                                phone_normalized=p["normalized"], label=p.get("label"), position=100 + pos):
            added += 1
            missing_raw = missing_raw or p["raw"]
    email = (entry.get("emails") or [None])[0]
    repository.fill_empty_fields(cur, contact_id, email=email, email_normalized=normalize_email(email),
                                 secondary_phone=missing_raw)
    return added


RESULT_KEYS = ("created", "updated", "unchanged", "skipped", "undecided", "phones_added", "no_phone")


def _apply(cur, ctx, agency_id: int, entry: dict, decisions: dict, result: dict, by_id: dict, index: dict) -> None:
    """Una scheda: classificata ADESSO sull'indice della rubrica, poi scritta.
    L'indice si aggiorna subito, cosi' le schede successive vedono i contatti
    appena creati e i numeri appena aggiunti."""
    _classify_by_id([entry], by_id, index)
    action, contact_id = _decision(decisions, entry)
    if entry["status"] == STATUS_NO_PHONE:
        result["no_phone"] += 1
    elif action == ACTION_CREATE:
        new_id = _create(cur, ctx, entry, agency_id)
        result["created"] += 1
        by_id[new_id] = {"id": new_id, "display_name": entry.get("display_name"), "first_name": entry.get("first_name"),
                         "last_name": entry.get("last_name"), "company_name": entry.get("company_name"),
                         "phone": entry["phones"][0]["raw"], "status": "active", "private_user_id": ctx.user_id,
                         "email": (entry.get("emails") or [None])[0]}
        for p in entry["phones"]:
            index.setdefault(p["normalized"], set()).add(new_id)
    elif action == ACTION_MERGE and contact_id is not None and _nothing_new(entry, contact_id, by_id, index):
        # gia' tutto in rubrica (tutti i numeri sul contatto, email gia' presente o assente): nessuna scrittura
        result["unchanged"] += 1
    elif action == ACTION_MERGE and contact_id is not None:
        added = _merge(cur, entry, agency_id, contact_id)
        result["phones_added"] += added
        result["updated" if added else "unchanged"] += 1
        for p in entry["phones"]:
            index.setdefault(p["normalized"], set()).add(contact_id)
        if contact_id in by_id and not by_id[contact_id].get("email") and entry.get("emails"):
            by_id[contact_id]["email"] = entry["emails"][0]
    elif entry["status"] == STATUS_AMBIGUOUS:
        result["undecided"] += 1
    else:
        result["skipped"] += 1


def _nothing_new(entry: dict, contact_id: int, by_id: dict, index: dict) -> bool:
    """La scheda non aggiunge nulla al contatto: ogni suo numero e' gia' del
    contatto e non porta un'email da mettere in un campo vuoto."""
    if any(contact_id not in index.get(p["normalized"], ()) for p in entry["phones"]):
        return False
    contact = by_id.get(contact_id) or {}
    return not entry.get("emails") or bool(contact.get("email"))


def _progress_view(imp_id: int, status: str, progress: dict, total: int, result: dict) -> dict:
    return {"import_id": imp_id, "status": status, "done": status == "done",
            "progress": {"processed": progress["next"], "total": total, "blocks": progress.get("blocks", 0)},
            "result": result}


def commit(ctx, import_id: int, decisions: dict | None) -> dict:
    """«Importa tutti», A BLOCCHI. Ogni blocco di BLOCK_SIZE schede e' una
    transazione con il proprio avanzamento; una chiamata elabora i blocchi che
    stanno in TIME_BUDGET_SECONDS e risponde con l'avanzamento («in_progress»);
    il browser richiama finche' non risponde «done». Le decisioni sulle voci
    ambigue si fissano alla prima chiamata. Alla fine: riconciliazione delle
    conversazioni WhatsApp e voci tolte dal registro. Nessun invio."""
    _require_operator(ctx)
    agency_id = ctx.require_agency()
    started = time.monotonic()
    by_id, index, loaded_at = None, None, None
    while True:
        with core_cursor(commit=True) as (_, cur):
            if not repository.installed(cur):
                raise NotInstalled("NOT_INSTALLED", NOT_INSTALLED_MESSAGE)
            # Il lock del registro serializza due chiamate sulla stessa importazione.
            imp = repository.get_import(cur, agency_id, ctx.user_id, import_id, lock=True)
            if imp is None:
                raise NotFound("IMPORT_NOT_FOUND", "Anteprima non trovata (o di un altro operatore).")
            summary = dict(imp["summary"] or {})
            entries = imp["entries"] or []
            if imp["status"] != "preview":
                raise Conflict("IMPORT_ALREADY_DONE", "Questa importazione è già stata completata.")
            progress = dict(summary.get("progress") or {"next": 0, "blocks": 0})
            if "decisions" not in summary:
                summary["decisions"] = {k: v for k, v in (decisions or {}).items()}
            result = dict(summary.get("result_partial") or {k: 0 for k in RESULT_KEYS})
            # L'indice della rubrica: caricato una volta per chiamata, ricaricato
            # se un'altra chiamata ha avanzato nel frattempo.
            if index is None or loaded_at != progress["next"]:
                contacts, index = repository.visible_contacts(cur, ctx)
                by_id = {c["id"]: c for c in contacts}
            if progress["next"] >= len(entries):
                linked = contact_match.reconcile(cur, agency_id)
                result["conversations_linked"] = linked.get("linked_conversations", 0)
                final = {k: v for k, v in summary.items() if k not in ("decisions", "result_partial")}
                final["progress"] = progress
                final["result"] = result
                repository.finish_import(cur, import_id, final)
                return _progress_view(import_id, "done", progress, len(entries), result)
            block = entries[progress["next"]:progress["next"] + BLOCK_SIZE]
            for entry in block:
                _apply(cur, ctx, agency_id, entry, summary["decisions"], result, by_id, index)
            progress = {"next": progress["next"] + len(block), "blocks": progress.get("blocks", 0) + 1}
            summary["progress"], summary["result_partial"] = progress, result
            repository.save_progress(cur, import_id, summary)
            loaded_at = progress["next"]
        if time.monotonic() - started >= TIME_BUDGET_SECONDS and progress["next"] < len(entries):
            return _progress_view(import_id, "in_progress", progress, len(entries), result)


def get(ctx, import_id: int) -> dict:
    _require_operator(ctx)
    with core_cursor() as (_, cur):
        if not repository.installed(cur):
            raise NotInstalled("NOT_INSTALLED", NOT_INSTALLED_MESSAGE)
        imp = repository.get_import(cur, ctx.require_agency(), ctx.user_id, import_id)
    if imp is None:
        raise NotFound("IMPORT_NOT_FOUND", "Importazione non trovata.")
    return {"import_id": imp["id"], "status": imp["status"], "file_name": imp["file_name"], "summary": imp["summary"],
            "entries": imp["entries"] or [], "created_at": _iso(imp["created_at"]),
            "committed_at": _iso(imp["committed_at"])}


def share_contact(ctx, contact_id: int) -> dict:
    """Un contatto riservato diventa dell'agenzia: solo chi l'ha importato."""
    _require_operator(ctx)
    with core_cursor(commit=True) as (_, cur):
        if not repository.installed(cur):
            raise NotInstalled("NOT_INSTALLED", NOT_INSTALLED_MESSAGE)
        if not repository.share_contact(cur, ctx.require_agency(), ctx.user_id, contact_id):
            raise NotFound("CONTACT_NOT_FOUND", "Contatto riservato non trovato fra i tuoi.")
    return {"contact_id": contact_id, "private": False}
