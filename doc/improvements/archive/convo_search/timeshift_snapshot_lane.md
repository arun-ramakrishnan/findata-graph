---
title: "Timeshift snapshot lane — close the unharvested tail, add the dsh glob, fix the live-id probe"
status: executed
filed: "2026-11-10"
executed: "2026-11-10"
completed_md: "499"
area: "helpers/maintenance/harvest_conversations.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Timeshift snapshot lane — close the unharvested tail, add the dsh glob, fix the live-id probe

**Date:** 2026-11-10 · **Status:** EXECUTED ·
**Completed:** completed.md #499 ·
**Area:** timeshift snapshot ingestion into convo_search + doctrine-pool history recovery

## 0. Traps register — read this before trusting anything below

Every number in this proposal was measured against the mounted
`/mnt/store/timeshift/snapshots/` tree on 2026-11-10. Five ways to get a
plausible-but-wrong answer, all of which were live at least once during the
survey:

| # | trap | what it silently produced | caught by |
|---|---|---|---|
| 1 | assume `memory/data/harness/` is memory | treated the harvested corpus as the doctrine layer — it is pipeline output (harvest_conversations.py:63 `CORPUS_ROOT`) | reading `helpers/maintenance/harvest_conversations.py:63` vs `rebuild_memory_search.py:75-77` |
| 2 | assume the last harvest saw the snapshots | concluded 'snapshots are fully harvested' because `opencode-snap:source-count` exists | reading the watermark VALUE (0), not its presence |
| 3 | count snapshot coverage by harness, not by session id | 'dsh snapshots are covered' — dsh has no snapshot glob at all | set-difference on session ids, per harness |
| 4 | trust the live-id probe | 3 live sessions labelled `orphan:opencode` | querying the live db's `session` vs `session_v2` tables |
| 5 | treat the timeshift mount as conversation-only | missed 117 doctrine records that exist ONLY in snapshots | diffing the three doctrine pools live vs snapshot-union |

## 1. Motivation

The timeshift mount was absent until 2026-11-10 (user mounted it mid-session,
m00223). Every harvest that ran before today therefore saw **zero**
snapshots, and the drift machinery never complained: the watermark key
`opencode-snap:source-count` was created with value `0` on 2026-11-09
22:52:30 and the growth check only fires on shrinkage. The lane is not
broken — it was never given anything to chew on, and it will not even say
so.

Three distinct defects and one surprise surface under the mount:

1. **The newest snapshot (2026-11-07_17-00-06) is unharvested** — no
   watermark exists for it, and `_opencode_source_drift` confirms it as a
   new source. Its content (3 opencode `session_v2` sessions, 32 prime jsonl
   files, 3 zcode rollouts, 60 dsh session dirs) is on disk but not in the
   corpus.
2. **DSH has no snapshot lane at all.** `_dsh_logs()` enumerates the live
   home only (harvest_conversations.py:994-997, :1055-1078). One dsh
   session present in five snapshots has never been ingested:
   `session-72bd46c5-d24b-4ac1-a600-71f6b5c5f1ef` (1900 bytes, in
   2026-09-26, 10-17, 10-24, 10-26, 10-31).
3. **The live-id probe reads the wrong table**, so three currently-live
   opencode sessions are labelled `orphan:opencode`, and 79 stale sessions
   (22 dsh + 8 prime + 49 zcode) are labelled `live` across three harnesses.

4. **The snapshots are the only surviving copy of 117 doctrine records**
   that today's consolidation run deleted or that were lost from the live
   pools months ago (82 zcode files, 16 prime entries, 19 opencode records).
   The consolidation tarballs cover only the live pools at consolidation
   time; the opencode records dropped this session are recoverable only
   from the 10-31 / 11-07 snapshots.

## 2. Evidence

### 2.1 Corpus and watermark state

| item | measured |
|---|---|
| corpus files / rows / distinct sids | 408 parquet files, 288,536 rows, 406 sids, 24 provenance lanes |
| `opencode-snap:source-count` watermark | **0**, updated 2026-11-09 22:52:30 |
| visible snapshot dbs on the mount | 8 |
| `_opencode_source_drift` output | `['new opencode-snap:2026-11-07_17-00-06', 'new opencode-snap:2026-11-07_17-00-06:v2 (2 sessions (1794032122241))']` |
| older snapshots (7 of them) | all report quiet |
| `_snap_count_drift` (now replaced) | silent — fired only when `cur_n < int(rec)` (harvest_conversations.py:1969-1976), so 0 → 8 was invisible and rotation false-fired |

### 2.2 Snapshot-vs-live session coverage, per harness

| harness | snapshot-union sids | live sids | snapshot-only | already in corpus | missing from corpus |
|---|---|---|---|---|---|
| opencode | 164 | 3 | 161 | 161 | 0 |
| prime | 44 | 34 | 12 | 12 | 0 |
| zcode | 21 | 3 | 21 | 21 | 0 |
| dsh | 82 | 79 | 22 | 21 | **1** (`session-72bd46c5-d24b-4ac1-a600-71f6b5c5f1ef`); its log carries NO searchable records, so it lands as a session with 0 rows |

### 2.3 2026-11-07 content delta (what a harvest would add)

| harness | snapshot content | corpus rows today |
|---|---|---|
| opencode | 3 `session_v2` sessions (`session_message` rows 3 / 319 / 1576) | 2 / 1 / 0 of those messages are newer than the corpus |
| prime | 32 jsonl files, 167 lines, mostly 5-line stub sessions | 0 rows |
| zcode | 3 model-io files, 162 / 19 / 25 lines | corpus has 1156 / 137 / 121 rows for the same sids |
| dsh | 60 session dirs | not covered by any glob |

### 2.4 Provenance lies

| label | sids / rows | truth |
|---|---|---|
| `orphan:opencode` | 65 sids / 71,326 rows | only **19** are truly dead; 46 exist in a snapshot; **3 are live right now** |
| `dsh:live` | 97 sids / 27,688 rows | 22 sids / 3,304 rows point at sessions that no longer exist |
| `prime:live` | 9 sids / 920 rows | 8 sids / 911 rows are stale |
| `zcode:live` | 52 sids / 16,343 rows | 49 sids / 16,038 rows are stale |

Root cause of the opencode lie, measured on the live db:

- `_oc_live_ids()` (harvest_conversations.py:2615-2629) ran
  `SELECT id FROM session` — the **legacy** table. Live db table counts:
  `session` = 0 rows, `session_v2` = 3 rows, `session_message` = 2,577,
  `event` = 66,883, `message` = 0, `part` = 0.
- The live db is FIRST in the candidate list and opens fine, so the query
  SUCCEEDS and returns an EMPTY SET (not an error). The snapshot fallback only
  engaged when the live open itself raised — which is what happened under the
  read-only-$HOME sandbox, where it answered with the 2026-07-26 image's 76
  July ids. Both outcomes are wrong for a live question: an empty set relabels
  every live session `orphan:opencode`, and a July id set answers "what existed
  then".
- The empty set is indistinguishable from "the harness purged everything", so
  `_resolve_orphans` relabelled all 3 live sessions / ~505 messages. This is
  also why `--resolve-orphans --dry-run` reported
  `would relabel 0 row(s) across 0 file(s)` over all 408 files: the relabel had
  already been applied, silently, by an earlier pass.

### 2.5 Doctrine-pool history in the snapshots

| pool | live | snapshot-distinct | gone from live |
|---|---|---|---|
| zcode `~/.zcode/cli/memories/projects/*/memory/*.md` | 357 files | 435 | **82** (e.g. scope `pdf-ocr-obsidian-4bdd382c82a9671a/`) |
| prime `~/.prime/agent/harness/harness_state.json` → `entries.memory` | 29 entries | 45 | **16** (all `*_pdf_ocr_obsidian` + `archival_procedure_completed_pending_checks_pdf_ocr_obsidian`, `ipython_bash_cell_convention`; last seen 2026-08-26) |
| opencode `.opencode/memory/*.logfmt` (excl. deletions.logfmt) | 97 records | 113 | **19** — exactly the 17 records dropped in the 2026-11-10 consolidation plus 2 older SUPERSEDED lines |

Per-snapshot opencode record counts: 07-26 0, 08-26 0, 09-26 16, 10-17 76,
10-24 104, 10-26 107, 10-31 112, 11-07 112. So 10-31 and 11-07 each carry
the pre-consolidation 112-record set.

### 2.6 BILI needs no snapshot lane (negative result, worth recording)

`~/.local/share/billion-context/sessions/index.db` exists in the 10-17 onward
snapshots with block counts 99 → 745 → 932 → 1,140 → 1,418; live has 1,576
blocks (499 active) across 132 sessions (dsh 78 / opencode 37 / proxy 17).
Union over all snapshots and live on `(session_id, block_id)` is 1,587; the
11 snapshot-only blocks are **already in the corpus** (bili part_id is
`{sid}#{bid}`). The corpus holds 1,583 bili rows in total
(`agent='bili'`, `part_type='compression-summary'`). Conclusion: the live-only
bili path (`_bili_index_path()`, harvest_conversations.py:1278) already
covers every block that exists in any snapshot — adding a bili snapshot
lane would be duplicate work.

### 2.7 Read-only-open crash on the live opencode db

`make convo-fresh --check` aborted with
`sqlite3.OperationalError: unable to open database file` from
`_opencode_source_drift` → `connect(path, read_only=True)` on the LIVE db
(harvest_conversations.py:1932-1934). Mechanism: the live `opencode.db` is
WAL-mode (header bytes 18/19 = 2) and a sqlite read-only open of a WAL db
needs to create a `-shm` sidecar; when `$HOME` is read-only that fails.
`?immutable=1` opens the same file fine.

SCOPE CORRECTION (measured 2026-11-10 after full permissions were granted):
with a writable `$HOME` the same check runs to completion (exit 2 = STALE,
never an abort), so this is not a defect on the normal gate path. It is a
robustness hole for any read-only `$HOME` — a sandboxed agent run, a
read-only home mount, a CI container — and the gate must not depend on write
access to the harness home. The fix is still worth having; the failure mode
is narrower than first written.

## 3. Design

### S1 — snapshot-image registry (as landed)

A scalar source COUNTER cannot answer "is any visible image unharvested?".
It shrinks when images rotate away and grows when new ones land, so a
`cur_n < rec` test is inert against growth and false-fires against rotation;
measured 2026-11-10 the legacy `opencode-snap:source-count` read `0` (stamped
by a harvest that ran with the mount gone), which permanently disabled the
growth branch it was supposed to power. The old `_snap_count_drift` is
therefore REPLACED by a stamp REGISTRY:

- `_SNAP_SEEN_KEY = "snap-images:seen"` holds a JSON list of image stamps,
  unioned (never overwritten) at every mount-aware harvest, so an image that
  later rotates away stays recorded and is never re-announced.
- `_snap_lane_drift` answers exactly the useful question — set difference
  between the stamps on the mount now and the stamps recorded as harvested —
  emitting one `new snapshot <stamp>` line per unseen image.
- ABSENT MOUNT IS NOT DRIFT. `/mnt/store/timeshift/snapshots` is a backup
  mount and is legitimately gone for long stretches; it yields no drift lines
  and `_coverage` reports `snapshots: mount absent (…) — snapshot lanes idle`.
- `_SNAP_COUNT_KEY_LEGACY` (`opencode-snap:source-count`) is retained only as
  the "a harvest has run at least once" marker, so a pre-registry corpus gets
  one `new snapshots N visible, none recorded as harvested` line instead of
  announcing every image individually.

### S2 — dsh snapshot lane (as landed)

`_dsh_logs()` now enumerates `*/localhost/home/arun/.dsh/sessions/*/*/`
under the mount before the live `~/.dsh/sessions/*/*/`, tagging snapshot rows
`dsh-snap:<stamp>` and live rows `dsh:live`. `_dsh_pick_log` keeps the
one-log-per-session-dir rule (`session.v4.jsonl.zstd`, else the
`session.jsonl.zstd` fallback): the two generations overlap and reading both
would double-ingest under different seqs. `_dsh_index_lag` skips
`dsh:live`-only logs, because snapshot sessions were never in the live derived
index — including them reported every snapshot session as `dsh-index:missing`.

The glob is deliberately session-file-shaped: a recursive walk of a snapshot
root is not an option (one image holds ~839k files, mostly `~/.dsh` profiles
and node_modules).

MEASURED OUTCOME, correcting the acceptance criterion: the mount carries 95
snapshot session dirs across 6 images (61 distinct sids) versus 79 live sids,
and only ONE sid is snapshot-only — `session-72bd46c5-d24b-4ac1-a600-
71f6b5c5f1ef`. Its logs yield ZERO searchable rows: it holds only
permission/preset, sandbox/mode, approval/policy and agent/inbox/spliced
records, no text/reasoning/tool rows. The session is therefore retained and
queryable as a session, but "non-zero rows" is unachievable by construction.

### S3 — live-id probe reads the modern tables (as landed)

`_oc_live_ids()` now reads ONLY the live db
(`~/.local/share/opencode/opencode.db`), unioning three queries —
`SELECT id FROM session_v2`, `SELECT id FROM session`, and
`SELECT DISTINCT session_id FROM session_message` — each in its own guard so a
pre-v2 schema contributes nothing instead of raising. It FAILS CLOSED: an
empty union returns `None` (not an empty set), which makes `_resolve_orphans`
skip the harness rather than relabel it wholesale — an empty live set is
indistinguishable from "the harness purged everything". The snapshot fallback
is gone entirely: a snapshot id set answers what existed then.

The dsh / prime / zcode live labels are left alone — `_live_session_ids` still
returns `None` for them (they are file-enumerated, with no manifest to probe),
so the relabel blast radius does not widen. Correcting their stale-live labels
needs a per-harness manifest, which is a separate slice.

### S4 — read-only open of a WAL database (as landed)

New `_probe_connect(path)` sits in front of both drift branches. It tries
`connect(path, read_only=True)`; if that raises, or returns a connection that
cannot run `SELECT 1`, it falls back to
`sqlite3.connect(f"{path.resolve().as_uri()}?immutable=1", uri=True)`.
`?immutable=1` skips locking and all sidecar work, which is right for a
watermark probe and WRONG for the harvest path (which must see the newest WAL
content) — that is why the fallback lives on the probe only.

### S5 — doctrine-history lane (memory_search, not convo_search) — as landed

The snapshot-only doctrine does NOT belong in the conversation corpus. It is
indexed into the memory sidecar instead, as provenance-tagged kinds
`zcode-snap` / `prime-snap` / `opencode-snap` alongside the live
`zcode` / `prime` / `opencode`, so a query can ask "was this ever true?".

- Dedupe is content-hash based and is the whole design: a record byte-identical
  to the live pool produces NO history row; the same content seen in N images
  produces ONE row whose purpose reads `seen <oldest> … <newest> in N image(s)`;
  two genuinely different content versions of one name BOTH survive, told apart
  by `source_path`.
- The dedupe key is `f"{kind}|{name}|{content_hash}"`, so any per-image value
  baked into `name` silently disables cross-image merging (measured: a stamp in
  the name turned 17 opencode records into 51 rows).
- The oldest stamp seen is kept, so the row answers how far back it goes.
- An ABSENT mount yields no units and no drift, and `memory_index_stale`
  filters `snap::` meta keys out of the removal set when the mount is gone —
  unmounting a backup mount must never mark the index stale.
- The 11-07 image's zcode scope dirs are partly SYMLINKS into the live pool, so
  the zcode extractor requires `str(mem.resolve()).startswith(str(SNAP_ROOT))`.
- `memory_query.py` gained `--history exclude|include|only` (default `exclude`,
  i.e. the ordinary search is unchanged) and the snap kinds in `--kind`.

### S6 — zcode per-snapshot label granularity (as landed)

`_zcode_src_for(files)` replaces the coarse `zcode:snap` label:
`zcode:live` when any of a session's files lives outside the mount, otherwise
`zcode-snap:<newest stamp>` (falling back to `zcode-snap:unknown`). Measured
after the change: 3 sessions per image for 6 images plus 3 live sessions,
versus a single undifferentiated `zcode:snap` pool before.

The first landing shipped a composition bug, found by exercising the lane
with real queries rather than a count: `_zcode_req_rows` / `_zcode_resp_rows`
composed `f"zcode:{src_tag}"` around a tag that S6 had just promoted to a FULL
label, so every snapshot row landed in the corpus as
`zcode:zcode-snap:<stamp>` (1,241 rows / 3 images). That label neither matches
`--source zcode-snap:` nor passes `_is_snapshot_src` (which tests
`startswith("zcode-snap")`), so the rows were silently misclassified as
non-snapshot — `convo_query --source 'zcode-snap:'` returned them with the
double prefix while a `star` filter would have called them live. Fix: the row
builders now take the label verbatim, pinned by
`test_zcode_rows_carry_the_label_verbatim` (which asserts both that rows carry
the label unchanged AND that the double-prefixed form fails
`_is_snapshot_src`). Re-harvested with `--force-lane zcode` (part_ids are
stable, so it merged in place) + `--refresh-index-provenance`.

## 4. Acceptance criteria

- `make convo-fresh --check` completes without error under a read-only
  `$HOME` (S4), and names every visible snapshot image rather than a count.
- An ABSENT `/mnt/store/timeshift/snapshots` produces no drift lines and no
  memory-index staleness; `_coverage` reports the lane as idle.
- After `APPLY=1`, the corpus contains `opencode-snap:2026-11-07_17-00-06`,
  `prime-snap:2026-11-07_17-00-06`, per-image `zcode-snap:<stamp>` labels and
  a `dsh-snap:<stamp>` lane.
- The single snapshot-only dsh session `session-72bd46c5-…` is ingested under
  `dsh-snap:<stamp>`. NOTE: it carries no searchable records, so the original
  "non-zero rows" formulation is unachievable — see S2.
- `memory_query.py --history only` returns the snapshot-only doctrine, and the
  default (live) search is unchanged.
- No corpus row is labelled `orphan:opencode` for a session that the live
  `session_v2` / `session` / `session_message` tables list (3 sessions /
  ~505 messages were affected); the probe fails closed and never relabels from
  an empty or snapshot-derived id set.
- The stale `live` labels for dsh / prime / zcode remain OUT OF SCOPE here
  (`_live_session_ids` returns `None` for them) — narrowing, not widening, the
  relabel blast radius.
- The post-apply `--refresh-index-provenance` repair joins on the stable
  `(file_path, part_id)` key (both `_backfill_provenance` and
  `_backfill_models` in rebuild_convo_search.py): the S2 seq-merge rewrites
  parquets in a different row order than the index was built from, and the
  old positional `(file_path, row_no)` join swapped labels between rows
  (measured: 29 dsh files with paired opposite mismatches after the first
  refresh; 39 of 491 rows shifted in session-0ac25dca). Pinned by
  `test_backfill_is_row_order_independent` and
  `test_backfill_models_is_row_order_independent`.
- **Eval gate:** run `helpers/misc/ontology_eval_gate.py` over the frozen
  question set between the dry-run and the canonical apply
  (ontology_governance S2) — the source-label change is query-visible
  semantics and must pass the frozen-set gate before apply.
- `make search-fresh` completes cleanly after the doc edits;
  `make qa` is green (ruff, md-lint, types, pytest, integrity, snapshot).

## 5. Risks

| risk | mitigation |
|---|---|
| relabelling shifts provenance for ~71k rows and changes `convo_query` results | do the relabel in one reviewed pass, keep `deletions.logfmt`-style audit trail, and gate with the frozen question set |
| snapshot scan cost (839k files in the 11-07 image) | harvest per-session-file globs only; never recursive walk of a snapshot root |
| immutable/WAL open hides recent writes in a WAL db | only use `immutable=1` for the read-only drift probe; the apply path keeps the normal reader |
| the live-id probe becomes a second source of truth that drifts | derive it from the same db the harvester reads, and fail closed (report unknown) rather than guess |
| snapshot doctrine re-entering the live pool as if current | S5 indexes snapshots as provenance-tagged variants; promotion to live is a separate, explicit act |
| duplicate content across snapshots and live | the corpus already dedupes by message identity; verify row counts do not double after the 11-07 harvest |

## 6. Non-goals

- Not building a general-purpose file-history browser over the timeshift
  images; the scope is harness memory and harness conversation.
- Not restoring the 82 zcode / 16 prime doctrine records into the live pools
  automatically — recovery is a human decision; the lane makes them
  discoverable.
- Not adding a bili snapshot lane (see §2.6 — already covered).
- Not changing the conversation corpus schema to carry doctrine records
  (see S5).

## Appendix A — measurement log (2026-11-10)

| measurement | command / probe | result |
|---|---|---|
| snapshot inventory | `ls /mnt/store/timeshift/snapshots` | 8 weekly rsync images, 2026-07-26 → 2026-11-07 |
| snapshot metadata | `info.json` | app-version 25.12.4, file_count 839054, tags weekly, type rsync |
| snapshot corpus footprint | `stat` on repo dbs under the 11-07 image | convo_search.duckdb 1.92 GB, fts 655 MB, embed_store 466 MB, research.db 405 MB, agent_traces 217 MB |
| hardlink check | `stat -c %i,%h` on snapshot opencode.db | distinct inodes, links=1 — snapshots are full copies, not links |
| watermark | `harvest_meta` in memory/convo_search.duckdb | `opencode-snap:source-count` = 0 @ 2026-11-09 22:52:30 |
| drift | `_opencode_source_drift` | 1 new source + 1 new v2 sub-lane, both 2026-11-07 |
| corpus shape | parquet scan | 408 files, 288,536 rows, 406 sids, 24 source lanes |
| live opencode schema | per-table counts | session 0, session_v2 3, session_message 2,577, event 66,883 |
| bili blocks | per-snapshot index.db block counts | 99 / 745 / 932 / 1,140 / 1,418; live 1,576; union 1,587; 11 snapshot-only all already in corpus |
| doctrine diff | live vs snapshot-union per pool | zcode 82 gone, prime 16 gone, opencode 19 gone |
| crash | `make convo-fresh --check` | `sqlite3.OperationalError: unable to open database file` at harvest_conversations.py:1932-1934 |
| backfill swap | `--refresh-index-provenance` + `--check` | 29 dsh files with PAIRED opposite mismatches; session-0ac25dca: 39 of 491 index rows held a `row_no` that no longer matched their parquet position after the S2 seq-merge; positional `(file_path, row_no)` backfill swapped 2 rows' labels — fixed by joining on `(file_path, part_id)` (rebuild_convo_search.py `_backfill_provenance` / `_backfill_models`), regression-pinned in tests/test_convo_provenance.py |
