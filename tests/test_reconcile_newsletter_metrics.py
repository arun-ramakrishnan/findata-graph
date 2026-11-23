"""Targeted tests for helpers/maintenance/reconcile_newsletter_metrics.py.

The driver is engine-agnostic (portable LIKE/params SQL); fixtures run on
a plain sqlite store shaped like the canonical company_metrics table.
Covers the §10.3 ladder rungs, verdict thresholds, the consolidated
nearest-pick, and sidecar apply idempotency + pruning.
"""

from __future__ import annotations

import sqlite3

import pytest

from helpers.maintenance import reconcile_newsletter_metrics as rnm

CM_DDL = """
CREATE TABLE company_metrics (
    id INTEGER PRIMARY KEY, entity TEXT, metric_label TEXT, value_raw TEXT,
    value_num REAL, unit TEXT, period TEXT, as_of_edition TEXT,
    source_quote TEXT, source_ref TEXT, properties TEXT, created_at TIMESTAMP,
    agent_id TEXT, source_tier TEXT, consolidated INTEGER
)
"""


def _metric(rid, entity, label, value, unit, period, *, nl=False, cons=None):
    return (
        rid,
        entity,
        label,
        "raw",
        value,
        unit,
        period,
        "Some_Edition" if nl else None,
        "quote" if nl else None,
        "derive:metrics:Ed:1" if nl else "nse-xbrl-url",
        '{"via": "nse-xbrl"}' if not nl else "{}",
        "2026-11-23",
        None,
        "external",
        cons,
    )


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute(CM_DDL)
    # Filing ground truth: Reliance FY27Q1 cons 311,850 / standalone 166,013 crore.
    c.executemany(
        "INSERT INTO company_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            _metric(1, "Reliance Industries", "q_revenue", 311850.0, "crore_inr", "FY27Q1", cons=1),
            _metric(2, "Reliance Industries", "q_revenue", 166013.0, "crore_inr", "FY27Q1", cons=0),
            _metric(3, "Reliance Industries", "q_pat", 30648.0, "crore_inr", "FY27Q1", cons=1),
        ],
    )
    c.commit()
    yield c
    c.close()


class TestParsePeriod:
    @pytest.mark.parametrize(
        "raw,want",
        [
            ("Q3 FY26", "FY26Q3"),
            ("Q3FY26", "FY26Q3"),
            ("FY26 Q3", "FY26Q3"),
            ("FY26Q3", "FY26Q3"),
            ("Q1 FY2026", "FY26Q1"),
            ("FY26", None),  # year-only refuses to anchor
            ("FY2025", None),
            ("December 2025", None),
            (None, None),
            ("", None),
        ],
    )
    def test_cases(self, raw, want):
        assert rnm.parse_period(raw) == want


class TestLabelAndUnit:
    @pytest.mark.parametrize(
        "raw,want",
        [
            ("revenue", "q_revenue"),
            ("Turnover", "q_revenue"),
            ("topline", "q_revenue"),
            ("pat", "q_pat"),
            ("net profit", "q_pat"),
            ("ebitda", "q_ebitda"),
            ("guidance", None),
            (None, None),
        ],
    )
    def test_map_label(self, raw, want):
        assert rnm.map_label(raw) == want

    @pytest.mark.parametrize(
        "value,unit,want",
        [
            (300000.0, "crore", 300000.0),
            (3.1, "lakh", 0.031),  # 1 lakh = 0.01 crore
            (12.0, "bn_usd", None),
            (5.0, None, None),
            (None, "crore", None),
        ],
    )
    def test_to_crore(self, value, unit, want):
        assert rnm.to_crore(value, unit) == pytest.approx(want)


class TestReconcile:
    def test_anchored_prefers_closer_consolidated(self, conn):
        conn.execute(
            "INSERT INTO company_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _metric(10, "Reliance Industries", "revenue", 300000.0, "crore", "Q1 FY27", nl=True),
        )
        (r,) = rnm.reconcile(conn)
        assert r.verdict == "anchored"
        assert r.filing_id == 1 and r.consolidated == 1  # 3.9% vs standalone's 45%
        assert r.reason is None and r.delta_pct == pytest.approx(0.0388, abs=1e-3)

    def test_anchored_standalone_when_closer(self, conn):
        conn.execute(
            "INSERT INTO company_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _metric(10, "Reliance Industries", "revenue", 170000.0, "crore", "Q1FY27", nl=True),
        )
        (r,) = rnm.reconcile(conn)
        assert r.verdict == "anchored"
        assert r.filing_id == 2 and r.consolidated == 0

    def test_suspect_beyond_threshold(self, conn):
        # the lakh-crore compound misread shape: 200 stored vs 311,850 filed
        conn.execute(
            "INSERT INTO company_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _metric(10, "Reliance Industries", "revenue", 200.0, "crore", "Q1 FY27", nl=True),
        )
        (r,) = rnm.reconcile(conn)
        assert r.verdict == "suspect"
        assert r.reason == "beyond_threshold"
        # nearest-candidate rule: standalone (99.88% off) edges consolidated
        # (99.94%) — both far beyond the band, so the verdict is suspect
        # regardless of which filing row "won"
        assert r.filing_id == 2

    def test_unmatched_reasons(self, conn):
        rows = [
            (10, "Reliance Industries", "revenue", 300000.0, "crore", None),  # period_missing
            (11, "Reliance Industries", "revenue", 300000.0, "crore", "FY27"),  # ambiguous
            (12, "Reliance Industries", None, 300000.0, "crore", "Q1 FY27"),  # label_missing
            (13, "Reliance Industries", "guidance", 300000.0, "crore", "Q1 FY27"),  # unmapped
            (14, "Reliance Industries", "revenue", 300000.0, "bn_usd", "Q1 FY27"),  # unit
            (15, "Acme Ltd", "revenue", 300000.0, "crore", "Q1 FY27"),  # no_filing_rows
        ]
        for rid, ent, label, value, unit, period in rows:
            conn.execute(
                "INSERT INTO company_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                _metric(rid, ent, label, value, unit, period, nl=True),
            )
        got = {r.id: (r.verdict, r.reason) for r in rnm.reconcile(conn)}
        assert got[10] == ("unmatched", "period_missing")
        assert got[11] == ("unmatched", "period_ambiguous")
        assert got[12] == ("unmatched", "label_missing")
        assert got[13] == ("unmatched", "label_unmapped")
        assert got[14] == ("unmatched", "unit_unmapped")
        assert got[15] == ("unmatched", "no_filing_rows")


class TestApply:
    def test_apply_replaces_and_prunes(self, conn):
        conn.execute(
            "INSERT INTO company_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _metric(10, "Reliance Industries", "revenue", 300000.0, "crore", "Q1 FY27", nl=True),
        )
        rows = rnm.reconcile(conn)
        assert rnm.apply_verdicts(conn, rows) == 1
        assert conn.execute("SELECT COUNT(*) FROM metric_reconciliation").fetchone()[0] == 1
        # rerun replaces in place (same id set, no duplicates)
        rows2 = rnm.reconcile(conn)
        rnm.apply_verdicts(conn, rows2)
        assert conn.execute("SELECT COUNT(*) FROM metric_reconciliation").fetchone()[0] == 1
        # a metric id no longer in the source set is pruned on the next apply
        conn.execute("DELETE FROM company_metrics WHERE id = 10")
        rnm.apply_verdicts(conn, rnm.reconcile(conn))
        assert conn.execute("SELECT COUNT(*) FROM metric_reconciliation").fetchone()[0] == 0

    def test_apply_never_touches_company_metrics(self, conn):
        before = conn.execute(
            "SELECT id, entity, metric_label, value_num, period FROM company_metrics ORDER BY id"
        ).fetchall()
        conn.execute(
            "INSERT INTO company_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _metric(10, "Reliance Industries", "revenue", 300000.0, "crore", "Q1 FY27", nl=True),
        )
        rnm.apply_verdicts(conn, rnm.reconcile(conn))
        after = conn.execute(
            "SELECT id, entity, metric_label, value_num, period FROM company_metrics "
            "WHERE id < 10 ORDER BY id"
        ).fetchall()
        assert before == after  # filing ground truth is read-only to the driver


class TestReport:
    def test_report_renders_sections(self, conn, tmp_path):
        conn.execute(
            "INSERT INTO company_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _metric(10, "Reliance Industries", "revenue", 300000.0, "crore", "Q1 FY27", nl=True),
        )
        conn.execute(
            "INSERT INTO company_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _metric(11, "Reliance Industries", "revenue", 200.0, "crore", "Q1 FY27", nl=True),
        )
        rows = rnm.reconcile(conn)
        out = tmp_path / "report.md"
        rnm.write_report(rows, out, 0.10)
        text = out.read_text(encoding="utf-8")
        assert "## Suspect (beyond threshold)" in text
        assert "## Anchored" in text
        assert "Reliance Industries" in text
