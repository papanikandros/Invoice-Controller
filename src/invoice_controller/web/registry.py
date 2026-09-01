"""Procedure registry for the web UI (U1): one declarative entry per procedure.

Each runner wraps the existing orchestrator, translating its results into the job's
event stream: per-file statuses, review FLAGS (yellow/red — not errors), outputs.
Genuine errors are raised (per-file ones recorded on the job and swallowed so one
bad file never kills a batch) and mapped by web/errors.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from invoice_controller.web.jobs import Job

ZAHLUNGSNACHWEIS_SUBFOLDER = "Zahlungsnachweise"


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    placeholder: str = ""
    options: tuple[str, ...] | None = None   # renders a select instead of an input


@dataclass(frozen=True)
class Procedure:
    key: str
    label: str
    upload_hint: str
    fields: tuple[FieldSpec, ...] = ()
    with_zahlungsnachweise: bool = False     # extra labeled dropzone
    accept: str = ".pdf"

    def run(self, job: Job, params: dict[str, str]) -> None:
        raise NotImplementedError


def _inputs(job: Job) -> Path:
    return job.run_dir / "inputs"


# --- eew cost-estimation ----------------------------------------------------------------


@dataclass(frozen=True)
class CostEstimation(Procedure):
    def run(self, job: Job, params: dict[str, str]) -> None:
        from invoice_controller.extract.classify import DocClass, classify_pdf
        from invoice_controller.extract.offer import extract_offer
        from invoice_controller.models import DocumentKind
        from invoice_controller.normalize import format_de_decimal
        from invoice_controller.template.xlsx import write_kostenaufstellung

        pdfs = sorted(_inputs(job).glob("*.pdf")) + sorted(_inputs(job).glob("*.PDF"))
        if not pdfs:
            raise ValueError("Keine PDF-Dateien hochgeladen.")

        offers = []
        for pdf in pdfs:
            cls = classify_pdf(pdf)
            if cls.doc_class is not DocClass.OFFER:
                job.file_status(pdf.name, "hinweis",
                                f"als '{cls.doc_class.value}' erkannt — übersprungen ({cls.reason})")
                continue
            job.file_status(pdf.name, "läuft")
            job.log(f"Angebot: {pdf.name}")
            try:
                offer = extract_offer(pdf)
            except Exception as exc:  # noqa: BLE001 — one bad file must not kill the batch
                job.add_error(exc, filename=pdf.name)
                continue
            offers.append(offer)
            if offer.cross_sum.not_applicable:
                job.file_status(pdf.name, "hinweis",
                                f"Schätzung — Σ {format_de_decimal(offer.cross_sum.actual)} € (manuell prüfen)")
            elif offer.cross_sum.passed:
                job.file_status(pdf.name, "ok",
                                f"Kreuzsumme OK — Σ {format_de_decimal(offer.cross_sum.actual)} €")
            else:
                job.file_status(pdf.name, "hinweis", "⚠ Kreuzsumme weicht ab — im .xlsx rot markiert")
                job.add_flag(f"{pdf.name}: Kreuzsumme weicht ab")
            if offer.grounding_check is not None and not offer.grounding_check.passed:
                job.add_flag(f"{pdf.name}: {offer.grounding_check.message}")

        if not offers:
            raise ValueError("Kein Angebot erfolgreich extrahiert — nichts zu schreiben.")
        out = job.run_dir / "Kostenaufstellung.xlsx"
        write_kostenaufstellung(offers, out)
        job.add_output(out)


# --- eew vne-generation -----------------------------------------------------------------


@dataclass(frozen=True)
class VneGeneration(Procedure):
    def run(self, job: Job, params: dict[str, str]) -> None:
        from invoice_controller.config import load_project_config
        from invoice_controller.extract.vne import build_vne_tabelle
        from invoice_controller.vne.xlsx import write_vne_tabelle

        config = load_project_config(_inputs(job) / "projekt.yaml")
        if config.client is None:
            job.add_flag("kein projekt.yaml hochgeladen — Zeitraum-/Adressprüfung und Förderbetrag-Block entfallen")

        result = build_vne_tabelle(_inputs(job), config, on_progress=job.log)

        if result.ratio_source == "none":
            job.add_flag("Keine IK/NK-Anteile verfügbar (keine Kostenaufstellung/Angebote hochgeladen) — "
                         "alle Rechnungen ohne Aufteilung, rot markiert")
        elif result.ratio_source == "live-extraction":
            job.add_flag("Anteile aus Live-Extraktion der Angebote (unverifiziert — Kostenaufstellung prüfen)")

        for row in result.invoices:
            name = row.invoice.source_path.name
            if row.unusable:
                job.file_status(name, "hinweis", "; ".join(row.flags) or "nicht verwertbar")
            elif row.flags:
                job.file_status(name, "hinweis", "; ".join(row.flags))
                job.add_flag(f"{name}: {'; '.join(row.flags)}")
            else:
                job.file_status(name, "ok")
        for path in result.ignored:
            job.file_status(path.name, "hinweis", "nicht Rechnung/Angebot — ignoriert")

        out = job.run_dir / "VNE-Tabelle.xlsx"
        write_vne_tabelle(result, config, out)
        job.add_output(out)


# --- beg vne-generation -----------------------------------------------------------------


@dataclass(frozen=True)
class BegVneGeneration(Procedure):
    def run(self, job: Job, params: dict[str, str]) -> None:
        from invoice_controller.beg.funding import BegProgramType
        from invoice_controller.beg.payments import PaymentStatus
        from invoice_controller.extract.beg import build_kostenzusammenstellung

        hint = params.get("template") or None
        result = build_kostenzusammenstellung(
            _inputs(job),
            output_path=job.run_dir / "Kostenzusammenstellung.xlsx",
            program_hint=BegProgramType(hint) if hint and hint != "automatisch" else None,
            on_progress=job.log,
        )

        missing = result.meta.missing_fields()
        if missing:
            job.add_flag("fehlende Programmdaten (rot im Blatt): " + ", ".join(missing))
        for row in result.table.rows:
            name = row.source_path.name if row.source_path else row.re_nr
            if row.flags:
                job.file_status(name, "hinweis", "; ".join(row.flags))
                job.add_flag(f"{row.firma} {row.re_nr}: {'; '.join(row.flags)}")
            else:
                job.file_status(name, "ok", f"{row.firma} — {row.re_nr}")
        no_proof = sum(1 for r in result.reconciliations if r.status is PaymentStatus.NO_PROOF)
        if no_proof:
            job.add_flag(f"{no_proof} Rechnung(en) ohne Zahlungsnachweis")
        for dup in result.duplicates:
            job.file_status(dup.source_path.name, "hinweis", "Duplikat — übersprungen")
        for path, reason in result.unreadable:
            job.add_error(RuntimeError(reason), filename=path.name)
        for c in result.ignored:
            job.file_status(c.path.name, "hinweis", f"'{c.doc_class.value}' — ignoriert")

        job.add_output(result.output_path)


# --- eew location-description -----------------------------------------------------------


@dataclass(frozen=True)
class LocationDescription(Procedure):
    def run(self, job: Job, params: dict[str, str]) -> None:
        from invoice_controller.standort import generate_standort, write_standort_docx
        from invoice_controller.standort.fragenkatalog import (
            find_fragenkatalog,
            fragenkatalog_to_input,
            parse_fragenkatalog,
        )

        fk = find_fragenkatalog(_inputs(job))
        if fk is None:
            raise ValueError("Kein 'Fragenkatalog*.pdf' hochgeladen — die Datei ist die Pflicht-Eingabe.")
        job.file_status(fk.name, "läuft")
        url = params.get("url", "").strip() or None
        if not url:
            job.add_flag("keine Website-URL angegeben — Firmenprofil bleibt leer")

        inp = fragenkatalog_to_input(parse_fragenkatalog(fk), website=url)
        job.log(f"Standortbeschreibung für {inp.betreiberfirma or inp.firma} ({inp.plz} {inp.stadt})")
        result = generate_standort(inp)
        for note in result.review_notes:
            job.add_flag(note)
        job.file_status(fk.name, "ok")

        out = job.run_dir / "Standortbeschreibung.docx"
        write_standort_docx(result, inp, out)
        job.add_output(out)


PROCEDURES: tuple[Procedure, ...] = (
    CostEstimation(
        key="eew-cost-estimation",
        label="EEW Kostenaufstellung (cost-estimation)",
        upload_hint="Angebots-PDFs des Projekts (auch Stellungnahmen/Schätzungen; Scans erlaubt)",
    ),
    VneGeneration(
        key="eew-vne-generation",
        label="EEW VNE-Tabelle (vne-generation)",
        upload_hint="Alle Rechnungen + Angebote ODER die geprüfte Kostenaufstellung.xlsx; optional projekt.yaml",
        accept=".pdf,.xlsx,.ods,.yaml,.yml",
        with_zahlungsnachweise=False,
    ),
    BegVneGeneration(
        key="beg-vne-generation",
        label="BEG Kostenzusammenstellung (vne-generation)",
        upload_hint="Rechnungen + Antragsbestätigung/BzA + Zuwendungsbescheid (PDFs)",
        fields=(FieldSpec("template", "Vorlage", options=("automatisch", "eh", "em")),),
        with_zahlungsnachweise=True,
        accept=".pdf,.png,.jpg,.jpeg",
    ),
    LocationDescription(
        key="eew-location-description",
        label="EEW Standortbeschreibung (location-description)",
        upload_hint="Der ausgefüllte 'Fragenkatalog Modul 4' (PDF)",
        fields=(FieldSpec("url", "Website des Kunden", placeholder="z. B. beispiel-gmbh.de"),),
    ),
)
