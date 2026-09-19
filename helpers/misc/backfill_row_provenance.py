#!/usr/bin/env python3
"""Converge row-level provenance (agent_id + source_tier) from source_ref prefixes.

S1 of the ontology_convention_stack proposal (2026-09-14): PROV-O
Starting-Point lineage as table conventions — a ``provenance_agents``
registry plus nullable ``agent_id`` / ``source_tier`` columns on the
five fact tables (graph_edges, events, quotes, company_metrics,
hyper_edges). ``source_ref`` is NEVER rewritten: it stays the
idempotency key every LIKE sweep depends on; the two columns are a
converged projection of its deterministic prefixes (PRE_FULL-legal per
the maint invariant: a projection of already-stamped state, the same
class as the okf sources[] converger).

Tiers (proposal D-O3 enum): manual | migration | derive | regulator |
external. ``regulator`` has no producer yet — the column lands reserved
for it.

Citation (S5 glossary convention): the lineage vocabulary is borrowed
from the W3C PROV-O Recommendation — agents/``wasGeneratedBy``
semantics (https://www.w3.org/TR/prov-o/); license + usage notes in
``doc/reference/ontology_glossary.md``.

Idempotent and never-blocking: CREATE IF NOT EXISTS + guarded ALTERs;
re-runs converge only rows where agent_id IS NULL; absent tables are
skipped; the exit code is always 0 (coverage gaps surface via the
``provenance_coverage`` integrity check, WARNING severity). Prefixes
are matched with LIKE-escaping so underscores in lane names
(``co_mentioned``) are literal, never single-char wildcards.

Usage:
    python3 helpers/misc/backfill_row_provenance.py           # dry-run report
    python3 helpers/misc/backfill_row_provenance.py --apply   # seed + converge
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # noqa: E402

from helpers.core.db import DEFAULT_DB_PATH, connect  # noqa: E402
from helpers.core.vocab import SOURCE_TIER_VALUES, sql_in  # noqa: E402

# The fact tables that carry (or will carry) agent_id + source_tier.
FACT_TABLES: tuple[str, ...] = (
    "graph_edges",
    "events",
    "quotes",
    "company_metrics",
    "hyper_edges",
)

# (source_ref prefix, agent_id, source_tier). Longest prefix wins —
# two-segment lanes sort ahead of their one-segment fallback (the bare
# "derive" row catches lanes added before they get a specific entry).
# Measured against the live store 2026-09-14: every prefix below is
# attested; unmapped prefixes land as NULL and surface in the
# provenance_coverage integrity check.
PREFIX_AGENTS: tuple[tuple[str, str, str], ...] = (
    ("derive:quotes", "derive_insights", "derive"),
    ("derive:metrics", "derive_insights", "derive"),
    ("derive:events", "derive_events", "derive"),
    ("derive:hyperedges", "derive_hyperedges", "derive"),
    ("derive:co_mentioned", "derive_co_mentions", "derive"),
    ("derive:themes", "derive_themes", "derive"),
    ("derive:cited_in", "derive_cited_in", "derive"),
    ("derive:countries", "derive_countries", "derive"),
    ("derive:relations", "extract_relations", "derive"),
    ("derive:sector_hierarchy", "build_sector_hierarchy", "derive"),
    ("embeddings", "enrich_relations", "derive"),
    ("yfinance", "enrich_relations", "external"),
    ("googlefinance", "googlefinance", "external"),
    ("parse_newsletter", "parse_newsletter", "manual"),
    ("manual", "manual", "manual"),
    ("triage", "triage_accept", "manual"),
    ("pending_relations", "triage_accept", "manual"),
    ("fix", "manual", "manual"),
    ("sector-sync:stub", "stub_sector_backfill", "migration"),
    ("move_sector", "move_sector", "manual"),
    ("extract_relations", "extract_relations", "derive"),
    ("migration", "migration", "migration"),
    ("backfill", "backfill_sector_edges", "migration"),
    ("stub_sector_backfill", "stub_sector_backfill", "migration"),
    ("coinfer", "coinfer", "migration"),
    ("Phase 2 seed", "phase2_seed", "migration"),
    ("derive", "derive_unspecified", "derive"),
)

# Registry seeds: agent_id -> (name, script path or None). Historical
# lanes carry version='baseline': pre-registry history has no honest
# per-version record, and fake precision is worse than a label
# (proposal D-O3 spirit). Producers that stamp their own rows from
# S1b onward insert richer rows at write time.
AGENT_SEED: dict[str, tuple[str, str | None]] = {
    "derive_insights": (
        "derive_insights (quotes + company_metrics)",
        "helpers/graph/derive_insights.py",
    ),
    "derive_events": ("derive_events", "helpers/graph/derive_events.py"),
    "derive_hyperedges": ("derive_hyperedges", "helpers/graph/derive_hyperedges.py"),
    "derive_co_mentions": ("derive_co_mentions", "helpers/graph/derive_co_mentions.py"),
    "derive_themes": ("derive_themes", "helpers/graph/derive_themes.py"),
    "derive_cited_in": ("derive_cited_in", "helpers/graph/derive_cited_in.py"),
    "derive_countries": ("derive_countries", "helpers/graph/derive_countries.py"),
    "extract_relations": ("extract_relations", "helpers/graph/extract_relations.py"),
    "build_sector_hierarchy": ("build_sector_hierarchy", "helpers/graph/build_sector_hierarchy.py"),
    "enrich_relations": (
        "enrich_relations (semantic_peer + yfinance lanes)",
        "helpers/maintenance/enrich_relations.py",
    ),
    "parse_newsletter": ("parse_newsletter", "helpers/core/parse_newsletter.py"),
    "triage_accept": ("triage queue accept lanes", "helpers/graph/triage_pending_relations.py"),
    "move_sector": ("move_sector", "helpers/maintenance/move_sector.py"),
    "phase2_seed": ("Phase 2 seed (historical one-off)", None),
    "googlefinance": ("googlefinance (historical lane, script retired)", None),
    "manual": ("manual entry", None),
    "migration": ("one-shot migrations", None),
    "backfill_sector_edges": ("backfill_sector_edges (historical lane)", None),
    "stub_sector_backfill": ("triage stub funnel", None),
    "coinfer": ("coinfer (S21 co-inference lane)", None),
    "derive_unspecified": ("derive lane without a specific mapping", None),
}

SEED_VERSION = "baseline"
SEED_AS_OF = "2026-09-14"

# Sorted longest-prefix-first once, at import.
_PREFIX_ORDER: tuple[tuple[str, str, str], ...] = tuple(
    sorted(PREFIX_AGENTS, key=lambda entry: -len(entry[0]))
)


def _like(prefix: str) -> str:
    """Escape a literal prefix for LIKE ... ESCAPE '\\'.

    SQLite LIKE has no bracket character classes — backslash is the
    escape char, so ``_`` must become ``\\_`` or lane names like
    ``co_mentioned`` silently match any character there.
    """
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the registry + add the two columns where missing (idempotent)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS provenance_agents (
            agent_id  TEXT PRIMARY KEY,
            name      TEXT NOT NULL,
            version   TEXT NOT NULL,
            repo_ref  TEXT,
            script    TEXT,
            command   TEXT,
            as_of     TEXT NOT NULL
        )
        """
    )
    for table in FACT_TABLES:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:  # table absent (minimal fixtures) — skip, never block
            continue
        if "agent_id" not in cols:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN agent_id TEXT "  # noqa: S608  # table from the in-repo FACT_TABLES constant
                f"REFERENCES provenance_agents(agent_id) "
                f"ON UPDATE CASCADE ON DELETE SET NULL"
            )
        if "source_tier" not in cols:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN source_tier TEXT "
                f"CHECK (source_tier IN ({sql_in(SOURCE_TIER_VALUES)}))"
            )
    conn.commit()


def seed_agents(conn: sqlite3.Connection) -> int:
    """Insert missing registry rows; returns the number inserted."""
    inserted = 0
    for agent_id, (name, script) in AGENT_SEED.items():
        cur = conn.execute(
            "INSERT OR IGNORE INTO provenance_agents "
            "(agent_id, name, version, repo_ref, script, command, as_of) "
            "VALUES (?, ?, ?, NULL, ?, NULL, ?)",
            (agent_id, name, SEED_VERSION, script, SEED_AS_OF),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


def _match_prefix(source_ref: str) -> tuple[str, str] | None:
    """Longest-prefix match for one source_ref (dry-run reporting path)."""
    for prefix, agent, tier in _PREFIX_ORDER:
        if source_ref.startswith(prefix):
            return agent, tier
    return None


def converge(conn: sqlite3.Connection, *, apply: bool) -> dict[str, dict[str, int]]:
    """Map agent_id/source_tier onto every unmapped row (longest prefix first).

    Returns a per-table report: total rows, mapped-this-run (or that
    WOULD map, in dry-run), and rows still unmapped after the pass.
    """
    report: dict[str, dict[str, int]] = {}
    for table in FACT_TABLES:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not {"source_ref", "agent_id", "source_tier"} <= cols:
            report[table] = {"total": 0, "mapped": 0, "unmapped": 0, "skipped": 1}
            continue
        touched = 0
        if apply:
            for prefix, agent, tier in _PREFIX_ORDER:
                touched += conn.execute(
                    f"UPDATE {table} SET agent_id = ?, source_tier = ? "  # noqa: S608  # table from FACT_TABLES; values are `?`-parameters
                    f"WHERE agent_id IS NULL AND source_ref LIKE ? ESCAPE '\\'",
                    (agent, tier, _like(prefix)),
                ).rowcount
        else:
            # first-match-wins in Python: without writes, overlapping
            # prefixes (specific lane + bare-derive fallback) would
            # double-count under per-prefix SQL COUNTs
            for (source_ref,) in conn.execute(
                f"SELECT source_ref FROM {table} WHERE agent_id IS NULL AND source_ref IS NOT NULL"  # noqa: S608  # table from FACT_TABLES; no user input
            ):
                if _match_prefix(source_ref) is not None:
                    touched += 1
        total, unmapped = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(agent_id IS NULL), 0) FROM {table}"  # noqa: S608  # table from FACT_TABLES; no user input
        ).fetchone()
        report[table] = {
            "total": total,
            "mapped": touched,
            "unmapped": unmapped,
            "skipped": 0,
        }
    if apply:
        conn.commit()
    return report


def unmapped_prefixes(
    conn: sqlite3.Connection, table: str, limit: int = 10
) -> list[tuple[str, int]]:
    """Top source_ref first-segments still carrying NULL agent_id."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if not {"source_ref", "agent_id"} <= cols:
        return []
    return [
        (row[0], row[1])
        for row in conn.execute(
            f"SELECT substr(source_ref, 1, instr(source_ref || ':', ':') - 1), COUNT(*) "  # noqa: S608  # table from FACT_TABLES; LIMIT is a `?`-parameter
            f"FROM {table} WHERE agent_id IS NULL AND source_ref IS NOT NULL "
            f"GROUP BY 1 ORDER BY 2 DESC LIMIT ?",
            (limit,),
        )
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write (default: dry-run report)")
    parser.add_argument(
        "--db", type=Path, default=None, help="SQLite path (default: live research.db)"
    )
    args = parser.parse_args(argv)

    conn = connect(args.db or DEFAULT_DB_PATH)
    try:
        ensure_schema(conn)
        if args.apply:
            seeded = seed_agents(conn)
            print(f"[provenance] registry: {seeded} agent(s) seeded")
        report = converge(conn, apply=args.apply)
        mode = "APPLIED" if args.apply else "DRY-RUN"
        for table, stats in report.items():
            if stats.get("skipped"):
                print(f"[provenance] {table}: skipped (table/columns absent)")
                continue
            print(
                f"[provenance] {table}: {stats['mapped']} "
                f"{'mapped' if args.apply else 'would map'} "
                f"({stats['unmapped']}/{stats['total']} "
                f"{'unmapped after pass' if args.apply else 'currently unmapped'}) "
                f"[{mode}]"
            )
            if stats["unmapped"]:
                for prefix, n in unmapped_prefixes(conn, table):
                    print(f"[provenance]   unmapped prefix {prefix!r}: {n}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
