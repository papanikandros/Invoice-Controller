# CLAUDE.md — Context for Claude Code sessions on Invoice-Controller

This file provides the context Claude needs to be effective when working on this project. Read it before making changes. For architecture and current phase, read [PLAN.md](PLAN.md) alongside this.

## What this project is

A Python CLI tool that runs at the close of an EEW Modul 4 funded investment project. Inputs: the original vendor offers (the same ones that fed the ESK funding application) and all invoices paid during implementation. Output: the consultant's working tables for the **Verwendungsnachweis** — the BAFA submission that proves funds were used as approved. **The tool has exactly four procedures (user decision 2026-09-21): EEW Kostenaufstellung (`eew cost-estimation`), EEW VNE-Tabelle (`eew vne-generation`), EEW Standortbeschreibung (`eew location-description`), BEG Kostenzusammenstellung (`beg vne-generation`).** The once-planned standalone **Kontrollmappe** (a separate 7-sheet offer↔invoice matching workbook) was a misunderstanding and is REMOVED — do not rebuild it or propose it. Its position matching (variance per offer position, FEHLT/EXTRA) runs inside `eew vne-generation` and lands on a second sheet `Positionsabgleich` of the VNE workbook; the `(Vorlage VNE-Maske)` sheet itself must stay identical to the consultant's examples. Older Kontrollmappe wording further down this file is historical.

**CURRENT STATE (2026-09-29): all pipelines shipped, deployed and in first colleague use** (deployed HEAD `fda35a1` at `https://invoice.bestdomaininthesolarsystem.com`, server checkout `badserver1:/opt/invoice-controller`; this file and `PRIVACY.md` are committed since 2026-09-29) — cost-estimation, vne-generation, location-description, BEG vne-generation (B0–B5), the web UI (U1 v1, NiceGUI, `invoice-controller serve`), the Positionsabgleich inside vne-generation (`vne/abgleich.py`: the former R6 multi-signal + LLM position matching; second sheet of the VNE workbook; zero extra tokens — offer side from the verified `Kostenaufstellung.xlsx` or the run's own live offer extraction), corrections capture (R8, `eew korrekturen`), the eval harness (R7, `tests/harness/`), and client masking with local recipient check (A1, `privacy.py`; design/analysis in the local PRIVACY.md). Research todos R1–R10 are closed (R3/R9 benchmarked and NOT adopted — records in PLAN.md); Q1–Q5 answered; Phase 4 (shared library with ESK-Generator) is DEPRECATED. 378 unit tests green; live suites opt-in via `--run-live`.

**OPEN TODOS (updated 2026-09-29):**

1. **Positionsabgleich acceptance ✅ in practice (2026-09-24)** — a colleague ran EK4_333 four times; after the fixes below he wrote the tool is usable for the VNE. Remaining: the consultant's own read of a `Positionsabgleich` sheet on a further project (scorer weights/thresholds still rest on ZePa + EK4_333, not a labelled corpus).
2. **User: B3 (eligibility layer)** — blocked on the two consultant-delivered fundability-rules documents (BEG 3-page PDF + separate EEW doc). Fills the `förderfähig` column (currently empty + flagged).
3. **Colleague feedback (EK4_333 runs 1–4) ✅ built and deployed 2026-09-29 (`eab6ecc`)** — Kostenaufstellung `.xlsx`/`.ods` found by name pattern + unused uploads listed; live-extracted Schätzung blocks carry the document kind; Positionsabgleich ignores Anzahlung-deduction lines, accepts Σ incl. optionals as lump sum, excludes the own-company offer, reads `.ods` positions, and subtotal rows no longer end a block; `FUE`/Fachunternehmererklärung → `other`; vendor taken from the filename when the extraction returned the recipient (flagged). Remaining from the older U1 polish list: mid-run file-grid stability, per-run cost display, cancel granularity, second EH sheet variant.
4. **A3 ops** — OpenRouter: code side DONE 2026-09-29 (`_openrouter_provider_config`: `data_collection=deny` by default, env knobs `OPENROUTER_ZDR`, `OPENROUTER_PROVIDERS` e.g. `google-vertex/eu`, `OPENROUTER_ALLOW_FALLBACKS`; gemini-2.5-flash probed OK under deny/zdr/EU-pin, all served by Google); account switch at openrouter.ai/settings/privacy flipped by the user 2026-09-29; server .env pins `OPENROUTER_ZDR=1`, `OPENROUTER_PROVIDERS=google-vertex/eu`, `OPENROUTER_ALLOW_FALLBACKS=0` (live-verified in the container) — **A3 closed 2026-09-29**; since 2026-09-28 the app's login page (`IC_WEB_PASSWORD`, per-address throttling in `web/gate.py`) is the ONLY gate — the proxy's basic_auth was dropped, so the password is mandatory on the server (fails closed) `webruns` retention DECIDED 2026-09-29: 100 runs on the current TEST server (only the consultant + colleagues hold the password; history = debugging material); revisit before the move to the company server / public login portal (PRIVACY.md §6).
5a. **OCR-engine evaluation (added 2026-10-04)** — benchmark Surya and PaddleOCR (candidates: docTR) against the current Tesseract tier on the R7 harness scan corpus (`tests/harness/`, reuse `tmp/bench_scan_tiers.py` from R9), measuring per-field accuracy and cross-sum pass rate on the colleague's raw scanner files too (EK4_333 run 5, the `doc0239…`/`RG001811B` class). Adopt only if measurably better; the engine must run on our server (no cloud). Context: Tesseract scored 17/17 netto, 18/19 cross-sums on the Presspart scans (R9) — the failures seen live come from low-dpi raw scans. Ask colleagues to scan at 300 dpi regardless.
5. **A2 — local model routing (Ollama)** for what masking can't cover (Zahlungsnachweise, BEG private clients, scans); needs a user-approved local vision model download; validate with the R7 harness before switching any tier. Design: local PRIVACY.md §5.
6. **UI redesign ✅ (2026-09-24, done)** — web UI restyled to the EnergieKonzept-Krause brand (`web/theme.py` holds the DESIGN.md tokens; Source Sans 3 + logo vendored in `web/static/`) and restructured: `/` chooses the funding program (EEW / BEG), `/<program>` shows that program's procedures as tabs. Gotchas learned: Tailwind 4 utilities sit in a cascade layer, so any unlayered CSS `padding` on the same element silently wins over `py-*`; `ui.image` collapses with `w-auto` (use a plain `<img>`); `/laeufe` must be registered before the `/{program}` wildcard route.
7. **Docs hygiene** — `CLAUDE.md` and `PRIVACY.md` are committed since 2026-09-29 (PRIVACY.md §6 rewritten for the shared-server setup; its two open decisions — `webruns` retention, OpenRouter settings — are todo #4). Small leftover: `web/gate.py` has no unit tests.
8. **Server deployment ✅ (done; domain + gate changed 2026-09-28)** — `Dockerfile` (uv, tesseract+deu, poppler, healthcheck, secrets only via `env_file`), app-only `docker-compose.yml` joining the **shared Caddy proxy stack** (`~/Workspace/server-proxy` → `/opt/proxy`) over the external docker network `proxy` under alias `invoice-app`. Public host `invoice.bestdomaininthesolarsystem.com` (Let's Encrypt via Caddy; the sslip.io host is gone — corporate filters blocked it). Since 2026-09-28 the app's login page is the only gate (`web/gate.py`: 5 failures / 5 min → 15 min lockout per address; Caddy ignores client-supplied `X-Forwarded-For` by default, so the lockout keys on the real address). Do NOT add Caddy/ports back into this repo's compose file. Ollama service left out until A2. Redeploy = `git pull --ff-only && docker compose up -d --build` on the server; error tracebacks land in each run's `audit.jsonl` and `docker compose logs app`.
9. **Rechnungen umbenennen** — folded into #10 V6 (format and placement decided 2026-10-03).
10. **VNE-Tabelle refactor (consultant's test of 2026-10-03; V1/V2/V3/V4/V6 BUILT 2026-10-04, V5/V7 open — full plan in PLAN.md §"Phase 2c")** — writer `vne/xlsx.py` rebuilt to the template's 3-row blocks with formulas + conditional formatting; `vne/rename.py`; config/Bescheid/form fields for Antragstellung, AavM, Bescheid dates, Kennung/Passwort/IBAN/Steuernummer (IBAN/Steuernummer are typed only — a Bescheid's IBAN is BAFA's account); PDF-only Kostenaufstellung tier (print setup in `template/xlsx.py`, locale-robust PDF money parsing); replay test over all 10 consultant workbooks proves formula verdict ≡ Python verdict (LibreOffice recalculation in tests, skipped without soffice). Live-checked on EK4_333 2026-10-04: figures = run 3, a misread scan date correctly red/NEIN!.
    - ✅ V1 `Kostenaufstellung.pdf` as the ONLY Kostenaufstellung input of vne-generation (decided 2026-10-04: xlsx/ods tiers removed, no PDF download in cost-estimation; consultant verifies the xlsx, exports the PDF, uploads it). Blocker measured: a LibreOffice export of our xlsx is unparseable (2 pages wide, en-US numbers) → print setup in the xlsx writer + locale-robust PDF money parsing (+ optionally emit the PDF ourselves).
    - ✅ V2 dates: new fields Antragstellung / AavM / Zuwendungsbescheid eingegangen + datiert (pre-filled from the Bescheid; Antragstellung + datiert MANDATORY since 2026-10-05); per invoice C Auftrag erteilt (blank when not extractable) · D Zahlungsdatum · E Rechnungsdatum; the two checks DECIDED 2026-10-04: `Auftrag erteilt ≥ Antragstellung` always; `Rechnungsdatum ≥ Zuwendungsbescheid datiert` unless an AavM exists (then the lower bound is the AavM-Genehmigung date — confirmed 2026-10-04) — the templates' B$7 logic is NOT adopted as Excel formulas AND Python flags AND red conditional formatting on the offending date cell. Replay test against all 10 ground-truth workbooks. **A missed early date would void the funding — no tolerance.**
    - ✅ V3 formulas not values: rewrite `vne/xlsx.py` to the template cell logic (3-row blocks, H = G/(1+MwSt) with brutto + MwSt-Satz as inputs and red CF vs the extracted netto, J/M–O/T–V/Σ/Fristen formulas; only extracted inputs as literals). Sheets: Maske + Positionsabgleich only (decided 2026-10-04). Header Kennung/Passwort/IBAN/Steuernummer: Bescheid-extracted + form inputs.
    - ✅ V4 colours: FFFFFF99 on inputs, no fill on formulas, red via conditional formatting for invalid cells (replaces the whole-row red).
    - ⏳ V5 NEW: Zahlungsnachweise/Bankauszüge for EEW — second dropzone, reuse `beg/payments.py`, validate every invoice paid exactly (fills D Zahlungsdatum, derives Skonto, flags unpaid/partial). Waits for the consultant's example files.
    - ✅ V6 rename invoices as FIRST step of each VNE run: `<Rechnungsgeber>_<Rechnungsnummer>_<YYYY-MM-DD>.pdf`, ALWAYS (scans too; unreadable field → `UNKLAR` + listed), ZIP download button, Beleg column B shows the new filename (decided 2026-10-03/04).
11. **BEG vne-generation: apply V2/V3/V4 as well (added 2026-10-04, PLANNED, after the EEW rewrite)** — the same three refactors for `beg/xlsx.py` (Kostenzusammenstellung): (V2) date checks per invoice against the BEG program dates that `beg/funding.py` already extracts from the Antragsbestätigung/BzA + Zuwendungsbescheid (Antragstellung/BzA-Datum, Bescheid datiert, Bewilligungszeitraum) as Excel formulas + Python flags + red conditional formatting on the offending date cell — the exact BEG rules (Maßnahmenbeginn vs BzA, invoice vs Bescheid, AavM equivalent?) must be confirmed by the consultant before coding, they are NOT assumed identical to EEW; (V3) formulas instead of values wherever the consultant's BEG sheets (`examples/BEG/`, 7 projects) compute — brutto/netto per client basis, Fördersatz × förderfähig, caps, Σ rows, `bezahlt` reconciliation; only extracted inputs as literals; (V4) `FFFFFF99` on inputs, no fill on formulas, red via conditional formatting for invalid cells, replacing whole-row red. Reuse the EEW writer's building blocks (CF rules, input/formula cell helpers) — build them shared from the start in the EEW rewrite. Replay test against the 7 BEG ground-truth workbooks like the EEW one.
12. **Pre-fill the form from the uploaded PDFs BEFORE the run (added 2026-10-05, PLANNED)** — today the Zuwendungsbescheid pre-fill happens inside the run (`_config_with_bescheid`), so the colleague never sees what was adopted before the pipeline uses it, and since 2026-10-05 Antragstellung + Bescheid datiert are MANDATORY typed fields. Target: the moment a file is uploaded, classify it; a Zuwendungsbescheid triggers the (one) Bescheid extraction immediately, the empty form fields get filled (Antragstellung, Bescheid datiert, Bewilligungszeitraum, Förderbetrag, Förderanteil, Kennung, Kunde via the deterministic Empfänger parse) and are marked "aus Bescheid übernommen" until the user edits them; Start then runs the rest with what the user confirmed. Typed values always win; a later upload never overwrites a typed value. Same hook for a Kostenaufstellung PDF (nothing to pre-fill yet) and later BEG (FundingMeta → BEG tab). Token cost: one Bescheid call even if the user never presses Start — negligible. NiceGUI: the upload handler is async; run the extraction in a worker thread and `set_value()` the inputs.
13. **Phase 3** (after the above): full Bescheid three-way comparison (config pre-fill exists), Verwendungsnachweis draft generation, VNE-Tabelle re-run diffing.

**cost-estimation (offer extraction → `Kostenaufstellung.ods`, since 2026-08-28 `.xlsx`) is implemented and shipping.** The pipeline is `pdfplumber (text) → pydantic_ai Agent (LLM extraction) → cross-sum check → odfdo .ods writer`, with a tiered fallback for scanned PDFs (no text layer → local Tesseract OCR → cloud vision-LLM). 166 unit tests pass (corpus-backed ones auto-skip when `examples/` is absent); live E2E tests pass against `examples/EK4_204/` (offers) and `examples/EK4_322/` (a scanned client statement). The user opens the generated `.ods` in LibreOffice for verification (the Textual TUI is on the vne-generation roadmap).

**cost-estimation also ingests client cost statements, not just vendor offers.** A `DocumentKind` ∈ {`offer`, `statement`} distinguishes the two. A STATEMENT (e.g. a *Stellungnahme* listing "Kosten … für die noch kein Angebot vorliegt") has no vendor offer number and no document-stated total, so its cross-sum is reported `not_applicable` (Σ of the listed lines is carried as the figure the consultant verifies by hand) rather than `failed`. Amounts are treated as netto with the unstated-VAT status recorded; the block renders with a `SCHÄTZUNG lt. Stellungnahme …` SOLL header. See the scanned client Stellungnahme in the `examples/EK4_322/` set (a scan — exercises both the statement path and the vision-OCR fallback).

**vne-generation (invoices → VNE-Tabelle .xlsx) is implemented (v1, 2026-08-25; position-level since 2026-08-28).** `eew vne-generation <folder>` classifies every document (shared 6-class classifier — no exclude lists), extracts invoices through the same text→Tesseract→vision tiers at temperature 0 — header AND all line items, with a per-invoice position cross-sum (`check_invoice_positions`: Σ positions vs stated netto, or cumulative_netto for Schlussrechnungen; failures flag the row, stated netto stays authoritative), splits each invoice's stated netto by its vendor's cost-estimation ratio (per-vendor, not per-position; ratio sources: verified `Kostenaufstellung.ods` → consultant Kostenaufstellung PDF → live cost-estimation), and writes the 3-category (IK/NK/EK) VNE-Tabelle with red-flagged check failures. Modules: `config.py` (slim projekt.yaml: client, Bewilligungszeitraum, Bescheid figures), `extract/classify.py`, `llm/extract_invoice.py` (+`extract/invoice.py`), `match/vendor.py`, `vne/{ratios,compute,xlsx}.py`, orchestrator `extract/vne.py`. Key semantics: stated netto is authoritative (never Brutto/1,19), consultant's own invoices → EK with `beantragt EK = Σ` of them, offer-less vendors → split by the single SCHÄTZUNG/statement block's ratio when one exists (red-flagged "lt. Schätzung — prüfen"), else red flag with EMPTY split cells; duplicates and netto-0 extractions are marked `unusable` and excluded from all sums; no override mechanism (consultant edits the result file). Validated live on ZePa (Σ IK within 0,01 € of the consultant's sheet). See [PLAN.md](PLAN.md) §"Phase 2" for the full canonical plan.

**The tool serves multiple funding-program families (renamed 2026-08-28, see PLAN.md §"Program families & CLI shape").** The program is always user input; procedures nest under it: `eew cost-estimation`, `eew vne-generation`, `eew location-description`, `beg vne-generation`. **Naming rule (2026-08-30): procedures are referred to by these program/procedure names everywhere — code, docs, CLI, conversation. The legacy F1–F4 shorthand is retired; the former hidden `f1`/`f2`/`f3` aliases are removed.** Each program family gets a consultant-delivered fundability-rules document (BEG: 3-page PDF pending; EEW: separate document pending) used for per-position eligibility classification. **Output formats are Microsoft-native (decided 2026-08-28): `.xlsx` for all tables, `.docx` for documents** — most end users are on Windows; the cost-estimation `.ods` writer migrates to openpyxl and location-description's `.odt` to python-docx (build step B0.5), LibreOffice still opens everything for the consultant's own review. Older `.ods`/`.odt` references in this file are historical.

**BEG vne-generation (invoices → Kostenzusammenstellung .xlsx) is IMPLEMENTED through B5 (2026-08-31; B3 eligibility still pending the rules PDF) — canonical plan + validation results in [PLAN.md](PLAN.md) §"Phase 2b".** Modules: `beg/{funding,payments,gewerk,compute,xlsx}.py`, orchestrator `extract/beg.py`. Live-validated: Aepker row-identical to the consultant's sheet; Madroch per-vendor reconciled with flagged divergences. `förderfähig` stays empty+flagged until B3. BEG (residential KfW/BAFA) is a second funding-program family next to EEW. Key differences locked in from the 7-project corpus `examples/BEG/`: Re-Betrag is brutto (netto basis for business clients — LLM-classified from the Antragsteller, never user input), ~90 % of invoices are scans, program metadata (Vorgangsnummer, Fördersatz, caps, dates) is extracted from the project's own Antragsbestätigung/BzA + Zuwendungsbescheid PDFs (no config file), `bezahlt` is reconciled against vision-extracted Zahlungsnachweise, and `förderfähig` is pre-filled per position against a consultant-provided fundability-rules PDF (pending delivery). As part of this, **all invoice extraction (vne-generation included) becomes position-level with a per-invoice cross-sum** (Σ positions ≈ stated netto; cumulative_netto for Schlussrechnungen), and document classification grows to 6 classes (offer / invoice / antragsbestaetigung / zuwendungsbescheid / zahlungsnachweis / other), recursive and image-aware, shared across cost-estimation/vne-generation/BEG vne-generation.

**location-description (client Standortbeschreibung → `.odt`) is implemented and shipping.** It reads a project's filled *Fragenkatalog Modul 4* PDF (via `pdftotext -layout`; falls back to ad-hoc `--firma/--strasse/--plz/--stadt`), scrapes the client website (httpx + trafilatura), derives geo facts **offline, no LLM** (PLZ → Bundesland/Regierungsbezirk via pgeocode; Kreis + roads via OpenStreetMap Nominatim/Overpass), and deterministically assembles the German company/location description required by Antrag section 1.2. The LLM builds only the company profile from scraped text. Lives in `src/invoice_controller/standort/` + `src/invoice_controller/geo.py`.

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

The VNE-Tabelle this tool produces (with its Positionsabgleich sheet) is the consultant's working artifact for assembling the Verwendungsnachweis. The Verwendungsnachweis itself is currently filled in by hand by the consultant; document generation is a Phase 3 candidate for the tool.

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
| Kontrollmappe                   | REMOVED 2026-09-21 — former standalone matching workbook; see Positionsabgleich |
| Positionsabgleich               | Offer↔invoice position matching — second sheet of the VNE workbook |
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

Once the user populates `examples/` in this project with a curated corpus of close-out cases (offer + all invoices + the VNE-Tabelle the consultant built manually), that becomes the primary reference. Until then, treat live folders as read-only and do not commit any of their data.

## Current implementation (cost-estimation)

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
- `src/invoice_controller/template/xlsx.py` — the CURRENT Kostenaufstellung renderer (openpyxl, since 2026-08-28): one styled block per offer (merged SOLL header, yellow money cells, Σ row with live SUM formulas — no cached values, Excel/LibreOffice recalculate on open — optional Sonderpreis + "Nettosumme lt. Dokument" rows, percentage row storing plain ratios with a `%`-token format, red merged warning row on failed cross-sum) plus the `Kostenbeschreibung` narrative sheet. Fully programmatic — no template file. Machine readers recompute Σ from position rows (`tests/ods_inspect.py` reads .xlsx and .ods through one OfferBlock model; `vne/ratios.py:from_kostenaufstellung_xlsx` does the same for vne-generation).
- `src/invoice_controller/template/ods.py` — the LEGACY .ods renderer (pre-2026-08-28 output format), kept so old projects' files stay readable/testable. When any offer has a `CostNarrative`, also writes a second sheet `Kostenbeschreibung` (built by `_build_description_table`): per offer the same SOLL header as the cost sheet, then the two word-wrapped prose blocks (style `IC_NARRATIVE`, merged across A–F). The Kostenaufstellung cost sheet itself is left untouched. Loads the shipped template `src/invoice_controller/template/template.ods` (a **sanitized** copy with `Musterlieferant` placeholder headers — the real project template stays in the gitignored `examples/`; `TEMPLATE_PATH` is `Path(__file__).parent / "template.ods"`), captures the first block as the canonical block shape, injects custom styles (`IC_HEADER_BOLD`, `IC_MONEY`, `IC_SUM_NUM`, `IC_SPP_NUM`, `IC_PCT`, etc.) + custom data styles (`IC_N_EUR_GRP`, `IC_N_PCT_GRP`) with `number:language="de" number:country="DE"` on the root element, renders one block per offer with merged SOLL header, live `SUM()` and percentage formulas, optional Sonderpreis row, pre-computed cached values so the file displays correctly without manual recalc.
- `src/invoice_controller/cli.py` — Typer entry with one sub-app per program: `eew cost-estimation <path>` (single PDF or directory; directories are classified and only offer-classified PDFs run), `eew vne-generation <project_dir>`, `eew location-description <project_dir>` (`.docx`), `beg vne-generation <project_dir>` (currently classification + FundingMeta report; writer follows in B5). Statement docs print a yellow `ⓘ Schätzung` line instead of the offer cross-sum pass/fail line.
- `src/invoice_controller/extract/classify.py` — the shared 6-class document classifier (offer / invoice / antragsbestaetigung / zuwendungsbescheid / zahlungsnachweis / other), recursive folder walk incl. PNG/JPEG payment-proof images, filename → parent-folder → first-page-text tiers, undecidable PDFs default LOUDLY to invoice.
- `src/invoice_controller/beg/funding.py` — FundingMeta extraction (pydantic_ai, temperature 0) from the classified Antragsbestätigung/BzA + Zuwendungsbescheid: program type EH/EM, Vorgangsnummer, dates, geplante Kosten, Fördersätze/caps, and the client basis (privat→brutto / unternehmen→netto). Absent fields stay None and render red-flagged "fehlt" — never guessed, never user input.
- `src/invoice_controller/web/{app,registry,jobs,errors,gate,theme}.py` — the NiceGUI UI: `/` chooses the program, `/<program>` shows its procedures as tabs (registry `PROGRAMS`/`procedures_for`), jobs run in worker threads with an `audit.jsonl` per run (error tracebacks included), `gate.py` is the throttled single-password login, `theme.py` the EnergieKonzept-Krause brand (DESIGN.md tokens; font/logo vendored in `web/static/`). UI gotchas: Tailwind 4 utilities sit in a cascade layer (unlayered `padding` beats `py-*`), `ui.image` collapses with `w-auto`, `/laeufe` must be registered before the `/{program}` wildcard.
- `src/invoice_controller/vne/xlsx.py` — the VNE-Maske writer (Phase 2c): template 3-row blocks, every derived cell a formula, inputs yellow, red only via conditional formatting; helper inputs W (MwSt-Satz) / X (Netto lt. Rechnung) right of the template columns; B8 = Untergrenze Rechnungsdatum (AavM-Genehmigung, else Bescheid datiert). `vne/rename.py` — `<Rechnungsgeber>_<Rechnungsnummer>_<YYYY-MM-DD>.pdf`, UNKLAR for unreadable fields, ZIP + Umbenennung.csv.
- `src/invoice_controller/vne/abgleich.py` — the Positionsabgleich: offer side from `Kostenaufstellung.xlsx`/PDF positions or the run's live offers, per-vendor matching (`match/positions.py` scorer + `match/llm.py` leftovers), lump-sum rule, statement/own-company exclusions; rendered as the workbook's second sheet by `vne/xlsx.py` (the VNE-Maske sheet must stay identical to the consultant's examples — number formats are copied from them).
- `tests/conftest.py` — `FunctionModel`-based stub agent for unit tests, `--run-live` opt-in flag, fixture loaders.
- `tests/ods_inspect.py` — semantic reader for output `.ods` (used by regression tests and the diff tool).
- `tests/diff_kostenaufstellung.py` — CLI for semantic diff between two Kostenaufstellung `.ods` files.
- `src/invoice_controller/template/template.ods` — the **shipped** Kostenaufstellung template (sanitized placeholder headers; the first block's layout + styles are what the writer uses). `examples/template.ods` is the original with real EK4_204 vendor headers, kept local/gitignored.
- `examples/EK4_204/` — the regression corpus: 3 offer PDFs (three different vendors) + the consultant's hand-built ground-truth `.ods`.
- `examples/EK4_322/` — a multi-vendor close-out incl. a scanned client Stellungnahme (no text layer) that exercises both the statement doc-kind path and the vision-OCR fallback.

**Stack:** pdfplumber + pypdf + pypdfium2 + pydantic_ai + odfdo + Pydantic v2 + Typer + python-dotenv, plus pgeocode + trafilatura + httpx for location-description (geo + scrape), openpyxl for the vne-generation ground-truth readers. Default LLM provider is OpenRouter (`google/gemini-2.5-flash`). location-description also needs the system `pdftotext` (poppler) for filled AcroForm PDFs. Optional `ocr` extra adds pytesseract (local OCR tier; needs the system `tesseract` binary + `deu` language pack). uv as the package manager. Python 3.13. See [PLAN.md](PLAN.md) §"Library stack" for rationale and rejected alternatives.

**Run cost-estimation:** `uv run invoice-controller eew cost-estimation path/to/project-folder/` (after `cp .env.example .env` and adding a provider key). **Run vne-generation:** `uv run invoice-controller eew vne-generation path/to/project-folder/`. **Run location-description:** `uv run invoice-controller eew location-description path/to/project-folder --url example.com`. **Run BEG vne-generation (B1 stage):** `uv run invoice-controller beg vne-generation path/to/beg-project/`.

**Run tests:** `uv run pytest tests/unit` (fast, no API). `uv run pytest tests/e2e --run-live` (hits the LLM API; the EK4_322 statement test uses the multimodal/vision path).

**Local OCR (keep scans on-prem):** `uv sync --extra ocr` plus the system `tesseract` + `deu` language pack. Without it, scans fall back to the cloud vision-LLM (the same confidentiality boundary cost-estimation already crosses by sending offer text to the cloud LLM).

**Notable design points to remember when editing cost-estimation code:**

- The user revised the LLM rule mid-design from "no LLM in v1" to "use LLM where it's better, deterministic where it's better" — and explicitly required **vendor-agnostic extraction**. The LLM is the primary extraction path; cross-sum is the safety net. Don't reintroduce per-vendor parsers.
- The `.ods` writer must explicitly set `number:language="de" number:country="DE"` on the **root element** of any custom data style. The template's `N172` has the locale only on the currency-symbol child, which caused numbers to fall back to system locale (`1,000.00` in en-US). The fix shipped in `template/ods.py`.
- Σ and percentage cells store BOTH a precomputed cached value AND a live formula. LibreOffice prefers the cached value on open; without precompute the cells would show `0` until manual recalc.
- Scratch outputs go to `./tmp/` (project-local), not `/tmp` or `$CLAUDE_JOB_DIR/tmp`. See the `project_tmp_folder` memory.
- **Statements have no cross-sum.** Don't "fix" `check_statement` to fail or to fabricate a total — a statement legitimately has no document-stated total, so `not_applicable` is the correct state and the consultant's review is the backstop. Detection is filename-first (`kind_from_filename`) then the LLM's `doc_type`; a statement filename overrides an LLM "offer" guess.
- **A failed offer cross-sum is written loudly, not silently.** The block still renders (the consultant needs the rows to review), but it gets a "Nettosumme lt. Dokument" comparison row under Σ and a red merged `⚠ KREUZSUMME WEICHT AB …` warning row (style `IC_WARN`) at the block end; the CLI prints a per-file ✗ plus an end-of-run summary naming the failed PDFs. A mismatch can be the vendor's own arithmetic error (the Reiling Eggersmann offer's stated Zwischensumme is 27,50 € above the sum of its own positions), so don't assume extraction is at fault.
- **Both LLM agents run at temperature 0** (`DETERMINISTIC_SETTINGS` in llm/extract.py, shared by summarize.py). Measured on the Reiling corpus: default sampling flapped judgment fields (optional flags, Kostenkategorie, description wording) between runs; at temperature 0 consecutive runs are near-identical. The one field that still flapped — whether a 0,00-€ "inklusive" line counts as optional — is pinned deterministically by `unflag_inklusive_positions` (extract/offer.py, consultant-approved: an inklusive freebie is binding scope, never a droppable option). Residual variance is description-wording cosmetics only; bit-perfect repeatability is not achievable with a cloud LLM — the deterministic guards + consultant review remain the correctness backstop.
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

A shared `bafa-tooling-common` library extraction was once planned (Phase 4) and is DEPRECATED since 2026-09-03 — code duplication with ESK-Generator is accepted permanently; the tools stay independent.

### Bulk verification UX is mandatory

With 10+ invoices and potentially 100+ positions, per-line click-through verification will not scale. The verification UI must support:

- Bulk-approve all `high`-confidence matches in one action
- Focus the consultant's review on `medium` and `low` confidence
- Quick keyboard navigation
- Override-and-continue rather than approve-each

Designs that require clicking through every position are unacceptable. Plan around this constraint.

## Architectural decisions and rationale

### Why standardised workbooks rather than a markdown report?

The user works in LibreOffice. The output of this tool feeds directly into the Verwendungsnachweis preparation, which is itself manual table work. A `.ods` workbook lands in the consultant's native tool, supports row-level edits, and serves as the working artifact for the rest of the close-out process. A markdown report would force a copy-paste into Excel anyway.

### Why independent re-extraction rather than reading ESK-Generator's `.ods`?

The user chose this in design discussion. Trade-off: duplicate extraction work per project, but the tool works for projects that did not go through ESK-Generator (older projects, projects done before ESK-Gen existed). The once-planned Phase-4 shared library is deprecated (2026-09-03) — the duplication is accepted permanently.

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
- **Do not skip the verification step on matching.** Auto-matched proposals are always proposals; the Positionsabgleich sheet presents them as proposals (confidence tier, red on any variance) for the consultant to confirm — it never feeds back into a VNE figure.
- **Do not silently apply or strip MwSt./VAT.** Offers and invoices are usually netto; if an amount is brutto without clear breakdown, flag it.
- **Do not match across vendors by default.** Same item invoiced by a sub-contractor with a different name is a special case requiring explicit override.
- **Do not silently commit when re-running** for a project that already has a generated output workbook. Hash-detect changes, require `--overwrite`, produce a diff report.
- **Do not couple this tool to ESK-Generator** in v1. The user has chosen the independent boundary deliberately. Resist the temptation to read the ESK-Gen `.ods` "for convenience."
- **Do not write code documentation for what code does** — only why. Same convention as ESK-Generator.
- **Do not commit client-confidential data** to the repo. Examples must be anonymised if shared beyond the user's machine.

## Quick orientation for new sessions

1. Read [README.md](README.md) for install / run / what cost-estimation does today.
2. Read [PLAN.md](PLAN.md) for the architecture, the actually-built library stack, and the Phase 2 (vne-generation) next-step list.
3. Read this file (CLAUDE.md) for domain context (funding lifecycle, German terminology, what NOT to do).
4. Skim `src/invoice_controller/` — the file pointers above tell you what each module does.
5. Run `uv run pytest tests/unit -v` to see the unit suite green; that's the regression anchor.
6. Look at `examples/EK4_204/` — 3 offer PDFs and the consultant's hand-built ground-truth `.ods` are the correctness reference.
7. Look at `tmp/ek4_204_template_v7.ods` (most recent generated output, gitignored) and the corresponding PDF preview if it exists, to see the current cost-estimation output shape.
8. The sister project ESK-Generator (`/home/badtoni/Workspace/EnergiaConsult/ESK-Generator/`) shares the funding domain — its CLAUDE.md is worth reading.
9. When in doubt about correctness vs. convenience, choose correctness.
