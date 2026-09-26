"""A30-2P - backfill `stima_inspections` -> `appointments` su PostgreSQL vero.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Riusa il mondo di prova e il
database usa-e-getta di `test_a30_2_appointments_postgres.py` (stesso schema:
catena LMC-15 + 072); questo modulo non apre connessioni proprie.

Le righe LMC-15 si creano con le funzioni PUBBLICHE di LMC-15 (lo scrittore
di oggi), poi il backfill le porta nell'Agenda. Si prova: il mapping stato per
stato, l'idempotenza, che `stima_inspections` e la timeline non cambino di un
byte, gli orfani, la corsa a vuoto, e - con la proiezione accesa SOLO dentro
il test - che una richiesta importata, fissata dall'Agenda, riusi la stessa
riga LMC-15 invece di crearne una seconda.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.test_a30_2_appointments_postgres import (  # noqa: F401 - fixture
    DSN, db, futuro, http, mondo, ore, proiezione_accesa, proiezione_spenta)

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare A30-2P")


# ---------------------------------------------------------------------------
# strumenti
# ---------------------------------------------------------------------------

def _lmc15():
    from acquisition import repository as lmc15
    return lmc15


def _fotografia(mondo):
    """`stima_inspections` e timeline, riga per riga: devono restare identiche."""
    ispezioni = mondo["sql"]("SELECT to_jsonb(i) FROM stima_inspections i ORDER BY id")
    timeline = mondo["sql"]("SELECT to_jsonb(t) FROM seller_timeline_events t ORDER BY id")
    return [r[0] for r in ispezioni], [r[0] for r in timeline]


def _backfill(apply=True):
    from appointments import backfill
    from core.database import core_cursor
    with core_cursor(commit=apply) as (_, cur):
        return backfill.run_backfill(cur, apply=apply)


def _census():
    from appointments import backfill
    from core.database import core_cursor
    with core_cursor(commit=False) as (_, cur):
        return backfill.census(cur)


def _quattro_ispezioni(mondo):
    """Una per stato LMC-15, piu' un completato registrato a posteriori."""
    lmc15, a, g = _lmc15(), mondo["a"], mondo["giorgio"]
    aperta = lmc15.create_inspection(a, stima_id=mondo["stima"], scheduled_for=ore(9),
                                     actor_user_id=g)
    fatta = lmc15.create_inspection(a, stima_id=mondo["stima"], scheduled_for=ore(11),
                                    actor_user_id=g)
    fatta = lmc15.complete_inspection(a, inspection_id=fatta["id"],
                                      completed_at=ore(11, 40, s=5, us=321),
                                      actor_user_id=g)
    annullata = lmc15.create_inspection(a, stima_id=mondo["stima"], scheduled_for=ore(15),
                                        actor_user_id=g)
    annullata = lmc15.cancel_inspection(a, inspection_id=annullata["id"],
                                        reason="Rinviato dal cliente", actor_user_id=g)
    posteriori = lmc15.create_completed_inspection(a, stima_id=mondo["stima"],
                                                   completed_at=ore(17, 10),
                                                   actor_user_id=g)
    return {"aperta": aperta, "fatta": fatta, "annullata": annullata,
            "posteriori": posteriori}


def _appuntamento_di(mondo, ispezione_id):
    righe = mondo["sql"](
        "SELECT to_jsonb(a) FROM appointments a WHERE stima_inspection_id = %s",
        (ispezione_id,))
    assert len(righe) == 1, righe
    return righe[0][0]


def _istante(valore):
    return datetime.fromisoformat(valore)


# ---------------------------------------------------------------------------
# A - CENSUS E MAPPING
# ---------------------------------------------------------------------------

def test_01_census_prima_e_dopo(mondo):
    _quattro_ispezioni(mondo)
    prima = _census()
    assert prima["totale"] == 4 and prima["con_stima"] == 4
    assert (prima["scheduled"], prima["completed"], prima["cancelled"]) == (1, 2, 1)
    assert prima["gia_collegati"] == 0 and prima["gia_backfill"] == 0
    assert prima["da_importare"] == 4 and prima["inizio_ricostruito"] == 1
    assert prima["da_importare_per_stato"] == {"scheduled": 1, "completed": 2, "cancelled": 1}
    _backfill()
    dopo = _census()
    assert dopo["gia_collegati"] == 4 and dopo["gia_backfill"] == 4
    assert dopo["da_importare"] == 0


def test_02_mapping_stato_per_stato(mondo):
    isp = _quattro_ispezioni(mondo)
    esito = _backfill()
    assert esito["inserted"] == 4 and esito["start_reconstructed"] == 1
    assert esito["by_status"] == {"requested": 1, "completed": 2, "cancelled": 1}

    a = _appuntamento_di(mondo, isp["aperta"]["id"])
    assert a["status"] == "requested" and a["assigned_user_id"] is None
    assert _istante(a["start_at"]) == ore(9) and _istante(a["end_at"]) == ore(10)
    assert a["appointment_type"] == "inspection" and a["stima_id"] == mondo["stima"]
    assert a["agency_id"] == mondo["a"] and a["created_by_user_id"] is None
    assert a["source"] == "stima_inspections_backfill"
    assert a["source_record_id"] == f"stima_inspections:{isp['aperta']['id']}"
    assert a["contact_id"] is None and a["lead_id"] is None and a["property_id"] is None

    f = _appuntamento_di(mondo, isp["fatta"]["id"])
    assert f["status"] == "completed" and f["assigned_user_id"] is None
    assert _istante(f["completed_at"]) == ore(11, 40, s=5, us=321)       # esatto
    assert _istante(f["start_at"]) == ore(11)

    c = _appuntamento_di(mondo, isp["annullata"]["id"])
    assert c["status"] == "cancelled" and c["cancelled_reason"] == "Rinviato dal cliente"
    assert _istante(c["cancelled_at"]) == isp["annullata"]["cancelled_at"]

    p = _appuntamento_di(mondo, isp["posteriori"]["id"])
    assert p["status"] == "completed"
    assert _istante(p["start_at"]) == ore(17, 10) == _istante(p["completed_at"])


def test_03_un_evento_created_per_riga_attore_sistema(mondo):
    _quattro_ispezioni(mondo)
    esito = _backfill()
    eventi = mondo["sql"](
        "SELECT e.appointment_id, e.event_type, e.from_status, e.to_status, e.actor_user_id "
        "FROM appointment_events e ORDER BY e.appointment_id")
    assert sorted(r[0] for r in eventi) == sorted(esito["inserted_ids"])
    for _, tipo, da, a, attore in eventi:
        assert tipo == "created" and da is None and attore is None


# ---------------------------------------------------------------------------
# B - IDEMPOTENZA E NON-INVASIVITA'
# ---------------------------------------------------------------------------

def test_10_idempotente_eseguito_piu_volte(mondo):
    _quattro_ispezioni(mondo)
    primo = _backfill()
    righe = mondo["sql"]("SELECT count(*) FROM appointments")[0][0]
    eventi = mondo["sql"]("SELECT count(*) FROM appointment_events")[0][0]
    for _ in range(3):
        altro = _backfill()
        assert altro["candidates"] == 0 and altro["inserted"] == 0
    assert primo["inserted"] == 4
    assert mondo["sql"]("SELECT count(*) FROM appointments")[0][0] == righe == 4
    assert mondo["sql"]("SELECT count(*) FROM appointment_events")[0][0] == eventi == 4


def test_11_stima_inspections_e_timeline_non_cambiano_di_un_byte(mondo):
    _quattro_ispezioni(mondo)
    prima = _fotografia(mondo)
    _backfill()
    _backfill()
    assert _fotografia(mondo) == prima


def test_12_corsa_a_vuoto_non_scrive_nulla(mondo):
    _quattro_ispezioni(mondo)
    esito = _backfill(apply=False)
    assert esito["candidates"] == 4 and esito["inserted"] == 0
    assert esito["by_status"] == {"requested": 1, "completed": 2, "cancelled": 1}
    assert mondo["sql"]("SELECT count(*) FROM appointments")[0][0] == 0


def test_13_una_riga_nuova_dopo_il_backfill_si_aggiunge_da_sola(mondo):
    _quattro_ispezioni(mondo)
    _backfill()
    nuova = _lmc15().create_inspection(mondo["a"], stima_id=mondo["stima"],
                                       scheduled_for=ore(18), actor_user_id=mondo["giorgio"])
    esito = _backfill()
    assert esito["inserted"] == 1
    assert _appuntamento_di(mondo, nuova["id"])["status"] == "requested"


def test_14_non_duplica_una_proiezione_gia_scritta_dall_agenda(http, mondo, proiezione_accesa):
    """Una riga LMC-15 creata DALL'AGENDA e' gia' rappresentata: niente backfill."""
    r = http("giorgio").post("/api/appointments", json={
        "appointment_type": "inspection", "stima_id": mondo["stima"],
        "assigned_user_id": mondo["luca"], "start_at": ore(10).isoformat(),
        "end_at": ore(11).isoformat(),
        "client_request_id": "7b0b8d0e-3d5c-4a41-9a1f-2f7f8c3f9b10"})
    assert r.status_code == 201, r.text
    assert r.json()["stima_inspection_id"] is not None
    esito = _backfill()
    assert esito["candidates"] == 0 and esito["inserted"] == 0
    assert mondo["sql"]("SELECT count(*) FROM appointments")[0][0] == 1


def test_15_orfani_senza_stima_esclusi_e_contati(mondo):
    _quattro_ispezioni(mondo)
    mondo["sql"]("INSERT INTO stime (agency_id, comune) VALUES (%s, 'Giulianova')",
                 (mondo["a"],))
    stima2 = mondo["sql"]("SELECT max(id) FROM stime")[0][0]
    orfana = _lmc15().create_inspection(mondo["a"], stima_id=stima2, scheduled_for=ore(8),
                                        actor_user_id=mondo["giorgio"])
    mondo["sql"]("DELETE FROM stime WHERE id = %s", (stima2,))         # ON DELETE SET NULL
    c = _census()
    assert c["totale"] == 5 and c["orfani_senza_stima"] == 1 and c["da_importare"] == 4
    esito = _backfill()
    assert esito["inserted"] == 4
    assert mondo["sql"]("SELECT count(*) FROM appointments WHERE stima_inspection_id = %s",
                        (orfana["id"],))[0][0] == 0


def test_16_il_backfill_non_passa_dall_agente_ne_dal_vincolo_anti_sovrapposizione(mondo):
    """Due righe LMC-15 alla stessa ora: senza agente non si scontrano."""
    lmc15 = _lmc15()
    for _ in range(2):
        lmc15.create_inspection(mondo["a"], stima_id=mondo["stima"], scheduled_for=ore(9),
                                actor_user_id=mondo["giorgio"])
    assert _backfill()["inserted"] == 2


# ---------------------------------------------------------------------------
# C - DOPO IL BACKFILL: LA PROIEZIONE RIUSA LA RIGA LMC-15
# ---------------------------------------------------------------------------

def test_20_proiezione_spenta_la_richiesta_importata_non_si_fissa(http, mondo,
                                                                  proiezione_spenta):
    isp = _quattro_ispezioni(mondo)
    _backfill()
    a = _appuntamento_di(mondo, isp["aperta"]["id"])
    prima = _fotografia(mondo)
    r = http("giorgio").post(f"/api/appointments/{a['id']}/schedule", json={
        "version": a["version"], "assigned_user_id": mondo["luca"],
        "start_at": ore(9).isoformat(), "end_at": ore(10).isoformat()})
    assert r.status_code == 422 and r.json()["code"] == "INSPECTION_PROJECTION_NOT_ACTIVE"
    r = http("giorgio").post(f"/api/appointments/{a['id']}/cancel",
                             json={"version": a["version"], "reason": "x"})
    assert r.status_code == 422 and r.json()["code"] == "INSPECTION_PROJECTION_NOT_ACTIVE"
    assert _fotografia(mondo) == prima


def test_21_accesa_fissare_una_richiesta_importata_riusa_e_sposta_la_riga(
        http, mondo, proiezione_accesa):
    isp = _quattro_ispezioni(mondo)
    _backfill()
    a = _appuntamento_di(mondo, isp["aperta"]["id"])
    righe_lmc15 = mondo["sql"]("SELECT count(*) FROM stima_inspections")[0][0]
    timeline = mondo["sql"]("SELECT count(*) FROM seller_timeline_events")[0][0]
    # SENTINELLA AGGIORNATA DA A30-7 (D4): un inizio futuro per l'orologio del
    # service (ORA = mezzogiorno di GIORNO).
    r = http("giorgio").post(f"/api/appointments/{a['id']}/schedule", json={
        "version": a["version"], "assigned_user_id": mondo["luca"],
        "start_at": ore(13, 30).isoformat(), "end_at": ore(14, 30).isoformat()})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "scheduled"
    assert r.json()["stima_inspection_id"] == isp["aperta"]["id"]          # stessa riga
    assert mondo["sql"]("SELECT count(*) FROM stima_inspections")[0][0] == righe_lmc15
    stato, quando = mondo["sql"](
        "SELECT status, scheduled_for FROM stima_inspections WHERE id=%s",
        (isp["aperta"]["id"],))[0]
    assert stato == "scheduled" and quando == ore(13, 30)
    # LMC-15 non definisce un evento di spostamento: la timeline non cambia
    assert mondo["sql"]("SELECT count(*) FROM seller_timeline_events")[0][0] == timeline


def test_22_accesa_annullare_una_richiesta_importata_chiude_la_riga_lmc15(
        http, mondo, proiezione_accesa):
    isp = _quattro_ispezioni(mondo)
    _backfill()
    a = _appuntamento_di(mondo, isp["aperta"]["id"])
    r = http("giorgio").post(f"/api/appointments/{a['id']}/cancel",
                             json={"version": a["version"], "reason": "Proprietario assente"})
    assert r.status_code == 200, r.text
    stato, motivo = mondo["sql"](
        "SELECT status, cancelled_reason FROM stima_inspections WHERE id=%s",
        (isp["aperta"]["id"],))[0]
    assert (stato, motivo) == ("cancelled", "Proprietario assente")


def test_23_accesa_riga_lmc15_gia_chiusa_altrove_e_un_conflitto_leggibile(
        http, mondo, proiezione_accesa):
    isp = _quattro_ispezioni(mondo)
    _backfill()
    a = _appuntamento_di(mondo, isp["aperta"]["id"])
    # dopo il backfill, LMC-15 (scrittore legacy) chiude la riga per conto suo
    _lmc15().cancel_inspection(mondo["a"], inspection_id=isp["aperta"]["id"],
                               reason="altrove", actor_user_id=mondo["giorgio"])
    # SENTINELLA AGGIORNATA DA A30-7 (D4): inizio futuro per l'orologio del service
    r = http("giorgio").post(f"/api/appointments/{a['id']}/schedule", json={
        "version": a["version"], "assigned_user_id": mondo["luca"],
        "start_at": ore(13).isoformat(), "end_at": ore(14).isoformat()})
    assert r.status_code == 409 and r.json()["code"] == "PROJECTION_CONFLICT"
    riga = _appuntamento_di(mondo, isp["aperta"]["id"])
    assert riga["status"] == "requested" and riga["version"] == a["version"]


def test_24_la_riga_importata_resta_immutabile_nella_provenienza(mondo):
    isp = _quattro_ispezioni(mondo)
    _backfill()
    a = _appuntamento_di(mondo, isp["aperta"]["id"])
    import psycopg2
    with pytest.raises(psycopg2.Error):
        mondo["sql"]("UPDATE appointments SET source_record_id = 'altro' WHERE id = %s",
                     (a["id"],))
    mondo["conn"].rollback()
    with pytest.raises(psycopg2.Error):
        mondo["sql"]("DELETE FROM appointments WHERE id = %s", (a["id"],))
    mondo["conn"].rollback()
    assert _appuntamento_di(mondo, isp["aperta"]["id"])["source_record_id"] == \
        f"stima_inspections:{isp['aperta']['id']}"


def test_25_il_census_misura_la_deriva_prima_della_facade(mondo):
    """Finche' la facade non c'e', LMC-15 puo' chiudere o spostare una riga gia'
    importata: il census lo conta (e nessuno lo corregge in silenzio)."""
    isp = _quattro_ispezioni(mondo)
    seconda = _lmc15().create_inspection(mondo["a"], stima_id=mondo["stima"],
                                         scheduled_for=ore(13), actor_user_id=mondo["giorgio"])
    _backfill()
    c = _census()
    assert (c["deriva_chiuse_in_lmc15"], c["deriva_spostate_in_lmc15"]) == (0, 0)
    _lmc15().complete_inspection(mondo["a"], inspection_id=isp["aperta"]["id"],
                                 completed_at=ore(9, 50), actor_user_id=mondo["giorgio"])
    from acquisition import repository as lmc15
    from core.database import core_cursor
    with core_cursor(commit=True) as (_, cur):
        lmc15.reschedule_inspection_in(cur, mondo["a"], inspection_id=seconda["id"],
                                       scheduled_for=ore(14))
    c = _census()
    assert (c["deriva_chiuse_in_lmc15"], c["deriva_spostate_in_lmc15"]) == (1, 1)
    assert _backfill()["inserted"] == 0          # il backfill non "ripara" nulla


def test_26_accesa_di_default_senza_interruttori_nei_test(http, mondo):
    """Nessun monkeypatch: il valore spedito e' ACCESO. Creazione fissata ->
    riga LMC-15 nella stessa transazione; completamento -> riga completata."""
    from appointments import projection
    assert projection.PROJECTION_ENABLED is True
    r = http("giorgio").post("/api/appointments", json={
        "appointment_type": "inspection", "stima_id": mondo["stima"],
        "assigned_user_id": mondo["luca"], "start_at": ore(10).isoformat(),
        "end_at": ore(11).isoformat(),
        "client_request_id": "0f8a2b1c-4d5e-4f60-8a7b-9c0d1e2f3a4b"})
    assert r.status_code == 201, r.text
    a = r.json()
    stato, quando = mondo["sql"]("SELECT status, scheduled_for FROM stima_inspections "
                                 "WHERE id = %s", (a["stima_inspection_id"],))[0]
    assert (stato, quando) == ("scheduled", ore(10))
    r = http("giorgio").post(f"/api/appointments/{a['id']}/complete",
                             json={"version": a["version"],
                                   "completed_at": ore(10, 45).isoformat()})
    assert r.status_code == 200, r.text
    stato, fatto, registrato = mondo["sql"](
        "SELECT status, completed_at, completed_recorded_at FROM stima_inspections "
        "WHERE id = %s", (a["stima_inspection_id"],))[0]
    assert stato == "completed" and fatto == ore(10, 45) and registrato >= fatto


# ---------------------------------------------------------------------------
# E - F9: cancel e no_show nativi scrivono il NOW() del database, lo stesso
#     istante che LMC-15 registra nella stessa transazione (Q10 = 0)
# ---------------------------------------------------------------------------

#: Q10 del census (roadmap/A30-2P_census_readonly.sql), copiata alla lettera.
Q10 = """
WITH coppie AS (
    SELECT a.id, a.agency_id, a.status AS a_status, a.source, a.stima_id AS a_stima,
           a.start_at, a.completed_at AS a_completed_at, a.cancelled_at AS a_cancelled_at,
           a.no_show_at, a.stima_inspection_id,
           i.id AS i_id, i.status AS i_status, i.stima_id AS i_stima,
           i.scheduled_for, i.completed_at AS i_completed_at,
           i.cancelled_at AS i_cancelled_at, i.cancelled_reason,
           s.agency_id AS i_agency
      FROM appointments a
      LEFT JOIN stima_inspections i ON i.id = a.stima_inspection_id
      LEFT JOIN stime s ON s.id = i.stima_id
     WHERE a.stima_inspection_id IS NOT NULL
)
SELECT
  count(*) FILTER (WHERE i_id IS NULL)
      AS collegamento_senza_riga_lmc15,
  count(*) FILTER (WHERE i_id IS NOT NULL AND NOT (
         (a_status IN ('scheduled', 'confirmed') AND i_status = 'scheduled')
      OR (a_status = 'requested' AND source = 'stima_inspections_backfill'
          AND i_status = 'scheduled')
      OR (a_status = 'completed' AND i_status = 'completed')
      OR (a_status = 'cancelled' AND i_status = 'cancelled')
      OR (a_status = 'no_show'   AND i_status = 'cancelled'
          AND cancelled_reason = 'no_show')))
      AS stato_incompatibile,
  count(*) FILTER (WHERE a_status IN ('requested', 'scheduled', 'confirmed')
                     AND i_status = 'scheduled'
                     AND scheduled_for IS DISTINCT FROM start_at)
      AS scheduled_for_diverso_da_start_at,
  count(*) FILTER (WHERE a_status = 'completed' AND i_status = 'completed'
                     AND i_completed_at IS DISTINCT FROM a_completed_at)
      AS completed_at_diverso,
  count(*) FILTER (WHERE a_status = 'cancelled' AND i_status = 'cancelled'
                     AND i_cancelled_at IS DISTINCT FROM a_cancelled_at)
      AS cancelled_at_diverso,
  count(*) FILTER (WHERE a_status = 'no_show' AND i_status = 'cancelled'
                     AND i_cancelled_at IS DISTINCT FROM no_show_at)
      AS no_show_at_diverso,
  count(*) FILTER (WHERE i_id IS NOT NULL
                     AND (i_stima IS DISTINCT FROM a_stima
                          OR i_agency IS DISTINCT FROM agency_id))
      AS stima_o_agenzia_diversa,
  (SELECT count(*) FROM appointments WHERE status = 'rescheduled'
                                      AND stima_inspection_id IS NOT NULL)
      AS rescheduled_ancora_collegato,
  -- gli orfani senza stima (Q2) non sono importabili e restano fuori
  (SELECT count(*) FROM stima_inspections i2
    WHERE i2.stima_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM appointments a2
                       WHERE a2.stima_inspection_id = i2.id))
      AS lmc15_con_stima_non_rappresentati
  FROM coppie
"""


def _q10(mondo):
    with mondo["conn"].cursor() as cur:
        cur.execute(Q10)
        nomi = [d[0] for d in cur.description]
        esito = dict(zip(nomi, cur.fetchone()))
    mondo["conn"].commit()
    return esito


def _sopralluogo_fissato(http, mondo, ora, chiave):
    r = http("giorgio").post("/api/appointments", json={
        "appointment_type": "inspection", "stima_id": mondo["stima"],
        "assigned_user_id": mondo["luca"], "start_at": ore(ora).isoformat(),
        "end_at": ore(ora + 1).isoformat(), "client_request_id": chiave})
    assert r.status_code == 201, r.text
    assert r.json()["stima_inspection_id"] is not None
    return r.json()


def test_30_f9_cancel_nativo_stesso_istante_su_appointments_e_lmc15(http, mondo):
    a = _sopralluogo_fissato(http, mondo, 14, "5a1f0c1e-9b7d-4c2a-8e3f-1b2c3d4e5f01")
    r = http("giorgio").post(f"/api/appointments/{a['id']}/cancel",
                             json={"version": a["version"], "reason": "Rinviato"})
    assert r.status_code == 200, r.text
    ag = mondo["sql"]("SELECT cancelled_at FROM appointments WHERE id=%s", (a["id"],))[0][0]
    i_at, i_rec, motivo = mondo["sql"](
        "SELECT cancelled_at, cancelled_recorded_at, cancelled_reason "
        "FROM stima_inspections WHERE id=%s", (a["stima_inspection_id"],))[0]
    assert ag == i_at == i_rec                   # identici, al microsecondo
    assert motivo == "Rinviato"
    assert datetime.fromisoformat(r.json()["cancelled_at"]) == ag
    q = _q10(mondo)
    assert all(v == 0 for v in q.values()), q


def test_31_f9_no_show_nativo_stesso_istante_su_appointments_e_lmc15(http, mondo):
    a = _sopralluogo_fissato(http, mondo, 10, "5a1f0c1e-9b7d-4c2a-8e3f-1b2c3d4e5f02")
    r = http("giorgio").post(f"/api/appointments/{a['id']}/no-show",
                             json={"version": a["version"]})
    assert r.status_code == 200, r.text
    ag = mondo["sql"]("SELECT no_show_at FROM appointments WHERE id=%s", (a["id"],))[0][0]
    stato, i_at, motivo = mondo["sql"](
        "SELECT status, cancelled_at, cancelled_reason FROM stima_inspections WHERE id=%s",
        (a["stima_inspection_id"],))[0]
    assert (stato, motivo) == ("cancelled", "no_show")
    assert ag == i_at
    q = _q10(mondo)
    assert all(v == 0 for v in q.values()), q


def test_32_f9_q10_zero_dopo_cancel_e_no_show_insieme_al_backfill(http, mondo):
    """Lo scenario completo: backfill, poi complete, reschedule, cancel e
    no-show nativi con la proiezione accesa. Q10: tutti 0."""
    _quattro_ispezioni(mondo)
    _backfill()
    a = _sopralluogo_fissato(http, mondo, 8, "5a1f0c1e-9b7d-4c2a-8e3f-1b2c3d4e5f03")
    r = http("giorgio").post(f"/api/appointments/{a['id']}/complete",
                             json={"version": a["version"], "completed_at": ore(8, 30).isoformat()})
    assert r.status_code == 200, r.text
    b = _sopralluogo_fissato(http, mondo, 12, "5a1f0c1e-9b7d-4c2a-8e3f-1b2c3d4e5f04")
    r = http("giorgio").post(f"/api/appointments/{b['id']}/reschedule",
                             json={"version": b["version"], "start_at": ore(13).isoformat(),
                                   "end_at": ore(14).isoformat()})
    assert r.status_code == 201, r.text
    c = _sopralluogo_fissato(http, mondo, 18, "5a1f0c1e-9b7d-4c2a-8e3f-1b2c3d4e5f05")
    r = http("giorgio").post(f"/api/appointments/{c['id']}/cancel",
                             json={"version": c["version"], "reason": "x"})
    assert r.status_code == 200, r.text
    d = _sopralluogo_fissato(http, mondo, 10, "5a1f0c1e-9b7d-4c2a-8e3f-1b2c3d4e5f06")
    r = http("giorgio").post(f"/api/appointments/{d['id']}/no-show",
                             json={"version": d["version"]})
    assert r.status_code == 200, r.text
    q = _q10(mondo)
    assert all(v == 0 for v in q.values()), q
    assert mondo["sql"]("SELECT count(*) FROM appointments WHERE stima_inspection_id "
                        "IS NOT NULL")[0][0] == 8


def test_33_f9_annullo_senza_proiezione_usa_comunque_il_now_del_database(http, mondo):
    r = http("giorgio").post("/api/appointments", json={
        "appointment_type": "seller_meeting", "assigned_user_id": mondo["luca"],
        "start_at": ore(16).isoformat(), "end_at": ore(17).isoformat(),
        "client_request_id": "5a1f0c1e-9b7d-4c2a-8e3f-1b2c3d4e5f07"})
    a = r.json()
    prima = mondo["sql"]("SELECT NOW()")[0][0]
    r = http("giorgio").post(f"/api/appointments/{a['id']}/cancel", json={"version": a["version"]})
    dopo = mondo["sql"]("SELECT NOW()")[0][0]
    assert r.status_code == 200, r.text
    fatto = datetime.fromisoformat(r.json()["cancelled_at"])
    assert prima <= fatto <= dopo                # non l'orologio iniettato ORA
    assert r.json()["cancelled_reason"] is None  # motivo facoltativo senza stima: invariato


def _sopralluogo_tra(http, mondo, inizio, fine, chiave, agente="luca"):
    r = http("giorgio").post("/api/appointments", json={
        "appointment_type": "inspection", "stima_id": mondo["stima"],
        "assigned_user_id": mondo[agente], "start_at": inizio.isoformat(),
        "end_at": fine.isoformat(), "client_request_id": chiave})
    assert r.status_code == 201, r.text
    return r.json()


def test_34_f9_regole_native_invariate(http, mondo):
    a = _sopralluogo_tra(http, mondo, futuro(15), futuro(16),
                         "5a1f0c1e-9b7d-4c2a-8e3f-1b2c3d4e5f08")
    # motivo obbligatorio per un sopralluogo con stima
    r = http("giorgio").post(f"/api/appointments/{a['id']}/cancel", json={"version": a["version"]})
    assert r.status_code == 422 and r.json()["code"] == "REASON_REQUIRED"
    # D11: l'assenza solo dopo la fine (fine nel futuro del NOW() del database)
    r = http("giorgio").post(f"/api/appointments/{a['id']}/no-show", json={"version": a["version"]})
    # A30-8 D6: "troppo presto" e' un 422 con codice proprio (era 409).
    assert r.status_code == 422 and r.json()["code"] == "NO_SHOW_TOO_EARLY"
    # un agente non vede l'appuntamento di un collega
    r = http("marta").post(f"/api/appointments/{a['id']}/cancel",
                           json={"version": a["version"], "reason": "x"})
    assert r.status_code == 404
    assert mondo["sql"]("SELECT status FROM stima_inspections WHERE id=%s",
                        (a["stima_inspection_id"],))[0][0] == "scheduled"


def test_35_f9_confine_no_show_sull_orologio_del_database(http, mondo, monkeypatch):
    """La guardia e `no_show_at` usano LO STESSO istante: il NOW() della
    transazione. L'orologio del processo non conta, ne' in un verso ne'
    nell'altro."""
    from appointments import service

    # 1) db_now < end_at: 422 NO_SHOW_TOO_EARLY (A30-8; era 409) anche se l'orologio del processo e' GIA' oltre
    db_now = mondo["sql"]("SELECT NOW()")[0][0]
    fine = db_now + timedelta(minutes=10)
    a = _sopralluogo_tra(http, mondo, fine - timedelta(hours=1), fine,
                         "5a1f0c1e-9b7d-4c2a-8e3f-1b2c3d4e5f09")
    monkeypatch.setattr(service, "_adesso", lambda: fine + timedelta(days=1))
    r = http("giorgio").post(f"/api/appointments/{a['id']}/no-show",
                             json={"version": a["version"]})
    # A30-8 D6: "troppo presto" e' un 422 con codice proprio (era 409).
    assert r.status_code == 422 and r.json()["code"] == "NO_SHOW_TOO_EARLY"
    assert datetime.fromisoformat(r.json()["available_from"]) == fine
    assert mondo["sql"]("SELECT status FROM stima_inspections WHERE id=%s",
                        (a["stima_inspection_id"],))[0][0] == "scheduled"

    # 2) db_now >= end_at: consentito anche se l'orologio del processo e'
    #    ancora PRIMA della fine. end_at = un NOW() gia' letto: la transazione
    #    della richiesta parte dopo, quindi il suo NOW() e' >= end_at.
    fine = mondo["sql"]("SELECT NOW()")[0][0]
    b = _sopralluogo_tra(http, mondo, fine - timedelta(hours=1), fine,
                         "5a1f0c1e-9b7d-4c2a-8e3f-1b2c3d4e5f10", agente="marta")
    monkeypatch.setattr(service, "_adesso", lambda: fine - timedelta(days=1))
    r = http("giorgio").post(f"/api/appointments/{b['id']}/no-show",
                             json={"version": b["version"]})
    assert r.status_code == 200, r.text
    no_show_at = mondo["sql"]("SELECT no_show_at FROM appointments WHERE id=%s",
                              (b["id"],))[0][0]
    assert no_show_at >= fine                    # mai prima della fine
    # 3) stesso istante su LMC-15
    stato, i_at, motivo = mondo["sql"](
        "SELECT status, cancelled_at, cancelled_reason FROM stima_inspections WHERE id=%s",
        (b["stima_inspection_id"],))[0]
    assert (stato, motivo) == ("cancelled", "no_show") and i_at == no_show_at
    # 4) Q10 tutto 0
    q = _q10(mondo)
    assert all(v == 0 for v in q.values()), q
