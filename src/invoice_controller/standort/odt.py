"""Write the F3 Standortbeschreibung to an .odt (ODF text) document."""

from __future__ import annotations

from pathlib import Path

from odfdo import Document, Header, Paragraph

from .models import Standortbeschreibung, StandortInput

SECTION_TITLE = "1.2 Beschreibung des Standortes"


def write_standort_odt(
    result: Standortbeschreibung, inp: StandortInput, out_path: str | Path
) -> Path:
    out_path = Path(out_path)
    doc = Document("text")
    body = doc.body
    body.clear()

    described = inp.betreiberfirma or inp.firma
    body.append(Header(1, described))
    addr = ", ".join(p for p in (inp.strasse, f"{inp.plz} {inp.stadt}".strip()) if p.strip())
    if addr:
        body.append(Paragraph(addr))
    body.append(Header(2, SECTION_TITLE))

    for block in result.text.split("\n\n"):
        for line in block.split("\n"):
            body.append(Paragraph(line))
        body.append(Paragraph(""))

    if result.review_notes:
        body.append(Header(2, "Hinweise zur Prüfung"))
        for note in result.review_notes:
            body.append(Paragraph(f"• {note}"))

    doc.save(str(out_path))
    return out_path
