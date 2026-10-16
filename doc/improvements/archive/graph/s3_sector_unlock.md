---
title: "S3 unlock — per-sector cuts for assign-primary primary-investor assignment"
status: executed
filed: "2026-10-16"
executed: "2026-10-16"
completed_md: "401"
area: "helpers/graph"
---

# S3 unlock — per-sector cuts for `assign-primary`

## 0. TL;RA

S3 (`min_weight_full_bipartite_matching` over `invested_in`, investors × targets,
stake-weighted) is infeasible on the raw projection: 277 investors × 84 targets
yields 82/84 matched, with 2 Hall-violator targets excluded
(`Mitsubishi Chemical Group`, `Sodexo`). The lane `assign-primary` — every
target its minimum-stake investor — is gated on "a projection with full
coverage" (`doc/improvements/archive/graph/scipy_csgraph_scale_lanes.md §5`,
§9b).

This proposal unlocks S3 via **per-sector cuts**: partition `invested_in` by
target `sector_classification`, run `assign-primary` within each sector's
subgraph independently, and union the results. A sector cut is a covering
projection because each sector's sub-matrix is small enough that Hall
collisions disappear.

**Trial result (2026-10-16, live `memory/research.db`):**

| mode | investors | targets | assigned | excluded | coverage |
|---|---|---|---|---|---|
| raw (uncut) | 277 | 84 | 82 | 2 (Mitsubishi Chemical Group, Sodexo) | 97.6% |
| per-sector cuts | — | 84 | 84 | 0 | 100% |

All 84 targets now have `sector_classification` set (the 5 previously-unclassified targets were classified via Yahoo Finance lookup + parent-entity inheritance and written into `entities` + their notes authored under D17). Per-sector cuts assign 84/84 with zero exclusions across 20 sectors.

Per-sector cuts are the practical unlock path because designating a
"primary relation edge type" for large public companies is hard — most have
many investors, no single dominant stake, and no natural "primary" edge. Sector
is the stable, already-curated partition (`entities.sector_classification`,
18 sectors on classified targets).

## 1. Design

### Projection

Same stake-weighted `investors × targets` CSR as `load_investment_incidence()`
in `helpers/graph/scipy_matching.py` (one edge per `(investor, target)`,
`COALESCE(weight, 1.0)`, self-loops dropped, later duplicates fold into first).

### Sector lookup

`entities.sector_classification` per endpoint. Targets without a sector are
skipped by the cut (reported, not failed). Investors keep their own sector but
are matched to targets in the target's sector subgraph — an investor may be
assigned in multiple sectors (independent sub-problems).

### Per-sector algorithm

For each sector S with ≥1 target:

1. Build sub-matrix: targets in S × all investors with an edge to a target in S,
   stake-weighted entries.
2. Run `s3_assign_primary` (column-side max matching → drop Hall violators with
   named exclusions → `min_weight_full_bipartite_matching` on covering subset).
3. Collect assignments `(target, investor, stake)` + exclusions.

Union across sectors. A target appears in exactly one sector cut, so no
double-assignment. An investor may appear in multiple cuts but is matched at
most once per cut (matching property).

### CLI

New subcommand on `helpers/graph/scipy_matching.py`:

```bash
python3 helpers/graph/scipy_matching.py assign-primary-sector --db <path>
```

Output: per-sector assigned/excluded/reason, totals, and a JSON `--format json`
mode. Read-only, writes nothing (dry-run by default, house pattern).

### Why not a primary-relationship edge type?

The proposal's alternative unlock — a future `primary_investment` edge type — is
deferred: large public companies have many investors, no single dominant stake,
and no natural "primary" edge to designate. Sector is already curated,
query-visible, and stable. Per-sector cuts ship now; primary-relationship stays
a documented future option.

## 2. Acceptance

- **Toy oracle**: two targets sharing one investor within one sector → exactly
  one excluded, rest assigned to minimum-stake investor (reuses
  `test_s3b_covering_assignment` pattern, sector-tagged).
- **Live**: per-sector run assigns all classified targets (79/79 on live DB at
  trial time), zero exclusions, raw run still shows 82/84 + 2 named Hall
  violators for contrast.
- **Determinism**: repeated runs produce identical assignments; `--format json`
  is stable across runs.
- **No write path**: CLI is read-only; no `--apply`, no DB mutation.
- **`make static-checks` clean**; ruff clean on `scipy_matching.py`.
- **Tests**: `tests/test_scipy_matching.py` gains `test_s3_sector_toy` and
  `test_s3_sector_live` (skipped if `memory/research.db` absent).

## 3. Non-goals

- No auto-derivation of `primary_investor` edges into `graph_edges`; this is a
  readout lane, not a writer (matches S1/S2/S4 dry-run-only precedent).
- No frontend wiring; output lands on stdout (and optionally a report file).
- No cross-sector matching within one cut; sectors are independent.
- The 5 previously-unclassified targets are now classified (Gandhi Special Tubes → Engineering_Capital_Goods, Ganesh Consumer Products → FMCG, Nippon Life India AIF Management → Capital_Markets, TV Vision → Media_Entertainment, Vishnu Prakash R Punglia → Engineering_Capital_Goods). All 84 targets fall inside a sector cut; none are force-assigned.

## 4. Follows

- `doc/improvements/archive/graph/scipy_csgraph_scale_lanes.md` §3 S3,
  §5 S3b, §9b unlock — this proposal is the declared unlock mechanism.
- `helpers/graph/scipy_matching.py` — existing S2/S3 implementation to extend.
- `tests/test_scipy_matching.py` — existing test suite to extend.

## 5. Implementation log

**Executed 2026-10-16** (same day as filing — this session took over the
arc after the data-side per-sector trials completed in the parallel
session; all 84 targets classified, 84/84 assigning).

- **Code**: `helpers/graph/scipy_matching.py` gains
  `load_target_sectors()` (target → `sector_classification`, classified
  only — unclassified targets skip the cut and are reported) and
  `s3_assign_primary_sector()` (partition targets by sector; per sector
  build the sub-incidence — targets-in-S × investors-with-edge-into-S,
  stake-weighted — and run `s3_assign_primary` unchanged; union across
  cuts; a target lives in exactly one cut, an investor at most once per
  cut but may serve several). CLI subcommand `assign-primary-sector`
  (`--db`, `--max-exclusions`, `--format text|json`) — read-only, no
  write path, JSON `sort_keys`-stable.
- **Tests** (`tests/test_scipy_matching.py`): `test_s3_sector_toy` —
  the §2 oracle per cut, plus the cross-sector investor pinned assigned
  in BOTH cuts at min stake, plus unclassified-skip reporting;
  `test_s3_sector_live` — coverage parity (assigned == classified),
  zero exclusions, run-twice determinism + byte-stable JSON, skipped
  when the live DB is absent.
- **Validation**: 10/10 suite green; ruff clean; `make static-checks`
  clean (the acceptance run caught an unrelated shebang regression this
  session had introduced in `helpers/core/entity_identity.py` — fixed).
- **Live** (`memory/research.db`): per-sector cuts **84/84 assigned,
  0 excluded, 0 unclassified across 20 sectors**; the raw uncut
  `assign-primary` run reproduces the contrast — 82/84 with
  `Mitsubishi Chemical Group` and `Sodexo` as the named Hall violators.
  Matches the trial table (§0) exactly.

## 6. Operational note — data refresh runtime (added 2026-10-16)

The S3 full-coverage projection consumes the source-refresh lanes, so a
note on their runtime for future runs of this arc:

- `APPLY=1 make refresh-chain-ser` is **hours-long on a cold/backlog run** (`refresh-chain` is the parallel default)
  (the XBRL sweep and MCA CIN web-resolve dominate; yfinance is
  rate-limit-bound). Never run it as a foreground tool call — the
  harness kills the process group on timeout.
- Backgrounded invocation (setsid detaches it from the tool call; the
  `.exit` marker carries the rc for short-command polling — never
  sleep-poll):

  ```bash
  setsid bash -c 'APPLY=1 make -C /home/arun/Research/findata-graph \
    refresh-chain-ser > /mnt/data/tmp/refresh_chain.log 2>&1; \
    echo $? > /mnt/data/tmp/refresh_chain.exit' </dev/null >/dev/null 2>&1 &
  ```

- Known-slow lanes can run solo in the background while the rest of
  the chain stays foreground; every lane is incremental, so repeat runs
  are quick — the first run after a long gap pays the backlog.
- Follow-on after a refresh APPLY: `make maint-full` (re-derivation;
  same backgrounding if the backlog is large).
