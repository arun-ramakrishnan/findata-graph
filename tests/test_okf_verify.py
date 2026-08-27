"""okf_readside N3: human verified[] stamping (helpers/misc/okf_verify.py).

Round-trip contract shared with the other OKF note-writers: every existing
key survives (especially generated), the body is byte-identical, re-run
writes nothing (idempotent per actor), and the result validates against the
frontmatter schema.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpers.core.frontmatter import split_frontmatter  # noqa: E402
from helpers.misc.okf_verify import main, verify_note  # noqa: E402
from helpers.validators.frontmatter_schema import (  # noqa: E402
    validate_frontmatter,
)


pytestmark = [pytest.mark.integration]

_NOTE = """---
title: Marico
type: company
generated:
  by: derive_insights.py/v1
  at: 2026-08-19T10:00:00Z
tags: [chatter/fmcg]
---

# Marico

Body stays byte-identical.
"""


@pytest.fixture()
def note(tmp_path):
    p = tmp_path / "Marico.md"
    p.write_text(_NOTE)
    return p


def _fm(p: Path) -> dict:
    return yaml.safe_load(split_frontmatter(p.read_text())[1])


class TestVerifyNote:
    def test_dry_run_default_writes_nothing(self, note):
        assert verify_note(note, "human:user").startswith("would stamp")
        assert "verified" not in _fm(note)

    def test_apply_stamps_and_preserves_everything(self, note):
        before_body = split_frontmatter(note.read_text())[2]
        status = verify_note(note, "human:user", apply=True)
        assert status == f"stamped: {note}"
        fm = _fm(note)
        v = fm["verified"]
        assert len(v) == 1 and v[0]["by"] == "human:user"
        assert v[0]["at"].endswith("Z")  # ISO 8601 UTC
        # generated untouched; all other keys intact.
        assert fm["generated"] == {"by": "derive_insights.py/v1",
                                   "at": "2026-08-19T10:00:00Z"}
        assert fm["title"] == "Marico" and fm["tags"] == ["chatter/fmcg"]
        assert split_frontmatter(note.read_text())[2] == before_body

    def test_idempotent_per_actor(self, note):
        verify_note(note, "human:user", apply=True)
        first = note.read_text()
        status = verify_note(note, "human:user", apply=True)
        assert status.startswith("already verified")
        assert note.read_text() == first  # zero-byte second write

    def test_second_actor_appends(self, note):
        verify_note(note, "human:user", apply=True)
        verify_note(note, "human:reviewer2", apply=True)
        assert len(_fm(note)["verified"]) == 2

    def test_no_frontmatter_note(self, tmp_path):
        p = tmp_path / "bare.md"
        p.write_text("# No YAML\n")
        assert verify_note(p, "human:user").startswith("no frontmatter")

    def test_result_validates_against_schema(self, note):
        verify_note(note, "human:user", apply=True)
        errs = validate_frontmatter(dict(_fm(note), sector="FMCG",
                                         normalized_name="Marico",
                                         permalink="/companies/fmcg/marico",
                                         created="2026-01-01",
                                         last_modified="2026-08-19",
                                         ticker=None, market_cap=None),
                                    "company")
        assert errs == []


class TestCli:
    def test_rejects_non_human_actor(self, note, capsys):
        with pytest.raises(SystemExit):
            main([str(note), "--by", "process:bot"])
        assert "human:" in capsys.readouterr().err

    def test_apply_flag_and_summary(self, note, capsys):
        rc = main([str(note), "--by", "human:user"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "would stamp" in out and "verified" not in _fm(note)
        rc = main([str(note), "--by", "human:user", "--apply"])
        assert rc == 0 and "stamped" in capsys.readouterr().out
