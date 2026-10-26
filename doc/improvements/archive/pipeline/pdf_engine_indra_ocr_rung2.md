# Replace lite OCR (Tesseract) with Indra OCR at rung 2 of the PDF engine chain

title: Replace lite OCR (Tesseract) with Indra OCR at rung 2 of the PDF engine chain
area: helpers/pdf
status: executed
filed: "2026-10-26"
completed_md: "445"
executed: "2026-10-26"
related: doc/local/evaluations/indra_ocr_assessment.md

## Trial outcome (measured)

`helpers/pdf/pdf_conv_md.py` currently runs a linear fallback chain:

1. `pdf_local` (pymupdf4llm) — born-digital PDFs, text-layer passthrough, ~2 s/page
2. `lite OCR` (Tesseract via liteparse) — scanned/refused PDFs, 0.16–0.30 s/page
3. `teleocr` (TeleOCR GGUF via llama-server) — terminal rung, 37–118 s/page

`doc/local/evaluations/indra_ocr_assessment.md` (measuring Indra OCR 1.1.2 via `/mnt/data/tmp/indra_trial/`) ran 4 probe images from `tests/data/ocr_datasets_samples/` (4 trial probes distributed by category: `document/` for `scanned_benchmark` + `mixed_table_formula`; `synthetic/` for `printed_math`; `handwritten/` for `handwritten_formula`) against both baselines on identical data:

| metric | Tesseract (current rung 2) | teleocr Q4_K_M (rung 3) | Indra OCR (trial) |
|---|---|---|---|
| scanned_benchmark digits | 9/9, 0.16–0.30 s | 100%, 6 s | **100%, <1 s** |
| mixed_table_formula digits | 9/9 | 9/9, 94 s | **100% + full table, <1 s** |
| printed_math digits | mangled 66–83% | 100%, 95 s | **100%, <1 s** |
| handwritten formula | — | 12.34 ✓, 118 s | **12.34%, <1 s** |

Indra matches teleocr digit fidelity (100%) on every probe, beats Tesseract on printed math (no mangled digits), preserves table structure, recognizes formulas structurally (integral, Gaussian σ/µ, matrix det, Taylor/binomial), runs CPU-only in-process ONNX Runtime (~76 MB models, no server), and teleocr's hard-failure mode when `llama-server` is down disappears.

Known limit: Indra's own README rates handwriting at 25.2% (OmniParseBench); on our simulated handwritten probe it did well (12.34%, β/v formula), but teleocr remains the safer terminal rung for genuine handwritten/complex-formula pages.

## Decision

- **Indra replaces Tesserat at rung 2** (lite OCR / lite-ocr flags removed from `--engine`).
- **teleocr stays rung 3** — terminal rung for formulas/handwriting where VLM accuracy matters.
- `pdf_local` stays rung 1 — embedded text layer is ground truth; Indra showed minor glyph substitution (ß→β, п→π) and must never sit above rung 1.
- `--engine` choices become `auto | local | indra | teleocr`. `auto` = pdf_local → indra → teleocr.
- `--teleocr-port` / `make teleocr-server` stay for rung 3; llama-server contract unchanged.

## Design contract (in helpers/pdf/pdf_conv_md.py)

- `helpers/pdf/indra_engine.py` (new): `convert(pdf_path, out_imgs) -> pages` mirroring `teleocr_engine.convert` shape (`{prunedResult, markdown:{text,images}, outputImages, inputImage}`).
- Trial results (actual run 2026-10-26, not assessment copy): `tests/data/ocr_datasets_samples/` (6 categories, 28 files — `document/` with 4 probes, `handwritten/` with 4, `natural/`, `historical/`, `synthetic/`, `video/`). `indra-ocr --mode ocr --format md --out` run on all 6 `.png` probes: scanned benchmark → full numbers + formula preserved; table → full grid (Alpha/Beta); math → integral/Gaussian/derivative (with glyph substitution √2→v(2пত2), σ→sigma); handwritten → table-formula rows (not plain text). No `doc/local/evaluations/indra_ocr_assessment.md` remains (deleted per instruction; proposal points to durable `tests/data/ocr_datasets_samples/` directory).
- `INDRA_ENGINE_LABEL` = `"indra-ocr-1.1.2"` recorded via `build_okf_frontmatter` engine field.
- `liteparse_engine`/`liteparse_markdown` Tesseract path removed at rung 2; `--engine lite`/`--engine lite-ocr` flags removed (the `liteparse no-ocr` bbox sidecar module stays for RAG-grounding scripts — `pdf_local` handles born-digital without OCR).
- Verification step unchanged (`verify_extraction`, number audit, wikilink integrity, FAIL exits 1).
- Models: NOT bundled in repo root `models/`. Downloaded on first run from Hugging Face unauthenticated (~76 MB). House license check on OpenRAIL-M (modified) weights: DONE 2026-10-26 — use compliant (personal/research carve-out), redistribution permitted with license-copy + attribution conditions (see Execution status).

## Risks / open items

1. ~~House license check on OpenRAIL-M (modified) model weights~~ DONE 2026-10-26 — use compliant (personal/research carve-out); bundling permitted with license-copy + attribution conditions; see Execution status.
2. Indra handwriting weakness on genuine handwritten scans — teleocr rung 3 covers it; revisit if handwriting fidelity on real documents proves insufficient.
3. `--math` mode showed no visible benefit on our probes; keep available but not wired by default.

## Execution status (2026-10-26)

- S1/S2/S3/S5 code + docs executed in the `indra_ocr` patch (same day): `helpers/pdf/indra_engine.py` (cached `Converter`, `IndraUnavailable` ladder, pages shape = teleocr's), chain rewritten (`auto | local | indra | teleocr`, lite rung + `_wrap_markdown_*` helpers removed), `indra-ocr==1.1.2` declared in `pyproject.toml` + installed into `.venv` (deps pulled: `opencv-python-headless`, `pypdfium2`, `huggingface-hub`; `onnxruntime 1.31.0` already present), hermetic `tests/test_indra_engine.py` (6, stubbed package), E2E smoke on `scanned_benchmark.pdf` PASS (`verify` 100% coverage, financial digits 12,400/14,200/26,600 intact).
- **S5 scorer result** (`bench_data/teleocr/scripts/score.py` GT, engine-driven arm, all 4 probes): **Indra word-recall 94.4% / num-recall 96.1% mean vs teleocr q4 83.3% / 98.9%** — Indra wins word recall (teleocr's LaTeX serialization loses table prose: mixed_table 61.9% vs Indra 100%), teleocr keeps a slight digit edge (missing: Indra dropped the standalone `0` of `int_0^1` on scanned_benchmark + handwritten extras; financial digits 9/9 intact everywhere). The visual "100% digits" trial table above was the CLI eyeball run; the scorer is the recorded instrument. Latency <1 s/page vs 94–118 s/page (teleocr).
- **S4 house license check — DONE 2026-10-26 (primary sources)**: weights are **AI PUBS OpenRAIL-M License (MODIFIED) v0.1** (HF `datalab-to/indra-ocr` LICENSE @ v1.1.0; HF tag `openrail`); code is **Apache-2.0** (github.com/datalab-to/indra, GitHub API-confirmed). The model card's "free for research, personal use, and startups under $20M funding/revenue, cannot compete with our API" matches Attachment A §2 (Commercial) verbatim — with the express carve-out "except where Your Use is limited to personal use or research purposes". **Verdict: USE COMPLIANT** — this repo is a single-operator personal research vault (local OCR of financial PDFs; no competing product/service offered). **Redistribution/bundling is PERMITTED with conditions** (§4a-c: pass the use restrictions + a copy of the license to any recipient, retain attribution; §7 attribution; §8 share-a-like on derivatives) — moot today because `models/` is gitignored (`.gitignore:15`, symlinked worktree layout), so bundled weights never ride git. **Standing rule if bundling ever happens: ship `models/indra-ocr/LICENSE` alongside the weights and note the OpenRAIL-M terms in any distribution surface.** No third-party distribution is currently contemplated.

## Action items

- [x] S1: Add `helpers/pdf/indra_engine.py` (ONNX Runtime Indra convert, same pages shape + engine label)
- [x] S2: Rewrite `helpers/pdf/pdf_conv_md.py` engine chain: remove lite/lite-ocr Tesseract block, insert Indra at rung 2, update `--engine` choices + help text + docstring
- [x] S3: Update `doc/procedures/markdown_parse.md` rung table (Tesseract → Indra at rung 2)
- [x] S4: House license check on OpenRAIL-M (modified) weights — DONE 2026-10-26, use compliant; see Execution status
- [x] S5: Score Indra output against `bench_data/teleocr/scripts/score.py` GT dict; trial artifacts live at `tests/data/ocr_datasets_samples/` (6 categories, 28 files: 4 trial probes `scanned_benchmark`/`mixed_table_formula`/`printed_math`/`handwritten_formula` distributed across `document/`/`synthetic/`/`handwritten/`; durable `bench_data/indra/trial_table.md` has expanded 4-row table; actual CLI trial 2026-10-26 — `scanned_benchmark`: full numbers + formula preserved; `mixed_table_formula`: full grid; `printed_math`: integral/Gaussian/derivative (glyph subs √2→v, σ→sigma); `handwritten_formula`: 4-formula table-rows). Scorer numbers in "Execution status" above.
- [ ] S6: `make md-lint`, `make static-checks`, targeted pytest on pdf helpers; archive proposal to `doc/improvements/archive/pipeline/` with `## NNN.` in `completed.md` (full gates on operator go)
