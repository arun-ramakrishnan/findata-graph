---
title: "Algorithms adoption sweep — push the installed library shelf into the lanes (fuzzy SQL pushdown, FDR stats, motifs census, igraph second opinions, RRF consolidation)"
status: executed
filed: "2026-11-08"
executed: "2026-11-08"
completed_md: "494"
area: "helpers/core, helpers/research, helpers/graph, helpers/misc, helpers/maintenance"
---

# Algorithms adoption sweep

**Date:** 2026-11-08 · **Status:** EXECUTED 2026-11-08 (S2/S3/S5/S7
landed; S1 measured-not-adopted same day; S4/S6/S8 deferred with
recorded reopen triggers) · **Survey:**
`doc/local/evaluations/algorithms_assessment.md` §9 · **Spin-off
arcs:** entity-alias fold rounds 1+2 (17 canonical folds, 2 keeps,
census re-baselined 79) and the LIVE follow-on proposal
`co_supply_peer_lane.md` (S1+S2 landed; S3 prereg-gated)

## 1. TL;DR

The 2026-11-08 algorithms sweep inventoried every algorithm in active use
(Onager/igraph/scipy/hypergraphx/Rust/hand-rolled — assessment §9.1) and
the installed-but-unused library shelf (scipy 1.18.1, numpy 2.5.4,
DuckDB 2.0.0, igraph 1.0.0 — all version-verified by introspection).
Eight adoption candidates came out of it (assessment §9.3). This
proposal carries all eight with an adopt/defer verdict per slice; S1
(DuckDB fuzzy pushdown) and S2 (scipy.stats significance layer) start
now — both are dep-free, small, and serve the three live fronts
(polyadic capture, richer DuckDB semantics, note capture). **S1
outcome (same day): MEASURED-NOT-ADOPTED** — parity 204/204 on the
live corpus, but the SQL backend is 0.27–0.39× the Python loop at
24,733 entities; module+tests+bench kept as instrument, no wiring
(§3 S1 verdict block). **S2 landed** (wilcoxon/binomtest/bootstrap in
event_study; exact binomial + BH FDR in linkpred_precision).
Everything
already probed-and-closed (HNSW, FTS5 optimize, quantization, gemma,
B/F-hyperpath, KaHyPar, Onager-PPR, Mojo) stays closed — see the
assessment §9.3 closed list, no re-probes here.

## 2. Evidence

- Active-use inventory: assessment §1–§8 tables + §9.1 corrections
  (igraph pilot-venv retirement, duckpgq retirement, scipy bridge lanes,
  Rust prod lanes, hypergraphx lanes).
- Environment inventory (2026-11-08, import/dir-verified): numpy 2.5.4,
  scipy 1.18.1, duckdb 2.0.0, igraph 1.0.0, hypergraphx 1.8.0,
  pyarrow 26.0.1; scikit-learn NOT installed; networkx/pandas installed
  but never imported in prod code.
- DuckDB 2.0.0 string-similarity SQL verified present via
  `duckdb_functions()`: jaro_similarity, jaro_winkler_similarity,
  levenshtein, damerau_levenshtein, editdist3, jaccard, hamming.
- scipy.stats verified present: false_discovery_control, permutation_test,
  wilcoxon, bootstrap, mannwhitneyu.
- Hot-path observation: `helpers/core/fuzzy_match.py:244-291`
  `word_overlap_match` scans all entities in Python per query, on the
  entity-resolve path that fires before VSS on every note capture.

## 3. Slices

### S1 — DuckDB fuzzy pushdown [STARTED → MEASURED-NOT-ADOPTED same day]

Replace the per-query O(all-entities × tokens) Python scan in
`word_overlap_match` with ONE set-based SQL pass over the entity corpus,
using DuckDB 2.0.0's built-in similarity/token functions.

- New module `helpers/core/fuzzy_duckdb.py`:
  `word_overlap_match_sql(query, entities, threshold=0.5, conn=None,
  table=None)` replicating the EXACT contract of
  `fuzzy_match.word_overlap_match` — containment short-circuit
  (blank/whitespace query guard included), stopword stripping
  (`_STOPWORDS`), distinctive-word requirement (`_GENERIC_WORDS`),
  score = |overlap| / max(|q_tokens|, |e_tokens|), strict `>` best-score,
  first-wins ties in entity order. Entities ride in via
  `duckdb.register`/Arrow (pyarrow already a dep; no SQLite-attach
  needed); an optional caller connection is used when provided.
- The SQL shape: one SELECT computing per-entity containment flag,
  token lists via `string_split_regex` + `list_filter` (stopword and
  generic filtering), `list_intersect` overlap, row-index column for
  deterministic first-wins ordering; `ORDER BY` containment-first,
  score-desc, idx-asc; `LIMIT 1` after threshold filter.
- Parity gate BEFORE any caller wiring: `tests/test_fuzzy_duckdb.py`
  asserts backend equivalence (match name, method, score) against the
  Python path on a fixture corpus covering the documented traps
  ("Adani Port"→"Adani Ports and SEZ" not "Adani Power"; generic-only
  overlap rejection; whitespace query; containment precedence; strict->
  ties). Parity r=1.0 on the fixture + the live entity list, or the
  slice does not wire.
- Wiring (after parity): callers that already hold a DuckDB connection
  or large entity sets route through the SQL backend; the Python path
  stays the degrade chain (small entity sets, no duckdb import budget).
  `fuzzy_match()` gains a `backend="auto"|"py"|"sql"` passthrough.
- Bench: `helpers/bench/fuzzy_backend_bench.py` (or an inline
  timeit in the test) — entity list = live `entities` names; expected
  order-of-magnitude win per query, recorded in the proposal thread.

**VERDICT 2026-11-08 (same-day, post-implementation): MEASURED-NOT-ADOPTED.**
The module, parity fixture (30 tests) and bench all landed and pass
(parity 204/204 on the live 24,733-entity corpus), but the pushdown
LOSES at current scale: mixed query path 0.39× (3.9 ms Python vs
10.2 ms SQL — early-exit containment hits make Python nearly free), and
the full-corpus miss path 0.27× per-call (35.5 ms vs 133.3 ms —
per-call entity registration + CTE planning dominates). Even with
tokens materialized once into a session table (bench probe, parity
verified on the miss path) the overlap scan only TIES at 33.8 ms vs
35.9 ms — DuckDB's per-row `list_intersect` is the same O(N) work the
Python loop does, just with more fixed overhead. The sweep's "hot path"
premise was wrong at 24.7k entities: the loop is 3.9 ms average /
35.5 ms worst case, not a gate-relevant cost. Disposition: module +
tests + bench stay (parity instrument, correct substrate); NO caller
wiring. Revisit triggers: entity corpus ~10× (≈250k names), or the
fuzzy stage showing up in a gate profile. At that scale the better
fix is an inverted index (token → entity postings), which changes the
complexity class for BOTH backends rather than moving the same O(N)
scan across the boundary.

### S2 — scipy.stats significance layer [ADOPT — LANDED 2026-11-08]

The research/price lanes report point estimates with no uncertainty.
Adopt three installed-and-unused scipy.stats instruments:

- `helpers/research/event_study.py`: per-horizon rows gain
  `wilcoxon` (signed-rank on the abnormal-return sample vs 0, two-sided
  alternative) and `percent_positive` binomial context via
  `scipy.stats.binomtest`-style read from the existing `pos/n`; plus a
  bootstrap CI on `median_abnormal` (`scipy.stats.bootstrap`,
  paired=False, n_resample capped at 2k for the 4-core box). Report-only
  columns appended to the existing table — the S3 report-only doctrine
  (price_lane_alpha_research) is unchanged, no gate.
- Link-prediction pre-reg (`doc/local/evaluations/linkpred_precision_prereg.md`):
  the decision rule gains a formal test at each run — exact binomial /
  permutation test of precision@25 vs the candidate base rate, and when
  multiple horizons/classes are scored together,
  `scipy.stats.false_discovery_control` (BH) over the p-value family.
  The pre-reg's NO-GO/KEEP thresholds stay frozen; the test is the
  instrument, not a re-registration. Outcome-log rows gain a p-value
  column from the next realized-deal run.
- Tests: `tests/test_event_study.py` extends with synthetic event sets
  where the answer is analytically known (symmetric noise → p≫0.05;
  shifted returns → p<0.05); scipy import is module-level-optional
  guarded the same way the repo guards optional engines (test skips
  cleanly if scipy were ever absent — it is a declared dep today).

### S3 — Repo-native full-incidence motifs census [ADOPT — LANDED 2026-11-08 as instrument]

HGX lane 1 (motifs census) is NO-GO on the current instrument — it only
sees edges of size ≤ order (`get_edges(size=order, up_to=True)`); the
2026-11-02 re-probe measured cross-edge 3-node overlap is real (edition
714 entities in ≥2 edges, same_group 903/320). The blocker is ours to
own. **LANDED**: `helpers/graph/hyper_motifs.py` (standalone lane CLI,
house pattern) — the census statistic is the count of unordered
hyperedge pairs sharing ≥ k distinct members over the FULL deduped
incidence (no size cap); realized as a node-postings index (the
proposal's SQL self-join shape, same complexity class; measured max
~30k pair-rows on the live store). Null: stub-matching configuration
model preserving node degrees + edge sizes, within-edge dupes deduped
(conservative), runs=10/seed=42 (the 2026-10-28 probe protocol);
norm_delta with the ±2 ENRICHED/DEPLETED bands. 10 hermetic tests
(`tests/test_hyper_motifs.py`, synthetic-only per house convention).

**Live census 2026-11-08** (norm_delta, |nd|≥2 bands):

| family | edges | pairs≥2 obs | k=2 | k=3 | read |
|---|---|---|---|---|---|
| supply | 547 | 6,603 | **+54.6** | **+59.6** | ENRICHED both — the strongest structure in the store; supplier edges co-share members far above the config model (real co-supply chains) |
| group | 506 | 243 | −2.9 | **+30.6** | bimodal — same-group edges either share a stable core (4+ members, 169 pairs) or don't touch |
| edition | 197 | 771 | −3.1 | **+12.6** | same bimodal shape — stable recurring core vs no overlap |
| index | 53 | 617 | −4.9 | +1.7 | ~NULL at k=3 — overlap is fully explained by giant edge sizes (617/1,596 pairs share 4+ members, but the size-preserving null reproduces it) |
| event | 1,100 | 19 | +0.5 | — | ~NULL — k=2-only family, consistent with dedup |
| industry / sub_sector / org_membership / country | — | 0–1 | — | — | zero cross-edge mass — genuinely disjoint |

**Finding**: the HGX instrument's norm_delta=0.0 verdict was an artifact
of the visibility defect. The full-incidence census shows real
higher-order structure the moment the store carries it — supply is
deeply enriched at BOTH orders, and edition/group are bimodal
(substantial-core overlap or none, depleted at casual k=2). This is the
non-trivial signal the proposal gated consumer wiring on: a
supply-family second lane (e.g. co-supply context packs or s-walk
seeding) now has measured justification; the D10 prediction-grade table
gains supply/subsidiary as structurally live families even where the
k≥3≥60% density test fails.

Instrument-only — no writes, no gate; consumer wiring is a separate
slice with its own eval bank.

### S4 — hypergraphx[ml] community second opinions [DEFER — reopen trigger named]

`uv pip install 'hypergraphx[ml]'` = 5 packages incl. scikit-learn 1.9.1
(verified dry-run 2026-11-05) unlocks HySC + hypergraph-mt as HyMMSBM
second opinions. Deferred because the gate is undefined
(`sweep_k` reports only log-likelihood/BIC; no disagreement
metric/threshold named) and the size-850 country edge collapses HyMMSBM
to one block under `allow_giant=True`. Reopen when: a consumer asks for
an overlapping-membership read on a specific family AND a disagreement
metric (ARI vs HyMMSBM on the same family) is pre-registered. The ARI
harness (`helpers/bench/hyper_community_probe.py`) already exists;
it needs the sklearn install to run its live-family variant.

### S5 — igraph second-opinion shelf [PARTIAL ADOPT — LANDED 2026-11-08]

ADOPT the three cheap structure instruments through the existing
`igraph_bridge.py` ROUTING pattern, wired as report-only additions to
`make graph-stats`:
- `articulation_points` — exact cut vertices (near-free; complements
  sampled betweenness for chokepoint QA).
- `coreness` — full k-core profile (the hand-rolled 2-core peel in
  `l1_closeness.py` proves the primitive's value; igraph gives the
  whole curve).
- `assortativity` (degree) — one-number graph-health trend tracked
  across editions.
DEFER infomap/walktrap/label_propagation/burt_constraint: community
lenses beyond Louvain/Leiden/HyMMSBM are a metric zoo until a consumer
asks; Burt's constraint overlaps the betweenness/broker lane.

**LANDED 2026-11-08**: `stats.py::igraph_second_opinion_lines()` — a
report-only section in `make graph-stats` (advisory-guarded like the
other sections; the lazy `_require_igraph` import degrades to an
"(unavailable)" line). 4 hermetic tests incl. a hand-checked
triangle-bridge-cycle graph (articulation n2/n3) and the no-igraph
degrade path. Live (post round-2 fold, 19,718 v / 59,943 e):
753 articulation points (3.8%), coreness max_k=32 with k17-19 spikes
(edition cliques) and 8,151 k1 leaves, degree assortativity −0.2416
(mildly disassortative — hubs-to-periphery, the expected supply/
mention shape).

### S6 — cKDTree near-duplicate lane [DEFER / WATCH]

`near_duplicate_notes` materialises the full P×P f64 GEMM (~800 MB at
10k paths — its own docstring, `query.py:4764-4829`). Because vectors
are unit-norm, L2 ordering ≡ cosine ordering, so a
`scipy.spatial.cKDTree` on the unit matrix gives per-row top-k in
O(P log P) with no GEMM. Deferred: current path counts are far below
the regime where the GEMM hurts. Watch trigger: path count → 5×10³, or
the first OOM/latency complaint on the near-dup lane. (Same
unit-norm equivalence the HNSW arc recorded; not an ANN proposal —
cKDTree is exact for this use.)

### S7 — RRF consolidation [ADOPT — LANDED 2026-11-08]

Four RRF implementations with one private cross-import
(`note_query.py:226`, `convo_query.py:250`, `rebuild_doc_search.py:1068`
+ `_RRF_K:849`, `rebuild_memory_search.py:906` reaching
`rds._RRF_K`): consolidate into `helpers/core/rank_fusion.py`.
The convo top-anchor pinning is a convo-specific extension, not a
shared default.

**LANDED 2026-11-08** — scope grew to SIX sites: the private-import web
turned out to carry the same two-leg formula in `rebuild_script_search`
and `rebuild_git_search` (the latter's extras rule — cosine-only rows
outside the BM25 page fuse on the cosine leg alone, the S5 live-bank
finding — absorbed as `rrf_two_leg`'s `extra_pred`). Module:
`RRF_K`, `rrf` (generic N-leg), `anchored_fusion` (convo named
variant), `rrf_two_leg` (frozen semantics: missing-cos rows at
`worst + bm25_pos`, `None` cos_rank = bm25-only). Bench/probe surfaces
(`note_deep_probe*`, `search_tui`, `master_query`) deliberately NOT
touched — probe formulas differ (e.g. `1/(k+pos)` without the +1) and
bench drift is out of scope. Equivalence evidence: 4 reference-parity
tests (each embeds the OLD inline body as the oracle) + 7 live query
surfaces captured pre-refactor under `bench_data/rrf_consolidation/
pre/` — all 7 rank IDENTICAL post-refactor (doc×2, note, convo, mem,
git byte-identical; script identical modulo a staleness warning from
the session's own index rebuild).

### S8 — numpy 2.5 features in ingest/price lanes [DEFER]

`np.strings` (vectorized lower/startswith/split) and
`sliding_window_view` for the derive_* per-bullet regex loops and
price rolling windows. Deferred pending a measured hot loop — the
2026-08 perf_improvs sweep already hoisted the real regex hotspots,
and no current leg is regex-bound at gate scale. Reopen with a profile.

## 4. Gates / eval discipline

- S1: parity fixture is the acceptance gate; wiring only after r=1.0;
  bench number recorded. Targeted tests only
  (`tests/test_fuzzy_duckdb.py`, `tests/test_fuzzy_match.py`),
  never `make pytest`.
- S2: synthetic known-answer tests; report-only — no gate arcs.
- S7: frozen-bank rank equivalence is the acceptance gate.
- All slices: `make search-fresh` advisory after doc edits; the arc
  takes its full gates quartet ONCE on the operator's go
  (`doc/procedures/gates.md`).

## 5. Risks

- **S1 parity traps**: DuckDB list-function semantics (empty-token
  filtering, NULL lists on degenerate names) differ from Python set
  ops; the fixture corpus must include names that are only punctuation,
  single generic words, and non-ASCII. First-wins tie order needs the
  explicit row-index column (DuckDB row_number without ORDER BY is
  unordered).
- **S2 statistical power**: realized-deal N is small (0 today); the
  pre-reg's ≥10-deal floor stands — the p-value column will be
  underpowered early, which is expected and recorded, not a flaw.
- **S3 blow-up guard**: the h_incidence self-join is bounded by capping
  candidate pairs to families with k≥3 mass (the same per-family gate
  D10 uses); supply-family singletons (7,521 k=1) must not enter the
  join.
- **Dependency discipline**: S4 is the only slice adding packages
  (sklearn via hypergraphx[ml]); it stays deferred precisely for that
  reason.
