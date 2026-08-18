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

Usage:
    python3 helpers/misc/backfill_okf_provenance.py                 # derived, dry-run
    python3 helpers/misc/backfill_okf_provenance.py --apply         # derived, write
    python3 helpers/misc/backfill_okf_provenance.py --sources --apply
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import yaml

# Repo root: helpers/misc/backfill_okf_provenance.py -> parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

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

_DERIVED_TREES = ("Companies", "Sectors", "Super_Sectors")
# Listing/chrome files: OKF §4 reserved names + the pipeline's image map.
_CHROME_FILES = {"image_map.md", "index.md", "log.md"}

_SERIES_RE = r"(the\s+chatter|points\s*(?:&|and)\s*figures|the\s+plotlines|plotlines)"
_EDITION_H_RE = re.compile(rf"^##\s+{_SERIES_RE}\s*[—:-]*(.+)$", re.M | re.I)
_SOURCE_FOOTER_RE = re.compile(r"^\*?Source:\s*(.+?)\*?\s*$", re.M | re.I)

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
    tags = list(dict.fromkeys(wanted + [_FLAT_TAG_MIGRATION[t] for t in migrated]
                              + rest + unknown_flat))
    return tags, bool(migrated), unknown_flat


def _norm(s: str) -> str:
    """Fuzzy-match key: NFKD, lowercase, all non-alphanumerics to spaces."""
    s = unicodedata.normalize("NFKD", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def _git_add_date(path: Path) -> str | None:
    """Date the file entered the repo (``git log --diff-filter=A``), or None.

    Memoized: the same edition note is queried once per derived note that
    references it, but its date never changes within a run.
    """
    key = str(path)
    if key in _GIT_DATE_MEMO:
        return _GIT_DATE_MEMO[key]
    git = shutil.which("git")
    d: str | None = None
    if git is not None:
        try:
            out = subprocess.run(  # noqa: S603  # resolved absolute path, fixed argv, no shell
                [git, "log", "--follow", "--diff-filter=A", "--format=%as",
                 "--", str(path)],
                capture_output=True, text=True, timeout=30, check=True,
                cwd=_REPO_ROOT,
            ).stdout.splitlines()
            d = out[-1].strip() if out else None
        except (OSError, subprocess.SubprocessError):
            d = None
    _GIT_DATE_MEMO[key] = d
    return d


_GIT_DATE_MEMO: dict[str, str | None] = {}


def _note_title(text: str, stem: str) -> str:
    """Frontmatter title -> first markdown heading -> file stem."""
    m = re.search(r"^title:\s*(.+)$", text, re.M)
    if m and m.group(1).strip():
        return m.group(1).strip()
    m = re.search(r"^#\s+(.+)$", text, re.M)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return stem


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


def _source_note_index(vault: Path) -> dict[str, Path]:
    """norm-key -> source-note path, over every non-derived findata tree."""
    index: dict[str, Path] = {}
    for tree in _source_trees(vault):
        for p in sorted(tree.rglob("*.md")):
            if p.name in _CHROME_FILES or "images" in p.parts:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            title = _note_title(text, p.stem)
            keys = {_norm(p.stem), _norm(title)}
            if ":" in title:
                keys.add(_norm(title.split(":")[-1]))
            for key in filter(None, keys):
                index.setdefault(key, p)
    return index


def _source_trees(vault: Path) -> list[Path]:
    """Every vault subtree that is NOT one of the derived trees."""
    return sorted(
        (d for d in vault.iterdir()
         if d.is_dir() and d.name not in _DERIVED_TREES and not d.name.startswith(".")),
        key=lambda d: d.name,
    )


def _resolve_editions(text: str, index: dict[str, Path]) -> list[Path]:
    """Source notes referenced by a derived note's body, deduped, sorted.

    Candidates: auto-block ``## <series> — <edition>`` headings and the
    trailing ``*Source: <edition>*`` footer. Each is normalized (series
    prefix, edition numbers, attribution/date tails stripped) and matched
    against the source-note index (exact key, then containment both ways
    for short/long title variants). Unmatchable candidates (Yahoo Finance,
    yfinance, "existing company note", ...) simply resolve to nothing.
    """
    cands = [m.group(2).strip() for m in _EDITION_H_RE.finditer(text)]
    cands += [f.strip() for f in _SOURCE_FOOTER_RE.findall(text)]
    matched: set[Path] = set()
    for c in cands:
        if not c:
            continue
        variants = [c]
        variants += re.split(r"[,;(]?\b[Ee]dition\s*#?\d+", c)
        variants += [re.split(
            r",\s*(?:Zerodha|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b)",
            c)[0]]
        m = re.match(rf"{_SERIES_RE}\s*[—:-]*(.*)$", c, re.I)
        if m:
            variants.append(m.group(1))
        for v in variants:
            k = _norm(v)
            if not k:
                continue
            if k in index:
                matched.add(index[k])
                break
            for key, p in index.items():
                if len(k) >= 8 and (k in key or key in k):
                    matched.add(p)
                    break
    return sorted(matched, key=lambda p: p.name)


def _edition_source_entry(src: Path, vault: Path) -> dict:
    """sources[] entry for an edition note (last_modified = git add-date)."""
    entry = {
        "id": src.stem,
        "resource": f"/{vault.name}/{src.relative_to(vault).as_posix()}",
        "title": _note_title(
            src.read_text(encoding="utf-8", errors="replace"), src.stem
        ),
    }
    d = _git_add_date(src)
    if d:
        entry["last_modified"] = d
    return entry


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
    base = max((e["last_modified"] for e in entries
                if isinstance(e.get("last_modified"), str)), default="")
    if not base:
        base = fallback
    return (_dt.date.fromisoformat(base) + _dt.timedelta(days=180)).isoformat()


def _merged_sources(fm: dict, text: str, index: dict, vault: Path) -> list[dict]:
    """Existing frontmatter sources + newly resolved edition entries, deduped."""
    entries = [_edition_source_entry(s, vault)
               for s in _resolve_editions(_body(text), index)]
    existing = ([e for e in fm["sources"]
                 if isinstance(e, dict)] if isinstance(
                     fm.get("sources"), list) else [])
    return existing + [e for e in entries
                       if e["id"] not in {x.get("id") for x in existing}]


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
        all_entries, _iso_date(fm.get("last_modified")
                               or fm.get("created")) or "")
    if not fm2["stale_after"]:
        return None
    return render_frontmatter(fm2) + _body(text)


def _stamp_derived(fm: dict, text: str, all_entries: list[dict]) -> str:
    """Backfill-stamp path: bump generated anchored to the note's own
    content date (last_modified/created), splicing in resolved sources."""
    lm = (_iso_date(fm.get("last_modified"))
          or _iso_date(fm.get("created")))
    at = f"{lm}T00:00:00Z" if lm else None
    base_text = text
    if all_entries and not isinstance(fm.get("sources"), list):
        fm2 = dict(fm)
        fm2["sources"] = all_entries
        base_text = render_frontmatter(fm2) + _body(text)
    return bump_generated(base_text, _ACTOR, now=at)


def backfill(vault: Path, *, apply: bool) -> dict[str, dict[str, int]]:
    """Stamp OKF provenance on every derived-tree note. See module docstring."""
    index = _source_note_index(vault)
    counts: dict[str, dict[str, int]] = {}
    for tree in _DERIVED_TREES:
        c = {"stamped": 0, "augmented": 0, "unchanged": 0,
             "skipped_real_writer": 0, "no_frontmatter": 0, "sourced": 0}
        for p in sorted((vault / tree).rglob("*.md")):
            text = p.read_text(encoding="utf-8", errors="replace")
            fm = _parse_fm(text)
            if fm is None:
                c["no_frontmatter"] += 1
                continue
            gen = fm.get("generated")
            real_writer = (isinstance(gen, dict)
                           and gen.get("by") not in (None, _ACTOR))
            all_entries = _merged_sources(fm, text, index, vault)
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
            d = _git_add_date(p)  # PDF lacks ModDate -> git date, not now
            return (f"{d}T00:00:00Z" if d else None), True
    d = _git_add_date(p)
    return (f"{d}T00:00:00Z" if d else None), False


def _prep_source_fm(text: str, p: Path, tree: Path,
                    vault: Path) -> tuple[dict, bool] | None:
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
        if (isinstance(gen, dict)
                and gen.get("by") not in (None, _ACTOR)):
            return None
    fm = dict(fm)
    fm.setdefault("type", "newsletter")
    if not str(fm.get("title") or "").strip():
        fm["title"] = _note_title(_body(text), p.stem)
    tags, migrated, unknown_flat = _source_tags(fm.get("tags"), tree.name)
    for t in unknown_flat:
        print(f"WARNING {p.relative_to(vault)}: unmigrated flat tag "
              f"{t!r} (no mapping; left in place)", file=sys.stderr)
    fm["tags"] = tags
    return fm, migrated


def backfill_sources(vault: Path, *, apply: bool,
                     repo_root: Path | None = None) -> dict[str, dict[str, int]]:
    """Construct/augment the producer OKF block on every source-tree note."""
    repo_root = repo_root or _REPO_ROOT
    counts: dict[str, dict[str, int]] = {}
    for tree in _source_trees(vault):
        c = {"stamped": 0, "unchanged": 0, "skipped_real_writer": 0,
             "pdf_linked": 0, "tag_migrations": 0}
        for p in sorted(tree.rglob("*.md")):
            if p.name in _CHROME_FILES or "images" in p.parts:
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
            new_text = bump_generated(
                render_frontmatter(fm) + _body(text), _ACTOR, now=at
            )
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
    print(f"total: {total} notes {verb} "
          f"({'apply' if apply else 'dry-run'})", file=sys.stderr)
    return 0


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Backfill OKF v0.2 provenance over the vault "
                    "(okf_adoption Q5).",
    )
    p.add_argument(
        "vault", nargs="?", default="findata",
        help="Vault root (repo-relative or absolute; default: findata).",
    )
    p.add_argument(
        "--apply", action="store_true",
        help="Write the stamps (default: dry-run counts only).",
    )
    p.add_argument(
        "--sources", action="store_true",
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

    counts = (backfill_sources if args.sources else backfill)(
        vault, apply=args.apply
    )
    return _report(counts, args.apply)


if __name__ == "__main__":
    raise SystemExit(_cli())
