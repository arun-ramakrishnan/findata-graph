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
source_ref prefix, made lifecycle-aware (ontology_governance S1): rows
the roster still produces are reinserted ``active`` (resurrecting a
superseded row); seed-owned rows the roster no longer produces flip to
``status='superseded'`` instead of being deleted, so absorptions and
renames stay queryable. Operator/agent rows (any other source_ref) are
never touched. ``status`` is candidate|active|superseded —
extractor/triage funnels land suggestions as ``candidate``; the
validated ``--promote`` / ``--promote-map`` surfaces promote them
(plan-then-apply; any failure blocks the batch). Enforcement (single
prefLabel among ACTIVE rows, acyclicity, lifecycle hygiene) lives in
the ``concepts`` integrity check (recursive walks, not OWL);
``subtree()`` below is the reusable closure helper (recursive CTE,
active-only by default). Never-blocking: absent source tables are
skipped and reported.

Usage:
    python3 helpers/misc/seed_concepts.py            # dry-run report
    python3 helpers/misc/seed_concepts.py --apply    # seed + converge
    python3 helpers/misc/seed_concepts.py --promote subsector:Software
    python3 helpers/misc/seed_concepts.py --promote-map 'industry:Banks - Regional->subsector:Banks'
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


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Guarded ALTER: add ``column`` to an existing ``table`` where missing."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")  # noqa: S608


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the three tables where missing (idempotent, guarded).

    Includes the S1 lifecycle columns (``status`` on concepts and
    concept_mappings) via CREATE for fresh DBs and guarded ALTER for
    pre-S1 DBs — existing rows carry 'active' by definition.
    """
    _STATUS_DDL = (
        "status TEXT NOT NULL DEFAULT 'active' "
        "CHECK (status IN ('candidate', 'active', 'superseded'))"
    )
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
        f"""
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
            {_STATUS_DDL},
            UNIQUE (scheme_id, concept_code)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS concept_mappings (
            source_scheme  TEXT NOT NULL,
            source_concept TEXT NOT NULL,
            target_scheme  TEXT NOT NULL,
            target_concept TEXT NOT NULL,
            match_type     TEXT NOT NULL CHECK (match_type IN
                             ('exactMatch', 'closeMatch', 'broadMatch', 'narrowMatch')),
            source_ref     TEXT NOT NULL,
            version        TEXT NOT NULL,
            {_STATUS_DDL}
        )
        """
    )
    _ensure_column(conn, "concepts", "status", _STATUS_DDL)
    _ensure_column(conn, "concept_mappings", "status", _STATUS_DDL)
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
    """Converge the three tables onto the collected roster (lifecycle-aware).

    Seed-owned rows the roster still produces are delete-then-reinserted
    ``active`` (the house idiom — resurrection of a superseded row falls
    out naturally); seed-owned rows the roster no longer produces flip to
    ``superseded`` instead of being deleted, so absorptions and renames
    stay queryable. Operator/agent rows (any other source_ref) are never
    touched. ``defer_foreign_keys`` covers the batch: a sub_sector row
    may reference its sector before that row's own INSERT lands.
    """

    def _mapping_key(m: tuple) -> tuple:
        return (m[0], m[1], m[2], m[3], m[4])  # scheme/code both sides + match_type

    schemes, concepts, mappings = _collect(conn)
    roster_concepts = set(concepts)
    roster_mappings = {_mapping_key(m) for m in mappings}

    existing_concepts = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT concept_id, status FROM concepts WHERE source_ref LIKE 'seed:%'"
        )
    }
    existing_mappings_live: dict[tuple, bool] = {}
    for row in conn.execute(
        "SELECT source_scheme, source_concept, target_scheme, target_concept, "
        "match_type, status FROM concept_mappings WHERE source_ref LIKE 'seed:%'"
    ):
        key = _mapping_key(row)
        existing_mappings_live[key] = (
            existing_mappings_live.get(key, False) or row[5] != "superseded"
        )

    superseded_concepts = sorted(
        cid
        for cid, st in existing_concepts.items()
        if st != "superseded" and cid not in roster_concepts
    )
    superseded_mappings = sorted(
        key for key, live in existing_mappings_live.items() if live and key not in roster_mappings
    )
    resurrected_concepts = sum(
        1 for cid in roster_concepts if existing_concepts.get(cid) == "superseded"
    )
    resurrected_mappings = sum(
        1
        for key in roster_mappings
        if key in existing_mappings_live and not existing_mappings_live[key]
    )
    counts = {
        "schemes": len(schemes),
        "concepts": len(concepts),
        "mappings": len(mappings),
        "superseded_concepts": len(superseded_concepts),
        "superseded_mappings": len(superseded_mappings),
        "resurrected_concepts": resurrected_concepts,
        "resurrected_mappings": resurrected_mappings,
    }
    if apply:
        conn.execute("PRAGMA defer_foreign_keys = ON")
        # lifecycle pass: supersede seed-owned rows the roster no longer produces
        conn.executemany(
            "UPDATE concepts SET status='superseded' WHERE concept_id=?",
            [(cid,) for cid in superseded_concepts],
        )
        conn.executemany(
            "UPDATE concept_mappings SET status='superseded' "
            "WHERE source_ref LIKE 'seed:%' AND source_scheme=? AND source_concept=? "
            "AND target_scheme=? AND target_concept=? AND match_type=?",
            superseded_mappings,
        )
        # house idiom for rows the roster still produces (reinsert = active);
        # superseded rows survive — only roster rows are deleted.
        conn.executemany(
            "DELETE FROM concepts WHERE source_ref LIKE 'seed:%' AND concept_id=?",
            [(cid,) for cid in sorted(roster_concepts)],
        )
        conn.executemany(
            "DELETE FROM concept_mappings WHERE source_ref LIKE 'seed:%' "
            "AND source_scheme=? AND source_concept=? AND target_scheme=? "
            "AND target_concept=? AND match_type=?",
            sorted(roster_mappings),
        )
        conn.execute("DELETE FROM concept_schemes")
        conn.executemany(
            "INSERT INTO concept_schemes (scheme_id, label, scheme_type, version, "
            "source_uri, license, attribution, active) VALUES (?, ?, ?, ?, NULL, NULL, NULL, 1)",
            list(schemes.values()),
        )
        conn.executemany(
            "INSERT INTO concepts (concept_id, scheme_id, concept_code, pref_label, "
            "alt_label, notation, broader_id, scope_note, source_ref, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')",
            list(concepts.values()),
        )
        conn.executemany(
            "INSERT INTO concept_mappings (source_scheme, source_concept, "
            "target_scheme, target_concept, match_type, source_ref, version, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
            mappings,
        )
        conn.commit()
    return counts


def subtree(
    conn: sqlite3.Connection, concept_id: str, *, include_inactive: bool = False
) -> list[str]:
    """Transitive narrower closure via recursive CTE (incl. the node itself).

    Active-only by default (ontology_governance S1): superseded and
    candidate concepts stop grouping their descendants — pass
    ``include_inactive=True`` for the full historical closure.
    """
    if include_inactive:
        filters = ("", "")
    else:
        filters = ("AND status='active'", "AND c.status='active'")
    rows = conn.execute(
        f"""
        WITH RECURSIVE closure(id) AS (
            SELECT concept_id FROM concepts WHERE concept_id = ? {filters[0]}
            UNION
            SELECT c.concept_id FROM concepts c
            JOIN closure cl ON c.broader_id = cl.id {filters[1]}
        )
        SELECT id FROM closure ORDER BY id
        """,  # noqa: S608 — interpolated strings are constant status filters, concept_id is bound
        (concept_id,),
    ).fetchall()
    return [row[0] for row in rows]


def _plan_mapping_promotions(
    conn: sqlite3.Connection,
    mapping_specs: list[str],
    errors: list[str],
    plan_mapping_rowids: list[int],
) -> None:
    """Validate ``src_scheme:src->tgt_scheme:tgt[:match_type]`` specs (in place).

    Appends rowids of promotable candidate/superseded rows to
    ``plan_mapping_rowids``; any spec failure appends to ``errors`` (the
    caller blocks the whole batch).
    """
    for spec in mapping_specs:
        left, sep, right = spec.partition("->")
        src_scheme, s_colon, src = left.partition(":")
        tgt_parts = right.split(":")
        if not sep or not s_colon or len(tgt_parts) not in (2, 3):
            errors.append(f"{spec}: expected src_scheme:src->tgt_scheme:tgt[:match_type]")
            continue
        tgt_scheme, tgt = tgt_parts[0], tgt_parts[1]
        match_type = tgt_parts[2] if len(tgt_parts) == 3 else "exactMatch"
        rows = conn.execute(
            "SELECT rowid, status FROM concept_mappings WHERE source_scheme=? AND "
            "source_concept=? AND target_scheme=? AND target_concept=? AND match_type=?",
            (src_scheme, src, tgt_scheme, tgt, match_type),
        ).fetchall()
        if not rows:
            errors.append(f"{spec}: no such mapping")
            continue
        if any(r[1] == "active" for r in rows):
            errors.append(f"{spec}: already active")
            continue
        conflict = conn.execute(
            "SELECT target_concept FROM concept_mappings WHERE source_scheme=? AND "
            "source_concept=? AND match_type=? AND status='active'",
            (src_scheme, src, match_type),
        ).fetchone()
        if conflict is not None:
            errors.append(f"{spec}: conflicting active mapping -> {conflict[0]}")
            continue
        plan_mapping_rowids.extend(r[0] for r in rows)


def promote(
    conn: sqlite3.Connection,
    concept_specs: list[str],
    mapping_specs: list[str],
) -> tuple[list[str], list[str]]:
    """Plan-then-apply promotion of candidate/superseded rows to active.

    The S3 ``--set-cin`` contract: every spec is validated FIRST and any
    failure blocks the whole batch — nothing is applied. Concept specs
    are ``scheme:code``; mapping specs are
    ``src_scheme:src->tgt_scheme:tgt[:match_type]`` (match_type defaults
    to exactMatch). Gates: target exists, not already active, and for
    mappings no other active mapping with the same source+match_type
    (a conflicting crosswalk).
    """
    plan_concepts: list[str] = []
    plan_mapping_rowids: list[int] = []
    errors: list[str] = []
    for spec in concept_specs:
        scheme_id, _, code = spec.partition(":")
        if not scheme_id or not code:
            errors.append(f"{spec}: expected scheme:code")
            continue
        row = conn.execute(
            "SELECT concept_id, status FROM concepts WHERE scheme_id=? AND concept_code=?",
            (scheme_id, code),
        ).fetchone()
        if row is None:
            errors.append(f"{spec}: no such concept")
        elif row[1] == "active":
            errors.append(f"{spec}: already active")
        else:
            plan_concepts.append(row[0])
    _plan_mapping_promotions(conn, mapping_specs, errors, plan_mapping_rowids)
    if errors:
        return [], errors  # batch blocked — the plan is void, report nothing applied
    conn.executemany(
        "UPDATE concepts SET status='active' WHERE concept_id=?",
        [(cid,) for cid in plan_concepts],
    )
    conn.executemany(
        "UPDATE concept_mappings SET status='active' WHERE rowid=?",
        [(rid,) for rid in plan_mapping_rowids],
    )
    conn.commit()
    applied = [f"concept {cid} -> active" for cid in plan_concepts]
    applied += [f"mapping rowid {rid} -> active" for rid in plan_mapping_rowids]
    return applied, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    parser.add_argument(
        "--db", type=Path, default=None, help="SQLite path (default: live research.db)"
    )
    parser.add_argument(
        "--promote",
        default=None,
        help="comma list of scheme:code concepts to promote candidate/superseded -> active",
    )
    parser.add_argument(
        "--promote-map",
        default=None,
        help="comma list of src_scheme:src->tgt_scheme:tgt[:match_type] mappings to promote",
    )
    args = parser.parse_args(argv)
    if (args.promote or args.promote_map) and args.apply:
        parser.error("--promote/--promote-map cannot combine with --apply")

    conn = connect(args.db or DEFAULT_DB_PATH)
    try:
        ensure_schema(conn)
        if args.promote or args.promote_map:
            concept_specs = [s for s in (args.promote or "").split(",") if s]
            mapping_specs = [s for s in (args.promote_map or "").split(",") if s]
            applied, errors = promote(conn, concept_specs, mapping_specs)
            for line in applied:
                print(f"[concepts] promoted {line}")
            for line in errors:
                print(f"[concepts] ERROR {line}")
            if errors:
                print("[concepts] batch BLOCKED — nothing applied")
                return 1
            return 0
        counts = seed(conn, apply=args.apply)
        mode = "APPLIED" if args.apply else "DRY-RUN"
        present = _tables_present(conn)
        skipped = [t for t, ok in present.items() if not ok]
        print(
            f"[concepts] {counts['schemes']} schemes / {counts['concepts']} concepts / "
            f"{counts['mappings']} mappings [{mode}]"
            + (f" (sources skipped: {', '.join(skipped)})" if skipped else "")
        )
        lifecycle = (
            counts["superseded_concepts"]
            + counts["superseded_mappings"]
            + counts["resurrected_concepts"]
            + counts["resurrected_mappings"]
        )
        if lifecycle:
            print(
                f"[concepts] lifecycle [{mode}]: superseded "
                f"{counts['superseded_concepts']}c/{counts['superseded_mappings']}m, "
                f"resurrected {counts['resurrected_concepts']}c/"
                f"{counts['resurrected_mappings']}m"
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
