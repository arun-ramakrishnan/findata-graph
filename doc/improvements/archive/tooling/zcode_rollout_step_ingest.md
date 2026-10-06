---
title: "Load zcode rollout model-I/O into fact_model_step"
status: executed
filed: "2026-10-06"
executed: "2026-10-06"
completed_md: "358"
area: "helpers/analytics/agent_traces.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Load zcode rollout model-I/O into fact_model_step

**Date:** 2026-10-06 · **Status:** PROPOSED ·
**Area:** helpers/analytics/agent_traces.py (loader) + helpers/analytics/trace_contracts.py (contract) + doc/local/engineering/capture_traces.md (reference)

## 1. Motivation

The open-sourced zcode harness now writes per-step model-I/O records to
`~/.zcode/cli/rollout/model-io-sess_*.jsonl` — one record per request
attempt carrying the normalized step response (`text`, `reasoningText`,
`toolCalls`, `usage`) plus the request's compaction shape. The trace
store (`memory/data/agent_traces.duckdb`, `helpers/analytics/agent_traces.py`)
does not read this store at all: `capture_traces.md` §2.3 recorded the
2026-09-25 decision "no report leg needs raw model request/response
bodies — revisit only for a concrete trajectory-debugging question."

That trigger is now met, and the prize is structured, not raw: three
signals the store cannot answer today — per-step **reasoning content**
(zcode `reasoning_tokens` is a provider-side structural zero,
capture_traces §9.3, so the store's `reasoning_ratio` reports 0% for
zcode while 403/680 current steps carry non-empty reasoning *text*);
per-step **tool-call arguments** (the model's intended tool inputs, 813
calls in the live window); and per-step **compaction shape**
(`messagesKind` full/delta/tail + offset — the context-lifecycle curve
currently proxies context size with peak `cache_read` only).

The load must land in a **new `fact_model_step` table**, not in
`fact_model_request`: the rollout's `requestId` space is disjoint from
`model_usage.id` (0/680 overlap, measured), so both streams describe the
same attempts under different ids — merging them into the request fact
would double-count every zcode request in every aggregate leg.

## 2. Evidence (measured 2026-10-06, this box)

Corpus: 3 files, 122 MB, 680 records, all `type: "model_io"`,
`startedAt` 2026-10-05T11:17 → 2026-10-06T07:38. The 2026-09-25 corpus
(539 records, 110.7 MB) has already rotated away — bounded retention
(`modelIoFullRetentionEnabled: false`, verified today) replaces the
corpus wholesale.

| Configuration | Result | Verdict |
|---|---|---|
| `requestId` ∩ `model_usage.id` | 0 / 680 | disjoint id spaces — never merge into `fact_model_request` |
| `sessionId` ∩ `session.id` | 3 / 3 | same sessions — join to `dim_session`/`fact_turn` on `session_id` (+`turn_id`) |
| usage rows with `input+output==total` | 673 / 673 | internally consistent; reconciles against its own total |
| usage rows with `cacheRead<=input` | 673 / 673 | inclusive input — same convention as `model_usage` (normalize `input = in − cacheRead`) |
| same-turn token/duration match vs `model_usage` | in/out tokens exact; `durationMs` 7129 vs 7188 | same grain, slightly different measurement boundary — parallel observation, not a duplicate row |
| non-empty `reasoningText` | 403 / 680 (277 null, 0 empty) | clean binary signal; p50 996 chars, max 14,069 |
| non-empty `text` | 357 / 680 (p50 142, max 5,994) | empty exactly where the step only made tool calls |
| records with ≥1 `toolCall` | 640 / 680, 813 calls `{id,name,input}` | per-step tool args exist nowhere else in the store |
| `messagesKind` full / delta / tail | 66 / 68 / 546 | only `full` carries the whole context — `tail`/`delta` are partial by design |
| `finishReason` tool-calls / stop / null | 640 / 33 / 7 | the 7 nulls are the `error` records (no usage — missing-usage-is-failure doctrine holds) |
| `error` records | 7 (`TerminalStreamChunkError: exceed quota limit` + stack) | quota-error forensics the store lacks |
| `attempt` distribution | 1×678, 2×1, 4×1 | retries exist in this stream (`model_usage.retry_count` is still 0) |
| `model.providerId` plans | `zai-individual-coding-plan` 419, `zai-start-plan` 261 (all GLM-5.3-Flash) | plan-routing dimensions ride along for free |

What the numbers mean: the rollout is a second, content-bearing
observation of the same request stream — tokens reconcile, identity
joins on `session_id`/`turn_id`, and three content signals (reasoning
text, tool args, compaction shape) have no home in the current schema.
Ruled out: treating `request.messages` as the conversation (only 66
`full` records carry it whole — this also silently degrades
`convo_union.py`'s zcode leg, which is a separate arc, see §6).

## 3. Design

New source `rollout` → new table `fact_model_step` (one row per
model-I/O attempt), `source='zcode_rollout'`, PK `(source, request_id)`.
`request_id` is harness-native (UUID per attempt), so §11 anchor
stability extends mechanically: `agent-trace:zcode-rollout#step:<request_id>`.

```sql
CREATE TABLE IF NOT EXISTS fact_model_step (
  source TEXT, request_id TEXT, session_id TEXT, turn_id TEXT, trace_id TEXT,
  attempt_index BIGINT, ts TIMESTAMP, day DATE, completed_ts TIMESTAMP,
  provider TEXT, model TEXT, query_source TEXT,
  status TEXT, finish_reason TEXT, duration_ms BIGINT,
  input BIGINT, output BIGINT, cache_read BIGINT, cache_write BIGINT,
  total_tokens BIGINT,                       -- provider-reported total (parity anchor)
  text_chars BIGINT, reasoning_chars BIGINT, tool_call_count BIGINT,
  tools_json TEXT,                            -- capped [{name, input-head}]
  text_head TEXT, reasoning_head TEXT,        -- capped heads (8 KiB), never full bodies
  messages_kind TEXT, message_offset BIGINT, message_count BIGINT,
  tool_names TEXT,                            -- offered tools (capped)
  error_name TEXT, error_message TEXT,        -- stack head only, capped
  PRIMARY KEY (source, request_id));
```

Token contract follows the store (§8 doctrine): `input` = fresh
(`inputTokens − cacheReadTokens`), so `input + cache_read` is the
served context; `total_tokens` is stored untouched as the parity
anchor. `cost_usd` is deliberately absent — spend stays in the
`mu.fact_usage` bridge. `ttft_ms` is absent — the stream has no
first-token field, NULL by design, no backfill. Raw request bodies
never enter the store (§2.3 retention decision stands).

Slices (each independently landable, in order):

- **S1 — `load rollout`** (unblocks everything): `CONTRACTS` entry in
  `trace_contracts.py` (ColumnSpec + ts/day casts); `_parse_rollout`
  in `agent_traces.py` mirroring `_parse_zcode` (glob
  `model-io-*.jsonl`, `_delete_window` on local day from `startedAt`,
  contract-validated insert, `load_log` entry); register in `LOADERS`
  (`agent_traces.py:2695`) so `load all` picks it up; `--days/--full`
  window semantics; unit tests for the parser (token normalization,
  cap enforcement, error-row mapping, window replace).
- **S2 — report legs**: reasoning-content lengths into
  `reasoning_ratio` (zcode stops reporting content-blind 0%);
  `messages_kind` transition cadence into `context_lifecycle`;
  `error_name` quota rows into `failure_forensics`/`error_taxonomy`.
  Step-grain only — no existing leg changes its source set.
- **S3 — parity + close-out**: `validation_rows` gains the
  `sum(input+cache_read+output-total_tokens)=0` invariant for
  `source='zcode_rollout'`; aggregate-leg invariance check (default
  `report` output byte-identical before/after a rollout load);
  `anchor_stability` re-run for `request_id` (two full re-ingests,
  100% expected); `capture_traces.md` §2.3 close-out + citation
  grammar extension.

Alternatives considered: (A) rollout rows into `fact_model_request`
with `source='zcode'` — rejected: disjoint ids make it silent
duplication, every aggregate leg double-counts. (B) same with
`source='zcode_rollout'` — rejected: legs read all sources by
default, and step content has no home in that schema. (C) extend
`fact_event` — rejected: span grain vs step grain, plus its NULL-ts
re-rotation history (§9.1). (D) `convo_union` only — rejected:
retrieval-shaped text blobs lose the typed token/timing columns the
legs need.

## 4. Acceptance criteria & shakedown

1. `.venv/bin/python3 helpers/analytics/agent_traces.py load rollout --full` inserts exactly the parsed record count (680 at filing; the command prints parsed vs inserted) with 0 contract rejections; an immediate re-run inserts 0 new rows (idempotent on `request_id`).
2. Token parity holds on every load: `SELECT sum(input + cache_read + output - total_tokens) FROM fact_model_step` returns 0 (S3 validation leg).
3. Aggregate-leg invariance: default `report` output is byte-identical before and after a rollout load (new source excluded from existing legs unless a leg opts in).
4. Anchor census: two `load rollout --full` runs with a `request_id`-set snapshot between them → 100% match (anchor-stability method, capture_traces §11).
5. Gates stay green: `helpers/validators/static_checks.py` rc=0, `make md-lint` 0 on touched docs, new parser tests green, existing `test_trace_contracts_*` / `test_trace_quote_*` suites untouched-green.

| Projected outcome | Today | After |
|---|---|---|
| zcode steps with reasoning-content lengths | 0 | ~403 per window (59% of steps) |
| per-step tool-call args in store | 0 | ~813 calls per window with name+input |
| compaction-shape signal (full/delta/tail+offset) | 0 | every step |
| quota-error (`TerminalStreamChunkError`) forensics | 0 | `error_name` rows on failed attempts |
| `fact_model_request` zcode row count | unchanged | unchanged (guard AC#3) |
| store size delta | — | ~680 narrow rows + capped heads (KB-class, not MB) |

## 5. Risks

- **Rotation outruns the loader** — bounded retention already replaced the 09-25 corpus wholesale. Mitigation: fold `load rollout` into the drive loop (after heavy zcode sessions); gaps are admitted in `load_log`, never hidden. Retention switch stays OFF (no unbounded growth).
- **Sensitive content in heads** — reasoning/text may carry secrets. Mitigation: 8 KiB heads, request inputs capped at 1–2 KiB, request bodies never stored; analytics-shaped, not a mirror.
- **Double-count by a future leg** — a leg that reads all sources naively would count zcode twice. Mitigation: `source='zcode_rollout'` keying + AC#3 invariance gate; any leg that opts in must say so on the leg.
- **Upstream schema drift** — the harness is open-source and moving. Mitigation: contract validation with `keep_rejected` reports unknown shapes; the Appendix field inventory pins what was measured.
- **Single-writer contention** — none new; same load contract, same file, short write.

## 6. Non-goals

Raw request/response body mirroring; enabling full retention; embeddings
over step content (Lane-5 doctrine — retrieval is the search stack's
job); cost materialization (spend stays in `mu`); TTFT backfill;
merging into `fact_model_request` (rejected by design, §2 row 1);
repairing `convo_union.py`'s zcode leg (same format change degrades it —
`d["model"]` is now a dict, `response` has no `body.choices`, tail/delta
requests are partial — but that is a separate `conversation_history`
arc; file separately if wanted). [Update 2026-10-06: filed and executed
as `zcode_conversation_ingest_repair` — the live harvester was repaired
and `convo_union.py` was retired (no callers, no artifact).]

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-06 | `ls/du ~/.zcode/cli/rollout/` | 3 files, 122 MB, mtimes Oct-05 21:55 → Oct-06 13:08 | per-session names `model-io-sess_<uuid>.jsonl` |
| 2026-10-06 | record/key census (python json scan) | 680 records, all `type=model_io`; req keys `{body,headers,messages,toolNames,messageCount,messagesKind,messageOffset[+bodyMessage* on 7]}`; resp keys `{finishReason:673,headers,modelId:673,providerMetadata:673,reasoningText:403,text:673,toolCalls:680,usage:673}` | `body.choices` gone — normalized response |
| 2026-10-06 | usage reconciliation | `input+output==total` 673/673; `cacheRead<=input` 673/673 | inclusive input, matches `model_usage` convention |
| 2026-10-06 | id-overlap vs `db.sqlite` | requestIds 0/680 in `model_usage`; sessions 3/3 in `session`; db scale 38 sessions / 7758 requests | parallel stream, disjoint ids |
| 2026-10-06 | same-turn correspondence | rollout `in 24266/out 181/dur 7129` vs db row `in 24266/out 181/dur 7188` | same grain, measurement-boundary delta |
| 2026-10-06 | content census | reasoningText non-empty 403 (p50 996, max 14,069); text non-empty 357 (p50 142, max 5,994); ≥1 toolCall 640 recs / 813 calls; kinds full 66 / delta 68 / tail 546; finish tool-calls 640 / stop 33 / null 7; attempts 1×678, 2×1, 4×1; plans individual 419 / start 261; `querySource` main_turn 677 / session_title 3 | trigger + shape for S1–S2 |
| 2026-10-06 | `grep modelIoFullRetentionEnabled ~/.zcode/v2/setting.json` | `false` | bounded mode confirmed; decision stands |
| 2026-10-06 | `rg "@register_parser\|^load_" agent_traces.py` + `trace_contracts.py` registry read | parsers are `load_<name>` assignments; `LOADERS` at `agent_traces.py:2512`; contracts at `trace_contracts.py:101`; existing tests `test_trace_contracts_s2_s4`, `test_trace_quote_s1` | S1 wire-in points verified (paths since moved to `helpers/analytics/`) |
