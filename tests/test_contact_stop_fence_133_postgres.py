"""MIGRATION 133 - la guardia T6 dei contatti senza scorrere tutti i contatti, su
PostgreSQL VERO (fixture `mondo`: tutte le migration dal runner, 133 compresa).

    G1  STESSO INSIEME: per migliaia di righe scritte (NEW/OLD con contatto, referente,
        agenzia, lead, stima, immobile; valori veri delle due agenzie, assenti,
        inesistenti, incrociati) l'insieme della 133 e' identico a quello della 097,
        con la select della 097 presa TALE E QUALE dal file della migration
    G2  STESSI LOCK: una scrittura che coinvolge un contatto tenuto da un'altra
        transazione aspetta (per lead, per immobile, per stima, per id); una che non
        lo coinvolge passa
    G3  DOWN: torna la funzione della 097 con lo stesso testo; la 133 si riapplica
    G4  TEMPI prima/dopo: 5.200 contatti spostati nel Cestino a blocchi con la
        guardia della 097 e con quella della 133 (stessi contatti, stessi blocchi)
"""
from __future__ import annotations

import random
import re
import time
from pathlib import Path

import pytest

from tests.test_delete_arch_0_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _collega, _immobile, _lead, _q, completo, mondo, operatori,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

MIGRAZIONI = Path(__file__).resolve().parent.parent / "migrations"
UP = (MIGRAZIONI / "133_contact_stop_fence_indexed.sql").read_text(encoding="utf-8")
DOWN = (MIGRAZIONI / "133_contact_stop_fence_indexed_down.sql").read_text(encoding="utf-8")


def _select_097() -> str:
    """La select dei contatti della 097, copiata dal file: da «SELECT DISTINCT» a «ORDER BY»."""
    testo = (MIGRAZIONI / "097_tappa6_request_followup.sql").read_text(encoding="utf-8")
    funzione = testo[testo.index("CREATE FUNCTION communication_contact_stop_fence()"):]
    trovato = re.search(r"(SELECT DISTINCT c\.id,c\.agency_id FROM contacts c WHERE.*?ORDER BY c\.id,c\.agency_id)",
                        funzione, re.S)
    assert trovato, "la select della 097 non e' piu' quella attesa"
    return trovato.group(1)


RIFERIMENTO = ("WITH x AS (SELECT %(data)s::jsonb AS data, %(old)s::jsonb AS old_data) "
               + _select_097().replace("FROM contacts c WHERE", "FROM x, contacts c WHERE", 1))
NUOVA = ("SELECT t.id, t.agency_id FROM communication_contact_stop_fence_targets(%(data)s::jsonb, %(old)s::jsonb) t "
         "ORDER BY t.id, t.agency_id")


def _mondo_collegato(m):
    """Contatti, lead, stime collegate, immobili con proprietari, nelle due agenzie."""
    import json

    ids = {"contatti": {1: [], 2: []}, "lead": {1: [], 2: []}, "immobili": {1: [], 2: []}, "stime": {1: [], 2: []}}
    for agenzia in (1, 2):
        for i in range(6):
            cid = _q(m, "INSERT INTO contacts (agency_id, display_name, first_name) VALUES (%s, %s, 'G') RETURNING id",
                     (agenzia, f"Guardia {agenzia}-{i}"))[0][0]
            ids["contatti"][agenzia].append(cid)
        for cid in ids["contatti"][agenzia][:4]:
            ids["lead"][agenzia].append(_lead(m, cid, agenzia=agenzia))
        for i in range(3):
            pid = _q(m, "INSERT INTO properties (agency_id, title, property_type, city) VALUES (%s, 'G', 'apartment', "
                        "'Guardiopoli') RETURNING id", (agenzia,))[0][0]
            ids["immobili"][agenzia].append(pid)
            _q(m, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, %s)",
               (pid, ids["contatti"][agenzia][i], ("owner", "seller", "tenant")[i]))
        for lid in ids["lead"][agenzia][:2]:
            sid = _q(m, "INSERT INTO stime (agency_id, nome, cognome, email, telefono) VALUES (%s, 'G', 'G', "
                        "'g@x.test', '3330000000') RETURNING id", (agenzia,))[0][0]
            ids["stime"][agenzia].append(sid)
            _q(m, "INSERT INTO lead_stime (lead_id, stima_id) VALUES (%s, %s)", (lid, sid))
    return ids, json


def test_g1_stesso_insieme_della_097_su_migliaia_di_righe(mondo):
    m = mondo
    ids, json = _mondo_collegato(m)
    tutti = lambda chiave: ids[chiave][1] + ids[chiave][2] + [None, 999999999]  # noqa: E731
    pool = {"contact_id": tutti("contatti"), "owner_contact_id": tutti("contatti"),
            "agency_id": [1, 2, None, 999], "lead_id": tutti("lead"), "stima_id": tutti("stime"),
            "stima_id_snapshot": tutti("stime"), "property_id": tutti("immobili")}
    caso = random.Random(133)

    def riga():
        chiavi = caso.sample(list(pool), caso.randint(0, len(pool)))
        return {k: caso.choice(pool[k]) for k in chiavi}

    non_vuoti = 0
    with m["conn"].cursor() as cur:
        for _ in range(3000):
            data, old = riga(), (riga() if caso.random() < 0.5 else {})
            parametri = {"data": json.dumps(data), "old": json.dumps(old)}
            cur.execute(RIFERIMENTO, parametri)
            atteso = [tuple(r) for r in cur.fetchall()]
            cur.execute(NUOVA, parametri)
            ottenuto = [tuple(r) for r in cur.fetchall()]
            assert ottenuto == atteso, (data, old, atteso, ottenuto)
            non_vuoti += bool(atteso)
    assert non_vuoti > 1000                  # la prova confronta davvero insiemi pieni, non solo vuoti


def test_g2_la_guardia_blocca_ancora_i_contatti_coinvolti(mondo):
    m = mondo
    ids, _ = _mondo_collegato(m)
    tenuto, libero = ids["contatti"][1][0], ids["contatti"][1][3]
    lead_tenuto, lead_libero = ids["lead"][1][0], ids["lead"][1][3]
    immobile_tenuto = ids["immobili"][1][0]              # proprietario: il contatto tenuto
    stima_tenuta = ids["stime"][1][0]                    # collegata al lead del contatto tenuto
    psycopg2 = m["psycopg2"]
    blocco = psycopg2.connect(m["dsn"])
    scrittore = psycopg2.connect(m["dsn"])
    try:
        with blocco.cursor() as cur:
            cur.execute("SELECT id FROM contacts WHERE id = %s FOR UPDATE", (tenuto,))
        with scrittore.cursor() as cur:
            cur.execute("SET lock_timeout = '300ms'")
            scrittore.commit()
        attese = {
            "per lead": ("UPDATE leads SET stage = stage WHERE id = %s", (lead_tenuto,)),
            "per id": ("UPDATE leads SET stage = stage WHERE contact_id = %s", (tenuto,)),
            "per immobile": ("UPDATE property_contacts SET role = role WHERE property_id = %s", (immobile_tenuto,)),
            "per stima": ("UPDATE lead_stime SET stima_id = stima_id WHERE stima_id = %s", (stima_tenuta,)),
        }
        for nome, (sql, params) in attese.items():
            with scrittore.cursor() as cur:
                with pytest.raises(psycopg2.errors.LockNotAvailable):
                    cur.execute(sql, params)
            scrittore.rollback()
            assert nome
        with scrittore.cursor() as cur:                    # un contatto non coinvolto: passa
            cur.execute("UPDATE leads SET stage = stage WHERE id = %s", (lead_libero,))
        scrittore.rollback()
        assert libero != tenuto
    finally:
        blocco.rollback()
        blocco.close()
        scrittore.close()


def test_g3_la_down_rimette_la_097_e_la_133_si_riapplica(mondo):
    m = mondo
    conn = m["psycopg2"].connect(m["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute(DOWN)
            cur.execute("SELECT pg_get_functiondef('communication_contact_stop_fence'::regproc)")
            definizione = cur.fetchone()[0]
            assert "OR EXISTS(SELECT 1 FROM leads l LEFT JOIN lead_stime ls" in definizione
            assert "communication_contact_stop_fence_targets" not in definizione
            cur.execute("SELECT to_regprocedure('communication_contact_stop_fence_targets(jsonb,jsonb)')")
            assert cur.fetchone()[0] is None
            cur.execute(UP)
            cur.execute("SELECT pg_get_functiondef('communication_contact_stop_fence'::regproc)")
            assert "communication_contact_stop_fence_targets" in cur.fetchone()[0]
            # i nove trigger della 097 restano associati alla stessa funzione
            cur.execute("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'trg_t6_%%_fence' "
                        "AND tgfoid = 'communication_contact_stop_fence'::regproc")
            assert cur.fetchone()[0] == 9
    finally:
        conn.rollback()                                   # nulla resta: DDL dentro la transazione
        conn.close()


def _ctx(m):
    from operator_auth.context import OperatorContext
    uid, agenzia, ruolo = m["ruoli"]["owner_a"]
    return OperatorContext(user_id=uid, agency_id=agenzia, role=ruolo, is_platform_admin=False,
                           session_id=None, auth_channel="operator_session")


def _sposta_tutti(m, ids):
    from core import contact_bulk
    inizio, spostati = time.monotonic(), 0
    for i in range(0, len(ids), contact_bulk.BLOCK_MAX):
        esito = contact_bulk.trash_many(_ctx(m), ids[i:i + contact_bulk.BLOCK_MAX], "duplicate")
        assert esito["remaining"] == []
        spostati += len(esito["trashed"])
    return time.monotonic() - inizio, spostati


def _ripristina_tutti(m, ids):
    from core import contact_bulk
    for i in range(0, len(ids), contact_bulk.BLOCK_MAX):
        contact_bulk.restore_many(_ctx(m), ids[i:i + contact_bulk.BLOCK_MAX])


def _installa(m, sql):
    conn = m["psycopg2"].connect(m["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()


def test_g4_tempi_prima_e_dopo_con_5200_contatti(mondo, capsys):
    m = mondo
    ids = sorted(r[0] for r in _q(m, "INSERT INTO contacts (agency_id, display_name, first_name) "
                                     "SELECT 1, 'Tempi ' || g, 'Tempi' FROM generate_series(1, 5200) g RETURNING id"))
    _q(m, "INSERT INTO contacts (agency_id, display_name, first_name) "
          "SELECT 2, 'Altra ' || g, 'Altra' FROM generate_series(1, 300) g")
    totale = _q(m, "SELECT count(*) FROM contacts")[0][0]
    try:
        _installa(m, DOWN)                                 # la guardia della 097
        prima, n_prima = _sposta_tutti(m, ids)
        _ripristina_tutti(m, ids)
    finally:
        _installa(m, UP)                                   # sempre di nuovo la 133
    dopo, n_dopo = _sposta_tutti(m, ids)
    _ripristina_tutti(m, ids)
    with capsys.disabled():
        print(f"\n[G4] {len(ids)} contatti spostati nel Cestino ({totale} contatti nel database): "
              f"097 {prima:.1f}s ({prima / len(ids) * 1000:.1f} ms/contatto) -> 133 {dopo:.1f}s "
              f"({dopo / len(ids) * 1000:.1f} ms/contatto), {prima / max(dopo, 0.001):.1f}x")
    assert n_prima == n_dopo == len(ids)
    assert dopo * 3 < prima, (prima, dopo)
