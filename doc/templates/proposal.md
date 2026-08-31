---
title: "<Title — imperative, names the mechanism>"
status: proposed
filed: "YYYY-MM-DD"
executed: null
completed_md: null
area: "<primary code/doc surface>"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# <Title — imperative, names the mechanism, e.g. "Parallel cold embed — process pool for the bge-small path">

<!--
House proposal skeleton (matches the executed corpus's shape — see
doc/improvements/archive/ for real examples). Rules:

- File the proposal BEFORE implementing multi-slice work (house rule
  2026-08-21). One proposal per arc; slices inside it.
- Every number is MEASURED on this box — a proposal with unmeasured
  claims gets challenged; keep the raw log in the Appendix.
- Tables for comparisons, prose for causality.
- On EXECUTED: git mv to ../archive/<topic>/, completed.md entry (unique
  number), pending.md sweep, archive/README topic line, README pointer
  reset, `make search-fresh APPLY=1` — the full checklist lives in the
  proposals README.
- If a proposals frontmatter contract lands, this template gains it —
  until then the bold-line header below is the canonical status field.
-->

**Date:** YYYY-MM-DD · **Status:** PROPOSED ·
**Area:** <subsystem / files the arc touches>

## 1. Motivation

<The pain, with the measured cost table that makes it undeniable. Name
the trigger (user report, drift audit, gate failure) and date.>

## 2. Evidence (measured YYYY-MM-DD, this box)

| Configuration | Result | Verdict |
|---|---|---|
| status quo | — | baseline |
| candidate | — | adopt / dead |

<One paragraph: what the numbers mean and what was ruled out —
"measured, do not re-audit" entries save future sessions.>

## 3. Design

<The chosen mechanism and the alternatives considered, with the reason
each alternative lost. Slices as a numbered list, each independently
landable: S1 (first), S2, ... Order matters; say what unblocks what.>

## 4. Acceptance criteria & shakedown

1. <objective, runnable criterion — a command and its expected shape>
2. <gate that must stay green>
3. <repeat-count for anything timing- or concurrency-shaped — never one run>

| Projected outcome | Today | After |
|---|---|---|
| <metric> | <measured> | <projected> |

## 5. Risks

- **<risk>** — <mitigation>.

## 6. Non-goals

<What this arc deliberately does NOT touch, so future sessions don't
scope-creep it.>

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| <date> | <command> | <number> | <context> |
