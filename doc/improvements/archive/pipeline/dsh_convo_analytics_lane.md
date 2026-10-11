---
title: "Cover the DeepSeek harness (dsh) in convo + analytics — session-log lane, usage loaders, trace-surface discovery"
status: executed
filed: "2026-10-11"
executed: "2026-10-11"
completed_md: "383"
area: "helpers/maintenance/harvest_conversations.py (new dsh lane), helpers/analytics/model_analytics.py + agent_traces.py (dsh loaders), memory/data/harness/dsh (new corpus), ~/.dsh/sessions (source)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Cover the DeepSeek harness (dsh) in convo + analytics

**Date:** 2026-10-11 · **Status:** PROPOSED ·
**Area:** `helpers/maintenance/harvest_conversations.py` (new dsh lane),
`helpers/analytics/{model_analytics,agent_traces}.py` (dsh loaders),
new corpus under `memory/data/harness/dsh/`, source `~/.dsh/sessions/`.

## 1. Motivation

A fourth harness is live on this box and entirely uncovered: DeepSeek
Harness (`dsh`, `Everything is a Plugin`, developer preview) is installed
(`/home/arun/.bun/bin/dsh`) with `dsh web` running and three sessions on
disk — while every convo/analytics read path here knows exactly three
harnesses (opencode, prime-rlm, zcode). The opencode v2-lane arc proved
the failure shape: an uncovered lane reports healthy by default. dsh must
not become the next one.

## 2. Evidence (measured 2026-10-11, this box, read-only)

### 2.1 Source layout

`~/.dsh/sessions/<project-slug>/session-<uuid>/` holds `session.jsonl.zstd`
(v0) and/or `session.v4.jsonl.zstd` (v4) plus a `session.lock` for live
sessions. Three sessions: two small (7 + 50 records, graph_algos
worktree) and one live agent session (2,473 records, findata-graph,
`session.lock` present — still growing).

Upstream authority (`deepseek-ai/deepseek-harness`,
`docs/session-format-status.md`): session format **v4 finalized and
released** (`dsh-v0.2.0-rc.2`); v0 historical. Machine-readable schemas
live upstream (`docs/persistence-schema.json`, `persistence-catalog.md`,
`persistence-changes/finalized/v4.json`) — the implementation arc pins
its field mapping against those, not against samples.

### 2.2 Record shape

Envelope `{type, seq, time, data}` (+ `surfaceOp`), epoch-ms, per-session
seq. v4 vocabulary on the live session (top types): `tool/ptc-dispatch`
518, `step/start` + `step/end` 258 each, `assistant/message` 256,
`tool/call` + `tool/result` 236 each, `user/message` 27, `turn/start` +
`turn/end` 22, plus `todo/write` 15, `workspace/changes` 13,
`model/selection` 9, `command/run` + `command/done` 5,
`session/title` 3, `system/message` 3. v0 carries `assistant/chunk`
(streaming fragments); v4 carries `assistant/attempt` + `assistant/message`
— two schema generations side by side, same session.

Key bodies (exact keys, truncated): `user/message.data` =
`{content[{type,text}], source{kind,rpcId}, role, id}`;
`assistant/message.data` =
`{turn, step, message{role,content[{reasoning|text}],source{kind,provider,model}}, usage{inputTokens,outputTokens,totalTokens}, stream}`;
`tool/call.data` = `{turn, step, callId, name, arguments(JSON string)}`;
`tool/result.data` =
`{turn, step, message{role:tool, toolCallId, content}, isError, id}`;
`session/title.data` = `{title, messageSeqs, source{kind}}`;
`model/selection.data` = `{provider, model}`. Usage has **no cache
split and no cost** — analytics cost stays null-basis for dsh until a
rate card exists (same posture as Zen rows).

### 2.3 The trace surface (discovery outcome)

dsh **does** support traces, three ways: the OTel plugin (`ctx.otel`,
ordinary-event + Session-log channels, consumer-owned shutdown),
session-telemetry (capture service with handoff cursors + redaction,
4 MB feedback ceiling), and session-query (live-preferred corpus + its
own SQLite FTS provider). On **this box none of them is configured**:
no `OTEL_*` env, no sqlite under `~/.dsh`, no otel/telemetry/session-query
refs in either active profile, and the web `/api` answers
`unauthorized` without a trust fence. The session logs are the only
materialized surface — so the trace half of this proposal is scoped as
discovery-gated (see S4), not build.

### 2.4 Pipeline constraints (measured, load-bearing for slicing)

- The worktree venv has **no python zstd binding** (`import zstandard`
  fails); decode today shells out to `/usr/bin/zstd`. The lane must
  `uv add zstandard` in the same change as its first import
  (dependency-gate ordering: an imported-nowhere dep reds `deptry`).
- Session dirs are **append-only while locked**: the harvest watermark
  must be `(seq, mtime, size)` like the prime/zcode file lanes, never
  seq alone — a live session grows between runs.
- One session carries **both** v0 and v4 logs (50 vs 47 records,
  overlapping content). v4 is the lane; v0 is fallback-or-ignore —
  but the overlap needs a measured dedupe rule (by `(session, seq,
  type)` or v4-only), not an assumption. Open measurement for the
  implementation arc.

## 3. Design

**Chosen:** a `dsh` harness beside the existing three: harvester lane
over the session logs + usage loaders + drift/check integration. New
harness directory keeps every existing facet (harness, role, model,
part_type) intact; no shared code changes shape.

- **S1 — dsh harvester lane.** New `_harvest_dsh()` reading
  `~/.dsh/sessions/*/*/session.v4.jsonl.zstd` (v0 only where v4 is
  absent): `user/message` → one text row; `assistant/message` → one row
  per content block (`reasoning`/`text`; `tool/call` + `tool/result`
  join on `callId` → one `tool` row with result text and
  `{status, tool, input}` meta); `session/title` → session metadata,
  not a row; `todo/write` + `workspace/changes` → meta-only rows for
  the edits facet or skip with a stated reason. part_id =
  `{session}:{seq}` (stable across runs — the log is append-only).
  Watermark `(seq, mtime, size)` per session file; live (locked) files
  re-read the tail. Sources: live dir only (timeshift coverage of
  `~/.dsh` unverified — record the backup gap, do not silently assume
  it).
- **S2 — analytics loaders.** `model_analytics`: per-`assistant/message`
  `usage` → `fact_usage` (`inputTokens` → fresh_in, `outputTokens` →
  output, no cache columns, cost 0 basis `null`); provider map gains
  `zai-coding-cn` → Zhipu. `agent_traces`: request grain per
  `assistant/message` (turn/step from the envelope), tool grain per
  `tool/call`+`result` join (duration from the two `time`s, error from
  `isError`), turn grain from `turn/start`+`turn/end`. Sessions with no
  usage block keep the `missing_usage` convention.
- **S3 — drift/check + backfill.** `_drift()` gains a dsh section on
  the same watermark; first APPLY backfills the 3 sessions; `Store`
  registry under harness `dsh` (no id overlap possible — `session-*`
  uuids vs `ses_`/`sess_` prefixes, assert it in a test).
- **S4 — trace-surface discovery (gated, not build).** If and when the
  operator configures an OTel exporter or the session-query index
  materializes, scope a second proposal for the exporter-fed lane
  (fact tables already fit: request/turn/tool grains). Until then the
  session logs are the whole source — say so in the docs, do not build
  against an unconfigured surface.

Alternatives considered:

- **Read v0 logs primarily.** Rejected: v0 is historical upstream;
  `assistant/chunk` fragments need reassembly the v4 `message` rows
  already did.
- **Wait for an OTel exporter and skip the logs.** Rejected: nothing
  is configured, and the logs are complete today (usage included).
- **Fold dsh into the opencode lane.** Rejected: different envelope,
  id space, and growth pattern (locked append-only files, not sqlite)
  — a fourth harness, not a fourth table.

Slices are independently landable in S1 → S2 → S3 order; S4 is a future
proposal, not a slice here.

## 4. Acceptance criteria & shakedown

1. `make convo-fresh APPLY=1` writes `memory/data/harness/dsh/
   conversations/*.parquet` (3 sessions); a second run is quiet;
   `--check` is FRESH.
2. `convo_query.py --harness dsh` returns hits with non-empty role and
   plain-string model; restored-session-style spot check: the
   "model name" exchange is rank-1 for its query.
3. `model_analytics.py load dsh` (new source name) puts rows in
   `fact_usage`; `agent_traces.py load dsh` fills request/turn/tool
   grains; `make analytics-fresh` (check) exits 0.
4. Hermetic tests: fixture zstd logs (writer: python `zstandard` is
   absent — fixtures may shell out to `/usr/bin/zstd` or store raw
   JSONL with a `.zstd` parse seam) covering v4 mapping, v0/v4 overlap
   dedupe, watermark advance on append, and the harness-prefix
   no-overlap assertion.
5. Eval gate — corpus content is query-visible, so parent-vs-candidate
   `convo_query` count/coverage diff over the dsh sessions between
   dry-run and canonical apply (ontology question set covers
   entity/graph ontology and is unaffected — rationale recorded, same
   posture as completed.md #382).
6. `make qa` legs covering the change (lint, md-lint, types,
   static-checks, deptry) green individually; full gates run once per
   the implementation arc's own baseline (gates.md).

**Shakedown.** No operator hands: no unmounts, no restarts. Wall-clock
is one harvest + one embed pass over ~2,500 records (seconds-class).

## 5. Appendix — raw measurements

Read-only, 2026-10-11, `~/.dsh` (147 MB, mostly profiles/node_modules):

```text
sessions              3 (2 graph_algos worktree, 1 findata-graph live+locked)
records               7 / 50+47 (v0+v4 overlap) / 2,473
formats               v0 historical + v4 finalized+released (dsh-v0.2.0-rc.2)
envelope              {type, seq, time(ms), data} + surfaceOp
usage                 assistant/message.data.usage = {inputTokens, outputTokens,
                      totalTokens} — no cache split, no cost
otel/telemetry        supported upstream, unconfigured here (no env, no sqlite,
                      no profile refs, /api unauthorized) — logs only
toolchain             no python zstandard module (use /usr/bin/zstd or uv add
                      with first import); dsh 2.0.26-era CLI: dsh web --port
```
