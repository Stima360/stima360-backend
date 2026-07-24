#!/usr/bin/env python3
"""INTEGRATION 0.1 — read-only FastAPI route inventory."""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path
import os

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'INTEGRATION_ROUTE_INVENTORY.md'

def main():
    # Import only; no HTTP calls and no writes. Import can still require TEST env vars.
    from main import app
    rows=[]
    for r in app.routes:
        methods=sorted(getattr(r,'methods',[]) or [])
        path=getattr(r,'path',None)
        if not path: continue
        endpoint=getattr(r,'endpoint',None)
        module=getattr(endpoint,'__module__','') if endpoint else ''
        name=getattr(r,'name','')
        for m in methods or ['MOUNT']:
            if m in {'HEAD','OPTIONS'}: continue
            rows.append((m,path,name,module))
    rows.sort(key=lambda x:(x[1],x[0],x[2]))
    bykey=defaultdict(list)
    for row in rows: bykey[(row[0],row[1])].append(row)
    collisions={k:v for k,v in bykey.items() if len(v)>1}
    lines=['# INTEGRATION 0.1 — Inventario route FastAPI','',
           '> Import applicazione e introspezione route; nessuna richiesta HTTP e nessuna modifica.','',
           f'## Totale route/metodi: {len(rows)}','',
           '| Metodo | Path | Nome | Modulo |','|---|---|---|---|']
    for m,p,n,mod in rows: lines.append(f'| `{m}` | `{p}` | `{n}` | `{mod}` |')
    lines += ['', '## Collisioni metodo + path','']
    if collisions:
        for (m,p),vals in collisions.items():
            lines.append(f'- **{m} {p}**: '+', '.join(f'`{v[2]}` ({v[3]})' for v in vals))
    else: lines.append('- Nessuna collisione esatta rilevata.')
    lines += ['', '## Mount amministrativi attesi','']
    for p in ['/core-admin','/property-admin','/buy-admin','/match-admin','/flow-admin','/owner-admin','/owner']:
        found=any(row[1].rstrip('/')==p.rstrip('/') for row in rows)
        lines.append(f'- `{p}/`: {"presente" if found else "ASSENTE"}')
    OUT.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f'OK: {OUT.name}; collisioni={len(collisions)}')
    if collisions: raise SystemExit(2)
if __name__=='__main__': main()
