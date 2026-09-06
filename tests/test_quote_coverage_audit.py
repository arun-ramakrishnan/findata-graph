"""Tests for the S0 quote coverage audit (helpers/validators/quote_coverage_audit.py).

Synthetic-content unit tests — no DB, no corpus dependency. The parity
gate, bucket attribution, coverage matcher, watchlist
lifecycle, and rule-candidate recurrence are exercised directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpers.graph import derive_insights as di
from helpers.validators import quote_coverage_audit as qca


NOTE = """---
title: Test Edition
---

# The Chatter: Test Edition

Some masthead prose.

## Software

Sector blurb content.

## Acme Ltd | Large Cap | Software

Acme descriptor.

## [Transcript]

"First quote line that is definitely long enough to count as an opening yes."

Prose between.

"Second quote line that is definitely long enough to count as an opening."

## Jane Smith, Chief Financial Officer

"Speaker-region quote line that is definitely long enough as an opening."

## On European Revival Despite Weak Earnings

"Prose-region quote line that is definitely long enough as an opening."

## Concall

"Bare-marker quote line that is definitely long enough as an opening."

## Zenith Chemicals | Mid Cap | Chemicals

"Zenith quote line that is definitely long enough to count as an opening."

- bullet item
"""


@pytest.fixture()
def content() -> str:
    return NOTE


class TestClassifyBoundaries:
    def test_parity_with_production(self, content):
        regions, parity = qca.classify_boundaries(content)
        production = [
            {"canonical": s.canonical_name, "heading_line": s.heading_line}
            for s in di.iter_company_sections(content)
        ]
        # Both Acme Ltd and Zenith Chemicals are company headings (pipe).
        assert parity == production
        assert [p["canonical"] for p in parity] == ["Acme", "Zenith Chemicals"]

    def test_region_kinds_post_s1(self, content):
        # Post-S1: markers, speaker, and prose headings are NON-boundaries —
        # their content joins the enclosing company section. They must not
        # split regions anymore (the pre-S1 g1_marker/g4_speaker/g4_prose
        # region kinds are gone by design; the funnel delta is the metric).
        regions, _ = qca.classify_boundaries(content)
        kinds = {r.kind for r in regions}
        assert "g5_masthead" in kinds          # preamble
        assert "g4_sector" in kinds            # ## Software
        assert "company_unresolved" in kinds   # resolution applied by caller
        assert "g1_marker" not in kinds
        assert "g4_speaker" not in kinds
        assert "g4_prose" not in kinds

    def test_resolution_rewrites_kind(self, content):
        # S2: resolution is the ladder's job (mirror of production
        # _extract_sections); assert_parity only gates classification parity.
        resolver = {"acme": "Acme Ltd"}
        regions, div = qca.assert_parity(content, resolver)
        assert div == []
        assert all(r.kind == "company_unresolved" for r in regions if r.canonical)
        ent, tier, sugg = di._resolve_ladder("Acme", resolver)
        assert (ent, tier, sugg) == ("Acme Ltd", "exact", [])

    def test_no_html_unescape_in_canonical(self):
        # The 2026-09-07 parity bug: unescaping `&amp;` before
        # canonicalization diverged from production. Byte-faithful mirror.
        note = "## Anya Polytech &amp; Fertilizers | Nano Cap | Fertilizers\nbody\n"
        _, parity = qca.classify_boundaries(note)
        assert parity[0]["canonical"] == "Anya Polytech &amp; Fertilizers"

    def test_bare_marker_absorbed_post_s1(self):
        note = "## .Concall\nbody\n## Recording\nmore\n"
        regions, _ = qca.classify_boundaries(note)
        # Bare markers are non-boundaries: no region split at all —
        # everything stays in the preamble region.
        assert len(regions) == 1 and regions[0].kind == "g5_masthead"


class TestOpeningsAndCoverage:
    def test_openings_count_and_curly_fold(self):
        text = (
            "\u201cCurly quote line long enough to be an opening for sure.\u201d\n"
            'plain "short"\n'
            '"A normal quote line that is long enough to be counted fine."\n'
        )
        opens = qca.opening_lines(text)
        assert opens == [1, 3]

    def test_opening_covered_prefix_containment(self):
        keys = qca.coverage_keys(["Total revenue reached a new record high of KRW 134 trillion, up"])
        assert qca.opening_covered(
            '"Total revenue reached a new record high of KRW 134 trillion, up by 43%."', keys
        )
        assert not qca.opening_covered('"Something else entirely different text here."', keys)

    def test_shape_of(self):
        assert qca._shape_of("- \"bullet quote\"") == "bullet/blockquote"
        assert qca._shape_of("> \"blockquote quote\"") == "bullet/blockquote"
        assert qca._shape_of('"short one"') == "sub-40-with-attribution"
        assert qca._shape_of('"Odd quote count line that is quite long indeed') == "unclosed/splice"
        assert qca._shape_of('"Normal shape quote line long enough here."') == "other"


class TestWatchlist:
    def _res(self, stem="Note", covered=0, openings=100, flagged=True, shapes=None):
        r = qca.NoteResult(
            tree="The_Chatter", stem=stem, path=f"findata/The_Chatter/{stem}.md",
            openings=openings, covered=covered, flagged=flagged,
            residual_shapes=shapes or {},
        )
        return r

    def test_open_and_close_lifecycle(self):
        wl = {"threshold": 0.95, "entries": {}}
        wl = qca.update_watchlist(wl, [self._res()], "2026-09-07")
        e = wl["entries"]["The_Chatter/Note"]
        assert e["status"] == "open" and e["runs_seen"] == 1
        wl = qca.update_watchlist(wl, [self._res(flagged=False, covered=99)], "2026-09-08")
        assert e["status"] == "closed"
        assert "recovered" in e["closed_by"]

    def test_rule_candidates_recurrence(self):
        wl = {"threshold": 0.95, "entries": {}}
        res = [self._res(shapes={"unclosed/splice": 5})]
        wl = qca.update_watchlist(wl, res, "2026-09-07")
        wl = qca.update_watchlist(wl, res, "2026-09-08")
        wl = qca.update_watchlist(wl, res, "2026-09-09")
        cands = qca.rule_candidates(wl, res)
        assert any(c["shape"] == "unclosed/splice" and c["runs"] >= 3 for c in cands)

    def test_no_candidates_below_thresholds(self):
        wl = {"threshold": 0.95, "entries": {}}
        res = [self._res(shapes={"other": 3})]
        wl = qca.update_watchlist(wl, res, "2026-09-07")
        assert qca.rule_candidates(wl, res) == []


class TestAuditNoteBuckets:
    def test_bucket_assignment(self, tmp_path, monkeypatch):
        # Minimal resolver: Acme resolves; Zenith does not.
        resolver = {"acme": "Acme"}
        db_rows = {}  # no rows -> coverage from walker keys
        p = tmp_path / "Note.md"
        p.parent.mkdir(exist_ok=True)
        p.write_text(NOTE)
        res = qca.audit_note(p, resolver, db_rows, threshold=0.95)
        b = res.buckets
        # Post-S1 the transcript/speaker/prose/bare-marker openings sit INSIDE
        # the resolved Acme section (absorbed) -> walker-extracted -> covered.
        # Only unresolved Zenith's opening remains unbuckets (G2).
        assert res.openings == 6
        assert res.covered == 5
        assert res.walker_rows == 5
        assert b.get("G2", 0) == 1          # Zenith unresolved
        assert b.get("G1", 0) == 0 and b.get("G4_speaker", 0) == 0
        assert res.unresolved_canonicals == ["Zenith Chemicals"]
        assert res.flagged
