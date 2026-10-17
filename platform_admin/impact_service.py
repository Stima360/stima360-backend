"""ADM-2 - anteprima delle conseguenze, per il Super Admin generale designato.

Prima di sospendere, archiviare o revocare un elemento della Rete, il Super
Admin legge cosa resta, quali accessi cessano, cosa puo' interrompersi e cosa
richiede una sua decisione. L'anteprima:

  * e' in SOLA LETTURA: la transazione e' `READ ONLY` e non viene committata;
    nessuna riga di audit operativa (l'ammissione e' gia' registrata);
  * conta solo le dipendenze che lo schema collega davvero; cio' che non si
    puo' rilevare e' dichiarato in `not_verified`, mai taciuto;
  * non dichiara mai un'operazione "sicura": le verifiche vere si ripetono, in
    transazione, al momento dell'operazione (ADM-1);
  * non cambia nulla: nessun trasferimento, nessuna destinazione dei lead.

Ogni voce e' `{key, label, count, note}`; `count` None = non rilevabile.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from . import (
    agencies_repository,
    aliases_repository,
    impact_repository as repo,
    operators_repository,
    super_admin,
    territories_repository,
)
from .database import platform_operation_cursor
from .enums import (
    AGENCY_NOT_FOUND_MESSAGE,
    ALIAS_NOT_FOUND_MESSAGE,
    ASSIGNMENT_NOT_FOUND_MESSAGE,
    OPERATOR_NOT_FOUND_MESSAGE,
)
from .exceptions import AgencyNotFound, AliasNotFound, OperatorNotFound, TerritoryAssignmentNotFound

NOTICE = ("Anteprima informativa, calcolata adesso in sola lettura. Non modifica nulla e non sostituisce le "
          "verifiche che il sistema ripete al momento dell'operazione. Nessun dato viene cancellato o trasferito.")
FREE_TEXT_NOTE = ("Richieste d'acquisto, abbinamenti, attivita' e task registrano l'incaricato in testo libero: "
                  "non sono collegati a un operatore e non sono contati.")
ROLE_LABELS = {"agency_owner": "Supervisori (titolari)", "agency_admin": "Direttori (amministratori)",
               "agent": "Agenti e collaboratori"}
RECENT = f"negli ultimi {repo.RECENT_DAYS} giorni"


def _v(key: str, label: str, count: int | None, note: str | None = None) -> dict[str, Any]:
    return {"key": key, "label": label, "count": count, "note": note}


def _get(counts: dict, table: str, field: str) -> int | None:
    row = counts.get(table)
    return None if row is None else int(row[field])


class _Anteprima:
    def __init__(self, kind: str, target: dict, scenario: str):
        self.body = {"kind": kind, "target": target, "scenario": scenario, "preserved": [], "access_lost": [],
                     "may_stop": [], "needs_decision": [], "not_verified": [], "notice": NOTICE}

    def add(self, section: str, key: str, label: str, count: int | None, note: str | None = None) -> None:
        if count is None:
            self.body["not_verified"].append(_v(key, label, None, "Dato non rilevabile in questo database."))
        else:
            self.body[section].append(_v(key, label, count, note))

    def note(self, section: str, key: str, label: str, note: str | None = None, count: int | None = None) -> None:
        self.body[section].append(_v(key, label, count, note))


@contextmanager
def _read_only():
    """Il cursore della piattaforma, in una transazione che il database stesso
    rende di sola lettura. Non committa mai (`platform_operation_cursor`)."""
    with platform_operation_cursor() as (_, cur):
        cur.execute("SET TRANSACTION READ ONLY")
        yield cur


# ---------------------------------------------------------------------------
# Agenzia
# ---------------------------------------------------------------------------

def agency_impact(agency_id: int) -> dict[str, Any]:
    with _read_only() as cur:
        agency = agencies_repository.get_agency(cur, agency_id)
        if agency is None:
            raise AgencyNotFound(AGENCY_NOT_FOUND_MESSAGE)
        present = repo.present_tables(cur, repo.AGENCY_TABLES)
        counts = repo.agency_data_counts(cur, agency_id, present)
        members = repo.agency_members(cur, agency_id)
        sessions = repo.agency_live_sessions(cur, agency_id)
        assignments = repo.agency_assignments(cur, agency_id)
        sharing = repo.agency_territorial_sharing(cur, agency_id) if "properties" in present else None
        system = repo.system_agency(cur)

    status = agency["status"]
    scenario = {"active": "suspend", "suspended": "archive"}.get(status, "already_archived")
    a = _Anteprima("agency", {"id": agency["id"], "name": agency["name"], "slug": agency["slug"],
                              "status": status}, scenario)
    is_system = system is not None and system["id"] == agency["id"]

    # dati conservati
    a.add("preserved", "properties", "Immobili", _get(counts, "properties", "live"),
          f"Nel Cestino: {_get(counts, 'properties', 'trash')}" if counts.get("properties") else None)
    a.add("preserved", "buildings", "Edifici", _get(counts, "buildings", "live"),
          f"Nel Cestino: {_get(counts, 'buildings', 'trash')}" if counts.get("buildings") else None)
    a.add("preserved", "contacts", "Contatti", _get(counts, "contacts", "live"),
          f"Nel Cestino: {_get(counts, 'contacts', 'trash')}" if counts.get("contacts") else None)
    a.add("preserved", "leads", "Lead", _get(counts, "leads", "live"))
    a.add("preserved", "buy_requests", "Richieste d'acquisto", _get(counts, "buy_requests", "live"))
    a.add("preserved", "appointments", "Appuntamenti (storico completo)", _get(counts, "appointments", "total"))
    a.add("preserved", "acquisitions", "Acquisizioni e incarichi", _get(counts, "acquisitions", "total"))
    a.note("preserved", "assignments", "Assegnazioni territoriali", count=len(assignments),
           note="Le righe restano invariate: la sospensione non le revoca e non le trasferisce.")
    a.note("preserved", "memberships", "Membership e ruoli",
           count=sum(int(m["total"]) for m in members), note="Restano registrate in ogni stato.")

    # accessi che cessano (solo se oggi l'agenzia e' attiva: sospesa, li ha gia' persi)
    if status == "active":
        for role, label in ROLE_LABELS.items():
            usable = sum(int(m["usable"]) for m in members if m["role"] == role and m["status"] == "active")
            if usable:
                a.note("access_lost", f"members_{role}", label, count=usable,
                       note="Non potranno piu' accedere finche' l'agenzia non torna attiva.")
        a.note("access_lost", "member_sessions", "Sessioni aperte dei membri", count=int(sessions["member_sessions"]),
               note="Vengono revocate: dopo una riattivazione serve un nuovo accesso.")
        if int(sessions["acting_sessions"]):
            a.note("access_lost", "acting_sessions", "Amministratori dentro l'agenzia",
                   count=int(sessions["acting_sessions"]), note="Escono dall'agenzia; la loro sessione resta valida.")

    # funzionalita' che possono interrompersi
    effective = [x for x in assignments if x["status"] == "active" and x["kind"] == "municipality"
                 and int(x["active_aliases"]) > 0]
    # ADM-3: un'agenzia SOSPESA continua a ricevere le stime dei suoi Comuni;
    # solo un'agenzia ARCHIVIATA non e' instradabile.
    if status == "active" and effective:
        a.note("preserved", "routing", "Comuni che ricevono le stime pubbliche", count=len(effective),
               note="Anche da sospesa i nuovi lead di questi Comuni restano a questa agenzia e la attendono; "
                    "i follow-up automatici sono in pausa fino alla riattivazione.")
    if status == "suspended" and effective:
        fallback = (f"all'agenzia di sistema «{system['name']}»" if system and not is_system
                    else "a nessuna agenzia di riserva attiva")
        a.note("needs_decision", "routing", "Comuni che ricevono ancora le stime pubbliche", count=len(effective),
               note=f"Archiviando, le nuove stime di questi Comuni andrebbero {fallback}: revoca o trasferisci "
                    "prima le assegnazioni.")
    a.add("may_stop", "recent_stime", f"Stime pubbliche ricevute {RECENT}", _get(counts, "stime", "recent"),
          "Indicatore del flusso di lead che l'agenzia riceve oggi.")
    if sharing is not None and status == "active":
        a.note("may_stop", "c1_visible", "Immobili di altre agenzie letti per competenza (C1)",
               count=int(sharing["visible_to_agency"]),
               note="Con l'agenzia sospesa non sono piu' leggibili da lei. Restano ai loro proprietari.")
    if sharing is not None:
        a.note("preserved", "c1_shared", "Immobili dell'agenzia letti da agenzie competenti (C1)",
               count=int(sharing["shared_with_others"]),
               note="Restano leggibili dall'agenzia territorialmente competente, come deciso.")

    # decisioni amministrative
    if is_system:
        a.note("needs_decision", "system_agency", "Agenzia di sistema",
               note="Non si sospende e non si archivia: regge i servizi centrali. L'operazione verrebbe rifiutata.")
    if status == "active":
        a.note("needs_decision", "archive_order", "Archiviazione",
               note="Un'agenzia si archivia solo dopo essere stata sospesa.")
    a.add("needs_decision", "upcoming_appointments", "Appuntamenti futuri", _get(counts, "appointments", "upcoming"),
          "Restano in agenda; chi li segue non potra' accedere finche' l'agenzia e' sospesa.")
    a.add("needs_decision", "open_acquisitions", "Acquisizioni aperte", _get(counts, "acquisitions", "open"))
    c2 = counts.get("property_territorial_change_requests")
    if c2 is not None:
        a.note("needs_decision", "c2_origin", "Proposte C2 pendenti su immobili dell'agenzia",
               count=int(c2["as_origin"]))
        a.note("needs_decision", "c2_competent", "Proposte C2 pendenti presentate dall'agenzia",
               count=int(c2["as_competent"]),
               note="Con l'agenzia non attiva l'approvazione verrebbe bloccata dalle verifiche di competenza.")
    else:
        a.add("needs_decision", "c2", "Proposte C2 pendenti", None)
    a.add("needs_decision", "building_deletions", "Richieste di eliminazione edifici pendenti",
          _get(counts, "building_deletion_requests", "pending"))
    service_note = ("Oggi non si fermano con la sospensione: il loro adeguamento e' previsto in ADM-3.")
    a.add("needs_decision", "booking_links", "Link di prenotazione pubblica attivi",
          _get(counts, "public_booking_links", "active"), service_note)
    a.add("needs_decision", "calendar_connections", "Calendari collegati",
          _get(counts, "calendar_connections", "connected"), service_note)
    a.add("needs_decision", "journeys", "Percorsi di comunicazione attivi",
          _get(counts, "communication_journeys", "active"), service_note)
    a.note("not_verified", "free_text", "Incarichi in testo libero", note=FREE_TEXT_NOTE)
    return a.body


# ---------------------------------------------------------------------------
# Operatore
# ---------------------------------------------------------------------------

def operator_impact(operator_user_id: int) -> dict[str, Any]:
    with _read_only() as cur:
        operator = operators_repository.get_operator(cur, operator_user_id)
        if operator is None:
            raise OperatorNotFound(OPERATOR_NOT_FOUND_MESSAGE)
        present = repo.present_tables(cur, repo.OPERATOR_TABLES)
        counts = repo.operator_data_counts(cur, operator_user_id, present)
        memberships = repo.operator_memberships(cur, operator_user_id)
        sessions = repo.operator_live_sessions(cur, operator_user_id)

    name = " ".join(x for x in (operator["first_name"], operator["last_name"]) if x) or operator["email"]
    designated = super_admin.is_designated(operator_user_id)
    a = _Anteprima("operator", {"id": operator["id"], "name": name, "status": operator["status"],
                                "is_platform_admin": operator["is_platform_admin"], "is_super_admin": designated,
                                "memberships": [{"agency_id": m["agency_id"], "agency_name": m["agency_name"],
                                                 "agency_status": m["agency_status"], "role": m["role"],
                                                 "status": m["status"]} for m in memberships]},
                   "disable" if operator["status"] == "active" else "already_inactive")

    keep = "Restano assegnati all'operatore: nessun trasferimento automatico."
    a.add("preserved", "contacts", "Contatti assegnati", _get(counts, "contacts", "assigned"), keep)
    a.add("preserved", "leads", "Lead assegnati", _get(counts, "leads", "assigned"), keep)
    a.add("preserved", "properties", "Immobili assegnati", _get(counts, "properties", "assigned"), keep)
    a.add("preserved", "acquisitions", "Acquisizioni assegnate", _get(counts, "acquisitions", "assigned"), keep)
    a.note("preserved", "memberships", "Membership", count=len(memberships),
           note="Restano registrate con il ruolo, in ogni stato.")
    a.note("preserved", "authorship", "Autore storico",
           note="Le operazioni gia' firmate dall'operatore (audit, eventi, proposte) restano a suo nome.")

    if operator["status"] == "active":
        a.note("access_lost", "login", "Accesso al CRM", note="L'operatore non potra' piu' accedere.")
        a.note("access_lost", "sessions", "Sessioni aperte", count=sessions,
               note="Vengono revocate: dopo una riattivazione serve un nuovo accesso.")
        a.add("may_stop", "booking_links", "Link di prenotazione pubblica attivi",
              _get(counts, "public_booking_links", "active"),
              "Smettono di proporre orari finche' l'operatore non e' di nuovo attivo.")
        a.add("may_stop", "calendar_connections", "Calendari collegati",
              _get(counts, "calendar_connections", "connected"), "La sincronizzazione si interrompe.")

    if designated:
        a.note("needs_decision", "super_admin", "Super Admin generale designato",
               note="Questo account non si disabilita: la piattaforma resterebbe senza amministratore. "
                    "L'operazione verrebbe rifiutata.")
    for m in memberships:
        if m["role"] == "agency_owner" and m["status"] == "active" and int(m["other_active_owners"]) == 0:
            a.note("needs_decision", f"sole_owner_{m['agency_id']}", f"Unico Supervisore di «{m['agency_name']}»",
                   count=int(m["agency_active_municipalities"]),
                   note="Comuni attivi presidiati dall'agenzia: senza un Supervisore attivo non si possono "
                        "presentare proposte territoriali C2. Valutare un trasferimento del titolare.")
    a.add("needs_decision", "upcoming_appointments", "Appuntamenti futuri assegnati",
          _get(counts, "appointments", "upcoming"), "Restano in agenda a suo nome: valutare una riassegnazione.")
    a.add("needs_decision", "open_acquisitions", "Acquisizioni aperte", _get(counts, "acquisitions", "open"))
    a.add("needs_decision", "c2_requested", "Proposte C2 pendenti presentate",
          _get(counts, "property_territorial_change_requests", "pending"))
    a.add("needs_decision", "building_deletions", "Richieste di eliminazione edifici pendenti",
          _get(counts, "building_deletion_requests", "pending"))
    a.note("not_verified", "free_text", "Incarichi in testo libero", note=FREE_TEXT_NOTE)
    return a.body


# ---------------------------------------------------------------------------
# Territorio
# ---------------------------------------------------------------------------

_TERRITORY_TABLES = ("stime", "properties", "property_territorial_change_requests")


def _routing_section(a: _Anteprima, *, effective: bool, values: list[str], counts: dict, system: dict | None,
                     agency_name: str | None, is_system: bool) -> None:
    if not values:
        a.note("may_stop", "routing", "Instradamento delle stime",
               note="Nessun valore di Comune dichiarato: oggi questo territorio non riceve stime pubbliche.")
    elif not effective:
        a.note("may_stop", "routing", "Instradamento delle stime",
               note="Oggi non e' attivo (assegnazione non attiva o agenzia archiviata): l'operazione non lo cambia.")
    else:
        fallback = (f"l'agenzia di sistema «{system['name']}» ({system['status']})" if system and not is_system
                    else "nessuna agenzia di riserva attiva")
        a.note("may_stop", "routing", "Instradamento delle stime", count=len(values),
               note=f"Le nuove stime per questo Comune smettono di arrivare a «{agency_name}» e vanno a {fallback}. "
                    "Nessuna destinazione viene cambiata automaticamente; i lead gia' ricevuti restano dove sono.")
    a.add("may_stop", "recent_stime", f"Stime pubbliche per questo Comune {RECENT}", counts["recent_stime"],
          "Tutte le agenzie: indica il flusso di lead coinvolto.")


def assignment_impact(agency_id: int, assignment_id: int) -> dict[str, Any]:
    with _read_only() as cur:
        assignment = territories_repository.get_assignment(cur, assignment_id, agency_id)
        if assignment is None:
            raise TerritoryAssignmentNotFound(ASSIGNMENT_NOT_FOUND_MESSAGE)
        territory = territories_repository.get_territory(cur, assignment["territory_id"])
        agency = agencies_repository.get_agency(cur, agency_id)
        present = repo.present_tables(cur, _TERRITORY_TABLES)
        aliases = repo.territory_aliases(cur, territory["id"])
        values = [x["match_value"] for x in aliases]
        counts = repo.comune_counts(cur, values, agency_id, present)
        c2 = repo.pending_c2_for_territory(cur, territory["id"], agency_id, present)
        others = repo.other_assignments(cur, territory["id"], assignment_id)
        system = repo.system_agency(cur)

    status = assignment["status"]
    a = _Anteprima("assignment", {
        "id": assignment["id"], "status": status, "agency_id": agency["id"], "agency_name": agency["name"],
        "agency_status": agency["status"], "territory_id": territory["id"], "territory_label": territory["label"],
        "territory_kind": territory["kind"], "aliases": values},
        {"active": "suspend_or_revoke", "suspended": "revoke"}.get(status, "already_revoked"))
    municipality = territory["kind"] == "municipality"
    # ADM-3: l'instradamento vale anche per un'agenzia sospesa; la lettura C1 no.
    effective = (status == "active" and agency["status"] in ("active", "suspended") and municipality and bool(values))
    c1_effective = status == "active" and agency["status"] == "active" and municipality
    is_system = system is not None and system["id"] == agency["id"]

    a.note("preserved", "assignment", "Riga dell'assegnazione",
           note="Resta nello storico: sospesa o revocata, mai cancellata.")
    a.add("preserved", "own_properties", "Immobili dell'agenzia in questo Comune", counts["own_properties"],
          "Restano all'agenzia: la competenza territoriale non sposta gli immobili.")
    if status != "revoked":
        _routing_section(a, effective=effective, values=values, counts=counts, system=system,
                         agency_name=agency["name"], is_system=is_system)
        if municipality:
            a.add("may_stop", "c1_visible", "Immobili di altre agenzie in questo Comune (C1)",
                  counts["other_properties"] if c1_effective else (0 if counts["other_properties"] is not None else None),
                  "Oggi letti dall'agenzia per competenza: con l'assegnazione non attiva non lo sono piu'.")
        a.add("needs_decision", "c2_pending", "Proposte C2 pendenti per questo territorio", c2,
              "L'approvazione verrebbe bloccata: la competenza non sarebbe piu' valida.")
    if not municipality:
        a.note("needs_decision", "kind", "Tipo di territorio",
               note="Non e' un Comune: routing delle stime e lettura territoriale C1 usano solo i Comuni.")
    for o in others:
        a.note("needs_decision", f"other_{o['id']}", f"Altra assegnazione {o['status']} per lo stesso territorio",
               note=f"Agenzia «{o['agency_name']}» ({o['agency_status']}). Una riattivazione andrebbe in conflitto.")
    if status == "active":
        a.note("needs_decision", "final", "Revoca", note="La revoca e' definitiva: per ridare il territorio "
                                                         "servira' una nuova assegnazione. La sospensione no.")
    return a.body


def alias_impact(alias_id: int) -> dict[str, Any]:
    with _read_only() as cur:
        alias = aliases_repository.get_alias(cur, alias_id)
        if alias is None:
            raise AliasNotFound(ALIAS_NOT_FOUND_MESSAGE)
        territory = territories_repository.get_territory(cur, alias["territory_id"])
        current = territories_repository.get_active_assignment(cur, territory["id"])
        agency = agencies_repository.get_agency(cur, current["agency_id"]) if current else None
        present = repo.present_tables(cur, _TERRITORY_TABLES)
        counts = repo.comune_counts(cur, [alias["match_value"]], agency["id"] if agency else None, present)
        system = repo.system_agency(cur)

    a = _Anteprima("alias", {
        "id": alias["id"], "match_value": alias["match_value"], "status": alias["status"],
        "territory_id": territory["id"], "territory_label": territory["label"],
        "agency_id": agency["id"] if agency else None, "agency_name": agency["name"] if agency else None,
        "agency_status": agency["status"] if agency else None},
        "revoke" if alias["status"] == "active" else "already_revoked")
    a.note("preserved", "alias", "Riga dell'alias", note="Resta nello storico come valore revocato.")
    if alias["status"] == "active":
        effective = (bool(agency) and agency["status"] in ("active", "suspended")
                     and territory["kind"] == "municipality")
        is_system = system is not None and agency is not None and system["id"] == agency["id"]
        _routing_section(a, effective=effective, values=[alias["match_value"]], counts=counts, system=system,
                         agency_name=agency["name"] if agency else None, is_system=is_system)
        if effective and agency["status"] == "active":
            a.add("may_stop", "c1_visible", "Immobili di altre agenzie con questo Comune (C1)",
                  counts["other_properties"],
                  f"Oggi letti da «{agency['name']}» per competenza tramite questo valore.")
        a.note("needs_decision", "final", "Revoca",
               note="La revoca e' definitiva: per riusare il valore servira' un alias nuovo.")
    return a.body
