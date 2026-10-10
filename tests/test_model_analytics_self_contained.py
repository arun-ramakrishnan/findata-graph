#!/usr/bin/env python3
"""model_analytics_self_contained S3 tests — no legacy-sibling dependency.

`zai_usage_query` / `opencode_usage_query` are legacy, unmaintained and
git-ignored: the tracked loader must not import them (fresh clones would
silently double-count spend via `_drop_api_covered`). AST-level import
ban (docstrings/comments may still name them) + a fresh-clone simulation
that poisons those module names and still imports and calls the vendored
client. The vendored bodies themselves are proven identical to the legacy
originals by diff, not by tests (see the proposal Appendix).

Proposal: doc/improvements/archive/tooling/model_analytics_self_contained.md (S3).
"""

from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

BANNED = {"zai_usage_query", "opencode_usage_query"}
VENDORED = ["load_api_key", "_zai_parse_range", "fetch", "local_stats", "day_cost", "local_latency"]


def _tree() -> ast.Module:
    return ast.parse((PROJECT_ROOT / "helpers/analytics/model_analytics.py").read_text())


def test_no_sibling_imports() -> None:
    imported: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & BANNED), imported & BANNED


def test_vendored_names_defined() -> None:
    defined = {n.name for n in ast.walk(_tree()) if isinstance(n, ast.FunctionDef)}
    missing = [name for name in VENDORED if name not in defined]
    assert not missing, missing


def test_fresh_clone_simulation() -> None:
    """Poison the legacy module names, then import and call the client."""
    script = "\n".join(
        [
            "import sys",
            "sys.path.insert(0, '.')",
            "class Blocker:",
            "    def find_module(self, name, path=None):",
            "        return self if name in "
            "            {'zai_usage_query', 'opencode_usage_query'} else None",
            "    def load_module(self, name):",
            "        raise ImportError(f'blocked legacy module {name}')",
            "sys.meta_path.insert(0, Blocker())",
            "from helpers.analytics.model_analytics import day_cost, _zai_parse_range",
            "assert day_cost('glm-5.3-flash', 1000, 200, 50) == 0.000181",
            "start, end = _zai_parse_range('7d')",
            "assert (end - start).days == 6",
            "print('fresh-clone ok')",
        ]
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "fresh-clone ok" in proc.stdout


def test_local_latency_executes_synthetic_db(tmp_path):
    """S2 acceptance (range review B3): local_latency() must EXECUTE clean.

    The S608 directives used to sit INSIDE the f-string SQL literals, so
    the directive text itself reached sqlite (OperationalError:
    unrecognized token). Against a synthetic part/message db the query now
    runs and returns per-day stats; on the bug this test is red.
    """
    db = tmp_path / "zcode.sqlite"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE message (id TEXT PRIMARY KEY, time_created INTEGER, data TEXT);\n"
        "CREATE TABLE part (message_id TEXT, time_created INTEGER,"
        " time_updated INTEGER, data TEXT);\n"
    )
    con.execute(
        "INSERT INTO message VALUES (?, ?, ?)",
        ("m1", 1700000000000, json.dumps({"providerID": "zai"})),
    )
    parts = [
        ("m1", 1700000000000, None, json.dumps({"type": "text"})),
        ("m1", 1700000000000, None, json.dumps({"type": "step-start"})),
        (
            "m1",
            1700000002000,
            1700000002000,
            json.dumps({"type": "step-finish"}),
        ),
        (
            "m1",
            1700000003000,
            None,
            json.dumps({"type": "tool", "state": {"status": "completed"}}),
        ),
    ]
    con.executemany("INSERT INTO part VALUES (?, ?, ?, ?)", parts)
    con.commit()
    con.close()

    from helpers.analytics.model_analytics import local_latency

    stats = local_latency(db, 1699999990000, 1700000090000)
    assert stats, "local_latency returned no rows for a populated db"
    (cell,) = stats.values()
    assert cell["reqs"] == 1
    assert cell["tool_calls"] == 1
    assert cell["tool_errs"] == 0
    assert cell["lat"] == 2.0
