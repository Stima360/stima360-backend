"""SELEZIONE MULTIPLA dei Contatti - spostamento nel Cestino e ripristino massivi,
su PostgreSQL VERO (fixture `mondo` di DELETE-ARCH Fase 0: tutte le migration,
router veri). E la RISERVATEZZA del Cestino singolo, che li precede.

    R1  riservatezza nel Cestino singolo: il contatto riservato a un agente e' un
        404 per titolare e amministratore (verifica, spostamento, ripristino),
        invisibile nel Cestino dei contatti e nel Cestino unico; chi l'ha importato
        lo sposta e lo ripristina
    S1  «Seleziona tutti i risultati»: la stessa regola dell'elenco (agenzia, scope
        dell'agente, riservati, Cestino, stato, ricerca); oltre il massimo, 400
    B1  spostamento a blocchi: un evento per contatto con l'operazione comune;
        nulla di cancellato davvero; «Ripristina questa operazione»
    B2  richieste costruite a mano: id dell'altra agenzia, riservati altrui, fuori
        scope, inesistenti -> «non disponibili» e intatti; corpo con agency_id,
        senza conferma, oltre 100 id -> 422; operazione altrui -> 409
    B3  stesse protezioni del singolo: processi aperti -> bloccato; storico ->
        solo amministratore; gia' nel Cestino; ripristino solo di cio' che ho spostato
    B4  oltre 5.000 contatti: selezione, spostamento e ripristino a blocchi con i
        tempi; l'altra agenzia non e' toccata
    B5  concorrenza: due operazioni sugli stessi contatti, ognuno spostato una
        volta; una riga tenuta da un'altra transazione e' «occupata», le altre vanno
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest

from tests.test_delete_arch_0_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _codice, _lead, _q, completo, mondo, operatori,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

C = "/api/core/contacts"
TOKEN = "multisel"


def _contatti(m, n, agenzia=1, prefisso="Multi", agente=None, privato=None):
    """`n` contatti sintetici in un colpo solo (generate_series)."""
    righe = _q(m, f"""INSERT INTO contacts (agency_id, display_name, first_name, last_name, email, email_normalized,
                                            assigned_agent_id, private_user_id)
                     SELECT %s, %s || ' ' || g, %s, 'N' || g, '{TOKEN}-' || md5(random()::text) || '@x.test',
                            '{TOKEN}-' || md5(random()::text) || '@x.test', %s, %s
                       FROM generate_series(1, %s) g RETURNING id""",
               (agenzia, prefisso, prefisso, agente, privato, n))
    return sorted(r[0] for r in righe)


def _sposta(m, chi, ids, op=None, motivo="duplicate"):
    corpo = {"ids": ids, "reason_code": motivo, "confirm": True}
    if op:
        corpo["operation_id"] = op
    return m["api"](chi).post(f"{C}/bulk-trash", json=corpo)


def _ripristina(m, chi, ids, op=None):
    corpo = {"ids": ids, "confirm": True}
    if op:
        corpo["operation_id"] = op
    return m["api"](chi).post(f"{C}/bulk-restore", json=corpo)


def _nel_cestino(m, ids):
    return {r[0] for r in _q(m, "SELECT id FROM contacts WHERE id = ANY(%s) AND deleted_at IS NOT NULL", (ids,))}


def _tutto_a_blocchi(m, chi, ids, fare, blocco=100):
    """Come il browser: blocchi da 100, l'operazione del primo blocco per tutti, i
    `remaining` rimandati."""
    op, fatti, esiti = None, [], []
    coda = list(ids)
    while coda:
        pezzo, coda = coda[:blocco], coda[blocco:]
        corpo = _codice(fare(m, chi, pezzo, op), 200)
        op = corpo["operation_id"]
        fatti += corpo.get("trashed", corpo.get("restored", []))
        coda = corpo["remaining"] + coda
        esiti.append(corpo)
    return op, fatti, esiti


# ---------------------------------------------------------------------------
# R1 - riservatezza nel Cestino singolo
# ---------------------------------------------------------------------------

def test_r1_il_contatto_riservato_altrui_non_si_tocca_dal_cestino_singolo(mondo):
    m = mondo
    agente = m["ids"]["agent_a"]
    [riservato] = _contatti(m, 1, prefisso="Riservato", agente=agente, privato=agente)
    for chi in ("owner_a", "admin_a", "agent_a2"):
        assert m["api"](chi).get(f"{C}/{riservato}/deletion-check").status_code == 404, chi
        r = m["api"](chi).post(f"{C}/{riservato}/trash", json={"reason_code": "duplicate"})
        assert r.status_code == 404, (chi, r.text)
    assert _nel_cestino(m, [riservato]) == set()

    # chi l'ha importato lo sposta; il Cestino degli altri non lo mostra e non lo ripristina
    _codice(m["api"]("agent_a").post(f"{C}/{riservato}/trash", json={"reason_code": "duplicate"}), 200)
    for chi in ("owner_a", "admin_a"):
        voci = _codice(m["api"](chi).get("/api/core/trash/contacts", params={"limit": 200}), 200)["items"]
        assert riservato not in {v["id"] for v in voci}, chi
        assert m["api"](chi).post(f"{C}/{riservato}/restore").status_code == 404, chi
    assert riservato in {v["id"] for v in _codice(m["api"]("agent_a").get("/api/core/trash/contacts"), 200)["items"]}

    # il Cestino unico (registro CG-1): stessa regola
    from operator_auth.context import OperatorContext
    from trash import service as cestino
    for chi, (uid, agenzia, ruolo) in (("owner_a", m["ruoli"]["owner_a"]), ("agent_a", m["ruoli"]["agent_a"])):
        ctx = OperatorContext(user_id=uid, agency_id=agenzia, role=ruolo, is_platform_admin=False,
                              session_id=None, auth_channel="operator_session")
        voci = cestino.list_items(ctx, scope="agency", kind="contact", limit=200)["items"]
        visto = riservato in {v["id"] for v in voci}
        assert visto is (chi == "agent_a"), chi
        if chi == "owner_a":
            with pytest.raises(Exception):
                cestino.relations(ctx, "contact", riservato, scope="agency")

    _codice(m["api"]("agent_a").post(f"{C}/{riservato}/restore"), 200)
    assert _nel_cestino(m, [riservato]) == set()


# ---------------------------------------------------------------------------
# S1 - la selezione di tutti i risultati
# ---------------------------------------------------------------------------

def test_s1_selezione_stessa_regola_dell_elenco(mondo):
    m = mondo
    agente = m["ids"]["agent_a"]
    miei = _contatti(m, 30, prefisso="Selezione")
    suoi = _contatti(m, 5, prefisso="Selezione", agente=agente)
    riservati = _contatti(m, 3, prefisso="Selezione", agente=agente, privato=agente)
    altra = _contatti(m, 7, agenzia=2, prefisso="Selezione")
    _q(m, "UPDATE contacts SET status = 'inactive' WHERE id = ANY(%s)", (miei[:4],))

    def sel(chi, **p):
        return _codice(m["api"](chi).get(f"{C}/selection", params={"search": "Selezione", **p}), 200)

    owner = sel("owner_a")
    assert set(owner["ids"]) == set(miei) | set(suoi) and owner["total"] == len(miei) + len(suoi)
    assert set(sel("agent_a")["ids"]) == set(suoi) | set(riservati)          # solo i suoi, riservati compresi
    assert set(sel("owner_b")["ids"]) == set(altra)
    assert set(sel("owner_a", status="inactive")["ids"]) == set(miei[:4])
    # la pagina e la selezione dicono la stessa cosa
    pagina = {c["id"] for c in _codice(m["api"]().get(C, params={"search": "Selezione", "limit": 200}), 200)["items"]}
    assert pagina == set(owner["ids"])
    # un contatto nel Cestino non e' selezionabile
    _codice(_sposta(m, "owner_a", miei[-1:]), 200)
    assert miei[-1] not in sel("owner_a")["ids"]


def test_s1_selezione_oltre_il_massimo_e_rifiutata(mondo, monkeypatch):
    from core import repository
    _contatti(mondo, 12, prefisso="Tetto")
    monkeypatch.setattr(repository, "CONTACT_SELECTION_MAX", 10)
    monkeypatch.setattr(repository.list_contact_ids, "__defaults__", (10,))
    r = mondo["api"]().get(f"{C}/selection", params={"search": "Tetto"})
    assert r.status_code == 400 and "restringi" in r.json()["detail"], r.text


# ---------------------------------------------------------------------------
# B1 - spostamento e ripristino a blocchi
# ---------------------------------------------------------------------------

def test_b1_spostamento_a_blocchi_registro_e_ripristino_dell_operazione(mondo):
    m = mondo
    ids = _contatti(m, 230, prefisso="Blocchi")
    op, spostati, esiti = _tutto_a_blocchi(m, "owner_a", ids, _sposta)
    assert sorted(spostati) == ids and len(esiti) == 3
    assert _nel_cestino(m, ids) == set(ids)
    assert _q(m, "SELECT count(*) FROM contacts WHERE id = ANY(%s)", (ids,))[0][0] == len(ids)   # nulla cancellato
    eventi = _q(m, "SELECT count(*), count(DISTINCT entity_id) FROM record_lifecycle_events WHERE entity_type = "
                   "'contact' AND action = 'trash' AND metadata->>'bulk_operation' = %s", (op,))[0]
    assert tuple(eventi) == (len(ids), len(ids))
    # il Cestino mostra l'operazione e i suoi contatti
    operazioni = _codice(m["api"]().get(f"{C}/trash-operations"), 200)["items"]
    voce = next(o for o in operazioni if o["operation_id"] == op)
    assert voce["in_trash"] == len(ids) and voce["reason_code"] == "duplicate"
    dettaglio = _codice(m["api"]().get(f"{C}/trash-operations/{op}"), 200)
    assert dettaglio["ids"] == ids and dettaglio["total"] == len(ids)
    # «Ripristina questa operazione»
    _, ripristinati, _ = _tutto_a_blocchi(m, "owner_a", dettaglio["ids"], _ripristina)
    assert sorted(ripristinati) == ids and _nel_cestino(m, ids) == set()
    assert m["api"]().get(f"{C}/trash-operations/{op}").status_code == 404
    # ripetere non fa nulla: gia' fuori dal Cestino
    corpo = _codice(_ripristina(m, "owner_a", ids[:5]), 200)
    assert corpo["restored"] == [] and [v["id"] for v in corpo["already"]] == ids[:5]


# ---------------------------------------------------------------------------
# B2 - richieste costruite a mano
# ---------------------------------------------------------------------------

def test_b2_id_altrui_costruiti_a_mano_non_si_toccano(mondo):
    m = mondo
    agente, agente2 = m["ids"]["agent_a"], m["ids"]["agent_a2"]
    [mio] = _contatti(m, 1, prefisso="Ostile")
    [di_b] = _contatti(m, 1, agenzia=2, prefisso="Ostile")
    [riservato] = _contatti(m, 1, prefisso="Ostile", agente=agente, privato=agente)
    [di_agente2] = _contatti(m, 1, prefisso="Ostile", agente=agente2)
    tutti = [mio, di_b, riservato, di_agente2, 999999999]

    # il titolare: il suo e quello di un agente della SUA agenzia si', il resto mai
    corpo = _codice(_sposta(m, "owner_a", [mio, di_b, riservato, 999999999]), 200)
    assert corpo["trashed"] == [mio]
    assert {v["id"] for v in corpo["not_available"]} == {di_b, riservato, 999999999}
    assert all(v.get("label") is None for v in corpo["not_available"])      # nessun nome trapela
    assert _nel_cestino(m, tutti) == {mio}

    # l'agente: fuori dal suo scope (un collega, l'altra agenzia) -> nulla
    corpo = _codice(_sposta(m, "agent_a", [di_agente2, di_b]), 200)
    assert corpo["trashed"] == [] and len(corpo["not_available"]) == 2
    # l'altra agenzia: nulla di A, nemmeno il riservato
    corpo = _codice(_sposta(m, "owner_b", [mio, riservato, di_agente2]), 200)
    assert corpo["trashed"] == [] and len(corpo["not_available"]) == 3
    # ripristino dall'altra agenzia e da un collega: nulla
    corpo = _codice(_ripristina(m, "owner_b", [mio]), 200)
    assert corpo["restored"] == [] and len(corpo["not_available"]) == 1
    corpo = _codice(_ripristina(m, "agent_a2", [mio]), 200)
    assert corpo["restored"] == [] and len(corpo["not_available"]) == 1
    assert _nel_cestino(m, [mio]) == {mio}
    assert _nel_cestino(m, [di_b, riservato, di_agente2]) == set()

    # corpi non ammessi
    for extra in ({"agency_id": 2}, {"deleted_by_user_id": 1}, {"confirm": False}):
        r = m["api"]().post(f"{C}/bulk-trash", json={"ids": [di_agente2], "reason_code": "duplicate", "confirm": True,
                                                       **extra})
        assert r.status_code == 422, (extra, r.text)
    r = m["api"]().post(f"{C}/bulk-trash", json={"ids": [di_agente2], "reason_code": "duplicate"})
    assert r.status_code == 422
    r = m["api"]().post(f"{C}/bulk-trash", json={"ids": list(range(1, 102)), "reason_code": "duplicate",
                                                   "confirm": True})
    assert r.status_code == 422
    r = m["api"]().post(f"{C}/bulk-trash", json={"ids": [0], "reason_code": "duplicate", "confirm": True})
    assert r.status_code == 400 and r.json()["code"] == "INVALID_IDS"
    assert _codice(_sposta(m, "owner_a", [di_agente2], motivo="boh"), 400)["code"] == "INVALID_TRASH_REASON"
    assert _nel_cestino(m, [di_agente2]) == set()

    # un'operazione altrui non si continua, ne' si legge da un'altra agenzia
    op = _codice(_sposta(m, "agent_a", _contatti(m, 1, prefisso="Ostile", agente=agente)), 200)["operation_id"]
    r = _sposta(m, "owner_a", [di_agente2], op=op)
    assert r.status_code == 409 and r.json()["code"] == "OPERATION_NOT_YOURS"
    assert _codice(_sposta(m, "owner_a", [di_agente2], op="non-un-uuid"), 400)["code"] == "INVALID_OPERATION"
    assert m["api"]("owner_b").get(f"{C}/trash-operations/{op}").status_code == 404
    assert op not in {o["operation_id"] for o in _codice(m["api"]("owner_b").get(f"{C}/trash-operations"), 200)["items"]}
    assert m["api"]("agent_a2").get(f"{C}/trash-operations/{op}").status_code == 404


# ---------------------------------------------------------------------------
# B3 - le protezioni del singolo
# ---------------------------------------------------------------------------

def test_b3_blocchi_storico_e_ripristino_come_il_singolo(mondo):
    m = mondo
    agente = m["ids"]["agent_a"]
    libero, con_lead, gia = _contatti(m, 3, prefisso="Protetto")
    _lead(m, con_lead)
    suo_libero, suo_storico = _contatti(m, 2, prefisso="Protetto", agente=agente)
    _q(m, "INSERT INTO activities (agency_id, contact_id, activity_type, subject) VALUES (1, %s, 'call', 'Chiamata')",
       (suo_storico,))
    _codice(m["api"]().post(f"{C}/{gia}/trash", json={"reason_code": "duplicate"}), 200)
    _q(m, "UPDATE contacts SET assigned_agent_id = %s WHERE id = %s", (agente, libero))   # visibile all'agente

    corpo = _codice(_sposta(m, "owner_a", [libero, con_lead, gia]), 200)
    assert corpo["trashed"] == [libero]
    [bloccato] = corpo["blocked"]
    assert bloccato["id"] == con_lead and bloccato["label"] and "LEAD_OPEN" in {b["code"] for b in bloccato["blockers"]}
    assert [v["id"] for v in corpo["already"]] == [gia]
    corpo = _codice(_sposta(m, "agent_a", [suo_libero, suo_storico]), 200)
    assert corpo["trashed"] == [suo_libero] and [v["id"] for v in corpo["needs_admin"]] == [suo_storico]
    assert _nel_cestino(m, [con_lead, suo_storico]) == set()
    # l'agente ripristina solo cio' che ha spostato lui (e che il suo scope vede)
    corpo = _codice(_ripristina(m, "agent_a", [libero, suo_libero]), 200)
    assert corpo["restored"] == [suo_libero] and [v["id"] for v in corpo["not_yours"]] == [libero]


# ---------------------------------------------------------------------------
# B4 - oltre 5.000 contatti
# ---------------------------------------------------------------------------

def test_b4_oltre_5000_contatti_a_blocchi_senza_toccare_l_altra_agenzia(mondo, capsys):
    m = mondo
    ids = _contatti(m, 5200, prefisso="Rubrica")
    altra = _contatti(m, 300, agenzia=2, prefisso="Rubrica")
    t0 = time.monotonic()
    selezione = _codice(m["api"]().get(f"{C}/selection", params={"search": "Rubrica"}), 200)
    t_sel = time.monotonic() - t0
    assert selezione["ids"] == ids and selezione["total"] == 5200

    t0 = time.monotonic()
    op, spostati, esiti = _tutto_a_blocchi(m, "owner_a", selezione["ids"], _sposta)
    t_sposta = time.monotonic() - t0
    assert len(spostati) == 5200 and len(esiti) == 52 and _nel_cestino(m, ids) == set(ids)
    assert _nel_cestino(m, altra) == set()
    assert _codice(m["api"]().get(f"{C}/selection", params={"search": "Rubrica"}), 200)["total"] == 0
    assert _codice(m["api"]("owner_b").get(f"{C}/selection", params={"search": "Rubrica"}), 200)["total"] == 300

    t0 = time.monotonic()
    dettaglio = _codice(m["api"]().get(f"{C}/trash-operations/{op}"), 200)
    _, ripristinati, _ = _tutto_a_blocchi(m, "owner_a", dettaglio["ids"], _ripristina)
    t_ripristina = time.monotonic() - t0
    assert len(ripristinati) == 5200 and _nel_cestino(m, ids) == set()
    with capsys.disabled():
        print(f"\n[B4] 5.200 contatti: selezione {t_sel:.2f}s, spostamento {t_sposta:.1f}s "
              f"({t_sposta / 52:.2f}s per blocco da 100), ripristino {t_ripristina:.1f}s")
    # Il tempo dello spostamento lo decide soprattutto la guardia delle comunicazioni
    # (communication_contact_stop_fence, che oggi scorre tutti i contatti a ogni
    # pausa): qui si misura e si riporta, con un tetto largo.
    assert t_sel < 5 and t_sposta < 900 and t_ripristina < 120


# ---------------------------------------------------------------------------
# B5 - concorrenza e righe occupate
# ---------------------------------------------------------------------------

def _ctx(m, chi):
    from operator_auth.context import OperatorContext
    uid, agenzia, ruolo = m["ruoli"][chi]
    return OperatorContext(user_id=uid, agency_id=agenzia, role=ruolo, is_platform_admin=False,
                           session_id=None, auth_channel="operator_session")


def test_b5_due_operazioni_sugli_stessi_contatti_ognuno_una_volta(mondo):
    from core import contact_bulk
    m = mondo
    ids = _contatti(m, 100, prefisso="Concorrenza")
    esiti, errori = [], []

    def lavora(ordine):
        try:
            esiti.append(contact_bulk.trash_many(_ctx(m, "owner_a"), ordine, "duplicate"))
        except Exception as exc:  # pragma: no cover - lo dice l'assert
            errori.append(exc)

    fili = [threading.Thread(target=lavora, args=(ids,)), threading.Thread(target=lavora, args=(ids[::-1],))]
    for f in fili:
        f.start()
    for f in fili:
        f.join(60)
    assert errori == [] and len(esiti) == 2
    spostati = esiti[0]["trashed"] + esiti[1]["trashed"]
    assert sorted(spostati) == ids                                      # ognuno una volta sola
    assert _q(m, "SELECT count(*) FROM record_lifecycle_events WHERE entity_type = 'contact' AND action = 'trash' "
                 "AND entity_id = ANY(%s)", (ids,))[0][0] == len(ids)


def test_b5_una_riga_occupata_non_ferma_le_altre(mondo, monkeypatch):
    from core import contact_bulk
    m = mondo
    ids = _contatti(m, 5, prefisso="Occupato")
    monkeypatch.setattr(contact_bulk, "LOCK_TIMEOUT", "300ms")
    altra = m["psycopg2"].connect(m["dsn"])
    try:
        with altra.cursor() as cur:
            cur.execute("SELECT id FROM contacts WHERE id = %s FOR UPDATE", (ids[2],))
            corpo = contact_bulk.trash_many(_ctx(m, "owner_a"), ids, "duplicate")
        altra.rollback()
    finally:
        altra.close()
    assert [v["id"] for v in corpo["busy"]] == [ids[2]]
    assert sorted(corpo["trashed"]) == [i for i in ids if i != ids[2]]
    assert _nel_cestino(m, [ids[2]]) == set()
