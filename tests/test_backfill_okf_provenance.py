"""Unit tests for helpers/misc/backfill_okf_provenance.py (tmp vault only)."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import helpers.misc.backfill_okf_provenance as bk  # noqa: E402
from helpers.core import edition_index as ei  # noqa: E402
from helpers.misc.backfill_okf_provenance import (  # noqa: E402
    _ACTOR,
    backfill,
    backfill_sources,
)


def _fm_of(p: Path) -> dict:
    return yaml.safe_load(p.read_text().split("---")[1])


def _make_vault(root: Path) -> Path:
    """Mirror the production layout: <root>/findata/<trees>."""
    v = root / "findata"
    (v / "Companies" / "Tech").mkdir(parents=True)
    (v / "Sectors").mkdir()
    (v / "Super_Sectors").mkdir()
    (v / "The_Chatter").mkdir()
    (v / "The_PlotLines").mkdir()
    (v / "Companies" / "Tech" / "Acme.md").write_text(
        "---\n"
        "title: Acme Ltd\n"
        "type: company\n"
        "permalink: /companies/tech/acme\n"
        "sector: Tech\n"
        "tags:\n"
        "- entity_type/company\n"
        "ticker: null\n"
        "created: 2026-01-01\n"
        "verified:\n"
        "- by: human:arun\n"
        "  at: 2026-06-01T10:00:00Z\n"
        "---\n"
        "\n"
        "Hand-written body.\n"
    )
    (v / "Companies" / "Tech" / "NoFrontmatter.md").write_text("# bare\n")
    (v / "Sectors" / "Tech.md").write_text(
        "---\ntitle: Tech\ntype: sector\ncreated: 2026-02-02\n"
        "last_modified: 2026-03-05\n---\nbody\n"
    )
    (v / "Super_Sectors" / "X.md").write_text(
        "---\ntitle: X\ntype: super_sector\ncreated: 2026-02-02\n"
        "last_modified: 2026-03-05\n---\nbody\n"
    )
    (v / "The_Chatter" / "edition.md").write_text(
        "---\ntype: newsletter\n---\nbody\n"
    )
    return v


# ---------------------------------------------------------------------------
# derived mode
# ---------------------------------------------------------------------------
def test_dry_run_counts_and_writes_nothing(tmp_path):
    v = _make_vault(tmp_path)
    before = {p: p.read_bytes() for p in v.rglob("*.md")}
    counts = backfill(v, apply=False)
    assert counts["Companies"]["stamped"] == 1
    assert counts["Companies"]["no_frontmatter"] == 1
    assert counts["Sectors"]["stamped"] == 1
    assert counts["Super_Sectors"]["stamped"] == 1
    assert {p: p.read_bytes() for p in v.rglob("*.md")} == before


def test_generated_at_uses_last_modified_not_stamp_time(tmp_path):
    v = _make_vault(tmp_path)
    backfill(v, apply=True)
    # Sector fixture: last_modified 2026-03-05 (unquoted YAML date object).
    fm = _fm_of(v / "Sectors" / "Tech.md")
    assert fm["generated"]["at"] == "2026-03-05T00:00:00Z"
    assert fm["stale_after"] == (
        dt.date(2026, 3, 5) + dt.timedelta(days=180)
    ).isoformat()
    # Company fixture has no last_modified -> falls back to created.
    fm = _fm_of(v / "Companies" / "Tech" / "Acme.md")
    assert fm["generated"]["at"] == "2026-01-01T00:00:00Z"


def test_apply_preserves_verified_and_keys(tmp_path):
    v = _make_vault(tmp_path)
    backfill(v, apply=True)
    fm = _fm_of(v / "Companies" / "Tech" / "Acme.md")
    assert fm["verified"] == [{"by": "human:arun", "at": "2026-06-01T10:00:00Z"}]
    assert fm["title"] == "Acme Ltd"
    assert fm["tags"] == ["entity_type/company"]
    assert (v / "Companies" / "Tech" / "NoFrontmatter.md"
            ).read_text() == "# bare\n"
    assert (v / "The_Chatter" / "edition.md").read_text() == (
        "---\ntype: newsletter\n---\nbody\n"  # sources mode's job, not derived
    )


def test_edition_reference_becomes_sources_entry(tmp_path, monkeypatch):
    v = _make_vault(tmp_path)
    acme = v / "Companies" / "Tech" / "Acme.md"
    text = acme.read_text().replace(
        "Hand-written body.",
        "Hand-written body.\n\n## The Chatter — Big Edition\n\n- q\n",
    )
    acme.write_text(text)
    monkeypatch.setattr(ei, "_GIT_DATE_MEMO",
                        {str(v / "The_Chatter" / "edition.md"): None})
    monkeypatch.setattr(ei, "git_add_date",
                        lambda p: "2026-08-15"
                        if p.name == "edition.md" else None)
    counts = backfill(v, apply=True)
    fm = _fm_of(acme)
    assert fm["sources"] == [{
        "id": "edition",
        "resource": "/findata/The_Chatter/edition.md",
        "title": "edition",  # no heading -> stem fallback
        "last_modified": "2026-08-15",
    }]
    # Stale anchored to the SOURCE date, not the note's own created date.
    assert fm["stale_after"] == (
        dt.date(2026, 8, 15) + dt.timedelta(days=180)
    ).isoformat()
    # generated.at still the note's own content date.
    assert fm["generated"]["at"] == "2026-01-01T00:00:00Z"
    assert counts["Companies"]["sourced"] == 1


def test_real_writer_stamp_preserved_but_augmented(tmp_path, monkeypatch):
    v = _make_vault(tmp_path)
    acme = v / "Companies" / "Tech" / "Acme.md"
    text = acme.read_text().replace(
        "created: 2026-01-01\n",
        "created: 2026-01-01\nlast_modified: 2026-08-18\n", 1
    ).replace(
        "Hand-written body.",
        "Hand-written body.\n\n*Source: The Chatter — edition*",
    ).replace(
        "verified:",
        "generated:\n  by: derive_insights.py/v1\n"
        "  at: 2026-08-18T09:00:00Z\nverified:",
    )
    acme.write_text(text)
    monkeypatch.setattr(ei, "git_add_date",
                        lambda p: "2026-08-15"
                        if p.name == "edition.md" else None)
    backfill(v, apply=True)
    fm = _fm_of(acme)
    # generated untouched (real writer), sources+stale added.
    assert fm["generated"] == {"by": "derive_insights.py/v1",
                               "at": "2026-08-18T09:00:00Z"}
    assert fm["sources"][0]["id"] == "edition"
    assert fm["stale_after"] == (
        dt.date(2026, 8, 15) + dt.timedelta(days=180)
    ).isoformat()
    assert fm["verified"] == [{"by": "human:arun", "at": "2026-06-01T10:00:00Z"}]


def test_real_writer_without_sources_skipped(tmp_path):
    v = _make_vault(tmp_path)
    acme = v / "Companies" / "Tech" / "Acme.md"
    text = acme.read_text().replace(
        "verified:",
        "generated:\n  by: derive_insights.py/v1\n"
        "  at: 2026-08-18T09:00:00Z\nverified:",
    )
    acme.write_text(text)
    before = acme.read_text()
    counts = backfill(v, apply=True)
    assert counts["Companies"]["skipped_real_writer"] == 1
    assert acme.read_text() == before


def test_second_run_is_noop(tmp_path):
    v = _make_vault(tmp_path)
    backfill(v, apply=True)
    snap = {p: p.read_bytes() for p in v.rglob("*.md")}
    counts = backfill(v, apply=True)
    assert counts["Companies"]["stamped"] == 0
    assert counts["Sectors"]["stamped"] == 0
    assert {p: p.read_bytes() for p in v.rglob("*.md")} == snap


# ---------------------------------------------------------------------------
# sources mode
# ---------------------------------------------------------------------------
def test_sources_mode_constructs_frontmatter(tmp_path, monkeypatch):
    v = _make_vault(tmp_path)
    note = v / "The_Chatter" / "Hot_Edition.md"
    note.write_text("# The Chatter: Hot Edition\n\nprose\n")
    monkeypatch.setattr(ei, "git_add_date", lambda p: "2026-08-15")
    counts = backfill_sources(v, apply=True, repo_root=tmp_path)
    fm = _fm_of(note)
    assert fm["type"] == "newsletter"
    assert fm["title"] == "The Chatter: Hot Edition"
    assert fm["generated"] == {"by": _ACTOR, "at": "2026-08-15T00:00:00Z"}
    assert fm["stale_after"] == (
        dt.date(2026, 8, 15) + dt.timedelta(days=180)
    ).isoformat()
    assert "sources" not in fm  # no PDF -> no fabricated resource
    assert counts["The_Chatter"]["stamped"] == 2  # edition.md + Hot_Edition.md
    # existing frontmatter is augmented, not replaced
    fm = _fm_of(v / "The_Chatter" / "edition.md")
    assert fm["type"] == "newsletter"
    assert fm["generated"]["by"] == _ACTOR


def test_sources_mode_links_pdf_when_present(tmp_path, monkeypatch):
    v = _make_vault(tmp_path)
    (tmp_path / "Reports").mkdir()
    (tmp_path / "Reports" / "Hot_Edition.pdf").write_bytes(b"%PDF-fake")
    note = v / "The_Chatter" / "Hot_Edition.md"
    note.write_text("# Hot Edition\n\nprose\n")
    monkeypatch.setattr(
        bk, "_pdf_metadata",
        lambda p: {"Title": "Hot Edition", "ModDate": "Mon Aug 10 21:35:08 2026 IST"},
    )
    counts = backfill_sources(v, apply=True, repo_root=tmp_path)
    fm = _fm_of(note)
    assert fm["sources"] == [{
        "id": "Hot_Edition",
        "resource": "/Reports/Hot_Edition.pdf",
        "title": "Hot Edition",
        "author": "process:pdf_conv_md",
        "last_modified": "2026-08-10",
    }]
    assert fm["generated"]["at"] == "2026-08-10T00:00:00Z"
    assert fm["stale_after"] == (
        dt.date(2026, 8, 10) + dt.timedelta(days=180)
    ).isoformat()
    assert counts["The_Chatter"]["pdf_linked"] == 1


def test_sources_mode_tags_untagged_notes(tmp_path, monkeypatch):
    v = _make_vault(tmp_path)
    plot = v / "The_PlotLines" / "Deep_Dive.md"
    plot.write_text("# Deep Dive\n")
    monkeypatch.setattr(ei, "git_add_date", lambda p: "2026-08-15")
    backfill_sources(v, apply=True, repo_root=tmp_path)
    assert _fm_of(plot)["tags"] == ["series/the_plotlines",
                                    "publisher/zerodha"]
    assert _fm_of(v / "The_Chatter" / "edition.md")["tags"] == [
        "series/the_chatter", "publisher/zerodha"]


def test_sources_mode_migrates_flat_tags(tmp_path, monkeypatch):
    v = _make_vault(tmp_path)
    note = v / "The_Chatter" / "edition.md"
    note.write_text(
        "---\ntype: newsletter\ntags:\n- zerodha\n- chatter\n---\nbody\n"
    )
    monkeypatch.setattr(ei, "git_add_date", lambda p: "2026-08-15")
    counts = backfill_sources(v, apply=True, repo_root=tmp_path)
    fm = _fm_of(note)
    assert fm["tags"] == ["series/the_chatter", "publisher/zerodha"]
    assert counts["The_Chatter"]["tag_migrations"] == 1


def test_sources_mode_keeps_unknown_flat_tags_and_warns(
        tmp_path, monkeypatch, capsys):
    v = _make_vault(tmp_path)
    note = v / "The_Chatter" / "edition.md"
    note.write_text("---\ntype: newsletter\ntags:\n- mystery\n---\nbody\n")
    monkeypatch.setattr(ei, "git_add_date", lambda p: "2026-08-15")
    backfill_sources(v, apply=True, repo_root=tmp_path)
    fm = _fm_of(note)
    assert fm["tags"] == ["series/the_chatter", "publisher/zerodha",
                          "mystery"]  # kept; schema gate flags it
    assert "mystery" in capsys.readouterr().err


def test_sources_mode_preserves_namespaced_tags(tmp_path, monkeypatch):
    v = _make_vault(tmp_path)
    note = v / "The_Chatter" / "edition.md"
    note.write_text(
        "---\ntype: newsletter\ntags:\n- company/avanti_feeds\n---\nbody\n"
    )
    monkeypatch.setattr(ei, "git_add_date", lambda p: "2026-08-15")
    backfill_sources(v, apply=True, repo_root=tmp_path)
    assert _fm_of(note)["tags"] == ["series/the_chatter",
                                    "publisher/zerodha",
                                    "company/avanti_feeds"]


def test_sources_mode_idempotent(tmp_path, monkeypatch):
    v = _make_vault(tmp_path)
    monkeypatch.setattr(ei, "git_add_date", lambda p: "2026-08-15")
    backfill_sources(v, apply=True, repo_root=tmp_path)
    snap = {p: p.read_bytes() for p in v.rglob("*.md")}
    counts = backfill_sources(v, apply=True, repo_root=tmp_path)
    assert counts["The_Chatter"]["stamped"] == 0
    assert {p: p.read_bytes() for p in v.rglob("*.md")} == snap
