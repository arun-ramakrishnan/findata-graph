"""Tests for the native scan leg (archive/tooling/review_scan_leg.md S1–S6).

House doctrine: every test must be able to FAIL on the bug it pins — the
mutations that verify each one are named in the test bodies.
"""

import json
import subprocess
import sys
from pathlib import Path

import helpers.misc.review_scan as rs


def _fake_git(stdout: str, returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=""
    )


# A real `git diff -U0 --no-renames` sample: a mid-file pure deletion
# (`@@ -3 +2,0 @@`) and an insertion hunk (`@@ -10,2 +12,3 @@`).
SAMPLE_DIFF = """\
diff --git a/f.txt b/f.txt
index 1111111..2222222 100644
--- a/f.txt
+++ b/f.txt
@@ -3 +2,0 @@ context ignored
-c
diff --git a/g.py b/g.py
index 3333333..4444444 100644
--- a/g.py
+++ b/g.py
@@ -10,2 +12,3 @@ def x():
+a
+b
+c
"""


class TestChangedLineMapper:
    """T1. Mutation: drop the `start + 1` in the pure-deletion branch, or
    count context lines, and these go red."""

    def test_hunk_ranges_and_deletion_adjacency(self, monkeypatch):
        monkeypatch.setattr(rs, "_git", lambda argv: _fake_git(SAMPLE_DIFF))
        assert rs._changed_lines("base", "head") == {"f.txt": {2, 3}, "g.py": {12, 13, 14}}

    def test_deletion_at_file_start_marks_line_one_only(self, monkeypatch):
        # `@@ -1 +0,0 @@` — the new first line is the only adjacency.
        diff = "+++ b/new.py\n@@ -1 +0,0 @@\n-import os\n"
        monkeypatch.setattr(rs, "_git", lambda argv: _fake_git(diff))
        assert rs._changed_lines("base", "head") == {"new.py": {1}}

    def test_dev_null_and_empty_results(self, monkeypatch):
        diff = "--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n"
        monkeypatch.setattr(rs, "_git", lambda argv: _fake_git(diff))
        assert rs._changed_lines("base", "head") == {}
        monkeypatch.setattr(rs, "_git", lambda argv: _fake_git(""))
        assert rs._changed_lines("base", "head") == {}

    def test_strip_prefix(self):
        assert rs._strip_prefix("b/helpers/x.py") == "helpers/x.py"
        assert rs._strip_prefix("helpers/x.py") == "helpers/x.py"


class TestNoqaGate:
    """T2. Mutation: return False unconditionally (the dead-noqa noise the
    trial measured returns), or ignore the code comparison (a noqa for an
    unrelated code starts dropping findings) — both go red."""

    def test_codes_parsing(self):
        assert rs._noqa_codes("x = 1") is None
        assert rs._noqa_codes("x = 1  # noqa: S608") == {"S608"}
        assert rs._noqa_codes("x = 1  # noqa: S603, S607") == {"S603", "S607"}
        assert rs._noqa_codes("x = 1  # noqa") == {"*"}

    def test_mapped_code_drops(self):
        content = ['conn.execute(f"DELETE FROM {T}")  # noqa: S608  # constant']
        assert rs._adjudicated("B608", content, 1) is True

    def test_wrong_code_does_not_drop(self):
        # the S607-anchor trap: a noqa for a different code keeps the finding
        content = ['conn.execute(f"DELETE FROM {T}")  # noqa: S607']
        assert rs._adjudicated("B608", content, 1) is False

    def test_bare_noqa_drops_and_unknown_rule_keeps(self):
        assert rs._adjudicated("B608", ["x  # noqa"], 1) is True
        assert rs._adjudicated("B999", ["x  # noqa: S608"], 1) is False

    def test_missing_content_or_out_of_range_keeps(self):
        # never drop on ignorance: deleted files, binary content, EOF
        assert rs._adjudicated("B608", None, 1) is False
        assert rs._adjudicated("B608", ["one line"], 9) is False

    def test_noqa_below_the_citation_drops(self):
        """semgrep anchors at `conn.execute(` and the house noqa sits on the
        f-string argument below (ruff anchors S608 on the string) — the KNN
        golden range leaked 10 findings until the span existed."""
        content = [
            "conn.execute(",
            '    f"DELETE FROM {VEC_TABLE} WHERE p = ?", (p,)  # noqa: S608  # constant',
            ")",
        ]
        # the SARIF ruleId is rule-path + rule-name (the doubled tail is real,
        # not a typo) — an unknown rule id must never drop anything
        assert rs._adjudicated(
            "python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query",
            content,
            1,
        ) is True
        assert rs._adjudicated("B608", content, 1) is True

    def test_neighbouring_statement_noqa_does_not_leak(self):
        """the drop must stay inside the cited statement: an UNADJUDICATED
        execute() one line above a noqa-carrying one must survive."""
        content = [
            "conn.execute(f'DELETE FROM {T} WHERE p = ?', (p,))",
            "conn.execute(",
            '    f"DELETE FROM {T} WHERE p = ?", (p,)  # noqa: S608  # constant',
            ")",
        ]
        assert rs._adjudicated("B608", content, 1) is False
        assert rs._adjudicated("B608", content, 2) is True

    def test_span_cap_bounds_a_runaway_paren(self):
        # a stray "(" in a long literal must not widen the walk over the file
        content = ["conn.execute(" + "("] + [f"line{i}" for i in range(40)]
        assert len(rs._statement_span(content, 1)) <= rs._NOQA_SPAN_CAP + 1

    def test_house_config_adjudication(self):
        """The pyproject per-file-ignores mirror: S101 asserts are the
        contract in tests/ (mutation: drop B101 from HOUSE_PER_FILE_IGNORES
        and the test-noise class the golden range measured returns)."""
        assert rs._config_adjudicated("B101", "tests/test_x.py") is True
        assert rs._config_adjudicated("B101", "doc/templates/test_module.py") is True
        assert rs._config_adjudicated("B101", "helpers/x.py") is False  # outside the ignore
        assert rs._config_adjudicated("B608", "tests/test_x.py") is False  # un-ignored code

    def test_rule_scope_survives_a_missing_selection_helper(self, monkeypatch):
        """Golden run 03743294b, in a worktree that predates
        review_selection.py: the import-failure branch rebound only the glob
        lists, so the loop's `_rule_excluded(...)` raised UnboundLocalError
        and the leg died instead of degrading to 'no rule scoping'."""
        monkeypatch.setitem(sys.modules, "helpers.misc.review_selection", None)
        assert rs._rule_scope({"a.py": {1}, "docs/b.md": {2}}) == {"a.py", "docs/b.md"}


class TestLockfileParsers:
    """T4. Mutation: parse the `dependencies` fallback away (legacy lockfiles
    silently lose every dep) and the npm test goes red."""

    def test_uv_lock_packages(self):
        text = (
            'version = 1\n[[package]]\nname = "duckdb"\nversion = "1.1.0"\n'
            '[[package]]\nname = "helpers"\nversion = "0.1.0"\nsource = { editable = "." }\n'
        )
        assert rs._uv_lock_packages(text) == [("duckdb", "1.1.0"), ("helpers", "0.1.0")]

    def test_uv_lock_skips_entries_without_version(self):
        text = '[[package]]\nname = "local-only"\n'
        assert rs._uv_lock_packages(text) == []

    def test_npm_lock_v3_packages_map(self):
        payload = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root"},
                "node_modules/source-map-js": {"version": "1.2.1"},
                "node_modules/@vue/runtime-core": {"version": "3.5.0"},
            },
        }
        assert rs._npm_lock_packages(json.dumps(payload)) == [
            ("source-map-js", "1.2.1"),
            ("@vue/runtime-core", "3.5.0"),
        ]

    def test_npm_lock_legacy_dependencies_fallback(self):
        payload = {"lockfileVersion": 1, "dependencies": {"vue": {"version": "3.4.0"}}}
        assert rs._npm_lock_packages(json.dumps(payload)) == [("vue", "3.4.0")]


class TestSeverityMapping:
    """T5. Mutation: swap the result/rule precedence in _semgrep_severity
    (the SARIF spec says the result's score wins) and that test goes red."""

    def test_score_bands(self):
        assert rs._score_band(9.8) == "critical"
        assert rs._score_band(8.7) == "high"
        assert rs._score_band(5.0) == "medium"
        assert rs._score_band(0.5) == "low"
        assert rs._score_band(0) == "info"

    def test_bandit_and_shellcheck(self):
        assert rs._bandit_severity({"issue_severity": "LOW"}) == "low"
        assert rs._bandit_severity({"issue_severity": "HIGH"}) == "high"
        assert rs._shellcheck_severity("info") == "low"
        assert rs._shellcheck_severity("error") == "high"

    def test_semgrep_result_score_beats_rule_score(self):
        rules = {"r1": {"properties": {"security-severity": "9.8"}}}
        result = {"ruleId": "r1", "level": "warning", "properties": {"security-severity": "2.0"}}
        assert rs._semgrep_severity(result, rules) == "low"

    def test_semgrep_falls_back_to_rule_then_level(self):
        rules = {"r1": {"properties": {"security-severity": "7.5"}}}
        assert rs._semgrep_severity({"ruleId": "r1"}, rules) == "high"
        assert rs._semgrep_severity({"ruleId": "r9", "level": "note"}, rules) == "low"


class TestHeadCoordinateSystem:
    """The golden-range lesson: scanners must read the RANGE HEAD's content,
    and the noqa gate must read the SAME content — a working-tree scan with
    a head-content gate compares two coordinate systems and silently misses
    every adjudication the moment the tree drifts.

    Mutation: pass ``files`` to ``_run`` instead of ``tree.values()`` (the
    pre-fix call) — the argv assertion below goes red, which is exactly the
    defect the KNN golden range measured.
    """

    HEAD_CONTENT = 'conn.execute(f"DELETE FROM {T}")  # noqa: S608  # constant'

    def _materialized(self, monkeypatch, tmp_path, content):
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        monkeypatch.setattr(rs, "_git", lambda argv: _fake_git(content + "\n"))
        return rs._materialize_at("head", ["helpers/x.py"])

    def test_materialize_writes_head_content_and_skips_absent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        # Only helpers/x.py exists at head; gone.py returns rc!=0 like a
        # deleted/binary/submodule file.
        monkeypatch.setattr(
            rs,
            "_git",
            lambda argv: _fake_git(self.HEAD_CONTENT + "\n", 0 if "x.py" in argv[-1] else 1),
        )
        tree = rs._materialize_at("head", ["helpers/x.py", "helpers/gone.py"])
        assert set(tree) == {"helpers/x.py"}  # absent content is omitted, not blanked
        from pathlib import Path

        assert Path(tree["helpers/x.py"]).read_text() == self.HEAD_CONTENT + "\n"

    def test_scanner_gets_scratch_path_and_finding_maps_back(self, monkeypatch, tmp_path):
        tree = self._materialized(monkeypatch, tmp_path, self.HEAD_CONTENT)
        scratch = tree["helpers/x.py"]
        seen: dict[str, list[str]] = {}

        def _fake_run(argv):
            seen["argv"] = argv
            payload = {
                "results": [
                    {
                        "filename": scratch,
                        "line_number": "1",
                        "test_id": "B101",
                        "issue_text": "assert",
                    }
                ]
            }
            return _fake_git(json.dumps(payload))

        monkeypatch.setattr(rs, "_venv_bin", lambda name: tmp_path / "bandit")
        monkeypatch.setattr(rs, "_run", _fake_run)
        found, _status = rs._leg_bandit(["helpers/x.py"], {"helpers/x.py": {1}}, "head", tree)
        assert scratch in seen["argv"], "the scanner must read the materialized head tree"
        assert "helpers/x.py" not in seen["argv"][3:]  # never the working-tree rel path
        # B101 is house-config-adjudicated only under tests/; here it survives
        # AND its reported path resolves back to the repo-relative path.
        assert [(f.rule, f.path) for f in found] == [("B101", "helpers/x.py")]

    def test_head_noqa_drops_on_the_same_content(self, monkeypatch, tmp_path):
        """The adjudication must see the line the scanner cited — one
        coordinate system, so the S608 drop works end to end."""
        tree = self._materialized(monkeypatch, tmp_path, self.HEAD_CONTENT)
        scratch = tree["helpers/x.py"]

        def _fake_run(argv):
            payload = {
                "results": [
                    {
                        "filename": scratch,
                        "line_number": "1",
                        "test_id": "B608",
                        "issue_text": "sql",
                    }
                ]
            }
            return _fake_git(json.dumps(payload))

        monkeypatch.setattr(rs, "_venv_bin", lambda name: tmp_path / "bandit")
        monkeypatch.setattr(rs, "_run", _fake_run)
        found, status = rs._leg_bandit(["helpers/x.py"], {"helpers/x.py": {1}}, "head", tree)
        assert found == [] and "0 kept" in status


class TestSkipIfMissing:
    """T3. Mutation: raise instead of returning the status line and the
    advisory-never-fails contract breaks."""

    def test_missing_binary_is_a_status_line_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(rs, "_venv_bin", lambda name: None)
        found, status = rs._leg_bandit(["x.py"], {}, "HEAD")
        assert found == []
        assert "not installed" in status and "uv sync --extra review" in status

    def test_unparseable_output_is_a_status_line(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rs, "_venv_bin", lambda name: tmp_path / "no-such")
        monkeypatch.setattr(rs, "_run", lambda argv: _fake_git("not json"))
        found, status = rs._leg_shellcheck(["x.sh"], {}, "HEAD")
        assert found == []
        assert "unparseable" in status


class TestSqlfluffEngineRouting:
    """schema_ddl_review_surface S5: the engine directory IS the dialect.
    Mutations: return None from _sql_engine (single default-dialect run
    misparses duckdb) or drop the PRS filter (6 extension-syntax findings
    return on every schema change)."""

    def test_engine_directory_selects_dialect(self):
        assert rs._sql_engine("schema/sqlite/research.sql") == "sqlite"
        assert rs._sql_engine("schema/duckdb/graph.sql") == "duckdb"
        assert rs._sql_engine("snapshots/parquet/_schema.sqlite.sql") is None
        assert rs._sql_engine("misc/other.sql") is None

    def test_extension_syntax_window(self):
        content = [
            "CREATE VIRTUAL TABLE doc_search",
            "USING fts5(title, section_title, file_path UNINDEXED,",
            "          content UNINDEXED);",
        ]
        assert rs._extension_syntax(3, content) is True  # inside the USING clause
        assert rs._extension_syntax(1, content) is False  # the CREATE line itself
        assert rs._extension_syntax(2, ["CREATE TABLE t(a INT);"]) is False

    def test_prs_on_extension_syntax_is_skipped_other_rules_kept(self, monkeypatch, tmp_path):
        calls: list[list[str]] = []

        def fake_run(argv: list[str]):
            calls.append(argv)
            payload = [
                {
                    "filepath": str(tmp_path / "doc_search.sql"),
                    "violations": [
                        {"start_line_no": 3, "code": "PRS", "description": "unparsable UNINDEXED"},
                        {"start_line_no": 3, "code": "LT12", "description": "files must end with newline"},
                    ],
                }
            ]
            return _fake_git(json.dumps(payload))

        monkeypatch.setattr(rs, "_venv_bin", lambda name: tmp_path / "sqlfluff")
        monkeypatch.setattr(rs, "_run", fake_run)
        content = [
            "CREATE VIRTUAL TABLE doc_search",
            "USING fts5(title,",
            "          content UNINDEXED);",
        ]
        monkeypatch.setattr(
            rs, "_content_at", lambda _ref, _path: content
        )
        changed = {"schema/sqlite/doc_search.sql": {3}}
        found, status = rs._leg_sqlfluff(
            list(changed), changed, "ansi", tree={q: str(tmp_path / Path(q).name) for q in changed}
        )
        assert "ran" in status
        # PRS on the USING-fts5 line suppressed; the real rule on the same line kept
        assert [f.rule for f in found] == ["LT12"]

    def test_duckdb_group_gets_duckdb_dialect(self, monkeypatch, tmp_path):
        argvs: list[list[str]] = []

        def fake_run(argv: list[str]):
            argvs.append(argv)
            return _fake_git("[]")

        monkeypatch.setattr(rs, "_venv_bin", lambda name: tmp_path / "sqlfluff")
        monkeypatch.setattr(rs, "_run", fake_run)
        changed = {"schema/duckdb/graph.sql": {1}, "schema/sqlite/research.sql": {1}}
        rs._leg_sqlfluff(
            list(changed), changed, "ansi", tree={q: str(tmp_path / Path(q).name) for q in changed}
        )
        assert len(argvs) == 2
        dialected = sorted(a[3] for a in argvs if len(a) > 3)
        assert dialected == ["duckdb", "sqlite"]


class TestMainSurface:
    def test_conflicting_range_flags_refuse(self, capsys):
        # a silently-ignored flag would scan the wrong thing
        assert rs.main(["--commit", "abc", "--stack", "2"]) == 2
        assert "one of" in capsys.readouterr().err

    def test_half_a_range_refuses(self, capsys):
        assert rs.main(["--from", "HEAD~1"]) == 2
        assert "go together" in capsys.readouterr().err

    def test_all_scanners_skipped_still_exits_zero(self, monkeypatch, capsys):
        monkeypatch.setattr(
            rs,
            "_git",
            lambda argv: _fake_git(SAMPLE_DIFF) if "diff" in argv else _fake_git("deadbeef\n"),
        )
        rc = rs.main(
            [
                "--from",
                "base",
                "--to",
                "head",
                "--skip",
                "bandit",
                "--skip",
                "shellcheck",
                "--skip",
                "sqlfluff",
                "--skip",
                "semgrep",
                "--skip",
                "osv",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "native scan base..head: 2 changed files" in out
        assert "bandit: skipped" in out and "osv: skipped" in out

    def test_json_artifact_lands_under_outputs_reviews(self, monkeypatch, tmp_path):
        out = tmp_path / "reviews"
        monkeypatch.setattr(rs, "REVIEW_OUT_DIR", out)
        monkeypatch.setattr(
            rs,
            "_git",
            lambda argv: _fake_git(SAMPLE_DIFF) if "diff" in argv else _fake_git("deadbeef\n"),
        )
        assert (
            rs.main(
                [
                    "--from",
                    "base",
                    "--to",
                    "head",
                    "--skip",
                    "bandit",
                    "--skip",
                    "shellcheck",
                    "--skip",
                    "sqlfluff",
                    "--skip",
                    "semgrep",
                    "--skip",
                    "osv",
                    "--json",
                ]
            )
            == 0
        )
        assert list(out.glob("review_scan_*.json"))
        rosters = list(out.glob("review_scan_*_*.md"))
        assert len(rosters) == 1 and "review-scan base..head" in rosters[0].read_text()


class TestRosterFormat:
    def test_roster_line_shape(self):
        f = rs.Finding(
            scanner="bandit",
            rule="B608",
            severity="low",
            path="helpers/x.py",
            line=42,
            message="Possible SQL injection\nsecond line ignored",
        )
        assert f.roster_line() == "low      bandit:B608  helpers/x.py:42  Possible SQL injection"

    def test_whole_file_finding_has_no_line(self):
        f = rs.Finding(
            severity="high",
            scanner="osv",
            rule="GHSA-x",
            path="uv.lock",
            line=None,
            message="dep@1.0",
        )
        assert f.roster_line().endswith("uv.lock  dep@1.0")
