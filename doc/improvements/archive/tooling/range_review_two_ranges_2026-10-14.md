---
title: "Combined range-review remediation: 205a2c719..a4b3e5f81 (39 findings) + 63e04dbde..e06b32798 (72 findings)"
status: executed
filed: "2026-10-14"
executed: "2026-10-14"
completed_md: "392"
area: "helpers/maintenance/store_sweep.py, doc/procedures/search.md, doc/improvements/archive/, tests/test_security_headers.py, tests/test_store_sweep.py, doc/improvements/completed.md"
---

# Combined range-review remediation (two ranges, one fix arc)

**Date:** 2026-10-14 · **Status:** PROPOSED
**Ranges:** R1 `205a2c719..a4b3e5f81` (8 commits, store-reclaim era) ·
R2 `63e04dbde..e06b32798` (30 commits, OKF-v0.2 + bge-small era).
**Legs (both ranges):** `delegation` (host, free) + `scan` (`review_scan.py`). No `managed`.
**Evidence:** `outputs/reviews/review_report_205a2c719..a4b3e5f81.md`,
`outputs/reviews/review_report_63e04dbde..e06b32798.md` (+4 rosters, all gitignored).
Supersedes `range_review_205a2c719_a4b3e5f81.md` (folded in whole, then removed).

## 1. Arithmetic

| Range | Delegation | Scan | Dedup |
|---|---|---|---|
| R1 | 14 (1H+4M+9L) | 28 (3M+25L) | 42 − 0 − 3 FP = **39 (1H/4M/34L)** |
| R2 | 26 (1H+9M+16L) | 135 (89M+46L) | 161 − 0 − 89 FP = **72 (1H/9M/62L)** |

Zero double-reports on either range. All scan mediums FP (validated-date/constant
SQL, SafeLoader, intentional bind, test-SQL class). No query-visible semantics
change rides this proposal — **no eval-gate bullet**.

## 2. Fix slices (all re-verified at HEAD 05e4a8475 before filing)

- **S1 — R1.H1 dead pointer:** `archive/graph/graph_improvs.md:132`
  `.txt` → `.md`. One line, blocking per pointer-sweep.
- **S2 — R1.M1 absent-store crash (EXECUTED proof):** `store_sweep.py`
  `run_sqlite:122` / `run_duckdb:198,211` `p.stat()` unguarded → `FileNotFoundError`.
  Mirror `db_maint` (`:1108-1110`): `if not p.exists(): action "absent", return`.
  Regression test `test_absent_store_is_absent_row_not_crash` (absent → row, exit 0),
  mutation-checked (guard neutered → red).
- **S3 — R1.M2 stale build label:** `search.md:97` `# full (convergence)` →
  `# incremental (default; no-op when clean)` + `--full` line; reword `:62/:70`.
- **S4 — R1.M3a/M3b status flips:** `store_wide_reclaim.md:12`,
  `worktree_fresh_trample_guard.md:12` body `PROPOSED` → `EXECUTED`.
- **S5 — R2.B.F1 vacuous assert:** `test_security_headers.py`
  `test_json_path_returns_description_verbatim_but_jsonified` asserts only under
  `if 400:` → assert `status_code == 400` first (comment already claims the 400).
- **S6 — R2.C.F5 dead pointer:** `completed.md:2511`
  `` `proposals/okf_readside.md` `` → `` `archive/okf/okf_readside.md` ``.

## 3. Operator-adjudicated (no unilateral action)

R1 Lows F1–F5 (test tightening) + 25 scan style nits; R2 A.M3 (IN-list chunking,
perf-only), B.F4 (live-only tripwires), B.F5 (snapshot-test overlap), B.F7 (S108
waivers), C.F1-residue (header-count semantics need a ruling: drop the count or
automate the census). B.F3 is gate-owned (qa already red — not a review finding).
Already healed at HEAD (no action): R2.H1 (SNAP-1), R2.D.1/D.2 (sanitize + delegated
listener), R2.A.M1/M2/L1/L2, B.F2/F6/F8, C.F2–F4/F8, D.3, Makefile stray PATH.
`.env.copy` history secret: known-class per no-rewrite doctrine.

## 4. Acceptance

S1–S6 applied + re-reviewed at same ref; new S2 test red/green proven; review
tests 118 green; md-lint clean; freshness re-recorded per range.
