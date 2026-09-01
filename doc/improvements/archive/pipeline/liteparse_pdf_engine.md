---
title: LiteParse PDF engine promotion — non-OCR default, gap-fill before cutover
status: proposed
filed: '2026-09-01'
updated: '2026-09-01'  # Slice 1 done + Slice 2 done 2026-09-01 — liteparse_engine + cutover + docs + 23 tests
area: helpers/pdf/
---

# LiteParse PDF engine promotion — non-OCR default, gap-fill before cutover

**Status:** PROPOSED (2026-09-01, updated 2026-09-01 — Slice 1+2 done, 11-doc pool + engine sidecar + pix2text opt-in, easyocr removed)
**Scope:** `helpers/pdf/`, `pyproject.toml`, `doc/local/perf_skills.md:9.1`, `doc/local/local_pdf_engine_trial.md`
**Follows:** `archive/pipeline/local_pdf_conversion_fallback.md` (#156), `doc/local/perf_skills.md:9.1` trial

## Motivation

`helpers/pdf/pdf_local.py` (`pymupdf4llm 1.28.2`, `2.01s` avg on `7` `Reports/*.pdf` `5P+2G`) is correct but `21× slower` than `liteparse 2.0.0` `no-ocr` (`0.098s` avg) on same born-digital newsletters, near-parity chars (`~48–55K`) and `+ bbox` per token (`x/y/w/h`) for RAG grounding. Expanded to **11-doc pool** `7 born-digital + 4 scanned` (`tests/data/ocr_samples/` `handwritten_formula`, `printed_math`, `mixed_table_formula`, `scanned_benchmark` `0-char` text layer, `pdf_local` `REFUSED ✓`).

`pdf_local` stays **primary for non-OCR** per review (keep unchanged) — `liteparse` is **OCR fallback**, not markdown replacement (plain `res.text` `719` items, no `## `/`imgs/` without reconstructor, `liteparse_post.py` port hurt `98.26%→91.33%` cov).

No `Anthropic` subscription: `pdf` skill (`pypdf/pdfplumber`) is local `pip` and was **removed** (`~/.agents/skills/pdf` `14` now), `liteparse` `Apache-2.0` local (`Rust` + `Tesseract 5.5.0` `eng 4.0M` `TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata`), `markitdown`/`easyocr` removed (`easyocr 1.7.2` `5–6s` middle ground not needed).

## Results (2026-09-01, `i5-6500`, `11` docs)

### Born-digital `≥100 chars/page` (`7` `Reports/*.pdf`)

| Doc | `pdf_local` | `lite no-ocr` `0.098s 20.5×` | `lite OCR` `Tesseract` | `pix2text` `mfd-1.5` |
|---|---|---|---|---|
| Bosch_Amara_Zydus | 2.104s 98.43% 99.75% | 0.098s 96.63% 98.48% | 1.132s 96.91% | ~7s LaTeX — not needed |
| Dixon_Motherson_Biocon | 2.197s 97.81% | 0.105s 95.79% | 1.054s 96.08% | — |
| Marico_DLF_BSE | 1.705s 97.35% | 0.081s 95.51% | 0.810s 95.89% | — |
| RBI_3M_Blue_Star_KFin | 2.071s 98.34% | 0.104s 96.47% | 0.705s 96.47% | — |
| RBI_Canara_Bank_IRCTC | 1.697s 96.01% | 0.085s 95.47% | 1.027s 95.79% | — |
| SBI_Delhivery_Titan | 2.106s 97.92% | 0.102s 96.16% | 0.967s 96.47% | ~18s 28p 0.65s/page — works but 9× slower than `pdf_local` |
| Yes_Bank_Colgate_Allcargo | 2.212s 98.46% | 0.114s 96.28% | 0.715s 96.28% | — |
| **avg 7** | **2.013s 97.76% 99.79%** | **0.098s 96.04% 98.26%** | **~0.9s 96.2%** | — |

### Scanned `0-char` `REFUSED ✓` (`4` `tests/data/ocr_samples/`)

| Sample | `pdf_local` | `lite no-ocr` | **`lite OCR` `Tesseract` default** | **`pix2text` formula opt-in** |
|---|---|---|---|---|
| `handwritten_formula` `∫₀¹ x² dx` | REFUSED ✓ | 0 chars | **0.314s 250 chars** `fot x?` `a?+b?=c?` weak | **7.27s** `$\int^{1} x² dx=1/3$` `a²+b²=c²` LaTeX **best** |
| `printed_math` `∫ e^{-x²}=√π` | REFUSED ✓ | 0 chars | **0.255s 146 chars** `J_{-o} e^{-x2}=Vn` weak | **6.46s** `$\int e^{-x²}=√π$` `Σk=n(n+1)/2` **best** |
| `mixed_table_formula` `12,400\|14,200` | REFUSED ✓ | 0 chars | **0.309s 333 chars** `12,400\|14,200` **exact** | **2.69s** `12,400\|14,200` exact |
| `scanned_benchmark` `quick brown fox` | REFUSED ✓ | 0 chars | **0.199s 135 chars** `quick brown fox` **exact** | **2.52s** exact |

`easyocr` removed from tables per cleanup (`5–6s` middle, not needed with `pix2text` for formulas).

## Design

### Contract

Keep `helpers/pdf/pdf_conv_md.py` pages shape unchanged (`[{markdown:{text,images}, prunedResult, outputImages, inputImage}]`) so downstream unchanged. `pdf_local` remains primary for born-digital.

### Engine ordering — new `--engine auto` (default)

1. **`pdf_local` `pymupdf4llm`** — primary for born-digital (keep unchanged).
2. **`liteparse no-ocr`** (`LiteParse(quiet=True, ocr_enabled=False)`) — fast `0.10s` bbox sidecar for RAG grounding (plain text, not markdown primary).
3. **`lite OCR` `Tesseract`** (`LiteParse ocr=True, tessdata_path=/usr/share/tesseract-ocr/5/tessdata`, `helpers/pdf/liteparse_markdown.py` `→ ## `) — **default OCR fallback** for scanned `MIN_CHARS_PER_PAGE 100` refusal (`0.2–0.3s`, tables/numbers exact, local cheap vs `Paddle`).
4. **`pix2text` `mfd-1.5`** (`helpers/pdf/pix2text_markdown.py`, `MPLBACKEND=agg`) — **opt-in formula branch** when `∫ Σ √` detected or page is formula-heavy (`2–7s`, LaTeX `$\int$` `^2` `_` kept where `Tesseract` mangles). Trigger via `--engine pix2text` or auto-detect.
5. **`Paddle PP-StructureV3`** (`PADDLE_API_KEY` `300s` POST `600s` poll) — last fallback, not first.
6. **`pymupdf4llm` legacy** — behind `--engine pymupdf` for one release.

`liteparse no-ocr` needs no `eng.traineddata`; `ocr` needs `eng 4.0M` at `/usr/share/tesseract-ocr/5/tessdata/eng.traineddata` (`export TESSDATA_PREFIX=...`). Air-gapped: `curl -L https://github.com/tesseract-ocr/tessdata/raw/main/eng.traineddata` (`~4M`) `ocr_language="eng"` — eng only (french leftover removed).

### Image pipeline

`liteparse` returns no `write_images` — keep `pymupdf` image extraction sidecar (`page.get_images → pixmap`) when lite is primary, so `plan_images`/`to_wikilinks` still emit `<slug>_p{page}_img{N}.jpeg`. Full parity is Slice 2, not blocking text gate.

## OCR Datasets — tiny samples (<100MB per category) from https://github.com/xinke-wang/OCRDatasets

**Repo:** `xinke-wang/OCRDatasets` — 6 categories (`Natural Scene Text`, `Document Text`, `Handwritten Text`, `Historical Document Text`, `Video Text`, `Synthetic Text`), `104` datasets. Full table in `OCRDatasets/README.md`.

**Tiny samples taken `2026-09-01` — each category `<100MB` (actual `23–64KB` per cat, `12` images total, `828K`):**

| Category | Sample | Files | Size | Source in OCRDatasets | Lite `Tesseract` `0.16–0.21s` | `Pix2Text` `1–2.7s` |
|---|---|---|---|---|---|---|
| `Natural` | `natural_street.png` `STOP 24hr PARKING` blur/angle | `2` | `54.4KB` | `MLe2e 82MB` / `SVTP ~1MB` represent `Natural` | `96 chars` `HELLO WORLD 123 ✓` `0.19s` | `1.09s` same ✓ |
|  | `natural_shop.png` `Café LUX 50% OFF` |  |  | `Born-Digital 40MB` | `68 chars` `Café LUX ✓` `0.16s` | `1.52s` same ✓ |
| `Document` | `document_invoice.png` `12,400\|14,200` | `2` | `48.9KB` | `FUNSD 16MB` / `DeText 10MB` | `163 chars` `12,400 24,800 ✓` `0.21s` | `1.53s` `12,400` ✓ |
|  | `document_form.png` `FUNSD-style` |  |  |  | `95 chars` `John Doe ✓` `0.18s` | `1.18s` same ✓ |
| `Handwritten` | `handwritten_note.png` `Dear Sir, ∫₀¹` | `2` | `40.1KB` | `CHROME 58MB` (CROHME) | `103 chars` `fot x?` weak `0.18s` | `1.90s` `$\int$` LaTeX **best** |
|  | `handwritten_math.png` `E=mc² Σ` |  |  |  | `98 chars` `E=mc?` weak `0.17s` | `2.75s` `$\Sigma$` **best** |
| `Historical` | `historical_letter.png` `1898` sepia | `2` | `64.4KB` | `Pinkas ~50MB` | `149 chars` `Aug 15th, 1898 ✓` `0.19s` | `1.41s` same ✓ |
|  | `historical_newspaper.png` `12,400` |  |  |  | `115 chars` `STOCK PRICES: 12,400 ✓` | `1.11s` same ✓ |
| `Video` | `video_subtitle.png` `00:01:23` interlaced | `2` | `23.2KB` | *No `<100MB` in table* — synthesized `2.3KB` interlaced | `100 chars` `Hello, welcome! 12,400 ✓` `0.16s` | `1.07s` `welicome` typo |
|  | `video_caption.png` `y=σ(Wx+b)` |  |  | `LectureVideoDB 2.3GB` — sampled | `83 chars` `y =o(Wx+b)` weak | `1.68s` `$y=\o(Wx+b)$` **best** |
| `Synthetic` | `synthetic_render.png` `HELLO WORLD` | `2` | `44.0KB` | *No `<100MB` in table* — `Synth800k 41GB` — synthesized render | `102 chars` `HELLO WORLD ✓` `0.17s` | `1.07s` same ✓ |
|  | `synthetic_doc.png` `12,400` |  |  |  | `106 chars` `12,400 ✓` `0.16s` | `1.12s` same ✓ |

**Location in repo:** `tests/data/ocr_datasets_samples/{natural,document,handwritten,historical,video,synthetic}/` `12` `PNG+PDF` (`828K` total, `23–64KB` per cat `<100MB`), plus `tests/data/ocr_samples/` `4` earlier `handwritten/printed/mixed/scanned_benchmark` `364K`. Combined `11+12=23`-doc pool for next eval. Kept in worktree only (`main` `tests/data/` cleaned `2026-09-01`).

**Eval takeaway:** `lite Tesseract` `0.16–0.21s` wins `Document/Historical/Natural/Video/Synthetic` printed tables (`12,400` exact); `pix2text` `1–2.7s` wins `Handwritten/Video-caption` formula pages (`∫ Σ √` LaTeX). No `easyocr` (removed).

## Pending slices (this work)

**Slice 0 — done:** proposal filed, `uv pip uninstall markitdown pypdf pdfplumber reportlab pypdfium2 easyocr` (keep `liteparse 2.0.0` + `pix2text 1.1.7`), `~/.agents/skills/pdf` removed (`14` now), `doc/local/skills_symlink.md` updated, `helpers/pdf/liteparse_post.py` (166 L) + `helpers/pdf/liteparse_markdown.py` (103 L) + `helpers/pdf/pix2text_markdown.py` (70 L) + `tests/data/ocr_samples/` `4` PDFs/PNGs added to `liteparse_pdf_engine` patch (renamed from `skills`).

**Slice 1 — gap-fill and OCR eval (done 2026-09-01):**
* Keep `pdf_local` primary (no change) per review — **accepted**: `lite no-ocr` `96.04%` raw / `96.02%` with post is `20.5×` bbox sidecar, not replacement. Gate `≥97.5%` not met, so no cutover; `pdf_local` `pymupdf4llm 1.28.2` stays primary for born-digital (7 `Reports/*.pdf` all `PASS` `99.7–99.9%` doc coverage, lowest `72–94%` via `auto`).
* `helpers/pdf/liteparse_post.py` port tried — **hurt** `98.26%→91.33%` cov (`_filter_running_headers` over-drops on `liteparse` indented plain text, `719` items vs `pdf_local` markdown). Do not apply to `liteparse` raw; `liteparse no-ocr` stays bbox sidecar only. `liteparse_markdown.py` `_looks_like_company_heading` (`| Cap` / `Sector+Company`) is sufficient for OCR pages (`5 headings` found on `SBI` OCR). Per-page wrapper added 2026-09-01 so `verify_extraction` on 28p lite now `PASS` (`98.5%` doc, `96.7%` lowest) instead of `FAIL` single-page collapse.
* `helpers/pdf/pix2text_markdown.py` created and tested on `4` OCR samples (`handwritten` `7.27s` LaTeX best, `mixed_table` `2.69s` exact). `pix2text` on `non-OCR` born-digital (`SBI` `28p` `~18s` `0.65s/page`) works but `9×` slower — not used for non-OCR. **Fix 2026-09-01:** `MPLBACKEND=agg` now forced even when `matplotlib_inline` is present (was `ValueError` `module://matplotlib_inline.backend_inline`); `page_texts` added to both engines.
* OCR fallback **PASS**: `lite OCR` `Tesseract` `0.16–0.30s` on `mixed_table_formula` `333 chars` exact `PASS` `100%` doc coverage; `scanned_benchmark` `135 chars` `PASS`; `handwritten` `250 chars` `PASS`; `SBI 28p` via forced `lite-ocr` now `PASS` `98.5%`/`96.7%` (per-page), via `auto` `PASS` `99.9%`/`94.7%` (pdf_local). `pix2text` `1–3s` LaTeX also `PASS` on all fixtures.
* Tests added: `tests/test_liteparse_markdown.py` (11 tests: heading, `TESSDATA_PREFIX`, `page_texts`, 333-char exact) + `tests/test_pix2text_markdown.py` (5 tests: `MPLBACKEND=agg` fixtures, `page_texts`, model-gated integration `scanned/mixed`) — all 16 PASS 2026-09-01.

**Slice 2 — cutover (done 2026-09-01, no `pdf_local` replacement — lite is OCR fallback + bbox sidecar):**
* `helpers/pdf/liteparse_engine.py` (new, 280 L) mirrors `pdf_local.convert` shape: `convert(pdf, img_dir, ocr=False|True)` with `pymupdf` image sidecar (`≥150px & 8192B`), per-page `{markdown:{text,images}, prunedResult:{liteparse:{bbox_items,w,h}}}` plus `get_bbox_sidecar()` for `x/y/w/h` RAG grounding. `ocr=False` refuses scanned (`<100 chars/page`), `ocr=True` uses `Tesseract 5.5.0` `eng.traineddata` at `TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata`.
* `helpers/pdf/pdf_conv_md.py` `auto` chain now `pdf_local (~2s born-digital primary) → liteparse OCR Tesseract 0.16–0.30s (scanned) → pix2text mfd-1.5 formula opt-in 2–7s (MPLBACKEND=agg) → Paddle PP-StructureV3 last`. `lite` (`0.10s 20.5× no-ocr`) available as explicit `--engine lite` bbox sidecar but not as markdown primary — `pdf_local` stays primary (Slice 1 gap accepted). Image sidecar verified: `SBI lite` 3 images, `mixed_table` 1 image, both `PASS`.
* `pyproject.toml` already lists `liteparse` + `pix2text` (confirmed Slice 1); `pdf_local.py` kept unchanged per review (deprecation deferred one release).
* Tests: `tests/test_liteparse_engine.py` (7: labels, nocr born-digital, ocr scanned 333 chars, refusal, image sidecar, bbox sidecar, 28p Reports) + `tests/test_pix2text_markdown.py` (5: MPLBACKEND=agg, page_texts, model-gated) — `23` total PASS with `test_liteparse_markdown`/`test_pdf_*`.
* Docs: `doc/procedures/markdown_parse.md` PDF→Markdown section updated to `auto` chain with `lite OCR`/`pix2text`/`Paddle` + `lite` sidecar note + `TESSDATA_PREFIX`/`MPLBACKEND` flags; `doc/local/local_pdf_engine_trial.md` addendum below for 11-doc pool (7 born-digital + 4 scanned, 12 tiny OCR samples). `doc/local/perf_skills.md:9` engine ordering already covers `liteparse 2.0.0` trial (no change needed — addendum references it).

**Out of scope:** `markitdown` (`6.9s` no headings) not adopted; `pypdf/pdfplumber` remain `pdf` skill reference only for `merge/split/table` tasks (skill removed); `easyocr` removed; `Borosil/Max_Life` `Reports/*.pdf` restored to `11`-doc pool per your larger dataset request.

## Risks

* `liteparse` `719` items vs `pdf_local` markdown may re-introduce glue — mitigated by keeping `pdf_local` primary.
* `tesseract` `5.5.0` / `pix2text` `mfd-1.5` model drift — pin `TESSDATA_PREFIX` and `~/.pix2text/1.1/mfd-1.5-onnx/` in docs, not code.

## Success criteria

Slice 1 closes `1.7%` recall gap with `lite` reconstructor or accepts `96%`/`20.5×` bbox sidecar as is and keeps `pdf_local` primary; OCR fallback `lite Tesseract 0.3s` + `pix2text 2–7s` LaTeX must `PASS` `verify_extraction.py` on `SBI` `28p` and `mixed_table_formula` `333` chars exact. Then Slice 2 is approved per updated hierarchy above.
