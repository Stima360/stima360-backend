"""A32-2 - il planner dei promemoria, SENZA database (P1-P22 + orizzonte).

Il repository e `communication.service.enqueue` sono sostituiti da un mondo
finto che si comporta come il ledger vero per cio' che conta qui: una chiave
gia' usata nella stessa agenzia restituisce la riga esistente con
`created=False`. Le stesse prove, sul PostgreSQL vero e con il dispatcher
vero, stanno in `test_a32_2_reminder_postgres.py`.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from appointment_reminders import planner, policy, repository, template
from operator_auth.context import OperatorContext, SystemAgencyContext

ROMA = ZoneInfo("Europe/Rome")
UTC = timezone.utc


def roma(anno, mese, giorno, ora, minuto=0):
    return datetime(anno, mese, giorno, ora, minuto, tzinfo=ROMA)


#: Un giovedi' qualunque, lontano dai cambi d'ora.
ADESSO = roma(2031, 6, 12, 10, 0)


def operatore(agency_id=7, ruolo="agency_owner"):
    return OperatorContext(user_id=42, agency_id=agency_id, role=ruolo,
                           is_platform_admin=False, session_id=None,
                           auth_channel="session")


def appuntamento(i=101, *, agency_id=7, tipo="seller_meeting", fonte="crm_manual",
                 stato="scheduled", inizio=None, creato=None, contatto=501,
                 immobile=None):
    inizio = inizio or roma(2031, 6, 13, 10, 0)
    return {"id": i, "agency_id": agency_id, "appointment_type": tipo, "status": stato,
            "source": fonte, "start_at": inizio,
            "created_at": creato or inizio - timedelta(days=5),
            "contact_id": contatto, "property_id": immobile}


def contatto(i=501, *, stato="active", email="mario@example.it", nome="Mario"):
    return {"id": i, "status": stato, "email": email, "first_name": nome}


class Mondo:
    """Repository + ledger finti, con l'idempotenza per (agenzia, chiave)."""

    def __init__(self):
        self.candidati: dict[int, list[dict]] = {}
        self.ledger: dict[tuple[int, str], dict] = {}
        self.accodati: list[dict] = []
        self.letture: list[int] = []
        self.migrata = True
        self.nome_agenzia = {7: "Agenzia Mare", 8: "Agenzia Monti"}

    def aggiungi(self, app, cont=None, indirizzo=None):
        self.candidati.setdefault(app["agency_id"], []).append(
            {"appointment": app, "contact": cont, "property_address": indirizzo})

    # --- repository
    def schema_ready(self, cur):
        return self.migrata

    def agency_name(self, cur, agency_id):
        return self.nome_agenzia.get(agency_id)

    def candidates(self, cur, agency_id, *, now, limit=repository.MAX_CANDIDATES):
        self.letture.append(agency_id)
        righe = sorted(self.candidati.get(agency_id, []),
                       key=lambda r: (r["appointment"]["start_at"], r["appointment"]["id"]))
        return righe[:limit]

    # --- ledger
    def enqueue(self, ctx, **kw):
        self.accodati.append({"ctx": ctx, **kw})
        chiave = (ctx.agency_id, kw["idempotency_key"])
        if chiave in self.ledger:
            return {"message": self.ledger[chiave], "created": False}
        riga = {"id": len(self.ledger) + 1, "agency_id": ctx.agency_id, **kw}
        self.ledger[chiave] = riga
        return {"message": riga, "created": True}


@pytest.fixture
def mondo(monkeypatch):
    m = Mondo()

    @contextmanager
    def cursore(**_kw):
        yield None, None

    monkeypatch.setattr(planner, "communication_cursor", cursore)
    for nome in ("schema_ready", "agency_name", "candidates"):
        monkeypatch.setattr(repository, nome, getattr(m, nome))
    monkeypatch.setattr(planner.communication_service, "enqueue", m.enqueue)
    return m


def giro(mondo, now=ADESSO, ctx=None):
    return planner.tick(ctx or operatore(), now=now)


# ---------------------------------------------------------------------------
# P1 - P5: il caso buono, l'idempotenza, i dati del template
# ---------------------------------------------------------------------------

def test_P1_appuntamento_idoneo_e_dovuto_un_promemoria_email_in_coda(mondo):
    mondo.aggiungi(appuntamento(), contatto())
    c = giro(mondo)
    assert (c["scanned"], c["due"], c["queued"], c["queued_idempotent"], c["errors"]) == \
        (1, 1, 1, 0, 0)
    [m] = mondo.accodati
    inizio = roma(2031, 6, 13, 10, 0)
    assert m["channel"] == "email" and m["communication_type"] == "service"
    assert m["mode"] == "automatic" and m["reason_code"] == "appointment_reminder"
    assert m["template_key"] == "appointment_reminder_24h" and m["template_version"] == 1
    assert m["contact_id"] == 501 and m["destination_snapshot"] == "mario@example.it"
    assert m["idempotency_key"] == f"appointment_reminder:v1:101:24h:{int(inizio.timestamp())}"
    assert m["idempotency_key"] == policy.occurrence_key(101, inizio)
    assert m["metadata"] == {"kind": "appointment_reminder", "appointment_id": 101,
                             "offset": "24h", "occurrence_key": m["idempotency_key"],
                             "start_epoch": int(inizio.timestamp())}
    oggetto, corpo = template.render_email(appointment_type="seller_meeting",
                                           start_at=inizio, agency_name="Agenzia Mare",
                                           customer_name="Mario")
    assert (m["subject_snapshot"], m["rendered_body"]) == (oggetto, corpo)
    # lo scope e' di sistema, della SOLA agenzia della sessione, con l'origin suo
    assert type(m["ctx"]) is SystemAgencyContext
    assert (m["ctx"].agency_id, m["ctx"].origin) == (7, "appointment_reminder")
    # nessun argomento fuori dalla lista
    assert set(m) - {"ctx"} == {
        "contact_id", "channel", "communication_type", "mode", "reason_code",
        "rendered_body", "destination_snapshot", "idempotency_key", "subject_snapshot",
        "template_key", "template_version", "metadata"}


def test_P2_stesso_giro_due_volte_stessa_riga_queued_idempotent(mondo):
    mondo.aggiungi(appuntamento(), contatto())
    primo, secondo = giro(mondo), giro(mondo)
    assert primo["queued"] == 1 and primo["queued_idempotent"] == 0
    assert secondo["queued"] == 0 and secondo["queued_idempotent"] == 1
    assert len(mondo.ledger) == 1
    assert mondo.accodati[0]["idempotency_key"] == mondo.accodati[1]["idempotency_key"]


def test_P3_buyer_visit_indirizzo_solo_dai_campi_strutturati(mondo):
    mondo.aggiungi(appuntamento(tipo="buyer_visit", immobile=30), contatto(),
                   {"address": "Via Roma", "civic_number": "12", "city": "Giulianova"})
    giro(mondo)
    corpo = mondo.accodati[0]["rendered_body"]
    assert "Via Roma 12, Giulianova" in corpo and "Indirizzo" in corpo


def test_P3b_buyer_visit_senza_indirizzo_resta_valido(mondo):
    mondo.aggiungi(appuntamento(tipo="buyer_visit", immobile=30), contatto(), None)
    c = giro(mondo)
    assert c["queued"] == 1 and "Indirizzo" not in mondo.accodati[0]["rendered_body"]


def test_P4_location_text_con_pii_mai_nel_corpo(mondo):
    app = appuntamento(tipo="buyer_visit", immobile=30)
    # anche se una riga lo portasse, il planner non lo legge ne' lo passa
    app["location_text"] = "Citofono Rossi, cell 333 1234567, codice portone 9911"
    mondo.aggiungi(app, contatto(), {"address": "Via Roma", "city": "Giulianova"})
    giro(mondo)
    m = mondo.accodati[0]
    testo = m["rendered_body"] + m["subject_snapshot"] + repr(m["metadata"])
    for pezzo in ("Citofono", "333 1234567", "9911"):
        assert pezzo not in testo
    assert "location_text" not in repository.APPOINTMENT_COLUMNS


def test_P5_altri_tipi_nessun_indirizzo(mondo):
    for i, tipo in enumerate(sorted(policy.ALLOWED_TYPES - {"buyer_visit"})):
        mondo.aggiungi(appuntamento(200 + i, tipo=tipo, immobile=30,
                                    inizio=roma(2031, 6, 13, 9, 50 + i)),
                       contatto(), {"address": "Via Roma", "civic_number": "12",
                                    "city": "Giulianova"})
    c = giro(mondo)
    assert c["queued"] == 4
    for m in mondo.accodati:
        assert "Via Roma" not in m["rendered_body"] and "Indirizzo" not in m["rendered_body"]


# ---------------------------------------------------------------------------
# P6 - P13: nessun invio, e il motivo contato per nome
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("campo,valore,motivo", [
    ("source", "legacy_stime_dettagliate", "source_not_allowed"),   # P6
    ("source", "system", "source_not_allowed"),
    ("appointment_type", "call", "type_not_allowed"),               # P7
    ("appointment_type", "notary", "type_not_allowed"),
    ("status", "cancelled", "status_not_allowed"),                  # P8
    ("status", "requested", "status_not_allowed"),
    ("status", "rescheduled", "status_not_allowed"),
])
def test_P6_P7_P8_fonte_tipo_stato_fuori_allowlist_zero(mondo, campo, valore, motivo):
    app = appuntamento()
    app[campo] = valore
    mondo.aggiungi(app, contatto())
    c = giro(mondo)
    assert mondo.accodati == [] and c["queued"] == 0
    assert c["ineligible"] == 1 and c["skipped_by_reason"][motivo] == 1


@pytest.mark.parametrize("cont,motivo", [
    (None, "contact_missing"),                                       # P9
    (contatto(stato="archived"), "contact_archived"),               # P10
    (contatto(email=None), "email_missing_or_invalid"),             # P11
    (contatto(email=""), "email_missing_or_invalid"),
    (contatto(email="non-una-email"), "email_missing_or_invalid"),
    (contatto(email=" mario@example.it"), "email_missing_or_invalid"),
])
def test_P9_P10_P11_contatto_mancante_archiviato_email(mondo, cont, motivo):
    mondo.aggiungi(appuntamento(), cont)
    c = giro(mondo)
    assert mondo.accodati == [] and c["skipped_by_reason"][motivo] == 1


def test_P12_creato_meno_di_12h_prima_zero(mondo):
    inizio = roma(2031, 6, 12, 20, 0)
    mondo.aggiungi(appuntamento(inizio=inizio, creato=inizio - timedelta(hours=11, minutes=59)),
                   contatto())
    c = giro(mondo, now=roma(2031, 6, 12, 10, 0))
    assert mondo.accodati == [] and c["skipped_by_reason"]["booked_less_than_12h_before"] == 1


def test_P13_meno_di_3h_zero(mondo):
    inizio = roma(2031, 6, 12, 12, 59)
    mondo.aggiungi(appuntamento(inizio=inizio), contatto())
    c = giro(mondo, now=roma(2031, 6, 12, 10, 0))
    assert mondo.accodati == [] and c["skipped_by_reason"]["less_than_3h_left"] == 1


def test_P6_P13_tutti_i_motivi_della_policy_sono_nei_conteggi(mondo):
    c = giro(mondo)
    assert set(c["skipped_by_reason"]) == {
        "type_not_allowed", "source_not_allowed", "status_not_allowed", "contact_missing",
        "contact_archived", "email_missing_or_invalid", "booked_less_than_12h_before",
        "less_than_3h_left"}
    assert set(c) == {"scanned", "due", "queued", "queued_idempotent", "not_due",
                      "ineligible", "errors", "skipped_by_reason"}


# ---------------------------------------------------------------------------
# P14 - P18: il tempo
# ---------------------------------------------------------------------------

def test_P14_target_futuro_not_due(mondo):
    mondo.aggiungi(appuntamento(inizio=roma(2031, 6, 13, 10, 1)), contatto())
    c = giro(mondo, now=roma(2031, 6, 12, 10, 0))
    assert c["not_due"] == 1 and mondo.accodati == []
    c = giro(mondo, now=roma(2031, 6, 12, 10, 1))
    assert c["queued"] == 1


def test_P15_target_passato_ma_ancora_valido_queued(mondo):
    # target 13/6 10:00, il giro arriva alle 15:00: mancano 19h -> si manda ora
    mondo.aggiungi(appuntamento(inizio=roma(2031, 6, 14, 10, 0)), contatto())
    c = giro(mondo, now=roma(2031, 6, 13, 15, 0))
    assert c["queued"] == 1


def test_P16_fuori_finestra_nessun_invio_fino_al_primo_istante_ammesso(mondo):
    # inizio 13/6 22:30 -> target 12/6 22:30 -> nella finestra: 19:00 del 12/6.
    # Il giro delle 21:00 del 12/6 e' fuori finestra: il primo istante ammesso
    # e' 08:00 del 13/6 (14h30 prima dell'inizio) -> not_due fino ad allora.
    mondo.aggiungi(appuntamento(inizio=roma(2031, 6, 13, 22, 30)), contatto())
    assert giro(mondo, now=roma(2031, 6, 12, 21, 0))["not_due"] == 1
    assert giro(mondo, now=roma(2031, 6, 13, 7, 59))["not_due"] == 1
    assert mondo.accodati == []
    assert giro(mondo, now=roma(2031, 6, 13, 8, 0))["queued"] == 1


def test_P16b_target_notturno_all_alba_va_alle_8(mondo):
    mondo.aggiungi(appuntamento(inizio=roma(2031, 6, 13, 6, 30)), contatto())
    assert giro(mondo, now=roma(2031, 6, 12, 7, 59))["not_due"] == 1
    assert giro(mondo, now=roma(2031, 6, 12, 8, 0))["queued"] == 1


def test_P17_dst_primavera(mondo):
    # 30/3/2031 (ultima domenica di marzo): 02:00 -> 03:00. Inizio domenica
    # 10:00 CEST (08:00Z); il target e' sabato 10:00 CET (09:00Z): 23h prima.
    inizio = roma(2031, 3, 30, 10, 0)
    assert inizio.utcoffset() == timedelta(hours=2)
    mondo.aggiungi(appuntamento(inizio=inizio, creato=inizio - timedelta(days=7)), contatto())
    assert giro(mondo, now=datetime(2031, 3, 29, 8, 59, tzinfo=UTC))["not_due"] == 1
    assert giro(mondo, now=datetime(2031, 3, 29, 9, 0, tzinfo=UTC))["queued"] == 1
    assert inizio - datetime(2031, 3, 29, 9, 0, tzinfo=UTC) == timedelta(hours=23)


def test_P18_dst_autunno(mondo):
    # 26/10/2031 (ultima domenica di ottobre): 03:00 -> 02:00. Inizio domenica
    # 10:00 CET (09:00Z); il target e' sabato 10:00 CEST (08:00Z): 25h prima.
    inizio = roma(2031, 10, 26, 10, 0)
    assert inizio.utcoffset() == timedelta(hours=1)
    mondo.aggiungi(appuntamento(inizio=inizio, creato=inizio - timedelta(days=7)), contatto())
    assert giro(mondo, now=datetime(2031, 10, 25, 7, 59, tzinfo=UTC))["not_due"] == 1
    assert giro(mondo, now=datetime(2031, 10, 25, 8, 0, tzinfo=UTC))["queued"] == 1


# ---------------------------------------------------------------------------
# P19 - P22
# ---------------------------------------------------------------------------

def test_P19_consenso_marketing_non_interrogato_il_servizio_parte(mondo, monkeypatch):
    import consent.guard

    def vietato(*_a, **_k):
        raise AssertionError("un promemoria di SERVIZIO non interroga il consenso marketing")

    monkeypatch.setattr(consent.guard, "can_send_marketing", vietato)
    cont = contatto()
    cont["marketing_consent"] = False           # anche se la riga lo portasse
    mondo.aggiungi(appuntamento(), cont)
    assert giro(mondo)["queued"] == 1
    assert mondo.accodati[0]["communication_type"] == "service"


def test_P20_il_giro_dell_agenzia_A_non_legge_ne_scrive_B(mondo):
    mondo.aggiungi(appuntamento(101, agency_id=7), contatto())
    mondo.aggiungi(appuntamento(102, agency_id=8), contatto(502))
    c = giro(mondo, ctx=operatore(7))
    assert mondo.letture == [7] and c["scanned"] == 1
    assert [m["ctx"].agency_id for m in mondo.accodati] == [7]
    assert all(k[0] == 7 for k in mondo.ledger)


def test_P20b_platform_admin_non_vincolato_rifiutato():
    from operator_auth.exceptions import PlatformAdminAgencyRequired
    ctx = OperatorContext(user_id=1, agency_id=None, role=None, is_platform_admin=True,
                          session_id=None, auth_channel="session")
    with pytest.raises(PlatformAdminAgencyRequired):
        planner.tick(ctx, now=ADESSO)


def test_P21_email_cambiata_stessa_occorrenza_nessun_secondo_messaggio(mondo):
    cont = contatto()
    mondo.aggiungi(appuntamento(), cont)
    giro(mondo)
    cont["email"] = "nuova@example.it"
    c = giro(mondo)
    assert c["queued"] == 0 and c["queued_idempotent"] == 1
    assert len(mondo.ledger) == 1
    [riga] = mondo.ledger.values()
    assert riga["destination_snapshot"] == "mario@example.it"
    assert "mario" not in riga["idempotency_key"] and "nuova" not in riga["idempotency_key"]


def test_P22_successore_di_un_reschedule_nuova_chiave_sotto_la_policy(mondo):
    vecchio_inizio = roma(2031, 6, 13, 10, 0)
    mondo.aggiungi(appuntamento(101, inizio=vecchio_inizio), contatto())
    giro(mondo)                                              # il vecchio parte
    mondo.candidati[7][0]["appointment"]["status"] = "rescheduled"
    # successore creato ADESSO per le 20:00 di stasera: < 12h -> niente
    succ = appuntamento(102, inizio=roma(2031, 6, 12, 21, 0), creato=ADESSO)
    mondo.aggiungi(succ, contatto())
    c = giro(mondo)
    assert c["skipped_by_reason"]["booked_less_than_12h_before"] == 1
    assert c["skipped_by_reason"]["status_not_allowed"] == 1
    assert len(mondo.ledger) == 1
    # successore per dopodomani: nuova occorrenza, nuova chiave, quando dovuta
    succ["start_at"] = roma(2031, 6, 14, 11, 0)
    assert giro(mondo)["not_due"] == 1
    c = giro(mondo, now=roma(2031, 6, 13, 11, 0))
    assert c["queued"] == 1 and len(mondo.ledger) == 2
    chiavi = {k for _a, k in mondo.ledger}
    assert len(chiavi) == 2 and any(":102:24h:" in k for k in chiavi)


# ---------------------------------------------------------------------------
# errori, 079, orologio
# ---------------------------------------------------------------------------

def test_un_candidato_rotto_conta_errors_e_il_giro_continua(mondo):
    mondo.aggiungi(appuntamento(101, inizio=roma(2031, 6, 13, 9, 0)), contatto())
    rotto = appuntamento(102, inizio=roma(2031, 6, 13, 9, 30))
    rotto["created_at"] = datetime(2031, 6, 1, 9, 30)            # senza fuso: solleva
    mondo.aggiungi(rotto, contatto())
    mondo.aggiungi(appuntamento(103, inizio=roma(2031, 6, 13, 9, 45)), contatto())
    c = giro(mondo)
    assert (c["scanned"], c["errors"], c["queued"]) == (3, 1, 2)


def test_enqueue_che_solleva_conta_errors_senza_retry(mondo, monkeypatch):
    chiamate = []

    def esplode(ctx, **kw):
        chiamate.append(kw["idempotency_key"])
        raise RuntimeError("guasto")

    monkeypatch.setattr(planner.communication_service, "enqueue", esplode)
    mondo.aggiungi(appuntamento(), contatto())
    c = giro(mondo)
    assert c["errors"] == 1 and c["due"] == 1 and len(chiamate) == 1


def test_079_assente_feature_not_migrated_e_nessuna_enqueue(mondo):
    mondo.migrata = False
    mondo.aggiungi(appuntamento(), contatto())
    with pytest.raises(planner.FeatureNotMigrated):
        giro(mondo)
    assert mondo.accodati == [] and mondo.letture == []


def test_un_solo_now_per_giro_e_con_fuso(mondo, monkeypatch):
    letti = []
    monkeypatch.setattr(planner, "_adesso", lambda: letti.append(1) or ADESSO)
    for i in range(3):
        mondo.aggiungi(appuntamento(101 + i, inizio=roma(2031, 6, 13, 9, i)), contatto())
    planner.tick(operatore())
    assert letti == [1]
    with pytest.raises(ValueError):
        planner.tick(operatore(), now=datetime(2031, 6, 12, 10, 0))


def test_i_conteggi_non_portano_pii(mondo):
    mondo.aggiungi(appuntamento(), contatto())
    c = giro(mondo)
    assert "mario" not in repr(c).lower() and "@" not in repr(c)


# ---------------------------------------------------------------------------
# L'ORIZZONTE DEI CANDIDATI (36h) BASTA: prova sui bordi
# ---------------------------------------------------------------------------

def _inizi_di_prova():
    """Ogni 15 minuti per due settimane attorno a ciascun cambio d'ora, piu'
    i bordi della finestra, su due anni."""
    inizi = []
    for anno in (2031, 2032):
        for mese, giorno in ((3, 20), (10, 18), (6, 10)):
            base = datetime(anno, mese, giorno, 0, 0, tzinfo=ROMA)
            for q in range(14 * 24 * 4):
                inizi.append((base + timedelta(minutes=15 * q)).astimezone(ROMA))
    return inizi


def test_orizzonte_il_promemoria_piu_anticipato_e_entro_36h():
    peggiore = max(s - policy.effective_reminder_at(s) for s in _inizi_di_prova())
    # il massimo reale: target nominale 23:45 del giorno prima portato alle
    # 19:00, piu' l'ora del cambio d'ora -> < 30h
    assert peggiore < timedelta(hours=30)
    assert repository.CANDIDATE_HORIZON == timedelta(hours=36)
    assert peggiore < repository.CANDIDATE_HORIZON


def test_orizzonte_oltre_36h_nessun_appuntamento_e_mai_dovuto():
    for inizio in _inizi_di_prova()[::7]:
        now = inizio - repository.CANDIDATE_HORIZON - timedelta(minutes=1)
        d = policy.send_decision(now=now, start_at=inizio, created_at=now - timedelta(days=9))
        assert not d.due, inizio


def test_orizzonte_dst_e_finestra_casi_estremi():
    for inizio in (roma(2031, 3, 30, 23, 45),      # CEST, target sabato 23:45 CET -> 19:00
                   roma(2031, 10, 26, 23, 45),     # CET, target sabato 23:45 CEST -> 19:00
                   roma(2031, 3, 31, 7, 59),       # target nella notte -> 08:00
                   roma(2031, 6, 13, 20, 0)):      # target 20:00 -> 19:00
        assert inizio - policy.effective_reminder_at(inizio) < repository.CANDIDATE_HORIZON
