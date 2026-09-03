#!/usr/bin/env python3
"""
SQLite DB maintenance helper for memory/research.db.

Performs, in this order:
  1. Snapshot settings + pre-maintenance metrics (size, pages, freelist, stat-staleness, indexes)
  2. BACKUP            (recovery point — BEFORE any mutation)
  3. VACUUM            (reclaim free pages; rebuilds file + indexes)
  4. ANALYZE           (refresh sqlite_stat1 planner stats)
  5. REINDEX           (rebuild indexes; no-op after VACUUM but harmless)
  6. Post-maintenance metrics
  7. integrity_check + foreign_key_check   (verify final state)
  8. (optional) --sync-check shells out to verify_notes.py + database_integrity_check.py

Note: index *usage* cannot be detected (SQLite keeps no per-index read counters),
so only structural *redundancy* is reported, never "unused".

Produces zstd-compressed PRE-MUTATION recovery points (snapshot_parallel_and_compressed_backups.md
D2; stdlib compression.zstd, library-default level):
``db-backup/research_backup.db.zst`` (+ the embed-store twin
``embed_store_backup.db.zst``, or the legacy ``<db>_vec.db.zst`` when it
exists) and ``db-backup/graph_backup.duckdb.zst`` (DuckDB cache). These
are deliberately kept distinct from ``snapshot_db.py``'s
``db-backup/research.snapshot.db.zst`` and ``db-backup/graph.snapshot.duckdb.zst``
(which are POST-mutation, gzipped, and git-tracked). Distinct purposes:
  - ``research_backup.db.zst`` / ``graph_backup.duckdb.zst``
                                 : recovery if VACUUM corrupts (rare but
                                   possible; overwritten each run, not
                                   versioned). Manual restore:
                                   ``zstd -dc db-backup/research_backup.db.zst > memory/research.db``
  - ``research.snapshot.db.zst`` / ``graph.snapshot.duckdb.zst``
                                 : reconstructable state for git history
                                   (taken after maintenance completes).

See also: ``helpers/maintenance/maint.py`` — the orchestrator that runs
db_maint → snapshot_db → graph-rebuild in the right order. Prefer
``make maint`` over invoking this script directly.

Usage:
  python3 helpers/maintenance/db_maint.py [--db PATH] [--backup PATH] [--dry-run]
                                          [--log LEVEL] [--sync-check]
"""

import argparse
import logging
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

_SYNC_HELPERS = [
    ("verify_notes", "helpers/validators/verify_notes.py"),
    ("database_integrity_check", "helpers/misc/database_integrity_check.py"),
]

# Decode numeric PRAGMA values to human-readable labels.
_SYNC_MAP = {0: "OFF", 1: "NORMAL", 2: "FULL", 3: "EXTRA"}
_AUTO_VACUUM_MAP = {0: "NONE", 1: "FULL", 2: "INCREMENTAL"}


def _fmt_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _pragma_ident(name: str) -> str:
    """PRAGMA arguments can't be bound; validate the identifier to avoid injection."""
    if not name or not all(c.isalnum() or c == "_" for c in name):
        raise ValueError(f"Invalid identifier for PRAGMA: {name!r}")
    return name


# Repo root: helpers/maintenance/db_maint.py -> parents[2]. Required for the
# lazy `from helpers.core.vec_search import EMBED_DB_PATH` in
# _backup_embed_store — without it the script crashes with ModuleNotFoundError
# when run as a subprocess (make maint step 1).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class DBMaintainer:
    """Encapsulates maintenance steps for a SQLite database (+ optional DuckDB cache)."""

    def __init__(
        self,
        db_path: Path,
        backup_path: Path,
        dry_run: bool = False,
        logger: logging.Logger | None = None,
        duckdb_path: Path | None = None,
        duckdb_backup_path: Path | None = None,
    ):
        self.db_path = db_path
        self.backup_path = backup_path
        self.dry_run = dry_run
        self.logger = logger or logging.getLogger(__name__)
        # Optional DuckDB graph cache path. When set and the file exists,
        # `run()` issues CHECKPOINT + VACUUM on it after the SQLite steps.
        self.duckdb_path = duckdb_path
        # Optional DuckDB pre-mutation recovery backup path. Mirrors the
        # SQLite backup_path semantics: a non-versioned, non-compressed
        # copy taken BEFORE any mutation so VACUUM corruption is
        # recoverable. Distinct from snapshot_db.py's gzipped snapshot.
        self.duckdb_backup_path = duckdb_backup_path

    def _log(self, level: int, msg: str) -> None:
        if self.logger:
            self.logger.log(level, msg)
        else:
            print(msg, flush=True)

    def ensure_paths(self) -> None:
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {self.db_path}")
        self.backup_path.parent.mkdir(parents=True, exist_ok=True)
        if self.duckdb_backup_path:
            self.duckdb_backup_path.parent.mkdir(parents=True, exist_ok=True)

    # ----- diagnostics (read-only) ----------------------------------------

    def settings(self, conn) -> dict:
        names = [
            "journal_mode",
            "synchronous",
            "cache_size",
            "auto_vacuum",
            "page_size",
            "encoding",
            "user_version",
            "journal_size_limit",
            "mmap_size",
            "wal_autocheckpoint",
            "temp_store",
        ]
        snap = {}
        for p in names:
            try:
                row = conn.execute(f"PRAGMA {p}").fetchone()
                snap[p] = row[0] if row else None
            except sqlite3.Error:
                snap[p] = None
        if isinstance(snap.get("synchronous"), int):
            snap["synchronous"] = _SYNC_MAP.get(snap["synchronous"], snap["synchronous"])
        if isinstance(snap.get("auto_vacuum"), int):
            snap["auto_vacuum"] = _AUTO_VACUUM_MAP.get(snap["auto_vacuum"], snap["auto_vacuum"])
        return snap

    def metrics(self, conn) -> dict:
        page_size = conn.execute("PRAGMA page_size").fetchone()[0] or 0
        pages = conn.execute("PRAGMA page_count").fetchone()[0] or 0
        freelist = conn.execute("PRAGMA freelist_count").fetchone()[0] or 0
        wasted_pct = (freelist / pages * 100.0) if pages else 0.0
        # P3.6: WAL size monitoring
        wal_path = self.db_path.parent / (self.db_path.name + "-wal")
        wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
        return {
            "file_size": self.db_path.stat().st_size,
            "pages": pages,
            "page_size": page_size,
            "freelist": freelist,
            "wasted_bytes": freelist * page_size,
            "wasted_pct": wasted_pct,
            "wal_bytes": wal_bytes,
        }

    def stat_staleness(self, conn) -> dict:
        """Compare sqlite_stat1 row estimates to live COUNT(*) per table."""
        try:
            rows = conn.execute("SELECT tbl, stat FROM sqlite_stat1").fetchall()
        except sqlite3.Error:
            return {}  # ANALYZE has never run
        est_by_tbl = {}
        for tbl, stat in rows:
            if tbl in est_by_tbl or not stat:
                continue
            try:
                est_by_tbl[tbl] = int(stat.split()[0])
            except ValueError, IndexError:
                pass
        out = {}
        for tbl, est in est_by_tbl.items():
            try:
                live = conn.execute(
                    f"SELECT COUNT(*) FROM {_pragma_ident(tbl)}"  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                ).fetchone()[0]
            except sqlite3.Error:
                live = None
            out[tbl] = {
                "stat": est,
                "live": live,
                "stale": live is not None and est != live,
            }
        return out

    def index_report(self, conn) -> list[dict[str, Any]]:
        """Per-table index list + structural redundancy detection.

        An index A is flagged 'redundant' only if it is a user-created, non-unique,
        non-partial index whose column list is a leading prefix of some other index B,
        AND each prefixed column matches on collation. Unique / PK / auto / partial
        indexes are never flagged (they enforce constraints).

        Collation matters: ``PRAGMA index_info`` does not expose it, so a
        ``COLLATE NOCASE`` index on a column also covered by a BINARY-collated PK
        auto-index would be falsely flagged. We use ``PRAGMA index_xinfo`` (which
        reports collation per column) and require a collation match for a prefix
        to count as redundant — a NOCASE index is a genuinely different index from
        the BINARY PK and can be load-bearing (e.g. the entities.name resolver).
        """
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        report: list[dict[str, Any]] = []
        for table in tables:
            # live row count for this table (for empty-table index detection)
            try:
                row_count = conn.execute(
                    f"SELECT COUNT(*) FROM {_pragma_ident(table)}"  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                ).fetchone()[0]
            except sqlite3.Error:
                row_count = None
            empty_table = row_count == 0
            idx_rows = conn.execute(f"PRAGMA index_list({_pragma_ident(table)})").fetchall()
            # cols: seq, name, unique, origin, partial
            indexes: list[dict[str, Any]] = []
            for _seq, name, unique, origin, partial in idx_rows:
                # xinfo rows: (seqno, cid, name, desc, collation, key)
                # key=1 marks a key column (cid>=0); key=0 is the auxiliary
                # rowid/extra column. We only compare key columns.
                xinfo = [
                    r
                    for r in conn.execute(f"PRAGMA index_xinfo({_pragma_ident(name)})").fetchall()
                    if r[5] == 1  # key columns only
                ]
                cols = [r[2] for r in xinfo]
                collations = [r[4] for r in xinfo]
                indexes.append(
                    {
                        "name": name,
                        "columns": cols,
                        "collations": collations,
                        "unique": bool(unique),
                        "origin": origin,
                        "partial": bool(partial),
                        "redundant_with": None,
                        "empty_table": empty_table,
                    }
                )
            # redundancy check — column prefix AND per-column collation must match
            for a in indexes:
                if not (a["origin"] == "c" and not a["unique"] and not a["partial"]):
                    continue  # only consider droppable user indexes as candidates
                n = len(a["columns"])
                for b in indexes:
                    if b is a or b["partial"]:
                        continue
                    if (
                        len(b["columns"]) >= n
                        and b["columns"][:n] == a["columns"]
                        and b["collations"][:n] == a["collations"]
                    ):
                        a["redundant_with"] = b["name"]
                        break
            report.append({"table": table, "row_count": row_count, "indexes": indexes})
        return report

    # ----- maintenance -----------------------------------------------------

    def _backup(self, conn) -> int:
        """WAL-consistent online backup, stored zstd-compressed
        (``<backup_path>.zst``; the plain copy exists only as a temp
        staging file)."""
        from helpers.core.zstd_io import compress_file, zst_path

        dst = zst_path(self.backup_path)
        with tempfile.NamedTemporaryFile(
            suffix=".db", dir=self.backup_path.parent, delete=False
        ) as tf:
            tmp_path = Path(tf.name)
        try:
            bconn = sqlite3.connect(str(tmp_path))
            try:
                with bconn:
                    conn.backup(bconn)
            finally:
                bconn.close()
            if dst.exists():
                dst.unlink()
            return compress_file(tmp_path, dst)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _backup_embed_store(self) -> int:
        """Paired recovery copy of the consolidated embed store.

        Since the embed_store consolidation the vec0 mirror + pooled
        content-hash cache live in ONE SQLite database
        (``memory/embed_store.db``, see helpers/core/vec_search.py); a
        research_backup.db without it restores to a cold re-embed (~minutes
        of CPU). Resolution order: a per-db ``<db>_vec.db`` sibling wins
        (pre-migration clones and tests that seed one); otherwise the
        shared EMBED_DB_PATH store is backed up as ``embed_store_backup.db``
        beside this run's backup. Same WAL-consistent sqlite online-backup
        as _backup; absent state just skips."""
        vec_src = self.db_path.with_name(self.db_path.name + "_vec.db")
        if vec_src.exists():
            src, dst = (
                vec_src,
                self.backup_path.with_name(self.backup_path.name.replace(".db", "_vec.db")),
            )
        else:
            from helpers.core.vec_search import EMBED_DB_PATH

            src = Path(EMBED_DB_PATH)
            if not src.exists():
                self._log(logging.INFO, f"Embed store absent — backup skipped ({src})")
                return 0
            dst = self.backup_path.parent / "embed_store_backup.db"
        from helpers.core.zstd_io import compress_file, zst_path

        zst_dst = zst_path(dst)
        with tempfile.NamedTemporaryFile(
            suffix=".db", dir=self.backup_path.parent, delete=False
        ) as tf:
            tmp_path = Path(tf.name)
        try:
            sconn = sqlite3.connect(str(src))
            bconn = sqlite3.connect(str(tmp_path))
            try:
                with bconn:
                    sconn.backup(bconn)
            finally:
                bconn.close()
                sconn.close()
            if zst_dst.exists():
                zst_dst.unlink()
            size = compress_file(tmp_path, zst_dst)
            self._log(logging.INFO, f"Embed store backed up to {zst_dst}")
            return size
        finally:
            tmp_path.unlink(missing_ok=True)

    def _backup_duckdb(self) -> int:
        """Pre-mutation recovery copy of the DuckDB cache file,
        stored zstd-compressed (``<duckdb_backup_path>.zst``).

        DuckDB doesn't expose an online-backup API like SQLite's
        ``conn.backup()``, but the canonical safe pattern is:
        open read-only, force CHECKPOINT (flushes the WAL into the
        main file), close, then ``shutil.copy2`` the file. With no
        writer active and the WAL merged, the file is quiescent.

        Version-sensitive assumption: read-only CHECKPOINT relies on
        DuckDB ≥ 1.5 allowing a reader connection to flush the WAL. The
        fallback (catch duckdb.Error → copy file as-is) degrades
        gracefully if a bump rejects it. See doc/design/graph_design.txt §9.3
        (Bundle O3) for the full caveat + how to re-test on pin bumps.

        See https://ducklake.select/docs/stable/duckdb/guides/backups_and_recovery.html
        """
        import shutil

        try:
            import duckdb
        except ImportError:
            self._log(logging.WARNING, "duckdb not installed; skipping DuckDB backup")
            return 0
        # Open read-only and CHECKPOINT so the WAL is merged into the
        # main .duckdb file before we copy it. read-only CHECKPOINT is
        # supported on DuckDB 1.5+ (it flushes the WAL even from a
        # reader connection). Re-test on pin bumps — see §17.11 (O3).
        try:
            con = duckdb.connect(str(self.duckdb_path), read_only=True)
            try:
                con.execute("CHECKPOINT;")
            except duckdb.Error as e:
                self._log(logging.WARNING, f"read-only CHECKPOINT failed ({e}); copying as-is")
            finally:
                con.close()
        except duckdb.Error as e:
            self._log(logging.WARNING, f"read-only connect failed ({e}); copying file as-is")
        src = self.duckdb_path
        bp = self.duckdb_backup_path
        if src is None or bp is None:
            return 0
        from helpers.core.zstd_io import compress_file, zst_path

        zst_bp = zst_path(bp)
        with tempfile.NamedTemporaryFile(suffix=".duckdb", dir=bp.parent, delete=False) as tf:
            tmp_path = Path(tf.name)
        try:
            shutil.copy2(src, tmp_path)
            if zst_bp.exists():
                zst_bp.unlink()
            return compress_file(tmp_path, zst_bp)
        finally:
            tmp_path.unlink(missing_ok=True)

    def run(self) -> dict:  # noqa: C901
        steps = [
            "SNAPSHOT",
            "BACKUP",
            "VACUUM",
            "ANALYZE",
            "REINDEX",
            "wal_checkpoint(TRUNCATE)",
            "SNAPSHOT",
            "integrity_check",
            "foreign_key_check",
        ]
        if self.duckdb_path and self.duckdb_path.exists():
            steps.extend(["DuckDB BACKUP", "DuckDB CHECKPOINT", "DuckDB VACUUM"])
        if self.dry_run:
            return {"status": "dry_run", "steps": steps}
        self.ensure_paths()
        # autocommit mode: VACUUM/ANALYZE/REINDEX cannot run inside a
        # transaction, so we open with isolation_level=None directly rather
        # than via helpers.core.db.connect() (which opens in deferred mode
        # and exposes no isolation_level kwarg). FK enforcement is irrelevant
        # here — this connection issues no INSERT/UPDATE/DELETE, only DDL +
        # PRAGMA diagnostics (foreign_key_check reports violations regardless
        # of the connection's enforcement flag).
        conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        try:
            settings = self.settings(conn)
            before = self.metrics(conn)
            before_staleness = self.stat_staleness(conn)
            indexes = self.index_report(conn)

            self._log(logging.INFO, f"Backing up to {self.backup_path}")
            backup_size = self._backup(conn)
            self._backup_embed_store()

            # P2.5: incremental vacuum when auto_vacuum==INCREMENTAL and freelist exists.
            # Full VACUUM rewrites 31 MB file (~0.6s); incremental_vacuum reclaims only freelist pages (~0.1s).
            # When auto_vacuum==NONE (current live DB), we still do full VACUUM. The one-time migration
            # to INCREMENTAL is via --migrate-incremental (PRAGMA auto_vacuum=INCREMENTAL + VACUUM).
            auto_vac = None
            try:
                auto_vac = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
            except Exception:
                auto_vac = 0
            freelist_before = before.get("freelist", 0) if isinstance(before, dict) else 0
            if auto_vac == 2 and freelist_before > 0:  # 2 == INCREMENTAL
                self._log(logging.INFO, f"incremental_vacuum({freelist_before} pages)")
                try:
                    # incremental_vacuum(N) reclaims at most N freelist pages; N=0 means all
                    conn.execute(f"PRAGMA incremental_vacuum({int(freelist_before)})")
                    self._log(logging.INFO, "incremental_vacuum done (no full rewrite)")
                except sqlite3.Error as e:
                    self._log(
                        logging.WARNING, f"incremental_vacuum failed ({e}); falling back to VACUUM"
                    )
                    conn.execute("VACUUM")
            else:
                if freelist_before == 0:
                    self._log(logging.INFO, "VACUUM skipped (freelist=0, no wasted pages)")
                else:
                    self._log(
                        logging.INFO,
                        f"VACUUM (freelist={freelist_before} pages, auto_vacuum={_AUTO_VACUUM_MAP.get(auto_vac, auto_vac)})",
                    )
                    conn.execute("VACUUM")
            self._log(logging.INFO, "ANALYZE")
            # P1.1: use PRAGMA optimize when available (faster than ANALYZE on large DBs, SQLite 3.32+)
            try:
                conn.execute("PRAGMA optimize")
                self._log(logging.INFO, "PRAGMA optimize done")
            except sqlite3.Error:
                pass
            conn.execute("ANALYZE")
            self._log(logging.INFO, "REINDEX")
            conn.execute("REINDEX")
            # In WAL mode VACUUM's rebuild lives in the -wal file; checkpoint so
            # the on-disk .db reflects the compacted state and the 'after' size is real.
            self._log(logging.INFO, "wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            after = self.metrics(conn)
            after_staleness = self.stat_staleness(conn)
            # P3.6: monitoring alerts
            for label, m in (("before", before), ("after", after)):
                wpct = m.get("wasted_pct", 0)
                wbytes = m.get("wal_bytes", 0)
                if wpct > 5.0:
                    self._log(
                        logging.WARNING,
                        f"P3.6 alert ({label}): freelist {wpct:.1f}% ({m.get('freelist')} pages) >5% — consider VACUUM",
                    )
                if wbytes > 67108864:  # 64 MB
                    self._log(
                        logging.WARNING,
                        f"P3.6 alert ({label}): WAL {wbytes} bytes >64 MB — check checkpoint",
                    )

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()

        result = {
            "status": "complete",
            "settings": settings,
            "backup": {"path": str(self.backup_path), "size": backup_size},
            "before": before,
            "after": after,
            "stat_staleness_before": before_staleness,
            "stat_staleness_after": after_staleness,
            "indexes": indexes,
            "integrity_check": integrity,
            "foreign_key_violations": len(fk_violations),
        }

        # Optional DuckDB cache maintenance. Runs after SQLite so the
        # connection is closed (DuckDB allows one read-write OR many
        # read-only connections per file, never both).
        if self.duckdb_path and self.duckdb_path.exists():
            result["duckdb"] = self._maintain_duckdb()
        return result

    def _maintain_duckdb(self) -> dict:
        """Back up + CHECKPOINT + VACUUM the DuckDB cache file.

        Order: BACKUP first (pre-mutation recovery copy via CHECKPOINT +
        shutil.copy2), then CHECKPOINT + VACUUM. Skips silently if
        ``self.duckdb_path`` doesn't exist (caller already checks, but
        this method is also defensive).
        """
        if not self.duckdb_path or not self.duckdb_path.exists():
            return {"status": "skipped", "reason": "no file"}
        try:
            import duckdb
        except ImportError:
            self._log(logging.WARNING, "duckdb not installed; skipping DuckDB maintenance")
            return {"status": "skipped", "reason": "duckdb not installed"}

        result: dict = {"path": str(self.duckdb_path)}

        # Pre-mutation recovery backup. Same shape as the SQLite backup:
        # non-versioned, non-compressed, overwritten each run. Distinct
        # from snapshot_db.py's gzipped, git-tracked snapshot.
        if self.duckdb_backup_path:
            self._log(logging.INFO, f"DuckDB backup → {self.duckdb_backup_path}")
            backup_size = self._backup_duckdb()
            result["backup"] = {"path": str(self.duckdb_backup_path), "size": backup_size}

        before_size = self.duckdb_path.stat().st_size
        self._log(logging.INFO, f"DuckDB CHECKPOINT ({self.duckdb_path})")
        con = duckdb.connect(str(self.duckdb_path))
        try:
            con.execute("CHECKPOINT;")
            self._log(logging.INFO, "DuckDB VACUUM")
            con.execute("VACUUM;")
            # Final checkpoint to flush VACUUM's rewrite to the main file.
            con.execute("CHECKPOINT;")
        finally:
            con.close()
        after_size = self.duckdb_path.stat().st_size
        self._log(
            logging.INFO,
            f"DuckDB size: {_fmt_bytes(before_size)} → {_fmt_bytes(after_size)}",
        )
        result.update(
            status="ok",
            before_bytes=before_size,
            after_bytes=after_size,
        )
        return result


def _print_report(r: dict) -> None:  # noqa: C901
    s = r["settings"]
    print("=== SETTINGS ===")
    print(
        f"journal_mode={s.get('journal_mode')}  synchronous={s.get('synchronous')}  "
        f"auto_vacuum={s.get('auto_vacuum')}  cache_size={s.get('cache_size')}  "
        f"page_size={s.get('page_size')}  encoding={s.get('encoding')}"
    )

    def metrics_line(label, m):
        print(
            f"{label}: file={_fmt_bytes(m['file_size'])}  pages={m['pages']}  "
            f"freelist={m['freelist']} ({m['wasted_pct']:.1f}%, "
            f"{_fmt_bytes(m['wasted_bytes'])} wasted)"
        )

    def staleness_line(label, st):
        if not st:
            print(f"{label}: (no sqlite_stat1 — ANALYZE had not run)")
            return
        parts = []
        for tbl, d in st.items():
            tag = "STALE" if d["stale"] else "fresh"
            parts.append(f"{tbl} stat={d['stat']} live={d['live']} {tag}")
        print(f"{label}: " + "; ".join(parts))

    print("\n=== BEFORE ===")
    metrics_line("metrics", r["before"])
    staleness_line("stat_staleness", r["stat_staleness_before"])

    print(f"\n=== BACKUP ===\n-> {r['backup']['path']} ({_fmt_bytes(r['backup']['size'])})")

    print("\n=== MAINTENANCE ===\nVACUUM: ok\nANALYZE: ok\nREINDEX: ok")

    print("\n=== AFTER ===")
    metrics_line("metrics", r["after"])
    staleness_line("stat_staleness", r["stat_staleness_after"])

    print("\n=== INDEXES ===")
    for entry in r["indexes"]:
        table = entry["table"]
        idxs = entry["indexes"]
        rc = entry["row_count"]
        user = [i for i in idxs if i["origin"] == "c"]
        auto = [i for i in idxs if i["origin"] != "c"]
        redundant = [i["name"] for i in idxs if i["redundant_with"]]
        empty = rc == 0
        flags = []
        if redundant:
            flags.append(f"redundant={redundant}")
        else:
            flags.append("redundancy=none")
        if empty:
            flags.append("EMPTY-TABLE")
        print(f"{table}: {rc} rows; {len(user)} user + {len(auto)} auto; " + "; ".join(flags))
        for i in idxs:
            attrs = []
            if i["unique"]:
                attrs.append("unique")
            if i["origin"] != "c":
                attrs.append(i["origin"])  # 'pk' or 'u'
            if i["partial"]:
                attrs.append("partial")
            # Surface non-default collation (NOCASE indexes look like plain
            # column indexes otherwise and are easy to mistake for redundant).
            non_default = [
                f"{c}:{col}" for c, col in zip(i["columns"], i["collations"]) if col != "BINARY"
            ]
            if non_default:
                attrs.append("collation=" + ",".join(non_default))
            if i["redundant_with"]:
                attrs.append(f"REDUNDANT-WITH:{i['redundant_with']}")
            if i["empty_table"]:
                attrs.append("EMPTY-INDEX: 0 rows")
            suffix = f" ({', '.join(attrs)})" if attrs else ""
            print(f"  {i['name']} {i['columns']}{suffix}")

    print("\n=== INTEGRITY ===")
    print(f"integrity_check: {r['integrity_check']}")
    print(f"foreign_key_check: {r['foreign_key_violations']} violations")


def _run_sync_check(root: Path) -> dict:
    out = {}
    for name, rel in _SYNC_HELPERS:
        path = root / rel
        if not path.exists():
            out[name] = {"exit": None, "note": f"missing: {path}"}
            continue
        proc = subprocess.run(["python3", str(path)], capture_output=True, text=True)  # noqa: S603,S607  # list-form call; shell=False (default); args are constants/controlled paths; PATH-resolved interpreter/binary (python3/node/grep) by design
        tail = "\n".join((proc.stdout or "").strip().splitlines()[-3:])
        out[name] = {"exit": proc.returncode, "tail": tail}
    return out


def _compute_root() -> Path:
    # helpers/maintenance/db_maint.py -> repo root is two parents up
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    parser = argparse.ArgumentParser(
        description="SQLite DB maintenance for memory/research.db (+ optional DuckDB cache)."
    )
    parser.add_argument(
        "--db",
        default="memory/research.db",
        help="Path to the SQLite database (relative to repo root).",
    )
    parser.add_argument(
        "--backup",
        default="db-backup/research_backup.db",
        help="Backup path (will be created if needed).",
    )
    parser.add_argument(
        "--duckdb",
        default="memory/graph.duckdb",
        help="Path to the DuckDB cache file (relative to repo root). Maintenance is skipped if the file is absent.",
    )
    parser.add_argument(
        "--duckdb-backup",
        default="db-backup/graph_backup.duckdb",
        help="Pre-mutation DuckDB recovery backup path (relative to repo root). Overwritten each run.",
    )
    parser.add_argument(
        "--skip-duckdb",
        action="store_true",
        help="Skip DuckDB maintenance even if the file exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned operations without executing them.",
    )
    parser.add_argument(
        "--log", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)."
    )
    parser.add_argument(
        "--sync-check",
        action="store_true",
        help="After maintenance, run verify_notes.py + database_integrity_check.py.",
    )
    parser.add_argument(
        "--migrate-incremental",
        action="store_true",
        help="One-time migration: set PRAGMA auto_vacuum=INCREMENTAL and VACUUM to convert file (P2.5). File will be rewritten once.",
    )
    args = parser.parse_args(argv)

    log_level = getattr(logging, args.log.upper(), logging.INFO)
    logging.basicConfig(level=log_level, format=LOG_FORMAT)

    # P2.5 one-time migration: convert auto_vacuum NONE -> INCREMENTAL
    if args.migrate_incremental:
        import sqlite3 as _sqlite3

        root_m = _compute_root()
        dbp = Path(args.db)
        if not dbp.is_absolute():
            dbp = root_m / dbp
        if not dbp.exists():
            print(f"DB not found for migration: {dbp}", file=sys.stderr)
            return 1
        mcon = _sqlite3.connect(str(dbp), isolation_level=None)
        try:
            cur = mcon.execute("PRAGMA auto_vacuum").fetchone()[0]
            print(f"auto_vacuum before: {_AUTO_VACUUM_MAP.get(cur, cur)} ({cur})")
            if cur != 2:
                print("Setting PRAGMA auto_vacuum=INCREMENTAL and VACUUMing (one-time rewrite)...")
                mcon.execute("PRAGMA auto_vacuum = INCREMENTAL")
                mcon.execute("VACUUM")
                cur2 = mcon.execute("PRAGMA auto_vacuum").fetchone()[0]
                print(f"auto_vacuum after: {_AUTO_VACUUM_MAP.get(cur2, cur2)} ({cur2})")
                print("Migration complete — future maint will use incremental_vacuum.")
            else:
                print("Already INCREMENTAL — nothing to do.")
        finally:
            mcon.close()
        return 0

    root = _compute_root()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = root / db_path
    backup_path = Path(args.backup)
    if not backup_path.is_absolute():
        backup_path = root / backup_path
    duckdb_path: Path | None = None
    duckdb_backup_path: Path | None = None
    if not args.skip_duckdb:
        dp = Path(args.duckdb)
        duckdb_path = dp if dp.is_absolute() else root / dp
        dbk = Path(args.duckdb_backup)
        duckdb_backup_path = dbk if dbk.is_absolute() else root / dbk

    maintainer = DBMaintainer(
        db_path,
        backup_path,
        dry_run=args.dry_run,
        logger=logging.getLogger("db_maint"),
        duckdb_path=duckdb_path,
        duckdb_backup_path=duckdb_backup_path,
    )
    try:
        results = maintainer.run()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 1
    except Exception as e:
        print(f"ERROR during maintenance: {e}")
        return 2

    if args.dry_run:
        print("Dry run. Planned steps:")
        for step in results["steps"]:
            print(f"  - {step}")
        return 0

    _print_report(results)

    sync_ok = True
    if args.sync_check:
        print("\n=== SYNC CHECK (DB <-> filesystem) ===")
        for name, res in _run_sync_check(root).items():
            exit_code = res["exit"]
            ok = exit_code == 0
            sync_ok = sync_ok and ok
            mark = "PASS" if ok else "FAIL"
            print(f"{name}: exit={exit_code} [{mark}]")
            if res.get("tail"):
                for line in res["tail"].splitlines():
                    print(f"  {line}")

    # exit non-zero if integrity failed, FK violations, or sync-check failed
    healthy = (
        results["integrity_check"] == "ok" and results["foreign_key_violations"] == 0 and sync_ok
    )
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
