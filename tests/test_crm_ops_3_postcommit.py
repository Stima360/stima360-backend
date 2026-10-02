"""CRM-OPS-3 post-commit - le tre cause reali emerse su TEST, senza database.

  RC-1  Elenco "Errore 500": il codice gira su un database SENZA la 081
        (`relation "acquisitions" does not exist`). Il router ora chiude in
        modo leggibile (503 + code), senza nascondere niente: un
        `UndefinedTable` di un'altra tabella resta un errore vero.
  RC-2  Selezione immobile nel dialog "Nuova acquisizione": il testo digitato
        non e' una selezione; il click lo e', con il suo id in evidenza;
        cambiare il testo azzera la scelta; una risposta vecchia non
        sovrascrive quella nuova.
  RC-3  "L'immobile non ha proprietari collegati" con il proprietario nel CRM:
        Acquisizioni e scheda Immobile leggono la STESSA fonte
        (`property_contacts` via GET /api/property/properties/{id}); il ruolo
        «Proprietario» sulla scheda del contatto (`contact_roles`) non e' un
        collegamento all'immobile. Qui si prova la parita' di contratto e il
        messaggio che spiega dove si collega.

Il giro su PostgreSQL vero (503 con la 081 assente, parita' con la scheda
Immobile) e' in `tests/test_crm_ops_3_acquisitions_postgres.py` (31-32).
"""
from __future__ import annotations

import json
import re

import pytest

from acquisitions import errors, router
from acquisitions import enums
from acquisitions.enums import OWNER_ROLES
from tests import test_crm_ops_3_acquisitions as base
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

ASSETS = base.ASSETS
VISTA = (ASSETS / "views" / "acquisizioni.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# RC-1 - router: la 081 assente e' un 503 leggibile, non un 500 muto
# ---------------------------------------------------------------------------

def _undefined_table(messaggio):
    psycopg2 = pytest.importorskip("psycopg2")
    return psycopg2.errors.UndefinedTable(messaggio)


@pytest.mark.parametrize("messaggio", [
    'relation "acquisitions" does not exist\nLINE 1: ...',       # elenco, scheda
    'relation "acquisition_events" does not exist',
    'column "acquisition_id" does not exist\nLINE 1: ...',       # creazione (lock_property)
])
def test_e01_oggetti_della_081_assenti_503_con_codice(messaggio):
    psycopg2 = pytest.importorskip("psycopg2")
    classe = psycopg2.errors.UndefinedColumn if "column" in messaggio else psycopg2.errors.UndefinedTable
    risposta = router._errore(classe(messaggio))
    assert risposta.status_code == 503
    corpo = json.loads(risposta.body)
    assert corpo == {"detail": errors.NOT_INSTALLED_MESSAGE, "code": errors.NOT_INSTALLED}
    assert "081" in corpo["detail"]


def test_e02_altre_tabelle_e_altri_errori_restano_errori_veri():
    psycopg2 = pytest.importorskip("psycopg2")
    with pytest.raises(Exception, match="appointments"):
        router._errore(_undefined_table('relation "appointments" does not exist'))
    with pytest.raises(Exception, match="column"):
        router._errore(psycopg2.errors.UndefinedColumn('column "lead_id" does not exist'))
    with pytest.raises(RuntimeError):
        router._errore(RuntimeError("qualunque altra cosa"))


def test_e03_la_ui_mostra_il_dettaglio_del_server_senza_elenco_vuoto():
    """Il 503 arriva con `detail`: api-client lo usa come messaggio e la
    vista lo mostra nell'error-box. Nessun fallback a elenco vuoto."""
    assert "Impossibile caricare le acquisizioni: ${escapeHtml(error.message)}" in VISTA
    assert "items = []" not in VISTA.split("Impossibile caricare le acquisizioni")[1][:400]


# ---------------------------------------------------------------------------
# RC-2 - selezione immobile (stub DOM eseguito)
# ---------------------------------------------------------------------------

node = base.node
IMMOBILE, SENZA = base.IMMOBILE, base.SENZA_PROPRIETARI
UN_SOLO = {**IMMOBILE, "id": 32, "code": "IMM-32", "contacts": [IMMOBILE["contacts"][0]]}
SOLO_INQUILINO = {**IMMOBILE, "id": 33, "code": "IMM-33", "contacts": [IMMOBILE["contacts"][2]]}

# Le rotte si cercano nell'ordine: queste vengono PRIMA di quelle di base.
DUE_RISULTATI = "\n".join([
    f'__route("GET", "/api/property/properties?", {json.dumps({"status": 200, "body": {"items": [IMMOBILE, SENZA]}})});',
    f'__route("GET", "/api/property/properties/32", {json.dumps({"status": 200, "body": UN_SOLO})});',
    f'__route("GET", "/api/property/properties/33", {json.dumps({"status": 200, "body": SOLO_INQUILINO})});',
])

SEL = r"""
const sel = () => q('#acq-property-selected');
const foto = () => ({ nascosto: sel().hidden === true, id: sel().dataset.propertyId || null,
                      testo: sel().textContent, owner: q('#acq-owner') ? q('#acq-owner').value : null,
                      opzioni: q('#acq-owner') ? opz(q('#acq-owner')) : [], noOwner: !!q('#acq-no-owner'),
                      prezzo: q('#acq-asking').value, risultati: q('#acq-property-results').querySelectorAll('[data-property-id]').length });
async function digita(testo) { const c = q('#acq-property-search'); c.value = testo; c.dispatch('input'); await wait(); await wait(); }
function clicca(id) { q('#acq-property-results').querySelectorAll('[data-property-id]').find((b) => b.dataset.propertyId === String(id)).dispatch('click'); }
"""


APRI = r"""
      await wait();
      bottone(C(), '+ Nuova acquisizione').dispatch('click'); await wait();
"""


def _run(staged, scenario, rotte=""):  # noqa: F811
    """Dall'elenco, con il pulsante "+ Nuova acquisizione" (nessun immobile
    preselezionato): la scelta parte dalla ricerca."""
    return base._run(staged, SEL + APRI + scenario, rotte + "\n" + base._rotte(), "#/acquisizioni")


@node
def test_f01_il_testo_digitato_non_e_una_selezione(staged):  # noqa: F811
    scenario = r"""
      await digita('trilo');
      const dopoTesto = foto();
      q('form').dispatch('submit'); await wait();
      report({ dopoTesto, errore: q('#acq-new-error').textContent, hint: q('#acq-property-hint').textContent });
    """
    out = _run(staged, scenario, DUE_RISULTATI)
    assert out["dopoTesto"]["risultati"] == 2                 # i risultati ci sono...
    assert out["dopoTesto"]["nascosto"] and out["dopoTesto"]["id"] is None   # ...ma nessuna scelta
    assert out["dopoTesto"]["opzioni"] == [] and out["dopoTesto"]["prezzo"] == ""
    assert out["errore"] == "Scegli l’immobile."
    assert "il testo da solo non seleziona" in out["hint"]
    assert base._scritture(out) == []


@node
def test_f02_il_click_seleziona_esattamente_quell_immobile(staged):  # noqa: F811
    scenario = r"""
      await digita('trilo');
      clicca(31); await wait(); await wait();
      const vuoto = foto();
      await digita('trilo');
      clicca(30); await wait(); await wait();
      const pieno = foto();
      report({ vuoto, pieno });
    """
    out = _run(staged, scenario, DUE_RISULTATI)
    v, p = out["vuoto"], out["pieno"]
    assert v["id"] == "31" and not v["nascosto"] and v["noOwner"] and v["opzioni"] == []
    assert p["id"] == "30" and not p["nascosto"] and not p["noOwner"]
    assert p["opzioni"] == ["41", "42"] and p["owner"] == "41"   # il principale e' preselezionato
    assert "Immobile selezionato" in p["testo"] and "IMM-30" in p["testo"]
    assert p["prezzo"] == "180000.00" and p["risultati"] == 0    # scelto: i risultati si chiudono


@node
def test_f03_cambiare_il_testo_o_l_immobile_azzera_la_scelta_vecchia(staged):  # noqa: F811
    scenario = r"""
      await digita('trilo'); clicca(30); await wait(); await wait();
      const prima = foto();
      const c = q('#acq-property-search'); c.value = 'trilox'; c.dispatch('input'); await wait(0);
      const subito = foto();               // azzerato PRIMA che arrivi la ricerca nuova
      await wait(); await wait();
      q('form').dispatch('submit'); await wait();
      const errore = q('#acq-new-error').textContent;
      clicca(30); await wait(); await wait();
      q('#acq-property-change').dispatch('click'); await wait();
      const dopoCambia = foto();
      report({ prima, subito, errore, dopoCambia, ricerca: q('#acq-property-search').value });
    """
    out = _run(staged, scenario, DUE_RISULTATI)
    assert out["prima"]["id"] == "30" and out["prima"]["prezzo"] == "180000.00"
    s = out["subito"]
    assert s["nascosto"] and s["id"] is None and s["opzioni"] == [] and s["prezzo"] == ""
    assert out["errore"] == "Scegli l’immobile."
    d = out["dopoCambia"]
    assert d["nascosto"] and d["id"] is None and d["opzioni"] == [] and out["ricerca"] == ""
    assert base._scritture(out) == []


@node
def test_f04_la_risposta_vecchia_non_sovrascrive_quella_nuova(staged):  # noqa: F811
    scenario = r"""
      // il dettaglio del 31 arriva DOPO quello del 30, anche se chiesto prima
      const vero = globalThis.fetch;
      globalThis.fetch = async (url, options) => {
        const r = await vero(url, options);
        if (url === '/api/property/properties/31') await new Promise((ok) => setTimeout(ok, 60));
        return r;
      };
      await digita('trilo');
      clicca(31); await wait(0);
      await digita('trilo');
      clicca(30); await wait(); await wait();
      const presto = foto();
      await wait(120); await wait();
      const tardi = foto();
      q('form').dispatch('submit'); await wait(); await wait();
      report({ presto, tardi, hash: window.location.hash });
    """
    out = _run(staged, scenario, DUE_RISULTATI)
    for f in (out["presto"], out["tardi"]):
        assert f["id"] == "30" and f["opzioni"] == ["41", "42"] and not f["noOwner"], f
    # e il payload porta il 30, non il 31 arrivato in ritardo
    dettagli = [c["url"] for c in out["calls"] if re.fullmatch(r"/api/property/properties/\d+", c["url"])]
    assert dettagli == ["/api/property/properties/31", "/api/property/properties/30"]


@node
def test_f05_un_solo_proprietario_proposto_e_il_payload_lo_porta(staged):  # noqa: F811
    # Il dialog e' aperto direttamente sull'immobile 32 (rotta `nuova/{id}`),
    # come dal pulsante della scheda Immobile.
    scenario = r"""
      await wait(); await wait();
      const scatto = foto();
      q('form').dispatch('submit'); await wait(); await wait();
      const titolo = dlg() ? dlg().querySelector('[data-title]').textContent : null;
      report({ f: scatto, titolo });
    """
    out = base._run(staged, SEL + scenario, DUE_RISULTATI + "\n" + base._rotte(), "#/acquisizioni/nuova/32")
    f = out["f"]
    assert f["id"] == "32" and f["opzioni"] == ["41"] and f["owner"] == "41" and not f["noOwner"]
    assert out["titolo"] == "Appuntamento di acquisizione"    # si passa all'Agenda
    assert base._scritture(out) == []                          # ...senza scrivere


@node
def test_f06_solo_ruoli_non_proprietari_bloccato_con_il_messaggio_che_spiega(staged):  # noqa: F811
    """RC-3: l'inquilino e' in property_contacts ma non e' un proprietario;
    il messaggio dice DOVE si collega il proprietario."""
    scenario = r"""
      await wait(); await wait();
      const scatto = foto();
      q('form').dispatch('submit'); await wait();
      report({ f: scatto, avviso: q('#acq-no-owner').textContent, link: q('#acq-no-owner').querySelector('a').getAttribute('href'),
               errore: q('#acq-new-error').textContent });
    """
    out = base._run(staged, SEL + scenario, DUE_RISULTATI + "\n" + base._rotte(), "#/acquisizioni/nuova/33")
    assert out["f"]["id"] == "33" and out["f"]["noOwner"] and out["f"]["opzioni"] == []
    assert "scheda Immobile → Proprietari" in out["avviso"]
    assert "ruolo «Proprietario» sulla scheda del contatto non basta" in out["avviso"]
    assert "Collega contatto" in out["avviso"] and out["link"] == "#/immobili/33"
    assert "non ha proprietari" in out["errore"]
    assert base._scritture(out) == []


# ---------------------------------------------------------------------------
# UX finale - «Continua: fissa appuntamento» governato dallo stato reale
# ---------------------------------------------------------------------------

# Dettaglio 34: errore del server (per H08).
ERRORE_DETTAGLIO = f'__route("GET", "/api/property/properties/34", {json.dumps({"status": 500, "body": {"detail": "Immobile non disponibile"}})});'

CONT = r"""
const cont = () => q('#acq-new-next').disabled === true;
"""


def _h(staged, scenario, rotte=DUE_RISULTATI, hash="#/acquisizioni"):  # noqa: F811
    apri = APRI if hash == "#/acquisizioni" else "\n      await wait(); await wait();\n"
    return base._run(staged, SEL + CONT + apri + scenario, rotte + "\n" + ERRORE_DETTAGLIO + "\n"
                     + base._rotte(), hash)


def test_h00_una_sola_fonte_per_disabled():
    """`disabled` di «Continua» si scrive in UN solo punto (aggiornaContinua);
    il submit ricontrolla comunque immobile, proprietari e proprietario."""
    assert len(re.findall(r"#acq-new-next'\)\.disabled\s*=", VISTA)) == 1
    assert 'id="acq-new-next" disabled>' in VISTA
    corpo = VISTA.split("function aggiornaContinua()")[1].split("\n  }\n")[0]
    for condizione in ("stato.property", "stato.owners.length > 0", "scelto.value",
                       "stato.owners.some("):
        assert condizione in corpo, condizione
    invio = VISTA.split("addEventListener('submit'")[1][:900]
    assert "if (!stato.property)" in invio and "if (!stato.owners.length)" in invio
    assert "stato.owners.some(" in invio


@node
def test_h01_all_apertura_disabilitato(staged):  # noqa: F811
    out = _h(staged, "report({ c: cont() });")
    assert out["c"] is True


@node
def test_h02_solo_testo_e_risultati_non_cliccati_disabilitato(staged):  # noqa: F811
    scenario = r"""
      await digita('trilo');
      report({ c: cont(), risultati: foto().risultati });
    """
    out = _h(staged, scenario)
    assert out["risultati"] == 2 and out["c"] is True


@node
def test_h03_immobile_senza_proprietari_e_durante_il_caricamento_disabilitato(staged):  # noqa: F811
    scenario = r"""
      const vero = globalThis.fetch;
      globalThis.fetch = async (url, options) => {
        const r = await vero(url, options);
        if (url === '/api/property/properties/30') await new Promise((ok) => setTimeout(ok, 60));
        return r;
      };
      await digita('trilo'); clicca(30); await wait(0);
      const caricando = cont();
      await wait(120); await wait();
      const caricato = cont();
      await digita('trilo'); clicca(31); await wait(); await wait();
      report({ caricando, caricato, senza: cont(), noOwner: foto().noOwner });
    """
    out = _h(staged, scenario)
    assert out["caricando"] is True                 # dettaglio non ancora arrivato
    assert out["caricato"] is False
    assert out["senza"] is True and out["noOwner"] is True


@node
def test_h04_immobile_con_un_proprietario_valido_abilitato(staged):  # noqa: F811
    out = _h(staged, "report({ c: cont(), f: foto() });", hash="#/acquisizioni/nuova/32")
    assert out["f"]["id"] == "32" and out["f"]["owner"] == "41" and out["c"] is False
    # con piu' proprietari il principale e' preselezionato: attivo anche li'
    out = _h(staged, "report({ c: cont(), f: foto() });", hash="#/acquisizioni/nuova/30")
    assert out["f"]["opzioni"] == ["41", "42"] and out["f"]["owner"] == "41" and out["c"] is False


@node
def test_h05_cambia_immobile_disabilita(staged):  # noqa: F811
    scenario = r"""
      await digita('trilo'); clicca(30); await wait(); await wait();
      const prima = cont();
      q('#acq-property-change').dispatch('click'); await wait();
      report({ prima, dopo: cont() });
    """
    out = _h(staged, scenario)
    assert out["prima"] is False and out["dopo"] is True


@node
def test_h06_modificare_il_testo_dopo_la_selezione_disabilita(staged):  # noqa: F811
    scenario = r"""
      await digita('trilo'); clicca(30); await wait(); await wait();
      const prima = cont();
      const c = q('#acq-property-search'); c.value = 'trilox'; c.dispatch('input'); await wait(0);
      const subito = cont();
      await wait(); await wait();
      report({ prima, subito, dopo: cont() });
    """
    out = _h(staged, scenario)
    assert out["prima"] is False and out["subito"] is True and out["dopo"] is True


@node
def test_h07_la_risposta_vecchia_non_riabilita(staged):  # noqa: F811
    """Due corse: (a) 30 scelto, poi 31 senza proprietari mentre il 30 e'
    ancora in volo; (b) 30 in volo e l'utente riscrive il testo. In entrambe
    la risposta tardiva del 30 non deve riabilitare «Continua»."""
    scenario = r"""
      const vero = globalThis.fetch;
      globalThis.fetch = async (url, options) => {
        const r = await vero(url, options);
        if (url === '/api/property/properties/30') await new Promise((ok) => setTimeout(ok, 60));
        return r;
      };
      await digita('trilo'); clicca(30); await wait(0);
      await digita('trilo'); clicca(31); await wait(); await wait();
      await wait(120); await wait();
      const a = { c: cont(), id: foto().id, noOwner: foto().noOwner };
      await digita('trilo'); clicca(30); await wait(0);
      const c = q('#acq-property-search'); c.value = 'altro'; c.dispatch('input');
      await wait(120); await wait();
      const b = { c: cont(), id: foto().id, nascosto: foto().nascosto, opzioni: foto().opzioni };
      report({ a, b });
    """
    out = _h(staged, scenario)
    assert out["a"] == {"c": True, "id": "31", "noOwner": True}
    assert out["b"] == {"c": True, "id": None, "nascosto": True, "opzioni": []}


@node
def test_h08_errore_del_dettaglio_disabilita(staged):  # noqa: F811
    scenario = r"""
      await digita('trilo'); clicca(30); await wait(); await wait();
      const prima = cont();
      // un risultato che punta al 34, il cui dettaglio risponde 500
      q('#acq-property-results').innerHTML = '';
      await digita('trilo');
      const b = q('#acq-property-results').querySelectorAll('[data-property-id]')[0];
      b.dataset.propertyId = '34';
      b.dispatch('click'); await wait(); await wait();
      report({ prima, dopo: cont(), errore: q('#acq-new-error').textContent, f: foto() });
    """
    out = _h(staged, scenario)
    assert out["prima"] is False
    assert out["dopo"] is True and out["errore"] == "Immobile non disponibile"
    assert out["f"]["nascosto"] and out["f"]["id"] is None


@node
def test_h09_h10_proprietario_valido_abilita_estraneo_disabilita(staged):  # noqa: F811
    scenario = r"""
      const sceltaOwner = (v) => { const s = q('#acq-owner'); s.value = v; s.dispatch('change'); };
      sceltaOwner('42'); await wait();
      const valido = cont();
      // (lo stub, a differenza del browser, ripiega sulla prima opzione con
      // value '': il caso "nessuna scelta" lo copre il submit qui sotto)
      sceltaOwner('999'); await wait();          // non e' nella lista corrente
      const estraneo = cont();
      q('form').dispatch('submit'); await wait();
      const errore = q('#acq-new-error').textContent;
      sceltaOwner('41'); await wait();
      report({ valido, estraneo, errore, dinuovo: cont(), dialogAgenda: !!(dlg() && dlg()._open) });
    """
    out = _h(staged, scenario, hash="#/acquisizioni/nuova/30")
    assert out["valido"] is False                      # H09
    assert out["estraneo"] is True                     # H10
    assert out["errore"] == "Scegli il proprietario principale."   # e il submit lo ferma comunque
    assert out["dinuovo"] is False and out["dialogAgenda"] is False
    assert base._scritture(out) == []
    assert not [c for c in out["calls"] if c["url"].startswith("/api/appointments/agents")]


# ---------------------------------------------------------------------------
# RC-3 - parita' di contratto con la scheda Immobile (lettura del codice)
# ---------------------------------------------------------------------------

def test_g01_stessa_fonte_della_scheda_immobile():
    """Acquisizioni legge lo stesso GET della scheda Immobile e filtra
    `contacts` (= property_contacts) per gli stessi ruoli del backend."""
    scheda = (ASSETS / "views" / "immobile-dettaglio.js").read_text(encoding="utf-8")
    assert "apiGet(`/api/property/properties/${encodeURIComponent(id)}`)" in VISTA
    assert "renderProprietari(property.contacts" in scheda
    assert "const OWNER_ROLES = ['owner', 'seller'];" in VISTA
    assert tuple(OWNER_ROLES) == ("owner", "seller")
    # il backend decide sugli stessi ruoli, dalla stessa tabella
    repo = (base.PACCHETTO / "repository.py").read_text(encoding="utf-8")
    assert "FROM property_contacts pc" in repo and "pc.role = ANY(%s)" in repo
    servizio = (base.PACCHETTO / "service.py").read_text(encoding="utf-8")
    assert "repository.property_owners(cur, agency_id, property_id, OWNER_ROLES)" in servizio
    # e nessuno dei due deduce il proprietario dai ruoli del CONTATTO
    for testo in (VISTA, repo, servizio):
        assert "contact_roles" not in testo.replace("(contact_roles)", "")


def test_g02_la_scheda_contatto_non_mostra_il_ruolo_come_collegamento():
    """Il ruolo in contact_roles e' un attributo del contatto; gli immobili
    collegati nella scheda Contatto vengono da Contact360.properties
    (property_contacts), non dal ruolo."""
    scheda = (ASSETS / "views" / "contatto-dettaglio.js").read_text(encoding="utf-8")
    assert "mai dedotti dalla presenza di lead/buy_request/immobili" in scheda
    assert "p.contacts (ruolo reale in property_contacts)" in scheda


# ---------------------------------------------------------------------------
# AUDIT ACQUISIZIONE -> INCARICO: la UI prima e dopo la conversione
# ---------------------------------------------------------------------------

DETTAGLIO = ASSETS / "views" / "acquisizione-dettaglio.js"
APERTA_AL_SOPRALLUOGO = dict(status="inspection_done", status_label="Sopralluogo effettuato",
                             allowed_actions={"edit": True, "reassign": True,
                                              "transitions": ["valuation_presented", "mandate_negotiation"],
                                              "lost": True, "new_appointment": False, "mandate": True})
ACQUISITA = dict(status="acquired", status_label="Acquisita", version=3,
                 acquired_at="2026-10-02T10:00:00+02:00",
                 property={**base._acquisizione()["property"], "acquisition_id": 501,
                           "commercial_status": "mandate", "mandate_type": "Esclusiva",
                           "mandate_start": "2026-10-02", "mandate_end": "2027-03-31"},
                 allowed_actions={"edit": False, "reassign": False, "transitions": [],
                                  "lost": False, "new_appointment": False, "mandate": False},
                 events=[*base._acquisizione()["events"],
                         {"id": 2, "event_type": "mandate_created", "from_status": "inspection_done",
                          "to_status": "acquired", "occurred_at": "2026-10-02T10:00:00+02:00",
                          "actor_name": "Giorgio"}])


def _rotte_conversione():
    """Prima GET: aperta al sopralluogo; dopo il POST /mandate: acquisita."""
    rt = base.a30_5._rt()
    prima, dopo = base._acquisizione(**APERTA_AL_SOPRALLUOGO), base._acquisizione(**ACQUISITA)
    return "\n".join([
        f'__route("GET", "/api/acquisitions/501", {json.dumps(rt.ok(prima))}, {json.dumps(rt.ok(dopo))});',
        f'__route("POST", "/api/acquisitions/501/mandate", {json.dumps(rt.ok(dopo))});',
    ]) + "\n" + base._rotte()


@node
def test_i01_genera_incarico_solo_negli_stati_ammessi(staged):  # noqa: F811
    """La CTA segue `allowed_actions.mandate` del server, stato per stato."""
    scenario = "await wait(); await wait(); report({ cta: !!C().querySelector('#acq-mandate-btn'), stato: C().querySelector('.acq-status') ? C().querySelector('.acq-status').textContent : C().textContent.slice(0, 0) });"
    casi = {
        "appointment_set": (False, {}),
        "inspection_done": (True, APERTA_AL_SOPRALLUOGO),
        "valuation_presented": (True, {**APERTA_AL_SOPRALLUOGO, "status": "valuation_presented",
                                       "status_label": "Valutazione presentata"}),
        "mandate_negotiation": (True, {**APERTA_AL_SOPRALLUOGO, "status": "mandate_negotiation",
                                       "status_label": "Trattativa incarico"}),
        "acquired": (False, ACQUISITA),
        "lost": (False, dict(status="lost", status_label="Persa", lost_reason="other_agency",
                             lost_reason_label="Altra agenzia", lost_at="2026-10-02T10:00:00+02:00",
                             allowed_actions={"edit": False, "reassign": False, "transitions": [],
                                              "lost": False, "new_appointment": False, "mandate": False})),
    }
    for stato, (attesa, extra) in casi.items():
        out = base._run(staged, scenario, base._rotte(acq=base._acquisizione(**extra)), "#/acquisizioni/501")
        assert out["cta"] is attesa, stato
        assert ("Genera incarico" in out["content"]) is attesa, stato
        assert base._scritture(out) == []


@node
def test_i02_dopo_l_incarico_la_scheda_si_ricarica_dal_server(staged):  # noqa: F811
    """Click -> dialog -> POST con il corpo giusto -> GET di ricarica -> la
    scheda mostra Acquisita, l'incarico, lo storico, e niente piu' azioni."""
    scenario = r"""
      await wait(); await wait();
      const primaCta = !!C().querySelector('#acq-mandate-btn');
      C().querySelector('#acq-mandate-btn').dispatch('click'); await wait();
      const dataProposta = q('#acq-mandate-start').value;
      q('#acq-mandate-type').value = 'Esclusiva';
      q('#acq-mandate-end').value = '2027-03-31';
      q('#acq-mandate-price').value = '185000.00';
      q('form').dispatch('submit'); await wait(); await wait(); await wait();
      const pulsanti = C().querySelectorAll('button').map((b) => b.textContent.trim());
      report({ primaCta, dataProposta, pulsanti, dialogAperto: !!D(),
               successo: C().querySelector('.success-box') ? C().querySelector('.success-box').textContent : null });
    """
    out = base._run(staged, scenario, _rotte_conversione(), "#/acquisizioni/501")
    assert out["primaCta"] is True
    # la data proposta e' il giorno di Roma, non quello UTC
    import subprocess as sp
    oggi_roma = sp.run([base.a30_5.NODE, "-e",
                        "console.log(new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Rome'}).format(new Date()))"],
                       capture_output=True, text=True).stdout.strip()
    assert out["dataProposta"] == oggi_roma
    scritture = base._scritture(out)
    assert [c["url"] for c in scritture] == ["/api/acquisitions/501/mandate"]
    assert scritture[0]["body"] == {"version": 2, "mandate_type": "Esclusiva",
                                    "mandate_start": oggi_roma, "mandate_end": "2027-03-31",
                                    "agreed_price": "185000.00"}
    letture = [c["url"] for c in out["calls"] if c["m"] == "GET" and c["url"] == "/api/acquisitions/501"]
    assert len(letture) == 2                                   # caricamento + ricarica dopo il POST
    assert out["successo"] == "Incarico generato."
    assert out["dialogAperto"] is False
    testo = out["content"]
    for atteso in ("Acquisita", "Esclusiva", "Acquisita il", enums.EVENT_LABELS_IT["mandate_created"], "Sopralluogo effettuato → Acquisita", "Apri immobile"):
        assert atteso in testo, atteso
    for vietato in ("Genera incarico", "Segna come persa", "Modifica", "Valutazione presentata"):
        assert vietato not in out["pulsanti"], vietato
    assert "Genera incarico" not in testo


@node
def test_i03_elenco_e_filtro_acquisita(staged):  # noqa: F811
    riga = {**base.RIGA_ELENCO, "status": "acquired", "status_label": "Acquisita",
            "appointment_status": "completed"}
    rotte = (f'__route("GET", "/api/acquisitions?", {json.dumps(base.a30_5._rt().ok({"items": [riga]}))});\n'
             + base._rotte())
    scenario = r"""
      await wait();
      const stato = C().querySelector('#acq-status');
      const etichette = stato.querySelectorAll('option').map((o) => o.textContent);
      stato.value = 'acquired'; stato.dispatch('change'); await wait();
      report({ etichette });
    """
    out = base._run(staged, scenario, rotte, "#/acquisizioni")
    assert "Acquisita" in out["content"] and "Acquisita" in out["etichette"]
    elenchi = [c["url"] for c in out["calls"] if c["m"] == "GET" and c["url"].startswith("/api/acquisitions?")]
    assert "statuses=acquired" in elenchi[-1]
    assert base._scritture(out) == []


@node
def test_i04_scheda_immobile_dopo_la_conversione(staged):  # noqa: F811
    """Panoramica: stato commerciale, incarico con tipo e date, origine con
    il link all'acquisizione, modifica ammessa; 'Mandato' non e' offerto nel
    selettore degli stati se l'immobile non ha origine."""
    immobile = {**base.IMMOBILE, "acquisition_id": 501, "commercial_status": "mandate",
                "mandate_type": "Esclusiva", "mandate_start": "2026-10-02", "mandate_end": "2027-03-31"}
    scenario = r"""
      await wait(); await wait();
      const sez = C().querySelector('#incarico-origin');
      report({ origine: sez ? sez.textContent : null, link: sez ? sez.querySelector('a').getAttribute('href') : null,
               modifica: !!C().querySelector('#incarico-edit-btn'),
               ctaNuova: !!C().querySelector('#incarico-acquisition-cta') });
    """
    out = base._run(staged, scenario, base._rotte(immobile=immobile), "#/immobili/30")
    testo = out["content"]
    for atteso in ("Stato commerciale", "Mandato", "Tipo incarico", "Esclusiva", "Data inizio",
                   "02/10/2026", "Data scadenza", "31/03/2027", "Origine"):
        assert atteso in testo, atteso
    assert out["link"] == "#/acquisizioni/501" and out["modifica"] is True and out["ctaNuova"] is False
    assert base._scritture(out) == []


def test_i05_il_selettore_stati_non_offre_mandato_senza_origine():
    from importlib import util  # noqa: F401
    testo = (ASSETS / "views" / "immobile-dettaglio.js").read_text(encoding="utf-8")
    assert "return MANUAL_COMMERCIAL_STATUSES.filter((s) => s !== 'mandate' || conMandato);" in testo
    assert "const conMandato = Boolean(p && (p.acquisition_id || p.commercial_status === 'mandate'));" in testo
    # e il corpo del POST /mandate porta solo i campi del contratto
    assert "mandate_type: String(valori.mandate_type || '').trim(), mandate_start: valori.mandate_start" in \
        DETTAGLIO.read_text(encoding="utf-8")
    assert "romeDateKey(new Date())" in DETTAGLIO.read_text(encoding="utf-8")
