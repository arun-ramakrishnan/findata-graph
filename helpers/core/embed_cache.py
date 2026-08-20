#!/usr/bin/env python3
"""Shared content-hash embedding cache for both embed indexers.

Q3 of the local_embeddings proposal (2026-08-20): with a real embedding
model, a FULL refresh costs minutes of CPU, busting the maint budget. Remedy:
a ``(sha256(text), model)`` -> vector cache so unchanged text never re-embeds.
It lives in the vec SIDECAR (``<db>_vec.db``, schema ``vecdb``) — derived,
snapshot-excluded, lazily rebuilt state, exactly like the vec0 mirror; a new
table in research.db would collide with the schema-drift guards and DuckDB
scanner expectations.

One cache serves BOTH text populations — the note_search FTS rebuild
(``helpers/maintenance/rebuild_note_search.py``, per-doc wrapper) and the
company-embeddings populate (``helpers/graph/embeddings.py``, batch wrapper).
Company texts are just another text population; the key is content + model
label, never the population. A model swap re-embeds everything (label is
part of the key), which is exactly the required semantics: vectors from
different models must never be served for each other's spaces.

Everything here is best-effort: when the sidecar can't be attached the
callers degrade to uncached embedding (correct, just slower).
"""

import hashlib
import json
from collections.abc import Callable

EMBED_CACHE_TABLE = "vecdb.note_search_emb_cache"
EMBED_CACHE_DDL = (
    "CREATE TABLE IF NOT EXISTS vecdb.note_search_emb_cache ("
    " text_hash TEXT NOT NULL,"
    " model     TEXT NOT NULL,"
    " embedding TEXT NOT NULL,"
    " PRIMARY KEY (text_hash, model)"
    ")"
)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


class CachedEmbed:
    """Per-text wrapper around a resolved embedder (note_search rebuild).

    Counts hits/misses/dirty for the stats report. Best-effort: if the
    sidecar can't be attached the wrapper degrades to the raw embed_fn.
    """

    def __init__(self, embed_fn: Callable[[str], list[float]], model_label: str, conn):
        self._fn = embed_fn
        self._model = model_label
        self._conn = conn
        self.hits = 0
        self.misses = 0
        self.dirty = 0
        self._ok = self._try_init()

    def _try_init(self) -> bool:
        try:
            from helpers.core.vec_search import _attach_vec_db

            _attach_vec_db(self._conn)
            self._conn.execute(EMBED_CACHE_DDL)
            return True
        except Exception:  # noqa: S110  # no sidecar -> embed uncached
            return False

    def __call__(self, text: str) -> list[float]:
        h = None
        if self._ok:
            h = _hash(text)
            try:
                row = self._conn.execute(
                    f"SELECT embedding FROM {EMBED_CACHE_TABLE} "  # noqa: S608  # constant table name
                    "WHERE text_hash = ? AND model = ?",
                    (h, self._model),
                ).fetchone()
                if row:
                    self.hits += 1
                    return json.loads(row[0])
            except Exception:  # noqa: S110  # cache read fails -> embed
                pass
        vec = self._fn(text)
        self.misses += 1
        if self._ok and h is not None:
            try:
                self._conn.execute(
                    f"INSERT OR REPLACE INTO {EMBED_CACHE_TABLE} "  # noqa: S608  # constant table name
                    "(text_hash, model, embedding) VALUES (?, ?, ?)",
                    (h, self._model, json.dumps(vec)),
                )
                self.dirty += 1
            except Exception:  # noqa: S110  # cache write fails -> fine
                pass
        return vec


def cached_embed_batch(
    conn,
    texts: list[str],
    model_label: str,
    embed_missing: Callable[[list[str]], list[list[float]]],
) -> tuple[list[list[float]], dict]:
    """Cache-aware BATCH embed (company-embeddings populate).

    Hits are served from the sidecar cache; only the misses go through ONE
    ``embed_missing`` call (the batch embedder — a single llama.cpp call for
    the whole corpus), and those vectors are stored back into the cache.
    Returns ``(vectors_in_input_order, {"hits", "misses", "dirty"})``.

    Cache rows are committed here, not by the caller's later transaction:
    pre-warm flows (e.g. a count-only run) must persist them (the
    rebuild_note_search --check lesson). The cache is content-addressed, so
    committing early is safe even if the caller's write later fails.
    """
    stats = {"hits": 0, "misses": 0, "dirty": 0}
    try:
        from helpers.core.vec_search import _attach_vec_db

        _attach_vec_db(conn)
        conn.execute(EMBED_CACHE_DDL)
    except Exception:  # noqa: S110  # no sidecar -> embed uncached
        vecs = embed_missing(texts)
        stats["misses"] = len(texts)
        return vecs, stats

    # One bulk load of this model's cache slice (a few thousand rows at
    # most — note_search + company texts) beats N point SELECTs.
    cached: dict[str, str] = {
        row[0]: row[1]
        for row in conn.execute(
            f"SELECT text_hash, embedding FROM {EMBED_CACHE_TABLE} "  # noqa: S608  # constant table name
            "WHERE model = ?",
            (model_label,),
        )
    }

    hashes = [_hash(t) for t in texts]
    vecs: list[list[float] | None] = []
    for h in hashes:
        raw = cached.get(h)
        try:
            vecs.append(json.loads(raw) if raw else None)
        except ValueError:  # corrupted cache row -> treat as a miss
            vecs.append(None)
    stats["hits"] = sum(v is not None for v in vecs)

    miss_idx = [i for i, v in enumerate(vecs) if v is None]
    stats["misses"] = len(miss_idx)
    if miss_idx:
        new_vecs = embed_missing([texts[i] for i in miss_idx])
        if len(new_vecs) != len(miss_idx):
            # A short/long reply would silently shift vectors onto the wrong
            # companies below — fail loudly instead.
            raise ValueError(
                f"batch embedder returned {len(new_vecs)} vectors "
                f"for {len(miss_idx)} texts"
            )
        for i, vec in zip(miss_idx, new_vecs):
            vecs[i] = vec
        try:
            conn.executemany(
                f"INSERT OR REPLACE INTO {EMBED_CACHE_TABLE} "  # noqa: S608  # constant table name
                "(text_hash, model, embedding) VALUES (?, ?, ?)",
                [(hashes[i], model_label, json.dumps(v)) for i, v in zip(miss_idx, new_vecs)],
            )
            conn.commit()  # persist NOW (pre-warm lesson; see docstring)
            stats["dirty"] = len(miss_idx)
        except Exception:  # noqa: S110  # cache write fails -> vectors still returned
            pass

    # All slots are filled by here (hits from cache, misses embedded).
    return [v for v in vecs if v is not None], stats
