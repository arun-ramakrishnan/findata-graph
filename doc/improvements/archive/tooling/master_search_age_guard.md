---
title: "Master query — the federated search front door and its age guard"
status: executed
filed: "2026-10-05"
executed: "2026-10-05"
completed_md: "351"
area: "helpers/misc/master_query.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Master query — the federated search front door and its age guard

**Date:** 2026-10-05 · **Status:** EXECUTED ·
**Area:** `helpers/misc/master_query.py` (new federated CLI) +
`helpers/misc/search_tui.py`/`search_tui_app.py` (memory lane) +
the `memory_search` sidecar builder/query pair

## 1. Motivation

The repo keeps six corpus search surfaces (docs, notes, scripts, memory,
convo, gates), each with its own CLI, freshness target, and drift
posture. A session that asks "what do we know about X" must first pick
the right index — and the right *freshness mechanism* — before it can
ask. That choice is exactly the kind of recall friction the query-CLI
program (AGENTS.md "Query, don't scan") exists to remove: the surfaces
already share one contract shape (`--json`, grouped hit dicts,
degrade-don't-fail), so the missing piece is a CLIENT, not more
indexes.

The settled #229 doctrine applies unchanged: **unification lives in
the client** — fan out, render grouped with per-leg ranking, and never
blend the legs' score spaces (BM25, fused RRF, similarity and report
rows are incomparable).

## 2. Design

- `master_query.py` fans one query out over the corpus legs in a thread
  pool and renders GROUPED per-leg results with per-leg status + index
  ages. Backends are the `search_tui.run_lane` adapters — the CLI owns
  no backend logic (S1).
- `--flat` is the one cross-leg blend, rank-based only: RRF over each
  leg's hit RANKS, leg-tagged, ties broken by canonical leg order (S1).
- Every leg degrades independently; a missing sidecar is that leg's
  status line, never a failed query. Exit 0 when at least one leg
  answered, 1 when every leg degraded, 2 usage error (S1).
- `--age-guard [HOURS]` (default 24): legs whose backing sidecar's
  mtime is older than the threshold are SKIPPED up front with the age
  and the refresh command in the status — stale-index hits never reach
  the caller. `gates` is exempt (self-refreshing before every query
  verb); `code`/`literal` are exempt (stateless). A skipped leg does
  not count as "answered" (S2). A wrong-interpreter invocation (bare
  `python` outside the repo venv) exits 2 up front with the
  `.venv/bin/python3` instruction — sentinel-checked via python-dotenv
  (S2).

## 3. Slices

- [x] **S1 — federated CLI + memory lane** (landed 2026-10-05):
  `master_query.py` (six default legs, `--legs all` incl. ripwire/rg,
  `--flat`, `--json`, `--serial`); `search_tui` memory lane (LANES,
  in-process adapter via `rebuild_memory_search.search_memories`, lane
  keys 1-8, index monitor + status-bar ages); AGENTS.md surface table +
  six-CLIs block; `doc/procedures/search.md` master section + TUI lane
  table (also fixed pre-existing convo-lane drift there). Measured:
  full six-leg hybrid ≈9 s wall, `--bm25` ≈0.9 s, subsets 0.1-3.4 s.
- [x] **S2 — `--age-guard`** (landed 2026-10-05, in-session): the flag
  above + per-leg sidecar age probe + skip statuses carrying the
  refresh command; JSON legs carry `skipped: true`; tests pin
  skip/fresh/missing-sidecar/exempt-leg behavior and the all-skipped
  exit 1. Plus the front-door interpreter guard the first live use
  surfaced (bare `python` → ~/.local/bin/python 3.14 without
  python-dotenv died as a six-leg error cascade): a python-dotenv
  sentinel check now exits 2 with one actionable
  `.venv/bin/python3` line before any leg runs.

## 4. Acceptance criteria

- One call, grouped structured results across all six corpus legs;
  every leg's status visible (hits count or honest degradation reason).
- No score-space blending in default output; `--flat` is rank-RRF only,
  leg-tagged.
- `--age-guard 0.001` skips every index leg with the refresh command in
  the status and exits 1; `--age-guard` off (default) changes nothing.
- Tests: fan-out degrade isolation, rank-RRF order + tie-breaks, CLI
  JSON shape, exit codes, age-guard skip/fresh/exempt matrix; ruff/ty
  clean; md-lint clean.

## 5. Non-goals

- No new index, no schema changes, no API endpoints (CLI-only, mirroring
  #229's client-side unification).
- No cross-leg SCORE fusion; no semantic re-ranking of the merged view.
- The TUI keeps its per-lane workflow; no combined lane.
