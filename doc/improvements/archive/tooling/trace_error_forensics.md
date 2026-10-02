---
title: "Trace error forensics — add error_message to fact_model_request and label column dispositions"
status: executed
filed: "2026-10-02"
executed: "2026-10-02"
completed_md: "337"
area: "bench_data/code/trace_contracts.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Trace error forensics — add error_message to fact_model_request and label column dispositions

**Date:** 2026-10-02 · **Status:** EXECUTED 2026-10-02 — S1–S3 DELIVERED ·
**Area:** `bench_data/code/trace_contracts.py` (contract) +
`bench_data/code/agent_traces.py` (loader/report) + `tests/` (tracked
surface). Measurement record (machine-local):
`doc/local/engineering/capture_traces.md` §9.4.2.

Follows the two 2026-10-02 trace arcs (completed.md entries 335/336) and
closes the S11 residue they inherited.

## 1. Motivation

The S11 evidence pass (2026-09-26) attributed five never-populated
request-fact columns to "provider-absent" and left one actionable gap:
the zcode telemetry source carries an `error_message` the store schema
lacks. Upstream verification against the now open-source harness
(github.com/zai-org/ZCode, 2026-10-02; corrected table recorded in
capture_traces.md §9.4.2) revised that attribution: only reasoning (and
cache-write, in substance) are provider-absent; `retry_count` and
`context_exceeded` are harness-wired counters and `error_code` is
class-gated. Two consequences define this arc:

- `error_message` is the one genuinely missing column. It rides the same
  source row as the already-mapped `error_type`, is populated on every
  non-completed zcode request, and is absent from the store schema — so
  no join can reach it (§9.4.1 called it "the only joinable case"; the
  source-verified count of populated rows lives in §9.4.2).
- The keep-vs-drop rationale for the "dead" columns inverted: they are
  live harness instrumentation that can turn nonzero without any
  provider change, so dropping them would discard signal — but legs that
  print them unlabeled report "not extracted" as "did not happen"
  (the §9.4.1 warning, which stands under the corrected labels).

## 2. Evidence (measured 2026-10-02; live counts in capture_traces.md §9.4.2)

Attribution table, verified against upstream source (symbol + anchor
inventory in §9.4.2):

| Column | Upstream mechanism | Verdict |
|---|---|---|
| `reasoning` | `normalizeUsage()` maps `outputTokenDetails.reasoningTokens`; recorder writes only when defined | provider-absent (confirmed) |
| `cache_write` | payload carries a `cacheWriteTokens` key in 100% of usage objects — always 0 (implicit caching, reads only) | provider-absent in substance |
| `retry_count` (+`retryable`, `attempt_index`) | client-side counter: network events filtered on a retry-scheduled type | harness-wired, never fired |
| `context_exceeded` | harness failure classification (context-exceeded error OR failed-network-event reason) | harness-wired, never fired |
| `error_code` | set only for harness CoreError instances; provider failures land as `error_type` + `error_message` | class-gated, never fired |

The gap, precisely: non-completed zcode requests carry no usage payload
at all (their token columns are correctly NULL in the store), but they
DO carry `error_type` (mapped today) and `error_message` (dropped at the
schema boundary — the loader never selects it). opencode/prime sources
do not carry the field, so the column stays NULL for them by design.

| Capability | Today | After |
|---|---|---|
| error signal in the store | `error_type` only | `error_type` + full `error_message` text |
| zero-column labeling | unlabeled columns; legacy "provider-absent" wording in docs | three-way disposition printed on the leg |
| S11 residue | one open follow-up | closed |

## 3. Design

- **S1 — `error_message` column** (machine-local, ~4 code lines plus a
  cheap metadata-only migration): optional `ColumnSpec` in the
  `fact_model_request` contract; add the field to the zcode extract and
  column list beside `error_type`; `ALTER TABLE` then one full re-load
  to backfill. Parity gate: row count unchanged, populated exactly on
  the non-completed zcode rows, other columns byte-stable.
- **S2 — tracked tests + fixture** (the qa-riding slice): extend the
  synthetic zcode fixture with an error-carrying row; assert the
  mapping lands through the loader; add the mutation pair (fixture
  without the field goes RED) per the #335 S3 pattern. Synthetic only —
  tracked fixtures never embed real transcript content.
- **S3 — disposition labels** (machine-local, ~20 lines): the
  validation/failure-forensics report leg prints the three-way label per
  column — `provider-absent` / `harness-wired, none observed` /
  `class-gated` — citing §9.4.2, so no future session re-derives the
  corrected rows of that table. Housekeeping: once S1 lands, drop
  `error_message` from capture_traces.md §9.5's never-extracted list.

Alternatives considered and rejected: backfill/join for the five zero
columns (nothing at the source to join from — §9.4.1, now with the
corrected mechanism per §9.4.2); dropping the three never-fired columns
(they are live instrumentation); mapping `error_message` into
`fact_turn` too (the request-level taxonomy already covers the failure
path; revisit if a turn-level question appears).

## 4. Acceptance criteria & shakedown

1. Contract: `validate_rows` stays quiet on `error_message`
   (`required=False`) and still rejects a type mismatch when populated.
2. Reload parity: `fact_model_request` row count unchanged; the new
   column populated exactly on non-completed zcode rows; spot-check
   column set byte-stable across the reload.
3. Tests: `.venv/bin/python3 -m pytest
   tests/test_trace_contracts_s2_s4.py tests/test_trace_quote_s1.py`
   green; the mutation pair RED-proves the mapping is load-bearing.
4. Leg label check: the validation/failure-forensics leg prints the
   three-way disposition per tracked column.
5. Validation is targeted-only per `doc/procedures/gates.md` — no
   full-suite runs.

Query-visible semantics unchanged (no roster/crosswalk/extractor rule
touched) — the ontology eval gate does not apply.

| Projected outcome | Today | After |
|---|---|---|
| error diagnostics depth | error_type (class label) | + full provider/harness message text |
| dead-column semantics | implicit, mis-documented | explicit three-way disposition |
| S11 | one open follow-up | closed |

## 5. Risks

- **Migration/load ordering** — loading against an un-migrated store
  fails on the new column; mitigate: migrate-then-load in one arc step
  under single-writer discipline (check no other holder first).
- **Upstream anchor rot** — file:line anchors drift with upstream;
  anchors cite symbol names first, line numbers second (the §9.4.2
  pattern).
- **Fixture discretion** — synthetic error rows only; tracked fixtures
  embed no real transcript content (data-discretion standing rule).

## 6. Non-goals

- No backfill attempts for provider-absent columns (`reasoning`,
  `cache_write`) — nothing at the source to recover.
- No schema change to `retry_count` / `context_exceeded` / `error_code`
  / `retryable` / `attempt_index` — present and correctly wired.
- No opencode/prime `error_message` mapping (their sources lack it).
- No new harness parser; no display-union adoption (explicit non-goal
  of #335, unchanged).

## Appendix — raw measurement log (public-source verification; live counts stay in capture_traces.md §9.4.2)

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-02 | exhaustive key census over the zcode usage payloads | exactly 5 keys: inputTokens / outputTokens / totalTokens / cacheReadTokens / cacheWriteTokens | no reasoning-like key ever; supersedes the earlier 400-row sample |
| 2026-10-02 | upstream grep, `usage-observability.ts` | retryCount = count of retry-scheduled network events; contextExceeded = failure classification; `errorInfoFor` at line 391 | `recordModelUsageFact` persists the normalized usage object verbatim as the raw-usage payload |
| 2026-10-02 | upstream grep, `runner-normalization.ts` | `normalizeUsage` emits the 5-key object; `cacheWriteTokens` mapped from input-token details | payload is harness-normalized, not provider-verbatim |
| 2026-10-02 | upstream grep, `model-api-recorder.ts` | reasoning/cache-write setters conditional on key presence (lines 237/243) | absent key = provider never reported it |
| 2026-10-02 | upstream grep, `runner-stream.ts` | second retryCount producer (empty-completion retries, line 405) | counter is client-side, can fire on this harness alone |

## Execution Results — S1–S3 DELIVERED 2026-10-02

- **S1**: `ColumnSpec("error_message", "TEXT", required=False)` appended
  to the request-fact contract; zcode extract + column list extended
  beside `error_code`; `SCHEMA` gains the column for fresh stores;
  `_ensure_model_request_error_message()` migrates existing stores at
  every main() entry (ADD COLUMN is metadata-only; migrated stores carry
  it physically last — all access is name-keyed). Full zcode reload
  through the CLI (io_lock + load_log bookkeeping).
- **Parity** (live source grew during the arc, so parity is per-row on
  shared ids, not a frozen count): every pre-existing column byte-stable
  on ALL shared request_ids; 0 missing rows; 77 new live rows picked up
  (6137 → 6214); `error_message` populated exactly on the 41
  non-completed rows (23 cancelled + 18 error; error_type co-populated
  1:1); 0 completed rows populated; opencode/prime untouched and NULL.
- **S2**: fixture gains an error-carrying row mirroring the real
  no-usage-payload shape (NULL tokens, error_type + error_message set,
  error_code NULL); three new tests — optional-but-typed validation,
  round-trip through `_insert_all`, and the mapping pin
  (`_Z_REQUEST_COLS == table_cols("fact_model_request")`). Mutation
  verified: stripping the column from the extract's list turns the pin
  RED; restore byte-identical, all green. 14 passed in the file
  (52 with the sibling trace_quote suite).
- **S3**: `report --legs column_dispositions` (text + json) prints the
  §9.4.2 three-way dispositions beside live per-source nonzero counts;
  capture_traces.md §9.4.2/§9.5 annotated closed.
- **Validation**: ruff format + check clean on touched files; targeted
  tests only per `doc/procedures/gates.md`. Awaiting the operator's
  gate run + batch archival.
