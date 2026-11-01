"""C3.5 sul backend del SITO (`main`): `POST /api/salva_stima` vero su un
PostgreSQL usa-e-getta creato e cancellato qui (schema legacy del sito + 018 +
019 + 112). Nessun invio: PDF, email, WhatsApp e ponte CRM sostituiti da
registratori locali.

  01  pagina nuova: scelta, data, testo e impronta ufficiali conservati; la
      risposta e gli invii sono gli stessi di una richiesta senza prova.
  02  valori non ufficiali (testo alterato, privacy storica, impronta
      inventata, campi malformati): non si conservano, la stima procede.
  03  casella non spuntata: la scelta resta false, la prova del testo mostrato
      si conserva lo stesso.
  04  rollback: senza la 112 (down) la stima procede identica; con la 112 di
      nuovo, la prova torna.
  05  guasti del modulo dei consensi (modulo mancante, eccezione, versioni
      ufficiali illeggibili, vincolo violato, database lento): la stima, la
      risposta, il PDF, le email e il WhatsApp sono quelli di sempre.

Opt-in: `P29_TEST_DSN` (amministratore di un PostgreSQL usa-e-getta).
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")
ROOT = Path(__file__).resolve().parents[1]
UFFICIALI = ROOT / "site_official" / "versions"
MIGRAZIONI_C35 = Path(os.getenv("C35_MIGRATIONS_DIR", str(ROOT / "migrations")))


class _Richiesta:
    headers = {"content-type": "application/json"}

    def __init__(self, corpo):
        self._corpo = corpo

    async def json(self):
        return dict(self._corpo)


@pytest.fixture
def sito(monkeypatch):
    import psycopg2
    from psycopg2.extensions import make_dsn, parse_dsn
    nome = f"c35_main_{uuid.uuid4().hex[:8]}"
    admin = psycopg2.connect(DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')
    dsn = make_dsn(**{**parse_dsn(DSN), "dbname": nome})
    import database
    import main
    monkeypatch.setattr(database, "get_connection", lambda: psycopg2.connect(dsn))
    monkeypatch.setattr(main, "get_connection", lambda: psycopg2.connect(dsn))
    database.crea_tabella_stime()
    database.crea_tabella_stime_dettagliate()
    database.crea_tabella_zone_valori()
    database.migrazione_allinea_stime()
    conn = psycopg2.connect(dsn)
    conn.autocommit = True

    def q(sql, p=None):
        with conn.cursor() as cur:
            cur.execute(sql, p)
            return cur.fetchall() if cur.description else None

    # lo schema del sito come in produzione (vedi tests/test_site_import_1_postgres.py su core-0.1-test)
    for nome_m in ("018_stime_consenso_marketing", "019_stime_vistamaredettaglio"):
        q((MIGRAZIONI_C35 / f"{nome_m}.sql").read_text(encoding="utf-8"))
    q("ALTER TABLE stime ALTER COLUMN locali TYPE text USING locali::text")
    q("ALTER TABLE stime ALTER COLUMN anno TYPE text USING anno::text")
    q((MIGRAZIONI_C35 / "112_stime_consenso_marketing_prova.sql").read_text(encoding="utf-8"))
    invii = []
    monkeypatch.setattr(main, "genera_pdf_stima", lambda *a, **k: "/static/pdf/prova.pdf")
    monkeypatch.setattr(main, "invia_mail", lambda *a, **k: invii.append(("mail", a[0], a[1])) or True)
    monkeypatch.setattr(main, "invia_whatsapp", lambda *a, **k: invii.append(("whatsapp",)) or True)
    monkeypatch.setattr(main.core_service, "bridge_public_stima",
                        lambda *a, **k: {"status": "skipped", "reason": "test"})

    def stima(**campi):
        # come lo manda dati_personali.html: valori stringa
        corpo = {"comune": "Tortoreto", "microzona": "Lido Sud", "via": "Via del Mare", "civico": "12",
                 "tipologia": "Appartamento", "mq": "85", "piano": "3", "locali": "Trilocale", "bagni": "2",
                 "ascensore": "si", "anno": "1998", "stato": "ristrutturato",
                 "nome": "Serena", "cognome": "Prova", "email": f"p{uuid.uuid4().hex[:6]}@example.invalid",
                 "telefono": "3331234567", **campi}
        return asyncio.run(main.salva_stima(_Richiesta(corpo)))

    def riga(sid):
        r = q("SELECT consenso_marketing, consenso_marketing_at IS NOT NULL, consenso_marketing_testo, "
              "consenso_marketing_privacy_sha256 FROM stime WHERE id = %s", (sid,))[0]
        return tuple(r)

    try:
        yield {"stima": stima, "riga": riga, "q": q, "invii": invii}
    finally:
        conn.close()
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)')
        admin.close()


def _ufficiali():
    import site_official
    v = site_official.official_versions()
    return v["marketing_checkbox"][0]["text"], next(p["sha256"] for p in v["privacy"] if p["accepted"])


def _senza_id(r):
    return {k: v for k, v in r.items() if k not in ("id", "token", "link", "pdf_url", "link_token")}


def test_01_pagina_nuova_prova_conservata_risposta_identica(sito):
    testo, impronta = _ufficiali()
    senza = sito["stima"](consenso_marketing=True)
    invii_senza = list(sito["invii"])
    con = sito["stima"](consenso_marketing=True, consenso_marketing_testo=testo,
                        informativa_privacy_sha256=impronta)
    assert sito["riga"](con["id"]) == (True, True, testo, impronta)
    assert sito["riga"](senza["id"]) == (True, True, None, None)
    assert set(con) == set(senza) and _senza_id(con).keys() == _senza_id(senza).keys()
    assert con.get("price_exact") == senza.get("price_exact")
    nuovi = sito["invii"][len(invii_senza):]
    assert [i[0] for i in nuovi] == [i[0] for i in invii_senza], "stessi invii (email, WhatsApp)"


def test_02_valori_non_ufficiali_non_si_conservano(sito):
    testo, impronta = _ufficiali()
    storica = hashlib.sha256((UFFICIALI / "privacy-d615e05d.html").read_bytes()).hexdigest()
    casi = [({"consenso_marketing_testo": testo + " e SMS", "informativa_privacy_sha256": impronta}, (None, impronta)),
            ({"consenso_marketing_testo": testo, "informativa_privacy_sha256": storica}, (testo, None)),
            ({"consenso_marketing_testo": testo, "informativa_privacy_sha256": "c" * 64}, (testo, None)),
            ({"consenso_marketing_testo": ["x"], "informativa_privacy_sha256": "zz"}, (None, None))]
    for campi, atteso in casi:
        r = sito["stima"](consenso_marketing=True, **campi)
        assert r.get("id") and r.get("price_exact")
        assert sito["riga"](r["id"])[2:] == atteso, campi
        assert sito["riga"](r["id"])[:2] == (True, True), "la scelta resta"


def test_03_non_spuntata(sito):
    testo, impronta = _ufficiali()
    r = sito["stima"](consenso_marketing=False, consenso_marketing_testo=testo, informativa_privacy_sha256=impronta)
    assert sito["riga"](r["id"]) == (False, False, testo, impronta)


def test_04_rollback_della_112(sito):
    testo, impronta = _ufficiali()
    sito["q"]((MIGRAZIONI_C35 / "112_stime_consenso_marketing_prova_down.sql").read_text(encoding="utf-8"))
    r = sito["stima"](consenso_marketing=True, consenso_marketing_testo=testo, informativa_privacy_sha256=impronta)
    assert r.get("id") and r.get("price_exact")
    assert sito["q"]("SELECT consenso_marketing, consenso_marketing_at IS NOT NULL FROM stime WHERE id = %s",
                     (r["id"],))[0] == (True, True)
    sito["q"]((MIGRAZIONI_C35 / "112_stime_consenso_marketing_prova.sql").read_text(encoding="utf-8"))
    r2 = sito["stima"](consenso_marketing=True, consenso_marketing_testo=testo, informativa_privacy_sha256=impronta)
    assert sito["riga"](r2["id"]) == (True, True, testo, impronta)


def _esito_di_riferimento(sito):
    testo, impronta = _ufficiali()
    r = sito["stima"](consenso_marketing=True, consenso_marketing_testo=testo, informativa_privacy_sha256=impronta)
    return r, [i[0] for i in sito["invii"]]


def _guasto(sito, monkeypatch, guasta):
    """La stessa richiesta con il modulo dei consensi guasto: deve andare
    esattamente come una richiesta senza prova."""
    import site_consent_proof
    testo, impronta = _ufficiali()
    guasta(monkeypatch, site_consent_proof)
    prima = len(sito["invii"])
    r = sito["stima"](consenso_marketing=True, consenso_marketing_testo=testo, informativa_privacy_sha256=impronta)
    assert r.get("id") and r.get("price_exact"), r
    assert sito["riga"](r["id"])[:2] == (True, True), "stima e scelta salvate"
    assert [i[0] for i in sito["invii"][prima:]] == ["mail", "mail", "whatsapp"], sito["invii"][prima:]
    return r


def test_05_modulo_mancante(sito, monkeypatch):
    import sys
    rif, _ = _esito_di_riferimento(sito)
    r = _guasto(sito, monkeypatch, lambda mp, mod: mp.setitem(sys.modules, "site_consent_proof", None))
    assert r["price_exact"] == rif["price_exact"] and sito["riga"](r["id"])[2:] == (None, None)


def test_06_eccezione_nel_modulo(sito, monkeypatch):
    def rompi(mp, mod):
        def boom(*a, **k):
            raise RuntimeError("guasto simulato")
        mp.setattr(mod, "record_after_commit", boom)
    r = _guasto(sito, monkeypatch, rompi)
    assert sito["riga"](r["id"])[2:] == (None, None)


def test_07_versioni_ufficiali_illeggibili(sito, monkeypatch):
    import site_official

    def rompi(mp, mod):
        mp.setattr(site_official, "accepted_checkbox", lambda *a: (_ for _ in ()).throw(OSError("disco")))
    r = _guasto(sito, monkeypatch, rompi)
    assert sito["riga"](r["id"])[2:] == (None, None)


def test_08_vincolo_violato(sito, monkeypatch):
    r = _guasto(sito, monkeypatch, lambda mp, mod: mp.setattr(mod, "verified_proof", lambda raw: ("x", "non-hash")))
    assert sito["riga"](r["id"])[2:] == (None, None)


def test_09_database_lento_tempo_limitato(sito, monkeypatch):
    import time

    def lenta(mp, mod):
        mp.setattr(mod, "STATEMENT_TIMEOUT_MS", 300)
        mp.setattr(mod, "UPDATE_SQL", "SELECT pg_sleep(5), %s::text, %s::text, %s::int")
    inizio = time.monotonic()
    r = _guasto(sito, monkeypatch, lenta)
    assert time.monotonic() - inizio < 4, "il timeout del modulo interrompe l'attesa"
    assert sito["riga"](r["id"])[2:] == (None, None)
