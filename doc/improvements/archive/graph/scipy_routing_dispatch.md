---
title: "ROUTING dispatch — wire the scipy lane into algorithms.py"
status: executed
filed: "2026-09-23"
area: "helpers/graph"
executed: "2026-09-23"
completed_md: "279"
---

# ROUTING dispatch — wire the scipy lane into `algorithms.py`

**Date:** 2026-09-23 · **Status:** EXECUTED — archived 2026-09-23

Child of `doc/improvements/archive/graph/scipy_graph_bridge.md` (S1
dispatch follow-through; §6 amends its S1 acceptance).

## 1. TL;DR

`helpers/graph/scipy_bridge.py` carries a `ROUTING` table
(`closeness_centrality`/`harmonic_centrality` → `SCIPY`,
`betweenness_centrality` → `L1B_FOLD`, everything else →
`ONAGER_DEFAULT`) that nothing reads: `algorithms.py closeness` /
`harmonic` still serve Onager (156.8 s cold leg), and the only
consumer of the lane is the `make perf` leg calling the bridge CLI
directly. This proposal wires `ROUTING` into the `_run_*` dispatch
(`algorithms.py:759`) so the unified `compute()` + CLI path serves
the owning lane — for `SCIPY` entries only, when no synthetic
`edges=` override is passed. Everything else keeps the existing
Onager path. Net effect on `make perf`: the closeness leg drops from
156.8 s toward ~6 s with bit-identical values.

## 2. Evidence this is safe (measured 2026-09-23, this box)

Two-projection shootout vs the 1,734 live `graph_analytics`
incumbent rows (script: inline `dijkstra` + `fused_derive` over both
projections; `.venv/bin/python3`):

| Projection | Closeness r / top-100 / maxdiff | Harmonic r / top-100 / maxdiff |
|---|---|---|
| ALL-EDGES (bridge as written) | 1.000000 / 100 / 0.0 | 1.000000 / 100 / 1.8e-12 |
| EX-INDEX (no `listed_on_index`) | 0.957055 / 91 / 9.5e-02 | 0.967177 / 92 / 2.9e+03 |

Consequences, each load-bearing for the design below:

- The bridge needs NO projection change: Onager's
  closeness/harmonic run on the FULL edge set (the index-noise
  exclusion is a betweenness-and-friends rule, not a global one —
  closeness covers all 22,046 endpoints). The spike's r=0.9571 was
  the ex-index variant's number, not a formula gap.
- The formulas are stamp-exact (`(N-1)/sum(reachable)`,
  raw `sum(1/d)`). The umbrella's S1 acceptance ("r ≥ 0.95")
  undersells the lane and is tightened to exact parity by this
  proposal (§6, slice S0).
- Timing: 5.32 s single-process all-edges leg (4.60 s ex-index);
  the 4-way fork split stays a budget lever (~2.9 s per spike).
- Convention fork (2026-09-23 amendment, measured same generation,
  same day): the exact parity above is parity against LANE-WRITTEN
  incumbents (lineage, not independent proof). Fresh Onager on the
  same data gives india 0.393350 vs lane 0.418669 — Onager's DB path
  applies the index-noise exclusion (`_onager_central`,
  `algorithms.py:594-602`) and a black-box C++ normalization, while
  the lane runs all-edges with `(N-1)/sum`. Direct ex-index-lane vs
  fresh-Onager measurement (149.7 s fresh run, n=1,734): r=1.000000
  with a CONSTANT ratio 1.2383 (p10=med=p90) — rank-exact, globally
  rescaled by an underived constant. Two universes therefore
  coexist: served `graph_analytics` rows (lane/all-edges scale) vs
  `v_centrality_*` stamps (Onager/ex-index scale). This proposal
  wires the SERVED universe and preserves its values bit-exact;
  reconciling the two universes (one projection + one formula for
  both stamp and served paths) is explicitly OUT OF SCOPE and
  recorded as a future decision — changing served absolutes by -6%
  on the basis of an underived black-box constant is riskier than
  preserving them, and rankings are identical either way.

## 3. What gets wired, exactly

`compute(metric, con, ...)` (`algorithms.py:776`) dispatches via
`_METRIC_DISPATCH` (`:759`) to `_run_*` handlers (`:735-744`),
which today all call the low-level Onager functions. The CLI loop
(`:1135`) calls `compute()`, prints `[via onager]` (`:1142`),
pretty-prints, and persists on `--apply` (D13, dry-run default).

The wire: `_run_closeness` / `_run_harmonic` consult `ROUTING`
(imported lazily — see §5) and, when the entry is `SCIPY` **and**
`edges is None` (DB-backed path), run the bridge
(`load_projection` → `contract_sources` → `source_positions` →
`compute(jobs=1)` → contract-keyed dict) instead of
`closeness_centrality()` / `harmonic_centrality()`. The routed set
is derived FROM the table
(`{m for m, o in ROUTING.items() if o == "SCIPY"}`) — never a
second hardcoded list. The CLI prints `[via scipy (ROUTING)]` for
routed metrics; `--apply` persistence, `_wrap_for_analytics`, and
top-N display are untouched.

ROUTING values other than `SCIPY` keep the existing path:

| Value | Disposition |
|---|---|
| `SCIPY` | lane serves DB-backed calls (this proposal) |
| `ONAGER_DEFAULT` | Onager, as today |
| `L1B_FOLD` | Onager + documented fallback UNTIL the fold meets the entry criteria in §8 (toy harness green, specials fix, fresh-bypass parity) — activation is a one-line dispatch change with its own acceptance, not this proposal |

## 4. Deliberate non-changes (each with a reason)

- **Low-level `closeness_centrality()` / `harmonic_centrality()`
  stay Onager, full-population.** `query.py:1539` consumes them for
  per-node scoring over the whole graph; rewiring them to the
  1,734-row contract lane would silently truncate arbitrary-node
  lookups. The wire lives one level up, at `_run_*` (whose only
  consumer is `compute()` → the CLI).
- **Synthetic `edges=` calls always go Onager.** Tests
  (`test_integration_perf.py:223,481`,
  `test_centrality_projection.py:112`) and API callers pass
  explicit edge lists that deliberately differ from the DB
  projection; the lane only knows the live SQLite graph. The
  `edges is None` gate preserves this.
- **No full-population scipy mode.** Serving all 22,046 sources
  through the lane needs a 22k² dense distance matrix (3.9 GiB —
  the documented dense wall) or ~67 s of chunked runs, defeating
  L1a's whole purpose. The contract population (1,734) is also the
  population `graph_analytics` actually holds for these metrics,
  so the CLI persist path keeps writing the table's live shape.
- **`jobs` stays 1 on the dispatch path.** Single-process is the
  lane default per umbrella §5; the perf leg already passes
  `--jobs 4` to the CLI directly when the budget demands it.
  (~6 s in `--all` runs vs ~156 s Onager — a net win regardless.)
- **Contract drift fails loud.** `source_positions` raises
  `KeyError` on contract names missing from the projection; the
  CLI surfaces it as `FAIL: KeyError`. Silent truncation would
  poison the stamp-shaped tables downstream.
- **No silent engine fallback, ever.** A failed fast leg must fail —
  never degrade into a silent Onager run nobody asked for (a quiet
  156 s blowup is worse than a failed leg). Pre-flight assertions
  in the handler (lane importable; projection non-empty and shaped
  like the contract's generation; contract fully resolves) fire
  BEFORE any compute, and every failure message states the slow
  path's measured cost, e.g. `refusing Onager fallthrough: fresh
  closeness ≈156 s cold — fix the contract/projection and rerun
  the ~6 s lane`. In a CLI world the error string is the mechanism
  that asks the user to fix the caller.

## 5. Consequences to record (behavior changes, all intended)

- **Cache bypass for two metrics.** `_run_closeness` today serves
  the stamp table / query-result cache when present; the lane
  always computes live from SQLite. Values equal a *fresh* Onager
  compute bit-for-bit (§2), but may differ from a *stale* stamp —
  that is the point (L1 measures compute, and staleness is the
  known hazard). `--compute` help text gains "(or the owning lane
  per ROUTING)".
- **`compute()` docstring fix.** `:787-792` currently claims "All
  metrics are computed directly from the DuckDB connection via
  Onager" — false after wiring. Updated to name the ROUTING
  exception.
- **Lazy import, no cycle.** `scipy_bridge` imports
  `helpers.core.db` + `forkmap` at top level only (its
  `algorithms` import is function-local inside `main`), so a
  top-level import would be cycle-safe — but scipy/numpy import
  cost lands on every `app.py`/`query.py` import chain, so the
  handler imports the lane lazily. (Precedent for the discipline:
  `duckdb_connect` resolves `query.connect` at call time, `:82`.)
- **Module header table.** The engine map (`:13-23`) lists
  `closeness_centrality`/`harmonic_centrality` as Onager — updated
  to scipy with the ROUTING pointer.
- **`--all` pays the lane twice** (2026-09-23 late note):
  `_run_scipy_lane` computes BOTH closeness and harmonic per call and
  returns one, so an `--all` run executes the dijkstra pass once per
  metric (~5-6 s each) instead of sharing one pass. Still ~28x under
  the Onager fresh path it replaces; a per-process memo keyed on the
  projection identity (or a combined handler) is the deferred
  optimization, deliberately not in this proposal's slices.

## 6. Slices

- **S0 — tighten the umbrella S1 acceptance** (this proposal
  amends, does not replace): exact parity table from §2 above;
  projection verdict (all-edges correct; ex-index was the 0.957
  source); note the index-noise rule's true scope
  (betweenness-family only).
- **S1 — dispatch + tags + docstrings**: `_run_closeness` /
  `_run_harmonic` routing, `[via scipy (ROUTING)]` tag,
  `compute()` docstring, `--compute` help, header table.
  `db_path` knob plumbed through `compute()` → handlers (default
  live DB; tests pass tmp stores).
- **S2 — tests** (in `tests/test_scipy_bridge.py`, the lane's home):
  ROUTING keys ⊆ known metric names and values ∈
  `{SCIPY, ONAGER_DEFAULT, L1B_FOLD}`; live closeness exact vs
  incumbents (one ~6 s test — served-universe preservation per the
  §2 fork note, NOT independent Onager parity);
  tmp-store dispatch mechanics for both metrics (fast,
  hand-checked); `edges=`-synthetic fallback takes the Onager
  path; low-level functions untouched (query.py path intact).
- **S3 (future, NOT this proposal)** — `L1B_FOLD` activation gated on
  the §8 entry criteria (toy harness green, specials fix, fresh-bypass
  parity); same shape, its own acceptance.

## 7. Risks

- `test_centrality_cache.py` exercises `closeness_centrality()`
  directly (low-level, untouched) — no interference; but
  `test_warm_tables_equal_fresh_compute` asserted stamp == `compute()`
  output for ALL score tables, an invariant routing invalidates by
  design (contract pop vs full pop) — carved out for SCIPY-routed
  metrics with an explicit comment at implementation time.- qa cost: one ~6 s live test added to the suite.
- The `KeyError`-on-drift posture assumes the contract only grows
  monotonically; a contract *shrink* with stale names fails the
  whole `--all` run at the closeness leg — loud, and preferable
  to partial writes.

## 8. Correctness record — five-route program (2026-09-23 amendment)

Operator decision: route ALL FIVE remaining slow paths and deal
with failures head-on (known-unknown hazards get assertions, not
avoidance). Readiness, honestly graded:

| # | Blowup | Route to | State |
|---|---|---|---|
| 1 | closeness / harmonic fresh Onager (156 s / ~192 s) | scipy lane (this proposal S1–S2) | READY — exact proven, §2 |
| 2 | betweenness fresh Onager (48.9 s) | L1b fold (`L1B_FOLD`) | EVIDENCE-COMPLETE + COUNTERSIGNED — §8.3 (re-run 2026-09-23: flat 1.000000); activation ready |
| 3 | link-pred `pref-attach` all-pairs (~485M pairs) | exact top-K heap + consent gate (both) | RESOLVED — §8.4 (guard on all surfaces, heap 0.83 s witnessed, unbounded stays refused) |
| 4 | stamp job (5–6 min) | skip lane-served, table-driven | DONE — §8.5 (census: zero request-time readers; seven native stamps in ~8 s) |
| 5 | hop metrics (~1 s apiece) | nowhere | CLOSED 2026-09-23 late — measured 0.76 s total live (diameter/radius/APL NULL via the component-check skip, as designed); connected-case ~3 s covered by per-generation result cache + route_graph_stats 5.0 s budget; tests pin connected values and disconnected NULLs |

Sequence: 1 → 2 → 3 → 4. Each route lands behind assertions (S1
hardening); nothing degrades silently.

Route-3 note (2026-09-23 late): pref-attach scores need NO
shared-neighbor join — `deg(u)*deg(v)` comes from degree vectors
alone, so a scoped top-K lane is computable directly (SQL order-by
over the degree product) without enumerating the ~485M pairs; the
all-pairs enumeration is only needed for full materialization, which
nothing consumes. That makes the scoped lane the leading option, not
just an alternative to the confirmation gate.

### 8.1 Retraction: the variant-B "match" proved nothing

2026-09-23 a 2.2e-16 match of unhalved-`U` code against all 1,734
`graph_analytics` betweenness rows was presented as a correctness
verdict. It was not: those rows are the output of the
divisor-only intermediate state (divisor fix applied, `U/2` lost),
so the match holds **by construction**. The fresh-bypass evidence
stands (`/tmp/graph_divisor.txt` q49: node-dependent ratios
0.045–0.75 vs live Onager under the halved variant) — NEITHER
variant is proven correct, and core TU/KK scale-mixing is
genuinely open. Lesson recorded: parity against persisted rows
proves lineage, never correctness; only parity against a live
independent compute (fresh bypass, networkx oracles) counts.

**Amendment 2026-09-23 late:** the LESSON stands; the factual basis
does not — see §8.3. On today's disk the 1,734 rows match the FINAL
fixed fold, not the divisor-only intermediate (probe below), and the
q49 ratios quoted above are the PRE-fix diagnosis: they are what
located the two root-cause bugs (non-disjoint coverage, source-self
pollution), both of which were fixed and re-verified afterwards.

### 8.2 `L1B_FOLD` entry criteria (S3 gate)

Status 2026-09-23 late (§8.3): criteria 1–4 are met on current tree
evidence. The numbered entries below are kept verbatim as the
mid-arc record they were written against.

1. Toy harness green vs networkx oracles (path / star / cycle /
   random-with-trees, ordered scale) — parked runs show path/star
   all-zero (peeled `specials` counted in `info` but never scored)
   and a suspect 3× cycle overcount; both must be proven or killed
   against the current file first.
2. Specials-scoring fix + whatever the cycle re-check finds.
3. Live parity vs the fresh Onager bypass (node-dependent ratios
   must collapse to ~1.0, not merely high-r rank parity).
4. Stale-row disposition: the 1,734 applied rows predate the fix
   and must be re-applied after, never consumed before.

### 8.3 Re-grade (2026-09-23 late, agent probe — COUNTERSIGN RE-RUN DONE, see (d))

Evidence standard note first, per §8.1: the probe below is
LINEAGE-standard (fold vs stored rows); the independent-compute
standard is met by the record cited in (d), with one re-run owed
post-restamp.

- **(a) Probe** (`/tmp/q82_probe.py`, this box, against the live
  `memory/research.db`): fresh `l1_betweenness.compute(db, jobs=1)`
  raw scores divided by the 1,734 stored `graph_analytics`
  betweenness rows give a CONSTANT ratio 230,083,426 =
  (21452 x 21451)/2 — exactly the settled final divisor — across all
  1,382 nonzero rows, with 0/1,382 mismatches at 1e-9 relative after
  scaling. The stored rows ARE the fixed fold's normalized output.
  §8.1's premise ("rows are the divisor-only intermediate state") is
  therefore false for the current rows. Alternatively, if the
  `e3a23f97` restamp rewrote them via Onager (master predates the
  fold), the same numbers constitute independent-engine agreement —
  the §8.1 standard itself. Either reading clears the rows.
- **(b) Criteria 1–2 met**: `tests/test_l1_betweenness.py` 4/4 green
  today — 16-node toy EXACT on all nodes (including the
  zero-attachment specials, now closed-form scored: the "all-zero"
  finding was pre-fix) and 30/30 random graphs vs brute force (the
  "3x cycle overcount" was pre-fix: the triple count was the
  non-disjoint-coverage bug).
- **(c) Criterion 4 met**: per (a) the rows equal the fixed fold;
  there is no stale pre-fix write to re-apply.
- **(d) Criterion 3 — COUNTERSIGNED**: the pre-restamp run gave a
  flat 1.000000 ratio (r=1.0, n=1,382, max 0.4380 = incumbent max;
  audit `/tmp/graph_divisor.txt` §10 FINAL RESOLUTION), and the owed
  re-run on the RESTAMPED generation completed 2026-09-23 late
  (`/tmp/q86_countersign.py`): fresh Onager via
  `bypass_centrality_cache()` (39.5 s, n=21,453) vs the fold
  normalized by its own apply-path formula (`info["endpoints"]` =
  21,453 unchanged by the intake) — ratio flat 1.000000 at rtol
  1e-9 (min=med=p10=p90=max) over ALL 1,924 both-positive nodes,
  r=1.000000, max 0.4380 on BOTH engines, and ZERO positive-only
  disagreements (no node positive in one engine and zero in the
  other). This is the §8.1-standard evidence on the current
  generation; nothing further is owed before the dispatch flip.
- **(e) Budget note**: `jobs=1` fold measured 8.8 s and 7.5 s in two
  runs today (vs 3.76–5.34 s recorded pre-restamp) — treat ~7-9 s as
  the current single-process figure when writing the activation
  budget (the earlier band is stale; `--jobs 4` remains the lever).

Activation remains a one-line dispatch change with its own
acceptance (§3 table) — evidence-complete and countersigned; the
flip itself is the operator's call.

### 8.4 Route-3 resolution: consent gate + exact top-K heap (2026-09-23 late)

Decision taken was BOTH, not either: the confirmation gate contains
the hazard on every surface, and the heap removes the cost for
every bounded call. (On "threading": no threads anywhere — the word
meant parameter plumbing. `allow_all_pairs` is a plain boolean
passed `onager_link_prediction()` ← `algorithms.link_prediction()`
← `suggest_relations()` ← both CLIs (each exposing
`--allow-all-pairs`); the `/api` endpoint refuses outright instead
of threading it. The heap is single-process; the 0.83 s witnessed
is interpreter boot + ~34 ms materialization + ms-scale heap.)

- **Trigger measurement.** The consented positive path was witnessed
  before fixing: killed at the 590 s tool timeout with 8 GB+ resident
  (orphan reaped clean, machine healthy). The old ~60 s figure is
  stale at VIGIL scale — all cost strings now state 10+ min / 8 GB+.
- **Incidental bug found by the witness run.** The DB-path
  pref-attach query was not just slow but BROKEN (binder error):
  the shared `{name_joins}` used the 2-hop branch's `s` alias while
  this branch aliases its subquery `p` — rotted while "rarely used".
  Fixed with per-branch aliases (`pairs_joins`); the first fix
  attempt broke the 2-hop branch the other way and the existing CLI
  tests caught it the same run.
- **Heap design** (`_pref_attach_topk`, `onager.py`): score factors
  as `deg(u)*deg(v)`, so nodes in degree-desc order over a running
  min-heap keyed `(score, -lo, -hi)` — inner/outer breaks fire only
  on strictly-smaller scores (the heap minimum only rises),
  ties expand, trim by `(score DESC, lo, hi)` identical to the
  extension's `ORDER BY`. Degrees from the symmetrised deduped
  projection (the 2-hop SQL's own definition). `top=None` resolves
  to `PREF_ATTACH_COMPUTE_FLOOR` (5,000); unbounded output stays
  refused — ~243M pairs is infeasible by nature, no engine fixes
  output size. Not numpy-dense (the outer product would be 486M
  entries / 3.9 GB — the dense wall again): pruned Python beats the
  C++ extension by refusing to enumerate.
- **Persistence shape change** (`_persist_link_prediction`):
  pref-attach persists per-node top-100 (`PREF_ATTACH_STORE_CAP`;
  the API serves top≤100); jaccard & co. keep every positive,
  unchanged. The old "persist everything" contract never applied
  to a method whose positive set is the whole non-edge space.
- **Proof.** Heap == raw extension pair-for-pair on a 130-node
  store carrying a self-loop and duplicate rows (degree semantics
  INCLUDING edge cases, plus sortedness); DB-path name-keyed
  regression test; CLI refusal (rc 2 pre-compute); live
  `link-predict --method pref-attach --allow-all-pairs --top 5`
  dry-run: 0.83 s, hub–hub degree products as theory demands.
- **Deliberate gaps.** No e2e test for the `suggest_relations`
  CLI flag or the API 400 (no harness/sixty-second budget —
  threading is one line each, review + ruff cover it); no live
  `--apply` of pref-attach witnessed (dry-run only — persisting
  would overwrite the jaccard table wholesale per the UPSERT
  contract, operator's call if ever wanted).

### 8.5 Route-4 resolution: stamp diet by census (2026-09-23 late)

- **Census.** `v_centrality_*` has exactly one writer (the explicit
  stamp lane) and zero request-time readers: low-level centrality
  functions are consumed only via `_run_*`/compute/CLI, the stamp
  writer itself, and tests; `query.py`'s score-metric list IS the
  stamp writer; `app.py` serves persisted `graph_analytics` rows.
  The tables accelerate CLI compute calls and test fixtures —
  nothing else.
- **Diet.** `stamp_centrality_cache` skips lane-served metrics via
  a ROUTING-derived set (reverting a value resumes its stamp, no
  other change); skipped tables are DROPPED so no stale scale
  lingers. Remaining seven stamp natively in ~8 s total (measured
  in-test) — parallelization rejected as unnecessary post-skip.
  Low-level `_cached_central_scores` calls stay (ephemeral-tolerant;
  a hand stamp still serves). Help strings updated (Makefile,
  query CLI); dated "ten tables" comments left as history.
- **Tests.** Count assertions updated to pin the diet (exact
  seven-set + absence assertions for the three skipped); the
  stamp==fresh invariant now covers Onager-native metrics only.
