#!/usr/bin/env python3
"""convo_query — hybrid search over the harvested conversation corpus.

The conversation-history counterpart of doc_query / script_query /
gate_query: BM25 (SQLite FTS5 porter sidecar) fused with cosine
(granite 384-d vectors in ``memory/convo_search.duckdb``) by reciprocal
rank fusion, over the pointer index built by
``helpers/maintenance/rebuild_convo_search.py``. Hits carry the parquet
pointer (file:row) — the corpus stays the archive of record, the index
never stores full bodies. Trial-verified contract (convo_search
proposal §3.6): FTS ~1 ms, cosine sub-second linear at corpus scale,
lanes complementary.

Exit codes: 0 on answered (even empty), 1 when the index is missing or
stale beyond the served-answer doctrine's WARNING.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

REPO = _REPO_ROOT
DEFAULT_DB = REPO / "memory/convo_search.duckdb"
FTS_DB_NAME = "convo_search_fts.db"
HARNESSES = ["opencode", "prime-rlm", "zcode"]
RRF_K = 60
HEAD = 160

# Rank prior. Tool traffic is ~30k of 53,423 indexed rows — but NOT under
# one part_type: assistant tool CALLS are part_type='tool' (17,049) while
# their OUTPUT arrives as role='toolResult', part_type='text' (12,622).
# Keying the prior on part_type alone therefore demotes nothing, which is
# exactly what the first live run showed. So both columns decide.
#
# It is a NUDGE, not a ban: a tool row is often the best answer for a query
# about code (a grep hit found by meaning), so it is demoted, not dropped.
# `--kinds` is the hard lever.
TOOL_ROLES = ("tool", "toolResult")
TOOL_PRIOR = 0.45
PART_PRIOR: dict[str, float] = {
    "text": 1.0,
    "reasoning": 1.0,
    "req-msg": 0.6,
    "step-finish": 0.3,
}
DEFAULT_PRIOR = 0.6
# `--kinds` accepts part_types AND roles; a row is kept when EITHER matches
KINDS = sorted({"text", "reasoning", "req-msg", "step-finish", "user", "assistant", *TOOL_ROLES})


def _prior_for(role: str, part_type: str) -> float:
    if part_type == "tool" or (role or "") in TOOL_ROLES:
        return TOOL_PRIOR
    return PART_PRIOR.get(part_type, DEFAULT_PRIOR)


# (harness, part_id) — part ids are only unique WITHIN a harness namespace.
Key = tuple[str, str]


@dataclass
class ConvoConnections:
    """The two-index handle: DuckDB pointers/vectors + FTS5 sidecar."""

    ddb: duckdb.DuckDBPyConnection
    sconn: sqlite3.Connection

    def close(self) -> None:
        for c in (self.ddb, self.sconn):
            try:
                c.close()
            except Exception:  # noqa: S110  # close is best-effort
                pass


def connect(db_path: Path) -> ConvoConnections:
    # S2+S3 (duckdb_transient_lock_retry): queue on <db>.io.lock while a
    # long writer (rebuild_convo_search, ~2 min) holds LOCK_EX, retry
    # short foreign holders on the ladder; the flock releases at open —
    # DuckDB's own per-file lock protects the connection from then on.
    from helpers.misc.duckdb_lock import open_read_only

    con = open_read_only(db_path)
    sconn = sqlite3.connect(  # noqa: S608  # FTS sidecar is a plain sqlite file, read-only URI
        f"file:{db_path.parent / FTS_DB_NAME}?mode=ro", uri=True
    )
    return ConvoConnections(con, sconn)


def _stored_dims(con: ConvoConnections) -> int | None:
    try:
        row = con.ddb.execute("SELECT value FROM convo_meta WHERE key = 'embed_dims'").fetchone()
    except duckdb.CatalogException:
        return None
    return int(row[0]) if row else None


def _stored_model(con: ConvoConnections) -> str | None:
    """Index-side model label from convo_meta, or None when the sidecar
    predates the stamp (dims-only back-compat, not a reject)."""
    try:
        row = con.ddb.execute("SELECT value FROM convo_meta WHERE key = 'embed_model'").fetchone()
    except duckdb.CatalogException:
        return None
    return row[0] if row else None


def _fts_hits(con: ConvoConnections, query: str, limit: int) -> list[tuple[Key, float]]:
    """Token-quoted FTS5 bm25 (lower-is-better rank → negate to score)."""
    q = " ".join(f'"{w.replace(chr(34), chr(34) * 2)}"' for w in query.split())
    rows = con.sconn.execute(
        "SELECT harness, part_id, rank FROM convo_fts WHERE convo_fts MATCH ? "
        "ORDER BY rank LIMIT ?",
        (q, limit),
    ).fetchall()
    return [((harness, pid), -float(rank)) for harness, pid, rank in rows]


def _cos_hits(
    con: ConvoConnections, query: str, limit: int, dims: int, query_vec=None
) -> list[tuple[Key, float]]:
    from helpers.core import local_embedder as le
    from helpers.maintenance import rebuild_common as rbc

    qv: list[float] | None = None
    if query_vec is not None:
        qv = rbc.check_query_vector(_stored_model(con), dims, query_vec)
    if qv is None:
        if le.available():
            qv = le.embed_query(query)
        else:
            from helpers.graph.embeddings import _pseudo_embedding

            qv = _pseudo_embedding(query, dims)
    if len(qv) != dims:
        return []
    # cast width MUST match the stored vectors (384 for the house embedder,
    # _PSEUDO_DIMS for the degraded path) — a hardcoded 384 breaks pseudo mode.
    cast = f"FLOAT[{int(dims)}]"
    rows = con.ddb.execute(
        "SELECT harness, part_id, array_cosine_similarity("  # noqa: S608  # cast width is int(dims) from the stored index; vector + limit are bound
        f"embedding::{cast}, ?::{cast}) s "
        "FROM convo_search WHERE embedding IS NOT NULL "
        "ORDER BY s DESC LIMIT ?",
        (list(qv), limit),
    ).fetchall()
    return [((harness, pid), float(s)) for harness, pid, s in rows]


def _rrf(*ranked: list[tuple[Key, float]]) -> dict[Key, float]:
    fused: dict[Key, float] = {}
    for hits in ranked:
        for i, (key, _score) in enumerate(hits):
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + i + 1)
    return fused


def _fetch_rows(con: ConvoConnections, keys: list[Key]) -> dict[Key, dict]:
    if not keys:
        return {}
    where = " OR ".join(["(harness = ? AND part_id = ?)"] * len(keys))
    params = [v for k in keys for v in k]
    rows = con.ddb.execute(
        "SELECT harness, session_id, part_id, file_path, row_no, ts, role, "  # noqa: S608  # where is a fixed OR-pattern of bound placeholders; all values parameterized
        "agent, model, part_type, text_len, snippet FROM convo_search "
        f"WHERE {where}",
        params,
    ).fetchall()
    names = (
        "harness",
        "session_id",
        "part_id",
        "file_path",
        "row_no",
        "ts",
        "role",
        "agent",
        "model",
        "part_type",
        "text_len",
        "snippet",
    )
    return {(r[0], r[2]): dict(zip(names, r)) for r in rows}


def _kinds_of(con: ConvoConnections, keys: list[Key]) -> dict[Key, tuple[str, str]]:
    """(role, part_type) per candidate — one OR-ed query for the whole pool.

    The FTS sidecar stores no part_type, so the type comes from here.
    """
    if not keys:
        return {}
    where = " OR ".join(["(harness = ? AND part_id = ?)"] * len(keys))
    rows = con.ddb.execute(
        f"SELECT harness, part_id, role, part_type FROM convo_search WHERE {where}",  # noqa: S608
        [v for k in keys for v in k],
    ).fetchall()
    return {(h, p): (role or "", t or "") for h, p, role, t in rows}


def _apply_prior(
    con: ConvoConnections, ranked: list[tuple[Key, float]], kinds: list[str] | None
) -> list[tuple[Key, float]]:
    """Re-weight the fused ranking by row kind, then hard-filter if asked."""
    if not ranked:
        return []
    kinds_of = _kinds_of(con, [k for k, _ in ranked])
    allowed = set(kinds) if kinds else None
    out = []
    for key, score in ranked:
        role, ptype = kinds_of.get(key, ("", ""))
        if allowed is not None and ptype not in allowed and role not in allowed:
            continue
        out.append((key, score * _prior_for(role, ptype)))
    return sorted(out, key=lambda kv: (-kv[1], kv[0]))


def search(
    con: ConvoConnections,
    query: str,
    limit: int = 5,
    hybrid: bool = True,
    harness: str | None = None,
    kinds: list[str] | None = None,
    query_vec=None,
) -> dict:
    """Hybrid (default) or bm25-only search; returns the result dicts.

    ``kinds`` restricts hits by part_type (e.g. ``["text", "reasoning"]``
    for pure conversation). When it is set the candidate pool is
    over-fetched, otherwise a tool-heavy result set would starve the
    filter. ``query_vec`` (shared_query_vector): a parent-fanned-out
    embedding used instead of a local model load when its stamp matches;
    on mismatch the leg embeds locally as before.
    """
    dims = _stored_dims(con)
    pool = max(limit * 4, 20) * (3 if kinds else 1)
    fts = _fts_hits(con, query, pool)
    if not hybrid or not dims:
        ranked = fts
        mode = "bm25"
    else:
        cos = _cos_hits(con, query, pool, dims, query_vec)
        ranked = sorted(_rrf(fts, cos).items(), key=lambda kv: (-kv[1], kv[0]))
        mode = "hybrid"
    ranked = _apply_prior(con, ranked, kinds)
    if harness:
        by_key = _fetch_rows(con, [k for k, _ in ranked])
        ranked = [(k, s) for k, s in ranked if by_key.get(k, {}).get("harness") == harness]
    top = ranked[:limit]
    by_key = _fetch_rows(con, [k for k, _ in top])
    results = []
    for rank, (key, score) in enumerate(top, 1):
        r = by_key.get(key)
        if not r:
            continue
        results.append(
            {
                "rank": rank,
                "score": round(score, 4),
                "mode": mode,
                "harness": r["harness"],
                "session_id": r["session_id"],
                "ts": r["ts"].isoformat(sep=" ") if r["ts"] else "",
                "role": r["role"],
                "part_type": r["part_type"],
                "text_len": r["text_len"],
                "head": r["snippet"][:HEAD],
                "pointer": f"{r['file_path']}:{r['row_no']}",
            }
        )
    return {"mode": mode, "results": results}


def _stale_warning(con: ConvoConnections) -> str | None:
    try:
        row = con.ddb.execute("SELECT COUNT(*) FROM convo_search").fetchone()
        if row and row[0]:
            return None
    except duckdb.CatalogException:
        pass
    return "convo_search index missing/empty — build it: make convo-fresh APPLY=1"


def expand(pointer: str, max_chars: int = 4000) -> dict:
    """Resolve a ``file.parquet:<row_no>`` pointer to the full corpus row.

    The index stores only an 8 KiB snippet; the body never enters the
    db. This is the read path the TUI preview uses.
    """
    rel, _, row = pointer.rpartition(":")
    if not rel or not row.isdigit():
        raise ValueError(f"not a corpus pointer: {pointer!r}")
    p = Path(rel)
    if not p.is_absolute():
        p = REPO / p
    if not p.exists():
        raise FileNotFoundError(f"corpus file gone: {p}")
    import pyarrow.parquet as pq

    n = int(row)
    tbl = pq.read_table(p)
    if not 0 <= n < tbl.num_rows:
        raise IndexError(f"row {n} out of range for {p.name} ({tbl.num_rows} rows)")
    rec = tbl.slice(n, 1).to_pylist()[0]
    text = rec.get("text") or ""
    # the corpus carries no harness column — the segment before
    # `conversations/` is the namespace (same rule as the index builder)
    parts = p.parts
    harness = parts[parts.index("conversations") - 1] if "conversations" in parts else "unknown"
    return {
        **rec,
        "harness": harness,
        # the corpus carries no `text_len` column (that lives only in the
        # DuckDB index) — derive it here so both call sites can rely on it
        "text_len": len(text),
        "text": text[:max_chars],
        "truncated": len(text) > max_chars,
        "pointer": pointer,
    }


def render_hits(results: list[dict]) -> None:
    for r in results:
        print(
            f"{r['rank']:>2}. [{r['score']:.3f} {r['mode']}] "
            f"{r['harness']}/{r['part_type']}/{r['role'] or '-'} "
            f"{r['ts'][:16]} len={r['text_len']}"
        )
        print(f"    {r['head'].replace(chr(10), ' ')}")
        print(f"    {r['pointer']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("query", help="free-text query; punctuation is safe")
    p.add_argument("--limit", type=int, default=5, help="max hits (default 5)")
    p.add_argument("--db", default=str(DEFAULT_DB), help="convo_search duckdb path")
    p.add_argument("--bm25", action="store_true", help="lexical leg only (skip cosine)")
    p.add_argument(
        "--query-vector",
        default=None,
        metavar="JSON",
        help="shared query embedding (shared_query_vector: parent fanned-out "
        '{"model","dims","vector"} — skips this leg\'s own model load; '
        "absent/garbled = embed locally as before)",
    )
    p.add_argument("--harness", default=None, choices=HARNESSES, help="filter hits to one harness")
    p.add_argument(
        "--kinds",
        default=None,
        help=f"comma-separated part_types to keep, e.g. text,reasoning (all: {','.join(KINDS)})",
    )
    p.add_argument(
        "--json", action="store_true", dest="as_json", help="emit the raw result dicts as JSON"
    )
    p.add_argument(
        "--expand",
        default=None,
        metavar="POINTER",
        help="print the full body of a file.parquet:<row> pointer",
    )
    args = p.parse_args(argv)

    if args.expand:
        rec = expand(args.expand)
        if args.as_json:
            print(json.dumps(rec, indent=2, default=str))
        else:
            head = (
                f"{rec['harness']}/{rec['part_type']}/{rec['role'] or '-'} "
                f"{rec['ts']} session={rec['session_id']} ({rec['text_len']} chars)"
            )
            print(head, file=sys.stderr)
            print(rec["text"] + ("\n[truncated]" if rec["truncated"] else ""))
        return 0

    con = connect(Path(args.db))
    try:
        warning = _stale_warning(con)
        if warning:
            print(warning, file=sys.stderr)
            return 1
        kinds = [k.strip() for k in args.kinds.split(",") if k.strip()] if args.kinds else None
        if kinds:
            unknown = [k for k in kinds if k not in KINDS]
            if unknown:
                p.error(f"unknown part_type(s) {unknown}; known: {','.join(KINDS)}")
        from helpers.maintenance import rebuild_common as rbc

        out = search(
            con,
            args.query,
            limit=max(1, min(args.limit, 50)),
            hybrid=not args.bm25,
            harness=args.harness,
            kinds=kinds,
            query_vec=rbc.parse_query_vector_flag(args.query_vector),
        )
    finally:
        con.close()

    if args.as_json:
        print(json.dumps({"mode": out["mode"], "results": out["results"]}, indent=2))
        return 0
    if not out["results"]:
        print(f"(no hits for {args.query!r}; mode={out['mode']})", file=sys.stderr)
        return 0
    print(f"# {len(out['results'])} hit(s), mode={out['mode']}", file=sys.stderr)
    render_hits(out["results"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
