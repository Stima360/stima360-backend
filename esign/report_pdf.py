"""FIRMA OTP - il RAPPORTO DELLE PROVE: un PDF separato, leggibile, verificabile anche senza il server di posta.

Contiene: identificativi (procedimento, immobile, visita, foglio e versione), firmatari, impronte dei documenti,
dichiarazione accettata, metodo di autenticazione, livello FES, cronologia completa delle prove con le impronte della
catena e la sua testa, versione del sistema. Mai codici OTP, token, segreti o testi tecnici superflui.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROMA = ZoneInfo("Europe/Rome")
SYSTEM_ID = "STIMA360 CRM — Firma OTP"
SYSTEM_VERSION = "firma_otp_v1"
EVENT_LABELS = {
    "request_created": "Richiesta di firma creata e documento congelato",
    "link_issued": "Collegamento di firma generato",
    "link_email_queued": "Email con il collegamento accodata",
    "link_revoked": "Collegamento precedente revocato",
    "link_opened": "Collegamento aperto (pagina caricata)",
    "document_viewed": "Documento visualizzato/scaricato dal firmatario",
    "otp_requested": "Codice OTP richiesto dal firmatario",
    "otp_email_queued": "Email con il codice accodata",
    "otp_invalidated": "Codice OTP invalidato",
    "otp_failed": "Codice OTP errato",
    "otp_verified": "Codice OTP verificato",
    "consent_recorded": "Dichiarazione di sottoscrizione accettata",
    "signer_signed": "Sottoscrizione del firmatario registrata",
    "agent_authenticated": "Agente autenticato (sessione CRM e password riverificata)",
    "signer_declined": "Firma rifiutata dal firmatario",
    "request_cancelled": "Richiesta revocata dall'agenzia",
    "request_expired": "Richiesta scaduta",
    "signature_completed": "Procedimento completato",
    "documents_archived": "Documento sottoscritto e rapporto archiviati",
}


def _quando(dt: datetime) -> str:
    return dt.astimezone(ROMA).strftime("%d/%m/%Y %H:%M:%S") + " (ora italiana)"


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def build(*, identity: dict, data: dict) -> bytes:
    """`data`: procedure, request_id, property, visit, sheet, signers, consent, events, chain_head, original_sha256,
    outcome, generated_at, signed_sha256 (facoltativa)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from property_valuations.pdf_render import _header, _kv_table, _register_fonts, _styles, clean_text

    _register_fonts()
    styles = _styles()
    cm = 28.3465
    margin = 2.0 * cm
    width = A4[0] - 2 * margin
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=1.6 * cm,
                            bottomMargin=1.8 * cm, title=f"Rapporto delle prove {data['procedure']}")
    story = list(_header(identity, styles, width))
    story.append(Paragraph("Rapporto delle prove della sottoscrizione elettronica", styles["title"]))
    story.append(Paragraph(clean_text(f"Procedimento {data['procedure']} · generato il {_quando(data['generated_at'])}"),
                           styles["small"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(clean_text(
        "Livello di firma: FIRMA ELETTRONICA SEMPLICE (FES). Metodo: collegamento personale inviato per email e codice OTP "
        "monouso inviato allo stesso indirizzo (cliente); sessione CRM autenticata con password riverificata (agente). "
        "Il controllo di una casella email prova il possesso della casella, non l'identità certa della persona. Questo "
        "rapporto non è una marca temporale qualificata né una conservazione a norma: le date sono quelle del server."),
        styles["note"]))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Identificativi", styles["h2"]))
    story.append(_kv_table([
        ("Procedimento", data["procedure"]), ("Richiesta di firma (CRM)", str(data["request_id"])),
        ("Immobile", clean_text(data["property"])), ("Visita", clean_text(data["visit"])),
        ("Documento", clean_text(data["sheet"])), ("Esito", data["outcome"]),
    ], styles, width))

    story.append(Paragraph("Documenti e impronte", styles["h2"]))
    rows = [("Originale sottoposto (SHA-256)", data["original_sha256"])]
    if data.get("signed_sha256"):
        rows.append(("Documento sottoscritto (SHA-256)", data["signed_sha256"]))
    rows.append(("Testa della catena delle prove", data["chain_head"] or "—"))
    story.append(_kv_table(rows, styles, width))

    story.append(Paragraph("Firmatari", styles["h2"]))
    for s in data["signers"]:
        story.append(_kv_table([
            ("Ruolo", s["role_label"]), ("Nominativo", clean_text(s["name"])), ("Recapito utilizzato", clean_text(s["email"])),
            ("Metodo di autenticazione", s["method"]), ("Stato", s["status_label"]),
            ("Conferma", _quando(s["signed_at"]) if s.get("signed_at") else "—"),
        ], styles, width))
        story.append(Spacer(1, 4))

    story.append(Paragraph("Dichiarazione accettata", styles["h2"]))
    story.append(Paragraph(clean_text(f"Versione {data['consent']['version']} · impronta SHA-256 {data['consent']['sha256']}"),
                           styles["small"]))
    story.append(Spacer(1, 3))
    story.append(Paragraph(clean_text(data["consent"]["text"]), styles["body"]))

    story.append(Paragraph("Cronologia delle prove", styles["h2"]))
    table_rows = [[Paragraph("N.", styles["label"]), Paragraph("Quando (UTC)", styles["label"]),
                   Paragraph("Evento", styles["label"]), Paragraph("Dettagli", styles["label"])]]
    for e in data["events"]:
        det = "; ".join(f"{k}: {v}" for k, v in (e.get("details") or {}).items() if v not in (None, "", [], {}))
        label = EVENT_LABELS.get(e["event_type"], e["event_type"])
        who = {"operator": "agenzia", "signer": "firmatario", "system": "sistema"}.get(e["actor_kind"], e["actor_kind"])
        table_rows.append([Paragraph(str(e["seq"]), styles["small"]), Paragraph(_utc(e["occurred_at"]), styles["small"]),
                           Paragraph(clean_text(f"{label} ({who})"), styles["small"]),
                           Paragraph(clean_text(det + (f" · impronta {e['entry_hash'][:16]}…" if e.get("entry_hash") else "")),
                                     styles["small"])])
    t = Table(table_rows, colWidths=[width * 0.06, width * 0.22, width * 0.34, width * 0.38], repeatRows=1)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 0.8, styles["colors"]["accent"]),
                           ("LINEBELOW", (0, 1), (-1, -1), 0.3, styles["colors"]["line"]),
                           ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    story.append(t)
    story.append(Spacer(1, 10))
    story.append(Paragraph(clean_text(
        f"Verifica: le voci della cronologia sono concatenate (ogni impronta copre la precedente) e sigillate con una chiave "
        f"del servizio; il CRM ricalcola catena e sigilli su richiesta (funzione «Verifica integrità»). Sistema: {SYSTEM_ID}, "
        f"versione {SYSTEM_VERSION}."), styles["small"]))
    doc.build(story)
    return buffer.getvalue()
