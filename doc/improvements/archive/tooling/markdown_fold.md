---
title: "doc/ .txt → .md conversion sweep — finish the archive migration (12 files, conversion slice of the pending_improvs arc)"
status: executed
filed: "2026-10-05"
executed: "2026-10-05"
completed_md: '346'
area: "doc/improvements/archive/**/*.txt → .md, doc/improvements/archive/README.md"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# doc/ .txt → .md conversion sweep — finish the archive migration (12 files, conversion slice of the pending_improvs arc)

**Date:** 2026-10-05 · **Status:** PROPOSED ·
**Area:** the 12 prose `.txt` files under `doc/improvements/archive/**`,
their inbound references.

Arc split (operator, 2026-10-05): the **content** — the whole pending
backlog recorded into the perpetual tracker, plus the close-out fold of
the gitignored local file `doc/local/pending_improvs.md` — is no longer a
proposal; its slices (incl. S3/S4) become unique proposals therefrom.
This proposal is the conversion half: **S1/S2/S5**. The arc closes when
the conversion proposal and the pending-improvements slice proposal(s)
are green.

## 1. Motivation

The `doc/` `.txt` → `.md` migration is unfinished. Two precedent commits
did this class of work already, extension-only with content preserved
verbatim and references repointed:

| commit | date | scope | gates |
|---|---|---|---|
| `4e1ec8f32` | 2026-09-14 | `doc/design/graph_design.txt` + `tech_avenues`, `graph_algos`, `graph_pending`, `duckpgq_retirement` → `.md` | references updated |
| `711c9a32a` | 2026-09-15 | `graph_improvs`, `hierarchy_design_roadmap`, `networkx_duckpgq_gap_plan` → `.md` ("extension-only migration, content preserved verbatim; only extension + lint-blocking formatting artifacts changed") | `make qa` 9/9, `make md-lint` clean |

**12 prose `.txt` files remain under `doc/`** (all in
`doc/improvements/archive/**`) — outside the lint gate (`_CMD` globs
`doc/**/*.md` only, `helpers/misc/markdown_lint.py:74`) and rendered as
plain text in the vault. This proposal finishes the migration.
`pending_improvs.txt` is the driver file because it is the carrier into
which the pending-improvements slice proposal(s) write their backlog.

## 2. Evidence (measured 2026-10-05, this box)

| Fact | Measurement | Consequence |
|---|---|---|
| remaining `.txt` in `doc/` | 12 prose files (`find doc -name '*.txt'` minus `archive/graph/evidence/p22/scale_probe.py.txt`, minus `doc/local/**` data artifacts) | S1 scope |
| inbound references, per file | pending_improvs 48 · findata_corpus_audit 11 · integration_plan 9 · duckdb_improvs 8 · mcp_tool_eval 6 · perf_improvs 5 · doc_browser 5 · sql_query_improvements 5 · metric_improvs 4 · stateful_relational 3 · lint_analysis 3 · parse_extraction_gaps 2 (109 file-level hits, overlapping) | S2 mechanical but wide; wrong counts = rot |
| txt is unlinted | gate globs `doc/**/*.md`, `findata/**/*.md` only | S1 puts all 12 under `make md-lint` |
| doc index already reads txt | `rebuild_doc_search.py:82` `DOC_EXTS = {".md", ".txt"}` | conversion = format/lint only, no index capability change |
| proposal contract stays out of the way | none of the 12 contains `**Status:**`/`**Date:**` in the first 2000 chars → `_has_proposal_header` false → headerless `.md` files are skipped by `_check_proposal_units` / `_check_archived_proposals` (`frontmatter_schema.py:344`) | no frontmatter needed; the converted files stay headerless |
| lint-blocker class | zero bare ` ``` ` fences in the 12; known blockers are non-H1 title lines (`integration_plan.txt`, `lint_analysis.txt`, `stateful_relational_test_plan.txt`, `sql_query_improvements.txt`) and `mcp_tool_eval.txt`'s `====` banner | S1 fixes MD041-class artifacts only, meaning preserved |
| vault reference surface | 38 `findata/**` stubs cite "item #11 in pending_improvs.txt"; **0** vault notes name the other 11 files | S2 touches no vault note |
| conversion target for the driver | `archive/tooling/pending_improvs.txt` → `.md` in S1 | same path, no fresh file — S3/S4 written into it by the pending-improvements slice proposal(s) |

## 3. Design

- **S1 — convert the remaining `doc/` `.txt` → `.md`.** All 12 prose
  files, extension-only + lint-blocking formatting fixes, content
  preserved verbatim, exactly like `4e1ec8f32` / `711c9a32a`.
  `pending_improvs.txt` → `pending_improvs.md` is part of this batch.
  Non-prose `.txt` stays `.txt` (§5).
- **S2 — repoint the references.** The 109 file-level hits switch to
  `.md`: archive README index lines, `tests/` + `helpers/` comments,
  sibling cross-references; desktop doc-browser fixture regenerated via
  `make fixtures` (generated from the doc index,
  `desktop/src-vue/test/main.js:5`). The 38 vault stubs need no edit —
  their anchor is the item number.
- **S5 — hygiene.** Targeted + full `make md-lint` (0), frontmatter
  corpus test and `check_proposal_lifecycle()` (headerless conversions
  must stay outside the contract), `make search-fresh APPLY=1` then plain
  (rc=0), `completed.md` entry + archive README topic line on execution
  per the proposals checklist.

(S3/S4 — recording the backlog and the close-out fold — live in the
sibling `pending_improvs.md`; arc numbering S1–S5 spans both files.)

Alternatives rejected: (a) keep `.txt` and append Markdown to it —
rejected, the migration finishes here; (b) one proposal covering both
halves — split per operator instruction (content vs conversion);
(c) convert `doc/local/**` data `.txt` — prompt batches/scratch, not
prose; (d) rewrite the 38 vault stubs — writer-owned vault, and the
item-number anchor survives the rename.

## 4. Acceptance criteria & shakedown

1. `find doc -name '*.txt' ! -name '*.py.txt'` returns only the §5
   non-goals — the 12 prose files exist as `.md`.
2. `make md-lint` → 0 violations (the conversion class is exactly the
   gate the `.txt` extension was dodging).
3. `rg -l 'pending_improvs\.txt' doc/ tests/ helpers/ desktop/` → only
   inside `pending_improvs.md`'s own legacy-citation note (0 stale path
   references); per-file spot check on the other 11 names; the 38 vault
   item-#11 anchors intact.
4. `pytest tests/test_frontmatter_schema.py::TestCorpusWalker::test_live_corpus_is_clean -q`
   passes; `check_proposal_lifecycle()` fatal `[]`.
5. `make search-fresh` rc=0; the converted files are queryable by
   `doc_query`.
6. The pending-improvements slice proposal(s) carry the backlog-recording acceptance criteria; their S3/S4 content writes into the file this proposal converts.
7. Eval gate: N/A — no query-visible semantics changes; recorded so the
   house rule is not silently skipped.

## 5. Non-goals & rollback

Non-goals: `doc/local/**` data artifacts (`eval.txt`, `jev_pilot` prompt
batches), the evidence artifact
`archive/graph/evidence/p22/scale_probe.py.txt`, canonical
`doc/improvements/pending.md`, `findata/**` vault notes, and the backlog
content itself (sibling `pending_improvs.md`).

Rollback: one revert per slice — S1/S2 are renames + reference edits
(`git revert` restores paths and text); nothing this proposal deletes is
unrecoverable.
