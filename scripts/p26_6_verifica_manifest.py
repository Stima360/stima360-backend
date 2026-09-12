#!/usr/bin/env python3
"""Verifica e promuove il manifest delle chiavi di storage del recupero P26-6.

PERCHE' UN PROGRAMMA E NON TRE RIGHE DI SHELL

Le tre righe di shell c'erano, e sbagliavano due volte.

  1. Cercavano il file con `ls -1t ... | head -1`. "Il piu' recente" non e'
     "quello di questa esecuzione": bastava una seconda shell aperta, un
     manifest di ieri rimasto li', o due tentativi ravvicinati, e il
     verificatore avrebbe promosso il file sbagliato senza accorgersene. Qui
     il percorso ARRIVA come argomento, dal wrapper che lo ha appena creato.

  2. Contavano le righe con `wc -l`. Un CSV non si conta a righe: un valore
     che contenga un a-capo - una chiave di storage puo' contenerne - vale
     una riga per `wc` e un campo per il formato. Qui si legge con il modulo
     `csv`, che e' l'unica cosa che sappia dove finisce un record.

COSA VERIFICA, PRIMA DI PROMUOVERE

  * il numero di righe coincide con quello che il database ha dichiarato;
  * l'impronta d'insieme coincide: si ricompone `md5` della concatenazione
    delle impronte per id, esattamente come fa la query del censimento;
  * ogni riga ha la chiave e la sua impronta COERENTI: `md5(storage_key)`
    ricalcolato qui deve dare la colonna `impronta`. E' il controllo che
    distingue un file troncato a meta' record da uno integro;
  * il file di destinazione NON esiste. Un manifest promosso non si
    sovrascrive: e' l'unico posto in cui quelle chiavi vivono.

COSA NON FA

Non stampa nessuna chiave. A video vanno il conteggio, le due impronte e il
percorso: quanto basta a un registro, niente che localizzi un oggetto nel
bucket. Non tocca il database e non tocca lo storage.

Uscita 0 = promosso. Qualunque altro valore = NON promosso, e il motivo e'
sulla prima riga di stderr.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path

COLONNE_ATTESE = ["id", "property_id", "storage_key", "impronta", "stato"]


class ManifestNonValido(Exception):
    """Il manifest non e' promuovibile. Il messaggio dice perche'."""


def _md5(testo: str) -> str:
    return hashlib.md5(testo.encode("utf-8")).hexdigest()


def leggi_controllo(percorso: Path) -> tuple[int, str]:
    """Il file di controllo: una riga, due campi separati da TAB.

    Formato macchina di proposito. La versione precedente estraeva il
    conteggio dal log con `tr -dc '0-9\\n'`, che toglie ogni carattere non
    numerico e quindi incollava il conteggio alle cifre dell'md5 stampato
    accanto: "2" e "3f9a1..." diventavano un unico numero.
    """
    if not percorso.is_file():
        raise ManifestNonValido(f"file di controllo assente: {percorso}")
    testo = percorso.read_text(encoding="utf-8").strip("\n")
    righe = [r for r in testo.split("\n") if r.strip()]
    if len(righe) != 1:
        raise ManifestNonValido(
            f"il controllo deve avere una riga sola, ne ha {len(righe)}")
    campi = righe[0].split("\t")
    if len(campi) != 2:
        raise ManifestNonValido(
            f"il controllo deve avere due campi separati da TAB, ne ha {len(campi)}")
    conteggio, impronta = campi[0].strip(), campi[1].strip()
    if not conteggio.isdigit():
        raise ManifestNonValido(f"conteggio non numerico nel controllo: {conteggio!r}")
    return int(conteggio), impronta


def leggi_manifest(percorso: Path) -> list[dict]:
    """Le righe del manifest, lette con un parser CSV vero."""
    if not percorso.is_file():
        raise ManifestNonValido(f"manifest assente: {percorso}")
    with percorso.open("r", encoding="utf-8", newline="") as sorgente:
        lettore = csv.DictReader(sorgente)
        if lettore.fieldnames != COLONNE_ATTESE:
            raise ManifestNonValido(
                f"intestazione inattesa: {lettore.fieldnames} "
                f"invece di {COLONNE_ATTESE}")
        righe = list(lettore)
    for numero, riga in enumerate(righe, start=1):
        if any(riga.get(colonna) in (None, "") for colonna in COLONNE_ATTESE):
            raise ManifestNonValido(
                f"riga {numero} incompleta: un campo e' vuoto o mancante")
        if riga.get(None) is not None:
            raise ManifestNonValido(f"riga {numero}: colonne in piu' del previsto")
    return righe


def verifica(righe: list[dict], conteggio_atteso: int, impronta_attesa: str) -> str:
    """Le tre domande, in ordine di severita' crescente. Ritorna l'impronta."""
    if len(righe) != conteggio_atteso:
        raise ManifestNonValido(
            f"righe {len(righe)}, il database ne ha dichiarate {conteggio_atteso}: "
            "manifest incompleto, NON promosso")

    for numero, riga in enumerate(righe, start=1):
        atteso = _md5(riga["storage_key"])
        if riga["impronta"] != atteso:
            raise ManifestNonValido(
                f"riga {numero}: l'impronta non corrisponde alla chiave "
                "(file alterato o troncato dentro un record)")

    per_id = sorted(righe, key=lambda r: int(r["id"]))
    impronta = _md5(",".join(r["impronta"] for r in per_id))
    if impronta != impronta_attesa:
        raise ManifestNonValido(
            "impronta d'insieme diversa da quella dichiarata dal database: "
            "il file non appartiene a questa esecuzione")
    return impronta


def promuovi(parziale: Path, definitivo: Path) -> Path:
    """Rinomina, senza mai sovrascrivere, e scrive lo sha256 accanto."""
    if definitivo.exists():
        raise ManifestNonValido(
            f"esiste gia' un manifest promosso in {definitivo}: non lo si "
            "sovrascrive. Va esaminato prima di rieseguire il censimento.")
    parziale.replace(definitivo)
    digest = hashlib.sha256(definitivo.read_bytes()).hexdigest()
    definitivo.with_suffix(definitivo.suffix + ".sha256").write_text(
        f"{digest}  {definitivo.name}\n", encoding="utf-8")
    return definitivo


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Verifica e promuove il manifest delle chiavi di storage.")
    parser.add_argument("--manifest", required=True, type=Path,
                        help="il .csv.parziale prodotto da questa esecuzione")
    parser.add_argument("--controllo", required=True, type=Path,
                        help="il .tsv con conteggio e impronta attesi")
    parser.add_argument("--definitivo", required=True, type=Path,
                        help="dove promuoverlo; deve NON esistere")
    argomenti = parser.parse_args(argv)

    try:
        conteggio, impronta_attesa = leggi_controllo(argomenti.controllo)
        righe = leggi_manifest(argomenti.manifest)
        impronta = verifica(righe, conteggio, impronta_attesa)
        destinazione = promuovi(argomenti.manifest, argomenti.definitivo)
    except ManifestNonValido as errore:
        print(f"MANIFEST NON PROMOSSO: {errore}", file=sys.stderr)
        return 2

    print(f"righe verificate: {len(righe)}")
    print(f"impronta d'insieme: {impronta}")
    print(f"manifest promosso: {destinazione}")
    print(f"sha256 in: {destinazione}.sha256")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
