#!/usr/bin/env python3
"""DuckDB watchlist S1 — perf smoke leg (advisory, never a gate).

Runs representative lexical queries against the live sidecars and
reports, per engine:

- median latency of the engine's search call (3 runs);
- the FTS path actually taken by the query: ``topk`` (SQL postings
  top-k) vs ``macro`` (match_bm25 fallback) — a drift toward ``macro``
  is the L2 watch item (whole-table seq-scan);
- whether the vector mirror exists (doc ``doc_vectors`` / note
  ``note_search_vec``) — its absence means the hybrid cosine leg falls
  back to the Python scan (L3);
- result counts.

Exit code is always 0 (advisory). Run::

    .venv/bin/python3 helpers/maintenance/perf_smoke.py
    .venv/bin/python3 helpers/maintenance/perf_smoke.py --json
    .venv/bin/python3 helpers/maintenance/perf_smoke.py --store note

Skipped stores (absent sidecar) are reported, not fatal.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_QUERIES = [
    "shrimp feed",
    "aquaculture",
    "market share",
    "to",  # allowlisted macro on script (dict stopword); topk elsewhere
]

_STORES = {
    "doc": {
        "path": _REPO_ROOT / "memory" / "doc_search.duckdb",
        "fts_table": "fts_main_doc_search",
        "base_table": "doc_search",
        "id_column": "id",
        "mirror": "doc_vectors",
        "shadow": "doc_search",
    },
    "note": {
        "path": _REPO_ROOT / "memory" / "note_search.duckdb",
        "fts_table": "fts_main_note_search",
        "base_table": "note_search",
        "id_column": "id",
        "mirror": None,
        "mirror_path": _REPO_ROOT / "memory" / "note_vectors.duckdb",
        "mirror_table": "note_search_vec",
        "shadow": "note_search",
    },
    "convo": {
        "path": _REPO_ROOT / "memory" / "convo_search.duckdb",
        "fts_table": "fts_main_convo_search",
        "base_table": "convo_search",
        "id_column": "part_id",
        "mirror": None,
        "shadow": "convo_search",
    },
    "script": {
        "path": _REPO_ROOT / "memory" / "script_search.duckdb",
        "fts_table": "fts_main_script_search",
        "base_table": "script_search",
        "id_column": "id",
        "mirror": None,
        "shadow": "script_search",
    },
    "memory": {
        "path": _REPO_ROOT / "memory" / "memory_search.duckdb",
        "fts_table": "fts_main_memory_search",
        "base_table": "memory_search",
        "id_column": "id",
        "mirror": None,
        "shadow": "memory_search",
    },
    "git": {
        "path": _REPO_ROOT / "memory" / "git_search.duckdb",
        "fts_table": "fts_main_git_search",
        "base_table": "git_search",
        "id_column": "id",
        "mirror": None,
        "shadow": "git_search",
    },
}


def _open_store(path: Path):
    from helpers.core.duckdb_shared import memory_cap_config

    import duckdb

    return duckdb.connect(str(path), read_only=True, **memory_cap_config())


def _engine_search(engine: str, conn, q: str) -> int:
    """Run the engine's real search call; returns result count."""
    if engine == "doc":
        from helpers.maintenance import rebuild_doc_search as rds

        out = rds.search_docs(conn, q, limit=10, hybrid=False)
        return len(out.get("results", []))
    if engine == "note":
        from helpers.misc import note_query as nq

        _rows, total = nq.search_notes_duckdb(conn, q, limit=10)
        return total
    if engine == "convo":
        from helpers.misc import convo_query as cq

        return len(cq._fts_hits_duckdb(conn, q, limit=10))
    if engine == "script":
        from helpers.maintenance import rebuild_script_search as rss

        out = rss._search_scripts_duckdb(conn, q, limit=10, hybrid=False)
        return len(out.get("results", []))
    if engine == "memory":
        from helpers.maintenance import rebuild_memory_search as rms

        out = rms.search_memories(conn, q, limit=10, hybrid=False)
        return len(out.get("results", []))
    if engine == "git":
        from helpers.maintenance import rebuild_git_search as rgs

        out = rgs.search_commits(conn, q, limit=10, hybrid=False)
        return len(out.get("results", []))
    return 0


def _topk_mode(conn, spec: dict, q: str) -> str:
    """Classify the lexical path the query WOULD take: 'topk' or 'macro'."""
    from helpers.maintenance import rebuild_doc_search as rds

    try:
        _rows, mode = rds.fts_topk(
            conn,
            fts_table=spec["fts_table"],
            base_table=spec["base_table"],
            tokens=rds.fts_query_tokens_duckdb(q),  # fts_query_token_parity S1
            limit=10,
            id_column=spec["id_column"],
        )
        return mode
    except Exception:  # noqa: S110  # probe is advisory
        return "macro"


def _mirror_present(conn, spec: dict) -> bool:
    mirror = spec.get("mirror") or spec.get("mirror_path")
    if not mirror:
        return False
    try:
        if spec.get("mirror_path"):
            # separate mirror file (note_vectors.duckdb)
            path = spec["mirror_path"]
            if not path.exists():
                return False
            from helpers.core.duckdb_shared import memory_cap_config

            import duckdb

            mconn = duckdb.connect(str(path), read_only=True, **memory_cap_config())
            try:
                row = mconn.execute(
                    "SELECT COUNT(*) FROM duckdb_tables() WHERE table_name = ?",
                    (spec["mirror_table"] or "note_search_vec",),
                ).fetchone()
                return bool(row and row[0])
            finally:
                mconn.close()
        row = conn.execute(
            "SELECT COUNT(*) FROM duckdb_tables() WHERE table_name = ?",
            (mirror,),
        ).fetchone()
        return bool(row and row[0])
    except Exception:  # noqa: S110
        return False


def run(store_filter: str | None = None) -> list[dict]:
    results = []
    for engine, spec in _STORES.items():
        if store_filter and engine != store_filter:
            continue
        path_raw = spec["path"]
        path = Path(path_raw) if path_raw is not None else _REPO_ROOT / "missing"
        if not path.exists():
            results.append({"engine": engine, "skipped": "store absent"})
            continue
        try:
            if engine == "convo":
                from helpers.misc import convo_query as cq

                conn = cq.connect(path)
                raw = conn.ddb
            else:
                conn = _open_store(path)
                raw = conn
        except Exception as exc:  # noqa: BLE001  # advisory
            results.append({"engine": engine, "skipped": f"open failed: {exc}"})
            continue
        try:
            mirror = _mirror_present(raw, spec)
            for q in _QUERIES:
                latencies = []
                count = 0
                for _ in range(3):
                    t0 = __import__("time").perf_counter()
                    count = _engine_search(engine, conn, q)
                    latencies.append((__import__("time").perf_counter() - t0) * 1000)
                results.append(
                    {
                        "engine": engine,
                        "query": q,
                        "mode": _topk_mode(raw, spec, q),
                        "median_ms": round(statistics.median(latencies), 1),
                        "results": count,
                        "mirror": mirror,
                    }
                )
        finally:
            conn.close()
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--json", action="store_true", help="emit JSON instead of the table")
    p.add_argument("--store", choices=sorted(_STORES), default=None, help="only probe one engine")
    args = p.parse_args(argv)

    rows = run(args.store)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    header = (
        f"{'engine':<7} {'query':<16} {'mode':<6} {'median_ms':>9} {'results':>8} {'mirror':>7}"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        if "skipped" in r:
            print(f"{r['engine']:<7} {r['skipped']}")
            continue
        print(
            f"{r['engine']:<7} {r['query']:<16} {r['mode']:<6} "
            f"{r['median_ms']:>9.1f} {r['results']:>8} {str(r['mirror']):>7}"
        )
    print("-" * len(header))
    # advisory summary: any macro-mode run is a watch flag. Since
    # fts_query_token_parity S1 (2026-11-18) the splitter mirrors the
    # analyzer, so a query should reach the macro fallback ONLY when its
    # tokens genuinely cannot resolve in that store's dict. The bank is
    # therefore an ALLOWLIST with a reason per pair; anything else joining
    # it is a regression signal (a punctuated query the splitter should
    # have resolved, or a dict term that vanished), and an allowlisted
    # pair leaving it means the corpus grew and the entry can retire.
    EXPECTED_MACRO = {
        (
            "script",
            "aquaculture",
        ): "0 script rows contain the term — token absent from the script dict; macro is the designed empty-result path",
        (
            "script",
            "to",
        ): "'to' is not in the script dict (corpus-scale stopword); macro is the designed path",
        (
            "memory",
            "aquaculture",
        ): "0 memory rows contain the term — token absent from the memory dict; macro is the designed empty-result path (fts_stem_ladder S4)",
    }
    probed = {r["engine"] for r in rows if "skipped" not in r}
    macros = [
        (r["engine"], r["query"]) for r in rows if r.get("mode") == "macro" and "skipped" not in r
    ]
    unexpected = [p for p in macros if p not in EXPECTED_MACRO]
    retired = [p for p in EXPECTED_MACRO if p[0] in probed and p not in macros]
    if unexpected:
        reasons = "; ".join(f"{e}/{q}" for e, q in unexpected)
        print(
            f"WARNING (macro regression): {reasons} took the whole-table "
            "match_bm25 fallback — the S1 analyzer-matched splitter should "
            "have resolved this, or a dict term vanished. Investigate "
            "before the next perf gate."
        )
    elif retired:
        names = "; ".join(f"{e}/{q}" for e, q in retired)
        print(
            f"NOTE: expected-macro pair(s) now resolve on topk — retire the "
            f"allowlist entry: {names}"
        )
    else:
        print(f"OK: macro bank matches the allowlist ({len(macros)} pair(s), all expected).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
