#!/usr/bin/env python3
"""
Query the memory_search sidecar index from the command line.

The agent-facing surface of the harness-memory index (rebuilt by
helpers/maintenance/rebuild_memory_search.py): a session asks "what did we
settle about stg refresh scope" / "which harness owns the code-test
conventions" / "what was the vss verdict" and gets ranked hits across ALL
THREE harness memory pools — zcode (markdown pool + MEMORY.md index),
prime-rlm (harness_state.json global store), opencode (logfmt records) —
instead of reading whole pools. Hits carry the recall unit as the locator
(zcode file path; prime/opencode 'path#record') since every harness
recalls whole records, not sections.

Usage:
    python3 helpers/misc/memory_query.py "stg refresh scope"
    python3 helpers/misc/memory_query.py "embed cache" --kind prime
    python3 helpers/misc/memory_query.py "operator contract" --json

A stale index still answers (with a stderr warning naming the refresh
command — slightly outdated knowledge beats none); a missing index is a
hard exit 1 with the build command.

Exit codes: 0 answered (possibly empty), 1 index missing, 2 usage error.
"""

import argparse
import re
import sys
from pathlib import Path

# Repo root: helpers/misc/memory_query.py -> parents[2]. Must be on sys.path
# BEFORE the `from helpers.maintenance...` below so the script works as a
# subprocess the same way it works under pytest. (House bootstrap.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.maintenance import rebuild_memory_search as rms  # noqa: E402

_MARK = re.compile(r"</?mark>")
_WS = re.compile(r"\s+")


def _plain_snippet(snippet: str, cap: int = 240) -> str:
    """Strip <mark> tags + collapse whitespace for terminal output."""
    text = _WS.sub(" ", _MARK.sub("", snippet)).strip()
    return text[: cap - 1] + "…" if len(text) > cap else text


def _render_memory_hits(hits: list[dict]) -> None:
    for hit in hits:
        print(f"{hit['kind']}/{hit['name']}  [{hit['score']}]")
        purpose = (hit["purpose"] or "").strip()
        if purpose and purpose != hit["title"]:
            print(f"    {_plain_snippet(purpose, 200)}")
        snip = _plain_snippet(hit["snippet"])
        if snip:
            print(f"    {snip}")
        print(f"    {hit['path']}")


def main(argv: list[str] | None = None) -> int:
    from helpers.maintenance import rebuild_common as rbc

    def _extra_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--kind",
            choices=["zcode", "prime", "opencode"],
            default=None,
            help="filter: zcode | prime | opencode pool rows",
        )

    def _search_kwargs(args: argparse.Namespace) -> dict:
        return {"kind": args.kind}

    return rbc.run_query_cli(
        argv,
        rbc.QueryCliSpec(
            description="Query the harness-memory index (memory_search sidecar).",
            default_db=lambda: rms.MEMORY_DB,
            db_flag_help="sidecar path (default: module MEMORY_DB)",
            connect_fn=rms.connect_memory_db,
            ready_fn=rms.memory_index_ready,
            not_built_msg=(
                "memory_search index not built. Run:\n"
                "  python3 helpers/maintenance/rebuild_memory_search.py"
            ),
            stale_fn=rms.memory_index_stale,
            stale_warning=(
                "WARNING: a harness memory pool changed since the last index — "
                "results may be outdated. Refresh: "
                "python3 helpers/maintenance/rebuild_memory_search.py"
            ),
            search_fn=rms.search_memories,
            render_hits=_render_memory_hits,
            extra_args=_extra_args,
            search_kwargs=_search_kwargs,
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
