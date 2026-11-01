"""Controllo, in sola lettura, della configurazione del CRM TEST per ricevere
l'inoltro WhatsApp dal ricevitore di produzione con UN solo mittente di prova.

Da eseguire come Render One-Off Job sul servizio TEST (legge le variabili del
servizio). Non apre connessioni, non invia nulla, non stampa alcun valore:
solo OK / STOP per ogni controllo. Esce con 0 solo se tutto e' superato.

    python scripts/check_whatsapp_relay_config.py
    python scripts/check_whatsapp_relay_config.py --expected-phone-number-id <Phone ID confermato in Meta>

Tre identita' da non confondere:
- numero business (destinatario): +39 329 567 4589, quello dei clienti. Non e'
  mai un mittente ammesso.
- Phone ID del numero business (`phone_number_id` di Meta): la chiave di
  `WHATSAPP_INBOUND_ROUTES`. Non e' il numero di telefono.
- mittente di prova autorizzato: UN telefono diverso dal numero business,
  l'unico in `WHATSAPP_INBOUND_ALLOWED_SENDERS`.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

BUSINESS_NUMBER = "393295674589"
MIN_SECRET = 32


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def run(env, *, business=BUSINESS_NUMBER, expected_phone_number_id=None, out=print) -> list[str]:
    problems: list[str] = []

    def need(ok, msg):
        out(("OK   " if ok else "STOP ") + msg)
        if not ok:
            problems.append(msg)

    # --- autenticazione dell'inoltro
    need(len(env.get("WHATSAPP_RELAY_SECRET") or "") >= MIN_SECRET,
         f"WHATSAPP_RELAY_SECRET presente e di almeno {MIN_SECRET} caratteri")

    # --- un solo mittente di prova, diverso dal numero business
    raw = (env.get("WHATSAPP_INBOUND_ALLOWED_SENDERS") or "").strip()
    items = [x for x in raw.split(",") if x.strip()]
    senders = [_digits(x) for x in items]
    need(raw != "*" and len(items) == 1, "WHATSAPP_INBOUND_ALLOWED_SENDERS contiene esattamente un mittente (mai *)")
    sender = senders[0] if len(senders) == 1 else ""
    need(bool(re.fullmatch(r"[1-9][0-9]{7,14}", sender)), "mittente di prova in formato internazionale (es. 39...)")
    need(sender != business, "il mittente di prova NON e' il numero business")

    # --- instradamento: Phone ID del numero business -> una agenzia
    routes = [x.strip() for x in (env.get("WHATSAPP_INBOUND_ROUTES") or "").split(",") if x.strip()]
    need(len(routes) == 1, "WHATSAPP_INBOUND_ROUTES ha esattamente una voce")
    pid, _, agency = routes[0].partition(":") if len(routes) == 1 else ("", "", "")
    pid, agency = pid.strip(), agency.strip()
    need(bool(re.fullmatch(r"[0-9]{6,30}", pid)) and agency.isdigit(), "voce nel formato <phone_number_id>:<agency_id>")
    need(pid not in (business, business[2:]) and pid != sender,
         "la chiave e' il Phone ID di Meta, non il numero di telefono business ne' il mittente")
    if expected_phone_number_id:
        need(pid == expected_phone_number_id.strip(), "Phone ID uguale a quello confermato in Meta")

    # --- nessun invio
    need((env.get("WHATSAPP_OUTBOUND_MODE") or "").strip().lower() != "meta",
         "WHATSAPP_OUTBOUND_MODE non e' meta (risposta manuale spenta)")
    need((env.get("WHATSAPP_AUTO_REPLY_MODE") or "").strip().lower() != "send",
         "WHATSAPP_AUTO_REPLY_MODE non e' send (risposte automatiche spente)")
    need((env.get("COMMUNICATION_AUTO_REPLY_TICK") or "").strip() != "1",
         "COMMUNICATION_AUTO_REPLY_TICK non e' 1 (nessun giro delle risposte automatiche)")
    need(not (env.get("WHATSAPP_SERVICE_URL") or "").strip(),
         "WHATSAPP_SERVICE_URL assente (il TEST non invia il template della stima)")

    out("ESITO: " + ("CONFIGURAZIONE PRONTA PER IL MESSAGGIO DI PROVA" if not problems
                     else f"{len(problems)} CONTROLLI NON SUPERATI"))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--expected-phone-number-id")
    args = parser.parse_args()
    return 1 if run(os.environ, expected_phone_number_id=args.expected_phone_number_id) else 0


if __name__ == "__main__":
    sys.exit(main())
