"""BLOCCO-1B - il logo dell'agenzia: decodifica e normalizzazione con Pillow.

Non si salva mai il file caricato. Si salva una versione NORMALIZZATA, ottenuta
cosi':

1. dimensione in ingresso: non vuoto, al massimo 2 MB;
2. `Image.open` con i soli formati PNG e JPEG: legge l'intestazione, NON i
   pixel. Il formato e' quello che Pillow riconosce dal contenuto, mai
   l'estensione o il MIME dichiarato dal browser;
3. dimensioni e numero di pixel dall'intestazione, PRIMA di decodificare
   (decompression bomb): al massimo 4096 pixel per lato. Oltre la soglia
   propria di Pillow, `Image.open` stesso rifiuta con DecompressionBombError;
4. immagini animate o con piu' fotogrammi (APNG, MPO) rifiutate;
5. decodifica completa (`load()`): un errore di decodifica e' un rifiuto;
6. orientamento EXIF applicato, conversioni di colore esplicite, ricodifica
   con Pillow senza metadati (EXIF, ICC, testi), e limite di 2 MB anche sui
   byte prodotti.

MIME, dimensioni e SHA-256 si calcolano sui byte normalizzati, cioe' su quelli
salvati.

COSA NON SI PROMETTE

La garanzia e' un'immagine decodificabile e normalizzata, ottenuta con risorse
limitate. Non e' il riconoscimento di qualunque alterazione dei pixel: libjpeg
tratta alcuni difetti di un JPEG come semplici avvisi (dati che finiscono prima
del previsto, un marker di restart fuori sequenza, byte alterati che formano
comunque codici validi) e Pillow restituisce un'immagine decodificata, con le
zone mancanti riempite. Quel risultato e' un'immagine valida e viene salvato
normalizzato: e' l'operatore a vedere nell'anteprima se il logo e' quello
giusto. Lo stesso vale per un PNG i cui dati compressi decodificano senza
errori. Il file originale, difetti compresi, non viene mai conservato.

Le impostazioni globali di Pillow non vengono toccate: `LOAD_TRUNCATED_IMAGES`
resta spenta e, se qualcuno nel processo la accendesse, la normalizzazione si
ferma invece di accettare file troncati.
"""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass

from .enums import (
    CONTENT_TYPE_JPEG,
    CONTENT_TYPE_PNG,
    LOGO_ANIMATED_MESSAGE,
    LOGO_CORRUPTED_MESSAGE,
    LOGO_DIMENSIONS_MESSAGE,
    LOGO_EMPTY_MESSAGE,
    LOGO_MAX_BYTES,
    LOGO_MAX_PIXELS,
    LOGO_MAX_SIDE,
    LOGO_MAX_STORED_BYTES,
    LOGO_OUTPUT_TOO_LARGE_MESSAGE,
    LOGO_TOO_LARGE_MESSAGE,
    LOGO_UNSUPPORTED_MESSAGE,
)

#: Il formato di Pillow -> il MIME salvato. Il formato in uscita e' quello in
#: ingresso: un PNG resta PNG (trasparenza), un JPEG resta JPEG.
_FORMATS = {"PNG": CONTENT_TYPE_PNG, "JPEG": CONTENT_TYPE_JPEG}

#: Qualita' della ricodifica JPEG: alta, perche' un logo ha bordi netti.
JPEG_QUALITY = 90


class InvalidLogo(ValueError):
    """Un file rifiutato. `code` e' stabile per i test e il client."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NormalizedLogo:
    content_type: str
    width: int
    height: int
    data: bytes


def _corrupted() -> InvalidLogo:
    return InvalidLogo("corrupted", LOGO_CORRUPTED_MESSAGE)


def normalize_logo(data: bytes) -> NormalizedLogo:
    """Il logo pronto da salvare, o InvalidLogo. Nessun effetto collaterale."""
    if not data:
        raise InvalidLogo("empty", LOGO_EMPTY_MESSAGE)
    if len(data) > LOGO_MAX_BYTES:
        raise InvalidLogo("too_large", LOGO_TOO_LARGE_MESSAGE)

    from PIL import Image, ImageFile, UnidentifiedImageError

    if ImageFile.LOAD_TRUNCATED_IMAGES:
        # Qualcuno nel processo ha acceso l'accettazione dei file troncati:
        # normalizzare in quelle condizioni darebbe per buono un file rotto.
        raise RuntimeError("Pillow accetta immagini troncate: normalizzazione del logo sospesa")

    try:
        with Image.open(io.BytesIO(data), formats=list(_FORMATS)) as image:
            content_type = _FORMATS.get(image.format)
            if content_type is None:
                # Un JPEG multi-immagine (MPO) si presenta come "MPO".
                raise InvalidLogo("animated", LOGO_ANIMATED_MESSAGE)
            _check_dimensions(*image.size)
            if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
                raise InvalidLogo("animated", LOGO_ANIMATED_MESSAGE)
            image.load()
            normalized = _normalize(image, content_type)
    except InvalidLogo:
        raise
    except UnidentifiedImageError as exc:
        raise InvalidLogo("unsupported", LOGO_UNSUPPORTED_MESSAGE) from exc
    except Image.DecompressionBombError as exc:
        raise InvalidLogo("dimensions", LOGO_DIMENSIONS_MESSAGE) from exc
    except (OSError, SyntaxError, ValueError, EOFError, struct.error) as exc:
        raise _corrupted() from exc

    if len(normalized.data) > LOGO_MAX_STORED_BYTES:
        raise InvalidLogo("output_too_large", LOGO_OUTPUT_TOO_LARGE_MESSAGE)
    return normalized


def _check_dimensions(width: int, height: int) -> None:
    if width < 1 or height < 1:
        raise _corrupted()
    if width > LOGO_MAX_SIDE or height > LOGO_MAX_SIDE or width * height > LOGO_MAX_PIXELS:
        raise InvalidLogo("dimensions", LOGO_DIMENSIONS_MESSAGE)


def _normalize(image, content_type: str) -> NormalizedLogo:
    from PIL import ImageOps

    # Orientamento EXIF applicato ai pixel: il file salvato non ha EXIF, quindi
    # l'immagine deve gia' essere dritta. Larghezza e altezza seguono.
    oriented = ImageOps.exif_transpose(image)
    if content_type == CONTENT_TYPE_PNG:
        converted, save = _png_mode(oriented), {"format": "PNG", "optimize": True}
    else:
        converted, save = _jpeg_mode(oriented), {"format": "JPEG", "quality": JPEG_QUALITY, "optimize": True}
    out = io.BytesIO()
    # Nessun `exif`, `icc_profile`, `pnginfo` o `dpi`: Pillow non scrive
    # metadati che non gli vengono passati.
    converted.save(out, **save)
    return NormalizedLogo(content_type, converted.width, converted.height, out.getvalue())


def _png_mode(image):
    """PNG: la trasparenza resta (canale alfa esplicito), i 16 bit diventano 8."""
    mode = image.mode
    if "transparency" in image.info or mode in ("P", "PA", "LA", "RGBA"):
        if mode == "LA":
            return image
        return image.convert("RGBA")
    if mode in ("1", "L", "RGB"):
        return image
    if mode.startswith("I"):
        # Scala di grigi a 16 bit (o intera): riportata a 8 bit dividendo,
        # non tagliando (`convert("L")` diretto saturerebbe oltre 255).
        return image.convert("I").point(lambda v: v * (1 / 256)).convert("L")
    return image.convert("RGB")


def _jpeg_mode(image):
    """JPEG: niente alfa. Scala di grigi resta tale; CMYK e YCbCr diventano RGB
    con la conversione di Pillow (senza profilo ICC: e' la conversione
    standard, non una gestione del colore calibrata)."""
    if image.mode in ("L", "RGB"):
        return image
    return image.convert("RGB")
