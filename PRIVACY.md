# Client-data exposure & anonymisation design (R10, 2026-09-03; ops section updated 2026-09-29)

Goal (user, 2026-08-30): anonymise as much client AND vendor data as possible in
every input document across all procedures. This document is the in-depth analysis
that todo required: exposure inventory, measurements, the masking design, the
local-routing recommendation, and the residual-risk statement.

**Honesty rule (binding):** the tool may claim *reduced exposure*, never
"anonymised". Every masking layer is best-effort; scans and free-text references
can always leak.

## 1. What leaves the machine today, per procedure

| Procedure | LLM payload | Client data inside |
|---|---|---|
| eew cost-estimation | full offer text or page images | client name/address (offer recipient), site details |
| eew vne-generation | full invoice text/images; offers only when no Kostenaufstellung is uploaded; for the Positionsabgleich one extra call per vendor with the unmatched position descriptions + amounts | client name + billing address (rule I7 extracts the recipient deliberately); the match payload itself carries no client identity |
| eew location-description | scraped public website text | client-identifying but public |
| beg vne-generation | invoices + Antragsbestätigung/BzA + Zuwendungsbescheid + payment-proof images | heaviest: names, home addresses, Vorgangsnummern — often PRIVATE individuals; bank data on proofs |

Not sent, ever: classification, cross-sums, ratio math, folding, table writers,
geo lookups, the corrections diff. Local Tesseract changes *how* text is obtained,
not where it goes afterwards.

Additional exposure surfaces beyond the LLM: the OpenRouter broker (multi-provider
routing — see §6), the public web UI on the shared server (since 2026-09-19;
`invoice.bestdomaininthesolarsystem.com` since 2026-09-28), and the `webruns` volume
on that server (uploads + outputs + audit log for the last 100 runs).

## 2. Measurements (corpus, 2026-09-03)

- 124 close-out documents, 87 with a text layer (70 %); 37 scans.
- The client name appears **verbatim in 72 %** of text-layer documents
  (avg 2,2 occurrences) and in **variant-only form in 0 %** — exact-string
  replacement plus a small variant set (umlauts, legal forms, spacing) covers
  essentially the whole text-path surface. **Corrected 2026-09-23 (EK4_333):** the
  consultant *types* the client name, and a typed "MKT - Mannel …" against a printed
  "MKT Mannel …" defeated exact matching on all seven invoices — since then the check
  is token-based with spelling tolerance and the mask a punctuation-tolerant pattern
  (`privacy.py`).
- Consequence: text-path masking is HIGH-value and cheap; scan masking is
  impossible before OCR and unreliable after (boxes can miss letterhead logos).

## 3. Masking design — text path (implementation phase A1)

The one identity we always know in advance is the client (Projekt/Kunde fields in
the UI, `--projekt`/config in the CLI). Design:

1. **Variant builder:** from client name + address generate match patterns:
   case-insensitive, umlaut/ß spellings, legal-form drift (`GmbH & Co. KG` ↔
   `GmbH`), street abbreviations (`Str.`/`Straße`), spacing/hyphen variants.
2. **Local recipient check FIRST:** before masking, verify the expected client
   name/address occurs in the raw text → this replaces rule I7's LLM-extracted
   recipient for the address check (deterministic, and strictly better: the check
   no longer depends on extraction fidelity). The LLM schema keeps
   `recipient_*` fields nullable; masked runs expect them null.
3. **Mask:** replace all variant hits with `[KUNDE]` / `[KUNDENADRESSE]` in the
   page texts (pdfplumber or Tesseract output) before any LLM payload is built.
   Applies to: cost-estimation and vne-generation extraction payloads (the
   Positionsabgleich's matching call sends only descriptions and amounts).
4. **Grounding interplay:** the R2 verbatim-amount guard operates on the masked
   text — amounts are untouched, no conflict.
5. **Vendor masking is NOT done:** vendor identity is load-bearing (grouping,
   ratios, folding, matching). Reducing vendor exposure is only achievable via
   local routing (§5), not masking.

Residual text-path leaks (accepted, documented): client references inside position
descriptions ("Lieferung an …", site addresses), contact persons, the vendor's
customer number, phone/mail of client staff. Estimated small (the 2,2 avg
occurrences are letterhead/address blocks), never zero.

## 4. Where masking cannot work

- **Scans / vision path** (37/124 corpus docs; ~90 % of BEG invoices): page images
  go to the model unmodified. OCR-box redaction was considered and REJECTED — a
  single missed box (logo letterhead, handwriting) leaks anyway while suggesting
  safety. Policy: no image masking; sensitive scans belong to local routing (§5).
- **BEG funding documents:** extracting the client-side metadata IS their purpose.
- **Zahlungsnachweise:** bank data, mostly screenshots — the most sensitive input
  and the least maskable.
- **location-description:** inherently about the client (public web content).

## 5. Local-routing recommendation (implementation phase A2)

Where sensitivity is highest, masking is weakest — the correct lever is keeping
the payload on-prem, per the project's original per-section-routing principle:

- Add an **Ollama route** to `_resolve_model()` (env `OLLAMA_MODEL` +
  per-procedure override, e.g. `IC_LOCAL_PROCEDURES=beg-payments,beg-funding`),
  so selected extractions run against a local vision-capable model.
- Priority order for going local: 1. Zahlungsnachweise (bank data),
  2. BEG funding docs of private clients, 3. BEG invoices (scans), 4. the rest.
- Quality caveat: local models will underperform Gemini Flash on scans; the
  existing guardrails (position cross-sum, amount checks, flags) are exactly the
  safety net that makes a weaker-but-local model workable — failures surface as
  review flags, not silent errors. Validate with the R7 harness + the 4-arm
  benchmark script (`tmp/bench_scan_tiers.py`) before switching any tier.

## 6. Operational measures (implementation phase A3 — mostly policy)

The web UI has run on a shared server since 2026-09-19 (Docker behind the shared
Caddy proxy; public host `invoice.bestdomaininthesolarsystem.com` since 2026-09-28).
That changes the operational picture compared with the original own-laptop/ngrok
assumption:

- **Access:** the app's own login page is the only gate (`IC_WEB_PASSWORD`, one
  shared password; `web/gate.py` locks an address out for 15 min after 5 failures
  in 5 min; the app refuses to serve on a non-loopback bind without a password).
  Caddy's basic_auth was dropped on 2026-09-28 because the browser dialog was
  indistinguishable from a broken site for the colleagues. Consequences: the
  password is shared by all colleagues — no per-person accounts, no audit of *who*
  ran what; rotate it when someone leaves. Caddy ignores client-supplied
  `X-Forwarded-For`, so the lockout keys on the real address.
- **Transport:** TLS via Let's Encrypt; the app port is never published on the
  host; the proxy logs at WARN level only (no request URIs with client filenames).
- **Run retention — DECIDED 2026-09-29 (user):** the `webruns` volume keeps uploads,
  outputs and `audit.jsonl` (which names every uploaded file and, since
  2026-09-19, error tracebacks) for the last **100** runs. Rationale: the current
  box is a *test* server, reachable only by the consultant and the colleagues who
  hold the password; the run history is the material for debugging the pipeline
  before the move to the company server or a public site with a login portal.
  **Revisit before that move** — options then: shorten to N days, or delete inputs
  after a successful run and keep only outputs + audit log (the audit log names
  client files too).
- **OpenRouter broker — code side DONE 2026-09-29, account side = user:** every
  request now carries `provider.data_collection = "deny"` (`llm/extract.py`,
  `_openrouter_provider_config`), so only hosts that do not store prompts may serve
  us; OpenRouter refuses (404 → 'llm-unavailable') instead of falling back. Probed
  live for `google/gemini-2.5-flash`: under deny, under `zdr` (zero data
  retention) and when pinned to the `google-vertex/eu` endpoint the request is
  served by Google itself — the model has EU-resident and ZDR endpoints. Env knobs:
  `OPENROUTER_ZDR=1`, `OPENROUTER_PROVIDERS=google-vertex/eu` (+
  `OPENROUTER_ALLOW_FALLBACKS=0`) for EU-only routing. **Decided and active on the
  server since 2026-09-29:** ZDR + `google-vertex/eu` + no fallbacks (an EU-endpoint
  outage fails runs loudly instead of routing elsewhere). The account-level switch
  at https://openrouter.ai/settings/privacy (providers that may train on inputs →
  off, paid and free) was set by the user the same day — the account setting is
  the upper bound for every key.
- **Verification copies:** for debugging, run inputs are sometimes copied from the
  server into the developer's gitignored `tmp/`; they must stay there and be
  deleted when the investigation is over.
- **Live project folders:** remain read-only for tooling; nothing from
  `FÖRDERPROJEKTE/` is ever copied into the repo or a run directory by the tool.

## 7. Status

A1 (masking + local recipient check) is implemented (`privacy.py`, 2026-09-03,
hardened 2026-09-23). A2 (local Ollama routing) and A3 (the two open decisions
above) are tracked in CLAUDE.md's open-todo list, which is the single place for
status. This document holds the analysis and the reasons; the honesty rule at the
top stays binding for any future feature.
