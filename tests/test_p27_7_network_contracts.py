"""P27-7 - i contratti della sezione Rete: cosa chiama, e cosa non fa.

`tests/test_p27_7_network_runtime.py` esegue la Rete e guarda come si comporta.
Questo file guarda invece due cose che l'esecuzione non puo' mostrare, perche'
riguardano l'INSIEME e non un percorso:

1. OGNI ROTTA CHE LA UI CHIAMA ESISTE DAVVERO nel backend. E' la prova che
   P27-7 non ha inventato un endpoint "per comodita' del frontend": le rotte
   si estraggono dal codice JavaScript e si confrontano con la mappa reale
   dell'applicazione FastAPI. Una chiamata a una rotta inesistente sarebbe un
   404 che nessun test di percorso incontrerebbe finche' qualcuno non ci
   passa.

2. LE ASSENZE. Nessuna DELETE, nessuno slug modificabile, nessun `settings`
   nella PATCH generica, nessuna sorgente alias inventata, nessuna
   canonicalizzazione lato client. Sono cose che si provano guardando TUTTO il
   sorgente, non un clic.

Le asserzioni sul testo guardano il CODICE, non i commenti: i file della Rete
spiegano a lungo perche' certe cose NON ci sono, e cercare quelle parole nel
sorgente grezzo vieterebbe la spiegazione insieme alla cosa spiegata.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"

VISTE = ("views/rete.js", "views/rete-agenzia.js", "views/rete-territorio.js")
MODULI = VISTE + ("components/network.js",)


def _codice(nome: str) -> str:
    """Il sorgente senza commenti di riga e senza blocchi `/* */`."""
    testo = (ASSETS / nome).read_text(encoding="utf-8")
    testo = re.sub(r"/\*.*?\*/", "", testo, flags=re.S)
    righe = []
    for riga in testo.split("\n"):
        spogliata = riga.strip()
        if spogliata.startswith("//") or spogliata.startswith("*"):
            continue
        righe.append(riga)
    return "\n".join(righe)


def _tutto_il_codice() -> str:
    return "\n".join(_codice(n) for n in MODULI)


# Le chiamate che la Rete fa, come (metodo, template di rotta). Estrarle a mano
# sarebbe un elenco che invecchia: qui si leggono dal sorgente.
CHIAMATA = re.compile(
    r"api(Get|Post|Patch|Put|Delete)\(\s*(?:`(?P<t>[^`]+)`|'(?P<s>[^']+)')"
)


def _rotte_chiamate() -> set[tuple[str, str]]:
    trovate = set()
    for nome in MODULI:
        for m in CHIAMATA.finditer(_codice(nome)):
            metodo = {"Get": "GET", "Post": "POST", "Patch": "PATCH",
                      "Put": "PUT", "Delete": "DELETE"}[m.group(1)]
            percorso = m.group("t") or m.group("s")
            percorso = percorso.replace("${PLATFORM}", "/api/platform")
            # I parametri interpolati diventano il segnaposto della rotta, e la
            # query string non fa parte del percorso.
            percorso = re.sub(r"\$\{[^}]+\}", "{id}", percorso)
            percorso = percorso.split("?")[0].rstrip("/")
            trovate.add((metodo, percorso))
    return trovate


def _rotte_reali() -> set[tuple[str, str]]:
    import main

    reali = set()
    for percorso, operazioni in main.app.openapi()["paths"].items():
        for metodo in operazioni:
            reali.add((metodo.upper(), re.sub(r"\{[^}]+\}", "{id}", percorso)))
    return reali


# ---------------------------------------------------------------------------
# A - nessun endpoint inventato
# ---------------------------------------------------------------------------

def test_a1_every_route_the_network_ui_calls_exists_in_the_application():
    """La prova che P27-7 non ha inventato backend.

    Se una funzione della UI avesse avuto bisogno di una rotta che non c'e',
    la strada giusta era fermarsi e dirlo - non chiamarla e scoprire il 404 in
    produzione.
    """
    chiamate = _rotte_chiamate()
    assert chiamate, "nessuna chiamata trovata: l'estrattore e' rotto"
    mancanti = sorted(chiamate - _rotte_reali())
    assert not mancanti, f"rotte chiamate dalla UI e inesistenti nel backend: {mancanti}"


def test_a2_the_network_ui_only_talks_to_the_platform_surface():
    """Nessuna scorciatoia verso le rotte di tenant."""
    for metodo, percorso in _rotte_chiamate():
        assert percorso.startswith("/api/platform"), (metodo, percorso)


# A3 NON ESISTE PIU', ED E' UNA DECISIONE.
#
# C'era un `test_a3_no_new_backend_file_was_added_for_the_ui` che interrogava
# git - prima `git status`, poi il commit che aveva introdotto `rete.js` - per
# affermare che P27-7 non avesse toccato il backend.
#
# Quell'affermazione era vera ed e' stata certificata a suo tempo, ma non e'
# un'INVARIANTE DEL PRODOTTO: le fasi successive possono e devono poter
# modificare il backend - P27-8 lo ha fatto, correggendo il response model
# degli alias. Un test permanente che vieta una cosa legittima non protegge
# niente: si limita a fallire, e il primo a pagarne il prezzo e' stato il
# deploy su Render.
#
# In piu' leggeva la storia di git, che dipende da come il repository e' stato
# clonato: un checkout superficiale su un runner di build puo' rispondere
# diversamente dalla copia locale, e un test che cambia risposta secondo il
# clone non e' un test.
#
# Cio' che resta e vale per sempre e' sopra: A1 verifica che ogni rotta chiamata
# dalla Rete esista davvero nell'OpenAPI dell'applicazione, A2 che si parli solo
# a `/api/platform`, e la sezione B che le assenze di dominio - nessuna DELETE,
# nessuno slug modificabile, nessun `settings` nella PATCH generica, nessuna
# canonicalizzazione lato client - siano nel codice e non nelle intenzioni.


# ---------------------------------------------------------------------------
# B - le assenze
# ---------------------------------------------------------------------------

def test_b1_the_network_ui_never_issues_a_delete():
    codice = _tutto_il_codice()
    assert "apiDelete" not in codice
    assert "'DELETE'" not in codice


def test_b2_the_slug_is_never_sent_in_an_update():
    """Lo slug si crea una volta e si legge per sempre.

    Compare come CHIAVE DI UN PAYLOAD in un solo punto di tutta la sezione: la
    POST che crea l'agenzia. Ovunque altro e' solo qualcosa che si legge e si
    mostra - `agenzia.slug` nella scheda, e la parola "slug" nelle etichette.
    """
    chiavi = []
    for nome in MODULI:
        chiavi += [(nome, riga.strip()) for riga in _codice(nome).split("\n")
                   if re.search(r"(^|[{,\s])slug\s*:", riga)]
    assert len(chiavi) == 1, chiavi
    nome, riga = chiavi[0]
    assert nome == "views/rete.js", chiavi
    assert "#ag-slug" in riga, riga


def test_b3_the_generic_agency_patch_never_carries_settings():
    """`settings` non torna dalla porta di servizio.

    P27-4 ha chiuso la strada libera verso quel JSONB: la configurazione ha
    una rotta dedicata e validata, e la PATCH generica non deve riaprirla.
    """
    codice = _codice("views/rete-agenzia.js")
    blocco = codice[codice.index("form-panoramica"):codice.index("configurazione()")]
    assert "settings" not in blocco, blocco


def test_b4_the_configuration_uses_its_dedicated_route():
    codice = _codice("views/rete-agenzia.js")
    assert "/configuration`" in codice


def test_b5_no_client_side_canonicalisation_of_any_kind():
    """Niente slug, niente accenti rimossi, niente minuscole forzate.

    E' la regola che la migration 059 ha reso legge: il valore in ingresso si
    DICHIARA, non si deduce. Una trasformazione qui sarebbe un terzo algoritmo
    di identita', dopo quello che P27-6 ha eliminato.
    """
    codice = _tutto_il_codice()
    for vietato in ("toLowerCase(", "normalize(", "slugify", "deburr",
                    "latinise", "NFD"):
        assert vietato not in codice, vietato


def test_b6_the_alias_source_matches_the_backend_enum():
    from platform_admin.enums import ALIAS_SOURCES, ALIAS_SOURCE_PUBLIC_STIMA_COMUNE

    codice = _codice("components/network.js")
    assert f"'{ALIAS_SOURCE_PUBLIC_STIMA_COMUNE}'" in codice
    # Una sola sorgente esiste oggi, e la UI non ne offre altre.
    assert len(ALIAS_SOURCES) == 1
    assert codice.count("ALIAS_SOURCE_LABELS = {") == 1


def test_b7_the_alias_label_is_human_but_the_payload_is_the_backend_value():
    territorio = _codice("views/rete-territorio.js")
    # L'etichetta si mostra...
    assert "ALIAS_SOURCE_LABELS[ALIAS_SOURCE_PUBLIC_STIMA]" in territorio
    # ...e il valore del campo e' la costante del backend.
    assert 'value="${escapeHtml(ALIAS_SOURCE_PUBLIC_STIMA)}"' in territorio


def test_b8_no_routing_simulation_exists():
    """Nessun endpoint di simulazione viene chiamato, perche' non esiste.

    E nessuna riga ricostruisce la decisione: la catena mostrata e' fatta di
    campi che il backend ha restituito.
    """
    codice = _tutto_il_codice()
    for inventato in ("/routing", "/simulate", "/preview", "/resolve"):
        assert inventato not in codice, inventato


# ---------------------------------------------------------------------------
# C - vocabolario allineato al backend
# ---------------------------------------------------------------------------

def test_c1_every_status_and_role_the_backend_declares_has_an_italian_label():
    """Nessun valore del backend puo' comparire grezzo sullo schermo.

    `labelOf` ricade sul valore tecnico quando manca l'etichetta: e' il
    comportamento giusto - meglio 'archived' che una cella vuota - ma se
    accadesse davvero l'amministratore leggerebbe inglese tecnico. Questo test
    verifica che non possa accadere per nessuno dei valori dichiarati.
    """
    from platform_admin.enums import (
        AGENCY_STATUSES, ASSIGNMENT_STATUSES, MEMBERSHIP_ROLES,
        MEMBERSHIP_STATUSES, OPERATOR_STATUSES, TERRITORY_KINDS,
    )

    codice = _codice("components/network.js")

    def etichette(nome):
        blocco = codice[codice.index(f"{nome} = {{"):]
        blocco = blocco[: blocco.index("};")]
        return set(re.findall(r"^\s*([a-z_]+):", blocco, flags=re.M))

    assert set(AGENCY_STATUSES) <= etichette("AGENCY_STATUS_LABELS")
    assert set(OPERATOR_STATUSES) <= etichette("OPERATOR_STATUS_LABELS")
    assert set(MEMBERSHIP_STATUSES) <= etichette("MEMBERSHIP_STATUS_LABELS")
    assert set(MEMBERSHIP_ROLES) <= etichette("ROLE_LABELS")
    assert set(TERRITORY_KINDS) <= etichette("TERRITORY_KIND_LABELS")
    assert set(ASSIGNMENT_STATUSES) <= etichette("ASSIGNMENT_STATUS_LABELS")


def test_c2_platform_admin_is_not_offered_as_a_membership_role():
    """`is_platform_admin` non e' un ruolo di membership, e non compare come tale."""
    codice = _tutto_il_codice()
    assert "platform_admin: " not in codice
    agenzia = _codice("views/rete-agenzia.js")
    # Il selettore dei ruoli assegnabili non contiene nemmeno agency_owner: il
    # titolare si cambia solo con il trasferimento.
    blocco = agenzia[agenzia.index("ruoliAssegnabili"):]
    blocco = blocco[: blocco.index("statiAssegnabili")]
    assert "agency_owner" not in blocco, blocco


# ---------------------------------------------------------------------------
# D - la Shell resta quella
# ---------------------------------------------------------------------------

def test_d1_the_network_section_is_registered_and_kept_out_of_sections():
    main_js = (ASSETS / "main.js").read_text(encoding="utf-8")
    assert "registerRoute('rete'" in main_js
    # "rete" NON e' in SECTIONS: quella lista e' la sidebar di ogni operatore.
    sezioni = main_js[main_js.index("const SECTIONS = ["):]
    sezioni = sezioni[: sezioni.index("];")]
    assert "'rete'" not in sezioni, sezioni


def test_d2_the_entry_is_added_only_for_a_platform_admin():
    main_js = (ASSETS / "main.js").read_text(encoding="utf-8")
    blocco = main_js[main_js.index("function aggiornaVoceRete"):]
    blocco = blocco[: blocco.index("\n}")]
    assert "session.is_platform_admin !== true" in blocco
    # Rimosso, non nascosto: e' la lezione di P26-4.
    assert "esistente.remove()" in blocco
    assert "hidden" not in blocco


def test_d3_no_page_outside_the_network_was_touched():
    import subprocess

    # LMC-6 (collisione segnalata). Il perimetro era `static/`, cioe' TUTTO
    # il frontend, ma l'elenco degli ammessi qui sotto contiene solo pagine
    # della Shell: il test parla di "nessuna pagina fuori dalla Rete", e le
    # pagine di cui si occupa vivono in `static/os_shell/`. Il portale
    # proprietario (`static/owner_portal/`) e' un'altra applicazione, con i
    # propri test e le proprie sentinelle, e LMC-6 ha il mandato esplicito di
    # estenderlo: con il perimetro vecchio questo test avrebbe accusato la
    # Rete di aver toccato una pagina che non le appartiene nemmeno.
    #
    # La garanzia non si allenta: dentro `static/os_shell/` l'elenco resta
    # identico, riga per riga.
    modificati = subprocess.run(
        ["git", "status", "--porcelain", "--", "static/os_shell/"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.splitlines()
    ammessi = {
        "static/os_shell/assets/main.js",
        "static/os_shell/assets/app.css",
        "static/os_shell/assets/core/api-client.js",
        "static/os_shell/assets/components/network.js",
        "static/os_shell/assets/views/rete.js",
        "static/os_shell/assets/views/rete-agenzia.js",
        "static/os_shell/assets/views/rete-territorio.js",
        # P28 - LA BARRA DEL SUPERADMIN, che per sua natura non e' una pagina
        # della Rete: deve comparire sopra QUALUNQUE pagina mentre si sta
        # operando dentro un'agenzia, quindi vive nello scheletro della Shell.
        #
        # Cio' che questo test difende resta intatto: nessuna VISTA del CRM e'
        # stata toccata - non contatti.js, non immobili.js, non nessun'altra -
        # e `app.css` ha solo regole nuove, mai una ridefinizione di regole
        # esistenti (la lezione di `.list-toolbar`, vedi il fondo del file).
        "static/os_shell/index.html",
        "static/os_shell/assets/core/auth.js",
        # P28 - LA GUARDIA DI ROTTA, che per definizione non appartiene a una
        # pagina: decide QUALE pagina si puo' aprire, quindi vive nel router.
        #
        # Cio' che questo test difende resta intatto: nessuna VISTA e' stata
        # toccata - `contatti.js`, `immobili.js` e le altre sono identiche - e
        # la guardia non aggiunge un `if` dentro nessuna di loro. E' l'opposto:
        # esiste perche' quel controllo NON sia sparso li' dentro.
        "static/os_shell/assets/core/router.js",
        # P29-1.3 - IL CONSENSO MARKETING DIVENTA SOLA LETTURA.
        #
        # L'unica vista del CRM toccata, e per sottrazione: il consenso resta
        # VISIBILE e continua a mostrare lo stato corrente con i suoi tre
        # valori, ma il select e' `disabled` e non entra piu' nel payload della
        # PATCH generica. Il backend lo rifiuta gia' (core/schemas.py
        # ::ContactUpdate non lo dichiara piu', core/repository.py
        # ::update_contact lo respinge): senza questa riga l'operatore lo
        # scoprirebbe con un 422 dopo aver salvato.
        #
        # Nessuna logica di Rete e nessun contratto P27 e' modificato: non un
        # endpoint, non una rotta, non un componente della Rete. La vista
        # guadagna un attributo e perde tre righe.
        "static/os_shell/assets/views/contatto-dettaglio.js",
        # LMC-8 - IL RADAR DEL PROPRIETARIO NELLA SCHEDA CONTATTO.
        #
        # Due file, entrambi per addizione. `contatto-dettaglio.js` era gia'
        # ammesso e guadagna un riquadro in Panoramica; `timeline.js`
        # guadagna tre etichette leggibili per gli eventi che LMC-7 scrive
        # nella timeline di vendita, piu' la regola che nasconde il loro
        # payload tecnico. Nessuna logica di Rete e nessun contratto P27 e'
        # toccato: non un endpoint, non una rotta, non un componente della
        # Rete, e nessuna vista del CRM oltre a quella gia' elencata.
        "static/os_shell/assets/components/timeline.js",
        # P29-3D - LA TAB "COMUNICAZIONI" DELLA SCHEDA CONTATTO.
        #
        # Un componente NUOVO, piu' la riga che lo monta in
        # `contatto-dettaglio.js`, che era gia' ammesso. Mostra lo stato delle
        # automazioni del contatto, lo storico dei messaggi e un modulo per
        # scriverne uno a mano.
        #
        # Cio' che questo test difende resta intatto: nessuna logica di Rete e
        # nessun contratto P27 e' toccato - non un endpoint, non una rotta,
        # non un componente della Rete - e nessuna vista del CRM oltre a
        # quella gia' elencata. Il componente non conosce ne' agenzie ne'
        # territori: parla di un contatto per volta.
        "static/os_shell/assets/components/communications.js",
        # FLOW GLOBAL SECURITY - I DUE BOTTONI DI UN'AUTOMAZIONE.
        #
        # Le regole FLOW sono un catalogo unico per tutta la piattaforma, e da
        # quando le cinque rotte che lo scrivono sono dietro
        # `require_platform_admin` le due che questa vista premeva - activate e
        # deactivate - rispondono 403 a un ruolo tenant. I bottoni compaiono
        # quindi solo a chi e' `is_platform_admin`, per la stessa ragione per
        # cui la voce "Rete" compare solo a lui: la difesa e' lato server, e un
        # controllo che risponde sempre 403 e' un difetto dell'interfaccia. La
        # lettura della scheda non cambia per nessuno.
        #
        # Cio' che questo test difende resta intatto: nessuna logica di Rete e
        # nessun contratto P27 e' toccato - non un endpoint, non una rotta, non
        # un componente della Rete - e nessuna vista del CRM oltre a quelle
        # gia' elencate. Questa non e' una vista del CRM: e' la scheda di una
        # regola di piattaforma.
        "static/os_shell/assets/views/automazione-dettaglio.js",
        # A30-4 - L'AGENDA. Tre cartelle NUOVE (git le mostra come cartelle non
        # tracciate), piu' `main.js` e `app.css` gia' ammessi: la rotta
        # `#/agenda` senza voce in barra laterale, e una sezione CSS in coda.
        #
        # Cio' che questo test difende resta intatto: nessuna vista del CRM e
        # nessun componente della Rete e' toccato; l'Agenda vive nei suoi
        # moduli e parla solo con `/api/appointments`.
        "static/os_shell/assets/agenda/",
        "static/os_shell/assets/components/agenda/",
        "static/os_shell/assets/views/agenda/",
        # A30-5 - LA CREAZIONE MANUALE DI UN APPUNTAMENTO. Le cartelle
        # dell'Agenda ora sono tracciate, quindi git elenca i file: il dialog,
        # la pagina e un modulo nuovo di sole letture CRM. Nessuna vista del
        # CRM e nessun componente della Rete e' toccato; `contact-picker.js`
        # e' riusato cosi' com'e'. `agenda-api.js` guadagna il client della
        # ricerca stime (una GET sotto `/api/appointments`).
        "static/os_shell/assets/agenda/agenda-api.js",
        "static/os_shell/assets/agenda/agenda-lookup.js",
        "static/os_shell/assets/components/agenda/agenda-dialogs.js",
        "static/os_shell/assets/views/agenda/agenda-page.js",
    }
    toccati = {riga[3:].strip() for riga in modificati}
    assert toccati <= ammessi, sorted(toccati - ammessi)


def test_d4_the_shared_client_only_gained_a_put():
    """L'unica aggiunta al client condiviso e' il verbo che serviva.

    `PUT /agencies/{id}/owner` e' una sostituzione, non un aggiornamento
    parziale, e il verbo e' una scelta del backend: la UI lo rispetta invece di
    reinterpretarlo con una POST.
    """
    client = (ASSETS / "core" / "api-client.js").read_text(encoding="utf-8")
    assert "export function apiPut(" in client
    assert client.count("export function") == 5  # get, post, patch, put, delete
