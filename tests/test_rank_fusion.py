"""rank_fusion equivalence tests (algorithms_adoption_sweep S7).

The shared module must reproduce the FORMER per-site implementations
EXACTLY — each test embeds the old inline logic as the reference and
asserts identical results (the zero-behavior-change contract; live
frozen rankings live in bench_data/rrf_consolidation/pre/).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from helpers.core import rank_fusion as rf  # noqa: E402

K = 60


def test_generic_rrf_matches_note_query_reference():
    """Reference: the former note_query.rrf / convo_query._rrf body."""

    def legacy(*ranked):
        fused = {}
        for hits in ranked:
            for i, (key, _s) in enumerate(hits):
                fused[key] = fused.get(key, 0.0) + 1.0 / (K + i + 1)
        return fused

    a = [("x", 1.0), ("y", 0.9), ("z", 0.5)]
    b = [("y", 1.0), ("x", 0.8), ("w", 0.3)]
    c = [("z", 1.0), ("z", 0.7)]  # duplicate key in one leg
    assert rf.rrf(a, b, c) == legacy(a, b, c)
    assert rf.rrf(a, b, c)["y"] == 1 / 62 + 1 / 61
    assert rf.rrf(a)["x"] == 1 / 61


def test_anchored_fusion_matches_convo_reference():
    """Reference: the former convo_query._anchored_fusion body."""

    def legacy(fts, cos):
        fused = {}
        for hits in (fts, cos):
            for i, (key, _s) in enumerate(hits):
                fused[key] = fused.get(key, 0.0) + 1.0 / (K + i + 1)
        order = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
        seen = set()
        out = []
        for key, _s in fts[:5]:
            if key in fused and key not in seen:
                out.append((key, fused[key]))
                seen.add(key)
        for key, s in order:
            if key not in seen:
                out.append((key, s))
        return out

    # the #442 shape: cosine's #1 is bm25's #6 — anchored keeps the FTS top-5 first
    fts = [(f"f{i}", 1.0 - i / 10) for i in range(6)]
    cos = [("f5", 0.95), ("f9", 0.9)] + [(f"f{i}", 0.5 - i / 20) for i in range(5)]
    assert rf.anchored_fusion(fts, cos) == legacy(fts, cos)
    assert [k for k, _ in rf.anchored_fusion(fts, cos)][:5] == [f"f{i}" for i in range(5)]
    assert [k for k, _ in rf.anchored_fusion(fts, cos)][5] == "f5"  # vector adds after


def test_two_leg_matches_doc_search_reference():
    """Reference: the former rebuild_doc_search inline block."""

    def legacy(candidates, cos_rank, worst):
        fused = []
        for bm25_pos, row, snippet in candidates:
            if cos_rank is not None:
                rrf = (1.0 / (K + bm25_pos + 1)) + (
                    1.0 / (K + cos_rank.get(row[0], worst + bm25_pos) + 1)
                )
            else:
                rrf = 1.0 / (K + bm25_pos + 1)
            fused.append((rrf, row, snippet))
        fused.sort(key=lambda t: t[0], reverse=True)
        return fused

    candidates = [(0, (10, "a"), "sa"), (1, (20, "b"), "sb"), (2, (30, "c"), "sc")]
    cos_rank = {20: 0, 30: 1}  # row 10 missing → worst + bm25_pos penalty
    worst = 5
    assert rf.rrf_two_leg(candidates, cos_rank, worst) == legacy(candidates, cos_rank, worst)
    assert rf.rrf_two_leg(candidates, None) == legacy(candidates, None, 0)
    # missing-cos default: (10, "a") sits at worst+bm25_pos = 5+0
    scores = {row[0]: s for s, row, _ in rf.rrf_two_leg(candidates, cos_rank, worst)}
    assert scores[10] == 1 / 61 + 1 / (K + worst + 0 + 1)
    # tie ordering preserved (stable sort on equal scores → insertion order)
    tie = [(0, (1, "x"), ""), (1, (2, "y"), "")]
    both = rf.rrf_two_leg(tie, {1: 0, 2: 0})
    assert [row[1] for _, row, _ in both] == ["x", "y"]


def test_two_leg_extras_rule_matches_git_reference():
    """Reference: the former rebuild_git_search._fuse_scores body — an
    EXTRA (cosine-only row outside the BM25 page) fuses on its cosine
    term ALONE."""

    def legacy(candidates, cos_rank):
        worst = len(cos_rank) if cos_rank else 0
        fused = []
        for bm25_pos, row, snippet, is_extra in candidates:
            if cos_rank is None:
                rrf = 1.0 / (K + bm25_pos + 1)
            elif is_extra:
                rrf = 1.0 / (K + cos_rank.get(row[0], worst + bm25_pos) + 1)
            else:
                rrf = (1.0 / (K + bm25_pos + 1)) + (
                    1.0 / (K + cos_rank.get(row[0], worst + bm25_pos) + 1)
                )
            fused.append((rrf, row, snippet))
        fused.sort(key=lambda t: t[0], reverse=True)
        return fused

    candidates = [
        (0, (10, "page"), "p", False),
        (5, (20, "extra"), "e", True),
    ]
    cos_rank = {10: 0, 20: 1}
    got = rf.rrf_two_leg(candidates, cos_rank, len(cos_rank), extra_pred=lambda c: c[3])
    want = legacy(candidates, cos_rank)
    assert got == want
    # the extras rule's point: the extra's score is its cosine leg alone
    scores = {row[1]: s for s, row, _ in got}
    assert scores["extra"] == 1.0 / (K + 1 + 1)
    assert scores["page"] == 1.0 / (K + 0 + 1) + 1.0 / (K + 0 + 1)
