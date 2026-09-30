#!/usr/bin/env python3
"""Review tooling tests — the OCR support CLIs `make review-patch` drives.

The two modules had zero unit coverage while carrying the review loop's
bookkeeping (bake-off finding class 9, the corpus's only Medium,
review_findings_collation.md S4): review_freshness.py is the ledger writer
the freshness check depends on (a silent mis-write corrupts the very
bookkeeping reviews cite), review_selection.py maps the stgit stack to
reviewable families. Hermetic by construction — the ledger is redirected to
tmp_path and git/stg are faked, so nothing here touches the real stack or
`memory/data/review-freshness.json`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import helpers.misc.review_freshness as rf
import helpers.misc.review_selection as rs
import pytest

FP_TEXT = "diff-text-A\n"
FP = "5ab2f84a6c4d3e91"  # not the real hash — asserted via recomputation below


def _proc(stdout="", returncode=0, stderr=""):
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """Redirect the ledger into tmp and fake the two subprocess surfaces."""
    path = tmp_path / "ledger.json"
    monkeypatch.setattr(rf, "LEDGER", path)
    monkeypatch.setattr(rf, "_diff_text", lambda stack: FP_TEXT)
    calls = []

    def fake_run(argv, **_kw):
        calls.append(argv)
        if argv[0] == "stg":
            return _proc(stdout="patch-one\npatch-two\n")
        return _proc()

    monkeypatch.setattr(rf.subprocess, "run", fake_run)
    return path


class TestReviewFreshness:
    def test_record_then_status_roundtrip_and_upsert(self, ledger):
        import hashlib

        fp = hashlib.sha256(FP_TEXT.encode()).hexdigest()[:16]
        assert rf.main(["--record", "--leg", "delegation", "--note", "test verdict"]) == 0
        rows = json.loads(ledger.read_text())
        assert len(rows) == 1 and rows[0]["fingerprint"] == fp
        assert rows[0]["scope"] == "HEAD~1..HEAD" and rows[0]["legs"] == ["delegation"]
        # same diff re-recorded with another leg UPSERTS, never duplicates
        assert rf.main(["--record", "--leg", "managed"]) == 0
        rows = json.loads(ledger.read_text())
        assert len(rows) == 1 and rows[0]["legs"] == ["delegation", "managed"]
        state, row, scope = rf.status(1)
        assert state == "FRESH" and row["fingerprint"] == fp and scope == "HEAD~1..HEAD"

    def test_content_change_goes_stale_same_scope(self, ledger, monkeypatch):
        rf.main(["--record"])
        monkeypatch.setattr(rf, "_diff_text", lambda stack: "diff-text-B\n")
        state, row, scope = rf.status(1)
        assert state == "STALE" and scope == "HEAD~1..HEAD" and row["legs"] == ["delegation"]

    def test_unreviewed_when_no_row(self, ledger):
        assert rf.status(1) == ("UNREVIEWED", None, "HEAD~1..HEAD")

    def test_stack_zero_refuses_the_lying_alias(self, ledger):
        with pytest.raises(RuntimeError, match="stack 0"):
            rf.fingerprint(0)

    def test_stack_beyond_applied_refuses_foreign_history(self, ledger, monkeypatch):
        # only ONE patch applied; --stack 2 would reach past the stack
        monkeypatch.setattr(
            rf.subprocess,
            "run",
            lambda argv, **_kw: _proc(stdout="patch-one\n") if argv[0] == "stg" else _proc(),
        )
        with pytest.raises(RuntimeError, match="exceeds the applied stack"):
            rf.fingerprint(2)

    def test_stg_failure_raises_cleanly_not_bogus_stack_depth(self, ledger, monkeypatch):
        # c83a11d9 fix: stg failure used to yield empty stdout and the lying
        # "exceeds stack" error; it must name the stg failure instead.
        monkeypatch.setattr(
            rf.subprocess,
            "run",
            lambda argv, **_kw: (
                _proc(returncode=1, stderr="error: no patches applied")
                if argv[0] == "stg"
                else _proc()
            ),
        )
        with pytest.raises(RuntimeError, match="stg series failed"):
            rf.fingerprint(1)

    def test_corrupt_ledger_names_itself(self, ledger, monkeypatch):
        ledger.write_text("{not json")
        with pytest.raises(SystemExit, match="ledger corrupt"):
            rf.status(1)


class TestReviewSelection:
    def test_ref_args_stack_one_is_single_commit(self, monkeypatch):
        sha = "0123456789abcdef" * 2 + "01234567"
        monkeypatch.setattr(rs.subprocess, "run", lambda argv, **_kw: _proc(stdout=sha + "\n"))
        assert rs._ref_args(1) == ["--commit", sha]

    def test_ref_args_stack_n_is_a_range(self):
        assert rs._ref_args(3) == ["--from", "HEAD~3", "--to", "HEAD"]

    def test_families_cover_every_product_family_with_prefixes(self):
        includes, excludes = rs._families()
        # hardcoded teeth stay in the union even if rule.json drops them
        for family in ("tests/", "Mojo/src/", "Mojo/tests/"):
            assert family in includes
        # prefix extends to the first WILDCARD segment: the Mojo collapse trap
        for pattern in ("tests/**/*.py", "Mojo/src/**/*.mojo", "doc/procedures/**"):
            segs = []
            for seg in pattern.split("/"):
                if "*" in seg:
                    break
                segs.append(seg)
            assert "/".join(segs) + "/" in includes

    def test_rule_excluded_matches_globs_and_dir_forms(self):
        globs = ["static/**/*.bundle.js", "Mojo/vendor/**", "findata/**"]
        assert rs._rule_excluded("static/bundles/app.bundle.js", globs)
        assert rs._rule_excluded("Mojo/vendor/lib/x.py", globs)
        assert not rs._rule_excluded("helpers/misc/review_selection.py", globs)
        # fnmatch has no path semantics: `**` is just `*`, so a DIRECTLY
        # nested file ("static/app.bundle.js") does not match the suffix
        # glob. Pre-existing quirk, harmless here — _rule_excluded only
        # filters the teeth's diff-traffic count; real selection is OCR's
        # own globbing.
        assert not rs._rule_excluded("static/app.bundle.js", globs)

    def test_house_checklist_is_carried(self):
        assert len(rs.HOUSE_CHECKLIST) >= 9
        assert all(item.strip() for item in rs.HOUSE_CHECKLIST)
