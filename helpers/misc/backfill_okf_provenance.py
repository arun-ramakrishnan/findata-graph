#!/usr/bin/env python3
"""One-off OKF v0.2 provenance backfill over the whole vault.

Two modes (default: derived):

- derived  — stamp the three schema-validated trees (Companies/Sectors/
  Super_Sectors): resolve each note's upstream newsletter edition(s) from
  its body (auto-block ``## <series> — <edition>`` headings + the trailing
  ``*Source: ...*`` footer) into ``sources[]`` entries pointing at the
  source markdown, with ``last_modified`` = the source note's git add-date
  (OCR ingest). ``stale_after`` then falls out of bump_generated's rule
  (max source last_modified + 180d); notes with no in-vault source anchor
  to the note's own ``last_modified`` + 180d. ``generated.at`` is the
  note's own last_modified — the real content date, NOT the backfill run
  (which is why the first, naive backfill stamped every note the same
  ``stale_after``). Notes already stamped by a real writer (e.g.
  derive_insights.py) keep their ``generated`` untouched; only
  ``sources``/``stale_after`` are augmented.

- sources (--sources) — stamp the OCR source trees (The_Chatter,
  The_PlotLines, Points_And_Figures, and any future non-derived findata/
  tree). These notes are primary sources with no writer that ever
  re-visits them, so the backfill CONSTRUCTS the producer block
  pdf_conv_md.py would emit today: ``type: newsletter``, title from
  frontmatter/first heading/stem, ``sources[]`` from a same-stem PDF under
  Reports/ when one exists (pdfinfo ModDate as the date), else no sources
  and ``generated.at`` = the note's git add-date. Never fabricates a
  resource: no PDF, no sources entry.

Actor honesty: stamps are ``process:okf_backfill`` — a metadata pass, not
a content derive; the next real machine rewrite overwrites ``generated``
(last-writer-wins). ``verified`` and every other frontmatter key survive
the YAML round-trip. Re-runs are no-ops when nothing changed (dates are
read from git/frontmatter, not the clock).

Role since okf_sources_maintenance §3.2: derive_insights maintains
``sources[]`` on the derived trees AT RENDER TIME (the splice), so for
rendered notes this backfill is no longer the routine path. It remains
the tool for pre-OKF notes a real writer never touches, the real-writer
``generated`` augment path, the source-tree stamping mode, and future OKF
schema migrations. Rarely needed; kept for bootstrap.

Usage:
    python3 helpers/misc/backfill_okf_provenance.py                 # derived, dry-run
    python3 helpers/misc/backfill_okf_provenance.py --apply         # derived, write
    python3 helpers/misc/backfill_okf_provenance.py --sources --apply
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

import yaml

# Repo root: helpers/misc/backfill_okf_provenance.py -> parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core import edition_index as _ei  # noqa: E402
from helpers.core.edition_index import (  # noqa: E402
    CHROME_FILES,
    DERIVED_TREES,
    merged_sources,
    note_title,
    source_note_index,
    source_trees,
)
from helpers.core.frontmatter import (  # noqa: E402
    bump_generated,
    moddate_to_iso_date,
    render_frontmatter,
    split_frontmatter,
    stringify_dates,
)
from helpers.pdf.pdf_conv_md import (  # noqa: E402
    _PUBLISHER_BY_SERIES,
    _pdf_metadata,
)

# OKF §7 actor convention: process:<id> for a non-content machine pass.
_ACTOR = "process:okf_backfill"

# Fixed migration for the one flat-tagged outlier (Scaling_Through_Slowdowns).
# Unknown flat tags are NOT migrated — reported and left in place; the
# newsletter schema gate surfaces them (never guess a namespace).
_FLAT_TAG_MIGRATION = {
    "zerodha": "publisher/zerodha",
    "chatter": "series/the_chatter",
}


def _source_tags(existing, tree_name: str) -> tuple[list[str], bool, list[str]]:
    """Merge machine tags for a source note (newsletter_notes_adoption S3).

    Returns (tags, migrated_any, unknown_flat_tags): ``series/<tree-slug>``
    + known ``publisher/`` first, then existing namespaced tags, then known
    flat tags migrated via the fixed map; unknown flat tags are kept verbatim
    and returned so the caller can report them.
    """
    series = re.sub(r"[^a-z0-9]+", "_", tree_name.lower()).strip("_")
    wanted = [f"series/{series}"]
    publisher = _PUBLISHER_BY_SERIES.get(series)
    if publisher:
        wanted.append(f"publisher/{publisher}")
    raw = [t for t in (existing or []) if isinstance(t, str)]
    migrated = [t for t in raw if t in _FLAT_TAG_MIGRATION]
    unknown_flat = [t for t in raw if "/" not in t and t not in _FLAT_TAG_MIGRATION]
    rest = [t for t in raw if "/" in t]
    tags = list(
        dict.fromkeys(wanted + [_FLAT_TAG_MIGRATION[t] for t in migrated] + rest + unknown_flat)
    )
    return tags, bool(migrated), unknown_flat


def _parse_fm(text: str) -> dict | None:
    """Frontmatter mapping, or None when the note has no parseable block."""
    opener, fm_text, _ = split_frontmatter(text)
    if not opener:
        return None
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return None
    return fm if isinstance(fm, dict) else None


def _body(text: str) -> str:
    """Note text minus its frontmatter block (leading blank lines kept)."""
    _, _, rest = split_frontmatter(text)
    if rest.startswith("---"):
        rest = rest[3:].lstrip(" \t")
        if rest.startswith("\n"):
            rest = rest[1:]
    return rest


def _iso_date(v) -> str | None:
    """YAML date object or ISO date string -> 'YYYY-MM-DD', else None."""
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", v.strip()):
        return v.strip()
    return None


def _stale_from_sources(entries: list[dict], fallback: str) -> str:
    """max(sources[].last_modified) + 180d else fallback + 180d
    (mirrors bump_generated's accepted Q3 rule)."""
    base = max(
        (e["last_modified"] for e in entries if isinstance(e.get("last_modified"), str)), default=""
    )
    if not base:
        base = fallback
    return (_dt.date.fromisoformat(base) + _dt.timedelta(days=180)).isoformat()


def _augment_real_writer(fm: dict, text: str, all_entries: list[dict]) -> str | None:
    """Real-writer path: keep ``generated``, add sources + stale_after only.

    Returns the rewritten note text, or None when there is no date basis
    for ``stale_after`` (caller skips). stringify first: parsed datetimes
    (generated.at, verified[].at) must re-render as ISO strings, not
    PyYAML's own datetime shape (bump_generated does this internally, but
    this path renders directly).
    """
    fm2 = stringify_dates(dict(fm))
    fm2["sources"] = all_entries
    fm2["stale_after"] = _stale_from_sources(
        all_entries, _iso_date(fm.get("last_modified") or fm.get("created")) or ""
    )
    if not fm2["stale_after"]:
        return None
    return render_frontmatter(fm2) + _body(text)


def _stamp_derived(fm: dict, text: str, all_entries: list[dict]) -> str:
    """Backfill-stamp path: bump generated anchored to the note's own
    content date (last_modified/created), splicing in resolved sources."""
    lm = _iso_date(fm.get("last_modified")) or _iso_date(fm.get("created"))
    at = f"{lm}T00:00:00Z" if lm else None
    base_text = text
    if all_entries and not isinstance(fm.get("sources"), list):
        fm2 = dict(fm)
        fm2["sources"] = all_entries
        base_text = render_frontmatter(fm2) + _body(text)
    return bump_generated(base_text, _ACTOR, now=at)


def backfill(vault: Path, *, apply: bool) -> dict[str, dict[str, int]]:
    """Stamp OKF provenance on every derived-tree note. See module docstring."""
    index = source_note_index(vault)
    counts: dict[str, dict[str, int]] = {}
    for tree in DERIVED_TREES:
        c = {
            "stamped": 0,
            "augmented": 0,
            "unchanged": 0,
            "skipped_real_writer": 0,
            "no_frontmatter": 0,
            "sourced": 0,
        }
        for p in sorted((vault / tree).rglob("*.md")):
            text = p.read_text(encoding="utf-8", errors="replace")
            fm = _parse_fm(text)
            if fm is None:
                c["no_frontmatter"] += 1
                continue
            gen = fm.get("generated")
            real_writer = isinstance(gen, dict) and gen.get("by") not in (None, _ACTOR)
            all_entries = merged_sources(fm, text, index, vault)
            if real_writer:
                if not all_entries:
                    c["skipped_real_writer"] += 1
                    continue
                new_text = _augment_real_writer(fm, text, all_entries)
                if new_text is None:
                    c["skipped_real_writer"] += 1
                    continue
                tag = "augmented"
            else:
                new_text = _stamp_derived(fm, text, all_entries)
                tag = "stamped"
            if new_text == text:
                c["unchanged"] += 1
                continue
            if apply:
                p.write_text(new_text, encoding="utf-8")
            c[tag] += 1
            if all_entries:
                c["sourced"] += 1
        counts[tree] = c
    return counts


def _source_at(fm: dict, p: Path, repo_root: Path) -> tuple[str | None, bool]:
    """generated.at for a source note + whether a PDF was linked.

    PDF ModDate when a same-stem PDF exists under Reports/ (also installs
    the sources[] entry), else the note's git add-date, else None (caller
    falls back to wall-clock now).
    """
    if not isinstance(fm.get("sources"), list):
        pdf = repo_root / "Reports" / f"{p.stem}.pdf"
        if pdf.exists():
            meta = _pdf_metadata(pdf)
            entry = {
                "id": p.stem,
                "resource": "/" + pdf.relative_to(repo_root).as_posix(),
                "title": meta.get("Title") or pdf.stem,
                "author": "process:pdf_conv_md",
            }
            lm = moddate_to_iso_date(meta.get("ModDate"))
            if lm:
                entry["last_modified"] = lm
            fm["sources"] = [entry]
            if lm:
                return f"{lm}T00:00:00Z", True
            d = _ei.git_add_date(p)  # PDF lacks ModDate -> git date, not now
            return (f"{d}T00:00:00Z" if d else None), True
    d = _ei.git_add_date(p)
    return (f"{d}T00:00:00Z" if d else None), False


def _prep_source_fm(text: str, p: Path, tree: Path, vault: Path) -> tuple[dict, bool] | None:
    """Frontmatter for one source note, ready for the OKF stamp.

    Preserves every existing key; defaults type/title; runs the namespaced
    tag pass (S3: series/publisher merge + flat-tag migration; unknown flat
    tags stay and are reported — the gate flags them). Returns (fm,
    migrated_any), or None when the note carries a real-writer
    ``generated`` stamp that must not be touched.
    """
    fm = _parse_fm(text)
    if fm is None:
        fm = {}
    else:
        gen = fm.get("generated")
        if isinstance(gen, dict) and gen.get("by") not in (None, _ACTOR):
            return None
    fm = dict(fm)
    fm.setdefault("type", "newsletter")
    if not str(fm.get("title") or "").strip():
        fm["title"] = note_title(_body(text), p.stem)
    tags, migrated, unknown_flat = _source_tags(fm.get("tags"), tree.name)
    for t in unknown_flat:
        print(
            f"WARNING {p.relative_to(vault)}: unmigrated flat tag "
            f"{t!r} (no mapping; left in place)",
            file=sys.stderr,
        )
    fm["tags"] = tags
    return fm, migrated


def backfill_sources(
    vault: Path, *, apply: bool, repo_root: Path | None = None
) -> dict[str, dict[str, int]]:
    """Construct/augment the producer OKF block on every source-tree note."""
    repo_root = repo_root or _REPO_ROOT
    counts: dict[str, dict[str, int]] = {}
    for tree in source_trees(vault):
        c = {
            "stamped": 0,
            "unchanged": 0,
            "skipped_real_writer": 0,
            "pdf_linked": 0,
            "tag_migrations": 0,
        }
        for p in sorted(tree.rglob("*.md")):
            if p.name in CHROME_FILES or "images" in p.parts:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            prepped = _prep_source_fm(text, p, tree, vault)
            if prepped is None:
                c["skipped_real_writer"] += 1
                continue
            fm, migrated = prepped
            if migrated:
                c["tag_migrations"] += 1
            at, pdf_linked = _source_at(fm, p, repo_root)
            if pdf_linked:
                c["pdf_linked"] += 1
            new_text = bump_generated(render_frontmatter(fm) + _body(text), _ACTOR, now=at)
            if new_text == text:
                c["unchanged"] += 1
                continue
            if apply:
                p.write_text(new_text, encoding="utf-8")
            c["stamped"] += 1
        counts[tree.name] = c
    return counts


def _report(counts: dict[str, dict[str, int]], apply: bool) -> int:
    verb = "stamped" if apply else "would stamp"
    total = 0
    for tree, c in counts.items():
        total += c.get("stamped", 0) + c.get("augmented", 0)
        bits = [f"{v} {k.replace('_', ' ')}" for k, v in c.items() if v]
        print(f"{tree}: {', '.join(bits)}", file=sys.stderr)
    print(f"total: {total} notes {verb} ({'apply' if apply else 'dry-run'})", file=sys.stderr)
    return 0


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Backfill OKF v0.2 provenance over the vault (okf_adoption Q5).",
    )
    p.add_argument(
        "vault",
        nargs="?",
        default="findata",
        help="Vault root (repo-relative or absolute; default: findata).",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write the stamps (default: dry-run counts only).",
    )
    p.add_argument(
        "--sources",
        action="store_true",
        help="Backfill the OCR source trees (The_Chatter &c.) instead of "
        "the derived Companies/Sectors/Super_Sectors trees.",
    )
    args = p.parse_args(argv)

    vault = Path(args.vault)
    if not vault.is_absolute():
        vault = _REPO_ROOT / vault
    if not vault.is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2

    counts = (backfill_sources if args.sources else backfill)(vault, apply=args.apply)
    return _report(counts, args.apply)


if __name__ == "__main__":
    raise SystemExit(_cli())
