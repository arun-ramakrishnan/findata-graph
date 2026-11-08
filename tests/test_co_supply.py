"""Hermetic tests for the co-supply peer lane (co_supply_peer_lane S1).

Synthetic buyers only — no live store (house convention,
test_hyper_motifs.py style).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pytest  # noqa: E402

import helpers.graph.co_supply as cs  # noqa: E402

BUYERS = {
    "supply:Alpha": ["s1", "s2", "s3", "s4"],  # 4 suppliers
    "supply:Beta": ["s1", "s2", "s3", "x1"],  # shares 3 with Alpha
    "supply:Gamma": ["s1", "s2", "s9"],  # shares 2 with Alpha/Beta
    "supply:Delta": ["s5", "s6"],  # shares nothing
    "supply:Epsilon": ["s1"],  # singleton portfolio (dropped by the
    # live guard; kept here to prove scoring tolerates it)
}


def test_score_pairs_math_and_normalization():
    rows = cs.score_pairs(BUYERS, min_shared=1)
    by_pair = {(r["buyer1"], r["buyer2"]): r for r in rows}
    # Alpha×Beta share 3 of min(4,4)=4 → 0.75
    ab = by_pair[("supply:Alpha", "supply:Beta")]
    assert ab["shared"] == 3 and ab["score"] == 0.75
    # Alpha×Gamma share 2 of min(4,3)=3 → 0.6667
    ag = by_pair[("supply:Alpha", "supply:Gamma")]
    assert ag["shared"] == 2 and ag["score"] == round(2 / 3, 4)
    # Gamma×Beta share 2 of min(3,4)=3
    assert by_pair[("supply:Beta", "supply:Gamma")]["shared"] == 2
    # Delta shares nothing — absent
    assert all("Delta" not in (r["buyer1"] + r["buyer2"]) for r in rows)


def test_score_pairs_banding():
    rows = cs.score_pairs(BUYERS, min_shared=1)
    by_pair = {(r["buyer1"], r["buyer2"]): r for r in rows}
    assert by_pair[("supply:Alpha", "supply:Beta")]["band"] == "watch"  # shared 3 < 4
    big = {
        "supply:One": ["s1", "s2", "s3", "s4", "s5"],
        "supply:Two": ["s1", "s2", "s3", "s4", "s6"],
    }
    (row,) = cs.score_pairs(big, min_shared=1)
    assert row["shared"] == 4 and row["band"] == "high"


def test_score_pairs_min_shared_filter():
    rows = cs.score_pairs(BUYERS, min_shared=3)
    assert len(rows) == 1  # only Alpha×Beta at 3
    rows = cs.score_pairs(BUYERS, min_shared=2)
    assert len(rows) == 3


def test_score_pairs_deterministic():
    assert cs.score_pairs(BUYERS, min_shared=1) == cs.score_pairs(BUYERS, min_shared=1)


def test_band_override():
    rows = cs.score_pairs(BUYERS, min_shared=1, band=3)
    by_pair = {(r["buyer1"], r["buyer2"]): r for r in rows}
    assert by_pair[("supply:Alpha", "supply:Beta")]["band"] == "high"  # shared 3 >= band 3


def test_cli_end_to_end(tmp_path, monkeypatch, capsys):
    """CLI over a monkeypatched family — rc 0, ranked report printed,
    no live store touched."""
    monkeypatch.setattr(cs, "load_buyers", lambda **k: BUYERS)
    rc = cs._cli(["--top", "2"])
    out = capsys.readouterr().out
    assert rc == 0
    # CLI defaults: min-shared 2 → 3 pairs, top 2 prints 1 overflow line
    assert "co-supply peers — 5 buyers" in out
    assert "3 pairs sharing >=2 suppliers" in out
    assert "Alpha (4) × Beta (4)" in out  # shared=3 ranks first
    assert "1 more (raise --top)" in out
    assert "prereg-gated" in out


def test_cli_empty_family(capsys):
    monkey = pytest.MonkeyPatch()
    monkey.setattr(cs, "load_buyers", lambda **k: {})
    rc = cs._cli([])
    monkey.undo()
    assert rc == 1
    assert "empty or absent" in capsys.readouterr().err
