"""Range-review R5: the shareholding --fetch-only → scratch → --apply-only
staging chain had NO test — writer (shareholding_sync.py:1288) and reader
(:1396) share only a directory + a JSON schema, so a schema drift would
surface as a silent skip or a production crash, never in CI. This smoke
leg pins the contract hermetically: tmp FETCH_SCRATCH + tmp SOURCES_DB,
no network, dry-run by default; a sentinel seam at _resolve_companies
stops main() right after the staging contract under test (the enrichment
tail is unrelated machinery).

The scratch payloads below are written in the WRITER's exact shape
(fid/url/parsed/rss/source — shareholding_sync.py:1288-1300), so a
writer-side schema change breaks these tests too, not just production.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from helpers.core import db as core_db  # noqa: E402
from helpers.maintenance import shareholding_sync as shp  # noqa: E402


class _StopContract(Exception):
    """Sentinel raised at the _resolve_companies seam."""


_STAGED_PARSED = {
    "symbol": "TEST",
    "period_start": "2026-09-01",
    "period_end": "2026-09-30",
    "promoter_pct": 55.0,
    "public_pct": 45.0,
    "emptr": 0.0,
    "named": [],
    "aggregates": {"Promoter & Promoter Group": 55.0, "Public": 45.0},
}

_STAGED = {
    "fid": "TESTFID1",
    "url": "https://nsearchives.nseindia.com/test/TESTFID1.xml",
    "parsed": _STAGED_PARSED,
    "rss": {"company": "Test Ltd"},
    "source": "nse",
}


@pytest.fixture()
def chain_env(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    sources = tmp_path / "sources.duckdb"
    sqlite_db = tmp_path / "research.sqlite"
    monkeypatch.setattr(shp, "FETCH_SCRATCH", scratch)
    monkeypatch.setattr(shp, "SOURCES_DB", sources)
    monkeypatch.setattr(core_db, "DEFAULT_DB_PATH", sqlite_db)

    def _resolve_sentinel(src, conn):  # noqa: ANN001
        raise _StopContract

    monkeypatch.setattr(shp, "_resolve_companies", _resolve_sentinel)
    return {"scratch": scratch, "sources": sources, "sqlite": sqlite_db}


def _stage(scratch: Path, payload: dict) -> None:
    # exact writer shape: json.dumps(payload) into FETCH_SCRATCH/f"{fid}.json"
    (scratch / f"{payload['fid']}.json").write_text(json.dumps(payload))


def _filings(sources: Path) -> int:
    con = duckdb.connect(str(sources), read_only=True)
    try:
        return con.execute("SELECT COUNT(*) FROM shp_filings").fetchone()[0]
    finally:
        con.close()


class TestRefreshChainContract:
    def test_apply_only_dry_run_prints_and_writes_nothing(self, chain_env, capsys):
        _stage(chain_env["scratch"], _STAGED)
        with pytest.raises(_StopContract):
            shp.main(["--apply-only"])
        out = capsys.readouterr().out
        assert "would apply TESTFID1 from scratch (dry-run)" in out
        assert _filings(chain_env["sources"]) == 0

    def test_apply_only_apply_consumes_scratch(self, chain_env, capsys):
        _stage(chain_env["scratch"], _STAGED)
        with pytest.raises(_StopContract):
            shp.main(["--apply-only", "--apply"])
        out = capsys.readouterr().out
        assert "applied TESTFID1 from scratch" in out
        assert _filings(chain_env["sources"]) == 1

    def test_empty_scratch_is_a_quiet_noop(self, chain_env, capsys):
        with pytest.raises(_StopContract):
            shp.main(["--apply-only"])
        assert "would apply" not in capsys.readouterr().out
        assert _filings(chain_env["sources"]) == 0

    def test_scratch_schema_drift_fails_loud(self, chain_env):
        broken = dict(_STAGED)
        del broken["parsed"]  # writer dropped a required key
        _stage(chain_env["scratch"], broken)
        with pytest.raises(KeyError):
            shp.main(["--apply-only"])
