---
title: OKF Read-Side — Per-Claim Footnotes + verify Helper
status: executed
filed: '2026-08-19'
executed: '2026-08-19'
completed_md: '137'
area: okf
---

# Proposal: OKF Read-Side — Per-Claim Footnotes + verify Helper

**Status:** EXECUTED 2026-08-19 — COMPLETE & ARCHIVED. N1 + N3 shipped
(completed.md #137), live note-writing apply intentionally HELD for the
operator (footprint rule: N1 full propagation = one non-stale-only apply,
currently 314 chatter + 173 key-figures notes would write; `--stale-only`
alone never propagates footnotes — evidence unchanged, gate correctly
closed; the operator runs derive-insights manually per their own note).
Post-ship, same day: the read-side revisit also produced #136 (edition
stems, standalone) and the Makefile-help regeneration #138. Residuals:
N4 (C3 temporal analytics) lives in pending.md; the refine scheduler
defect observed while scheduling harness notes is external to this repo.
**Date:** 2026-08-19
**Author:** Agent analysis (user-directed)
**Builds on:** the archived `okf_adoption.md` (#130–#133), `okf_activation.md`
(#134), `okf_sources_maintenance.md` (#135), and the standalone edition-stem
normalization shipped as #136 (originally item 2 of this read-side revisit —
executed separately as a take-now, per the accepted split).

## Why now, and what's in scope

Ranked deferral revisit (2026-08-19) surfaced four unlocked items. Per the
accepted plan, **#2 (edition stems in the DB) shipped standalone** (#136);
**#4 (temporal analytics) stays in pending.md** as a "whenever wanted"
report; THIS proposal is the **#1+#3 bundle** — both are note-writers over
OKF frontmatter/blocks and share the same YAML round-trip test path, which
is exactly why they move together.

| # | Item | Deferred at | Unlocked by |
|---|------|-------------|-------------|
| N1 | Per-claim `[^source-id]` footnotes in chatter blocks | adoption Q4, activation scope | `sources[]` on 476 notes + edition stems everywhere + renderer splices on change |
| N3 | `okf_verify.py` human `verified[]` helper | adoption Q2 (tier empty by design) | tier fully plumbed (census reads, bump preserves, schema validates) — only writing is hand-YAML surgery |

Still deferred, unchanged rationale: S5 `company/` coverage tags, B2 relation
sidecars, frontend trust badges, `okf_version` note key.

---

## N1 — Per-claim `[^source-id]` footnotes

Render each quote attribution in auto chatter blocks as
`— Name, Title [^chatter-<stem>]` and append a footnotes section inside the
sentinel block:

```
[^chatter-<stem>]: <Edition title> — [[<stem>]]
```

- IDs namespaced `chatter-` so hand-written footnotes can never collide.
- `bump_generated` already bumps on block change, so blocks churn once and
  ride `--stale-only` thereafter.
- `resolve_edition_string` at render time (index already shared run-wide
  since #136); unresolvable attributions get no footnote (honest miss,
  same discipline as sources splice).
- Design point held open for review: footnote targets the EDITION (stem),
  not the per-claim PDF page — adoption Q4's original sketch bound
  quotes to `sources[].id`, which IS the edition here.

**Effort ~2–3 h. Risk: Medium** — renderer path; byte-churn on rendered
blocks; needs round-trip tests asserting hand-written blocks untouched.

## N3 — `okf_verify.py` verify helper

Tiny CLI (`helpers/misc/okf_verify.py`):

```
python3 -m helpers.misc.okf_verify <note>... [--by human:user]
```

Appends `verified: [{by, at: <now UTC>}]` via the safe YAML round-trip
(`parse_frontmatter` → append → `render_frontmatter`), preserving `generated`
and every other key. Idempotent (same by+at → no write). The census's
human-reviewed tier activates with zero hand-YAML friction; pairs with N1
(read the footnoted block, verify, stamp).

**Effort ~1–1.5 h. Risk: Low** — pure frontmatter writer; mirrors
backfill round-trip tests.

## Shared test path (why bundled)

Both N1 and N3 write notes through the OKF round-trip: one test module
extension covering (a) pre-existing `verified` survives, (b) block-content
preservation outside the sentinel, (c) no-op second run writes nothing,
(d) schema stays valid post-write (`validate_frontmatter` on the result).

## Definition of Done

- N1: rendered blocks carry namespaced footnotes; second render is
  byte-identical; hand-written blocks never touched; `--okf` census unchanged
  in shape.
- N3: `okf_verify.py note.md` stamps `verified[]`, round-trips all keys,
  re-run writes 0; census human-reviewed count increments.
- Gates: touched suites + ruff + `make types` + static_checks green once.
- Docs: completed.md entry; this proposal archives.
