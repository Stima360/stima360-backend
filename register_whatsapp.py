import requests

PHONE_NUMBER_ID = "2573249773033415"  # <-- questo è corretto, è il tuo
CERT_PATH = "whatsapp_certificate.txt"

with open(CERT_PATH, "r") as f:
    certificate = f.read().strip()

url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/register"

payload = {
    "messaging_product": "whatsapp",
    "certificate": certificate
}

r = requests.post(url, json=payload)
print("STATUS:", r.status_code)
print("RISPOSTA:", r.text)
