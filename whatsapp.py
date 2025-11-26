import requests
import os

ACCESS_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_ID")
API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v19.0")

WHATSAPP_URL = f"https://graph.facebook.com/{API_VERSION}/{PHONE_NUMBER_ID}/messages"


def normalize_number(number: str) -> str:
    """Rende il numero compatibile con WhatsApp Cloud API."""
    if not number:
        return None
    digits = "".join(ch for ch in number if ch.isdigit())

    if digits.startswith("39"):
        return digits
    return "39" + digits.lstrip("0")


def send_template_stima(to_number: str, nome: str, pdf_link: str):
    """
    Invia il TEMPLATE WhatsApp 'stima_pronta'
    (arriva SEMPRE anche senza chat aperta).
    """

    dest = normalize_number(to_number)
    if not dest:
        print("⚠️ Numero non valido:", to_number)
        return None

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": dest,
        "type": "template",
        "template": {
            "name": "stima_pronta",
            "language": {"code": "it"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": nome},
                        {"type": "text", "text": pdf_link}
                    ]
                }
            ]
        }
    }

    r = requests.post(WHATSAPP_URL, json=payload, headers=headers)
    print("📨 WhatsApp TEMPLATE status:", r.status_code, r.text[:300])
    return r.json()
