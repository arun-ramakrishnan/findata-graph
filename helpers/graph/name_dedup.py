#!/usr/bin/python3
"""Entity-name dedup: census, merge plan, standing gate (entity_name_dedup).

``graph_edges`` carries names inline (no node table), and three writers
(vigil bulk reports, newsletter extraction, derived lanes) have drifted
into spelling variants of the same real-world entity: 1,575 collision
clusters over 22,067 distinct names, 11.2% of edge rows touched
(2026-10-15 census). Variant names defeat every exact-name surface —
suggest_relations dedup, A² pairing, co-mention pairing, fuzzy match.

Slices (proposal ``entity_name_dedup.md``):

- **S1** ``--census`` / ``--plan`` — normalisation-key clustering and a
  human-reviewable merge plan. DRY-RUN ONLY: nothing here writes to the
  graph. The plan artifact is the gate: S2 runs only on operator
  sign-off.
- **S2** ``--apply`` — re-point ``graph_edges`` per the SIGNED plan
  (requires ``--yes``), collapse exact-duplicate rows, report deltas.
  Cache refresh (duckdb ``e_*``) rides the standard graph rebuild after.
- **S4** ``--check`` / ``--baseline`` — collision-cluster count vs the
  recorded baseline; a rise means a writer is regressing.

Conservative by construction: the normalisation key folds case,
punctuation and footnote markers only — ``HDFC Bank`` vs ``HDFC
Securities`` never cluster; token-transposition pairs are REPORTED
separately and never auto-merged.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.core.entity_identity import (  # noqa: E402
    FOOTNOTE_RE,
    is_person_name,
    name_key as _name_key,
    speaker_names as _speaker_names,
    token_sort_key as _token_sort_key,
)

PLAN_PATH = _REPO_ROOT / "findata" / "Misc" / "_name_merge_plan.txt"
BASELINE_PATH = _REPO_ROOT / "findata" / "Misc" / "_name_dedup_baseline.txt"
LEDGER_PATH = _REPO_ROOT / "findata" / "Misc" / "_name_fold_ledger.jsonl"


def normalise(name: str) -> str:
    """The census key — delegates to the one home
    (helpers.core.entity_identity.name_key): the dedup census, the
    write paths and the resolver must never fold differently."""
    return _name_key(name)


def token_sort_key(name: str) -> str:
    """Order-insensitive secondary key (report-only clustering — never
    a merge key): 'IndianOil Adani Gas' ≡ 'Adani Gas IndianOil'."""
    return _token_sort_key(name)


QUOTES_PATH = _REPO_ROOT / "findata" / "Super_Sectors" / "Quotes.md"


def is_footnote_variant(name: str) -> bool:
    """True when stripping the marker changes the name (a `**` class
    variant). Such names can never win canonical selection."""
    return bool(FOOTNOTE_RE.search(name.rstrip()))


def _load_graph(con) -> tuple[list[str], dict[str, int], set[str]]:
    """(distinct names, degree per name, listed-entity set)."""
    names = sorted(
        {
            r[0]
            for r in con.execute(
                "SELECT source FROM graph_edges UNION SELECT target FROM graph_edges"
            ).fetchall()
            if r[0]
        }
    )
    degree: dict[str, int] = dict.fromkeys(names, 0)
    for (n,) in con.execute(
        "SELECT source FROM graph_edges UNION ALL SELECT target FROM graph_edges"
    ).fetchall():
        if n in degree:
            degree[n] += 1
    listed = {
        r[0]
        for r in con.execute(
            "SELECT DISTINCT source FROM graph_edges WHERE edge_type = 'listed_on_index'"
        ).fetchall()
    }
    return names, degree, listed


def cluster_names(names: list[str]) -> dict[str, list[str]]:
    """Group names by the normalisation key; only multi-member groups
    are collisions."""
    groups: dict[str, list[str]] = defaultdict(list)
    for n in names:
        groups[normalise(n)].append(n)
    return {k: sorted(v) for k, v in groups.items() if len(v) > 1}


def canonical_of(cluster: list[str], degree: dict[str, int], listed: set[str]) -> str:
    """Canonical choice, first rule wins (proposal §S1):

    (a) an exact listed-entity name; (b) highest total edge degree
    (ties → lexicographic, stable); footnote-marker variants can never
    win. The outcome is deterministic for a given graph state.
    """
    eligible = [n for n in cluster if not is_footnote_variant(n)]
    if not eligible:  # pathological all-footnote cluster: base-form wins
        eligible = sorted(cluster, key=len)
    for n in eligible:
        if n in listed:
            return n
    return max(eligible, key=lambda n: (degree.get(n, 0), n))


def census(con) -> dict:
    """Cluster census + per-cluster canonical + variant stats. Person
    clusters are EXCLUDED from the mergeable census (the #293
    person-resolver arc owns them) and reported separately."""
    names, degree, listed = _load_graph(con)
    clusters = cluster_names(names)
    speakers = _speaker_names()
    plan = []
    persons = []
    edge_rows_touched = 0
    for key in sorted(clusters):
        members = clusters[key]
        # A listed entity is never a person: the exemption un-blocks
        # company clusters whose spelling merely LOOKS person-shaped
        # (A. O. Smith, Dr. Reddy's subsidiaries).
        if any(m in listed for m in members):
            persons_check = False
        else:
            persons_check = any(is_person_name(m) or normalise(m) in speakers for m in members)
        if persons_check:
            persons.append({"key": key, "variants": members})
            continue
        cluster_edge_rows = sum(degree.get(m, 0) for m in members)
        plan.append(
            {
                "key": key,
                "canonical": canonical_of(members, degree, listed),
                "variants": members,
                "listed_hit": any(m in listed for m in members),
                "edge_rows": cluster_edge_rows,
            }
        )
        edge_rows_touched += cluster_edge_rows
    transpositions = _transposition_clusters(names, clusters)
    return {
        "n_names": len(names),
        "n_clusters": len(plan),
        "clusters": plan,
        "edge_rows_touched": edge_rows_touched,
        "footnote_names": sum(1 for n in names if is_footnote_variant(n)),
        "transpositions": transpositions,
        "person_clusters": persons,
    }


def _transposition_clusters(names: list[str], already: dict[str, list[str]]) -> list[dict]:
    """Token-sort collisions among names NOT already in a normalise
    multi-cluster (report-only — never merged without operator ruling).
    Membership in a reported cluster disqualifies a name here: the
    section stays strictly additive."""
    multi_members = {n for v in already.values() for n in v}
    groups: dict[str, list[str]] = defaultdict(list)
    for n in names:
        if n in multi_members:
            continue
        groups[token_sort_key(n)].append(n)
    out = []
    for k, v in groups.items():
        if len(v) > 1:
            out.append({"key": k, "variants": sorted(v)})
    return sorted(out, key=lambda d: d["key"])


def write_plan(cen: dict, path: Path = PLAN_PATH) -> Path:
    """Human-reviewable plan artifact (the S2 gate) + the machine form.
    Header carries the census + rule; one block per cluster;
    transpositions appended as a review-only section. The JSONL twin
    (``_name_merge_plan.jsonl``) carries VERBATIM names — some entity
    names contain raw newlines, which a line-format artifact cannot
    (and the S2 apply must not guess about)."""
    jsonl_path = path.with_suffix(".jsonl")
    lines = [
        "# Entity-name merge plan (entity_name_dedup S1 — DRY-RUN artifact)",
        f"# clusters: {cen['n_clusters']} | names: {cen['n_names']} | "
        f"footnote names: {cen['footnote_names']} | "
        f"edge rows touched: {cen['edge_rows_touched']}",
        "# canonical rule: listed-entity > degree > lex; footnote `**` variants never win",
        "# S2 applies ONLY on operator sign-off of this file (name_dedup.py --apply --yes)",
        f"# machine form: {jsonl_path.name} (verbatim names; regenerate together)",
        "",
    ]
    jsonl_rows = []
    for c in cen["clusters"]:
        tag = " [listed]" if c["listed_hit"] else ""
        display = lambda s: s.replace("\n", " ")  # noqa: E731  # txt readability only
        lines.append(
            f"== {display(c['canonical'])}{tag}  (key: {c['key']}; edge rows {c['edge_rows']})"
        )
        for v in c["variants"]:
            mark = "" if v == c["canonical"] else "  -> fold"
            lines.append(f"   {display(v)}{mark}")
        lines.append("")
        jsonl_rows.append(
            {
                "canonical": c["canonical"],
                "variants": [v for v in c["variants"] if v != c["canonical"]],
                "key": c["key"],
                "listed_hit": c["listed_hit"],
            }
        )
    if cen["transpositions"]:
        lines.append("# -- token-transposition pairs (REPORT-ONLY, never auto-merged) --")
        for t in cen["transpositions"]:
            lines.append(f"   {' | '.join(t['variants'])}")
        lines.append("")
    if cen.get("person_clusters"):
        lines.append(
            "# -- person-name clusters: EXCLUDED from this merge (route to the "
            "#293 person-resolver arc) --"
        )
        for pc in cen["person_clusters"]:
            lines.append(f"   {' | '.join(pc['variants'])}")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    jsonl_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in jsonl_rows),
        encoding="utf-8",
    )
    return path


def _plan_rows(jsonl_path: Path) -> list[dict]:
    if not jsonl_path.exists():
        raise SystemExit(f"plan machine form missing: {jsonl_path} — run --plan first")
    rows = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def ledger_path_for(plan_path: Path) -> Path:
    """The append-only fold ledger a given plan's apply extends.

    ``_name_merge_plan.*``   → ``_name_fold_ledger.jsonl``
    ``_person_merge_plan.*`` → ``_person_fold_ledger.jsonl``

    Derived from the plan stem so the company arc and the person arc keep
    separate ledgers (a merged spelling never routes across arcs)."""
    name = plan_path.name
    for suffix in ("_merge_plan.jsonl", "_merge_plan.txt"):
        if name.endswith(suffix):
            return plan_path.with_name(name[: -len(suffix)] + "_fold_ledger.jsonl")
    return plan_path.with_name(plan_path.stem + "_fold_ledger.jsonl")


def _append_ledger(folded: dict[str, str], ledger_path: Path) -> int:
    """EXTEND the append-only fold ledger — never replace it.

    The plan artifact is REGENERATED by ``--plan`` after every merge
    (clusters→0), which would silently drop the fold map the write paths
    consult — ``entity_identity.fold_registry`` promises a signed fold
    keeps routing old spellings to the canonical *forever*. The ledger is
    the durable half: one JSON row per folded spelling, VERBATIM (names
    may contain raw newlines, which only the machine form survives),
    appended once and never rewritten. Idempotent: an already-recorded
    (variant, canonical) pair is not re-appended, so re-running ``--apply``
    on an unchanged plan is a no-op here. Returns rows appended."""
    have: set[tuple[str, str]] = set()
    if ledger_path.exists():
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                have.add((row["variant"], row["canonical"]))
    fresh = sorted((v, c) for v, c in folded.items() if (v, c) not in have)
    if not fresh:
        return 0
    stamp = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    with ledger_path.open("a", encoding="utf-8") as fh:
        for variant, canonical in fresh:
            fh.write(
                json.dumps(
                    {"variant": variant, "canonical": canonical, "folded_at": stamp},
                    ensure_ascii=False,
                )
                + "\n"
            )
    return len(fresh)


def apply_plan(  # noqa: C901
    con,
    plan_path: Path = PLAN_PATH,
    *,
    yes: bool = False,
    skip_persons: bool = True,
    ledger_path: Path | None = None,
) -> dict:
    """S2: re-point variants → canonical per the plan artifacts, then
    collapse exact-duplicate rows. Gated: refuses without ``yes`` and
    refuses missing artifacts. Folds by VERBATIM name from the JSONL
    machine form (newlines and all). ``skip_persons`` is the company
    merge's person defense — the PERSON arc's own apply passes False
    (its plan is all person folds by construction). Row accounting is
    exact: re-pointing never deletes; collisions against UNIQUE(source,
    target, edge_type) and intra-variant self-loop rows are
    consolidated first (both reported)."""
    jsonl_path = plan_path.with_suffix(".jsonl")
    if not plan_path.exists() or not jsonl_path.exists():
        raise SystemExit(f"plan artifacts missing: {plan_path} (+.jsonl) — run --plan first")
    if not yes:
        raise SystemExit("S2 is gated on operator sign-off: review the plan, then --apply --yes")
    folded: dict[str, str] = {}
    for row in _plan_rows(jsonl_path):
        canon = row["canonical"]
        if skip_persons and is_person_name(canon):  # stale artifact: person cluster → skip
            continue
        for variant in row["variants"]:
            if skip_persons and is_person_name(variant):
                continue
            folded[variant] = canon
    if not folded:
        # An all-person (stale) plan defuses to a no-op, not an error:
        # the S2 gate is operator sign-off, and a signed-but-person-only
        # plan must still be inert.
        before = con.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        return {
            "variants_folded": 0,
            "rows_repointed": 0,
            "duplicate_rows_removed": 0,
            "collapsing_duplicates": 0,
            "entities_folded": 0,
            "entities_retained_referenced": 0,
            "rows_before": before,
            "rows_after": before,
            "zero_loss_ok": True,
            "fold_ledger_appended": 0,
            "fold_ledger": str(ledger_path or ledger_path_for(plan_path)),
            "note": "no applicable fold rows (person clusters excluded)",
        }
    before = con.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
    repointed = 0
    collapsed = 0
    with con:
        for variant, canon in sorted(folded.items()):
            # Relations already stored under BOTH spellings: the re-point
            # would violate UNIQUE(source, target, edge_type). Consolidate
            # first — the canonical row keeps its id/properties, the
            # variant row drops (its relation survives through the
            # canonical row; count reported).
            for side in ("source", "target"):
                other = "target" if side == "source" else "source"
                collapsed += con.execute(
                    "DELETE FROM graph_edges WHERE id IN ("
                    "  SELECT v.id FROM graph_edges v JOIN graph_edges c"
                    f"   ON c.{side} = ? AND v.{side} = ?"
                    f"  AND c.{other} = v.{other} AND c.edge_type = v.edge_type"
                    ")",
                    (canon, variant),
                ).rowcount
            # Edges BETWEEN the two spellings themselves: post-fold these
            # would be self-loops (CHECK source != target) — intra-variant
            # artifacts, dropped and reported.
            collapsed += con.execute(
                "DELETE FROM graph_edges WHERE (source = ? AND target = ?)"
                " OR (source = ? AND target = ?)",
                (variant, canon, canon, variant),
            ).rowcount
            cur = con.execute(
                "UPDATE graph_edges SET source = ? WHERE source = ?", (canon, variant)
            ).rowcount
            cur += con.execute(
                "UPDATE graph_edges SET target = ? WHERE target = ?", (canon, variant)
            ).rowcount
            repointed += cur
        # Collapse exact duplicates (oldest id kept) — the re-point's
        # expected consequence, reported not hidden.
        dupes = con.execute(
            "DELETE FROM graph_edges WHERE id NOT IN ("
            "  SELECT MIN(id) FROM graph_edges"
            "  GROUP BY source, target, edge_type, source_ref, symmetric)"
        ).rowcount
    # Registry fold: with the edges re-pointed, no edge references a
    # variant entity — the registry rows go too, or the next vigil sync
    # re-creates the variants from them (the reversibility the proposal
    # flags). Rows still referenced by OTHER FK estates are retained
    # and reported; the fold map keeps resolving them regardless.
    entities_folded = 0
    entities_retained = 0
    has_entities = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='entities'"
    ).fetchone()[0]
    if has_entities:
        for variant in folded:
            try:
                with con:
                    entities_folded += con.execute(
                        "DELETE FROM entities WHERE name = ?", (variant,)
                    ).rowcount
            except sqlite3.IntegrityError:
                entities_retained += 1
    after = con.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
    ledger = ledger_path or ledger_path_for(plan_path)
    return {
        "variants_folded": len(folded),
        "rows_repointed": repointed,
        "duplicate_rows_removed": dupes + collapsed,
        "collapsing_duplicates": collapsed,
        "entities_folded": entities_folded,
        "entities_retained_referenced": entities_retained,
        "rows_before": before,
        "rows_after": after,
        "zero_loss_ok": before - (dupes + collapsed) == after,
        "fold_ledger_appended": _append_ledger(folded, ledger),
        "fold_ledger": str(ledger),
    }


def check(con, baseline_path: Path = BASELINE_PATH) -> int:
    """S4 gate: exit 1 when the live cluster count exceeds the recorded
    baseline. Missing baseline → report only (exit 0): the baseline is
    set post-S2 via ``--baseline``."""
    cen = census(con)
    if not baseline_path.exists():
        print(f"dup-census: {cen['n_clusters']} clusters (no baseline recorded — informational)")
        return 0
    baseline = int(baseline_path.read_text(encoding="utf-8").strip())
    if cen["n_clusters"] > baseline:
        print(
            f"FAIL: dup-census {cen['n_clusters']} > baseline {baseline} — "
            f"a writer is creating name variants"
        )
        return 1
    print(f"ok: dup-census {cen['n_clusters']} <= baseline {baseline}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Entity-name dedup: census / plan / check / apply")
    p.add_argument("--census", action="store_true", help="print the collision census")
    p.add_argument("--plan", action="store_true", help="write the merge-plan artifact (dry-run)")
    p.add_argument(
        "--transpositions", action="store_true", help="include token-sort pairs in the plan"
    )
    p.add_argument(
        "--check", action="store_true", help="S4 gate: census vs baseline (exit 1 on growth)"
    )
    p.add_argument(
        "--baseline", action="store_true", help="record the CURRENT count as the S4 baseline"
    )
    p.add_argument("--apply", action="store_true", help="S2: apply the plan (requires --yes)")
    p.add_argument("--yes", action="store_true", help="confirm the S2 write")
    args = p.parse_args(argv)

    con = connect()
    try:
        if args.apply:
            res = apply_plan(con, yes=args.yes)
            print(json.dumps(res, indent=2))
            print("next: refresh the duckdb graph caches (standard graph rebuild)")
            return 0
        cen = census(con)
        if args.census:
            print(
                f"names={cen['n_names']} clusters={cen['n_clusters']} "
                f"person_clusters={len(cen['person_clusters'])} "
                f"edge_rows_touched={cen['edge_rows_touched']} "
                f"footnote_names={cen['footnote_names']} "
                f"transpositions={len(cen['transpositions'])}"
            )
            for c in cen["clusters"][:10]:
                print(
                    f"  {c['canonical']}  <- {len(c['variants'])} variants, {c['edge_rows']} edge rows"
                )
            return 0
        if args.plan:
            if not args.transpositions:
                cen["transpositions"] = []
            path = write_plan(cen)
            print(f"plan -> {path}")
            print(f"clusters={cen['n_clusters']} (operator review gates S2: --apply --yes)")
            return 0
        if args.baseline:
            BASELINE_PATH.write_text(str(cen["n_clusters"]) + "\n", encoding="utf-8")
            print(f"baseline <- {cen['n_clusters']} ({BASELINE_PATH})")
            return 0
        if args.check:
            return check(con)
        p.print_help()
        return 2
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
