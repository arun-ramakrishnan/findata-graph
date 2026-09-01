#!/usr/bin/env python3
"""Integration tests for the derive_insights CLI write path — the flagship
gap of doc/improvements/archive/testing/integration_fuzz_enhancement.md §4 A1.

The unit suite (test_derive_insights.py) pins the extractors and the
apply_* idempotency helpers in isolation; test_integration_derive_chain.py
stops at scan/apply_quotes. What NO test exercised is the real `_cli()`
flow over a tmp PROJECT_ROOT: DB writes + note rendering + OKF sources
splicing + the --stale-only gate + byte-stability of a second apply —
the exact surface maint-full steps 8/9 run in production.

Every test drives the genuine `_cli()` entrypoint with only the two
module seams the unit suite already established (``di.connect`` and
``di.PROJECT_ROOT``); the live vault/DB is never touched.
"""

from __future__ import annotations

import sqlite3
import sys
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.graph import derive_events as de  # noqa: E402
from helpers.graph import derive_insights as di  # noqa: E402
from helpers.core.frontmatter import (  # noqa: E402
    render_frontmatter,
    split_frontmatter,
    stringify_dates,
    yaml_safe_load,
)

pytestmark = [pytest.mark.integration]

_SCHEMA_SQL = """
CREATE TABLE entities(
    name TEXT PRIMARY KEY,
    entity_type TEXT,
    normalized_name TEXT,
    file_path TEXT,
    last_updated TEXT
);
CREATE TABLE quotes(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    quote_text TEXT NOT NULL,
    paraphrase TEXT,
    speaker_name TEXT,
    speaker_title TEXT,
    as_of_edition TEXT,
    source_ref TEXT NOT NULL,
    properties TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity, quote_text, as_of_edition)
);
CREATE TABLE company_metrics(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    metric_label TEXT,
    value_raw TEXT NOT NULL,
    value_num REAL,
    unit TEXT,
    period TEXT,
    as_of_edition TEXT,
    source_quote TEXT,
    source_ref TEXT NOT NULL,
    properties TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# The newsletter under a SOURCE tree (The_Chatter — not one of the derived
# Companies/Sectors/Super_Sectors trees) so the edition index resolves it:
# stem TC_Alpha, H1 title "The Chatter: Alpha Edition" (norm-key + post-colon
# tail both hit). The first paraphrase is guidance-shaped on purpose — the
# chain-contract test needs a bullet derive_events can extract AFTER the
# chatter render.
_NEWSLETTER = """\
# The Chatter: Alpha Edition

Welcome to the Alpha edition of The Chatter.

## FMCG

## Marico Ltd. | Large Cap | FMCG

Marico is a leading Indian consumer goods company.

## [Concall]

Management reiterated FY27 revenue growth guidance at 10-12% for the full year.

"We delivered 8% volume growth this quarter and gained over 300 basis points of market share."

— Saugata Gupta, MD & CEO

Margins remain resilient despite input cost pressure.

"We expect copra prices to stay range-bound through FY27."

-Saugata Gupta, MD & CEO
"""

_GUIDANCE_PARAPHRASE = (
    "Management reiterated FY27 revenue growth guidance at 10-12% for the full year."
)

_BASE_NOTE = """\
---
title: Marico
type: company
tags:
- entity_type/company
created: '2026-01-01'
last_modified: '2026-01-02'
---
# Marico

A leading Indian consumer goods company.
"""


class _Project:
    """Tmp PROJECT_ROOT + newsletter + company note + seeded DB, with the
    two derive_insights seams (connect / PROJECT_ROOT) monkeypatched."""

    def __init__(self, tmp_path: Path, monkeypatch, note_text: str = _BASE_NOTE):
        self.root = tmp_path
        self.nl = tmp_path / "findata" / "The_Chatter" / "TC_Alpha.md"
        self.nl.parent.mkdir(parents=True)
        self.nl.write_text(_NEWSLETTER, encoding="utf-8")
        self.note = tmp_path / "findata" / "Companies" / "FMCG" / "Marico.md"
        self.note.parent.mkdir(parents=True)
        self.note.write_text(note_text, encoding="utf-8")
        self.db_path = tmp_path / "test_insights.db"
        self._init_db()
        monkeypatch.setattr(di, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(di, "connect", self._connect)
        self.edition = di._edition_title(self.nl.stem, _NEWSLETTER)

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.executescript(_SCHEMA_SQL)
        conn.execute(
            "INSERT INTO entities(name, entity_type, file_path) "
            "VALUES ('Marico','company','findata/Companies/FMCG/Marico.md')"
        )
        conn.commit()
        conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def run(self, args: list[str]) -> tuple[int, str]:
        err = StringIO()
        with redirect_stderr(err):
            rc = di._cli([*args, str(self.nl)])
        return rc, err.getvalue()

    def quote_rows(self) -> list[tuple]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT id, entity, quote_text, paraphrase, speaker_name, "
            "speaker_title, as_of_edition, source_ref, properties, created_at "
            "FROM quotes ORDER BY id"
        ).fetchall()
        conn.close()
        return [tuple(r) for r in rows]

    def metric_rows(self) -> list[tuple]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT id, entity, metric_label, value_raw, value_num, unit, "
            "period, as_of_edition, source_quote, source_ref, properties, "
            "created_at FROM company_metrics ORDER BY id"
        ).fetchall()
        conn.close()
        return [tuple(r) for r in rows]

    def note_text(self) -> str:
        return self.note.read_text(encoding="utf-8")

    def note_fm(self) -> dict:
        _, fm_text, _ = split_frontmatter(self.note_text())
        return yaml_safe_load(fm_text)

    def pin_fresh_okf_state(self) -> None:
        """Simulate the post-render OKF state the gate trusts: a fresh
        generated.at plus a dated sources[] entry (the tmp tree has no git,
        so the splice itself can't produce last_modified)."""
        opener, fm_text, body = split_frontmatter(self.note_text())
        fm = yaml_safe_load(fm_text)
        gen = fm.setdefault("generated", {})
        gen["by"] = di._OKF_ACTOR
        gen["at"] = "2026-08-16T00:00:00Z"
        for s in fm.get("sources", []):
            if isinstance(s, dict):
                s["last_modified"] = "2026-08-15"
        self.note.write_text(render_frontmatter(stringify_dates(fm)) + body, encoding="utf-8")


@pytest.fixture
def insights_project(tmp_path, monkeypatch) -> _Project:
    return _Project(tmp_path, monkeypatch)


# --------------------------------------------------------------------------- #
# DB write path (--no-notes) + stable writes                                  #
# --------------------------------------------------------------------------- #
class TestApplyNoNotes:
    def test_apply_no_notes_writes_rows_and_second_apply_is_byte_stable(self, insights_project):
        """--apply --no-notes writes quotes + company_metrics; a second
        identical apply must not restamp created_at or reshuffle ids (the
        _stable_prefix_replace contract, 2026-08-21)."""
        p = insights_project
        rc, err = p.run(["--apply", "--no-notes"])
        assert rc == 0
        q1, m1 = p.quote_rows(), p.metric_rows()
        assert q1 and m1
        # The write boundary stores the canonical edition STEM, not the
        # display title (the OKF backfill discipline).
        assert {r[6] for r in q1} == {"TC_Alpha"}
        rc, _ = p.run(["--apply", "--no-notes"])
        assert rc == 0
        assert p.quote_rows() == q1
        assert p.metric_rows() == m1

    def test_no_notes_leaves_note_bytes_identical(self, insights_project):
        """--no-notes must not touch notes at all (the maint-full
        housekeeping contract)."""
        p = insights_project
        before = p.note_text()
        rc, _ = p.run(["--apply", "--no-notes"])
        assert rc == 0
        assert p.note_text() == before


# --------------------------------------------------------------------------- #
# Note rendering (--apply)                                                     #
# --------------------------------------------------------------------------- #
class TestApplyRendersNotes:
    def test_chatter_block_with_sentinels(self, insights_project):
        p = insights_project
        rc, err = p.run(["--apply"])
        assert rc == 0
        text = p.note_text()
        assert di._BEGIN in text and di._END in text
        assert f"## The Chatter — {p.edition}" in text
        assert di._markers_balanced(text)
        assert text.endswith("\n"), "writers must terminate notes with a newline (MD047)"
        assert "1 notes wrote" in err

    def test_key_figures_region_rendered(self, insights_project):
        p = insights_project
        rc, _ = p.run(["--apply"])
        assert rc == 0
        text = p.note_text()
        assert di._KF_BEGIN in text and di._KF_END in text
        assert di._KF_HEADING in text
        assert di._markers_balanced(text)

    def test_sources_spliced_with_stem_leg_link(self, insights_project):
        """OKF sources[] lands in frontmatter at render, and the chatter
        attribution carries the stem-leg footnote [[TC_Alpha]]."""
        p = insights_project
        rc, _ = p.run(["--apply"])
        assert rc == 0
        fm = p.note_fm()
        ids = {s.get("id"): s for s in fm.get("sources", [])}
        assert "TC_Alpha" in ids
        assert ids["TC_Alpha"]["resource"] == ("/findata/The_Chatter/TC_Alpha.md")
        text = p.note_text()
        assert "[^chatter-TC_Alpha]:" in text
        assert "[[TC_Alpha]]" in text

    def test_full_apply_idempotent_bytes_and_rows(self, insights_project):
        """Second full --apply: note bytes identical, DB rows keep their
        ids/created_at (byte-guard no-op — no generated.at restamp)."""
        p = insights_project
        rc, _ = p.run(["--apply"])
        assert rc == 0
        text1 = p.note_text()
        q1, m1 = p.quote_rows(), p.metric_rows()
        rc, err = p.run(["--apply"])
        assert rc == 0
        assert p.note_text() == text1
        assert p.quote_rows() == q1
        assert p.metric_rows() == m1
        # The byte-guard no-op: run 1 reported "1 notes wrote", run 2 must
        # report zero (byte-identical block + already-spliced sources).
        assert "0 notes wrote" in err

    def test_curation_safety_hand_written_block_preserved(self, tmp_path, monkeypatch):
        """A hand-written (non-sentinel) `## The Chatter — <edition>` block
        for the SAME edition blocks the auto block: hand text survives
        verbatim, no sentinel region is added, no duplication."""
        note = _BASE_NOTE + (
            "\n## The Chatter — The Chatter: Alpha Edition\n\n"
            "Hand-curated commentary that must survive.\n"
        )
        p = _Project(tmp_path, monkeypatch, note_text=note)
        rc, err = p.run(["--apply"])
        assert rc == 0
        text = p.note_text()
        assert "Hand-curated commentary that must survive." in text
        assert di._BEGIN not in text
        assert text.count("## The Chatter — ") == 1
        assert "1 edition blocks skipped" in err

    def test_dry_run_writes_nothing(self, insights_project):
        p = insights_project
        before = p.note_text()
        rc, err = p.run([])
        assert rc == 0
        assert "dry-run" in err
        assert p.quote_rows() == [] and p.metric_rows() == []
        assert p.note_text() == before


# --------------------------------------------------------------------------- #
# --stale-only gate (CLI level)                                                #
# --------------------------------------------------------------------------- #
class TestStaleOnlyCli:
    def test_stale_only_skips_fresh_note(self, insights_project):
        """A note at its fixed point (fresh render stamp, no newer source,
        scanned stem already in sources[]) is gated: zero writes, bytes
        untouched."""
        p = insights_project
        rc, _ = p.run(["--apply"])
        assert rc == 0
        p.pin_fresh_okf_state()
        pinned = p.note_text()
        rc, err = p.run(["--apply", "--stale-only"])
        assert rc == 0
        assert "gated by --stale-only" in err
        assert p.note_text() == pinned


# --------------------------------------------------------------------------- #
# Chain contract: step 8 (derive_insights) feeds step 9 (derive_events)        #
# --------------------------------------------------------------------------- #
class TestChainContract:
    def test_rendered_chatter_is_what_derive_events_extracts(self, insights_project):
        """maint-full runs derive-insights BEFORE derive-events because the
        events extractor reads the rendered chatter bullets out of company
        notes. Before the render there is no guidance event; after it, the
        guidance event's source_quote IS the rendered paraphrase bullet."""
        p = insights_project
        companies = p.root / "findata" / "Companies"
        before = [e for e in de.extract_from_prose(root=companies) if e.event_type == "guidance"]
        assert before == []

        rc, _ = p.run(["--apply"])
        assert rc == 0
        events = [e for e in de.extract_from_prose(root=companies) if e.event_type == "guidance"]
        assert len(events) == 1
        ev = events[0]
        assert ev.entity == "Marico"
        assert ev.source_quote is not None
        assert _GUIDANCE_PARAPHRASE in ev.source_quote
        assert ev.period == "FY27"
        assert ev.magnitude == "10-12%"
