"""
Tests for helpers/misc/doc_query.py — the agent-facing CLI over the
doc_search sidecar index. Hermetic: tmp doc tree + sidecar, fake embedder
(mirrors tests/test_api_docs.py fixtures).
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "helpers"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from helpers.misc import doc_query  # noqa: E402
from helpers.maintenance import rebuild_doc_search as rds  # noqa: E402

pytestmark = [pytest.mark.integration]

_GUIDE = (
    "# Repo Guide\n"
    "\n"
    "## Cache Design\n"
    "how the cache warms up and persists rows\n"
    "\n"
    "## Other\n"
    "unrelated beta prose\n"
)


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    doc_root = tmp_path / "doc"
    doc_root.mkdir()
    (doc_root / "guide.md").write_text(_GUIDE, encoding="utf-8")
    db = tmp_path / "doc_search.db"
    monkeypatch.setattr(rds, "DOC_ROOT", doc_root)
    monkeypatch.setattr(rds, "DOC_DB", db)
    return db


@pytest.fixture
def fake_local(monkeypatch):
    from helpers.core import local_embedder as LE

    def _vec(text: str) -> list[float]:
        import math as _math

        v = [0.0] * 8
        if "cache" in text.lower():
            v[2] = 1.0
        n = _math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    monkeypatch.setattr(LE, "available", lambda: True)
    monkeypatch.setattr(LE, "embed_document", _vec)
    monkeypatch.setattr(LE, "embed_query", _vec)
    monkeypatch.setattr(LE, "DIM", 8)
    return LE


class TestDocQueryCli:
    def test_hits_print_path_line_and_snippet(self, seeded, fake_local, capsys):
        rds.rebuild(write=True)
        rc = doc_query.main(["cache design"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "doc/guide.md:3" in out
        assert "[Cache Design]" in out
        # <mark> tags are stripped for terminal output.
        assert "<mark>" not in out
        assert "cache warms up" in out

    def test_json_mode(self, seeded, fake_local, capsys):
        rds.rebuild(write=True)
        rc = doc_query.main(["cache", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["mode"] in ("hybrid", "bm25")
        assert payload["results"]
        assert {"path", "anchor", "section_title", "snippet"} <= set(payload["results"][0])

    def test_missing_index_exit_1_with_hint(self, seeded, capsys):
        rc = doc_query.main(["cache"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "rebuild_doc_search.py" in err

    def test_stale_index_still_answers_with_warning(self, seeded, fake_local, capsys):
        rds.rebuild(write=True)
        guide = Path(rds.DOC_ROOT) / "guide.md"
        future = time.time() + 10
        os.utime(guide, (future, future))
        rc = doc_query.main(["cache"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "guide.md" in captured.out

    def test_bm25_flag(self, seeded, fake_local, capsys):
        rds.rebuild(write=True)
        rc = doc_query.main(["cache", "--bm25", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["mode"] == "bm25"

    def test_no_hits_exit_0(self, seeded, fake_local, capsys):
        rds.rebuild(write=True)
        # BM25 leg: a token that matches nothing is genuinely empty.
        rc = doc_query.main(["zzz_never_matches", "--bm25"])
        assert rc == 0
        assert "no hits" in capsys.readouterr().err
        # Hybrid: cosine candidates mean an unmatched query still returns
        # the nearest chunks (standard vector-search posture) — no crash.
        rc = doc_query.main(["zzz_never_matches"])
        assert rc == 0
