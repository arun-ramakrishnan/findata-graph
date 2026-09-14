"""Tests for S19 data-format guards (data_format_checks.py).

Four pinned outcomes (proposal S19(c)):
1. a zstd-less parquet writer fails; zstd-pinned passes
2. a dict-returning designated loader fails; Arrow passes; HGX boundary exempt
3. baselined entries pass
4. stale baseline entries fail loudly (baseline hygiene)
Plus: the live repo scans clean (0 new violations).
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.validators import data_format_checks as dfc

ZSTD_BAD = """
import pyarrow.parquet as pq

def writer(tbl, path):
    pq.write_table(tbl, path)
"""

ZSTD_GOOD = """
import pyarrow.parquet as pq

def writer(tbl, path):
    pq.write_table(tbl, path, compression="zstd")
"""

COPY_BAD = """
SQL = "COPY t TO 'x.parquet' (FORMAT PARQUET)"
"""

ARROW_BAD = """
def load_x(sources):
    return {}
"""

ARROW_GOOD = """
import pyarrow as pa

def load_x(sources) -> pa.Table:
    ...
"""

HGX_BOUNDARY = """
def load_hgx(tbl):
    rows = tbl.to_pylist()
    from hypergraphx import Hypergraph
    return Hypergraph(edge_list=[tuple(sorted(r["node"] for r in rows))])
"""


def _with_module(monkeypatch, tmp_path, filename: str, src: str):
    """Point the scanner at a synthetic module inside a data-lane path."""
    lane = tmp_path / "helpers" / "graph"
    lane.mkdir(parents=True)
    (lane / filename).write_text(src)
    monkeypatch.setattr(dfc, "SCAN_ROOTS", [tmp_path / "helpers"])
    monkeypatch.setattr(dfc, "DATA_LANE_MODULES", {f"helpers/graph/{filename}"})
    monkeypatch.setattr(dfc, "_rel", lambda p: f"helpers/graph/{p.name}")


class TestZstdGuard:
    def test_plain_write_fails(self, monkeypatch, tmp_path):
        _with_module(monkeypatch, tmp_path, "m.py", ZSTD_BAD)
        v = dfc.scan_zstd_violations()
        assert any("compression='zstd'" in d for d in v.values())

    def test_zstd_write_passes(self, monkeypatch, tmp_path):
        _with_module(monkeypatch, tmp_path, "m.py", ZSTD_GOOD)
        assert dfc.scan_zstd_violations() == {}

    def test_duckdb_copy_without_zstd_fails(self, monkeypatch, tmp_path):
        _with_module(monkeypatch, tmp_path, "m.py", COPY_BAD)
        v = dfc.scan_zstd_violations()
        assert any("COMPRESSION ZSTD" in d for d in v.values())

    def test_docstrings_are_prose_not_sql(self, monkeypatch, tmp_path):
        _with_module(
            monkeypatch, tmp_path, "m.py", '"""COPY ... TO x.parquet (FORMAT PARQUET) docs"""\n'
        )
        assert dfc.scan_zstd_violations() == {}


class TestArrowGuard:
    def test_dict_loader_fails(self, monkeypatch, tmp_path):
        _with_module(monkeypatch, tmp_path, "m.py", ARROW_BAD)
        v = dfc.scan_arrow_violations()
        assert "helpers/graph/m.py:load_x" in v

    def test_arrow_loader_passes(self, monkeypatch, tmp_path):
        _with_module(monkeypatch, tmp_path, "m.py", ARROW_GOOD)
        assert dfc.scan_arrow_violations() == {}

    def test_hgx_boundary_exempt_not_baselined(self, monkeypatch, tmp_path):
        _with_module(monkeypatch, tmp_path, "m.py", HGX_BOUNDARY)
        assert dfc.scan_arrow_violations() == {}


class TestBaselines:
    def test_baselined_entry_passes(self, monkeypatch, tmp_path):
        _with_module(monkeypatch, tmp_path, "m.py", ARROW_BAD)
        monkeypatch.setattr(
            dfc, "_ARROW_BASELINE", {"helpers/graph/m.py:load_x": ("reason", "exit")}
        )
        fatal, _ = dfc.check_data_format()
        assert fatal == []

    def test_stale_baseline_fails_loudly(self, monkeypatch, tmp_path):
        _with_module(monkeypatch, tmp_path, "m.py", ZSTD_GOOD)
        monkeypatch.setattr(
            dfc, "_ARROW_BASELINE", {"helpers/graph/m.py:load_x": ("reason", "exit")}
        )
        fatal, _ = dfc.check_data_format()
        assert any("stale" in f and "remove" in f for f in fatal)


class TestLiveRepo:
    def test_live_repo_scans_clean(self):
        fatal, advisory = dfc.check_data_format()
        assert fatal == []
        assert advisory == []  # baseline ledger emptied 2026-09-14 (S18(b)/S14 exits)
