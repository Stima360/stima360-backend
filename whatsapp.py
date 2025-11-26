import requests
import os

# 🔐 Legge token e phone_number_id dalle variabili ambiente
ACCESS_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_ID")
VERSION = os.getenv("WHATSAPP_API_VERSION", "v19.0")

WHATSAPP_URL = f"https://graph.facebook.com/{VERSION}/{PHONE_NUMBER_ID}/messages"


def normalize_number(number: str) -> str | None:
    """
    Normalizza numero:
    - tiene solo cifre
    - aggiunge prefisso 39 se manca
    """
    if not number:
        return None
    
    digits = "".join(ch for ch in number if ch.isdigit())
    if digits.startswith("39"):
        return digits
    return "39" + digits.lstrip("0")


def send_whatsapp_template(to_number: str, nome: str, pdf_link: str):
    """
    Invia il template WhatsApp "stima_pronta" con:
    {{1}} = nome
    {{2}} = link PDF
    """

    if not ACCESS_TOKEN or not PHONE_NUMBER_ID:
        print("⚠️ WhatsApp disabilitato: variabili ambiente mancanti")
        return None

    dest = normalize_number(to_number)
    if not dest:
        print(f"⚠️ Numero non valido: {to_number}")
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

    try:
        r = requests.post(WHATSAPP_URL, headers=headers, json=payload)
        print("📨 WhatsApp template status:", r.status_code, r.text[:300])
        return r.json()
    except Exception as e:
        print("❌ Errore invio WhatsApp template:", e)
        return None
