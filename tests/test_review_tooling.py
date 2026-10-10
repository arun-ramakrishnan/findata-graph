#!/usr/bin/env python3
"""Review tooling tests — the OCR support CLIs `make review-patch` drives.

The two modules had zero unit coverage while carrying the review loop's
bookkeeping (bake-off finding class 9, the corpus's only Medium,
review_findings_collation.md S4): review_freshness.py is the ledger writer
the freshness check depends on (a silent mis-write corrupts the very
bookkeeping reviews cite), review_selection.py maps the stgit stack to
reviewable families. Hermetic by construction — the ledger is redirected to
tmp_path and git/stg are faked, so nothing here touches the real stack or
`outputs/reviews/review-freshness.json`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import helpers.misc.review_freshness as rf
import helpers.misc.review_selection as rs
import pytest

FP_TEXT = "diff-text-A\n"


def _proc(stdout="", returncode=0, stderr=""):
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """Redirect the ledger into tmp and fake the two subprocess surfaces."""
    path = tmp_path / "ledger.json"
    monkeypatch.setattr(rf, "LEDGER", path)
    monkeypatch.setattr(rf, "_diff_text", lambda stack: FP_TEXT)
    # stack mode resolves SHAs for the diff-cache key (S11 perf): pin them
    monkeypatch.setattr(rf, "_rev_sha", lambda ref: f"sha-{ref}")
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
        assert row is not None
        assert state == "FRESH" and row["fingerprint"] == fp and scope == "HEAD~1..HEAD"

    def test_content_change_goes_stale_same_scope(self, ledger, monkeypatch):
        rf.main(["--record"])
        monkeypatch.setattr(rf, "_diff_text", lambda stack: "diff-text-B\n")
        rf._diff_cache_save({})  # moved HEAD ⇒ new SHAs ⇒ cache miss (in prod, SHAs pin content)
        state, row, scope = rf.status(1)
        assert row is not None
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

    def test_ref_args_commit_needs_no_git(self, monkeypatch):
        def boom(*_a, **_kw):
            raise AssertionError("git must not run in commit mode")

        monkeypatch.setattr(rs.subprocess, "run", boom)
        # OCR resolves the ref itself, so range-ish values pass through
        assert rs._ref_args(1, "e4258377b") == ["--commit", "e4258377b"]
        assert rs._ref_args(3, "HEAD~2") == ["--commit", "HEAD~2"]

    def test_commit_and_stack_together_refuses(self, capsys):
        # a silently-ignored flag would review the wrong thing
        assert rs.main(["--commit", "e4258377b", "--stack", "2"]) == 2
        assert "one of --stack or --commit" in capsys.readouterr().err

    def test_ref_args_from_to_is_the_landed_pair_encoding(self, monkeypatch):
        def boom(*_a, **_kw):
            raise AssertionError("git must not run in --from/--to mode")

        monkeypatch.setattr(rs.subprocess, "run", boom)
        # a landed pair is not HEAD-relative: --stack cannot express it
        assert rs._ref_args(1, None, "4fd3a20f", "bda590995") == [
            "--from",
            "4fd3a20f",
            "--to",
            "bda590995",
        ]

    def test_half_range_refuses(self, capsys):
        # mutation: dropping the pair check silently reviews base..HEAD
        assert rs.main(["--from", "4fd3a20f"]) == 2
        assert "go together" in capsys.readouterr().err

    def test_ref_args_half_range_refuses_rather_than_emitting_none(self):
        """Argparse already refuses a half-range at the CLI, but _ref_args is
        callable directly — and it used to build ["--from", x, "--to", None],
        which stringifies to the literal "None" and sails past OCR's
        both-or-neither validation as an unresolvable git ref.

        mutation: delete the `to_ref is None` guard → no raise, test red."""
        with pytest.raises(ValueError, match="half a range"):
            rs._ref_args(1, None, "4fd3a20f", None)

    def test_from_to_with_commit_or_stack_refuses(self, capsys):
        # same voice as review_scan's validator — one rule, one message
        for argv in (
            ["--from", "a", "--to", "b", "--commit", "c"],
            ["--from", "a", "--to", "b", "--stack", "2"],
        ):
            assert rs.main(argv) == 2
            assert "one of --stack, --commit or --from/--to" in capsys.readouterr().err

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


class TestSelectionTeeth:
    """The split teeth helpers (c901_d1_split_batch1 S3): violation =
    a rule-covered family with diff traffic OCR selected nothing from."""

    GLOBS = ["Mojo/vendor/**"]

    def test_family_with_selected_traffic_is_no_violation(self):
        assert rs._selection_teeth(["tests/test_x.py"], [], ["tests/"], self.GLOBS) == []

    def test_touched_but_unselected_family_is_a_violation(self):
        # the shipped-defect class: tests changed, tests invisible
        assert rs._selection_teeth(
            ["helpers/misc/x.py"], ["tests/test_x.py"], ["tests/"], self.GLOBS
        ) == ["tests/"]

    def test_rule_excluded_traffic_is_declared_invisible(self):
        # vendor hits are excluded traffic, not a family the include missed
        assert rs._selection_teeth([], ["Mojo/vendor/lib/x.mojo"], ["Mojo/src/"], self.GLOBS) == []

    def test_in_family_covers_exact_root_and_prefix(self):
        assert rs._in_family("tests", "tests")
        assert rs._in_family("tests/test_x.py", "tests/")
        assert not rs._in_family("testsuite/test_x.py", "tests/")


class TestPrintFreshness:
    def test_fresh_row_prints_the_reviewed_line(self, ledger, capsys):
        rf.main(["--record", "--leg", "delegation", "--note", "test verdict"])
        rs._print_freshness(1)
        out = capsys.readouterr().out
        # rf.main --record also prints (its "recorded …" line precedes ours)
        assert "review-freshness: FRESH (reviewed " in out
        assert "legs=['delegation']" in out and "test verdict" in out

    def test_unavailable_never_raises(self, ledger, monkeypatch, capsys):
        def boom(stack):
            raise RuntimeError("ledger corrupt")

        monkeypatch.setattr(rf, "status", boom)
        rs._print_freshness(1)  # advisory context — must not raise
        out = capsys.readouterr().out
        assert out == "review-freshness: unavailable (RuntimeError: ledger corrupt)\n"


class TestFreshnessRangeMode:
    """S11 (2026-10-11): landed --from/--to pairs are recordable — the
    ledger keys the range's diff TEXT, so a re-run of an already-reviewed
    range reads FRESH and the next scan can start from the last head.

    Mutations: key the row on the SHA instead of the diff text and the
    STALE case goes red (refreshed SHAs would look fresh); drop the
    half-range guard and test_half_range_refuses goes red."""

    RANGE_TEXT = "diff --git a/x.py b/x.py\n+range mode body\n"

    @pytest.fixture(autouse=True)
    def _range_ledger(self, ledger, monkeypatch):
        # pair-dependent text: different ranges MUST hash differently, or the
        # fp match would mask the SUBSUMED/UNREVIEWED containment paths
        monkeypatch.setattr(
            rf, "_diff_text_refs", lambda base, head: self.RANGE_TEXT + f"{base}..{head}\n"
        )
        monkeypatch.setattr(
            rf,
            "_rev_sha",
            # multiple spellings resolve to the SAME commit — that identity is
            # what the canonical scope label must preserve
            lambda ref: {
                "aaa111": "sha-aaa",
                "aaa111111": "sha-aaa",
                "bbb222": "sha-bbb",
                "bbb222222": "sha-bbb",
                "mid222": "sha-mid",
                "ccc333": "sha-ccc",
            }.get(ref, ref),
        )

    def test_record_then_fresh_roundtrip(self, capsys):
        rf.main(["--from", "aaa111", "--to", "bbb222", "--record", "--note", "range review"])
        rf.main(["--from", "aaa111", "--to", "bbb222"])
        out = capsys.readouterr().out
        assert "recorded sha-aaa..sha-bbb fp=" in out  # canonical scope from RESOLVED SHAs
        assert "review-freshness" not in out  # rf.main prints state lines, not the prefix
        assert "-> FRESH" in out

    def test_content_change_reads_stale(self, capsys, monkeypatch):
        rf.main(["--from", "aaa111", "--to", "bbb222", "--record"])
        monkeypatch.setattr(rf, "_diff_text_refs", lambda base, head: self.RANGE_TEXT + "+more\n")
        rf._diff_cache_save({})  # moved head ⇒ new SHAs ⇒ cache miss
        rf.main(["--from", "aaa111", "--to", "bbb222"])
        assert "-> STALE" in capsys.readouterr().out

    def test_recorded_row_carries_null_stack(self, capsys):
        rf.main(["--from", "aaa111", "--to", "bbb222", "--record"])
        rows = rf._load()
        assert rows[-1]["stack"] is None
        assert rows[-1]["scope"] == "sha-aaa..sha-bbb"

    def test_half_range_refuses(self, capsys):
        with pytest.raises(SystemExit, match="go together"):
            rf.main(["--from", "aaa111"])

    def test_print_freshness_range_args(self, capsys):
        rf.main(["--from", "aaa111", "--to", "bbb222", "--record"])
        rs._print_freshness(1, range_args=("aaa111", "bbb222"))
        out = capsys.readouterr().out
        assert "review-freshness: FRESH" in out

    def test_repeat_range_uses_diff_cache(self, monkeypatch):
        """S11 perf (2026-10-11): the range diff is ~60s on a big landed
        range, and identical SHAs provably mean identical diff text
        (content-addressed) — so the fp is memoized by the resolved SHA
        pair and a repeat lookup never recomputes. Mutation: remove the
        cache lookup in _fingerprint_cached and the call-count assert goes
        red (two computes)."""
        calls = {"n": 0}
        real_text = self.RANGE_TEXT

        def counting_text(base, head):
            calls["n"] += 1
            return real_text

        monkeypatch.setattr(rf, "_diff_text_refs", counting_text)
        fp1, scope1 = rf.fingerprint_range("aaa111", "bbb222")
        fp2, scope2 = rf.fingerprint_range("aaa111", "bbb222")
        assert fp1 == fp2 and scope1 == scope2 == "sha-aaa..sha-bbb"
        assert calls["n"] == 1, "second lookup must hit the SHA-pair cache"
        cache = rf._diff_cache_load()
        assert cache["sha-aaa:sha-bbb"]["fp"] == fp1

    def test_ranged_row_records_base_and_head_shas(self):
        rf.main(["--from", "aaa111", "--to", "bbb222", "--record"])
        row = rf._load()[-1]
        assert row["base_sha"] == "sha-aaa" and row["head_sha"] == "sha-bbb"

    def test_subset_head_is_subsumed(self, monkeypatch, capsys):
        """S11b: asking for 4fd..mid when 4fd..head was reviewed reports
        SUBSUMED (same base, asked head an ancestor of the recorded head) —
        not a lying UNREVIEWED. Mutation: drop the base_sha equality guard
        and test_no_subsumption_across_different_base goes red; drop the
        _is_ancestor call and both SUBSUMED tests go red."""
        rf.main(["--from", "aaa111", "--to", "bbb222", "--record", "--note", "superset"])
        monkeypatch.setattr(rf, "_is_ancestor", lambda asked, recorded: True)
        state, row, scope = rf.status_range("aaa111", "mid222")
        assert state == "SUBSUMED" and row["note"] == "superset"
        assert scope == "sha-aaa..sha-mid"
        rf.main(["--from", "aaa111", "--to", "mid222"])
        out = capsys.readouterr().out
        assert "-> SUBSUMED" in out
        assert "contained in recorded range sha-aaa..sha-bbb" in out

    def test_no_subsumption_when_not_ancestor(self, monkeypatch):
        rf.main(["--from", "aaa111", "--to", "bbb222", "--record"])
        monkeypatch.setattr(rf, "_is_ancestor", lambda asked, recorded: False)
        assert rf.status_range("aaa111", "mid222")[0] == "UNREVIEWED"

    def test_no_subsumption_across_different_base(self, monkeypatch):
        rf.main(["--from", "aaa111", "--to", "bbb222", "--record"])
        monkeypatch.setattr(rf, "_is_ancestor", lambda asked, recorded: True)
        # same recorded head, but the asked base differs — containment over
        # a different starting point is partial overlap, never SUBSUMED
        assert rf.status_range("ccc333", "bbb222")[0] == "UNREVIEWED"

    def test_scope_label_is_spelling_invariant(self):
        """2026-10-11 bug: scope was built from the ARGUMENT spelling, so
        `4fd3a20f..0805d829` and `4fd3a20f5..0805d829c` labelled the same
        range differently and --show / STALE grouping missed rows. The
        label must come from RESOLVED SHAs. Mutation: revert to
        `f"{base[:12]}..{head[:12]}"` and this goes red."""
        fp1, scope1 = rf.fingerprint_range("aaa111", "bbb222")
        fp2, scope2 = rf.fingerprint_range("aaa111111", "bbb222222")  # same SHAs, longer spelling
        assert scope1 == scope2 == "sha-aaa..sha-bbb"
        assert fp1 == fp2

    def test_show_finds_rows_under_any_spelling(self, capsys):
        rf.main(["--from", "aaa111", "--to", "bbb222", "--record"])
        rf.main(["--from", "aaa111111", "--to", "bbb222222", "--show"])
        out = capsys.readouterr().out
        assert "no rows" not in out and '"scope": "sha-aaa..sha-bbb"' in out

    def test_leg_without_record_refuses(self):
        with pytest.raises(SystemExit, match="--leg only matters with --record"):
            rf.main(["--from", "aaa111", "--to", "bbb222", "--leg", "delegation"])

    def test_all_lists_every_record_newest_first(self, capsys):
        """Operator ask 2026-10-11: --show is scope-scoped, so an old record
        could go unnoticed for weeks. --all is the whole-ledger view.
        Mutation: drop the reviewed_at sort key and the newest-first assert
        goes red on this seeded order."""
        rf._save(
            [
                {
                    "scope": "old..range",
                    "stack": None,
                    "fingerprint": "fp-old",
                    "reviewed_at": "2026-09-30T13:34:20+00:00",
                    "legs": ["delegation"],
                    "note": "historic leg",
                },
                {
                    "scope": "sha-new..sha-head",
                    "stack": None,
                    "fingerprint": "fp-new",
                    "reviewed_at": "2026-10-11T02:58:25+00:00",
                    "legs": ["delegation", "scan"],
                    "note": "recent leg",
                },
            ]
        )
        rf.main(["--all"])
        out = capsys.readouterr().out
        assert out.index("sha-new..sha-head") < out.index("old..range"), "newest first"
        assert "2 record(s), newest first" in out
        assert "recent leg" in out and "historic leg" in out

    def test_all_refuses_recording_mixes(self):
        with pytest.raises(SystemExit, match="whole-ledger view"):
            rf.main(["--all", "--record"])
