#!/usr/bin/env python3
"""
VSS query-side core: the embedder picker + cosine matchers over
``company_embeddings`` (single importable home, derive_render_shared_note_grouping S3).

One home for the python query core that used to live (copied) inside
``helpers/core/get_tickers.py``: the per-call tier (sha1-keyed decode cache
+ python zip-sum cosine) and the run-index tier (float64 matvec built once
per run, end-of-run COUNT/MAX(rowid) tripwire). ``get_tickers`` keeps its
public names as thin re-exports; ``triage_pending_quotes`` /
``triage_pending_relations`` / ``embed_eval`` keep using
``get_tickers.vss_match`` unchanged.

Non-goals (from the proposal): query.py's DuckDB lane (different engine,
serves SQL graph joins; documented as the SQL-side consumer of the same
table) and ``company_neighbors_base_probe.py`` (bench stays self-contained).
"""

import ast
import hashlib
import sys
from pathlib import Path

# Ensure the repo root is importable when this module is imported from a
# subprocess that did not bootstrap sys.path itself.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import connect

# ---------------------------------------------------------------------------
# Embedder selection
# ---------------------------------------------------------------------------


def _pick_embedder(rows, embed_fn):
    """Resolve the embedder for a query vector. Returns (embed_fn, dims) or
    (None, 0) when no local embedder can reconstruct the query vector."""
    first_emb = ast.literal_eval(rows[0][1])
    dims = len(first_emb)
    if dims < 1:
        return None, 0
    if embed_fn is not None:
        return embed_fn, dims
    model = rows[0][2] or ""
    if model.startswith("dry-run"):
        from helpers.graph.embeddings import _pseudo_embedding

        return _pseudo_embedding, dims
    # Local bge-small model (local_embeddings, 2026-08-20): query-side
    # embed_query against index rows embedded with embed_document. Warn on
    # unavailability — the table holds real vectors but we cannot embed the
    # query into the same space, so the stage yields no match rather than a
    # garbage-scored one.
    from helpers.core import local_embedder

    if model == local_embedder.MODEL_ID:
        if dims != local_embedder.DIM:
            return None, 0
        if not local_embedder.available():
            print(
                f"WARNING: company_embeddings model is {model!r} but the local "
                "embedder is unavailable — VSS match skipped "
                "(see helpers/core/local_embedder.py).",
                file=sys.stderr,
            )
            return None, 0
        return (lambda q, _d: local_embedder.embed_query(q)), dims
    # Real (API) model: cannot recompute the query vector without the
    # provider key from this CLI. Caller may inject embed_fn instead.
    return None, 0


# ---------------------------------------------------------------------------
# Per-call tier: sha1-keyed decode cache + python zip-sum cosine
# ---------------------------------------------------------------------------

# derive_insights_perf Slice 2 (2026-09-05): the company_embeddings table
# is re-decoded from its stored strings on EVERY vss_match call (one call
# per ticker query per run — literal_eval dominates the scan at ~83ms per
# 1500×384 vs 0.04ms for the dots). Decode once per table CONTENT and
# share across calls. Key is a sha1 over the raw embedding strings (not
# COUNT/rowid — those miss in-place rewrites, and tests reuse table
# shapes), so any mutation is a different key by construction. Dims
# filtering stays at scan time (identical skip semantics to the old
# per-row _candidate_vec). Bounded (evict-oldest) so long newsletter runs
# with rotating conns can't grow it.
_VSS_DECODE_CACHE: dict[str, list] = {}
_VSS_DECODE_CACHE_MAX = 8


def _decoded_vss_table(rows):
    """[(name, vec|None)] with one literal_eval pass per table content."""
    # usedforsecurity=False: content fingerprint for a cache key, not security.
    digest = hashlib.sha1(
        "|".join((r[1] or "") for r in rows).encode(), usedforsecurity=False
    ).hexdigest()
    hit = _VSS_DECODE_CACHE.get(digest)
    if hit is not None:
        return hit
    table = [(r[0], _raw_vec(r[1])) for r in rows]
    _VSS_DECODE_CACHE[digest] = table
    while len(_VSS_DECODE_CACHE) > _VSS_DECODE_CACHE_MAX:
        _VSS_DECODE_CACHE.pop(next(iter(_VSS_DECODE_CACHE)))
    return table


def _raw_vec(emb_str):
    """Parse a stored embedding string; None if unparsable (no dims check —
    the caller filters by dims, matching _candidate_vec skip semantics)."""
    try:
        return ast.literal_eval(emb_str)
    except ValueError, SyntaxError, TypeError:
        return None


def _best_vss_match(qvec, rows, dims, entity_set):
    """Scan stored embeddings, return (best_name, best_score) via cosine
    (both vectors L2-normalized, so cosine == dot product)."""
    table = _decoded_vss_table(rows)
    best_name, best_score = None, 0.0
    for name, vec in table:
        if entity_set is not None and name not in entity_set:
            continue
        if vec is None or len(vec) != dims:
            continue
        dot = sum(a * b for a, b in zip(qvec, vec))
        if dot > best_score:
            best_score, best_name = dot, name
    return best_name, best_score


# ---------------------------------------------------------------------------
# Run-scoped VSS index (scan_render_vss_microperf S3, 2026-09-06): the
# company_embeddings table is static within a Yahoo run (only writers are
# the embeddings.py maint commands — separate CLI runs, never concurrent;
# concurrent-writer audit in the proposal §2), so fetch + decode it ONCE
# per run instead of per VSS fire (14.6ms fetch of 9.2 MB + 24.2ms digest
# per call). The per-call path above stays as-is for embed_eval/tests;
# the digest's rewrite defense is bypassed only on this explicit path,
# guarded by a COUNT/MAX(rowid) tripwire re-checked at run end.
# ---------------------------------------------------------------------------


class _VssRunIndex:
    """One fetchall + one decode, stacked as a float64 matrix.

    float64 (not float32) so the matvec matches the python sum() loop it
    replaces (~1e-15; parity-pinned by test). ``fingerprint`` is
    (COUNT(*), MAX(rowid)) at build time for the end-of-run tripwire.
    """

    def __init__(self, names, matrix, embed_fn, dims, fingerprint, db_path):
        self.names = names
        self.matrix = matrix
        self.embed_fn = embed_fn
        self.dims = dims
        self.fingerprint = fingerprint
        self.db_path = db_path


def _index_fingerprint(conn):
    """(COUNT(*), MAX(rowid)) of company_embeddings; None when unreadable."""
    try:
        return conn.execute("SELECT COUNT(*), MAX(rowid) FROM company_embeddings").fetchone()
    except Exception:
        return None


def build_vss_run_index(db_path=None, embed_fn=None):
    """Fetch + decode company_embeddings once; None when unusable.

    None (→ per-call behavior) on: absent/empty table, no embedder for the
    stored model, unparsable rows only, or numpy missing (lazy import —
    CLI startup and the no-DuckDB standalone constraint are untouched).
    Dims filtering happens here, matching the per-call skip semantics; the
    entity-set filter stays per call (it varies by query) via a names mask.
    """
    try:
        import numpy as np
    except ImportError:
        return None
    conn = None
    owns = False
    try:
        # connect(None) resolves to memory/research.db (the default) —
        # stored as-is on the index so the tripwire re-check hits the
        # same database.
        conn = connect(db_path, row_factory=None)
        owns = True
        try:
            rows = conn.execute(
                "SELECT company_name, embedding, model FROM company_embeddings"
            ).fetchall()
        except Exception:
            return None
        if not rows:
            return None
        try:
            picked_fn, dims = _pick_embedder(rows, embed_fn)
        except Exception:
            return None
        if picked_fn is None:
            return None
        fingerprint = _index_fingerprint(conn)
        names, vecs = [], []
        for name, emb_str, _model in rows:
            vec = _raw_vec(emb_str)
            if vec is None or len(vec) != dims:
                continue
            names.append(name)
            vecs.append(vec)
        if not names:
            return None
        return _VssRunIndex(
            names, np.asarray(vecs, dtype=np.float64), picked_fn, dims, fingerprint, db_path
        )
    finally:
        if owns and conn is not None:
            conn.close()


def check_vss_run_index(index):
    """End-of-run tripwire: True when the table is unchanged since build.

    Warns loudly on mismatch (a maint writer committed mid-run — the
    run's VSS results served the pre-write snapshot) so the operator
    re-runs for fresh vectors. ~1ms: one aggregate query.
    """
    if index is None:
        return True
    conn = None
    try:
        conn = connect(index.db_path, row_factory=None)
        now = _index_fingerprint(conn)
    except Exception:
        return True
    finally:
        if conn is not None:
            conn.close()
    if now is None or tuple(now) != tuple(index.fingerprint):
        print(
            "WARNING: company_embeddings changed mid-run — VSS matches "
            "served the pre-write snapshot; re-run for fresh vectors.",
            file=sys.stderr,
        )
        return False
    return True


def _index_best_match(index, qvec, entity_set, threshold):
    """Argmax over the run-index matrix with per-call entity filtering.

    Same contract as _best_vss_match: (best_name, best_score) with
    strict->first tie behavior (argmax returns the first maximum, like
    the loop's strict >), no-match below threshold.
    """
    import numpy as np

    scores = index.matrix @ np.asarray(qvec, dtype=np.float64)
    if entity_set is not None:
        mask = np.array([n in entity_set for n in index.names])
        if not mask.any():
            return None, 0.0
        scores = np.where(mask, scores, -np.inf)
    best_i = int(np.argmax(scores))
    best_score = float(scores[best_i])
    if best_score >= threshold:
        return index.names[best_i], best_score
    return None, 0.0


def _vss_match_with_index(index, query, entity_set, threshold):
    """Run-index path of vss_match (S3): one matvec, no SELECT/digest/dots."""
    try:
        qvec = index.embed_fn(query, index.dims)
    except Exception:  # noqa: S110  # no-match, don't kill a whole run on one bad query
        return None, 0.0
    return _index_best_match(index, qvec, entity_set, threshold)
