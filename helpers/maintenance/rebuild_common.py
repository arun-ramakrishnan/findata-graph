#!/usr/bin/env python3
"""Shared CLI + reporting scaffold for the rebuild_*_search scripts.

rebuild_doc_search / rebuild_note_search / rebuild_script_search grew
parallel copies of the same CLI flow (--db/--check/--incremental argparse,
the post-rebuild report, the staleness verdict, the last-good backup,
embedder resolution). This module owns those pieces; each script keeps
its own walker/extractor/``rebuild`` core and delegates here (S3,
code_duplication_consolidation, 2026-09-08; per-index config bundled
into spec dataclasses, cli_param_bundling_doc_anchor_repair, 2026-09-09).

Delegation contract: the per-script wrappers keep their module-level
globals (``_pseudo_warned``) and pass them through —
tests reset those attributes per module (monkeypatch.setattr), so the
globals must stay the live state, not move here byte-for-byte.

The APPLY guard (`worktree_fresh_trample_guard`, S1/S2/S5, 2026-10-12)
also lives here, because it is a property of the shared CLI flow rather
than of any one walker: `memory/` is a symlink, so every worktree shares
ONE index, and an incremental apply from a tree that is BEHIND the index
rewrites that shared surface backward and bumps its generation — by
construction, not by mistake. A spec that names its `stamp_table` gets:
the tree's HEAD/worktree/ts stamped into the index after every apply
(S1), a named refusal of a strictly-behind apply plus the `--allow-stale`
escape (S2), and `--sandbox`, which runs the same apply against a scratch
copy and discards it (S5). Every check is fail-open: no git, no stamp, or
an unreadable DB means "cannot tell" and the apply proceeds as before.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from helpers.core import jsonio  # noqa: E402  (orjson parse cutover; dumps stay stdlib per db_scaling_tuning §6.3 criteria)
from helpers.core.db import APPLY_STAMP_PREFIX


@dataclass(frozen=True)
class RebuildCliSpec:
    """Per-index config for :func:`run_rebuild_cli`.

    The argparse quad strings, the ``rebuild`` core, and the two
    doc/script-vs-note flow switches (``resolve_db`` anchors relative
    --db paths at the repo root; ``handle_errors`` off lets exceptions
    propagate) — one concern, the CLI surface of one rebuild script.
    ``stamp_table`` opts the index into the shared-store APPLY guard.
    """

    description: str
    default_db: str
    db_help: str
    check_help: str
    incremental_help: str
    rebuild_fn: Callable[..., dict]
    summary: Callable[[dict], str]
    migrated_msg: str
    full_help: str | None = None
    resolve_db: Callable[[str], Path | None] | None = None
    handle_errors: bool = True
    swap_help: str | None = None
    # S1: name of the key/value meta table in THIS index that carries the
    # working-set stamp (db_meta / doc_search_info / script_search_info /
    # memory_search_info). None = no stamp, no guard (duckdb convo lane).
    stamp_table: str | None = None
    # Register --allow-stale WITHOUT arming the guard: for indexes that
    # opt out of the guard entirely (git: a history index's corpus IS the
    # reachable set — rebuild_git_search stamp_table=None) but still ride
    # the search-fresh loop, which forwards ARGS verbatim to every
    # rebuilder. The flag is accepted and ignored; --help says so.
    allow_stale_noop: bool = False


@dataclass(frozen=True)
class QueryCliSpec:
    """Per-index config for :func:`run_query_cli`.

    Per-index callables stay fields (the shape that genuinely differs);
    ``default_db`` is a zero-arg callable so tests that retarget the
    module DB constant keep working.
    """

    description: str
    default_db: Callable[[], Path]
    db_flag_help: str
    connect_fn: Callable[..., sqlite3.Connection]
    ready_fn: Callable[..., bool]
    not_built_msg: str
    stale_fn: Callable[..., bool]
    stale_warning: str
    search_fn: Callable[..., dict]
    render_hits: Callable[[list[dict]], None]
    extra_args: Callable[[argparse.ArgumentParser], None] | None = None
    search_kwargs: Callable[[argparse.Namespace], dict] | None = None


# --------------------------------------------------------------------------- #
# S1/S2/S5 (worktree_fresh_trample_guard, 2026-10-12): shared-index APPLY guard #
# Key names are generic because each index owns its own key/value meta table;  #
# see the module docstring for the full rationale.                             #
# --------------------------------------------------------------------------- #
STAMP_HEAD_KEY = APPLY_STAMP_PREFIX + "head"
STAMP_WORKTREE_KEY = APPLY_STAMP_PREFIX + "worktree"
STAMP_TS_KEY = APPLY_STAMP_PREFIX + "ts"
_STAMP_KEYS = (STAMP_HEAD_KEY, STAMP_WORKTREE_KEY, STAMP_TS_KEY)


def read_apply_stamp(db_path: Path, table: str) -> dict[str, str]:
    """The index's last-apply stamp rows, or {} when there is nothing to read.

    An absent file (a spec-driven fixture), an index built before S1, or a
    read error all mean "no stamp" — and the guard is warn-only on missing
    evidence, so refusing here would break every pre-S1 index and every
    git-less fixture instead of protecting them.
    """
    from helpers.core.db import connect

    if not db_path.exists():
        return {}
    try:
        conn = connect(db_path, read_only=True)
    except sqlite3.Error:
        return {}
    try:
        rows = conn.execute(
            f"SELECT key, value FROM {table} WHERE key IN (?, ?, ?)",  # noqa: S608  # interpolated part is a schema-constant table name from the spec
            _STAMP_KEYS,
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return {row[0]: row[1] for row in rows}


def write_apply_stamp(db_path: Path, table: str) -> None:
    """Record this tree's HEAD + worktree name in the index's meta table.

    Runs after a successful writing apply so the NEXT apply can tell whether
    it is behind. Best-effort: a failure (read-only fixture, locked sidecar,
    no git) warns and returns — an apply that already succeeded must not fail
    on a diagnostic stamp, and an unstamped index only means the next apply
    arms the guard instead of checking it.
    """
    from helpers.core import git_state
    from helpers.core.db import connect, utc_now

    head = git_state.head_sha()
    if not head:
        return
    try:
        conn = connect(db_path)
        try:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table} "  # noqa: S608  # schema-constant table name from the spec
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            conn.executemany(
                f"INSERT OR REPLACE INTO {table} (key, value) VALUES (?, ?)",  # noqa: S608
                (
                    (STAMP_HEAD_KEY, head),
                    (STAMP_WORKTREE_KEY, git_state.worktree_name()),
                    (STAMP_TS_KEY, utc_now()),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(
            f"WARNING: could not record the apply stamp in {db_path.name}: {exc}",
            file=sys.stderr,
        )


def stale_apply_reason(db_path: Path, spec: RebuildCliSpec) -> str | None:
    """Named refusal when this tree's HEAD is NOT a forward step over the index.

    Refuse in the two cases that make an incremental delta a rewrite of the
    shared surface rather than an advance over it:

    - **behind** — the current HEAD is an ANCESTOR of the stamped HEAD, so
      the delta describes content the index already moved past;
    - **divergent** — neither HEAD is an ancestor of the other, so the
      delta is a branch-shaped rewrite. This is the normal shape of a
      patch-testing worktree carrying its own commits, i.e. the shape the
      guard most needs to catch (worktree_fresh_trample_guard F8).

    Same-HEAD (dirty edits included — the common fresh-after-edit case),
    forward (main after a worktree applied), and every "cannot tell" (no
    git, no stamp, unreadable index, a stamped sha this clone has never
    seen) proceed: the guard protects the shared surface only when the
    ordering is a fact it can verify.
    """
    if spec.stamp_table is None:
        return None
    from helpers.core import git_state

    current = git_state.head_sha()
    if not current:
        return None
    stamp = read_apply_stamp(db_path, spec.stamp_table)
    stamped = stamp.get(STAMP_HEAD_KEY)
    if not stamped or stamped == current:
        return None

    behind = git_state.is_ancestor(current, stamped)
    if behind is None:
        return None  # git cannot order the two HEADs -> warn-only
    if behind:
        relation = "is BEHIND it"
        detail = (
            "An incremental apply from a behind working set rewrites the "
            "shared index backward and bumps the shared generation for "
            "content that never changed"
        )
        fix = "sync this worktree forward"
    else:
        # Not behind: forward is the clean case, anything else is divergent.
        # `is not False` keeps "cannot tell" warn-only, same as above.
        if git_state.is_ancestor(stamped, current) is not False:
            return None
        relation = "has DIVERGED from it"
        detail = (
            "An incremental apply from a diverged working set is not a "
            "forward step: the delta is a branch-shaped rewrite of the "
            "shared index, and it bumps the shared generation for content "
            "that never changed"
        )
        fix = "sync or rebase this worktree onto the stamped HEAD"

    who = stamp.get(STAMP_WORKTREE_KEY) or "main"
    when = stamp.get(STAMP_TS_KEY) or "unknown"
    here = git_state.worktree_name() or "main"
    return (
        f"REFUSED: stale apply — {db_path.name} reflects HEAD {stamped[:12]} "
        f"(worktree {who}, stamped {when}) and this tree ({here}, "
        f"{current[:12]}) {relation}. {detail} "
        "(worktree_fresh_trample_guard); nothing was written.\n"
        f"  fix: {fix}, or re-run with --allow-stale to apply this working "
        "set on purpose.\n"
        "  preview without touching the shared index: add --sandbox."
    )


def _sandbox_copy_db(db_path: Path) -> tuple[Path, Path]:
    """(scratch DB path, scratch dir) for --sandbox; caller discards the dir.

    SQLite files are copied through the backup API, which reads a consistent
    snapshot INCLUDING pages still in the -wal (a plain file copy would miss
    them); anything else (convo_search.duckdb) gets a file copy plus its
    sidecars. Errors propagate: a sandbox run that silently fell back to the
    live file is the one outcome --sandbox exists to prevent.
    """
    import shutil
    import tempfile

    work = Path(tempfile.mkdtemp(prefix="rebuild-sandbox-"))
    dest = work / db_path.name
    with db_path.open("rb") as fh:
        sqlite_file = fh.read(16).startswith(b"SQLite format 3")
    if sqlite_file:
        from helpers.core.db import connect

        src = connect(db_path, read_only=True)
        try:
            # House connect() for the scratch destination too: the backup
            # API is indifferent to pragmas/row_factory, and this keeps
            # both sqlite3-connect mirrors (validator + pytest allowlist)
            # closed with no exemption.
            dst = connect(str(dest))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        return dest, work
    shutil.copy2(db_path, dest)
    for suffix in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            shutil.copy2(side, dest.with_name(dest.name + suffix))
    return dest, work


def _sandbox_discard(work: Path | None) -> None:
    """Remove a --sandbox scratch dir; never raises."""
    if work is None:
        return
    import shutil

    shutil.rmtree(work, ignore_errors=True)


def build_rebuild_parser(spec: RebuildCliSpec) -> argparse.ArgumentParser:
    """The --db/--check/--incremental/--full argparse quad shared by all rebuild CLIs.

    Two more flags belong to the shared-store guard and are registered
    per spec: ``--sandbox`` (always — preview against a scratch copy)
    and ``--allow-stale`` (only when ``stamp_table`` opts the index in).

    Incremental is the default and safe no-op path on unchanged input:
    row-keyed (doc/script/memory) or file-granularity (doc) diffs mean
    unchanged content produces no writes. ``--full`` is the explicit
    opt-out that reproduces the legacy behavior (complete rewrite).
    """
    p = argparse.ArgumentParser(description=spec.description)
    p.add_argument("--db", default=spec.default_db, help=spec.db_help)
    # --sandbox is the read-only twin of --check: both leave the shared
    # index alone, one by not writing, one by writing to a throwaway copy.
    shared = p.add_mutually_exclusive_group()
    shared.add_argument("--check", action="store_true", help=spec.check_help)
    shared.add_argument(
        "--sandbox",
        action="store_true",
        help="apply against a scratch COPY of the index and discard it: "
        "the shared index and its generation stay untouched (shared "
        "sidecar embed stores and the db-backup last-good copy are still "
        "written). Preview lane for a worktree that is behind; mutually "
        "exclusive with --check.",
    )
    if spec.stamp_table is not None:
        p.add_argument(
            "--allow-stale",
            action="store_true",
            help="apply even when this tree's HEAD is not a forward step "
            "over the index last apply — BEHIND it, or DIVERGED from it — "
            "rewriting the shared surface on purpose "
            "(worktree_fresh_trample_guard); the stamp is updated so the "
            "other tree is not blocked afterwards.",
        )
    elif spec.allow_stale_noop:
        p.add_argument(
            "--allow-stale",
            action="store_true",
            help="accepted and IGNORED: this index opts out of the "
            "shared-store guard (a history index's corpus is the reachable "
            "set of main for any HEAD), so there is no staleness to "
            "allow — the flag exists so the search-fresh loop can pass "
            "ARGS uniformly to every rebuilder.",
        )
    # store_true with default=True: no flag → incremental; --full wins.
    p.add_argument("--incremental", action="store_true", default=True)
    if spec.full_help is not None:
        p.add_argument("--full", action="store_true", help=spec.full_help)
    if spec.swap_help is not None:
        p.add_argument("--swap", action="store_true", help=spec.swap_help)
    return p


def print_rebuild_report(stats: dict, *, migrated_msg: str) -> None:
    """Post-write report: indexed/migrated/embedded/cache/stale lines.

    Emitted only on a writing run (never --check); every line to stderr.
    """
    print(f"indexed {stats.get('indexed', 0)} rows", file=sys.stderr)
    if stats.get("migrated"):
        print(migrated_msg, file=sys.stderr)
    emb = stats.get("embedded")
    if emb is not None:
        print(f"embedded {emb} rows", file=sys.stderr)
        if "embed_cache_hits" in stats:
            line = (
                f"embed cache: {stats['embed_cache_hits']} hits, "
                f"{stats['embed_cache_misses']} misses"
            )
            state = stats.get("embed_cache_state")
            if state and not state.startswith("on"):
                line += f" [{state}]"
            print(line, file=sys.stderr)
    if stats.get("index_stale"):
        print(
            f"index was STALE before this rebuild: "
            f"{len(stats.get('stale_changed', []))} changed, "
            f"{len(stats.get('stale_new', []))} new, "
            f"{len(stats.get('stale_deleted', []))} deleted — now fresh",
            file=sys.stderr,
        )


def print_staleness(stats: dict, *, count_key: str, unit: str, refresh_cmd: str) -> None:
    """--check verdict: FRESH, or the drift breakdown + remediation.

    Mirrors the sync_sector_wikilinks --check shape: name the drift and
    the exact refresh command so gate output is actionable on its own.
    """
    new = stats.get("stale_new", [])
    changed = stats.get("stale_changed", [])
    deleted = stats.get("stale_deleted", [])
    if not (new or changed or deleted):
        print(
            f"index state: FRESH ({stats.get(count_key, 0)} {unit} unchanged)",
            file=sys.stderr,
        )
        return
    print(
        f"index state: STALE — {len(changed)} changed, {len(new)} new, {len(deleted)} deleted",
        file=sys.stderr,
    )
    drift = (
        [(fp, "changed") for fp in changed]
        + [(fp, "new") for fp in new]
        + [(fp, "deleted") for fp in deleted]
    )
    for fp, kind in drift[:10]:
        print(f"  {kind:8s} {fp}", file=sys.stderr)
    if len(drift) > 10:
        print(f"  … and {len(drift) - 10} more", file=sys.stderr)
    print(f"refresh: {refresh_cmd}", file=sys.stderr)


def resolve_embedder(
    already_warned: bool, pseudo_dims: int = 64
) -> tuple[Callable[[str], list[float]], int, str, bool]:
    """Index-side embedder: (embed_fn, dims, model_label, warned_now).

    Real local model when available; pseudo fallback otherwise. The
    caller owns the warn-once flag as a module global (tests reset it
    per module) — pass its current value in, store the returned flag
    back.
    """
    from helpers.core import local_embedder

    if local_embedder.available():
        return (
            local_embedder.embed_document,
            local_embedder.DIM,
            local_embedder.MODEL_ID,
            (already_warned),
        )
    if not already_warned:
        print(
            "WARNING: local bge-small embedder unavailable — using 64-dim "
            "pseudo-embeddings (hybrid ranking stays lexical-ish). Setup: "
            "helpers/core/local_embedder.py module docstring.",
            file=sys.stderr,
        )

    def _pseudo(text: str) -> list[float]:
        from helpers.graph.embeddings import _pseudo_embedding

        return _pseudo_embedding(text, pseudo_dims)

    return _pseudo, pseudo_dims, f"dry-run-v{pseudo_dims}", True


@dataclass(frozen=True)
class QueryVector:
    """One shared query embedding (shared_query_vector proposal, 2026-10-06).

    Same text + same model = identical floats, so the master_query parent
    embeds once and fans this out instead of each leg loading the GGUF.
    The stamp (model label + dims) is the contract: a leg only scores
    this against its index when the stamp matches the index's stored
    stamp — otherwise it falls back to its own embed (today's path).
    Labels mirror resolve_embedder: the granite MODEL_ID or dry-run-vN.
    """

    vector: tuple[float, ...]
    model: str
    dims: int

    def to_json(self) -> str:
        """Compact argv-safe encoding for the --query-vector CLI flag."""
        return json.dumps({"model": self.model, "dims": self.dims, "vector": list(self.vector)})


def make_query_vector(text: str) -> QueryVector | None:
    """Embed once for the fan-out (parent side). None when there is
    nothing to embed (empty text) or the embed raises — legs then fall
    back to their own embed, exactly today's behavior."""
    if not text or not text.strip():
        return None
    try:
        from helpers.core import local_embedder as le

        if le.available():
            return QueryVector(tuple(le.embed_query(text)), le.MODEL_ID, le.DIM)
        from helpers.graph.embeddings import _pseudo_embedding

        vec = _pseudo_embedding(text, 64)
        return QueryVector(tuple(vec), "dry-run-v64", 64)
    except Exception:  # noqa: BLE001  # embed failure -> per-leg fallback, never a dead query
        return None


def check_query_vector(
    stored_model: str | None, stored_dims: int | None, qv: QueryVector | None
) -> list[float] | None:
    """Usable vector or None (fall back to the leg's own embed).

    Accept on dims match AND (no stored label yet, or label match). A
    storeddims mismatch covers the real/pseudo split (384 vs 64); the
    label covers same-dims model swaps. Missing stored label (old
    sidecar) keeps today's dims-only behavior, not a hard reject.
    """
    if qv is None or stored_dims is None:
        return None
    if stored_dims != qv.dims or qv.dims != len(qv.vector):
        return None
    if stored_model is not None and stored_model != qv.model:
        return None
    return list(qv.vector)


def parse_query_vector_flag(raw: str | None) -> QueryVector | None:
    """Parse a --query-vector JSON payload (child-CLI side). None on
    missing/garbled input (stderr warning) — the leg falls back to its
    own embed, so a bad flag never kills a query."""
    if not raw:
        return None
    try:
        d = jsonio.loads(raw)
        vec = tuple(float(x) for x in d["vector"])
        qv = QueryVector(vector=vec, model=str(d["model"]), dims=int(d["dims"]))
    except ValueError, TypeError, KeyError, AttributeError:
        print(
            "WARNING: --query-vector payload unparseable, embedding locally",
            file=sys.stderr,
        )
        return None
    if qv.dims <= 0 or len(vec) != qv.dims:
        print(
            "WARNING: --query-vector dims mismatch payload, embedding locally",
            file=sys.stderr,
        )
        return None
    return qv


class _ApplyAbort(Exception):
    """Carries the exit code when the S2 guard or --sandbox staging stops a run."""

    def __init__(self, code: int):
        super().__init__(str(code))
        self.code = code


def _guard_and_stage(
    args: argparse.Namespace, spec: RebuildCliSpec, db_path: Path, timer
) -> tuple[Path, Path | None]:
    """S2 refusal + --sandbox copy. Raises _ApplyAbort(exit code) on either.

    Extracted from run_rebuild_cli (C901): decide BEFORE anything is copied
    or written. --sandbox is exempt by design — it exists so a behind tree
    can see exactly the delta the refusal forbids applying, and it cannot
    reach the shared index.
    """
    sandbox = bool(getattr(args, "sandbox", False))
    if not args.check:
        reason = stale_apply_reason(db_path, spec)
        if reason is not None and not sandbox and not getattr(args, "allow_stale", False):
            print(reason, file=sys.stderr)
            raise _ApplyAbort(timer.finish(1, reason.split("\n")[0][:500]))
    sandbox_dir: Path | None = None
    if sandbox:
        try:
            db_path, sandbox_dir = _sandbox_copy_db(db_path)
        except (OSError, sqlite3.Error) as exc:
            print(
                f"ERROR: --sandbox copy of {db_path.name} failed: {exc}",
                file=sys.stderr,
            )
            raise _ApplyAbort(timer.finish(1, f"--sandbox copy failed: {exc}"[:500])) from exc
    return db_path, sandbox_dir


def run_rebuild_cli(argv: list[str] | None, spec: RebuildCliSpec) -> int:
    """The shared main() flow: parse → rebuild → report → exit code.

    ``spec.resolve_db`` turns the --db string into the actual DB path (the
    note-search resolver anchors relative paths at the repo root and
    guards existence, printing its own ERROR and returning None).
    ``spec.handle_errors`` matches the doc/script try/except ERROR shape;
    note-search lets exceptions propagate (its guard already covered
    the only realistic failure). The --check staleness verdict itself
    is printed by each module's ``rebuild()`` via its ``_print_staleness``
    delegate — main() only maps ``index_stale`` to the exit code.

    Incremental is the default; ``--full`` reproduces the legacy complete
    rewrite by passing incremental=False through to the rebuild core.

    Shared-store guard (armed only when ``spec.stamp_table`` is set): a
    NON-FORWARD HEAD (behind or diverged from the stamped HEAD) is refused
    up front with a named reason and zero writes (``--allow-stale``
    overrides), and ``--sandbox`` redirects the apply to a throwaway copy
    so a behind tree can still inspect its delta.
    """
    args = build_rebuild_parser(spec).parse_args(argv)

    from helpers.maintenance.maint_timing import RunTimer, active

    mode = "check" if args.check else ("swap" if getattr(args, "swap", False) else "apply")
    # --full wins over --incremental (the default) and maps to incremental=False
    # for a complete rewrite. --check disables writes and reports freshness.
    # getattr: --full is only registered when spec.full_help is set (the convo
    # spec omits it, since its atomic swap rebuilds the whole duckdb anyway).
    incremental = bool(
        not args.check and not getattr(args, "full", False) and getattr(args, "incremental", True)
    )
    if incremental:
        mode += "+incremental"
    timer = RunTimer(mode=mode)  # cmd defaults to the script stem
    with active(timer):
        if spec.resolve_db is not None:
            db_path = spec.resolve_db(args.db)
            if db_path is None:
                return timer.finish(1, "db resolve failed")
        else:
            db_path = Path(args.db)

        sandbox = bool(getattr(args, "sandbox", False))
        # S2: decide BEFORE anything is copied or written (see
        # _guard_and_stage); a refusal or a failed --sandbox copy exits
        # here with zero writes.
        try:
            db_path, sandbox_dir = _guard_and_stage(args, spec, db_path, timer)
        except _ApplyAbort as aborted:
            return aborted.code
        try:
            kwargs: dict = dict(write=not args.check, incremental=incremental)
            if spec.swap_help is not None:
                kwargs["swap"] = args.swap
            if spec.handle_errors:
                try:
                    stats = spec.rebuild_fn(db_path, **kwargs)
                except Exception as exc:  # pragma: no cover - defensive
                    print(f"ERROR: {exc}", file=sys.stderr)
                    return timer.finish(1, f"ERROR: {exc}"[:500])
            else:
                stats = spec.rebuild_fn(db_path, **kwargs)

            summary_line = spec.summary(stats)
            print(summary_line, file=sys.stderr)
            if not args.check:
                # S1: stamp the LIVE index only, and only after a successful
                # apply — a sandbox run must not claim the shared index
                # reflects this (possibly older) working set.
                if not sandbox and spec.stamp_table is not None:
                    write_apply_stamp(db_path, spec.stamp_table)
                print_rebuild_report(stats, migrated_msg=spec.migrated_msg)
                return timer.finish(0, summary_line.split("\n")[0][:500])
            stale = bool(stats.get("index_stale"))
            return timer.finish(1 if stale else 0, summary_line.split("\n")[0][:500])
        finally:
            _sandbox_discard(sandbox_dir)


def run_query_cli(argv: list[str] | None, spec: QueryCliSpec) -> int:
    """Read-side counterpart of :func:`run_rebuild_cli`'s flow: the
    query CLI shared by doc_query / script_query (S5,
    code_duplication_consolidation). Shared: the
    query/--limit/--db/--bm25/--json argparse, ready-check → exit 1 with
    the rebuild hint, stale WARNING (answers anyway — the stale-index
    doctrine), the JSON emission, and the no-hits/hit-count header.
    Per-index: the spec's callables plus ``render_hits`` (the hit-line
    shape genuinely differs: doc path:anchor vs script kind/area rows)
    and optional ``extra_args``/``search_kwargs`` (script's --kind/--area).
    """
    p = argparse.ArgumentParser(description=spec.description)
    p.add_argument("query", help="free-text query; punctuation is safe")
    if spec.extra_args is not None:
        spec.extra_args(p)
    p.add_argument("--limit", type=int, default=5, help="max hits (default 5)")
    p.add_argument("--db", default=None, help=spec.db_flag_help)
    p.add_argument("--bm25", action="store_true", help="lexical leg only (skip the cosine re-rank)")
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the raw result dicts as JSON",
    )
    p.add_argument(
        "--query-vector",
        default=None,
        metavar="JSON",
        help="shared query embedding (shared_query_vector: parent fanned-out "
        '{"model","dims","vector"} — skips this leg\'s own model load; '
        "absent/garbled = embed locally as before)",
    )
    args = p.parse_args(argv)

    conn = spec.connect_fn(Path(args.db) if args.db else spec.default_db())
    try:
        if not spec.ready_fn(conn):
            print(spec.not_built_msg, file=sys.stderr)
            return 1
        if spec.stale_fn(conn):
            print(spec.stale_warning, file=sys.stderr)
        kwargs = spec.search_kwargs(args) if spec.search_kwargs is not None else {}
        kwargs["query_vec"] = parse_query_vector_flag(args.query_vector)
        out = spec.search_fn(
            conn,
            args.query,
            limit=max(1, min(args.limit, 100)),
            hybrid=not args.bm25,
            **kwargs,
        )
    finally:
        conn.close()

    if args.as_json:
        print(json.dumps({"mode": out["mode"], "results": out["results"]}, indent=2))
        return 0
    if not out["results"]:
        print(f"(no hits for {args.query!r}; mode={out['mode']})", file=sys.stderr)
        return 0
    print(f"# {len(out['results'])} hit(s), mode={out['mode']}", file=sys.stderr)
    spec.render_hits(out["results"])
    return 0
