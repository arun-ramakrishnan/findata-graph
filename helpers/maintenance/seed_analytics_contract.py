#!/usr/bin/env python3
"""Seed the ``graph_analytics_contract`` served-universe table (graph_analytics_contract S1).

The routed centralities (closeness, harmonic, betweenness) used to derive
their served universe FROM the table they write to:
``SELECT DISTINCT entity_name FROM graph_analytics WHERE metric = ?``
(``scipy_bridge.contract_sources`` + the L1B lane's inline copy). The
definition equalling its own output froze the contract at an accumulated
1,732-name top-N union (2026-07-27 → 2026-10-16) — 8.6% of the live
20,044-node universe, growable only by luck.

This script materializes the contract ONCE from that legacy query, in
legacy query order (``seq`` — order is part of the contract,
``scipy_bridge.source_positions``), into a table whose FK to
``entities(name) ON DELETE/UPDATE CASCADE`` keeps the delete/rename
self-healing the self-read used to inherit implicitly. After seeding,
``contract_sources`` reads THIS table; ``graph_analytics`` rows become
pure cache. Day-one serving is byte-identical by construction: the seed
copies exactly what the legacy query returns, in the order it returns
it.

Dry-run default (house rule). ``--apply`` is idempotent
(``ON CONFLICT DO NOTHING``) and stamps schema v14 via
``ensure_db_meta``. Unseeded / pre-v14 stores need nothing:
``contract_sources`` falls back to the legacy self-read when the table
is absent or empty for the metric (restore-safe).

Growth is S2 (``--grow``, top-N companies by degree) and deliberately
NOT here: S1's acceptance is parity, not expansion.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Repo root: helpers/maintenance/seed_analytics_contract.py -> parents[2].
# Must be on sys.path BEFORE the helpers.* imports below so the script
# works as a subprocess the same way it works under pytest. (House
# bootstrap, mirrors the rebuild_* scripts.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import DEFAULT_DB_PATH, connect, ensure_db_meta  # noqa: E402
from helpers.graph.l1_betweenness import BETWEENNESS_METRIC  # noqa: E402
from helpers.graph.scipy_bridge import CLOSINESS_METRIC, HARMONIC_METRIC  # noqa: E402

CONTRACT_DDL = """
CREATE TABLE IF NOT EXISTS graph_analytics_contract (
    metric      TEXT NOT NULL,
    entity_name TEXT NOT NULL
                  REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
    seq         INTEGER NOT NULL,
    origin      TEXT NOT NULL DEFAULT 'seed',
    seeded_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (metric, entity_name)
)
"""

# The metrics the routed lanes serve — the only rows contract_sources
# consults. Anything else in graph_analytics (degree, pagerank, ...) is
# already full-universe and stays outside the contract concept.
SERVED_METRICS = (CLOSINESS_METRIC, HARMONIC_METRIC, BETWEENNESS_METRIC)


def _legacy_contract(con, metric: str) -> list[str]:
    """The pre-S1 definition, verbatim — the seed's source of truth and the
    reader's fallback."""
    return [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT entity_name FROM graph_analytics WHERE metric = ?",
            (metric,),
        ).fetchall()
    ]


def seed(
    db_path: str | Path = DEFAULT_DB_PATH, *, apply: bool = False
) -> dict[str, tuple[int, int]]:
    """Seed (or, dry-run, only measure) the contract table.

    Returns ``{metric: (legacy_count, contract_rows)}`` — for ``apply``
    the second figure is the table's row count post-insert (so an
    idempotent re-run shows identical numbers, proving parity held).
    """
    con = connect(db_path)
    try:
        report: dict[str, tuple[int, int]] = {}
        if not apply:
            for metric in SERVED_METRICS:
                names = _legacy_contract(con, metric)
                report[metric] = (len(names), len(names))
                print(f"[dry-run] {metric}: would seed {len(names)} contract rows")
            print("[dry-run] nothing written — pass --apply to materialize")
            return report

        con.execute(CONTRACT_DDL)
        inserted = 0
        for metric in SERVED_METRICS:
            names = _legacy_contract(con, metric)
            before = con.execute(
                "SELECT COUNT(*) FROM graph_analytics_contract WHERE metric = ?",
                (metric,),
            ).fetchone()[0]
            con.executemany(
                "INSERT INTO graph_analytics_contract (metric, entity_name, seq, origin) "
                "VALUES (?, ?, ?, 'seed') ON CONFLICT (metric, entity_name) DO NOTHING",
                ((metric, name, seq) for seq, name in enumerate(names)),
            )
            after = con.execute(
                "SELECT COUNT(*) FROM graph_analytics_contract WHERE metric = ?",
                (metric,),
            ).fetchone()[0]
            inserted += after - before
            report[metric] = (len(names), after)
            print(
                f"{metric}: legacy {len(names)} rows → contract table {after} "
                f"(+{after - before} this run)"
            )
        # Stamp v14 (idempotent): the DDL above is the schema delta; this
        # also refreshes the db_meta.schema_version mirror.
        ensure_db_meta(con)
        con.commit()
        print(
            f"seeded graph_analytics_contract ({inserted} new rows total)"
            if inserted
            else "contract table already current"
        )
        return report
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="research.db path")
    parser.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    args = parser.parse_args(argv)
    report = seed(args.db, apply=args.apply)
    total = sum(legacy for legacy, _ in report.values())
    print(f"\n{total} contract rows across {len(report)} served metrics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
