---
title: "Archify diagram refresh — evidence backfill, staleness repair, quote_capture S9"
status: executed
filed: "2026-09-09"
executed: "2026-09-10"
completed_md: "220"
area: doc/design/diagrams
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Archify diagram refresh — evidence backfill, staleness repair, quote_capture S9

**Date:** 2026-09-09 · **Status:** EXECUTED 2026-09-10 · **completed.md:** #220 ·
**Area:** `doc/design/diagrams/`, `doc/improvements/archive/tooling/archify_diagram_pipeline.md`

## 1. Motivation

The archify pipeline (archify_diagram_pipeline.md, executed 2026-09-05)
produced 7 diagrams (S1–S3, S5–S8). A 4-day-old audit reveals two
classes of drift:

1. **Evidence gap** — 5 of 7 diagrams carry zero `sources` anchors. The
   pipeline's own contract (§2: "every committed diagram carries
   repository evidence: verified file:line anchors") is unmet on the
   majority of artifacts. Only S1 (system_overview) and S5
   (embeddings_stack) have proper evidence pins to origin/main.

2. **Content staleness** — S6 (relations_pipeline) and S7 (derive_chain)
   were authored 2026-09-05; since then, 3+ commits each touched their
   core code (triage hygiene, CLI param bundling, symmetric peer
   acceptance, derive_cli.py as new entrypoint, derive_events/insights
   changes). The IRs describe a code surface that no longer exists.

A third item: the pipeline doc (§Slices, §Candidate census) references
integration test suites (`test_integration_extract_relations_cli`,
`test_integration_derive_chain`, `test_integration_snapshot_cycle`) that
do not exist as files. Tests live in `tests/test_*.py` instead.

Separately, the quote_capture_coverage proposal (executed 2026-09-07/08,
`archive/graph/quote_capture_coverage.md`) added a concrete 6-stage
workflow with executable specs and tests but was not in the original
candidate census. It is a natural S9.

## 2. Evidence (measured 2026-09-09, this box)

### Evidence-marking audit

| Diagram | `sources` count | `meta.repository` | Verdict |
|---|---|---|---|
| system_overview (S1) | 9 | pinned origin/main | ✓ clean |
| embeddings_stack (S5) | 9 | pinned origin/main | ✓ clean |
| markdown_parse (S2) | 0 | missing | ✗ needs backfill |
| maint_full (S3) | 0 | missing | ✗ needs backfill |
| relations_pipeline (S6) | 0 | missing | ✗ needs re-author |
| derive_chain (S7) | 0 | missing | ✗ needs re-author |
| snapshot_lifecycle (S8) | 0 | missing | ✗ needs backfill |

### Staleness audit (commits since 2026-09-05 creation)

| Diagram | Key files touched | Severity |
|---|---|---|
| relations_pipeline (S6) | `extract_relations.py`, `triage_pending_relations.py` (3 commits: symmetric peers, alias guard, CLI bundling) | **high** — triage logic reworked |
| derive_chain (S7) | `derive_cli.py` (new), `derive_events.py`, `derive_insights.py`, `derive_cited_in.py` (4 commits: render arc, CLI entry, code consolidation) | **high** — new entrypoint + churn |
| snapshot_lifecycle (S8) | parquet untrack only | low |
| markdown_parse (S2) | `markdown_parse.md` touched (quote_capture sections added) | low — prose changed, pipeline stages same |
| maint_full (S3) | `db_maint.py`, `rebuild_common.py` refactored | medium — internal reshuffling, chain steps unchanged |

### Integration test references

Pipeline doc cites 3 test suites that don't exist:
- `test_integration_extract_relations_cli` → actual: `tests/test_extract_relations_extraction.py`, `tests/test_triage_pending_relations.py`
- `test_integration_derive_chain` → actual: `tests/test_quote_capture_s*.py` (closest); no single derive_chain suite
- `test_integration_snapshot_cycle` → no matching file found

### New candidate: quote_capture (S9)

| Attribute | Value |
|---|---|
| Subject | Quote capture pipeline — coverage audit → marker regex → resolver ladder → sector capture → catch-all → triage |
| Type | `workflow` |
| Owner | `doc/procedures/markdown_parse.md` (quote capture section) or standalone procedure |
| Executable specs | `quote_coverage_audit.py`, `triage_pending_quotes.py`, `derive_insights.py` (S1–S4 sections), `test_quote_capture_s*.py` |
| Stages | S0 (audit) → S1 (marker regex) → S2 (resolver ladder) → S3 (sector capture) → S4 (catch-all) → S5–S7 (triage + perf) |
| Why DRAW | Multi-dimensional workflow with concrete test coverage; 85% → 99% coverage target; not visible in any existing diagram |

## 3. Plan

### Slice A — Evidence backfill (S2, S3, S8)

Lightest touch: add `sources` anchors and `meta.repository` to the
existing IRs without restructuring nodes/edges. Verify each anchor
against origin/main (`git show origin/main:<path>#L<n>`), pin revision
from `git ls-remote origin main`. Re-validate (9-check showcase) after
each.

- S2 (markdown_parse): backfill sources for the 31-node dataflow —
  focus on the 6 stage nodes and the parse_newsletter consumer.
- S3 (maint_full): backfill sources for the 16-node workflow — the
  PRE_FULL / TIER1 / TIER2 lane steps.
- S8 (snapshot_lifecycle): backfill sources for the 15-node lifecycle —
  create / verify / restore bands.

### Slice B — Re-author (S6, S7)

Full re-author with sources from the start, incorporating code changes
since 2026-09-05. Use ripwire to map current symbol→file:line anchors
before writing IR.

- S6 (relations_pipeline): re-author the workflow IR. Key changes to
  reflect: triage_pending_relations.py reworked (word-overlap alias
  guard, discard-persistence noise gate, symmetric semantic_peer
  accepts), CLI param bundling in extract_relations.py.
- S7 (derive_chain): re-author the dataflow IR. Key changes: derive_cli.py
  is now the unified entrypoint (replaces direct script invocations),
  derive_events/insights/cited_in all touched.

### Slice C — New diagram S9 (quote_capture)

Author a `workflow` diagram for the quote capture pipeline. Stages:
audit → marker → resolver → sector → catch-all → triage. Sources from
quote_coverage_audit.py, derive_insights.py sections, triage_pending_quotes.py.
Link from owner doc. Full showcase validation + visual-check.

### Slice D — Pipeline doc cleanup

Update `archify_diagram_pipeline.md`:
- Fix integration test references to actual test file paths.
- Add S9 to the candidate census table (verdict: DRAW, executed).
- Add note that S2/S3/S6/S7/S8 were evidence-backfilled or re-authored
  on 2026-09-09.

## 4. Execution order

A → B → C → D (backfill first so re-authors build on clean base; new
diagram after existing ones are stable; doc cleanup last).

## 5. Verification

- Each diagram: `node bin/archify.mjs validate <type> <ir> --quality
  showcase --repo-root . --json` → 9 checks, 0 errors, 0 warnings.
- Each diagram: `node bin/archify.mjs deliver ... --json` → non-zero
  exit on failure.
- Each diagram: `node bin/archify.mjs visual-check <html> --json` →
  overflow + rendering green at 1440×900 / 1600×1000 / 1920×1080 /
  2048×1320, light+dark.
- S6/S7: verify ripwire symbol anchors against live code before pinning.
- Final: `make qa`, `make advisory`, `make search-fresh APPLY=1`.

## 6. Risks

- **Time**: re-authors (B) are the heaviest slice; budget ~6 validate
  rounds each per the pipeline's own estimate.
- **Scope creep**: quote_capture is a new workflow; keep it bounded to
  the 6-stage chain, not the full derive_insights internals.
- **Rot**: same mitigation as original pipeline — few diagrams, suite
  tripwires, re-render on arc change. The evidence backfill makes
  staleness detectable (anchors 404 at origin/main = diagram is wrong).
