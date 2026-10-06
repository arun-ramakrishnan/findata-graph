---
title: "Repair zcode conversation ingest for the normalized rollout"
status: executed
filed: "2026-10-06"
executed: "2026-10-06"
completed_md: "359"
area: "helpers/maintenance/harvest_conversations.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Repair zcode conversation ingest for the normalized rollout

**Date:** 2026-10-06 · **Status:** PROPOSED ·
**Area:** helpers/maintenance/harvest_conversations.py (live harvester) + helpers/analytics/convo_union.py (retire-vs-repair)

## 1. Motivation

The open-sourced zcode harness replaced raw model-I/O bodies with a
normalized step response (`text`, `reasoningText`, `toolCalls`,
`usage`, `responseId`, `model: {modelId, providerId}`). Both zcode
conversation readers predate that shape, and each is degraded in a
different way:

- The **live harvester** (`harvest_conversations.py`, feeds
  `convo_search`) adapted halfway: it reads the normalized `text` /
  `reasoningText` correctly, but `model` falls through to the raw
  record dict. The searchable corpus now carries **14 distinct
  dict-shaped model values** over 7,670 zcode parts — model faceting
  and grouping for zcode is broken (older shapes even vary by
  role/source/variant keys).
- The **legacy union builder** (`convo_union.py`) never adapted: run
  against the live rollout it yields **396/396 NULL response texts**
  and dict-repr models, and it has no live artifact left
  (`conversation_history.duckdb` does not exist) and no callers.

Meanwhile the corpus rotates in hours (680 → 396 records, 122 → 73 MB
on 2026-10-06 alone), so anything the ingest drops is unrecoverable —
including the 7 error rows per window, whose responses carry no
`responseId` and are silently skipped by the harvester today.

## 2. Evidence (measured 2026-10-06, this box)

| Configuration | Result | Verdict |
|---|---|---|
| `load_zcode_rollout` on live rollout (396 records) | 571 parts: 396/396 NULL response texts, dict-repr models, 175 req-msgs (26 empty) | fully degraded — repair or retire |
| live corpus `harness/zcode/conversations/*.parquet` | 7,670 parts, 14 distinct `model` values, all `{"modelId": …, "providerId": …[, "role", "source", "variant"]}` shapes | model facet broken, normalize at write |
| harvest `_zcode_*` code reading | `text`/`reasoningText`/`responseId` reads match the normalized shape; `r.get("model") or d.get("model")` and `best.get("model")` land dicts; error rows lack `responseId` → skipped; `messageCount` heuristic is kind-blind (only 66/680 `full` records carry whole context) | partial degradation — S1+S2 |
| rotation same-day | sess_0c94 (301 recs, 55 MB) gone, replaced by sess_8388 (17 recs); corpus 680 → 396 recs | cadence load-bearing, note in S3 |
| `conversation_history.duckdb` | absent; zero importers of `convo_union` outside proposals | S3 retire-vs-repair is real |

What the numbers mean: the harvester's response path survived the
format change but its identity path did not, and the conversation
heuristic (`messageCount` max wins) never learned `messagesKind`.
Ruled out: blaming rotation (it only sets the cadence); fixing only
`convo_union` (no live readers — the value is in the harvest path).

## 3. Design

Normalize identity at write time; make partial context explicit;
decide the legacy builder's fate. Slices as numbered, in order:

- **S1 — harvest model normalization** (unblocks faceting): model
  resolution order `response.modelId ?? record.model.modelId ??
  session-model`, stored as a plain string; the newest-wins merge
  backfills existing zcode rows on the next `APPLY` (model string
  differs ⇒ row rewrites; text unchanged ⇒ embed cache should hit —
  measured in S3 shakedown, not assumed). Timeshift-snapshot corpora
  keep their dict models (immutable history, documented not fixed).
- **S2 — `messagesKind`-aware conversation strategy**: prefer
  `full`-kind requests for the conversation rows; mark `tail`/`delta`
  contributions partial in part meta; give `responseId`-less error
  rows synthetic ids so failures stay searchable instead of skipped.
- **S3 — `convo_union` retire-vs-repair + tests**: no callers, no
  artifact — recommended: retire (delete file, repoint the two
  proposal mentions + `capture_traces` note), keeping the repair diff
  small and reviewable if the operator disagrees; synthetic rollout
  fixtures pinning S1+S2 (dict model → string, error row without
  `responseId`, tail-marked conversation).

Alternatives considered: (A) leave degraded — rejected: model facet
stays broken and every rotation permanently narrows recovery options.
(B) query-time normalization — rejected: splits the model taxonomy
across write eras and every reader reimplements it. (C) drop zcode
from the convo corpus — rejected: zcode is the deepest harness
history; the fix is small.

## 4. Acceptance criteria & shakedown

1. `make convo-fresh APPLY=1` green; then `SELECT DISTINCT model`
   over the zcode parquet shows plain `GLM-5.3-Flash`-style strings,
   zero `{` values.
2. Synthetic harvest run over a fixture rollout dir: dict-model
   record → string row; `tail` request → `partial` meta; error record
   without `responseId` → searchable row (not skipped).
3. `convo_union` fate executed as decided (retire: file gone,
   references repointed, `make search-fresh` green; repair: NULL rate
   100% → 0% on the §2 probe).
4. Gates stay green: ruff, `ty check`, pytest (incl. new fixture
   tests), static_checks rc=0, md-lint 0; re-harvest wall time
   recorded (embed-cache behavior stated, not assumed).

| Projected outcome | Today | After |
|---|---|---|
| zcode `model` shapes in corpus | 14 dicts | 1 string per model |
| error-step searchability | skipped (no `responseId`) | searchable rows |
| conversation rows from `tail`/`delta` | silent partial | marked partial |
| `convo_union.py` | dead code, fully degraded | retired or repaired |

## 5. Risks

- **Re-harvest rewrite volume** — normalizing `model` rewrites most
  zcode rows; text is unchanged so embedding should cache-hit, but a
  full zcode re-embed costs real minutes if it misses. Mitigation:
  AC#4 records wall time; slices stay landable independently.
- **Rotation racing the backfill** — a session that rotates between
  harvests keeps its dict model forever. Mitigation: cadence (the
  `analytics-fresh` drive loop already covers the trace side;
  `convo-fresh` the conversation side); gaps admitted, not hidden.
- **Snapshot corpora frozen in the old shape** — accepted (immutable
  history); the S1 code path documents it so a future reader does not
  "fix" snapshots.
- **Kind-blind heuristic residue** — S2 prefers but cannot always
  find a `full` request (only ~10% of records); partial marking is
  honesty, not recovery.

## 6. Non-goals

Rollout analytics (the `agent_traces` S-lanes own behavior; this arc
owns conversation text); the full-retention switch (stays off);
opencode/prime harvest paths (untouched); `conversation_history.duckdb`
resurrection (retire means retire); any `mu.fact_usage` or spend
semantics.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-06 | `load_zcode_rollout` into `:memory:` over live rollout | 3 sessions, 571 parts; responses 396/396 NULL; models 2 dict-reprs | fully degraded |
| 2026-10-06 | `SELECT count(*), count(DISTINCT model)` over zcode parquet | 7,670 parts, 14 dict models | live facet breakage |
| 2026-10-06 | read `harvest_conversations.py:538-629` | normalized text reads OK; model falls to dict; error rows skipped; kind-blind best | partial degradation |
| 2026-10-06 | `wc -l` + `ls` rollout dir, twice | 680 recs/122 MB → 396 recs/73 MB same day | bounded retention live |
| 2026-10-06 | `rg "convo_union" helpers/ bench_data/ tests/ Makefile` | no functional importers | retire is on the table |
| 2026-10-06 | `make convo-fresh APPLY=1` | 441/441 harvested, 530 parts indexed (total 77,772) | pipeline green around the fix |
