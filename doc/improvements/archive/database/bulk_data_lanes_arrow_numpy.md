---
title: "Bulk-data lanes — native PyArrow/NumPy (snapshot parquet, embed_matrix revival, vss mask, fts-parity bench repair)"
status: executed
filed: "2026-09-10"
executed: "2026-09-11"
completed_md: "224"
area: "helpers/maintenance/snapshot_db.py"
---

# Bulk-data lanes — native PyArrow/NumPy (snapshot parquet, embed_matrix revival, vss mask, fts-parity bench repair)

**Date:** 2026-09-10 · **Status:** PROPOSED ·
**Area:** `helpers/maintenance/snapshot_db.py` (SQLite-side parquet export
+ restore), `helpers/core/embed_matrix.py` +
`rebuild_note_search.py:_refresh_embed_matrix`, `helpers/core/vss_index.py`,
`helpers/bench/fts_duckdb_parity.py`, `tests/test_snapshot.py`

## 1. Motivation

Four bulk-data lanes share one theme: **stuck on pre-#223 shapes** (pandas
middleman, JSON-encoded vectors) or leaving vectorized numpy on the table.
PyArrow and NumPy are already direct dependencies; no new deps.

1. The git-tracked Parquet snapshot's SQLite side runs pandas as a pure
   middleman in BOTH directions (export and restore).
2. The #223 f32-BLOB migration left two `json.loads` readers behind — one
   of them **silently kills the aligned-matrix refresh** (a derived-state
   lane frozen at pre-migration content), the other **outright breaks the
   FTS-vs-DuckDB parity bench** on the live corpus.
3. One numpy python-loop mask in the VSS index (micro, rides along).

## 2. Current state (exact seams)

| Seam | Location | Today |
|---|---|---|
| Export | `export_parquet_sqlite` (snapshot_db.py:696) | `pd.read_sql` → `pa.Table.from_pandas` → `pq.write_table(zstd)` |
| Restore read | `_parquet_rows` (snapshot_db.py:860) | `pd.read_parquet` → `astype(object).where(notna)` → `itertuples` → `executemany` |
| Verify | `_verify_parquet_sqlite_side` (snapshot_db.py:794) | row counts vs `pq.read_metadata` — schema-agnostic, no change |
| Matrix refresh | `rebuild_note_search.py:590` `_refresh_embed_matrix` | `_json.loads(r[1])` on now-BLOB embeddings → `UnicodeDecodeError` → swallowed by blanket `except` (line 592) → **silent no-op since #223** |
| Matrix source | `embed_matrix.py:210` `from_note_search` | same `json.loads(r[1])`; diagnostic reader, benches only |
| VSS mask | `vss_index.py:268` | `np.array([n in entity_set for n in index.names])` python-loop mask |
| FTS bench | `bench/fts_duckdb_parity.py:178` | `json.loads(c["embedding"])` on BLOB → every section skipped → empty matrix → `AxisError` crash at line 184 |

`_parquet_rows` has exactly one caller; the restore change is a
single-function body swap behind an unchanged signature.

## 3. Evidence (measured 2026-09-10, this box)

**Snapshot scale**: 13 SQLite-side parquet tables, ~77k rows / ~66 MB;
`note_search_content.parquet` dominates (61.8 MB, 16,479 rows).

**Restore read** (`_parquet_rows` body, `note_search_content.parquet`):

| Path | Time | Result |
|---|---|---|
| pandas `astype(object).where(notna)` + `itertuples` | 1.07 s | 16,479 row tuples |
| arrow `pq.read_table` + per-column `to_pylist` + `zip(*)` | 0.42 s | 16,479 row tuples, **equality: True** |

**Export** (three biggest tables, read-only live `research.db`):
`pd.read_sql` + `from_pandas` 0.31 s → `fetchall` + column-wise `pa.array`
0.19 s.

**Type mapping delta** (export path, all 13 tables):

- TEXT: `large_string` → `string`; every reader in the stack (DuckDB
  `read_parquet` incl. analytics + textconv, pandas, polars) identical.
- INTEGER (`id` on company_metrics/events/quotes): `int64` both paths
  today — only because those columns are NOT NULL. The pandas path
  promotes a NULLable INTEGER to `float64` (NULL→NaN→REAL on restore);
  the arrow path emits `int64` + real nulls. Latent hazard, closed
  permanently (§S4 tests pin it).
- BLOB: `large_binary` → `binary`; REAL: `double` both paths (today
  0 nulls / 0 NaNs, checked programmatically).

**Matrix lane dead**: `note_search.embedding` is 100% BLOB (16,479/
16,479). `_refresh_embed_matrix` dies in `json.loads` and returns `None`
silently on every rebuild apply — `memory/embed_matrix.f32` (built
2026-09-10 21:21, pre-migration) never refreshes again. The
`app.py:_flat_knn_map` staleness gate only checks id coverage
(16,479/16,479 covered) so the frozen matrix keeps being served; vectors
are currently bit-identical (sampled — #223 was lossless), but any note
edited from now on gets silently stale fallback similarity, undetectable
by design.

**Matrix rebuild, JSON vs binary** (live BLOBs, 16,479 × 384):

| Path | Time |
|---|---|
| per-row `load_vec` + `np.array` (tolerant-codec fix) | 0.504 s |
| `np.frombuffer(b"".join(blobs), f32).reshape(n, -1)` (proposed) | **0.018 s (28x)** |

(The pre-#223 `json.loads` path cannot run at all — it throws on BLOB.)

**Bench dead on live corpus**: `build_vector_leg` parses 0/16,479
sections (UnicodeDecodeError is a ValueError subclass → silently
`continue`), then `np.linalg.norm(np.asarray([]), axis=1)` raises
`numpy.exceptions.AxisError` — `main()` crashes before any measurement.
Same root cause as the matrix lane: a `json.loads` reader not migrated
in #223.

## 4. Design

### S1 — Export: column-wise `pa.array` from the sqlite cursor

```python
cur = con.execute(f"SELECT * FROM [{t}]")
names = [d[0] for d in cur.description]
cols = list(zip(*cur.fetchall()))
table = pa.table({n: pa.array(c) for n, c in zip(names, cols)})
```

Replaces `pd.read_sql` + `from_pandas`. `pq.write_table(...,
compression="zstd")` and the #174/#176 level policy untouched. Peak
memory drops (rows + arrow table instead of rows + DataFrame + arrow
table); one-time byte churn of the 13 git-tracked parquet files on the
next `make snapshot` (regenerated each run regardless).

**Mixed-storage-class guard (fail loud):** SQLite is dynamically typed;
`pa.array` raises `ArrowInvalid` where `pd.read_sql` silently produced an
object column. Catch per table, re-raise with table name, column, and a
`SELECT typeof(<col>), count(*) ... GROUP BY 1` diagnostic. A snapshot
must not silently re-type data.

### S2 — Restore: `pq.read_table` + column-wise `to_pylist`

`_parquet_rows` body becomes:

```python
table = pq.read_table(path)
rows = list(zip(*[table[c].to_pylist() for c in table.column_names]))
return list(table.column_names), rows
```

Parquet nulls → `None` natively (no `astype(object)` full-table copy).
Signature, caller, downstream `executemany`/FTS5-rebuild unchanged.

**NaN guard:** a `double` column containing actual NaN (not null) reads
back as `float("nan")`, not `None`. Add an explicit per-float-column pass
(`pc.if_else(pc.is_nan(col), None, col)` + `fill_null`, float-typed
columns only) so restore inserts NULL deterministically (today's
correct-for-accidental-reasons SQLite NaN→NULL coercion becomes
explicit, test-pinned).

### S3 — Matrix lane revival (BLOB-native, vectorized)

One shared builder in `embed_matrix.py`, used by both callers:

```python
def matrix_from_rows(rows) -> tuple[list[str], np.ndarray]:
    blobs = [r[1] for r in rows if isinstance(r[1], (bytes, bytearray))]
    if len(blobs) == len(rows):  # fast path: all BLOB
        emb = np.frombuffer(b"".join(blobs), dtype=np.float32)
        return [r[0] for r in rows], emb.reshape(len(blobs), -1)
    ...  # mixed/legacy: per-row vec_codec.load_vec fallback
```

- `rebuild_note_search._refresh_embed_matrix` (line 590) and
  `embed_matrix.from_note_search` (line 210) both route through it —
  0.018 s vs the dead path, TEXT fallback via the #223 tolerant codec
  `load_vec` for un-migrated DBs (same contract as every other reader
  #223 converted).
- Keep the best-effort never-gate-the-rebuild contract, but stop being
  SILENT: parse failures log one warning line (the current blanket
  `except` is exactly what froze the lane unnoticed).
- `from_note_search` also adopts the sectioned key
  (`file_path || '#' || anchor`, as `_refresh_embed_matrix` already
  does) — it still keys by bare `file_path`, a pre-sectioning shape.

### S4 — Tests (`tests/test_snapshot.py` + matrix/bench)

- Existing snapshot suites stay green: roundtrips, pandas readability
  (pandas reads arrow `string` fine), FTS5 shadow exclusion, verify
  mismatch/pass, DuckDB-side determinism (untouched).
- New: NULLable-INTEGER roundtrip (`(1, NULL, 2)` → parquet `int64`
  with nulls → restores as `typeof = integer/null/integer`).
- New: NULL-vs-NaN double roundtrip (both restore to SQLite NULL).
- New: mixed-storage-class export raises the loud `ArrowInvalid` error.
- New (matrix): refresh over a BLOB corpus rewrites changed rows and
  returns a count (not `None`); TEXT-legacy corpus falls back via
  `load_vec`; parse failure logs (not silent).
- New (bench): `build_vector_leg` on a BLOB corpus returns a non-empty
  `(ids, matrix f32)` and an empty corpus raises a loud error naming
  the storage type — never the empty-matrix `AxisError`.

### S5 — `vss_index.py:268` mask

`np.array([n in entity_set for n in index.names])` → `np.isin(
np.asarray(index.names), list(entity_set))`. ~1.1k entities — cosmetic
vectorization, rides along.

### S6 — `fts_duckdb_parity.py` repair (foreign-harness orphan)

Fix list (bench is currently UNRUNNABLE on the live corpus):

1. **Line 178**: `json.loads(c["embedding"])` → route through
   `helpers.core.vec_codec.load_vec` (or the S3 builder) — the #223
   codec seam this file missed. This is the crash.
2. **Line 183**: `np.asarray(vecs, dtype=np.float64)` → `float32` —
   production cosine is f32; the parity bench must mirror it.
3. **Empty-corpus guard**: 0 parsed sections must raise a loud error
   ("embedding storage changed?") — the silent-empty path is exactly
   what produced the `AxisError`.
4. **Line 179**: `except TypeError, ValueError:` — PEP 758 parens-less
   form is 3.14-only syntax; make it `except (TypeError, ValueError):`
   (free portability).
5. **Line 70**: dead `# noqa: F401` import of `fts_match_expr` inside
   `fts5_search` (the real import lives in `main`); drop it.
6. **Docstring line 11**: "stored 384-d JSON" → BLOB (stale post-#223).

Not broken, leave as-is: `WINDOWS=1024` still matches `app.py:875`
inner LIMIT (parity holds); direct `sqlite3.connect` is allowlisted
(test_static_checks.py:275); `_DDB_CON` bench-lifetime connection;
`INSTALL fts` (first run needs the extension cache — same as every
DuckDB fts use here).

Record a fresh baseline run after repair — the pre-#223 numbers are
from a corpus that no longer exists.

### S7 — Dependency surface

`snapshot_db.py` drops its `import pandas` (export + restore were the
only uses in the module). Pandas stays a project dependency; no
pyproject change.

## 5. Alternatives considered

- **DuckDB ATTACH sqlite + `INSERT ... SELECT FROM read_parquet` for
  restore** — rejected: DuckDB's strict timestamp parsing on legacy rows
  is the documented reason the sqlite3+pyarrow path exists
  (snapshot_db.py:699); the write-side type mapping re-opens the
  fidelity questions this change closes.
- **polars** — new dependency, zero functional gain over pyarrow.
- **Retire the embed_matrix lane instead of reviving it** (it is the
  "fallback of the fallback") — rejected for now: the Mojo FlatKNN /
  100M-element contract (corpus_embeddings_scaling S2b/c) is built on
  the aligned matrix file. Revisit trigger: if the vec0 mirror becomes
  the only KNN substrate, delete the lane wholesale (matrix + refresh +
  `_flat_knn_map`) rather than maintain it.
- **`enrich_relations._parse_holders_dataframe` `iterrows()`** — real
  antipattern but ~10-row frames; fix is `to_dict("records")`, not
  PyArrow. Out of scope.
- **`get_tickers._print_history_section` `iterrows()`** — display-only,
  ~30 rows. Out of scope.

## 6. Acceptance criteria

- `make snapshot` green; snapshot-check + integrity gates pass on the
  regenerated (one-time churned) parquet set.
- Restore roundtrip tests (existing + §S4) green; row-tuple equality
  with pre-change restore output asserted once during execution across
  all 13 tables.
- A live note rebuild reports `stats["matrix_rows"]` != None and the
  matrix mtime advances; hash-gated rewrite still skips unchanged rows.
- The repaired bench runs end-to-end on the live corpus (non-empty
  vector leg) and a fresh baseline table is recorded.
- Measured before/after (export leg, restore read leg, matrix rebuild,
  total `make snapshot` wall time) appended at execution.
- `make qa` 9/9.

## 7. Risks

- **One-time parquet byte churn** (schema annotation shift) → one noisy
  `git add snapshots/` commit; subsequent snapshots deterministic. Low.
- **Future mixed storage classes fail the export loudly** instead of
  silently re-typing — intended; the diagnostic makes the fix
  mechanical. Medium-low.
- **NaN semantics change on restore** — behavior-identical end to end,
  test-pinned. Low.
- **Matrix revival re-adds a 25 MB derived artifact** — derived state,
  snapshot-excluded, regenerates; unchanged class from
  corpus_embeddings_scaling. Low.
- **Bench numbers move vs the pre-#223 run** — expected (f32 parity leg
  + fresh corpus); the fresh baseline replaces them. Low.

## 8. Non-goals

- No DuckDB-side export changes (already native COPY).
- No change to verify (counts-only by design), FTS5 rebuild, binary zstd
  branch, or the parquet textconv.
- No Mojo FlatKNN changes, no vec0 mirror changes, no app.py fallback
  chain redesign.
- No schema/DDL changes, no compression-level changes, no new
  dependencies.

## Execution results (2026-09-11)

All slices S1–S7 landed as designed; measured on this box:

| Leg | Before | After |
|---|---|---|
| Restore read (`note_search_content`, 16,479 rows) | 1.07 s (pandas astype+itertuples) | 0.42 s (arrow to_pylist) |
| Export (3 biggest tables) | 0.31 s (read_sql+from_pandas) | 0.19 s (column-wise pa.array) |
| Matrix rebuild (16,479 × 384) | 0.504 s per-row decode (JSON path dead) | 0.018 s (concat+frombuffer) |
| Live `_refresh_embed_matrix` pass | `None` silently (dead since #223) | 0.164 s, `rewritten=0` |

Fidelity: old-vs-new restore row tuples **bit-equal across all 13
SQLite-side tables** (pandas path replicated inline vs `_parquet_rows`).
`make snapshot` regen + verify: 43 tables OK; one-time schema
annotation churn (`large_string→string`, `large_binary→binary`) masked
by the snapshots/ skip-worktree flags until `git add`;
`note_search_content.parquet` 61.9 → 31.4 MB — the first snapshot
export carrying BLOB-era embeddings through the new path.

Matrix lane: alive again — refresh runs the hash gate end-to-end
(`rewritten=0` is correct: #223 was lossless, so pre-migration matrix
content matches); failures now print one stderr warning instead of the
blanket silent `return None` that froze the lane. `from_note_search`
keys sectioned (`file_path#anchor`), matching the rebuild writer.

Bench (fresh full baseline, 27-query eval set, post-#223 corpus — the
pre-#223 numbers died with the JSON corpus):

| Arm | FTS5 | DuckDB fts |
|---|---|---|
| Lexical recall@5 | **25/27 (0.93)** | 23/27 (0.85) |
| Hybrid + shared vector leg | **27/27** | **27/27** |
| Warm latency/query | **8.4 ms** | 32.5 ms |
| Index build | incremental | ~13.5 s per corpus |

B3 verdict stands with numbers: keep FTS5 as the lexical engine —
DuckDB fts adds no hybrid recall and costs ~4x query latency plus a
full rebuild per corpus change.

Riders folded in during gates: two `# ty: ignore[unresolved-attribute]`
for dynamically generated `pyarrow.compute` functions; ruff-format
footprint on two #223 files (`note_ab_granite.py`,
`migrate_embedding_blob.py`); `S608` noqas in
`test_embedding_blob_migration.py` (test-local constant tables);
proposal frontmatter needed the required-null `executed`/
`completed_md` keys (OKF `frontmatter.proposal.v1.json`).

Verification: 10 new tests (snapshot NULL-int / NaN-vs-NULL /
mixed-storage-class roundtrips; matrix BLOB fast-path, TEXT fallback,
loud-empty, refresh-over-BLOB; bench vector leg decode / loud-empty /
legacy-TEXT) + full suites green — `make qa` 9/9 (2,741 passed).
