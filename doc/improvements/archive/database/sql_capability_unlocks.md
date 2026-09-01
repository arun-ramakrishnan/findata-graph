---
title: SQL Capability Unlocks — Note Vectors in DuckDB + Shortest-Path Fix
status: executed
filed: '2026-08-21'
executed: '2026-08-21'
completed_md: '143'
area: helpers/graph/query.py` (materialisation
---

# Proposal: SQL Capability Unlocks — Note Vectors in DuckDB + Shortest-Path Fix

**Status:** EXECUTED (2026-08-21) — all of Parts A/B/C implemented,
tested, and gated; see §11 for the implementation log and live-run
numbers. Key decisions were locked 2026-08-21 (§9); review findings
folded the same day (§10).
**Date:** 2026-08-21
**Author:** Agent analysis (user-directed); all measurements on the live DB
(1,209 entities / ~5.1k edges / 1,227 note docs, bge-small-en-v1.5 384-dim,
DuckDB 1.5.5, SQLite 3.46.1).
**Scope:** `helpers/graph/query.py` (materialisation + wrappers),
`app.py` (one route guard), tests, `_SCHEMA_VERSION` bump. No SQLite schema
change, no new virtual tables in `research.db`, no frontend change.
**Builds on:** `local_embeddings.md` (archived; bge apply + eval),
`vec_search.py` sidecar design, `duckpgq_retirement.txt` (plain-SQL posture).

---

## 1. TL;DR

Three findings from a SQL-capabilities study of both engines:

- **A (unlock):** DuckDB can read the `note_search` embedding JSON column
  through the sqlite scanner. A `v_note_embeddings` CTAS in the existing
  `_build_graph` materialisation makes the *whole corpus* (companies +
  sectors + newsletters) vector-searchable **inside SQL**, unlocking
  cross-doc-type semantic joins and a near-duplicate QA tripwire that have no
  implementation path today. Verified live: KNN+graph-filter 7ms,
  edition→companies 8ms, pairwise self-join ~1s.
- **B (P0 perf bug):** `shortest_path`'s recursive CTE enumerates *all simple
  paths* before `LIMIT 1`. At the production default `max_hops=5`
  (`/api/graph/shortest`, which permits up to 10) this is a latency bomb:
  >100s walking the attached SQLite table; still 8.3s over a local table.
  Fix: level-by-level BFS over a materialised undirected adjacency table —
  true shortest-path semantics, bounded work, milliseconds.
- **C (hardening):** DuckDB accepts bind parameters inside recursive CTEs
  (verified), so `_lit()` string interpolation in the walk queries can be
  replaced with `?` params — deleting the escaping/control-char class
  (`_STRING_LIT_RE`, `_CONTROL_RE`, fuzz-discovered NUL crash) at its root.

Also records four **measured-not-adopted** items so future audits don't
re-run them (§6).

---

## 2. Background

The embeddings stack post-`local_embeddings.md`: `note_search` (FTS5,
SQLite) carries a 384-dim JSON `embedding` column for all 1,227 indexed
docs; the vec0 mirror (`note_search_vec`) lives in the `<db>_vec.db`
**sidecar** because vec0/spellfix1-class virtual tables break DuckDB's
scanner catalog scan ("no such module: vec0", completed.md #73) and all
vector state is deliberately derived/snapshot-excluded. Company-only
embeddings additionally exist as `company_embeddings` (SQLite) →
`v_embeddings` (DuckDB CTAS, `FLOAT[]`).

Consequence: note-level vectors are reachable only from SQLite-side code
(Python RRF / vec0 KNN). There is **no SQL join path** from a note vector to
the graph — which is exactly what the scanner + `CAST` unlock provides
without touching any of the constraints above.

---

## 3. Part A — `v_note_embeddings`: whole-corpus vectors in DuckDB

### 3.1 Verified capability (live measurements, 2026-08-21)

```sql
-- materialise (cold cost)
SET sqlite_all_varchar=true;
CREATE TABLE nv AS
  SELECT file_path, doc_type, title,
         CAST(embedding AS FLOAT[384]) AS emb
  FROM fin.note_search WHERE embedding IS NOT NULL;
-- 1,227 docs, 179ms
```

| Query shape | Measured | Result quality |
|---|---|---|
| KNN + graph filter in ONE query (CEAT-similar ∧ sector='Automotive') | **7.2ms** | Apollo Tyres, Camso, MRF |
| Edition → most-similar companies (doc-type join, no `cited_in` edge) | **8.2ms** | defence edition → BEL, Bharat Dynamics, HAL, BF |
| Pairwise company self-join (~570k pairs) | **~1.0s** | Patanjali↔Ruchi Soya (rename!), Ujjivan pair, Piramal pair, Muthoot pair |

The pairwise result doubles as a **data-quality tripwire**: top cosine pairs
were exactly the rename-candidate / promoter-group duplicate clusters the
rename machinery cares about — as a single maintenance query.

### 3.2 Design

**Materialisation** (a sibling CTAS in `_build_graph` — note the build
calls `_materialise_vertices`/`_materialise_edges`; it does NOT call
`_materialise_embeddings`, so the SET below must live at the new CTAS
site):

```python
# The bridge's auto-conversion chokes on typed columns; the only
# production SET today lives (conditionally) inside
# _materialise_embeddings (query.py:778) — issue it here too, idempotent.
con.execute("SET sqlite_all_varchar=true")
con.execute(
    "CREATE TABLE v_note_embeddings AS "
    "SELECT file_path, doc_type, title, "
    "CAST(embedding AS FLOAT[%d]) AS emb FROM fin.note_search "
    "WHERE embedding IS NOT NULL AND embedding != ''" % dims
)
```

- **Drop-list:** add `v_note_embeddings` to the `_build_graph` DROP loop
  (query.py:561). Without it the SECOND rebuild crashes on "table already
  exists" (B1 already carries the same note for the adjacency tables).
- **Dims gate:** probe `len()` on one stored row first, filtered the same
  way (`IS NOT NULL AND != ''` — the `stored_embed_dims()` discipline;
  unparsable JSON counts as absent). Store the resolved dims in
  `_build_meta` (`note_embed_dims`). If zero rows / unparsable JSON →
  create the empty typed table (mirrors `_materialise_embeddings`'
  fallback) so wrappers degrade to `[]` instead of raising.
- **Staleness (review finding 1 — the original "generation already covers
  content changes" claim was FALSE):** generation triggers exist only on
  `entities`/`graph_edges` (db.py:246; FTS5 virtual tables cannot carry
  triggers anyway), so a `note_search` rebuild does NOT bump generation —
  a warm `.duckdb` would keep serving a stale `v_note_embeddings` after
  every maint-full (step 6 runs after the TIER1 graph-rebuild; nothing
  after it forces a rebuild). Fix at the writer: the APPLY path of
  `rebuild_note_search` and `embeddings --maint` (only when rows changed)
  bump `db_meta.generation` after commit — `--check` and sidecar-only
  writes must NEVER bump. This closes the identical hole for
  `company_embeddings` → `v_embeddings`, including the new maint-full
  step 6b (whose "DuckDB materialises on connect()" behavior is only true
  when generation moves). Accepted cost: a maint-full that changed
  embeddings forces one ~2s DuckDB rebuild on the next connect.
- **Model-swap safety:** a dims mismatch between `_build_meta` and the
  live column must force a cold rebuild (explicit dims check in `_is_warm`,
  parity with the `duckdb_version` drift check). Never serve zip-truncated
  cosines. Dims alone cannot see a 384→384 model swap (e.g. MiniLM is
  also 384-dim), so ALSO stamp the model label: `rebuild_note_search`
  writes it as a `db_meta` key (`note_embed_model`) — `note_search` rows
  carry no model column, so `db_meta` is the only SQL-side home — and
  `_is_warm` compares it exactly like `duckdb_version`.
- **`_SCHEMA_VERSION` "10" → "11"** to force one rebuild on deploy.
- **Query-prefix asymmetry:** doc-doc joins are prefix-free on both sides —
  correct by construction. Any future text-query wrapper over this table MUST
  go through `query_embedder()` (BGE instruction prefix), never
  `embed_document`.

**New wrappers** (all `@_with_generation_cache`, all read-only):

| Wrapper | Contract | Consumer |
|---|---|---|
| `similar_notes(con, file_path, k=10, doc_type=None)` | `list[(file_path, title, sim)]`; KNN over `v_note_embeddings`; EXCLUDES the query note itself (self-cosine is 1.0) | CLI + `/api/graph/similar/<path>` |
| `notes_like_entity(con, entity, k=10, doc_types=('chatter','points_and_figures','plotlines'))` | newsletters semantically closest to a company's note — reverse of `cited_in`, needs no edge | context packs (future) |
| `edition_companies(con, edition_title_or_stem, k=10)` | companies most similar to an edition note | narrative attribution (future) + `/api/graph/edition_companies` |
| `near_duplicate_notes(con, min_sim=0.9, doc_type='company')` | pairwise self-join above threshold | maintenance/QA command (`make` target or `db_maint` section) |

Pairwise cost control: at 1,068 companies the full self-join is ~1s — fine
for a maintenance command, not an API hot path. If it grows, pre-group by
sector (cosine within sector buckets first, cross-sector only above a
candidate threshold). Do not build that now.

**Explicitly out of scope (Part A):**
- Moving query-time `/api/search` ranking into DuckDB — the scanner cannot
  evaluate FTS5 `MATCH`, so hybrid fusion stays SQLite-side (Python RRF /
  vec0 KNN, unchanged).
- Any change to the sidecar, `rebuild_note_search`, or `research.db` schema.
- HNSW anything (standing directive; brute-force is single-digit-ms here).
- An endpoint for `near_duplicate_notes` — it is a human-triage maintenance
  tool, not an API concern (decision 2026-08-21).

**In scope (decided 2026-08-21):** two read-only GET endpoints over the new
wrappers — `/api/graph/similar/<path:note_path>` (`similar_notes`) and
`/api/graph/edition_companies` (`edition_companies`). Rationale: wrappers
are the hard part; the endpoints are ~30 min each, curl-evaluable against the
labeled set immediately, and `edition_companies` has no edge-based
substitute. Both validate their path/title argument and return **404 for an
unknown note** (parity with the other graph routes).

### 3.3 Snapshot impact

One more parquet file in `snapshots/parquet/duckdb/`
(`v_note_embeddings.parquet`, L1 pattern). **Manifest note (2026-08-21
hardening):** `export_parquet_duckdb()` now exports ONLY tables listed in
`MATERIALISED_TABLES` (helpers/graph/query.py — single source of truth
with the `_build_graph` drop pass; tables outside it are skipped +
warned, after the `e_all_und` benchmark leftover leaked an orphan parquet
into a snapshot commit). A1/B1 must therefore ADD `v_note_embeddings`
(and `e_all_und`/`e_dir`) to the manifest in the same change that creates
them. Content-derived; `make snapshot-check` verifies it like
`v_embeddings.parquet`. No exclusion needed.

---

## 4. Part B — shortest_path latency fix (P0)

### 4.1 Problem (measured)

`_shortest_path_cte` (query.py:1205) seeds one row and joins
`(ge.source = w.node OR ge.target = w.node)` against **`fin.graph_edges` —
the attached SQLite table** — with a per-path cycle guard
(`array_contains(string_to_array(w.path,...))`). The guard prevents revisits
*within one path* but not re-exploration of the same node across different
paths, so the CTE materialises every simple path ≤ max_hops before
`ORDER BY depth LIMIT 1` picks one.

| walk substrate | hops=3 | hops=4 | hops=5 |
|---|---|---|---|
| attached `fin.graph_edges` (current) | 81ms | sec-scale | **>100s (timed out)** |
| materialised undirected adjacency | 28ms | 329ms | 8.3s |

Production exposure: `/api/graph/shortest` defaults to `max_hops=5` and
accepts up to 10 (app.py:1540) — i.e. the default endpoint sits on a
multi-second-to-minute bomb, and `max_hops=10` would hang the worker.

### 4.2 Fix — level-by-level BFS over a materialised adjacency table

1. **Materialise `e_all_und`** in `_materialise_edges`:

   ```sql
   CREATE TABLE e_all_und AS
     SELECT source AS a, target AS b, edge_type, valid_from, valid_to
       FROM fin.graph_edges
     UNION ALL
     SELECT target AS a, source AS b, edge_type, valid_from, valid_to
       FROM fin.graph_edges;
   ```

   (~10k rows; lives in the same rebuildable cache as the other `e_*`
   tables, so the staleness contract is unchanged.) **`edge_type` is
   load-bearing (review finding 3):** `shortest_path`'s existing contract
   filters traversal by `edge_label` via EDGE_REGISTRY (query.py:1226) —
   without the column the BFS rewrite silently drops a documented feature.
   The per-level step adds `AND edge_type = ?` when `edge_label` resolves
   (unrecognized label = no filter, the historical behavior).

2. **Replace the single-statement CTE with a BFS loop** (≤ max_hops
   iterations, each one set-based DISTINCT step):

   ```text
   frontier = {src}; visited = {src}; parent = {}
   repeat max_hops times:
       next = SELECT DISTINCT b FROM e_all_und
              WHERE a IN frontier AND b NOT IN visited   -- one query
       record parents; if dst ∈ next: reconstruct & return
       frontier, visited = next, visited ∪ next
   ```

   Each level touches each edge once → total O(max_hops · (V+E)) instead of
   O(paths). True hop-shortest semantics guaranteed by construction (BFS
   layer order), which the current code only approximates via
   `ORDER BY depth LIMIT 1` after full enumeration.

   Implementation note: keep it SQL-set-based per level rather than
   fetching all edges into Python — consistent with the repo's "push work
   into SQL" posture (K-series), while the Python-side loop supplies the
   cross-level state a recursive CTE cannot express. Mechanics pinned by
   review: use **temp tables** for frontier/visited, not `?`-lists
   (visited can reach ~1.2k nodes; 8 levels × 1k+ bind parameters per
   step is the wrong shape — `NOT EXISTS (SELECT 1 FROM visited ...)`
   scales and stays in SQL); pick each discovered node's parent
   **deterministically** (`MIN(a)`) so path reconstruction is stable
   across runs; **seed the frontier from `v_node`**, not the attached
   `fin.entities` the old CTE read. Contract pins: `src == dst` →
   `[(src, 0)]`; unknown src/dst → `None`; unreachable → `None` — all
   three mirror the old CTE's behavior, now with bounded cost.

3. **Temporal filter moves into the adjacency CTAS?** No — `as_of` filters
   per query, and `e_all_und` is shared. Keep `valid_from/valid_to`
   predicates as a WHERE on each level's step (join back to
   `fin.graph_edges` by (source,target) pair, or carry the validity columns
   in `e_all_und` and filter per level — carry them; two extra columns beat
   a join).

4. **`find_cycles` stays as-is except substrate**: its explosion is
   bounded by `max_hops ≤ 6` + `LIMIT`, cycles enumeration is inherently
   exponential, and it's a diagnostic tool (G3). Substrate caveat (review
   finding 2): `find_cycles` is a **directed** walk (query.py:1294 — a
   single symmetric `A→B` row is deliberately NOT a cycle; two directed
   rows are). It must NOT read `e_all_und`: on the doubled undirected
   table every edge becomes a false 2-cycle and the diagnostic drowns.
   Materialise a directed **`e_dir`** (source→target as stored, plus
   `edge_type` for its label filter) alongside `e_all_und` and switch
   `find_cycles` to THAT for the ~2.9x constant factor — or leave it on
   `fin.graph_edges` if the second table isn't worth it.

5. **API guard:** cap `max_hops` at 8 in `/api/graph/shortest` (400 above).
   Decided 2026-08-21: with BFS the cost is linear per hop, so the cap's job
   shifts from protecting the server (the old enumeration bomb) to defining
   what the endpoint means — and the honest boundary is the graph diameter
   (8). Beyond it every pair is reachable and "shortest path" stops
   discriminating. No existing caller passes an explicit `max_hops`
   (verified 2026-08-21), so this is purely forward-looking.

### 4.3 Part C — bind parameters in the walks (verified)

DuckDB accepts `?` params inside recursive CTEs and set-based steps
(verified live). Convert `_shortest_path_cte`'s replacement, `find_cycles`,
and `semantic_neighbors`'s `lit_co` interpolations to bound parameters.
The walk path contains two more literal interpolations beyond `lit_co`
(review note): the temporal clause interpolates the validated `'{iso}'`
string directly (query.py:1239) and the edge-type clause uses
`_lit(reg['edge_type'])` — both are in scope for the conversion.
Keep `_lit()` itself (other callers + CLI `sql` passthrough remain), but the
`_CONTROL_RE` NUL-crack class disappears from the walk paths; the fuzz tests
(`test_fuzz_semantic.py`) stay as regression guards. Verified-safe during
review: `_with_generation_cache` keys on `(fn, args, generation,
schema_version)` — NOT the SQL text — so this conversion cannot corrupt
the query cache.

---

## 5. Implementation plan

| Step | Files | Effort |
|---|---|---|
| B1. `e_all_und` + `e_dir` CTAS (+`edge_type`, validity cols) in `_materialise_edges`; drop-list update | `query.py` | ~45 min |
| B2. BFS `shortest_path` rewrite (levels + parents + reconstruction), keep signature/contract `list[(name, hop)] \| None`; edge-case pins (src==dst / unknown / unreachable) | `query.py` | ~2 h |
| B3. API cap `max_hops ≤ 8` (= diameter, §4.2.5) + test | `app.py` | ~15 min |
| B4. Derived-index generation bumps: apply-path `rebuild_note_search` + `embeddings --maint` (changed rows only) bump `db_meta.generation` (small `bump_generation()` helper in `db.py`); `--check` / sidecar-only writes never bump | `db.py`, `rebuild_note_search.py`, `embeddings.py` | ~45 min |
| C. Bind-param conversion (walks + `semantic_neighbors` + temporal `'{iso}'` + edge-type `_lit`) | `query.py` | ~45 min |
| A1. `v_note_embeddings` CTAS (+ `SET sqlite_all_varchar` at site, `!= ''` filter, drop-list entry) + dims probe + `_build_meta` dims stamp + `note_embed_model` stamp in `db_meta` + compare in `_is_warm` + empty-table fallback; `_SCHEMA_VERSION` → 11 | `query.py`, `rebuild_note_search.py` | ~1.5 h |
| A2. Wrappers: `similar_notes` (self-excluding), `notes_like_entity`, `edition_companies`, `near_duplicate_notes` (+CLI subcommands) | `query.py` | ~2 h |
| A3. Near-duplicate maintenance entry point (CLI only; **dry-run default**, `--apply` gated on explicit user permission) | `Makefile` / `db_maint.py` | ~30 min |
| A4. `/api/graph/similar/<path>` + `/api/graph/edition_companies` GET endpoints over A2 wrappers (+ validation/404 + tests) | `app.py` | ~1 h |
| Tests: BFS correctness (vs old CTE on small fixture, all (src,dst,max_hops) combos + edge cases + `edge_label` filter), temporal filter, generation bump (apply bumps, `--check` doesn't), model-stamp swap detection, dims-gate degradation, wrapper shapes (incl. `similar_notes` self-exclusion + endpoint 404), near-dup detection on injected duplicates | `tests/` | ~2.5 h |
| Gates: `make qa`, `make perf` (add shortest-path perf gate: <100ms @ hops=5 **and on the unreachable-dst worst case** — a full-graph traversal — on a live-shaped fixture), snapshot regen + `snapshot-check` | — | ~30 min |

Total ~2.5 days. Deploy sequence: code → `make graph-rebuild` (schema 11
forces cold) → snapshot regen → gates.

**Execution rules (standing, decided 2026-08-21):**

1. **Dry-run default for anything that modifies notes on disk.** Every
   command in this work that can write notes/files runs read-only by
   default; writes happen only behind an explicit `--apply` flag, and
   `--apply` runs only after the user has explicitly granted permission for
   that specific step (house pattern from `local_embeddings.md` §11).
   None of B1–A4 modify notes directly — they write derived DuckDB/SQLite
   state — but any follow-up remediation surfaced by `near_duplicate_notes`
   triage (e.g. rename edits) falls under this rule.
2. **Gate expansion parked until the end.** `make qa` / `perf` / `advisory`
   and the snapshot regen run once, after all code + tests land (step G) —
   not interleaved per step. Interim verification uses targeted pytest
   runs only.

## 6. Measured-not-adopted (record; do not re-audit)

- **Generation-counter triggers**: +244% on bulk inserts (37ms vs 11ms per
  2k rows) — absolute cost tiny; the O(1) staleness detection is worth it. KEEP.
- **FTS5 bm25 column weights** (title ×8): identical top-5 on sample queries;
  moot anyway under the embeddings-first ranking posture.
- **`COUNT(*) OVER()` piggyback for `/api/search`** (B3-style): blocked —
  FTS5 `snippet()` cannot run in window context ("unable to use function
  snippet in the requested context"); the two-query pattern is required.
- **ASOF JOIN / MERGE INTO / QUALIFY** (unused DuckDB 1.5 features): no
  surface here where they beat current forms at this scale; write-back path
  already audited (algorithms.py:611).

## 7. Risks

| Risk | Mitigation |
|---|---|
| BFS rewrite changes result contract subtly (tie-breaking among equal-depth paths) | Old CTE returned an arbitrary shortest path too (no tie-break spec); assert same depth + endpoints in tests, document tie-break as unspecified (deterministic `MIN(a)` parent pick in the new code) |
| `v_note_embeddings` stale after maint-full — `note_search` is invisible to the generation triggers (only `entities`/`graph_edges` bump) | Writer-side generation bump (B4): apply paths bump after commit; `--check`/sidecar never do |
| Dims drift after model swap serves garbage cosines | `_build_meta.note_embed_dims` check in warm path → forced rebuild (mirrors `stored_embed_dims()` gate); the `db_meta.note_embed_model` stamp catches the 384→384 swap dims can't see |
| Generation bump forces a DuckDB rebuild after every embedding-changing maint-full | Bounded ~2s rebuild on next connect; accepted (correctness over a two-second warm-up) |
| Scanner breaks on FTS5 virtual table in a future DuckDB | Verified working on 1.5.5; wrap CTAS in try/except → empty typed table + WARNING (house best-effort pattern), wrappers degrade to `[]` |
| Pairwise self-join growth as corpus grows | Maintenance-only entry point; sector-bucket prefilter documented as the escalation path |
| `max_hops` cap surprises a future caller | No caller passes explicit `max_hops` today (verified 2026-08-21); 400 message documents the cap and points at the diameter rationale |

## 8. Success criteria

- `/api/graph/shortest` default request p95 < 100ms (was multi-second),
  INCLUDING the unreachable-dst worst case (full-graph traversal, bounded);
  `max_hops>8` returns 400, never hangs.
- Shortest-path depths identical to the old CTE on a 50-node fixture across
  all (src,dst,max_hops) combos; edge cases pinned (src==dst → `[(src,0)]`,
  unknown → `None`, unreachable → `None`); `edge_label` filtering preserved.
- After a maint-full that rebuilt `note_search` with NO entities/edges
  write, the next `connect()` refreshes `v_note_embeddings` (generation
  bump verified — the new docs appear in `similar_notes`).
- `similar_notes('CEAT')` top-5 dominated by tyre/auto peers and never
  returns the note itself; unknown note path → 404;
  `near_duplicate_notes()` flags the known rename cluster
  (Patanjali/Ruchi Soya) in its top rows.
- `make qa` + `make perf` green; `snapshot-check` green with the new parquet.

## 9. Open questions

1. **RESOLVED 2026-08-21 — endpoints.** `similar_notes` and
   `edition_companies` ship as read-only GETs now (OKF adoption is archived,
   so no competing diff budget; consumer-absence alone didn't justify
   withholding two ~30-min endpoints). `near_duplicate_notes` is CLI-only.
2. **RESOLVED 2026-08-21 — integrity report.** Ship `near_duplicate_notes`
   as a standalone maintenance command with human triage. The measured top
   pairs mix genuine rename candidates (Patanjali↔Ruchi Soya, 0.942) with
   real promoter-group siblings (Ujjivan/Piramal/Muthoot pairs), so a
   warning-class gate check would train users to ignore the report — and
   would couple a deterministic gate to embedding freshness. Promote to an
   *informational* section of the integrity report only once the noise rate
   is known from a few triage runs; never warning class.
3. **RESOLVED 2026-08-21 — no `weight` in `e_all_und`.** Weights are all
   1.0 (C5 verdict), so a weighted-BFS would be identical to hop-count BFS;
   the column can be added in a schema bump if real weights ever arrive.

## 10. Review fold (2026-08-21)

Agent review against the live code (`query.py`, `db.py`, `app.py`) folded
in before implementation. Findings → where they landed:

1. Generation triggers cover only `entities`/`graph_edges` (db.py:246) —
   the "generation check already covers content changes" claim was false
   for `note_search` (and `company_embeddings`). → writer-side bumps, new
   step B4; staleness bullet in §3.2; risk row.
2. `find_cycles` is a directed walk (query.py:1294) — it must not read
   `e_all_und` (every edge would become a false 2-cycle). → directed
   `e_dir` in §4.2.4 / B1.
3. `e_all_und` carries `edge_type` — the BFS must preserve the
   `edge_label` contract (query.py:1226). → §4.2.1 CTAS + per-level
   filter.
4. `v_note_embeddings` in the `_build_graph` drop-list (query.py:561) —
   else the second rebuild crashes. → §3.2 drop-list bullet.
5. `SET sqlite_all_varchar=true` at the CTAS site (the only production
   SET is conditional inside `_materialise_embeddings`, query.py:778);
   placement corrected to "sibling CTAS in `_build_graph`" (the build
   never calls `_materialise_embeddings`). → §3.2 snippet.
6. Filter parity (`embedding IS NOT NULL AND != ''`) on CTAS + dims
   probe; edge-case contracts pinned (src==dst → `[(src,0)]`, unknown →
   `None`, unreachable → `None`). → §3.2, §4.2.2, tests row.
7. Model-label stamp (`db_meta.note_embed_model`, compared in `_is_warm`
   like `duckdb_version`) closes the 384→384 model-swap hole the dims
   gate cannot see. → §3.2 model-swap bullet, A1.

Hardening folded from the same review: `similar_notes` self-exclusion +
endpoint 404 (§3.2 wrappers/in-scope); BFS temp-table frontier/visited,
deterministic `MIN(a)` parent pick, `v_node` seeding (§4.2.2); Part C
explicitly covers the temporal `'{iso}'` and edge-type `_lit`
interpolations (§4.3); perf gate extended to the unreachable-dst worst
case (§5 gates, §8).

Verified-safe during review (no change needed): `_with_generation_cache`
keys on `(fn, args, generation, schema_version)` — not the SQL text — so
Part C's bind-param conversion cannot corrupt the query cache; and
`_SCHEMA_VERSION` is currently `"10"` (query.py:183) as the bump plan
assumed.

---

## 11. Implementation log (EXECUTED 2026-08-21)

All of Parts A/B/C landed in one sitting, slice by slice (B1→B2→B3→B4→
C→A1→A2→A3→A4), targeted tests per slice, full gates once at the end —
the standing execution rules in §5.

- **B1:** `_materialise_walk_substrate()` creates `e_dir` (stored
  direction; the `find_cycles` substrate — the doubled table would
  false-2-cycle) and `e_all_und` (doubled undirected; the BFS substrate),
  both carrying `edge_type` + validity, endpoints resolved to `v_node`
  ids; both in `_EXTRA_MATERIALIZED`/`MATERIALISED_TABLES`.
- **B2:** `shortest_path` → `_shortest_path_bfs` (temp-table
  frontier/visited/parents, deterministic `MIN(a_id)` parent, `v_node`
  seeding, contract pins: src==dst → `[(src,0)]`, unknown → None,
  unreachable → None, hops=0 → None). `_shortest_path_cte` retained as
  the small-fixture ORACLE only — the equivalence tests
  (`TestBfsShortestPath`) compare (depth, endpoints) across
  pairs × hops {1,2,3} × labels × temporal dates, never full sequences
  (tie-breaks differ by design).
- **B3:** `/api/graph/shortest` cap 1..8 with the diameter rationale in
  the 400 message; boundary test (8 passes validation, 9 rejected).
- **B4:** `bump_generation()` in db.py (no-op without db_meta; bare test
  fixtures unaffected). Apply-path `rebuild_note_search` bumps
  unconditionally + stamps `db_meta.note_embed_model` (apply path only —
  the stamp describes table content; `--check` never writes it);
  incremental bumps only on a non-empty delta; `populate_local` bumps
  only on real change (cache misses or GC > 0) so a no-change maint-full
  stays warm.
- **C:** binds in BFS, `find_cycles`, and `semantic_neighbors` (incl.
  the cross-sector subquery). `_lit()` survives for the CLI `sql`
  passthrough only.
- **A1:** `_materialise_note_embeddings()` (SET at the CTAS site,
  json_array_length dims probe, `!= ''` filter, empty-typed-table
  fallback) + `_SCHEMA_VERSION` 10→11 + `_build_meta.note_embed_dims`/
  `note_embed_model` stamps + `_probe_note_embed_state()` live-side
  comparison in `_is_warm` (same-dims model swaps force cold; both tests
  cover the drift).
- **A2:** `similar_notes` / `notes_like_entity` / `edition_companies` /
  `near_duplicate_notes` + CLI subcommands. None-for-unknown-reference
  contract throughout (endpoints map it to 404).
- **A3:** `make near-duplicates` (help-annotation tested). Inherently
  read-only — no `--apply` exists; remediation stays user-held rename
  work per the §5 execution rules.
- **A4:** `/api/graph/similar/<path:note_path>` (findata/ prefix added
  when absent) + `/api/graph/edition_companies` (?edition= required);
  404 parity, k/doc_type validation, endpoint tests with stubbed
  wrappers.

Live-run numbers (live DB, 1,317 entities / 5,126 edges / 1,227 embedded
docs):

- BFS shortest_path: 10ms default request, 50ms unreachable worst case
  (full component traversal; the live undirected graph has 42
  components), steady-state after a one-time ~300ms per-connection
  warm-up — vs the old CTE's multi-second/timeout at the same requests.
- `v_note_embeddings`: 1,227 rows × 384 dims; `note_embed_dims=384`
  stamped. `db_meta.note_embed_model` materialises on the next
  note-search apply (costs exactly one extra rebuild, then stable).
- Wrapper quality matches §3.1: `edition-companies` on
  `BEL_HUL_Tata_Capital` → Bharat Electronics 0.845 / Bharat Dynamics
  0.821 / HAL 0.783; `near-duplicates` top pairs Patanjali↔Ruchi Soya
  0.942, Ujjivan 0.915, Piramal 0.914, Muthoot 0.909 (~1s self-join).
- Snapshot regenerated: 25 DuckDB manifest tables (three new parquets:
  e_all_und, e_dir, v_note_embeddings); snapshot-check green
  (generation 25481 both sides, 36 tables all OK).

Gates: `make qa` fully green (lint/ty/deptry/static/pytest 1,900+/
notes/integrity/snapshot); `make perf` 20/20 with the new
`shortest_path_bfs` gate (`tests/bench_shortest_path.py`, <100ms
steady-state asserted on both the default and the unreachable worst
case, warm-up call absorbing the one-time compile).

Incidental fix en route: `_is_warm`'s SQLite probe now tries the
colocated `<duckdb>.db` first and STOPS at the first existing candidate
(previously it fell through to the live research.db whenever a colocated
test DB had `db_meta` but no generation row — a latent fixture-isolation
bug the new warm-path tests exposed; production unaffected since
`memory/graph.db` never exists).
