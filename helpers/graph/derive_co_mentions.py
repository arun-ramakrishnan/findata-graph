#!/usr/bin/env python3
"""Derive `co_mentioned_in` edges from newsletter enhancement blocks.

When `parse_newsletter` enhances a company note from a newsletter edition, it
appends a `## <Newsletter> — <edition title>` heading plus a matching
`*Source: <Newsletter> — <edition title>*` footer. Every company enhanced
from the same edition therefore carries an identical edition title; those
companies are "co-mentioned" and we materialise a symmetric `co_mentioned_in`
edge between every unordered pair.

This is Slice C of Graph Phase 2 — see `doc/design/graph_design.md` §4 for the
symmetric-edge convention (`source LE target`, one row per pair).

Public API
----------
- ``extract_co_mentions(newsletter_type)`` — scan the vault, group entities
  by edition title. Returns ``{edition_title: [entity_name, ...]}``.
- ``derive_edges(edition_to_entities)`` — generate canonical unordered pairs
  with ``properties`` + ``source_ref`` for each edition.
- ``apply_edges(edges, conn, dry_run)`` — INSERT OR IGNORE into
  ``graph_edges``. Idempotent via the UNIQUE(source, target, edge_type)
  constraint.

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
from helpers.graph._edge_writer import apply_typed_edges  # noqa: E402

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #
COMPANIES_DIR = _REPO_ROOT / "findata" / "Companies"

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
def apply_edges(
    edges: Iterable[tuple[str, str, dict, str]],
    *,
    conn=None,
    dry_run: bool = True,
) -> int:
    """Insert ``co_mentioned_in`` edges into ``graph_edges`` with INSERT OR IGNORE.

    Thin wrapper over :func:`helpers.graph._edge_writer.apply_typed_edges`
    (``edge_type='co_mentioned_in'``, ``symmetric=1``). Kept as the module's
    public API because tests and the CLI call it by name.

    Args:
        edges: Iterable of ``(source, target, properties, source_ref)``.
        conn: Reuse an existing SQLite connection. If None, opens a fresh one.
        dry_run: If True (default), no rows are written; the function still
            counts how many would be inserted (i.e. not already present).

    Returns:
        Number of rows actually inserted (``dry_run=False``) or that would be
        inserted (``dry_run=True``). Rows skipped due to the
        ``UNIQUE(source, target, edge_type)`` constraint are not counted.
    """
    inserted = apply_typed_edges(
        edges,
        edge_type="co_mentioned_in",
        symmetric=1,
        conn=conn,
        dry_run=dry_run,
    )
    if not dry_run:
        backfill_weights(conn=conn, edges=edges)
    return inserted


def backfill_weights(  # noqa: C901
    conn=None,
    *,
    edges=None,
    pair_counts=None,
    edition_to_entities=None,
) -> int:
    """Set ``co_mentioned_in`` edge weights from the per-pair co-mention count.

    Weight = ``count + 1`` (mirroring ``derive_cited_in.backfill_weights``).
    Idempotent: derives the count from (in priority order) an explicit
    ``pair_counts`` mapping, ``edition_to_entities``, the supplied ``edges``,
    or the ``count``/aliases already stamped in ``properties``. Returns the
    number of rows updated.
    """
    own = conn is None
    if own:
        conn = connect()
    try:
        # Resolve pair -> count.
        resolved: dict[tuple[str, str], int] = {}
        if pair_counts is not None:
            for k, v in pair_counts.items():
                key = tuple(sorted(k)) if isinstance(k, (list, tuple)) else k
                resolved[tuple(sorted(key))] = int(v)  # type: ignore[arg-type]
        elif edition_to_entities is not None:
            for pair, n in count_pair_co_mentions(edition_to_entities).items():
                a, b = sorted(pair)
                resolved[(a, b)] = int(n)
        elif edges is not None:
            # Aggregate distinct editions per pair from the edge batch.
            agg: dict[tuple[str, str], set[str]] = {}
            for source, target, props, _sr in edges:
                key = tuple(sorted((source, target)))
                agg.setdefault(key, set()).add(str((props or {}).get("edition", "")))
            resolved = {k: len(v) for k, v in agg.items()}
        else:
            resolved = {}

        total_updated = 0
        if resolved:
            for (a, b), count in resolved.items():
                cur = conn.execute(
                    "UPDATE graph_edges SET weight = ? "
                    "WHERE edge_type = 'co_mentioned_in' "
                    "AND source = ? AND target = ?",
                    (float(count) + 1.0, a, b),
                )
                # symmetric row (target, source) if present
                cur2 = conn.execute(
                    "UPDATE graph_edges SET weight = ? "
                    "WHERE edge_type = 'co_mentioned_in' "
                    "AND source = ? AND target = ?",
                    (float(count) + 1.0, b, a),
                )
                n1 = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                n2 = cur2.rowcount if cur2.rowcount and cur2.rowcount > 0 else 0
                total_updated += n1 + n2
        else:
            # Fall back to properties-stamped count (cited_in style).
            cur = conn.execute(
                "UPDATE graph_edges SET weight = "
                "COALESCE("
                "CAST(json_extract(properties, '$.count') AS REAL),"
                "CAST(json_extract(properties, '$.n_mentions') AS REAL),"
                "CAST(json_extract(properties, '$.n_co_mentions') AS REAL),"
                "CAST(json_extract(properties, '$.co_mention_count') AS REAL),"
                "CAST(json_extract(properties, '$.n_editions') AS REAL)"
                ") + 1.0 "
                "WHERE edge_type = 'co_mentioned_in' "
                "AND ("
                "json_extract(properties, '$.count') IS NOT NULL OR "
                "json_extract(properties, '$.n_mentions') IS NOT NULL OR "
                "json_extract(properties, '$.n_co_mentions') IS NOT NULL OR "
                "json_extract(properties, '$.co_mention_count') IS NOT NULL OR "
                "json_extract(properties, '$.n_editions') IS NOT NULL"
                ") "
                "AND (weight IS NULL OR weight != "
                "COALESCE("
                "CAST(json_extract(properties, '$.count') AS REAL),"
                "CAST(json_extract(properties, '$.n_mentions') AS REAL),"
                "CAST(json_extract(properties, '$.n_co_mentions') AS REAL),"
                "CAST(json_extract(properties, '$.co_mention_count') AS REAL),"
                "CAST(json_extract(properties, '$.n_editions') AS REAL)"
                ") + 1.0)"
            )
            total_updated = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

        if own:
            conn.commit()
        return int(total_updated)
    finally:
        if own:
            conn.close()


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
_ARGS = dcli.DeriveArgsSpec(
    apply_help="Write edges to graph_edges (default: dry-run summary only).",
    stale_help="S1c: skip when no source newer than last derived.",
    corpus=True,
)


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Derive co_mentioned_in edges from newsletter enhancement blocks.",
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
    edges = derive_edges(editions, newsletter_type=args.newsletter)

    # Summary to stderr so stdout stays machine-readable if needed.
    print(
        f"[{args.newsletter}] editions={len(editions)} "
        f"derived_edges={len(edges)} "
        f"({'apply' if args.apply else 'dry-run'})",
        file=sys.stderr,
    )
    # Per-edition breakdown.
    for edition_title in sorted(editions):
        names = editions[edition_title]
        n_pairs = len(names) * (len(names) - 1) // 2
        print(
            f"  {len(names):3d} co  {n_pairs:5d} pairs  {edition_title}",
            file=sys.stderr,
        )

    # Report the per-pair co-mention count distribution (proposal acceptance).
    pc = count_pair_co_mentions(editions)
    from collections import Counter

    dist = Counter(pc.values())
    print(
        "pair_count_distribution=" + ",".join(f"{k}:{dist[k]}" for k in sorted(dist)),
        file=sys.stderr,
    )

    inserted = apply_edges(edges, dry_run=not args.apply)
    action = "inserted" if args.apply else "would insert"
    print(f"{inserted} {action}.", file=sys.stderr)

    if args.verbose:
        for source, target, props, source_ref in edges:
            print(f"{source}\t{target}\t{json.dumps(props, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
