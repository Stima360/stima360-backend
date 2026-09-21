"""P29-3G su PostgreSQL reale: i due atti assistiti prima e dopo la scadenza.

COSA SOLO UN DATABASE PUO' DIRE

Il modulo puro prova che la decisione e' una sola e che i tre chiamanti la
interrogano. Qui si prova la cosa che conta per chi usa il prodotto: che un
`skip-current` anticipato **non muova niente**. Non che risponda male - che
non muova niente. `next_step_no`, `run_no`, `next_action_kind`,
`next_action_at`, i messaggi, la timeline: tutto identico prima e dopo il
rifiuto, riga alla mano.

E' il difetto trovato dal vivo in P29-3F, dove uno skip anticipato rispose
200 e salto' M3 per davvero.

INVIO E SALTO SU ISCRIZIONI SEPARATE, sempre. Provarli sulla stessa
iscrizione farebbe consumare al primo il passo del secondo - che e'
esattamente l'errore costato M3 durante la certificazione.

L'harness e' quello di P29-3C, importato invece che ricopiato.
"""
from __future__ import annotations

import os
from datetime import timedelta

import pytest

from tests.test_p29_3c_orchestrator_postgres import (  # noqa: F401
    GIORNO, PASSWORD, accedi, client, db, iscrizioni, journey_attiva, mondo,
    modulo, passo,
)

pytestmark = pytest.mark.skipif(not os.getenv("P29_TEST_DSN"),
                                reason="P29_TEST_DSN non impostata")

#: Un passo assistito dovuto SUBITO, e uno che scade fra una settimana.
ASSISTITO_SUBITO = [passo(1, mode="assisted", delay=0)]
ASSISTITO_FRA_UNA_SETTIMANA = [passo(1, mode="assisted", delay=7 * GIORNO.total_seconds())]

CAMPI_SORVEGLIATI = ("status", "next_step_no", "run_no", "next_action_kind",
                     "next_action_at", "awaiting_since", "stopped_at", "stop_reason")


def _prepara(mondo, modulo, passi, *, key="stima_lead"):
    """Una journey attiva, un contatto, e il tick che porta all'attesa.

    `key` serve ai test che hanno bisogno di DUE iscrizioni indipendenti: una
    seconda journey con la stessa chiave violerebbe l'unicita'
    (agenzia, chiave, versione), che e' giusto che la violi.
    """
    ctx = mondo["ctx"]()
    journey_attiva(modulo, ctx, key=key, passi=passi)
    mondo["contatto"]()
    modulo["tick"].tick(ctx)
    return iscrizioni(mondo)[-1]


def _fotografia(mondo, enrollment_id):
    riga = [r for r in iscrizioni(mondo) if r["id"] == enrollment_id][0]
    return {c: riga[c] for c in CAMPI_SORVEGLIATI}


def _messaggi(mondo):
    """I messaggi NATI DALLA JOURNEY. Il trigger `stima_pdf` che `contatto()`
    inserisce c'e' sempre e non e' cio' che si sta misurando."""
    return mondo["righe"]("communication_messages", "enrollment_id IS NOT NULL")


def _eventi(mondo):
    return mondo["righe"]("seller_timeline_events")


# ===========================================================================
# A - PRIMA DELLA SCADENZA: NIENTE
# ===========================================================================

def test_01_il_read_model_non_offre_le_azioni_prima_della_scadenza(mondo, modulo):
    """A del mandato. L'iscrizione ASPETTA una persona - e la card lo dice -
    ma quella persona non puo' ancora agire."""
    from communication import contact_view

    ctx = mondo["ctx"]()
    iscrizione = _prepara(mondo, modulo, ASSISTITO_FRA_UNA_SETTIMANA)
    card = contact_view.journey(ctx, iscrizione["contact_id"])["enrollment"]

    assert card["awaiting_operator"] is True, "l'attesa e' vera e va detta"
    assert card["status_label"] == "in attesa dell'agente"
    assert card["can_send_current"] is False
    assert card["can_skip_current"] is False


def test_02_send_current_anticipato_e_rifiutato_e_non_muove_niente(mondo, modulo):
    """B del mandato."""
    from communication.exceptions import ConflictError

    ctx = mondo["ctx"]()
    iscrizione = _prepara(mondo, modulo, ASSISTITO_FRA_UNA_SETTIMANA)
    prima, messaggi_prima = _fotografia(mondo, iscrizione["id"]), len(_messaggi(mondo))

    with pytest.raises(ConflictError) as errore:
        modulo["tick"].send_current(ctx, iscrizione["id"])

    assert "not due yet" in str(errore.value)
    assert _fotografia(mondo, iscrizione["id"]) == prima
    assert len(_messaggi(mondo)) == messaggi_prima


def test_03_skip_current_anticipato_e_rifiutato_e_non_muove_niente(mondo, modulo):
    """C del mandato, ed e' IL test di questa fase.

    Prima rispondeva 200 e saltava il passo. Qui non si guarda solo il
    rifiuto: si guarda che l'iscrizione non abbia cambiato un campo, che non
    sia nato un messaggio, e che non sia stato scritto un evento di salto
    nella timeline - il quale, avendo una chiave di idempotenza, avrebbe
    impedito al salto VERO di essere registrato una volta arrivato il
    momento.
    """
    from communication.exceptions import ConflictError

    ctx = mondo["ctx"]()
    iscrizione = _prepara(mondo, modulo, ASSISTITO_FRA_UNA_SETTIMANA)
    prima = _fotografia(mondo, iscrizione["id"])
    messaggi_prima, eventi_prima = len(_messaggi(mondo)), len(_eventi(mondo))

    with pytest.raises(ConflictError) as errore:
        modulo["tick"].skip_current(ctx, iscrizione["id"])

    assert "not due yet" in str(errore.value)
    dopo = _fotografia(mondo, iscrizione["id"])
    assert dopo == prima, {c: (prima[c], dopo[c]) for c in prima if prima[c] != dopo[c]}
    assert dopo["next_step_no"] == 1, "il passo e' stato saltato"
    assert dopo["run_no"] == iscrizione["run_no"], "run_no e' cambiato"
    assert len(_messaggi(mondo)) == messaggi_prima
    assert len(_eventi(mondo)) == eventi_prima, "un evento di salto e' stato scritto"


def test_04_nemmeno_dieci_tentativi_anticipati_smuovono_l_iscrizione(mondo, modulo):
    """Il rifiuto non si logora: non c'e' un contatore che cede."""
    from communication.exceptions import ConflictError

    ctx = mondo["ctx"]()
    iscrizione = _prepara(mondo, modulo, ASSISTITO_FRA_UNA_SETTIMANA)
    prima = _fotografia(mondo, iscrizione["id"])

    for _ in range(5):
        with pytest.raises(ConflictError):
            modulo["tick"].send_current(ctx, iscrizione["id"])
        with pytest.raises(ConflictError):
            modulo["tick"].skip_current(ctx, iscrizione["id"])

    assert _fotografia(mondo, iscrizione["id"]) == prima
    assert _messaggi(mondo) == []


def test_05_la_rotta_HTTP_risponde_409_a_entrambe(client, mondo, modulo):
    """La sicurezza non dipende dall'interfaccia: chi chiama l'API a mano
    prende lo stesso rifiuto di chi non vede il bottone."""
    ctx = mondo["ctx"]()
    iscrizione = _prepara(mondo, modulo, ASSISTITO_FRA_UNA_SETTIMANA)
    prima = _fotografia(mondo, iscrizione["id"])

    accedi(client, mondo, "admin")
    base = f"/api/communication/journeys/enrollments/{iscrizione['id']}"
    invio = client.post(f"{base}/send-current")
    salto = client.post(f"{base}/skip-current")

    assert invio.status_code == 409, invio.text
    assert salto.status_code == 409, salto.text
    assert "not due yet" in salto.text
    assert _fotografia(mondo, iscrizione["id"]) == prima


# ===========================================================================
# B - ALLA SCADENZA: TUTTE E DUE, SU ISCRIZIONI SEPARATE
# ===========================================================================

def test_06_alla_scadenza_il_read_model_offre_le_azioni(mondo, modulo):
    """D del mandato."""
    from communication import contact_view

    ctx = mondo["ctx"]()
    iscrizione = _prepara(mondo, modulo, ASSISTITO_SUBITO)
    card = contact_view.journey(ctx, iscrizione["contact_id"])["enrollment"]

    assert card["awaiting_operator"] is True
    assert card["can_send_current"] is True
    assert card["can_skip_current"] is True


def test_07_alla_scadenza_send_current_manda(mondo, modulo):
    """E del mandato. Iscrizione dedicata: non condivide niente con il salto."""
    ctx = mondo["ctx"]()
    iscrizione = _prepara(mondo, modulo, ASSISTITO_SUBITO)

    esito = modulo["tick"].send_current(ctx, iscrizione["id"])

    assert esito["created"] is True and esito["stopped"] is None
    assert esito["message"]["mode"] == "assisted"
    assert esito["message"]["step_no"] == 1
    assert esito["message"]["status"] == "queued"


def test_08_alla_scadenza_skip_current_salta(mondo, modulo):
    """F del mandato. Anche questa su un'iscrizione sua."""
    ctx = mondo["ctx"]()
    iscrizione = _prepara(mondo, modulo, [
        passo(1, mode="assisted", delay=0),
        passo(2, delay=GIORNO.total_seconds(), delay_from="previous_step_sent")])

    esito = modulo["tick"].skip_current(ctx, iscrizione["id"])

    assert esito["skipped_step_no"] == 1 and esito["completed"] is False
    assert esito["enrollment"]["next_step_no"] == 2
    assert _messaggi(mondo) == [], "il salto non manda niente"


def test_09_il_confine_e_incluso(mondo, modulo):
    """Un passo dovuto ESATTAMENTE adesso e' dovuto. Il rifiuto vale per il
    futuro, non per l'istante stesso della scadenza."""
    ctx = mondo["ctx"]()
    iscrizione = _prepara(mondo, modulo, ASSISTITO_SUBITO)
    quando = [r for r in iscrizioni(mondo) if r["id"] == iscrizione["id"]][0]["next_action_at"]

    esito = modulo["tick"].send_current(ctx, iscrizione["id"], now=quando)
    assert esito["created"] is True

    # E un microsecondo prima, no. Seconda journey e secondo contatto: le
    # due meta' di questo test non devono toccarsi.
    altra = _prepara(mondo, modulo, ASSISTITO_SUBITO, key="stima_lead_confine")
    from communication.exceptions import ConflictError
    with pytest.raises(ConflictError):
        modulo["tick"].skip_current(
            ctx, altra["id"],
            now=[r for r in iscrizioni(mondo) if r["id"] == altra["id"]][0]["next_action_at"]
            - timedelta(microseconds=1))


def test_10_una_iscrizione_attiva_SENZA_scadenza_il_database_non_la_permette(mondo, modulo):
    """G del mandato, e la risposta e' migliore di quella che cercavo.

    Il guard applicativo tratta `next_action_at IS NULL` come "nessuna
    azione assistita consentita" - lo prova `test_p29_3g_final_fixes.py`
    sulla decisione pura, dove quello stato si puo' costruire a mano. Qui si
    prova che nel DATABASE quello stato non esiste affatto: la matrice della
    071 pretende `next_action_at IS NOT NULL` su ogni iscrizione `active`, e
    rifiuta la scrittura.

    Il ramo NULL del guard resta quindi una difesa in profondita' e non un
    caso raggiungibile - ed e' bene che ci sia: prima di questa fase quel
    confronto era `riga["next_action_at"] > adesso`, che su un NULL avrebbe
    sollevato `TypeError` e fatto rispondere 500 invece di 409.
    """
    import psycopg2

    iscrizione = _prepara(mondo, modulo, ASSISTITO_SUBITO)
    with pytest.raises(psycopg2.errors.CheckViolation) as errore:
        mondo["sql"]("UPDATE communication_enrollments SET next_action_at = NULL "
                     "WHERE id = %s", (iscrizione["id"],))
    assert "comm_enroll_matrix_chk" in str(errore.value)

    # E l'iscrizione e' rimasta quella di prima.
    assert _fotografia(mondo, iscrizione["id"])["next_action_at"] is not None


# ===========================================================================
# C - L'ANTEPRIMA, CONTRO IL LEDGER VERO
# ===========================================================================

def test_11_l_anteprima_di_un_corpo_HTML_nel_ledger_e_leggibile(mondo, modulo):
    """Il difetto come si vedeva su TEST: la mail della stima e' HTML, e
    l'anteprima ne mostrava i tag. Qui il corpo passa davvero dal database.

    Il messaggio si ACCODA con quel corpo invece di riscrivere quello
    esistente: la guardia del ledger vieta di modificare `rendered_body`
    (cio' che un messaggio dice di essere non e' riscrivibile), ed e' la
    ragione per cui questa correzione vive nella lettura e non nel dato.
    """
    from communication import contact_view, service as comm_service

    ctx = mondo["ctx"]()
    dati = mondo["contatto"]()
    corpo = ('<div style="font-family:Arial,Helvetica,sans-serif; color:#222;">'
             '<h2 style="color:#0b6bff;">Il tuo Piano&nbsp;Vendita</h2>'
             '<p>Ciao <b>Anna</b>, la stima &egrave; pronta.</p></div>')
    accodato = comm_service.enqueue(
        ctx, contact_id=dati["contact"], channel="email", communication_type="service",
        mode="automatic", reason_code="stima_pdf", rendered_body=corpo,
        subject_snapshot="Il tuo Piano Vendita", destination_snapshot="anna@esempio.test",
        stima_id=dati["stima"], idempotency_key=f"p29_3g:html:{dati['stima']}")

    riga = [m for m in contact_view.messages(ctx, dati["contact"])["messages"]
            if m["id"] == accodato["message"]["id"]][0]

    assert "<" not in riga["preview"] and "style=" not in riga["preview"], riga["preview"]
    assert "Il tuo Piano Vendita" in riga["preview"]
    assert "Ciao Anna, la stima è pronta." in riga["preview"]

    # E il ledger conserva il corpo esattamente com'era: questa e' una lettura.
    conservato = mondo["righe"]("communication_messages", "id = %s",
                                (accodato["message"]["id"],))[0]
    assert conservato["rendered_body"] == corpo
