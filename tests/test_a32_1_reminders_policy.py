"""A32-1 - la policy dei promemoria (pura): idoneita', tempo, DST, identita'.

Nessun database, nessun orologio implicito: `now` e' sempre un argomento,
quindi le date fisse qui sotto non dipendono dal giorno in cui gira la suite.
Le lettere seguono la matrice del gate A32-1 (A..U).
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from appointment_reminders import policy

ROMA = ZoneInfo("Europe/Rome")


def R(y, m, d, h, mi=0, fold=0):
    return datetime(y, m, d, h, mi, tzinfo=ROMA, fold=fold)


def locale(istante):
    return istante.astimezone(ROMA)


def app(**kw):
    base = {"appointment_type": "buyer_visit", "source": "crm_manual", "status": "scheduled",
            "start_at": R(2026, 10, 14, 10, 30), "created_at": R(2026, 10, 1, 9)}
    base.update(kw)
    return base


CONTATTO = {"status": "active", "email": "mario.rossi@example.it"}


# ---------------------------------------------------------------------------
# A-H - idoneita' statica
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tipo", ["buyer_visit", "inspection", "seller_meeting",
                                  "valuation_presentation", "mandate_signing"])
def test_A_i_cinque_tipi_ammessi(tipo):
    assert policy.ineligibility_reason(app(appointment_type=tipo), CONTATTO) is None


@pytest.mark.parametrize("tipo", ["call", "video_call", "proposal", "preliminary_contract",
                                  "notary", "technical", "other", None, ""])
def test_B_tipi_non_ammessi(tipo):
    assert policy.ineligibility_reason(app(appointment_type=tipo), CONTATTO) == policy.REASON_TYPE


def test_A_B_la_allowlist_e_esattamente_quella_del_gate():
    assert policy.ALLOWED_TYPES == {"buyer_visit", "inspection", "seller_meeting",
                                    "valuation_presentation", "mandate_signing"}


@pytest.mark.parametrize("fonte", ["crm_manual", "booking_link", "lmc15_facade"])
def test_C_le_tre_fonti_ammesse(fonte):
    assert policy.ineligibility_reason(app(source=fonte), CONTATTO) is None


@pytest.mark.parametrize("fonte", ["legacy_stime_dettagliate", "stima_inspections_backfill",
                                   "system", "a30_test", "sconosciuta", None])
def test_D_fonti_legacy_system_test_escluse(fonte):
    assert policy.ineligibility_reason(app(source=fonte), CONTATTO) == policy.REASON_SOURCE


def test_D_le_esclusioni_dichiarate_non_sono_nella_allowlist():
    assert policy.ALLOWED_SOURCES == {"crm_manual", "booking_link", "lmc15_facade"}
    assert not (policy.EXCLUDED_SOURCES & policy.ALLOWED_SOURCES)


@pytest.mark.parametrize("stato", ["scheduled", "confirmed"])
def test_E_scheduled_e_confirmed_ammessi(stato):
    assert policy.ineligibility_reason(app(status=stato), CONTATTO) is None


@pytest.mark.parametrize("stato", ["requested", "cancelled", "completed", "no_show",
                                   "rescheduled", None])
def test_F_gli_altri_stati_esclusi(stato):
    assert policy.ineligibility_reason(app(status=stato), CONTATTO) == policy.REASON_STATUS


def test_G_contatto_archiviato_escluso_inattivo_no():
    assert policy.ineligibility_reason(app(), {**CONTATTO, "status": "archived"}) == \
        policy.REASON_CONTACT_ARCHIVED
    assert policy.ineligibility_reason(app(), {**CONTATTO, "status": "inactive"}) is None


def test_G_contatto_mancante_escluso():
    assert policy.ineligibility_reason(app(), None) == policy.REASON_NO_CONTACT


@pytest.mark.parametrize("email", [None, "", "   ", "senza-chiocciola", "a@b", "@example.it",
                                   "a@@example.it", " mario@example.it", "mario@example.it ",
                                   "mario rossi@example.it", "<x>@example.it",
                                   "a" * 65 + "@example.it", 42])
def test_H_email_assente_o_non_valida_esclusa(email):
    assert policy.ineligibility_reason(app(), {**CONTATTO, "email": email}) == policy.REASON_NO_EMAIL


@pytest.mark.parametrize("email", ["mario@example.it", "m.rossi+casa@studio-rossi.co.uk",
                                   "UPPER@Example.IT"])
def test_H_email_valida(email):
    assert policy.is_valid_email(email)


# ---------------------------------------------------------------------------
# I-L - target e finestra
# ---------------------------------------------------------------------------

def test_I_target_normale_stessa_ora_del_giorno_prima():
    inizio = R(2026, 10, 14, 10, 30)                  # mercoledi', CEST
    assert locale(policy.nominal_reminder_at(inizio)) == R(2026, 10, 13, 10, 30)
    assert policy.effective_reminder_at(inizio) == policy.nominal_reminder_at(inizio)
    d = policy.send_decision(now=R(2026, 10, 13, 10, 29), start_at=inizio,
                             created_at=R(2026, 10, 1, 9))
    assert (d.reason, d.due, locale(d.send_at)) == (None, False, R(2026, 10, 13, 10, 30))
    d = policy.send_decision(now=R(2026, 10, 13, 10, 30), start_at=inizio,
                             created_at=R(2026, 10, 1, 9))
    assert d.due and d.reason is None


@pytest.mark.parametrize("ora,attesa", [((7, 30), (8, 0)), ((0, 15), (8, 0)), ((7, 59), (8, 0)),
                                        ((8, 0), (8, 0))])
def test_J_prima_delle_08_va_alle_08_dello_stesso_giorno(ora, attesa):
    inizio = R(2026, 10, 14, *ora)
    assert locale(policy.effective_reminder_at(inizio)) == R(2026, 10, 13, *attesa)


@pytest.mark.parametrize("ora,attesa", [((20, 0), (19, 0)), ((21, 0), (19, 0)),
                                        ((23, 59), (19, 0)), ((19, 59), (19, 59))])
def test_K_dalle_20_va_alle_19_dello_stesso_giorno(ora, attesa):
    inizio = R(2026, 10, 14, *ora)
    assert locale(policy.effective_reminder_at(inizio)) == R(2026, 10, 13, *attesa)


def test_K_la_finestra_e_08_incluse_20_escluse():
    assert policy.in_window(R(2026, 10, 13, 8, 0))
    assert policy.in_window(R(2026, 10, 13, 19, 59))
    assert not policy.in_window(R(2026, 10, 13, 20, 0))
    assert not policy.in_window(R(2026, 10, 13, 7, 59))


def test_L_la_domenica_e_inclusa():
    inizio = R(2026, 10, 12, 10)                      # lunedi'
    promemoria = locale(policy.effective_reminder_at(inizio))
    assert promemoria == R(2026, 10, 11, 10) and promemoria.isoweekday() == 7
    assert policy.in_window(R(2026, 10, 11, 10))


# ---------------------------------------------------------------------------
# M-P - soglie e promemoria tardivo
# ---------------------------------------------------------------------------

def test_M_meno_di_3h_nessun_promemoria():
    inizio = R(2026, 10, 14, 16)
    creato = R(2026, 10, 10, 9)
    d = policy.send_decision(now=R(2026, 10, 14, 13, 1), start_at=inizio, created_at=creato)
    assert d.reason == policy.REASON_LEAD_TOO_SHORT and d.send_at is None and not d.due
    # esattamente 3h: ancora ammesso (la regola e' "< 3h")
    d = policy.send_decision(now=R(2026, 10, 14, 13), start_at=inizio, created_at=creato)
    assert d.reason is None and d.due


def test_N_prenotato_meno_di_12h_prima_nessun_promemoria():
    inizio = R(2026, 10, 14, 18)
    d = policy.send_decision(now=R(2026, 10, 14, 10), start_at=inizio,
                             created_at=R(2026, 10, 14, 6, 1))            # 11h59
    assert d.reason == policy.REASON_BOOKING_TOO_LATE and d.send_at is None
    d = policy.send_decision(now=R(2026, 10, 14, 10), start_at=inizio,
                             created_at=R(2026, 10, 14, 6))               # 12h esatte
    assert d.reason is None and d.due


def test_M_N_le_due_soglie_sono_distinte():
    """Creato con largo anticipo ma a 2h dall'inizio: solo la 3h; creato da poco
    ma con 5h davanti: solo la 12h."""
    inizio = R(2026, 10, 14, 15)
    solo_3h = policy.send_decision(now=R(2026, 10, 14, 13), start_at=inizio,
                                   created_at=R(2026, 10, 1, 9))
    solo_12h = policy.send_decision(now=R(2026, 10, 14, 10), start_at=inizio,
                                    created_at=R(2026, 10, 14, 9))
    assert solo_3h.reason == policy.REASON_LEAD_TOO_SHORT
    assert solo_12h.reason == policy.REASON_BOOKING_TOO_LATE


def test_O_prenotazione_tardiva_valida_parte_al_primo_giro_utile():
    """Domani 10:00, prenotato oggi 15:00 (19h): il target (oggi 10:00) e' gia'
    passato -> parte subito."""
    inizio = R(2026, 10, 15, 10)
    d = policy.send_decision(now=R(2026, 10, 14, 15, 10), start_at=inizio,
                             created_at=R(2026, 10, 14, 15))
    assert d.reason is None and d.due and locale(d.send_at) == R(2026, 10, 14, 15, 10)


def test_O_l_esempio_A_del_gate_non_e_tardivo():
    """Domani 18:00 prenotato oggi 05:00: il target e' oggi 18:00, ancora futuro."""
    inizio = R(2026, 10, 15, 18)
    d = policy.send_decision(now=R(2026, 10, 14, 5, 10), start_at=inizio,
                             created_at=R(2026, 10, 14, 5))
    assert d.reason is None and not d.due and locale(d.send_at) == R(2026, 10, 14, 18)


def test_P_tardiva_fuori_finestra_che_lascia_meno_di_3h_nessun_promemoria():
    """Domani 10:00 prenotato alle 21:30 (12h30): la finestra riapre alle 08:00,
    a 2h dall'inizio."""
    d = policy.send_decision(now=R(2026, 10, 14, 21, 35), start_at=R(2026, 10, 15, 10),
                             created_at=R(2026, 10, 14, 21, 30))
    assert d.reason == policy.REASON_LEAD_TOO_SHORT and d.send_at is None


def test_P_tardiva_fuori_finestra_parte_alla_riapertura():
    inizio = R(2026, 10, 15, 12)
    creato = R(2026, 10, 14, 20, 30)                  # 15h30 prima
    d = policy.send_decision(now=R(2026, 10, 14, 20, 40), start_at=inizio, created_at=creato)
    assert d.reason is None and not d.due and locale(d.send_at) == R(2026, 10, 15, 8)
    d = policy.send_decision(now=R(2026, 10, 15, 8), start_at=inizio, created_at=creato)
    assert d.due and locale(d.send_at) == R(2026, 10, 15, 8)
    assert policy.next_window_instant(R(2026, 10, 14, 6)) == R(2026, 10, 14, 8)


def test_is_reminder_eligible_combina_idoneita_e_tempo():
    d = policy.is_reminder_eligible(now=R(2026, 10, 13, 10, 30), appointment=app(),
                                    contact=CONTATTO)
    assert d.reason is None and d.due
    d = policy.is_reminder_eligible(now=R(2026, 10, 13, 10, 30),
                                    appointment=app(status="cancelled"), contact=CONTATTO)
    assert d.reason == policy.REASON_STATUS and not d.due


def test_un_orario_senza_fuso_e_rifiutato():
    with pytest.raises(ValueError):
        policy.nominal_reminder_at(datetime(2026, 10, 14, 10))
    with pytest.raises(ValueError):
        policy.send_decision(now=datetime(2026, 10, 13, 10), start_at=R(2026, 10, 14, 10),
                             created_at=R(2026, 10, 1, 9))


# ---------------------------------------------------------------------------
# DST - CET, CEST, salti di marzo e di ottobre (Q, R)
# ---------------------------------------------------------------------------

def test_giorno_normale_cet():
    inizio = R(2026, 1, 14, 10, 30)
    assert inizio.utcoffset() == timedelta(hours=1)
    assert locale(policy.nominal_reminder_at(inizio)) == R(2026, 1, 13, 10, 30)
    assert inizio - policy.nominal_reminder_at(inizio) == timedelta(hours=24)


def test_giorno_normale_cest():
    inizio = R(2026, 7, 15, 17)
    assert inizio.utcoffset() == timedelta(hours=2)
    assert locale(policy.nominal_reminder_at(inizio)) == R(2026, 7, 14, 17)


def test_Q_ora_legale_resta_la_stessa_ora_civile():
    """Dom 29/03/2026 10:00 CEST: il giorno prima (sab 28/03) e' CET. Stessa
    ora civile, ma solo 23 ore reali: `start - 24h` darebbe le 09:00."""
    inizio = R(2026, 3, 29, 10)
    promemoria = policy.nominal_reminder_at(inizio)
    assert locale(promemoria) == R(2026, 3, 28, 10)
    assert inizio - promemoria == timedelta(hours=23)
    # cio' che NON si fa: 24 ore REALI (aritmetica in UTC) -> le 09:00
    assert locale(inizio.astimezone(timezone.utc) - timedelta(hours=24)).hour == 9


def test_R_ora_solare_resta_la_stessa_ora_civile():
    """Dom 25/10/2026 10:00 CET: il giorno prima e' CEST. 25 ore reali."""
    inizio = R(2026, 10, 25, 10)
    promemoria = policy.nominal_reminder_at(inizio)
    assert locale(promemoria) == R(2026, 10, 24, 10)
    assert inizio - promemoria == timedelta(hours=25)
    # cio' che NON si fa: 24 ore REALI (aritmetica in UTC) -> le 11:00
    assert locale(inizio.astimezone(timezone.utc) - timedelta(hours=24)).hour == 11


def test_Q_R_le_ore_notturne_dei_salti_finiscono_alle_08():
    # 29/03 02:30 non esiste; 25/10 02:30 esiste due volte. Entrambe -> 08:00.
    assert locale(policy.effective_reminder_at(R(2026, 3, 30, 2, 30))) == R(2026, 3, 29, 8)
    assert locale(policy.effective_reminder_at(R(2026, 10, 26, 2, 30))) == R(2026, 10, 25, 8)
    nominale = locale(policy.nominal_reminder_at(R(2026, 10, 26, 2, 30)))
    assert nominale.date() == R(2026, 10, 25, 0).date() and nominale.utcoffset() == \
        timedelta(hours=2)                                      # fold=0: la prima


def test_i_risultati_sono_in_utc():
    assert policy.effective_reminder_at(R(2026, 10, 14, 10)).tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# S-U - identita' dell'occorrenza
# ---------------------------------------------------------------------------

def test_S_la_chiave_e_deterministica_e_documentata():
    inizio = R(2026, 10, 14, 10, 30)
    chiave = policy.occurrence_key(42, inizio)
    assert chiave == f"appointment_reminder:v1:42:24h:{int(inizio.timestamp())}"
    assert chiave == policy.occurrence_key(42, inizio.astimezone(timezone.utc))  # stesso istante
    assert chiave == policy.occurrence_key(42, inizio, policy.OFFSET_24H)
    assert len(chiave) <= 80


def test_T_la_chiave_cambia_con_start_at_e_con_l_appuntamento():
    inizio = R(2026, 10, 14, 10, 30)
    assert policy.occurrence_key(42, inizio) != policy.occurrence_key(42, inizio + timedelta(minutes=30))
    assert policy.occurrence_key(42, inizio) != policy.occurrence_key(43, inizio)


def test_U_la_chiave_non_dipende_dall_email():
    firma = inspect.signature(policy.occurrence_key)
    assert list(firma.parameters) == ["appointment_id", "start_at", "offset"]
    corpo = inspect.getsource(policy.occurrence_key).split('"""', 2)[-1]
    assert "email" not in corpo and "contact" not in corpo


@pytest.mark.parametrize("args", [(0, R(2026, 10, 14, 10)), (-1, R(2026, 10, 14, 10)),
                                  (True, R(2026, 10, 14, 10)), ("42", R(2026, 10, 14, 10))])
def test_la_chiave_rifiuta_id_non_validi(args):
    with pytest.raises(ValueError):
        policy.occurrence_key(*args)


def test_la_chiave_rifiuta_offset_sconosciuti_e_orari_senza_fuso():
    with pytest.raises(ValueError):
        policy.occurrence_key(42, R(2026, 10, 14, 10), "2h")
    with pytest.raises(ValueError):
        policy.occurrence_key(42, datetime(2026, 10, 14, 10))
