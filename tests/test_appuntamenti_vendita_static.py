"""APPUNTAMENTI DI VENDITA - prove statiche (senza database).

  01  la 106 e' valida per il runner e ha la sua down (SEMPLIFICAZIONE-CRM-1:
      l'ultima della serie e' ora la 107, verificata nel suo test)
  02  la 106 aggiunge al CHECK della 072 SOLO `sale_appointment`, e il vincolo
      incarico/cliente/agente; la down si ferma se ci sono appuntamenti di vendita
  03  il catalogo del codice e gli specchi dell'interfaccia coincidono
  04  la vendita non si prenota da un link pubblico (schema e interfaccia)
  05  il service verifica l'incarico con la definizione canonica (tutte le
      origini) e gli stati ammessi (in corso, scaduto); il dettaglio offre
      «Apri incarico» solo per gli incarichi con una scheda in #/incarichi
  06  i promemoria A32 non cambiano: la loro lista chiusa resta quella del gate
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SU = (ROOT / "migrations" / "106_sale_appointments.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / "106_sale_appointments_down.sql").read_text(encoding="utf-8")
T072 = (ROOT / "migrations" / "072_a30_1_appointments.sql").read_text(encoding="utf-8")
ASSETS = ROOT / "static" / "os_shell" / "assets"


def _eseguibile(sql):
    return "\n".join(r for r in sql.splitlines() if not r.strip().startswith("--"))


def _valori(testo, nome):
    m = re.search(rf"CONSTRAINT {nome} CHECK \(\s*\w+ IN \((.*?)\)\)", testo, re.S)
    assert m, nome
    return tuple(re.findall(r"'([a-z0-9_]+)'", m.group(1)))


def test_01_la_106_e_valida_e_l_ultima():
    from scripts import p26_migrate as runner
    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    ultima = next(m for m in tutte if m.version == "106_sale_appointments")
    assert ultima.down_path is not None and not ultima.non_transactional
    assert runner.validate_migration(ultima) == []
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", _eseguibile(SU), re.M)
    assert re.search(r"^\s*BEGIN\s*;", GIU, re.M) and re.search(r"^\s*COMMIT\s*;", GIU, re.M)


def test_02_solo_una_tipologia_in_piu_e_il_vincolo():
    e = _eseguibile(SU)
    assert _valori(e, "appointments_type_chk") == _valori(T072, "appointments_type_chk") + (
        "sale_appointment",)
    vincolo = e[e.index("appointments_sale_links_chk"):]
    for colonna in ("property_id IS NOT NULL", "contact_id IS NOT NULL",
                    "assigned_user_id IS NOT NULL"):
        assert colonna in vincolo, colonna
    for vietato in ("UPDATE ", "INSERT INTO", "DELETE FROM", "TRUNCATE", "DROP COLUMN",
                    "ADD COLUMN", "CREATE TABLE", "public_booking_links"):
        assert vietato not in e, vietato
    giu = _eseguibile(GIU)
    assert "IF EXISTS (SELECT 1 FROM appointments WHERE appointment_type = 'sale_appointment')" in giu
    assert _valori(giu, "appointments_type_chk") == _valori(T072, "appointments_type_chk")


def test_03_catalogo_ed_etichette():
    from appointments import enums
    assert enums.APPOINTMENT_TYPES[-1] == enums.SALE_APPOINTMENT_TYPE == "sale_appointment"
    assert enums.APPOINTMENT_TYPE_LABELS_IT["sale_appointment"] == "Appuntamento di vendita"
    assert enums.APPOINTMENT_TYPE_LABELS_IT["buyer_visit"] == "Visita immobile"
    assert enums.SALE_MANDATE_STATES == ("active", "expired")
    modello = (ASSETS / "agenda" / "agenda-model.js").read_text(encoding="utf-8")
    assert "sale_appointment: 'Appuntamento di vendita'," in modello
    assert "export const SALE_APPOINTMENT_TYPE = 'sale_appointment';" in modello


def test_04_non_si_prenota_da_un_link_pubblico():
    from pydantic import ValidationError
    from public_booking.enums import NOT_BOOKABLE_TYPES
    from public_booking.schemas import BookingLinkCreateBody, BookingLinkPatchBody
    assert NOT_BOOKABLE_TYPES == ("buyer_visit", "sale_appointment")
    with pytest.raises(ValidationError):
        BookingLinkCreateBody(assigned_user_id=1, appointment_type="sale_appointment",
                              duration_minutes=60)
    with pytest.raises(ValidationError):
        BookingLinkPatchBody(appointment_type="sale_appointment")
    dialoghi = (ASSETS / "components" / "agenda" / "agenda-dialogs.js").read_text(encoding="utf-8")
    assert "if (NOT_BOOKABLE_TYPES.includes(t)) continue;" in dialoghi


def test_05_service_e_dettaglio():
    service = (ROOT / "appointments" / "service.py").read_text(encoding="utf-8")
    controllo = service[service.index("def _controlla_vendita"):]
    controllo = controllo[:controllo.index("\n# ----")]
    assert "mandate_origin(immobile) is None" in controllo
    assert 'immobile["signed_mandate"] = signed_link_mandate(cur, property_id)' in controllo
    assert "mandate_state(immobile) not in SALE_MANDATE_STATES" in controllo
    assert 'immobile["deleted_at"] is not None' in controllo
    # chiamato alla creazione e alla modifica, prima di scrivere
    assert len(re.findall(r"^\s+_controlla_vendita\(cur, agency_id,", service, re.M)) == 2
    assert 'link.append("mandate")' in service
    assert "mandate_origin(immobile) == ORIGIN_ACQUISITION)" in service
    assert '@router.get("/lookups/mandates")' in (ROOT / "appointments" / "router.py").read_text(encoding="utf-8")
    pannello = (ASSETS / "components" / "agenda" / "agenda-drawer.js").read_text(encoding="utf-8")
    assert "mandate: { etichetta: 'Apri incarico', fonte: 'property', rotta: (d) => `#/incarichi/" in pannello


def test_06_i_promemoria_non_cambiano():
    from appointment_reminders import policy
    assert "sale_appointment" not in policy.ALLOWED_TYPES
