import requests
import json

ACCESS_TOKEN = "IL_TUO_TOKEN"
PHONE_NUMBER_ID = "IL_TUO_PHONE_NUMBER_ID"

WHATSAPP_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

def send_whatsapp_template(to_number: str, template_name: str, language: str = "it"):
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language}
        }
    }

    response = requests.post(WHATSAPP_URL, headers=headers, data=json.dumps(payload))
    return response.json()
