---
title: "Write down the near-duplicate guard invariant and gate the endpoint — the docstring's 'API-floored at 0.9' is false"
status: executed
filed: "2026-10-01"
executed: "2026-10-01"
completed_md: "330"
area: "helpers/graph/query.py docstring, tests/run_perf_benchmarks.py, doc/improvements/archive/security/near_duplicates_api_compute_cap.md"
---

# Write down the near-duplicate guard invariant and gate the endpoint — the docstring's "API-floored at 0.9" is false

**Date:** 2026-10-01 · **Status:** EXECUTED ·
**Area:** `helpers/graph/query.py:3684-3686` (docstring), new perf leg in
`tests/run_perf_benchmarks.py` (+ its `make perf` table),
`doc/improvements/archive/security/near_duplicates_api_compute_cap.md`

> **Position:** the invariant + gate arc for the function
> `c901_d1_split_near_duplicate_notes.md` refactors and
> `near_duplicate_gemm_rework_record.md` records. Those two own the refactor
> and the numeric record respectively; this one owns **what the guards
> actually guarantee** and **whether anyone would notice a regression**.

## 1. Motivation

`near_duplicate_notes`'s docstring justifies its bounded accumulator with a
causal claim that is false in two independent ways
(`query.py:3684-3686`):

> "The pair accumulator is separately bounded to O(limit): it is pruned back
> to `limit` (sort + slice, no heap) whenever it reaches `4 x limit`, so a low
> `min_sim` can no longer accumulate tens of millions of pairs ahead of the
> truncation. **`min_sim` is API-floored at 0.9, so the wide case is latent,
> not live.**"

Checked against the code on 2026-10-01:

- **`min_sim` is not floored at 0.9.** `app.py:2398` reads
  `float(request.args.get("min_sim", "0.9"))` — 0.9 is the *default* — and
  `app.py:2400` validates only the range `0.0 < min_sim <= 1.0`. A client may
  legitimately pass `0.001`.
- **The wide case is not latent.** Measured on the live corpus, the prune fires
  **188 times at `min_sim=0.9`** and 2,342 times at `0.001` (table in
  `near_duplicate_gemm_rework_record.md` §2). The 4× bound is not
  rarely-needed belt-and-braces on the default path; it is load-bearing there.

The bound itself is fine — and *stronger* than the docstring claims, because
`app.py:2409` validates `1 <= limit <= 500`, so `prune_at = 4 × limit ≤ 2000`
**unconditionally**, for every threshold. Measured `peak` was exactly 400 at
`limit=100` across a 900× threshold sweep. The problem is that the stated
reason is wrong, the invariant is written nowhere as an invariant, and
**nothing gates it**: `rg near_dup tests/run_perf_benchmarks.py` returns no
hits, so the endpoint's ~2.07 s live compute has no budget and no leg.

That combination is the actual risk. A maintainer who trusts the docstring's
implication — that the prune is a rarely-exercised safety net — could
simplify it away, and no gate would go red. Meanwhile the endpoint is
unauthenticated and its per-call cost is ungated.

## 2. Evidence (measured 2026-10-01, this box)

| Check | Result | Meaning |
|---|---|---|
| `app.py:2398` | `float(request.args.get("min_sim", "0.9"))` | 0.9 is a default, not a floor |
| `app.py:2400-2401` | `if not 0.0 < min_sim <= 1.0` → 400 | the domain is `(0, 1]`; 0.001 is legal |
| `app.py:2404-2409` | `limit` must be `1..500` → 400 | so `prune_at = 4 × limit ≤ 2000` always |
| prune counters, live, `limit=100` | `peak` 400 / `post_prune_max` 100 at every `min_sim` from 0.9 to 0.001 | the bound holds unconditionally, as predicted |
| `prunes` at `min_sim=0.9` vs `0.001` | 188 vs 2,342 | the bound is load-bearing on the **default** path — "latent, not live" is false |
| `rg near_dup tests/run_perf_benchmarks.py` | 0 hits | no leg, no budget for a ~2.07 s uncached compute |
| residual amplification | memo key is `(gen, doc_type, min_sim, limit)` (`app.py:403`) and `min_sim` is continuous in `(0,1]` | an anonymous client can force *distinct* ~2 s computes by varying `min_sim`; the LRU bounds cache size, not compute. Pre-existing (AVAIL-1 closed *repeated identical* compute), not a regression — but `_cached_near_duplicates`'s docstring claims "An anonymous client can no longer force repeated compute", which is only true per-key |

## 3. Design

Three slices, smallest first. S1 is documentation and can land alone; S2 is
the gate; S3 is a scoping correction to an existing record.

- **S1 — state the invariant, delete the false reason.** Replace the two
  sentences at `query.py:3684-3686` with the true statement: the accumulator
  is bounded by `4 × limit`, which is unconditionally ≤ 2,000 because the API
  validates `limit ≤ 500`; the bound does not depend on `min_sim`; and record
  that the prune fires on the default path (188× measured at `min_sim=0.9`).
  Keep the retired-SQL memory note (S4) intact. Cross-reference
  `near_duplicate_gemm_rework_record.md` for the counter table. No behaviour
  change, no test change.
- **S2 — add the perf leg.** A `near_duplicate_api` leg in
  `tests/run_perf_benchmarks.py` at a stated budget, timed against the live
  read-only cache the way the other graph legs are (the function is
  deliberately not a build path, so the leg needs no rebuild). Two cases
  worth separate budgets: the default (`min_sim=0.9`) and a low threshold
  (`min_sim=0.01`) — measured 2.07 s and 2.34 s today, so the gap between them
  is small and a single budget would hide a future divergence. If the leg
  needs a warm `graph.duckdb`, follow the existing graph-leg fixture pattern
  rather than introducing a new one.
- **S3 — scope the AVAIL-1 claim.** Append a dated note to
  `near_duplicates_api_compute_cap.md` recording that the
  memo closes *repeated identical* compute within a cache generation, and
  that `min_sim`'s continuous domain still permits distinct keys at the
  measured per-key cost — a fact for the operator, not a code change. No
  behaviour change in this arc; if the operator wants the amplification closed
  (clamping `min_sim` to a floor, or keying the memo on a quantised
  threshold), that is a **separate** decision with its own security review and
  is explicitly out of scope here.

## 4. Acceptance criteria & shakedown

1. `rg -n "API-floored" helpers/graph/query.py` → 0 hits; the replacement
   sentence names `4 × limit`, the `limit ≤ 500` validation, and the measured
   prune count. Docstring-only, so `make types` and `lint` must be unaffected.
2. `make perf` reports the new leg(s) and the run is green at the stated
   budget, with the budget and the measured actual recorded in
   `outputs/perf_report.md` by the normal writer.
3. The two thresholds are budgeted separately, so a divergence between the
   default and the wide case is visible in the table rather than averaged away.
4. The AVAIL-1 archive note is appended with its date; its `completed.md`
   entry is **not** renumbered or edited (append-only, per the archival
   checklist).
5. `make qa` legs green: `lint`, `md-lint`, `types`, `static_checks`,
   `pytest`; `make search-fresh` rc=0 after the doc edits.

| Projected outcome | Today | After |
|---|---|---|
| stated reason for the accumulator bound | false (claims a 0.9 floor) | true, with the `limit ≤ 500` derivation |
| near-duplicate perf legs | 0 | 1–2, budgeted at two thresholds |
| live default compute | 2.07 s, unbudgeted | budgeted, gated by `make perf` |
| AVAIL-1 "can no longer force repeated compute" | unqualified | scoped to per-key, with the residual cost measured |

## 5. Risks

- **A new perf leg is a flaky-gate risk.** Timing legs have already produced
  one flaky failure in this repo (`test_bold_line_regex_scales_subquadratically`,
  gate 1137, now owned by `bold_line_ratio_floor`). Mitigation: take the
  leg's number as min-of-N the way the other graph legs do, and set the budget
  with headroom over the measured 2.07/2.34 s rather than at it; if it proves
  noisy, land it advisory-first (report without gating) and promote on
  evidence.
- **Documenting the residual amplification may be read as a security
  finding requiring action.** It is pre-existing and bounded to the measured
  per-key cost, which is far below the 59.31 s AVAIL-1 closed. Mitigation: S3
  states the fact and explicitly routes any clamping decision to a separate
  review; it changes no code.
- **Editing a docstring in a file two other proposals are about to refactor.**
  The split will move these lines into a helper. Mitigation: S1 is
  deliberately prose-only and lands first; whoever executes the split carries
  the corrected text into the extracted helper.
- **The "ungated 2 s" framing may overstate exposure** if the memo absorbs
  most real traffic. Mitigation: S2's leg measures the function, not the
  endpoint's cache-hit path, and the archived note says so.

## 6. Non-goals

- **No behaviour change.** No clamping, no quantised memo key, no new
  validation, no change to `limit` or the 10,000-path ceiling.
- **Not the AVAIL-1 memo's design** — keying, LRU bound, and the
  generation-invalidation contract are unchanged.
- **Not the refactor** (`c901_d1_split_near_duplicate_notes.md`) or the
  numeric record (`near_duplicate_gemm_rework_record.md`).
- **Not a security review.** The amplification note is recorded, not remediated.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-01 | read `query.py:3684-3686` | "``min_sim`` is API-floored at 0.9, so the wide case is latent, not live" | the false claim, verbatim |
| 2026-10-01 | read `app.py:2396-2409` | `min_sim` default `"0.9"`, range-checked `(0, 1]`; `limit` range-checked `1..500` | 0.9 is a default; `prune_at ≤ 2000` unconditionally |
| 2026-10-01 | `near_duplicate_notes(..., stats=st)` sweep, `min_sim` 0.9→0.001, `limit=100`, read-only cache | 2.07–2.41 s; `prunes` 188→2,342; `peak` 400; `post_prune_max` 100 | "latent, not live" is false; bound holds at every threshold |
| 2026-10-01 | `rg near_dup tests/run_perf_benchmarks.py` | 0 hits | no leg, no budget |
| 2026-10-01 | read `app.py:401-403`, `_NEAR_DUP_CACHE_MAX` | `key = (gen, doc_type, min_sim, limit)`, LRU-bounded | distinct `min_sim` ⇒ distinct compute, cache size bounded but compute is not |

## Execution Results

### S1 + S2 + S3 executed 2026-10-01 (S1 landed before the split so the corrected text moved into the entry docstring)

- **S1:** the false sentence is gone (`rg -n "API-floored"
  helpers/graph/query.py` → 0). The replacement states the true
  invariant — bounded by `4 x limit`, unconditionally ≤ 2,000 because
  the API validates `1 <= limit <= 500`; the bound does not depend on
  `min_sim` (domain `(0, 1]`, 0.9 only the default); the prune is
  load-bearing on the default path (188 fires measured, P=1,186) — with
  the retired-SQL memory note kept intact and the counter table
  cross-referenced by bare proposal name (survives archival).
- **S2:** two legs in `tests/run_perf_benchmarks.py`, budgeted
  separately — `near_duplicates_default` (min_sim 0.9, 7.0 s) and
  `near_duplicates_wide` (min_sim 0.01, 8.0 s). Measured CLI walls
  2026-10-01: 2.2–2.4 s warm / 4.3 s cold first-open (0.9) and
  3.5–3.6 s (0.01). First `make perf` with the legs: 5.29 s OK and
  3.29 s OK. Budgets ~2x warm headroom, far below any super-linear
  decay; if they prove noisy in-gate, the proposal's advisory-first
  fallback applies.
- **S3:** dated §6 scope note appended to
  `near_duplicates_api_compute_cap.md` (memo closes
  repeated-identical compute per key; continuous `min_sim` permits
  distinct ~2 s computes; amplification recorded, not remediated). The
  archive record's completed.md entry untouched (append-only).
