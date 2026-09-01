"""CLI — one sub-app per funding-program family (renamed 2026-08-28).

The program is always the user's first token; procedures nest under it:

    invoice-controller eew cost-estimation <path>
    invoice-controller eew vne-generation <project>
    invoice-controller eew location-description <dir>
    invoice-controller beg vne-generation <project>
"""

from __future__ import annotations

from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table as RichTable

from invoice_controller.extract.classify import DocClass, classify_folder
from invoice_controller.extract.offer import extract_offer
from invoice_controller.models import DocumentKind
from invoice_controller.normalize import format_de_decimal
from invoice_controller.template.xlsx import write_kostenaufstellung

load_dotenv()

app = typer.Typer(no_args_is_help=True, add_completion=False)
eew_app = typer.Typer(no_args_is_help=True, help="EEW Modul 4 procedures")
beg_app = typer.Typer(no_args_is_help=True, help="BEG procedures (Effizienzhaus / Einzelmaßnahmen)")
app.add_typer(eew_app, name="eew")
app.add_typer(beg_app, name="beg")
console = Console()


DEFAULT_OUTPUT_NAME = "Kostenaufstellung.xlsx"


def _collect_offer_pdfs(path: Path) -> list[Path]:
    """Offer inputs for cost-estimation: an explicit single file is honoured as-is; a
    directory is classified (2026-08-28: the shared classifier replaced the old
    filename deny-list) and only offer-classified PDFs enter the run."""
    if not path.exists():
        raise typer.BadParameter(f"{path} does not exist")
    if path.is_file():
        if path.suffix.lower() != ".pdf":
            raise typer.BadParameter(f"{path} is not a PDF")
        return [path]  # explicit single file: honour the user's choice
    classified = [c for c in classify_folder(path, recursive=False) if c.path.suffix.lower() == ".pdf"]
    pdfs = [c.path for c in classified if c.doc_class is DocClass.OFFER]
    skipped = [c for c in classified if c.doc_class is not DocClass.OFFER]
    if skipped:
        console.print(
            f"[dim]Skipping {len(skipped)} non-offer PDF(s): "
            f"{', '.join(f'{c.path.name} ({c.doc_class.value})' for c in skipped)}[/dim]"
        )
    if not pdfs:
        raise typer.BadParameter(f"no offer PDFs found in {path}")
    return pdfs


def _default_output_for(path: Path) -> Path:
    """Place the output workbook alongside the offers — inside the directory if `path`
    is a directory, or alongside the PDF if `path` is a single file."""
    parent = path if path.is_dir() else path.parent
    return parent / DEFAULT_OUTPUT_NAME


def cost_estimation(
    path: Path = typer.Argument(..., help="Single offer PDF or directory of PDFs"),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output .xlsx path. Defaults to <offer-folder>/Kostenaufstellung.xlsx.",
    ),
) -> None:
    """EEW cost-estimation — extract offers and write Kostenaufstellung.xlsx."""
    pdfs = _collect_offer_pdfs(path)
    if output is None:
        output = _default_output_for(path)
    console.print(f"[bold]Processing {len(pdfs)} offer PDF(s)[/bold]")

    offers = []
    for pdf in pdfs:
        console.print(f"\n[cyan]→ {pdf.name}[/cyan]")
        offer = extract_offer(pdf)
        offers.append(offer)

        if offer.kind is DocumentKind.STATEMENT:
            tbl_title = (
                f"{offer.header.vendor_name} — Stellungnahme/Schätzung "
                f"({offer.header.offer_date.isoformat()})"
            )
        else:
            tbl_title = (
                f"{offer.header.vendor_name} — Angebot {offer.header.offer_number} "
                f"({offer.header.offer_date.isoformat()})"
            )
        tbl = RichTable(title=tbl_title, title_style="bold")
        tbl.add_column("Pos", style="dim")
        tbl.add_column("Description")
        tbl.add_column("Qty", justify="right")
        tbl.add_column("Unit")
        tbl.add_column("E-Preis", justify="right")
        tbl.add_column("G-Preis", justify="right")
        for p in offer.positions:
            desc = p.description[:60]
            if p.optional:
                desc = f"{desc} (optional)"
            tbl.add_row(
                p.pos,
                desc,
                format_de_decimal(p.qty) if p.qty is not None else "",
                p.unit or "",
                format_de_decimal(p.unit_price_net) if p.unit_price_net else "",
                format_de_decimal(p.line_total_net) if p.line_total_net is not None else "",
            )
        console.print(tbl)

        if offer.cross_sum.not_applicable:
            console.print(
                f"  [yellow]ⓘ Schätzung[/yellow]: kein Dokument-Gesamtbetrag — "
                f"Σ = {format_de_decimal(offer.cross_sum.actual)} EUR (netto angenommen, manuell zu prüfen)"
            )
        elif offer.cross_sum.passed:
            console.print(
                f"  [green]✓ Cross-sum OK[/green]: Σ = {format_de_decimal(offer.cross_sum.actual)} EUR "
                f"(Nettosumme = {format_de_decimal(offer.cross_sum.expected) if offer.cross_sum.expected else '?'})"
            )
        else:
            console.print(f"  [red]✗ Cross-sum FAILED[/red]: {offer.cross_sum.message}")

    write_kostenaufstellung(offers, output)
    console.print(f"\n[bold green]→ Wrote[/bold green] {output}")
    failed = [o for o in offers if not o.cross_sum.passed and not o.cross_sum.not_applicable]
    if failed:
        console.print(
            f"[bold red]⚠ {len(failed)} Block/Blöcke ohne Kreuzsummen-Abgleich[/bold red] — "
            "im .xlsx rot markiert, Positionen manuell gegen das PDF prüfen: "
            + ", ".join(o.source_path.name for o in failed)
        )


def vne_generation(
    project_dir: Path = typer.Argument(..., help="Project folder with offers + invoices (+ optional projekt.yaml)"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Output .xlsx (default: VNE-Tabelle.xlsx in the folder)"),
) -> None:
    """EEW vne-generation — classify folder, extract invoices, split by cost-estimation ratios, write VNE-Tabelle.xlsx."""
    from invoice_controller.config import load_project_config
    from invoice_controller.extract.vne import build_vne_tabelle
    from invoice_controller.vne.xlsx import write_vne_tabelle

    config = load_project_config(project_dir / "projekt.yaml")
    if config.client is None:
        console.print("[yellow]ⓘ kein projekt.yaml[/yellow] — Zeitraum-/Adressprüfung und Förderbetrag-Block entfallen")

    result = build_vne_tabelle(
        project_dir, config,
        on_progress=lambda msg: console.print(f"[cyan]→ {msg}[/cyan]"),
    )

    if result.ratio_source == "none":
        console.print(
            "[bold red]⚠ Keine IK/NK-Anteile verfügbar[/bold red]: weder eine Kostenaufstellung "
            "(.xlsx/.ods/PDF) noch Angebots-PDFs im Ordner gefunden. Alle Rechnungen werden ohne "
            "Aufteilung rot markiert — erst cost-estimation laufen lassen oder Angebote in den Ordner legen."
        )
    elif result.ratio_source == "live-extraction":
        console.print(
            "[yellow]ⓘ Keine vorhandene Kostenaufstellung gefunden[/yellow] — Live-Extraktion lief "
            "live über die Angebots-PDFs (unverifizierte Anteile; Kostenaufstellung prüfen)."
        )

    tbl = RichTable(title="VNE-Tabelle — Rechnungen", title_style="bold")
    tbl.add_column("Empfänger")
    tbl.add_column("Rg-Nr.")
    tbl.add_column("Datum")
    tbl.add_column("Netto", justify="right")
    tbl.add_column("IK", justify="right")
    tbl.add_column("NK", justify="right")
    tbl.add_column("EK", justify="right")
    tbl.add_column("Hinweis")
    for row in result.invoices:
        inv = row.invoice
        hint = "; ".join(row.flags)
        tbl.add_row(
            inv.vendor_name[:34],
            inv.invoice_number,
            inv.invoice_date.strftime("%d.%m.%Y"),
            format_de_decimal(inv.netto),
            format_de_decimal(row.ik_amount) if row.ik_amount else "",
            format_de_decimal(row.nk_amount) if row.nk_amount else "",
            format_de_decimal(row.ek_amount) if row.ek_amount else "",
            f"[red]⚠ {hint}[/red]" if hint else "[green]✓[/green]",
        )
    console.print(tbl)

    console.print(
        f"  Σ IK = {format_de_decimal(result.sum_ik)} €   Σ NK = {format_de_decimal(result.sum_nk)} €   "
        f"Σ EK = {format_de_decimal(result.sum_ek)} €   (Anteile aus {result.ratio_source})"
    )
    for vs in result.vendor_summaries:
        if vs.variance is not None:
            color = "green" if abs(vs.variance) < 1 else "yellow"
            console.print(
                f"  [{color}]{vs.vendor}: Rechnungen {format_de_decimal(vs.invoiced_netto)} € vs. "
                f"Angebot {format_de_decimal(vs.offer_sonderpreis or vs.offer_gesamt)} € "
                f"(Δ {format_de_decimal(vs.variance)} €)[/{color}]"
            )
    if result.ignored:
        console.print("[yellow]ⓘ ignoriert (keine Rechnung):[/yellow] " + ", ".join(p.name for p in result.ignored))
    # Per-invoice position cross-sum (2026-08-28): the cost-estimation-style per-file verdict.
    pos_failed = [
        r for r in result.invoices
        if r.invoice.position_check is not None and not r.invoice.position_check.passed
    ]
    if pos_failed:
        console.print(
            f"[bold red]✗ {len(pos_failed)} Rechnung(en) ohne Positions-Kreuzsummen-Abgleich[/bold red]: "
            + ", ".join(r.invoice.source_path.name for r in pos_failed)
        )
    flagged = [r for r in result.invoices if r.flags]
    if flagged:
        console.print(f"[bold red]⚠ {len(flagged)} Rechnung(en) mit Prüf-Hinweisen[/bold red] — im .xlsx rot markiert")

    if output is None:
        output = project_dir / "VNE-Tabelle.xlsx"
    write_vne_tabelle(result, config, output)
    console.print(f"\n[bold green]→ Wrote[/bold green] {output}")


def location_description(
    project_dir: Path = typer.Argument(
        None, help="Project folder containing a 'Fragenkatalog Modul 4' PDF (writes into it)"
    ),
    output: Path = typer.Option(None, "--output", "-o", help="Output .docx (default: <project>/Standortbeschreibung.docx)"),
    url: str = typer.Option(None, "--url", help="Client website URL to scrape"),
    input_md: Path = typer.Option(
        None, "--input", "-i", help="'Beschreibung Standort.md'-style header file (ad-hoc)"
    ),
    firma: str = typer.Option(None, "--firma", help="Company (Antragsteller) legal name"),
    strasse: str = typer.Option("", "--strasse", help="Street + number"),
    plz: str = typer.Option("", "--plz", help="Postal code"),
    stadt: str = typer.Option("", "--stadt", help="City"),
    betreiber: str = typer.Option(
        None, "--betreiber", help="Operating tenant (Untervermieter), if different from Antragsteller"
    ),
    schicht: int = typer.Option(None, "--schicht", help="Shift count override (1/2/3 → hours)"),
    offline: bool = typer.Option(False, "--offline", help="Skip OSM lookups (Kreis/roads)"),
) -> None:
    """EEW location-description — generate the client Standortbeschreibung (Antrag section 1.2) as a .docx.

    Preferred: pass a <project_dir> containing the filled 'Fragenkatalog Modul 4' PDF; the
    Standortbeschreibung.docx is written into that folder. Also accepts --input <md> or
    --firma/... for ad-hoc runs.
    """
    from invoice_controller.standort import (
        OperationalDefaults,
        StandortInput,
        generate_standort,
        write_standort_docx,
    )
    from invoice_controller.standort.fragenkatalog import (
        find_fragenkatalog,
        fragenkatalog_to_input,
        parse_fragenkatalog,
    )
    from invoice_controller.standort.input_md import parse_header

    if project_dir is not None:
        if not project_dir.is_dir():
            raise typer.BadParameter(f"{project_dir} is not a directory")
        fk_pdf = find_fragenkatalog(project_dir)
        if fk_pdf is None:
            raise typer.BadParameter(f"no 'Fragenkatalog*.pdf' found in {project_dir}")
        console.print(f"  [dim]Fragenkatalog:[/dim] {fk_pdf.name}")
        inp = fragenkatalog_to_input(parse_fragenkatalog(fk_pdf), website=url)
        if output is None:
            output = project_dir / "Standortbeschreibung.docx"
    elif input_md is not None:
        inp = parse_header(input_md, website=url)
    elif firma:
        inp = StandortInput(firma=firma, strasse=strasse, plz=plz, stadt=stadt, website=url)
    else:
        raise typer.BadParameter("provide <project_dir>, --input <file>, or --firma/--plz/--stadt")

    if output is None:
        raise typer.BadParameter("no output path — pass a <project_dir> or --output")
    if betreiber:
        inp.betreiberfirma = betreiber
    if schicht is not None:  # explicit override of the shift model → hours
        inp.operational = OperationalDefaults.from_shift(schicht)
    if url and not inp.website:
        inp.website = url

    described = inp.betreiberfirma or inp.firma
    console.print(f"[bold]location-description[/bold] — {described} ({inp.plz} {inp.stadt})")
    if not inp.website:
        console.print("  [yellow]⚠ no website URL — company profile will be empty[/yellow]")

    result = generate_standort(inp, online=not offline)
    write_standort_docx(result, inp, output)
    console.print(f"[bold green]→ Wrote[/bold green] {output}")
    for note in result.review_notes:
        console.print(f"  [yellow]⚠ {note}[/yellow]")


def beg_vne_generation(
    project_dir: Path = typer.Argument(..., help="BEG project folder (invoices, EKK documents, Zahlungsnachweise)"),
    output: Path | None = typer.Option(
        None, "--output", "-o",
        help="Output .xlsx (default: Kostenzusammenstellung_<folder>.xlsx in the project folder)",
    ),
    template: str | None = typer.Option(
        None, "--template",
        help="EH/EM template override when the project ships no Antragsbestätigung/Bescheid (eh|em)",
    ),
) -> None:
    """BEG vne-generation — classify the project and extract the funding parameters.

    Build stage B1: document classification + FundingMeta extraction. The
    Kostenzusammenstellung writer follows in stage B5.
    """
    from invoice_controller.beg.funding import extract_funding_meta

    if not project_dir.is_dir():
        raise typer.BadParameter(f"{project_dir} is not a directory")

    classified = classify_folder(project_dir)
    tbl = RichTable(title=f"BEG — {project_dir.name}: {len(classified)} Dokument(e)", title_style="bold")
    tbl.add_column("Klasse")
    tbl.add_column("Datei")
    tbl.add_column("Signal", style="dim")
    for c in sorted(classified, key=lambda c: (c.doc_class.value, c.path.name)):
        tbl.add_row(c.doc_class.value, str(c.path.relative_to(project_dir)), c.reason)
    console.print(tbl)

    from invoice_controller.beg.funding import BegProgramType, FundingMeta

    program_hint = BegProgramType(template.lower()) if template else None
    funding_docs = [
        c.path for c in classified
        if c.doc_class in (DocClass.ANTRAGSBESTAETIGUNG, DocClass.ZUWENDUNGSBESCHEID)
    ]
    if not funding_docs:
        console.print(
            "[bold red]⚠ Keine Antragsbestätigung / kein Zuwendungsbescheid gefunden[/bold red] — "
            "Programmdaten (Fördersatz, Vorgangsnummer, Basis) bleiben leer und werden rot markiert."
        )
        meta = FundingMeta()
    else:
        console.print(f"\n[cyan]→ Programmdaten aus {len(funding_docs)} Förderdokument(en) extrahieren[/cyan]")
        meta = extract_funding_meta(funding_docs)

    rows = [
        ("Programm", f"{meta.program_type.value}" + (f" — {meta.program_label}" if meta.program_label else "")),
        ("Vorgangsnummer", meta.vorgangsnummer),
        ("Antrag gestellt", meta.antrag_date.strftime("%d.%m.%Y") if meta.antrag_date else None),
        ("Zuwendungsbescheid", meta.bescheid_date.strftime("%d.%m.%Y") if meta.bescheid_date else None),
        ("Antragsteller", meta.antragsteller_name),
        ("Basis", f"{meta.client_basis.value}" + (f" ({meta.client_basis_reason})" if meta.client_basis_reason else "")),
        ("Kosten Maßnahmen lt. Antrag", format_de_decimal(meta.geplante_kosten_massnahmen) + " €" if meta.geplante_kosten_massnahmen is not None else None),
        ("Kosten Baubegleitung lt. Antrag", format_de_decimal(meta.geplante_kosten_baubegleitung) + " €" if meta.geplante_kosten_baubegleitung is not None else None),
        ("Fördersatz", f"{meta.foerdersatz_pct} % auf {format_de_decimal(meta.foerderfaehige_kosten_cap) + ' €' if meta.foerderfaehige_kosten_cap is not None else '?'}" + (f" ({meta.foerdersatz_zusammensetzung})" if meta.foerdersatz_zusammensetzung else "") if meta.foerdersatz_pct is not None else None),
        ("Fördersatz Baubegleitung", f"{meta.baubegleitung_foerdersatz_pct} % bis {format_de_decimal(meta.baubegleitung_kosten_cap) + ' €' if meta.baubegleitung_kosten_cap is not None else '?'}" if meta.baubegleitung_foerdersatz_pct is not None else None),
    ]
    meta_tbl = RichTable(title="Programmdaten (FundingMeta)", title_style="bold")
    meta_tbl.add_column("Feld")
    meta_tbl.add_column("Wert")
    for label, value in rows:
        meta_tbl.add_row(label, value if value is not None else "[red]fehlt[/red]")
    console.print(meta_tbl)

    missing = meta.missing_fields()
    if missing:
        console.print("[bold red]⚠ fehlende Programmdaten:[/bold red] " + ", ".join(missing))

    # B5: full pipeline — invoices, payments, Gewerke, EH/EM writer. Classification
    # and FundingMeta are handed in so their LLM work is not repeated.
    from invoice_controller.beg.payments import PaymentStatus
    from invoice_controller.extract.beg import build_kostenzusammenstellung

    console.print("\n[cyan]→ Rechnungen, Zahlungsnachweise und Gewerke verarbeiten[/cyan]")
    result = build_kostenzusammenstellung(
        project_dir,
        output_path=output,
        classified=classified,
        meta=meta,
        program_hint=program_hint,
        on_progress=lambda msg: console.print(f"  · {msg}", style="dim"),
    )

    row_tbl = RichTable(
        title=f"Kostenzusammenstellung — {len(result.table.rows)} Zeile(n)", title_style="bold"
    )
    for col in ("Gewerk", "Firma", "Re-Nr.", "Re-Betrag", "bezahlt", "Hinweise"):
        row_tbl.add_column(col)
    for row in result.table.rows:
        hints = "; ".join(row.flags) if row.flags else row.anmerkung_text
        row_tbl.add_row(
            row.gewerk,
            row.firma,
            row.re_nr,
            format_de_decimal(row.re_betrag) if row.re_betrag is not None else "[red]—[/red]",
            format_de_decimal(row.bezahlt) if row.bezahlt is not None else "[red]—[/red]",
            f"[red]⚠ {hints}[/red]" if row.flags else hints,
        )
    console.print(row_tbl)

    no_proof = sum(
        1 for r in result.reconciliations if r.status is PaymentStatus.NO_PROOF
    )
    if no_proof:
        console.print(f"[bold red]⚠ {no_proof} Rechnung(en) ohne Zahlungsnachweis[/bold red]")
    if result.duplicates:
        console.print(
            "[yellow]ⓘ Duplikate übersprungen:[/yellow] "
            + ", ".join(d.source_path.name for d in result.duplicates)
        )
    if result.unreadable:
        console.print("[bold red]⚠ nicht verarbeitbar:[/bold red]")
        for path, reason in result.unreadable:
            console.print(f"  ✗ {path.name}: {reason}")
    if result.ignored:
        console.print(
            f"[dim]ignoriert ({len(result.ignored)}): "
            + ", ".join(c.path.name for c in result.ignored[:10])
            + ("…" if len(result.ignored) > 10 else "")
            + "[/dim]"
        )
    console.print(f"\n[bold green]✓ geschrieben:[/bold green] {result.output_path}")


def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (0.0.0.0 for LAN)"),
    port: int = typer.Option(8080, "--port", help="Port"),
) -> None:
    """Web UI — select procedure, upload documents, run, download results.

    Optional password gate via IC_WEB_PASSWORD in .env (recommended before exposing
    the port through ngrok; additionally use `ngrok http --basic-auth "user:pw" <port>`)."""
    from invoice_controller.web.app import run_server

    console.print(f"[bold]Invoice-Controller Web-UI[/bold] → http://{host}:{port}")
    if not (host.startswith("127.") or host == "localhost") and not __import__("os").environ.get("IC_WEB_PASSWORD"):
        console.print("[yellow]⚠ Nicht-lokale Bindung ohne IC_WEB_PASSWORD — Zugang ist ungeschützt.[/yellow]")
    run_server(host=host, port=port)


# --- command registration: program sub-apps + hidden deprecated aliases -----------------

eew_app.command("cost-estimation")(cost_estimation)
eew_app.command("vne-generation")(vne_generation)
eew_app.command("location-description")(location_description)
beg_app.command("vne-generation")(beg_vne_generation)
app.command("serve")(serve)


if __name__ == "__main__":
    app()
