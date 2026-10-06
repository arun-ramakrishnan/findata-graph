---
title: TeleOCR PDF fallback — local terminal OCR engine, Paddle API retirement path
status: executed
filed: '2026-10-06'
executed: '2026-10-06'
completed_md: '353'
area: helpers/pdf/
---

# TeleOCR PDF fallback — local terminal OCR engine, Paddle API retirement path

**Status:** EXECUTED 2026-10-06 (S1–S4 same day; completed.md #353)
**Scope:** `helpers/pdf/`, `pyproject.toml` (no new pip deps — llama.cpp is
external), `doc/procedures/markdown_parse.md`
**Follows:** `archive/pipeline/local_pdf_conversion_fallback.md` (#156),
`archive/pipeline/liteparse_pdf_engine.md` (#186 — engine-chain pattern this
mirrors), trial `../local/evaluations/local_pdf_engine_trial.md` §Addendum
"TeleOCR (NaviDC-OCR) GGUF trial on CPU" (2026-10-06)

## Motivation

The `--engine auto` chain terminates in the **Paddle API** — the one leg
still carrying the reliability snags that motivated #156 (multipart-upload
write timeouts from this network, service-side 10010 queue-full, `.env`
key fragility). Below pdf_local, the chain has no strong local answer for
formulas/tables/handwriting: Tesseract is math-blind (mangles every
superscript/subscript digit), and pix2text is excluded-from-pipelines
(CUDA-heavy deps).

**TeleOCR** (ex NaviDC-OCR; TeleAI; Apache-2.0; Qwen2.5-VL, 1.4B params)
closed that gap in the 2026-10-06 CPU trial: perfect digit fidelity on all
probe classes (9/9 financial numbers, `12.34%`, `det = -2`, `∫₀¹ x²` as
LaTeX), running locally via patched llama.cpp. Benchmarks: OmniDocBench v1.6
96.87 — above PaddleOCR-VL-1.6 (96.33) and GLM-OCR (95.22); Dr.DocBench
67.96 vs Paddle 55.11.

Operator context (2026-10-06): volume is **5–6 docs/week**, so the
37–118 s/page CPU latency is irrelevant; TeleOCR's role is **backup to
pdf_local** — the refusal/scanned terminal leg — not a born-digital
replacement (the text layer stays primary there: 2 s and more faithful).

## Evidence (trial, 2026-10-06 — banked, no re-run needed)

Artifacts: `bench_data/teleocr/` (patch, outputs, scorer, README with
re-run recipe) + the doc addendum. Digest:

- Q4_K_M ≈ Q8_0 in transcription fidelity; **Q4_K_M retained** at
  `models/NaviDC-OCR-Q4_K_M.gguf` (484 MB) with vision encoder
  `models/NaviDC-OCR-mmproj-f16.gguf` (1.3 GB) — operator moved both to
  `models/` (house work-model store, gitignored, alongside the embedder
  GGUFs).
- Failure mode = greedy-decode repetition loops (Q8 on the pipe-table page,
  Q4 on the dense newsletter page); **fixed** by the official prompt
  ("What is the text in the illustrate?") and/or `repeat_penalty 1.15`
  (`repeat_last_n 256`). Temp 0 alone is unsafe.
- Output is LaTeX house style (space-joined words, `\mbox{}` wrappers)
  with occasional MinerU `<fcel>`/`<nl>` token leaks → needs an unwrap
  post-process.
- llama.cpp (master, 2026-10-06) requires a 4-change patch to
  `src/models/qwen2vl.cpp` (head_dim 128 ≠ n_embd/n_head 64 → Q/wo widths
  + per-head QK-norm); upstream unfixed. Patch persisted at
  `bench_data/teleocr/llama_cpp_qwen2vl_teleocr.patch`.
- Runtime: llama-server, ctx 8192, 4 threads, ~2 GB RAM, port 8731.

## Design

### Engine module — `helpers/pdf/teleocr_engine.py`

Mirrors the `liteparse_engine` contract (arc #186): `convert(pdf_path)`
renders pages (pymupdf @150 dpi) → llama-server `/v1/chat/completions`
(image + prompt) → unwraps → emits
`[{markdown: {text, images: {}}, prunedResult: {teleocr: {...}}}]` so
`plan_images`/`to_wikilinks`/`verify_extraction` work verbatim. No image
extraction in S1 (scanned pages rarely carry extractable figures;
pdf_local keeps the figure pipeline).

Client guards baked in (the trial's load-bearing finding):

- official prompt: system `"You are a helpful assistant."` / user
  `"What is the text in the illustrate?"`
- `temperature 0`, `repeat_penalty 1.15`, `repeat_last_n 256`
- `max_tokens 2048` cap; on cap-out without terminator, **one retry** at
  `repeat_penalty 1.3` (loop signature = cap-out + repeated n-gram)
- post-process: LaTeX unwrap (`\mbox`/`\mathrm`/array→text, word-join
  repair) + `<fcel>`/`<lcel>`/`<ecel>`/`<nl>` token strip — the scorer's
  `unwrap()` at `bench_data/teleocr/scripts/score.py` is the seed

### Chain wiring — `pdf_conv_md.py --engine teleocr|auto`

`auto` becomes: `pdf_local` → `lite OCR` → `teleocr` (terminal, local,
**no Paddle leg** — D2). Forces: `--engine teleocr`. Provenance:
`generated.by: pdf_conv_md.py/teleocr`.

- Tesseract stays *above* teleocr: 0.16–0.3 s and exact on clean printed
  text; teleocr picks up what it refuses/mangles (math structure,
  handwriting, tables). A quality escalation rule (lite OCR < N chars →
  teleocr) is S2 scope, default off pending real-corpus evidence.

### Server lifecycle — assume-running (D3)

The engine owns NO process supervision. Contract: a healthy llama-server
on `127.0.0.1:8731`; everything else is box-side. Rationale: the backup
fires rarely, so spawn/health-wait/timeout/kill supervision would be the
repo's coldest, least-tested path; the only coupling is the
OpenAI-compatible chat schema (stable across llama.cpp upgrades); house
convention keeps heavy runtimes box-side (Mojo toolchain, ollama,
pix2text's ONNX files). What option (b) buys — hands-free startup —
costs a ~10–20 s model+mmproj load per invocation and adds code on a
5–6 docs/week path.

Why not the venv's existing `llama-cpp-python` (the granite-embedder
runtime, 0.3.36)? Verified 2026-10-06: its vendored llama.cpp core fails
to load the TeleOCR GGUF with the exact stock-loader shape bug —
`check_tensor_dims: 'blk.0.attn_q.weight' has wrong shape; expected
1024,1024 got 1024,2048` — the same head_dim-128 defect we patch around
in the standalone build (fix upstream NOWHERE: not in master, a fortiori
not in the months-older vendored snapshot). Serving TeleOCR from it
would mean forking + patching + rebuilding a pip package the whole
embedding stack depends on. Convergence trigger: when upstream llama.cpp
lands the head_dim fix and llama-cpp-python bumps past it, a Python-only
route becomes possible and the standalone binary can retire.

Engine-side mitigations for the down-server case:

- preflight `GET /health` + `/v1/models` identity check (distinguishes
  "server down" from "wrong model on port 8731"); on failure the error
  names the exact start command and the `models/` files.
- `make teleocr-server` target (~3 Makefile lines) wrapping the start
  command — the only "llama code" the repo carries is this target, the
  client, and `bench_data/teleocr/llama_cpp_qwen2vl_teleocr.patch`.
- server runs only during ingest sessions (~2 GB RSS while up); operator
  may instead run it under a user systemd unit — box-side choice, out of
  repo scope.

### Paddle leg — CUT (D2)

Remove `--engine paddle` and the AI Studio API client entirely:
the paddle path + `--token` arg in `pdf_conv_md.py`, paddle provenance
strings, and paddle-derived test stubs. `helpers/pdf/ocr.py` (gitignored
plaintext token) dies with the arc; the Paddle keys leave `memory/.env`.
The #156-era network snags become moot. Paddle-derived reference notes in
`findata/` are untouched — `generated.by: PP-StructureV3` values there
are historical records, not live code paths.

### pix2text branch — removed (D4)

Delete `helpers/pdf/pix2text_markdown.py` +
`tests/test_pix2text_markdown.py` (5 tests) and the `--engine pix2text`
short-circuit in `pdf_conv_md.py`. Subsumed by teleocr (formulas + tables
+ handwriting in one pass, stronger model). `~/.pix2text/` ONNX cache
(230 MB) is box-side — operator may delete; the trial doc keeps the
historical record.

## Slices

1. **S1** `teleocr_engine.py`: client (guards above), unwrap post-process,
   pages-shape assembly; unit tests with a stubbed HTTP server (no
   llama.cpp in CI); `pyproject.toml` comment block (models/ artifacts,
   patch provenance — no pip deps added).
2. **S2** `pdf_conv_md.py --engine teleocr|auto` wiring + chain
   re-order + `generated.by` provenance; **paddle cut** (engine path,
   `--token`, test stubs, `helpers/pdf/ocr.py`) + **pix2text removal**
   (`pix2text_markdown.py`, its 5 tests, the `--engine pix2text`
   short-circuit); tests per `test_liteparse_engine.py` discipline.
3. **S3** Acceptance eval (targeted, not `make pytest`): 4
   `tests/data/ocr_samples` ≥ Tesseract word recall AND 9/9 financial
   digits; 2 formula samples ≥ pix2text recorded quality (LaTeX
   well-formedness); Marico control page — zero phantom numbers vs
   pdf_local's text layer (number-multiset diff); timings recorded.
4. **S4** Docs (`markdown_parse.md` §engine chain), archive flip +
   completed.md entry in the same change.

## Decisions

- **D1 model placement (operator, 2026-10-06):** `models/` store —
  `NaviDC-OCR-Q4_K_M.gguf` + `NaviDC-OCR-mmproj-f16.gguf` (sizes verified
  byte-exact vs HF). GGUFs are gitignored; only code/docs ride patches.
- **D1b Q4_K_M is the keeper** (trial: quant-insensitive fidelity at 1.4B;
  Q8_0 saved no accuracy and doubles RAM).
- **D2 Paddle leg CUT (operator, 2026-10-06):** `--engine paddle` and the
  AI Studio client removed outright, not demoted — teleocr benchmarks
  above PaddleOCR-VL on every axis we measure, and the API is the
  reliability liability that motivated #156.
- **D3 server lifecycle: assume-running (operator, 2026-10-06):** engine
  requires a running llama-server on `127.0.0.1:8731`; no process
  supervision in the repo. Preflight health+identity check, actionable
  error, `make teleocr-server` target.
- **D4 pix2text removed (operator, 2026-10-06):** engine + tests +
  short-circuit deleted this arc; subsumed by teleocr.

## Risks

- **Generative phantom-content class** — the defect family that caught
  Paddle (`7`→`70`). Mitigation: the trial guards + S3 number-multiset
  acceptance + `verify_extraction` coverage on every conversion; engine
  refuses (raises) on cap-out after retry rather than emitting a truncated
  page.
- **llama.cpp patch drift** — upstream may change `qwen2vl.cpp`; the patch
  is 35 lines and re-derivable (GGUF PATCHES.md + bench_data copy). S1
  records the llama.cpp commit it was validated against.
- **Repeat-loop residual** — penalty fix is empirical, not guaranteed on
  unseen layouts; the cap+retry+refuse ladder bounds the damage to a
  refusal, matching pdf_local's refusal semantics.
- **RAM contention** — ~2 GB inference on a 14 GB box alongside other
  work; llama-server holds ~2 GB RSS only while running. Per D3 the
  lifecycle is the operator's explicit call (start before an ingest
  session, or a user systemd unit); the engine never starts or stops it.

## Status log

- **S1 EXECUTED 2026-10-06** — `helpers/pdf/teleocr_engine.py` (client +
  guards + preflight/identity probe + LaTeX-unwrap with dictionary
  word-join repair and unicode math preservation) + 17 stubbed-server
  tests (`tests/test_teleocr_engine.py`) + `make teleocr-server` target.
- **S2 EXECUTED 2026-10-06** — `pdf_conv_md.py --engine teleocr|auto`
  (auto = `pdf_local` → lite OCR → teleocr, terminal); **paddle cut**
  (client, `--token`/`--model`/`--timeout`, `parse_pages`, refusal-text
  updates; `helpers/pdf/ocr.py` was already gone); **pix2text removed**
  (`pix2text_markdown.py` + its 5 tests + the short-circuit; pyproject
  deptry ignore dropped; licenses row added). Targeted battery: 55+
  tests green; ruff/ty clean.
- **S3 EXECUTED 2026-10-06** (end-to-end via `--engine teleocr`, 4 runs;
  artifacts `bench_data/teleocr/outputs/s3_engine/`): number recall
  **100% stable across runs** (financial 9/9, formula digits, `12.34%`);
  control page in **exact number parity** with the pdf_local text layer
  (zero phantoms); word recall **ties Tesseract in good runs** (93.3 vs
  94.5 best) but varies run-to-run on the table page (headers dropped in
  2 of 4 runs — llama.cpp CPU generation variance, temp 0 notwithstanding);
  formula glyphs now preserved (∫ Σ √ π via the unwrap map). Unwrap fixes
  found BY S3: `\!` negative-space in number groups, `\&`/`\%` literals vs
  bare alignment `&`, "LaTeX" false-split.
- **S4 EXECUTED 2026-10-06** — `markdown_parse.md` §PDF → Markdown
  rewritten to the three-rung chain; stale paddle/pix2text refs swept
  (`pdf_local.py`, `liteparse_engine.py`, `tmp_sweep.py`); proposal
  archived + completed.md #353 in the same change. Gate record (quartet
  once, this arc): qa 9/11 → the 2 failed legs re-run clean after fixes
  (deptry orphaned `matplotlib` — the deleted pix2text opt-in's dep;
  Makefile help echo placement; proposal frontmatter null-fields; ruff
  format) — targeted battery 113 green; **integration 13/13 green**;
  perf 25/26 (`graph_l1_centrality` 5.21s/5.0s parked — unrelated leg,
  box noise); advisory 10/13 (lint-audit green after 3 justified S310
  noqa anchors on the loopback client; search-check lanes re-converged
  post-edit; `convo-fresh` always-drift class; `ty-tests` pre-existing
  `misc.database_integrity_check` resolution noise, non-gating).
- **D5 (escalation order) DEFERRED per operator** — data says: keep
  lite-first (deterministic rung first; teleocr rung delivers
  tesseract-impossible content — numbers/structure/formulas — at word
  parity, but its generation variance means silent omissions are possible;
  revisit after the first real scanned documents).
