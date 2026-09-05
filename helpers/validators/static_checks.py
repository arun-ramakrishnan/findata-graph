#!/usr/bin/env python3
"""Fast static checks for the FinData repo.

Runs in a few hundred ms on a laptop. Designed to be the FIRST gate in
`make qa` — catches low-effort regressions before the slower validators /
pytest / snapshot checks run.

Exits 0 on success, 1 if any check fails. Each failure is reported once,
with the offending file/line, so a single run surfaces every issue.

Checks (in order, cheapest first):
  1. Python syntax    — py_compile every .py under repo (excluding venv/git)
  2. JS syntax        — node --check every .js under static/ (skipped if no node)
  3. Merge markers + artifacts — stgit-stack-scoped conflict-marker scan
                        (applied patches' files; fallback: pruned repo walk)
  4. Helper shebangs  — every helpers/**/*.py starts with a #! line
  5. YAML parse       — every findata/**/*.md frontmatter parses as YAML
  6. Required files   — pytest.ini, pyproject.toml, Makefile exist

Usage:
    python3 helpers/validators/static_checks.py
"""

from __future__ import annotations

try:
    from helpers.core.corpus import Corpus  # S1b shared walk

    _HAS_CORPUS = True
except ImportError:  # pragma: no cover
    Corpus = None  # type: ignore[assignment]
    _HAS_CORPUS = False

import os
import py_compile
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# yaml / sqlite3 are optional in principle but always available in this
# project's env; import them up-front so the findata-specific checks below
# don't have to repeat the dance.
try:
    import sqlite3
except ImportError:  # pragma: no cover
    sqlite3 = None
try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# Prefer the C-accelerated loader when available (5-10x faster than the
# pure-Python safe_load). The four findata checks parse 1102 YAML frontmatters
# per run; this was the dominant static_checks cost.
if yaml is not None:
    try:
        from yaml import CSafeLoader as _SafeLoader
    except ImportError:  # pragma: no cover - PyYAML without libyaml
        from yaml import SafeLoader as _SafeLoader

REPO_ROOT = Path(__file__).resolve().parents[2]

# Patterns of files/dirs to skip when walking.
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}

# Files committed but should never be.
ARTIFACT_PATTERNS = re.compile(r"(__pycache__|\.pyc$|\.pyo$|\.DS_Store$|.*\.swp$|\.bak$)")

MERGE_MARKER_RE = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.MULTILINE)

# Cache/dependency dirs that must never be committed. Encountered ones are
# FLAGGED (advisory) and pruned from any walk — never recursed into.
STRAY_DIR_NAMES = {"__pycache__", "venv", ".venv", "node_modules", ".pytest_cache"}

# Binary suffixes a merge-marker scan can never match in. Guard for the
# FALLBACK walk only (the stgit stack scope touches source/doc files) —
# without it the walk reads+decodes ~83MB of gguf/duckdb/parquet per run
# (found 2026-08-21; the .gguf arrived with the local embedder).
_BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".pdf",
    ".zip",
    ".gz",
    ".db",
    ".pkl",
    ".gguf",
    ".duckdb",
    ".parquet",
    ".wal",
    ".so",
    ".node",
    ".ttf",
    ".woff",
    ".woff2",
    ".map",
}
# Dotfiles with no suffix — Path(".coverage").suffix is "", so these need a
# name check.
_BINARY_NAMES = {".coverage", ".DS_Store"}


def _is_text_candidate(p: Path) -> bool:
    """True when a merge-marker scan should read this file's content."""
    return p.suffix.lower() not in _BINARY_SUFFIXES and p.name not in _BINARY_NAMES


def _stack_files() -> set[Path] | None:
    """Union of files touched by the APPLIED stgit patches.

    ``stg series --applied`` -> ``stg files <name>`` per patch (git-status
    style output). Returns None when stgit is unavailable or REPO_ROOT
    isn't a stack (plain checkouts, tests) — callers fall back to the
    filesystem walk. Unapplied patches are deliberately skipped: their
    content isn't in the working tree, and a push conflict leaves markers
    in applied-surface files where the next run catches them.
    """
    try:
        series = subprocess.run(  # noqa: S603  # list-form call; controlled stgit CLI
            ["stg", "series", "--applied"],  # noqa: S607  # PATH-resolved stgit by design
            capture_output=True,
            text=True,
            check=True,
            cwd=str(REPO_ROOT),
        ).stdout
    except OSError, subprocess.CalledProcessError:
        return None
    names: list[str] = []
    for line in series.splitlines():
        # Applied patches print as '+ name' (or '> name' for the top).
        if line[:1] in "+>":
            name = line[1:].strip()
            if name:
                names.append(name)
    files: set[Path] = set()
    for name in names:
        try:
            out = subprocess.run(  # noqa: S603  # list-form call; controlled stgit CLI
                ["stg", "files", name],  # noqa: S607  # PATH-resolved stgit by design
                capture_output=True,
                text=True,
                check=True,
                cwd=str(REPO_ROOT),
            ).stdout
        except OSError, subprocess.CalledProcessError:
            continue
        for line in out.splitlines():
            # 'XY <path>' (single-letter status is common: 'M file').
            _status, _sep, path_part = line.partition(" ")
            path_part = path_part.strip()
            if " -> " in path_part:  # rename: take the destination
                path_part = path_part.rsplit(" -> ", 1)[1]
            if path_part:
                files.add(REPO_ROOT / path_part)
    return files


def _walk(root: Path, suffix: str):
    """Yield files under `root` matching `suffix`, skipping venv/cache."""
    for p in root.rglob(f"*{suffix}"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def _all_files():
    """Yield every file under repo, skipping venv/cache."""
    for p in REPO_ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix in SKIP_SUFFIXES:
            continue
        yield p


# --------------------------------------------------------------------------- #
# Individual checks                                                            #
# --------------------------------------------------------------------------- #
def check_python_syntax() -> list[str]:
    """py_compile every .py — surfaces syntax errors without running them."""
    failures = []
    for p in _walk(REPO_ROOT, ".py"):
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as e:
            failures.append(f"{p.relative_to(REPO_ROOT)}: {e}")
    return failures


def check_js_syntax() -> list[str]:
    """node --check every .js under static/. Skipped if node isn't installed."""
    if not _has_node():
        return []  # advisory skip; not a failure
    static = REPO_ROOT / "static"
    if not static.is_dir():
        return []
    js_files = [p for p in static.rglob("*.js") if not any(part in SKIP_DIRS for part in p.parts)]
    if not js_files:
        return []

    def _check_one(p: Path) -> str | None:
        rc = subprocess.run(  # noqa: S603  # list-form call; shell=False (default); args are constants/controlled paths
            ["node", "--check", str(p)],  # noqa: S607  # PATH-resolved interpreter/binary (python3/node/grep) by design
            capture_output=True,
            text=True,
        )
        if rc.returncode != 0:
            first = rc.stderr.strip().splitlines()[0] if rc.stderr else "unknown error"
            return f"{p.relative_to(REPO_ROOT)}: {first}"
        return None

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=min(len(js_files), 8)) as pool:
        for result in pool.map(_check_one, js_files):
            if result:
                failures.append(result)
    return failures


def check_stray_artifacts() -> list[str]:
    """No committed __pycache__, .pyc, .DS_Store, *.swp, *.bak.

    NOTE: This check is now combined with check_merge_markers into
    check_merge_markers_and_artifacts() for a single walk.
    This standalone function is kept for backward compatibility.
    """
    failures = []
    for p in _all_files():
        rel = str(p.relative_to(REPO_ROOT))
        if ARTIFACT_PATTERNS.search(rel):
            failures.append(rel)
    return failures


def check_helper_shebangs() -> list[str]:
    """Every helpers/**/*.py must start with a shebang line.

    Exceptions:
      - `__init__.py` files are package markers, not executables.
      - `_*`-prefixed modules are internal library modules (imported, never
        run directly) — e.g. ``helpers/graph/_edge_writer.py``. They don't
        take a shebang.
    """
    helpers = REPO_ROOT / "helpers"
    if not helpers.is_dir():
        return []
    failures = []
    for p in helpers.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.name == "__init__.py":
            continue  # package marker, not an executable
        if p.name.startswith("_"):
            continue  # private library module, not an executable
        try:
            first_line = p.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        except IndexError:
            failures.append(f"{p.relative_to(REPO_ROOT)}: empty file")
            continue
        if not first_line.startswith("#!"):
            failures.append(f"{p.relative_to(REPO_ROOT)}: missing shebang")
    return failures


def _scan_file_for_markers(p: Path, rel: str) -> tuple[list[str], list[str]]:
    """Scan one file for merge-conflict markers + stray artifact patterns.

    Returns (merge_failures, artifact_failures). Non-text files and unreadable
    paths are skipped best-effort. Stray-dir discovery is left to the caller so
    the stgit-stack and plain-checkout code paths can track them differently.
    """
    artifact_failures: list[str] = []
    merge_failures: list[str] = []
    if ARTIFACT_PATTERNS.search(rel):
        artifact_failures.append(rel)
    if not _is_text_candidate(p):
        return merge_failures, artifact_failures
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: S112  # best-effort; skip item on failure
        return merge_failures, artifact_failures
    if MERGE_MARKER_RE.search(text):
        merge_failures.append(f"{rel}: merge conflict markers")
    return merge_failures, artifact_failures


def check_merge_markers_and_artifacts() -> tuple[list[str], list[str]]:
    """Merge markers + stray artifacts, scoped to the APPLIED stgit stack.

    stgit surface (2026-08-21): markers enter through patches — a push or
    rebase conflict leaves <<<<<<< in the working tree and a hasty refresh
    folds it in — so the scan covers the union of files touched by applied
    patches (`_stack_files`). Every patch is checked while it lives in the
    stack; main is trusted by graduation (a marker in the merged base is a
    one-time manual `git grep '<<<<<<<'`, not a recurring gate cost).

    Fallback (no stgit / not a stack — plain checkouts, tests): walk the
    repo, pruning cache/dependency dirs (flagged as advisory, never
    recursed) and binary suffixes.

    Returns (merge_failures, advisories) — artifacts and stray dirs are
    advisory, matching the pre-existing contract.
    """
    merge_failures: list[str] = []
    artifact_failures: list[str] = []
    stray_dirs: set[str] = set()

    stack = _stack_files()
    if stack is not None:
        for p in sorted(stack):
            try:
                rel = p.relative_to(REPO_ROOT).as_posix()
            except ValueError:
                continue  # patch path outside the repo — nothing to scan
            stray_dirs.update(d for d in p.parts[:-1] if d in STRAY_DIR_NAMES)
            m, a = _scan_file_for_markers(p, rel)
            merge_failures.extend(m)
            artifact_failures.extend(a)
    else:
        for root, dirs, files in os.walk(REPO_ROOT):
            rel_root = Path(root).relative_to(REPO_ROOT)
            for d in list(dirs):
                if d in STRAY_DIR_NAMES or d == ".git":
                    if d in STRAY_DIR_NAMES:
                        stray_dirs.add((rel_root / d).as_posix())
                    dirs.remove(d)  # prune: never descend into caches/venvs
            for name in files:
                p = Path(root) / name
                rel = (rel_root / name).as_posix()
                m, a = _scan_file_for_markers(p, rel)
                merge_failures.extend(m)
                artifact_failures.extend(a)

    advisory = artifact_failures + sorted(stray_dirs)
    return merge_failures, advisory


def check_yaml_frontmatter() -> list[str]:
    """Every .md under findata/ must start with valid YAML frontmatter."""
    if yaml is None:
        return []  # advisory skip

    failures = []
    findata = REPO_ROOT / "findata"
    if not findata.is_dir():
        return failures
    for p in findata.rglob("*.md"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            failures.append(f"{p.relative_to(REPO_ROOT)}: read error {e}")
            continue
        if not text.startswith("---"):
            continue  # notes without FM are allowed (validators handle)
        end = text.find("\n---", 3)
        if end == -1:
            failures.append(f"{p.relative_to(REPO_ROOT)}: no closing ---")
            continue
        try:
            yaml.load(text[3:end], Loader=_SafeLoader)
        except yaml.YAMLError as e:
            failures.append(f"{p.relative_to(REPO_ROOT)}: {str(e)[:80]}")
    return failures


def check_required_files() -> list[str]:
    """Repo must contain pytest.ini, pyproject.toml, Makefile."""
    required = ["pytest.ini", "pyproject.toml", "Makefile"]
    return [f for f in required if not (REPO_ROOT / f).exists()]


# --------------------------------------------------------------------------- #
# findata-specific checks (require SQLite + YAML parsing)                      #
# --------------------------------------------------------------------------- #
# Canonical 42-sector set. Generated from entities.sector_classification;
# the canonical tag form is sector/<lowercase_of_this_value>.
CANONICAL_SECTORS = {
    "Agriculture",
    "Automotive",
    "Aviation",
    "Banking",
    "Building_Materials",
    "Capital_Markets",
    "Chemicals",
    "Consumer",
    "Defense",
    "Diagnostics",
    "Diversified",
    "Education_Training",
    "Electronics",
    "EMS_Manufacturing",
    "Energy",
    "Engineering_Capital_Goods",
    "Fertilizer",
    "Financial_Services",
    "Fintech_Payments",
    "FMCG",
    "Healthcare",
    "Hospitals",
    "Housing_Finance",
    "Infrastructure",
    "Insurance",
    "International",
    "Logistics",
    "Media_Entertainment",
    "Metals",
    "Mining",
    "NBFC",
    "Packaging",
    "Pharma",
    "Railways",
    "Real_Estate",
    "Renewables",
    "Retail",
    "Semiconductors",
    "Technology",
    "Telecommunications",
    "Textiles",
    "Travel",
}
CANONICAL_SECTOR_TAGS = {f"sector/{s.lower()}" for s in CANONICAL_SECTORS}

# Canonical cross-sector theme set (D4). Themes CUT ACROSS the GICS hierarchy
# (China+1 spans Electronics + EMS + Pharma API + Textiles), so they are a
# separate orthogonal dimension, not a sector child. Each was chosen because
# keyword evidence showed real membership density across company notes (see
# the alias map in helpers/graph/derive_themes.py). The canonical tag form is
# investment_theme/<lowercase_of_this_value> (the existing namespace, synced
# via sync_tags). Curated, not extracted — mirrors the CANONICAL_SECTORS
# discipline so theme membership is high-precision, not free-text sprawl.
CANONICAL_THEMES = {
    "China_Plus_One",
    "PLI_Scheme",
    "Premiumization",
    "EV_Transition",
    "Data_Center_Infrastructure",
    "Renewable_Energy",
    "Make_In_India",
    "Defense_Indigenization",
    "Battery_Energy_Storage",
    "Electronic_Manufacturing_Services",
    "API_Manufacturing",
    "Beverage_Portfolio",
}
CANONICAL_THEME_TAGS = {f"investment_theme/{t.lower()}" for t in CANONICAL_THEMES}

# D7 — the temporal-spine event vocabulary. Events are timestamped happenings
# (acquisitions, JVs, guidance, management changes) giving every company a
# timeline. Curated, not free-text — mirrors the CANONICAL_SECTORS/THEMES
# discipline so the events table stays a controlled vocabulary. The integrity
# check rejects any event_type outside this set. `earnings` is deliberately
# absent: no reliable date source in the corpus (deferred to D8 transcripts).
CANONICAL_EVENT_TYPES = frozenset({"acquisition", "jv", "guidance", "management_change"})


def _db_path() -> Path:
    return REPO_ROOT / "memory" / "research.db"


def _parse_frontmatter(text: str) -> dict | None:
    """Return parsed YAML frontmatter dict, or None if absent/unparseable."""
    if yaml is None or not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    try:
        return yaml.load(text[3:end], Loader=_SafeLoader)
    except yaml.YAMLError:
        return None


def check_orphan_markdown_files() -> list[str]:
    """Markdown files in findata/Companies or findata/Sectors whose
    normalized_name isn't in entities.normalized_name. Reverse of the
    DB->filesystem check in database_integrity_check.py."""
    db = _db_path()
    if not db.exists() or sqlite3 is None:
        return []  # live DB unavailable; skip
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from helpers.core.db import connect as _db_connect

    conn = _db_connect(str(db))
    try:
        rows = conn.execute(
            "SELECT normalized_name FROM entities WHERE normalized_name IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    known = {r[0] for r in rows}

    failures = []
    for sub in ("Companies", "Sectors"):
        root = REPO_ROOT / "findata" / sub
        if not root.is_dir():
            continue
        for p in root.rglob("*.md"):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            normalized = p.stem  # filename minus .md
            if normalized not in known:
                failures.append(str(p.relative_to(REPO_ROOT)))
    return failures


def check_tag_canonicalization() -> tuple[list[str], list[str]]:
    """Two-tier check on sector/* tags in note YAML.

    Fatal: tag value has wrong casing (any uppercase letter). The canonical
           form is sector/<lowercase>. Casing drift splits tag-based queries.
    Advisory: tag value is lowercase but doesn't match any canonical sector.
              These are synonyms (e.g. sector/telecom vs sector/telecommunications)
              and need human judgment to merge.
    """
    fatal, advisory = [], []
    findata = REPO_ROOT / "findata"
    if not findata.is_dir():
        return fatal, advisory
    for p, _text, fm in _iter_findata_md(findata):
        if not fm:
            continue
        f, a = _check_tags_one(p, fm)
        fatal.extend(f)
        advisory.extend(a)
    return fatal, advisory


def _check_tags_one(p: Path, fm: dict) -> tuple[list[str], list[str]]:
    """Per-file tag-canonicalization logic (shared by standalone + combined)."""
    tags = fm.get("tags") or []
    if not isinstance(tags, list):
        return [], []
    fatal, advisory = [], []
    rel = p.relative_to(REPO_ROOT)
    for tag in tags:
        if not isinstance(tag, str) or not tag.startswith("sector/"):
            continue
        value = tag.split("/", 1)[1]
        if value != value.lower():
            fatal.append(f"{rel}: tag '{tag}' should be lowercase")
        elif tag not in CANONICAL_SECTOR_TAGS:
            advisory.append(f"{rel}: tag '{tag}' not in canonical set")
    return fatal, advisory


def check_permalink_sector_consistency() -> tuple[list[str], list[str]]:
    """Company note permalinks must match the sector dir the file lives in.

    Fatal: permalink's sector slug differs from the file's parent dir.
    Catches both misclassification (file in wrong sector dir) and stale
    permalinks (file moved but permalink not updated).

    Note: prior to #7 normalization (Jul 2026) this was advisory because
    399 permalinks were either flat or used hyphen slugs. Post-normalization
    it's fatal so future drift fails fast.
    """
    failures, advisory = [], []
    companies = REPO_ROOT / "findata" / "Companies"
    if not companies.is_dir():
        return failures, advisory
    for p, _text, fm in _iter_findata_md(companies):
        if not fm:
            continue
        failures.extend(_check_permalink_one(p, fm))
    return failures, advisory


def _check_permalink_one(p: Path, fm: dict) -> list[str]:
    """Per-file permalink/sector-dir logic (shared by standalone + combined)."""
    permalink = fm.get("permalink")
    if not permalink or not isinstance(permalink, str):
        return []
    sector_dir = p.parent.name
    expected_slug = sector_dir.lower()
    rel = p.relative_to(REPO_ROOT)
    permalink_clean = permalink.lstrip("/")
    if permalink_clean.startswith("companies/"):
        parts = permalink_clean.split("/", 2)
        if len(parts) >= 2 and parts[1] != expected_slug:
            return [f"{rel}: permalink '{permalink}' sector='{parts[1]}' != dir '{expected_slug}'"]
    return []


def check_date_sanity() -> list[str]:
    """YAML frontmatter: last_modified must be >= created (when both present)."""
    failures = []
    findata = REPO_ROOT / "findata"
    if not findata.is_dir():
        return failures
    for p, _text, fm in _iter_findata_md(findata):
        if not fm:
            continue
        failures.extend(_check_date_one(p, fm))
    return failures


def _check_date_one(p: Path, fm: dict) -> list[str]:
    """Per-file date-sanity logic (shared by standalone + combined)."""
    created = fm.get("created")
    modified = fm.get("last_modified")
    if not created or not modified:
        return []
    c = str(created).strip("'\"")
    m = str(modified).strip("'\"")
    if m < c:
        return [f"{p.relative_to(REPO_ROOT)}: last_modified ({m}) < created ({c})"]
    return []


def _iter_findata_md(root: Path):
    """Single-walk generator: yield (path, text, frontmatter_or_None) per .md.

    Reads each file once and parses YAML frontmatter once, so callers don't
    repeat the I/O + parse cost. Used by the three findata YAML checks to
    avoid 3× walks of the 1102-file corpus.
    """
    for p in root.rglob("*.md"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: S112  # best-effort; skip item on failure
            continue
        yield p, text, _parse_frontmatter(text)


def check_findata_yaml() -> tuple[list[str], list[str]]:
    """Combined single-walk runner for the three findata YAML checks.

    Replaces check_tag_canonicalization + check_permalink_sector_consistency +
    check_date_sanity in the CHECKS list so the 1102-file corpus is walked,
    read, and YAML-parsed exactly ONCE instead of three times. The individual
    check functions remain available for tests / targeted use.
    """
    fatal, advisory = [], []
    findata = REPO_ROOT / "findata"
    companies = REPO_ROOT / "findata" / "Companies"
    if not findata.is_dir():
        return fatal, advisory
    for p, _text, fm in _iter_findata_md(findata):
        if not fm:
            continue
        # tag canonicalization (all of findata)
        f, a = _check_tags_one(p, fm)
        fatal.extend(f)
        advisory.extend(a)
        # date sanity (all of findata)
        fatal.extend(_check_date_one(p, fm))
        # permalink/sector (Companies only)
        if companies.is_dir() and p.is_relative_to(companies):
            fatal.extend(_check_permalink_one(p, fm))
    return fatal, advisory


def check_frontmatter_schema_contract() -> tuple[list[str], list[str]]:
    """B1: JSON-Schema structural validation of note frontmatter.

    Thin wrapper over helpers/validators/frontmatter_schema.py, which loads
    doc/okf/frontmatter.<type>.v1.json and checks key presence, value
    types, formats/enums and rogue keys for Companies/Sectors/Super_Sectors
    notes (the frontmatter-bearing note types). Degrades to an advisory when
    the dev-only jsonschema package is absent.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from helpers.validators.frontmatter_schema import check_frontmatter_schema

    return check_frontmatter_schema()


def _check_archived_proposals(archive_dir: Path, fatal: list[str]) -> None:
    """Archived proposals must be status: executed with dates + number;
    a header without frontmatter means an un-backfilled proposal."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from helpers.validators.frontmatter_schema import (
        _has_proposal_header,
        parse_frontmatter,
    )

    if not archive_dir.is_dir():
        return
    for p in sorted(archive_dir.rglob("*.md")):
        if p.name == "README.md":
            continue
        fm = parse_frontmatter(p)
        if fm is None:
            if _has_proposal_header(p):
                fatal.append(
                    f"archive/{p.relative_to(archive_dir)}: proposal header "
                    f"present but no frontmatter block (backfill it)"
                )
            continue
        if fm.get("status") != "executed":
            fatal.append(
                f"archive/{p.relative_to(archive_dir)}: archived proposal must be status: executed"
            )
        if fm.get("executed") is None or fm.get("completed_md") is None:
            fatal.append(
                f"archive/{p.relative_to(archive_dir)}: executed proposal "
                f"needs an executed date and completed_md number"
            )


def _check_live_list_refs(proposals_dir: Path, live: set[str], fatal: list[str]) -> None:
    """The proposals README live list may only reference files that
    exist in proposals/ (archived entries belong in archive/README.md)."""
    readme = proposals_dir / "README.md"
    if not readme.is_file():
        return
    text = readme.read_text(encoding="utf-8", errors="replace")
    section = text.split("## Current live proposals", 1)[-1]
    for name in re.findall(r"`([\w./-]+\.md)`", section):
        if "/" in name:
            continue  # cross-references to archive paths, not live entries
        if name not in live:
            fatal.append(
                f"proposals/README live list references {name}, which is "
                f"not in proposals/ (archived entries belong in "
                f"archive/README.md)"
            )


def check_proposal_lifecycle() -> tuple[list[str], list[str]]:
    """P0 (corpus_uniformity S3): proposal frontmatter agrees with its
    directory and itself — every live proposals/*.md (README excluded)
    is status: proposed with null executed/completed_md; every archived
    proposal (frontmatter OR bold-line header present) is status:
    executed with a real executed date and completed_md number; the
    proposals README live list references only files that exist there."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from helpers.validators.frontmatter_schema import parse_frontmatter

    fatal: list[str] = []
    proposals_dir = REPO_ROOT / "doc" / "improvements" / "proposals"
    archive_dir = REPO_ROOT / "doc" / "improvements" / "archive"
    if not proposals_dir.is_dir():
        return fatal, []

    live: set[str] = set()
    for p in sorted(proposals_dir.glob("*.md")):
        if p.name == "README.md":
            continue
        live.add(p.name)
        fm = parse_frontmatter(p)
        if fm is None:
            fatal.append(f"{p.name}: live proposal without frontmatter block")
            continue
        if fm.get("status") != "proposed":
            fatal.append(f"{p.name}: live proposal must be status: proposed")
        if fm.get("executed") is not None or fm.get("completed_md") is not None:
            fatal.append(f"{p.name}: proposed proposal must keep executed/completed_md null")

    _check_archived_proposals(archive_dir, fatal)

    _check_live_list_refs(proposals_dir, live, fatal)

    return fatal, []


def check_okf_conformance_contract() -> tuple[list[str], list[str]]:
    """OKF v0.2 §11 sweep (doc/okf/README.md) — whole-vault, ADVISORY-ONLY.

    Thin wrapper over frontmatter_schema.check_okf_conformance: every
    non-reserved findata note must carry parseable frontmatter with a
    non-empty ``type`` (OKF §11's two hard rules), newsletter OKF blocks
    get the producer-shape check (actor/at/bundle-relative resources), and
    the provenance census (trust tiers + staleness) reports adoption
    progress. Advisory-only in ``make qa`` by design: the B1 schema check
    (check_frontmatter_schema_contract) already gates the derived trees,
    and OKF §11 forbids consumers from rejecting over optional-key issues
    on the OCR-source trees — missing ``type`` on a newsletter surfaces in
    ``--okf`` CLI output rather than failing the build.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from helpers.validators.frontmatter_schema import check_okf_conformance

    fatal, advisory = check_okf_conformance()
    # Downgrade the §11 structural fatals to advisories for qa (see
    # docstring); the CLI --okf mode keeps them fatal for manual runs.
    for line in fatal:
        advisory.append(line)
    return [], advisory


def check_dependency_pinning() -> tuple[list[str], list[str]]:
    """Advisory only: [project].dependencies using loose (>=, >, <, <=) pins.

    Sourced from pyproject.toml since the dependency manifest migrated there
    (2026-08-13); bare names with no version are allowed, only range operators
    trigger the advisory.
    """
    advisory: list[str] = []
    pp = REPO_ROOT / "pyproject.toml"
    if not pp.exists():
        return [], advisory
    # requires-python >= 3.14 guarantees tomllib is always available.
    try:
        import tomllib as toml

        data = toml.loads(pp.read_text(encoding="utf-8"))
        deps = (data.get("project", {}) or {}).get("dependencies", []) or []
    except Exception:
        return [], advisory
    for dep in deps:
        if any(op in dep for op in (">=", ">", "<=", "<")) and "==" not in dep:
            advisory.append(f"pyproject.toml: dependency '{dep}' is unpinned (use ==)")
    return [], advisory


# --------------------------------------------------------------------------- #
# Runner                                                                       #
# --------------------------------------------------------------------------- #
def _has_node() -> bool:
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)  # noqa: S607  # PATH-resolved interpreter/binary (python3/node/grep) by design
        return True
    except subprocess.CalledProcessError, FileNotFoundError:
        return False


def check_sqlite_helper_usage() -> list[str]:  # noqa: C901
    """P0: every sqlite3.connect outside helpers/core/db.py must be allowlisted.

    The allowlist covers legitimate ephemeral/temp DBs and the helper itself:
      - helpers/core/db.py  (the definition)
      - helpers/maintenance/db_maint.py     (backup pairs + VACUUM/checkpoint
        isolation_level=None — 5 sites: 293, 339, 340, 435, 746)
    Any other sqlite3.connect is a violation (should use helpers.core.db.connect).
    """
    allowlist_prefixes = (
        "helpers/core/db.py",
        "helpers/maintenance/db_maint.py",
        # Bench probes: mode=ro URI connections for measurement SELECTs plus
        # throwaway sandbox copies — diagnostics that must never take the
        # writer path (FK enforcement irrelevant, no production writes).
        "helpers/bench/",
    )
    failures: list[str] = []
    for py in REPO_ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in py.parts):
            continue
        rel = py.relative_to(REPO_ROOT).as_posix()
        # P0: only enforce helpers/ + app.py (production code); tests/ and
        # ephemeral :memory: DBs are exempt (they use sqlite3.connect intentionally)
        if rel.startswith("tests/"):
            continue
        if rel.startswith("helpers/core/db.py"):
            continue
        if rel == "helpers/validators/static_checks.py":
            continue  # this file's own string literal scanner would self-flag
        if any(rel == pref or rel.startswith(pref) for pref in allowlist_prefixes):
            # Still report if db_maint adds unexpected extra sites outside known lines
            # For now, allow any use inside that file (maintenance-only ephemeral)
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except Exception:  # noqa: S112  # best-effort; skip item on failure
            continue
        if "sqlite3.connect" in text:
            # Find line numbers for the message
            for i, line in enumerate(text.splitlines(), start=1):
                if "sqlite3.connect" in line and "helpers/core/db" not in line:
                    failures.append(
                        f"{rel}:{i}: use helpers.core.db.connect instead of sqlite3.connect"
                    )
    return failures


def check_db_meta_generation() -> list[str]:
    """P0: live DB must have db_meta.generation and user_version == 7."""
    db = _db_path()
    if not db.exists() or sqlite3 is None:
        return []
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from helpers.core.db import EXPECTED_USER_VERSION, connect

    try:
        conn = connect(db, read_only=True)
        try:
            has = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='db_meta'"
            ).fetchone()
            if not has:
                return [f"{db}: db_meta table missing (run helpers.core.db.ensure_db_meta)"]
            row = conn.execute("SELECT value FROM db_meta WHERE key='generation'").fetchone()
            if row is None:
                return [f"{db}: db_meta.generation row missing"]
            try:
                int(row[0])
            except Exception:
                return [f"{db}: generation not an int: {row[0]!r}"]
            uv = conn.execute("PRAGMA user_version").fetchone()[0]
            if int(uv) != EXPECTED_USER_VERSION:
                return [f"{db}: PRAGMA user_version={uv} != expected {EXPECTED_USER_VERSION}"]
            return []
        finally:
            conn.close()
    except Exception as e:
        return [f"{db}: check failed: {e}"]


# Each check returns either:
#   list[str]             -> fatal failures only
#   (list[str], list[str]) -> (fatal, advisory). Advisory never affects exit code.
CHECKS = [
    ("Python syntax", check_python_syntax),
    ("JS syntax", check_js_syntax),
    ("Merge markers + artifacts", check_merge_markers_and_artifacts),
    ("Helper shebangs", check_helper_shebangs),
    ("Required files", check_required_files),
    ("Orphan markdown", check_orphan_markdown_files),
    # Combined single-walk: tags + permalink/sector + date sanity in one pass
    # over the 1102-file corpus (was 3 separate walks + 3× YAML parse).
    ("Findata YAML", check_findata_yaml),
    # B1: structural frontmatter contract (doc/okf/*.json)
    ("Frontmatter schema", check_frontmatter_schema_contract),
    # corpus_uniformity S3: proposals/ = proposed, archive/ = executed
    ("Proposal lifecycle", check_proposal_lifecycle),
    ("OKF conformance", check_okf_conformance_contract),
    ("Dependency pinning", check_dependency_pinning),
    ("SQLite helper usage", check_sqlite_helper_usage),
    ("DB meta generation", check_db_meta_generation),
]


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    # Test seam: flag-less tool — argv accepted and ignored.
    print("🔍 Static checks...")
    total_failures = 0
    total_advisory = 0
    for label, fn in CHECKS:
        result = fn()
        if isinstance(result, tuple):
            # Handle (fatal, advisory) or (fatal_list, advisory_list)
            if len(result) == 2 and isinstance(result[0], list) and isinstance(result[1], list):
                failures, advisory = result
            else:
                failures, advisory = result, []
        else:
            failures, advisory = result, []
        if failures:
            print(f"  ✗ {label}: {len(failures)} issue(s)")
            for f in failures[:5]:
                print(f"      {f}")
            if len(failures) > 5:
                print(f"      ... and {len(failures) - 5} more")
            total_failures += len(failures)
        elif advisory:
            # No fatal issues but some advisory ones — report but pass.
            print(f"  ~ {label}: {len(advisory)} advisory")
            for a in advisory[:3]:
                print(f"      {a}")
            if len(advisory) > 3:
                print(f"      ... and {len(advisory) - 3} more")
            total_advisory += len(advisory)
        else:
            print(f"  ✓ {label}")

    if total_failures == 0:
        msg = "✓ All static checks passed."
        if total_advisory:
            msg += f" ({total_advisory} advisory warnings — non-blocking)"
        print(msg)
        return 0
    print(f"\n✗ {total_failures} static-check failure(s).", file=sys.stderr)
    if total_advisory:
        print(f"  ({total_advisory} additional advisory warnings)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
