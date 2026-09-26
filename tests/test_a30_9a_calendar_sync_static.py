"""A30-9A - le fondamenta della sincronizzazione calendario, SENZA database.

Cifratura, id evento deterministico (golden), payload senza dati personali,
hash canonico, classificazione degli errori e backoff, provider finto (zero
rete), piano di riconciliazione, confini del package e della migration.
Il comportamento su PostgreSQL vero e' in
`test_a30_9a_calendar_sync_postgres.py`.
"""
from __future__ import annotations

import ast
import re
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACCHETTO = ROOT / "calendar_sync"
MIGRAZIONE = ROOT / "migrations" / "074_a30_9a_calendar_sync.sql"
DOWN = ROOT / "migrations" / "074_a30_9a_calendar_sync_down.sql"
ROMA = ZoneInfo("Europe/Rome")


def _chiave():
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# CRITTOGRAFIA (7, 8, 9, 47)
# ---------------------------------------------------------------------------

def test_01_crypto_roundtrip_e_ciphertext_opaco():
    from calendar_sync import crypto
    ring = crypto.parse_keyring(f"k1:{_chiave()}")
    ct = ring.encrypt("1//refresh-token-segreto")
    assert ct.key_id == "k1" and b"refresh-token-segreto" not in ct.value
    assert ring.decrypt(ct) == "1//refresh-token-segreto"
    assert ring.decrypt((ct.value, ct.key_id)) == "1//refresh-token-segreto"


def test_02_crypto_rotazione_la_prima_chiave_cifra_le_altre_decifrano():
    from calendar_sync import crypto
    vecchia, nuova = _chiave(), _chiave()
    prima = crypto.parse_keyring(f"v1:{vecchia}")
    ct_vecchio = prima.encrypt("token")
    ruotato = crypto.parse_keyring(f"v2:{nuova}, v1:{vecchia}")
    assert ruotato.primary_key_id == "v2"
    assert ruotato.decrypt(ct_vecchio) == "token"                  # la vecchia decifra ancora
    nuovo = ruotato.rotate(ct_vecchio)
    assert nuovo.key_id == "v2" and ruotato.decrypt(nuovo) == "token"
    assert ruotato.rotate(nuovo) == nuovo                           # gia' corrente
    solo_nuova = crypto.parse_keyring(f"v2:{nuova}")
    assert solo_nuova.decrypt(nuovo) == "token"
    with pytest.raises(crypto.CalendarCryptoError, match="non disponibile"):
        solo_nuova.decrypt(ct_vecchio)                               # v1 ritirata


def test_03_crypto_chiave_sbagliata_errore_senza_dettagli():
    from calendar_sync import crypto
    a = crypto.parse_keyring(f"k1:{_chiave()}")
    b = crypto.parse_keyring(f"k1:{_chiave()}")
    ct = a.encrypt("token-segreto")
    with pytest.raises(crypto.CalendarCryptoError) as info:
        b.decrypt(ct)
    assert str(info.value) == "decifratura non riuscita"
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_04_crypto_configurazione_assente_controllata_e_l_import_non_legge_env(monkeypatch):
    import importlib

    from calendar_sync import crypto
    monkeypatch.delenv(crypto.ENV_KEYS, raising=False)
    importlib.reload(crypto)                                         # import innocuo
    assert crypto.is_configured() is False and crypto.load_keyring() is None
    with pytest.raises(crypto.CalendarCryptoNotConfigured):
        crypto.require_keyring()
    for vuoto in ("", "   "):
        assert crypto.load_keyring({crypto.ENV_KEYS: vuoto}) is None
    ring = crypto.require_keyring({crypto.ENV_KEYS: f"k1:{_chiave()}"})
    assert ring.primary_key_id == "k1"


@pytest.mark.parametrize("raw", ["k1", "k1:non-una-chiave", "k!:{}", "k1:{},k1:{}"])
def test_05_crypto_configurazione_sbagliata_fail_closed_senza_eco(raw):
    from calendar_sync import crypto
    chiave = _chiave()
    raw = raw.format(chiave, chiave)
    with pytest.raises(crypto.CalendarCryptoError) as info:
        crypto.parse_keyring(raw)
    assert chiave not in str(info.value) and "non-una-chiave" not in str(info.value)


def test_06_nessun_segreto_in_repr():
    from calendar_sync import crypto
    from calendar_sync.provider import ProviderAuth
    chiave = _chiave()
    ring = crypto.parse_keyring(f"k1:{chiave}")
    ct = ring.encrypt("token-segretissimo")
    for testo in (repr(ring), repr(ct), repr(ProviderAuth(7, "token-segretissimo"))):
        assert chiave not in testo and "token-segretissimo" not in testo
        assert ct.value.decode() not in testo


# ---------------------------------------------------------------------------
# ID EVENTO DETERMINISTICO (10) - GOLDEN
# ---------------------------------------------------------------------------

#: (namespace di deployment, agenzia, radice) -> id. Il namespace entra nel
#: digest: TEST e PROD con gli stessi id numerici danno id DIVERSI.
GOLDEN = {
    ("stima360-test", 35, 10): "s360nfp3bns6hit70e8spbfatddkc5alv3r70o4esieem2m50f6iculg",
    ("stima360-prod", 35, 10): "s360fqosvm4ln6u8a51au846v0cggjn7h3uji8hhqeaehd7ugi8p7q5g",
    ("stima360-test", 35, 11): "s360002vdaac7nofv7ihkigp3f9ttoq6of1rf764trlegsfebpltoal0",
    ("stima360-test", 36, 10): "s36020bfqasmtg7m81ol1vfoh1bo77n4g0108s6juvdr813v5vs8paeg",
    ("stima360-prod", 1, 1): "s360bpbrebb3mnf44qpevaifu0oo0nnqb2nf965fclhcvc72k96apvp0",
}
EVENTO = GOLDEN[("stima360-test", 35, 10)]


def test_10_event_id_golden_stabile_e_nel_charset_google():
    from calendar_sync.service import deterministic_event_id
    for (ns, agenzia, radice), atteso in GOLDEN.items():
        assert deterministic_event_id(ns, agenzia, radice) == atteso
        assert deterministic_event_id(ns, agenzia, radice) == atteso        # identico al retry
    for valore in GOLDEN.values():
        # contratto Google: base32hex minuscolo [0-9a-v], 5..1024 caratteri
        assert re.fullmatch(r"[0-9a-v]{5,1024}", valore) and len(valore) == 56
    assert len(set(GOLDEN.values())) == len(GOLDEN)


def test_10b_namespace_di_deployment_test_e_prod_mai_lo_stesso_id():
    from calendar_sync.service import deterministic_event_id
    test = deterministic_event_id("stima360-test", 35, 10)
    prod = deterministic_event_id("stima360-prod", 35, 10)
    assert test != prod                                            # stessi id numerici
    assert test == deterministic_event_id("stima360-test", 35, 10)  # stesso namespace: stabile
    for id_ in (test, prod):                                       # nome d'ambiente mai in chiaro
        assert "test" not in id_ and "prod" not in id_ and "stima" not in id_
    # nessuna ambiguita' di concatenazione fra i campi
    assert deterministic_event_id("ns-1", 23, 4) != deterministic_event_id("ns-12", 3, 4)


@pytest.mark.parametrize("ns", [None, "", "ab", "Stima360-TEST", "stima 360", "x" * 65,
                                "-inizia-male", 360])
def test_10c_namespace_obbligatorio_e_valido(ns):
    from calendar_sync.service import deterministic_event_id
    with pytest.raises(ValueError):
        deterministic_event_id(ns, 35, 10)


def test_10d_ingressi_numerici_interi():
    from calendar_sync.service import deterministic_event_id
    for agenzia, radice in (("35", 10), (35, "10"), (True, 10), (35, 1.0)):
        with pytest.raises(TypeError):
            deterministic_event_id("stima360-test", agenzia, radice)


def test_11_event_id_solo_deployment_agenzia_e_radice_niente_pii():
    import inspect

    from calendar_sync import repository, service
    firma = inspect.signature(service.deterministic_event_id)
    assert list(firma.parameters) == ["deployment_namespace", "agency_id",
                                      "chain_root_appointment_id"]
    corpo = inspect.getsource(service.deterministic_event_id)
    assert "sha256" in corpo and "b32hexencode" in corpo
    # il dominio non conosce nomi d'ambiente: nessun default, nessun valore cablato
    for nome in ("ensure_sync_row", "mark_dirty_with_cursor"):
        parametro = inspect.signature(getattr(repository, nome)).parameters["deployment_namespace"]
        assert parametro.default is inspect.Parameter.empty
        assert parametro.kind is inspect.Parameter.KEYWORD_ONLY
    for file in PACCHETTO.glob("*.py"):
        testo = file.read_text(encoding="utf-8")
        for vietato in ('"stima360-test"', '"stima360-prod"', "RENDER_SERVICE", "current_database",
                        "stima360_db_test"):
            assert vietato not in testo, (file.name, vietato)


# ---------------------------------------------------------------------------
# PAYLOAD (27, 28)
# ---------------------------------------------------------------------------

def _appuntamento(**kw):
    riga = {"id": 12, "agency_id": 35, "appointment_type": "inspection", "status": "scheduled",
            "start_at": datetime(2030, 1, 8, 9, 0, tzinfo=timezone.utc),
            "end_at": datetime(2030, 1, 8, 10, 0, tzinfo=timezone.utc),
            "assigned_user_id": 57, "timezone": "Europe/Rome",
            # tutto cio' che NON deve uscire
            "notes": "Citofono Rossi, chiave dal vicino", "location_text": "Via Roma 1, Giulianova",
            "contact_name": "Mario Rossi", "phone": "3331234567", "email": "mario@example.it",
            "cancelled_reason": "motivo", "outcome_note": "nota esito", "lead_id": 501,
            "stima_id": 900, "property_id": 77, "contact_id": 41}
    riga.update(kw)
    return riga


def test_27_payload_senza_dati_personali():
    from calendar_sync.service import build_payload
    p = build_payload(_appuntamento(), agency_id=35, chain_root_appointment_id=10,
                      event_id=EVENTO)
    assert p.summary == "Sopralluogo" and p.description == "Stima360"
    assert p.timezone == "Europe/Rome" and p.start_at.tzinfo is not None
    assert p.start_at == datetime(2030, 1, 8, 10, 0, tzinfo=ROMA)
    assert p.private_properties == {"stima360_chain_id": "10", "stima360_appointment_id": "12",
                                    "stima360_origin": "stima360_crm"}
    testo = repr(p) + str(p.__dict__)
    for vietato in ("Rossi", "Giulianova", "333", "@", "Citofono", "motivo", "nota esito",
                    "501", "900", "77", "57"):
        assert vietato not in testo.replace(EVENTO, ""), vietato
    campi = set(p.__dataclass_fields__)
    assert campi == {"event_id", "summary", "start_at", "end_at", "timezone", "description",
                     "private_properties"}                         # niente location/attendees
    with pytest.raises(ValueError):
        build_payload(_appuntamento(agency_id=36), agency_id=35, chain_root_appointment_id=10,
                      event_id=EVENTO)


def test_27b_etichette_neutre_per_ogni_tipo():
    from appointments.enums import APPOINTMENT_TYPES
    from calendar_sync.service import build_payload
    for tipo in APPOINTMENT_TYPES:
        p = build_payload(_appuntamento(appointment_type=tipo), agency_id=35,
                          chain_root_appointment_id=10, event_id=EVENTO)
        assert p.summary in {"Sopralluogo", "Appuntamento", "Visita", "Telefonata",
                             "Videochiamata"}, tipo


def test_28_hash_canonico_e_stabile():
    from calendar_sync.service import build_payload, payload_hash
    base = _appuntamento()
    a = build_payload(base, agency_id=35, chain_root_appointment_id=10, event_id=EVENTO)
    stesso = _appuntamento(start_at=base["start_at"].astimezone(ROMA),
                           end_at=base["end_at"].astimezone(timezone(timedelta(hours=-5))),
                           notes="altro", location_text="altro indirizzo")
    b = build_payload(stesso, agency_id=35, chain_root_appointment_id=10,
                      event_id=EVENTO)
    assert payload_hash(a) == payload_hash(b)                  # stessi istanti, stessi campi
    assert re.fullmatch(r"[0-9a-f]{64}", payload_hash(a))
    spostato = build_payload(_appuntamento(start_at=base["start_at"] + timedelta(minutes=30)),
                             agency_id=35, chain_root_appointment_id=10,
                             event_id=EVENTO)
    assert payload_hash(spostato) != payload_hash(a)


# ---------------------------------------------------------------------------
# ERRORI E BACKOFF (22-26)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,reason,op,azione", [
    ("rate_limited", None, "ensure", "retry"),
    ("server_error", None, "ensure", "retry"),
    ("timeout", None, "ensure", "retry"),
    ("network", None, "delete", "retry"),
    ("conflict", None, "ensure", "retry"),
    ("forbidden", "rateLimitExceeded", "ensure", "retry"),
    ("forbidden", "userRateLimitExceeded", "ensure", "retry"),
    ("forbidden", "insufficientPermissions", "ensure", "needs_reauth"),
    ("forbidden", None, "ensure", "needs_reauth"),
    ("unauthorized", None, "ensure", "needs_reauth"),
    ("invalid_grant", None, "delete", "needs_reauth"),
    ("not_found", None, "delete", "success"),
    ("gone", None, "delete", "success"),
    ("not_found", None, "ensure", "retry"),
    ("bad_request", None, "ensure", "failed"),
])
def test_22_26_classificazione(kind, reason, op, azione):
    from calendar_sync.provider import CalendarProviderError, classify
    c = classify(CalendarProviderError(kind, reason=reason, operation=op))
    assert c.action == azione
    assert re.fullmatch(r"[a-z0-9_]{1,64}", c.code)


def test_26b_backoff_deterministico_con_tetto_e_tentativi_massimi():
    from calendar_sync import constants as k
    from calendar_sync.provider import backoff_seconds, exhausted
    assert [backoff_seconds(n) for n in range(1, 9)] == \
        [60, 180, 540, 1620, 4860, 14580, 21600, 21600]
    assert k.MAX_ATTEMPTS == 8
    assert [exhausted(n) for n in (1, 7, 8, 9)] == [False, False, True, True]
    with pytest.raises(ValueError):
        backoff_seconds(0)


def test_26c_errore_del_provider_senza_dettagli_remoti():
    from calendar_sync.provider import CalendarProviderError
    e = CalendarProviderError("unauthorized", http_status=401)
    assert str(e) == "unauthorized (401)"
    with pytest.raises(ValueError):
        CalendarProviderError("boh")


# ---------------------------------------------------------------------------
# PROVIDER FINTO (46): semantica del contratto, zero rete
# ---------------------------------------------------------------------------

def _payload(**kw):
    from calendar_sync.service import build_payload
    return build_payload(_appuntamento(**kw), agency_id=35, chain_root_appointment_id=10,
                         event_id=EVENTO)


def test_46_fake_semantica_e_zero_rete(monkeypatch):
    from calendar_sync.fake_provider import FakeCalendarProvider, SimulatedCrash
    from calendar_sync.provider import CalendarProviderError, ProviderAuth

    def niente_rete(*a, **kw):
        raise AssertionError("il provider finto ha aperto un socket")

    monkeypatch.setattr(socket, "socket", niente_rete)
    monkeypatch.setattr(socket, "create_connection", niente_rete)
    fake, auth = FakeCalendarProvider(), ProviderAuth(1, "t")
    p = _payload()
    assert fake.ensure_event(auth, "primary", p).outcome == "created"
    assert fake.ensure_event(auth, "primary", p).outcome == "unchanged"
    assert fake.ensure_event(auth, "primary", _payload(appointment_type="call")).outcome == "updated"
    fake.vanish(1, "primary", p.event_id)                                  # 404 remoto
    assert fake.ensure_event(auth, "primary", p).outcome == "created"      # ricreato, stesso id
    assert list(fake.active_events(1)) == [p.event_id]
    assert fake.delete_event(auth, "primary", p.event_id).outcome == "deleted"
    assert fake.delete_event(auth, "primary", p.event_id).outcome == "absent"   # 410
    assert fake.delete_event(auth, "primary", "s360nonesiste").outcome == "absent"  # 404
    assert fake.ensure_event(auth, "primary", p).outcome == "updated"      # riattivato (409->patch)
    fake.fail_next("ensure", "rate_limited", http_status=429)
    with pytest.raises(CalendarProviderError) as info:
        fake.ensure_event(auth, "primary", p)
    assert info.value.kind == "rate_limited"
    fake.crash_after_success_next("delete")
    with pytest.raises(SimulatedCrash):
        fake.delete_event(auth, "primary", p.event_id)
    assert fake.active_events(1) == {}                                     # il remoto e' cambiato


def test_46b_il_package_non_importa_librerie_di_rete():
    vietati = {"requests", "httpx", "httpx2", "urllib", "urllib3", "socket", "http",
               "aiohttp", "googleapiclient", "google"}
    for file in PACCHETTO.glob("*.py"):
        albero = ast.parse(file.read_text(encoding="utf-8"))
        for nodo in ast.walk(albero):
            if isinstance(nodo, ast.Import):
                nomi = [a.name.split(".")[0] for a in nodo.names]
            elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0:
                nomi = [nodo.module.split(".")[0]]
            else:
                continue
            assert not (set(nomi) & vietati), (file.name, nomi)


# ---------------------------------------------------------------------------
# PIANO DI RICONCILIAZIONE (29-36, 39-41) - funzione pura
# ---------------------------------------------------------------------------

def _conn(i, stato="connected", membership=True):
    """Come la restituisce il repository: `usable` e `membership_active`
    calcolati a ogni lettura."""
    return {"id": i, "status": stato, "calendar_id": "primary",
            "membership_active": membership,
            "usable": stato == "connected" and membership}


def _riga(remote=None, hash_=None):
    return {"remote_connection_id": remote, "synced_payload_hash": hash_}


@pytest.mark.parametrize("stato", ["scheduled", "confirmed"])
def test_29_30_fissato_o_confermato_evento_sul_calendario_dell_agente(stato):
    from calendar_sync.service import plan_reconciliation
    p = plan_reconciliation(appointment={"status": stato}, sync_row=_riga(),
                            desired_connection=_conn(1), remote_connection=None, desired_hash="h")
    assert p.ensure_on["id"] == 1 and p.delete_on is None and p.final_status == "synced"
    gia = plan_reconciliation(appointment={"status": stato}, sync_row=_riga(1, "h"),
                              desired_connection=_conn(1), remote_connection=_conn(1),
                              desired_hash="h")
    assert gia.ensure_on is None and gia.final_status == "synced"          # nulla da fare


@pytest.mark.parametrize("stato", ["requested", "completed", "no_show"])
def test_31_33_34_nessuna_azione_remota(stato):
    from calendar_sync.service import plan_reconciliation
    for riga, remoto in ((_riga(), None), (_riga(1, "h"), _conn(1))):
        p = plan_reconciliation(appointment={"status": stato}, sync_row=riga,
                                desired_connection=_conn(1), remote_connection=remoto,
                                desired_hash=None)
        assert p.ensure_on is None and p.delete_on is None and p.final_status == "synced"


def test_32_annullato_evento_assente():
    from calendar_sync.service import plan_reconciliation
    casi = [
        (_riga(), None, None, "synced", None),
        (_riga(1), _conn(1), 1, "synced", None),
        (_riga(1), _conn(1, "needs_reauth"), None, "needs_reauth", "connection_needs_reauth"),
        (_riga(1), _conn(1, "disconnected"), None, "detached", "disconnected"),
    ]
    for riga, remoto, cancella, finale, codice in casi:
        p = plan_reconciliation(appointment={"status": "cancelled"}, sync_row=riga,
                                desired_connection=_conn(1), remote_connection=remoto,
                                desired_hash=None)
        assert (p.delete_on["id"] if p.delete_on else None) == cancella
        assert p.ensure_on is None and (p.final_status, p.final_code) == (finale, codice)


def test_39_41_riassegnazione():
    from calendar_sync.service import plan_reconciliation
    # A -> B, entrambe collegate: prima togli da A, poi assicura su B
    p = plan_reconciliation(appointment={"status": "scheduled"}, sync_row=_riga(1, "h"),
                            desired_connection=_conn(2), remote_connection=_conn(1),
                            desired_hash="h")
    assert p.delete_on["id"] == 1 and p.ensure_on["id"] == 2 and p.final_status == "synced"
    # B senza connessione: togli da A, NON ricreare altrove
    p = plan_reconciliation(appointment={"status": "scheduled"}, sync_row=_riga(1, "h"),
                            desired_connection=None, remote_connection=_conn(1),
                            desired_hash="h")
    assert p.delete_on["id"] == 1 and p.ensure_on is None
    assert p.final_status == "waiting_connection"
    # A non piu' raggiungibile: il mapping la dimentica, B riceve l'evento
    p = plan_reconciliation(appointment={"status": "scheduled"}, sync_row=_riga(1, "h"),
                            desired_connection=_conn(2), remote_connection=_conn(1, "needs_reauth"),
                            desired_hash="h")
    assert p.delete_on is None and p.abandon_remote and p.ensure_on["id"] == 2
    # B da ri-autorizzare
    p = plan_reconciliation(appointment={"status": "scheduled"}, sync_row=_riga(),
                            desired_connection=_conn(2, "needs_reauth"), remote_connection=None,
                            desired_hash="h")
    assert p.ensure_on is None and p.final_status == "needs_reauth"


def test_41b_membership_sospesa_la_connessione_non_si_usa_mai():
    """Final review, punto 2: stato `connected` e token presenti, ma membership
    non piu' attiva -> connessione NON utilizzabile: nessun ensure, nessun
    delete (neppure degli eventi dell'utente sospeso), waiting_connection."""
    from calendar_sync.service import plan_reconciliation
    sospesa = _conn(1, "connected", membership=False)
    # fissato, agente sospeso, nessun remoto
    p = plan_reconciliation(appointment={"status": "scheduled"}, sync_row=_riga(),
                            desired_connection=sospesa, remote_connection=None, desired_hash="h")
    assert (p.ensure_on, p.delete_on, p.abandon_remote) == (None, None, False)
    assert (p.final_status, p.final_code) == ("waiting_connection", "no_connection")
    # l'evento vive gia' sulla STESSA connessione sospesa: il mapping resta
    p = plan_reconciliation(appointment={"status": "scheduled"}, sync_row=_riga(1, "h"),
                            desired_connection=sospesa, remote_connection=sospesa,
                            desired_hash="h")
    assert (p.ensure_on, p.delete_on, p.abandon_remote) == (None, None, False)
    assert p.final_status == "waiting_connection"
    # annullato con l'evento sulla connessione sospesa: niente DELETE
    p = plan_reconciliation(appointment={"status": "cancelled"}, sync_row=_riga(1),
                            desired_connection=sospesa, remote_connection=sospesa,
                            desired_hash=None)
    assert p.delete_on is None and p.final_status == "waiting_connection"
    # riassegnato da A (sospeso) a B (valido): A non si tocca, B riceve l'evento
    p = plan_reconciliation(appointment={"status": "scheduled"}, sync_row=_riga(1, "h"),
                            desired_connection=_conn(2), remote_connection=sospesa,
                            desired_hash="h")
    assert p.delete_on is None and p.abandon_remote and p.ensure_on["id"] == 2
    # needs_reauth con membership sospesa: non c'e' nessuno da ri-autorizzare
    p = plan_reconciliation(appointment={"status": "scheduled"}, sync_row=_riga(),
                            desired_connection=_conn(1, "needs_reauth", membership=False),
                            remote_connection=None, desired_hash="h")
    assert p.final_status == "waiting_connection"


def test_42_riga_viva_spostata_catena_rotta():
    from calendar_sync.service import plan_reconciliation
    p = plan_reconciliation(appointment={"status": "rescheduled"}, sync_row=_riga(),
                            desired_connection=_conn(1), remote_connection=None, desired_hash=None)
    assert (p.final_status, p.final_code) == ("failed", "chain_broken")


# ---------------------------------------------------------------------------
# CONFINI: package, migration, costanti
# ---------------------------------------------------------------------------

def test_50_nessuno_importa_calendar_sync_in_a30_9a():
    for file in [ROOT / "main.py", *ROOT.glob("appointments/*.py"),
                 *ROOT.glob("appointments_legacy/*.py")]:
        assert "calendar_sync" not in file.read_text(encoding="utf-8"), file.name


def test_51_costanti_specchio_dei_check_della_074():
    from calendar_sync import constants as k
    sql = MIGRAZIONE.read_text(encoding="utf-8")
    for stato in k.SYNC_STATUSES:
        assert f"'{stato}'" in sql
    blocco = sql[sql.index("appointment_calendar_sync_status_chk"):]
    blocco = blocco[:blocco.index(")),")]
    assert sorted(re.findall(r"'(\w+)'", blocco)) == sorted(k.SYNC_STATUSES)
    blocco = sql[sql.index("calendar_connections_status_chk"):]
    blocco = blocco[:blocco.index(")),")]
    assert sorted(re.findall(r"'(\w+)'", blocco)) == sorted(k.CONNECTION_STATUSES)


def test_52_la_074_non_tocca_appointments_ne_google_della_072():
    sql = MIGRAZIONE.read_text(encoding="utf-8")
    codice = "\n".join(r for r in sql.splitlines() if not r.lstrip().startswith("--"))
    assert not re.search(r"ALTER\s+TABLE\s+appointments", codice, re.I)
    assert not re.search(r"(INSERT\s+INTO|UPDATE)\s+appointments\b", codice, re.I)
    assert "google_" not in codice
    assert "access_token" not in codice and "client_secret" not in codice
    # tre tabelle, non una di piu'
    assert sorted(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", codice)) == [
        "appointment_calendar_sync", "calendar_connections", "calendar_oauth_states"]


def test_53_la_down_toglie_solo_la_074():
    down = DOWN.read_text(encoding="utf-8")
    codice = "\n".join(r for r in down.splitlines() if not r.lstrip().startswith("--"))
    assert sorted(re.findall(r"DROP TABLE IF EXISTS (\w+)", codice)) == [
        "appointment_calendar_sync", "calendar_connections", "calendar_oauth_states"]
    assert sorted(re.findall(r"DROP FUNCTION IF EXISTS (\w+)", codice)) == [
        "appointment_calendar_sync_guard", "calendar_connections_guard"]
    assert "google_" not in codice and "appointments " not in codice.replace(
        "appointment_calendar_sync", "")
    assert "DELETE FROM schema_migrations WHERE version = '074_a30_9a_calendar_sync'" in codice


def test_54_nessun_log_di_segreti_nel_package():
    for file in PACCHETTO.glob("*.py"):
        testo = file.read_text(encoding="utf-8")
        for chiamata in re.findall(r"log\.\w+\((.*?)\)\n", testo, re.S):
            for vietato in ("token", "ciphertext", "secret", "refresh", "payload"):
                assert vietato not in chiamata.lower(), (file.name, chiamata)
