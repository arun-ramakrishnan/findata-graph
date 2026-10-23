---
title: "Review lanes — separate semantic findings from mechanical lint, with a cosmetic floor"
status: executed
filed: "2026-10-21"
executed: "2026-10-22"
completed_md: "432"
area: "doc/procedures/ocr_review.md + helpers/misc/review_scan.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — see doc/templates/proposal.md -->
# Review lanes — separate semantic findings from mechanical lint, with a cosmetic floor

**Date:** 2026-10-21 · **Status:** PROPOSED ·
**Area:** `doc/procedures/ocr_review.md` (§4 severity), `helpers/misc/review_scan.py` (keep filter), `tests/test_review_scan*.py`

Range under review: `7b122d915..e3147ef3c` (the five-patch stack
`review_automation → review_annotate → merger_semantics → gates_upgrade → db_sync`,
1,395 reviewable files, 63,879+/−4,033).

## 1. Motivation

The composite `make review-patch` runs two legs on the same range: the OCR
delegation (selection + house checklist + host adjudication) and the native
scan (bandit/shellcheck/sqlfluff/semgrep/osv on the changed lines). On this
range the scan produced **113 findings, of which 92 (81 %) are sqlfluff
cosmetics** (`LT01` spacing 43, `LT05` line length 32, `RF06` quoted
identifier 16, `RF04` keyword case 1). One finding is medium
(`shellcheck:2034`, unused `db_args` in `helpers/misc/quack_servers.sh:107`);
the remaining 20 are low-severity bandit/shellcheck hygiene
(`B112` ×11, `B404` ×3, `B105` ×3, `B311` ×2, `shellcheck:2013` ×1).

The operator's complaint (2026-10-21) is precision, not recall: a roster that
ranks 92 whitespace findings alongside `B112` and a real medium forces a
manual read of noise before the signal. The scan's file coverage is *not* the
problem — measured on this range, **all 113 flagged files were already inside
the delegation selection (335 files selected, 0 outside)**. The problem is
severity meaning: "low" currently mixes *security hygiene* with *formatting
style*.

## 2. Evidence (measured 2026-10-21, this box)

| Leg | Result | Notes |
|---|---|---|
| delegation selection | 335/1,395 files + 13-item house checklist | 0 auto-findings by design; artifact `review_patch_7b122d915..e3147ef3c_af9e523b.md` |
| scan (offline) | 113 kept: 1 medium + 112 low | sqlfluff 92 / bandit 19 / shellcheck 2; semgrep + osv skipped |
| scan (online, prior artifact) | +8 semgrep mediums | formatted-SQL/`execute_raw` pairs; sink reads show repo/operator-controlled interpolation |
| join | 113/113 inside selection, 0 outside | delegation's view covered every scan-flagged file |

The scan's value on this range is therefore **mechanical breadth** (a
structural guarantee that every changed line was looked at by five tools),
not discovery of files the delegation missed. Its ranking is what fails.

## 3. Design

Two named lanes plus an explicit floor, applied in §4 of
`doc/procedures/ocr_review.md`:

- **Lane A — semantic.** Everything the delegation surfaces: correctness,
  contract/API breaks, data-shape errors, security reachability, doc/code
  divergence. Ranked H / M / L by exploit-today / user-impact ordering as today.
- **Lane B — mechanical.** Everything the scan surfaces, split by tool class:
  - **hygiene** (`bandit:*`, `shellcheck:2013/2034`, `semgrep:*`) — keeps the
    existing low/medium ranking, subject to §4b validity sweep;
  - **cosmetic** (`sqlfluff:LT01/LT05/RF06/RF04`, and future pure-formatting
    rules) — down-ranked **below Low** into a `cosmetic` lane: listed, never
    dropped, never ranked against Lane A, and never a gate input.
- **Floor.** No finding may be removed by ranking. The cosmetic lane is
  printed as a collapsed count + file list (`92 cosmetic (LT01 43, LT05 32,
  RF06 16, RF04 1)`), with per-line detail available on request. Anything
  carrying a security class or a semantic claim is never cosmetic — the
  floor is "never silently dropped", not "never shown".

Slices:

1. **S1 — procedure.** §4 gains the lane table + the cosmetic floor rule; the
   severity table keeps H/M/L and gains a fourth, non-ranking `cosmetic` line.
2. **S2 — scan keep-filter.** `helpers/misc/review_scan.py` labels each kept
   finding with `lane` (`hygiene` / `cosmetic`) from a small rule→lane map,
   so the roster and the JSON keep the split mechanically; unknown rules
   default to `hygiene` (fail loud, never silently cosmetic).
3. **S3 — roster layout.** Scan roster groups by lane (Lane B hygiene first,
   cosmetic collapsed at the end) and prints the per-tool count line.
4. **S4 — freshness.** `review_patch` records the sitting through
   `helpers/misc/review_freshness.py --record --leg delegation --leg scan`
   with the lane counts in the note, so STALE/FRESH stays honest.

Alternatives considered: (a) exclude `schema/duckdb/*.sql` from sqlfluff — the
92 cosmetic findings are mostly on **tracked, regenerated DDL**
(`make schema-dump` output), so the noise is structural; rejected because the
DDL include is exactly the surface a schema review must see (proposal
`schema/duckdb/graph.sql` drift was caught by the new gate leg) — the fix is
lane labelling, not exclusion; (b) drop sqlfluff entirely — rejected, `RF04`
and `LT05` occasionally catch a real typo; (c) fix the 92 cosmetics — rejected
as churn on generated files that the generator would reintroduce.

## 4. Acceptance criteria & shakedown

1. `make review-patch FROM=7b122d915 TO=e3147ef3c OFFLINE=1` prints the
   `92 cosmetic (LT01 43, LT05 32, RF06 16, RF04 1)` summary line and lists
   the single medium (`shellcheck:2034`) above it.
2. `pytest tests/test_review_scan*.py tests/test_lint_gates.py` green.
3. `make advisory` lint-audit leg still green (no new `# noqa` debt).
4. Two consecutive runs on the same range produce identical lane counts
   (deterministic fingerprint, repeat-count 2).
5. No eval-gate bullet: this changes review reporting only, not query-visible
   semantics.

| Projected outcome | Today | After |
|---|---|---|
| ranked findings on this range | 113 | 21 ranked + 92 collapsed |
| medium/high visible at top | 1 (amid 112) | 1 (lane head) |

## 5. Risks

- **Mislabeling a real finding as cosmetic** — mitigated by the fail-loud
  default: only an explicit rule→lane map entry is cosmetic, and the map is
  reviewed in the diff.
- **Losing the audit trail** — the floor prints counts and file lists; the
  raw scan artifact is unchanged on disk.
- **Scanner drift** (new rule ids) — unknown ids land in `hygiene`, so a
  future tool addition can only over-rank, never hide.

## 6. Non-goals

- No change to the delegation leg, selection teeth, or the 13-item checklist.
- No change to `review-patch`'s two-leg composition or the exclusion roster.
- No change to H/M/L definitions or the §4b validity sweep.
- No auto-fix of any finding.

## 7. Execution record — findings from the range review (2026-10-24)

The full delegation review over this range (harness-driven, 13-item checklist)
produced **seven real findings — all fixed in-sitting**; no High or confirmed-Medium
security findings. Full report: the sitting's delegation record.

| # | Finding | Severity | Fix |
|---|---|---|---|
| 1 | `helpers/misc/quack_servers.sh:107` — dead `db_args` declaration (the scan leg's single medium, shellcheck 2034) | Low (zero behavior) | `local i=0 db_args` → `local i=0` |
| 2 | `archive/README.md` — `security/cloudflare_audit_adoption.md` missing from the index | Low (bookkeeping) | index line added (section head, #423) |
| 3 | `archive/README.md` — `tooling/range_review_1fda0c158_6182cd2c7.md` missing from the index | Low (bookkeeping) | index line added (before the two-ranges entry, #394) |
| 4 | `archive/graph/l1_closeness_fold.md` — `completed_md: "8823"` pointed at a nonexistent entry | Low (broken backref) | `## 433.` entry backfilled in `completed.md` (sibling-lane convention #408/#409); frontmatter → `"433"` |
| 5 | `doc/design/ontology.md:202` — stale `proposals/person_ontology_spine` pointer | Low (pointer rot) | repointed to `archive/database/person_ontology_spine.md` |
| 6 | `doc/procedures/search.md:277` — stale `proposals/git_history_search` pointer | Low (pointer rot) | repointed to `archive/git/git_history_search.md` |
| 7 | `archive/tooling/ocr_review_lanes_7b122d915_e3147ef3c.md` — frontmatter lost `filed:` in the archival flip (schema check fires only when the file is touched) | Low (frontmatter) | `filed: "2026-10-21"` restored |

Verified benign (no action): duplicate `completed_md: "398"` is the intentional
companion pair (`entity_name_dedup` main + `entity_identity_home` companion);
the three `status: deferred` archives are closed by their DEFERRED-verdict
entries (#404/#430/#431); the "missing executed dates" on the two observability
proposals was a regex artifact (single-quoted dates present); the 12 teeth-sweep
candidates all had negative-path tests under other names; the 8 semgrep medium
SQL sites are operator-controlled with the `sql_lit` discipline enforced; the
Mojo `integrity_check.mojo` change is the sector-dyad retirement parity work;
schema mirrors are pure DDL.

## Appendix — raw measurement log

```text
$ make review-patch FROM=7b122d915 TO=e3147ef3c OFFLINE=1
  scan: 113 kept (1 medium, 112 low); semgrep/osv SKIPPED (offline)
  tools: sqlfluff:LT01 43, LT05 32, RF06 16, RF04 1;
         bandit:B112 11, B404 3, B105 3, B311 2;
         shellcheck:2013 1, shellcheck:2034 1
  worst severity: medium — shellcheck:2034 helpers/misc/quack_servers.sh:107

$ join(scan findings, delegation selection roster)
  335 selected paths; 113 findings; 113 inside, 0 outside
```
