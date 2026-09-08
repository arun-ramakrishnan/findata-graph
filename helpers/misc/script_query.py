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
questions (symbols, callers) stay with ripwire (AGENTS.md posture) — this
index is the INTENT layer: what each script is FOR, its CLI surface, its make
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


def _render_script_hits(hits: list[dict]) -> None:
    for hit in hits:
        where = f"make {hit['path']}" if hit["kind"] == "make" else hit["path"]
        print(f"{where}  [{hit['kind']}/{hit['area']}]  {hit['score']}")
        purpose = (hit["purpose"] or "").strip()
        if purpose:
            print(f"    {_plain_snippet(purpose, 200)}")
        snip = _plain_snippet(hit["snippet"])
        if snip and snip[:60] != purpose[:60]:
            print(f"    {snip}")


def main(argv: list[str] | None = None) -> int:
    from helpers.maintenance import rebuild_common as rbc

    def _extra_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--kind",
            choices=["script", "test", "make", "mojo", "ts"],
            default=None,
            help="filter: script | test | make | mojo | ts rows",
        )
        p.add_argument(
            "--area",
            default=None,
            help="filter by area (helpers subdir | app | test | make | "
            "Mojo package: bench/common | TS package: core/views/"
            "src/types)",
        )

    def _search_kwargs(args: argparse.Namespace) -> dict:
        return {"kind": args.kind, "area": args.area}

    return rbc.run_query_cli(
        argv,
        rbc.QueryCliSpec(
            description="Query the script metadata index (script_search sidecar).",
            default_db=lambda: rss.SCRIPT_DB,
            db_flag_help="sidecar path (default: module SCRIPT_DB)",
            connect_fn=rss.connect_script_db,
            ready_fn=rss.script_index_ready,
            not_built_msg=(
                "script_search index not built. Run:\n"
                "  python3 helpers/maintenance/rebuild_script_search.py"
            ),
            stale_fn=rss.script_index_stale,
            stale_warning=(
                "WARNING: helpers/tests/Makefile/Mojo/frontend sources changed "
                "since the last index — results may be outdated. Refresh: "
                "python3 helpers/maintenance/rebuild_script_search.py"
            ),
            search_fn=rss.search_scripts,
            render_hits=_render_script_hits,
            extra_args=_extra_args,
            search_kwargs=_search_kwargs,
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
