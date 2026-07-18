from __future__ import annotations

from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table as RichTable

from invoice_controller.extract.offer import extract_offer
from invoice_controller.models import DocumentKind
from invoice_controller.normalize import format_de_decimal
from invoice_controller.template.ods import write_kostenaufstellung

load_dotenv()

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


DEFAULT_OUTPUT_NAME = "Kostenaufstellung.ods"


# Filenames F1 must never treat as an offer, so a per-project folder can hold the
# intake form, the tool's own outputs, and invoices/credit notes alongside the offers:
#   - the Fragenkatalog intake form and F1/F2/F3 generated artifacts
#   - invoices (consultant convention: filename starts "Rg " / "Rg_") and Gutschriften
#   - the BAFA Verwendungsnachweis forms (eewvn_/qstvn_)
_NON_OFFER_PATTERNS = (
    "fragenkatalog", "kostenaufstellung", "vne-tabelle", "vne tabelle",
    "standortbeschreibung", "gutschrift", "rechnung", "eewvn", "qstvn",
)
_INVOICE_PREFIXES = ("rg ", "rg_")  # e.g. "Rg 2024-001 …", "Rg_Muster …"


def _is_offer_pdf(name: str) -> bool:
    low = name.strip().lower()
    if low.startswith(_INVOICE_PREFIXES):
        return False
    return not any(pat in low for pat in _NON_OFFER_PATTERNS)


def _collect_pdfs(path: Path) -> list[Path]:
    if not path.exists():
        raise typer.BadParameter(f"{path} does not exist")
    if path.is_file():
        if path.suffix.lower() != ".pdf":
            raise typer.BadParameter(f"{path} is not a PDF")
        return [path]  # explicit single file: honour the user's choice
    all_pdfs = sorted(path.glob("*.pdf"))
    pdfs = [p for p in all_pdfs if _is_offer_pdf(p.name)]
    skipped = [p for p in all_pdfs if not _is_offer_pdf(p.name)]
    if skipped:
        console.print(
            f"[dim]Skipping {len(skipped)} non-offer PDF(s): "
            f"{', '.join(p.name for p in skipped)}[/dim]"
        )
    if not pdfs:
        raise typer.BadParameter(f"no offer PDFs found in {path}")
    return pdfs


def _default_output_for(path: Path) -> Path:
    """Place the output .ods alongside the offers — inside the directory if `path` is a
    directory, or alongside the PDF if `path` is a single file."""
    parent = path if path.is_dir() else path.parent
    return parent / DEFAULT_OUTPUT_NAME


@app.command()
def f1(
    path: Path = typer.Argument(..., help="Single offer PDF or directory of PDFs"),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output .ods path. Defaults to <offer-folder>/Kostenaufstellung.ods.",
    ),
) -> None:
    """F1 — extract offers and write Kostenaufstellung.ods."""
    pdfs = _collect_pdfs(path)
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


@app.command()
def f2(
    project_dir: Path = typer.Argument(..., help="Project directory with angebote/ and rechnungen/"),
) -> None:
    """F2 — extract offers + invoices, match, write Kontrollmappe.ods. (not yet implemented)"""
    raise typer.Exit(code=2)


@app.command()
def f3(
    project_dir: Path = typer.Argument(
        None, help="Project folder containing a 'Fragenkatalog Modul 4' PDF (writes into it)"
    ),
    output: Path = typer.Option(None, "--output", "-o", help="Output .odt (default: <project>/Standortbeschreibung.odt)"),
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
    """F3 — generate the client Standortbeschreibung (Antrag section 1.2) as an .odt.

    Preferred: `f3 <project_dir>` reads the project's 'Fragenkatalog Modul 4' PDF and writes
    Standortbeschreibung.odt into that folder. Also accepts --input <md> or --firma/... for ad-hoc runs.
    """
    from invoice_controller.standort import (
        OperationalDefaults,
        StandortInput,
        generate_standort,
        write_standort_odt,
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
            output = project_dir / "Standortbeschreibung.odt"
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
    console.print(f"[bold]F3[/bold] Standortbeschreibung — {described} ({inp.plz} {inp.stadt})")
    if not inp.website:
        console.print("  [yellow]⚠ no website URL — company profile will be empty[/yellow]")

    result = generate_standort(inp, online=not offline)
    write_standort_odt(result, inp, output)
    console.print(f"[bold green]→ Wrote[/bold green] {output}")
    for note in result.review_notes:
        console.print(f"  [yellow]⚠ {note}[/yellow]")


if __name__ == "__main__":
    app()
