---
title: "OCR rule census — version-proof selection, a rule-source catalog, and a working pointer sweep"
status: executed
filed: "2026-09-30"
executed: "2026-09-30"
completed_md: "323"
area: ".opencodereview/rule.json, doc/procedures/ocr_review.md, doc/procedures/doc-hygiene.md"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# OCR rule census — version-proof selection, a rule-source catalog, and a working pointer sweep

**Date:** 2026-09-30 · **Status:** EXECUTED 2026-09-30 — S1–S6 DELIVERED ·
**Area:** `.opencodereview/rule.json` · `doc/procedures/ocr_review.md` ·
`doc/procedures/doc-hygiene.md` · stale archive pointers

## 1. Motivation

The nine-model delegation bake-off (2026-09-30,
`doc/local/notes/delegation_reviews.md`) showed review rules scattered
across carriers — house checklist, static checks, gate legs, procedure
sections, operator memory — with no catalog naming them, and the round-2
measurement (7/10 finding classes after codifying two checklist items)
proved codification converts blind spots into findings. A census via the
four indexes plus ripwire then surfaced three concrete defects: selection
config that is declarative-only under 1.12.11's additive semantics (one
version flip from silently dropping desktop/bench_data), a doc-hygiene
pointer sweep that is broken as written (false MISSING on real refs;
relative-form refs escape it entirely), and live stale archive pointers
the sweep would have caught had it run.

## 2. Evidence (measured 2026-09-30, this box)

| Check | Command | Result | Verdict |
|---|---|---|---|
| rule source census | doc_query + script_query + ripwire --for | 26 static-check families (`static_checks.py`), 10 qa legs + 9 advisory legs (`run_gate_report.py:167,249`), 13 procedures, 9-item house checklist | rules live in ≥6 carriers, uncataloged |
| additive semantics | `delegate preview` on the lint range | 19/19 reviewable incl. `desktop/**` with no desktop include | `.py` include entries are declarative today |
| doc-hygiene live-proposals sweep | recipe as written, multi-path scope | 3 false `MISSING` on existing `proposals/README.md` refs (`rg -o` file-prefix defeats the `[ -f ]` test) | broken |
| doc-hygiene relative-form gap | pattern vs `pending.md:7` | the armed trigger's relative-form cite unmatched | the class it owns escapes it |
| A4 archive-pointer check | recipe as written | 3 live stale hits: `chain_tally_determinism.md:23,210`, `duckdb_pin_reconciliation.md:53`, `c901_complexity_debt.md:452` | real, currently red |
| JSON roster risk | `git check-ignore` + rule excludes | `package-lock.json` unexcluded; `memory/**`, `outputs/**` already gitignored | one exclude needed |

The `.mojo` include entries in `rule.json` are the precedent: declarative
lines that document intent even where OCR cannot parse the extension.

## 3. Design

- **S1 — rule.json selection hardening.** Include gains
  `desktop/**/*.py` + `bench_data/**/*.py` (version-proofing: 1.12.11
  admits them via the extension default, but semantics flipped once
  already and the `PRODUCT_FAMILIES` teeth only guard
  `Mojo/src|tests` + `tests/`). Exclude gains `**/package-lock.json`,
  `**/*.lock`, `snapshots/**` (regenerated db_sync churn),
  `.opencodereview/**` (the tracked config itself). No `rules` array —
  the channel is dead (Known limits).
- **S2 — ocr_review.md §1b landed-range gap.** `review_freshness.py` is
  stack-scoped; three bake-off rounds independently declined to record a
  landed range rather than write a false row. State the gap + the interim
  rule (hand-compute the fingerprint; never `--stack N` a landed range).
- **S3 — ocr_review.md §3 rule-source catalog.** One table naming where
  the house rules live (house checklist, static-check families,
  doc-hygiene A-classes, ruff per-file-ignores, md-lint tiers) so a host
  needs no bake-off context to find the carriers.
- **S4 — ocr_review.md §4b claim-audit + pointer cross-ref.** Commit/gate
  claims are hypotheses: re-derive counts via `gate_query`, `merge-base
  --is-ancestor` any claimed gate SHA (bake-off class 7). Pointer sweep
  cross-references doc-hygiene's A-class checks.
- **S5 — doc-hygiene.md recipe repair.** Strip `rg`'s `file:` prefix
  before the existence test; extend the pattern to relative
  `proposals/…` forms; note code-side refs are the review checklist's
  diff-scoped duty (fixture strings make a mechanical code sweep
  false-positive-prone).
- **S6 — stale pointer fixes** (the sweep's live hits): the three A4
  archive refs + `pending.md:7` (armed trigger) + `:38` +
  `duckdb_lock.py:13` + `tests/test_duckdb_lock.py:3` +
  `xdist_shared_graph_cache.md:225`.
- **S7 (deferred, not this arc)** — `review_freshness.py --from/--to`
  mode so landed ranges can be banked without a stack.

## 4. Acceptance criteria & shakedown

1. `ocr delegate preview` on `8711c96d..a363d12a` still selects 19/19
   with the test spine visible; `make review-patch` teeth pass.
2. The repaired doc-hygiene sweep prints no `MISSING` for existing refs
   and flags exactly the moved-target refs until S6 lands, nothing after.
3. `make md-lint` green; `make search-fresh` rc=0 after APPLY.
4. One further delegation leg over the same range with the patched
   config, measured against legs 8/9 (findings, classes, wall, tokens).

| Projected outcome | Leg 8 (round 1) | Leg 9 | This leg |
|---|---|---|---|
| finding classes (of 10) | 3 | 7 | 7 (config deltas are future-facing; procedure deltas cut grounding cost) |

## 5. Risks

- **Over-broad exclude prunes a wanted file** — each exclude names a
  generated/lock artifact; the teeth assertion still guards the product
  families.
- **Declarative includes rot** — same failure mode as the `.mojo`
  entries; acceptable, they are documentation that turns load-bearing if
  semantics flip back.

## 6. Non-goals

- No `rules` array / rules dir / `--rule` content (channel dead,
  re-verified 1.12.11).
- No managed-mode changes, no PRODUCT_FAMILIES edits, no checklist
  wording changes (S4 codifies in the procedure; the checklist file stays
  as-is this arc).
- No eval-visible semantics (rosters/crosswalks/extractors untouched).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-30 | `doc_query "review rules conventions reviewer duties checklist"` | 6 hits | census entry points |
| 2026-09-30 | `rg -n "^def check_" helpers/validators/static_checks.py` | 26 families | executable-rule census |
| 2026-09-30 | doc-hygiene live-proposals recipe, as written | 3 false MISSING | `file:`-prefix defect |
| 2026-09-30 | A4 archive recipe, as written | 3 live stale refs | §3 lines above |
| 2026-09-30 | `git check-ignore memory/data/review-freshness.json outputs/x snapshots/y .opencodereview/rule.json` | first two ignored, last two not | excludes needed for snapshots + config |
