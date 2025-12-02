import os
import base64
import requests

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO  = os.getenv("GITHUB_REPO")  # es: "Stima360/stima360-pdf"
GITHUB_USER  = os.getenv("GITHUB_USER")

def upload_pdf_to_github(pdf_path, pdf_filename):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("GitHub token o repo non configurati.")
        return None
    
    # 1. Crea (o usa) la release
    tag = "pdf-storage"
    release_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}

    # Controlla se la release esiste
    r = requests.get(release_url, headers=headers).json()
    release = next((rel for rel in r if rel["tag_name"] == tag), None)

    if not release:
        payload = {
            "tag_name": tag,
            "name": "PDF Storage",
            "body": "Archivio PDF Stima360"
        }
        release = requests.post(release_url, json=payload, headers=headers).json()

    upload_url = release["upload_url"].replace("{?name,label}", f"?name={pdf_filename}")

    # 2. Carica PDF
    with open(pdf_path, "rb") as f:
        data = f.read()

    headers.update({"Content-Type": "application/pdf"})

    upload_res = requests.post(upload_url, headers=headers, data=data).json()

    # URL pubblico del PDF
    return upload_res.get("browser_download_url")
