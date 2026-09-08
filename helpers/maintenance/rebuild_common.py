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
globals (``BACKUP_DIR``, ``_pseudo_warned``) and pass them through —
tests reset those attributes per module (monkeypatch.setattr), so the
globals must stay the live state, not move here byte-for-byte. Specs
that carry a global-derived value (backup_dir, copier) are constructed
INSIDE the wrapper at call time for the same reason — a module-level
spec would snapshot the pre-monkeypatch value at import.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RebuildCliSpec:
    """Per-index config for :func:`run_rebuild_cli`.

    The argparse trio strings, the ``rebuild`` core, and the two
    doc/script-vs-note flow switches (``resolve_db`` anchors relative
    --db paths at the repo root; ``handle_errors`` off lets exceptions
    propagate) — one concern, the CLI surface of one rebuild script.
    """

    description: str
    default_db: str
    db_help: str
    check_help: str
    incremental_help: str
    rebuild_fn: Callable[..., dict]
    summary: Callable[[dict], str]
    migrated_msg: str
    resolve_db: Callable[[str], Path | None] | None = None
    handle_errors: bool = True


@dataclass(frozen=True)
class IndexBackupSpec:
    """Per-index backup identity for :func:`backup_last_good_index`."""

    backup_dir: str | Path
    table: str
    dest_name: str
    copier: Callable[[Path, Path], bool]


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


def build_rebuild_parser(spec: RebuildCliSpec) -> argparse.ArgumentParser:
    """The --db/--check/--incremental argparse trio shared by all three CLIs."""
    p = argparse.ArgumentParser(description=spec.description)
    p.add_argument("--db", default=spec.default_db, help=spec.db_help)
    p.add_argument("--check", action="store_true", help=spec.check_help)
    p.add_argument("--incremental", action="store_true", help=spec.incremental_help)
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
            print(
                f"embed cache: {stats['embed_cache_hits']} hits, "
                f"{stats['embed_cache_misses']} misses",
                file=sys.stderr,
            )
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
        print(f"index state: FRESH ({stats.get(count_key, 0)} {unit} unchanged)", file=sys.stderr)
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


def backup_last_good_index(db_path: Path, spec: IndexBackupSpec) -> None:
    """Last-good recovery copy of an INDEX DB into db-backup/.

    Runs AFTER a successful FULL rewrite (last-good-state semantics: a
    failed rebuild rolls back and the previous run's backup survives).
    Best-effort with WARNINGs — a failed backup must not fail the
    rebuild. Completeness guard: never displace the last-good archive
    with an EMPTY index.
    """
    import sqlite3

    from helpers.core.db import connect

    dest = Path(spec.backup_dir) / spec.dest_name
    try:
        conn = connect(db_path, read_only=True)
        try:
            rows = conn.execute(f"SELECT COUNT(*) FROM {spec.table}").fetchone()[0]  # noqa: S608  # interpolated part is a schema-constant identifier (doc_search / script_search) — the spec's table is a code constant, never input
        finally:
            conn.close()
    except sqlite3.Error:
        rows = 0
    if not rows:
        print(
            f"WARNING: {db_path.name} empty ({rows} rows) — last-good "
            "backup skipped (recovery point kept; rebuild continues)",
            file=sys.stderr,
        )
        return
    try:
        Path(spec.backup_dir).mkdir(parents=True, exist_ok=True)
    except OSError:
        print(
            f"WARNING: cannot create backup dir {spec.backup_dir} "
            "(recovery point skipped; rebuild continues)",
            file=sys.stderr,
        )
        return
    if not db_path.exists():
        return
    if not spec.copier(db_path, dest):
        print(
            f"WARNING: could not back up {db_path.name} to {dest} "
            "(recovery point skipped; rebuild continues)",
            file=sys.stderr,
        )


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
    """
    args = build_rebuild_parser(spec).parse_args(argv)

    if spec.resolve_db is not None:
        db_path = spec.resolve_db(args.db)
        if db_path is None:
            return 1
    else:
        db_path = Path(args.db)

    if spec.handle_errors:
        try:
            stats = spec.rebuild_fn(db_path, write=not args.check, incremental=args.incremental)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    else:
        stats = spec.rebuild_fn(db_path, write=not args.check, incremental=args.incremental)

    print(spec.summary(stats), file=sys.stderr)
    if not args.check:
        print_rebuild_report(stats, migrated_msg=spec.migrated_msg)
        return 0
    return 1 if stats.get("index_stale") else 0


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
        "--json", action="store_true", dest="as_json", help="emit the raw result dicts as JSON"
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
