"""P29-1.3 - il bypass del consenso, chiuso.

Fino a P29-1.2 il dominio `consent/` era il percorso GIUSTO, non l'UNICO:
`core/repository.py::update_contact` faceva un UPDATE cieco su qualunque
chiave ricevuta, quindi un PATCH sul contatto poteva cambiare
`contacts.marketing_consent` senza scrivere un evento in `consent_events`,
senza provenienza, senza attore e - poiche' solo `ContactCreate` derivava il
timestamp - spesso senza nemmeno una data. Era il rischio R2, dichiarato al
momento in cui e' stato introdotto.

Questo file prova che quella porta e' chiusa, che quelle accanto sono rimaste
aperte, e che l'unica scrittura di consenso ancora fuori dal dominio e' quella
dichiarata e rimandata a P29-1.4.

Nessun database: il repository viene esercitato con un falso in memoria, con la
stessa convenzione di tests/test_followup_repository.py e degli altri.

Mappa:

    M1  update_contact rifiuta i campi di consenso
    M2  gli aggiornamenti ordinari continuano a funzionare
    M3  create_contact non concede consenso
    M4  gli schemi HTTP non dichiarano piu' quei campi
    M5  il consent service continua a funzionare
    M6  non esistono altri write-site applicativi non autorizzati
    M7  la scheda contatto: consenso visibile, non modificabile
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path

import pytest

from core import repository as core_repository
from core import schemas as core_schemas
from core.scope import ProgrammingError

ROOT = Path(__file__).resolve().parents[1]

CONSENT_COLUMNS = (
    "marketing_consent",
    "marketing_consent_at",
    "marketing_revoked_at",
    "marketing_consent_source",
    "marketing_consent_notice_id",
    "privacy_terms_accepted",
    "privacy_terms_accepted_at",
    "privacy_terms_revoked_at",
    "privacy_terms_source",
    "privacy_terms_notice_id",
)


class Ctx:
    is_platform_admin = False

    def __init__(self, agency_id=1, role="agency_owner", user_id=10):
        self.agency_id = agency_id
        self.role = role
        self.user_id = user_id

    def require_agency(self):
        return self.agency_id


class FakeCursor:
    def __init__(self, log):
        self.log = log
        self.rows = []

    def execute(self, query, params=None):
        self.log.append((" ".join(str(query).split()), params))
        self.rows = [{"id": 1, "display_name": "Mario Rossi"}]

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


@pytest.fixture
def sql(monkeypatch):
    registro: list = []

    @contextmanager
    def fake_cursor(*, commit: bool = False):
        yield None, FakeCursor(registro)

    monkeypatch.setattr(core_repository, "core_cursor", fake_cursor)
    return registro


@pytest.fixture
def ctx():
    return Ctx()


# ===========================================================================
# M1  update_contact rifiuta i campi di consenso
# ===========================================================================
@pytest.mark.parametrize("colonna", CONSENT_COLUMNS)
def test_m1_update_contact_rifiuta_ogni_campo_di_consenso(sql, ctx, colonna):
    with pytest.raises(ProgrammingError) as errore:
        core_repository.update_contact(ctx, 1, {colonna: True})

    assert colonna in str(errore.value)
    assert "consent.service" in str(errore.value), (
        "il messaggio deve dire dove si scrive il consenso, non solo che qui non si scrive"
    )
    assert sql == [], "nessuna istruzione deve raggiungere il database"


def test_m1_il_rifiuto_arriva_prima_di_qualunque_scrittura(sql, ctx):
    """Anche mescolato a campi legittimi: si rifiuta tutto, non si scrive meta'."""
    with pytest.raises(ProgrammingError):
        core_repository.update_contact(
            ctx, 1, {"notes": "nota legittima", "marketing_consent": False}
        )
    assert sql == []


def test_m1_l_elenco_protetto_copre_tutte_le_colonne_di_proiezione():
    """L'elenco del repository e quello del dominio devono coincidere.

    Se P29 aggiungesse una colonna di proiezione senza metterla qui, quella
    colonna tornerebbe scrivibile da un PATCH generico senza che nessuno se ne
    accorga.
    """
    from consent.enums import PROJECTION_COLUMNS

    dal_dominio = {
        colonna
        for colonne in PROJECTION_COLUMNS.values()
        for colonna in colonne.values()
    }
    assert dal_dominio <= set(core_repository.CONSENT_OWNED_COLUMNS)
    assert set(CONSENT_COLUMNS) == set(core_repository.CONSENT_OWNED_COLUMNS)


# ===========================================================================
# M2  gli aggiornamenti ordinari continuano a funzionare
# ===========================================================================
def test_m2_un_update_ordinario_passa(sql, ctx):
    core_repository.update_contact(ctx, 1, {"notes": "richiamare lunedi"})

    assert len(sql) == 1
    istruzione, _params = sql[0]
    assert istruzione.startswith("UPDATE contacts c SET notes = %s")
    assert "agency_id = %s" in istruzione, "lo scoping resta quello di P26"


@pytest.mark.parametrize(
    "payload",
    [
        {"first_name": "Mario"},
        {"status": "inactive"},
        {"email": "m@example.it", "email_normalized": "m@example.it"},
        {"notes": None},
        {"archived_at": None},
    ],
)
def test_m2_nessuna_regressione_sui_campi_legittimi(sql, ctx, payload):
    core_repository.update_contact(ctx, 1, payload)
    assert len(sql) == 1


def test_m2_la_guardia_sullo_scope_e_ancora_li(sql, ctx):
    """P29-1.3 aggiunge una guardia, non sostituisce quella di P26."""
    with pytest.raises(ProgrammingError):
        core_repository.update_contact(ctx, 1, {"agency_id": 2})
    assert sql == []


# ===========================================================================
# M3  create_contact non concede consenso
# ===========================================================================
def test_m3_create_contact_rifiuta_i_campi_di_consenso(sql, ctx):
    with pytest.raises(ProgrammingError):
        core_repository.create_contact(
            ctx, {"contact_type": "person", "display_name": "Mario", "marketing_consent": True}
        )
    assert sql == []


def test_m3_un_contatto_nasce_senza_consenso(sql, ctx):
    core_repository.create_contact(
        ctx, {"contact_type": "person", "display_name": "Mario", "notes": None}
    )

    assert len(sql) == 1
    _istruzione, params = sql[0]
    assert params["marketing_consent"] is None
    assert params["marketing_consent_at"] is None, (
        "NULL e' `never_given`: il solo stato onesto per chi non ha deciso niente"
    )


# ===========================================================================
# M4  gli schemi HTTP non dichiarano piu' quei campi
# ===========================================================================
@pytest.mark.parametrize("modello", ["ContactCreate", "ContactUpdate"])
@pytest.mark.parametrize("colonna", CONSENT_COLUMNS)
def test_m4_gli_schemi_non_dichiarano_il_consenso(modello, colonna):
    campi = getattr(core_schemas, modello).__fields__
    assert colonna not in campi, (
        f"{modello} dichiara ancora {colonna}: un PATCH generico potrebbe "
        "cambiare il consenso senza scrivere un evento"
    )


@pytest.mark.parametrize("modello", ["ContactCreate", "ContactUpdate"])
def test_m4_extra_forbid_rende_il_rifiuto_esplicito(modello):
    """Senza `extra = "forbid"` il campo verrebbe IGNORATO in silenzio.

    Un client che continua a mandarlo - la scheda contatto della OS Shell lo
    fa ancora, fino a P29-1.7 - deve ricevere un errore che lo nomina, non un
    successo che non ha cambiato niente.
    """
    modello_cls = getattr(core_schemas, modello)
    config = getattr(modello_cls, "model_config", None) or getattr(modello_cls, "Config", None)
    extra = config.get("extra") if isinstance(config, dict) else getattr(config, "extra", None)
    assert str(extra).endswith("forbid"), extra

    with pytest.raises(Exception) as errore:
        modello_cls(contact_type="person", display_name="Mario", marketing_consent=True)
    assert "marketing_consent" in str(errore.value)


def test_m4_il_validatore_non_deriva_piu_il_timestamp():
    """Derivarlo qui significava scrivere una data senza un evento dietro.

    Si guarda l'assegnazione, non il testo: il docstring di ContactUpdate
    NOMINA quei campi per spiegare perche' non ci sono piu', ed e' giusto che
    lo faccia.
    """
    sorgente = (ROOT / "core" / "schemas.py").read_text(encoding="utf-8")
    assert 'values["marketing_consent_at"]' not in sorgente
    assert "datetime.utcnow()" not in sorgente


# ===========================================================================
# M5  il consent service continua a funzionare
# ===========================================================================
def test_m5_il_dominio_e_ancora_l_unico_scrittore_legittimo():
    from consent import repository as consent_repository

    sorgente = (ROOT / "consent" / "repository.py").read_text(encoding="utf-8")
    assert "UPDATE contacts c SET" in sorgente
    assert hasattr(consent_repository, "record_decision")


def test_m5_grant_e_revoke_restano_verdi():
    """La copertura vera e' in test_p29_1_consent_service.py: qui si prova solo
    che la chiusura di P29-1.3 non abbia rotto il percorso autorizzato."""
    from consent.enums import PROJECTION_COLUMNS, PURPOSE_MARKETING
    from consent.service import state_from_projection

    colonne = PROJECTION_COLUMNS[PURPOSE_MARKETING]
    concesso = {colonna: None for colonna in CONSENT_COLUMNS}
    concesso[colonne["flag"]] = True
    assert state_from_projection(concesso, PURPOSE_MARKETING)["status"] == "granted"

    revocato = {colonna: None for colonna in CONSENT_COLUMNS}
    revocato[colonne["flag"]] = False
    revocato[colonne["revoked_at"]] = "2026-09-15T10:00:00Z"
    assert state_from_projection(revocato, PURPOSE_MARKETING)["status"] == "revoked"


# ===========================================================================
# M6  non esistono altri write-site applicativi non autorizzati
# ===========================================================================
SORGENTI_APPLICATIVE = [
    percorso
    for percorso in ROOT.rglob("*.py")
    if ".venv" not in percorso.parts
    and "__pycache__" not in percorso.parts
    and "tests" not in percorso.parts
    and "migrations" not in percorso.parts
    and not percorso.name.startswith("run_")
    and "scripts" not in percorso.parts
]

# Gli unici file autorizzati a NOMINARE una colonna di consenso in scrittura.
#
#   consent/*                  il dominio: e' il suo mestiere
#   core/repository.py         la guardia che le vieta, e la INSERT che le
#                              azzera esplicitamente
#   core/service.py            il dizionario del bridge pubblico (vedi sotto)
#   database_revival/*         SOLE LETTURE (`marketing_consent IS TRUE`)
SCRITTORI_AUTORIZZATI = {
    "core/repository.py",
    "core/service.py",
}


def _relativo(percorso: Path) -> str:
    return percorso.relative_to(ROOT).as_posix()


def test_m6_nessun_altro_file_applicativo_scrive_le_colonne_di_consenso():
    colpevoli = []
    for percorso in SORGENTI_APPLICATIVE:
        relativo = _relativo(percorso)
        if relativo.startswith("consent/") or relativo in SCRITTORI_AUTORIZZATI:
            continue
        testo = percorso.read_text(encoding="utf-8")
        for colonna in CONSENT_COLUMNS:
            # Una scrittura SQL (`colonna = %s`, o la colonna dentro una INSERT)
            # oppure un'assegnazione Python su un dizionario di dati.
            if re.search(rf"{colonna}\s*=\s*%s", testo) or re.search(
                rf"[\"']{colonna}[\"']\s*:", testo
            ):
                colpevoli.append((relativo, colonna))
    assert not colpevoli, f"write-site di consenso non autorizzati: {colpevoli}"


def test_m6_le_letture_di_p24_restano_letture():
    """`database_revival` legge e non scrive: e' cio' che ne permette
    l'invarianza attraverso tutto P29."""
    for nome in ("eligibility.py", "repository.py", "service.py"):
        testo = (ROOT / "database_revival" / nome).read_text(encoding="utf-8")
        assert not re.search(r"marketing_consent\s*=\s*%s", testo), nome
        assert not re.search(r"UPDATE\s+contacts", testo, re.I), nome


def test_m6_il_bridge_pubblico_e_l_unica_eccezione_e_lo_dice():
    """L'eccezione dichiarata, e rimandata a P29-1.4.

    Il bridge scrive ancora il consenso nella propria INSERT. Non e' una
    dimenticanza: spostarlo sul dominio senza il resto di P29-1.4 romperebbe la
    creazione dei lead da stima360.it. Questo test pretende che l'eccezione
    resti UNA, e che il codice la dichiari invece di lasciarla intendere.
    """
    sorgente = (ROOT / "core" / "repository.py").read_text(encoding="utf-8")
    assert "bridge_public_stima" in sorgente
    assert "P29-1.4" in sorgente, (
        "l'eccezione del bridge va dichiarata nel file che la contiene"
    )

    # Nel repository ci sono esattamente due INSERT INTO contacts eseguibili:
    # quella generica (azzerata da P29-1.3) e quella del bridge. Si contano le
    # istruzioni - la parentesi che apre l'elenco delle colonne - e non le
    # occorrenze del testo, che compare anche nel docstring del modulo.
    assert len(re.findall(r"INSERT INTO contacts\s*\(", sorgente)) == 2

    servizio = (ROOT / "core" / "service.py").read_text(encoding="utf-8")
    inizio = servizio.index("def bridge_public_stima")
    assert '"marketing_consent"' in servizio[inizio:], (
        "il solo dizionario di core/service.py che nomina il consenso deve "
        "essere quello del bridge"
    )
    assert '"marketing_consent"' not in servizio[:inizio]


# ===========================================================================
# M7  la scheda contatto della OS Shell
#
# Chiudere il backend senza toccare la UI avrebbe lasciato un controllo che
# l'operatore puo' muovere e che risponde 422: una regressione introdotta da
# P29-1.3, non un limite ereditato. La correzione e' la piu' piccola possibile
# - il campo resta dov'e' e mostra quel che mostrava, ma non si tocca.
# ===========================================================================
CONTATTO_JS = ROOT / "static" / "os_shell" / "assets" / "views" / "contatto-dettaglio.js"


def _dialog_source() -> str:
    testo = CONTATTO_JS.read_text(encoding="utf-8")
    inizio = testo.index("function openEditContactDialog")
    fine = testo.index("// --- P25.2: Lead SELL")
    return testo[inizio:fine]


def test_m7_il_consenso_resta_visibile_nella_panoramica():
    """Non si toglie informazione: la panoramica continua a mostrarlo."""
    testo = CONTATTO_JS.read_text(encoding="utf-8")
    inizio = testo.index("function renderPanoramica")
    panoramica = testo[inizio:]
    assert "'Consenso marketing'" in panoramica
    assert "contact.marketing_consent" in panoramica


def test_m7_il_consenso_resta_visibile_nel_dialog_con_lo_stato_corrente():
    dialog = _dialog_source()
    assert 'name="marketing_consent"' in dialog, "il campo non e' stato rimosso"
    assert "currentConsent" in dialog, "mostra lo stato corrente, non un valore vuoto"
    for stato in ('<option value=""', '<option value="true"', '<option value="false"'):
        assert stato in dialog, f"lo stato {stato} non e' piu' rappresentabile"


def test_m7_il_consenso_non_e_modificabile():
    dialog = _dialog_source()
    select = dialog[dialog.index('name="marketing_consent"'):]
    select = select[: select.index("</select>")]
    assert " disabled" in select, (
        "un select abilitato che risponde 422 e' peggio di uno disabilitato: "
        "l'operatore scopre il divieto dopo aver perso il lavoro"
    )
    assert 'aria-disabled="true"' in select


def test_m7_il_payload_della_patch_non_contiene_mai_il_consenso():
    """Due ragioni indipendenti, e il test le pretende entrambe.

    `disabled` esclude il campo da FormData; l'assenza del codice che lo
    leggeva esclude anche il caso in cui qualcuno riabilitasse il controllo
    senza accorgersi del resto.
    """
    dialog = _dialog_source()
    assert "consentTarget" not in dialog
    assert "formData.get('marketing_consent')" not in dialog
    assert "payload.marketing_consent" not in dialog


def test_m7_gli_altri_campi_entrano_ancora_nel_payload():
    """La modifica e' chirurgica: tutto il resto del P25.4 e' intatto."""
    dialog = _dialog_source()
    assert "const payload = {};" in dialog
    for campo in (
        "contact_type", "status", "first_name", "last_name", "company_name",
        "display_name", "email", "phone", "secondary_phone", "source", "notes",
    ):
        assert f"'{campo}'" in dialog, f"{campo} non entra piu' nel payload"
    assert "if (!Object.keys(payload).length)" in dialog
    assert "apiPatch(`/api/core/contacts/${contact.id}`, payload)" in dialog


def test_m7_nessun_422_nel_normale_flusso_di_modifica():
    """I campi che il dialog puo' inviare sono tutti accettati da ContactUpdate.

    E' la prova che chiude il cerchio: con `extra = "forbid"`, un solo campo
    non dichiarato basterebbe a far fallire ogni salvataggio. Qui si confronta
    l'elenco che il browser puo' produrre con quello che il modello accetta.
    """
    dialog = _dialog_source()
    inviabili = set(re.findall(r"(?:textField|selectField)\('(\w+)'", dialog))
    assert inviabili, "nessun campo inviabile trovato: il parsing e' da rivedere"

    accettati = set(core_schemas.ContactUpdate.__fields__)
    non_accettati = inviabili - accettati
    assert not non_accettati, (
        f"il dialog puo' inviare campi che ContactUpdate rifiuta con 422: {sorted(non_accettati)}"
    )
    assert "marketing_consent" not in inviabili
