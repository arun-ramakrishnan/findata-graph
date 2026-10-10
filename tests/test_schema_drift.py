"""Blocking schema-drift gate (proposal schema_ddl_review_surface S3).

`schema/<engine>/<db>.sql` is the tracked, reviewable DDL surface dumped
from the live `memory/` DBs. A stale file is a lie in the review surface,
so drift FAILS qa (operator decision — blocking, not advisory) instead of
relying on the advisory `make schema-check` report.

SKIP doctrine: `memory/` is gitignored, so a fresh clone has NO live DBs —
there the gate must skip cleanly, not fail (qa must stay runnable off this
machine). Drift only means anything when the source DBs exist.

Mutations: (M1) append a comment line to schema/sqlite/corpus.sql → the
drift test goes red naming the file; restore re-green. (M2) an orphan
tracked file in a scratch out-dir → reported by check(); (M3) drop the
SKIP branch → the gate fails on a machine without memory/ DBs.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from helpers.misc import schema_dump as sd

REPO_ROOT = Path(__file__).resolve().parents[1]


def _skip_reason() -> str | None:
    if not sd.MEMORY_DIR.is_dir():
        return "no memory/ DBs on this machine (fresh clone) — drift is undefinable"
    if not sd.source_dbs():
        return "no readable DBs under memory/ — drift is undefinable"
    return None


@pytest.fixture(scope="module")
def _live_dbs_present() -> None:
    reason = _skip_reason()
    if reason:
        pytest.skip(reason)


@pytest.mark.usefixtures("_live_dbs_present")
class TestSchemaDrift:
    def test_tracked_ddl_matches_live_dbs(self):
        drift = sd.check()
        assert not drift, (
            "tracked schema/ DDL is stale vs the live memory/ DBs — run "
            "`make schema-dump` and commit:\n  " + "\n  ".join(drift)
        )

    def test_fts5_companion_tables_are_tracked(self):
        """S7/M5 (range review): the shadow matcher is an engine-keyed
        suffix allowlist — real house companion tables (<virtual>_meta/
        _info/_mojo_doc/_ts_doc) must appear in the rendered DDL, not be
        swallowed by the old `<virtual>_anything` prefix match. The drift
        gate above re-renders the same generator, so only THIS assertion
        can catch a matcher regression."""
        expected_by_db = {
            "script_search": (
                "script_search_meta",
                "script_search_info",
                "script_search_mojo_doc",
                "script_search_ts_doc",
            ),
            "doc_search": ("doc_search_meta", "doc_search_info"),
            "memory_search": ("memory_search_meta", "memory_search_info"),
            "research": ("note_search_meta",),
        }
        checked = 0
        for path, engine in sd.source_dbs():
            if engine != "sqlite" or path.stem not in expected_by_db:
                continue
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            present = {
                r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            con.close()
            text = sd.sqlite_ddl(path)
            for companion in expected_by_db[path.stem]:
                if companion not in present:
                    continue  # not a live table on this machine
                checked += 1
                assert re.search(rf"CREATE TABLE {companion}\b", text), (
                    f"{companion} missing from the rendered DDL of {path} — the"
                    " shadow matcher is swallowing a real house table again"
                )
        assert checked >= 8, f"only {checked} companion tables checked; expected the 9 live ones"


class TestCheckOrphans:
    def test_orphan_tracked_file_reported(self, tmp_path):
        """A tracked file with no source DB is drift too (the DB was
        deleted/renamed without cleaning schema/)."""
        orphan = tmp_path / "sqlite" / "ghost.sql"
        orphan.parent.mkdir(parents=True)
        orphan.write_text("-- nobody home\n", encoding="utf-8")
        drift = sd.check(tmp_path)
        assert any("ghost.sql" in line and "orphan" in line for line in drift)

    @pytest.mark.usefixtures("_live_dbs_present")
    def test_missing_file_reported(self, tmp_path):
        """Source present, tracked file absent — the 'forgot to dump' class."""
        sources = sd.source_dbs()
        assert sources, "machine has no live DBs; this test needs one"
        path, engine = sources[0]
        drift = sd.check(tmp_path)
        assert any(
            f"{engine}/{path.stem}.sql" in line.replace("\\", "/").replace(f"{tmp_path}/", "")
            and line.startswith("missing:")
            for line in drift
        )
