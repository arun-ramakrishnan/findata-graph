#!/usr/bin/env python3
"""Derive ``exposed_to`` edges (company -> theme) from company-note prose.

D4 — cross-sector theme nodes. Themes CUT ACROSS the GICS hierarchy (China+1 =
Electronics + EMS + Pharma API + Textiles), so they are an orthogonal
dimension, not a sector child. This extractor turns the theme *signal* already
in company notes (analyst synthesis prose, management commentary) into
first-class queryable membership edges.

Design (conservative, deterministic — no LLM dependency):
  * The theme set is CURATED (``CANONICAL_THEMES`` in static_checks.py), not
    extracted from free text. Only themes on the canonical list become nodes.
  * Membership is keyword-matched against a narrow ALIAS MAP per theme. The
    aliases are deliberately precise: e.g. PLI matches "pli scheme"/"pli
    benefit", NOT bare "pli" (which appears in 42/42 sector notes as
    boilerplate and would be a false positive). This trades some recall for
    high precision, matching D3's conservative-enum discipline.
  * Sector notes are SKIPPED (theme membership is company-scoped, mirroring
    extract_relations.py's sector-skip).

Three-stage shape mirrors derive_co_mentions.py: scan -> derive -> apply.

Usage:
    python3 helpers/graph/derive_themes.py            # dry-run summary
    python3 helpers/graph/derive_themes.py --apply    # write edges + entities
    python3 helpers/graph/derive_themes.py --verbose  # list every edge
"""

from __future__ import annotations
# ruff: noqa: C901, S101, S110, UP037  # S1b scale: Corpus + stale advisory, complexity is domain logic not lint

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# sys.path bootstrap so this works both as `python3 helpers/graph/...` (the
# Makefile form) and as a package import. Mirrors derive_co_mentions.py:43-45.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect, utc_now  # noqa: E402
from helpers.validators.static_checks import CANONICAL_THEMES  # noqa: E402
from helpers.graph._edge_writer import apply_typed_edges  # noqa: E402
from helpers.core.frontmatter import strip_frontmatter as _strip_frontmatter  # noqa: E402

try:
    from helpers.core.corpus import Corpus  # noqa: E402  # S1b shared walk

    _HAS_CORPUS = True
except ImportError:  # pragma: no cover
    Corpus = None  # type: ignore[assignment]
    _HAS_CORPUS = False

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #
COMPANIES_DIR = _REPO_ROOT / "findata" / "Companies"
EDGE_TYPE = "exposed_to"
SOURCE_REF = "derive:themes:keyword"

# Keyword alias map: theme -> list of lowercased phrases. A company note is
# `exposed_to` a theme if ANY alias appears (as a substring) in the note body.
# Aliases are NARROW by design to avoid boilerplate false positives:
#   * No bare short tokens that match inside words (e.g. "bev" is avoided —
#     it fans out to 56 notes, many false). Prefer multi-word phrases.
#   * No trailing/leading spaces (a substring search already matches the word;
#     the space variant just double-counts in matched_aliases).
# Add an alias only when you have evidence it carries real signal (not
# sector-note boilerplate). The matched aliases are recorded in edge properties
# for review, so precision is auditable after the fact.
THEME_ALIASES: dict[str, list[str]] = {
    "China_Plus_One": [
        "china+1",
        "china plus one",
        "china plus 1",
        "china + 1",
        "china-one",
        "china one strategy",
    ],
    "PLI_Scheme": [
        "pli scheme",
        "pli benefit",
        "production linked incentive",
        "production-linked incentive",
        "pli for",
    ],
    "Premiumization": [
        "premiumization",
        "premiumisation",
        "premium portfolio",
        "premium mix",
        "premiumisation trend",
        "premiumization trend",
    ],
    "EV_Transition": [
        "ev transition",
        "electric vehicle transition",
        "ev adoption",
        "ev penetration",
        "ev strategy",
        "ev ecosystem",
        "electric vehicle adoption",
        "battery electric vehicle",
    ],
    "Data_Center_Infrastructure": [
        "data center",
        "datacentre",
        "data-centre",
        "data-center",
        "hyperscaler",
        "ai infra",
        "ai infrastructure",
    ],
    "Renewable_Energy": [
        "renewable energy",
        "clean energy",
        "solar power",
        "wind power",
        "renewables capacity",
        "green energy",
        "energy transition",
    ],
    "Make_In_India": [
        "make in india",
        "make-in-india",
        "atmanirbhar",
        "localization",
        "localisation",
        "import substitution",
    ],
    "Defense_Indigenization": [
        "indigenization",
        "indigenisation",
        "defense import substitution",
        "defence indigen",
        "defense indigen",
        "indigenous defense",
        "indigenous defence",
    ],
    # Widen (P3 widen) — curated multi-word only, mined from 79 chatter editions; each alias is ≥2 words to avoid boilerplate single-token false positives (e.g. bare "api"/"bev" rejected).
    "Battery_Energy_Storage": [
        "battery storage",
        "battery recycling",
        "lithium ion battery",
        "sodium ion battery",
        "battery energy storage",
        "battery material",
        "battery manufacturing",
    ],
    "Electronic_Manufacturing_Services": [
        "electronic manufacturing services",
        "ems electronics",
        "electronics manufacturing",
        "electronic system design",
        "pcb assembly",
    ],
    "API_Manufacturing": [
        "api manufacturing",
        "api development",
        "active pharmaceutical ingredient",
        "api capacity",
        "api portfolio",
    ],
    "Beverage_Portfolio": [
        "beverage portfolio",
        "alcoholic beverage",
        "carbonated beverage",
        "food and beverage",
        "beverage industry",
        "beverage product",
    ],
}

# Flatten to (theme, alias) pairs for C-speed substring tests. Every alias
# is a literal substring probe by design, so ``alias in scan_text`` is
# exactly equivalent to the former ``re.compile(re.escape(alias)).search``
# (identical results verified over the full corpus, S4 2026-09-01) at
# memmem speed with zero per-call regex overhead — this loop was ~82K
# re.search calls per run, the module's dominant cost.
_THEME_ALIAS_PAIRS: list[tuple[str, str]] = [
    (theme, alias) for theme, aliases in THEME_ALIASES.items() for alias in aliases
]


# --------------------------------------------------------------------------- #
# Stage 1 — create theme entities                                             #
# --------------------------------------------------------------------------- #
def create_theme_entities(conn, *, apply: bool = True) -> int:
    """Insert a ``theme`` entity row per canonical theme (idempotent).

    Themes are structural nodes like sub_sectors — they have no file_path
    (no backing note yet). Mirrors build_sector_hierarchy.py's INSERT pattern.
    Returns the number of rows inserted (0 if all already existed).
    """
    now = utc_now()
    inserted = 0
    with conn:
        for theme in CANONICAL_THEMES:
            cur = conn.execute(
                "INSERT OR IGNORE INTO entities "
                "(name, entity_type, normalized_name, last_updated) "
                "VALUES (?, 'theme', ?, ?)",
                (theme, theme, now),
            )
            inserted += cur.rowcount
    return inserted


# --------------------------------------------------------------------------- #
# Stage 2 — scan notes, derive membership                                     #
# --------------------------------------------------------------------------- #
def extract_theme_membership(
    root: Path = COMPANIES_DIR,
    path_to_name: dict[str, str] | None = None,
    corpus: Corpus | None = None,  # S1b: pre-loaded Corpus (shared across maint --full)
):  # noqa: C901
    """Scan company notes and return ``(company_name, theme, matched_aliases)``.

    Args:
        root: Directory to scan for ``*.md`` (default findata/Companies).
        path_to_name: Map of posix-relative note path -> entity display name
            (the sync_tags / entities.file_path join contract). When provided,
            a note is only emitted if its path resolves to a known company —
            this skips stray .md files and resolves the display name (entity
            names use spaces, e.g. "ABB India", while file stems use
            underscores, e.g. "ABB_India"). When None, the file stem is used
            as the company name (test convenience).

    Yields ``(company, theme, [alias, ...])`` tuples. A company may be exposed
    to multiple themes; each (company, theme) pair is yielded once with the
    list of aliases that matched.
    """
    # S1b: corpus fast path — iterate over pre-parsed notes instead of re-walking
    if corpus is not None:

        def _in_root(n):  # type: ignore[no-untyped-def]
            # corpus paths are repo-relative findata/Companies/...; root is absolute _REPO_ROOT/findata/Companies
            rp = n.path.as_posix()
            # Try is_relative_to when both absolute, else string prefix on findata subtree
            try:
                if hasattr(n.path, "is_relative_to") and n.path.is_absolute():
                    return n.path.is_relative_to(root)
            except Exception:  # noqa: S110
                pass
            # Fallback: check suffix/prefix for Companies subtree
            return (
                "findata/Companies" in rp
                or rp.startswith("findata/Companies")
                or str(root).endswith("Companies")
                and "Companies" in rp
            )

        for note_obj in corpus.notes:
            if not _in_root(note_obj):
                continue
            note = note_obj.path
            text = note_obj.text
            # need to handle try/except for OSError already done
            # Resolve company as before but using note path
            try:
                rel = (
                    note.resolve().relative_to(_REPO_ROOT).as_posix()
                    if note.is_absolute()
                    else note.as_posix()
                )
            except ValueError:
                rel = note.stem
            company = None
            if path_to_name is not None:
                company = path_to_name.get(rel)
            else:
                company = note.stem
            if company is None:
                continue
            m_chatter = re.search(
                r"<!-- BEGIN auto chatter block.*?-->(.*?)<!-- END auto chatter block -->",
                text,
                flags=re.S | re.I,
            )
            scan_text = (
                m_chatter.group(1).lower() if m_chatter else _strip_frontmatter(text).lower()
            )
            if not scan_text:
                continue
            matched: dict[str, list[str]] = defaultdict(list)
            for theme, alias in _THEME_ALIAS_PAIRS:
                if alias in scan_text:
                    matched[theme].append(alias)
            for theme, aliases in matched.items():
                yield company, theme, sorted(set(aliases))
        return
    for note in sorted(root.rglob("*.md")):
        try:
            text = note.read_text(encoding="utf-8")
        except OSError:
            continue
        # Resolve the company display name via the file_path join (robust:
        # entity names use spaces, stems use underscores). The DB file_path is
        # stored repo-relative (findata/Companies/...), so resolve the note
        # the same way. If no map is given, fall back to the stem (test use).
        try:
            rel = note.resolve().relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            # note is outside the repo root (e.g. a tmp_path in tests) —
            # fall back to the stem when no map constrains it.
            rel = note.stem
        company = None
        if path_to_name is not None:
            company = path_to_name.get(rel)
        else:
            company = note.stem
        if company is None:
            continue
        # Chatter-block scoping: prefer the sentinel-wrapped auto chatter block (concall prose) when present — that is where The_Chatter richness lives. Fallback to full body for notes without chatter.
        m_chatter = re.search(
            r"<!-- BEGIN auto chatter block.*?-->(.*?)<!-- END auto chatter block -->",
            text,
            flags=re.S | re.I,
        )
        scan_text = m_chatter.group(1).lower() if m_chatter else _strip_frontmatter(text).lower()
        if not scan_text:
            continue
        # Collect matched aliases per theme for this note.
        matched: dict[str, list[str]] = defaultdict(list)
        for theme, alias in _THEME_ALIAS_PAIRS:
            if alias in scan_text:
                matched[theme].append(alias)
        for theme, aliases in matched.items():
            yield company, theme, sorted(set(aliases))


def derive_edges(membership) -> list[tuple[str, str, dict, str]]:
    """Turn membership into ``(source, target, properties, source_ref)`` edges.

    source = company, target = theme, edge_type = exposed_to (fixed in
    apply_edges). ``properties`` records which aliases matched so a reviewer
    can audit precision. ``source_ref`` carries the provenance prefix
    ``derive:`` (the convention for all derive-* scripts).
    """
    edges: list[tuple[str, str, dict, str]] = []
    for company, theme, aliases in membership:
        props = {"matched_aliases": aliases}
        edges.append((company, theme, props, SOURCE_REF))
    return edges


# --------------------------------------------------------------------------- #
# Stage 3 — persist edges                                                     #
# --------------------------------------------------------------------------- #
def apply_edges(edges, *, conn=None, dry_run: bool = True) -> int:
    """Insert ``exposed_to`` edges into ``graph_edges`` with INSERT OR IGNORE.

    Thin wrapper over :func:`helpers.graph._edge_writer.apply_typed_edges`
    (``edge_type='exposed_to'``, ``symmetric=0``). Kept as the module's public
    API because tests and the CLI call it by name.

    Idempotent via the ``UNIQUE(source, target, edge_type)`` constraint, so
    re-running is safe. ``dry_run=True`` (default) counts what would be
    inserted without writing — the derive-* convention.
    """
    return apply_typed_edges(
        edges,
        edge_type="exposed_to",
        symmetric=0,
        conn=conn,
        dry_run=dry_run,
    )


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def _cli(argv: list[str] | None = None) -> int:  # noqa: C901
    p = argparse.ArgumentParser(
        description="Derive exposed_to (company -> theme) edges from company notes.",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write theme entities + edges (default: dry-run summary only).",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print every edge in addition to the summary.",
    )
    p.add_argument(
        "--corpus",
        action="store_true",
        help="S1b: use helpers.core.corpus shared walk (maint --full) — one walk for all derivations.",
    )
    p.add_argument(
        "--stale-only",
        action="store_true",
        help="S1c: skip when no source newer than last derived (no-op cut for maint --full).",
    )
    args = p.parse_args(argv)

    conn = connect()
    try:
        # S1c --stale-only: skip full 1078 scan when no Company note newer than last derived.
        if args.stale_only:
            try:
                db_max = conn.execute(
                    "SELECT MAX(created_at) FROM graph_edges WHERE edge_type='exposed_to'"
                ).fetchone()[0]
            except Exception:
                db_max = None
            if db_max:
                # File mtime max vs DB string compare via ISO; fallback to timestamp compare.
                import datetime as _dt

                try:
                    db_dt = _dt.datetime.fromisoformat(db_max.replace(" ", "T"))
                    # fastest walk: fs_walk is not needed for single max, rglob is fine for 1078
                    max_mtime = max(
                        (pp.stat().st_mtime for pp in COMPANIES_DIR.rglob("*.md")), default=0
                    )
                    if max_mtime and _dt.datetime.fromtimestamp(max_mtime) <= db_dt:
                        print(
                            f"themes stale-only: no Company note newer than last derived {db_max} — skipping 0 edges (dry-run)",
                            file=sys.stderr,
                        )
                        return 0
                except Exception:  # noqa: S110
                    pass
        # Build file_path -> display-name map (the sync_tags join contract) so
        # notes resolve to the entity display name (spaces, e.g. "ABB India"),
        # not the underscore stem. Only companies with a file_path are scannable.
        path_to_name = {
            r[1]: r[0]
            for r in conn.execute(
                "SELECT name, file_path FROM entities "
                "WHERE entity_type = 'company' AND file_path IS NOT NULL"
            ).fetchall()
        }
        corpus = None
        if args.corpus and _HAS_CORPUS:
            try:
                # S2a shard: themes are company-scoped — load only the Companies
                # subtree (1078/1243 notes, ~8 MB vs ~29 MB resident; the shared
                # corpus cache eviction is root-scoped, so the other shards' rows
                # survive a shard load — see corpus.py S2a fix).
                corpus = Corpus.load_shard("Companies", workers=1, use_cache=True)  # ty: ignore[unresolved-attribute]
            except Exception:  # noqa: S110
                corpus = None
        membership = list(extract_theme_membership(COMPANIES_DIR, path_to_name, corpus=corpus))
        edges = derive_edges(membership)

        # Per-theme breakdown.
        by_theme: dict[str, list[str]] = defaultdict(list)
        for company, theme, aliases in membership:
            by_theme[theme].append(company)

        print(
            f"themes={len(CANONICAL_THEMES)} companies_scanned={len(path_to_name)} "
            f"derived_edges={len(edges)} "
            f"({'apply' if args.apply else 'dry-run'})",
            file=sys.stderr,
        )
        for theme in sorted(CANONICAL_THEMES):
            members = by_theme.get(theme, [])
            print(
                f"  {len(members):4d}  {theme}",
                file=sys.stderr,
            )

        # Create theme entities first (edges FK to entities.name).
        ent_inserted = (
            create_theme_entities(conn, apply=args.apply) if args.apply else len(CANONICAL_THEMES)
        )
        edge_inserted = apply_edges(edges, conn=conn, dry_run=not args.apply)
        action = "inserted" if args.apply else "would insert"
        print(
            f"{ent_inserted} theme entities {action}; {edge_inserted} exposed_to edges {action}.",
            file=sys.stderr,
        )

        if args.verbose:
            for source, target, props, source_ref in edges:
                print(f"{source}\t{target}\t{json.dumps(props, ensure_ascii=False)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(_cli())
