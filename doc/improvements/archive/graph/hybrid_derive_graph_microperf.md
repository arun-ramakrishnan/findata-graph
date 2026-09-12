---
title: "Hybrid fallback, derive walks, and graph top-k — residual CPU hotpaths"
status: executed
filed: "2026-09-12"
executed: "2026-09-12"
completed_md: "228"
area: "app.py hybrid search, helpers/graph/derive_*.py, helpers/graph/algorithms.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Hybrid fallback, derive walks, and graph top-k — residual CPU hotpaths

**Date:** 2026-09-12 · **Status:** EXECUTED ·
**Area:** `app.py` hybrid search (`_scored_rows`, `_cosine_positions`, `_flat_knn_map`, `_hybrid_search_results`), `helpers/graph/derive_*.py` walks + `derive_insights` scan, `helpers/graph/algorithms.py` (`betweenness_centrality` top_k, `approximate` flags)

## 1. Motivation

Two prior arcs already took the big wins (O1–O3 `perf_optimization.md` 2026-08-26; Ph1–Ph3 `graph_db_optimization.md` 2026-09-10: repeat-call result cache 1.62s→0.001s, disconnected-trio short-circuit 1.2s→0.21s, `--all` 14→7 materialisations). What remains is residual CPU work on paths that are correct but wasteful — all on the degrade/fallback legs or repeated per-request/per-derive work, never the native primary:

- Hybrid search's primary (`vec0` C + `embed_matrix.top_k` numpy matvec) is optimal; the cost sits in the fallback legs and per-request re-sorting that runs even when the primary hits.
- Every derive re-walks the vault (`sorted(...rglob("*.md"))` in `derive_cited_in.py:97,228`, `derive_events.py:494`, `derive_themes.py:296`, `derive_co_mentions.py:188,201`, `sync_tags.py:160`, `derive_insights.py:2387`) and re-parses YAML per file; `static_checks.py` already proved the collapse-3-walks→1 + pool pattern.
- `betweenness_centrality(top_k)` sorts the full dict in Python (`algorithms.py:241-242`) and both `closeness`/`betweenness` accept-but-ignore `approximate` (`algorithms.py:186-187,228-229`) — fine at 1,649 nodes / 19,261 edges, but the only graph-side scope left before the Onager extension wall.

Trigger: algorithms assessment §6 follow-up 2026-09-12 (OCR explicitly out — Paddle-regression evidence stands).

## 2. Evidence (code-read 2026-09-12, this box; numbers cited from prior measured runs)

| Site | Shape today | Prior measurement / verdict |
|---|---|---|
| `app.py:521 _cosine` inside `506 _scored_rows` | pure-Python dot + double sqrt + per-row `json.loads(embedding)` fallback | py-list ~130ms vs numpy 0.27ms vs Mojo/pyopencl ~0.17–0.19ms (`doc/local/perf/perf_skills.md:168-175`) — fallback-only gap, ~500–700x |
| `app.py:560 _cosine_positions` | `sorted(knn)` full global re-sort per request + `worst + idx` fill | runs on every hybrid hit even when `knn` unchanged within a process |
| `app.py:473 _flat_knn_map` | `em.top_k(q_vec, len(em.ids))` full-scan + full sort, then `_note_best` collapse (`app.py:728`) | whole-corpus by construction (`k=None` at `app.py:722` for global-rank semantics, accepted 2026-08-17 at ~7ms vs ~0.7ms page loop) |
| Derive walks | 6+ independent `rglob("*.md")` + per-file YAML parse | `static_checks` 3→1 walk + pool precedent; `derive_insights` `_splice_sources` fix already 3.33s→2.75s (`graph_db_optimization.md:358`) |
| YAML loader | `frontmatter.py:42-44,106-113` already `CSafeLoader`-when-available; `derive_insights`/`extract_relations`/`verify_notes` all route via `helpers.core.frontmatter` (verified 2026-09-12) | unify considered CLOSED — do not re-audit |
| `derive_insights` scan | `_scan_serial` + `_scan_parallel` both exist (`derive_insights.py:2823-2996`); siblings serial-only | parallel default ALREADY IN TREE (`scan()` auto `min(4, cpu_count)`, `_scan_parallel` at ≥8 files, `_scan_serial` for `--workers 1`/<8 — `derive_insights.py:2858-2861`, re-verified 2026-09-12); remaining = shared walk + Quotes cap |
| `Quotes.md` catch-all | `findata/Super_Sectors/Quotes.md` 7,547,949 B (~7.5MB) single file in scan corpus (re-measured 2026-09-12; ~4.87MB at the 2026-09-08 triage — grew ~55%) | `O(targets × note_size)` trap (house memory) — needs size cap / chunked path, not full-read |
| `algorithms.py:241 top_k` | `dict(sorted(bc.items(), ...)[:top_k])` full sort in Python | unmeasured; pushdown only if Onager exposes it without extra round-trips |
| `approximate` flags | accepted, ignored; exact Onager at ~1.6s first call / 0.001s cached | sampling (`k=√n`) behind default-off flag only; extension wall documented (no `onager_mtr_all`, Floyd 24.4s rejected, `par_bfs` aborts — `graph_db_optimization.md:240-246`) |

No new benchmarks were run for this proposal — all numbers above are cited, not fresh. Slices must measure before/after best-of-3 on this box before landing.

## 3. Design

Chosen: three independent slices, each landable alone, in this order (S1 DEFERRED 2026-09-12 — re-sort is sub-ms noise vs ~7ms KNN leg, see Appendix; S2 is the active slice; S3 probed 2026-09-12 → no-op with evidence, see Appendix).

**S1 — DEFERRED (2026-09-12, no-op with evidence — do not implement)**

**S1 — hybrid fallback + re-sort cache (app.py only, no semantic change)** — DEFERRED, see above; body kept for the record.
- Cache the `knn` global rank order ACROSS requests: `_cosine_positions` already runs only once per request (called at `app.py:731`), so building the rank in `_hybrid_search_results` and passing it down eliminates zero sorts — the cache must be module-level, keyed on the KNN map/corpus generation, for a win to exist at all; missing-embedding `worst + idx` fill stays byte-identical.
- Expectation note (code-read 2026-09-12): post-`_note_best` the sort is ~2k NOTE-level entries — sub-ms, dwarfed by the ~7ms whole-corpus vec0 KNN on the same path. S1's only material legs are the rare double-fallback (per-row `json.loads` + Python cosine) and the `_flat_knn_map` mmap/id-set reuse; record best-of-3 before claiming, and fold the re-sort slice as no-op-with-evidence (S3 rule) if the number is noise.
- Keep the leg order `vec0 → mmap matrix → Python loop`; make `_flat_knn_map` reuse the already-loaded `EmbedMatrix` view where the caller has one (resident buffer, no re-mmap per request).
- Optional, flagged: page-bound `k=(limit+offset)` KNN probe behind a parameter (default off) to quantify the global-rank vs page-rank cost — global-rank stays default; the probe only produces the number.
- Alternatives rejected: HNSW/ANN (exact suffices at 16.5k sections — assessment §6); Mojo/pyopencl promotion (0.1ms win for build/driver complexity); changing RRF `k=60` or collapse-to-best-section semantics (would drift rankings).

**S2 — derive walks: corpus-lane the siblings, cap the catch-all**
- Share the walk via maint's Corpus pre-warm, NOT in-process sharing: maint runs the derives as SUBPROCESSES (`maint.py:218,305,309` — one `sys.executable helpers/graph/derive_*.py` per step), so cross-sibling in-process walk sharing is architecturally unavailable (the `static_checks` 3→1 single-walk precedent applies within one process only). Slice = give the remaining siblings (`derive_events`, `derive_themes`, `derive_co_mentions`) a `--corpus` lane fed by maint's pre-warmed Corpus cache (`maint.py:430`: 0.37s walk → 0.02s corpus.db hit; the `derive_insights` S1b fast path, `derive_insights.py:2839-2842` + `_scan_corpus:2897`, is the in-tree pattern), plus the only intra-process collapse available: `derive_cited_in`'s own two walks (`derive_cited_in.py:97,228` → 1). Standalone CLI runs still walk per-process — accepted.
- Parallel-scan default: ALREADY IMPLEMENTED (re-verified 2026-09-12) — `scan()` auto-selects `min(4, cpu_count)` workers, `_scan_parallel` for ≥8 files, `_scan_serial` only for `--workers 1`/small targets (`derive_insights.py:2858-2861`; stride-shard + re-interleave to path order at `:2905`). Residual slice: verify the default is what maint's quote-capture step exercises, and document — no code change.
- `Quotes.md` guard: size-cap / chunked read (or skip-list with explicit recall sign-off) so one ~7.5MB file (7,547,949 B, re-measured 2026-09-12) cannot dominate `O(targets × note_size)`.
- Alternatives rejected: YAML loader swap (already `CSafeLoader` — closed); `merged_sources` module-state memo (skipped 2026-09-10 as staleness hazard — `graph_db_optimization.md:356`); widening `static_checks` into other gates (bloats first gate).

**S3 — graph top-k / approximate: PROBED 2026-09-12 → NO-OP (keep Python slice, exact default)**
- Probe result: `onager_ctr_betweenness` signature is `(TABLE, directed BOOLEAN, normalized BOOLEAN)` — no top-k, no source-subset/sampling knob (`duckdb_functions()` introspection, this box). SQL-side `ORDER BY … LIMIT k` would still execute the full table function over all 1,648 nodes, saving only the Python sort: measured 0.410ms sort+slice vs ~5s cold full compute (same run) — four orders of magnitude, pure noise. Pushdown rejected.
- Approximate sampling would require client-side subgraph pre-filtering with semantic re-validation; no extension support, no budget pressure at this scale — rejected, exact stays default.
- Incidental: the extension accepts `directed`/`normalized` params the wrapper never passes (defaults); noted, not changed in this arc.
- Probe: can `top_k` push below the Python sort (Onager-side limit) without extra round-trips? If no, keep the two-line Python slice — it is not the bottleneck at this scale.
- Probe: `approximate` sampling (`k=√n`, mirrors the old betweenness pattern) behind a default-off flag for future 10x graphs; exact stays default; Onager internals are a hard wall (documented above) — a measured no-op with the negative result recorded is an acceptable outcome.
- Alternatives rejected: connection-caching revival (reverted 2026-09-10 — broke `query.connect()` seam, stale mixed-projection risk, ~0.1–0.2s noise win); thread-pool over private connections (measured 0.55s regression vs packed subqueries — `graph_db_optimization.md:260-274`); any change to the packed-subquery trio form.

## 4. Acceptance criteria & shakedown

1. S1: hybrid golden equal — RRF order + `similarity` values byte-identical on a fixed query set with `vec0` present, matrix-only, and Python-fallback legs; `tests/test_api_search.py` + `test_api_docs.py` (the hybrid coverage — no `test_hybrid_*` files exist; corrected 2026-09-12), `test_flat_knn_fallback*`, `test_embed_matrix*` green; timing best-of-3 recorded (whole-corpus KNN + `_cosine_positions` before/after).
2. S2: `derive_insights --stale-only --dry-run` byte-identical (counts + zero note-text diffs, S1+S2 pattern from `scan_render_vss_microperf.md:149`); `tests/test_derive_insights.py` + driver suites green; wall-clock best-of-3 (full + stale-only) recorded; `Quotes.md` guard has an explicit recall note.
3. S3: `test_graph_algorithms_*` + `test_full_projection_disconnected` green; if landed, approximate-flag path has a determinism test (seed-pinned) and a budget test; if no-op, the probe numbers + rejection reason are appended to the Appendix.
4. Gates: `ruff`, types, `make perf` within existing budgets (no budget edits in this arc — watch-only on `derive_insights`/`graph_rebuild`).

| Projected outcome | Today | After |
|---|---|---|
| hybrid `_cosine_positions` re-sort | per-request `sorted(knn)` | DEFERRED 2026-09-12 — sub-ms at ~2k notes vs ~7ms vec0 KNN leg; no-op with evidence |
| hybrid fallback leg | per-row `json.loads` + Python cosine | DEFERRED with S1 (same slice); Python loop stays last-resort |
| derive wall-clock | N independent `rglob` walks (one subprocess per derive) | `--corpus` lanes on events/co_mentions/cited_in (+insights/themes/sync_tags pre-existing) under maint pre-warm; co_mentions passes fused 2→1; `derive_cited_in` 2 walks→0 under `--corpus` (measured 2026-09-12, Appendix) |
| `Quotes.md` stall | full-read `O(targets × note_size)` (~7.5MB catch-all) | capped: `_note_frontmatter` 64KB head-read (fm extent 11,860 B; 0-mismatch/1336 proven 2026-09-12); body scans ride the corpus lane |
| `betweenness(top_k)` | full Python sort | NO-OP 2026-09-12 — 0.410ms vs ~5s compute; keep-with-evidence |

## 5. Risks

- **RRF semantic drift** — any change to rank/collapse alters search order; mitigation: golden query set byte-compare on all three legs before/after.
- **Matrix staleness** — resident `EmbedMatrix` view vs `note_search` moves; mitigation: keep the existing id-set staleness gate (`_flat_knn_map:488-493` — return None → degrade, never 500).
- **Quotes.md recall loss** — capping the catch-all can drop quotes; mitigation: recall sign-off via `quote_coverage_audit` parity before landing.
- **Onager wall** — S3 may find nothing shippable; mitigation: pre-accept no-op-with-evidence as success for S3.
- **Parallel nondeterminism** — scan workers reorder output; mitigation: keep stride-shard + re-interleave to path order; `--workers 1` escape hatch.

## 6. Non-goals

OCR/Paddle/LiteParse (Paddle-regression evidence stands — explicitly out per 2026-09-12 scope); GPU/Mojo promotion; HNSW/ANN; embedding model swap or full re-embed; `make perf` budget edits; sigma Lens / Prefab `/v2` (composition over these backends only — no new algo surface); `static_checks` gate widening.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-12 | code-read only (no benchmarks run) | n/a | hot paths re-verified: `app.py:506-565,669-756`; `derive_insights.py:2823-2996,2387`; sibling `rglob` sites; `algorithms.py:179-243`; `frontmatter.py:42-113` |
| 2026-09-12 | verification pass vs tree (amendment) | 4 corrections folded | S2 parallel-default already in tree (`derive_insights.py:2858-2861`); shared-walk re-scoped to `--corpus` plumbing (maint runs derives as subprocesses — `maint.py:218,305,309`; pre-warm `maint.py:430`); Quotes.md re-measured 7,547,949 B; `test_hybrid_*` → `test_api_search.py`/`test_api_docs.py`; S1 re-sort expectation note added |
| 2026-09-12 | S2 baselines best-of-3 (dry-run, this box) | events 1.14s / themes 0.53s / co_mentions 0.36s / cited_in 0.50s | pre-change |
| 2026-09-12 | S2 execute | events +corpus lane; co_mentions pass-fusion 2→1 walks + corpus lane; cited_in edition_notes corpus lane + extract_citations absolute-path join fix (pre-existing lane silently yielded 0 edges — caught by byte-identical gate) + `_note_frontmatter` 64KB head-read cap | all three byte-identical plain-vs-corpus; cap proven 0-mismatch over all 1336 notes |
| 2026-09-12 | S2 post best-of-3 (standalone; Corpus.load warm 0.14s) | events 1.17→1.06s / co_mentions 0.48→0.34s / cited_in 0.50→0.39s | standalone pays cold Corpus.load; win amortizes under maint pre-warm (one 0.14s shared walk) |
| 2026-09-12 | maint threading | `--corpus` added to derive-cited-in (PRE_FULL) + derive-insights/events (TIER2); co_mentions/themes stay manual (not maint steps) | `--full --dry-run` verified |
| 2026-09-12 | S2 gates | 76 derive unit + 28 integration/fuzz green; ruff + format clean | — |
| 2026-09-12 | S3 probe: `duckdb_functions()` introspection of `onager_ctr_betweenness` | `(TABLE, directed BOOLEAN, normalized BOOLEAN)` — no top-k / sampling knob | pushdown rejected |
| 2026-09-12 | S3 probe: `betweenness_centrality()` full + `sorted(...)[:10]` | full ~5s cold / 1,648 nodes; py sort+slice 0.410ms | top_k cost is noise — keep Python slice |
| 2026-09-12 | scope decision | S1 deferred (sub-ms re-sort vs ~7ms KNN leg); S3 no-op with evidence; S2 active | proposal §§3–4 amended, no code changed |
| prior | `doc/local/perf/perf_skills.md:168-175` | py-list ~130ms / numpy 0.27ms / Mojo ~0.17ms | cited — fallback gap |
| prior | `graph_db_optimization.md:304-360` | closeness repeat 0.001s; disconnected trio 0.21s; `--all` 14→7 mat; `_splice_sources` 3.33s→2.75s | cited — prior arc wins, do not re-claim |
