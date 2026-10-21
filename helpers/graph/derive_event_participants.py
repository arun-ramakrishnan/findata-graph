#!/usr/bin/env python3
"""Derive the ``event_participants`` n-ary store (person_ontology_spine S4).

``events`` rows carry participation only as two columns — ``entity`` and
``counterparty_entity`` — so the event hyperedges flatten to dyads
(measured 2026-10-19: 1,252 event hyperedges, k=1 → 106, k=2 → 1,146,
**zero k≥3**) and the D10 density lane has nothing to chew on. This lane
lifts participation into its own table:

    event_participants (
        event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
        participant TEXT    NOT NULL REFERENCES entities(name) ON DELETE CASCADE,
        role        TEXT    NOT NULL,
        source_ref  TEXT    NOT NULL,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (event_id, participant, role)
    )

— the #234 n-ary non-goal, unblocked. The PK makes inserts idempotent
per (event, participant, role); the writer OWNS the table and replaces
each event's rows wholesale, so role re-mappings converge.

Role vocabulary (``EVENT_ROLE_BY_TYPE``) covers all four canonical event
types, entity-first: ``acquisition`` → (acquirer, target), ``jv`` →
(partner, partner), ``guidance`` → (issuer, —), ``management_change`` →
(subject, —). ``derive_hyperedges`` imports the same map, so the
incidence role-tagging and this store can never disagree.

What this lane deliberately does NOT do: invent a speaker→event join.
The obvious key is dead — ``events.as_of_edition`` holds free-text
section titles ("The Chatter #69") while ``quotes.as_of_edition`` holds
edition stems, 0/1,477 events join, and a source_quote substring rescue
matches only 14/461 (3%) — not a mechanism. Until a real join key
lands, the store holds exactly the participation ``events`` can prove,
and event hyperedges stay dyadic. ``helpers/graph/derive_hyperedges``
reads THIS store for the event family, so any future writer (an
announcements lane, triage accept) that adds a participant row grows
the hyperedge — one write path, no flattening step to keep in sync.

Dry-run is the default; ``--apply`` writes inside one transaction.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import DEFAULT_DB_PATH, connect  # noqa: E402

SOURCE_REF = "derive:event_participants"
AGENT_ID = "derive_event_participants"
SOURCE_TIER = "derive"

#: The canonical per-type participant roles, ``(entity_role,
#: counterparty_role)``. Counterparty role ``None`` = the event type has
#: no counterparty semantics; the store then carries the entity row only.
#: Single source of truth — ``derive_hyperedges`` imports this for its
#: incidence role-tagging.
EVENT_ROLE_BY_TYPE: dict[str, tuple[str | None, str | None]] = {
    "acquisition": ("acquirer", "target"),
    "jv": ("partner", "partner"),
    "guidance": ("issuer", None),
    "management_change": ("subject", None),
}

EVENT_PARTICIPANTS_DDL = """
CREATE TABLE IF NOT EXISTS event_participants (
    event_id    INTEGER NOT NULL
                  REFERENCES events(id) ON DELETE CASCADE ON UPDATE CASCADE,
    participant TEXT    NOT NULL
                  REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
    role        TEXT    NOT NULL,
    source_ref  TEXT    NOT NULL,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id, participant, role)
)
"""

EVENT_PARTICIPANTS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_event_participants_participant "
    "ON event_participants(participant)",
)


def ensure_schema(conn) -> None:
    """Create the table + index when missing; a no-op otherwise.

    The events table must exist first (this store anchors on it) — a
    fresh DB without ``events`` raises, so callers derive after the
    events migration like every other events-anchored lane.
    """
    conn.execute(EVENT_PARTICIPANTS_DDL)
    for idx in EVENT_PARTICIPANTS_INDEXES:
        conn.execute(idx)
    conn.commit()


def collect(conn) -> dict[int, list[tuple[str, str]]]:
    """``{event_id: [(participant, role), ...]}`` over all event types.

    Events whose ``event_type`` is outside ``EVENT_ROLE_BY_TYPE`` are
    reported by the caller (worklist posture) and carry no rows — an
    unknown type never confabulates roles.
    """
    out: dict[int, list[tuple[str, str]]] = {}
    for eid, etype, ent, cp in conn.execute(
        "SELECT id, event_type, entity, counterparty_entity FROM events ORDER BY id"
    ):
        ent_role, cp_role = EVENT_ROLE_BY_TYPE.get(etype, (None, None))
        rows: list[tuple[str, str]] = []
        if ent_role:
            rows.append((ent, ent_role))
        if cp_role and cp:
            rows.append((cp, cp_role))
        if rows:
            out[int(eid)] = rows
    return out


def apply_participants(
    conn, participants: dict[int, list[tuple[str, str]]], *, dry_run: bool = True
) -> dict:
    """Replace the store's rows with ``participants``; return the stats."""
    existing = dict(
        conn.execute(
            "SELECT event_id, COUNT(*) FROM event_participants GROUP BY event_id"
        ).fetchall()
    )
    new_events = sum(1 for eid in participants if eid not in existing)
    new_rows = sum(len(rows) for eid, rows in participants.items() if eid not in existing)
    # Content-aware: a count-only compare misses same-length role
    # re-mappings, and the report exists precisely to name those.
    changed_events = 0
    for eid, rows in participants.items():
        if eid not in existing:
            continue
        stored = sorted(
            conn.execute(
                "SELECT participant, role FROM event_participants WHERE event_id = ?",
                (eid,),
            ).fetchall()
        )
        if sorted(rows) != stored:
            changed_events += 1
    dropped_events = sorted(set(existing) - set(participants))
    stats = {
        "events": len(participants),
        "rows": sum(len(r) for r in participants.values()),
        "new_events": new_events,
        "new_rows": new_rows,
        "changed_events": changed_events,
        "dropped_events": dropped_events,
    }
    if dry_run:
        return stats
    with conn:
        for eid in dropped_events:
            conn.execute("DELETE FROM event_participants WHERE event_id = ?", (eid,))
        for eid, rows in participants.items():
            conn.execute("DELETE FROM event_participants WHERE event_id = ?", (eid,))
            conn.executemany(
                "INSERT INTO event_participants (event_id, participant, role, source_ref) "
                "VALUES (?, ?, ?, ?)",
                [(eid, participant, role, SOURCE_REF) for participant, role in rows],
            )
    return stats


def derive(db_path: str | Path = DEFAULT_DB_PATH, *, apply: bool = False) -> dict:
    """Census + apply in one call; the printed report is the sync artifact."""
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        participants = collect(conn)
        stats = apply_participants(conn, participants, dry_run=not apply)
        roles: dict[str, int] = {}
        for rows in participants.values():
            for _participant, role in rows:
                roles[role] = roles.get(role, 0) + 1
        print(
            f"event_participants: events={stats['events']} rows={stats['rows']} "
            f"roles={dict(sorted(roles.items()))}"
        )
        print(
            f"  {'[dry-run]' if not apply else '[apply]'} "
            f"new_events={stats['new_events']} new_rows={stats['new_rows']} "
            f"changed_events={stats['changed_events']} "
            f"dropped_events={len(stats['dropped_events'])}"
        )
        return stats
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="research.db path")
    parser.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    args = parser.parse_args(argv)
    derive(args.db, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
