"""ticker_hygiene S1 tests — every network seam monkeypatched at module attrs.

Covers: the scrip predicate, candidate selection, the four resolution
statuses, stage resume, the guarded apply, the ``## ticker_issues`` report
shape (must stay parseable by ``enrich_relations.load_gf_targets``), and
the CLI modes (dry-run / fetch-only / apply-only / mutual exclusion).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from helpers.core import db as core_db
from helpers.maintenance import ticker_hygiene as th

# --- fixtures ----------------------------------------------------------------

_ROWS = [
    ("Patel Engineering", "company", "531120.BO"),
    ("BF Utilities", "company", "532430.BO"),
    ("Reliance Industries", "company", "RELIANCE.NS"),
    ("Samsung SDI Co", "company", "006400.KS"),
    ("5paisa Capital", "company", "5PAISA.NS"),
    ("No Ticker Co", "company", None),
    ("Some Institution", "institution", "531121.BO"),
]


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = core_db.connect(str(tmp_path / "t.db"))
    conn.execute(
        "CREATE TABLE entities ("
        "name TEXT PRIMARY KEY, entity_type TEXT, ticker TEXT, last_updated TEXT)"
    )
    conn.executemany("INSERT INTO entities (name, entity_type, ticker) VALUES (?, ?, ?)", _ROWS)
    conn.commit()
    return conn


def _fake_search(results: dict[str, tuple[str, dict] | tuple[None, None]]):
    def _search(name: str):
        return results.get(name, (None, None))

    return _search


# --- pure predicate ----------------------------------------------------------


def test_is_scrip_ticker_truth_table():
    assert th.is_scrip_ticker("531120.BO")
    assert th.is_scrip_ticker("544364.BO")
    assert th.is_scrip_ticker("500126.NS")  # the 5 numeric .NS rows are in scope too
    assert not th.is_scrip_ticker("PATELENG.NS")
    assert not th.is_scrip_ticker("TCS.BO")
    assert not th.is_scrip_ticker("5PAISA.NS")  # digit prefix, but not all digits
    assert not th.is_scrip_ticker("3MINDIA.NS")
    assert not th.is_scrip_ticker("006400.KS")  # foreign numerics ARE Yahoo symbols
    assert not th.is_scrip_ticker("1810.HK")
    assert not th.is_scrip_ticker("2222.SR")
    assert not th.is_scrip_ticker("")
    assert not th.is_scrip_ticker(None)  # type: ignore[arg-type]
    assert not th.is_scrip_ticker("531120")  # no exchange suffix — not a ticker form


def test_select_candidates_filters_scrip_only(tmp_path):
    conn = _db(tmp_path)
    got = th.select_candidates(conn)
    assert sorted(got) == [
        ("BF Utilities", "532430.BO"),
        ("Patel Engineering", "531120.BO"),
    ]
    conn.close()


# --- resolve_one statuses ----------------------------------------------------


def test_resolve_one_resolved(tmp_path, monkeypatch):
    monkeypatch.setattr(
        th,
        "search_ticker",
        _fake_search(
            {"Patel Engineering": ("PATELENG.NS", {"longName": "Patel Engineering Limited"})}
        ),
    )
    monkeypatch.setattr(th, "identity_ok", lambda name, info, aliases=None: True)
    rec = th.resolve_one("Patel Engineering", "531120.BO")
    assert rec["status"] == "resolved"
    assert rec["new_ticker"] == "PATELENG.NS"
    assert rec["yf_name"] == "Patel Engineering Limited"


def test_resolve_one_unresolved(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "search_ticker", _fake_search({}))
    rec = th.resolve_one("BF Utilities", "532430.BO")
    assert rec["status"] == "unresolved"
    assert rec["new_ticker"] is None


def test_resolve_one_guard_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(
        th,
        "search_ticker",
        _fake_search({"BF Utilities": ("BFUTIL.NS", {"longName": "Bharat Fritz Werner"})}),
    )
    monkeypatch.setattr(th, "identity_ok", lambda name, info, aliases=None: False)
    rec = th.resolve_one("BF Utilities", "532430.BO")
    assert rec["status"] == "guard_refused"
    assert rec["new_ticker"] == "BFUTIL.NS"  # kept for the report, never applied


def test_resolve_one_foreign_symbol(tmp_path, monkeypatch):
    monkeypatch.setattr(
        th,
        "search_ticker",
        _fake_search({"Some Co": ("MMM", {"longName": "3M Company"})}),
    )
    monkeypatch.setattr(th, "identity_ok", lambda name, info, aliases=None: True)
    rec = th.resolve_one("Some Co", "531120.BO")
    assert rec["status"] == "foreign_symbol"
    assert rec["new_ticker"] == "MMM"


# --- stage -------------------------------------------------------------------


def test_stage_roundtrip_and_resume(tmp_path):
    stage = tmp_path / "stage.jsonl"
    rec_a = {
        "name": "A",
        "old_ticker": "1.BO",
        "new_ticker": "A.NS",
        "yf_name": "A",
        "status": "resolved",
    }
    rec_b = {
        "name": "B",
        "old_ticker": "2.BO",
        "new_ticker": None,
        "yf_name": None,
        "status": "unresolved",
    }
    assert th._append_stage(stage, [rec_a]) == 1
    assert th._append_stage(stage, [rec_b]) == 1  # extend-only, second run appends
    loaded = th._load_stage(stage)
    assert set(loaded) == {"A", "B"}
    assert loaded["A"]["status"] == "resolved"
    assert th._load_stage(tmp_path / "missing.jsonl") == {}


def test_stage_skips_corrupt_line(tmp_path):
    stage = tmp_path / "stage.jsonl"
    stage.write_text('{"name": "A", "old_ticker": "1.BO"}\nnot json\n', encoding="utf-8")
    assert set(th._load_stage(stage)) == {"A"}


# --- guarded apply -----------------------------------------------------------


def test_apply_resolved_guarded(tmp_path):
    conn = _db(tmp_path)
    records = [
        {
            "name": "Patel Engineering",
            "old_ticker": "531120.BO",
            "new_ticker": "PATELENG.NS",
            "yf_name": "x",
            "status": "resolved",
        },
        # BF Utilities' ticker was fixed by hand after staging — must be skipped
        {
            "name": "BF Utilities",
            "old_ticker": "532430.BO",
            "new_ticker": "BFUTIL.NS",
            "yf_name": "x",
            "status": "resolved",
        },
        # refused rows never touch the store even if present
        {
            "name": "Reliance Industries",
            "old_ticker": "RELIANCE.NS",
            "new_ticker": "RI.NS",
            "yf_name": "x",
            "status": "guard_refused",
        },
    ]
    conn.execute("UPDATE entities SET ticker = 'BFUTILITES.NS' WHERE name = 'BF Utilities'")
    conn.commit()
    applied, skipped = th.apply_resolved(conn, records, now="2026-10-16T00:00:00+00:00")
    assert (applied, skipped) == (1, 1)
    row = conn.execute(
        "SELECT ticker, last_updated FROM entities WHERE name = 'Patel Engineering'"
    ).fetchone()
    assert tuple(row) == ("PATELENG.NS", "2026-10-16T00:00:00+00:00")
    kept = conn.execute("SELECT ticker FROM entities WHERE name = 'BF Utilities'").fetchone()
    assert kept[0] == "BFUTILITES.NS"  # untouched — the guard refused the clobber
    conn.close()


# --- report shape ------------------------------------------------------------


def test_report_ticker_issues_shape(tmp_path):
    report = tmp_path / "report.md"
    records = [
        {
            "name": "Patel Engineering",
            "old_ticker": "531120.BO",
            "new_ticker": "PATELENG.NS",
            "yf_name": "Patel Engineering Limited",
            "status": "resolved",
        },
        {
            "name": "BF Utilities",
            "old_ticker": "532430.BO",
            "new_ticker": None,
            "yf_name": None,
            "status": "unresolved",
        },
    ]
    th.append_report(report, records, mode="dry-run")
    text = report.read_text(encoding="utf-8")

    # the load_gf_targets parse: rows under '## ticker_issues', two cells,
    # second cell non-empty
    section = None
    issues: list[tuple[str, str]] = []
    for line in text.splitlines():
        header = re.match(r"^##\s+(\w+)", line)
        if header:
            section = header.group(1)
            continue
        if section == "ticker_issues":
            m = re.match(r"^\|\s*(.+?)\s*\|\s*(\S+)\s*\|$", line)
            name = m.group(1).strip() if m else ""
            # same exclusions as load_gf_targets: header, sentinel, dash rows
            if m and name not in ("Name", "(none)") and not set(name) <= {"-", "|", " "}:
                issues.append((name, m.group(2).strip()))
    assert issues == [("BF Utilities", "532430.BO")]

    # no issues -> the (none) sentinel row must NOT parse as an issue
    th.append_report(report, records[:1], mode="dry-run")
    last_block = text = report.read_text(encoding="utf-8").split("# ticker_hygiene")[-1]
    m_none = re.search(r"## ticker_issues.*?\| \(none\) \|  \|", last_block, re.DOTALL)
    assert m_none, "empty issues table must carry the (none) sentinel row"


# --- CLI modes ---------------------------------------------------------------


def test_cli_dry_run_resolves_but_never_writes(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    conn.close()
    db = tmp_path / "t.db"
    stage, report = tmp_path / "stage.jsonl", tmp_path / "report.md"
    monkeypatch.setattr(
        th,
        "search_ticker",
        _fake_search(
            {"Patel Engineering": ("PATELENG.NS", {"longName": "Patel Engineering Limited"})}
        ),
    )
    monkeypatch.setattr(th, "identity_ok", lambda name, info, aliases=None: True)
    rc = th.main(["--db", str(db), "--stage", str(stage), "--report", str(report), "--sleep", "0"])
    assert rc == 0
    conn = core_db.connect(str(db))
    tickers = dict(conn.execute("SELECT name, ticker FROM entities").fetchall())
    conn.close()
    assert tickers["Patel Engineering"] == "531120.BO"  # dry-run: store untouched
    assert stage.exists() and "PATELENG.NS" in stage.read_text(encoding="utf-8")
    assert "## ticker_issues" in report.read_text(encoding="utf-8")


def test_cli_fetch_only_skips_staged_names(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    conn.close()
    db, stage, report = tmp_path / "t.db", tmp_path / "stage.jsonl", tmp_path / "report.md"
    calls: list[str] = []

    def _counting_search(name: str):
        calls.append(name)
        return "X.NS", {"longName": name}

    monkeypatch.setattr(th, "search_ticker", _counting_search)
    monkeypatch.setattr(th, "identity_ok", lambda name, info, aliases=None: True)
    common = ["--db", str(db), "--stage", str(stage), "--report", str(report), "--sleep", "0"]
    assert th.main(["--fetch-only", *common]) == 0
    first = len(calls)
    assert first == 2  # both scrip candidates
    assert th.main(["--fetch-only", *common]) == 0
    assert len(calls) == first  # staged names skipped on re-run (resume)
    lines = [ln for ln in stage.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2  # extend-only: re-run appended nothing


def test_cli_apply_only_applies_staged(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    conn.close()
    db, stage, report = tmp_path / "t.db", tmp_path / "stage.jsonl", tmp_path / "report.md"
    stage.write_text(
        '{"name": "Patel Engineering", "old_ticker": "531120.BO", "new_ticker": "PATELENG.NS",'
        ' "yf_name": "Patel Engineering Limited", "status": "resolved"}\n',
        encoding="utf-8",
    )
    rc = th.main(
        ["--apply-only", "--apply", "--db", str(db), "--stage", str(stage), "--report", str(report)]
    )
    assert rc == 0
    conn = core_db.connect(str(db))
    row = conn.execute("SELECT ticker FROM entities WHERE name = 'Patel Engineering'").fetchone()
    conn.close()
    assert row[0] == "PATELENG.NS"


def test_cli_apply_only_without_stage_fails(tmp_path):
    rc = th.main(
        [
            "--apply-only",
            "--db",
            str(tmp_path / "t.db"),
            "--stage",
            str(tmp_path / "none.jsonl"),
            "--report",
            str(tmp_path / "r.md"),
        ]
    )
    assert rc == 1


def test_cli_mutually_exclusive(tmp_path):
    base = ["--db", str(tmp_path / "t.db"), "--stage", "s", "--report", "r"]
    assert th.main(["--fetch-only", "--apply-only", *base]) == 2
    assert th.main(["--fetch-only", "--apply", *base]) == 2


def test_cli_company_filter_and_limit(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    conn.close()
    db, stage, report = tmp_path / "t.db", tmp_path / "stage.jsonl", tmp_path / "report.md"
    monkeypatch.setattr(th, "search_ticker", _fake_search({}))
    rc = th.main(
        [
            "--company",
            "patel",
            "--db",
            str(db),
            "--stage",
            str(stage),
            "--report",
            str(report),
            "--sleep",
            "0",
        ]
    )
    assert rc == 0
    lines = [ln for ln in stage.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1 and "Patel Engineering" in lines[0]


def test_cli_summary_line(tmp_path, monkeypatch, capsys):
    conn = _db(tmp_path)
    conn.close()
    db, stage, report = tmp_path / "t.db", tmp_path / "stage.jsonl", tmp_path / "report.md"
    monkeypatch.setattr(
        th,
        "search_ticker",
        _fake_search(
            {
                "Patel Engineering": ("PATELENG.NS", {"longName": "Patel Engineering Limited"}),
            }
        ),
    )
    monkeypatch.setattr(th, "identity_ok", lambda name, info, aliases=None: True)
    assert (
        th.main(["--db", str(db), "--stage", str(stage), "--report", str(report), "--sleep", "0"])
        == 0
    )
    out = capsys.readouterr().out
    assert "candidates=2" in out
    assert "resolved=1" in out
    assert "unresolved=1" in out
    assert "applied=0" in out


# --- tier2 (BSE scrip match) + retry-failed ----------------------------------


def test_resolve_tier2_resolves_via_scrip_match(monkeypatch):
    from helpers.maintenance.exchange_search import BseMatch

    monkeypatch.setattr(
        th,
        "bse_search_cached",
        lambda q, cd, **kw: (
            [
                BseMatch(
                    scrip="544742", symbol="SAIPARENT", name="Sai Parenteral's Limited", isin=""
                )
            ],
            False,
        ),
    )
    monkeypatch.setattr(
        th,
        "get_basic_info",
        lambda sym: {"longName": "Sai Parenteral's Limited"} if sym == "SAIPARENT.NS" else None,
    )
    monkeypatch.setattr(th, "identity_ok", lambda name, info, aliases=None: True)
    rec = th.resolve_tier2("Sai Parenterals", "544742.BO")
    assert rec["status"] == "resolved"
    assert rec["new_ticker"] == "SAIPARENT.NS"
    assert rec["via"] == "tier2"


def test_resolve_tier2_no_bse_row(monkeypatch):
    monkeypatch.setattr(th, "bse_search_cached", lambda q, cd, **kw: ([], False))
    rec = th.resolve_tier2("Some Co", "531120.BO")
    assert rec["status"] == "tier2_no_bse_row"
    assert rec["new_ticker"] is None


def test_resolve_tier2_rename_refused(monkeypatch):
    from helpers.maintenance.exchange_search import BseMatch

    monkeypatch.setattr(
        th,
        "bse_search_cached",
        lambda q, cd, **kw: (
            [BseMatch(scrip="517556", symbol="PVP", name="Evervie Health Limited", isin="")],
            False,
        ),
    )
    monkeypatch.setattr(th, "get_basic_info", lambda sym: {"longName": "Evervie Health Limited"})
    monkeypatch.setattr(th, "identity_ok", lambda name, info, aliases=None: False)
    rec = th.resolve_tier2("PVP Ventures", "517556.BO")
    assert rec["status"] == "tier2_refused"
    assert rec["new_ticker"] is None  # a rename is evidence, never a write
    assert "Evervie" in str(rec["yf_name"])


def test_cli_retry_failed_refetches_only_failures(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    conn.close()
    db, stage, report = tmp_path / "t.db", tmp_path / "stage.jsonl", tmp_path / "report.md"
    stage.write_text(
        '{"name": "Patel Engineering", "old_ticker": "531120.BO", "new_ticker": "PATELENG.NS",'
        ' "yf_name": "Patel Engineering Limited", "status": "resolved"}\n'
        '{"name": "BF Utilities", "old_ticker": "532430.BO", "new_ticker": null,'
        ' "yf_name": null, "status": "unresolved"}\n',
        encoding="utf-8",
    )
    calls: list[str] = []

    def _counting_search(name: str):
        calls.append(name)
        return "X.NS", {"longName": name}

    monkeypatch.setattr(th, "search_ticker", _counting_search)
    monkeypatch.setattr(th, "identity_ok", lambda name, info, aliases=None: True)
    rc = th.main(
        [
            "--retry-failed",
            "--db",
            str(db),
            "--stage",
            str(stage),
            "--report",
            str(report),
            "--sleep",
            "0",
        ]
    )
    assert rc == 0
    assert calls == ["BF Utilities"]  # resolved row skipped; failed row retried
    loaded = th._load_stage(stage)
    assert loaded["BF Utilities"]["status"] == "resolved"  # later row supersedes


def test_cli_tier2_with_apply_only_rejected(tmp_path):
    rc = th.main(
        ["--tier2", "--apply-only", "--db", str(tmp_path / "t.db"), "--stage", "s", "--report", "r"]
    )
    assert rc == 2


# --- tier3 (kite direct-symbol probe) ----------------------------------------


def test_resolve_tier3_resolved_via_direct_symbol(monkeypatch):
    monkeypatch.setattr(
        th,
        "get_basic_info",
        lambda sym: {"longName": "BF Utilities Limited"} if sym == "BFUTIL.BO" else None,
    )
    monkeypatch.setattr(th, "identity_ok", lambda name, info, aliases=None: True)
    rec = th.resolve_tier3("BF Utilities", "532430.BO", {"532430": "BFUTIL"})
    assert rec["status"] == "resolved"
    assert rec["new_ticker"] == "BFUTIL.BO"
    assert rec["via"] == "tier3"


def test_resolve_tier3_rename_evidence(monkeypatch):
    monkeypatch.setattr(
        th,
        "get_basic_info",
        lambda sym: {"longName": "Evervie Health Limited"} if sym == "PVP.NS" else None,
    )
    monkeypatch.setattr(th, "identity_ok", lambda name, info, aliases=None: False)
    rec = th.resolve_tier3("PVP Ventures", "517556.BO", {"517556": "PVP"})
    assert rec["status"] == "tier3_rename"
    assert rec["new_ticker"] is None  # rename evidence, never a write
    assert rec["yf_name"] == "Evervie Health Limited"


def test_resolve_tier3_dead_and_no_kite(monkeypatch):
    monkeypatch.setattr(th, "get_basic_info", lambda sym: None)
    rec = th.resolve_tier3("Some Co", "531120.BO", {"531120": "SOMECO"})
    assert rec["status"] == "tier3_dead"  # kite known, no live Yahoo listing
    rec2 = th.resolve_tier3("Other Co", "599999.BO", {})
    assert rec2["status"] == "tier3_no_kite"


def test_cli_tier3_with_apply_only_rejected(tmp_path):
    rc = th.main(
        ["--tier3", "--apply-only", "--db", str(tmp_path / "t.db"), "--stage", "s", "--report", "r"]
    )
    assert rc == 2
