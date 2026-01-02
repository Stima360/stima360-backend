from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # poi lo stringiamo
    allow_methods=["*"],
    allow_headers=["*"],
)

ADMIN_USER = os.getenv("ADMIN_USER")
ADMIN_PASS = os.getenv("ADMIN_PASS")

@app.post("/admin/check")
def admin_check(data: dict):
    if (
        data.get("user") == ADMIN_USER and
        data.get("password") == ADMIN_PASS
    ):
        return {"ok": True}

    raise HTTPException(status_code=401, detail="Unauthorized")
