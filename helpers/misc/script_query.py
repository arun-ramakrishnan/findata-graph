#!/usr/bin/env python3
"""
Query the script_search sidecar index from the command line.

The agent-facing surface of the script-metadata-search proposal
(doc/improvements/archive/tooling/script_metadata_search.md): a future session
asks "which script audits relation diffs" / "which test file covers the
yfinance driver" / "what does make qa run" and gets ranked hits with the
purpose line, instead of guessing filenames or grepping. Wraps the same
query core an eventual /api/scripts/search would use
(helpers/maintenance/rebuild_script_search.search_scripts). Code STRUCTURE
questions (symbols, callers) stay with codebase-memory-mcp — this index
is the INTENT layer: what each script is FOR, its CLI surface, its make
wiring, its tests.

Usage:
    python3 helpers/misc/script_query.py "audit relation diffs"
    python3 helpers/misc/script_query.py "yfinance" --kind test
    python3 helpers/misc/script_query.py "integrity" --area misc --json
    python3 helpers/misc/script_query.py "what does qa run" --kind make

A stale index still answers (with a stderr warning naming the refresh
command — slightly outdated knowledge beats none); a missing index is a
hard exit 1 with the build command.

Exit codes: 0 answered (possibly empty), 1 index missing, 2 usage error.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Repo root: helpers/misc/script_query.py -> parents[2]. Must be on sys.path
# BEFORE the `from helpers.maintenance...` below so the script works as a
# subprocess the same way it works under pytest. (House bootstrap.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.maintenance import rebuild_script_search as rss  # noqa: E402

_MARK = re.compile(r"</?mark>")
_WS = re.compile(r"\s+")


def _plain_snippet(snippet: str, cap: int = 240) -> str:
    """Strip <mark> tags + collapse whitespace for terminal output."""
    text = _WS.sub(" ", _MARK.sub("", snippet)).strip()
    return text[: cap - 1] + "…" if len(text) > cap else text


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Query the script metadata index (script_search sidecar).",
    )
    p.add_argument("query", help="free-text query; punctuation is safe")
    p.add_argument("--kind", choices=["script", "test", "make"], default=None,
                   help="filter: script | test | make rows")
    p.add_argument("--area", default=None,
                   help="filter by area (helpers subdir | app | test | make)")
    p.add_argument("--limit", type=int, default=5, help="max hits (default 5)")
    p.add_argument("--db", default=None, help="sidecar path (default: module SCRIPT_DB)")
    p.add_argument("--bm25", action="store_true",
                   help="lexical leg only (skip the cosine re-rank)")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="emit the raw result dicts as JSON")
    args = p.parse_args(argv)

    db_path = Path(args.db) if args.db else rss.SCRIPT_DB
    conn = rss.connect_script_db(db_path)
    try:
        if not rss.script_index_ready(conn):
            print(
                "script_search index not built. Run:\n"
                "  python3 helpers/maintenance/rebuild_script_search.py",
                file=sys.stderr,
            )
            return 1
        if rss.script_index_stale(conn):
            print(
                "WARNING: helpers/tests/Makefile changed since the last "
                "index — results may be outdated. Refresh: "
                "python3 helpers/maintenance/rebuild_script_search.py",
                file=sys.stderr,
            )
        out = rss.search_scripts(
            conn, args.query, limit=max(1, min(args.limit, 100)),
            kind=args.kind, area=args.area, hybrid=not args.bm25,
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
    for hit in out["results"]:
        where = f"make {hit['path']}" if hit["kind"] == "make" else hit["path"]
        print(f"{where}  [{hit['kind']}/{hit['area']}]  {hit['score']}")
        purpose = (hit["purpose"] or "").strip()
        if purpose:
            print(f"    {_plain_snippet(purpose, 200)}")
        snip = _plain_snippet(hit["snippet"])
        if snip and snip[:60] != purpose[:60]:
            print(f"    {snip}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
