---
title: "db_scaling_tuning: sqlite cache + duckdb threads/connection reuse for 2x headroom"
status: proposed
filed: "2026-10-29"
completed_md: null
executed: null
area: "helpers/core (PRAGMA path), helpers/graph/onager.py (DuckDB connect path), doc/local"
---

# db_scaling_tuning: sqlite/duckdb tuning for 2x scale — proposal

**Date:** 2026-10-29 · **Status:** PROPOSED
**Rationale:** repo DB footprint growing; 2x scaling target requires tightening two under-tuned levers before considering infra swaps. Postgres remains a documented anti-recommendation (git 4e1ec8f322): "contradicts the embedded, git-shippable design." Both engines already serve without a server (serverless model dead — Quack/Slice B); scaling is a tuning problem, not a topology problem.

**Follows:** duckdb_20_quack.md (#428), duckdb_20_sqlite_reentry.md, DuckDB 2.0 VARIANT deferred 2026-10-25. **See also:** doc/local/engineering/rust_database.md §5 SQLite access lanes, graph_db_optimization.md graph compute cost table, helpers/graph/onager.py.

---

## 1. TL;DR

Two verified, low-risk changes close the 2x headroom gap without touching query-visible semantics (no eval-gate needed — infra/perf only):

1. **DuckDB threads=4 + explicit memory_limit in connect_kwargs()** (helpers/graph/onager.py:168). Biggest compute lever: graph algos currently run single-threaded.
2. **Cache the Onager DuckDB connection** instead of rebuilding per call (onager.py:180 _prepare). Per-call duckdb.connect + LOAD churn is the second biggest lever.
3. **SQLite cache_size 80MB -> 256MB** (helpers/core/db.py:16, env-guarded). 80MB no longer covers hot indexes on a 351MB DB.

Expected: graph algos ~4x faster; SQLite reads 2-3x deeper cache hits; no Postgres, no schema changes, embedded/git-shippable preserved.

**Addendum 2026-10-29:** §3.1's explicit `threads`/`memory_limit` was declined at
implementation — the landed `_get_onager_connection()` keeps DuckDB 2.0 all-core
defaults (helpers/graph/onager.py, "NO threads= / memory_limit=" comment). §3.2 conn
cache + §3.3 cache bump landed same day in the `db_tuning` patch. Three further
trial-first levers (FTS5 optimize, preserve_insertion_order, orjson) added in §6.

---

## 2. Evidence (verified)

### 2.1 File sizes (present)
research.db 351M, embed_store.db 445M, graph.duckdb 80M, convo_search_fts.db 317M, corpus.db 55M. At 2x scale research.db ~700MB; current 80MB SQLite page cache (below) won't span hot index set.

### 2.2 SQLite tuned pragmas already applied (helpers/core/db.py:198-216)
PRAGMA journal_mode=WAL; journal_size_limit=64MB; cache_size=-20000 (80MB) <-- GAP; mmap_size=256MB; temp_store=MEMORY; synchronous=NORMAL; wal_autocheckpoint=1000; busy_timeout=5000. close_connection runs PRAGMA optimize (completed.md #128). pragma doc-comment claims "fits entire DB + indexes in memory" — at 351MB already exceeded by cache_size alone (80MB).

### 2.3 DuckDB single-threaded per call (helpers/graph/onager.py:168-203)
connect_kwargs returns unsigned_ext_kwargs() with NO threads= and NO memory_limit=. DuckDB default threads=1. _prepare() does duckdb.connect(**kw) + LOAD sqlite + LOAD onager + LOAD vss every call. perf_skills.md:548 confirms host: 4P0 cores, no HT -> threads=4 safe ceiling. graph_db_optimization.md perf table: graph algos dominate runtime (~1.66s of 16 internal _duckdb.execute calls, single-threaded). Quack Slice B (#428, 2026-10-22) made DuckDB shared compute for graph + convo_search.

### 2.4 Maintenance already scheduled (helpers/maintenance/db_maint.py)
VACUUM/ANALYZE/REINDEX (S4, db_maint.py:869-926) refreshes sqlite_stat1 planner stats; PRAGMA optimize on close. Stats healthy — gap is cache capacity, not staleness.

### 2.5 Postgres explicitly rejected
git_query("Postgres pgvector") -> 3 commits, all rejection:
> PostgreSQL/pgvector: contradicts the embedded, git-shippable design.
- git 4e1ec8f322 (2026-09-14) doc/design/graph_design.md ANTI-RECOMMENDATIONS
- git 03743294bf (2026-08-17)
- git e3147ef3c0 (2026-10-25) — postgres/mysql/odbc: unused today
Only migration debated was sqlite -> duckdb (conv 2026-10-21); landed (embedded split: SQLite=search, DuckDB=set-SQL).

---

## 3. Proposed changes

### 3.1 DuckDB threads + memory cap (highest leverage)
File: helpers/graph/onager.py — connect_kwargs()

```python
def connect_kwargs(read_only=False):
    threads = int(os.environ.get("FINDATA_DUCKDB_THREADS", "4"))  # perf_skills.md:548 (4P0 no HT)
    mem_limit = os.environ.get("FINDATA_DUCKDB_MEM_LIMIT", "8GB")  # explicit vs DuckDB auto ~50%RAM
    kw = unsigned_ext_kwargs() | {"threads": threads, "memory_limit": mem_limit}
    if read_only:
        kw["read_only"] = True
    return kw
```

Pins parallelism to 4-core ceiling; explicit memory_limit prevents DuckDB auto-cap surprise. Env-overridable for bench sweeps.

### 3.2 Cache the Onager DuckDB connection (second lever)
File: helpers/graph/onager.py — `_prepare()`/module init
Module-level `get_onager_connection()` lazily built once, shared across `closeness_centrality`/`pagerank`/`eigenvector`/`betweenness`. Drop per-`_prepare` LOAD onager (keep LOAD sqlite — ATTACH still per-graph). Temp tables `_onager_int`/`_onager_e` could be cached too (fix-sketch in proposal #222 — out of scope for S1).

### 3.3 SQLite cache bump (third lever)
File: helpers/core/db.py:16

```python
# was: PRAGMA cache_size = -20000  (80 MiB)
cache_mb = int(os.environ.get("FINDATA_SQLITE_CACHE_MB", "256"))
conn.execute(f"PRAGMA cache_size = {-cache_mb * 256}")
```

Env-guarded so CI/low-RAM can dial back. 256MB ~1% of RAM on 32GB+ host, pays in mmap-fault reduction at 2x.

### 3.4 Out of scope (explicit)
- Postgres/pgvector — anti-recommendation, no design path.
- SQL index additions on graph.duckdb — write-once cache, 0 indexes by design (db_estate_analysis.md:61); adding any is a write perf regression.
- FK enforcement under DuckDB — port cost documented (duckdb_20_sqlite_reentry.md §3.4); 2-hop entities-rooted FK rewrites scale fine.

---

## 4. Acceptance criteria (bench-gated, non-blocking)
Gates per doc/procedures/gates.md: perf-only -> advisory gate (no qa/integration fail-block unless benchmark regresses).

| criterion | verify | target |
|---|---|---|
| graph algo runtime | make bench graph algos (Onager) | <=0.6s for pagerank/eigenvector/betweenness (from 2.0s) |
| DuckDB threads pinned | duckdb.query("SELECT current_setting('threads')") | == 4 |
| SQLite cache | PRAGMA cache_size on fresh conn via db.py | -65536 (256 MiB) |
| no conn churn | trace Onager duckdb.connect calls per graph_metrics | 1 conn/process (was N algos) |
| 2x scale sim | duplicate graph_edges x2 | same runtime, no DuckDB spill |

Bench lane: helpers/bench/ graph-algo bench (reuse Onager cost table). Roll-back via env knobs FINDATA_DUCKDB_THREADS=1 / FINDATA_SQLITE_CACHE_MB=80.

---

## 5. Rollback / risk
- All knobs env-overridable — no behavioral default change without env var; CI keeps threads=1/cache=80.
- DuckDB conn cache is per-process; safe under single-writer-per-memory/*.duckdb (Quack dead, file mode only).
- Snapshot first per snapshot_parallel_and_compressed_backups.md D2; dsh-undo-savepoint mount re-ensured on config touch.
- Risk: none structural. Lowest-risk order: 3.1 threads -> 3.2 conn cache -> 3.3 cache bump, each independently bench-gated.

---

## 6. Addendum 2026-10-29 — three trial-first follow-up levers

From the post-landing performance sweep (session summary + 2026-10-26 rust-ports
survey in harness memory): the Python compute side is measured-saturated — numba is
Phase-C-gated (>=~500k rows), HNSW/quant closed, jemalloc residents-only, pyopencl
tiered behind numpy. Three DB/IO levers remain untried. All three are
bench-before-wire; none adds a runtime dep to helpers/ without a green trial.

### 6.1 FTS5 segment maintenance (`optimize`) — zero current usage
**TRIALED 2026-10-29 — MEASURED-NOT-ADOPTED.** Scratch trial on a backup-API
snapshot of `convo_search_fts.db` (337MB, 175,920 rows; artifacts
`$TMPDIR/fts5_optimize_trial/`): `INSERT INTO convo_fts(convo_fts) VALUES('optimize')`
merged 8 segments → 1 (convo_fts_idx shadow-table census: segid 8→1, idx rows
8617→4899, data blocks 18984→18329) in a 2.7s optimize wall — and bought ZERO
query-latency change on a 12-query real-shaped set (best-of-3: rare terms
3.3→3.2ms, common 56.9→56.8ms, phrase 12.0→11.8ms; sum 199→197ms), with file
size 337→340MB. The premise is refuted on the live index: FTS5 automerge
already holds the segment count near-flat (8 segments over the whole corpus
of incremental DELETE+INSERT harvests), so there is no accumulated merge cost
to reclaim. Wiring `optimize` into every harvest APPLY would spend ~3s/APLY
for nothing. Diagnostic note: the fts5vocab 'segment'/'segdir' table types DO
NOT EXIST in any modern SQLite — verified absent on 3.46.1 (system), 3.50.4
(venv), 3.51.1 (pysqlite3-binary) and 3.54.1 (apsw, latest; probe packages
installed then removed, nothing imports them) — they date to the pre-3.25
%_segdir/%_segments layout. The segment census route on modern SQLite is the
`%_idx` shadow table (`SELECT COUNT(DISTINCT segid) FROM <fts>_idx`), which is
what this trial used. Revisit trigger: rare-term latency degradation or
unbounded segment growth (automerge churn / a future harvest that disables
it) — the trial recipe is cheap (backup-API snapshot + the 12-query set,
~2 min).
Evidence: `rg "optimize|automerge" helpers/` -> no hits. `convo_search_fts.db`
(317MB) is maintained incrementally — stale-row DELETE + INSERT per harvest
(rebuild_convo_search.py :432/:443/:447) — so FTS5 segments accumulate and every
convo query leg pays segment-merge cost at query time.
Change: `INSERT INTO convo_fts(convo_fts) VALUES('optimize')` after harvest batches
in rebuild_convo_search.py, plus a periodic full optimize in db_maint.py next to
the VACUUM/ANALYZE/REINDEX block (S4, db_maint.py:869-926). doc/note/script/git
FTS DBs get the same treatment if the convo trial pays.
Trial: copy the FTS DB to $TMPDIR; measure segment count (fts5vocab) + fixed
query-set latency + DB size before/after optimize. Single-writer rules apply.
Risk: low — one-time compact pass; write cost lands on the harvest leg that
created the segments.

### 6.2 DuckDB `preserve_insertion_order=false` on bulk-write legs
**TRIALED 2026-10-29 — MEASURED-NOT-ADOPTED.** A/B via a connect-patching
driver (`SET preserve_insertion_order=false` at every `duckdb.connect`,
artifacts + `run_leg.py` in `$TMPDIR/pio_trial/`), scratch outputs only,
interleaved reps with run-order control:
- snapshot parquet export (`snapshot_db.py --format parquet`, scratch
  `--parquet-dir`): apparent 10-15% win in stock-first ordering VANISHED
  under position control — first-run-of-pair ≈10.2s regardless of mode,
  second-run ≈8.5-8.8s (warm-up bias). Mechanically confirmed: the leg is
  sqlite3+pyarrow-bound (65MB SQLite side, threaded workers); the duckdb COPY
  share pio could affect is ~0.2s (34 tables / 44.6MB, logged 0.2s). Output
  parity: byte totals identical, content equal, row order stable across pio
  runs AND vs stock on the tested tables (entities/e_belongs/e_comention).
- graph-rebuild (`query.py rebuild --duckdb <scratch>`): NO difference
  (warm 2.2 vs 2.3s) — compute-bound SQL derivation.
- agent_traces zcode full load (`load zcode --full --db <scratch>`): NO
  difference (1.8/2.0/2.3 vs 2.0/1.8/2.0s) — parse-bound (81MB JSONL).
No writing leg is write-bound today, so the config buys nothing; unspecified
scan order would also sit under the git-tracked diff-stable parquet mirror
(order currently stable, but that is an implementation detail, not a
contract). Revisit trigger: a genuinely write-bound bulk duckdb leg (e.g.
large INSERT...SELECT loads at 2x scale) — re-trial per-leg with the connect
patch.
Evidence: `rg preserve_insertion_order` -> no usage. DuckDB preserves insertion
order by default, which serializes portions of large writes.
Change: per-connection opt-in config in helpers/core/duckdb_shared.py, applied only
to the writing legs (snapshot_db parquet export, graph-rebuild, agent_traces
harvest); read/query legs keep defaults.
Trial: wall-time the three write legs on scratch copies before/after; confirm no
consumer relies on raw scan order (write-once caches + column-keyed parquet
exports today).
Risk: scan order unspecified for SELECTs without ORDER BY — gate on a grep for
order-dependent readers before promoting.

### 6.3 orjson for JSONL-heavy maintenance legs (dep decision)
Evidence: no orjson/msgspec/polars in the venv (pyproject carries pyarrow only).
Stdlib json is correct for the markdown pipeline; the harness-corpus legs parse
the session-JSONL corpus (81MB/pass live and growing monotonically) —
harvest_conversations.py (22 json.loads sites), helpers/analytics/agent_traces.py
(12), the convo leg.
Trial 2026-10-29: microbench stdlib json vs orjson on real session JSONLs with
harvest's access pattern (per-line loads + field probes). Original decision rule
(single-leg lens): propose the dep only if parse share of harvest-leg wall >=~20%
AND orjson >=2x on the real corpus; else close measured-not-adopted (numkong
precedent). Measured masses: live prime sessions 4.6MB + live zcode model-io
76MB = 81MB/pass (snapshot generations on the timeshift mount mirror live
content — live lanes are the representative basis; corpus grows monotonically).

Wheel datum: orjson 3.13.0 installs from a prebuilt cp314 wheel (uv, 67ms) — no
source build. Venv status: installed as a TRIAL-ONLY dep (nothing in helpers/
imports it; deptry-inert; numba/numkong precedent) — promoted to a real
pyproject dep at S0 if the arc goes.
First pass (single-leg lens): check-mode parse share measured ~2% of the ~31s
warm `--check` wall (cProfile: JSON absent from top cumulative frames) — the
original rule read NOT-ADOPTED on that basis. SUPERSEDED same day by operator
correction: the lens was one leg in check mode; JSON is a repo-wide surface
(942 loads/dumps sites across 217 files, 119 files importing json — graph 111,
maintenance 101, misc 91, core 49, analytics 35, validators 34) and the
decision axis is CPU+memory footprint across every JSON-heavy leg, not one
leg's wall share. LEVER REOPENED as a repo-wide JSON-engine decision.

Footprint bench (apply-shaped parse, live mass lanes prime 4.6MB + zcode
model-io 76MB = 81MB/pass, harvest probes, 2 reps,
$TMPDIR/orjson_trial/bench_footprint.py):

  engine        wall      throughput    peak RSS
  stdlib        ~800ms    100-102 MB/s  37.1-37.2MB
  orjson text   ~515ms    154-161 MB/s  40.8-40.9MB
  orjson bin    ~325ms    229-274 MB/s  28.4-28.9MB

Readings: 1.55-1.6x text, 2.5-2.7x binary on wall/CPU; peak RSS -22% ONLY via
binary reads (text mode is +10% — orjson re-encodes str input to bytes before
parsing). Both the memory win and the larger CPU win require the `open "rb"`
read path at the mass-lane sites.

Bench history (artifacts in $TMPDIR/orjson_trial/): bench.py ran first —
harvest-shaped probes over `open(errors="replace")` text: 1.48-1.53x text,
2.54-2.60x binary; pure-parse rows stdlib 132 MB/s vs orjson 202 MB/s text /
336 MB/s binary on the 76MB zcode file. bench_footprint.py (broader probe set
across message/data/response shapes, adds peak-RSS via ru_maxrss) produced the
table above; the two agree within rep variance. harvest_check.prof + prof.out
hold the cProfile behind §6.4 (58s instrumented warm --check).

Adoption design (awaiting arc go):
- S0: promote orjson to a real pyproject dep; add helpers/core/jsonio.py shim —
  `loads = orjson.loads` (API-identical incl. JSONDecodeError), `dumps(obj)`
  returning str via .decode() for str-expecting callers, explicit bytes
  fast path where callers accept bytes.
  **S0 LANDED 2026-10-29:** orjson 3.13.0 declared in [project].dependencies
  (bare, unpinned convention); helpers/core/jsonio.py exposes
  loads / dumps / dumps_bytes / JSONDecodeError (orjson.JSONDecodeError is a
  DISTINCT class from stdlib json.JSONDecodeError — converted sites catching
  decode errors must catch jsonio.JSONDecodeError; both ValueError
  subclasses). dumps honours sort_keys via OPT_SORT_KEYS and indent=2 via
  OPT_INDENT_2 (other indents fall back to stdlib); output is orjson's
  canonical compact UTF-8 form, byte-different from stdlib. Drift vs stdlib
  pinned by tests/test_jsonio.py (11 green: NaN/Infinity refused where
  stdlib emits, compact separators, non-ASCII unescaped). ruff/ty clean;
  deptry DEP002 markupsafe pre-existing (unrelated). uv.lock gitignored —
  venv already carried the trial install, no lock trail required.
- S1: convert mass lanes only — harvest_conversations, agent_traces,
  model_analytics (loads + `rb` reads), then gate_query/review_scan dumps
  sites. Sub-KB config loads stay stdlib (churn without measurable footprint).
  **S1 LANDED 2026-10-29:** all 40 `json.loads` sites across the three mass
  lanes moved to `jsonio.loads` (harvest 22, agent_traces 12, model_analytics
  5 — the 6th, models.json config, reverted to stdlib per the sub-KB rule);
  all companion `except json.JSONDecodeError` nets moved to
  `jsonio.JSONDecodeError` (tuple sites included; the two bare-ValueError
  nets already catch it as a subclass). Mass read paths converted to bytes:
  harvest `_harvest_prime_file` + the responses lane (`open(f, "rb")`),
  agent_traces model-io rollup (`open(f, "rb")`), model_analytics all four
  `read_text(errors="replace").splitlines()` lanes → `read_bytes().
  splitlines()` (3 model-io legs + command-journal). The `errors="replace"`
  drift is now: invalid-UTF-8 lines raise inside `jsonio.loads` and hit the
  existing per-line skip — real-corpus smoke over prime sessions, daemon
  journals and zcode model-io files parsed 100% (zero skips; 14MB/71-line
  model-io file at 159 MB/s end-to-end incl. read+split). Dumps side
  untouched in all three files: watermarks/caps/meta are stored-JSON columns
  (§6.3 out-of-scope rule), the two `sort_keys` writers and both indent
  stdout exports stay stdlib. **gate_query/review_scan dumps: EVALUATED,
  NOT CONVERTED** — every site is either a stored `metadata_json` column
  (out-of-scope by §6.3), a `sort_keys=True` diff-stable writer (guardrail),
  or a str-consumer CLI/print path; no bytes-accepting consumer exists, so
  the conversion is churn without a measured win (reports are KB-scale).
  Targeted tests 140 passed / 1 pre-existing skip (harvest ×4, agent_traces,
  model_analytics, trace_contracts, convo_provenance); ruff + ty clean.

**Conversion-criteria table — where orjson applies and where it is limited**
(operator-requested record 2026-10-30; the 1.55-1.6x text figure is a PARSE
number measured on the 81MB/pass mass lanes — site class, not global truth,
decides conversion):

| site class | verdict | criterion / blocker |
|---|---|---|
| mass-lane parse (MB-scale, str or rb) | CONVERT (S1 done) | 1.55-1.6x text / 2.5-2.7x rb — measurable per-pass win |
| KB-scale dumps (gate_query/review_scan) | PARKED | 1.6x on ~µs = sub-ms per run; bench-before-wire has no gateable criterion |
| stored `metadata_json` columns | OUT OF SCOPE | §6.3 stored-JSON rule + mixed byte-form rows across the cutover (non-ASCII `\uXXXX` vs raw UTF-8) |
| `default=str` writers | PARKED (audit-gated) | orjson serializes datetime natively as RFC 3339 — `default` never fires for timestamps → silent format change; needs per-site audit |
| `sort_keys=True` diff-stable writers | STAY STDLIB | compact form byte-differs from stdlib (`", "` separators) → churns diffs of reviewed reports |
| `indent` ≠ 2 stdout | STDLIB (shim falls back) | orjson has only OPT_INDENT_2 |
| KB-scale parse (report/scanner JSON loads) | CONVERT as consistency | jsonio.loads API-identical; win is catch-taxonomy uniformity + tiny CPU, recorded as consistency not measured-perf |

**S1 addendum 2026-10-30 — repo-wide loads cutover (operator go):** after the
criteria table, the gate_query/review_scan loads side converted (12+7 sites,
incl. review_scan's deliberate `raise jsonio.JSONDecodeError` — ctor is
(msg, doc, pos)-compatible), then a scripted repo-wide sweep moved ALL
remaining stdlib `json.loads` to `jsonio.loads` (80 files; per-file
py_compile + ruff gates, lookbehind regex so `_json.loads` aliases were not
corrupted). Final stdlib sites are the DELIBERATE three: model_analytics
models.json config (sub-KB rule), static_checks.py (source-scanner whose
text patterns mention json.loads — converting it would have corrupted the
scan patterns it sells), corpus.py (mixed `_json` alias with stored-column
`_json.dumps` in the same functions). Second-order effects caught and
fixed: (1) the emb-decode tripwire
(`static_checks.check_embedding_decode_chokepoint`) matched only
`json.loads` — extended to `jsonio.loads` (the strike class — raw-JSON
decode of embedding receivers — is engine-independent); (2) 7 entry-point
scripts with zero-helpers-deps-by-design (quack_query, review_menu,
license_check, nic_scorer_bench, relation_diff_audit, trace_quote,
security_scopes) REVERTED to stdlib — adding a jsonio import there requires
a sys.path bootstrap and changes their dependency posture (caught by
test_helper_entry_points_set_sys_path_before_helpers_import); (3) orjson was
missing from THIRD_PARTY_LICENSES.md — added (MIT OR Apache-2.0 dual);
license_check green. Validation: 2,045 targeted tests green across all 80
module-matched suites; ruff/ty/deptry clean (deptry DEP002 markupsafe
pre-existing); license_check green. Loads-only: no dumps site changed in
this sweep.
- Deltas to gate per converted site: dumps returns bytes and orjson has no
  sort_keys (sorted-keys/diff-stable writers stay stdlib or pre-sort); strict
  UTF-8 on `rb` reads replaces errors="replace" (bad lines raise + skip —
  harvest already try/excepts per line; accepted drift for log corpora).
- Expected shape: ~0.45s and ~8MB peak saved per 81MB parsed on the mass
  lanes today, same ratio per generation as the corpus grows monotonically.

Routing-principle conformance (architecture.md §10: parquet(zstd) at rest,
Arrow in flight, JSON transient-on-demand only): orjson never enters either
governed tier. The bulk mass lane is a foreign-format INGESTION edge —
harness-owned JSONL in (format not ours to choose), parquet/duckdb out; a
faster decoder strengthens the JSON->columnar conversion §10 mandates. The
dumps side splits: consumer-demand stdout/exports (model_analytics.py:1656,
agent_traces.py:3041) are §10's sanctioned transient pattern — keep stdlib
there (orjson has no indent=1; cosmetic sites, tiny); JSON-as-text columns
that already exist in our stores (harvest meta/watermarks
harvest_conversations.py:138/:924, traces caps agent_traces.py:1998/:2036)
are pre-existing, bounded, and OUT OF SCOPE — this arc must not create or
expand stored-JSON columns because the encoder got fast. Guardrails: the
`sort_keys=True` sites (harvest_conversations.py:1395 watermarks,
agent_traces.py:712) stay stdlib or pre-sort (orjson has no sort_keys);
dumps fast path only where the consumer accepts bytes.

### 6.4 Trial by-catch: the --check wall is opencode-lane sqlite, not JSON
cProfile of one warm `--check` (58s instrumented vs 31s uninstrumented): 52s of
58s cumulative sits in `_opencode_source_drift` -> `_lane_uncovered`
(harvest_conversations.py:1743) — per source, two full-scan GROUP-BYs:
`SELECT session_id, MAX(time_updated) FROM session_message GROUP BY session_id`
(materializes every session's max — 164 executes at ~245ms avg = 40.2s self)
and `SELECT json_extract(data,'$.part.sessionID'), MAX(json_extract(data,'$.time'))
FROM event WHERE type='message.part.updated.1' GROUP BY 1` (json_extract per row
over the whole event table — 8.8s in fetchall). Remaining wall: dsh lane ~8s
(`_dsh_index_lag` 4.1s + `_dsh_read_log` 3.9s over 14.6k calls), pyarrow parquet
census 1.7s, JSON parse ~0.6s. Any real convo-fresh --check lever lives in these
drift queries (unindexed scans of harness-owned opencode storage sqlite), not in
JSON speed — but the stores are the LIVE harness DBs, so indexing them from our
side is an invasive decision that needs its own look (watermark-bounded queries
or a sidecar max-ts mirror are the non-invasive shapes). Side notes: warm --check
is ~31s, not "seconds-class" as the AGENTS.md convo-fresh line claims (rebuild
leg separate); a pre-existing drift warning (13 deleted sessions labelled
opencode:live) is cleared by `harvest_conversations.py --resolve-orphans`.
