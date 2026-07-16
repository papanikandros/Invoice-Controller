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


def _collect_pdfs(path: Path) -> list[Path]:
    if not path.exists():
        raise typer.BadParameter(f"{path} does not exist")
    if path.is_file():
        if path.suffix.lower() != ".pdf":
            raise typer.BadParameter(f"{path} is not a PDF")
        return [path]
    pdfs = sorted(path.glob("*.pdf"))
    if not pdfs:
        raise typer.BadParameter(f"no PDFs found in {path}")
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


if __name__ == "__main__":
    app()
