#!/usr/bin/env python3
"""INTEGRATION 0.1 — read-only residual test-data inventory."""
from __future__ import annotations
import os,re
from pathlib import Path
from database import get_connection
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'INTEGRATION_TEST_DATA_REPORT.md'
PATTERNS=('E2E_','TEST_','FLOW01_','E2E_FLOW','OWNER_E2E','MATCH_','BUY_','PROPERTY_','CORE_')

def guard_test():
    db=(os.getenv('DB_NAME') or os.getenv('POSTGRES_DB') or '').lower(); url=(os.getenv('DATABASE_URL') or '').lower()
    if 'test' not in db and 'test' not in url: raise SystemExit('BLOCCATO: database non TEST')

def main():
    guard_test(); conn=get_connection(); conn.autocommit=False
    findings=[]
    try:
        cur=conn.cursor(); cur.execute('SET TRANSACTION READ ONLY')
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
        tables=[r[0] for r in cur.fetchall()]
        for table in tables:
            cur.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",(table,))
            textcols=[r[0] for r in cur.fetchall() if r[1] in ('text','character varying','character')]
            if not textcols: continue
            clauses=[]; params=[]
            for col in textcols:
                for pat in PATTERNS:
                    clauses.append(f'CAST("{col}" AS TEXT) ILIKE %s'); params.append('%'+pat+'%')
            q=f'SELECT COUNT(*) FROM "{table}" WHERE '+ ' OR '.join(clauses)
            cur.execute(q,params); count=cur.fetchone()[0]
            if count: findings.append((table,count,textcols))
        conn.rollback()
    finally: conn.close()
    lines=['# INTEGRATION 0.1 — Dati test residui','',
           '> Inventario esclusivamente read-only. Nessuna cancellazione proposta viene eseguita.','',
           '## Pattern cercati','', ', '.join(f'`{p}`' for p in PATTERNS),'',
           '## Risultati','']
    if findings:
        for t,c,cols in findings: lines.append(f'- `{t}`: {c} record potenzialmente test; colonne testuali ispezionate: {", ".join(cols)}')
    else: lines.append('- Nessun record riconoscibile tramite i pattern testuali configurati.')
    lines += ['', '## Limiti','',
              '- I record test senza prefisso riconoscibile non possono essere distinti automaticamente.',
              '- Prima di qualsiasi pulizia servono query mirate, verifica FK e approvazione separata.',
              '- Questo report non contiene né esegue `DELETE`, `UPDATE`, `INSERT`, DDL o cleanup.']
    OUT.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f'OK: {OUT.name}; tabelle_con_residui={len(findings)}')
if __name__=='__main__': main()
