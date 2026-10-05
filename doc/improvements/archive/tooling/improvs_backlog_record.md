---
title: "Record the pending-improvements backlog into the perpetual tracker, then fold the working copy away"
status: executed
filed: '2026-10-05'
executed: '2026-10-05'
completed_md: '348'
area: "doc/improvements/archive/tooling/pending_improvs.md, doc/local/pending_improvs.md"
---

# Proposal: Record the pending-improvements backlog into the perpetual tracker, then fold the working copy away

**Date**: 2026-10-05
**Status**: EXECUTED 2026-10-05 — the last two slices of the pending-improvements
arc (S3/S4, moved out of the sibling `markdown_fold` proposal at
filing; markdown_fold itself executed + archived as completed.md entry
346). The third sibling, `graph_rebuild_fast_path.md` (backlog rows
4/5/6/12 as S1–S4), is live awaiting its execution go.

## 1. Motivation & operator ruling (2026-10-05)

> `doc/improvements/archive/tooling/pending_improvs` is the **perpetual**
> improvements tracker. First record the entire pending analysis there —
> every category, including DB, Onager and the other gated items. When
> the arc is done, fold things into that same archive file. No fresh
> file.

The whole 2026-10-05 backlog analysis lives only in the gitignored
`doc/local/pending_improvs.md` (`.gitignore:2`) — outside git, outside
the archive, temporary by construction. The tracker is now a Markdown
file (markdown_fold #346 converted it and its siblings), so the record
is unblocked. This proposal carries the content: the working copy's §2
table (21 rows, 5 categories, evidence locators) IS what gets recorded,
at its current re-verdicted truth.

## 2. Design — S1/S2

- **S1 — record the backlog.** Write all 21 rows with their evidence
  locators into `doc/improvements/archive/tooling/pending_improvs.md`
  as the file's main body, from the working copy's §2 at record time
  (census as of 2026-10-05 second pass: 4 rows FILED to
  `graph_rebuild_fast_path.md` awaiting execution go, 8 CLOSED, 2
  NO-ACTION, 7 BLOCKED — the table, not this census line, is the
  record). Banner rewritten: HISTORICAL → **perpetual improvements
  tracker**; the A–F bundles become a dated historical section; the
  item-number "citations resolve here" promise is kept verbatim; a
  *Where tracking lives now* note names `doc/improvements/pending.md`
  as the committed live-trigger list and links the two sibling
  proposals. The stale `.txt` sibling names in the old banner
  (`graph_improvs.txt`, `hierarchy_design_roadmap.txt`) are repointed
  to their converted `.md` names while editing that block.
- **S2 — close-out fold.** When the operator calls the arc done
  (graph_rebuild_fast_path executed or explicitly parked), merge
  anything added to the working copy while the arc ran into the same
  tracker, then delete `doc/local/pending_improvs.md`. Single home
  thereafter; **no fresh file is created at any point**.

Order: S1 precedes S2; S1 can land the moment this proposal is
accepted.

## 3. Acceptance criteria & shakedown

1. The tracker carries **all 5 categories and all 21 rows** with their
   evidence locators, at record-time verdicts.
2. Its banner says perpetual tracker; the A–F history and the
   item-number promise survive; canonical `git diff --
   doc/improvements/pending.md` is empty (the backlog row pointing at
   pending.md's P2.2 section cites it, never edits it).
3. After S2: `doc/local/pending_improvs.md` absent and **no new
   archive file** (`git status` shows the tracker modification only).
4. `make search-fresh` rc=0; `doc_query "perpetual improvements
   tracker"` / `doc_query "2026-10-05 survey"` returns the recorded
   rows at `archive/tooling/pending_improvs.md`.
5. Gates: `make md-lint` 0 on the touched file,
   `check_proposal_lifecycle()` fatal `[]` (frontmatter corpus test
   rides qa).
6. Eval gate: N/A — no query-visible semantics (rosters, crosswalks,
   hierarchies, extractor rules) changes; recorded so the house rule
   is not silently skipped.

## 4. Risks & non-goals

Risk: the tracker is a citation target (~45 note stubs + several
doc/test files cite "item #n" / "Bundle Xn") — the banner rewrite must
preserve those anchors, which is why the A–F section is kept verbatim
as dated history rather than summarised.

Non-goals: the verdicts themselves (the table is a record of measured
state — changing a verdict is new work in its own proposal, e.g.
graph_rebuild_fast_path for rows 4/5/6/12), canonical `pending.md`,
`findata/**` vault notes, and the conversion sweep (done, #346).

Rollback: S1 is content in one file; revert restores the HISTORICAL
banner. S2 is one deletion; the working copy is gitignored, so the
backlog is never single-sourced in git until S1 lands — that is the
point.

## Execution results — 2026-10-05 (S1+S2)

- **S1 (record):** all 21 rows recorded into
  `archive/tooling/pending_improvs.md` — perpetual banner, A–F kept as
  dated history with the item-number citation promise verbatim, the
  stale `.txt` sibling names repointed (`sqlite_improvs` noted as the
  long-removed zero-byte stub), and the *Where tracking lives now* note
  naming `doc/improvements/pending.md` as the committed live-trigger
  list. md-lint 0; `doc_query "perpetual improvements tracker"`
  returns the recorded rows.
- **S2 (fold-away):** the working copy's mid-arc updates (rows
  1–3/8/11/13 re-verdicted; rows 4/5/6/12 filed-then-executed through
  `graph_rebuild_fast_path.md`) merged into the tracker census;
  `doc/local/pending_improvs.md` deleted — single home, and no fresh
  file was created at any point.
