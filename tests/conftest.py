import sys,types,contextlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
core=types.ModuleType('core');core.__path__=[];db=types.ModuleType('core.database');ex=types.ModuleType('core.exceptions')
@contextlib.contextmanager
def core_cursor(commit=False): raise RuntimeError;yield
db.core_cursor=core_cursor
class E(Exception):pass
ex.NotFoundError=type('NotFoundError',(E,),{});ex.ConflictError=type('ConflictError',(E,),{})
sys.modules.setdefault('core',core);sys.modules.setdefault('core.database',db);sys.modules.setdefault('core.exceptions',ex)
