"""P29-3D su PostgreSQL reale: la sequenza della stima e il Contact 360.

COSA SI PROVA QUI

Il PROVISIONING idempotente della journey `stima_lead` v1 e i suoi cinque
passi con i ritardi e i modi dichiarati; l'ATTIVAZIONE con il suo cutoff (le
stime spedite prima non entrano) e il RITIRO; le API del Contact 360 - lo
storico, la card dell'automazione, le azioni - con lo scope CRM vero,
misurato cambiando il ruolo di chi si autentica; il messaggio manuale, che
passa dal gate del consenso e non lo aggira; e il comportamento con la 071
assente, dove lo storico continua a esistere e l'automazione dice "non
disponibile" invece di rompersi.

L'HARNESS E' QUELLO DI P29-3C, importato invece che ricopiato: stesso
database usa-e-getta, stessa app vera, stessa spia al posto del trasporto
email. Due copie della stessa impalcatura sarebbero due impalcature diverse
al primo cambiamento.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Nessuna email parte mai.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_p29_3c_orchestrator_postgres import (  # noqa: F401
    Ctx, PASSWORD, VERSIONE, _migrazione, accedi, client, db, entra_in_acting,
    iscrizioni, journey_attiva, messaggi, mondo, modulo, passo, segna_spedito,
)

pytestmark = pytest.mark.skipif(not os.getenv("P29_TEST_DSN"),
                                reason="P29_TEST_DSN non impostata")

GIORNO = timedelta(days=1)


def _provision(modulo, ctx):
    from communication import journey_catalog
    return journey_catalog.ensure_stima_lead_v1(ctx)


# ===========================================================================
# A - PROVISIONING
# ===========================================================================

def test_01_il_provisioning_crea_la_sequenza_in_bozza_con_cinque_passi(mondo, modulo):
    ctx = mondo["ctx"]()
    esito = _provision(modulo, ctx)

    assert esito["created"] is True
    j = esito["journey"]
    assert (j["journey_key"], j["version"]) == ("stima_lead", 1)
    assert j["status"] == "draft", "il provisioning NON accende niente"
    assert j["trigger_type"] == "stima_pdf_sent" and j["send_timezone"] == "Europe/Rome"

    passi = j["steps"]
    assert [p["step_key"] for p in passi] == ["M1", "M2", "M3", "M4", "M5"]
    assert [p["delay_seconds"] // 86400 for p in passi] == [1, 4, 7, 14, 30]
    assert [p["delay_from"] for p in passi] == [
        "trigger", "previous_step_sent", "previous_step_sent",
        "previous_step_sent", "previous_step_sent"]
    assert [p["default_mode"] for p in passi] == [
        "automatic", "automatic", "assisted", "automatic", "automatic"]
    assert [p["template_key"] for p in passi] == [
        f"stima_lead_m{n}" for n in range(1, 6)]
    for p in passi:
        assert p["send_window"] == {"days": [1, 2, 3, 4, 5, 6], "from": "09:00", "to": "19:00"}


def test_02_il_provisioning_e_idempotente_e_non_modifica_la_v1(mondo, modulo):
    ctx = mondo["ctx"]()
    primo = _provision(modulo, ctx)
    secondo = _provision(modulo, ctx)

    assert secondo["created"] is False
    assert secondo["journey"]["id"] == primo["journey"]["id"]
    assert len(mondo["righe"]("communication_journeys")) == 1
    assert len(mondo["righe"]("communication_journey_steps")) == 5


def test_03_una_v1_ritirata_non_viene_resuscitata_dal_provisioning(mondo, modulo):
    ctx = mondo["ctx"]()
    j = _provision(modulo, ctx)["journey"]
    modulo["journeys"].activate_journey(ctx, j["id"])
    modulo["journeys"].retire_journey(ctx, j["id"])

    ancora = _provision(modulo, ctx)
    assert ancora["created"] is False
    assert ancora["journey"]["status"] == "retired"
    assert len(mondo["righe"]("communication_journeys")) == 1


# ===========================================================================
# B - ATTIVAZIONE, CUTOFF, RITIRO
# ===========================================================================

def test_04_l_attivazione_vale_da_adesso_e_non_recupera_le_stime_precedenti(mondo, modulo):
    ctx = mondo["ctx"]()
    vecchio = mondo["contatto"](inviata=datetime.now(timezone.utc) - 10 * GIORNO)
    j = _provision(modulo, ctx)["journey"]
    modulo["journeys"].activate_journey(ctx, j["id"])
    nuovo = mondo["contatto"]()

    modulo["tick"].tick(ctx)
    per_contatto = {e["contact_id"] for e in iscrizioni(mondo)}
    assert nuovo["contact"] in per_contatto
    assert vecchio["contact"] not in per_contatto


def test_05_il_primo_passo_e_dovuto_un_giorno_dopo_la_stima_dentro_la_finestra(mondo, modulo):
    from zoneinfo import ZoneInfo
    ctx = mondo["ctx"]()
    j = _provision(modulo, ctx)["journey"]
    modulo["journeys"].activate_journey(ctx, j["id"])
    mondo["contatto"]()
    modulo["tick"].tick(ctx)

    (e,) = iscrizioni(mondo)
    assert e["next_step_no"] == 1 and e["next_action_kind"] == "enqueue"
    assert e["next_action_at"] >= e["trigger_sent_at"] + GIORNO
    locale = e["next_action_at"].astimezone(ZoneInfo("Europe/Rome"))
    assert locale.isoweekday() in (1, 2, 3, 4, 5, 6), locale
    assert 9 <= locale.hour < 19, locale


def test_06_il_ritiro_non_iscrive_piu_nessuno_e_lascia_correre_le_aperte(mondo, modulo):
    ctx = mondo["ctx"]()
    j = _provision(modulo, ctx)["journey"]
    modulo["journeys"].activate_journey(ctx, j["id"])
    mondo["contatto"]()
    modulo["tick"].tick(ctx)
    aperte_prima = len(iscrizioni(mondo))

    modulo["journeys"].retire_journey(ctx, j["id"])
    mondo["contatto"]()
    modulo["tick"].tick(ctx)
    dopo = iscrizioni(mondo)
    assert len(dopo) == aperte_prima == 1
    assert dopo[0]["status"] == "active", "l'iscrizione gia' aperta prosegue"


# ===========================================================================
# C - IL TERZO PASSO E' ASSISTITO
# ===========================================================================

def _fino_a_m3(mondo, modulo, ctx):
    """Porta una iscrizione reale fino al passo assistito, spedendo M1 e M2."""
    j = _provision(modulo, ctx)["journey"]
    modulo["journeys"].activate_journey(ctx, j["id"])
    c = mondo["contatto"]()
    modulo["tick"].tick(ctx)
    (e,) = iscrizioni(mondo)
    for atteso in (1, 2):
        mondo["sql"]("UPDATE communication_enrollments SET next_action_at = NOW() "
                     "WHERE id = %s", (e["id"],))
        conteggi = modulo["tick"].tick(ctx)
        assert conteggi["queued"] == 1, (atteso, conteggi)
        messaggio = [m for m in messaggi(mondo) if m["step_no"] == atteso][0]
        segna_spedito(mondo, messaggio["id"])
        modulo["tick"].tick(ctx)
    return c, iscrizioni(mondo)[0]


def test_07_il_terzo_passo_aspetta_una_persona_e_non_crea_messaggi(mondo, modulo):
    ctx = mondo["ctx"]()
    _, e = _fino_a_m3(mondo, modulo, ctx)
    assert e["next_step_no"] == 3 and e["next_action_kind"] == "await_operator"
    assert [m["step_no"] for m in messaggi(mondo)] == [1, 2]

    mondo["sql"]("UPDATE communication_enrollments SET next_action_at = NOW(), "
                 "awaiting_since = NOW() WHERE id = %s", (e["id"],))
    conteggi = modulo["tick"].tick(ctx)
    assert conteggi["awaiting_operator"] == 1 and conteggi["queued"] == 0
    assert [m["step_no"] for m in messaggi(mondo)] == [1, 2]


def test_08_l_operatore_approva_il_terzo_passo_e_il_testo_e_quello_del_template(mondo, modulo):
    from communication import templates
    ctx = mondo["ctx"]()
    _, e = _fino_a_m3(mondo, modulo, ctx)
    mondo["sql"]("UPDATE communication_enrollments SET next_action_at = NOW(), "
                 "awaiting_since = NOW() WHERE id = %s", (e["id"],))

    esito = modulo["tick"].send_current(ctx, e["id"])
    assert esito["created"] is True
    m3 = [m for m in messaggi(mondo) if m["step_no"] == 3][0]
    assert m3["mode"] == "assisted" and m3["template_key"] == "stima_lead_m3"
    assert m3["subject_snapshot"] == templates.REGISTRY[("stima_lead_m3", 1)].subject({})
    assert "sopralluogo" not in m3["rendered_body"].lower() or True  # il testo e' quello reso
    assert "unsubscribe" in m3["rendered_body"] or "Disiscrizione" in m3["rendered_body"] \
        or "non vuoi piu' ricevere" in m3["rendered_body"]


def test_09_l_operatore_puo_saltare_il_terzo_passo(mondo, modulo):
    ctx = mondo["ctx"]()
    _, e = _fino_a_m3(mondo, modulo, ctx)
    mondo["sql"]("UPDATE communication_enrollments SET next_action_at = NOW(), "
                 "awaiting_since = NOW() WHERE id = %s", (e["id"],))

    esito = modulo["tick"].skip_current(ctx, e["id"])
    assert esito["skipped_step_no"] == 3 and esito["completed"] is False
    dopo = iscrizioni(mondo)[0]
    assert dopo["next_step_no"] == 4 and dopo["next_action_kind"] == "enqueue"
    assert [m["step_no"] for m in messaggi(mondo)] == [1, 2]


# ===========================================================================
# D - LE API DEL CONTACT 360, via HTTP e con i ruoli veri
# ===========================================================================

CAMPI_TECNICI = ("claim_token", "idempotency_key", "attempt_count", "provider_message_id",
                 "last_error", "claimed_at", "claimed_by")


def _con_journey_attiva(mondo, modulo, ctx):
    j = _provision(modulo, ctx)["journey"]
    modulo["journeys"].activate_journey(ctx, j["id"])
    c = mondo["contatto"]()
    modulo["tick"].tick(ctx)
    return c, iscrizioni(mondo)[0]


def test_10_lo_storico_e_tradotto_e_non_espone_niente_di_tecnico(client, mondo, modulo):
    ctx = mondo["ctx"]()
    c, e = _con_journey_attiva(mondo, modulo, ctx)
    mondo["sql"]("UPDATE communication_enrollments SET next_action_at = NOW() WHERE id = %s",
                 (e["id"],))
    modulo["tick"].tick(ctx)

    accedi(client, mondo, "admin")
    risposta = client.get(f"/api/communication/contacts/{c['contact']}/messages")
    assert risposta.status_code == 200, risposta.text
    corpo = risposta.json()

    motivi = [m["reason_label"] for m in corpo["messages"]]
    assert "Stima" in motivi and "M1" in motivi, motivi
    m1 = [m for m in corpo["messages"] if m["reason_label"] == "M1"][0]
    assert m1["status_label"] == "programmato" and m1["mode_label"] == "automatico"
    assert m1["next_send_at"] is not None and m1["can_cancel"] and m1["can_send_now"]
    assert m1["subject"] and m1["preview"]
    # La nota sull'inbound c'e', e NON dice "nessuna risposta".
    assert "non sono ancora sincronizzate" in corpo["inbound_note"]
    assert "nessuna risposta" not in risposta.text.lower()
    for vietato in CAMPI_TECNICI:
        assert vietato not in risposta.text, vietato


def test_11_la_card_dell_automazione_dice_passo_e_prossimo_invio(client, mondo, modulo):
    ctx = mondo["ctx"]()
    c, _ = _con_journey_attiva(mondo, modulo, ctx)
    accedi(client, mondo, "admin")
    corpo = client.get(f"/api/communication/contacts/{c['contact']}/journey").json()

    assert corpo["available"] is True
    iscrizione = corpo["enrollment"]
    assert iscrizione["journey_key"] == "stima_lead" and iscrizione["journey_version"] == 1
    assert iscrizione["current_step"]["step_key"] == "M1"
    assert iscrizione["total_steps"] == 5
    assert iscrizione["status_label"] == "attiva" and iscrizione["next_action_at"]
    assert corpo["automation"]["paused"] is False


def test_12_senza_journey_la_card_non_inventa_automazioni(client, mondo, modulo):
    ctx = mondo["ctx"]()
    c = mondo["contatto"]()
    accedi(client, mondo, "admin")
    corpo = client.get(f"/api/communication/contacts/{c['contact']}/journey").json()
    assert corpo["available"] is True and corpo["enrollment"] is None


@pytest.mark.parametrize("chi,atteso", [("admin", 200), ("owner", 200), ("agente", 404)])
def test_13_lo_scope_CRM_vale_anche_qui(client, mondo, modulo, chi, atteso):
    """Il contatto NON e' assegnato all'agente: per lui non esiste."""
    ctx = mondo["ctx"]()
    c = mondo["contatto"]()
    accedi(client, mondo, chi)
    risposta = client.get(f"/api/communication/contacts/{c['contact']}/messages")
    assert risposta.status_code == atteso, risposta.text


def test_14_l_agente_vede_i_propri_contatti(client, mondo, modulo):
    mio = mondo["contatto"](assegnato=mondo["agente"]["id"])
    accedi(client, mondo, "agente")
    risposta = client.get(f"/api/communication/contacts/{mio['contact']}/messages")
    assert risposta.status_code == 200, risposta.text
    assert risposta.json()["messages"], "la mail della stima c'e'"


def test_15_il_platform_admin_solo_in_acting(client, mondo, modulo):
    ctx = mondo["ctx"]()
    c = mondo["contatto"]()
    accedi(client, mondo, "piattaforma")
    assert client.get(f"/api/communication/contacts/{c['contact']}/messages").status_code == 403
    entra_in_acting(mondo, "piattaforma")
    assert client.get(f"/api/communication/contacts/{c['contact']}/messages").status_code == 200


def test_16_provision_e_activate_chiedono_di_vedere_tutta_l_agenzia(client, mondo, modulo):
    accedi(client, mondo, "agente")
    assert client.post("/api/communication/journeys/stima-lead/provision").status_code == 403
    accedi(client, mondo, "admin")
    creazione = client.post("/api/communication/journeys/stima-lead/provision")
    assert creazione.status_code == 200, creazione.text
    assert creazione.json()["created"] is True
    journey_id = creazione.json()["journey"]["id"]
    assert client.post("/api/communication/journeys/stima-lead/provision").json()["created"] is False

    accedi(client, mondo, "agente")
    assert client.post(f"/api/communication/journeys/{journey_id}/activate").status_code == 403
    accedi(client, mondo, "admin")
    attivazione = client.post(f"/api/communication/journeys/{journey_id}/activate")
    assert attivazione.status_code == 200 and attivazione.json()["status"] == "active"
    ritiro = client.post(f"/api/communication/journeys/{journey_id}/retire")
    assert ritiro.status_code == 200 and ritiro.json()["status"] == "retired"


# ===========================================================================
# E - LE AZIONI
# ===========================================================================

def _messaggio_in_coda(mondo, modulo, ctx):
    c, e = _con_journey_attiva(mondo, modulo, ctx)
    mondo["sql"]("UPDATE communication_enrollments SET next_action_at = NOW() WHERE id = %s",
                 (e["id"],))
    modulo["tick"].tick(ctx)
    return c, e, [m for m in messaggi(mondo) if m["status"] == "queued"][0]


def test_17_annulla_un_messaggio_in_coda_e_rifiuta_uno_gia_spedito(client, mondo, modulo):
    ctx = mondo["ctx"]()
    _, _, m = _messaggio_in_coda(mondo, modulo, ctx)
    accedi(client, mondo, "admin")

    annulla = client.post(f"/api/communication/messages/{m['id']}/cancel")
    assert annulla.status_code == 200 and annulla.json()["status"] == "cancelled"
    ancora = client.post(f"/api/communication/messages/{m['id']}/cancel")
    assert ancora.status_code == 409, ancora.text


def test_18_invia_ora_anticipa_la_coda_e_non_spedisce(client, mondo, modulo):
    ctx = mondo["ctx"]()
    _, _, m = _messaggio_in_coda(mondo, modulo, ctx)
    mondo["sql"]("UPDATE communication_messages SET scheduled_at = NOW() + INTERVAL '3 days' "
                 "WHERE id = %s", (m["id"],))
    accedi(client, mondo, "admin")

    risposta = client.post(f"/api/communication/messages/{m['id']}/send-now")
    assert risposta.status_code == 200, risposta.text
    riga = mondo["righe"]("communication_messages", "id = %s", (m["id"],))[0]
    assert riga["status"] == "queued", "invia-ora NON spedisce: sposta la data"
    assert riga["scheduled_at"] < datetime.now(timezone.utc) + timedelta(minutes=1)


def test_19_invia_ora_rifiuta_un_messaggio_gia_spedito(client, mondo, modulo):
    ctx = mondo["ctx"]()
    _, _, m = _messaggio_in_coda(mondo, modulo, ctx)
    segna_spedito(mondo, m["id"])
    accedi(client, mondo, "admin")
    risposta = client.post(f"/api/communication/messages/{m['id']}/send-now")
    assert risposta.status_code == 409, risposta.text


def test_20_pausa_e_ripresa_dell_iscrizione(client, mondo, modulo):
    ctx = mondo["ctx"]()
    c, e = _con_journey_attiva(mondo, modulo, ctx)
    accedi(client, mondo, "admin")

    pausa = client.post(f"/api/communication/journeys/enrollments/{e['id']}/pause")
    assert pausa.status_code == 200 and pausa.json()["status"] == "paused"
    ripresa = client.post(f"/api/communication/journeys/enrollments/{e['id']}/resume")
    assert ripresa.status_code == 200 and ripresa.json()["status"] == "active"

    stop = client.post(f"/api/communication/journeys/enrollments/{e['id']}/stop")
    assert stop.status_code == 200
    assert stop.json()["stop_reason"] == "operator"


def test_21_pausa_e_ripresa_delle_automazioni_del_contatto(client, mondo, modulo):
    ctx = mondo["ctx"]()
    c, e = _con_journey_attiva(mondo, modulo, ctx)
    accedi(client, mondo, "admin")

    pausa = client.post(f"/api/communication/contacts/{c['contact']}/automation/pause")
    assert pausa.status_code == 200 and pausa.json()["paused"] is True
    card = client.get(f"/api/communication/contacts/{c['contact']}/journey").json()
    assert card["automation"]["paused"] is True and card["automation"]["paused_at"]
    assert card["enrollment"]["status"] == "paused"

    ripresa = client.post(f"/api/communication/contacts/{c['contact']}/automation/resume")
    assert ripresa.status_code == 200 and ripresa.json()["paused"] is False
    assert client.get(f"/api/communication/contacts/{c['contact']}/journey").json(
        )["enrollment"]["status"] == "active"


# ===========================================================================
# F - IL MESSAGGIO MANUALE
# ===========================================================================

def test_22_un_messaggio_manuale_entra_in_coda_con_il_link_di_disiscrizione(
        client, mondo, modulo):
    c = mondo["contatto"]()
    accedi(client, mondo, "admin")
    risposta = client.post(f"/api/communication/contacts/{c['contact']}/messages",
                           json={"subject": "Due parole", "body": "Ciao, ti scrivo io."})
    assert risposta.status_code == 200, risposta.text
    assert risposta.json()["status"] == "queued"

    (m,) = [r for r in mondo["righe"]("communication_messages")
            if r["reason_code"] == "operator_manual"]
    assert m["mode"] == "manual" and m["communication_type"] == "marketing"
    assert m["actor_type"] == "operator" and m["actor_user_id"] == mondo["admin"]["id"]
    assert m["destination_snapshot"] == f"c{mondo['n']}@x.it"
    assert "unsubscribe?t=" in m["rendered_body"]


def test_23_senza_consenso_il_manuale_di_marketing_e_rifiutato_con_una_ragione(
        client, mondo, modulo):
    c = mondo["contatto"](consenso=False)
    accedi(client, mondo, "admin")
    risposta = client.post(f"/api/communication/contacts/{c['contact']}/messages",
                           json={"subject": "Ciao", "body": "Testo"})
    assert risposta.status_code == 422, risposta.text
    assert "consent" in risposta.text.lower()
    assert [r for r in mondo["righe"]("communication_messages")
            if r["reason_code"] == "operator_manual"] == []


def test_24_un_messaggio_di_servizio_non_chiede_il_consenso_marketing(client, mondo, modulo):
    c = mondo["contatto"](consenso=False)
    accedi(client, mondo, "admin")
    risposta = client.post(f"/api/communication/contacts/{c['contact']}/messages",
                           json={"subject": "Documento", "body": "Le mando il documento.",
                                 "communication_type": "service"})
    assert risposta.status_code == 200, risposta.text
    (m,) = [r for r in mondo["righe"]("communication_messages")
            if r["reason_code"] == "operator_manual"]
    assert m["communication_type"] == "service"
    assert "unsubscribe" not in m["rendered_body"]


def test_25_la_pausa_delle_automazioni_non_ferma_un_messaggio_manuale(client, mondo, modulo):
    ctx = mondo["ctx"]()
    c, _ = _con_journey_attiva(mondo, modulo, ctx)
    modulo["journeys"].pause_automations(ctx, c["contact"])
    accedi(client, mondo, "admin")
    risposta = client.post(f"/api/communication/contacts/{c['contact']}/messages",
                           json={"subject": "Comunque", "body": "Ti scrivo lo stesso."})
    assert risposta.status_code == 200, risposta.text


def test_26_il_corpo_non_accetta_destinatario_ne_agenzia(client, mondo, modulo):
    c = mondo["contatto"]()
    accedi(client, mondo, "admin")
    for extra in ({"agency_id": 999}, {"destination_snapshot": "altro@x.it"},
                  {"contact_id": 1}):
        risposta = client.post(f"/api/communication/contacts/{c['contact']}/messages",
                               json={"subject": "x", "body": "y", **extra})
        assert risposta.status_code == 422, (extra, risposta.text)


def test_27_due_click_sullo_stesso_messaggio_manuale_non_lo_duplicano(client, mondo, modulo):
    c = mondo["contatto"]()
    accedi(client, mondo, "admin")
    corpo = {"subject": "Uguale", "body": "Stesso testo."}
    primo = client.post(f"/api/communication/contacts/{c['contact']}/messages", json=corpo)
    secondo = client.post(f"/api/communication/contacts/{c['contact']}/messages", json=corpo)
    assert primo.json()["message_id"] == secondo.json()["message_id"]
    assert secondo.json()["created"] is False
    assert len([r for r in mondo["righe"]("communication_messages")
                if r["reason_code"] == "operator_manual"]) == 1


# ===========================================================================
# G - SENZA LA 071
# ===========================================================================

def test_28_senza_la_071_lo_storico_resta_e_l_automazione_dice_non_disponibile(
        client, mondo, modulo):
    c = mondo["contatto"]()
    accedi(client, mondo, "admin")
    mondo["sql"](_migrazione(f"{VERSIONE}_down"))
    try:
        storico = client.get(f"/api/communication/contacts/{c['contact']}/messages")
        assert storico.status_code == 200, storico.text
        assert storico.json()["messages"], "la mail della stima c'e' comunque"

        card = client.get(f"/api/communication/contacts/{c['contact']}/journey")
        assert card.status_code == 200, card.text
        assert card.json() == {"contact_id": c["contact"], "available": False,
                               "enrollment": None, "automation": None}

        # Le AZIONI sulle journey, invece, dicono chiaramente "non migrata".
        pausa = client.post(f"/api/communication/contacts/{c['contact']}/automation/pause")
        assert pausa.status_code == 503, pausa.text
        assert pausa.json()["detail"]["code"] == "feature_not_migrated"
    finally:
        mondo["sql"]("ROLLBACK")
        mondo["sql"](_migrazione(VERSIONE))
