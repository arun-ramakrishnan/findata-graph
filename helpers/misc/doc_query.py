#!/usr/bin/env python3
"""
Query the doc_search sidecar index from the command line.

The agent-facing surface of the doc-search proposal
(doc/improvements/archive/tooling/doc_search_embeddings.md): a future session
can ask "where did we write down X" and get ranked, deep-linkable hits
(path:line) without the Flask app running. Wraps the same query core as
GET /api/docs/search (helpers/maintenance/rebuild_doc_search.search_docs).

Usage:
    python3 helpers/misc/doc_query.py "how does the embed cache work"
    python3 helpers/misc/doc_query.py "rrf fusion" --limit 10
    python3 helpers/misc/doc_query.py "sidecar" --json     # machine output
    python3 helpers/misc/doc_query.py "cache" --bm25       # lexical leg only

A stale index still answers (with a stderr warning naming the refresh
command — slightly outdated knowledge beats none); a missing index is a
hard exit 1 with the build command.

Exit codes: 0 answered (possibly empty), 1 index missing, 2 usage error.
"""

import re
import sys
from pathlib import Path

# Repo root: helpers/misc/doc_query.py -> parents[2]. Must be on sys.path
# BEFORE the `from helpers.maintenance...` below so the script works as a
# subprocess the same way it works under pytest. (Mirrors the house bootstrap.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.maintenance import rebuild_doc_search as rds  # noqa: E402

_MARK = re.compile(r"</?mark>")
_WS = re.compile(r"\s+")


def _plain_snippet(snippet: str, cap: int = 240) -> str:
    """Strip <mark> tags + collapse whitespace for terminal output."""
    text = _WS.sub(" ", _MARK.sub("", snippet)).strip()
    return text[: cap - 1] + "…" if len(text) > cap else text


def _render_doc_hits(hits: list[dict]) -> None:
    for hit in hits:
        where = f"{hit['path']}:{hit['anchor']}" if hit["anchor"] else hit["path"]
        label = hit["section_title"] or "(preamble)"
        print(f"{where}  [{label}]  {hit['score']}")
        snip = _plain_snippet(hit["snippet"])
        if snip:
            print(f"    {snip}")


def main(argv: list[str] | None = None) -> int:
    from helpers.maintenance import rebuild_common as rbc

    return rbc.run_query_cli(
        argv,
        rbc.QueryCliSpec(
            description="Query the doc/ knowledge index (doc_search sidecar).",
            default_db=lambda: rds.DOC_DB,
            db_flag_help="sidecar path (default: module DOC_DB)",
            connect_fn=rds.connect_doc_db,
            ready_fn=rds.doc_index_ready,
            not_built_msg=(
                "doc_search index not built. Run:\n  python3 helpers/maintenance/rebuild_doc_search.py"
            ),
            stale_fn=rds.doc_index_stale,
            stale_warning=(
                "WARNING: doc/ changed since the last index — results may be "
                "outdated. Refresh: python3 helpers/maintenance/rebuild_doc_search.py"
            ),
            search_fn=rds.search_docs,
            render_hits=_render_doc_hits,
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
