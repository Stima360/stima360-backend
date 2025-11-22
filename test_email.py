import os
import yagmail
from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))  # 465 = SSL, 587 = STARTTLS (ma qui usiamo SSL)
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)

def _client():
    # smtp_ssl=True → connessione sicura (porta 465)
    return yagmail.SMTP(user=SMTP_USER, password=SMTP_PASS, host=SMTP_HOST, port=SMTP_PORT, smtp_ssl=True)

def invia_email_test(destinatario: str):
    print(f"📧 Test invio tramite {SMTP_HOST}:{SMTP_PORT} come {SMTP_USER}…")
    html = "<h2>Test OK ✅</h2><p>Se vedi questa mail, SMTP funziona.</p>"
    with _client() as yag:
        yag.send(
            to=destinatario,
            subject="Test Stima360 ✅",
            contents=[yagmail.raw(html)]
        )
    print("✅ Email di test inviata!")

def invia_email_con_pdf(destinatario: str, subject: str, html_body: str, pdf_fs_path: str):
    """
    Invia una mail HTML con il PDF in ALLEGATO (non solo link).
    pdf_fs_path: percorso su disco, es. backend/reports/stima_48.pdf
    """
    assert os.path.isfile(pdf_fs_path), f"File non trovato: {pdf_fs_path}"
    filename = os.path.basename(pdf_fs_path)

    with _client() as yag:
        yag.send(
            to=destinatario,
            subject=subject,
            contents=[yagmail.raw(html_body)],
            attachments=[pdf_fs_path]  # 👈 allegato vero
        )
    print(f"✅ Inviata a {destinatario} con allegato {filename}")

if __name__ == "__main__":
    # 👉 prova veloce:
    invia_email_test("Giorgiocens@hotmail.it")
    # Esempio invio con PDF:
    # invia_email_con_pdf(
    #     "Giorgiocens@hotmail.it",
    #     "🏡 Stima360 – La tua valutazione",
    #     "<h2 style='color:#0077cc;margin:0;'>La tua stima è pronta</h2><p>In allegato trovi il PDF.</p>",
    #     "backend/reports/stima_48.pdf"
    # )
