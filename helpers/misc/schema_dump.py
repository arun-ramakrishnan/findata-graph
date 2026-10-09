#!/usr/bin/env python3
"""Dump live DDL from every DB under memory/ into the tracked schema/ tree.

The live schema previously existed only inside gitignored DB files and
Python DDL constants (rebuild_schema.py), so a PR that changed a table
had no reviewable artifact and no linter saw the DDL. This dump makes it
a tracked, reviewable, lintable surface — proposal
doc/improvements/archive/tooling/schema_ddl_review_surface.md.

Layout is schema/<engine>/<db-stem>.sql; the engine directory doubles as
the sqlfluff dialect selector (review_scan maps sqlite/ -> sqlite,
duckdb/ -> duckdb).

Rules that make drift meaningful (a diff is schema intent, not churn):
- classification by MAGIC BYTES, never extension (graph.xdist-shared.duckdb
  is a .duckdb-named scratch copy and is skipped by name anyway);
- deterministic emission: sqlite objects in sqlite_master.rowid order
  (creation order, the stable choice snapshot_db.py already relies on),
  duckdb tables in name order; no timestamps, no volatile stats;
- shadow tables of a virtual table (fts5/vec0) are EXCLUDED — the
  CREATE VIRTUAL TABLE statement recreates them; keeping them would
  make the file non-replayable and churn on every rebuild;
- internal sqlite_% objects excluded;
- header comment carries the repo-relative source path and a sha256 of
  the DDL body, so `--check` is a byte compare.

Run via `make schema-dump` (write) / `make schema-check` (drift report).
Advisory on its own; the BLOCKING gate is tests/test_schema_drift.py.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# entry-point bootstrap: this file runs as `python3 helpers/misc/schema_dump.py`,
# where the repo root is NOT on sys.path (static_checks guards the ordering).
sys.path.insert(0, str(REPO_ROOT))
from helpers.core.db import connect  # noqa: E402

MEMORY_DIR = REPO_ROOT / "memory"
SCHEMA_DIR = REPO_ROOT / "schema"

SQLITE_MAGIC = b"SQLite format 3\x00"
DUCKDB_MAGIC = b"DUCK"

# A pytest-xdist shared copy is scratch, not an index (same bytes as
# graph.duckdb, different inode). Anything matching is never a source.
SKIP_NAME_PARTS = (".xdist-",)


def classify(path: Path) -> str | None:
    """'sqlite' | 'duckdb' | None by magic bytes — never by extension."""
    with path.open("rb") as fh:
        head = fh.read(16)
    if head.startswith(SQLITE_MAGIC):
        return "sqlite"
    if DUCKDB_MAGIC in head:
        return "duckdb"
    return None


def source_dbs() -> list[tuple[Path, str]]:
    """(path, engine) for every live DB under memory/, sorted."""
    out: list[tuple[Path, str]] = []
    if not MEMORY_DIR.is_dir():
        return out
    for p in sorted(MEMORY_DIR.rglob("*")):
        if not p.is_file() or p.suffix not in (".db", ".duckdb"):
            continue
        if any(part in p.name for part in SKIP_NAME_PARTS):
            continue
        engine = classify(p)
        if engine is None:
            continue
        out.append((p, engine))
    return out


def _virtual_table_names(con: sqlite3.Connection) -> set[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%VIRTUAL TABLE%'"
    ).fetchall()
    return {r[0] for r in rows}


def _is_shadow(name: str, virtuals: set[str]) -> bool:
    """True for fts5/vec0 shadow tables: <virtual>_anything. The CREATE
    VIRTUAL TABLE statement recreates its shadows, so emitting them would
    make the dump non-replayable."""
    return any(name.startswith(v + "_") for v in virtuals)


def sqlite_ddl(path: Path) -> str:
    """Replayable sqlite DDL: tables (virtual included), indexes, views,
    triggers — creation order — shadows and internals excluded.

    Opens through the house connect(read_only=True) — same mode=ro URI the
    raw open used, so a missing or non-sqlite target fails loudly instead of
    creating a phantom 0-byte db — without needing a static_checks exemption."""
    con = connect(path, read_only=True)
    try:
        virtuals = _virtual_table_names(con)
        blocks: list[str] = []
        for kind in ("table", "index", "view", "trigger"):
            rows = con.execute(
                "SELECT name, sql FROM sqlite_master WHERE type=? AND sql IS NOT NULL "
                "ORDER BY rowid",
                (kind,),
            ).fetchall()
            for name, sql in rows:
                if name.startswith("sqlite_"):
                    continue
                if kind == "table" and _is_shadow(name, virtuals):
                    continue
                if kind in ("index", "trigger") and _is_shadow(name, virtuals):
                    continue
                blocks.append(sql.rstrip().rstrip(";") + ";")
        return "\n".join(blocks)
    finally:
        con.close()


def duckdb_ddl(path: Path) -> str:
    """Replayable duckdb DDL: tables then views, name order."""
    import duckdb

    con = duckdb.connect(str(path), read_only=True)
    try:
        blocks: list[str] = []
        for catalog, name_col in (("duckdb_tables()", "table_name"), ("duckdb_views()", "view_name")):
            rows = con.execute(
                f"SELECT schema_name, {name_col}, sql FROM {catalog} "  # noqa: S608  # both the catalog and the column are fixed constants
                "WHERE internal = false AND sql IS NOT NULL ORDER BY schema_name, 2"
            ).fetchall()
            for _schema, _name, sql in rows:
                blocks.append(sql.rstrip().rstrip(";") + ";")
        return "\n".join(blocks)
    finally:
        con.close()


def render(rel_source: str, engine: str, body: str) -> str:
    digest = hashlib.sha256(body.encode()).hexdigest()[:12]
    header = (
        f"-- LIVE DDL dump — generated by helpers/misc/schema_dump.py; DO NOT EDIT\n"
        f"-- source: {rel_source} ({engine})\n"
        f"-- body sha256: {digest}\n"
        f"-- refresh: make schema-dump\n"
    )
    return header + body + "\n"


def dump_all(out_dir: Path = SCHEMA_DIR) -> list[Path]:
    """Write every DB's DDL; returns the files written."""
    written: list[Path] = []
    for path, engine in source_dbs():
        body = sqlite_ddl(path) if engine == "sqlite" else duckdb_ddl(path)
        target = out_dir / engine / f"{path.stem}.sql"
        target.parent.mkdir(parents=True, exist_ok=True)
        rel = path.relative_to(REPO_ROOT)
        target.write_text(render(str(rel), engine, body), encoding="utf-8")
        written.append(target)
    return written


def check(out_dir: Path = SCHEMA_DIR) -> list[str]:
    """Drift: (a) tracked file vs fresh render, (b) source DB with no
    tracked file. Returns human-readable drift lines; empty = fresh."""
    def disp(p: Path) -> str:
        # relative to the repo when inside it, absolute otherwise —
        # check() must work on a scratch out-dir too (the drift test's)
        try:
            return str(p.relative_to(REPO_ROOT))
        except ValueError:
            return str(p)

    drift: list[str] = []
    seen: set[Path] = set()
    for path, engine in source_dbs():
        target = out_dir / engine / f"{path.stem}.sql"
        seen.add(target)
        body = sqlite_ddl(path) if engine == "sqlite" else duckdb_ddl(path)
        rel = path.relative_to(REPO_ROOT)
        expected = render(str(rel), engine, body)
        if not target.exists():
            drift.append(f"missing: {disp(target)} (source {rel})")
        elif target.read_text(encoding="utf-8") != expected:
            drift.append(f"stale: {disp(target)} (source {rel} changed)")
    for tracked in sorted(out_dir.rglob("*.sql")):
        if tracked not in seen:
            drift.append(f"orphan: {disp(tracked)} (no source DB)")
    return drift


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="report drift, write nothing")
    ap.add_argument(
        "--out",
        default=str(SCHEMA_DIR),
        help=f"output tree (default {SCHEMA_DIR})",
    )
    args = ap.parse_args(argv)
    out_dir = Path(args.out)
    if not MEMORY_DIR.is_dir():
        print("no memory/ DBs on this machine — nothing to dump (SKIP, not a failure)")
        return 0
    sources = source_dbs()
    if not sources:
        print("no readable DBs under memory/ — nothing to dump (SKIP, not a failure)")
        return 0
    if args.check:
        drift = check(out_dir)
        if drift:
            print(f"schema drift ({len(drift)}):")
            for line in drift:
                print(f"  {line}")
            print("refresh with: make schema-dump")
            return 1
        print(f"schema ok: {len(sources)} DBs match their tracked DDL")
        return 0
    written = dump_all(out_dir)
    for f in written:
        print(f"  {f.relative_to(REPO_ROOT)}")
    drift = check(out_dir)
    print(f"schema-dump: wrote {len(written)} files; drift check: {'clean' if not drift else 'FAILED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
