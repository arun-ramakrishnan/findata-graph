#!/usr/bin/env python3
"""
Database Integrity Checking for Knowledge Graph Verification System

This script implements the first requirement: Database Integrity Checking
- Query all entities and identify those with missing or invalid file paths
- Validate file paths against actual filesystem existence
- Generate comprehensive reports on data integrity status
- Support both companies and sectors entity types
"""

import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Repo root: helpers/misc/database_integrity_check.py -> parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The event-type vocabulary (D7). Imported at module level so this file is
# self-contained when run as a subprocess (the static check requires any
# helpers.* import to be preceded by a sys.path bootstrap).
from helpers.validators.static_checks import CANONICAL_EVENT_TYPES  # noqa: E402
from helpers.core.frontmatter import extract_tags as _note_yaml_tags  # noqa: E402

# Canonical edge-type allowlist. Must match EDGE_REGISTRY keys in
# helpers/graph/query.py. The relations view (now unfiltered — see
# migrate_to_graph_edges.py RELATIONS_VIEW_DDL) exposes all edge types,
# so check_relations() validates graph_edges.edge_type against this set.
# Keep in sync if a new edge type is registered.
_KNOWN_EDGE_TYPES: tuple[str, ...] = (
    "part_of",
    "has_company",
    "competes_with",
    "jv_with",
    "same_group",
    "supplier_to",
    "customer_of",
    "acquired",
    "subsidiary_of",
    "co_mentioned_in",
    "belongs_to",  # Bundle M4: sector hierarchy (sector->super_sector, sub_sector->sector)
    "exposed_to",  # D4: cross-sector theme membership (company -> theme)
    "cited_in",  # okf_activation P: OKF provenance (company/sector -> edition)
    "semantic_peer",  # E3: embedding cosine neighbours (Relations 2.0)
    "invested_in",  # E5: institution -> company holders (Relations 2.0)
)


# --------------------------------------------------------------------------- #
# Check registry — the single declaration of what the checker validates.      #
# Adding a check is now a one-line edit here (a new Check row), not four      #
# coordinated edits across check_integrity() + print_report() +               #
# write_report_file() + main().                                               #
#                                                                             #
#   name     : results-dict key (results[name] = method()'s return dict).     #
#   method   : name of the check_<x> method on DatabaseIntegrityChecker.      #
#   severity : 'error' (gate-failing — counted toward the exit code) or       #
#              'warning' (advisory — reported, never fails the gate).         #
#   label    : human-readable heading for the printed/written report.         #
#                                                                             #
# The file_path/entity validation (the original check, run inline in          #
# check_integrity over get_all_entities()) is NOT in the registry — it has    #
# bespoke aggregation logic (by_entity_type, validation_rate, etc.) that      #
# doesn't fit the uniform check_<x>()->dict shape. Everything else is.        #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Check:
    name: str
    method: str
    severity: str  # "error" | "warning"
    label: str


_CHECKS: tuple[Check, ...] = (
    Check("relations", "check_relations", "error", "Relations"),
    Check("entity_tags", "check_entity_tags", "error", "Entity Tags"),
    Check("note_tags", "check_note_tags", "error", "Note Tags (source trees)"),
    Check("events", "check_events", "error", "Events"),
    Check("quotes", "check_quotes", "error", "Quotes"),
    Check("company_metrics", "check_company_metrics", "error", "Company Metrics"),
    Check("orphan_companies", "check_orphan_companies", "error", "Orphan Companies"),
    Check("hierarchy", "check_hierarchy", "error", "Sector Hierarchy"),
    Check(
        "market_cap_conflicts", "check_market_cap_conflicts", "error", "Market Cap Tag Conflicts"
    ),
    Check("cache_consistency", "check_cache_consistency", "error", "DuckDB Cache Consistency"),
    Check("normalization", "check_normalization", "error", "Normalization"),
    Check(
        "duplicate_tickers",
        "check_duplicate_tickers",
        "error",
        "Semantic Uniqueness (duplicate tickers)",
    ),
    Check("fuzzy_duplicates", "check_fuzzy_duplicate_names", "warning", "Fuzzy Name Similarity"),
    Check("validity_window", "check_validity_window", "warning", "Edge Validity Window Coverage"),
    Check("graph_summary", "check_graph_summary", "warning", "Graph Summary"),
    Check("db_meta", "check_db_meta", "error", "DB Meta (generation + user_version)"),
)


class DatabaseIntegrityChecker:
    def __init__(
        self,
        db_path: str | None = None,
        base_path: str | None = None,
    ):
        # Derive from repo root by default so the script is portable; allow
        # explicit overrides for tests / alternate layouts.
        self.db_path = db_path or str(_REPO_ROOT / "memory" / "research.db")
        self.base_path = Path(base_path) if base_path else _REPO_ROOT
        self.findata_path = self.base_path / "findata"
        self._conn: sqlite3.Connection | None = None

    def get_connection(self) -> sqlite3.Connection:
        """Get a shared database connection.

        Memoized on the instance so a single integrity-check run reuses
        one connection (avoids ~5-10ms setup cost per query for WAL + FK
        pragma). Call close() at the end of the run.
        """
        if self._conn is not None:
            return self._conn
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Database not found: {self.db_path}")
        # P0: route through central helper so FK/WAL/busy_timeout are canonical
        from helpers.core.db import connect as _db_connect

        self._conn = _db_connect(self.db_path)
        return self._conn

    def close(self) -> None:
        """Close the memoized connection if open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def get_all_entities(self) -> list[dict]:
        """Query all entities from the database"""
        conn = self.get_connection()
        cursor = conn.cursor()

        query = """
        SELECT name, entity_type, file_path, normalized_name, sector_classification
        FROM entities
        ORDER BY entity_type, name
        """

        cursor.execute(query)
        columns = [description[0] for description in cursor.description]
        entities = []

        for row in cursor.fetchall():
            entity = dict(zip(columns, row))
            entities.append(entity)

        return entities

    def validate_file_path(self, file_path: str, entity_type: str = "") -> tuple[bool, str]:
        """
        Validate if file path exists and follows correct format
        Returns: (is_valid, validation_message)
        """
        if not file_path:
            return False, "Empty file path"

        # Convert to absolute path
        full_path = self.base_path / file_path

        # Check if file exists
        if not full_path.exists():
            return False, f"File does not exist: {full_path}"

        # Check if it's a markdown file
        if not file_path.endswith(".md"):
            return False, f"Not a markdown file: {file_path}"

        # Check directory structure compliance (entity-type-aware; Bundle M4)
        if not self._check_directory_structure(file_path, entity_type):
            return False, f"Invalid directory structure: {file_path}"

        # Check filename format. Editions are exempt: their filenames come
        # from the OCR conversion pipeline, not the entity-naming convention
        # (5 live stems legitimately break PascalCase, e.g. "Bets and
        # blueprints.md" — okf_activation P).
        if entity_type != "edition" and not self._check_filename_format(file_path):
            return False, f"Invalid filename format: {file_path}"

        return True, "Valid"

    def _check_directory_structure(self, file_path: str, entity_type: str = "") -> bool:
        """Check if file path follows correct directory structure.

        Per entity_type:
          - company       -> findata/Companies/{Sector}/{Entity}.md
          - sector        -> findata/Sectors/{Entity}.md (subdirs ok)
          - super_sector  -> findata/Super_Sectors/{Entity}.md (Bundle M4)
          - sub_sector    -> no note expected (facets; exempt from this check)
          - edition       -> findata/{The_Chatter|The_PlotLines|Points_And_
                             Figures}/{Edition}.md (okf_activation P)
        """
        # Bundle M4: sub_sectors are intra-sector facets (Iron and Steel,
        # Airlines, ...) with no dedicated note — file_path is legitimately
        # NULL. They are validated as path-exempt in check_entities(), so
        # reaching here with one is unexpected, but accept any path.
        # E5: institutions (institution -> company holders) are likewise noteless.
        if entity_type in ("sub_sector", "institution"):
            return True
        if file_path.startswith("findata/Companies/"):
            parts = file_path.split("/")
            return len(parts) == 4  # findata, Companies, {Sector}, {Entity}.md
        elif file_path.startswith("findata/Sectors/"):
            parts = file_path.split("/")
            return len(parts) >= 3  # findata, Sectors, {Entity}.md (or subdirs)
        elif file_path.startswith("findata/Super_Sectors/"):
            parts = file_path.split("/")
            return len(parts) == 3  # findata, Super_Sectors, {Entity}.md
        elif entity_type == "edition" and file_path.startswith(
            ("findata/The_Chatter/", "findata/The_PlotLines/", "findata/Points_And_Figures/")
        ):
            parts = file_path.split("/")
            return len(parts) == 3  # findata, {tree}, {Edition}.md
        return False

    def _check_filename_format(self, file_path: str) -> bool:
        """Check if filename follows EntityName.md format with PascalCase and underscores"""
        filename = os.path.basename(file_path)
        name_without_ext = filename.replace(".md", "")
        if not name_without_ext:
            return False
        # Allow leading digit for brand names that legitimately start with a number
        # (e.g. 360_ONE_WAM). Other constraints (no special chars, etc.) still apply.
        return bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9_]*$", name_without_ext))

    # ------------------------------------------------------------------ #
    # Relations integrity                                                #
    # ------------------------------------------------------------------ #
    def check_relations(self) -> dict:
        """
        Integrity of the relations table.

        Relation model (doc/design/db_schema.md): part_of = company->sector,
        has_company = sector->company. Every part_of must have a matching
        has_company and vice versa. All ERROR-level here; the live graph is
        pristine, so any nonzero value is a real regression.
        """
        conn = self.get_connection()
        cur = conn.cursor()
        # Tolerate a DB that has no relations table/view yet (fresh / partial).
        # NB: since the graph_edges migration, `relations` is a VIEW over
        # graph_edges, so we accept either kind.
        if (
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE name='relations' AND type IN ('table','view')"
            ).fetchone()
            is None
        ):
            return {
                "total": 0,
                "unknown_type": 0,
                "self_loops": 0,
                "orphaned": 0,
                "type_mismatch": 0,
                "part_of_without_has_company": 0,
                "has_company_without_part_of": 0,
                "circular": 0,
                "errors": 0,
            }

        def one(sql: str) -> int:
            return cur.execute(sql).fetchone()[0]

        total = one("SELECT COUNT(*) FROM relations")
        # Validate every edge_type in graph_edges (via the unfiltered relations
        # view) against the canonical allowlist. Catches typo'd edge types that
        # would otherwise silently hide from every typed lookup.
        placeholders = ",".join("?" for _ in _KNOWN_EDGE_TYPES)
        unknown_type = cur.execute(
            f"SELECT COUNT(*) FROM relations WHERE relation_type NOT IN ({placeholders})",  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
            _KNOWN_EDGE_TYPES,
        ).fetchone()[0]
        self_loops = one("SELECT COUNT(*) FROM relations WHERE source = target")
        orphaned = one(
            "SELECT COUNT(*) FROM relations r WHERE r.source NOT IN (SELECT name FROM entities)"
            " OR r.target NOT IN (SELECT name FROM entities)"
        )

        po_src_bad = one(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON r.source=e.name "
            "WHERE r.relation_type='part_of' AND e.entity_type!='company'"
        )
        po_tgt_bad = one(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON r.target=e.name "
            "WHERE r.relation_type='part_of' AND e.entity_type!='sector'"
        )
        hc_src_bad = one(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON r.source=e.name "
            "WHERE r.relation_type='has_company' AND e.entity_type!='sector'"
        )
        hc_tgt_bad = one(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON r.target=e.name "
            "WHERE r.relation_type='has_company' AND e.entity_type!='company'"
        )

        po_no_hc = one(
            "SELECT COUNT(*) FROM relations p WHERE p.relation_type='part_of' AND NOT EXISTS "
            "(SELECT 1 FROM relations h WHERE h.source=p.target AND h.target=p.source AND h.relation_type='has_company')"
        )
        hc_no_po = one(
            "SELECT COUNT(*) FROM relations h WHERE h.relation_type='has_company' AND NOT EXISTS "
            "(SELECT 1 FROM relations p WHERE p.source=h.target AND p.target=h.source AND p.relation_type='part_of')"
        )

        circular = one(
            "SELECT COUNT(*) FROM relations r1 WHERE r1.source < r1.target AND EXISTS "
            "(SELECT 1 FROM relations r2 WHERE r2.source=r1.target AND r2.target=r1.source "
            "AND r2.relation_type=r1.relation_type)"
        )

        # Bundle M4: `belongs_to` endpoint-type check. The hierarchy edge
        # connects sector->super_sector and sub_sector->sector. A belongs_to
        # row whose source isn't a sector/sub_sector or whose target isn't a
        # super_sector/sector is a taxonomy error (e.g. a company mistakenly
        # linked to a super-sector). Unlike part_of, belongs_to has NO reverse
        # edge (the hierarchy is queried downward), so there is no
        # belongs_to_without_reverse check.
        bt_src_bad = one(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON r.source=e.name "
            "WHERE r.relation_type='belongs_to' AND e.entity_type NOT IN ('sector','sub_sector')"
        )
        bt_tgt_bad = one(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON r.target=e.name "
            "WHERE r.relation_type='belongs_to' AND e.entity_type NOT IN ('super_sector','sector')"
        )

        # D4: `exposed_to` endpoint-type check. The cross-sector theme edge
        # connects company -> theme. An exposed_to row whose source isn't a
        # company or whose target isn't a theme is a taxonomy error (e.g. a
        # sector mistakenly exposed to a theme). Like belongs_to, exposed_to is
        # directed with no reverse edge (theme membership is queried from the
        # theme outward), so there is no reverse-pair check.
        et_src_bad = one(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON r.source=e.name "
            "WHERE r.relation_type='exposed_to' AND e.entity_type!='company'"
        )
        et_tgt_bad = one(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON r.target=e.name "
            "WHERE r.relation_type='exposed_to' AND e.entity_type!='theme'"
        )

        errors = (
            unknown_type
            + self_loops
            + orphaned
            + po_src_bad
            + po_tgt_bad
            + hc_src_bad
            + hc_tgt_bad
            + po_no_hc
            + hc_no_po
            + circular
            + bt_src_bad
            + bt_tgt_bad
            + et_src_bad
            + et_tgt_bad
        )
        return {
            "total": total,
            "unknown_type": unknown_type,
            "self_loops": self_loops,
            "orphaned": orphaned,
            "type_mismatch": (
                po_src_bad
                + po_tgt_bad
                + hc_src_bad
                + hc_tgt_bad
                + bt_src_bad
                + bt_tgt_bad
                + et_src_bad
                + et_tgt_bad
            ),
            "part_of_without_has_company": po_no_hc,
            "has_company_without_part_of": hc_no_po,
            "belongs_to_endpoint_bad": bt_src_bad + bt_tgt_bad,
            "exposed_to_endpoint_bad": et_src_bad + et_tgt_bad,
            "circular": circular,
            "errors": errors,
        }

    def check_entity_tags(self) -> dict:
        """Integrity of the ``entity_tags`` table (Bundle R1).

        Checks for orphaned tags — rows whose ``entity_name`` doesn't exist
        in ``entities``. Such rows arise if FK enforcement is OFF during a
        rename/delete (the ON DELETE CASCADE that should clean them up never
        fires). ``sync_tags`` rebuilds the whole table each run so drift is
        self-healing, but between syncs this check surfaces the leak.

        All ERROR-level here: the live table has 0 orphans (FK enforcement
        is ON via connect()), so any nonzero value is a real regression.
        """
        conn = self.get_connection()
        cur = conn.cursor()
        # Tolerate a DB that has no entity_tags table yet (fresh / partial).
        if (
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE name='entity_tags' AND type='table'"
            ).fetchone()
            is None
        ):
            return {"total": 0, "orphaned": 0, "errors": 0}

        total = cur.execute("SELECT COUNT(*) FROM entity_tags").fetchone()[0]
        orphaned = cur.execute(
            "SELECT COUNT(*) FROM entity_tags WHERE entity_name NOT IN (SELECT name FROM entities)"
        ).fetchone()[0]
        return {
            "total": total,
            "orphaned": orphaned,
            "errors": orphaned,
        }

    def check_note_tags(self) -> dict:
        """Integrity of the ``note_tags`` table (newsletter_notes_adoption S4).

        ``note_tags`` mirrors the source newsletter notes' YAML tags (no FK
        — editions have no entity rows), so drift = rows whose note is gone
        or whose YAML no longer carries the tag. sync_tags full-rebuilds the
        table each run; between syncs this check surfaces the leak. Tolerates
        a DB without the table (pre-S4 snapshots).
        """
        conn = self.get_connection()
        cur = conn.cursor()
        if (
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE name='note_tags' AND type='table'"
            ).fetchone()
            is None
        ):
            return {"total": 0, "stale": 0, "errors": 0}

        rows = cur.execute("SELECT note_path, tag FROM note_tags").fetchall()
        stale = 0
        for note_path, tag in rows:
            p = self.base_path / note_path
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                stale += 1
                continue
            if tag not in _note_yaml_tags(text):
                stale += 1
        return {
            "total": len(rows),
            "stale": stale,
            "errors": stale,
        }

    def check_events(self) -> dict:
        """Integrity of the ``events`` table (D7 — temporal spine).

        Validates four ERROR-level conditions:
          - unknown event_type (outside CANONICAL_EVENT_TYPES — the controlled
            vocabulary; prevents free-text sprawl, mirroring the edge-type guard)
          - orphaned entity (events.entity with no matching entities.name — a
            real regression; FK CASCADE should prevent this, so any nonzero
            value means FK was OFF during a rename/delete)
          - malformed properties JSON (the CHECK(json_valid) constraint should
            block this at insert, but a pre-constraint DB could hold bad rows)

        date_precision/event_date consistency is advisory only (undated events
        are legitimate — many management changes and some guidance have no
        absolute date) so it is NOT in the error sum. Tolerates a DB that has
        no events table yet (fresh / pre-D7), returning zeros.
        """
        conn = self.get_connection()
        cur = conn.cursor()
        if (
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE name='events' AND type='table'"
            ).fetchone()
            is None
        ):
            return {"total": 0, "unknown_type": 0, "orphaned": 0, "bad_properties": 0, "errors": 0}

        total = cur.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        # Build the type allowlist inline (CANONICAL_EVENT_TYPES is a frozenset
        # of bare strings; quote each for the IN clause).
        type_list = ",".join(f"'{t}'" for t in sorted(CANONICAL_EVENT_TYPES))
        unknown_type = cur.execute(
            f"SELECT COUNT(*) FROM events WHERE event_type NOT IN ({type_list})"  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
        ).fetchone()[0]
        orphaned = cur.execute(
            "SELECT COUNT(*) FROM events WHERE entity NOT IN (SELECT name FROM entities)"
        ).fetchone()[0]
        bad_properties = cur.execute(
            "SELECT COUNT(*) FROM events WHERE json_valid(properties) = 0"
        ).fetchone()[0]
        errors = unknown_type + orphaned + bad_properties
        return {
            "total": total,
            "unknown_type": unknown_type,
            "orphaned": orphaned,
            "bad_properties": bad_properties,
            "errors": errors,
        }

    def check_quotes(self) -> dict:
        """Integrity of the ``quotes`` table (derive_insights capture layer).

        Two ERROR-level conditions (no controlled vocabulary here — speakers
        and paraphrases are free text):
          - orphaned entity (quotes.entity with no matching entities.name;
            FK CASCADE should prevent this, so nonzero = FK was off during a
            rename/delete)
          - malformed properties JSON (the CHECK constraint blocks at insert
            but a pre-constraint DB could hold bad rows)
        Tolerates a DB that has no quotes table yet (fresh / pre-derive-insights).
        """
        conn = self.get_connection()
        cur = conn.cursor()
        if (
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE name='quotes' AND type='table'"
            ).fetchone()
            is None
        ):
            return {"total": 0, "orphaned": 0, "bad_properties": 0, "errors": 0}
        total = cur.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        orphaned = cur.execute(
            "SELECT COUNT(*) FROM quotes WHERE entity NOT IN (SELECT name FROM entities)"
        ).fetchone()[0]
        bad_properties = cur.execute(
            "SELECT COUNT(*) FROM quotes WHERE json_valid(properties) = 0"
        ).fetchone()[0]
        return {
            "total": total,
            "orphaned": orphaned,
            "bad_properties": bad_properties,
            "errors": orphaned + bad_properties,
        }

    def check_company_metrics(self) -> dict:
        """Integrity of the ``company_metrics`` table (derive_insights arm 2).

        Same shape as check_quotes: orphan + bad-properties only (metric_label
        and unit are free-text best-effort fields, not a controlled vocab).
        """
        conn = self.get_connection()
        cur = conn.cursor()
        if (
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE name='company_metrics' AND type='table'"
            ).fetchone()
            is None
        ):
            return {"total": 0, "orphaned": 0, "bad_properties": 0, "errors": 0}
        total = cur.execute("SELECT COUNT(*) FROM company_metrics").fetchone()[0]
        orphaned = cur.execute(
            "SELECT COUNT(*) FROM company_metrics WHERE entity NOT IN (SELECT name FROM entities)"
        ).fetchone()[0]
        bad_properties = cur.execute(
            "SELECT COUNT(*) FROM company_metrics WHERE json_valid(properties) = 0"
        ).fetchone()[0]
        return {
            "total": total,
            "orphaned": orphaned,
            "bad_properties": bad_properties,
            "errors": orphaned + bad_properties,
        }

    def check_orphan_companies(self) -> dict:
        """Companies with no ``part_of`` edge — not attached to any sector
        (Bundle R2).

        Mirrors the ``/api/graph/stats`` hygiene counter (app.py): a company
        ``e`` is orphaned iff no edge has ``edge_type='part_of' AND
        source=e.name``. Queried via the ``relations`` view (which exposes
        ``edge_type AS relation_type`` over ``graph_edges`` in production)
        so this method works uniformly with the production view and with
        test fixtures that materialise ``relations`` as a table — the same
        pattern ``check_relations`` uses. The predicate is identical to the
        API's; only the access path differs.

        0 today; a nonzero value means a company slipped through
        parse_newsletter without a sector assignment, or a ``part_of`` edge
        was deleted between syncs. ERROR-level so the maintenance gate
        catches the regression before it surfaces in the UI.
        """
        conn = self.get_connection()
        cur = conn.cursor()
        # Tolerate a DB that has no relations table/view yet (fresh / partial).
        if (
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE name='relations' AND type IN ('table','view')"
            ).fetchone()
            is None
        ):
            return {"total_companies": 0, "orphan_companies": 0, "errors": 0}
        row = cur.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM entities WHERE entity_type='company') AS total, "
            "(SELECT COUNT(*) FROM entities e WHERE e.entity_type='company' AND NOT EXISTS "
            "(SELECT 1 FROM relations r "
            "WHERE r.relation_type='part_of' AND r.source=e.name)) AS orphans"
        ).fetchone()
        total, orphans = row[0], row[1]
        return {
            "total_companies": total,
            "orphan_companies": orphans,
            "errors": orphans,
        }

    # ------------------------------------------------------------------ #
    # Sector-hierarchy integrity (Bundle M4)                             #
    # ------------------------------------------------------------------ #
    def check_hierarchy(self) -> dict:
        """Completeness + structure of the 3-level sector hierarchy:
        ``super_sector -> sector -> sub_sector`` via the ``belongs_to`` edge.

        This is the graph-structural counterpart to the file-based checks.
        sub_sectors are noteless facets (file_path is NULL by design), so
        their integrity is purely relational — verified here, not in
        verify_notes.py. super_sectors/sectors have notes (checked there),
        but their *hierarchy membership* is checked here.

        All ERROR-level (live data is pristine). Queried via the ``relations``
        view (same pattern as check_relations / check_orphan_companies) so it
        works against the production view AND test fixtures that materialise
        ``relations`` as a table.

        NOTE: endpoint-TYPE errors (a belongs_to whose source isn't a
        sector/sub_sector, or whose target isn't a super_sector/sector) are
        already caught by ``bt_src_bad``/``bt_tgt_bad`` in check_relations —
        do NOT re-add them here (would double-count). This method focuses on
        the gap: completeness (orphans), structure (multi-parent, cycle),
        and taxonomy-drift (DB vs the curated source-of-truth in
        build_sector_hierarchy.SUPER_SECTORS / SUB_CATEGORIES).
        """
        conn = self.get_connection()
        cur = conn.cursor()
        # Tolerate a DB that has no relations table/view yet (fresh / partial).
        if (
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE name='relations' AND type IN ('table','view')"
            ).fetchone()
            is None
        ):
            return {
                "total_belongs_to": 0,
                "sub_sector_orphans": 0,
                "sector_orphans": 0,
                "super_sector_orphans": 0,
                "multi_parent": 0,
                "cycles": 0,
                "taxonomy_drift": 0,
                "errors": 0,
            }

        def one(sql: str) -> int:
            return cur.execute(sql).fetchone()[0]

        total = one("SELECT COUNT(*) FROM relations WHERE relation_type='belongs_to'")

        # Orphans: entities at each level with no hierarchy edge.
        # sub_sector with no outgoing belongs_to (-> sector).
        sub_orphans = one(
            "SELECT COUNT(*) FROM entities e WHERE e.entity_type='sub_sector' "
            "AND NOT EXISTS (SELECT 1 FROM relations r "
            "WHERE r.relation_type='belongs_to' AND r.source=e.name)"
        )
        # sector with no outgoing belongs_to to a super_sector.
        sec_orphans = one(
            "SELECT COUNT(*) FROM entities e WHERE e.entity_type='sector' "
            "AND NOT EXISTS (SELECT 1 FROM relations r "
            "JOIN entities t ON t.name=r.target AND t.entity_type='super_sector' "
            "WHERE r.relation_type='belongs_to' AND r.source=e.name)"
        )
        # super_sector with no incoming belongs_to.
        ss_orphans = one(
            "SELECT COUNT(*) FROM entities e WHERE e.entity_type='super_sector' "
            "AND NOT EXISTS (SELECT 1 FROM relations r "
            "WHERE r.relation_type='belongs_to' AND r.target=e.name)"
        )

        # Multi-parent: a sector/sub_sector linked to >1 distinct parent.
        # The UNIQUE(source,target,edge_type) constraint does NOT prevent a
        # sector having two different super_sector targets — this catches it.
        multi_parent = one(
            "SELECT COUNT(*) FROM ("
            "  SELECT r.source FROM relations r "
            "  WHERE r.relation_type='belongs_to' "
            "  GROUP BY r.source HAVING COUNT(DISTINCT r.target) > 1"
            ")"
        )

        # Cycle: a belongs_to chain that returns to its start (A->B->A).
        # The hierarchy is a strict forest (3 fixed levels), so any cycle is
        # a structural corruption. Recursive CTE bounded by depth as a guard.
        cycles = one(
            "WITH RECURSIVE walk(src, cur, depth) AS ("
            "  SELECT r.source, r.target, 1 FROM relations r "
            "  WHERE r.relation_type='belongs_to'"
            "  UNION ALL"
            "  SELECT w.src, r.target, w.depth+1 FROM walk w "
            "  JOIN relations r ON r.source=w.cur "
            "  WHERE r.relation_type='belongs_to' AND w.depth < 10"
            ")"
            "SELECT COUNT(*) FROM walk WHERE walk.src = walk.cur"
        )

        # Taxonomy drift: the curated source-of-truth
        # (build_sector_hierarchy.SUPER_SECTORS / SUB_CATEGORIES) vs the live
        # belongs_to edges. Flags drift in EITHER direction — a DB edge the
        # taxonomy doesn't list, or a taxonomy mapping absent from the DB.
        taxonomy_drift = self._check_taxonomy_drift(cur)

        errors = sub_orphans + sec_orphans + ss_orphans + multi_parent + cycles + taxonomy_drift
        return {
            "total_belongs_to": total,
            "sub_sector_orphans": sub_orphans,
            "sector_orphans": sec_orphans,
            "super_sector_orphans": ss_orphans,
            "multi_parent": multi_parent,
            "cycles": cycles,
            "taxonomy_drift": taxonomy_drift,
            "errors": errors,
        }

    @staticmethod
    def _check_taxonomy_drift(cur: sqlite3.Cursor) -> int:
        """Count disagreements between the curated taxonomy (the
        SUPER_SECTORS / SUB_CATEGORIES dicts in build_sector_hierarchy.py —
        the single source of truth authored by the curator) and the live
        belongs_to edges in the DB.

        A mapping is a (child, parent) pair. We compare the taxonomy's set
        against the DB's set; the symmetric difference is drift. Names are
        normalized the same way build_sector_hierarchy does it (spaces ->
        underscores) so ``Consumer Discretionary`` matches both forms.

        Returns 0 when taxonomy and DB agree. Tolerates the dicts being
        absent (returns 0) so a partial checkout / refactor doesn't break
        the gate.
        """
        try:
            # Lazy import: avoids a hard dep on the maintenance module at
            # checker-import time (and its transitive helpers.core.db).
            from helpers.maintenance.build_sector_hierarchy import (
                SUPER_SECTORS,
                SUB_CATEGORIES,
            )
        except Exception:
            return 0

        def norm(s: str) -> str:
            return s.replace(" ", "_")

        # Build the expected (child->parent) set from the taxonomy dicts.
        expected: set[tuple[str, str]] = set()
        for ss, members in SUPER_SECTORS.items():
            for m in members:
                expected.add((norm(m), norm(ss)))
        for sector, subs in SUB_CATEGORIES.items():
            for sub in subs:
                expected.add((norm(sub), norm(sector)))

        # Build the live set from the DB (belongs_to edges only).
        live: set[tuple[str, str]] = {
            (r[0], r[1])
            for r in cur.execute(
                "SELECT source, target FROM relations WHERE relation_type='belongs_to'"
            ).fetchall()
        }

        # Symmetric difference = in one set but not the other.
        return len(expected.symmetric_difference(live))

    # ------------------------------------------------------------------ #
    # Normalization integrity                                            #
    # ------------------------------------------------------------------ #
    def check_normalization(self) -> dict:
        """
        normalized_name integrity.

        ERROR (gate-failing; data currently clean): missing, duplicate,
        bad format (PascalCase, no '__', no trailing '_').
        WARNING (advisory; real pre-existing drift): normalized_name does not
        match the file_path basename; orphaned files (on disk, not in DB).
        """
        # Allow leading digit for brand names that legitimately start with a number
        name_ok = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]*$")
        conn = self.get_connection()
        cur = conn.cursor()

        missing = cur.execute(
            "SELECT COUNT(*) FROM entities WHERE normalized_name IS NULL OR normalized_name = ''"
        ).fetchone()[0]

        duplicates = {
            r[0]: r[1]
            for r in cur.execute(
                "SELECT normalized_name, COUNT(*) c FROM entities "
                "GROUP BY normalized_name HAVING c > 1"
            ).fetchall()
        }

        bad_format = []
        file_mismatches = []  # WARNING
        for name, nn, fp, etype in cur.execute(
            "SELECT name, normalized_name, file_path, entity_type FROM entities"
        ).fetchall():
            nn = nn or ""
            # Editions follow the OCR filename convention, not the entity-
            # naming one (5 live stems legitimately break PascalCase — same
            # rationale as the filename exemption in validate_file_path).
            if (
                nn
                and etype != "edition"
                and (not name_ok.match(nn) or "__" in nn or nn.endswith("_"))
            ):
                bad_format.append({"name": name, "normalized_name": nn})
            stem = Path(fp).stem if fp else ""
            if nn and stem and nn != stem:
                file_mismatches.append({"name": name, "normalized_name": nn, "file_stem": stem})

        # Orphaned files: on disk but no entity by normalized_name (WARNING)
        nn_set = {
            r[0]
            for r in self._query(
                "SELECT normalized_name FROM entities WHERE normalized_name IS NOT NULL"
            )
        }
        orphaned_files = []
        for vault in ("findata/Companies", "findata/Sectors"):
            vd = self.base_path / vault
            if not vd.exists():
                continue
            for md in vd.rglob("*.md"):
                if md.stem not in nn_set:
                    orphaned_files.append(str(md.relative_to(self.base_path)))

        return {
            "missing": missing,
            "duplicates": duplicates,
            "bad_format": bad_format,
            "errors": missing + len(duplicates) + len(bad_format),
            "file_mismatches": file_mismatches,
            "orphaned_files": orphaned_files,
            "warnings": len(file_mismatches) + len(orphaned_files),
        }

    def check_duplicate_tickers(self) -> dict:
        """
        Semantic-uniqueness check: companies sharing the same non-null ticker
        are almost always the same underlying entity recorded twice (the
        Groww/Billionbrains, IRCTC, Gujarat Gas class of bug). Treated as an
        ERROR (gate-failing) because file_path/normalized_name sync can be
        perfect while two rows still point at one listed company.
        """
        conn = self.get_connection()
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT ticker, name FROM entities "
            "WHERE entity_type = 'company' AND ticker IS NOT NULL AND ticker <> '' "
            "ORDER BY ticker, name"
        ).fetchall()

        groups: dict[str, list[str]] = {}
        for ticker, name in rows:
            groups.setdefault(ticker, []).append(name)
        duplicates = {t: ns for t, ns in groups.items() if len(ns) > 1}
        return {
            "duplicate_ticker_groups": duplicates,
            "errors": len(duplicates),
        }

    # --- tokenization helpers shared by the fuzzy-name check ---------------
    _STOPWORDS = {
        "the",
        "of",
        "and",
        "ltd",
        "limited",
        "private",
        "pvt",
        "india",
        "industries",
        "company",
        "corporation",
        "enterprise",
        "group",
        "holdings",
    }
    # Generic words that are NOT distinctive on their own. A fuzzy name match
    # must share at least one token NOT in this set, so two unrelated companies
    # sharing only "life insurance" / "power gas" don't false-match.
    _GENERIC_WORDS = {
        "life",
        "insurance",
        "financial",
        "finance",
        "bank",
        "banking",
        "power",
        "gas",
        "oil",
        "energy",
        "capital",
        "markets",
        "global",
        "technologies",
        "solutions",
        "services",
        "trading",
        # Common geographic / group prefixes shared by many DISTINCT Indian
        # companies (Indian Bank vs Indian Overseas Bank; Shree Cement vs
        # Shree Digvijay Cement; Bajaj Finance vs Bajaj Housing Finance).
        "indian",
        "shree",
        "bajaj",
        "national",
        "union",
        "hospitality",
        "housing",
        "pharma",
        "overseas",
        "south",
        "north",
        "east",
        "west",
    }

    # ------------------------------------------------------------------ #
    # Fuzzy-name pairs that have been triaged and confirmed as distinct   #
    # (parent/subsidiary or genuinely separate companies).  Each entry is #
    # a frozenset of the two entity names.  Add new entries here after    #
    # reviewing a fuzzy-similarity report.                                #
    # ------------------------------------------------------------------ #
    _FUZZY_SUPPRESSED: set[frozenset[str]] = {
        frozenset({"Shree Cement", "Shree Digvijay Cement"}),
        frozenset({"PTC Industries", "PTC India"}),
        frozenset({"JTEKT India", "JTEKT"}),
        frozenset({"Sanofi India", "Sanofi"}),
        frozenset({"Hyundai Motor India", "Hyundai Motor Company"}),
        frozenset({"Carraro India", "Carraro Group"}),
        frozenset({"Colgate Palmolive India", "Colgate-Palmolive Company"}),
        frozenset({"3M India", "3M Company"}),
    }

    @staticmethod
    def _meaningful_tokens(name: str | None) -> set[str]:
        return {
            t
            for t in re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
            if t and t not in DatabaseIntegrityChecker._STOPWORDS
        }

    def _fuzzy_names_match(self, tokens_i: set[str], tokens_j: set[str]) -> bool:
        """Return True if two token sets represent a fuzzy-name match."""
        shared = tokens_i & tokens_j
        # Single-token exact match (e.g. 'Hindalco' == 'Hindalco').
        if len(tokens_i) == 1 and len(tokens_j) == 1 and shared:
            return True
        # Subset match with >= 2 shared distinctive tokens.
        return bool(
            len(tokens_i) >= 2
            and len(shared) >= 2
            and (shared == tokens_i or shared == tokens_j)
            and (shared - self._GENERIC_WORDS)
        )

    @staticmethod
    def _share_ticker(ti: str | None, tj: str | None) -> bool:
        """Return True if both tickers are present and equal."""
        return bool(ti and tj and ti == tj)

    def check_fuzzy_duplicate_names(self) -> dict:
        """
        Advisory (WARNING, never gate-failing): pairs of company entities whose
        NAMES suggest they may be the same underlying company recorded twice
        under different renderings — e.g. 'Hindalco' vs 'Hindalco Industries',
        'Apollo Hospitals' vs 'Apollo Hospitals Enterprise'. This catches the
        class of duplication that a strict duplicate-ticker check cannot see
        (same company filed under two different tickers, or one under a ticker
        and one unlisted).

        Not treated as an error: name similarity is genuinely ambiguous and a
        human should confirm before merging. Surfaced for review only.

        Excludes pairs that already share a ticker (those are flagged as errors
        by check_duplicate_tickers and would be double-counted here).

        Resolved pairs (triaged as distinct companies / parent-subsidiary / merged)
        are suppressed via ``_FUZZY_SUPPRESSED`` so they don't re-appear after review.
        """
        conn = self.get_connection()
        rows = conn.execute(
            "SELECT name, ticker FROM entities WHERE entity_type = 'company'"
        ).fetchall()

        pairs = []
        token_cache = [(ni, ti, self._meaningful_tokens(ni)) for (ni, ti) in rows]

        from collections import defaultdict

        token_to_indices: dict[str, set[int]] = defaultdict(set)
        for idx, (_, _, tokens) in enumerate(token_cache):
            for t in tokens:
                token_to_indices[t].add(idx)

        seen_pairs: set[frozenset[str]] = set()
        for i, (ni, ti, ci) in enumerate(token_cache):
            if not ci:
                continue
            candidates: set[int] = set()
            for t in ci:
                candidates.update(token_to_indices[t])
            candidates.discard(i)
            for j in candidates:
                if j <= i:
                    continue
                nj, tj, cj = token_cache[j]
                dominated = (
                    self._share_ticker(ti, tj) or not cj or not self._fuzzy_names_match(ci, cj)
                )
                if dominated:
                    continue
                pair_key = frozenset({ni, nj})
                if pair_key in self._FUZZY_SUPPRESSED or pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                pairs.append({"name_a": ni, "name_b": nj})
        return {
            "fuzzy_duplicate_pairs": pairs,
            "warnings": len(pairs),
            "errors": 0,  # advisory — never gate-failing
        }

    # ------------------------------------------------------------------ #
    # Graph summary (advisory reporting — folded from helpers/graph/stats.py)
    # ------------------------------------------------------------------ #
    def check_graph_summary(self) -> dict:
        """Advisory snapshot of the graph's shape: entity counts by type,
        edge counts by type, sector-size distribution, and market-cap
        distribution. Pure reporting — no errors, never gate-failing.

        This is the structured counterpart to helpers/graph/stats.py's
        human-readable printout. stats.py now renders FROM this dict (one
        source of truth) instead of re-querying SQLite.
        """
        conn = self.get_connection()
        cur = conn.cursor()

        entity_counts = {
            r[0]: r[1]
            for r in cur.execute(
                "SELECT entity_type, COUNT(*) FROM entities GROUP BY entity_type ORDER BY 2 DESC"
            ).fetchall()
        }

        edge_counts = (
            {
                r[0]: r[1]
                for r in cur.execute(
                    "SELECT edge_type, COUNT(*) AS n FROM graph_edges "
                    "GROUP BY edge_type ORDER BY n DESC"
                ).fetchall()
            }
            if cur.execute(
                "SELECT 1 FROM sqlite_master WHERE name='graph_edges' AND type='table'"
            ).fetchone()
            else {}
        )

        # Sector-size distribution (companies per sector_classification).
        sector_sizes = cur.execute(
            "SELECT sector_classification, COUNT(*) AS n FROM entities "
            "WHERE entity_type='company' AND sector_classification IS NOT NULL "
            "GROUP BY sector_classification ORDER BY n DESC"
        ).fetchall()
        sizes = [n for _, n in sector_sizes]
        if sizes:
            size_summary = {
                "sector_count": len(sector_sizes),
                "min": min(sizes),
                "median": sorted(sizes)[len(sizes) // 2],
                "max": max(sizes),
                "mean": round(sum(sizes) / len(sizes), 1),
            }
        else:
            size_summary = {"sector_count": 0, "min": 0, "median": 0, "max": 0, "mean": 0}
        largest = [{"sector": s, "n": n} for s, n in sector_sizes[:10]]
        smallest = [{"sector": s, "n": n} for s, n in sector_sizes[-5:]]

        # Market-cap distribution (from entity_tags — the source of truth
        # since the entities.market_cap column was dropped, Bundle C2).
        cap_counts = (
            cur.execute(
                "SELECT substr(t.tag, length('market_cap/')+1) AS cap, COUNT(*) AS n "
                "FROM entities e "
                "LEFT JOIN entity_tags t ON t.entity_name = e.name "
                "AND t.tag LIKE 'market_cap/%' "
                "WHERE e.entity_type='company' "
                "GROUP BY cap ORDER BY n DESC"
            ).fetchall()
            if cur.execute(
                "SELECT 1 FROM sqlite_master WHERE name='entity_tags' AND type='table'"
            ).fetchone()
            else []
        )
        market_cap = [{"tier": (c or "(unset)"), "n": n} for c, n in cap_counts]

        return {
            "entity_counts": entity_counts,
            "edge_counts": edge_counts,
            "sector_size_summary": size_summary,
            "largest_sectors": largest,
            "smallest_sectors": smallest,
            "market_cap_distribution": market_cap,
            "errors": 0,
            "warnings": 0,
        }

    # ------------------------------------------------------------------ #
    # Market-cap tag conflicts (data quality)
    # ------------------------------------------------------------------ #
    def check_market_cap_conflicts(self) -> dict:
        """Entities carrying MORE THAN ONE ``market_cap/*`` tag.

        This is a genuine data error, not a style issue: the DuckDB
        materialization (``_materialise_vertices`` in helpers/graph/query.py)
        picks ``MIN(tag)`` to avoid vertex fan-out, which silently resolves
        to the alphabetically-FIRST (most-optimistic) tier. So a company
        tagged both ``large_cap`` and ``mid_cap`` shows as ``large_cap`` in
        every consumer (sector_members, the /api/graph/sector endpoint, the
        market-cap distribution). See findata_corpus_audit.txt C2-FIX.

        ERROR-level: the live graph was clean as of the 2026-08-05 dedupe
        (helpers/maintenance/dedupe_market_cap_tags.py), so any nonzero value
        is a regression — a note acquired a second market_cap tag and the
        silent MIN() tiebreak is hiding the wrong value again.
        """
        conn = self.get_connection()
        cur = conn.cursor()
        if (
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE name='entity_tags' AND type='table'"
            ).fetchone()
            is None
        ):
            return {"conflicts": [], "errors": 0}

        conflicts = [
            {"entity": r[0], "tags": r[1].split(",")}
            for r in cur.execute(
                "SELECT entity_name, GROUP_CONCAT(tag, ',') "
                "FROM entity_tags WHERE tag LIKE 'market_cap/%' "
                "GROUP BY entity_name HAVING COUNT(*) > 1 "
                "ORDER BY entity_name"
            ).fetchall()
        ]
        return {"conflicts": conflicts, "errors": len(conflicts)}

    # ------------------------------------------------------------------ #
    # Edge validity-window coverage (advisory)
    # ------------------------------------------------------------------ #
    def check_validity_window(self) -> dict:
        """Per-edge-type coverage of ``valid_from`` / ``valid_to`` on
        ``graph_edges``.

        A missing ``valid_from`` is often LEGITIMATE (dateless prose,
        undated JVs, structural edges like ``part_of`` that have no time
        dimension), so this is WARNING-level — it never fails the gate. Its
        purpose is to surface coverage drift over time: the
        findata_corpus_audit H2 residual (acquired edges missing valid_from)
        quietly drifted from 7 back up to 21 between audits because nothing
        tracked it. This check makes that drift visible each ``make qa``.

        The ``warnings`` count sums missing ``valid_from`` across the
        "should-be-dated" M&A edge types (``acquired``, ``subsidiary_of``) —
        the ones where a date almost always exists somewhere in the source
        and a gap is most likely a salvage opportunity, not a genuine
        dateless event.
        """
        conn = self.get_connection()
        cur = conn.cursor()
        if (
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE name='graph_edges' AND type='table'"
            ).fetchone()
            is None
        ):
            return {"by_type": {}, "warnings": 0, "errors": 0}

        # Edge types where a missing date is most likely salvageable
        # (acquisitions / corporate actions almost always have a date in
        # the source prose, even if the extractor didn't capture it).
        _SHOULD_BE_DATED = ("acquired", "subsidiary_of")

        by_type = {}
        warning_total = 0
        for etype, total, with_from, with_to in cur.execute(
            "SELECT edge_type, COUNT(*), "
            "SUM(CASE WHEN valid_from IS NOT NULL AND valid_from <> '' "
            "         THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN valid_to IS NOT NULL AND valid_to <> '' "
            "         THEN 1 ELSE 0 END) "
            "FROM graph_edges GROUP BY edge_type ORDER BY edge_type"
        ).fetchall():
            missing_from = total - with_from
            by_type[etype] = {
                "total": total,
                "with_valid_from": with_from,
                "missing_valid_from": missing_from,
                "with_valid_to": with_to,
            }
            if etype in _SHOULD_BE_DATED:
                warning_total += missing_from
        return {"by_type": by_type, "warnings": warning_total, "errors": 0}

    # ------------------------------------------------------------------ #
    # Cross-store cache consistency (DuckDB cache vs SQLite source-of-truth)
    # ------------------------------------------------------------------ #
    def check_db_meta(self) -> dict:
        """P0: verify db_meta.generation exists + user_version == expected.

        Errors:
          - missing db_meta table or generation row (needs migration)
          - non-integer generation
          - PRAGMA user_version != EXPECTED_USER_VERSION (7)
          - schema_version in db_meta != helpers.core.db.EXPECTED_SCHEMA_VERSION
        """
        conn = self.get_connection()
        errors = 0
        reasons: list[str] = []
        # db_meta existence
        has_meta = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='db_meta'"
        ).fetchone()
        if not has_meta:
            return {
                "errors": 1,
                "warnings": 0,
                "reasons": ["db_meta table missing (run ensure_db_meta migration)"],
            }
        row = conn.execute("SELECT value FROM db_meta WHERE key='generation'").fetchone()
        if row is None or row[0] is None:
            errors += 1
            reasons.append("db_meta.generation missing")
        else:
            try:
                int(row[0])
            except ValueError, TypeError:
                errors += 1
                reasons.append(f"generation not an int: {row[0]!r}")
        # user_version
        try:
            from helpers.core.db import EXPECTED_USER_VERSION

            uv = conn.execute("PRAGMA user_version").fetchone()[0]
            if int(uv) != EXPECTED_USER_VERSION:
                errors += 1
                reasons.append(f"user_version {uv} != expected {EXPECTED_USER_VERSION}")
        except Exception as e:
            errors += 1
            reasons.append(f"user_version check failed: {e}")
        # schema_version mirror (advisory but treated as error if drift)
        try:
            from helpers.core.db import EXPECTED_SCHEMA_VERSION

            r2 = conn.execute("SELECT value FROM db_meta WHERE key='schema_version'").fetchone()
            if r2 is None or r2[0] != EXPECTED_SCHEMA_VERSION:
                errors += 1
                reasons.append(
                    f"db_meta.schema_version {r2[0] if r2 else None!r} != {EXPECTED_SCHEMA_VERSION!r}"
                )
        except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
            pass
        return {
            "errors": errors,
            "warnings": 0,
            "reasons": reasons,
            "generation": row[0] if row else None,
        }

    def check_cache_consistency(self) -> dict:
        """Reconcile the DuckDB materialized graph cache
        (``memory/graph.duckdb``) against SQLite.

        The DuckDB file is a read-derived cache of SQLite; after any SQLite
        writer (``parse_newsletter --apply``, ``derive-relations``) the cache
        goes stale and must be refreshed via ``make graph-rebuild`` or the
        Refresh button. There is no auto-detection (see doc/design/graph_design.txt
        §18) — THIS check is the programmatic staleness detector, comple-
        menting the UI's Refresh button.

        Checks (only when the cache file is present):
          - schema_version drift: ``_build_meta.schema_version`` in the cache
            vs ``helpers.graph.query._SCHEMA_VERSION`` in the code. A mismatch
            means the materialization SHAPE changed and the cache would serve
            a structurally-wrong graph. ERROR.
          - row-count reconciliation: for each materialized table (v_node,
            each e_* in EDGE_REGISTRY, e_belongs_to, e_exposed_to), compare
            the DuckDB row count to the equivalent SQLite count. A mismatch
            means the cache is stale (a SQLite write happened without a
            rebuild). ERROR.

        DuckDB-optional: if the cache file is absent, or the duckdb extension
        can't be loaded, the check returns ``{skipped: True, ...}`` as a
        WARNING (not an error) — the app rebuilds on demand, and CI may run
        without DuckDB installed. The gate only fails on a PRESENT-but-drifted
        cache. ``duckdb`` is imported lazily here so the checker's SQLite-only
        checks (and tests) don't gain a hard DuckDB dependency.
        """
        duckdb_path = self.base_path / "memory" / "graph.duckdb"
        if not duckdb_path.exists():
            return {"skipped": True, "reason": "cache file absent", "errors": 0, "warnings": 1}

        try:
            import duckdb  # lazy — not a hard dep of the checker
        except ImportError:
            return {"skipped": True, "reason": "duckdb not installed", "errors": 0, "warnings": 1}

        try:
            con = duckdb.connect(str(duckdb_path), read_only=True)
        except Exception as e:
            # Corrupted / unreadable cache — treat as a drift ERROR (the
            # app would also fail to read it). Fresh-rebuild fixes it.
            return {
                "skipped": False,
                "errors": 1,
                "warnings": 0,
                "row_mismatches": [],
                "reason": f"cache unreadable: {e}",
            }

        try:
            return self._reconcile_cache(con)
        finally:
            con.close()

    def _reconcile_cache(self, duck_con) -> dict:  # noqa: C901
        """Compare the open DuckDB connection against SQLite. Factored out
        of check_cache_consistency for testability (a test can pass a fake
        DuckDB connection without touching the filesystem)."""
        from helpers.graph.query import _SCHEMA_VERSION, EDGE_REGISTRY

        warnings = 0
        # --- schema_version drift (ERROR) ---
        try:
            r = duck_con.execute(
                "SELECT value FROM _build_meta WHERE key='schema_version'"
            ).fetchone()
            cache_sv = r[0] if r else None
        except Exception:
            cache_sv = None  # no _build_meta table → cold/invalid cache
        sv_drift = 1 if cache_sv != _SCHEMA_VERSION else 0

        # --- row-count reconciliation (ERROR) ---
        # For each materialized edge table, the DuckDB count must equal the
        # SQLite count for that edge_type. v_node must equal the count of
        # the 5 entity kinds. We attach SQLite read-only to the SAME DuckDB
        # connection so both stores are queryable in one SQL statement.
        try:
            duck_con.execute(f"ATTACH '{self.db_path}' AS fin (TYPE sqlite, READ_ONLY);")
        except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
            # Already attached (e.g. warm cache opened by connect()) — fine.
            pass

        mismatches: list = []

        # v_node: one row per entity of the modeled kinds (the same list as
        # _materialise_vertices in helpers/graph/query.py — currently 6,
        # editions joined via okf_activation P).
        try:
            duck_n = duck_con.execute("SELECT COUNT(*) FROM v_node").fetchone()[0]
        except Exception:
            duck_n = None
        sqlite_n = (
            self.get_connection()
            .execute(
                "SELECT COUNT(*) FROM entities WHERE entity_type IN "
                "('company','sector','super_sector','sub_sector','theme','edition',"
                "'institution')"
            )
            .fetchone()[0]
        )
        if duck_n is None or duck_n != sqlite_n:
            mismatches.append({"table": "v_node", "duckdb": duck_n, "sqlite": sqlite_n})

        # Each EDGE_REGISTRY edge table: DuckDB e_<x> count vs SQLite
        # graph_edges WHERE edge_type='<key>'.
        ge_present = (
            self.get_connection()
            .execute("SELECT 1 FROM sqlite_master WHERE name='graph_edges' AND type='table'")
            .fetchone()
        )
        for etype, spec in EDGE_REGISTRY.items():
            try:
                dn = duck_con.execute(
                    f"SELECT COUNT(*) FROM {spec['table']}"  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                ).fetchone()[0]
            except Exception:
                dn = None
            sn = (
                self.get_connection()
                .execute("SELECT COUNT(*) FROM graph_edges WHERE edge_type=?", (etype,))
                .fetchone()[0]
                if ge_present
                else 0
            )
            if dn is None or dn != sn:
                mismatches.append(
                    {"table": spec["table"], "edge_type": etype, "duckdb": dn, "sqlite": sn}
                )

        # e_belongs_to + e_exposed_to (declared outside EDGE_REGISTRY).
        for tbl, etype in (("e_belongs_to", "belongs_to"), ("e_exposed_to", "exposed_to")):
            try:
                dn = duck_con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
            except Exception:
                dn = None
            sn = (
                self.get_connection()
                .execute("SELECT COUNT(*) FROM graph_edges WHERE edge_type=?", (etype,))
                .fetchone()[0]
                if ge_present
                else 0
            )
            if dn is None or dn != sn:
                mismatches.append({"table": tbl, "edge_type": etype, "duckdb": dn, "sqlite": sn})

        errors = sv_drift + len(mismatches)
        return {
            "skipped": False,
            "schema_version": cache_sv,
            "expected_schema_version": _SCHEMA_VERSION,
            "schema_version_drift": sv_drift,
            "row_mismatches": mismatches,
            "errors": errors,
            "warnings": warnings,
        }

    def _query(self, sql: str):
        conn = self.get_connection()
        return conn.execute(sql).fetchall()

    def check_integrity(self) -> dict:  # noqa: C901
        """
        Perform comprehensive integrity check
        Returns dictionary with detailed results
        """
        print("🔍 Starting Database Integrity Check...")
        print(f"📊 Database: {self.db_path}")
        print(f"📁 Base Path: {self.base_path}")
        print()

        entities = self.get_all_entities()

        results = {
            "timestamp": datetime.now().isoformat(),
            "total_entities": len(entities),
            "valid_entities": 0,
            "invalid_entities": 0,
            "missing_file_paths": 0,
            "file_not_found": 0,
            "invalid_structure": 0,
            "invalid_filename": 0,
            "by_entity_type": {},
            "invalid_entities_list": [],
            "summary": {},
        }

        # Initialize entity type counters
        for entity in entities:
            entity_type = entity["entity_type"]
            if entity_type not in results["by_entity_type"]:
                results["by_entity_type"][entity_type] = {
                    "total": 0,
                    "valid": 0,
                    "invalid": 0,
                    "missing_paths": 0,
                }

        print("📋 Analyzing entities...")
        for i, entity in enumerate(entities, 1):
            if i % 100 == 0:
                print(f"   Processed {i}/{len(entities)} entities...")

            entity_type = entity["entity_type"]
            results["by_entity_type"][entity_type]["total"] += 1

            file_path = entity.get("file_path", "")
            normalized_name = entity.get("normalized_name", "")

            # Bundle M4: sub_sectors are intra-sector facets (e.g. "Iron and
            # Steel", "Airlines") with no dedicated note — file_path is
            # legitimately NULL by design. They are valid without a path, so
            # they don't count against missing_file_paths / validation_rate.
            # companies and sectors are expected to have notes.
            # D4: theme entities are cross-sector nodes (China+1, PLI, ...) that
            # are likewise legitimately fileless — they have no backing note
            # (membership is via the exposed_to edge, not a markdown file). So
            # they share the sub_sector exemption.
            if not file_path and entity_type in ("sub_sector", "theme", "institution"):
                results["valid_entities"] += 1
                results["by_entity_type"][entity_type]["valid"] += 1
                continue

            # Check for missing file path
            if not file_path:
                results["missing_file_paths"] += 1
                results["invalid_entities"] += 1
                results["by_entity_type"][entity_type]["missing_paths"] += 1
                results["by_entity_type"][entity_type]["invalid"] += 1

                results["invalid_entities_list"].append(
                    {
                        "name": entity["name"],
                        "entity_type": entity_type,
                        "issue": "Missing file path",
                        "file_path": file_path,
                        "normalized_name": normalized_name,
                    }
                )
                continue

            # Validate file path (entity_type-aware; Bundle M4)
            is_valid, message = self.validate_file_path(file_path, entity_type)

            if is_valid:
                results["valid_entities"] += 1
                results["by_entity_type"][entity_type]["valid"] += 1
            else:
                results["invalid_entities"] += 1
                results["by_entity_type"][entity_type]["invalid"] += 1

                # Categorize the issue
                if "does not exist" in message:
                    results["file_not_found"] += 1
                    issue_type = "File not found"
                elif "Invalid directory structure" in message:
                    results["invalid_structure"] += 1
                    issue_type = "Invalid directory structure"
                elif "Invalid filename format" in message:
                    results["invalid_filename"] += 1
                    issue_type = "Invalid filename format"
                else:
                    issue_type = "Other"

                results["invalid_entities_list"].append(
                    {
                        "name": entity["name"],
                        "entity_type": entity_type,
                        "issue": issue_type,
                        "details": message,
                        "file_path": file_path,
                        "normalized_name": normalized_name,
                    }
                )

        # Calculate summary metrics
        total = results["total_entities"]
        results["summary"] = {
            "validation_rate": round((results["valid_entities"] / total) * 100, 2)
            if total > 0
            else 0,
            "coverage_rate": round(((total - results["missing_file_paths"]) / total) * 100, 2)
            if total > 0
            else 0,
            "missing_path_rate": round((results["missing_file_paths"] / total) * 100, 2)
            if total > 0
            else 0,
            "file_not_found_rate": round((results["file_not_found"] / total) * 100, 2)
            if total > 0
            else 0,
            "structure_issues_rate": round((results["invalid_structure"] / total) * 100, 2)
            if total > 0
            else 0,
            "filename_issues_rate": round((results["invalid_filename"] / total) * 100, 2)
            if total > 0
            else 0,
        }

        # Run every registered check and merge its result into `results`.
        # The registry (above) is the single source of truth for which
        # checks exist; check_integrity(), the report methods, and main()
        # all iterate _CHECKS so adding a check is one edit, not four.
        for chk in _CHECKS:
            results[chk.name] = getattr(self, chk.method)()

        self.close()  # release the memoized connection
        return results

    def write_report_file(self, results: dict):  # noqa: C901
        """
        Persist the full report (including every advisory warning) to disk so
        warnings can be reviewed in detail after the run. Mirrors verify_notes'
        report-file approach. Path: <base_path>/database_integrity_report.txt.
        """
        out = self.base_path / "database_integrity_report.txt"
        rel = results["relations"]
        norm = results["normalization"]
        lines = []
        lines.append("FinData Knowledge Graph - Database Integrity Report")
        lines.append("=" * 60)
        lines.append(f"Generated: {results['timestamp']}")
        lines.append(f"Database: {self.db_path}")
        lines.append(f"Base Path: {self.base_path}")
        lines.append(
            f"Entities: {results['total_entities']} "
            f"(valid {results['valid_entities']}, invalid {results['invalid_entities']}) "
            f"| validation_rate {results['summary']['validation_rate']}%"
        )
        lines.append("")

        lines.append("## RELATIONS (ERROR-level; gate-failing)")
        lines.append(
            f"total={rel['total']} unknown_type={rel['unknown_type']} "
            f"self_loops={rel['self_loops']} orphaned={rel['orphaned']} "
            f"type_mismatch={rel['type_mismatch']} "
            f"part_of_without_has_company={rel['part_of_without_has_company']} "
            f"has_company_without_part_of={rel['has_company_without_part_of']} "
            f"circular={rel['circular']} -> errors={rel['errors']}"
        )
        lines.append("")

        et = results.get("entity_tags", {})
        lines.append("## ENTITY_TAGS (ERROR-level; gate-failing)")
        lines.append(
            f"total={et.get('total', 0)} orphaned={et.get('orphaned', 0)} "
            f"-> errors={et.get('errors', 0)}"
        )
        lines.append("")

        nt = results.get("note_tags", {})
        lines.append("## NOTE TAGS (ERROR-level; gate-failing)")
        lines.append(
            f"total={nt.get('total', 0)} stale={nt.get('stale', 0)} -> errors={nt.get('errors', 0)}"
        )
        lines.append("")

        ev = results.get("events", {})
        lines.append("## EVENTS (ERROR-level; gate-failing)")
        lines.append(
            f"total={ev.get('total', 0)} unknown_type={ev.get('unknown_type', 0)} "
            f"orphaned={ev.get('orphaned', 0)} bad_properties={ev.get('bad_properties', 0)} "
            f"-> errors={ev.get('errors', 0)}"
        )
        lines.append("")

        qu = results.get("quotes", {})
        lines.append("## QUOTES (ERROR-level; gate-failing)")
        lines.append(
            f"total={qu.get('total', 0)} orphaned={qu.get('orphaned', 0)} "
            f"bad_properties={qu.get('bad_properties', 0)} "
            f"-> errors={qu.get('errors', 0)}"
        )
        lines.append("")

        cm = results.get("company_metrics", {})
        lines.append("## COMPANY METRICS (ERROR-level; gate-failing)")
        lines.append(
            f"total={cm.get('total', 0)} orphaned={cm.get('orphaned', 0)} "
            f"bad_properties={cm.get('bad_properties', 0)} "
            f"-> errors={cm.get('errors', 0)}"
        )
        lines.append("")

        oc = results.get("orphan_companies", {})
        lines.append("## ORPHAN COMPANIES (ERROR-level; gate-failing)")
        lines.append(
            f"total_companies={oc.get('total_companies', 0)} "
            f"orphan_companies={oc.get('orphan_companies', 0)} "
            f"-> errors={oc.get('errors', 0)}"
        )
        lines.append("")

        hie = results.get("hierarchy", {})
        lines.append("## SECTOR HIERARCHY (ERROR-level; gate-failing)")
        lines.append(
            f"total_belongs_to={hie.get('total_belongs_to', 0)} "
            f"sub_sector_orphans={hie.get('sub_sector_orphans', 0)} "
            f"sector_orphans={hie.get('sector_orphans', 0)} "
            f"super_sector_orphans={hie.get('super_sector_orphans', 0)} "
            f"multi_parent={hie.get('multi_parent', 0)} "
            f"cycles={hie.get('cycles', 0)} "
            f"taxonomy_drift={hie.get('taxonomy_drift', 0)} "
            f"-> errors={hie.get('errors', 0)}"
        )
        lines.append("")

        mc = results.get("market_cap_conflicts", {})
        lines.append("## MARKET CAP TAG CONFLICTS (ERROR-level; gate-failing)")
        conflicts = mc.get("conflicts", [])
        if conflicts:
            lines.append(f"  {len(conflicts)} entity(ies) with >1 market_cap/* tag:")
            for c in conflicts:
                lines.append(f"    - {c['entity']}: {', '.join(c['tags'])}")
        else:
            lines.append("  none (0 conflicts)")
        lines.append(f"  -> errors={mc.get('errors', 0)}")
        lines.append("")

        cc = results.get("cache_consistency", {})
        lines.append("## DUCKDB CACHE CONSISTENCY (ERROR-level; gate-failing)")
        if cc.get("skipped"):
            lines.append(f"  SKIPPED ({cc.get('reason', '?')}) — warnings={cc.get('warnings', 0)}")
        else:
            lines.append(
                f"schema_version={cc.get('schema_version', '?')} "
                f"(expected {cc.get('expected_schema_version', '?')}) "
                f"drift={cc.get('schema_version_drift', 0)} "
                f"row_mismatches={len(cc.get('row_mismatches', []))} "
                f"-> errors={cc.get('errors', 0)}"
            )
            for m in cc.get("row_mismatches", []):
                lines.append(f"  - {m['table']}: duckdb={m['duckdb']} sqlite={m['sqlite']}")
        lines.append("")

        dm = results.get("db_meta", {})
        lines.append("## DB META (generation + user_version) (ERROR-level; gate-failing)")
        if dm.get("errors", 0):
            for r in dm.get("reasons", []):
                lines.append(f"  ✗ {r}")
            lines.append(f"  -> errors={dm.get('errors', 0)}")
        else:
            lines.append(f"  generation={dm.get('generation', '?')} user_version=OK -> errors=0")
        lines.append("")

        lines.append("## NORMALIZATION ERRORS (gate-failing)")
        lines.append(
            f"missing={norm['missing']} duplicate_groups={len(norm['duplicates'])} "
            f"bad_format={len(norm['bad_format'])} -> errors={norm['errors']}"
        )
        if norm["duplicates"]:
            lines.append("  duplicate normalized_name:")
            for nn, c in norm["duplicates"].items():
                lines.append(f"    - {nn!r}: {c}")
        if norm["bad_format"]:
            lines.append("  bad format (PascalCase/__/trailing):")
            for b in norm["bad_format"]:
                lines.append(f"    - {b['name']}: {b['normalized_name']!r}")
        lines.append("")

        dup = results.get("duplicate_tickers", {})
        lines.append("## SEMANTIC UNIQUENESS (duplicate tickers) (ERROR-level; gate-failing)")
        dup_groups = dup.get("duplicate_ticker_groups", {})
        lines.append(f"duplicate_ticker_groups={len(dup_groups)} -> errors={dup.get('errors', 0)}")
        for ticker, names in dup_groups.items():
            lines.append(f"  - {ticker}: {', '.join(names)}")
        lines.append("")

        fuzzy = results.get("fuzzy_duplicates", {})
        lines.append("## FUZZY NAME SIMILARITY (advisory — likely-same-company pairs)")
        fuzzy_pairs = fuzzy.get("fuzzy_duplicate_pairs", [])
        lines.append(f"similar_pairs={len(fuzzy_pairs)} -> warnings={fuzzy.get('warnings', 0)}")
        for pair in fuzzy_pairs:
            lines.append(f"  - {pair['name_a']}  ~=  {pair['name_b']}")
        lines.append("")

        lines.append("## WARNINGS (advisory; do NOT fail the gate)")
        mm = norm["file_mismatches"]
        of = norm["orphaned_files"]
        lines.append(f"### normalized_name != filename ({len(mm)})")
        for m in mm:
            lines.append(
                f"  - {m['name']}: normalized_name={m['normalized_name']!r} file={m['file_stem']!r}"
            )
        lines.append(f"### orphaned files on disk, not in DB ({len(of)})")
        for f in of:
            lines.append(f"  - {f}")
        lines.append("")

        vw = results.get("validity_window", {})
        lines.append("## EDGE VALIDITY WINDOW COVERAGE (advisory)")
        for etype, stats in vw.get("by_type", {}).items():
            lines.append(
                f"  {etype:20} total={stats['total']:4} "
                f"valid_from={stats['with_valid_from']:4} "
                f"(missing {stats['missing_valid_from']}) "
                f"valid_to={stats['with_valid_to']}"
            )
        lines.append("")

        gs = results.get("graph_summary", {})
        lines.append("## GRAPH SUMMARY (advisory; shape snapshot)")
        ec = gs.get("entity_counts", {})
        lines.append("  entity counts: " + ", ".join(f"{k}={v}" for k, v in ec.items()))
        xec = gs.get("edge_counts", {})
        lines.append("  edge counts: " + ", ".join(f"{k}={v}" for k, v in xec.items()))
        ss = gs.get("sector_size_summary", {})
        if ss.get("sector_count", 0):
            lines.append(
                f"  sectors: {ss['sector_count']} "
                f"(min={ss['min']} median={ss['median']} "
                f"max={ss['max']} mean={ss['mean']})"
            )
        mc = gs.get("market_cap_distribution", [])
        if mc:
            lines.append("  market cap: " + ", ".join(f"{m['tier']}={m['n']}" for m in mc))
        lines.append("")

        lines.append("## INVALID ENTITIES (file_path issues)")
        for issue in results.get("invalid_entities_list", []):
            lines.append(f"  - {issue['name']} ({issue['entity_type']}): {issue['issue']}")

        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out

    def print_report(self, results: dict):  # noqa: C901
        """Print comprehensive integrity report"""
        print("\n" + "=" * 80)
        print("📊 DATABASE INTEGRITY REPORT")
        print("=" * 80)
        print(f"🕐 Generated: {results['timestamp']}")
        print()

        # Summary
        print("📈 SUMMARY METRICS:")
        print(f"   Total Entities: {results['total_entities']}")
        print(f"   Valid Entities: {results['valid_entities']}")
        print(f"   Invalid Entities: {results['invalid_entities']}")
        print(f"   Validation Rate: {results['summary']['validation_rate']}%")
        print(f"   Coverage Rate: {results['summary']['coverage_rate']}%")
        print(f"   Missing Path Rate: {results['summary']['missing_path_rate']}%")
        print()

        # Issue breakdown
        print("🔍 ISSUE BREAKDOWN:")
        print(f"   Missing File Paths: {results['missing_file_paths']}")
        print(f"   Files Not Found: {results['file_not_found']}")
        print(f"   Invalid Directory Structure: {results['invalid_structure']}")
        print(f"   Invalid Filename Format: {results['invalid_filename']}")
        print()

        # By entity type
        print("📋 BREAKDOWN BY ENTITY TYPE:")
        for entity_type, stats in results["by_entity_type"].items():
            print(f"   {entity_type.title()}:")
            print(f"     Total: {stats['total']}")
            print(f"     Valid: {stats['valid']}")
            print(f"     Invalid: {stats['invalid']}")
            print(f"     Missing Paths: {stats['missing_paths']}")
            if stats["total"] > 0:
                validation_rate = (stats["valid"] / stats["total"]) * 100
                print(f"     Validation Rate: {validation_rate:.2f}%")
            print()

        # Detailed issues (first 20)
        if results["invalid_entities_list"]:
            print("⚠️  DETAILED ISSUES (First 20):")
            for i, issue in enumerate(results["invalid_entities_list"][:20], 1):
                print(f"   {i}. {issue['name']} ({issue['entity_type']})")
                print(f"      Issue: {issue['issue']}")
                if "details" in issue:
                    print(f"      Details: {issue['details']}")
                print(f"      File Path: {issue['file_path']}")
                print()

            if len(results["invalid_entities_list"]) > 20:
                print(f"   ... and {len(results['invalid_entities_list']) - 20} more issues")
                print()

        print("=" * 80)
        print("🔗 RELATIONS INTEGRITY:")
        rel = results.get("relations", {})
        print(f"   Total relations: {rel.get('total', 0)}")
        print(f"   Unknown relation_type: {rel.get('unknown_type', 0)}")
        print(f"   Self-loops (source==target): {rel.get('self_loops', 0)}")
        print(f"   Orphaned (source/target not an entity): {rel.get('orphaned', 0)}")
        print(f"   Type/direction mismatches: {rel.get('type_mismatch', 0)}")
        print(
            f"   part_of WITHOUT matching has_company: {rel.get('part_of_without_has_company', 0)}"
        )
        print(
            f"   has_company WITHOUT matching part_of: {rel.get('has_company_without_part_of', 0)}"
        )
        print(f"   Circular (same-type A<->B): {rel.get('circular', 0)}")
        print(f"   Relation errors: {rel.get('errors', 0)}")
        print()

        print("🏷️  ENTITY_TAGS INTEGRITY:")
        et = results.get("entity_tags", {})
        print(f"   Total tags: {et.get('total', 0)}")
        print(f"   Orphaned (entity_name not in entities): {et.get('orphaned', 0)}")
        print(f"   Tag errors: {et.get('errors', 0)}")
        print()

        print("🏷️  NOTE TAGS INTEGRITY:")
        nt = results.get("note_tags", {})
        print(f"   Total tags: {nt.get('total', 0)}")
        print(f"   Stale (note missing or tag dropped): {nt.get('stale', 0)}")
        print(f"   Tag errors: {nt.get('errors', 0)}")
        print()

        print("📅 EVENTS (D7 — temporal spine):")
        ev = results.get("events", {})
        print(f"   Total events: {ev.get('total', 0)}")
        print(f"   Unknown event_type: {ev.get('unknown_type', 0)}")
        print(f"   Orphaned (entity not in entities): {ev.get('orphaned', 0)}")
        print(f"   Bad properties JSON: {ev.get('bad_properties', 0)}")
        print(f"   Event errors: {ev.get('errors', 0)}")
        print()

        print("💬 QUOTES INTEGRITY:")
        qu = results.get("quotes", {})
        print(f"   Total quotes: {qu.get('total', 0)}")
        print(f"   Orphaned (entity not in entities): {qu.get('orphaned', 0)}")
        print(f"   Bad properties JSON: {qu.get('bad_properties', 0)}")
        print(f"   Quote errors: {qu.get('errors', 0)}")
        print()

        print("📊 COMPANY METRICS INTEGRITY:")
        cm = results.get("company_metrics", {})
        print(f"   Total metrics: {cm.get('total', 0)}")
        print(f"   Orphaned (entity not in entities): {cm.get('orphaned', 0)}")
        print(f"   Bad properties JSON: {cm.get('bad_properties', 0)}")
        print(f"   Metric errors: {cm.get('errors', 0)}")
        print()

        print("🏢 ORPHAN COMPANIES:")
        oc = results.get("orphan_companies", {})
        print(f"   Total companies: {oc.get('total_companies', 0)}")
        print(f"   Orphaned (no part_of edge to a sector): {oc.get('orphan_companies', 0)}")
        print(f"   Orphan errors: {oc.get('errors', 0)}")
        print()

        print("🗂️  SECTOR HIERARCHY:")
        hie = results.get("hierarchy", {})
        print(f"   Total belongs_to edges: {hie.get('total_belongs_to', 0)}")
        print(f"   sub_sector orphans (no parent sector): {hie.get('sub_sector_orphans', 0)}")
        print(f"   sector orphans (no parent super_sector): {hie.get('sector_orphans', 0)}")
        print(f"   super_sector orphans (no children): {hie.get('super_sector_orphans', 0)}")
        print(f"   Multi-parent (child with >1 parent): {hie.get('multi_parent', 0)}")
        print(f"   Cycles: {hie.get('cycles', 0)}")
        print(f"   Taxonomy drift (DB vs source-of-truth): {hie.get('taxonomy_drift', 0)}")
        print(f"   Hierarchy errors: {hie.get('errors', 0)}")
        print()

        print("💰 MARKET CAP TAG CONFLICTS:")
        mc = results.get("market_cap_conflicts", {})
        conflicts = mc.get("conflicts", [])
        if conflicts:
            print(f"   {len(conflicts)} entity(ies) with >1 market_cap/* tag:")
            for c in conflicts[:10]:
                print(f"     {c['entity']}: {', '.join(c['tags'])}")
            if len(conflicts) > 10:
                print(f"     ... and {len(conflicts) - 10} more (see report file)")
        else:
            print("   none (0 conflicts)")
        print(f"   Conflicts: {mc.get('errors', 0)}")
        print()

        print("🦆 DUCKDB CACHE CONSISTENCY:")
        cc = results.get("cache_consistency", {})
        if cc.get("skipped"):
            print(f"   SKIPPED — {cc.get('reason', '?')} (advisory)")
        else:
            print(
                f"   schema_version: {cc.get('schema_version', '?')} "
                f"(expected {cc.get('expected_schema_version', '?')})"
            )
            print(f"   schema_version drift: {cc.get('schema_version_drift', 0)}")
            rm = cc.get("row_mismatches", [])
            print(f"   row-count mismatches: {len(rm)}")
            for m in rm:
                print(f"     - {m['table']}: duckdb={m['duckdb']} sqlite={m['sqlite']}")
            print(f"   Cache errors: {cc.get('errors', 0)}")
        print()

        print("🗄️  DB META (generation + user_version):")
        dm = results.get("db_meta", {})
        if dm.get("errors", 0):
            for r in dm.get("reasons", []):
                print(f"   ✗ {r}")
            print(f"   DB meta errors: {dm.get('errors', 0)}")
        else:
            print(f"   generation: {dm.get('generation', '?')}")
            print("   user_version: OK")
            print("   DB meta errors: 0")
        print()

        print("🔤 NORMALIZATION INTEGRITY:")
        norm = results.get("normalization", {})
        print(f"   Missing normalized_name: {norm.get('missing', 0)}")
        print(f"   Duplicate normalized_name groups: {len(norm.get('duplicates', {}))}")
        print(f"   Bad format (PascalCase/__/trailing): {len(norm.get('bad_format', []))}")
        print(f"   Normalization errors: {norm.get('errors', 0)}")
        print(
            f"   ⚠ normalized_name != filename: {len(norm.get('file_mismatches', []))} (advisory)"
        )
        print(
            f"   ⚠ Orphaned files (on disk, not in DB): {len(norm.get('orphaned_files', []))} (advisory)"
        )
        print()

        print("🔢 SEMANTIC UNIQUENESS (duplicate tickers):")
        dup = results.get("duplicate_tickers", {})
        dup_groups = dup.get("duplicate_ticker_groups", {})
        print(f"   Duplicate ticker groups: {len(dup_groups)}")
        for ticker, names in dup_groups.items():
            print(f"     {ticker}: {', '.join(names)}")
        print(f"   Uniqueness errors: {dup.get('errors', 0)}")
        print()

        print("🔍 FUZZY NAME SIMILARITY (advisory — likely-same-company pairs):")
        fuzzy = results.get("fuzzy_duplicates", {})
        fuzzy_pairs = fuzzy.get("fuzzy_duplicate_pairs", [])
        print(f"   Similar name pairs: {len(fuzzy_pairs)} (advisory)")
        for p in fuzzy_pairs[:30]:
            print(f"     {p['name_a']}  ~=  {p['name_b']}")
        if len(fuzzy_pairs) > 30:
            print(f"     ... and {len(fuzzy_pairs) - 30} more (see report file)")
        print()

        print("📅 EDGE VALIDITY WINDOW COVERAGE (advisory):")
        vw = results.get("validity_window", {})
        for etype, stats in vw.get("by_type", {}).items():
            print(
                f"   {etype:20} total={stats['total']:4} "
                f"valid_from={stats['with_valid_from']:4} "
                f"(missing {stats['missing_valid_from']})"
            )
        print()

        print("📊 GRAPH SUMMARY (advisory; shape snapshot):")
        gs = results.get("graph_summary", {})
        ec = gs.get("entity_counts", {})
        print("   entities: " + ", ".join(f"{k}={v}" for k, v in ec.items()))
        xec = gs.get("edge_counts", {})
        print("   edges: " + ", ".join(f"{k}={v}" for k, v in xec.items()))
        ss = gs.get("sector_size_summary", {})
        if ss.get("sector_count", 0):
            print(
                f"   sectors: {ss['sector_count']} "
                f"(min={ss['min']} median={ss['median']} "
                f"max={ss['max']} mean={ss['mean']})"
            )
        mc = gs.get("market_cap_distribution", [])
        if mc:
            print("   market cap: " + ", ".join(f"{m['tier']}={m['n']}" for m in mc))
        print()

        print("=" * 80)
        print("🎯 RECOMMENDATIONS:")
        if results["missing_file_paths"] > 0:
            print(f"   • Add missing file paths for {results['missing_file_paths']} entities")
        if results["file_not_found"] > 0:
            print(
                f"   • Create missing files or correct paths for {results['file_not_found']} entities"
            )
        if results["invalid_structure"] > 0:
            print(f"   • Fix directory structure for {results['invalid_structure']} entities")
        if results["invalid_filename"] > 0:
            print(
                f"   • Rename files with invalid format for {results['invalid_filename']} entities"
            )
        dup_groups = results.get("duplicate_tickers", {}).get("duplicate_ticker_groups", {})
        if dup_groups:
            print(
                f"   • Resolve {len(dup_groups)} duplicate-ticker group(s) — same listed entity recorded under multiple names"
            )
        fuzzy_pairs = results.get("fuzzy_duplicates", {}).get("fuzzy_duplicate_pairs", [])
        if fuzzy_pairs:
            print(
                f"   • Review {len(fuzzy_pairs)} fuzzy name-similarity pair(s) above — confirm whether they are the same company (advisory)"
            )

        if results["summary"]["validation_rate"] < 95:
            print(
                f"   ⚠️  Current validation rate ({results['summary']['validation_rate']}%) is below target (95%)"
            )
        else:
            print(f"   ✅ Validation rate ({results['summary']['validation_rate']}%) meets target!")

        print("=" * 80)


def main(argv: list[str] | None = None):
    """Main function to run database integrity check.

    Test seam: flag-less tool — argv accepted and ignored."""
    try:
        checker = DatabaseIntegrityChecker()
        results = checker.check_integrity()
        checker.print_report(results)
        report_path = checker.write_report_file(results)
        print(f"\n📝 Full report written to: {report_path}")

        # Exit code is data-driven by the registry: any ERROR-severity
        # check whose result dict reports a nonzero `errors` fails the
        # gate. WARNING-severity checks never fail it. The file_path
        # coverage target (< 95%) is a separate structural gate that
        # predates the registry and stays explicit.
        if results["summary"]["validation_rate"] < 95:
            return 1  # Entity file_path coverage below target
        for chk in _CHECKS:
            if chk.severity != "error":
                continue
            if results.get(chk.name, {}).get("errors", 0):
                return 1  # a registered ERROR check found regressions
        return 0  # Success

    except Exception as e:
        print(f"❌ Error during integrity check: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
