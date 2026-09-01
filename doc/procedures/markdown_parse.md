# Markdown Parse Procedure

Parse documents to extract entities (companies, sectors), create synchronized SQLite records + markdown files with enhanced tags, then validate.

**Inputs are either (a) an existing markdown newsletter or (b) a source PDF.** For a PDF, first convert it to markdown with `helpers/pdf/pdf_conv_md.py` (see [PDF → Markdown](#pdf--markdown)) — that step also downloads + embeds the figures, so no separate image capture is needed. For an existing markdown, **capture its remote OCR-crop images FIRST (see [Image Capture](#image-capture)), before any parsing** — the signed URLs expire and the inline embeds are later used to attach figures to company notes.

> **Output destination — ask the user; never assume.** There is no safe way to infer the destination from the input. For a **PDF**, ask the user which directory to store the converted `.md` in before running `pdf_conv_md.py`. For an **existing markdown**, capture is in-place (the source `.md` is rewritten and figures land in its own `images/` dir), so confirm the markdown's location with the user rather than relocating it.

## Workflow

> **One-command entry point.** Stages 1–4 + 6–7 are automated by
> `helpers/core/parse_newsletter.py`. Run it first; it emits a
> `<slug>_enhancement_worklist.json` for the only manual step (Stage 5).
>
> ```bash
> python3 helpers/core/parse_newsletter.py findata/The_Chatter/Foo.md                          # plan (dry-run)
> python3 helpers/core/parse_newsletter.py findata/The_Chatter/Foo.md --apply                    # execute
> python3 helpers/core/parse_newsletter.py findata/The_Chatter/Foo.md --apply --with-analytics   # also refresh graph_analytics
> ```

0. **Convert PDF → markdown** *(PDF inputs only)* — **ask the user for the destination directory first** (there is no safe way to infer it). Then convert the source PDF with `helpers/pdf/pdf_conv_md.py` into a The_Chatter-style `.md` in that directory. The script also emits the note's frontmatter (OKF provenance + namespaced `series/`/`publisher/` tags derived from the destination directory — see [Tags](#tags)) and downloads + embeds the figures as `![[images/<slug>_p{p}_img{N}.jpeg]]` into a sibling `images/` dir, so **no separate image capture is needed for PDF inputs** (see [PDF → Markdown](#pdf--markdown)). Then proceed to Stage 1.
1. **Capture images** *(existing-markdown inputs only)* — **confirm the markdown's location with the user first** (capture is in-place: it rewrites the source `.md` and drops figures into its own `images/` dir). Then download all remote `<img>` crops and rewrite the source `.md` so figures embed inline next to their content (see [Image Capture](#image-capture)). Do this **before** parsing, since the signed URLs expire and the inline embeds are later used to attach figures to company notes. *(Skipped for PDF inputs — already done by Stage 0.)*
2. **Extract entities** — companies/sectors from document content.
3. **Get tickers** — the orchestrator uses `get_tickers.search_ticker()` (which delegates to `fuzzy_match.word_overlap_match()` for name matching). **Prefer NSE (`.NS`) over BSE (`.BO`)** — NSE is the canonical Indian listing in this KB; use `.BO` only when a name is BSE-only (e.g. SME-only listings).
4. **Add each NEW entity** — create SQLite record + markdown file + tags only for companies not already in the DB (see [Adding an Entity](#adding-an-entity)). When lifting insights from a newsletter, also embed the figure(s) that sit under that company's section as `![[images/<slug>_p{p}_img{N}.jpeg]]` so the chart travels with the insight.
5. **Enhance existing entities** — for every company already in the DB that has a concall/management section in this newsletter, append a per-edition newsletter block to its note (see [Enhancing Existing Entities](#enhancing-existing-entities)). This is the default action when the entity already exists — do not skip it. *Not automated — the orchestrator emits the worklist; an agent lifts the insights.*
6. **Create relationships** — bidirectional `part_of` / `has_company` between company and sector.
7. **Validate** — run the two post-processing scripts (see [Validation](#validation)).
8. **Refresh graph analytics** *(opt-in, `--with-analytics`)* — recompute PageRank / clustering / community detection across the updated graph and persist to `graph_analytics`. Use when you intend to consume graph metrics next; adds ~2–5s on the current ~1,500-entity graph.
9. **Re-derive structured relations** *(opt-in, separate command)* — re-scan newsletter prose AND synced company notes for `jv_with` / `acquired` / `subsidiary_of` / `same_group` / `supplier_to` / `customer_of` edges and write verified matches to `graph_edges`. Anything that names an unknown entity goes to `findata/_pending_relations.txt` for human triage.

   ```bash
   # Dry-run summary across all sources (recursive directory scan):
   python3 helpers/graph/extract_relations.py findata

   # Apply:
   python3 helpers/graph/extract_relations.py findata --apply

   # Or via make (canonical newsletters only):
   make derive-relations
   ```

   Accepts files, directories (scanned recursively), or shell-expanded globs.
   `image_map.md` and files under `images/` are skipped automatically.
   Document type is auto-detected from YAML front matter:
     - `type: company` notes (under `findata/Companies/`) are scanned as
       single-section documents anchored to the note's `normalized_name`.
       Edges carry `properties.doc_type='company'` and `properties.note` for
       a distinct audit trail.
     - `type: sector` notes (under `findata/Sectors/`) are skipped silently
       (sectors don't anchor company relations).
     - Files with no YAML are treated as newsletters (multi-section, pipe-
       separated company headings).
   Double-counting is avoided via the `UNIQUE(source, target, edge_type)`
   constraint: an edge discovered in both a newsletter and a company note
   is persisted once; whichever runs first wins the `source_ref`.

   **Temporal extraction** for `acquired` edges: the helper also extracts
   a year/month/FY-quarter from the surrounding prose (e.g. "in 2024",
   "Dec 2025", "Q4 FY26") and populates the `valid_from` column (DB DATE)
   plus `properties.year` (machine-friendly filter). Yahoo Finance
   attribution lines are stripped as noise; future years filtered
   (acquisitions are past-tense). When only the year is known,
   `valid_from = YYYY-01-01` (sortable but loses month precision);
   `properties.year` preserves the actual integer.

   Idempotent via the `UNIQUE(source, target, edge_type)` constraint; safe to re-run after every newsletter batch. Re-run **after** the human reviewer has triaged `_pending_relations.txt` and added any new stub entities.

   **Triage loop (`make triage-relations`, proposal `pending_relations_triage`):** the queue is handled mechanically now —
   1. `make triage-relations` (or `python3 helpers/graph/triage_pending_relations.py`) — dedupes, splits `suggested` rows (link-prediction candidates, `_pending_suggestions.txt`) from true extraction misses, buckets prose rows (`discard` noise / `alias_candidate` / `stub_candidate` / `manual`), and writes the eyeball report + an annotated-ready `findata/_pending_triage_decisions.jsonl`.
   2. Annotate `decision` per row in the decisions file: `discard` | `alias:<Existing Entity Name>` | `stub` | `skip` (foreign/out-of-corpus parents).
   3. `python3 helpers/graph/triage_pending_relations.py --apply-decisions --write` — persists alias entries to git-tracked `findata/relation_aliases.json` (loaded by the extractor at run time — triage cycles need no code edits), drops applied rows, moves suggestions out, keeps unresolved rows deduped, and prints the follow-up chain (re-run extract → roster sync if stubs → `make graph-rebuild` → snapshot). Stub creation itself stays explicit (collision-check discipline); the script prints a stub plan.
   4. `--clear` truncates the queue once everything is resolved.
   Countries/generic-phrase/mangled-fragment targets never enter the queue anymore (write-time noise gate in the extractor).

10. **Refresh the events timeline** *(automatic with `make maint-full`, or manual)* — D7. The `events` table (acquisition / jv / guidance / management_change) is reconciled against the full corpus by `derive_events.py`, which (a) promotes `acquired`/`jv_with` edges into event rows and (b) extracts new guidance + management-change events from the `## The Chatter` blocks just enhanced in Stage 5. This runs automatically as the 6th step of `make maint-full` (post-ingest cleanup), so a normal ingest → enhance → `maint-full` cycle refreshes the timeline with no extra command. To run it standalone:

   ```bash
   make derive-events          # apply
   python3 helpers/graph/derive_events.py        # dry-run summary
   python3 helpers/graph/derive_events.py -v     # list every derived event
   ```

   Idempotent via DELETE-then-INSERT of derived rows (`source_ref LIKE 'derive:events:%'`); hand-seeded `manual:`/`migration:` rows are preserved. Query the timeline via `GET /api/events/<company>`.

11. **Auto-extract concall quotes + magnitudes** *(standalone command)* — `derive_insights.py` reads each company's `## [Concall]` body and captures every verbatim quote + speaker attribution + paraphrase into the `quotes` table, plus financial magnitudes (₹/%/bps/$bn) into `company_metrics`. It renders the quotes into a sentinel-wrapped `## The Chatter — <edition>` block in each company note (the deterministic first pass of Stage 5; hand-written blocks are never clobbered). Run after the newsletter is parsed and entities exist:

    ```bash
    make derive-insights                                        # DRY-RUN preview (writes nothing)
    python3 helpers/graph/derive_insights.py findata --apply     # the actual write (bumps OKF generated/stale_after on notes whose blocks changed; maint-full runs --no-notes and never mutates notes)
    python3 helpers/graph/derive_insights.py findata --verbose  # list every quote + metric
    ```

    Idempotent via DELETE-then-INSERT (`source_ref LIKE 'derive:quotes:%'` / `'derive:metrics:%'`). See [Auto-generated chatter blocks](#auto-generated-chatter-blocks-deterministic-first-pass) for the curation-safety rule and how to replace an auto block with a curated one.

```python
# Extraction patterns
companies = re.findall(r'#[A-Z][a-zA-Z\s]+(?:Limited|Ltd|Private)', content)
sectors   = re.findall(r'#(?:Banking|Healthcare|Technology)', content)
```

## PDF → Markdown

When the input is a source PDF (rather than an existing newsletter markdown), convert it first. `helpers/pdf/pdf_conv_md.py` is **local-first** (2026-08-26, lite-OCR fallback 2026-09-01): by default (`--engine auto`) it parses the PDF locally with `pymupdf4llm` (~2s, born-digital), and falls back to `liteparse` OCR (`Tesseract 5.5.0` `eng 4.0M`, `0.16–0.30s`, `TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata`) for scanned PDFs (`MIN_CHARS_PER_PAGE 100` refusal), then `pix2text` `mfd-1.5` formula opt-in (`2–7s` LaTeX `MPLBACKEND=agg`) when the OCR is sparse or formula-heavy, and finally the Paddle AI Studio `PP-StructureV3` job API (`PADDLE_API_KEY`) only when all local engines refuse. The fast `liteparse no-ocr` (`0.10s`, `20.5×`, `bbox` per token) is available as `helpers/pdf/liteparse_engine.py` sidecar for RAG grounding (`--engine lite`) but **not** used for markdown primary — `pdf_local` stays primary for born-digital (Slice 1 gap `96.04%` accepted). Any engine writes a The_Chatter-style `.md` **plus** the figures already downloaded/copied and embedded — so a PDF input skips the [Image Capture](#image-capture) step entirely. The note frontmatter records which engine ran (`generated.by: pdf_conv_md.py/pymupdf4llm-X.Y.Z` vs `.../liteparse-2.0.0-ocr-eng` vs `.../pix2text-mfd-1.5` vs `.../PP-StructureV3`).

**Destination directory — always ask the user.** The `<output_dir>` argument is explicit and required; there is no safe way to infer it. Ask the user which directory to write into (a newsletter dir like `findata/The_Chatter/`, or any other path they choose) before running the converter, and use exactly that.

```bash
# Convert a PDF into <output_dir> (local engine by default; no API key needed).
python3 helpers/pdf/pdf_conv_md.py <source.pdf> <output_dir>

# Force an engine: local (born-digital only) / lite (fast no-OCR sidecar 0.10s) / lite-ocr (Tesseract scanned) / pix2text (formula LaTeX) / paddle (PP-StructureV3, needs PADDLE_API_KEY).
python3 helpers/pdf/pdf_conv_md.py <source.pdf> <output_dir> --engine local
python3 helpers/pdf/pdf_conv_md.py <source.pdf> <output_dir> --engine lite
python3 helpers/pdf/pdf_conv_md.py <source.pdf> <output_dir> --engine lite-ocr
python3 helpers/pdf/pdf_conv_md.py <source.pdf> <output_dir> --engine pix2text
python3 helpers/pdf/pdf_conv_md.py <source.pdf> <output_dir> --engine paddle

# Engine flags: --model <name> --token <key> --timeout <sec> (Paddle only); --no-images works for all.
# Lite OCR needs TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata (eng.traineddata 4.0M); pix2text needs MPLBACKEND=agg (auto-forced).
```

After every conversion the script **self-verifies** (skip with `--no-verify`): per-page coverage of the `.json` vs the PDF text layer, document coverage of the `.md`, md↔json page consistency, a ≥3-digit number audit, and wikilink integrity. The verdict prints with the summary (WARN passes — it flags e.g. a lost number-range dash; FAIL exits 1) and the full manifest lands beside the note as `<stem>.verify.json` (sha256 of source + md, engine, per-page metrics).

Local-engine fidelity (7-PDF trial, `doc/local/local_pdf_engine_trial.md`): word recall 96–98.6% vs the reference notes; residual diffs are dropped footer ads, reference OCR artifacts, and two minor glyph quirks (`seri`, `Ufex`). Known-good heading contract: company sections come out as `## Name | Cap | Sector` (wrapper-stripped, sector-glue split, bold-body headings rescued) — `parse_newsletter.py` sees the same sections as for Paddle-derived notes.

Outputs (written under the user-chosen `<output_dir>`, e.g. `findata/The_Chatter/` for a Chatter edition):

- `<slug>.md` — combined markdown in the newsletter style, with images embedded inline as Obsidian wikilinks `![[images/<slug>_p{page}_img{N}.jpeg]]`. Filename is just the PDF stem (company names, spaces → underscores), no model suffix — e.g. `RBI_Canara_Bank_IRCTC.pdf` → `RBI_Canara_Bank_IRCTC.md`.
- `<slug>.json` — raw per-page structured result (the shape used by the Reports/ eval JSONs), for audit/debugging.
- `images/<slug>_p{page}_img{N}.jpeg` — downloaded figures, one file per figure, matching the [convention](#convention) below (`slug` = PDF stem with spaces→underscores; `page` = 1-based physical PDF page; `N` = global 1-based counter in document order).

The script names images exactly like [Image Capture](#convention) does (`.jpeg`, `<slug>_p{page}_img{N}`), so downstream stages (embedding figures into company notes in Stage 4) work identically whether the newsletter came from a PDF or was captured later. `--no-images` skips the download and leaves absolute `<img src=...>` URLs in the markdown.

> **The Chatter / Points & Figures / Plotlines PDFs are born-digital** (real text layers — verified on all in-tree `Reports/*.pdf`, 2026-08-25), so the local engine handles them; the Paddle OCR path exists for true scans. If you have the PDF, use this converter and skip image capture. Only fall back to [Image Capture](#image-capture) for an existing markdown whose figures are still remote URLs.

## Image Capture

Existing newsletter markdown inputs whose figures are served as **signed, expiring** remote URLs (`maas-watermark-prod-new.cn-wlcb.ufileos.com/...?Expires=<ts>&Signature=...`). Left as-is they (a) 404 after `Expires`, and (b) bloat the source as giant `<div><img ...></div>` blocks that aren't embedded in any note. Capture them **before** parsing. *(For PDF inputs this is already handled by the converter above — skip this section.)*

### Convention
Prior newsletters already follow this pattern (e.g. `findata/Points_And_Figures/images/` holds ~960 files). Mirror it exactly:

- **Location:** a sibling `images/` dir under the newsletter type: `findata/Points_And_Figures/images/`, `findata/The_Chatter/images/`, `findata/The_PlotLines/images/`.
- **Filename:** `{slug}_p{page}_img{N}.jpeg` where
  - `slug` = newsletter filename stem, spaces → underscores (e.g. `No_shortcuts_here.md` → `No_shortcuts_here`).
  - `N`   = global 1-based image counter in document order — **the canonical link key**.
  - `page` = 1-based OCR crop-group: a new page begins whenever the crop counter in the URL (`crop_<n>_<ts>`) returns to 1 (contiguous `1..K`). These are *OCR crop-group* pages, which may differ from physical PDF page numbers; `N` is what matters for linking.
- **Source rewrite:** replace each remote block in the newsletter `.md` IN PLACE with an Obsidian embed at the same position, leaving **0** remote URLs:
  ```
  ![[images/{slug}_p{page}_img{N}.jpeg]]
  ```
- **Company notes:** when adding insights from a newsletter, embed the figure(s) sitting under that company's section using the same `![[images/...]]` syntax (e.g. `JSW_Steel.md` embeds `![[Context_beyond_the_charts_p32_img28.jpeg]]`). The rewritten source is the image→section map.

### How to capture

The helper writes in-place: figures go into the markdown's own `images/` dir and the source `.md` is rewritten where it already lives. Confirm that location with the user before running — do not move or copy the markdown to relocate the output.

```bash
# Download + verify + rewrite the source .md in place. Idempotent & resumable
# (skips already-valid files, retries failures). Works for any newsletter path.
python3 helpers/pdf/capture_newsletter_images.py <newsletter.md> --rewrite
```

The helper:
1. Parses every `<div ...><img src='URL'></div>` in document order.
2. Derives `{slug, page, imgN}` per the rules above.
3. Downloads concurrently into `<newsletter_dir>/images/` with retries; verifies each file is a non-empty JPEG/PNG; re-fetches failures.
4. Writes an audit manifest `<slug>_image_manifest.json` (`{imgN, page, line, file, url, bytes, ok}` per image).
5. With `--rewrite`: replaces each remote block with `![[images/<file>]]`; idempotent (no re-download).

### Detecting which notes still need capture
```bash
# Count remaining remote URLs per newsletter (0 = already converted)
for f in findata/Points_And_Figures/*.md findata/The_Chatter/*.md findata/The_PlotLines/*.md; do
  echo "$(grep -c ufileos "$f")  $(basename "$f")"
done | sort -rn
```

### Image-capture checklist
- [ ] All remote `<img>` URLs downloaded into `<newsletter_dir>/images/`
- [ ] 0 download failures (or any failures re-fetched / flagged)
- [ ] Every file is a non-empty JPEG/PNG (magic bytes)
- [ ] Source `.md` rewritten: 0 `ufileos` URLs, 0 raw `<img>` tags, all `![[images/<slug>_p{p}_img{N}.jpeg]]`
- [ ] Manifest written and line positions preserved

> **Expiry is time-critical.** Run capture as soon as a newsletter is identified, before the signed URLs lapse. Always capture+rewrite **before** the extract/ticker/entity stages.

## Naming & Sync Rules (MANDATORY)

These rules apply to every file under `findata/Companies/` and `findata/Sectors/`.

### Filename format (`normalized_name`)
- **PascalCase** with **single underscores** only.
- **No** special chars: `&`, spaces, `(`, `)`, `-`, consecutive `__`, or trailing `_`.
- **≤ 100 characters.**
- **Drop redundant suffixes** — omit `Company`, `Ltd`, `Limited` to save tokens.
- Use consistent abbreviations (e.g., `Tech` not `Technology`).

| ✅ Correct | ❌ Incorrect | Reason |
|---|---|---|
| `Asian_Paints.md` | `Asian_Paints_Company_Analysis.md` | extra words |
| `Mahindra_Mahindra.md` | `Mahindra_&_Mahindra_Ltd.md` | `&`, `Ltd` |
| `Jupiter_Alloys_Steel_India.md` | `Jupiter_Alloys___Steel_India_.md` | `__`, trailing `_` |

### Synchronization (character-for-character)
1. `normalized_name` **MUST** equal the filename (minus `.md`) exactly.
2. `file_path` **MUST** point to an existing file.
3. Paths use `findata/Companies/{Sector}/{Company}.md` or `findata/Sectors/{Sector}.md`.
4. No duplicate `normalized_name`; no orphaned files.

> Known legacy violations to avoid reintroducing: `Mahindra_&_Mahindra_Ltd.md`, `FSN_E-Commerce_Ventures_(Nykaa).md`, `5paisa_Capital.md` (→ `Five_Paisa_Capital.md`), and ad-hoc sector dirs vs the 42 canonical sectors in `findata/Sectors/` (see [Canonical sector list](#canonical-sector-list-42)).

## Tags

The categories below apply to the DERIVED notes (Companies/Sectors/Super_Sectors) and are mirrored into the `entity_tags` table by `sync_tags.py`. The SOURCE newsletter notes (The_Chatter/The_PlotLines/Points_And_Figures) use their own namespaced vocabulary (same `^[a-z0-9_]+/[a-z0-9_]+$` grammar, validated by `doc/schema/frontmatter.newsletter.v1.json` and mirrored into the `note_tags` table):

```
series/          the_chatter | points_and_figures | the_plotlines (from the note's tree)
publisher/       zerodha (per-series map; omitted when unknown)
company/         <entity-slug> coverage (deferred slice, S5)
```

Source-note tags are fully machine-written: `pdf_conv_md.py` emits them at conversion, and `helpers/misc/backfill_okf_provenance.py --sources --apply` backfills/migrates them on pre-existing notes. See `doc/improvements/archive/okf/newsletter_notes_adoption.md`.

### Edition identity & provenance activation (okf_activation)

The **note STEM is the canonical edition key** everywhere (`sources[].id`,
wikilinks, `entities.name` for edition nodes). `quotes.as_of_edition` is
free text that matches note titles only 28/71 — never use it as a join
key; resolve free-text edition references with
`helpers/core/edition_index.py` (`resolve_edition_string`), which
reports misses instead of guessing. The activation targets built on this:

- `make derive-cited-in-rebuild` — edition entities + `cited_in` edges
  projected from OKF `sources[]` (idempotent; pair with the DuckDB
  rebuild, which the target runs).
- `make analytics REPORT=coverage` — series × sector coverage matrix from
  clean entity/note_tags/cited_in joins (no fuzzy bridge).
- `python3 helpers/graph/derive_insights.py findata --apply --stale-only`
  — render only notes whose evidence moved since their last render
  (a `sources[].last_modified` newer than `generated.at`, OR a scanned
  edition whose stem is missing from `sources[]`); notes without
  `sources[]` always render. Rendered notes also get newly referenced
  editions **spliced into `sources[]`** (okf_sources_maintenance §3.2 —
  entry builders live in `edition_index.py`), so the evidence list and
  the gate read the same world; `stale_after` re-bases from the spliced
  sources. Opt-in; the first run after an OKF backfill re-renders every
  sourced note (backfill stamps are not render stamps).
- Post-render chain: `derive_insights --apply [--stale-only]` →
  `make derive-cited-in-rebuild` (new citations become edges) →
  `make maint-full` (snapshot captures everything).

Categories for derived notes (apply relevant ones; abbreviate to save tokens):

```
entity_type/     company | sector
sector/          one of the 42 canonical sectors (see below)
market_cap/      large_cap | mid_cap | small_cap
geography/       india | global
business_model/  b2b | b2c
risk_investment/ dividend | high_growth | medium_risk
```

### Canonical sector list (42)
Always classify into one of these. **Never create ad-hoc sector dirs** — if a company doesn't fit, use `Diversified` or propose a new sector explicitly. Carve-outs (in **bold**) must be checked before their parent catch-all during classification.

```
Agriculture · Automotive · Aviation · Banking · Building_Materials ·
Capital_Markets · Chemicals · Consumer · Defense · **Diagnostics** ·
Diversified · **EMS_Manufacturing** · Education_Training · Electronics ·
Energy · Engineering_Capital_Goods · FMCG · Fertilizer · Financial_Services ·
**Fintech_Payments** · Healthcare · **Hospitals** · **Housing_Finance** ·
Infrastructure · Insurance · International · Logistics · Media_Entertainment ·
Metals · Mining · **NBFC** · Packaging · **Pharma** · **Railways** ·
Real_Estate · **Renewables** · Retail · **Semiconductors** · Technology ·
Telecommunications · Textiles · Travel
```

**Classification precedence** (check carve-out before parent):
- `Renewables` (solar/wind/biofuel) before `Energy`
- `Pharma` / `Hospitals` / `Diagnostics` before `Healthcare`
- `Semiconductors` / `EMS_Manufacturing` before `Technology`
- `Railways` before `Engineering_Capital_Goods`
- `Banking` / `NBFC` / `Housing_Finance` / `Capital_Markets` / `Fintech_Payments` / `Insurance` before `Financial_Services`

The orchestrator's `guess_sector_for()` in `helpers/core/parse_newsletter.py` encodes these rules; the first match wins.

Auto-detection by regex over content (first match wins per category; default to `geography/global`, `business_model/b2c`, `risk_investment/medium_risk`):

```python
def extract_enhanced_tags(content, entity_name, entity_type):
    tags = [f'entity_type/{entity_type}']
    sector_patterns = {
        # Carve-outs first (more specific) — order matters, first match wins
        'renewables':      r'\b(solar|wind|renewable|biofuel|biomass)\b',
        'pharma':          r'\b(pharma|drug|api|formulation|vaccine)\b',
        'hospitals':       r'\b(hospital|clinic chain)\b',
        'diagnostics':     r'\b(diagnostic|pathlab|pathology|ivd)\b',
        'semiconductors':  r'\b(semiconductor|chip|foundry|wafer)\b',
        'nbfc':            r'\bnbfc\b',
        'housing_finance': r'\b(housing finance|home loan)\b',
        'fintech_payments':r'\b(fintech|payments|upi|wallet)\b',
        # Parent catch-alls
        'banking':         r'\b(bank|banking)\b',
        'healthcare':      r'\b(healthcare|medical|wellness)\b',
        'technology':      r'\b(software|it services|technology|saas|cloud)\b',
        'energy':          r'\b(oil|gas|power|energy|petroleum|refiner)\b',
    }
    for sector, pattern in sector_patterns.items():
        if re.search(pattern, content, re.IGNORECASE):
            tags.append(f'sector/{sector}'); break
    if re.search(r'\b(large cap|big cap)\b', content, re.I):    tags.append('market_cap/large_cap')
    elif re.search(r'\b(mid cap|medium)\b', content, re.I):     tags.append('market_cap/mid_cap')
    tags.append('geography/india' if re.search(r'\b(India|Mumbai|Delhi)\b', content, re.I) else 'geography/global')
    tags.append('business_model/b2b' if re.search(r'\b(B2B|enterprise|wholesale)\b', content, re.I) else 'business_model/b2c')
    if re.search(r'\b(dividend|yield)\b', content, re.I):       tags.append('risk_investment/dividend')
    elif re.search(r'\b(growth|expansion)\b', content, re.I):   tags.append('risk_investment/high_growth')
    else:                                                        tags.append('risk_investment/medium_risk')
    return tags
```

## YAML Front Matter

**Structural contract: [`doc/schema/frontmatter_keys.md`](../schema/frontmatter_keys.md) (GENERATED from the JSON Schemas in `doc/schema/`) — enforced by the "Frontmatter schema" static check (`helpers/validators/frontmatter_schema.py`).** When creating notes, the schema's rules apply in full; the four that most often bite:

- `ticker: null` when unlisted — never the string `"N/A"` (the schema rejects it)
- Quote the dates (`created: '2025-11-16'`) — unquoted YAML auto-parses into date objects (the validator normalizes, but quoting is the canonical form)
- Permalink segments are lowercase `[a-z0-9_]` — underscores, no hyphens (`/companies/defense/apollo_micro_systems`, not `apollo-micro-systems`)
- No rogue keys — only the keys in the generated reference (schema is `additionalProperties: false`); a new key means a schema update first

Template (build by string substitution; `normalized_name` and `file_path` are the sync-critical fields):

```yaml
---
title: "{entity_name}"
type: {entity_type}            # company | sector
tags:
- entity_type/{entity_type}
- sector/{sector}
- market_cap/{market_cap}
- geography/{geography}
- business_model/{business_model}
- risk_investment/{risk_investment}
normalized_name: {normalized_name}   # MUST match filename (minus .md)
sector: {sector}
market_cap: {market_cap}
geography: {geography}
ticker: {ticker}                      # prefer .NS (NSE) over .BO (BSE); e.g. INFY.NS
created: YYYY-MM-DD
last_modified: YYYY-MM-DD
permalink: {permalink}
---
```

**Permalink rule** (lowercase): `companies/{sector}/{name}` · `sectors/{name}` · fallback `{type}s/{name}`.

## Adding an Entity

For each entity: extract tags → compute `normalized_name` → resolve `file_path` → insert SQLite row → write markdown file. `normalized_name` must match the filename and `file_path` must resolve.

> **Legacy snippet (predates 2026-07-28).** `entities.market_cap` no longer exists as a column — it is tag-derived (Bundle C2); membership edges are written to **`graph_edges`**, not a `relations` table (that name is now a backward-compat VIEW over graph_edges); and the dual-MCP tool subsystem shown below was removed. Follow the flow shape; use the house insert paths (`create_entity`, `helpers.core.db.connect`) today.

```python
def add_entity_with_tags(name, content, entity_type='company', sector=None, market_cap=None, ticker=None):
    tags = extract_enhanced_tags(content, name, entity_type)
    normalized_name = normalize_name(name)                       # MUST match filename exactly
    sector_dir = get_sector_directory(sector) if sector else 'Other'
    if entity_type == 'company':
        file_path = f'findata/Companies/{sector_dir}/{normalized_name}.md'
    elif entity_type == 'sector':
        file_path = f'findata/Sectors/{normalized_name}.md'
    else:
        file_path = f'findata/{entity_type.title()}s/{normalized_name}.md'

    sqlite_result = use_mcp_tool___sqlite___create_record({
        'table': 'entities',
        'data': {
            'name': name, 'normalized_name': normalized_name, 'entity_type': entity_type,
            'sector_classification': sector, 'market_cap': market_cap,
            'file_path': file_path, 'ticker': ticker,
            'last_updated': datetime.now().isoformat(),
        },
    })
    yaml_content = create_yaml_front_matter(name, tags, normalized_name, entity_type, sector, ticker=ticker)
    create_file_at_path(file_path, yaml_content + f"# {name}\n\n[Entity content from document]")
    # NOTE: tags are NOT stored on the entities row. They live in the note YAML
    # and are mirrored into the entity_tags table by sync_tags (see Validation).
    return {'sqlite': sqlite_result, 'tags_applied': tags, 'file_path': file_path,
            'normalized_name': normalized_name, 'ticker': ticker, 'validation_required': True}
```

`create_yaml_front_matter(...)` renders the YAML template above with the entity's values (`normalized_name`, `sector`, `market_cap`, `geography`, `ticker`, etc.).

**Relationships** — create both directions per company↔sector pair:

```python
for company in companies:
    for sector in sectors:
        if related_in_context(company, sector, content):
            use_mcp_tool___sqlite___create_record({'table': 'relations', 'data':
                {'source': company, 'target': sector, 'relation_type': 'part_of'}})
            use_mcp_tool___sqlite___create_record({'table': 'relations', 'data':
                {'source': sector, 'target': company, 'relation_type': 'has_company'}})
```

## Enhancing Existing Entities

When an entity already exists in the DB, the parsing action is **enhancement**, not creation — append a per-edition newsletter block to its note. This is mandatory for every existing company that has a concall / management / reference section in the source newsletter. One block per newsletter edition; editions accumulate over time (a company note may carry several `## The Chatter — …` blocks).

### Block format

Append to the company note (after existing content, before any closing matter):

```markdown
## The Chatter — <edition title>

**<Insight headline>:** <1–2 line summary of the management point>

**<Insight headline>:** <summary>

> "<verbatim quote if notable>"
> — <speaker>, <title>

*Source: The Chatter — <edition title>*
```

Rules:
- **Edition title** = the source newsletter's H1 stem (e.g. `HDFC, Groww, Yes Bank, Havells & More`). Use the same string in the heading and the `*Source:*` footer.
- Pull **3–5 bullet insights** from the company's concall/reference section only — do not fabricate. Each bullet = one management point (margins, capex, volume, pricing, guidance, segment color).
- Include **1 notable verbatim quote** with speaker + title when the transcript has one; omit the quote block otherwise.
- **Do not touch the YAML front matter** except bumping `last_modified`. Do not rewrite existing sections.
- **Skip the edition block if the note already has one for this edition** (idempotent — check for the `## The Chatter — <edition title>` heading first).
- If a figure from the rewritten source sits directly under the company's section, embed it inline with `![[images/<slug>_p{p}_img{N}.jpeg]]` at the relevant bullet.

### Finding the company's note

```sql
SELECT file_path FROM entities WHERE name = ?;   -- exact entity name
```
If multiple rows (legacy dupes), pick the one whose `file_path` resolves. If no row, the company is NEW — go to [Adding an Entity](#adding-an-entity) instead.

### Sector-level expert commentary

Newsletter editions often carry **interviews / podcasts / op-eds that are not tied to one company** but speak to a whole sector (e.g. a microfinance cycle, a regulatory shift, a commodity supercycle). Capture these on the **sector note**, not on any single company note. Same edition-block shape, but the heading uses the sector name and bullets synthesize the expert's thesis (not one company's numbers).

Detection: a section listed under `## Interviews/Podcasts` (or otherwise lacking a `## <Company> | …` header) whose speaker is a journalist / advisor / regulator / consultant, and whose content ranges across multiple players or industry-wide dynamics. Map it to the closest canonical sector (see the 42-sector list); if none fits cleanly, skip — do not force-fit.

```markdown
## The Chatter — <edition title>

**<Thesis headline>:** <1–2 line synthesis of the expert's point>

> "<verbatim quote if notable>"
> — <speaker>, <title>

*Source: The Chatter — <edition title>*
```

Rules:
- Append to the **sector note** (e.g. `findata/Sectors/NBFC.md`), after existing content.
- Pull **3–5 bullets** that capture the sector-level thesis (cycle dynamics, regulatory drivers, structural shifts, what breaks the pattern). Do not lift single-company commentary — that goes on company notes.
- Include **1–2 verbatim quotes** with speaker + title.
- Same idempotency rule (check for an existing `## The Chatter — <edition title>` heading on the sector note first).
- If the edition had **both** company-specific commentary *and* sector-level commentary, write one block on each relevant note — they don't overlap.

### Cross-edition sector synthesis

Beyond per-edition blocks, sector notes benefit from a **synthesis block** that distils the sector-level thesis across many editions into one place. This is the right vehicle when you want to capture the big picture (demand cycles, channel shifts, competitive structure, regulatory regime) rather than the events of any single quarter.

Trigger: opportunistically, when the sector note has no synthesis block and you have ≥4 company sections across ≥3 editions in that sector. Do NOT block if material is thin — the block should feel authoritative, not padded.

```markdown
## Newsletter synthesis — <Sector> (multi-edition)

**<Thesis headline>:** <synthesis of the cross-cutting theme — rural > urban, premiumisation, capacity cycle, regulatory reset, etc.>

**<Thesis headline>:** <synthesis>

> "<quote that crystallises the sector mood>"
> — <speaker>, <title>, <company>

*Sources: The Chatter — <edition1>; <edition2>; Points & Figures — <edition3>; Plotlines — <edition4> (<edition range>, <year>).*
```

Rules:
- Heading MUST be `## Newsletter synthesis — <Sector> (multi-edition)` — source-agnostic, distinct from a single-edition `## The Chatter — <edition title>` (or `## Points & Figures — …`) block so the two don't collide. Single-edition sector blocks keep their source newsletter's name in the heading; **only the multi-source synthesis drops it**.
- Pull **5–8 bullets**, each one a cross-cutting theme supported by ≥2 companies (do not lift a single company's view — that goes on its note). Use company attributions inline (e.g. "HUL, ITC and Britannia all flagged rural leading urban…").
- Include **1–2 verbatim quotes** from CEOs/Chairmen that capture the sector's structural narrative — attribute with speaker, title, company.
- Footer is `*Sources:*` (plural) listing editions actually drawn from, with each source newsletter named explicitly (The Chatter / Points & Figures / Plotlines).
- Idempotent: if the synthesis block already exists, refresh it in place rather than appending a second one.
- Treat `findata/The_PlotLines/` as a first-class source — its `# <Sector>` headers and `# Why It Matters:` / `## Watch For:` blocks are pre-digested sector synthesis and should be mined directly.

### Management commentary capture (mandatory)

The point of an edition block is to surface what management **said** — not to restate the business description. When enhancing a company note, each bullet MUST come from the concall / reference / interview section and capture one of:

- **Guidance** — forward outlook, growth/margin/volume targets, order book commentary
- **Margins** — beat/miss drivers, cost levers, pricing power, mix shifts
- **Capex / capacity** — sanctioned vs spent, commissioning timelines, funding mix
- **Segment / geography color** — which verticals accelerated/decelerated and why
- **Strategic shifts** — new bets, M&A, reorganisations, client wins/losses, leadership changes
- **Risk / macro response** — how mgmt is navigating headwinds (tariffs, rates, commodity, demand)

Do NOT pad the block with the company's boilerplate description (already in the note). Each bullet should read as something an investor couldn't get from the website — a delta, a number, a judgement. Prefer concrete figures ("₹72,275 cr revenue, +13.9% YoY") over vague summaries.

### Auto-generated chatter blocks (deterministic first pass)

`helpers/graph/derive_insights.py` automates the *first pass* of Stage 5: it reads each company's `## [Concall]` body, extracts every verbatim quote + speaker attribution + paraphrase, and renders them into a sentinel-wrapped `## The Chatter — <edition>` block. It also captures financial magnitudes (₹/%/bps/$bn/GW) into the `company_metrics` table. This is the deterministic capture layer that fills empty notes — the manual curation above refines it.

```bash
# Dry-run summary across all sources:
python3 helpers/graph/derive_insights.py findata

# Apply (write quotes/metrics tables + render auto note blocks):
python3 helpers/graph/derive_insights.py findata --apply

# Preview only (make target is deliberately dry-run — mass note
# rewrites must be an explicit --apply decision):
make derive-insights

# Verbose (list every quote + metric):
python3 helpers/graph/derive_insights.py findata --verbose
```

**What it writes:**
- `quotes` table — one row per verbatim quote (entity, quote_text, paraphrase, speaker_name, speaker_title, as_of_edition). Speakers are string attributes, NOT entities (the D6 person-node deferral is honored). `as_of_edition` stores the canonical edition STEM (normalized at the write boundary via edition_index, #136; note headings keep the display title) — join it straight to `entities.name` / `sources[].id`.
- `company_metrics` table — one row per financial magnitude (value_raw, unit, period, source_quote, best-effort metric_label).
- A `## The Chatter — <edition>` block in each company note, wrapped in `<!-- BEGIN auto chatter block (derive_insights.py) -->` … `<!-- END auto chatter block -->` sentinels (invisible in Obsidian render).

**Curation-safety rule (critical):** the auto pass NEVER clobbers human work. If a note already has a `## The Chatter — <edition>` heading that is NOT sentinel-wrapped (hand/agent-written), that edition is skipped. Only sentinel-wrapped auto blocks are refreshed on re-run. To replace an auto block with a curated one, delete the sentinel markers and rewrite the block by hand — the next run will leave it alone.

**What it does NOT do:** bullet curation (picking the best 3-5 quotes, rewriting paraphrases) remains a manual/LLM refinement step. The auto block surfaces ALL attributed quotes from the edition; a curator tightens it to the highlights. Idempotent via DELETE-then-INSERT on `source_ref LIKE 'derive:quotes:%'` / `'derive:metrics:%'`; re-run after any newsletter batch.

## Validation

After **all** companies are processed, run from the project root. Both exit `0` on success; `database_integrity_check.py` exits `1` below 95% validation rate:

```bash
python3 helpers/maintenance/sync_sector_wikilinks.py  # refresh the 42 sector-note auto rosters (run after creating entities; the orchestrator's --apply runs it as the first step of its stage 5 — orchestrator numbering: stage 5 = validate, distinct from this doc's manual-enhancement "Stage 5")
python3 helpers/validators/verify_notes.py          # YAML validity, required fields, content completeness, duplicates
python3 helpers/misc/database_integrity_check.py    # every file_path resolves, normalized_name sync, orphans
python3 helpers/core/sync_tags.py                   # rebuild entity_tags from note YAML (run after creating/editing entities)
python3 -m helpers.validators.frontmatter_schema    # B1: JSON-Schema contract (also part of `make static-checks`)
```

If a run legitimately introduces a NEW frontmatter key or value class: update `doc/schema/frontmatter.<type>.v1.json`, regenerate the reference (`python3 -m helpers.validators.frontmatter_schema --emit-doc`), then re-run the check — do not weaken the schema to pass.

Fix any issue traceable to the current run, then re-run. Pre-existing unrelated issues may be deferred.

### Inline sync checks (before relying on a created entity)

```python
def validate_normalized_name_format(filename, normalized_name):
    for c in ['&', ' ', '(', ')', '-', '__']:
        if c in filename: return False, f"Invalid char '{c}'"
    if len(filename) > 100: return False, "Exceeds 100 chars"
    if filename.replace('.md', '') != normalized_name: return False, "filename != normalized_name"
    return True, "OK"

def validate_entity_creation(entity_name):
    entity = query_sqlite("SELECT normalized_name, file_path FROM entities WHERE name = ?", [entity_name])
    if not entity: return False, "Not in SQLite"
    entity = entity[0]
    if not file_exists(entity['file_path']): return False, f"Missing file: {entity['file_path']}"
    ok, msg = validate_normalized_name_format(entity['file_path'].split('/')[-1], entity['normalized_name'])
    if not ok: return False, msg
    dup = query_sqlite("SELECT COUNT(*) c FROM entities WHERE normalized_name = ?", [entity['normalized_name']])
    if dup[0]['c'] > 1: return False, f"Duplicate normalized_name: {entity['normalized_name']}"
    return True, "All sync rules validated"

def validate_bidirectional_sync():
    issues = []
    for m in query_sqlite("SELECT name, file_path FROM entities WHERE file_path NOT LIKE 'findata/%'"):
        issues.append(f"Bad path: {m['name']} -> {m['file_path']}")
    for d in query_sqlite("SELECT normalized_name, COUNT(*) c FROM entities GROUP BY normalized_name HAVING c > 1"):
        issues.append(f"Duplicate: {d['normalized_name']} ({d['c']})")
    return not issues, issues
```

### Checklist
- [ ] **(Existing-markdown inputs)** Images captured into `<newsletter_dir>/images/` and source `.md` rewritten (0 remote URLs) — see [Image Capture](#image-capture). **(PDF inputs)** Converter ran and produced `<slug>.md` + `images/` — see [PDF → Markdown](#pdf--markdown)
- [ ] Filename: PascalCase, single underscores, no special chars, ≤100 chars
- [ ] `normalized_name` matches filename exactly
- [ ] `file_path` resolves to an existing file
- [ ] No duplicate `normalized_name`
- [ ] Bidirectional `part_of`/`has_company` relations created
- [ ] Enhanced tags populated
- [ ] **Existing entities enhanced** — every existing company with a concall/management section in the newsletter has a `## The Chatter — <edition>` block appended (see [Enhancing Existing Entities](#enhancing-existing-entities))
- [ ] **Sector-note auto rosters refreshed** (`sync_sector_wikilinks`) — mandatory after any entity creation; stale rosters pass every other validator (user catch 2026-08-25; now the first step of the orchestrator's stage 5)
- [ ] Short, token-efficient names (no `Ltd`/`Company` suffixes)
- [ ] **(Newsletter inputs)** Relevant figures embedded in company notes via `![[images/<slug>_p{p}_img{N}.jpeg]]`
- [ ] `verify_notes.py` exits 0
- [ ] `database_integrity_check.py` exits 0 (≥95%)
- [ ] `frontmatter_schema` exits 0 (no rogue keys, no `"N/A"`/wrong-typed values, ISO dates, underscore permalinks)

### Search examples
```sql
-- tags now live in the normalized entity_tags table (run sync_tags first):
SELECT e.name FROM entities e JOIN entity_tags t ON t.entity_name = e.name
WHERE t.tag = 'sector/healthcare' AND e.entity_type = 'company';

-- large-cap technology companies (tag intersection)
SELECT e.name FROM entities e
JOIN entity_tags a ON a.entity_name = e.name AND a.tag = 'sector/technology'
JOIN entity_tags b ON b.entity_name = e.name AND b.tag = 'market_cap/large_cap';
```

---
*Version 8.7 | B1 frontmatter contract wired in: schema reference + the four drift rules (no `"N/A"` tickers, quoted ISO dates, underscore-only permalink segments, no rogue keys), schema check in Validation + checklist, schema-evolution path documented. Prior 8.6: Output destination is now explicit: **ask the user where output goes — never assume**. PDF inputs: ask for the `<output_dir>` before running `pdf_conv_md.py`. Markdown inputs: capture is in-place (rewrites the source `.md`, figures into its own `images/`), so confirm the markdown's location with the user. Prior 8.5: filenames are just the PDF stem (no `_by_<Model>` suffix); Stage 0 → markdown.*
