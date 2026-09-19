#!/usr/bin/env python3
"""Tests for helpers/misc/author_subsector.py — the S2 surgical frontmatter
writer (subsector_authoring_pass proposal). Idempotency and body-preserving
surgery are the contract: the note body below the frontmatter must survive
byte-identical, and a re-run over an authored note is a no-op."""

from __future__ import annotations

from pathlib import Path

from helpers.misc.author_subsector import author_note

NOTE = """---
title: Test Corp
type: company
sector: Renewables
industry: Utilities - Renewable
market_cap: small_cap
normalized_name: Test_Corp
permalink: /companies/renewables/test_corp
tags:
- entity_type/company
created: '2026-01-01'
last_modified: '2026-09-19'
---

# Test Corp

Body text that must survive untouched.


"""


def _write(tmp_path: Path) -> Path:
    p = tmp_path / "Test_Corp.md"
    p.write_text(NOTE, encoding="utf-8")
    return p


def test_insert_after_industry(tmp_path: Path) -> None:
    p = _write(tmp_path)
    assert author_note(p, "Solar") == "inserted"
    lines = p.read_text(encoding="utf-8").splitlines()
    i = [n for n, ln in enumerate(lines) if ln.startswith("industry:")]
    s = [n for n, ln in enumerate(lines) if ln.startswith("subsector:")]
    assert len(s) == 1 and s[0] == i[0] + 1
    assert lines[s[0]] == "subsector: Solar"
    # body preserved byte-identical
    assert p.read_text(encoding="utf-8").split("---", 2)[2] == NOTE.split("---", 2)[2]


def test_idempotent_rerun_is_noop(tmp_path: Path) -> None:
    p = _write(tmp_path)
    author_note(p, "Solar")
    before = p.read_text(encoding="utf-8")
    assert author_note(p, "Solar") == "noop"
    assert p.read_text(encoding="utf-8") == before


def test_replace_existing_value(tmp_path: Path) -> None:
    p = _write(tmp_path)
    author_note(p, "Solar")
    assert author_note(p, "Hydro") == "replaced"
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines.count("subsector: Hydro") == 1
    assert not any(ln.startswith("subsector:") and ln != "subsector: Hydro" for ln in lines)


def test_no_frontmatter_is_reported(tmp_path: Path) -> None:
    p = tmp_path / "Bare.md"
    p.write_text("# just a body\n", encoding="utf-8")
    assert author_note(p, "Solar") == "no-frontmatter"
