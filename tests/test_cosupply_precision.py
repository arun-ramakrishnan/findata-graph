"""Hermetic tests for the co-supply precision runner (co_supply_peer_lane S2).

Synthetic snapshots + monkeypatched surfaces — no live store, no live
snapshot file.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


import helpers.research.cosupply_precision as cp  # noqa: E402

ROWS = [
    # Alpha's dense neighborhood: Beta(5), Gamma(4), Delta(3)
    {"buyer1": "Alpha", "buyer2": "Beta", "shared": "5", "score": "1.0", "computed_at": "F"},
    {"buyer1": "Alpha", "buyer2": "Gamma", "shared": "4", "score": "0.8", "computed_at": "F"},
    {"buyer1": "Alpha", "buyer2": "Delta", "shared": "3", "score": "0.6", "computed_at": "F"},
    # Beta-Gamma at 2 (both lists already have each other via Alpha rows)
    {"buyer1": "Beta", "buyer2": "Gamma", "shared": "2", "score": "0.4", "computed_at": "F"},
]


def test_ranked_candidates_order_is_frozen_deterministic():
    c = cp.ranked_candidates(ROWS)
    # Alpha's list: shared desc → Beta(5), Gamma(4), Delta(3)
    assert c["Alpha"] == ["Beta", "Gamma", "Delta"]
    # ties resolve by name asc (Beta's list via Alpha 5 / Gamma 2)
    assert c["Beta"][0] == "Alpha"
    assert set(c["Beta"]) == {"Alpha", "Gamma"}
    # a pure function of the frozen rows — same input, same output
    assert c == cp.ranked_candidates(ROWS)


def test_hit_at_k_symmetric_directions():
    # realized pair (Alpha, Delta): Delta is rank 3 in Alpha's list and
    # Alpha is rank 1 in Delta's list — symmetric hit detection
    c = cp.ranked_candidates(ROWS)
    k = 3
    assert c["Delta"].index("Alpha") + 1 <= k  # B-side rank
    assert c["Alpha"].index("Delta") + 1 <= k  # A-side rank
    # at k=2, Alpha→Delta misses but Delta→Alpha hits — the OR keeps it
    assert (c["Alpha"].index("Delta") + 1 <= 2) is False
    assert (c["Delta"].index("Alpha") + 1 <= 2) is True


def _score_with(monkeypatch, rows, realized, alias_map=None, titles_pre=0, titles_new=0):
    monkeypatch.setattr(cp, "load_snapshot", lambda: (rows, "F"))
    monkeypatch.setattr(cp, "_alias_map", lambda: alias_map or {})
    monkeypatch.setattr(cp, "realized_pairs", lambda con, stamp: (realized, titles_new))
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cp.score()
    return rc, buf.getvalue()


def test_score_counts_hits_and_base_rate(monkeypatch):
    realized = {("Alpha", "Delta")}  # in both lists
    rc, out = _score_with(monkeypatch, ROWS, realized)
    assert rc == 0
    assert "precision@10: 1/1 = 100.0%" in out
    # base@10: A=Alpha list len 3 → min(10,3)/(N-1)=3/3=1.0 pooled
    assert "base 1.0000" in out


def test_score_realized_pair_outside_universe_ignored(monkeypatch):
    realized = {("Alpha", "Zeta")}  # Zeta has no frozen candidates
    rc, out = _score_with(monkeypatch, ROWS, realized)
    assert rc == 0
    assert "0/0" in out  # nothing measured


def test_score_alias_exclusion(monkeypatch):
    # Gamma fold-routes to Beta → the (Beta, Gamma) realization is excluded
    realized = {("Beta", "Gamma")}
    rc, out = _score_with(monkeypatch, ROWS, realized, alias_map={"gamma": "Beta"})
    assert rc == 0
    assert "alias-routed pairs excluded: 1" in out
    assert "0/0" in out


def test_score_no_realizations_prints_registration_note(monkeypatch):
    rc, out = _score_with(monkeypatch, ROWS, set())
    assert rc == 0
    assert "Nothing measurable yet" in out
    assert "prereg" in out or "outcome log" in out


def test_realized_pairs_new_title_guard(monkeypatch):
    """A title staged BOTH pre- and post-freeze is a re-stage, not a
    realization; only genuinely new titles contribute."""
    rows = [
        {"edition_title": "old", "created_at": "2026-01-01 00:00:00"},
        {"edition_title": "new", "created_at": "2026-12-01 00:00:00"},
    ]

    class FakeCon:
        @staticmethod
        def _rows(rs):
            class R:
                def __init__(self, rs):
                    self._rs = rs

                def fetchall(self):
                    return self._rs

            return R(rs)

        def execute(self, sql, *a):
            if "DISTINCT edition_title" in sql:
                return self._rows(rows)
            return self._rows([{"entity_name": "Alpha"}, {"entity_name": "Beta"}])

    monkeypatch.setattr(cp, "connect", lambda read_only=True: FakeCon())
    pairs, n_new = cp.realized_pairs(FakeCon(), "2026-06-01T00:00:00Z")
    assert n_new == 1  # only "new"
    assert ("Alpha", "Beta") in pairs
    assert len(pairs) == 1
