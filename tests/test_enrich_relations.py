#!/usr/bin/env python3
"""E2 tests — enrich_relations.py driver (yfinance competes_with pass).

Covers (proposal §8 E2 gate): mocked-fetcher unit tests, KNN/clique
topology math, double-run idempotence, dry-run parity, ticker-hygiene
classification. No network: fetch_fn is always injected.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typing import Any

from helpers.maintenance.enrich_relations import (  # noqa: E402
    ENTITY_GF_MAP_DDL,
    ENTITY_TICKER_STATUS_DDL,
    GF_NAME_MATCH_THRESHOLD,
    GfOutcome,
    append_gf_report_section,
    apply_edges,
    build_candidate_edges,
    clique_industry_pairs,
    knn_industry_pairs,
    load_companies,
    load_gf_targets,
    resolve_gf_target,
    run_finnhub_pass,
    run_googlefinance_pass,
    run_yfinance_pass,
    weight_for_rank,
)
from helpers.maintenance.exchange_search import BseMatch  # noqa: E402
from helpers.maintenance.finnhub_search import FhMatch  # noqa: E402


GRAPH_EDGES_DDL = """
CREATE TABLE graph_edges (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL REFERENCES entities(name),
    target TEXT NOT NULL REFERENCES entities(name),
    edge_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    properties TEXT NOT NULL DEFAULT '{}',
    valid_from DATE,
    valid_to DATE,
    source_ref TEXT NOT NULL,
    symmetric INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, target, edge_type),
    CHECK (source != target)
)
"""


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.execute(GRAPH_EDGES_DDL)
    conn.execute(
        "CREATE TABLE entities (name TEXT PRIMARY KEY, ticker TEXT,"
        " file_path TEXT, entity_type TEXT)"
    )
    yield conn
    conn.close()


def _info(industry):
    return {"industry": industry}


# --------------------------------------------------------------------------- #
# Topology math                                                               #
# --------------------------------------------------------------------------- #
class TestKnnTopology:
    def test_symmetric_canonical_pairs(self):
        caps = {"A": 3.0, "B": 3.1, "C": 5.0}
        pairs = knn_industry_pairs(["A", "B", "C"], caps, k=1)
        # Every node contributes its single nearest neighbour: A<->B meet in
        # the middle (one canonical pair); C's nearest is B.
        assert set(pairs) == {("A", "B"), ("B", "C")}
        assert all(a <= b for a, b in pairs)

    def test_k_caps_neighbours(self):
        names = [f"N{i:02d}" for i in range(10)]
        caps = {n: float(i) for i, n in enumerate(names)}
        pairs = knn_industry_pairs(names, caps, k=3)
        # Each of 10 nodes contributes <=3 directed arcs; union symmetric.
        per_node: dict[str, int] = {}
        for a, b in pairs:
            per_node[a] = per_node.get(a, 0) + 1
            per_node[b] = per_node.get(b, 0) + 1
        assert max(per_node.values()) <= 6  # <=k out-arcs + <=k in-arcs
        # Nearest neighbours are size-adjacent: N00 must pair with N01.
        assert ("N00", "N01") in pairs

    def test_missing_mcap_falls_back_to_median(self):
        names = ["A", "B", "C"]
        caps = {"B": 4.0}  # A and C have no signal
        pairs = knn_industry_pairs(names, caps, k=2)
        # Median anchors A and C at 4.0; every node can still connect.
        assert len(pairs) >= 2
        assert any("A" in p for p in pairs)

    def test_all_missing_mcap_does_not_crash(self):
        pairs = knn_industry_pairs(["A", "B", "C"], {}, k=2)
        assert len(pairs) == 3  # complete graph via alphabetical tie-break

    def test_single_member_industry_no_edges(self):
        assert knn_industry_pairs(["Solo"], {"Solo": 3.0}, k=8) == {}

    def test_clique_topology(self):
        pairs = clique_industry_pairs(["B", "A", "C"])
        assert pairs == {("A", "B"): 1, ("A", "C"): 1, ("B", "C"): 1}

    def test_weight_decay_bounds(self):
        k = 8
        ws = [weight_for_rank(r, k) for r in range(1, k + 1)]
        assert ws[0] == 1.0
        assert ws[-1] == pytest.approx(0.4)
        assert ws == sorted(ws, reverse=True)

    def test_mutual_filter_drops_one_sided_picks(self):
        # Size ladder: A-B close, B-C mid, C alone at the top of the pile.
        # Directed K=1: A picks B; B picks A (mutual pair); C picks B (one
        # way — B's own nearest is A, not C).
        caps = {"A": 3.0, "B": 3.2, "C": 6.0}
        directed = knn_industry_pairs(["A", "B", "C"], caps, k=1)
        assert set(directed) == {("A", "B"), ("B", "C")}
        mutual = knn_industry_pairs(["A", "B", "C"], caps, k=1, mutual=True)
        assert set(mutual) == {("A", "B")}

    def test_mutual_filter_keeps_all_when_symmetric(self):
        # Two companies only: each is the other's nearest — mutuality keeps it.
        caps = {"X": 2.0, "Y": 2.1}
        assert set(knn_industry_pairs(["X", "Y"], caps, k=1, mutual=True)) == {("X", "Y")}


# --------------------------------------------------------------------------- #
# Candidate edges from fetched info                                           #
# --------------------------------------------------------------------------- #
class TestBuildCandidateEdges:
    def test_industries_grouped_and_sorted(self):
        info = {
            "Alpha": _info("Steel"),
            "Beta": _info("Steel"),
            "Gamma": _info("Steel"),
            "Delta": _info("Banking"),
            "Echo": _info(""),  # no industry -> excluded
        }
        edges = build_candidate_edges(info, {}, topology="clique", k=8)
        types = {(s, t) for s, t, *_ in edges}
        assert ("Alpha", "Beta") in types and ("Alpha", "Gamma") in types
        assert all("Banking" not in e[4]["industry"] or True for e in edges)
        assert not any({"Delta"} & {s, t} for s, t, *_ in edges)
        assert not any("Echo" in (s, t) for s, t, *_ in edges)
        source_refs = {e[3] for e in edges}
        assert all(r.startswith("yfinance:industry:") for r in source_refs)

    def test_edge_properties_carry_provenance(self):
        info = {"Alpha": _info("Steel"), "Beta": _info("Steel")}
        edges = build_candidate_edges(info, {"Alpha": 3.0, "Beta": 3.5}, k=1)
        (_s, _t, weight, _ref, props) = edges[0]
        assert props["industry"] == "Steel"
        assert "fetched_at" in props and props["rank"] == 1
        assert weight == 1.0


# --------------------------------------------------------------------------- #
# Apply / idempotence / parity                                                #
# --------------------------------------------------------------------------- #
def _sample_edges(today="2026-08-24"):
    return [
        ("A", "B", 1.0, f"yfinance:industry:{today}", {"industry": "X", "rank": 1}),
        ("A", "C", 0.7, f"yfinance:industry:{today}", {"industry": "X", "rank": 4}),
        ("B", "C", 0.4, f"yfinance:industry:{today}", {"industry": "X", "rank": 8}),
    ]


class TestApplyEdges:
    def test_dry_run_writes_nothing_and_counts(self, db):
        n = apply_edges(db, _sample_edges(), dry_run=True)
        assert n == 3
        assert db.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0] == 0

    def test_double_run_idempotent(self, db):
        first = apply_edges(db, _sample_edges(), dry_run=False)
        second = apply_edges(db, _sample_edges(), dry_run=False)
        assert (first, second) == (3, 3)  # DELETE-by-prefix + re-INSERT
        assert (
            db.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE source_ref LIKE 'yfinance:%'"
            ).fetchone()[0]
            == 3
        )

    def test_apply_replaces_stale_yfinance_rows_only(self, db):
        apply_edges(db, _sample_edges(), dry_run=False)
        db.execute(
            "INSERT OR IGNORE INTO graph_edges (source, target, edge_type, "
            "weight, properties, source_ref, symmetric) "
            "VALUES ('A', 'D', 'competes_with', 1.0, '{}', "
            "'derive:relations:The_Chatter', 1)"
        )
        db.commit()
        # New run with fewer candidates: prose-derived row must SURVIVE the
        # prefix-scoped delete; stale yfinance rows must be replaced.
        apply_edges(db, _sample_edges()[:1], dry_run=False)
        kept = db.execute(
            "SELECT source_ref FROM graph_edges WHERE edge_type='competes_with'"
        ).fetchall()
        refs = {r[0] for r in kept}
        assert refs == {
            "derive:relations:The_Chatter",
            db.execute(
                "SELECT source_ref FROM graph_edges WHERE source='A' AND target='B'"
            ).fetchone()[0],
        }

    def test_unrelated_types_survive(self, db):
        db.execute(
            "INSERT OR IGNORE INTO graph_edges (source, target, edge_type, "
            "properties, source_ref, symmetric) "
            "VALUES ('A', 'Z', 'jv_with', '{}', 'derive:relations:X', 1)"
        )
        db.commit()
        apply_edges(db, _sample_edges(), dry_run=False)
        n = db.execute("SELECT COUNT(*) FROM graph_edges WHERE edge_type='jv_with'").fetchone()[0]
        assert n == 1

    def test_row_shape(self, db):
        apply_edges(db, _sample_edges(), dry_run=False)
        row = db.execute(
            "SELECT source, target, weight, properties, source_ref, symmetric "
            "FROM graph_edges WHERE source='A' AND target='B'"
        ).fetchone()
        assert row[0] <= row[1]
        assert row[2] == 1.0
        assert json.loads(row[3])["rank"] == 1
        assert row[5] == 1


# --------------------------------------------------------------------------- #
# Mocked-fetcher pass + hygiene                                               #
# --------------------------------------------------------------------------- #
class TestYfinancePass:
    @staticmethod
    def _fake_fetcher(fail=()):
        def fetch_fn(ticker):
            if ticker in fail:
                return None
            return {"industry": "Steel"}

        return fetch_fn

    @staticmethod
    def _seed_companies(db, tickers=("T1.NS", "T2.NS", "T3.BO")):
        for i, t in enumerate(tickers):
            db.execute("INSERT INTO entities VALUES (?, ?, NULL, 'company')", (f"C{i}", t))
        db.execute("INSERT INTO entities VALUES ('UnlistedCo', NULL, NULL, 'company')")
        db.commit()

    def test_pass_classifies_hygiene_failures(self, db, tmp_path, monkeypatch):
        self._seed_companies(db)
        monkeypatch.setattr(
            "helpers.maintenance.enrich_relations.REPORT_PATH", tmp_path / "relations_report.txt"
        )
        rc = run_yfinance_pass(
            db,
            check_only=True,
            fetch_fn=self._fake_fetcher(fail={"T2.NS"}),
            workers=1,
            fetch_cache=None,
        )  # cache off: exercise the fetcher
        assert rc == 0
        report = (tmp_path / "relations_report.txt").read_text()
        assert "[ticker_issues]" in report
        assert "C1 | T2.NS" in report
        assert "UnlistedCo" in report  # reported as deliberately skipped

    def test_fetch_cache_write_and_reuse(self, db, tmp_path, monkeypatch):
        self._seed_companies(db)
        monkeypatch.setattr(
            "helpers.maintenance.enrich_relations.REPORT_PATH", tmp_path / "relations_report.txt"
        )
        cache_path = tmp_path / "fetch_cache.json"
        calls = {"n": 0}

        def counting_fetch(ticker):
            calls["n"] += 1
            return {"industry": "Steel"}

        run_yfinance_pass(
            db, check_only=True, fetch_fn=counting_fetch, workers=1, fetch_cache=cache_path
        )
        assert calls["n"] == 3
        payload = json.loads(cache_path.read_text())
        assert "fetched_at" in payload and len(payload["info_by_name"]) == 3

        # Second run: fetch_fn must NOT be called again.
        def exploding_fetch(ticker):
            raise AssertionError("network hit despite warm cache")

        run_yfinance_pass(
            db, check_only=True, fetch_fn=exploding_fetch, workers=1, fetch_cache=cache_path
        )

    def test_refresh_cache_refetches(self, db, tmp_path, monkeypatch):
        self._seed_companies(db)
        monkeypatch.setattr(
            "helpers.maintenance.enrich_relations.REPORT_PATH", tmp_path / "relations_report.txt"
        )
        cache_path = tmp_path / "fetch_cache.json"
        calls = {"n": 0}

        def counting_fetch(ticker):
            calls["n"] += 1
            return {"industry": "Steel"}

        run_yfinance_pass(
            db, check_only=True, fetch_fn=counting_fetch, workers=1, fetch_cache=cache_path
        )
        run_yfinance_pass(
            db,
            check_only=True,
            fetch_fn=counting_fetch,
            workers=1,
            fetch_cache=cache_path,
            refresh_cache=True,
        )
        assert calls["n"] == 6

    def test_check_only_writes_nothing(self, db, tmp_path, monkeypatch):
        self._seed_companies(db)
        monkeypatch.setattr(
            "helpers.maintenance.enrich_relations.REPORT_PATH", tmp_path / "relations_report.txt"
        )
        run_yfinance_pass(
            db, check_only=True, fetch_fn=self._fake_fetcher(), workers=1, fetch_cache=None
        )
        assert db.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# Universe loading (ticker-first contract)                                    #
# --------------------------------------------------------------------------- #
class TestLoadCompanies:
    def test_ticker_first_split(self, db):
        db.executemany(
            "INSERT INTO entities VALUES (?, ?, NULL, 'company')",
            [("Listed", "X.NS"), ("Blank", ""), ("NullTicker", None)],
        )
        db.commit()
        companies, unlisted = load_companies(db)
        assert [c[0] for c in companies] == ["Listed"]
        assert sorted(unlisted) == ["Blank", "NullTicker"]


# --------------------------------------------------------------------------- #
# Google-Finance fallback pass (F2: curated read + tier 1, no network —      #
# fetch_fn serves saved F1 fixtures)                                          #
# --------------------------------------------------------------------------- #
_GF_FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "googlefinance"
_AJAX_PAGE = (_GF_FIXTURES / "quote_AJAXENGG_NSE.html").read_text("utf-8")
_SRIGEE_PAGE = (_GF_FIXTURES / "quote_544399_BOM.html").read_text("utf-8")
_SHELL_PAGE = (_GF_FIXTURES / "quote_BOGUS_NSE.html").read_text("utf-8")


def _gf_fetch(pages: dict[str, str]):
    """Fake fetch_fn: known slugs serve the mapped fixture HTML, every
    other slug serves the 200-but-dead shell. from_cache=True throughout
    so the politeness delay never sleeps in tests."""

    def fetch(slug, cache_dir, **_kw):
        return pages.get(slug, _SHELL_PAGE), True

    return fetch


def _write_gf_report(
    path, *, issues=(("Srigee DLM", "SRIGEE.NS"),), unlisted=("Veeda Clinical Research",)
) -> str:
    """A minimal relations_report.txt as the yfinance pass writes it."""
    text = (
        "relations_report.txt — enrich_relations.py\n"
        "[universe]\n"
        "  tickered companies: 1\n"
        + "".join(f"  (unlisted) {n}\n" for n in unlisted)
        + "\n[ticker_issues]  # 404 / no-data — enrichment silently starves on these\n"
        "    # name | ticker\n"
        + "".join(f"    {n} | {t}\n" for n, t in issues)
        + "\n[industries]\n  1  Steel\n"
    )
    path.write_text(text, encoding="utf-8")
    return text


class TestLoadGfTargets:
    def test_parses_ticker_issues_and_skips_comment(self, tmp_path):
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=(("Srigee DLM", "SRIGEE.NS"), ("Gati", "ACLGATI.BO")))
        assert load_gf_targets(rp, include_unlisted=False) == [
            ("Srigee DLM", "SRIGEE.NS"),
            ("Gati", "ACLGATI.BO"),
        ]

    def test_include_unlisted_appends_none_ticker_targets(self, tmp_path):
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp)
        targets = load_gf_targets(rp, include_unlisted=True)
        assert targets == [("Srigee DLM", "SRIGEE.NS"), ("Veeda Clinical Research", None)]


class TestResolveGfTarget:
    # cache_dir is opaque to the fake fetch; delay=0 keeps tests sleep-free.
    KW: dict[str, Any] = {"cache_dir": Path("/nonexistent"), "delay": 0}

    def test_tier1_verified_hit_gf_only(self):
        out = resolve_gf_target(
            "Ajax Engineering",
            "AJAXENGG.NS",
            curated=None,
            fetch_fn=_gf_fetch({"AJAXENGG:NSE": _AJAX_PAGE}),
            **self.KW,
        )
        # Same stem as the failing Yahoo symbol -> GF-only, no writeback.
        assert out.outcome == "resolved gf-only"
        assert out.slug == "AJAXENGG:NSE"
        assert out.score is not None
        assert out.score >= GF_NAME_MATCH_THRESHOLD
        assert out.stats["pe_ratio"] == pytest.approx(29.01)

    def test_tier1_hit_with_new_stem_is_yahoo_candidate(self):
        # Tier-1 candidates are stem-preserving swaps of the failing Yahoo
        # symbol, so a tier-1 hit is gf-only BY CONSTRUCTION here — the
        # yahoo-candidate classification (different stem, G4 writeback)
        # only becomes reachable via tier 2 (F3) or curation. Pin that.
        out = resolve_gf_target(
            "Ajax Engineering",
            "ACLGATI.BO",
            curated=None,
            fetch_fn=_gf_fetch({"ACLGATI:NSE": _AJAX_PAGE}),
            **self.KW,
        )
        assert out.outcome == "resolved gf-only"
        assert out.slug == "ACLGATI:NSE"

    def test_first_verified_candidate_wins(self):
        # Both variants serve valid pages; native-exchange-first order
        # must win, not whichever fetch returned last.
        out = resolve_gf_target(
            "Ajax Engineering",
            "SRIGEE.NS",
            curated=None,
            fetch_fn=_gf_fetch({"SRIGEE:NSE": _AJAX_PAGE, "SRIGEE:BOM": _SRIGEE_PAGE}),
            **self.KW,
        )
        assert out.outcome == "resolved gf-only"
        assert out.slug == "SRIGEE:NSE"

    def test_below_threshold_kept_as_unverified_for_curation(self):
        out = resolve_gf_target(
            "Bosch Limited",
            "X.NS",
            curated=None,
            fetch_fn=_gf_fetch({"X:NSE": _AJAX_PAGE}),
            **self.KW,
        )
        assert out.outcome == "unverified"
        assert out.parsed_name == "AJAX Engineering Ltd"
        assert out.score is not None
        assert out.score < GF_NAME_MATCH_THRESHOLD

    def test_all_shell_pages_still_dead(self):
        out = resolve_gf_target(
            "Srigee DLM", "SRIGEE.NS", curated=None, fetch_fn=_gf_fetch({}), **self.KW
        )
        assert out.outcome == "still-dead"
        assert out.slug is None

    def test_bare_foreign_ticker_no_candidates(self):
        out = resolve_gf_target(
            "Hanesbrands", "HBI", curated=None, fetch_fn=_gf_fetch({}), **self.KW
        )
        assert out.outcome == "no-candidates"

    def test_unlisted_entity_no_candidates_without_curation(self):
        out = resolve_gf_target(
            "Veeda Clinical Research", None, curated=None, fetch_fn=_gf_fetch({}), **self.KW
        )
        assert out.outcome == "no-candidates"  # tier 2 (F3) is their hope

    def test_curated_hit_trusted_and_serves_unlisted_too(self):
        # Curated overrides are the ONLY tier that can serve an unlisted
        # entity before F3 lands.
        out = resolve_gf_target(
            "Ajax Engineering",
            None,
            curated=("AJAXENGG:NSE", "gf_only"),
            fetch_fn=_gf_fetch({"AJAXENGG:NSE": _AJAX_PAGE}),
            **self.KW,
        )
        assert out.outcome == "curated (gf_only)"
        assert out.slug == "AJAXENGG:NSE"
        assert out.stats  # sample data still parsed for the report

    def test_curated_broken_reported_not_re_resolved(self):
        # Tier-3 discipline: a rotted curated slug is REPORTED for the
        # human to fix; tier 1 must not silently take over.
        out = resolve_gf_target(
            "Ajax Engineering",
            "AJAXENGG.NS",
            curated=("BOGUS:NSE", "gf_only"),
            fetch_fn=_gf_fetch({"AJAXENGG:NSE": _AJAX_PAGE}),
            **self.KW,
        )
        assert out.outcome == "curated-broken"
        assert out.slug == "BOGUS:NSE"

    def test_curated_yahoo_mapped_back_kind_propagates(self):
        out = resolve_gf_target(
            "Gati",
            "ACLGATI.BO",
            curated=("GATI:NSE", "yahoo_mapped_back"),
            fetch_fn=_gf_fetch({"GATI:NSE": _AJAX_PAGE}),
            **self.KW,
        )
        assert out.outcome == "curated (yahoo_mapped_back)"


class TestResolveTier2:
    """F3: BSE name-search candidates behind the tier2 flag."""

    KW: dict[str, Any] = {"cache_dir": Path("/nonexistent"), "delay": 0}

    @staticmethod
    def _search(matches):
        def search_fn(_query, _cache_dir):
            return matches, True

        return search_fn

    def test_srigee_case_resolved_via_bse_scrip(self):
        # The §7 success criterion: tier-1 swaps are shells, the BSE row
        # gives scrip 544399 -> GF slug 544399:BOM verifies.
        out = resolve_gf_target(
            "Srigee DLM",
            "SRIGEE.NS",
            curated=None,
            tier2=True,
            search_fn=self._search([BseMatch("544399", "SRIGEE", "SRIGEE DLM LTD")]),
            fetch_fn=_gf_fetch({"544399:BOM": _SRIGEE_PAGE}),
            **self.KW,
        )
        assert out.outcome == "resolved yahoo-candidate"  # stem 544399 != SRIGEE
        assert out.slug == "544399:BOM"
        assert out.source_tier == 2
        assert out.score == pytest.approx(1.0)

    def test_tier2_skips_tier1_tried_slugs(self):
        # Search returns the same symbol tier 1 already tried; it must be
        # fetched once (tier 1), never re-fetched by tier 2.
        fetched: list[str] = []
        pages = {"AJAXENGG:NSE": _AJAX_PAGE}

        def recording_fetch(slug, cache_dir, **_kw):
            fetched.append(slug)
            return pages.get(slug, _SHELL_PAGE), True

        out = resolve_gf_target(
            "Ajax Engineering",
            "BOGUS.NS",
            curated=None,
            tier2=True,
            search_fn=self._search([BseMatch("544356", "AJAXENGG", "AJAX ENGINEERING LTD")]),
            fetch_fn=recording_fetch,
            **self.KW,
        )
        # Tier 1: BOGUS:NSE/BOM both tried; search adds 544356:BOM (hit)
        # and AJAXENGG:NSE — already tried, must not appear twice.
        assert out.slug == "544356:BOM" or out.slug == "AJAXENGG:NSE"
        assert fetched.count("AJAXENGG:NSE") == 1

    def test_wrong_company_rows_stay_unverified(self):
        # 'gati' search returns JAIN IRRIGATION rows; a valid page for a
        # different company never resolves (Tier-C discipline).
        out = resolve_gf_target(
            "Gati",
            "ACLGATI.BO",
            curated=None,
            tier2=True,
            search_fn=self._search(
                [BseMatch("500219", "JISLJALEQS", "JAIN IRRIGATION SYSTEMS LTD")]
            ),
            fetch_fn=_gf_fetch({"500219:BOM": _AJAX_PAGE}),
            **self.KW,
        )
        assert out.outcome == "unverified"
        assert out.source_tier == 2
        assert out.parsed_name == "AJAX Engineering Ltd"

    def test_unlisted_entity_resolves_when_listed_after_all(self):
        # Unlisted entities have no tier 1; tier 2 can discover they ARE
        # listed (a G4 writeback candidate: ticker was null).
        out = resolve_gf_target(
            "Ajax Engineering",
            None,
            curated=None,
            tier2=True,
            search_fn=self._search([BseMatch("544356", "AJAXENGG", "AJAX ENGINEERING LTD")]),
            fetch_fn=_gf_fetch({"AJAXENGG:NSE": _AJAX_PAGE}),
            **self.KW,
        )
        assert out.outcome == "resolved yahoo-candidate"
        assert out.source_tier == 2

    def test_tier2_off_by_default_never_searches(self):
        def exploding_search(_query, _cache_dir):
            raise AssertionError("tier-2 search with flag off")

        out = resolve_gf_target(
            "Srigee DLM",
            "SRIGEE.NS",
            curated=None,
            search_fn=exploding_search,
            fetch_fn=_gf_fetch({}),
            **self.KW,
        )
        assert out.outcome == "still-dead"

    def test_tier1_unverified_evidence_survives_tier2_still_dead(self):
        # Tier 1 finds a live wrong-name page; tier 2 rows are all shells.
        # The report should keep the live-page evidence (higher score).
        out = resolve_gf_target(
            "Bosch Limited",
            "X.NS",
            curated=None,
            tier2=True,
            search_fn=self._search([BseMatch("999999", "ZZZ", "SOMEONE ELSE LTD")]),
            fetch_fn=_gf_fetch({"X:NSE": _AJAX_PAGE}),
            **self.KW,
        )
        assert out.outcome == "unverified"
        assert out.source_tier == 1
        assert out.slug == "X:NSE"

    def test_search_failure_is_not_fatal(self):
        def failing_search(_query, _cache_dir):
            raise OSError("BSE down")

        out = resolve_gf_target(
            "Srigee DLM",
            "SRIGEE.NS",
            curated=None,
            tier2=True,
            search_fn=failing_search,
            fetch_fn=_gf_fetch({}),
            **self.KW,
        )
        assert out.outcome == "still-dead"

    def test_search_no_rows_reports_no_candidates(self):
        # Bare ticker: tier 2 was its only hope and found nothing.
        out = resolve_gf_target(
            "Hanesbrands",
            "HBI",
            curated=None,
            tier2=True,
            search_fn=self._search([]),
            fetch_fn=_gf_fetch({}),
            **self.KW,
        )
        assert out.outcome == "no-candidates"


class TestRunGoogleFinancePass:
    def test_missing_report_returns_2(self, db, tmp_path):
        rc = run_googlefinance_pass(
            db, report_path=tmp_path / "absent.txt", cache_dir=tmp_path, fetch_fn=_gf_fetch({})
        )
        assert rc == 2

    def test_appends_section_and_writes_no_data_rows(self, db, tmp_path):
        rp = tmp_path / "relations_report.txt"
        original = _write_gf_report(
            rp, issues=(("Ajax Engineering", "AJAXENGG.NS"), ("Srigee DLM", "SRIGEE.NS"))
        )
        rc = run_googlefinance_pass(
            db, report_path=rp, cache_dir=tmp_path, fetch_fn=_gf_fetch({"AJAXENGG:NSE": _AJAX_PAGE})
        )
        assert rc == 0
        # Dry-run creates the (empty) table but writes zero data rows.
        assert db.execute("SELECT COUNT(*) FROM entity_gf_map").fetchone()[0] == 0
        text = rp.read_text(encoding="utf-8")
        assert text.startswith(original)  # append-only, never regenerated
        assert "[google_finance]" in text
        assert "Ajax Engineering | resolved gf-only [t1] | AJAXENGG:NSE" in text
        assert "Srigee DLM | still-dead" in text

    def test_curated_row_drives_outcome(self, db, tmp_path):
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=(("Ajax Engineering", "AJAXENGG.NS"),))
        db.execute(ENTITY_GF_MAP_DDL)  # seed before the pass (it creates it)
        db.execute(
            "INSERT INTO entity_gf_map VALUES "
            "('Ajax Engineering', 'AJAXENGG:NSE', 'gf_only', "
            "'2026-08-25', 'AJAX Engineering Ltd')"
        )
        db.commit()
        rc = run_googlefinance_pass(
            db, report_path=rp, cache_dir=tmp_path, fetch_fn=_gf_fetch({"AJAXENGG:NSE": _AJAX_PAGE})
        )
        assert rc == 0
        assert "curated (gf_only)" in rp.read_text(encoding="utf-8")

    def test_include_unlisted_reports_them_as_no_candidates(self, db, tmp_path):
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=())
        rc = run_googlefinance_pass(
            db, include_unlisted=True, report_path=rp, cache_dir=tmp_path, fetch_fn=_gf_fetch({})
        )
        assert rc == 0
        text = rp.read_text(encoding="utf-8")
        assert "Veeda Clinical Research | no-candidates" in text

    def test_tier2_run_annotates_rows_and_header(self, db, tmp_path):
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=(("Srigee DLM", "SRIGEE.NS"),))
        rc = run_googlefinance_pass(
            db,
            tier2=True,
            report_path=rp,
            cache_dir=tmp_path,
            fetch_fn=_gf_fetch({"544399:BOM": _SRIGEE_PAGE}),
            search_fn=lambda q, c: ([BseMatch("544399", "SRIGEE", "SRIGEE DLM LTD")], True),
        )
        assert rc == 0
        text = rp.read_text(encoding="utf-8")
        assert "tier2 (BSE name search): on" in text
        assert "Srigee DLM | resolved yahoo-candidate (544399.BO) [t2] | 544399:BOM | 1.00" in text


# --------------------------------------------------------------------------- #
# GF --apply (S3): entity_gf_map persistence + company_metrics via Sheets   #
# --------------------------------------------------------------------------- #
_COMPANY_METRICS_DDL = """
CREATE TABLE company_metrics (
    id INTEGER PRIMARY KEY,
    entity TEXT NOT NULL,
    metric_label TEXT,
    value_raw TEXT NOT NULL,
    value_num REAL,
    unit TEXT,
    period TEXT,
    as_of_edition TEXT,
    source_quote TEXT,
    source_ref TEXT NOT NULL,
    properties TEXT NOT NULL DEFAULT '{}',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (json_valid(properties))
)
"""


class TestGfApply:
    def test_apply_persists_map_and_metrics_idempotently(self, db, tmp_path):
        db.execute(_COMPANY_METRICS_DDL)
        # Entity name matches the page's About-name so tier 1 verifies.
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=(("Srigee DLM", "544442.BO"),))

        def metrics_fn(requests):
            canned = {
                ("544442:BOM", "price"): 199.0,
                ("544442:BOM", "marketcap"): 2_190_000_000.0,
                ("544442:BOM", "pe"): 10.5,
                ("544442:BOM", "eps"): 18.95,
                ("544442:BOM", "high52"): 250.0,
                ("544442:BOM", "low52"): 120.0,
            }
            return {r: canned.get(r) for r in requests}

        for _ in range(2):  # second run must not duplicate (delete-by-prefix)
            rc = run_googlefinance_pass(
                db,
                apply_resolutions=True,
                report_path=rp,
                cache_dir=tmp_path,
                fetch_fn=_gf_fetch({"544442:BOM": _SRIGEE_PAGE}),
                metrics_fn=metrics_fn,
            )
            assert rc == 0

        maps = db.execute(
            "SELECT entity_name, gf_slug, kind, verified_name FROM entity_gf_map"
        ).fetchall()
        assert maps == [("Srigee DLM", "544442:BOM", "gf_only", "Srigee DLM Ltd")]
        rows = db.execute(
            "SELECT metric_label, value_num, unit, source_ref FROM "
            "company_metrics ORDER BY metric_label"
        ).fetchall()
        assert len(rows) == 6
        by_label = {r[0]: r for r in rows}
        assert by_label["pe_ratio"][1] == pytest.approx(10.5)
        # marketcap converts INR -> crore (stored-metric contract)
        assert by_label["market_capitalization"][1] == pytest.approx(219.0)
        assert by_label["market_capitalization"][2] == "crore"
        assert by_label["price"][2] == "inr"
        assert all(r[3].startswith("googlefinance:544442:BOM:") for r in rows)
        # apply summary line in the report
        assert "apply: 1 entity_gf_map rows · 6 company_metrics rows" in rp.read_text(
            encoding="utf-8"
        )

    def test_dry_run_writes_no_rows(self, db, tmp_path):
        db.execute(_COMPANY_METRICS_DDL)
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=(("Srigee DLM", "544442.BO"),))
        rc = run_googlefinance_pass(
            db,
            report_path=rp,
            cache_dir=tmp_path,
            fetch_fn=_gf_fetch({"544442:BOM": _SRIGEE_PAGE}),
            metrics_fn=lambda r: (_ for _ in ()).throw(
                AssertionError("metrics fetched on dry-run")
            ),
        )
        assert rc == 0
        assert db.execute("SELECT COUNT(*) FROM entity_gf_map").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM company_metrics").fetchone()[0] == 0

    def test_yahoo_candidate_gets_map_row_but_no_metrics(self, db, tmp_path):
        db.execute(_COMPANY_METRICS_DDL)
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=(("Srigee DLM", "SRIGEE.NS"),))
        rc = run_googlefinance_pass(
            db,
            tier2=True,
            apply_resolutions=True,
            report_path=rp,
            cache_dir=tmp_path,
            fetch_fn=_gf_fetch({"544399:BOM": _SRIGEE_PAGE}),
            search_fn=lambda q, c: ([BseMatch("544399", "SRIGEE", "SRIGEE DLM LTD")], True),
            metrics_fn=lambda r: (_ for _ in ()).throw(
                AssertionError("yahoo_mapped_back must not fetch metrics")
            ),
        )
        assert rc == 0
        assert db.execute("SELECT kind FROM entity_gf_map").fetchone()[0] == "yahoo_mapped_back"
        assert db.execute("SELECT COUNT(*) FROM company_metrics").fetchone()[0] == 0

    def test_section_renders_yahoo_candidate_symbol(self, tmp_path):
        # F3-forward render coverage: a different-stem resolved slug (only
        # producible by tier 2 or curation, never by stem-preserving
        # tier-1 swaps) must show the mapped-back Yahoo symbol.
        rp = tmp_path / "relations_report.txt"
        rp.write_text("", encoding="utf-8")
        outcomes = [
            GfOutcome(
                "Gati",
                "ACLGATI.BO",
                "resolved yahoo-candidate",
                "GATI:NSE",
                0.95,
                "Gati Ltd",
                {"pe_ratio": 12.3},
            )
        ]
        append_gf_report_section(rp, outcomes, include_unlisted=False)
        text = rp.read_text(encoding="utf-8")
        assert "[google_finance]" in text
        assert "Gati | resolved yahoo-candidate (GATI.NS) | GATI:NSE" in text


# --------------------------------------------------------------------------- #
# FinnHub stage-1 pass (market_data_resolution.md S1/S2)                      #
# --------------------------------------------------------------------------- #
class TestFinnhubPass:
    KW: dict[str, Any] = {"cache_dir": Path("/nonexistent"), "delay": 0}

    @staticmethod
    def _lookup(matches):
        def lookup_fn(_query, _cache_dir):
            return matches, True

        return lookup_fn

    @staticmethod
    def _verify(by_ticker):
        def verify_fn(ticker):
            return by_ticker.get(ticker)

        return verify_fn

    @staticmethod
    def _seed(db, rows):
        db.execute(
            "CREATE TABLE IF NOT EXISTS entities (name TEXT PRIMARY KEY,"
            " ticker TEXT, file_path TEXT, entity_type TEXT)"
        )
        db.executemany("INSERT OR REPLACE INTO entities VALUES (?, ?, ?, 'company')", rows)
        db.commit()

    @staticmethod
    def _note(tmp_path, name, ticker_line):
        note = tmp_path / f"{name.replace(' ', '_')}.md"
        note.write_text(
            f"---\nentity_type: company\n{ticker_line}\nsector: Test\n---\n\nbody\n",
            encoding="utf-8",
        )
        return note

    def test_writeback_discovered_and_verified(self, db, tmp_path):
        self._seed(db, [("Piramal Enterprises", "PIEIL.NS", None)])
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=(("Piramal Enterprises", "PIEIL.NS"),))
        verify = self._verify(
            {"PEL.NS": {"longName": "Piramal Enterprises Ltd", "industry": "Insurance"}}
        )
        rc = run_finnhub_pass(
            db,
            report_path=rp,
            lookup_fn=self._lookup([FhMatch("PEL.NS", "Piramal Enterprises Ltd")]),
            verify_fn=verify,
            fetch_cache=None,
            **self.KW,
        )
        assert rc == 0
        # dry-run writes nothing
        assert (
            db.execute("SELECT ticker FROM entities WHERE name = 'Piramal Enterprises'").fetchone()[
                0
            ]
            == "PIEIL.NS"
        )
        text = rp.read_text(encoding="utf-8")
        assert "[finnhub]" in text
        assert "Piramal Enterprises | writeback-candidate | PIEIL.NS -> PEL.NS" in text

    def test_akzo_guard_blocks_foreign_parent(self, db, tmp_path):
        # The §5 trap: FinnHub returns the Dutch parent for 'Akzo Nobel';
        # an India-domiciled entity must never take a non-.NS/.BO symbol.
        self._seed(db, [("Akzo Nobel India", "AKZOINDIA.BO", None)])
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=(("Akzo Nobel India", "AKZOINDIA.BO"),))
        rc = run_finnhub_pass(
            db,
            report_path=rp,
            lookup_fn=self._lookup([FhMatch("AKZA.AS", "Akzo Nobel NV")]),
            verify_fn=self._verify({}),  # empty: any fetch attempt would
            fetch_cache=None,
            **self.KW,
        )  # prove the guard leaked
        assert rc == 0
        assert "Akzo Nobel India | no-candidates" in rp.read_text("utf-8")

    def test_stored_ticker_excluded_from_candidates(self, db, tmp_path):
        self._seed(db, [("Srigee DLM", "544399.BO", None)])
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=(("Srigee DLM", "544399.BO"),))
        rc = run_finnhub_pass(
            db,
            report_path=rp,
            lookup_fn=self._lookup([FhMatch("544399.BO", "Srigee DLM Ltd")]),
            verify_fn=self._verify({}),
            fetch_cache=None,
            **self.KW,
        )
        assert rc == 0
        assert "Srigee DLM | no-candidates" in rp.read_text("utf-8")

    def test_yahoo_dead_candidate_is_still_dead(self, db, tmp_path):
        self._seed(db, [("Srigee DLM", "SRIGEE.NS", None)])
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=(("Srigee DLM", "SRIGEE.NS"),))
        rc = run_finnhub_pass(
            db,
            report_path=rp,
            lookup_fn=self._lookup([FhMatch("SRIGEE.BO", "Srigee DLM Ltd")]),
            verify_fn=self._verify({"SRIGEE.BO": None}),
            fetch_cache=None,
            **self.KW,
        )
        assert rc == 0
        assert "Srigee DLM | still-dead" in rp.read_text("utf-8")

    def test_name_mismatch_is_unverified(self, db, tmp_path):
        # Info payload exists but names the wrong company: never write.
        self._seed(db, [("Gati", "ACLGATI.BO", None)])
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=(("Gati", "ACLGATI.BO"),))
        rc = run_finnhub_pass(
            db,
            report_path=rp,
            lookup_fn=self._lookup([FhMatch("500219.BO", "JAIN IRRIGATION SYSTEMS LTD")]),
            verify_fn=self._verify({"500219.BO": {"longName": "Jain Irrigation Systems Ltd"}}),
            fetch_cache=None,
            **self.KW,
        )
        assert rc == 0
        assert "Gati | unverified" in rp.read_text("utf-8")

    def test_missing_report_returns_2(self, db, tmp_path):
        rc = run_finnhub_pass(
            db,
            report_path=tmp_path / "absent.txt",
            lookup_fn=self._lookup([]),
            verify_fn=self._verify({}),
            fetch_cache=None,
            **self.KW,
        )
        assert rc == 2

    def test_apply_writes_ticker_frontmatter_and_cache(self, db, tmp_path):
        note = self._note(tmp_path, "Piramal Enterprises", "ticker: PIEIL.NS")
        # absolute path: PROJECT_ROOT / <absolute> yields the absolute
        # path itself (pathlib join semantics), keeping the test hermetic
        self._seed(db, [("Piramal Enterprises", "PIEIL.NS", str(note))])
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=(("Piramal Enterprises", "PIEIL.NS"),))
        cache_file = tmp_path / "fetch_cache.json"
        cache_file.write_text(
            json.dumps(
                {
                    "fetched_at": "x",
                    "info_by_name": {},
                    "failures": [["Piramal Enterprises", "PIEIL.NS"]],
                }
            ),
            encoding="utf-8",
        )
        info = {"longName": "Piramal Enterprises Ltd", "industry": "Insurance"}
        rc = run_finnhub_pass(
            db,
            dry_run=False,
            report_path=rp,
            cache_dir=tmp_path,
            delay=0,
            lookup_fn=self._lookup([FhMatch("PEL.NS", "Piramal Enterprises Ltd")]),
            verify_fn=self._verify({"PEL.NS": info}),
            fetch_cache=cache_file,
        )
        assert rc == 0
        assert (
            db.execute("SELECT ticker FROM entities WHERE name = 'Piramal Enterprises'").fetchone()[
                0
            ]
            == "PEL.NS"
        )
        assert "ticker: PEL.NS" in note.read_text(encoding="utf-8")
        assert "ticker: PIEIL.NS" not in note.read_text(encoding="utf-8")
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        assert payload["info_by_name"]["Piramal Enterprises"] == info
        assert payload["failures"] == []

    def test_apply_parity_with_dry_run(self, db, tmp_path):
        # The dry-run writeback table IS what apply writes — same pass,
        # same fakes, only dry_run flips.
        self._seed(db, [("Piramal Enterprises", "PIEIL.NS", None), ("Gati", "ACLGATI.BO", None)])
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=(("Piramal Enterprises", "PIEIL.NS"), ("Gati", "ACLGATI.BO")))
        kw = dict(
            report_path=rp,
            fetch_cache=None,
            lookup_fn=lambda q, c: {
                "Piramal Enterprises": ([FhMatch("PEL.NS", "Piramal Enterprises")], True),
                "Gati": ([FhMatch("500219.BO", "JAIN IRRIGATION")], True),
            }[q],
            verify_fn=self._verify(
                {
                    "PEL.NS": {"longName": "Piramal Enterprises Ltd"},
                    "500219.BO": {"longName": "Jain Irrigation Systems Ltd"},
                }
            ),
            **self.KW,
        )
        rc1 = run_finnhub_pass(db, **kw)  # ty: ignore[invalid-argument-type]
        text = rp.read_text(encoding="utf-8")
        # row-shaped occurrences only (the summary line also says the word)
        would = text.count(" | writeback-candidate | ")
        assert "Gati | unverified" in text
        rc2 = run_finnhub_pass(db, dry_run=False, **kw)  # ty: ignore[invalid-argument-type]
        assert (rc1, rc2) == (0, 0)
        assert (
            db.execute("SELECT ticker FROM entities WHERE name = 'Gati'").fetchone()[0]
            == "ACLGATI.BO"
        )  # unverified never written
        assert would == 1


# --------------------------------------------------------------------------- #
# Terminal classifications (1b): entity_ticker_status                         #
# --------------------------------------------------------------------------- #
class TestTerminalClassifications:
    def _seed_status(self, db, name, status, successor=None):
        db.execute(ENTITY_TICKER_STATUS_DDL)
        db.execute(
            "INSERT INTO entity_ticker_status VALUES (?, ?, ?, 'now')", (name, status, successor)
        )
        db.commit()

    _lookup = staticmethod(lambda matches: lambda q, c: (matches, True))
    _verify = staticmethod(lambda by_ticker: lambda t: by_ticker.get(t))

    def _seed_entities(self, db, rows):
        db.executemany("INSERT OR REPLACE INTO entities VALUES (?, ?, ?, 'company')", rows)
        db.commit()

    def test_gf_pass_skips_terminal_and_reports_it(self, db, tmp_path):
        self._seed_status(db, "Akzo Nobel India", "amalgamated", "JSW Paints")
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(
            rp, issues=(("Srigee DLM", "SRIGEE.NS"), ("Akzo Nobel India", "AKZOINDIA.BO"))
        )
        probed: list[str] = []

        def fetch_fn(slug, _cache):
            probed.append(slug)
            return "", True

        rc = run_googlefinance_pass(
            db,
            report_path=rp,
            cache_dir=tmp_path,
            fetch_fn=fetch_fn,
            search_fn=lambda *_a, **_k: ([], True),
            delay=0,
        )
        assert rc == 0
        text = rp.read_text(encoding="utf-8")
        # Srigee swept; Akzo never resolved, only classified.
        assert "Srigee DLM |" in text
        assert "Akzo Nobel India |" not in text.split("[terminal]")[0].split("[google_finance]")[1]
        assert "[terminal]" in text
        assert "Akzo Nobel India | amalgamated | JSW Paints" in text
        assert not any("AKZOINDIA" in s for s in probed)

    def test_finnhub_pass_skips_terminal(self, db, tmp_path):
        self._seed_status(db, "Hanesbrands", "delisted")
        self._seed_entities(db, [("Hanesbrands", "HBI", None)])
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=(("Hanesbrands", "HBI"),))
        queried: list[str] = []

        def lookup_fn(query, _cache_dir):
            queried.append(query)
            return [], True

        rc = run_finnhub_pass(
            db,
            report_path=rp,
            lookup_fn=lookup_fn,
            verify_fn=lambda _t: None,
            fetch_cache=None,
            cache_dir=tmp_path,
            delay=0,
        )
        assert rc == 0
        text = rp.read_text(encoding="utf-8")
        assert "[finnhub]" in text
        fh_section = text.split("[finnhub]")[1].split("[terminal]")[0]
        assert "Hanesbrands" not in fh_section  # never resolved
        assert queried == []  # never looked up at all

    def test_classify_cli_roundtrip(self, tmp_path, monkeypatch):
        from helpers.maintenance import enrich_relations as er

        dbp = tmp_path / "r.db"
        conn = sqlite3.connect(dbp)
        conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO entities VALUES ('Akzo Nobel India')")
        conn.commit()
        conn.close()
        monkeypatch.setattr(er, "DB_PATH", dbp)
        rc = er.main(["--classify", "Akzo Nobel India", "amalgamated", "JSW", "Paints"])
        assert rc == 0
        conn = sqlite3.connect(dbp)
        row = conn.execute(
            "SELECT status, successor FROM entity_ticker_status "
            "WHERE entity_name = 'Akzo Nobel India'"
        ).fetchone()
        conn.close()
        assert row == ("amalgamated", "JSW Paints")

    def test_classify_rejects_unknown_status(self, tmp_path, monkeypatch):
        from helpers.maintenance import enrich_relations as er

        dbp = tmp_path / "r.db"
        monkeypatch.setattr(er, "DB_PATH", dbp)
        assert er.main(["--classify", "Some Co", "merged-into", "Someone"]) == 2

    def test_unclassify_removes_row(self, tmp_path, monkeypatch):
        from helpers.maintenance import enrich_relations as er

        dbp = tmp_path / "r.db"
        conn = sqlite3.connect(dbp)
        conn.execute(er.ENTITY_TICKER_STATUS_DDL)
        conn.execute("INSERT INTO entity_ticker_status VALUES ('X', 'delisted', NULL, 'now')")
        conn.commit()
        conn.close()
        monkeypatch.setattr(er, "DB_PATH", dbp)
        assert er.main(["--unclassify", "X"]) == 0
        conn = sqlite3.connect(dbp)
        n = conn.execute("SELECT COUNT(*) FROM entity_ticker_status").fetchone()[0]
        conn.close()
        assert n == 0

    def test_ticker_ownership_guard_blocks_sibling(self, db, tmp_path):
        # The Kotak trap (live 2026-08-25): 'Kotak Mahindra Life
        # Insurance' fuzzy-matches the BANK's payload at 0.71 — but the
        # bank already owns KOTAKBANK.NS, so the candidate is ineligible
        # no matter how good the name looks.
        self._seed_entities(
            db,
            [
                ("Kotak Mahindra Life Insurance", "KOTAKLIFE.NS", None),
                ("Kotak Mahindra Bank", "KOTAKBANK.NS", None),
            ],
        )
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=(("Kotak Mahindra Life Insurance", "KOTAKLIFE.NS"),))
        rc = run_finnhub_pass(
            db,
            report_path=rp,
            lookup_fn=self._lookup([FhMatch("KOTAKBANK.NS", "Kotak Mahindra Bank Limited")]),
            verify_fn=self._verify({"KOTAKBANK.NS": {"longName": "Kotak Mahindra Bank Limited"}}),
            fetch_cache=None,
            cache_dir=tmp_path,
            delay=0,
        )
        assert rc == 0
        text = rp.read_text(encoding="utf-8")
        fh_sec = text.split("[finnhub]")[1]
        assert " | writeback-candidate | " not in fh_sec
        assert "Kotak Mahindra Life Insurance | no-candidates" in fh_sec
        assert (
            db.execute(
                "SELECT ticker FROM entities WHERE name = 'Kotak Mahindra Life Insurance'"
            ).fetchone()[0]
            == "KOTAKLIFE.NS"
        )

    def test_stale_report_target_skipped(self, db, tmp_path):
        # Report says TATAMOTORS.NS but the DB already holds TMPV.NS
        # (applied by an earlier run): resolution happens ONCE.
        self._seed_entities(db, [("Tata Motors Passenger Vehicles", "TMPV.NS", None)])
        rp = tmp_path / "relations_report.txt"
        _write_gf_report(rp, issues=(("Tata Motors Passenger Vehicles", "TATAMOTORS.NS"),))
        probed: list[str] = []

        def lookup_fn(query, _cache_dir):
            probed.append(query)
            return [FhMatch("TMPV.NS", "Tata Motors Passenger Vehicles Limited")], True

        rc = run_finnhub_pass(
            db,
            report_path=rp,
            lookup_fn=lookup_fn,
            verify_fn=lambda _t: None,
            fetch_cache=None,
            cache_dir=tmp_path,
            delay=0,
        )
        assert rc == 0
        assert probed == []  # target dropped before any lookup
