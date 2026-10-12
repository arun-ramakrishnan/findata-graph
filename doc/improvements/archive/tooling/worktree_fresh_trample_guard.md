---
title: "Worktree fresh trample guard — a behind-worktree APPLY regresses the shared notes surface and bumps the generation (ping-pong)"
status: executed
filed: "2026-10-12"
executed: "2026-10-12"
completed_md: "385"
area: "helpers/maintenance/rebuild_note_search.py, rebuild_common.py, helpers/maintenance/snapshot_db.py, helpers/core/db.py, Makefile, AGENTS.md"
---

# Worktree fresh trample guard

**Date:** 2026-10-12 · **Status:** PROPOSED ·
**Area:** `helpers/maintenance/rebuild_note_search.py`, `rebuild_common.py`,
`helpers/maintenance/snapshot_db.py`, `helpers/core/db.py`, `AGENTS.md`

## 1. Problem

`memory/` is a symlink: every worktree shares ONE `research.db` (by
design — one notes/graph surface). Each tree runs its own
`search-fresh APPLY=1`, and the trigger is not manual: the AGENTS.md
doc-edit rule instructs every agent session to run `make search-fresh`
after `doc/**` edits, so during active development freshes fire
automatically from whichever tree the session lives in — with
concurrent model/session worktrees, that is both trees all day.

An incremental APPLY computes its delta as **(my working set) vs (the
shared index's stored fingerprints)**. When the working tree is BEHIND
(a few patches old — the normal state of a patch-testing worktree),
that delta is non-empty *by construction*: newer rows look changed,
not-yet-existing rows look deleted. The apply then:

1. **rewrites the shared index BACKWARD** to its older content
   (measured: wt apply left `doc_search` at 3185 section rows where
   main had 3186);
2. **bumps `db_meta.generation`** — by design (`rebuild_note_search.py`
   bumps when `to_upsert or to_delete` is non-empty; the bump is the
   writer-side staleness signal for warm DuckDB consumers).

The main tree's next fresh rewrites forward and bumps again. One
round-trip = two bumps + two full-surface rewrites. During active
development the operator expects **hundreds of these per day**.

Downstream, every bump invalidates the standing snapshot and every
warm cache: `snapshot_check`/`integrity` gate legs fail with a
generation MISMATCH that looks like a ghost (chased live 2026-10-12,
four times before noon), and each bump costs every consumer a ~2s
`connect()` rebuild. The surface-content regression windows are the
worse half: between wt-apply and main-apply the shared index serves
OLD note content.

**The shared store is load-bearing.** The dbs are untracked, so the
alternative to the symlink is a frozen per-worktree copy: its own
generation counter, its own note surface, queries answering a dead
world, applies writing a db nobody reads — split brain. One shared
`research.db` is the correct model for concurrent sessions; the
hazard is therefore never storage topology but **working-set skew at
the apply layer** (S2 gates exactly that), and behind-worktree
sessions that need to validate indexing changes need a preview lane,
not a private store (S5).

## 2. Evidence (measured 2026-10-12, live reproduction)

Instrumentation: temporary audit — `ghost_audit(ts, tbl, op, caller)`
plus a caller-tagging wrapper inside `bump_generation` (both trees),
and `trg_ghost_audit_*` triggers on `entities`/`graph_edges`.

| step | generation | audit |
|---|---|---|
| baseline | 415305 | 0 |
| `search-fresh APPLY=1` from **wt** (one patch behind) | → **415306** | `rebuild_note_search.py:<wt line>:rebuild` |
| `search-fresh APPLY=1` from **main** | → **415307** | `rebuild_note_search.py:1156:rebuild` |

- The wt's apply recorded its own corpus in the wt's private
  `outputs/wt/graph_algos/outputs/gate_runs.duckdb` — invisible to
  main-tree corpus reads, which is why the bumps looked like ghosts.
- Corpus summaries show the regression concretely: the wt apply left
  `doc_search` at **3185** section rows; main's next apply restored
  **3186**.
- No-op applies are clean: search-fresh / convo-fresh / analytics-fresh
  on an unchanged corpus bump nothing (the conditionals
  `content_changed` / `to_upsert or to_delete` / `count or gc` hold) —
  the ping-pong needs ONLY a behind working set, never an operator
  mistake.
- Bump sites (all by design): `rebuild_note_search.py:1065` (full
  branch), `:1156` (incremental), `helpers/graph/embeddings.py:395`
  (company_embeddings), plus the `entities`/`graph_edges` trigger set
  (fires on value-identical UPDATEs too).

## 3. Design

### S1 — working-set stamp at apply time

Every indexing APPLY (note/doc/script/memory search) records in its
index meta: the tree's HEAD sha, the worktree name
(`_git_worktree_name()` already exists in `tests/run_gate_report.py`;
move the helper to a shared home), and a timestamp. Cost: one
`git rev-parse` per apply (~10 ms). This is the fact everything else
reads.

**Snapshot contract (amended 2026-10-12, F4).** The stamp lands in each
index's key/value meta table, so it shows up as extra rows in
`db_meta`. Dropping them from the *verify* count is not enough: the
verifier filters the live side (`_sqlite_live_count` drops `apply_*`)
while the *export* does not (`_sqlite_table_arrow` runs `SELECT *`), so
the first `make snapshot` after a stamping apply writes the stamp rows
into `db_meta.parquet` and `--check` then reports
`Parquet MISMATCH: sqlite/db_meta: 6/3` permanently — the same
self-inflicted drift class S3 exists to remove. Exclude the keys at
**export**, mirroring the `db_bump_log` exclusion in
`_list_sqlite_tables` on the same argument (operational metadata, not
content, and machine/HEAD-specific — a restored copy would otherwise
claim a foreign tree's last apply).

### S2 — refuse backward applies (the guard)

At APPLY start: if the recorded last-apply HEAD exists and the current
HEAD is an **ancestor** of it (`git merge-base --is-ancestor`), this
working set is older than what the index reflects — the delta would
regress the surface. Refuse: non-zero exit, named reason
("index reflects <newer-sha>; this tree is behind — sync or pass
`--allow-stale`"), and **no writes**. Same-HEAD and forward applies
behave exactly as today. `--allow-stale` documents intent and proceeds.

**Divergence (amended 2026-10-12, F8).** "Neither ancestor nor
descendant" is a **refusal**, not a pass. A patch-testing worktree with
its own commits on top is *divergent*, not behind — which is the shape
the guard most needs to catch and the one an ancestor-only predicate
misses (the live `graph_algos` worktree, `0d2ffd297`, is exactly this:
neither ancestor nor descendant of `main`). The predicate is therefore
three-valued: ancestor → refuse; same/forward → apply; divergent →
refuse unless `--allow-stale`.

**Eval gate (semantics change):** the refusal path gets a test pinning
(apply at head A → simulate behind tree → apply refused, index rows
byte-identical, generation unchanged; `--allow-stale` applies and the
existing suites — which all run same-HEAD — stay green unmodified),
**plus the divergent pair** (stamp at a HEAD that is neither ancestor
nor descendant → refused; `--allow-stale` → applies). Uncommitted edits
are NOT stale (HEAD equal, content newer) and must keep applying: the
common agent fresh-after-edit case.

### S3 — name the bump, don't ghost it

Make a permanent, trimmed bump log (ring table, last ~100: ts, caller
`file:line`, worktree name — the current hunt instrumentation made
supportable) and have `snapshot_db --check` MISMATCH messages cite it:
"source moved +2 since snapshot; last bump 17:52 by
rebuild_note_search.py:1156 (worktree graph_algos)". A stale snapshot
then reads as a named event, not a mystery. Replaces the temporary
`ghost_audit`/caller-wrapper hunt scaffolding.

**Wiring correction (2026-10-12, F3).** As implemented the citation is
attached to `--quick` (`_cmd_quick`) only, and `--quick` is
generation-only — it can never annotate the **generation-matching**
table MISMATCH that `--check` emits as
`Parquet MISMATCH: sqlite/<table>: <snap>/<live>`. That class is not
hypothetical: the 2026-10-12 incident behind `SOURCES_TABLES` was a
fail-closed verify refusing to drop ~370k live rows with generations
equal, and `tests/run_gate_report.py` already names the archaeology as
living in `snapshot_check`. So the citation must also be emitted by the
`--check` leg (next to the `_verify_parquet_snapshot` warning and the
`_cmd_check` sources branch), and the `db.py` comment claiming
`--check` becomes true. Two smaller defects in the citation itself:
`source moved +N` renders `+-N` when the snapshot is ahead of live
(post-restore), and a restored (bump-log-less) index reads identically
to a trigger-driven bump.

### S4 — etiquette follows enforcement

AGENTS.md doc-edit rule gains one line: in a worktree, a refused fresh
is expected — sync (or `--allow-stale` with reason) instead of
retrying. Doc-only change, lands with S2.

### S5 — sandbox apply: validate a behind tree without touching the shared surface

A patch-testing worktree legitimately needs to know "does my rebuild
produce a sane delta?" — S2's refusal blocks its only way to see that.
Add `--sandbox`: copy `research.db` to a scratch path (~314 MB, one
file copy ≈ 1–2 s), run the full apply against the copy, print the
same delta/count summary, discard. The shared surface is untouched
(no bump possible), the session gets its validation, and the
copy-per-run cost is trivial next to the embed work it may trigger.
Deferred slice — S2 + S3 remove the urgency; S5 removes the temptation
to bypass.

**Operator usage (recorded 2026-10-13 — keep, this is the part you reach
for).** `--sandbox` is a preview lane, not an apply: it answers "what
would my rebuild do?" without writing anything shared.

- *What it does:* copies the live index DB to a scratch dir (SQLite
  through the online-backup API so WAL pages come along; DuckDB
  sidecars with their `-wal`/`-shm`), runs the full rebuild against
  the copy, prints the same delta/count summary as a real apply, then
  discards the scratch dir. The shared index, its apply stamp and its
  generation are untouched — a sandbox run cannot bump anything, by
  construction — and it skips the S1 stamp write, so it never claims
  the shared index reflects your working set.
- *How to invoke:* one index —
  `python3 helpers/maintenance/rebuild_note_search.py --sandbox`; all
  four — `make search-fresh ARGS="--sandbox"` (the Makefile drops the
  default `--check` for you; the flags are mutually exclusive).
  `--sandbox` is exempt from the S2 refusal: a behind tree that would
  be REFUSED on apply still gets the full preview.
- *When to reach for it:* the guard refuses your apply but you want to
  see the delta before choosing between syncing, `--allow-stale`, or
  doing nothing. Proven 2026-10-12: 3,201 doc rows indexed on scratch,
  shared index byte-untouched.
- *Limits:* one full copy per run (research.db ~314 MB ≈ 1–2 s) plus
  whatever embed work the delta triggers — trivial next to a re-embed,
  but not free. `--sandbox` pairs with nothing: `--check` and
  `--sandbox` together are refused (read-only twins, not companions).

### Slice order / independence

S1 first (S2 reads its stamp). S3 and S4 are independent of S2 and
valuable alone; S3 can land immediately off the existing instrument's
shape. S5 is independent and deferrable.

## 4. Risks

- **Git-less / bare contexts** (some test fixtures): no HEAD →
  warn-only, never refuse (guard needs the stamp to mean something).
- **False refusals**: disjoint-corpus worktrees (a wt that only touches
  `helpers/` still holds an older `findata/**` checkout) — by
  construction its delta vs the shared meta is suspect, so refusal is
  correct; `--allow-stale` is the escape.
- **Divergent worktrees (amended 2026-10-12, F8)**: the first cut
  refused strictly-behind HEADs only, so a patch-testing tree carrying
  its own commits — neither ancestor nor descendant of the stamp —
  still trampled. That is the common worktree shape, not an edge.
  Tightened per §3/S2: divergent → refuse unless `--allow-stale`. The
  `stale_apply_reason` docstring's earlier "divergent … proceed" line is
  superseded.
- **Pre-S1 indexes**: no stamp → first apply stamps, warns only; guard
  arms from the second apply.
- Convo/graph lanes are out of scope: `rebuild_convo_search` already
  refuses cross-generation swaps via its own wal/fingerprint checks;
  `graph` reads a generation-keyed cache, doesn't write the notes
  surface.

## 5. Summary

| behavior | today | after S2 |
|---|---|---|
| fresh from current HEAD (dirty ok) | applies | applies (unchanged) |
| fresh from a behind worktree | regresses surface + bump | **refused**, named reason |
| fresh from a divergent worktree (own commits) | regresses surface + bump | **refused**, named reason (F8) |
| same, `--allow-stale` | — | applies, stamped as deliberate |
| behind-wt patch validation | tramples the shared surface | `--sandbox` preview, shared store untouched |
| stale-snapshot gate failure | "ghost" MISMATCH | names ts/caller/worktree |
| bumps during active devel | 2 per round-trip, 100s/day | 0 from trample; only real content changes |

**Revisit trigger:** any recurrence of an unexplained generation MISMATCH
after S2 lands, or the first operator-hit refusal that turns out to be
wrong (the refusal predicate gets tightened, not bypassed).

## 6. Review findings (delegation review, 2026-10-12)

Reviewed against the implementation as it stands in the stack:
`bug_fixe` (`d50c87366`, bottom — the incremental-default flip plus this
proposal) and `db_stale` (`c8d5a28ce`, top — S1–S5). Advisory; the
operator disposes. Everything below is reproduced at those commits, not
inferred. Findings live here, in the tree — not in a scratch sidecar.

| id | finding | owner | impact |
|---|---|---|---|
| F1 | `ty check helpers app.py` red: `helpers/graph/extract_relations.py` `_extract_sections` declares `tuple[list[tuple[str, str]], …]` while the newsletter branch appends `(resolver.resolve(), body)` and `resolve()` is `str \| None` | `bug_fixe` | **`types` qa leg** |
| F2 | `ruff format --check .` red: 3 files — `helpers/maintenance/snapshot_db.py` (one hunk, the `SOURCES_TABLES` frozenset), `tests/run_gate_report.py`, `tests/run_perf_benchmarks.py` | `bug_fixe` | **pytest leg** (`tests/test_lint_gates.py::test_ruff_format_footprint_clean`) |
| F3 | S3's citation is wired to `--quick` (generation-only), so it cannot annotate the generation-matching table MISMATCH class the 2026-10-12 sources incident belonged to; §3/S3 corrected | `db_stale` | advisory |
| F4 | stamp keys dropped in the verify count but not in the export → `Parquet MISMATCH: sqlite/db_meta: 6/3` from the first stamping apply + `make snapshot`; §3/S1 amended | `db_stale` | latent, arms on first apply + snapshot |
| F5 | no test covers the guard, the stamp or the ring — S2's mandated eval gate never shipped (`rg` under `tests/` finds zero references) | `db_stale` | advisory |
| F6 | `make search-fresh` forwards only `--check`/nothing, so the AGENTS.md `--allow-stale` escape and the §3/S5 `--sandbox` preview are unreachable from the documented entry point | `db_stale` | advisory |
| F7 | advisory lint-audit only: `helpers/core/db.py:339` S110 (`try/except/pass` in `_log_bump`); `helpers/maintenance/rebuild_common.py:533` C901 (`run_rebuild_cli`, 13 > 10) — both clean below `db_stale` | `db_stale` | `lint-audit` (non-gating) |
| F8 | divergence was unguarded — **decided: tighten** (§3/S2, §4, §5) | `db_stale` | semantics change |
| F9 | `ensure_db_meta`'s comment claims the bump log is created so a restored index "carries it", but restore replays `snapshots/parquet/_schema.sqlite.sql` and never calls `ensure_db_meta`; that file is behind by `db_bump_log` (and, pre-existing, `note_search_meta`). Self-heals on the first writer | `db_stale` + a `make snapshot` | advisory |

Reproduced clean at the same commits, for the record: `ruff check .`;
`snapshot_db --quick` and `--check` (55 + 7 tables, all OK); the
blocking `tests/test_schema_drift.py` (4 passed); `git_state`'s
three-valued behaviour including the malformed-SHA guard; and all four
indexes still stamp-free — which is what keeps F4 and F5 latent rather
than live.

**Implementation state:** the F1/F2 gate failures are the only reds in
`make qa`; the rest are advisory or design. The frontmatter still reads
`proposed` while the stack carries S1–S5 — the lifecycle flip is the
operator's call.
