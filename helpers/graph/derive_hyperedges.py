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

S4 (--roles, ontology_convention_stack): participant-level n-ary facets
on the incidence store. ``hyper_incidences`` gains ``role`` /
``valid_from`` / ``valid_to`` (guarded ALTERs here — the canonical DDL
lives in migrate_to_graph_edges); event hyperedges get role-tagged
participants (acquirer/target for acquisition, partner/partner for jv —
unmapped types stay NULL, never confabulated), the observation date on
``hyper_edges.valid_from``, and an event-facet ``properties`` JSON
(period, date_precision, magnitude_raw + magnitude_numeric +
magnitude_unit — the raw string stays for audit, never identity). The
``events`` table REMAINS the canonical temporal spine (D-O2): event/jv
hyperedges are derived projections, re-converged here. A
reconciliation report (unresolved counterparties / duplicate
observations / conflicting stored sets / untagged types) prints BEFORE
any apply, dry-run or not.

Usage:
    python3 helpers/graph/derive_hyperedges.py             # dry-run summary
    python3 helpers/graph/derive_hyperedges.py --apply     # write hyperedges
    python3 helpers/graph/derive_hyperedges.py --roles     # + S4 facets + reconciliation report
    python3 helpers/graph/derive_hyperedges.py --verbose   # list every hyperedge
"""

from __future__ import annotations

import argparse
import json
import re
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


# Operator-curated company -> sub_sector classifications (D11, 2026-09-15).
# Version-controlled EDGE lane: same canonical precedence as the note
# `subsector:` field (map wins on conflict); revisited after D12 (NIC-2008).
COMPANY_SUB_SECTORS: dict[str, list[str]] = {
    "Paper_Products": ["Kuantum Papers"],
    "Computer_Hardware": [
        "DC Infotech Comm",
    ],
    "Digital_Platforms": [
        "Nazara Technologies",
    ],
    "Two_Wheelers": [
        "Harley-Davidson",
    ],
    "Brokers": [
        "5paisa Capital",
        "Angel One",
        "Dolat Algotech",
        "Emkay Global Financial Services",
        "Geojit Financial Services",
        "Motilal Oswal Financial Services",
        "SMC Global",
        "Anand Rathi Share and Stock Brokers",
        "Yes Securities",
        "Upstox",
    ],
    "Capital_Goods": [
        "John Cockerill India",
        "SKF India",
        "Timken India",
    ],
    "Cooperative_Banks": [
        "Cosmos Co-operative Bank",
    ],
    "Dealerships": [
        "CarTrade Tech",
        "Landmark Cars",
    ],
    "Depositories": [
        "Central Depository Services",
        "NSDL",
    ],
    "Design": [
        "Advanced Micro Devices",
        "Broadcom",
        "Intel Corporation",
        "NVIDIA Corporation",
        "Synaptics",
    ],
    "E&P": [
        "Oil India",
        "ONGC",
        "Saudi Aramco",
        "TotalEnergies SE",
    ],
    "Electrical_Equipment": [
        "Shivalik Bimetal Controls",
    ],
    "Exchanges": [
        "BSE",
        "NSE",
        "Multi Commodity Exchange of India (MCX)",
        "Indian Energy Exchange",
        "M1Xchange",
    ],
    "Flexible_Packaging": [
        "Cosmo First",
        "EPL",
        "Jindal Poly Films",
        "TCPL Packaging",
        "Uflex",
        "Huhtamaki Oyj",
    ],
    "Footwear": [
        "Bata India",
        "Campus Activewear",
        "Redtape",
        "Birkenstock",
        "Crocs",
    ],
    "Foreign_Banks": [
        "Standard Chartered",
    ],
    "Forge_Castings": [
        "MM Forgings",
        "Karbonsteel Engineering",
        "Nelcast",
        "PTC Industries",
        "Pitti Engineering",
        "Ramkrishna Forgings",
        "Frontier Springs",
    ],
    "Gas_Distribution": [
        "Petronet LNG",
        "Gujarat Energy",
        "Gujarat Gas",
    ],
    "General_Insurance": [
        "ICICI Lombard General Insurance",
        "The New India Assurance",
    ],
    "Gold_Loans": [
        "Muthoot Finance",
        "Manappuram Finance",
        "IIFL Finance",
    ],
    "HFC": [
        "Aavas Financiers",
        "Aptus Value Housing Finance",
        "Can Fin Homes",
        "GIC Housing Finance",
        "Home First Finance Company India",
        "LIC Housing Finance",
        "PNB Housing Finance",
        "Repco Home Finance",
        "Sammaan Capital",
    ],
    "Hydro": [
        "NHPC",
        "SJVN",
    ],
    "Hypermarkets": [
        "Avenue Supermarts DMart",
        "Shoppers Stop",
        "Spencers Retail",
        "V-Mart Retail",
        "Costco Wholesale",
        "Walmart",
        "Target",
    ],
    "Infra_Lending": [
        "Housing and Urban Development Corporation",
        "Indian Railway Finance",
        "Indian Renewable Energy Development Agency",
        "REC",
        "Power Finance Corporation",
    ],
    "Life_Insurance": [
        "Sun Life Financial",
    ],
    "Market_Infrastructure": [
        "KFin Technologies",
        "Computer Age Management Services",
    ],
    "Memory": [
        "Micron Technology",
    ],
    "Microfinance": [
        "CreditAccess Grameen",
        "Arman Financial Services",
        "Muthoot Microfin",
        "Spandana Sphoorty Financial",
    ],
    "Multi_Line": [
        "Bajaj Finance",
        "HDB Financial Services",
        "Magma Fincorp",
        "L&T Finance",
        "Tata Capital",
        "Shriram Finance",
        "JM Financial",
    ],
    "OTA": [
        "Airbnb",
        "EaseMyTrip (Easy Trip Planners)",
        "IRCTC",
        "Le Travenues Technology",
        "TBO Tek",
        "Yatra Online",
        "Easy Trip Planners",
    ],
    "Private_Sector": [
        "AU Small Finance Bank",
        "Axis Bank",
        "Bandhan Bank",
        "CSB Bank",
        "City Union Bank",
        "DCB Bank",
        "Dhanlaxmi Bank",
        "ESAF Small Finance Bank",
        "Equitas Small Finance Bank",
        "Federal Bank",
        "Fino Payments Bank",
        "HDFC Bank",
        "ICICI Bank",
        "IDFC First Bank",
        "IndusInd Bank",
        "Jammu & Kashmir Bank",
        "Jana Small Finance Bank",
        "Karnataka Bank",
        "Karur Vysya Bank",
        "Kotak Mahindra Bank",
        "RBL Bank",
        "South Indian Bank",
        "Suryoday Small Finance Bank",
        "Tamilnad Mercantile Bank",
        "Ujjivan Small Finance Bank",
        "Yes Bank",
    ],
    "Public_Sector": [
        "Bank of Baroda",
        "Bank of India",
        "Bank of Maharashtra",
        "Canara Bank",
        "Central Bank of India",
        "Indian Bank",
        "Indian Overseas Bank",
        "Punjab National Bank (PNB)",
        "Punjab Sind Bank",
        "State Bank of India",
        "UCO Bank",
        "Union Bank of India",
    ],
    "Publishing": [
        "D B Corp",
        "MPS",
        "Cyber Media Research & Services",
    ],
    "Ratings": [
        "CRISIL",
        "ICRA",
    ],
    "Recycling": [
        "Gravita India",
    ],
    "Refining": [
        "Bharat Petroleum Corporation",
        "Hindustan Petroleum",
        "Indian Oil Corporation",
        "Mangalore Refinery and Petrochemicals",
        "Reliance Industries",
    ],
    "Rigid_Packaging": [
        "Huhtamaki India",
        "Mold Tek Packaging",
        "Swashthik Plascon",
        "Time Technoplast",
        "Worth Peripherals",
    ],
    "Solar": [
        "Acme Solar Holdings",
        "Adani Green",
        "KPI Green Energy",
    ],
    "Specialty_Chemicals": [
        "Deepak Nitrite",
        "GHCL",
        "Shiv Texchem",
    ],
    "Telecom_Equipment": [
        "Sterlite Technologies",
        "Tejas Networks",
        "Telecommunications Consultants India",
        "HFCL",
    ],
    "Telecom_Services": [
        "Bharti Airtel",
        "Bharti Hexacom",
        "Tata Communications",
        "Vodafone Idea",
        "Railtel Corporation Of India",
    ],
    "Vehicle_Loans": [
        "Cholamandalam Investment and Finance Company",
        "Mahindra & Mahindra Financial Services",
        "Sundaram Finance",
        "Muthoot Capital Services",
    ],
}


def _company_path_map(conn) -> dict[str, str]:
    """company note path -> entity display name (the S5 frontmatter-read join)."""
    return {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT file_path, name FROM entities "
            "WHERE entity_type = 'company' "
            "AND file_path IS NOT NULL AND file_path != ''"
        )
    }


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


def extract_subsector_membership(
    root: Path = COMPANIES_DIR,
    path_to_name: dict[str, str] | None = None,
) -> dict[str, set[str]]:
    """Read the YAML ``subsector:`` field of company notes -> ``{value: members}``.

    D7 authored lane (company_subsector_authored_lane.md): the field is the
    operator-authored canonical classification (``Apparel_Retail`` — an
    existing sub_sector entity name; case/spacing-insensitive). Absent/null
    values are skipped, exactly like ``extract_industry_membership``. 0
    authored values live at landing — the lane is a no-op until authored.
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
        subsector = fields.get("subsector")
        if not subsector or not isinstance(subsector, str):
            continue  # covers subsector: null and absent
        try:
            rel = note.resolve().relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            rel = note.resolve().relative_to(root.resolve()).as_posix()
        company = path_to_name.get(rel) if path_to_name else note.stem
        if company is None:
            continue  # stray .md not backed by an entity
        out.setdefault(subsector.strip(), set()).add(company)
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
# S4: n-ary event facets (role / valid_from / valid_to on incidences)         #
# --------------------------------------------------------------------------- #
# Role vocabulary: (entity role, counterparty role) per event_type. ONLY the
# two types that carry counterparties live (acquisition 41, jv 69 — measured
# 2026-09-14); an unmapped type resolves to (None, None) — participants stay
# unlabeled rather than confabulated, and the reconciliation report names it.
_EVENT_ROLES: dict[str, tuple[str | None, str | None]] = {
    "acquisition": ("acquirer", "target"),
    "jv": ("partner", "partner"),
}

_INCIDENCE_FACETS: tuple[tuple[str, str], ...] = (
    ("role", "TEXT"),
    ("valid_from", "DATE"),
    ("valid_to", "DATE"),
)


def ensure_incidence_facets(conn) -> None:
    """Add role/valid_from/valid_to to ``hyper_incidences`` where missing.

    Mirrors the canonical DDL (migrate_to_graph_edges) column order via
    guarded ALTERs; idempotent, and a no-op on DBs without the table
    (never-block).
    """
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='hyper_incidences' AND type='table'"
        ).fetchone()
        is None
    ):
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hyper_incidences)")}
    for name, decl in _INCIDENCE_FACETS:
        if name not in cols:
            conn.execute(f"ALTER TABLE hyper_incidences ADD COLUMN {name} {decl}")  # noqa: S608 -- fixed identifier names
    conn.commit()


def _parse_magnitude(raw: str | None) -> tuple[float | None, str | None]:
    """Split a raw magnitude string into (numeric, unit).

    ``"USD 1,234.5 crore"`` -> ``(1234.5, "USD crore")`` — the first
    standalone numeric token becomes the number (currency prefixes like
    ``Rs`` are common), everything else collapses into the unit
    qualifier. Ranges (``10-12%``) and range members are NOT single
    magnitudes -> ``(None, None)``; the raw string is always preserved
    as ``magnitude_raw`` for audit.
    """
    if not raw or not raw.strip():
        return None, None
    m = re.search(r"(?<![\d.])(?<!-)(\d[\d,]*(?:\.\d+)?)(?!\d)(?!-[\d.])", raw)
    if not m:
        return None, None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return None, None
    unit = " ".join((raw[: m.start(1)] + raw[m.end(1) :]).split()) or None
    return num, unit


def collect_event_facets(conn) -> dict:
    """Role/facet projections for counterparty-carrying events (S4).

    Returns ``{"roles": {(label, member): role}, "props": {label: {...}},
    "valid_from": {label: date}}`` over the SAME event hyperedges
    collect_hyperedges builds (``{event_type}:{id}``). Absent events
    table / column → empty maps (fresh DBs skip, never-block).
    """
    facets: dict = {"roles": {}, "props": {}, "valid_from": {}}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    if not cols or "counterparty_entity" not in cols:
        return facets
    for eid, etype, ent, cp, date, period, prec, mag in conn.execute(
        "SELECT id, event_type, entity, counterparty_entity, event_date, "
        "period, date_precision, magnitude FROM events "
        "WHERE counterparty_entity IS NOT NULL"
    ):
        label = f"{etype}:{eid}"
        ent_role, cp_role = _EVENT_ROLES.get(etype, (None, None))
        if ent_role:
            facets["roles"][(label, ent)] = ent_role
        if cp_role:
            facets["roles"][(label, cp)] = cp_role
        num, unit = _parse_magnitude(mag)
        props = {
            k: v
            for k, v in {
                "period": period,
                "date_precision": prec,
                "magnitude_raw": mag,
                "magnitude_numeric": num,
                "magnitude_unit": unit,
            }.items()
            if v is not None
        }
        if props:
            facets["props"][label] = props
        if date:
            facets["valid_from"][label] = date
    return facets


def reconcile_events(conn) -> dict:
    """S4 pre-apply reconciliation report (never mutates).

    Counts what the role backfill WOULD consume: unresolved counterparty
    names (0 expected after S9's 110/110), duplicate observations (same
    event_type + participants + date across multiple rows — each row
    still gets its own hyperedge, one per observation; enumerated, not
    collapsed), conflicting stored hyperedges (existing event hyperedge
    whose member set != {entity, counterparty} — converged on apply),
    and counterparty-carrying types with no role mapping (participants
    stay unlabeled).
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    if not cols or "counterparty_entity" not in cols:
        return {
            "observations": 0,
            "unresolved": 0,
            "unresolved_names": [],
            "duplicate_groups": 0,
            "duplicate_rows": [],
            "conflicts": [],
            "untagged": {},
        }
    unresolved = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT counterparty FROM events "
            "WHERE counterparty IS NOT NULL AND counterparty != '' "
            "AND counterparty_entity IS NULL"
        )
    ]
    duplicates = [
        (r[0], r[1], r[2], r[3], r[4])
        for r in conn.execute(
            # pair normalized (min/max): an acquisition and its direction
            # flip are the same observation pair
            "SELECT event_type, min(entity, counterparty_entity), "
            "max(entity, counterparty_entity), event_date, COUNT(*) "
            "FROM events WHERE counterparty_entity IS NOT NULL "
            "GROUP BY 1, 2, 3, 4 HAVING COUNT(*) > 1 ORDER BY 5 DESC"
        )
    ]
    conflicts: list[str] = []
    has_hyper = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='hyper_edges' AND type='table'"
        ).fetchone()
        is not None
    )
    if has_hyper:
        for eid, etype, ent, cp in conn.execute(
            "SELECT id, event_type, entity, counterparty_entity FROM events "
            "WHERE counterparty_entity IS NOT NULL"
        ):
            label = f"{etype}:{eid}"
            stored = {
                r[0]
                for r in conn.execute(
                    "SELECT i.entity_name FROM hyper_incidences i "
                    "JOIN hyper_edges h ON h.id = i.edge_id "
                    "WHERE h.edge_type = 'event' AND h.label = ?",
                    (label,),
                )
            }
            # absent hyperedge = "to be created" (new_edges counts it), not a conflict
            if stored and stored != {ent, cp}:
                conflicts.append(label)
    untagged: dict[str, int] = {}
    for etype, n in conn.execute(
        "SELECT event_type, COUNT(*) FROM events WHERE counterparty_entity IS NOT NULL GROUP BY 1"
    ):
        if etype not in _EVENT_ROLES:
            untagged[etype] = n
    return {
        "observations": conn.execute(
            "SELECT COUNT(*) FROM events WHERE counterparty_entity IS NOT NULL"
        ).fetchone()[0],
        "unresolved": len(unresolved),
        "unresolved_names": sorted(unresolved)[:5],
        "duplicate_groups": len(duplicates),
        "duplicate_rows": [f"{t}:{e}↔{c}@{d} ×{n}" for t, e, c, d, n in duplicates[:5]],
        "conflicts": conflicts[:5],
        "untagged": untagged,
    }


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
    "Mortgage Finance": "HFC",
    "Oil & Gas Refining & Marketing": "Refining",
    "Financial Data & Stock Exchanges": "Exchanges",
    "Footwear & Accessories": "Footwear",
    "Auto & Truck Dealerships": "Dealerships",
    "Insurance - Diversified": "Multi_Line",
    "Travel Services": "OTA",
    "Discount Stores": "Hypermarkets",
}

SUB_SECTOR_ALIASES["Diagnostics & Research"] = "Pathology"  # fix placeholder

# D13 additions (2026-09-15, operator-approved worklist triage round 2):
# 7 labels onto EXISTING nodes + 19 onto the new D13 sub_sector nodes
# (taxonomy edits in build_sector_hierarchy.SUB_CATEGORIES). Closes 48 of
# the 407 no-sub_sector authored companies; the remaining ~354 lack a note
# `industry:` field entirely and ride D18 (yfinance enrichment lane).
SUB_SECTOR_ALIASES.update(
    {
        # onto existing nodes
        "Communication Equipment": "Telecom_Equipment",
        "Telecom Services": "Telecom_Services",
        "Insurance Brokers": "Brokers",
        "Real Estate Services": "Real_Estate_Development",
        "Chemicals": "Specialty_Chemicals",
        "Oil & Gas E&P": "E&P",
        "Rental & Leasing Services": "Construction_Equipment",
        # onto the 19 new D13 nodes
        "Electronic Components": "Electronic_Components",
        "Specialty Business Services": "Facility_Services",
        "Education & Training Services": "Training_Services",
        "Medical Devices": "Medical_Devices",
        "Business Equipment & Supplies": "Business_Equipment",
        "Tobacco": "Tobacco",
        "Health Information Services": "Health_IT",
        "Computer Hardware": "Computer_Hardware",
        "Lumber & Wood Production": "Wood_Products",
        "Pollution & Treatment Controls": "Water_Treatment",
        "Grocery Stores": "Grocery",
        "Medical Instruments & Supplies": "Medical_Instruments",
        "Specialty Retail": "Specialty_Retail",
        "Infrastructure Operations": "Infrastructure_Operations",
        "Real Estate - Diversified": "Diversified_Real_Estate",
        "Scientific & Technical Instruments": "Instruments",
        "Credit Services": "Credit_Cards",
        "Advertising Agencies": "Advertising",
        "Staffing & Employment Services": "Staffing",
        "Oil & Gas Equipment & Services": "Oilfield_Services",
    }
)

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


def _norm_subsector(value: str) -> str:
    """Canonical lookup key: lowercase, spaces/hyphens -> underscores."""
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def derive_sub_sectors(
    hyper: dict[str, dict[str, set[str]]],
    valid_entities: set[str],
    authored: dict[str, set[str]] | None = None,
    sub_sector_entities: set[str] | None = None,
) -> tuple[dict[str, set[str]], list[tuple[str, int, str]], list[tuple[str, int]]]:
    """S11 union + D7 authored precedence: build sub_sector member groups.

    Returns ``(sub_sector_groups, unmapped, unmapped_authored)`` where
    sub_sector_groups is ``{sub_sector_name: set(company members)}``
    (union across every industry label mapping there), unmapped is
    ``[(label, n_members, suggestion)]`` sorted by member count desc, and
    unmapped_authored is ``[(value, n_members)]`` for hand-authored values
    that resolve to no existing sub_sector entity (warned + worklisted;
    membership stays alias-derived — hand typos must be visible, not
    pipeline-fatal). A stale alias whose target entity no longer exists
    raises before anything is written (map rides in version control).

    D7 precedence: an authored ``subsector:`` assignment is CANONICAL for
    that company — the company is removed from every alias-derived group
    and placed only in the authored one.
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

    unmapped_authored: list[tuple[str, int]] = []
    if authored and sub_sector_entities:
        canon = {}
        for e in sub_sector_entities:
            canon.setdefault(_norm_subsector(e), e)
        for value, members in sorted(authored.items()):
            canonical = canon.get(_norm_subsector(value))
            if canonical is None:
                unmapped_authored.append((value, len(members)))
                continue  # worklist; membership stays alias-derived
            groups.setdefault(canonical, set())
            for m in members:
                for members_of in groups.values():
                    members_of.discard(m)  # authored is canonical: exclusive
                groups[canonical].add(m)
        unmapped_authored.sort(key=lambda x: -x[1])
    return groups, unmapped, unmapped_authored


def _write_subsector_worklist(
    unmapped: list[tuple[str, int, str]],
    unmapped_authored: list[tuple[str, int]] | None = None,
) -> Path:
    """Mirror counterparty_worklist: JSON under findata/Misc/, count-sorted.

    D7: ``unmapped_authored`` lists hand-authored ``subsector:`` note values
    that resolve to no existing sub_sector entity (visible, not fatal).
    """
    wl = _REPO_ROOT / "findata" / "Misc" / "subsector_worklist.json"
    wl.parent.mkdir(parents=True, exist_ok=True)
    wl.write_text(
        json.dumps(
            {
                "unmapped": [
                    {"label": lbl, "members": n, "suggestion": s} for lbl, n, s in unmapped
                ],
                "count": len(unmapped),
                "unmapped_authored": [
                    {"value": v, "members": n} for v, n in unmapped_authored or []
                ],
                "hint": "mapped labels live in derive_hyperedges.SUB_SECTOR_ALIASES; "
                "new sub_sector nodes are an operator taxonomy decision; authored "
                "company-level overrides ride the `subsector:` note field (D7)",
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
    facets: dict | None = None,
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

    ``facets`` (S4, from :func:`collect_event_facets`, ``--roles``):
    per-incidence roles are written on INSERT and converged on existing
    rows (a mapped participant whose stored role drifted — or an untagged
    one carrying a stale role — is UPDATEd; pass ``facets=None`` to leave
    roles untouched), event-hyperedge ``properties`` gain the event-facet
    keys, and ``valid_from`` converges to the observation date.
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
                        # base properties every hyperedge carries; S4 event
                        # facets merge in (period/date_precision/magnitude)
                        props = {
                            "n_members": len(members),
                            "upstream_types": _upstream_types(hyper_type),
                        }
                        if facets:
                            props.update(facets.get("props", {}).get(label, {}))
                        props_json = json.dumps(props, sort_keys=True)
                        # refresh properties on existing hyperedges too
                        # (upstream_types was [] for industry before S8 —
                        # backfills converge stale props in one pass)
                        conn.execute(
                            "UPDATE hyper_edges SET properties = ? "
                            "WHERE edge_type = ? AND label = ? AND properties != ?",
                            (props_json, hyper_type, label, props_json),
                        )
                        if facets:
                            vf = facets.get("valid_from", {}).get(label)
                            if vf:
                                conn.execute(
                                    "UPDATE hyper_edges SET valid_from = ? "
                                    "WHERE edge_type = ? AND label = ? AND valid_from IS NOT ?",
                                    (vf, hyper_type, label, vf),
                                )
                        if is_new:
                            source_ref = f"derive:hyperedges:{_upstream(hyper_type)}"
                            vf_new = facets.get("valid_from", {}).get(label) if facets else None
                            cur = conn.execute(
                                "INSERT OR IGNORE INTO hyper_edges "
                                "(edge_type, label, properties, source_ref, valid_from) "
                                "VALUES (?, ?, ?, ?, ?)",
                                (hyper_type, label, props_json, source_ref, vf_new),
                            )
                            if cur.rowcount:
                                new_edges += 1
                        edge_id = conn.execute(
                            "SELECT id FROM hyper_edges WHERE edge_type = ? AND label = ?",
                            (hyper_type, label),
                        ).fetchone()[0]
                        for member in sorted(members):
                            w = (weights or {}).get((hyper_type, label, member))
                            role = facets.get("roles", {}).get((label, member)) if facets else None
                            cur = conn.execute(
                                "INSERT OR IGNORE INTO hyper_incidences "
                                "(edge_id, entity_name, weight, role) VALUES (?, ?, ?, ?)",
                                (edge_id, member, w, role),
                            )
                            if cur.rowcount:
                                new_inc += 1  # genuinely new membership
                            else:
                                # existing member: converge the intensity
                                # (S8 re-runs after new quotes) and, in
                                # --roles mode, the participant role (S4 —
                                # untagged participants converge to NULL)
                                if w is not None:
                                    conn.execute(
                                        "UPDATE hyper_incidences SET weight = ? "
                                        "WHERE edge_id = ? AND entity_name = ?",
                                        (w, edge_id, member),
                                    )
                                if facets:
                                    conn.execute(
                                        "UPDATE hyper_incidences SET role = ? "
                                        "WHERE edge_id = ? AND entity_name = ? AND role IS NOT ?",
                                        (role, edge_id, member, role),
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


def _cli(argv: list[str] | None = None) -> int:  # noqa: C901  # arg-dispatch CLI
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    dcli.add_derive_args(
        p,
        dcli.DeriveArgsSpec(
            apply_help="Write hyper_edges/hyper_incidences (default: dry-run summary only).",
            stale_help="Unused (source is graph_edges, no note watch path) — kept for CLI parity.",
            corpus=False,
        ),
    )
    p.add_argument(
        "--roles",
        action="store_true",
        help="S4: write/converge participant roles + event facets on incidences "
        "(reconciliation report prints before any apply)",
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
        facets = None
        if args.roles:
            ensure_incidence_facets(conn)
            rec = reconcile_events(conn)
            dup = (
                f"{rec['duplicate_groups']} group(s): " + ", ".join(rec["duplicate_rows"])
                if rec["duplicate_groups"]
                else "0"
            )
            print(
                f"S4 event reconciliation: {rec['observations']} observations, "
                f"{rec['unresolved']} unresolved"
                + (f" ({', '.join(rec['unresolved_names'])})" if rec["unresolved"] else "")
                + f", duplicates {dup}"
                + f", {len(rec['conflicts'])} conflicting stored set(s)"
                + (f" ({', '.join(rec['conflicts'])})" if rec["conflicts"] else "")
                + (
                    f", untagged types {rec['untagged']}"
                    if rec["untagged"]
                    else ", all types role-mapped"
                )
            )
            facets = collect_event_facets(conn)
        hyper, weights = collect_hyperedges(conn)
        valid = {r[0] for r in conn.execute("SELECT name FROM entities")}
        # D7 authored lane: `subsector:` note field is canonical per company.
        # D11 operator lane: the version-controlled COMPANY_SUB_SECTORS map
        # rides the same precedence (map wins on conflict — no note writes).
        p2n = _company_path_map(conn)
        authored = extract_subsector_membership(path_to_name=p2n) if p2n else {}
        for sub, members in COMPANY_SUB_SECTORS.items():
            for m in members:
                authored.setdefault(sub, set()).add(m)
        ss_entities = {
            r[0] for r in conn.execute("SELECT name FROM entities WHERE entity_type='sub_sector'")
        }
        sub_groups, unmapped, unmapped_authored = derive_sub_sectors(
            hyper, valid, authored=authored, sub_sector_entities=ss_entities
        )
        if sub_groups or unmapped:
            hyper["sub_sector"] = sub_groups
            n_new_members = sum(len(m) for m in sub_groups.values())
            n_authored = sum(len(m) for m in authored.values())
            print(
                f"S11 sub-sector map: {len(SUB_SECTOR_ALIASES) - 0} aliases -> "
                f"{len(sub_groups)} sub_sector hyperedges ({n_new_members} members); "
                f"{len(unmapped)} industry labels unmapped"
                + (
                    " (top: " + ", ".join(f"{lbl} ({n})" for lbl, n, _ in unmapped[:3]) + ")"
                    if unmapped
                    else ""
                )
                + (
                    f"; D7 authored: {n_authored} assignment(s)"
                    f" ({sum(len(v) for v in COMPANY_SUB_SECTORS.values())} map)"
                    + (
                        f", {len(unmapped_authored)} unknown value(s) worklisted"
                        if unmapped_authored
                        else ""
                    )
                    if authored
                    else ""
                )
            )
            if args.apply and (unmapped or unmapped_authored):
                wl = _write_subsector_worklist(unmapped, unmapped_authored)
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

        stats = apply_hyperedges(hyper, weights, conn=conn, dry_run=not args.apply, facets=facets)
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
