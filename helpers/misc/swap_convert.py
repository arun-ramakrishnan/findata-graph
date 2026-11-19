#!/usr/bin/env python3
"""swap_convert.py — S1 canonical converter: research.db (SQLite) → research.duckdb.

duckdb_research_swap_execution S1: build the canonical converted artifact
with DECLARED-TYPE fidelity (not the Step-1 inferred CTAS), then prove
answer-preservation against the live store.

What lands in the converted store:
- 33 carried tables (38 minus the FTS5 rollback artifacts — `note_search`
  + `_config`/`_content`/`_data`/`_idx` (+ `_docsize` when present,
  e.g. in unit-test fixtures; the live store has none) — dropped per the
  2026-11-19 ruling; the live FTS index is `memory/note_search.duckdb`,
  already DuckDB-native). `note_search_meta` is a plain table and IS carried.
- Declared column types (PRAGMA table_info → DuckDB map), NOT NULL,
  defaults (`datetime('now')` → `CURRENT_TIMESTAMP`), PRIMARY KEYs
  (composite included; PK columns forced NOT NULL — DuckDB requires it),
  FOREIGN KEYs WITHOUT cascade actions (DuckDB 2.x parse-rejects
  CASCADE/SET NULL/SET DEFAULT; semantics move to S3 code rewrites —
  original actions live in
  `findata/Misc/duckdb_research_swap_cascade_manifest.json`).
- The 3 views (relations, v_quote_speaker, company_metrics_ruled), with
  the SQLite GLOB in the ruled-grain view translated to regexp_matches
  (same 14-digit `_WEB.xml` selection, substr offsets preserved).
- All 23 user indexes (`COLLATE NOCASE` → `lower(col)` expression form;
  DESC order preserved).

What does NOT land (recorded, not silently lost):
- 16 triggers: DuckDB 2.0 has no BEFORE row triggers / UPDATE-DELETE row
  triggers / RAISE. The 10 agent_id ABORT guards stay application guards +
  the first-party checker; the 6 generation bumps move into the write
  funnel (S3). db_meta.generation itself carries verbatim as data.
- 5 FTS5 artifacts (above).

Engine facts this relies on (probed on duckdb 2.0.0, 2026-11-19): CREATE
INDEX works incl. expression + DESC forms; CHECK/UNIQUE/PK work; FKs work
only WITHOUT actions; CREATE TRIGGER exists but not in the shapes we need;
no FTS5 virtual tables; regexp_matches available.

Usage:
  python3 helpers/misc/swap_convert.py                # build + parity
  python3 helpers/misc/swap_convert.py --check STORE  # parity only

Exit 0 iff the artifact built (or was checked) AND every parity gate
passed; the JSON report is written next to the store by default.
Read-only on the live store.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent.parent

sys.path.insert(0, str(REPO))  # noqa: E402

import duckdb  # noqa: E402

from helpers.core.db import connect as sqlite_connect  # noqa: E402
from helpers.misc.swap_fk_integrity import scan_orphans  # noqa: E402

DEFAULT_SOURCE = REPO / "memory" / "research.db"
DEFAULT_DEST = REPO / "bench_data" / "duckdb_swap" / "stores" / "s1" / "research_converted.duckdb"

# FTS5 rollback residue: virtual table + its shadow tables (carried-vs-drop
# ruling 2026-11-19 — DROP; see module docstring / proposal progress log).
FTS_RESIDUE = frozenset(
    {
        "note_search",
        "note_search_config",
        "note_search_content",
        "note_search_data",
        "note_search_docsize",
        "note_search_idx",
    }
)
FTS_CARRY_NOTE = "note_search_meta"  # plain table, rebuilt by rebuild_note_search.py

# DuckDB 2.0 trigger dispositions (probed 2026-11-19; see proposal log).
TRIGGER_DISPOSITION = {
    "agent_id_registered_*": "application guard + first-party checker (S3)",
    "trg_*_gen": "explicit generation-counter increments in the write funnel (S3)",
}


def _duck_type(sqlite_type: str) -> str:
    """Declared-type map (Step 2 DDL types). Unknown/absent → VARCHAR."""
    t = (sqlite_type or "").strip().upper()
    if "INT" in t:  # INTEGER, INT, BIGINT, SMALLINT, TINYINT (not DATETIME)
        return "BIGINT"
    if t.startswith("BOOL"):
        return "BOOLEAN"
    if t.startswith(("REAL", "DOUBLE", "FLOAT", "NUMERIC", "DECIMAL")):
        return "DOUBLE"
    if t.startswith(("DATETIME", "TIMESTAMP")):
        return "TIMESTAMP"
    if t.startswith("DATE"):
        return "DATE"
    if "BLOB" in t:
        return "BLOB"
    return "VARCHAR"  # TEXT/CHAR(n)/VARCHAR/JSON-as-text and anything undeclared


def _duck_default(sqlite_default: str) -> str:
    """Translate SQLite default expressions DuckDB understands."""
    d = sqlite_default.strip()
    if d.lower().startswith("datetime(") or d.lower() in ("current_timestamp", "strftime('%s','now')"):
        return "CURRENT_TIMESTAMP"
    return d  # literal constants ('active', '{}', 0, 0.85, 1.0) pass through


def _read_schema(source: Path) -> dict:
    """Read the live schema: carried tables, their columns/PKs/FKs, views,
    user indexes, plus the recorded drop lists."""
    con = sqlite_connect(source, read_only=True, wal=False)
    try:
        all_tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        tables = [t for t in all_tables if t not in FTS_RESIDUE]
        schema: dict = {
            "source_tables": all_tables,
            "tables": {},
            "views": {},
            "indexes": {},
            "dropped_fts": sorted(FTS_RESIDUE & set(all_tables)),
        }
        for t in tables:
            cols = [
                {
                    "name": r[1],
                    "type": _duck_type(r[2]),
                    "sqlite_type": r[2] or "",
                    "notnull": bool(r[3]),
                    "default": _duck_default(r[4]) if r[4] is not None else None,
                    "pk": int(r[5]),
                }
                for r in con.execute(f"PRAGMA table_info('{t}')")
            ]
            fks: dict[int, dict] = {}
            # PRAGMA foreign_key_list row: id, seq, table, from, to, on_update, on_delete, match
            for r in con.execute(f"PRAGMA foreign_key_list('{t}')"):
                fk = fks.setdefault(r[0], {"parent": r[2], "from": [], "to": [], "on_update": r[5], "on_delete": r[6]})
                fk["from"].append(r[3])
                fk["to"].append(r[4])
            schema["tables"][t] = {"columns": cols, "fks": list(fks.values())}
        for name, sql in con.execute("SELECT name, sql FROM sqlite_master WHERE type='view' ORDER BY name"):
            schema["views"][name] = sql
        for name, tbl, sql in con.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL ORDER BY name"
        ):
            schema["indexes"][name] = {"table": tbl, "sql": sql}
        return schema
    finally:
        con.close()


def _translate_view(sql: str) -> str:
    """SQLite → DuckDB view SQL. Translate the one SQLite-ism in the tracked
    surface — the GLOB in company_metrics_ruled — to regexp_matches, so the
    canonical artifact carries engine-idiomatic SQL. (DuckDB 2.0 does parse
    GLOB natively as the ~~~ operator and the native form is also
    parity-true, but the regexp form was the §4-validated smoke shape.)"""
    out = sql.replace("CREATE VIEW ", "CREATE OR REPLACE VIEW ")
    glob_re = re.compile(r"source_ref GLOB '\*(?P<digits>[^']*)_WEB\.xml'")
    m = glob_re.search(out)
    if m:
        n_digits = m.group("digits").count("[0-9]")
        # ONE backslash in the emitted SQL literal: DuckDB passes single-quoted
        # strings through untouched, so \. reaches the regex engine as \.
        out = glob_re.sub("regexp_matches(source_ref, '_[0-9]{%d}_WEB\\.xml$')" % n_digits, out)
    return out


def _translate_index(table: str, sql: str) -> str:
    """SQLite → DuckDB index SQL: COLLATE NOCASE → lower(col); DESC passes."""
    m = re.match(r"CREATE\s+INDEX\s+(\S+)\s+ON\s+(\S+)\s*\((?P<cols>.*)\)\s*$", sql, re.I | re.S)
    if not m:
        raise ValueError(f"unparsed index SQL: {sql!r}")
    name, cols = m.group(1), m.group("cols")
    parts = []
    for raw in cols.split(","):
        col = raw.strip()
        if re.search(r"\s+COLLATE\s+NOCASE\s*$", col, re.I):
            # a NOCASE index on a column == case-insensitive lookup on that
            # column; DuckDB's equivalent is an expression index on lower(col).
            col = re.sub(r"\s+COLLATE\s+NOCASE\s*$", "", col, flags=re.I)
            col = f"lower({col})"
        parts.append(col)
    return f'CREATE INDEX "{name}" ON "{table}" ({", ".join(parts)})'


def _emit_table_ddl(t: str, spec: dict) -> str:
    cols_sql = []
    pk_cols = sorted(
        (c for c in spec["columns"] if c["pk"]), key=lambda c: c["pk"]
    )
    pk_names = {c["name"] for c in pk_cols}
    for c in spec["columns"]:
        seg = f'"{c["name"]}" {c["type"]}'
        if c["notnull"] or c["name"] in pk_names:
            seg += " NOT NULL"
        if c["default"] is not None:
            seg += f" DEFAULT {c['default']}"
        cols_sql.append(seg)
    if pk_cols:
        cols_sql.append("PRIMARY KEY (" + ", ".join(f'"{c["name"]}"' for c in pk_cols) + ")")
    for fk in spec["fks"]:
        # actions intentionally DROPPED (DuckDB 2.x parse-rejects CASCADE /
        # SET NULL); NO ACTION ≡ RESTRICT for the enforced parent lock, and
        # the real semantics are S3's code rewrites.
        cols = ", ".join(f'"{c}"' for c in fk["from"])
        target = ", ".join(f'"{c}"' for c in fk["to"] if c)
        ref = f'"{fk["parent"]}"' + (f" ({target})" if target else "")
        cols_sql.append(f"FOREIGN KEY ({cols}) REFERENCES {ref}")
    return f'CREATE TABLE "{t}" (\n  ' + ",\n  ".join(cols_sql) + "\n)"


def _load_order(tables: dict) -> list[str]:
    """Parents-first topological order (DuckDB enforces FKs at insert time;
    ALTER TABLE ADD FK is unsupported upstream, so FKs are declared at
    CREATE and children must load after parents). Self-referencing FKs
    (concepts.broader_id) don't constrain cross-table order."""
    deps = {
        t: {fk["parent"] for fk in spec["fks"] if fk["parent"] in tables and fk["parent"] != t}
        for t, spec in tables.items()
    }
    order: list[str] = []
    done: set[str] = set()
    pending = set(tables)
    while pending:
        ready = sorted(t for t in pending if deps[t] <= done)
        if not ready:  # cycle — shouldn't happen (self-FKs excluded above)
            raise ValueError(f"FK cycle among {sorted(pending)}")
        order.extend(ready)
        done |= set(ready)
        pending -= set(ready)
    return order


def _build(source: Path, dest: Path) -> tuple[dict, float]:
    """Build the converted store at dest; returns (report, seconds)."""
    schema = _read_schema(source)
    tables = schema["tables"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".duckdb.building")
    if tmp.exists():
        tmp.unlink()
    t0 = time.time()
    con = duckdb.connect(str(tmp))
    try:
        con.execute("LOAD sqlite_scanner")
        con.execute(f"ATTACH '{source}' AS rs (TYPE sqlite, READ_ONLY)")
        for t in _load_order(tables):
            spec = tables[t]
            con.execute(_emit_table_ddl(t, spec))
            cols = ", ".join(f'"{c["name"]}"' for c in spec["columns"])
            sel = f'SELECT {cols} FROM rs."{t}"'
            # Self-referencing FK (concepts.broader_id): DuckDB enforces FKs
            # per statement, so load NULL-reference roots first, then the rest.
            self_fk_cols: list[str] = []
            self_fk_to: list[str] = []
            pk_names = [
                c["name"] for c in sorted(
                    (c for c in spec["columns"] if c["pk"]), key=lambda c: c["pk"]
                )
            ]
            for fk in spec["fks"]:
                if fk["parent"] == t and len(fk["from"]) == 1:
                    target = fk["to"][0] if fk["to"] else (pk_names[0] if len(pk_names) == 1 else None)
                    if target:
                        self_fk_cols.append(fk["from"][0])
                        self_fk_to.append(target)
            if self_fk_cols:
                # Self-referencing FK (concepts.broader_id → concept_id):
                # DuckDB enforces FKs per statement, so load NULL-rooted rows
                # first, then iteratively load rows whose parent is present.
                # Leftover rows after convergence = dangling refs (SQLite
                # does not enforce FKs by default) — fail loudly, never
                # silently drop.
                f, pk_col = self_fk_cols[0], self_fk_to[0]
                con.execute(f'INSERT INTO "{t}" ({cols}) {sel} WHERE "{f}" IS NULL')
                while True:
                    # RETURNING count(*) is rejected (no aggregates in
                    # RETURNING) — count the returned PKs client-side.
                    # NOT EXISTS: never re-match rows already loaded (the
                    # parent-present condition stays true across iterations).
                    ret = con.execute(
                        f'INSERT INTO "{t}" ({cols}) {sel} s WHERE s."{f}" IS NOT NULL '
                        f'AND EXISTS (SELECT 1 FROM "{t}" d WHERE d."{pk_col}" = s."{f}") '
                        f'AND NOT EXISTS (SELECT 1 FROM "{t}" e WHERE e."{pk_col}" = s."{pk_col}") '
                        f'RETURNING "{pk_col}"'
                    ).fetchall()
                    if not ret:
                        break
                dangling = _scalar(
                    con,
                    f'SELECT COUNT(*) FROM rs."{t}" s WHERE s."{f}" IS NOT NULL '
                    f'AND NOT EXISTS (SELECT 1 FROM "{t}" d WHERE d."{pk_col}" = s."{f}")',
                )
                if dangling:
                    raise ValueError(
                        f"{t}: {dangling} row(s) reference a missing {pk_col} "
                        "(dangling self-FK in the live store)"
                    )
            else:
                con.execute(f'INSERT INTO "{t}" ({cols}) {sel}')
        for name, sql in schema["views"].items():
            con.execute(_translate_view(sql))
        for name, idx in schema["indexes"].items():
            con.execute(_translate_index(idx["table"], idx["sql"]))
    finally:
        con.close()
    dt = time.time() - t0
    tmp.replace(dest)  # atomic: never leave a half-built artifact
    return schema, dt


# ---------------------------------------------------------------- parity


def _spot_values(con: duckdb.DuckDBPyConnection, tables: set[str]) -> dict:
    """Typed spot values the Step-1 finding pinned (TIMESTAMP fidelity,
    int cin_year) plus stable probes — only for tables that exist, so the
    mini-store unit test can run the same battery."""
    spots: dict = {}
    if "entities" in tables:
        spots["entities.created_at.type"] = con.execute(
            "SELECT typeof(created_at) FROM entities WHERE created_at IS NOT NULL LIMIT 1"
        ).fetchone()
        spots["entities.birkenstock.created_at"] = con.execute(
            "SELECT typeof(created_at), created_at::VARCHAR FROM entities WHERE name LIKE 'Birkenstock%' LIMIT 1"
        ).fetchone()
        spots["entities.cin_year.type"] = con.execute(
            "SELECT typeof(cin_year) FROM entities WHERE cin_year IS NOT NULL LIMIT 1"
        ).fetchone()
    if "company_metrics" in tables:
        spots["company_metrics.value_num.type"] = con.execute(
            "SELECT typeof(value_num) FROM company_metrics LIMIT 1"
        ).fetchone()
    if "db_meta" in tables:
        spots["db_meta.generation"] = con.execute(
            "SELECT value FROM db_meta WHERE key='generation'"
        ).fetchone()
    return spots


def _scalar(con: Any, sql: str):
    """fetchone() with an explicit None guard (duckdb types it Optional)."""
    row = con.execute(sql).fetchone()
    if row is None:
        raise ValueError(f"query returned no rows: {sql}")
    return row[0]


def _parity(source: Path, dest: Path, schema: dict) -> tuple[bool, dict[str, Any]]:
    """Full S1 parity battery on the built artifact."""
    report: dict[str, Any] = {"tables": {}, "views": {}, "indexes": {}, "fk": {}, "spots": {}}
    ok = True
    tables = schema["tables"]

    live = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    duck = duckdb.connect(str(dest), read_only=True)
    try:
        # 1. row counts + column order + declared types, 33/33 carried
        for t, spec in tables.items():
            cols = [c["name"] for c in spec["columns"]]
            types = [c["type"] for c in spec["columns"]]
            n_live = _scalar(live, f'SELECT COUNT(*) FROM "{t}"')
            n_duck = _scalar(duck, f'SELECT COUNT(*) FROM "{t}"')
            duck_info = duck.execute(f'PRAGMA table_info("{t}")').fetchall()
            duck_cols = [r[1] for r in duck_info]
            duck_types = [(r[2] or "").upper() for r in duck_info]
            row_ok = n_live == n_duck
            col_ok = duck_cols == cols
            type_ok = duck_types == types
            report["tables"][t] = {
                "live": n_live,
                "duck": n_duck,
                "row_parity": row_ok,
                "column_order_parity": col_ok,
                "type_parity": type_ok,
            }
            if not type_ok:
                report["tables"][t]["type_diff"] = [
                    {"col": c, "expected": e, "got": g}
                    for c, e, g in zip(cols, types, duck_types)
                    if e != g
                ]
            ok = ok and row_ok and col_ok and type_ok

        # 2. FK orphans on both engines
        fk_live = live.execute("PRAGMA foreign_key_check").fetchall()
        findings, _edges = scan_orphans(duck)
        report["fk"] = {
            "live_foreign_key_check_violations": len(fk_live),
            "duck_orphans": len(findings),
            "duck_findings": findings[:10],
        }
        ok = ok and not fk_live and not findings

        # 3. view result-set equality (sort ROWS, then hash — views have no ORDER BY)
        for name in schema["views"]:
            cols = [r[1] for r in live.execute(f'PRAGMA table_info("{name}")')]
            sel = ", ".join(f'"{c}"' for c in cols)

            def _digest(rows: list[tuple]) -> str:
                h = hashlib.sha256()
                for line in sorted(repr(sorted(str(v) for v in row)) for row in rows):
                    h.update(line.encode())
                return h.hexdigest()

            live_rows = live.execute(f'SELECT {sel} FROM "{name}"').fetchall()
            duck_rows = duck.execute(f'SELECT {sel} FROM "{name}"').fetchall()
            match = _digest(live_rows) == _digest(duck_rows)
            report["views"][name] = {"rows": len(live_rows), "result_set_parity": match}
            ok = ok and match

        # 4. index coverage (23 expected)
        duck_idx = {r[0] for r in duck.execute("SELECT index_name FROM duckdb_indexes()").fetchall()}
        expected = set(schema["indexes"])
        report["indexes"] = {
            "expected": len(expected),
            "created": len(expected & duck_idx),
            "missing": sorted(expected - duck_idx),
        }
        ok = ok and not (expected - duck_idx)

        # 5. typed spot values (conditional on carried tables)
        spots = _spot_values(duck, set(tables))
        report["spots"] = {k: [str(v) for v in vals] if vals else None for k, vals in spots.items()}
        if "entities" in tables:
            created_at_type = spots.get("entities.created_at.type")
            ok = ok and bool(created_at_type) and created_at_type[0] == "TIMESTAMP"
            birk = spots.get("entities.birkenstock.created_at")
            ok = ok and (birk is None or birk[0] == "TIMESTAMP")
            cin_year_type = spots.get("entities.cin_year.type")
            ok = ok and bool(cin_year_type) and cin_year_type[0].startswith("BIGINT")
        if "company_metrics" in tables:
            vm = spots.get("company_metrics.value_num.type")
            ok = ok and bool(vm) and vm[0].startswith("DOUBLE")
    finally:
        live.close()
        duck.close()

    report["dropped_fts"] = schema["dropped_fts"]
    report["carried_tables"] = len(tables)
    report["source_tables"] = len(schema["source_tables"])
    report["parity_ok"] = ok
    return ok, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="live SQLite store (read-only)")
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="converted store to build")
    ap.add_argument("--report", type=Path, default=None, help="parity report JSON (default: <dest>.report.json)")
    ap.add_argument("--check", type=Path, default=None, help="skip build; run the parity battery on this store")
    ap.add_argument("--no-build", action="store_true", help="report dispositions only")
    args = ap.parse_args(argv)

    report_path = args.report or (args.check or args.dest).with_suffix(".report.json")
    report: dict[str, Any]
    if args.check:
        schema = _read_schema(args.source)
        ok, report = _parity(args.source, args.check, schema)
        report["mode"] = "check"
        report["store"] = str(args.check)
    elif args.no_build:
        schema = _read_schema(args.source)
        no_build_report: dict[str, Any] = {
            "mode": "no-build",
            "carried_tables": len(schema["tables"]),
            "dropped_fts": schema["dropped_fts"],
        }
        ok, report = True, no_build_report
    else:
        schema, dt = _build(args.source, args.dest)
        ok, report = _parity(args.source, args.dest, schema)
        report["mode"] = "build"
        report["store"] = str(args.dest)
        report["build_seconds"] = round(dt, 1)

    report["dispositions"] = {
        "fk_actions": "dropped at DDL (DuckDB 2.x parse-rejects CASCADE/SET NULL); semantics = S3 code rewrites; originals in findata/Misc/duckdb_research_swap_cascade_manifest.json",
        "triggers": TRIGGER_DISPOSITION,
        "fts_residue": "dropped (virtual table unsupported everywhere); note_search_meta carried",
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.no_build:
        print(f"swap-convert: no-build; carried={report['carried_tables']} dropped_fts={report['dropped_fts']}")
        return 0
    verdict = "PASS" if ok else "FAIL"
    print(
        f"swap-convert: {verdict} store={args.check or args.dest} "
        f"tables={report['carried_tables']}/{report['source_tables']} "
        f"indexes={report['indexes']['created']}/{report['indexes']['expected']} "
        f"views={len(report['views'])} fk_orphans={report['fk']['duck_orphans']}"
    )
    if not ok:
        bad = {
            t: r
            for t, r in report["tables"].items()
            if not (r["row_parity"] and r["column_order_parity"] and r["type_parity"])
        }
        if bad:
            print("  table mismatches:", json.dumps(bad)[:400])
        if report["indexes"]["missing"]:
            print("  missing indexes:", report["indexes"]["missing"])
        for r in report["fk"]["duck_findings"]:
            print("  orphan:", r)
    print(f"  report: {report_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
