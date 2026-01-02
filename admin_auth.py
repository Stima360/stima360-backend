from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # poi lo restringiamo
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/admin/check")
def admin_check(data: dict):
    admin_user = os.getenv("ADMIN_USER")
    admin_pass = os.getenv("ADMIN_PASS")

    if not admin_user or not admin_pass:
        raise HTTPException(
            status_code=500,
            detail="ADMIN credentials not set on server"
        )

    if (
        data.get("user") == admin_user and
        data.get("password") == admin_pass
    ):
        return {"ok": True}

    raise HTTPException(status_code=401, detail="Unauthorized")
