"""R7 evaluation harness — type-aware scoring of extractor output against ground
truth (VRDU-style typed matching + GLIRM-style line-item pairing). Used by the
opt-in live eval tests and by ad-hoc benchmarks (e.g. the R9 OCR-tier comparison);
lives in tests/ because it consumes the gitignored corpus readers, not shipped code."""
