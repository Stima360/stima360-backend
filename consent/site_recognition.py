"""C3.4 - il RICONOSCIMENTO dei consensi marketing espressi sul sito.

La casella marketing di stima360.it («... tramite email e WhatsApp») arriva nel
CRM come una spunta SENZA canale: fonte `public_stima`, prova `stima:<id>`,
istante registrato dal server del sito. Da sola non autorizza niente (C2).

Qui si valuta, spunta per spunta, se le prove bastano a un consenso valido:

    1. la spunta esiste come evento (il solo vecchio flag non basta);
    2. ha una data certa (`decided_at`);
    3. cita la richiesta di valutazione (`evidence_type = 'stima'`);
    4. esiste l'INFORMATIVA DEL SITO applicabile: una versione marketing che
       il Super Admin ha registrato nel CRM dichiarandola come testo del sito
       (il contesto nomina `stima360.it`), valida alla data della spunta, e
       PROVATA in uno di due modi:
         a. era gia' registrata nel CRM in quel momento (`created_at`,
            immutabile), oppure
         b. riporta ESATTAMENTE il testo della casella del sito provato da
            prove documentali effettive (`consent.site_evidence`: la copia
            firmata, un backup datato, uno storico delle pubblicazioni) e la
            data della spunta cade in un giorno o intervallo che una di esse
            documenta. Due copie identiche in date diverse NON provano
            l'intervallo fra loro. La riga del CRM puo' essere posteriore:
            la prova di cio' che la persona ha letto sono quei documenti.
       Niente e' attribuito senza prova: una versione registrata dopo la
       spunta e senza il testo provato non vale; una data non coperta da
       copie non vale, anche se "probabilmente" il sito non era cambiato.

Se tutto c'e', per ogni canale che la casella nomina E che l'informativa
copre si registra un consenso per canale - «Concesso dal sito» - con la data
della spunta, la richiesta e l'informativa come prove, senza double opt-in.
Se manca qualcosa, la spunta resta cio' che e' (prova conservata) e la scheda
dice esattamente cosa manca. Nessun canale fuori dalla casella (SMS,
telefonate) viene mai riconosciuto.

Una decisione SUCCESSIVA sul canale (una revoca, un consenso dal link)
prevale sempre: il riconoscimento non la tocca. Un indirizzo disiscritto da
tutta la piattaforma non viene riconosciuto.

Idempotente: la chiave `public_stima:<stima>:marketing:<canale>` e' la stessa
che il bridge pubblico usa per i canali dichiarati dal sito, quindi
ripetere il riconoscimento non crea un secondo evento.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from . import repository
from . import service as consent_service
from .capture import SITE_CHECKBOX_CHANNELS, _registrabile, site_choices
from .database import consent_cursor
from .enums import ACTOR_SUBJECT, PURPOSE_MARKETING, SOURCE_PUBLIC_STIMA
from .site_evidence import KIND_LABELS, describe_windows, normalize_text, proven_window, site_checkbox_evidence

#: Il contesto di un'informativa marketing che il Super Admin dichiara essere
#: il testo mostrato sul sito pubblico (confronto senza maiuscole).
SITE_NOTICE_CONTEXT_MARKER = "stima360.it"

EVIDENCE_STIMA = "stima"

#: Cosa puo' mancare, detto in italiano alla scheda.
MISSING_LABELS = {
    "event": "la spunta come evento con data certa e richiesta di valutazione (c'e' solo il vecchio flag)",
    "decided_at": "la data della spunta",
    "stima": "il riferimento alla richiesta di valutazione",
    "notice": ("l'informativa del sito valida alla data della spunta (registrata nel CRM prima della spunta, "
               "oppure con il testo esatto della casella provato dalle copie firmate del sito)"),
    # con {quando} e {copie}: la data della spunta e le date delle copie firmate
    "text_proof": ("la prova documentale che il testo della casella pubblicato il {quando} fosse quello della "
                   "copia firmata (prove disponibili: {copie}; serve un backup datato di quel giorno o uno "
                   "storico delle pubblicazioni che lo copra)"),
}
PROOF_LABELS = {
    "registered": "informativa registrata nel CRM prima della spunta",
    "site_capture": "testo della casella provato da una prova documentale del sito",
}
CHANNEL_STATUS_LABELS = {
    "recognized": "concesso dal sito",
    "recognizable": "riconoscibile",
    "not_covered": "l'informativa applicabile non copre questo canale",
    "superseded": "una decisione successiva su questo canale prevale",
    "platform_optout": "indirizzo disiscritto da tutto STIMA360",
    "no_address": "nessun indirizzo o numero registrato",
    "incomplete": "prova incompleta",
}


def site_notice_at(cur, when: datetime) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """(informativa del SITO valida in `when`, prova, cosa manca).

    Prima una versione gia' registrata in `when`; altrimenti una registrata
    dopo ma con il testo esatto della casella provato dalle copie firmate del
    sito, SOLO se `when` cade in un intervallo provato. Se una versione con
    il testo esatto c'e' ma la data non e' coperta da copie, manca la prova
    del testo per quella data (`text_proof`), non l'informativa."""
    cur.execute(
        """SELECT id, version, context, content, content_hash, channels, valid_from, valid_to, created_at
             FROM consent_notices
            WHERE purpose = %s AND content_available
              AND valid_from <= %s AND (valid_to IS NULL OR valid_to >= %s)
              AND lower(context) LIKE %s
            ORDER BY (created_at <= %s) DESC, valid_from DESC, id DESC""",
        [PURPOSE_MARKETING, when, when, f"%{SITE_NOTICE_CONTEXT_MARKER}%", when])
    righe = [dict(r) for r in cur.fetchall()]
    for riga in righe:
        if riga["created_at"] <= when:
            return riga, {"kind": "registered", "label": PROOF_LABELS["registered"]}, None
    prova = site_checkbox_evidence()
    if prova is None:
        return None, None, "notice"
    candidate = [r for r in righe if r["valid_from"] >= prova["proven_since"]
                 and normalize_text(r["content"]) == normalize_text(prova["text"])]
    if not candidate:
        return None, None, "notice"
    finestra = proven_window(prova, when)
    if finestra is None:
        return None, None, "text_proof"
    riga = candidate[0]
    return riga, {"kind": "site_capture", "label": PROOF_LABELS["site_capture"],
                  "captured_on": prova["captured_on"],
                  "evidence_kind": finestra["kind"], "evidence_label": KIND_LABELS[finestra["kind"]],
                  "reference": finestra["reference"],
                  "window": [finestra["from"].strftime("%Y-%m-%d"), finestra["to"].strftime("%Y-%m-%d")],
                  "sha256": prova["sha256"], "source": prova["source"], "url": prova["url"]}, None


def _idempotency(stima_id: int, canale: str) -> str:
    return f"public_stima:{stima_id}:marketing:{canale}"


def _eventi_canale_dopo(cur, ctx, contact_id: int, canale: str, dopo: datetime) -> bool:
    cur.execute(
        """SELECT 1 FROM consent_events
            WHERE agency_id = %s AND contact_id = %s AND purpose = %s AND channel = %s
              AND decided_at > %s LIMIT 1""",
        [ctx.require_agency(), contact_id, PURPOSE_MARKETING, canale, dopo])
    return cur.fetchone() is not None


def _gia_riconosciuto(cur, ctx, stima_id: int, canale: str) -> bool:
    cur.execute("SELECT 1 FROM consent_events WHERE agency_id = %s AND idempotency_key = %s",
                [ctx.require_agency(), _idempotency(stima_id, canale)])
    return cur.fetchone() is not None


def assess(cur, ctx, contact_id: int) -> dict[str, Any]:
    """La valutazione, senza scrivere niente. Per la scheda e per `recognize`."""
    contact = repository.select_contact(cur, ctx, contact_id)
    spunte = [s for s in site_choices(cur, ctx, contact_id) if s["decision"] == "granted"]
    stati = consent_service.channel_states(cur, ctx, contact_id)
    valutazioni = []
    for s in spunte:
        mancano = []
        if s["decided_at"] is None:
            mancano.append("decided_at")
        if s["stima_id"] is None:
            mancano.append("stima")
        informativa, prova, motivo = (site_notice_at(cur, s["decided_at"]) if s["decided_at"] is not None
                                      else (None, None, "notice"))
        if informativa is None:
            mancano.append(motivo or "notice")
        canali = {}
        for canale in SITE_CHECKBOX_CHANNELS:
            if s["stima_id"] is not None and _gia_riconosciuto(cur, ctx, s["stima_id"], canale):
                # gia' riconosciuto: se poi e' stato revocato, prevale la revoca
                canali[canale] = {"status": "recognized" if stati[canale]["status"] == "granted" else "superseded"}
                continue
            if mancano:
                canali[canale] = {"status": "incomplete"}
            elif canale not in (informativa.get("channels") or ()):
                canali[canale] = {"status": "not_covered"}
            elif _eventi_canale_dopo(cur, ctx, contact_id, canale, s["decided_at"]) \
                    or stati[canale]["status"] != "never_given":
                canali[canale] = {"status": "superseded"}
            elif not repository.contact_address(contact, canale):
                canali[canale] = {"status": "no_address"}
            elif repository.latest_platform_optout(cur, canale, repository.contact_address(contact, canale)):
                canali[canale] = {"status": "platform_optout"}
            else:
                canali[canale] = {"status": "recognizable"}
        for canale, v in canali.items():
            v["label"] = CHANNEL_STATUS_LABELS[v["status"]]
        valutazioni.append({
            "event_id": s["event_id"], "stima_id": s["stima_id"], "decided_at": s["decided_at"],
            "notice": None if informativa is None else {
                "id": informativa["id"], "version": informativa["version"],
                "channels": list(informativa.get("channels") or [])},
            "proof": prova,
            "missing": mancano,
            "missing_labels": [_etichetta_mancante(m, s["decided_at"]) for m in mancano],
            "channels": canali,
        })
    solo_flag = contact.get("marketing_consent") is True and not spunte \
        and all(v["status"] != "granted" for v in stati.values())
    return {
        "ticks": valutazioni,
        "legacy_only": solo_flag,
        "legacy_missing_labels": [MISSING_LABELS["event"]] if solo_flag else [],
        "registrable": _registrabile(contact),
    }


def recognize(ctx, contact_id: int, *, cur=None) -> dict[str, Any]:
    """Registra i consensi riconoscibili. Idempotente; una transazione."""
    if cur is not None:
        return _recognize(cur, ctx, contact_id)
    with consent_cursor(commit=True) as (_, cur2):
        return _recognize(cur2, ctx, contact_id)


def _recognize(cur, ctx, contact_id: int) -> dict[str, Any]:
    valutazione = assess(cur, ctx, contact_id)
    concessi = []
    if not valutazione["registrable"]:
        return {"granted": concessi, "assessment": valutazione}
    for spunta in valutazione["ticks"]:
        for canale, v in spunta["channels"].items():
            if v["status"] != "recognizable":
                continue
            consent_service.record_grant(
                ctx, contact_id=contact_id, purpose=PURPOSE_MARKETING, channel=canale,
                source=SOURCE_PUBLIC_STIMA, actor_type=ACTOR_SUBJECT, decided_at=spunta["decided_at"],
                notice_id=spunta["notice"]["id"], evidence_type=EVIDENCE_STIMA,
                evidence_ref=str(spunta["stima_id"]), note=_nota_prova(spunta),
                idempotency_key=_idempotency(spunta["stima_id"], canale), cur=cur)
            v["status"] = "recognized"
            v["label"] = CHANNEL_STATUS_LABELS["recognized"]
            concessi.append({"stima_id": spunta["stima_id"], "channel": canale})
    return {"granted": concessi, "assessment": valutazione}


def _etichetta_mancante(codice: str, quando: datetime | None) -> str:
    if codice != "text_proof":
        return MISSING_LABELS[codice]
    return MISSING_LABELS["text_proof"].format(quando=quando.strftime("%d/%m/%Y") if quando else "?",
                                               copie=describe_windows(site_checkbox_evidence()))


def _nota_prova(spunta: dict[str, Any]) -> str:
    """La catena di prova, scritta nell'evento: chi legge lo storico sa su
    cosa poggia il riconoscimento."""
    p = spunta.get("proof") or {}
    base = (f"Riconosciuto dalla casella marketing del sito: richiesta di valutazione n. {spunta['stima_id']}, "
            f"spunta del {spunta['decided_at']:%Y-%m-%d %H:%M %Z}; informativa versione {spunta['notice']['version']}")
    if p.get("kind") == "site_capture":
        inizio, fine = p["window"]
        periodo = inizio if inizio == fine else f"{inizio} - {fine}"
        return (f"{base}; prova del testo: {p['evidence_label']} ({periodo}; {p['reference']}), "
                f"testo uguale alla copia firmata {p['source']} SHA-256 {p['sha256']}.")
    return f"{base}, registrata nel CRM prima della spunta."


def pending_contacts(cur, *, limit: int = 200) -> list[tuple[int, int]]:
    """(agency_id, contact_id) con una spunta del sito non ancora riconosciuta
    sui canali della casella: per la riconciliazione a ogni giro di import."""
    cur.execute(
        """SELECT DISTINCT ce.agency_id, ce.contact_id
             FROM consent_events ce
             JOIN contacts c ON c.id = ce.contact_id
            WHERE ce.purpose = %s AND ce.channel IS NULL AND ce.source = %s AND ce.decision = 'granted'
              AND ce.evidence_type = %s AND c.deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM consent_events r
                   WHERE r.agency_id = ce.agency_id AND r.contact_id = ce.contact_id
                     AND r.idempotency_key = 'public_stima:' || ce.evidence_ref || ':marketing:' || %s)
            ORDER BY ce.agency_id, ce.contact_id LIMIT %s""",
        [PURPOSE_MARKETING, SOURCE_PUBLIC_STIMA, EVIDENCE_STIMA, SITE_CHECKBOX_CHANNELS[0], limit])
    return [(int(r["agency_id"]), int(r["contact_id"])) for r in cur.fetchall()]


__all__ = ["assess", "recognize", "pending_contacts", "site_notice_at", "SITE_NOTICE_CONTEXT_MARKER",
           "MISSING_LABELS", "CHANNEL_STATUS_LABELS", "PROOF_LABELS"]
