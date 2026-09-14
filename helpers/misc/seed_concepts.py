#!/usr/bin/env python3
"""Seed SKOS-style concept schemes, concepts, and crosswalks (S2).

S2 of the ontology_convention_stack proposal (2026-09-14): SKOS as
table conventions. Three tables — ``concept_schemes``, ``concepts``
(pref_label/alt_label/notation/broader_id, UNIQUE(scheme_id,
concept_code)), ``concept_mappings`` (the ONE crosswalk home, D-O1; no
match-id columns on concepts) — seeded as a deterministic projection of
already-stamped state (PRE_FULL-legal per the maint invariant):

- 9 ``entity_tags`` namespaces → schemes, tag values → concepts;
- the taxonomy trio (super_sector/sector/sub_sector entities) →
  concepts with ``broader_id`` from ``belongs_to`` edges — entity names
  are CANONICAL where tags and entities collide case-insensitively
  (post-S17 the subsector tags lag the entity roster 57 vs 100);
- ``industry`` label scheme from the live ``hyper_edges`` industry
  labels;
- ``concept_mappings`` from ``derive_hyperedges.SUB_SECTOR_ALIASES``
  (the S11/S17 operator-curated Yahoo-label → sub_sector map) with
  match_type exactMatch.

Citation (S5 glossary convention): the SKOS vocabulary names —
prefLabel / broader / exactMatch — are borrowed from the W3C SKOS
Reference (https://www.w3.org/TR/skos-reference/); license + usage
notes in ``doc/reference/ontology_glossary.md``.

Idempotency = the house DELETE-then-INSERT idiom on the ``seed:%``
source_ref prefix. Enforcement (single prefLabel, acyclicity) lives in
the ``concepts`` integrity check (recursive walks, not OWL);
``subtree()`` below is the reusable closure helper (recursive CTE).
Never-blocking: absent source tables are skipped and reported.

Usage:
    python3 helpers/misc/seed_concepts.py            # dry-run report
    python3 helpers/misc/seed_concepts.py --apply    # seed + converge
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # noqa: E402

from helpers.core.db import DEFAULT_DB_PATH, connect  # noqa: E402

# The tag namespaces that become schemes (db_schema.md roster).
_TAG_NAMESPACES: tuple[str, ...] = (
    "entity_type",
    "sector",
    "subsector",
    "market_cap",
    "geography",
    "holding_company",
    "business_model",
    "risk_investment",
    "investment_theme",
)

# scheme_id -> (label, scheme_type, version) for the non-tag schemes.
_EXTRA_SCHEMES: dict[str, tuple[str, str, str]] = {
    "super_sector": ("Super sectors", "taxonomy", "S17"),
    "industry": ("Yahoo/yfinance industry labels", "label_scheme", "yfinance"),
}

SOURCE_SCHEMES = "seed:schemes:roster"
SOURCE_TAGS = "seed:concepts:tags"
SOURCE_TAXONOMY = "seed:concepts:taxonomy"
SOURCE_INDUSTRY = "seed:concepts:industry"
SOURCE_MAPPINGS = "seed:mappings:sub_sector_aliases"

# entity_type -> scheme_id for the taxonomy trio.
_TAXONOMY: tuple[tuple[str, str], ...] = (
    ("super_sector", "super_sector"),
    ("sector", "sector"),
    ("sub_sector", "subsector"),
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the three tables where missing (idempotent, guarded)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS concept_schemes (
            scheme_id    TEXT PRIMARY KEY,
            label        TEXT NOT NULL,
            scheme_type  TEXT NOT NULL,
            version      TEXT NOT NULL,
            source_uri   TEXT,
            license      TEXT,
            attribution  TEXT,
            active       INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS concepts (
            concept_id   TEXT PRIMARY KEY,
            scheme_id    TEXT NOT NULL REFERENCES concept_schemes(scheme_id),
            concept_code TEXT NOT NULL,
            pref_label   TEXT NOT NULL,
            alt_label    TEXT,
            notation     TEXT,
            broader_id   TEXT REFERENCES concepts(concept_id),
            scope_note   TEXT,
            source_ref   TEXT NOT NULL,
            UNIQUE (scheme_id, concept_code)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS concept_mappings (
            source_scheme  TEXT NOT NULL,
            source_concept TEXT NOT NULL,
            target_scheme  TEXT NOT NULL,
            target_concept TEXT NOT NULL,
            match_type     TEXT NOT NULL CHECK (match_type IN
                             ('exactMatch', 'closeMatch', 'broadMatch', 'narrowMatch')),
            source_ref     TEXT NOT NULL,
            version        TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _tables_present(conn: sqlite3.Connection) -> dict[str, bool]:
    present = {}
    for table in ("entities", "entity_tags", "graph_edges", "hyper_edges"):
        present[table] = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name=? AND type='table'", (table,)
            ).fetchone()
            is not None
        )
    return present


def _collect(  # noqa: C901  # one gather pass per source family (taxonomy → tags → industry → crosswalk); each block feeds the next's absorption lookups
    conn: sqlite3.Connection,
) -> tuple[dict, dict, list]:
    """Gather schemes, concepts, and mappings from the stamped state.

    Returns (schemes, concepts, mappings) where concepts maps
    concept_id -> row tuple and schemes maps scheme_id -> row tuple.
    Entity names are canonical on collision: a tag whose lowercase form
    matches a taxonomy entity (``sector/agriculture`` vs entity
    ``Agriculture``) is absorbed by the entity row, never duplicated;
    genuinely stale tag-only values are kept so nothing silently
    vanishes.
    """
    present = _tables_present(conn)
    schemes: dict[str, tuple] = {}
    concepts: dict[str, tuple] = {}
    mappings: list[tuple] = []

    for ns in _TAG_NAMESPACES:
        schemes[ns] = (ns, ns.replace("_", " "), "tag_namespace", "live-tags")
    for scheme_id, (label, stype, version) in _EXTRA_SCHEMES.items():
        schemes[scheme_id] = (scheme_id, label, stype, version)

    def _concept(scheme_id: str, code: str, source: str, broader: str | None = None):
        concept_id = f"{scheme_id}:{code}"
        alt = code.replace("_", " ") if "_" in code else None
        concepts[concept_id] = (
            concept_id,
            scheme_id,
            code,
            code,
            alt,
            None,
            broader,
            None,
            source,
        )

    scheme_of_type = dict(_TAXONOMY)

    # Taxonomy first: entity names canonical + the absorption lookup.
    entity_types = {}
    if present["entities"]:
        for name, etype in conn.execute(
            "SELECT name, entity_type FROM entities "
            "WHERE entity_type IN ('super_sector', 'sector', 'sub_sector')"
        ):
            entity_types[name] = etype
    broader_of = {}
    if present["graph_edges"]:
        for src, tgt in conn.execute(
            "SELECT source, target FROM graph_edges WHERE edge_type='belongs_to'"
        ):
            if src in entity_types and tgt in entity_types:
                broader_of[src] = tgt
    entity_codes: dict[str, set[str]] = {}
    for name, etype in entity_types.items():
        scheme_id = scheme_of_type[etype]
        entity_codes.setdefault(scheme_id, set()).add(name.lower())
        target = broader_of.get(name)
        broader_id = f"{scheme_of_type[entity_types[target]]}:{target}" if target else None
        _concept(scheme_id, name, SOURCE_TAXONOMY, broader_id)

    if present["entity_tags"]:
        for (tag,) in conn.execute(
            "SELECT DISTINCT tag FROM entity_tags WHERE instr(tag, '/') > 0"
        ):
            ns, _, code = tag.partition("/")
            if ns not in _TAG_NAMESPACES:
                continue
            if code.lower() in entity_codes.get(ns, set()):
                continue  # absorbed by the canonical entity row
            _concept(ns, code, SOURCE_TAGS)

    # Industry label scheme from the live hyper store.
    if present["hyper_edges"]:
        for (label,) in conn.execute(
            "SELECT DISTINCT label FROM hyper_edges WHERE edge_type='industry'"
        ):
            _concept("industry", label, SOURCE_INDUSTRY)

    # Crosswalk: the operator-curated S11/S17 alias map.
    try:
        from helpers.graph.derive_hyperedges import SUB_SECTOR_ALIASES

        for label, target in SUB_SECTOR_ALIASES.items():
            mappings.append(
                (
                    "industry",
                    label,
                    "subsector",
                    target,
                    "exactMatch",
                    SOURCE_MAPPINGS,
                    "S11+S17",
                )
            )
    except ImportError:
        pass  # derive_hyperedges absent on minimal fixtures — skip

    return schemes, concepts, mappings


def seed(conn: sqlite3.Connection, *, apply: bool) -> dict[str, int]:
    """Converge the three tables (DELETE seed:% rows, re-INSERT).

    ``defer_foreign_keys`` covers the batch: a sub_sector row may
    reference its sector before that row's own INSERT lands.
    """
    schemes, concepts, mappings = _collect(conn)
    counts = {
        "schemes": len(schemes),
        "concepts": len(concepts),
        "mappings": len(mappings),
    }
    if apply:
        conn.execute("PRAGMA defer_foreign_keys = ON")
        conn.execute("DELETE FROM concept_mappings WHERE source_ref LIKE 'seed:%'")
        conn.execute("DELETE FROM concepts WHERE source_ref LIKE 'seed:%'")
        conn.execute("DELETE FROM concept_schemes")
        conn.executemany(
            "INSERT INTO concept_schemes (scheme_id, label, scheme_type, version, "
            "source_uri, license, attribution, active) VALUES (?, ?, ?, ?, NULL, NULL, NULL, 1)",
            list(schemes.values()),
        )
        conn.executemany(
            "INSERT INTO concepts (concept_id, scheme_id, concept_code, pref_label, "
            "alt_label, notation, broader_id, scope_note, source_ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(concepts.values()),
        )
        conn.executemany(
            "INSERT INTO concept_mappings (source_scheme, source_concept, "
            "target_scheme, target_concept, match_type, source_ref, version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            mappings,
        )
        conn.commit()
    return counts


def subtree(conn: sqlite3.Connection, concept_id: str) -> list[str]:
    """Transitive narrower closure via recursive CTE (incl. the node itself)."""
    rows = conn.execute(
        """
        WITH RECURSIVE closure(id) AS (
            SELECT concept_id FROM concepts WHERE concept_id = ?
            UNION
            SELECT c.concept_id FROM concepts c
            JOIN closure cl ON c.broader_id = cl.id
        )
        SELECT id FROM closure ORDER BY id
        """,
        (concept_id,),
    ).fetchall()
    return [row[0] for row in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    parser.add_argument(
        "--db", type=Path, default=None, help="SQLite path (default: live research.db)"
    )
    args = parser.parse_args(argv)

    conn = connect(args.db or DEFAULT_DB_PATH)
    try:
        ensure_schema(conn)
        counts = seed(conn, apply=args.apply)
        mode = "APPLIED" if args.apply else "DRY-RUN"
        present = _tables_present(conn)
        skipped = [t for t, ok in present.items() if not ok]
        print(
            f"[concepts] {counts['schemes']} schemes / {counts['concepts']} concepts / "
            f"{counts['mappings']} mappings [{mode}]"
            + (f" (sources skipped: {', '.join(skipped)})" if skipped else "")
        )
        if args.apply:
            for scheme in ("super_sector", "sector"):
                roots = conn.execute(
                    "SELECT COUNT(*) FROM concepts WHERE scheme_id=? AND broader_id IS NULL",
                    (scheme,),
                ).fetchone()[0]
                print(f"[concepts] {scheme}: {roots} root(s)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
