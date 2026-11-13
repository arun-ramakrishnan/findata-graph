#!/usr/bin/env python3
"""Derive ``hyper_edges``/``hyper_incidences`` from existing dyadic membership
edges (hypergraph_incidence_hyx, S2 — Phase 0 backfill).

WHY THIS EXISTS
---------------
The graph layer is strictly pairwise (``graph_edges``: two FK columns), so
every set-valued fact is stored as a materialised star of dyads: an edition IS
its co-mentioned set as C(n,2) ``co_mentioned_in`` pairs. That destroys the set
identity — "which entity-sets recur across editions" is unanswerable from the
dyads (``doc/local/evaluations/hyper_graph_assessment.md`` §6.1). The
incidence tables store the star expansion losslessly; dyadic projections stay
what they are (nothing existing is rewritten).

**The ``sector`` family is the exception and the destination.** It USED to be
regrouped from ``part_of`` here, which made the incidence store a copy of a
copy: a single-valued dyad cannot express dual sector membership (Kuantum
Papers holds Agriculture AND Packaging as hyperedges, one ``part_of`` row), so
the derive silently flattened the richer representation. part_of/has_company
retirement (2026-10-19) deleted the dyads and made the sector hyperedges
CANONICAL: sector has no upstream source, is skipped by the regroup pass, and
``helpers/graph/query.py`` projects the pair back out
(``_materialise_sector_membership``) for every dyadic reader.

Four SQL regroupings of existing dyads (no note re-scan) plus one frontmatter
read (S5):

  ================  ==================  =======================================
  hyper edge_type   upstream source     grouping key
  ================  ==================  =======================================
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

``sector`` is deliberately absent: see the canonical-store note above.

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
from collections import Counter
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
_STAR_SOURCES: list[tuple[str, str, int | None]] = [
    # (family, dyad edge type, min portfolio size k). k=None = threshold-free
    # (every label becomes a hyperedge); k>=2 drops sub-threshold portfolios
    # in the regroup (star_hyperedge_consumer_wiring S0, 2026-11-08 — the
    # k>=3 shapes the hgx arc recorded were never applied before this).
    # part_of/has_company retirement (2026-10-19): the sector family is
    # CANONICAL here (the dyad pair that used to feed it is retired), so
    # sector is derived from nothing and skipped by the regroup pass —
    # deleting it from this list is what makes the store the source of
    # truth rather than a copy of one.
    ("theme", "exposed_to", None),
    ("country", "listed_in", None),
    # hgx_over_dyads S2 (2026-10-30): index membership joins the star lane —
    # 6,615 dyads → 57 hyperedges (116× compression), genuinely
    # one-directional (company → index) so the symmetric regroup was
    # rejected. Threshold-free: each of the 57 indexes is genuine standing
    # structure (753 members sit in ≥2 of them). The dyad type stays
    # excluded from the DYADIC centrality projection
    # (EDGE_TYPES_EXCLUDED_FROM_CENTRALITY, hub-noise ruling); the
    # hyperedge family is a separate lane whose s-walk centrality
    # measured healthy (hgx_over_dyads §2.5).
    ("index", "listed_on_index", None),
    # hgx_over_dyads S4 (2026-10-31): tree/portfolio families join the star
    # lane at k≥3 — one parent → many children; one buyer → many
    # suppliers. The threshold is what keeps context-pack facts signal-
    # dense (sub-threshold portfolios duplicate the dyadic facts
    # context_pack already renders) and keeps the centrality lanes free
    # of the hub noise the hgx thresholds were designed to exclude.
    # Deferred from the S2/S3 wave for lack of a consumer; registered +
    # applied as standing structure — OPT-IN for centrality lanes (index
    # doctrine: run --families deliberately; wired into recompute-hyper by
    # star_hyperedge_consumer_wiring S2).
    ("subsidiary", "subsidiary_of", 3),
    ("supply", "supplier_to", 3),
]
_SYMMETRIC_SOURCES: list[tuple[str, str, str]] = [
    ("group", "same_group", "$.group"),
    # S5 (polyadic_capture_edge_fidelity): the edition family left the dyad
    # regroup — same retirement shape as sector above. The co_mentioned_in
    # dyads are retired from graph_edges; the family is the UNION of the
    # co_mention_editions staging table (newsletter lane) and the quotes
    # table (S8), collected in collect() below. Deleting this entry is what
    # makes the store the source of truth rather than a copy of the dyads.
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
    "Two_Wheelers": [
        "Harley-Davidson",
    ],
    "Brokers": [
        "5paisa Capital",
        "Angel One",
        "Dolat Algotech",
        "Emkay Global Financial Services",
        "Geojit Financial Services",
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
    ],
    "Asset_Management": [
        "JM Financial",
        "Motilal Oswal Financial Services",
    ],
    "Gaming": [
        "Nazara Technologies",
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


def _load_counterparty_aliases(path: Path | None = None) -> dict[str, str]:
    """Curated S9 aliases: ``{raw_counterparty: canonical_entity_name}``.

    Loaded only when the file exists; a malformed file is a hard error
    (curation is a tracked edit — fail loudly, same doctrine as family
    seeds). Targets are validated against entities at resolve time, so a
    stale alias is dropped + reported, never minted.
    """
    p = path or COUNTERPARTY_ALIASES_PATH
    try:
        doc = json.loads(p.read_text())
    except FileNotFoundError:
        return {}
    aliases = doc.get("aliases")
    if not isinstance(aliases, dict):
        raise ValueError(f"counterparty aliases {p}: 'aliases' must be an object")
    for raw, target in aliases.items():
        if not isinstance(raw, str) or not isinstance(target, str) or not raw.strip():
            raise ValueError(f"counterparty aliases {p}: bad entry {raw!r}")
    return aliases


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
    aliases = _load_counterparty_aliases()
    # alias targets must resolve to a stored entity; stale entries are
    # dropped + reported (worklist), never minted (same doctrine as family seeds)
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
            target = aliases.get(cp)
            if target is not None and (target in names or target.lower() in nocase):
                resolved.append((target, str(eid)))
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
    # Worklist rewrite is independent of `resolved`: a run can have
    # unresolved-only rows (nothing auto-resolves), or none at all while
    # the stale worklist still lists entries — either way an apply must
    # converge the file so fresh parking takes effect (S4 read-back).
    if not dry_run:
        from helpers.core.review_kit import latest_action_by

        parked = latest_action_by(
            _REPO_ROOT / "outputs" / "worklist_parking" / "counterparty" / "journal.jsonl",
            key_field="name",
        )
        fresh = sorted(cp for cp in set(unresolved) if cp not in parked)
        wl = _REPO_ROOT / "findata" / "Misc" / "counterparty_worklist.json"
        wl.parent.mkdir(parents=True, exist_ok=True)
        wl.write_text(
            json.dumps(
                {"unresolved": fresh, "count": len(fresh), "parked": len(parked)},
                indent=1,
            )
            + "\n"
        )
    return len(resolved), len(unresolved), sorted(set(unresolved))


# --------------------------------------------------------------------------- #
# S4: n-ary event facets (role / valid_from / valid_to on incidences)         #
# --------------------------------------------------------------------------- #
# Role vocabulary is OWNED by derive_event_participants (the store that
# materialises it, person_ontology_spine S4) — imported, not re-declared,
# so the facets and the store can never disagree. An unmapped type
# resolves to (None, None) — participants stay unlabeled rather than
# confabulated, and the reconciliation report names it.
from helpers.graph.derive_event_participants import EVENT_ROLE_BY_TYPE as _EVENT_ROLES  # noqa: E402

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


def collect_event_facets(conn) -> dict:  # noqa: C901  # facet collector: one branch per event family
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
    # S1 (polyadic): events with a participants payload get their roles
    # from the event_participants store rows themselves (payload roles:
    # acquirer/target/seller/partner/lead_partner — beyond the pair
    # vocabulary), so N-party incidences tag roles like pairs do.
    has_store = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='event_participants' AND type='table'"
        ).fetchone()
        is not None
    )
    if has_store and "properties" in cols:
        import json  # noqa: PLC0415

        for eid, etype, date, period, prec, mag, props_raw in conn.execute(
            "SELECT id, event_type, event_date, period, date_precision, magnitude, "
            "properties FROM events WHERE properties LIKE '%\"participants\"%'"
        ):
            try:
                payload = (json.loads(props_raw) if props_raw else {}).get("participants") or []
            except json.JSONDecodeError:  # pragma: no cover — CHECK-enforced
                continue
            if not any(p.get("entity") for p in payload):
                continue
            label = f"{etype}:{eid}"
            for participant, role in conn.execute(
                "SELECT participant, role FROM event_participants WHERE event_id = ?",
                (eid,),
            ):
                if role:
                    facets["roles"][(label, participant)] = role
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
                facets["props"].setdefault(label, props)
            if date:
                facets["valid_from"].setdefault(label, date)
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
        "Recreational Vehicles": "Automobiles",
        "Telecom Tower Infrastructure": "Telecom_Services",
        "Oil & Gas Midstream": "Infrastructure_Operations",
        "Industrial Distribution": "Specialized_Logistics",
        "Waste Management": "Waste_Management",
    }
)

# subsector_authoring_pass S1 (2026-09-19): 8 labels onto EXISTING nodes
# — the operator-approved resolution of the 16 parked worklist buckets
# (decision matrix in doc/improvements/archive/graph/subsector_authoring_pass.md).
# The split buckets (Banks - Regional, Capital Markets, Utilities -
# Renewable, Packaging & Containers, Metal Fabrication) and the
# Semiconductors bucket (per-company onto the existing Design/Foundry/
# Memory leaves — "Semiconductors" itself is the SUB_CATEGORIES PARENT,
# not a leaf) ride the D7 authored lane instead (per-note `subsector:`
# via author_subsector.py) — see S2.
SUB_SECTOR_ALIASES.update(
    {
        "Electronic Gaming & Multimedia": "Gaming",  # NEW leaf under Media_Entertainment (Nazara)
        "Oil & Gas Integrated": "E&P",
        "Department Stores": "Hypermarkets",
        "Publishing": "Publishing",
        "Paper & Paper Products": "Paper_Products",
        "Tools & Accessories": "Capital_Goods",
        "Banks - Diversified": "Foreign_Banks",
        "Electronics & Computer Distribution": "Computer_Hardware",
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
    unmapped.sort(key=lambda x: (-x[1], x[0]))

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
        unmapped_authored.sort(key=lambda x: (-x[1], x[0]))
    return groups, unmapped, unmapped_authored


def _write_subsector_worklist(
    unmapped: list[tuple[str, int, str]],
    unmapped_authored: list[tuple[str, int]] | None = None,
) -> Path:
    """Mirror counterparty_worklist: JSON under findata/Misc/, count-sorted.

    D7: ``unmapped_authored`` lists hand-authored ``subsector:`` note values
    that resolve to no existing sub_sector entity (visible, not fatal).
    """
    from helpers.core.review_kit import latest_action_by

    parked = latest_action_by(
        _REPO_ROOT / "outputs" / "worklist_parking" / "subsector" / "journal.jsonl",
        key_field="label",
    )
    unmapped = [(lbl, n, s) for lbl, n, s in unmapped if lbl not in parked]
    wl = _REPO_ROOT / "findata" / "Misc" / "subsector_worklist.json"
    wl.parent.mkdir(parents=True, exist_ok=True)
    wl.write_text(
        json.dumps(
            {
                "unmapped": [
                    {"label": lbl, "members": n, "suggestion": s} for lbl, n, s in unmapped
                ],
                "count": len(unmapped),
                "parked": len(parked),
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


# --------------------------------------------------------------------------- #
# hgx_over_dyads S3: RPT disclosure sets widen the `group` family              #
# --------------------------------------------------------------------------- #
_RPT_SOURCE_REF_LIKE = "vigil:rpt%"

_RELATIONSHIP_NON_PLURAL_S = frozenset({"news", "this", "status"})
"""Plain-'s' word endings that are not plurals — never stripped."""


def normalise_relationship(value: str | None) -> str | None:
    """Case-fold an RPT relationship label into its class (S3, the minimum
    normalisation the proposal demands): Fellow Subsidiary / Fellow
    subsidiary / Fellow Subsidiaries / Fellow Subsidiary Company are four
    spellings of one class. lowercase → strip a trailing
    'company'/'companies' → per-word plural fold ('ies'→'y', final 's'
    dropped unless 'ss'/short/stoplisted) → collapse whitespace. Free-text
    one-offs ("Subsidiary of Tech Mahindra Limited") pass through folded
    but NOT canonicalised — deeper vocabulary work is a separate arc.
    """
    if value is None:
        return None
    text = " ".join(value.lower().split()).rstrip("#").strip()
    for suffix in (" companies", " company"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    words = []
    for word in text.split():
        if word.endswith("ies") and len(word) > 4:
            word = word[:-3] + "y"
        elif (
            word.endswith("s")
            and not word.endswith("ss")
            and len(word) > 3
            and word not in _RELATIONSHIP_NON_PLURAL_S
        ):
            word = word[:-1]
        words.append(word)
    return " ".join(words) or None


def collect_rpt_disclosure_sets(
    conn,
) -> tuple[dict[str, set[str]], dict[tuple[str, str], str], dict[str, int]]:
    """Widen ``group`` with RPT disclosure sets (hgx_over_dyads S3, operator
    direction 2026-10-30: widen ``group`` rather than mint a 13th family).

    One set per RPT FILER — "everything declared in one company's
    related-party filing" — keyed on ``source_ref`` (``vigil:rpt:<SYMBOL>``);
    the normalised relationship class rides ``hyper_incidences.role``. The
    ``$.group``-keyed curated corporate-group edges are collected as before
    and coexist (labels cannot collide: ``Tata`` vs ``vigil:rpt:TCS``).

    Role rule: a (filer, member) pair seen under several classes (~3% of
    pairs, measured) takes the DOMINANT folded class — most dyads, tie
    broken by the lexicographically smallest class — so the role is
    deterministic across runs. Class-split sets (one label per
    filer:class) would give exact roles and were the proposal's original
    shape; the operator direction chose filer-level keying.

    Returns ``(groups, roles, fold_counts)``:

    - ``groups``: ``{label: members}`` to merge into ``hyper["group"]``
    - ``roles``: ``{(label, member): folded_class}`` for the ``--roles``
      facets pass
    - ``fold_counts``: ``{"pre_fold_sets": (filer, raw class) pairs,
      "post_fold_sets": (filer, folded class) pairs}`` — the
      normalisation's provenance (acceptance: both recorded)
    """
    groups: dict[str, set[str]] = {}
    class_votes: dict[tuple[str, str], Counter] = {}
    raw_pairs: set[tuple[str, str]] = set()
    folded_pairs: set[tuple[str, str]] = set()
    for source, target, source_ref, rel in conn.execute(
        "SELECT source, target, source_ref, json_extract(properties, '$.relationship') "
        "FROM graph_edges WHERE edge_type = 'same_group' AND source_ref LIKE ?",  # noqa: S608
        (_RPT_SOURCE_REF_LIKE,),
    ):
        if not source_ref:
            continue
        members = {m for m in (source, target) if m}
        groups.setdefault(source_ref, set()).update(members)
        folded = normalise_relationship(rel)
        if rel is not None:
            raw_pairs.add((source_ref, rel))
        if folded is not None:
            folded_pairs.add((source_ref, folded))
            for member in members:
                class_votes.setdefault((source_ref, member), Counter())[folded] += 1
    roles: dict[tuple[str, str], str] = {}
    for key, votes in class_votes.items():
        # dominant class: most dyads, tie -> lexicographically smallest
        roles[key] = min(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    fold_counts = {
        "pre_fold_sets": len(raw_pairs),
        "post_fold_sets": len(folded_pairs),
    }
    return groups, roles, fold_counts


# --------------------------------------------------------------------------- #
# S6 curated family lane (rpt_attribution_filer_resolution_and_group_family
# B4): git-tracked seeds widened into ``group`` under ``family:<name>``
# labels. Curation is an operator edit of helpers/misc/family_seeds.json;
# the disclosure-scope ``vigil:rpt:<SYMBOL>`` sets are NOT families.
# --------------------------------------------------------------------------- #

FAMILY_SEEDS_PATH = Path(__file__).resolve().parents[1] / "misc" / "family_seeds.json"
COUNTERPARTY_ALIASES_PATH = (
    Path(__file__).resolve().parents[1] / "misc" / "counterparty_aliases.json"
)
FAMILY_LABEL_PREFIX = "family:"


def collect_family_seeds(seed_path: Path | None = None) -> dict[str, set[str]]:
    """Load the curated family lane into ``{label: members}``.

    Seed file (helpers/misc/family_seeds.json) maps ``family`` ->
    ``members[]``; labels are namespaced ``family:<name>`` so they cannot
    collide with the disclosure lane (``vigil:rpt:<SYMBOL>``) or the
    curated ``$.group`` dyad sets (bare names). Same return shape as
    :func:`collect_rpt_disclosure_sets` groups for the ``hyper["group"]``
    merge. A missing file is an empty lane (fresh/test trees); a malformed
    file is a hard error (curation is a tracked edit — fail loudly).
    """
    path = seed_path or FAMILY_SEEDS_PATH
    try:
        doc = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    families = doc.get("families")
    if not isinstance(families, list):
        raise ValueError(f"family seed file {path}: 'families' must be a list")
    out: dict[str, set[str]] = {}
    for entry in families:
        name = entry.get("family")
        members = entry.get("members")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(members, list)
            or not members
            or not all(isinstance(m, str) and m.strip() for m in members)
        ):
            raise ValueError(
                f"family seed file {path}: bad entry {entry!r} "
                "(need family + non-empty members[] of strings)"
            )
        label = FAMILY_LABEL_PREFIX + name
        if label in out:
            raise ValueError(f"family seed file {path}: duplicate family {name!r}")
        out[label] = {m.strip() for m in members}
    return out


def validate_family_seeds(
    groups: dict[str, set[str]], valid: set[str]
) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    """Drop seed members that do not resolve to a stored entity.

    Curation is a git-tracked edit, so a typo must be VISIBLE, not a mint:
    unknown names are removed per family and reported (the S6 print line
    carries them). Returns ``(filtered_groups, dropped)``.
    """
    filtered: dict[str, set[str]] = {}
    dropped: dict[str, list[str]] = {}
    for label, members in groups.items():
        unknown = sorted(m for m in members if m not in valid)
        kept = members.difference(unknown)
        if kept:
            filtered[label] = kept
        if unknown:
            dropped[label] = unknown
    return filtered, dropped


def collect_hyperedges(  # noqa: C901
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

    for hyper_type, dyad_type, min_k in _STAR_SOURCES:
        groups: dict[str, set[str]] = {}
        for member, label in conn.execute(
            "SELECT source, target FROM graph_edges WHERE edge_type = ?",  # noqa: S608
            (dyad_type,),
        ):
            groups.setdefault(label, set()).add(member)
        if min_k is not None:
            # S0 threshold (star_hyperedge_consumer_wiring): drop
            # sub-threshold portfolios — they duplicate the dyadic facts
            # consumers already render and would re-import hub noise into
            # the centrality lanes.
            groups = {label: m for label, m in groups.items() if len(m) >= min_k}
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

    # S5 (polyadic_capture_edge_fidelity): the newsletter co-mention lane
    # stages per-edition participant sets into ``co_mention_editions`` (the
    # dyad capture path is retired) — unioned here exactly like the quotes
    # path above: one edition = one participant set, however captured.
    # Weight stays None (the apply path leaves existing quote-count weights
    # untouched; staging carries no intensity of its own).
    try:
        has_staging = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='co_mention_editions' AND type='table'"
            ).fetchone()
            is not None
        )
        if has_staging:
            for label, member in conn.execute(
                "SELECT edition_title, entity_name FROM co_mention_editions"
            ):
                out.setdefault("edition", {}).setdefault(label, set()).add(member)
    except sqlite3.OperationalError:
        pass  # fresh DB without entities — nothing staged to union

    # S9: event hyperedges — one PER EVENT ROW (participants from the
    # event_participants store, person_ontology_spine S4). NOT grouped by
    # (event_type, date, source_ref): measured 2026-09-13, 'derive:events:
    # edge-promotion' + NULL date lumps 60 unrelated JVs together —
    # provenance is not a happening key. Label carries the event id for
    # traceability; properties carry the readable pair. Reads the
    # participant store when present (so any writer that adds a
    # participant row grows the hyperedge — k≥3 arrives with data, not
    # with this query), and falls back to the events-table pair when the
    # store is absent (pre-v16 restores / fresh test DBs). Requires
    # counterparty_entity (migrate 2e) — fresh DBs skip.
    try:
        has_store = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='event_participants' AND type='table'"
            ).fetchone()
            is not None
        )
        ph = ", ".join("?" for _ in _EVENT_ROLES)
        if has_store:
            for eid, label in conn.execute(
                "SELECT id, event_type || ':' || id FROM events ORDER BY id"
            ):
                members = {
                    r[0]
                    for r in conn.execute(
                        "SELECT DISTINCT participant FROM event_participants WHERE event_id = ?",
                        (eid,),
                    )
                }
                if len(members) >= 2:
                    out.setdefault("event", {})[label] = members
        else:
            _mapped = tuple(_EVENT_ROLES)
            for eid, etype, ent, cp_ent in conn.execute(
                "SELECT id, event_type, entity, counterparty_entity FROM events "  # noqa: S608  # ph is ? placeholders for a typed tuple
                f"WHERE counterparty_entity IS NOT NULL AND event_type IN ({ph})",
                _mapped,
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


def prune_stale_event_edges(
    conn, wanted_labels: set[str], *, dry_run: bool = True
) -> dict[str, int]:
    """S1 density verdict (hyperedge_density_dq): the ``event`` family is
    OWNED — it converges to the collect output.

    Event labels embed their event id, so a label the collector no
    longer produces is provably noise: either its event row was deleted
    (measured live 2026-10-19: 115 such hyperedges — 102 k=1, 13 k=2 —
    left behind by the additive apply) or the event now carries <2
    participants (4 leftover singletons; the store path emits k≥2
    only). Both classes are stale signal, not membership facts: unlike
    industry/country singletons (a genuine one-member category), a
    one-participant event carries NO co-membership. Delete by label;
    the incidence FK cascade takes the memberships. Every other family
    keeps the additive posture (their singletons are valid membership).
    """
    stored = {
        label
        for (label,) in conn.execute("SELECT label FROM hyper_edges WHERE edge_type = 'event'")
    }
    stale = sorted(stored - wanted_labels)
    stats = {
        "stored": len(stored),
        "kept": len(stored & wanted_labels),
        "dropped": len(stale),
    }
    if dry_run or not stale:
        return stats
    qmarks = ",".join("?" * len(stale))
    with conn:
        conn.execute(
            f"DELETE FROM hyper_edges WHERE edge_type = 'event' AND label IN ({qmarks})",  # noqa: S608  # ?-placeholders only, values parameterized
            stale,
        )
    return stats


def prune_stale_group_edges(conn, wanted_labels: set[str], *, dry_run: bool) -> dict[str, int]:
    """Group-family convergence (graph_lane_unlocks S2): ``group`` converges
    to the collect output.

    #457 widened ``group`` to 498 filer-keyed RPT disclosure sets
    (``vigil:rpt:<SYMBOL>``) fed by an upstream that REWRITES — S9
    counterparty resolution mutates source_refs, filings reclassify —
    and the additive apply cannot remove a label the collector no
    longer produces. Same verdict rule as the event family: a stored
    ``group`` label absent from the collect output is provably stale
    (a filer that stopped reporting, or a curated ``$.group`` dyad
    since edited), not a membership fact. Delete by label; the
    incidence FK cascade takes the memberships. The curated corporate
    groups participate on the same terms — they are collected every
    run, so a surviving dyad keeps its label.
    """
    stored = {
        label
        for (label,) in conn.execute("SELECT label FROM hyper_edges WHERE edge_type = 'group'")
    }
    stale = sorted(stored - wanted_labels)
    stats = {
        "stored": len(stored),
        "kept": len(stored & wanted_labels),
        "dropped": len(stale),
    }
    if dry_run or not stale:
        return stats
    qmarks = ",".join("?" * len(stale))
    with conn:
        conn.execute(
            f"DELETE FROM hyper_edges WHERE edge_type = 'group' AND label IN ({qmarks})",  # noqa: S608  # ?-placeholders only, values parameterized
            stale,
        )
    return stats


def compact_group_membership(
    conn,
    groups: dict[str, set[str]],
    *,
    dry_run: bool = True,
) -> int:
    """S1 compaction: converge group-HYPEREDGE member incidences to the
    current dyad-derived membership.

    Only dyad-derived labels are compacted — ``vigil:rpt:*`` (filer
    disclosure sets, keyed by ``source_ref``) and bare ``$.group`` labels
    (curated corporate groups) both rebuild their membership from current
    ``same_group`` dyads. ``family:*`` seed labels are NOT dyad-derived
    (curated lists) and are untouched. Incidences whose member has no
    current dyad under the label are deleted — the additive ``INSERT OR
    IGNORE`` apply path never shrinks, so without this pass stale members
    (companies that left a filer's disclosure set / a curated group) linger
    forever. Returns the number of incidences deleted (or would-delete in
    dry-run).
    """
    pruned = 0
    for label, members in groups.items():
        if label.startswith(FAMILY_LABEL_PREFIX) or not members:
            continue
        edge = conn.execute(
            "SELECT id FROM hyper_edges WHERE edge_type = 'group' AND label = ?",
            (label,),
        ).fetchone()
        if edge is None:
            continue
        qmarks = ",".join("?" * len(members))
        if dry_run:
            cur = conn.execute(
                f"SELECT COUNT(*) FROM hyper_incidences WHERE edge_id = ? "  # noqa: S608  # ?-placeholders only, values parameterized
                f"AND entity_name NOT IN ({qmarks})",
                (edge[0], *members),
            )
        else:
            cur = conn.execute(
                f"DELETE FROM hyper_incidences WHERE edge_id = ? "  # noqa: S608  # ?-placeholders only, values parameterized
                f"AND entity_name NOT IN ({qmarks})",
                (edge[0], *members),
            )
        pruned += cur.fetchone()[0] if dry_run else cur.rowcount
    return pruned


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
    shrinkage (a company leaving a category) is compacted by
    :func:`compact_group_membership` for dyad-derived ``group`` labels
    (``vigil:rpt:*`` + bare ``$.group``; ``family:*`` seeds untouched) —
    run it from the caller after apply with ``dry_run`` mirroring this
    call (proposal §3 S1/S2, honest-scope note).

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
        # S5: the dyad capture path is retired — the newsletter lane stages
        # per-edition sets into co_mention_editions, collected below.
        return ["newsletter_co_mention", "quotes"]  # union of both capture paths
    for ht, t, _k in _STAR_SOURCES + [(ht, t, None) for ht, t, _ in _SYMMETRIC_SOURCES]:
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
    p.add_argument(
        "--families",
        default=None,
        help="Comma-separated family subset to (re)derive, e.g. "
        "'index,subsidiary,supply' (star_hyperedge_consumer_wiring S2). "
        "Default: all families. Threshold families in the selection are "
        "delete-then-insert on --apply (fully derived, no authored rows).",
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
        # hgx_over_dyads S3: widen `group` with the RPT disclosure sets
        # (filer-keyed; curated $.group sets coexist). Roles ride the
        # --roles facets pass — without --roles the sets land untagged,
        # same as every other non-event family.
        rpt_groups, rpt_roles, rpt_counts = collect_rpt_disclosure_sets(conn)
        if rpt_groups:
            merged_group = dict(hyper.get("group", {}))
            for label, members in rpt_groups.items():
                merged_group.setdefault(label, set()).update(members)
            hyper["group"] = merged_group
            if facets is not None:
                facets["roles"].update(rpt_roles)
            print(
                f"S3 RPT disclosure sets: {len(rpt_groups)} filer set(s) "
                f"widening `group`; relationship fold "
                f"{rpt_counts['pre_fold_sets']} -> {rpt_counts['post_fold_sets']} "
                f"(filer, class) pair(s); {len(rpt_roles)} member role(s) "
                "(dominant folded class)"
            )
        valid = {r[0] for r in conn.execute("SELECT name FROM entities")}
        # S6 (rpt_attribution_filer_resolution_and_group_family B4): widen
        # ``group`` with the curated family lane — git-tracked seeds under
        # ``family:<name>`` labels, members validated against entities (a
        # seed typo is dropped and printed, never minted — hyper_incidences
        # is FK-clean by construction). Labels cannot collide with the
        # disclosure lane or the curated ``$.group`` sets. Landing inside
        # ``hyper["group"]`` also puts the labels under the S2 prune
        # convergence: a seed removed from the file is pruned on the next run.
        family_groups = collect_family_seeds()
        if family_groups:
            family_groups, family_dropped = validate_family_seeds(family_groups, valid)
            if family_groups:
                merged_family = dict(hyper.get("group", {}))
                for label, members in family_groups.items():
                    merged_family.setdefault(label, set()).update(members)
                hyper["group"] = merged_family
            n_family_members = sum(len(m) for m in family_groups.values())
            n_family_dropped = sum(len(v) for v in family_dropped.values())
            print(
                f"S6 family seeds: {len(family_groups)} curated family set(s) "
                f"widening `group` ({n_family_members} member(s))"
                + (f"; unresolved dropped: {family_dropped}" if n_family_dropped else "")
            )

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
        # S2 family selector (star_hyperedge_consumer_wiring): narrow the
        # write set AFTER all cross-family derivations (sub_sector reads
        # industry/group inputs) so a filtered run never starves them.
        if args.families:
            selected = {f.strip() for f in args.families.split(",") if f.strip()}
            unknown = selected - set(hyper)
            if unknown:
                p.error(
                    f"--families: unknown family name(s) {sorted(unknown)}; known: {sorted(hyper)}"
                )
            hyper = {k: v for k, v in hyper.items() if k in selected}
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

        # S0 threshold convergence (star_hyperedge_consumer_wiring): the
        # threshold families are fully derived (no authored content), but
        # apply_hyperedges is INSERT OR IGNORE additive-only — without a
        # delete-then-insert pass, sub-threshold rows from earlier runs
        # linger forever. Delete the selected threshold families' derived
        # rows before the apply re-inserts only the k≥3 portfolios.
        if args.apply:
            selected = (
                {f.strip() for f in args.families.split(",") if f.strip()}
                if args.families
                else None  # None = all families (default full run)
            )
            for ht, _t, k in _STAR_SOURCES:
                if k is None or (selected is not None and ht not in selected):
                    continue
                edges = conn.execute(
                    "SELECT id FROM hyper_edges WHERE edge_type = ? "
                    "AND source_ref LIKE 'derive:hyperedges:%'",
                    (ht,),
                ).fetchall()
                with conn:
                    for (eid,) in edges:
                        conn.execute("DELETE FROM hyper_incidences WHERE edge_id = ?", (eid,))
                        conn.execute("DELETE FROM hyper_edges WHERE id = ?", (eid,))
                print(f"S0 prune: dropped {len(edges)} derived '{ht}' hyperedges (pre-threshold)")

        stats = apply_hyperedges(hyper, weights, conn=conn, dry_run=not args.apply, facets=facets)
        # S1 density verdict: the event family converges to the collect
        # output (stale labels + sub-threshold singletons drop).
        # --families guard (star_hyperedge_consumer_wiring S2): a filtered
        # run's hyper dict lacks the unselected families — pruning against
        # an empty wanted-set would nuke every stored row of a family the
        # run never re-derived. Skip convergence for unselected families.
        selected_families = (
            {f.strip() for f in args.families.split(",") if f.strip()} if args.families else None
        )
        family_selected = lambda fam: (  # noqa: E731  # trivial guard closure
            selected_families is None or fam in selected_families
        )
        wanted_events = set(hyper.get("event", {}))
        if family_selected("event") and (
            wanted_events
            or conn.execute(
                "SELECT 1 FROM hyper_edges WHERE edge_type = 'event' LIMIT 1"
            ).fetchone()
        ):
            prune = prune_stale_event_edges(conn, wanted_events, dry_run=not args.apply)
            if prune["dropped"]:
                mode_p = "would drop" if not args.apply else "dropped"
                print(
                    f"S1 event-family convergence: {mode_p} {prune['dropped']} stale "
                    f"hyperedge(s) of {prune['stored']} stored ({prune['kept']} kept)"
                )
        # graph_lane_unlocks S2: group converges to the collect output —
        # filer-keyed RPT sets ride a rewriting upstream, so a label the
        # collector no longer produces must drop (apply is otherwise
        # ADD/UPDATE-only for this family).
        wanted_groups = set(hyper.get("group", {}))
        if family_selected("group") and (
            wanted_groups
            or conn.execute(
                "SELECT 1 FROM hyper_edges WHERE edge_type = 'group' LIMIT 1"
            ).fetchone()
        ):
            gprune = prune_stale_group_edges(conn, wanted_groups, dry_run=not args.apply)
            if gprune["dropped"]:
                mode_g = "would drop" if not args.apply else "dropped"
                print(
                    f"S2 group-family convergence: {mode_g} {gprune['dropped']} stale "
                    f"hyperedge(s) of {gprune['stored']} stored ({gprune['kept']} kept)"
                )
            # S1 member-level compaction: converge incidence membership to the
            # current dyad-derived set for vigil/$.group labels (family:*
            # seeds untouched). Prune first (labels absent from collect are
            # dropped); then delete incidences whose member has no current
            # dyad under a surviving label.
            if args.apply:
                compacted = compact_group_membership(conn, hyper.get("group", {}), dry_run=False)
                if compacted:
                    print(f"S1 group-membership compaction: {compacted} stale incidence(s) dropped")
            elif hyper.get("group"):
                would_compact = compact_group_membership(
                    conn, hyper.get("group", {}), dry_run=True
                )
                if would_compact:
                    print(
                        f"S1 group-membership compaction: {would_compact} stale "
                        f"incidence(s) would drop"
                    )
        print(
            f"{'source':<10} {'new_edges':>10} {'new_incidences':>15}"
            + (f"  ({len(weights)} weighted)" if weights else "")
        )
        for hyper_type, (new_edges, new_inc) in sorted(stats.items()):
            print(f"{hyper_type:<10} {new_edges:>10} {new_inc:>15}")
        # D7 convergence prune: sub_sector groups are FULLY recomputed each
        # run (alias union + COMPANY_SUB_SECTORS + authored precedence), so
        # an apply must also DROP incidences the recomputed groups no longer
        # contain — relabeling/precedence swaps would otherwise linger
        # forever, because the write path above is INSERT OR IGNORE.
        pruned = 0
        if args.apply and "sub_sector" in hyper:
            wanted: dict[str, set[str]] = {}
            for lbl, members in hyper["sub_sector"].items():
                wanted.setdefault(lbl, set()).update(members)
            with conn:
                for eid, lbl in conn.execute(
                    "SELECT id, label FROM hyper_edges WHERE edge_type = 'sub_sector'"
                ).fetchall():
                    keep = wanted.get(lbl, set())
                    if keep:
                        qmarks = ",".join("?" * len(keep))
                        cur = conn.execute(
                            f"DELETE FROM hyper_incidences WHERE edge_id = ? "  # noqa: S608  # ?-placeholders only, values parameterized
                            f"AND entity_name NOT IN ({qmarks})",
                            (eid, *keep),
                        )
                    else:
                        cur = conn.execute("DELETE FROM hyper_incidences WHERE edge_id = ?", (eid,))
                    pruned += cur.rowcount
            if pruned:
                print(f"D7 convergence prune: {pruned} stale sub_sector incidence(s) dropped")

        mode = "APPLY" if args.apply else "DRY-RUN (use --apply to write)"
        total_e = sum(e for e, _ in stats.values())
        total_i = sum(i for _, i in stats.values())
        print(f"{mode}: {total_e} new hyperedges, {total_i} new incidences")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
