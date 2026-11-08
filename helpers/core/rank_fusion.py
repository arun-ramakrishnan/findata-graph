#!/usr/bin/env python3
"""Reciprocal-rank fusion — the ONE home for the RRF constant and formulas.

Consolidates the formerly-duplicated implementations (algorithms_adoption_sweep
S7): note_query.rrf, convo_query._rrf + _anchored_fusion, and the inline
two-leg blocks in rebuild_doc_search / rebuild_memory_search /
rebuild_script_search / rebuild_git_search (the last adds the extras rule).
Zero-behavior-change contract: pre-refactor rankings frozen under
``bench_data/rrf_consolidation/pre/`` — post-refactor re-runs must rank
identically (tests/test_rank_fusion.py + the bank captures).

Forms:

- :func:`rrf` — generic N-leg symmetric fusion, ``1/(k + rank + 1)`` summed
  per key. The search-CLI fusion (note/convo).
- :func:`anchored_fusion` — the convo named variant (frozen-bank verdict
  2026-10-27, #442 sweep): symmetric RRF alone measures 8-10/57 vs the
  bm25-alone floor 22/57 — the cosine leg drags exact-match hits out of
  the top-5. Anchoring the FTS top-``anchors`` in production order
  preserves exact-match recall by construction; the fused order fills the
  rest, so the vector leg can only add. Convo-only by design — NOT a
  shared default.
- :func:`rrf_two_leg` — the index-CLI form over ``(bm25_pos, row, snippet)``
  candidates: bm25 leg + cosine-position leg (``row[0]`` keyed), missing
  cosine rows take ``worst + bm25_pos`` as the position (a penalty default,
  frozen semantics), ``cos_rank=None`` degrades to the bm25 leg alone.
  ``extra_pred`` carries git_search's extras rule: a candidate outside the
  BM25 page fuses on its cosine term ALONE — a synthetic bm25 leg would
  let generic chunks outrank real page evidence (S5 live-bank finding).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

RRF_K = 60


def rrf(*ranked: list[tuple[Any, float]], k: int = RRF_K) -> dict[Any, float]:
    """Generic N-leg symmetric RRF: score = Σ 1/(k + position + 1)."""
    fused: dict[Any, float] = {}
    for hits in ranked:
        for i, (key, _s) in enumerate(hits):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + i + 1)
    return fused


def anchored_fusion(
    fts: list[tuple[Any, float]],
    cos: list[tuple[Any, float]],
    anchors: int = 5,
    k: int = RRF_K,
) -> list[tuple[Any, float]]:
    """bm25-anchored fusion — the convo named variant (see module docstring)."""
    fused = rrf(fts, cos, k=k)
    order = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
    seen: set[Any] = set()
    out: list[tuple[Any, float]] = []
    for key, _s in fts[:anchors]:
        if key in fused and key not in seen:
            out.append((key, fused[key]))
            seen.add(key)
    for key, s in order:
        if key not in seen:
            out.append((key, s))
    return out


def rrf_two_leg(
    candidates: list[tuple],
    cos_rank: dict | None = None,
    worst: int = 0,
    k: int = RRF_K,
    extra_pred: Callable[[tuple], bool] | None = None,
) -> list[tuple[float, Any, Any]]:
    """Two-leg RRF over (bm25_pos, row, snippet) candidates — frozen
    semantics: missing cosine rows sit at ``worst + bm25_pos``; ``None``
    cos_rank = bm25 leg alone; ``extra_pred`` flags cosine-only extras
    (git_search) which fuse on the cosine leg alone."""
    fused = []
    for cand in candidates:
        bm25_pos, row, snippet = cand[0], cand[1], cand[2]
        if cos_rank is None:
            score = 1.0 / (k + bm25_pos + 1)
        elif extra_pred is not None and extra_pred(cand):
            score = 1.0 / (k + cos_rank.get(row[0], worst + bm25_pos) + 1)
        else:
            score = (1.0 / (k + bm25_pos + 1)) + (
                1.0 / (k + cos_rank.get(row[0], worst + bm25_pos) + 1)
            )
        fused.append((score, row, snippet))
    fused.sort(key=lambda t: t[0], reverse=True)
    return fused
