import requests
import json
import os

# 🔐 Legge token e phone_number_id dalle variabili di ambiente (Render)
ACCESS_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_ID")
VERSION = os.getenv("WHATSAPP_API_VERSION", "v19.0")

WHATSAPP_URL = f"https://graph.facebook.com/{VERSION}/{PHONE_NUMBER_ID}/messages"

def normalize_number(number: str) -> str:
    """
    Normalizza numero in formato internazionale:
    - tiene solo cifre
    - se manca prefisso, aggiunge +39
    """
    if not number:
        return None
    digits = ''.join(ch for ch in number if ch.isdigit())
    
    if digits.startswith("39"):
        return digits  # già corretto

    return "39" + digits.lstrip("0")


def send_whatsapp_template(to_number: str, template_name: str, language: str = "it"):
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
            "name": template_name,     # 👈 NOME DEL TEMPLATE APPROVATO SU META
            "language": {"code": language}
        }
    }

    try:
        r = requests.post(WHATSAPP_URL, headers=headers, json=payload)
        print("📨 WhatsApp template status:", r.status_code, r.text[:300])
        return r.json()
    except Exception as e:
        print("❌ Errore invio WhatsApp template:", e)
        return None
