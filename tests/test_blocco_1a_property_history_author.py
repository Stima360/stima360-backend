"""BLOCCO-1A - l'autore dello storico prezzi e stati viene dalla sessione.

Offline. `property.repository.core_cursor` e' sostituito da un cursore che
registra ogni `(sql, params)`; le asserzioni leggono gli argomenti EFFETTIVI
delle INSERT su `property_price_history` e `property_status_history`.

Prima di BLOCCO-1A la PATCH di un immobile scriveva come autore il campo
`changed_by` del payload: il browser sceglieva chi aveva cambiato il prezzo
(la OS Shell non lo inviava, quindi NULL; il vecchio property_admin mandava
"property-admin"). La creazione non scriveva alcun autore.

Adesso l'autore e' l'operatore del contesto autenticato, nella forma P26-5
`operator:<user_id>` (operator_auth.dependencies.audit_actor). `changed_by`
resta ACCETTATO dallo schema per compatibilita' e non conta nulla.

Coverage map:

    A  PATCH: autore contraffatto o assente -> l'operatore reale (prezzo, stati)
    B  creazione: le righe iniziali portano l'operatore reale
    C  payload legacy di property_admin, attraverso il service
    D  valori invariati: nessuna riga di storico
    E  contesto mancante o senza operatore: nessuna attribuzione inventata
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from core.scope import ProgrammingError
from operator_auth.context import OperatorContext
from property import repository as property_repository
from property import service as property_service
from property.schemas import PropertyUpdate

AGENCY = 77
# Deliberatamente non 1: un operatore fisso passerebbe un test che usasse 1.
OPERATOR = 4242
EXPECTED = f"operator:{OPERATOR}"
FORGED = "operator:1"
PROPERTY_ID = 501

OLD_ROW = {
    "asking_price": 200000,
    "commercial_status": "draft",
    "classification": "A",
    "deleted_at": None,
    "title": "Titolo scritto a mano",
    "code": "IMM-501",
    "archived_at": None,
    "metadata": {},
}


def session_ctx(user_id=OPERATOR) -> OperatorContext:
    return OperatorContext(
        user_id=user_id,
        agency_id=AGENCY,
        role="agency_owner",
        is_platform_admin=False,
        session_id=9,
        auth_channel="operator_session",
    )


class RecordingCursor:
    """Risponde alla SELECT ... FOR UPDATE con la riga precedente, alle
    INSERT/UPDATE su `properties` con la riga scritta, a tutto il resto None."""

    def __init__(self, old=None, created=None):
        self.old = dict(OLD_ROW if old is None else old)
        self.created = created
        self.calls: list[tuple[str, object]] = []
        self._row = None

    def execute(self, query, params=None):
        squashed = " ".join(str(query).split())
        self.calls.append((squashed, params))
        lowered = squashed.lower()
        if lowered.startswith("select") and "for update" in lowered:
            self._row = dict(self.old)
        elif lowered.startswith("insert into properties"):
            self._row = dict(self.created)
        elif lowered.startswith("update properties"):
            self._row = {"id": PROPERTY_ID, "parent_property_id": None}
        else:
            self._row = None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []

    def history(self, table):
        return [c for c in self.calls if c[0].lower().startswith(f"insert into {table}")]


@pytest.fixture
def install(monkeypatch):
    state = {"opened": 0}

    def _install(**kwargs):
        cursor = RecordingCursor(**kwargs)

        @contextmanager
        def _core_cursor(*_args, **_kwargs):
            state["opened"] += 1
            yield (None, cursor)

        monkeypatch.setattr(property_repository, "core_cursor", _core_cursor)
        state["cursor"] = cursor
        return cursor

    state["install"] = _install
    return state


def _columns(sql):
    inside = sql[sql.index("(") + 1:sql.index(")")]
    return [c.strip().lower() for c in inside.split(",")]


def _value(call, column):
    """Il valore legato a `column` in una INSERT ... VALUES(%s,...).

    I letterali nella lista VALUES (es. 'commercial_status') non consumano un
    parametro: si mappano solo i segnaposto."""
    sql, params = call
    values_part = sql[sql.lower().index("values") + len("values"):]
    slots = [v.strip() for v in values_part.strip()[1:-1].split(",")]
    bound = iter(params)
    mapped = {}
    for name, slot in zip(_columns(sql), slots):
        mapped[name] = next(bound) if slot == "%s" else slot.strip("'")
    assert column in mapped, f"{column!r} non e' scritta: {sql}"
    return mapped[column]


# ---------------------------------------------------------------------------
# A - PATCH: l'autore e' l'operatore della sessione
# ---------------------------------------------------------------------------

def test_a_forged_changed_by_on_price_change_records_the_real_operator(install):
    cursor = install["install"]()
    property_repository.update_property(session_ctx(), PROPERTY_ID, {
        "asking_price": 210000, "change_reason": "ribasso concordato", "changed_by": FORGED,
    })

    [call] = cursor.history("property_price_history")
    assert _value(call, "changed_by") == EXPECTED
    assert _value(call, "old_price") == 200000
    assert _value(call, "new_price") == 210000
    assert _value(call, "change_reason") == "ribasso concordato"


def test_a_missing_changed_by_on_price_change_records_the_real_operator(install):
    """La OS Shell non invia `changed_by`: prima la riga restava senza autore."""
    cursor = install["install"]()
    property_repository.update_property(session_ctx(), PROPERTY_ID, {"asking_price": 210000})

    [call] = cursor.history("property_price_history")
    assert _value(call, "changed_by") == EXPECTED


def test_a_status_history_records_the_real_operator_and_keeps_the_note(install):
    cursor = install["install"]()
    property_repository.update_property(session_ctx(), PROPERTY_ID, {
        "commercial_status": "evaluation", "classification": "B",
        "history_note": "nota dell'operatore", "changed_by": FORGED,
    })

    calls = cursor.history("property_status_history")
    assert {_value(c, "field_name") for c in calls} == {"commercial_status", "classification"}
    for call in calls:
        assert _value(call, "changed_by") == EXPECTED
        assert _value(call, "note") == "nota dell'operatore"


# ---------------------------------------------------------------------------
# B - creazione: le righe iniziali gia' previste portano l'operatore
# ---------------------------------------------------------------------------

def test_b_create_initial_price_and_status_rows_record_the_real_operator(install):
    created = {"id": PROPERTY_ID, "code": "IMM-501", "asking_price": 250000,
               "commercial_status": "draft", "classification": "A"}
    cursor = install["install"](created=created)
    property_repository.create_property(session_ctx(), {"title": "Villa", "code": "IMM-501"})

    [price] = cursor.history("property_price_history")
    assert _value(price, "changed_by") == EXPECTED
    assert _value(price, "change_reason") == "initial price"
    statuses = cursor.history("property_status_history")
    assert {_value(c, "note") for c in statuses} == {"initial status", "initial classification"}
    for call in statuses:
        assert _value(call, "changed_by") == EXPECTED


# ---------------------------------------------------------------------------
# C - il payload legacy resta accettato, ma non decide l'autore
# ---------------------------------------------------------------------------

def test_c_legacy_property_admin_payload_is_accepted_and_ignored_as_author(install):
    """static/property_admin/assets/app.js manda `changed_by:'property-admin'`."""
    cursor = install["install"]()
    body = PropertyUpdate(asking_price=215000, change_reason="aggiornamento", changed_by="property-admin")
    property_service.update_property(session_ctx(), PROPERTY_ID, body)

    [call] = cursor.history("property_price_history")
    assert _value(call, "changed_by") == EXPECTED
    assert _value(call, "change_reason") == "aggiornamento"
    update = [c for c in cursor.calls if c[0].lower().startswith("update properties")]
    assert update and "changed_by" not in update[0][0].lower()


# ---------------------------------------------------------------------------
# D - nessun cambiamento, nessuna riga
# ---------------------------------------------------------------------------

def test_d_unchanged_price_and_statuses_write_no_history(install):
    cursor = install["install"]()
    property_repository.update_property(session_ctx(), PROPERTY_ID, {
        "asking_price": 200000, "commercial_status": "draft", "classification": "A",
        "changed_by": FORGED,
    })

    assert cursor.history("property_price_history") == []
    assert cursor.history("property_status_history") == []


# ---------------------------------------------------------------------------
# E - nessuna attribuzione inventata
# ---------------------------------------------------------------------------

def test_e_no_context_refuses_a_history_patch_before_any_write(install):
    """Il ramo senza ctx (nessun router lo usa) non ha un'identita' attendibile:
    prima avrebbe scritto il `changed_by` del chiamante. Ora rifiuta prima di
    aprire la transazione."""
    install["install"]()
    with pytest.raises(ProgrammingError):
        property_repository.update_property(PROPERTY_ID, {"asking_price": 1, "changed_by": FORGED})
    assert install["opened"] == 0


def test_e_context_without_operator_records_the_author_as_absent(install):
    """`user_id` None (forma legacy Basic, contesto di sistema) e' "assente",
    registrato come tale (operator_auth.context.OperatorContext): mai il
    valore del payload, mai un operatore inventato."""
    cursor = install["install"]()
    property_repository.update_property(session_ctx(user_id=None), PROPERTY_ID, {
        "asking_price": 210000, "commercial_status": "evaluation", "changed_by": FORGED,
    })

    for table in ("property_price_history", "property_status_history"):
        [call] = cursor.history(table)
        assert _value(call, "changed_by") is None
