# Invoice-Controller

A Python CLI that runs at the close of an EEW Modul 4 funded investment, in preparation for the **Verwendungsnachweis** submission to BAFA. It extracts structured data from German vendor offers (and, in F2, the paid invoices) and writes a standardised **Kontrollmappe** `.ods` workbook that the consultant uses as the working artifact for the BAFA filing.

For the funding-lifecycle context, German terminology, and design rationale, see [CLAUDE.md](CLAUDE.md).

## Status

Three procedures, at different stages:

- **F1 — offer extraction → `Kostenaufstellung.ods` — shipping.** Given one or more offer PDFs, the tool extracts vendor metadata, all priced positions (including optionals), totals, and (where present) Sonderpreis / Preisnachlass; classifies each position into Investitionskosten / Nebenkosten / Nachlass; and writes a styled, formula-driven `.ods` with all numbers in German format (`1.000,00 €`, `100,00 %`). Scanned PDFs (no text layer) fall back to local Tesseract OCR, then a cloud vision-LLM. Client cost statements (*Stellungnahme* / *Schätzung*) are handled alongside vendor offers. A cross-sum check guards every extraction.
- **F3 — client Standortbeschreibung → `.odt` — shipping.** Reads a project's filled *Fragenkatalog Modul 4* PDF (or ad-hoc `--firma/--strasse/--plz/--stadt`), scrapes the client website, derives geo facts offline (PLZ → Bundesland/Regierungsbezirk via pgeocode; Kreis/roads via OpenStreetMap), and assembles the German company/location description required by Antrag section 1.2. No LLM is used for the geo data.
- **F2 — offer + invoice matching → VNE-Tabelle — not yet implemented.** The `f2` command is a stub.

## Prerequisites

- **Linux** (developed on Manjaro; should work on any modern distro)
- **Python 3.13**
- **[uv](https://docs.astral.sh/uv/)** — the package manager
- **LibreOffice** — to open the generated `.ods` / `.odt`. Optional headless PDF preview: `libreoffice --headless --convert-to pdf <file>.ods`
- **poppler** (`pdftotext`) — used by the F3 Fragenkatalog parser to read filled AcroForm PDFs
- An **LLM API key** (see below). Optional: system `tesseract` + `deu` language pack for on-prem OCR of scanned PDFs.

## Install

```sh
git clone <repo-url>
cd Invoice-Controller
uv sync                 # add `--extra ocr` for the local Tesseract tier
```

This creates a `.venv`, installs all dependencies plus the project in editable mode, and makes the `invoice-controller` command available via `uv run`.

## Configure: `.env`

```sh
cp .env.example .env
# then edit .env and fill in ONE provider key
```

The provider preference order is **OpenRouter → OpenAI → Gemini → Anthropic**; the first key present wins. The recommended default is OpenRouter with `OPENROUTER_MODEL=google/gemini-2.5-flash` — one key reaches a reliable multimodal model (needed for the scanned-PDF vision path) at cents per project. Avoid `:free` models for F1/F2: they return unreliable structured output. See [`.env.example`](.env.example) for every supported variable. `.env` is gitignored; `.env.example` is committed.

## Run F1

`invoice-controller f1 <path>` accepts **either a single offer PDF or a directory** of PDFs. When given a directory it skips non-offer files (Fragenkatalog, Kostenaufstellung, VNE-Tabelle, invoices, …) and processes only the offers.

```sh
# All offers in a project folder → writes <folder>/Kostenaufstellung.ods
uv run invoice-controller f1 path/to/project-folder/

# Single offer, explicit output path
uv run invoice-controller f1 path/to/offer.pdf -o tmp/offer.ods
```

Output: a per-offer console summary (vendor, offer #, positions table, cross-sum pass/fail) and a `.ods` workbook with one styled block per offer (SOLL header → column header → positions → Σ row → optional Sonderpreis row → percentage row). Σ totals are live `SUM()` formulas; edit a position and totals recompute. Open it in LibreOffice to review.

## Run F3

`invoice-controller f3 <project_dir>` reads the *Fragenkatalog Modul 4* PDF in the folder and writes `Standortbeschreibung.odt` beside it.

```sh
uv run invoice-controller f3 path/to/project-folder --url example.com
```

Without a Fragenkatalog you can drive it ad-hoc: `--firma "Muster GmbH" --strasse "Hauptstr. 1" --plz 59227 --stadt Ahlen --url example.com`. Use `--offline` to skip the OSM Kreis/road lookups, `--betreiber` for a distinct operating tenant, and `--schicht 1|2|3` to override the shift model (→ working hours 8–16 / 8–12 / 8–8).

## How F1 extraction works

1. **PDF text** — pdfplumber pulls layout-preserved text per page. If there's no text layer, it routes to local Tesseract OCR, then a cloud vision-LLM.
2. **LLM extraction** — pydantic_ai's `Agent` returns a typed `ExtractedOffer`: vendor metadata, positions with category classification, totals. The prompt is vendor-agnostic (no per-vendor parsers).
3. **Cross-sum check** — `Σ(positions.line_total_net) ≈ Nettosumme` within `0.02 €`. A mismatch surfaces in the console with the delta. Client statements have no document total, so their check is reported *not applicable* (the consultant verifies the Σ by hand).
4. **`.ods` rendering** — odfdo writes the workbook from a shipped template (`src/invoice_controller/template/template.ods`), reusing its styles and block shape. German locale is set on the data-style root so numbers render as `1.000,00 €` regardless of the user's LibreOffice locale.

## Testing

```sh
# Unit tests — fast, no API calls (uses a FunctionModel stub agent)
uv run pytest tests/unit -q

# Live E2E tests — real API calls, needs a key in .env
uv run pytest tests/e2e --run-live -q
```

**Note on the example corpus:** the `examples/` folder holds real client offers, invoices, and close-out artifacts. It is **client-confidential and not included in the repository** (`examples/` is gitignored). Tests that depend on it skip automatically on a fresh clone. So a fresh clone runs the pure-logic tests green (parsing, cross-sum, models, CLI filtering, statement handling, ODS writer) and skips the corpus-backed ones. With the corpus present locally, the full suite passes. To exercise the corpus-backed tests, drop your own project folders into `examples/`.

## Project layout

```
Invoice-Controller/
├── README.md                       — this file
├── CLAUDE.md                       — domain context for assistant sessions
├── pyproject.toml                  — uv project + ruff/mypy/pytest config
├── .env.example                    — copy to .env and add a provider key
├── src/invoice_controller/
│   ├── cli.py                      — Typer CLI: f1, f2 (stub), f3
│   ├── models.py                   — Pydantic: OfferDocument, Position, OfferTotals, CrossSumCheck, …
│   ├── normalize.py                — German number / date / umlaut / soft-hyphen helpers
│   ├── geo.py                      — offline PLZ geo (pgeocode) + OSM Kreis/roads
│   ├── pdf/                        — pdfplumber text + OCR routing (Tesseract / vision)
│   ├── extract/                    — orchestrator, cross-sum check
│   ├── llm/                        — pydantic_ai Agent, prompt, provider resolution, HTTP retry
│   ├── narrative.py                — deterministic prose-block assembly for the description sheet
│   ├── standort/                   — F3: Fragenkatalog parse, scrape, assemble, .odt writer
│   └── template/
│       ├── ods.py                  — odfdo Kostenaufstellung renderer
│       └── template.ods            — shipped template (sanitized placeholder headers)
├── tests/
│   ├── conftest.py                 — FunctionModel stub agent, fixtures, --run-live flag
│   ├── corpus/                     — ground-truth readers (F1 PDF, F2 VNE .xlsx)
│   ├── unit/                       — fast, deterministic tests (corpus-backed ones auto-skip)
│   └── e2e/                        — live API tests (opt-in via --run-live)
├── examples/                       — client-confidential corpus (gitignored, not shipped)
├── tmp/                            — scratch outputs (gitignored)
└── test.py                         — historical 2024 prototype (kept for reference; not used)
```

## What's next (F2)

Invoice extraction, per-invoice date/address sanity checks, per-vendor multi-signal matching with confidence tiers, a bulk-approve verification UX, and the **VNE-Tabelle** `.xlsx` output (per-vendor Investitionskosten / Nebenkosten ratio).

## Sibling project

[ESK-Generator](../../EnergiaConsult/ESK-Generator/) handles the project-start side of the same funding lifecycle (offer → ESK → BAFA application). The two are intentionally independent in v1; a shared `bafa-tooling-common` library extraction is on the long-term roadmap.
