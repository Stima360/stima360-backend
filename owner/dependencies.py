from fastapi import Cookie,HTTPException
from .enums import COOKIE_NAME
from .repository import get_session
def current_owner(stima360_owner_session:str|None=Cookie(None,alias=COOKIE_NAME)):
 try:return get_session(stima360_owner_session)
 except Exception:raise HTTPException(404,'Risorsa non trovata')
