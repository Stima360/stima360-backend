#!/usr/bin/env python3
"""INTEGRATION 0.1 — read-only schema/migration/FK inventory for TEST only."""
from __future__ import annotations
import os, re, json
from pathlib import Path
from database import get_connection

ROOT=Path(__file__).resolve().parent
MIG=ROOT/'migrations'
OUT_MIG=ROOT/'INTEGRATION_MIGRATIONS_INVENTORY.md'
OUT_SCHEMA=ROOT/'INTEGRATION_SCHEMA_MAP.md'

def guard_test():
    db=(os.getenv('DB_NAME') or os.getenv('POSTGRES_DB') or '').lower()
    url=(os.getenv('DATABASE_URL') or '').lower()
    if 'test' not in db and 'test' not in url:
        raise SystemExit('BLOCCATO: database non identificato come TEST')

def migration_files():
    ups=sorted([p for p in MIG.glob('[0-9][0-9][0-9]_*.sql') if not p.stem.endswith('_down')])
    return ups

def expected_from_sql(files):
    result={}
    for p in files:
        sql=p.read_text(encoding='utf-8',errors='replace')
        creates=re.findall(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?"?([a-zA-Z_][\w]*)"?',sql,re.I)
        alters=re.findall(r'ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:public\.)?"?([a-zA-Z_][\w]*)"?',sql,re.I)
        result[p.name]={'creates':sorted(set(creates)),'alters':sorted(set(alters)),'rollback':(MIG/(p.stem+'_down.sql')).exists()}
    return result

def main():
    guard_test(); files=migration_files(); expected=expected_from_sql(files)
    conn=get_connection(); conn.autocommit=False
    try:
        cur=conn.cursor(); cur.execute('SET TRANSACTION READ ONLY')
        cur.execute("""SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name""")
        tables=[r[0] for r in cur.fetchall()]
        cur.execute("""SELECT table_name,column_name,data_type,is_nullable,column_default
                       FROM information_schema.columns WHERE table_schema='public'
                       ORDER BY table_name,ordinal_position""")
        columns=cur.fetchall()
        cur.execute("""SELECT tc.constraint_name,tc.constraint_type,tc.table_name,
                              kcu.column_name,ccu.table_name AS foreign_table_name,
                              ccu.column_name AS foreign_column_name
                       FROM information_schema.table_constraints tc
                       LEFT JOIN information_schema.key_column_usage kcu
                         ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
                       LEFT JOIN information_schema.constraint_column_usage ccu
                         ON tc.constraint_name=ccu.constraint_name AND tc.table_schema=ccu.table_schema
                       WHERE tc.table_schema='public'
                       ORDER BY tc.table_name,tc.constraint_type,tc.constraint_name,kcu.ordinal_position""")
        constraints=cur.fetchall()
        # Presence of a migration ledger is evidence; table existence alone is not.
        cur.execute("""SELECT table_name FROM information_schema.tables
                       WHERE table_schema='public' AND table_name IN
                       ('alembic_version','schema_migrations','migration_history','migrations')""")
        ledgers=[r[0] for r in cur.fetchall()]
        conn.rollback()
    finally:
        conn.close()

    expected_tables=sorted({t for v in expected.values() for t in v['creates']})
    missing=sorted(set(expected_tables)-set(tables)); extra=sorted(set(tables)-set(expected_tables))
    lines=['# INTEGRATION 0.1 — Inventario migrazioni','',
           '> Verifica read-only. Una migrazione non è classificata come applicata solo perché esiste una tabella.','',
           '## Ordine file rilevato','']
    for i,p in enumerate(files,1):
        e=expected[p.name]
        lines.append(f"{i}. `{p.name}` — crea: {', '.join(e['creates']) or 'nessuna'}; altera: {', '.join(e['alters']) or 'nessuna'}; rollback: {'presente' if e['rollback'] else 'ASSENTE'}")
    lines += ['', '## Evidenza di applicazione', '']
    if ledgers:
        lines.append('Ledger migrazioni individuati: '+', '.join(f'`{x}`' for x in ledgers)+'. Verificare i record manualmente: lo script non presume il formato del ledger.')
    else:
        lines.append('**Nessun ledger migrazioni standard individuato.** Stato di applicazione: `NON DIMOSTRABILE` dai soli metadati dello schema.')
    lines += ['',f'- Tabelle attese dai file: {len(expected_tables)}',f'- Tabelle attese mancanti nel reale: {missing or "nessuna"}',f'- Tabelle reali non create dai file 001–009: {len(extra)} (legacy o modifiche manuali da classificare)', '',
              '## Modifiche manuali / divergenze','',
              '- Le tabelle extra non sono automaticamente anomalie: possono appartenere al legacy.',
              '- Colonne/vincoli reali non riconducibili ai file vanno classificati come legacy o modifica manuale dopo confronto puntuale.']
    OUT_MIG.write_text('\n'.join(lines)+'\n',encoding='utf-8')

    bytable={}
    for t,c,d,n,default in columns: bytable.setdefault(t,[]).append((c,d,n,default))
    fk=[r for r in constraints if r[1]=='FOREIGN KEY']
    sl=['# INTEGRATION 0.1 — Schema reale TEST','', '> Generato con query `information_schema` in transazione read-only.','',
        f'## Tabelle reali ({len(tables)})','', ', '.join(f'`{t}`' for t in tables),'', '## Colonne','']
    for t in tables:
        sl.append(f'### `{t}`')
        for c,d,n,default in bytable.get(t,[]): sl.append(f'- `{c}` — {d}; nullable={n}; default={default}')
        sl.append('')
    sl += ['## Foreign key','']
    for name,typ,t,col,ft,fc in fk: sl.append(f'- `{name}`: `{t}.{col}` → `{ft}.{fc}`')
    if not fk: sl.append('- Nessuna foreign key rilevata.')
    sl += ['', '## Vincoli','']
    for name,typ,t,col,ft,fc in constraints:
        if typ!='FOREIGN KEY': sl.append(f'- `{t}` — {typ} `{name}`' + (f' su `{col}`' if col else ''))
    OUT_SCHEMA.write_text('\n'.join(sl)+'\n',encoding='utf-8')
    print(f'OK: {OUT_MIG.name}, {OUT_SCHEMA.name}')
if __name__=='__main__': main()
