"""FIRMA OTP - lo stato della richiesta COME LO LEGGE L'OPERATORE (riquadro firma, sezione Fogli visita, avvisi).

Solo presentazione: legge i fatti gia' calcolati da `otp_service.decorate` (stato della richiesta, firmatari, esito
dell'email registrato dal dispatcher, codice in attesa) e li dice con sette parole. Non cambia nulla nel database e
non decide nulla sulla firma.

Regole di onesta':
* «Completato» solo con la richiesta davvero firmata da tutti (stato `signed`);
* un'email «accettata dal server di posta» NON e' «consegnata»: si dice cosi';
* un esito d'invio incerto non diventa ne' successo ne' fallimento.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

ROMA = ZoneInfo("Europe/Rome")

TO_SEND, AWAITING_CLIENT, AWAITING_AGENT, COMPLETED, EXPIRED, DECLINED, SEND_FAILED = (
    "to_send", "awaiting_client", "awaiting_agent", "completed", "expired", "declined", "send_failed")
LABELS = {
    TO_SEND: "Da inviare", AWAITING_CLIENT: "In attesa del cliente", AWAITING_AGENT: "In attesa dell'agente",
    COMPLETED: "Completato", EXPIRED: "Scaduto", DECLINED: "Rifiutato", SEND_FAILED: "Invio non riuscito",
}
#: Gli esiti dell'email che dicono con certezza che il messaggio NON e' partito.
MAIL_FAILED = ("failed", "suppressed", "cancelled")


def _quando(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).astimezone(ROMA).strftime("%d/%m/%Y alle %H:%M")
    except ValueError:
        return None


def _signer(view: dict, role: str) -> dict | None:
    return next((s for s in view.get("signers") or [] if s.get("role") == role), None)


def ui_state(view: dict | None) -> dict:
    """`{state, label, note}` di una richiesta (o di nessuna). `note` e' una frase per l'operatore, mai tecnica."""
    if view is None or view.get("status") == "cancelled":
        return {"state": TO_SEND, "label": LABELS[TO_SEND],
                "note": "L'ultima richiesta è stata annullata." if view else None}
    status = view.get("status")
    visitor, agent = _signer(view, "visitor"), _signer(view, "agent")
    if status == "signed":
        quando = _quando((visitor or {}).get("signed_at") or view.get("closed_at"))
        chi = f" da {visitor['name']}" if visitor and visitor.get("name") else ""
        return {"state": COMPLETED, "label": LABELS[COMPLETED],
                "note": f"Documento firmato{chi}{f' il {quando}' if quando else ''}."}
    if status == "expired":
        return {"state": EXPIRED, "label": LABELS[EXPIRED], "note": "Il cliente non ha firmato entro la scadenza."}
    if status == "declined":
        return {"state": DECLINED, "label": LABELS[DECLINED], "note": "Il cliente ha scelto di non firmare."}
    # aperta
    if visitor and visitor.get("status") == "signed":
        if agent and agent.get("status") == "pending":
            return {"state": AWAITING_AGENT, "label": LABELS[AWAITING_AGENT],
                    "note": f"Il cliente ha firmato: manca la firma di {agent['name']}."}
    agente_firmato = bool(agent and agent.get("status") == "signed")
    mail = (view.get("link") or {}).get("email")
    if mail and mail.get("status") in MAIL_FAILED:
        return {"state": SEND_FAILED, "label": LABELS[SEND_FAILED],
                "note": "L'email con il collegamento non è partita: controlla l'indirizzo e reinvia."}
    parti = []
    if view.get("otp_pending"):
        parti.append("Il cliente ha chiesto il codice di conferma.")
    elif mail is None:
        parti.append("Collegamento non ancora inviato.")
    elif mail.get("status") == "sent":
        quando = _quando((view.get("link") or {}).get("issued_at"))
        parti.append(f"Email accettata dal server di posta{f' il {quando}' if quando else ''} (non è una conferma di lettura).")
    elif mail.get("status") == "indeterminate":
        parti.append("Esito dell'invio dell'email incerto: se il cliente non la trova, reinviala.")
    else:
        parti.append("Invio dell'email in corso.")
    if agente_firmato:
        parti.append("La firma dell'agente è già registrata.")
    return {"state": AWAITING_CLIENT, "label": LABELS[AWAITING_CLIENT], "note": " ".join(parti)}
