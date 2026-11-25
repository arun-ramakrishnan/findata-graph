---
title: "Test coverage gap sweep #2 — re-pin the post-11-01 feature wave: Rust/Mojo in a blocking gate, then unit, fuzz, chain and perf slices"
status: executed
filed: "2026-11-24"
executed: "2026-11-25"
completed_md: "569"
area: "tests/ + Rust/ + Mojo/tests + Makefile"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Test coverage gap sweep #2 — re-pin the post-11-01 feature wave: Rust/Mojo in a blocking gate, then unit, fuzz, chain and perf slices

**Date:** 2026-11-24 · **Status:** PROPOSED ·
**Area:** `tests/`, `Rust/`, `Mojo/tests/`, `Makefile`, `tests/run_gate_report.py`

## 1. Motivation

The first gap sweep (filed + executed 2026-11-01, completed.md #463,
`../archive/testing/test_coverage_gap_sweep.md`) closed what it
targeted: four unit files, four fuzz files, two integration chains,
four perf legs, five bugs fixed. It was a snapshot, and the gaps have
regrown on everything except Python unit coverage.

Since 2026-11-01 the main branch took 146 commits that touched 183
helper modules, added 48 helper modules and added 82 test files. Of
those 82, five are fuzz files and two are integration files, and all
seven came from the 11-01 sweep itself. Nothing fuzzed, chained or
benchmarked has been added for any lane that landed after 11-01.

The operator asked (2026-11-24) for a coverage analysis including
fuzz, perf and integration in both Mojo and Rust, observing that gaps
accumulate as features land. This proposal is the slice plan that came
out of it.

**Corrections to the oral analysis (2026-11-24), found while filing:**

- Integration tests are NOT unexecuted. `make integration` was dropped
  as a standing arc leg on 2026-11-09 because all `-m integration`
  tests also run inside qa's pytest (`doc/procedures/gates.md`). The
  gap is chain *coverage*, not execution.
- `mojo-test` is not gate-less. The `Makefile:361` comment says
  "FROZEN 2026-10-26 — retired from advisory", but gates.md records it
  re-added to advisory on 2026-11-21 when the freeze was revoked. The
  comment is stale (S1 fixes it). `mojo-bench` is still manual-only.
- `helpers/graph/algorithm_wrappers.py` is re-exported from
  `helpers/graph/query.py:5401`, so its behaviour is exercised
  transitively by the 81 test files that reference `query`. It has no
  direct pin, which is a smaller gap than first stated.
- `helpers/maintenance/build_route_p4.py` has only comment mentions
  outside itself (`helpers/maintenance/store_sweep.py:86`,
  `helpers/maintenance/db_maint.py:139`,
  `helpers/validators/static_checks.py:1233`). It is an ops tool from
  the #552 arc, not a production-wired module. Demoted to non-goal.

## 2. Evidence (measured 2026-11-24, this box)

Method: name/content matrix over the 183 helper modules changed since
2026-11-01 (`git log --since=2026-11-01 --name-only --diff-filter=AM
-- helpers`), then `rg -w <module>` across `tests/*.py` split by file
kind, then importer grep across `helpers/ app.py` to separate
production-wired gaps from bench/ops code. Same method as the 11-01
sweep. It measures production-wiredness × semantic centrality, not
lines. Raw log in Appendix A.

### Where coverage held

| Layer | State | Verdict |
|---|---|---|
| Python unit | 36 of 48 modules added since 11-01 have a test file that imports them | healthy |
| Integration execution | all `-m integration` tests run inside qa's pytest | healthy |
| Mojo parity | `mojo-parity` (92 canonical keys) is a qa leg | healthy |
| Rust property tests | proptest in both extension crates (9 + 8 tests) | healthy in shape |

### Gap 1 — Rust and Mojo tests do not block

State before S1 (line numbers as of `af8ccbdf2`; S1 moved the first
and third rows, see §3):

| Gate leg | Gate | Source |
|---|---|---|
| `rust-test` (cargo test --workspace) | advisory, SKIPs without cargo | `Makefile:525` |
| `rust-clippy`, `rust-deny` | advisory | `Makefile:518`, `Makefile:532` |
| `mojo-test` | advisory (re-added 2026-11-21) | `doc/procedures/gates.md` |
| `mojo-bench` | no gate, manual only | `Makefile:352` |
| `fuzz` | advisory | `doc/procedures/gates.md` |

The two Rust extension modules sit under production query paths
(`helpers/graph/stats.py:207`, `helpers/graph/scipy_bridge.py`,
`helpers/graph/l1_closeness.py`). A failing Rust or Mojo test cannot
fail `make qa`.

### Gap 2 — Rust surfaces

| Crate | `#[test]` | proptest | Note |
|---|---|---|---|
| `graph_kernels` (1388 lines, added 11-01) | 12 | 9 | 7 `#[pyfunction]`s |
| `cosine_knn` | 7 | 8 | 2 `#[pyfunction]`s (`knn`, `knn_batch`) |
| `embed_cache` lib (126 lines) | 1 | 0 | 3 bins with none |

`graph_kernels` exposes seven functions (corrected 2026-11-24: the
first filing listed four). Python-side coverage, measured with
`rg -w` over `tests/` and the importers:

| Function | Production importer | Python test that reaches it |
|---|---|---|
| `all_pairs_bfs_stats` | `helpers/graph/stats.py:212` | `test_graph_stats.py` (:235, :380) via the public stats path |
| `limited_bfs_sources` | `helpers/graph/hyper_trails.py:422` | `test_hyper_trails.py:284` kernel batch vs scipy pool, payload-identical |
| `personalized_pagerank` | `helpers/graph/scipy_bridge.py:806` | `test_graph_stats.py:485`, `test_scipy_bridge.py:882` |
| `personalized_pagerank_vec` | none | `test_graph_stats.py:530` only (test-only API) |
| `personalized_pagerank_batch` | `helpers/graph/scipy_bridge.py:852` | `test_scipy_bridge.py:1079` only: batch rows equal the single-seed solves on one 4-leaf star |
| `brandes_fold_sources` | `helpers/graph/l1_betweenness.py:397` | `test_l1_kernel_parity.py` |
| `core_bfs_sources_batch` | `helpers/graph/l1_closeness.py:330` | `test_l1_kernel_parity.py:117`, `:149` |

Two residual gaps behind that table:

- The Rust kernels assume a validated CSR (the `#[pyfunction]`s check
  `indptr` shape and index range first). No test feeds a random valid
  CSR to the batch PageRank lane against an independent scipy oracle;
  the one pin is a single 4-node differential against the other Rust
  lane.
- `embed_cache`: one unit test for the whole library, three binaries
  (`embed-cache-lookup.rs`, `sha_s2.rs`, `trial.rs`) with none. The
  Python half has `tests/test_embed_cache.py` and
  `tests/test_embed_gc.py`. The Rust decode boundary
  (`decode_embedding`, `Rust/embed_cache/src/lib.rs:14`: blob length
  `% 4`, JSON text as `Vec<f32>`) is exercised by that one test with a
  single malformed case (a 3-byte blob).

### Gap 3 — Mojo surfaces

| Module | `.mojo` unit test | Note |
|---|---|---|
| `Mojo/src/common/integrity_check.mojo` (82 KB) | none | most-changed file in the lane (10 commits since 10-20); covered only by the 92-key parity golden |
| `Mojo/src/common/{list_utils,multiproc_bridge,spinlock_counter,taskgroup_fanout}.mojo` | none | no Python reference either |
| `Mojo/src/bench/{a2_topk_batch,motifs_null_batch,bfs_bipartite_all_batch,bfs_bipartite_all}.mojo` | none | all added or changed since 11-01 |
| `Mojo/src/common/{cosine,slice_utils,window_shingles}.mojo` | yes | `test_cosine`, `test_slice_utils`, `test_window_shingles` |

`Mojo/tests/` is five files, 632 lines, last changed 2026-11-13.

### Gap 4 — Python unit

| Module | LOC | Production refs | State |
|---|---|---|---|
| `helpers/maintenance/maint_timing.py` | 235 | 16 helpers | no test mentions it by name |
| `helpers/graph/hyper_hysc.py` | 152 | 0 | added 11-20 (#549), untested and unreferenced |
| `helpers/graph/hyper_core_periphery.py` | 122 | 0 | same |
| `helpers/graph/hyper_hypergraphmt.py` | 131 | 0 | same |
| `helpers/graph/algorithm_wrappers.py` | 200 | 1 | covered only transitively via `query.py` |

The three HGX lanes are a wiring question before a testing question.
Operator direction (2026-11-24): find a consumer and wire them — the
intent is to expand their use, not to retire them (S3 item 3).

### Gap 5 — Fuzz

- 29 `test_fuzz_*.py` files. The five added since 11-01 are all from
  the 11-01 sweep.
- 47 of the 183 changed modules have ≥5 production references and no
  fuzz file.
- No fuzz leg for any post-11-01 lane:

| Surface | Why it is fuzz-shaped |
|---|---|
| `helpers/core/db_engine.py` (643 lines, 18 refs) | engine switch: result parity across backends |
| `helpers/core/rank_fusion.py` (94 lines, 6 refs) | pure fusion: order invariance + idempotence |
| `helpers/graph/hyper_hif.py` | row validation on malformed incidence rows |
| `helpers/graph/hyper_trails.py` (712 lines) | differential vs its own Python fallback |
| `helpers/misc/swap_convert.py` (596 lines) | roundtrip on synthetic stores |
| `helpers/maintenance/name_token_candidates.py` (546 lines) | parser totality on arbitrary text |
| `helpers/maintenance/ingest_nse_xbrl.py` (494 lines) | malformed-row rejection |

### Gap 6 — Integration chains

- 17 `test_integration_*.py` files; the two added since 11-01 are the
  11-01 sweep's.
- 55 of the 183 changed modules have unit tests and no fuzz or
  integration reference.
- Multi-module flows with no end-to-end test:

| Chain | Parts, each unit-tested alone |
|---|---|
| foreign links | `foreign_name_isin_probe` → `foreign_links_generate` → `foreign_links_apply` |
| swap flow | `swap_convert` → `swap_rewrite_flows` → `swap_fk_integrity` |
| hyper trails | dispatch → Mojo kernel or Python fallback → result shape |
| org membership | `derive_org_membership` → `name_token_candidates` |

### Gap 7 — Perf

`BENCHMARKS` (`tests/run_perf_benchmarks.py:192`) has 35 legs. Three
look newer than 11-01 by name (`derive_co_mentions`, `ladder_indexing`,
`ppr_edge_attribution`); the rest are the 11-01 roster or older.

| Hot path with no leg | Size |
|---|---|
| `hyper_trails` / `hyper_motifs` | 712 / 474 lines, 8 / 6 commits since 11-01 |
| `db_engine` | 643 lines |
| `context_pack` | 728 lines |
| `swap_convert` | 596 lines |
| `name_token_candidates` | 546 lines |
| `foreign_links_generate` | 329 lines |

## 3. Design

Ten independently landable slices, in order. S1 first because it
makes every later test actually able to fail a gate. All tests hermetic
(synthetic fixtures, tmp-path; `conftest.py` env redirects apply).

- **S1 — make Rust and Mojo tests block.** EXECUTED 2026-11-24
  (acceptance 1 closes on the first three qa runs).
  1. Roster: `rust-test` and `mojo-test` moved from the advisory entry
     to qa in `tests/run_gate_report.py` (`GATES` dict, not the
     Makefile), placed after `mojo-parity`. Steps are submitted FIFO, so
     a step listed earlier never waits on a later one: `mojo-parity`
     (the front phase's long pole, 71–92 s green) starts in the same
     wave as before and the two new legs (~9 s and ~7.5 s warm) run in
     its shadow. Advisory keeps `rust-clippy` and `rust-deny`.
  2. SKIP semantics — no separate toolchain-presence leg. A presence
     leg would only fail on the same box state that makes the real leg
     skip, and the runner cannot mark a step as skipped (`run_step`
     never sets `Result.skipped`; an rc-0 SKIP renders as "✓ OK").
     Instead each promoted leg is made unable to pass without running:
     - the qa harness exports `RUST_TEST_STRICT=1` (the
       `SCRIPT_KNN_STRICT` pattern) and `make rust-test` then FAILS on a
       missing cargo; direct runs and boxes without the toolchain keep
       the rc-0 skip;
     - `mojo-test` already fails without `mojo`; its one silent-pass
       path was an EMPTY roster — `MOJO_TESTS` is a cwd-relative
       wildcard, and `MOJO_TESTS=` printed "✓ Mojo tests passed
       (0 files)" and exited 0. Guard added in `Makefile.mojo`.
  3. Comments fixed: `Makefile:361`, the `qa:` / `advisory:` help lines,
     `doc/procedures/gates.md`, `doc/local/engineering/rust_database.md`.
  4. Wall time measured before the move (Appendix B): standalone medians
     9.0 s and 7.5 s. Replaying the last 12 qa runs through the 4-job
     FIFO pool at those times gives +0.0 s on 11 runs and +2.7 s on the
     one where `mojo-parity` fast-failed in 0.7 s; at the contended
     medians from advisory history (26.65 s and 17.33 s) it gives +0.0 s
     on the 8 runs with a green `mojo-parity` and +13.5 to +16.1 s on
     the 4 where it fast-failed. The measured delta from the first three
     qa runs replaces the replay in Appendix B.

- **S2 — Rust parity and boundary tests.** EXECUTED 2026-11-24
  1. `tests/test_graph_kernels_parity.py`: `personalized_pagerank_batch`
     through `personalized_pagerank_scores_multi` against a scipy
     oracle on random CSR graphs (Hypothesis, moderate case count). The
     batch lane's only pin today is one 4-leaf star against the
     single-seed lane. Skips cleanly when `graph_kernels` is not
     installed (triage T1).
  2. `Rust/embed_cache`: unit tests at the decode boundary — blob length
     not divisible by 4, empty blob, JSON text that is not a
     `Vec<f32>` (non-numeric entries, nested arrays, integers past the
     `f32` range), column types other than blob/text.
  3. `personalized_pagerank_vec`: no importer; `ripwire --callers`
     first, then remove it or document it as test-only.
  - LANDED: `tests/test_graph_kernels_parity.py` (batch-vs-spsolve
    parity at 1e-9 rel on random binary CSR graphs, batch row ==
    single-seed kernel, vec one-hot reduction, arbitrary teleport
    sums-to-one, no-dangling linear-in-teleport, and the 10-case
    malformed-input guard matrix) + `tests/test_embed_cache_boundary.py`
    (codec pins: len%4, empty, legacy TEXT-JSON, malformed JSON,
    dict/None; cache-layer dispatch pins via a native duckdb host conn:
    corrupt → miss + embed, empty → miss, packed BLOB → hit, mixed).
    `personalized_pagerank_vec` triage: it IS an exported kernel with
    production-compatible guards — kept, and the tests above pin it
    (documenting it as a tested surface rather than removing a Rust
    function that sector scans may adopt). 28 new tests pass; ruff
    clean; skips when the extension is absent.
  - LANDED (Rust half, 2026-11-24 evening): `Rust/embed_cache/src/lib.rs`
    `mod tests` grew 10 dedicated cases around the one pre-existing
    lookup test (11 total): empty blob → miss (the Python codec decodes
    `b""` to a zero-dim vector — recorded asymmetry); blob len not a
    multiple of 4 (1,2,3,5,6,7,9); packed little-endian f32 round trip
    incl. hand-packed `0x3F800000`/`0xC0000000` words and a 4-byte blob
    = one dimension; legacy text JSON list accepted (ints, `1e2`, `[]`,
    whitespace); 10 malformed-text cases (truncated, extra `]`, object,
    string member, nested list, JSON string, null, bare number, empty,
    trailing text) → miss; Null/Integer/Real → miss; lookup scoped to
    the model; duplicate indices all answered + undecodable row → miss;
    empty batch → no hits, no misses. Characterisation (probe, kept as
    a pin `legacy_text_outside_f32_range_is_still_a_hit_today`):
    serde_json reads f64 and casts, so `[1e40]` → `Some([inf])`,
    `[1e-50]` → `Some([0.0])`, `[NaN]` → None — a finite guard in
    `decode_embedding` would flip the first two to misses (follow-up
    candidate). `cargo test -p embed_cache` 11 passed; `make rust-test`
    (workspace) rc 0 — cosine_knn 7 + 8 proptest, embed_cache 11,
    graph_kernels 12 + 9 proptest; `cargo fmt` + `clippy
    --all-targets` clean. The parity file also grew the two §7-T1
    sentinel pins (below), 18 tests total in that file.

- **S3 — Python unit pins.** EXECUTED 2026-11-24 (items 1–2 evening;
  item 3 decided + executed earlier the same day)
  1. `tests/test_maint_timing.py`: timing-record shape, the consumer
     contract the 16 importing helpers rely on, missing/None timing.
  2. `tests/test_algorithm_wrappers.py`: pin the wrapper signatures and
     the `query.py` re-export list, so an extraction drift fails here
     rather than in a route.
  3. HGX lanes — DECIDED 2026-11-24 (operator, after the consumer
     map in Appendix C):
     - `hyper_hypergraphmt` is RETIRED: file deleted. It returned the
       same partition as `hyper_hysc` on every family with structure
       (ARI 1.000), one-hot memberships everywhere, at 3–15× the cost.
       It had zero non-self references and no stored rows, so deleting
       it changes no stored graph.
     - `hyper_hysc` stays an unwired probe (operator, 2026-11-24: if
       HySC is not the answer, name what is). S9 unblocked its loader,
       but on this data it puts 75–95% of each family in one block and
       returns one-hot memberships on every family fitted
       (Appendix C), so wiring it would add no block the incumbent
       lacks. What to use instead, per question (Appendix C):
       - "which groups is this entity in, and how strongly" — the
         `core_mass_arrow` columns (S10).
       - "which entities are close" —
         `hyper_arrow.shared_group_pairs_arrow` (landed, see Appendix
         C): per pair, shared hyperedge count and shared inverse-size
         mass. Graded where HySC is not (shared counts run 2–11 on
         `edition`), no fit, no sampler, sub-second on the live
         families.
       - "which community" — the incumbent `hypermmsbm_community`
         (stored, K=8); HySC agrees with it on 0.2% of pairings
         (ARI 0.002–0.009) and offers no mixed membership to justify
         replacing it. `bipartite_louvain_community` was not compared.
       - `event` has no overlap structure to cluster: 63 of 1,175 nodes
         share a hyperedge with another node more than once. That is
         why both seats returned one giant block; no clustering method
         fixes it. Treat `event` as a pairing table, not a community
         source.
     - `hyper_core_periphery`: its signal is reproduced by a SQL
       aggregate (S10, done) at Spearman +0.921; the 25 s HGX sampler
       stays an unwired probe.
  - LANDED (items 1–2, 2026-11-24 evening): `tests/test_maint_timing.py`
     (11 pins: run/phase row shape the query layer reads; explicit
     elapsed beats derived and extra[:500]/summary[:2000] caps; run ids
     continue at MAX+1; no target → nothing stored or printed; step
     table on stderr only; exception → exit 1 + `ValueError: boom`
     summary + the raised phase still recorded; failed write →
     `[maint_timing] timing record skipped (` + exit code kept;
     active/current stack routes the module-level `phase` to the
     innermost timer; target precedence arg > `MAINT_TARGET` > "" and
     cmd default `Path(sys.argv[0]).stem`; AST pin over `helpers/**` —
     importers may import only {RunTimer, active, current, phase};
     maint_query reads a recorded run back). `tests/test_algorithm_wrappers.py`
     (7 pins: signatures + return annotations; query.py re-exports
     exactly `__all__` ∪ {`_company_names`, `_label_to_edge_type`,
     `_label_to_table`} by identity + AST, no same-named defs; label
     registry mapping incl. BelongsToHierarchy→belongs_to,
     ExposedTo→exposed_to, unknown → `ValueError`; `_company_names` is
     the one SQL the wrappers issue (`SELECT name FROM v_company`);
     Economic unweighted / named label `supplier_to` + as_of / weighted
     variant; membership wrappers call the edge projection only, sorted).
     Can-fail: 25 in-memory source mutations of the two modules, ALL
     KILLED (driver in session scratch; pytest meta-path loader, never
     writes to disk); it first found 2 gaps in my own tests (wcc data
     had the lower cid on the alphabetically-first name; as_of was only
     exercised on the named-label branch) — fixed in the tests, then
     25/25. The importer-surface pin proven with a real stray file
     (`from helpers.maintenance.maint_timing import _warn` → pin fails;
     removed after). Findings: the module-level `mt.phase` context
     manager has ZERO importers in helpers/ and tests/ (kept + tested;
     deleting it is the operator's call); registry edge types
     BelongsTo→`part_of`, SuppliesTo→`supplier_to`,
     SubsidiaryOf→`subsidiary_of`. Both files ruff/format clean.

- **S4 — Fuzz legs.** EXECUTED 2026-11-24
  `test_fuzz_db_engine.py`, `test_fuzz_rank_fusion.py`,
  `test_fuzz_hyper_hif.py`, `test_fuzz_hyper_trails.py`,
  `test_fuzz_swap_convert.py`, `test_fuzz_name_tokens.py`. Each gets a
  roundtrip or parity property plus a malformed-rejection property, and
  a can-fail proof (a hand-built bad input that the property rejects).
  `hyper_trails` is differential: Mojo dispatch result equals Python
  oracle on the same fixture, when Mojo is available.
  - LANDED: all six files; 41 fuzz tests + 1 Mojo-gated skip (50
    passed / 1 skipped including S5 chains in the same run). Property
    focus: `_DuckDBConn` executemany/execute roundtrip + wrong-arity
    rejection; RRF/anchored/two-leg fusion formula pins + position
    monotonicity; HIF export/read roundtrip (nodes/edges/weights/footer
    metadata) with empty-source → empty tables and missing-edge JOIN
    drop can-fail; swap_convert view GLOB→regexp_matches translation,
    NOCASE→lower(col) index translation, unparsed-SQL rejection; name
    tokenizer determinism/memoization/NFKD accent fold/`&`≡" and
    " equivalence + None rejection; hyper-trails differential vs an
    even-hop BFS oracle with unknown-source ValueError + limit
    truncation can-fail. Mojo dispatch leg skips unless
    `MOJO_TRAIL_BFS=1` and the binary exists.

- **S5 — Integration chains.** EXECUTED 2026-11-24
  `test_integration_foreign_links_chain.py`,
  `test_integration_swap_chain.py`,
  `test_integration_hyper_trails_chain.py` (dispatch both ways),
  `test_integration_org_membership_chain.py`. Fixture stores only,
  mirroring `test_integration_hyper_family_chain.py`.
  - LANDED: four chain files, 10 chain tests, all passing with the S4
    fuzz set (50 passed / 1 skipped). Chains: foreign-links
    validate→propose→scan_conflicts→apply (and re-apply idempotency +
    conflict never overwritten); SQLite→DuckDB swap convert (row
    parity, view regexp_matches, index lower(), rowid-alias sequence,
    `_parity` ok); hyper-trails build_bipartite census → trail_distances
    vs oracle → trail_source_stats shape → Rust kernel batch equals
    scipy per-source (importorskip) → CSR bins written with matching
    metadata; org-membership collect (latest valid_from wins) →
    dry_run writes nothing → apply writes edges + `role` column →
    idempotent re-run → shrinkage converges.

- **S6 — Perf legs.** EXECUTED 2026-11-24
  Add `hyper_trails`, `db_engine` and `context_pack` to `BENCHMARKS`,
  budgets = quiet-box baseline ×2 (house convention), 3 runs per leg.
  - LANDED: three legs appended to `tests/run_perf_benchmarks.py`
    `BENCHMARKS` (after `cosine_knn_ratios`): `db_engine_10k`
    (`helpers/bench/db_engine_write_bench.py --db
    {scratch}/db_engine_bench.duckdb --rows 10000`, budget 150.0s),
    `hyper_trails` (`helpers/graph/hyper_trails.py --sources 200
    --limit 40`, budget 2.0s), `context_pack`
    (`helpers/graph/context_pack.py INFY.NS`, budget 2.6s). Measured
    quiet-box baselines (2026-11-24, 3 runs each): db_engine
    executemany 71.87/69.21/71.10s + bulk-Arrow rewrite
    0.27/0.28/0.28s (per-row class ≈250× the rewrite class);
    hyper_trails 0.94/0.87/0.94s; context_pack 1.21/1.26/1.17s.
    Harness `run_one` sanity: all three legs OK (db_engine_10k
    70.02s, hyper_trails 0.91s, context_pack 1.25s). New bench script
    `helpers/bench/db_engine_write_bench.py` is idempotent (CREATE
    TABLE IF NOT EXISTS + DELETE, unregister of `_bench_bulk`).

- **S7 — Mojo unit tests.** EXECUTED 2026-11-24
  `Mojo/tests/test_list_utils.mojo`, `test_integrity_check_units.mojo`
  (the non-parity branches of the 82 KB module, chosen by reading it),
  and a differential test for the four untested `src/bench` kernels
  against their Python equivalents where one exists. `mojo-bench` stays
  manual; this slice does not reopen the bench gate.
  - LANDED (unit half): `Mojo/tests/test_list_utils.mojo` (9 tests:
    insertion/merge sort empty/sorted/reversed/duplicate/lexicographic,
    merge==insertion on mixed, single, pairs) and `Mojo/tests/
    test_integrity_check_units.mojo` (43 tests: uniq_sorted,
    _ticker_dup_suppressed, subset, norm_underscore,
    dir_structure_ok, filename_ok, name_ok, note_has_tag, csv_contains,
    is_stopword, is_generic, meaningful_tokens, fuzzy_match). Both run
    green via `mojo run -I Mojo/src/common -I Mojo/src/bench -I
    Mojo/vendor/mojo-yaml/src -I Mojo/vendor/mojo-intel-gpu/src`
    (43/43 pass; meaningful_tokens bridges Python lower fn).
  - LANDED (diff half): `tests/test_mojo_bench_kernels.py` — 5
    differential tests, all green with the built binaries (skip when
    `Mojo/bin/<bin>` missing): `a2_topk_batch` vs plain-Python sorted
    top-K (toy + sparse matrices); `bfs_bipartite_all_batch` vs
    `trail_source_stats` oracle (reachable + hop_histogram per source,
    `_mojo_bins`/`_mojo_batch_invoke` helpers reused);
    `motifs_null_batch` vs `configuration_null` on the same RNG stream
    (rel_tol 1e-9); `window_shingles` golden-fixture parity leg
    (expects `parity: 210 / 210 passed (0 failed)`).
  - NOTE (timing, not a cost): `TestSuite` prints
    `PASS [ 51.409 ] test_meaningful_tokens_dedupes`, but the whole file
    runs in 2.4 s including compile. The first `Python.evaluate` costs
    ~0.05 s and TestSuite reports durations ×1000 once the Python bridge
    is live in-process (probe: a standalone Mojo file timing the first
    and later `Python.evaluate` calls with `perf_counter_ns`).
    The qa `mojo-test` leg does not pay it.
  - OPEN: none — S7 complete.

- **S8 — cargo-fuzz targets for the Rust crates. PARKED 2026-11-24**
  (operator: nightly cannot be installed and slow builds are not
  being tracked now). Nothing below is executed or counted in this
  arc; the design stays as written for when nightly is available.
  Scope provenance: added to scope by
  the operator 2026-11-24 (filed first as "alternative (b), rejected").
  0. Prerequisite — an operator-approved environment change: cargo-fuzz
     needs a nightly compiler (unstable flags plus LLVM sanitizer
     support; cargo-fuzz README) and this box has `stable` and `1.99.1`
     only (`rustup toolchain list`); cargo-fuzz is not installed.
     `rustup toolchain install nightly`, then
     `cargo install cargo-fuzz`. `Rust/rust-toolchain.toml` stays on
     1.99.1.
  1. `Rust/fuzz/` as an independent workspace
     (`cargo fuzz init --fuzzing-workspace=true`) with path dependencies
     on the three crates, so `cargo test --workspace` under the pinned
     toolchain — the qa `rust-test` leg — never builds sanitizer
     targets. Pinned by a test.
  2. Extract the CSR validation duplicated across the seven
     `graph_kernels` `#[pyfunction]`s
     (`Rust/graph_kernels/src/lib.rs` :231, :353, :414, :468, :533,
     :987, :1040) into one plain function, so the targets exercise the
     production validator rather than a copy. The three private
     kernels the targets need (`ppr_power_iterate`,
     `ppr_power_iterate_seed`, `decode_embedding`) become `pub`.
  3. Targets and oracles:

     | Target | Entry points | Oracle |
     |---|---|---|
     | `cosine_topk` | `cosine_knn::{cosine_similarity, topk, topk_single, topk_parallel}` | no panic on any `f32` bit pattern, length or `dim`; single equals parallel; returned rows distinct, in range, scores non-increasing |
     | `csr_hist` | `graph_kernels::{all_pairs_hist_seq, all_pairs_hist, stats_from_hist}` | valid CSR: sequential equals forced-parallel; invalid CSR: rejected by the validator, kernel never entered |
     | `trail_brandes_bfs` | `limited_bfs_hists`, `brandes_fold`, `core_bfs_distances` | same two-part oracle |
     | `ppr` | `ppr_power_iterate`, `ppr_power_iterate_seed` | finite output, mass conserved |
     | `embed_decode` | `embed_cache::lookup_batch` against an in-memory SQLite DB, `sha256_hex` | arbitrary bytes or text stored as the embedding → a hit whose length is divisible by 4, or a miss; never a panic; hash is 64 hex characters |

  4. Seeds from the existing proptest cases and the Python parity
     fixtures; corpus under `Rust/fuzz/corpus/` (gitignored except the
     seeds).
  5. `make rust-fuzz`: manual and time-boxed
     (`cargo fuzz run <target> -- -max_total_time=60` per target), SKIPs
     with a message when nightly or cargo-fuzz is missing. Not a qa leg:
     sanitizer builds are slow and would tie the blocking gate to
     nightly. An advisory slot is triage item T5.
  6. A crash is a finding: recorded here, fixed in its own change, and
     regression-pinned as a proptest case under the stable toolchain.

- **S9 — `hyper_arrow` live path follows the house door. (DONE)**
  `load_incidence_arrow(source="live")` (`helpers/graph/hyper_arrow.py`
  `_from_live`) attaches `DEFAULT_DB_PATH` =
  `memory/research.db` through DuckDB's SQLite scanner, and
  `store_rowcount` probes the same file. That file is gone since the
  research store moved to `memory/research.duckdb` (which holds
  `hyper_edges` 4,636 rows and `hyper_incidences` 59,660), so on this
  box `store_rowcount` returns `None` and every HGX lane skips.
  1. `_from_live` reads through `helpers.core.db.connect()` (the
     post-S6 door that routes `.duckdb` paths) instead of the SQLite
     scanner; `store_rowcount` drops its `path.exists()` probe and
     its `sqlite_master` lookup for `table_exists` from
     `helpers/core/db_engine.py`.
  2. Hermetic test over a tmp-path DuckDB with the two incidence
     tables: `load_incidence_arrow` returns the same rows from a
     `.duckdb` path as from `source="snapshot"`, and
     `store_rowcount` returns the count rather than `None`.
  3. Sweep the other `DEFAULT_DB_PATH` importers for the same stale
     default (`rg -n DEFAULT_DB_PATH helpers`), recorded as findings.
  4. Executed: `_from_live` reads through `connect(path, read_only=True)`;
     `store_rowcount` uses `table_exists` and treats the `.duckdb`
     sibling of an absent `.db` as present (never creates a store).
     `tests/test_hyper_arrow_live_path.py` (5 pins): DuckDB path,
     research-store redirect, absent store never created, store without
     incidence tables, live == snapshot on the `edition` family.
     Finding: `connect()` redirected the old default on its own; the
     only stale consumers were the two `hyper_arrow` functions (every
     other `DEFAULT_DB_PATH` importer in `helpers/` passes it to
     `connect()`). Before the fix, live on this box: `store_rowcount`
     → `None`; after: the partition family loads 2,601 rows in 0.33 s
     and the `edition` family equals the snapshot row for row. Not
     covered: a lane reading `memory/research.duckdb` while another
     process holds it (qa history has a `snapshot_check` lock conflict
     on that file) — the first real `recompute-hyper` run records
     whether a lock conflict appears.

- **S10 — core-periphery signal as a SQL aggregate (DONE).**
  `hyper_arrow.CORE_MASS_SQL` + `core_mass_arrow(table)`: per entity,
  `n_hyperedges`, `mean_edge_size`, `inverse_size_mass =
  SUM(1 / hyperedge size)`, `max_edge_size`, keyed per
  (edge_type, label). Pinned by `tests/test_hyper_core_mass.py`
  (4 hermetic pins). It is a rank proxy for `coreness`, not a
  reproduction of its values (Appendix C).

Alternatives considered:

- (d) Keep HypergraphMT for the `event` family, the only one where the
  two seats disagree (ARI 0.40) — rejected: its memberships are still
  one-hot there (minimum 0.51), and nothing consumes either partition
  today. Re-proposed from the snapshot if a consumer appears.

- (a) Full `coverage.py` percentage sweep — rejected again: measures
  lines, not risk; same call as 11-01.
- (b) cargo-fuzz / libFuzzer targets for Rust — filed as rejected
  (needs nightly and a corpus; proptest plus the S2 boundary tests
  cover the decode seam at lower cost). The operator added it to scope
  the same day: it is S8 now — parked, see S8.
- (c) A separate toolchain-presence qa leg for S1 — rejected: it could
  only fail on the box state that already makes the real leg skip, and
  the runner has no SKIP row to carry it (Appendix B).

## 4. Acceptance criteria & shakedown

1. S1: `tests/test_run_gate_report.py` pins the roster, the strict pin
   and the two recipes; the four new-behaviour pins fail on the
   pre-move code (Appendix B). `gate_query recent --gate qa --last 3`
   shows `rust-test` and `mojo-test` in the qa leg list on three
   consecutive runs; the measured qa wall-time delta replaces the replay
   in Appendix B.
2. S2: `.venv/bin/python3 -m pytest
   tests/test_graph_kernels_parity.py -q` green with the kernel
   installed; `make rust-test` green with the new `embed_cache` tests;
   prove the parity test fails by asserting one deliberately wrong
   oracle value.
3. S3: each new test file green. HGX lanes as decided: S9 green with
   `rg -n 'DEFAULT_DB_PATH' helpers/graph/hyper_arrow.py` empty;
   `rg -w hyper_hypergraphmt helpers app.py Makefile` empty;
   `tests/test_hyper_core_mass.py` green (4 pins); no route wired in
   this arc, so no eval-gate bullet for S3.
4. S4: `make fuzz` green; every new property has a can-fail proof
   test next to it.
5. S5: `.venv/bin/python3 -m pytest
   tests/test_integration_foreign_links_chain.py
   tests/test_integration_swap_chain.py
   tests/test_integration_hyper_trails_chain.py
   tests/test_integration_org_membership_chain.py -q` green, all
   tmp-path hermetic.
6. S6: 3 quiet-box runs of `tests/run_perf_benchmarks.py`, new legs
   within budget each time; budgets recorded in Appendix A.
7. S7: `make mojo-test` green with the new files.
8. S8 (PARKED — not an acceptance criterion while parked):
   `make rust-fuzz` runs each target for its time box; every crash
   is recorded as a finding. `cargo test --workspace` under the pinned
   toolchain does not build `Rust/fuzz`. Each target has a can-fail
   proof: a seed that trips the oracle once one check is relaxed.
9. Gates stay green: targeted tests only post-fix; the full quartet
   ONCE on the operator's go per `doc/procedures/gates.md`.
10. Eval gate: S1, S2, S4–S8 change tests, gate rosters and Rust
    internals only — no bullet. S3 item 3 adds a consumer for the HGX
    lanes; if that consumer is a query-visible route, the eval-gate
    bullet is added before the wiring lands.
11. A defect found by any slice is recorded as a finding here and fixed
    in a separate targeted commit, never silently inside the slice.

Measured close-out (2026-11-24 evening):

1. S1 — pins green (B10 can-fail); `gate_query recent --gate qa
   --last 3` shows `rust-test` and `mojo-test` on the last three
   consecutive qa runs (4755 15.42s/12.41s ✓, 4768 16.19s/12.79s ✓,
   4776 16.19s/10.80s ✓ — the runs' own verdicts at those timestamps
   were FAIL/FAIL/PASS on other legs); the measured qa delta is in
   Appendix B.
2. S2 — parity file 18 passed with the kernel installed; `make
   rust-test` green with the new `embed_cache` tests (11 in the lib);
   wrong-oracle proof: scaling the spsolve oracle by `1 + 1e-6` in
   memory fails `test_batch_matches_spsolve_per_seed` (1 failed /
   17 passed) — Appendix A.
3. S3 — all three green (11 + 7; 22 with `test_hyper_core_mass.py`).
   `rg -n 'DEFAULT_DB_PATH' helpers/graph/hyper_arrow.py` is NOT empty
   (4 hits) — the criterion predates S9: the constant stays as the
   loader's default path but is routed through
   `helpers.core.db.connect(path, read_only=True)`;
   `rg -w hyper_hypergraphmt helpers app.py Makefile` is empty.
4. S4 — `make fuzz` green (289 passed, 1 Mojo-gated skip, 48.21s).
5. S5 — exact command green (12 passed).
6. S6 — 3 quiet-box runs per new leg through the harness recipe, all
   within budget; budgets recorded with the S6 LANDED block
   (Appendix A rows added).
7. S7 — `make mojo-test` green with the new files (7 files; twice on
   2026-11-24).
8. S8 — parked; not an acceptance criterion while parked.
9. Full gate quartet on the final code — the ONLY open criterion;
   needs the operator's go (S2–S7 + the §7 T1 pin + the live-gate
   ports have landed since qa 4776).
10. Eval gate — no bullet needed (no query-visible route in this arc).
11. Findings recorded in the slice LANDED blocks and §7; none fixed
    silently.

| Projected outcome | Today | After |
|---|---|---|
| Rust/Mojo test legs that can fail `make qa` | 0 (`mojo-parity` only) | 2 (`rust-test`, `mojo-test`) — done in S1 |
| `graph_kernels` `#[pyfunction]`s reached by a Python test | 6 of 7; the batch lane on one 4-node fixture | 6 of 7 with an independent scipy oracle for the batch lane; `personalized_pagerank_vec` removed or documented test-only |
| `embed_cache` decode-boundary cases (Rust) | 1 malformed case, inside the single lookup test | ≥6 dedicated cases |
| libFuzzer targets over the Rust crates | 0 | 0 (S8 parked: no nightly on the box) |
| changed modules with no test mention, production-wired | 1 (`maint_timing`) | 0 |
| HGX lanes with a production consumer | 0 of 3 | HypergraphMT retired; core-periphery signal via SQL (S10); HySC stays a probe — its questions are answered by the co-membership SQL (Appendix C) |
| fuzz files | 29 | 35 |
| integration files | 17 | 21 |
| perf legs | 35 | 38 |
| `.mojo` unit test files | 5 | 7–8 |

## 5. Risks

- **S1 lengthens or destabilises qa** — `mojo-test` is one `mojo run`
  per file. Measured before the move (Appendix B): with a green
  `mojo-parity` (71–92 s) the replay delta is +0.0 s, at standalone and
  at contended leg times; when `mojo-parity` fast-fails the front phase
  grows by 3–16 s. The one case the shadow does not bound is a cold
  cargo build under contention (advisory history has a single
  `rust-test` run at 307.83 s). If the first three real qa runs show a
  delta the box cannot afford, both legs go back to advisory and the
  gap is recorded as accepted.
- **A missing or stale `graph_kernels` `.so` hides S2** — its Python
  tests skip (triage T1); S1 does not cover that case.
- **S8 ties a manual lane to nightly** — the reason it is parked; if
  revisited it stays out of qa and advisory
  (T5); the pinned toolchain never builds `Rust/fuzz`. A sanitizer
  build of `rayon` + `pyo3` crates is slow to compile the first time.
- **Differential fuzz needs Mojo available** — `hyper_trails` fuzz
  falls back to Python-only properties and skips the Mojo leg visibly.
- **Perf legs on a contended box** — budgets from quiet-box baselines,
  3 runs, parked rather than rerun on failure (gates.md).
- **The HGX consumer is a product call** — decided 2026-11-24
  (Appendix C): HypergraphMT retired, HySC kept as an unwired probe, no
  route wired. Separable from the rest
  of S3.
- **Dense HGX fits swap the box** — HySC and the core-periphery sampler
  build node-by-node matrices; `group` (13,211 nodes) is 1.4 GB per
  matrix. A probe run on unsized families put the box into swap on
  2026-11-24. Any further HGX fit sizes the family from the parquet
  first, caps nodes, and runs in the foreground under a memory cap.
- **S9 changes which store the lanes read** — a lane that was skipping
  because the SQLite file is gone starts finding 4,636 hyperedges in
  `memory/research.duckdb`. The fix lands with a hermetic test that
  asserts the loader follows `helpers.core.db.connect`, and the
  `recompute-hyper` roster is checked for the same stale default.

## 6. Non-goals

- No production code changes except the S9 loader fix (landed), the S3 item 3 wiring,
  the S8 item 2
  refactor (validation extraction and `pub` visibility, no behaviour
  change), and fixes for defects found, each separately recorded.
- No coverage-percentage tooling or CI coverage thresholds.
- No reopening of `mojo-bench` or `make integration` as standing legs.
- No new qa leg for `rust-fuzz` (S8 item 5).
- No nightly toolchain, no cargo-fuzz install and no sanitizer builds
  while S8 is parked (operator, 2026-11-24).

## 7. End-of-arc triage — measure, then decide

Operator direction 2026-11-24: three filed non-goals are not refused,
they are deferred. Each gets one measurement at the end of the arc; a
test is written only where the measurement says it matters.

| # | Item | Measurement | Decision |
|---|---|---|---|
| T1 | A missing or stale `graph_kernels` extension makes its Python tests skip silently in qa (the `SCRIPT_KNN_STRICT` pin covers `cosine_knn` only) — found while correcting Gap 2 | count the tests that skip with the `.so` absent vs present | a strict pin like `RUST_TEST_STRICT`, or accept |
| T2 | the three `embed_cache` binaries (`embed-cache-lookup.rs`, `sha_s2.rs`, `trial.rs`) | `ripwire --callers` + `rg` for anyone invoking them | test, or propose deleting the dead ones |
| T3 | bench and ops tools (`helpers/bench/*`, `perf_smoke`, `swap_prep_census`, `build_route_p4`) | production reachability of each | test the reachable ones, leave the rest |
| T4 | `Mojo/src/gpu/*` probes | is any on a built path (`make mojo-build`, `mojo-parity`) | test the built ones |
| T5 | `rust-fuzz` | wall time of one full time-boxed run after S8 — deferred with S8 | advisory slot, or stay manual |

All five measured 2026-11-24 (end of arc), before the final gate
quartet:

- **T1 — measured by run.** Blocking the import with the exact
  exception a missing `.so` raises (`ModuleNotFoundError` with
  `name=`) over the 7 test files that name `graph_kernels` (197
  tests, `-m "not live" -n 4`): present 196 passed / 1 skipped;
  absent 2 failed / 170 passed / 25 skipped — 24 tests skip because
  the extension is gone (14 `personalized_pagerank_vec`, 3 core-bfs,
  3 brandes, 2 ppr lanes, 1 extension-without-personalized_pagerank,
  1 `importorskip`), 1 skip is the Mojo gate, 170 pass on the Python
  fallback, and 2 fail by accident
  (`tests/test_hyper_trails.py::test_kernel_batch_matches_scipy_pool_payload`,
  `tests/test_hyper_incidence.py::TestLongestChains::test_capped_sampling_deterministic_and_labeled`).
  A stale `.so` (importable, one lane short) skips with nothing
  failing. Decision: strict pin, the `SCRIPT_KNN_STRICT` pattern —
  `tests/test_graph_kernels_parity.py` gained
  `test_every_exported_lane_is_present_under_qa` (under
  `GRAPH_KERNELS_STRICT=1` a missing/stale lane list FAILS with
  `make rust-install` in the message; without the env it skips) and
  `test_lane_list_matches_the_crate_and_a_stale_build_is_named` (the
  lane list is pinned against `wrap_pyfunction!` in
  `Rust/graph_kernels/src/lib.rs`); `tests/run_gate_report.py` sets
  `GRAPH_KERNELS_STRICT=1` for qa only, pinned in
  `tests/test_run_gate_report.py`. Can-fail: blocked import + STRICT →
  1 failed naming all 7 lanes; blocked without STRICT → skipped;
  present → 18 passed. This hardens qa on purpose — operator veto
  window before the gate arc.
- **T2 — measured (rg over the live tree): none of the three binaries
  has a production caller.** `FINDATA_RUST_EMBED_CACHE_LOOKUP`: 0
  references in helpers/ and tests/ — the Python caller the docs cite
  (`doc/local/engineering/rust_database.md:219` →
  `helpers/core/embed_cache.py:94-116`) no longer exists
  (embed_cache "stays backup-only", Makefile:542); `sha_s2` and
  `trial` are manual harnesses (README + rust_database.md only).
  Decision: no test written — they do not carry production. Propose
  deleting `sha_s2.rs` + `trial.rs` and deciding on
  `embed-cache-lookup.rs` (operator's call); the lib surface
  (`decode_embedding`/`lookup_batch`) is what S2 pins.
- **T3 — measured.** Reachable from production paths:
  `helpers/bench/db_engine_write_bench.py` (the S6 perf leg,
  `tests/run_perf_benchmarks.py:549`), `helpers/bench/hyper_predict_bank.py`
  (Makefile:744), `helpers/maintenance/perf_smoke.py` (advisory report
  step, `tests/run_gate_report.py:460`), `helpers/misc/swap_prep_census.py`
  (advisory report step, `:466`). Not reachable: `build_route_p4`
  (comments + a static-checks mention only) and 15 of the 17
  `helpers/bench/*.py` (lint allowlists, doc mentions, comments).
  Decision: no new unit pins written. OPEN: `perf_smoke` and
  `swap_prep_census` always exit 0 (advisory), so an internal
  regression is invisible to the report — follow-on candidate if
  either grows a budget.
- **T4 — measured: all 7 gpu `.mojo` are on a built path.**
  `Makefile.mojo:18-24` builds every `Mojo/src/*/*.mojo` flat into
  `Mojo/bin` (all 7 binaries exist); zero test/parity/helper
  references outside the build. They are Level Zero hardware probes
  (need a device + adjacent `.spv`). Decision: accept — the build leg
  is their only gate; no runtime test written.
- **T5 — deferred with S8** (unchanged).

Arc finding (2026-11-24, close-out — acceptance 11): `rebuild_script_search`'s
`.duckdb` lane DIED with `ERROR: Connection Error: Connection already
closed!` (`make search-fresh` apply rc=2, twice, the second time with
nothing else running). Root cause: the mojo/ts decl cache writes used
`with conn:`; on the RAW `duckdb.connect` sidecar conn
(`connect_script_db`) that CLOSES the connection on exit (duckdb 2.0.1,
probed in isolation — even a fresh `duckdb.connect` to the same file
then fails; the `_DuckDBConn` proxy at `helpers/core/db_engine.py:238`
restores sqlite3 semantics only for funnel conns). Every later
embed-cache lookup silently missed (the `except Exception: pass` in
`__call__`), ~919 rows re-embedded uncached, every cache store failed,
and the rebuild's final write raised. The latent bug surfaced only when
a NEW Mojo unit took the INSERT path — S7's two new Mojo test files
were the first. Fix: a `_doc_cache_write` helper (statement + explicit
commit, engine-agnostic) at `helpers/maintenance/rebuild_script_search.py`
for both call sites; pins `TestDocCacheWriteKeepsConnOpen` in
`tests/test_rebuild_script_search_mojo.py` (2 tests; can-fail in memory
by re-closing the conn after commit — both pins fail). The sweep for
other `with conn:`-on-raw-duckdb sites: none left on this lane (the
full/incremental writers at `_write_full`/`_write_incremental` are the
legacy sqlite lane); the other rebuild sidecars are sqlite (safe).

## Appendix A — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 11-24 | `master_query.py "test coverage fuzz perf integration gaps"` | 4 doc hits, prior art = #463 | baseline proposal found |
| 11-24 | `git log --since=2026-11-01 --oneline \| wc -l` | 146 commits | sweep window |
| 11-24 | name/content matrix, 183 changed helper modules | script kept in session scratch | per-module unit/fuzz/integ/perf/prod-ref counts |
| 11-24 | `git log --since=2026-11-01 --diff-filter=A -- tests` | 82 files: 5 fuzz, 2 integration, rest unit | all 7 cross-cutting files are the 11-01 sweep's |
| 11-24 | `git log --since=2026-11-01 --diff-filter=A -- helpers` | 48 modules; 36 with an importing test | Gap 4 |
| 11-24 | `grep -c '#\[test\]'` over Rust sources | graph_kernels 12 (+9 proptest), cosine_knn 7 (+8 proptest), embed_cache 1 | Gap 2 |
| 11-24 | `rg -w <pyfunction> tests helpers` ×4 | `personalized_pagerank_batch`: 0 test refs, 1 importer | Gap 2; superseded same day — see the next row |
| 11-24 | `rg -n -w <fn> tests helpers`, then the importing wrapper's name | `graph_kernels` has 7 `#[pyfunction]`s; batch lane reached via `personalized_pagerank_scores_multi` (`test_scipy_bridge.py:1079`, one 4-leaf star) | Gap 2 corrected |
| 11-24 | `ls Mojo/tests`, `wc -l` | 5 files, 632 lines, last change 2026-11-13 | Gap 3 |
| 11-24 | `rg -w <module> tests/*.py Makefile` for maint_timing, algorithm_wrappers, build_route_p4, three HGX lanes | 0 / 0 direct / comments only / 0 each | Gap 4, corrections |
| 11-24 | `BENCHMARKS` parse, `tests/run_perf_benchmarks.py:192` | 35 legs | Gap 7 |
| 11-24 | `gate_query recent --gate qa --last 5` | 5 FAIL runs (runs 4621–4749) | not this arc; noted |
| 11-24 | `sed -n 170,195p doc/procedures/gates.md` | integration dropped 11-09; `mojo-test` re-added to advisory 11-21 | corrections |
| 11-24 | blocked-import run over the 7 `graph_kernels` test files (197 tests, `-m "not live" -n 4`) | present 196 ✓ / 1 skip; absent 2 fail / 170 ✓ / 25 skip (24 extension skips) | §7 T1 |
| 11-24 | `pytest tests/test_graph_kernels_parity.py tests/test_run_gate_report.py -q` | 46 passed 4.89s; blocked + `GRAPH_KERNELS_STRICT=1` → 1 failed naming all 7 lanes; blocked without STRICT → skipped | §7 T1 pin can-fail |
| 11-24 | `cargo test -p embed_cache`; `make rust-test` | 11 passed (1 pre-existing + 10 new); workspace rc 0 (cosine_knn 7+8, embed_cache 11, graph_kernels 12+9); fmt + clippy clean | S2 Rust half |
| 11-24 | `pytest tests/test_maint_timing.py tests/test_algorithm_wrappers.py -q` | 18 passed; with `tests/test_hyper_core_mass.py` the batch is 22 passed | S3 |
| 11-24 | S3 can-fail: 25 in-memory source mutations (pytest meta-path loader) | ALL KILLED after 2 test gaps found and fixed; stray-importer probe fails the AST pin | S3 acceptance |
| 11-24 | S2 can-fail: spsolve oracle scaled ×(1+1e-6) in memory | `test_batch_matches_spsolve_per_seed` fails (1 failed / 17 passed) | acceptance 2 |
| 11-24 | S5 exact command (4 chain files, `-q`) | 12 passed in 1.92s | acceptance 5 |
| 11-24 | `make fuzz` | 289 passed, 1 skipped (Mojo gate), 48.21s, rc 0 | acceptance 4 |
| 11-24 | S6 acceptance: 3 runs per new leg through the harness recipe (`run_one` semantics, fresh scratch, load 1.6–1.9) | db_engine_10k 67.42 / 68.25 / 68.64s < 150.0; hyper_trails 0.94–0.98s < 2.0; context_pack 1.21–1.29s < 2.6 | acceptance 6; budgets = baseline ×2 recorded in the S6 LANDED block |
| 11-24 | `gate_query timing --leg rust-test\|mojo-test --last 8` | rust-test 15.42 / 16.19 / 16.19s ✓ and mojo-test 12.41 / 12.79 / 10.80s ✓ on qa runs 4755 / 4768 / 4776 (the last 3 consecutive qa runs) | acceptance 1 |
| 11-24 | script-search rebuild root-cause repro + pins | 17 passed (`test_rebuild_script_search_{mojo,ts}.py`, 2 new pins); in-memory can-fail (close-after-commit) fails both | arc finding: `with conn:` closes a raw duckdb conn |

## Appendix B — S1 execution log (2026-11-24)

Box: 4 cores, load 0.7–1.7, no gate running (`outputs/.gate_active`
absent). Baseline tree for the before/after rows is `ddb28c08d` (the
tree S1 was authored on; the top patch was empty at the time).

| # | Command | Result | What it shows |
|---|---|---|---|
| B1 | `make rust-test` ×3, then ×3 again | 10.45 / 8.67 / 8.66 s and 8.87 / 9.12 / 9.42 s, all rc 0 | warm cost ~9 s; 37 tests pass (cosine_knn 7 + 8 proptest, embed_cache 1, graph_kernels 12 + 9 proptest) |
| B2 | `make -f Makefile.mojo mojo-test` ×3, then ×3 again | 7.73 / 7.44 / 7.98 s and 7.33 / 7.45 / 7.53 s, all rc 0 | warm cost ~7.5 s; 5 files, 36 tests (8+4+7+9+8) |
| B3 | `cargo test --workspace` with an empty `CARGO_TARGET_DIR` | rc 0, wall 67.39 s; ``Finished `test` profile [unoptimized + debuginfo] target(s) in 58.64s``; 169 crates compiled | one-off cost after a toolchain or lockfile change |
| B4 | `make -f Makefile.mojo mojo-test MOJO_TESTS=` before the guard | rc 0, `✓ Mojo tests passed (0 files)` | the silent-pass path |
| B5 | same after the guard | rc 2, `FAIL mojo-test: no Mojo/tests/*.mojo found — an empty roster is not a green lane` | closed |
| B6 | `make -f Makefile.mojo mojo-test MOJO=/nonexistent/mojo` | rc 2, `/bin/sh: 3: /nonexistent/mojo: not found` | a missing `mojo` already fails the leg |
| B7 | a scratch `.mojo` test with one failing `assert_true` | `mojo run` exit 1, `1 passed , 1 failed` | a failing Mojo test propagates through `mojo run` |
| B8 | `env -u RUST_TEST_STRICT HOME=<empty dir> PATH=/usr/bin:/bin make rust-test` | rc 0, `SKIP rust-test: cargo not installed` | a direct run keeps the skip |
| B9 | same with `RUST_TEST_STRICT=1` | rc 2, `FAIL rust-test: cargo not installed (RUST_TEST_STRICT=1 — the qa gate requires the Rust toolchain)` | the qa pin fails |
| B10 | the new tests run against the baseline runner, `Makefile` and `Makefile.mojo` (scratch driver, `pytest.main`) | `4 failed, 1 passed`: `KeyError: 'rust-test'` (`test_run_gate_report.py:444`), `KeyError: 'RUST_TEST_STRICT'` (`:485`), `AssertionError: rc=0` + `SKIP rust-test: cargo not installed` (`:559`), `AssertionError: rc=0` + `✓ Mojo tests passed (0 files)` (`:568`) | can-fail proof for the four new-behaviour pins; the fifth pins the pre-existing skip and passes on both trees |
| B11 | `.venv/bin/python3 -m pytest tests/test_run_gate_report.py tests/test_static_checks.py -q` | 146 passed, 5 skipped (`live DB not present`) | targeted validation, working tree |
| B12 | `gate_query timing`, 12 advisory runs in a 4-job pool | `rust-test` median 26.65 s (min 15.80, max 307.83); `mojo-test` median 17.33 s (min 10.18, max 33.15) | under pool contention the legs cost 2–3× their standalone time |
| B13 | replay of the last 12 qa runs through the 4-job FIFO pool (`outputs/gate_runs.duckdb`, read-only), warm 9.0 s / 7.5 s and cold 67.39 s / 7.5 s | warm delta +0.0 s on 11 runs, +2.7 s on run 4749 (`mojo-parity` 0.7 s); cold +54.2 to +56.8 s on the 4 runs where `mojo-parity` finished in under 15 s, +0.0 s on the other 8; the baseline roster reproduces every recorded wall to 0.1 s | expected wall-time impact at standalone leg times |
| B14 | same replay at the B12 medians (26.65 s / 17.33 s), then with `rust-test` at its recorded max (307.83 s) | contended delta +0.0 s on the 8 runs with `mojo-parity` at 71–92 s, +13.5 to +16.1 s on the 4 where it fast-failed; the 307.83 s variant would add +216 to +297 s on every run | impact under pool contention, and the cold-build tail |
| B15 | First qa run with both legs in the roster (run 4755, 2026-11-24 10:54:30, baseline of the gate arc) | gate_query latest --gate qa: `rust-test 15.42 ✓ OK`, `mojo-test 12.41 ✓ OK`; qa wall 940.3 s | Both legs ran and passed inside the blocking gate (acceptance 1, run 1 of 3). The box was contended (pytest 918 s against a ~333 s median), so these are contended times, not the quiet-box medians in B5. Five other legs failed on one cause unrelated to S1 — see B16. |
| B16 | Pre-existing red found by the same baseline: five qa legs lock-collide on `memory/research.duckdb` | artifacts `outputs/.artifacts/qa/20261124T105430-984524/qa.{schema-check,group-growth-check,family-label-coverage,mojo-parity,snapshot_check}.log.zst`: "Could not set lock on file ... research.duckdb: Conflicting lock is held in ... python3.14 (PID 199096)"; `lockprobe.py integrity_check` before the fix: `probes ok=1 blocked=47`; after: `ok=44 blocked=0` | `DatabaseIntegrityChecker.get_connection` (`helpers/misc/database_integrity_check.py`) opened the store WRITABLE for its whole run, and a writable DuckDB handle is an exclusive process lock. The checker only SELECTs, so it now opens `read_only=True`. Same defect, same run, in advisory: `helpers/graph/suggest_relations.py` `existing_edge_pairs()` — `connect()` → `connect(read_only=True)`. Pins: `tests/test_database_integrity_check.py::TestConnectionIsReadOnly` (2), `tests/test_suggest_relations.py::TestExistingEdgePairsReadOnly` (1); both shown failing on the pre-fix code. |
| B17 | Pre-existing red found by the same baseline: `tests/test_integration_maint_chain.py::test_chain_idempotence` depends on the LIVE repo HEAD | qa run 4755 and the targeted rerun (11:26, 305 s) both: `REFUSED: stale apply — ... has DIVERGED from it` then `assert 1 == 0` at `test_integration_maint_chain.py:846`; after the fix the targeted rerun passed (442 s) | The hermetic project has no git history, but the fresh-trample guard (`helpers/maintenance/rebuild_common.py` `stale_apply_reason`) orders each index's stamped HEAD against the live HEAD, so an `stg refresh` during the run turned the next index step into a refusal (4755: `research.db`, rerun: `doc_search.db`). `_make_dispatcher` now pins `git_state.head_sha` to `None` — the guard's own "cannot tell" proceed path. The other two pytest reds (`test_fuzz_regex` suffix linearity 2.80x > 2.6, `test_integration_ts_contract` suggestions `assert 500 == 200`) passed on targeted rerun with no code change: load-induced. |
| B18 | Perf ran forced on the contended box (run 4760, 2026-11-24 12:01:30, `--force`, wall 155.8 s, exit 1) | gate_query latest --gate perf: 32/35 within budget; FAIL sync_tags 28.49 s / 2.0 s, graph_l1_centrality 5.27 s (psi avg10 20.6%), doc_query 12.39 s / 3.0 s; FORCED block appended to outputs/perf_report.md | doc_query (history 0.31-0.37 s) and graph_l1_centrality are single-run, load-attributed — parked. sync_tags is a persistent regression, not load: 0.68 s green on 11-21, then 151.2 / 87.1 / 29.4 / 28.5 s in every run since 11-22 morning; the leg is `helpers/core/sync_tags.py --apply` and the research store's DuckDB 2.x writer wave landed in that window — needs its own arc. Found by the same run: the console footer printed `35/35 passed` against three OVER_BUDGET rows and exit 1 — tests/run_perf_benchmarks.py:816 unpacked the 6-tuple as `*_, ok, _`, binding `budget` (truthy float), not `ok`. Fixed to named unpacking; pinned by tests/test_run_perf_benchmarks.py::test_console_footer_counts_legs_not_budgets (fails on the old unpack, passes after). |
| B19 | qa run 2 of the gate arc (2026-11-24 12:12:51, 16/17) closed every lock red except `mojo-parity` 0.44 s — and that one is the SAME collision, mirrored | artifacts `outputs/.artifacts/qa/20261124T121251-507584/qa.mojo-parity.log.zst` + `/mnt/data/tmp/mojo_parity.log`: "IO Error: Could not set lock on file .../research.duckdb: Conflicting lock is held" | Mojo/src/common/integrity_check.mojo:300 opened the research store with default WRITABLE `dbmod.connect` while the sweep only SELECTs (its own POLICY header says "duckdb read-only", and the graph.duckdb open at :1530 already used `duckdb_lock.open_read_only`). Fixed to `connect("memory/research.db", read_only=True)` (sibling resolution is db_engine's :308). Evidence: writable open under a read-only holder fails with the exact qa log line; `read_only=True` under the same holder opens; rebuilt binary's `make mojo-parity` under a live read-only holder → rc 0, "parity: 92 / 92 keys match", 72.4 s (the normal green time). qa run 2 also validated the B16 fixes: pytest 6656 passed / 0 failed (all three baseline pytest reds gone), schema-check / group-growth-check / family-label-coverage / snapshot_check all ✓; rust-test 16.19 ✓ + mojo-test 12.79 ✓ = acceptance 1, run 2 of 3. |

Reading of B13 against B14: the standalone replay is the quiet-box
case, B12 says the legs cost 2–3× that inside a busy pool, and B14 is
that case. Both put the delta at +0.0 s whenever `mojo-parity` is
green; the growth lands on runs where `mojo-parity` already fast-failed
and the gate is red anyway. The one case the shadow does not bound is a
cold cargo build under contention; it happens once per toolchain or
lockfile change. The first three real qa runs decide (acceptance 1).

Finding recorded, not acted on: `run_step` never sets
`Result.skipped`, so every rc-0 SKIP in the roster (md-lint without
Node, `cargo-audit` without the binary, and until S1 `rust-test`
without cargo) is reported as "✓ OK". Changing that touches the
report semantics of every leg and belongs in its own proposal.

## Appendix C — HGX consumer map (2026-11-24)

Measured on the snapshot parquet (`snapshots/parquet/sqlite/`, 2026-11-23)
with the lanes' own `_exclude_degenerate`; the bounded rerun script is
`covgap/s3/seats3.py`, logs under `$TMPDIR/opencode/covgap/s3/`.

| Family | Nodes / hyperedges | HySC vs HypergraphMT | Reading |
|---|---|---|---|
| sector, theme, industry | 1,228 / 152 | identical partitions at K=4 and K=8 | ARI vs incumbent `hypermmsbm_community` 0.002 / 0.009; one block holds 922–1,040 of 1,228 |
| edition | 776 / 265 | identical (ARI 1.000) at K=4 and K=8 | K=4 sizes 753/16/4/3 |
| event | 1,175 / 1,123 | ARI 0.4033 (K=4), 0.4197 (K=8) | HySC 1,119/25/24/7 vs HgMT 996/107/62/10; memberships still one-hot (min 0.51) |

- Every fit returns one giant block plus small ones; memberships are
  one-hot everywhere. No family gets a mixed-membership signal.
- Cost (same input): HySC 0.3–4.0 s, HypergraphMT 2.1–12.3 s.
- core-periphery (seed 42/7/2024): rank correlation 0.9933 / 0.9940
  across seeds, ~25 s per fit. Against a SQL aggregate on the same 152
  hyperedges: Spearman +0.921 for `inverse_size_mass`, +0.488 for the
  plain hyperedge count, -0.600 for mean hyperedge size, -0.328 for
  max size. By hyperedge count: 1 → mean 0.27, 2–3 → 0.66, 4–6 → 0.85.
- Family sizes that make a dense fit unsafe: `group` 13,211 nodes,
  `subsidiary` 8,522, `supply` 6,241 (1.4 / 0.6 / 0.3 GB per node-by-node
  matrix). Not fitted.
- What replaces HySC (operator question 2026-11-24) — pairwise
  co-membership on the same live tables (hyperedges of ≤200 members):

  | Family | Nodes | Pairs sharing ≥1 hyperedge | ≥2 | ≥3 | Max shared | Nodes with a ≥2 partner |
  |---|---|---|---|---|---|---|
  | sector, theme, industry, sub_sector | 1,229 | 41,471 | 10,615 | 5,494 | 6 | 926 |
  | edition | 778 | 11,384 | 9,203 | 5,714 | 11 | 673 |
  | event | 1,175 | 1,053 | 88 | 13 | 3 | 63 |

  `edition` is the one family with a graded, dense overlap structure;
  `event` is nearly disjoint (93% of its nodes never share two
  hyperedges with anyone), which is why every seat returns one giant
  block there. Landed as `hyper_arrow.shared_group_pairs_arrow`
  (bounded: edges of ≤200 members by default, `min_shared` floor;
  `tests/test_shared_group_pairs.py`, 6 pins): live partition family →
  10,615 pairs at ≥2 shared in 0.41 s (mass p50 0.074, p90 0.186, max
  1.038), `edition` → 9,203 pairs in 0.08 s. Not checked: whether a
  route already exposes shared-group pairs.
- Live store: `memory/research.duckdb` `graph_analytics` holds only
  `hypermmsbm_community` (1,102 rows) of the four community metrics; no
  rows ever existed for `hysc_community`, `hypergraphmt_community` or
  `core_periphery`.

### Why not HySC — recorded for future reference

If the HGX lanes are ever revisited, these are the measured reasons
`hyper_hysc` stays unwired (all from this appendix's tables):

- One giant block on every family fitted: 75–95% of nodes land in a
  single block; the rest come back as fragments of 3–25 nodes.
- Memberships are one-hot everywhere (min 0.51 on `event`): the
  mixed-membership premise never fires on this data, so there is no
  soft signal to threshold or serve.
- No added value over the incumbent: agreement with the stored
  `hypermmsbm_community` is ARI 0.002–0.009, so wiring HySC would add
  no block the incumbent lacks. Against `hyper_hypergraphmt` (deleted)
  it was identical wherever structure exists (ARI 1.000).
- `event` cannot be clustered at all: 93% of its nodes never share two
  hyperedges with anyone. Both seats returned one giant block because
  of the data, not the method.
- Dense fits are O(N²) in memory and the unsafe families are the big
  ones (`group` 13,211 nodes ≈ 1.4 GB per matrix). Any revisit sizes
  the family from the parquet first and caps nodes (the seats2 swap
  incident happened exactly here).
- ARI caveat when re-judging: with one block holding 75–95% of the
  nodes, ARI is dominated by that block — the "1.000" between HySC and
  HypergraphMT meant "both put everyone in one block", not "the
  fragments agree". Read block sizes alongside ARI, and keep ARI as
  the acceptance judge for any future lane (plus NMI if soft
  memberships ever appear), never as a served output.

Use instead, per question:

- per-entity group strength → `hyper_arrow.core_mass_arrow` (S10,
  landed; Spearman +0.921 against the HGX coreness sampler);
- pairwise closeness → `hyper_arrow.shared_group_pairs_arrow` (landed,
  table above; graded, bounded, sub-second, no fit);
- a hard community label → the incumbent `hypermmsbm_community` (K=8,
  stored), or `bipartite_louvain_community` (exists, not benchmarked
  against HySC);
- any new lane is judged by ARI against the incumbent before wiring,
  and needs the eval-gate bullet first if it touches a route.

## Perf follow-on: graph connect() warm path (2026-11-24)

Advisory deep dive (run 4777) flagged `test_refresh_resets_connection`
at 186.9 s — the single largest live-invariants cost. Diagnosis and
result:

- Isolation: the test completes in ~3.1 s (cProfile-instrumented run
  2026-11-24) and sequential live requests are ~0.5 s each (peers ×5:
  0.78/0.48/0.49/0.49/0.51 s). The 186.9 s figure is therefore a
  PARALLEL artifact (xdist + quack-wire served `memory/graph.duckdb`):
  every connect() is wire-ATTACHed and every graph-algos/rebuild test
  in other workers queues behind the exclusive `build.lock` flock, so
  connect churn costs multiply under contention.
- Change: `_build_graph_connection` now performs ONE authoritative
  under-lock warmth probe (`warm = exists and _is_warm(...)`), used
  for both the stale-unlink gate and `needs_build`; previously the
  build path probed `_is_warm` twice (plus `_stale_not_cold`
  separately). The F1 under-lock re-check semantic (test
  `test_read_only_downgrade_under_lock`) is preserved — connect()'s
  outer check is still NOT trusted once the build flock is held.
  Redundant probe removal matters most under contention: fewer wire
  opens per request → less time queued behind a builder's LOCK_EX.
- Validation: `tests/test_api_graph_live.py` 30 passed, `tests/test_graph_disk.py`
  + `tests/test_graph_stats.py` + `tests/test_integration_maint_chain.py`
  95 passed / 1 skipped, ruff+format clean. Untouched: `_stale_not_cold`
  raising `GraphCacheStaleError` when `GRAPH_STALE_REBUILD != inline`.
- Fixed: (c) locking protocol — warm read-only opens now NEVER take
  `<cache>.build.lock`; `connect()` verifies warmth outside the flock
  and opens directly (`_open_read_only_connection`), with the
  transient-lock ladder covering a builder that starts mid-open. Proof:
  `tests/test_graph_disk.py::TestDiskBasics::test_warm_read_only_open_takes_no_build_lock`
  holds the build flock and asserts the RO connect completes <5s.
- Fixed: served-store clients (quack wire) never take the build flock
  at all — the server coordinates writers, and a local build against a
  served store is refused by the split-brain guard, so there is no
  legitimate contender (`_build_graph_connection` now branches to a
  lock-free `_open_or_build` for served stores). Proof:
  `tests/test_graph_disk.py::TestStaleUnlinkServedGuard::test_served_open_takes_no_build_lock`
  holds the flock and asserts the served open completes <5s.
  (Why it was left over: the flock predates serving; the served branch
  was added later inside the same locked wrapper, and since file-mode
  builds of served stores were already refused, it was dead weight —
  only visible once parallel wire-readers started queueing on it.)
- Still open: (a) gate orchestration — split live-invariants so
  graph-writers (graph_disk) and graph-readers never share an xdist
  window; (b) per-request wire-connection reuse within one worker under
  a read-only lease (touches the CONC-1 per-request-connection
  contract, so a design change).

## Perf follow-on: graph_disk/fuzz shared-fixture setup (2026-11-24)

Profile of the once-per-run fixture build (xdist-shared via
`_shared_template_locked`):

- `restore_sqlite_from_parquet` (legacy donor, 604,622 rows / 30
  tables): 16.3s — `executemany` INSERTs 9.9s, pyarrow `to_pylist`
  3.3s, sqlite datetime adapters 2.6s, DeprecationWarnings (549K:
  525,202 datetime + 23,963 date, Python 3.12+ deprecated adapters)
  1.4s, commit 0.7s.
- `build_trimmed_template` on top: backup 1.0s + trim DELETEs 4.8s +
  VACUUM 1.9s (~7.7s).
- `build_schema_template` reused the same donor (backup + wipe +
  VACUUM ~3s) — but paid the full restore when it was the only
  consumer (fuzz-only runs).

Fixed:

- `helpers/maintenance/snapshot_db.py`: idempotent re-registration of
  the conforming datetime/date adapters (`d.isoformat(sep=" ")` /
  `d.isoformat()`) — identical output, zero warnings: restore 16.3s →
  13.6s, suite warning budget relieved.
- `tests/_tmp_hygiene.py`: `build_schema_template` fast path
  (`_build_schema_template_from_ddl`) — construct straight from the
  tracked `_schema.sqlite.sql` + `db_meta.parquet` (3 rows) instead of
  restoring the full-corpus donor and deleting every row. Parity
  verified against the donor route: same table/trigger surface
  (6 triggers post-drop), same `db_meta` rows, FTS5 empty. 1.8s vs
  ~16s restore when no other fixture needs the donor. Consumers
  validated: fuzz_shortest_path, snapshot_cycle, derive_events_cli,
  extract_relations_cli, rebuild_schema, maint_chain, near_duplicates,
  test_graph, note_writers — 156 passed.

Still open: the legacy-donor restore (~16s) remains for the trimmed and
full templates (they need data). Candidate follow-on: persist the three
shared templates across runs keyed by a parquet-dir fingerprint
(size/mtime of the 30 parquet + DDL) under a gitignored cache dir —
removes the per-run setup tax entirely after the first run; not
implemented here (semantic change to the once-per-run freshness
contract).

## Perf follow-on: scipy lane derive fuse (2026-11-24)

Advisory live-invariants #3 (`test_dispatch_live_closeness_serving_stack_invariants`,
~32.5s under gate 4777) was attributed to "serving-stack scans" —
profiling showed the real cost is NOT the DB read: `load_projection`
+ `edge_rows` ≈ 0.6s of ~13s per lane run. The cost is the multi-source
dijkstra + `fused_derive` over the 22.8k-node live projection, run
twice per test (determinism assertion).

Fixed: `helpers/graph/scipy_bridge.py` — `fused_derive` reworked to a
single-buffer implementation. Old builds `F=isfinite(D)` +
`M=where(F,D,0)` + two nested `where` temporaries for harmonic (~5× the
D-sized buffers, GB scale); new keeps the same `M` reduction (bit-
identical closeness, verified) and computes harmonic through one reused
`inv` buffer. ~1.7× faster at live size. Verified offline: 54 scipy
bridge tests + live tests — closeness test 32.5s gate → 19.4s isolated
(was 25.4s cProfile before the change), betweenness twin 12.8s (was
~18.1s gate). No API change; `jobs=1` deterministic default preserved
(the perf leg already forks with `--jobs`).

Still open: the dijkstra itself is the floor (~7–8s per run, inherent
C work over 1,731 sources × 22.8k nodes). Parallelizing the live-serve
lane with `jobs>1` would cut it ~3–4× (fork_map preserves order, so
determinism holds) — deliberately NOT changed here because the lane's
`jobs=1` single-process default is doctrine; a follow-on could add a
gate-side `--jobs`/env override.

## Perf follow-on: static-checks DuckDB user_version (2026-11-24)

`make static-checks` broke after the research-store cutover: `check_db_meta_generation`
(helpers/validators/static_checks.py) ran raw `PRAGMA user_version` on a
store that now opens as DuckDB — `Catalog Error: Pragma Function with name
user_version does not exist`. Fixed by using the engine-aware
`helpers.core.db.get_user_version()` (SQLite → PRAGMA, DuckDB →
`db_meta.schema_version`; both expected 17), matching the integrity
checker's `db_engine.schema_version()` use. static-checks green again.

## Perf follow-on: harvest check dry-run (2026-11-24)

`test_check_path_populates_the_field` (and siblings) called
`harvest(check=True)` with no `lanes`, which dry-runs ALL five lanes
to build the pending-parts table even though the asserts only read
`stale_*` fields. The dry-run is the entire cost (~102s cProfile:
`_harvest_opencode` 55s + `_harvest_dsh` 36s + others). Fixed:
- `harvest_conversations._format_new_table`: `w` guard default 7 so
  an empty lane set renders (was `max()` over empty rows → ValueError).
- tests pass `lanes=()` on the three check-path calls (the lane set is
  a documented test-only knob; production callers still pass nothing =
  all five lanes).

Measured: tests/test_harvest_retention.py 2.1s total (was ~100s+ per
check-path test). Regression source: e0b3b99766 (2026-11-11, per-harness
new-parts tally).

## Perf follow-on: fuzz schema template (2026-11-24)

Verified the `build_schema_template` fast path (above) where it matters:
tests/test_fuzz_shortest_path.py is now 7.4s wall / 277MB peak (was
~24s setup + tests). No further change needed; the legacy-donor restore
still serves trimmed/full templates (cross-run cache candidate recorded
above).

[EOF]

## Live legs keyed to the retired store path (2026-11-24)

Found while recording P4 of `sync_tags_duckdb_setbased.md`. Several legs
ask "is this the live box?" by testing whether `memory/research.db`
exists. The 2026-11-21 DuckDB cutover deleted that file; the store is its
sibling `memory/research.duckdb`. The house door
(`helpers.core.db.connect`) redirects `.db` to `.duckdb` whether or not
the `.db` exists, so every loader kept working — only the existence
*gates* broke, and a gate breaks by skipping.

Evidence, qa 4776 and advisory 4777 junit (2026-11-24; the store was
present on the box throughout):

| leg | what the gate did | how it reads in the report |
|---|---|---|
| `tests/test_a2_candidates.py::test_a2_live`, `tests/test_l1_mst.py::test_live_replay`, `tests/test_scipy_matching.py::test_s3_sector_live`, `tests/test_no_confidence_level.py`, `tests/test_answerability_probe.py` | skipped ("live DB missing" / "absent" / "not available") | five skip lines, unmarked, inside the qa collection |
| `tests/test_graph.py` — 85 test functions | module-level skip at collection | one "collection skipped"; no per-test line |
| `tests/test_db_maint_duckdb.py` — the six DuckDB-backup tests | module-level skip at collection | one "collection skipped" |
| `tests/test_derive_co_mentions.py` — four live tests | skipped | live leg |
| perf legs `l1_kernels`, `ppr_edge_attribution` | printed `SKIP … (exit 0)` | scored OK |
| `tests/test_tier_tripwire_advisory.py` note-count axis | returned `None` ("absent") | advisory line |

The two perf legs had been vacuous since the cutover:
`gate_query timing --leg <leg> --last 14` shows `l1_kernels` at
0.19–0.27 s scored OK for six runs from 2026-11-22 08:46 to 2026-11-24
12:01 (67.58–78.76 s on 2026-11-21), and `ppr_edge_attribution` at
0.37–0.51 s scored OK for six runs (5.54–7.56 s before). Forced perf run
4760, reported above as "32/35 within budget", counted both. `db_maint`'s
DuckDB backup is the store's only backup leg and its tests were the
module that had been skipped whole.

Why nobody saw it: a zero-byte `memory/research.db` appeared in the tree
at 17:19 that day (creator not attributed; removed by the operator), and
while it existed every `.exists()` gate passed again — the ported tests
ran green at 17:27, which is how the class was first noticed.

Fix — one resolver, and a ratchet on the spelling:

- `tests/_live_store.py`: `live_store_path()` asks the house door
  (`ResearchEngine.from_env().resolve_path(None)`, so the engine selector
  and `FINDATA_RESEARCH_DB_PATH` are honoured) and returns the store only
  when it is a file; `require_live_store()` skips otherwise.
- Eleven files gate through it now: the five qa tests above, the four
  derive_co_mentions tests, the tier tripwire, `test_graph.py`,
  `test_db_maint_duckdb.py`, and both bench scripts.
  `tests/test_graph_disk.py::TestProductionPath` also *asserted* that the
  retired file exists — stale since the cutover and masked by the
  served-store skip; it asserts the resolved store now.
- `tests/test_live_store_gate.py` pins the resolver against a fake repo
  (stray legacy file present, legacy gone, rollback engine, no store,
  env override, `require_live_store` skip) and ratchets the spelling: no
  test file may name the retired path outside an allowlist that says why
  (hermetic fixture roots; the five SQLite-dialect schema guards below).
- `pytest.ini`: the `live` marker text and the comment above it name the
  DuckDB store.

Validation (2026-11-24 20:10, store present, no `memory/research.db`):
the ten ported test modules run serial with
`-m "live or not live"` — **220 passed, 1 skipped in 272.93 s**. The one
skip is the designed served-store skip in
`TestProductionPath::test_default_connect_uses_memory_graph_duckdb`.
That run is the first since the cutover for the 85 `test_graph.py` tests
and the six `db_maint` DuckDB-backup tests; both pass unchanged. Both
bench legs run for real again: `tests/bench_l1_kernels.py` PASS in
73.5 s (betweenness numpy 54.89 s vs Rust 5.02 s, 10.93×; closeness
8.78 s vs 3.63 s, 2.42×) and `tests/bench_ppr_edge_attribution.py` PASS
in 6.6 s (harness wall 6.20 s). `tests/test_live_store_gate.py` 10
passed; ruff and `ruff format --check` clean on the fourteen touched
files; no file reappeared at the retired path. Serial, not xdist — the
gate's live leg still has to confirm it.

Still open:

- Five live schema guards in `tests/test_static_checks.py` (`LIVE_DB` at
  `:859`) open the store with `sqlite3.connect` and assert
  SQLite-dialect facts (`sqlite_master`, `EXPLAIN QUERY PLAN`, named
  secondary indexes). They need a DuckDB port, which is a decision about
  the canonical DuckDB schema — not a mechanical rewrite — and DuckDB has
  no named secondary indexes for the index guards to assert.
- The perf harness scores an exit-0 `SKIP` as OK: item 4 of
  `perf_followon_gate_reuse_cache.md`.
- `tests/bench_rebuild_scale.py` reads the SQLite source directly
  (`:65`); manual only, and obsolete since the cutover.
- The retired name is also six CLIs' `--db` default: P5 in
  `sync_tags_duckdb_setbased.md`.

## Gate-arc Phase 2 — qa baseline reds fixed (2026-11-25)

The 2026-11-24 21:52 baseline battery (`make qa && make advisory &&
make perf`) stopped at qa: 13/17 legs (8 pytest + lint + static_checks +
schema-check FAIL; advisory/perf never started). All reds fixed and
validated with TARGETED runs only, per gates.md:

- static_checks ×3 + `test_frontmatter_schema` corpus-clean: one cause —
  the perf_followon proposal was flipped `executed` while still live
  with a non-canonical frontmatter. Fixed by ARCHIVING it
  (completed.md #568, `archive/tooling/`).
- lint-gates ×4 (`ruff_python/format_footprint` +
  `ruff_sees_every_tracked_file` + mojo format): 6 ruff findings in the
  sweep's own S4/S5 files (4× F401 unused imports, 2× F841) + 6 files
  ruff-format drift + 1 Mojo file — fixed (`ruff --fix`/`format`,
  `mojo-format`); `tests/test_lint_gates.py` 5/5.
- `test_perf_writer_appends_run_chain`: the append-convention pin still
  called `write_report` with the pre-SKIP 6-tuple — updated to the
  7-tuple (follow-on item 4's shape).
- `test_qa_harness_pins_rust_test_strict`: the pin delenv'd only
  `RUST_TEST_STRICT`; under the real qa battery the pytest leg inherits
  `GRAPH_KERNELS_STRICT`/`SCRIPT_KNN_STRICT` from the harness process,
  so the advisory-first no-leak assert tripped on inherited env. The
  pin now strips all three before the advisory call.
- `test_schema_drift`: `schema/duckdb/corpus.sql` stale vs the live
  store — the corpus-cache rewrite's `mtime FLOAT` → `DOUBLE` DDL
  change; `make schema-dump` regenerated (1 file, drift check clean).

Validation: 59 targeted pytest across the touched files, lint gates
5/5, static-checks leg "All static checks passed", md-lint clean.
Advisory + perf completed the interrupted baseline after these fixes
(their numbers reflect the fixed tree); qa's red baseline stands as
measured, with these targeted greens as the Phase 2 record.

## Gate-arc Phase 4 — qa 4796 reds fixed (2026-11-25)

qa run 4796 (15/17): three reds, all fixed and targeted-validated —
(1) `helpers/core/parallelism.py` missing shebang (static_checks Helper
shebangs); (2) `doc/improvements/proposals/perf_harness_revamp.md`
frontmatter missing the `executed`/`completed_md` keys the corpus walker
requires (added as `null` while the proposal is open); (3)
`tests/test_get_tickers.py::TestVssRunIndex::test_parity_live_table_readonly`
— `helpers/core/get_tickers.py` `load_entities()` opened the live store in
writable config by default and its `except Exception` swallowed the
config-conflict error under the parallel gate into `0 entities`; flipped to
`connect(db_path, read_only=True)` (same reader doctrine as the
derive_co_mentions fix). Standalone rerun: 27,754 entities; both tests
pass; static_checks clean.
