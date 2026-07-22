import sys, types

if 'psycopg2' not in sys.modules:
    psy=types.ModuleType('psycopg2'); extras=types.ModuleType('psycopg2.extras')
    class Json:
        def __init__(self, adapted): self.adapted=adapted
    extras.Json=Json; psy.extras=extras
    sys.modules['psycopg2']=psy; sys.modules['psycopg2.extras']=extras

core=types.ModuleType('core'); db=types.ModuleType('core.database'); exc=types.ModuleType('core.exceptions'); repo=types.ModuleType('core.repository')
class NotFoundError(Exception): pass
class ConflictError(Exception): pass
class ValidationError(Exception): pass
exc.NotFoundError=NotFoundError; exc.ConflictError=ConflictError; exc.ValidationError=ValidationError
class DummyCM:
    def __enter__(self): raise RuntimeError('DB not available in unit tests')
    def __exit__(self,*a): return False
def core_cursor(*,commit=False): return DummyCM()
db.core_cursor=core_cursor
repo.create_task=lambda data: {'id':1,**data}
core.database=db; core.exceptions=exc; core.repository=repo
sys.modules.setdefault('core',core); sys.modules.setdefault('core.database',db); sys.modules.setdefault('core.exceptions',exc); sys.modules.setdefault('core.repository',repo)
