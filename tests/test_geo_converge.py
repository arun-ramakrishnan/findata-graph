#!/usr/bin/env python3
"""Tests for helpers/maintenance/geo_converge.py (country layer C2).

plan_note is a pure function over (note text, ticker-derived country) —
these pin the convergence contract: line surgery only (no YAML
round-trip churn), ticker authority on company notes, slop policy on
worklisted notes, and last_modified bumps only when something changed.
"""

from __future__ import annotations

from helpers.maintenance import geo_converge as gc  # noqa: E402

TODAY = "2026-09-09"


def _note(geo_key: str | None, geo_tags: list[str], last_mod: str = "'2026-01-01'") -> str:
    key_line = f"geography: {geo_key}\n" if geo_key is not None else ""
    tag_lines = "".join(f"- geography/{t}\n" for t in geo_tags)
    return (
        "---\n"
        "title: Acme\n"
        "type: company\n"
        "ticker: ACME.NS\n"
        f"{key_line}"
        "tags:\n"
        "- entity_type/company\n"
        "- sector/technology\n"
        f"{tag_lines}"
        "market_cap: null\n"
        f"created: '2026-01-01'\n"
        f"last_modified: {last_mod}\n"
        "---\n\n# Acme\n"
    )


class TestTickerBacked:
    def test_key_added_tag_already_correct(self):
        text = _note(None, ["india"])
        new, changes = gc.plan_note(text, "india", TODAY)
        assert "geography: india\n" in new
        assert new.count("- geography/") == 1
        assert changes == ["key_add"]
        # last_modified bumped
        assert f"last_modified: '{TODAY}'" in new

    def test_key_fixed_ticker_wins(self):
        text = _note("global", ["global"])
        new, changes = gc.plan_note(text, "usa", TODAY)
        assert "geography: usa\n" in new
        assert "- geography/usa" in new
        assert "- geography/global" not in new
        assert "key_fix" in changes and "tag_converge" in changes

    def test_sloppy_extra_tags_dedupe_onto_country(self):
        text = _note("india", ["india", "domestic_focused", "global"])
        new, changes = gc.plan_note(text, "india", TODAY)
        assert new.count("- geography/") == 1
        assert "- geography/india" in new
        assert "tag_converge" in changes

    def test_no_tag_block_inserts_after_last_tag(self):
        text = _note(None, [], last_mod="'2026-01-01'")
        new, changes = gc.plan_note(text, "uk", TODAY)
        assert "- geography/uk" in new
        assert "tag_converge" in changes

    def test_already_converged_is_noop(self):
        text = _note("india", ["india"])
        new, changes = gc.plan_note(text, "india", TODAY)
        assert new == text
        assert changes == []
        # last_modified NOT bumped when nothing changed
        assert "last_modified: '2026-01-01'" in new


class TestWorklisted:
    def test_regional_folds_to_india(self):
        text = _note(None, ["domestic_focused"])
        new, changes = gc.plan_note(text, None, TODAY)
        assert "- geography/india" in new
        assert "- geography/domestic_focused" not in new
        assert changes == ["regional_to_india"]

    def test_regional_alongside_india_just_dropped(self):
        text = _note(None, ["india", "domestic_focused"])
        new, changes = gc.plan_note(text, None, TODAY)
        assert new.count("- geography/") == 1
        assert "slop_drop" in changes

    def test_global_tag_dropped_on_worklist(self):
        text = _note(None, ["global"])
        new, changes = gc.plan_note(text, None, TODAY)
        assert "- geography/" not in new
        assert changes == ["slop_drop"]

    def test_global_key_dropped_on_worklist(self):
        text = _note("global", ["global"])
        new, changes = gc.plan_note(text, None, TODAY)
        assert "geography:" not in new
        assert "- geography/" not in new
        assert "key_drop" in changes and "slop_drop" in changes

    def test_country_valued_key_survives_on_worklist(self):
        """A human-assigned country key on a worklisted note is evidence — kept."""
        text = _note("usa", ["usa"])
        new, changes = gc.plan_note(text, None, TODAY)
        assert new == text
        assert changes == []


class TestSurgeryShape:
    def test_no_yaml_roundtrip_churn(self):
        """Only the geography + last_modified lines move — everything else
        byte-identical (the line-surgery contract)."""
        text = _note("global", ["global"])
        new, _ = gc.plan_note(text, "india", TODAY)
        old_lines = text.split("\n")
        new_lines = new.split("\n")
        assert old_lines[:3] == new_lines[:3]  # dashes/title/type untouched
        assert "# Acme" in new
        assert new.endswith("---\n\n# Acme\n")

    def test_no_frontmatter_is_noop(self):
        text = "# Just a body\n"
        new, changes = gc.plan_note(text, "india", TODAY)
        assert new == text
        assert changes == []
