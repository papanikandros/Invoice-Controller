"""Close-out example corpus: the 10 named-client projects used as F1/F2 tests.

Each project ships the offers, all invoices, the consultant's F1 Kostenaufstellung
(PDF) and the F2 VNE-Tabelle (.xlsx). The readers here recover the expected
values so the pipelines can be regression-tested against real close-outs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CORPUS_ROOT = Path(__file__).resolve().parents[2] / "examples"

# Named-client close-out projects (offer + invoices + F1 + F2 ground truth).
PROJECT_IDS = [
    "Craemer", "Dannemann", "Jacob", "KRR", "Meyring",
    "NaturinForm", "Presspart", "Thess", "WHW", "ZePa",
]

_INVOICE_HINT = ("rg ", "rg_", "re-", "rg0", "rechnung", "gutschrift")
_OFFER_HINT = ("angebot", "-soll", " soll", "schätzung", "stellungnahme", "kostenvoranschlag")


@dataclass
class Project:
    id: str
    dir: Path
    offers: list[Path] = field(default_factory=list)
    invoices: list[Path] = field(default_factory=list)
    f1_pdf: Path | None = None
    vne_xlsx: Path | None = None


def _looks_like_invoice(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in _INVOICE_HINT) and "kostenaufstellung" not in low


def _looks_like_offer(name: str) -> bool:
    low = name.lower()
    if "kostenaufstellung" in low or "vne-tabelle" in low:
        return False
    return any(h in low for h in _OFFER_HINT)


def load_project(project_id: str) -> Project:
    from .kostenaufstellung import find_f1_pdf

    d = CORPUS_ROOT / project_id
    pdfs = sorted(d.glob("*.pdf")) + sorted(d.glob("*.PDF"))
    xlsx = sorted(d.glob("*.xlsx"))
    proj = Project(id=project_id, dir=d)
    proj.f1_pdf = find_f1_pdf(d)
    proj.vne_xlsx = xlsx[0] if xlsx else None
    for p in pdfs:
        if p == proj.f1_pdf:
            continue
        if _looks_like_invoice(p.name):
            proj.invoices.append(p)
        elif _looks_like_offer(p.name):
            proj.offers.append(p)
    return proj


def all_projects() -> list[Project]:
    return [load_project(pid) for pid in PROJECT_IDS]
