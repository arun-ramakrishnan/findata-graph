#!/usr/bin/env python3
"""Derive ``hyper_edges``/``hyper_incidences`` from existing dyadic membership
edges (hypergraph_incidence_hyx, S2 — Phase 0 backfill).

WHY THIS EXISTS
---------------
The graph layer is strictly pairwise (``graph_edges``: two FK columns), so
every set-valued fact is stored as a materialised star of dyads: a sector IS
its member companies as 1,165 ``part_of`` rows, an edition IS its co-mentioned
set as C(n,2) ``co_mentioned_in`` pairs. That destroys the set identity —
"which entity-sets recur across editions" is unanswerable from the dyads
(``doc/local/evaluations/hyper_graph_assessment.md`` §6.1). The incidence
tables store the star expansion losslessly; dyadic projections stay what they
are (nothing existing is rewritten).

Five SQL regroupings of existing dyads (no note re-scan) plus one frontmatter
read (S5):

  ================  ==================  =======================================
  hyper edge_type   upstream source     grouping key
  ================  ==================  =======================================
  sector            part_of             target (the sector)
  theme             exposed_to          target (the theme)
  country           listed_in           target (the country)
  group             same_group          properties -> '$.group' (both endpoints
                                        are members — symmetric dyads)
  edition           co_mentioned_in     properties -> '$.edition' (both
                                        endpoints are members)
  industry          note YAML field     `industry:` written by
                                        enrich_from_yfinance (816 non-null /
                                        117 labels live; capture-ledger #1)
  ================  ==================  =======================================

Idempotent via ``UNIQUE(edge_type, label)`` + ``PRIMARY KEY(edge_id,
entity_name)`` — re-running inserts only what changed. No ``--stale-only``
gate (mirrors derive_countries.py): the source is ``graph_edges`` itself
(DB-derived), so there is no note watch path to gate on.

Usage:
    python3 helpers/graph/derive_hyperedges.py             # dry-run summary
    python3 helpers/graph/derive_hyperedges.py --apply     # write hyperedges
    python3 helpers/graph/derive_hyperedges.py --verbose   # list every hyperedge
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# sys.path bootstrap so this works both as `python3 helpers/graph/...` (the
# Makefile form) and as a package import. Mirrors derive_themes.py.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.core.frontmatter import split_frontmatter, yaml_safe_load  # noqa: E402
from helpers.graph import derive_cli as dcli  # noqa: E402  # S6 shared CLI scaffold

COMPANIES_DIR = _REPO_ROOT / "findata" / "Companies"

# --------------------------------------------------------------------------- #
# Source registry                                                              #
# --------------------------------------------------------------------------- #
# Each source regroups one dyadic edge family into hyperedges.
#   mode "star"     — member = source, label = target (directed membership
#                     dyads; the category is the hyperedge).
#   mode "symmetric"— label = a JSON property key of the dyad, members = BOTH
#                     endpoints (peer dyads: same_group, co_mentioned_in).
_STAR_SOURCES: list[tuple[str, str]] = [
    ("sector", "part_of"),
    ("theme", "exposed_to"),
    ("country", "listed_in"),
]
_SYMMETRIC_SOURCES: list[tuple[str, str, str]] = [
    ("group", "same_group", "$.group"),
    ("edition", "co_mentioned_in", "$.edition"),
    # S10: regroup jv_with by the captured venture name where prose states
    # it (2/69 rows live; capture hook landed in extract_relations S10).
    ("jv", "jv_with", "$.venture"),
]


def extract_industry_membership(
    root: Path = COMPANIES_DIR,
    path_to_name: dict[str, str] | None = None,
) -> dict[str, set[str]]:
    """Read the YAML ``industry:`` field of company notes -> ``{label: members}``.

    The field is written by ``enrich_from_yfinance`` (Yahoo Finance industry
    string, e.g. "Auto Parts"); ``null``/absent values are skipped. This is a
    FRONTMATTER-field read (not prose, not tags) — the capture-ledger #1
    follow-up (proposal §6): 816 non-null values over 117 distinct labels
    measured 2026-09-13, a controlled vocabulary sitting unused as structure.

    ``path_to_name`` follows the derive_themes contract (posix-relative note
    path -> entity display name from the entities.file_path join); when None,
    the file stem is the name (test convenience).
    """
    out: dict[str, set[str]] = {}
    for note in sorted(root.rglob("*.md")):
        try:
            text = note.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        _dashes, yaml_body, _rest = split_frontmatter(text)
        if not yaml_body:
            continue
        try:
            fields = yaml_safe_load(yaml_body) or {}
        except Exception:  # noqa: BLE001, S112 — a malformed note must not kill the derive
            continue
        if not isinstance(fields, dict):
            continue
        industry = fields.get("industry")
        if not industry or not isinstance(industry, str):
            continue  # covers industry: null and absent
        # Key contract mirrors derive_themes: repo-relative posix path in
        # production; anything outside the repo (tmp vaults) falls back to
        # root-relative, so both prod path_to_name and test maps resolve.
        try:
            rel = note.resolve().relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            rel = note.resolve().relative_to(root.resolve()).as_posix()
        company = path_to_name.get(rel) if path_to_name else note.stem
        if company is None:
            continue  # stray .md not backed by an entity
        out.setdefault(industry.strip(), set()).add(company)
    return out


def resolve_counterparties(conn, *, dry_run: bool = True) -> tuple[int, int, list[str]]:
    """S9: resolve ``events.counterparty`` -> ``counterparty_entity`` (FK).

    Three tiers: exact ``entities.name`` -> NOCASE -> ``normalized_name``
    (spaces<->underscores). Returns ``(n_resolved, n_unresolved, unresolved_names)``.
    On apply, matched rows are UPDATEd in place and unresolved names go to
    ``findata/Misc/counterparty_worklist.json`` (country_worklist precedent).
    The column itself is created by migrate() step 2e (ALTER, idempotent);
    a missing column (fresh DBs) resolves nothing.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    if "counterparty_entity" not in cols:
        return 0, 0, []
    names = {r[0] for r in conn.execute("SELECT name FROM entities")}
    nocase = {r[0].lower() for r in conn.execute("SELECT name FROM entities")}
    norm = {
        (r[0] or "").replace("_", " ").lower()
        for r in conn.execute("SELECT normalized_name FROM entities")
    }
    resolved: list[tuple[str, str]] = []
    unresolved: list[str] = []
    for eid, cp in conn.execute(
        "SELECT id, counterparty FROM events "
        "WHERE counterparty IS NOT NULL AND counterparty != '' "
        "AND counterparty_entity IS NULL"
    ):
        if cp in names:
            resolved.append((cp, str(eid)))
        elif cp.lower() in nocase:
            resolved.append((cp, str(eid)))  # exact-name entities win on apply
        elif cp.replace("_", " ").lower() in norm:
            resolved.append((cp, str(eid)))
        else:
            unresolved.append(cp)
    if not dry_run and resolved:
        with conn:
            for cp, eid in resolved:
                conn.execute(
                    "UPDATE events SET counterparty_entity = "
                    "(SELECT name FROM entities WHERE name = ? "
                    "   OR lower(name) = lower(?) "
                    "   OR replace(coalesce(normalized_name,''),'_',' ') = "
                    "      replace(?,'_',' ')) WHERE id = ?",
                    (cp, cp, cp, int(eid)),
                )
        if unresolved:
            wl = _REPO_ROOT / "findata" / "Misc" / "counterparty_worklist.json"
            wl.parent.mkdir(parents=True, exist_ok=True)
            wl.write_text(
                json.dumps(
                    {"unresolved": sorted(set(unresolved)), "count": len(set(unresolved))},
                    indent=1,
                )
                + "\n"
            )
    return len(resolved), len(unresolved), sorted(set(unresolved))


# --------------------------------------------------------------------------- #
# S11: industry label -> canonical sub_sector (THEME_ALIASES precedent)       #
# --------------------------------------------------------------------------- #
# Yahoo `industry:` labels curated onto the 78 canonical sub_sector entities.
# Discipline: map only where the label IS the sub_sector (Indian-market
# reading); ambiguous labels stay unmapped and land in the worklist with a
# taxonomy suggestion (creating sub_sector nodes is an operator decision).
SUB_SECTOR_ALIASES: dict[str, str] = {
    "Agricultural Inputs": "Agrochemicals",  # dominant reading: fert/agrochem
    "Airlines": "Airlines",
    "Airports & Air Services": "Airport_Operations",
    "Aluminum": "Aluminum",
    "Apparel Manufacturing": "Apparel_Manufacturing",
    "Beverages - Brewers": "Food_Beverages",
    "Beverages - Non-Alcoholic": "Food_Beverages",
    "Beverages - Wineries & Distilleries": "Food_Beverages",
    "Broadcasting": "Broadcasting",
    "Coal": "Coal",
    "Confectioners": "Food_Processing",
    "Conglomerates": "Multi_Segment",
    "Copper": "Copper",
    "Diag & Research": "Pathology",  # placeholder; replaced below
    "Drug Manufacturers - General": "Formulations",
    "Drug Manufacturers - Specialty & Generic": "Formulations",
    "Entertainment": "Cinema",  # vault's entertainment names are exhibition
    "Farm Products": "Crop_Production",
    "Financial Conglomerates": "Multi_Segment",
    "Food Distribution": "Food_Beverages",
    "Gold": "Precious_Metals",
    "Household & Personal Products": "Personal_Care",
    "Integrated Freight & Logistics": "Specialized_Logistics",
    "Insurance - Life": "Life_Insurance",
    "Insurance - Property & Casualty": "General_Insurance",
    "Leisure": "Leisure",
    "Lodging": "Hotels",
    "Marine Shipping": "Transportation",
    "Medical Care Facilities": "Hospital_Chains",
    "Other Industrial Metals & Mining": "Non_Ferrous",
    "Packaged Foods": "Food_Processing",
    "Pharmaceutical Retailers": "Pharma_Retail",
    "Railroads": "Transportation",
    "REIT - Office": "Commercial",
    "Resorts & Casinos": "Resorts",
    "Solar": "Solar",
    "Specialty Chemicals": "Specialty_Chemicals",
    "Steel": "Iron_and_Steel",
    "Textile Manufacturing": "Traditional_Textiles",
    "Thermal Coal": "Coal",
    "Trucking": "Transportation",
    # S17 additions (2026-09-13): 25 Yahoo-industry -> sub_sector mappings from
    # the operator-approved worklist triage (members >= 5 + clean name), plus
    # the Aerospace & Defense alias onto the existing Defense facet.
    "Aerospace & Defense": "Aerospace",
    "Apparel Retail": "Apparel_Retail",
    "Asset Management": "Asset_Management",
    "Auto Manufacturers": "Automobiles",
    "Auto Parts": "Auto_Ancillary",
    "Biotechnology": "Biotech",
    "Building Materials": "Construction_Materials",
    "Building Products & Equipment": "Construction_Materials",
    "Consumer Electronics": "Consumer_Electronics",
    "Electrical Equipment & Parts": "Electrical_Equipment",
    "Engineering & Construction": "Infra_EPC",
    "Farm & Heavy Construction Machinery": "Construction_Equipment",
    "Furnishings, Fixtures & Appliances": "Consumer_Durables",
    "Information Technology Services": "IT_Services",
    "Insurance - Reinsurance": "Reinsurance",
    "Internet Content & Information": "Digital_Platforms",
    "Internet Retail": "Ecommerce",
    "Luxury Goods": "Luxury_Goods",
    "Real Estate - Development": "Real_Estate_Development",
    "Restaurants": "Restaurants",
    "Software - Application": "Software",
    "Software - Infrastructure": "Software",
    "Specialty Industrial Machinery": "Capital_Goods",
    "Utilities - Independent Power Producers": "Power_Generation",
    "Utilities - Regulated Electric": "Power_Generation",
    "Utilities - Regulated Gas": "Gas_Distribution",
}
SUB_SECTOR_ALIASES["Diagnostics & Research"] = "Pathology"  # fix placeholder

# Worklist hints for unmapped majors (suggested NEW sub_sector nodes).
SUB_SECTOR_SUGGESTIONS: dict[str, str] = {
    "Auto Parts": "new sub_sector Auto_Ancillary",
    "Auto Manufacturers": "new sub_sector Automobiles (no passenger-car node)",
    "Banks - Regional": "new sub_sector Banks, or Private_Sector/Public_Sector per company",
    "Banks - Diversified": "new sub_sector Banks",
    "Credit Services": "new sub_sector NBFC",
    "Mortgage Finance": "new sub_sector Housing_Finance",
    "Asset Management": "new sub_sector Asset_Management",
    "Capital Markets": "new sub_sector Capital_Markets",
    "Information Technology Services": "new sub_sector IT_Services",
    "Software - Application": "new sub_sector Software",
    "Software - Infrastructure": "new sub_sector Software",
    "Electrical Equipment & Parts": "new sub_sector Electrical_Equipment",
    "Specialty Industrial Machinery": "new sub_sector Capital_Goods",
    "Engineering & Construction": "new sub_sector Infra_EPC",
    "Farm & Heavy Construction Machinery": "new sub_sector Construction_Equipment",
    "Building Products & Equipment": "new sub_sector Building_Materials",
    "Building Materials": "new sub_sector Building_Materials",
    "Utilities - Regulated Electric": "new sub_sector Power_Generation",
    "Utilities - Independent Power Producers": "new sub_sector Power_Generation",
    "Utilities - Regulated Gas": "new sub_sector Gas_Distribution",
    "Utilities - Renewable": "Solar/Wind split per company",
    "Internet Content & Information": "new sub_sector Digital_Platforms",
    "Internet Retail": "new sub_sector Ecommerce",
    "Real Estate - Development": "Commercial/Residential split per company",
    "Insurance - Diversified": "Life/General split per company",
    "Insurance - Reinsurance": "new sub_sector Reinsurance",
    "Biotechnology": "new sub_sector Biotech",
    "Semiconductors": "new sub_sector Semiconductors",
}


def derive_sub_sectors(
    hyper: dict[str, dict[str, set[str]]], valid_entities: set[str]
) -> tuple[dict[str, set[str]], list[tuple[str, int, str]]]:
    """S11: union mapped industry hyperedges into sub_sector hyperedges.

    Returns ``(sub_sector_groups, unmapped)`` where sub_sector_groups is
    ``{sub_sector_name: set(company members)}`` (union across every industry
    label mapping there) and unmapped is ``[(label, n_members, suggestion)]``
    sorted by member count desc. A stale alias whose target entity no longer
    exists raises before anything is written (map rides in version control).
    """
    groups: dict[str, set[str]] = {}
    unmapped: list[tuple[str, int, str]] = []
    for label, members in sorted(hyper.get("industry", {}).items()):
        target = SUB_SECTOR_ALIASES.get(label)
        if target is None:
            unmapped.append(
                (
                    label,
                    len(members),
                    SUB_SECTOR_SUGGESTIONS.get(label, "no canonical target — taxonomy decision"),
                )
            )
            continue
        if target not in valid_entities:
            msg = f"S11 stale alias: industry {label!r} -> {target!r} not an entity"
            raise ValueError(msg)
        groups.setdefault(target, set()).update(members)
    unmapped.sort(key=lambda x: -x[1])
    return groups, unmapped


def _write_subsector_worklist(unmapped: list[tuple[str, int, str]]) -> Path:
    """Mirror counterparty_worklist: JSON under findata/Misc/, count-sorted."""
    wl = _REPO_ROOT / "findata" / "Misc" / "subsector_worklist.json"
    wl.parent.mkdir(parents=True, exist_ok=True)
    wl.write_text(
        json.dumps(
            {
                "unmapped": [
                    {"label": lbl, "members": n, "suggestion": s} for lbl, n, s in unmapped
                ],
                "count": len(unmapped),
                "hint": "mapped labels live in derive_hyperedges.SUB_SECTOR_ALIASES; "
                "new sub_sector nodes are an operator taxonomy decision",
            },
            indent=1,
        )
        + "\n"
    )
    return wl


def collect_hyperedges(
    conn, companies_dir: Path | None = None
) -> tuple[dict[str, dict[str, set[str]]], dict[tuple[str, str, str], float]]:
    """Regroup the source dyads into ``{edge_type: {label: set(members)}}``.

    Pure read; used by both dry-run and apply. Members are entity names as
    stored in graph_edges (FK-clean by construction).

    Returns ``(hyper, weights)``: hyper is ``{edge_type: {label: members}}``;
    weights carries the S8 per-incidence intensities
    ``{(edge_type, label, member): quote_count}`` (empty when the quotes
    table is absent/empty — fresh test DBs stay unweighted).

    S5: the `industry` source is a NOTE-FRONTMATTER read, not a dyad regroup —
    path->name resolution uses the same entities.file_path join as
    derive_themes, so a note without a backing entity is skipped.
    """
    out: dict[str, dict[str, set[str]]] = {}

    for hyper_type, dyad_type in _STAR_SOURCES:
        groups: dict[str, set[str]] = {}
        for member, label in conn.execute(
            "SELECT source, target FROM graph_edges WHERE edge_type = ?",  # noqa: S608
            (dyad_type,),
        ):
            groups.setdefault(label, set()).add(member)
        out[hyper_type] = groups

    for hyper_type, dyad_type, prop in _SYMMETRIC_SOURCES:
        groups = {}
        for label, member in conn.execute(
            "SELECT json_extract(properties, ?) AS label, source AS member "
            "FROM graph_edges WHERE edge_type = ? AND label IS NOT NULL",  # noqa: S608
            (prop, dyad_type),
        ):
            groups.setdefault(label, set()).add(member)
        for label, member in conn.execute(
            "SELECT json_extract(properties, ?) AS label, target AS member "
            "FROM graph_edges WHERE edge_type = ? AND label IS NOT NULL",  # noqa: S608
            (prop, dyad_type),
        ):
            groups.setdefault(label, set()).add(member)
        out[hyper_type] = groups

        # S8: quotes regroup — the quotes table IS an edition->participant
    # incidence (93 editions x 693 entities live). Members are company-type
    # only (sector/edition/super_sector quote rows are mis-captures); the
    # per-(edition, company) quote COUNT becomes the per-incidence weight
    # (HIF granularity 2, proposal §3 S1). Merged as a UNION into the
    # existing edition hyperedges: one edition = one participant set,
    # however captured. Concall-title as_of_edition values ride along —
    # the consumer's degeneracy guard drops the tiny ones.
    weights: dict[tuple[str, str, str], float] = {}
    try:
        for label, member, n_quotes in conn.execute(
            "SELECT q.as_of_edition AS label, q.entity AS member, COUNT(*) AS n "
            "FROM quotes q JOIN entities e ON e.name = q.entity "
            "WHERE e.entity_type = 'company' "
            "GROUP BY 1, 2"
        ):
            out.setdefault("edition", {}).setdefault(label, set()).add(member)
            weights[("edition", label, member)] = float(n_quotes)
    except sqlite3.OperationalError:
        pass  # no quotes table (fresh/test DBs) — unweighted edition sets

    # S9: event hyperedges — one PER EVENT ROW (participants = entity +
    # resolved counterparty). NOT grouped by (event_type, date, source_ref):
    # measured 2026-09-13, 'derive:events:edge-promotion' + NULL date lumps
    # 60 unrelated JVs together — provenance is not a happening key. Label
    # carries the event id for traceability; properties carry the readable
    # pair. Requires counterparty_entity (migrate 2e) — fresh DBs skip.
    try:
        for eid, etype, ent, cp_ent in conn.execute(
            "SELECT id, event_type, entity, counterparty_entity FROM events "
            "WHERE counterparty_entity IS NOT NULL"
        ):
            out.setdefault("event", {})[f"{etype}:{eid}"] = {ent, cp_ent}
    except sqlite3.OperationalError:
        pass  # events table absent (fresh test DBs)

    # S5: industry — frontmatter field regroup (path->name join). Skipped
    # entirely when no company carries a resolvable file_path (fresh/test
    # DBs): nothing to resolve means no scan, so synthetic fixtures stay hermetic.
    path_to_name = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT file_path, name FROM entities "
            "WHERE entity_type = 'company' "
            "AND file_path IS NOT NULL AND file_path != ''"
        )
    }
    out["industry"] = (
        extract_industry_membership(root=companies_dir or COMPANIES_DIR, path_to_name=path_to_name)
        if path_to_name
        else {}
    )

    return out, weights


def apply_hyperedges(  # noqa: C901 — per-edge_type DDL + dry-run/apply branches mirror the store schema
    hyper: dict[str, dict[str, set[str]]],
    weights: dict[tuple[str, str, str], float] | None = None,
    *,
    conn=None,
    dry_run: bool = True,
) -> dict[str, tuple[int, int]]:
    """Upsert the collected hyperedges; return ``{edge_type: (new_edges,
    new_incidences)}`` per source.

    Idempotent: ``INSERT OR IGNORE`` against both uniqueness keys, so a
    re-run reports 0/0 when nothing upstream changed (S8 weight refreshes on
    existing members are not counted as new). ``weights`` supplies
    per-incidence intensities; members without one get NULL. Membership
    shrinkage
    (a company leaving a category) is NOT compacted here — the backfill is
    additive; compaction belongs to a future full-refresh mode (proposal
    §3 S2, honest-scope note).
    """
    own_conn = conn is None
    if own_conn:
        conn = connect()

    stats: dict[str, tuple[int, int]] = {}
    try:
        existing_edges = {
            (et, label) for et, label in conn.execute("SELECT edge_type, label FROM hyper_edges")
        }
        with conn:
            for hyper_type, groups in hyper.items():
                new_edges = 0
                new_inc = 0
                for label, members in sorted(groups.items()):
                    is_new = (hyper_type, label) not in existing_edges
                    if dry_run and is_new:
                        new_edges += 1
                        new_inc += len(members)
                        continue

                    if not dry_run:
                        # refresh properties on existing hyperedges too
                        # (upstream_types was [] for industry before S8 —
                        # backfills converge stale props in one pass)
                        conn.execute(
                            "UPDATE hyper_edges SET properties = ? "
                            "WHERE edge_type = ? AND label = ? AND properties != ?",
                            (
                                json.dumps(
                                    {
                                        "n_members": len(members),
                                        "upstream_types": _upstream_types(hyper_type),
                                    },
                                    sort_keys=True,
                                ),
                                hyper_type,
                                label,
                                json.dumps(
                                    {
                                        "n_members": len(members),
                                        "upstream_types": _upstream_types(hyper_type),
                                    },
                                    sort_keys=True,
                                ),
                            ),
                        )
                        if is_new:
                            props = json.dumps(
                                {
                                    "n_members": len(members),
                                    "upstream_types": _upstream_types(hyper_type),
                                },
                                sort_keys=True,
                            )
                            source_ref = f"derive:hyperedges:{_upstream(hyper_type)}"
                            cur = conn.execute(
                                "INSERT OR IGNORE INTO hyper_edges "
                                "(edge_type, label, properties, source_ref) "
                                "VALUES (?, ?, ?, ?)",
                                (hyper_type, label, props, source_ref),
                            )
                            if cur.rowcount:
                                new_edges += 1
                        edge_id = conn.execute(
                            "SELECT id FROM hyper_edges WHERE edge_type = ? AND label = ?",
                            (hyper_type, label),
                        ).fetchone()[0]
                        for member in sorted(members):
                            w = (weights or {}).get((hyper_type, label, member))
                            cur = conn.execute(
                                "INSERT OR IGNORE INTO hyper_incidences "
                                "(edge_id, entity_name, weight) VALUES (?, ?, ?)",
                                (edge_id, member, w),
                            )
                            if cur.rowcount:
                                new_inc += 1  # genuinely new membership
                            elif w is not None:
                                # existing member: refresh the intensity only
                                # (S8 re-runs after new quotes; not new rows)
                                conn.execute(
                                    "UPDATE hyper_incidences SET weight = ? "
                                    "WHERE edge_id = ? AND entity_name = ?",
                                    (w, edge_id, member),
                                )
                    else:
                        # dry-run, edge exists: count only missing incidences
                        edge_id = conn.execute(
                            "SELECT id FROM hyper_edges WHERE edge_type = ? AND label = ?",
                            (hyper_type, label),
                        ).fetchone()[0]
                        n_have = conn.execute(
                            "SELECT COUNT(*) FROM hyper_incidences WHERE edge_id = ?",
                            (edge_id,),
                        ).fetchone()[0]
                        new_inc += max(0, len(members) - n_have)
                stats[hyper_type] = (new_edges, new_inc)
    finally:
        if own_conn:
            conn.close()
    return stats


def _upstream_types(hyper_type: str) -> list[str]:
    """All upstream sources contributing to an edge_type (S8: edition has two)."""
    if hyper_type == "industry":
        return ["frontmatter_industry"]  # note YAML field, not a dyad family
    if hyper_type == "sub_sector":
        return ["industry"]  # S11: derived label map, not a dyad family
    if hyper_type == "edition":
        return ["co_mentioned_in", "quotes"]  # S8: union of both capture paths
    for ht, t in _STAR_SOURCES + [(ht, t) for ht, t, _ in _SYMMETRIC_SOURCES]:
        if ht == hyper_type:
            return [t]
    return [hyper_type]


def _upstream(hyper_type: str) -> str:
    return "+".join(_upstream_types(hyper_type))


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    dcli.add_derive_args(
        p,
        dcli.DeriveArgsSpec(
            apply_help="Write hyper_edges/hyper_incidences (default: dry-run summary only).",
            stale_help="Unused (source is graph_edges, no note watch path) — kept for CLI parity.",
            corpus=False,
        ),
    )
    args = p.parse_args(argv)

    conn = connect()
    try:
        n_ok, n_bad, names_bad = resolve_counterparties(conn, dry_run=not args.apply)
        if n_ok or n_bad:
            mode = "APPLY" if args.apply else "DRY-RUN"
            print(
                f"S9 counterparty resolution ({mode}): {n_ok} resolved, "
                f"{n_bad} unresolved{'; ' + ', '.join(names_bad[:5]) if names_bad else ''}"
            )
        hyper, weights = collect_hyperedges(conn)
        valid = {r[0] for r in conn.execute("SELECT name FROM entities")}
        sub_groups, unmapped = derive_sub_sectors(hyper, valid)
        if sub_groups or unmapped:
            hyper["sub_sector"] = sub_groups
            n_new_members = sum(len(m) for m in sub_groups.values())
            print(
                f"S11 sub-sector map: {len(SUB_SECTOR_ALIASES) - 0} aliases -> "
                f"{len(sub_groups)} sub_sector hyperedges ({n_new_members} members); "
                f"{len(unmapped)} industry labels unmapped"
                + (
                    " (top: " + ", ".join(f"{lbl} ({n})" for lbl, n, _ in unmapped[:3]) + ")"
                    if unmapped
                    else ""
                )
            )
            if args.apply and unmapped:
                wl = _write_subsector_worklist(unmapped)
                print(f"worklist: {wl.relative_to(_REPO_ROOT)}")
        n_types = sum(len(g) for g in hyper.values())
        n_members = sum(len(m) for g in hyper.values() for m in g.values())
        print(
            f"sources regrouped: {n_types} hyperedges, {n_members} memberships "
            f"from {len(_STAR_SOURCES)} star + {len(_SYMMETRIC_SOURCES)} symmetric dyad families + industry frontmatter"
        )
        if args.verbose:
            for hyper_type, groups in hyper.items():
                for label, members in sorted(groups.items()):
                    print(f"{hyper_type}\t{label}\t{len(members)} members")

        stats = apply_hyperedges(hyper, weights, conn=conn, dry_run=not args.apply)
        print(
            f"{'source':<10} {'new_edges':>10} {'new_incidences':>15}"
            + (f"  ({len(weights)} weighted)" if weights else "")
        )
        for hyper_type, (new_edges, new_inc) in sorted(stats.items()):
            print(f"{hyper_type:<10} {new_edges:>10} {new_inc:>15}")
        mode = "APPLY" if args.apply else "DRY-RUN (use --apply to write)"
        total_e = sum(e for e, _ in stats.values())
        total_i = sum(i for _, i in stats.values())
        print(f"{mode}: {total_e} new hyperedges, {total_i} new incidences")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
