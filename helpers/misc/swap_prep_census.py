#!/usr/bin/env python3
r"""swap_prep_census.py — Step 0 census for the research.db SQLite→DuckDB swap readiness program.

Advisory, read-only, always exits 0. Prints the §2.2 census metrics of
``doc/improvements/archive/database/duckdb_research_swap_steps.md`` using
deterministic, pinned methods so later steps (and later sessions) can compare
against a dated baseline. See the proposal's Step 0 acceptance.

No production writes: every research.db open is read-only (mode=ro) and the
only subprocess calls are ``rg`` counts over ``helpers/`` (read-only scans).

Methods pinned (do not silently change — the Step 0 baseline is dated to them):

* tables        sqlite_master type='table', excluding ``sqlite_*`` (38)
* schema_version db_meta ``schema_version`` value (17)
* FK links      regex ``REFERENCES\s+\S+`` over CREATE TABLE sql (24)
* FK action      regex ``ON\s+(DELETE|UPDATE)\s+(CASCADE|SET NULL|RESTRICT|NO ACTION)``
  clauses        over the same sql; only clauses whose trailing modifier list
                contains a non-NO-ACTION action are counted (22) — split by
                (directive, action) pair tallied per clause
* PRAGMA sites  rg -c "PRAGMA" helpers/ (150)
* connect( sites  rg -c "connect\\(" helpers/ --glob '*.py' (485)
* writer files   rg -l -e "INSERT\\s+(INTO|OR)" -e "UPDATE\\s+" -e
                "DELETE\\s+FROM" -e "REPLACE\\s+INTO" -e "UPSERT"
                helpers/ --glob '*.py' (92; filing said 93 from an
                unreproducible pattern — this pinned pattern is now truth)
* importers      rg -l "helpers\\.core\\.db" helpers/ --glob '*.py' (118)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RESEARCH_DB = _REPO_ROOT / "memory" / "research.db"
_HELPERS = _REPO_ROOT / "helpers"

# Script runs as helpers/misc/swap_prep_census.py (sys.path[0] = helpers/misc),
# so the repo root must be on sys.path before helpers.* imports resolve.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect as _db_connect  # noqa: E402

_REFERENCE_RE = re.compile(r"REFERENCES\s+\S+", re.I)
_ACTION_RE = re.compile(r"ON\s+(DELETE|UPDATE)\s+(CASCADE|SET NULL|RESTRICT|NO ACTION)", re.I)

# Pinned writer pattern (see module docstring — B variant, 92 files).
_WRITER_PATTERN = [
    r"INSERT\s+(INTO|OR)",
    r"UPDATE\s+",
    r"DELETE\s+FROM",
    r"REPLACE\s+INTO",
    r"UPSERT",
]

_PRAGMA_PATTERN = "PRAGMA"
_CONNECT_PATTERN = r"connect\("
_IMPORTER_PATTERN = r"helpers\.core\.db"

# The census script must not pollute its own measurements (its docstring
# contains the very tokens it counts) — exclude it from every rg scan.
_SELF_EXCLUDE = ["-g", "!**/swap_prep_census.py"]


def _rg_count(args, path):
    proc = subprocess.run(
        ["rg", *args, str(path)],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        check=False,
    )
    return proc


def _sum_rg_c(file):
    """Sum `rg -c` per-file tallies (output lines are ``path:count``)."""
    total = 0
    for line in file.stdout.splitlines():
        _path, _, count = line.rpartition(":")
        if count.isdigit():
            total += int(count)
    return total


def _census_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def _census_schema_version(conn):
    rows = conn.execute("SELECT value FROM db_meta WHERE key = 'schema_version'").fetchall()
    return int(rows[0][0]) if rows else None


def _census_fk(conn, tables):
    """Return (links, link_tables, action_clauses, action_split, action_tables)."""
    links = 0
    link_tables = set()
    action_clauses = 0
    action_split = Counter()
    action_tables = set()
    for name in tables:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?", (name,)
        ).fetchone()
        sql = row[0] if row else None
        if not sql:
            continue
        link_tables_this = 0
        for _ in _REFERENCE_RE.finditer(sql):
            links += 1
            link_tables_this += 1
        if link_tables_this:
            link_tables.add(name)
        # Split REFERENCES clauses (roughly per FK column clause) so each clause
        # that carries at least one non-NO-ACTION modifier is counted once.
        for clause in re.split(r"REFERENCES\s+\S+\s*\([^)]*\)", sql):
            tail = clause
            actions = _ACTION_RE.findall(tail)
            if not actions:
                continue
            real = [a for a in actions if a[1].upper() != "NO ACTION"]
            if real:
                action_clauses += 1
                action_tables.add(name)
                for directive, act in actions:
                    if act.upper() != "NO ACTION":
                        action_split[(directive.upper(), act.upper())] += 1
    return links, link_tables, action_clauses, action_split, action_tables


def _run(quiet=False):
    out: dict[str, object] = {}

    # research.db (read-only; helpers.core.db.connect applies project pragmas)
    conn = _db_connect(_RESEARCH_DB, read_only=True, wal=False)
    try:
        tables = _census_tables(conn)
        out["tables"] = len(tables)
        out["schemas"] = _census_schema_version(conn)
        links, link_tables, action_clauses, action_split, action_tables = _census_fk(conn, tables)
        out["fk_links"] = links
        out["fk_link_tables"] = len(link_tables)
        out["fk_action_clauses"] = action_clauses
        out["fk_action_tables"] = len(action_tables)
        out["fk_action_split"] = {f"{d} {a}": n for (d, a), n in sorted(action_split.items())}
    finally:
        conn.close()

    # rg counts over helpers/
    pragma = _rg_count(["-c", _PRAGMA_PATTERN, *_SELF_EXCLUDE], _HELPERS)
    out["pragmama_sites"] = _sum_rg_c(pragma) if pragma.returncode == 0 else None
    connect_ = _rg_count(["-c", _CONNECT_PATTERN, "--glob", "*.py", *_SELF_EXCLUDE], _HELPERS)
    out["connect_sites"] = _sum_rg_c(connect_) if connect_.returncode == 0 else None
    writers = _rg_count(
        ["-l", *[e for p in _WRITER_PATTERN for e in ("-e", p)], "--glob", "*.py", *_SELF_EXCLUDE],
        _HELPERS,
    )
    out["writer_files"] = len(writers.stdout.splitlines()) if writers.returncode == 0 else None
    importers = _rg_count(["-l", _IMPORTER_PATTERN, "--glob", "*.py", *_SELF_EXCLUDE], _HELPERS)
    out["importers"] = len(importers.stdout.splitlines()) if importers.returncode == 0 else None

    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit as JSON")
    args = ap.parse_args(argv)

    data = _run(quiet=args.json)

    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print("swap-prep-census (Step 0 baseline — 2026-11-17)")
        print(f"  tables              {data['tables']}")
        print(f"  schema_version      {data['schemas']}")
        print(f"  FK links           {data['fk_links']} across {data['fk_link_tables']} tables")
        print(
            f"  FK action clauses   {data['fk_action_clauses']} across {data['fk_action_tables']} tables"
        )
        for k, v in data["fk_action_split"].items():
            print(f"      {k}: {v}")
        print(f"  PRAGMA sites       {data['pragmama_sites']}")
        print(f"  connect( sites      {data['connect_sites']}")
        print(f"  writer files        {data['writer_files']}")
        print(f"  importers           {data['importers']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
