---
title: "derive render pass — group shared-note buckets + per-note block plan; VSS query core consolidation"
status: executed
filed: "2026-09-08"
executed: "2026-09-08"
completed_md: "214"
area: "helpers/graph/derive_insights.py, helpers/core/get_tickers.py, helpers/core/vss_index.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# derive render pass — group shared-note buckets + per-note block plan; VSS query core consolidation

**Date:** 2026-09-08 · **Status:** PROPOSED · **Mode:** measured first
(profile + phase harness), implement same-day.
**Area:** `helpers/graph/derive_insights.py` — `render_notes`,
`render_metrics_notes`, `_replace_or_insert_block` call path.

## 1. Motivation (measured)

After quote-capture landed the catch-all home
(`findata/Super_Sectors/Quotes.md`, **4.87 MB**, 668 chatter blocks —
~23% of the vault in one note), `derive_insights.py` regressed from
~1.24 s (#211) to 7.7 s wall (vault) and 93 s (`--corpus`) against the
4.0 s perf budget. Phase harness (in-process, serial scan):

| phase | vault | `--corpus` |
|---|---|---|
| Corpus.load | — | 0.14 s |
| scan (quotes + metrics) | 1.28 s / 8186 q | 4.49 s / 27762 q |
| render_notes | **7.83 s** / 1245 targets | **105.5 s** / 1992 targets |
| apply_quotes + apply_metrics (dry) | ~0.0 s | ~0.01 s |

cProfile: `_replace_or_insert_block` 6.2 s cum (1245 calls), of which
`_existing_hand_block_for_edition`'s single `_AUTO_BLOCK_RE.sub` is
2.6 s and `_swap` 0.7 s. Instrumentation: **47 render targets resolve
to the one catch-all path — 229 MB of regex input across 47 full-note
passes.**

## 2. Root cause

The catch-all works by pointing many entities' `entities.file_path` at
one note (`ensure_quotes_catchall`, quote-capture S5). Both renderers
loop per-**entity** (`render_notes` :1850, `render_metrics_notes`
:2158) and each iteration does a full read → whole-note regexes →
write cycle on the SHARED path:

1. `_existing_hand_block_for_edition`: `_AUTO_BLOCK_RE.sub` over the
   full 4.87 MB body, per bucket.
2. `_replace_or_insert_block`: `_AUTO_BLOCK_RE.finditer` walking all
   668 blocks + a heading search per block, per bucket.
3. `_swap`: full-string rebuild (`text[:s] + repl + text[e:]`) +
   byte-compare **even when the outcome is byte-identical** — the
   idempotency guard itself is O(note) per bucket.

At #211 the largest note was ~117 KB and the pattern was fine; 40×
note growth turns it into O(targets × note_size). It grows with every
triage batch (more catch-all entities AND a longer note).

## 3. Design

### S1 — group render buckets by note path

`render_notes`: after `by_entity`, regroup to
`path → {entity → {edition → quotes}}` (insertion-ordered — same
entity order as today). One read, one `_load_frontmatter`, one
note-level `--stale-only` gate (merged scanned stems across the
path's entities — matches apply-mode end state), one sources splice,
one balance check, one write per path per run. The chatter bucket
loop body (dedup → `render_chatter_block` → replace) is unchanged
per (entity, edition).

`render_metrics_notes`: same grouping; `_render_metric_note` splits
into inner (text-in/text-out) + outer (read/write once per path);
entities apply sequentially on the in-memory text, preserving today's
last-entity-wins KF block content for shared notes byte-for-byte, and
the note-level gate computes once per path on the merged stems.

### S2 — per-note block plan (the O(note) work happens once)

Per note, build once: `spans = {norm_edition → (start, end)}` from one
`_AUTO_BLOCK_RE.finditer`, plus the once-computed hand-written heading
check (same `_AUTO_BLOCK_RE.sub` + `_CHATTER_HEADING_RE.search`
semantics as `_existing_hand_block_for_edition`, evaluated a single
time — sentinel-interior mutations cannot change the stripped text).
Per bucket, replace the whole-note passes with:

- hand-block match → `skipped += 1` (unchanged semantics);
- edition span exists and `text[s:e] == new_block` → O(block)
  short-circuit, `skipped += 1` (the idempotency guard, now
  block-local);
- edition span exists and differs → rescue nested blocks (same
  `_extract_nested_blocks` contract; unbalanced = skip) and splice in
  place, invalidating the span map (lazily re-parsed — mutations are
  rare: only moved editions);
- no span → `_find_insertion_point` insert (as today).

`_replace_or_insert_block` itself stays untouched (tests + other
callers keep the exact current semantics); `render_notes` stops using
it in favor of the plan. The KF side keeps `_replace_or_insert_kf` but
searches once per note (span or None cached; re-searched after a
mutating splice), short-circuits on block equality, and only
genuinely-different blocks pay the rebuild.

### Preserved invariants (S1–S3)

Curation safety (hand block per edition, first-heading semantics
unchanged), nested-block rescue, byte-identical idempotency, balanced
marker write gate, OKF `generated` bump, `--dry-run` writes nothing.

### Deliberate counter shift (S1–S3)

`written`/`gated` count NOTES; with grouping, a shared path counts
once instead of once per entity. Visible only when content actually
changes (today's steady state: written = 0 for both). `skipped` still
counts edition blocks — the 1245 stays 1245.

### S3 — VSS query-side consolidation (one importable core)

Operator observation 2026-09-08: VSS-over-company_embeddings now
surfaces in three scripts. Measured map (read, not assumed):

- **get_tickers.py** carries TWO overlapping query stacks in-process:
  the per-call tier (`_best_vss_match` python zip-sum cosine over
  `_decoded_vss_table`'s sha1-keyed decode cache) and the run-index
  tier (`_VssRunIndex` + `build_vss_run_index` / `check_vss_run_index`
  / `_vss_match_with_index`, float64 matvec — #211 S3), kept side by
  side for embed_eval/tests compat.
- **triage_pending_quotes.py** already reuses `get_tickers.vss_match`
  (import, not a copy).
- **query.py** runs the DuckDB lane: `v_embeddings` (FLOAT[] projected
  from the same table at connect, sqlite+vss extensions,
  `array_cosine_similarity` brute force ~3 ms) behind
  `semantic_neighbors`; **enrich_relations.py** is a consumer of that
  (`gq.semantic_neighbors`), not a copy.
- **company_neighbors_base_probe.py** (bench) holds its own
  fetch+cosine — benches stay self-contained (FlatKNN precedent).

Slice: extract the python query core — `_VssRunIndex`,
`build_vss_run_index`, `check_vss_run_index`, `_vss_match_with_index`,
`_decoded_vss_table`, `_raw_vec`, `_best_vss_match`, the decode cache —
into **`helpers/core/vss_index.py`** as the single home. get_tickers
keeps its public names as thin wrappers (zero caller churn for
triage/embed_eval/tests); the two tiers stay (they serve different run
shapes — that duality is measured design, not accident). Non-goals:
query.py's DuckDB lane (different engine, serves SQL graph joins;
documented as the SQL-side consumer of the same table) and the bench
copy.

## 4. Acceptance

1. Byte-identical `--stale-only` dry-run stderr on vault AND corpus
   vs the pre-change capture — the `quotes=`/`metrics=` scan summary and
   the render would-write counters (1245/687/496) stay byte-identical;
   the "N quotes would write" lines are the ONE deliberate delta,
   replaced by the honest `new/unchanged/stale` split (baseline must be
   refreshed after the counters land). NOTE: the pre-fix corpus baseline
   is void — `--corpus` whole-vault was scanning the ENTIRE corpus
   (33,730 quotes) instead of the three newsletter trees; fixed to mirror
   the plain path (8,186 quotes) on 2026-09-08. Capture the new baseline.
2. Targeted tests: shared-path two-entity chatter render (one write,
   both editions' blocks present, byte-identical to sequential apply);
   second run idempotent (zero writes); hand-written block preserved
   on a shared path; KF shared path keeps last-entity-wins content
   with one write.
3. Existing suite green (`test_derive_insights.py`,
   `test_quote_capture_s4.py`, fuzz region tests).
4. Timing (3-run median): vault wall ≤ 2.0 s, `--corpus` ≤ 12 s,
   perf-gate `derive_insights` leg back inside its 4.0 s budget.
5. S3: `test_get_tickers.py` VSS tests + embed_eval surface green
   through the moved core with get_tickers wrappers; no caller changes
   outside get_tickers (triage/embed_eval imports untouched).

## 5. Risks

- Span-map staleness after an in-place splice → lazy re-parse
  (correctness over micro-optimization; mutations are rare).
- Edition collision across entities sharing a path → entity insertion
  order decides, exactly as apply-mode sequential writes do today.
- Dry-run gate semantics on shared paths move from
  per-entity-against-original to per-path-against-original with merged
  stems — strictly closer to apply-mode; pinned by the byte-diff (1).
- Rescued-nested-block layout differs from `new_block` → falls to the
  slow splice path (correct, rare, stable after first rescue).

## 6. Non-goals

- ~~The quotes/metrics "would write" counters~~ — **ADDRESSED 2026-09-08**
  (do it, not a non-goal): `stable_prefix_replace` now returns a
  `ReplaceResult{inserted, kept, deleted}` breakdown and dry-run is backed
  by the read-only `stable_prefix_diff` — a converged scan reports
  `(0, N, 0)` instead of pretending every scanned row would write; apply
  reports real churn. Same for the events sibling. Reporting-only, no DB
  schema change, one prefix `SELECT` per leg at this volume (~ms).
- Vault layout (splitting/capping the catch-all note) — curation
  decision, separate arc.
- Corpus-mode attribution rate (22% vs 72% vault) — data question.
- Scan-side parallelism changes (scan is 1.3 s / 4.5 s, healthy).
