# Invoice-Controller

A Python CLI for closing out funded projects across program families (EEW Modul 4; BEG in progress), in preparation for the **Verwendungsnachweis** submission. It extracts structured data from German vendor offers and paid invoices and writes the consultant's working artifacts. Since 2026-08-28 all outputs are Microsoft-native (`.xlsx` tables, `.docx` documents) and the CLI nests one sub-app per program: `eew cost-estimation`, `eew vne-generation`, `eew location-description`, `beg vne-generation` (building).

For the funding-lifecycle context, German terminology, and design rationale, see [CLAUDE.md](CLAUDE.md).

## Status

Three procedures, at different stages:

- **cost-estimation / `eew cost-estimation` — offer extraction → `Kostenaufstellung.xlsx` — shipping.** Given one or more offer PDFs, the tool extracts vendor metadata, all priced positions (including optionals), totals, and (where present) Sonderpreis / Preisnachlass; classifies each position into Investitionskosten / Nebenkosten / Nachlass; and writes a styled, formula-driven `.xlsx` (locale-independent number formats render `1.000,00 €` on German systems). Scanned PDFs (no text layer) fall back to local Tesseract OCR, then a cloud vision-LLM. Client cost statements (*Stellungnahme* / *Schätzung*) are handled alongside vendor offers. A cross-sum check guards every extraction.
- **location-description / `eew location-description` — client Standortbeschreibung → `.docx` — shipping.** Reads a project's filled *Fragenkatalog Modul 4* PDF (or ad-hoc `--firma/--strasse/--plz/--stadt`), scrapes the client website, derives geo facts offline (PLZ → Bundesland/Regierungsbezirk via pgeocode; Kreis/roads via OpenStreetMap), and assembles the German company/location description required by Antrag section 1.2. No LLM is used for the geo data.
- **vne-generation / `eew vne-generation` — invoices → VNE-Tabelle `.xlsx` — shipping.** Classifies every document in the project folder, extracts each invoice (header + all line items, with a per-invoice position cross-sum against the stated netto), splits the netto by the vendor's cost-estimation IK/NK ratio, and writes the 3-category VNE-Tabelle with red-flagged check failures.
- **BEG vne-generation / `beg vne-generation` — BEG Kostenzusammenstellung — building (B1 done).** Classifies BEG project folders (6 document classes incl. Zahlungsnachweis images) and extracts the program parameters (FundingMeta) from the Antragsbestätigung/BzA + Zuwendungsbescheid. The `.xlsx` writer follows.

## Prerequisites

- **Linux** (developed on Manjaro; should work on any modern distro)
- **Python 3.13**
- **[uv](https://docs.astral.sh/uv/)** — the package manager
- **LibreOffice or Excel/Word** — to open the generated `.xlsx` / `.docx`. Optional headless PDF preview: `libreoffice --headless --convert-to pdf <file>.xlsx`
- **poppler** (`pdftotext`) — used by the location-description Fragenkatalog parser to read filled AcroForm PDFs
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

The provider preference order is **OpenRouter → OpenAI → Gemini → Anthropic**; the first key present wins. The recommended default is OpenRouter with `OPENROUTER_MODEL=google/gemini-2.5-flash` — one key reaches a reliable multimodal model (needed for the scanned-PDF vision path) at cents per project. Avoid `:free` models for cost-estimation/vne-generation: they return unreliable structured output. See [`.env.example`](.env.example) for every supported variable. `.env` is gitignored; `.env.example` is committed.

## Web UI (`serve`)

`invoice-controller serve [--host 0.0.0.0] [--port 8080]` starts the browser UI (German): select a
procedure, upload the documents, press start, download the result. Runs execute in the background
with live per-file status; review flags and errors are shown loudly and logged to a per-run
`audit.jsonl` (`tmp/webruns/`, last 20 runs kept). Set `IC_WEB_PASSWORD` in `.env` to enable the
password gate — strongly recommended before exposing the port (e.g. `ngrok http --basic-auth "user:pw" 8080`).

## Server deployment (Docker + Caddy)

For a shared server, the compose stack runs the web UI behind a Caddy reverse proxy
(automatic TLS + basic-auth as the outer layer; `IC_WEB_PASSWORD` stays the inner gate —
this replaces the ngrok setup):

```bash
cp .env.example .env    # provider key + IC_WEB_PASSWORD + IC_WEB_STORAGE_SECRET
# outer basic-auth credentials for Caddy:
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'choose-a-password'
#   → put user + hash into .env as IC_BASIC_AUTH_USER / IC_BASIC_AUTH_HASH
docker compose up -d --build
```

Reachable at `https://localhost` (internal CA) or set `IC_DOMAIN=your.domain` in `.env`
for Let's Encrypt. The app port is never published on the host — Caddy is the only
entrance. Run history persists in the `webruns` volume (last 20 runs; on a shared box,
mind that it holds client documents). The image bundles tesseract+deu and poppler; the
LLM key comes from `.env` at runtime and is never baked into the image.

## Run cost-estimation

`invoice-controller eew cost-estimation <path>` accepts **either a single offer PDF or a directory** of PDFs. A directory is classified and only offer-classified PDFs are processed (skips Fragenkatalog, tool outputs, invoices, …).

```sh
# All offers in a project folder → writes <folder>/Kostenaufstellung.xlsx
uv run invoice-controller eew cost-estimation path/to/project-folder/

# Single offer, explicit output path
uv run invoice-controller eew cost-estimation path/to/offer.pdf -o tmp/offer.xlsx
```

Output: a per-offer console summary (vendor, offer #, positions table, cross-sum pass/fail) and an `.xlsx` workbook with one styled block per offer (SOLL header → column header → positions → Σ row → optional Sonderpreis row → percentage row). Σ totals are live `SUM()` formulas; edit a position and totals recompute. Open it in LibreOffice to review.

## Run location-description

`invoice-controller eew location-description <project_dir>` reads the *Fragenkatalog Modul 4* PDF in the folder and writes `Standortbeschreibung.docx` beside it.

```sh
uv run invoice-controller eew location-description path/to/project-folder --url example.com
```

Without a Fragenkatalog you can drive it ad-hoc: `--firma "Muster GmbH" --strasse "Hauptstr. 1" --plz 59227 --stadt Ahlen --url example.com`. Use `--offline` to skip the OSM Kreis/road lookups, `--betreiber` for a distinct operating tenant, and `--schicht 1|2|3` to override the shift model (→ working hours 8–16 / 8–12 / 8–8).

## How cost-estimation extraction works

1. **PDF text** — pdfplumber pulls layout-preserved text per page. If there's no text layer, it routes to local Tesseract OCR, then a cloud vision-LLM.
2. **LLM extraction** — pydantic_ai's `Agent` returns a typed `ExtractedOffer`: vendor metadata, positions with category classification, totals. The prompt is vendor-agnostic (no per-vendor parsers).
3. **Cross-sum check** — `Σ(positions.line_total_net) ≈ Nettosumme` within `0.02 €`. A mismatch surfaces in the console with the delta. Client statements have no document total, so their check is reported *not applicable* (the consultant verifies the Σ by hand).
4. **`.xlsx` rendering** — openpyxl writes the workbook (`template/xlsx.py`) with live formulas; xlsx number-format codes are locale-independent, so each viewer sees their own locale's formatting. The legacy `.ods` writer (`template/ods.py`) remains for reading old projects.

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
│   ├── cli.py                      — Typer CLI: eew/beg program sub-apps
│   ├── models.py                   — Pydantic: OfferDocument, Position, OfferTotals, CrossSumCheck, …
│   ├── normalize.py                — German number / date / umlaut / soft-hyphen helpers
│   ├── geo.py                      — offline PLZ geo (pgeocode) + OSM Kreis/roads
│   ├── pdf/                        — pdfplumber text + OCR routing (Tesseract / vision)
│   ├── extract/                    — orchestrator, cross-sum check
│   ├── llm/                        — pydantic_ai Agent, prompt, provider resolution, HTTP retry
│   ├── narrative.py                — deterministic prose-block assembly for the description sheet
│   ├── standort/                   — location-description: Fragenkatalog parse, scrape, assemble, .docx writer
│   └── template/
│       ├── ods.py                  — odfdo Kostenaufstellung renderer
│       └── template.ods            — shipped template (sanitized placeholder headers)
├── tests/
│   ├── conftest.py                 — FunctionModel stub agent, fixtures, --run-live flag
│   ├── corpus/                     — ground-truth readers (cost-estimation PDF, vne-generation VNE .xlsx)
│   ├── unit/                       — fast, deterministic tests (corpus-backed ones auto-skip)
│   └── e2e/                        — live API tests (opt-in via --run-live)
├── examples/                       — client-confidential corpus (gitignored, not shipped)
├── tmp/                            — scratch outputs (gitignored)
└── test.py                         — historical 2024 prototype (kept for reference; not used)
```

## What's next (vne-generation)

Invoice extraction, per-invoice date/address sanity checks, per-vendor multi-signal matching with confidence tiers, a bulk-approve verification UX, and the **VNE-Tabelle** `.xlsx` output (per-vendor Investitionskosten / Nebenkosten ratio).

## Sibling project

[ESK-Generator](../../EnergiaConsult/ESK-Generator/) handles the project-start side of the same funding lifecycle (offer → ESK → BAFA application). The two are intentionally independent in v1; a shared `bafa-tooling-common` library extraction is on the long-term roadmap.
