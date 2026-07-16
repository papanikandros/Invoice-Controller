# Invoice-Controller

A Python CLI that runs at the close of an EEW Modul 4 funded investment, in preparation for the **Verwendungsnachweis** submission to BAFA. It extracts structured data from German vendor offers (and, in F2, the paid invoices) and writes a standardised **Kontrollmappe** `.ods` workbook that the consultant uses as the working artifact for the BAFA filing.

For the funding-lifecycle context, German terminology, and design rationale, see [PLAN.md](PLAN.md) and [CLAUDE.md](CLAUDE.md).

## Status

**F1 (offer extraction → Kostenaufstellung.ods) is implemented and working.** Given one or more offer PDFs, the tool extracts vendor metadata, all priced positions, totals, and (where present) Sonderpreis / Preisnachlass; classifies each position into Investitionskosten / Nebenkosten / Nachlass; and writes a styled, formula-driven `.ods` matching the consultant's existing workbook layout, with all numbers in German format (`1.000,00 €`, `100,00 %`). 27 unit tests pass; live E2E tests pass against `examples/EK4_204/` (OpenAI gpt-5-mini).

**F2 (offer + invoice matching) is the next phase.** See [PLAN.md](PLAN.md) for the roadmap.

## Prerequisites

- **Linux** (developed on Manjaro; should work on any modern distro)
- **Python 3.13**
- **[uv](https://docs.astral.sh/uv/)** — the package manager
- **LibreOffice** — to open the generated `.ods`. Optional for headless PDF preview: `libreoffice --headless --convert-to pdf <file>.ods`
- An **LLM API key** for one of: OpenAI (recommended), Google Gemini, or Anthropic

## Install

```sh
git clone <repo-url>
cd Invoice-Controller
uv sync
```

This creates a `.venv`, installs all dependencies including the project itself in editable mode, and makes the `invoice-controller` command available via `uv run`.

## Configure: `.env`

Create a `.env` file in the project root with at least one provider key. The tool's provider preference order is **OpenAI → Gemini → Anthropic**, so set the one you want as primary.

```sh
# Pick one (or set several; OpenAI wins by default):
OPENAI_API_KEY=sk-proj-...
GEMINI_API_KEY=AIza...
ANTHROPIC_API_KEY=sk-ant-...

# Optional model overrides:
OPENAI_MODEL=gpt-5-mini            # default
GEMINI_MODEL=gemini-2.5-flash      # default
ANTHROPIC_MODEL=claude-haiku-4-5-20251001

# Optional full override of the model string passed to pydantic_ai:
# LLM_MODEL=openai-chat:gpt-5
```

`.env` is gitignored.

## Run F1

`invoice-controller f1 <path>` accepts **either a single offer PDF or a directory** containing PDFs.

```sh
# Single offer
uv run invoice-controller f1 "examples/EK4_204/6. atb Angebot-Soll- Netzanschluss.pdf" -o tmp/atb.ods

# All offers in a directory
uv run invoice-controller f1 examples/EK4_204/ -o tmp/EK4_204.ods
```

Output:

- Console summary per offer (vendor, offer #, date, positions table, cross-sum check pass/fail)
- A `.ods` workbook with one styled block per offer (SOLL header → column header → positions → Σ row → optional Sonderpreis row → percentage row)

Open the `.ods` in LibreOffice to review and edit. Cells use German EUR / percentage formatting; Σ totals are live `SUM()` formulas; Sonderpreis Nachlass and the percentage row use cell-reference formulas. Edit a position value and totals will recompute.

## How extraction works

1. **PDF text extraction** — pdfplumber pulls layout-preserved text from each page.
2. **LLM extraction** — pydantic_ai's `Agent` (configured for OpenAI / Gemini / Anthropic via the env vars above) returns a typed `ExtractedOffer` Pydantic model: vendor metadata, positions with category classification, totals (Nettosumme, MwSt., Endbetrag, Sonderpreis, Preisnachlass). The prompt enforces 9 explicit rules covering sub-items, group subtotals, document-level discounts, classification, and the cross-sum constraint.
3. **Cross-sum check** — `Σ(positions.line_total_net) ≈ totals.nettosumme` within `0.02 €` tolerance. Failures surface in the console with the specific delta; the consultant decides whether to fix.
4. **`.ods` rendering** — odfdo writes the workbook with the template's existing styles plus a handful of custom styles (German locale on the data-style root, Light Yellow 3 background on the money columns, bold/underline on Σ row, merged 6-column SOLL header). All numbers are pre-formatted in German style (`1.000,00 €`) AND tagged with the German data-style, so the file renders correctly regardless of the user's LibreOffice locale.

## Testing

```sh
# Unit tests (27 tests, ~4 s, no API calls — uses FunctionModel stub)
uv run pytest tests/unit -v

# Live E2E tests against examples/EK4_204/ (real API calls, costs ~$0.05 with gpt-5-mini)
uv run pytest tests/e2e --run-live -v
```

The unit suite includes:

- German number / date parsing round-trips
- Cross-sum edge cases (exact match, within tolerance, outside tolerance, missing Nettosumme)
- Full pipeline regression on the 3 EK4_204 offers using pre-recorded LLM responses
- `.ods` write → read round-trip verifying formula presence + structure

E2E tests require an API key in `.env` and verify the real LLM produces the expected positions, vendor metadata, cross-sum, and Sonderpreis.

## Project layout

```
Invoice-Controller/
├── README.md                       — this file
├── PLAN.md                         — architecture, library stack, roadmap
├── CLAUDE.md                       — domain context for assistant sessions
├── pyproject.toml                  — uv project + ruff/mypy/pytest config
├── .env                            — your local API keys (gitignored)
├── src/invoice_controller/
│   ├── cli.py                      — Typer CLI: `f1`, `f2`
│   ├── models.py                   — Pydantic: OfferDocument, OfferHeader, Position, OfferTotals, CrossSumCheck, Kostenkategorie
│   ├── normalize.py                — German number / date / umlaut / soft-hyphen helpers
│   ├── pdf/text.py                 — pdfplumber wrapper (per-page text extraction)
│   ├── extract/
│   │   ├── offer.py                — orchestrator: pdf → LLM → cross-sum → OfferDocument
│   │   └── cross_sum.py            — Σ(positions) vs Nettosumme tolerance check
│   ├── llm/extract.py              — pydantic_ai Agent + prompt + HTTP retry (503/429)
│   └── template/ods.py             — odfdo-based Kostenaufstellung renderer with style injection
├── tests/
│   ├── conftest.py                 — FunctionModel-based stub agent, fixtures, --run-live flag
│   ├── ods_inspect.py              — semantic reader for output .ods (block / row / cell)
│   ├── diff_kostenaufstellung.py   — CLI: semantic diff between two Kostenaufstellung .ods
│   ├── fixtures/ek4_204/           — ideal LLM responses for atb / Munk / L&R
│   ├── unit/                       — fast, deterministic regression tests
│   └── e2e/                        — live API tests (opt-in via --run-live)
├── examples/
│   ├── template.ods                — the canonical Kostenaufstellung template (first block = block template)
│   └── EK4_204/                    — the regression corpus: 3 offer PDFs + ground-truth .ods
├── tmp/                            — scratch outputs (gitignored)
└── test.py                         — historical 2024 prototype (kept for reference; not used)
```

## What's next (F2)

- **Invoice extraction**: same LLM pipeline applied to invoice PDFs, with an invoice-specific schema (Rechnungsnummer, Rechnungsdatum, Lieferant, Rechnungsempfänger, Skontoabzug, MwSt., Endbetrag).
- **Per-invoice sanity checks**: date within project window, billing address fuzzy-match against the project's client.
- **Multi-signal matching**: per-vendor, scoring on article# / description similarity / price proximity / quantity / section context; confidence tiers (high / medium / low); LLM second opinion on medium-confidence.
- **Verification TUI**: Textual-based bulk-approve workflow.
- **F2 `.ods` extension**: additional sheets for invoice positions, Abgleich (matching), Datums- & Adressprüfung, Summen.
- **`projekt.yaml`**: per-project config (client, address, project window, expected vendor list, output filename pattern).

See [PLAN.md](PLAN.md) §"Phase 2" for the full breakdown.

## Sibling project

[ESK-Generator](../../EnergiaConsult/ESK-Generator/) handles the project-start side of the same funding lifecycle (offer → ESK → BAFA application). The two are intentionally independent in v1; a shared `bafa-tooling-common` library extraction is on the long-term roadmap.
