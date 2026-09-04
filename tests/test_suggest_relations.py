"""Tests for helpers/graph/suggest_relations.py (C2 suggestions workflow)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

from helpers.graph import suggest_relations as SR  # noqa: E402

PAIRS = [
    ("Acme", "Beta", 1.0),
    ("Acme", "Gamma", 0.5),
    ("Acme", "Delta", 0.2),  # below floor
    ("Acme", "Acme", 0.9),  # self
    ("Beta", "Gamma", 0.7),
    ("Acme", "Epsilon", 0.6),  # non-company endpoint
    ("Acme", "Beta", 0.8),  # duplicate of pair 1 (already seen)
    ("Zeta", "Eta", 0.9),  # already has a real edge
    ("Theta", "Iota", 0.4),
]
COMPANIES = {"Acme", "Beta", "Gamma", "Zeta", "Eta", "Theta", "Iota"}
EXISTING = {frozenset(("Zeta", "Eta"))}


class TestFilterSuggestions:
    def test_accepts_valid_pairs_in_order(self):
        got = SR.filter_suggestions(
            PAIRS,
            existing_pairs=EXISTING,
            companies=COMPANIES,
            top=10,
            min_score=0.3,
        )
        assert got == [
            ("Acme", "Beta", 1.0),
            ("Acme", "Gamma", 0.5),
            ("Beta", "Gamma", 0.7),
            ("Theta", "Iota", 0.4),
        ]

    def test_min_score_floor_drops_weak_pairs(self):
        got = SR.filter_suggestions(
            PAIRS,
            existing_pairs=EXISTING,
            companies=COMPANIES,
            top=10,
            min_score=0.5,
        )
        assert ("Theta", "Iota", 0.4) not in got

    def test_top_caps_output(self):
        got = SR.filter_suggestions(
            PAIRS,
            existing_pairs=EXISTING,
            companies=COMPANIES,
            top=2,
            min_score=0.3,
        )
        assert len(got) == 2

    def test_companies_only_false_admits_non_companies(self):
        got = SR.filter_suggestions(
            PAIRS,
            existing_pairs=EXISTING,
            companies=COMPANIES,
            top=10,
            min_score=0.3,
            companies_only=False,
        )
        assert ("Acme", "Epsilon", 0.6) in got

    def test_existing_edges_and_self_and_dupes_excluded(self):
        got = SR.filter_suggestions(
            PAIRS,
            existing_pairs=EXISTING,
            companies=COMPANIES,
            top=10,
            min_score=0.0,
        )
        flat = {frozenset((a, b)) for a, b, _ in got}
        assert frozenset(("Zeta", "Eta")) not in flat
        assert frozenset(("Acme", "Acme")) not in flat
        assert sum(1 for p in got if frozenset((p[0], p[1])) == frozenset(("Acme", "Beta"))) == 1


class TestSidecar:
    def test_suggestion_row_contract(self):
        s = SR.Suggestion("Acme", "Beta", 0.83333, "jaccard", "link-prediction/jaccard/2026-08-18")
        row = s.to_row()
        assert row["edge_type"] == "suggested"
        assert row["source"] == "Acme"
        assert row["target_mention"] == "Beta"
        assert row["quote"] == ""
        assert row["origin"] == "link_prediction"
        assert row["score"] == 0.8333
        assert row["method"] == "jaccard"

    def test_append_and_dedup(self, tmp_path):
        sidecar = tmp_path / "_pending_relations.txt"
        suggs = [
            SR.Suggestion("Acme", "Beta", 0.8, "jaccard", "e1"),
            SR.Suggestion("Gamma", "Delta", 0.5, "jaccard", "e1"),
        ]
        assert SR.append_suggestions(suggs, path=sidecar, existing_pairs=set()) == 2
        rows = [json.loads(line) for line in sidecar.read_text().splitlines()]
        assert len(rows) == 2 and rows[0]["edge_type"] == "suggested"
        # second run: all pairs already suggested -> 0 appended, no dupes
        assert SR.append_suggestions(suggs, path=sidecar, existing_pairs=set()) == 0
        assert len(sidecar.read_text().splitlines()) == 2
        # an existing real edge suppresses even a new suggestion
        assert (
            SR.append_suggestions(
                [SR.Suggestion("Acme", "Beta", 0.8, "jaccard", "e1")],
                path=tmp_path / "other.txt",
                existing_pairs={frozenset(("Acme", "Beta"))},
            )
            == 0
        )

    def test_prior_suggestion_pairs_reads_only_origin_rows(self, tmp_path):
        sidecar = tmp_path / "_pending.txt"
        sidecar.write_text(
            json.dumps(
                {
                    "edge_type": "jv_with",
                    "source": "Old",
                    "target_mention": "Miss",
                    "quote": "",
                    "edition": "x",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "edge_type": "suggested",
                    "source": "A",
                    "target_mention": "B",
                    "quote": "",
                    "edition": "y",
                    "origin": "link_prediction",
                }
            )
            + "\n"
            + "not json at all\n"
        )
        pairs = SR.prior_suggestion_pairs(sidecar)
        assert pairs == {frozenset(("A", "B"))}

    def test_append_empty_is_noop(self, tmp_path):
        sidecar = tmp_path / "_pending.txt"
        assert SR.append_suggestions([], path=sidecar) == 0
        assert not sidecar.exists()


class TestCLI:
    def _patch(self, monkeypatch, suggs):
        monkeypatch.setattr(SR, "suggest_relations", lambda **kw: suggs)

    def test_dry_run_default_prints_and_writes_nothing(self, monkeypatch, tmp_path, capsys):
        self._patch(monkeypatch, [SR.Suggestion("A", "B", 0.5, "jaccard", "e")])
        rc = SR.main(["--out", str(tmp_path / "s.txt")])
        out = capsys.readouterr().out
        assert rc == 0 and "A  <->  B" in out and "(1 suggestions" in out
        assert not (tmp_path / "s.txt").exists()

    def test_append_flag_writes_sidecar(self, monkeypatch, tmp_path, capsys):
        sidecar = tmp_path / "s.txt"
        suggs = [SR.Suggestion("A", "B", 0.5, "jaccard", "e")]
        self._patch(monkeypatch, suggs)
        assert SR.main(["--append", "--out", str(sidecar)]) == 0
        assert "appended 1" in capsys.readouterr().out
        rows = [json.loads(line) for line in sidecar.read_text().splitlines()]
        assert rows[0]["origin"] == "link_prediction"
        # rerun: deduped
        self._patch(monkeypatch, suggs)
        SR.main(["--append", "--out", str(sidecar)])
        assert "appended 0" in capsys.readouterr().out


@pytest.mark.live
class TestLiveSuggestions:
    """End-to-end against the real graph (skips without memory/graph.duckdb)."""

    @pytest.fixture(autouse=True)
    def _need_live(self):
        if not SR.DEFAULT_DUCKDB.exists():
            pytest.skip("live graph.duckdb not present")

    def test_live_suggestions_respect_filters(self):
        got = SR.suggest_relations(top=5)
        assert 0 < len(got) <= 5
        for s in got:
            assert 0.0 < s.score <= 1.0
            assert s.source and s.target and s.source != s.target
            assert s.method == "jaccard"
