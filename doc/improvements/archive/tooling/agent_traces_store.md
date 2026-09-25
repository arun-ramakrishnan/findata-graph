---
title: "Agent trace store — behavioral-telemetry DuckDB with per-harness loaders"
status: executed
filed: "2026-09-25"
executed: "2026-09-25"
completed_md: "297"
area: "bench_data/code/agent_traces.py + memory/data/agent_traces.duckdb"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Agent trace store — behavioral-telemetry DuckDB with per-harness loaders

**Date:** 2026-09-25 · **Status:** EXECUTED (completed.md #297) ·
**Area:** `bench_data/code/agent_traces.py` (new), `memory/data/agent_traces.duckdb` (new); design + decision record = `doc/local/engineering/capture_traces.md`

## 1. Motivation

The coding-agent harnesses record behavioral telemetry (latency, TTFT,
retries, error taxonomy, tool economics, agentic depth, code churn) in five
machine-local stores. None of it is mined: the existing
`model_usage.duckdb` answers only "what did I spend" at
day × provider × model grain. The trace stores are partially **rotating
away** (zcode CLI logs capped ~10 MB; 44 of 52 prime sessions already
purged post-upload; opencode deletes old messages), so the unmined signal
is actively decaying. Trigger: capture-traces inventory arc, 2026-09-25
(`doc/local/engineering/capture_traces.md` — store map, §3 insight table,
§5 store decision, §6 lane plan; that doc carries the full analysis and is
the design record for this proposal).

Governance note: `bench_data/` carries **zero git-tracked files**, so the
proposal/archival trail is the only durable record for this code — filed
per the house rule (before implementing multi-slice work).

## 2. Evidence (measured 2026-09-25, this box)

All numbers read read-only from the live stores this session
(`~/.zcode/cli/db/db.sqlite`, `~/.local/share/opencode/opencode.db`):

| Signal | Measured | Mined today? |
|---|---|---|
| zcode LLM requests (Sep 11–25) | 2,889 reqs / 18 sessions; 94% end in tool-calls | counts only |
| zcode tool calls | 3,503; Edit fails 59/661 (8.9%, stale-read guards) | ad-hoc only |
| Agentic depth | 171 turns, mean 17.4 model reqs/turn, max 142 | one-off |
| Reliability | 0 retries, 14 failed reqs (9 cancelled, 2 rate-limited) | counts only |
| opencode tool parts | 1,023; edit 188/188 clean (cross-harness A/B) | no |
| File-level edit attribution (oc `patch` parts) | 148 snapshots | no |
| Subagent/delegation lineage | prime ledger 20 spawns/17 deletes; zcode 93 subagent reqs | no |
| Process memory samples (zcode CLI logs) | 1,791 | no |
| Slowest calls | Bash max 545 s; Agent mean 258 s (parallel fan-outs) | no |

Full per-store inventory, caveats (token redaction in CLI logs, broken
trace bridge under rotation, prime `toolResult` counting trap, day-boundary
inconsistency) and the five-store table: `capture_traces.md` §2/§4/§8.

## 3. Design

**Chosen mechanism:** a second DuckDB store, `memory/data/agent_traces.duckdb`,
kept separate from `model_usage.duckdb` and joined by `ATTACH`. Schema =
`fact_model_request` / `fact_turn` / `fact_tool_call` / `fact_event` /
`fact_file_edit` / `dim_session` / `load_log` (`capture_traces.md` §5.1,
with the 2026-09-25 PK verification: zcode ids globally unique —
`turn_usage` 171/171, `tool_usage` 3,503/3,503 — so `(source, turn_id)` /
`(source, tool_call_id)` stand for loaded rows; the composite
`(source, session_id, turn_id)` applies to *derived* opencode/prime turns;
`fact_model_request` grain = one row per request attempt, keyed
`(source, request_id)` with `attempt_index` as a column).

Why separate (four reasons, §5): different contract (reconciled vs raw
event-sourced observations — mixing breaks wholesale-replace idempotency
past a ~50:1 row-count imbalance); different questions/cadence; the
`ATTACH` bridge loses nothing analytically; and DuckDB's single-writer rule
— separate files keep the two load/report cycles contention-free.

Why not embeddings: every §3 signal lives in a typed column; embeddings are
for retrieval, not analytics — deferred until a concrete retrieval question
exists (§5, Lane 5).

Slices (each independently landable; order matters):

- **S1 — store + Lane 1 zcode loader.** `bench_data/code/agent_traces.py`
  mirroring `model_analytics.py`'s CLI shape: `load zcode [--days N|--full]`
  → `fact_model_request` + `fact_turn` + `fact_tool_call` + `dim_session`
  + `load_log` from `~/.zcode/cli/db/db.sqlite` (typed SQL, zero JSON
  parsing), read-only source access, incremental window keyed on stable ids.
- **S2 — report legs (zcode-backed).** `report [--range 1w] [--json]`:
  latency p50/p95/max + TTFT per model, tool-economics table
  (calls/latency/error-rate/output-bytes per tool), agentic-depth
  distribution, retry/error taxonomy, per-turn attribution. Cross-store
  legs (`spend-per-tool-hour`) via `ATTACH model_usage.duckdb`.
- **S3 — opencode loader.** Part-level extraction (`step-finish` tokens +
  cost, `tool` latency/I-O, `patch` → `fact_file_edit`, `compaction`,
  `session_v2` churn, `event` → `fact_event`), with the **dedupe
  assertion**: zcode sessions also live in `opencode.db` — session-id
  collisions are reported in `load_log`, never silently dropped (§2.3).
- **S4 — prime-rlm loader + recovery feeds.** Sessions JSONL (assistant
  `usage`/`cost`; **missing-usage on a genuine assistant message = failed
  call** — filter `role=='assistant'` BEFORE any usage-presence statistic,
  the trap that cost one wrong finding), `rlm-ledger` → delegation lineage,
  `logs/agent.jsonl` → auth-failure mining. Reads
  `memory/data/prime_sessions_backup/` + `memory/data/telemetry_backup/`
  as inputs; refreshes the prime snapshot on a cadence faster than the
  purge.
- **S5 — deferred semantic layer.** Out of scope until a real retrieval
  question appears (§5/Lane 5). S1–S4 LANDED 2026-09-25 (same day as
  filing, operator go).

### S5 deferral rationale (recorded 2026-09-25, for future reference)

Parked, not rejected — four stacked reasons, any one sufficient:

1. **Category fit.** Every analytical signal is a typed column
   (`duration_ms`, `exit_code`, `error_type`, `tool_name`); SQL answers
   the §2 question set exactly and aggregably. Embeddings serve
   *semantic similarity over text* — they add nothing to analytics and
   would make those queries slower and fuzzier.
2. **No live consumer.** Embeddings would only enable queries like
   "find sessions where the agent struggled with DuckDB DDL". Nobody has
   asked that question. House doctrine: no serving infrastructure ahead
   of a demonstrated consumer (same principle that killed the OpenRouter
   usage source).
3. **The right home already exists.** If a retrieval question becomes
   real, the repo's search-embeddings stack (granite embedder, sectioned
   index, content-hash cache) is the substrate; the trace store would
   need only a truncated `text` column + optional embedding column on
   existing facts — not a vector store inside the analytics DB, and not
   a second copy of the embedder machinery (model pins, cache
   invalidation, HNSW availability hazards).
4. **Content is mostly noise today.** 98,685 of ~127k rows are prime
   warn/error logs and oc state-transition events (`fact_event`);
   embedding that corpus now indexes noise. The genuinely retrievable
   content (reasoning text, tool I/O) is small and its questions are
   hypothetical.

**Reactivation path:** a real retrieval question appears → add the text
column to the relevant fact table → embed via the existing stack → no
schema upheaval, no new dependency.

## 4. Acceptance criteria & shakedown

1. `agent_traces.py load zcode --days 30` creates the store and reports
   per-table row counts + a `load_log` row; a reload yields **identical
   row counts** (window delete + reinsert keyed on LOCAL day, stable-id
   PKs as backstop — the same idempotency contract as model_analytics).
2. Source stores are opened **read-only** (URI `?mode=ro` / DuckDB
   `read_only=True`) — the loaders never write to `~/.zcode`, `~/.local`,
   or `~/.prime`.
3. `report --range 1w --json` emits all S2 legs with sane numbers
   (spot-check Edit error rate ≈ 8.9% and mean requests/turn ≈ 17.4
   against §2's measured values).
4. Ruff/type gates stay green on the new module; no new dependencies
   (deptry-clean: stdlib + duckdb only).

| Projected outcome | Today | After |
|---|---|---|
| Behavioral telemetry mined | ad-hoc SQL only, lost on rotation | standing store + report legs |
| Trace coverage | decays with harness rotation | recovery feeds as loader inputs |
| Time to answer "which tool/model misbehaves" | hand-rolled SQL per question | one `report` call |

## 5. Risks

- **Source-schema drift** (harness updates rename/add columns) — loaders
  read `PRAGMA table_info` defensively; `load_log.detail` records column
  mismatches instead of failing the whole load.
- **Rotation gaps** — mitigated by recovery-feed inputs (S4) and stable-id
  windows; residual gap: zcode CLI-log spans before 2026-09-20 are already
  gone (accepted, §2.2).
- **DuckDB write contention** with the operator's own `model_analytics.py`
  runs — separate file (§5 reason 4); loaders use short transactions.

## 6. Non-goals

- No embeddings / semantic retrieval (Lane 5, deferred by decision §5).
- No changes to `model_usage.duckdb` or its loaders; no coverage-rule
  changes; no harness modification (zcode/opencode/prime are upstream
  binaries — e.g. the Edit stale-read guard finding goes upstream, not
  here).
- No scheduler/daemon: loads are operator- or session-triggered.
- No deletion/pruning of source stores.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-25 | sqlite ro: `SELECT COUNT(*), COUNT(DISTINCT turn_id) …` | 171 / 171 | turn_id globally unique → PK verified |
| 2026-09-25 | sqlite ro: tool_call_id distinctness | 3,503 / 3,503 | PK verified |
| 2026-09-25 | sqlite ro: per-tool durations/errors | Edit 59/661 fails | worst tool by error rate (8.9%) |
| 2026-09-25 | sqlite ro: top durations | Bash 545 s max; 4× exactly 420.1 s | timeout-cap cluster |
| 2026-09-25 | sqlite ro: Agent calls | 6 calls, mean 258 s, 2 parallel fan-outs | wall = max child |
| 2026-09-25 | sqlite ro: opencode tool parts | edit 188/188 completed | cross-harness A/B clean |
| 2026-09-25 | `agent_traces.py load all --full` (scratch db) | 6,676→6,700 rows | full rerun: identical counts ✓ |
| 2026-09-25 | `load all --days 2` (scratch db) | 928 rows | window replace only ✓ |
| 2026-09-25 | `report --db <scratch>` text + `--json` | all 6 legs | Edit err 8.7%, depth 16.9 reqs/turn ≈ §2 baselines ✓ |
| 2026-09-25 | `ruff check bench_data/code/agent_traces.py` | passed | markdownlint on proposal: clean |
| 2026-09-25 | first production load (default `--db`) | 6,700 rows | `memory/data/agent_traces.duckdb` live |
| 2026-09-25 | S3+S4: `load all --full` | zcode 6,762 + oc 21,914 + prime 98,879 | full rerun identical ✓; incremental 3d: 2,337 rows, counts stable ✓ |
| 2026-09-25 | oc dedupe assertion | skipped_zai_sessions=4 | provider-keyed (session ids never overlap — doc §2.3 corrected) |
| 2026-09-25 | recovery feeds status | prime backup + oc snapshots GONE | loaders keep globs; re-stage to restore coverage |
| 2026-09-25 | report legs | 10 legs text + json | S2+S4 complete incl. hotspots/ratio/lifecycle/delegation |
