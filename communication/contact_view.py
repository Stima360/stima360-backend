"""P29-3D - cio' che il Contact 360 mostra, e cio' che non mostra mai.

QUESTO MODULO E' UNA TRADUZIONE, NON UNA SECONDA VERITA'

Il ledger e le iscrizioni restano l'autorita'. Qui si prende quella verita' e
si sceglie COSA di essa arriva a uno schermo: le colonne operative - il token
di claim, la chiave di idempotenza, il testo grezzo di un errore del provider,
il conteggio dei tentativi - non escono. Non perche' siano segrete, ma perche'
non dicono niente a chi legge una scheda contatto e, messe in una pagina,
diventano la cosa che qualcuno un giorno incollera' in un ticket.

Una whitelist e non una blacklist: si nominano i campi che ESCONO. Cosi' una
colonna aggiunta domani al ledger non compare per errore in una risposta HTTP.

LE ETICHETTE SONO QUI E NON NEL DATABASE. `queued` resta `queued` nel ledger -
e' uno stato tecnico, e i test lo nominano - mentre l'italiano ("programmato")
vive in questo strato, che e' l'unico che parla a una persona.

SENZA LA 071 QUESTA PAGINA FUNZIONA LO STESSO. Lo storico dei messaggi esiste
dalla 064: si legge e si mostra. Le journey no, e la risposta lo dice con un
campo (`available: false`) invece di rompersi con un 500 - il codice puo'
arrivare in TEST prima della migration, e una scheda contatto che esplode
sarebbe la prima cosa che qualcuno nota.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any

from . import journey_repository as journey_repo
from . import journey_service
from . import service as comm_service
from . import unsubscribe
from .enums import (
    MODE_ASSISTED, MODE_AUTOMATIC, MODE_MANUAL, STATUS_QUEUED, TYPE_MARKETING,
    TYPE_SERVICE,
)
from .exceptions import ValidationError
from .journey_enums import (
    ENR_ACTIVE, ENR_PAUSED, KIND_AWAIT_OPERATOR, assisted_action_is_due,
)

#: Quanti caratteri del corpo finiscono nell'anteprima. Abbastanza per
#: riconoscere il messaggio, non abbastanza per leggerlo di sfuggita in una
#: lista: chi vuole il testo apre il messaggio.
ANTEPRIMA = 160

#: Le etichette dei motivi. `stima_pdf` e' la mail della stima, `m1..m5` sono
#: i passi della sequenza, tutto il resto e' un gesto di una persona.
ETICHETTE_MOTIVO = {
    "stima_pdf": "Stima", "m1": "M1", "m2": "M2", "m3": "M3", "m4": "M4", "m5": "M5",
    "operator_manual": "Manuale", "operator_reply": "Risposta operatore",
    "owner_login_link": "Accesso proprietario",
}

ETICHETTE_STATO = {
    "queued": "programmato", "sending": "in invio", "sent": "inviato",
    "failed": "fallito", "indeterminate": "esito incerto",
    "suppressed": "soppresso", "cancelled": "annullato",
}

ETICHETTE_MODO = {
    MODE_AUTOMATIC: "automatico", MODE_ASSISTED: "assistito", MODE_MANUAL: "manuale",
}

#: LA NOTA SULL'INBOUND. Le risposte in arrivo non sono modellate (P29-3A §F),
#: quindi la pagina non puo' dire "nessuna risposta": non lo sa. Dice cosa
#: sa, ed e' una frase sola.
NOTA_INBOUND = (
    "Le risposte ricevute non sono ancora sincronizzate automaticamente nel CRM."
)

#: Cio' che rende un corpo HTML riconoscibile: un tag di chiusura, o uno dei
#: tag che un'email costruita davvero contiene. Deliberatamente stretto: un
#: testo scritto a mano che dice "se a < b > c" non deve finire smontato.
#: Il `<` deve essere ATTACCATO al nome del tag: `<b>` e' HTML, `a < b > c`
#: e' aritmetica scritta da una persona, e la prima stesura di questa
#: espressione le confondeva - ammetteva lo spazio e smontava la frase.
_SEMBRA_HTML = re.compile(
    r"</[a-zA-Z]|<(?:br|div|p|span|table|tr|td|a|img|h[1-6]|ul|ol|li"
    r"|strong|b|em|i|hr|html|body|head|style|script)[\s/>]", re.IGNORECASE)

#: `<style>` e `<script>` non si tolgono come tag: si toglie anche cio' che
#: contengono. Levare solo i delimitatori lascerebbe il CSS nel testo, che e'
#: esattamente l'anteprima illeggibile da cui nasce questa correzione.
_BLOCCHI_MUTI = re.compile(r"<\s*(style|script)\b[\s\S]*?<\s*/\s*\1\s*>",
                           re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")

#: Un tag diventa uno SPAZIO e non il nulla, altrimenti `<b>a</b><b>b</b>`
#: diventerebbe `ab`. Lo spazio pero' non deve restare davanti alla
#: punteggiatura: `Ciao <b>Anna</b>, la stima` finirebbe "Anna , la stima".
_SPAZIO_PRIMA_DI_PUNTEGGIATURA = re.compile(r"\s+([,.;:!?])")


def _testo_leggibile(corpo: str) -> str:
    """Il corpo come lo legge una persona, per l'anteprima e per nient'altro.

    P29-3G. La mail della stima e' HTML, e l'anteprima ne mostrava i primi
    centosessanta caratteri cosi' com'erano: `<div style="font-family:Arial`
    - identico su ogni messaggio, quindi buono a distinguerne nessuno.

    `rendered_body` NON viene toccato: il ledger conserva cio' che e' stato
    spedito, parola per parola, e questa e' una lettura. Un corpo di testo
    puro attraversa questa funzione senza cambiare di una virgola: niente
    tag da togliere, e nessuna entita' da decodificare in un testo che
    nessuno ha codificato.
    """
    if not _SEMBRA_HTML.search(corpo):
        return corpo
    testo = _BLOCCHI_MUTI.sub(" ", corpo)
    testo = _TAG.sub(" ", testo)
    return _SPAZIO_PRIMA_DI_PUNTEGGIATURA.sub(r"\1", html.unescape(testo))


#: I CAMPI CHE ESCONO. Tutto il resto resta nel ledger.
def _messaggio_visibile(riga: dict[str, Any]) -> dict[str, Any]:
    corpo = (riga.get("rendered_body") or "").strip()
    # Prima leggibile, POI troncato: troncare per primo taglierebbe a meta'
    # un tag e lascerebbe l'anteprima piu' corta del previsto una volta
    # tolto il resto.
    anteprima = " ".join(_testo_leggibile(corpo).split())
    return {
        "id": riga["id"],
        "created_at": riga.get("created_at"),
        "scheduled_at": riga.get("scheduled_at"),
        "sent_at": riga.get("sent_at"),
        "channel": riga.get("channel"),
        "direction": riga.get("direction"),
        "communication_type": riga.get("communication_type"),
        "reason_code": riga.get("reason_code"),
        "reason_label": ETICHETTE_MOTIVO.get(riga.get("reason_code"), riga.get("reason_code")),
        "status": riga.get("status"),
        "status_label": ETICHETTE_STATO.get(riga.get("status"), riga.get("status")),
        "mode": riga.get("mode"),
        "mode_label": ETICHETTE_MODO.get(riga.get("mode"), riga.get("mode")),
        "subject": riga.get("subject_snapshot"),
        "preview": anteprima[:ANTEPRIMA],
        # Il prossimo invio ha senso SOLO se il messaggio e' ancora in coda:
        # su una riga gia' spedita `scheduled_at` e' storia, e mostrarla come
        # "prossimo invio" farebbe credere che partira' di nuovo.
        "next_send_at": riga.get("scheduled_at") if riga.get("status") == STATUS_QUEUED else None,
        "from_journey": riga.get("enrollment_id") is not None,
        "step_no": riga.get("step_no"),
        "can_cancel": riga.get("status") == STATUS_QUEUED,
        "can_send_now": riga.get("status") == STATUS_QUEUED,
    }


def messages(ctx, contact_id: int, *, limit: int = 50, cur=None) -> dict[str, Any]:
    """Lo storico del contatto, tradotto. Funziona anche senza la 071."""
    righe = comm_service.list_for_contact(ctx, contact_id, limit=limit, cur=cur)
    return {
        "contact_id": contact_id,
        "messages": [_messaggio_visibile(r) for r in righe],
        "inbound_note": NOTA_INBOUND,
    }


def _passo_visibile(passo: dict[str, Any] | None) -> dict[str, Any] | None:
    if passo is None:
        return None
    return {"step_no": passo["step_no"], "step_key": passo["step_key"],
            "mode": passo["default_mode"],
            "mode_label": ETICHETTE_MODO.get(passo["default_mode"], passo["default_mode"]),
            "reason_label": ETICHETTE_MOTIVO.get(passo["reason_code"], passo["reason_code"])}


def journey(ctx, contact_id: int, *, cur=None) -> dict[str, Any]:
    """La card dell'automazione: com'e' messa questa journey, adesso.

    `available: false` quando la 071 non c'e'; `enrollment: null` quando il
    contatto non ha nessuna journey aperta - e in quel caso la pagina mostra
    lo storico e basta, senza inventare automazioni che non esistono.
    """
    with journey_service.cursore(cur) as (_, c):
        if not journey_repo.schema_ready(c):
            return {"contact_id": contact_id, "available": False, "enrollment": None,
                    "automation": None}

        comm_service.repository.contact_in_scope(c, ctx, contact_id)
        controllo = journey_repo.select_control(c, ctx, contact_id)
        automazione = {
            "paused": bool(controllo and controllo["paused"]),
            "paused_at": controllo["paused_at"] if controllo else None,
            "pause_reason": controllo["pause_reason"] if controllo else None,
        }

        aperta = journey_repo.select_open_enrollment(c, ctx, contact_id)
        if aperta is None:
            return {"contact_id": contact_id, "available": True, "enrollment": None,
                    "automation": automazione}

        testata = journey_repo.select_journey(c, ctx, aperta["journey_id"])
        passi = journey_repo.list_steps(c, ctx, aperta["journey_id"])
        corrente = next((p for p in passi if p["step_no"] == aperta["next_step_no"]), None)
        # P29-3G. `in_attesa` dice che l'iscrizione ASPETTA una persona, ed e'
        # cio' che l'etichetta deve raccontare anche prima della scadenza.
        # `azionabile` dice un'altra cosa: che quella persona puo' agire
        # ADESSO. Confonderle e' il difetto trovato in P29-3F - due bottoni
        # offerti con una settimana di anticipo, uno che rispondeva 409 e
        # l'altro che saltava il passo sul serio. La decisione e' la stessa
        # che applicano i due service: una sola, in `journey_enums`.
        in_attesa = aperta["next_action_kind"] == KIND_AWAIT_OPERATOR
        azionabile = assisted_action_is_due(aperta, datetime.now(timezone.utc))
        return {
            "contact_id": contact_id,
            "available": True,
            "automation": automazione,
            "enrollment": {
                "id": aperta["id"],
                "journey_name": testata["name"],
                "journey_key": testata["journey_key"],
                "journey_version": testata["version"],
                "status": aperta["status"],
                "status_label": ("in attesa dell'agente" if in_attesa and
                                 aperta["status"] == ENR_ACTIVE
                                 else "in pausa" if aperta["status"] == ENR_PAUSED
                                 else "attiva"),
                "current_step": _passo_visibile(corrente),
                "next_action_at": aperta["next_action_at"],
                "awaiting_operator": in_attesa,
                "awaiting_since": aperta["awaiting_since"],
                "total_steps": len(passi),
                "can_pause": aperta["status"] == ENR_ACTIVE,
                "can_resume": aperta["status"] == ENR_PAUSED,
                "can_send_current": azionabile,
                "can_skip_current": azionabile,
            },
        }


def _contesto_manuale(c, ctx, contact_id: int) -> dict[str, Any]:
    anagrafica = journey_repo.rendering_context(c, ctx, [contact_id]).get(contact_id)
    if anagrafica is None:
        from .exceptions import NotFoundError
        raise NotFoundError("Risorsa non trovata")
    return anagrafica


def send_manual(ctx, contact_id: int, *, subject: str, body: str,
                communication_type: str = TYPE_MARKETING, cur=None) -> dict[str, Any]:
    """Un messaggio scritto da una persona, per questo contatto.

    IL DESTINATARIO NON ARRIVA DAL CLIENT. Si risolve dal contatto, nello
    scope: accettare un indirizzo dal browser vorrebbe dire permettere di
    mandare la posta di un'agenzia a chiunque, e nessuna schermata ha
    bisogno di quel potere.

    NESSUNA SCORCIATOIA SUL CONSENSO. Un messaggio `marketing` passa dal
    gate come tutti gli altri - lo interroga il dispatcher immediatamente
    prima di spedire - e questa funzione non lo aggira e non lo anticipa.
    Cio' che fa e' dirlo SUBITO a chi scrive, invece di lasciargli credere
    di aver mandato qualcosa che verra' soppresso: se il consenso manca, la
    richiesta viene rifiutata qui, con la ragione.

    Le automazioni in pausa NON bloccano un messaggio manuale: la pausa
    ferma cio' che parte da solo, non le persone.
    """
    from consent.guard import can_send_marketing

    oggetto = (subject or "").strip()
    testo = (body or "").strip()
    if not oggetto:
        raise ValidationError("subject is required")
    if not testo:
        raise ValidationError("body is required")
    if communication_type not in (TYPE_MARKETING, TYPE_SERVICE):
        raise ValidationError(f"communication_type must be {TYPE_SERVICE} or {TYPE_MARKETING}")

    utente = journey_service.operatore(ctx)
    with journey_service.cursore(cur) as (_, c):
        anagrafica = _contesto_manuale(c, ctx, contact_id)
        destinazione = (anagrafica.get("email") or "").strip()
        if not destinazione:
            raise ValidationError(
                "this contact has no email address: there is nowhere to send it")

        if communication_type == TYPE_MARKETING:
            decisione = can_send_marketing(ctx, contact_id)
            if not decisione.allowed:
                raise ValidationError(
                    f"marketing consent is not granted for this contact ({decisione.reason}): "
                    "the message was not queued")

        corpo = testo
        if communication_type == TYPE_MARKETING:
            # Anche un marketing scritto a mano porta il link di disiscrizione:
            # e' un obbligo del messaggio, non del template.
            corpo = (f"{testo}\n\n---\nSe non vuoi piu' ricevere questi messaggi: "
                     f"{unsubscribe.unsubscribe_url(_base(), ctx.require_agency(), contact_id)}\n")

        return comm_service.enqueue(
            ctx, contact_id=contact_id, channel="email",
            communication_type=communication_type, mode=MODE_MANUAL,
            reason_code="operator_manual", rendered_body=corpo,
            subject_snapshot=oggetto, destination_snapshot=destinazione,
            idempotency_key=f"manual:{utente}:{contact_id}:{_impronta(oggetto, testo)}",
            cur=c)


def _base() -> str:
    from .journey_tick import _base_pubblica
    return _base_pubblica()


def _impronta(oggetto: str, testo: str) -> str:
    """Una chiave stabile per lo STESSO messaggio: due click sul pulsante
    "invia" non devono produrre due email identiche al contatto. Un testo
    diverso e' un messaggio diverso, e passa."""
    import hashlib
    return hashlib.sha256(f"{oggetto}\n{testo}".encode()).hexdigest()[:24]
