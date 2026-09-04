"""DB access layer for the Mojo port of database_integrity_check.py.

POLICY (2026-08-29): native Mojo DB drivers are immature — this fixture
IS the data layer (Python sqlite3/duckdb drivers, called from Mojo via
the bridge); the CHECK LOGIC lives in the Mojo port
(Mojo/src/common/integrity_check.mojo). python_baseline() runs the
ORIGINAL checker in-process for the perf comparison and golden counts.
"""

from __future__ import annotations

import sqlite3
import time

from bridge_utils import (  # Mojo/bench is on sys.path for every importer
    REPO,
    RESEARCH_DB,
    connect_duckdb_ro,
    connect_sqlite_ro,
)

_conn: sqlite3.Connection | None = None


def _sq() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = connect_sqlite_ro(RESEARCH_DB, row_factory=sqlite3.Row, query_only=True)
    return _conn


def entities():
    """(name, entity_type, file_path, normalized_name) for all entities."""
    return (
        _sq()
        .execute(
            "SELECT name, entity_type, file_path, normalized_name "
            "FROM entities ORDER BY entity_type, name"
        )
        .fetchall()
    )


def note_tag_rows():
    """(note_path, tag) rows — the port reads each note + parses YAML in
    Mojo (vendored mojo-yaml) to recompute staleness."""
    return _sq().execute("SELECT note_path, tag FROM note_tags").fetchall()


def relations_counts():
    """The 14 scalar integrity queries of check_relations, verbatim."""

    def one(sql):
        row = _sq().execute(sql).fetchone()
        if row is None:
            raise RuntimeError("expected row for COUNT(*) query")
        return row[0]

    return {
        "total": one("SELECT COUNT(*) FROM relations"),
        "self_loops": one("SELECT COUNT(*) FROM relations WHERE source = target"),
        "orphaned": one(
            "SELECT COUNT(*) FROM relations r WHERE r.source NOT IN "
            "(SELECT name FROM entities) OR r.target NOT IN "
            "(SELECT name FROM entities)"
        ),
        "po_no_hc": one(
            "SELECT COUNT(*) FROM relations p WHERE p.relation_type='part_of' "
            "AND NOT EXISTS (SELECT 1 FROM relations h WHERE h.source=p.target "
            "AND h.target=p.source AND h.relation_type='has_company')"
        ),
        "hc_no_po": one(
            "SELECT COUNT(*) FROM relations h WHERE h.relation_type='has_company' "
            "AND NOT EXISTS (SELECT 1 FROM relations p WHERE p.source=h.target "
            "AND p.target=h.source AND p.relation_type='part_of')"
        ),
        "circular": one(
            "SELECT COUNT(*) FROM relations r1 WHERE r1.source < r1.target "
            "AND EXISTS (SELECT 1 FROM relations r2 WHERE r2.source=r1.target "
            "AND r2.target=r1.source AND r2.relation_type=r1.relation_type)"
        ),
    }


def cache_reconcile_rows():
    """DuckDB vs SQLite row counts for the materialized cache tables."""
    con = connect_duckdb_ro()
    try:
        duck_counts = {}
        for (t,) in con.execute("SHOW TABLES").fetchall():
            if t.startswith(("v_", "e_")):
                row = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()  # noqa: S608
                if row is None:
                    raise RuntimeError(f"expected row for COUNT(*) from {t}")
                duck_counts[t] = row[0]
        return duck_counts
    finally:
        con.close()


def sqlite_cache_counts():
    """SQLite side of the cache reconciliation headline counts."""

    def one(sql):
        row = _sq().execute(sql).fetchone()
        if row is None:
            raise RuntimeError("expected row for COUNT(*) query")
        return row[0]

    return {
        "v_node": one("SELECT COUNT(*) FROM entities"),
        "e_dir": one("SELECT COUNT(*) FROM graph_edges"),
    }


def python_sections(reps=5):
    """Per-section timings of the ORIGINAL for the ported four sections:
    {section: seconds/rep}. Same warm in-process conditions as the port."""
    import sys
    import time as _t

    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from helpers.misc.database_integrity_check import DatabaseIntegrityChecker

    out = {"entities_paths": 0.0, "relations": 0.0, "note_tags": 0.0, "cache": 0.0}
    for _ in range(reps):
        chk = DatabaseIntegrityChecker(str(REPO / "memory" / "research.db"), str(REPO))
        t0 = _t.perf_counter()
        ents = chk.get_all_entities()
        for e in ents:
            if not e.get("file_path") and e["entity_type"] in (
                "sub_sector",
                "theme",
                "institution",
            ):
                continue
            chk.validate_file_path(e.get("file_path", ""), e.get("entity_type", ""))
        out["entities_paths"] += _t.perf_counter() - t0
        t0 = _t.perf_counter()
        chk.check_relations()
        out["relations"] += _t.perf_counter() - t0
        t0 = _t.perf_counter()
        chk.check_note_tags()
        out["note_tags"] += _t.perf_counter() - t0
        t0 = _t.perf_counter()
        chk.check_cache_consistency()
        out["cache"] += _t.perf_counter() - t0
        chk.close()
    return {k: v / reps for k, v in out.items()}


def python_spawn_wall():
    """Cold-process wall of the ORIGINAL CLI (interpreter + helpers.*
    imports + full suite + report write). The mojo port's cold wall is the
    ENCLOSING binary run — spawning it from here would self-recurse."""
    import subprocess
    import sys as _sys
    import time as _t

    t0 = _t.perf_counter()
    py = subprocess.run(  # noqa: S603  # repo-local script + venv python
        [_sys.executable, str(REPO / "helpers" / "misc" / "database_integrity_check.py")],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO,
        check=False,
    )
    return {"python_rc": py.returncode, "python_wall": _t.perf_counter() - t0}


def python_baseline(reps=1):
    """Run the ORIGINAL checker in-process (stdout suppressed).

    Returns (total_seconds, golden_counts_dict) — the goldens the Mojo
    port must reproduce: valid/invalid entities, missing paths, relation
    errors, stale note tags.
    """
    import contextlib
    import io
    import sys

    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from helpers.misc.database_integrity_check import DatabaseIntegrityChecker

    total = 0.0
    gold = {}
    for _ in range(reps):
        chk = DatabaseIntegrityChecker(str(REPO / "memory" / "research.db"), str(REPO))
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            res = chk.check_integrity()
        total += time.perf_counter() - t0
        gold = {
            "valid_entities": res["valid_entities"],
            "invalid_entities": res["invalid_entities"],
            "missing_file_paths": res["missing_file_paths"],
            "relations_errors": res["relations"]["errors"],
            "note_tags_stale": res["note_tags"]["stale"],
        }
    return total, gold


# ---------------------------------------------------------------------------
# FULL-port goldens (2026-08-30, proposal
# doc/improvements/proposals/mojo_db_integrity_port.md): run the ORIGINAL
# checker live and flatten every check result into canonical strings the
# Mojo tool must reproduce exactly (three-way parity: mojo vs python-live
# vs this canonical form). Data access in the tool is bridge-only; this
# fixture is the parity oracle, not the data layer.
# ---------------------------------------------------------------------------
def python_all_checks() -> dict[str, str]:  # noqa: C901
    import sys

    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from helpers.misc.database_integrity_check import (
        DatabaseIntegrityChecker,
        _CHECKS,
    )

    import contextlib
    import io

    chk = DatabaseIntegrityChecker()
    out: dict[str, str] = {}

    # -- header: entity/file-path validation (inline in check_integrity) --
    # (check_integrity prints progress lines; capture+discard them so the
    # tool's own stdout stays clean)
    with contextlib.redirect_stdout(io.StringIO()):
        res = chk.check_integrity()  # runs the full registry
    hdr = {
        "total_entities": res["total_entities"],
        "valid_entities": res["valid_entities"],
        "invalid_entities": res["invalid_entities"],
        "missing_file_paths": res["missing_file_paths"],
        "file_not_found": res["file_not_found"],
        "invalid_structure": res["invalid_structure"],
        "invalid_filename": res["invalid_filename"],
        "invalid_list_count": len(res["invalid_entities_list"]),
    }
    for k, v in hdr.items():
        out[f"hdr.{k}"] = str(v)
    bt = res["by_entity_type"]
    out["hdr.by_type"] = ",".join(
        f"{t}:{v['total']},{v['valid']},{v['invalid']},{v['missing_paths']}"
        for t, v in sorted(bt.items())
    )
    for k, v in res["summary"].items():
        out[f"hdr.summary.{k}"] = str(v)

    # -- the 17 registry checks --
    for c in _CHECKS:
        r = res[c.name]
        p = c.name
        if p == "relations":
            for k in (
                "total",
                "unknown_type",
                "self_loops",
                "orphaned",
                "type_mismatch",
                "part_of_without_has_company",
                "has_company_without_part_of",
                "belongs_to_endpoint_bad",
                "exposed_to_endpoint_bad",
                "circular",
                "errors",
            ):
                out[f"{p}.{k}"] = str(r[k])
        elif p in (
            "entity_tags",
            "note_tags",
            "events",
            "quotes",
            "company_metrics",
            "orphan_companies",
            "hierarchy",
            "db_meta",
        ):
            for k, v in r.items():
                if isinstance(v, (int, float, str)) and not isinstance(v, bool):
                    out[f"{p}.{k}"] = str(v)
                elif isinstance(v, list) and p == "db_meta":
                    out[f"{p}.{k}"] = ";".join(sorted(v))
        elif p == "normalization":
            out[f"{p}.missing"] = str(r["missing"])
            out[f"{p}.duplicates"] = ",".join(
                f"{k}:{v}" for k, v in sorted(r["duplicates"].items())
            )
            out[f"{p}.bad_format"] = ",".join(
                f"{d['name']}:{d['normalized_name']}"
                for d in sorted(r["bad_format"], key=lambda d: d["name"])
            )
            out[f"{p}.errors"] = str(r["errors"])
            out[f"{p}.file_mismatches"] = ",".join(
                f"{d['name']}:{d['normalized_name']}:{d['file_stem']}"
                for d in sorted(r["file_mismatches"], key=lambda d: d["name"])
            )
            out[f"{p}.orphaned_files"] = ",".join(sorted(r["orphaned_files"]))
            out[f"{p}.warnings"] = str(r["warnings"])
        elif p == "duplicate_tickers":
            out[f"{p}.groups"] = ",".join(
                f"{t}:{';'.join(ns)}" for t, ns in sorted(r["duplicate_ticker_groups"].items())
            )
            out[f"{p}.errors"] = str(r["errors"])
        elif p == "fuzzy_duplicates":
            out[f"{p}.pairs"] = ",".join(
                "|".join(sorted((d["name_a"], d["name_b"])))
                for d in sorted(
                    r["fuzzy_duplicate_pairs"], key=lambda d: (d["name_a"], d["name_b"])
                )
            )
            out[f"{p}.warnings"] = str(r["warnings"])
            out[f"{p}.errors"] = str(r["errors"])
        elif p == "validity_window":
            out[f"{p}.by_type"] = ",".join(
                f"{et}:{v['total']},{v['with_valid_from']},{v['missing_valid_from']},{v['with_valid_to']}"
                for et, v in sorted(r["by_type"].items())
            )
            out[f"{p}.warnings"] = str(r["warnings"])
        elif p == "graph_summary":
            out[f"{p}.entity_counts"] = ",".join(
                f"{k}:{v}" for k, v in sorted(r["entity_counts"].items())
            )
            out[f"{p}.edge_counts"] = ",".join(
                f"{k}:{v}" for k, v in sorted(r["edge_counts"].items())
            )
            s = r["sector_size_summary"]
            out[f"{p}.sector_size"] = (
                f"{s['sector_count']}:{s['min']}:{s['median']}:{s['max']}:{s['mean']}"
            )
            out[f"{p}.largest"] = ",".join(f"{d['sector']}:{d['n']}" for d in r["largest_sectors"])
            out[f"{p}.smallest"] = ",".join(
                f"{d['sector']}:{d['n']}" for d in r["smallest_sectors"]
            )
            out[f"{p}.market_cap"] = ",".join(
                f"{d['tier']}:{d['n']}" for d in r["market_cap_distribution"]
            )
        elif p == "market_cap_conflicts":
            out[f"{p}.conflicts"] = ",".join(
                f"{d['entity']}={'+'.join(d['tags'])}"
                for d in sorted(r["conflicts"], key=lambda d: d["entity"])
            )
            out[f"{p}.errors"] = str(r["errors"])
        elif p == "cache_consistency":
            out[f"{p}.skipped"] = str(int(bool(r.get("skipped"))))
            out[f"{p}.schema_version"] = repr(r.get("schema_version"))
            out[f"{p}.expected_schema_version"] = repr(r.get("expected_schema_version"))
            out[f"{p}.schema_version_drift"] = str(r.get("schema_version_drift", 0))
            out[f"{p}.row_mismatches"] = ",".join(
                f"{m['table']}:{m.get('duckdb')}:{m.get('sqlite')}"
                for m in sorted(r.get("row_mismatches", []), key=lambda m: m["table"])
            )
            out[f"{p}.errors"] = str(r.get("errors", 0))
            out[f"{p}.warnings"] = str(r.get("warnings", 0))
    chk.close()
    return out
