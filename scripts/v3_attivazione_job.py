#!/usr/bin/env python3
"""ATTIVAZIONE DEI FOLLOW-UP V3 PER UN'AGENZIA - un solo job, sul servizio TEST.

    python scripts/v3_attivazione_job.py --agenzia 1 --operatore EMAIL --escludi 182 189
    python scripts/v3_attivazione_job.py --agenzia 1 --operatore EMAIL --escludi 182 189 --esegui

Senza `--esegui` fa SOLO il rapporto. Con `--esegui`, nell'ordine:
  1. riconosce le spunte del sito che HANNO le prove (funzione C3.4/C3.5:
     informativa valida alla data o prova scritta con la richiesta): consenso
     email con data, origine, informativa e prova `stima:<id>`;
  2. mette in PAUSA MANUALE le automazioni dei contatti esclusi (idempotente):
     niente iscrizioni, niente reiscrizione dal cron, storico intatto;
  3. un giro del motore delle sequenze: iscrive gli idonei (consenso email
     valido + proprietario o valutazione; al massimo 20 al giorno per
     agenzia) e accoda la prima email, dovuta subito;
  4. un giro del dispatcher con l'adapter SMTP REALE: ricontrolla il consenso
     prima di ogni invio e applica il limite dei destinatari se attivo;
  5. un secondo giro del motore: chi ha ricevuto la prima email passa alla
     seconda (cosi' il rapporto mostra M2 programmata);
  6. PREDISPONE l'acquisizione per chi NON ha un consenso email dimostrabile:
     un link personale del consenso (30 giorni), pronto nella scheda del
     contatto («Mostra QR»), senza inviare niente - lo consegna l'operatore;
     chi ha gia' un link aperto non ne riceve un altro;
  7. il rapporto: consensi validi, iscritti, email accettate dal server di
     posta, prossima email programmata, e i contatti ESCLUSI con il motivo.

NON registra consensi al posto di nessuno: un consenso senza prova resta «da
regolarizzare» e lo fa un operatore dal CRM (scheda o «Consensi email»), con
la sua dichiarazione. Nessun indirizzo viene stampato.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any


def _ctx_operatore(cur, email: str, agenzia: int):
    from operator_auth.context import OperatorContext
    cur.execute("SELECT u.id, m.role FROM operator_users u "
                "LEFT JOIN agency_memberships m ON m.operator_user_id = u.id AND m.agency_id = %s AND m.status = 'active' "
                "WHERE u.email_normalized = lower(btrim(%s)) AND u.status = 'active'", (agenzia, email))
    r = cur.fetchone()
    if r is None:
        raise SystemExit("operatore non trovato o non attivo")
    return OperatorContext(user_id=r["id"], agency_id=agenzia, role=r["role"] or "agency_admin",
                           is_platform_admin=False, session_id=None, auth_channel="script")


def rapporto(cur, ctx, esclusi: list[int]) -> dict[str, Any]:
    from communication import audience, consent_bulk, test_recipients
    quadro = consent_bulk.overview(ctx)
    cur.execute("SELECT j.id FROM communication_journeys j WHERE j.agency_id = %s AND j.status = 'active' "
                "AND j.journey_key = 'stima_lead' ORDER BY j.version DESC LIMIT 1", (ctx.require_agency(),))
    riga = cur.fetchone()
    journey_id = riga["id"] if riga else None
    cur.execute("""SELECT e.contact_id, e.status, e.next_step_no, e.next_action_at,
                          (SELECT count(*) FROM communication_messages m WHERE m.enrollment_id = e.id AND m.status = 'sent') AS inviate
                     FROM communication_enrollments e JOIN communication_journeys j ON j.id = e.journey_id
                    WHERE e.agency_id = %s AND j.journey_key = 'stima_lead' AND e.status IN ('active', 'paused')""",
                (ctx.require_agency(),))
    iscrizioni = {r["contact_id"]: dict(r) for r in cur.fetchall()}
    from datetime import datetime, timezone
    adesso = datetime.now(timezone.utc)
    contatti = []
    for c in quadro["contacts"]:
        cid = c["contact_id"]
        voce = {**c, "enrollment": iscrizioni.get(cid)}
        if cid in esclusi:
            voce["why"] = "escluso (pausa manuale richiesta dal titolare)"
        elif c["state"] == "valid" and cid not in iscrizioni and journey_id is not None:
            voce["why"] = audience.MOTIVI_ESCLUSIONE.get(
                audience.motivo_esclusione(cur, ctx, cid, now=adesso, journey_id=journey_id), "")
        elif c["state"] != "valid":
            voce["why"] = {"revoked": "revocato/disiscritto", "to_document": "consenso email da regolarizzare (prova mancante)"}[c["state"]]
        else:
            voce["why"] = None
        contatti.append(voce)
    consentiti = test_recipients.allowed()
    return {"journey_id": journey_id, "contatti": contatti, "counts": quadro["counts"], "totale": quadro["total"],
            "limite_destinatari": None if consentiti is None else len(consentiti)}


def stampa(r: dict[str, Any], esclusi: list[int]) -> None:
    print(f"contatti con email: {r['totale']} | consenso email valido: {r['counts']['valid']} | "
          f"da regolarizzare: {r['counts']['to_document']} | revocati: {r['counts']['revoked']}")
    print("sequenza V3 attiva:", r["journey_id"] or "NESSUNA")
    print("limite destinatari TEST:", "non attivo" if r["limite_destinatari"] is None
          else f"ATTIVO ({r['limite_destinatari']} indirizzi): gli altri restano in coda")
    iscritti = [c for c in r["contatti"] if c["enrollment"]]
    print(f"iscritti alla sequenza: {len(iscritti)}")
    for c in iscritti:
        e = c["enrollment"]
        quando = e["next_action_at"].strftime("%d/%m/%Y %H:%M") if e["next_action_at"] else "-"
        print(f"  contatto {c['contact_id']}: {e['status']} | inviate {e['inviate']} | prossima email {e['next_step_no'] or '-'} il {quando}")
    print("esclusi o non iscritti:")
    for c in r["contatti"]:
        if not c["enrollment"] and c["why"]:
            print(f"  contatto {c['contact_id']}: {c['why']}")


def esegui(cur, ctx, esclusi: list[int]) -> dict[str, Any]:
    from consent import site_recognition
    from communication import dispatcher, journey_service, journey_tick
    from operator_auth.context import SystemAgencyContext
    agenzia = ctx.require_agency()
    sistema = SystemAgencyContext(agency_id=agenzia, origin=dispatcher.DISPATCH_ORIGIN)
    riconosciuti = 0
    for ag, cid in site_recognition.pending_contacts(cur, agency_id=agenzia):
        try:
            riconosciuti += len(site_recognition.recognize(
                SystemAgencyContext(agency_id=ag, origin="public_stima"), cid)["granted"])
        except Exception as exc:  # noqa: BLE001 - un contatto non ferma il job
            print(f"  riconoscimento contatto {cid} non riuscito: {type(exc).__name__}")
    for cid in esclusi:
        try:
            journey_service.pause_automations(ctx, cid, reason="Escluso dalle automazioni per decisione del titolare.")
        except Exception as exc:  # noqa: BLE001
            print(f"  pausa contatto {cid} non riuscita: {type(exc).__name__}")
    tick = journey_tick.tick(sistema)
    invio = dispatcher.dispatch_batch(sistema, channel="email", provider=dispatcher.adapter_per("email"), limit=50)
    journey_tick.tick(sistema)   # chi ha ricevuto la prima email passa alla seconda
    link_pronti = _prepara_link(cur, ctx, esclusi)
    return {"riconosciuti": riconosciuti, "tick": tick, "dispatch": invio, "link_pronti": link_pronti}


def _prepara_link(cur, ctx, esclusi: list[int]) -> int:
    """Per chi non ha un consenso email dimostrabile: un link personale del
    consenso gia' pronto nella scheda. Nessun invio: il link lo consegna una
    persona. Chi ha gia' un link aperto non ne riceve un altro."""
    from communication import consent_bulk, consent_page
    quadro = consent_bulk.overview(ctx)
    creati = 0
    for c in quadro["contacts"]:
        if c["state"] != consent_bulk.STATO_DA_DOCUMENTARE or c["contact_id"] in esclusi or c["paused"]:
            continue
        cur.execute("""SELECT 1 FROM consent_links WHERE agency_id = %s AND contact_id = %s
                        AND superseded_at IS NULL AND disabled_at IS NULL AND expires_at > NOW()""",
                    (ctx.require_agency(), c["contact_id"]))
        if cur.fetchone():
            continue
        try:
            consent_page.generate_link(ctx, c["contact_id"])
            creati += 1
        except Exception as exc:  # noqa: BLE001 - un contatto non ferma il job
            print(f"  link per il contatto {c['contact_id']} non creato: {type(exc).__name__}")
    return creati


def main(argv: list[str]) -> int:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    a = argparse.ArgumentParser()
    a.add_argument("--agenzia", type=int, required=True)
    a.add_argument("--operatore", required=True, help="email dell'operatore che esegue (per lo storico)")
    a.add_argument("--escludi", type=int, nargs="*", default=[])
    a.add_argument("--esegui", action="store_true")
    args = a.parse_args(argv)
    conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
                            dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
                            password=os.environ["DB_PASSWORD"], cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            ctx = _ctx_operatore(cur, args.operatore, args.agenzia)
        conn.rollback()
        if args.esegui:
            with conn.cursor() as cur:
                esito = esegui(cur, ctx, args.escludi)
            conn.rollback()  # ogni funzione ha gia' committato per conto suo
            print(f"spunte del sito riconosciute: {esito['riconosciuti']}")
            t, d = esito["tick"], esito["dispatch"]
            print(f"giro del motore: iscritti {t['enrolled_active']} | fermati {t['enrolled_stopped']} | "
                  f"accodate {t['queued']} | errori {t['errors']}")
            print(f"giro di invio SMTP: prese {d['claimed']} | accettate dal server {d['sent']} | "
                  f"soppresse {d['suppressed']} | non riuscite {d['failed']} | incerte {d['indeterminate']}")
            print(f"link del consenso predisposti per chi e' da regolarizzare: {esito['link_pronti']}")
        with conn.cursor() as cur:
            r = rapporto(cur, ctx, args.escludi)
        conn.rollback()
        stampa(r, args.escludi)
        if not args.esegui:
            print("PROVA: nessuna modifica. Aggiungi --esegui per applicare.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
