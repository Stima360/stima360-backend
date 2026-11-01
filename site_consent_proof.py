"""C3.5 - la prova del consenso marketing scritta con la richiesta di valutazione.

La pagina `dati_personali.html` manda, oltre alla scelta `consenso_marketing`
(che `main.py` salva come sempre), il testo della casella marketing e l'impronta
SHA-256 di `privacy.html`. Qui, DOPO che la stima e' stata salvata e confermata
da `main.py`, i due valori si scrivono nella stessa riga `stime` SOLO se
coincidono con una versione ufficiale accettata conservata sul server
(`site_official`); i valori del browser da soli non sono una prova.

Garanzie verso `main.py`:

    * `record_after_commit` non solleva MAI: ogni errore (modulo mancante,
      versioni ufficiali illeggibili, colonne assenti, vincolo violato,
      database lento) diventa un esito testuale e una riga di log senza dati
      personali;
    * lavora in una transazione PROPRIA, dopo il commit della stima: la stima,
      la valutazione, il PDF, le email e il WhatsApp non dipendono da qui;
    * il tempo e' limitato da `statement_timeout` (STATEMENT_TIMEOUT_MS);
    * senza la migrazione 112 (colonne assenti) non scrive niente.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

TEXT_MAX = 1000
STATEMENT_TIMEOUT_MS = 2000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
COLUMNS_SQL = ("SELECT count(*) FROM information_schema.columns WHERE table_schema = current_schema() "
               "AND table_name = 'stime' AND column_name IN "
               "('consenso_marketing_testo', 'consenso_marketing_privacy_sha256')")
UPDATE_SQL = ("UPDATE stime SET consenso_marketing_testo = %s, consenso_marketing_privacy_sha256 = %s "
              "WHERE id = %s")


def verified_proof(raw) -> tuple[str | None, str | None]:
    """(testo ufficiale della casella, impronta ufficiale della privacy), o
    None dove il browser non li manda o non coincidono con una versione
    ufficiale accettata."""
    import site_official
    raw = raw if isinstance(raw, dict) else {}
    testo = raw.get("consenso_marketing_testo")
    testo = re.sub(r"\s+", " ", testo).strip() if isinstance(testo, str) else ""
    impronta = raw.get("informativa_privacy_sha256")
    impronta = impronta.strip().lower() if isinstance(impronta, str) else ""
    testo = testo if 0 < len(testo) <= TEXT_MAX else None
    impronta = impronta if _SHA256.match(impronta) else None
    casella = site_official.accepted_checkbox(testo)
    privacy = site_official.accepted_privacy(impronta)
    if (testo and casella is None) or (impronta and privacy is None):
        log.warning("public_stima_marketing_proof_not_official text=%s privacy=%s",
                    "unofficial" if testo and casella is None else "ok_or_absent",
                    "unofficial" if impronta and privacy is None else "ok_or_absent")
    return (casella["text"] if casella else None, privacy["sha256"] if privacy else None)


def record_after_commit(conn, stima_id, raw) -> str:
    """Scrive la prova nella riga della stima GIA' confermata. Esiti:
    'stored', 'absent' (niente di ufficiale da scrivere), 'no_columns'
    (manca la 112), 'error'. Non solleva mai."""
    try:
        testo, impronta = verified_proof(raw)
        if testo is None and impronta is None:
            return "absent"
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = {int(STATEMENT_TIMEOUT_MS)}")
            cur.execute(COLUMNS_SQL)
            if cur.fetchone()[0] != 2:
                conn.rollback()
                return "no_columns"
            cur.execute(UPDATE_SQL, (testo, impronta, stima_id))
        conn.commit()
        return "stored"
    except Exception as exc:  # noqa: BLE001 - la stima non dipende dalla prova
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        log.warning("public_stima_marketing_proof_failed stima_id=%s error_type=%s", stima_id, type(exc).__name__)
        return "error"
