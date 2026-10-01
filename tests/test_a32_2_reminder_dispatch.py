"""A32-2 - la revalida finale nel dispatcher, SENZA database (R1-R15).

Il dispatcher e' quello VERO (`communication.dispatcher.dispatch_batch`); sono
finti il ledger (claim/finalize), il database che la revalida rilegge e il
provider, che conta le chiamate. Le stesse prove sul PostgreSQL vero stanno in
`test_a32_2_reminder_postgres.py`.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from appointment_reminders import planner, policy, repository, revalidation
from communication import dispatcher
from communication.providers.base import (OUTCOME_ACCEPTED, ProviderCapabilities,
                                          ProviderResult)
from operator_auth.context import OperatorContext

ROMA = ZoneInfo("Europe/Rome")
INIZIO = datetime(2031, 6, 13, 10, 0, tzinfo=ROMA)
#: Il momento del dispatch: il target (12/6 10:00) e' appena passato.
DISPATCH = datetime(2031, 6, 12, 10, 5, tzinfo=ROMA)
CHIAVE = policy.occurrence_key(101, INIZIO)


def operatore(agency_id=7):
    return OperatorContext(user_id=42, agency_id=agency_id, role="agency_owner",
                           is_platform_admin=False, session_id=None,
                           auth_channel="session")


def messaggio(i=1, **diversi):
    m = {
        "id": i, "agency_id": 7, "contact_id": 501, "channel": "email",
        "communication_type": "service", "mode": "automatic",
        "reason_code": "appointment_reminder", "template_key": "appointment_reminder_24h",
        "template_version": 1, "subject_snapshot": "Promemoria", "rendered_body": "<p>x</p>",
        "destination_snapshot": "mario@example.it", "idempotency_key": CHIAVE,
        "metadata": planner.metadata_for(101, INIZIO), "claim_token": f"tok-{i}",
    }
    m.update(diversi)
    return m


def riga_db(**diversi):
    app = {"id": 101, "agency_id": 7, "appointment_type": "seller_meeting",
           "status": "scheduled", "source": "crm_manual", "start_at": INIZIO,
           "created_at": INIZIO - timedelta(days=5), "contact_id": 501, "property_id": None}
    cont = {"id": 501, "status": "active", "email": "mario@example.it", "first_name": "Mario"}
    for k, v in diversi.items():
        if k.startswith("c_"):
            if cont is not None:
                cont[k[2:]] = v
        elif k == "contatto":
            cont = v
        else:
            app[k] = v
    return {"appointment": app, "contact": cont}


class Mondo:
    def __init__(self):
        self.coda: list[dict] = []
        self.db: dict[tuple[int, int], dict] = {(7, 101): riga_db()}
        self.letture: list[tuple[int, int]] = []
        self.eventi: list[tuple] = []
        self.inviati: list[dict] = []
        self.consenso = SimpleNamespace(allowed=True, reason=None)

    # ledger finto
    def recover_stale(self, ctx, limit):
        return []

    def claim_due(self, ctx, *, provider, channel, limit):
        presi, self.coda = [m for m in self.coda if m["agency_id"] == ctx.agency_id], []
        return [{"message": m} for m in presi]

    def finalize_suppressed(self, ctx, mid, tok, *, reason, cur=None):
        self.eventi.append(("suppressed", mid, reason))
        return {"message": {"id": mid}}

    def finalize_sent(self, ctx, mid, tok, *, cur=None, provider_message_id=None):
        self.eventi.append(("sent", mid))
        return {"message": {"id": mid, "reason_code": "x"}}

    def finalize_failed(self, ctx, mid, tok, **kw):
        self.eventi.append(("failed", mid))
        return {"message": {"id": mid}}

    def finalize_indeterminate(self, ctx, mid, tok, **kw):
        self.eventi.append(("indeterminate", mid))
        return {"message": {"id": mid}}

    # database riletto dalla revalida
    def appointment_for_revalidation(self, cur, agency_id, appointment_id):
        self.letture.append((agency_id, appointment_id))
        self.eventi.append(("revalida", appointment_id))
        return self.db.get((agency_id, appointment_id))

    # provider finto
    NAME = "finto"
    CAPABILITIES = ProviderCapabilities(returns_message_id=False,
                                        distinguishes_failure_class=False,
                                        reports_delivery=False)

    def send(self, message):
        self.eventi.append(("provider", message["id"]))
        self.inviati.append(message)
        return ProviderResult(outcome=OUTCOME_ACCEPTED)


@pytest.fixture
def mondo(monkeypatch):
    m = Mondo()

    @contextmanager
    def cursore(**_kw):
        yield None, None

    for nome in ("recover_stale", "claim_due", "finalize_suppressed", "finalize_sent",
                 "finalize_failed", "finalize_indeterminate"):
        monkeypatch.setattr(dispatcher.service, nome, getattr(m, nome))
    monkeypatch.setattr(dispatcher, "communication_cursor", cursore)
    monkeypatch.setattr(dispatcher.integrations, "dopo_invio", lambda cur, msg: None)
    monkeypatch.setattr(dispatcher, "can_send_marketing",
                        lambda ctx, contact_id: m.consenso)
    monkeypatch.setattr(revalidation, "communication_cursor", cursore)
    monkeypatch.setattr(repository, "appointment_for_revalidation",
                        m.appointment_for_revalidation)
    monkeypatch.setattr(revalidation, "_adesso", lambda: DISPATCH)
    return m


def giro(mondo, *messaggi, agency_id=7):
    mondo.coda.extend(messaggi)
    return dispatcher.dispatch_batch(operatore(agency_id), channel="email", provider=mondo)


def soppresso(mondo, motivo):
    return ("suppressed", 1, motivo) in mondo.eventi


# ---------------------------------------------------------------------------

def test_R1_promemoria_valido_provider_esattamente_una_volta_sent(mondo):
    c = giro(mondo, messaggio())
    assert len(mondo.inviati) == 1 and c["sent"] == 1 and c["suppressed"] == 0
    # la revalida avviene PRIMA del provider (M9)
    assert mondo.eventi.index(("revalida", 101)) < mondo.eventi.index(("provider", 1))
    assert mondo.letture == [(7, 101)]


@pytest.mark.parametrize("stato,campi", [
    ("cancelled", {}),                     # R2
    ("completed", {}),                     # R3
    ("no_show", {}),                       # R4
    ("rescheduled", {}),                   # R5 (il predecessore)
    ("requested", {}),
])
def test_R2_R5_stato_non_piu_aperto_provider_zero(mondo, stato, campi):
    mondo.db[(7, 101)] = riga_db(status=stato, **campi)
    c = giro(mondo, messaggio())
    assert mondo.inviati == [] and c["suppressed"] == 1
    assert soppresso(mondo, "status_not_allowed")


def test_R6_contatto_archiviato_provider_zero(mondo):
    mondo.db[(7, 101)] = riga_db(c_status="archived")
    giro(mondo, messaggio())
    assert mondo.inviati == [] and soppresso(mondo, "contact_archived")


def test_R6b_contatto_sparito_provider_zero(mondo):
    mondo.db[(7, 101)] = riga_db(contatto=None)
    giro(mondo, messaggio())
    assert mondo.inviati == [] and soppresso(mondo, "contact_missing")


def test_R7_email_cambiata_provider_zero_destination_changed(mondo):
    mondo.db[(7, 101)] = riga_db(c_email="nuova@example.it")
    giro(mondo, messaggio())
    assert mondo.inviati == [] and soppresso(mondo, "destination_changed")


def test_R7b_email_diventata_invalida_provider_zero(mondo):
    mondo.db[(7, 101)] = riga_db(c_email="non valida")
    giro(mondo, messaggio())
    assert mondo.inviati == [] and soppresso(mondo, "email_missing_or_invalid")


def test_R8_contatto_dell_appuntamento_cambiato_provider_zero(mondo):
    mondo.db[(7, 101)] = riga_db(contact_id=777)
    giro(mondo, messaggio())
    assert mondo.inviati == [] and soppresso(mondo, "contact_changed")


def test_R9_inizio_spostato_occurrence_changed(mondo):
    mondo.db[(7, 101)] = riga_db(start_at=INIZIO + timedelta(minutes=30))
    giro(mondo, messaggio())
    assert mondo.inviati == [] and soppresso(mondo, "occurrence_changed")


def test_R9b_chiave_del_ledger_diversa_occurrence_changed(mondo):
    giro(mondo, messaggio(idempotency_key=CHIAVE + "x"))
    assert mondo.inviati == [] and soppresso(mondo, "occurrence_changed")


def test_R10_meno_di_3h_al_dispatch_provider_zero(mondo, monkeypatch):
    monkeypatch.setattr(revalidation, "_adesso", lambda: INIZIO - timedelta(hours=2, minutes=59))
    giro(mondo, messaggio())
    assert mondo.inviati == [] and soppresso(mondo, "less_than_3h_left")


def test_R11_fuori_finestra_di_roma_al_dispatch_provider_zero(mondo, monkeypatch):
    # il promemoria era dovuto alle 19:00; il dispatch arriva alle 20:00
    inizio = datetime(2031, 6, 13, 22, 30, tzinfo=ROMA)
    mondo.db[(7, 101)] = riga_db(start_at=inizio)
    monkeypatch.setattr(revalidation, "_adesso",
                        lambda: datetime(2031, 6, 12, 20, 0, tzinfo=ROMA))
    giro(mondo, messaggio(idempotency_key=policy.occurrence_key(101, inizio),
                          metadata=planner.metadata_for(101, inizio)))
    assert mondo.inviati == [] and soppresso(mondo, "reminder_not_due_now")


def test_R11b_non_ancora_dovuto_provider_zero(mondo, monkeypatch):
    monkeypatch.setattr(revalidation, "_adesso", lambda: datetime(2031, 6, 12, 9, 59, tzinfo=ROMA))
    giro(mondo, messaggio())
    assert mondo.inviati == [] and soppresso(mondo, "reminder_not_due_now")


def test_R11c_prenotato_meno_di_12h_prima_provider_zero(mondo):
    mondo.db[(7, 101)] = riga_db(created_at=INIZIO - timedelta(hours=11))
    giro(mondo, messaggio())
    assert mondo.inviati == [] and soppresso(mondo, "booked_less_than_12h_before")


@pytest.mark.parametrize("metadata", [
    None, {}, "x", [1],
    {"kind": "appointment_reminder"},
    {**planner.metadata_for(101, INIZIO), "kind": "journey"},
    {**planner.metadata_for(101, INIZIO), "offset": "2h"},
    {**planner.metadata_for(101, INIZIO), "appointment_id": "101"},
    {**planner.metadata_for(101, INIZIO), "appointment_id": True},
    {**planner.metadata_for(101, INIZIO), "appointment_id": 0},
    {**planner.metadata_for(101, INIZIO), "start_epoch": None},
    {**planner.metadata_for(101, INIZIO), "occurrence_key": 5},
    {**planner.metadata_for(101, INIZIO), "email": "mario@example.it"},
])
def test_R12_metadata_corrotti_o_mancanti_fail_closed(mondo, metadata):
    giro(mondo, messaggio(metadata=metadata))
    assert mondo.inviati == [] and soppresso(mondo, "reminder_metadata_invalid")
    assert mondo.letture == []          # nessuna lettura su metadata non validi


@pytest.mark.parametrize("campo,valore", [("template_key", "altro"), ("template_version", 2),
                                          ("channel", "whatsapp")])
def test_R12b_identita_del_template_o_canale_diversi_fail_closed(mondo, campo, valore):
    giro(mondo, messaggio(**{campo: valore}))
    assert mondo.inviati == [] and soppresso(mondo, "reminder_metadata_invalid")


def test_R13_id_di_un_appuntamento_di_altra_agenzia_provider_zero_nessuna_lettura_fuori(mondo):
    # l'appuntamento 900 esiste, ma nell'agenzia 8
    mondo.db[(8, 900)] = riga_db(id=900, agency_id=8)
    giro(mondo, messaggio(metadata=planner.metadata_for(900, INIZIO),
                          idempotency_key=policy.occurrence_key(900, INIZIO)))
    assert mondo.inviati == [] and soppresso(mondo, "appointment_missing")
    assert mondo.letture == [(7, 900)]          # letto SOLO nella propria agenzia
    assert ("suppressed", 1, "appointment_missing") in mondo.eventi


def test_R13b_messaggio_di_un_altra_agenzia_nel_contesto_fail_closed(mondo):
    motivo = revalidation.decide(messaggio(agency_id=8), riga_db(), agency_id=7, now=DISPATCH)
    assert motivo == "reminder_metadata_invalid"


def test_R14_servizio_non_promemoria_invariato(mondo):
    altro = messaggio(reason_code="stima_pdf", metadata={}, template_key=None,
                      template_version=None, idempotency_key="k")
    c = giro(mondo, altro)
    assert len(mondo.inviati) == 1 and c["sent"] == 1
    assert mondo.letture == [] and not any(e[0] == "revalida" for e in mondo.eventi)


def test_R15_marketing_gate_del_consenso_invariato(mondo):
    mondo.consenso = SimpleNamespace(allowed=False, reason="deny_revoked")
    m = messaggio(communication_type="marketing", reason_code="m1", metadata={},
                  idempotency_key="k")
    c = giro(mondo, m)
    assert mondo.inviati == [] and c["suppressed"] == 1
    assert ("suppressed", 1, "deny_revoked") in mondo.eventi and mondo.letture == []
    mondo.consenso = SimpleNamespace(allowed=True, reason=None)
    mondo.eventi.clear()
    giro(mondo, messaggio(2, communication_type="marketing", reason_code="m1", metadata={},
                          idempotency_key="k2"))
    assert len(mondo.inviati) == 1 and mondo.letture == []


def test_un_promemoria_non_interroga_il_consenso_marketing(mondo, monkeypatch):
    def vietato(*_a, **_k):
        raise AssertionError("servizio: il consenso marketing non si interroga")
    monkeypatch.setattr(dispatcher, "can_send_marketing", vietato)
    c = giro(mondo, messaggio())
    assert c["sent"] == 1


def test_batch_misto_un_soppresso_non_ferma_gli_altri(mondo):
    mondo.db[(7, 102)] = riga_db(id=102, status="cancelled")
    k102 = policy.occurrence_key(102, INIZIO)
    c = giro(mondo, messaggio(1), messaggio(2, metadata=planner.metadata_for(102, INIZIO),
                                            idempotency_key=k102),
             messaggio(3, reason_code="stima_pdf", metadata={}, idempotency_key="z",
                       template_key=None, template_version=None))
    assert c == {"claimed": 3, "sent": 2, "suppressed": 1, "failed": 0,
                 "indeterminate": 0, "lost": 0}
    assert [m["id"] for m in mondo.inviati] == [1, 3]


def test_i_motivi_sono_stabili_corti_e_senza_pii():
    motivi = revalidation.REVALIDATION_REASONS | set(
        getattr(policy, n) for n in dir(policy) if n.startswith("REASON_"))
    assert revalidation.REVALIDATION_REASONS == {
        "appointment_missing", "reminder_metadata_invalid", "occurrence_changed",
        "contact_changed", "destination_changed", "reminder_not_due_now"}
    for m in motivi:
        assert len(m) <= 60 and m.replace("_", "").isalnum() and m == m.lower()


def test_un_guasto_della_rilettura_non_arriva_al_provider(mondo, monkeypatch):
    def rotto(*_a, **_k):
        raise RuntimeError("db giu'")
    monkeypatch.setattr(repository, "appointment_for_revalidation", rotto)
    with pytest.raises(RuntimeError):
        giro(mondo, messaggio())
    assert mondo.inviati == []      # resta `sending`: recover_stale -> indeterminate
