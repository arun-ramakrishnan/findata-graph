#!/usr/bin/env python3
"""Canonical edition keys + fuzzy resolution over source-note trees.

F0 of doc/improvements/archive/okf/okf_activation.md: edition identity is
fragmented — ``sources[].id`` and wikilinks key on the note STEM,
``note_tags.note_path`` on the path, and ``quotes.as_of_edition`` is
free text that matches note titles only 28/71. The canonical key is the
note STEM. This module owns the shared machinery: key normalization,
the source-note index (stem/title keys), and free-text -> note
resolution. Consumers: the OKF backfill (edition references in derived
bodies -> ``sources[]``) and, per okf_activation C1, the coverage
report's ``quotes.as_of_edition`` bridge.

Resolution is honest, never guessing: unmatched strings resolve to
None (the caller reports them).
"""

from __future__ import annotations

try:
    from helpers.core.corpus import Corpus  # S1b shared walk

    _HAS_CORPUS = True
except ImportError:  # pragma: no cover
    Corpus = None  # type: ignore[assignment]
    _HAS_CORPUS = False

import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DERIVED_TREES = ("Companies", "Sectors", "Super_Sectors")
# Listing/chrome files: OKF §4 reserved names + the pipeline's image map.
CHROME_FILES = {"image_map.md", "index.md", "log.md"}

SERIES_RE = r"(the\s+chatter|points\s*(?:&|and)\s*figures|the\s+plotlines|plotlines)"
EDITION_HEADING_RE = re.compile(rf"^##\s+{SERIES_RE}\s*[—:-]*(.+)$", re.M | re.I)
SOURCE_FOOTER_RE = re.compile(r"^\*?Source:\s*(.+?)\*?\s*$", re.M | re.I)

# Guards against substring false-positives in the containment fallback:
# keys shorter than this are too generic to match by containment.
_MIN_CONTAINMENT_LEN = 8


def norm_key(s: str) -> str:
    """Fuzzy-match key: NFKD, lowercase, all non-alphanumerics to spaces."""
    s = unicodedata.normalize("NFKD", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def note_title(text: str, stem: str) -> str:
    """Frontmatter title -> first markdown heading -> file stem.

    The title value is parsed as a YAML scalar so quoting is stripped —
    a raw line grab keeps the quote characters in the value, and they
    then propagate into ``sources[].title`` entries as triple-quote soup
    (found 2026-09-04 once merged_sources started converging live
    editions to the canonical builder output).
    """
    m = re.search(r"^title:\s*(.+)$", text, re.M)
    if m and m.group(1).strip():
        raw = m.group(1).strip()
        try:
            import yaml

            parsed = yaml.safe_load(raw)
        except yaml.YAMLError:
            parsed = None
        if isinstance(parsed, str) and parsed.strip():
            return parsed.strip()
        return raw
    m = re.search(r"^#\s+(.+)$", text, re.M)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return stem


def source_trees(vault: Path) -> list[Path]:
    """Every vault subtree that is NOT one of the derived trees."""
    return sorted(
        (
            d
            for d in vault.iterdir()
            if d.is_dir() and d.name not in DERIVED_TREES and not d.name.startswith(".")
        ),
        key=lambda d: d.name,
    )


def source_note_index(vault: Path) -> dict[str, Path]:
    """norm-key -> source-note path, over every non-derived findata tree.

    Each note is keyed by its stem, its title, and (for "Series: Edition"
    titles) the post-colon tail, so free-text references in either shape
    hit the index directly.
    """
    index: dict[str, Path] = {}
    for tree in source_trees(vault):
        for p in sorted(tree.rglob("*.md")):
            if p.name in CHROME_FILES or "images" in p.parts:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            title = note_title(text, p.stem)
            keys = {norm_key(p.stem), norm_key(title)}
            if ":" in title:
                keys.add(norm_key(title.split(":")[-1]))
            for key in filter(None, keys):
                index.setdefault(key, p)
    return index


def _resolve_variants(c: str, index: dict[str, Path]) -> Path | None:
    """Match one candidate string's variant forms against the index."""
    variants = [c]
    variants += re.split(r"[,;(]?\b[Ee]dition\s*#?\d+", c)
    variants += [
        re.split(r",\s*(?:Zerodha|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b)", c)[0]
    ]
    m = re.match(rf"{SERIES_RE}\s*[—:-]*(.*)$", c, re.I)
    if m:
        variants.append(m.group(1))
    for v in variants:
        k = norm_key(v)
        if not k:
            continue
        if k in index:
            return index[k]
        for key, p in index.items():
            if len(k) >= _MIN_CONTAINMENT_LEN and (k in key or key in k):
                return p
    return None


def resolve_edition_string(
    cand: str,
    index: dict[str, Path],
    memo: dict[str, Path | None] | None = None,
) -> Path | None:
    """Resolve ONE free-text edition reference to a source note, or None.

    Variants tried in order: the raw string; the string minus an
    "Edition #N" tail; minus a ", Zerodha"/", <Month>" attribution tail;
    minus a leading series prefix. Each is normalized and matched
    against the index (exact key, then containment both ways for short/
    long title variants). First hit wins. ``memo`` (optional, caller-
    owned) caches candidates across calls — the containment fallback is
    O(index), and one edition string recurs across dozens of notes.
    """
    c = (cand or "").strip()
    if not c:
        return None
    if memo is not None and c in memo:
        return memo[c]
    hit = _resolve_variants(c, index)
    if memo is not None:
        memo[c] = hit
    return hit


def resolve_editions(
    text: str,
    index: dict[str, Path],
    memo: dict[str, Path | None] | None = None,
) -> list[Path]:
    """Source notes referenced by a derived note's body, deduped, sorted.

    Candidates: auto-block ``## <series> — <edition>`` headings and the
    trailing ``*Source: <edition>*`` footer. Unmatchable candidates
    (Yahoo Finance, yfinance, "existing company note", ...) simply
    resolve to nothing.
    """
    cands = [m.group(2).strip() for m in EDITION_HEADING_RE.finditer(text)]
    cands += [f.strip() for f in SOURCE_FOOTER_RE.findall(text)]
    matched: set[Path] = set()
    for c in cands:
        p = resolve_edition_string(c, index, memo)
        if p is not None:
            matched.add(p)
    return sorted(matched, key=lambda p: p.name)


# --------------------------------------------------------------------------- #
# sources[] entry builders (okf_sources_maintenance §3.1 — lifted from the   #
# OKF backfill so derive_insights can maintain sources[] at render time;     #
# the backfill reimports them).                                              #
# --------------------------------------------------------------------------- #
_GIT_DATE_MEMO: dict[str, str | None] = {}
# Repo-wide add-date map (path -> date the path entered the repo), built
# by ONE `git log --diff-filter=A` pass on first query; None until then.
# Per-path `--follow` remains as the fallback when the batch pass fails
# (giant repos, git errors) — note it can differ for RENAMED files (the
# batch gives the at-path add date); this vault never renames OCR notes.
_GIT_LOG_DATES: dict[str, str] | None = None
# Source-note titles keyed by path: edition_source_entry re-reads the
# source note per derived note that references it; only the first read
# per run is necessary.
_TITLE_MEMO: dict[str, str] = {}


def _batch_add_dates() -> dict[str, str] | None:
    """One-pass add dates for the whole repo, or None if git unavailable."""
    global _GIT_LOG_DATES
    if _GIT_LOG_DATES is not None:
        return _GIT_LOG_DATES
    git = shutil.which("git")
    if git is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603  # resolved absolute path, fixed argv, no shell
            [git, "log", "--diff-filter=A", "--name-only", "--format=%x00%as"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
            cwd=_REPO_ROOT,
        ).stdout
    except OSError, subprocess.SubprocessError:
        return None
    dates: dict[str, str] = {}
    date = ""
    for line in out.splitlines():
        if line.startswith("\x00"):
            date = line[1:]
        elif line and date:
            # log walks newest -> oldest; overwriting leaves the OLDEST
            # add date per path ("entered the repo"), matching the
            # per-path --follow intent for never-renamed files.
            dates[line] = date
    _GIT_LOG_DATES = dates
    return dates


def _git_add_date_follow(path: Path) -> str | None:
    """Per-path fallback: `git log --follow --diff-filter=A`."""
    git = shutil.which("git")
    if git is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603  # resolved absolute path, fixed argv, no shell
            [git, "log", "--follow", "--diff-filter=A", "--format=%as", "--", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
            cwd=_REPO_ROOT,
        ).stdout.splitlines()
        return out[-1].strip() if out else None
    except OSError, subprocess.SubprocessError:
        return None


def git_add_date(path: Path) -> str | None:
    """Date the file entered the repo (``git log --diff-filter=A``), or None.

    Memoized: the date is served from the repo-wide batch map built on
    the first query (one subprocess for every path), falling back to a
    per-path ``--follow`` query only when the batch pass failed.
    """
    key = str(path)
    if key in _GIT_DATE_MEMO:
        return _GIT_DATE_MEMO[key]
    d: str | None = None
    try:
        rel = path.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        rel = ""
    batch = _batch_add_dates()
    if batch is not None and rel:
        d = batch.get(rel)
    elif batch is None:
        d = _git_add_date_follow(path)
    _GIT_DATE_MEMO[key] = d
    return d


def edition_source_entry(src: Path, vault: Path) -> dict:
    """sources[] entry for an edition note (last_modified = git add-date,
    the accepted Q1 basis — identical to the 476 backfill-stamped entries)."""
    entry = {
        "id": src.stem,
        "resource": f"/{vault.name}/{src.relative_to(vault).as_posix()}",
    }
    tkey = str(src)
    if tkey not in _TITLE_MEMO:
        _TITLE_MEMO[tkey] = note_title(src.read_text(encoding="utf-8", errors="replace"), src.stem)
    entry["title"] = _TITLE_MEMO[tkey]
    d = git_add_date(src)
    if d:
        entry["last_modified"] = d
    return entry


def merged_sources(
    fm: dict,
    text: str,
    index: dict[str, Path],
    vault: Path,
    memo: dict[str, Path | None] | None = None,
) -> list[dict]:
    """Existing frontmatter sources + newly resolved edition entries, deduped.

    Entries for editions the body still references CONVERGE to the
    canonical builder output — title/resource/last_modified refresh when
    the edition note changes (e.g. the 2026-09-04 converter title
    repairs; without this, a repaired edition title never propagates into
    already-stamped notes). Entries for editions the body no longer
    references — including deleted editions — are kept verbatim (accepted
    Q2: historical pointers — advisories surface them, never silently
    rewritten). No cap (Q3) — the list IS the evidence trail.
    ``memo`` (optional, caller-owned) caches edition-string resolution
    across calls.
    """
    entries = [edition_source_entry(s, vault) for s in resolve_editions(_body(text), index, memo)]
    existing = (
        [e for e in fm["sources"] if isinstance(e, dict)]
        if isinstance(fm.get("sources"), list)
        else []
    )
    canonical = {e["id"]: e for e in entries}
    existing = [canonical.get(e.get("id"), e) for e in existing]
    return existing + [e for e in entries if e["id"] not in {x.get("id") for x in existing}]


def _body(text: str) -> str:
    """Note text minus its frontmatter block (leading blank lines kept)."""
    from helpers.core.frontmatter import split_frontmatter

    _, _, rest = split_frontmatter(text)
    if rest.startswith("---"):
        rest = rest[3:].lstrip(" \t")
        if rest.startswith("\n"):
            rest = rest[1:]
    return rest
