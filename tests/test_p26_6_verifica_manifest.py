"""P26-6 - il verificatore del manifest delle chiavi di storage.

PERCHE' QUESTO FILE ESISTE

La promozione del manifest era tre righe di shell, e sbagliavano due volte:

  * `ls -1t ... | head -1` prendeva "il piu' recente", non quello di questa
    esecuzione. Un manifest di ieri, una seconda shell aperta, due tentativi
    ravvicinati - e veniva promosso il file sbagliato, in silenzio;
  * `wc -l` contava le righe di un CSV, che non si conta a righe, e il
    conteggio atteso veniva estratto dal log con `tr -dc '0-9\\n'`, che
    incollava il conteggio alle cifre dell'md5 stampato accanto.

Qui il percorso ARRIVA come argomento e il file si legge con un parser CSV.
I tre casi che la procedura deve distinguere - due righe valide, file
incompleto, manifest gia' presente - hanno una prova ciascuno, su dati
sintetici: nessuna chiave vera, nessun database.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _carica_verificatore():
    """Il modulo sotto `scripts/`, che non e' un pacchetto importabile."""
    percorso = ROOT / "scripts" / "p26_6_verifica_manifest.py"
    spec = importlib.util.spec_from_file_location("p26_6_verifica_manifest", percorso)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


verificatore = _carica_verificatore()


def _md5(testo: str) -> str:
    return hashlib.md5(testo.encode("utf-8")).hexdigest()


CHIAVI = {
    5101: "owner/2026/09/12/9f8e7d6c5b4a39281706fedcba098765",
    5102: "owner/2026/09/12/1a2b3c4d5e6f708192a3b4c5d6e7f809",
}


def _scrivi_manifest(cartella: Path, chiavi=None, intestazione=True,
                     tronca_a=None) -> Path:
    """Un manifest sintetico, nella forma esatta che il censimento produce."""
    chiavi = CHIAVI if chiavi is None else chiavi
    righe = []
    if intestazione:
        righe.append("id,property_id,storage_key,impronta,stato")
    for identificativo, chiave in sorted(chiavi.items()):
        righe.append(f"{identificativo},50,{chiave},{_md5(chiave)},da_rimuovere")
    testo = "\n".join(righe) + "\n"
    if tronca_a is not None:
        testo = testo[:tronca_a]
    percorso = cartella / "manifest.csv.parziale"
    percorso.write_text(testo, encoding="utf-8")
    return percorso


def _scrivi_controllo(cartella: Path, chiavi=None, conteggio=None) -> Path:
    """Il file di controllo: conteggio TAB impronta, una riga sola."""
    chiavi = CHIAVI if chiavi is None else chiavi
    impronta = _md5(",".join(_md5(c) for _i, c in sorted(chiavi.items())))
    quante = len(chiavi) if conteggio is None else conteggio
    percorso = cartella / "controllo_manifest.tsv"
    percorso.write_text(f"{quante}\t{impronta}\n", encoding="utf-8")
    return percorso


# ---------------------------------------------------------------------------
# 1. Il caso buono: due righe valide
# ---------------------------------------------------------------------------

def test_01_due_righe_valide_vengono_promosse(tmp_path, capsys):
    parziale = _scrivi_manifest(tmp_path)
    controllo = _scrivi_controllo(tmp_path)
    definitivo = tmp_path / "manifest.csv"

    esito = verificatore.main(["--manifest", str(parziale),
                              "--controllo", str(controllo),
                              "--definitivo", str(definitivo)])
    assert esito == 0

    assert definitivo.is_file()
    assert not parziale.exists(), "il parziale deve sparire, non restare accanto"
    sha = definitivo.with_suffix(definitivo.suffix + ".sha256")
    assert sha.is_file()
    assert hashlib.sha256(definitivo.read_bytes()).hexdigest() in sha.read_text()

    # NESSUNA CHIAVE A VIDEO: il registro riceve numeri, non posizioni di
    # oggetti nel bucket.
    uscita = capsys.readouterr()
    for chiave in CHIAVI.values():
        assert chiave not in uscita.out, uscita.out
        assert chiave not in uscita.err, uscita.err
    assert "righe verificate: 2" in uscita.out


def test_02_il_percorso_arriva_come_argomento_non_dal_piu_recente(tmp_path):
    """Due esecuzioni, due directory: si verifica QUELLA indicata.

    E' il difetto di `ls -1t | head -1`: con un manifest piu' recente accanto,
    avrebbe promosso l'altro.
    """
    vecchia = tmp_path / "20260912T090000Z"
    nuova = tmp_path / "20260912T100000Z"
    vecchia.mkdir(), nuova.mkdir()

    altre = {7001: "owner/altro/aaaa1111bbbb2222cccc3333dddd4444"}
    _scrivi_manifest(vecchia, chiavi=altre)
    _scrivi_controllo(vecchia, chiavi=altre)
    parziale = _scrivi_manifest(nuova)
    controllo = _scrivi_controllo(nuova)

    esito = verificatore.main(["--manifest", str(parziale),
                              "--controllo", str(controllo),
                              "--definitivo", str(nuova / "manifest.csv")])
    assert esito == 0
    assert (nuova / "manifest.csv").is_file()
    # L'altra esecuzione non e' stata toccata.
    assert (vecchia / "manifest.csv.parziale").is_file()
    assert not (vecchia / "manifest.csv").exists()


# ---------------------------------------------------------------------------
# 2. Il file incompleto
# ---------------------------------------------------------------------------

def test_03_un_manifest_con_una_riga_in_meno_non_passa(tmp_path, capsys):
    """Il conteggio del database dice due, il file ne ha una."""
    parziale = _scrivi_manifest(tmp_path, chiavi={5101: CHIAVI[5101]})
    controllo = _scrivi_controllo(tmp_path)          # attende DUE righe
    definitivo = tmp_path / "manifest.csv"

    assert verificatore.main(["--manifest", str(parziale),
                             "--controllo", str(controllo),
                             "--definitivo", str(definitivo)]) == 2
    assert not definitivo.exists(), "promosso un manifest incompleto"
    assert parziale.is_file(), "il parziale va conservato per l'esame"
    assert "incompleto" in capsys.readouterr().err


def test_04_un_file_di_sola_intestazione_non_passa(tmp_path):
    """`COPY` che non trova righe produce un file di sola intestazione.

    Senza il confronto col conteggio sembrerebbe un manifest valido e vuoto -
    e la pulizia del bucket non rimuoverebbe niente, credendo di aver finito.
    """
    parziale = _scrivi_manifest(tmp_path, chiavi={})
    controllo = _scrivi_controllo(tmp_path)
    assert verificatore.main(["--manifest", str(parziale),
                             "--controllo", str(controllo),
                             "--definitivo", str(tmp_path / "manifest.csv")]) == 2
    assert not (tmp_path / "manifest.csv").exists()


def test_05_un_record_troncato_a_meta_non_passa(tmp_path, capsys):
    """Troncato DENTRO un record: `wc -l` non se ne accorgerebbe.

    Il taglio porta via colonne, non righe: il file resta "due righe" per una
    conta a linee e non lo e' per il formato.
    """
    intero = _scrivi_manifest(tmp_path).read_text(encoding="utf-8")
    parziale = _scrivi_manifest(tmp_path, tronca_a=len(intero) - 40)
    controllo = _scrivi_controllo(tmp_path)
    assert verificatore.main(["--manifest", str(parziale),
                             "--controllo", str(controllo),
                             "--definitivo", str(tmp_path / "manifest.csv")]) == 2
    errore = capsys.readouterr().err
    assert "incompleta" in errore, errore
    assert not (tmp_path / "manifest.csv").exists()


def test_05b_una_chiave_mozzata_con_tutte_le_colonne_non_passa(tmp_path, capsys):
    """Cinque colonne, struttura intatta, chiave accorciata di sei caratteri.

    Nessun controllo strutturale la vede: il record e' completo e il numero di
    righe e' giusto. A vederla e' il confronto fra la chiave e la sua
    impronta, riga per riga - senza quello il manifest indicherebbe un oggetto
    che nel bucket non esiste, e la pulizia lo dichiarerebbe "gia' rimosso".
    """
    righe = _scrivi_manifest(tmp_path).read_text(encoding="utf-8").split("\n")
    campi = righe[2].split(",")
    campi[2] = campi[2][:-6]                     # la chiave, mozzata
    righe[2] = ",".join(campi)
    parziale = tmp_path / "manifest.csv.parziale"
    parziale.write_text("\n".join(righe), encoding="utf-8")
    controllo = _scrivi_controllo(tmp_path)

    assert verificatore.main(["--manifest", str(parziale),
                             "--controllo", str(controllo),
                             "--definitivo", str(tmp_path / "manifest.csv")]) == 2
    errore = capsys.readouterr().err
    assert "impronta non corrisponde" in errore, errore
    assert not (tmp_path / "manifest.csv").exists()


def test_06_un_manifest_di_un_altra_esecuzione_non_passa(tmp_path):
    """Conteggio giusto, chiavi diverse: l'impronta d'insieme lo rivela."""
    altre = {5101: "owner/altro/1111111111111111111111111111aaaa",
             5102: "owner/altro/2222222222222222222222222222bbbb"}
    parziale = _scrivi_manifest(tmp_path, chiavi=altre)
    controllo = _scrivi_controllo(tmp_path)          # impronta delle NOSTRE
    assert verificatore.main(["--manifest", str(parziale),
                             "--controllo", str(controllo),
                             "--definitivo", str(tmp_path / "manifest.csv")]) == 2


# ---------------------------------------------------------------------------
# 3. Il manifest precedente gia' presente
# ---------------------------------------------------------------------------

def test_07_un_manifest_gia_promosso_non_viene_sovrascritto(tmp_path, capsys):
    """E' l'unico posto in cui quelle chiavi vivono: non si sovrascrive.

    Il file precedente resta identico, byte per byte, e il parziale resta
    dov'e' per essere esaminato.
    """
    definitivo = tmp_path / "manifest.csv"
    definitivo.write_text("id,property_id,storage_key,impronta,stato\n"
                          "9,50,owner/vecchia/chiave,x,rimosso\n", encoding="utf-8")
    prima = definitivo.read_bytes()

    parziale = _scrivi_manifest(tmp_path)
    controllo = _scrivi_controllo(tmp_path)

    assert verificatore.main(["--manifest", str(parziale),
                             "--controllo", str(controllo),
                             "--definitivo", str(definitivo)]) == 2
    assert definitivo.read_bytes() == prima, "il manifest precedente e' cambiato"
    assert parziale.is_file()
    assert "non lo si sovrascrive" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 4. Il file di controllo, che sostituisce l'estrazione dal log
# ---------------------------------------------------------------------------

def test_08_il_controllo_e_in_formato_macchina():
    """Conteggio e impronta separati, non da spremere con una regex.

    `tr -dc '0-9\\n'` sull'output tabellare incollava il conteggio alle cifre
    dell'md5: "2" e "3f9a1..." diventavano un numero solo.
    """
    conteggio, impronta = verificatore.leggi_controllo(
        _scrivi_controllo(Path(__import__("tempfile").mkdtemp())))
    assert conteggio == 2
    assert len(impronta) == 32 and all(c in "0123456789abcdef" for c in impronta)


@pytest.mark.parametrize("contenuto,motivo", [
    ("2 3f9a\n", "due campi separati da TAB"),
    ("2\tabc\n3\tdef\n", "una riga sola"),
    ("due\tabc\n", "non numerico"),
    ("", "una riga sola"),
])
def test_09_un_controllo_malformato_blocca_invece_di_indovinare(
        tmp_path, contenuto, motivo):
    percorso = tmp_path / "controllo_manifest.tsv"
    percorso.write_text(contenuto, encoding="utf-8")
    with pytest.raises(verificatore.ManifestNonValido) as errore:
        verificatore.leggi_controllo(percorso)
    assert motivo in str(errore.value), str(errore.value)


def test_10_un_manifest_assente_non_e_un_manifest_vuoto(tmp_path):
    """"Non c'e'" e "non contiene righe" sono due risposte diverse."""
    controllo = _scrivi_controllo(tmp_path)
    assert verificatore.main(["--manifest", str(tmp_path / "mai_scritto.csv"),
                             "--controllo", str(controllo),
                             "--definitivo", str(tmp_path / "manifest.csv")]) == 2


# ---------------------------------------------------------------------------
# 5. Il censimento e il verificatore parlano della stessa cosa
# ---------------------------------------------------------------------------

def test_11_il_censimento_produce_i_due_file_che_il_verificatore_legge():
    """I nomi sono letterali in entrambi: nessuna interpolazione di mezzo.

    `\\copy ... TO :'variabile'` NON funziona - psql non interpola gli
    argomenti di `\\copy` - ed e' il difetto che questo giro corregge. Il
    censimento usa `COPY ... TO STDOUT` con `\\g` e un nome letterale; qui si
    verifica che i due nomi siano ancora quelli.
    """
    censimento = (ROOT / "censimento_42e32975ccd6.sql").read_text(encoding="utf-8")
    istruzioni = "\n".join(r for r in censimento.split("\n")
                           if not r.strip().startswith("--"))
    assert "\\copy" not in istruzioni, "e' tornato un \\copy, che non interpola"
    assert "TO STDOUT WITH (FORMAT csv, HEADER true) \\g manifest.csv.parziale" \
        in istruzioni
    assert "TO STDOUT WITH (FORMAT text, DELIMITER E'\\t')" in istruzioni
    assert "\\g controllo_manifest.tsv" in istruzioni

    # E le colonne sono quelle che il parser si aspetta, nello stesso ordine.
    assert "SELECT id, property_id, storage_key, md5(storage_key) AS impronta," \
        in istruzioni
    assert verificatore.COLONNE_ATTESE == [
        "id", "property_id", "storage_key", "impronta", "stato"]


# ---------------------------------------------------------------------------
# 6. Il formato REALMENTE esportato, non un TSV scritto a mano
#
# I test qui sopra scrivono il file di controllo con `f"{n}\t{md5}\n"`: sono
# utili, ma partono da un TSV gia' corretto e quindi non direbbero nulla se il
# censimento esportasse un'altra forma. Qui si parte dalla QUERY - letta dal
# file del censimento - e si riproduce cosa `COPY ... TO STDOUT WITH (FORMAT
# text, DELIMITER E'\t')` scriverebbe, regole di escape comprese.
#
# E' UNA RIPRODUZIONE, NON POSTGRESQL. In questo ambiente non c'e' un server:
# quello che si prova e' che il verificatore accetta la forma che il formato
# testo produce, e rifiuta quella che produceva la versione concatenata. La
# conferma sul server vero resta da fare, come tutto il resto del censimento.
# ---------------------------------------------------------------------------

CENSIMENTO = ROOT / "censimento_42e32975ccd6.sql"


def _copy_di_controllo() -> tuple[str, str]:
    """(sottoquery, opzioni) della COPY del punto 8c, dal censimento.

    Il taglio e' sul marcatore `) TO STDOUT`, non su `rindex(")")`: l'ultima
    parentesi della riga chiude il `WITH (...)`, non la sottoquery.
    """
    testo = CENSIMENTO.read_text(encoding="utf-8")
    istruzioni = "\n".join(r for r in testo.split("\n")
                           if not r.strip().startswith("--"))
    inizio = istruzioni.index("COPY (\n    SELECT count(*) AS conteggio")
    chiusura = istruzioni.index(") TO STDOUT", inizio)
    fine = istruzioni.index("\\g controllo_manifest.tsv", chiusura)
    interna = istruzioni[inizio + len("COPY ("):chiusura]
    opzioni = istruzioni[chiusura:fine]
    return interna.strip(), opzioni


def _campo_formato_testo(valore) -> str:
    """Un campo come lo scrive `COPY ... WITH (FORMAT text)`.

    Le regole che contano qui: NULL diventa `\\N`, e backslash, TAB, a-capo e
    ritorno carrello vengono protetti con una sequenza di escape. E' proprio
    l'escape del TAB a rendere indistinguibile un tab CONTENUTO in un valore
    dal TAB che separa due campi: il primo esce come `\\t`, il secondo come un
    tab vero.
    """
    if valore is None:
        return r"\N"
    return (str(valore).replace("\\", "\\\\").replace("\t", "\\t")
            .replace("\n", "\\n").replace("\r", "\\r"))


def _esporta_controllo_due_colonne(cartella: Path, chiavi=None) -> Path:
    """Cosa scrive la COPY del punto 8c, con la semantica della sua query."""
    chiavi = CHIAVI if chiavi is None else chiavi
    conteggio = len(chiavi)
    # `string_agg` su un insieme vuoto da' NULL: il coalesce lo porta a
    # `md5('')`, che e' l'impronta della concatenazione vuota.
    impronta = (_md5(",".join(_md5(c) for _i, c in sorted(chiavi.items())))
                if chiavi else _md5(""))
    riga = "\t".join(_campo_formato_testo(v) for v in (conteggio, impronta))
    percorso = cartella / "controllo_manifest.tsv"
    percorso.write_text(riga + "\n", encoding="utf-8")
    return percorso


def _esporta_controllo_concatenato(cartella: Path, chiavi=None) -> Path:
    """La forma PRECEDENTE: una colonna sola, col TAB dentro il valore.

    `count(*)::text || E'\\t' || md5(...)` produce UN campo. In formato testo
    quel tab viene protetto come `\\t` - due caratteri - quindi la riga non ha
    nessun TAB delimitatore e il verificatore vede un campo solo.
    """
    chiavi = CHIAVI if chiavi is None else chiavi
    impronta = (_md5(",".join(_md5(c) for _i, c in sorted(chiavi.items())))
                if chiavi else "")
    unico = f"{len(chiavi)}\t{impronta}"
    percorso = cartella / "controllo_manifest.tsv"
    percorso.write_text(_campo_formato_testo(unico) + "\n", encoding="utf-8")
    return percorso


def test_12_la_query_di_controllo_espone_due_colonne():
    """Due colonne dichiarate, e il coalesce per il caso vuoto."""
    import sqlglot

    interna, opzioni = _copy_di_controllo()
    espressione = sqlglot.parse_one(interna, dialect="postgres")
    proiezione = espressione.selects
    assert len(proiezione) == 2, [p.sql() for p in proiezione]
    assert [p.alias_or_name for p in proiezione] == ["conteggio", "impronta"]

    normalizzata = " ".join(interna.split()).lower()
    assert "count(*) as conteggio" in normalizzata, normalizzata
    assert "coalesce(" in normalizzata and "md5('')" in normalizzata, normalizzata
    assert "||" not in normalizzata, "e' tornata la concatenazione in una colonna"

    # E il formato dichiarato e' TESTO con il TAB come DELIMITATORE: e' cio'
    # che rende il tab un separatore di campi invece di un carattere qualsiasi.
    assert "FORMAT text" in opzioni, opzioni
    assert "DELIMITER E'\\t'" in opzioni, opzioni


def test_13_il_verificatore_legge_cio_che_quella_copy_esporta(tmp_path):
    """Due colonne, due righe di manifest: accettato."""
    parziale = _scrivi_manifest(tmp_path)
    controllo = _esporta_controllo_due_colonne(tmp_path)

    # La riga esportata ha UN tab vero, che separa due campi.
    grezzo = controllo.read_text(encoding="utf-8").rstrip("\n")
    assert grezzo.count("\t") == 1, repr(grezzo)

    assert verificatore.main(["--manifest", str(parziale),
                             "--controllo", str(controllo),
                             "--definitivo", str(tmp_path / "manifest.csv")]) == 0
    assert (tmp_path / "manifest.csv").is_file()


def test_14_il_caso_zero_righe_resta_coerente(tmp_path, capsys):
    """Nessun oggetto nello storage: manifest vuoto, e le due parti d'accordo.

    `string_agg` su un insieme vuoto e' NULL; senza `coalesce(..., md5(''))` la
    colonna sarebbe uscita come `\\N` e il verificatore avrebbe confrontato
    l'impronta della concatenazione vuota con la stringa `\\N`, rifiutando un
    risultato corretto. Qui coincidono, e un run che non ha caricato nulla
    produce un manifest vuoto VALIDO invece di un errore fuorviante.
    """
    parziale = _scrivi_manifest(tmp_path, chiavi={})
    controllo = _esporta_controllo_due_colonne(tmp_path, chiavi={})
    grezzo = controllo.read_text(encoding="utf-8").rstrip("\n")
    assert grezzo.split("\t") == ["0", _md5("")], repr(grezzo)
    assert r"\N" not in grezzo, repr(grezzo)

    assert verificatore.main(["--manifest", str(parziale),
                             "--controllo", str(controllo),
                             "--definitivo", str(tmp_path / "manifest.csv")]) == 0
    assert "righe verificate: 0" in capsys.readouterr().out


def test_15_la_forma_concatenata_precedente_viene_rifiutata(tmp_path, capsys):
    """LA REGRESSIONE SUL DIFETTO: una colonna sola non passa.

    `count(*)::text || E'\\t' || md5(...)` produce un campo unico; il formato
    testo protegge quel tab come `\\t`, quindi nella riga non c'e' nessun TAB
    delimitatore. Il verificatore lo dice invece di indovinare dove finisce il
    conteggio.
    """
    parziale = _scrivi_manifest(tmp_path)
    controllo = _esporta_controllo_concatenato(tmp_path)
    grezzo = controllo.read_text(encoding="utf-8").rstrip("\n")
    assert "\t" not in grezzo, repr(grezzo)
    assert "\\t" in grezzo, repr(grezzo)

    assert verificatore.main(["--manifest", str(parziale),
                             "--controllo", str(controllo),
                             "--definitivo", str(tmp_path / "manifest.csv")]) == 2
    assert "due campi separati da TAB" in capsys.readouterr().err
    assert not (tmp_path / "manifest.csv").exists()


def test_16_un_tab_dentro_un_valore_non_confonde_i_campi(tmp_path):
    """L'escape del formato testo e' la ragione per cui due colonne bastano.

    Se un giorno una delle due colonne contenesse un tab, `COPY` lo scriverebbe
    come `\\t` e il campo resterebbe uno: il delimitatore resta riconoscibile.
    """
    riga = "\t".join(_campo_formato_testo(v) for v in (2, "a\tb"))
    assert riga.count("\t") == 1, repr(riga)
    assert riga.split("\t")[1] == "a\\tb", repr(riga)
