"""V3 - I TESTI DEFINITIVI DELLE 16 EMAIL (2026-11-03), senza database.

  01  numero, giorno, oggetto e corpo di ogni email coincidono ESATTAMENTE
      con i testi definitivi consegnati dal titolare (copia fedele in
      `tests/data/followup_v3_testi_definitivi.txt`): nessuna
      normalizzazione, nemmeno di apostrofi, virgolette o emoji.
  02  l'email 6: il testo definitivo con i due soli adattamenti approvati
      dal titolare («in allegato» -> link di download), perche' il canale
      spedisce la guida con un link e non come allegato.
  03  html con il marchio e testo semplice dai corpi nuovi: ogni riga, logo,
      colore, CTA, piede, disiscrizione; i link sono solo quelli reali.
  04  la CTA segue il corpo: «Rispondi» solo dove il testo chiede di
      rispondere, verso il Reply-To gia' presente nei metadati.
  05  l'email 6: link del PDF ufficiale nel corpo e nel pulsante, nessun
      allegato passato dall'adapter.
"""
from __future__ import annotations

import html as _html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "data" / "followup_v3_testi_definitivi.txt"
BASE = "https://crm-test.example.invalid"
FIRMA = "Il team STIMA360"
_BLOCCO = re.compile(r"^EMAIL (\d+) — GIORNO (\d+)\n\nOggetto: ([^\n]+)\n\n(.*)$", re.S)


def testi_definitivi() -> dict[int, tuple[int, str, str, str]]:
    """{numero: (giorno, oggetto, corpo, nota)} letti dal file consegnato."""
    out = {}
    for blocco in re.split(r"\n---\n", FIXTURE.read_text(encoding="utf-8")):
        m = _BLOCCO.search(blocco.strip("\n"))
        if not m:
            continue
        corpo, _, nota = m.group(4).partition("\n" + FIRMA)
        out[int(m.group(1))] = (int(m.group(2)), m.group(3), (corpo + "\n" + FIRMA).strip("\n"), nota.strip())
    return out


def _messaggio(n: int) -> dict:
    from communication import templates
    t = templates.get(f"followup_v3_m{n}", 3)
    contesto = {"contact_first_name": "Anna", "agency_name": "Agenzia Prova",
                "unsubscribe_url": f"{BASE}/api/public/communication/unsubscribe?t=tok{n}",
                "guide_url": f"{BASE}/api/public/communication/guida-vendere-casa-2026.pdf"}
    return {"template_key": f"followup_v3_m{n}", "subject_snapshot": t.subject(contesto),
            "rendered_body": t.body(contesto), "destination_snapshot": "anna@example.it",
            "metadata": {"body_format": "text", "reply_to": "info@stima360.it",
                         "unsubscribe_url": contesto["unsubscribe_url"]}}


def test_01_oggetti_e_corpi_identici_ai_testi_definitivi():
    from communication import followup_v3_texts as t
    definitivi = testi_definitivi()
    assert sorted(definitivi) == list(range(1, 17))
    for n, (giorno, oggetto, corpo, nota) in definitivi.items():
        assert t.CALENDARIO[n - 1][0] == giorno, n
        assert t.CALENDARIO[n - 1][1] == oggetto, n
        if n == t.EMAIL_GUIDA:
            continue   # il corpo: vedi test_02
        assert t.TESTI[n] == corpo, n
        assert nota == "", (n, nota)
        assert not t.problemi(n, t.TESTI[n]), n


#: Gli unici due adattamenti approvati dal titolare (2026-11-03, opzione 1):
#: il canale email spedisce la guida con un link, non come allegato.
ADATTAMENTI_EMAIL_6 = (
    ("Abbiamo raccolto questi argomenti nella guida gratuita STIMA360 che trovi in allegato.",
     "Abbiamo raccolto questi argomenti nella guida gratuita STIMA360 che puoi scaricare qui: {{link_guida}}."),
    ("Scarica l’allegato e parti dall’argomento più utile per te oggi.",
     "Scarica la guida gratuita e parti dall’argomento più utile per te oggi."),
)


def test_02_l_email_6_e_il_testo_definitivo_con_i_due_adattamenti_approvati():
    from communication import followup_v3_texts as t
    giorno, oggetto, corpo, nota = testi_definitivi()[6]
    assert giorno == 33 and nota == "Allegato: mantenere il PDF già previsto e verificarne la presenza."
    # il testo consegnato, cosi' com'e', il canale non lo puo' spedire
    assert t.problemi(6, corpo) == [
        "l'email 6 deve contenere {{link_guida}}",
        "l'email 6 non puo' promettere un allegato: il canale email spedisce il link"]
    atteso = corpo
    for prima, dopo in ADATTAMENTI_EMAIL_6:
        assert atteso.count(prima) == 1
        atteso = atteso.replace(prima, dopo)
    assert t.CALENDARIO[5] == (33, oggetto)
    assert t.TESTI[6] == atteso and not t.problemi(6, t.TESTI[6])
    assert "allegat" not in t.TESTI[6].lower() and t.TESTI[6].endswith("\n\n" + FIRMA)


def test_03_html_e_testo_dai_corpi_nuovi_con_link_reali():
    from communication import followup_v3_texts as t
    from communication.providers import email_brand_v3 as b
    url_ammessi = {"https://www.stima360.it"}
    for n in range(1, 17):
        m = _messaggio(n)
        testo = m["rendered_body"]
        assert testo.startswith("Ciao!\n\n")
        corpo = t.TESTI[n].replace("{{link_guida}}", f"{BASE}/api/public/communication/guida-vendere-casa-2026.pdf")
        assert testo.startswith(corpo + "\n\nAgenzia Prova\nsu Stima360\n"), n
        assert testo.rstrip().endswith(f"Se non vuoi piu' ricevere questi messaggi: {BASE}/api/public/communication/unsubscribe?t=tok{n}")
        # nel testo approvato solo indirizzi reali; i link tecnici li aggiunge il CRM
        assert set(re.findall(r"https?://[^\s]+", t.TESTI[n].replace("{{link_guida}}", ""))) <= url_ammessi, n
        h = b.html(m)
        nome, col, tinta, _ = b.colore(n)
        assert f'data-tappa="{n}"' in h and f'data-colore="{nome}"' in h and h.count(col) >= 3
        assert f'src="{BASE}/api/public/communication/logo-stima360.jpg"' in h
        assert f"<h1" in h and _html.escape(m["subject_snapshot"]) in h
        for riga in (r.strip() for r in corpo.splitlines()):
            if not riga:
                continue
            riga = "\u2022 " + riga[2:] if riga.startswith("* ") else riga
            # ogni frammento di testo (con escape) e ogni link della riga
            link = [u.rstrip(".,;:!?)]") for u in re.findall(r"https?://\S+", riga)]
            resto = riga
            for u in link:
                resto = resto.replace(u, "\n")
            for pezzo in filter(None, (x.strip() for x in resto.split("\n"))):
                assert _html.escape(pezzo) in h, (n, pezzo)
            for u in link:
                assert f'<a href="{_html.escape(u, quote=True)}">{_html.escape(u)}</a>' in h, (n, u)
        assert f'href="{BASE}/api/public/communication/unsubscribe?t=tok{n}"' in h
        assert 'href="https://www.stima360.it"' in h and "Agenzia Prova su Stima360" in h
        # ogni href e' un indirizzo reale: sito, disiscrizione, guida, risposta
        for href in re.findall(r'href="([^"]+)"', h):
            assert href == "https://www.stima360.it" or href.startswith(BASE + "/api/public/communication/") \
                or href == "mailto:info@stima360.it", (n, href)


def test_04_la_cta_segue_il_corpo():
    from communication import followup_v3_texts as t
    from communication.providers import email_brand_v3 as b
    for n in range(1, 17):
        etichetta, dove = b.CTA[n]
        chiede_risposta = bool(re.search(r"\b[Rr]ispondi\b", t.TESTI[n]))
        assert (dove == "risposta") == chiede_risposta, (n, etichetta, dove)
        h = b.html(_messaggio(n))
        assert _html.escape(etichetta) in h
        if dove == "risposta":
            assert 'href="mailto:info@stima360.it"' in h, n
        elif dove == "guida":
            assert n == 6 and f'href="{BASE}/api/public/communication/guida-vendere-casa-2026.pdf"' in h
        else:
            assert f'href="https://www.stima360.it" style="display:inline-block' in h, n
    assert [n for n in range(1, 17) if b.CTA[n][1] == "risposta"] == [4, 8, 10, 15]
    assert b.CTA[1][0] == "Scopri il modello STIMA360"
    assert {b.CTA[n][0] for n in (3, 12, 16)} == {"Richiedi una valutazione gratuita"}
    assert b.CTA[15][0] == "Rispondi con A, B, C o D"


def test_05_l_email_6_viaggia_con_il_link_del_pdf_e_senza_allegato(monkeypatch):
    from communication import guida_pdf
    from communication.providers import email_smtp
    chiamate = []
    monkeypatch.setattr(email_smtp, "invia_mail",
                        lambda dest, ogg, corpo_html, allegato=None, **kw: chiamate.append((allegato, corpo_html, kw)) or True)
    m = _messaggio(6)
    assert email_smtp.send(m).outcome == "accepted"
    allegato, corpo_html, kw = chiamate[0]
    link = f"{BASE}{guida_pdf.PERCORSO}"
    assert allegato is None, "il canale non allega: la guida e' un link"
    assert f"che puoi scaricare qui: {link}.\n" in kw["corpo_testo"]
    # il link nel testo e il pulsante giallo puntano al PDF, senza il punto finale
    assert corpo_html.count(f'href="{link}"') == 2 and f'href="{link}.' not in corpo_html
    assert f'href="{link}" style="display:inline-block;background:#F2C300' in corpo_html
    assert ">Scarica la guida gratuita</a>" in corpo_html
    assert guida_pdf.disponibile() and guida_pdf.url(BASE) == link
