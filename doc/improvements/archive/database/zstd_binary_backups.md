---
title: zstd binary backups — `.gz` → `.zst` for db-backup/ recovery artifacts
status: executed
filed: '2026-08-29'
executed: '2026-08-29'
completed_md: '176'
area: helpers/maintenance/snapshot_db.py` (binary branch)
---

# zstd binary backups — `.gz` → `.zst` for db-backup/ recovery artifacts

> **Measured outcome (2026-08-29):** snapshot binary branch now zstd
> (library-default level, no explicit switches). One full `make snapshot`:
> 8.0 s → **2.35 s**; `research.snapshot.db.zst` 23.1→22.4 MB,
> `graph.snapshot.duckdb.zst` 3.41→3.39 MB, `embed_store.snapshot.db.zst`
> 15.94→16.37 MB (BLOB-heavy table: zstd -3 trades ~3% size for ~3×
> speed — the accepted trade). Old `.gz` trio deleted after green
> verification; `snapshot-check` green on the .zst artifacts.
**Date:** 2026-08-29 · **Status:** EXECUTED 2026-08-29 (completed.md #176) ·
**Area:** `helpers/maintenance/snapshot_db.py` (binary branch) + restore/verify
paths

> Companion to `../archive/database/maint_full_single_snapshot.md` (#174),
> which moved the SQLite *parquet* codec to zstd (container-transparent,
> landed) but deliberately deferred the *binary backup* switch: unlike
> parquet, the `.gz` recovery copies ARE the format — changing codec means
> changing extension, readers, and the documented manual-recovery command.
>
> **User decision 2026-08-29: default is ZSTD LEVEL 3 everywhere** — the
> -9 disk win over -3 is ~10% for 3-4× the CPU; -3 is ~10× faster than
> gzip -9 at ~equal size, which is the trade worth having. Also landed
> same day: DuckDB parquet COPY standardized snappy → zstd, making zstd
> the single codec of both parquet trees. See §2 for how "level 3
> everywhere" maps onto library defaults (no explicit level switches).

## 1. Measured case (2026-08-29, full 4-path bench, gzip -9 = today's code)

| Source (raw) | gzip -9 | zstd -3 | zstd -9 | decompress g9 → z9 |
|---|---|---|---|---|
| `research.db` (63.3 MB) | 6.70 s → 23.4 MB | 0.62 s → 22.4 MB | 2.35 s → 20.8 MB | 0.31 s → 0.15 s |
| `embed_store.db` (36.6 MB) | 6.38 s → 15.8 MB | 0.48 s → 16.2 MB | 2.03 s → 15.4 MB | 0.21 s → 0.12 s |
| `graph.duckdb` (11.0 MB) | 0.24 s → 3.4 MB | 0.05 s → 3.4 MB | 0.10 s → 3.3 MB | 0.04 s → 0.01 s |

zstd -9 is smaller AND ~3× faster on every path, but its ~10% size win
over -3 costs 3-4× the CPU — **level 3 is the default** (user decision
2026-08-29): ~10× faster than gzip -9 at ~equal size, decompression ~2×
faster. With #174's single-snapshot maint-full, the gzip branch (~13 s of
the 32.3 s run) is now the largest remaining leg — zstd -3 cuts it to
~1.2 s. **zstd -19 / pzstd are slower than gzip -9 at these file sizes —
do not use.**

## 2. Level policy — NO explicit level switch anywhere (crucial, measured)

"Level 3 everywhere" is satisfied by each stack's own default — passing
an explicit `-3` would be a magic constant spelled three different ways
across three APIs, and in one case it would actively hurt:

| Stack | Our use | zstd default level | Explicit switch needed? |
|---|---|---|---|
| stdlib `compression.zstd` (PEP 784) | future `.zst` backup writers | **3** | No — default is what we want |
| DuckDB `COPY (… COMPRESSION ZSTD)` | DuckDB parquet blobs | **3** (a `COMPRESSION_LEVEL` knob exists on 1.5.4 if ever needed) | No |
| pyarrow `pq.write_table(compression="zstd")` | SQLite parquet export | **1** — the one stack whose default is NOT 3 | **No — and deliberately so**, see below |

pyarrow's level 1 stays because **measured on this corpus
(2026-08-29, the two heavy tables)**:

| Table | zstd L1 | zstd L3 | Verdict |
|---|---|---|---|
| `note_search_content` (note text) | 0.11 s / 8.50 MB | 0.21 s / 8.44 MB | L3 gains 0.06 MB for 2× the time — pointless |
| `company_embeddings` (float32 BLOBs) | 0.05 s / **3.54 MB** | 0.10 s / **4.03 MB** | **L3 is 14% BIGGER** — zstd's higher-level strategies are tuned for text-like redundancy and lose on float-byte data |

So the trap this records for posterity: "just pass level 3" sounds like
strict standardization but would have silently bloated the BLOB-heavy
git-tracked artifact. Policy: **library defaults everywhere; any future
level change is a deliberate, benchmarked, per-site decision** (the
backup writers get their level 3 from their own defaults).

## 3. Design

**D1 — writer:** `create_snapshot` / `create_duckdb_snapshot` /
`_cmd_create`'s embed-store branch write via Python 3.14's stdlib
`compression.zstd` (PEP 784 — no new dependency) at the LIBRARY-DEFAULT level (plain `zstd`, no explicit level switch — the user's final call; see §2 level policy).
Artifacts rename in lockstep:
`db-backup/research.snapshot.db.zst`, `graph.snapshot.duckdb.zst`,
`embed_store.snapshot.db.zst` (+ the legacy `<db>_vec.db.zst` sibling
name for the pre-#166 branch).

**D2 — readers:** `verify_snapshot` / `verify_duckdb_snapshot` /
`--restore` decompress via `compression.zstd.open`; the embed-store
reuse rider's `_gz_source_size` becomes the zstd frame-size check
(`compression.zstd` exposes stream info; fall back to "always re-gzip"
on any read failure — the fail-safe direction).

**D3 — operator surface:** the documented manual recovery command in
`snapshot_db.py`'s module docstring + `doc/schema.md` +
`doc/improvements/archive/database/…` pointers changes from
`gunzip -c` to `zstd -dc` (zstd CLI is ubiquitous; already on this box).
`--check` output/log lines drop the `.gz` naming.

**D4 — cutover:** one-time: old `.gz` artifacts deleted by hand (or kept
one cycle alongside — the writer emits `.zst` names only; nothing reads
`.gz` after D2, so retention is optional). `snapshot-check` immediately
after cutover is the acceptance gate.

## 4. Costs & risks

- Effort: ~half a day incl. tests (mechanical swap + name changes; the
  reuse-rider fingerprint check is the only subtle bit).
- The restore path is disaster recovery — the risk is a bug in the NEW
  reader discovered precisely when the old artifact is already gone.
  Mitigation: D4 keeps one `.gz` cycle, and `compression.zstd` is
  stdlib-tested; round-trip tests cover every artifact kind before
  cutover.
- `db-backup/` is gitignored/local-only — no published-artifact churn
  (unlike #174's parquet re-encode).

## 5. Out of scope / already landed

- **LANDED 2026-08-29:** DuckDB parquet COPY snappy → `COMPRESSION ZSTD`
  (level 3) in `export_parquet_duckdb` — both parquet trees now standard
  on one codec; one-time re-encode of the 28 duckdb blobs (hidden behind
  the snapshots/ skip-worktree flag until `git add`).
- Further SQLite parquet codec work (zstd landed in #174).
- Deleting the frozen `research.snapshot.db_vec.db.gz` orphan (manual,
  independent).
