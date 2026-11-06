#!/usr/bin/env python3
"""Stage newsletter co-mention edition sets (S5 — the dyads are retired).

When `parse_newsletter` enhances a company note from a newsletter edition, it
appends a `## <Newsletter> — <edition title>` heading plus a matching
`*Source: <Newsletter> — <edition title>*` footer. Every company enhanced
from the same edition therefore carries an identical edition title; those
companies "co-mention" each other.

S5 (polyadic_capture_edge_fidelity) retired the `co_mentioned_in` DYADS:
C(n,2) pair rows in `graph_edges` misrepresent shared-edition context as
company-to-company relations (9,638 rows duplicating what the edition
hyperedge family already carries). This lane now stages the per-edition
participant SET into `co_mention_editions` (one row per edition x company);
`derive_hyperedges` unions it with the quotes table (S8) into the canonical
edition hyperedge family, and pair views are PROJECTED on demand
(``project_co_mention_pairs`` — sqlite consumers) or materialised in the
DuckDB cache (``e_comention`` — the sector-retirement compatibility
pattern).

Public API
----------
- ``extract_co_mentions(newsletter_type)`` — scan the vault, group entities
  by edition title. Returns ``{edition_title: [entity_name, ...]}``.
- ``apply_edition_sets(edition_to_entities, ...)`` — stage the sets
  (idempotent INSERT OR IGNORE into ``co_mention_editions``).
- ``project_co_mention_pairs(conn)`` — shared-edition pair view over the
  canonical edition family, ``weight = n_shared_editions + 1``.
- ``derive_edges(edition_to_entities)`` — pair math over extracted sets
  (projection/parity tests; no longer a store writer).
- ``apply_edges(...)`` — RETIRED, raises with the migration pointer.

CLI
---
    python3 helpers/graph/derive_co_mentions.py --newsletter The_Chatter
    python3 helpers/graph/derive_co_mentions.py --newsletter The_Chatter --apply
"""

from __future__ import annotations

try:
    from helpers.core.corpus import Corpus  # S1b shared walk

    _HAS_CORPUS = True
except ImportError:  # pragma: no cover
    import sys

    Corpus = None  # type: ignore[assignment]
    _HAS_CORPUS = False
    # A foreign package named `helpers` (the PyPI namesake of the repo
    # package) can satisfy this import in script mode and cache a `helpers`
    # lacking .core in sys.modules — evict it so the post-bootstrap imports
    # re-resolve to the repo package.
    sys.modules.pop("helpers", None)

import argparse
import json
import re
import sys
from itertools import combinations
from pathlib import Path
from collections.abc import Iterable, Mapping

# Bootstrap so the module is importable both as a script (`python3
# helpers/graph/derive_co_mentions.py ...`) and as a package import. When run
# as a script, only the script's own directory is on sys.path by default;
# add the repo root so `helpers.core.db` resolves.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.graph import derive_cli as dcli  # noqa: E402  # S6 shared CLI scaffold

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #
COMPANIES_DIR = _REPO_ROOT / "findata" / "Companies"

# S5 (polyadic_capture_edge_fidelity): the co_mentioned_in DYADS are
# retired from graph_edges — shared-edition context is carried by the
# canonical edition hyperedge family (hyper_edges/hyper_incidences), and
# pair views are PROJECTED on demand. This lane's capture output is the
# per-edition participant SET, staged here; derive_hyperedges unions it
# with the quotes table (S8) into the edition family. One row per
# (edition, company) — the C(n,2) pair blowup dies at the source.
CO_MENTION_EDITIONS_DDL = """
CREATE TABLE IF NOT EXISTS co_mention_editions (
    edition_title TEXT    NOT NULL,
    entity_name   TEXT    NOT NULL
                    REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
    newsletter    TEXT    NOT NULL,
    source_ref    TEXT    NOT NULL,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (edition_title, entity_name)
)
"""

# Supported newsletter types. The key is the canonical slug (matches the
# source_ref / properties.newsletter value); the value is the literal heading
# text used inside markdown notes. Notes use "&" for "Points & Figures", not
# the underscore form.
NEWSLETTER_TITLES: dict[str, str] = {
    "The_Chatter": "The Chatter",
    "Points_And_Figures": "Points & Figures",
    "The_PlotLines": "The PlotLines",
}

# Heading: `## <Newsletter> — <edition title>` (em-dash). We tolerate em/en
# dash, hyphen, or ellipsis as the separator, and accept any non-empty title.
_HEADING_RE_TEMPLATE = r"^##\s+{nl}\s*[—–-]{{1,3}}\s*(.+?)\s*$"

# Source footer: `*Source: <Newsletter> — <edition title> (Edition #N, ...)*`
# Captures both the edition title (group 1) and the trailing parenthetical
# containing the edition number (group 2), e.g. "(Edition #69, Q1FY27)".
# The parenthetical is optional; some footers omit it.
_FOOTER_RE_TEMPLATE = (
    r"^\*Source:\s*{nl}\s*[—–-]{{1,3}}\s*(.+?)\s*(\([^()]*Edition\s*#\d+[^()]*\))?\s*\*\s*$"
)

_EDITION_NUM_RE = re.compile(r"Edition\s*#(\d+)", re.IGNORECASE)


def _heading_regex(newsletter_type: str) -> re.Pattern[str]:
    """Build the heading regex for a given newsletter slug.

    ``re.MULTILINE`` is required so ``^``/``$`` anchor at every line, not
    just the start/end of the whole file.
    """
    return re.compile(
        _HEADING_RE_TEMPLATE.format(nl=re.escape(_newsletter_title(newsletter_type))),
        re.MULTILINE,
    )


def _footer_regex(newsletter_type: str) -> re.Pattern[str]:
    """Build the source-footer regex for a given newsletter slug."""
    return re.compile(
        _FOOTER_RE_TEMPLATE.format(nl=re.escape(_newsletter_title(newsletter_type))),
        re.MULTILINE,
    )


def _newsletter_title(newsletter_type: str) -> str:
    title = NEWSLETTER_TITLES.get(newsletter_type)
    if title is None:
        raise ValueError(
            f"Unknown newsletter_type {newsletter_type!r}; "
            f"expected one of {sorted(NEWSLETTER_TITLES)}"
        )
    return title


# --------------------------------------------------------------------------- #
# Extraction                                                                  #
# --------------------------------------------------------------------------- #
def _resolve_entity_name(conn, file_path: str) -> str | None:
    """Map a company note path -> entities.name (display name).

    Returns None if the note isn't registered as an entity (avoids FK
    violations when inserting edges).
    """
    row = conn.execute("SELECT name FROM entities WHERE file_path = ?", (file_path,)).fetchone()
    return row["name"] if row else None


def extract_co_mentions(  # noqa: C901
    newsletter_type: str = "The_Chatter",
    *,
    companies_dir: Path | None = None,
    conn=None,
    corpus=None,  # S1b shared walk: pre-parsed notes (helpers.core.corpus)
) -> dict[str, list[str]]:
    """Scan the vault for `## <Newsletter> — <edition>` headings.

    Args:
        newsletter_type: One of ``NEWSLETTER_TITLES`` (e.g. ``"The_Chatter"``).
        companies_dir: Override for the companies root (used in tests).
        conn: Reuse an existing SQLite connection. If None, opens a fresh one
            via ``helpers.core.db.connect()``.
        corpus: Pre-loaded ``Corpus`` (maint --full pre-warm) — scan
            in-memory texts instead of re-walking + re-reading.

    Returns:
        ``{edition_title: [entity_name, ...]}`` where ``entity_name`` is the
        display name from the ``entities`` table (FK-safe). Editions with no
        resolvable entities are omitted. The entity lists are de-duplicated
        and sorted alphabetically.

    Edition-title canonicalisation:
        The heading only carries the title text (e.g. "Jio Financial, Wipro,
        Polycab, Piramal & More"). The integer edition number (e.g. #69) is
        often only present in the ``*Source: ... (Edition #N, ...)`` footer.
        To make edition titles stable + number-bearing across the whole vault,
        we first scan every footer and build a ``title -> parenthetical`` map
        (e.g. "Jio Financial, Wipro, Polycab, Piramal & More" -> " (Edition #69,
        Q1FY27)"). When a heading's title has no inline ``Edition #N`` we append
        the parenthetical (if any) from the footer map. Editions whose titles
        already carry an inline number (e.g. "Edition #52 (Mar 27, 2026)")
        are left untouched.
    """
    heading_re = _heading_regex(newsletter_type)
    footer_re = _footer_regex(newsletter_type)
    root = companies_dir if companies_dir is not None else COMPANIES_DIR
    own_conn = conn is None
    if own_conn:
        conn = connect()

    try:
        # Bundle V3: bulk-fetch the {file_path: entity_name} map ONCE instead
        # of calling _resolve_entity_name (a per-file SELECT) for every .md
        # note (~1000 round-trips on the live vault). The map is checked
        # in-memory during Pass 2 below.
        file_to_name: dict[str, str] = {
            r["file_path"]: r["name"]
            for r in conn.execute(
                "SELECT name, file_path FROM entities WHERE entity_type = 'company'"
            ).fetchall()
            if r["file_path"]
        }

        # S1b: one read per file — corpus texts or a single walk — then the
        # two existing phases run over in-memory texts. Phases stay ordered
        # (the footer map must be complete before headings canonicalise),
        # but files are read ONCE, not twice (was: 2 walks × ~1000 reads).
        texts: list[tuple[str, str]] = []
        if corpus is not None:
            try:
                root_rel = root.resolve().relative_to(_REPO_ROOT).as_posix()
            except ValueError:
                root_rel = ""
            for note in corpus.notes:
                rel = note.path.as_posix()
                if note.path.is_absolute():
                    try:
                        rel = note.path.resolve().relative_to(_REPO_ROOT).as_posix()
                    except ValueError:
                        continue
                if root_rel and not (rel == root_rel or rel.startswith(root_rel + "/")):
                    continue
                texts.append((rel, note.text))
            texts.sort()
        else:
            for md_path in sorted(root.rglob("*.md")):
                try:
                    text = md_path.read_text(encoding="utf-8")
                except OSError, UnicodeDecodeError:
                    continue
                try:
                    rel_path = md_path.relative_to(_REPO_ROOT)
                except ValueError:
                    # companies_dir was overridden (tests); fall back to a path
                    # fragment that won't match any entity row.
                    rel_path = md_path
                texts.append((str(rel_path).replace("\\", "/"), text))

        # Pass 1: collect title -> footer-parenthetical (with edition number)
        # across the entire vault. The first occurrence wins; subsequent
        # footers for the same title are assumed to agree.
        title_to_suffix: dict[str, str] = {}
        for _file_path_str, text in texts:
            for m in footer_re.finditer(text):
                title, paren = m.group(1).strip(), m.group(2)
                if not title:
                    continue
                title_to_suffix.setdefault(title, paren or "")

        # Pass 2: collect entities per (canonicalised) edition title.
        editions: dict[str, set[str]] = {}
        for file_path_str, text in texts:
            matches = heading_re.findall(text)
            if not matches:
                continue
            # Resolve the note's entity name via the bulk map (was: per-file
            # SELECT via _resolve_entity_name — Bundle V3).
            entity_name = file_to_name.get(file_path_str)
            if entity_name is None:
                continue
            for raw_title in matches:
                raw_title = raw_title.strip().rstrip(".")
                canonical = _canonicalise_title(raw_title, title_to_suffix)
                editions.setdefault(canonical, set()).add(entity_name)
    finally:
        if own_conn:
            conn.close()

    return {title: sorted(names) for title, names in editions.items() if names}


def _canonicalise_title(
    raw_title: str,
    title_to_suffix: dict[str, str],
) -> str:
    """Return a stable, edition-number-bearing title for grouping.

    If ``raw_title`` already contains an inline ``Edition #N`` (e.g. heading
    was "Edition #52 (Mar 27, 2026)"), return it as-is.

    Otherwise, if a source-footer was seen for the same bare title, append
    the footer's parenthetical (e.g. " (Edition #69, Q1FY27)") so the key
    carries the edition number and the ``derive_edges`` step can parse it.

    Falls back to the bare title when no footer was found.
    """
    if _EDITION_NUM_RE.search(raw_title):
        return raw_title
    suffix = title_to_suffix.get(raw_title)
    if suffix:
        return f"{raw_title} {suffix}"
    return raw_title


def _parse_edition_number(edition_title: str) -> int | None:
    """Extract an integer edition number from a title, if present.

    Handles both the inline form ("Edition #52 (Mar 27, 2026)") and the
    footer-only form (the caller may pass the footer text directly).
    """
    m = _EDITION_NUM_RE.search(edition_title)
    return int(m.group(1)) if m else None


def _newsletter_source_ref(newsletter_type: str) -> str:
    return f"derive:co_mentioned:{newsletter_type}"


# --------------------------------------------------------------------------- #
# Edge derivation                                                             #
# --------------------------------------------------------------------------- #
def count_pair_co_mentions(
    edition_to_entities: Mapping[str, Iterable[str]],
) -> dict[tuple[str, str], int]:
    """Count how many editions co-mention each unordered company pair."""
    counts: dict[tuple[str, str], int] = {}
    for _title, entities in edition_to_entities.items():
        unique_sorted = sorted(set(entities))
        if len(unique_sorted) < 2:
            continue
        for a, b in combinations(unique_sorted, 2):
            key = (a, b) if a <= b else (b, a)
            counts[key] = counts.get(key, 0) + 1
    return counts


def derive_edges(
    edition_to_entities: Mapping[str, Iterable[str]],
    *,
    newsletter_type: str = "The_Chatter",
) -> list[tuple[str, str, dict, str]]:
    """Generate canonical unordered pairs per edition.

    For an edition with N entities, emits ``N*(N-1)/2`` edges. Each edge is
    a tuple ``(source, target, properties, source_ref)`` where ``source`` is
    alphabetically ≤ ``target`` (symmetric convention from graph_design.md
    §4). Editions with fewer than 2 entities produce no edges.

    The ``properties`` dict carries:

    - ``edition``: the edition title verbatim.
    - ``newsletter``: the newsletter slug (e.g. ``"The_Chatter"``).
    - ``edition_number``: integer if extractable from the title, else omitted.
    """
    source_ref = _newsletter_source_ref(newsletter_type)
    pair_counts = count_pair_co_mentions(edition_to_entities)
    edges: list[tuple[str, str, dict, str]] = []
    for edition_title, entities in edition_to_entities.items():
        # De-dup + sort so the canonical ordering is deterministic regardless
        # of how the caller assembled the list.
        unique_sorted = sorted(set(entities))
        if len(unique_sorted) < 2:
            continue
        props: dict = {
            "edition": edition_title,
            "newsletter": newsletter_type,
        }
        edition_num = _parse_edition_number(edition_title)
        if edition_num is not None:
            props["edition_number"] = edition_num
        for a, b in combinations(unique_sorted, 2):
            # `combinations` over a sorted list already yields a < b, but be
            # defensive: enforce canonical ordering explicitly.
            source, target = (a, b) if a <= b else (b, a)
            count = pair_counts.get((source, target), 1)
            edge_props = dict(props)
            edge_props["count"] = count
            edge_props["n_mentions"] = count
            edge_props["n_co_mentions"] = count
            edge_props["co_mention_count"] = count
            edge_props["n_editions"] = count
            edges.append((source, target, edge_props, source_ref))
    return edges


# --------------------------------------------------------------------------- #
# Persistence                                                                 #
# --------------------------------------------------------------------------- #
def ensure_co_mention_editions_schema(conn) -> None:
    """Create the ``co_mention_editions`` staging table when missing."""
    conn.execute(CO_MENTION_EDITIONS_DDL)
    conn.commit()


def apply_edition_sets(
    edition_to_entities: Mapping[str, Iterable[str]],
    *,
    newsletter_type: str = "The_Chatter",
    conn=None,
    dry_run: bool = True,
) -> int:
    """Stage the per-edition participant sets (S5 writer — replaces the dyad INSERT).

    One row per ``(edition_title, entity_name)`` into
    ``co_mention_editions``; ``derive_hyperedges`` unions this with the
    quotes table into the canonical edition hyperedge family. Idempotent
    via the PRIMARY KEY (INSERT OR IGNORE — the lane's stance stays
    additive, same as the dyads ever were).

    Returns the number of rows actually inserted (``dry_run=False``) or
    that would be inserted (``dry_run=True``).
    """
    own = conn is None
    if own:
        conn = connect()
    try:
        ensure_co_mention_editions_schema(conn)
        source_ref = _newsletter_source_ref(newsletter_type)
        rows = [
            (edition_title, entity_name, newsletter_type, source_ref)
            for edition_title, entities in sorted(edition_to_entities.items())
            for entity_name in sorted(set(entities))
        ]
        if dry_run:
            staged = {
                (r[0], r[1])
                for r in conn.execute(
                    "SELECT edition_title, entity_name FROM co_mention_editions"
                ).fetchall()
            }
            return sum(1 for row in rows if (row[0], row[1]) not in staged)
        inserted = 0
        with conn:
            before = conn.total_changes
            conn.executemany(
                "INSERT OR IGNORE INTO co_mention_editions "
                "(edition_title, entity_name, newsletter, source_ref) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )
            inserted = conn.total_changes - before
        return int(inserted)
    finally:
        if own:
            conn.close()


def project_co_mention_pairs(conn) -> list[tuple[str, str, float]]:
    """Project shared-edition pairs from the CANONICAL edition family (S5).

    The store-level replacement for reading ``co_mentioned_in`` rows from
    ``graph_edges``: for every edition hyperedge, all unordered member
    pairs are emitted once, ``a < b``, with ``weight = n_shared_editions + 1``
    (the ``backfill_weights`` count+1 law, preserved so consumers that
    ranked on dyad weights keep their scale). NOTE the semantic widening:
    the projection covers the WHOLE edition family — newsletter co-mentions
    AND quote-participation editions — which is precisely the
    "shared-edition context" the retired dyads misnamed company-to-company
    relations.

    Pure read over the caller's connection; the DuckDB cache carries the
    same projection materialised as ``e_comention`` (sector-retirement
    precedent — consumers keep working with zero changes).
    """
    import sqlite3  # noqa: PLC0415  (OperationalError guard)

    try:
        rows = conn.execute(
            """
            SELECT i1.entity_name AS a, i2.entity_name AS b,
                   COUNT(DISTINCT i1.edge_id) AS n
            FROM hyper_incidences i1
            JOIN hyper_incidences i2
              ON i2.edge_id = i1.edge_id AND i2.entity_name > i1.entity_name
            JOIN hyper_edges h ON h.id = i1.edge_id AND h.edge_type = 'edition'
            GROUP BY a, b
            ORDER BY a, b
            """
        ).fetchall()
    except sqlite3.OperationalError:
        # no hyper store (fresh/synthetic DB) — empty projection, never-block
        return []
    return [(r[0], r[1], float(r[2]) + 1.0) for r in rows]


def apply_edges(
    edges: Iterable[tuple[str, str, dict, str]],
    *,
    conn=None,
    dry_run: bool = True,
) -> int:
    """RETIRED (S5): pair materialization into graph_edges is gone.

    Shared-edition context lives in the edition hyperedge family; pair
    views are projected (:func:`project_co_mention_pairs`). This stub
    refuses loudly so no caller silently re-materializes the clique.
    """
    raise NotImplementedError(
        "S5 (polyadic_capture_edge_fidelity): co_mentioned_in dyads are "
        "retired from graph_edges. Stage edition sets with "
        "apply_edition_sets() and project pair views with "
        "project_co_mention_pairs()."
    )


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
_ARGS = dcli.DeriveArgsSpec(
    apply_help="Stage edition sets into co_mention_editions (default: dry-run summary only).",
    stale_help="S1c: skip when no source newer than last derived.",
    corpus=True,
)


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Stage newsletter co-mention edition sets (S5: the co_mentioned_in "
            "dyads are retired — derive_hyperedges unions these sets with the "
            "quotes table into the canonical edition hyperedge family)."
        ),
    )
    p.add_argument(
        "--newsletter",
        default="The_Chatter",
        choices=sorted(NEWSLETTER_TITLES),
        help="Newsletter slug (default: The_Chatter)",
    )
    dcli.add_derive_args(p, _ARGS)
    args = p.parse_args(argv)

    # S1b: when --corpus, share maint --full's pre-warmed walk.
    _corpus = None
    if args.corpus and _HAS_CORPUS:
        try:
            _corpus = Corpus.load("findata", workers=1, use_cache=True)  # ty: ignore[unresolved-attribute]
        except Exception:  # noqa: S110
            _corpus = None
    editions = extract_co_mentions(args.newsletter, corpus=_corpus)

    # Summary to stderr so stdout stays machine-readable if needed.
    n_members = sum(len(v) for v in editions.values())
    print(
        f"[{args.newsletter}] editions={len(editions)} members={n_members} "
        f"({'apply' if args.apply else 'dry-run'})",
        file=sys.stderr,
    )
    # Per-edition breakdown (pairs shown as the projection they feed).
    for edition_title in sorted(editions):
        names = editions[edition_title]
        n_pairs = len(names) * (len(names) - 1) // 2
        print(
            f"  {len(names):3d} co  {n_pairs:5d} proj pairs  {edition_title}",
            file=sys.stderr,
        )

    staged = apply_edition_sets(editions, newsletter_type=args.newsletter, dry_run=not args.apply)
    action = "staged" if args.apply else "would stage"
    print(f"{staged} edition memberships {action}.", file=sys.stderr)

    if args.verbose:
        for edition_title, names in sorted(editions.items()):
            print(f"{edition_title}\t{json.dumps(names, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
