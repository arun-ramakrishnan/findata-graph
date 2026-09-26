---
title: "Trace-analyzer coverage — mine the loaded columns and read the orphaned tables"
status: executed
filed: "2026-09-26"
executed: "2026-09-26"
completed_md: "303"
area: "bench_data/code/agent_traces.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Trace-analyzer coverage — mine the loaded columns and read the orphaned tables

**Date:** 2026-09-26 · **Status:** EXECUTED (S1–S10 landed; S11 resolved as
docs + one schema addition — see §6) · **completed.md #303** ·
**Area:** `bench_data/code/agent_traces.py` (report legs, `store_range`),
`memory/data/agent_traces.duckdb` (read path); evidence + store-state record =
`doc/local/engineering/capture_traces.md` §9

## 1. Motivation

The store landed with 8 tables and ~120 populated columns; the report reads
5 tables and ~35 of those columns. A schema-vs-usage census on 2026-09-26
found **39 populated columns in zero report SQL**, **two tables with no
reader at all**, and **a structural hole where plan-routing dimensions and
cost never intersect**. The most alarming instance is free: `fact_log`
carries 527 prime `ai.provider` **"provider stream failure"** rows and 1,419
"no models match pattern" warnings spanning 2026-08-27 → 09-25, and the
analyzer surfaces none of them. Everything the new legs need is *already
loaded* — S1–S4 and S6 are report-only, no loader or schema change.

## 2. Evidence (measured 2026-09-26, this box)

### 2.1 What the report reads vs what is populated

`report` queries `fact_model_request`, `fact_tool_call`, `fact_turn`,
`fact_file_edit`, `fact_event` (plus `ATTACH mu.fact_usage`). Never read:
`fact_log` (3,743 rows, 100% populated), `load_log` (20 rows).
`dim_session` is read for exactly one column (`compactions`).

Columns present in the store with **no occurrence in report SQL** (verified
by regex over the report section, not by eye):

| Table | Unused-but-populated columns |
|---|---|
| `fact_model_request` | `provider`, `variant`, `task_type`, `agent`, `mode`, `status`, `input`, `cost_usd`, `cost_basis`, `trace_id`, `span_id` |
| `fact_tool_call` | `side_effect_scope`, `read_only`, `destructive`, `approval_status`, `exit_code`, `ttf_output_ms`, `stdout_bytes`, `stderr_bytes` |
| `fact_turn` | `tool_errors`, `status`, `context_exceeded` |
| `dim_session` | `title`, `directory`, `ts_end`, `model`, `agent`, `adds`, `dels`, `files`, `input`, `output`, `reasoning`, `cache_read`, `cost_usd`, `trace_id` |
| `fact_file_edit` | `session_id`, `ts`, `snapshot_hash` |

### 2.2 The structural hole — dimensions and cost never intersect

`fact_model_request`, non-null counts per source:

| source | reqs | `provider` | `variant` | `query_source` | `cost_usd` | `reasoning` | `ttft_ms` | `trace_id` |
|---|---|---|---|---|---|---|---|---|
| zcode | 3079 | 3079 | 3079 | 3079 | **0** | **0** | 2018 | 3079 |
| opencode | 1985 | 1985 | 0 | 0 | 1985 | 429194 | 0 | 0 |
| prime | 262 | 262 | 0 | 0 | 262 | null | 0 | 0 |

zcode is the only source with plan routing (`account:zai-individual-coding-plan`
1305, `builtin:zai-coding-plan` 798, `builtin:zai-start-plan` 532,
`account:zai-start-plan` 444) and the only one with **zero** cost, so
"which plan am I burning" is unanswerable from the store. The existing
`spend_per_tool_hour` leg works around it by `ATTACH`ing `mu.fact_usage` —
which itself has no `variant`/`query_source`, and whose join hardcodes
`source = 'zcode'` tool-seconds, so opencode/prime cost is never divided by
their tool time.

`reasoning` is **0 across all 3079 zcode requests** while `variant` (the
effort knob: high 1752 / max 1316 / low 6 / disabled 5) is 100% populated —
the effort ladder currently has no outcome signal. Cause unproven: either
the provider does not report reasoning tokens, or the loader drops them. S5
must measure before designing. (Re-measured 2026-09-26 after the post-filing
reload: `reasoning` is now non-null on 3079/3079 zcode rows but all-zero —
still no usable signal; the matrix cell below shows the filing-time null
count. The 60-day reload puts the ladder at high 1752 / max 1396 / low 7 /
disabled 5, still 3160 rows.)

> **RESOLVED at execution — see §6.2.** The provider does not report reasoning
> tokens: `reasoning_tokens` is 0 in 3160/3160 source rows and 0/400 sampled
> `raw_usage_json` payloads contain any reasoning key. The loader drops
> nothing, so the effort ladder has cost and latency (materialized in S5) but
> permanently no reasoning signal. S5 additionally had to fix a **token
> convention bug** this table could not have revealed, which changes several
> §2/S8 figures.

### 2.3 Error reporting misses half the failures

`fact_tool_call.error_type` covers 90/6378 rows (1.4%), but `exit_code`
covers 3571 with **94 non-zero exits**, and `stdout_bytes`/`stderr_bytes`
are populated on the same 3571. `error_taxonomy_rows` filters
`error_type IS NOT NULL`, so it reports 90 rows and misses every
exit-code failure. (Request-level taxonomy is *not* blind: all 14
non-completed requests — 9 cancelled, 5 error — carry an `error_type`.)
Re-measured 2026-09-26 after the 60-day zcode reload: error-typed 93,
non-zero-exit 96, and the two sets are **disjoint** (union 189 = 93 + 96) —
exit-code classification doubles detected failures rather than overlapping
them.

### 2.4 Constant columns that make a leg lie

`attempt_index`, `retry_count` (both always 0), `context_exceeded` (always
False), `cache_write` (always 0), `truncated`, `destructive`,
`approval_status` (always `none`), and `fact_event.trace_id` /
`duration_ms` / `status` (0% non-null). Consequence: `model_latency` prints
a `retries` column that is structurally 0 for zcode and NULL for
opencode/prime, and `fact_event`'s 98.8%-populated `span_id` /
`parent_span_id` tree is unusable because its `trace_id` root is empty.
§4 of the design doc claims retry/context_exceeded are "stored + reported";
measured, they are stored and structurally empty.

### 2.5 Never-extracted source tables (headroom, not in S1–S4)

zcode `db.sqlite`: `session_task_link` (parent/child session, role, depth,
phase, agent_type — real delegation lineage vs the 34 prime `delegation.*`
rows we have), `workflow_run` / `workflow_activity` / `workflow_event`
(phase, `budget_spent` vs `budget_total`, `failure_json`),
`session_target` (objective, `token_budget`, `tokens_used`,
`time_used_seconds`), `todo` (status, priority),
`model_usage.error_message` / `retryable` / `raw_usage_json`,
`tool_usage.error_message` / `retryable`, `session.time_compacting` /
`time_archived`, `session_input.status_reason`. opencode.db:
`session_v2.fork_session_id` / `fork_boundary` / `resume_attempts` /
`idle_outcome` / `time_suspended`, `todo`, `instruction_entry` /
`instruction_state`.

## 3. Design

Slices are report-only unless marked; each is independently landable and
ordered by value/effort. All SQL keys on stable ids, never timestamps
(standing caveat, `capture_traces.md` §6).

- **S1 — `reliability` leg** (`fact_log`). Daily warn/error counts by
  `component` + `level`, plus the top `message` per component. Surfaces the
  527 stream failures and the 1,419 model-resolution warnings. ~15 lines;
  highest value/effort in the set. **Do this first.**
- **S2 — `spend` leg** (in-store `cost_usd`; drops the `mu.fact_usage`
  dependency for the primary view). Per-day and per-source/model cost plus
  token economics (`input`, `cache_read`, `output`, `cost_basis`). Covers
  opencode/prime, which the current leg excludes. Until S5 lands, zcode
  cost is absent in-store ($0 for the 746M-token source), so the leg MUST
  label zcode as not-materialized on the face of the output — never
  present the $0.36 opencode+prime total as "spend" (review 2026-09-26).
  Keep the existing `spend_per_tool_hour` for its zcode-only tool-hour
  denominator; fix its hardcoded `source = 'zcode'` join so
  opencode/prime tool time is included when `mu` has those days.
- **S3 — `side_effects` leg** (`fact_tool_call`). `side_effect_scope` x
  `read_only` x `destructive` x `approval_status` x error rate, by
  source/tool. The governance view the schema was designed for; the data is
  already loaded and wholly unread.
- **S4 — `load_health` leg** (`load_log`). Per-source last load, rows,
  status — a freshness footer so a stale or failed store is visible instead
  of silently reported on. Also makes the `_load_log_extra` detail string
  (`requests=… turns=… events=…`) queryable.
- **S5 — `plan_routing` leg + zcode cost materialization** (loader change;
  the one slice that is not report-only). Reuse the price table that already
  backs `mu.fact_usage.cost_usd` (`bench_data/code/zai_usage_query.py`
  `COST_RATES_PER_M`, per `capture_traces.md` §7) to fill
  `fact_model_request.cost_usd` for zcode at load time, then report
  `provider` x `variant` request share, latency, and cost. **Blocked on a
  measurement first**: establish whether zcode `reasoning` is 0 at the
  source (`turn_usage`/`model_usage` in `db.sqlite`) or 0 because of the
  loader. If the source is genuinely 0, the effort ladder reports
  latency/cost only and the doc says so.
- **S6 — `failure_forensics` leg** (`fact_tool_call`). Replace
  error-type-only classification with `exit_code` + `stdout_bytes` /
  `stderr_bytes` (2x detected failures — the 94 exit-code failures are
  disjoint from the 92 error-typed rows); failure rate by tool and by source.
- **S7 — `session_economics` leg** (`dim_session`). Cost, token split,
  churn (`adds`/`dels`/`files`), duration, compactions, `directory`,
  `title`; rank most-expensive sessions and cost-per-useful-request. Note
  `dim_session.cost_usd` totals $0.77 vs $0.36 at request level — the two
  disagree and the leg should surface both rather than pick one.
- **S8 — `overhead_tax` leg** (`query_source`). Token + cost split across
  `main_turn` (746M tok-in) / `subagent` (5.1M) / `compact` (4.6M) /
  `session_title` (3.5K): the non-work token tax. Scope note (review
  2026-09-26): `query_source` is zcode-only — the NULL bucket (opencode +
  prime, 460.2M tok-in re-measured) is larger than every labeled bucket
  except `main_turn`; the leg must either state its zcode-only scope on
  the output or show the NULL bucket as a separate unattributed row, or
  the tax denominator silently excludes half the corpus.
- **S9 — `turn_quality` leg** (`fact_turn.tool_errors`, `status`). Turns
  with >=1 tool error (21 of 2429), cancelled (12) and error (5) turns, and
  the tool-error-per-turn distribution — `agentic_depth_rows` reports
  `tool_calls` but never the error rate.
- **S10 — `store_range` correctness fix.** `store_range` spans only
  `fact_model_request` / `fact_tool_call` / `fact_turn`; it ignores
  `fact_file_edit`, `fact_event`, and `dim_session`, so `--range all` can
  under-report the true span. Add the missing tables (min/max union).
- **S11 — dead-column decisions** (schema/loader hygiene, not a leg). Either
  populate the §2.4 constants from the sources that carry them
  (`retryable`, `error_message`) or drop them from the schema; and either
  populate `fact_event.trace_id` (zcode model_usage rows carry a real
  `trace_id`, 3079/3079) or stop carrying `span_id` / `parent_span_id`,
  which are unusable without their root. **Deliberately deferred**: dropping
  columns changes the store contract, so it needs its own evidence pass.

### Non-goals

- No embeddings / semantic layer (Lane 5 of `capture_traces.md` §6 stands).
- No `fact_event` span-tree reconstruction; the event table is a rotating
  mirror (see the redaction note in `capture_traces.md` §9), and the
  delegation lineage it was meant to serve is better served by zcode
  `session_task_link` in a future slice.
- No new harness source added to the loader (S5 materializes cost into
  existing columns; §2.5 tables stay unmined until a query is concrete).

## 4. Acceptance criteria & shakedown

1. `bench_data/code/agent_traces.py report --range 30d` prints S1, S2, S3,
   S4, S5, S6, S7, S8, S9 legs; `--json` emits them all as keys, and
   `model_analytics.py`'s CLI shape is unchanged. → **met** (19 legs; text +
   `--json` clean on `2d`/`3d`/`30d`/`all`).
2. S1 surfaces the `ai.provider` error count (527 as of 2026-09-26) from
   `fact_log` without touching the loaders. → **met, exact.**
3. S2's per-day totals for zcode match `mu.fact_usage` for the same days
   once S5 lands, and disagree by a stated, explained delta before it.
   → **REWRITTEN after execution.** Equality is unachievable and was the
   wrong assertion: `mu` is the quota-wide Zhipu API, the store is the local
   zcode harness. The verified criterion is the **subset property** plus a
   stated ratio (store 0.346x `mu`, fresh-subset 9/11 days) and a stated
   day-boundary caveat. See §6.2.
4. `--range all` covers the union span including `fact_file_edit` /
   `fact_event` / `dim_session` days (S10), and no leg regresses on a
   `--range 2d` shape check. → **met, and the criterion was too weak**: the
   default start is 2026-08-27, not 2026-09-05. A second pass found
   `fact_log` and `load_log` missing from the union, so 15 days of log rows
   were still unreachable by any range. See §6.7.
5. `make qa` stays green (ruff, md-lint, types, static_checks, pytest);
   `md-lint` clean on the touched markdown. → **partly met.** `md-lint` and
   `static_checks` clean; the touched code is **git-ignored**
   (`bench_data/code/agent_traces.py`), so `make qa` cannot gate it and was
   not run. That code is verified directly instead: ruff clean plus text/JSON
   smoke across four ranges. See §6.6.
6. `capture_traces.md` §9's unused-column table is re-measured at
   completion, and the §3 "already mined" claims corrected for anything this
   proposal closes. → **met** (§9.2 census 111 populated / 90 read / 21
   unread; 29 of the original 39 read; §3 table extended; new §8 doctrine on
   token conventions).

## 5. Risks

- **Two cost sources.** `fact_model_request.cost_usd`, `dim_session.cost_usd`
  ($0.77) and `mu.fact_usage.cost_usd` already disagree. S2 must not silently
  pick a winner; state the basis (`cost_basis`) on every cost line.
- **Constant columns re-introduced by a future loader.** A leg that reports
  `retries` or `context_exceeded` is reporting zeros that mean "not
  extracted", not "did not happen". S9/S11 must label these.
- **Report leg growth.** 11 new legs is a lot of output for a human-facing
  CLI. Consider a `--legs` filter before S7 lands; not required for the
  proposal to be correct. → **Done:** `--legs` shipped with the legs; unknown
  names warn on stderr and are skipped, and the full set is still the default.
- **`fact_log` age-out.** It spans 2026-08-27 → 09-25 and will be pruned
  like everything else; S1 is a rolling view, not an archive.

## Review assessment (2026-09-26, pre-execution)

Verdict: **execute-ready.** The mechanism is real, the slice ordering is
correct, and every load-bearing evidence claim was independently
re-measured against the live store and the code on 2026-09-26.

Verified exact (re-measured, store reloaded 19:48 same day):

- `fact_log`: 3,743 rows, 527 `ai.provider` errors (08-27 → 09-25),
  1,419 model-resolver warns; zero mentions in the report section — S1's
  case stands.
- Plan-routing/cost hole: zcode 3,079 requests, `cost_usd` 0/3079,
  variant ladder 1752/1316/6/5 — §2.2 exact. Costs $0.36 (request) vs
  $0.77 (`dim_session`) — both confirmed.
- `store_range` unions exactly three tables (`agent_traces.py:834-839`);
  hardcoded `source = 'zcode'` at `:987` — S10 and the S2 join fix are
  real code defects, not speculation.
- `main_turn` 746.1M tok-in (input+cache_read) vs subagent 2.7M /
  compact 2.5M — S8's split exact. (Later found to be a *mixed-basis* split,
  not the true one; §6.2.)

Corrections applied during this review (same day, in this file):
§1 census count 15 → **39** (all 39 §2.1 columns verified populated);
§2.3/S6 multiplier 6x → **2x** (error-typed and non-zero-exit sets are
disjoint; 92/94 union 186 at filing time, **93/96 union 189** after the 60-day
reload); §2.2 reasoning re-measure note (non-null zeros
post-reload, S5 gate unchanged); S2 gained the mandatory
not-materialized label for pre-S5 zcode cost; S8 gained the NULL-bucket
scope requirement (460.2M unattributed tok-in).

Live-store drift since filing (expected, re-load at 19:48): opencode
1,985 → 2,044 requests; `fact_tool_call` 6,378 → 6,440; error-typed
90 → 92; turns 2,429 → 2,488; `load_log` 20 → 21. Acceptance numbers
should read "as of 2026-09-26"; re-measure at execution start
(acceptance criterion 6 already mandates the closing pass).

Design notes that survived scrutiny: S1-first is right (highest
value/effort — a ~15-line leg surfaces 527 silent provider failures);
quarantining the only loader change in S5 behind a measurement gate is
the correct discipline; S11's deferral of dead-column drops respects the
store contract; the non-goals hold (the `fact_event` span-tree refusal
is justified by its empty `trace_id` root, and §6's Lane-4 resolution
independently matches the earlier session's finding that the 22M-token
turn was cumulative per-request reprocessing, not a runaway).

## 6. Execution record (2026-09-26)

**S1–S10 are implemented and verified** in `bench_data/code/agent_traces.py`.
S11 is resolved as *documentation plus one schema addition* (§6.4) rather than
a data-recovery project. Only `error_message` is genuinely recoverable, and it
needs a schema change rather than a loader fix.

| Slice | Leg / change | Verified against live store |
| --- | --- | --- |
| S1 | `reliability` | 527 `ai.provider` errors, 1,419 model-resolver warns, 3,743 rows total — exact |
| S2 | `spend` + `spend_per_tool_hour` join fix | per-day/source; `cost_coverage` labels the basis; §6.3 |
| S3 | `side_effects` | 32 rows, all 4 governance columns rendered |
| S4 | `load_health` | 3 sources, 23 loads, 0 failures |
| S5 | `plan_routing` + zcode cost materialization | 3160/3160 zcode requests priced, 0 negatives, 12 plan×variant rows; §6.2 |
| S6 | `failure_forensics` | error-typed 93, non-zero-exit 96, both 0, union 189 — exact, disjoint |
| S7 | `session_economics` | request-derived cost is now complete; session cost labelled partial (§6.5) |
| S8 | `overhead_tax` | 383,519,628 / 460,185,231 / 2,686,020 / 2,542,800 / 3,160 — §6.2 changed these |
| S9 | `turn_quality` | 2,044 opencode turns, 23 with tool errors (1.13%), 12 cancelled / 5 errored zcode turns |
| S10 | `store_range` | default start moved 2026-09-11 → 2026-09-05 → **2026-08-27**; see §6.7 |
| — | `--legs` filter | unknown names warn on stderr and are skipped; valid legs still render |

Smoke-tested clean on `--range 2d`, `3d`, `30d`, `all` in both text and
`--json`; ruff clean; the ten pre-existing legs and the `source` dimensions in
`tool_economics` / `error_taxonomy` / `top_turns` are unchanged.

### 6.1 Two design corrections made during implementation

**The `not-materialized` label could not live inside the cost column.** The
obvious `CASE WHEN count(cost_usd)=0 THEN 'not-materialized' ELSE sum(...) END`
fails at runtime — DuckDB binds the branch to DOUBLE and rejects the string
(`Conversion Error: Could not convert string 'not-materialized' to DOUBLE`).
Every cost line therefore keeps `cost_usd` numeric (NULL when unmaterialized)
and carries the verdict in a separate `cost_coverage` column
(`2306/2306` vs `not-materialized`). This is *better* than the proposal's
wording: a NULL plus a coverage ratio distinguishes "no cost extracted" from
"zero requests" and "genuinely free", which a single sentinel cannot.

**S8's NULL bucket is not "unattributed" — it is a different taxonomy.**
`query_source` is zcode-only, so the 2,306 NULL rows are exactly opencode+prime
(3,079 zcode = 2,960 + 93 + 15 + 11). Those rows are *not* missing data: they
carry full cost (`2306/2306`, $0.3593). Calling them unattributed would have
implied a data gap where a taxonomy boundary exists. The bucket is now named
`(no query_source)` and a `sources` column shows its membership, so the split is
visible and the denominator stays whole.

### 6.2 S5 — the cost half landed, and it exposed a token-accounting bug first

**S5's gate question is RESOLVED: zcode reasoning is a provider-side structural
zero.** Measured directly against `~/.zcode/cli/db/db.sqlite`:

- `model_usage.reasoning_tokens`: **3160/3160 rows non-null, 1 distinct value,
  and that value is 0.**
- 0 of 400 sampled `raw_usage_json` payloads contain any key matching
  `reason`/`think` — there is no upstream field to lose.
- `min` = `max` = 0 across all 12 (provider, model, variant) combinations,
  while `output_tokens` ranges 0–12,933 on the same rows: the counter is live,
  the provider reports nothing.

**The loader drops nothing.** No loader change can recover zcode reasoning, so
`reasoning_ratio` and `plan_routing` correctly report 0% and say why.

**Materializing cost required a prerequisite fix, because the rate table prices
fresh and cached tokens separately — and the two harnesses disagree on what
`input` even means.** This was not in the proposal and is the most important
finding of the execution:

| Source | Provider's own total identity | `input` means |
| --- | --- | --- |
| zcode | `provider_total_tokens == input_tokens + output_tokens` | **inclusive** of cache reads |
| opencode | `total == input + output + reasoning + cache.read` (2672/2672, 0 exceptions) | **exclusive** |

The store wrote both into `input` + `cache_read` as if disjoint, so **every
cross-source token sum double-counted zcode's cached context** and cost
double-billed it at the uncached rate (~5.4x for glm-5.3). The tell was
already in the review's own numbers: `main_turn` 746.1M was inclusive while
`subagent` 2.7M / `compact` 2.5M were fresh-only — three buckets on two bases.

Resolution: `_Z_REQUESTS` now stores `input_tokens - cache_read_input_tokens`,
so the store has one meaning — `input` is fresh, `input + cache_read` is the
context served — and the extract is annotated so it is not "simplified" back.

Two independent invariants confirm the fix, neither constructed for it:

- **Subset invariant.** `mu`'s zai rows are the quota-wide Zhipu API, so each
  harness must be a strict subset. Treating raw `input` as fresh violated
  `mu.fresh_in >= local fresh` on **9/10** shared days (the local store claimed
  more fresh Zhipu tokens than the whole account). After normalization it holds
  **9/11**, with the misses being the documented "current day reads LOW for
  Zhipu" transient.
- **Cost ratio.** Per-day totals went from store $386.64 vs `mu` $206.75
  (**1.87x — impossible**, local spend exceeding the entire account) to store
  **$71.5712** vs `mu` **$206.7506** (**0.346x — a proper subset**).

The rate table itself was validated as the shared contract: it reproduces
`mu`'s zai cost to **$0.000000** (glm-5.3 $675.0540, glm-5.3-flash $45.0471).

**Acceptance criterion 3 is therefore satisfied in substance but not in letter,
and must be rewritten.** It said S2's zcode per-day totals should "match
`mu.fact_usage`". They cannot and must not: `mu` is the quota-wide plan API
(the union across every harness on the account), the store is the local
`db.sqlite` for the zcode harness. The correct assertion is the subset
property plus a stated ratio, not equality. Per-day figures shuffle in both
directions because the two sources use different day boundaries (API =
server-side UTC, local = machine clock).

One implementation trap worth recording: the generated cost CASE initially
emitted `... + input_tokens - cache_read_input_tokens * rate + ...`, and
SQL's left-to-right precedence turned the fresh-input term into a subtraction,
producing **negative** cost for glm-5.3. Each rate term is now individually
parenthesized.

**`plan_routing` is scoped to zcode, because `provider` means two different
things across sources.** The first implementation grouped every source with a
non-null `provider`, which silently merged two taxonomies:

| Source | What `provider` holds | `variant` | Rows |
| --- | --- | --- | --- |
| zcode | the **plan** (`account:zai-individual-coding-plan`, `builtin:zai-coding-plan`, …) | effort knob | 3160 |
| opencode / prime | the **model vendor** (`openrouter`, `opencode`, `Atria`, `Zhipu`, `Inception`) | NULL | 2306 |

A "plan routing" table containing `openrouter` under a `provider` column reads
as if a routing plan existed where none does, and it buries the plan answer
under 2,306 rows carrying neither a plan nor an effort level. This is the same
error as S8's `(no query_source)` bucket, one level up: `provider` is not a
shared dimension, it is two dimensions sharing a name. The leg now filters
`source = 'zcode'` — `variant` is zcode-only anyway — and other harnesses'
cost stays in S2's per-source totals.

The leg is also NULL-safe. Its printer formatted `cost_usd` with `:>9`, which
raises `TypeError` on any unpriced model rather than printing a gap; all
cost fields now go through `_fmt`, so a future unpriced model degrades to
`-` instead of crashing the whole report.

### 6.3 Post-S5 measurements that supersede earlier figures

| Quantity | Before (inclusive `input`) | After (normalized) |
| --- | --- | --- |
| S8 `main_turn` context | 746,070,602 | **383,519,628** |
| S8 `subagent` context | 5,098,500 | **2,686,020** |
| S8 `compact` context | 4,642,704 | **2,542,800** |
| S8 `(no query_source)` (opencode+prime) | 460,185,231 | 460,185,231 (already exclusive) |
| zcode `cost_usd` | NULL (0/3079) | **71.5713, 3160/3160 priced** |
| zcode fresh input | (not separable) | 10,019,832 |
| zcode cached | 378,731,776 | 378,731,776 |

`subagent` and `compact` barely moved because they were already fresh-only
figures — which is exactly why the mixed basis was previously invisible.

### 6.4 S11 resolved — no join, because there is nothing to join from

The question was whether to repair the dead columns by a join/backfill.
**No**, and the evidence is that the source is empty for 6 of the 7 candidates
(measured on `model_usage` / `session`, 2026-09-26):

| Column | At source | Verdict |
| --- | --- | --- |
| `reasoning_tokens` | 3160/3160 non-null, 1 distinct = 0 | provider-absent |
| `cache_creation_input_tokens` | 1 distinct = 0 | provider-absent |
| `retry_count`, `retryable`, `attempt_index` | 1 distinct = 0 | provider-absent |
| `context_exceeded` | 1 distinct = 0 | provider-absent |
| `session.summary_additions/deletions/files` | 0/20 non-null | provider-absent |
| `error_code` | 0/3160 | provider-absent |
| `error_message` | 16/3160 non-null, 6 distinct | **the one real gap** |

`error_message` is the only recoverable item, and it is a **schema addition**,
not a loader fix — the data is already selected next to `error_type`. The
remaining choice is documentation vs dropping each provider-absent column; a
leg printing one would be reporting "not extracted" as "did not happen".

`fact_event.trace_id` is a third case where a join is the wrong tool: the event
rows are opencode/prime while `model_usage.trace_id` is a **zcode** column, so
there is no shared key or source. The `span_id`/`parent_span_id` tree is
unusable by construction.

This is why deferring S11 was correct: the evidence pass it required is what
showed there is almost nothing to recover.

### 6.5 S7 had to change once cost existed

`dim_session.cost_usd` is NULL for all 20 zcode sessions (zcode's `session`
table carries no cost), so the session-level total of $0.77 silently covered
only opencode/prime while request-level reached $71.93 — a 93x gap that is pure
coverage, not disagreement. S7 now ranks by **request-derived** cost (complete
after S5) and shows the session figure beside it with a
`session_cost_coverage` label, so a partial session cost can never be read as
the session's cost.

Census caveat this exposed: population is counted **per table, not per source**.
`dim_session.adds` reads "populated" on the strength of opencode while being
NULL for every zcode session.

S7's request aggregate is keyed on `(source, session_id)`, not `session_id`
alone. Nothing guarantees a harness's id space is disjoint from another's, and
a shared id would silently merge two sessions' token and cost totals — the
failure mode is a plausible-looking number rather than an error. No collision
exists today (0 `session_id`s appear under more than one source, and 0 request
sessions are missing from `dim_session`), so this is hardening rather than a
fix, and the reported figures are unchanged. The brittle
`where.replace('day', 's.day')` that qualified the day filter is gone too: it
depended on `_day_where` returning exactly `day BETWEEN ? AND ?`, so any
future qualifier in that helper would have produced invalid SQL silently.

### 6.6 The gate mismatch on criterion 5

`bench_data/code/agent_traces.py` is **git-ignored**, so `make qa` cannot gate
it: ruff, types, deptry, static_checks and pytest all run against the tracked
tree, which contains only the markdown half of this work. Running `make qa`
would have produced a green result that says nothing about the code.

The code is therefore verified directly: `ruff check` clean on the file, and
the report exercised in both text and `--json` across `--range 2d`, `3d`,
`30d`, `all`, with every acceptance number reproduced against the live store.
That is weaker than the repo's own gate and is recorded as such — if these
loaders ever need gate coverage, the prerequisite is un-ignoring
`bench_data/code/`, which is a separate decision.

`md-lint` and `static_checks` *were* run, since both documents are tracked
(this file, then at `doc/improvements/proposals/trace_analyzer_legs.md`) or
indexed (`doc/local/engineering/capture_traces.md`, git-ignored but
lint-covered).

### 6.7 S10 was fixed twice — the criterion only asked for three tables

The acceptance criterion named `fact_file_edit` / `fact_event` / `dim_session`,
and the first fix satisfied it: the default start moved 2026-09-11 →
2026-09-05, recovering six days of prime events. But the union still omitted
the two tables that the criterion did not name, and both are day-carrying:

| Table | Date column | Span | Was in the union? |
| --- | --- | --- | --- |
| `fact_log` | `ts` (no `day`) | 2026-08-27 → 09-25, 3743 rows | **no** |
| `load_log` | `started_at` (no `day`) | 2026-09-25 → 09-26, 23 rows | **no** |

So `--range all` was still 15 days short at the front: `fact_log` holds the
527 `ai.provider` failures and 1,419 resolver warnings that S1 exists to
report, and **no range spec could reach them** — not even `all`. The default
start is now **2026-08-27**, which is `fact_log`'s earliest day.

The lesson worth keeping: S1's own acceptance numbers (527 / 1,419) were
always measured by querying `fact_log` directly, so they looked correct while
the leg that is supposed to surface them was silently truncated. A criterion
that enumerates tables will always be weaker than "every table that can date a
row", because the enumeration is written before someone checks which tables
those are. `store_range` now says so in its docstring and prefers each
table's own `day` column where one exists, since that is the same value the
legs filter on.

Re-measured after the fix: S1 still reports 527 / 1,419 over 3743 rows (the
range fix changed reachability, not the data), S6 moved to 93 / 96 / 189
because the 60-day zcode reload added 91 tool calls, and S2 / S7 / S8 are
unchanged.

## 7. Appendix — raw measurement log

Reproduce with `.venv/bin/python3` + `duckdb.connect(
"memory/data/agent_traces.duckdb", read_only=True)`:

- Column census: per-table `count(col)`, `count(DISTINCT col)`.
- Unused-column proof: regex `for col in [...]` over
  `agent_traces.py` lines 820-1062 (the report section) — 15 columns with
  zero hits.
- Dimension/cost matrix: `SELECT source, count(*), count(provider),
  count(variant), count(query_source), count(cost_usd), count(reasoning),
  count(ttft_ms), count(trace_id) FROM fact_model_request GROUP BY 1`.
- `fact_log` inventory: `SELECT component, level, count(*),
  count(DISTINCT message), min(ts), max(ts) FROM fact_log GROUP BY 1,2`.
- Tool-failure coverage: `SELECT count(*), count(error_type),
  count(exit_code), count(*) FILTER (WHERE exit_code <> 0) FROM
  fact_tool_call` → 6378 / 90 / 3571 / 94.
- `fact_turn.tokens` semantics (settles the `capture_traces.md` §6 Lane-4
  open question): the 22,056,214-token turn of 2026-09-11 is **134
  sequential model requests**, avg 324,926 tokens each, peak single-request
  `cache_read` 206,272 — i.e. 0.9% of the turn total. The turn figure is an
  **expenditure aggregate** (the harness's own `computed_total_tokens` =
  sum over the turn's requests, each re-reading the growing cached context),
  **not** a context size. Not a context overrun, and not a runaway
  artifact: it is a genuinely deep loop. Consequence for S9/S2: never print
  `fact_turn.tokens` beside a context-size metric without that label.
