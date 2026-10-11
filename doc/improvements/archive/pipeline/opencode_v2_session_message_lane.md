---
title: "Harvest the v2 session_message lane — restore opencode ingest after the v2 schema migration"
status: executed
filed: "2026-10-11"
executed: "2026-10-11"
completed_md: "382b"
area: "helpers/maintenance/harvest_conversations.py (opencode lane), helpers/analytics/model_analytics.py + agent_traces.py (opencode loaders), memory/data/harness/opencode/conversations (corpus parquet zstd)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Harvest the v2 session_message lane — restore opencode ingest after the v2 schema migration

**Date:** 2026-10-11 · **Status:** PROPOSED ·
**Area:** `helpers/maintenance/harvest_conversations.py` (opencode lane),
`helpers/analytics/{model_analytics,agent_traces}.py` (opencode loaders),
corpus under `memory/data/harness/opencode/conversations/`.

## 1. Motivation

The opencode v2.0.26 upgrade (measured on this box, 2026-10-11) moved
sessions into a **second, parallel schema**. The v2 migration relocates a
session's history out of `message`/`part` and into
`session_v2`/`session_message`, with the old rows dropped. Every opencode
read path in this repo still reads `message`+`part`.

The result is silent data loss with a green gate: `make convo-fresh`
reports the opencode lane healthy while ingesting almost nothing, and
the watermark that would prove otherwise is derived from the same table
that went empty. This is the "reports success while having done less than
it claims" shape the 2026-10-10 `review_pass_findings` batch (#380)
already named as the repo's recurring failure mode.

Trigger: operator request to verify the v2 upgrade against
`analytics-fresh` and `convo-fresh`, 2026-10-11.

## 2. Evidence (measured 2026-10-11, this box)

### 2.1 The two schemas

| store | legacy sessions (28) | v2-migrated sessions (15) |
|---|---|---|
| `session` rows | 28 | 0 (dropped) |
| `session_v2` rows | 0 | 15 |
| `message` rows | 3,538 | **2** |
| `part` rows | 51,663 | **5** |
| `session_message` rows | 0 | **10,162** |
| `event` rows | — | 106,948 (7 of 15 sessions) |

14 of 15 migrated sessions have **zero** rows in `message` and `part`.
Their content sits in `session_message.data` — assistant rows carry a
`content[]` block array (5,573 reasoning, 3,631 text, 9,967 tool), user
rows carry `text`; 10,058 searchable units in total.

### 2.2 New activity lands only in v2

| window | `part` rows | `session_message` rows |
|---|---|---|
| last 24 h | 2,355 (28 legacy sessions) | 1,028 |
| last 6 h | **1** | **295** (3 v2 sessions) |

Newest rows: `part` 2026-10-11 08:22, `session_message` 2026-10-11 14:57.
The harvester's watermark, `MAX(time_updated) FROM part`, is **frozen at
08:22 — 6.6 h behind wall clock** and only advances while legacy sessions
still write.

### 2.3 The drift check cannot see it

`_drift()` (`harvest_conversations.py:759`) computes the opencode
watermark as `MAX(time_updated) FROM part` and compares it to the stored
watermark — the same source on both sides, so the comparison is
self-consistent by construction. With the fs unmounted and mounted,
`harvest_conversations.py --check` emitted **no opencode drift at all**;
the only hit was one zcode session.

Current backlog: **181 searchable units across 3 v2 sessions** are newer
than anything in the corpus, growing ~15 rows/h. One session
(`ses_ed5ea2ac…`) has 70 units and **zero** corpus rows.

### 2.4 The analytics loaders are blind the same way

| reader | line | reads | v2 sessions |
|---|---|---|---|
| `_harvest_opencode_db` | `harvest_conversations.py:299` | `message`+`part` | 0 rows |
| `_oc_messages` | `model_analytics.py:813` | `message` WHERE role=assistant | 0 rows |
| opencode loader | `agent_traces.py:803` | `message` + `part` | 0 rows |

255 assistant messages with usage totals are invisible to
`model_usage` for 2026-10-11 alone. `analytics_fresh` does not catch it
either: its opencode frontier reads `session`+`session_v2`
(`analytics_fresh.py:88`), which v2 keeps current, and `check()` only
declares STALE when `hday > sday and hday < today` — so the verdict stays
`fresh` until the day rolls over, then goes STALE for the wrong reason.

### 2.5 The `event` table — the part types `session_message` cannot carry

CORRECTED 2026-10-11 after an operator session-restore run (script at
`/mnt/data/tmp/opencode/restore_sessions.py`, exports in
`.../restore/`) rebuilt seven v2 sessions end-to-end from the `event`
table alone. Two claims in the first draft of this section were wrong;
both corrections are load-bearing.

**(a) The event lane DOES have a usable watermark.** The `created`
column is `0` on all 173,831 rows — but the epoch-ms timestamp lives in
the payload: `json_extract(data,'$.time')` is non-null on **114,159 of
114,159** `message.part.updated.1` rows. `seq` is also monotonic by
rowid within each of the 36 aggregates (gaps present, from event
retention). Rowid is a monotonic global fallback. The first draft's
"no watermark possible" was wrong.

**(b) `session_message` alone would silently regress the corpus.**
Exhaustive block-type census — `session_message.content[]` carries
**only** `tool` (10,002), `reasoning` (6,914), `text` (4,342):

| part type | in `event` | in `session_message` | corpus rows, v2 sessions | corpus rows, legacy |
|---|---|---|---|---|
| tool | 62,125 | yes | 10,616 | 21,141 |
| reasoning | 15,766 | yes | 7,228 | 14,745 |
| text | 12,306 | yes | 5,419 | 12,071 |
| **step-start** | 11,189 | **no** | 9,699 | 18,782 |
| **step-finish** | 11,056 | **no** | 9,605 | 18,657 |
| **patch** | 1,539 | **no** | 1,477 | 2,524 |
| **compaction** | 165 | **no** | 84 | 95 |
| **file** | 13 | **no** | 18 | 76 |

20,883 of the v2 sessions' 44,146 corpus rows (47%) are part types
`session_message` cannot reproduce. Today they got in via the
pre-migration v1 harvest; for any session created after the cutover an
S1-only lane yields zero of them, so the corpus `part_type` facet goes
silently skewed by session vintage — patch payloads (1,539 parts,
0.7 MB of file lists) and compaction markers included.

opencode's own v2 projection drops them: `restore_sessions.py:135`
returns `None` for `step-start/finish, patch, compaction, file`. Once a
session round-trips through v2 export/import that history is gone from
the db, and the `event` table is the last copy.

`step-finish` also carries `cost`, `tokens`, `reason` and `snapshot` —
usage data S3 can use.

### 2.6 Snapshot copies lane — measured, and it matters

| source | count | harvest result |
|---|---|---|
| `opencode-snap:*` dbs | 7 | 68,167 corpus rows (52% of the opencode corpus) |
| `opencode:traces` (snapshot `agent_traces.duckdb`) | 4 | **0 rows** |

Corpus sessions absent from the live db: **104 of 146 (69,362 rows)**.
Across the 7 snapshots **99 of those 104 are recoverable**; the
remaining 5 are held by no snapshot and no live db — and are already in
the corpus. The corpus is safe *today*; the lane is what keeps it that
way.

**Unmounting the snapshot fs is invisible.** With `/mnt/store/timeshift`
absent, `_opencode_sources()` returned 1 source (`opencode:live`), the
glob simply yielded nothing, and `_drift()` emitted no snapshot lines —
`convo-fresh` reported the opencode lane healthy. A missing source root
is indistinguishable from "no new snapshots".

**The `opencode:traces` pre-redaction lane is dead by construction.**
All four snapshot copies and the live `agent_traces.duckdb` return 0
rows: `fact_event` rows for `message.part.updated.1` carry a `context`
of `{agent, id, model, role, sessionID}` with `part: null` on all
105,336 / 97,646 rows. The redaction the lane existed to beat now
happens upstream at capture time, so the lane can never fire. Its four
watermarks are all `'0'`.

Ruled out by measurement, do not re-audit: (a) `message.removed.1`
tombstones — 1 row, recorded only; (b) reading `event` *instead of*
`session_message` — the two are complementary, see §2.5(b): events
carry the five part types the message store drops, `session_message`
carries the per-message envelope (role/agent/model/tokens/cost) that
`event` splits across `message.updated.1` and `step-finish`.

## 3. Design

**Chosen:** add a `session_message` lane **and** an `event` lane beside
the existing `message`/`part` lane, and make every opencode watermark a
union over all three. The lanes are complementary by measurement (§2.5):
`session_message` supplies the per-message envelope and the
text/reasoning/tool content; `event` supplies the five part types the
message store drops. The legacy lane stays — it is the only source for
28 live legacy sessions and every pre-cutover snapshot. The three are
disjoint by session set today (28 legacy / 15 v2), and overlap is
handled by the existing `Store.register` part-id registry.

Alternatives considered:

- **Read `event` alone.** Rejected: no per-message envelope (role /
  agent / model / tokens / cost are split across `message.updated.1`
  and `step-finish`), parts arrive 1–8× each (50,160 ids over 114,159
  rows) so last-write-wins dedupe is mandatory, and the restore run
  shows the folding code needed to reassemble messages is non-trivial —
  it is worth writing once, in the right place, not twice.
- **Read `session_message` alone.** Rejected — this was the first
  draft's design, and §2.5(b) falsifies it: it drops 47% of the v2
  sessions' corpus rows.
- **Wait for opencode to migrate everything and drop the v1 lane.**
  Rejected: the v1 lane is the only source for 28 live legacy sessions
  and for every snapshot older than the cutover.
- **Read `session`+`session_v2` for the frontier only (status quo).**
  Rejected: the frontier is already v2-aware and still reports `fresh` —
  knowing the harness is current is not the same as knowing the store is.

Slices, each independently landable:

- **S1 — `session_message` harvester lane.** New
  `_harvest_opencode_v2()`: one corpus row per user `text`, and one per
  assistant `content[]` block (`text` → part_type `text`, `reasoning` →
  `reasoning`, `tool` → `tool` with `state.content[].text` as text and
  `{"status", "tool", "input"}` in meta — `input` truncated as the legacy
  lane does; `title` has no v2 equivalent, the projection drops it).
  part_id is the message id for message-grain rows and `{mid}#{idx}` per
  content block (deterministic — content order is append-only, so
  re-harvests merge newest-wins). Role/agent/model from the row envelope;
  user/compaction/synthetic rows take the session envelope from
  `session_v2` (`model` is a nested `{providerID, id, variant}` — the
  shared normalizer now reads `modelId | modelID | id`, fixing the bare
  zcode normalizer's silent `""` on v2 shapes). **Compaction rows are
  emitted** (`text` = `summary` — the summary lives only there; event
  parts carry just `{auto, tail_start_id}`), **synthetic rows are emitted
  as `text`** (legacy parity — the v1 lane emits synthetic text parts).
  Skip `type` ∈ {idle, model-switched} (outcome/routing markers, no
  searchable text). Watermark `MAX(time_updated) FROM session_message`
  under its own key, launching from per-session corpus seeds (see S3).
- **S2 — `event` lane for the dropped part types.** New
  `_harvest_opencode_events()` reading `message.part.updated.1` (folded
  last-write-wins per part id, `ORDER BY rowid`) with the envelope from
  `session_message` + `message`. Emits exactly the five dropped types —
  text/reasoning/tool stay with S1 so the lanes never double-ingest one
  block: `step-start` (legacy else-branch parity), `step-finish` (text
  from `reason`, meta carries `cost`/`tokens`/`snapshot` — usage data S3
  can use), `patch` (`text = ""`, meta `{files, hash}` — legacy parity,
  overriding this draft's first `text = files` idea), `compaction` and
  `file` rows. Part ids are the stable v1 space, so re-emission merges
  with legacy-lane rows instead of duplicating. Watermark
  `MAX(json_extract(data,'$.time'))` — not the dead `created` column.
  **Not optional:** without it S1 regresses the corpus per §2.5(b).
- **S3 — per-session seeds + union drift.** Each lane keeps its own
  watermark key (`<src-key>:v2`, `<src-key>:evt`); the legacy key is
  untouched. Launching is by per-session corpus seed, not by global
  maximum: seed = the session's corpus parquet max-ts where harvested,
  else full ingest (nothing harvested → nothing to duplicate). This
  closes the residual window between the last legacy harvest and the
  cutover by construction, and makes first runs on already-harvested
  snapshots duplicate-free (S1's synthesized ids could never dedupe
  against legacy `prt_` ids). `_drift()` mirrors the rule in check mode
  (a lane fires when a session holds rows newer than both its seed and
  the stored key) and emits distinct lines per lane, so a frozen lane
  cannot mask a live one. A deleted-then-regrown parquet self-heals
  (seeds recompute, stable ids merge newest-wins).
- **S4 — analytics loaders read `session_message`.** `_oc_messages`
  gains a v2 UNION leg (column `type`, `model.providerID`/`model.id`;
  shared mid space keeps the `seen` dedupe exact). In `agent_traces.py`
  the sessions grain (already `session`+`session_v2` unioned) and the
  events grain (already reading `event`) are explicitly untouched;
  added: assistant-message and tool-call passes over `session_message`
  (tool ids = `callID`, stable across snapshots; output text from
  `state.content[]`; `mode` is NULL in v2), plus event-derived edits and
  compaction counts restricted to v2 sessions (legacy sessions' edits
  come from the part loop — re-deriving them would double-count).
  Backfill rides the loaders' full-reload pattern; run `--full` once
  (migrated history spans 2026-09-11..10-08, outside the default 2-day
  window). Unblocks the analytics half: without it `analytics-fresh`
  goes STALE on day-rollover for a reason this proposal is fixing.
- **S5 — snapshot-lane health assertion.** `_drift()` fails when
  `SNAP_ROOT` is absent or yields fewer sources than the last successful
  harvest recorded, naming the path. Closes the silent-dead-lane finding
  in §2.6.
- **S6 — retire the dead `opencode:traces` lane.** Delete
  `_harvest_traces_duckdb` and its snapshot glob. Records that the
  pre-redaction premise no longer holds — the redaction moved upstream
  to capture time, so the lane can never fire.

## 4. Acceptance criteria & shakedown

1. `make convo-fresh` (APPLY) ingests v2 sessions: the corpus gains rows
   for `ses_ed5ea2ac…` (landed 680 rows from 0 — text/reasoning/tool
   plus a compaction row) and the §2.3 backlog closes; `--check`
   afterwards names only genuinely-new live rows, per-lane.
2. `harvest_conversations.py --check` reports FRESH on a quiet box
   **and** the opencode watermark advances past `session_message`'s
   newest `time_updated`; a regression fixture with a v1-frozen /
   v2-advancing db must produce a drift line naming the v2 lane.
3. `convo_query.py "session_message"` returns hits from v2 sessions'
   parquet with non-empty `role` and plain-string non-empty `model`
   (no `{` values, no blanks — the zcode S1 facet invariant, extended
   to opencode by the shared normalizer + one-shot migration).
4. Part-type parity (§2.5b): the corpus `part_type` histogram for a v2
   session harvested post-slice is not missing `patch`, `compaction`,
   `step-start` or `step-finish` relative to a legacy session of the
   same shape. A fixture with a `session_message` row plus its matching
   `message.part.updated.1` events must yield rows of both kinds.
5. `model_analytics.py load opencode --full` + `agent_traces.py load
   opencode --full` put 2026-10-11 opencode usage in
   `model_usage.fact_usage` and `agent_traces.fact_model_request`
   (`--full`: migrated history spans 2026-09-11..10-08, outside the
   default 2-day window; Zhipu rows drop where the zai API covers —
   assert on Zen/OpenRouter rows); `make analytics-fresh` (check)
   exits 0.
6. Eval gate — corpus content is query-visible: the ontology question
   set covers entity/graph ontology (unaffected — this arc writes no
   graph store), so the operable check is a `convo_query` count/coverage
   diff parent-vs-candidate for the §2.3 sessions (restored sessions
   surface at rank 1–2; `ses_ed5ea2ac` goes 0 → 680 rows).
7. With the snapshot fs unmounted (or renamed), `convo-fresh --check`
   exits 1 naming `/mnt/store/timeshift/snapshots` — the §2.6 failure is
   no longer silent. (Covered hermetically by a synthetic missing-root
   test; live unmount not required.)
8. `make qa` green (ruff, md-lint, types, pytest, integrity, snapshot);
   fixes validated by targeted tests only.

**Shakedown.** No operator hands needed: the snapshot fs stays mounted
per operator 2026-10-11 (AC7's absent-fs case is covered hermetically).
`make convo-fresh APPLY=1` rewrites the opencode corpus parquet — the
embed cache is keyed by part id, so S1 mints new ids only for v2-lane
content; expect `make convo-fresh` to embed those rows and budget
wall-clock for it.

## 5. Appendix — raw measurements

All read-only, `opencode v2.0.26`, `~/.local/share/opencode/opencode.db`
(1.93 GB), corpus `memory/data/harness/opencode/conversations` (146
sessions / 132,237 rows), `memory/convo_search.duckdb`
(`harvest_meta` watermarks).

```text
sessions            legacy=28  v2-migrated=15
message/part rows   legacy=3,538 / 51,663   v2=2 / 5
session_message     10,162 rows across 15 sessions
event               173,831 rows; created=0 on ALL rows
                    families: message.part.updated.1 114,159 /
                    message.updated.1 46,581 / session.updated.1 13,056 /
                    session.created.1 34 / message.removed.1 1
newest part         2026-10-11 08:22      newest session_message 2026-10-11 14:57
watermark lag       6.6 h (MAX(time_updated) FROM part)
_drift()            19 items with fs mounted (1 opencode-snap new +
                    17 prime-snap changed + 1 zcode); 1 item unmounted
                    (zcode only) — no opencode line either way
orphan sessions     104 of 146 corpus sessions absent from the live db
                    69,362 corpus rows; 99 recoverable from snapshots,
                    5 held nowhere (all already in the corpus)
opencode:traces     4 snapshot copies -> 0 rows each; fact_event
                    message.part.updated.1 context.part is null on
                    105,336 / 97,646 rows
v2 assistant        content blocks: tool 10,002 / reasoning 6,914 / text 4,342
                    (NO patch / compaction / step-start / step-finish / file)
event part census   tool 62,125 / reasoning 15,766 / text 12,306 /
                    step-start 11,189 / step-finish 11,056 / patch 1,539 /
                    compaction 165 / file 13
event watermark     json_extract(data,'$.time') non-null on 114,159/114,159
                    part rows; per-session seq monotonic by rowid (36/36)
                    => created=0 is a dead column, not a missing timestamp
restore artifacts   /mnt/data/tmp/opencode/restore_sessions.py (read-only vs
                    opencode.db) + restore/*.json — 7 sessions rebuilt
                    end-to-end from the event table; this run is what
                    falsified the "no watermark" and "event is optional"
                    claims above
```

Re-captured 2026-10-11 ~16:00 UTC, post-implementation (all read-only
except the APPLY loads the ACs required):

```text
session/message/part   29 / 4,920 / 18,736  (legacy lane frozen: part newest
                       still 2026-10-11 08:22)
session_v2             15 rows (8 native + 7 restored; an 8th restored
                       session, ses_ed709, was deleted on the live TUI
                       mid-arc by operator hand — events purged by the
                       delete cascade, import JSON retained, 8/9 pins
                       resolve)
session_message        10,490 rows (was 10,162 at proposal time; +328 live
                       operator activity during the arc)
corpus opencode        150,313 rows (was 132,237); ses_ed5ea2ac 0 -> 680;
                       migrated sessions carry all 8 part_types
model facet            3,166 dict-string values rewritten to plain ids
                       (--migrate-opencode-models); zero brace/blank models
                       on all new v2 rows
model_usage            opencode 2026-10-11 rows present (muse-spark 391
                       reqs, space-bunny 92, mimo 28)
agent_traces           restored sessions in fact_model_request
                       (f0436937:1404 = message count; f2c3aecb:1036);
                       2,389 tool calls; 2,674 file edits; ses_ee4e1c35
                       correctly absent (session-level zai-coding-plan ->
                       documented zai exclusion, same as v1)
analytics-fresh        exit 0, opencode fresh on both stores
```
