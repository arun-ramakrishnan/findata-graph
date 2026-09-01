#!/usr/bin/env python3
"""Derive ``cited_in`` edges (company/sector -> edition) from OKF ``sources[]``.

okf_activation P (doc/improvements/archive/okf/okf_activation.md): editions
become first-class graph nodes and the provenance already stamped in note
frontmatter becomes traversable. The source of truth is the YAML
``sources[]`` the OKF backfill resolved from derived-note bodies (F0
canonical edition key = note STEM); this derive step only PROJECTS that
metadata into entities/graph_edges — it never re-resolves editions.

Shape:
  * Edition entities: entity_type='edition', name = note STEM (the
    canonical-key + wikilink rule), file_path vault-relative,
    normalized_name = frontmatter title. No ticker/sector; not in the
    belongs_to forest.
  * Edges: one per (derived-note entity, edition) pair — edge_type
    'cited_in', symmetric=0, weight=1 (column default),
    properties={resource, n_quotes}, source_ref 'derive:cited_in'.
  * PDF sources are skipped (Q4 amendment, proposal §5.4): derived notes
    cite editions exclusively today, and the 5 PDF citations live on
    edition notes with no note entity to attach to.

Consumer integration (proposal §3.3):
  * analytics excludes cited_in from activity reports (_MEMBERSHIP_TYPES).
  * link_prediction's default projection (co_mentioned_in, jv_with,
    competes_with, same_group) does not include cited_in — co-citation
    is a possible future feature, deliberately not one now (the quarterly
    roundup hub would make it noise).
  * context_packs rank cited_in last and do not expand hops through it.

Idempotent: entities via INSERT OR IGNORE, edges via the
UNIQUE(source, target, edge_type) constraint. Re-runs only add citations
newly stamped in frontmatter — the backfill/derive_insights own that side.

Writing graph_edges requires a paired DuckDB rebuild (placement rule):
`make derive-cited-in-rebuild`, or this script + `make graph-rebuild`.

Usage:
    python3 helpers/graph/derive_cited_in.py             # dry-run summary
    python3 helpers/graph/derive_cited_in.py --apply     # write entities+edges
    python3 helpers/graph/derive_cited_in.py --verbose   # list every edge
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import yaml

# Bootstrap so this works as `python3 helpers/graph/...` (Makefile form) and
# as a package import. Mirrors derive_themes.py.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect, utc_now  # noqa: E402
from helpers.core.edition_index import (  # noqa: E402
    CHROME_FILES,
    note_title,
    resolve_edition_string,
    source_note_index,
)
from helpers.core.frontmatter import split_frontmatter, yaml_safe_load  # noqa: E402
from helpers.graph._edge_writer import apply_typed_edges  # noqa: E402

DERIVED_TREES = ("Companies", "Sectors", "Super_Sectors")
SOURCE_TREES = ("The_Chatter", "The_PlotLines", "Points_And_Figures")
EDGE_TYPE = "cited_in"
SOURCE_REF = "derive:cited_in"


def edition_notes(vault: Path) -> list[dict]:
    """One record per source-tree note: {stem, file_path, title}.

    Fails loudly on duplicate stems across the three trees — the stem is
    the canonical edition key (sources[].id, wikilinks, entity name), so
    a collision would corrupt every join downstream.
    """
    seen: dict[str, Path] = {}
    out: list[dict] = []
    for tree in SOURCE_TREES:
        for p in sorted((vault / tree).rglob("*.md")):
            if p.name in CHROME_FILES or "images" in p.parts:
                continue
            if p.stem in seen:
                raise ValueError(
                    f"edition stem collision: {p} and {seen[p.stem]} — "
                    "the stem is the canonical edition key; resolve manually"
                )
            seen[p.stem] = p
            out.append(
                {
                    "stem": p.stem,
                    "file_path": f"{vault.name}/{p.relative_to(vault).as_posix()}",
                    "title": note_title(p.read_text(encoding="utf-8", errors="replace"), p.stem),
                }
            )
    return out


def create_edition_entities(conn, editions: list[dict], *, apply: bool = True) -> int:
    """Insert an ``edition`` entity per source note (idempotent).

    Stub-collision guard: an existing NON-edition entity with the same
    name is a hard error (the edge would silently point at the wrong
    node) — measured NONE on the live corpus 2026-08-19, but guarded
    anyway. Returns the number of rows inserted (0 if all existed).
    """
    names = [e["stem"] for e in editions]
    placeholders = ",".join("?" * len(names))
    clashes = conn.execute(
        f"SELECT name, entity_type FROM entities WHERE name IN ({placeholders}) "  # noqa: S608  # parameterized placeholders
        "AND entity_type != 'edition'",
        names,
    ).fetchall()
    if clashes:
        raise RuntimeError(
            "edition stems collide with existing non-edition entities: "
            + ", ".join(f"{n} ({t})" for n, t in clashes)
        )
    if not apply:
        have = {
            n
            for (n,) in conn.execute(
                f"SELECT name FROM entities WHERE name IN ({placeholders})",  # noqa: S608
                names,
            ).fetchall()
        }
        return len(set(names) - have)
    now = utc_now()
    inserted = 0
    with conn:
        for e in editions:
            # normalized_name = the stem itself (theme-entity precedent): the
            # integrity check requires the entity-name format there, and the
            # note title stays on the note (edition_index reads it on demand).
            cur = conn.execute(
                "INSERT OR IGNORE INTO entities "
                "(name, entity_type, normalized_name, file_path, last_updated) "
                "VALUES (?, 'edition', ?, ?, ?)",
                (e["stem"], e["stem"], e["file_path"], now),
            )
            inserted += cur.rowcount
    return inserted


def _note_frontmatter(p: Path) -> dict:
    text = p.read_text(encoding="utf-8", errors="replace")
    opener, fm_text, _ = split_frontmatter(text)
    if not opener:
        return {}
    try:
        fm = yaml_safe_load(fm_text)
    except yaml.YAMLError:
        return {}
    return fm if isinstance(fm, dict) else {}


def extract_citations(
    vault: Path, path_to_name: dict[str, str], edition_stems: set[str]
) -> tuple[list[tuple[str, str, str]], dict]:
    """Collect ``(note_entity, edition_stem, resource)`` from derived notes.

    ``path_to_name`` is the entities file_path -> display-name join (the
    sync_tags contract); notes without an entity row are skipped, mirroring
    derive_themes. sources[] entries pointing at /Reports/*.pdf are skipped
    (Q4 amendment); entries whose id is not a known edition stem are counted
    in the returned stats (not emitted). Returns ``(citations, stats)``.
    """
    citations: list[tuple[str, str, str]] = []
    stats = {"skipped_pdf": 0, "unknown_id": 0}
    for tree in DERIVED_TREES:
        for p in sorted((vault / tree).rglob("*.md")):
            fm = _note_frontmatter(p)
            sources = fm.get("sources")
            if not isinstance(sources, list):
                continue
            entity = path_to_name.get(f"{vault.name}/{p.relative_to(vault).as_posix()}")
            if entity is None:
                continue
            for s in sources:
                if not isinstance(s, dict) or not s.get("id"):
                    continue
                resource = s.get("resource", "")
                if resource.startswith("/Reports/"):
                    stats["skipped_pdf"] += 1
                    continue
                if s["id"] not in edition_stems:
                    stats["unknown_id"] += 1
                    continue
                citations.append((entity, s["id"], resource))
    return citations, stats


def quote_counts(conn, index: dict[str, Path]) -> dict[tuple[str, str], int]:
    """(entity, edition_stem) -> quote-row count, via the F0 bridge.

    quotes.as_of_edition is free text (titles, 28/71 exact) — resolved
    with the shared edition_index helper. Unresolvable strings simply
    don't count (4 today); they carry no n_quotes signal.
    """
    counts: dict[tuple[str, str], int] = collections.Counter()
    for edition, entity, n in conn.execute(
        "SELECT as_of_edition, entity, COUNT(*) FROM quotes GROUP BY as_of_edition, entity"
    ).fetchall():
        p = resolve_edition_string(edition or "", index)
        if p is not None:
            counts[(entity, p.stem)] += n
    return counts


def derive_edges(citations, n_quotes: dict[tuple[str, str], int]):
    """Citations -> ``(source, target, properties, source_ref)`` edges."""
    edges = []
    for entity, stem, resource in citations:
        props = {"n_quotes": n_quotes.get((entity, stem), 0)}
        if resource:
            props["resource"] = resource
        edges.append((entity, stem, props, SOURCE_REF))
    return edges


def apply_edges(edges, *, conn=None, dry_run: bool = True) -> int:
    """Insert ``cited_in`` edges (INSERT OR IGNORE; idempotent)."""
    return apply_typed_edges(
        edges,
        edge_type=EDGE_TYPE,
        symmetric=0,
        conn=conn,
        dry_run=dry_run,
    )


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Derive cited_in (note -> edition) edges from OKF sources[].",
    )
    p.add_argument(
        "--apply", action="store_true", help="Write edition entities + edges (default: dry-run)."
    )
    p.add_argument(
        "--verbose", "-v", action="store_true", help="Print every edge in addition to the summary."
    )
    p.add_argument(
        "--vault",
        default=str(_REPO_ROOT / "findata"),
        help="Vault root (default: <repo>/findata; tests override).",
    )
    args = p.parse_args(argv)

    vault = Path(args.vault).resolve()
    conn = connect()
    try:
        editions = edition_notes(vault)
        stems = {e["stem"] for e in editions}
        path_to_name = {
            r[1]: r[0]
            for r in conn.execute(
                "SELECT name, file_path FROM entities "
                "WHERE entity_type IN ('company','sector','super_sector') "
                "AND file_path IS NOT NULL"
            ).fetchall()
        }
        citations, stats = extract_citations(vault, path_to_name, stems)
        nq = quote_counts(conn, source_note_index(vault))
        edges = derive_edges(citations, nq)

        tree_of = {e["stem"]: e["file_path"].split("/")[1] for e in editions}
        per_tree = collections.Counter(tree_of[stem] for _, stem, _ in citations if stem in tree_of)
        cited = {stem for _, stem, _ in citations}
        mode = "apply" if args.apply else "dry-run"
        print(
            f"editions={len(editions)} cited_editions={len(cited)} "
            f"edges={len(edges)} ({mode}) "
            f"[skipped_pdf={stats.get('skipped_pdf', 0)} "
            f"unknown_id={stats.get('unknown_id', 0)}]",
            file=sys.stderr,
        )
        for tree in SOURCE_TREES:
            print(f"  {per_tree.get(tree, 0):4d}  cited_in edges -> {tree}", file=sys.stderr)

        ent = create_edition_entities(conn, editions, apply=args.apply)
        ins = apply_edges(edges, conn=conn, dry_run=not args.apply)
        verb = "inserted" if args.apply else "would insert"
        print(f"{ent} edition entities {verb}; {ins} cited_in edges {verb}.", file=sys.stderr)

        if args.verbose:
            for source, target, props, _ref in edges:
                print(f"{source}\t{target}\t{json.dumps(props, ensure_ascii=False)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(_cli())
