---
title: "BILI summaries as first-class convo_search citizens — query + rank + filter"
status: executed
filed: "2026-11-10"
executed: "2026-11-10"
completed_md: "500"
area: "helpers/misc/convo_query.py, helpers/maintenance/harvest_conversations.py, helpers/maintenance/rebuild_convo_search.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# BILI summaries as first-class convo_search citizens — query + rank + filter

**Date:** 2026-11-10 · **Status:** EXECUTED · **Completed:** completed.md #500 ·
**Area:** convo_search query lane + bili harvest post-processing + rebuild schema

## 0. Traps register — read this before trusting anything below

Defects found in this survey (2026-11-10) of the executed bili lane. Each one
returned a plausible-looking number; every one was wrong.

| # | trap | what it silently produced | caught by |
|---|---|---|---|
| 1 | read the executed proposal and assume it works as written | expected a separate `memory/data/harness/bili/` directory — none exists (§1, §7 of the executed proposal retired it, the lane routes by namespace) | re-reading `harvest_conversations.py:1332-1501` |
| 2 | count bili rows by `source LIKE 'bili-index%'` | 539 rows instead of the true 1,583 (opencode 1,044, dsh 456, proxy 83) — the orphan-relabel pass rewrites `bili-index/<ns>` → `orphan:<ns>` so the prefix filter misses everything (`harvest_conversations.py:2485+`) | filter by `agent='bili'` and `meta_json LIKE '%namespace%'` |
| 3 | assume the bili/opencode gap was a routing bug | proposed a routing fix that was already correct — the gap was an analytical artifact of #2 | row counts via the `meta_json.namespace` field across all harnesses |
| 4 | trust the bili block count as the bili-row count | 1,041 bili blocks for opencode-namespace sessions; 1,044 bili rows in the opencode corpus (3-row delta is one rerun + meta updates) — measure rows not blocks when sizing the lane | row-level count of `agent='bili' part_type='compression-summary'` |
| 5 | mix `compaction` (208 rows, different source) with `compression-summary` | double-counted the bili lane's footprint by ~13% | partition on `part_type` |

**The general shape.** Every error here was the same finding at three scales
(§0.1, §0.4 of the executed bili proposal): a silent wrong answer is the only
kind that gets shipped. The fix in every case was the same — measure rows via
the field that IS authoritative (`meta_json.namespace`), not the field that IS
convenient (`source`).

## 1. Motivation

`convo_search` covers opencode / prime-rlm / zcode / dsh but **bili compression
summaries** — the densest provenance text in any harness store (decisions with
rationale, `path:line` anchors, exact errors) — are second-class citizens:

1. **No filter.** `convo_query.py` has no `--agent bili`, no `--part-type
   compression-summary`, no `--bili-tier N`. Users can't ask "what did the
   proxy conclude about X". The executed bili proposal retired `bili` from
   `HARNESSES` (§7 amendment) so even the prefix is gone.
2. **No ranking boost.** A 200 KB raw session ranks above a 1 KB dense summary
   on identical cosine similarity — the conclusion loses to the noise it
   replaced. The executed bili proposal §3 S2 indexes every block but does not
   intervene in scoring.
3. **Invisible active/inactive distinction.** 824/1041 (79%) of bili opencode
   blocks are `active=0` — superseded by a later tier, but the executed
   proposal §3 S2 explicitly keeps them because they hold unique topics that
   the active set misses. `convo_query` has no way to surface or filter this
   signal.
4. **Source-tag re-labeling.** The orphan-relabel pass
   (`harvest_conversations.py:2485+`) rewrites `bili-index/<ns>` to
   `orphan:<ns>` for sessions the harness no longer has. This is correct
   behavior (the bili blocks survive) but `--source bili-index/opencode`
   returns 0 rows and `--source orphan:opencode --agent bili` finds them — the
   truth is one extra clause away from a query.

Trigger: 2026-11-10 follow-on review of the executed bili lane (#406). The
executed lane was correct; what is missing is the user surface on top of it.

## 2. Evidence (measured 2026-11-10, this box)

| Configuration | Result | Verdict |
|---|---|---|
| bili rows across the corpus (via `agent='bili'`) | 1,583 total (opencode 1,044, dsh 456, proxy 83) | baseline (lane is alive) |
| bili opencode `active=0` blocks | 824/1041 = 79.2% | confirms executed proposal §3 S2 thesis |
| bili rows retrievable via `--source bili-index/*` | **0** | the relabel pass swallows them — filter #4 |
| bili rows retrievable via `--agent bili --harness opencode` | **1,044** | correct retrieval path today |
| `convo_query` rank: raw user prompt vs bili summary on identical vector | raw wins 7/10 (cosine ties, length dominates) | confirms ranking gap |
| `convo_query` tier signal surfaced to user | **none** (not in select, not in rank, not in filter) | confirms gap #3 |

```bash
$ .venv/bin/python3 helpers/misc/convo_query.py "what did we settle about mem" \
    --harness opencode --limit 3
# returns 3 raw opencode session rows; the bili summary that answered this
# question sits at rank 7 with cosine=0.41 (just below the cutoff)
```

**`/mnt/store/timeshift/snapshots/` is now mounted** (8 dated snapshots:
2026-07-26_19-00-02 → 2026-11-07_17-00-06). The new `2026-11-07` snapshot
postdates the corpus's last digest (2026-10-31) by 7 days; running
`make convo-fresh APPLY=1` will ingest it as `opencode-snap:2026-11-07`,
`prime-snap:2026-11-07`, `zcode-snap:2026-11-07` lanes (the harvester
auto-discovers; no patch needed). Earlier notes that the snapshot dir was
empty are now stale and removed from §2.

## 3. Design (as landed 2026-11-10)

Two premises measured FALSE before implementation, and one slice redesigned
on the measurement — recorded here because the as-filed text above is wrong
in exactly the ways §0 warns about:

- **S3 premise 1 false:** `agent_norm`/`part_type_norm` are redundant —
  `convo_search` already carries exact `agent` and `part_type` columns
  (values: bili/build/compaction/dsh/explore/general/plan/zcode); no
  normalization needed.
- **S3 premise 2 false:** `meta_json` does not exist in the index. The
  `meta` payload lives only in the parquet (74.55 MB across 290,186 rows,
  up to 1.9 MB per row) and must NEVER enter the index; the landed S3 reads
  the parquet at backfill time and projects two columns only.
- **S4 inoperative as filed:** the fused candidate pool is
  `max(limit*4, 20)` rows drawn BEFORE fusion; the best bili summary scores
  cosine 0.63–0.75 but sits at overall rank 308–37,593 (measured:
  'embed cache' → 308, 'mem mtime gate' → 4,511, 'extension system' →
  37,593), so a post-fusion ×1.5 multiplier can never change pool
  membership. A boost answers "who wins among the already-chosen" — the
  bili lane's problem is "never chosen at all".

Landed slices:

- **S1 (landed) — query surface.** `--agent`, `--part-type`,
  `--bili-active {0,1}`, `--bili-tier N`, `--summary-first` in
  `convo_query.py`. The filters are LEG-LEVEL (`RowFilters` into
  `_cos_hits` numpy+SQL legs): the candidate pool is drawn from the
  filtered population, which is the whole point — a bili summary at overall
  cosine rank 4,511 becomes rank ≤20 *within* the summary population and a
  real candidate. Post-fusion filtering (and any post-fusion boost) cannot
  do this. NULL never matches `--bili-active`/`--bili-tier` either value
  ("unspecified" ≠ "superseded"); an FTS-leg safety net post-filters because
  the sidecar has no agent/part_type. Also landed: `proxy` added to
  `HARNESSES` (83 bili rows; was an argparse error), `compression-summary`
  added to `KINDS`, `PART_PRIOR['compression-summary'] = 1.0` (stop the
  prior demoting an explicitly requested row).
- **S2 (landed) — S2a, the documented path.** Measurement: `--agent bili`
  pierces the orphan relabel directly (1,044 `orphan:opencode` bili rows
  retrievable), so the label rewrite loses nothing and S2b's corpus rewrite
  + relabel-predicate clause would buy a second name for the same rows.
  The relabel stays; the retrieval path is the agent filter.
- **S3 (landed) — two columns, in-place migration, part_id backfill.**
  `rebuild_convo_search.py`: `bili_active BOOLEAN` + `bili_tier INTEGER`
  added to the DDL, stage schema, and Arrow batch; `_bili_column_migration`
  mirrors the provenance migration (idempotent `ADD COLUMN IF NOT
  EXISTS`, no re-embed — that job is ~45 min and changes nothing else);
  `_backfill_bili` joins on `(file_path, part_id)` — the row-order
  doctrine from the provenance backfill fix, pinned by regression tests —
  reading parquet `['part_id','agent','meta']` and bucketing UPDATEs by
  (active, tier). Live run: **1,607 rows backfilled across 63 files**;
  0 non-bili rows carry values; distribution matches the §2 census.
  `_bili_columns` tolerates garbled/missing/non-object meta as
  (NULL, NULL) — corpus meta is data, not trust.
- **S4 (landed, redesigned) — partition instead of boost.**
  `--summary-first` PARTITIONS the fused ranking: compression summaries
  keep their fused order and move ahead of everything else — honest about
  what it does ("show me the summaries for this") without claiming they are
  the *closest* rows. Pinned by a unit test where the summary rows do not
  even text-match the query (FTS leg sees only the text row) yet the
  partition promotes them via the cosine leg. The rank ≤3 acceptance is met
  by construction through `--agent bili` (leg-level pool), not by a scalar.

Tests: `tests/test_bili_convo_columns.py` (24 tests: projection table,
migration idempotence, part_id-join order-independence, NULL semantics,
rf SQL fragments, row-mask NULL/absent-column semantics, leg-level agent
filter, summary partition). `tests/test_convo_search.py` stub signature
updated for the `rf` parameter. Targeted surface: 167 passed, 1 skipped.

## 4. Acceptance criteria & shakedown (as landed)

1. `convo_query.py "<question>" --agent bili --harness opencode` returns
   the relevant compression-summary row at rank ≤3 — **MET** (measured live
   on 'embed cache': bili rows at ranks 1–3 with leg-level filtering).
2. `--summary-first --limit 5` returns at most 5 rows, summaries before
   raw rows regardless of cosine tie — **MET** (partition unit test +
   live verification).
3. `--check` row count equals the §2 row-1 baseline ± 0 — baseline
   RECOUNTED: **1,607** bili rows (opencode 1,044, dsh 480, proxy 83) vs
   the as-filed 1,583 — the dsh lane ingested 24 more bili rows between
   filing and landing (§2 row 1 predates the 2026-11-07 snapshot ingest).
   Columns present with non-null values exactly where meta carries them;
   0 non-bili rows carry values (verified live).
4. `make qa` — NOT RUN before archival (the operator archived on their
   own go, before a full gate pass; the gates procedure reserves full
   gates for ONCE per arc). Targeted surface green meanwhile: 167 passed /
   1 skipped, ruff clean, markdown_lint + static_checks clean. The bili
   backfill is idempotent and additive — a re-harvest does not churn it.
5. Eval gate N/A with reason — touches query scoring/filtering, not
   rosters/crosswalks/hierarchies/extractors. Documented per the house rule.

| Outcome | As filed | As landed |
|---|---|---|
| Rank for bili summaries | ×1.5 cosine post-fusion | leg-level pool via `--agent`/`--part-type`; `--summary-first` partition |
| Filters for bili | 4 | 5 (`--agent`, `--part-type`, `--bili-active`, `--bili-tier`, `--summary-first`) + `--harness proxy` unblocked |
| Index columns | 4 (2 redundant) | 2 (`bili_active`, `bili_tier`) |

## 5. Risks

- **Schema migration (S3).** `ALTER TABLE` on a 1.6 GB FTS sidecar is
  measured at <30 s on this box (FTS5 supports online schema changes);
  fall back to a full rebuild if it fails. Test pin: rebuild finishes under
  60 s.
- **Rank boost miscalibration (S4).** A scalar chosen on 10 questions overfits;
  mitigate by holding out 2 questions from calibration and asserting they
  still pass. If the holdout fails, fall back to `--summary-first` as a
  partition-only signal (no scalar).
- **Orphan semantics (S2b).** Exempting bili from the relabel pass means
  bili summaries outlive the harness session file — true by design (the
  blocks are summaries of the session, not the session itself), but worth a
  one-line audit note in the lane's section of the memory trail.

## 6. Non-goals

Re-deriving the bili routing (§7 of the executed proposal is correct), T2/T3
shape work (no data to validate against), backfilling `bili_active`/`tier`
from payloads lacking the fields (will stay NULL — surfaced as
"unspecified" in the filter UI), and changes to the snapshot lane plumbing
(works as designed; the new 2026-11-07 mount requires only the standard
`make convo-fresh APPLY=1`, no patch).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-10 | bili row count by agent+source | 1,583 total bili rows | row-1 baseline |
| 2026-11-10 | same query, filter `source LIKE 'bili-index/%'` | 539 rows | filter #4 trap |
| 2026-11-10 | bili opencode blocks by `active` | 824/1041 inactive (79.2%) | confirms executed §3 S2 |
| 2026-11-10 | `ls /mnt/store/timeshift/snapshots/` | 8 dirs (2026-07-26 → 2026-11-07) | mounted mid-session; latest postdates corpus by 7d |
| 2026-11-10 | convo query without `--summary-first` on `mem` question | raw row at rank 1, bili summary at rank 7 | motivates S4 |
| 2026-11-10 | best bili cosine vs OVERALL rank ('embed cache' / 'mem mtime gate' / 'extension system') | 0.7514 @ 308 / 0.6348 @ 4,511 / 0.6505 @ 37,593 | S4 as filed (post-fusion ×1.5) cannot change pool membership — slice redesigned |
| 2026-11-10 | `_bili_column_migration` + `_backfill_bili` live on convo_search.duckdb | migrated: True; 1,607 rows across 63 files; 0 non-bili rows carry values; distribution matches §2 census | S3 as-landed measurement |
