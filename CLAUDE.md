# CLAUDE.md — Context for Claude Code sessions on Invoice-Controller

This file provides the context Claude needs to be effective when working on this project. Read it before making changes. For architecture and current phase, read [PLAN.md](PLAN.md) alongside this.

## What this project is

A Python CLI tool that runs at the close of an EEW Modul 4 funded investment project. Inputs: the original vendor offers (the same ones that fed the ESK funding application) and all invoices paid during implementation. Output: a standardised Excel/ODS workbook (the **Kontrollmappe**) containing extracted positions from both sides, a matching table linking invoice positions to offer positions with variance per position, plus per-invoice date and address sanity checks. The consultant uses the Kontrollmappe as their primary working artifact when preparing the **Verwendungsnachweis** — the BAFA submission that proves funds were used as approved.

**F1 (offer extraction → `Kostenaufstellung.ods`) is implemented and shipping.** The pipeline is `pdfplumber (text) → pydantic_ai Agent (LLM extraction) → cross-sum check → odfdo .ods writer`, with a tiered fallback for scanned PDFs (no text layer → local Tesseract OCR → cloud vision-LLM). 166 unit tests pass (corpus-backed ones auto-skip when `examples/` is absent); live E2E tests pass against `examples/EK4_204/` (offers) and `examples/EK4_322/` (a scanned client statement). The user opens the generated `.ods` in LibreOffice for verification (the Textual TUI is on the F2 roadmap).

**F1 also ingests client cost statements, not just vendor offers.** A `DocumentKind` ∈ {`offer`, `statement`} distinguishes the two. A STATEMENT (e.g. a *Stellungnahme* listing "Kosten … für die noch kein Angebot vorliegt") has no vendor offer number and no document-stated total, so its cross-sum is reported `not_applicable` (Σ of the listed lines is carried as the figure the consultant verifies by hand) rather than `failed`. Amounts are treated as netto with the unstated-VAT status recorded; the block renders with a `SCHÄTZUNG lt. Stellungnahme …` SOLL header. See the scanned client Stellungnahme in the `examples/EK4_322/` set (a scan — exercises both the statement path and the vision-OCR fallback).

**F2 (offer + invoice matching) has not started.** The `f2` CLI command is a stub. See [PLAN.md](PLAN.md) §"Phase 2" for the concrete next-step list.

**F3 (client Standortbeschreibung → `.odt`) is implemented and shipping.** It reads a project's filled *Fragenkatalog Modul 4* PDF (via `pdftotext -layout`; falls back to ad-hoc `--firma/--strasse/--plz/--stadt`), scrapes the client website (httpx + trafilatura), derives geo facts **offline, no LLM** (PLZ → Bundesland/Regierungsbezirk via pgeocode; Kreis + roads via OpenStreetMap Nominatim/Overpass), and deterministically assembles the German company/location description required by Antrag section 1.2. The LLM builds only the company profile from scraped text. Lives in `src/invoice_controller/standort/` + `src/invoice_controller/geo.py`.

## Domain primer

### Where this fits in the funding lifecycle

```
T+0     Offers received          → ESK-Generator Stage A (sister project)
T+1     ESK submitted to BAFA    → ESK-Generator Stage B (sister project)
T+3     Bewilligungsbescheid received (BAFA approves, possibly with adjusted amounts)
T+4..   Implementation: vendors deliver, client pays invoices over months
T+18+   Implementation complete  → INVOICE-CONTROLLER RUNS HERE
T+19    Verwendungsnachweis submitted to BAFA → final funding paid out
```

The tool runs months (sometimes a year+) after the ESK was submitted. It deals with a different concern (post-implementation verification) than ESK-Generator (pre-application drafting), and is structurally a sibling project, not a stage of the ESK tool.

### Verwendungsnachweis

The proof-of-funds-usage submission to BAFA at the end of a funded project. Documents that the funds were used as approved. Typically requires:

- A list of all paid invoices with dates, amounts, vendors
- Mapping of invoiced positions to the originally-approved positions
- Total spent, broken into Investitionskosten and Nebenkosten
- A recalculated Förderbetrag based on actual spending (BAFA pays out the lower of approved and actual)
- Declarations and signatures

The Kontrollmappe this tool produces is the consultant's working artifact for assembling the Verwendungsnachweis. The Verwendungsnachweis itself is currently filled in by hand by the consultant; document generation is a Phase 3 candidate for the tool.

### Bewilligungsbescheid / Zuwendungsbescheid

The BAFA approval letter — the binding document specifying:

- Approved investment amount (which may be lower than what was offered, if BAFA capped certain positions)
- Approved Mehrkosten-Anteil (the funding rate)
- Approved Förderbetrag
- Conditions / Auflagen
- Project window: earliest invoice date that counts, deadline for completion

The Bescheid can be amended (Änderungsbescheid) during the project. The latest Bescheid is the binding one. **In v1 the tool does not extract from the Bescheid**; it compares offer to invoices only. Bescheid integration is a Phase 3 candidate. The consultant manually accounts for Bescheid-driven adjustments for now.

### Invoice types

- **Anzahlungsrechnung** — down-payment / advance invoice, usually issued at order placement
- **Teilrechnung** / **Abschlagsrechnung** — partial / installment invoice, issued at milestones
- **Schlussrechnung** — final invoice, issued at handover/completion
- **Skontoabzug** — early-payment cash discount the client took (reduces the actually-paid amount)

A single offer line may be invoiced across multiple of these. The matching algorithm must support many-to-many: one offer line ↔ multiple invoice lines.

### MwSt. / USt. (VAT)

German VAT is 19 % (some items 7 %). Offers and invoices are usually quoted netto (without VAT). The funding calculation works in netto. The tool extracts VAT lines separately and never silently applies or strips them. If an invoice line is brutto without a clear VAT breakdown, it is flagged for the consultant.

### The consultant's own fee

The energy consultant's own service is itself a position in the cost calculation (typically labelled "sonstiges Einsparkonzept", e.g., 10.000 €). It comes as an invoice from the consultant's own company (e.g., the example `2025_01_Rechnung_PAPANIKANDROS_EnergieKonzept.pdf` is the consultant invoicing themselves). This is a special vendor case — the matching counterpart in the offer is not a vendor offer but a fixed-amount line in the cost calc.

## Key German terminology

| German                          | Meaning                                                            |
|---------------------------------|--------------------------------------------------------------------|
| Abschlagsrechnung               | Installment invoice                                                |
| Adresse (Lieferadresse, Rechnungsadresse) | Address (delivery, billing)                              |
| Anlage(n)                       | Annex(es) — numbered supporting document(s)                        |
| Anzahlung                       | Down payment, advance                                              |
| Anzahlungsrechnung              | Down-payment invoice                                               |
| Auflage(n)                      | Condition(s) attached to BAFA approval                             |
| Auszahlung                      | Disbursement (final funding payout)                                |
| BAFA                            | German federal office for economic affairs (the funding body)      |
| Bestellung                      | Purchase order                                                     |
| Brutto                          | Gross (with VAT)                                                   |
| Bewilligungsbescheid            | Approval letter from BAFA                                          |
| Beilage                         | Attachment / appendix to a document (often referenced by an invoice) |
| Förderbetrag                    | Funding amount                                                     |
| Förderquote / Förderanteil      | Funding rate / share                                               |
| Investitionskosten              | Investment costs (eligible category)                               |
| Kontrollmappe                   | Reconciliation workbook — this tool's primary output               |
| Mehrkosten / Mehrkosten-Anteil  | Additional costs / funding rate on additional costs                |
| Mittelabruf                     | Funds request (interim disbursement during project)                |
| MwSt. / USt.                    | VAT (Mehrwertsteuer / Umsatzsteuer)                                |
| Nachlass / Rabatt               | Discount                                                           |
| Nebenkosten                     | Ancillary/incidental costs (eligible, sometimes capped)            |
| Netto                           | Net (without VAT)                                                  |
| Position / Pos.                 | Line item                                                          |
| Preis                           | Price                                                              |
| Rechnung                        | Invoice                                                            |
| Rechnungsbetrag                 | Invoice amount                                                     |
| Rechnungsdatum                  | Invoice date                                                       |
| Rechnungsnummer                 | Invoice number                                                     |
| Schlussrechnung                 | Final invoice                                                      |
| Skonto                          | Cash discount (early-payment discount)                             |
| Stk. / Stück                    | Unit (count)                                                       |
| Teilrechnung                    | Partial invoice                                                    |
| UID-Nr. / USt-IdNr.             | VAT identification number                                          |
| Verwendungsnachweis             | Proof of funds usage (BAFA submission at project close)            |
| Zahlungskonditionen             | Payment terms                                                      |
| Zwischensumme                   | Subtotal                                                           |
| Zuwendungsbescheid              | Grant approval letter                                              |
| Änderungsbescheid               | Amendment letter (modifies a prior Bescheid)                       |

## The user

A working German energy consultant. Manages 30+ funded EEW Modul 4 projects per year, each of which eventually closes with a Verwendungsnachweis submission. Comfortable with technical detail. Works in LibreOffice (not Microsoft Office), runs Linux (Manjaro). Communicates in English with this assistant but works in German with clients, vendors, and BAFA.

The user has emphasised: cost extraction and matching must be precise; the workflow guarantees this through cross-sum checks and mandatory line-by-line human verification. Algorithmic perfection is not claimed; the workflow is what makes the result trustworthy.

## Where the example projects live

For close-out / Verwendungsnachweis examples, browse the live project folders cautiously (client-confidential):

- `/home/badtoni/Documents/EnergieKonzept/FÖRDERPROJEKTE/EEW/Projekte/MODUL4/EK4_<n>-<Client>/` — most projects
- Look for sub-folders named `Verwendungsnachweis`, `Schlussrechnung`, `Rechnungen`, or files with `Bescheid`, `Auszahlung` in the name
- Bescheide are often in the project root (e.g., a `Bescheid_*.pdf` or `Zuwendungsbescheid_*.pdf`)

Once the user populates `examples/` in this project with a curated corpus of close-out cases (offer + all invoices + the Kontrollmappe the consultant built manually), that becomes the primary reference. Until then, treat live folders as read-only and do not commit any of their data.

## Current implementation (F1)

The real code lives in `src/invoice_controller/`. The 2024 prototype `test.py` is kept at the repo root for historical reference only — none of its code is imported.

**Key files to know:**

- `src/invoice_controller/models.py` — Pydantic v2: `OfferDocument` (carries `kind: DocumentKind` and `vat_basis_unstated`), `OfferHeader`, `Position` (with `Kostenkategorie` enum + LLM confidence/rationale; an `optional`/`optional_reason` flag for Optional-/Eventual-/Mehrpreis positions; `line_total_net` is nullable but a model validator requires it on every non-optional position), `OfferTotals` (incl. `sonderpreis`, `preisnachlass`), `CrossSumCheck` (carries `actual` = Σ mandatory, `actual_incl_optional`, and a `not_applicable` flag for statements), `DocumentKind` enum (`offer` / `statement`).
- `src/invoice_controller/normalize.py` — German number/date helpers: `format_de_decimal` (`Decimal` → `"1.234,56"`), `parse_de_decimal`, `parse_de_date` (numeric + named months), umlaut/ß repair, soft-hyphen reassembly.
- `src/invoice_controller/pdf/text.py` — pdfplumber per-page text extraction with `layout=True`.
- `src/invoice_controller/pdf/ocr.py` — scanned-PDF fallback. `is_text_layer_empty()` detection; `render_page_pngs()` (pypdfium2, non-AGPL) for the vision path; `local_ocr_text()` (Tesseract via the optional `ocr` extra — returns `None` when no local engine is installed, so the caller falls through to the cloud vision-LLM). Generic over offers and statements; keyed on "no text layer", not doc kind.
- `src/invoice_controller/llm/extract.py` — the LLM layer. pydantic_ai `Agent` with typed `ExtractedOffer` output (now carries `doc_type`). Defines `_resolve_model()` (provider preference: OpenRouter → OpenAI → Gemini → Anthropic via env var presence; OpenRouter is the default, keyed on `OPEN_ROUTER_API_KEY`, model from `OPENROUTER_MODEL`, default `google/gemini-2.5-flash`), `get_agent()` (lazy + cached), `extract_offer_llm()` (text path) and `extract_offer_llm_vision()` (sends rendered page PNGs as `BinaryContent` to the same multimodal agent for scans). Includes the 11-rule `SYSTEM_PROMPT` (R10 = capture every priced position including optional/Eventual/Mehrpreis ones, flagged `optional`, never silently dropped; R11 = client STATEMENT handling: set `doc_type="statement"`, extract each "<name>: <Betrag> Euro" line, EXCLUDE non-cost operating metrics like t/a and kWh/a, no Nettosumme, amounts netto) and the HTTP retry layer (`_run_with_http_retry`: 503 with exponential backoff, 429 with 60s wait; accepts a plain prompt or a multimodal message list).
- `src/invoice_controller/llm/summarize.py` — second LLM call producing a `CostNarrative` (German prose item-enumerations for Investitionskosten / Nebenkosten + the source pages each spans). Reuses `_resolve_model()` and the generic `_run_with_http_retry` from `extract.py`.
- `src/invoice_controller/narrative.py` — deterministically assembles the two prose blocks: LLM item-enumeration + category subtotal (omitted when zero) + `(s. Anlage <file>, Seite …)` citation via `format_seiten`. Optionals are folded into the relevant block by the LLM and marked "optional".
- `src/invoice_controller/extract/offer.py` — orchestrator: `pdf → (text | OCR | vision) → LLM extract → kind resolution → cross-sum → (best-effort) narrative → OfferDocument`. `kind_from_filename()` is the primary doc-kind signal (filename matches `Stellungnahme/Schätzung/Eigenerklärung/Kostenvoranschlag/…` → statement); the LLM's `doc_type` is the fallback when the filename is silent. Scanned PDFs (no text layer) route to local OCR, then vision; `extraction_method` records which path ran (`pdfplumber+llm` / `tesseract+llm` / `vision-llm`). The narrative call is wrapped so a failure never invalidates the cross-summed extraction (skipped on the vision path, which has no page text); disable it with `with_narrative=False`.
- `src/invoice_controller/extract/cross_sum.py` — `check_offer()` passes when the stated `Nettosumme` reconciles (within `0.02 €`) to EITHER Σ(mandatory positions) OR Σ(all priced positions incl. optionals). Offers are inconsistent about whether optionals sit inside the stated total, so either match confirms faithful extraction. `check_statement()` handles statements: no document total exists to reconcile against, so it returns `not_applicable=True` (Σ of listed lines in `actual`) — correctness for statements rests on the consultant's review, not an internal redundancy.
- `src/invoice_controller/template/ods.py` — odfdo-based renderer. When any offer has a `CostNarrative`, also writes a second sheet `Kostenbeschreibung` (built by `_build_description_table`): per offer the same SOLL header as the cost sheet, then the two word-wrapped prose blocks (style `IC_NARRATIVE`, merged across A–F). The Kostenaufstellung cost sheet itself is left untouched. Loads the shipped template `src/invoice_controller/template/template.ods` (a **sanitized** copy with `Musterlieferant` placeholder headers — the real project template stays in the gitignored `examples/`; `TEMPLATE_PATH` is `Path(__file__).parent / "template.ods"`), captures the first block as the canonical block shape, injects custom styles (`IC_HEADER_BOLD`, `IC_MONEY`, `IC_SUM_NUM`, `IC_SPP_NUM`, `IC_PCT`, etc.) + custom data styles (`IC_N_EUR_GRP`, `IC_N_PCT_GRP`) with `number:language="de" number:country="DE"` on the root element, renders one block per offer with merged SOLL header, live `SUM()` and percentage formulas, optional Sonderpreis row, pre-computed cached values so the file displays correctly without manual recalc.
- `src/invoice_controller/cli.py` — Typer entry: `f1 <path>` accepts a single PDF or a directory (directory mode filters out non-offer PDFs — Fragenkatalog/Kostenaufstellung/VNE-Tabelle/invoices — via `_is_offer_pdf`); `f2 <project_dir>` is a stub; `f3 <project_dir>` generates the Standortbeschreibung `.odt`. Statement docs print a yellow `ⓘ Schätzung` line (Σ + "netto angenommen, manuell zu prüfen") instead of the offer cross-sum pass/fail line.
- `tests/conftest.py` — `FunctionModel`-based stub agent for unit tests, `--run-live` opt-in flag, fixture loaders.
- `tests/ods_inspect.py` — semantic reader for output `.ods` (used by regression tests and the diff tool).
- `tests/diff_kostenaufstellung.py` — CLI for semantic diff between two Kostenaufstellung `.ods` files.
- `src/invoice_controller/template/template.ods` — the **shipped** Kostenaufstellung template (sanitized placeholder headers; the first block's layout + styles are what the writer uses). `examples/template.ods` is the original with real EK4_204 vendor headers, kept local/gitignored.
- `examples/EK4_204/` — the regression corpus: 3 offer PDFs (three different vendors) + the consultant's hand-built ground-truth `.ods`.
- `examples/EK4_322/` — a multi-vendor close-out incl. a scanned client Stellungnahme (no text layer) that exercises both the statement doc-kind path and the vision-OCR fallback.

**Stack:** pdfplumber + pypdf + pypdfium2 + pydantic_ai + odfdo + Pydantic v2 + Typer + python-dotenv, plus pgeocode + trafilatura + httpx for F3 (geo + scrape), openpyxl for the F2 ground-truth readers. Default LLM provider is OpenRouter (`google/gemini-2.5-flash`). F3 also needs the system `pdftotext` (poppler) for filled AcroForm PDFs. Optional `ocr` extra adds pytesseract (local OCR tier; needs the system `tesseract` binary + `deu` language pack). uv as the package manager. Python 3.13. See [PLAN.md](PLAN.md) §"Library stack" for rationale and rejected alternatives.

**Run F1:** `uv run invoice-controller f1 path/to/project-folder/` (after `cp .env.example .env` and adding a provider key). **Run F3:** `uv run invoice-controller f3 path/to/project-folder --url example.com`.

**Run tests:** `uv run pytest tests/unit` (fast, no API). `uv run pytest tests/e2e --run-live` (hits the LLM API; the EK4_322 statement test uses the multimodal/vision path).

**Local OCR (keep scans on-prem):** `uv sync --extra ocr` plus the system `tesseract` + `deu` language pack. Without it, scans fall back to the cloud vision-LLM (the same confidentiality boundary F1 already crosses by sending offer text to the cloud LLM).

**Notable design points to remember when editing F1 code:**

- The user revised the LLM rule mid-design from "no LLM in v1" to "use LLM where it's better, deterministic where it's better" — and explicitly required **vendor-agnostic extraction**. The LLM is the primary extraction path; cross-sum is the safety net. Don't reintroduce per-vendor parsers.
- The `.ods` writer must explicitly set `number:language="de" number:country="DE"` on the **root element** of any custom data style. The template's `N172` has the locale only on the currency-symbol child, which caused numbers to fall back to system locale (`1,000.00` in en-US). The fix shipped in `template/ods.py`.
- Σ and percentage cells store BOTH a precomputed cached value AND a live formula. LibreOffice prefers the cached value on open; without precompute the cells would show `0` until manual recalc.
- Scratch outputs go to `./tmp/` (project-local), not `/tmp` or `$CLAUDE_JOB_DIR/tmp`. See the `project_tmp_folder` memory.
- **Statements have no cross-sum.** Don't "fix" `check_statement` to fail or to fabricate a total — a statement legitimately has no document-stated total, so `not_applicable` is the correct state and the consultant's review is the backstop. Detection is filename-first (`kind_from_filename`) then the LLM's `doc_type`; a statement filename overrides an LLM "offer" guess.
- **A failed offer cross-sum is written loudly, not silently.** The block still renders (the consultant needs the rows to review), but it gets a "Nettosumme lt. Dokument" comparison row under Σ and a red merged `⚠ KREUZSUMME WEICHT AB …` warning row (style `IC_WARN`) at the block end; the CLI prints a per-file ✗ plus an end-of-run summary naming the failed PDFs. A mismatch can be the vendor's own arithmetic error (the Reiling Eggersmann offer's stated Zwischensumme is 27,50 € above the sum of its own positions), so don't assume extraction is at fault.
- **Both LLM agents run at temperature 0** (`DETERMINISTIC_SETTINGS` in llm/extract.py, shared by summarize.py). Measured on the Reiling corpus: default sampling flapped judgment fields (optional flags, Kostenkategorie, description wording) between runs; at temperature 0 consecutive runs are near-identical, with residual variance confined to 0-€-"inklusive" lines and description cosmetics. Bit-perfect repeatability is not achievable with a cloud LLM — the deterministic guards + consultant review remain the correctness backstop.
- **Document-level discounts (Sonderrabatt/Rechnungsrabatt/Preisnachlass) are normalized defensively.** LLM runs are inconsistent about where they land: `OfferTotals._normalize_discount_fields` folds a negative `sonderpreis` into `preisnachlass`, and `drop_duplicate_discount_positions` (extract/offer.py) drops a discount pseudo-position (non-numeric Pos., Rabatt-like label) when totals already carry the same amount — genuine numbered negative positions ("Entfall …") are always kept. Prompt rule R3b names the labels explicitly.
- **OCR is tiered and keyed on "no text layer", not doc kind.** Scanned offers benefit too (the EK4_322 corpus had an `UNBEKANNT – keine Textdaten im PDF` block from a scanned offer). Local OCR is optional and on-prem; the cloud vision-LLM is the fallback when no local engine is installed. The cross-sum remains the OCR guardrail for offers.

## Constraints — important

### Correctness is non-negotiable

The Verwendungsnachweis submission must be 100% correct — incorrect figures can lead to BAFA reducing funding or rejecting the submission entirely. The consultant always reviews. The tool exists to make that review focus on judgment (variance flags, scope-drift questions) rather than mechanical cross-referencing.

Implications:

- Per-extraction: cross-sum check refuses to commit on any mismatch with the PDF's stated total.
- Per-match: confidence-scored proposals; mandatory human confirmation; manual matching always available.
- Audit log records every decision (extraction method, confidence, original vs. final value, consultant override).
- "100% accurate" is a workflow property (extract + check + match + verify + audit), not an algorithmic claim.

### Confidentiality

Client invoices and offers are sensitive. Same as ESK-Generator: per-section model routing supports keeping confidential content local (Ollama). Default to caution.

### Independence from ESK-Generator (in v1)

The user has chosen to keep Invoice-Controller fully independent from ESK-Generator: re-extract the offer from PDF rather than read the ESK-Generator's `.ods`. This costs some duplicate extraction work per project but keeps the tool self-contained and usable for projects that did not go through ESK-Generator at all.

A shared `bafa-tooling-common` library extraction is planned for Phase 4 once both tools have settled patterns. Until then, accept code duplication as a deliberate v1 trade-off.

### Bulk verification UX is mandatory

With 10+ invoices and potentially 100+ positions, per-line click-through verification will not scale. The verification UI must support:

- Bulk-approve all `high`-confidence matches in one action
- Focus the consultant's review on `medium` and `low` confidence
- Quick keyboard navigation
- Override-and-continue rather than approve-each

Designs that require clicking through every position are unacceptable. Plan around this constraint.

## Architectural decisions and rationale

### Why a standardised Kontrollmappe rather than a markdown report?

The user works in LibreOffice. The output of this tool feeds directly into the Verwendungsnachweis preparation, which is itself manual table work. A `.ods` workbook lands in the consultant's native tool, supports row-level edits, and serves as the working artifact for the rest of the close-out process. A markdown report would force a copy-paste into Excel anyway.

### Why independent re-extraction rather than reading ESK-Generator's `.ods`?

The user chose this in design discussion. Trade-off: duplicate extraction work per project, but the tool works for projects that did not go through ESK-Generator (older projects, projects done before ESK-Gen existed). Phase 4's shared library reduces the duplication without coupling the two tools' deployment.

### Why two inputs (offer + invoices) rather than three (+ Bescheid)?

The user chose this in design discussion. Simpler v1; the consultant accounts for Bescheid-driven adjustments manually for now. Bescheid integration is a Phase 3 candidate, especially valuable for projects with significant Änderungsbescheide where the approved amount diverged meaningfully from the offer.

### Why multi-signal matching rather than just description similarity?

Description text varies wildly between offers and invoices. Single-signal matching on descriptions alone would mis-match too often. Combining article number, description similarity, price proximity, quantity, and section context produces robust scores even when individual signals are weak. The confidence tier captures uncertainty honestly so the consultant knows where to focus.

### Why LLM-assist for matching rather than only for extraction?

Matching across documents is exactly the kind of fuzzy semantic task LLMs are good at. Claude given two position lists can often spot matches the multi-signal scorer misses (e.g., synonyms, abbreviations). But LLM matching is just a proposal — same verification rule applies. Used as augmentation, not replacement.

### Why a per-vendor matching boundary?

Most matches are within-vendor (an invoice line for vendor X corresponds to an offer line from vendor X). Cross-vendor matching is rare and usually wrong (same item from a sub-contractor). Grouping by vendor first sharpens the matching algorithm and reduces false positives.

## What NOT to do

- **Do not skip the cross-sum check on extraction.** Same rule as ESK-Generator: extracted lines must sum to the document's stated grand total exactly, or extraction is treated as failed.
- **Do not skip the verification step on matching.** Auto-matched proposals are always proposals; nothing writes to the Kontrollmappe until the consultant has confirmed (in bulk or per-line, but always confirmed).
- **Do not silently apply or strip MwSt./VAT.** Offers and invoices are usually netto; if an amount is brutto without clear breakdown, flag it.
- **Do not match across vendors by default.** Same item invoiced by a sub-contractor with a different name is a special case requiring explicit override.
- **Do not silently commit when re-running** for a project that already has a Kontrollmappe. Hash-detect changes, require `--overwrite`, produce a diff report.
- **Do not couple this tool to ESK-Generator** in v1. The user has chosen the independent boundary deliberately. Resist the temptation to read the ESK-Gen `.ods` "for convenience."
- **Do not write code documentation for what code does** — only why. Same convention as ESK-Generator.
- **Do not commit client-confidential data** to the repo. Examples must be anonymised if shared beyond the user's machine.

## Quick orientation for new sessions

1. Read [README.md](README.md) for install / run / what F1 does today.
2. Read [PLAN.md](PLAN.md) for the architecture, the actually-built library stack, and the Phase 2 (F2) next-step list.
3. Read this file (CLAUDE.md) for domain context (funding lifecycle, German terminology, what NOT to do).
4. Skim `src/invoice_controller/` — the file pointers above tell you what each module does.
5. Run `uv run pytest tests/unit -v` to see the unit suite green; that's the regression anchor.
6. Look at `examples/EK4_204/` — 3 offer PDFs and the consultant's hand-built ground-truth `.ods` are the correctness reference.
7. Look at `tmp/ek4_204_template_v7.ods` (most recent generated output, gitignored) and the corresponding PDF preview if it exists, to see the current F1 output shape.
8. The sister project ESK-Generator (`/home/badtoni/Workspace/EnergiaConsult/ESK-Generator/`) shares the funding domain — its CLAUDE.md is worth reading.
9. When in doubt about correctness vs. convenience, choose correctness.
