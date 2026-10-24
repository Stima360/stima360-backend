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

C3.5 - LA PROVA SCRITTA CON LA RICHIESTA. Dalla 112, la pagina del sito manda
con la scelta il testo della casella e l'impronta SHA-256 di `privacy.html`, e
il server li scrive nella riga `stime` insieme all'istante SOLO se coincidono
con una versione ufficiale conservata sul server (`site_official`).
Il CRM ripete lo stesso confronto. Per quelle spunte
la prova di cio' che la persona aveva davanti e' QUELLA, e vale solo lei:
il testo deve essere quello di un'informativa marketing del sito registrata nel
CRM e l'impronta quella di un'informativa `privacy_terms` registrata. La data
di registrazione nel CRM qui non conta (la riga del CRM cataloga il testo, la
prova e' la richiesta); se il testo o l'impronta non trovano un'informativa
registrata, la prova e' incompleta e la scheda dice quale. Le stime storiche,
senza queste colonne, restano alle regole 4a/4b: niente si ricostruisce.

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

import site_official

from . import repository
from . import service as consent_service
from .capture import SITE_CHECKBOX_CHANNELS, _registrabile, site_choices
from .database import consent_cursor
from .enums import ACTOR_SUBJECT, PURPOSE_MARKETING, PURPOSE_PRIVACY_TERMS, SOURCE_PUBLIC_STIMA
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
    "text_mismatch": ("un'informativa del sito coerente con la documentazione: quella registrata nel CRM per "
                      "questa data ha un testo diverso da quello della casella documentato per lo stesso giorno"),
    # C3.5: la prova scritta con la richiesta
    "recorded_text": "il testo della casella marketing registrato con la richiesta",
    "recorded_privacy": "l'impronta dell'informativa privacy registrata con la richiesta",
    "recorded_text_unofficial": ("un testo della casella che coincida con una versione ufficiale del sito "
                                 "conservata sul server"),
    "recorded_privacy_unofficial": ("un'informativa privacy che coincida con una versione ufficiale accettata "
                                    "conservata sul server"),
    "recorded_text_unregistered": ("un'informativa marketing del sito registrata nel CRM con il testo della casella "
                                   "mostrato in questa richiesta"),
    "recorded_privacy_unregistered": ("l'informativa privacy mostrata in questa richiesta registrata nel CRM "
                                      "(nessuna informativa privacy con la sua impronta SHA-256)"),
}
PROOF_LABELS = {
    "registered": "informativa registrata nel CRM prima della spunta",
    "site_capture": "testo della casella provato da una prova documentale del sito",
    "submission_record": "testo della casella e informativa privacy registrati dal server con la richiesta",
}
CHANNEL_STATUS_LABELS = {
    "recognized": "concesso dal sito",
    "recognizable": "riconoscibile",
    "not_covered": "l'informativa applicabile non copre questo canale",
    "superseded": "una decisione successiva su questo canale prevale",
    "already_granted": "gia' concesso da una decisione precedente",
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
    prova = site_checkbox_evidence()
    # Una versione registrata nel CRM prima della spunta e' la dichiarazione
    # del titolare del testo in vigore. Se pero' la documentazione del sito
    # (copia, backup, storico) copre quello stesso giorno con un testo
    # DIVERSO, la dichiarazione e' contraddetta e non vale.
    documentata = proven_window(prova, when) if prova is not None else None
    contraddetta = False
    for riga in righe:
        if riga["created_at"] <= when:
            if documentata is not None and normalize_text(riga["content"]) != normalize_text(prova["text"]):
                contraddetta = True
                continue
            return riga, {"kind": "registered", "label": PROOF_LABELS["registered"]}, None
    if prova is None:
        return None, None, "notice"
    candidate = [r for r in righe if r["valid_from"] >= prova["proven_since"]
                 and normalize_text(r["content"]) == normalize_text(prova["text"])]
    if not candidate:
        return None, None, "text_mismatch" if contraddetta else "notice"
    finestra = documentata
    if finestra is None:
        return None, None, "text_proof"
    riga = candidate[0]
    return riga, {"kind": "site_capture", "label": PROOF_LABELS["site_capture"],
                  "captured_on": prova["captured_on"],
                  "evidence_kind": finestra["kind"], "evidence_label": KIND_LABELS[finestra["kind"]],
                  "reference": finestra["reference"],
                  "window": [finestra["from"].strftime("%Y-%m-%d"), finestra["to"].strftime("%Y-%m-%d")],
                  "sha256": prova["sha256"], "source": prova["source"], "url": prova["url"]}, None


_COLONNE_PROVA = ("consenso_marketing_testo", "consenso_marketing_privacy_sha256")


def _ha_colonne_prova(cur) -> bool:
    """La 112 c'e'? Senza, nessuna stima ha la prova scritta con la richiesta."""
    cur.execute("SELECT count(*) AS n FROM information_schema.columns WHERE table_schema = current_schema() "
                "AND table_name = 'stime' AND column_name = ANY(%s)", [list(_COLONNE_PROVA)])
    return int(cur.fetchone()["n"]) == len(_COLONNE_PROVA)


def submission_record(cur, ctx, stima_id: int | None) -> dict[str, Any] | None:
    """Il testo della casella e l'impronta di privacy.html scritti con la
    richiesta, o None (stima storica, 112 assente): mai ricostruiti."""
    if stima_id is None or not _ha_colonne_prova(cur):
        return None
    cur.execute("SELECT consenso_marketing_testo AS testo, consenso_marketing_privacy_sha256 AS privacy "
                "FROM stime WHERE id = %s AND agency_id = %s", [stima_id, ctx.require_agency()])
    r = cur.fetchone()
    if not r or (r["testo"] is None and r["privacy"] is None):
        return None
    return {"text": r["testo"], "privacy_sha256": r["privacy"].strip() if r["privacy"] else None}


def submission_notice(cur, record: dict[str, Any], when: datetime
                      ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """(informativa marketing del sito, prova, cosa manca) dalla prova scritta
    con la richiesta: il testo registrato deve essere quello di un'informativa
    marketing del sito, l'impronta quella di un'informativa privacy, entrambe
    registrate nel CRM."""
    if not record.get("text"):
        return None, None, "recorded_text"
    if not record.get("privacy_sha256"):
        return None, None, "recorded_privacy"
    # anche il CRM confronta con le versioni ufficiali del server: una riga
    # arrivata dall'import non e' una prova solo perche' c'e'
    if site_official.accepted_checkbox(record["text"]) is None:
        return None, None, "recorded_text_unofficial"
    if site_official.accepted_privacy(record["privacy_sha256"]) is None:
        return None, None, "recorded_privacy_unofficial"
    cur.execute(
        """SELECT id, version, context, content, channels, valid_from, valid_to, created_at
             FROM consent_notices
            WHERE purpose = %s AND content_available AND lower(context) LIKE %s
            ORDER BY (valid_from <= %s AND (valid_to IS NULL OR valid_to >= %s)) DESC, created_at DESC, id DESC""",
        [PURPOSE_MARKETING, f"%{SITE_NOTICE_CONTEXT_MARKER}%", when, when])
    testo = normalize_text(record["text"])
    informativa = next((dict(r) for r in cur.fetchall() if normalize_text(r["content"]) == testo), None)
    if informativa is None:
        return None, None, "recorded_text_unregistered"
    # l'impronta e' dei byte di privacy.html; il testo incollato nel CRM puo'
    # avere i ritorni a capo CRLF del modulo web: si confrontano entrambe le forme
    cur.execute("SELECT id, version FROM consent_notices WHERE purpose = %s AND (content_hash = %s "
                "OR encode(sha256(convert_to(replace(content, E'\\r\\n', E'\\n'), 'UTF8')), 'hex') = %s) "
                "ORDER BY id DESC LIMIT 1", [PURPOSE_PRIVACY_TERMS, record["privacy_sha256"], record["privacy_sha256"]])
    privacy = cur.fetchone()
    if privacy is None:
        return None, None, "recorded_privacy_unregistered"
    return informativa, {"kind": "submission_record", "label": PROOF_LABELS["submission_record"],
                         "text": record["text"], "privacy_sha256": record["privacy_sha256"],
                         "privacy_notice": {"id": privacy["id"], "version": privacy["version"]}}, None


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
        registrata = submission_record(cur, ctx, s["stima_id"])
        if s["decided_at"] is None:
            informativa, prova, motivo = None, None, "notice"
        elif registrata is not None:
            # la prova scritta con la richiesta decide da sola
            informativa, prova, motivo = submission_notice(cur, registrata, s["decided_at"])
        else:
            informativa, prova, motivo = site_notice_at(cur, s["decided_at"])
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
            elif _eventi_canale_dopo(cur, ctx, contact_id, canale, s["decided_at"]):
                # solo una decisione SUCCESSIVA alla spunta prevale; una revoca
                # precedente cede a una nuova spunta, che e' un nuovo consenso
                canali[canale] = {"status": "superseded"}
            elif stati[canale]["status"] == "granted":
                canali[canale] = {"status": "already_granted"}
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
            "record": registrata,
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
    if p.get("kind") == "submission_record":
        return (f"{base}; prova: testo della casella e informativa privacy (versione "
                f"{p['privacy_notice']['version']}, SHA-256 {p['privacy_sha256']}) registrati dal server "
                f"con la richiesta.")
    if p.get("kind") == "site_capture":
        inizio, fine = p["window"]
        periodo = inizio if inizio == fine else f"{inizio} - {fine}"
        return (f"{base}; prova del testo: {p['evidence_label']} ({periodo}; {p['reference']}), "
                f"testo uguale alla copia firmata {p['source']} SHA-256 {p['sha256']}.")
    return f"{base}, registrata nel CRM prima della spunta."


def pending_contacts(cur, *, limit: int = 200) -> list[tuple[int, int]]:
    """(agency_id, contact_id) con una spunta del sito che ORA si puo'
    riconoscere su almeno un canale della casella:

        * la spunta ha una prova possibile: con la prova scritta con la
          richiesta (C3.5), testo e impronta che trovano le loro informative;
          senza, un'informativa del sito valida e gia' registrata alla sua
          data, oppure una data coperta dalla documentazione del sito;
        * su quel canale non e' gia' riconosciuta, non c'e' una decisione
          successiva, non e' gia' concesso e c'e' un recapito.

    Le spunte che non potranno mai essere riconosciute (nessuna prova,
    superate, gia' concesse) restano fuori: non occupano il lotto a ogni giro
    e non nascondono quelle riconoscibili. La valutazione completa resta di
    `assess`; questa e' solo la selezione dei candidati."""
    prova = site_checkbox_evidence()
    finestre = [(f["from"], f["to"]) for f in (prova or {}).get("windows") or []]
    coperta_da_documenti = " OR ".join(["ce.decided_at BETWEEN %s AND %s"] * len(finestre)) or "FALSE"
    recapito = {"email": "c.email_normalized", "whatsapp": "c.phone_normalized"}
    canale_aperto = []
    parametri: list[Any] = [PURPOSE_MARKETING, SOURCE_PUBLIC_STIMA, EVIDENCE_STIMA]
    for canale in SITE_CHECKBOX_CHANNELS:
        canale_aperto.append(f"""(
            COALESCE({recapito[canale]}, '') <> ''
            AND NOT EXISTS (SELECT 1 FROM consent_events r
                             WHERE r.agency_id = ce.agency_id AND r.contact_id = ce.contact_id
                               AND r.idempotency_key = 'public_stima:' || ce.evidence_ref || ':marketing:' || %s)
            AND NOT EXISTS (SELECT 1 FROM consent_events d
                             WHERE d.agency_id = ce.agency_id AND d.contact_id = ce.contact_id
                               AND d.purpose = %s AND d.channel = %s AND d.decided_at > ce.decided_at)
            AND NOT EXISTS (SELECT 1 FROM contact_channel_consents g
                             WHERE g.agency_id = ce.agency_id AND g.contact_id = ce.contact_id
                               AND g.channel = %s AND g.status = 'granted'))""")
        parametri += [canale, PURPOSE_MARKETING, canale, canale]
    per_data = f"""(EXISTS (
                        SELECT 1 FROM consent_notices n
                         WHERE n.purpose = %s AND n.content_available AND lower(n.context) LIKE %s
                           AND n.created_at <= ce.decided_at AND n.valid_from <= ce.decided_at
                           AND (n.valid_to IS NULL OR n.valid_to >= ce.decided_at))
                    OR {coperta_da_documenti})"""
    parametri_per_data: list[Any] = [PURPOSE_MARKETING, f"%{SITE_NOTICE_CONTEXT_MARKER}%"]
    for da, a in finestre:
        parametri_per_data += [da, a]
    if _ha_colonne_prova(cur):
        # C3.5: con la prova scritta con la richiesta decide solo lei
        stima_di = ("s.id = CASE WHEN ce.evidence_ref ~ '^[0-9]+$' THEN ce.evidence_ref::bigint END "
                    "AND s.agency_id = ce.agency_id")
        prova_possibile = f"""((EXISTS (
                        SELECT 1 FROM stime s
                          JOIN consent_notices n ON n.purpose = %s AND n.content_available
                               AND lower(n.context) LIKE %s
                               AND lower(btrim(regexp_replace(n.content, '\\s+', ' ', 'g')))
                                 = lower(btrim(regexp_replace(s.consenso_marketing_testo, '\\s+', ' ', 'g')))
                          JOIN consent_notices p ON p.purpose = %s
                               AND (p.content_hash = s.consenso_marketing_privacy_sha256
                                    OR encode(sha256(convert_to(replace(p.content, E'\\r\\n', E'\\n'), 'UTF8')),
                                              'hex') = s.consenso_marketing_privacy_sha256)
                         WHERE {stima_di}
                           AND lower(btrim(regexp_replace(s.consenso_marketing_testo, '\\s+', ' ', 'g'))) = ANY(%s)
                           AND s.consenso_marketing_privacy_sha256 = ANY(%s)))
                 OR (NOT EXISTS (
                        SELECT 1 FROM stime s
                         WHERE {stima_di} AND (s.consenso_marketing_testo IS NOT NULL
                                               OR s.consenso_marketing_privacy_sha256 IS NOT NULL))
                     AND {per_data}))"""
        parametri += [PURPOSE_MARKETING, f"%{SITE_NOTICE_CONTEXT_MARKER}%", PURPOSE_PRIVACY_TERMS,
                      site_official.accepted_texts(), site_official.accepted_privacy_hashes(),
                      *parametri_per_data]
    else:
        prova_possibile = per_data
        parametri += parametri_per_data
    parametri.append(limit)
    cur.execute(
        f"""SELECT ce.agency_id, ce.contact_id, min(ce.decided_at) AS prima
              FROM consent_events ce
              JOIN contacts c ON c.id = ce.contact_id AND c.agency_id = ce.agency_id
             WHERE ce.purpose = %s AND ce.channel IS NULL AND ce.source = %s AND ce.decision = 'granted'
               AND ce.evidence_type = %s AND c.deleted_at IS NULL
               AND ({" OR ".join(canale_aperto)})
               AND {prova_possibile}
             GROUP BY ce.agency_id, ce.contact_id
             ORDER BY prima, ce.contact_id LIMIT %s""",
        parametri)
    return [(int(r["agency_id"]), int(r["contact_id"])) for r in cur.fetchall()]


__all__ = ["assess", "recognize", "pending_contacts", "site_notice_at", "submission_notice", "submission_record",
           "SITE_NOTICE_CONTEXT_MARKER",
           "MISSING_LABELS", "CHANNEL_STATUS_LABELS", "PROOF_LABELS"]
