"""Pre-fill the EEW VNE form from an uploaded Zuwendungsbescheid at UPLOAD time (todo #12,
2026-10-05) — not inside the run, where the colleague never sees what was adopted.

Pure pieces here (testable without a browser): the field mapping from the extracted
Bescheid meta + the deterministic Empfänger parse, and the never-overwrite merge. The
upload handler in web/app.py classifies the file, calls the (content-hash-cached)
extraction in a worker thread and `set_value()`s the inputs; the run later reuses the
cached meta, so the Bescheid is read exactly once per upload.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from invoice_controller.extract.bescheid import EewBescheidMeta
from invoice_controller.normalize import format_de_decimal

PREFILL_FIELDS = (
    "kunde_name", "kunde_adresse", "antragstellung", "bescheid_datiert",
    "zeitraum_von", "zeitraum_bis", "foerderbetrag", "foerderanteil", "kennung",
)
PREFILL_HINT = "aus Zuwendungsbescheid übernommen — bitte prüfen"


def bescheid_prefill(meta: EewBescheidMeta, empfaenger: tuple[str, str] | None = None) -> dict[str, str]:
    """Form values (German formats, as the colleague would type them) from the Bescheid.
    The deterministic Empfänger parse wins over the LLM's name (it is LLM-free)."""
    out: dict[str, str] = {}
    if empfaenger:
        out["kunde_name"], out["kunde_adresse"] = empfaenger
    elif meta.empfaenger_name:
        out["kunde_name"] = meta.empfaenger_name
        if meta.empfaenger_adresse:
            out["kunde_adresse"] = meta.empfaenger_adresse
    if meta.antrag_datum:
        out["antragstellung"] = f"{meta.antrag_datum:%d.%m.%Y}"
    if meta.bescheid_datum:
        out["bescheid_datiert"] = f"{meta.bescheid_datum:%d.%m.%Y}"
    if meta.bewilligungszeitraum_start:
        out["zeitraum_von"] = f"{meta.bewilligungszeitraum_start:%d.%m.%Y}"
    if meta.bewilligungszeitraum_end:
        out["zeitraum_bis"] = f"{meta.bewilligungszeitraum_end:%d.%m.%Y}"
    if meta.foerderbetrag is not None:
        out["foerderbetrag"] = format_de_decimal(meta.foerderbetrag)
    if meta.foerderanteil_pct is not None:
        # "40" / "37,5" — the form takes a percentage, not a money format
        txt = format(meta.foerderanteil_pct, "f")
        if "." in txt:
            txt = txt.rstrip("0").rstrip(".")
        out["foerderanteil"] = txt.replace(".", ",")
    if meta.kennung:
        out["kennung"] = meta.kennung
    return out


def funding_prefill(meta) -> dict[str, str]:
    """BEG tab (V7): the Antragsbestätigung/Bescheid extraction fills the date fields."""
    out: dict[str, str] = {}
    if meta.antrag_date:
        out["antragstellung"] = f"{meta.antrag_date:%d.%m.%Y}"
    if meta.bescheid_date:
        out["bescheid_datiert"] = f"{meta.bescheid_date:%d.%m.%Y}"
    return out


def apply_prefill(current: dict[str, str], new: dict[str, str]) -> dict[str, str]:
    """Only EMPTY fields are filled — a typed value always wins, and a later upload
    never overwrites an earlier value either."""
    return {k: v for k, v in new.items() if not (current.get(k) or "").strip()}


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def temp_upload_path(name: str, content: bytes) -> Path:
    """The classifier and the extractor read files, not bytes: a scratch copy under
    tmp/ (never the run directory — no job exists yet at upload time)."""
    base = Path("tmp") / "webuploads"
    base.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(dir=base))
    p = d / Path(name).name
    p.write_bytes(content)
    return p
