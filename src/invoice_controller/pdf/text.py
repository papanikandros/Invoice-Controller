from __future__ import annotations

from pathlib import Path

import pdfplumber


def extract_pages(path: Path) -> list[str]:
    pages: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(layout=True, x_density=7.25) or ""
            pages.append(text)
    return pages
