---
title: "Scipy personalized PageRank — ship the deferred exact-solve lane (CLI + API + dispatch)"
status: executed
filed: "2026-11-06"
area: "helpers/graph"
executed: "2026-11-06"
completed_md: "484"
---

# SciPy personalized PageRank — ship the deferred exact-solve lane

**Date:** 2026-11-06 · **Status:** EXECUTED 2026-11-06 (trial live: CLI + API + dispatch + `--all-ppr-top50`; persistence via `ppr_results` — see `../database/ppr_results_table.md` (sibling archive), same-day projection-evolution record) · **Archived:** 2026-11-06, completed.md #484 (replaces the stale 2026-09-23 deferral copy at this path; its §7 gate record preserved below)

The `personalized_pagerank` child of `scipy_graph_bridge.md` was
DEFERRED 2026-09-23 on two grounds. Both are now resolved:

1. **Onager bug (the filed reason)** — `onager_ctr_personalized_pagerank`
   ignores its personalisation column and hardcodes the restart to
   `node_id 1`. Re-probed on the rebuilt custom extension
   (build `ab472a7`, 2026-10-13): still live — pers→node2 vs node4
   byte-identical (peak 0.3022 at node 1), and a projection without
   node 1 raises `Invalid Input Error: Personalized PageRank failed:
   Invalid argument: Personalization node 1 not found in graph`.
   The vendored build (`vendor/onager`, CogitatorTech/onager v0.5.0)
   changed nothing here: it is the DuckDB 2.0.0 API port, and the
   personalised-pagerank algorithm is untouched by it. The scipy
   exact solve sidesteps the bug entirely.
2. **"1-hop ego" degeneracy** — CORRECTED 2026-10-31 by the re-probe
   (#409, `archive/graph/ppr_reprobe_economic.md`): PPR is
   **seed-dependent, not universally degenerate** (Infosys hop≥2
   0.038 ego-local; HDFC Bank hop≥2 0.508 propagates).

What remains of the deferral: the lane needs a **named consumer** and
a **projection that fixes seed-dependent localization**
(hub-stripped / per-sector subgraph). This proposal defines both:

- **Named consumer:** `GET /api/graph/personalized-pagerank?seed=<entity>`
  — a read-only "who is related to X, by random-walk proximity" ranking.
  This is the user-visible surface the ego bundle
  (`/api/graph/neighbors`) cannot serve: neighbors returns the direct
  ego set; PPR ranks the whole projection by walk proximity, which is
  where multi-hop structure (the HDFC-Bank-style propagating seeds)
  shows up.
- **Fixing projection:** the centrality projection (`listed_on_index`
  excluded — `load_centrality_projection`'s rule) on the undirected
  CSR, same as the Katz/eigenvector exact lanes. Hub contamination
  (india/nifty membership nodes) is already gone from post-seed top-10
  on the live graph; the projection keeps it that way.

Operator surface decision (2026-11-06): **CLI + API + dispatch** —
`scipy_bridge.py pagerank-personalized --seed X` (persist via
`--apply` under `pagerank_personalized`), a read-only API route, and
an `algorithms.compute('pagerank_personalized', seed=...)` dispatch
entry.

## 1. TL;DR

``x = (I - alpha*P^T)^-1 (1-alpha) v`` with ``P = D^-1 A`` row-stochastic,
``v`` one-hot on the seed — exact, one sparse solve. Resurrects the
personalized-PageRank lane Onager dropped.

## 2. The algorithm

Identical to the deferred record
(`archive/graph/scipy_personalized_pagerank.md` §2), with the
**implementation trap** from §7 respected: the stationary form is
``x = alpha*P^T x + (1-alpha) v``, i.e. solve ``(I - alpha*P^T)``.
Using ``(I - alpha*P)`` does not normalise (sum ≈ 127, not 1) — wrong
eigenvector orientation.

- `v`: one-hot on the seed (required). Dangling rows teleport to `v`.
- alpha default 0.85.
- `x` sums to 1: scores are comparable across seeds on the same graph.

## 3. Design

### 3.1 `helpers/graph/scipy_bridge.py`

- `PPR_METRIC = "pagerank_personalized"`; `PPR_LANE = "pagerank_personalized"`;
  `PPR_ALPHA_DEFAULT = 0.85`.
- `personalized_pagerank_scores(A, names, seed, alpha=0.85) -> np.ndarray`:
  - P = D^-1 A row-stochastic (dangling rows → teleport row to seed).
  - `spsolve(I - alpha*P^T, (1-alpha)*v)`.
  - Missing seed raises `KeyError` with a clear message (matches
    `yen_paths` / `max_flow` pre-flight posture).
  - `alpha` outside (0, 1) raises `ValueError`.
- `ROUTING["pagerank_personalized"] = "SCIPY"`;
  `LANE_BUDGETS["pagerank_personalized"] = 5.0`
  (measured ~0.22–0.29 s live, × ~20 headroom).
- CLI command `pagerank-personalized`: `--seed` (required), `--alpha`,
  `--top`, `--apply` (dry-run default; `--apply` UPSERTs under
  `pagerank_personalized` via `write_analytics`, same as the exact
  lanes). Reuses `load_centrality_projection` + `_run_exact_lane`
  shape; contract check is **skipped** (a seed-conditioned relative
  metric has no contract population — the payload is the full
  projection, filtered to finite/positive mass on read).

### 3.2 `helpers/graph/algorithms.py`

- `_run_pagerank_personalized(con, *, seed=None, alpha=0.85, edges=None, db_path=None, **_)`
  → `{entity_name: float}` over the full projection (finite, >0 mass).
  `seed` is required; missing seed raises `ValueError` (unknown metric
  raises `ValueError` — contract surface).
- Register `"pagerank_personalized": _run_pagerank_personalized` in
  `_METRIC_DISPATCH`; add it to the `compute()` docstring list.
- The handler runs the scipy lane over SQLite `graph_edges`
  (db_path-aware, same as the closeness/harmonic DB path); NOT via
  `onager.py` (the extension cannot serve it).

### 3.3 `app.py`

- `GET /api/graph/personalized-pagerank?seed=<entity>&alpha=0.85&top=20`
  → `{"seed": ..., "alpha": ..., "n": <projection size>, "results":
  [{"entity": ..., "score": ...}, ...]}` ranked desc by score.
- Read-only, no persistence; `alpha` clamped (0, 1), `top` bounded
  (1..100). Missing seed → 400 with clear message (unknown seed name
  surfaces the lane's `KeyError` as 400, not 500).

### 3.4 Contract/test seams

- The metric is **opt-in** (no stamp lane, no `graph_analytics_contract`
  seed): first `--apply` creates the rows; `write_analytics` UPSERT
  semantics apply (no `replace_population` — each apply is one seed's
  relative ranking, not a population).
- Fresh-store behavior: `load_centrality_projection` on an empty store
  raises the same loud error the exact lanes raise.

### 3.5 Trial batch in `make recompute-graph` / `maint.py` (operator: Nifty-50)

`--all` cannot carry `seed` (mutually exclusive with any explicit
command — `p.error("--all cannot be combined with an explicit
command")`, `helpers/graph/algorithms.py:1424`), so PPR cannot ride
the per-metric loop directly.

Trial design (`make recompute-graph` and `maint.py` share the same
command, so one change covers both):

- Add a `--all-ppr-top50` scope on the algorithms CLI (separate from
  `--all`): iterates 50 off-the-shelf solves over the centrality
  projection and persists each under `pagerank_personalized`, keyed
  by seed in `graph_analytics` (UPSERT; no `replace_population`).
- Seed list: the DB has 389 `market_cap/large_cap` companies, 258
  India-listed (`listed_in = india`). The trial cap is 50 — by
  definition Nifty-50 are large-cap; no numeric market-cap ranking
  exists in the DB (buckets only), so the 50 are the India-listed
  large-cap set (sampled alpha-order today; a curated top-50 list
  is the follow-up).
  *(Review BLOCKER-1, 2026-11-06: alpha-order = A–D-biased prefix,
  not a sample — see §5b for the disposition: `--seeds-file` shipped,
  uncurated runs self-label, curated NSE list pending operator.)*
- Guard: skip a seed with no edges in the projection (loud warn,
  not a silent empty ranking). Seed set is auditable — the persisted
  rows record which 50 seeds were run.
- Cost: 50 × ~0.25 s = ~12.5 s serial (within the `--jobs 4` heavy
  pool it's parallel — same budget accounting as L1B/scipy fork
  pool). `make recompute-graph` stays the batch persistence path.

## 4. Acceptance

1. **Hand oracle** (a–b path, seed a, alpha=0.85): x_a = 0.5405,
   x_b = 0.4595; sums to 1 (`archive/.../scipy_personalized_pagerank.md` §2).
2. **Dense oracle** (numpy `solve` on a small synthetic graph): exact
   match to `spsolve` over the same matrix (rtol 1e-6).
3. **Seed conditioning**: two different seeds give different argmax
   scores (seed mass follows the seed); missing seed raises.
4. **Normalisation**: sum(x) == 1 within 1e-9 (the transposition trap
   is a regression test, not just a comment).
5. **CLI**: dry-run prints top-N; `--apply` persists under
   `pagerank_personalized`; `tests/test_scipy_bridge.py` additions green.
6. **Dispatch**: `algorithms.compute('pagerank_personalized', seed=...)`
   returns `{name: score}`; contract dispatch surfaces unknown metric
   (unchanged) and missing seed as `ValueError`.
7. **API**: `GET /api/graph/personalized-pagerank?seed=<known>` returns
   ranked results; unknown seed → 400; alpha/top validation → 400.
8. **Routing**: `ROUTING` covers `pagerank_personalized`;
   `test_routing_table_covers_known_metrics` extended.
9. **Targeted tests only** (house rule — never a full pytest sweep).

## 5. Non-goals / risks

- No personalisation over multi-seed vectors (one-hot on `--seed` only).
- No directed PPR (undirected CSR, same as the other exact lanes).
- The `pagerank_personalized` rows in `graph_analytics` are a cache,
  not a contract: `compute()` never reads them; they exist only for
  the CLI `--apply` audit trail. (Superseded same day: multi-seed
  writes moved to the seed-keyed `ppr_results` table — see
  `../database/ppr_results_table.md` §3–§4, 2026-11-06.)
- `onager.py` NOTE + `graph_design.md §5.5` + `pending.md` N5-6 stay —
  they record the Onager bug correctly; this proposal adds a lane
  **beside** Onager rather than wrapping it.

## 5b. Review outcomes (operator, 2026-11-06 evening)

**BLOCKER-1 — trial seed set is alphabetical, not curated.** §3.5's
"sampled alpha-order" is the first 50 of 258 India-listed large_cap
candidates by `ORDER BY entity_name` — an A–D-biased prefix
(360 ONE WAM → CRISIL), not a Nifty-50. **Disposition — fixed in
code same day:** `--all-ppr-top50` accepts `--seeds-file PATH`
(one name per line, `#` comments ignored) so a curated constituent
list can replace the prefix; without it the run now LABELS itself
`alpha-prefix sample (A–D bias, NOT curated)` in the output. The
silent-shrink part of the finding also fixed: the summary reports
`solved / skipped of listed` explicitly and echoes skipped names —
`applied=len(seeds)` could read "50" while the edgeless guard ran
fewer. Curated NSE list itself is operator-supplied (store-validated;
`Reliance Industries` verified present). Status: mechanism shipped,
list pending operator.

**MAJOR-1 — Onager bug sidestepped, not fixed; localization blocker
unaddressed.** Half-true on both counts.
(a) *Sidestep by design*: the lane exists BECAUSE the Onager bug can't
be fixed without upstream (§1; wrap stays forbidden) — recorded, no
change. (b) *"projection unchanged"* is now stale: the same-day
projection passes (archived `ppr_results_table.md` §7) ARE the
hub-stripped projection the revival gate asked for — `listed_in`
country hub, `co_mentioned_in` volume, `invested_in` investor nodes,
sector/theme/regulator hubs all out (include-only 8-type relational
set). (c) The localization CONCERN numerically stands: on the
relational projection Infosys holds **0.4257 self-mass** (vs HDFC
Bank 0.163) — seed-localization is seed-dependent and Infosys is the
ego-local case. The gate's remaining arm — per-sector subgraph —
stays OPEN in §7 below (the 2026-09-23 deferral record, preserved);
no un-deferral of that remainder.

## 5c. Per-sector subgraph slice — hybrid (operator decision, 2026-11-06 evening)

Operator picked **record + implement now (hybrid)** for the revival
gate's localization arm (the pure-sector option was declined: a hard
cut deletes cross-sector structure — CRISIL → Oracle Financial
Services Software was the trial's best output).

**Semantics**: solve on the seed's `sector_classification` cohort
**∪** the seed's direct relational neighbours (either direction,
`PPR_INCLUDE_EDGE_TYPES` only). Sector mass cannot leave the cohort;
genuinely cross-sector counterparts survive via the direct-edge arm.
No-sector seeds fall back to `{seed} ∪ neighbours`.

**Surfaces**: `sector_cohort()` in `scipy_bridge.py`; CLI
`pagerank-personalized --sector` (scipy_bridge + algorithms);
`compute("pagerank_personalized", seed=..., sector_scope=True)`;
`GET /api/graph/personalized-pagerank?seed=...&sector=1`. The
`--all-ppr-top50` batch stays GLOBAL by design (per-seed scoping is a
consumer-driven follow-up, not part of this slice).

**Measured (live store, 2026-11-06)**: HDFC Bank cohort 63 → top-8
pure banks (seed 0.192, DCB 0.032, CSB 0.031 — Bharti Airtel retained
in cohort via direct edge, out of top-8); CRISIL cohort 94 keeps
Oracle Financial (Technology, direct `semantic_peer`) and Axis Bank
(Banking, direct) beside the Capital_Markets cohort; Infosys cohort
281 → pure IT-services top-8, self-mass 0.4455 (≈ the global 0.4257 —
sector purity is NOT de-localization: pruning weak ties concentrates
mass on the strong cluster. The localization REMAINDER stays with the
revival gate arm; this slice delivers sector-pure rankings).

**Tests**: `test_ppr_sector_scope_hybrid` (cohort membership, CLI
--sector excludes cross-sector non-neighbour, global solve includes
it). Suite 50 passed.

## 6. Gate reference

`doc/procedures/gates.md` full gates ONCE per arc on operator go;
fixes validated by targeted tests only.

## 7. Revival gate — preserved from the 2026-09-23 deferral record

*(The same-titled deferral doc that previously occupied this archive
path was a stale copy; operator-directed replacement 2026-11-06. Its
structural finding and re-anchored verdict are preserved verbatim
below — dispositions live in §5b/§5c.)*

**Onager bug (the filed reason).** `onager_ctr_personalized_pagerank`
ignores its personalization column and hardcodes the restart to
`node_id 1` (`onager.py:1099`, `graph_design.md:286`, `pending.md`
N5-6). scipy sidesteps this — but not the next one.

**Graph structure (the real blocker, 2026-09-24 measurement).** The
graph is star-dominated and leaf-heavy: **84.5% of nodes are degree-1
leaves** (`deg1=18,639 / 22,046`), `degmax=850`, top hubs are
membership/country nodes (`india` 850, NIFTY indices ~500-750). Seed
mass: Infosys ex-index 0.416 seed / 0.184 hop≥2; company-only 0.443 /
0.137. Implementation trap: the printed equation `(I − αP)⁻¹(1−α)v`
is transposed — solve `(I − αPᵀ)`; `(I − αP)` with `P = D⁻¹A` does not
normalise (`sum(x) ≈ 127`).

**Re-anchor (2026-10-14 probe).** 57,589 edges, ~40,000
ownership/supply. Hub contamination gone on the live graph (post-seed
top-10 all real companies); economic projection is where re-anchoring
lands. Infosys hop≥2 ≈ 0.038 but HDFC Bank hop≥2 = 0.508 — the
blocker is **seed-dependent, not universally degenerate**. Stripping
hubs removes *contamination*, not *localization*.

**Verdict (re-anchored):** stays DEFERRED. Revival requires BOTH a
named consumer AND a projection fixing seed-dependent localization
(hub-stripped / per-sector subgraph). *(Disposition 2026-11-06: lane
shipped via this proposal — §5b review outcomes, §5c the hybrid
per-sector slice; the localization measurement remainder is recorded
there.)*
