# Local PDF-to-markdown conversion fallback

**Status:** EXECUTED (2026-08-26; completed.md #156)
**Scope:** `helpers/pdf/`, `pyproject.toml`, `doc/procedures/markdown_parse.md`, tests

## Motivation

The Paddle AI Studio job endpoint (the only PDF→markdown path,
`helpers/pdf/pdf_conv_md.py`) is unreliable from this network: repeated
client-side write timeouts on the multipart upload (observed 2026-08-25;
the 300s per-socket-op timeout in `submit_job` was already a mitigation).
When it fails there is no fallback — newsletter ingestion blocks.

All 7 in-tree `Reports/*.pdf` are **born-digital** (real text layers:
36K–54K extractable chars each; `pdftotext` confirms). OCR is not needed
for structure — only a layout-aware structure→markdown converter.

## Evaluation of candidates (spike, 2026-08-25)

| Tool | Verdict |
|---|---|
| **pymupdf4llm** (PyMuPDF, MIT) | **Chosen.** 7.9s for the 22-page Marico PDF; per-page chunks; images extracted to disk (`write_images=True`); works with Tesseract absent (it OCRs only embedded raster fragments — our path does not rely on it); pure-Python install, no service. |
| pdfplumber / pdfminer | text+tables primitives only; we would own heading logic and image extraction. More work, no better fit. |
| docling / marker | heavyweight model dependencies; unnecessary for born-digital PDFs. |
| pdfmux 1.8.7 | **Rejected** (eval 2026-08-26): builds on pymupdf4llm but pins it <1.0 (downgrade conflict), extracts NO images (figure pipeline needs `write_images`), loose PASS verification; content identical to direct pymupdf4llm on the 7-PDF corpus. Trial: `doc/local/local_pdf_engine_trial.md` §pdfmux. |

Parity check that matters: Paddle's PP-StructureV3 produced **0 pipe
tables** for all 7 PDFs (tables are rendered as images in these
newsletters — see the existing findata notes, which embed them as JPEGs).
pymupdf4llm also extracts them as images. No table regression.

## Design

### Contract reuse — produce the same `pages` shape

`pdf_conv_md.py` is provider-agnostic downstream of `download_jsonl()`:
`parse_pages()` normalizes to
`[{markdown: {text, images: {rel: url}}, prunedResult, outputImages,
inputImage}]`, and `write_outputs()` + `build_okf_frontmatter()` +
`plan_images()` + `to_wikilinks()` consume only that shape.

The local engine therefore **produces the same page dicts** and reuses
everything after the API boundary unchanged:

- `helpers/pdf/pdf_conv_md.py` gains `--engine {paddle,local}` plus
  `auto` (default): try Paddle; on any submit/poll failure print the
  error and run the local engine in the same invocation (one command,
  no operator re-entry — "fallback", not a second tool to remember).
- New module `helpers/pdf/pdf_local.py` exposing
  `convert(pdf_path, *, model="pymupdf4llm") -> list[dict]` mirroring
  the `parse_pages()` output shape.

### Local engine steps (`pdf_local.convert`)

1. `pymupdf4llm.to_markdown(pdf, page_chunks=True, write_images=True,
   image_path=<tmpdir>, image_format=".jpeg")`.
2. **Heading normalization** — `parse_newsletter.py:84` keys company
   sections on `## Foo | Large Cap | Sector`:
   - `### **<u>Name | Cap | Sector</u>**` → `## Name | Cap | Sector`
   - `# **Sector**` → `## Sector`
   - strip `**`/`<u>` wrappers; drop `<!-- picture text -->` blocks
     (decoration/badge OCR noise, e.g. "Y ZERODHAAUG 05, 2026").
3. **Image pipeline** — pymupdf4llm writes `![](<tmpdir>/<name>.jpeg)`
   refs. Post-pass: read each image's pixel size, **filter
   decorations** (min-dimension and byte thresholds, tuned on the eval
   below — pymupdf4llm finds 8 images for Marico where Paddle kept 5;
   the extras are logos/badges), number survivors into the existing
   document-wide counter, and emit `images: {rel: local_path}` in the
   page dict so `write_outputs()` **copies** instead of
   `requests.get()` (one branch: copy when the "url" is an existing
   local path). Filenames keep the
   `<slug>_p{page}_img{N}.jpeg` convention verbatim — downstream figure
   embedding (Stage 4) cannot tell the engines apart.
4. Frontmatter: `build_okf_frontmatter` unchanged;
   `generated.by: pdf_conv_md.py/<engine>` records the actual engine
   (OKF provenance honesty: `PP-StructureV3` vs `pymupdf4llm-local`).

### What explicitly does NOT change

- `parse_newsletter.py` and every downstream stage (entities, sync,
  embeddings, figure embedding) — the pages/markdown contract is
  identical.
- The OKF frontmatter schema, tag vocabulary, image filename
  convention, `<slug>.json` debug output shape.
- Image Capture procedure (still skipped for PDF inputs).

## Slices

1. `pdf_local.py` pure transforms + unit tests (no network, mirrors
   `tests/test_pdf_conv_md.py` discipline): heading normalize, picture
   -text strip, image-ref rewrite + size filter, pages-shape assembly.
2. `pdf_conv_md.py --engine auto|paddle|local` wiring + local-copy
   branch in `write_outputs`; tests with a stubbed engine.
3. Eval across all 7 `Reports/*.pdf` vs the existing Paddle-derived
   notes: heading counts, kept-image counts, and a
   `parse_newsletter --dry-run` parity diff (entities/sectors). Tune
   the decoration threshold here.
4. Docs (`markdown_parse.md` §PDF → Markdown), pyproject dependency
   (`pymupdf4llm` next to `requests` under the pdf-pipeline comment),
   completed.md entry; archive this proposal.

## Decisions

- **Q1 default (operator):** decided after the trial — see below.
- **Q2 picture-text comments:** **DROP** (confirmed 2026-08-25). Trial:
  picture-text blocks hold only ad/footer/badge text across all 7 PDFs.
- **Q3 decoration filter:** **ACCEPTED** — min dimension ≥ 150px AND
  ≥ 8KB; exact image-set tuning in slice 3.

## Trial results (2026-08-25)

Full 7-PDF content-parity eval vs the in-tree notes (6 Paddle +
1 GLM: `RBI_3M_Blue_Star_KFin.md`): word recall 96.0–98.5%, sentence
coverage 100% on 2 of 7 PDFs (incl. the GLM-ref one). No document
content lost: residual diffs are footer ads (dropped per Q2), Paddle
OCR errors where local is more accurate (pixel-verified: `effi ciency`
splits ×~40, phantom digit `70` vs the PDF's `7`), and two minor local
artifacts (`seri`, one lost dash). Details:
`doc/local/local_pdf_engine_trial.md` (private).

## Risks

- Heading heuristics miss an unseen layout → eval slice catches it on
  the 7-PDF corpus; fallback prints which headings it rewrote.
- pymupdf4llm API drift (fast-moving project) → pin nothing (house
  style, cf. duckdb), but the module isolates all its calls behind
  `pdf_local.convert`.
- Scanned (no-text-layer) PDF would silently produce garbage → local
  engine asserts a minimum extracted-text volume per page and refuses
  (points back to Paddle, which OCRs).
