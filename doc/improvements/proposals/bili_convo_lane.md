---
title: "Index BILI compression summaries in convo_search (bili lane)"
status: proposed
filed: "2026-10-16"
executed: null
completed_md: null
area: "convo_search harvest + rebuild"
---

# Index BILI compression summaries in convo_search (bili lane)

**Date:** 2026-10-16 · **Status:** PROPOSED (S1–S3 implemented; amended 2026-10-17, see §7) ·
**Area:** helpers/maintenance/harvest_conversations.py, helpers/maintenance/rebuild_convo_search.py, memory/data/harness/{opencode,dsh}/

## 0. Traps register — read this before trusting anything below

21 defects from the 2026-10-17 amendments, **none of which raised an
error**. Every one reported a plausible number. Grouped by what they have in
common, because the shared lesson matters more than any single entry: *a
silent wrong answer is always cheaper to find than a missing one, and always
more expensive than a loud one.*

| # | trap | what it silently produced | § | caught by |
|---|---|---|---|---|
| **Log parsing — the nudge line** | | | | |
| 1 | filter per session on `[sess=…]`, not `[<sid>] nudge` | max growth 225,890 instead of 232,772 | 8.11 | `test_family_is_parsed_once_not_per_session` |
| 2 | substring match on `"nudge"` | picks up `[headroom] … nudge/preflight` lines | 8.11 | `test_headroom_line_is_not_a_nudge` |
| 3 | index `m.groups()` from 1 | every field shifted one; plausible nonsense Counters | 8.11 | — (script-level; named groups now used throughout) |
| 4 | `INJECT[^\s:]*` for the kind token | matches **nothing** — the token is two words (`INJECT T1`, `INJECT-ESC T?`) | 8.11, 12 | `test_pins_the_trap` |
| 5 | look for `usage=` inside `reason` | `reason` holds only the compressible clause | 8.11 | — |
| 6 | `window` captured as `\d+` | drops any line with a fractional effective window (`83558.25`) | 12 | `test_fractional_window_parses` |
| 7 | classify nudge actions by their `reason` text | an `INJECT-ESC` line's reason is byte-identical to an idle line's, so 22 escalations were filed as `below_compressible_threshold` | 8.7 | `test_esc_is_counted_as_an_escalation_not_a_block` |
| **Performance fixes that changed shape** | | | | |
| 8 | re-parse the whole log family **per session** | 4.1 s of 7.7 s — introduced by the rotation merge that fixed blank models | 9.1 | `test_family_is_parsed_once_not_per_session` |
| 9 | build `intervals_seen` with `Counter(n.interval …)` | keys become `int`, so `--json` consumers see a different shape, no error | 9.1 | `test_interval_keys_stay_strings` |
| 10 | drop `reasoning_pair_violations` when partitioning | it is a bare `int`, not a list — **13 violations on one session silently became 0** | 9.1 | `test_reasoning_pair_violations_are_partitioned` |
| 11 | walk `/proc` once per session | 71 walks, 22,072 `read_text` calls | 9.1 | `test_proc_walk_happens_once` |
| 12 | `sid in log_path.read_text()` per session | a whole-file read just to test substring containment | 9.1 | (superseded by the family index) |
| **Signal design** | | | | |
| 13 | keep a session-killer in a field no column reads | 3 sessions died; the evidence was present, correct, and shown only by `--detail` | 10 | `test_viol_is_a_table_column` |
| **Retention** | | | | |
| 14 | `stale_deleted` declared, never assigned | the vestige of an unimplemented deletion path — the reason nothing noticed 29 deleted sessions | 11.1, 11.4 | `TestDeletedSessionsAreReported` (9) |
| 14a | partial relabel counted as resolved | `orphan:*` presence, not absence of `opencode:live` → 3 sessions / 2,926 rows of stale provenance reported clean | 11.4 | `test_PARTIAL_relabel_does_not_pass_as_resolved` |
| 22 | index `provenance` not refreshed after an in-place relabel | 912 index rows kept asserting a dropped harness; `convo-fresh APPLY=1` did **not** fix it — the diff sees no file/row change | 12.3 | `test_resolve_orphans_refreshes_index_provenance` |
| 23 | index↔corpus `provenance` disagreement unchecked | 912 wrong rows, and nothing in `--check` could see them — a repair wired to one command is a convention | 12.3 | `TestProvenanceDriftIsChecked` (4) |
| 15 | guess what a bare `m` means | months vs minutes is a factor of 43,200, on a flag that **purges** | 11.2 | `test_rejects_bare_m` |
| 16 | approximate months as 30 days | cutoff drifts silently on short months (Oct 31 − 6mo is Apr 30, not May 1) | 11.2 | `test_month_end_clamps_to_last_day` |
| 17 | age a session by file **mtime** | `flush()` rewrites on every harvest, so mtime tracks the last APPLY — purging on it keeps dead sessions forever and kills live ones on restore | 11.2 | `test_purges_by_row_timestamp_not_mtime` |
| 18 | relabel every non-live session | snapshot retention is the **feature**; only snapshot-less sessions are orphans | 11.3 | `test_leaves_snapshot_backed_sessions_alone` |
| **Index schema** | | | | |
| 19 | `CREATE TABLE IF NOT EXISTS` to add a column | it is a **no-op on an existing table** — all 121,007 rows read `''`, nothing raised | 12.1 | `test_detects_a_missing_column` |
| 20 | backfill provenance **per file** | 19 of 271 files carry **mixed** provenance (live + snapshot in one parquet), so 50,723 of 121,007 rows — **42%** — stayed empty | 12.1 | `test_handles_a_mixed_provenance_file` |
| 21 | post-filter `--source` on the normal pool | the FTS sidecar has no provenance column, so `--source orphan:` returned **zero hits on every query** | 12.5 | `test_like_pattern_escapes_wildcards` |

**The general shape, three times over.** §9.3 (an optimisation never measured
against its own side effects), §10.4 (a number nobody reads is not a number that
is zero), and this register (a silent wrong answer is the only kind that gets
shipped) are the same finding at three scales. `cProfile`, a field-by-field
equivalence check, and counting the empty rows each found one that reasoning did
not.

### 0.1 Search / leg-function compatibility

**`convo_query.search()` passes `source=` to `_cos_hits` only when a source
filter is active.** The call is otherwise the historical
`_cos_hits(con, query, pool, dims, query_vec, harness=harness)`:

```python
cos = (
    _cos_hits(con, query, pool, dims, query_vec, harness=harness, source=source)
    if source
    else _cos_hits(con, query, pool, dims, query_vec, harness=harness)
)
```

This is deliberate, not stylistic. Widening the call unconditionally to
`_cos_hits(..., source=source)` broke `tests/test_convo_search.py`, which
stubs the leg with a positional lambda and raised
`TypeError: test_query_hybrid_fuse.<locals>.<lambda>() got an unexpected
keyword argument 'source'`.

**If you wrap, monkeypatch or replace `_cos_hits` or `_fts_hits`, accept
`**kwargs` — do not fix the signature to today's call shape.** The same applies
to `_fts_hits`, whose pool grows 25× when `source` is set (`fts_pool = pool *
(25 if source else 1)`); a stub that assumes a fixed pool size will silently
see a different candidate count. Both are load-bearing for query results, and
both fail loudly today only because a test double happened to catch them.

## 1. Motivation

`convo_search` covers opencode, prime-rlm, zcode (+dsh) but not the
billion-context proxy store at `~/.local/share/billion-context/sessions/index.db`
(21 sessions). Its T1 compression blocks — decisions with rationale,
`path:line` anchors, exact errors — are the densest provenance text in any
harness store and are invisible to `convo_query` today. Trigger: viewer
inspection 2026-10-16 showed rich T1 summaries with zero index coverage.

## 2. Evidence (measured 2026-10-16, this box)

| Configuration | Result | Verdict |
|---|---|---|
| ALL blocks (68) | 9/10 (8 at rank 1) | adopt |
| ACTIVE-only (30) | 4/10 — all 5 inactive-only queries miss | dead |
| ALL + 526 raw messages | 9/10, rank table byte-identical to ALL | messages dead weight |
| Full block text (80–210 KB/block) | 10–25x over 8 KiB snippet cap | excluded permanently |

Manual compacts deactivate blocks without replacement
(`supersedes_block_id`/seq spans unpopulated), so the active flag is not a
coverage signal — mtime_gate, D2, crash-recovery, probe S6/S7, prior-arc
answers live only in inactive blocks. index.db-wide search: zero T2/T3 rows,
zero tier markers in 11,467 content blobs.

## 3. Design

- S1: new harvest lane `bili` over index.db `blocks` → corpus
  `memory/data/harness/bili/<session>.parquet`; existing rebuild glob absorbs
  it unchanged, keyed `(harness=bili, part_id=session:block)`. `part_id` MUST
  be session-scoped (`{sid}#{bid}`): bare block ids collide across sessions on
  the `(harness, part_id)` primary key and silently evict each other (found
  live: 72 corpus rows indexed as 57). **Superseded by §7 — billion-context is a
  harness-specific plugin, so the lane routes by namespace instead of writing a
  `bili/` directory.**
- S2: index every block regardless of `active`; tier rides as metadata.
  Highest-tier-per-span rule ships as future-proofing (no T2/T3 data to
  validate against).
- S3: incremental fingerprint `(tier, summary_hash, LENGTH(payload))` —
  unchanged blocks skip re-embed; deactivation is a metadata flip, never a
  delete; true deletes via embed-gc. Fingerprint carries a lane version `v`
  so row-shape changes re-harvest once (v1->v2: session-scoped part_ids).

## 4. Acceptance criteria & shakedown

1. `convo_query.py "<mtime_gate S1/S2 question>"` returns the inactive b11
   block top-3 (regression pin for the active-flag trap).
2. `make qa` green; second harvest run re-embeds zero unchanged blocks.
3. Eval-gate bullet: N/A with reason — the 164 frozen questions answer
   against the graph/research store (subtree roster, incidence, crosswalks,
   provenance_agents); this lane writes convo_search rows only and touches no
   extractor, roster, crosswalk, or hierarchy. No ontology input changed, so
   no answer can move; a parent-vs-candidate run would be vacuous.

| Projected outcome | Today | After |
|---|---|---|
| convo recall over bili sessions | 0/10 | 9/10 |
| harvest rows added | 0 | ~68 |

## 5. Risks

- **Weeks-long sessions churn blocks** — mitigated by summary_hash
  fingerprint (delta-only re-embed) and metadata-flip deactivation.
- **A future T2 changes summary shape** — harvest reads topic+summary keys
  with fallback to full payload; mismatch surfaces as a loud parse error,
  not silent skew.

## 6. Non-goals

Raw messages, full block text, T2/T3 special-casing, backfilling superseded
generations beyond newest-per-topic if the corpus ever needs capping.

## 7. Amendment 2026-10-17 — routing by namespace, and the owned-harness quirk

**Trigger.** billion-context 0.1.197 became a harness-specific plugin system.
`sessions.namespace` is therefore the harness whose sessions it compressed
(`opencode`, `dsh`), not a peer of them. §3 S1's `memory/data/harness/bili/`
directory modelled the wrong shape, and measurement showed why it was never
merely untidy:

- **All 6 bili session files collided by name** with the owning lane's own copy
  — `memory/data/harness/bili/conversations/ses_ebc043….parquet` and
  `…/opencode/conversations/ses_ebc043….parquet` held the same session twice.
- `convo_query.HARNESSES` listed `bili` alongside real harnesses, advertising a
  filter over a directory that should not exist.

**The change.** The lane reads `sessions.namespace` per session and writes to
`writer.add(ns, rows)`, so a dsh session's summaries land in `dsh/` and an
opencode session's in `opencode/`. Safe because the lanes use disjoint `part_id`
schemes — bili `sid#bid`, opencode/dsh `sid:seq` — and share one
`SessionWriter`, so both lanes' rows land in the same per-session file. The 72
migrated rows had **0 part_id overlaps**; corpus total unchanged at 198,147.
`convo_query.HARNESSES` drops `bili` (its two uses: the `--harness` `choices=`
list only).

### The quirk: `_BILI_OWNED_HARNESSES`

`SessionWriter.flush()` merges an existing session file **unless** that session
is in `writer.fresh`, in which case it writes the touched buffer *alone*. The
`fresh` mark exists so a `part_id` shape change cleans both generations instead
of unioning them (the 57 + 57 lesson above). Under routing that same mark is a
**data-loss bug**: the target session file already holds the owning lane's rows,
so marking it fresh overwrites them with the bili rows alone.

So `fresh` is now scoped to harnesses bili owns outright:

```python
_BILI_OWNED_HARNESSES = {"unknown"}
```

`unknown` is the index-without-a-`sessions`-table case — the only namespace with
no corpus directory of its own. Every real namespace (`opencode`, `dsh`, …) has
another lane writing its session files, so routed sessions merge
unconditionally. That is correct precisely because `sid#bid` part_ids are stable
and keyed, so a re-harvest overwrites its own rows in place.

**Corollary for the next arc.** If a new namespace ever gets its own corpus
directory *and* its own harvest lane, the bili lane will route into it and must
**not** be added to `_BILI_OWNED_HARNESSES` — doing so erases that lane's rows.
The set means "no other lane owns these files", not "these are bili files".

Mutation-verified: forcing the mark (`if True:`) fails
`test_bili_rows_merge_into_the_owning_lane_session_file`; routing back to
`writer.add("bili", rows)` fails 6 of 9.

### Also in this amendment: lost vs absent

Under `storage.unified=shadow` (0.1.197+) every session write is double-ingested
into `sessions/index.db` and digest-reconciled, making that file a *derived*
artefact whose lifecycle is governed by the proxy's migration. The lane's quiet
skip conflated "no billion-context on this machine" with "the proxy is installed
but the index was reset", so a migration deleting it truncated the lane's history
while the gate stayed green. Now tri-state — `_bili_index_state()` returns
`ready` / `absent` (quiet, by design) / `lost` (reported by `_bili_drift()` and
`_coverage()` as `bili: LOST index.db — …`).

## 8. Amendment 2026-10-17 — how compaction and folding actually decide

Everything here was measured from `~/.local/state/billion-context/bili.log*`
(proxy 0.1.197) on session `ses_ec7af063cffew2a8xvEcV142n6`, plus a whole-family
sweep over 2026-10-13 → 2026-10-17. **Several of these are counter-intuitive and
each one has already cost a wrong conclusion — read the whole section before
tuning anything.**

### 8.1 A nudge line is an *evaluation*, not an event

Every request emits one, whether or not anything happened. `reason=` is a
decision trace, not an alarm:

```text
nudge idle:   usage=37% (291536/792576), growth=232772/90000 (ref=58764, interval=200000),
              pendingT1=199877/200000  model=…  reason="max compressible 199877 < threshold 200000"
nudge INJECT T1: usage=37% (291748/792576), growth=232984/90000 (ref=58764, interval=200000),
              pendingT1=200849/200000  model=…  reason="T1 effective 200849 >= 200000, growth 232984, usage 37%"
```

868 evaluations, **1** action. Reading "the nudge fired 750 times" is the single
most common misreading of this log — the count is the *decision* count.

### 8.2 Two gates, and `reason=` is their conjunction

`"max compressible N < threshold N; growth N < floor N"` — both clauses must hold
to block, so a tier-1 fold needs **both** to open:

| gate | max reached | bar | crossed |
|---|---|---|---|
| growth | 232,772 | floor 90,000 / 135,000 | **262 of 868 (30%)** |
| max compressible | 199,877 | threshold 200,000 / 300,000 | **0** |

**Growth is almost never the blocker.** It ran 2.6× over its floor on a third of
evaluations while nothing happened. Tuning the growth number is almost always
tuning the wrong dial — see §8.3.

### 8.3 `nudgeGrowthTokens` does double duty — raising it *suppresses* folds

Verified on every sample: `threshold == interval` (always). The compressible
threshold **is** `nudgeGrowthTokens`. And the growth floor is derived from it:

```text
growth floor = interval × 0.45
```

| `nudgeGrowthTokens` | growth floor | compressible threshold | folds in the 200k phase |
|---|---|---|---|
| 200,000 | 90,000 | 200,000 | 1 |
| 250,000 | 112,500 | 250,000 | 0 |
| 300,000 | 135,000 | 300,000 | 0 |

So the knob's intuitive direction is **inverted for compression**: raising it
raises the compressible bar in lockstep and suppresses folds. A session that
shows "too many folds" and raises the value will see fewer, silently.

### 8.4 `max compressible` *is* `pendingT1`

Identical in **836 of 868** samples (96.3%). One quantity, two names — do not
model them as independent.

### 8.5 The compressible span is a *fraction* of context, so the bar is a usage bar

`compressible / used_tokens`: **median 0.64, p90 0.76, max 1.43**. The threshold is
absolute while the quantity is proportional, so a fixed `nudgeGrowthTokens` maps
to a *usage percentage* that varies with how compressible your content is:

| threshold | tokens needed | ≈ % of a 792,576 window |
|---|---|---|
| 200,000 | ~313,878 | 39.6% |
| 150,000 | ~235,408 | 29.7% |
| 100,000 | ~156,939 | 19.8% |
| 75,000 | ~117,704 | 14.9% |

This session peaked at **36.8%** — pinned just under a trigger that sat at ~39.6%.

### 8.6 The margin is razor thin, and that is the whole quirk

| | miss (04:25:0x) | fire (04:25:16) |
|---|---|---|
| pendingT1 / compressible | 199,877 | **200,849** |
| vs threshold 200,000 | **−123** | **+849** |
| usage | 37% | 37% |
| growth / floor | 232,772 / 90,000 | 232,984 / 90,000 |

**One request and +212 tokens of context** separated a miss from a fire. The gate
is a hard `>=` on a quantity that grows ~0.65× with context, so in practice it is
either unreachable or cleared within a single turn. There is no tuning regime
where it fires "about when you want".

### 8.7 T2 and T3 have never fired — not once, in the whole log family

| kind | count (2026-10-13 → 10-17) |
|---|---|
| `INJECT T1` | 34 |
| `INJECT-ESC T?` | 22 |
| **`INJECT T2`** | **0** |
| **`INJECT T3`** | **0** |

Today (2026-10-17) the only action was the T1 above. If you are reading a
dashboard and see no T2/T3, that is the expected state, not a misconfiguration.

Two non-tier paths exist and are easy to mistake for tier-2/3 activity:

- **`EMERGENCY T1`** — `reason="EMERGENCY T1: max effective pending N, usage N%"`.
  Fires on **usage alone**, bypassing both gates (observed at 108% and 176% usage).
- **`INJECT-ESC T?`** — the tier is literally unknown (`T?`). All 22 instances sit
  at **70–75% usage with *both* gates unmet** (e.g. `usage=70%, growth=3834/90000,
  pendingT1=91467/200000`). A usage-pressure path, not a tier-2 fold.

### 8.8 Manual `compress` arms the automatic trigger — it is self-defeating

The 04:25 fold ran on `compress requested 1 range(s): m01560–m02410` — the range
**I had manually compressed three messages earlier**:

```text
[acp-compress-obs] shrink 31% (~197546/638479 tok) foldPoint=m01560 blocks=1
                    anchor≈19123 tok (19 active blocks, sys excluded)
                    postCtx≈440933 → next-request cache ceiling ≥4%
```

`blocks=1`, `anchor≈19123`: the anchor was the block *I* had just created. The
~197,546-token block became pending-T1 mass, pushed `pendingT1` from 199,877 to
200,849, and the automatic T1 then re-compressed the same range. **This also
explains the `pendingT1` climb itself** — it was tracking my own fold, not
organic drift.

Practical rule: do not manually compress a range and then keep working inside it.

### 8.9 Fold *health* and fold *cost* are different things

The feared failure was a degenerate fold (≥80% shrink) wrecking the cache. That
did **not** happen — shrink was 31%. The real cost is a one-turn cache miss:

```text
input=294698 cached=291748 (cache hit 99%)   ← request that triggered the fold
input= 54060 cached= 32779 (cache hit 61%)   ← first request after
input= 54631 cached= 54060 (cache hit 99%)   ← recovered next turn
```

Context 294,698 → 54,060 (the 638,479 denominator counts 19 blocks, mostly
already-compressed ones no longer billed). The plugin's own predicted "≥4%
ceiling" never materialised — the anchor structure absorbed it. **Judge a fold on
the cache dip, not on the shrink percentage.**

### 8.10 The dominant cache-loss mechanism is *not* compaction

This is the finding that most changes how the numbers should be read.

| metric | value |
|---|---|
| cache collapses (≥85% → ≤60%) across 5 days | **154** (29 / 51 / 39 / 49 / 4 by day) |
| preceded by a `compress-obs` / `compress requested` marker | **2** |
| median `input` delta across a collapse | **+135 tokens** |
| collapses within 6 lines of a `[window-change]` | **~2** |
| input at collapse | median 99,775, spread evenly 12k–292k — no threshold clustering |
| gap between consecutive collapses | min 5s, p25 286s, median 824s, max 37,865s |

The conversation is **essentially unchanged** across these events — the context
did not shrink, so nothing compacted. This is a cache-key invalidation on an
unchanged conversation, ~30× more frequent than folds, and it is the real
budget line. Two hypotheses remain open and the log does not settle it:
provider-side cache TTL/expiry (the gap distribution is broad, *not* a clean
5-minute TTL, which argues against it) versus harness/session-side events.

`bili_sessions.py`'s fold counters therefore **understate** cache damage badly.
Any tuning decision made from fold counts alone is made on the wrong signal.

### 8.11 Parsing this log — five traps that each produced a wrong number

1. **Anchor per-session filters on `[<sid>] nudge`**, not `[sess=<sid>]`.**
   `[sess=…]` also matches lines whose *payload* belongs to another session, and
   the raw scan then returned max growth 225,890 instead of 232,772.
2. **`nudge` also appears in `[headroom] … nudge/preflight` lines.** A substring
   match on `"nudge"` picks them up.
3. **`m.groups()` is 0-indexed.** Indexing from 1 silently shifts every field by
   one and yields plausible-looking nonsense Counters.
4. **The kind token contains a space** — `INJECT T1`, `INJECT-ESC T?`. A pattern
   of `INJECT[^\s:]*` **fails to match** because `[^\s:]` stops at the space. Use
   `INJECT[^:]*`.
5. **`usage=` lives in the line prefix, not in `reason`.** `NudgeSample.reason`
   carries only the compressible clause, so correlating compressible against
   usage requires re-parsing the raw line.

Also: **`max growth seen` must be re-read after the rotation-merge fix.** A
`--detail` run made before `log_rotation_family` existed reported 197,532 for
this session; the true value is 232,772. The print itself
(`bili_analytics.py:742-743`) reads `max(n.growth for n in a.nudges)` and cannot
under-report on its own — the stale figure came from a stale parse, not a bug.

## 9. Amendment 2026-10-17 — `bili_sessions` profile, and three silent under-reports

Profiling the 35-session table: **7.7 s**, and *every* second of it was
structural waste rather than work.

### 9.1 Three defects found by profiling

| cost | cause | fix |
|---|---|---|
| 4.1 s (53%) | `parse_logs(log_rotation_family(log_path), session_filter=sid)` **once per session** — a full rescan of the 15 MB family 35 times. Added by the §8 rotation merge. | `_family_index(log_path)` parses the family **once** and buckets by session |
| 1.3 s (17%) | `_dashboard_urls()` walked **every PID's `cmdline`** once per session — 71 walks, **22,072 `read_text` calls** | `functools.lru_cache` |
| 1.3 s (17%) | 142 `urlopen` calls, ~2 per session, for payloads that cannot change mid-run | `lru_cache` on the fetches |

Plus a fourth that profiling could not see: `sid in log_path.read_text(...)`
per session — a whole-file read per session purely to test substring
containment. The family index answers that for free.

**6.30 s → 1.63 s (3.9×)**, with the equivalence verified field-by-field across
all 35 sessions: nudges, usages, folds, growth sums, input sums, fold savings,
`intervals_seen`, `reasoning_pair_violations`, `effective_interval`, warning
kinds — **0 mismatches**.

Two shape traps the equivalence check caught, both of which would have been
silent data changes smuggled in as a "performance fix":

- `intervals_seen` keys are **strings** (`{"200000": 264}`) in `parse_log`. An
  index built with `Counter(n.interval ...)` yields `int` keys, so `--json`
  consumers would have seen a different shape with no error raised.
- `reasoning_pair_violations` is a bare `int`, **not** a list, so it cannot be
  partitioned post-hoc — it is recovered from `warnings` where each violation
  carries its session. Dropping it silently zeroed 13 violations on one session.

### 9.2 `--since` / `--all`: the table grows forever, the log does not get read

`--since DAYS` (default **7**) hides sessions whose `lastSeen` is older;
`--all` (or `--since 0`) disables the window. Verified: `--since 0.5` hides 29 of
35 and prints the count to **stderr** so the table on stdout stays pipeable.

Two deliberate choices:

- **Rows with no parsable `lastSeen` are KEPT.** The window is a performance
  default; silently dropping rows it cannot date would hide sessions rather than
  merely defer them.
- **The window sorts nothing and fetches nothing extra** — it filters the rows
  the dashboard already returned. So today it changes nothing (the log only
  spans 4 days); it is insurance against the corpus growing, and the 3.9× above
  is what makes `--all` cheap enough that the insurance is not load-bearing.

Tests: `tests/test_bili_sessions_index.py` (17) and
`tests/test_bili_nudge_parse.py` (11). Mutation-verified — reverting each fix
individually fails a named test, except the per-bucket `sort()`, which is
defensive against a `parse_logs` guarantee rather than load-bearing today.

### 9.3 The general lesson

§8.10 and §9.1 are the same shape: **an optimisation never measured against its
own side effects.** The rotation merge fixed blank models by reading 3.3× more
data, and the table got slower because the per-session rescan was never
revisited. Neither defect throws — both report plausible numbers. Only
`cProfile` and a field-by-field equivalence check found them.

## 10. Reasoning-pair violations are a SESSION KILLER, not a warning

**This section corrects §9.1, which treated losing the violation count as a
"silent data-shape change" worth a test but not worth surfacing. It is a
terminal event and belongs at the top of the default view.**

### 10.1 What it is

Emitted by the proxy at log level `warn` (`#684/#762/#2864`):

```text
reasoning-pair-violated: 1 assistant tool-call message(s) lack reasoning_content while 6
carry it — strict-echo upstreams (DeepSeek thinking mode) will reject the request;
first seen: call_de44456220bf4c6088751ce0 (window: since this proxy started)
```

Assistant **tool-call** messages that carry no reasoning block, mixed into a
transcript whose other tool-call messages do carry one. A strict-echo upstream
rejects the whole request. So a stored transcript with a missing reasoning block
is not a cosmetic defect — it is a request that some providers will refuse.

### 10.2 The measurement that reclassified it

Operator report: **only DeepSeek and zAI recover; every other model crashes the
session and it has to be restarted** (and DS/zAI were hit once too). Confirmed
against the log — all three violating sessions end within half an hour of their
**last** violation and never resume:

| session | violations | last violation → session end | lifespan percentile |
|---|---|---|---|
| `ses_ee4e1c35effe9qT1190OSJ0GHv` | 13 | **10 min** | 100th |
| `ses_ec9ae51bcffe7Gq8YkIoFW5N5Y` | 1 | **29 min** | 87.5th |
| `session-e2fe8e51-…` | 1 | **3 min** | 84.4th |

The percentile column is the part that matters. Clean-session lifespan median is
**0.1 h**, so these are among the **longest** sessions in the corpus — the
violation does not shorten a session, it **terminates a long healthy one**, which
is the expensive case. Neither is DeepSeek- or zAI-primary: glm-5.3-flash,
space-bunny-free, muse-spark, cat-jam-free, mimo-v2.6-flash-free, ling-3.1.

Two readings that are wrong and were corrected here:

- ❌ "every violating session kept working" — the gap analysis says the opposite.
- ❌ "not fold-induced, because 13 of 15 predate the folding work" — 13 of 15 sit
  in one 95-hour session, so the count says nothing about causation either way.
  Untested; do not assert it.

### 10.3 Why it was invisible, and the fix

`reason_pair_violations` was a row field that **no column read**, shown only by
`--detail` — which you consult once you already suspect trouble. A session-killer
sat behind a flag, and no gate checked it.

| surface | change |
|---|---|
| default table | new **`viol`** column, last and unsqueezeable, `*13` / `*1` when non-zero, `-` otherwise |
| above the table | **two lines**, nothing else: `!! reasoning-pair violation — N session(s) at terminal risk: *13 ses_…, *1 ses_…` and the `--detail <session>` pointer |
| `--detail <sid>` | the full treatment — general (NOT model-gated; DS/zAI were hit too), the mechanism, this session's own measured end-gap, and the raw proxy lines |
| `--json` | callout goes to **stderr**, never stdout — a human line in front of the array breaks every piped consumer. `reason_pair_violations` is always present (default `0`) so the shape is stable |
| `_viol_details` / `_viol_gap` | private; too long for a column, meaningless until someone has decided to look |

**The `*count` is deliberately the whole indicator in the table.** An earlier
draft put a four-paragraph explanation plus a per-session block above the table;
it was correct and it made the default view unreadable on the runs where the
answer is `-` (32 of 35 sessions). The mechanism belongs behind `--detail`, where
you go *because* the column told you to. A test asserts the callout stays two
lines and contains none of the explanation words.

Sub-sessions share the parent's transcript, so a violation on `session-…` also
shows on `session-…|sub:<id>`. The callout collapses on the parent id so the
count names transcripts at risk rather than rows — without that it reported
"4 sessions" for 3 transcripts.

Tests: `tests/test_bili_sessions_index.py::TestReasoningPairViolationsAreLoud`
(10). Mutation-verified — dropping the marker, the callout guard, the
sub-session collapse, the column entry, the "last violation not first" basis, or
the stderr routing of the `--json` path each fails a named test.

### 10.4 The general lesson, twice over

§9.3 said an unmeasured optimisation is the failure mode. This is the sharper
version: **a number nobody reads is not the same as a number that is zero.** The
count was always available, always correct, and carried no information for
anyone looking at the table — so three sessions died with the evidence sitting
in a field. If a metric predicts data loss, it belongs on the default surface
even when it is almost always empty.

## 11. Retention: deleting a harness session did NOT remove it

### 11.1 The leak

Deleting a session in the harness removes it from `opencode.db` completely —
**0 rows left in `message`, 0 in `part`** — but the corpus kept all of it, still
searchable. Measured 2026-10-17:

| | count |
|---|---|
| `opencode.db` live sessions | 29 |
| corpus parquet files | 181 |
| in corpus, not in `opencode.db` | 152 |
| …covered by a point-in-time snapshot | **123** — deliberate retention |
| …**pure orphans** (live-harvested, deleted, no snapshot) | **29 sessions / 16,949 rows** |
| …of those, searchable in `convo_search` | **11,711 rows** |

Three layers, all one-directional:

1. `SessionWriter.flush()` (`:257`) iterates `self.touched` — the sessions a
   source *reported* — merges, and never enumerates what vanished. Add/merge only.
2. `stale_deleted` (`:1884`) was **declared and never assigned**: one occurrence,
   zero writes. The vestige of a deletion-tracking design that was never built,
   sitting beside a live `stale_new` and `stale_changed`. **Now populated** —
   see 11.4.
3. `rebuild_convo_search.py:456` drops DuckDB rows only for files in
   `diff["deleted"]` — and the parquet is never deleted.

### 11.4 Closing the vestigial field

Documenting the dead `stale_deleted` field is not the same as fixing it, and
leaving it dead is how 29 sessions stayed invisible. `_deleted_sessions()`
(`harvest_conversations.py`) now enumerates every corpus session the harness no
longer has and returns a pair:

| field | meaning | gates? |
|---|---|---|
| `stale_deleted` | every corpus session absent from the source's live manifest | no — snapshot-backed and already-relabelled ones are not drift |
| `stale_deleted_unresolved` | the subset **still labelled `opencode:live`** | **yes** |

The split is what makes the switch usable. Gating on all 29 would mean a corpus
that is permanently red until someone deletes history; gating on none would
mean the provenance lie goes unreported. Gating on *unresolved* means
`--resolve-orphans` closes the gate once and the corpus stays green afterwards,
while a newly-orphaned session still trips `--check` on the next run.

**"Unresolved" means ANY row still claims `<harness>:live` — not "the orphan
label is absent".** The first version checked for the label's presence, and a
partially-relabelled file passed. `--resolve-orphans` rewrites the rows present
when it runs; rows harvested afterwards re-assert `<harness>:live` into the same
parquet, so a file can carry both labels at once. Measured on the live corpus
after the 11:42 APPLY: **3 of the 29 orphans were mixed**
(`ses_ec7af063cffew2a8xvEcV142n6` 1351 orphan / 775 live + 19 `bili-index`,
`ses_ebc043049ffeXE8Vvcudwh4VCu` 730 / 9, `ses_ebc050416ffeNxMq6pRKMrtzwL` 3 / 39)
— **2,926 rows** still asserting a harness that dropped them, all of which the
presence-check reported as resolved. Correcting it flipped `unresolved` 0 → 3
on the real corpus, which is the number the gate should have been reporting all
along.

Two exclusions are load-bearing:

- **Snapshot-backed sessions are not drift.** 123 of the 152 predate this work
  and are retained deliberately — the snapshot lanes exist so history outlives
  the harness. Treating "the harness purged it" as a fault would flag correct
  behaviour.
- **A harness with no manifest is skipped, not guessed at.** `_live_session_ids()`
  returns `None` for dsh/prime-rlm/zcode (file-enumerated sources, no equivalent
  of opencode's `session` table). An empty set would report every one of their
  sessions as deleted; `None` means "cannot answer".

`--check` prints the deleted count with an `UNRESOLVED` / `orphan:*` marker per
session and names the fix command.

### 11.2 Two new switches, one shared parser

`helpers/core/ranges.py` parses `30d` / `6mo` / `1y` / `12h` / `45min`, or a
bare number of days. Two decisions exist to prevent mistakes on a flag that
deletes data:

- **A bare `m` is REJECTED, not guessed.** `m` is months in a retention window
  and minutes everywhere else — a factor of 43,200. `parse_range("6m")` raises
  and names both spellings.
- **Months and years use calendar arithmetic.** `6mo` from 2026-10-31 is
  2026-04-30, not 2026-05-01. A 30-day approximation drifts silently on short
  months. Implemented here rather than with `dateutil.relativedelta`, which is
  present in the venv but only transitively — `deptry` would flag it.

| flag | where | effect |
|---|---|---|
| `--since RANGE` | `bili_sessions.py` | display window, default `7d`; `0`/`--all` disables |
| `--keep RANGE` | `harvest_conversations.py` | **purges** corpus parquets whose newest row predates RANGE; `--dry-run` reports only |
| `--resolve-orphans` | `harvest_conversations.py` | relabels sessions the harness dropped as `orphan:<harness>`; rows retained |

**`--keep` ages by the session's own newest `ts`, never by file mtime.** `flush()`
rewrites a file on every harvest it touches, so mtime tracks the last APPLY, not
the conversation — purging on mtime would keep dead sessions alive forever and
delete live ones the moment the store was restored.

### 11.3 What was applied, and what was deliberately not

- **Relabelled 16,949 rows across 29 files** as `orphan:opencode`. Verified: 29
  orphans now labelled, **0 still claiming `opencode:live`**, all 16,949 rows
  retained, and a second pass is a no-op (idempotent). Backup at
  `$TMPDIR/corpus_pre_orphan_20261017_114233`.
- **123 snapshot-backed sessions untouched** — the snapshot lanes exist so
  history outlives the harness. `--keep` still bounds them, because that flag is
  about age, not provenance.
- **dsh / prime / zcode are skipped.** They are file-enumerated with no live
  manifest, so `--resolve-orphans` returns `None` for them and changes nothing.
  Relabelling 500 live sessions as orphans is not a recoverable mistake.
- **Nothing was purged.** 16 sessions are already older than 90d (31,614 rows)
  and `--keep 90d` will take them, but that is the operator's call.

`make convo-fresh APPLY=1` is needed to reindex the relabelled rows.

Tests: `tests/test_ranges.py` (40), `tests/test_harvest_retention.py` (15).
Mutation-verified — dropping the snapshot exemption, relabelling unknown
harnesses, purging by mtime, validating the range after the purge, or losing
idempotency each fails a named test.

## 12. `provenance`: the orphan label, queryable

§11.3 relabelled 29 orphans in the parquet and noted the consequence:
`convo_search` had **no `source` column**, so the label was invisible to every
query. It is now indexed.

### 12.1 Two traps, both silent

**`CREATE TABLE IF NOT EXISTS` cannot add a column.** It is a no-op on a table
that already exists, so the DDL change alone left all **121,007 rows reading
`''`** with nothing raised anywhere. `_provenance_migration()` checks `DESCRIBE`
and `ALTER TABLE ... ADD COLUMN`.

**19 of the 271 corpus files carry MIXED provenance** — a session harvested
live *and* covered by a snapshot has both `opencode:live` and
`opencode-snap:<ts>` in one parquet. A per-file `UPDATE` therefore either
writes a lie or skips the file, and skipping leaves ~2,670 rows empty per file.
The backfill joins on `(file_path, row_no)` instead. Measured on the first
attempt: **50,723 of 121,007 rows still empty (42%)** — caught only because I
counted the empty rows instead of trusting the exit code.

### 12.2 Backfill, not rebuild

A missing column on existing rows invites a full rebuild — and a full rebuild is
a **45-minute re-embed** of every vector that changes nothing about the vector
column. The backfill is one `UPDATE` per (file, source-bucket):

```text
backfilled 260 files, 121,007 rows in 4.6s
index: 121,007 rows, 0 empty, 100.00% coverage
orphan sessions queryable: 29
```

### 12.3 The corpus is the truth; the index is a copy that can go stale

`--resolve-orphans` rewrites the `source` column **in place**. That produces no new
file and no moved row, so the rebuild's file-diff has nothing to detect — and a
`convo_search.provenance` copied at read time keeps serving the old label until
something re-reads it. Nothing raised. Measured after relabelling 842 rows across
3 files:

| session | index said | parquet said |
|---|---|---|
| `ses_ebc043049ffeXE8Vvcudwh4VCu` | `orphan:opencode` 713 / **`opencode:live` 8** | all orphan |
| `ses_ebc050416ffeNxMq6pRKMrtzwL` | **`opencode:live` 39** / `orphan:opencode` 3 | all orphan |
| `ses_ec7af063cffew2a8xvEcV142n6` | `orphan:opencode` 1363 / **`opencode:live` 865** / `bili-index` 4 | all orphan |

**912 index rows were asserting a harness that had dropped the session**, and
running `make convo-fresh APPLY=1` did not fix them — the APPLY re-read the
corpus and still wrote the stale values, because the diff it consults is
file/row-identity based and neither moved.

The fix puts the repair where the change is known:

- `_refresh_index_provenance(files)` calls the existing `_backfill_provenance`,
  which joins on `(file_path, row_no)` positionally — correct for mixed-provenance
  files and needs no re-embedding, since provenance is not a vector.
- `--resolve-orphans` now collects the files it touched and refreshes them in the
  same invocation, so the corpus and its index cannot disagree.
- `--refresh-index-provenance` is the standalone repair for an index already left
  behind (this document's own state for a while).

```text
$ harvest_conversations.py --refresh-index-provenance
  provenance: refreshed 121932 index row(s) across 260 file(s)
```

After which all three sessions read `orphan:opencode` end to end — 721 / 42 / 2232
rows — with `121932 rows, 0 empty` overall.

**Known loss, stated rather than hidden:** `--resolve-orphans` collapses *every*
non-orphan source in a file to `orphan:<harness>`, which discards the
`bili-index/opencode` sub-label on rows inside orphan files — 19 rows of
`ses_ec7af063cffew2a8xvEcV142n6`. 57 such rows survive elsewhere in the corpus,
but a `--source 'bili-index%'` query is now blind to those 19. Preserving the
sub-label would mean keeping the row's *store* while rewriting its *ownership*,
which needs a second column; it is not worth one for 19 rows, and the loss is
recorded here rather than discovered later.

**And a check, because a repair wired into one command is a convention, not a
guarantee.** `_provenance_drift()` joins `convo_search.provenance` against the
parquet's `source` on `(file_path, row_no)` and reports every disagreement;
`--check` surfaces it as `stale_provenance` and **gates on it**, naming
`--refresh-index-provenance` as the fix. Cost on the live corpus: **0.8s** for
199,448 compared rows, which is affordable on the check path.

Three distinctions the check is careful about:

- **An unindexed row is not drift.** It is `stale_new`'s business — the file has
  not been read yet, so there is no stored value to disagree with.
- **A corpus file outside `REPO` is checked, not skipped.** `--corpus-root` can
  point anywhere; `relative_to(REPO)` raising would silently check nothing, which
  is precisely how an unchecked drift gets in. The first test run caught exactly
  this.
- **The 19 `bili-index` rows are not drift either** — corpus and index agree on
  the lossy value. Drift is disagreement; loss is something else, and §12.3
  records it separately so the two are not confused.

### 12.4 Retention and provenance are separate questions

Treating "a snapshot backs this session" as "this session is clean" was the
worst assumption of the four, and the most expensive. It made the 29 pure
orphans look like the whole problem while hiding a larger one:

| set | sessions | rows labelled `<harness>:live` for a session the harness dropped |
|---|---|---|
| pure orphans (no snapshot) | 29 | 2,926 → relabelled 2026-10-17 |
| **snapshot-backed** | **123** | **21,127 → relabelled 2026-10-17** |
| genuinely live | 29 | 0 |

A snapshot answers *should we keep this file?* It says nothing about *does the
harness still own these rows?* Those are different questions with different
answers, and answering the first silently gave the second for free.

So the relabel is now **per row, not per file**: `_is_snapshot_src()` names the
two snapshot label shapes (`opencode-snap:<stamp>` and `<harness>:snap`), and
`_claims_live_harness()` is the single predicate that decides whether a label
asserts ownership — anything not a snapshot and not already `orphan:` does.
`_resolve_orphans` keeps snapshot rows verbatim and rewrites only the live-claimed
ones; `_deleted_sessions` reports a snapshot-backed session as unresolved when
any of its rows still claims the harness.

Net effect on the live corpus: `deleted=152` (all of them, informational),
`unresolved=16` (the files carrying live claims), and after the relabel both
sources are snapshot + `orphan:` with no harness claim left.

`test_snapshot_backed_is_not_drift` was rewritten rather than deleted: its
original assertion — a snapshot-backed session is absent from `deleted` — is
what encoded the bug. It now asserts the session is *reported* but does not
*gate*, which is the distinction that was missing.

**Applied 2026-10-17** (backup `$TMPDIR/corpus_pre_snapshot_relabel_20261017_130221`):

```text
relabelled 23242 row(s) across 16 file(s) as orphan
  scanned 271 parquet file(s)
provenance: refreshed 121932 index row(s) across 16 file(s)
```

| check | before | after |
|---|---|---|
| sessions gone from `opencode.db` still carrying a live claim | **16** | **0** |
| sessions holding a snapshot label **and** `orphan:opencode` | 0 | **14** |
| `_deleted_sessions` unresolved | 0 (wrongly) | **0** (correctly — nothing left to claim) |
| `_provenance_drift` | 0 | **0** |
| index rows with empty `provenance` | 0 | **0** |
| index rows claiming a dropped session | **21,127** | **0** |
| `orphan:opencode` rows in the index | 730 | **33,811** |
| orphan sessions queryable | 29 | **43** |

43, not 29: the 14 mixed sessions now carry `orphan:opencode` *alongside* their
snapshot rows rather than instead of nothing, so `--source 'orphan:%'` reaches
content it could not before. The relabel is additive to the query surface.

`--check` now reports `deleted=152 unresolved=0` — all 152 gone sessions are
listed, none gate — and separately flags `opencode:live:v2` and
`bili:ses_ec7af063` as drifted source *content*, which is the ordinary
post-rewrite state and is cleared by `make convo-fresh APPLY=1` (the provenance
column is already correct; only the vector/fts side lags).

**The lesson worth keeping:** the check that found this one — `_provenance_drift`
— passed clean the whole time, because corpus and index agreed with each other
and both were wrong. Agreement is not correctness. It took a question about 19
rows to get the count that mattered, and the fix that followed exposed a larger
class than the one that prompted it. A gate that only compares two derived
stores cannot see a shared upstream error.

### 12.5 The query surface — and the starvation it nearly shipped with

`--source PATTERN` filters on provenance. It works on the cosine leg *in SQL*
(`convo_search` has the column) but the FTS leg **cannot**: `convo_fts` is a
separate sqlite sidecar with no provenance column. So the FTS leg over-fetches
25× and filters after.

That is not a theoretical concern — the first implementation post-filtered on
the normal pool and `--source orphan:` returned **zero hits** on every query,
because orphans are ~10% of the corpus and the top-20 BM25 results on a generic
term contained none. Same failure the `harness` filter's own comment records
from the bili lane. An earlier version of this document proposed a plain
post-filter and would have shipped a filter that always looked broken.

`render_hits` prints the label only when it is not `opencode:live`, so the
default result view is unchanged and the interesting labels stand out:

```text
 1. [8.948 bm25] opencode/reasoning/assistant 2026-10-03 12:09 len=687
    provenance: orphan:opencode
    memory/data/harness/opencode/conversations/ses_f03f7e197ffePmV6cr2MHW8yFX.parquet:1430
```

Tests: `tests/test_convo_provenance.py` (10). Mutation-verified — a no-op
migration, a per-file backfill, unescaped LIKE, dropping `provenance` from
`_fetch_rows`, and printing the uninteresting live label each fail a named test.

**`convo_query.search()` only passes `source=` to `_cos_hits` when a source
filter is active** — see §0.1 for the full compatibility note and the reason
the call is not simply widened.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-16 | /mnt/data/tmp/bili_trial.py | ALL 9/10, ACTIVE 4/10 | local_embedder 384-d, 594 docs |
| 2026-10-16 | /mnt/data/tmp/bili_trial2.py | rank tables identical | vectors at bili_vecs.pkl |
