"""P29-3G senza database: l'anteprima leggibile e la decisione condivisa.

DUE DIFETTI TROVATI DAL VIVO, non immaginati a tavolino. La certificazione
P29-3F li ha visti su dati veri, su TEST, e questo modulo li chiude e monta
la sentinella perche' non tornino.

IL PRIMO era una lettura: la mail della stima e' HTML, e l'anteprima ne
mostrava i primi centosessanta caratteri cosi' com'erano - `<div
style="font-family:Arial`, identico su ogni messaggio e quindi buono a
distinguerne nessuno.

IL SECONDO era una decisione presa in tre posti diversi. "Questo passo
assistito si puo' approvare o saltare?" aveva tre risposte: il servizio
dell'invio guardava l'orologio, quello del salto no, e la scheda contatto
nemmeno. Risultato osservato: la card offriva i due bottoni con una settimana
di anticipo, "Approva e invia" rispondeva 409, e "Salta questo passo"
SALTAVA DAVVERO un passo che nessuno aveva ancora potuto leggere. Adesso la
risposta e' una sola, in `journey_enums.assisted_action_is_due`, e i tre
chiamanti la interrogano invece di ricalcolarla.

Le prove che hanno bisogno di righe vere - i 409 contro l'applicazione, e
l'iscrizione che non si muove di un campo - stanno nel modulo gemello
`test_p29_3g_final_fixes_postgres.py`.
"""
from __future__ import annotations

import hashlib
import inspect
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

ORA = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
GIORNO = timedelta(days=1)


def _iscrizione(**cambiamenti):
    base = {"next_action_kind": "await_operator", "next_action_at": ORA - timedelta(minutes=1)}
    return {**base, **cambiamenti}


# ===========================================================================
# A - L'ANTEPRIMA
# ===========================================================================

CORPO_STIMA = (
    '<div style="font-family:Arial,Helvetica,sans-serif; color:#222; line-height:1.6;">'
    '  <h2 style="margin:0 0 14px 0; color:#0b6bff;">Il tuo Piano&nbsp;Vendita</h2>'
    '  <p>Ciao <b>Anna</b>, la stima &egrave; pronta &amp; ti aspetta.</p>'
    '</div>'
)


def _anteprima(corpo, **cambiamenti):
    from communication import contact_view
    riga = {"id": 1, "rendered_body": corpo, "status": "sent", **cambiamenti}
    return contact_view._messaggio_visibile(riga)["preview"]


def test_01_un_corpo_HTML_diventa_testo_senza_un_solo_tag():
    """Il difetto, preso alla lettera: questo e' il corpo che su TEST
    produceva `<div style="font-family:Arial`."""
    testo = _anteprima(CORPO_STIMA)
    assert "<" not in testo and ">" not in testo, testo
    assert "style=" not in testo and "font-family" not in testo, testo
    assert "Il tuo Piano Vendita" in testo
    assert "Ciao Anna, la stima" in testo, testo


def test_02_le_entita_HTML_sono_decodificate():
    testo = _anteprima(CORPO_STIMA)
    assert "è pronta & ti aspetta" in testo, testo
    for entita in ("&nbsp;", "&amp;", "&egrave;", "&lt;", "&#"):
        assert entita not in testo, entita


def test_03_lo_spazio_bianco_e_collassato_e_l_anteprima_sta_su_una_riga():
    testo = _anteprima('<p>Prima riga</p>\n\n\n   <p>Seconda     riga</p>')
    assert testo == "Prima riga Seconda riga", repr(testo)
    assert "\n" not in testo and "  " not in testo


def test_04_style_e_script_non_lasciano_il_loro_contenuto():
    """Togliere solo i delimitatori lascerebbe il CSS nel testo: sarebbe la
    stessa anteprima illeggibile con un'altra faccia."""
    corpo = ('<style>.titolo{color:#0b6bff;font-size:22px}</style>'
             '<script>var traccia = 1;</script>'
             '<p>Il testo che conta</p>')
    testo = _anteprima(corpo)
    assert testo == "Il testo che conta", repr(testo)
    for rumore in ("color", "font-size", "var", "traccia", "#0b6bff"):
        assert rumore not in testo, rumore


@pytest.mark.parametrize("corpo", [
    "Ciao Stima, ti scrivo solo per sapere se hai ricevuto la stima.",
    "Sonda di servizio.",
    "Ciao, se a < b > c allora la disuguaglianza regge",
    "5<10 ma 20>15, e nessuno ha scritto HTML",
    "Un testo con una & commerciale e un &amp; scritto a mano",
])
def test_05_un_corpo_di_testo_puro_non_viene_toccato(corpo):
    """La regola piu' importante di questa correzione: chi scrive a mano non
    deve vedere la propria frase smontata. La prima stesura dell'espressione
    che riconosce l'HTML ammetteva lo spazio dopo `<` e trasformava
    "a < b > c" in "a c": e' il motivo per cui questi casi sono qui."""
    assert _anteprima(corpo) == " ".join(corpo.split())


def test_06_l_anteprima_rispetta_la_lunghezza_massima():
    from communication import contact_view
    lungo = "<p>" + ("parola " * 400) + "</p>"
    testo = _anteprima(lungo)
    assert len(testo) <= contact_view.ANTEPRIMA
    # E il taglio avviene DOPO aver reso leggibile: un troncamento prima
    # lascerebbe un'anteprima piu' corta del previsto una volta tolti i tag.
    assert len(testo) == contact_view.ANTEPRIMA


def test_07_un_corpo_vuoto_o_assente_resta_vuoto():
    assert _anteprima(None) == ""
    assert _anteprima("") == ""
    assert _anteprima("   \n  ") == ""


def test_08_il_corpo_del_ledger_non_viene_modificato():
    """Questa e' una LETTURA. Il ledger conserva cio' che e' stato spedito,
    parola per parola, e nessuna anteprima puo' riscriverlo."""
    from communication import contact_view
    riga = {"id": 1, "rendered_body": CORPO_STIMA, "status": "sent"}
    copia = dict(riga)
    contact_view._messaggio_visibile(riga)
    assert riga == copia
    assert riga["rendered_body"] == CORPO_STIMA


def test_09_l_anteprima_non_apre_una_porta_a_campi_nuovi():
    """La whitelist resta la whitelist: la correzione cambia un VALORE, non
    l'insieme delle chiavi che escono."""
    from communication import contact_view
    riga = {"id": 7, "rendered_body": CORPO_STIMA, "status": "queued",
            "claim_token": "segreto", "idempotency_key": "chiave",
            "last_error": "smtp 550", "attempt_count": 3,
            "destination_snapshot": "anna@esempio.test", "provider": "smtp"}
    visibile = contact_view._messaggio_visibile(riga)
    assert set(visibile) == {
        "id", "created_at", "scheduled_at", "sent_at", "channel", "direction",
        "communication_type", "reason_code", "reason_label", "status", "status_label",
        "mode", "mode_label", "subject", "preview", "next_send_at", "from_journey",
        "step_no", "can_cancel", "can_send_now"}
    testo = repr(visibile)
    for segreto in ("segreto", "chiave", "smtp 550", "anna@esempio.test"):
        assert segreto not in testo, segreto


# ===========================================================================
# B - LA DECISIONE CONDIVISA
# ===========================================================================

def test_10_dovuta_solo_quando_tutte_e_tre_le_condizioni_valgono():
    from communication.journey_enums import assisted_action_is_due as dovuta
    assert dovuta(_iscrizione(next_action_at=ORA - timedelta(minutes=1)), ORA) is True
    assert dovuta(_iscrizione(next_action_at=ORA), ORA) is True, "il confine e' incluso"
    assert dovuta(_iscrizione(next_action_at=ORA + timedelta(seconds=1)), ORA) is False
    assert dovuta(_iscrizione(next_action_at=ORA + 7 * GIORNO), ORA) is False
    assert dovuta(_iscrizione(next_action_at=None), ORA) is False
    assert dovuta(_iscrizione(next_action_kind="enqueue"), ORA) is False
    assert dovuta(_iscrizione(next_action_kind="enqueue", next_action_at=None), ORA) is False


def test_11_la_decisione_pretende_un_istante_con_fuso():
    """Un confronto con un istante ingenuo deve SOLLEVARE, non sbagliare di
    due ore in silenzio."""
    from communication.journey_enums import assisted_action_is_due as dovuta
    with pytest.raises(TypeError):
        dovuta(_iscrizione(), datetime(2026, 9, 21, 12, 0))


def test_12_la_decisione_e_UNA_e_i_tre_chiamanti_la_interrogano():
    """SENTINELLA DEL DIFETTO. Prima la stessa domanda aveva tre risposte
    scritte in tre posti; se ne ricompare una quarta, questo test cade."""
    from communication import contact_view, journey_enums, journey_tick

    assert inspect.getmodule(journey_enums.assisted_action_is_due) is journey_enums
    for modulo in (contact_view, journey_tick):
        assert "assisted_action_is_due" in inspect.getsource(modulo), modulo.__name__

    # E la scheda contatto non rifa' il confronto per conto suo: `next_action_at`
    # lo ESPONE - e' un campo della card - ma non lo mette mai a paragone con
    # un orologio, perche' quel paragone e' della decisione condivisa.
    codice_view = re.sub(r'"{3}[\s\S]*?"{3}', "", inspect.getsource(contact_view))
    codice_view = re.sub(r"#[^\n]*", "", codice_view)
    for confronto in ('next_action_at"] <', 'next_action_at"] >', "next_action_at <",
                      "next_action_at >"):
        assert confronto not in codice_view, confronto


def test_13_il_read_model_offre_le_azioni_SOLO_alla_scadenza():
    """Il difetto come si vedeva: due bottoni offerti con una settimana di
    anticipo. La card puo' dire che l'iscrizione ASPETTA una persona - e'
    vero - ma non che quella persona possa agire adesso."""
    from communication import contact_view
    sorgente = inspect.getsource(contact_view.journey)
    assert '"can_send_current": azionabile' in sorgente
    assert '"can_skip_current": azionabile' in sorgente
    assert "azionabile = assisted_action_is_due(" in sorgente
    # `awaiting_operator` resta legato al TIPO di azione: e' l'etichetta, e
    # dice una cosa diversa.
    assert '"awaiting_operator": in_attesa' in sorgente


def test_14_i_due_service_passano_dallo_stesso_contratto():
    from communication import journey_tick
    for funzione in (journey_tick.send_current, journey_tick.skip_current):
        sorgente = inspect.getsource(funzione)
        assert "_pretendi_azione_assistita(" in sorgente, funzione.__name__
    # E il raiser chiede alla decisione condivisa invece di riscriverla.
    raiser = inspect.getsource(journey_tick._pretendi_azione_assistita)
    assert "assisted_action_is_due(riga, adesso)" in raiser


def test_15_il_rifiuto_dice_QUALE_delle_tre_condizioni_manca():
    """A chi ha cliccato non basta "no": un passo automatico, un'iscrizione
    senza scadenza e un passo non ancora dovuto sono tre situazioni diverse
    e portano a tre gesti diversi."""
    from communication.exceptions import ConflictError
    from communication.journey_tick import _pretendi_azione_assistita as pretendi

    with pytest.raises(ConflictError) as automatico:
        pretendi(_iscrizione(next_action_kind="enqueue"), ORA, enrollment_id=1, step_no=3)
    assert "not waiting for an operator" in str(automatico.value)

    with pytest.raises(ConflictError) as senza_data:
        pretendi(_iscrizione(next_action_at=None), ORA, enrollment_id=1, step_no=3)
    assert "no due time" in str(senza_data.value)

    with pytest.raises(ConflictError) as presto:
        pretendi(_iscrizione(next_action_at=ORA + 7 * GIORNO), ORA, enrollment_id=1, step_no=3)
    assert "not due yet" in str(presto.value)
    assert "2026-09-28" in str(presto.value), "il rifiuto dice QUANDO"

    # E quando e' dovuta non solleva niente.
    assert pretendi(_iscrizione(), ORA, enrollment_id=1, step_no=3) is None


# ===========================================================================
# C - IL PERIMETRO: NIENTE ALTRO E' CAMBIATO
# ===========================================================================

def _git(*argomenti) -> str:
    return subprocess.run(["git", "--no-optional-locks", *argomenti],
                          cwd=ROOT, capture_output=True, text=True).stdout


def test_16_i_ritardi_della_sequenza_non_sono_stati_toccati():
    from communication import journey_catalog as cat
    assert [p["delay_seconds"] // 86400 for p in cat.PASSI_STIMA_LEAD] == [1, 4, 7, 14, 30]
    assert [p["default_mode"] for p in cat.PASSI_STIMA_LEAD] == [
        "automatic", "automatic", "assisted", "automatic", "automatic"]
    assert cat.FINESTRA_LAVORATIVA == {"days": [1, 2, 3, 4, 5, 6],
                                       "from": "09:00", "to": "19:00"}


def test_17_i_cinque_template_sono_gli_stessi():
    from communication import templates
    impronte = {
        "stima_lead_m1": "57633d44a89a6df9", "stima_lead_m2": "929d7e224aa554a9",
        "stima_lead_m3": "37c5b021227bb2a1", "stima_lead_m4": "272af4a5c51ee83c",
        "stima_lead_m5": "6c1190114db4dc27",
    }
    for chiave, attesa in impronte.items():
        sorgente = inspect.getsource(templates.REGISTRY[(chiave, 1)].body)
        assert hashlib.sha256(sorgente.encode()).hexdigest()[:16] == attesa, chiave


def test_18_il_motore_il_dispatcher_il_cron_e_il_consenso_non_sono_cambiati():
    """Questa fase tocca una lettura e un contratto temporale. Il resto del
    diff deve essere vuoto, e lo si chiede a `git`."""
    intatti = ("communication/dispatcher.py", "communication/service.py",
               "communication/repository.py", "communication/journey_service.py",
               "communication/journey_repository.py", "communication/journey_catalog.py",
               "communication/templates.py", "communication/router.py",
               "run_communication_dispatch_cron.py", "consent/", "migrations/",
               "static/")
    # LA COLLISIONE CON "FLOW GLOBAL SECURITY", NOMINATA E NON AGGIRATA.
    #
    # Questa asserzione era "il diff e' vuoto", ed era giusta finche' P29-3G
    # era la fase in corso: nessun'altra mano stava scrivendo. Dopo il suo
    # commit il working tree appartiene alla fase successiva, e la prima che
    # e' arrivata tocca `static/` per una ragione sua - i due bottoni
    # Attiva/Disattiva di un'automazione, che da quando il catalogo globale ha
    # una porta rispondono 403 a un ruolo tenant.
    #
    # Si sottrae ESATTAMENTE cio' che quella fase dichiara, non l'intero
    # prefisso: ogni altro file sotto `static/`, `migrations/`, `consent/` e
    # gli otto moduli di communication resta soggetto all'asserzione di prima.
    # Un `intatti` accorciato avrebbe chiuso il test invece di aggiornarlo.
    from tests.flow_global_security_diff import (
        FILE_MODIFICATI as MOD_FGS, FILE_NUOVI as NUOVI_FGS)

    # A30-4 (collisione dichiarata, stessa forma): la UI Agenda tocca
    # `static/` - `main.js` (la rotta, senza voce in barra laterale) e
    # `app.css` (una sezione in coda). Si sottrae SOLO cio' che A30-4 dichiara.
    from tests.a30_4_diff import FILE_MODIFICATI as MOD_A30_4, FILE_NUOVI as NUOVI_A30_4

    diff = set(_git("diff", "--name-only", "--", *intatti).split())
    fuori = diff - MOD_FGS - NUOVI_FGS - MOD_A30_4 - NUOVI_A30_4
    assert fuori == set(), sorted(fuori)


def test_19_STOP_PRIORITY_e_le_fasi_del_tick_sono_intatte():
    from communication.journey_enums import STOP_PRIORITY
    from communication import journey_tick
    assert STOP_PRIORITY[0] == "mandate_signed" and STOP_PRIORITY[-1] == "operator"
    assert len(STOP_PRIORITY) == 11
    assert [nome for nome, _ in journey_tick.FASI] == ["stop", "advance", "enroll", "due"]


def test_20_i_file_toccati_sono_quelli_dichiarati_e_P29_2_0_resta_fuori():
    from tests.p29_3g_diff import FILE_MODIFICATI, FILE_NUOVI
    # FLOW GLOBAL SECURITY si dichiara allo stesso modo: l'unione cresce di
    # una fase, il verso del controllo no. Non e' una fase di P29 - e' il
    # catalogo globale di FLOW - ma questa sentinella guarda il working tree
    # intero, quindi la collisione c'e' e va nominata.
    from tests.flow_global_security_diff import (
        FILE_MODIFICATI as MOD_FGS, FILE_NUOVI as NUOVI_FGS)
    # A30-1 si dichiara allo stesso modo: l'unione cresce di una fase, il
    # verso del controllo no. Non e' una fase di P29 - e' l'Agenda CRM - ma
    # questa sentinella guarda il working tree intero.
    # SENTINELLA AGGIORNATA DA A30-2: l'Agenda si dichiara in due file
    # (A30-1 + A30-2), letti come un'unica fase.
    from tests.a30_1_diff import FILE_MODIFICATI as MOD_A30_1, FILE_NUOVI as NUOVI_A30_1
    from tests.a30_2_diff import FILE_MODIFICATI as MOD_A30_2, FILE_NUOVI as NUOVI_A30_2
    # SENTINELLA AGGIORNATA DA A30-2P: terza dichiarazione dell'Agenda.
    from tests.a30_2p_diff import FILE_MODIFICATI as MOD_A30_2P, FILE_NUOVI as NUOVI_A30_2P
    # SENTINELLA AGGIORNATA DAL MOUNT A30: quarta dichiarazione dell'Agenda.
    from tests.a30_mount_diff import FILE_MODIFICATI as MOD_A30_M, FILE_NUOVI as NUOVI_A30_M
    # A30-4: la UI Agenda (OS Shell), sesta dichiarazione.
    from tests.a30_4_diff import FILE_MODIFICATI as MOD_A30_4, FILE_NUOVI as NUOVI_A30_4
    NUOVI_A30 = NUOVI_A30_1 | NUOVI_A30_2 | NUOVI_A30_2P | NUOVI_A30_M | NUOVI_A30_4
    MOD_A30 = MOD_A30_1 | MOD_A30_2 | MOD_A30_2P | MOD_A30_M | MOD_A30_4

    righe = _git("status", "--porcelain").splitlines()
    nuovi = {r[3:].strip() for r in righe if r[:2].strip() in ("??", "A")}
    modificati = {r[3:].strip() for r in righe if r[:2].strip() not in ("??", "A")}
    tracciati = set(_git("ls-files").split())

    tutti_nuovi = FILE_NUOVI | NUOVI_FGS | NUOVI_A30
    tutti_modificati = FILE_MODIFICATI | MOD_FGS | MOD_A30 | tutti_nuovi
    assert nuovi - tutti_nuovi == {"P29_2_0_COMMUNICATION_DESIGN.md"}, \
        sorted(nuovi - tutti_nuovi)
    assert modificati <= tutti_modificati, sorted(modificati - tutti_modificati)
    for nome in FILE_NUOVI:
        assert (ROOT / nome).exists(), nome
    for nome in FILE_MODIFICATI:
        assert nome in tracciati and nome in modificati, nome
    assert "P29_2_0_COMMUNICATION_DESIGN.md" not in tracciati


def test_21_il_documento_di_design_non_e_stato_toccato():
    percorso = ROOT / "P29_2_0_COMMUNICATION_DESIGN.md"
    assert hashlib.md5(percorso.read_bytes()).hexdigest() == "37b3066fc5cf41b905964e1f31fe93f4"


def test_22_il_piano_di_ricertificazione_esiste_ed_e_minimale():
    """Tre prove sole: cio' che e' gia' certificato in P29-3F non si rifa'."""
    piano = ROOT / "docs" / "P29_3G_LIVE_RECERTIFICATION.md"
    testo = piano.read_text(encoding="utf-8")
    for atteso in ("LIVE TEST A", "LIVE TEST B", "LIVE TEST C", "409"):
        assert atteso in testo, atteso
    for da_non_rifare in ("provision", "cutoff", "unsubscribe", "cron"):
        assert da_non_rifare in testo, da_non_rifare
