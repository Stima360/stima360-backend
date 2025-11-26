import requests
import os

ACCESS_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_ID")
VERSION = os.getenv("WHATSAPP_API_VERSION", "v20.0")

WHATSAPP_URL = f"https://graph.facebook.com/{VERSION}/{PHONE_NUMBER_ID}/messages"


def normalize_number(number: str) -> str:
    if not number:
        return None
    digits = ''.join(ch for ch in number if ch.isdigit())
    if digits.startswith("39"):
        return digits
    return "39" + digits.lstrip("0")


def send_template_stima(to_number: str, indirizzo: str, pdf_link: str):
    """
    Usa il template ufficiale META: stima_ok
    Variabili:
    {{1}} → indirizzo
    {{2}} → link PDF
    """

    if not ACCESS_TOKEN or not PHONE_NUMBER_ID:
        print("⚠️ Variabili ambiente WhatsApp mancanti")
        return None

    dest = normalize_number(to_number)

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": dest,
        "type": "template",
        "template": {
            "name": "stima_ok",
            "language": {"code": "it"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": indirizzo},
                        {"type": "text", "text": pdf_link}
                    ]
                }
            ]
        }
    }

    r = requests.post(WHATSAPP_URL, headers=headers, json=payload)
    print("📲 WHATSAPP STATUS TEMPLATE:", r.status_code, r.text)
    return r.json()
