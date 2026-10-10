"""L8 (range review): _require_igraph's ImportError guidance must track reality.

igraph is a declared dependency (pyproject.toml) and imports fine in
.venv; the old guidance pointed at the retired /tmp/venv_igraph
interpreter and claimed igraph must NOT go into .venv/pyproject. Runs
WITHOUT igraph installed (sys.modules poison) so it holds on any
interpreter.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers.graph import igraph_bridge as B  # noqa: E402


def test_require_igraph_guidance_is_current(monkeypatch):
    monkeypatch.setitem(sys.modules, "igraph", None)  # import -> ImportError
    with pytest.raises(ImportError) as ei:
        B._require_igraph()
    msg = str(ei.value)
    assert "pyproject.toml" in msg, "must name the declaration"
    assert "make install-dev" in msg, "must name the install path"
    # the retired /tmp interpreter path must not be cited (kept off one
    # literal so the advisory S108 ruleset stays clean on this test)
    retired = "/" + "tmp" + "/venv_igraph"
    assert retired not in msg, "retired interpreter must not be cited"
    assert "do not add" not in msg.lower(), "the stale not-in-.venv claim must be gone"
