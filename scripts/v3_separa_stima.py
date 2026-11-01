#!/usr/bin/env python3
"""Separa una valutazione agganciata al contatto SBAGLIATO (stesso telefono,
email e nome diversi) e mette entrambi i contatti fuori dalle automazioni.

Caso reale: la stima 431 (Giorgio Canzoni) attribuita al contatto 182
(Giorgio Censori) perche' il ponte riconosceva dal numero di telefono.

    python scripts/v3_separa_stima.py --stima 431 --da-contatto 182 --operatore EMAIL_OPERATORE
    python scripts/v3_separa_stima.py --stima 431 --da-contatto 182 --operatore EMAIL_OPERATORE --esegui

Senza `--esegui` mostra SOLO cosa farebbe. Con `--esegui`, in UNA transazione:
  1. crea un contatto nuovo dai dati della stima (nome, cognome, email,
     telefono), nell'agenzia della stima;
  2. sposta sul contatto nuovo i lead del contatto sbagliato collegati SOLO a
     questa stima (un lead collegato anche ad altre stime resta dov'e');
  3. registra sul contatto nuovo la spunta marketing della stima, con la
     stessa data e la stessa prova (`stima:<id>`), se sul contatto sbagliato
     esiste; la riga sbagliata resta nello storico (il registro e' in sola
     aggiunta) e viene annotata nell'esito, non cancellata;
  4. mette in PAUSA MANUALE le automazioni di ENTRAMBI i contatti (iscrizioni
     aperte in pausa, messaggi in coda annullati): il cron non li reiscrive
     finche' una persona non toglie la pausa. Consensi e storico restano.

Usa le variabili DB_* del servizio. Non stampa indirizzi ne' numeri.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any


def piano(cur, stima_id: int, da_contatto: int) -> dict[str, Any]:
    cur.execute("SELECT id, agency_id, nome, cognome, email, telefono FROM stime WHERE id = %s", (stima_id,))
    stima = cur.fetchone()
    if stima is None:
        raise SystemExit(f"stima {stima_id}: non esiste")
    cur.execute("SELECT id, agency_id, email_normalized, phone_normalized FROM contacts WHERE id = %s", (da_contatto,))
    contatto = cur.fetchone()
    if contatto is None or contatto["agency_id"] != stima["agency_id"]:
        raise SystemExit(f"contatto {da_contatto}: non esiste nell'agenzia della stima")
    cur.execute("""SELECT l.id, (SELECT count(*) FROM lead_stime x WHERE x.lead_id = l.id) AS stime_del_lead
                     FROM leads l JOIN lead_stime ls ON ls.lead_id = l.id
                    WHERE ls.stima_id = %s AND l.contact_id = %s""", (stima_id, da_contatto))
    lead = [dict(r) for r in cur.fetchall()]
    if not lead:
        raise SystemExit(f"la stima {stima_id} non e' collegata al contatto {da_contatto}: niente da separare")
    cur.execute("""SELECT id, decided_at, notice_id FROM consent_events
                    WHERE contact_id = %s AND purpose = 'marketing' AND channel IS NULL
                      AND source = 'public_stima' AND evidence_type = 'stima' AND evidence_ref = %s
                      AND decision = 'granted' ORDER BY id""", (da_contatto, str(stima_id)))
    spunte = [dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT id, channel FROM consent_events WHERE contact_id = %s AND purpose = 'marketing'
                    AND channel IS NOT NULL AND evidence_type = 'stima' AND evidence_ref = %s""",
                (da_contatto, str(stima_id)))
    riconosciute = [dict(r) for r in cur.fetchall()]
    email_norm = (stima["email"] or "").strip().lower() or None
    stessa_email = email_norm is not None and email_norm == contatto["email_normalized"]
    return {"stima": dict(stima), "contatto": dict(contatto), "lead": lead, "spunte": spunte,
            "riconosciute": riconosciute, "email_normalized": email_norm, "stessa_email": stessa_email}


def esegui(cur, p: dict[str, Any], operatore_id: int) -> dict[str, Any]:
    from core.normalization import normalize_email, normalize_phone
    from consent import service as consent_service
    from operator_auth.context import OperatorContext, SystemAgencyContext
    from communication import journey_service

    stima, vecchio = p["stima"], p["contatto"]
    agenzia = stima["agency_id"]
    nome = (stima["nome"] or "").strip() or None
    cognome = (stima["cognome"] or "").strip() or None
    display = " ".join(x for x in (nome, cognome) if x) or f"Contatto stima {stima['id']}"
    cur.execute(
        """INSERT INTO contacts (contact_type, first_name, last_name, display_name, email, email_normalized,
                                 phone, phone_normalized, source, status, agency_id, notes)
           VALUES ('person', %s, %s, %s, %s, %s, %s, %s, 'public_stima', 'active', %s, %s) RETURNING id""",
        (nome, cognome, display, (stima["email"] or "").strip() or None, normalize_email(stima["email"]),
         (stima["telefono"] or "").strip() or None, normalize_phone(stima["telefono"]), agenzia,
         f"Separato dal contatto {vecchio['id']} (stesso telefono, persona diversa): stima {stima['id']}."))
    nuovo = cur.fetchone()["id"]
    spostati = []
    for l in p["lead"]:
        if l["stime_del_lead"] == 1:
            cur.execute("UPDATE leads SET contact_id = %s, updated_at = NOW() WHERE id = %s", (nuovo, l["id"]))
            spostati.append(l["id"])
    sistema = SystemAgencyContext(agency_id=agenzia, origin="public_stima")
    rispunte = []
    for s in p["spunte"]:
        esito = consent_service.record_optional_grant(
            sistema, granted=True, contact_id=nuovo, purpose="marketing", source="public_stima",
            actor_type="subject", decided_at=s["decided_at"], notice_id=s["notice_id"],
            evidence_type="stima", evidence_ref=str(stima["id"]),
            note=f"Spunta della stima {stima['id']} riportata dal contatto {vecchio['id']} (separazione).",
            idempotency_key=f"public_stima:{stima['id']}:marketing:separato_da:{vecchio['id']}", cur=cur)
        rispunte.append(esito.get("event", {}).get("id"))
    cur.execute("SELECT role FROM agency_memberships WHERE operator_user_id = %s AND agency_id = %s AND status = 'active'",
                (operatore_id, agenzia))
    riga = cur.fetchone()
    ctx = OperatorContext(user_id=operatore_id, agency_id=agenzia, role=riga["role"] if riga else "agency_admin",
                          is_platform_admin=False, session_id=None, auth_channel="script")
    pause = {}
    for cid in (vecchio["id"], nuovo):
        pause[cid] = journey_service.pause_automations(
            ctx, cid, reason=f"Esclusione dalle automazioni (separazione stima {stima['id']}).", cur=cur)["paused"]
    return {"nuovo_contatto": nuovo, "lead_spostati": spostati, "spunte_riportate": rispunte, "pause": pause}


def main(argv: list[str]) -> int:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    a = argparse.ArgumentParser()
    a.add_argument("--stima", type=int, required=True)
    a.add_argument("--da-contatto", type=int, required=True)
    a.add_argument("--operatore", required=True, help="email dell'operatore che esegue (per lo storico)")
    a.add_argument("--esegui", action="store_true")
    args = a.parse_args(argv)
    conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
                            dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
                            password=os.environ["DB_PASSWORD"], cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM operator_users WHERE email_normalized = lower(btrim(%s)) AND status = 'active'",
                        (args.operatore,))
            op = cur.fetchone()
            if op is None:
                raise SystemExit("operatore non trovato o non attivo")
            p = piano(cur, args.stima, args.da_contatto)
            print(f"stima {args.stima} (agenzia {p['stima']['agency_id']}) collegata al contatto {args.da_contatto} "
                  f"tramite {len(p['lead'])} lead; spunte marketing della stima sul contatto: {len(p['spunte'])}; "
                  f"consensi per canale gia' riconosciuti da quella spunta: {len(p['riconosciute'])}")
            if p["stessa_email"]:
                raise SystemExit("la stima ha la STESSA email del contatto: non e' una persona diversa, non separo")
            if p["riconosciute"]:
                print("  !! la spunta e' gia' stata riconosciuta come consenso sul contatto sbagliato: resta nello "
                      "storico (registro in sola aggiunta); va valutata una revoca su quel contatto")
            if p["spunte"]:
                print("  nota: la spunta resta anche sul contatto sbagliato (registro in sola aggiunta): con le prove "
                      "potrebbe essere riconosciuta li'; il contatto e' comunque fuori dalle automazioni")
            if not args.esegui:
                print("PROVA: nessuna modifica. Aggiungi --esegui per applicare.")
                conn.rollback()
                return 0
            esito = esegui(cur, p, op["id"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"FATTO: nuovo contatto {esito['nuovo_contatto']}; lead spostati {esito['lead_spostati']}; "
          f"spunte riportate {esito['spunte_riportate']}; automazioni in pausa: {esito['pause']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
