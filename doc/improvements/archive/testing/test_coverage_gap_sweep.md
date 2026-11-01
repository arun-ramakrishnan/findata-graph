---
title: "Test coverage gap sweep — close the P1 unit gaps from the late-October feature wave, then fuzz/integration/perf slices"
status: executed
filed: "2026-11-01"
executed: "2026-11-01"
completed_md: "463"
area: "tests/ + helpers/maintenance/price_sync.py + helpers/research/price_join.py + helpers/graph/edge_projection.py + helpers/core/msgpackio.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Test coverage gap sweep — close the P1 unit gaps from the late-October feature wave, then fuzz/integration/perf slices

**Date:** 2026-11-01 · **Status:** EXECUTED (#463) ·
**Area:** `tests/` (new files), `helpers/maintenance/price_sync.py`,
`helpers/research/price_join.py`, `helpers/graph/edge_projection.py`,
`helpers/core/msgpackio.py`

## 1. Motivation

The 2026-10-20 → 11-01 feature wave touched **192 helper modules**
(`git log --since=2026-10-20 --name-only -- 'helpers/*.py'`) across
nine landed arcs: temporal query lane (as-of substrate, weekly OHLCV,
event studies), alpha lane (edge dating, leak-free sweep), unlock sweep
(matching Hall fix, group convergence), analytics integrity + hgx index
family, S4 tree/portfolio hyperedge families, msgpack transport + Struct
edge serialization, orjson cutover, chatter parse enhancements. The
operator asked (2026-11-01) whether core/fuzz/perf/integration suites
cover the new surface.

**Good news first:** every one of the seven newest feature commits
landed tests in-commit (`2ad4e405f`→test_gate_query/test_search_tui,
`7803fead2`→test_star_family_registry, `450423857`→test_date_edges,
`c943ec3a5`→test_as_of/test_event_study, `bf338a09d`→test_l1_mst/
test_scipy_matching, `200e6d8d9`→test_prune_analytics et al.,
`4c52fb920`→test_msgpackio). The wave is NOT test-starved at the
commit level.

The gaps are structural, not per-commit: three **production-wired
modules have zero direct tests**, one new **deserializer ships
happy-path-only**, and the three cross-cutting suites (fuzz /
integration / perf) predate the wave and have no legs for any of its
chains.

## 2. Evidence (measured 2026-11-01, this box)

Method: per-module coverage matrix over all 192 touched helper modules —
unit file match, then **content grep across `tests/*.py`** (name
matching alone fabricates coverage: `test_bili_*` covers
`bili_analytics`, `test_merger_hyperedges` covers
`derive_merger_hyperedges`), then `helpers/`+`app.py` importer grep to
separate production-wired gaps from research/bench code. Appendix A has
the raw command log.

### P1 — production-wired, zero or happy-path-only tests

| Module | Test refs | Production consumers | Risk |
|---|---|---|---|
| `helpers/maintenance/price_sync.py` | **0** | substrate for the whole temporal lane | OHLCV ingestion bugs = silent as-of garbage downstream (event studies, signal sweeps) |
| `helpers/research/price_join.py` (`entity_week_census`) | **0** | `helpers/graph/stats.py:531` (production stats path) | weekly-bar join correctness unverified |
| `helpers/graph/edge_projection.py` | **0 direct** (transitively via 7 consumers' tests) | `csr.py:55`, `l1_mst.py:31`, `l1_betweenness.py:63`, `scipy_bridge.py:84`, `suggest_relations.py:58`, `layout.py:43`, `graph_scale_watch.py:70` | the part_of/has_company retired-edge projection is the single shared semantic of every graph lane; tolerances unpinned |
| `helpers/core/msgpackio.py` | 3 happy-path roundtrips only (`tests/test_msgpackio.py:10-22`: dict/list/struct) | Rust embed-cache boundary (`embed-cache-lookup.rs`) | new deserializer, no malformed/truncated/depth/size input coverage |

### Cross-cutting suite gaps

- **Fuzz** (`make fuzz`, Hypothesis): 21 distinct helper surfaces
  targeted (`helpers.graph` ×5, `pdf.capture_newsletter_images` ×3,
  `core.parse_newsletter` ×3, `core.frontmatter` ×3, …). **None** of
  the wave's new serial/parse code: `msgpackio`, `jsonio` (orjson↔stdlib
  parity on edge inputs), `hyperedge_types` row validation, `date_edges`
  bound parsing. `as_of` has unit tests for window semantics
  (`tests/test_as_of.py:62-93`) but the alpha lane's leak doctrine is
  not an executable property anywhere.
- **Integration** (`make integration`): 15 files. No chain test for
  **price_sync → price_join → as_of → event_study** (the temporal lane
  end-to-end), none for **derive_hyperedges star families → HIF → hyper
  algorithms** (`derive_hyperedges` appears only via
  `test_integration_graph_algorithms`).
- **Perf** (`make perf`): 12 legs (integrity_check, verify_notes,
  sync_tags, static_checks, snapshot_check, graph_pagerank,
  route_graph_stats, shortest_path_bfs, derive_events,
  pdf_pipeline_local, derive_insights, cosine_knn_ratios). **No leg**
  for any wave hot path: `derive_hyperedges` (star families),
  `l1_mst`/`scipy_matching` (the Hall-fix lanes), hyper
  centralities/communities, or the as-of weekly census join.

### Verified-fine / deliberately out (do not scope-creep)

- `signal_sweep.py` — self-declared "Report-only — EXPLORATORY"
  (`helpers/research/signal_sweep.py:4`); `linkpred_precision.py`,
  `hyper_predict.py` (D10, consumed only by `helpers/bench/
  hyper_predict_bank.py`). Research lanes: untested by design until
  promoted.
- `quack_query`/`quack_health`/`quack_server_child` — zero direct tests,
  but the safety-critical split-brain guard lives in
  `duckdb_shared.assert_not_served`, unit-tested (`test_duckdb_shared.py`).
  CLI wrappers only.
- `security_scopes`/`route_skeleton` — indirectly covered via
  `static_checks` + `coverage_ledger` tests.
- One-off ops tools: `bili_thresh_replay`, `ocr_pin`,
  `seed_note_gemma_cache`, `mca_cin_sync` (sibling `mca_cin_resolve` IS
  tested) — low value.

## 3. Design

Four independently landable slices, P1 first. All tests hermetic
(synthetic fixtures, no live API/DB — `conftest.py` env redirects apply).

- **S1 — P1 unit gap closure** (first, unblocks everything else):
  1. `tests/test_price_sync.py` — synthetic OHLCV fixture: happy path,
     gap/missing-week handling, malformed rows, ticker-mapping misses,
     idempotent re-run.
  2. `tests/test_price_join.py` — `entity_week_census`: misaligned
     weeks, NaN/duplicate closes, company with no bars, as-of window
     edges.
  3. `tests/test_edge_projection.py` — pin `edge_rows()`/`pairs()`
     semantics: part_of/has_company projection, tolerance bounds,
     undated-edge visibility. (If the pin exposes a live bug, record as
     a finding — do not silently change behavior inside this slice.)
  4. Extend `tests/test_msgpackio.py` — truncated packs, wrong type
     tags, oversized payloads, non-UTF-8 keys, roundtrip property
     (small Hypothesis case counts inline).
- **S2 — fuzz legs** (`test_fuzz_msgpackio.py`,
  `test_fuzz_jsonio.py`, `test_fuzz_date_edges.py`): Hypothesis
  roundtrip + malformed-rejection for `msgpackio`;
  orjson↔stdlib parity on edge inputs (NaN, big ints, non-str keys,
  deep nesting) for `jsonio`; bound-parsing for `date_edges`. Plus the
  **as-of leak property** in `test_as_of.py`: for arbitrary T and edge
  sets, a join at T never returns rows with `valid_from > T` nor
  `valid_to <= T` — the alpha-lane leak doctrine as an executable
  property.
- **S3 — integration chains**: `test_integration_temporal_chain.py`
  (price_sync → price_join → as_of → event_study on a fixture store)
  and `test_integration_hyper_family_chain.py` (derive_hyperedges star
  families → HIF → hyper algorithms, mirroring
  `test_integration_graph_algorithms`'s fixture pattern).
- **S4 — perf legs** (after S1–S3 land, baselines measured quiet-box
  per gates.md): add `derive_hyperedges`, `l1_mst`+`scipy_matching`
  (one leg each or one combined), and the as-of weekly census join to
  `BENCHMARKS` in `tests/run_perf_benchmarks.py` with budgets from
  measured baselines ×2 (house headroom convention).

Alternatives considered: (a) full `coverage.py` percentage sweep —
rejected: measures lines, not risk; the matrix above measures
production-wiredness × semantic centrality. (b) Test the research lanes
too — rejected: report-only code churns fastest; testing it pins churn.
(c) A quack CLI smoke test — deferred (guard already tested; wrappers
are thin).

## 4. Acceptance criteria & shakedown

1. S1: `.venv/bin/python3 -m pytest tests/test_price_sync.py
   tests/test_price_join.py tests/test_edge_projection.py
   tests/test_msgpackio.py -q` — green, ≥12 new test functions total,
   each naming the behavior it pins (not smoke).
2. S2: `make fuzz` green; the as-of leak property runs ≥100 Hypothesis
   examples and would fail if a `valid_from > T` row were returned
   (prove the test can fail: assert the property holds on the unit
   fixture AND demonstrates rejection with a hand-built leaking row).
3. S3: `make integration` green with the two new chain tests; both use
   fixture stores only (no live DB, no network).
4. S4: `make perf` green on a quiet box, 3 runs per new leg (never one
   run — timing-shaped), budgets recorded in the table.
5. Gates stay green: targeted tests only post-fix (never `make
   pytest`/`make test` mid-arc); full quartet ONCE on the operator's
   go per `doc/procedures/gates.md`.
6. No eval-gate bullet required — no query-visible semantics change
   (tests only; `BENCHMARKS` additions change perf-report shape only).

| Projected outcome | Today | After |
|---|---|---|
| production-wired modules with zero direct tests (P1 set) | 3 (+1 happy-path-only) | 0 |
| fuzz surfaces | 21 | 24 (+as-of leak property) |
| integration chains for the wave | 0 | 2 |
| perf legs for the wave's hot paths | 0 | 3–4 |

## 5. Risks

- **Edge-projection pin exposes a live bug** — likely benefit, but
  mid-arc discipline: record the finding in this proposal, fix in a
  targeted follow-up commit, never widen the slice silently. (S1
  outcome: edge_projection itself pinned clean; the exposed bugs were
  in `price_sync` — flat-columns crash + degenerate empty-parquet
  store, both recorded in Appendix A with guard tests.)
- **Perf legs add gate wall time** — each new leg gets an explicit
  budget; combined estimate <60 s on the measured baselines of similar
  legs (derive_events 3.0 s, derive_insights 9.0 s budgets).
- **Fixture drift vs live schemas** — price/temporal fixtures must pin
  the schema they assume; reuse `tests/schema.py` helpers where they
  exist, assert on schema at fixture build time.

## 6. Non-goals

- No production code changes (except, if S3/S4 reveal a genuine defect,
  a separately-recorded targeted fix).
- No tests for research lanes (`signal_sweep`, `linkpred_precision`,
  `hyper_predict`), one-off ops tools (`bili_thresh_replay`, `ocr_pin`,
  `seed_note_gemma_cache`, `mca_cin_sync`), or quack CLI wrappers.
- No coverage-percentage tooling or CI coverage gates.
- No changes to the Rust embed-cache binary (its response-shape
  validator landed with `d14390e1e`).

## Appendix A — raw measurement log (2026-11-01)

| Run | Command | Result | Notes |
|---|---|---|---|
| 11-01 | `git log --oneline --since=2026-10-15` | 60+ commits, 9 feature arcs in window | wave inventory |
| 11-01 | `git log --since=2026-10-20 --name-only -- 'helpers/*.py'` | 192 distinct helper modules | sweep universe |
| 11-01 | per-commit `git show --name-status` ×7 | all 7 newest feature commits touched test files in-commit | wave not commit-level starved |
| 11-01 | name-match matrix (first pass) | 60 modules "NONE" | superseded by content grep — name matching fabricates misses |
| 11-01 | content grep `grep -rl <module> tests/*.py` ×41 | 14 true zeros; e.g. `bili_analytics`→3 refs, `derive_merger_hyperedges`→3, `agent_traces`→8 | strips false positives |
| 11-01 | importer grep `edge_projection` | 7 production consumers (paths in §2) | P1 |
| 11-01 | importer grep `price_join` | `stats.py:531` only test-less consumer | P1 |
| 11-01 | `sed -n 1,22p tests/test_msgpackio.py` | 3 roundtrip tests, no negative cases | P1 |
| 11-01 | fuzz target census (`grep "from helpers" tests/test_fuzz_*.py`) | 21 surfaces, none from the wave | P2 |
| 11-01 | `BENCHMARKS` table parse (`tests/run_perf_benchmarks.py:148`) | 12 legs | P2 |
| 11-01 | `ls tests/test_integration_*.py` | 15 files, no temporal/hyper-family chains | P2 |
| 11-01 | `head -15 helpers/research/signal_sweep.py` | "Report-only — EXPLORATORY" | park |
| 11-01 | grep split-brain guard | `quack_server_child.py:27-28` → `duckdb_shared.assert_not_served`, unit-tested | quack trio deprioritized |
| 11-01 | `convo_query "test coverage gaps fuzz perf integration"` | prior art 2026-08-12 (prime-rlm) on older suite | no live proposal existed; this filing supersedes |

### S1 execution (2026-11-01, same day as filing)

| Run | Command | Result | Notes |
|---|---|---|---|
| 11-01 | `.venv/bin/python3 -m pytest tests/test_price_sync.py tests/test_price_join.py tests/test_edge_projection.py tests/test_msgpackio.py -q` | **49 passed + 11 subtests** (was: 4 tests, all happy-path) | ruff check + format clean |
| 11-01 | FINDING — `price_sync.build()` flat-columns branch | **LIVE BUG, pinned not fixed**: a single-ticker batch collapsing to flat columns crashes at `if sym not in chunk` (`helpers/maintenance/price_sync.py:141`) — `sym` is the whole column Index, Index-in-list is elementwise → ambiguous-truth `ValueError`. Batch lost. Observed production shape is MultiIndex, so impact is latent. | `test_flat_columns_single_ticker_batch_crashes_build` guards the crash; fix deliberately deferred to a code slice |
| 11-01 | FINDING — fully-failed `build()` writes degenerate parquet | All-batches-failed → `pa.Table.from_pylist([])` writes a zero-column parquet that `duckdb read_parquet` cannot open (`InvalidInputException`). `--fill-missing` recovery cannot then read the store it is recovering. Pinned in `test_all_batches_failing_reports_and_writes_empty`. | recovery-path fix deferred with the flat-columns bug |
| 11-01 | msgpackio edges pinned empirically | bin-key decode → `{b'\xff\xfe': 1}` bytes key (asymmetric with encode); strict trailing-garbage rejection; typed mismatch → `msgspec.ValidationError`; 2**64 encode → `OverflowError`; NaN/±inf/int64-uint64 boundaries roundtrip | `TestMsgpackioNegativeInputs` + `TestMsgpackioValueEdges` (14 tests) |

### S2 execution (2026-11-01, same day)

| Run | Command | Result | Notes |
|---|---|---|---|
| 11-01 | `make fuzz` (all `tests/test_fuzz_*.py`, xdist) | **195 passed** incl. 4 NEW files: `test_fuzz_msgpackio.py`, `test_fuzz_jsonio.py`, `test_fuzz_date_edges.py`, `test_fuzz_as_of.py` | ruff clean; deviation from proposal letter: the as-of leak property lives in `test_fuzz_as_of.py` (not `test_as_of.py`) so `make fuzz` actually exercises it |
| 11-01 | as-of leak property (`test_as_of_never_leaks`, 100 generated edge-set/probe examples) | **HOLDS**: visible set == independently recomputed coverage predicate, exactly (soundness + completeness); boundary hits generated by building windows from the probe pool | can-fail proof: `test_property_is_discriminating` (hand-built future-dated row rejected) |
| 11-01 | FINDING — differential() drift census gap | `expired_at` SQL requires `valid_from IS NOT NULL AND valid_from <= probe`, so a row with **valid_from=NULL but valid_to set** is invisible yet in NO drift class — the docstring's "both must equal the live-vs-asof difference" identity breaks. `edges_as_of` itself is leak-safe (row correctly invisible). | pinned `test_differential_gap_from_null_to_dated_is_pinned`; partition property restricted to well-formed windows; fix deferred |
| 11-01 | FINDING — msgpackio second error class | a msgpack str chunk with invalid UTF-8 raises **UnicodeDecodeError** (ValueError sibling, NOT a msgspec.DecodeError subclass) — callers catching only DecodeError miss it. Property accepts both classes; specific pin test added. | fix (wrapping or documenting at the seam) deferred |
| 11-01 | FINDING — jsonio NaN asymmetry vs its own docstring | docstring says "orjson rejects NaN/Infinity by default" — TRUE on loads (`loads('NaN')` → JSONDecodeError) but **FALSE on dumps: `dumps(nan)` writes `null`**, so a NaN silently becomes None downstream | pinned `test_nan_asymmetry_is_pinned`; docstring correction deferred to a code-slice |
| 11-01 | date_edges plan() model-based oracle | plan == independently recomputed evidence model on 100 arbitrary edge mixes (1-based rowid trap found in the oracle itself, not the code); parsers total (arbitrary text → None or ISO); census closes | `except ValueError, TypeError:` at `date_edges.py:95` is PEP 758 py314 syntax — left untouched per AGENTS.md |

### S3 execution (2026-11-01, same day)

| Run | Command | Result | Notes |
|---|---|---|---|
| 11-01 | `pytest tests/test_integration_temporal_chain.py tests/test_integration_hyper_family_chain.py` | **8 passed** (2 temporal + 6 hyper-family; the hgx leg RAN — hypergraphx 1.8.0 is in the venv) | ruff clean; both files `pytestmark = [pytest.mark.integration]`, all fixtures tmp-path hermetic |
| 11-01 | temporal chain: price_sync(faked download) → weekly parquet → price_join census → as-of event selection → event_study | end-to-end hand-checked: subject +10% / peers +5%/+20% → median_abnormal −2.5%, prices flowing FROM the parquet (not hand dicts); future-dated and expired edges never become events (leak-free selection); pre-first-bar event → `censored=1, n=0` | the S3 unit-test arithmetic now proven through the real chain |
| 11-01 | hyper-family chain: 14 fixture dyads → collect_hyperedges → apply (dry-run/reapply/idempotent) → HIF parquet → validate → hgx Hypergraph | 7 labels / 17 incidences land; dry-run writes nothing; re-apply 0/0 everywhere; `validate_hif_roundtrip` counts match the store; drift test — a post-export hyperedge fails the count check as designed | fixture hermetic by construction: entities.file_path NULL skips the industry vault scan; no quotes/events tables keeps edition/event families empty |
| 11-01 | hgx availability | **CORRECTED 2026-11-01**: hypergraphx 1.8.0 IS installed and the Hypergraph leg passed. Initial record claimed absence — the probe imported `hgx` (the helpers' internal nickname) instead of the package module `hypergraphx`. Absence claims need the right name checked. | `_require_hgx` imports `hypergraphx` lazily; leg stays `importorskip` so the chain proves itself on an hgx-less box too |

### Fixes applied + S4 execution (2026-11-01, operator go "verify the fixes"; perf with `--force` per operator — box not quiet)

| Run | Command | Result | Notes |
|---|---|---|---|
| 11-01 | FIX 1+2 — `price_sync.py` | flat-columns single-ticker batch now attributes to the lone ticker (was: ambiguous-truth ValueError crash); flat frame from a MULTI-ticker batch fails the chunk loudly (unattributable → `--fill-missing` recoverable) instead of crashing; parquet write carries an explicit 7-column schema so a fully-failed run's zero-row store stays readable (was: zero-column file duckdb cannot open, locking `--fill-missing` out) | first fix attempt had `df[sym]` KeyError on the flat frame (columns are field names) — the flat frame IS the ticker's table; caught by the flipped guard before landing |
| 11-01 | FIX 3 — `as_of.py` differential | `expired_at` now counts from-NULL/to-dated rows (`valid_to <= probe AND (valid_from IS NULL OR valid_from <= probe)`); identity `asof + expired + not_yet == live` holds on UNRESTRICTED windows (fuzz property re-widened from well-formed-only); disjointness preserved — born-after rows with closed windows count as `not_yet_born` (pinned) | |
| 11-01 | FIX 4 — `msgpackio.py` | invalid-UTF-8 str chunk → `msgspec.DecodeError` (chained `__cause__`), unifying the seam to ONE typed error class; property narrowed from two accepted classes to one | |
| 11-01 | FIX 5 — `jsonio.py` | docstring corrected: loads REJECTS NaN/Infinity on parse (JSONDecodeError), dumps SERIALIZES them as null — behavior unchanged, documented + pinned | |
| 11-01 | guard flip verification | **88 passed + 11 subtests** across all 11 arc files (S1 unit + S2 fuzz + S3 integration), guards now pin the FIXED behavior (incl. 2 new: flat-multi-ticker chunk-fail, born-after-expired disjointness) | ruff clean |
| 11-01 | S4 — 4 new perf legs in `tests/run_perf_benchmarks.py` BENCHMARKS | `graph_mst` (l1_mst mst-backbone), `graph_matching` (scipy_matching match-suppliers), `hyper_derive` (derive_hyperedges dry-run — never applies), `price_census` (price_join census, read-only) | budgets = measured baseline ×2: 2.0/3.0/4.0/1.0 s (baselines 0.72/1.06/1.71/0.25 s) |
| 11-01 | `.venv/bin/python3 tests/run_perf_benchmarks.py --force` | **31/31 budget-passed; all 4 new legs ✓ OK** (0.59/0.99/1.60/0.22 s vs budgets 2.0/3.0/4.0/1.0) | first attempt used bare `python3` — the interpreter trap: all 31 legs died at ~0.05 s on `helpers.core.db` import death while the budget table still said "passed"; always `.venv/bin/python3` |
| 11-01 | pre-existing FAIL(rc) legs — NOT this arc | pagerank/louvain/eigenvector/link_prediction = `GraphCacheStaleError` (needs `make graph-rebuild`); shortest_path_bfs = quack split-brain guard (`graph.duckdb` served by pid 2931161); snapshot_check = drift warnings (source moved +6) | all store/box state predating the arc; remedy is operator-side (graph-rebuild / quack-stop), not a code change |
