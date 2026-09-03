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
    required: bool = False


# Every procedure carries the project name — it feeds the job title and the dated
# output filenames (naming decision Q3, 2026-09-03: <Name>_<Projekt>_<YYYY-MM-DD>).
PROJEKT_FIELD = FieldSpec("projekt", "Projekt", placeholder="z. B. EK4_204 Wirox", required=True)


def _projekt(params: dict[str, str]) -> str:
    from datetime import date

    name = (params.get("projekt") or "").strip().replace("/", "-")
    if not name:
        raise ValueError("Projekt-Name fehlt — bitte angeben (z. B. EK4_204).")
    return f"{name}_{date.today():%Y-%m-%d}"


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
        out = job.run_dir / f"Kostenaufstellung_{_projekt(params)}.xlsx"
        write_kostenaufstellung(offers, out)
        job.add_output(out)


# --- eew vne-generation -----------------------------------------------------------------


def vne_config_from_params(params: dict[str, str]) -> tuple["object", list[str]]:
    """Build the ProjektConfig from the UI fields (decided 2026-09-03: input fields
    replace the projekt.yaml upload). German number/date formats; empty fields keep
    the same graceful degradation as a missing yaml, each named in a flag."""
    from invoice_controller.config import BescheidConfig, ClientConfig, ProjektConfig
    from invoice_controller.normalize import parse_de_date, parse_de_decimal

    def txt(key: str) -> str | None:
        v = (params.get(key) or "").strip()
        return v or None

    def de_date(key: str, label: str):
        v = txt(key)
        if v is None:
            return None
        try:
            return parse_de_date(v)
        except Exception as exc:
            raise ValueError(f"{label}: Datum nicht lesbar ({v!r}) — Format TT.MM.JJJJ") from exc

    def de_dec(key: str, label: str):
        v = txt(key)
        if v is None:
            return None
        try:
            return parse_de_decimal(v)
        except Exception as exc:
            raise ValueError(f"{label}: Zahl nicht lesbar ({v!r})") from exc

    flags: list[str] = []
    client = None
    if txt("kunde_name"):
        client = ClientConfig(name=txt("kunde_name"), address=txt("kunde_adresse"))
    else:
        flags.append("Kunde nicht angegeben — Adressprüfung entfällt")

    start = de_date("zeitraum_von", "Bewilligungszeitraum von")
    end = de_date("zeitraum_bis", "Bewilligungszeitraum bis")
    if start is None or end is None:
        flags.append("Bewilligungszeitraum unvollständig — Zeitraum-Prüfung entfällt")

    foerderbetrag = de_dec("foerderbetrag", "Förderbetrag lt. Bescheid")
    anteil_pct = de_dec("foerderanteil", "Kostendeckel-Förderanteil")
    agvo = de_dec("agvo", "AGVO-Referenzkosten")
    bescheid = None
    if any(v is not None for v in (foerderbetrag, anteil_pct, agvo)):
        bescheid = BescheidConfig(
            foerderbetrag=foerderbetrag,
            kostendeckel_foerderanteil=(anteil_pct / 100) if anteil_pct is not None else None,
            agvo_referenzkosten=agvo,
        )
    else:
        flags.append("Bescheid-Werte nicht angegeben — Förderbetrag-Block bleibt leer")

    return ProjektConfig(
        client=client,
        bewilligungszeitraum_start=start,
        bewilligungszeitraum_end=end,
        bescheid=bescheid,
    ), flags


def merge_bescheid_into_config(config, meta) -> list[str]:
    """Fill config gaps from the extracted Zuwendungsbescheid — typed UI fields ALWAYS
    win; every adopted value is reported with provenance. Mutates config, returns the
    provenance notes."""
    from invoice_controller.config import BescheidConfig, ClientConfig
    from invoice_controller.normalize import format_de_decimal

    adopted: list[str] = []
    if config.client is None and meta.empfaenger_name:
        config.client = ClientConfig(name=meta.empfaenger_name, address=meta.empfaenger_adresse)
        adopted.append(f"Kunde: {meta.empfaenger_name}")
    if config.bewilligungszeitraum_start is None and meta.bewilligungszeitraum_start:
        config.bewilligungszeitraum_start = meta.bewilligungszeitraum_start
        adopted.append(f"Zeitraum von {meta.bewilligungszeitraum_start:%d.%m.%Y}")
    if config.bewilligungszeitraum_end is None and meta.bewilligungszeitraum_end:
        config.bewilligungszeitraum_end = meta.bewilligungszeitraum_end
        adopted.append(f"Zeitraum bis {meta.bewilligungszeitraum_end:%d.%m.%Y}")
    if config.bescheid is None:
        config.bescheid = BescheidConfig()
    if config.bescheid.foerderbetrag is None and meta.foerderbetrag is not None:
        config.bescheid.foerderbetrag = meta.foerderbetrag
        adopted.append(f"Förderbetrag {format_de_decimal(meta.foerderbetrag)} €")
    if config.bescheid.kostendeckel_foerderanteil is None and meta.foerderanteil_pct is not None:
        config.bescheid.kostendeckel_foerderanteil = meta.foerderanteil_pct / 100
        adopted.append(f"Förderanteil {format_de_decimal(meta.foerderanteil_pct)} %")
    return adopted


@dataclass(frozen=True)
class VneGeneration(Procedure):
    def run(self, job: Job, params: dict[str, str]) -> None:
        from invoice_controller.extract.classify import DocClass, classify_folder
        from invoice_controller.extract.vne import build_vne_tabelle
        from invoice_controller.vne.xlsx import write_vne_tabelle

        config, config_flags = vne_config_from_params(params)

        # An uploaded Zuwendungsbescheid pre-fills what the colleague did not type
        # (2026-09-03, user request — mirrors the BEG procedure's document-first rule).
        bescheid_docs = [
            c.path for c in classify_folder(_inputs(job))
            if c.doc_class is DocClass.ZUWENDUNGSBESCHEID
        ]
        if bescheid_docs:
            from invoice_controller.extract.bescheid import extract_eew_bescheid

            job.log(f"Zuwendungsbescheid: {', '.join(d.name for d in bescheid_docs)}")
            meta = extract_eew_bescheid(bescheid_docs)
            adopted = merge_bescheid_into_config(config, meta)
            if adopted:
                job.add_flag("aus Zuwendungsbescheid übernommen: " + "; ".join(adopted))
            # Degradation flags may no longer apply after the merge.
            config_flags = [
                f for f in config_flags
                if not (("Zeitraum" in f and config.has_window)
                        or ("Adressprüfung" in f and config.client is not None)
                        or ("Förderbetrag-Block" in f and config.bescheid is not None
                            and config.bescheid.foerderbetrag is not None))
            ]
        for flag in config_flags:
            job.add_flag(flag)

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

        out = job.run_dir / f"VNE-Tabelle_{_projekt(params)}.xlsx"
        write_vne_tabelle(result, config, out)
        job.add_output(out)


# --- beg vne-generation -----------------------------------------------------------------


@dataclass(frozen=True)
class BegVneGeneration(Procedure):
    def run(self, job: Job, params: dict[str, str]) -> None:
        from invoice_controller.beg.funding import BegProgramType
        from invoice_controller.beg.payments import PaymentStatus
        from invoice_controller.extract.beg import build_kostenzusammenstellung

        # The Vorlage select carries explanatory labels; map the leading word.
        template = (params.get("template") or "").split(" ")[0].lower()
        hint = {"effizienzhaus": BegProgramType.EH, "einzelmaßnahme": BegProgramType.EM}.get(template)
        result = build_kostenzusammenstellung(
            _inputs(job),
            output_path=job.run_dir / f"Kostenzusammenstellung_{_projekt(params)}.xlsx",
            program_hint=hint,
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

        out = job.run_dir / f"Standortbeschreibung_{(params.get('projekt') or '').strip().replace('/', '-') or 'Projekt'}.docx"
        write_standort_docx(result, inp, out)
        job.add_output(out)


PROCEDURES: tuple[Procedure, ...] = (
    CostEstimation(
        key="eew-cost-estimation",
        label="EEW Kostenaufstellung (cost-estimation)",
        upload_hint="Angebots-PDFs des Projekts (auch Stellungnahmen/Schätzungen; Scans erlaubt)",
        fields=(PROJEKT_FIELD,),
    ),
    VneGeneration(
        key="eew-vne-generation",
        label="EEW VNE-Tabelle (vne-generation)",
        upload_hint="Alle Rechnungen + Angebote ODER die geprüfte Kostenaufstellung.xlsx; "
                    "optional den Zuwendungsbescheid (füllt leere Felder unten automatisch)",
        accept=".pdf,.xlsx,.ods",
        fields=(
            PROJEKT_FIELD,
            FieldSpec("kunde_name", "Kunde (Name)", placeholder="z. B. ZePa GmbH"),
            FieldSpec("kunde_adresse", "Kunde (Adresse)", placeholder="Straße Nr., PLZ Ort"),
            FieldSpec("zeitraum_von", "Bewilligungszeitraum von", placeholder="TT.MM.JJJJ"),
            FieldSpec("zeitraum_bis", "Bewilligungszeitraum bis", placeholder="TT.MM.JJJJ"),
            FieldSpec("foerderbetrag", "Förderbetrag lt. Bescheid (€)", placeholder="z. B. 45.000,00"),
            FieldSpec("foerderanteil", "Kostendeckel-Förderanteil (%)", placeholder="z. B. 40"),
            FieldSpec("agvo", "AGVO-Referenzkosten (€)", placeholder="optional"),
        ),
        with_zahlungsnachweise=False,
    ),
    BegVneGeneration(
        key="beg-vne-generation",
        label="BEG Kostenzusammenstellung (vne-generation)",
        upload_hint="Rechnungen + Antragsbestätigung/BzA + Zuwendungsbescheid (PDFs)",
        fields=(PROJEKT_FIELD, FieldSpec(
            "template", "Vorlage",
            options=(
                "automatisch (aus Antragsbestätigung/Bescheid erkannt)",
                "Effizienzhaus (KfW, Bestätigung nach Durchführung)",
                "Einzelmaßnahme (BAFA/KfW 458, Technischer Projektnachweis)",
            ),
        )),
        with_zahlungsnachweise=True,
        accept=".pdf,.png,.jpg,.jpeg",
    ),
    LocationDescription(
        key="eew-location-description",
        label="EEW Standortbeschreibung (location-description)",
        upload_hint="Der ausgefüllte 'Fragenkatalog Modul 4' (PDF)",
        fields=(
            PROJEKT_FIELD,
            FieldSpec("url", "Website des Kunden", placeholder="z. B. beispiel-gmbh.de"),
        ),
    ),
)
