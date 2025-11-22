import smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os

SMTP_HOST = os.getenv("SMTP_HOST", "mail.stima360.it")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))  # STARTTLS
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")

DEST = os.getenv("TEST_TO", SMTP_USER)

print("🔍 Test invio email")
print("SMTP_HOST:", SMTP_HOST)
print("SMTP_PORT:", SMTP_PORT)
print("SMTP_USER:", SMTP_USER)
print("DEST:", DEST)

msg = MIMEMultipart()
msg["From"] = SMTP_USER
msg["To"] = DEST
msg["Subject"] = "TEST SMTP da Render"
msg.attach(MIMEText("<b>Email di test da Render</b>", "html"))

try:
    server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
    server.ehlo()
    server.starttls()
    server.login(SMTP_USER, SMTP_PASS)
    server.sendmail(SMTP_USER, DEST, msg.as_string())
    server.quit()
    print("✅ EMAIL INVIATA CON SUCCESSO")
except Exception as e:
    print("❌ ERRORE SMTP:", e)
