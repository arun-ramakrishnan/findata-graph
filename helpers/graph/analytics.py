#!/usr/bin/env python3
"""Read-only analytics over the git-tracked Parquet snapshot (A3, 2026-08-18).

DuckDB queries ``snapshots/parquet`` directly — the snapshot IS the
transport of record (Option B), so this gives DB-less, git-diffable
analytics (year-over-year edge growth, sector churn) without ever touching
the live databases. Every report is a pure function of the snapshot tree:
two checkouts of the same commit render identical markdown.

Reports:
    summary        snapshot provenance (_build_meta) + row counts per table
    edge-growth    edges per ingest-year x edge_type (graph_edges.created_at)
    sector-growth  companies per sector + new companies per year (entities)
    top-entities   highest-degree entities over non-membership edges
    coverage       series x sector coverage matrix from cited_in edges (C2)
    temporal       chatter volume by quarter, series coverage timeline,
                   staleness curve by sector, events timeline (C3; the
                   only composite report — fetch returns list[Report])

Usage:
    python3 helpers/graph/analytics.py                     # summary
    python3 helpers/graph/analytics.py edge-growth
    python3 helpers/graph/analytics.py top-entities --json
    make analytics                                         # default report

Note: edge-growth years are INGEST years (when the edge was written), not
event years — event time lives in valid_from (structured edges) and
e_acquired.year.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Repo root: helpers/graph/analytics.py -> parents[2] (house bootstrap;
# mirrors context_pack.py so the script works as a subprocess).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import duckdb  # noqa: E402  # after sys.path bootstrap

DEFAULT_SNAPSHOTS = _REPO_ROOT / "snapshots" / "parquet"
REPORTS = ("summary", "edge-growth", "sector-growth", "top-entities",
           "coverage", "temporal")

# Membership edges are structural (company -> sector/dir) and would drown
# every activity signal; analytics exclude them unless noted. cited_in is
# provenance (okf_activation P) — same treatment, and its roundup-edition
# hub (360/1,005 edges) would dominate every degree-based view.
_MEMBERSHIP_TYPES = ("part_of", "has_company", "belongs_to", "cited_in")


@dataclass
class Report:
    """One materialized report: a title, column headers, string rows."""

    title: str
    headers: list[str]
    rows: list[list[str]]
    note: str = ""
    meta: dict[str, str] = field(default_factory=dict)


def _p(root: Path, side: str, table: str) -> str:
    """POSIX path of a snapshot table (bound as a read_parquet($N) parameter)."""
    return (root / side / table).as_posix()


def _summary(root: Path, con: duckdb.DuckDBPyConnection) -> Report:
    meta = dict(con.execute(
        "SELECT key, value FROM read_parquet($1) ORDER BY key",
        [_p(root, "duckdb", "_build_meta.parquet")],
    ).fetchall())
    rows = []
    for side in ("duckdb", "sqlite"):
        for f in sorted((root / side).glob("*.parquet")):
            row = con.execute(
                "SELECT count(*) FROM read_parquet($1)", [f.as_posix()]
            ).fetchone()
            n = row[0] if row else 0
            rows.append([side, f.stem, str(n)])
    return Report(
        "Snapshot summary",
        ["side", "table", "rows"],
        rows,
        note="provenance from _build_meta; row counts from every parquet file",
        meta=meta,
    )


def _edge_growth(root: Path, con: duckdb.DuckDBPyConnection) -> Report:
    rows = con.execute(
        """
        SELECT strftime('%Y', TRY_CAST(created_at AS TIMESTAMP)) AS yr,
               edge_type, count(*) AS n
        FROM read_parquet($1)
        GROUP BY 1, 2 ORDER BY yr DESC, n DESC, edge_type
        """,
        [_p(root, "sqlite", "graph_edges.parquet")],
    ).fetchall()
    return Report(
        "Edge growth by ingest year",
        ["year", "edge_type", "edges"],
        [[str(r[0] or "?"), r[1], str(r[2])] for r in rows],
        note="ingest years (created_at), not event years — see module docstring",
    )


def _sector_growth(root: Path, con: duckdb.DuckDBPyConnection) -> Report:
    rows = con.execute(
        """
        WITH co AS (
            SELECT sector_classification AS sec,
                   strftime('%Y', TRY_CAST(created_at AS TIMESTAMP)) AS yr,
                   count(*) AS n
            FROM read_parquet($1)
            WHERE entity_type = 'company'
            GROUP BY 1, 2
        )
        SELECT sec,
               sum(n) AS total,
               max_by(yr, yr) AS latest_year,
               sum(n) FILTER (WHERE yr = (SELECT max(yr) FROM co)) AS latest_new
        FROM co GROUP BY 1
        ORDER BY total DESC, sec
        """,
        [_p(root, "sqlite", "entities.parquet")],
    ).fetchall()
    return Report(
        "Sector size and churn",
        ["sector", "companies", "latest_ingest_year", "new_in_latest_year"],
        [[r[0] or "?", str(r[1]), str(r[2] or "?"), str(r[3] or 0)] for r in rows],
        note="company entities per sector; 'new' = entities ingested in the latest year",
    )


def _top_entities(root: Path, con: duckdb.DuckDBPyConnection) -> Report:
    # $2.. placeholders expand with _MEMBERSHIP_TYPES (4 since cited_in
    # joined the exclusions) — build them, don't hardcode the arity.
    _ph = ", ".join(f"${i + 2}" for i in range(len(_MEMBERSHIP_TYPES)))
    rows = con.execute(
        f"""
        WITH ne AS (
            SELECT source AS e, edge_type, created_at FROM read_parquet($1)
            WHERE edge_type NOT IN ({_ph})
            UNION ALL
            SELECT target, edge_type, created_at FROM read_parquet($1)
            WHERE edge_type NOT IN ({_ph})
        )
        SELECT e, count(*) AS deg,
               count(*) FILTER (WHERE edge_type = 'co_mentioned_in') AS coment,
               strftime('%Y', min(TRY_CAST(created_at AS TIMESTAMP))) AS first_yr
        FROM ne GROUP BY 1
        ORDER BY deg DESC, e LIMIT 15
        """,  # noqa: S608  # parameterized; interpolated $N list is arity, not data
        [_p(root, "sqlite", "graph_edges.parquet"), *_MEMBERSHIP_TYPES],
    ).fetchall()
    return Report(
        "Top entities by non-membership degree",
        ["entity", "edges", "co_mentions", "first_ingest_year"],
        [[r[0], str(r[1]), str(r[2]), str(r[3] or "?")] for r in rows],
        note=f"excludes membership edges ({', '.join(_MEMBERSHIP_TYPES)})",
    )


def _coverage(root: Path, con: duckdb.DuckDBPyConnection) -> Report:
    """Series × sector coverage matrix from ``cited_in`` edges (C2).

    Joins are clean by construction — no free-text bridge: editions
    resolve via their entity rows (name = note stem), series via
    note_tags (the sync_tags mirror on the edition note path), sectors
    via entities.sector_classification, and quote depth via the edges'
    own ``n_quotes`` properties (stamped at derive time from the quotes
    table). Hygiene: the note reports how many cited_in edges joined a
    series-tagged edition; a gap means stale entities/note_tags, never a
    guessed row.
    """
    _editions = """
        SELECT e.name AS edition, t.tag AS series
        FROM read_parquet($1) e
        JOIN read_parquet($2) t
          ON t.note_path = e.file_path AND t.tag LIKE 'series/%'
        WHERE e.entity_type = 'edition'
    """
    matrix = con.execute(
        f"""
        WITH ed AS ({_editions})
        SELECT ed.series AS series,
               COALESCE(NULLIF(c.sector_classification, ''), '?') AS sector,
               COUNT(DISTINCT ge.source) AS companies,
               COUNT(DISTINCT ed.edition) AS editions,
               SUM(COALESCE(
                   CAST(json_extract_string(ge.properties, '$.n_quotes') AS BIGINT),
                   0)) AS quotes
        FROM read_parquet($3) ge
        JOIN ed ON ed.edition = ge.target
        JOIN read_parquet($1) c ON c.name = ge.source AND c.entity_type = 'company'
        WHERE ge.edge_type = 'cited_in'
        GROUP BY 1, 2
        ORDER BY companies DESC, series, sector
        LIMIT 40
        """,  # noqa: S608  # parameterized; interpolated CTE is a schema-constant literal
        [_p(root, "sqlite", "entities.parquet"),
         _p(root, "sqlite", "note_tags.parquet"),
         _p(root, "sqlite", "graph_edges.parquet")],
    ).fetchall()
    rollup = con.execute(
        f"""
        WITH ed AS ({_editions})
        SELECT ed.series,
               COUNT(DISTINCT ed.edition) AS editions_cited,
               COUNT(DISTINCT ge.source) FILTER (
                   WHERE s.entity_type = 'company') AS companies,
               COUNT(*) FILTER (WHERE s.entity_type IN ('sector', 'super_sector'))
                   AS sector_notes,
               COUNT(*) AS edges,
               SUM(COALESCE(
                   CAST(json_extract_string(ge.properties, '$.n_quotes') AS BIGINT),
                   0)) AS quotes
        FROM read_parquet($3) ge
        JOIN ed ON ed.edition = ge.target
        LEFT JOIN read_parquet($1) s ON s.name = ge.source
        WHERE ge.edge_type = 'cited_in'
        GROUP BY 1
        ORDER BY 1
        """,  # noqa: S608  # parameterized; interpolated CTE is a schema-constant literal
        [_p(root, "sqlite", "entities.parquet"),
         _p(root, "sqlite", "note_tags.parquet"),
         _p(root, "sqlite", "graph_edges.parquet")],
    ).fetchall()
    row = con.execute(
        "SELECT COUNT(*) FROM read_parquet($1) WHERE edge_type = 'cited_in'",
        [_p(root, "sqlite", "graph_edges.parquet")],
    ).fetchone()
    total = row[0] if row else 0
    joined = sum(r[4] for r in rollup)
    rollup_txt = "; ".join(
        f"{r[0].removeprefix('series/')} {r[1]} editions / {r[2]} companies"
        f" / {r[3]} sector-notes / {r[5]} quotes"
        for r in rollup
    )
    return Report(
        "Series × sector coverage (cited_in)",
        ["series", "sector", "companies", "editions", "quotes"],
        [[r[0], r[1], str(r[2]), str(r[3]), str(r[4])] for r in matrix],
        note=(f"per series: {rollup_txt}. "
              f"{joined}/{total} cited_in edges joined a series-tagged edition"
              + (" — sync_tags/derive drift!" if joined != total else "")),
    )


def _temporal(root: Path, con: duckdb.DuckDBPyConnection) -> list[Report]:
    """C3 temporal analytics: four time-keyed tables over the snapshot.

    Time axes (verified against the live snapshot, proposal §3):

    - ``quotes.as_of_edition`` -> edition entity ``created_at`` (first
      ingest). The edition stem join is the piece #136 unlocked; quote
      ``created_at`` is a single-batch re-derivation stamp, not a time
      axis, and is deliberately unused.
    - ``entities.last_updated`` -> staleness (company freshness vs now).
    - ``events.event_date`` -> true calendar time (2020..2027; future
      years are guidance forward-dates by D7 design — reported, not
      filtered).

    Composite report: returns four ``Report`` objects; ``fetch`` handles
    ``Report | list[Report]`` and ``main`` renders them sequentially.
    """
    entities = _p(root, "sqlite", "entities.parquet")
    quotes = _p(root, "sqlite", "quotes.parquet")
    events = _p(root, "sqlite", "events.parquet")

    def _editions() -> str:
        return """
            SELECT normalized_name AS stem, created_at
            FROM read_parquet($entities)
            WHERE entity_type = 'edition'
        """

    # --- T1: chatter volume by quarter ------------------------------------
    by_quarter = con.execute(
        f"""
        WITH ed AS ({_editions()})
        SELECT year(TRY_CAST(e.created_at AS TIMESTAMP)) || '-Q'
               || quarter(TRY_CAST(e.created_at AS TIMESTAMP)) AS quarter,
               COUNT(DISTINCT q.as_of_edition) AS editions,
               COUNT(*) AS quotes
        FROM read_parquet($quotes) q
        JOIN ed e ON q.as_of_edition = e.stem
        GROUP BY 1 ORDER BY 1
        """,  # noqa: S608  # parameterized; interpolated CTE is a schema-constant literal
        {"entities": entities, "quotes": quotes},
    ).fetchall()
    unmatched = con.execute(
        """
        SELECT COUNT(DISTINCT q.as_of_edition)
        FROM read_parquet($quotes) q
        WHERE NOT EXISTS (
            SELECT 1 FROM read_parquet($entities) e
            WHERE e.entity_type = 'edition'
              AND e.normalized_name = q.as_of_edition)
        """,
        {"entities": entities, "quotes": quotes},
    ).fetchone()
    unmatched_n = unmatched[0] if unmatched else 0
    t1 = Report(
        "Chatter volume by quarter",
        ["quarter", "editions", "quotes"],
        [[r[0], str(r[1]), str(r[2])] for r in by_quarter],
        note=(f"edition stem join: {unmatched_n} unmatched quote editions"
              + (" — concall/company-source chatter whose as_of_edition is a"
                 " concall title (honest-miss by design, #136)" if unmatched_n else "")
              + "; quarter = edition first-ingest date (entities.created_at)"),
    )

    # --- T2: coverage trend per edition -----------------------------------
    per_edition = con.execute(
        f"""
        WITH ed AS ({_editions()}),
        agg AS (
            SELECT as_of_edition AS stem, 'q' AS kind, COUNT(*) AS n
            FROM read_parquet($quotes) GROUP BY 1
            UNION ALL
            SELECT as_of_edition, 'e', COUNT(*)
            FROM read_parquet($events) GROUP BY 1
        )
        SELECT CAST(e.created_at AS DATE) AS ingested, e.stem,
               COALESCE(SUM(n) FILTER (WHERE kind = 'q'), 0) AS quotes,
               COALESCE(SUM(n) FILTER (WHERE kind = 'e'), 0) AS events
        FROM ed e
        LEFT JOIN agg ON agg.stem = e.stem
        GROUP BY 1, 2
        ORDER BY 1, 2
        """,  # noqa: S608  # parameterized; interpolated CTE is a schema-constant literal
        {"entities": entities, "quotes": quotes, "events": events},
    ).fetchall()
    t2 = Report(
        "Coverage trend per edition (ingest order)",
        ["ingested", "edition", "quotes", "events", "thin"],
        [[str(r[0]), r[1], str(r[2]), str(r[3]),
          "*" if r[2] + r[3] < 10 else ""] for r in per_edition],
        note="thin* = quotes+events < 10; ingested = edition first-ingest date",
    )

    # --- T3: staleness curve by sector ------------------------------------
    stale = con.execute(
        """
        SELECT COALESCE(NULLIF(sector_classification, ''), '?') AS sector,
               COUNT(*) AS companies,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY days) AS p50,
               percentile_cont(0.9) WITHIN GROUP (
                   ORDER BY days) AS p90,
               MAX(days) AS max_days,
               COUNT(*) FILTER (WHERE days > 30) AS stale_gt_30d
        FROM (
            SELECT sector_classification,
                   date_diff('day',
                             TRY_CAST(last_updated AS TIMESTAMP), now()) AS days
            FROM read_parquet($entities)
            WHERE entity_type = 'company'
        ) GROUP BY 1
        ORDER BY stale_gt_30d DESC, p90 DESC, sector
        """,
        {"entities": entities},
    ).fetchall()
    t3 = Report(
        "Staleness curve by sector (days since last_updated)",
        ["sector", "companies", "p50_days", "p90_days", "max_days", "stale>30d"],
        [[r[0], str(r[1]),
          f"{r[2]:.0f}", f"{r[3]:.0f}", str(r[4]), str(r[5])] for r in stale],
        note="staleness relative to report-run time (now()); company entities only",
    )

    # --- T4: events timeline ----------------------------------------------
    ev_tl = con.execute(
        """
        SELECT COALESCE(CAST(year(TRY_CAST(event_date AS DATE)) AS VARCHAR),
                        '?') AS yr,
               event_type, COUNT(*) AS n
        FROM read_parquet($events)
        GROUP BY 1, 2 ORDER BY yr, event_type
        """,
        {"events": events},
    ).fetchall()
    t4 = Report(
        "Events timeline (D7 spine)",
        ["year", "event_type", "events"],
        [[r[0], r[1], str(r[2])] for r in ev_tl],
        note=("'?' = undated events (NULL event_date, date_precision "
              "none/absent); future years are guidance forward-dates by "
              "D7 design, not data errors"),
    )
    return [t1, t2, t3, t4]


_FETCHERS = {
    "summary": _summary,
    "edge-growth": _edge_growth,
    "sector-growth": _sector_growth,
    "top-entities": _top_entities,
    "coverage": _coverage,
    "temporal": _temporal,
}


def fetch(name: str, root: Path = DEFAULT_SNAPSHOTS) -> Report | list[Report]:
    """Materialize one report (or a composite list) against the snapshot tree."""
    if name not in _FETCHERS:
        raise ValueError(f"unknown report {name!r} (choose from {list(REPORTS)})")
    if not root.exists():
        raise FileNotFoundError(f"snapshot tree {root} not found (run make snapshot)")
    con = duckdb.connect()
    try:
        return _FETCHERS[name](root, con)
    finally:
        con.close()


def render_markdown(r: Report) -> str:
    """Plain aligned table (stable across terminals, git-diffable)."""
    widths = [
        max(len(str(r.headers[i])), *(len(row[i]) for row in r.rows)) if r.rows
        else len(str(r.headers[i]))
        for i in range(len(r.headers))
    ]
    def fmt(cells: list[str]) -> str:
        return "  ".join(str(c).ljust(w) for c, w in zip(cells, widths, strict=True)).rstrip()

    L = [f"# {r.title}", ""]
    if r.meta:
        L.append(" · ".join(f"{k}={v}" for k, v in sorted(r.meta.items())))
        L.append("")
    L.append(fmt(r.headers))
    L.append("  ".join("-" * w for w in widths))
    L.extend(fmt(row) for row in r.rows)
    if r.note:
        L += ["", f"_{r.note}_"]
    return "\n".join(L)


def render_json(r: Report) -> str:
    """Machine-readable form (headers + rows + meta + note)."""
    return json.dumps(
        {"title": r.title, "headers": r.headers, "rows": r.rows,
         "meta": r.meta, "note": r.note},
        indent=2, ensure_ascii=False,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Parquet snapshot analytics (A3)")
    p.add_argument("report", nargs="?", default="summary", choices=REPORTS)
    p.add_argument("--snapshots", type=Path, default=DEFAULT_SNAPSHOTS)
    p.add_argument("--json", action="store_true", help="JSON instead of markdown")
    args = p.parse_args(argv)
    try:
        r = fetch(args.report, args.snapshots)
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if isinstance(r, list):  # composite report (temporal)
        if args.json:
            print(json.dumps(
                [json.loads(render_json(x)) for x in r], indent=2,
                ensure_ascii=False))
        else:
            print("\n\n".join(render_markdown(x) for x in r))
    else:
        print(render_json(r) if args.json else render_markdown(r))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
