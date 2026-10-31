---
title: "Graph alpha lane — edge dating from provenance, leak-free re-sweep, confirmatory pre-registration"
status: executed
filed: "2026-10-31"
executed: "2026-10-31"
completed_md: "462"
area: "helpers/maintenance/date_edges.py + graph_edges.valid_from backfill + helpers/research/signal_sweep.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Graph alpha lane — edge dating from provenance, leak-free re-sweep, confirmatory pre-registration

**Date:** 2026-10-31 · **Status:** EXECUTED (#462) ·
**Area:** `helpers/maintenance/date_edges.py`, `graph_edges.valid_from`,
`helpers/research/signal_sweep.py`, `helpers/research/event_study.py`

## 1. Motivation

The price-lane arc (#461) made the graph testable: as-of substrate (S0),
weekly outcome bars (S1), an event-study harness (S3), and a
pre-registration pattern (S4). The first unlock sweep
(`helpers/research/signal_sweep.py`, §2 below — filed from
`doc/local/evaluations/`) found **no signal that survives leak-free
testing** — and pinned the reason: 61k of 69k edges are undated, so the
leak-free (`dated`) mode has too thin a universe to discriminate, while
the strongest candidate (`competes`, all-mode t=+3.08 across 14/18
dates) is exactly one of the untestable families. The unlock is data
quality: **date the undated families from their own provenance** — 100%
of undated rows carry `source_ref` labels whose dates are recoverable
(vault edition dates, RPT `properties.period_end`) — then re-sweep
leak-free and pre-register whatever survives.

## 2. Evidence — sweep #1 (measured 2026-10-31, live store)

**Tool:** `helpers/research/signal_sweep.py` · **Outcome data:** S1
weekly sidecar, forward 13w, 18 quarter-ends
(2022-03-31..2026-06-30), universe ~979 companies/date (survivorship
caveat applies throughout)

### Method

Per quarter-end T: per-company graph signals from the S0 as-of edge
set; Spearman rank-IC(signal, forward 13w return) + quintile spread.
Two visibility modes because undated edges (NULL bounds = always
visible, ~61k of 69k) are LOOKAHEAD for backtesting: `all` (substrate
as-is, optimistic) and `dated` (only `valid_from <= T`, leak-free).
A signal must be consistent in BOTH modes and across dates before it
earns a pre-registration (`linkpred_precision_prereg.md` pattern).

## Results (rank-IC, mean over dates; t = mean/σ·√n)

| signal | mode | meanIC | t | pos/dates | q-spread |
|---|---|---|---|---|---|
| subsidiaries | dated | −0.0740 | −4.81 | 0/3 | −0.0069 |
| jv | dated | −0.0429 | −0.77 | 5/12 | −0.0130 |
| subsidiaries | all | +0.0369 | +2.36 | 9/18 | +0.0066 |
| jv | all | +0.0346 | +2.14 | 12/18 | +0.0146 |
| degree | all | +0.0331 | +1.64 | 12/18 | +0.0137 |
| **competes** | **all** | **+0.0327** | **+3.08** | **14/18** | **+0.0144** |
| suppliers | all | +0.0233 | +1.65 | 10/18 | +0.0042 |
| index_membership | all | +0.0218 | +0.57 | 2/4 | −0.0021 |
| holders | dated | −0.0208 | −0.44 | 9/18 | +0.0169 |
| degree | dated | −0.0198 | −0.33 | 6/18 | −0.0194 |
| rpt_disclosure | all | +0.0129 | +0.90 | 10/18 | −0.0025 |
| holders | all | −0.0029 | −0.34 | 8/18 | −0.0049 |

## Verdicts

1. **No signal earns pre-registration.** Every `all`-mode positive
   flips sign or vanishes in the leak-free `dated` mode (subsidiaries
   and degree flip outright; the dated runs also have few effective
   dates — many quarter-ends drop below the 8-company IC floor).
2. **The most promising untestable signal is `competes`** (all-mode
   t=+3.08, positive on 14/18 dates, q-spread +1.4%/quarter) — but
   `competes_with` has NO dated rows, so the leak-free test is
   impossible today. Same for `suppliers` and `rpt_disclosure`.
3. **The binding constraint is edge dating, not signal strength.**
   61k of 69k edges are undated, and the dated-only test universe is
   too thin to discriminate.

## The unlock that emerges: date the undated families from provenance

100% of undated rows carry `source_ref` — edition/provenance labels
(`Phase 2 seed (Edition #69 …)`, `derive:relations:The_Chatter`,
`vigil:rpt:*` filing periods already in `properties`) whose publication
dates are recoverable: edition dates live in the vault frontmatter
(`edition_index`), derive-batch refs map to harvest timestamps. Dating
~50k edges mechanically (edition → `valid_from`, with a
derive-batch-ref fallback policy to record — NOT to stamp, which would
fabricate precision) would make `competes`/`suppliers`/`rpt_disclosure`
leak-free testable and is the pre-condition for any real alpha claim.

## 3. Design — slices

### S1 — edge dating lane (`helpers/maintenance/date_edges.py`)

Backfill `graph_edges.valid_from` from per-family provenance, DRY-RUN
default, `--apply` writes. Per-family extraction policies, each
evidenced — nothing fabricated:

1. **`same_group` (RPT rows, 10,357 undated)**: `properties.period_end`
   ("31-MAR-2026") parsed to the filing-period end date. This is the
   disclosure's own date — the strongest evidence class.
2. **Edition-labeled refs** (competes_with, supplier_to, co_mentioned_in,
   semantic_peer, cited_in — refs carrying `Edition #N`): the edition's
   publication date from the vault frontmatter / `edition_index`. The
   relationship is at least as old as the edition that printed it.
3. **`derive:*` batch refs**: policy DECISION — these name a derivation
   run, not an event date. Stamping the derive date would understate
   visibility (the fact predates the run) but stamping the note's
   edition is correct where the note is edition-backed. Rows without a
   resolvable date stay UNDATED — recorded per-family, never guessed.

Guard rails: the as-of substrate consumes `valid_from` directly, so a
bad stamp shifts every study — the dry-run prints a per-family
census (rows dated / unresolvable / skipped) and a date-distribution
histogram before any apply; `--apply` is one transaction; a
`--revert <snapshot>` path (pre-apply parquet/SQL dump of touched
rows) because this edits 50k+ live rows. `static_checks` +
integrity-gate re-run after apply.

### S2 — leak-free re-sweep

Re-run `signal_sweep.py sweep` post-dating. Acceptance bar for the
sweep itself: the `dated` mode now covers ALL 18 quarter-ends for the
dating-enabled families (vs 3 today), and per-date N ≥ 8 holds. Fresh
table appended to the run record; the S1-before/S2-after delta per
signal is the deliverable.

### S3 — confirmatory pre-registration (decision point)

If `competes` (or any family signal) survives both modes with the same
sign and |meanIC| ≥ 0.03 across ≥ 12 dated dates: pre-register the
confirmatory test in the `linkpred_precision_prereg.md` pattern —
frozen signal definition + decision rule (NO-GO if cumulative IC
decays to ≤ 0 on out-of-sample quarter-ends as they accrue; KEEP at
sustained |IC| ≥ 0.03) — and log the first in-sample measurement as
the baseline row. If nothing survives: record evaluated-not-warranted
and keep the sweep as the standing quarterly instrument.

### S4 — sweep enhancements (ride along, cheap)

- **Sector-relative outcome**: abnormal forward return (subject minus
  sector-mate median, reusing `event_study.sector_peers`) as the IC
  target alongside raw returns — removes the sector beta the raw IC
  currently absorbs.
- **Two horizons in one run** (`--horizons 4,13,26`) — the long-horizon
  read the operator asked for, reported per horizon.
- **Quarterly cadence**: the sweep rides `make recompute-graph`'s wake
  by convention (manual run, no gate) — one row per quarter accrues in
  the run record.

## 4. Acceptance criteria & shakedown

1. **S1**: dry-run census printed per family (dated/unresolvable/
   skipped); `same_group` RPT rows ≥ 95% dated from `period_end`;
   edition-labeled families dated at the edition-coverage rate with
   unresolvable rows explicitly listed; `--revert` round-trips on a
   store COPY; post-apply integrity gate + static_checks green.
2. **S2**: re-sweep table with `dated` coverage on all 18 dates for
   dating-enabled families; before/after delta recorded.
3. **S3**: either a pre-registered confirmatory test exists with its
   baseline row, or the evaluated-not-warranted record does.
4. **S4**: sweep reports per-horizon + sector-relative columns.
5. Eval-gate bullet: N/A for the ontology question set — this arc
   writes `valid_from` (point-in-time correctness), not query-visible
   semantics; the as-of differential + S2 sweep ARE the check.
6. Targeted tests (`test_as_of.py`, sweep unit tests), static_checks,
   md-lint, `search-fresh APPLY=1` then plain rc=0 (doc changes).

## 5. Risks

| Risk | Mitigation |
|---|---|
| Bad stamps corrupt every as-of study | dry-run census + date histograms before apply; `--revert` snapshot; integrity gate after |
| Fabricated precision (derive-date stamps) | policy: evidenced dates only; unresolvable rows stay undated and are counted, never guessed |
| Multiple testing across the enlarged dated universe | pre-registration gate (S3) with decision rule BEFORE confirmatory runs; quarterly accrual out-of-sample |
| `valid_to` never touched | this arc stamps `valid_from` only; expiry semantics unchanged |
| Survivorship | carried from S2's census on every output (unchanged) |

## 6. Non-goals

- No served metric, no UI, no gate on the sweep (report lanes only).
- No `valid_to`/expiry inference; no date fabrication for unresolvable refs.
- No daily data, no charting (S1's ruling holds); weekly sidecar only.
- No trading/execution anything — research measurement only.

## 7. Execution record (2026-10-31)

All four slices executed same-day. The headline: **the dating lane
disproved the arc's own most promising signal** — `competes` collapsed
from all-mode +0.033 IC (t=+3.08, sweep #1) to −0.018/−0.014/−0.001
(h=26/13/4, t all ≤ 2.0) once tested leak-free. The undated-lookahead
instrument worked exactly as designed: it prevented a false
pre-registration.

| Slice | Result (measured) |
|---|---|
| S1 dating lane | `date_edges.py`: **46,045 rows dated** (99.5% of the five families: same_group 10,299 · supplier_to 16,007 · subsidiary_of 7,325 from RPT `period_end`; competes_with 3,522 · semantic_peer 8,892 from ref-embedded derive dates). 58/689/74/55/25 unresolvable stay undated per policy. 4/4 tests; `--revert` round-trip pinned; as-of substrate transformed (at 2025-06-30: 20,886 visible vs 61k+ pre-dating) |
| S2 re-sweep | dated mode covers **all 18 quarter-ends** (was 3); 96-row table (8 signals × 2 modes × 3 horizons × raw/abnormal). Sweep-#1's all-mode positives mostly evaporate: `jv` h=26 +0.052 (t=2.92) → dated +0.006 (t=0.09); `degree` all-mode flat. The one consistent dated read: **`rpt_disclosure` h=4 abnormal +0.045, t=+2.98, positive 6/6 usable dates** — but 6 < the 12-date pre-registration bar |
| S3 decision | **NO pre-registration.** `competes` dead (lookahead artifact); `rpt_disclosure` h=4 abnormal = the WATCH signal — it accrues one dated quarter-end per calendar quarter under the quarterly cadence; pre-register at ≥12 dates if the sign holds |
| S4 enhancements | built into the sweep: multi-horizon (`--horizons 4,13,26`), sector-relative abnormal target (subject − sector-mate median), O(1) bar indexing (the naive scan was millions of walks), quarterly cadence convention (manual run per `recompute-graph`) |

10 targeted tests green; static_checks + md-lint clean; search-fresh
converged. The sweep's standing honest conclusion: **no graph signal
has yet cleared the leak-free bar** — the lane's value is the
instrument (as-of substrate + dated edges + two-target IC + the
pre-registration discipline that just caught its first artifact).

## Appendix A — raw sweep log

Tool output as run 2026-10-31 (`signal_sweep.py sweep`, 18 quarter-ends,
forward 13w): the table in §2 IS the raw record; scratch artifacts in
`$TMPDIR/` are volatile. The run record accrues per §3 S4's cadence.
