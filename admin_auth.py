from fastapi import FastAPI, HTTPException
import os

app = FastAPI()

ADMIN_USER = os.getenv("ADMIN_USER")
ADMIN_PASS = os.getenv("ADMIN_PASS")

@app.post("/admin/login")
def admin_login(data: dict):
    if data.get("user") == ADMIN_USER and data.get("password") == ADMIN_PASS:
        return {"ok": True}
    raise HTTPException(status_code=401, detail="Accesso negato")
