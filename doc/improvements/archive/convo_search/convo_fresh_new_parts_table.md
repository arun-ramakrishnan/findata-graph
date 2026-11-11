---
title: "convo-fresh new-parts breakdown — a per-harness tally of what the refresh would harvest, in APPLY and check"
status: executed
filed: "2026-11-11"
executed: "2026-11-11"
completed_md: "503"
area: "helpers/maintenance/harvest_conversations.py, helpers/maintenance/rebuild_convo_search.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# convo-fresh new-parts breakdown — a per-harness tally of what the refresh would harvest, in APPLY and check

**Date:** 2026-11-11 · **Status:** EXECUTED · **Completed:** completed.md #503 ·
**Area:** convo-fresh harvest lanes + check mode + rebuild embed accounting

## 1. Motivation

Operator ask (2026-11-11): "print a neat table of all the new
sessions/parts it encountered for refresh/embedding". Two gaps sat behind
it:

- `make convo-fresh` (default `--check`) printed only the coverage/drift
  block and the verdict — a no-change run was indistinguishable from a
  broken one, and there was no per-harness accounting of what a refresh
  WOULD do before committing to it (the APPLY lanes print activity, but
  check never ran the lanes at all).
- Embed cost was never shown because nothing measured s/row: the rebuild
  knew how long its embed callback took on cache misses but threw the
  timing away.

## 2. Design (as landed 2026-11-11)

**Tally plumbing.** All five lane fns (`_harvest_opencode`/`_prime`/
`_zcode`/`_dsh`/`_bili`) take kwarg-only `tally=None` and record
units/parts/quiet/skipped per harness via `_tally(tally, lane, kind,
label, n)`. Inert for positional callers, so existing lane tests are
untouched.

**Rendering.** `_format_new_table(tally, embed_rate, lanes)`
(~helpers/maintenance/harvest_conversations.py:2477) renders a
"new-parts breakdown" table: per-harness rows (new units, new parts,
detail = top-6 units by parts + "… +N more"), quiet/skipped counts, a
total row, and — only when the rate is known —
`est embed ≈Ns (P parts @ R s/part, last measured embed)`.
`_read_embed_rate(con)` (:2458) reads the `convo_meta` key
`embed_s_per_row`, None-safe. APPLY prints the table after lane output
(~:3208).

**Check mode — the load-bearing trick.** `harvest(check=True)` now
dry-runs the lanes on the read-only store with a `SessionWriter(corpus_root)`
that NEVER flushes. Safe by construction, not by discipline:
`Store.set_watermark`/`register` only buffer in memory
(`_wm`/`_pending_wm`/`_pending_parts`) and `commit()` RAISES on
`read_only` (harvest_conversations.py:226); the writer touches no disk
until `flush()`, which never happens. The writer wraps the REAL
`corpus_root` so opencode per-session seeds (`_corpus_max_ts`,
:673/:785) read the same files APPLY would — the numbers shown are
exactly the next APPLY's. The check return dict gains
`tally`/`embed_rate`/`table`/`elapsed` (`elapsed` was hardcoded
0.0 — now real). `main()` prints the table after the coverage block
(:3186). Verdict/exit semantics untouched: STALE exit 1 comes from the
`_drift` mirrors, which fail LOUD on corruption where the lanes would
SKIP.

## 3. `embed_s_per_row` — the estimate's fuel

helpers/maintenance/rebuild_convo_search.py (~:1323) records
`embed_s_per_row = round(miss_embed_callback_seconds / cstats["misses"], 4)`
into `convo_meta` when a real cache-miss embed carries timing; the
pseudo/dims-mismatch paths have no timing, so the key is absent and the
estimate line is suppressed — never faked. The live index
(embed_model=granite-embedding-97m-r2, embed_dims=384) has no key yet;
it appears after the next real rebuild embed.

## 4. Regression caught mid-implementation (kept as a record)

The first 27-tuple edit batch deleted bili's version-bump condition
`if wm is None or wm.get("v") != cur["v"]:` (~:1517), leaving
`writer.fresh.add` unreachable inside the quiet-continue branch —
`test_bili_version_bump_writes_fresh_without_merging` failed
(2 rows where 1 expected). HEAD-green→tree-red was proven in a throwaway
git worktree (HEAD 588a94586) before touching anything; the line was
restored and the other four lanes verified intact (they use different
bump logic — no `wm.get("v")`). Lesson: batch edits across a 3k-line
file need the lane suite run between batches, not after.

## 5. Acceptance criteria & shakedown (as landed)

- The table prints in BOTH modes; APPLY behavior otherwise unchanged;
  check persists NOTHING — purity test asserts
  `watermark("bili:ses_x") is None` after `harvest(check=True)`.
- Targeted suite: test_dsh_lane.py, test_harvest_bili.py,
  test_opencode_v2_lanes.py, test_harvest_retention.py,
  test_harvest_reasoning.py + new tests/test_harvest_new_table.py (4
  tests: render+estimate, empty fallback, bili tally roundtrip,
  check-mode dry-run purity) = **84 passed, 1 skipped** (intentional
  `--since` skip, test_harvest_retention.py:440). ruff clean.
- Live check run: table shows `dsh:live:session-3c347: 171 parts`
  (that session is the conversation it ran in), bili 1, total 5 units /
  172 parts; verdict STALE exit 1 preserved (pre-existing drift); check
  wall 9.05 s.

## 6. Risks

- The check-mode writer wraps the real corpus_root; a future lane change
  that flushes mid-lane would WRITE in check mode. The purity test pins
  current behavior — a stray `flush()` inside a lane fails it loudly.
- The estimate is one scalar from the last rebuild's cache-miss batch;
  embed speed varies with batch composition. The line says "≈" and names
  its source ("last measured embed").

## 7. Non-goals

- No index-schema, watermark, or lane-semantics changes; APPLY gains
  only the printout. No estimate until a real embed has been measured.
  No per-part listing — the table is a breakdown, not a roster.
