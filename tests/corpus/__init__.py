"""Close-out example corpus: the 10 named-client projects used as cost-estimation/vne-generation tests.

Each project ships the offers, all invoices, the consultant's cost-estimation Kostenaufstellung
(PDF) and the vne-generation VNE-Tabelle (.xlsx). The readers here recover the expected
values so the pipelines can be regression-tested against real close-outs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CORPUS_ROOT = Path(__file__).resolve().parents[2] / "examples"

# The named-client close-out projects (offer + invoices + cost-estimation + vne-generation ground truth) are real
# BAFA clients, so the roster — and the set of projects with a consultant category override —
# live in a gitignored manifest beside the fixtures rather than in committed source. Absent on
# a fresh clone → empty, and the corpus tests skip.
_ROSTER = Path(__file__).resolve().parents[1] / "fixtures" / "corpus_projects.json"
_roster = json.loads(_ROSTER.read_text()) if _ROSTER.exists() else {}
PROJECT_IDS: list[str] = _roster.get("projects", [])
# Projects where the consultant overrode per-invoice categorization away from the cost-estimation offer
# ratio; vne-generation needs a projekt.yaml categorization override to reproduce these.
CATEGORY_OVERRIDE: set[str] = set(_roster.get("category_override", []))

_INVOICE_HINT = ("rg ", "rg_", "re-", "rg0", "rechnung", "gutschrift")
_OFFER_HINT = ("angebot", "-soll", " soll", "schätzung", "stellungnahme", "kostenvoranschlag")


@dataclass
class Project:
    id: str
    dir: Path
    offers: list[Path] = field(default_factory=list)
    invoices: list[Path] = field(default_factory=list)
    kostenaufstellung_pdf: Path | None = None
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
    from .kostenaufstellung import find_kostenaufstellung_pdf

    d = CORPUS_ROOT / project_id
    pdfs = sorted(d.glob("*.pdf")) + sorted(d.glob("*.PDF"))
    xlsx = sorted(d.glob("*.xlsx"))
    proj = Project(id=project_id, dir=d)
    proj.kostenaufstellung_pdf = find_kostenaufstellung_pdf(d)
    proj.vne_xlsx = xlsx[0] if xlsx else None
    for p in pdfs:
        if p == proj.kostenaufstellung_pdf:
            continue
        if _looks_like_invoice(p.name):
            proj.invoices.append(p)
        elif _looks_like_offer(p.name):
            proj.offers.append(p)
    return proj


def all_projects() -> list[Project]:
    return [load_project(pid) for pid in PROJECT_IDS]
