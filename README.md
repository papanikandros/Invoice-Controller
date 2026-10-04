# Invoice-Controller

A Python CLI for closing out funded projects across program families (EEW Modul 4 and BEG), in preparation for the **Verwendungsnachweis** submission. It extracts structured data from German vendor offers and paid invoices and writes the consultant's working artifacts. Since 2026-08-28 all outputs are Microsoft-native (`.xlsx` tables, `.docx` documents) and the CLI nests one sub-app per program: `eew cost-estimation`, `eew vne-generation`, `eew location-description`, `beg vne-generation`.

For the funding-lifecycle context, German terminology, and design rationale, see [CLAUDE.md](CLAUDE.md).

## Status

Four procedures:

- **cost-estimation / `eew cost-estimation` — offer extraction → `Kostenaufstellung.xlsx` — shipping.** Given one or more offer PDFs, the tool extracts vendor metadata, all priced positions (including optionals), totals, and (where present) Sonderpreis / Preisnachlass; classifies each position into Investitionskosten / Nebenkosten / Nachlass; and writes a styled, formula-driven `.xlsx` (locale-independent number formats render `1.000,00 €` on German systems). Scanned PDFs (no text layer) fall back to local Tesseract OCR, then a cloud vision-LLM. Client cost statements (*Stellungnahme* / *Schätzung*) are handled alongside vendor offers. A cross-sum check guards every extraction.
- **location-description / `eew location-description` — client Standortbeschreibung → `.docx` — shipping.** Reads a project's filled *Fragenkatalog Modul 4* PDF (or ad-hoc `--firma/--strasse/--plz/--stadt`), scrapes the client website, derives geo facts offline (PLZ → Bundesland/Regierungsbezirk via pgeocode; Kreis/roads via OpenStreetMap), and assembles the German company/location description required by Antrag section 1.2. No LLM is used for the geo data.
- **vne-generation / `eew vne-generation` — invoices → VNE-Tabelle `.xlsx` — shipping.** Classifies every document in the project folder, extracts each invoice (header + all line items, with a per-invoice position cross-sum against the stated netto), splits the netto by the vendor's cost-estimation IK/NK ratio, and writes the 3-category VNE-Tabelle with red-flagged check failures. A second sheet, `Positionsabgleich`, matches invoice positions to offer positions per vendor (variance per position, `FEHLT` for offered-but-never-invoiced, `EXTRA` for invoiced-but-never-offered) — a scope check the per-vendor ratio split cannot do. It reuses the run's own extractions, so it costs no second pass; the VNE sheet itself stays identical to the consultant's template.
- **BEG vne-generation / `beg vne-generation` — invoices → BEG Kostenzusammenstellung `.xlsx` — shipping (B0–B5).** Classifies BEG project folders (6 document classes incl. Zahlungsnachweis images), extracts the program parameters (Vorgangsnummer, Fördersätze, caps, dates, client basis brutto/netto) from the Antragsbestätigung/BzA + Zuwendungsbescheid, extracts every invoice position-level with a cross-sum, reconciles `bezahlt` against the payment proofs and writes the Kostenzusammenstellung. The `förderfähig` column stays empty and flagged until the fundability-rules documents arrive (B3).

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

`invoice-controller serve [--host 0.0.0.0] [--port 8080]` starts the browser UI (German):
select a program, pick a procedure, upload the documents, press start, download the result.
Runs execute in the background with live per-file status; review flags and errors are shown
loudly and logged to a per-run `audit.jsonl` (`tmp/webruns/`, last 100 runs kept).

**Login.** `IC_WEB_PASSWORD` in `.env` is the gate: one password, no username, shown as a
login page. On a **non-loopback bind it is required** — without it the UI refuses to serve
rather than opening up. Bound to loopback only (the default) it stays open, which is what
you want while developing. Failed logins are throttled per client address: five failures in
five minutes lock that address out for fifteen minutes. Set `IC_WEB_STORAGE_SECRET` so
sessions survive a restart.

## Server deployment (Docker, behind the shared Caddy)

The compose stack runs only the web UI. TLS and the hostname come from the server's shared
Caddy stack (`~/Workspace/server-proxy`, deployed to `/opt/proxy`), which reaches the app
over the docker network `proxy` as `invoice-app`. Access control is the app's own login
page — the proxy's basic_auth was dropped on 2026-09-28 — so `IC_WEB_PASSWORD` must be set
in `.env` or the UI stays locked.

```bash
cp .env.example .env    # provider key + IC_WEB_PASSWORD + IC_WEB_STORAGE_SECRET
docker compose up -d --build     # proxy stack must be running (it owns the network)
```

Public host: `https://invoice.bestdomaininthesolarsystem.com` (set as `INVOICE_HOST` in the proxy's
`.env`; Caddy obtains the Let's Encrypt certificate itself). Redeploy after a push:
`git pull --ff-only && docker compose up -d --build` in the server checkout.

The app port is never published on the host — Caddy is the only entrance. Run history
persists in the `webruns` volume (last 100 runs; on a shared box, mind that it holds
client documents). The image bundles tesseract+deu and poppler; the LLM key comes from
`.env` at runtime and is never baked into the image.

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

## Run vne-generation

`invoice-controller eew vne-generation <project_dir>` classifies the folder, extracts every invoice
(header + positions, per-invoice cross-sum), **renames every invoice** to
`<Rechnungsgeber>_<Rechnungsnummer>_<YYYY-MM-DD>.pdf` (ZIP `Rechnungen_umbenannt.zip` with an
`Umbenennung.csv`; a field the extraction could not read becomes `UNKLAR`), splits each netto by the
vendor's IK/NK ratio and writes `VNE-Tabelle.xlsx`. The ratio source: the consultant's **verified
Kostenaufstellung as PDF** (export the checked `Kostenaufstellung.xlsx` to PDF and put it in the
folder — the xlsx itself is no input), else live extraction of the offer PDFs (the only tier that
costs offer-side LLM calls). A `projekt.yaml` supplies client, Antragstellung, AavM-Genehmigung,
Zuwendungsbescheid (eingegangen/datiert), Bewilligungszeitraum, Bescheid figures and the header
identifiers; the web UI takes them as form fields and pre-fills them from an uploaded
Zuwendungsbescheid.

```sh
uv run invoice-controller eew vne-generation path/to/project-folder/ [--no-llm-matching]
```

The workbook follows the consultant's `(Vorlage VNE-Maske)` cell logic: only extracted or typed
values are literals (yellow), everything else is an Excel formula (netto = brutto/(1+MwSt), Netto
nach Skonto, category amounts, Σ rows, Förderbetrag chain, Fristen). The two fundability rules
— `Auftrag erteilt ≥ Antragstellung` and `Rechnungsdatum ≥ Zuwendungsbescheid` (or `≥ AavM-Genehmigung`
when one exists) — are checked three ways that must agree: the F formula with its "… zu früh!"
message row, the Python flags on the console/job page, and conditional formatting that turns the
offending date cell red. A failed invoice counts nothing. Every value that feeds those checks also
carries **evidence**: the model cites a verbatim quote per field, the code locates it in the text it read
and grades it (`belegt`, `Wert nicht im Zitat`, `Zitat nicht im Text`, `ohne Zitat`, `Scan — nicht
prüfbar`). Fields the first pass could not ground get ONE narrow second question — the model must
cite the passage again, and the value is only accepted if that passage exists in the text and contains
it (`unbestätigt` otherwise, the first reading is kept, never guessed). What stays
doubtful turns its cell yellow ("unbelegt"), becomes `UNKLAR` in the
renamed file and a flag on the job page, and the quote sits in a cell comment. The second sheet, `Positionsabgleich`, is
the scope check: invoice positions matched to offer positions per vendor, variance per position in
red, `FEHLT` / `EXTRA` rows, a lump-sum rule for vendors that bill the whole order as one line.

## Run BEG vne-generation

```sh
uv run invoice-controller beg vne-generation path/to/beg-project/
```

Same classifier and invoice extraction; program metadata comes from the project's own
Antragsbestätigung/BzA + Zuwendungsbescheid (no config file), payment proofs go in a
`Zahlungsnachweise/` subfolder (or the second dropzone of the web UI). The sheet follows the
consultant's Kostenzusammenstellung with a parameter block on top (program dates, Fördersatz,
Deckel — yellow inputs the Förderung formulas reference), the same two fundability date rules as
EEW (`--antragstellung`, `--aavm-genehmigung`, `--bescheid-datiert` override the extracted dates;
the web tab pre-fills them from an uploaded Antragsbestätigung/Bescheid), and rule-driven row
colours: green = checked, yellow = Unterlagen fehlen (no payment proof), red = nicht förderfähig /
prüfen. `förderfähig` stays an empty input until the eligibility rules (B3) exist.

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
├── PLAN.md                         — architecture, decisions, research records (gitignored, local)
├── pyproject.toml                  — uv project + ruff/mypy/pytest config
├── .env.example                    — copy to .env: provider key, IC_WEB_PASSWORD, IC_WEB_STORAGE_SECRET
├── Dockerfile / docker-compose.yml — server image + app-only stack behind the shared Caddy proxy
├── src/invoice_controller/
│   ├── cli.py                      — Typer CLI: eew/beg program sub-apps, serve, korrekturen
│   ├── config.py                   — projekt.yaml (client, Bewilligungszeitraum, Bescheid figures)
│   ├── models.py                   — Pydantic: OfferDocument, InvoiceDocument, Position, CrossSumCheck, …
│   ├── normalize.py                — German number / date / umlaut / soft-hyphen helpers
│   ├── privacy.py                  — client masking for LLM payloads + local recipient check
│   ├── geo.py                      — offline PLZ geo (pgeocode) + OSM Kreis/roads
│   ├── pdf/                        — pdfplumber text + OCR routing (Tesseract / vision)
│   ├── extract/                    — classifier, offer/invoice/Bescheid extraction, orchestrators (vne, beg)
│   ├── llm/                        — pydantic_ai Agents, prompts, provider resolution, HTTP retry
│   ├── match/                      — vendor matching; position scorer + LLM assist for the Positionsabgleich
│   ├── vne/                        — ratios, compute, Positionsabgleich (abgleich.py), VNE-Tabelle writer
│   ├── beg/                        — funding meta, payments, Gewerke, compute, Kostenzusammenstellung writer
│   ├── standort/                   — location-description: Fragenkatalog parse, scrape, assemble, .docx writer
│   ├── audit/                      — consultant-corrections capture (korrekturen.jsonl)
│   ├── web/                        — NiceGUI UI: app (pages), registry (procedures), jobs, errors, gate, theme, static/
│   └── template/                   — Kostenaufstellung writers: xlsx.py (current), ods.py + template.ods (legacy)
├── tests/
│   ├── conftest.py                 — FunctionModel stub agent, fixtures, --run-live flag
│   ├── corpus/                     — ground-truth readers (cost-estimation PDF, vne-generation VNE .xlsx)
│   ├── harness/                    — typed extraction-eval harness (live corpus baseline)
│   ├── unit/                       — fast, deterministic tests (corpus-backed ones auto-skip)
│   └── e2e/                        — live API tests (opt-in via --run-live)
├── examples/                       — client-confidential corpus (gitignored, not shipped)
├── tmp/                            — scratch outputs (gitignored)
└── test.py                         — historical 2024 prototype (kept for reference; not used)
```

## What's next

- **Robustness from the first colleague runs (EK4_333, 2026-09-24):** find a Kostenaufstellung
  `.xlsx`/`.ods` by pattern (not only the exact name) and report unused non-PDF uploads; carry the
  document kind into live-extracted ratios so a Schätzung block is recognised; ignore
  Anzahlung-deduction lines in the Positionsabgleich and accept Σ incl. optionals as a lump-sum
  target; keep the consultant's own offer out of the Abgleich; route Fachunternehmererklärungen
  to `other`.
- **Invoice renaming** (`Rechnungssteller-Rechnungsnummer-Rechnungsdatum-Leistung.pdf`), requested
  by a colleague — planned as a ZIP by-product of every VNE run plus a standalone procedure.
- **B3 eligibility layer** for BEG once the fundability-rules documents arrive.
- **A2 local model routing (Ollama)** for scans and other content masking cannot cover.
- **Phase 3:** Bescheid three-way comparison, Verwendungsnachweis draft, re-run diffing.

## Sibling project

[ESK-Generator](../../EnergiaConsult/ESK-Generator/) handles the project-start side of the same funding lifecycle (offer → ESK → BAFA application). The two are intentionally independent in v1; a shared `bafa-tooling-common` library extraction is on the long-term roadmap.
