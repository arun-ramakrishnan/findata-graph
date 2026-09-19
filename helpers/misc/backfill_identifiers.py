#!/usr/bin/env python3
"""Identifier registry + CIN facet converger (S3).

S3 of the ontology_convention_stack proposal (2026-09-14): identifiers
as table conventions. Two surfaces:

- ``entities.cin`` + five facets (``cin_listing``/``cin_nic5``/
  ``cin_state``/``cin_year``/``cin_ownership``) — the PRIMARY live-CIN
  home, parsed at ingest (helpers/core/cin.py);
- ``entity_identifiers`` — the registry for everything else (lei, cik,
  isin, llpin, alias; 'cin' exists in the CHECK only for superseded/
  historic CINs written directly, the live one lives on entities).

This script is the maint-full PRE_FULL ``identifiers`` step: a
deterministic projection of already-stamped state (re-parse
``entities.cin`` → refresh facets), so it is idempotent and a no-op
between ingests — PRE_FULL-legal per the maint invariant. It owns its
DDL via the self-ensure pattern (guarded ALTERs + CREATE IF NOT EXISTS;
db.py owns only the version constants) and is the operator write
surface:

    python3 helpers/misc/backfill_identifiers.py              # dry-run report
    python3 helpers/misc/backfill_identifiers.py --apply      # ensure schema + converge
    python3 helpers/misc/backfill_identifiers.py --apply \
        --set-cin "Tata Steel=L01631KA2010PTC096843"
    python3 helpers/misc/backfill_identifiers.py --apply \
        --set-id "Tata Steel:lei=XXXXXXXXXXXXXXXXXXXX" --source-ref manual

--set-cin/--set-id validate hard (parse/format/entity-existence/
cross-entity ambiguity) and write NOTHING without --apply; validation
failures block the whole batch, never partially apply.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # noqa: E402

from helpers.core.cin import LLPIN_RE, parse_cin  # noqa: E402
from helpers.core.db import DEFAULT_DB_PATH, connect, utc_now  # noqa: E402
from helpers.core.vocab import IDENTIFIER_TYPE_VALUES, sql_in  # noqa: E402

# entities.<col> additions (memo §6.6 landing zone). Facets are a
# projection of cin — converge() rewrites them, never the reverse.
_ENTITIES_COLUMNS: tuple[tuple[str, str], ...] = (
    ("cin", "CHAR(21)"),
    ("cin_listing", "CHAR(1)"),
    ("cin_nic5", "CHAR(5)"),
    ("cin_state", "CHAR(2)"),
    ("cin_year", "SMALLINT"),
    ("cin_ownership", "CHAR(3)"),
)

_REGISTRY_DDL = f"""
    CREATE TABLE IF NOT EXISTS entity_identifiers (
        entity_name      TEXT NOT NULL REFERENCES entities(name)
                           ON DELETE CASCADE ON UPDATE CASCADE,
        identifier_type  TEXT NOT NULL CHECK (identifier_type IN
                           ({sql_in(IDENTIFIER_TYPE_VALUES)})),
        identifier_value TEXT NOT NULL,
        namespace        TEXT,
        valid_from       TEXT,
        valid_to         TEXT,
        source_ref       TEXT NOT NULL,
        created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_updated     DATETIME,
        PRIMARY KEY (entity_name, identifier_type, identifier_value),
        UNIQUE (identifier_type, identifier_value)
    )
"""

_REGISTRY_TYPES: frozenset[str] = frozenset({"cin", "lei", "cik", "isin", "llpin", "alias"})

# Light format gates for registry writes ('alias' is free-form; 'cin'
# history rows are direct-SQL only, never --set-id).
_ID_VALUE_RES: dict[str, re.Pattern[str]] = {
    "lei": re.compile(r"^[A-Z0-9]{20}$"),
    "cik": re.compile(r"^\d{1,10}$"),
    "isin": re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$"),
    "llpin": LLPIN_RE,
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Add the entities.cin facet block + registry where missing (idempotent).

    Never-blocking: a DB without an ``entities`` table has nothing to
    anchor the FKs — skip both blocks and let the report say so.
    """
    cols = _entities_columns(conn)
    if not cols:
        return
    for name, decl in _ENTITIES_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE entities ADD COLUMN {name} {decl}")  # noqa: S608 -- fixed identifier names, never user input
    conn.execute(_REGISTRY_DDL)
    conn.commit()


def _entities_columns(conn: sqlite3.Connection) -> set[str]:
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='entities' AND type='table'"
        ).fetchone()
        is None
    ):
        return set()
    return {row[1] for row in conn.execute("PRAGMA table_info(entities)")}


def _schema_ensured(conn: sqlite3.Connection) -> tuple[bool, list[str]]:
    cols = _entities_columns(conn)
    missing = [name for name, _ in _ENTITIES_COLUMNS if name not in cols]
    has_registry = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='entity_identifiers' AND type='table'"
        ).fetchone()
        is not None
    )
    return (not missing and has_registry), missing


def converge(conn: sqlite3.Connection, *, apply: bool) -> dict[str, int]:
    """Re-parse every ``entities.cin`` and refresh drifted facets.

    Deterministic projection of the stamped ``cin`` values — the facets
    are never authoritative. Returns counts; writes only with ``apply``.
    """
    stats = {"cin_entities": 0, "facets_stale": 0, "malformed_cin": 0}
    rows = conn.execute(
        "SELECT name, cin, cin_listing, cin_nic5, cin_state, cin_year, cin_ownership "
        "FROM entities WHERE cin IS NOT NULL AND TRIM(cin) <> ''"
    ).fetchall()
    for row in rows:
        name, cin = row[0], row[1]
        stats["cin_entities"] += 1
        p = parse_cin(cin)
        if not p.ok:
            stats["malformed_cin"] += 1
            continue
        want = (p.listing, p.nic5, p.state, p.year, p.ownership)
        if tuple(row[2:7]) != want:
            stats["facets_stale"] += 1
            if apply:
                conn.execute(
                    "UPDATE entities SET cin_listing=?, cin_nic5=?, cin_state=?, "
                    "cin_year=?, cin_ownership=? WHERE name=?",
                    (*want, name),
                )
    if apply:
        conn.commit()
    return stats


def _canonical_entity(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute(
        "SELECT name FROM entities WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return row[0] if row else None


def plan_set_ops(  # noqa: C901  # one validation gate per failure class (shape → type → format → entity → ownership); splitting the ladder would scatter the batch-blocking contract
    conn: sqlite3.Connection,
    set_cins: list[str] | None,
    set_ids: list[str] | None,
) -> tuple[list[tuple], list[str]]:
    """Validate --set-cin/--set-id operands; returns (ops, failures).

    ops items: ``("cin", canonical, CinParse)`` or
    ``("id", canonical, id_type, value)``. No writes here — the caller
    applies the batch only when ``failures`` is empty.
    """
    ops: list[tuple] = []
    failures: list[str] = []

    for spec in set_cins or []:
        name, _, value = spec.partition("=")
        name, value = name.strip(), value.strip()
        canonical = _canonical_entity(conn, name)
        if canonical is None:
            failures.append(f"--set-cin {spec!r}: entity {name!r} not found")
            continue
        p = parse_cin(value)
        if not p.ok:
            failures.append(f"--set-cin {spec!r}: {p.message}")
            continue
        ops.append(("cin", canonical, p))

    for spec in set_ids or []:
        lhs, eq, value = spec.rpartition("=")
        if not eq:
            failures.append(f"--set-id {spec!r}: expected NAME:TYPE=VALUE")
            continue
        name, sep, id_type = lhs.partition(":")
        name, id_type, value = name.strip(), id_type.strip().lower(), value.strip()
        if not sep or not name or not value:
            failures.append(f"--set-id {spec!r}: expected NAME:TYPE=VALUE")
            continue
        if id_type == "cin":
            failures.append(
                f"--set-id {spec!r}: the live CIN belongs on entities.cin — "
                "use --set-cin (historic CINs: direct SQL with source_ref)"
            )
            continue
        if id_type not in _REGISTRY_TYPES:
            failures.append(f"--set-id {spec!r}: type {id_type!r} not in {sorted(_REGISTRY_TYPES)}")
            continue
        pattern = _ID_VALUE_RES.get(id_type)
        if pattern is not None and not pattern.match(value.upper()):
            failures.append(f"--set-id {spec!r}: value fails {id_type} format gate")
            continue
        value = value.upper()
        canonical = _canonical_entity(conn, name)
        if canonical is None:
            failures.append(f"--set-id {spec!r}: entity {name!r} not found")
            continue
        owner = conn.execute(
            "SELECT entity_name FROM entity_identifiers "
            "WHERE identifier_type=? AND identifier_value=?",
            (id_type, value),
        ).fetchone()
        if owner is not None and owner[0] != canonical:
            failures.append(
                f"--set-id {spec!r}: {id_type} {value!r} already owned by "
                f"{owner[0]!r} (identifiers resolve to exactly one entity)"
            )
            continue
        ops.append(("id", canonical, id_type, value))

    return ops, failures


def apply_set_ops(conn: sqlite3.Connection, ops: list[tuple]) -> dict[str, int]:
    """Write a validated ops batch (plan_set_ops returned zero failures)."""
    wrote = {"cin_set": 0, "ids_upserted": 0}
    for op in ops:
        if op[0] == "cin":
            _, canonical, p = op
            conn.execute(
                "UPDATE entities SET cin=?, cin_listing=?, cin_nic5=?, cin_state=?, "
                "cin_year=?, cin_ownership=?, last_updated=? WHERE name=?",
                (
                    p.value,
                    p.listing,
                    p.nic5,
                    p.state,
                    p.year,
                    p.ownership,
                    utc_now(),
                    canonical,
                ),
            )
            wrote["cin_set"] += 1
        else:
            _, canonical, id_type, value = op
            conn.execute(
                """
                INSERT INTO entity_identifiers
                    (entity_name, identifier_type, identifier_value,
                     source_ref, last_updated)
                VALUES (?, ?, ?, 'manual', ?)
                ON CONFLICT (entity_name, identifier_type, identifier_value)
                DO UPDATE SET source_ref='manual', last_updated=excluded.last_updated
                """,
                (canonical, id_type, value, utc_now()),
            )
            wrote["ids_upserted"] += 1
    conn.commit()
    return wrote


def main(argv: list[str] | None = None) -> int:  # noqa: C901  # report/ensure/converge/set-ops CLI ladder; each branch is a two-line mode print
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    parser.add_argument(
        "--db", type=Path, default=None, help="SQLite path (default: live research.db)"
    )
    parser.add_argument(
        "--set-cin",
        action="append",
        metavar="NAME=CIN",
        default=[],
        help="set a company's CIN + parsed facets (repeatable; requires --apply)",
    )
    parser.add_argument(
        "--set-id",
        action="append",
        metavar="NAME:TYPE=VALUE",
        default=[],
        help="upsert a registry identifier: lei|cik|isin|llpin|alias (repeatable)",
    )
    args = parser.parse_args(argv)

    conn = connect(args.db or DEFAULT_DB_PATH)
    try:
        ensured, missing = _schema_ensured(conn)
        registry_rows = 0
        if (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='entity_identifiers' AND type='table'"
            ).fetchone()
            is not None
        ):
            registry_rows = conn.execute("SELECT COUNT(*) FROM entity_identifiers").fetchone()[0]

        if args.apply:
            ensure_schema(conn)
            ensured, missing = _schema_ensured(conn)
            stats = (
                converge(conn, apply=True)
                if ensured
                else {"cin_entities": 0, "facets_stale": 0, "malformed_cin": 0}
            )
        elif ensured:
            stats = converge(conn, apply=False)
        else:
            stats = {"cin_entities": 0, "facets_stale": 0, "malformed_cin": 0}

        mode = "APPLIED" if args.apply else "DRY-RUN"
        print(
            f"[identifiers] cin={stats['cin_entities']} "
            f"facets-stale={stats['facets_stale']} "
            f"malformed={stats['malformed_cin']} registry={registry_rows} [{mode}]"
        )
        if not ensured:
            if _entities_columns(conn):
                detail = ", ".join(missing) if missing else "entity_identifiers table"
            else:
                detail = "entities table"
            suffix = "" if args.apply else " — run --apply"
            print(f"[identifiers] schema not ensured (missing: {detail}){suffix}")

        if args.set_cin or args.set_id:
            if not args.apply:
                print(
                    f"[identifiers] {len(args.set_cin) + len(args.set_id)} set op(s) "
                    "requested — pass --apply to write"
                )
            ops, failures = plan_set_ops(conn, args.set_cin, args.set_id)
            if failures:
                print("VALIDATION FAILURES — nothing set:", file=sys.stderr)
                for f in failures:
                    print(f"  {f}", file=sys.stderr)
                return 1
            if args.apply:
                wrote = apply_set_ops(conn, ops)
                print(
                    f"[identifiers] wrote cin_set={wrote['cin_set']} "
                    f"ids_upserted={wrote['ids_upserted']}"
                )
            for op in ops:
                kind, target = op[0], op[1]
                detail = f"cin={op[2].value}" if kind == "cin" else f"{op[2]}={op[3]}"
                verb = "set" if args.apply else "would set"
                print(f"[identifiers] {verb} {target!r} {detail}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
