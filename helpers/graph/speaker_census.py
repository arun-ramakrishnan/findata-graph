#!/usr/bin/python3
"""Speaker census — S1 of person_ontology_spine (REPORT-ONLY).

The quote corpus's speaker population was never in the identity registry.
Measured 2026-10-19 on the live store:

- 1,419 distinct ``quotes.speaker_name`` strings across 6,269 non-NULL rows
  (2,418 of 8,687 quotes carry no speaker at all);
- those strings collapse to **1,410 clusters** under the person-dedup key
  (``_merge_line``/``_base_key`` from :mod:`helpers.graph.person_dedup`) —
  only 9 clusters hold more than one spelling;
- **3 of 1,410 clusters** match an existing ``person`` entity by base key.
  The 277 person entities are SHP-holder legal-line names (UPPERCASE, no
  notes); the speaker corpus is title-cased journalist attribution. The two
  populations are effectively disjoint.

That gap is the whole point of this slice: a person spine that cannot see
the speakers is not a spine. This module builds the resolution plan the
later slices consume — **it writes nothing to the database**. S2 (linking
``quotes`` to person entities) is gated on operator review of the plan
artifact, exactly like ``person_dedup``'s S1→S2 boundary.

Merge policy is inherited, never re-implemented: the HUF-into-Karta ruling
of 2026-10-16 (:func:`helpers.graph.person_dedup._merge_line`), the
never-merge boundary across legal lines (a trust owns, its trustee manages),
and the S1 company-shaped over-inclusion routing all come from the same
functions the entity census uses. Two censuses, one policy.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.core.entity_identity import _COMPANY_MARKER_RE, name_key  # noqa: E402
from helpers.graph.person_dedup import _base_key, _merge_line  # noqa: E402

PLAN_PATH = _REPO_ROOT / "findata" / "Misc" / "_speaker_resolution_plan.txt"
BASELINE_PATH = _REPO_ROOT / "findata" / "Misc" / "_speaker_census_baseline.txt"


def _speaker_rows(con) -> list[tuple[str, int, str | None]]:
    """``(speaker_name, quote_count, most-common picked title)`` per string.

    Title is evidence, not identity: a speaker's title drifts between
    editions ("MD & CEO" / "CEO and MD"), so the plan reports ONE
    representative title per string and never keys on it.
    """
    rows = con.execute(
        """
        SELECT speaker_name,
               COUNT(*) AS n,
               (
                   SELECT speaker_title FROM quotes q2
                   WHERE q2.speaker_name = q1.speaker_name
                     AND q2.speaker_title IS NOT NULL
                     AND q2.speaker_title <> ''
                   GROUP BY speaker_title
                   ORDER BY COUNT(*) DESC, speaker_title
                   LIMIT 1
               ) AS title
        FROM quotes q1
        WHERE speaker_name IS NOT NULL AND speaker_name <> ''
        GROUP BY speaker_name
        """
    ).fetchall()
    return [(r[0], int(r[1]), r[2]) for r in rows]


def _display_rank(name: str, quotes: int) -> tuple:
    """Order candidate spellings for the canonical display form.

    Most-quoted spelling wins; a title-cased spelling beats an ALL-CAPS or
    all-lowercase one in the same cluster; the remaining tie breaks on the
    string so the pick is deterministic. Punctuation no longer decides the
    winner by accident (``B Thiagarajan`` over ``B. Thiagarajan``) — the
    canonical is a REVIEW artifact, so a stable, human-looking pick
    matters more than any linguistic rule.
    """
    title_case = name[:1].isupper() and not name.isupper() and not name.islower()
    return (quotes, title_case, -len(name), name)


def speaker_census(con) -> dict:
    """Cluster every quote speaker string and score it against the registry.

    Returns ``clusters`` (one per identity), ``never_merge`` (base keys
    spanning legal lines), ``company_shaped`` (attribution strings that are
    actually company names) and the headline ``stats``.
    """
    persons: dict[str, str] = {}
    for (n,) in con.execute("SELECT name FROM entities WHERE entity_type='person'").fetchall():
        if n:
            persons.setdefault(name_key(n), n)

    groups: dict[tuple[str, str], list[tuple[str, int, str | None]]] = defaultdict(list)
    for name, n, title in _speaker_rows(con):
        groups[(_merge_line(name), _base_key(name))].append((name, n, title))

    clusters: list[dict] = []
    for (line, base), members in sorted(groups.items()):
        canonical = max(members, key=lambda m: _display_rank(m[0], m[1]))[0]
        quotes = sum(m[1] for m in members)
        titles = sorted({m[2] for m in members if m[2]})
        clusters.append(
            {
                "canonical": canonical,
                "variants": sorted(m[0] for m in members),
                "line": line,
                "base": base,
                "quotes": quotes,
                "titles": titles[:3],
                "entity": persons.get(base),
            }
        )

    # Never-merge: one base spelled across several legal lines. Measured zero
    # on the speaker corpus (2026-10-19) — reported, never gated, because a
    # trust/trustee pair is a CORRECT refusal, not a defect.
    by_base: dict[str, set[str]] = defaultdict(set)
    for line, base in groups:
        by_base[base].add(line)
    never_merge = [
        {
            "base": b,
            "lines": sorted(ls),
            "clusters": [c["canonical"] for c in clusters if c["base"] == b],
        }
        for b, ls in sorted(by_base.items())
        if len(ls) > 1
    ]

    company_shaped = [
        {
            "canonical": c["canonical"],
            "variants": c["variants"],
            "reason": "company marker in spelling",
        }
        for c in clusters
        if any(_COMPANY_MARKER_RE.search(v) for v in c["variants"])
    ]

    multi = [c for c in clusters if len(c["variants"]) > 1]
    matched = [c for c in clusters if c["entity"]]
    return {
        "clusters": clusters,
        "never_merge": never_merge,
        "company_shaped": company_shaped,
        "stats": {
            "speaker_strings": sum(len(c["variants"]) for c in clusters),
            "clusters": len(clusters),
            "multi_spelling_clusters": len(multi),
            "quote_rows": sum(c["quotes"] for c in clusters),
            "matched_entity": len(matched),
            "unmatched_entity": len(clusters) - len(matched),
        },
    }


def write_plan(cen: dict, path: Path = PLAN_PATH) -> Path:
    """Write the reviewable plan (``.txt`` for humans, ``.jsonl`` verbatim)."""
    s = cen["stats"]
    jsonl_path = path.with_suffix(".jsonl")
    lines = [
        "# Speaker resolution plan (person_ontology_spine S1 — REPORT-ONLY)",
        f"# strings: {s['speaker_strings']} | clusters: {s['clusters']} | "
        f"multi-spelling: {s['multi_spelling_clusters']} | quotes: {s['quote_rows']}",
        f"# already a person entity: {s['matched_entity']} | "
        f"NO person entity: {s['unmatched_entity']}",
        "# rule: identity policy is inherited from person_dedup (HUF folds into its",
        "# Karta; legal lines never merge; company-shaped spellings route to the",
        "# company arc). S2 links quotes ONLY after operator review of this file.",
        "",
    ]
    rows: list[dict] = []
    for c in sorted(cen["clusters"], key=lambda c: (-c["quotes"], c["canonical"])):
        mark = f"  == entity: {c['entity']}" if c["entity"] else "  == NO entity (S2 candidate)"
        lines.append(f"{c['canonical']}  [{c['line']}]  quotes={c['quotes']}{mark}")
        if c["titles"]:
            lines.append(f"   titles: {' | '.join(c['titles'])}")
        for v in c["variants"]:
            if v != c["canonical"]:
                lines.append(f"   {v}  -> fold")
        lines.append("")
        rows.append(
            {
                "canonical": c["canonical"],
                "variants": [v for v in c["variants"] if v != c["canonical"]],
                "line": c["line"],
                "quotes": c["quotes"],
                "titles": c["titles"],
                "entity": c["entity"],
            }
        )
    if cen["never_merge"]:
        lines.append("# -- NEVER-MERGE (legal lines differ — identity boundary) --")
        for nm in cen["never_merge"]:
            lines.append(f"   {' | '.join(nm['clusters'])}  (lines: {', '.join(nm['lines'])})")
        lines.append("")
    if cen["company_shaped"]:
        lines.append("# -- company-shaped: attribution is a company, not a person --")
        for cs in cen["company_shaped"]:
            lines.append(f"   {' | '.join(cs['variants'])}")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    jsonl_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    return path


def check(con, baseline_path: Path = BASELINE_PATH) -> int:
    """Standing gate: multi-spelling clusters + company-shaped attribution.

    Both are regressions — a new spelling of a known speaker, or a company
    name recorded as a person. Cluster COUNT is not gated: every newsletter
    mints new speakers, and gating a number that must grow is a false alarm
    generator (the lesson ``person_dedup.check`` records).
    """
    cen = speaker_census(con)
    n = cen["stats"]["multi_spelling_clusters"] + len(cen["company_shaped"])
    note = (
        f" (clusters {cen['stats']['clusters']} reported, not gated)"
        if cen["stats"]["clusters"]
        else ""
    )
    if not baseline_path.exists():
        print(f"speaker-census: {n} regression-shaped clusters{note} (no baseline — informational)")
        return 0
    baseline = int(baseline_path.read_text(encoding="utf-8").strip())
    if n > baseline:
        print(f"FAIL: speaker-census {n} > baseline {baseline}{note} — a new spelling appeared")
        return 1
    print(f"ok: speaker-census {n} <= baseline {baseline}{note}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Speaker census (person_ontology_spine S1)")
    p.add_argument("--census", action="store_true", help="print the census summary")
    p.add_argument("--plan", action="store_true", help="write the resolution plan (dry-run)")
    p.add_argument("--check", action="store_true", help="standing gate vs baseline")
    p.add_argument("--baseline", action="store_true", help="record the CURRENT count as baseline")
    p.add_argument("--json", action="store_true", help="print the full census as JSON")
    args = p.parse_args(argv)

    con = connect()
    try:
        cen = speaker_census(con)
        if args.json:
            print(json.dumps(cen, indent=2, ensure_ascii=False))
            return 0
        s = cen["stats"]
        if args.plan:
            path = write_plan(cen)
            print(f"plan -> {path} (+.jsonl)")
        if args.baseline:
            n = s["multi_spelling_clusters"] + len(cen["company_shaped"])
            BASELINE_PATH.write_text(str(n) + "\n", encoding="utf-8")
            print(f"baseline <- {n} ({BASELINE_PATH})")
        if args.check:
            return check(con)
        if args.census or not (args.plan or args.baseline):
            print(
                f"strings={s['speaker_strings']} clusters={s['clusters']} "
                f"multi_spelling={s['multi_spelling_clusters']} quotes={s['quote_rows']} "
                f"matched_entity={s['matched_entity']} unmatched={s['unmatched_entity']}"
                f" never_merge={len(cen['never_merge'])} company_shaped={len(cen['company_shaped'])}"
            )
            for c in sorted(cen["clusters"], key=lambda c: -c["quotes"])[:10]:
                ent = c["entity"] or "-"
                print(f"  {c['quotes']:5}  {c['canonical']:32} entity={ent}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
