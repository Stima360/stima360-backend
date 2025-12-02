import os
import requests

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO  = os.getenv("GITHUB_REPO")  # es: "Stima360/stima360-pdf"

def upload_pdf_to_github(pdf_path, pdf_filename):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("[GITHUB] Token o repo non configurati.")
        return None
    
    tag = "pdf-storage"
    release_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
    headers = {"Authorization": f"token {GITHUB_TOKEN}",
               "Accept": "application/vnd.github+json"}

    # 1) Leggi le release esistenti
    resp = requests.get(release_url, headers=headers)
    try:
        data = resp.json()
    except Exception:
        print("[GITHUB] Impossibile decodificare JSON dalla lista release:", resp.text)
        return None

    if resp.status_code != 200:
        print(f"[GITHUB] Errore list releases {resp.status_code}: {data}")
        return None

    release = next((rel for rel in data if rel.get("tag_name") == tag), None)

    # 2) Se non esiste, crea la release
    if not release:
        payload = {
            "tag_name": tag,
            "name": "PDF Storage",
            "body": "Archivio PDF Stima360"
        }
        resp_create = requests.post(release_url, json=payload, headers=headers)
        try:
            release = resp_create.json()
        except Exception:
            print("[GITHUB] Impossibile decodificare JSON dalla create release:", resp_create.text)
            return None

        if resp_create.status_code not in (200, 201):
            print(f"[GITHUB] Errore create release {resp_create.status_code}: {release}")
            return None

    # 3) Verifica che ci sia upload_url
    upload_url = release.get("upload_url")
    if not upload_url:
        print("[GITHUB] release senza upload_url:", release)
        return None

    upload_url = upload_url.replace("{?name,label}", f"?name={pdf_filename}")

    # 4) Carica il PDF come asset
    try:
        with open(pdf_path, "rb") as f:
            data_bin = f.read()
    except Exception as e:
        print("[GITHUB] Errore lettura file PDF:", e)
        return None

    headers_upload = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type": "application/pdf",
        "Accept": "application/vnd.github+json",
    }
    resp_upload = requests.post(upload_url, headers=headers_upload, data=data_bin)
    try:
        upload_res = resp_upload.json()
    except Exception:
        print("[GITHUB] Impossibile decodificare JSON upload asset:", resp_upload.text)
        return None

    if resp_upload.status_code not in (200, 201):
        print(f"[GITHUB] Errore upload asset {resp_upload.status_code}: {upload_res}")
        return None

    url = upload_res.get("browser_download_url")
    print("[GITHUB] Upload OK, url:", url)
    return url
