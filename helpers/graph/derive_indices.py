#!/usr/bin/env python3
"""Derive ``index`` entities + ``listed_on_index`` edges (company -> index).

Index-membership fill S2 (doc/improvements/archive/graph/index_membership_fill.md).
Reads the raw constituent sidecar (``sources.duckdb::vw_index_constituent``,
written by ``helpers/maintenance/index_sync.py``) and projects it into the
canonical SQLite graph:

- one fileless ``index`` entity per index name (idempotent INSERT OR IGNORE),
- one dyadic ``listed_on_index`` edge per (company, index),
- converged ``sector_classification`` on resolved companies whose field is
  still empty, via an explicit NSE-industry -> canonical-sector map (the
  raw exchange label is NOT the house taxonomy and stays sidecar-side).

Resolution precedence (never fuzzy name matching — #218 misfires four ways):
ISIN via ``exchange_listings.isin`` -> NSE symbol via ``entities.ticker``
(``SYMBOL`` or ``SYMBOL.NS``) -> worklist.

Idempotent: INSERT OR IGNORE for entities, apply_typed_edges
(UNIQUE(source, target, edge_type)) for edges. Re-running is safe.

Usage:
    python3 helpers/graph/derive_indices.py            # dry-run summary
    python3 helpers/graph/derive_indices.py --apply    # entities + edges + worklist
    python3 helpers/graph/derive_indices.py --verbose  # list every edge
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import DEFAULT_DB_PATH, connect, utc_now  # noqa: E402
from helpers.core import duckdb_shared as ds  # noqa: E402
from helpers.graph import derive_cli as dcli  # noqa: E402  # S6 shared CLI scaffold
from helpers.graph._edge_writer import apply_typed_edges  # noqa: E402

EDGE_TYPE = "listed_on_index"

# Index aliases measured identical to a canonical index (2026-11-13):
# constituent sets compared member-by-member — NIFTY 500 ≡ Nifty500
# Multicap 50-25-25 Index ≡ Nifty500 LargeMidSmall Equal-Cap Weighted
# (all 499/499); plus the MidSmallcap-400 and Financial-Services
# weight-scheme alias pairs (operator-approved 2026-11-14, same
# "one index, two weight schemes" class — see
# helpers/maintenance/index_label_dedup.py). Folding here makes
# derivation idempotent against duplicate labels in the NSE sidecar;
# the canonical name is the fold target.
INDEX_NAME_CANONICAL: dict[str, str] = {
    "Nifty500 Multicap 50-25-25 Index": "NIFTY 500",
    "Nifty500 LargeMidSmall Equal-Cap Weighted": "NIFTY 500",
    "Nifty MidSmallcap400 50-50": "Nifty MidSmallcap 400",
    "Nifty Financial Services 25/50 Index": "Nifty Financial Services",
}
SOURCE_REF = "derive:indices:nse-constituents"
SOURCES_DB = _REPO_ROOT / "memory" / "data" / "sources.duckdb"
WORKLIST = _REPO_ROOT / "findata" / "Misc" / "index_worklist.json"

# Explicit NSE-Indices `Industry` -> house `sector_classification` map. The
# exchange vocabulary is coarser/different (raw values stay in the sidecar);
# this only fills companies whose sector_classification is still empty, and
# unmapped labels are parked in the worklist, never silently dropped.
INDUSTRY_TO_SECTOR: dict[str, str] = {
    "Automobile and Auto Components": "Automotive",
    "Capital Goods": "Engineering_Capital_Goods",
    "Chemicals": "Chemicals",
    "Construction": "Infrastructure",
    "Construction Materials": "Building_Materials",
    "Consumer Durables": "Consumer",
    "Consumer Services": "Consumer",
    "Diversified": "Diversified",
    "Fast Moving Consumer Goods": "FMCG",
    "Financial Services": "Financial_Services",
    "Forest Materials": "Packaging",
    "Healthcare": "Healthcare",
    "Information Technology": "Technology",
    "Media Entertainment & Publication": "Media_Entertainment",
    "Metals & Mining": "Metals",
    "Oil Gas & Consumable Fuels": "Energy",
    "Oil, Gas & Consumable Fuels": "Energy",
    "Power": "Energy",
    "Realty": "Real_Estate",
    "Telecommunication": "Telecommunications",
    "Textiles": "Textiles",
}


def load_constituents(db: Path = SOURCES_DB) -> list[dict]:
    """Read the latest constituent vintage per index from the sidecar.

    Returns [] when the sidecar/table/view is absent (fresh clone) so the
    derive reports cleanly instead of crashing.
    """
    if not db.exists():
        return []
    con = ds.connect_shared(str(db), read_only=True)
    try:
        present = con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'index_constituents'"
        ).fetchone()
        if not present:
            return []
        rows = con.execute(
            "SELECT index_name, index_slug, symbol, isin, company_name, industry, as_of "
            "FROM vw_index_constituent ORDER BY index_name, symbol"
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "index_name": r[0],
            "index_slug": r[1],
            "symbol": r[2],
            "isin": r[3],
            "company_name": r[4],
            "industry": r[5],
            "as_of": r[6].isoformat() if r[6] is not None else None,
        }
        for r in rows
    ]


def _symbol_key(ticker: str | None) -> str | None:
    """``RELIANCE.NS`` / ``RELIANCE.BO`` -> ``RELIANCE`` (the NSE symbol)."""
    if not ticker:
        return None
    return ticker.split(".", 1)[0].strip().upper() or None


def _build_resolver(entities: list[tuple], exchange_rows: list[tuple]):
    """Build a ``(symbol, isin) -> company name`` resolver.

    ``entities``: ``(name, entity_type, ticker, sector_classification)``;
    ``exchange_rows``: ``(isin, symbol, exchange)``. Precedence: ISIN via
    ``exchange_listings`` (NSE row wins) -> NSE symbol via ``entities.ticker``
    (``SYMBOL`` or ``SYMBOL.NS``). Never fuzzy name matching (#218).
    """
    by_ticker: dict[str, str] = {}
    for name, etype, ticker, _sector in entities:
        if etype != "company" or not ticker:
            continue
        by_ticker[ticker.strip().upper()] = name
        key = _symbol_key(ticker)
        if key:
            by_ticker.setdefault(key, name)
    isin_to_symbol: dict[str, str] = {}
    for isin, symbol, exchange in exchange_rows:
        if not isin or not symbol:
            continue
        if isin not in isin_to_symbol or (exchange or "").upper() == "NSE":
            isin_to_symbol[isin.strip().upper()] = symbol.strip().upper()

    def company_for(symbol: str, isin: str) -> str | None:
        if isin:
            via_isin = isin_to_symbol.get(isin.strip().upper())
            if via_isin and via_isin in by_ticker:
                return by_ticker[via_isin]
        key = symbol.strip().upper()
        return by_ticker.get(key)

    return company_for


#: Safety valve for :func:`plan_close` — never close more than this many edges in
#: one pass without an explicit ``--max-close``. A sidecar refresh that suddenly
#: "departs" a large slice of the graph is far more likely a resolver or vintage
#: problem than a real reconstitution, so the CLI aborts loudly instead.
DEFAULT_MAX_CLOSE = 5000


def current_pairs(constituents: list[dict]) -> set[tuple[str, str]]:
    """``(symbol, canonical index)`` pairs the sidecar currently asserts.

    A constituent with a NULL ``as_of`` is stale metadata, not an absent
    member — it still counts as present, so a dateless row can never be
    closed by a vintage sweep.
    """
    pairs: set[tuple[str, str]] = set()
    for c in constituents:
        index = INDEX_NAME_CANONICAL.get(c["index_name"], c["index_name"])
        symbol = (c["symbol"] or "").strip().upper()
        if index and symbol:
            pairs.add((symbol, index))
    return pairs


def plan_close(  # noqa: C901 — vintage close planning is one pass over the census; splitting hides the order
    constituents: list[dict],
    *,
    conn=None,
    symbols: set[str] | None = None,
) -> list[tuple[str, str, str, str]]:
    """Plan which ``listed_on_index`` edges a reconstitution superseded.

    An edge is a close candidate when the sidecar no longer asserts the
    ``(symbol, canonical index)`` pair. Sidecar identity is the symbol in
    ``properties``, and that symbol must still exist somewhere in the
    sidecar — a row the sidecar never described (foreign derivation) is out
    of scope and never closed. An edge whose symbol moved to another index
    survives here: it is re-derived against the new index rather than being
    closed and reborn.

    ``symbols`` restricts the sweep to a set of sidecar symbols — the safe
    mode when the caller only re-derived part of the graph.

    Returns ``(source, target, symbol, close_on)`` where ``close_on`` is the
    newest sidecar vintage for that index (the date the departure became
    effective), or ``''`` when the index has no dated vintage.
    """
    pairs = current_pairs(constituents)
    latest: dict[str, str] = {}
    for c in constituents:
        if not c["as_of"] or not c["index_name"]:
            continue
        name = INDEX_NAME_CANONICAL.get(c["index_name"], c["index_name"])
        if c["as_of"] > latest.get(name, ""):
            latest[name] = c["as_of"]
    sidecar_symbols = {(c["symbol"] or "").strip().upper() for c in constituents}
    sidecar_symbols.discard("")
    own = conn is None
    if own:
        conn = connect()
    try:
        rows = conn.execute(
            "SELECT source, target, valid_from, properties FROM graph_edges "
            "WHERE edge_type = ? ORDER BY target, source",
            (EDGE_TYPE,),
        ).fetchall()
    finally:
        if own:
            conn.close()
    out: list[tuple[str, str, str, str]] = []
    for source, target, valid_from, props in rows:
        try:
            symbol = (json.loads(props).get("symbol") or "").strip().upper()
        except TypeError, ValueError:
            symbol = ""
        if not symbol or symbol not in sidecar_symbols:
            continue
        if symbols is not None and symbol not in symbols:
            continue
        if (symbol, target) in pairs:
            continue
        out.append((source, target, symbol, latest.get(target) or valid_from or ""))
    return out


def close_superseded(
    candidates: list[tuple[str, str, str, str]],
    *,
    conn=None,
    apply: bool = True,
) -> int:
    """Set ``valid_to`` on edges a reconstitution superseded.

    ``valid_to`` is the EXCLUSIVE end of the window (``as_of.py``: visible at
    D iff ``valid_from <= D < valid_to``). The source CSVs publish only the
    NEW vintage, never the day a name left, so ``valid_to`` is set to the new
    vintage date: the departed member stays visible through the day before
    the reconstitution and disappears from it onward. Re-adding the symbol
    later re-opens the row (``valid_to = NULL``) instead of inserting a
    second one, because ``UNIQUE(source, target, edge_type)`` leaves no room
    for a duplicate.

    Idempotent: a row already closed at the same date is not touched again.
    Returns rows closed (dry-run count when ``apply=False``).
    """
    own = conn is None
    if own:
        conn = connect()
    closed = 0
    try:
        for source, target, _symbol, close_on in candidates:
            if not close_on:
                continue
            if apply:
                cur = conn.execute(
                    "UPDATE graph_edges SET valid_to = ? "
                    "WHERE edge_type = ? AND source = ? AND target = ? "
                    "AND (valid_to IS NULL OR valid_to <> ?)",
                    (close_on, EDGE_TYPE, source, target, close_on),
                )
                closed += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            else:
                row = conn.execute(
                    "SELECT valid_to FROM graph_edges WHERE edge_type = ? "
                    "AND source = ? AND target = ?",
                    (EDGE_TYPE, source, target),
                ).fetchone()
                if row is not None and row[0] != close_on:
                    closed += 1
        if apply:
            conn.commit()
    finally:
        if own:
            conn.close()
    return closed


#: Global (cross-index) safety valve for :func:`reopen_current`. Re-adding a
#: symbol that left one index and joined another would otherwise re-open a
#: closed membership edge; a symbol moving between thousands of indices is a
#: resolver bug, not data.
DEFAULT_MAX_REOPEN = 5000

#: A closed membership edge the sidecar still asserts is a REVIVAL, not a
#: departure. Nothing in the derive CLI calls :func:`reopen_current`, so without
#: this guard such a row silently stays closed: it disappears from the graph
#: while the exchange list still carries the name. Anything above this many rows
#: means the close and reopen lanes disagree about a whole vintage — a resolver
#: or vintage problem, not data (same class as DEFAULT_MAX_CLOSE).
DEFAULT_MAX_REVIVAL = 5000

#: Candidates one index may contribute before :func:`audit_prune_plan` escalates
#: a WARN to a named single-index suspect. 250 is the smallest full NSE index
#: (Microcap 250 / Smallcap 250), so a real reconstitution of one index lands at
#: or under it; the 2026-11-13/14 event peaked at 31.
INDEX_CLOSE_WARN = 250

#: How many company names to sample per index in the advisory line. The full
#: roster stays one query away; a 250-row index would otherwise print a wall of
#: tickers nobody reads.
CLOSE_SAMPLE = 5


@dataclass
class PruneAudit:
    """Read-only report on what a prune sweep WOULD write.

    Three counters answer the three questions the plain 'pruned N' line cannot:
    how many rows change (``would_write``), how many are already closed at the
    same date (``already_closed`` — non-zero on an unchanged sidecar means the
    previous run already pruned, so re-printing the same count is not progress),
    and how many the writer refused for want of a date (``dateless`` — the silent
    no-op class: ``plan_close`` returns a candidate, ``close_superseded`` skips
    it, and the two counts disagree with nothing printed in between).

    ``by_index`` keeps the ``(total, dateless, sample symbols, close_on)``
    breakdown so the advisory can name the index a mass closure came from.
    ``by_date`` maps close date -> row count, the fastest way to tell a
    single-vintage event (all rows one date) from a resolver fault (dates
    scattered, or empty).
    """

    candidates: int = 0
    would_write: int = 0
    already_closed: int = 0
    dateless: int = 0
    by_index: dict[str, tuple[int, int, list[str], str]] = field(default_factory=dict)
    by_date: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: int = 0


def audit_prune_plan(  # noqa: C901 — the audit mirrors close_superseded decision-by-decision
    candidates: list[tuple[str, str, str, str]],
    *,
    conn=None,
) -> PruneAudit:
    """Report what :func:`close_superseded` would write, without writing it.

    Answers the question a re-run cannot: 'is this prune about to do anything?'.
    Read-only — it never passes ``apply=True`` to the writer, so the only way a
    row counts as ``already_closed`` is that it already carries this close date.

    Advisory only: never raises, never fails a gate. ``warnings`` is the
    operator-facing list (single-index mass closure, undated candidates, an
    empty plan); ``errors`` counts conditions that make the plan untrustworthy
    rather than merely notable.
    """
    out = PruneAudit(candidates=len(candidates))
    own = conn is None
    if own:
        conn = connect()
    try:
        for source, target, _symbol, close_on in candidates:
            row = conn.execute(
                "SELECT valid_to FROM graph_edges WHERE edge_type = ? "
                "AND source = ? AND target = ?",
                (EDGE_TYPE, source, target),
            ).fetchone()
            if row is None:
                continue
            if not close_on:
                out.dateless += 1
                total, dateless, sample, _on = out.by_index.get(target, (0, 0, [], close_on))
                out.by_index[target] = (total + 1, dateless + 1, sample, close_on)
                continue
            if row[0] is not None and row[0] == close_on:
                out.already_closed += 1
                total, dateless, sample, _on = out.by_index.get(target, (0, 0, [], close_on))
                out.by_index[target] = (total + 1, dateless, sample, close_on)
                out.by_date[close_on] = out.by_date.get(close_on, 0) + 1
                continue
            out.would_write += 1
            total, dateless, sample, _on = out.by_index.get(target, (0, 0, [], close_on))
            if len(sample) < CLOSE_SAMPLE:
                sample = [*sample, source]
            out.by_index[target] = (total + 1, dateless, sample, close_on)
            out.by_date[close_on] = out.by_date.get(close_on, 0) + 1
    finally:
        if own:
            conn.close()

    for index, (total, _dateless, _sample, close_on) in sorted(out.by_index.items()):
        if total > INDEX_CLOSE_WARN:
            out.warnings.append(
                f"{index}: {total} edges would close (> {INDEX_CLOSE_WARN}) — "
                "verify this is a real reconstitution, not a resolver/vintage fault"
            )
        if not close_on:
            out.warnings.append(f"{index}: no dated vintage — closure cannot be stamped")
    if out.dateless:
        out.warnings.append(
            f"{out.dateless} candidates carry no close date and will be skipped by "
            "close_superseded (plan and write counts will disagree)"
        )
    if out.candidates == 0:
        out.warnings.append("plan is empty — nothing to close")
    return out


def render_prune_advisory(
    audit: PruneAudit,
    *,
    revival: int = 0,
    close_n: int | None = None,
) -> list[str]:
    """Render :func:`audit_prune_plan` as operator-facing advisory lines.

    Printed by the derive CLI on every run that touches the prune lane, and
    reusable by the integrity checker so ``make qa`` shows the same numbers
    without re-deriving them. Shape mirrors the validity-window advisory:
    one line per index that closes rows, then a flat WARNINGS block.

    ``close_n`` is the writer's own rowcount when it ran; passing it lets the
    advisory call out the silent no-op class explicitly — a plan of N with a
    write of 0 is otherwise indistinguishable from 'nothing to do'.
    """
    lines: list[str] = []
    top = sorted(audit.by_index.items(), key=lambda kv: (-kv[1][0], kv[0]))
    for index, (total, dateless, sample, close_on) in top:
        detail = f"{total} edges  close_on={close_on or 'NONE'}"
        if dateless:
            detail += f"  ({dateless} undated)"
        if sample:
            detail += "  e.g. " + ", ".join(sample)
        lines.append(f"  {index:38} {detail}")
    lines.append(
        f"  prune plan: {audit.candidates} candidates -> {audit.would_write} would write, "
        f"{audit.already_closed} already closed at that date, {audit.dateless} undated"
    )
    if revival:
        lines.append(
            f"  revival: {revival} closed rows are asserted again by the sidecar "
            "(reopen_current is not wired into the CLI — these stay closed)"
        )
    if close_n is not None and audit.candidates and close_n != audit.would_write:
        lines.append(
            f"  WARNING wrote {close_n} rows against a plan of {audit.would_write} "
            "— the prune was refused or re-counted; see the counters above"
        )
    for message in audit.warnings:
        lines.append(f"  WARNING {message}")
    return lines


def format_prune_advisory(
    audit: PruneAudit,
    *,
    revival: int = 0,
    close_n: int | None = None,
) -> str:
    """One-block text form of :func:`render_prune_advisory` (report/README use)."""
    return "\n".join(render_prune_advisory(audit, revival=revival, close_n=close_n))


def latest_vintages(constituents: list[dict]) -> dict[str, str]:
    """Newest sidecar `as_of` per canonical index name (empty when undated)."""
    latest: dict[str, str] = {}
    for c in constituents:
        if not c["as_of"] or not c["index_name"]:
            continue
        name = INDEX_NAME_CANONICAL.get(c["index_name"], c["index_name"])
        if c["as_of"] > latest.get(name, ""):
            latest[name] = c["as_of"]
    return latest


def plan_revival(
    constituents: list[dict],
    *,
    conn=None,
    symbols: set[str] | None = None,
) -> list[tuple[str, str, str, str]]:
    """Plan which closed ``listed_on_index`` edges the sidecar now RE-ASSERTS.

    The mirror of :func:`plan_close`, and the check that lane never had: a row
    closed by a prune is only correct while the sidecar still omits it. When a
    later vintage re-admits the name, :func:`reopen_current` is the only writer
    that can revive it (``UNIQUE(source, target, edge_type)`` leaves no room for
    a second row) — and nothing calls it from the CLI, so the edge stays closed
    and the company silently loses an index membership it really holds.

    Returns ``(source, target, symbol, reopen_on)`` where ``reopen_on`` is the
    newest sidecar vintage for that index, or ``''`` when the index is undated.
    """
    pairs = current_pairs(constituents)
    latest = latest_vintages(constituents)
    own = conn is None
    if own:
        conn = connect()
    try:
        rows = conn.execute(
            "SELECT source, target, properties FROM graph_edges "
            "WHERE edge_type = ? AND valid_to IS NOT NULL ORDER BY target, source",
            (EDGE_TYPE,),
        ).fetchall()
    finally:
        if own:
            conn.close()
    out: list[tuple[str, str, str, str]] = []
    for source, target, props in rows:
        try:
            symbol = (json.loads(props).get("symbol") or "").strip().upper()
        except TypeError, ValueError:
            symbol = ""
        if not symbol:
            continue
        if symbols is not None and symbol not in symbols:
            continue
        if (symbol, target) not in pairs:
            continue
        out.append((source, target, symbol, latest.get(target) or ""))
    return out


def reopen_current(  # noqa: C901 — revival mirrors plan_close; the symmetry IS the contract
    constituents: list[dict],
    *,
    conn=None,
    apply: bool = True,
    symbols: set[str] | None = None,
    max_reopen: int = DEFAULT_MAX_REOPEN,
) -> int:
    """Re-open ``listed_on_index`` rows the sidecar still asserts (``valid_to = NULL``).

    A closed row keeps its identity (``UNIQUE(source, target, edge_type)`` has
    no room for a second one), so a symbol that returns to an index is revived
    by clearing ``valid_to`` instead of by inserting a duplicate.

    A row is re-opened only when the sidecar currently asserts the pair.
    ``symbols`` restricts the sweep (safe partial re-derive).

    A returning symbol keeps the row's identity (``UNIQUE(source, target,
    edge_type)`` leaves no room for a second one), so the revival also moves
    the row's vintage stamp: ``properties.as_of`` and ``valid_from`` become
    the newest sidecar vintage for that index, describing current membership
    instead of the window that closed.

    Returns rows re-opened (dry-run count when ``apply=False``).
    """
    own = conn is None
    if own:
        conn = connect()
    reopened = 0
    pairs = current_pairs(constituents)
    latest = latest_vintages(constituents)
    try:
        rows = conn.execute(
            "SELECT source, target, properties FROM graph_edges "
            "WHERE edge_type = ? AND valid_to IS NOT NULL",
            (EDGE_TYPE,),
        ).fetchall()
        for source, target, props in rows:
            try:
                symbol = (json.loads(props).get("symbol") or "").strip().upper()
            except TypeError, ValueError:
                continue
            if not symbol or (symbols is not None and symbol not in symbols):
                continue
            if (symbol, target) not in pairs:
                continue
            reopen_on = latest.get(target, "")
            reopened += 1
            if reopened > max_reopen:
                raise RuntimeError(
                    f"reopen_current would re-open {reopened} edges (> {max_reopen}); "
                    "refusing — check the resolver / sidecar vintage"
                )
            if apply:
                if reopen_on:
                    conn.execute(
                        "UPDATE graph_edges "
                        "SET valid_to = NULL, valid_from = ?, "
                        "properties = json_set(properties, '$.as_of', ?) "
                        "WHERE edge_type = ? AND source = ? AND target = ?",
                        (reopen_on, reopen_on, EDGE_TYPE, source, target),
                    )
                else:
                    conn.execute(
                        "UPDATE graph_edges SET valid_to = NULL "
                        "WHERE edge_type = ? AND source = ? AND target = ?",
                        (EDGE_TYPE, source, target),
                    )
        if apply:
            conn.commit()
    finally:
        if own:
            conn.close()
    return reopened


def backfill_validity(*, conn=None) -> int:
    """Set ``listed_on_index.valid_from`` from ``properties.as_of`` (S3).

    Idempotent UPDATE; leaves rows without an as_of untouched. Mirrors the
    invested_in in-tree pattern (filing date as validity start).
    """
    own = conn is None
    if own:
        from helpers.core.db import connect  # noqa: PLC0415

        conn = connect()
    try:
        cur = conn.execute(
            "UPDATE graph_edges SET valid_from = json_extract(properties, '$.as_of') "  # noqa: S608  # literal schema identifiers only
            "WHERE edge_type = 'listed_on_index' "
            "AND json_extract(properties, '$.as_of') IS NOT NULL "
            "AND (valid_from IS NULL OR valid_from != json_extract(properties, '$.as_of'))"
        )
        n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        if own:
            conn.commit()
        return n
    finally:
        if own:
            conn.close()


def resolve(
    constituents: list[dict], entities: list[tuple], exchange_rows: list[tuple]
) -> tuple[list[tuple], list[str], list[dict], dict[str, list[str]]]:
    """Resolve constituent rows to companies.

    ``entities``: ``(name, entity_type, ticker, sector_classification)``.
    ``exchange_rows``: ``(isin, symbol, exchange)``.

    Returns ``(edges, index_names, worklist, industry_map)`` where edges are
    ``(company, index, properties, source_ref)`` tuples for apply_typed_edges
    and ``industry_map`` maps industry label -> resolved company names.

    Index aliases (NIFTY 500 ≡ Nifty500 Multicap 50-25-25 Index ≡
    Nifty500 LargeMidSmall Equal-Cap Weighted — identical constituent sets,
    validated 2026-11-13) fold onto the canonical name so derivation never
    re-creates duplicate index entities/edges (index_label_dedup).
    """
    company_for = _build_resolver(entities, exchange_rows)

    edges: list[tuple] = []
    index_names: set[str] = set()
    worklist: list[dict] = []
    industry_map: dict[str, list[str]] = {}
    for c in constituents:
        index = INDEX_NAME_CANONICAL.get(c["index_name"], c["index_name"])
        if not index:
            continue
        index_names.add(index)
        company = company_for(c["symbol"] or "", c["isin"] or "")
        props = {
            "symbol": c["symbol"],
            "isin": c["isin"],
            "as_of": c["as_of"],
            "index_slug": c["index_slug"],
        }
        if company is None:
            worklist.append(
                {
                    "symbol": c["symbol"],
                    "isin": c["isin"],
                    "company_name": c["company_name"],
                    "index": index,
                    "industry": c["industry"],
                    "reason": "no_entity",
                }
            )
            continue
        edges.append((company, index, props, SOURCE_REF))
        if c["industry"]:
            industry_map.setdefault(c["industry"], []).append(company)
    return edges, sorted(index_names), worklist, industry_map


def normalized_name(name: str) -> str:
    """Vault ``normalized_name`` contract (``^[A-Za-z0-9][A-Za-z0-9_]*$``):
    runs of non-alphanumerics collapse to a single ``_``; edge underscores
    trimmed. Same rule as ``exchange_sync.normalized_name`` (index display
    names carry spaces, ``&`` and ``/``)."""
    return re.sub(r"[^A-Za-z0-9]+", "_", (name or "").strip()).strip("_")


def create_index_entities(conn, names, *, apply: bool = True) -> int:
    """Insert a fileless ``index`` entity row per index name (idempotent).

    Bare structural rows like countries/themes — no file_path, no note.
    Existing index rows get their ``normalized_name`` repaired (a first run
    stamped the raw display name). Returns rows inserted (dry-run count when
    apply=False).
    """
    if not apply:
        return len(names)
    now = utc_now()
    inserted = 0
    with conn:
        for name in names:
            nn = normalized_name(name)
            cur = conn.execute(
                "INSERT OR IGNORE INTO entities "
                "(name, entity_type, normalized_name, last_updated) "
                "VALUES (?, 'index', ?, ?)",
                (name, nn, now),
            )
            inserted += cur.rowcount
            conn.execute(
                "UPDATE entities SET normalized_name = ? "
                "WHERE name = ? AND entity_type = 'index' AND IFNULL(normalized_name, '') <> ?",
                (nn, name, nn),
            )
    return inserted


def converge_industry(conn, industry_map: dict[str, list[str]], *, apply: bool = True) -> tuple:
    """Fill empty ``sector_classification`` from the explicit map.

    Only NULL/empty fields are written (authored sectors never clobbered).
    Returns ``(filled, unmapped_labels)`` where unmapped_labels is a sorted
    dict of label -> [company, ...] for the worklist.
    """
    unmapped: dict[str, list[str]] = {}
    planned = 0
    filled = 0
    with conn:
        for label, companies in sorted(industry_map.items()):
            sector = INDUSTRY_TO_SECTOR.get(label)
            if sector is None:
                unmapped[label] = sorted(set(companies))
                continue
            for name in sorted(set(companies)):
                row = conn.execute(
                    "SELECT sector_classification FROM entities WHERE name = ?", (name,)
                ).fetchone()
                if row is None or row[0]:
                    continue  # absent or already classified — never clobber
                planned += 1
                if not apply:
                    continue
                cur = conn.execute(
                    "UPDATE entities SET sector_classification = ?, last_updated = ? "
                    "WHERE name = ? AND (sector_classification IS NULL OR sector_classification = '')",
                    (sector, utc_now(), name),
                )
                filled += cur.rowcount
    return (filled if apply else planned), unmapped


def write_worklist(worklist: list, unmapped: dict[str, list[str]], path: Path = WORKLIST) -> int:
    """Persist unresolved constituents + unmapped industry labels.

    Same git-tracked operator surface as ``country_worklist.json``.
    """
    payload = {
        "generated": utc_now(),
        "count": len(worklist),
        "constituents": sorted(worklist, key=lambda w: (w["index"], w["symbol"] or "")),
        "unmapped_industries": {
            label: sorted(set(companies)) for label, companies in sorted(unmapped.items())
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return len(worklist)


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
_ARGS = dcli.DeriveArgsSpec(
    apply_help="Write index entities + listed_on_index edges + converged sectors + worklist "
    "(default: dry-run summary).",
    stale_help="No-op for indices: the source is the sidecar (index_sync), "
    "so there is no note-corpus watch path to gate on.",
    corpus=False,
)


def _cli(argv: list[str] | None = None) -> int:  # noqa: C901 — argparse wiring + per-phase reporting
    p = argparse.ArgumentParser(
        description="Derive listed_on_index (company -> index) edges from NSE constituents.",
    )
    dcli.add_derive_args(p, _ARGS)
    p.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite target (default: live research.db; use for the ontology-gate parent/candidate copies)",
    )
    p.add_argument(
        "--prune-absent",
        action="store_true",
        help="close (valid_to) listed_on_index edges the sidecar no longer asserts — "
        "reconstitution sweep; requires --apply",
    )
    p.add_argument(
        "--max-close",
        type=int,
        default=DEFAULT_MAX_CLOSE,
        help=f"abort the prune sweep above this many edges (default: {DEFAULT_MAX_CLOSE})",
    )
    args = p.parse_args(argv)

    constituents = load_constituents()
    if not constituents:
        print("index_constituents empty/absent — run `make refresh-indices --apply` first")
        return 0

    conn = connect(args.db or DEFAULT_DB_PATH)
    try:
        entities = conn.execute(
            "SELECT name, entity_type, ticker, sector_classification FROM entities ORDER BY name"
        ).fetchall()
    except Exception:  # noqa: BLE001 — minimal schemas may lack sector_classification
        entities = [
            (r[0], r[1], r[2], None)
            for r in conn.execute(
                "SELECT name, entity_type, ticker FROM entities ORDER BY name"
            ).fetchall()
        ]
    con = ds.connect_shared(str(SOURCES_DB), read_only=True)
    try:
        exchange_rows = con.execute(
            "SELECT isin, symbol, exchange FROM exchange_listings WHERE isin IS NOT NULL"
        ).fetchall()
    finally:
        con.close()

    edges, index_names, worklist, industry_map = resolve(constituents, entities, exchange_rows)
    mode = "apply" if args.apply else "dry-run"
    print(
        f"constituents={len(constituents)} indices={len(index_names)} "
        f"resolved={len(edges)} worklist={len(worklist)} ({mode})",
        file=sys.stderr,
    )
    try:
        ent_inserted = create_index_entities(conn, index_names, apply=args.apply)
        edge_inserted = apply_typed_edges(
            edges, edge_type=EDGE_TYPE, symmetric=0, conn=conn, dry_run=not args.apply
        )
        # Vintage sweep: a constituent that disappeared from the sidecar is a
        # departure, not a no-op. Close its edge at the new vintage so the graph
        # tracks the CURRENT membership instead of accumulating union-of-vintages.
        close_n = 0
        if args.prune_absent:
            if not args.apply:
                print(
                    "  --prune-absent requires --apply (dry-run cannot close edges)",
                    file=sys.stderr,
                )
            else:
                # Only the symbols this run actually resolved are safe to close
                # against: an unresolved constituent has no graph identity.
                # The sidecar symbol is the ONLY identity in properties — resolve()
                # appends a 4-tuple whose source_ref slot is SOURCE_REF, not a symbol.
                resolved_symbols = {e[2]["symbol"].upper() for e in edges if e[2].get("symbol")}
                candidates = plan_close(constituents, conn=conn, symbols=resolved_symbols)
                if len(candidates) > args.max_close:
                    print(
                        f"  ABORT --prune-absent: {len(candidates)} edges would close "
                        f"(> --max-close {args.max_close}); refusing — inspect the sidecar "
                        "vintage/resolver before pruning",
                        file=sys.stderr,
                    )
                else:
                    # Audit BEFORE writing: the counters separate a real
                    # reconstitution from an already-pruned sidecar, a refused
                    # write, and a resolver fault — the plain rowcount cannot.
                    audit = audit_prune_plan(candidates, conn=conn)
                    close_n = close_superseded(candidates, conn=conn, apply=True)
                    for line in render_prune_advisory(audit, revival=0, close_n=close_n):
                        print(line, file=sys.stderr)
                    backfilled = backfill_validity(conn=conn)
                    print(
                        f"  pruned {close_n} departed membership edges "
                        f"(valid_to = new vintage); backfilled {backfilled} valid_from",
                        file=sys.stderr,
                    )
        conv, unmapped = converge_industry(conn, industry_map, apply=args.apply)
        action = "inserted" if args.apply else "would insert"
        print(
            f"{ent_inserted} index entities {action}; {edge_inserted} {EDGE_TYPE} edges {action}; "
            f"{conv} sector_classification {'filled' if args.apply else 'would fill'}.",
            file=sys.stderr,
        )
        if unmapped:
            print(
                "  WARNING unmapped industry labels parked: "
                + ", ".join(f"{k} ({len(v)})" for k, v in sorted(unmapped.items())),
                file=sys.stderr,
            )
        if args.apply and args.db is None:
            write_worklist(worklist, unmapped)
            print(f"worklist written: {WORKLIST} ({len(worklist)})", file=sys.stderr)
        elif args.apply:
            print(
                "  --db target: live worklist NOT written (disposable copy run)",
                file=sys.stderr,
            )
        if args.verbose:
            dcli.dump_edges_verbose(edges)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(_cli())
