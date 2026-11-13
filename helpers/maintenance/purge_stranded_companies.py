#!/usr/bin/env python3
"""Purge stranded-orphan companies: entities homed ONLY by stale group
incidences (proposal group_membership_compaction S3).

After S1 compacted `vigil:rpt:*`/`$.group` group membership, any company
that (a) has no sector membership (the `check_orphan_companies` predicate),
and (b) is referenced by nothing else — no graph_edges, no
hyper_incidences, no metrics/quotes, no events, no relations — is fully
unreferenced and is removed. Event-referenced names (counterparty_entity
rows) are KEPT and reported: their events are re-derived, not the names
purged. Default is plan-only (--check); --apply writes.

Idempotent. FKs do the cleanup: entities.name is ON DELETE CASCADE from
graph_edges source/target, hyper_incidences.entity_name, company_metrics,
quotes, events.entity; events.counterparty_entity is SET NULL — which is
exactly why event-referenced names are never auto-purged.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402


_ORPHAN_PREDICATE = """
    e.entity_type = 'company'
    AND NOT EXISTS (
        SELECT 1 FROM hyper_incidences hi
        JOIN hyper_edges he ON he.id = hi.edge_id
        WHERE he.edge_type = 'sector' AND hi.entity_name = e.name
    )
    AND NOT (e.file_path IS NULL AND e.ticker IS NOT NULL AND e.ticker <> '')
    AND NOT (
        e.file_path IS NULL AND e.ticker IS NULL AND EXISTS (
            SELECT 1 FROM relations r WHERE r.source = e.name OR r.target = e.name
        )
    )
"""

_OTHER_REFS = """
    (SELECT COUNT(*) FROM graph_edges WHERE source = e.name OR target = e.name)
    + (SELECT COUNT(*) FROM hyper_incidences WHERE entity_name = e.name)
    + (SELECT COUNT(*) FROM company_metrics WHERE entity = e.name)
    + (SELECT COUNT(*) FROM quotes WHERE entity = e.name)
    + (SELECT COUNT(*) FROM events WHERE entity = e.name)
    + (SELECT COUNT(*) FROM relations WHERE source = e.name OR target = e.name)
"""

_EVENT_REFS = """
    SELECT COUNT(*) FROM events e2 WHERE e2.counterparty_entity = e.name
"""

# Static query strings composed once at module scope (bandit S608-clean:
# no f-strings/concatenation inside execute() calls).
_PURGEABLE_SQL = (
    "SELECT e.name, (" + _EVENT_REFS.strip() + ") AS evt FROM entities e "  # noqa: S608  # module-level constants spliced; no user input
    "WHERE " + _ORPHAN_PREDICATE.strip() + " AND (" + _OTHER_REFS.strip() + ") = 0"
    " AND evt = 0"
)
_KEPT_SQL = (
    "SELECT e.name, (" + _EVENT_REFS.strip() + ") AS evt FROM entities e "  # noqa: S608  # module-level constants spliced; no user input
    "WHERE " + _ORPHAN_PREDICATE.strip() + " AND (" + _OTHER_REFS.strip() + ") = 0"
    " AND evt > 0"
)


def collect_stranded(con) -> tuple[list[tuple[str, int, int]], list[tuple[str, int, int]]]:
    """Return (purgeable, kept_event_referenced) lists of (name, other_refs, event_refs)."""
    purgeable_rows = con.execute(_PURGEABLE_SQL).fetchall()
    kept_rows = con.execute(_KEPT_SQL).fetchall()
    purgeable = [(name, 0, evt) for (name, evt) in purgeable_rows]
    kept = [(name, 0, evt) for (name, evt) in kept_rows]
    return purgeable, kept


def purge(write: bool = False, con=None) -> int:
    if con is None:
        con = connect()  # FK-enabled via helpers.core.db.connect
    purgeable, kept = collect_stranded(con)
    if write and purgeable:
        with con:
            for name, _, _ in purgeable:
                con.execute("DELETE FROM entities WHERE name = ?", (name,))
    verb = "Applied" if write else "Plan"
    print(f"{verb}: {len(purgeable)} stranded-orphan company(ies) purged")
    if kept:
        print(f"Kept (event-referenced, {len(kept)}):")
        for name, _, evt in sorted(kept):
            print(f"  {name} ({evt} event ref(s))")
    if con is None:
        con.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="Plan-only (default).")
    g.add_argument("--apply", action="store_true", help="Purge (writes).")
    args = p.parse_args(argv)
    return purge(write=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
