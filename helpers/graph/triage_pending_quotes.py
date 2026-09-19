#!/usr/bin/env python3
"""Triage the `findata/Misc/quote_entity_worklist.json` queue
(triage_pending_quotes — quote_capture_coverage S7).

Mirrors `triage_pending_relations.py`: the worklist that
`quote_coverage_audit.py` emits is bucketed into an annotated-ready
decisions file; user decisions are validated and applied; aliases
persist to `findata/Misc/quote_aliases.json` (runtime-loaded by
`derive_insights._resolve_ladder`); stubs are emitted for the user-held
entity flow; entries auto-close on the next `derive-insights --apply`.

Decision actions: alias:<Entity> | stub | stub|cin=<CIN> | discard
(`stub|cin=` marks the stub's CIN at triage time — validated on apply
via helpers/core/cin.py, which then prints the ready-to-run
backfill_identifiers --set-cin command for after the entity exists).

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
from helpers.core.cin import parse_cin  # noqa: E402
from helpers.core.db import connect  # noqa: E402

WORKLIST = _REPO_ROOT / "findata" / "Misc" / "quote_entity_worklist.json"
ALIASES = _REPO_ROOT / "findata" / "Misc" / "quote_aliases.json"
DECISIONS = _REPO_ROOT / "findata" / "Misc" / "quote_triage_decisions.jsonl"
REPORT = _REPO_ROOT / "findata" / "Misc" / "quote_triage_report.md"
REVIEW_JOURNAL_DIR = _REPO_ROOT / "outputs" / "quotes_review"

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
        "(`alias:<Entity>` | `stub` | `stub|cin=<CIN>` | `discard`), then:",
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
    cin_stubs: list[tuple[str, str]] = []
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
        elif d == "stub" or d.startswith("stub|cin="):
            # stub|cin=<CIN>: the CIN is validated NOW (bad parse blocks
            # the batch) and echoed as a ready-to-run --set-cin command
            # for after the user creates the stub entity.
            if d.startswith("stub|cin="):
                cin_value = d.split("=", 1)[1].strip()
                p = parse_cin(cin_value)
                if not p.ok:
                    failures.append(f"{r['id']} ({canonical}): cin {cin_value!r}: {p.message}")
                    continue
                assert p.value is not None  # noqa: S101  # ty narrowing; ok implies a parsed 21-char value
                cin_stubs.append((canonical, p.value))
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
    if cin_stubs:
        print(f"stub CINs — run after the stub entities exist ({len(cin_stubs)}):")
        for name, cin in cin_stubs:
            print(
                f'  python3 helpers/misc/backfill_identifiers.py --apply --set-cin "{name}={cin}"'
            )
    print(
        "re-run `derive-insights --apply` to re-home quotes; worklist "
        "entries auto-close when canonicals resolve."
    )
    return 0


# --------------------------------------------------------------------------- #
# review — journaled sitting over the open worklist (review-kit S3)          #
# --------------------------------------------------------------------------- #
_REVIEW_FILE_FIELDS = ("id", "canonical", "bucket", "suggestions", "notes", "vss_hint")


def _entity_lookup(query: str, entities: dict[str, str]) -> tuple[str | None, list[str]]:
    """Resolve an alias target against entities: exact (case-insensitive)
    hit, else substring (space-stripped too) + acronym-initials candidates.

    ``ongc`` resolves to ``Oil & Natural Gas Corporation`` via initials —
    the abbreviations operators type rarely appear inside the name.
    """
    q = query.lower()
    if q in entities:
        return entities[q], []
    cands = {
        name
        for name in entities.values()
        if q in name.lower() or q.replace(" ", "") in name.lower().replace(" ", "")
    }
    # token-prefix (1-char stem): every query token prefixes some name
    # token, optionally minus its last char ('exide industry' ->
    # 'Exide Industries' — y->ies and trailing-s plurals)
    qt = [t for t in q.split() if len(t) >= 4]
    if qt:  # guard: all() over no tokens would vacuously match everything
        for name in entities.values():
            nt = [t for t in name.lower().split() if len(t) >= 3]
            if all(
                any(n.startswith(t) or (len(t) >= 4 and n.startswith(t[:-1])) for n in nt)
                for t in qt
            ):
                cands.add(name)
    if " " not in q:
        for name in entities.values():
            initials = "".join(w[0] for w in name.split() if w.isalpha())
            if q == initials.lower():
                cands.add(name)
    return None, sorted(cands)[:8]


def review(  # noqa: C901 — keypress parsing + kit wiring, split would scatter state
    *,
    limit: int | None = None,
    input_fn=input,
    print_fn=print,
    apply: bool = True,
    journal_dir: Path | None = None,
    redecide: bool = False,
    skipped: bool = False,
) -> dict:
    """Interactive sitting over the open quote canonicals (review-kit S3).

    One keypress per canonical — the sitting only PRODUCES decision rows;
    the confirm gate appends them to the decisions file, and apply stays
    the existing ``--apply-decisions`` step (unchanged validator,
    unchanged writes; the file is no longer hand-edited):

      1/2/3      alias to suggestion n (entity-validated)
      al NAME    alias to an existing entity NAME
      st         stub (user-held entity creation)
      sc CIN     stub + CIN captured now (`stub|cin=`, parse-validated)
      d          discard
      p          park — never re-asked (journal read-back)
      ?          re-render evidence
      q / x      quit-and-review / abort the walk

    Parking keys on the stable entry id (sha256 of the canonical);
    already-decided rows (decisions-file read-back) are excluded unless
    ``--redecide``. VSS hints render only when a prior ``--report`` run
    computed them (never recomputed, never pre-filled — D4).
    """
    from helpers.core.review_kit import Journal, ReviewSession, assemble_entries, latest_action_by

    journal_path = (journal_dir or REVIEW_JOURNAL_DIR) / "journal.jsonl"
    wl = json.loads(WORKLIST.read_text())
    rows = build_decisions(wl)
    decided_rows: list[dict] = []
    decided_ids: set[str] = set()
    if DECISIONS.exists():
        for r in load_decisions():
            if r.get("decision", "").strip():
                decided_rows.append(r)
                decided_ids.add(r["id"])
    parked_ids = latest_action_by(journal_path, key_field="id")
    hint_by_id = {r["id"]: r.get("vss_hint", "") for r in rows if r.get("vss_hint")}

    def _entry(r: dict) -> dict:
        e = dict(r)
        e.setdefault("vss_hint", "")
        e["label"] = r["id"]  # kit item key == stable entry id
        return e

    open_rows = [r for r in rows if r["id"] not in decided_ids]
    for r in open_rows:
        r["vss_hint"] = hint_by_id.get(r["id"], "")
    wl_lanes = {
        "suggested": [_entry(r) for r in open_rows if r["id"] not in parked_ids],
        "promoted": [_entry(r) for r in decided_rows],
        "skipped": [_entry(r) for r in open_rows if r["id"] in parked_ids],
        "no_signal": [],
    }
    _bucket_rank = {"alias_candidate": 0, "garbage_shape": 1, "stub_candidate": 2}
    entries = assemble_entries(
        wl_lanes,
        labels_filter=None,
        redecide=redecide,
        skipped=skipped,
        sort_key=lambda e: (_bucket_rank.get(e.get("bucket", ""), 9), e["label"]),
        limit=limit,
    )

    conn = connect()
    entities = {r[0].lower(): r[0] for r in conn.execute("SELECT name FROM entities").fetchall()}
    conn.close()

    legend = [
        "  keys: 1-3 alias to suggestion n (* = suggestion is an existing entity)",
        "        al NAME  alias to an existing entity by exact name",
        "        st       stub (entity note created by hand later)",
        "        sc CIN   stub + record CIN now (validated at keypress)",
        "        d        discard — never asked again   p  park — skip, reopen via --skipped",
        "        ?        this legend + evidence        q/x  quit-and-review / abort walk",
    ]
    if entries:
        print_fn("\n".join(legend))

    def _evidence(e: dict) -> None:
        for n, sugg in enumerate(e.get("suggestions", []), 1):
            mark = "*" if sugg.lower() in entities else " "
            print_fn(f"     {mark}{n}) {sugg}")
        for note in e.get("notes", [])[:3]:
            print_fn(f"     note: {note}")
        if e.get("vss_hint"):
            print_fn(f"     vss: {e['vss_hint']}")

    def render(idx: int, total: int, e: dict) -> None:
        print_fn(f"[{idx}/{total}] `{e['id']}` {e['canonical']}  ({e.get('bucket', '?')})")
        _evidence(e)

    def ask(e: dict, note) -> dict:  # noqa: C901
        rid = e["id"]
        sugg = e.get("suggestions", [])
        while True:
            ans = input_fn("  alias 1-3 / al NAME / st / sc CIN / d / p / ? / q / x: ").strip()
            if ans in {"p", "q", "x"}:
                return {"id": rid, "action": {"p": "skip", "q": "quit", "x": "abort"}[ans]}
            if ans == "?":
                print_fn("\n".join(legend))
                _evidence(e)
                continue
            file_decision = None
            if ans in {"1", "2", "3"}:
                n = int(ans)
                if n > len(sugg):
                    print_fn(f"  no suggestion {n} — pick within 1-{len(sugg)}")
                    note({"id": rid, "action": "bad-pick", "pick": n})
                    continue
                target = sugg[n - 1]
                if target.lower() not in entities:
                    print_fn(f"  suggestion {target!r} is not an existing entity — use al/st")
                    note({"id": rid, "action": "bad-pick", "target": target})
                    continue
                file_decision = f"alias:{target}"
            elif ans.startswith("al ") or ans == "al":
                query = ans[3:].strip()
                if not query:
                    print_fn("  al <part of name> — exact, substring, or acronym (e.g. al ongc)")
                    continue
                target, cands = _entity_lookup(query, entities)
                if target is None and len(cands) == 1:
                    target = cands[0]
                    print_fn(f"  matched: {target}")
                if target is None:
                    if not cands:
                        print_fn(f"  no entity matches {query!r} — try another spelling or st")
                        note({"id": rid, "action": "bad-alias", "target": query})
                        continue
                    print_fn("  candidates:")
                    for i, c in enumerate(cands, 1):
                        print_fn(f"    {i}) {c}")
                    pick = input_fn("  pick 1-8 / or al <name> again: ").strip()
                    if pick.isdigit() and 1 <= int(pick) <= len(cands):
                        target = cands[int(pick) - 1]
                    elif pick.startswith("al "):
                        target, c2 = _entity_lookup(pick[3:].strip(), entities)
                        if target is None and len(c2) == 1:
                            target = c2[0]
                    if target is None:
                        print_fn("  ? (pick a number or al <name>)")
                        note({"id": rid, "action": "bad-alias", "target": pick})
                        continue
                file_decision = f"alias:{target}"
            elif ans == "st":
                file_decision = "stub"
            elif ans.startswith("sc "):
                cin_value = ans[3:].strip()
                p = parse_cin(cin_value)
                if not p.ok:
                    print_fn(f"  cin {cin_value!r}: {p.message}")
                    note({"id": rid, "action": "bad-cin", "cin": cin_value})
                    continue
                file_decision = f"stub|cin={p.value}"
            elif ans == "d":
                file_decision = "discard"
            else:
                print_fn("  ? (1-3 / al NAME / st / sc CIN / d / p / ? / q / x)")
                continue
            d = {"id": rid, "action": "approve", "decision": file_decision}
            for k in _REVIEW_FILE_FIELDS:
                d[k] = e.get(k, [] if k in ("suggestions", "notes") else "")
            return d

    def spec_of(d: dict, e: dict) -> str:  # noqa: ARG001 — d carries the row fields
        return f"{d['canonical']} -> {d['decision']}"

    def apply_batch(specs: list[str], decisions: list[dict]) -> tuple[list[str], list[str]]:
        out_rows = [
            {k: d.get(k, [] if k in ("suggestions", "notes") else "") for k in _REVIEW_FILE_FIELDS}
            | {"decision": d["decision"]}
            for d in decisions
            if d.get("action") == "approve"
        ]
        with DECISIONS.open("a", encoding="utf-8") as f:
            for r in out_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return (
            [f"{len(out_rows)} decision row(s) appended -> {DECISIONS.name}"],
            ["next: python3 helpers/graph/triage_pending_quotes.py --apply-decisions"],
        )

    return ReviewSession(
        entries=entries,
        journal=Journal(journal_path),
        render=render,
        ask=ask,
        spec_of=spec_of,
        apply_batch=apply_batch,
        batch_header="batch to append to the decisions file:",
        apply_noun="decision row(s)",
        input_fn=input_fn,
        print_fn=print_fn,
        apply_flag=apply,
    ).run()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Triage the quote entity worklist (S7, triage_pending_quotes)"
    )
    ap.add_argument(
        "--apply-decisions",
        action="store_true",
        help="apply annotated decisions (default: regenerate report)",
    )
    ap.add_argument(
        "--review",
        action="store_true",
        help="interactive journaled sitting over open canonicals (review-kit S3); "
        "appends decision rows on confirm",
    )
    ap.add_argument("--limit", type=int, default=None, help="review: cap rows per sitting")
    ap.add_argument(
        "--redecide", action="store_true", help="review: also re-walk annotated-unapplied rows"
    )
    ap.add_argument(
        "--skipped", action="store_true", help="review: also re-walk journaled-parked/declined rows"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="review: journal + print the batch, write nothing"
    )
    args = ap.parse_args(argv)
    if args.review:
        review(
            limit=args.limit,
            redecide=args.redecide,
            skipped=args.skipped,
            apply=not args.dry_run,
        )
        return 0
    return cmd_apply() if args.apply_decisions else cmd_report()


if __name__ == "__main__":
    raise SystemExit(main())
