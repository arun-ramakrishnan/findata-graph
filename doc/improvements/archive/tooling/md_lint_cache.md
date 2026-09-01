---
title: md-lint stale-scan cache — lint only what changed since the last lint
status: executed
filed: '2026-09-01'
executed: '2026-09-01'
completed_md: '192'
area: helpers/misc/
---

# md-lint stale-scan cache — lint only what changed since the last lint

**Status:** EXECUTED 2026-09-01 (same day — helper rewrite + sidecar + tests,
verified live) · **Follows:** `archive/tooling/markdown_lint_adoption.md` (#191)

## Motivation

`make md-lint` re-lints the entire 1,314-file corpus on every invocation.
Measured decomposition (2026-09-01): npx warm-cache overhead ≈ 0
(a locally-installed cli2 binary ran identically at 16.8 s), cli2 fixed
startup 0.29 s (one file, config-less dir) — so ~16.5 s is genuine
micromark parsing at ≈12.7 ms/file across doc/ (71) + findata/ (1,244).
Under the qa gate's 4-job contention the step shows 37 s. Parallel
sharding was probed and rejected: two cli2 processes each use ~1.2 cores
internally (2 workers bought only 1.4× wall), and file arguments cannot
scope the run anyway (see Design). Two further probes misled before the
numbers settled, both recorded as traps:

- cli2 command-line globs are **additive** to the config `globs` — every
  "shard" probe run from the repo root silently re-linted the full
  corpus (a 622-file "shard" took as long as 1,314 files).
- Config discovery makes even a one-file probe outside the repo pay the
  full corpus cost when the probe runs under the repo (the scratch-dir
  fix below is what makes subset runs honest).

## Goal

Warm `make md-lint` on an unchanged corpus should cost ~seconds, not
~17 s; an edit to one file should re-scan that file only. Verdicts must
be exactly those of a full run — same violations, same repo-relative
paths, same overrides (findata Tier-1, the 7-file P&F reprint
quarantine), same exit codes.

## Non-goals

- No `--fix` surface (unchanged from #191 — the fixer stays forbidden
  over findata).
- No change to the gate wiring, digest format, or exit codes; the qa
  row stays byte-compatible plus one observability line.
- No shared epoch with search-fresh (see Rejected alternative).

## Design

Verdict = pure function of (file bytes, config bytes, cli2 version).
Record it per file in the gitignored machine-local sidecar
`memory/md_lint_cache.db` (SQLite, same locality doctrine as the embed
and search sidecars — never `research.db`):

- `verdicts(path PK, hash, violations_json)` — `path` is the
  repo-relative posix path, i.e. exactly the string cli2 prints, so
  cached violation lines replay verbatim into the digest.
- `meta(config_hash)` — blake2b of the config file + the pinned cli2
  version. Any rule/override edit or version bump mismatches the hash
  and flushes all verdicts (a stale record can never survive a config
  change).
- Per run: walk the config globs (doc/ + findata/ minus `ignores`),
  hash every file (0.05 s measured for 1,314), re-scan only files whose
  hash is unrecorded, prune records for files no longer walked.

Subset scanning needs a trick because cli2 cannot be scoped by
arguments: a scratch `tempfile.mkdtemp` tree mirrors the repo-relative
layout with one symlink per stale file plus a config copy, and cli2
runs there with cwd = scratch. Relative paths are preserved, so
override `filter`s match identically (verified: a doc/ probe fires the
prose rules, a findata probe fires Tier-1 only — MD047 yes, MD034 no).
A full-cold run (nothing recorded) executes from `REPO_ROOT` directly.

Correctness guards:

- **No poison on execution failure:** when cli2 exits ≥ 2 (npx/config
  error) nothing is recorded — otherwise every file in the failed scan
  would be cached "clean" and its violations silently masked.
- **Degradation:** missing/unparseable config, or a locked sidecar,
  falls back to the uncached full run (the pre-cache behavior).
- **`--full` bypass** streams raw cli2 from the repo root, cache
  untouched — the hand-fix path from #191.
- **Globs edits** change which files the walker visits; they are
  handled by the flush (config hash) + prune (walk) combination, but a
  *narrowed* glob set combined with a config hash that happens to match
  is impossible — globs live in the same hashed file. Documented
  residual: the walker derives roots from `globs` (`<root>/**/*.md`);
  a non-glob glob form would need a walker update (test pins the two
  shipped shapes).

## Rejected alternative: search-fresh's stale set as the lint target

Shortcut proposal: lint whatever `make search-fresh` reports stale.
Rejected on epoch grounds — search-fresh answers "changed since the
last index rebuild", the lint needs "changed since the last lint".
Those reset at different times and the search diff is drained by its
own consumers (`search-fresh APPLY=1`, maint-full) on a schedule the
lint does not control: edit 3 notes → `APPLY=1` per the AGENTS.md
workflow → indexes FRESH → md-lint asks for the stale set, gets
nothing, skips the scan → violations pass a green gate. The indexes
also partition differently (script_search holds no markdown at all;
doc_search 94 vs the lint's ~70; note_search 1,241 vs the lint's
1,244 minus ignores/quarantine). What *is* reusable is the mechanism —
content-hash keys — which both sides already share; only the epochs
must stay separate.

## Results (2026-09-01, live)

| Scenario | Before | After |
|---|---|---|
| Cold (sidecar deleted) | 16.8 s | 19.5 s (mirror build ~+2.5 s, once per flush) |
| Warm (unchanged corpus) | 16.8 s | **0.24 s** |
| One file edited | 16.8 s | ~2 s (`scan: 1/1314`) |
| mtime-only touch | 16.8 s | 0.24 s (content-hash key) |

Live red-path verification: planted bare URL in `doc/design/findata.md`
→ `1 violation(s) in 1 file(s)`, MD034 at the repo-relative path, exit
1; planted MD047 + bare URL in a findata note → MD047 fired, MD034
suppressed (Tier-1 through the mirror); both reverts clean.

Tests (`tests/test_markdown_lint.py`, 15 total): the 9 originals plus
cache-hit-skips-cli2, changed-file-rescanned (mirror holds exactly the
changed file), dirty-verdict-replays-from-cache, config-change-flush,
deleted-file-pruned, execution-failure-does-not-poison. An autouse
fixture points `_CACHE_DB` at `tmp_path` — tests never touch `memory/`.

## Ops notes

- Gate line gains `scan: N/M files (K unchanged, answered from cache)`.
- Sidecar is disposable: `rm memory/md_lint_cache.db` = cold next run.
- qa contention: md-lint's warm 0.24 s effectively removes the 37 s
  row from the contended schedule; pytest keeps the wall clock.
