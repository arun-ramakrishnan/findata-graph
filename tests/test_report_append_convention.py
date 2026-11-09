"""Report writers append runs at the tail — the chain the TUI reports
panel reads (search_tui_ux_pass B5). One test per writer family; the
gate runner's append is already pinned by tests/test_run_gate_report.py
(including the concurrent double-append)."""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers.misc.search_tui import (
    parse_integrity_runs,
    parse_perf_report,
    report_run_spans,
)


def _integrity_results(ts: str) -> dict:
    """Minimal results dict for DatabaseIntegrityChecker.write_report_file
    (direct-indexed keys only; every other section reads via .get)."""
    return {
        "timestamp": ts,
        "relations": {
            "total": 10,
            "unknown_type": 0,
            "self_loops": 0,
            "orphaned": 0,
            "type_mismatch": 0,
            "sector_membership_endpoint_bad": 0,
            "circular": 0,
            "errors": 0,
        },
        "normalization": {
            "missing": 0,
            "duplicates": {},
            "bad_format": [],
            "errors": 0,
            "file_mismatches": [],
            "orphaned_files": [],
        },
        "by_entity_type": {},
        "invalid_entities_list": [],
    }


def test_integrity_writer_appends_run_chain(tmp_path: Path) -> None:
    """Two write_report_file calls → two # blocks, oldest first; the
    panel parser reads both with ISO-T timestamps normalized."""
    from helpers.misc.database_integrity_check import DatabaseIntegrityChecker

    chk = DatabaseIntegrityChecker(db_path=str(tmp_path / "research.db"), base_path=str(tmp_path))
    chk.write_report_file(_integrity_results("2026-09-20T10:00:00"))
    chk.write_report_file(_integrity_results("2026-09-21T11:00:00"))
    p = tmp_path / "outputs" / "database_integrity_report.md"
    assert p.read_text(encoding="utf-8").count("# FinData") == 2
    assert [r.timestamp for r in parse_integrity_runs(p)] == [
        "2026-09-20 10:00:00",
        "2026-09-21 11:00:00",
    ]


def test_perf_writer_appends_run_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """write_report appends # blocks at the tail; the parser reads the
    chain oldest-first."""
    from tests import run_perf_benchmarks as rpb

    report = tmp_path / "perf_report.md"
    monkeypatch.setattr(rpb, "REPORT", report)
    rpb.write_report([("bench_one", 1.2, "ok", True, 2.0, "")], "2026-09-21 10:00:00", 1.5)
    rpb.write_report([("bench_one", 1.3, "ok", True, 2.0, "")], "2026-09-21 11:00:00", 1.6)
    assert len(report_run_spans(report)) == 2
    blocks = parse_perf_report(report)
    assert [b.steps[0].seconds for b in blocks] == [1.2, 1.3]


def test_metrics_writer_appends_run_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """metrics_report.md gains one # block per run at the tail."""
    pytest.importorskip("yfinance")
    from helpers.maintenance import enrich_from_yfinance as efy

    report = tmp_path / "metrics_report.md"
    monkeypatch.setattr(efy, "REPORT_PATH", report)
    monkeypatch.setattr(efy, "PROJECT_ROOT", tmp_path)  # write_report logs relative_to
    efy.write_report([], [], 0, 0, 0, 0, 1.0, dry_run=True)
    efy.write_report([], [], 3, 1, 2, 0, 2.0, dry_run=False)
    text = report.read_text(encoding="utf-8")
    chunks = text.split("# yfinance Enrichment Report")
    assert len(chunks) == 3  # 2 runs at the tail
    assert "DRY-RUN" in chunks[1]
    assert "APPLIED" in chunks[2]


# ── gate_output_fidelity S4: writers retain, the index can follow ──────
# The three lanes that never ride run_gate_report used to keep only a tail in
# their own report, so `gate_query output --leg L` could reach none of them.
# These drive the REAL writers end to end (write -> **Artifacts:** -> logs on
# disk with the name the reader derives) rather than a hand-built fixture.


def test_integrity_writer_retains_one_log_per_check(tmp_path: Path) -> None:
    from helpers.misc.database_integrity_check import DatabaseIntegrityChecker

    base = tmp_path
    (base / "outputs").mkdir()
    chk = DatabaseIntegrityChecker(db_path=str(base / "research.db"), base_path=str(base))
    results = _integrity_results("2026-11-09T12:00:00")
    results["ended"] = "2026-11-09T12:00:09"
    results["elapsed_s"] = 9.0
    out = chk.write_report_file(results)

    text = out.read_text(encoding="utf-8")
    head = text.splitlines()[:8]
    artifacts = next(
        (ln.split("**Artifacts:**")[1].strip().split()[0] for ln in head if "**Artifacts:**" in ln),
        None,
    )
    assert artifacts and artifacts.startswith(".artifacts/integrity/"), head
    assert any(ln.startswith("**Exit:**") for ln in head), head

    run_dir = base / "outputs" / artifacts
    # the reader derives <gate>.<leg>.log, and integrity legs are the section
    # headings ("RELATIONS"), not the registry keys ("relations")
    log = run_dir / "integrity.RELATIONS.log"
    assert log.is_file(), sorted(p.name for p in run_dir.iterdir())
    assert "-> errors=0" in log.read_text(encoding="utf-8")
    assert (run_dir / "integrity.json").is_file()


def test_verify_writer_retains_one_log_per_bucket(tmp_path: Path) -> None:
    from helpers.validators.verify_notes import NotesVerifier

    (tmp_path / "outputs").mkdir()
    verifier = NotesVerifier(project_root=tmp_path)
    verifier.issues = {"yaml_structure": [{"file": "a.md", "description": "boom"}]}
    verifier.warnings = {"tag_format": [{"file": "b.md", "description": "warn"}]}
    verifier.stats["total_files"] = 3
    report = tmp_path / "outputs" / "verify_notes_report.md"
    verifier.generate_report(str(report))

    text = report.read_text(encoding="utf-8")
    head = text.splitlines()[:8]
    artifacts = next(
        (ln.split("**Artifacts:**")[1].strip().split()[0] for ln in head if "**Artifacts:**" in ln),
        None,
    )
    assert artifacts and artifacts.startswith(".artifacts/verify/"), head
    assert "**Exit:** 1" in head

    run_dir = tmp_path / "outputs" / artifacts
    err_log = run_dir / "verify.yaml_structure.log"
    warn_log = run_dir / "verify.tag_format.log"
    assert err_log.is_file() and warn_log.is_file()
    assert "a.md: boom" in err_log.read_text(encoding="utf-8")
    assert "b.md: warn" in warn_log.read_text(encoding="utf-8")
