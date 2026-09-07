#!/usr/bin/env python3
"""Triage the `findata/Misc/quote_entity_worklist.json` queue
(triage_pending_quotes — quote_capture_coverage S7).

Mirrors `triage_pending_relations.py`: the worklist that
`quote_coverage_audit.py` emits is bucketed into an annotated-ready
decisions file; user decisions are validated and applied; aliases
persist to `findata/Misc/quote_aliases.json` (runtime-loaded by
`derive_insights._resolve_ladder`); stubs are emitted for the user-held
entity flow; entries auto-close on the next `derive-insights --apply`.

Decision actions: alias:<Entity> | stub | discard

Usage:
    python3 helpers/graph/triage_pending_quotes.py                 # report
    python3 helpers/graph/triage_pending_quotes.py --apply-decisions

Exit codes: 0 ok, 1 bad decisions / validation failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
from helpers.core.db import connect  # noqa: E402

WORKLIST = _REPO_ROOT / "findata" / "Misc" / "quote_entity_worklist.json"
ALIASES = _REPO_ROOT / "findata" / "Misc" / "quote_aliases.json"
DECISIONS = _REPO_ROOT / "findata" / "Misc" / "quote_triage_decisions.jsonl"
REPORT = _REPO_ROOT / "findata" / "Misc" / "quote_triage_report.md"

# Mangled-canonical signature: converter artifacts neither the exact nor the
# qualifier tiers can fix — trailing `I` / `]` / `,` / `;`, stray `_`, a
# literal backslash (escaped `&` that unescape+fold already failed to
# resolve), or a doubled first word ("Poonawala Poonawalla Fincorp").
_GARBAGE_RE = re.compile(r"(\s+I$|\]$|[,;]\s*$|_\S*$|\\)", re.I)


def _doubled_word(canonical: str) -> bool:
    words = canonical.split()
    return len(words) >= 2 and len(words[0]) > 2 and words[0].lower() == words[1].lower()


def bucket(canonical: str, suggestions: list[str]) -> str:
    if suggestions:
        return "alias_candidate"
    if _GARBAGE_RE.search(canonical) or _doubled_word(canonical):
        return "garbage_shape"
    return "stub_candidate"


def _entry_id(canonical: str) -> str:
    return hashlib.sha256(canonical.encode()).hexdigest()[:10]


def build_decisions(wl: dict) -> list[dict]:
    rows = []
    for canonical in sorted(wl.get("entries", {})):
        e = wl["entries"][canonical]
        if e.get("status") != "open":
            continue
        sugg = e.get("suggestions", [])
        rows.append(
            {
                "id": _entry_id(canonical),
                "canonical": canonical,
                "bucket": bucket(canonical, sugg),
                "suggestions": sugg,
                "notes": e.get("notes", [])[:3],
                "vss_hint": "",
                "decision": "",
            }
        )
    return rows


def write_decisions(rows: list[dict]) -> None:
    DECISIONS.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def load_decisions() -> list[dict]:
    rows = []
    for line in DECISIONS.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def cmd_report(vss_hints: bool = True) -> int:
    wl = json.loads(WORKLIST.read_text())
    rows = build_decisions(wl)
    # VSS hints (S7 follow-up): for entries WITHOUT a jaccard suggestion,
    # embed the canonical and take the nearest company (get_tickers.vss_match).
    # The hint is EYEBALL-ONLY — it frequently lands on semantically adjacent
    # competitors (Tesla -> Tata Elxsi, Nike -> Campus Activewear), so it is
    # never pre-filled as a decision and never auto-applied (D4).
    if vss_hints:
        from helpers.core.get_tickers import vss_match
        from helpers.core.db import connect as _db_connect

        _c = _db_connect()
        _entities = [
            r[0]
            for r in _c.execute("SELECT name FROM entities WHERE entity_type='company'").fetchall()
        ]
        _c.close()
        hinted = 0
        for r in rows:
            if r["bucket"] == "alias_candidate":
                continue
            match, score = vss_match(r["canonical"], _entities)
            if match:
                r["vss_hint"] = f"{match} ({score:.2f})"
                hinted += 1
        if hinted:
            print(f"VSS hints added: {hinted}/{len(rows)}", file=sys.stderr)
    write_decisions(rows)
    counts = Counter(r["bucket"] for r in rows)
    lines = [
        "# Quote worklist triage report",
        "",
        f"open canonicals: {len(rows)} — "
        + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())),
        "",
        "Annotate `decision` in `findata/Misc/quote_triage_decisions.jsonl`",
        "(`alias:<Entity>` | `stub` | `discard`), then:",
        "`python3 helpers/graph/triage_pending_quotes.py --apply-decisions`",
        "",
    ]
    for b in sorted(counts):
        lines.append(f"## {b} ({counts[b]})")
        for r in rows:
            if r["bucket"] == b:
                sugg = f" -> {r['suggestions'][0]!r}" if r["suggestions"] else ""
                hint = f"  [vss: {r['vss_hint']}]" if r.get("vss_hint") else ""
                lines.append(f"- `{r['canonical']}`{sugg}{hint}")
    REPORT.write_text("\n".join(lines) + "\n")
    print(
        f"decisions file: {len(rows)} open canonicals "
        f"({', '.join(f'{k} {v}' for k, v in sorted(counts.items()))})"
    )
    print(f"report: {REPORT}")
    print("annotate decisions, then re-run with --apply-decisions")
    return 0


def cmd_apply() -> int:  # noqa: C901
    rows = load_decisions()
    decided = [r for r in rows if r.get("decision", "").strip()]
    if not decided:
        print("no annotated decisions found — nothing to apply")
        return 0

    conn = connect()
    entities = {r[0].lower() for r in conn.execute("SELECT name FROM entities").fetchall()}
    conn.close()

    aliases = json.loads(ALIASES.read_text()) if ALIASES.exists() else {}
    failures = []
    alias_rows: list[tuple[str, str]] = []
    stubs: list[str] = []
    for r in decided:
        d = r["decision"].strip()
        canonical = r["canonical"]
        if d.startswith("alias:"):
            target = d.split(":", 1)[1].strip()
            if target.lower() not in entities:
                failures.append(
                    f"{r['id']} ({canonical}): alias target {target!r} does not exist in entities"
                )
                continue
            aliases[canonical.lower()] = target
            alias_rows.append((canonical, target))
        elif d == "stub":
            stubs.append(canonical)
        elif d == "discard":
            continue
        else:
            failures.append(f"{r['id']} ({canonical}): unknown decision {d!r}")
    if failures:
        print("VALIDATION FAILURES — nothing applied:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    ALIASES.write_text(json.dumps(aliases, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    applied_ids = {r["id"] for r in decided}
    wl = json.loads(WORKLIST.read_text())
    for canonical, e in wl["entries"].items():
        if _entry_id(canonical) in applied_ids:
            e["status"] = "decided"
            e["decision"] = next(r["decision"] for r in decided if r["id"] == _entry_id(canonical))
    WORKLIST.write_text(json.dumps(wl, indent=1, sort_keys=True, ensure_ascii=False) + "\n")

    print(f"aliases merged: {len(alias_rows)} (now {len(aliases)} total) -> {ALIASES}")
    for c, tgt in alias_rows:
        print(f"  {c!r} -> {tgt!r}")
    if stubs:
        print(f"stubs to create (user-held, {len(stubs)}):")
        for s in stubs:
            print(f"  {s}")
    print(
        "re-run `derive-insights --apply` to re-home quotes; worklist "
        "entries auto-close when canonicals resolve."
    )
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Triage the quote entity worklist (S7, triage_pending_quotes)"
    )
    ap.add_argument(
        "--apply-decisions",
        action="store_true",
        help="apply annotated decisions (default: regenerate report)",
    )
    args = ap.parse_args(argv)
    return cmd_apply() if args.apply_decisions else cmd_report()


if __name__ == "__main__":
    raise SystemExit(main())
