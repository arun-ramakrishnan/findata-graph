"""pytest tmp hygiene: one shared trimmed template per run, keep-1 roots.

Historically tmpfs-bound (2026-09-27, advisory run 547): two amplifiers
filled the 7.1 GB /tmp tmpfs and poisoned gates with `disk I/O error`
cascades that masqueraded as test failures (65 environmental failures):

1. The production-DB trim was session-scoped, but an xdist worker IS a
   session, so `-n auto` built one 308 MB -> ~76 MB template per worker;
   the db_maint module then took another full 308 MB sqlite backup per
   worker on top — ~6 materialisations (~1.9 GB) per live-invariants
   run. Fix: `trimmed_template()` below builds ONE template per run,
   shared by every worker.

2. pytest's numbered-root retention keeps the last 3 run roots; with
   ~0.6-1.9 GB per root that is pure ballast — gate_query already
   retains what matters from past runs (junit + artifacts under
   outputs/.artifacts, indexed in gate_runs.duckdb). Fix:
   `prune_old_pytest_roots()` wired to `pytest_unconfigure` in
   conftest.py: keep only roots newer than ours or still quiet-recent
   (a concurrent live run keeps touching its root and is never touched).

The sharing rides xdist's own layout: every worker's basetemp is
`<run-root>/popen-gw<k>`, so `<run-root>` (basetemp().parent) is common
to exactly this run's workers and is reaped by the retention above — no
cross-run collision, no new cleanup surface. A flock-protected build
plus an atomic `os.replace` publish means a worker that crashes
mid-build leaves nothing but an ignored `.part`.

Single-process runs (no `popen-gw` in basetemp.name) have no peers and
build locally as before — sharing would point at the parent of the run
root, which IS shared across runs.

NB (2026-09-28, operator): the basetemp moved OFF tmpfs — gate runs in
the operator's shell set ``TMPDIR=/mnt/data/tmp`` (223 GB disk), so the
7.1 GB tmpfs cap no longer binds there. The amplifier story above is
the historical motivation; the keep-1 retention stays as plain disk
hygiene (``make tmp-sweep`` follows ``tempfile.gettempdir()`` and so
sweeps whichever root is active). Agent shells that do NOT inherit
TMPDIR still land on /tmp tmpfs — the cap still binds for agent-driven
heavy runs unless TMPDIR is exported.
"""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "memory" / "research.db"

# A-fix (2026-08-21): downsample the production copy. A full-corpus cold
# build costs ~1.2s and the build-mechanics tests build up to 3x each;
# the contract under test is cold/warm/rebuild/meta behavior, not live
# scale. Every non-company entity is kept, plus CEAT (the name the
# assertions pin) and a deterministic alphabetical company sample;
# anything referencing a dropped entity goes with it. The trim pass
# costs ~0.35s (the note_search FTS delete alone is ~0.17s); VACUUM then
# compacts the file, which is what makes every downstream copy cheap.
_KEEP_COMPANIES = 120


def build_trimmed_template(dst: Path) -> Path:
    """Build the downsampled, VACUUMed production copy at ``dst``."""
    src = sqlite3.connect(str(DB_PATH))
    dstdb = sqlite3.connect(str(dst))
    try:
        src.backup(dstdb)
        # LIMIT lives inside the subquery so it bounds only the company
        # sample, not the whole compound select.
        dstdb.execute(
            "CREATE TEMP TABLE keep AS "
            "SELECT name FROM entities WHERE entity_type != 'company' "
            "UNION SELECT 'CEAT' "
            "UNION SELECT name FROM (SELECT name FROM entities "
            "  WHERE entity_type = 'company' AND name != 'CEAT' "
            "  ORDER BY name LIMIT ?)",
            (_KEEP_COMPANIES,),
        )
        dstdb.execute(
            "DELETE FROM graph_edges "
            "WHERE source NOT IN (SELECT name FROM keep) "
            "   OR target NOT IN (SELECT name FROM keep)"
        )
        # FK children first (FKs are off on this raw connection, but keep
        # the copy tidy for tests that later connect with FKs on).
        for tbl, col in (
            ("entity_tags", "entity_name"),
            ("graph_analytics", "entity_name"),
            ("events", "entity"),
            ("quotes", "entity"),
            ("company_metrics", "entity"),
            ("company_embeddings", "company_name"),
        ):
            dstdb.execute(f"DELETE FROM {tbl} WHERE {col} NOT IN (SELECT name FROM keep)")
        dstdb.execute("DELETE FROM entities WHERE name NOT IN (SELECT name FROM keep)")
        # note_search feeds v_note_embeddings: keep the kept entities' docs
        # plus a small newsletter slice so the doc_type mix stays realistic.
        dstdb.execute(
            "DELETE FROM note_search WHERE file_path NOT IN "
            "  (SELECT file_path FROM entities WHERE file_path IS NOT NULL) "
            "AND file_path NOT IN "
            "  (SELECT file_path FROM note_search WHERE doc_type IN "
            "   ('chatter','points_and_figures','plotlines') LIMIT 30)"
        )
        dstdb.commit()
        # DELETE doesn't shrink the file (50MB production-sized) and leaves
        # FTS tombstones in the note_search shadow — VACUUM compacts both.
        dstdb.execute("VACUUM")
    finally:
        dstdb.close()
        src.close()
    return dst


def trimmed_template(tmp_path_factory) -> Path:
    """The shared trimmed template: built once per run, reused by every worker.

    Callers treat the returned file as READ-ONLY (byte-copy it per test);
    the file is shared concurrently by all workers of this run.
    """
    return _shared_template(tmp_path_factory, "trimmed_template.db", build_trimmed_template)


def build_schema_template(dst: Path) -> Path:
    """Production SCHEMA + db_meta, zero rows, VACUUMed tiny (D2).

    Serves the schema-only test class (fuzz_shortest_path, test_graph, the
    extraction/events CLIs, snapshot_cycle, near_duplicates): they backed up
    the production DB only to wipe it and seed their own rows — the backup
    was pure schema donor. FTS5 virtual tables are cleared with the
    ``delete-all`` command — never DELETE their shadow tables directly
    (zeroing ``%_data`` corrupts the index: "invalid fts5 file format",
    hit 2026-09-29); ``db_meta`` rows survive (schema_version/generation
    behavior under test matches the old copy_production_db path, which
    kept them).
    """
    src = sqlite3.connect(str(DB_PATH))
    out = sqlite3.connect(str(dst))
    try:
        src.backup(out)
        virtual = {
            r[0]
            for r in out.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND sql LIKE 'CREATE VIRTUAL TABLE%'"
            )
        }
        shadows = {
            f"{v}_{suffix}"
            for v in virtual
            for suffix in ("data", "idx", "content", "docsize", "config")
        }
        tables = [
            r[0]
            for r in out.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name != 'db_meta'"
            )
            if r[0] not in shadows
        ]
        # FKs are off on this raw connection; VACUUM below compacts anyway.
        ddl = {
            r[0]: (r[1] or "")
            for r in out.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")
        }
        for t in tables:
            if t in virtual:
                # 'delete-all' is only legal on contentless/external-content
                # fts5 tables; a contentful fts5 (note_search) takes DELETE.
                if "content=" in ddl.get(t, ""):
                    out.execute(f'INSERT INTO "{t}"("{t}") VALUES(\'delete-all\')')  # noqa: S608
                else:
                    out.execute(f'DELETE FROM "{t}"')  # noqa: S608
            else:
                out.execute(f'DELETE FROM "{t}"')  # noqa: S608  # names from sqlite_master
        out.commit()
        out.execute("VACUUM")
    finally:
        out.close()
        src.close()
    return dst


def schema_template(tmp_path_factory) -> Path:
    """The shared schema-only template: built once per run, byte-copy per
    scope. Same sharing contract as :func:`trimmed_template`."""
    return _shared_template(tmp_path_factory, "schema_template.db", build_schema_template)


def build_full_template(dst: Path) -> Path:
    """Full live corpus, once per run (production_db_copy_audit S2/S3).

    Serves the two former keep_all amplifiers: query_plans opens the
    template READ-ONLY in place (EXPLAIN never writes; ten
    byte-identical per-test backups were pure waste), and rebuild_schema
    copyfiles it per test (isolation without the backup — measured
    2026-09-29: backup 1.07 s vs copyfile 0.22 s for the 307 MiB file).

    The journal-mode normalisation is load-bearing: a backup inherits
    the source's WAL header, and every mode=ro open of a WAL file
    creates -shm/-wal sidecars the reader can neither checkpoint nor
    remove — on a shared template that means N concurrent readers
    racing sidecars onto one file. DELETE mode makes the published file
    self-contained; read-only opens then touch nothing.
    """
    src = sqlite3.connect(str(DB_PATH))
    out = sqlite3.connect(str(dst))
    try:
        src.backup(out)
        out.execute("PRAGMA journal_mode=DELETE")
        out.commit()
    finally:
        out.close()
        src.close()
    return dst


def full_template(tmp_path_factory) -> Path:
    """The shared full-corpus template: built once per run, opened
    read-only (query_plans) or byte-copied per test (rebuild_schema).
    Same sharing contract as :func:`trimmed_template`."""
    return _shared_template(tmp_path_factory, "full_template.db", build_full_template)


def reflink_or_copy(src: Path, dst: Path) -> Path:
    """Reflink-favouring clone for template donors (S3, 2026-09-29).

    On reflink-capable filesystems (btrfs — /mnt/data is btrfs) the
    clone is metadata-only COW: a 307 MiB full-template donor costs
    0.017 s and ~zero bytes instead of a full read+write (copyfile
    0.221 s; the pre-S3 sqlite backup 1.07 s). Only the extents a test
    actually rewrites get materialised. Falls back to a plain copyfile
    (tmpfs, ext4, missing cp) so behaviour on /tmp or foreign CI is
    unchanged. DONORS ONLY, never a live database: a reflink snapshots
    extents, not WAL state — clone from closed, checkpointed templates.
    """
    try:
        subprocess.run(
            ["cp", "--reflink=auto", str(src), str(dst)],  # noqa: S603, S607  # constant argv
            check=True,
        )
    except OSError, subprocess.CalledProcessError:
        shutil.copyfile(src, dst)
    return dst


def _shared_template(tmp_path_factory, filename: str, build) -> Path:
    """Once-per-run flock-shared template build (the #307 S1 mechanism)."""
    base = tmp_path_factory.getbasetemp()
    if base.name.startswith("popen-gw"):
        # xdist worker: <run-root>/popen-gw<k> — the run root is the
        # natural once-per-run home, owned by pytest's own retention.
        shared = base.parent / "_worker_shared"
        shared.mkdir(parents=True, exist_ok=True)
        target = shared / filename
        with open(shared / (filename + ".lock"), "w") as lockfh:
            fcntl.flock(lockfh, fcntl.LOCK_EX)
            if not target.exists():
                # Build beside the target, publish atomically: a crashed
                # builder leaves only an ignored .part for the next run.
                with tempfile.NamedTemporaryFile(dir=shared, prefix=".part.", delete=False) as part:
                    part_path = Path(part.name)
                try:
                    build(part_path)
                    os.replace(part_path, target)
                finally:
                    part_path.unlink(missing_ok=True)
        return target
    # Single-process run: no peers to share with (the parent of a bare
    # run root is shared ACROSS runs — wrong scope), so build locally.
    return build(tmp_path_factory.mktemp("template") / filename)


def prune_old_pytest_roots(current: Path, quiet_seconds: float = 1800.0) -> list[Path]:
    """Keep-1 retention for pytest's numbered run roots (plus our own).

    Deletes sibling `pytest-*` directories under `current.parent` that
    are not `current` and have been quiet for `quiet_seconds` (default
    30 min). Quiet means the root AND its direct children (the
    `popen-gw*` worker dirs) all stopped changing — a concurrent live
    run keeps touching worker dirs as tests write, so anything
    recently-modified is presumed alive and left alone. The
    `pytest-current` symlink is never a deletion target.
    Returns the pruned roots (for tests and the dry-run story).
    """
    pruned: list[Path] = []
    now = time.time()
    try:
        siblings = sorted(current.parent.glob("pytest-*"))
    except OSError:
        return pruned

    def _last_touch(root: Path) -> float:
        newest = root.stat().st_mtime
        for child in root.iterdir():
            try:
                newest = max(newest, child.stat().st_mtime)
            except OSError:
                continue
        return newest

    for sib in siblings:
        if sib == current or sib.is_symlink():
            continue
        try:
            if now - _last_touch(sib) < quiet_seconds:
                continue  # possibly a live concurrent run
        except OSError:
            continue
        shutil.rmtree(sib, ignore_errors=True)
        pruned.append(sib)
    return pruned


def _default_pid_alive(pid: int) -> bool:
    """`os.kill(pid, 0)` as a liveness probe: no signal is delivered, the
    kernel only checks the PID. PermissionError means the PID exists but
    is owned by another user — still alive, keep the conservative read."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def sweep_stale_xdist_caches(
    directory: Path,
    *,
    max_age_seconds: float = 24 * 3600.0,
    now: float | None = None,
    pid_alive=_default_pid_alive,
) -> list[Path]:
    """Reclaim orphaned per-worker xdist graph caches
    (xdist_shared_graph_cache S1; wired to pytest_configure in conftest.py).

    `pytest_sessionfinish` deletes a worker's cache family only on a
    graceful exit — a SIGKILLed/OOM-killed/timed-out leg leaves the
    `graph.xdist-<worker>-<pid>` files (`.duckdb` + `.wal` +
    `.build.lock` + rebuild temporaries) on disk forever. The sweep runs
    once per invocation BEFORE this run's workers exist (controller or
    plain-run configure), so the only live files it can see belong to a
    CONCURRENT invocation — exactly what the guards protect:

    - PID liveness (primary, exact): the owning PID is embedded in the
      filename; a live owner means keep. The advisory gate runs two
      pytest invocations at once (entry 189), and one may never delete
      the other's cache.
    - mtime floor (backstop): a dead worker whose PID has since been
      recycled by an unrelated process looks alive; the file's age
      eventually settles it.
    - the S2 shared cache (`graph.xdist-shared.*`) has NO owner PID, so
      it is age-only: reclaimed once no xdist session has used it for
      the floor (the controller stamps mtime at session finish — RO
      opens never touch it).
    - a name the parser cannot attribute is kept, never guessed at.

    Returns the removed paths (conftest prints them; empty stays silent).
    """
    removed: list[Path] = []
    if now is None:
        now = time.time()
    try:
        candidates = sorted(directory.glob("graph.xdist-*"))
    except OSError:
        return removed
    for f in candidates:
        try:
            fresh = (now - f.stat().st_mtime) < max_age_seconds
        except OSError:
            continue
        if f.name.startswith("graph.xdist-shared."):
            # S2 shared cache: age-only (no owner PID to probe); a
            # controller-stamped mtime means "an xdist session finished
            # with me recently" — keep. Age it out and the next run just
            # rebuilds once.
            if fresh:
                continue
        else:
            # owner key is `gw<k>-<pid>`; the pid must end at a `.` so a
            # number embedded in a suffix can never masquerade as one
            m = re.match(r"^graph\.xdist-gw\d+-(\d+)(?:\.|$)", f.name)
            if m is None:
                continue
            if pid_alive(int(m.group(1))) and fresh:
                continue
        try:
            f.unlink()
        except OSError:
            continue
        removed.append(f)
    return removed
