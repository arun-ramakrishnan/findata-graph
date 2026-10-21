#!/usr/bin/env python3
"""Checklist walking for manual-review procedures (review-kit S4).

Render a procedure's checklist as a crisp-choice menu / agent prompt
packets, reusing the ``ReviewSession`` spine from ``review_kit`` and the
``review_menu`` input backends. Procedure logic is NOT changed — this is a
new surface that walks a checklist (the same items a human would tick by
hand) and records the verdicts.

Surfaces (``CHECKLISTS``):

- ``ocr`` — the ``ocr_review.md`` host-agent loop (ref → selection →
  freshness → rules → brief → review → validity → verdicts → apply →
  record → report).
- ``markdown-parse`` — the ``markdown_parse.md`` §Checklist (capture →
  naming → relations/tags → entities → citations → figures → verify gates).

Verdict verbs (per item):

      y                 done — the item holds
      n|why            needs attention — must carry a note
      p                park — revisit later (journal read-back skips)
      ?                this legend + item
      q / x            quit-and-review / abort the walk

Any verb may carry ``| free note`` — it lands in the row's note field.
The batch confirm is the only write gate; verdicts append to
``outputs/<surface>_checklist_review/decisions.jsonl`` (advisory — never a
gate leg). ``--dry-run`` journals ``preview-*`` non-terminal actions and
parks nothing.

``--menu`` renders each item as a numbered menu; ``--prompt`` reads one
answer per item from ``--answers`` (walk order; a final ``y``/``n`` line
answers the batch confirm) and ``--packets-out`` writes the prompt packets
(JSONL) for the agent flow. ``menu``/``prompt`` are mutually exclusive.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Run as a script (sys.path[0] == helpers/core), so the repo root has to be
# on sys.path BEFORE the runtime helpers.* imports inside review().
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

CHECKLIST_JOURNAL_ROOT = REPO / "outputs"

# Each item: {id, label, section}. `section` names the procedure anchor so a
# reader can jump back to the authoritative text — the menu never restates
# procedure logic, only the item.
CHECKLISTS: dict[str, dict] = {
    "ocr": {
        "title": "OCR delegation review (ocr_review.md host-agent loop)",
        "items": (
            {
                "id": "ocr-01",
                "label": "Ref named and resolved (stg id / sha; never the moving top alone)",
                "section": "ocr_review.md §0–1",
            },
            {
                "id": "ocr-02",
                "label": "Selection: reviewable nonempty; BOTH spines visible (tests/**/*.py AND tests/fixtures/**)",
                "section": "ocr_review.md §1 check 1+2, §S9",
            },
            {
                "id": "ocr-03",
                "label": "At least one non-test source path in the roster",
                "section": "ocr_review.md §1 check 2",
            },
            {
                "id": "ocr-04",
                "label": "Freshness read before reviewing (UNREVIEWED/FRESH/STALE/SUBSUMED)",
                "section": "ocr_review.md §1b",
            },
            {
                "id": "ocr-05",
                "label": "Per-path rule checklist obtained (ocr delegate rule <path>…)",
                "section": "ocr_review.md §3",
            },
            {
                "id": "ocr-06",
                "label": "Brief built (~15 lines) from the four indexes — claims, not instructions; never summarise the diff",
                "section": "ocr_review.md §2",
            },
            {
                "id": "ocr-07",
                "label": "Diff reviewed against the house checklist (9 duties); every finding verified against source; gate cross-checked",
                "section": "ocr_review.md §3 house checklist",
            },
            {
                "id": "ocr-08",
                "label": "Findings deduped across legs; every unverified claim executed; re-verified at HEAD; vault sweep done; triage persisted",
                "section": "ocr_review.md §4b 1–6",
            },
            {
                "id": "ocr-09",
                "label": "Severity ≤ demonstrated impact; nothing graded on unvalidated records",
                "section": "ocr_review.md §4 anti-inflation",
            },
            {
                "id": "ocr-10",
                "label": "Operator verdicts collected (accept/reject/defer); applied ONLY accepted fixes; test mutation-checked (goes RED)",
                "section": "ocr_review.md §4–5",
            },
            {
                "id": "ocr-11",
                "label": "Legs recorded in the freshness ledger — only the legs that ran (delegation/scan/managed)",
                "section": "ocr_review.md §1b",
            },
            {
                "id": "ocr-12",
                "label": "Report written under outputs/reviews/ (accounting table + false positives)",
                "section": "ocr_review.md §6",
            },
        ),
        "hints": (
            "next: .venv/bin/python3 helpers/misc/review_freshness.py --record "
            '--leg delegation --leg scan --note "outputs/reviews/review_report_<from>..<to>.md §…"',
            "report: outputs/reviews/review_report_<from>..<to>.md (gitignored)",
        ),
    },
    "markdown-parse": {
        "title": "markdown_parse.md §Checklist (per note processed)",
        "items": (
            {
                "id": "mdp-01",
                "label": "Input capture: images → <newsletter_dir>/images/ + rewritten .md (0 remote URLs), OR PDF converter produced <slug>.md + images/",
                "section": "markdown_parse.md §Checklist 1",
            },
            {
                "id": "mdp-02",
                "label": "Filename: PascalCase, single underscores, no special chars, ≤100 chars",
                "section": "markdown_parse.md §Checklist 2",
            },
            {
                "id": "mdp-03",
                "label": "normalized_name matches filename exactly",
                "section": "markdown_parse.md §Checklist 3",
            },
            {
                "id": "mdp-04",
                "label": "file_path resolves to an existing file",
                "section": "markdown_parse.md §Checklist 4",
            },
            {
                "id": "mdp-05",
                "label": "No duplicate normalized_name",
                "section": "markdown_parse.md §Checklist 5",
            },
            {
                "id": "mdp-06",
                "label": "Bidirectional part_of / has_company relations created",
                "section": "markdown_parse.md §Checklist 6",
            },
            {
                "id": "mdp-07",
                "label": "Enhanced tags populated",
                "section": "markdown_parse.md §Checklist 7",
            },
            {
                "id": "mdp-08",
                "label": "Existing entities enhanced — ## The Chatter — <edition> block appended where applicable",
                "section": "markdown_parse.md §Checklist 8",
            },
            {
                "id": "mdp-09",
                "label": "Edition citations spliced (backfill_okf_provenance.py --apply)",
                "section": "markdown_parse.md §Checklist 9",
            },
            {
                "id": "mdp-10",
                "label": "Key-Figures render confirmed — ungated derive_insights.py findata --apply when the dry-run promised writes",
                "section": "markdown_parse.md §Checklist 10",
            },
            {
                "id": "mdp-11",
                "label": "Sector-note auto rosters refreshed (sync_sector_wikilinks / make sync-sector-links)",
                "section": "markdown_parse.md §Checklist 11",
            },
            {
                "id": "mdp-12",
                "label": "Short, token-efficient names (no Ltd / Company suffixes)",
                "section": "markdown_parse.md §Checklist 12",
            },
            {
                "id": "mdp-13",
                "label": "Newsletter figures embedded in company notes via ![[images/<slug>_p{p}_img{N}.jpeg]]",
                "section": "markdown_parse.md §Checklist 13",
            },
            {
                "id": "mdp-14",
                "label": "verify_notes.py exits 0",
                "section": "markdown_parse.md §Checklist 14",
            },
            {
                "id": "mdp-15",
                "label": "database_integrity_check.py exits 0 (≥95%)",
                "section": "markdown_parse.md §Checklist 15",
            },
            {
                "id": "mdp-16",
                "label": 'frontmatter_schema exits 0 (no rogue keys, no "N/A"/wrong-typed values, ISO dates, underscore permalinks)',
                "section": "markdown_parse.md §Checklist 16",
            },
        ),
        "hints": (
            "next: .venv/bin/python3 helpers/validators/verify_notes.py",
            "next: .venv/bin/python3 helpers/misc/database_integrity_check.py",
            "next: .venv/bin/python3 helpers/validators/frontmatter_schema.py",
        ),
    },
}


def build_entries(surface: str) -> list[dict]:
    items = CHECKLISTS[surface]["items"]
    return [
        {
            "label": item["id"],
            "evidence": [
                {"kind": "checklist", "description": item["label"]},
                {"kind": "provenance", "description": item["section"]},
            ],
        }
        for item in items
    ]


def review(  # noqa: C901 — keypress parsing + kit wiring, split would scatter state
    surface: str,
    *,
    journal_dir: Path | None = None,
    decisions: Path | None = None,
    menu: bool = False,
    prompt: bool = False,
    answers_path: Path | None = None,
    packets_out: Path | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    input_fn=input,
    print_fn=print,
) -> dict:
    """Walk one checklist surface; returns the ReviewSession outcome."""
    from helpers.core.review_kit import Journal, ReviewSession
    from helpers.core.review_menu import (
        Choice,
        MenuSpec,
        make_menu_input,
        make_scripted_input,
        write_packets,
    )

    if surface not in CHECKLISTS:
        raise ValueError(f"unknown checklist surface {surface!r} (have {sorted(CHECKLISTS)})")
    if menu and prompt:
        raise ValueError("--menu and --prompt are mutually exclusive")
    if prompt and answers_path is None:
        raise ValueError("--prompt requires --answers")

    _MENU_SPEC = MenuSpec(
        name=surface,
        prompt="Mark this checklist item:",
        choices=(
            Choice("y", "done — the item holds"),
            Choice("n|why", "needs attention — must carry a note"),
            Choice("p", "park — revisit later (reopen via --skipped)"),
            Choice("?", "legend + item"),
            Choice("q/x", "quit-and-review / abort walk"),
        ),
    )

    entries = build_entries(surface)
    if limit:
        entries = entries[:limit]
    if packets_out is not None and entries:
        write_packets(entries, _MENU_SPEC, packets_out)

    root = (journal_dir or CHECKLIST_JOURNAL_ROOT) / f"{surface}_checklist_review"
    journal_path = root / "journal.jsonl"
    decisions_path = decisions or (root / "decisions.jsonl")
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    decisions_path.parent.mkdir(parents=True, exist_ok=True)

    if menu:
        reader = make_menu_input(_MENU_SPEC, print_fn=print_fn, input_fn=input_fn)
    elif prompt:
        if answers_path is None:  # pragma: no cover - guarded by the ValueError above
            raise ValueError("--prompt requires --answers")
        reader = make_scripted_input(answers_path.read_text().splitlines(), _MENU_SPEC)
    else:
        reader = input_fn

    def render(idx: int, total: int, e: dict) -> None:
        print_fn(f"[{idx}/{total}] {e['label']}")
        for line in e["evidence"]:
            print_fn(f"     {line['description']}")

    def ask(e: dict, note) -> dict:
        while True:
            ans = reader("  y / n|why / p / ? / q / x: ").strip()
            ans, _, free_note = ans.partition("|")
            if ans in {"p", "q", "x"}:
                return {
                    "id": e["label"],
                    "action": {"p": "skip", "q": "quit", "x": "abort"}[ans],
                    "note": free_note,
                }
            if ans == "?":
                print_fn(
                    "\n".join(
                        (
                            "  y  done — the item holds",
                            "  n|why  needs attention — must carry a note",
                            "  p  park — revisit later",
                            "  q / x  quit-and-review / abort walk",
                        )
                    )
                )
                continue
            if ans == "y":
                return {
                    "id": e["label"],
                    "action": "approve",
                    "file_decision": "done",
                    "note": free_note,
                }
            if ans == "n":
                if not free_note:
                    print_fn("  needs attention needs a note — n|why")
                    continue
                return {
                    "id": e["label"],
                    "action": "approve",
                    "file_decision": "needs_attention",
                    "note": free_note,
                }
            print_fn("  ? (y / n|why / p / ? / q / x)")
            continue

    def spec_of(d: dict, e: dict) -> str:  # noqa: ARG001
        return f"{d['id']} — {d['file_decision']}"

    hints = CHECKLISTS[surface]["hints"]

    def apply_batch(specs: list[str], decisions: list[dict]) -> tuple[list[str], list[str]]:
        rows = [
            {
                "surface": surface,
                "item_id": d["id"],
                "decision": d["file_decision"],
                "note": d.get("note", ""),
            }
            for d in decisions
            if d.get("action") == "approve"
        ]
        with decisions_path.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return (
            [f"{len(rows)} checklist verdict(s) recorded -> {decisions_path.name}"],
            list(hints),
        )

    return ReviewSession(
        entries=entries,
        journal=Journal(journal_path),
        render=render,
        ask=ask,
        spec_of=spec_of,
        apply_batch=apply_batch,
        batch_header="batch to record:",
        apply_noun="checklist verdict(s)",
        input_fn=reader,
        print_fn=print_fn,
        apply_flag=not dry_run,
    ).run()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("surface", choices=sorted(CHECKLISTS), help="checklist surface to walk")
    ap.add_argument(
        "--journal-dir", type=Path, help="override the journal parent dir (default outputs/)"
    )
    ap.add_argument(
        "--decisions",
        type=Path,
        help="decision output JSONL (default outputs/<surface>_checklist_review/decisions.jsonl)",
    )
    ap.add_argument("--menu", action="store_true", help="render each item as a numbered menu")
    ap.add_argument(
        "--prompt", action="store_true", help="read answers from --answers (walk order)"
    )
    ap.add_argument("--answers", type=Path, help="answers file (one verb per item + final y/n)")
    ap.add_argument(
        "--packets-out", type=Path, help="write the prompt packets (JSONL) for the agent flow"
    )
    ap.add_argument("--limit", type=int, default=None, help="walk at most N items (tests)")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="journal preview-* non-terminal actions; parks nothing",
    )
    parsed = ap.parse_args(argv)

    review(
        parsed.surface,
        journal_dir=parsed.journal_dir,
        decisions=parsed.decisions,
        menu=parsed.menu,
        prompt=parsed.prompt,
        answers_path=parsed.answers,
        packets_out=parsed.packets_out,
        limit=parsed.limit,
        dry_run=parsed.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
