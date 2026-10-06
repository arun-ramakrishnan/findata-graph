---
title: "Share one query vector across the master_query fan-out"
status: executed
filed: "2026-10-06"
executed: "2026-10-06"
completed_md: "360"
area: "helpers/misc/master_query.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Share one query vector across the master_query fan-out

**Date:** 2026-10-06 · **Status:** PROPOSED ·
**Area:** helpers/misc/master_query.py + search_tui lanes + doc/script/convo CLIs + note/memory cores

## 1. Motivation

Every hybrid `master_query` embeds the same text with the same granite
model five times: three child processes (docs, scripts, convo) each
load the GGUF, and the parent loads it once more for the notes + memory
legs. Same text + same model = identical 384 floats, recomputed 5×.
Measured 2026-10-06 (warm page cache, this 4C box):

| Configuration | Result | Verdict |
|---|---|---|
| fresh-process `embed_query` (import + load + 1 forward) | 1.34s wall | the unit cost, paid 4× per query |
| one subprocess leg (`doc_query --json`) | 1.53s wall | load dominates the leg |
| full `master_query "embed cache"` (parallel, 6 legs) | 5.0s wall / 15.3s CPU | ~3× CPU inflation = redundant loads |
| post-fix stability | 3/3 parallel runs rc=0, stderr empty | race fixed; waste remains |

Trigger: operator report 2026-10-06 ("master_query is too flaky") —
the flake was the `_EMBED_LOCK` race (fixed same day); the waste it
exposed is this arc. Projected after: 1 parent load + 3 model-free
child startups ≈ ~2s wall.

## 2. Evidence (measured 2026-10-06, this box)

Query-side embed call sites (hybrid mode), all defaulting to the house
granite model:

| Leg | Call site | Process | Today |
|---|---|---|---|
| docs | `doc_query.py` → `rebuild_doc_search.query_embedder()` | child | own load |
| scripts | `script_query.py` → same pattern | child | own load |
| convo | `convo_query.py:121` → `le.embed_query` | child | own load |
| notes | `note_query.semantic_hits:165` → `embed_query(query)` | parent | shared singleton |
| memory | `search_memories` hybrid cosine | parent | shared singleton |

What the numbers mean: the parent legs already share one load (via the
module singleton); only the fan-out boundary multiplies it. The vector
is identical across legs by construction (same text, same model tag),
so sharing is exact, not approximate. Ruled out: unifying the DBs
(each leg's index stays where it is — this arc shares the vector, not
the stores); an embedder daemon (saves ~1.4s/query for a managed
long-lived process — negative value); going all in-process (kills
crash isolation; one segfaulting leg would take the whole query
instead of degrading one leg — explicitly rejected a day after a
silent-death in this area).

## 3. Design

Embed once in the parent, fan the vector out, stamp-check at every
leg. Slices as numbered, in order:

- **S1 — parent embeds once**: `fan_out` calls `le.embed_query`
  (bm25 mode skips — no vector needed), threads the vector + model
  stamp through `run_lane` into the two in-process legs
  (`semantic_hits`, `search_memories` gain an optional `query_vec`).
  Standalone behavior unchanged when no vector is passed.
- **S2 — subprocess legs accept a vector**: `--query-vector '<json>'`
  on `doc_query.py`, `script_query.py`, `convo_query.py` (≈3KB argv);
  when present the leg skips its own model load. No flag = today's
  path, so the CLIs stay standalone-testable.
- **S3 — stamp check + fallback**: each leg compares the vector's
  `(model_label, dims)` against its index's stored stamp and falls
  back to its own embed (status names it) on mismatch — a pseudo
  vector must never score against real index rows, and a stale vector
  must never silently rank. Regression tests: forced-mismatch leg
  degrades with status intact; serial-vs-shared vectors bit-identical.

Alternatives considered: (A) daemon — rejected above. (B) all
in-process — rejected above. (C) query-vector cache in
`embed_cache` — orthogonal (saves re-embed of repeat queries, not
the 4× per-query loads); left for a future arc.

## 4. Acceptance criteria & shakedown

1. `master_query "embed cache"` (parallel): one model load per query
   (observable: `EMBED_TRACE`-style counter or timing — wall ≈ ~2s
   vs today's 5.0s; CPU inflation ~1× vs ~3×).
2. Shared-vs-own vectors bit-identical: `--serial` run with and
   without the fan-out vector diffs empty on all five legs.
3. Forced stamp mismatch (tampered label) → leg falls back to its own
   embed, status says so, no silent ranking.
4. Standalone CLIs untouched: `doc_query`/`script_query`/`convo_query`
   without `--query-vector` behave exactly as today (existing suites
   green, no flag required).
5. Gates stay green: ruff, `ty check`, targeted pytest (new vector
   tests + leg suites), static_checks rc=0, md-lint 0.

| Projected outcome | Today | After |
|---|---|---|
| model loads per hybrid query | 4 (3 child + 1 parent) | 1 (parent) |
| master_query wall (warm) | 5.0s | ≈2s |
| CPU inflation vs wall | ~3× | ~1× |
| crash isolation | per-leg (subprocess) | unchanged |

## 5. Risks

- **Stamp skew across indexes** — an index rebuilt under a different
  model tag rejects every shared vector (fallback path, slower but
  correct). Mitigation: S3 fallback + status; `search-fresh` keeps
  stamps uniform in practice.
- **Argv size / quoting fragility** — 384 floats as JSON ≈ 3KB, far
  under limits; quoting handled by argv arrays (no shell), same
  contract as the existing `--json` legs.
- **Lock contention on the parent embed** — the single parent embed
  holds `_EMBED_LOCK` for ~ms warm; legs then run vector-only and
  never touch the model. No new contention vs today.
- **Scope creep into CLI refactors** — the `--query-vector` flag is
  additive only; any leg that resists clean threading keeps its own
  load (S2 is per-leg, independently landable).

## 6. Non-goals

Unifying the five index DBs; the embedder daemon; the query-vector
cache; touching opencode/prime harvest or `convo-fresh` paths;
changing `--bm25` behavior (no vector involved); any embedding-model
swap (granite stays pinned).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-06 | fresh-process `embed_query` | 1.34s wall | import + GGUF load + 1 forward, warm cache |
| 2026-10-06 | `doc_query.py "embed cache" --json` | 1.53s wall, clean JSON, empty stderr | load dominates; no fd leakage solo |
| 2026-10-06 | `master_query "embed cache"` parallel ×3 | 5.0s wall / 15.3s CPU each; rc=0; all legs answered | post-race-fix baseline |
| 2026-10-06 | barrier-forced 2-thread `embed_query` (pre-fix) | silent exit 0, no output | the flake; fixed via `_EMBED_LOCK` |
| 2026-10-06 | same repro (post-fix) | 1.43s, both vectors, stdout intact | fix verified |
