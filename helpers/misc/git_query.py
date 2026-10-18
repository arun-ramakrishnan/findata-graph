#!/usr/bin/env python3
"""
Query the git_search sidecar index from the command line.

The agent-facing surface of the git-history proposal
(doc/improvements/proposals/git_history_search.md): "when did X land and
why", "which commit touched this symbol and what moved with it". Commits
carry the operator's WHY/WHAT write-ups (median ~1.6 KB) and the
noise-excluded diff motion; ripwire answers STRUCTURE, doc_query answers
CONCLUSIONS, convo_query answers the in-flight path — this leg answers
what LANDED.

Usage:
    python3 helpers/misc/git_query.py "bili namespace routing"
    python3 helpers/misc/git_query.py "_bili_namespace" --path harvest
    python3 helpers/misc/git_query.py "gate fixes" --since 30d
    python3 helpers/misc/git_query.py "textconv" --bm25 --json

A stale index still answers (stderr warning naming the refresh command);
a missing index is a hard exit 1 with the build command.

Exit codes: 0 answered (possibly empty), 1 index missing, 2 usage error.
"""

import argparse
import re
import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path

# Repo root: helpers/misc/git_query.py -> parents[2]. Must be on sys.path
# BEFORE the helpers.maintenance import below (house bootstrap).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.maintenance import rebuild_git_search as rgs  # noqa: E402

_MARK = re.compile(r"</?mark>")
_WS = re.compile(r"\s+")

# parse_range canonical unit -> seconds (search-filter granularity; month
# averaging is fine for a cutoff, calendar clamping is a retention-window
# concern, not this one).
_UNIT_SECONDS = {
    "d": 86400.0,
    "w": 604800.0,
    "h": 3600.0,
    "min": 60.0,
    "s": 1.0,
    "mo": 2629800.0,
    "y": 31557600.0,
}


def _since_cutoff(raw: str) -> str:
    """`14d`/`6mo` -> the ISO date cutoff the index filters on."""
    from helpers.core.ranges import RangeError, parse_range

    try:
        amount, unit = parse_range(raw)
    except RangeError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    cutoff = datetime.now(UTC) - timedelta(seconds=amount * _UNIT_SECONDS[unit])
    return cutoff.date().isoformat()


def _plain_snippet(snippet: str, cap: int = 240) -> str:
    """Strip <mark> tags + collapse whitespace for terminal output."""
    text = _WS.sub(" ", _MARK.sub("", snippet)).strip()
    return text[: cap - 1] + "…" if len(text) > cap else text


def _render_commit_hits(hits: list[dict]) -> None:
    for hit in hits:
        marker = " (body)" if hit.get("vector_from") == "body" else ""
        print(f"{hit['sha'][:10]}  {hit['date']}  [{hit['score']}]{marker}  {hit['subject']}")
        files = (hit.get("files") or "").splitlines()
        if files:
            head = " · ".join(files[:3])
            more = f"  (+{len(files) - 3} more)" if len(files) > 3 else ""
            print(f"    files: {head}{more}")
        snip = _plain_snippet(hit.get("snippet", ""))
        if snip:
            print(f"    {snip}")
        print(f"    git show {hit['sha']}")


def main(argv: list[str] | None = None) -> int:
    from helpers.maintenance import rebuild_common as rbc

    def _extra_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--since",
            type=_since_cutoff,
            default=None,
            metavar="RANGE",
            help="only commits newer than RANGE (14d / 6mo / 1y; bare number = days)",
        )
        p.add_argument(
            "--path",
            default=None,
            metavar="SUBSTR",
            help="only commits whose touched paths contain SUBSTR (LIKE-escaped)",
        )

    def _search_kwargs(args: argparse.Namespace) -> dict:
        return {"since": args.since, "path": args.path}

    return rbc.run_query_cli(
        argv,
        rbc.QueryCliSpec(
            description="Query the git history index (git_search sidecar).",
            default_db=lambda: rgs.GIT_SEARCH_DB,
            db_flag_help="sidecar path (default: module GIT_SEARCH_DB)",
            connect_fn=rgs.connect_git_db,
            ready_fn=rgs.git_index_ready,
            not_built_msg=(
                "git_search index not built. Run:\n"
                "  python3 helpers/maintenance/rebuild_git_search.py"
            ),
            stale_fn=rgs.git_index_stale,
            stale_warning=(
                "WARNING: main has commits the index has not seen — results may "
                "be outdated. Refresh: python3 helpers/maintenance/rebuild_git_search.py"
            ),
            search_fn=rgs.search_commits,
            render_hits=_render_commit_hits,
            extra_args=_extra_args,
            search_kwargs=_search_kwargs,
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
