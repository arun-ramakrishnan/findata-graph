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


class TestCheckOrphans:
    def test_orphan_tracked_file_reported(self, tmp_path):
        """A tracked file with no source DB is drift too (the DB was
        deleted/renamed without cleaning schema/)."""
        orphan = tmp_path / "sqlite" / "ghost.sql"
        orphan.parent.mkdir(parents=True)
        orphan.write_text("-- nobody home\n", encoding="utf-8")
        drift = sd.check(tmp_path)
        assert any("ghost.sql" in line and "orphan" in line for line in drift)

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
