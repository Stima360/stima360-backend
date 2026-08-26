import os
import secrets

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials


_admin_security = HTTPBasic(auto_error=False)


def require_admin(
    credentials: HTTPBasicCredentials | None = Depends(_admin_security),
) -> str:
    admin_user = os.getenv("ADMIN_USER")
    admin_pass = os.getenv("ADMIN_PASS")

    if not admin_user or not admin_pass:
        raise HTTPException(
            status_code=503,
            detail="Servizio amministrativo non disponibile",
        )

    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Non autorizzato",
            headers={"WWW-Authenticate": 'Basic realm="STIMA360 Admin"'},
        )

    user_ok = secrets.compare_digest(credentials.username, admin_user)
    password_ok = secrets.compare_digest(credentials.password, admin_pass)

    if not (user_ok and password_ok):
        raise HTTPException(
            status_code=401,
            detail="Non autorizzato",
            headers={"WWW-Authenticate": 'Basic realm="STIMA360 Admin"'},
        )

    return credentials.username
