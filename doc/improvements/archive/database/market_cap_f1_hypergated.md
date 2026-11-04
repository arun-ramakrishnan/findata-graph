---
title: "F1 market_cap conflict + hypergraph gated lanes — DEFERRED 2026-11-05"
status: deferred
filed: "2026-11-05"
executed: "2026-11-05"
completed_md: "480"
area: "helpers/core/db.py, helpers/graph/, app.py"
---

# Proposal — F1 + hypergraph gated items (2026-11-05) — DEFERRED

**Date:** 2026-11-05 · **Status:** DEFERRED 2026-11-05 (completed.md
entry 480) — S4 one-shot QA banked; S5/S6 parked. No code owed.

> **Deferred 2026-11-05 — S4 banked, S5/S6 parked.** S4 (giant-k
> probe) executed as one-shot QA and banked: `bench_data/hyper_giantk_probe/`
> (`S4_PROBE_REPORT.md` + `s4_probe_20261105-102753.json`) — `country`/`index`
> already load at defaults, `--k2-cap` is the admission guard not `max_k`,
> lifted `max_k` 60→850 costs ~0.1 s extra, `commutes=False` everywhere, trail
> lane linear (103,108 arcs). No production consumer is named; no lane is
> wired. S5 stays gated on an undefined disagreement metric (`sweep_k`
> `allow_giant` is dead; no live-family ARI variant); S6 stays parked as only
> a prerequisite for S5's harness (`hyper_community_probe.py:38` sklearn ARI)
> and HySC fit — no lane needs it while S5 is parked.
>
> **One-line summary (post-review correction 2026-11-05).** Split into two
> arcs: F1 is a monitoring note (conflicts already 0, guard already wired);
> the hypergraph half is a re-specified probe (order-cap, not size-cap;
> undefined disagreement metric; S6 is a local install, not an upstream
> wait). Detail in the dated correction notes below.
> **Correction 2026-11-05 (post-review, read-only re-check) — §3.1 premise is stale.**
> The 41 conflicting `market_cap/*` companies were fixed at the SOURCE on
> 2026-08-05 (`findata_corpus_audit.md:126` "C2-FIX-RESOLVED"); the archived
> ACTIVE claim at `sql_query_improvements.md:4` is stale — the same file at
> `:39` records "Data is clean TODAY." Verified 2026-11-05: source
> `findata/**/*.md` scan (1,212 notes carry a market_cap value, 0 with >1
> distinct value) and live DB (`conflicts 0`, `market_cap_rows 1129`). S3 is
> already satisfied: `check_market_cap_conflicts` is registered as an
> ERROR-severity `Check` (`database_integrity_check.py:126-178`) wired into
> `make qa` via `tests/run_gate_report.py`, plus `/api/graph/stats`
> `conflicting_market_cap` (`app.py` CTE) and green guards
> (`tests/test_sql_query_correctness.py`, `tests/test_validators.py`,
> `tests/test_database_integrity_check.py`, `tests/test_api_graph_unit.py`).
> S1/S2 quarantine is NEW work (the `dedupe_market_cap_tags.py` remediation
> tool no longer exists), and the acceptance criterion must not rewrite
> archived history to satisfy a grep. §3.1 is retained below with corrected
> framing; implement only as monitoring/annotation.
>
> **Correction 2026-11-05 (post-review, read-only re-check) — §3.2 mechanism note.**
> `max_k` in `helpers/graph/hyper_spectral.py:70` `load_family(edge_type,
> *, max_k=60, k2_cap=300)` is the ORDER cap on Laplacian orders
> (`orders = sorted(laps)[:max_k]` at `:132-135`), not a hyperedge-size
> filter: it appears once in `load_family` (the signature) and filters
> nothing. The only size/count guard is `k2_cap` on the COUNT of k>=2 edges.
> Measured 2026-11-05 (read-only): `country` (10 k>=2 edges, 919 nodes, max
> edge 850) and `index` (57, 1,274 nodes, max edge 752) already LOAD at
> defaults; `group` (506), `subsidiary` (451), `supply` (709) RAISE. The two
> named probe families need no "lift"; the k>=2-count families need
> `--k2-cap`. Spectral cost on the named families is seconds (country max_k
> 60→850: 3.0→2.9 s, peak RSS 173-179 MB; index 4.7→4.6 s; higher orders
> are empty: nnz 1→12, 2→9, 3→48, 4-5→0). The "6.8× trigger" is a
> STORE-GROWTH ratio (11,693/1,708 hyperedges) that FIRED the 2026-11-02
> re-probe (`hgx_unlock_shelf_probe.md:173-196`), not a runtime budget.
> Trail-lane cost is linear in incidence mass (103,108 arcs; country-850
> edge = 850 arcs = 1.65%), with no max_k knob. See §3.2 S4/S5/S6.

## 1. Motivation

This proposal covers the two clusters the SQL investigation surfaced via
`master_query` / `convo_query` / `ripwire` (not grep):

- **F1 production-active:** `doc/improvements/archive/database/sql_query_improvements.md:1`
  — *"F1 is ACTIVE in production — 41 live companies carry conflicting
  market_cap/* tags, so the MIN fix matters now, not just latently."*
  `master_query "market_cap conflicting F1"` also hits
  `doc/improvements/archive/pipeline/findata_corpus_audit.md:60`
  — *"the v4 LEFT JOIN entity_tags fanned out for 41 companies that had
  MULTIPLE conflicting `market_cap/*` tags"*. `master_query` script leg hits
  `tests/test_sql_query_correctness.py` (SQL-builder correctness — the
  silent-wrong-answer layer) and `tests/fixtures/seed_research_db.py`
  (seeded DB exercising `market_cap_sql()` MIN tie-break F1 and F2).
  `convo_query "market_cap conflict F1 MIN(t.tag)"` expands to
  `memory/data/harness/prime-rlm/conversations/019ff54d-5816-70fc-b9e1-672d423fbb80.parquet:619`
  — *"market_cap_sql DOES contain MIN(t.tag) (line 94) … So F1 IS APPLIED"*
  (verified against live file, stale in-memory var corrected). `ripwire --grep=market_cap_sql --grep-in=any`
  hits: `helpers/core/db.py:115 def market_cap_sql`, `helpers/core/db.py:131`
  SELECT name, {market_cap_sql()} …, `helpers/maintenance/migrate_to_graph_edges.py:51`
  sourced from entity_tags, `tests/test_db.py:22 test_market_cap_sql_default_alias`,
  `tests/test_sql_query_correctness.py:48 test_market_cap_sql_contains_min`.

- **Hypergraph lanes:** `master_query "hypergraph motifs NO-GO reducibility community"`
  hits `doc/improvements/archive/graph/hgx_over_dyads.md:359` — *"No production
  motif lane. The NO-GO stands; S5 corrects its stated cause, it does not reopen
  it."*; `doc/improvements/completed.md:12787` *451. HGX unlock-shelf probe —
  motifs NO-GO, reducibility measured, four lanes re-gated*;
  `doc/improvements/archive/graph/hgx_unlock_shelf_probe.md:48` Probe 1 motifs
  census NO-GO. `master_query "giant hyperedge country 850 index 752 max_k"`
  hits git `c355d698` — *"max_k ≤ 60 and k≥2 edges ≤ 300 (the giant-k families —
  country 850, index 752 …)"*; `convo_query` hits DSH session
  `session-fe99aae2-330f-4332-8cc2-bffbfb9630e5.parquet:47` trail-scaling
  discussion. `ripwire --for="hypergraph trail bipartite BFS"` hits
  `helpers/graph/hyper_trails.py:297 _trail_bfs`, `helpers/graph/hyper_spectral.py:54 _require_hgx`,
  `helpers/graph/hyper_communities.py:59 _require_hgx`, `helpers/graph/hyper_centralities.py:59`,
  `helpers/graph/hyper_predict.py:59`.

## 2. Evidence (live, measured)

| Item | Evidence | Verdict |
|---|---|---|
| F1 code fix | `helpers/core/db.py:115 market_cap_sql` uses `MIN(t.tag)`; `tests/test_sql_query_correctness.py:53 test_market_cap_sql_deterministic_for_conflict` | APPLIED (green guard) |
| F1 data | `sql_query_improvements.md:4` 41 companies conflicting tags — STALE; corrected at source 2026-08-05 (`findata_corpus_audit.md:126`); same file `:39` "Data is clean TODAY"; 2026-11-05 live: 0 conflicts, 1,129 rows, 0/1,212 source notes ambiguous | Remediated 2026-08-05; regression guard exists |
| Motifs | `hgx_over_dyads.md:359` NO-GO stands; `hgx_unlock_shelf_probe.md:48` | CLOSED, do not reopen without new data |
| Reducibility | `completed.md:12787` #451 reducibility measured | Measured, one-shot QA |
| Community lanes 2-4 | `pending.md:509` gated on hy-MMSBM block-disagreement QA; `helpers/graph/hyper_communities.py` | Gated — gate metric undefined (see S5) |
| Giant-k families | country 850, index 752, group 230, subsidiary 227, supply 222 max_k (pending.md:445-460) | Partially probed 2026-11-05: country 10 k>=2 edges LOAD (919 nodes, max edge 850, 0.34 s), index 57 LOAD (1,274 nodes, max edge 752, 0.13 s); group 506 / subsidiary 451 / supply 709 RAISE the `--k2-cap` guard (needs `--k2-cap`) |
| BFS h_incidence | `hyper_trail_bipartite_bfs_lane.md` 5.1 ms/source, 800 sources, 18,233 entities, 166.8 s full map | LANDED (#473) |
| Spectral/RW lanes 3+5 | `hgx_shelf_spectral_rw_measurements.md` #472 `helpers/graph/hyper_spectral.py` | LANDED |
| Hypergraphx[ml] | `pyproject.toml:103` lists plain `hypergraphx`; HySC needs the sklearn extra (`hy_sc/model.py` imports `sklearn.cluster.KMeans`); `hyperlink_communities` load-path mismatch (see S6) | Local action: `uv pip install 'hypergraphx[ml]'` (5 packages, sklearn 1.9.1) |
| S-walk paths | `pending.md:498` reclassified to hyper_trail_bipartite_bfs_lane | Owned by #473 |
| C2 supply-chain | `sql_query_improvements.md` C2 — 5 supplier_to edges → 0 pairs | Data-gated |
| C5 weighted path | `sql_query_improvements.md` C5 — all 4,107 edges weight=1.0 | MOOT until weights populated |
| S5 personalized PageRank | `pending.md:666` Onager personalisation column ignored, restart hardcoded node_id 1 | Watch / parked |
| D9 directed weighted / maxflow | retired unserved after igraph pilot deleted 2026-09-13 (`graph_design.md:472`) | RETIRED, needs own proposal |

## 3. Design

### 3.1 F1 — keep the MIN guard; conflicts already remediated 2026-08-05

The code fix (MIN) is applied (convo 019ff54d:619). The 41 conflicting
companies were fixed at the SOURCE on 2026-08-05 (`findata_corpus_audit.md:126`
"C2-FIX-RESOLVED"; remediation tool was `helpers/maintenance/dedupe_market_cap_tags.py`,
since removed). Do NOT re-run a "clean sweep" — there is no data leg left.

1. S1 — monitoring only: add a dated annotation to
    `doc/improvements/archive/database/sql_query_improvements.md` (repo
    convention, e.g. `C2-FIX-RESOLVED`) recording that the `:4` ACTIVE claim
    is superseded by `:39` / the 2026-08-05 fix. Do NOT rewrite archived
    history to satisfy a grep.
2. S2 — `sync_tags` reconciliation is NOT a bounded task: "dup quarantine"
    (debris-entity pattern) is new work, and two different market_cap values
    in one note remain two tags by design. Not started; if ever
    re-triggered, file as its own proposal.
3. S3 — already satisfied, verify only: `check_market_cap_conflicts`
    (`database_integrity_check.py:1412`) is an ERROR-severity `Check`
    (`:126-178`) wired into `make qa` via `tests/run_gate_report.py`
    (`Step("integrity_check", ...)`), plus `/api/graph/stats`
    `conflicting_market_cap` (`app.py` CTE). No `make integrity` target
    exists; the acceptance command
    `python3 helpers/misc/database_integrity_check.py market_cap`
    ignores argv (main() is flag-less) and runs the whole suite — a
    0-conflicts report on the current DB is the verification.

Acceptance: `python3 helpers/misc/database_integrity_check.py` reports
market_cap conflicts = 0; `tests/test_sql_query_correctness.py` green;
dated annotation added. (No criterion about `master_query` output.)

### 3.2 Hypergraph — do not wire motifs; probe giant-k + community QA

1. S4 — giant-k probe: spectral/community lanes cap at `max_k ≤ 60`
    (git c355d698) — but `max_k` is the ORDER cap on Laplacian orders
    (`orders = sorted(laps)[:max_k]`, `hyper_spectral.py:132-135`), not a
    hyperedge-size filter: in `load_family(...)` it appears once, in the
    signature, and filters nothing. The guard is `k2_cap` on the COUNT of
    k>=2 edges. Probe country 850 / index 752 families on
    `helpers/graph/hyper_spectral.py` with `--max-k` lifted (order cap
    only) and the k>=2-count families with `--k2-cap`. The bipartite lane
    (`helpers/graph/hyper_trails.py`) has NO family/max_k knob and costs
    linear in incidence mass (103,108 arcs; the country edge = 850 arcs =
    1.65%), so there is nothing to "lift" there — its 5.1 ms/source
    baseline (`hyper_trail_bipartite_bfs_lane.md:46-47`) is descriptive
    only; do not gate a seconds-long spectral probe on a per-source BFS
    budget. Measured cost: country max_k 60→850 = 3.0→2.9 s (peak RSS
    173-179 MB); higher orders are empty (order nnz 1→12, 2→9, 3→48,
    4-5→0). The "6.8× trigger" is a store-growth ratio (11,693/1,708
    hyperedges) that already fired the 2026-11-02 re-probe
    (`hgx_unlock_shelf_probe.md:173-196`), not a runtime budget. One-shot
    QA output, no production wire unless a probe result has a named
    consumer.
2. S5 — community lanes 2–4: `helpers/graph/hyper_communities.py` exists,
    but the "block-disagreement QA" gate is undefined: `sweep_k`
    (`hyper_communities.py:178`) reports only log_likelihood/bic/
    effective_blocks/fit_s, and its `allow_giant` parameter is DEAD inside
    (filtering happens upstream in `_cli`/`_exclude_degenerate`).
    Before any production lane: define the disagreement metric (which two
    methods disagree, at what threshold, on which families) — the only
    existing second-opinion harness is
    `helpers/bench/hyper_community_probe.py` (ARI proj-vs-hgx via
    `from sklearn.metrics import adjusted_rand_score`, banks
    `ari_methods`/`ari_proj_truth`/`ari_hgx_truth` under
    `bench_data/hyper_community_probe/`, e.g. `WALL4_PROBE_REPORT.md`;
    seeded synthetic planted hypergraphs only — no live-family
    variant exists). Note: with `allow_giant=True` the size-850 country
    edge collapses HyMMSBM to one block (894/919 nodes), per the
    `_exclude_degenerate` docstring (`hyper_communities.py:80`) — giant
    families need the giant-allowed path to be a defined arm of any
    disagreement comparison. `helpers/bench/hyper_community_probe.py`
    needs the sklearn install from S6 to run in this venv.
3. S6 — local unblock, NOT an upstream watch. `hypergraphx[ml]` is a
    5-package installable extra (dry-run 2026-11-05: cloudpickle, joblib,
    narwhals, scikit-learn 1.9.1, threadpoolctl). HySC needs only that
    extra (`hy_sc/model.py` imports `sklearn.cluster.KMeans` with a
    `KMeans = None` fallback); HypergraphMT's `model.py` has no sklearn
    import. The hyperlink_communities "pickle bug" is an upstream
    exception-type mismatch (`load_pickle` in
    `hypergraphx/readwrite/io_pickle.py` wraps any failure as
    `ReadwriteError`, never `FileNotFoundError`, so the caller's
    `except FileNotFoundError` fallback at
    `hyperlink_communities.py:62` never fires and surfaces as
    `Failed to load pickle 'None.hlcd'`); the save path has a second
    defect (`save_pickle` requires `expose_data_structures`, which a
    numpy array lacks). A tiny local shim (make `load_pickle` raise
    `FileNotFoundError`, make `save_pickle` a no-op) unblocks the
    fallback computation path — the density wall (the |E|² array, ~1.1 GB
    fp64 at 11,693 edges) remains the real blocker. Recommended action:
    add `hypergraphx[ml]` to `pyproject.toml:103` instead of plain
    `hypergraphx`, OR just `uv pip install 'hypergraphx[ml]'`
    (uv 0.9.0 manages this venv; per doc rule, never bare pip into a
    uv-managed venv). No workaround lane for the density wall — the n²
    design is dead twice over.

Ruled out, do not re-audit: library switch for `dict_to_df` (no fixed fork,
PyPI checked 2026-11-02); waiting upstream for motifs (S5 corrected the
cause, NO-GO stands); clique-projection BFS (materializes the 2.45M dyad
OOM wall).

## 4. Gated / parked — future arcs

| # | Item | Status | Trigger / owner | File |
|---|---|---|---|---|
| F1-data | 41 conflicting market_cap tags | remedied 2026-08-05; regression guard | integrity = 0 conflicts (wired into make qa) | this proposal §3.1 (monitoring only) |
| C2 | `supply_chain_concentration()` | GATED on denser data (5 supplier_to, 0 pairs) | supplier_to/customer_of fill-in | `sql_query_improvements.md` C2 |
| C5 | weighted shortest path | MOOT — all edges weight=1.0 (4,107) | weight population (data task) | `sql_query_improvements.md` C5 |
| P2.2(ii) | fold 12 EDGE_REGISTRY CTAS + e_all_und/e_dir | HOLD — 1.70 s fixed overhead, measurement-gated | revisit measurement | `pending.md` P2.2 |
| P2.2(iii) | dirty tracking via per-input hashes | PARKED | `_build_meta` rebuild scope | `query.py` |
| P2.2(C) | `rebuild()` from ingest lanes | PARKED — no ingest lane calls it; first query pays inline | ingest-lane ownership | `query.py` |
| D9 | directed weighted shortest path / maxflow_mincut | RETIRED unserved (igraph pilot deleted 2026-09-13) | own proposal to revive | `graph_design.md:472` |
| S5 | personalized PageRank | WATCH — Onager ignores personalisation, restart = node_id 1 | Onager honours column | `pending.md:666` |
| BFS h_incidence | bipartite BFS 5.1 ms/source | LANDED (#473) — S3 waits on first path ask | first UI/API path ask | `hyper_trails.py` |
| S-walk | hyperedge-traversing shortest paths | GATED — first path-explanation ask | UI/API ask | `pending.md:498` |
| Motifs | order-3 census | NO-GO (visibility defect, not sparsity) | new data density | `hgx_over_dyads.md:359` |
| Reducibility | measured | ONE-SHOT QA | none | `completed.md` #451 |
| Community 2–4 | hy-MMSBM lanes | GATED | block-disagreement QA metric undefined (see S5) | `hyper_communities.py` |
| Giant-k | country 850, index 752, group 230, subsidiary 227, supply 222 | Partially probed 2026-11-05 — group/subsidiary/supply blocked by `--k2-cap` (count guard), not by max_k | S4 probe (this proposal) | this proposal §3.2 |
| HySC / HypergraphMT | `hypergraphx[ml]` missing | LOCAL ACTION (not upstream blocked) — `uv pip install 'hypergraphx[ml]'` | venv install | S6 (this proposal §3.2); canonical owner: `pending.md` 452-454 |
| hyperlink_communities | pickle exception-type mismatch | LOCAL SHIM possible; density wall stands | `load_pickle`/`save_pickle` shim | S6 (this proposal §3.2) |

## 5. Gate reference

`doc/procedures/gates.md`: advisory (`make search-fresh`) at start; targeted
tests only (not full `make pytest`); operator stages/commits. No full gate
until the operator's go on an arc. This proposal is advisory until then.
