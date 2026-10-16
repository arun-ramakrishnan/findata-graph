#!/usr/bin/python3
"""Person-name dedup: census, merge plan, standing gate (person arc).

The person analog of ``name_dedup`` (entity_name_dedup #398): same
slice shape — census → reviewable plan → gated merge → standing gate —
over the person populations: the graph's person-shaped clusters
(excluded from the company merge by the S1 filter), the SHP holder
stubs, and the Quotes speaker corpus.

Load-bearing difference (person_name_dedup §1): **legal lines are part
of identity** — an HUF never merges into the individual. Clusters whose
members differ in legal line land in a NEVER-MERGE section. Clusters
carrying company markers (``A S Packers``, ``M.G.T Cements``,
``MABELLA TRUSTEE SERVICES`` — company-shaped spellings the person
FILTER over-included, in the safe direction) land in a review section
for hand-routing back to the company arc — never folded here.

Apply reuses ``name_dedup.apply_plan`` with ``skip_persons=False``:
same constraint handling (UNIQUE consolidation, self-loop drops,
registry fold), zero-loss accounting, and the same twin artifacts
(``.txt`` for review, ``.jsonl`` verbatim machine form — person names
may contain anything).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.core.entity_identity import (  # noqa: E402
    _COMPANY_MARKER_RE,
    legal_line,
    name_key,
    speaker_names,
)

PLAN_PATH = _REPO_ROOT / "findata" / "Misc" / "_person_merge_plan.txt"
BASELINE_PATH = _REPO_ROOT / "findata" / "Misc" / "_person_dedup_baseline.txt"


def _partition_cluster(members: list[str]) -> str:
    """person | company_shaped — one company marker anywhere in the
    cluster routes the whole cluster to review (a variant list mixing
    'Mr X' with 'X Holdings Ltd' is a company family, not a person)."""
    for m in members:
        if _COMPANY_MARKER_RE.search(m):
            return "company_shaped"
    return "person"


# Legal-line tokens: stripped to form the BASE key — 'Deepak Agarwal
# (HUF)' and 'Deepak Agarwal' share a base but sit on different lines,
# which is precisely the NEVER-MERGE signal (#293's founding hazard).
_LINE_TOKEN_RE = re.compile(r"\b(huf|hindu|undivided|family|trust|small|karta|kartha)\b", re.I)


def _base_key(name: str) -> str:
    return name_key(_LINE_TOKEN_RE.sub(" ", name))


def _merge_line(name: str) -> str:
    """Legal line as the MERGE sees it — narrows :func:`legal_line`.

    Operator ruling 2026-10-16: a HUF is defined by its Karta, so 'X (HUF)'
    and 'X' are the SAME person and must fold rather than sit across an
    identity boundary. Only 'trust' stays a real boundary — the trust owns
    the shares, the trustee merely manages them, which is the same shape as
    the unlisted subsidiary the company arc re-merges.

    Deliberately NOT changed in helpers.core.person_names
    ::classify_person_name: that classifier is shared (resolve_person and
    the speaker corpus), and 'this name denotes a HUF' stays true even when
    the merge now treats it as one person. This is a merge-policy narrowing
    local to the census, not a redefinition of the name.
    """
    line = legal_line(name)
    return "person" if line == "huf" else line


def person_census(con) -> dict:
    """Cluster the graph's person populations by (legal line, base key).

    - ``folds``: same line, same base, >1 spelling — safe within-line
      variants (case / punctuation / spacing / honorific spacing). HUF
      rows fold into their Karta under the 2026-10-16 operator ruling
      (see :func:`_merge_line`).
    - ``never_merge``: same base, DIFFERENT legal lines — the identity
      boundary (a trust owns; its trustee manages). Listed as
      never-merge even though the names beg for it. REPORTED, NEVER
      GATED: the count tracks how many trust holders appear in filings,
      not code quality (see :func:`check`).
    - ``company_shaped``: S1 filter over-inclusion — review, route
      back to the company arc by hand.
    Entities-person rows and graph person-cluster members are scanned
    together (the populations share spellings).
    """
    from helpers.graph.name_dedup import census as company_census

    pcs = company_census(con)["person_clusters"]
    degree: dict[str, int] = {}
    for (n,) in con.execute(
        "SELECT source FROM graph_edges UNION ALL SELECT target FROM graph_edges"
    ).fetchall():
        degree[n] = degree.get(n, 0) + 1

    # universe: person-cluster members (pre-classified by the S1
    # filter — taken as-is, they arrive via ANY-member shape) + registry
    # person rows (gated: the registry also holds company rows typed
    # 'person' from other lanes).
    names: set[str] = set()
    for pc in pcs:
        names.update(pc["variants"])
    for (n,) in con.execute("SELECT name FROM entities WHERE entity_type='person'").fetchall():
        if n:
            names.add(n)  # the registry's own classification is authoritative
    names = {n for n in names if n}

    groups: dict[tuple[str, str], list[str]] = {}
    for n in sorted(names):
        groups.setdefault((_merge_line(n), _base_key(n)), []).append(n)

    folds: list[dict] = []
    never_merge: list[dict] = []
    for (line, base), members in sorted(groups.items()):
        if len(members) > 1:
            # Degree, then — new — prefer the NON-huf member, then name.
            # With degrees tied at 1 the old (degree, name) rule let the
            # longer 'X (HUF)' string win the max() by accident, keeping the
            # HUF suffix on the survivor. The 2026-10-16 ruling is that a
            # HUF IS its Karta, so the plain person is the identity and the
            # HUF row is the duplicate. Safe for every other cluster: trust
            # never shares a fold group (_merge_line keeps it its own line),
            # so the middle term only ever separates huf from person.
            canon = max(
                members,
                key=lambda m: (degree.get(m, 0), legal_line(m) == "person", m),
            )
            folds.append({"canonical": canon, "variants": sorted(members), "line": line})
    # base collisions ACROSS lines (different (line, base) groups, same
    # base) are the never-merge hazards:
    by_base: dict[str, dict[str, list[str]]] = {}
    for (line, base), members in groups.items():
        by_base.setdefault(base, {}).setdefault(line, []).extend(members)
    for base, lines in sorted(by_base.items()):
        if len(lines) > 1:
            never_merge.append(
                {
                    "variants": sorted(n for ms in lines.values() for n in ms),
                    "reason": "legal lines differ ("
                    + ", ".join(sorted(lines))
                    + ") — same base name",
                }
            )
    company_shaped: list[dict] = []
    company_members: set[str] = set()
    for pc in pcs:
        if any(_COMPANY_MARKER_RE.search(m) for m in pc["variants"]):
            company_shaped.append(
                {"variants": pc["variants"], "reason": "company marker in spelling"}
            )
            company_members.update(pc["variants"])
    # company-shaped members are review-only: never fold stock
    folds = [
        f
        for f in folds
        if f["canonical"] not in company_members and not (set(f["variants"]) & company_members)
    ]
    speakers = speaker_names()
    return {
        "folds": folds,
        "never_merge": never_merge,
        "company_shaped": company_shaped,
        "speakers": len(speakers),
        "n_clusters": len(folds),
    }


def write_plan(cen: dict, path: Path = PLAN_PATH) -> Path:
    jsonl_path = path.with_suffix(".jsonl")
    lines = [
        "# Person-name merge plan (person_name_dedup S1 — DRY-RUN artifact)",
        f"# fold clusters: {cen['n_clusters']} | never-merge: {len(cen['never_merge'])} | "
        f"company-shaped review: {len(cen['company_shaped'])} | speakers: {cen['speakers']}",
        "# rule: same legal line + same normalised key folds; cross-line NEVER; "
        "company-shaped clusters route back to the company arc",
        "# S2 applies ONLY on operator sign-off (person_dedup.py --apply --yes)",
        "",
    ]
    jsonl_rows = []
    for c in cen["folds"]:
        lines.append(f"== {c['canonical']}  [{c['line']}]")
        for v in c["variants"]:
            mark = "" if v == c["canonical"] else "  -> fold"
            lines.append(f"   {v.replace(chr(10), ' ')}{mark}")
        lines.append("")
        jsonl_rows.append(
            {
                "canonical": c["canonical"],
                "variants": [v for v in c["variants"] if v != c["canonical"]],
                "line": c["line"],
            }
        )
    if cen["never_merge"]:
        lines.append("# -- NEVER-MERGE (legal lines differ — identity boundary) --")
        for nm in cen["never_merge"]:
            lines.append(f"   {' | '.join(nm['variants'])}  ({nm['reason']})")
        lines.append("")
    if cen["company_shaped"]:
        lines.append(
            "# -- company-shaped (S1 over-inclusion): route to the company arc, not folded here --"
        )
        for cs in cen["company_shaped"]:
            lines.append(f"   {' | '.join(cs['variants'])}")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    jsonl_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in jsonl_rows),
        encoding="utf-8",
    )
    return path


def check(con, baseline_path: Path = BASELINE_PATH) -> int:
    """S4 gate: mergeable person-variant count vs baseline (growth = regression).

    never_merge is REPORTED but NOT GATED (operator, 2026-10-16). It counts
    base-key collisions across legal lines — a CORRECT refusal to merge a
    trust with its trustee, not a defect to fix. It also cannot converge:
    every refresh mints more holder names from the filings (person rows went
    30 -> 280 in a single refresh on 2026-10-16, and all five then-current
    never_merge pairs were minted that same day), so gating on it meant the
    check could only ever ratchet upward and fail for behaving correctly.
    folds + company_shaped remain the gated number — those ARE regressions:
    misspelled variants of one person, and S1 filter over-inclusion.
    """
    cen = person_census(con)
    n = len(cen["folds"]) + len(cen["company_shaped"])
    n_nm = len(cen["never_merge"])
    note = f" (never_merge {n_nm} reported, not gated)" if n_nm else ""
    if not baseline_path.exists():
        print(f"person-census: {n} clusters{note} (no baseline recorded — informational)")
        return 0
    baseline = int(baseline_path.read_text(encoding="utf-8").strip())
    if n > baseline:
        print(
            f"FAIL: person-census {n} > baseline {baseline}{note} — a writer is creating person variants"
        )
        return 1
    print(f"ok: person-census {n} <= baseline {baseline}{note}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Person-name dedup: census / plan / check / apply")
    p.add_argument("--census", action="store_true", help="print the person census")
    p.add_argument("--plan", action="store_true", help="write the merge-plan artifacts (dry-run)")
    p.add_argument("--check", action="store_true", help="S4 gate: census vs baseline")
    p.add_argument("--baseline", action="store_true", help="record the CURRENT count as baseline")
    p.add_argument("--apply", action="store_true", help="S2: apply the plan (requires --yes)")
    p.add_argument("--yes", action="store_true", help="confirm the S2 write")
    args = p.parse_args(argv)

    con = connect()
    try:
        from helpers.graph.name_dedup import apply_plan

        if args.apply:
            res = apply_plan(con, PLAN_PATH, yes=args.yes, skip_persons=False)
            print(json.dumps(res, indent=2))
            print("next: refresh the duckdb graph caches (helpers/graph/query.py rebuild)")
            return 0
        cen = person_census(con)
        if args.census:
            print(
                f"folds={cen['n_clusters']} never_merge={len(cen['never_merge'])} "
                f"company_shaped={len(cen['company_shaped'])} speakers={cen['speakers']}"
            )
            for c in cen["folds"]:
                print(f"  {c['canonical']}  <- {len(c['variants'])} variants [{c['line']}]")
            return 0
        if args.plan:
            path = write_plan(cen)
            print(f"plan -> {path} (+.jsonl)")
            print(f"folds={cen['n_clusters']} (operator review gates S2: --apply --yes)")
            return 0
        if args.baseline:
            n = len(cen["folds"]) + len(cen["never_merge"]) + len(cen["company_shaped"])
            BASELINE_PATH.write_text(str(n) + "\n", encoding="utf-8")
            print(f"baseline <- {n} ({BASELINE_PATH})")
            return 0
        if args.check:
            return check(con)
        p.print_help()
        return 2
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
