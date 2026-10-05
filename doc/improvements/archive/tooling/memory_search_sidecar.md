---
title: "Memory search — one FTS5 sidecar over the three harness pools"
status: executed
filed: "2026-10-05"
executed: "2026-10-05"
completed_md: "352"
area: "helpers/misc/memory_query.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Memory search — one FTS5 sidecar over the three harness pools

**Date:** 2026-10-05 · **Status:** EXECUTED 2026-10-05 (completed.md #352) ·
**Area:** helpers/misc/memory_query.py, helpers/maintenance/rebuild_memory_search.py.
Filed after implementation (operator forgot to file first) so future
sessions have search context for the feature; executed the same day.
Implementation: the `memory_search` patch; the federated front-door leg
over it lives in #351 (`master_search_age_guard`).

## 1. Motivation

Three harnesses keep three memory pools (zcode markdown pool +
MEMORY.md index, prime-rlm `harness_state.json` global store, opencode
logfmt records), and a session asking "what did we settle about X" had
to read whole pools. One FTS5 sidecar (`memory/memory_search.db`) with
a thin CLI (`helpers/misc/memory_query.py`, 104 lines) makes harness
memory the sixth query surface beside docs/notes/scripts/convo/gates —
the AGENTS.md table row agents already follow.

## 2. Evidence (measured 2026-10-05, this box)

| Check | Result | Verdict |
|---|---|---|
| index rows by pool | 86 total: opencode 31 / prime 28 / zcode 27 | adopt — all three pools covered |
| sidecar shape | FTS5 `memory_search` + `_data/_idx/_content/_docsize/_config`, per-source mtime+hash meta, 1.8 MB | adopt — staleness is detectable per source |
| CLI hybrid query | "stg refresh scope" → 3 hits, zcode pool, 1.6 s | adopt |
| `--kind` filter | "embed cache --kind opencode" → 2 hits, `path#record` locators | adopt — recall unit is the record, not the section |
| stale-index behavior | warns with the refresh command, still answers | adopt — outdated beats none (matches doc-query doctrine) |
| tests | `test_rebuild_memory_search` 19 passed, `test_master_query` 29 passed | adopt |
| ruff/ty on touched helpers | clean | adopt |

The stale warning fired during measurement (a pool had moved) — the
behavior above is observed, not asserted.

## 3. Design

- **S1 — sidecar builder** (`helpers/maintenance/rebuild_memory_search.py`,
  819 lines): pool readers (zcode markdown + MEMORY.md, prime
  harness_state.json, opencode logfmt) normalize to one row shape with
  the recall unit as locator (file path; `path#record` for
  prime/opencode), FTS5 index + meta table.
- **S2 — CLI** (`helpers/misc/memory_query.py`): `--kind` pool filter,
  `--json`, `--limit`, exit 1 on missing index / 0 answered (possibly
  empty) / 2 usage error; stale-warns-and-answers, missing-is-fatal.
- **Boundary with #351:** the federated `master_query` memory leg and
  the search_tui memory lane read this sidecar but belong to
  `master_search_age_guard` — this proposal owns sidecar + CLI only.

## 4. Acceptance criteria & shakedown

1. `memory_query.py "<query>" --limit 3` returns ranked hits with
   `path`/`path#record` locators on this box — met (§2 rows 3–4).
2. `test_rebuild_memory_search.py` + `test_master_query.py` green —
   met (19 + 29 passed 2026-10-05).
3. `make static-checks` all pass; `make md-lint` 0 — met at archival.
4. Stale pool → warning + answers; missing index → exit 1 — met
   (warning observed live; missing path is a two-line branch).

No ontology eval-gate bullet: nothing here alters query-visible
semantics of rosters, crosswalks, hierarchies, or extractor rules —
N/A stated explicitly per the house rule.

| Projected outcome | Before | After |
|---|---|---|
| harness pools queryable in one call | 0 (read whole pools) | 3 pools, 86 rows, ~1.6 s |
| recall unit | whole file/pool | record locator |

## 5. Risks

- **Stale reads** — pools move mid-session; mitigated by per-source
  meta + warn-and-answer (never silent).
- **Pool-format drift** — a harness changes its store shape;
  mitigated: readers are per-pool and the rebuild test pins the row
  shape.

## 6. Non-goals

No write path (memory mutation stays with the harness tools); no
re-ranking across pools (the federated leg in #351 owns ordering);
no duplicate of convo_search (sessions) or doc_search (conclusions) —
memory holds doctrine, each with its canonical home.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-05 | `memory_query.py "stg refresh scope" --limit 3` | 3 hits, 1.6 s, stale warning fired | warning names the refresh command |
| 2026-10-05 | `memory_query.py "embed cache" --kind opencode --limit 2` | 2 hits, `path#record` locators | kind filter works |
| 2026-10-05 | sqlite row/kind counts on `memory/memory_search.db` | 86 rows (31/28/27) | FTS5 + meta tables present |
| 2026-10-05 | `pytest tests/test_rebuild_memory_search.py tests/test_master_query.py` | 19 + 29 passed | hermetic |
