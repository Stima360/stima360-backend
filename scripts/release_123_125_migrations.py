"""Rilascio TEST delle migrazioni 123-125 tramite Render One-Off Job.

Avvolge il runner ufficiale (`scripts/p26_migrate.py`) con le verifiche di
sicurezza del metodo gia' collaudato per la 118. Non contiene SQL di schema:
le migrazioni le applica soltanto il runner, con il suo registro.

    python scripts/release_123_125_migrations.py check  --expected-host <host TEST>
    python scripts/release_123_125_migrations.py apply  --expected-host <host TEST>
    python scripts/release_123_125_migrations.py verify --expected-host <host TEST>

check   sola lettura: identita' del database, runner e file attesi, 115-122
        registrate con i checksum attesi, nessuna migrazione estranea
        registrata o pendente, status e plan del runner.
apply   tutte le verifiche di `check`, poi `p26_migrate.py apply`, poi `verify`.
        Rifiuta se 123-125 sono gia' registrate (un secondo apply non parte).
verify  sola lettura: 123-125 registrate con checksum up/down attesi e down
        disponibile, tabelle, trigger e vincoli presenti, nulla di pendente.

Esce con codice 0 solo se tutto e' superato. Nessuna credenziale viene stampata.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "p26_migrate.py"
OPERATOR = "stefano.maggiorini"
EXPECTED_DB = "stima360_db_test"
RUNNER_SHA = "0a4fce2ec08d5bdc8dfb50a2312313bd51089c2f043aecc87f33dec73b72249a"
PRE = {
    "115_agency_logos": "793bc413333ffa74a9602fcc53d08084dfaca001ae1579622621f9d32cf86c8e",
    "116_property_valuations": "4ac259c83dbfa9aaf9a8fa956cb5d40d08428551028d796ff71a5a2e2919860c",
    "117_property_valuation_pdfs": "558432a719968a17b4b7ad8f16c7191fe990c4486a0786e50e76e9432f8c0ed2",
    "118_whatsapp_inbound": "6abf4103a6d97eaa7aa253483a22a147281b17953bc145ef88c7cf0017a0234e",
    "119_whatsapp_auto_reply_simulation": "bc6d057be6ddbb6c6a530a7be861831db510067d1361b85054f82d9d57a16fab",
    "120_property_visit_booking": "08e659361a0feeec2d95d4e7ff42484535298db5a7e45bafe566bcc40de8d9a6",
    "121_visit_sheets": "04065b278e82af7aa8e1a1d5bc438ce4231e0b0a14b03bdae6999ce3f1ac3962",
    "122_signature_requests": "c81361bc4b3a9e5d2b4c977d1315d08e7a43bbddefc633f83cb29ba037bc4c03",
}
NEW = {
    "123_whatsapp_auto_reply_send": (
        "e2ef4fcf67fe40b6d3e98c3e39a1446d7b91fff87f24551dc434c819732d5117",
        "b133220c95cd4c8ee0fcee39ca9e86cf90ef5a2eab8c60343891b98be6c0cc00"),
    "124_do_not_contact_and_link_revocation": (
        "60928f8a41d8dad4c410d81bbacedc76815ab91c0b108395864b036b3785eaf7",
        "70e6c80f558cc3d84000deb119bc7f64bc22bc4e0d39cbc5d019f3495875070b"),
    "125_signature_provider_readiness": (
        "2047ee3e160b6a586e3fba1bf5058975bab68c95a31b90f1fe227eaa15cdc888",
        "392f97873c1d7e55470e749d57e756cb80a264d945a4dfd9d595042da8fb26fd"),
}
TABLES = ("whatsapp_auto_reply_dispatches", "contact_do_not_contact", "signature_provider_events")
TRIGGERS = ("trg_whatsapp_auto_reply_dispatches_guard", "trg_contact_do_not_contact_guard",
            "trg_signature_provider_events_append_only")
CONSTRAINTS = ("property_visit_booking_links_issued_unq", "property_visit_booking_links_revocation_note_chk",
               "signature_requests_simulation_env_chk", "signature_provider_events_unq")


class Gate:
    def __init__(self):
        self.problems: list[str] = []

    def need(self, ok: bool, msg: str) -> None:
        print(("OK   " if ok else "STOP ") + msg, flush=True)
        if not ok:
            self.problems.append(msg)

    def stop_if_failed(self, what: str) -> None:
        if self.problems:
            print(f"ESITO: CONTROLLI NON SUPERATI - {what}", flush=True)
            sys.exit(1)


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def files_gate(g: Gate, *, allow_later: bool = False) -> None:
    """`allow_later`: in `verify` le migrazioni successive alla 125 non fermano
    la verifica delle 123-125."""
    g.need(_sha(RUNNER) == RUNNER_SHA, "runner ufficiale scripts/p26_migrate.py con lo SHA-256 verificato")
    mig = ROOT / "migrations"
    for v, s in PRE.items():
        g.need(_sha(mig / f"{v}.sql") == s, f"{v}.sql nel deploy con checksum atteso")
    for v, (up, down) in NEW.items():
        g.need(_sha(mig / f"{v}.sql") == up and _sha(mig / f"{v}_down.sql") == down,
               f"{v} (up + down) nel deploy con checksum attesi")
    extra = sorted(p.name for p in mig.glob("*.sql") if p.name[:3].isdigit() and int(p.name[:3]) > 125)
    if allow_later:
        print("     file successivi alla 125 nel deploy: " + (", ".join(extra) or "nessuno"), flush=True)
    else:
        g.need(extra == [], "nessun file di migrazione oltre la 125 nel deploy")


def _connect():
    import psycopg2

    conn = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT", "5432"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"))
    conn.set_session(readonly=True)
    return conn


def database_gate(g: Gate, expected_host: str, mode: str) -> None:
    host = os.getenv("DB_HOST") or ""
    g.need(os.getenv("DB_NAME") == EXPECTED_DB, f"DB_NAME del servizio = {EXPECTED_DB}")
    g.need(bool(expected_host) and "INCOLLA" not in expected_host, "host TEST atteso indicato")
    g.need(host == expected_host, "DB_HOST del servizio = host TEST atteso")
    g.stop_if_failed("ambiente non verificato, nessuna connessione aperta")
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT current_database(), pg_is_in_recovery(), current_setting('transaction_read_only')")
        db, recovery, ro = cur.fetchone()
        g.need(db == EXPECTED_DB, f"current_database = {db}")
        g.need(recovery is False, "database primario (non in recovery)")
        g.need(ro == "on", "sessione di controllo in sola lettura")
        cur.execute("SELECT version, checksum_up, checksum_down, down_available, applied_by_operator, applied_at, "
                    "rolled_back_at FROM schema_migrations WHERE version >= '115' ORDER BY version")
        ledger = {r[0]: r for r in cur.fetchall()}
        for v, s in PRE.items():
            row = ledger.get(v)
            g.need(row is not None and row[1].strip() == s and row[6] is None, f"{v} applicata con checksum atteso")
        others = sorted(v for v in ledger if v not in PRE and v not in NEW)
        g.need(others == [], "nessuna migrazione estranea registrata oltre la 122")
        cur.execute("SELECT " + ", ".join(f"to_regclass('{t}') IS NOT NULL" for t in TABLES))
        present = cur.fetchone()
        if mode == "pre":
            g.need(all(v not in ledger for v in NEW), "123-125 non ancora registrate")
            g.need(not any(present), "tabelle di 123-125 assenti")
        else:
            for v, (up, down) in NEW.items():
                row = ledger.get(v)
                g.need(row is not None and row[1].strip() == up and (row[2] or "").strip() == down
                       and row[3] is True and row[6] is None,
                       f"{v} registrata con checksum up/down attesi e down disponibile")
                if row is not None:
                    print(f"     applicata da: {row[4]} il {row[5].isoformat()}", flush=True)
            g.need(all(present), "tre tabelle di 123-125 presenti")
            cur.execute("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname = ANY(%s)",
                        (list(TRIGGERS),))
            g.need(cur.fetchone()[0] == len(TRIGGERS), "tre trigger di guardia presenti")
            cur.execute("SELECT count(*) FROM pg_constraint WHERE conname = ANY(%s)", (list(CONSTRAINTS),))
            g.need(cur.fetchone()[0] == len(CONSTRAINTS), "vincoli di unicita', revoca e ambiente presenti")
        conn.rollback()
    finally:
        conn.close()


def runner(*args: str) -> tuple[int, str]:
    out = subprocess.run([sys.executable, str(RUNNER), *args], cwd=ROOT, capture_output=True, text=True)
    return out.returncode, out.stdout + out.stderr


def runner_gate(g: Gate, *, expect_pending: tuple[str, ...]) -> None:
    code, status = runner("status")
    print("== status del runner (sola lettura)", flush=True)
    print("\n".join(line for line in status.splitlines()
                    if line.lstrip().startswith(("pending", "PROBLEM")) or "rolled back" in line
                    or line.lstrip().startswith(("applied      11", "applied      12", "database"))), flush=True)
    pending = tuple(line.split()[1] for line in status.splitlines() if line.lstrip().startswith("pending"))
    g.need(code == 0, "status del runner superato")
    g.need("PROBLEM" not in status and "rolled back" not in status, "registro senza problemi ne' annullamenti")
    g.need(pending == expect_pending,
           "pendenti esattamente: " + (", ".join(expect_pending) or "nessuna") + f" (trovate: {', '.join(pending) or 'nessuna'})")
    if expect_pending:
        code, plan = runner("plan", "--operator", OPERATOR)
        print("== plan del runner (solo validazione statica)", flush=True)
        print("\n".join(plan.splitlines()[-2:]), flush=True)
        g.need(code == 0 and "static validation: OK" in plan, "plan del runner superato")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("check", "apply", "verify"))
    parser.add_argument("--expected-host", required=True)
    args = parser.parse_args()
    g = Gate()
    print(f"== rilascio 123-125 su {EXPECTED_DB}: {args.mode} (operatore {OPERATOR})", flush=True)
    files_gate(g, allow_later=args.mode == "verify")
    g.stop_if_failed("file del deploy non verificati")
    if args.mode == "verify":
        database_gate(g, args.expected_host, "post")
        runner_gate(g, expect_pending=())
        g.stop_if_failed("verifica finale")
        print("VERIFICA FINALE SUPERATA: 123-125 applicate e registrate", flush=True)
        return 0
    database_gate(g, args.expected_host, "pre")
    runner_gate(g, expect_pending=tuple(NEW))
    g.stop_if_failed("apply NON eseguito" if args.mode == "apply" else "sola lettura")
    if args.mode == "check":
        print("ESITO: CONTROLLI SUPERATI (sola lettura, nessuna migrazione applicata)", flush=True)
        return 0
    print("== apply (runner ufficiale, registro migrazioni)", flush=True)
    code, out = runner("apply", "--operator", OPERATOR)
    print(out, flush=True)
    if code != 0:
        print("ESITO: APPLY NON RIUSCITO - eseguire verify e non ripetere senza analisi", flush=True)
        return 1
    g2 = Gate()
    database_gate(g2, args.expected_host, "post")
    runner_gate(g2, expect_pending=())
    g2.stop_if_failed("verifica dopo l'apply")
    print("VERIFICA FINALE SUPERATA: 123-125 applicate e registrate", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
