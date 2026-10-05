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
  S8: agent_id guard triggers installed at run start; provenance_agent_report
      is emitted after step 7 (both advisory/DDL, never fail-blocking)

Note: index *usage* cannot be detected (SQLite keeps no per-index read counters),
so only structural *redundancy* is reported, never "unused".

Produces zstd-compressed PRE-MUTATION recovery points (snapshot_parallel_and_compressed_backups.md
D2; stdlib compression.zstd, library-default level):
``db-backup/research_backup.db.zst`` (+ the embed-store twin
``embed_store_backup.db.zst``, or the legacy ``<db>_vec.db.zst`` when it
exists; + the corpus-cache twin ``corpus_backup.db.zst`` — full note
bodies, the private-content class the git snapshot excludes, so db-backup
is its only copy — added 2026-09-04) and ``db-backup/graph_backup.duckdb.zst`` (DuckDB cache). These
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

from helpers.core.env import REPO_ROOT  # noqa: E402  (folds _compute_root)

_AGENT_FACT_TABLES: tuple[str, ...] = (
    "graph_edges",
    "events",
    "quotes",
    "company_metrics",
    "hyper_edges",
)


def install_agent_id_guard(conn: sqlite3.Connection) -> list[str]:
    """S8 guard: reject ``''``/unregistered ``agent_id`` writes in the DB
    itself, so the rule holds even on raw ``sqlite3.connect`` (FK off).

    The FK on ``agent_id`` already covers FK-off sessions for unregistered
    ids; the trigger additionally quarantines the empty-string case — the
    class of five-edge ``db_maint`` drift the S8 audit recorded. Table
    tables absent or lacking the column are skipped silently.
    """
    if (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='provenance_agents'"
        ).fetchone()[0]
        == 0
    ):
        return []
    installed: list[str] = []
    for table in _AGENT_FACT_TABLES:
        for event in ("INSERT", "UPDATE"):
            ddl = (
                f"CREATE TRIGGER IF NOT EXISTS {table}_agent_id_registered_{event.lower()} "  # noqa: S608  # {table} rides the _AGENT_FACT_TABLES constant
                f'BEFORE {event} ON "{table}" '
                "FOR EACH ROW "
                "WHEN NEW.agent_id IS NOT NULL "
                "     AND (LENGTH(NEW.agent_id) = 0 "
                "          OR NEW.agent_id NOT IN (SELECT agent_id FROM provenance_agents)) "
                "BEGIN "
                "    SELECT RAISE(ABORT, 'agent_id must be NULL or a registered non-empty id'); "
                "END"
            )
            try:
                conn.execute(ddl)
                installed.append(f"{table}:{event}")
            except sqlite3.OperationalError as exc:
                msg = str(exc)
                if "no such table" in msg or "no such column" in msg:
                    continue
                raise
    return installed


def provenance_agent_report(conn: sqlite3.Connection) -> dict:
    """S8 advisory probe: counts of ``''`` and unregistered ``agent_id``
    rows per fact table, plus the registered-agent count. Return shape:
    ``{"registered": int, "empty_agent_id": {table: count},
       "unregistered_agent_id": {table: [ids]}}``.
    """
    out: dict = {"registered": 0, "empty_agent_id": {}, "unregistered_agent_id": {}}
    try:
        out["registered"] = conn.execute("SELECT COUNT(*) FROM provenance_agents").fetchone()[0]
        id_ok = True
    except sqlite3.OperationalError:
        id_ok = False
    for table in _AGENT_FACT_TABLES:
        try:
            empty = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE agent_id IS NOT NULL AND LENGTH(agent_id)=0'  # noqa: S608  # {table} rides the _AGENT_FACT_TABLES constant
            ).fetchone()[0]
        except sqlite3.OperationalError:
            continue
        if empty:
            out["empty_agent_id"][table] = empty
        if id_ok:
            try:
                unreg = conn.execute(
                    f'SELECT DISTINCT agent_id FROM "{table}" '  # noqa: S608  # {table} rides the _AGENT_FACT_TABLES constant
                    "WHERE agent_id IS NOT NULL AND LENGTH(agent_id) > 0 "
                    "AND agent_id NOT IN (SELECT agent_id FROM provenance_agents)"
                ).fetchall()
                if unreg:
                    out["unregistered_agent_id"][table] = sorted(r[0] for r in unreg)
            except sqlite3.OperationalError:
                pass
    return out


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
        backup_sidecars: bool = True,
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
        # Production sidecar backups (embed store, note corpus, the
        # convo_search index pair, convo corpus tar, memory/ catch-all)
        # copy HUNDREDS OF MB of rebuildable production state per run.
        # Tests run against tmp copies and must not drag that world into
        # pytest basetemp (the tmpfs-amplification lesson, 2026-09-27) —
        # they pass backup_sidecars=False; production keeps the default.
        self.backup_sidecars = backup_sidecars

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

    def _sqlite_zstd_backup(self, src: Path, dst: Path, *, label: str) -> int:
        """WAL-consistent sqlite online-backup → zstd. Shared tail of the
        paired store backups (embed store, corpus cache, convo FTS sidecar
        — S7, code_duplication_consolidation); the resolution logic that
        picks ``src``/``dst`` stays with each caller."""
        from helpers.core.zstd_io import compress_file, zst_path

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._log(logging.WARNING, f"cannot create backup dir ({e}); {label} backup skipped")
            return 0
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
            self._log(logging.INFO, f"{label} backed up to {zst_dst}")
            return size
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
        return self._sqlite_zstd_backup(src, dst, label="Embed store")

    def _backup_corpus(self) -> int:
        """Paired recovery copy of the S1b corpus cache (memory/corpus.db).

        corpus_cache holds path/mtime/content_hash/frontmatter/body/text
        for every vault note — the private-content class the git snapshot
        deliberately excludes, so this is its only recovery copy
        (operator decision 2026-09-04). Rebuildable (one findata walk),
        but a lost cache also loses the mtime-incremental baseline.
        Resolution + WAL-consistent sqlite online-backup + zstd exactly
        as _backup_embed_store; absent state just skips."""
        from helpers.core.corpus import CORPUS_DB

        src = Path(CORPUS_DB)
        if not src.exists():
            self._log(logging.INFO, f"Corpus cache absent — backup skipped ({src})")
            return 0
        dst = self.backup_path.parent / "corpus_backup.db"
        return self._sqlite_zstd_backup(src, dst, label="Corpus cache")

    def _duckdb_zstd_backup(self, src: Path, dst: Path, label: str) -> int:
        """Checkpoint-then-copy one DuckDB file into ``<dst>.zst``.

        DuckDB has no online-backup API, so the canonical safe pattern
        is: open read-only, force CHECKPOINT (flushes the WAL into the
        main file), close, then copy the quiescent file. With no writer
        active and the WAL merged, the copy is consistent.

        Version-sensitive assumption: read-only CHECKPOINT relies on
        DuckDB >= 1.5 allowing a reader connection to flush the WAL; the
        fallback (catch duckdb.Error -> copy as-is) degrades gracefully
        if a bump rejects it. See doc/design/graph_design.md §9.3
        (Bundle O3) for the full caveat + how to re-test on pin bumps.

        A file another process holds open (a running rebuild) raises
        IOException on connect — that is a normal "busy", logged and
        skipped, never a backup failure.
        """
        import shutil

        try:
            import duckdb
        except ImportError:
            self._log(logging.WARNING, "duckdb not installed; skipping DuckDB backup")
            return 0
        if not src.exists():
            self._log(logging.INFO, f"{label} absent — backup skipped ({src})")
            return 0
        try:
            # S3 (duckdb_transient_lock_retry): the whole open+checkpoint
            # window sits under io.lock SH — a concurrent long writer
            # (rebuild, stamp) makes the backup QUEUE instead of hitting
            # the RW hold and skipping; a queued writer waits its turn
            # behind the checkpoint.
            from helpers.misc.duckdb_lock import io_lock as _io_lock

            with _io_lock(src, exclusive=False):
                con = duckdb.connect(str(src), read_only=True)
                try:
                    con.execute("CHECKPOINT;")
                except duckdb.Error as e:
                    self._log(logging.WARNING, f"read-only CHECKPOINT failed ({e}); copying as-is")
                finally:
                    con.close()
        except (duckdb.Error, OSError) as e:
            self._log(logging.WARNING, f"{label} busy/unreadable ({e}); backup skipped")
            return 0
        from helpers.core.zstd_io import compress_file, zst_path

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._log(logging.WARNING, f"cannot create backup dir ({e}); {label} backup skipped")
            return 0
        zst_dst = zst_path(dst)
        with tempfile.NamedTemporaryFile(suffix=src.suffix, dir=dst.parent, delete=False) as tf:
            tmp_path = Path(tf.name)
        try:
            shutil.copy2(src, tmp_path)
            if zst_dst.exists():
                zst_dst.unlink()
            size = compress_file(tmp_path, zst_dst)
            self._log(logging.INFO, f"{label} backed up to {zst_dst}")
            return size
        finally:
            tmp_path.unlink(missing_ok=True)

    def _backup_convo_search(self) -> int:
        """Recovery copies of the convo_search index pair.

        ``memory/convo_search.duckdb`` (pointers + snippets + vectors)
        and its FTS5 sidecar ``memory/convo_search_fts.db`` are both
        rebuildable from the corpus — but the rebuild is not free: a cold
        run embeds 50k+ texts and the pool lane took 2h10m on 2026-09-27.
        A restored corpus + a restored embed_store already avoids the
        re-embed, so these copies are the second line of defence against
        losing the derived pair outright (operator decision: 2h+ runs are
        worth a backup). Skipped silently while a rebuild holds the lock.
        """
        from helpers.maintenance.rebuild_convo_search import (
            FTS_DB_NAME,
            REPO,
        )

        idx = REPO / "memory/convo_search.duckdb"
        total = self._duckdb_zstd_backup(
            idx, self.backup_path.parent / "convo_search_backup.duckdb", label="convo_search index"
        )
        fts = idx.parent / FTS_DB_NAME
        if fts.exists() and fts.stat().st_size:
            total += self._sqlite_zstd_backup(
                fts,
                self.backup_path.parent / "convo_search_fts_backup.db",
                label="convo_search FTS sidecar",
            )
        return total

    def _backup_convo_corpus(self) -> int:
        """Tar+zstd copy of the harvested conversation corpus.

        ``memory/data/harness/<harness>/conversations/*.parquet`` is the
        archive of record for harness history the harnesses themselves
        delete (opencode purges sessions, prime purges sessions, zcode
        rotates rollouts). The parquet corpus is derived from harness
        sources, but those sources are exactly what disappears, and the
        timeshift window is finite — so unlike the index this artifact
        has no rebuild path. One tar.zst, ~10 MB for ~35 MB of parquet.
        """
        import tarfile

        from helpers.core.zstd_io import zst_path
        from helpers.maintenance.harvest_conversations import CORPUS_ROOT

        root = Path(CORPUS_ROOT)
        files = sorted(root.glob("*/conversations/*.parquet"))
        if not files:
            self._log(logging.INFO, f"convo corpus empty — backup skipped ({root})")
            return 0
        dst = self.backup_path.parent / "convo_corpus_backup.tar"
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._log(logging.WARNING, f"cannot create backup dir ({e}); corpus backup skipped")
            return 0
        from compression import zstd

        with zstd.open(zst_path(dst), "wb") as raw:
            with tarfile.open(fileobj=raw, mode="w|") as tar:  # streaming, no temp tar
                for f in files:
                    tar.add(f, arcname=str(f.relative_to(root.parent)))
        size = zst_path(dst).stat().st_size
        self._log(logging.INFO, f"convo corpus ({len(files)} parquet) backed up to {zst_path(dst)}")
        return size

    def _backup_memory_sidecars(self) -> int:  # noqa: C901  # maintenance population, keep per archived c901_complexity_debt D1 verdict
        """Catch-all: every ``memory/`` + ``memory/data/`` file WITHOUT its
        own registration goes into one tar.zst.

        Operator thesis (2026-09-27): at bare minimum everything under
        ``memory/`` and ``memory/data/`` belongs in ``db-backup/``. The
        big artifacts each have a bespoke routine above (research,
        graph, embed_store, corpus, doc/script search, sources, agent
        traces, model_usage, convo index pair + corpus) because their
        engines need WAL-consistent or checkpointed copies. What is left
        is a long tail of derived matrices, layout JSON, fetch caches,
        worklists and raw ingest drops (~44 MB raw) that nobody had
        registered — a coverage gap, not a judgement.

        Two deny-lists, both deliberate and both logged so the skip is
        auditable rather than silent:

        - SECRETS never enter a backup: ``.env``, service-account JSON,
          anything matching key/credential/token/secret. ``memory/``
          holds live GCP credentials, and a blanket tar would multiply
          them.
        - Transient files are not state: ``*-wal``, ``*-shm``,
          ``*.lock``. The WAL is already merged by the online-backup /
          checkpoint routines of the artifacts that own one.
        """
        import re
        import tarfile

        from compression import zstd

        from helpers.core.zstd_io import zst_path

        mem = self.db_path.parent
        data_dir = mem / "data"
        # already covered by a dedicated routine (relative to memory/)
        covered = {
            "research.db",
            "graph.duckdb",
            "embed_store.db",
            "corpus.db",
            "doc_search.db",
            "script_search.db",
            "memory_search.db",
            "convo_search.duckdb",
            "convo_search_fts.db",
            "data/sources.duckdb",
            "data/agent_traces.duckdb",
            "data/model_usage.duckdb",
            "data/harness",
        }
        secret_re = re.compile(
            r"(^\.env$)|(svc_account)|(credential)|(secret)|(token)|(\.key$)|(\.pem$)",
            re.IGNORECASE,
        )
        transient_suffixes = ("-wal", "-shm", ".lock", ".tmp")
        # A catch-all must not become a way to tar a data lake: one huge
        # drop (model weights, a raw ingest) is LOGGED as a coverage gap
        # and left out, never silently archived and never a failure.
        max_file_bytes = 512 * 2**20

        def _skip(rel: str, size: int) -> str | None:
            if rel in covered:
                return "covered"
            if secret_re.search(Path(rel).name):
                return "secret"
            if rel.endswith(transient_suffixes):
                return "transient"
            if size > max_file_bytes:
                return "oversize"
            return None

        members: list[Path] = []
        seen: set[str] = set()
        skipped: dict[str, list[str]] = {}

        def _corpus_file(rel: str) -> bool:
            # only the harvested corpus has its own tar (T5) — the rest of
            # data/harness/ holds operator artifacts (prime-rlm's
            # memory_trail.md + the pre-consolidation harness_state tarball)
            # that nothing else backs up, so the sweep must take them.
            # Shape: data/harness/<harness>/conversations/<session>.parquet
            parts = Path(rel).parts
            return len(parts) >= 4 and parts[-2] == "conversations"

        for base in (mem, data_dir):
            if not base.exists():
                continue
            for p in sorted(base.rglob("*")):
                if not p.is_file() and not p.is_symlink():
                    continue
                rel = str(p.relative_to(mem))
                if _corpus_file(rel):
                    continue
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                why = _skip(rel, size)
                if why:
                    skipped.setdefault(why, []).append(rel)
                    continue
                if rel in seen:
                    continue  # memory/ and memory/data/ walks overlap
                seen.add(rel)
                members.append(p)
        if not members:
            self._log(logging.INFO, "memory sidecars: nothing uncovered — backup skipped")
            return 0
        dst = self.backup_path.parent / "memory_sidecars_backup.tar"
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._log(logging.WARNING, f"cannot create backup dir ({e}); sidecar backup skipped")
            return 0
        with zstd.open(zst_path(dst), "wb") as raw:
            with tarfile.open(fileobj=raw, mode="w|") as tar:
                for p in members:
                    tar.add(p, arcname=str(p.relative_to(mem)))
        for why, names in sorted(skipped.items()):
            self._log(
                logging.INFO,
                f"memory sidecars: skipped {len(names)} {why} file(s): "
                + ", ".join(sorted(names)[:6])
                + (" …" if len(names) > 6 else ""),
            )
        self._log(
            logging.INFO, f"memory sidecars ({len(members)} files) backed up to {zst_path(dst)}"
        )
        return zst_path(dst).stat().st_size

    def _backup_duckdb(self) -> int:
        """Pre-mutation recovery copy of the DuckDB cache file,
        stored zstd-compressed (``<duckdb_backup_path>.zst``).

        Delegates to :meth:`_duckdb_zstd_backup` (the checkpoint-then-copy
        routine, shared with the convo_search index) — see that method for
        the version-sensitive read-only CHECKPOINT caveat and
        doc/design/graph_design.md §9.3 (Bundle O3).

        See https://ducklake.select/docs/stable/duckdb/guides/backups_and_recovery.html
        """
        src, bp = self.duckdb_path, self.duckdb_backup_path
        if src is None or bp is None:
            return 0
        return self._duckdb_zstd_backup(src, bp, label="graph.duckdb")

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
            "agent_id_guard",
            "provenance_agent_report",
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

            guard_installed = install_agent_id_guard(conn)
            self._log(
                logging.INFO,
                f"S8 agent_id guard installed: {len(guard_installed)} trigger(s) "
                f"({len(guard_installed) // 2} fact table(s), INSERT+UPDATE)",
            )

            self._log(logging.INFO, f"Backing up to {self.backup_path}")
            backup_size = self._backup(conn)
            if self.backup_sidecars:
                self._backup_embed_store()
                self._backup_corpus()
                self._backup_convo_search()
                self._backup_convo_corpus()
                self._backup_memory_sidecars()

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
            provenance_report = provenance_agent_report(conn)
            if provenance_report["empty_agent_id"] or provenance_report["unregistered_agent_id"]:
                self._log(
                    logging.WARNING,
                    f"S8: '' agent_id rows: {provenance_report['empty_agent_id']}; "
                    f"unregistered: {provenance_report['unregistered_agent_id']} — "
                    "repair via helpers/misc/backfill_row_provenance.py --apply",
                )
            else:
                self._log(
                    logging.INFO,
                    f"S8: provenance clean (registered agents: {provenance_report['registered']})",
                )
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
            "provenance_agent_report": provenance_report,
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
        # S3 (duckdb_transient_lock_retry): LOCK_EX spans the
        # checkpoint+VACUUM window — readers queue on the io.lock instead
        # of racing the RW hold.
        from helpers.misc.duckdb_lock import io_lock as _io_lock

        with _io_lock(self.duckdb_path, exclusive=True):
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
    p = r.get("provenance_agent_report") or {}
    print(
        f"provenance_agents: {p.get('registered', '?')} registered; "
        f"empty-agent_id rows: {p.get('empty_agent_id', {})}; "
        f"unregistered: {p.get('unregistered_agent_id', {})}"
    )


def _run_sync_check(root: Path) -> dict:
    out = {}
    for name, rel in _SYNC_HELPERS:
        path = root / rel
        if not path.exists():
            out[name] = {"exit": None, "note": f"missing: {path}"}
            continue
        # sys.executable, not a PATH-resolved "python3": sync helpers must
        # run under the same interpreter (venv or not) running db_maint.
        proc = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)  # noqa: S603  # list-form call; shell=False (default); args are constants/controlled paths
        tail = "\n".join((proc.stdout or "").strip().splitlines()[-3:])
        out[name] = {"exit": proc.returncode, "tail": tail}
    return out


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

        root_m = REPO_ROOT
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

    root = REPO_ROOT
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
