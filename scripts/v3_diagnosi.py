#!/usr/bin/env python3
"""FOLLOW-UP V3 - diagnosi IN SOLA LETTURA dell'ambiente in cui gira.

Da eseguire nella Shell Render del servizio web TEST (usa le variabili DB_*
del servizio). Non scrive niente: sessione `readonly`, transazione annullata.
Non stampa indirizzi email ne' numeri: solo id, stati, conteggi, date e
host dei link (mai i token).

    python scripts/v3_diagnosi.py                 # quadro generale
    python scripts/v3_diagnosi.py 182 185 186     # + i contatti indicati
    python scripts/v3_diagnosi.py --stima 431     # + a chi e' agganciata una stima

Risponde a: quale commit gira; dove puntano i link (PUBLIC_BASE_URL contro
il servizio); se il cron ha l'identita' tecnica; se il limite destinatari e'
attivo e per quanti indirizzi; quali sequenze sono attive; per ogni contatto
chiesto: consensi email, iscrizione, pausa manuale, messaggi e loro esito;
quante spunte del sito aspettano ancora una prova.
"""
from __future__ import annotations

import os
import sys


def _host(url: str | None) -> str:
    if not url:
        return "(non impostata)"
    return url.split("://", 1)[-1].split("/", 1)[0].split("@")[-1].split(":", 1)[0].lower()


def ambiente() -> list[str]:
    base = os.getenv("PUBLIC_BASE_URL")
    servizio = (os.getenv("RENDER_EXTERNAL_HOSTNAME") or "").lower()
    host = _host(base)
    righe = [
        f"commit in esecuzione: {os.getenv('RENDER_GIT_COMMIT') or '(sconosciuto)'}",
        f"servizio (RENDER_EXTERNAL_HOSTNAME): {servizio or '(non Render)'}",
        f"host dei link (PUBLIC_BASE_URL): {host}",
    ]
    if servizio and host.endswith(".onrender.com") and host != servizio:
        righe.append("  !! i link di conferma/disiscrizione puntano a UN ALTRO servizio: i clic non arrivano qui")
    elif base and servizio and host == servizio:
        righe.append("  ok: i link puntano a questo servizio")
    token = os.getenv("COMMUNICATION_CRON_TOKEN") or ""
    righe.append(f"identita' tecnica del cron (COMMUNICATION_CRON_TOKEN): "
                 f"{'configurata' if len(token) >= 32 else 'NON configurata (' + str(len(token)) + ' caratteri)'}; "
                 f"agenzie consentite: {os.getenv('COMMUNICATION_CRON_AGENCY_IDS') or '(nessuna)'}")
    try:
        from communication import test_recipients
        consentiti = test_recipients.allowed()
    except Exception:  # noqa: BLE001 - fuori dal repository
        consentiti = None
    righe.append("limite destinatari marketing (COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS): "
                 + (f"ATTIVO, {len(consentiti)} indirizzi ammessi" if consentiti is not None else "non attivo (nessun limite)"))
    righe.append(f"double opt-in del sito (SITE_CONSENT_DOUBLE_OPT_IN): {os.getenv('SITE_CONSENT_DOUBLE_OPT_IN') or 'off'}")
    righe.append(f"SMTP configurato: {'si' if os.getenv('SMTP_USER') and os.getenv('SMTP_PASS') else 'NO'} "
                 f"({os.getenv('SMTP_HOST', 'mail.stima360.it')}:{os.getenv('SMTP_PORT', '587')})")
    return righe


Q_SEQUENZE = """SELECT j.agency_id, j.journey_key, j.version, j.status,
                       (SELECT count(*) FROM communication_enrollments e WHERE e.journey_id = j.id AND e.status = 'active') AS attive,
                       (SELECT count(*) FROM communication_enrollments e WHERE e.journey_id = j.id AND e.status = 'paused') AS in_pausa
                  FROM communication_journeys j ORDER BY 1, 2, 3"""

Q_CONTATTO = """SELECT c.id, c.agency_id, c.status, c.deleted_at IS NOT NULL AS cestino, c.archived_at IS NOT NULL AS archiviato,
                       c.email_normalized IS NOT NULL AS ha_email,
                       (SELECT count(*) FROM communication_automation_controls k WHERE k.contact_id = c.id AND k.paused) AS pausa_manuale,
                       (SELECT count(*) FROM contact_roles r WHERE r.contact_id = c.id AND r.role IN ('owner','seller')
                           AND (r.valid_to IS NULL OR r.valid_to >= NOW())) AS ruoli_proprietario,
                       (SELECT count(*) FROM leads l JOIN lead_stime ls ON ls.lead_id = l.id WHERE l.contact_id = c.id) AS stime_collegate
                  FROM contacts c WHERE c.id = %s"""

Q_CONSENSI = """SELECT channel, decision, source, evidence_type, decided_at
                  FROM consent_events WHERE contact_id = %s AND purpose = 'marketing' ORDER BY decided_at, id"""

Q_PROIEZIONE = """SELECT channel, status, granted_at FROM contact_channel_consents WHERE contact_id = %s ORDER BY channel"""

Q_ISCRIZIONI = """SELECT e.id, e.status, e.paused_source, e.pause_ref, e.stop_reason, e.next_step_no,
                         e.next_action_at, e.trigger_sent_at, j.version
                    FROM communication_enrollments e JOIN communication_journeys j ON j.id = e.journey_id
                   WHERE e.contact_id = %s ORDER BY e.id"""

Q_MESSAGGI = """SELECT id, communication_type, reason_code, step_no, status, scheduled_at, sent_at, attempt_count,
                       suppressed_reason, error_code,
                       substring(rendered_body from 'https?://[^/[:space:]]+') AS host_del_link
                  FROM communication_messages WHERE contact_id = %s ORDER BY id"""

Q_RICHIESTE = """SELECT id, origin, email_status, submitted_at, confirm_expires_at, confirmed_at
                   FROM consent_submissions WHERE contact_id = %s ORDER BY id"""

Q_STIMA = """SELECT s.id, s.agency_id, s.email IS NOT NULL AS ha_email, s.telefono IS NOT NULL AS ha_telefono,
                    s.consenso_marketing_testo IS NOT NULL AS prova_testo,
                    s.consenso_marketing_privacy_sha256 IS NOT NULL AS prova_impronta,
                    (SELECT array_agg(DISTINCT l.contact_id) FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id
                      WHERE ls.stima_id = s.id) AS contatti_collegati,
                    (SELECT array_agg(DISTINCT c.id) FROM contacts c
                      WHERE c.agency_id = s.agency_id AND c.deleted_at IS NULL
                        AND c.email_normalized = lower(btrim(s.email))) AS contatti_con_stessa_email,
                    (SELECT array_agg(DISTINCT c.id) FROM contacts c
                      WHERE c.agency_id = s.agency_id AND c.deleted_at IS NULL
                        AND c.phone_normalized = regexp_replace(s.telefono, '[^0-9]', '', 'g')) AS contatti_con_stesso_telefono
               FROM stime s WHERE s.id = %s"""

Q_SPUNTE_IN_ATTESA = """SELECT count(DISTINCT ce.contact_id) FROM consent_events ce
                         WHERE ce.purpose = 'marketing' AND ce.channel IS NULL AND ce.source = 'public_stima'
                           AND ce.decision = 'granted'
                           AND NOT EXISTS (SELECT 1 FROM contact_channel_consents g
                                            WHERE g.contact_id = ce.contact_id AND g.channel = 'email' AND g.status = 'granted')"""


def _tabella(cur, nome: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{nome}",))
    return bool(cur.fetchone()[0])


def quadro(cur, contatti: list[int], stime: list[int]) -> list[str]:
    righe = []
    cur.execute("SELECT current_database()")
    righe.append(f"database: {cur.fetchone()[0]}")
    righe.append("")
    righe.append("== SEQUENZE ==")
    if _tabella(cur, "communication_journeys"):
        cur.execute(Q_SEQUENZE)
        for a, k, v, st, att, pau in cur.fetchall():
            righe.append(f"agenzia {a} | {k} v{v} | {st:8} | attive {att} | in pausa {pau}")
    else:
        righe.append("(tabelle delle sequenze assenti: migrazioni non applicate)")
    cur.execute(Q_SPUNTE_IN_ATTESA)
    righe.append(f"contatti con la spunta del sito ancora SENZA consenso email riconosciuto: {cur.fetchone()[0]}")
    for cid in contatti:
        righe += ["", f"== CONTATTO {cid} =="]
        cur.execute(Q_CONTATTO, (cid,))
        r = cur.fetchone()
        if r is None:
            righe.append("non esiste")
            continue
        _, ag, st, cest, arch, email, pausa, ruoli, stime_c = r
        righe.append(f"agenzia {ag} | stato {st} | cestino {cest} | archiviato {arch} | ha email {email} | "
                     f"pausa manuale {'SI' if pausa else 'no'} | ruoli proprietario {ruoli} | stime collegate {stime_c}")
        if not (ruoli or stime_c):
            righe.append("  -> non proprietario e senza valutazione collegata: NON entra nel V3")
        cur.execute(Q_CONSENSI, (cid,))
        eventi = cur.fetchall()
        righe.append("consensi marketing (eventi): " + (
            "; ".join(f"{ch or 'SENZA CANALE'} {dec} da {src}/{ev} il {d:%d/%m/%Y %H:%M}" for ch, dec, src, ev, d in eventi)
            or "nessuno"))
        cur.execute(Q_PROIEZIONE, (cid,))
        proiezione = cur.fetchall()
        righe.append("consenso per canale (proiezione): " + (
            "; ".join(f"{ch} {st}" + (f" dal {g:%d/%m/%Y}" if g else "") for ch, st, g in proiezione) or "nessuno"))
        if not any(ch == "email" and st == "granted" for ch, st, _ in proiezione):
            righe.append("  -> nessun consenso EMAIL valido: NON entra nel V3 finche' non e' riconosciuto o registrato")
        if _tabella(cur, "consent_submissions"):
            cur.execute(Q_RICHIESTE, (cid,))
            for i, orig, est, sub, exp, conf in cur.fetchall():
                righe.append(f"richiesta di conferma {i} ({orig}): {est} | inviata {sub:%d/%m %H:%M} | "
                             f"scade {exp:%d/%m %H:%M} | confermata {conf.strftime('%d/%m %H:%M') if conf else 'MAI'}")
        if _tabella(cur, "communication_enrollments"):
            cur.execute(Q_ISCRIZIONI, (cid,))
            for i, st, ps, ref, stop, passo, quando, inizio, ver in cur.fetchall():
                righe.append(f"iscrizione {i} (v{ver}): {st}"
                             + (f" per {ps} [{ref}]" if ps else "") + (f" motivo {stop}" if stop else "")
                             + (f" | prossimo passo {passo} il {quando:%d/%m/%Y %H:%M}" if passo and quando else "")
                             + f" | inizio {inizio:%d/%m/%Y}")
            cur.execute(Q_MESSAGGI, (cid,))
            for i, tipo, motivo, passo, st, sch, sent, tent, sopp, err, host in cur.fetchall():
                righe.append(f"messaggio {i} {tipo}/{motivo}" + (f" passo {passo}" if passo else "")
                             + f": {st} | previsto {sch:%d/%m %H:%M}"
                             + (f" | inviato {sent:%d/%m %H:%M}" if sent else "") + f" | tentativi {tent}"
                             + (f" | soppresso: {sopp}" if sopp else "") + (f" | errore: {err}" if err else "")
                             + (f" | link verso {host}" if host else ""))
    for sid in stime:
        righe += ["", f"== STIMA {sid} =="]
        cur.execute(Q_STIMA, (sid,))
        r = cur.fetchone()
        if r is None:
            righe.append("non esiste")
            continue
        _, ag, he, ht, pt, pi, coll, se, st = r
        righe.append(f"agenzia {ag} | email {he} | telefono {ht} | prova C3.5 testo {pt} impronta {pi}")
        righe.append(f"contatti collegati tramite lead: {coll or 'NESSUNO (stima non agganciata)'}")
        righe.append(f"contatti con la stessa email: {se or 'nessuno'} | con lo stesso telefono: {st or 'nessuno'}")
        if se and st and set(se) != set(st):
            righe.append("  !! email e telefono della stima appartengono a contatti DIVERSI (identity_conflict): "
                         "il ponte non la aggancia")
        elif not se and st:
            righe.append("  !! email nuova ma telefono di un contatto esistente: la richiesta e' stata attribuita a quel "
                         "contatto, che conserva la SUA email")
    return righe


def main(argv: list[str]) -> int:
    import psycopg2
    contatti, stime, prossimo_stima = [], [], False
    for a in argv:
        if a == "--stima":
            prossimo_stima = True
        elif a.isdigit():
            (stime if prossimo_stima else contatti).append(int(a))
    conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
                            dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
                            password=os.environ["DB_PASSWORD"])
    conn.set_session(readonly=True)
    try:
        with conn.cursor() as cur:
            righe = quadro(cur, contatti, stime)
    finally:
        conn.rollback()
        conn.close()
    print("== AMBIENTE ==")
    print("\n".join(ambiente()))
    print()
    print("\n".join(righe))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
