#!/usr/bin/env python3
"""note_query — hybrid search over the `findata/**` vault notes.

Notes are the one index that had no CLI: the query legs lived inside
`search_tui.py` as TUI-lane adapters, which makes them unreachable from
a shell — useless for an agent session and for scripting. This module is
the canonical query surface; the TUI lane delegates here (TUI = the
interactive front door, not the implementation).

Two legs over the same rows in `memory/research.db`:

- **bm25**: FTS5 `note_search` (porter tokens), `bm25(note_search)`
  ascending. Lexical precision — exact terms, tickers, metric names.
- **cosine**: the note semantic matrix (`memory/embed_matrix.f32` via
  `EmbedMatrixStore`) searched with `top_k`. Semantic recall — a query
  phrased differently from the note still lands.

Fused with RRF, keyed on ``(file_path, anchor)`` because one note has
many sections. Hits carry ``file_path:line`` so a caller can Read the
exact section — notes are real markdown, so the pointer is a text
locator, unlike convo_search's parquet rows.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from functools import lru_cache
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

REPO = _REPO
DEFAULT_DB = REPO / "memory/research.db"
RRF_K = 60
HEAD = 400
SEMANTIC_CANDIDATES = 200


Key = tuple[str, str]


def fts_safe(query: str) -> str:
    """Make a raw query safe for an FTS5 MATCH, degrading to OR-tokens.

    Bare hyphens and other punctuation parse as FTS5 column-filter syntax
    (``no such column: dup``); quoted tokens are the reliable form.
    """
    if re.fullmatch(r"[\w']+(?:\s+[\w']+)*", query.strip(), re.UNICODE):
        return query
    from helpers.core.db import connect as _db_connect

    try:
        probe = _db_connect(":memory:", wal=False)
        probe.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        probe.execute("SELECT * FROM t WHERE t MATCH ?", (query,))
        probe.close()
        return query
    except sqlite3.OperationalError:
        pass
    tokens = [t for t in re.findall(r"[\w']+", query) if t]
    if not tokens:
        return '""'
    return " OR ".join(f'"{t}"' for t in tokens)


def note_snippet(content: str, query: str) -> str:
    """A 32-word window of `content` centred on the first query token.

    Word-window rather than character offsets: note sections are prose,
    and matching on token-in-word (``revenue`` in ``revenues``) beats
    exact substring hits on the raw text.
    """
    tokens = re.findall(r"[\w']+", query.casefold())
    if not tokens or not content:
        return ""
    words = content.split()
    lowered = [w.casefold() for w in words]
    position = next(
        (i for token in tokens for i, word in enumerate(lowered) if token in word),
        0,
    )
    start = max(0, position - 8)
    return " ".join(words[start : start + 32])


def bm25_hits(conn, query: str, limit: int) -> list[dict]:
    """Token-safe FTS5 bm25 (lower-is-better rank → negate to score).

    Returns full dicts — the bm25 leg owns every prose field, so the
    fusion has something to show even when the semantic leg adds a hit.
    """
    rows = conn.execute(
        "SELECT file_path, anchor, title, section_title, sector, doc_type, "
        "substr(content, 1, 4000) AS content, bm25(note_search) "
        "FROM note_search WHERE note_search MATCH ? "
        "ORDER BY bm25(note_search) LIMIT ?",
        (fts_safe(query), limit),
    ).fetchall()
    return [
        {
            "path": str(r[0]).removeprefix("/"),
            "line": _anchor_line(r[1]),
            "title": r[2] or "",
            "section": r[3] or "",
            "sector": r[4] or "",
            "doc_type": r[5] or "",
            "head": note_snippet(r[6] or "", query),
            "score": -float(r[7] or 0.0),
        }
        for r in rows
    ]


@lru_cache(maxsize=4)
def load_notes_matrix(db_path: str):
    """Staleness-gated note semantic matrix (cached; 25 MB, do not reload).

    Raises RuntimeError with an operator-actionable message when the
    matrix no longer matches the index — the caller degrades to bm25
    rather than searching a stale space.
    """
    import numpy as np  # noqa: F401  (import guard: numpy missing = bm25 only)

    from helpers.core.db import connect as db_connect
    from helpers.core.embed_matrix import EmbedMatrixStore
    from helpers.maintenance.rebuild_note_search import stored_embed_dims

    conn = db_connect(Path(db_path), read_only=True, wal=False)
    try:
        row_count = conn.execute("SELECT COUNT(*) FROM note_search").fetchone()[0]
        dims = stored_embed_dims(conn)
    finally:
        conn.close()
    matrix = EmbedMatrixStore().load()
    if int(matrix.meta["count"]) != row_count:
        raise RuntimeError("matrix stale — run rebuild-note-search")
    if dims is not None and int(dims) != int(matrix.meta["dims"]):
        raise RuntimeError("matrix dimensions stale — run rebuild-note-search")
    return matrix


@lru_cache(maxsize=1)
def note_query_embedder():
    """Cached query embedder for the note semantic leg."""
    from helpers.maintenance.rebuild_note_search import query_embedder

    return query_embedder()


def _stored_note_model(db_path: Path) -> str | None:
    """db_meta.note_embed_model — the real index-side model label (the
    matrix meta stamps a generic 'note_search'; the SQL home carries the
    model the rows were built with). None when unstamped (dims-only
    back-compat, not a reject)."""
    try:
        from helpers.core.db import connect as db_connect

        conn = db_connect(Path(db_path), read_only=True, wal=False)
        try:
            row = conn.execute(
                "SELECT value FROM db_meta WHERE key = 'note_embed_model'"
            ).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001  # missing table / locked db -> None
        return None
    return row[0] if row else None


def semantic_hits(db_path: Path, query: str, limit: int, query_vec=None) -> tuple[list[dict], str]:
    """Exact cosine top-k over the note matrix. Returns (hits, reason).

    Hits carry path/line/score only — the bm25 leg owns the prose fields.
    ``query_vec`` (shared_query_vector): a parent-fanned-out embedding
    used instead of a local model load when its stamp matches; on
    mismatch the leg embeds locally as before.
    """
    try:
        import numpy as np

        from helpers.maintenance import rebuild_common as rbc

        matrix = load_notes_matrix(str(db_path))
        vec: list[float] | None = None
        if query_vec is not None:
            vec = rbc.check_query_vector(
                _stored_note_model(db_path), int(matrix.meta["dims"]), query_vec
            )
        if vec is None:
            embed_query, _d = note_query_embedder()
            vec = embed_query(query)
        raw = matrix.top_k(np.asarray(vec), max(limit * 4, SEMANTIC_CANDIDATES))
        best: dict[Key, float] = {}
        for key, score in raw:
            path, _sep, anchor = str(key).partition("#")
            k = (path.removeprefix("/"), anchor)
            if float(score) > best.get(k, -1.0):
                best[k] = float(score)
        return [
            {"path": p, "line": _anchor_line(a), "score": s}
            for (p, a), s in sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        ], ""
    except RuntimeError as exc:
        return [], str(exc)
    except (OSError, ValueError, KeyError, TypeError, ImportError, AttributeError) as exc:
        return [], f"semantic notes unavailable: {type(exc).__name__}"


def rrf(*ranked: list[tuple[Key, float]]) -> dict[Key, float]:
    fused: dict[Key, float] = {}
    for hits in ranked:
        for i, (key, _s) in enumerate(hits):
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + i + 1)
    return fused


def _fuse_key(h: dict) -> Key:
    return (h["path"], str(h.get("line") or ""))


def _fuse_line_key(k: Key, per_note: bool) -> Key:
    return (k[0], "") if per_note else k


def _fuse_borrow_prose(rows: dict[Key, dict], base: dict, lk: Key, per_note: bool) -> dict:
    """per_note collapse may have kept a semantic key whose bm25 twin
    has a different anchor — borrow that row's prose if it exists."""
    if not per_note or base.get("head"):
        return base
    for k, h in rows.items():
        if _fuse_line_key(k, per_note) == lk and h.get("head"):
            return dict(h)
    return base


def fuse(bm25: list[dict], semantic: list[dict], limit: int, per_note: bool = False) -> list[dict]:
    """RRF the two legs. Keys are (path, anchor) — one note has many
    sections, so per-SECTION is the default.

    ``per_note=True`` collapses to the best-scoring section per note.
    That is what the TUI lane shows (one row per note, not eight
    sections of the same file), so the difference is a flag rather than
    two implementations.
    """
    rows: dict[Key, dict] = {}
    for h in semantic:
        rows[_fuse_key(h)] = h
    for h in bm25:
        rows[_fuse_key(h)] = h
    scores = rrf([(_fuse_key(h), 0.0) for h in bm25], [(_fuse_key(h), 0.0) for h in semantic])

    best: dict[Key, tuple[float, Key]] = {}
    for key, score in scores.items():
        lk = _fuse_line_key(key, per_note)
        if lk not in best or score > best[lk][0]:
            best[lk] = (score, key)
    ordered = sorted(best.items(), key=lambda kv: (-kv[1][0], kv[0]))
    out = []
    for _lk, (score, key) in ordered[:limit]:
        base = _fuse_borrow_prose(rows, dict(rows.get(key, {})), _lk, per_note)
        base["score"] = round(score, 6)
        out.append(base)
    return out


def search(
    db_path: Path, query: str, limit: int = 5, hybrid: bool = True, per_note: bool = False
) -> dict:
    """Hybrid (default) or bm25-only note search; returns hits + mode."""
    from helpers.core.db import connect as db_connect

    conn = db_connect(db_path, read_only=True, wal=False)
    try:
        pool = max(limit * 4, 20)
        bm = bm25_hits(conn, query, pool)
        reason = ""
        if not hybrid or not bm:
            ranked, mode = bm, "bm25"
        else:
            sem, reason = semantic_hits(db_path, query, pool)
            if not sem:
                ranked, mode = bm, "bm25"
            else:
                ranked, mode = fuse(bm, sem, pool, per_note=per_note), "hybrid"
        top = ranked[:limit]
        results = []
        for hit in top:
            row = conn.execute(
                "SELECT title, section_title, sector, doc_type FROM note_search "
                "WHERE file_path IN (?, ?) AND anchor = ? LIMIT 1",
                (f"/{hit['path'].lstrip('/')}", hit["path"], str(hit.get("line") or "")),
            ).fetchone()
            results.append(
                {
                    "path": hit["path"],
                    "line": hit.get("line"),
                    "title": hit.get("title") or (row[0] if row else "") or "",
                    "section": hit.get("section") or (row[1] if row else "") or "",
                    "sector": hit.get("sector") or (row[2] if row else "") or "",
                    "doc_type": hit.get("doc_type") or (row[3] if row else "") or "",
                    "head": hit.get("head") or "",
                    "score": hit.get("score"),
                }
            )
    finally:
        conn.close()
    out = {"mode": mode, "results": results}
    if reason:
        out["note"] = reason
    return out


def _anchor_line(anchor: str | None) -> int | None:
    try:
        return int(anchor) if anchor not in (None, "") else None
    except TypeError, ValueError:
        return None


def render(results: list[dict]) -> None:
    for i, r in enumerate(results, 1):
        loc = f":{r['line']}" if r.get("line") else ""
        score = r.get("score")
        head = f"[{score:.4f}] " if isinstance(score, (int, float)) else ""
        head += f"{r.get('sector') or '-'} · {r.get('doc_type') or '-'}  {r.get('title') or ''}"
        print(f"{i:>2}. {head}"[:160])
        if r.get("section"):
            print(f"    {r['section']}")
        if r.get("head"):
            print(f"    {r['head'][:HEAD]}")
        print(f"    {r['path']}{loc}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("query", help="free-text query; punctuation is safe")
    p.add_argument("--limit", type=int, default=5, help="max hits (default 5)")
    p.add_argument("--db", default=str(DEFAULT_DB), help="note_search sqlite path")
    p.add_argument("--bm25", action="store_true", help="lexical leg only (skip cosine)")
    p.add_argument(
        "--per-note",
        action="store_true",
        help="one hit per note (best section) instead of per section",
    )
    p.add_argument("--json", action="store_true", dest="as_json", help="raw result dicts")
    args = p.parse_args(argv)

    out = search(
        Path(args.db),
        args.query,
        limit=max(1, min(args.limit, 50)),
        hybrid=not args.bm25,
        per_note=args.per_note,
    )
    if out.get("note"):
        print(f"({out['note']})", file=sys.stderr)
    if args.as_json:
        print(json.dumps({"mode": out["mode"], "results": out["results"]}, indent=2))
        return 0
    if not out["results"]:
        print(f"(no hits for {args.query!r}; mode={out['mode']})", file=sys.stderr)
        return 0
    print(f"# {len(out['results'])} hit(s), mode={out['mode']}", file=sys.stderr)
    render(out["results"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
