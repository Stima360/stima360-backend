"""Query reali dell'archivio su uno schema sintetico PostgreSQL usa-e-getta.

Non applica migration del progetto e non si collega ad ambienti operativi.
"""

import os
import uuid

import psycopg2
import pytest
from fastapi import HTTPException

from crm import valuations
from operator_auth.context import OperatorContext


DSN = os.environ.get("P29_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN locale non configurato")


def _ctx(agency_id):
    return OperatorContext(user_id=6, agency_id=agency_id, role="agency_owner",
                           is_platform_admin=False, session_id=1, auth_channel="operator_session")


@pytest.fixture
def db(monkeypatch):
    from core import database as core_db

    schema = "stime_ui_" + uuid.uuid4().hex[:12]
    admin = psycopg2.connect(DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'''SET search_path TO "{schema}"
        ''')
        cur.execute("""
          CREATE TABLE stime_ui_agencies (id bigint PRIMARY KEY);
          CREATE TABLE stime (
            id integer PRIMARY KEY, agency_id bigint NOT NULL REFERENCES stime_ui_agencies(id),
            data timestamptz, comune text, microzona text, via text, civico text,
            tipologia text, mq integer, piano text, nome text, cognome text,
            email text, telefono text, locali text, bagni integer, ascensore text,
            pertinenze text, anno text, stato text, posizionemare text,
            distanzamare text, barrieramare text, vistamare text,
            mqgiardino integer, mqgarage integer, mqcantina integer,
            mqpostoauto integer, mqtaverna integer, mqsoffitta integer,
            mqterrazzo integer, numbalconi integer, altrodescrizione text
          );
          CREATE TABLE stime_dettagliate (
            id integer PRIMARY KEY, agency_id bigint NOT NULL REFERENCES stime_ui_agencies(id),
            stima_id integer NOT NULL REFERENCES stime(id), data timestamptz,
            classe text, riscaldamento text, condizionatore text, spese_cond integer,
            esposizione text, arredo text, note text, contatto text, sopralluogo timestamptz
          );
          CREATE TABLE contacts (id bigint PRIMARY KEY, agency_id bigint NOT NULL REFERENCES stime_ui_agencies(id));
          CREATE TABLE leads (id bigint PRIMARY KEY, agency_id bigint NOT NULL REFERENCES stime_ui_agencies(id),
                              contact_id bigint NOT NULL REFERENCES contacts(id));
          CREATE TABLE lead_stime (lead_id bigint NOT NULL REFERENCES leads(id),
                                   stima_id integer NOT NULL REFERENCES stime(id));
          CREATE TABLE properties (id bigint PRIMARY KEY, agency_id bigint NOT NULL REFERENCES stime_ui_agencies(id));
          CREATE TABLE property_site_sources (
            id bigint PRIMARY KEY, agency_id bigint NOT NULL REFERENCES stime_ui_agencies(id),
            stima_id integer NOT NULL REFERENCES stime(id), property_id bigint NOT NULL REFERENCES properties(id),
            lead_id bigint REFERENCES leads(id), contact_id bigint REFERENCES contacts(id), status text NOT NULL
          );
          CREATE UNIQUE INDEX one_active_source ON property_site_sources(stima_id) WHERE status='active';
          CREATE TABLE stima_pdf_artifacts (
            stima_id integer PRIMARY KEY REFERENCES stime(id),
            agency_id bigint NOT NULL REFERENCES stime_ui_agencies(id), status text NOT NULL,
            render_payload jsonb NOT NULL DEFAULT '{}'::jsonb
          );
          INSERT INTO stime_ui_agencies VALUES (1), (35);
          INSERT INTO stime (id,agency_id,data,nome,cognome,comune,via,mq,pertinenze,mqgarage)
          VALUES (10,1,'2026-10-01','Ada','Rossi','Alba Adriatica','Via Lago',82,'garage',17),
                 (11,1,'2026-10-02','A%_!','Letterale','Giulianova','Via Uno',65,NULL,NULL),
                 (20,35,'2026-10-03','Bruno','Verdi','Giulianova','Via Due',70,NULL,NULL);
          INSERT INTO stime_dettagliate (id,agency_id,stima_id,data,classe)
          VALUES (31,1,10,'2026-10-04','A'), (32,1,10,'2026-10-05','B'),
                 (33,35,20,'2026-10-05','C');
          INSERT INTO contacts VALUES (100,1),(101,1),(200,35);
          INSERT INTO leads VALUES (110,1,100),(111,1,101),(210,35,200);
          INSERT INTO lead_stime VALUES (110,10),(111,10),(210,20);
          INSERT INTO properties VALUES (120,1),(220,35);
          INSERT INTO property_site_sources VALUES (1,1,10,120,110,100,'active');
          INSERT INTO stima_pdf_artifacts(stima_id,agency_id,status,render_payload)
          VALUES (10,1,'ready','{"price_exact":185000}'::jsonb),
                 (20,35,'pending','{"price_exact":210000}'::jsonb);
        """)

    def connection():
        conn = psycopg2.connect(DSN)
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}"')
        return conn

    monkeypatch.setattr(core_db, "get_connection", connection)
    try:
        yield connection
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def test_single_archive_detail_links_and_pdf_state(db):
    all_rows = valuations.list_stime(_ctx(1), view="all", search=None, limit=30, offset=0)
    assert all_rows["total"] == 2
    assert all_rows["stats"] == {"total": 2, "detailed": 1}
    assert len(all_rows["items"]) == 2
    assert valuations.list_stime(_ctx(1), view="detailed", search=None, limit=30, offset=0)["total"] == 1
    assert valuations.list_stime(_ctx(1), view="base", search=None, limit=30, offset=0)["total"] == 1
    row = valuations.get_stima(_ctx(1), 10)
    assert (row["detail_id"], row["classe"], row["property_id"], row["contact_id"], row["lead_id"]) == (32, "B", 120, 100, 110)
    assert row["pdf_status"] == "ready"
    assert row["price_exact"] == "185000"
    assert (row["pertinenze"], row["mqgarage"]) == ("garage", 17)


def test_agency_scope_literal_search_and_pagination(db):
    assert valuations.list_stime(_ctx(35), view="all", search=None, limit=30, offset=0)["total"] == 1
    with pytest.raises(HTTPException) as error:
        valuations.get_stima(_ctx(35), 10)
    assert error.value.status_code == 404
    assert valuations.get_stima(_ctx(35), 20)["pdf_status"] == "pending"
    assert valuations.list_stime(_ctx(1), view="all", search="%_!", limit=30, offset=0)["total"] == 1
    first = valuations.list_stime(_ctx(1), view="all", search=None, limit=1, offset=0)
    second = valuations.list_stime(_ctx(1), view="all", search=None, limit=1, offset=1)
    assert first["has_more"] and not second["has_more"]
    assert first["items"][0]["id"] != second["items"][0]["id"]


def test_ambiguous_leads_do_not_select_an_arbitrary_person(db):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM property_site_sources WHERE stima_id=10")
        conn.commit()
    row = valuations.get_stima(_ctx(1), 10)
    assert row["lead_id"] is None and row["contact_id"] is None and row["property_id"] is None
