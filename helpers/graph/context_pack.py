#!/usr/bin/env python3
"""GraphRAG-lite context packs (C1, 2026-08-17).

Serializes a scored ego-subgraph around one entity into a Markdown "context
pack" — the exact artifact an LLM (or the D5 agent workflow) consumes. Pure
composition over the existing graph layer; no new dependencies.

Sources, in priority order (each contributes typed Facts):

- structured star tables (e_subsidiary / e_acquired / e_jv / e_supplier /
  e_customer / e_group / e_competes / e_belongs_to / e_exposed_to /
  e_comention), directionalized and name-joined via v_node;
- v_node / v_company profile (kind, sector, market cap, ticker);
- semantic_neighbors (embedding kNN) when the entity has a vector;
- sector rollup of every entity that made the pack;
- R2 deep-relations section (opt-in via ``research_db``): per-semantics
  rollups over the R3 insight tables in research.db (ultimate parent +
  depth-3 descendant forest, hidden connections, competitor overlap,
  chokepoint suppliers — read DIRECTLY from sqlite, no v_* mirror) plus
  two-step supply neighborhoods and shared groups/editions from the
  DuckDB cache (e_supplier 2-step, h_edge/h_incidence).

Budget semantics: fact-count (user decision 2026-08-17). ``budget`` bounds
the number of relation facts kept; profile + semantic + rollup sections are
small fixed costs reported separately. Facts rank by edge-type priority
(ownership/structural before co-mention), then weight desc, then name —
so trimming drops the least informative tail (co-mentions) first. A char
estimate (len/4) is reported in the footer, not enforced.

Usage:
    python3 helpers/graph/context_pack.py "Mahindra & Mahindra"
    python3 helpers/graph/context_pack.py INFY.NS --budget 25 --hops 2
    python3 -m helpers.graph.context_pack "Tata Consumer" > pack.md
"""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root: helpers/graph/context_pack.py -> parents[2]. Must be on sys.path
# BEFORE the `from helpers...` imports below so the script works as a
# subprocess the same way it works under pytest.
# (Mirrors the rebuild_note_search.py bootstrap.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import datetime as _dt  # noqa: E402  # after sys.path bootstrap
import json  # noqa: E402
import sqlite3  # noqa: E402
from dataclasses import dataclass  # noqa: E402

import duckdb  # noqa: E402

from helpers.core import jsonio  # noqa: E402  (orjson parse cutover; dumps stay stdlib per db_scaling_tuning §6.3 criteria)
from helpers.core.db import connect as _db_connect  # noqa: E402

DEFAULT_DUCKDB = Path("memory") / "graph.duckdb"
DEFAULT_RESEARCH_DB = Path("memory") / "research.db"

# R2 deep-relations constants. The supply corroboration floor mirrors the
# hidden_connections doctrine (a single shared supplier is hub noise).
_DEEP_MAX_DEPTH = 3
_DEEP_TOP = 8
_SUPPLY_MIN_SHARED = 2
_SUPPLY_TOP = 8
_HYPER_FAMILIES = ("group", "edition", "jv")

# (table, subject_col, object_col, label, priority). Priority asc = keep
# first; co-mention is the firehose so it trims first. okf_activation P:
# cited_in is provenance/display-only (priority 11, trims with/after the
# firehose) and excluded from _STRUCTURED_TABLES so hops never expand
# through editions (a quarterly-roundup hub would pull in half the graph).
_EDGE_SPECS: tuple[tuple[str, str, str, str, int], ...] = (
    ("e_subsidiary", "subsidiary_name", "parent_name", "subsidiary_of", 1),
    ("e_acquired", "acquirer_name", "target_name", "acquired", 2),
    ("e_jv", "a_name", "b_name", "joint_venture_with", 3),
    ("e_supplier", "supplier_name", "customer_name", "supplies_to", 4),
    ("e_customer", "customer_name", "supplier_name", "customer_of", 5),
    ("e_group", "a_name", "b_name", "same_group", 6),
    ("e_competes", "a_name", "b_name", "competes_with", 7),
    ("e_belongs_to", "child_id", "parent_id", "belongs_to", 8),
    ("e_exposed_to", "company_id", "theme_id", "exposed_to", 9),
    ("e_comention", "a_name", "b_name", "co_mentioned_with", 10),
    ("e_cited_in", "company_id", "edition_id", "cited_in", 11),
)
# Hop expansion runs over these; firehose/display tables stay out.
_NON_STRUCTURED = frozenset({"co_mentioned_with", "cited_in"})
_STRUCTURED_TABLES = tuple(s[0] for s in _EDGE_SPECS if s[3] not in _NON_STRUCTURED)


@dataclass(frozen=True)
class Fact:
    """One relation fact: SUBJECT label OBJECT, with provenance."""

    subject: str
    label: str
    obj: str
    priority: int
    weight: float
    year: str | None
    source_ref: str | None

    def line(self) -> str:
        bits = [f"{self.subject} —{self.label}→ {self.obj}"]
        meta = []
        if self.weight:
            meta.append(f"w={self.weight:.2f}".rstrip("0").rstrip("."))
        if self.year:
            meta.append(self.year)
        if self.source_ref:
            meta.append(f"src={self.source_ref}")
        if meta:
            bits.append("(" + ", ".join(meta) + ")")
        return " ".join(bits)


def _resolve_node(con: duckdb.DuckDBPyConnection, name: str) -> tuple[int, str] | None:
    """Exact, then case-insensitive, then ticker match on v_node."""
    row = con.execute("SELECT id, name FROM v_node WHERE name = $1", [name]).fetchone()
    if row:
        return row[0], row[1]
    row = con.execute(
        "SELECT id, name FROM v_node WHERE lower(name) = lower($1) LIMIT 1", [name]
    ).fetchone()
    if row:
        return row[0], row[1]
    row = con.execute(
        "SELECT n.id, n.name FROM v_node n JOIN v_company c ON c.id = n.id"
        " WHERE c.ticker = $1 LIMIT 1",
        [name],
    ).fetchone()
    return (row[0], row[1]) if row else None


def _collect_facts(
    con: duckdb.DuckDBPyConnection, ids: set[int]
) -> tuple[list[Fact], dict[int, str]]:
    """All structured + co-mention facts touching the id set (either side).

    Two passes: raw (sid, oid) rows first — co-mention partners are NOT in
    the expansion set, so the name map must cover every id the edges
    actually reference — then build the frozen Fact rows. Returns
    (facts, name_by_id) so callers can reuse the resolved names.
    """
    raw: list[tuple[str, int, int, float, str | None, str | None, str, int]] = []
    referenced: set[int] = set()
    id_list = ", ".join(str(int(i)) for i in ids) or "-1"
    for table, subj_col, obj_col, label, priority in _EDGE_SPECS:
        year_col = ", year" if table == "e_acquired" else ", NULL"
        rows = con.execute(
            f"SELECT s.{subj_col}, s.{obj_col}, s.weight{year_col}, s.source_ref"  # noqa: S608
            f" FROM {table} s WHERE s.{subj_col} IN ({id_list})"
            f" OR s.{obj_col} IN ({id_list})"
        ).fetchall()
        for sid, oid, weight, *rest in rows:
            year, source_ref = rest[0], rest[1]
            referenced.update((int(sid), int(oid)))
            raw.append(
                (
                    table,
                    int(sid),
                    int(oid),
                    float(weight or 0.0),
                    str(year) if year else None,
                    str(source_ref) if source_ref else None,
                    label,
                    priority,
                )
            )
    name_by_id = _names_for(con, referenced)
    facts = [
        Fact(
            subject=name_by_id.get(sid, f"#{sid}"),
            label=label,
            obj=name_by_id.get(oid, f"#{oid}"),
            priority=priority,
            weight=weight,
            year=year,
            source_ref=source_ref,
        )
        for _table, sid, oid, weight, year, source_ref, label, priority in raw
    ]
    return facts, name_by_id


def _names_for(con: duckdb.DuckDBPyConnection, ids: set[int]) -> dict[int, str]:
    """id -> name for every id in the set (empty set -> empty map)."""
    if not ids:
        return {}
    id_list = ", ".join(str(int(i)) for i in sorted(ids))
    return {
        int(r[0]): str(r[1])
        for r in con.execute(
            f"SELECT id, name FROM v_node WHERE id IN ({id_list})"  # noqa: S608
        ).fetchall()
    }


def _expand_hops(con: duckdb.DuckDBPyConnection, seed_id: int, hops: int) -> set[int]:
    """Entity id set for fact collection.

    hops=1 is the EGO pack: facts touching the seed only (no expansion).
    hops=N>1 adds N-1 rounds over structured (non-comention) edges.
    Comention is excluded from expansion (it would swallow the graph at
    1.3k edges) but still contributes facts for the final id set."""
    frontier = {seed_id}
    seen = {seed_id}
    for _ in range(max(0, hops - 1)):
        if not frontier:
            break
        fl = ", ".join(str(int(i)) for i in frontier) or "-1"
        nxt: set[int] = set()
        for table, subj_col, obj_col, _label, _p in _EDGE_SPECS:
            if table not in _STRUCTURED_TABLES:
                continue
            rows = con.execute(
                f"SELECT s.{subj_col}, s.{obj_col} FROM {table} s"  # noqa: S608
                f" WHERE s.{subj_col} IN ({fl}) OR s.{obj_col} IN ({fl})"
            ).fetchall()
            for sid, oid in rows:
                nxt.update((int(sid), int(oid)))
        frontier = nxt - seen
        seen |= frontier
    return seen


def _profile_of(
    con: duckdb.DuckDBPyConnection, seed_id: int
) -> tuple[str | None, str | None, str | None, str | None]:
    """(kind, sector, market_cap, ticker) for the seed node."""
    prof = con.execute(
        "SELECT kind, sector_classification, market_cap, ticker FROM v_node WHERE id = $1",
        [seed_id],
    ).fetchone()
    return prof if prof else (None, None, None, None)


def _semantic_of(
    con: duckdb.DuckDBPyConnection,
    seed_name: str,
    kind: str | None,
    ticker: str | None,
    k_semantic: int,
) -> list[tuple[str, str, float]]:
    """Embedding kNN neighbors; empty when the entity has no vector or the
    embeddings table is absent (read-only conns on a cold sidecar graph)."""
    if kind != "company" and not ticker:
        return []
    try:
        from helpers.graph.query import semantic_neighbors

        return semantic_neighbors(con, seed_name, k=k_semantic)
    except Exception:  # noqa: S110  # no embedding / table missing -> skip
        return []


def _rollup_of(con: duckdb.DuckDBPyConnection, entities: set[str]) -> list[tuple[str | None, int]]:
    """Sector distribution of the given entity names, biggest first."""
    if not entities:
        return []
    names = sorted(entities)
    placeholders = ", ".join("?" for _ in names)
    return con.execute(
        "SELECT sector_classification, count(*) FROM v_node"  # noqa: S608  # placeholders, values parameterized
        f" WHERE name IN ({placeholders})"
        " GROUP BY 1 ORDER BY 2 DESC, 1",
        names,
    ).fetchall()


def _render_relations(L: list[str], kept: list[Fact]) -> None:
    """Append the grouped relation section to the output lines."""
    if not kept:
        L.append("_(no relation facts found)_")
        L.append("")
        return
    cur: str | None = None
    for f in kept:
        if f.label != cur:
            L.append(f"### {f.label}")
            L.append("")
            cur = f.label
        L.append(f"- {f.line()}")
    L.append("")


# --------------------------------------------------------------------------- #
# R2 deep-relations rollups (graph_relational_depth): per-semantics card facts
# --------------------------------------------------------------------------- #
def _sqlite_ro(path: Path) -> sqlite3.Connection | None:
    if not Path(path).exists():
        return None
    return _db_connect(path, read_only=True)


def _q(scon: sqlite3.Connection, sql: str, params: tuple = ()) -> list:
    """Read with absent-table tolerance: a pre-R3 research.db answers []."""
    try:
        return scon.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def _deep_control(scon: sqlite3.Connection, seed_name: str) -> list[str]:
    """Control rollup: the ultimate_parent row + the descendant forest."""
    lines: list[str] = []
    row = _q(
        scon,
        "SELECT ultimate_parent, depth, path, root_kind, coalition_pct "
        "FROM ultimate_parent WHERE company = ?",
        (seed_name,),
    )
    if row:
        _apex, depth, path_json, _kind, coalition = row[0]
        path = jsonio.loads(path_json)
        if depth == 0:
            line = "- control: self-root (no trusted parent)"
            if coalition is not None:
                line += f" — promoter coalition {coalition:.1f}%"
            lines.append(line)
        else:
            chain = " → ".join(path)
            lines.append(f"- ultimate parent: {path[-1]} (depth {depth}; chain: {chain})")
    needle = json.dumps(seed_name)  # quoted name inside the path JSON array
    descendants: list[tuple[str, int]] = []
    total_desc = 0
    for company, pjson in _q(
        scon,
        "SELECT company, path FROM ultimate_parent "
        "WHERE depth > 0 AND company <> ? AND instr(path, ?) > 0",
        (seed_name, needle),
    ):
        p = jsonio.loads(pjson)
        try:
            # path starts at the descendant (index 0): depth-below-seed IS
            # the seed's index in the chain
            below = p.index(seed_name)
        except ValueError:
            continue
        total_desc += 1
        if below <= _DEEP_MAX_DEPTH:
            descendants.append((company, below))
    if total_desc:
        descendants.sort()
        direct = sum(1 for _c, b in descendants if b == 1)
        sample = ", ".join(
            f"{c} ({b} hop{'s' if b > 1 else ''})" for c, b in descendants[:_DEEP_TOP]
        )
        lines.append(
            f"- descendant forest: {total_desc} companies roll up to this root "
            f"({direct} direct); nearest: {sample}"
        )
    return lines


def _deep_pairwise(scon: sqlite3.Connection, seed_name: str) -> list[str]:
    """Hidden-connection / competitor-overlap / chokepoint rollups."""
    lines: list[str] = []
    hidden = [
        r[0]
        for r in _q(
            scon,
            "SELECT b FROM hidden_connections WHERE a = ? "
            "UNION SELECT a FROM hidden_connections WHERE b = ?",
            (seed_name, seed_name),
        )
    ]
    if hidden:
        top = ", ".join(sorted(hidden)[:_DEEP_TOP])
        lines.append(f"- hidden connections ({len(hidden)}): {top}")
    overlap = sorted(
        {
            r[0]
            for r in _q(
                scon,
                "SELECT b FROM competitive_overlap WHERE a = ? "
                "UNION SELECT c FROM competitive_overlap WHERE a = ? "
                "UNION SELECT a FROM competitive_overlap WHERE b = ? "
                "UNION SELECT c FROM competitive_overlap WHERE b = ? "
                "UNION SELECT a FROM competitive_overlap WHERE c = ? "
                "UNION SELECT b FROM competitive_overlap WHERE c = ?",
                (seed_name,) * 6,
            )
        }
    )
    if overlap:
        lines.append(
            f"- competitor-of-competitor set ({len(overlap)}): " + ", ".join(overlap[:_DEEP_TOP])
        )
    chokes = [
        supplier
        for supplier, companies_json in _q(
            scon, "SELECT supplier, companies FROM supply_chokepoints"
        )
        if seed_name in jsonio.loads(companies_json or "[]")
    ]
    if chokes:
        lines.append(
            f"- chokepoint suppliers ({len(chokes)} — shared with rivals): "
            + ", ".join(sorted(chokes)[:_DEEP_TOP])
        )
    return lines


def _two_step_supply(con: duckdb.DuckDBPyConnection, seed_id: int) -> tuple[list[str], set[int]]:
    """2-step supply neighborhood over e_supplier: rivals sharing ≥2 of my
    suppliers, my suppliers' suppliers, and suppliers shared by ≥2 of my
    customers. Returns (lines, referenced ids) — names resolve later."""
    lines: list[str] = []
    ids: set[int] = set()
    sup_ids = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT supplier_name FROM e_supplier WHERE customer_name = ?",
            [seed_id],
        ).fetchall()
    ]
    cust_ids = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT customer_name FROM e_supplier WHERE supplier_name = ?",
            [seed_id],
        ).fetchall()
    ]
    if sup_ids:
        slist = ", ".join(str(int(i)) for i in sup_ids)
        rivals = con.execute(
            f"SELECT customer_name, count(*) AS c FROM e_supplier "  # noqa: S608  # int-list inlined
            f"WHERE supplier_name IN ({slist}) AND customer_name <> ? "
            "GROUP BY 1 HAVING count(*) >= ? ORDER BY c DESC, 1",
            [seed_id, _SUPPLY_MIN_SHARED],
        ).fetchall()
        if rivals:
            ids.update(r[0] for r in rivals)
            lines.append(
                "- shared-supply rivals ("
                + ", ".join(f"#{r[0]} ({r[1]})" for r in rivals[:_SUPPLY_TOP])
                + ")"
            )
        second = con.execute(
            f"SELECT DISTINCT supplier_name FROM e_supplier "  # noqa: S608  # int-list inlined
            f"WHERE customer_name IN ({slist}) AND supplier_name <> ? ORDER BY 1",
            [seed_id],
        ).fetchall()
        if second:
            ids.update(r[0] for r in second)
            lines.append(
                "- second-tier suppliers: " + ", ".join(f"#{r[0]}" for r in second[:_SUPPLY_TOP])
            )
    if cust_ids:
        clist = ", ".join(str(int(i)) for i in cust_ids)
        shared = con.execute(
            f"SELECT supplier_name, count(*) AS c FROM e_supplier "  # noqa: S608  # int-list inlined
            f"WHERE customer_name IN ({clist}) AND supplier_name <> ? "
            "GROUP BY 1 HAVING count(*) >= ? ORDER BY c DESC, 1",
            [seed_id, _SUPPLY_MIN_SHARED],
        ).fetchall()
        if shared:
            ids.update(r[0] for r in shared)
            lines.append(
                "- suppliers shared by my customers: "
                + ", ".join(f"#{r[0]} ({r[1]})" for r in shared[:_SUPPLY_TOP])
            )
    return lines, ids


def _groups_editions(con: duckdb.DuckDBPyConnection, seed_name: str) -> list[str]:
    """Shared-context hyperedges (group / edition / jv families) holding
    the seed. Absent h_* tables (pre-D4 cache) answer []."""
    fams = ", ".join(f"'{f}'" for f in _HYPER_FAMILIES)
    try:
        rows = con.execute(
            f"SELECT h.edge_type, h.label FROM h_edge h "  # noqa: S608  # constant families
            "JOIN h_incidence i ON i.edge_id = h.id "
            f"WHERE i.entity_name = ? AND h.edge_type IN ({fams}) "
            "ORDER BY h.edge_type, h.label",
            [seed_name],
        ).fetchall()
    except duckdb.Error:
        return []
    lines = []
    for fam in _HYPER_FAMILIES:
        labels = [label for etype, label in rows if etype == fam]
        if labels:
            lines.append(f"- {fam} ({len(labels)}): " + ", ".join(labels[:_DEEP_TOP]))
    return lines


def _deep_relations(
    scon: sqlite3.Connection,
    con: duckdb.DuckDBPyConnection,
    seed_id: int,
    seed_name: str,
) -> list[str]:
    """Assemble the whole deep-relations section; empty when nothing rolls
    up (the header is then omitted)."""
    L: list[str] = []
    ctrl = _deep_control(scon, seed_name)
    if ctrl:
        L += ["### Control", ""] + ctrl + [""]
    supply, sup_ids = _two_step_supply(con, seed_id)
    if supply:
        names = _names_for(con, sup_ids)
        for i, line in enumerate(supply):
            for nid, nm in names.items():
                line = line.replace(f"#{nid}", nm)
            supply[i] = line
        L += ["### Two-step supply", ""] + supply + [""]
    pairwise = _deep_pairwise(scon, seed_name)
    if pairwise:
        L += ["### Insight rollups", ""] + pairwise + [""]
    hyper = _groups_editions(con, seed_name)
    if hyper:
        L += ["### Groups & editions", ""] + hyper + [""]
    return L


def _deep_section(
    con: duckdb.DuckDBPyConnection,
    seed_id: int,
    seed_name: str,
    research_db: Path | None,
) -> list[str]:
    """R2 deep-relations lines; None/missing research_db skips (empty)."""
    if research_db is None:
        return []
    scon = _sqlite_ro(Path(research_db))
    if scon is None:
        return []
    try:
        return _deep_relations(scon, con, seed_id, seed_name)
    finally:
        scon.close()


def build_context_pack(
    con: duckdb.DuckDBPyConnection,
    name: str,
    *,
    hops: int = 1,
    budget: int = 40,
    k_semantic: int = 8,
    research_db: Path | None = None,
) -> str:
    """Build the Markdown context pack for one entity.

    Args:
        con: DuckDB connection to graph.duckdb (read-only is fine).
        name: entity name (exact, case-insensitive, or ticker).
        hops: expansion rounds over structured edges (default 1 = ego).
        budget: max relation facts kept (fact-count semantics; profile,
            semantic neighbors and the rollup are small fixed sections).
        k_semantic: semantic-neighbor rows when an embedding exists.
        research_db: research.db path enabling the R2 deep-relations
            section (R3 insight tables + two-step supply + groups/editions);
            None or a missing file skips the section.

    Returns:
        Markdown string. Raises ValueError for an unknown entity.
    """
    node = _resolve_node(con, name)
    if node is None:
        raise ValueError(f"entity not found in v_node: {name!r}")
    seed_id, seed_name = node

    ids = _expand_hops(con, seed_id, hops)
    facts, _name_by_id = _collect_facts(con, ids)
    available = len(facts)
    # Rank: priority asc (ownership/structural first), weight desc, then
    # deterministic name order; trim to budget -> co-mentions drop first.
    facts.sort(key=lambda f: (f.priority, -f.weight, f.subject, f.obj))
    kept = facts[:budget]

    kind, sector, market_cap, ticker = _profile_of(con, seed_id)
    semantic = _semantic_of(con, seed_name, kind, ticker, k_semantic)

    # Sector rollup over every entity that made the pack.
    pack_entities = {seed_name} | {f.subject for f in kept} | {f.obj for f in kept}
    rollup = _rollup_of(con, pack_entities)

    today = _dt.date.today().isoformat()
    L: list[str] = []
    L.append(f"# Context pack — {seed_name}")
    L.append("")
    L.append(f"_generated {today} · hops={hops} · budget={budget} facts_")
    L.append("")
    L.append("## Profile")
    L.append("")
    L.append(f"- name: {seed_name}")
    L.append(f"- kind: {kind or 'unknown'}")
    L.append(f"- sector: {sector or 'unknown'}")
    if market_cap:
        L.append(f"- market_cap: {market_cap}")
    if ticker:
        L.append(f"- ticker: {ticker}")
    L.append("")

    L.append(f"## Relations ({len(kept)} of {available} available)")
    L.append("")
    _render_relations(L, kept)

    deep_lines = _deep_section(con, seed_id, seed_name, research_db)
    if deep_lines:
        L.append("## Deep relations")
        L.append("")
        L.extend(deep_lines)

    if semantic:
        L.append(f"## Semantic neighbors (top {len(semantic)})")
        L.append("")
        for other, _sector, sim in semantic:
            L.append(f"- {other} (cosine {sim:.3f})")
        L.append("")

    if rollup:
        L.append("## Sector rollup (pack entities)")
        L.append("")
        L.append("| sector | entities |")
        L.append("|---|---|")
        for sect, n in rollup:
            L.append(f"| {sect or 'unknown'} | {n} |")
        L.append("")

    pack = "\n".join(L)
    est_chars = len(pack)
    L.append("---")
    L.append(
        f"_budget: {len(kept)}/{budget} relation facts kept of {available}"
        f" available · pack ≈ {est_chars} chars ≈ {est_chars // 4} tokens_"
    )
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="GraphRAG-lite context pack")
    p.add_argument("name", help="entity name or ticker (e.g. 'Mahindra & Mahindra', INFY.NS)")
    p.add_argument("--duckdb", type=Path, default=DEFAULT_DUCKDB, help="graph.duckdb path")
    p.add_argument(
        "--research-db",
        type=Path,
        default=DEFAULT_RESEARCH_DB,
        help="research.db for the deep-relations section (missing file skips it)",
    )
    p.add_argument("--hops", type=int, default=1, help="expansion rounds over structured edges")
    p.add_argument("--budget", type=int, default=40, help="max relation facts kept")
    p.add_argument("--k-semantic", type=int, default=8, help="semantic neighbor rows")
    args = p.parse_args(argv)
    if not args.duckdb.exists():
        print(
            f"error: {args.duckdb} not found (run make graph-rebuild first)",
            file=sys.stderr,
        )
        return 2
    # Shared reader contract (not raw duckdb.connect): loads the vss scalars
    # the semantic_neighbors leg needs, never triggers a build (see
    # query.connect_read_only). The raw open previously relied on DuckDB
    # extension autoload — explicit LOAD is the query.py house contract.
    from helpers.graph.query import connect_read_only

    con = connect_read_only(args.duckdb)
    try:
        print(
            build_context_pack(
                con,
                args.name,
                hops=args.hops,
                budget=args.budget,
                k_semantic=args.k_semantic,
                research_db=args.research_db if args.research_db.exists() else None,
            )
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        con.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
