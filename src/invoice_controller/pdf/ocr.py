"""Tiered fallback for scanned (image-only) PDFs that have no text layer.

extract_pages() returns empty strings for a scan, which would feed the LLM nothing. The
recovery order, decided with the consultant:

  1. LOCAL OCR (Tesseract) — keeps client scans on-prem. Used only when a local engine is
     actually installed (pytesseract + the tesseract binary + a German language pack). If the
     engine is absent or yields no usable text, we fall through.
  2. CLOUD VISION-LLM — render each page to a PNG and let the multimodal model read it
     directly (handled in llm/extract.py). This sends the scan image to the cloud, the same
     confidentiality boundary F1 already crosses by sending offer text to the cloud LLM.

The cross-sum check remains the guardrail for offers; for statements the consultant's review
is, as always, the backstop.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pypdfium2 as pdfium

# Below this many non-whitespace characters across all pages, a "text layer" is treated as
# absent (a scan, or an OCR pass that produced nothing usable).
_MIN_TEXT_CHARS = 20

# Render resolution for OCR / vision. 200 DPI is a good accuracy/size trade-off for A4 letters.
_RENDER_DPI = 200


def is_text_layer_empty(pages: list[str]) -> bool:
    """True when the extracted pages hold essentially no text (an image-only scan)."""
    return sum(len(p.strip()) for p in pages) < _MIN_TEXT_CHARS


def render_page_pngs(path: Path, dpi: int = _RENDER_DPI) -> list[bytes]:
    """Render every page to PNG bytes via pypdfium2 (no system dependency, non-AGPL)."""
    import io

    pngs: list[bytes] = []
    pdf = pdfium.PdfDocument(str(path))
    try:
        for page in pdf:
            bitmap = page.render(scale=dpi / 72)
            image = bitmap.to_pil()
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            pngs.append(buf.getvalue())
    finally:
        pdf.close()
    return pngs


@lru_cache(maxsize=1)
def _tesseract() -> object | None:
    """Return the pytesseract module if both it and the tesseract binary are usable, else None."""
    try:
        import pytesseract
    except ImportError:
        return None
    try:
        pytesseract.get_tesseract_version()
    except Exception:  # noqa: BLE001 — binary missing / not on PATH
        return None
    module: object = pytesseract
    return module


def local_ocr_text(path: Path, dpi: int = _RENDER_DPI, lang: str = "deu") -> list[str] | None:
    """OCR each page locally with Tesseract. Returns one text string per page, or None when no
    local engine is available (caller then falls back to the cloud vision path). German is the
    default language; falls back to the engine default if the 'deu' pack is missing."""
    pt = _tesseract()
    if pt is None:
        return None

    pages: list[str] = []
    pdf = pdfium.PdfDocument(str(path))
    try:
        for page in pdf:
            image = page.render(scale=dpi / 72).to_pil()
            try:
                text = pt.image_to_string(image, lang=lang)  # type: ignore[attr-defined]
            except pt.TesseractError:  # type: ignore[attr-defined]
                # Language pack missing — retry with the engine default rather than failing.
                text = pt.image_to_string(image)  # type: ignore[attr-defined]
            pages.append(text)
    finally:
        pdf.close()
    return pages
