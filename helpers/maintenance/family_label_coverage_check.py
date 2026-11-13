#!/usr/bin/env python3
"""Family-seed coverage census — core-rule maximality guard (read-only).

The curated family lane (family_seeds.json) is built by an evidence rule:
a *company* disclosed as a related party in >=2 filer labels (`vigil:rpt:*`)
of a group belongs in that group's seed (v7 core extraction, #514). This
tool enforces that rule as an invariant:

- for each **complete** family (COMPLETE_FAMILIES, overridable via
  ``--families``), every company member of a filer label that appears in
  >=2 filer labels overall must be contained in the family seed;
- evaluation is scoped to company-type entities: non-company members
  (persons, foreign/offshore structs, JV counters) are excluded from the
  invariant — they are disclosed related parties, not family members
  (measurement lesson 2026-11-13: a literal `label ⊆ family` invariant
  fails by design on these rows);
- independent-label company members outside the seed are reported
  (informational), never failed;
- non-filer labels (`family:*`, `$.group`, …) are never evaluated.

``--check`` exits 1 on any violation (gate mode); default invocation prints
the coverage report and exits 0. Read-only: no store writes.

Usage::

    .venv/bin/python3 helpers/maintenance/family_label_coverage_check.py
    .venv/bin/python3 helpers/maintenance/family_label_coverage_check.py --check
    .venv/bin/python3 helpers/maintenance/family_label_coverage_check.py --families all
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import TypedDict

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402
from helpers.maintenance.group_discovery_inventory import load_family_seeds  # noqa: E402

FAMILY_SEEDS_PATH = REPO_ROOT / "helpers" / "misc" / "family_seeds.json"
DEFAULT_DB = REPO_ROOT / "memory" / "research.db"

# Families whose seed must satisfy the core-rule maximality invariant. A
# family becomes complete only when curation reached maximality (Tata:
# live census 2026-11-13 — 0 company members with >=2 filer-label
# appearances outside the seed).
COMPLETE_FAMILIES = {"Tata"}

FILER_PREFIX = "vigil:rpt:"

# Family root tokens: a company whose name carries a complete family's root
# token AND appears in >=2 of that family's context filer labels must be
# seeded (tier A, blocking). Non-token context-multi members are tier B
# (informational) — they are typically suppliers/JV counters, not family
# members (measured 2026-11-13: 24 context-multi candidates, ~12 token
# bearers, ~12 noise).
FAMILY_ROOT_TOKENS = {
    "Tata": ("tata", "taj", "trent", "titan", "voltas"),
}


class CoverageRow(TypedDict):
    """One filer-label coverage row (findings are derived from rows later)."""

    label: str
    size: int
    companies: int
    intersects: str | None
    coverage: str
    outside_multi: list[str]
    outside_tokens: list[str]
    outside_single: list[str]


def _load_group_labels(conn) -> dict[str, dict]:
    """Return {label: {"members": set, "companies": set}} for group edges."""
    rows = conn.execute(
        """
        SELECT he.label, hi.entity_name, e.entity_type
        FROM hyper_edges he
        JOIN hyper_incidences hi ON hi.edge_id = he.id
        LEFT JOIN entities e ON e.name = hi.entity_name
        WHERE he.edge_type = 'group'
        ORDER BY he.label, hi.entity_name
        """
    ).fetchall()
    out: dict[str, dict] = {}
    for label, member, etype in rows:
        d = out.setdefault(label, {"members": set(), "companies": set()})
        d["members"].add(member)
        if etype == "company":
            d["companies"].add(member)
    return out


def _assess_label(
    label: str,
    d: dict,
    fam: str,
    fam_members: set[str],
    appears: dict[str, int],
    tokens: tuple[str, ...],
) -> CoverageRow:
    """One label's coverage row (findings are derived from rows later)."""
    inter = d["companies"] & fam_members
    full = inter == d["companies"]
    outside_multi = sorted(c for c in d["companies"] - fam_members if appears[c] >= 2)
    outside_tokens = sorted(c for c in outside_multi if any(tok in c.lower() for tok in tokens))
    outside_single = sorted(c for c in d["companies"] - fam_members if appears[c] == 1)
    return {
        "label": label,
        "size": len(d["members"]),
        "companies": len(d["companies"]),
        "intersects": fam,
        "coverage": "FULL" if full else "PARTIAL",
        "outside_multi": outside_multi,
        "outside_tokens": outside_tokens,
        "outside_single": outside_single[:8],
    }


def _family_contexts(
    labels: dict[str, dict], families: dict[str, set[str]]
) -> dict[str, list[str]]:
    """Filer labels intersecting each family (the family's context)."""
    return {
        fam: [
            lab
            for lab, d in labels.items()
            if lab.startswith(FILER_PREFIX) and (d["companies"] & members)
        ]
        for fam, members in families.items()
    }


def _context_counts(
    fam_ctx: dict[str, list[str]], labels: dict[str, dict]
) -> dict[str, dict[str, int]]:
    """Per-family, per-company context appearance counts."""
    fam_appears: dict[str, dict[str, int]] = {}
    for fam, ctx in fam_ctx.items():
        cnt: dict[str, int] = defaultdict(int)
        for lab in ctx:
            for c in labels[lab]["companies"]:
                cnt[c] += 1
        fam_appears[fam] = cnt
    return fam_appears


def coverage_report(
    labels: dict[str, dict], families: dict[str, set[str]], complete: set[str]
) -> dict:
    """Evaluate the core-rule invariant per filer label.

    Returns ``{"labels": [...], "violations": [...], "advisory": [...]}``.
    Each label row: ``{label, size, companies, family, coverage,
    outside_multi, outside_single}``. ``coverage`` is FULL when every
    company member lies in the (first intersecting) family seed; PARTIAL
    otherwise (informational unless the family is complete).

    Violations (tier A, blocking): company members of a complete family's
    context that (a) appear in >=2 of that family's context filer labels
    AND (b) carry the family's root token — while missing from the seed.
    Advisory (tier B): context-multi company members without a root token
    (suppliers/JV counters; never fail the gate).
    """
    # context per family = filer labels intersecting it
    fam_ctx: dict[str, list[str]] = _family_contexts(labels, families)
    # per-family, per-company context appearance counts
    fam_appears: dict[str, dict[str, int]] = _context_counts(fam_ctx, labels)

    rows: list[CoverageRow] = []
    fam_order = sorted(families.items(), key=lambda kv: (kv[0] not in complete, kv[0]))
    for label, d in sorted(labels.items()):
        if not label.startswith(FILER_PREFIX):
            continue
        for fam, fam_members in fam_order:
            if not (d["companies"] & fam_members):
                continue
            row = _assess_label(
                label,
                d,
                fam,
                fam_members,
                fam_appears[fam],
                FAMILY_ROOT_TOKENS.get(fam, ()),
            )
            rows.append(row)
            break

    # Derive tier A/tier B findings from rows (single source of truth).
    violations = [
        {"label": r["label"], "family": r["intersects"], "missing": r["outside_tokens"]}
        for r in rows
        if r["intersects"] in complete and r["outside_tokens"]
    ]
    advisory = [
        {"label": r["label"], "family": r["intersects"], "member": c}
        for r in rows
        if r["intersects"] in complete
        for c in r["outside_multi"]
        if c not in r["outside_tokens"]
    ]
    covered = {r["label"] for r in rows}
    for label, d in sorted(labels.items()):
        if label.startswith(FILER_PREFIX) and label not in covered:
            rows.append(
                {
                    "label": label,
                    "size": len(d["members"]),
                    "companies": len(d["companies"]),
                    "intersects": None,
                    "coverage": "NONE",
                    "outside_multi": [],
                    "outside_tokens": [],
                    "outside_single": [],
                }
            )
    rows.sort(key=lambda r: str(r["label"]))
    # dedupe advisory by member
    seen = set()
    advisory_u = []
    for a in advisory:
        if (a["member"], a["family"]) in seen:
            continue
        seen.add((a["member"], a["family"]))
        advisory_u.append(a)
    return {"labels": rows, "violations": violations, "advisory": advisory_u}


def render(report: dict) -> str:
    lines = ["# Family-label coverage census (core-rule maximality)", ""]
    lines.append(f"filer labels inspected: {len(report['labels'])}")
    if report["labels"]:
        lines.append("")
        lines.append("| label | size | cos | family | coverage | multi-out | single-out |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in report["labels"]:
            lines.append(
                f"| {r['label']} | {r['size']} | {r['companies']} | "
                f"{r['intersects'] or '—'} | {r['coverage']} | "
                f"{len(r['outside_multi'])} | {len(r['outside_single'])} |"
            )
    if report["violations"]:
        lines.append("")
        lines.append("## Violations (complete family, token-tier breach)")
        for v in report["violations"]:
            missing = ", ".join(v["missing"][:10])
            lines.append(
                f"- {v['label']}: {len(v['missing'])} token-bearing company "
                f"member(s) with >=2 context appearances outside "
                f"family:{v['family']} — {missing}"
            )
    if report["advisory"]:
        lines.append("")
        lines.append("## Advisory (context-multi, no root token — never blocks)")
        for a in report["advisory"][:20]:
            lines.append(f"- {a['member']} ({a['label']})")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument(
        "--check", action="store_true", help="exit 1 on any complete-family core-rule violation"
    )
    ap.add_argument(
        "--families",
        default=None,
        help="comma-separated complete families (default: "
        "COMPLETE_FAMILIES; 'all' = every curated family)",
    )
    args = ap.parse_args()

    if not FAMILY_SEEDS_PATH.exists():
        print(f"family_seeds.json not found: {FAMILY_SEEDS_PATH}", file=sys.stderr)
        return 1

    families = load_family_seeds()
    if not families:
        print("no families in family_seeds.json — nothing to check", file=sys.stderr)
        return 1

    # load_family_seeds returns {member: set(family labels)} — the report
    # wants {family: set(members)}. Rebuild from the JSON for the correct
    # shape (live-run bug fixed 2026-11-13: comparing companies against
    # label-name strings produced spurious violations).
    seeds_payload = json.loads(FAMILY_SEEDS_PATH.read_text(encoding="utf-8"))
    families = {f["family"]: set(f["members"]) for f in seeds_payload.get("families", [])}

    if args.families is None:
        complete = set(COMPLETE_FAMILIES) & set(families)
    elif args.families.strip().lower() == "all":
        complete = set(families)
    else:
        complete = {f.strip() for f in args.families.split(",") if f.strip()}
        unknown = complete - set(families)
        if unknown:
            print(f"unknown families: {sorted(unknown)}", file=sys.stderr)
            return 1

    import duckdb

    conn = duckdb.connect(args.db, **memory_cap_config())
    try:
        labels = _load_group_labels(conn)
    finally:
        conn.close()

    if not labels:
        print("no group hyperedges — skipping (rc 0)")
        return 0

    report = coverage_report(labels, families, complete)
    print(render(report))

    if args.check:
        if report["violations"]:
            print("family-label coverage gate: FAIL")
            print(
                "Fix: seed any multi-label company member of a complete "
                "family, or demote the family from COMPLETE_FAMILIES."
            )
            return 1
        print("family-label coverage gate: PASS")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
