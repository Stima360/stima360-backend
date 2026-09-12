"""Prove su `scripts/p26_6_live_cert.py` - la matrice ostile A/B.

PERCHE' UNA MATRICE VA VERIFICATA PIU' DI QUANTO VERIFICHI

Questo script e' l'ultima prova prima di autorizzare una seconda agenzia reale.
Il suo output e' l'evidenza su cui si chiude GATE-MA1. Un difetto qui non rompe
la produzione: dice una cosa falsa su di essa, e nessuno lo scopre - il che e'
peggio.

Ci sono due modi in cui una matrice ostile puo' mentire, e sono opposti:

1. **Puo' fallire senza motivo** e far sembrare rotto un isolamento che
   funziona - un cookie che il jar non invia, una fixture che non nasce.
2. **Puo' passare senza aver provato niente**: un dominio dimenticato, un caso
   saltato in silenzio, un confronto su un database vuoto dove "A non vede i
   dati di B" e' vero perche' non ci sono dati.

Il secondo e' quello pericoloso, ed e' quello contro cui e' scritta la maggior
parte di questo file: completezza rispetto all'inventario delle route, entrambe
le direzioni per ogni dominio, impossibilita' di ottenere PASS con uno skip,
con un caso rimosso, con un cleanup fallito o su un database sbagliato.

Nessuna prova qui apre una connessione o una socket: database e HTTP sono
sostituiti da doppi, che e' il motivo per cui lo script e' scritto con quei due
seam invece che con chiamate dirette.
"""
from __future__ import annotations

import ast
import functools
import io
import py_compile
import re
import textwrap
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "p26_6_live_cert.py"

# Import diretto e non `importorskip`: se questo modulo smette di importarsi la
# risposta giusta e' un fallimento, non uno skip che farebbe sparire l'intero
# file dal conteggio senza che nessuno se ne accorga.
from scripts import p26_6_live_cert as cert  # noqa: E402


# ---------------------------------------------------------------------------
# Doppi
# ---------------------------------------------------------------------------

class FakeCursor:
    """Un pezzo di database in memoria, abbastanza vero da poter essere rovinato."""

    def __init__(self, state: dict) -> None:
        self.state = state
        self.rowcount = 0
        self._row = None
        self._rows: list[dict] = []

    def _users(self) -> dict:
        return self.state.setdefault("users", {})

    def _effetti_vive(self) -> dict:
        """{tabella: set(id)} delle righe degli effetti ancora presenti.

        Materializzate davvero: e' cio' che rende non vuota la verifica per id
        introdotta per le relazioni SET NULL. Un doppio che rispondesse 0 a
        ogni conteggio renderebbe quella verifica soddisfatta anche da un
        cleanup che non cancella niente.
        """
        if "_effetti_vive" not in self.state:
            self.state["_effetti_vive"] = {
                t: set(ids) for t, ids in self.state.get("effetti_righe", {}).items()}
        return self.state["_effetti_vive"]

    def _osservazioni(self) -> dict:
        return self.state.setdefault("osservazioni", {})

    def _osservazioni_materializzate(self) -> bool:
        """Vero quando le osservazioni sono righe e non un numero.

        `figli_cascata` resta un'uscita esplicita: i test che vogliono una
        figlia RESTRICT ESTRANEA - una riga che il run NON ha creato - la
        dichiarano di la', e questo ramo risponde con righe sintetiche che
        portano una chiave di idempotenza diversa da quella derivata. Cosi' i
        due casi restano distinguibili invece di sommarsi in un conteggio solo,
        che e' esattamente l'ambiguita' che la correzione elimina.
        """
        return ("property_watch_observations"
                not in self.state.get("figli_cascata", {}))

    def _osservazioni_execute(self, upper, params):
        """Le quattro domande che il cleanup pone su questa tabella."""
        righe = self._osservazioni()
        if upper.startswith("DELETE"):
            for i in set(params[0]) if params else set():
                righe.pop(i, None)
            self.state.setdefault("deletes", []).append(upper)
            return
        if "FOR UPDATE NOWAIT" in upper:
            ids = set(params[0]) if params else set()
            self._rows = [{"id": i} for i in sorted(ids & set(righe))]
            return
        if "IDEMPOTENCY_KEY" in upper:
            watch = set(params[0]) if params else set()
            self._rows = [dict(r, id=i) for i, r in sorted(righe.items())
                          if r["watch_id"] in watch]
            return
        if "WATCH_ID = %S" in upper:
            # Le osservazioni di UN watch, per id singolo: e' la rilettura che
            # la fixture delle agenzie condivise fa subito dopo `initialize`,
            # per sapere quali righe dovra' cancellare.
            watch = params[0] if params else None
            self._rows = [{"id": i} for i, r in sorted(righe.items())
                          if r["watch_id"] == watch]
            return
        # Un conteggio: per id (verifica dei residui) oppure per watch_id con
        # l'eventuale esclusione delle nostre (guardia RESTRICT e preflight).
        if " ID IN " in f" {upper} " and "WATCH_ID" not in upper:
            ids = set(params[0]) if params else set()
            self._row = {"n": len(ids & set(righe))}
            return
        watch = set(params[0]) if params else set()
        esclusi = set(params[1]) if params and len(params) > 1 else set()
        self._row = {"n": len([i for i, r in righe.items()
                               if r["watch_id"] in watch and i not in esclusi])}

    def _osservazioni_sintetiche(self, upper, params):
        """Il caso `figli_cascata`: figlie RESTRICT che il run non ha creato.

        Il conteggio resta quello dichiarato dal test - prima e dopo la
        cancellazione del genitore - ma l'istantanea riceve righe VERE, con una
        chiave che non e' quella derivata: e' cosi' che restano estranee, e la
        guardia deve continuare a fermarsi su di loro.
        """
        prima, dopo = self.state.get("figli_cascata", {}).get(
            "property_watch_observations", (0, 0))
        quante = dopo if self.state.get("genitori_cancellati") else prima
        if "IDEMPOTENCY_KEY" in upper:
            watch = list(params[0]) if params else [0]
            self._rows = [
                {"id": 990000 + k, "watch_id": watch[0],
                 "idempotency_key": f"scansione-periodica-{k}",
                 "observation_type": "price_change", "source": "scan"}
                for k in range(prima)
            ]
            return
        if "FOR UPDATE NOWAIT" in upper:
            self._rows = []
            return
        self._row = {"n": quante}

    def execute(self, sql, params=None):
        statement = " ".join(sql.split())
        self.state.setdefault("sql", []).append(statement)
        # Query E parametri: per le interrogazioni sul catalogo la tabella sta
        # nel parametro, e senza registrarlo non si puo' verificare SU QUALI
        # tabelle la guardia delle dipendenze abbia davvero girato.
        self.state.setdefault("interrogazioni", []).append((statement, params))
        upper = statement.upper()

        if self.state.get("explode_on") and self.state["explode_on"] in statement:
            raise RuntimeError("il database e' caduto")

        if upper.startswith("SELECT CURRENT_DATABASE"):
            self._row = {"name": self.state.get("database", "stima360_db_test")}
        elif "FROM OWNER_AUDIT_LOG" in upper and "GROUP BY" in upper:
            # La classificazione degli audit senza radice: righe, non un
            # conteggio. Un doppio che rispondesse con un numero solo non
            # potrebbe distinguere "classificati" da "contati".
            self._rows = list(self.state.get("audit_senza_radice", []))
        elif "COUNT(*) AS N FROM AGENCIES" in upper:
            # Prima del ramo generico: quello imposta `_rows` e lascerebbe
            # `fetchone()` su un risultato vecchio.
            #
            # Il conteggio riflette le cancellazioni davvero eseguite: un
            # doppio che rispondesse sempre 0 renderebbe la verifica finale
            # incapace di accorgersi di una DELETE mancante.
            if "agenzie_residue" in self.state:
                self._row = {"n": self.state["agenzie_residue"]}
            else:
                vive = set(self.state.get("agenzie_vive", ()))
                ids = set(params[0]) if params else set()
                self._row = {"n": len(vive & ids)}
        elif (upper.startswith("SELECT") and "FROM AGENCIES" in upper
              and "PG_CONSTRAINT" not in upper):
            # `startswith("SELECT")` e non solo "FROM AGENCIES": senza, questo
            # ramo inghiottiva anche `DELETE FROM agencies`, che finiva per
            # restituire un elenco invece di cancellare - e il cleanup delle
            # agenzie dedicate risultava fallito per un difetto del doppio.
            self._rows = list(self.state.get("agencies", []))
        elif "COUNT(*) AS N FROM OPERATOR_USERS" in upper:
            if "leftovers" in self.state:
                self._row = {"n": self.state["leftovers"]}
            else:
                self._row = {"n": sum(1 for e in self._users().values()
                                      if e.startswith(cert.CERT_PREFIX))}
        elif "AS USERS" in upper:
            if "residue" in self.state:
                self._row = dict(self.state["residue"])
            else:
                ids = set(params[0]) if params else set()
                self._row = {"users": len([i for i in ids if i in self._users()]),
                             "memberships": 0, "sessions": 0}
        elif "FROM FOLLOWUP_ACTIONS" in upper and "TASK_ID = %S" in upper:
            # L'azione persistita dalla scansione: 1 per difetto, cosi' un run
            # sano passa. `azioni_persistite` = 0 modella l'escalation che
            # risponde 'completed' senza aver scritto nulla.
            self._row = {"n": self.state.get("azioni_persistite", 1)}
        elif "PROPERTY_WATCH_OBSERVATIONS" in upper and not self._osservazioni_materializzate():
            self._osservazioni_sintetiche(upper, params)
        elif "PROPERTY_WATCH_OBSERVATIONS" in upper:
            # LE OSSERVAZIONI, MATERIALIZZATE - E PRIMA DI "AS N FROM PUBLIC.".
            #
            # Quel ramo risponde a OGNI conteggio di dipendenze con il numero
            # che il test ha dichiarato, tabella per tabella indistinguibili.
            # Su questa tabella non basta piu': la domanda del preflight e'
            # "quante di queste righe NON sono del run", e la risposta deve
            # venire dalle righe vere, con la loro chiave, altrimenti la
            # distinzione che tutta la correzione introduce non viene mai
            # esercitata e il mutante che la rimuove sopravvive.
            self._osservazioni_execute(upper, params)
        elif ("AS N FROM PUBLIC." in upper
              and not self.state.get("dipendenti_estranee")
              and re.search(r"AS N FROM PUBLIC\.(OWNER_NOTIFICATIONS|OWNER_DOCUMENT_READS)",
                            upper)):
            # IL PERCORSO DOCUMENTALE SI CONTA SULLE RIGHE VERE.
            #
            # Il ramo generico qui sotto risponde con il numero che il test ha
            # dichiarato, uguale per ogni tabella: con quello, togliere
            # `owner_notifications` dal perimetro non cambiava nulla e il
            # mutante sopravviveva. Qui la domanda del preflight - "quante di
            # queste figlie NON sono nel perimetro" - riceve la risposta che
            # darebbe PostgreSQL: le righe prodotte, meno quelle escluse.
            #
            # Solo quando il test NON inietta una dipendenza estranea: se la
            # inietta, comanda lui, ed e' cosi' che si prova il blocco.
            tabella = re.search(
                r"AS N FROM PUBLIC\.([A-Z_]+)", upper).group(1).lower()
            vive = set(self._effetti_vive().get(tabella, ()))
            esclusi = set(params[1]) if params and len(params) > 1 else set()
            self._row = {"n": len(vive - esclusi)}
        elif "AS N FROM PUBLIC." in upper:
            # Le dipendenze fuori perimetro. Prima del ramo generico "AS N":
            # quello e' il censimento incoerenze e rispondeva 0, facendo
            # sembrare che nessuno referenziasse le nostre righe.
            #
            # Due conteggi distinti, ed e' il punto: senza l'esclusione del
            # perimetro la query vede ANCHE le righe del run che si
            # referenziano fra loro - `buy_requests.contact_id` punta ai nostri
            # contatti - e il cleanup si rifiuterebbe di procedere per sempre.
            interne = self.state.get("dipendenti_interne", 0)
            estranee = self.state.get("dipendenti_estranee", 0)
            self._row = {"n": estranee if "NOT (T.ID IN" in upper
                         else interne + estranee}
        elif upper.startswith("SELECT ID, STORAGE_KEY FROM PROPERTY_DOCUMENTS"):
            # Le chiavi degli oggetti caricati. `senza_chiave` modella i
            # documenti nati da un URL, che non hanno nulla nel bucket.
            # Solo i documenti CARICATI hanno una chiave: quelli nati da un
            # URL hanno storage_key NULL, e la query li esclude gia' con
            # `WHERE storage_key IS NOT NULL`. Un doppio che desse una chiave
            # a tutti farebbe sembrare pulito un bucket mai toccato.
            ids = list(params[0]) if params else []
            caricati = set(self.state.get("documenti_caricati", []))
            if self.state.get("senza_chiave"):
                self._rows = []
            else:
                self._rows = [{"id": i, "storage_key": f"k/{i}"}
                              for i in ids if i in caricati]
        elif upper.startswith("SELECT PROPERTY_DOCUMENT_ID FROM OWNER_SHARED_DOCUMENTS"):
            # Il recupero dell'origine quando la risposta non la porta.
            # `origine_perduta` modella il caso in cui nemmeno il database
            # risponde: allora l'oggetto nel bucket non e' piu' raggiungibile
            # per id, ed e' li' che il cleanup deve fermarsi.
            condiviso = params[0] if params else None
            origine = self.state.get("origine_condivisa", {}).get(condiviso)
            self._row = (None if self.state.get("origine_perduta")
                         else {"property_document_id": origine})
        elif (re.match(r"^SELECT ID(?:, [A-Z_, ]+)? FROM ([A-Z_]+) WHERE [A-Z_]+ IN", upper)
              and re.match(r"^SELECT ID(?:, [A-Z_, ]+)? FROM ([A-Z_]+) ", upper).group(1).lower()
              in self.state.get("figlie_derivate", {})):
            # Le figlie derivate, con TUTTE le colonne richieste: la seconda
            # FK serve a distinguere una riga nostra da una MISTA, e un doppio
            # che restituisse solo l'id renderebbe quella distinzione
            # impossibile da sbagliare - cioe' impossibile da provare.
            tabella = re.match(r"^SELECT ID(?:, [A-Z_, ]+)? FROM ([A-Z_]+) ",
                               upper).group(1).lower()
            colonne = [c.strip().lower() for c in
                       re.match(r"^SELECT (.*?) FROM ", upper).group(1).split(",")]
            genitori = set(params[0]) if params else set()
            legame = re.search(r"WHERE ([A-Z_]+) IN", upper).group(1).lower()
            self._rows = [
                {c: riga.get(c) for c in colonne}
                for riga in self.state["figlie_derivate"][tabella]
                if riga.get(legame) in genitori
            ]
        elif upper.startswith("SELECT ID, STIMA_ID FROM PROPERTY_WATCHES"):
            # La stima di ciascun watch: e' da li' che si deriva la chiave di
            # idempotenza attesa. Un doppio che non la desse renderebbe
            # l'insieme atteso vuoto, e OGNI osservazione sembrerebbe estranea.
            ids = set(params[0]) if params else set()
            self._rows = [{"id": i, "stima_id": s}
                          for i, s in self.state.get("watch_stima", {}).items()
                          if i in ids]
        elif upper.startswith("SELECT ID FROM PROPERTY_WATCHES") and "STIMA_ID" in upper:
            # Il watch appena creato nelle agenzie CONDIVISE, riletto per id:
            # e' quello su cui il cleanup cancellera'. `watch_per_stima` lo
            # registra quando `initialize` riesce.
            stima, agenzia = (params[0], params[1]) if params else (None, None)
            trovato = self.state.get("watch_per_stima", {}).get(stima)
            self._row = {"id": trovato} if trovato else None
        elif upper.startswith("SELECT ID FROM PROPERTY_WATCH_OBSERVATIONS") \
                and "WATCH_ID = %S" in upper:
            watch = params[0] if params else None
            self._rows = [{"id": i} for i, r in self._osservazioni().items()
                          if r["watch_id"] == watch]
        elif upper.startswith("SELECT ID FROM PROPERTY_WATCHES"):
            # I watch delle agenzie dedicate, fotografati PRIMA del cleanup.
            # Un doppio che non li restituisse renderebbe vuota l'istantanea,
            # e la verifica delle figlie salterebbe in silenzio.
            agenzie = set(params[0]) if params else set()
            # `senza_watch` modella l'initialize che non ha creato nulla: e' il
            # caso in cui la verifica delle figlie non ha genitori da cui
            # partire, e deve dirlo invece di dichiararsi superata.
            self._rows = [] if self.state.get("senza_watch") else [
                {"id": i} for i, a in
                self.state.get("watch_vivi", {}).items() if a in agenzie]
        elif upper.startswith("SELECT ID FROM LEADS"):
            agenzie = set(params[0]) if params else set()
            self._rows = [{"id": i} for i, a in
                          self.state.get("lead_dedicati", {}).items() if a in agenzie]
        elif upper.startswith("SELECT ID FROM CONTACTS"):
            agenzie = set(params[0]) if params else set()
            self._rows = [{"id": i} for i, a in
                          self.state.get("contatti_dedicati", {}).items()
                          if a in agenzie]
        elif any(f" {f.upper()} " in f" {upper} "
                 for f, _c, _g, _a in cert.Certification.CHILD_FOREIGN_KEYS):
            # Le figlie: DUE conteggi, prima e dopo la cancellazione del
            # genitore. Un doppio con un solo numero non distingue "il CASCADE
            # ha funzionato" da "non stavo guardando": e' esattamente il
            # difetto che questa correzione rimuove dallo script.
            tabella = next(f for f, _c, _g, _a in cert.Certification.CHILD_FOREIGN_KEYS
                           if f" {f.upper()} " in f" {upper} ")
            prima, dopo = self.state.get("figli_cascata", {}).get(tabella, (0, 0))
            self._row = {"n": dopo if self.state.get("genitori_cancellati") else prima}
        elif "FROM BUY_REQUESTS WHERE ID = %S" in upper:
            # LA PRECONDIZIONE DELLA FIXTURE FLOW, letta sul dato.
            #
            # `next_action_at` NULL e' cio' che rende R004 non corrispondente e
            # quindi l'esecuzione priva di altri effetti. Il valore arriva dallo
            # stato, cosi' un test puo' modellare la fixture BUY cambiata e
            # verificare che la matrice si fermi invece di scrivere.
            self._row = {"status": "active",
                         "next_action_at": self.state.get("buy_next_action")}
        elif "FROM FLOW_EVENTS WHERE DEDUPLICATION_KEY" in upper:
            chiave = params[0] if params else None
            chiavi = self.state.get("flow_chiavi", {})
            trovati = chiavi.get(chiave, [])
            vivi = self.state.get("flow_events_vivi", {})
            self._rows = [{"id": i, "agency_id": vivi.get(i)} for i in trovati]
        elif "FROM FLOW_EXECUTIONS E" in upper and "WHERE E.EVENT_ID" in upper:
            evento = params[0] if params else None
            self._rows = [
                {"id": i, "agency_id": e["agency"], "status": e["status"],
                 "rule_code": e["rule_code"]}
                for i, e in sorted(self.state.get("flow_esecuzioni", {}).items())
                if e.get("event_id") == evento]
        elif "FROM FLOW_EXECUTIONS WHERE RETRY_OF_EXECUTION_ID IN" in upper:
            ids = set(params[0]) if params else set()
            self._rows = [{"id": i} for i, e in
                          sorted(self.state.get("flow_esecuzioni", {}).items())
                          if e.get("retry_of") in ids]
        elif "FROM FLOW_ACTION_RECORDS WHERE EXECUTION_ID IN" in upper:
            ids = set(params[0]) if params else set()
            self._rows = [{"id": i} for i, e in
                          sorted(self.state.get("flow_record_azione", {}).items())
                          if e in ids]
        elif "FROM TASKS WHERE (METADATA ->> 'FLOW_EXECUTION_ID')" in upper:
            ids = set(params[0]) if params else set()
            self._rows = [{"id": i} for i, e in
                          sorted(self.state.get("flow_task_generati", {}).items())
                          if e in ids]
        elif "COUNT(*) AS N FROM FLOW_EXECUTIONS" in upper and "ID IN" in upper:
            ids = set(params[0]) if params else set()
            self._row = {"n": len(ids & set(self.state.get("flow_esecuzioni", {})))}
        elif "COUNT(*) AS N FROM FLOW_EVENTS" in upper:
            # MATERIALIZZATI DAVVERO. Prima questo ramo non esisteva e il
            # censimento generico rispondeva 0 sempre: i test passavano
            # qualunque fosse l'ordine delle cancellazioni, che e' il motivo
            # per cui l'ordine sbagliato non si vedeva.
            ids = set(params[0]) if params else set()
            self._row = {"n": len(ids & set(self.state.get("flow_events_vivi", {})))}
        elif "COUNT(*) AS N FROM STIME" in upper:
            ids = set(params[0]) if params else set()
            self._row = {"n": len(ids & set(self.state.get("stime_create", {})))}
        elif "COUNT(*) AS N FROM SELLER_TIMELINE_EVENTS" in upper and "ID IN" in upper:
            ids = set(params[0]) if params else set()
            vivi = set(self.state.get("eventi_creati", {}))
            # Gli id del DOMINIO seller_timeline_events non passano di qui: li
            # conta il ramo delle righe residue. Questo guarda solo gli eventi
            # `stima_completata` creati dalla fixture PROPERTY_WATCH.
            self._row = {"n": len(ids & vivi)}
        elif re.match(r"^SELECT T\.ID FROM ([A-Z_]+) T WHERE", upper):
            # L'istantanea degli id delle righe del run negli effetti, presa
            # prima delle DELETE.
            tabella = re.match(r"^SELECT T\.ID FROM ([A-Z_]+) T ",
                               upper).group(1).lower()
            self._rows = [{"id": i} for i in sorted(self._effetti_vive().get(tabella, ()))]
        elif re.match(r"^SELECT COUNT\(\*\) AS N FROM ([A-Z_]+) WHERE ID IN", upper) \
                and re.match(r"^SELECT COUNT\(\*\) AS N FROM ([A-Z_]+) ",
                             upper).group(1).lower() in self._effetti_vive():
            # Le righe degli effetti dopo il cleanup: quante di quelle
            # fotografate sono ancora la'.
            tabella = re.match(r"^SELECT COUNT\(\*\) AS N FROM ([A-Z_]+) ",
                               upper).group(1).lower()
            ids = set(params[0]) if params else set()
            self._row = {"n": len(ids & self._effetti_vive()[tabella])}
        elif re.match(r"^SELECT COUNT\(\*\) AS N FROM ([A-Z_]+) WHERE ID IN", upper) \
                and "effetti_vivi" in self.state:
            # Righe sopravvissute alla cancellazione, per tabella. Serve a
            # esercitare la verifica finale su cio' che NON sta in
            # `created_rows`: documenti, match, conti, vendite.
            tabella = re.match(r"^SELECT COUNT\(\*\) AS N FROM ([A-Z_]+) ",
                               upper).group(1).lower()
            self._row = {"n": self.state["effetti_vivi"].get(tabella, 0)}
        elif "COUNT(*) AS N FROM NEXT_BEST_ACTIONS" in upper:
            # Prima dei rami generici "AS N", che la intercettavano e
            # rispondevano 0 rendendo l'appartenenza sempre soddisfatta.
            self._row = {"n": self.state.get("nba_estranee", 0)}
        elif "AS N" in upper and any(
                t in upper for t in (" PROPERTY_STATUS_HISTORY", " BUY_REQUEST_HISTORY",
                                     " MATCH_RUNS", " OWNER_AUDIT_LOG",
                                     " MATCH_REQUIREMENT_RESULTS")):
            # Gli effetti delle API: per difetto zero, cosi' un run sano passa.
            # `effetti_residui` li fa sopravvivere, ed e' il caso in cui il
            # CASCADE non e' avvenuto e nessuno se ne accorgeva.
            # La piu' SPECIFICA per prima: la query sui risultati per criterio
            # nomina anche match_runs, e scegliere la prima corrispondenza
            # attribuirebbe il conteggio alla tabella sbagliata.
            chiave = next((t.strip().lower() for t in
                           (" MATCH_REQUIREMENT_RESULTS", " PROPERTY_STATUS_HISTORY",
                            " BUY_REQUEST_HISTORY", " OWNER_AUDIT_LOG",
                            " MATCH_RUNS") if t in upper), None)
            self._row = {"n": self.state.get("effetti_residui", {}).get(chiave, 0)}
        elif "AS N" in upper and " FROM " in upper and self.state.get("residue_rows") \
                and any(t in upper for t in (" CONTACTS", " PROPERTIES", " BUY_REQUESTS", " TASKS")) \
                and "ID IN" in upper:
            # Righe ancora presenti: e' cio' che la verifica finale deve vedere
            # quando una DELETE ha risposto 2xx senza cancellare.
            self._row = {"n": 2}
        elif "AS N" in upper:                      # il censimento incoerenze
            key = next((k for k in self.state.get("census", {}) if k in statement), None)
            self._row = {"n": self.state.get("census", {}).get(key, 0)}
        elif "FROM AGENCY_MEMBERSHIPS" in upper and "OPERATOR_USER_ID AS ID" in upper:
            # L'agency_owner reale dell'agenzia. `owners` assente = nessun
            # titolare: e' il caso in cui OWNER Admin e il portale restano
            # BLOCKED invece di essere dichiarati provati.
            owners = self.state.get("owners", {})
            agency = params[0] if params else None
            self._row = {"id": owners[agency]} if agency in owners else None
        elif upper.startswith("SELECT 1 FROM AGENCIES"):
            self._row = {"1": 1} if self.state.get("slug_collide") else None
        elif upper.startswith("INSERT INTO AGENCIES"):
            self.state["next_agency"] = self.state.get("next_agency", 500) + 1
            self.state.setdefault("agenzie_create", []).append(params)
            self.state.setdefault("agenzie_vive", []).append(self.state["next_agency"])
            self._row = {"id": self.state["next_agency"]}
            self.rowcount = 1
        elif "FROM PG_CONSTRAINT" in upper and "GENITORE_SCHEMA" in upper:
            # Le ALTRE chiavi esterne di una figlia derivata, con la RELAZIONE
            # INTERA: schema, tabella, colonna riferita e numero di colonne del
            # vincolo. Un doppio che restituisse solo (colonna, genitore)
            # renderebbe impossibile sbagliare `confkey` o una FK composita -
            # cioe' impossibile provare che il codice le rifiuta.
            #
            # `fk_figlia` mappa tabella -> [(colonna, schema, genitore,
            # colonna_riferita, quante_colonne)]; le forme corte restano
            # ammesse e prendono i valori normali.
            tabella = params[0] if params else None
            righe = []
            for voce in self.state.get("fk_figlia", {}).get(tabella, []):
                # Forma corta (colonna, genitore) = il caso normale; forma
                # lunga (colonna, schema, genitore, colonna_riferita,
                # quante_colonne) = le forme che il codice deve RIFIUTARE.
                if len(voce) == 2:
                    colonna, genitore = voce
                    schema, riferita, quante = "public", "id", 1
                else:
                    colonna, schema, genitore, riferita, quante = (
                        list(voce) + [None] * 5)[:5]
                righe.append({
                    "vincolo": f"{tabella}_{colonna}_fkey",
                    "genitore_schema": schema or "public",
                    "genitore_tabella": genitore,
                    "colonna": colonna,
                    "colonna_riferita": riferita or "id",
                    "quante_colonne": quante or 1,
                })
            self._rows = righe
        elif "FROM PG_CONSTRAINT" in upper and "AGENCIES" in upper:
            self._rows = list(self.state.get("fk_agencies", []))
        elif "FROM PG_CONSTRAINT" in upper:
            # Le FK verso una tabella del perimetro. `fk_perimetro` mappa
            # tabella -> [(figlio, colonna)]: e' cosi' che un test fa comparire
            # una dipendenza che il censimento non aveva visto.
            genitore = params[0] if params else None
            self._rows = [{"figlio": f, "colonna": c}
                          for f, c in self.state.get("fk_perimetro", {}).get(genitore, [])]
        elif upper.startswith("INSERT INTO OPERATOR_SESSIONS"):
            self.state["next_session"] = self.state.get("next_session", 5000) + 1
            self.state.setdefault("owner_sessions", []).append(params)
            self._row = {"id": self.state["next_session"]}
            self.rowcount = 1
        elif "FROM TASKS" in upper and "AGENCY_ID" in upper and upper.startswith("SELECT ID"):
            # L'agenzia reale delle attivita' selezionate. `task_owner` mappa
            # id -> agenzia; ogni id non elencato e' attribuito all'agenzia 1,
            # cosi' un test puo' iniettare una riga estranea senza elencarle tutte.
            # Per difetto un'attivita' appartiene all'agenzia che l'ha
            # selezionata: e' il caso corretto. `task_owner` lo sovrascrive,
            # ed e' cosi' che un test inietta una riga che NON appartiene a chi
            # l'ha vista - il difetto che la disgiunzione non saprebbe cogliere.
            derivato = {int(riga["id"]): agenzia
                        for agenzia, righe in self.state.get("stale_followup", {}).items()
                        for riga in righe}
            derivato.update(self.state.get("task_owner", {}))
            ids = list(params[0]) if params else []
            self._rows = [{"id": i, "agency_id": derivato.get(i)} for i in ids]
        elif upper.startswith("INSERT INTO SELLER_TIMELINE_EVENTS ("):
            # L'evento `stima_completata` che la fixture PROPERTY_WATCH
            # inserisce: senza, `initialize` risponde 400 e nessun watch nasce.
            # L'agenzia e' l'ULTIMO parametro, come nella query vera.
            self.state["next_evento"] = self.state.get("next_evento", 10600) + 1
            self.state.setdefault("eventi_creati", {})[
                self.state["next_evento"]] = params[-1]
            self.state.setdefault("stime_valutate", set()).add(params[0])
            self._row = {"id": self.state["next_evento"]}
            self.rowcount = 1
        elif upper.startswith("INSERT INTO STIME"):
            self.state["next_stima"] = self.state.get("next_stima", 9500) + 1
            # id -> agenzia: e' cosi' che la riga puo' essere davvero
            # cancellata dal DELETE per agency_id, invece che ignorata.
            # L'agenzia e' l'ULTIMO parametro: la query vera ne ha sei, e
            # leggerne uno per posizione fissa si rompe appena cambia.
            self.state.setdefault("stime_create", {})[self.state["next_stima"]] = params[-1]
            self._row = {"id": self.state["next_stima"]}
            self.rowcount = 1
        elif upper.startswith("SELECT ID FROM STIME"):
            stime = self.state.get("stime", {})
            agency = params[0] if params else None
            self._row = {"id": stime[agency]} if agency in stime else None
        elif upper.startswith("SELECT 1 FROM OPERATOR_USERS"):
            self._row = {"1": 1} if self.state.get("collide") else None
        elif upper.startswith("INSERT INTO OPERATOR_USERS"):
            self.state["next_id"] = self.state.get("next_id", 900) + 1
            self.state.setdefault("inserted", []).append(params)
            self._users()[self.state["next_id"]] = params[0]
            self._row = {"id": self.state["next_id"]}
            self.rowcount = 1
        elif upper.startswith("INSERT INTO AGENCY_MEMBERSHIPS"):
            self.state.setdefault("memberships", []).append(params)
            self.rowcount = 1
        elif upper.startswith("DELETE"):
            self.state.setdefault("deletes", []).append(statement)
            # Solo le DELETE per agenzia portano una tupla di id: quella delle
            # fixture orfane passa una lista piatta di terne, e leggerla come
            # insieme di agenzie sarebbe un errore del doppio.
            agenzie = (set(params[0]) if params and isinstance(params[0], (tuple, list, set))
                       else set())
            # PER ID E PER AGENZIA SONO DUE CANCELLAZIONI DIVERSE, e il doppio
            # deve distinguerle: le esecuzioni e gli eventi delle agenzie
            # CONDIVISE se ne vanno per id (EFFECT_BY_ID_TABLES), quelli delle
            # DEDICATE per `agency_id`. Un ramo solo leggerebbe gli id come se
            # fossero agenzie e non cancellerebbe nulla, lasciando credere al
            # test che il cleanup funzioni perche' il doppio non guarda.
            per_agenzia = "WHERE AGENCY_ID IN" in upper
            if "FROM FLOW_EVENTS" in upper:
                vivi = self.state.get("flow_events_vivi", {})
                bersagli = ([i for i, a in vivi.items() if a in agenzie]
                            if per_agenzia else [i for i in vivi if i in agenzie])
                for i in bersagli:
                    del vivi[i]
            if "FROM FLOW_EXECUTIONS" in upper:
                vive = self.state.get("flow_esecuzioni", {})
                bersagli = ([i for i, e in vive.items() if e["agency"] in agenzie]
                            if per_agenzia else [i for i in vive if i in agenzie])
                for i in bersagli:
                    del vive[i]
            if "FROM STIME" in upper:
                # In posto, non riassegnando: `probe.stime_dedicate` e'
                # un alias di questo dizionario.
                vive = self.state.get("stime_create", {})
                for i in [i for i, a in vive.items() if a in agenzie]:
                    del vive[i]
            if "FROM SELLER_TIMELINE_EVENTS" in upper:
                eventi = self.state.get("eventi_creati", {})
                for i in [i for i, a in eventi.items() if a in agenzie]:
                    del eventi[i]
            # La DELETE per predicato di appartenenza - quella con l'alias `t`
            # - e' l'unica che il doppio modella per gli effetti. E'
            # un'approssimazione conservativa: la cancellazione per conto
            # proprietario, in cleanup_owner_fixtures, non e' rappresentata,
            # quindi il doppio puo' segnalare un residuo che il database vero
            # non avrebbe, mai nasconderne uno.
            per_predicato = re.match(r"^DELETE FROM ([A-Z_]+) T WHERE", upper)
            if per_predicato:
                tabella = per_predicato.group(1).lower()
                if (tabella in self._effetti_vive()
                        and tabella not in self.state.get("delete_inefficace", ())):
                    self._effetti_vive()[tabella] = set()
            # Un effetto cancellato PER ID: e' la forma che il percorso
            # documentale usa, perche' le sue righe vanno rimosse prima del
            # conto proprietario che se le porterebbe via in CASCADE. Senza
            # questo ramo il doppio le lasciava vive e la verifica finale le
            # segnalava come residui - un guasto del doppio, non del cleanup.
            # Le righe create nelle agenzie CONDIVISE si cancellano per id,
            # non per agenzia: senza questo ramo il doppio le lascerebbe vive
            # e la verifica finale le segnalerebbe come residui - un guasto
            # del doppio, non del cleanup.
            ids_puntuali = (set(params[0]) if params and isinstance(params[0], (tuple, list, set))
                            else set())
            if "FROM STIME" in upper and "WHERE ID IN" in upper:
                vive = self.state.get("stime_create", {})
                for i in [i for i in vive if i in ids_puntuali]:
                    del vive[i]
            if "FROM SELLER_TIMELINE_EVENTS" in upper and "WHERE ID IN" in upper:
                eventi = self.state.get("eventi_creati", {})
                for i in [i for i in eventi if i in ids_puntuali]:
                    del eventi[i]
            if "FROM PROPERTY_WATCHES" in upper and "WHERE ID IN" in upper:
                vivi = self.state.get("watch_vivi", {})
                for i in [i for i in vivi if i in ids_puntuali]:
                    del vivi[i]
            per_id = re.match(r"^DELETE FROM ([A-Z_]+) WHERE ID IN", upper)
            if per_id:
                tabella = per_id.group(1).lower()
                vive = self._effetti_vive()
                if (tabella in vive
                        and tabella not in self.state.get("delete_inefficace", ())):
                    vive[tabella] = vive[tabella] - (
                        set(params[0]) if params else set())
            if agenzie and ("FROM PROPERTY_WATCHES" in upper or "FROM CONTACTS" in upper):
                # I genitori delle agenzie dedicate se ne sono andati: da qui
                # in poi le figlie rispondono con il conteggio "dopo". La
                # DELETE delle fixture orfane non conta: cancella contatti
                # delle agenzie condivise, e non e' il genitore in questione.
                self.state["genitori_cancellati"] = True
            if "FROM AGENCIES" in upper and params:
                self.state["agenzie_vive"] = [
                    a for a in self.state.get("agenzie_vive", [])
                    if a not in set(params[0])]
            if "delete_rowcount" in self.state:
                self.rowcount = self.state["delete_rowcount"]
            elif "FROM OPERATOR_USERS" in upper and params:
                targets = [i for i in params[0] if i in self._users()]
                for identifier in targets:
                    del self._users()[identifier]
                self.rowcount = len(targets)
            else:
                self.rowcount = 1
        return None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


def fake_database(**state) -> cert.Database:
    shared = dict(state)

    @contextmanager
    def factory(*, commit=False):
        shared.setdefault("commits", []).append(commit)
        yield (None, FakeCursor(shared))

    database = cert.Database(factory)
    database.state = shared          # type: ignore[attr-defined]
    return database


AGENCIES = [
    {"id": 1, "slug": cert.DEFAULT_AGENCY_SLUG, "name": "STIMA360", "status": "active"},
    {"id": 2, "slug": cert.AGENCY_B_SLUG, "name": "Agenzia B (TEST)", "status": "active"},
]


def quiet_report():
    stream = io.StringIO()
    return cert.Report(stream=stream), stream


@functools.lru_cache(maxsize=1)
def real_routes():
    """La tavola delle route dell'APP VERA, non quella che il doppio immagina.

    PERCHE' ESISTE, E COSA E' COSTATO NON AVERLA

    Il doppio HTTP rispondeva 200/201 a qualunque percorso gli si chiedesse.
    Era comodo e falso: uno script che invocava route inesistenti passava qui e
    riceveva 405 su Render. Tre dei difetti del run 22d007af7916 sono
    esattamente questo -

      GET    /api/property/properties/{id}/visits   non esiste (c'e' la POST)
      DELETE /api/core/contacts/{id}                non esiste affatto

    - e nessuno dei due poteva essere visto da un doppio permissivo. Peggio:
    il 405 veniva CONTATO COME PROVA SUPERATA, perche' 405 non e' 200.

    Da qui in avanti il doppio conosce le route vere, lette da `main.app`:
    percorso sconosciuto -> 404, metodo non previsto -> 405, come farebbe
    Starlette. Un percorso inventato nello script fa fallire la suite locale
    invece di arrivare sul TEST.

    LA FONTE E' L'OPENAPI, NON `app.routes`

    Il primo tentativo leggeva `main.app.routes` e trovava 23 route su oltre
    duecento: questa applicazione non appiattisce `include_router`, tiene i
    sotto-router in oggetti `_IncludedRouter`, e camminare quell'albero
    significherebbe dipendere da una classe interna. Il documento OpenAPI
    elenca ogni percorso con i suoi metodi, prefissi gia' risolti, ed e' la
    stessa fonte su cui poggia l'inventario di P26-5.
    """
    import main

    table = []
    for path, item in main.app.openapi()["paths"].items():
        methods = {method.upper() for method, operation in item.items()
                   if isinstance(operation, dict)}
        if not methods:
            continue
        pattern = re.compile(
            "^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path)) + "$")
        table.append((pattern, methods, path))
    return tuple(table)


def resolve_route(method: str, path: str):
    """(stato, percorso) come li deciderebbe Starlette: 404, 405 oppure None."""
    bare = path.split("?")[0]
    allowed = set()
    for pattern, methods, template in real_routes():
        if pattern.match(bare):
            allowed |= methods
    if not allowed:
        return 404, None
    if method.upper() not in allowed:
        return 405, None
    return None, bare


def cert_reason() -> str:
    """Il motivo che il motore MATCH restituisce quando manca un criterio."""
    return "Nessun criterio MATCH effettivo impostato"


def _prefix_of(path: str) -> str:
    """Il prefisso di dominio di un percorso, per il doppio HTTP.

    Il piu' LUNGO che corrisponde, non il primo: `/api/property-watch/...`
    comincia anche per `/api/property`, e prendere il primo lo attribuirebbe al
    dominio sbagliato.
    """
    candidates = [d.prefix for d in cert.DOMAINS if path.startswith(d.prefix)]
    return max(candidates, key=len) if candidates else path.split("?")[0]


class FakeHttp(cert.HttpProbe):
    """Un'applicazione finta che ISOLA DAVVERO.

    Tiene le risorse per agenzia e risponde 404 a chi chiede quelle di un
    altro. E' il comportamento corretto, quindi la matrice su questo doppio
    deve passare - e ogni mutazione che rompe l'isolamento del doppio deve
    farla fallire. E' cosi' che si prova che la matrice guarda davvero.
    """

    def __init__(self, base="https://test.example", broken=None, prepopulate=(),
                 stime=None, only=None):
        super().__init__(base)
        self.broken = broken or set()
        # LA SUPERFICIE DA ROMPERE.
        #
        # `Report.check` interrompe il run alla prima prova fallita - ed e'
        # giusto: una matrice che continuasse dopo una fuga accertata
        # produrrebbe pagine di risultati su un sistema gia' compromesso. Ma
        # significa che una rottura globale non arriva mai ai domini in fondo:
        # per provare che la sonda di OWNER Admin sa fallire, va rotto OWNER
        # Admin e nient'altro.
        self.only = only
        # Domini gia' popolati sul database, uno per agenzia: e' la condizione
        # di un TEST con la catena a monte gia' costruita. Serve a distinguere
        # "lista vuota, non provato" da "lista piena, provato".
        self.prepopulate = tuple(prepopulate)
        self.rows: dict[int, dict] = {}       # id -> {"agency": .., "body": ..}
        self.sessions: dict[str, int] = {}    # token -> agency_id
        self.next_id = 100
        # I tre principali del doppio. Un token che non compare in `roles` e'
        # un operatore della matrice.
        self.roles: dict[str, str] = {}       # token -> ruolo
        self.portal: dict[str, int] = {}      # cookie portale -> conto
        self.issued: dict[str, int] = {}      # token monouso -> conto
        # Le stime che "esistono gia'" sul TEST, per agenzia: hanno un watch
        # leggibile dal proprietario. `{}` riproduce un TEST senza stime, dove
        # PROPERTY_WATCH deve risultare BLOCKED e non PASS.
        self.stime = dict(stime or {})
        # Le precondizioni reali della vendita, modellate: senza un legame
        # proprietario su quell'immobile, create_sale_scoped rifiuta con 409.
        # Il doppio che non lo sapeva ha lasciato passare uno script che sul
        # TEST prendeva 409 su entrambe le agenzie.
        self.owner_links: set = set()          # (property_id, contact_id)
        # FOLLOWUP: attivita' per agenzia e regola abilitata. La scansione del
        # doppio escala SOLO le attivita' della propria agenzia - come la
        # query vera, che porta il predicato sul tenant - e restituisce gli id
        # elaborati, che e' cio' che la matrice confronta.
        self.rule_enabled = True
        # email operatore dedicato -> agency_id, popolata dal test.
        self.dedicate: dict = {}
        # id dei property_documents nati da un CARICAMENTO: solo questi hanno
        # un oggetto nel bucket.
        self.caricati: list = []
        # Righe che il doppio del database deve poter contare e cancellare.
        # `working_run` le aggancia allo stato del database: finche' restavano
        # qui e basta, ogni conteggio rispondeva 0 e nessun ordine di
        # cancellazione poteva risultare sbagliato.
        self.flow_events_vivi: dict = {}       # id evento -> agenzia
        # chiave di deduplicazione -> [id evento]. E' una LISTA: la fixture
        # esige di trovarne esattamente uno, e senza poterne rappresentare due
        # quella verifica non sarebbe esercitabile.
        self.flow_chiavi: dict = {}
        # id esecuzione -> {agency, event_id, status, rule_code, retry_of}
        self.flow_esecuzioni: dict = {}
        self.watch_vivi: dict = {}             # id watch  -> agenzia
        self.watch_stima: dict = {}            # id watch  -> stima_id
        self.lead_vivi: dict = {}              # id lead   -> agenzia
        # Gli effetti del percorso documentale, materializzati dove il
        # database simulato li cerca. `working_run` li aggancia a
        # `effetti_righe`: finche' restavano qui e basta, il perimetro non
        # vedeva nulla e il run 42e32975ccd6 si e' bloccato su righe che la
        # matrice stessa aveva appena prodotto.
        self.effetti_prodotti: dict | None = None
        self.stato_db: dict | None = None
        self.prossimo_effetto = 77000
        # id osservazione -> riga. La baseline che `initialize` scrive insieme
        # al watch: e' RESTRICT verso property_watches, quindi finche' c'e' il
        # watch non si cancella.
        self.osservazioni: dict = {}
        # stime per cui esiste un evento `stima_completata`.
        self.stime_valutate: set = set()
        # id condivisione -> id property_documents. Nello schema la colonna e'
        # NOT NULL: la riga c'e' anche quando la risposta HTTP tace.
        self.origine_condivisa: dict = {}
        self.match_property: dict = {}         # match_id  -> property_id
        self.proposal_property: dict = {}      # proposal_id -> property_id

    # -- helper -----------------------------------------------------------
    def _effetto(self, tabella: str) -> int | None:
        """Registra una riga di effetto prodotta da un percorso reale.

        Scrive DOVE il doppio del database la cercherebbe, cache compresa: se
        finisse solo in `effetti_righe` dopo che `_effetti_vive` ha gia'
        costruito la sua copia, l'istantanea non la troverebbe e il test
        sarebbe verde su un perimetro incompleto - cioe' proprio il difetto da
        provare.
        """
        if self.effetti_prodotti is None:
            return None
        self.prossimo_effetto += 1
        identificativo = self.prossimo_effetto
        self.effetti_prodotti.setdefault(tabella, []).append(identificativo)
        # La cache di `_effetti_vive` puo' non esistere ancora: si aggiorna
        # solo se e' gia' stata costruita, altrimenti nascera' dalla lista.
        cache = self.stato_db.get("_effetti_vive") if self.stato_db else None
        if isinstance(cache, dict):
            cache.setdefault(tabella, set()).add(identificativo)
        return identificativo

    def _agency_of(self, jar):
        token = self.token_in(jar) if jar else None
        return self.sessions.get(token)

    def _stato(self, chiave):
        """Uno stato del doppio del DATABASE, letto dal lato HTTP.

        Serve ai casi in cui una prova deve spegnere un percorso che passa da
        entrambi i doppi - `initialize`, per esempio - senza inventare una
        seconda manopola.
        """
        return bool((self.stato_db or {}).get(chiave))

    def _broken(self, kind, surface="generic"):
        """Vero se `kind` va rotto SU QUESTA superficie."""
        return kind in self.broken and (self.only is None or self.only == surface)

    def _role_of(self, jar):
        token = self.token_in(jar) if jar else None
        return self.roles.get(token, cert.CERT_ROLE)

    def _portal_account(self, jar):
        """Il conto proprietario dietro il cookie del PORTALE.

        Cookie diverso da quello dell'operatore, di proposito: un doppio che
        li confondesse lascerebbe passare una matrice che interroga il portale
        con l'identita' sbagliata.
        """
        if jar is None:
            return None
        for cookie in jar:
            if cookie.name == cert.OWNER_COOKIE_NAME and cookie.value:
                return self.portal.get(cookie.value)
        return None

    def _marker_of(self, identifier):
        """Il marcatore della riga, se ne porta uno.

        Serve a far ereditare il marcatore alle risorse derivate: un match
        proietta i titoli della richiesta e dell'immobile, un conto
        proprietario il nome del contatto. Se il doppio non lo propagasse, la
        prova "la lista di A contiene la propria risorsa" fallirebbe per un
        difetto del doppio invece che del sistema.
        """
        import json as _json

        row = self.rows.get(identifier)
        if row is None:
            return ""
        body = _json.loads(row["body"])
        return next((str(v) for v in body.values()
                     if isinstance(v, str) and v.startswith("P26-6-")), "")

    def _seed(self, agency):
        """Una riga preesistente per agenzia nei domini richiesti."""
        import json as _json

        for prefix in self.prepopulate:
            if any(r["prefix"] == prefix and r["agency"] == agency
                   for r in self.rows.values()):
                continue
            self.next_id += 1
            self.rows[self.next_id] = {
                "agency": agency, "prefix": prefix,
                "body": _json.dumps({"id": self.next_id, "pre": "esistente"}),
            }

    def upload(self, path, *, jar, campi, nome_file, contenuto, tipo="application/pdf"):
        """Il doppio del caricamento multipart: registra i campi e delega."""
        self.upload_property = int(campi["property_id"])
        self.upload_title = campi["public_title"]
        agency = self._agency_of(jar)
        if agency is None:
            return self._reply("POST", path, 401, b'{"detail":"Non autorizzato"}')
        if self._role_of(jar) != cert.OWNER_ADMIN_MIN_ROLE:
            return self._reply("POST", path, 403, b'{"detail":"riservato"}')
        return self._owner_admin("POST", path, jar, agency, None)

    def _reply(self, method, path, status, body=b""):
        self.exchanges.append((f"{method} {path.split('?')[0]}", status, body))
        return cert.Response(status, {}, body)

    # -- le superfici dei principali diversi -------------------------------

    def _owner_admin(self, method, path, jar, agency, payload):
        """OWNER Admin per un titolare vero: conti, concessioni, token."""
        import json as _json

        tail = path[len("/api/owner/admin"):].split("?")[0]

        if tail == "/accounts" and method == "POST":
            contact = (payload or {}).get("contact_id")
            row = self.rows.get(contact)
            if row is None or row["agency"] != agency:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            self.next_id += 1
            # Il conto eredita il marcatore del contatto: e' cio' che la lista
            # proietta come `display_name`, quindi e' cio' che la matrice cerca.
            body = {"id": self.next_id, "status": "active",
                    "display_name": self._marker_of(contact)}
            self.rows[self.next_id] = {"agency": agency, "prefix": "/api/owner/admin",
                                       "body": _json.dumps(body)}
            return self._reply(method, path, 201, _json.dumps(body).encode())

        if tail == "/access" and method == "POST":
            account = self.rows.get((payload or {}).get("owner_account_id"))
            prop = self.rows.get((payload or {}).get("property_id"))
            if account is None or prop is None or account["agency"] != agency:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            # IL GRANT INCOERENTE: conto e immobile con radici discordi. Il
            # doppio lo rifiuta perche' e' cio' che fa il repository vero; una
            # mutazione che tolga questa riga deve far fallire la matrice.
            if prop["agency"] != account["agency"] and not self._broken("grant", "owner_admin"):
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            self.next_id += 1
            self.grants = getattr(self, "grants", {})
            self.grants[self.next_id] = (payload["owner_account_id"],
                                         payload["property_id"])
            return self._reply(method, path, 201,
                               _json.dumps({"id": self.next_id}).encode())

        if tail.endswith("/tokens") and method == "POST":
            account = int(tail.split("/")[2])
            row = self.rows.get(account)
            if row is None or row["agency"] != agency:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            token = f"one-time-{account}"
            self.issued[token] = account
            return self._reply(method, path, 200,
                               _json.dumps({"token": token, "token_id": 1}).encode())

        if tail.endswith(("/disable", "/enable")) and method == "POST":
            account = int(tail.split("/")[2])
            row = self.rows.get(account)
            if row is None:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            if row["agency"] != agency and not (
                    self._broken("isolation", "owner_admin")
                    or self._broken("owner_write", "owner_admin")):
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            body = _json.loads(row["body"])
            body["status"] = "disabled" if tail.endswith("/disable") else "active"
            row["body"] = _json.dumps(body)
            return self._reply(method, path, 200, row["body"].encode())

        if tail == "/documents/upload" and method == "POST":
            # Il caricamento richiede lo storage oggetti. "no_storage_backend"
            # modella un TEST in cui OWNER_DOCUMENT_STORAGE_ENABLED e' falso.
            if self._broken("no_storage_backend", "portal"):
                return self._reply(method, path, 503, b'{"detail":"storage non attivo"}')
            self.next_id += 1
            self.shared = getattr(self, "shared", {})
            self.shared[self.next_id] = {"agency": agency, "property": self.upload_property,
                                         "title": self.upload_title,
                                         "published": False, "storage": True}
            # La risposta porta l'id del documento di origine, che e' l'unico
            # modo per risalire allo storage_key: senza, il bucket non si
            # ripulisce e nessuno se ne accorge.
            self.next_id += 1
            origine = self.next_id
            self.caricati.append(origine)
            condiviso = self.next_id - 1
            # La riga esiste comunque nel database: e' NOT NULL nello schema.
            # Che la RISPOSTA la porti o no e' un'altra questione, ed e'
            # esattamente cio' che `upload_senza_origine` mette alla prova.
            self.origine_condivisa[condiviso] = origine
            corpo = {"id": condiviso}
            if self._broken("upload_origine_non_numerica", "portal"):
                # 201, campo presente, valore non convertibile: se la
                # diagnostica lo tocca prima di aver messo al sicuro il
                # canonico, il canonico non viene registrato affatto.
                corpo["property_document_id"] = "abc"
            elif self._broken("upload_origine_sbagliata", "portal"):
                # 201, campo presente, valore di un ALTRO documento: il caso
                # che un controllo "se manca, leggi il database" non vede.
                corpo["property_document_id"] = origine + 7000
            elif not self._broken("upload_senza_origine", "portal"):
                corpo["property_document_id"] = origine
            return self._reply(method, path, 201, _json.dumps(corpo).encode())

        if tail == "/documents" and method == "POST":
            base = self.rows.get((payload or {}).get("property_document_id"))
            if base is None or base["agency"] != agency:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            # LA REGOLA DEL REPOSITORY, MODELLATA.
            #
            # `owner/repository.create_shared_document` rifiuta un documento
            # che non sia `available` E in storage privato:
            #
            #     if src["status"] != "available" or not src.get("storage_key"):
            #         raise ValidationError(...)
            #
            # e `x()` traduce quella ValidationError in 422. Il doppio
            # accettava tutto, e per questo la fixture che condivideva un
            # documento nato da un `url` era verde qui e 422 sul TEST - due run
            # interi spesi a cercare la causa altrove.
            import json as _j
            origine = _j.loads(base["body"])
            if not origine.get("storage_key"):
                return self._reply(
                    method, path, 422,
                    _j.dumps({"detail": "Il documento deve essere disponibile "
                                        "in storage privato"}).encode())
            self.next_id += 1
            self.shared = getattr(self, "shared", {})
            # documento condiviso -> (agenzia, immobile, titolo, pubblicato)
            import json as _j
            self.shared[self.next_id] = {"agency": agency,
                                         "property": _j.loads(base["body"]).get("_property"),
                                         "title": payload["public_title"], "published": False}
            return self._reply(method, path, 201, _json.dumps({"id": self.next_id}).encode())

        if tail.endswith("/publish") and "/documents/" in tail and method == "POST":
            ident = int(tail.split("/")[2])
            doc = getattr(self, "shared", {}).get(ident)
            if doc is None or doc["agency"] != agency:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            doc["published"] = True
            # LA PUBBLICAZIONE NOTIFICA, e questa riga mancava al doppio.
            #
            # `publish_shared_document` chiama `_emit_notification_event`, che
            # inserisce una `owner_notifications` per ogni titolare con
            # concessione attiva - piu' la riga di audit. E' l'effetto su cui
            # il preflight del run 42e32975ccd6 si e' fermato.
            self._effetto("owner_notifications")
            return self._reply(method, path, 200, b'{"status":"published"}')

        if tail.startswith("/accounts") and method == "GET":
            if self._broken("owner_own_list", "owner_admin"):
                # Il titolare non riesce a leggere la PROPRIA lista: la prova
                # positiva deve accorgersene invece di dedurla dal silenzio.
                return self._reply(method, path, 500, b'{"detail":"errore"}')
            visible = [r for r in self.rows.values()
                       if r["prefix"] == "/api/owner/admin"
                       and (r["agency"] == agency
                            or self._broken("listing", "owner_admin")
                            or self._broken("owner_list", "owner_admin"))]
            body = _json.dumps({"items": [_json.loads(r["body"]) for r in visible]})
            return self._reply(method, path, 200, body.encode())

        return self._reply(method, path, 200, b'{"items":[]}')

    def _portal(self, method, path, jar):
        """Il portale: il proprietario vede solo cio' che gli e' concesso."""
        import json as _json

        account = self._portal_account(jar)
        if account is None:
            return self._reply(method, path, 401, b'{"detail":"Non autorizzato"}')
        granted = [prop for acc, prop in getattr(self, "grants", {}).values()
                   if acc == account]

        tail = path[len("/api/owner/portal"):].split("?")[0]
        if tail == "/properties":
            items = [_json.loads(self.rows[p]["body"]) for p in granted
                     if p in self.rows]
            if self._broken("isolation", "portal") or self._broken("portal_list", "portal"):
                items = [_json.loads(r["body"]) for r in self.rows.values()
                         if r["prefix"] == "/api/property"]
            return self._reply(method, path, 200,
                               _json.dumps({"items": items}).encode())

        # /documents/{i}/download e /documents/{i}: 404 se non concesso.
        # "no_storage": anche il proprietario legittimo riceve 404, come su un
        # TEST senza storage - il download ostile deve allora restare BLOCKED.
        found_dl = re.match(r"^/documents/(\d+)(/download)?$", tail)
        if found_dl:
            doc = getattr(self, "shared", {}).get(int(found_dl.group(1)))
            if doc is None or doc["property"] not in granted or not doc["published"]:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            if found_dl.group(2) and (self._broken("no_storage", "portal")
                                      or not doc.get("storage")):
                # Senza oggetto nello storage non c'e' nulla da aprire: e'
                # cosi' che si comporta prepare_shared_document_download su un
                # documento nato da un URL.
                return self._reply(method, path, 404, b'{"detail":"Risorsa non trovata"}')
            if found_dl.group(2):
                # LO SCARICAMENTO REGISTRA LA LETTURA.
                #
                # `prepare_shared_document_download` chiama
                # `read_shared_document`, che fa UPSERT su
                # `owner_document_reads` - una riga per (documento, conto) -
                # piu' l'audit `shared_document_viewed`. Il doppio non la
                # produceva, quindi il perimetro non poteva contenerla e il
                # preflight del run 42e32975ccd6 l'ha trovata estranea.
                self._effetto("owner_document_reads")
            return self._reply(method, path, 200, _json.dumps({"id": doc["title"]}).encode())

        found_docs = re.match(r"^/properties/(\d+)/documents$", tail)
        if found_docs:
            # IL CONTRATTO REALE: SELECT filtrata per conto -> 200 con lista
            # vuota su un immobile non concesso (e su uno inesistente).
            pid = int(found_docs.group(1))
            items = [{"id": i, "public_title": d["title"]}
                     for i, d in getattr(self, "shared", {}).items()
                     if d["property"] == pid and d["published"]
                     and (pid in granted or self._broken("portal_documents", "portal"))]
            corpo = {"items": items}
            if pid not in granted:
                # "docs_anon_leak": lista non vuota MA senza il marcatore -
                # un documento altrui con titolo neutro. Solo il controllo
                # "vuota" lo vede.
                if self._broken("docs_anon_leak", "portal"):
                    corpo["items"] = [{"id": 0, "public_title": "documento"}]
                # "docs_marker_leak": items vuoto MA il marcatore altrui
                # compare altrove nel corpo. Solo il controllo sul marcatore lo vede.
                if self._broken("docs_marker_leak", "portal"):
                    corpo["debug"] = [d["title"] for d in getattr(self, "shared", {}).values()
                                      if d["property"] == pid]
            elif self._broken("docs_hide_own", "portal"):
                corpo["items"] = []
            return self._reply(method, path, 200, _json.dumps(corpo).encode())

        found = re.search(r"/properties/(\d+)", tail)
        if found:
            identifier = int(found.group(1))
            if identifier not in granted and not (
                    self._broken("isolation", "portal") or self._broken("portal_detail", "portal")):
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            row = self.rows.get(identifier)
            if row is None:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            return self._reply(method, path, 200, row["body"].encode())

        return self._reply(method, path, 200, b'{"items":[]}')

    def _property_watch(self, method, path, agency):
        # Le stime create dal run nelle agenzie dedicate: la proprieta' viene
        # dal doppio del database, non da `self.stime`.
        creata = getattr(self, "stime_dedicate", {})
        found_d = re.search(r"/stime/(\d+)", path)
        if found_d and int(found_d.group(1)) in creata:
            identifier = int(found_d.group(1))
            estraneo = creata[identifier] != agency
            if estraneo and method == "POST" and self._broken("watch_write_leak", "watch"):
                return self._reply(method, path, 200, b'{"initialized":true}')
            # `watch_read`/`watch_write` anche QUI, non solo sulle stime
            # preesistenti. Da quando la fixture crea una stima osservabile
            # per A e per B, le prove ostili passano da questo ramo: senza
            # queste due manopole `test_39` non riusciva piu' a rompere il
            # proprio bersaglio, cioe' la sonda era diventata inaffondabile -
            # che e' il modo peggiore in cui una prova puo' "passare".
            kind = "watch_write" if method == "POST" else "watch_read"
            if estraneo and not (self._broken("isolation", "watch")
                                 or self._broken(kind, "watch")):
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            if method == "POST" and path.endswith("/initialize"):
                # LA VALUTAZIONE COMPLETATA E' OBBLIGATORIA, come nel
                # servizio vero: `_baseline_for_stima_scoped` legge
                # `seller_timeline_events` con event_type 'stima_completata'
                # e, se non la trova, solleva ValidationError -> 400. Un
                # doppio che inizializzasse comunque avrebbe risposto 200 a
                # cio' che sul TEST prendeva 400.
                if self._stato("senza_initialize"):
                    return self._reply(method, path, 400, b'{"detail":"initialize sospeso"}')
                if identifier not in self.stime_valutate:
                    return self._reply(method, path, 400, _json.dumps(
                        {"detail": f"completed valuation not found for stima "
                                   f"{identifier}"}).encode())
                self.watch_id = getattr(self, "watch_id", 7700) + 1
                self.watch_vivi[self.watch_id] = agency
                # Il doppio del database deve poter rileggere il watch per
                # stima: e' l'id su cui il cleanup delle agenzie CONDIVISE
                # cancella, e senza questa riga la fixture si fermerebbe
                # dicendo "watch creato ma non rileggibile".
                if self.stato_db is not None:
                    self.stato_db.setdefault(
                        "watch_per_stima", {})[identifier] = self.watch_id
                # IL WATCH NON NASCE DA SOLO.
                #
                # `ensure_watch_with_baseline_scoped` scrive, nella STESSA
                # transazione, il watch e una osservazione `watch_started` la
                # cui chiave di idempotenza si deriva dallo stima_id. Il doppio
                # non la creava, e per questo la matrice poteva dichiararsi
                # verde mentre sul TEST il preflight si fermava proprio li':
                # il run 58aa0e189aaa ha bloccato l'intero cleanup su due righe
                # che questo ramo non sapeva di produrre.
                self.watch_stima[self.watch_id] = identifier
                self.osservazione_id = getattr(self, "osservazione_id", 8800) + 1
                self.osservazioni[self.osservazione_id] = {
                    "watch_id": self.watch_id,
                    "idempotency_key":
                        cert.WATCH_BASELINE_KEY.format(stima_id=identifier),
                    "observation_type": cert.WATCH_BASELINE_TYPE,
                    "source": cert.WATCH_BASELINE_SOURCE,
                }
            return self._reply(method, path, 200,
                               f'{{"stima_id":{identifier},"watch":true}}'.encode())
        return self._property_watch_preesistenti(method, path, agency)

    def _property_watch_preesistenti(self, method, path, agency):
        """Le stime: esistono gia', e appartengono a un'agenzia."""
        found = re.search(r"/stime/(\d+)", path)
        identifier = int(found.group(1))
        owner = next((label for label, sid in self.stime.items() if sid == identifier),
                     None)
        if owner is None:
            return self._reply(method, path, 404, b'{"detail":"non trovata"}')
        if owner != agency:
            kind = "watch_write" if method == "POST" else "watch_read"
            if not (self._broken("isolation", "watch") or self._broken(kind, "watch")):
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
        return self._reply(method, path, 200,
                           f'{{"stima_id":{identifier},"watch":true}}'.encode())

    def _link_contact(self, method, path, agency, property_id, contact_id):
        """Il legame proprietario: entrambe le righe devono essere del chiamante.

        Due controlli e non uno. Un isolamento che guardasse solo l'immobile
        lascerebbe agganciare il contatto di un'altra agenzia al proprio
        immobile, e da li' `property_sale_sellers` porterebbe il nome di un
        estraneo dentro una vendita.
        """
        prop = self.rows.get(property_id)
        contact = self.rows.get(contact_id)
        if prop is None or contact is None:
            return self._reply(method, path, 404, b'{"detail":"non trovata"}')
        estranei = prop["agency"] != agency or contact["agency"] != agency
        if estranei and not self._broken("isolation", "relation"):
            return self._reply(method, path, 404, b'{"detail":"non trovata"}')
        self.owner_links.add((property_id, contact_id))
        return self._reply(method, path, 201,
                           f'{{"property_id":{property_id},"contact_id":{contact_id},'
                           f'"role":"owner"}}'.encode())

    def _create_sale(self, method, path, agency, payload):
        """La vendita, con la precondizione che il run live ha scoperto.

        `create_sale_scoped` cerca in `property_contacts` un ruolo owner/seller
        per quell'immobile, nella stessa agenzia. Senza nemmeno una riga
        solleva ConflictError -> 409, ed e' l'ultima delle cinque precondizioni.
        """
        import json as _json

        proposal_id = payload.get("proposal_id")
        proposal = self.rows.get(proposal_id)
        if proposal is None or proposal["agency"] != agency:
            return self._reply(method, path, 404, b'{"detail":"non trovata"}')
        property_id = self.proposal_property.get(proposal_id)
        venditori = [link for link in self.owner_links if link[0] == property_id]
        if not venditori:
            return self._reply(
                method, path, 409,
                b'{"detail":"no eligible seller/owner registered for this property"}')
        self.next_id += 1
        body = {"id": self.next_id, "notes": payload.get("notes")}
        self.rows[self.next_id] = {"agency": agency, "prefix": "/api/sales",
                                   "body": _json.dumps(body)}
        return self._reply(method, path, 201, _json.dumps(body).encode())

    def _scan_temporal(self, method, path, agency):
        """La scansione: candidate della PROPRIA agenzia, escalate, restituite.

        Il predicato e' quello di followup/repository.py, ridotto a cio' che
        le fixture della matrice esercitano: aperta, priorita' bassa, tipo
        `automated_followup`, sorgente e regola nel metadata. Con
        `broken={"scan_scope"}` la scansione ignora il tenant: e' il difetto
        che la matrice deve saper vedere.
        """
        import json as _json

        if not self.rule_enabled:
            return self._reply(method, path, 400,
                               b'{"detail":"followup rule is not enabled"}')
        items = []
        for identifier, row in self.rows.items():
            if not row.get("task"):
                continue
            body = _json.loads(row["body"])
            foreign = row["agency"] != agency
            # "scan_mutates_other": modifica la riga altrui SENZA elencarla.
            # E' il caso che solo l'istantanea puo' vedere: gli id restituiti
            # sono puliti, il danno c'e' lo stesso.
            if foreign and self._broken("scan_mutates_other", "followup"):
                body["priority"], body["status"] = "high", "in_progress"
                row["body"] = _json.dumps(body)
                continue
            if foreign and not self._broken("scan_scope", "followup"):
                continue
            # "scan_skips_own": la scansione non elabora nemmeno le proprie.
            if not foreign and self._broken("scan_skips_own", "followup"):
                continue
            eligible = (body.get("status") == "open" and body.get("priority") in ("low", "normal")
                        and body.get("task_type") == "automated_followup"
                        and (body.get("metadata") or {}).get("source") == "followup")
            if not eligible:
                continue
            # "scan_lies": nell'elenco come 'completed', ma il task non viene
            # toccato. E' il caso del run 9b95b3ee215e: fixture C rimasta
            # open/low con la prova segnata PASS.
            if not self._broken("scan_lies", "followup"):
                body["priority"], body["status"] = "high", "in_progress"
                row["body"] = _json.dumps(body)
            stato_elemento = ("failed" if self._broken("scan_item_fails", "followup")
                              else "completed")
            items.append({"task_id": identifier, "status": stato_elemento,
                          "idempotency_key": f"followup:time:RULE:task:{identifier}:v1"})
        # "intruso_dopo_preflight": un task ESTRANEO che ha attraversato la
        # soglia delle 24 ore mentre il run procedeva. Il preflight lo aveva
        # contato a zero - correttamente, allora non era eleggibile - e nessun
        # secondo conteggio potrebbe arrivare prima della scansione.
        #
        # La scansione lo elabora davvero: e' la riga di qualcun altro portata
        # a 'high'/'in_progress'. La matrice deve accorgersene guardando QUALI
        # id sono tornati, non quanti.
        if self._broken("intruso_dopo_preflight", "followup"):
            self.next_id += 1
            items.insert(0, {"task_id": self.next_id, "status": "completed",
                             "idempotency_key": f"followup:time:RULE:task:{self.next_id}:v1"})
        out = {"agency_id": agency, "scanned": len(items), "escalated": len(items),
               "skipped": 0, "failed": 0, "items": items}
        return self._reply(method, path, 200, _json.dumps(out).encode())

    def _calculate(self, method, path, jar, agency, payload):
        """Il calcolo del match: nasce dalla coppia, ed eredita i marcatori."""
        import json as _json

        buy = self.rows.get(payload.get("buy_request_id"))
        prop = self.rows.get(payload.get("property_id"))
        if buy is None or prop is None or buy["agency"] != agency:
            return self._reply(method, path, 404, b'{"detail":"non trovata"}')
        self.next_id += 1
        body = {"id": self.next_id,
                "buy_title": self._marker_of(payload["buy_request_id"]),
                "property_title": self._marker_of(payload["property_id"])}
        self.rows[self.next_id] = {"agency": agency, "prefix": "/api/match",
                                   "body": _json.dumps(body)}
        self.match_property[self.next_id] = payload["property_id"]
        return self._reply(method, path, 201, _json.dumps(body).encode())

    def request(self, method, path, *, jar=None, payload=None):
        import json as _json

        # PRIMA DI TUTTO: la route esiste, con questo metodo?
        # Starlette risponde 404 a un percorso sconosciuto e 405 a un metodo
        # non previsto, e lo fa PRIMA di qualunque dipendenza. Un doppio che
        # saltasse questo passo accetterebbe route inventate - ed e' cosi' che
        # tre sonde rotte sono arrivate fino al TEST.
        refusal, _ = resolve_route(method, path)
        if refusal is not None:
            return self._reply(method, path, refusal, b'{"detail":"non disponibile"}')

        if path == cert.PUBLIC:
            return self._reply(method, path, 200, b"{}")

        if path == cert.LOGIN:
            token = f"token-{payload['email']}"
            if payload["email"] not in self.logins:
                # Operatore di un'agenzia dedicata: l'agenzia si ricava dal
                # suffisso dello slug registrato alla creazione.
                self.logins[payload["email"]] = self.dedicate.get(
                    payload["email"], 0) or 0
            self.sessions[token] = self.logins[payload["email"]]
            self._seed(self.logins[payload["email"]])
            self.put_cookie(jar, token)
            return self._reply(method, path, 204)

        if path == cert.LOGOUT:
            return self._reply(method, path, 204)

        # -- IL PORTALE: principale diverso, cookie diverso -----------------
        if path == cert.PORTAL_LOGIN:
            account = self.issued.get((payload or {}).get("token"))
            if account is None:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            session = f"portal-{account}"
            self.portal[session] = account
            self.put_cookie(jar, session, cert.OWNER_COOKIE_NAME)
            return self._reply(method, path, 204)

        if path == cert.PORTAL_LOGOUT:
            return self._reply(method, path, 204)

        if path.startswith("/api/owner/portal"):
            return self._portal(method, path, jar)

        agency = self._agency_of(jar)
        if agency is None:
            return self._reply(method, path, 401, b'{"detail":"Non autorizzato"}')

        if path == cert.ME:
            return self._reply(method, path, 200,
                               _json.dumps({"agency_id": agency}).encode())

        if path.startswith("/api/owner/admin"):
            if (self._role_of(jar) != cert.OWNER_ADMIN_MIN_ROLE
                    and not self._broken("threshold", "owner_admin")):
                # La soglia: agency_admin non entra, e il rifiuto arriva prima
                # di ogni domanda sullo scope.
                return self._reply(method, path, 403, b'{"detail":"riservato"}')
            return self._owner_admin(method, path, jar, agency, payload)

        if self._broken("listing_500") and path.startswith("/api/admin/"):
            # Una route ROTTA, non isolata male: e' il 500 di LEGACY_ADMIN nel
            # run 38e341f68f8a. Il corpo somiglia a quello che psycopg2 lascia
            # arrivare quando la query non e' eseguibile.
            return self._reply(method, path, 500, (
                b'{"detail":"UndefinedColumn: column s.note_internal does not '
                b'exist\nLINE 3: SELECT s.id, s.note_internal"}'))
        if path.startswith("/api/property-watch/stime/"):
            return self._property_watch(method, path, agency)

        found = re.match(r"^/api/property/properties/(\d+)/contacts$", path)
        if found and method == "POST":
            return self._link_contact(method, path, agency, int(found.group(1)),
                                      (payload or {}).get("contact_id"))

        if path == cert.FOLLOWUP_SCAN and method == "POST":
            return self._scan_temporal(method, path, agency)

        if path.startswith("/api/core/tasks") and method == "GET":
            wanted = re.search(r"contact_id=(\d+)", path)
            items = [_json.loads(r["body"]) | {"id": i}
                     for i, r in self.rows.items()
                     if r["prefix"] == "/api/core" and r.get("task")
                     and (r["agency"] == agency or self._broken("listing"))
                     and (not wanted or _json.loads(r["body"]).get("contact_id") == int(wanted.group(1)))]
            return self._reply(method, path, 200, _json.dumps({"items": items}).encode())

        if path == "/api/sales" and method == "POST":
            return self._create_sale(method, path, agency, payload or {})

        if path == "/api/flow/events" and method == "POST":
            # IL CONTRATTO VERO, non quello che il doppio immagina.
            #
            # `EventCreate` esige `source_module` fra sei valori e senza
            # predefinito. Un doppio che accettasse qualunque payload avrebbe
            # risposto 201 a quello che sul TEST prendeva 422 - ed e'
            # esattamente cio' che e' successo al run 38e341f68f8a.
            import pydantic
            from flow.schemas import EventCreate
            try:
                EventCreate(**(payload or {}))
            except pydantic.ValidationError as exc:
                return self._reply(method, path, 422, _json.dumps(
                    {"detail": [{"loc": list(e["loc"]), "type": e["type"]}
                                for e in exc.errors()]}).encode())
            self.next_id += 1
            # Registrata anche nel doppio del database: e' cio' che rende la
            # riga cancellabile - e quindi contabile - invece di un id che
            # nessuna query conosce.
            self.flow_events_vivi[self.next_id] = agency
            evento = self.next_id
            if payload.get("deduplication_key"):
                self.flow_chiavi.setdefault(
                    payload["deduplication_key"], []).append(evento)
            self.rows[evento] = {"agency": agency, "prefix": "/api/flow",
                                 "body": _json.dumps({"id": evento,
                                                      "event_type": payload["event_type"]})}
            # L'ESECUZIONE NASCE QUI, ALLE CONDIZIONI DEL BACKEND VERO.
            #
            # `_process_saved_event` crea una riga per ogni regola ATTIVA il cui
            # `event_type` E `entity_type` corrispondano - e la crea PRIMA di
            # sapere se la regola corrisponde nel merito, chiudendola
            # `not_matched` quando non corrisponde. Un doppio che creasse
            # un'esecuzione per qualunque evento avrebbe fatto passare la
            # fixture col marcatore nel tipo, che sul TEST non ne produce
            # nessuna: e' il BLOCKED di ogni run fino a 42e32975ccd6.
            if (payload.get("event_type") == cert.Certification.FLOW_EXECUTION_TRIGGER
                    and payload.get("entity_type") == cert.Certification.FLOW_EXECUTION_ENTITY):
                self.next_id += 1
                self.flow_esecuzioni[self.next_id] = {
                    "agency": agency, "event_id": evento, "retry_of": None,
                    # `not_matched` perche' `next_action_at` e' NULL: e' la
                    # semantica di flow/engine.py per R004, non una scelta del
                    # doppio.
                    "status": ("matched" if self.stato_db.get("buy_next_action")
                               else "not_matched"),
                    "rule_code": cert.Certification.FLOW_EXECUTION_RULE,
                }
            return self._reply(method, path, 201,
                               _json.dumps({"id": evento}).encode())

        trovata = re.match(r"^/api/flow/executions/(\d+)(/retry)?$", path)
        if trovata:
            identificativo = int(trovata.group(1))
            esecuzione = self.flow_esecuzioni.get(identificativo)
            if esecuzione is None:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            mia = esecuzione["agency"] == agency or self._broken("flow_leak", "generic")
            if not mia:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            if trovata.group(2):
                # Un retry riuscito RIESEGUE l'automazione: crea una riga nuova
                # che porta il legame con l'originale. Il doppio la
                # materializza, cosi' la sonda che la cerca non guarda nel
                # vuoto.
                self.next_id += 1
                self.flow_esecuzioni[self.next_id] = {
                    **esecuzione, "retry_of": identificativo}
                return self._reply(method, path, 200,
                                   _json.dumps({"id": self.next_id}).encode())
            return self._reply(method, path, 200, _json.dumps(
                {"id": identificativo, "status": esecuzione["status"],
                 "rule_code": esecuzione["rule_code"]}).encode())

        if path.startswith("/api/flow/executions") and method == "GET":
            visibili = [
                {"id": i, "status": e["status"], "rule_code": e["rule_code"],
                 "entity_type": cert.Certification.FLOW_EXECUTION_ENTITY}
                for i, e in sorted(self.flow_esecuzioni.items())
                if e["agency"] == agency or self._broken("flow_leak", "generic")]
            if self._broken("flow_hide_own", "generic"):
                visibili = [v for v in visibili
                            if self.flow_esecuzioni[v["id"]]["agency"] != agency]
            return self._reply(method, path, 200,
                               _json.dumps({"items": visibili}).encode())

        if path.startswith("/api/flow/events") and method == "GET":
            visibili = [_json.loads(r["body"]) for r in self.rows.values()
                        if r["prefix"] == "/api/flow"
                        and (r["agency"] == agency or self._broken("flow_leak", "batch"))]
            if self._broken("flow_hide_own", "batch"):
                # La lista nasconde i propri: il confronto col marcatore
                # altrui sarebbe vero per la ragione sbagliata.
                visibili = [v for v in visibili if v["agency_marker"] != agency] \
                    if visibili and "agency_marker" in visibili[0] else []
            return self._reply(method, path, 200,
                               _json.dumps({"items": visibili}).encode())

        if path == "/api/next-best-action/refresh" and method == "POST":
            if self._broken("nba_vuoto", "batch"):
                return self._reply(method, path, 200, b'{"created":0}')
            self.next_id += 1
            self.nba = getattr(self, "nba", {})
            self.nba.setdefault(agency, []).append(self.next_id)
            return self._reply(method, path, 200, b'{"created":1}')

        if path.startswith("/api/next-best-action?") and method == "GET":
            miei = getattr(self, "nba", {}).get(agency, [])
            if self._broken("nba_leak", "batch"):
                miei = [i for lista in getattr(self, "nba", {}).values() for i in lista]
            return self._reply(method, path, 200,
                               _json.dumps({"items": [{"id": i} for i in miei]}).encode())

        if path.startswith("/api/match/readiness"):
            if self._broken("readiness", "generic"):
                # Il motore dice perche' non puo' calcolare. Il messaggio e'
                # la parte utile: senza, il report direbbe solo "400".
                return self._reply(method, path, 200, _json.dumps({
                    "can_match": False, "ready": False, "eligible": True,
                    "buy": {"reasons": [cert_reason()]},
                    "property": {"reasons": []},
                }).encode())
            return self._reply(method, path, 200, _json.dumps({
                "can_match": True, "ready": True, "eligible": True,
                "buy": {"reasons": []}, "property": {"reasons": []},
            }).encode())

        if path == "/api/match/calculate":
            return self._calculate(method, path, jar, agency, payload or {})

        if path.startswith("/api/proposals/") and path.endswith("/transition"):
            identifier = int(path.split("/")[3])
            row = self.rows.get(identifier)
            if row is None or (row["agency"] != agency
                               and not self._broken("isolation")):
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            return self._reply(method, path, 200, row["body"].encode())

        # Creazione di una fixture.
        if method == "POST" and payload is not None:
            self.next_id += 1
            # Le righe portano il proprio prefisso: una lista restituisce solo
            # quelle del proprio dominio. Un doppio che mostrasse ogni risorsa
            # su ogni lista renderebbe non vuote anche le liste dei domini
            # derivati, e il caso "lista vuota" - quello in cui il confronto col
            # marcatore non prova nulla - non verrebbe mai esercitato.
            self.rows[self.next_id] = {"agency": agency, "prefix": _prefix_of(path),
                                       "body": _json.dumps(payload)}
            if path == "/api/proposals":
                self.proposal_property[self.next_id] = self.match_property.get(
                    payload.get("match_id"))
            if path == "/api/core/tasks":
                self.rows[self.next_id]["task"] = True
            if path == "/api/core/leads":
                # Il lead della fixture NEXT_BEST_ACTION: il doppio del
                # database deve conoscerlo, altrimenti l'istantanea delle
                # agenzie dedicate non lo vede e la guardia non interroga mai
                # le sue otto figlie SET NULL.
                self.lead_vivi[self.next_id] = agency
            if path == "/api/core/contacts":
                # Genitore di seller_revival_suppressions: il doppio del
                # database deve saperlo per poterlo fotografare.
                self.contatti_vivi[self.next_id] = agency
            found_doc = re.match(r"^/api/property/properties/(\d+)/documents$", path)
            if found_doc:
                body = _json.loads(self.rows[self.next_id]["body"])
                body["_property"] = int(found_doc.group(1))
                self.rows[self.next_id]["body"] = _json.dumps(body)
            return self._reply(method, path, 201,
                               _json.dumps({"id": self.next_id, **payload}).encode())

        # Accesso per id.
        match = re.search(r"/(\d+)(?:/|$|\?)", path)
        if match:
            identifier = int(match.group(1))
            row = self.rows.get(identifier)
            if row is None:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            foreign = row["agency"] != agency
            if foreign and not self._broken("isolation"):
                # "status": risponde 200 su una risorsa altrui ma NON ne rivela
                # il contenuto. E' la perdita piu' sottile - un contatore, un
                # ETag, la semplice conferma che l'id esiste - e senza una
                # modalita' apposta la matrice sembrerebbe accorgersene mentre
                # in realta' se ne accorge solo il controllo sul marcatore.
                if self._broken("status") and method == "GET":
                    return self._reply(method, path, 200, b'{"ok":true}')
                # "write": rifiuta a parole e scrive comunque. E' il caso
                # peggiore, perche' il chiamante vede un 404 e il danno resta.
                if self._broken("write") and method in ("PATCH", "PUT"):
                    row["body"] = _json.dumps(payload)
                    return self._reply(method, path, 404, b'{"detail":"non trovata"}')
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            if method in ("PATCH", "PUT", "POST") and payload is not None:
                row["body"] = _json.dumps(payload)
            if method == "DELETE":
                # "soft_delete": risponde 2xx e NON rimuove la riga. E' cio' che
                # fanno `archive_property` e `archive_request`, ed e' il difetto
                # che ha lasciato quattro righe sul TEST senza segnalarle.
                if self._broken("soft_delete"):
                    return self._reply(method, path, 200, row["body"].encode())
                self.rows.pop(identifier, None)
                return self._reply(method, path, 204)
            return self._reply(method, path, 200, row["body"].encode())

        # Liste e ricerche: solo le risorse del dominio interrogato.
        prefix = _prefix_of(path)
        visible = [r for r in self.rows.values()
                   if r["prefix"] == prefix
                   and (r["agency"] == agency or self._broken("listing"))]
        body = _json.dumps({"items": [_json.loads(r["body"]) for r in visible]}).encode()
        return self._reply(method, path, 200, body)


#: I due titolari reali del TEST simulato: un agency_owner per agenzia.
#: Senza questi, OWNER Admin e il portale restano BLOCKED - che e' il
#: comportamento corretto ma non esercita nulla.
OWNERS = {1: 7001, 2: 7002}

#: Le stime che ciascuna agenzia gia' possiede. Chiave: agency_id.
STIME = {1: 9001, 2: 9002}


def working_run(monkeypatch, database=None, http=None, owners=OWNERS,
                stime=STIME, dedicated_agencies=False, **state):
    """Esegue `run()` su doppi che isolano correttamente."""
    report, stream = quiet_report()
    database = database or fake_database(agencies=AGENCIES, owners=owners,
                                         stime=stime, **state)
    monkeypatch.setattr(cert, "_git",
                        lambda *a: "abc123" if a[0] == "rev-parse" else cert.APPROVED_BRANCH)

    probe = http or FakeHttp()
    probe.logins = getattr(probe, "logins", {})
    # Le stime del doppio HTTP sono le stesse che `derive_stime` legge dal
    # doppio del database: se divergessero, la matrice proverebbe l'isolamento
    # su righe che il database non conosce.
    if not probe.stime:
        probe.stime = dict(stime or {})

    def factory(base):
        probe.base = base.rstrip("/")
        return probe

    # Le sessioni di titolare: il doppio deve sapere che quel token porta il
    # ruolo agency_owner, altrimenti OWNER Admin risponderebbe 403 anche a chi
    # ha il diritto di entrarci e la prova sullo scope non verrebbe mai esercitata.
    # Le agenzie dedicate: il doppio deve sapere che l'operatore appena
    # creato appartiene all'agenzia appena creata.
    # Le stime inserite via SQL nelle agenzie dedicate: il doppio HTTP deve
    # sapere a chi appartengono.
    original_cursor_write = cert.Database.write

    original_create_dedicated = cert.Certification.create_dedicated_agency

    def create_dedicated(self, label):
        agency = original_create_dedicated(self, label)
        if agency is not None:
            probe.dedicate[agency["email"]] = agency["id"]
            probe.logins[agency["email"]] = agency["id"]
        return agency

    monkeypatch.setattr(cert.Certification, "create_dedicated_agency", create_dedicated)

    probe.stime_dedicate = database.state.setdefault("stime_create", {})
    database.state["documenti_caricati"] = probe.caricati
    # Un solo dizionario per i due doppi: cio' che l'HTTP crea, l'SQL lo
    # conta e lo cancella. Con due copie separate il cleanup avrebbe potuto
    # dimenticarsi una tabella senza che nessun test se ne accorgesse.
    probe.flow_events_vivi = database.state.setdefault("flow_events_vivi", {})
    probe.flow_chiavi = database.state.setdefault("flow_chiavi", {})
    probe.flow_esecuzioni = database.state.setdefault("flow_esecuzioni", {})
    probe.watch_vivi = database.state.setdefault("watch_vivi", {})
    probe.watch_stima = database.state.setdefault("watch_stima", {})
    probe.lead_vivi = database.state.setdefault("lead_dedicati", {})
    probe.osservazioni = database.state.setdefault("osservazioni", {})
    probe.stime_valutate = database.state.setdefault("stime_valutate", set())
    probe.origine_condivisa = database.state.setdefault("origine_condivisa", {})
    # LE RIGHE DEGLI EFFETTI, materializzate come il run le crea davvero.
    #
    # Senza, l'istantanea non trova nulla, le cancellazioni degli effetti non
    # partono e ogni prova su di esse e' soddisfatta dal vuoto. `followup_actions`
    # compare SOLO con le agenzie dedicate, che e' l'unico caso in cui il run ne
    # scrive: e' anche cio' che tiene in piedi la garanzia "sulle agenzie
    # condivise FOLLOWUP non viene mai toccata".
    predefiniti = {
        "property_documents": [5101, 5102],
        "owner_shared_documents": [6101, 6102],
        "property_contacts": [2201, 2202],
        "property_status_history": [8501, 8502],
        "buy_request_history": [11301, 11302],
        "match_requirement_results": [18501, 18502],
        "match_runs": [2701, 2702],
        "owner_audit_log": [5601, 5602],
        "property_sale_sellers": [9101, 9102],
        "owner_property_access": [9201, 9202],
        "owner_access_tokens": [9301, 9302],
        "owner_sessions": [9401, 9402],
        "agency_memberships": [9501, 9502],
        "operator_sessions": [9601, 9602],
    }
    if dedicated_agencies:
        predefiniti["followup_actions"] = [9701, 9702]
    database.state.setdefault("effetti_righe", predefiniti)
    # Notifiche e letture NON stanno in `predefiniti`: le PRODUCE il percorso,
    # pubblicando e scaricando, come fa il backend vero. Dichiararle qui le
    # renderebbe presenti anche in un run che non pubblica nulla, e la prova
    # che il perimetro le raccoglie diventerebbe una prova sul dizionario.
    probe.effetti_prodotti = database.state["effetti_righe"]
    probe.stato_db = database.state
    # I contatti, genitori di seller_revival_suppressions. Registrati tutti,
    # con la loro agenzia: e' la query a filtrare quelli delle dedicate.
    probe.contatti_vivi = database.state.setdefault("contatti_dedicati", {})

    # Lo storage oggetti: un doppio che registra le chiavi cancellate, cosi'
    # un test puo' verificare che il bucket venga davvero ripulito.
    class StorageDoppio:
        def __init__(self, stato):
            self.stato = stato
        def delete_object(self, chiave):
            if self.stato.get("storage_rotto"):
                raise RuntimeError("bucket irraggiungibile")
            self.stato.setdefault("chiavi_cancellate", []).append(chiave)

    stato = database.state
    if stato.get("storage_assente"):
        monkeypatch.setattr("owner.document_storage.get_document_storage",
                            lambda: (_ for _ in ()).throw(RuntimeError("non configurato")))
    else:
        monkeypatch.setattr("owner.document_storage.get_document_storage",
                            lambda: StorageDoppio(stato))

    original_open = cert.OwnerSessions.open

    def open_session(self, user_id):
        raw = original_open(self, user_id)
        agency = next((a for a, u in (owners or {}).items() if u == user_id), None)
        probe.sessions[raw] = agency
        probe.roles[raw] = cert.OWNER_ADMIN_MIN_ROLE
        return raw

    monkeypatch.setattr(cert.OwnerSessions, "open", open_session)

    # La guardia read-only di FOLLOWUP interroga il repository vero, che qui
    # non ha un database: `stale` e' cio' che quella lettura restituirebbe.
    stale = dict(state.pop("stale_followup", {}))
    monkeypatch.setattr(cert, "stale_followup_candidates",
                        lambda agency_id: list(stale.get(agency_id, [])))

    # Le due identita' create vanno mappate sulle agenzie del doppio HTTP.
    original_create = cert.Certification.create_operator

    def create(self, agency):
        operator = original_create(self, agency)
        probe.logins = getattr(probe, "logins", {})
        probe.logins[operator["email"]] = agency["id"]
        return operator

    monkeypatch.setattr(cert.Certification, "create_operator", create)

    # `RENDER_GIT_COMMIT` c'e' su Render e la sua assenza e' BLOCKED: qui va
    # fornito, altrimenti ogni run di prova sarebbe INCOMPLETO per una ragione
    # che non ha nulla a che vedere con l'isolamento.
    env = {"DB_NAME": cert.REQUIRED_DB_NAME, "RENDER_GIT_BRANCH": cert.APPROVED_BRANCH,
           "RENDER_GIT_COMMIT": "abc123",
           "RENDER_EXTERNAL_URL": "https://test.example"}
    code = cert.run(report, database, env, "abc123", http_factory=factory,
                    dedicated_agencies=dedicated_agencies)
    return code, report, database, probe, stream


# ---------------------------------------------------------------------------
# 1-3 - sintassi, import, entry point
# ---------------------------------------------------------------------------

def test_1_the_script_compiles(tmp_path):
    py_compile.compile(str(SCRIPT), cfile=str(tmp_path / "out.pyc"), doraise=True)


def test_2_the_module_imports_without_doing_anything():
    """Importarlo non deve connettersi ne' leggere l'ambiente: tutto il lavoro
    sta dietro `main()`, che e' anche cio' che rende verificabile il resto."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    calls = [
        ast.unparse(n) for n in tree.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
    ]
    assert len(calls) == 1 and calls[0].startswith("sys.path.insert"), calls


def test_3_the_entry_point_is_guarded_and_does_not_hard_exit():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in source
    assert "raise SystemExit(main())" in source
    # os._exit salterebbe il finally del cleanup.
    assert "os._exit" not in source


# ---------------------------------------------------------------------------
# 4-7 - COMPLETEZZA: la matrice contro l'inventario ricavato dal codice
# ---------------------------------------------------------------------------

def _mounted_tenant_prefixes() -> set[str]:
    """I prefissi dei router tenant montati, letti da main.py e dai router.

    Derivato dal codice e non da una lista scritta a mano: un elenco compilato
    dallo stesso autore della matrice non e' una prova di completezza, e' la
    stessa dimenticanza scritta due volte.
    """
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    mounted = set(re.findall(r"app\.include_router\((\w+)", main_source))

    prefixes = set()
    for path in sorted(ROOT.glob("*/router.py")):
        module = path.parent.name
        symbol = f"{module}_router"
        text = path.read_text(encoding="utf-8")
        # Entrambi gli stili di virgoletta: `buy/router.py` e
        # `property/router.py` usano gli apici singoli, e un pattern che
        # accettasse solo le doppie li renderebbe INVISIBILI a questo
        # inventario - due router di tenant montati che non risultano montati.
        # E' esattamente il modo in cui una prova di completezza smette di
        # esserlo restando verde.
        found = re.search(r"""APIRouter\(\s*prefix=["']([^"']+)["']""", text)
        if not found:
            continue
        prefix = found.group(1)
        # `operator_auth` e' l'autenticazione stessa, non un dominio di tenant.
        if prefix == "/api/operator-auth":
            continue
        if symbol in mounted or module == "flow":
            prefixes.add(prefix)

    # OWNER: due router, prefissi dichiarati nei rispettivi moduli. Entrambi
    # sono superfici di tenant. Il portale autentica un principale diverso - un
    # proprietario, non un operatore - e per un intero giro di revisione questo
    # e' bastato a tenerlo fuori dalla matrice. Non basta: un proprietario vede
    # immobili che appartengono a un'agenzia, quindi l'isolamento fra agenzie
    # su quella superficie e' una domanda con una risposta.
    prefixes.add("/api/owner/admin")
    prefixes.add("/api/owner/portal")
    # Le route @app che toccano dati di tenant.
    if re.search(r'@app\.\w+\("/api/admin/stime"', main_source):
        prefixes.add("/api/admin")
    return prefixes


def test_4_the_matrix_covers_every_mounted_tenant_prefix():
    """LA PROVA DI COMPLETEZZA.

    Un dominio montato e non elencato in `DOMAINS` significa una superficie che
    la matrice ostile non guarda - e un PASS che non dice nulla su di essa.
    L'inventario viene ricavato dal codice, quindi aggiungere un router e
    dimenticare la matrice fa fallire questa prova invece di passare in
    silenzio.
    """
    declared = {domain.prefix for domain in cert.DOMAINS}
    mounted = _mounted_tenant_prefixes()

    missing = mounted - declared
    assert missing == set(), (
        f"domini montati e non coperti dalla matrice ostile: {sorted(missing)}"
    )

    # Nessuna esenzione: l'elenco calcolato e quello dichiarato devono
    # coincidere. L'esenzione che stava qui mascherava il difetto del pattern
    # sopra - i due router con gli apici singoli non comparivano fra i montati,
    # e la loro presenza nella matrice sembrava un'invenzione da perdonare.
    invented = declared - mounted
    assert invented == set(), (
        f"la matrice elenca prefissi che non risultano montati: {sorted(invented)}"
    )


def test_5_the_portal_is_in_the_matrix_and_cannot_leave_it_silently():
    """L'inverso di cio' che questo test diceva prima.

    Diceva: il portale e' deliberatamente assente perche' autentica
    proprietari, non operatori. Vero, e non sufficiente - un proprietario vede
    immobili che appartengono a un'agenzia, quindi "A vede solo A" e' una
    domanda con una risposta, e non porla lasciava scoperta l'unica superficie
    di P26 raggiungibile da qualcuno che non lavora in agenzia.

    Adesso c'e', con un principale suo. Questo test e' cio' che impedisce che
    torni a sparire.
    """
    declared = {domain.prefix for domain in cert.DOMAINS}
    assert "/api/owner/portal" in declared
    portal = _domain("OWNER_PORTAL")
    assert portal.principal == "owner"
    assert portal.certifier == "owner_portal"


def test_6_every_domain_declares_at_least_one_probe():
    """Un dominio elencato senza modo di interrogarlo sarebbe copertura finta:
    comparirebbe nell'inventario e non produrrebbe una sola prova.

    Le sonde possono essere dichiarative (`listing`, `detail`, ...) oppure
    stare dentro un certificatore dedicato - ma non possono mancare, e non
    possono piu' essere delegate a un altro dominio.
    """
    for domain in cert.DOMAINS:
        probes = [domain.listing, domain.search, domain.detail,
                  domain.update, domain.delete, domain.cross_links,
                  domain.certifier]
        assert any(probes), f"{domain.name} non dichiara alcuna sonda"


def test_7_the_direct_id_and_write_probes_exist_where_a_resource_can_be_made():
    """Dove il run puo' creare una fixture, deve anche provare l'ID diretto:
    creare una risorsa e non tentare mai di rubarla sarebbe il caso piu' facile
    da dimenticare e il piu' importante da avere."""
    for domain in cert.DOMAINS:
        if domain.fixture:
            # L'ID diretto quando la route esiste; altrimenti un percorso che
            # attraversa la relazione verso la risorsa altrui. SELLER_INTELLIGENCE
            # non ha GET /events/{id}: il furto si tenta sul timeline del
            # contatto dell'altra agenzia, che e' l'unica lettura per id.
            assert domain.detail or domain.cross_links, (
                f"{domain.name} crea una fixture ma non tenta mai di rubarla")

    # E l'inverso: una sonda sull'ID e' forte solo se il run POSSIEDE quell'id.
    # Un dominio che la dichiarasse senza avere ne' una fixture propria, ne' una
    # catena che la produca, ne' una derivazione da cio' che esiste, ne' un
    # dominio da cui prenderla, darebbe un 404 che non distingue "isolato" da
    # "non esiste": una sonda che sembra forte ed e' vuota.
    for domain in cert.DOMAINS:
        if domain.detail:
            assert (domain.fixture or domain.chain or domain.derive
                    or domain.depends_on), (
                f"{domain.name} prova un ID diretto che questo run non possiede"
            )


def test_7b_the_upstream_requirement_is_a_chain_step_not_an_excuse():
    """"Richiede un id a monte" NON e' una prova di non applicabilita'.

    Questa e' la lezione del giro precedente. PROPOSAL richiede un `match_id` e
    SALE un `proposal_id`: entrambi veri, entrambi ricavati correttamente dagli
    schemi - e la conclusione tratta era che i due domini non fossero
    provabili. Sbagliata, perche' l'id a monte lo produce la stessa API con
    risorse che il run possiede gia'.

    Il contratto resta la fonte: se un campo a monte sparisse, la catena
    descritta in `chain` non sarebbe piu' quella giusta e questo test lo dice.
    Ma il campo obbliga a un PASSO IN PIU', non a rinunciare.
    """
    contracts = {
        # dominio -> (file dello schema, modello, campo a monte, chi lo produce)
        "PROPOSAL": ("proposal/schemas.py", "ProposalCreate", "match_id", "MATCH"),
        "SALE": ("sale/schemas.py", "SaleCreate", "proposal_id", "PROPOSAL"),
    }
    for name, (path, model, upstream, producer) in contracts.items():
        source = (ROOT / path).read_text(encoding="utf-8")
        block = source[source.index(f"class {model}"):]
        block = block[:block.index("\n\nclass ") if "\n\nclass " in block else len(block)]
        assert re.search(rf"^\s*{upstream}\s*:", block, re.M), (
            f"{model} non richiede piu' {upstream}: la catena dichiarata per "
            f"{name} non descrive piu' l'API e va ricavata di nuovo"
        )
        domain = _domain(name)
        assert domain.chain, f"{name} non dichiara come si costruisce"
        assert domain.depends_on == producer, (
            f"{name} richiede {upstream} ma non dichiara di dipendere da {producer}"
        )

    # MATCH non ha una POST di creazione - si CALCOLA - e per questo era
    # finito fra i non applicabili. Ma il calcolo e' una route come le altre, e
    # prende due risorse che il run possiede.
    match_router = (ROOT / "match" / "router.py").read_text(encoding="utf-8")
    assert '@router.post("")' not in match_router, (
        "MATCH ha acquisito una POST di creazione diretta: la catena va rivista"
    )
    assert '@router.post("/calculate", status_code=201)' in match_router, (
        "la POST di calcolo non c'e' piu': MATCH, PROPOSAL e SALE non sono "
        "costruibili come descritto e la matrice va ripensata"
    )

    # PROPERTY_WATCH: qui la non applicabilita' della FIXTURE e' vera, e questa
    # e' la differenza. Non c'e' nessuna route che crei una stima - il funnel
    # pubblico risolve l'agenzia lato server - quindi la risorsa si deriva
    # invece di nascere. Cio' che NON e' ammesso e' saltare le prove: il
    # dominio ha un certificatore suo.
    watch_router = (ROOT / "property_watch" / "router.py").read_text(encoding="utf-8")
    creates = re.findall(r'@router\.post\(\s*["\']([^"\']*)["\']', watch_router)
    assert not any(path in ("", "/stime") for path in creates), (
        f"PROPERTY_WATCH ha acquisito una POST di creazione: {creates}"
    )
    watch = _domain("PROPERTY_WATCH")
    assert watch.fixture is None and watch.derive == "stime"
    assert watch.certifier == "property_watch"


# ---------------------------------------------------------------------------
# 8-12 - il database: un solo bersaglio
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    None, "", "   ", "stima360_db", "stima360_db_prod", "stima360_db_test_2",
    "stima360_db_test_copy", "STIMA360_DB_TEST", "postgres",
])
def test_8_every_other_database_is_refused(name):
    with pytest.raises(cert.GuardFailure):
        cert.assert_certification_database(name)


def test_9_only_the_certification_database_is_accepted():
    assert cert.assert_certification_database("stima360_db_test") == "stima360_db_test"
    assert cert.assert_certification_database("  stima360_db_test  ") == "stima360_db_test"


def test_10_the_guard_runs_before_any_write(monkeypatch):
    """Non basta che la guardia esista: deve essere la prima cosa.

    `approved_commit` e' volutamente irraggiungibile: se il controllo sul
    commit venisse prima, mascherebbe la guardia sul database.
    """
    report, _ = quiet_report()
    database = fake_database(agencies=AGENCIES)
    env = {"DB_NAME": "stima360_db", "RENDER_GIT_BRANCH": cert.APPROVED_BRANCH,
           "RENDER_EXTERNAL_URL": "https://test.example"}

    with pytest.raises(cert.GuardFailure):
        cert.preflight(report, database, env, approved_commit="irraggiungibile")

    executed = " ".join(database.state.get("sql", [])).upper()
    assert "INSERT" not in executed and "DELETE" not in executed


def test_11_a_lying_db_name_is_caught_by_the_real_connection(monkeypatch):
    report, _ = quiet_report()
    database = fake_database(database="stima360_db", agencies=AGENCIES)
    env = {"DB_NAME": cert.REQUIRED_DB_NAME, "RENDER_GIT_BRANCH": cert.APPROVED_BRANCH,
           "RENDER_EXTERNAL_URL": "https://test.example"}

    with pytest.raises(cert.CheckFailed):
        cert.preflight(report, database, env, approved_commit="abc123")

    assert "0.2" in [ident for kind, ident, _ in report.rows if kind == cert.FAIL]
    assert "INSERT" not in " ".join(database.state.get("sql", [])).upper()


def test_12_a_non_https_base_stops_the_run(monkeypatch):
    """Il cookie e' Secure: su http:// il jar non lo invia, e OGNI prova di
    isolamento leggerebbe 401 come "isolamento funzionante". E' il modo piu'
    silenzioso in cui questa matrice potrebbe mentire."""
    report, _ = quiet_report()
    monkeypatch.setattr(cert, "_git",
                        lambda *a: "abc123" if a[0] == "rev-parse" else cert.APPROVED_BRANCH)
    env = {"DB_NAME": cert.REQUIRED_DB_NAME, "RENDER_GIT_BRANCH": cert.APPROVED_BRANCH,
           "RENDER_EXTERNAL_URL": "http://test.example"}

    with pytest.raises(cert.CheckFailed):
        cert.preflight(report, fake_database(agencies=AGENCIES), env, "abc123")


# ---------------------------------------------------------------------------
# 13-17 - la matrice passa su un'app che isola, e fallisce su una che no
# ---------------------------------------------------------------------------

# I domini che questo run non sa costruire e la cui lista, su un TEST poco
# popolato, e' semplicemente vuota. MATCH, PROPOSAL e SALE NON sono piu' qui:
# la catena li produce.
DERIVED = ("/api/followup",
           "/api/next-best-action", "/api/flow", "/api/admin")


def test_13_an_empty_database_yields_INCOMPLETE_and_never_PASS(monkeypatch):
    """LA TRAPPOLA PIU' FACILE, E LA PROVA CHE NON CI CADE.

    "Il marcatore di B non compare nella lista di A" e' vero anche quando la
    lista di A e' vuota. Su un TEST poco popolato i domini che questo run non
    puo' creare sarebbero comparsi fra i PASS senza che una riga fosse stata
    confrontata.

    Qui l'applicazione ISOLA correttamente e non c'e' un solo FAIL. Il verdetto
    e' comunque INCOMPLETO: e' la distinzione fra "va bene" e "non lo so".
    """
    # Nessuna stima PREESISTENTE. Non e' piu' la stessa cosa di "nessuna
    # stima": da quando la fixture ne crea una osservabile per A e per B,
    # PROPERTY_WATCH non dipende dai dati che trova. E' il miglioramento, e il
    # test lo registra invece di pretendere il vecchio BLOCKED.
    code, report, database, probe, _ = working_run(monkeypatch, stime={})

    failures = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    assert failures == [], failures

    assert code == 2, report.verdict
    assert not report.verdict.startswith("PASS")

    blocked = [i for k, i, _ in report.rows if k == cert.BLOCKED]
    assert any("NEXT_BEST_ACTION" in i for i in blocked), blocked
    # FLOW NON E' PIU' FRA I BLOCCATI, per la stessa ragione di
    # PROPERTY_WATCH: la fixture fa nascere un'esecuzione dalla richiesta
    # d'acquisto che ogni operatore crea nella propria agenzia condivisa,
    # quindi il dominio si prova su righe del run e non su cio' che il TEST
    # si trova addosso. E le sue prove devono esserci DAVVERO.
    assert not any(i.startswith("FLOW-") for i in blocked), blocked
    identificatori_flow = [i for _k, i, _t in report.rows if i.startswith("FLOW-")]
    for atteso in ("FLOW-list-A-non-vede-B", "FLOW-list-B-non-vede-A",
                   "FLOW-dettaglio-A-B", "FLOW-retry-ostile-B-A",
                   "FLOW-appartenenza-A", "FLOW-effetti"):
        assert atteso in identificatori_flow, (atteso, identificatori_flow)
    # E PROPERTY_WATCH non e' piu' fra i bloccati: la fixture crea la stima,
    # la valutazione e il watch, quindi il dominio si prova su righe del run
    # invece che su cio' che il TEST si trova addosso. Le sue prove pero'
    # devono esserci DAVVERO - un dominio che sparisse dal report sarebbe
    # peggio di uno bloccato.
    identificatori = [i for _k, i, _t in report.rows]
    assert any(i.startswith("PROPERTY_WATCH-propria-") for i in identificatori), \
        identificatori
    assert not any("PROPERTY_WATCH" in i for i in blocked), blocked


def test_13b_with_everything_populated_only_the_escalation_stays_BLOCKED(monkeypatch):
    """L'altra meta' di test_13, e il punto in cui oggi si ferma la matrice.

    Con i dati a monte presenti non resta un solo FAIL e ogni dominio porta
    righe vere. Il verdetto pero' NON e' PASS, e non per un difetto: la
    scansione FOLLOWUP modifica attivita' preesistenti e nessuna separazione e'
    costruibile senza cambiare il backend, quindi quella meta' resta non
    provata - e una prova non eseguita impedisce il PASS globale.

    Questo test fissa che sia l'UNICA cosa a impedirlo. Se domani comparisse un
    secondo BLOCKED, sarebbe una regressione mascherata da una condizione nota.
    """
    code, report, database, probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})

    failures = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    assert failures == [], failures

    blocked = [i for k, i, _ in report.rows if k == cert.BLOCKED]
    # SENZA le agenzie dedicate restano non provabili esattamente le superfici
    # la cui risorsa nasce da un'operazione sull'intero tenant. Elencarle una
    # per una - invece di tollerare "qualche BLOCKED" - fa si' che un terzo
    # dominio che diventasse improvabile venga notato.
    assert sorted(blocked) == ["FOLLOWUP-escalation", "NEXT_BEST_ACTION-batch"], (
        f"l'insieme delle prove non eseguibili e' cambiato: {blocked}"
    )
    assert code == 2, report.verdict


def test_13b_bis_with_dedicated_agencies_nothing_stays_BLOCKED(monkeypatch):
    """E con la finestra attestata non resta niente di non provato.

    E' la contropartita del test sopra: se il percorso dedicato smettesse di
    coprire uno di quei domini, il BLOCKED ricomparirebbe qui.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    blocked = [i for k, i, _ in report.rows if k == cert.BLOCKED]
    assert blocked == [], blocked
    rows = {i: k for k, i, _ in report.rows}
    for label, altro in (("C", "D"), ("D", "C")):
        assert rows.get(f"FLOW-list-{label}-vede-la-propria") == cert.PASS
        assert rows.get(f"FLOW-list-{label}-non-vede-{altro}") == cert.PASS
        assert rows.get(f"NEXT_BEST_ACTION-appartenenza-{label}") == cert.PASS
        assert rows.get(f"PROPERTY_WATCH-propria-{label}") == cert.PASS
        assert rows.get(f"PROPERTY_WATCH-ostile-{label}-{altro}") == cert.PASS
        assert rows.get(f"PROPERTY_WATCH-ostile-write-{label}-{altro}") == cert.PASS
    assert rows.get("NEXT_BEST_ACTION-disgiunte") == cert.PASS
    assert code == 0, report.verdict


def test_13d_the_chain_is_really_built_and_really_probed(monkeypatch):
    """I tre domini della catena non sono piu' BLOCKED: sono costruiti.

    E' la differenza fra la revisione precedente e questa. Se `build_chain`
    smettesse di funzionare, MATCH, PROPOSAL e SALE tornerebbero BLOCKED - il
    che sarebbe onesto, ma va visto, non subito in silenzio.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    rows = {i: k for k, i, _ in report.rows}

    for label in ("A", "B"):
        for step in ("MATCH", "PROPOSAL", "SALE"):
            assert rows.get(f"chain-{step}-{label}") == cert.PASS, (
                f"la catena non ha prodotto {step} per {label}"
            )
    # E ciascuno e' stato interrogato sull'ID diretto nelle due direzioni.
    for step in ("MATCH", "PROPOSAL", "SALE"):
        for direction in (f"{step}-detail-A-B", f"{step}-detail-B-A"):
            assert rows.get(direction) == cert.PASS, f"manca {direction}"
    # La catena e' completa; il verdetto resta INCOMPLETO per l'escalation
    # FOLLOWUP, che e' l'unica prova non eseguibile - vedi test_13b.
    assert code == 2, report.verdict


def test_13c_a_populated_list_is_really_observed_not_assumed(monkeypatch):
    """La riga preesistente dell'altra agenzia esiste davvero e NON compare.

    Il conteggio degli elementi osservati finisce nel messaggio del report: se
    fosse zero, il test 13 avrebbe gia' bloccato. Qui si verifica che il numero
    sia maggiore di zero, cioe' che il confronto abbia guardato qualcosa.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED))

    observed = [t for k, i, t in report.rows
                if k == cert.PASS and "MATCH-list-" in i and "non-vede" in i]
    assert observed, "nessun confronto sulla lista MATCH"

    # Il conteggio va ESTRATTO, non cercato per assenza.
    #
    # La prima versione di questa riga asseriva `"0 elementi osservati" not in
    # t`, ed era vacua: togliendo del tutto il conteggio dal messaggio la
    # stringa spariva e il controllo passava. Un'asserzione sull'assenza di un
    # testo e' soddisfatta anche da un testo che non c'e' affatto.
    counts = []
    for message in observed:
        found = re.search(r"\((\d+) elementi osservati\)", message)
        assert found, f"il report non dice quanti elementi ha guardato: {message}"
        counts.append(int(found.group(1)))
    assert counts and all(count > 0 for count in counts), counts


def test_14_the_matrix_fails_when_direct_ids_leak(monkeypatch):
    """LA PROVA CHE LA MATRICE GUARDA DAVVERO.

    Se un PASS su un'app corretta non fosse accompagnato da un FAIL su un'app
    che perde, non direbbe nulla: passerebbe anche una matrice che non
    interroga niente.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(broken={"isolation"}))

    assert code == 1
    leaked = [i for k, i, _ in report.rows if k == cert.FAIL and "detail" in i]
    assert leaked, [i for k, i, _ in report.rows if k == cert.FAIL]


def test_15_the_matrix_fails_when_listings_leak(monkeypatch):
    """L'altra direzione della stessa perdita: non l'ID diretto, ma la lista."""
    code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(broken={"listing"}))

    assert code == 1
    leaked = [i for k, i, _ in report.rows if k == cert.FAIL and "list" in i]
    assert leaked, [i for k, i, _ in report.rows if k == cert.FAIL]


def test_15b_the_matrix_fails_on_a_status_leak_that_reveals_no_content(monkeypatch):
    """La perdita piu' sottile: 200 su una risorsa altrui, senza mostrarne nulla.

    Un contatore, un ETag, o la semplice conferma che quell'id esiste. Il
    controllo sul marcatore non se ne accorge - non c'e' contenuto da
    riconoscere - quindi serve che la matrice guardi anche lo STATO. Senza
    questa prova, una mutazione che toglie il controllo sullo stato
    sopravviveva.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(broken={"status"}))

    assert code == 1
    leaked = [i for k, i, _ in report.rows
              if k == cert.FAIL and "detail" in i and "neutro" not in i]
    assert leaked, [i for k, i, _ in report.rows if k == cert.FAIL]


def test_15c_the_matrix_fails_when_a_refused_write_is_applied_anyway(monkeypatch):
    """Rifiuta a parole e scrive comunque: il caso peggiore.

    Il chiamante vede un 404 e crede di essere stato fermato; la risorsa
    dell'altra agenzia porta il suo dato. Un controllo che si fermasse allo
    stato della risposta direbbe che tutto e' a posto, ed e' il motivo per cui
    dopo ogni scrittura ostile la matrice rilegge la risorsa con la sessione
    del proprietario.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(broken={"write"}))

    assert code == 1
    tampered = [i for k, i, _ in report.rows if k == cert.FAIL and "intatta" in i]
    assert tampered, [i for k, i, _ in report.rows if k == cert.FAIL]


def test_16_both_directions_are_probed_for_every_domain(monkeypatch):
    """A verso B e B verso A, non solo una delle due.

    Una matrice che provasse una direzione sola passerebbe su un sistema in cui
    l'isolamento e' asimmetrico - e l'asimmetria e' proprio il difetto che una
    JOIN scopata a meta' produce.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    idents = [i for _k, i, _ in report.rows]

    # Ogni dominio, non solo quelli con `detail`: la versione precedente
    # escludeva OWNER_ADMIN per nome e non guardava affatto i domini provati da
    # un certificatore dedicato, che sono esattamente quelli in cui una sola
    # direzione era piu' facile da dimenticare.
    # FOLLOWUP non ha due direzioni OSTILI: ha due osservazioni della
    # selezione, A e B, piu' il confronto fra le due. La forma e' diversa
    # perche' la superficie e' una lettura, non un tentativo di furto.
    assert "FOLLOWUP-selezione-A" in idents and "FOLLOWUP-selezione-B" in idents
    assert "FOLLOWUP-selezione-disgiunta" in idents

    # NEXT_BEST_ACTION non ha due direzioni ostili sulle agenzie condivise: la
    # sua risorsa nasce da un refresh sull'intero tenant, e la prova sta sulle
    # dedicate (test_13b_bis). Nominarlo qui, invece di tollerare i domini
    # senza direzioni, fa si' che un secondo caso venga notato.
    assert "NEXT_BEST_ACTION-batch" in idents
    for domain in cert.DOMAINS:
        if domain.name in ("FOLLOWUP", "NEXT_BEST_ACTION"):
            continue
        own = [i for i in idents if i.startswith(f"{domain.name}-")]
        # Tre forme per "A verso B": l'id diretto (`...-A-B`), la lista
        # (`...-non-vede-B`) e la scansione (`...-non-elabora-B`).
        forward = [i for i in own if i.endswith(("A-B", "-non-vede-B", "-non-elabora-B"))]
        backward = [i for i in own if i.endswith(("B-A", "-non-vede-A", "-non-elabora-A"))]
        assert forward, f"{domain.name}: manca la direzione A->B fra {own}"
        assert backward, f"{domain.name}: manca la direzione B->A fra {own}"


def test_17_owner_admin_is_probed_on_the_threshold_AND_on_the_scope(monkeypatch):
    """Le due prove sono diverse e servono entrambe.

    LA SOGLIA: le identita' della matrice sono `agency_admin` e devono ricevere
    403. E' isolamento sulla dimensione del RUOLO.

    LO SCOPE: con due sessioni `agency_owner` vere, le domande tornano quelle
    di sempre. E' isolamento sulla dimensione dell'AGENZIA.

    Per un giro di revisione c'era solo la prima, e sembrava sufficiente. Non
    lo era: un 403 su entrambe le agenzie e' compatibile con qualunque cosa
    accada dietro la soglia, fuga inclusa.
    """
    _code, report, _db, _probe, _ = working_run(monkeypatch)
    idents = [i for _k, i, _ in report.rows]

    soglia = sorted(i for i in idents if i.startswith("OWNER_ADMIN-soglia-"))
    assert soglia == ["OWNER_ADMIN-soglia-A", "OWNER_ADMIN-soglia-B"]

    scope = sorted(i for i in idents if i.startswith("OWNER_ADMIN-scope-"))
    assert scope == ["OWNER_ADMIN-scope-A", "OWNER_ADMIN-scope-B"], (
        "la prova sullo scope di OWNER Admin non e' stata eseguita: resta solo "
        "quella sulla soglia, che non dice nulla sull'isolamento fra agenzie"
    )
    for direction in ("OWNER_ADMIN-non-vede-A", "OWNER_ADMIN-non-vede-B"):
        assert direction in idents, f"manca la direzione ostile {direction}"
    for direction in ("OWNER_ADMIN-write-A-B", "OWNER_ADMIN-write-B-A"):
        assert direction in idents, f"manca la scrittura ostile {direction}"


# ---------------------------------------------------------------------------
# 18-22 - il verdetto non si puo' addolcire
# ---------------------------------------------------------------------------

def test_18_a_blocked_probe_cannot_produce_pass():
    report, _ = quiet_report()
    report.note("a", "ok")
    report.blocked("b", "non eseguita")
    assert report.exit_code == 2
    assert "INCOMPLETO" in report.verdict
    assert not report.verdict.startswith("PASS")


def test_19_a_failure_outranks_everything_including_many_passes():
    report, _ = quiet_report()
    for index in range(50):
        report.note(f"x{index}", "ok")
    report.blocked("b", "non eseguita")
    report.fail("c", "perdita")
    assert report.exit_code == 1
    assert report.verdict == "FAIL"


def test_20_an_incomplete_cleanup_is_a_failure():
    report, _ = quiet_report()
    database = fake_database(residue={"users": 1, "memberships": 0, "sessions": 0},
                             delete_rowcount=0)
    certification = cert.Certification(database, report)
    certification.created_user_ids.extend([1, 2])

    certification.cleanup_database()

    assert report.exit_code == 1
    assert not report.verdict.startswith("PASS")


def test_21_a_dead_database_during_cleanup_is_a_failure():
    report, _ = quiet_report()
    database = fake_database(explode_on="DELETE FROM operator_sessions")
    certification = cert.Certification(database, report)
    certification.created_user_ids.append(1)

    certification.cleanup_database()          # non solleva

    assert report.exit_code == 1


def test_22_leftovers_stop_the_run_without_creating_or_deleting(monkeypatch):
    """Da qui non si distingue il residuo di ieri da un run in corso adesso, e
    le due chiedono risposte opposte: nel dubbio non si tocca niente."""
    other_run = {900: f"{cert.CERT_PREFIX}deadbeef-11{cert.CERT_DOMAIN}"}
    database = fake_database(agencies=AGENCIES, users=dict(other_run))

    code, report, database, _probe, _ = working_run(monkeypatch, database=database)

    assert code == 1
    executed = " ".join(database.state.get("sql", [])).upper()
    assert "INSERT" not in executed, "ha creato identita' nonostante il residuo"
    assert "DELETE" not in executed, "ha cancellato righe che non ha creato"
    assert database.state["users"] == other_run


# ---------------------------------------------------------------------------
# 23-27 - fixture, cleanup e concorrenza
# ---------------------------------------------------------------------------

def test_23_cleanup_runs_even_after_a_failure(monkeypatch):
    code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(broken={"isolation"}))

    deletes = " ".join(database.state.get("deletes", []))
    for table in ("operator_sessions", "agency_memberships", "operator_users"):
        assert table in deletes, f"il cleanup non ha toccato {table}"
    assert code == 1


def test_24_the_cleanup_is_lexically_inside_a_finally():
    """La struttura del sorgente lo garantisce, non l'ordine delle istruzioni:
    `except Exception` copre tutto il resto, quindi la differenza si vede solo
    con un KeyboardInterrupt - ed e' un caso reale, un Ctrl-C su una richiesta
    lenta."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    run_fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "run")
    in_finally = [
        ast.unparse(stmt)
        for node in ast.walk(run_fn) if isinstance(node, ast.Try)
        for stmt in node.finalbody
    ]
    assert any("cleanup_database()" in s for s in in_finally), in_finally
    assert any("cleanup_http_fixtures" in s for s in in_finally), in_finally


def test_25_no_delete_is_ever_written_by_prefix():
    """Il criterio e' l'id. Una DELETE per prefisso porterebbe via le identita'
    di una certificazione concorrente mentre le sta usando."""
    source = SCRIPT.read_text(encoding="utf-8")
    deletes = re.findall(r"DELETE FROM [a-z_]+ WHERE [^\"]+", source)
    assert deletes, "nessuna DELETE trovata: il pattern e' cambiato"
    for statement in deletes:
        assert "IN %s" in statement, statement
        for forbidden in ("email", "LIKE", "run_id"):
            assert forbidden not in statement, f"DELETE per {forbidden}: {statement}"


def test_26_a_concurrent_run_survives_this_run_cleanup():
    """La regressione che vale piu' di tutte: due matrici in parallelo.

    Il fake cancella davvero, quindi la sopravvivenza si legge dalla tabella e
    non dal testo della query.
    """
    report, _ = quiet_report()
    database = fake_database()
    certification = cert.Certification(database, report)

    mine = [certification.create_operator(AGENCIES[0])["id"],
            certification.create_operator(AGENCIES[1])["id"]]
    concurrent = 555
    database.state["users"][concurrent] = f"{cert.CERT_PREFIX}altro-99{cert.CERT_DOMAIN}"

    certification.cleanup_database()

    assert concurrent in database.state["users"], (
        "il cleanup ha cancellato l'identita' di un run concorrente"
    )
    for identifier in mine:
        assert identifier not in database.state["users"]
    assert report.exit_code == 0


def test_27_each_run_has_its_own_identifier_and_marker():
    report, _ = quiet_report()
    database = fake_database()
    identifiers = {cert.Certification(database, report).run_id for _ in range(50)}
    assert len(identifiers) == 50

    certification = cert.Certification(database, report)
    assert certification.run_id in certification.marker("A")
    assert certification.marker("A") != certification.marker("B"), (
        "le due agenzie devono avere marcatori distinti, altrimenti 'A non vede "
        "il marcatore di B' e' vero per costruzione"
    )


def _function_source(name: str) -> str:
    """Il corpo di una funzione, delimitato dall'AST e non da `index()`.

    Un taglio testuale fra due `def` sembra equivalente e non lo e': basta
    inserire un metodo in mezzo perche' la fetta ne inghiotta uno che non
    doveva guardare. E' successo con i due cleanup nuovi, e il test che ne e'
    derivato accusava il metodo sbagliato.
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"funzione {name} non trovata")


def test_28_api_deletable_fixtures_are_deleted_through_the_api():
    """Dove esiste una DELETE, si passa da li'.

    Via HTTP di proposito: la cancellazione attraversa le stesse regole di
    scope delle altre chiamate, quindi non puo' toccare nulla di estraneo. Una
    DELETE diretta sarebbe piu' comoda e aggirerebbe cio' che lo script prova.
    """
    block = _function_source("cleanup_http_fixtures")
    assert "http.request(" in block
    for forbidden in ("DELETE FROM", "cur.execute", "self.db"):
        assert forbidden not in block, f"il cleanup delle fixture usa {forbidden}"


def test_28b_the_rows_no_route_can_delete_are_removed_by_id_only():
    """Dove NON esiste una DELETE, si passa dal database - e per soli ID.

    Match, proposte, vendite e conti proprietario non hanno una route di
    cancellazione: l'API sa crearli e non sa disfarli. Ignorarli lascerebbe
    dietro il run righe che maneggiano denaro; cancellarli per marcatore o per
    prefisso porterebbe via quelle di una certificazione concorrente. Restano
    gli id, e solo quelli.
    """
    for name in ("cleanup_chain_fixtures", "cleanup_owner_fixtures"):
        block = _function_source(name)
        assert "WHERE id IN %s" in block or "_id IN %s" in block, (
            f"{name} non cancella per elenco di id"
        )
        for forbidden in ("LIKE", "CERT_EMAIL_LIKE", "marker", "run_id LIKE"):
            assert forbidden not in block, (
                f"{name} usa {forbidden} come criterio: cancellerebbe anche le "
                "righe di un run concorrente"
            )

    # E il verificatore finale conta i residui sugli stessi id, non su un
    # prefisso: un cleanup che dicesse "fatto" senza guardare non e' una prova.
    scoped = _function_source("_delete_scoped")
    assert "self.report.fail" in scoped and "INCOMPLETO" in scoped


# ---------------------------------------------------------------------------
# 29-32 - segreti, censimento, gate
# ---------------------------------------------------------------------------

def test_29_no_credential_is_hardcoded():
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    suspicious = re.compile(r"(pbkdf2_sha256\$|[A-Za-z0-9+/]{40,}={0,2}|[0-9a-f]{40,})")
    allowed = {"pbkdf2_sha256$"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value in allowed or len(value) > 400:
                continue
            assert not suspicious.search(value), f"letterale sospetto: {value[:40]!r}"
    assert "secrets.token_urlsafe" in source
    assert source.count("os.environ") == 1


def test_30_the_leak_scanner_can_actually_fail():
    report, _ = quiet_report()

    class Probe(cert.HttpProbe):
        def __init__(self):
            self.exchanges = [("GET /x", 200, b'{"eco":"super-segreto"}')]

    with pytest.raises(cert.CheckFailed):
        cert.scan_for_leaks(report, Probe(), ["super-segreto"])

    clean, _ = quiet_report()
    cert.scan_for_leaks(clean, Probe(), ["un-altro-valore"])
    assert clean.exit_code == 0


def test_31_the_incoherence_census_is_read_only_and_blocks_on_findings():
    """Conta e non ripara. Un'incoerenza trovata e non classificata deve
    impedire la chiusura del gate, non essere corretta di nascosto."""
    source = SCRIPT.read_text(encoding="utf-8")
    block = source[source.index("def incoherence_census"):source.index("def _fill")]
    assert "SELECT COUNT(*)" in block
    for forbidden in ("DELETE", "UPDATE", "INSERT"):
        assert forbidden not in block, f"il censimento esegue {forbidden}"

    report, _ = quiet_report()
    database = fake_database(census={"owner_property_access": 3})
    cert.incoherence_census(report, database)
    assert report.exit_code == 1


def test_32_the_script_never_closes_the_gate_by_itself():
    """Un esito PASS e' una condizione necessaria, non l'autorizzazione.

    Lo script non deve poter scrivere `LIVE_HOSTILE_MATRIX_PASSED`: quella
    riga la muove una persona, dopo aver letto l'output.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    assert "LIVE_HOSTILE_MATRIX_PASSED = True" not in source
    assert "GATE-MA1 resta APERTO" in source

    gate = (ROOT / "tests" / "test_p26_6c_backend_gate_closure.py").read_text(encoding="utf-8")
    assert "LIVE_HOSTILE_MATRIX_PASSED = False" in gate


# ---------------------------------------------------------------------------
# 33-38 - COMPLETEZZA, SECONDA REVISIONE
#
# La prima versione di questo file provava che ogni prefisso montato comparisse
# in `DOMAINS`. Non bastava: un dominio puo' essere elencato e non provato.
# Quattro modi in cui e' successo davvero, tutti verdi:
#
#   * OWNER_ADMIN era "provato" da un 403. Le identita' della matrice sono
#     `agency_admin` e la soglia e' `agency_owner`, quindi il rifiuto arrivava
#     PRIMA di qualunque domanda sullo scope: dell'isolamento fra agenzie su
#     quella superficie non si sapeva nulla.
#   * OWNER Portal non era nella matrice affatto, dichiarato "non una
#     superficie da operatore" - vero, e irrilevante: e' una superficie di
#     tenant, e il principale diverso e' un motivo per provarla in modo
#     diverso, non per non provarla.
#   * PROPERTY_WATCH era "coperto" da LEGACY_ADMIN. Una copertura per etichetta:
#     due domini diversi, due route diverse, una sola prova.
#   * PROPOSAL e SALE erano "non applicabili" perche' la loro POST richiede un
#     id a monte. Ma quell'id la stessa API sa produrlo, quindi la catena si
#     costruisce e le categorie sono applicabili eccome.
#
# Le prove che seguono impediscono il ritorno di ognuno dei quattro.
# ---------------------------------------------------------------------------

def _domain(name):
    return next(d for d in cert.DOMAINS if d.name == name)


def test_33_no_domain_is_covered_by_another_domain_label():
    """Nessuna copertura delegata, in nessuna forma.

    `covered_by` permetteva a un dominio di sparire dalla matrice nominandone
    un altro. Il meccanismo non deve tornare: se una superficie e' distinta
    abbastanza da avere un prefisso suo, e' distinta abbastanza da avere prove
    sue.
    """
    assert not hasattr(cert.Domain("X", "/x", listing="/x"), "covered_by"), (
        "l'attributo covered_by e' tornato: una superficie puo' di nuovo "
        "sparire dalla matrice nominandone un'altra"
    )

    # Strutturale e non testuale: un controllo su "la parola non compare" e'
    # soddisfatto anche cancellando la spiegazione del perche' e' stata tolta,
    # ed e' proprio il commento che serve a chi legge il diff fra un anno.
    # Cio' che deve restare vietato e' l'ARGOMENTO, in una chiamata a Domain.
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    delegated = [
        keyword.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Domain"
        for keyword in node.keywords
        if keyword.arg in ("covered_by", "covered", "delegates_to")
    ]
    assert delegated == [], (
        f"un dominio delega la propria copertura a un altro: {delegated}"
    )


def test_34_every_domain_is_probed_by_something_that_actually_runs():
    """Ogni dominio deve avere sonde proprie O un certificatore dedicato.

    E il certificatore dichiarato deve esistere come funzione del modulo: una
    stringa che non risolve sarebbe copertura scritta e non eseguita.
    """
    for domain in cert.DOMAINS:
        probes = [domain.listing, domain.search, domain.detail, domain.update,
                  domain.delete, domain.cross_links]
        if domain.certifier:
            function = getattr(cert, f"certify_{domain.certifier}", None)
            assert callable(function), (
                f"{domain.name} dichiara il certificatore {domain.certifier!r}, "
                "che non esiste nel modulo"
            )
            continue
        assert any(probes), f"{domain.name} non dichiara alcuna sonda"


def test_35_owner_admin_is_probed_with_a_real_agency_owner_session():
    """La prova su OWNER Admin non puo' fermarsi al 403.

    Un 403 su tutte e due le agenzie e' compatibile con qualunque cosa accada
    dietro la soglia di ruolo, isolamento incluso. Servono due sessioni
    `agency_owner` vere - una per agenzia - e allora le domande diventano
    quelle di sempre: A vede A, B vede B, A non vede B, B non vede A.
    """
    domain = _domain("OWNER_ADMIN")
    assert domain.certifier == "owner_admin"
    assert domain.principal == cert.OWNER_ADMIN_MIN_ROLE, (
        "OWNER_ADMIN va interrogato da un agency_owner, non dal ruolo della "
        "matrice: altrimenti si prova la soglia e non lo scope"
    )
    source = SCRIPT.read_text(encoding="utf-8")

    # Le sessioni sono agganciate agli owner REALI e create come sole righe in
    # operator_sessions: nessuna password, nessuna membership, nessun utente.
    block = source[source.index("class OwnerSessions"):source.index("class Certification")]
    assert "INSERT INTO operator_sessions" in block
    for forbidden in ("UPDATE operator_users", "UPDATE agency_memberships",
                      "INSERT INTO operator_users", "INSERT INTO agency_memberships",
                      "password_hash"):
        assert forbidden not in block, (
            f"OwnerSessions tocca {forbidden}: gli owner reali devono restare "
            "esattamente come sono"
        )
    # E il cleanup cancella per ID di sessione, mai per utente.
    assert "DELETE FROM operator_sessions WHERE id IN" in block
    assert "DELETE FROM operator_users" not in block


def test_36_the_owner_portal_is_in_the_matrix_with_its_own_principal():
    """Il portale e' una superficie di tenant, e va provato come tale."""
    portal = _domain("OWNER_PORTAL")
    assert portal.prefix == "/api/owner/portal"
    assert portal.certifier == "owner_portal"
    assert portal.principal == "owner"
    source = SCRIPT.read_text(encoding="utf-8")
    # Il portale autentica con un cookie DIVERSO: usarne uno solo per due
    # principali diversi sarebbe il modo piu' rapido di provare la cosa
    # sbagliata senza accorgersene.
    assert cert.OWNER_COOKIE_NAME == "stima360_owner_session"
    assert cert.OWNER_COOKIE_NAME != cert.COOKIE_NAME
    # E la prova del grant incoerente esiste: e' l'unica che dice qualcosa
    # sull'origine dei dati del portale invece che sulla loro lettura.
    assert "grant-incoerente" in source


def test_37_property_watch_has_its_own_probes():
    """Niente piu' delega a LEGACY_ADMIN."""
    watch = _domain("PROPERTY_WATCH")
    assert watch.certifier == "property_watch"
    assert watch.prefix == "/api/property-watch"
    source = SCRIPT.read_text(encoding="utf-8")
    # Le stime non si creano da questa API: la fixture si DERIVA, in sola
    # lettura, da cio' che l'agenzia gia' possiede.
    assert watch.fixture is None
    assert watch.derive is not None
    block = source[source.index("def derive_stime"):]
    block = block[:block.index("\ndef ")]
    assert "SELECT" in block
    for forbidden in ("INSERT", "UPDATE", "DELETE"):
        assert forbidden not in block, f"la derivazione esegue {forbidden}"


def test_38_the_derived_chain_is_built_not_declared_impossible():
    """MATCH, PROPOSAL e SALE si costruiscono: la catena esiste nell'API.

    "Richiede un id a monte" non e' una prova di non applicabilita' finche' lo
    stesso client puo' produrre quell'id. Questo test fissa i sei passi che la
    rendono costruibile, ricavati dai router: se uno sparisse, la catena va
    ripensata invece di tornare a dichiararla impossibile.
    """
    for name in ("MATCH", "PROPOSAL", "SALE"):
        domain = _domain(name)
        assert domain.chain, f"{name} non dichiara la catena che lo produce"
        assert domain.detail, f"{name} e' costruibile ma non prova l'ID diretto"

    match_router = (ROOT / "match" / "router.py").read_text(encoding="utf-8")
    assert '@router.post("/calculate"' in match_router, (
        "MATCH non ha piu' la POST di calcolo: la catena non e' costruibile "
        "come descritto e va ricavata di nuovo dal router"
    )
    proposal_router = (ROOT / "proposal" / "router.py").read_text(encoding="utf-8")
    assert '@router.post("/{proposal_id}/transition")' in proposal_router
    sale_router = (ROOT / "sale" / "router.py").read_text(encoding="utf-8")
    assert '@router.post("", status_code=201)' in sale_router


# ---------------------------------------------------------------------------
# 39-45 - LE NUOVE SONDE SANNO FALLIRE
#
# Una sonda che non sa fallire non e' una prova: e' una riga di report. Le
# sette che seguono rompono, una per volta, esattamente cio' che ciascuna nuova
# superficie deve sorvegliare, e pretendono un FAIL.
# ---------------------------------------------------------------------------

def _first_failure(monkeypatch, kind, surface):
    """Rompe UNA cosa e ritorna la prima prova che fallisce.

    UNA, e non tutte insieme: `Report.check` interrompe il run alla prima prova
    fallita - ed e' la scelta giusta, perche' una matrice che proseguisse dopo
    una fuga accertata produrrebbe pagine di risultati su un sistema gia'
    compromesso. Ma significa che una rottura globale ne esercita UNA SOLA.

    Questa e' la lezione della prima tornata di mutazioni: dieci sopravvissute
    su venti, tutte perche' i test rompevano tutto e poi si accontentavano di
    "una prova di questo dominio e' fallita". Bastava che ne fallisse una
    qualunque, e le altre potevano essere indebolite senza che nessuno se ne
    accorgesse. Adesso ogni sonda ha la sua rottura e il suo identificatore.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch,
        http=FakeHttp(broken={kind}, only=surface, prepopulate=DERIVED, stime=STIME))
    failures = [i for k, i, _ in report.rows if k == cert.FAIL]
    return code, failures, report


@pytest.mark.parametrize("kind,surface,expected", [
    # OWNER Admin: quattro sonde, quattro rotture distinte.
    ("owner_own_list", "owner_admin", "OWNER_ADMIN-scope-A"),
    ("owner_list", "owner_admin", "OWNER_ADMIN-non-vede-B"),
    ("owner_write", "owner_admin", "OWNER_ADMIN-write-A-B"),
    ("threshold", "owner_admin", "OWNER_ADMIN-soglia-A"),
    # Il portale: lista, dettaglio, documenti, e l'origine del dato.
    ("portal_list", "portal", "OWNER_PORTAL-non-vede-B"),
    ("portal_detail", "portal", "OWNER_PORTAL-detail-A-B"),
    ("portal_documents", "portal", "OWNER_PORTAL-documenti-A-B"),
    ("grant", "owner_admin", "OWNER_PORTAL-grant-incoerente-A-B"),
    # PROPERTY_WATCH: lettura e scrittura ostili.
    ("watch_read", "watch", "PROPERTY_WATCH-ostile-A-B"),
    ("watch_write", "watch", "PROPERTY_WATCH-ostile-write-A-B"),
])
def test_39_every_new_probe_fails_when_its_own_surface_leaks(
        monkeypatch, kind, surface, expected):
    """Ogni sonda nuova, rotta da sola, deve produrre IL SUO fallimento.

    Non "un fallimento da qualche parte in quel dominio": quello e' soddisfatto
    anche da una sonda vicina, ed e' come dieci mutazioni sono sopravvissute la
    prima volta. Qui l'identificatore atteso e' esatto.
    """
    code, failures, _report = _first_failure(monkeypatch, kind, surface)
    assert expected in failures, (
        f"rompendo {kind!r} su {surface!r} la prova {expected} non ha fallito; "
        f"fallimenti osservati: {failures}"
    )
    assert code == 1


def test_40_the_hostile_write_that_is_refused_and_applied_anyway_is_caught(monkeypatch):
    """Il caso peggiore: 404 a parole, scrittura eseguita.

    Il chiamante vede un rifiuto, il danno resta, e nessuno lo scopre. La
    sonda che lo prende non e' quella sullo stato ma quella che RILEGGE la
    risorsa dell'altro subito dopo.
    """
    code, failures, _report = _first_failure(monkeypatch, "write", "generic")
    assert any(i.endswith("-intatta") for i in failures), failures
    assert code == 1


def test_41_a_chain_that_cannot_be_built_says_why(monkeypatch):
    """Un BLOCKED senza motivo e' un BLOCKED inutile.

    Quando il motore MATCH non puo' calcolare, la prontezza dice quale criterio
    manca. Se lo script saltasse quella domanda vedrebbe solo un 400, e chi
    legge il report non saprebbe cosa sistemare sul TEST.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch,
        http=FakeHttp(broken={"readiness"}, prepopulate=DERIVED, stime=STIME))
    blocked = [(i, t) for k, i, t in report.rows if k == cert.BLOCKED]
    chain = [t for i, t in blocked if i.startswith("chain-")]
    assert chain, blocked
    assert any(cert_reason() in t for t in chain), (
        f"il motivo riportato dal motore non compare nel report: {chain}"
    )
    # E i tre domini a valle restano BLOCKED, non PASS.
    assert code == 2, report.verdict
    for step in ("MATCH", "PROPOSAL", "SALE"):
        assert any(i.startswith(f"{step}-detail") for i, _ in blocked), blocked


def test_42_a_residue_left_behind_by_the_new_cleanups_is_a_failure(monkeypatch):
    """Le righe che nessuna route sa cancellare vanno via, o e' FAIL.

    Match, proposte, vendite e conti proprietario non hanno una DELETE: se il
    cleanup per id fallisse in silenzio, ogni certificazione lascerebbe dietro
    di se' righe che maneggiano denaro.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch,
        census={"FROM property_sales": 2, "FROM owner_accounts": 1},
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    failures = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    assert any(i == "CLEAN-CHAIN" for i, _ in failures), failures
    assert any(i == "CLEAN-OWNER" for i, _ in failures), failures
    assert any("INCOMPLETO" in t for _, t in failures), failures


def test_43_without_a_real_owner_the_surfaces_are_BLOCKED_not_PASSED(monkeypatch):
    """Nessun agency_owner sul TEST: OWNER Admin e il portale restano BLOCKED.

    E' la risposta onesta. 027 ammette un solo titolare per agenzia e questo
    script non ne crea: se non c'e', quelle superfici non sono state provate -
    non "sono a posto".
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, owners={}, http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("owner-session-A") == cert.BLOCKED
    assert rows.get("OWNER_ADMIN-scope-A") == cert.BLOCKED
    assert rows.get("OWNER_PORTAL-propria-A") == cert.BLOCKED
    assert code == 2, report.verdict
    # E la soglia resta provata: e' l'unica cosa che si puo' ancora sapere.
    assert rows.get("OWNER_ADMIN-soglia-A") == cert.PASS


def test_44_the_temporary_owner_sessions_are_removed_by_id(monkeypatch):
    """Le sessioni di titolare valgono quanto le sue credenziali.

    Devono sparire, e la cancellazione deve nominare gli id di questo run: un
    DELETE per utente porterebbe via anche la sessione con cui il titolare vero
    sta lavorando in quel momento.
    """
    _code, _report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    deletes = [d for d in database.state.get("deletes", [])
               if "OPERATOR_SESSIONS" in d.upper()]
    assert deletes, "nessuna cancellazione di sessioni owner"
    assert any("WHERE id IN" in d for d in deletes), deletes
    for statement in deletes:
        assert "operator_user_id IN" not in statement or "WHERE id IN" in " ".join(deletes)


def test_45_the_one_time_token_exemption_is_not_a_blanket_allow(monkeypatch):
    """L'esenzione dello scanner vale per UNA risposta, non per il token.

    `POST /accounts/{id}/tokens` puo' contenere il token: e' il suo contratto.
    Qualunque ALTRA risposta che lo contenga e' una fuga, e questo test lo
    prova mettendo lo stesso segreto in uno scambio diverso.
    """
    report, _ = quiet_report()
    probe = cert.HttpProbe("https://test.example")
    probe.exchanges = [
        ("POST /api/owner/admin/accounts/1/tokens", 200, b'{"token":"SEGRETO"}'),
        ("GET /api/owner/portal/properties", 200, b'{"items":["SEGRETO"]}'),
    ]
    # `check` interrompe il run su una prova fallita: la fuga di un token non
    # e' una riga da annotare e proseguire.
    with pytest.raises(cert.CheckFailed):
        cert.scan_for_leaks(
            report, probe, ["SEGRETO"],
            expected=(("POST /api/owner/admin/accounts/1/tokens", "SEGRETO"),),
        )
    assert report.exit_code == 1, "una fuga fuori dallo scambio esentato e' passata"

    # E con la sola risposta esentata, nessuna fuga.
    report2, _ = quiet_report()
    probe2 = cert.HttpProbe("https://test.example")
    probe2.exchanges = [probe.exchanges[0]]
    cert.scan_for_leaks(
        report2, probe2, ["SEGRETO"],
        expected=(("POST /api/owner/admin/accounts/1/tokens", "SEGRETO"),),
    )
    assert report2.exit_code == 0


# ---------------------------------------------------------------------------
# 46-49 - LE REGRESSIONI DEL RUN 22d007af7916
#
# Tre sonde dello script invocavano route che l'applicazione non ha. Nessuna
# poteva essere vista da un doppio che rispondeva 200 a qualunque percorso: e'
# per questo che il difetto e' arrivato fino al TEST, dove il 405 e' stato
# perfino contato come prova superata (405 non e' 200).
# ---------------------------------------------------------------------------

def test_46_every_declared_probe_hits_a_route_that_exists():
    """LA REGRESSIONE PRINCIPALE: nessun percorso inventato.

    Ogni sonda dichiarata nei domini viene risolta contro la tavola delle route
    vere. Un percorso inesistente, o un metodo che quella route non accetta,
    fa fallire qui - dove costa un secondo - invece che sul TEST.
    """
    problemi = []
    for domain in cert.DOMAINS:
        sonde = [("GET", domain.listing), ("GET", domain.search),
                 ("GET", domain.detail)]
        if domain.fixture:
            sonde.append(("POST", domain.fixture[0]))
        if domain.update:
            sonde.append((domain.update[0], domain.update[1]))
        sonde += [("GET", c) for c in domain.cross_links]
        # La DELETE di cleanup si registra SOLO dove c'e' una fixture.
        if domain.fixture and domain.detail and domain.api_delete:
            sonde.append(("DELETE", domain.detail))
        for method, template in sonde:
            if not template:
                continue
            path = (template.replace("{id}", "1").replace("{marker}", "x")
                    .replace("{core_id}", "1"))
            status, _ = resolve_route(method, path)
            if status:
                problemi.append(f"{domain.name}: {status} {method} {path}")
    assert problemi == [], "sonde verso route inesistenti: " + "; ".join(problemi)


def _delete_handler(module: str, path_suffix: str):
    """Il gestore della route DELETE, e i nomi che invoca. Dal sorgente."""
    tree = ast.parse((ROOT / module / "router.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            attr = getattr(deco.func, "attr", None)
            args = [a.value for a in deco.args if isinstance(a, ast.Constant)]
            if attr == "delete" and any(a == path_suffix for a in args):
                chiamate = {getattr(c.func, "attr", None) or getattr(c.func, "id", None)
                            for c in ast.walk(node) if isinstance(c, ast.Call)}
                return node.name, {c for c in chiamate if c}
    return None, set()


def test_47_api_delete_means_physical_removal_not_a_2xx():
    """LA REGRESSIONE DEL RESIDUO SILENZIOSO.

    `api_delete` non significa "la route DELETE esiste": significa che RIMUOVE
    FISICAMENTE la riga. Due route di questa API rispondono 200 e archiviano -
    `archive_property`, `archive_request` - e il run 22d007af7916 ha creduto al
    codice di stato, lasciando quattro righe sul TEST senza segnalarle.

    Il giudizio e' ricavato dal router: se il gestore della DELETE, o una
    funzione che invoca, si chiama `archive*`, la cancellazione e' logica e
    `api_delete` deve essere False. Se un domani diventasse fisica, questo test
    fallisce e la dichiarazione va rivista - invece di restare pessimista per
    sempre.
    """
    rotte = {  # dominio -> (modulo, percorso dichiarato nel router)
        "CORE": ("core", "/contacts/{contact_id}"),
        "PROPERTY": ("property", "/properties/{property_id}"),
        "BUY": ("buy", "/requests/{request_id}"),
    }
    for name, (module, suffix) in rotte.items():
        domain = _domain(name)
        handler, chiamate = _delete_handler(module, suffix)
        archivia = handler is not None and any(
            "archive" in n for n in {handler} | chiamate)
        fisica = handler is not None and not archivia
        assert domain.api_delete == fisica, (
            f"{name}.api_delete={domain.api_delete} ma la route DELETE "
            f"{'non esiste' if handler is None else ('archivia (' + handler + ')') if archivia else 'cancella davvero'}"
        )

    # E cio' che non si cancella via API ha una tabella e un marcatore per il
    # cleanup SQL: senza, la riga resterebbe e nessuno lo direbbe.
    for domain in cert.DOMAINS:
        if domain.fixture and not domain.api_delete:
            assert domain.table and domain.marker_column, (
                f"{domain.name} non e' cancellabile via API e non dichiara "
                "tabella/colonna per il cleanup SQL"
            )

    # E la cancellazione incrocia TERNE (id, marcatore, agenzia) sulla stessa
    # riga: tre elenchi indipendenti lascerebbero passare la riga di A con il
    # marcatore di B.
    blocco = _function_source("cleanup_orphan_fixtures")
    assert "t.id = f.id" in blocco and "t.agency_id = f.agency_id" in blocco
    assert "f.marcatore" in blocco, blocco[-400:]
    for vietato in ("LIKE", "marker_column}"):
        assert vietato not in blocco


def test_47b_a_2xx_delete_that_leaves_the_row_is_caught_on_the_database(monkeypatch):
    """LA REGRESSIONE DEL RESIDUO SILENZIOSO.

    Il doppio risponde 2xx alla DELETE e TIENE la riga: e' esattamente cio' che
    fanno `archive_property` e `archive_request`. Nel run 22d007af7916 questo
    e' passato inosservato perche' il cleanup si fidava del codice di stato.

    Qui la rete di sicurezza SQL rimuove comunque la riga, e la verifica finale
    lo conferma leggendo il database - non un 2xx. Se anche quella rete si
    rompesse, il residuo verrebbe dichiarato: e' il caso sotto.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch,
        http=FakeHttp(broken={"soft_delete"}, prepopulate=DERIVED, stime=STIME))
    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("CLEAN-VERIFICA") == cert.PASS, [r for r in report.rows if "CLEAN" in r[1]]

    # E la verifica ha guardato QUALCOSA. Un run che non tracciasse nulla
    # passerebbe in silenzio - ed e' il modo esatto in cui il difetto e'
    # sfuggito dal vivo: nessuna riga dichiarata, nessun residuo dichiarato.
    testo = next(t for k, i, t in report.rows if i == "CLEAN-VERIFICA")
    osservate = int(re.search(r"righe di dominio=(\d+)", testo).group(1))
    assert osservate >= 6, (
        f"la verifica ha considerato solo {osservate} righe: contatti, immobili "
        "e richieste di entrambe le agenzie devono esserci tutti"
    )
    # E le altre categorie sono nominate: un conteggio delle sole righe di
    # dominio direbbe meno del vero proprio dove il report afferma di piu'.
    assert "effetti=" in testo and "matches=" in testo, testo
    # Le righe sopravvissute alla DELETE HTTP sono state rimosse via SQL, per
    # terne id+marcatore+agenzia: immobili e richieste, che sono le due
    # superfici le cui route DELETE archiviano soltanto.
    cancellazioni = " ".join(database.state.get("deletes", [])).upper()
    assert "FROM PROPERTIES" in cancellazioni and "FROM BUY_REQUESTS" in cancellazioni, (
        database.state.get("deletes", []))


def test_47c_a_residue_on_the_database_is_a_failure_whatever_http_said(monkeypatch):
    """E la verifica finale sa fallire.

    Il database dichiara righe ancora presenti: il verdetto e' FAIL anche se
    ogni DELETE ha risposto 2xx. Senza questo caso, `verify_no_residue`
    potrebbe essere una nota che dice sempre di si'.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, residue_rows=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    fallimenti = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    assert any(i == "CLEAN-VERIFICA" and "ANCORA PRESENTI" in t
               for i, t in fallimenti), fallimenti


def test_48_the_sale_precondition_is_satisfied_before_the_chain(monkeypatch):
    """SALE dava 409: mancava il legame proprietario.

    `create_sale_scoped` esige una riga owner/seller in `property_contacts`.
    Il doppio adesso la esige davvero, quindi se `link_property_owner` sparisse
    la catena si fermerebbe sulla vendita - come sul TEST.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    rows = {i: k for k, i, _ in report.rows}
    for label in ("A", "B"):
        assert rows.get(f"PROPERTY-relazione-{label}") == cert.PASS
        assert rows.get(f"chain-SALE-{label}") == cert.PASS, (
            "la vendita non e' stata creata: la precondizione del venditore "
            "non e' soddisfatta"
        )


def test_48b_without_the_owner_link_the_sale_is_refused_with_409(monkeypatch):
    """E la precondizione e' reale, non decorativa: senza legame, 409."""
    import scripts.p26_6_live_cert as module

    monkeypatch.setattr(module, "link_property_owner",
                        lambda *a, **k: None)
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    testi = [t for k, i, t in report.rows
             if k == cert.BLOCKED and i.startswith("chain-")]
    assert any("409" in t for t in testi), (
        f"senza il legame proprietario la vendita doveva fallire con 409: {testi}"
    )


# ---------------------------------------------------------------------------
# 49-52 - FOLLOWUP: la scansione, provata e non dichiarata
#
# L'assenza di GET rende non provabili le letture, non il dominio. La route che
# c'e' - POST /scan-temporal - e' una scrittura scopata, e una scrittura si
# prova cosi': ognuno la lancia, e si osserva che tocchi solo le proprie righe.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 49-53 - FOLLOWUP: si osserva la selezione, non si esegue l'escalation
#
# Tre difese sono state costruite e scartate prima di arrivare qui, e la
# ragione e' sempre la stessa: `scan-temporal` seleziona E scrive, e il
# predicato non ammette una restrizione agli id di questo run.
#
#   * contare le candidate prima: dice quante ce ne sono ADESSO. Il predicato
#     e' `due_at <= NOW() - INTERVAL '24 hours'`, quindi una riga preesistente
#     diventa eleggibile da sola, col passare del tempo;
#   * un secondo conteggio: insegue lo stesso istante che non puo' fermare;
#   * fixture con scadenza remotissima e limite 1: un inserimento concorrente
#     con scadenza ancora piu' vecchia la scavalca, e accorgersene dopo non e'
#     isolamento - la riga altrui e' gia' stata modificata.
#
# Resta provabile la SELEZIONE, dove l'isolamento fra agenzie vive per intero,
# ed e' una lettura pura.
# ---------------------------------------------------------------------------

def test_49_the_matrix_never_runs_the_followup_scan(monkeypatch):
    """LA GARANZIA: nessuna scrittura su FOLLOWUP, in nessuna direzione.

    Non "la scrittura e' circoscritta": non avviene. E' l'unica separazione
    costruibile senza cambiare il backend per far passare una prova.
    """
    _code, _report, database, probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME))

    scansioni = [label for label, _s, _b in probe.exchanges if "scan-temporal" in label]
    assert scansioni == [], f"la matrice ha lanciato la scansione: {scansioni}"
    creazioni = [label for label, _s, _b in probe.exchanges
                 if label == "POST /api/core/tasks"]
    assert creazioni == [], f"la matrice crea fixture FOLLOWUP: {creazioni}"
    assert not any("FOLLOWUP_ACTIONS" in d.upper()
                   for d in database.state.get("deletes", []))


def test_50_the_selection_is_observed_in_both_agencies(monkeypatch):
    """La selezione e' una SELECT: si esegue davvero, sui dati reali."""
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("FOLLOWUP-selezione-A") == cert.PASS
    assert rows.get("FOLLOWUP-selezione-B") == cert.PASS
    assert rows.get("FOLLOWUP-selezione-disgiunta") == cert.PASS


def test_51_a_task_selected_by_both_agencies_is_a_failure(monkeypatch):
    """Se la stessa attivita' comparisse in entrambe le selezioni, il predicato
    di tenant non starebbe facendo il suo lavoro."""
    # L'attivita' 99 e' vista da entrambe ed e' davvero di A: l'appartenenza
    # per A passa, quella per B fallirebbe per prima e nasconderebbe la
    # disgiunzione. `task_owner` la attribuisce a entrambe le richiedenti
    # tramite un'agenzia che coincide con quella interrogata, cosi' il primo
    # controllo passa e resta in piedi solo il difetto da osservare.
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}, {"id": 99}], 2: [{"id": 99}]},
        task_owner={99: None})
    righe = {i: (k, t) for k, i, t in report.rows}
    kind, testo = righe.get("FOLLOWUP-selezione-disgiunta", (None, ""))
    assert kind == cert.FAIL and "99" in testo, righe.get("FOLLOWUP-selezione-disgiunta")


def test_52_two_empty_selections_prove_nothing_and_are_BLOCKED(monkeypatch):
    """La regola di tutta la matrice vale anche qui: su insiemi vuoti la
    disgiunzione e' vera per costruzione e non dimostra nulla."""
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={})
    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("FOLLOWUP-selezione-disgiunta") == cert.BLOCKED


def test_53_the_escalation_is_BLOCKED_with_a_reason_taken_from_the_signature():
    """L'altra meta' della route resta NON PROVATA, e il motivo e' verificabile.

    Non e' un'opinione: la firma della selezione accetta solo `agency_id`,
    `limit` e `rule_code`. Se un domani accettasse un elenco di id, la
    separazione diventerebbe costruibile e questo test fallisce - cosi' che il
    BLOCKED non sopravviva alla ragione che lo giustifica.
    """
    import inspect

    from followup import repository

    firma = inspect.signature(
        repository.list_temporal_escalation_candidates_for_agency)
    assert set(firma.parameters) == {"agency_id", "limit", "rule_code"}, (
        f"la firma e' cambiata ({list(firma.parameters)}): la scansione "
        "potrebbe essere restringibile agli id del run e l'escalation va "
        "riportata nella matrice"
    )

    blocco = _function_source("certify_followup")
    assert "report.blocked(" in blocco and "FOLLOWUP-escalation" in blocco
    assert "FOLLOWUP_SCAN" not in blocco, "il certificatore lancia ancora la scansione"

    # E la selezione passa dalla funzione applicativa vera, non da una copia
    # della sua SQL ne' da un elenco vuoto: nei test quella funzione e'
    # sostituita da un doppio, quindi la sua implementazione non viene mai
    # esercitata e solo un controllo sul codice puo' dire che c'e' davvero.
    lettura = _function_source("stale_followup_candidates")
    albero = ast.parse(lettura)
    invocate = [n for n in ast.walk(albero) if isinstance(n, ast.Call)]
    nomi = {getattr(c.func, "attr", None) or getattr(c.func, "id", None)
            for c in invocate}
    assert "list_temporal_escalation_candidates_for_agency" in nomi, (
        f"la selezione non chiama il predicato applicativo: {sorted(n for n in nomi if n)}"
    )
    ritorni = [n for n in ast.walk(albero) if isinstance(n, ast.Return)]
    assert len(ritorni) == 1 and isinstance(ritorni[0].value, ast.Call), (
        "la selezione ha un'uscita che non passa dal predicato: una scorciatoia "
        "qui renderebbe la disgiunzione vera per costruzione"
    )


def test_53b_a_task_belonging_to_another_agency_is_caught(monkeypatch):
    """LA PROVA CHE LA DISGIUNZIONE NON DA'.

    Due selezioni possono essere disgiunte e sbagliate entrambe: basta che la
    selezione di A restituisca attivita' di un'agenzia terza e quella di B di
    un'altra ancora. Nessun id in comune, e nessuna delle due appartiene a chi
    l'ha chiesta.

    Qui l'attivita' 11 e' selezionata da A ma appartiene all'agenzia 99. La
    disgiunzione resta vera; l'appartenenza no.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]},
        task_owner={11: 99})
    fallimenti = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    assert any(i == "FOLLOWUP-appartenenza-A" and "99" in t
               for i, t in fallimenti), fallimenti


def test_53c_an_empty_selection_cannot_prove_ownership(monkeypatch):
    """Un caso positivo vuoto non e' un caso positivo: senza righe non c'e'
    nulla di cui verificare l'agenzia, e dichiararlo superato sarebbe la stessa
    vacuita' che questa matrice combatte ovunque."""
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}]})
    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("FOLLOWUP-appartenenza-A") == cert.PASS
    assert rows.get("FOLLOWUP-appartenenza-B") == cert.BLOCKED


# ---------------------------------------------------------------------------
# 56-60 - FOLLOWUP su agenzie dedicate
#
# La separazione che ordinamento, limite e controlli a posteriori non davano:
# su un'agenzia creata dal run non esiste una riga che non sia nostra, quindi
# `WHERE t.agency_id = %s` diventa il confine. Il backend non cambia.
# ---------------------------------------------------------------------------

def test_56_without_the_flag_nothing_is_created_and_the_escalation_stays_BLOCKED(monkeypatch):
    """Il percorso dedicato non parte da solo.

    Creare agenzie e' l'operazione piu' consequenziale che questo script sappia
    fare: deve richiedere un'attestazione esplicita, non un default.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("FOLLOWUP-escalation") == cert.BLOCKED
    assert not any(str(x).upper().startswith("INSERT INTO AGENCIES")
                   for x in database.state.get("sql", []))


def test_57_with_the_flag_the_scan_runs_on_dedicated_agencies(monkeypatch):
    """Con l'attestazione, la route reale viene esercitata nelle due direzioni."""
    code, report, database, probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    rows = {i: k for k, i, _ in report.rows}
    for label, altro in (("C", "D"), ("D", "C")):
        assert rows.get(f"FOLLOWUP-scan-{label}") == cert.PASS
        assert rows.get(f"FOLLOWUP-scan-{label}-elabora-la-propria") == cert.PASS
        assert rows.get(f"FOLLOWUP-scan-{label}-solo-la-propria") == cert.PASS
        assert rows.get(f"FOLLOWUP-scan-{label}-{altro}-invariata") == cert.PASS
    # E l'escalation non e' piu' una lacuna dichiarata.
    assert rows.get("FOLLOWUP-escalation") == cert.PASS
    # Due agenzie create, due rimosse.
    assert len([x for x in database.state.get("sql", [])
                if x.upper().startswith("INSERT INTO AGENCIES")]) == 2
    assert rows.get("CLEAN-DEDICATA") == cert.PASS


@pytest.mark.parametrize("kind,expected", [
    # La scansione elenca una riga che non e' del run: il confine dell'agenzia
    # dedicata non ha tenuto.
    ("intruso_dopo_preflight", "FOLLOWUP-scan-C-solo-la-propria"),
    # La scansione non elabora nemmeno la propria: la prova positiva e' vuota,
    # e senza di essa "non ha toccato nulla di altrui" sarebbe vero per la
    # ragione sbagliata.
    ("scan_skips_own", "FOLLOWUP-scan-C-elabora-la-propria"),
    # Peggio: tocca l'attivita' dell'altra agenzia SENZA nominarla. Gli id
    # tornati sono puliti, e solo il confronto con l'istantanea se ne accorge.
    ("scan_mutates_other", "FOLLOWUP-scan-C-D-invariata"),
])
def test_58_each_dedicated_scan_assertion_fails_on_its_own_defect(
        monkeypatch, kind, expected):
    """Due difetti distinti, due asserzioni distinte.

    Un test che si accontentasse di "una prova FOLLOWUP e' fallita" sarebbe
    soddisfatto da una sonda vicina, e le altre resterebbero indebolibili senza
    che nulla lo dicesse - e' gia' successo due volte in questo file.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(broken={kind}, only="followup",
                      prepopulate=DERIVED, stime=STIME))
    fallimenti = [i for k, i, _ in report.rows if k == cert.FAIL]
    assert expected in fallimenti, f"rompendo {kind!r}: {fallimenti}"
    assert code == 1


def test_59_a_surviving_dedicated_agency_is_a_failure_with_a_recovery_hint(monkeypatch):
    """Il residuo peggiore possibile: una radice di tenancy.

    Il report deve nominarla con l'id - non con lo slug, non con dati personali
    - e dire come rimuoverla a mano.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True, agenzie_residue=2,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    fallimenti = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    testo = next((t for i, t in fallimenti if i == "CLEAN-DEDICATA"), None)
    assert testo is not None, fallimenti
    assert "ANCORA PRESENTI" in testo and "RECUPERO:" in testo
    assert "mai per prefisso" in testo


def test_60_the_dedicated_cleanup_deletes_by_id_and_never_by_prefix():
    """Il catalogo serve a SCOPRIRE, non ad autorizzare.

    L'elenco delle tabelle da cui cancellare e' scritto a mano e in ordine di
    dipendenza. L'interrogazione del catalogo che segue e' solo una lettura: se
    trova una dipendenza imprevista la nomina e fa fallire, e non la cancella -
    cancellare cio' che nessuno aveva considerato e' il modo in cui un cleanup
    diventa il danno.
    """
    blocco = _function_source("cleanup_dedicated_agencies")
    assert "DEDICATED_TABLES" in blocco
    assert "IN %s" in blocco
    for vietato in ("LIKE", "slug", "p26-6-cert-"):
        assert vietato not in blocco, f"il cleanup usa {vietato} come criterio"

    # Le DELETE dinamiche dal catalogo non devono esistere: solo conteggi.
    fra_catalogo = blocco[blocco.index("pg_constraint"):]
    assert "DELETE" not in fra_catalogo, (
        "il cleanup cancella tabelle scoperte dal catalogo invece di limitarsi "
        "a segnalarle"
    )
    assert "SELECT COUNT(*)" in fra_catalogo


def test_61_the_api_side_effects_are_verified_not_assumed(monkeypatch):
    """Gli effetti delle API entrano nella verifica finale.

    Archiviare scrive history, calcolare scrive match_runs e i risultati per
    criterio. Sono figli CASCADE e dovrebbero sparire con il genitore - ma il
    run 22d007af7916 ne ha lasciati 32 sul TEST, perche' il genitore era stato
    archiviato invece che cancellato e nessuno li contava.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    assert {i: k for k, i, _ in report.rows}.get("CLEAN-VERIFICA") == cert.PASS

    blocco = _function_source("verify_no_residue")
    dichiarate = {fk.table for fk in cert.Certification.EFFECT_FOREIGN_KEYS}
    for tabella in ("property_status_history", "buy_request_history",
                    "match_runs", "owner_audit_log"):
        assert tabella in dichiarate, f"la verifica finale ignora {tabella}"
    # E la verifica le percorre davvero: un elenco dichiarato e mai letto
    # sarebbe una descrizione, non un controllo.
    assert "EFFECT_FOREIGN_KEYS" in blocco, blocco[:200]
    assert "match_requirement_results" in blocco, "la verifica finale ignora i risultati"


@pytest.mark.parametrize("tabella", [
    "property_status_history", "buy_request_history", "match_runs",
    "owner_audit_log", "match_requirement_results",
])
def test_62_a_surviving_side_effect_is_a_residue(monkeypatch, tabella):
    """Ognuna sa fallire per conto proprio.

    Un test che si accontentasse di "la verifica ha fallito" sarebbe soddisfatto
    da una tabella vicina, e le altre resterebbero scoperte - lo stesso difetto
    gia' trovato tre volte in questo file.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        effetti_residui={tabella: 3})
    fallimenti = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    assert any(i == "CLEAN-VERIFICA" and tabella in t for i, t in fallimenti), (
        f"{tabella} sopravvive e non viene segnalata: {fallimenti}")


def test_63_the_run_audit_trail_is_removed_by_id(monkeypatch):
    """L'audit del run se ne va, e per id.

    `owner_audit_log` ha entrambe le FK in SET NULL: senza una cancellazione
    esplicita la riga resterebbe con due colonne azzerate - una traccia che non
    dice piu' di cosa parlava, e che nessun run successivo saprebbe attribuire.
    """
    _code, _report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    cancellazioni = [d for d in database.state.get("deletes", [])
                     if "OWNER_AUDIT_LOG" in d.upper()]
    assert cancellazioni, database.state.get("deletes", [])
    assert "owner_account_id IN %s" in cancellazioni[0], cancellazioni[0]
    for vietato in ("LIKE", "created_at", "action ="):
        assert vietato not in cancellazioni[0]


def test_64_a_foreign_dependency_appearing_after_the_census_stops_the_cleanup(monkeypatch):
    """LA CORSA CHE IL CONTEGGIO NON VEDE.

    Il censimento dice che nessuno referenzia le nostre righe. Poi, prima del
    cleanup, compare una riga di qualcun altro che le punta - un'attivita', una
    visita, una proposta creata nel frattempo.

    Le quaranta righe del run sono ancora tutte al loro posto, quindi il
    conteggio "40" torna: non rileva nulla. Ma cancellare adesso avrebbe un
    effetto collaterale - CASCADE porterebbe via quella riga, SET NULL le
    azzererebbe un campo - e sono entrambi danni a dati non nostri.

    Il cleanup deve fermarsi, dire dove, e non toccare niente.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        fk_perimetro={"properties": [("public.property_visits", "property_id")]},
        dipendenti_estranee=1)

    fallimenti = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    # Il preflight la trova PRIMA della prima DELETE: e' li' che il cleanup si
    # ferma, non piu' a meta' strada quando vendite e conti sono gia' spariti.
    testo = next((t for i, t in fallimenti if i == "CLEAN-PREFLIGHT"), None)
    assert testo is not None, fallimenti
    assert "fuori perimetro" in testo and "property_visits" in testo, testo
    assert "Nessuna DELETE" in testo

    # E davvero non ha cancellato: nessuna DELETE sulle tabelle del perimetro.
    cancellazioni = " ".join(database.state.get("deletes", [])).upper()
    for tabella in ("FROM PROPERTIES", "FROM BUY_REQUESTS", "FROM CONTACTS"):
        assert tabella not in cancellazioni, (
            f"ha cancellato {tabella} nonostante la dipendenza estranea")
    # E non ha nemmeno toccato la riga estranea.
    assert "PROPERTY_VISITS" not in cancellazioni


def test_65_being_unable_to_look_is_not_the_same_as_nothing_found(monkeypatch):
    """Se la verifica delle dipendenze non e' eseguibile, non si procede.

    Un `except` che restituisse una lista vuota trasformerebbe un errore di
    lettura in un via libera.
    """
    blocco = _function_source("_foreign_dependencies")
    assert "return [f\"verifica non eseguibile" in blocco or \
           "verifica non eseguibile" in blocco, blocco[-300:]
    assert "return []" not in blocco.split("except")[-1], (
        "in caso di errore la guardia restituisce 'nessuna dipendenza'")


def test_66_rows_of_the_run_referencing_each_other_are_not_foreign(monkeypatch):
    """L'esclusione del perimetro non e' un dettaglio.

    `buy_requests.contact_id` punta ai nostri contatti: senza escludere le
    righe che sono esse stesse del run, la guardia le conterebbe come estranee
    e il cleanup si rifiuterebbe di procedere a ogni esecuzione - trasformando
    una difesa in un blocco permanente.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        fk_perimetro={"contacts": [("public.buy_requests", "contact_id")]},
        dipendenti_interne=2, dipendenti_estranee=0)

    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("CLEAN-ORFANE") == cert.PASS, [
        r for r in report.rows if r[1] == "CLEAN-ORFANE"]
    # E ha davvero cancellato.
    cancellazioni = " ".join(database.state.get("deletes", [])).upper()
    assert "FROM CONTACTS" in cancellazioni


# ---------------------------------------------------------------------------
# 67-69 - RUN 9b95b3ee215e: le due cause dei residui e del falso PASS
# ---------------------------------------------------------------------------

def test_67_the_runs_own_effects_are_not_foreign_and_get_cleaned(monkeypatch):
    """IL CASO LIVE: property_contacts, history, match_runs, risultati.

    La guardia sulle dipendenze vedeva `property_contacts` - il legame che il
    run stesso crea - come riga estranea, rifiutava tutto e non cancellava
    nulla: sei radici e i loro effetti sono rimasti sul TEST, di nuovo.

    La guardia aveva ragione a rifiutare cio' che non riconosceva. Il difetto
    era il perimetro: gli effetti delle nostre API non vi erano dichiarati.
    Adesso una riga di quelle tabelle e' del run se OGNI suo riferimento non
    nullo cade nel perimetro - e allora si cancella, prima delle radici.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        fk_perimetro={"properties": [("public.property_contacts", "property_id"),
                                     ("public.property_status_history", "property_id"),
                                     ("public.match_runs", "property_id")],
                      "buy_requests": [("public.buy_request_history", "buy_request_id")]},
        dipendenti_interne=0, dipendenti_estranee=0)
    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("CLEAN-ORFANE") == cert.PASS, [r for r in report.rows if r[1] == "CLEAN-ORFANE"]
    cancellazioni = " ".join(database.state.get("deletes", [])).upper()
    for tabella in ("PROPERTY_CONTACTS", "PROPERTY_STATUS_HISTORY", "BUY_REQUEST_HISTORY",
                    "MATCH_RUNS", "OWNER_AUDIT_LOG"):
        assert f"FROM {tabella} T WHERE" in cancellazioni, (
            f"{tabella} non viene rimossa: {database.state.get('deletes')}")
    # E le radici DOPO gli effetti: property_contacts e' RESTRICT su contacts.
    ordine = database.state.get("deletes", [])
    i_effetto = next(i for i, d in enumerate(ordine) if "FROM property_contacts" in d)
    i_radice = next(i for i, d in enumerate(ordine) if "FROM contacts t USING" in d)
    assert i_effetto < i_radice


def test_67b_an_effect_row_pointing_outside_the_perimeter_is_still_foreign(monkeypatch):
    """Il predicato di appartenenza non e' un lasciapassare per tabella.

    Una riga di `property_contacts` che lega il NOSTRO immobile a un contatto
    che non e' nostro non e' del run: e' qualcuno che ha agganciato una
    persona vera alla fixture. La guardia deve fermarsi, come prima.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        fk_perimetro={"properties": [("public.property_contacts", "property_id")]},
        dipendenti_estranee=1)
    fallimenti = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    assert any(i == "CLEAN-ORFANE" and "property_contacts" in t for i, t in fallimenti), fallimenti
    assert "FROM PROPERTIES T USING" not in " ".join(database.state.get("deletes", [])).upper()


@pytest.mark.parametrize("kind,expected", [
    ("scan_lies", "FOLLOWUP-scan-C-task-escalato"),
    ("scan_item_fails", "FOLLOWUP-scan-C-elabora-la-propria"),
])
def test_68_being_listed_is_not_escalation(monkeypatch, kind, expected):
    """Tre verifiche distinte per un solo 'elaborato'.

    `scan_lies`: l'elemento dice 'completed' ma il task resta open/low - e'
    esattamente cio' che e' successo alla fixture C. `scan_item_fails`:
    l'elemento c'e', con stato 'failed'. Prima entrambe passavano, perche' la
    prova guardava solo la presenza nell'elenco.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(broken={kind}, only="followup", prepopulate=DERIVED, stime=STIME))
    fallimenti = [i for k, i, _ in report.rows if k == cert.FAIL]
    assert expected in fallimenti, f"rompendo {kind!r}: {fallimenti}"
    assert code == 1


def test_69_a_completed_item_without_a_persisted_action_is_caught(monkeypatch):
    """La terza gamba: l'azione deve esistere sul database.

    Un elemento 'completed' e un task escalato senza una riga in
    followup_actions vorrebbero dire che l'idempotenza non ha memoria - la
    prossima scansione lo escalerebbe di nuovo.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True, azioni_persistite=0,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    fallimenti = [i for k, i, _ in report.rows if k == cert.FAIL]
    assert "FOLLOWUP-scan-C-azione-persistita" in fallimenti, fallimenti


def test_70_the_ownership_predicate_is_never_vacuously_true():
    """Il predicato "questa riga e' del run" non puo' degenerare in TRUE.

    Se nessuna colonna puo' puntare dentro il perimetro, non esiste una riga
    che sia nostra: la risposta e' None, non un predicato sempre vero che
    farebbe cancellare l'intera tabella.
    """
    report, _ = quiet_report()
    c = cert.Certification(fake_database(agencies=AGENCIES), report)

    # Perimetro senza immobili: property_status_history non puo' essere nostra.
    assert c._ownership_predicate(("property_id",), {"contacts": (1,)}, "t") is None

    frammento, params = c._ownership_predicate(
        ("property_id", "contact_id"), {"properties": (42, 43), "contacts": (82,)}, "t")
    # Almeno un riferimento DENTRO, e ogni riferimento non nullo dentro.
    assert frammento.startswith("(t.property_id IN %s OR t.contact_id IN %s) AND")
    assert "(t.property_id IS NULL OR t.property_id IN %s)" in frammento
    assert "(t.contact_id IS NULL OR t.contact_id IN %s)" in frammento
    assert params == [(42, 43), (82,), (42, 43), (82,)]
    assert "TRUE" not in frammento

    # Una colonna il cui genitore non e' nel perimetro DEVE essere NULL.
    frammento, _ = c._ownership_predicate(
        ("buy_request_id", "match_id"), {"buy_requests": (28,)}, "t")
    assert "t.match_id IS NULL" in frammento and "t.match_id IN" not in frammento


def test_70b_the_guard_counts_only_effect_rows_outside_the_perimeter():
    """La query della guardia sugli effetti nega il predicato di appartenenza.

    Il doppio non esegue SQL: una mutazione che aggiungesse `WHERE FALSE` alla
    query non verrebbe vista da nessun run simulato. Si legge il codice.
    """
    blocco = _function_source("_foreign_dependencies")
    assert 'WHERE t.{colonna} IN %s AND NOT ({frammento})' in blocco
    assert "WHERE FALSE" not in blocco
    # E gli effetti si cancellano CON il predicato, non per genitore - e per
    # gli id fotografati prima, cosi' che una riga cambiata nel frattempo non
    # rientri nella cancellazione.
    cleanup = _function_source("cleanup_orphan_fixtures")
    assert 'DELETE FROM {table} t WHERE t.id IN %s AND ({frammento})' in cleanup


# ---------------------------------------------------------------------------
# 71-73 - OWNER Portal, documenti: il contratto reale
# ---------------------------------------------------------------------------

def test_71_a_published_document_is_seen_by_its_owner_and_not_by_the_other(monkeypatch):
    """Il 200 sull'immobile altrui NON e' una fuga: `portal_shared_documents`
    filtra per conto nella JOIN e risponde lista vuota. Il run 9b95b3ee215e lo
    aveva segnato FAIL perche' la sonda pretendeva 403/404.

    La prova regge solo con un documento vero: il proprietario legittimo lo
    vede, l'altro riceve 200 vuoto senza marcatore."""
    _code, report, _db, probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    rows = {i: (k, t) for k, i, t in report.rows}
    for label, other in (("A", "B"), ("B", "A")):
        assert rows[f"owner-fixture-{label}-documento"][0] == cert.PASS
        assert rows[f"OWNER_PORTAL-documenti-{label}"][0] == cert.PASS
        k, t = rows[f"OWNER_PORTAL-documenti-{label}-{other}"]
        assert k == cert.PASS and "con lista vuota" in t, (k, t)
        assert rows[f"OWNER_PORTAL-download-{label}"][0] == cert.PASS
        assert rows[f"OWNER_PORTAL-download-{label}-{other}"][0] == cert.PASS


def test_72_a_document_list_that_leaks_is_still_caught(monkeypatch):
    """Il 200 e' accettabile SOLO vuoto: se la lista altrui portasse il
    documento dell'altra agenzia, e' FAIL."""
    code, failures, _r = _first_failure(monkeypatch, "portal_documents", "portal")
    assert "OWNER_PORTAL-documenti-A-B" in failures, failures
    assert code == 1


def test_73_without_storage_the_download_stays_BLOCKED_not_PASS(monkeypatch):
    """Il download apre lo storage. Su un TEST senza storage anche il
    proprietario legittimo riceve 404: il rifiuto verso l'altro sarebbe
    ambiguo, e la prova resta BLOCKED."""
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(broken={"no_storage"}, only="portal",
                                   prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("OWNER_PORTAL-download-A") == cert.BLOCKED
    assert "OWNER_PORTAL-download-A-B" not in rows, "download ostile eseguito senza positivo"


@pytest.mark.parametrize("kind,expected", [
    ("docs_anon_leak", "OWNER_PORTAL-documenti-A-B"),
    ("docs_marker_leak", "OWNER_PORTAL-documenti-A-B"),
    ("docs_hide_own", "OWNER_PORTAL-documenti-A"),
])
def test_74_each_document_defence_is_load_bearing(monkeypatch, kind, expected):
    """Le due difese sulla lista altrui - "vuota" e "senza marcatore" - si
    coprono a vicenda: ciascuna sopravvive se l'altra e' sana. Qui ogni rottura
    ne aggira una sola. E la prova positiva deve vedere IL documento, non un 200."""
    code, failures, _r = _first_failure(monkeypatch, kind, "portal")
    assert expected in failures, f"rompendo {kind!r}: {failures}"


@pytest.mark.parametrize("kind,expected", [
    ("flow_leak", "FLOW-list-C-non-vede-D"),
    ("nba_leak", "NEXT_BEST_ACTION-disgiunte"),
])
def test_75_each_batch_domain_fails_on_its_own_defect(monkeypatch, kind, expected):
    """FLOW e NEXT_BEST_ACTION, rotti uno alla volta.

    Una lista non vuota non basta: se quella di C contenesse l'evento di D, o
    se le due liste NBA condividessero un id, la prova deve fallire nominando
    la propria sonda - non una vicina.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(broken={kind}, only="batch", prepopulate=DERIVED, stime=STIME))
    fallimenti = [i for k, i, _ in report.rows if k == cert.FAIL]
    assert expected in fallimenti, f"rompendo {kind!r}: {fallimenti}"
    assert code == 1


def test_76_an_empty_nba_refresh_is_BLOCKED_not_PASS(monkeypatch):
    """Zero azioni materializzate: la disgiunzione sarebbe vera per
    costruzione e non direbbe nulla sull'isolamento."""
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(broken={"nba_vuoto"}, only="batch", prepopulate=DERIVED, stime=STIME))
    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("NEXT_BEST_ACTION-disgiunte") == cert.BLOCKED


def test_77_a_watch_that_crosses_the_dedicated_agency_is_caught(monkeypatch):
    """La stima e' del run, l'agenzia e' del run: se il watch dell'altra
    fosse leggibile, il predicato di tenant non terrebbe."""
    code, report, _db, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(broken={"isolation"}, only="watch", prepopulate=DERIVED, stime=STIME))
    fallimenti = [i for k, i, _ in report.rows if k == cert.FAIL]
    assert any(i.startswith("PROPERTY_WATCH-ostile-C") for i in fallimenti), fallimenti
    assert code == 1


def test_78_the_stima_is_created_by_the_run_and_removed_with_the_agency(monkeypatch):
    """La stima non nasce dal funnel: viene inserita nell'agenzia del run.

    Nessun invio, nessun PDF, nessuna email - quelli stanno in `salva_stima`,
    non nell'INSERT - e nessun dato preesistente toccato. Sparisce con
    l'agenzia dedicata, che e' cancellata per id.
    """
    _code, _report, database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    sql = " ".join(database.state.get("sql", [])).upper()
    assert "INSERT INTO STIME" in sql
    cancellazioni = " ".join(database.state.get("deletes", [])).upper()
    for tabella in ("STIME", "PROPERTY_WATCHES", "FLOW_EVENTS", "NEXT_BEST_ACTIONS"):
        assert f"FROM {tabella} WHERE AGENCY_ID IN" in cancellazioni, (
            f"{tabella} non viene rimossa con l'agenzia dedicata")


def test_79_the_error_shape_never_carries_the_raw_message():
    """Un messaggio grezzo puo' contenere valori di riga: psycopg2 mette nel
    DETAIL la chiave che ha violato il vincolo. Ripulirlo da email e cifre non
    basta - resterebbero nomi, indirizzi, titoli. Si estrae la FORMA."""
    grezzo = ('duplicate key value violates unique constraint '
              '"followup_actions_idempotency_key_key"\n'
              'DETAIL:  Key (idempotency_key)=(followup:time:X:task:501:v1) '
              'already exists. SQLSTATE 23505')
    forma = cert._error_shape(grezzo)
    assert "sqlstate=23505" in forma
    assert "vincolo=followup_actions_idempotency_key_key" in forma
    for perso in ("DETAIL", "Key (", "task:501", "already exists"):
        assert perso not in forma, f"la forma porta ancora {perso!r}: {forma}"

    assert cert._error_shape(None) == "(vuoto)"
    ignoto = cert._error_shape("Mario Rossi via Roma 3 non trovato")
    assert "non classificato" in ignoto and "Mario" not in ignoto


@pytest.mark.parametrize("kind,surface,expected", [
    ("flow_hide_own", "batch", "FLOW-list-C-vede-la-propria"),
    ("watch_write_leak", "watch", "PROPERTY_WATCH-ostile-write-C-D"),
])
def test_80_the_remaining_dedicated_probes_fail_on_their_own_defect(
        monkeypatch, kind, surface, expected):
    """Le due sonde che restavano coperte da una vicina.

    `flow_hide_own`: la lista nasconde i propri eventi, quindi "non vede quelli
    di D" e' vero perche' non vede niente. `watch_write_leak`: l'inizializzazione
    sulla stima altrui riesce - la lettura e' ancora rifiutata, quindi solo la
    sonda in scrittura lo vede.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(broken={kind}, only=surface, prepopulate=DERIVED, stime=STIME))
    fallimenti = [i for k, i, _ in report.rows if k == cert.FAIL]
    assert expected in fallimenti, f"rompendo {kind!r}: {fallimenti}"
    assert code == 1


def test_81_an_nba_row_of_another_agency_is_caught(monkeypatch):
    """La disgiunzione non dice l'appartenenza: due insiemi possono essere
    disgiunti e materializzati entrambi nell'agenzia sbagliata."""
    code, report, _db, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True, nba_estranee=1,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    fallimenti = [i for k, i, _ in report.rows if k == cert.FAIL]
    assert "NEXT_BEST_ACTION-appartenenza-C" in fallimenti, fallimenti
    assert code == 1


def test_82_without_the_storage_backend_the_portal_has_nothing_to_show(monkeypatch):
    """Nessun oggetto nello storage: niente elenco e niente scaricamento.

    L'ULTIMA RIGA DI QUESTO TEST DICEVA IL CONTRARIO, ED ERA FALSA.

    Affermava che "il documento via URL basta" per la lista del portale. Non
    basta: `create_shared_document` rifiuta con 422 un documento senza
    `storage_key`, quindi quella condivisione non nasceva mai e la lista era
    vuota per entrambe le agenzie - il 422 dei run 52f6d97b5214 e
    58aa0e189aaa. Adesso il documento del portale e' quello CARICATO, e senza
    storage non c'e' nulla da elencare: e' una perdita di copertura reale, e
    va dichiarata BLOCKED invece di essere simulata da un URL che il backend
    non accetterebbe.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(broken={"no_storage_backend"}, only="portal",
                                   prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    righe = {i: (k, t) for k, i, t in report.rows}
    k, t = righe["owner-fixture-A-documento"]
    assert k == cert.BLOCKED and "OWNER_DOCUMENT_STORAGE_ENABLED" in t, (k, t)
    assert righe["OWNER_PORTAL-download-A"][0] == cert.BLOCKED
    assert righe["OWNER_PORTAL-documenti-A"][0] == cert.BLOCKED, \
        righe["OWNER_PORTAL-documenti-A"]


def test_83_uploaded_objects_are_removed_from_the_bucket(monkeypatch):
    """Il file caricato non resta nel bucket.

    `storage.delete_object` esiste ma nel codice applicativo e' invocata solo
    sul rollback di un caricamento fallito: nessuna route la chiama quando un
    documento viene revocato o archiviato. Cancellare le righe lascerebbe un
    file che nessun censimento SQL vedrebbe mai - non e' sul database.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("CLEAN-STORAGE") == cert.PASS, [r for r in report.rows if "STORAGE" in r[1]]
    assert database.state.get("chiavi_cancellate"), "nessun oggetto rimosso dal bucket"
    # Due documenti caricati, due oggetti rimossi: se l'id di origine non
    # venisse registrato, la chiave non sarebbe recuperabile e il bucket
    # resterebbe sporco in silenzio.
    assert len(database.state["chiavi_cancellate"]) == 2, database.state["chiavi_cancellate"]

    # E la chiave si legge PRIMA di cancellare la riga che la contiene.
    sql = database.state.get("sql", [])
    i_lettura = next(i for i, q in enumerate(sql)
                     if q.upper().startswith("SELECT ID, STORAGE_KEY"))
    i_delete = next((i for i, q in enumerate(sql)
                     if "DELETE FROM property_documents" in q), len(sql))
    assert i_lettura < i_delete


@pytest.mark.parametrize("guasto,atteso", [
    ("storage_assente", "RESTANO nel bucket"),
    ("storage_rotto", "NON rimossi dal bucket"),
])
def test_84_a_bucket_that_cannot_be_cleaned_is_a_failure(monkeypatch, guasto, atteso):
    """Non poter pulire non e' "pulito": e' FAIL, con gli id dei documenti e
    dove ritrovare la chiave. Mai la chiave stessa, che e' un localizzatore."""
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]}, **{guasto: True})
    fallimenti = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    testo = next((t for i, t in fallimenti if i == "CLEAN-STORAGE"), None)
    assert testo is not None, fallimenti
    assert atteso in testo
    assert "k/" not in testo, f"il report stampa la chiave dell'oggetto: {testo}"


def test_85_a_url_only_document_needs_no_bucket_cleanup(monkeypatch):
    """Un documento nato da un URL non ha nulla nel bucket: la pulizia lo dice,
    invece di fallire per un oggetto che non e' mai esistito."""
    _code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]}, senza_chiave=True)
    righe = {i: (k, t) for k, i, t in report.rows}
    k, t = righe["CLEAN-STORAGE"]
    assert k == cert.PASS and "niente da rimuovere" in t
    assert not database.state.get("chiavi_cancellate")


def _dedicato(monkeypatch, **stato):
    """Un run con le agenzie dedicate e i doppi che materializzano le righe."""
    return working_run(monkeypatch, dedicated_agencies=True,
                       http=FakeHttp(prepopulate=DERIVED, stime=STIME), **stato)


def test_86_a_surviving_child_is_caught_after_the_parent_is_gone(monkeypatch):
    """La figlia che sopravvive al genitore.

    `seller_revival_suppressions` e' ON DELETE CASCADE verso `contacts`:
    cancellando il contatto dovrebbe sparire. "Dovrebbe" non e' "l'ha fatto",
    ed e' proprio questo che la verifica precedente non poteva vedere - la sua
    JOIN passava dal contatto, che a quel punto non c'era piu'.
    """
    _code, report, _db, _probe, _ = _dedicato(
        monkeypatch, figli_cascata={"seller_revival_suppressions": (3, 3)})
    fallimenti = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    assert any(i == "CLEAN-VERIFICA" and "seller_revival_suppressions=3" in t
               for i, t in fallimenti), fallimenti


def test_86b_a_child_that_really_disappears_is_reported_before_and_after(monkeypatch):
    """3 prima, 0 dopo: la sola forma in cui "il CASCADE ha funzionato" e'
    un'affermazione e non una formalita'. Senza il "prima" il report direbbe
    0 anche su un database in cui non e' mai esistito nulla."""
    _code, report, _db, _probe, _ = _dedicato(
        monkeypatch, figli_cascata={"seller_revival_suppressions": (3, 0)})
    righe = {i: (k, t) for k, i, t in report.rows}
    assert "CLEAN-FIGLIE" in righe, [r[1] for r in report.rows]
    k, t = righe["CLEAN-FIGLIE"]
    assert k == cert.PASS
    assert "seller_revival_suppressions (CASCADE): 3 prima, 0 dopo" in t, t
    assert righe["CLEAN-VERIFICA"][0] == cert.PASS


def test_86c_the_verification_does_not_join_the_deleted_parent(monkeypatch):
    """La query finale sulle figlie non nomina il genitore.

    Una JOIN a una riga appena cancellata restituisce 0 qualunque cosa sia
    rimasta: e' il modo in cui una verifica smette di verificare senza che il
    report cambi di una riga.
    """
    _code, _report, database, _probe, _ = _dedicato(
        monkeypatch, figli_cascata={"seller_revival_suppressions": (1, 0)})
    finali = [q for q in database.state["sql"]
              if "FROM seller_revival_suppressions" in q]
    assert finali, database.state["sql"]
    assert not any("JOIN" in q.upper() for q in finali), finali
    # E l'ultima interrogazione arriva DOPO la cancellazione del genitore.
    sql = database.state["sql"]
    i_delete = max(i for i, q in enumerate(sql)
                   if q.startswith("DELETE FROM contacts WHERE agency_id"))
    i_check = max(i for i, q in enumerate(sql)
                  if "FROM seller_revival_suppressions" in q)
    assert i_check > i_delete, (i_check, i_delete)


def test_86d_a_restrict_child_stops_the_deletion_instead_of_crashing_it(monkeypatch):
    """`property_watch_observations.watch_id` e
    `invisible_sale_opportunities.watch_id` sono ON DELETE **RESTRICT**: una
    riga la' dentro non viene portata via dal DELETE, lo fa fallire - e con
    esso l'intera transazione del cleanup. Si nomina e ci si ferma, invece di
    cancellare righe che il run non ha creato."""
    _code, report, database, _probe, _ = _dedicato(
        monkeypatch, figli_cascata={"property_watch_observations": (2, 2)})
    fallimenti = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    assert any(i == "CLEAN-DEDICATA" and "property_watch_observations=2" in t
               for i, t in fallimenti), fallimenti
    assert not any(q.startswith("DELETE FROM property_watches WHERE agency_id")
                   for q in database.state["sql"]), \
        "il watch dedicato e' stato cancellato lo stesso"


def test_86e_without_parents_the_report_says_so_instead_of_passing(monkeypatch):
    """Nessun watch nelle agenzie dedicate: la verifica non prova nulla, e
    deve dirlo. "0 figlie" su 0 genitori non e' una prova, e un report che
    tacesse la differenza renderebbe indistinguibile un CASCADE riuscito da
    una verifica mai eseguita."""
    # `senza_watch` toglie i watch DEDICATI; la fixture delle agenzie
    # condivise ne crea comunque due, quindi per ottenere "nessun genitore"
    # serve anche che quella non riesca - `senza_initialize` la ferma.
    _code, report, _db, _probe, _ = _dedicato(
        monkeypatch, senza_watch=True, senza_initialize=True)
    righe = {i: (k, t) for k, i, t in report.rows}
    testo = righe["CLEAN-FIGLIE"][1]
    assert "property_watch_observations: nessun property_watches del run" in testo, testo
    # E il contatto, che invece esiste, resta verificato: la reticenza e'
    # circoscritta al genitore mancante.
    assert "seller_revival_suppressions (CASCADE): 0 prima, 0 dopo" in testo, testo


@functools.lru_cache(maxsize=1)
def _fk_delle_migrazioni():
    """{(figlia, colonna, genitore, azione)} letto dalle migration.

    Il corpo di ogni CREATE TABLE si delimita CONTANDO LE PARENTESI, non
    cercando `\\n);`: `owner_accounts` e altre tabelle sono dichiarate su una
    riga sola, e una regex ancorata all'a capo inghiottiva le tabelle
    successive attribuendo loro colonne che non hanno.

    Si leggono anche le `ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY`:
    quasi tutte le colonne `agency_id` sono state aggiunte cosi', e fermarsi
    alle CREATE TABLE avrebbe dichiarato completa un'analisi che non vedeva
    meta' delle chiavi.
    """
    testo = "\n".join(p.read_text(encoding="utf-8")
                      for p in sorted((ROOT / "migrations").glob("*.sql")))
    dentro = re.compile(
        r"[\s,(]([a-z_][a-z0-9_]*)\s+[A-Za-z][^,]*?REFERENCES\s+([a-z_]+)\s*\(\s*id\s*\)"
        r"(?:\s+ON DELETE\s+(CASCADE|RESTRICT|SET NULL|NO ACTION))?", re.I)
    alterata = re.compile(
        r"ALTER TABLE\s+(?:IF EXISTS\s+)?(?:ONLY\s+)?([a-z_]+)[^;]*?FOREIGN KEY\s*"
        r"\(\s*([a-z_]+)\s*\)\s*REFERENCES\s+([a-z_]+)\s*\(\s*id\s*\)"
        r"(?:[^;]*?ON DELETE\s+(CASCADE|RESTRICT|SET NULL|NO ACTION))?", re.I | re.S)
    trovate = set()
    for testa in re.finditer(r"CREATE TABLE (?:IF NOT EXISTS )?([a-z_][a-z0-9_]*)\s*\(", testo):
        i, livello = testa.end(), 1
        while i < len(testo) and livello:
            livello += 1 if testo[i] == "(" else -1 if testo[i] == ")" else 0
            i += 1
        for f in dentro.finditer(testo[testa.end():i - 1]):
            trovate.add((testa.group(1), f.group(1), f.group(2),
                         (f.group(3) or "NO ACTION").upper()))
    for a in alterata.finditer(testo):
        trovate.add((a.group(1), a.group(2), a.group(3),
                     (a.group(4) or "NO ACTION").upper()))
    return frozenset(trovate)


def _fk_reale(figlia, colonna):
    """(genitore, azione) letti dalle migration, o (None, motivo)."""
    trovate = [f for f in _fk_delle_migrazioni()
               if f[0] == figlia and f[1] == colonna]
    if not trovate:
        colonne = sorted(f[1] for f in _fk_delle_migrazioni() if f[0] == figlia)
        if not colonne:
            return None, f"{figlia} non ha chiavi esterne in nessuna migration"
        return None, f"{figlia}.{colonna} non e' una FK: le FK di {figlia} sono {colonne}"
    # PostgreSQL senza ON DELETE esplicito applica NO ACTION, che blocca come
    # RESTRICT ma solo a fine istruzione: sono azioni diverse e si dichiarano
    # con nomi diversi.
    return trovate[0][2], trovate[0][3]


#: OGNI chiave esterna NON-CASCADE che punta a una tabella da cui il cleanup
#: cancella righe. Non e' l'elenco di quelle che lo script dichiara: e'
#: l'elenco di quelle che lo SCHEMA ha, riletto e rivisto. Serve a far
#: fallire la suite quando una migration ne aggiunge una, perche' una FK
#: nuova verso una di quelle tabelle o blocca una cancellazione (RESTRICT) o
#: modifica in silenzio una riga che sopravvive (SET NULL), e in entrambi i
#: casi va esaminata prima del prossimo run live - non dopo.
#:
#: Le CASCADE non sono elencate: se ne vanno con il genitore, e il loro
#: numero e' comunque confrontato perche' passare da CASCADE a SET NULL e'
#: proprio il cambiamento che questa prova deve intercettare.
FK_NON_CASCADE_ATTESE = frozenset({
    # LE OTTO FIGLIE DI `leads`, esaminate quando la fixture
    # NEXT_BEST_ACTION ha cominciato a creare un lead. Sono tutte SET NULL:
    # non impediscono la cancellazione, azzerano una colonna. Sulle righe del
    # run e' cio' che si vuole - il lead sparisce con la sua agenzia dedicata
    # e le sue tracce restano coerenti; su una riga altrui sarebbe un campo
    # svuotato in silenzio, ed e' il motivo per cui il preflight le interroga
    # tutte prima di cancellare.
    ("activities", "lead_id", "leads", "SET NULL"),
    ("buy_requests", "lead_id", "leads", "SET NULL"),
    ("followup_actions", "lead_id", "leads", "SET NULL"),
    ("next_best_actions", "lead_id", "leads", "SET NULL"),
    ("property_visits", "lead_id", "leads", "SET NULL"),
    ("seller_revival_suppressions", "lead_id", "leads", "SET NULL"),
    ("seller_timeline_events", "lead_id", "leads", "SET NULL"),
    ("tasks", "lead_id", "leads", "SET NULL"),
    ("activities", "contact_id", "contacts", "SET NULL"),
    ("agency_memberships", "agency_id", "agencies", "RESTRICT"),
    ("buy_request_history", "match_id", "matches", "SET NULL"),
    ("buy_request_history", "property_id", "properties", "SET NULL"),
    ("buy_request_history", "task_id", "tasks", "SET NULL"),
    ("buy_request_interactions", "match_id", "matches", "SET NULL"),
    ("buy_request_interactions", "property_id", "properties", "SET NULL"),
    ("buy_requests", "agency_id", "agencies", "RESTRICT"),
    ("buy_requests", "contact_id", "contacts", "RESTRICT"),
    ("flow_executions", "event_id", "flow_events", "SET NULL"),
    ("flow_executions", "retry_of_execution_id", "flow_executions", "SET NULL"),
    ("followup_actions", "agency_id", "agencies", "RESTRICT"),
    ("followup_actions", "contact_id", "contacts", "SET NULL"),
    ("followup_actions", "stima_id", "stime", "SET NULL"),
    ("followup_actions", "task_id", "tasks", "SET NULL"),
    ("invisible_sale_candidates", "buy_request_id", "buy_requests", "RESTRICT"),
    ("invisible_sale_opportunities", "watch_id", "property_watches", "RESTRICT"),
    ("leads", "contact_id", "contacts", "RESTRICT"),
    # Verso `match_runs`, che il cleanup cancella per predicato. Comparse
    # quando `match_runs` e' entrata fra i genitori dichiarati: prima nessuno
    # aveva mai guardato chi la referenziasse, e sono tre campi che un SET
    # NULL azzererebbe su righe potenzialmente non nostre.
    ("match_refresh_history", "new_run_id", "match_runs", "SET NULL"),
    ("match_refresh_history", "previous_run_id", "match_runs", "SET NULL"),
    ("matches", "latest_run_id", "match_runs", "SET NULL"),
    ("next_best_actions", "agency_id", "agencies", "RESTRICT"),
    ("next_best_actions", "contact_id", "contacts", "SET NULL"),
    ("next_best_actions", "stima_id", "stime", "SET NULL"),
    ("owner_accounts", "contact_id", "contacts", "RESTRICT"),
    ("owner_audit_log", "owner_account_id", "owner_accounts", "SET NULL"),
    ("owner_audit_log", "property_id", "properties", "SET NULL"),
    ("owner_shared_documents", "property_document_id", "property_documents", "RESTRICT"),
    ("owner_shared_documents", "superseded_by_shared_document_id",
     "owner_shared_documents", "RESTRICT"),
    ("owner_shared_documents", "supersedes_shared_document_id",
     "owner_shared_documents", "RESTRICT"),
    ("properties", "agency_id", "agencies", "RESTRICT"),
    ("property_contacts", "contact_id", "contacts", "RESTRICT"),
    ("property_proposals", "match_id", "matches", "RESTRICT"),
    ("property_sale_sellers", "contact_id", "contacts", "RESTRICT"),
    ("property_sales", "buy_request_id", "buy_requests", "RESTRICT"),
    ("property_sales", "property_id", "properties", "RESTRICT"),
    ("property_sales", "proposal_id", "property_proposals", "RESTRICT"),
    ("property_visits", "contact_id", "contacts", "SET NULL"),
    ("property_watch_observations", "watch_id", "property_watches", "RESTRICT"),
    ("property_watches", "agency_id", "agencies", "RESTRICT"),
    ("property_watches", "stima_id", "stime", "SET NULL"),
    ("seller_timeline_events", "agency_id", "agencies", "RESTRICT"),
    ("seller_timeline_events", "contact_id", "contacts", "SET NULL"),
    ("seller_timeline_events", "property_id", "properties", "SET NULL"),
    ("seller_timeline_events", "stima_id", "stime", "SET NULL"),
    ("stime", "agency_id", "agencies", "RESTRICT"),
    ("stime_dettagliate", "agency_id", "agencies", "RESTRICT"),
    ("tasks", "contact_id", "contacts", "SET NULL"),
})


@pytest.mark.parametrize("fk", list(cert.Certification.CHILD_FOREIGN_KEYS)
                         + list(cert.Certification.EFFECT_FOREIGN_KEYS),
                         ids=lambda f: f"{f.table}.{f.column}")
def test_86f_every_declared_foreign_key_matches_the_schema(fk):
    """Colonna, genitore E AZIONE, letti dalle migration.

    Due difetti diversi, e nessun doppio poteva vedere l'uno o l'altro:

    * la colonna. La versione precedente interrogava
      `property_watch_observations.property_watch_id`, che non esiste - si
      chiama `watch_id`. Sul TEST quella query non avrebbe risposto "0
      figlie": avrebbe sollevato UndefinedColumn, e il ramo di cattura
      l'avrebbe tradotta in "verifica non eseguibile", cioe' in un guasto
      attribuito al database invece che alla query.
    * l'azione. Le stesse righe erano chiamate "figli CASCADE" mentre due su
      tre sono RESTRICT: un CASCADE se ne va con il genitore, un RESTRICT
      impedisce al genitore di andarsene. Un report che le confonde descrive
      un comportamento che quelle FK non hanno.
    """
    genitore, azione = _fk_reale(fk.table, fk.column)
    assert genitore is not None, azione
    assert genitore == fk.parent, (
        f"{fk.table}.{fk.column} punta a {genitore}, non a {fk.parent}")
    assert azione == fk.on_delete, (
        f"{fk.table}.{fk.column} e' ON DELETE {azione}, dichiarata {fk.on_delete}")


def test_86g_the_report_never_calls_a_restrict_relation_a_cascade(monkeypatch):
    """Nessuna riga di report chiama CASCADE una relazione che non lo e'.

    Il vincolo e' sul testo che l'operatore legge: e' li' che una RESTRICT
    travestita da CASCADE diventa una diagnosi sbagliata - "il CASCADE non e'
    avvenuto" manda a cercare un guasto, quando il fatto e' che quella riga
    impedisce la cancellazione e va esaminata.
    """
    _code, report, _db, _probe, _ = _dedicato(
        monkeypatch, figli_cascata={"seller_revival_suppressions": (2, 0),
                                    "property_watch_observations": (0, 0)})
    azione_di = {fk.table: fk.on_delete for fk in cert.Certification.CHILD_FOREIGN_KEYS}
    visti = set()
    # Segmento per segmento: una riga di report ne concatena molti, e cercare
    # la parola nell'intera riga confonderebbe la figlia CASCADE accanto.
    segmenti = [s for _k, _i, testo in report.rows for s in testo.split(";")]
    for segmento in segmenti:
        for tabella, azione in azione_di.items():
            if tabella not in segmento:
                continue
            visti.add(tabella)
            if azione != "CASCADE":
                assert "CASCADE" not in segmento, (tabella, segmento)
            assert f"{tabella} ({azione})" in segmento or "nessun" in segmento, (
                tabella, segmento)
    assert visti == set(azione_di), (visti, set(azione_di))


def test_86i_no_relevant_foreign_key_is_unaccounted_for():
    """COMPLETEZZA, non solo correttezza.

    I test qui sopra verificano che le FK DICHIARATE dallo script siano vere.
    Non dicono nulla sulle FK che lo script non ha dichiarato - ed e' proprio
    una di quelle a essersi fatta scoprire dal vivo: nessuno aveva guardato
    che `property_watch_observations` fosse RESTRICT finche' non ha rischiato
    di far cadere la transazione di cleanup.

    Qui si parte dallo schema, non dallo script: ogni FK non-CASCADE che punta
    a una tabella da cui il cleanup cancella righe deve comparire
    nell'inventario rivisto. Una migration che ne aggiunge una fa fallire
    questa prova finche' qualcuno non l'ha esaminata.

    NON e' una prova sullo schema vivo: legge le migration del repository. Le
    dipendenze che comparissero altrove le trova la guardia a run time, che
    interroga `pg_constraint` - e che il test seguente verifica giri su tutto
    il perimetro.
    """
    genitori = set(cert.Certification.CLEANUP_PARENTS)
    osservate = {f for f in _fk_delle_migrazioni()
                 if f[2] in genitori and f[3] != "CASCADE"}
    comparse = osservate - FK_NON_CASCADE_ATTESE
    sparite = FK_NON_CASCADE_ATTESE - osservate
    assert not comparse, (
        "chiavi esterne non-CASCADE nuove verso una tabella che il cleanup "
        f"cancella, mai esaminate: {sorted(comparse)}")
    assert not sparite, (
        f"chiavi esterne sparite dallo schema: {sorted(sparite)}. "
        "Se la migration e' voluta, va aggiornato l'inventario.")


def _tabelle_cancellate():
    """Cio' che il cleanup cancella, DEDOTTO dalle strutture dello script.

    Non un elenco scritto a mano: un elenco a mano si aggiorna quando qualcuno
    si ricorda, e la lacuna che ha fatto passare `tasks` e `flow_executions`
    era esattamente questa - erano cancellate per `agency_id` e non comparivano
    in nessuna struttura che qualcuno stesse guardando.
    """
    C = cert.Certification
    tabelle = {d.table for d in cert.DOMAINS if d.table}
    tabelle |= {t for t, _colonne in C.EFFECT_TABLES}
    tabelle |= set(C.EFFECT_BY_ID_TABLES)
    tabelle |= set(C.DEDICATED_EFFECT_TABLES)
    tabelle |= {t for _attributo, t in C.TRACKED_ID_TABLES}
    tabelle |= {t for t, _colonna in C.DEDICATED_TABLES}
    tabelle |= {"agencies", "operator_users"}
    return tabelle


def _parent_da_sorvegliare():
    """Cio' che si cancella E ha almeno una FK non-CASCADE entrante.

    L'intersezione e' il minimo indiscutibile: una FK CASCADE porta via la
    figlia con il genitore, una RESTRICT fa cadere la transazione, una SET
    NULL azzera in silenzio un campo di una riga che sopravvive. Le ultime due
    su una riga non nostra sono un danno, e nessuna delle due si vede se la
    guardia non ha mai interrogato quel genitore.
    """
    con_fk = {fk[2] for fk in _fk_delle_migrazioni() if fk[3] != "CASCADE"}
    return _tabelle_cancellate() & con_fk


def _run_tracciato(database, report):
    """Un run che ha creato qualcosa in OGNI categoria distruttiva."""
    c = cert.Certification(database, report)
    c.created_rows = {
        "contacts": [(11, "M", "display_name")],
        "properties": [(21, "M", "title")],
        "buy_requests": [(31, "M", "title")],
        "seller_timeline_events": [(41, "M", "description")],
    }
    c.created_effects = {"property_documents": [51], "owner_shared_documents": [61],
                         "flow_events": [71], "stime": [81]}
    c.created_match_ids = [901]
    c.created_owner_account_ids = [801]
    c.created_sale_ids = [701]
    c.created_proposal_ids = [601]
    c.created_agency_ids = [501]
    c.created_user_ids = [401]
    # Cio' che nelle agenzie dedicate si cancella per agency_id: senza
    # istantanea non avrebbe id, e resterebbe fuori dal perimetro.
    c.child_parents = {"property_watches": (301,), "contacts": (12,),
                       "tasks": (201,), "flow_executions": (101,),
                       "flow_events": (71,), "stime": (81,),
                       # Il lead della fixture NEXT_BEST_ACTION: si cancella
                       # per agency_id, quindi senza istantanea le sue otto
                       # figlie SET NULL non verrebbero mai interrogate.
                       "leads": (151,)}
    c.effect_rows_before = {"match_runs": (1001,), "owner_audit_log": (1101,)}
    return c


def test_86j_the_guard_walks_every_parent_that_needs_watching():
    """L'insieme da sorvegliare e' DEDOTTO, non elencato.

    Si interseca cio' che il cleanup cancella - ricavato dalle strutture dello
    script - con i genitori che hanno una FK non-CASCADE entrante, ricavati
    dalle migration. Ogni tabella di quell'intersezione deve essere passata a
    `pg_constraint`.

    Un elenco scritto a mano avrebbe certificato la propria stessa lacuna:
    `tasks` e `flow_executions` si cancellano per `agency_id` nelle agenzie
    dedicate, non comparivano in nessuna struttura tracciata per id, e hanno
    entrambe FK non-CASCADE entranti - sparivano senza che nessuno avesse
    chiesto chi le referenziasse.
    """
    database = fake_database(agencies=AGENCIES)
    report, _stream = quiet_report()
    c = _run_tracciato(database, report)

    assert c._foreign_dependencies() == []
    # La tabella sta nel PARAMETRO, non nel testo della query: e' la stessa
    # query per tutte, con `('public.' || %s)::regclass`.
    interrogate = {p[0] for q, p in database.state["interrogazioni"]
                   if "pg_constraint" in q and p}
    attesi = _parent_da_sorvegliare()
    assert {"tasks", "flow_executions", "match_runs"} <= attesi, sorted(attesi)
    mancanti = attesi - interrogate
    assert not mancanti, f"la guardia non ha mai guardato: {sorted(mancanti)}"
    # E nessuna tabella e' interrogata senza essere dichiarata: la prova di
    # completezza sullo schema copre solo CLEANUP_PARENTS.
    assert interrogate <= set(cert.Certification.CLEANUP_PARENTS), \
        sorted(interrogate - set(cert.Certification.CLEANUP_PARENTS))


def test_86t_the_snapshot_really_asks_for_every_dedicated_table():
    """L'istantanea INTERROGA davvero ogni tabella dichiarata.

    Il test precedente costruisce `child_parents` a mano: verifica cosa fa la
    guardia con gli id, non che qualcuno sia andato a prenderli. Qui si guida
    la fotografia vera e si controlla la query, tabella per tabella - perche'
    e' li' che `tasks` e `flow_executions` mancavano.
    """
    database = fake_database(agencies=AGENCIES, stime={})
    report, _stream = quiet_report()
    c = cert.Certification(database, report)
    c.created_agency_ids = [501]

    assert c._snapshot_child_parents() is None, report.rows
    interrogate = {q.split(" FROM ")[1].split(" ")[0]
                   for q in database.state["sql"]
                   if q.startswith("SELECT id FROM ") and "agency_id IN" in q}
    attese = ({fk.parent for fk in cert.Certification.CHILD_FOREIGN_KEYS}
              | set(cert.Certification.DEDICATED_SNAPSHOT_TABLES))
    assert attese <= interrogate, sorted(attese - interrogate)
    assert {"tasks", "flow_executions"} <= interrogate, sorted(interrogate)


def test_86p_every_table_the_cleanup_deletes_is_a_declared_parent():
    """E l'insieme dedotto sta tutto dentro `CLEANUP_PARENTS`.

    E' il legame fra le due prove: la completezza sullo schema interroga solo
    le tabelle dichiarate, quindi una tabella cancellata e non dichiarata
    sarebbe una tabella le cui FK nessuno ha mai letto.
    """
    fuori = _tabelle_cancellate() - set(cert.Certification.CLEANUP_PARENTS)
    assert not fuori, f"cancellate ma non dichiarate: {sorted(fuori)}"


def test_86q_the_dedicated_snapshot_covers_every_table_that_needs_it():
    """Ogni `DEDICATED_TABLES` con FK non-CASCADE entranti e' fotografata.

    Li' si cancella per `agency_id`: senza istantanea quelle righe non hanno
    un id, e senza id non entrano nel perimetro della guardia. Il criterio e'
    derivato dallo schema, cosi' che una migration che aggiunga una FK a una
    di quelle tabelle faccia fallire questa prova.
    """
    C = cert.Certification
    dedicate = {t for t, _c in C.DEDICATED_TABLES}
    con_fk = {fk[2] for fk in _fk_delle_migrazioni() if fk[3] != "CASCADE"}
    servono = dedicate & con_fk
    assert {"tasks", "flow_executions"} <= servono, sorted(servono)
    mancanti = servono - set(C.DEDICATED_SNAPSHOT_TABLES)
    assert not mancanti, f"cancellate per agency_id e mai fotografate: {sorted(mancanti)}"


def test_86m_the_guard_runs_before_the_first_delete(monkeypatch):
    """Il preflight precede la prima DELETE, e una dipendenza estranea le
    ferma tutte.

    `cleanup_chain_fixtures` e `cleanup_owner_fixtures` arrivano prima di
    `cleanup_orphan_fixtures`: vendite, proposte, match e conti proprietario
    erano gia' spariti quando la guardia veniva eseguita per la prima volta.
    Su quelle tabelle non arrivava mai in tempo.
    """
    _code, report, database, probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]},
        fk_perimetro={"property_sales": [("public.property_sale_sellers", "sale_id")]},
        dipendenti_estranee=1)
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-PREFLIGHT" in fallimenti, [r[1] for r in report.rows]
    assert "PRIMA di cancellare" in fallimenti["CLEAN-PREFLIGHT"]
    assert "property_sale_sellers" in fallimenti["CLEAN-PREFLIGHT"], \
        "la dipendenza e' stata trovata su una tabella che la guardia vecchia " \
        "non avrebbe mai interrogato"
    # Nessuna cancellazione sui dati, ne' via SQL ne' via API.
    dati = ("contacts", "properties", "buy_requests", "seller_timeline_events",
            "property_documents", "owner_shared_documents", "matches",
            "property_sales", "property_proposals", "owner_accounts",
            "owner_audit_log", "property_status_history", "buy_request_history",
            "property_contacts", "match_runs", "agencies", "stime", "flow_events")
    eseguite = database.state.get("deletes", [])
    assert not [q for q in eseguite if any(f"FROM {t} " in q + " " for t in dati)], \
        eseguite
    assert not [s for s, _st, _b in probe.exchanges if s.startswith("DELETE")], \
        [s for s, _st, _b in probe.exchanges if s.startswith("DELETE")]

    # Le IDENTITA' invece se ne vanno, ed e' voluto: i soli figli di
    # `operator_users` sono `agency_memberships` e `operator_sessions`, in
    # CASCADE e create da questo run - nessuna riga altrui puo' risentirne, e
    # lasciare vive delle credenziali sul TEST sarebbe il danno peggiore fra i
    # due. Se un giorno comparisse un terzo figlio, la prova di completezza
    # sulle FK lo segnalerebbe prima di qui.
    assert any("FROM operator_users" in q for q in eseguite), eseguite


def test_86o_a_block_also_stops_the_deletions_made_through_the_api():
    """Il blocco vale anche per le DELETE via HTTP.

    Dall'altra parte di quelle chiamate c'e' comunque un handler che scrive:
    archivia un immobile, chiude una proposta, tocca lo storico. Se una
    dipendenza estranea e' comparsa, quelle scritture sono lo stesso danno
    delle DELETE dirette - solo con un intermediario.

    Si esercita direttamente, perche' nello scenario completo la matrice
    registra poche fixture cancellabili via API e la guardia non verrebbe mai
    raggiunta: una prova che non arriva al punto e' una prova che non c'e'.
    """
    class HttpSpia:
        def __init__(self):
            self.chiamate = []

        def request(self, method, path, jar=None, payload=None):
            self.chiamate.append((method, path))
            return cert.Response(200, {}, b"{}")

    database = fake_database(agencies=AGENCIES)
    report, _stream = quiet_report()
    c = cert.Certification(database, report)
    c.fixtures = [("A", "DELETE", "/api/match/matches/9")]
    spia = HttpSpia()

    c.blocking_reason = "una dipendenza estranea"
    c.cleanup_http_fixtures(spia, {"A": object()})
    assert spia.chiamate == [], spia.chiamate
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-FIXTURE" in fallimenti, report.rows
    assert "una dipendenza estranea" in fallimenti["CLEAN-FIXTURE"]

    # E senza blocco la stessa chiamata parte: la guardia non e' un rifiuto
    # permanente travestito.
    c.blocking_reason = None
    c.cleanup_http_fixtures(spia, {"A": object()})
    assert spia.chiamate == [("DELETE", "/api/match/matches/9")], spia.chiamate


@pytest.mark.parametrize("rotta,atteso", [
    ("SELECT id FROM property_watches", "genitori non fotografabili"),
    ("SELECT t.id FROM owner_audit_log", "righe degli effetti non fotografabili"),
])
def test_86r_an_incomplete_snapshot_stops_every_data_deletion(monkeypatch, rotta, atteso):
    """FAIL-CLOSED: una fotografia incompleta ferma tutto.

    Non e' prudenza generica. Dopo le DELETE le righe che non sono state
    fotografate non sono piu' identificabili: il genitore non c'e', la
    colonna SET NULL e' azzerata, e la verifica finale risponderebbe "0
    presenti" a una domanda che non ha potuto fare. Cancellare dopo aver
    segnalato significherebbe distruggere proprio cio' che non si sa piu'
    controllare - e il report direbbe che e' andato tutto bene.
    """
    _code, report, database, probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        explode_on=rotta)
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-ISTANTANEA" in fallimenti, [r[1] for r in report.rows]
    assert atteso in fallimenti["CLEAN-ISTANTANEA"], fallimenti["CLEAN-ISTANTANEA"]
    assert "Nessuna cancellazione" in fallimenti["CLEAN-ISTANTANEA"]

    # Il preflight riconosce il blocco e NON dichiara il perimetro pulito.
    assert "CLEAN-PREFLIGHT" in fallimenti, [r[1] for r in report.rows]
    assert "controllo non eseguito" in fallimenti["CLEAN-PREFLIGHT"]
    assert not [t for k, i, t in report.rows
                if i == "CLEAN-PREFLIGHT" and k == cert.PASS], report.rows

    # Zero cancellazioni sui dati, SQL o HTTP.
    dati = ("contacts", "properties", "buy_requests", "seller_timeline_events",
            "property_documents", "owner_shared_documents", "matches",
            "property_sales", "property_proposals", "owner_accounts",
            "owner_audit_log", "property_status_history", "buy_request_history",
            "property_contacts", "match_runs", "agencies", "stime", "flow_events",
            "tasks", "flow_executions", "next_best_actions", "followup_actions")
    eseguite = database.state.get("deletes", [])
    assert not [q for q in eseguite if any(f"FROM {t} " in q + " " for t in dati)], \
        eseguite
    assert not [s for s, _st, _b in probe.exchanges if s.startswith("DELETE")], \
        [s for s, _st, _b in probe.exchanges if s.startswith("DELETE")]


def test_86s_a_healthy_snapshot_does_not_block_anything(monkeypatch):
    """E senza guasto le cancellazioni partono: la guardia non e' un rifiuto
    permanente travestito. Senza questo caso, un blocco sempre attivo
    supererebbe la prova qui sopra."""
    _code, report, database, _probe, _ = _dedicato(monkeypatch)
    assert not any(i == "CLEAN-ISTANTANEA" for _k, i, _t in report.rows), report.rows
    assert any("FROM agencies" in q for q in database.state.get("deletes", [])), \
        database.state.get("deletes")


@pytest.mark.parametrize("guasto,chiavi_attese", [
    ({"explode_on": "SELECT t.id FROM owner_audit_log"}, 0),
    ({"fk_perimetro": {"properties": [("public.property_visits", "property_id")]},
      "dipendenti_estranee": 1}, 0),
    ({}, 2),
])
def test_86u_a_block_stops_the_bucket_too(monkeypatch, guasto, chiavi_attese):
    """Sotto blocco non si tocca nemmeno lo storage.

    `delete_object` e' l'unica cancellazione che non passa dal database, ed e'
    la sola davvero irreversibile: una riga rimossa per errore si ritrova in
    un dump, un oggetto no.

    Il blocco esiste per CONSERVARE le righe - la `storage_key` che localizza
    il file, gli id che l'istantanea non ha potuto prendere. Svuotare il
    bucket mentre si conservano quelle righe sarebbe l'immagine speculare del
    difetto per cui il blocco e' stato scritto: resterebbero i puntatori e
    sparirebbe cio' a cui puntano.

    Il terzo caso e' senza guasto, e serve a rendere non vacui i primi due:
    senza, un `cleanup_storage_objects` che non cancellasse mai niente li
    supererebbe entrambi.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]}, **guasto)

    assert len(database.state.get("chiavi_cancellate", [])) == chiavi_attese, \
        database.state.get("chiavi_cancellate")
    if chiavi_attese:
        return

    # Sotto blocco: il FAIL lo dice, e le righe che localizzano i file restano.
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-STORAGE" in fallimenti, [r[1] for r in report.rows]
    assert "nessun oggetto rimosso dal bucket" in fallimenti["CLEAN-STORAGE"]
    eseguite = database.state.get("deletes", [])
    assert not [q for q in eseguite
                if "property_documents" in q or "owner_shared_documents" in q], eseguite
    # E le chiavi non sono state nemmeno LETTE: il blocco precede ogni cosa.
    assert not [q for q in database.state["sql"]
                if q.startswith("SELECT id, storage_key")], database.state["sql"]


def test_86n_the_preflight_precedes_every_cleanup_in_the_finally():
    """E l'ordine e' nel codice: il preflight prima di ogni `cleanup_*`."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    run = next(n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "run")
    finale = next(t for t in ast.walk(run) if isinstance(t, ast.Try) and t.finalbody)
    chiamate = [n.func.attr for n in ast.walk(
        ast.Module(body=finale.finalbody, type_ignores=[]))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    preflight = chiamate.index("preflight_dependencies")
    puliture = [i for i, nome in enumerate(chiamate) if nome.startswith("cleanup_")]
    assert puliture and preflight < min(puliture), chiamate


def test_86k_a_perimeter_table_outside_the_declaration_stops_the_cleanup():
    """Una tabella cancellata ma non dichiarata ferma tutto.

    E' il legame fra le due prove: la completezza sullo schema copre solo le
    tabelle di `CLEANUP_PARENTS`, quindi cancellare altrove significherebbe
    cancellare dove nessuno ha guardato le chiavi esterne.
    """
    database = fake_database(agencies=AGENCIES)
    report, _stream = quiet_report()
    c = cert.Certification(database, report)
    c.created_rows = {"contacts": [(11, "M", "display_name")]}
    c.created_effects = {"una_tabella_non_dichiarata": [7]}
    c.cleanup_orphan_fixtures({"A": {"id": 1}})
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-ORFANE" in fallimenti, report.rows
    assert "non dichiarata in CLEANUP_PARENTS" in fallimenti["CLEAN-ORFANE"]
    assert "una_tabella_non_dichiarata" in fallimenti["CLEAN-ORFANE"]
    assert not database.state.get("deletes"), database.state.get("deletes")


def test_86l_every_on_delete_claim_in_the_source_is_true():
    """Anche i COMMENTI dicono qualcosa di verificabile.

    Il commento su `DEDICATED_TABLES` affermava che
    `property_watch_observations` fosse una figlia CASCADE e se ne andasse con
    il watch. E' RESTRICT: non se ne va, impedisce al watch di andarsene. Una
    frase del genere non rompe niente finche' qualcuno non la legge per
    decidere, ed e' esattamente cosi' che si prendono le decisioni sbagliate
    in un cleanup.

    Nessuna prova di comportamento puo' cogliere un commento falso: si legge
    quello che il sorgente AFFERMA e lo si confronta con lo schema.
    """
    sorgente = SCRIPT.read_text(encoding="utf-8")
    dichiarazione = re.compile(
        r"`?([a-z_]+)\.([a-z_]+)`?(?:\s+(?:e'|sono))?\s+ON DELETE\s+"
        r"(CASCADE|RESTRICT|SET NULL|NO ACTION)")
    affermazioni = {(m.group(1), m.group(2), m.group(3))
                    for m in dichiarazione.finditer(sorgente)}
    assert affermazioni, "nessuna affermazione sulle FK nel sorgente: la prova e' vuota"
    for figlia, colonna, azione in sorted(affermazioni):
        _genitore, vera = _fk_reale(figlia, colonna)
        assert vera == azione, (
            f"il sorgente afferma {figlia}.{colonna} ON DELETE {azione}, "
            f"lo schema dice {vera}")


def test_86h_a_set_null_relation_is_declared_unprovable_not_clean(monkeypatch):
    """Per un SET NULL, "0 per id del genitore" non e' "rimossa".

    `owner_audit_log.property_id` e' ON DELETE SET NULL: cancellato
    l'immobile, la colonna e' NULL e la riga non risponde piu' al suo id. Il
    conteggio dopo la cancellazione e' quindi 0 per costruzione - come una
    JOIN a una riga cancellata - e presentarlo accanto ai CASCADE lo
    farebbe leggere come una prova di pulizia. La riga se ne va per il
    predicato di appartenenza, in `cleanup_orphan_fixtures`; questo conteggio
    puo' solo dimostrare il contrario, cioe' che il genitore c'e' ancora.
    """
    _code, report, _db, _probe, _ = _dedicato(monkeypatch)
    riga = next(t for _k, i, t in report.rows if i == "CLEAN-FIGLIE")
    set_null = [fk for fk in cert.Certification.EFFECT_FOREIGN_KEYS
                if fk.on_delete == "SET NULL"]
    assert set_null, "nessuna FK SET NULL dichiarata: il caso non e' esercitato"
    for fk in set_null:
        assert f"{fk.table}.{fk.column} (SET NULL)" in riga, riga
    assert "non prova la rimozione" in riga, riga


# ---------------------------------------------------------------------------
# 87-89 - l'ordine delle cancellazioni
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("guasto", ["storage_assente", "storage_rotto"])
def test_87_a_failed_bucket_cleanup_stops_the_destructive_db_cleanup(monkeypatch, guasto):
    """Se un oggetto resta nel bucket, le righe che lo localizzano NON si
    cancellano.

    La chiave sta in `property_documents.storage_key` e da nessun'altra parte:
    l'API non la restituisce. Cancellare quelle righe subito dopo aver
    stampato "la chiave e' li'" renderebbe falsa quella frase nell'istante
    stesso in cui viene letta, e l'oggetto resterebbe nel bucket senza che
    nessuno possa piu' dire a cosa apparteneva.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]}, **{guasto: True})
    eseguite = database.state.get("deletes", [])
    assert not any("property_documents" in q for q in eseguite), eseguite
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-STORAGE" in fallimenti
    assert "SOSPESO" in fallimenti["CLEAN-STORAGE"], fallimenti["CLEAN-STORAGE"]
    assert "CLEAN-ORFANE" in fallimenti, list(fallimenti)
    assert "nessuna cancellazione eseguita" in fallimenti["CLEAN-ORFANE"]


def test_87b_the_block_also_holds_the_dedicated_agencies(monkeypatch):
    """Il blocco vale per ogni cancellazione, non solo per quella che
    tocca i documenti: una sola decisione, in un solo posto, cosi' che non
    resti una seconda strada per arrivare alle stesse righe."""
    _code, report, database, _probe, _ = _dedicato(monkeypatch, storage_rotto=True)
    eseguite = database.state.get("deletes", [])
    assert not any("FROM agencies" in q for q in eseguite), eseguite
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-DEDICATA" in fallimenti
    assert "NON rimosse" in fallimenti["CLEAN-DEDICATA"]
    # Le identita' invece se ne vanno: nessuna credenziale resta viva per
    # colpa di un file rimasto in un bucket.
    assert any("FROM operator_users" in q for q in eseguite), eseguite


def test_87c_with_a_clean_bucket_the_rows_are_removed_as_usual(monkeypatch):
    """La guardia non e' un blocco permanente travestito: senza guasto le
    stesse cancellazioni partono. Senza questo caso, una guardia sempre vera
    supererebbe i due test qui sopra."""
    _code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    eseguite = database.state.get("deletes", [])
    assert any("property_documents" in q for q in eseguite), eseguite
    assert not any(i in ("CLEAN-ORFANE", "CLEAN-STORAGE")
                   for k, i, _t in report.rows if k == cert.FAIL)


def test_88_effects_living_in_the_dedicated_agencies_are_really_counted(monkeypatch):
    """`flow_events` e `stime` esistono davvero nel doppio, e spariscono solo
    se qualcuno li cancella.

    Finche' il doppio rispondeva 0 a ogni conteggio, questa verifica era
    soddisfatta da qualunque ordine di cancellazione - compreso quello che sul
    TEST avrebbe prodotto un CLEAN-VERIFICA FAIL su righe ancora legittimamente
    presenti. Qui si tolgono le due tabelle dall'elenco delle cancellazioni
    dedicate: se il doppio le materializza, restano, e la verifica lo dice.
    """
    ridotto = tuple((t, c) for t, c in cert.Certification.DEDICATED_TABLES
                    if t not in cert.Certification.DEDICATED_EFFECT_TABLES)
    monkeypatch.setattr(cert.Certification, "DEDICATED_TABLES", ridotto)
    _code, report, _db, _probe, _ = _dedicato(monkeypatch)
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-VERIFICA" in fallimenti, [r[1] for r in report.rows]
    for tabella in cert.Certification.DEDICATED_EFFECT_TABLES:
        assert tabella in fallimenti["CLEAN-VERIFICA"], (tabella, fallimenti["CLEAN-VERIFICA"])


def test_88b_verifying_before_the_dedicated_cleanup_is_refused(monkeypatch):
    """Chiamare la verifica troppo presto non produce un residuo: produce un
    FAIL che dice che il difetto e' nell'ordine.

    Senza questa guardia il report avrebbe nominato `flow_events` e `stime`
    come righe "ancora presenti" - vere solo per qualche riga di codice
    ancora - e la diagnosi sarebbe partita dal TEST invece che da qui.
    """
    def non_invocata(self):
        # Il metodo non viene MAI eseguito: e' il `finally` scritto al
        # contrario, senza doverlo riscrivere. La bandiera non viene toccata
        # da qui, cosi' che il test osservi il suo valore iniziale invece di
        # imporglielo - un test che la azzerasse da se' passerebbe anche con
        # una bandiera nata vera.
        return None

    monkeypatch.setattr(cert.Certification, "cleanup_dedicated_agencies", non_invocata)
    _code, report, _db, _probe, _ = _dedicato(monkeypatch)
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-VERIFICA" in fallimenti
    assert "PRIMA di cleanup_dedicated_agencies" in fallimenti["CLEAN-VERIFICA"]


def test_89_the_finally_orders_snapshot_cleanup_and_verification(monkeypatch):
    """L'ordine e' nel codice, non nella memoria di chi l'ha scritto.

    Tre vincoli, e ciascuno e' un difetto gia' visto: l'istantanea prima di
    ogni cancellazione, il bucket prima delle righe che lo localizzano, le
    agenzie dedicate prima della verifica dei residui.
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    run = next(n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "run")
    finale = next(t for t in ast.walk(run) if isinstance(t, ast.Try) and t.finalbody)
    chiamate = [n.func.attr for n in ast.walk(ast.Module(body=finale.finalbody, type_ignores=[]))
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    posizione = {nome: chiamate.index(nome) for nome in set(chiamate)}
    for prima, dopo in (("snapshot_before_cleanup", "cleanup_chain_fixtures"),
                        ("snapshot_before_cleanup", "cleanup_orphan_fixtures"),
                        ("cleanup_storage_objects", "cleanup_orphan_fixtures"),
                        ("cleanup_orphan_fixtures", "cleanup_dedicated_agencies"),
                        ("cleanup_dedicated_agencies", "verify_no_residue")):
        assert posizione[prima] < posizione[dopo], (
            f"{prima} deve precedere {dopo}: {chiamate}")


# ---------------------------------------------------------------------------
# 90-92 - cio' che il run ha creato NON e' solo `created_rows`
# ---------------------------------------------------------------------------

def test_90_a_run_with_only_effects_is_cleaned_and_verified():
    """Nessuna fixture di dominio, due documenti: si cancella e si verifica.

    `created_rows` raccoglie solo le risorse dei domini della matrice. Un run
    in cui quei domini restano BLOCKED - una route che cambia forma, una
    sessione che non si apre - puo' avere creato lo stesso documenti,
    condivisioni, eventi e stime. Legare la pulizia a `created_rows`
    significava, in quel caso, stampare "nessuna riga da rimuovere" e
    "verificato" su righe che erano ancora la'.
    """
    database = fake_database(agencies=AGENCIES)
    report, _stream = quiet_report()
    certificazione = cert.Certification(database, report)
    certificazione.created_effects = {"owner_shared_documents": [61],
                                      "property_documents": [51]}

    certificazione.snapshot_before_cleanup()
    certificazione.cleanup_orphan_fixtures({})
    certificazione.verify_no_residue()

    cancellazioni = " ".join(database.state.get("deletes", []))
    assert "DELETE FROM property_documents WHERE id IN" in cancellazioni, cancellazioni
    assert "DELETE FROM owner_shared_documents WHERE id IN" in cancellazioni, cancellazioni
    esiti = {i: k for k, i, _t in report.rows}
    assert esiti["CLEAN-ORFANE"] == cert.PASS, report.rows
    assert esiti["CLEAN-VERIFICA"] == cert.PASS, report.rows
    testo = next(t for _k, i, t in report.rows if i == "CLEAN-VERIFICA")
    assert "effetti=2" in testo, testo


def test_90b_an_effects_only_run_that_leaves_a_row_fails():
    """E la verifica non e' compiacente: se il documento resta, lo dice. Senza
    questo caso, un `verify_no_residue` che non guardasse nulla passerebbe il
    test qui sopra."""
    database = fake_database(agencies=AGENCIES, effetti_vivi={"property_documents": 1})
    report, _stream = quiet_report()
    certificazione = cert.Certification(database, report)
    certificazione.created_effects = {"property_documents": [51]}
    certificazione.verify_no_residue()
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "property_documents=1" in fallimenti.get("CLEAN-VERIFICA", ""), report.rows


def test_91_the_tracked_ids_outside_created_rows_are_verified_too():
    """Vendite, proposte, match e conti proprietario hanno un cleanup che
    verifica se stesso. Questa e' l'ultima parola e non deve fidarsene: se
    quella verifica fosse sbagliata, nessun altro se ne accorgerebbe."""
    database = fake_database(agencies=AGENCIES,
                             effetti_vivi={"matches": 1, "owner_accounts": 2})
    report, _stream = quiet_report()
    certificazione = cert.Certification(database, report)
    certificazione.created_match_ids = [901]
    certificazione.created_owner_account_ids = [801, 802]
    certificazione.verify_no_residue()
    testo = next(t for k, i, t in report.rows if i == "CLEAN-VERIFICA" and k == cert.FAIL)
    assert "matches=1" in testo and "owner_accounts=2" in testo, testo


def test_93_an_upload_without_the_origin_recovers_it_from_the_database(monkeypatch):
    """201 senza `property_document_id`: l'origine si recupera dal database.

    La risposta dell'API e' un contratto piu' fragile dello schema. Il
    documento dell'immobile e' l'unica riga che porta `storage_key`: se il run
    non ne conosce l'id, l'oggetto caricato resta nel bucket e nessun
    censimento SQL lo vedra' mai - non e' sul database.

    `owner_shared_documents.property_document_id` e' NOT NULL: per una
    condivisione che esiste, l'origine esiste.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch,
        http=FakeHttp(broken={"upload_senza_origine"}, only="portal",
                      prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    righe = {i: (k, t) for k, i, t in report.rows}
    assert righe["CLEAN-STORAGE-ORIGINE"][0] == cert.PASS, report.rows
    assert "risolta dal database" in righe["CLEAN-STORAGE-ORIGINE"][1]
    # E gli oggetti sono stati davvero rimossi: due caricamenti, due chiavi.
    assert len(database.state.get("chiavi_cancellate", [])) == 2, \
        database.state.get("chiavi_cancellate")


def test_93b_an_unrecoverable_origin_stops_the_cleanup_instead_of_leaking(monkeypatch):
    """Ne' la risposta ne' il database danno l'origine: si blocca.

    E' il solo esito che non produce un orfano. Proseguire cancellerebbe
    `owner_shared_documents` e `property_documents` - l'unica traccia da cui
    un operatore potrebbe risalire alla chiave - lasciando nel bucket un file
    che nessuno sa piu' a cosa apparteneva.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch,
        http=FakeHttp(broken={"upload_senza_origine"}, only="portal",
                      prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]}, origine_perduta=True)
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-STORAGE-ORIGINE" in fallimenti, report.rows
    assert "non ha un property_documents associabile" in fallimenti["CLEAN-STORAGE-ORIGINE"]
    assert "SOSPESO" in fallimenti["CLEAN-STORAGE-ORIGINE"]
    # Nessuna riga cancellata: la traccia resta leggibile.
    eseguite = database.state.get("deletes", [])
    assert not any("property_documents" in q or "owner_shared_documents" in q
                   for q in eseguite), eseguite
    assert "CLEAN-ORFANE" in fallimenti, list(fallimenti)


def test_94_a_set_null_effect_row_is_verified_by_its_own_id(monkeypatch):
    """La riga che sopravvive a un SET NULL viene trovata lo stesso.

    `owner_audit_log.property_id` e' ON DELETE SET NULL: cancellato
    l'immobile, la colonna e' NULL e il conteggio per id del genitore
    restituisce 0 anche se la riga e' ancora la'. L'id della riga, invece,
    nessun ON DELETE lo tocca.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]},
        effetti_righe={"owner_audit_log": [561, 565]},
        delete_inefficace=("owner_audit_log",))
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-VERIFICA" in fallimenti, [r[1] for r in report.rows]
    assert "owner_audit_log=2 di 2 righe fotografate" in fallimenti["CLEAN-VERIFICA"], \
        fallimenti["CLEAN-VERIFICA"]


def test_94b_when_the_delete_works_the_id_check_confirms_it(monkeypatch):
    """E se la cancellazione funziona, le stesse due righe non ci sono piu':
    senza questo caso la verifica potrebbe essere sempre vera."""
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]},
        effetti_righe={"owner_audit_log": [561, 565]})
    righe = {i: (k, t) for k, i, t in report.rows}
    assert righe["CLEAN-VERIFICA"][0] == cert.PASS, [r for r in report.rows if "CLEAN" in r[1]]
    assert "verificata per id (2 righe fotografate prima)" in righe["CLEAN-FIGLIE"][1], \
        righe["CLEAN-FIGLIE"][1]


def test_94c_without_the_snapshot_the_verification_cannot_call_itself_done(monkeypatch):
    """Nessuna istantanea degli id: `CLEAN-VERIFICA` non puo' dire "0
    presenti, verificato" mentre `CLEAN-FIGLIE` ammette di non poter
    verificare una relazione SET NULL. Le due affermazioni non stanno
    insieme, e prima ci stavano."""
    def niente_istantanea(self):
        self._snapshot_child_parents()

    monkeypatch.setattr(cert.Certification, "snapshot_before_cleanup", niente_istantanea)
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-VERIFICA" in fallimenti, [r[1] for r in report.rows]
    assert "effetti non verificabili per id" in fallimenti["CLEAN-VERIFICA"]
    righe = {i: t for _k, i, t in report.rows}
    assert "NESSUNA istantanea degli id" in righe["CLEAN-FIGLIE"], righe["CLEAN-FIGLIE"]


def _valori_dei_parametri(stato, filtro=lambda _q: True):
    """Ogni valore scalare finito nei PARAMETRI delle query scelte.

    Le query sono tutte uguali nel testo - `WHERE id IN %s` - quindi cercare
    un id nel SQL non dimostra niente: l'id viaggia nei parametri, e li' va
    cercato. Le tuple annidate si appiattiscono, perche' psycopg riceve
    `(tuple_di_id,)`.
    """
    def piatti(valore):
        if isinstance(valore, (list, tuple, set, frozenset)):
            for singolo in valore:
                yield from piatti(singolo)
        elif isinstance(valore, dict):
            for singolo in valore.values():
                yield from piatti(singolo)
        else:
            yield valore

    trovati = set()
    for query, parametri in stato.get("interrogazioni", []):
        if parametri is not None and filtro(query):
            trovati.update(piatti(parametri))
    return trovati


#: Gli otto figli che il run 52f6d97b5214 creava senza raccoglierli, con la
#: colonna da cui pendono. Il perimetro non li conteneva, quindi la guardia li
#: vedeva referenziare le nostre righe e li dichiarava ESTRANEI: il cleanup si
#: fermava su righe che il run aveva creato lui stesso un passo prima.
FIGLI_DELLE_FIXTURE = {
    "property_sale_sellers": ("public.property_sales", "sale_id"),
    "followup_actions": ("public.contacts", "contact_id"),
    "owner_property_access": ("public.owner_accounts", "owner_account_id"),
    "owner_access_tokens": ("public.owner_accounts", "owner_account_id"),
    "owner_sessions": ("public.owner_accounts", "owner_account_id"),
    "agency_memberships": ("public.operator_users", "operator_user_id"),
    "operator_sessions": ("public.operator_users", "operator_user_id"),
    "match_requirement_results": ("public.match_runs", "match_run_id"),
}


def _fk_dei_figli():
    """{genitore: [(figlia, colonna)]} come lo vedrebbe il catalogo."""
    per_genitore = {}
    for figlia, (genitore, colonna) in FIGLI_DELLE_FIXTURE.items():
        per_genitore.setdefault(genitore.split(".")[-1], []).append(
            (f"public.{figlia}", colonna))
    return per_genitore


def test_96_the_children_of_the_fixtures_are_recognised_and_removed(monkeypatch):
    """IL CASO LIVE 52f6d97b5214: otto figli, tutti del run, tutti rimossi.

    Nessuno di loro aveva un id tracciato - nascono come effetto di una POST,
    non come risorsa chiesta - e nessuno compariva nel perimetro. La guardia
    faceva il suo mestiere e rifiutava: il difetto era che non sapeva
    riconoscerli.

    La risposta non e' esentarli dal controllo, che sarebbe cecita' proprio
    dove serve vedere: si raccolgono per APPARTENENZA, con lo stesso predicato
    di tutti gli altri effetti.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        fk_perimetro=_fk_dei_figli(), dipendenti_estranee=0)

    esiti = {i: k for k, i, _t in report.rows}
    assert esiti.get("CLEAN-PREFLIGHT") == cert.PASS, \
        [r for r in report.rows if r[1] == "CLEAN-PREFLIGHT"]
    assert esiti.get("CLEAN-ORFANE") == cert.PASS, \
        [r for r in report.rows if r[1] == "CLEAN-ORFANE"]
    assert esiti.get("CLEAN-VERIFICA") == cert.PASS, \
        [r for r in report.rows if r[1] == "CLEAN-VERIFICA"]

    cancellazioni = " ".join(database.state.get("deletes", [])).upper()
    for figlia in FIGLI_DELLE_FIXTURE:
        assert f"FROM {figlia.upper()} T WHERE" in cancellazioni, (
            f"{figlia} non viene rimossa: {database.state.get('deletes')}")

    # E LA FIGLIA PRIMA DEL GENITORE. `match_requirement_results` e' CASCADE
    # verso `match_runs`: cancellare il genitore per primo se la porterebbe
    # via senza che nessuno abbia verificato che fosse nostra. Sul doppio non
    # fallisce niente - non ci sono chiavi esterne vere - quindi l'ordine si
    # osserva qui, che e' l'unico posto in cui e' osservabile.
    eseguite = [q for q in database.state["deletes"] if " t WHERE t.id IN" in q]
    i_figlia = next(i for i, q in enumerate(eseguite)
                    if q.startswith("DELETE FROM match_requirement_results"))
    i_genitore = next(i for i, q in enumerate(eseguite)
                      if q.startswith("DELETE FROM match_runs"))
    assert i_figlia < i_genitore, eseguite


def test_96b_each_child_is_watched_by_the_guard_and_declared(monkeypatch):
    """E ciascuno passa dal catalogo: raccolto non vuol dire esentato.

    Se una di queste tabelle fosse semplicemente saltata, il cleanup
    funzionerebbe lo stesso e nessuno saprebbe che una riga altrui, nella
    stessa tabella, non verrebbe piu' vista.
    """
    _code, _report, database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        fk_perimetro=_fk_dei_figli(), dipendenti_estranee=0)
    interrogate = {p[0] for q, p in database.state["interrogazioni"]
                   if "pg_constraint" in q and p}
    for figlia in FIGLI_DELLE_FIXTURE:
        assert figlia in set(cert.Certification.CLEANUP_PARENTS), figlia
    # I genitori da cui pendono sono tutti passati alla guardia.
    genitori = {g.split(".")[-1] for g, _c in FIGLI_DELLE_FIXTURE.values()}
    assert genitori <= interrogate, sorted(genitori - interrogate)


def test_96d_the_membership_belongs_to_the_run_through_its_operator():
    """Due criteri di appartenenza, e la ragione per cui sono due.

    La membership dell'operatore A punta all'agenzia 1, che e' preesistente:
    con la regola generale - ogni riferimento non nullo dentro il perimetro -
    verrebbe dichiarata estranea, e bloccherebbe il cleanup su una riga che il
    run ha creato lui stesso.

    `operator_user_id` basta da solo perche' `operator_users` contiene solo
    identita' di questo run. Il doppio non valuta le WHERE, quindi questa
    differenza non si vede in un run simulato: si legge il predicato.
    """
    database = fake_database(agencies=AGENCIES)
    report, _stream = quiet_report()
    c = cert.Certification(database, report)

    frammento, params = c._effect_predicate(
        "agency_memberships", ("agency_id", "operator_user_id"),
        {"operator_users": (401,), "agencies": (501,)}, "t")
    assert frammento == "t.operator_user_id IN %s", frammento
    assert params == [(401,)], params
    assert "agency_id" not in frammento, frammento

    # Senza identita' del run non c'e' niente da rivendicare.
    assert c._effect_predicate(
        "agency_memberships", ("agency_id", "operator_user_id"),
        {"agencies": (501,)}, "t") is None

    # E per una tabella senza colonna propria resta la regola generale: ogni
    # riferimento non nullo dentro, almeno uno che ci punti.
    generale, _p = c._effect_predicate(
        "property_contacts", ("property_id", "contact_id"),
        {"properties": (21,), "contacts": (11,)}, "t")
    assert " OR " in generale and "IS NULL" in generale, generale


@pytest.mark.parametrize("figlia", sorted(FIGLI_DELLE_FIXTURE))
def test_96c_a_foreign_row_in_the_same_table_still_blocks(monkeypatch, figlia):
    """Una riga ESTRANEA nella stessa tabella continua a fermare tutto.

    E' la meta' che conta: raccogliere le nostre non deve rendere invisibili
    quelle di qualcun altro. Se `agency_memberships` contiene la membership di
    un operatore vero, o `followup_actions` un'azione che non abbiamo scritto
    noi, il cleanup si ferma - e nessun dato viene cancellato.
    """
    genitore, colonna = FIGLI_DELLE_FIXTURE[figlia]
    _code, report, database, probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        fk_perimetro={genitore.split(".")[-1]: [(f"public.{figlia}", colonna)]},
        dipendenti_estranee=1)

    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-PREFLIGHT" in fallimenti, [r[1] for r in report.rows]
    assert figlia in fallimenti["CLEAN-PREFLIGHT"], fallimenti["CLEAN-PREFLIGHT"]

    eseguite = database.state.get("deletes", [])
    # Nessuna cancellazione per appartenenza, su NESSUNA tabella di effetti:
    # e' la forma `... t WHERE t.id IN ... AND (predicato)`.
    assert not [q for q in eseguite if " t WHERE t.id IN" in q], eseguite
    dati = ("contacts", "properties", "buy_requests", "seller_timeline_events",
            "property_documents", "owner_shared_documents", "matches",
            "property_sales", "property_proposals", "owner_accounts",
            "owner_audit_log", "agencies", "stime", "flow_events", "tasks")
    assert not [q for q in eseguite if any(f"FROM {t} " in q + " " for t in dati)], \
        eseguite
    assert not [s for s, _st, _b in probe.exchanges if s.startswith("DELETE")]
    # `agency_memberships` e `operator_sessions` restano cancellabili da
    # `cleanup_database`: sono figlie di `operator_users`, tutte e due in
    # CASCADE e create da questo run, e lasciare credenziali vive sul TEST
    # sarebbe il danno peggiore. Non e' un'eccezione al blocco sui DATI.
    assert any("FROM operator_users" in q for q in eseguite), eseguite


#: Le TREDICI relazioni che il preflight del run 38e341f68f8a ha dichiarato
#: estranee. Sono tutte righe che il run aveva creato lui stesso: il perimetro
#: non le conteneva, e la guardia - che faceva il suo mestiere - ha bloccato
#: sette cancellazioni su nove. Qui si riproducono TUTTE, non una per tabella:
#: `property_sale_sellers` era segnalata due volte, da `contact_id` e da
#: `sale_id`, e una prova che ne coprisse una sola sarebbe passata lo stesso.
RELAZIONI_DEL_RUN_38E341 = [
    ("contacts", "public.property_sale_sellers", "contact_id"),
    ("contacts", "public.followup_actions", "contact_id"),
    ("properties", "public.owner_property_access", "property_id"),
    ("owner_accounts", "public.owner_property_access", "owner_account_id"),
    ("owner_accounts", "public.owner_access_tokens", "owner_account_id"),
    ("owner_accounts", "public.owner_sessions", "owner_account_id"),
    ("property_sales", "public.property_sale_sellers", "sale_id"),
    ("agencies", "public.agency_memberships", "agency_id"),
    ("agencies", "public.followup_actions", "agency_id"),
    ("operator_users", "public.agency_memberships", "operator_user_id"),
    ("operator_users", "public.operator_sessions", "operator_user_id"),
    ("tasks", "public.followup_actions", "task_id"),
    ("match_runs", "public.match_requirement_results", "match_run_id"),
]


def _fk_del_run_38e341():
    per_genitore = {}
    for genitore, figlia, colonna in RELAZIONI_DEL_RUN_38E341:
        per_genitore.setdefault(genitore, []).append((figlia, colonna))
    return per_genitore


def test_97_the_live_preflight_no_longer_calls_the_runs_own_rows_foreign(monkeypatch):
    """IL CASO LIVE 38e341f68f8a, tutte e tredici le relazioni insieme.

    Il preflight le aveva dichiarate estranee e aveva fermato il cleanup:
    sette FAIL su dieci erano quella stessa riga ripetuta dai metodi a valle.
    Nessuna di quelle righe era di qualcun altro - erano il legame venditore
    della vendita del run, l'azione FOLLOWUP della sua agenzia dedicata, i
    token e le sessioni del suo proprietario, la membership della sua
    identita', i risultati del suo match.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        fk_perimetro=_fk_del_run_38e341(), dipendenti_estranee=0)

    esiti = {i: k for k, i, _t in report.rows}
    assert esiti.get("CLEAN-PREFLIGHT") == cert.PASS, \
        [r for r in report.rows if r[1] == "CLEAN-PREFLIGHT"]
    assert esiti.get("CLEAN-ORFANE") == cert.PASS, \
        [r for r in report.rows if r[1] == "CLEAN-ORFANE"]
    assert esiti.get("CLEAN-DEDICATA") == cert.PASS, \
        [r for r in report.rows if r[1] == "CLEAN-DEDICATA"]
    assert esiti.get("CLEAN-VERIFICA") == cert.PASS, \
        [r for r in report.rows if r[1] == "CLEAN-VERIFICA"]

    # E ogni genitore delle tredici e' stato davvero interrogato.
    interrogate = {p[0] for q, p in database.state["interrogazioni"]
                   if "pg_constraint" in q and p}
    genitori = {g for g, _f, _c in RELAZIONI_DEL_RUN_38E341}
    assert genitori <= interrogate, sorted(genitori - interrogate)


def test_97b_a_blocked_cleanup_makes_the_verification_say_it_is_a_consequence(
        monkeypatch):
    """CLEAN-VERIFICA e' una conseguenza, non un secondo guasto.

    Nel run 38e341f68f8a elencava ventun voci di "righe ANCORA PRESENTI" -
    tutte vere, e tutte per DECISIONE: il cleanup era bloccato. Dieci FAIL per
    un problema solo spostano la diagnosi sul sintomo.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]},
        fk_perimetro={"properties": [("public.property_visits", "property_id")]},
        dipendenti_estranee=1)
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-PREFLIGHT" in fallimenti, [r[1] for r in report.rows]
    assert "CLEAN-VERIFICA" in fallimenti
    testo = fallimenti["CLEAN-VERIFICA"]
    assert "conseguenza" in testo, testo
    assert "PER DECISIONE" in testo, testo
    # E NON elenca le righe una per una: quell'elenco e' il sintomo.
    assert "ANCORA PRESENTI" not in testo, testo


def test_97c_the_flow_event_payload_passes_the_real_route_model():
    """Il payload dell'evento FLOW, validato dal MODELLO VERO.

    `flow/schemas.EventCreate` dichiara `source_module` come Literal chiuso e
    SENZA valore predefinito: ometterlo e' 422 prima ancora del servizio. E'
    il `FLOW-fixture-C/D -> 422` del run 38e341f68f8a, per cui le due liste
    non avevano niente da confrontare.
    """
    import typing

    import pydantic
    from flow.schemas import EventCreate

    payload = {"event_type": "P26-6-xxxx-C", "entity_type": "contact",
               "entity_id": 90, "source_module": cert.Certification.FLOW_SOURCE_MODULE,
               "payload": {}, "deduplication_key": "P26-6-xxxx-C"}
    modello = EventCreate(**payload)
    assert modello.source_module == cert.Certification.FLOW_SOURCE_MODULE

    ammessi = set(typing.get_args(
        EventCreate.model_fields["source_module"].annotation))
    assert cert.Certification.FLOW_SOURCE_MODULE in ammessi, sorted(ammessi)

    # Senza il campo il modello rifiuta: e' cio' che rende non vacua la prova.
    senza = {k: v for k, v in payload.items() if k != "source_module"}
    with pytest.raises(pydantic.ValidationError):
        EventCreate(**senza)


def test_97d_the_watch_fixture_provides_the_completed_valuation():
    """La stima da sola non basta: serve l'evento `stima_completata`.

    `property_watch.service._baseline_for_stima_scoped` legge DUE cose - gli
    attributi della stima e la valutazione completata da
    `seller_timeline_events` - e se la seconda manca solleva ValidationError,
    che il router traduce in 400. E' l'`initialize -> 400` del run
    38e341f68f8a: la stima c'era, la sua valutazione no.
    """
    sorgente = SCRIPT.read_text(encoding="utf-8")
    assert "INSERT INTO seller_timeline_events" in sorgente
    assert "STIMA_COMPLETATA_EVENT" in sorgente
    assert cert.Certification.STIMA_COMPLETATA_EVENT == "stima_completata"

    # Il tipo di evento e' quello che il servizio cerca davvero.
    servizio = (ROOT / "property_watch" / "repository.py").read_text(encoding="utf-8")
    assert f"event_type = '{cert.Certification.STIMA_COMPLETATA_EVENT}'" in servizio

    # E l'evento vive in un'agenzia dedicata: deve sparire con lei, altrimenti
    # `agencies.agency_id` e' RESTRICT e la cancellazione dell'agenzia fallisce.
    dedicate = {t for t, _c in cert.Certification.DEDICATED_TABLES}
    assert "seller_timeline_events" in dedicate, sorted(dedicate)


def test_97e_the_perimeter_unions_instead_of_overwriting():
    """`seller_timeline_events` sta in DUE registri, e il perimetro li unisce.

    E' la risorsa del dominio SELLER_INTELLIGENCE e insieme l'evento della
    fixture PROPERTY_WATCH. Sovrascrivendo, gli id del dominio sparivano dal
    perimetro - e con loro la protezione che il perimetro fornisce.
    """
    database = fake_database(agencies=AGENCIES)
    report, _stream = quiet_report()
    c = cert.Certification(database, report)
    c.created_rows = {"seller_timeline_events": [(108, "M", "event_type")]}
    c.created_effects = {"seller_timeline_events": [10601]}
    assert set(c._perimeter()["seller_timeline_events"]) == {108, 10601}


def test_97f_the_audit_without_roots_is_classified_and_still_fails():
    """I sei audit senza radice: classificati, e ancora un FAIL.

    "Non classificate" era la descrizione di cio' che il censimento NON
    faceva. Adesso li raggruppa per `entity_type`, che e' quel che la riga
    dice davvero di se stessa, e dichiara che l'origine non e' attribuita -
    non che un SET NULL sia gia' avvenuto, cosa che dalla riga sola non si
    distingue. Restano un FAIL e restano sul database.
    """
    database = fake_database(agencies=AGENCIES,
                             audit_senza_radice=[{"tipo": "shared_document",
                                                  "n": 4, "con_entita": 4},
                                                 {"tipo": "(nessuno)",
                                                  "n": 2, "con_entita": 0}])
    report, _stream = quiet_report()
    cert.incoherence_census(report, database)

    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CENSUS" in fallimenti, report.rows
    testo = fallimenti["CENSUS"]
    assert "6 righe" in testo, testo
    assert "shared_document=4" in testo and "(nessuno)=2" in testo, testo
    assert "ORIGINE NON ATTRIBUITA" in testo, testo
    assert "non classificate" not in testo, testo
    # Nessuna cancellazione: il censimento e' in sola lettura.
    assert not database.state.get("deletes"), database.state.get("deletes")


def test_97g_a_broken_route_is_reported_as_broken_not_as_bad_isolation(monkeypatch):
    """Un 5xx su una lista dice che la ROUTE e' rotta, non che l'isolamento
    fallisce - e il report deve distinguerli.

    Nel run 38e341f68f8a `LEGACY_ADMIN-list-A` si chiudeva con `-> 500` e
    nient'altro: il run successivo avrebbe rifatto la stessa domanda e
    ottenuto la stessa riga. Adesso il corpo viene ridotto alla sua forma -
    tipo di eccezione e identificatori di schema - e mai stampato integralmente.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(broken={"listing_500"},
                                   prepopulate=DERIVED, stime=STIME))
    fallimenti = [t for k, i, t in report.rows
                  if k == cert.FAIL and i.startswith("LEGACY_ADMIN-list")]
    assert fallimenti, [r[1] for r in report.rows if r[0] == cert.FAIL]
    testo = fallimenti[0]
    assert "-> 500" in testo, testo
    assert "ROTTA, non isolata male" in testo, testo
    assert "UndefinedColumn" in testo, testo
    # E l'OGGETTO nominato dall'errore: senza, il tipo da solo dice che
    # qualcosa non esiste ma non che cosa, e la diagnosi ricomincia da capo.
    assert "oggetto=s.note_internal" in testo, testo
    # La FORMA, non il testo: nessuna riga di query, nessun frammento di SQL.
    assert "LINE 3" not in testo and "SELECT s.id" not in testo, testo


def test_97h_the_watch_fixture_runs_before_the_nba_refresh(monkeypatch):
    """L'ordine e' osservabile, e conta.

    Il refresh NBA legge i segnali P17-P22: in un'agenzia appena creata non
    c'e' niente da cui nascere, e "0 azioni" sarebbe garantito prima ancora di
    chiamare la route. Una prova che non puo' fallire non e' una prova. La
    stima con la sua valutazione completata va creata PRIMA.
    """
    _code, _report, _db, probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    percorsi = [s for s, _st, _b in probe.exchanges]
    i_watch = next(i for i, s in enumerate(percorsi) if s.endswith("/initialize"))
    i_nba = next(i for i, s in enumerate(percorsi)
                 if s == "POST /api/next-best-action/refresh")
    assert i_watch < i_nba, percorsi[min(i_watch, i_nba):max(i_watch, i_nba) + 1]


def test_95_the_shared_document_payload_passes_the_real_route_model():
    """Il payload della condivisione, validato dal MODELLO VERO della route.

    Il run 52f6d97b5214 ha preso 422 su `POST /api/owner/admin/documents`
    perche' mandava `public_document_type: "other"`, e
    `owner/schemas.SharedDocumentType` e' un Literal chiuso che non lo
    contiene. Il doppio HTTP non poteva accorgersene: risponde 201 a quello
    che gli si manda, e nessuna prova locale passava dal modello.

    Qui il payload viene costruito come lo costruisce lo script e dato a
    `SharedDocumentCreate`. Se il valore torna a essere invalido, questa prova
    fallisce prima del run, non dopo.
    """
    from owner.schemas import SharedDocumentCreate

    payload = {"property_document_id": 51,
               "public_title": "P26-6-xxxx-A",
               "public_document_type": cert.Certification.OWNER_PUBLIC_DOCUMENT_TYPE}
    modello = SharedDocumentCreate(**payload)
    assert modello.public_document_type == cert.Certification.OWNER_PUBLIC_DOCUMENT_TYPE

    # E il valore di prima sarebbe rifiutato: senza questo, la prova sopra
    # passerebbe anche se il modello accettasse qualunque stringa.
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        SharedDocumentCreate(**{**payload, "public_document_type": "other"})


def test_95b_the_upload_form_takes_the_same_closed_type():
    """Anche il caricamento passa dallo stesso Literal.

    `document_upload` dichiara `public_document_type: SharedDocumentType =
    Form(...)`: era il secondo 422, e sarebbe rimasto anche correggendo solo
    la condivisione.
    """
    import inspect
    import typing

    from owner.router_admin import document_upload
    from owner.schemas import SharedDocumentType

    annotazione = inspect.signature(document_upload).parameters[
        "public_document_type"].annotation
    assert annotazione is SharedDocumentType, annotazione
    ammessi = set(typing.get_args(SharedDocumentType))
    assert cert.Certification.OWNER_PUBLIC_DOCUMENT_TYPE in ammessi, sorted(ammessi)
    assert "other" not in ammessi, sorted(ammessi)


def test_95c_the_property_document_type_is_free_text_and_stays_other():
    """Il tipo del documento dell'IMMOBILE e' un'altra cosa.

    `property/schemas.py` lo dichiara stringa libera: "other" li' e' valido, e
    cambiarlo insieme all'altro sarebbe stato correggere un difetto che non
    c'era. Due campi con nomi simili, due contratti diversi.
    """
    from property.schemas import DocumentCreate

    modello = DocumentCreate(
        document_type=cert.Certification.PROPERTY_DOCUMENT_TYPE,
        title="P26-6-xxxx-A",
        url="https://certification.invalid/P26-6-xxxx-A",
        status="available")
    assert modello.document_type == "other"


def test_93c_a_wrong_origin_in_the_response_is_caught_and_not_deleted(monkeypatch):
    """201 con `property_document_id` SBAGLIATO.

    E' il caso che un controllo "se manca, leggilo dal database" non vede: il
    campo c'e', non e' nullo, e indica un altro documento. Cancellare quello
    rimuoverebbe una riga che non appartiene a questo run - potenzialmente di
    un'altra agenzia - e lascerebbe nel bucket proprio l'oggetto che era
    nostro, cioe' entrambi i danni insieme.

    La fonte e' `owner_shared_documents.property_document_id`, che e' la
    colonna legata all'oggetto da una FK ed e' NOT NULL. La risposta si
    confronta, non si segue.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch,
        http=FakeHttp(broken={"upload_origine_sbagliata"}, only="portal",
                      prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-STORAGE-ORIGINE" in fallimenti, report.rows
    assert "il database dice" in fallimenti["CLEAN-STORAGE-ORIGINE"]

    canonici = set(database.state["origine_condivisa"].values())
    assert len(canonici) == 2, canonici
    inventati = {c + 7000 for c in canonici}

    # 1. L'id inventato non compare in NESSUN parametro, di nessuna query:
    #    ne' in una DELETE, ne' nella lettura delle chiavi dello storage, ne'
    #    nella verifica finale. Non e' mai entrato nel perimetro.
    ovunque = _valori_dei_parametri(database.state)
    assert not (inventati & ovunque), sorted(inventati & ovunque)

    # 2. I canonici invece SONO entrati, e proprio in una DELETE su
    #    property_documents: senza questo, il punto 1 sarebbe soddisfatto anche
    #    da un cleanup che non ha cancellato niente.
    su_documenti = _valori_dei_parametri(
        database.state,
        lambda q: q.upper().startswith("DELETE FROM PROPERTY_DOCUMENTS"))
    assert canonici <= su_documenti, (sorted(canonici), sorted(su_documenti))

    # 3. E il bucket e' stato ripulito sugli id veri: due caricamenti, due chiavi.
    assert sorted(database.state.get("chiavi_cancellate", [])) == \
        sorted(f"k/{c}" for c in canonici), database.state.get("chiavi_cancellate")


def test_93e_a_non_numeric_origin_is_diagnosed_without_losing_the_canonical(monkeypatch):
    """201 con `property_document_id` NON NUMERICO.

    Il canonico viene registrato PRIMA di guardare la risposta. Se l'ordine
    fosse l'inverso, la conversione che esplode porterebbe via anche l'id
    valido che il database aveva gia' dato: il bucket non verrebbe ripulito -
    e per un difetto nella diagnostica di un altro difetto, che e' il modo
    peggiore di perdere un file.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch,
        http=FakeHttp(broken={"upload_origine_non_numerica"}, only="portal",
                      prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-STORAGE-ORIGINE" in fallimenti, report.rows
    testo = fallimenti["CLEAN-STORAGE-ORIGINE"]
    assert "non numerico" in testo and "str" in testo, testo
    # Il valore remoto non viene stampato: solo tipo e lunghezza.
    assert "abc" not in testo, testo

    canonici = set(database.state["origine_condivisa"].values())
    su_documenti = _valori_dei_parametri(
        database.state,
        lambda q: q.upper().startswith("DELETE FROM PROPERTY_DOCUMENTS"))
    assert canonici <= su_documenti, (sorted(canonici), sorted(su_documenti))
    assert sorted(database.state.get("chiavi_cancellate", [])) == \
        sorted(f"k/{c}" for c in canonici), database.state.get("chiavi_cancellate")
    # E il cleanup non e' stato bloccato: il canonico c'era.
    assert not any(i == "CLEAN-ORFANE" for k, i, _t in report.rows if k == cert.FAIL), \
        report.rows


def test_93d_the_database_is_read_even_when_the_response_carries_the_id(monkeypatch):
    """La lettura canonica avviene SEMPRE, non solo quando il campo manca.

    Senza questo vincolo, un id sbagliato ma non nullo salterebbe ogni
    controllo: e' esattamente la forma che aveva il difetto.
    """
    _code, _report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    letture = [q for q in database.state["sql"]
               if q.startswith("SELECT property_document_id FROM owner_shared_documents")]
    assert len(letture) == 2, letture


def test_92_a_run_that_created_nothing_says_so_instead_of_passing_silently():
    """Zero id in ogni categoria: si dichiara, non si tace. Il silenzio era
    indistinguibile da una verifica riuscita."""
    database = fake_database(agencies=AGENCIES)
    report, _stream = quiet_report()
    certificazione = cert.Certification(database, report)
    certificazione.verify_no_residue()
    righe = {i: (k, t) for k, i, t in report.rows}
    assert righe["CLEAN-VERIFICA"][0] == cert.PASS
    assert "non ha creato nulla" in righe["CLEAN-VERIFICA"][1]
    assert not database.state.get("sql"), database.state.get("sql")


# ===========================================================================
# 98 - property_watch_observations nel perimetro distruttivo
#
# IL FAIL DEL RUN 58aa0e189aaa, e perche' era nostro.
#
# `CLEAN-PREFLIGHT` si e' fermato su `property_watch_observations.watch_id=2`
# e ha bloccato l'intero cleanup: sei dei dieci FAIL erano quella stessa riga
# ripetuta dai metodi a valle. Le due righe non erano di qualcun altro - le
# aveva scritte `POST .../initialize`, cioe' la fixture PROPERTY_WATCH del run
# stesso, nella stessa transazione del watch.
#
# Questi test pretendono le due cose insieme, ed e' la coppia che conta: le
# NOSTRE entrano nel perimetro, si cancellano per id e si verificano per id;
# quelle di chiunque altro continuano a fermare tutto.
# ===========================================================================

def _osservazione_estranea(database, watch_id=None, **campi):
    """Una riga scritta da qualcun altro sullo stesso watch."""
    osservazioni = database.state.setdefault("osservazioni", {})
    if watch_id is None:
        watch_id = next(iter(database.state.get("watch_vivi", {})), 7701)
    riga = {"watch_id": watch_id, "idempotency_key": "scansione:2026-09-12",
            "observation_type": "price_change", "source": "scan"}
    riga.update(campi)
    osservazioni[990001] = riga
    return 990001


def test_98a_the_derived_key_is_the_one_the_repository_really_writes():
    """La chiave non e' inventata qui: e' letta dal repository.

    Tutto il criterio di appartenenza pende da tre costanti. Se un giorno il
    repository cambiasse la forma della chiave, o il tipo, o la sorgente, il
    cleanup smetterebbe di riconoscere le proprie righe e tornerebbe a
    bloccarsi - senza che nulla, nel frattempo, fosse diventato rosso. Questo
    test e' il legame che manca a quel silenzio.
    """
    sorgente = (ROOT / "property_watch" / "repository.py").read_text(encoding="utf-8")
    corpo = re.search(
        r"def ensure_watch_with_baseline_scoped\(.*?\n(?=\ndef )", sorgente, re.S)
    assert corpo, "ensure_watch_with_baseline_scoped non trovata"
    testo = corpo.group(0)

    # La chiave, con il segnaposto al posto dello stima_id.
    chiavi = re.findall(r'f"(property_watch:[^"]+)"', testo)
    assert chiavi, testo[:400]
    normalizzata = chiavi[0].replace("{stima_id}", "{stima_id}")
    assert normalizzata == cert.WATCH_BASELINE_KEY, (normalizzata,
                                                     cert.WATCH_BASELINE_KEY)
    # Tipo e sorgente sono letterali dentro la INSERT.
    inserimento = re.search(
        r"INSERT INTO property_watch_observations.*?VALUES \(([^)]*)\)", testo, re.S)
    assert inserimento, testo[:400]
    letterali = re.findall(r"'([a-z_]+)'", inserimento.group(1))
    assert cert.WATCH_BASELINE_TYPE in letterali, letterali
    assert cert.WATCH_BASELINE_SOURCE in letterali, letterali


def test_98b_the_baseline_observation_is_recognised_and_does_not_block(monkeypatch):
    """IL CASO LIVE 58aa0e189aaa: la riga e' nostra, e il cleanup prosegue."""
    _code, report, database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        fk_perimetro={"property_watches": [
            ("public.property_watch_observations", "watch_id")]},
        dipendenti_estranee=0)

    # Il doppio ha davvero prodotto le osservazioni: senza, questo test
    # sarebbe soddisfatto dal vuoto.
    assert database.state["watch_stima"], database.state
    esiti = {i: k for k, i, _t in report.rows}
    assert esiti.get("CLEAN-OSSERVAZIONI") == cert.PASS, \
        [r for r in report.rows if r[1] == "CLEAN-OSSERVAZIONI"]
    assert esiti.get("CLEAN-PREFLIGHT") == cert.PASS, \
        [r for r in report.rows if r[1] == "CLEAN-PREFLIGHT"]
    assert esiti.get("CLEAN-DEDICATA") == cert.PASS, \
        [r for r in report.rows if r[1] == "CLEAN-DEDICATA"]
    assert esiti.get("CLEAN-VERIFICA") == cert.PASS, \
        [r for r in report.rows if r[1] == "CLEAN-VERIFICA"]
    riga = next(t for k, i, t in report.rows if i == "CLEAN-OSSERVAZIONI")
    # QUATTRO: due nelle agenzie dedicate e due in quelle condivise, da
    # quando la fixture PROPERTY_WATCH crea una stima osservabile anche per A
    # e B. Il numero e' parte della prova: se tornasse a due, una coppia di
    # osservazioni sarebbe fuori dal riconoscimento.
    assert "4 riconosciute" in riga, riga
    assert "0 estranee" in riga, riga
    # E l'azione dichiarata accompagna il nome, come per ogni altra figlia.
    assert "property_watch_observations (RESTRICT)" in riga, riga


def test_98c_they_are_locked_and_deleted_by_id_before_the_watch(monkeypatch):
    """Lock, poi DELETE per id, poi il genitore. In quest'ordine.

    Senza il lock si cancellerebbe cio' che si e' guardato un istante prima;
    per `watch_id` si porterebbe via anche una riga scritta da una scansione
    nel frattempo; e dopo il watch la DELETE non arriverebbe mai, perche' la
    RESTRICT avrebbe gia' fatto cadere la transazione.
    """
    _code, _report, database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    sql = database.state["sql"]

    lock = [i for i, q in enumerate(sql)
            if q.startswith("SELECT id FROM property_watch_observations")
            and "FOR UPDATE NOWAIT" in q]
    cancella = [i for i, q in enumerate(sql)
                if q.startswith("DELETE FROM property_watch_observations")]
    # LE DUE CANCELLAZIONI DEI WATCH, e sono due percorsi diversi: le agenzie
    # CONDIVISE per id (`cleanup_shared_watch_fixtures`), quelle DEDICATE per
    # agenzia. L'ordine da provare e' quello del percorso dedicato, che e' il
    # solo in cui il lock precede la cancellazione.
    watch_per_agenzia = [i for i, q in enumerate(sql)
                         if q.startswith("DELETE FROM property_watches WHERE agency_id")]
    assert lock, [q for q in sql if "property_watch_observations" in q]
    assert cancella, [q for q in sql if "property_watch_observations" in q]
    assert watch_per_agenzia, [q for q in sql if "property_watches" in q]
    dopo_lock = [i for i in cancella if i > max(lock)]
    assert dopo_lock, (lock, cancella)
    assert max(lock) < min(dopo_lock) < min(watch_per_agenzia), \
        (lock, dopo_lock, watch_per_agenzia)
    # MAI per watch_id: quello e' il criterio che prenderebbe righe altrui.
    for q in (sql[i] for i in cancella):
        assert "WHERE id IN" in q, q
        assert "watch_id" not in q, q


def test_98d_the_residue_is_verified_by_id_after_the_parent_is_gone(monkeypatch):
    """La verifica finale le cerca per ID, dopo la cancellazione del watch.

    Cercarle per `watch_id` risponderebbe 0 comunque: quel watch non esiste
    piu'. E' la stessa vacuita' della JOIN al genitore cancellato.
    """
    _code, _report, database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    sql = database.state["sql"]
    verifiche = [i for i, q in enumerate(sql)
                 if q.startswith("SELECT COUNT(*) AS n FROM property_watch_observations")
                 and "WHERE id IN" in q]
    watch = max(i for i, q in enumerate(sql)
                if q.startswith("DELETE FROM property_watches"))
    assert verifiche, [q for q in sql if "property_watch_observations" in q]
    assert max(verifiche) > watch, (verifiche, watch)


def test_98e_a_delete_that_does_nothing_is_reported_as_a_residue(monkeypatch):
    """Se la DELETE non cancella, la verifica se ne accorge.

    La mutazione e' sul DOPPIO, non sul test: la riga resta nel database
    simulato dopo la cancellazione. Se la verifica finale non la cercasse per
    id, questo run risulterebbe pulito.
    """
    originale = FakeCursor._osservazioni_execute

    def sorda(self, upper, params):
        if upper.startswith("DELETE"):
            self.state.setdefault("deletes", []).append(upper)
            return
        return originale(self, upper, params)

    monkeypatch.setattr(FakeCursor, "_osservazioni_execute", sorda)
    _code, report, _database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-VERIFICA" in fallimenti, [r[1] for r in report.rows]
    assert "property_watch_observations (RESTRICT)=4" in fallimenti["CLEAN-VERIFICA"], \
        fallimenti["CLEAN-VERIFICA"]
    assert "verificate per id" in fallimenti["CLEAN-VERIFICA"]


def test_98f_a_foreign_observation_on_our_watch_still_blocks_everything(monkeypatch):
    """LA GUARDIA NON E' STATA INDEBOLITA.

    Stessa tabella, stesso watch, chiave diversa: la riga non e' del run - una
    scansione periodica, il motore invisible-sale - e deve fermare tutto come
    prima. Se la correzione avesse escluso la TABELLA invece delle RIGHE
    RICONOSCIUTE, questo test sarebbe verde per il motivo sbagliato.
    """
    _code, report, _database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        figli_cascata={"property_watch_observations": (1, 1)})
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-DEDICATA" in fallimenti, [r[1] for r in report.rows]
    assert "property_watch_observations=1" in fallimenti["CLEAN-DEDICATA"], \
        fallimenti["CLEAN-DEDICATA"]
    assert "vanno esaminate prima" in fallimenti["CLEAN-DEDICATA"]


def test_98g_a_row_with_the_right_key_but_another_source_is_foreign(monkeypatch):
    """Tre condizioni, non una. La chiave da sola non basta.

    Un'osservazione che portasse la chiave attesa ma fosse stata scritta da
    un'altra sorgente non e' quella che `initialize` ha creato, e trattarla
    come propria significherebbe cancellare una riga altrui per somiglianza.
    """
    database = fake_database(agencies=AGENCIES, owners=OWNERS, stime=STIME)
    report, _stream = quiet_report()
    certificazione = cert.Certification(database, report)
    certificazione.created_agency_ids = [20]
    certificazione.child_parents = {"property_watches": (7701,)}
    database.state["watch_stima"] = {7701: 501}
    database.state["osservazioni"] = {
        1: {"watch_id": 7701,
            "idempotency_key": cert.WATCH_BASELINE_KEY.format(stima_id=501),
            "observation_type": cert.WATCH_BASELINE_TYPE,
            "source": cert.WATCH_BASELINE_SOURCE},
        2: {"watch_id": 7701,
            "idempotency_key": cert.WATCH_BASELINE_KEY.format(stima_id=501),
            "observation_type": cert.WATCH_BASELINE_TYPE,
            "source": "scan"},
    }
    certificazione._snapshot_owned_children()
    assert certificazione.owned_child_ids == {
        "property_watch_observations": (1,)}, certificazione.owned_child_ids


def test_98h_an_unreadable_snapshot_blocks_every_deletion(monkeypatch):
    """FAIL-CLOSED. Non poter distinguere non e' "erano tutte nostre".

    Senza questa istantanea le righe del run e quelle altrui sono
    indistinguibili: cancellare le prime significherebbe rischiare le seconde.
    Il mutante da uccidere e' "segnala e prosegui".
    """
    database = fake_database(agencies=AGENCIES, owners=OWNERS, stime=STIME,
                             explode_on="idempotency_key")
    report, _stream = quiet_report()
    certificazione = cert.Certification(database, report)
    certificazione.created_agency_ids = [20]
    # Il watch esiste davvero nel doppio: senza, l'istantanea non avrebbe
    # genitori da cui partire, uscirebbe subito e il mutante da uccidere non
    # verrebbe mai raggiunto.
    database.state["watch_vivi"] = {7701: 20}
    database.state["watch_stima"] = {7701: 501}
    certificazione.snapshot_before_cleanup()
    assert certificazione._destructive_db_blocked(), report.rows
    assert any(i == "CLEAN-OSSERVAZIONI" and k == cert.FAIL
               for k, i, _t in report.rows), report.rows

    certificazione.cleanup_dedicated_agencies()
    assert not [q for q in database.state.get("sql", []) if q.startswith("DELETE")], \
        [q for q in database.state["sql"] if q.startswith("DELETE")]


def test_98i_removing_the_table_from_CLEANUP_PARENTS_is_caught(monkeypatch):
    """MUTAZIONE: la tabella si cancella ma nessuno l'ha dichiarata.

    Una tabella fuori da CLEANUP_PARENTS non passa dalla prova di completezza
    sulle FK: le sue dipendenze entranti non sono mai state esaminate. Il
    preflight deve rifiutarsi di procedere, non ignorare la cosa.
    """
    monkeypatch.setattr(
        cert.Certification, "CLEANUP_PARENTS",
        tuple(t for t in cert.Certification.CLEANUP_PARENTS
              if t != "property_watch_observations"))
    _code, report, _database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-PREFLIGHT" in fallimenti, [r[1] for r in report.rows]
    assert "non dichiarata in CLEANUP_PARENTS" in fallimenti["CLEAN-PREFLIGHT"], \
        fallimenti["CLEAN-PREFLIGHT"]
    assert "property_watch_observations" in fallimenti["CLEAN-PREFLIGHT"]


def test_98j_without_the_perimeter_entry_the_preflight_blocks_again(monkeypatch):
    """MUTAZIONE: le nostre righe fuori dal perimetro distruttivo.

    E' esattamente lo stato in cui girava il run 58aa0e189aaa. Il mutante
    svuota `owned_child_ids` dopo l'istantanea: la guardia torna a chiamare
    estranea una riga creata da noi, e il cleanup si ferma. Se questo test
    passasse anche con la correzione attiva, la correzione non servirebbe a
    niente.
    """
    originale = cert.Certification._snapshot_owned_children

    def poi_dimentica(self):
        esito = originale(self)
        self.owned_child_ids = {}
        return esito

    monkeypatch.setattr(cert.Certification, "_snapshot_owned_children", poi_dimentica)
    _code, report, _database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        fk_perimetro={"property_watches": [
            ("public.property_watch_observations", "watch_id")]},
        dipendenti_estranee=0)
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-PREFLIGHT" in fallimenti, [r[1] for r in report.rows]
    # QUATTRO: due osservazioni nelle agenzie dedicate e due in quelle
    # condivise, da quando la fixture PROPERTY_WATCH crea una stima
    # osservabile anche per A e B.
    assert "property_watch_observations.watch_id=4" in fallimenti["CLEAN-PREFLIGHT"], \
        fallimenti["CLEAN-PREFLIGHT"]


def test_98k_without_the_exclusion_the_restrict_guard_blocks_its_own_rows(monkeypatch):
    """MUTAZIONE: la guardia RESTRICT senza l'esclusione delle nostre.

    `_blocking_children` conterebbe di nuovo anche le righe che la stessa
    transazione sta per rimuovere, e CLEAN-DEDICATA si fermerebbe su di esse.
    """
    originale = cert.Certification._blocking_children

    def senza_esclusione(self, genitore):
        salvate, self.owned_child_ids = self.owned_child_ids, {}
        try:
            return originale(self, genitore)
        finally:
            self.owned_child_ids = salvate

    monkeypatch.setattr(cert.Certification, "_blocking_children", senza_esclusione)
    _code, report, _database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-DEDICATA" in fallimenti, [r[1] for r in report.rows]
    assert "property_watch_observations=2" in fallimenti["CLEAN-DEDICATA"], \
        fallimenti["CLEAN-DEDICATA"]


# ===========================================================================
# 99 - il 422 dei documenti OWNER: causa, corpo, e fixture corretta
#
# Due run - 52f6d97b5214 e 58aa0e189aaa - hanno chiuso
# `owner-fixture-A/B-documento` con "condivisione -> 422" e nient'altro. La
# prima diagnosi (`public_document_type` fuori dal Literal) era plausibile,
# veniva da una lettura dello schema, ed era SBAGLIATA: corretta quella, il run
# successivo ha ripreso lo stesso 422.
#
# La causa vera non e' nello schema ma nel repository, ed e' una regola di
# dominio: un documento nato da un `url` non ha `storage_key`, e il portale
# condivide solo file che il sistema custodisce. Il backend ha ragione.
#
# Il motivo era nel corpo della risposta, che nessuno stampava. Per questo i
# test qui sotto sono tre cose insieme: la regola, il corpo, e la fixture.
# ===========================================================================

def test_99a_the_rule_that_returns_422_is_where_this_says_it_is():
    """Il 422 nasce in `create_shared_document`, non nello schema.

    Se un giorno la regola sparisse o cambiasse eccezione, la fixture qui
    sotto starebbe provando una cosa che non succede piu'.
    """
    repo = (ROOT / "owner" / "repository.py").read_text(encoding="utf-8")
    corpo = re.search(r"def create_shared_document\(.*?\n(?=\ndef )", repo, re.S)
    assert corpo, "create_shared_document non trovata"
    testo = corpo.group(0)
    guardia = re.search(
        r"if src\[.status.\] != .available. or not src\.get\(.storage_key.\):\s*"
        r"\n\s*raise ValidationError", testo)
    assert guardia, testo[:600]

    # E il wrapper della route traduce ValidationError in 422: senza questo
    # anello, la regola ci sarebbe ma lo stato osservato sarebbe un altro.
    router = (ROOT / "owner" / "router_admin.py").read_text(encoding="utf-8")
    assert re.search(r"except ValidationError as exc:\s*\n\s*raise HTTPException\(422",
                     router), router[:400]


def test_99b_a_url_only_document_cannot_be_shared_and_the_schema_is_not_why():
    """Lo schema ACCETTA il payload: il rifiuto arriva dopo.

    E' questa la distinzione che e' costata due run. `SharedDocumentCreate`
    valida senza obiezioni quello che la fixture manda - `public_document_type`
    compreso - quindi cercare la causa nello schema non poteva che fallire.
    """
    from owner.schemas import SharedDocumentCreate

    modello = SharedDocumentCreate(
        property_document_id=51,
        public_title="P26-6-xxxx-A",
        public_document_type=cert.Certification.OWNER_PUBLIC_DOCUMENT_TYPE)
    assert modello.public_document_type == \
        cert.Certification.OWNER_PUBLIC_DOCUMENT_TYPE

    # E il documento di origine, come la fixture lo creava, e' valido come
    # documento e inservibile come sorgente di condivisione: `url` senza
    # `storage_key`.
    from property.schemas import DocumentCreate

    origine = DocumentCreate(
        document_type=cert.Certification.PROPERTY_DOCUMENT_TYPE,
        title="P26-6-xxxx-A", url="https://certification.invalid/x",
        status="available")
    assert origine.url and not origine.storage_key


def test_99c_the_portal_document_now_comes_from_the_upload(monkeypatch):
    """La lista del portale si regge sul documento CARICATO.

    Con la fixture precedente `portal_documents` veniva dalla condivisione di
    un documento via URL, che il backend non crea mai: la lista restava vuota
    e i due `OWNER_PORTAL-documenti-*` erano BLOCKED a ogni run.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    righe = {i: (k, t) for k, i, t in report.rows}
    assert righe["owner-fixture-A-documento"][0] == cert.PASS, \
        righe["owner-fixture-A-documento"]
    assert "caricato e pubblicato" in righe["owner-fixture-A-documento"][1]
    assert righe["OWNER_PORTAL-documenti-A"][0] == cert.PASS, \
        righe["OWNER_PORTAL-documenti-A"]
    assert righe["OWNER_PORTAL-download-A"][0] == cert.PASS, \
        righe["OWNER_PORTAL-download-A"]
    # UN SOLO DOCUMENTO REGGE ENTRAMBE LE PROVE: elencabile perche'
    # pubblicato, scaricabile perche' ha un oggetto nello storage. L'id lo
    # dicono le due righe di report, che e' l'unico posto in cui un lettore
    # del run puo' verificarlo.
    caricato = re.search(r"documento (\d+) caricato",
                         righe["owner-fixture-A-documento"][1])
    assert caricato, righe["owner-fixture-A-documento"][1]
    assert f"documento {caricato.group(1)} -> 200" in righe["OWNER_PORTAL-download-A"][1], \
        righe["OWNER_PORTAL-download-A"]


def test_99d_the_refusal_of_the_url_document_is_certified_not_suffered(monkeypatch):
    """Il 422 diventa una prova, invece di un BLOCKED perpetuo.

    La richiesta si fa lo stesso - il documento via URL e' una riga vera che il
    cleanup deve rimuovere - e il rifiuto atteso viene verificato.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    righe = {i: (k, t) for k, i, t in report.rows}
    k, t = righe["owner-fixture-A-url-non-condivisibile"]
    assert k == cert.PASS, (k, t)
    assert "422 come da regola" in t, t
    # E IL CORPO C'E'. E' cio' che ai due run e' mancato.
    assert "storage privato" in t, t


def test_99e_a_double_that_accepted_it_would_be_caught(monkeypatch):
    """MUTAZIONE SUL DOPPIO: se il backend accettasse, il run lo direbbe.

    Un doppio permissivo e' esattamente cio' che ha reso verde, in locale, una
    fixture che sul TEST prendeva 422. Qui si rimette quella permissivita' e si
    pretende che la matrice se ne accorga.
    """
    originale = FakeHttp._owner_admin

    def permissivo(self, method, path, jar, agency, payload):
        risposta = originale(self, method, path, jar, agency, payload)
        tail = path[len("/api/owner/admin"):].split("?")[0]
        if tail == "/documents" and method == "POST" and risposta.status == 422:
            import json as _j
            self.next_id += 1
            return self._reply(method, path, 201,
                               _j.dumps({"id": self.next_id}).encode())
        return risposta

    monkeypatch.setattr(FakeHttp, "_owner_admin", permissivo)
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "owner-fixture-A-url-non-condivisibile" in fallimenti, \
        [r[1] for r in report.rows]
    assert "ACCETTATA" in fallimenti["owner-fixture-A-url-non-condivisibile"]


def test_99f_an_unexpected_status_is_a_failure_with_the_body(monkeypatch):
    """Ne' 422 ne' 201: si fallisce, e si stampa cosa ha risposto il server."""
    originale = FakeHttp._owner_admin

    def rotto(self, method, path, jar, agency, payload):
        tail = path[len("/api/owner/admin"):].split("?")[0]
        if tail == "/documents" and method == "POST":
            return self._reply(method, path, 500, b'{"detail":"boom interno"}')
        return originale(self, method, path, jar, agency, payload)

    monkeypatch.setattr(FakeHttp, "_owner_admin", rotto)
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    testo = fallimenti["owner-fixture-A-url-non-condivisibile"]
    assert "rifiuto atteso 422, ottenuto 500" in testo, testo
    assert "boom interno" in testo, testo


def test_99g_the_body_is_reported_whole_then_trimmed():
    """`_corpo` riporta il corpo per intero, e lo tronca dicendo quanto manca.

    Un corpo vuoto viene dichiarato tale: "nessun corpo" e "non ho guardato"
    sono due cose diverse, e il run 58aa0e189aaa non permetteva di
    distinguerle.
    """
    class R:
        def __init__(self, t): self._t = t
        def text(self): return self._t

    assert "corpo vuoto" in cert._corpo(R(""))
    assert cert._corpo(R('{"detail":"x"}')) == ' [corpo: {"detail":"x"}]'
    # Gli a-capo non spezzano la riga di report.
    assert "\n" not in cert._corpo(R("a\nb"))
    # Riempitivo fatto di PAROLE, non di una stringa unica: un blocco di
    # seicento caratteri senza spazi e' una stringa opaca, la redazione lo
    # toglie per intero e il troncamento non avrebbe nulla da troncare -
    # provando il tetto su un corpo che non gli arriva mai.
    testo = ("motivo " * 200).strip()
    assert len(testo) > cert.CORPO_MAX
    lungo = cert._corpo(R(testo))
    assert f"+{len(testo) - cert.CORPO_MAX} caratteri" in lungo, lungo
    assert len(lungo) < cert.CORPO_MAX + 80

    class Rotto:
        def text(self): raise UnicodeError("bang")

    assert "illeggibile" in cert._corpo(Rotto())


def test_99h_the_url_document_is_still_created_and_still_cleaned(monkeypatch):
    """Il documento via URL resta una riga del run, e il cleanup la rimuove.

    Trasformare il passo in una prova del rifiuto non deve far sparire la
    riga dal perimetro: sarebbe un residuo in piu' a ogni run.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    esiti = {i: k for k, i, _t in report.rows}
    assert esiti.get("CLEAN-ORFANE") == cert.PASS, \
        [r for r in report.rows if r[1] == "CLEAN-ORFANE"]
    orfane = next(t for k, i, t in report.rows if i == "CLEAN-ORFANE")
    assert "property_documents=" in orfane, orfane
    assert esiti.get("CLEAN-VERIFICA") == cert.PASS, \
        [r for r in report.rows if r[1] == "CLEAN-VERIFICA"]


def test_99i_the_body_keeps_the_diagnosis_and_drops_the_secrets():
    """La redazione e' mirata: toglie i segreti, lascia il motivo.

    `_corpo` esiste perche' due run non hanno potuto dire PERCHE'. Una
    redazione troppo larga - troncare, o riassumere per prudenza - ricreerebbe
    quel difetto invece di curarlo, quindi la prova chiede le due cose
    insieme: il messaggio per intero, e niente di cio' che non deve uscire.
    """
    class R:
        def __init__(self, t): self._t = t
        def text(self): return self._t

    # IL MOTIVO PASSA INTATTO: e' il caso reale del 422.
    motivo = '{"detail":"Il documento deve essere disponibile in storage privato"}'
    assert cert._corpo(R(motivo)) == f" [corpo: {motivo}]"
    # E anche la forma strutturata di FastAPI, che nomina il campo.
    validazione = ('{"detail":[{"loc":["body","public_document_type"],'
                   '"msg":"unexpected value"}]}')
    assert "public_document_type" in cert._corpo(R(validazione))

    casi = {
        # URL firmata: restano schema e host, sparisce tutto il resto.
        '{"url":"https://bucket.s3.amazonaws.com/docs/k?X-Amz-Signature=abc"}': (
            ["https://bucket.s3.amazonaws.com", "percorso rimosso"],
            ["X-Amz-Signature", "/docs/", "abc"]),
        # Token di sessione.
        '{"session_token":"eyJhbGciOiJIUzI1NiJ9.payload.firma"}': (
            ["session_token", "segreto rimosso"], ["eyJhbGciOiJIUzI1NiJ9"]),
        # Chiave di storage: identifica un oggetto nel bucket.
        '{"storage_key":"owner/2026/09/9f8e7d6c5b4a39281706fedcba098765"}': (
            ["storage_key", "segreto rimosso"], ["9f8e7d6c5b4a39281706fedcba098765"]),
        # Dati personali di un contatto.
        '{"nome":"Mario","cognome":"Rossi","telefono":"+39 333 1234567"}': (
            ["dato personale rimosso"], ["Mario", "Rossi", "333 1234567"]),
        # Un indirizzo email in mezzo a una frase, fuori da ogni JSON.
        'scrivere a mario.rossi@example.com': (
            ["email rimossa", "scrivere a"], ["mario.rossi", "example.com"]),
        # Una stringa opaca senza un campo che la nomini.
        '{"x":"9f8e7d6c5b4a39281706fedcba0987654321abcd"}': (
            ["stringa opaca rimossa"], ["9f8e7d6c5b4a39281706fedcba0987654321abcd"]),
    }
    for corpo, (attesi, vietati) in casi.items():
        reso = cert._corpo(R(corpo))
        for frammento in attesi:
            assert frammento in reso, (corpo, frammento, reso)
        for frammento in vietati:
            assert frammento not in reso, (corpo, frammento, reso)


def test_99j_the_redaction_runs_before_the_length_cap():
    """Il tetto non deve poter fare da redazione, ne' la redazione da tetto.

    Se il taglio venisse prima, un segreto oltre il seicentesimo carattere
    sarebbe "protetto" solo dalla lunghezza - cioe' non protetto - e un corpo
    corto lo stamperebbe per intero.
    """
    class R:
        def __init__(self, t): self._t = t
        def text(self): return self._t

    # IL SEGRETO STA A CAVALLO DEL TETTO, e non e' un dettaglio: e' l'unica
    # posizione in cui i due ordini danno risultati diversi.
    #
    # Un primo tentativo metteva il segreto DOPO il seicentesimo carattere, e
    # il test passava con la redazione spostata dopo il taglio - cioe' non
    # provava niente: il segreto spariva perche' troncato, non perche' redatto.
    # Qui invece il taglio cadrebbe DENTRO il valore, lasciando un frammento
    # che nessuna regola riconosce piu': la coppia `"token": "..."` non ha piu'
    # la virgoletta di chiusura, e dieci caratteri non bastano alla regola
    # sulle stringhe opache. Tagliare prima significa stampare quel frammento.
    segreto = "S" * 40
    prefisso = ("motivo " * 200)[:cert.CORPO_MAX - 40]
    lungo = prefisso + '{"token":"' + segreto + '"}'
    # Sopra il tetto PRIMA della redazione: e' questo che manda in funzione il
    # taglio nell'ordine sbagliato.
    assert len(lungo) > cert.CORPO_MAX

    reso = cert._corpo(R(lungo))
    assert segreto not in reso, reso
    assert "S" * 8 not in reso, reso          # nemmeno un frammento
    assert "segreto rimosso" in reso, reso
    assert "motivo" in reso, reso             # e il resto del corpo c'e' ancora
    # E sotto il tetto DOPO: la redazione lo ha accorciato abbastanza che il
    # troncamento non serva piu'. Se questa riga fosse falsa, il marcatore
    # stesso sarebbe tagliato a meta' e il report direbbe meno di quanto puo'.
    assert "caratteri)" not in reso, reso

    # E l'ordine e' quello dichiarato: la firma dentro la query sparisce con
    # l'URL, prima che la regola sulle stringhe opache la incontri.
    firmato = ('{"u":"https://h/p?sig=' + "b" * 40 + '"}')
    assert "percorso rimosso" in cert._corpo(R(firmato))
    assert "b" * 40 not in cert._corpo(R(firmato))


# ===========================================================================
# 100 - LA PROVA CHE CHIUDE IL CICLO
#
# Quattro run di fila hanno seguito la stessa traiettoria: si corregge una
# funzione, la funzione ricomincia a scrivere, le sue righe non sono nel
# perimetro, il preflight le chiama estranee e blocca tutto.
#
#   52f6d97b5214  otto figli delle fixture
#   58aa0e189aaa  property_watch_observations (initialize scrive la baseline)
#   42e32975ccd6  owner_notifications, owner_document_reads
#
# Ogni volta la correzione e' arrivata DOPO un run live speso a scoprirla. Qui
# la domanda si fa prima e su TUTTE le tabelle che il codice applicativo puo'
# scrivere: ognuna deve avere una collocazione dichiarata, e la dichiarazione
# deve essere verificabile - non una promessa.
#
# Tre collocazioni, in ordine di forza:
#
#   perimetro     la matrice la scrive, e il cleanup la raccoglie. Deve
#                 comparire in una delle strutture del perimetro.
#   guardia       la matrice non la scrive, ma HA una FK verso una tabella che
#                 il cleanup cancella: se un giorno comparisse, il preflight la
#                 vedrebbe e bloccherebbe invece di cancellarla in silenzio.
#                 Si verifica sulle migration, non si promette.
#   fuori-portata nessuna FK verso il perimetro: non puo' ne' bloccare ne'
#                 essere travolta.
#
# Una tabella nuova, o un INSERT nuovo in un percorso esistente, rende rosso
# questo file prima che un run live lo scopra.
# ===========================================================================

PACCHETTI_APPLICATIVI = (
    "core", "property", "buy", "match", "proposal", "sale", "crm", "flow",
    "owner", "followup", "seller_intelligence", "seller_intent",
    "property_watch", "next_best_action", "operator_auth", "database_revival",
)

PERIMETRO, GUARDIA, FUORI = "perimetro", "guardia", "fuori-portata"

#: tabella -> (collocazione, motivo). Il motivo e' obbligatorio e viene
#: controllato: una riga senza spiegazione e' una decisione non presa.
COLLOCAZIONE_SCRITTURE = {
    # -- il perimetro: la matrice le scrive e il cleanup le raccoglie --------
    "contacts": (PERIMETRO, "fixture CORE, piu' il contatto di ciascuna agenzia dedicata"),
    "properties": (PERIMETRO, "fixture PROPERTY, una per agenzia"),
    "property_contacts": (PERIMETRO, "PROPERTY-relazione collega contatto e immobile"),
    "property_documents": (PERIMETRO, "upload OWNER crea il documento dell'immobile"),
    "property_status_history": (PERIMETRO, "la vendita cambia lo stato dell'immobile"),
    "buy_requests": (PERIMETRO, "fixture BUY, una richiesta per agenzia"),
    "buy_request_history": (PERIMETRO, "storico scritto dalla creazione della richiesta"),
    "matches": (PERIMETRO, "catena MATCH calcolata dalla coppia del run"),
    "match_runs": (PERIMETRO, "il calcolo del match registra la propria esecuzione"),
    "match_requirement_results": (PERIMETRO, "un risultato per criterio per ogni match_run"),
    "property_proposals": (PERIMETRO, "catena PROPOSAL, costruita sul match del run"),
    "property_sales": (PERIMETRO, "catena SALE, costruita sulla proposta del run"),
    "property_sale_sellers": (PERIMETRO, "il legame venditore nasce con la vendita"),
    "seller_timeline_events": (PERIMETRO,
                               "fixture SELLER_INTELLIGENCE e evento stima_completata"),
    "tasks": (PERIMETRO, "fixture FOLLOWUP nelle agenzie dedicate"),
    "followup_actions": (PERIMETRO, "la scansione FOLLOWUP persiste l'azione"),
    "flow_events": (PERIMETRO, "fixture FLOW: un evento per agenzia dedicata"),
    "flow_executions": (PERIMETRO, "process_event valuta le regole e puo' eseguirle"),
    "flow_action_records": (PERIMETRO,
                            "figlia dell'esecuzione, senza agency_id: OWNED_BY_PARENT"),
    "flow_suppressions": (PERIMETRO,
                          "la valutazione puo' sopprimere; agency_id RESTRICT verso agencies"),
    "property_watches": (PERIMETRO, "fixture PROPERTY_WATCH nelle agenzie dedicate"),
    "property_watch_observations": (PERIMETRO,
                                    "initialize scrive la baseline insieme al watch"),
    "next_best_actions": (PERIMETRO, "il refresh materializza le azioni"),
    "owner_accounts": (PERIMETRO, "fixture OWNER: un conto proprietario per agenzia"),
    "owner_property_access": (PERIMETRO, "la concessione del titolare sul proprio immobile"),
    "owner_access_tokens": (PERIMETRO, "il token monouso con cui il portale autentica"),
    "owner_sessions": (PERIMETRO, "la sessione del proprietario nel portale"),
    "owner_audit_log": (PERIMETRO, "ogni operazione OWNER lascia una traccia"),
    "owner_shared_documents": (PERIMETRO, "la condivisione creata dall'upload"),
    "owner_notifications": (PERIMETRO, "la pubblicazione notifica il titolare"),
    "owner_document_reads": (PERIMETRO, "lo scaricamento registra la lettura"),
    "operator_sessions": (PERIMETRO, "il login delle identita' create dal run"),
    "seller_revival_suppressions": (PERIMETRO,
                                    "figlia CASCADE del contatto, dichiarata in CHILD_FOREIGN_KEYS"),

    # -- la guardia: non scritte dalla matrice, ma il preflight le vedrebbe --
    "contact_roles": (GUARDIA, "solo POST /core/contacts/{id}/roles, che la matrice non chiama"),
    "leads": (PERIMETRO,
              "la fixture NEXT_BEST_ACTION crea un lead con azione scaduta: e' il segnale da cui nasce l'azione"),
    "lead_stime": (GUARDIA, "ponte lead-stima, scritto dal funnel pubblico"),
    "property_leads": (GUARDIA, "nessuna fixture collega lead a immobili"),
    "property_price_history": (GUARDIA, "solo su cambio prezzo: la matrice non aggiorna i propri immobili"),
    "property_visits": (GUARDIA, "nessuna fixture fissa visite"),
    "buy_request_interactions": (GUARDIA, "nessuna fixture registra interazioni"),
    "buy_request_task_links": (GUARDIA, "nessuna fixture collega attivita' alle richieste"),
    "match_exclusions": (GUARDIA, "nessuna fixture esclude abbinamenti"),
    "match_feedback": (GUARDIA, "nessuna fixture lascia riscontri"),
    "match_refresh_history": (GUARDIA, "scritta dal refresh, non dal calcolo"),
    "invisible_sale_candidates": (GUARDIA, "il motore invisible-sale non viene avviato"),
    "invisible_sale_events": (FUORI, "pende da invisible_sale_opportunities, che il cleanup non cancella mai: una opportunita' presente BLOCCA, quindi i suoi eventi non possono essere travolti"),
    "invisible_sale_opportunities": (PERIMETRO,
                                     "figlia RESTRICT del watch, dichiarata in CHILD_FOREIGN_KEYS: il cleanup la conta prima di cancellare"),
    "owner_feedback": (GUARDIA, "il portale non invia riscontri in questa matrice"),
    # SCOPERTE DAL NOME DINAMICO: `create_child`/`add_child` compongono
    # `INSERT INTO {table}` e il censimento testuale non le vedeva. I nomi si
    # risolvono ai letterali dei chiamanti, vedi NOMI_DINAMICI.
    "property_photos": (GUARDIA, "add_photo non e' chiamato dalla matrice; referenzia properties"),
    "buy_request_locations": (GUARDIA, "nessuna fixture aggiunge criteri di zona alla richiesta"),
    "buy_request_typologies": (GUARDIA, "nessuna fixture aggiunge tipologie alla richiesta"),
    "buy_request_features": (GUARDIA, "nessuna fixture aggiunge caratteristiche alla richiesta"),
    "owner_publications": (GUARDIA, "nessuna fixture pubblica avanzamenti"),
    "owner_publication_reads": (GUARDIA, "nessuna pubblicazione da leggere"),
    "owner_visit_feedback_publications": (GUARDIA, "nessuna visita, quindi nessun riscontro"),

    # -- fuori portata ------------------------------------------------------
    "activities": (GUARDIA, "POST /core/activities non e' chiamato; referenzia contacts e stime, quindi il preflight la vedrebbe"),
    "flow_rules": (FUORI, "catalogo di regole, globale: nessuna FK verso il perimetro"),
    "owner_notification_preferences": (GUARDIA,
                                       "preferenze per conto, mai scritte dalla matrice; referenzia owner_accounts"),
}


#: I nomi di tabella che il codice COMPONE invece di scriverli.
#:
#: `INSERT INTO {table}` non e' leggibile da un censimento testuale: e' il
#: primo dei limiti dichiarati in `test_103d`, e non e' teorico - nasconde
#: quattro tabelle. Qui i nomi si risolvono ai LETTERALI che i chiamanti
#: passano, e `test_103e` li ri-deriva dai sorgenti perche' questa mappa non
#: possa restare indietro.
NOMI_DINAMICI = {
    "property/service.py": ("property_documents", "property_photos"),
    "buy/service.py": ("buy_request_locations", "buy_request_typologies",
                       "buy_request_features"),
}


def _tabelle_da_nomi_dinamici() -> dict[str, set[str]]:
    """I letterali passati alle funzioni che compongono il nome."""
    trovate: dict[str, set[str]] = {}
    for sorgente, _attese in NOMI_DINAMICI.items():
        testo = (ROOT / sorgente).read_text(encoding="utf-8")
        for chiamata in re.finditer(
                r"\b(?:create_child|add_child|add_child_scoped)\s*\(([^)]*)\)", testo):
            for letterale in re.findall(r"['\"]([a-z_]+)['\"]", chiamata.group(1)):
                trovate.setdefault(letterale, set()).add(sorgente)
    return trovate


def _tabelle_scritte_dal_codice() -> dict[str, set[str]]:
    """{tabella: file che la inseriscono}, dai sorgenti applicativi."""
    per_tabella: dict[str, set[str]] = {}
    for pacchetto in PACCHETTI_APPLICATIVI:
        cartella = ROOT / pacchetto
        if not cartella.is_dir():
            continue
        for percorso in sorted(cartella.rglob("*.py")):
            testo = percorso.read_text(encoding="utf-8", errors="replace")
            for trovato in re.finditer(
                    r"INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", testo, re.I):
                nome = trovato.group(1).lower()
                if not re.fullmatch(r"[a-z_][a-z0-9_]*", nome):
                    continue          # nome composto: risolto qui sotto
                per_tabella.setdefault(nome, set()).add(percorso.name)
    for tabella, sorgenti in _tabelle_da_nomi_dinamici().items():
        per_tabella.setdefault(tabella, set()).update(sorgenti)
    return per_tabella


def _fk_fra_tabelle() -> dict[str, set[str]]:
    """{figlia: tabelle referenziate}, dalle migration e da database.py."""
    testi = [(ROOT / "database.py").read_text(encoding="utf-8")]
    for percorso in sorted((ROOT / "migrations").glob("*.sql")):
        if not percorso.name.endswith("_down.sql"):
            testi.append(percorso.read_text(encoding="utf-8"))

    riferimenti: dict[str, set[str]] = {}
    for testo in testi:
        # Scansione a parentesi BILANCIATE, non un `.*?\n);`. Le tabelle di
        # 009 sono scritte su una riga sola e i loro CHECK contengono `))`:
        # una regex non bilanciata le troncava, perdeva le REFERENCES e
        # dichiarava "nessuna FK" su tabelle che ne hanno tre. La
        # classificazione ne usciva sbagliata nel verso peggiore - una
        # dipendenza vera etichettata come innocua.
        for apertura in re.finditer(
                r"CREATE TABLE (?:IF NOT EXISTS )?([a-z_]+)\s*\(", testo, re.I):
            figlia = apertura.group(1).lower()
            profondita, inizio = 0, apertura.end() - 1
            for posizione in range(inizio, len(testo)):
                if testo[posizione] == "(":
                    profondita += 1
                elif testo[posizione] == ")":
                    profondita -= 1
                    if profondita == 0:
                        corpo = testo[inizio + 1:posizione]
                        break
            else:
                continue
            for genitore in re.findall(r"REFERENCES\s+([a-z_]+)", corpo, re.I):
                riferimenti.setdefault(figlia, set()).add(genitore.lower())
        for blocco in re.finditer(
                r"ALTER TABLE\s+(?:public\.)?([a-z_]+)(.*?);", testo, re.S | re.I):
            figlia = blocco.group(1).lower()
            for genitore in re.findall(r"REFERENCES\s+([a-z_]+)",
                                       blocco.group(2), re.I):
                riferimenti.setdefault(figlia, set()).add(genitore.lower())
    return riferimenti


def _strutture_del_perimetro() -> set[str]:
    """Ogni tabella che una struttura del cleanup nomina."""
    C = cert.Certification
    dentro = set(C.CLEANUP_PARENTS)
    dentro |= {t for t, _c in C.EFFECT_TABLES}
    dentro |= set(C.EFFECT_BY_ID_TABLES)
    dentro |= {t for t, _c in C.DEDICATED_TABLES}
    dentro |= set(C.DEDICATED_SNAPSHOT_TABLES)
    dentro |= set(C.DEDICATED_EFFECT_TABLES)
    dentro |= {fk.table for fk in C.CHILD_FOREIGN_KEYS}
    dentro |= {fk.table for fk in C.EFFECT_FOREIGN_KEYS}
    dentro |= {t for t, _c, _g in C.OWNED_BY_PARENT}
    dentro |= {t for _a, t in C.TRACKED_ID_TABLES}
    return dentro


def test_100_ogni_tabella_scritta_dal_codice_ha_una_collocazione():
    """Nessun INSERT applicativo senza una decisione presa su di lui."""
    scritte = set(_tabelle_scritte_dal_codice())
    dichiarate = set(COLLOCAZIONE_SCRITTURE)

    mancanti = sorted(scritte - dichiarate)
    assert not mancanti, (
        f"tabelle che il codice scrive e che nessuno ha collocato: {mancanti}. "
        "E' la forma esatta dei blocchi di 52f6d97b5214, 58aa0e189aaa e "
        "42e32975ccd6: decidere adesso costa meno di un run live.")
    obsolete = sorted(dichiarate - scritte)
    assert not obsolete, (
        f"collocazioni per tabelle che nessuno scrive piu': {obsolete}")

    for tabella, (dove, motivo) in COLLOCAZIONE_SCRITTURE.items():
        assert dove in (PERIMETRO, GUARDIA, FUORI), (tabella, dove)
        assert len(motivo) > 20, (tabella, motivo)


def test_100b_le_tabelle_del_perimetro_sono_davvero_nel_perimetro():
    """"perimetro" e' una collocazione, non un'etichetta."""
    dentro = _strutture_del_perimetro()
    fuori_posto = sorted(
        t for t, (dove, _m) in COLLOCAZIONE_SCRITTURE.items()
        if dove == PERIMETRO and t not in dentro)
    assert not fuori_posto, (
        f"dichiarate nel perimetro ma nominate da nessuna struttura: {fuori_posto}")

    # E il contrario: una struttura che nomina una tabella non collocata
    # sarebbe un perimetro che nessuno ha esaminato.
    scritte = set(_tabelle_scritte_dal_codice())
    ignote = sorted(
        t for t in dentro
        if t in scritte and COLLOCAZIONE_SCRITTURE.get(t, (None,))[0] != PERIMETRO)
    assert not ignote, (
        f"il cleanup le tocca ma non sono collocate nel perimetro: {ignote}")


def test_100c_le_tabelle_sotto_guardia_hanno_davvero_una_fk_al_perimetro():
    """La promessa "il preflight la vedrebbe" si verifica sulle migration.

    Il preflight interroga il catalogo per ogni tabella di CLEANUP_PARENTS e
    trova le figlie. Una tabella "guardia" senza FK verso quell'insieme non
    sarebbe vista da nessuno: la sua collocazione sarebbe una speranza.
    """
    riferimenti = _fk_fra_tabelle()
    parents = set(cert.Certification.CLEANUP_PARENTS)
    senza_guardia = []
    for tabella, (dove, _motivo) in COLLOCAZIONE_SCRITTURE.items():
        if dove != GUARDIA:
            continue
        if not (riferimenti.get(tabella, set()) & parents):
            senza_guardia.append((tabella, sorted(riferimenti.get(tabella, ()))))
    assert not senza_guardia, (
        "dichiarate sotto guardia ma senza FK verso una tabella che il cleanup "
        f"cancella: {senza_guardia}")


def test_100d_le_tabelle_fuori_portata_non_toccano_il_perimetro():
    """E il contrario: "fuori portata" non deve nascondere una dipendenza."""
    riferimenti = _fk_fra_tabelle()
    parents = set(cert.Certification.CLEANUP_PARENTS)
    sbagliate = []
    for tabella, (dove, _motivo) in COLLOCAZIONE_SCRITTURE.items():
        if dove != FUORI:
            continue
        contatto = riferimenti.get(tabella, set()) & parents
        if contatto:
            sbagliate.append((tabella, sorted(contatto)))
    assert not sbagliate, (
        f"dichiarate fuori portata ma con una FK verso il perimetro: {sbagliate}")


# ===========================================================================
# 101 - il percorso documentale, dal blocco del run 42e32975ccd6
# ===========================================================================

def _perimetro_documentale():
    return {"owner_accounts": [("public.owner_notifications", "owner_account_id"),
                               ("public.owner_document_reads", "owner_account_id")],
            "properties": [("public.owner_notifications", "property_id")],
            "owner_shared_documents": [("public.owner_document_reads",
                                        "shared_document_id")]}


def test_101_il_percorso_documentale_non_blocca_piu_il_cleanup(monkeypatch):
    """IL CASO LIVE 42e32975ccd6: quattro segnalazioni, un blocco, otto FAIL.

    Le righe erano nostre: la pubblicazione notifica il titolare, lo
    scaricamento registra la lettura. Il doppio ora le produce davvero - se
    non lo facesse, questo test sarebbe verde sul vuoto - e il cleanup le
    riconosce, le cancella e lo verifica.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        fk_perimetro=_perimetro_documentale(), dipendenti_estranee=0)

    prodotte = database.state["effetti_righe"]
    assert prodotte.get("owner_notifications"), prodotte
    assert prodotte.get("owner_document_reads"), prodotte

    esiti = {i: k for k, i, _t in report.rows}
    for riga in ("CLEAN-PREFLIGHT", "CLEAN-OWNER", "CLEAN-ORFANE",
                 "CLEAN-DEDICATA", "CLEAN-VERIFICA"):
        assert esiti.get(riga) == cert.PASS, \
            [r for r in report.rows if r[1] == riga]


def test_101b_le_due_tabelle_sono_cancellate_prima_del_conto(monkeypatch):
    """Ordine: letture e notifiche PRIMA di `owner_accounts`.

    Sono CASCADE verso il conto: cancellare il conto per primo se le
    porterebbe via in silenzio, senza che il predicato di appartenenza le
    abbia mai guardate - ed e' proprio cio' che il predicato serve a impedire.
    """
    _code, _report, database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    sql = database.state["sql"]
    letture = [i for i, q in enumerate(sql)
               if q.startswith("DELETE FROM owner_document_reads")]
    notifiche = [i for i, q in enumerate(sql)
                 if q.startswith("DELETE FROM owner_notifications")]
    conti = [i for i, q in enumerate(sql)
             if q.startswith("DELETE FROM owner_accounts")]
    assert letture and notifiche and conti, [q for q in sql if "owner_" in q]
    # LA CANCELLAZIONE PER ID VIENE PRIMA DEL CONTO. La ripassata per
    # predicato di `cleanup_orphan_fixtures` arriva dopo ed e' innocua - a
    # quel punto non c'e' piu' niente - ma non e' lei a proteggere l'ordine,
    # quindi il controllo guarda la forma `WHERE id IN`.
    per_id = [i for i in letture + notifiche if "WHERE id IN" in sql[i]]
    assert per_id, [sql[i] for i in letture + notifiche]
    assert max(per_id) < min(conti), ([sql[i] for i in per_id], conti)
    # E mai per `owner_account_id`: quel criterio prenderebbe anche una riga
    # che lega il nostro conto a un documento di qualcun altro.
    for indice in per_id:
        assert "owner_account_id" not in sql[indice], sql[indice]


def test_101c_una_lettura_di_un_conto_estraneo_blocca_ancora(monkeypatch):
    """RIFERIMENTI MISTI: la guardia non e' stata indebolita.

    Una riga che lega il NOSTRO documento a un conto che non e' nostro non e'
    del run: il predicato la lascia fuori dal perimetro e il preflight la
    dichiara estranea. Se la correzione avesse esentato la TABELLA invece
    delle RIGHE, questo test passerebbe per il motivo sbagliato.
    """
    _code, report, _database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        fk_perimetro=_perimetro_documentale(), dipendenti_estranee=1)
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-PREFLIGHT" in fallimenti, [r[1] for r in report.rows]
    assert "owner_document_reads" in fallimenti["CLEAN-PREFLIGHT"], \
        fallimenti["CLEAN-PREFLIGHT"]
    # E nessuna DELETE e' partita: il blocco e' PRIMA, non a meta' strada.
    for riga in ("CLEAN-CHAIN", "CLEAN-OWNER", "CLEAN-DEDICATA"):
        assert riga in fallimenti, riga


@pytest.mark.parametrize("tabella", ["owner_notifications", "owner_document_reads"])
def test_101d_un_effetto_tolto_dal_tracciamento_torna_a_bloccare(monkeypatch, tabella):
    """MUTAZIONE: la tabella esce da EFFECT_TABLES.

    E' lo stato esatto in cui girava il run 42e32975ccd6. Il preflight torna a
    chiamare estranea una riga che la matrice ha appena prodotto.
    """
    monkeypatch.setattr(
        cert.Certification, "EFFECT_TABLES",
        tuple(e for e in cert.Certification.EFFECT_TABLES if e[0] != tabella))
    _code, report, _database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        fk_perimetro=_perimetro_documentale(), dipendenti_estranee=0)
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-PREFLIGHT" in fallimenti, [r[1] for r in report.rows]
    assert tabella in fallimenti["CLEAN-PREFLIGHT"], fallimenti["CLEAN-PREFLIGHT"]


@pytest.mark.parametrize("tabella", ["owner_notifications", "owner_document_reads"])
def test_101e_una_cancellazione_inefficace_e_un_residuo(monkeypatch, tabella):
    """Se la DELETE non cancella, la verifica finale se ne accorge.

    La mutazione e' sul DOPPIO: la riga resta viva. Senza la verifica per id
    il run si dichiarerebbe pulito.
    """
    _code, report, _database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        delete_inefficace=(tabella,))
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert fallimenti, [r[1] for r in report.rows]
    testo = " ".join(fallimenti.values())
    assert tabella in testo, testo[:400]


def test_101f_flow_action_records_entra_per_genitore(monkeypatch):
    """La figlia trovata CERCANDOLA, non da un run fallito.

    `POST /api/flow/events` valuta le regole e puo' scrivere esecuzioni e
    record di azione. Questi ultimi non hanno `agency_id`: senza
    OWNED_BY_PARENT non sarebbero entrati nel perimetro per nessuna via.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    righe = {i: (k, t) for k, i, t in report.rows}
    assert "CLEAN-FIGLIE-DERIVATE" in righe, [r[1] for r in report.rows]
    assert righe["CLEAN-FIGLIE-DERIVATE"][0] == cert.PASS
    assert "flow_action_records" in righe["CLEAN-FIGLIE-DERIVATE"][1]
    # E l'interrogazione e' stata fatta davvero, sugli id delle esecuzioni.
    assert any(q.startswith("SELECT id") and "FROM flow_action_records" in q
               for q in database.state["sql"]), \
        [q for q in database.state["sql"] if "flow_action" in q]
    # E porta con se' il riferimento LOGICO, che nessuna FK dichiara.
    assert any("target_entity_id" in q for q in database.state["sql"]), \
        [q for q in database.state["sql"] if "flow_action" in q]


def test_101g_un_genitore_non_fotografato_e_un_errore_dichiarato(monkeypatch):
    """OWNED_BY_PARENT non accetta un genitore che nessuno fotografa.

    L'appartenenza "figlia di una riga nostra" vale SOLO perche' il genitore
    vive in un'agenzia creata dal run. Se il genitore non fosse fra quelli
    fotografati, quella prova non esisterebbe e la dichiarazione sarebbe muta.
    """
    monkeypatch.setattr(
        cert.Certification, "OWNED_BY_PARENT",
        (("flow_action_records", "execution_id", "contacts"),))
    monkeypatch.setattr(
        cert.Certification, "DEDICATED_SNAPSHOT_TABLES",
        tuple(t for t in cert.Certification.DEDICATED_SNAPSHOT_TABLES
              if t != "contacts"))
    _code, report, _database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-FIGLIE-DERIVATE" in fallimenti, [r[1] for r in report.rows]
    assert "AssertionError" in fallimenti["CLEAN-FIGLIE-DERIVATE"], \
        fallimenti["CLEAN-FIGLIE-DERIVATE"]
    # E fail-closed: nessuna cancellazione parte.
    for riga in ("CLEAN-CHAIN", "CLEAN-DEDICATA"):
        assert riga in fallimenti, riga


def test_101h_flow_suppressions_e_nella_cancellazione_delle_dedicate():
    """`flow_suppressions` ha agency_id NOT NULL e RESTRICT verso agencies.

    Una riga sola avrebbe fatto fallire `DELETE FROM agencies` e con essa
    l'intera transazione. Non e' stata trovata da un run: e' stata cercata
    leggendo 052 e 054.
    """
    tabelle = [t for t, _c in cert.Certification.DEDICATED_TABLES]
    assert "flow_suppressions" in tabelle, tabelle
    # Prima di `flow_executions`: la sua FK verso `flow_rules` e' CASCADE, ma
    # l'ordine dichiarato deve restare quello delle dipendenze.
    assert tabelle.index("flow_suppressions") < tabelle.index("flow_executions")

    migrazione = (ROOT / "migrations" / "052_p26_flow_agency_columns.sql").read_text(
        encoding="utf-8")
    assert "REFERENCES agencies (id) ON DELETE RESTRICT" in migrazione
    enforce = (ROOT / "migrations" / "054_p26_flow_agency_enforce.sql").read_text(
        encoding="utf-8")
    assert "ALTER TABLE flow_suppressions ALTER COLUMN agency_id SET NOT NULL" in enforce


# ===========================================================================
# 102 - OWNED_BY_PARENT: tre condizioni, non una
# ===========================================================================

def _run_con_figlie_derivate(monkeypatch, righe, fk_figlia=None):
    return working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        figlie_derivate={"flow_action_records": righe},
        fk_figlia=fk_figlia or {})


def test_102_una_figlia_che_punta_solo_al_nostro_genitore_e_nostra(monkeypatch):
    """Il caso semplice: unica FK, genitore in un'agenzia del run."""
    _code, report, _db, probe, _ = _run_con_figlie_derivate(
        monkeypatch, [{"id": 4001, "execution_id": None}])
    # L'esecuzione vera la conosce solo il run: si rilegge dal report.
    riga = next(t for k, i, t in report.rows if i == "CLEAN-FIGLIE-DERIVATE")
    assert "flow_action_records" in riga, riga
    assert "altre FK esaminate: 0" in riga, riga
    # E la condizione operativa e' scritta accanto al criterio che ne dipende.
    assert "platform-wide sospesi" in riga, riga


def test_102b_una_figlia_con_un_secondo_riferimento_estraneo_resta_fuori(monkeypatch):
    """RELAZIONE MISTA: non diventa cancellabile per generalizzazione.

    La riga pende da una NOSTRA esecuzione e punta anche a un contatto che non
    e' nel perimetro. "Figlia di una riga nostra" direbbe di cancellarla; la
    terza condizione dice di no, e il report lo scrive.
    """
    database = fake_database(agencies=AGENCIES, owners=OWNERS, stime=STIME)
    report, _stream = quiet_report()
    certificazione = cert.Certification(database, report)
    certificazione.created_agency_ids = [24]
    certificazione.child_parents = {"flow_executions": (3001,)}
    database.state["fk_figlia"] = {
        "flow_action_records": [("contact_id", "contacts")]}
    database.state["figlie_derivate"] = {"flow_action_records": [
        {"id": 4001, "execution_id": 3001, "contact_id": None},      # nostra
        {"id": 4002, "execution_id": 3001, "contact_id": 999999},    # MISTA
    ]}
    certificazione._snapshot_owned_by_parent()

    assert certificazione.owned_child_ids == {"flow_action_records": (4001,)}, \
        certificazione.owned_child_ids
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-FIGLIE-DERIVATE" in fallimenti, report.rows
    assert "contact_id=999999" in fallimenti["CLEAN-FIGLIE-DERIVATE"], \
        fallimenti["CLEAN-FIGLIE-DERIVATE"]
    assert "mista" in fallimenti["CLEAN-FIGLIE-DERIVATE"].lower()


def test_102c_un_secondo_riferimento_DENTRO_il_perimetro_non_esclude(monkeypatch):
    """La severita' e' sul riferimento ESTRANEO, non sul numero di FK.

    Senza questo test, "escludo tutto cio' che ha una seconda FK" passerebbe
    la prova precedente ed escluderebbe righe che sono nostre: il cleanup
    lascerebbe residui e nessuno se ne accorgerebbe.
    """
    database = fake_database(agencies=AGENCIES, owners=OWNERS, stime=STIME)
    report, _stream = quiet_report()
    certificazione = cert.Certification(database, report)
    certificazione.created_agency_ids = [24]
    certificazione.child_parents = {"flow_executions": (3001,)}
    certificazione.created_rows = {"contacts": [(555, "m", 24)]}
    database.state["fk_figlia"] = {
        "flow_action_records": [("contact_id", "contacts")]}
    database.state["figlie_derivate"] = {"flow_action_records": [
        {"id": 4003, "execution_id": 3001, "contact_id": 555},
    ]}
    certificazione._snapshot_owned_by_parent()
    assert certificazione.owned_child_ids == {"flow_action_records": (4003,)}, \
        certificazione.owned_child_ids
    assert not [r for r in report.rows if r[0] == cert.FAIL], report.rows


def test_102d_le_altre_fk_si_leggono_dal_catalogo_non_da_un_elenco():
    """La query che le cerca esiste, e interroga `pg_constraint` per OID."""
    import inspect
    corpo = inspect.getsource(cert.Certification._altre_chiavi_esterne)
    # Il CODICE, senza la docstring: quella descrive il difetto precedente e
    # ne cita la forma, quindi cercarla nel testo intero direbbe il falso.
    codice = corpo.split('"""')[-1]
    assert "pg_constraint" in corpo
    assert "::regclass" in corpo, corpo
    # L'IDENTITA' RESTA: schema da pg_namespace, mai uno split sul nome.
    assert "pg_namespace" in corpo, corpo
    assert 'split(".")' not in codice, codice
    # E la colonna REFERENZIATA si legge: senza `confkey` il confronto col
    # perimetro varrebbe solo per le FK verso `id`, ma verrebbe fatto lo
    # stesso su tutte.
    assert "confkey" in corpo, corpo
    assert "colonna_riferita" in corpo, corpo
    # Lo schema serve a RIFIUTARE, non a decorare: la tupla restituita non lo
    # porta, perche' a valle sarebbe sempre "public".
    assert 'genitore_schema"] != "public"' in codice, codice
    # Le forme non supportate bloccano invece di essere indovinate.
    assert corpo.count("raise AssertionError") >= 4, corpo
    # Esclude la colonna dichiarata, altrimenti il legame col genitore
    # sembrerebbe un riferimento estraneo a se stesso.
    assert 'riga["colonna"] == esclusa' in corpo, corpo


# ===========================================================================
# 103 - "guardia" non e' "il cleanup e' completo"
#
# La serie 100 dimostra che ogni tabella ha una collocazione e che le
# "guardia" POSSONO bloccare. Non dimostra che gli effetti dei percorsi
# realmente esercitati siano materializzati e rimossi: quella e' una domanda
# osservativa, e si risponde guardando cosa resta dopo il cleanup.
# ===========================================================================

#: Tabelle che i percorsi esercitati dalla matrice DEVONO materializzare nei
#: doppi. Non e' l'elenco delle "perimetro": e' il sottoinsieme che il run
#: produce davvero, e per ognuna il cleanup deve finire a zero.
EFFETTI_OSSERVABILI = (
    "property_documents", "owner_shared_documents", "property_contacts",
    "property_status_history", "buy_request_history", "match_runs",
    "match_requirement_results", "owner_audit_log", "property_sale_sellers",
    "owner_property_access", "owner_access_tokens", "owner_sessions",
    "agency_memberships", "operator_sessions", "followup_actions",
    "owner_notifications", "owner_document_reads",
)


def test_103_gli_effetti_dei_percorsi_esercitati_sono_materializzati(monkeypatch):
    """Prodotti davvero, non dichiarati: un doppio muto non prova nulla."""
    _code, _report, database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    prodotte = database.state["effetti_righe"]
    vuote = [t for t in EFFETTI_OSSERVABILI if not prodotte.get(t)]
    assert not vuote, (
        f"il percorso non ha prodotto righe per {vuote}: la prova che il "
        "cleanup le rimuove sarebbe soddisfatta dal vuoto")


def test_103b_e_il_cleanup_le_rimuove_tutte(monkeypatch):
    """Zero residui, tabella per tabella, sulle righe davvero prodotte."""
    _code, report, database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    vive = database.state["_effetti_vive"]
    # Le righe che il PERCORSO ha prodotto (id >= 77000, vedi
    # `FakeHttp._effetto`) devono essere sparite tutte.
    prodotte_vive = {t: sorted(i for i in ids if i >= 77000)
                     for t, ids in vive.items()}
    prodotte_vive = {t: ids for t, ids in prodotte_vive.items() if ids}
    assert not prodotte_vive, prodotte_vive

    # E LE PREESISTENTI DEVONO RESTARE. `predefiniti` semina anche righe che
    # il run non ha creato: `owner_shared_documents` 6101/6102 appartengono a
    # qualcun altro, e un cleanup che le portasse via sarebbe il danno da cui
    # tutto questo meccanismo difende. Questa riga e' una prova, non una
    # tolleranza.
    assert sorted(vive.get("owner_shared_documents", ())) == [6101, 6102], \
        vive.get("owner_shared_documents")

    esiti = {i: k for k, i, _t in report.rows}
    assert esiti.get("CLEAN-VERIFICA") == cert.PASS, \
        [r for r in report.rows if r[1] == "CLEAN-VERIFICA"]


def test_103c_una_figlia_davvero_estranea_blocca_ancora_prima_delle_delete(monkeypatch):
    """La prova DISTINTA che la serie 103 non deve annacquare.

    Materializzare gli effetti e rimuoverli e' una cosa; accorgersi di una
    riga che NON e' nostra e' l'altra, e la seconda non deve indebolirsi
    perche' la prima e' migliorata. Qui la dipendenza estranea c'e' davvero e
    il cleanup si ferma PRIMA di qualunque DELETE.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        fk_perimetro={"properties": [("public.property_visits", "property_id")]},
        dipendenti_estranee=1)
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-PREFLIGHT" in fallimenti, [r[1] for r in report.rows]
    assert "property_visits" in fallimenti["CLEAN-PREFLIGHT"]
    assert not [q for q in database.state.get("deletes", [])
                if q.upper().startswith("DELETE FROM CONTACTS")], \
        database.state.get("deletes")


def test_103d_i_limiti_del_controllo_statico_sono_dichiarati_e_veri():
    """COSA LA SERIE 100 NON PUO' VEDERE, verificato invece che promesso.

    Il censimento degli INSERT e' testuale: legge `INSERT INTO <nome>` nei
    sorgenti. Tre cose gli sfuggirebbero, e questo test controlla che oggi non
    esistano - cosi' che il giorno in cui compaiono si sappia.

      1. SQL COMPOSTA: un nome di tabella interpolato (`INSERT INTO {t}`,
         f-string o `%s`) non e' leggibile staticamente.
      2. TRIGGER: una riga scritta dal database non passa da nessun sorgente
         Python.
      3. COPY / INSERT ... SELECT verso tabelle non nominate letteralmente.

    Non e' un elenco di scuse: e' il perimetro di validita' della serie 100, e
    va riletto quando una di queste diventa falsa.
    """
    # 1. SQL COMPOSTA. Esiste: `create_child`/`add_child` scrivono
    #    `INSERT INTO {table}`. Non si pretende che sparisca - si pretende che
    #    OGNI file che la contiene abbia una risoluzione dichiarata in
    #    NOMI_DINAMICI, e che quella risoluzione sia ri-derivabile (test_103e).
    #    Senza, quattro tabelle resterebbero invisibili al censimento, come lo
    #    erano fino a questo giro.
    composte = set()
    for pacchetto in PACCHETTI_APPLICATIVI:
        cartella = ROOT / pacchetto
        if not cartella.is_dir():
            continue
        for percorso in sorted(cartella.rglob("*.py")):
            testo = percorso.read_text(encoding="utf-8", errors="replace")
            for riga in testo.splitlines():
                if "cur.execute" not in riga and "INSERT INTO {" not in riga:
                    continue
                for trovato in re.finditer(r"INSERT\s+INTO\s+(\{[a-z_]+\})", riga, re.I):
                    composte.add(f"{pacchetto}/{percorso.name}")
    risolti = {s.split("/")[0] for s in NOMI_DINAMICI}
    senza_risoluzione = sorted(c for c in composte if c.split("/")[0] not in risolti)
    assert not senza_risoluzione, (
        "INSERT con nome di tabella composto e nessuna risoluzione dichiarata: "
        f"{senza_risoluzione}")

    # 2. Nessun trigger applicativo che scriva in una tabella di tenant.
    #    026 scrive `schema_baseline`, che e' la riga della baseline stessa.
    trigger_che_scrivono = []
    for percorso in sorted((ROOT / "migrations").glob("*.sql")):
        if percorso.name.endswith("_down.sql"):
            continue
        testo = percorso.read_text(encoding="utf-8")
        if "CREATE TRIGGER" not in testo.upper():
            continue
        for trovato in re.finditer(r"INSERT\s+INTO\s+([a-z_]+)", testo, re.I):
            tabella = trovato.group(1).lower()
            if tabella != "schema_baseline":
                trigger_che_scrivono.append((percorso.name, tabella))
    assert not trigger_che_scrivono, (
        "una migration con trigger scrive in una tabella di tenant: nessun "
        f"sorgente Python la nomina e la serie 100 non la vedrebbe: "
        f"{trigger_che_scrivono}")

    # 3. Nessuna COPY verso tabelle applicative.
    copy_trovate = []
    for pacchetto in PACCHETTI_APPLICATIVI:
        cartella = ROOT / pacchetto
        if not cartella.is_dir():
            continue
        for percorso in sorted(cartella.rglob("*.py")):
            testo = percorso.read_text(encoding="utf-8", errors="replace")
            if re.search(r"\bCOPY\s+[a-z_]+\s+FROM\b", testo, re.I):
                copy_trovate.append(percorso.name)
    assert not copy_trovate, copy_trovate


def test_103e_la_risoluzione_dei_nomi_dinamici_e_ri_derivata_dai_sorgenti():
    """NOMI_DINAMICI non e' un elenco che invecchia in silenzio.

    I letterali si rileggono dai service a ogni esecuzione del test: un
    `add_child('property_photos_v2', ...)` aggiunto domani compare qui e deve
    essere collocato, esattamente come un `INSERT INTO` scritto per esteso.
    """
    trovati = _tabelle_da_nomi_dinamici()
    for sorgente, attese in NOMI_DINAMICI.items():
        dai_sorgenti = {t for t, files in trovati.items() if sorgente in files}
        assert dai_sorgenti == set(attese), (sorgente, sorted(dai_sorgenti),
                                             sorted(attese))
    # E ognuno di quei nomi ha una collocazione.
    for tabella in trovati:
        assert tabella in COLLOCAZIONE_SCRITTURE, tabella


# ===========================================================================
# 104 - i riferimenti che il catalogo NON vede, e le forme che non si decidono
# ===========================================================================

def _cert_con_esecuzione(database):
    report, _stream = quiet_report()
    certificazione = cert.Certification(database, report)
    certificazione.created_agency_ids = [24]
    certificazione.child_parents = {"flow_executions": (3001,)}
    return certificazione, report


def test_104_il_riferimento_logico_e_quello_reale_dello_schema():
    """`flow_action_records` ha UNA sola FK: l'altro riferimento e' logico.

    IL TEST 102b INVENTAVA UN `contact_id` CHE NON ESISTE. Provava il
    meccanismo su una colonna immaginaria, quindi non diceva nulla sul caso
    che puo' capitare davvero. Lo schema reale (008) dichiara:

        execution_id BIGINT NOT NULL REFERENCES flow_executions(id)
        target_entity_type VARCHAR(50)
        target_entity_id BIGINT

    e `flow/repository.py` scrive `target_entity_type='task'` con l'id del
    task creato dall'azione. Nessuna FK lega quelle due colonne: il catalogo
    non le vede, e una verifica che si fermasse li' direbbe "nessun altro
    riferimento" su una riga che ne ha uno.
    """
    schema = (ROOT / "migrations" / "008_flow_01.sql").read_text(encoding="utf-8")
    blocco = re.search(r"CREATE TABLE flow_action_records \((.*?)\n\);", schema, re.S)
    assert blocco, schema[:200]
    assert blocco.group(1).count("REFERENCES") == 1, blocco.group(1)
    assert "target_entity_type" in blocco.group(1)
    assert "target_entity_id" in blocco.group(1)

    # E il repository scrive davvero quel tipo.
    repo = (ROOT / "flow" / "repository.py").read_text(encoding="utf-8")
    assert "target_entity_type='task'" in repo, "la forma scritta e' cambiata"

    # La dichiarazione dello script corrisponde allo schema reale.
    tipo, ident, mappa = cert.Certification.RIFERIMENTI_LOGICI["flow_action_records"]
    assert (tipo, ident) == ("target_entity_type", "target_entity_id")
    assert mappa == {"task": "tasks"}, mappa


def test_104b_un_target_fuori_perimetro_rende_la_riga_mista():
    """Il caso REALMENTE possibile: l'azione punta a un task non nostro."""
    database = fake_database(agencies=AGENCIES, owners=OWNERS, stime=STIME)
    database.state["fk_figlia"] = {"flow_action_records": []}
    database.state["figlie_derivate"] = {"flow_action_records": [
        {"id": 4001, "execution_id": 3001,
         "target_entity_type": "task", "target_entity_id": 46},      # nostro
        {"id": 4002, "execution_id": 3001,
         "target_entity_type": "task", "target_entity_id": 999999},  # NON nostro
    ]}
    certificazione, report = _cert_con_esecuzione(database)
    certificazione.created_rows = {"tasks": [(46, "m", 24)]}
    certificazione._snapshot_owned_by_parent()

    assert certificazione.owned_child_ids == {"flow_action_records": (4001,)}, \
        certificazione.owned_child_ids
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "CLEAN-FIGLIE-DERIVATE" in fallimenti, report.rows
    assert "target_entity_id=999999" in fallimenti["CLEAN-FIGLIE-DERIVATE"], \
        fallimenti["CLEAN-FIGLIE-DERIVATE"]
    assert "tasks" in fallimenti["CLEAN-FIGLIE-DERIVATE"]


def test_104c_un_tipo_non_classificato_non_viene_ignorato():
    """"Non so a cosa punti" non e' "punta dentro il perimetro".

    Se domani una nuova azione scrivesse `target_entity_type='activity'`, la
    mappa non lo conoscerebbe. Ignorarlo significherebbe cancellare una riga
    che punta a qualcosa di non esaminato.
    """
    database = fake_database(agencies=AGENCIES, owners=OWNERS, stime=STIME)
    database.state["fk_figlia"] = {"flow_action_records": []}
    database.state["figlie_derivate"] = {"flow_action_records": [
        {"id": 4003, "execution_id": 3001,
         "target_entity_type": "activity", "target_entity_id": 77},
    ]}
    certificazione, report = _cert_con_esecuzione(database)
    certificazione._snapshot_owned_by_parent()

    assert certificazione.owned_child_ids == {}, certificazione.owned_child_ids
    fallimenti = {i: t for k, i, t in report.rows if k == cert.FAIL}
    assert "non classificato" in fallimenti["CLEAN-FIGLIE-DERIVATE"], \
        fallimenti["CLEAN-FIGLIE-DERIVATE"]


def test_104d_un_target_nullo_non_e_un_riferimento():
    """`log_only` e `mark_for_review` non creano nulla: target NULL, riga nostra."""
    database = fake_database(agencies=AGENCIES, owners=OWNERS, stime=STIME)
    database.state["fk_figlia"] = {"flow_action_records": []}
    database.state["figlie_derivate"] = {"flow_action_records": [
        {"id": 4004, "execution_id": 3001,
         "target_entity_type": None, "target_entity_id": None},
    ]}
    certificazione, report = _cert_con_esecuzione(database)
    certificazione._snapshot_owned_by_parent()
    assert certificazione.owned_child_ids == {"flow_action_records": (4004,)}
    assert not [r for r in report.rows if r[0] == cert.FAIL], report.rows


@pytest.mark.parametrize("forma,attesa", [
    # (colonna, schema, genitore, colonna_riferita, quante_colonne)
    (("codice", "public", "contacts", "codice", 1), "perimetro conosce solo gli id"),
    (("contact_id", "altro_schema", "contacts", "id", 1), "tutto in public"),
    (("contact_id", "public", "contacts", "id", 2), "composita"),
])
def test_104e_le_forme_non_supportate_bloccano_invece_di_indovinare(forma, attesa):
    """Una FK che il perimetro non sa leggere non si interpreta: si blocca.

    Prima si confrontava il valore della colonna figlia con gli id del
    perimetro comunque: su `REFERENCES t(codice)` avrebbe paragonato un codice
    a degli id e dichiarato estranea ogni riga. Adesso solleva, e il ramo
    fail-closed ferma ogni cancellazione.
    """
    database = fake_database(agencies=AGENCIES, owners=OWNERS, stime=STIME)
    database.state["fk_figlia"] = {"flow_action_records": [forma]}
    database.state["figlie_derivate"] = {"flow_action_records": [
        {"id": 4005, "execution_id": 3001}]}
    certificazione, report = _cert_con_esecuzione(database)
    esito = certificazione._snapshot_owned_by_parent()

    assert esito and "AssertionError" in esito, esito
    assert certificazione.owned_child_ids == {}
    testo = " ".join(t for k, _i, t in report.rows if k == cert.FAIL)
    assert "non fotografabili" in testo, testo


# ===========================================================================
# 105 - le fixture dei BLOCKED: tracciamento e cancellazione insieme
# ===========================================================================

def test_105_le_agenzie_condivise_hanno_una_stima_osservabile(monkeypatch):
    """Stima + valutazione + watch: le tre cose, non solo la prima.

    `GET /api/property-watch/stime/{id}` passa da
    `get_watch_for_stima_scoped`, che senza watch solleva `WatchNotFoundError`
    -> 404. Una stima inserita da sola avrebbe lasciato la lettura propria a
    404 e il confronto ostile senza significato: e' il motivo per cui la
    fixture chiama anche `initialize`.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    righe = {i: (k, t) for k, i, t in report.rows}
    for label in ("A", "B"):
        k, t = righe[f"PROPERTY_WATCH-fixture-{label}"]
        assert k == cert.PASS, (label, k, t)
        assert "watch" in t and "valutazione" in t, t
        assert righe[f"PROPERTY_WATCH-propria-{label}"][0] == cert.PASS, \
            righe[f"PROPERTY_WATCH-propria-{label}"]
    assert righe["PROPERTY_WATCH-ostile-A-B"][0] == cert.PASS
    assert righe["PROPERTY_WATCH-ostile-B-A"][0] == cert.PASS

    # Le tre righe create per agenzia esistono nel doppio del database.
    assert len(database.state["watch_per_stima"]) >= 2, database.state["watch_per_stima"]


def test_105b_e_il_cleanup_le_rimuove_per_id(monkeypatch):
    """Nelle agenzie CONDIVISE l'unico criterio ammesso e' l'id.

    Un `DELETE ... WHERE agency_id = <condivisa>` porterebbe via i dati veri
    di TEST: e' la cosa che questo progetto non deve mai fare.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    righe = {i: (k, t) for k, i, t in report.rows}
    assert righe["CLEAN-CONDIVISE"][0] == cert.PASS, righe["CLEAN-CONDIVISE"]
    assert righe["CLEAN-VERIFICA"][0] == cert.PASS, righe["CLEAN-VERIFICA"]

    cancellazioni = [q for q in database.state["sql"] if q.startswith("DELETE")]
    per_id = [q for q in cancellazioni
              if q.startswith(("DELETE FROM stime WHERE id IN",
                               "DELETE FROM property_watches WHERE id IN",
                               "DELETE FROM seller_timeline_events WHERE id IN"))]
    assert len(per_id) >= 3, cancellazioni
    # E NESSUNA cancellazione per agenzia sulle condivise (1 e 2).
    for query, parametri in database.state["interrogazioni"]:
        if not query.startswith("DELETE") or "agency_id" not in query:
            continue
        valori = parametri[0] if parametri else ()
        if not isinstance(valori, (tuple, list, set)):
            valori = (valori,)
        assert not ({1, 2} & set(valori)), (query, valori)


def test_105c_l_ordine_rispetta_la_restrict_dell_osservazione(monkeypatch):
    """Osservazione prima del watch: `watch_id` e' ON DELETE RESTRICT.

    Invertire l'ordine non fa fallire "in parte": fa cadere l'intera
    transazione del passo.
    """
    _code, _report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    sql = database.state["sql"]
    osservazioni = [i for i, q in enumerate(sql)
                    if q.startswith("DELETE FROM property_watch_observations WHERE id IN")]
    watch = [i for i, q in enumerate(sql)
             if q.startswith("DELETE FROM property_watches WHERE id IN")]
    assert osservazioni and watch, [q for q in sql if "property_watch" in q]
    assert min(osservazioni) < min(watch), (osservazioni, watch)


def test_105d_la_lista_legacy_admin_non_e_piu_vuota(monkeypatch):
    """La stima del run porta il marcatore e cade nel filtro `day=oggi`.

    `/api/admin/stime?day=oggi` filtra `s.agency_id = <chiamante>` e
    `s.data >= oggi`: la riga nasce con `data DEFAULT CURRENT_TIMESTAMP` e il
    marcatore in `comune`, che la proiezione della route restituisce.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    righe = {i: (k, t) for k, i, t in report.rows}
    for label, altro in (("A", "B"), ("B", "A")):
        chiave = f"LEGACY_ADMIN-list-{label}-non-vede-{altro}"
        assert chiave in righe, sorted(righe)
        assert righe[chiave][0] == cert.PASS, righe[chiave]


def test_105e_il_lead_scaduto_e_il_segnale_di_next_best_action(monkeypatch):
    """Il prerequisito letto nei segnali reali, non indovinato.

    `_lead_candidates_from_score` emette `next_action_overdue` quando il lead
    e' aperto e `next_action_at` e' passato. `LeadCreate` esige il solo
    `contact_id`. Senza questo lead il refresh non ha segnali e "0 azioni" e'
    garantito prima ancora di chiamare la route.
    """
    _code, report, _db, probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    righe = {i: (k, t) for k, i, t in report.rows}
    for label in ("C", "D"):
        chiave = f"NEXT_BEST_ACTION-segnale-{label}"
        assert chiave in righe, sorted(k for k in righe if "NEXT_BEST" in k)
        assert righe[chiave][0] == cert.PASS, righe[chiave]
        assert "azione scaduta" in righe[chiave][1]

    # Il lead e' stato creato con una data GIA' passata, non con una futura.
    creati = [p for s, _st, p in probe.exchanges
              if s == "POST /api/core/leads"]
    assert creati, [s for s, _st, _p in probe.exchanges if "leads" in s]


def test_105f_il_lead_e_tracciato_prima_di_essere_usato(monkeypatch):
    """Tracciamento e cancellazione nascono con la fixture, non dopo.

    `leads` entra in `created_effects` (perimetro, preflight, verifica), in
    `DEDICATED_TABLES` prima di `contacts` - la FK e' RESTRICT - e in
    `DEDICATED_SNAPSHOT_TABLES`, senza cui le sue otto figlie SET NULL non
    sarebbero mai interrogate.
    """
    tabelle = [t for t, _c in cert.Certification.DEDICATED_TABLES]
    assert "leads" in tabelle, tabelle
    assert tabelle.index("leads") < tabelle.index("contacts")
    assert "leads" in cert.Certification.DEDICATED_SNAPSHOT_TABLES
    assert "leads" in cert.Certification.CLEANUP_PARENTS

    _code, report, database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    esiti = {i: k for k, i, _t in report.rows}
    assert esiti.get("CLEAN-PREFLIGHT") == cert.PASS, \
        [r for r in report.rows if r[1] == "CLEAN-PREFLIGHT"]
    assert esiti.get("CLEAN-DEDICATA") == cert.PASS, \
        [r for r in report.rows if r[1] == "CLEAN-DEDICATA"]
    # La guardia ha interrogato `leads`: e' nel perimetro, non solo nel codice.
    interrogate = {p[0] for q, p in database.state["interrogazioni"]
                   if "pg_constraint" in q and p}
    assert "leads" in interrogate, sorted(interrogate)


def test_105g_senza_initialize_la_fixture_condivisa_si_dichiara_bloccata(monkeypatch):
    """Se `initialize` non riesce, non si finge che il watch esista.

    E la stima gia' inserita resta tracciata: il cleanup deve poterla
    rimuovere anche quando la fixture si ferma a meta'.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        senza_initialize=True,
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    righe = {i: (k, t) for k, i, t in report.rows}
    assert righe["PROPERTY_WATCH-fixture-A"][0] == cert.BLOCKED, \
        righe["PROPERTY_WATCH-fixture-A"]
    assert "initialize" in righe["PROPERTY_WATCH-fixture-A"][1]
    # La stima inserita prima del fallimento viene comunque cancellata.
    assert any(q.startswith("DELETE FROM stime WHERE id IN")
               for q in database.state["sql"]), \
        [q for q in database.state["sql"] if "stime" in q]
    assert righe["CLEAN-VERIFICA"][0] == cert.PASS, righe["CLEAN-VERIFICA"]


# ---------------------------------------------------------------------------
# 106 - LA FIXTURE FLOW: l'esecuzione esiste, e' propria, e se ne va
#
# `FLOW-list-B-non-vede-A` e' stata BLOCKED in ogni run fino a `42e32975ccd6`
# compreso. Il motivo scritto nel report - "il run crea solo eventi" - era
# esatto ma incompleto: un evento col MARCATORE nel tipo non corrisponde a
# nessuna regola, quindi non fa nascere alcuna esecuzione, e la lista di
# entrambe le agenzie resta vuota.
#
# Questa serie prova tre cose distinte, e nessuna delle tre implica le altre:
#
#   a) l'esecuzione nasce, appartiene all'agenzia giusta, ed e' UNA;
#   b) le prove di isolamento la usano davvero - ogni rottura del doppio le
#      fa fallire, che e' l'unico modo di sapere che non passano per il vuoto;
#   c) le righe create se ne vanno, per id e nell'ordine imposto dal SET NULL.
#
# E una quarta, offline, che regge tutte le altre: che l'esito della regola
# sia DETERMINATO, chiesto al motore vero e non a un doppio.
# ---------------------------------------------------------------------------

def _esiti(report) -> dict:
    return {i: (k, t) for k, i, t in report.rows}


def test_106a_r004_su_una_richiesta_senza_prossima_azione_non_corrisponde():
    """IL FONDAMENTO, chiesto a `flow/engine.py` e non a un doppio.

    La fixture sceglie il ramo non corrispondente perche' e' l'unico i cui
    effetti siano uno solo. Se `evaluate` un giorno facesse corrispondere una
    richiesta senza `next_action_at`, la fixture scriverebbe in un'agenzia
    CONDIVISA un record d'azione e un task che nessun `DELETE ... WHERE
    agency_id` rimuove - e nessun altro test qui dentro se ne accorgerebbe.

    Il parametro non entra nel confronto: si prova su TUTTI gli estremi che lo
    schema della regola ammette, cosi' l'affermazione "qualunque sia la
    configurazione di TEST" e' verificata e non asserita.
    """
    from flow.engine import evaluate
    from flow.rules.registry import RULES

    regola = RULES[cert.Certification.FLOW_EXECUTION_RULE]
    assert regola.event_type == cert.Certification.FLOW_EXECUTION_TRIGGER
    assert regola.entity_type == cert.Certification.FLOW_EXECUTION_ENTITY

    limiti = regola.allowed_parameters["overdue_hours"]
    for ore in (limiti["min"], limiti["max"], regola.default_parameters["overdue_hours"]):
        parametri = dict(regola.default_parameters) | {"overdue_hours": ore}
        corrisponde, _motivi = evaluate(
            cert.Certification.FLOW_EXECUTION_RULE,
            {"entity_type": "buy_request", "entity_id": 1,
             "status": "active", "next_action_at": None},
            parametri)
        assert corrisponde is False, ore


def test_106b_la_fixture_buy_non_valorizza_la_prossima_azione():
    """La precondizione che rende vero il test precedente, sul payload reale.

    Se qualcuno aggiungesse `next_action_at` alla fixture BUY - per una
    ragione che con FLOW non c'entra - la regola comincerebbe a corrispondere.
    La matrice non scriverebbe comunque nulla, perche' `certify_flow` rilegge
    il valore dal database e si ferma; questo test fa notare il cambiamento
    prima, invece di lasciarlo scoprire da un BLOCKED sul TEST.
    """
    buy = next(d for d in cert.DOMAINS if d.name == "BUY")
    _percorso, modello = buy.fixture
    assert "next_action_at" not in modello, modello
    assert modello["status"] == "active"


def test_106c_flow_non_passa_dal_certificatore_generico():
    """Il marcatore non puo' comparire in un'esecuzione: la prova sarebbe vacua.

    `certify_generic` conclude "la lista di A non contiene il marcatore di B".
    Su `flow_executions` quella frase e' vera anche se la lista di A contiene
    per intero le esecuzioni di B - nessun campo proiettato porta testo scelto
    da chi crea la risorsa. Un dominio che usasse quella prova comparirebbe
    fra i PASS senza che una sola riga sia stata confrontata.
    """
    flow = next(d for d in cert.DOMAINS if d.name == "FLOW")
    assert flow.certifier == "flow"
    assert callable(getattr(cert, "certify_flow"))
    # E il certificatore non delega le domande 3-4 a quello generico. Il
    # confronto e' sul CODICE, non sul sorgente: la docstring qui sopra nomina
    # `certify_generic` per spiegare perche' non lo usa, e un controllo sul
    # testo grezzo fallirebbe proprio per averlo spiegato.
    import inspect
    albero = ast.parse(textwrap.dedent(inspect.getsource(cert.certify_flow)))
    chiamate = {n.func.id for n in ast.walk(albero)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "certify_generic" not in chiamate, sorted(chiamate)


def test_106d_la_fixture_crea_una_esecuzione_per_agenzia_condivisa(monkeypatch):
    """Nasce, e' una, ed e' dell'agenzia di chi ha inviato l'evento."""
    _code, report, database, probe, _ = working_run(monkeypatch)
    esiti = _esiti(report)

    for label in ("A", "B"):
        # `report.note` registra un PASS: la riga esiste e non e' un BLOCKED.
        assert esiti[f"FLOW-fixture-{label}"][0] == cert.PASS, esiti[f"FLOW-fixture-{label}"]
        assert esiti[f"FLOW-appartenenza-{label}"][0] == cert.PASS
        assert esiti[f"FLOW-regola-{label}"][0] == cert.PASS
        assert esiti[f"FLOW-list-{label}-vede-la-propria"][0] == cert.PASS
    for label, altro in (("A", "B"), ("B", "A")):
        assert esiti[f"FLOW-list-{label}-non-vede-{altro}"][0] == cert.PASS
        assert esiti[f"FLOW-dettaglio-{label}-{altro}"][0] == cert.PASS
        assert esiti[f"FLOW-retry-ostile-{label}-{altro}"][0] == cert.PASS
    assert esiti["FLOW-effetti"][0] == cert.PASS, esiti["FLOW-effetti"]

    # DUE esecuzioni in tutto, una per agenzia: non una per evento inviato.
    # Si contano sugli id che la cancellazione ha ricevuto, perche' a fine run
    # le righe non ci sono piu' - ed e' proprio cio' che si vuole.
    cancellate = [p for q, p in database.state["interrogazioni"]
                  if q.startswith("DELETE FROM flow_executions WHERE id IN")]
    assert len(cancellate) == 1, cancellate
    assert len(set(cancellate[0][0])) == 2, cancellate


@pytest.mark.parametrize("guasto,rotto", [
    ("flow_leak", "FLOW-list-A-non-vede-B"),
    ("flow_hide_own", "FLOW-list-A-vede-la-propria"),
])
def test_106e_ogni_rottura_del_doppio_fa_fallire_la_prova(monkeypatch, guasto, rotto):
    """LE PROVE NON PASSANO PER IL VUOTO.

    `flow_leak` fa vedere a ciascuno le esecuzioni dell'altro; `flow_hide_own`
    nasconde le proprie, che e' il modo in cui "non vedo il tuo" diventa vero
    per la ragione sbagliata. La matrice deve fallire in entrambi i casi, e
    sulla riga giusta.
    """
    _code, report, _database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(broken={guasto}, only="generic"))
    fallite = [i for k, i, _t in report.rows if k == cert.FAIL]
    assert rotto in fallite, fallite


def test_106f_una_prossima_azione_gia_valorizzata_blocca_invece_di_scrivere(monkeypatch):
    """La precondizione letta sul dato: se non regge, non si invia l'evento.

    E' il caso in cui la fixture BUY cambiasse: R004 corrisponderebbe e
    scriverebbe in un'agenzia condivisa righe che questo cleanup non rimuove.
    Il risultato dev'essere un BLOCKED spiegato e ZERO eventi inviati.
    """
    import datetime
    _code, report, database, probe, _ = working_run(
        monkeypatch,
        buy_next_action=datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc))
    esiti = _esiti(report)
    for label in ("A", "B"):
        assert esiti[f"FLOW-fixture-{label}"][0] == cert.BLOCKED
        assert "prossima azione" in esiti[f"FLOW-fixture-{label}"][1]
    assert esiti["FLOW-list-A-non-vede-B"][0] == cert.BLOCKED
    # Nessuna esecuzione creata: la fixture non ha inviato nulla.
    assert database.state["flow_esecuzioni"] == {}, database.state["flow_esecuzioni"]


def test_106g_le_righe_condivise_se_ne_vanno_per_id_e_nell_ordine(monkeypatch):
    """Cancellate, verificate, e l'esecuzione PRIMA dell'evento.

    `flow_executions.event_id` e' SET NULL: cancellare l'evento per primo non
    fallirebbe, azzererebbe la colonna e lascerebbe l'esecuzione sul TEST come
    riga non piu' attribuibile. L'ordine e' quindi parte della correttezza, non
    dello stile.
    """
    _code, report, database, _probe, _ = working_run(monkeypatch)
    esiti = _esiti(report)
    assert esiti["CLEAN-FLOW"][0] == cert.PASS, esiti["CLEAN-FLOW"]
    assert esiti["CLEAN-VERIFICA"][0] == cert.PASS, esiti["CLEAN-VERIFICA"]

    cancellazioni = [q for q in database.state["sql"] if q.startswith("DELETE FROM flow_")]
    esecuzioni = next(i for i, q in enumerate(cancellazioni)
                      if q.startswith("DELETE FROM flow_executions WHERE id IN"))
    eventi = next(i for i, q in enumerate(cancellazioni)
                  if q.startswith("DELETE FROM flow_events WHERE id IN"))
    assert esecuzioni < eventi, cancellazioni
    # E davvero non resta niente.
    assert database.state["flow_esecuzioni"] == {}
    assert database.state["flow_events_vivi"] == {}


def test_106h_gli_eventi_delle_agenzie_dedicate_non_si_cancellano_per_id(monkeypatch):
    """La distinzione che tiene in piedi il test 88.

    Se gli eventi FLOW delle agenzie DEDICATE finissero nella cancellazione
    per id, un buco in `DEDICATED_TABLES` sarebbe mascherato: le righe
    sparirebbero per un'altra strada e nessuno se ne accorgerebbe. Qui si
    verifica che `shared_flow_event_ids` contenga SOLO i due eventi delle
    agenzie condivise, mentre `created_effects` - perimetro e verifica - li
    contiene tutti.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))

    # Le due cancellazioni esistono ENTRAMBE e non si sovrappongono: quella
    # per id porta due soli eventi - uno per agenzia condivisa - e quella per
    # agenzia porta le agenzie dedicate.
    per_id = [p for q, p in database.state["interrogazioni"]
              if q.startswith("DELETE FROM flow_events WHERE id IN")]
    per_agenzia = [p for q, p in database.state["interrogazioni"]
                   if q.startswith("DELETE FROM flow_events WHERE agency_id IN")]
    assert len(per_id) == 1, per_id
    assert len(per_agenzia) == 1, per_agenzia
    assert len(per_id[0][0]) == 2, per_id
    assert not (set(per_id[0][0]) & set(per_agenzia[0][0])), (per_id, per_agenzia)
    # E la verifica finale non trova residui: i quattro eventi se ne sono
    # andati per le due strade giuste.
    assert _esiti(report)["CLEAN-VERIFICA"][0] == cert.PASS


# La persistenza non rende riuscita una risposta HTTP fallita.
@pytest.mark.parametrize("guasto,atteso", [
    ("post_500", "FLOW-post-A"),
    ("stato_failed", "FLOW-stato-A"),
    ("dettaglio_404", "FLOW-dettaglio-proprio-A"),
])
def test_106i_flow_non_maschera_errori_http_o_esecuzioni(monkeypatch, guasto, atteso):
    class Probe(FakeHttp):
        def request(self, method, path, **kwargs):
            risposta = super().request(method, path, **kwargs)
            if (method == "POST" and path == "/api/flow/events"
                    and (kwargs.get("payload") or {}).get("event_type")
                    == cert.Certification.FLOW_EXECUTION_TRIGGER):
                if guasto == "post_500":
                    return self._reply(method, path, 500, b'{"detail":"synthetic error"}')
                if guasto == "stato_failed":
                    for riga in self.flow_esecuzioni.values():
                        riga["status"] = "failed"
            if (guasto == "dettaglio_404" and method == "GET"
                    and re.fullmatch(r"/api/flow/executions/[0-9]+", path)):
                return self._reply(method, path, 404, b'{"detail":"not found"}')
            return risposta

    _code, report, _database, _probe, _ = working_run(
        monkeypatch, http=Probe(only="generic"))
    assert _esiti(report)[atteso][0] == cert.FAIL, _esiti(report).get(atteso)
    # I guasti simulati non devono impedire di rintracciare e ripulire le righe.
    assert _esiti(report)["CLEAN-VERIFICA"][0] == cert.PASS
