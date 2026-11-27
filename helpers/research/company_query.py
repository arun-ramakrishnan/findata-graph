#!/usr/bin/env python3
"""company_query — read-only composite company analytics over the repo stores.

One query string -> typed target -> entity set that every verb accepts:
``snapshot`` (one entity, 14 sections), ``group`` (three labeled rosters +
bounded relations), ``bulk`` (any resolved set, JSONL/CSV) and ``digest``
(typed-key digest JSON for the opt-in master_query ``company`` leg).

Sources: the DuckDB snapshot parquet mounted via ``connect_snapshots()``
(`<side>__<table>` views) with a live-first attach of
``memory/research.duckdb``, ``memory/data/sources.duckdb`` and
``memory/graph.duckdb``. A store served on the quack wire routes through
``connect_shared`` (the wire client); an unserved one attaches read-only
through the duckdb-lock entry points — shared ``io_lock`` +
``connect_with_lock_retry`` around a read-only ``ATTACH`` (lock held around
the open only). The snapshot is the fallback of last resort; every read is
read-only.

Resolution order (proposal "Query resolution"): identifier shape (ticker,
ISIN, FIGI, CIN — a well-formed but unstored value is "not in store", it
never falls through to name matching) -> bare NSE symbol in ALL CAPS
(ticker stem) -> taxonomy label (sector/super_sector/sub_sector,
normalized; ``--as`` resolves the two-level collision) -> family label ->
relation phrase (closed grammar, routes to ``group``) -> plain name
(exact -> normalized -> suffix-stripped -> abbreviation/alias; then
ranked candidates, never auto-picked).

Usage:
    python3 helpers/research/company_query.py snapshot SBIN.NS
    python3 helpers/research/company_query.py snapshot "State Bank of India"
    python3 helpers/research/company_query.py group Tata --depth 2
    python3 helpers/research/company_query.py bulk Banking --format csv
    python3 helpers/research/company_query.py bulk Banking --summary
    python3 helpers/research/company_query.py digest SBIN.NS --json

Exit codes: 0 resolved (any outcome incl. empty sets); 1 unresolved or
candidates (a person must pick); 2 usage error.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.duckdb_shared import connect_shared, is_served  # noqa: E402
from helpers.maintenance.analytics.query import connect_snapshots  # noqa: E402

MAX_CANDIDATES = 10
DEFAULT_BULK_FIELDS = "entity,identifiers,figi,index_membership"
SECTION_ORDER: tuple[str, ...] = (
    "entity",
    "identifiers",
    "centrality",
    "graph_analytics",
    "edge_counts",
    "company_metrics",
    "listings",
    "bhavcopy_recent",
    "rpt_summary",
    "shareholding_recent",
    "credit_ratings",
    "index_membership",
    "mca_cin",
    "figi",
)

# Identifier shape tests (key 1). Order = attempt order. CIN: U/L + 5 digits
# + 2-letter state + 4-digit year + 3-letter class + 6-digit number.
_TICKER_RE = re.compile(r"^[A-Za-z0-9&._-]{1,24}$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_FIGI_RE = re.compile(r"^BBG[0-9A-Z]{9}(-?[0-9A-Z])?$")
_CIN_RE = re.compile(r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$")

# Plain-name normalization (key 6): lowercase, & -> and, strip punctuation.
_NAME_SUFFIXES = (
    "limited",
    "ltd",
    "private",
    "pvt",
    "p",
    "llp",
    "plc",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
)

# Relation grammar (key 5): closed list, lead and tail forms. Verb maps to
# the group-verb relation walkers.
_REL_LEAD = re.compile(
    r"^(?:all\s+)?(subsidiaries|parents?|ultimate parents?|peers|competitors"
    r"|group companies|acquisitions|investments|jv partners?|joint ventures?)"
    r"\s+(?:of|for)\s+(.+)$",
    re.IGNORECASE,
)
_REL_TAIL = re.compile(
    r"^(.+?)\s+(subsidiaries|parents?|ultimate parent|peers|competitors"
    r"|group companies|group|acquisitions)$",
    re.IGNORECASE,
)
_REL_VERB = {
    "subsidiaries": "subsidiaries",
    "subsidiary": "subsidiaries",
    "parent": "parents",
    "parents": "parents",
    "ultimate parent": "parents",
    "ultimate parents": "parents",
    "peers": "peers",
    "competitors": "peers",
    "group": "family",
    "group companies": "family",
    "acquisitions": "acquisitions",
    "investments": "invested",
    "jv partner": "jv",
    "jv partners": "jv",
    "joint venture": "jv",
    "joint ventures": "jv",
}

DEFAULT_RELATIONS = ("subsidiaries", "peers")
KNOWN_RELATIONS = ("subsidiaries", "peers", "parents", "acquisitions", "invested", "jv")


class Unresolved(Exception):
    """The query did not resolve decisively (unresolved / candidates)."""


@dataclass
class Target:
    """What one query string resolved to (proposal four outcomes + relation)."""

    kind: str  # "one" | "set" | "candidates" | "unresolved" | "relation"
    key: str  # resolver key that matched: ticker/isin/figi/cin/symbol/taxonomy/family/relation/name
    entities: list[str] = field(default_factory=list)  # entity names (one/set)
    candidates: list[dict] = field(default_factory=list)
    label: str = ""  # display label (entity name / taxonomy label / family)
    digest_kind: str = ""  # "entity" | "set"
    relation: str | None = None  # group-verb relation when kind == "relation"
    note: str = ""  # e.g. "not in store: isin INE999999010101"

    @property
    def resolved(self) -> bool:
        return self.kind in ("one", "set", "relation")


@dataclass
class _Domain:
    """Per-domain access route: snapshot view, live attach, or wire client."""

    side: str  # snapshot dir: sqlite|sources|duckdb
    db: Path | None  # live store (None = snapshot-only domain)
    alias: str  # ATTACH alias for the live store
    mode: str = "snapshot"  # snapshot | live | wire
    wire: Any = None  # served-store client connection (mode == wire)


class Sources:
    """Read-only composite access: snapshot views + live-first attaches."""

    def __init__(self, repo_root: Path | str | None = None):
        self.repo_root = Path(repo_root) if repo_root else _REPO_ROOT
        self.warnings: list[str] = []
        self.con = connect_snapshots(self.repo_root)
        self.domains: dict[str, _Domain] = {
            "research": _Domain("sqlite", Path("memory") / "research.duckdb", "live_research"),
            "sources": _Domain(
                "sources", Path("memory") / "data" / "sources.duckdb", "live_sources"
            ),
            "graph": _Domain("duckdb", Path("memory") / "graph.duckdb", "live_graph"),
        }
        self._tables: dict[str, set[str]] = {}
        self._name_maps: dict[str, dict[str, str]] = {}
        for d in self.domains.values():
            if d.db is not None:
                self._attach_live(d)

    # -- connection plumbing ------------------------------------------------

    def _attach_live(self, d: _Domain) -> None:
        """Live-first attach: served store -> quack wire, else file ATTACH.

        A served store has no attachable file (its server holds it), so it
        routes through ``connect_shared`` as a wire client. An unserved one
        takes the duckdb-lock ladder — shared ``io_lock`` +
        ``connect_with_lock_retry`` around a read-only ``ATTACH``; the lock
        is held around the open only. Every failure degrades to the
        snapshot views with a warning, never crashes."""
        if d.db is None:  # snapshot-only domain
            return
        path = self.repo_root / d.db  # relative: fixture tests point at tmp roots
        if not path.exists():
            self.warnings.append(f"live store missing: {d.db.name} — {d.side}__ views used")
            return
        if is_served(path):
            try:
                d.wire = connect_shared(str(path), read_only=True)
                d.mode = "wire"
            except Exception as exc:  # noqa: BLE001  degrade to snapshot, never crash
                self.warnings.append(
                    f"{d.db.name} served but wire attach failed "
                    f"({type(exc).__name__}) — {d.side}__ views used"
                )
            return
        from helpers.misc.duckdb_lock import (
            _EXTENDED_RETRY_DELAYS_S,
            connect_with_lock_retry,
            io_lock,
        )

        try:
            with io_lock(path, exclusive=False):
                connect_with_lock_retry(
                    lambda: self.con.execute(  # noqa: S608  alias/path are module constants
                        f"ATTACH '{path.as_posix()}' AS {d.alias} (READ_ONLY)"
                    ),
                    delays=_EXTENDED_RETRY_DELAYS_S,
                )
            d.mode = "live"
        except Exception as exc:  # noqa: BLE001  degrade to snapshot, never crash
            self.warnings.append(
                f"{d.db.name} unavailable ({type(exc).__name__}) — snapshot views used"
            )

    def close(self) -> None:
        for d in self.domains.values():
            if d.wire is not None:
                d.wire.close()
                d.wire = None
        self.con.close()

    # -- table access --------------------------------------------------------

    def _domain_tables(self, domain: str) -> set[str]:
        cached = self._tables.get(domain)
        if cached is not None:
            return cached
        d = self.domains[domain]
        if d.mode == "snapshot":
            names = {r[0] for r in self.con.execute("SHOW TABLES").fetchall()}
            names = {n for n in names if n.startswith(f"{d.side}__")}
        elif d.mode == "live":
            q = (
                "SELECT table_name FROM duckdb_tables() WHERE database_name = ? "
                "UNION SELECT view_name FROM duckdb_views() WHERE database_name = ?"
            )
            names = {r[0] for r in self.con.execute(q, [d.alias, d.alias]).fetchall()}
        else:  # wire
            names = {r[0] for r in d.wire.execute("SHOW TABLES").fetchall()}
        self._tables[domain] = names
        return names

    def has(self, domain: str, table: str) -> bool:
        if domain not in self.domains:
            return False
        d = self.domains[domain]
        name = f"{d.side}__{table}" if d.mode == "snapshot" else table
        return name in self._domain_tables(domain)

    def fetch(
        self,
        domain: str,
        table: str,
        sql: str,
        params: list | None = None,
        table2: str | None = None,
    ) -> list[tuple]:
        """Run one section query; ``{t}``/``{t2}`` render to the qualified
        tables, ``{v}`` to the domain-qualified ``v_node`` (ID->name map —
        several queries join it twice under different aliases)."""
        d = self.domains[domain]
        if d.mode == "wire":
            sql = sql.format(t=table, t2=table2 or table, v="v_node")
            return d.wire.execute(sql, params or []).fetchall()
        qual = f"{d.side}__{table}" if d.mode == "snapshot" else f"{d.alias}.{table}"
        vqual = f"{d.side}__v_node" if d.mode == "snapshot" else f"{d.alias}.v_node"
        sql = sql.replace("{t}", qual)
        sql = sql.replace("{v}", vqual)
        if table2 is not None and "{t2}" in sql:
            sql = sql.replace(
                "{t2}", f"{d.side}__{table2}" if d.mode == "snapshot" else f"{d.alias}.{table2}"
            )
        return self.con.execute(sql, params or []).fetchall()

    # -- name maps (plain-name tiers, built lazily once per process) ---------

    def _company_names(self) -> list[str]:
        return [
            r[0]
            for r in self.fetch(
                "research", "entities", "SELECT name FROM {t} WHERE entity_type = 'company'"
            )
        ]

    def name_map(self) -> dict[str, str]:
        """normalized name -> stored name (companies)."""
        if "norm" not in self._name_maps:
            m: dict[str, str] = {}
            for n in self._company_names():
                m.setdefault(norm_name(n), n)
            self._name_maps["norm"] = m
        return self._name_maps["norm"]

    def suffix_map(self) -> dict[str, str]:
        """suffix-stripped normalized name -> stored name."""
        if "suffix" not in self._name_maps:
            m: dict[str, str] = {}
            for n in self._company_names():
                m.setdefault(strip_suffixes(norm_name(n)), n)
            self._name_maps["suffix"] = m
        return self._name_maps["suffix"]


# -- plain-name normalization -------------------------------------------------


def norm_name(s: str) -> str:
    t = s.lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def strip_suffixes(norm: str) -> str:
    toks = norm.split()
    changed = True
    while changed and len(toks) > 1:
        changed = False
        if toks[-1] in _NAME_SUFFIXES:
            toks.pop()
            changed = True
    return " ".join(toks)


def norm_label(s: str) -> str:
    """Taxonomy label normalization: case + underscores as spaces + _Super tail dropped."""
    lab = re.sub(r"\s+", " ", s.strip().lower().replace("_", " "))
    return lab[:-6].rstrip() if lab.endswith(" super") else lab


# -- resolver -----------------------------------------------------------------


def _resolve_identifier(src: Sources, key_type: str, value: str) -> Target:
    """Key 1: stored-value lookup for one identifier shape."""
    if key_type == "ticker":
        rows = src.fetch(
            "research", "entities", "SELECT name FROM {t} WHERE upper(ticker) = ?", [value.upper()]
        )
    elif key_type == "cin":
        rows = src.fetch(
            "research", "entities", "SELECT name FROM {t} WHERE upper(cin) = ?", [value.upper()]
        )
    else:
        ident = "isin" if key_type == "isin" else "figi"
        rows = src.fetch(
            "research",
            "entity_identifiers",
            "SELECT entity_name FROM {t} WHERE identifier_type = ? AND upper(identifier_value) = ?",
            [ident, value.upper()],
        )
    names = sorted({r[0] for r in rows})
    if not names:
        return Target("unresolved", key_type, note=f"not in store: {key_type} {value}")
    if len(names) == 1:
        return Target("one", key_type, entities=names, label=names[0], digest_kind="entity")
    return Target(
        "candidates",
        key_type,
        candidates=[{"name": n, "reason": f"{key_type} shared"} for n in names][:MAX_CANDIDATES],
        note=f"ambiguous {key_type}: {len(names)} entities",
    )


def _resolve_symbol(src: Sources, q: str) -> Target | None:
    """Key 2: bare NSE symbol in ALL CAPS -> ticker stem."""
    if not (q.isupper() and 1 <= len(q) <= 24 and _TICKER_RE.match(q)):
        return None
    rows = src.fetch(
        "research",
        "entities",
        "SELECT name, ticker FROM {t} WHERE ticker <> '' AND upper(split_part(ticker, '.', 1)) = ?",
        [q],
    )
    names = sorted({r[0] for r in rows})
    if not names:
        return None
    if len(names) == 1:
        return Target("one", "symbol", entities=names, label=names[0], digest_kind="entity")
    return Target(
        "candidates",
        "symbol",
        candidates=[{"name": n, "reason": f"symbol stem {q}"} for n in names][:MAX_CANDIDATES],
        note=f"ambiguous symbol stem {q}: {len(names)} entities",
    )


def _taxonomy_members(src: Sources, node_id: int, kind: str, label: str) -> list[str]:
    """Member company names for one taxonomy node (kind-specific join)."""
    if kind == "sector":
        rows = src.fetch(
            "graph",
            "e_has",
            "SELECT v.name FROM {t} e JOIN {v} v ON v.id = e.company_name WHERE e.sector_name = ?",
            [node_id],
        )
    elif kind == "sub_sector":
        rows = src.fetch(
            "research",
            "hyper_edges",
            "SELECT i.entity_name FROM {t2} i JOIN {t} e ON e.id = i.edge_id "
            "WHERE e.edge_type = 'sub_sector' AND e.label = ?",
            [label],
            table2="hyper_incidences",
        )
        return sorted({r[0] for r in rows})
    else:  # super_sector: sectors under it, then their companies
        rows = src.fetch(
            "graph",
            "e_belongs_to",
            "SELECT c.name FROM {t} t JOIN {v} c ON c.id = t.child_id WHERE t.parent_id = ?",
            [node_id],
        )
        names = [r[0] for r in rows]
        if not names:
            return []
        ph = ",".join("?" * len(names))
        ids = src.fetch(
            "graph",
            "v_node",
            f"SELECT id FROM {{t}} WHERE kind = 'sector' AND name IN ({ph})",  # noqa: S608  ph ?-builder
            names,
        )
        out: list[str] = []
        for (sector_id,) in ids:
            out.extend(_taxonomy_members(src, sector_id, "sector", ""))
        return sorted(set(out))
    return sorted({r[0] for r in rows})


def _resolve_taxonomy(src: Sources, q: str, as_level: str | None) -> Target | None:
    """Key 3: sector / super_sector / sub_sector label, normalized."""
    lab = norm_label(q)
    if not lab:
        return None
    rows = src.fetch(
        "graph",
        "v_node",
        "SELECT id, name, kind FROM {t} WHERE kind IN "
        "('sector', 'super_sector', 'sub_sector') AND lower(replace(name, '_', ' ')) = ?",
        [lab],
    )
    if not rows:
        return None
    if len(rows) > 1:
        # Two-level collision (Energy, Healthcare): sector wins unless --as.
        want = as_level or "sector"
        picked = [r for r in rows if r[2] == want]
        if not picked:
            picked = rows[:1]
        rows = picked
    node_id, name, kind = rows[0]
    members = _taxonomy_members(src, node_id, kind, name)
    return Target(
        "set",
        "taxonomy",
        entities=members,
        label=name,
        digest_kind="set",
        note=f"{kind} {name}: {len(members)} companies",
    )


def _resolve_family(src: Sources, q: str) -> Target | None:
    """Key 4: curated family:<Name> label -> member set (+ group routing)."""
    fam = q[len("family:") :] if q.lower().startswith("family:") else q
    if not fam:
        return None
    rows = src.fetch(
        "research",
        "hyper_edges",
        "SELECT id, label FROM {t} WHERE edge_type = 'group' AND lower(label) = ?",
        [f"family:{fam}".lower()],
    )
    if not rows:
        return None
    ids = [r[0] for r in rows]
    fam = rows[0][1][len("family:") :]  # canonical stored casing
    members: list[tuple] = []
    if ids:
        ph = ",".join("?" * len(ids))
        members = src.fetch(
            "research",
            "hyper_incidences",
            f"SELECT DISTINCT entity_name FROM {{t}} WHERE edge_id IN ({ph})",  # noqa: S608  ph ?-builder
            ids,
        )
    names = sorted({r[0] for r in members})
    return Target(
        "relation",
        "family",
        entities=names,
        label=fam,
        relation="family",
        digest_kind="set",
        note=f"family {fam}: {len(names)} members",
    )


def _match_relation(q: str) -> tuple[str, str] | None:
    """Key 5: closed relation grammar -> (verb, remainder)."""
    m = _REL_LEAD.match(q.strip())
    if m:
        return _REL_VERB[m.group(1).lower()], m.group(2)
    m = _REL_TAIL.match(q.strip())
    if m:
        return _REL_VERB[m.group(2).lower()], m.group(1)
    return None


def _resolve_name(src: Sources, q: str, allow_candidates: bool) -> Target:
    """Key 6: plain name tiers, then ranked candidates."""
    rows = src.fetch(
        "research",
        "entities",
        "SELECT name FROM {t} WHERE entity_type = 'company' AND lower(name) = lower(?)",
        [q],
    )
    names = sorted({r[0] for r in rows})
    if len(names) == 1:
        return Target("one", "name", entities=names, label=names[0], digest_kind="entity")
    if len(names) > 1:
        return Target(
            "candidates",
            "name",
            candidates=[{"name": n, "reason": "duplicate stored name"} for n in names][
                :MAX_CANDIDATES
            ],
            note=f"ambiguous name: {len(names)} entities",
        )
    nm = src.name_map().get(norm_name(q))
    if nm:
        return Target(
            "one", "name", entities=[nm], label=nm, digest_kind="entity", note="normalized"
        )
    sm = src.suffix_map().get(strip_suffixes(norm_name(q)))
    if sm:
        return Target(
            "one", "name", entities=[sm], label=sm, digest_kind="entity", note="suffix-stripped"
        )
    abbrev = _abbrev_name(q)
    if abbrev:
        hit = src.fetch(
            "research",
            "entities",
            "SELECT name FROM {t} WHERE entity_type = 'company' AND name = ?",
            [abbrev],
        )
        if hit:
            return Target(
                "one",
                "name",
                entities=[abbrev],
                label=abbrev,
                digest_kind="entity",
                note="abbreviation",
            )
    alias = src.fetch(
        "research",
        "entity_identifiers",
        "SELECT entity_name FROM {t} WHERE identifier_type = 'alias' AND lower(identifier_value) = lower(?)",
        [q],
    )
    if len(alias) == 1:
        nm = alias[0][0]
        return Target("one", "name", entities=[nm], label=nm, digest_kind="entity", note="alias")
    if not allow_candidates:
        return Target("unresolved", "name", note=f"no exact match: {q}")
    cands = _rank_candidates(src, q)
    if not cands:
        return Target("unresolved", "name", note=f"no match: {q}")
    return Target("candidates", "name", candidates=cands, note=f"{len(cands)} candidates")


def _abbrev_name(q: str) -> str | None:
    from helpers.core.fuzzy_match import ABBREVIATIONS

    for key in (q, q.upper(), q.replace("&", "and").upper(), q.replace("and", "&")):
        if key in ABBREVIATIONS:
            return ABBREVIATIONS[key]
    return None


def _rank_candidates(src: Sources, q: str) -> list[dict]:
    pat_prefix = f"{q.lower()}%"
    pat_sub = f"%{q.lower()}%"
    rows = src.fetch(
        "research",
        "entities",
        "SELECT name, ticker, sector_classification, "
        "CASE WHEN ticker IS NOT NULL AND ticker <> '' THEN 0 ELSE 1 END AS listed, "
        "CASE WHEN lower(name) LIKE ? THEN 0 WHEN lower(name) LIKE ? THEN 1 ELSE 2 END AS m "
        "FROM {t} WHERE entity_type = 'company' AND lower(name) LIKE ? "
        "ORDER BY listed, m, length(name), name LIMIT ?",
        [pat_prefix, pat_prefix, pat_sub, MAX_CANDIDATES],
    )
    out = []
    for name, ticker, sector, _listed, m in rows:
        reason = ("prefix match", "token/substring match", "substring match")[m]
        out.append({"name": name, "ticker": ticker, "sector": sector, "reason": reason})
    return out


def resolve(  # noqa: C901  one dispatcher over six key tiers; each tier is a straight-line block
    src: Sources,
    query: str,
    *,
    as_level: str | None = None,
    allow_candidates: bool = True,
    allow_relation: bool = True,
) -> Target:
    """One query string -> Target (keys 1-6, first decisive wins)."""
    q = query.strip()
    if not q:
        return Target("unresolved", "empty", note="empty query")
    for key_type, rx in (
        ("ticker", _TICKER_RE),
        # figi before isin: BBG… strings also satisfy the isin shape
        ("figi", _FIGI_RE),
        ("isin", _ISIN_RE),
        ("cin", _CIN_RE),
    ):
        probe = q.upper()
        if key_type in ("isin", "figi", "cin") and rx.match(probe):
            return _resolve_identifier(src, key_type, probe)
        if key_type == "ticker" and rx.match(q) and ("." in q or q.isupper()):
            t = _resolve_identifier(src, "ticker", q.upper())
            if t.kind != "unresolved":
                return t
            # a dotted shape is decisively a ticker: never fall through
            if "." in q:
                return t
    sym = _resolve_symbol(src, q)
    if sym is not None and sym.kind in ("one", "candidates"):
        return sym
    tax = _resolve_taxonomy(src, q, as_level)
    if tax is not None:
        return tax
    fam = _resolve_family(src, q)
    if fam is not None:
        return fam
    if allow_relation:
        rel = _match_relation(q)
        if rel:
            verb, remainder = rel
            sub = resolve(
                src,
                remainder,
                as_level=as_level,
                allow_candidates=allow_candidates,
                allow_relation=False,
            )
            if sub.kind in ("one", "set"):
                return Target(
                    "relation",
                    "relation",
                    entities=sub.entities,
                    label=sub.label,
                    relation=verb,
                    digest_kind="set",
                    note=f"{verb} of {sub.label}",
                )
            return Target(
                "unresolved",
                "relation",
                relation=verb,
                note=f"{verb} of {remainder!r}: {sub.note}",
            )
    return _resolve_name(src, q, allow_candidates)


# -- snapshot sections (S1) ----------------------------------------------------


def _entity_row(src: Sources, name: str) -> dict:
    rows = src.fetch(
        "research",
        "entities",
        "SELECT name, entity_type, sector_classification, ticker, cin, normalized_name, file_path "
        "FROM {t} WHERE name = ?",
        [name],
    )
    if not rows:
        return {}
    r = rows[0]
    ent = {
        "name": r[0],
        "entity_type": r[1],
        "sector_classification": r[2],
        "ticker": r[3],
        "cin": r[4],
        "normalized_name": r[5],
        "note_path": r[6],
    }
    vnode = src.fetch(
        "graph", "v_node", "SELECT market_cap FROM {t} WHERE name = ? AND kind = 'company'", [name]
    )
    if vnode:
        ent["market_cap_band"] = vnode[0][0]
    if src.has("research", "entity_ticker_status"):
        st = src.fetch(
            "research",
            "entity_ticker_status",
            "SELECT status, successor FROM {t} WHERE entity_name = ?",
            [name],
        )
        if st:
            ent["ticker_status"] = st[0][0]
            ent["ticker_successor"] = st[0][1]
    if src.has("research", "entity_tags"):
        ent["tags"] = [
            r[0]
            for r in src.fetch(
                "research", "entity_tags", "SELECT tag FROM {t} WHERE entity_name = ?", [name]
            )
        ]
    return ent


def _isins_of(src: Sources, name: str) -> list[str]:
    return [
        r[0]
        for r in src.fetch(
            "research",
            "entity_identifiers",
            "SELECT DISTINCT identifier_value FROM {t} WHERE entity_name = ? AND identifier_type = 'isin'",
            [name],
        )
    ]


def _ticker_stem(name: str, ticker: str | None) -> str:
    t = ticker or ""
    return t.split(".", 1)[0] if t else ""


def section_entity(src: Sources, name: str) -> dict:
    return _entity_row(src, name)


def section_identifiers(src: Sources, name: str) -> list[dict]:
    if not src.has("research", "entity_identifiers"):
        return []
    rows = src.fetch(
        "research",
        "entity_identifiers",
        "SELECT identifier_type, identifier_value, namespace, source_ref FROM {t} "
        "WHERE entity_name = ? ORDER BY identifier_type, identifier_value",
        [name],
    )
    return [{"type": r[0], "value": r[1], "namespace": r[2], "source_ref": r[3]} for r in rows]


def _graph_analytics_value(raw: str) -> object:
    try:
        v = json.loads(raw)
    except TypeError, ValueError:
        return raw
    if isinstance(v, dict) and set(v) == {"value"}:
        return v["value"]
    if isinstance(v, dict) and "block" in v:
        return {"block": v.get("block"), "k": v.get("k")}
    if isinstance(v, dict) and "candidates" in v:
        v = dict(v)
        v["candidates"] = v["candidates"][:5]
        return v
    return v


def section_centrality(src: Sources, name: str) -> dict:
    if not src.has("research", "graph_analytics"):
        return {}
    rows = src.fetch(
        "research",
        "graph_analytics",
        "SELECT metric, value FROM {t} WHERE entity_name = ? AND metric LIKE '%centrality%'",
        [name],
    )
    return {r[0]: _graph_analytics_value(r[1]) for r in rows}


def section_graph_analytics(src: Sources, name: str) -> dict:
    if not src.has("research", "graph_analytics"):
        return {}
    rows = src.fetch(
        "research",
        "graph_analytics",
        "SELECT metric, value FROM {t} WHERE entity_name = ? AND metric NOT LIKE '%centrality%'",
        [name],
    )
    return {r[0]: _graph_analytics_value(r[1]) for r in rows}


def section_edge_counts(src: Sources, name: str) -> dict:
    if not src.has("research", "graph_edges"):
        return {}
    out: dict[str, int] = {}
    for direction, col in (("out", "source"), ("in", "target")):
        rows = src.fetch(
            "research",
            "graph_edges",
            f"SELECT edge_type, count(*) FROM {{t}} WHERE {col} = ? GROUP BY 1",  # noqa: S608  col is the constant above
            [name],
        )
        for et, n in rows:
            out[f"{direction}:{et}"] = n
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def section_company_metrics(src: Sources, name: str) -> dict:
    if not src.has("research", "company_metrics"):
        return {}
    rows = src.fetch(
        "research",
        "company_metrics",
        "SELECT metric_label, value_raw, period, as_of_edition, consolidated FROM {t} "
        "WHERE entity = ? ORDER BY metric_label, period",
        [name],
    )
    out: dict[str, list] = {}
    for label, raw, period, edition, consolidated in rows:
        out.setdefault(label, []).append(
            {"value": raw, "period": period, "as_of_edition": edition, "consolidated": consolidated}
        )
    return out


def section_listings(src: Sources, name: str, ticker: str | None, isins: list[str]) -> list[dict]:
    if not isins or not src.has("sources", "exchange_listings"):
        return []
    ph = ",".join("?" * len(isins))
    rows = src.fetch(
        "sources",
        "exchange_listings",
        f"SELECT exchange, segment, symbol, kite_tradingsymbol, industry, asset_type FROM {{t}} "
        f"WHERE isin IN ({ph}) ORDER BY exchange",  # noqa: S608  ph is a ?-builder; isins bound
        isins,
    )
    return [
        {
            "exchange": r[0],
            "segment": r[1],
            "symbol": r[2],
            "trading_symbol": r[3],
            "industry": r[4],
            "asset_type": r[5],
        }
        for r in rows
    ]


def section_bhavcopy_recent(src: Sources, name: str, isins: list[str]) -> dict:
    if not isins or not src.has("sources", "bhavcopy_sample"):
        return {"rows": [], "coverage": "sample table absent"}
    ph = ",".join("?" * len(isins))
    rows = src.fetch(
        "sources",
        "bhavcopy_sample",
        f"SELECT exchange, trade_date, symbol, series FROM {{t}} WHERE isin IN ({ph}) "
        f"ORDER BY trade_date DESC LIMIT 10",  # noqa: S608  ph is a ?-builder; isins bound
        isins,
    )
    return {
        "rows": [
            {"exchange": r[0], "trade_date": str(r[1]), "symbol": r[2], "series": r[3]}
            for r in rows
        ],
        "coverage": "sample presence (not OHLCV)",
    }


def section_rpt_summary(src: Sources, name: str, ticker: str | None) -> dict:
    if not src.has("sources", "rpt_transactions"):
        return {}
    stem = _ticker_stem(name, ticker)
    if not stem:
        return {"coverage": "no NSE symbol"}
    rows = src.fetch(
        "sources",
        "rpt_transactions",
        "SELECT period_end_date, count(*), count(DISTINCT counter_party), "
        "sum(TRY_CAST(amount_during_period AS DOUBLE)) FROM {t} WHERE symbol = ? GROUP BY 1 "
        "ORDER BY 1 DESC",
        [stem],
    )
    if not rows:
        return {"coverage": f"no RPT filings for symbol {stem}"}
    parties = src.fetch(
        "sources",
        "rpt_transactions",
        "SELECT counter_party, count(*) FROM {t} WHERE symbol = ? GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
        [stem],
    )
    return {
        "symbol": stem,
        "periods": [
            {"period_end": r[0], "txns": r[1], "counterparties": r[2], "amount_sum": r[3]}
            for r in rows
        ],
        "top_counterparties": [{"name": r[0], "txns": r[1]} for r in parties],
    }


def section_shareholding_recent(src: Sources, name: str, ticker: str | None) -> dict:
    if not src.has("sources", "shp_filings"):
        return {"filings": [], "coverage": "shp tables absent"}
    stem = _ticker_stem(name, ticker)
    filings = src.fetch(
        "sources",
        "shp_filings",
        "SELECT filing_id, period_end, submission_dt, promoter_pct, public_pct FROM {t} "
        "WHERE symbol = ? ORDER BY submission_dt DESC LIMIT 3",
        [stem] if stem else ["__none__"],
    )
    out = {
        "filings": [
            {
                "filing_id": r[0],
                "period_end": str(r[1]) if r[1] else None,
                "submitted": r[2],
                "promoter_pct": r[3],
                "public_pct": r[4],
            }
            for r in filings
        ]
    }
    total = src.fetch("sources", "shp_filings", "SELECT count(*) FROM {t}")[0][0]
    out["coverage"] = (
        f"{len(filings)} of {total} filings (symbol {stem or 'n/a'})"
        if filings
        else f"0 of {total} filings (symbol {stem or 'n/a'})"
    )
    if filings and src.has("sources", "shp_holders"):
        fid = filings[0][0]
        holders = src.fetch(
            "sources",
            "shp_holders",
            "SELECT holder_name, category, stake_pct FROM {t} WHERE filing_id = ? "
            "AND holder_name IS NOT NULL ORDER BY stake_pct DESC NULLS LAST LIMIT 6",
            [fid],
        )
        out["top_holders_latest"] = [
            {"holder": r[0], "category": r[1], "stake_pct": r[2]} for r in holders
        ]
    return out


def section_credit_ratings(src: Sources, name: str, isins: list[str]) -> list[dict]:
    if not isins or not src.has("sources", "credit_ratings"):
        return []
    ph = ",".join("?" * len(isins))
    rows = src.fetch(
        "sources",
        "credit_ratings",
        f"SELECT rating_agency, credit_rating, rating_action, date_of_rating, instrument_type "
        f"FROM {{t}} WHERE equity_isin IN ({ph}) OR isin IN ({ph}) "
        f"ORDER BY date_of_rating DESC NULLS LAST LIMIT 8",  # noqa: S608  ph ?-builders; isins bound
        isins + isins,
    )
    return [
        {
            "agency": r[0],
            "rating": r[1],
            "action": r[2],
            "date": str(r[3]) if r[3] else None,
            "instrument": r[4],
        }
        for r in rows
    ]


def section_index_membership(src: Sources, name: str, isins: list[str]) -> list[dict]:
    out: dict[str, dict] = {}
    if isins and src.has("sources", "index_constituents"):
        ph = ",".join("?" * len(isins))
        rows = src.fetch(
            "sources",
            "index_constituents",
            f"WITH latest AS (SELECT index_name, max(as_of) AS as_of FROM {{t}} GROUP BY 1) "
            f"SELECT c.index_name, c.as_of FROM {{t}} c JOIN latest l "
            f"ON l.index_name = c.index_name AND l.as_of = c.as_of "
            f"WHERE c.isin IN ({ph}) ORDER BY 1",  # noqa: S608  ph ?-builder; isins bound
            isins,
        )
        for idx, as_of in rows:
            out[idx] = {"index": idx, "as_of": str(as_of), "source": "index_constituents"}
    if src.has("graph", "e_listed_on_index"):
        rows = src.fetch(
            "graph",
            "e_listed_on_index",
            "SELECT v.name, json_extract_string(e.properties, '$.as_of') FROM {t} e "
            "JOIN {v} v ON v.id = e.index_id "
            "JOIN {v} c ON c.id = e.company_id WHERE c.name = ? ORDER BY 1",
            [name],
        )
        for idx, as_of in rows:
            out.setdefault(idx, {"index": idx, "as_of": as_of, "source": "graph_edges"})
    return sorted(out.values(), key=lambda d: d["index"])


def section_mca_cin(src: Sources, name: str, cin: str | None) -> dict:
    if not cin or not src.has("sources", "mca_cin"):
        return {}
    rows = src.fetch(
        "sources",
        "mca_cin",
        "SELECT cin, mca_name, status, class, pba, state, via, query FROM {t} WHERE cin = ?",
        [cin],
    )
    if not rows:
        return {}
    r = rows[0]
    return {
        "cin": r[0],
        "mca_name": r[1],
        "status": r[2],
        "class": r[3],
        "pba": r[4],
        "state": r[5],
        "via": r[6],
        "query": r[7],
    }


def section_figi(src: Sources, name: str, ticker: str | None, isins: list[str]) -> dict:
    out: dict = {"identifiers": []}
    for r in section_identifiers(src, name):
        if r["type"] == "figi":
            out["identifiers"].append(r["value"])
    if isins and src.has("sources", "figi_instruments"):
        stem = _ticker_stem(name, ticker)
        if stem:
            rows = src.fetch(
                "sources",
                "figi_instruments",
                "SELECT figi, name, exch_code, market_sector, security_type FROM {t} "
                "WHERE ticker <> '' AND upper(split_part(ticker, '.', 1)) = ? LIMIT 5",
                [stem.upper()],
            )
            out["instruments"] = [
                {"figi": r[0], "name": r[1], "exchange": r[2], "sector": r[3], "type": r[4]}
                for r in rows
            ]
    return out


def snapshot_sections(src: Sources, name: str) -> dict:
    """All 14 sections for one entity (missing tables -> coverage notes)."""
    ent = _entity_row(src, name)
    ticker = ent.get("ticker")
    isins = _isins_of(src, name)
    secs: dict = {}
    secs["entity"] = ent
    secs["identifiers"] = section_identifiers(src, name)
    secs["centrality"] = section_centrality(src, name)
    secs["graph_analytics"] = section_graph_analytics(src, name)
    secs["edge_counts"] = section_edge_counts(src, name)
    secs["company_metrics"] = section_company_metrics(src, name)
    secs["listings"] = section_listings(src, name, ticker, isins)
    secs["bhavcopy_recent"] = section_bhavcopy_recent(src, name, isins)
    secs["rpt_summary"] = section_rpt_summary(src, name, ticker)
    secs["shareholding_recent"] = section_shareholding_recent(src, name, ticker)
    secs["credit_ratings"] = section_credit_ratings(src, name, isins)
    secs["index_membership"] = section_index_membership(src, name, isins)
    secs["mca_cin"] = section_mca_cin(src, name, ent.get("cin"))
    secs["figi"] = section_figi(src, name, ticker, isins)
    return {
        "entity": name,
        "sections": secs,
        "source_mode": dict(_modes(src)),
        "warnings": src.warnings,
    }


def _modes(src: Sources) -> list[tuple[str, str]]:
    return [(dom, d.mode) for dom, d in src.domains.items()]


# -- S2: group -----------------------------------------------------------------


def _family_rosters_for(src: Sources, name: str) -> list[dict]:
    rows = src.fetch(
        "research",
        "hyper_edges",
        "SELECT e.label FROM {t} e JOIN {t2} i ON i.edge_id = e.id "
        "WHERE i.entity_name = ? AND e.edge_type = 'group' AND e.label LIKE 'family:%'",
        [name],
        table2="hyper_incidences",
    )
    return [_family_roster(src, r[0][len("family:") :]) for r in rows]


def _family_roster(src: Sources, fam: str) -> dict:
    rows = src.fetch(
        "research",
        "hyper_edges",
        "SELECT id FROM {t} WHERE edge_type = 'group' AND lower(label) = ?",
        [f"family:{fam}".lower()],
    )
    ids = [r[0] for r in rows]
    members: dict[str, dict] = {}
    if ids:
        ph = ",".join("?" * len(ids))
        rows2 = src.fetch(
            "research",
            "hyper_incidences",
            f"SELECT DISTINCT entity_name FROM {{t}} WHERE edge_id IN ({ph})",  # noqa: S608  ph ?-builder
            ids,
        )
        for (m,) in rows2:
            members[m] = {"name": m}
    _decorate(src, members)
    return {
        "source": "family",
        "label": fam,
        "members": sorted(members.values(), key=lambda m: m["name"]),
    }


def _ownership_roster(src: Sources, name: str) -> dict:
    if not src.has("research", "ultimate_parent"):
        return {"source": "ownership", "coverage": "ultimate_parent absent"}
    mine = src.fetch(
        "research",
        "ultimate_parent",
        "SELECT ultimate_parent, depth, path, root_kind FROM {t} WHERE company = ?",
        [name],
    )
    if not mine:
        return {"source": "ownership", "coverage": f"{name} is its own apex or absent"}
    apexes = sorted({r[0] for r in mine})
    ph = ",".join("?" * len(apexes))
    rows = src.fetch(
        "research",
        "ultimate_parent",
        f"SELECT company, min(depth) FROM {{t}} WHERE ultimate_parent IN ({ph}) "  # noqa: S608  ph ?-builder
        "GROUP BY 1 ORDER BY 1",
        apexes,
    )
    members = [{"name": r[0], "depth": r[1], "relation": "same apex"} for r in rows]
    _decorate(src, {m["name"]: m for m in members})
    flags = sorted({r[3] for r in mine if r[3] == "cycle-guard"})

    def _path(raw: str) -> list[str]:
        try:
            return json.loads(raw)  # stored as a json array string
        except ValueError, TypeError:
            return [raw]

    return {
        "source": "ownership",
        "apexes": apexes,
        "paths": [{"apex": r[0], "depth": r[1], "path": _path(r[2])} for r in mine],
        "root_kinds": sorted({r[3] for r in mine}),
        "cycle_guard": bool(flags),
        "members": members,
    }


def _disclosed_roster(src: Sources, name: str, ticker: str | None) -> dict:
    members: dict[str, dict] = {}
    rows = src.fetch(
        "research",
        "hyper_edges",
        "SELECT id FROM {t} WHERE edge_type = 'subsidiary' AND label = ?",
        [name],
    )
    for r in rows:
        for (m,) in src.fetch(
            "research",
            "hyper_incidences",
            "SELECT DISTINCT entity_name FROM {t} WHERE edge_id = ?",
            [r[0]],
        ):
            members.setdefault(m, {"name": m, "relation": "rpt set"})
    stem = _ticker_stem(name, ticker)
    if stem:
        for r in src.fetch(
            "research",
            "hyper_edges",
            "SELECT id FROM {t} WHERE label = ?",
            [f"vigil:rpt:{stem}"],
        ):
            for (m,) in src.fetch(
                "research",
                "hyper_incidences",
                "SELECT DISTINCT entity_name FROM {t} WHERE edge_id = ?",
                [r[0]],
            ):
                members.setdefault(m, {"name": m, "relation": "rpt set"})
    _decorate(src, members)
    return {
        "source": "disclosed",
        "members": sorted(members.values(), key=lambda m: m["name"]),
        "coverage": f"{len(members)} entities in the filer's related-party set",
    }


def _decorate(src: Sources, members: dict[str, dict]) -> None:
    """Attach ticker/CIN/kind to roster members (one query per field)."""
    if not members:
        return
    names = list(members)
    ph = ",".join("?" * len(names))
    for name, tk, cin in src.fetch(
        "research",
        "entities",
        f"SELECT name, ticker, cin FROM {{t}} WHERE name IN ({ph})",  # noqa: S608  ph ?-builder
        names,
    ):
        members[name]["ticker"] = tk
        members[name]["cin"] = cin


def _relation_walk(src: Sources, name: str, relation: str, depth: int, cap: int = 2000) -> dict:
    """Bounded walk over e_subsidiary/e_group/e_acquired/e_invested (node IDs)."""
    visited: set[str] = {name}
    out: list[dict] = []
    frontier = [(name, 0)]
    truncated = False
    while frontier and len(visited) < cap:
        cur, d = frontier.pop(0)
        if d >= depth:
            continue
        rows = _relation_edges(src, cur, relation)
        for other, source_ref in rows:
            entry = {"name": other, "from": cur, "depth": d + 1, "source_ref": source_ref}
            if other in visited:
                entry["cycle"] = True
                out.append(entry)
                continue
            visited.add(other)
            out.append(entry)
            frontier.append((other, d + 1))
        if len(visited) >= cap:
            truncated = True
    _decorate(src, {e["name"]: e for e in out})
    return {"relation": relation, "depth": depth, "edges": out, "truncated": truncated}


def _relation_edges(src: Sources, node_name: str, relation: str) -> list[tuple[str, str]]:
    if relation == "subsidiaries":
        rows = src.fetch(
            "graph",
            "e_subsidiary",
            "SELECT v.name, e.source_ref FROM {t} e "
            "JOIN {v} v ON v.id = e.subsidiary_name "
            "JOIN {v} p ON p.id = e.parent_name WHERE p.name = ?",
            [node_name],
        )
    elif relation == "parents":
        rows = src.fetch(
            "graph",
            "e_subsidiary",
            "SELECT p.name, e.source_ref FROM {t} e "
            "JOIN {v} p ON p.id = e.parent_name "
            "JOIN {v} v ON v.id = e.subsidiary_name WHERE v.name = ?",
            [node_name],
        )
    elif relation == "peers":
        rows = src.fetch(
            "graph",
            "e_group",
            "SELECT b.name, e.source_ref FROM {t} e "
            "JOIN {v} a ON a.id = e.a_name JOIN {v} b ON b.id = e.b_name "
            "WHERE a.name = ?",
            [node_name],
        )
    elif relation == "acquisitions":
        rows = src.fetch(
            "graph",
            "e_acquired",
            "SELECT t.name, e.source_ref FROM {t} e "
            "JOIN {v} a ON a.id = e.acquirer_name "
            "JOIN {v} t ON t.id = e.target_name WHERE a.name = ?",
            [node_name],
        )
    elif relation == "invested":
        rows = src.fetch(
            "graph",
            "e_invested",
            "SELECT c.name, e.source_ref FROM {t} e "
            "JOIN {v} i ON i.id = e.institution_name "
            "JOIN {v} c ON c.id = e.company_name WHERE i.name = ?",
            [node_name],
        )
    elif relation == "jv":
        rows = src.fetch(
            "research",
            "graph_edges",
            "SELECT target, source_ref FROM {t} WHERE source = ? AND edge_type = 'jv_with' "
            "UNION ALL SELECT source, source_ref FROM {t} WHERE target = ? AND edge_type = 'jv_with'",
            [node_name, node_name],
        )
    else:  # family handled by the roster, not the walker
        return []
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for other, ref in rows:
        key = (other, ref or "")
        if key not in seen:
            seen.add(key)
            out.append((other, ref or ""))
    return out


def group_report(src: Sources, target: Target, depth: int, include: list[str]) -> dict:
    """S2: labeled union of the three rosters + bounded relations."""
    name = target.entities[0] if target.entities else target.label
    ent = _entity_row(src, name)
    families = _family_rosters_for(src, name)
    if not families:
        one = src.fetch(
            "research",
            "hyper_edges",
            "SELECT id FROM {t} WHERE edge_type = 'group' AND lower(label) = ?",
            [f"family:{name}".lower()],
        )
        if one:
            families = [_family_roster(src, name)]
    out: dict = {
        "target": name,
        "ticker": ent.get("ticker"),
        "rosters": {
            "family": families,
            "ownership": _ownership_roster(src, name),
            "disclosed": _disclosed_roster(src, name, ent.get("ticker")),
        },
        "relations": {},
    }
    rels = [r for r in include if r in KNOWN_RELATIONS]
    if target.relation and target.relation in KNOWN_RELATIONS and target.relation not in rels:
        rels.insert(0, target.relation)
    if target.relation == "family":
        rels = [r for r in rels if r != "family"]
    for rel in rels:
        out["relations"][rel] = _relation_walk(src, name, rel, depth)
    out["source_mode"] = dict(_modes(src))
    out["warnings"] = list(src.warnings)
    return out


# -- S3: bulk ------------------------------------------------------------------


def _index_universe(src: Sources, index_name: str) -> tuple[list[str], list[str]]:
    """(entity names, unresolved isins) for one index at its latest vintage."""
    rows = src.fetch(
        "sources",
        "index_constituents",
        "WITH latest AS (SELECT max(as_of) AS as_of FROM {t} WHERE index_name = ?) "
        "SELECT c.isin FROM {t} c JOIN latest l ON l.as_of = c.as_of WHERE c.index_name = ?",
        [index_name, index_name],
    )
    isins = sorted({r[0] for r in rows if r[0]})
    if not isins:
        return [], []
    ph = ",".join("?" * len(isins))
    pairs = src.fetch(
        "research",
        "entity_identifiers",
        f"SELECT DISTINCT identifier_value, entity_name FROM {{t}} "
        f"WHERE identifier_type = 'isin' AND upper(identifier_value) IN ({ph})",  # noqa: S608  ph ?-builder
        [i.upper() for i in isins],
    )
    found = {v.upper(): n for v, n in pairs}
    names = sorted({found[i.upper()] for i in isins if i.upper() in found})
    missing = [i for i in isins if i.upper() not in found]
    return names, missing


def _file_universe(src: Sources, path: Path) -> tuple[list[str], list[dict]]:
    """(resolved names, unresolved entries) from a mixed-identifier file."""
    names: list[str] = []
    unresolved: list[dict] = []
    for line in path.read_text().splitlines():
        token = line.strip()
        if not token or token.startswith("#"):
            continue
        t = resolve(src, token, allow_candidates=False, allow_relation=False)
        if t.kind == "one":
            names.extend(t.entities)
        else:
            unresolved.append({"token": token, "note": t.note})
    return sorted(set(names)), unresolved


def bulk_rows(src: Sources, names: list[str], fields: list[str]) -> list[dict]:
    """One flat dict per member over the selected sections."""
    rows: list[dict] = []
    for name in names:
        ent = _entity_row(src, name)
        ticker = ent.get("ticker")
        isins = _isins_of(src, name)
        row: dict = dict(ent) if ent else {"entity": name}
        if "entity" in fields:
            row.pop("tags", None)
        if "identifiers" in fields:
            row["identifiers"] = section_identifiers(src, name)
        if "figi" in fields:
            row["figi"] = section_figi(src, name, ticker, isins)
        if "listings" in fields:
            row["listings"] = section_listings(src, name, ticker, isins)
        if "index_membership" in fields:
            row["index_membership"] = [
                d["index"] for d in section_index_membership(src, name, isins)
            ]
        if "company_metrics" in fields:
            row["company_metrics"] = section_company_metrics(src, name)
        if "credit_ratings" in fields:
            row["credit_ratings"] = section_credit_ratings(src, name, isins)
        if "mca_cin" in fields:
            row["mca_cin"] = section_mca_cin(src, name, ent.get("cin"))
        rows.append(row)
    return rows


def _metric_values(vals: dict[str, list[float]], metrics: dict) -> None:
    """Fold numeric ``company_metrics`` label values into ``vals``."""
    for label, series in metrics.items():
        for item in series or []:
            try:
                num = float(item.get("value"))
            except TypeError, ValueError:
                continue
            vals.setdefault(f"company_metrics.{label}", []).append(num)


def _numeric_values(rows: list[dict]) -> dict[str, list[float]]:
    """Collect numeric values per field: top-level numbers (entity columns),
    the length of each list-valued section, and numeric ``company_metrics``
    label values — metric payloads carry raw strings (``"1,000 Cr"``) until
    a numeric coercion exists, so non-numeric values are skipped, not
    guessed (a list length of 0 counts — absence is data)."""
    vals: dict[str, list[float]] = {}
    for r in rows:
        for k, v in r.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                vals.setdefault(k, []).append(float(v))
            elif isinstance(v, list):
                vals.setdefault(k, []).append(float(len(v)))
            elif isinstance(v, dict) and k == "company_metrics":
                _metric_values(vals, v)
    return vals


def summary_rows(rows: list[dict]) -> list[dict]:
    """One aggregate row per numeric field (count, sum, median) per set."""
    return [
        {
            "field": k,
            "count": len(xs),
            "sum": sum(xs),
            "median": statistics.median(xs),
        }
        for k, xs in sorted(_numeric_values(rows).items())
    ]


# -- S5: digest ----------------------------------------------------------------


def _entity_digest(src: Sources, name: str) -> dict:
    ent = _entity_row(src, name)
    ticker = ent.get("ticker") or ""
    isins = _isins_of(src, name)
    exchanges = sorted({r["exchange"] for r in section_listings(src, name, ticker, isins)})
    idx = [
        d["index"]
        for d in section_index_membership(src, name, isins)
        if "NIFTY" in d["index"].upper()
        and d["index"].upper() in ("NIFTY 50", "NIFTY 100", "NIFTY 500", "NIFTY NEXT 50")
    ]
    mcap = ""
    for label, vals in section_company_metrics(src, name).items():
        if label == "market_capitalization" and vals:
            mcap = str(vals[0].get("value") or "")
            break
    key = ticker or name
    flag = "--ticker" if ticker else "--name"
    bits: list[str] = []
    if exchanges:
        bits.append("+".join(exchanges))
    if idx:
        bits.append("/".join(idx[:2]))
    if mcap:
        bits.append(f"mcap {mcap}")
    bits.append(f"run: company_query.py snapshot {flag} {key}")
    snippet = " · ".join(bits)
    if len(snippet) > 140:
        snippet = snippet[:137] + "..."
    return {
        "path": f"company:{key}",
        "line": None,
        "title": f"{name} · {ent.get('sector_classification') or ''}".strip(" ·"),
        "snippet": snippet,
        "score": 1.0,
        "kind": "company",
    }


def _nifty50_count(src: Sources, names: list[str]) -> int | None:
    if not src.has("sources", "index_constituents"):
        return None
    ph = ",".join("?" * len(names))
    rows = src.fetch(
        "research",
        "entity_identifiers",
        f"SELECT DISTINCT identifier_value FROM {{t}} WHERE identifier_type = 'isin' "
        f"AND entity_name IN ({ph})",  # noqa: S608  ph ?-builder
        names,
    )
    isins = [r[0] for r in rows]
    if not isins:
        return 0
    ph2 = ",".join("?" * len(isins))
    n = src.fetch(
        "sources",
        "index_constituents",
        f"WITH latest AS (SELECT max(as_of) AS as_of FROM {{t}} WHERE index_name = 'NIFTY 50') "
        f"SELECT count(DISTINCT c.isin) FROM {{t}} c JOIN latest l ON l.as_of = c.as_of "
        f"WHERE c.index_name = 'NIFTY 50' AND c.isin IN ({ph2})",  # noqa: S608  ph ?-builder
        isins,
    )
    return int(n[0][0]) if n else 0


def _set_digest(src: Sources, target: Target) -> dict:
    label = target.label
    n = len(target.entities)
    listed = 0
    if target.entities:
        ph = ",".join("?" * len(target.entities))
        rows = src.fetch(
            "research",
            "entities",
            f"SELECT count(*) FROM {{t}} WHERE name IN ({ph}) "  # noqa: S608  ph ?-builder
            "AND ticker IS NOT NULL AND ticker <> ''",
            target.entities,
        )
        listed = int(rows[0][0])
    kind_word = "family" if target.key == "family" else "sector"
    head = f"{label} · {n} companies ({listed} listed)"
    nifty = _nifty50_count(src, target.entities)
    if nifty is not None and target.key != "family":
        head += f", {nifty} in NIFTY 50"
    quoted = label if " " in label else label
    return {
        "path": f"{kind_word}:{label}",
        "line": None,
        "title": head,
        "snippet": f"run: company_query.py bulk {quoted} --format csv"[:140],
        "score": 0.5,
        "kind": kind_word,
    }


def digest_hits(
    src: Sources, query: str, *, as_level: str | None = None, limit: int = 4
) -> tuple[list[dict], str]:
    """S5 leg backend: typed keys 1-5 (+exact/normalized names) -> hits."""
    t = resolve(src, query, as_level=as_level, allow_candidates=False)
    if t.kind == "one":
        return [_entity_digest(src, t.entities[0])], "1 hits · typed-key digest"
    if t.kind == "set":
        return [_set_digest(src, t)], "1 hits · typed-key digest"
    if t.kind == "relation":
        if t.relation == "family":
            return [_set_digest(src, t)], "1 hits · typed-key digest"
        run = f"run: company_query.py group {t.label} --depth 2"
        return (
            [
                {
                    "path": f"group:{t.label}",
                    "line": None,
                    "title": f"{t.relation} of {t.label}",
                    "snippet": run[:140],
                    "score": 0.5,
                    "kind": "group",
                }
            ],
            "1 hits · typed-key digest",
        )
    if t.kind == "candidates":
        hits = []
        for c in t.candidates[:limit]:
            h = _entity_digest(src, c["name"])
            h["title"] = f"ambiguous ({len(t.candidates)}) {h['title']}"
            hits.append(h)
        return hits, f"{len(hits)} hits · typed-key digest (ambiguous)"
    return [], "0 hits"


# -- rendering -----------------------------------------------------------------


def _fmt_cell(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    return str(v)


def render_snapshot(report: dict, fields: list[str] | None) -> None:
    print(f"# company snapshot — {report['entity']}")
    print(f"  source_mode: {report['source_mode']}")
    for w in report["warnings"]:
        print(f"  note: {w}")
    secs = report["sections"]
    for key in SECTION_ORDER:
        if fields and key not in fields:
            continue
        val = secs.get(key)
        print(f"\n## {key}")
        if val in (None, {}, []):
            print("    (empty)")
            continue
        if isinstance(val, dict):
            for k, v in list(val.items())[:24]:
                print(f"    {k}: {_fmt_cell(v)[:170]}")
        else:
            for row in val[:8]:
                if isinstance(row, dict):
                    cells = " · ".join(
                        f"{k}={_fmt_cell(v)}" for k, v in row.items() if v not in (None, "")
                    )
                    print(f"    {cells[:170]}")
                else:
                    print(f"    {_fmt_cell(row)[:170]}")
            if len(val) > 8:
                print(f"    … {len(val) - 8} more")


def render_group(report: dict, member_cap: int = 15) -> None:  # noqa: C901  one print block per roster shape
    print(f"# company group — {report['target']}")
    for w in report.get("warnings", []):
        print(f"  note: {w}")
    for label, roster in report["rosters"].items():
        if isinstance(roster, list):
            for r in roster:
                members = r["members"]
                print(f"\n## {label}:{r['label']} — {len(members)} members")
                for m in members[:member_cap]:
                    tk = f" [{m.get('ticker')}]" if m.get("ticker") else ""
                    print(f"    {m['name']}{tk}")
                if len(members) > member_cap:
                    print(f"    … {len(members) - member_cap} more")
        else:
            members = roster.get("members", [])
            print(f"\n## {label} — {len(members)} members")
            if roster.get("cycle_guard"):
                print("    cycle-guard: ownership paths unreliable here")
            if roster.get("apexes"):
                print(f"    apex: {', '.join(roster['apexes'])}")
            if roster.get("coverage"):
                print(f"    coverage: {roster['coverage']}")
            for m in members[:member_cap]:
                tk = f" [{m.get('ticker')}]" if m.get("ticker") else ""
                print(f"    {m['name']}{tk}")
            if len(members) > member_cap:
                print(f"    … {len(members) - member_cap} more")
    for rel, walk in report.get("relations", {}).items():
        edges = walk.get("edges", [])
        print(f"\n## relation {rel} (depth {walk['depth']}) — {len(edges)} edges")
        if walk.get("truncated"):
            print("    truncated at the node cap")
        for e in edges[:member_cap]:
            cyc = " (cycle)" if e.get("cycle") else ""
            print(f"    {e['from']} -> {e['name']} d{e['depth']}{cyc}")
        if len(edges) > member_cap:
            print(f"    … {len(edges) - member_cap} more")


def render_candidates(t: Target, cmd: str) -> None:
    print(f"unresolved: {t.note}")
    if not t.candidates:
        return
    print("candidates (re-run with the exact name):")
    for c in t.candidates:
        tk = f" [{c.get('ticker')}]" if c.get("ticker") else ""
        print(f"  {c['name']}{tk} — {c.get('reason', '')}")
    print(f'e.g. {cmd} "{t.candidates[0]["name"]}"')


# -- CLI -----------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="company_query", description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", default=None, help="repo root override (tests)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_target_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("query", nargs="?", default=None, help="one query string (typed keys 1-6)")
        sp.add_argument("--ticker", default=None)
        sp.add_argument("--name", default=None)
        sp.add_argument("--cin", default=None)
        sp.add_argument("--isin", default=None)
        sp.add_argument("--figi", default=None)
        sp.add_argument("--sector", default=None)
        sp.add_argument("--super-sector", default=None, dest="super_sector")
        sp.add_argument("--sub-sector", default=None, dest="sub_sector")
        sp.add_argument(
            "--as",
            dest="as_level",
            choices=("sector", "super_sector", "sub_sector"),
            default=None,
            help="disambiguate a label that exists at two levels",
        )

    sp = sub.add_parser("snapshot", help="14-section snapshot for one entity")
    add_target_args(sp)
    sp.add_argument("--format", choices=("human", "json"), default="human")
    sp.add_argument("--fields", default=None, help="comma list of sections to include")

    sp = sub.add_parser("group", help="three labeled rosters + bounded relations")
    add_target_args(sp)
    sp.add_argument("--depth", type=int, default=2)
    sp.add_argument("--include", default=",".join(DEFAULT_RELATIONS), help="relations to walk")
    sp.add_argument("--format", choices=("human", "json"), default="human")

    sp = sub.add_parser("bulk", help="one row per member over any resolved set")
    add_target_args(sp)
    sp.add_argument("--tickers-file", default=None, dest="tickers_file")
    sp.add_argument("--index", default=None)
    sp.add_argument("--fields", default=DEFAULT_BULK_FIELDS)
    sp.add_argument("--format", choices=("jsonl", "csv", "json"), default="jsonl")
    sp.add_argument(
        "--summary",
        action="store_true",
        help="one aggregate row per numeric field (count, sum, median) instead of member rows",
    )

    sp = sub.add_parser("digest", help="typed-key digest JSON (master_query leg backend)")
    add_target_args(sp)
    sp.add_argument("--json", action="store_true", default=True)
    sp.add_argument("--limit", type=int, default=4)

    sp = sub.add_parser("resolve", help="show the resolution outcome only")
    add_target_args(sp)
    return p


def _target_from_args(src: Sources, args: argparse.Namespace) -> Target:
    """Typed flags first (explicit form), else the positional query string."""
    for flag, key_type in (
        ("ticker", "ticker"),
        ("isin", "isin"),
        ("figi", "figi"),
        ("cin", "cin"),
    ):
        val = getattr(args, flag)
        if val:
            return _resolve_identifier(src, key_type, val)
    if args.super_sector:
        return _resolve_taxonomy(src, args.super_sector, "super_sector") or Target(
            "unresolved", "taxonomy", note=f"no super_sector {args.super_sector!r}"
        )
    if args.sub_sector:
        return _resolve_taxonomy(src, args.sub_sector, "sub_sector") or Target(
            "unresolved", "taxonomy", note=f"no sub_sector {args.sub_sector!r}"
        )
    if args.sector:
        return _resolve_taxonomy(src, args.sector, None) or Target(
            "unresolved", "taxonomy", note=f"no sector {args.sector!r}"
        )
    if args.name:
        return _resolve_name(src, args.name, allow_candidates=True)
    if not args.query:
        return Target("unresolved", "empty", note="no query given")
    return resolve(src, args.query, as_level=args.as_level)


_VERBS = ("snapshot", "group", "bulk", "digest", "resolve")


_VALUE_FLAGS = frozenset(
    {
        "--repo-root",
        "--ticker",
        "--name",
        "--cin",
        "--isin",
        "--figi",
        "--sector",
        "--super-sector",
        "--sub-sector",
        "--as",
        "--fields",
        "--format",
        "--limit",
        "--depth",
        "--index",
        "--tickers-file",
        "--include",
    }
)


def _bare_query_slot(argv: list[str]) -> int | None:
    """Index of the first bare (non-flag, non-flag-value) token, else None."""
    if any(tok in _VERBS for tok in argv):
        return None  # explicit verb present — nothing to route
    expect_value = False
    for i, tok in enumerate(argv):
        if expect_value:
            expect_value = False
            continue
        if tok.startswith("-"):
            expect_value = tok in _VALUE_FLAGS
            continue
        return i
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # bare-query form (proposal): "company_query.py 'parent of X'" routes by
    # the relation grammar — group verbs for relations, snapshot otherwise.
    slot = _bare_query_slot(argv)
    if slot is not None:
        argv.insert(slot, "group" if _match_relation(argv[slot]) else "snapshot")
    args = _build_parser().parse_args(argv)
    src = Sources(args.repo_root)
    try:
        return _dispatch(src, args)
    finally:
        src.close()


def _dispatch(src: Sources, args: argparse.Namespace) -> int:  # noqa: C901  one branch per verb
    cmd = args.cmd
    if cmd == "bulk" and (args.index or args.tickers_file):
        return _bulk_cmd(src, None, args)  # explicit universe forms skip resolution
    if cmd == "digest":
        hits, status = digest_hits(src, args.query or "", as_level=args.as_level, limit=args.limit)
        print(
            json.dumps(
                {
                    "query": args.query,
                    "status": status,
                    "sources": dict(_modes(src)),
                    "warnings": src.warnings,
                    "results": hits,
                },
                default=str,
            )
        )
        return 0
    t = _target_from_args(src, args)
    if cmd == "resolve":
        print(json.dumps(_target_view(t), indent=2, default=str))
        return 0 if t.resolved else 1
    if not t.resolved:
        render_candidates(t, "company_query.py snapshot")
        return 1
    if cmd == "snapshot":
        if t.kind != "one":
            print(
                f"snapshot needs one entity; {t.key} resolved to {len(t.entities)}", file=sys.stderr
            )
            return 1
        report = snapshot_sections(src, t.entities[0])
        fields = [f.strip() for f in args.fields.split(",")] if args.fields else None
        if args.format == "json":
            print(json.dumps(report, indent=2, default=str))
        else:
            render_snapshot(report, fields)
        return 0
    if cmd == "group":
        if t.kind not in ("one", "relation"):
            print(
                f"group needs one company or a family; {t.key} resolved to {t.kind}",
                file=sys.stderr,
            )
            return 1
        include = [s.strip() for s in args.include.split(",") if s.strip()]
        report = group_report(src, t, max(1, args.depth), include)
        if args.format == "json":
            print(json.dumps(report, indent=2, default=str))
        else:
            render_group(report)
        return 0
    if cmd == "bulk":
        return _bulk_cmd(src, t, args)
    return 2


def _target_view(t: Target) -> dict:
    return {
        "kind": t.kind,
        "key": t.key,
        "entities": t.entities,
        "candidates": t.candidates,
        "label": t.label,
        "relation": t.relation,
        "note": t.note,
    }


def _bulk_cmd(src: Sources, t: Target | None, args: argparse.Namespace) -> int:  # noqa: C901  three universe forms + three output formats
    names: list[str] = []
    unresolved: list[dict] = []
    if args.index:
        names, missing = _index_universe(src, args.index)
        unresolved = [{"token": i, "note": "isin not in entity_identifiers"} for i in missing]
    elif args.tickers_file:
        names, unresolved = _file_universe(src, Path(args.tickers_file))
    elif t is not None and t.kind == "one":
        names = t.entities
    elif t is not None and t.kind in ("set", "relation"):
        names = t.entities
    else:
        render_candidates(
            t or Target("unresolved", "empty", note="no query given"), "company_query.py bulk"
        )
        return 1
    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    rows = bulk_rows(src, names, fields)
    members = len(names)
    if args.summary:
        rows = summary_rows(rows)
    for u in unresolved:
        print(f"unresolved: {u['token']} — {u['note']}", file=sys.stderr)
    if args.format == "jsonl":
        for r in rows:
            print(json.dumps(r, default=str))
    elif args.format == "json":
        env: dict = {"count": len(rows), "unresolved": unresolved, "rows": rows}
        if args.summary:
            env["members"] = members
        print(json.dumps(env, default=str))
    else:
        cols: list[str] = []
        for r in rows:
            for k in r:
                if k not in cols:
                    cols.append(k)
        w = csv.writer(sys.stdout)
        w.writerow(cols)
        for r in rows:
            w.writerow([_fmt_cell(r.get(c)) for c in cols])
    return 0


if __name__ == "__main__":
    sys.exit(main())
