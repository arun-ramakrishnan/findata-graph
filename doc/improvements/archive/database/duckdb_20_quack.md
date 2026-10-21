---
title: "duckdb_20_quack: quack server/client for shared memory/ DBs (2.0)"
status: executed
filed: "2026-10-21"
executed: "2026-10-22"
completed_md: "428"
area: "helpers/misc (ops) + helpers/core (connect path) + docs"
---

# duckdb_20_quack: quack server/client for shared memory/ DBs (2.0)

**Date:** 2026-10-21 · **Status:** EXECUTED (2026-10-22, completed.md #428) — Slice A (verification) **PASS**; Slice B (integration) wave 1 (graph) + wave 2 (convo_search) **landed and proven**; waves 3+ gated by contention

Part of the duckdb_20_* family, split from the bundled
`duckdb20_quack_variant` filing (2026-10-21). Sibling proposals:
`duckdb_20_variant.md` (VARIANT bench), `duckdb_20_sqlite_reentry.md`
(gated SQLite retirement re-entry). The executed 2.0-compat migration
(drain discipline, re-stamp, storage posture) is recorded in
`doc/improvements/completed.md` #404.

## 1. TL;DR

Quack (DuckDB's multi-process serve/attach extension) solves the one
shared-infrastructure pain the repo still has: file-mode DuckDB is
single-writer per DB, and `memory/*.duckdb` serves many OS processes.
The 2026-08-27 pilot on 1.5.5 proved the process model but was blocked
by beta write semantics; the pilot's revisit trigger #4 ("Quack ships
production, targeted v2.0") fired on 2026-10-21. The 2.0.0 re-pilot
(2026-10-21) **passed every gate** — this proposal now covers only the
integration (Slice B).

## 2. Background

### 2.1 The multi-writer pain

- "DuckDB: single-writer. A running rebuild holds the lock — `witr -f
  memory/convo_search.duckdb`; never start a second writer" (repo
  AGENTS.md).
- 2026-10-17 23:21 embed-cache loss: a concurrent bili `embed_store`
  writer starved the doc pass (proposal #410, archived).
- 2026-08-25 `make advisory` graph-algos crash class (the failure mode
  Quack's architecture removes).

### 2.2 Evidence base

- Pilot on 1.5.5 (2026-08-27), verbatim numbers:
  `doc/improvements/archive/database/duckdb_vector_search_assessment.md` §"Server
  setup" onward — process model proven (2W+1R zero errors), write
  semantics broken across the board.
- Re-pilot on 2.0.0 (2026-10-21, run 3), same doc §"Quack re-pilot —
  DuckDB 2.0.0": UPDATE/DELETE/INSERT-OR-IGNORE all work,
  BEGIN/ROLLBACK genuinely rolls; warm KNN p50 24.9 ms (2.2× better
  than the pilot); 2W×200+1R = 400/400 writes, 0 reader errors; kill
  -9 mid-burst reopens clean.

### 2.3 Slice A verdict (the gate this proposal stood behind)

**PASS — Slice B unblocked.** One new integration constraint: the
quack wire does not carry prepared-statement parameters — remote DML
must ship literal SQL (`InvalidInputException: Values were not
provided for the following parameters: 1`). Every Slice B remote
statement goes through a literal-emission layer.

**Embed-path write semantics verified over the wire (2026-10-22,
2.0.0):** `INSERT OR REPLACE` upsert is idempotent across the wire and
`BEGIN`/`INSERT OR REPLACE`/`ROLLBACK` genuinely rolls back — the two
semantics the CachedEmbed store needs, both broken in the 1.5.5 pilot.
Autocommit note: individually-executed statements commit on their own;
an explicit `COMMIT` with no open transaction raises
TransactionContext — the literal-emission layer must not sprinkle
COMMITs. (Probe harness: `$TMPDIR/opencode/quack_graph/repl_*.py`.)

### 2.4 Graph read-surface trial (2026-10-21, Slice B design input)

A/B over a throwaway copy of `memory/graph.duckdb` (48.5 MB;
production untouched): server-child `quack_serve` vs plain file, real
read classes on `e_all_und` (129,708 rows). p50 local → wire:

| class | local | wire | ratio |
|---|---|---|---|
| trivial count | 0.44 ms | 4.77 ms | 10.8× |
| point lookup | 0.93 ms | 5.84 ms | 6.3× |
| degree aggregation | 4.34 ms | 10.85 ms | 2.5× |
| recursive walk | 8.64 ms | 15.18 ms | 1.8× |
| two-hop join | 84.76 ms | 93.02 ms | 1.1× |

~4–5 ms **fixed** per-query wire overhead; the ratio collapses toward 1
as compute dominates, and every class sits ≤93 ms against multi-second
perf budgets — the wire is viable for the graph read surface.
`USE rdb` resolves unqualified SQL remotely (the enabler for a thin
`connect_shared`); attach-once costs 158–260 ms per process — the same
fresh-connection disease `graph_db_optimization` (#222) fixed for
Onager, so connect-once-per-process is a design constraint, not an
optimization. Onager-backed analytics (pagerank/betweenness/louvain)
are parked under 2.0 and were not wire-tested. Harness + full results:
`$TMPDIR/opencode/quack_graph/` (`run_trial.py`, `results.json`).

### 2.5 Write-arm trial — congress-bills bulk load (2026-10-22)

Same harness family, the dataset of the unfinished TypeDB trial
(`bench_data/congress-bills.json.gz`; 30k-bill subset = 646,897 rows):

| write class | local | wire | ratio |
|---|---|---|---|
| 1-row autocommit (embed-cache class) | 6.5 ms/row | 13.6 ms/row | 2.1× |
| 25-row transactions (upsert class) | — | 3.2 ms/row | beats local single |
| 1000-row transactions (bulk class) | 3,962 rows/s | 463 rows/s | 8.6× |

Wire probes verified `COUNT`/`SUM(weight)`/top-10 join over the wire —
the count + weight-sum verifications the TypeDB trial left OPEN, now
with timings (`probe_driver.py`/`probe_client.py`):

| probe | TypeDB 3.12.3 | quack wire p50 | duckdb local p50 |
|---|---|---|---|
| `COUNT(bills)` | 0.36 s | 6.32 ms | 0.49 ms |
| `SUM(weight)` | 0.41 s | 6.32 ms | 0.71 ms |
| top-10 join + group-by | 4.96 s | 8.13 ms | 9.74 ms |

The fixed ~5 ms wire overhead dominates trivial aggregates (count/sum
pay ~10×) and vanishes where compute dominates (top-10 join is *faster*
over the wire than this local copy — the local file carries 30k bills
vs the wire copy's 27k, and server-side execution does the scan).
local 1-row autocommit is itself fsync-bound (6.5 ms/row on this disk),
so the wire tax in the embed-cache class is ~7 ms/row and batching to
25 rows undercuts local autocommit entirely. Bulk DML over the wire is
parse/round-trip-bound — bulk loads belong server-side (or as COPY),
not across the wire. Harness: `$TMPDIR/opencode/quack_graph/`
(`write_arm.py`, `wresults.json`).

### 2.6 Operating learnings — what works, what doesn't, how to optimize

Qualitative distillation of every trial above; the numbers live in the
tables. These are the rules Slice B (and wave conversions) must follow.

**Works (validated 2.0.0):**

- **Small batched transactional writes.** Batching to ~25 rows per
  transaction amortizes the wire toll below even local autocommit —
  the upsert class (embed-store wave) is fine as-is, just batch it.
- **Server-side execution for compute-heavy queries.** The wire ships
  SQL out and only the answer back; the top-10 join *beat* local. Ship
  queries to the data, never rows to the client.
- **The 1.5.5 write-semantics gaps are gone** — UPDATE, DELETE,
  INSERT OR IGNORE, and genuine BEGIN/ROLLBACK all pass; the cache's
  idempotent-upsert pattern is legal again over the wire.
- **Concurrency without corruption.** Multi-writer + reader over the
  wire ran clean, and kill -9 mid-burst reopened without recovery
  errors — the file-mode attach-race class (embed-cache Oct-17) cannot
  occur in server mode.
- **`USE rdb` after ATTACH** makes unqualified SQL resolve remotely —
  a thin `connect_shared` needs no per-statement table rewriting.
- **Attach-once per process** (~50–260 ms) is cheap when processes are
  long-lived — the #222 connect-once lesson carries over.

**Doesn't work / bites:**

- **Fixed ~5 ms per-query wire overhead** — chatty patterns (per-row
  round-trips, trivial aggregates in a loop) pay ~10×; latency-bound
  callers must batch or move server-side.
- **Bulk DML over the wire** is literal-parse + round-trip bound
  (8.6× slower); bulk loads are a server-side or `COPY` job, full stop.
- **Prepared-statement parameters do not cross the quack wire** —
  clients must emit literal SQL. Any Slice B write path needs a
  literal-emission layer with its own escaping discipline.
- **Onager-backed analytics are not wire-runnable** (parked under
  2.0) — the graph analytics surface stays local until onager builds.
- **Mixed file+server access to one store is impossible** (the rw lock
  excludes cross-process readers) — conversion is all-or-nothing per
  store; no gradual per-leg mixing.
- **Silent file-mode fallback would resurrect the race** — when the
  discovery file says a server should be there, preflight must refuse
  loudly, not fall back.

**Optimization levers (Slice B design rules):** batch writes ≥25 rows;
run bulk inside the server child (or `COPY`); connect once per
process; keep latency-sensitive single-row legs local (their own
store) and put multi-writer stores on the wire; emit literals through
one audited helper; fail loudly on server-absent.

## 3. Slice B — quack server/client integration

Architecture (minimal, opt-in, no daemon framework) — lifecycle reuses
the proven llama-server mechanism (`helpers/misc/llama_servers.sh` +
`helpers/misc/llamacpp_health.py`), not a bespoke driver:

- `helpers/misc/quack_servers.sh` — start/stop/status/restart in the
  `llama_servers.sh` shape: setsid-detached server process (survives
  the invoking shell), stdout+stderr streamed to `$TMPDIR/<target>.log`
  with rotate-to-`.prev` on restart (failure evidence never destroyed),
  exact-PID TERM + port-free wait on stop, discovery file
  (`memory/.quack.json`: uri, token, pid, attached DBs, started-at)
  written by start and removed by stop. `make quack-serve` /
  `quack-stop` / `quack-status` targets mirror the llama Makefile
  targets.
- `helpers/misc/quack_health.py` — one-shot read-only probe in the
  `llamacpp_health.py` shape: attach over the wire from the discovery
  file, run identity + trivial-read assertions, exit 0 only when the
  served DBs answer; usable from maint / cron / operator shell.
- `helpers/core/duckdb_shared.py` — `connect_shared(path,
  read_only=True)`: if `memory/.quack.json` exists and the PID is
  alive, attach via `ATTACH 'quack:localhost' AS ...` with the token
  secret; else plain file connect. Read paths
  (`helpers/graph/query.py` read-only opens, convo retrieval,
  analytics reads) route through it.
- Writers keep file-mode and gain a preflight: refuse to open
  read-write on a served DB (discovery file alive) with a pointer to
  the server — prevents the file/server split-brain class.
- Remote statements (today only writes go over the wire; reads may
  stay local-attach) ship literal SQL per the §2.3 constraint.
- Tests: lifecycle smoke (start ephemeral server on a temp DB, client
  attach, read, insert, stop, discovery-file cleanup) marked skippable
  when the quack extension is absent; no other test depends on it.

Acceptance:

- [ ] `make quack-serve` + two parallel sessions doing read+write on
  `graph.duckdb` with zero lock errors (the 2026-08-25 crash class
  reproduced-safe).
- [ ] With no server: every existing test/file path unchanged (default
  off proven by full targeted suite green).
- [ ] `witr -f` doctrine updated: a served DB shows the server PID.
- [ ] AGENTS.md single-writer paragraph rewritten to describe the
  server mode.
- [ ] Any query-visible semantic change from routing reads through
  `connect_shared` carries an eval-gate bullet
  (`helpers/misc/ontology_eval_gate.py`, dry-run vs post-apply).

## 4. Scope guard / non-goals

- Gate conftest stays file-mode: no gate leg may require a resident
  server.
- Writers stay file-mode by default; server mode is opt-in via the
  discovery file, never the default path.
- Non-goals: cross-host serving (localhost-only per docs; TLS proxy
  out of scope), changing the embed-store consolidation decision,
  SQLite retirement (that is `duckdb_20_sqlite_reentry.md`, gated).

## 5. Verification plan

- Lifecycle smoke test + two-session concurrency demo + full targeted
  suite (default-off proven) + doc updates (AGENTS.md, and
  `doc/procedures/search.md` §locks).
- Close-out per `doc/procedures/gates.md` on the operator's go; archival
  flips status/executed/completed_md in the same change.

## 6. Slice B progress (2026-10-21)

Wired, validating:

- **Server lifecycle** — `helpers/misc/quack_server_child.py` (serves
  one primary + attached extras, absolute-path discovery json with pid,
  SIGTERM + stop-file shutdown), `helpers/misc/quack_servers.sh`
  (start/stop/status/restart; log rotate, readiness via
  `wait_healthy`), `helpers/misc/quack_health.py` (identity +
  per-alias table resolution probe), Makefile targets `quack-start /
  quack-stop / quack-status / quack-restart / quack-health`.
- **Route helper** — `helpers/core/duckdb_shared.py`:
  `connect_shared(path, read_only=True)` = wire attach when served,
  transparent file fallback when not; `assert_not_served(path)` = the
  split-brain guard (actionable error naming `make quack-stop`);
  `sql_lit`, `wait_healthy`, discovery path (`QUACK_DISCOVERY`
  overridable). `load_discovery` coerces `str` paths (found via the
  readiness-probe crash).
- **graph.duckdb reads** — `helpers/graph/query.py` RO funnel +
  warmth/staleness/fingerprint probes and
  `helpers/graph/derive_indices.py` constituents probe all route
  through `connect_shared`; extensions still install client-side.
- **Split-brain guards** — `assert_not_served` before the auto-build
  rw connect and inside `_rebuild_via_swap` (the `os.replace` swap
  would silently swap the inode under a live server — the exact
  silent-fallback class §2.6 forbids).
- **Smoke (live)** — `quack-start` → RO read served over the wire
  (`current_database=graph`, `e_all_und` 129,708 rows) → guard fires
  while served → `quack-stop` → same call falls back to file mode.
  Targeted graph suite (cache/disk/rebuild-fast-path/analytics) is
  the remaining gate for Slice B's wave 1. The 4 `TestPhase3Native`
  failures are the documented parked-external onager gap (§2.2,
  completed.md: "INSTALL onager under 2.0.0 → HTTP 404"), not a
  Slice B regression — 131 other tests pass.
- **Concurrency demo (2026-10-21, PASS)** — dedicated demo store,
  served via `QUACK_DBS`. Two cross-process wire readers + one
  cross-process wire writer, 4 s: readers `mode=wire` (quack
  extension loaded), 0 read errors, saw live growth 10→110, both
  land at 110; writer 20/20 txns × 5 rows, p50 38.3 ms/txn. While
  served: a second-process file-mode RO open is blocked by the 2.0
  rw lock (`Could not set lock on file …` — the old-world
  exclusivity the wire dissolves) and `connect_shared(…,
  read_only=False)` hits the split-brain guard. Demo gotchas folded
  into the scripts: `make quack-start PATH` passes PATH as a make
  goal (ignored) — must be `QUACK_DBS=PATH make quack-start`; and
  wire-vs-file fallback is proven by the loaded quack extension,
  not `current_database()` (alias and file stem collide).
- **Wave 2 (convo_search) wired, proof complete (2026-10-22)** —
  `connect_shared` serves+writes over the wire when the store is
  served (rw file-mode connect guard removed; the server is the
  single writer of record and small batched txns are the wire's win
  case per §2.5); `duckdb_lock.open_read_only` (the repo-wide RO
  funnel) wire-routes when served, skipping the flock queue the
  server never joins; `harvest_conversations` Store writes via
  `connect_shared` (rw flock kept for file-mode) with the two
  `executemany` lanes converted to literal-SQL executes (prepared
  parameters do not cross the wire — the one audited escaping point
  is `sql_lit`); 4 `assert_not_served` write guards in
  `rebuild_convo_search` (swap entry + both migrations +
  `rebuild(write=True)`).
- **Primary-only wire discovery → one server PER STORE
  (2026-10-22)** — restarting with two stores on one server exposed
  the wire protocol's shape: `quack_serve` exposes ONLY the primary
  database to clients. A second server-side `ATTACH` is invisible
  over the wire, and the client's attach alias is a local label
  (`current_database()` returns the alias while the rows are the
  primary's) — proven by attaching "as convo_search" and receiving
  graph content (e_all_und 129,708; convo_meta absent). Silent
  wrong-store routing, the worst failure class, so the shared-server
  topology is dead by construction. Discovery is now a JSON object
  keyed by alias, `{alias: {uri, token, pid, path, started_at}}`,
  maintained by fcntl-locked read-modify-write
  (`read_discovery_locked`/`write_discovery_locked`); the sh spawns
  one child per store on `QUACK_PORT_BASE+i` (9494, 9495, …),
  `stop` TERMs every live pid, and `connect_shared` routes by
  resolved path — verified: convo attach returns 154,681 convo rows,
  graph attach returns 129,708 edges, zero cross-talk.
- **Shadow-bind defect found + fixed (2026-10-22)** — the wave-2
  rw-over-wire smoke failed intermittently with `Authentication
  failed`. Root cause (proven via `ss`): `quack_serve` binds
  127.0.0.1:9494 with `SO_REUSEPORT`, so a second server
  **shadow-binds** the same port successfully and the kernel
  load-balances client connections between the two — each holding a
  different token and catalog. Clients could not distinguish them:
  the bare `quack:localhost` form returns a portless uri that
  resolves to the default 9494. Fix, two layers:
  `quack_server_child --port` (explicit port in the served uri and
  discovery, so routing is unambiguous) + a kernel-truth pre-flight
  (bind-probing would succeed — ss does not) that refuses to start
  when a foreign listener holds the port (`exit 2`, names the
  holder, points at `make quack-stop`). Post-fix: smoke `rw attach →
  cross-client visibility → stop → file persistence` PASS ×3
  consecutive (was ~50% flaky), and the guard verifies (foreign
  listener on 9599 → exit 2 naming the pid).
- **Wave-2 proof cycle (2026-10-22, PASS)** — served store:
  `harvest_conversations` APPLY over the wire rc=0 (corpus + literal
  watermark upserts); swap rebuild refused loudly while served
  (make rc=2, message names pid + uri); `quack-stop` →
  `make convo-search-rebuild` file-mode atomic swap rc=0 (597 s,
  fts_sync 33 s); restart → wire read of the fresh index
  (154,681 rows, +1,573 from the harvest). `make types` rc=0;
  targeted suites 17 passed.
- **Serving policy (2026-10-22)** — serve by contention, not count:
  `convo_search` + `graph` served resident (~100 MB RSS each);
  `research`/`sources` file-mode until contention shows;
  `agent_traces`/`model_usage`/`gate_runs` file forever (writers
  outside the repo). Unserved stores degrade byte-identically via
  `connect_shared`, so the served set is a dial, not a commitment.
  Remaining waves gated on demonstrated contention.
- **Onager (operator call)** — staying on 2.0; onager-served tables
  (`v_centrality_*` ×9 + `v_graph_structure`) and their consumers
  stay parked until upstream ships a 2.0.0 build.
